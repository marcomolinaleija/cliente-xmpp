from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import datetime

from slixmpp import ClientXMPP

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    MessageHistoryLoaded,
    MessageReceived,
    RosterLoaded,
    XmppConnected,
    XmppDisconnected,
    XmppError,
    XmppEvent,
)

EventHandler = Callable[[XmppEvent], None]


class BridgeXmppClient(ClientXMPP):
    def __init__(self, settings: ConnectionSettings, password: str, emit: EventHandler) -> None:
        super().__init__(settings.jid, password)
        self.settings = settings
        self._emit = emit
        self.force_starttls = settings.use_tls

        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.add_event_handler("failed_auth", self._on_failed_auth)
        self.add_event_handler("message", self._on_message)

    async def _on_session_start(self, _event: object) -> None:
        self.send_presence()
        await self.get_roster()
        self._emit(XmppConnected())
        try:
            await asyncio.wait_for(self.load_recent_activity(), timeout=8)
        except TimeoutError:
            pass
        self._emit(RosterLoaded(self._build_roster_chats()))

    def _on_disconnected(self, _event: object) -> None:
        self._emit(XmppDisconnected())
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _on_failed_auth(self, _event: object) -> None:
        self._emit(XmppError("No se pudo autenticar con el servidor XMPP."))

    def _on_message(self, msg: object) -> None:
        if msg["type"] not in ("chat", "normal"):
            return

        body = str(msg["body"] or "").strip()
        if not body:
            return

        bare_jid = str(msg["from"].bare)
        self._emit(
            MessageReceived(
                Message(
                    chat_jid=bare_jid,
                    sender_jid=bare_jid,
                    body=body,
                    outgoing=False,
                )
            )
        )

    def _build_roster_chats(self) -> list[Chat]:
        chats: list[Chat] = []
        for jid in sorted(self.client_roster.keys()):
            item = self.client_roster[jid]
            name = self._roster_item_name(item) or jid
            chats.append(Chat(jid=jid, name=name))
        return chats

    async def load_history(self, chat_jid: str, limit: int | None = None) -> None:
        try:
            archived_messages: list[Message] = []
            mam = self["xep_0313"]
            async for result in mam.iterate(
                with_jid=chat_jid,
                reverse=True,
                rsm={"max": 100},
                total=limit,
            ):
                message = self._message_from_mam_result(chat_jid, result)
                if message:
                    archived_messages.append(message)

            archived_messages.sort(key=lambda message: message.sent_at)
            self._emit(MessageHistoryLoaded(chat_jid=chat_jid, messages=archived_messages))
        except Exception as exc:
            self._emit(XmppError(f"No se pudo cargar el historial de {chat_jid}: {exc}"))

    async def load_recent_activity(self, limit: int = 1000) -> None:
        try:
            activity_by_chat: dict[str, datetime] = {}
            mam = self["xep_0313"]
            async for result in mam.iterate(reverse=True, rsm={"max": 100}, total=limit):
                if not self._message_body_from_mam_result(result):
                    continue

                chat_jid = self._chat_jid_from_mam_result(result)
                sent_at = self._sent_at_from_mam_result(result)
                if not chat_jid or not sent_at:
                    continue

                current = activity_by_chat.get(chat_jid)
                if current is None or sent_at > current:
                    activity_by_chat[chat_jid] = sent_at

            for chat_jid, sent_at in activity_by_chat.items():
                self._emit(ChatActivityLoaded(chat_jid=chat_jid, sent_at=sent_at))
        except Exception:
            pass

    def _message_from_mam_result(self, chat_jid: str, result: object) -> Message | None:
        body = self._message_body_from_mam_result(result)
        if not body:
            return None

        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        sender_jid = str(stanza["from"].bare)
        outgoing = sender_jid == self.boundjid.bare
        return Message(
            chat_jid=chat_jid,
            sender_jid="Yo" if outgoing else sender_jid,
            body=body,
            sent_at=self._sent_at_from_mam_result(result) or datetime.now(),
            outgoing=outgoing,
        )

    def _chat_jid_from_mam_result(self, result: object) -> str:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        from_jid = stanza["from"]
        to_jid = stanza["to"]
        if str(from_jid.bare) == self.boundjid.bare:
            return str(to_jid.bare)

        return str(from_jid.bare)

    @staticmethod
    def _sent_at_from_mam_result(result: object) -> datetime | None:
        forwarded = result["mam_result"]["forwarded"]
        return forwarded["delay"]["stamp"]

    @staticmethod
    def _message_body_from_mam_result(result: object) -> str:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        return str(stanza["body"] or "").strip()

    @staticmethod
    def _roster_item_name(item: object) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or "")

        try:
            return str(item["name"] or "")
        except (KeyError, TypeError):
            return ""


class XmppService:
    def __init__(self, emit: EventHandler) -> None:
        self._emit = emit
        self._client: BridgeXmppClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def connect(self, settings: ConnectionSettings, password: str) -> None:
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._thread = threading.Thread(
            target=self._run_client,
            args=(settings, password),
            daemon=True,
        )
        self._thread.start()

    def disconnect(self) -> None:
        if self._client and self._loop:
            self._loop.call_soon_threadsafe(self._client.disconnect)

    def send_message(self, to_jid: str, body: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def send() -> None:
            if self._client:
                self._client.send_message(mto=to_jid, mbody=body, mtype="chat")

        self._loop.call_soon_threadsafe(send)

    def load_history(self, chat_jid: str, limit: int | None = None) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(self._client.load_history(chat_jid, limit))

        self._loop.call_soon_threadsafe(load)

    def load_recent_activity(self, limit: int = 1000) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(self._client.load_recent_activity(limit))

        self._loop.call_soon_threadsafe(load)

    def _run_client(self, settings: ConnectionSettings, password: str) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._client = BridgeXmppClient(settings, password, self._emit)
            plugins = ("xep_0030", "xep_0199", "xep_0313")
            for plugin in plugins:
                self._client.register_plugin(plugin)

            if settings.host:
                self._client.connect(settings.host, settings.port)
            else:
                self._client.connect()

            self._loop.run_forever()
        except Exception as exc:
            self._emit(XmppError(f"Error en la conexion XMPP: {exc}"))
        finally:
            self._client = None
            self._loop = None
