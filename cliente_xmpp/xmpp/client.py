from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from slixmpp import ClientXMPP

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.xmpp.events import (
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
        self._emit(RosterLoaded(self._build_roster_chats()))

    def _on_disconnected(self, _event: object) -> None:
        self._emit(XmppDisconnected())

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
            name = item.get("name") or jid
            chats.append(Chat(jid=jid, name=name))
        return chats


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

        self._loop.call_soon_threadsafe(
            self._client.send_message,
            mto=to_jid,
            mbody=body,
            mtype="chat",
        )

    def _run_client(self, settings: ConnectionSettings, password: str) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._client = BridgeXmppClient(settings, password, self._emit)
        plugins = ("xep_0030", "xep_0199")
        for plugin in plugins:
            self._client.register_plugin(plugin)

        connected = self._client.connect(
            address=(settings.host, settings.port) if settings.host else None,
            use_ssl=False,
        )
        if not connected:
            self._emit(XmppError("No se pudo abrir la conexion con el servidor XMPP."))
            return

        self._client.process(forever=True)
