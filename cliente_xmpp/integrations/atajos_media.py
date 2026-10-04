from __future__ import annotations

import asyncio
import uuid
from pathlib import PureWindowsPath

from aiohttp import web


class MediaAPIMixin:
    def _initialize_media(self, store) -> None:
        self._media_store = store
        self._media_ids = {}
        self._media_gate = asyncio.Semaphore(1)

    def _media_routes(self, app: web.Application) -> None:
        app.router.add_post("/v1/media", self._list_media)
        app.router.add_post("/v1/media/content", self._read_media)

    async def _list_media(self, request: web.Request) -> web.Response:
        from cliente_xmpp.integrations.atajos_api import _text, _uuid

        body = await self._body(request, {"account_id", "contact_id", "kind", "count"})
        account, _, contacts = self._snapshot()
        if not account or body["account_id"] != self._account_id(account):
            return web.json_response({"error": "La cuenta cambió."}, status=409)
        if self._media_store is None:
            return web.json_response(
                {"error": "Actualiza el cliente para consultar adjuntos."}, status=501
            )
        contact = _text(body["contact_id"], 36, empty=True)
        chosen = contacts if not contact else {contact: contacts.get(_uuid(contact))}
        if any(value is None for value in chosen.values()):
            return web.json_response({"error": "El chat ya no está disponible."}, status=409)
        kind, count = body["kind"], body["count"]
        if (
            not isinstance(kind, str)
            or kind not in {"image", "audio", "video", "all"}
            or type(count) is not int
            or not 1 <= count <= 10
        ):
            raise ValueError("Tipo o cantidad no válido.")
        rows = await asyncio.to_thread(
            self._media_store.list_received, account, list(chosen.values()), kind, count
        )
        if self._snapshot()[0] != account:
            return web.json_response({"error": "La cuenta cambió durante la consulta."}, status=409)
        now = self._clock()
        self._media_ids = {key: value for key, value in self._media_ids.items() if value[-1] > now}
        lookup = {(c["jid"], c["is_group"]): (key, c) for key, c in chosen.items()}
        result = []
        for row in rows:
            chat_id, chat = lookup[(row["chat_jid"], bool(row["chat_is_group"]))]
            media_id = str(uuid.uuid4())
            while len(self._media_ids) >= 200:
                self._media_ids.pop(next(iter(self._media_ids)))
            self._media_ids[media_id] = (account, chat_id, row["message_key"], now + 600)
            result.append(
                {
                    "id": media_id,
                    "chat": chat["name"],
                    "is_group": chat["is_group"],
                    "kind": row["media_kind"],
                    "sent_at": row["sent_at"],
                    "name": PureWindowsPath(row["media_filename"]).name[:180],
                    "bytes": row["media_size"],
                }
            )
        return web.json_response(
            {
                "account_id": self._account_id(account),
                "media": result,
                "source": "local-cache",
                "received_only": True,
                "marks_read": False,
            }
        )

    async def _read_media(self, request: web.Request) -> web.Response:
        from cliente_xmpp.integrations.atajos_api import _uuid

        body = await self._body(request, {"account_id", "media_id"})
        account, _, contacts = self._snapshot()
        entry = self._media_ids.get(_uuid(body["media_id"]))
        if (
            not entry
            or entry[-1] <= self._clock()
            or entry[0] != account
            or body["account_id"] != self._account_id(account)
            or entry[1] not in contacts
        ):
            return web.json_response({"error": "El adjunto caducó o cambió de cuenta."}, status=409)
        chat = contacts[entry[1]]
        async with self._media_gate:
            try:
                data, mime = await asyncio.to_thread(
                    self._media_store.read, account, chat["jid"], chat["is_group"], entry[2]
                )
            except (OSError, ValueError):
                return web.json_response(
                    {"error": "El adjunto no está disponible, no se descargó o supera 100 MiB."},
                    status=422,
                )
        if self._snapshot()[0] != account or entry[1] not in self._snapshot()[2]:
            return web.json_response(
                {"error": "La cuenta o el chat cambió durante la lectura."}, status=409
            )
        return web.Response(
            body=data,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Atajos-Media-Mime": mime,
                "X-Atajos-Account": self._account_id(account),
            },
        )
