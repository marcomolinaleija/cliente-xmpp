from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import datetime

from aiohttp import web

from cliente_xmpp.models.local_commands import is_local_bridge_command
from cliente_xmpp.storage.assistant_automation import AssistantAutomationStore
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore


class AutomationAPIMixin:
    """Live input and UI dispatch gates; SQLite remains on the API worker."""

    def _initialize_automation(self) -> None:
        self._automation = None
        self._automation_epoch = str(uuid.uuid4())  # Restart invalidates running monitors.
        self._observation_lock = asyncio.Lock()
        self._automation_operation_lock = asyncio.Lock()
        self._observations = deque()
        self._revisions = {}
        self._flushed_revisions = {}
        self._leases = {}
        self._lease_settings = {}
        self._dispatch_permissions = {}
        self._seen_live = {}

    def observe_message(self, account: str, message, kind: str) -> None:
        identity = message.message_id or message.displayed_marker_id
        if not account or kind not in {"incoming", "outgoing", "changed", "cleared"}:
            return
        if not identity:
            # Even an unidentifiable live message changes the conversation. It
            # cannot trigger a reply and must invalidate a draft already in flight.
            identity = "unidentified-" + str(uuid.uuid4())
            kind = "changed"
            self.error = "Se omitió una respuesta: el cliente no pudo identificar ese mensaje."
        event = {
            "account": account,
            "jid": message.chat_jid,
            "identity": identity,
            "kind": kind,
            "body": message.body[:4000] if kind == "incoming" else "",
            "sender": (message.sender_name or "Participante")[:160],
            "is_group": int(message.chat_is_group),
            "sent_at": message.sent_at.isoformat(),
            "received_at": self._clock(),
        }
        with self._lock:
            identity_key = (account, message.chat_jid, identity, kind)
            if kind in {"incoming", "outgoing"}:
                if identity_key in self._seen_live:
                    return
                self._seen_live[identity_key] = None
                if len(self._seen_live) > 10000:
                    self._seen_live.pop(next(iter(self._seen_live)))
            key = (account, message.chat_jid)
            previous_revision = self._revisions.get(key, 0)
            self._revisions[key] = self._revisions.get(key, 0) + 1
            if kind == "outgoing" and identity.startswith("cliente-xmpp-api-"):
                own_row = identity[len("cliente-xmpp-api-") :]
                if self._dispatch_permissions.get(own_row) == previous_revision:
                    # The optimistic message belonging to this send invalidates other
                    # drafts, while retaining its own last protocol authorization gate.
                    self._dispatch_permissions[own_row] = self._revisions[key]
            event["revision"] = self._revisions[key]
            self._observations.append(event)
            # A flood must stop automation, never silently drop a trigger or authorization change.
            if len(self._observations) > 10000:
                self._leases.clear()
                self.error = (
                    "Automatizaciones detenidas por exceso de novedades; reactiva desde Atajos."
                )
                self._automation_epoch = str(uuid.uuid4())

    def can_dispatch_on_ui(self, row: dict) -> bool:
        if not row.get("rule_id"):
            return True
        with self._lock:
            gate = self._dispatch_permissions.get(row["id"])
            chat = next((c for c in self._contacts.values() if c["jid"] == row["jid"]), None)
            return bool(
                gate
                and not self._closed.is_set()
                and self._ready
                and self._account == row["account"]
                and chat
                and chat["is_group"] == bool(row["is_group"])
                and self._leases.get(row["rule_id"], 0) > self._clock()
                and row["auto_expires"] > self._clock()
                and gate == self._revisions.get((row["account"], row["jid"]), 0)
            )

    def monitored_group_jids(self, account: str) -> set[str]:
        with self._lock:
            if account != self._account or not self._ready or self._closed.is_set():
                return set()
            permitted = set()
            for rule, settings in self._lease_settings.items():
                bound_account, scope, selected, excluded = settings
                if bound_account != account or self._leases.get(rule, 0) <= self._clock():
                    continue
                permitted.update(
                    chat["jid"]
                    for chat in self._contacts.values()
                    if chat["is_group"]
                    and chat["jid"] not in excluded
                    and (
                        scope in {"groups", "all"}
                        or scope == "selected"
                        and chat["jid"] in selected
                    )
                )
            return permitted

    async def _flush_observations(self) -> None:
        async with self._observation_lock:
            if self._store is None:
                self._store = await asyncio.to_thread(ScheduledMessageStore)
            if self._automation is None:
                self._automation = await asyncio.to_thread(
                    AssistantAutomationStore, self._store.path
                )
            with self._lock:
                batch = list(self._observations)
                self._observations.clear()
            try:
                await asyncio.to_thread(self._automation.check_database)
                if batch:
                    await asyncio.to_thread(self._automation.observe, batch)
                    with self._lock:
                        for event in batch:
                            self._flushed_revisions[(event["account"], event["jid"])] = event[
                                "revision"
                            ]
            except Exception:
                with self._lock:
                    self._leases.clear()
                    self._automation_epoch = str(uuid.uuid4())
                self.error = "No se guardaron las novedades; reactiva las automatizaciones."
                raise

    def _automation_routes(self, app: web.Application) -> None:
        def serial(handler):
            async def invoke(request):
                async with self._automation_operation_lock:
                    return await handler(request)

            return invoke

        app.router.add_post("/v1/automation/lease", serial(self._automation_lease))
        app.router.add_post("/v1/automation/revoke", serial(self._automation_revoke))
        app.router.add_post("/v1/automation/revoke-all", serial(self._automation_revoke_all))
        app.router.add_post("/v1/automation/events", self._automation_events)
        app.router.add_post("/v1/automation/reply", serial(self._automation_reply))
        app.router.add_get("/v1/requests/{id}", self._automation_request)

    async def _automation_input(self, request, fields):
        body = await self._body(request, fields | {"account_id", "epoch"})
        await self._flush_observations()
        account, ready, chats = self._snapshot()
        if (
            not account
            or body["account_id"] != self._account_id(account)
            or body["epoch"] != self._automation_epoch
        ):
            raise web.HTTPConflict(
                reason="Cambió la cuenta o se reinició el cliente; reactiva la regla."
            )
        return body, account, ready, chats

    async def _automation_lease(self, request):
        from .atajos_api import _uuid

        body, account, ready, chats = await self._automation_input(
            request, {"rule_id", "scope", "chat_ids", "excluded_ids", "expires_at"}
        )
        if not ready:
            raise web.HTTPConflict(reason="Conecta el cliente antes de activar la regla.")
        rule = _uuid(body["rule_id"])
        scope = body["scope"]
        if scope not in {"selected", "contacts", "groups", "all"}:
            raise ValueError("Alcance no válido.")
        lists = []
        for field in ("chat_ids", "excluded_ids"):
            values = body[field]
            if not isinstance(values, list) or len(values) > 500 or len(set(values)) != len(values):
                raise ValueError("Chats no válidos.")
            selected = [chats.get(_uuid(value)) for value in values]
            if any(chat is None for chat in selected):
                raise web.HTTPConflict(reason="Un chat ya no está disponible.")
            lists.append(sorted(chat["jid"] for chat in selected))
        if scope == "selected" and not lists[0] or scope != "selected" and lists[0]:
            raise ValueError("Selecciona chats únicamente para el alcance seleccionado.")
        expires = datetime.fromisoformat(body["expires_at"])
        if expires.tzinfo is None or not self._clock() < expires.timestamp() <= self._clock() + 300:
            raise ValueError("Permiso temporal no válido.")
        baseline = await asyncio.to_thread(
            self._automation.lease, rule, account, scope, *lists, expires.timestamp(), self._clock()
        )
        if self._snapshot()[0] != account:
            await asyncio.to_thread(self._automation.revoke, rule, account)
            raise web.HTTPConflict()
        with self._lock:
            self._leases[rule] = expires.timestamp()
            self._lease_settings[rule] = (account, scope, *lists)
        return web.json_response(
            {
                "baseline": baseline,
                "account_id": body["account_id"],
                "epoch": self._automation_epoch,
            }
        )

    async def _automation_revoke(self, request):
        from .atajos_api import _uuid

        body = await self._body(request, {"rule_id", "account_id"})
        rule = _uuid(body["rule_id"])
        with self._lock:
            self._leases.pop(rule, None)  # Invalidate already queued wx callbacks first.
            self._lease_settings.pop(rule, None)
        await self._flush_observations()
        account, _, _ = self._snapshot()
        if self._account_id(account) != body["account_id"]:
            raise web.HTTPConflict()
        await asyncio.to_thread(self._automation.revoke, rule, account)
        return web.json_response({"revoked": True})

    async def _automation_events(self, request):
        body, account, _, chats = await self._automation_input(request, {"after_seq"})
        after = body["after_seq"]
        if type(after) is not int or after < 0:
            raise ValueError("Cursor no válido.")
        mapped = {chat["jid"]: identifier for identifier, chat in chats.items()}
        page = await asyncio.to_thread(self._automation.events, account, after, set(mapped))
        for event in page["events"]:
            jid = event.pop("jid")
            identity = event.pop("identity")
            event.pop("account")
            event["chat_id"] = mapped[jid]
            event["message_id"] = str(
                uuid.uuid5(self._namespace, account + "\0" + jid + "\0" + identity)
            )
        if self._snapshot()[0] != account:
            raise web.HTTPConflict()
        return web.json_response(
            {**page, "account_id": body["account_id"], "epoch": self._automation_epoch}
        )

    async def _automation_revoke_all(self, request):
        body = await self._body(request, {"account_id"})
        with self._lock:
            account = (
                self._account
                if self._account_id(self._account) == body["account_id"]
                else next(
                    (
                        values[0]
                        for values in self._lease_settings.values()
                        if self._account_id(values[0]) == body["account_id"]
                    ),
                    "",
                )
            )
            if not account:
                raise web.HTTPConflict()
            for rule in list(self._lease_settings):
                if self._lease_settings[rule][0] == account:
                    self._leases.pop(rule, None)
                    self._lease_settings.pop(rule, None)
        await self._flush_observations()
        await asyncio.to_thread(self._automation.revoke_all, account)
        return web.json_response({"revoked": True})

    async def _automation_reply(self, request):
        from .atajos_api import _text, _uuid

        body, account, ready, chats = await self._automation_input(
            request, {"request_id", "rule_id", "chat_id", "trigger_seq", "text", "expires_at"}
        )
        rule, request_id = _uuid(body["rule_id"]), _uuid(body["request_id"])
        chat = chats.get(_uuid(body["chat_id"]))
        text = _text(body["text"], 4000)
        trigger = body["trigger_seq"]
        expires = datetime.fromisoformat(body["expires_at"])
        if (
            type(trigger) is not int
            or trigger <= 0
            or is_local_bridge_command(text)
            or expires.tzinfo is None
            or expires.timestamp() > self._clock() + 300
        ):
            raise ValueError("Respuesta no válida.")
        if not ready or chat is None:
            raise web.HTTPConflict()
        with self._lock:
            revision = self._flushed_revisions.get((account, chat["jid"]), 0)
        try:
            row = await asyncio.to_thread(
                self._automation.accept_reply,
                request_id,
                rule,
                account,
                chat["jid"],
                chat["name"],
                chat["is_group"],
                trigger,
                text,
                self._clock(),
                expires.timestamp(),
            )
        except ValueError:
            raise web.HTTPConflict(reason="El mensaje cambió o perdió su autorización.") from None
        with self._lock:
            self._dispatch_permissions[row["id"]] = revision
        return web.json_response(
            {"accepted": True, "request_id": request_id, "messages": [self._store.public(row)]},
            status=202,
        )

    async def _automation_request(self, request):
        from .atajos_api import _uuid

        if set(request.query) != {"account_id"}:
            raise ValueError("Cuenta ausente.")
        await self._flush_observations()
        account, _, _ = self._snapshot()
        if request.query["account_id"] != self._account_id(account):
            raise web.HTTPConflict()
        rows = await asyncio.to_thread(
            self._automation.request, account, _uuid(request.match_info["id"])
        )
        return web.json_response({"messages": [self._store.public(row) for row in rows]})
