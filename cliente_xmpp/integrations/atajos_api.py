from __future__ import annotations

import asyncio
import hmac
import json
import sqlite3
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime

from aiohttp import web

from cliente_xmpp.integrations.atajos_automation import AutomationAPIMixin
from cliente_xmpp.models.local_commands import is_local_bridge_command
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore

PORT = 47843
STATES = frozenset(
    {
        "all",
        "pending",
        "held",
        "dispatching",
        "submitted",
        "delivered",
        "read",
        "failed",
        "uncertain",
        "canceled",
    }
)


def _text(value: object, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError("Texto ausente o demasiado largo.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("El texto contiene caracteres de control.")
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Identificador no válido.")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ValueError("Identificador no válido.") from None
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("Identificador no válido.")
    return value


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c)
    )


def _json_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Parámetro duplicado.")
        result[key] = value
    return result


class LocalAssistantAPI(AutomationAPIMixin):
    def __init__(
        self,
        token: str,
        send: Callable[[dict[str, object]], None],
        *,
        store: ScheduledMessageStore | None = None,
        clock: Callable[[], float] = time.time,
        read_context: Callable | None = None,
    ) -> None:
        self._token = token
        self._send = send
        self._store = store
        self._clock = clock
        self._read_context = read_context
        self._context_cursors: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._account = ""
        self._ready = False
        self._contacts: dict[str, dict[str, str]] = {}
        self._namespace = uuid.uuid5(uuid.NAMESPACE_OID, token)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self.error = ""
        self._initialize_automation()

    def update(self, account: str, ready: bool, contacts: list[tuple[str, str]]) -> None:
        mapped = {}
        for entry in contacts:
            jid, name = entry[:2]
            group = bool(entry[2]) if len(entry) > 2 else False
            identity = account + "\0" + jid + ("\0group" if group else "")
            mapped[str(uuid.uuid5(self._namespace, identity))] = {
                "jid": jid,
                "name": name,
                "is_group": group,
            }
        with self._lock:
            self._account = account
            self._ready = ready
            self._contacts = mapped

    def _snapshot(self) -> tuple[str, bool, dict]:
        with self._lock:
            return self._account, self._ready, self._contacts.copy()

    def _account_id(self, account: str) -> str:
        return str(uuid.uuid5(self._namespace, account)) if account else ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="atajos-local-api")
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        if self._loop and self._stopped and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._stopped.set)
            except RuntimeError:
                pass  # The worker may finish between the state check and this call.

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except sqlite3.Error:
            self.error = (
                "La API local se detuvo por un problema de almacenamiento; "
                "vuelve a activar la integración."
            )
        except Exception:
            self.error = (
                "No se pudo iniciar la API local; comprueba que el puerto 47843 esté libre."
            )
        finally:
            self._closed.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopped = asyncio.Event()
        if self._store is None:
            self._store = await asyncio.to_thread(ScheduledMessageStore)
        if self._closed.is_set():
            return
        runner = web.AppRunner(self.application(), access_log=None, shutdown_timeout=3)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", PORT).start()
            while not self._stopped.is_set() and not self._closed.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(self._stopped.wait(), 1)
                except TimeoutError:
                    pass
        finally:
            await runner.cleanup()

    def application(self) -> web.Application:
        @web.middleware
        async def authenticate(request: web.Request, handler: Callable) -> web.StreamResponse:
            if (
                request.remote != "127.0.0.1"
                or request.headers.get("Origin") is not None
                or request.host.split(":", 1)[0] != "127.0.0.1"
            ):
                return web.json_response({"error": "Origen no permitido."}, status=403)
            if not hmac.compare_digest(
                request.headers.get("Authorization", ""), "Bearer " + self._token
            ):
                return web.json_response({"error": "Integración no autorizada."}, status=401)
            try:
                response = await handler(request)
            except (ValueError, json.JSONDecodeError, TypeError):
                return web.json_response({"error": "Parámetros no válidos."}, status=400)
            except web.HTTPException:
                raise
            except Exception:
                return web.json_response(
                    {
                        "error": "No se pudo completar la operación local. "
                        "Consulta los pendientes antes de repetir."
                    },
                    status=503,
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        app = web.Application(middlewares=[authenticate], client_max_size=65536)
        app.router.add_get("/v1/status", self._status)
        app.router.add_get("/v1/contacts", self._find_contacts)
        app.router.add_post("/v1/context", self._chat_context)
        app.router.add_post("/v1/messages", self._create_messages)
        app.router.add_get("/v1/messages", self._list_messages)
        app.router.add_post("/v1/messages/{id}/cancel", self._cancel_message)
        self._automation_routes(app)
        return app

    async def _status(self, _request: web.Request) -> web.Response:
        await self._flush_observations()
        account, ready, _ = self._snapshot()
        return web.json_response(
            {
                "protocol": 1,
                "application": "WhatsApp CAN",
                "connected": ready,
                "account_id": self._account_id(account),
                "late_default": "send-when-connected",
                "chat_context": self._read_context is not None,
                "automation": True,
                "journal_epoch": self._automation_epoch,
            }
        )

    async def _find_contacts(self, request: web.Request) -> web.Response:
        if set(request.query) - {"query", "offset", "kind"} or len(request.query) != len(
            set(request.query)
        ):
            raise ValueError("Parámetros no válidos.")
        account, _, contacts = self._snapshot()
        query = _fold(_text(request.query.get("query", ""), 100, empty=True)).split()
        offset = int(request.query.get("offset", "0"))
        kind = request.query.get("kind", "contacts")
        if kind not in {"contacts", "groups", "all"}:
            raise ValueError("Tipo de chat no válido.")
        if not 0 <= offset <= 100000:
            raise ValueError("Página no válida.")
        matches = sorted(
            (
                {"id": key, "name": contact["name"], "is_group": contact["is_group"]}
                for key, contact in contacts.items()
                if all(word in _fold(contact["name"]) for word in query)
                and (kind == "all" or contact["is_group"] == (kind == "groups"))
            ),
            key=lambda c: c["name"],
        )
        return web.json_response(
            {
                "account_id": self._account_id(account),
                "contacts": matches[offset : offset + 50],
                "total": len(matches),
                "next_offset": offset + 50 if len(matches) > offset + 50 else None,
            }
        )

    async def _body(self, request: web.Request, fields: set[str]) -> dict:
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        body = await request.json(
            loads=lambda value: json.loads(value, object_pairs_hook=_json_pairs)
        )
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError("Campos no válidos.")
        return body

    async def _chat_context(self, request: web.Request) -> web.Response:
        body = await self._body(request, {"account_id", "contact_id", "count", "cursor"})
        account, _, contacts = self._snapshot()
        if not account or body["account_id"] != self._account_id(account):
            return web.json_response(
                {"error": "La cuenta cambió; busca los contactos de nuevo."}, status=409
            )
        contact = contacts.get(_uuid(body["contact_id"]))
        if contact is None:
            return web.json_response({"error": "El contacto ya no está disponible."}, status=409)
        count = body["count"]
        if type(count) is not int or not 1 <= count <= 400:
            raise ValueError("Cantidad de contexto no válida.")
        cursor = _text(body["cursor"], 36, empty=True)
        before = anchor = None
        now = self._clock()
        self._context_cursors = {
            key: value for key, value in self._context_cursors.items() if value[4] > now
        }
        if cursor:
            entry = self._context_cursors.get(_uuid(cursor))
            if entry is None or entry[0] != account or entry[1] != contact["jid"]:
                return web.json_response(
                    {"error": "La página caducó o pertenece a otro contacto."}, status=409
                )
            before, anchor = entry[2], entry[3]
        if self._read_context is None:
            return web.json_response(
                {"error": "Este cliente no permite consultar contexto."}, status=501
            )
        options = {"is_group": True} if contact["is_group"] else {}
        page = await asyncio.to_thread(
            self._read_context, account, contact["jid"], count, before, anchor, **options
        )
        for message in page["messages"]:
            identity = message.pop("identity", "")
            message["id"] = (
                str(uuid.uuid5(self._namespace, account + "\0" + contact["jid"] + "\0" + identity))
                if identity
                else ""
            )
        active_account, _, active_contacts = self._snapshot()
        if active_account != account or body["contact_id"] not in active_contacts:
            return web.json_response(
                {"error": "La cuenta o el contacto cambió durante la lectura."}, status=409
            )
        next_before = page.pop("next_before")
        anchor = page.pop("anchor")
        next_cursor = ""
        if next_before is not None:
            if len(self._context_cursors) >= 256:
                self._context_cursors.pop(next(iter(self._context_cursors)))
            next_cursor = str(uuid.uuid4())
            self._context_cursors[next_cursor] = (
                account,
                contact["jid"],
                next_before,
                anchor,
                now + 600,
            )
        return web.json_response(
            {
                **page,
                "account_id": self._account_id(account),
                "name": contact["name"],
                "is_group": contact["is_group"],
                "source": "local-cache",
                "next_cursor": next_cursor,
                "marks_read": False,
            }
        )

    async def _create_messages(self, request: web.Request) -> web.Response:
        body = await self._body(
            request, {"request_id", "account_id", "messages", "send_at", "late_policy"}
        )
        account, _, contacts = self._snapshot()
        if not account or body["account_id"] != self._account_id(account):
            return web.json_response(
                {"error": "La cuenta cambió; consulta los contactos otra vez."}, status=409
            )
        request_id = _uuid(body["request_id"])
        date = _text(body["send_at"], 50)
        parsed = datetime.fromisoformat(date)
        if parsed.tzinfo is None:
            raise ValueError("Falta zona horaria.")
        due = parsed.timestamp()
        if due < self._clock() - 30 or due > self._clock() + 366 * 86400:
            raise ValueError("La hora está fuera del intervalo permitido.")
        policy = body["late_policy"]
        if policy not in {"hold", "send-when-connected"}:
            raise ValueError("Política no válida.")
        supplied = body["messages"]
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= 10:
            raise ValueError("Se admiten hasta diez destinatarios.")
        messages = []
        seen = set()
        for message in supplied:
            if not isinstance(message, dict) or set(message) != {"contact_id", "text"}:
                raise ValueError("Mensaje no válido.")
            contact_id = _uuid(message["contact_id"])
            contact = contacts.get(contact_id)
            if contact is None:
                return web.json_response(
                    {
                        "error": "Un contacto ya no está disponible. "
                        "Consulta los contactos de nuevo."
                    },
                    status=409,
                )
            text = _text(message["text"], 4000)
            if contact_id in seen or is_local_bridge_command(text):
                raise ValueError("Destinatario duplicado o comando local no permitido.")
            seen.add(contact_id)
            messages.append(
                {
                    "jid": contact["jid"],
                    "name": contact["name"],
                    "text": text,
                    "is_group": contact["is_group"],
                }
            )
        rows = await asyncio.to_thread(
            self._store.create, request_id, account, messages, due, policy
        )
        return web.json_response(
            {
                "accepted": True,
                "request_id": request_id,
                "messages": [self._store.public(row) for row in rows],
            },
            status=202,
        )

    async def _list_messages(self, request: web.Request) -> web.Response:
        if set(request.query) - {"state", "offset"} or len(request.query) != len(
            set(request.query)
        ):
            raise ValueError("Parámetros no válidos.")
        account, _, _ = self._snapshot()
        state = request.query.get("state", "all")
        offset = int(request.query.get("offset", "0"))
        if state not in STATES or not 0 <= offset <= 100000:
            raise ValueError("Filtro no válido.")
        rows, total = await asyncio.to_thread(self._store.list, account, state, offset)
        return web.json_response(
            {
                "messages": [self._store.public(row) for row in rows],
                "account_id": self._account_id(account),
                "total": total,
                "next_offset": offset + 50 if total > offset + 50 else None,
            }
        )

    async def _cancel_message(self, request: web.Request) -> web.Response:
        await self._body(request, set())
        account, _, _ = self._snapshot()
        item_id = _uuid(request.match_info["id"])
        canceled = await asyncio.to_thread(self._store.cancel, item_id, account)
        return web.json_response({"canceled": canceled}, status=200 if canceled else 409)

    async def tick(self) -> None:
        await self._flush_observations()
        account, ready, contacts = self._snapshot()
        active = {contact["jid"] for contact in contacts.values()}
        rows = await asyncio.to_thread(self._store.due, self._clock(), account)
        for row in rows:
            if self._closed.is_set():
                break
            if row["rule_id"] and not await asyncio.to_thread(
                self._automation.authorized, row, self._clock()
            ):
                await asyncio.to_thread(
                    self._store.transition,
                    row["id"],
                    "pending",
                    "canceled",
                    "La regla caducó o el chat cambió; no se envió.",
                )
                continue
            if row["late_policy"] == "send-when-connected" and (
                row["account"] != account or not ready
            ):
                continue
            reason = ""
            if row["account"] != account:
                reason = "La cuenta activa es diferente; revisa el mensaje antes de reprogramarlo."
            elif row["jid"] not in active:
                reason = "El contacto ya no está disponible; revisa antes de reprogramar."
            elif not ready:
                reason = "La conexión no estaba disponible a la hora indicada."
            elif self._clock() - float(row["due"]) > 30:
                reason = "La hora pasó mientras el cliente estaba cerrado o no disponible."
            if reason:
                if row["late_policy"] == "send-when-connected" and row["account"] == account:
                    if not ready:
                        continue
                    if row["jid"] in active:
                        reason = ""
                if reason:
                    await asyncio.to_thread(
                        self._store.transition,
                        row["id"],
                        "pending",
                        "held",
                        reason + " No se envió.",
                    )
                    continue
            claimed = await asyncio.to_thread(
                self._store.transition, row["id"], "pending", "dispatching"
            )
            if claimed:
                try:
                    self._send(row)
                except Exception:
                    await asyncio.to_thread(
                        self._store.transition,
                        row["id"],
                        "dispatching",
                        "uncertain",
                        "No se pudo confirmar el envío. Comprueba el chat antes de repetir.",
                    )

    def delivery(self, message_id: str, state: str) -> None:
        if self._store:
            self._background(self._store.finish_delivery, message_id, state)

    def unconfirmed(self, item_id: str) -> None:
        if self._store:
            self._background(
                self._store.transition,
                item_id,
                "dispatching",
                "uncertain",
                "No se confirmó el envío; revisa el chat antes de repetir.",
            )

    def rejected(self, item_id: str, reason: str, *, wait: bool = False) -> None:
        if self._store:
            self._background(
                self._store.transition,
                item_id,
                "dispatching",
                "pending" if wait else "held",
                reason,
            )

    def _background(self, operation: Callable, *args: object) -> None:
        if not self._loop or not self._loop.is_running():
            return
        coroutine = asyncio.to_thread(operation, *args)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return

        def finished(result: object) -> None:
            try:
                result.result()
            except Exception:
                self.error = "No se pudo guardar un estado de la cola; consulta antes de repetir."

        future.add_done_callback(finished)
