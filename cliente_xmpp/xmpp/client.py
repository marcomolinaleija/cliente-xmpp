from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import datetime
from xml.etree import ElementTree as ET

from slixmpp import ClientXMPP

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.xmpp.events import (
    ChatActivityLoadFinished,
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
INBOX_NS = "urn:xmpp:inbox:1"
MAM_NS = "urn:xmpp:mam:2"
FORWARD_NS = "urn:xmpp:forward:0"
CLIENT_NS = "jabber:client"


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
        self.add_event_handler("carbon_received", self._on_carbon_received)
        self.add_event_handler("carbon_sent", self._on_carbon_sent)

    async def _on_session_start(self, _event: object) -> None:
        self.send_presence()
        await self.get_roster()
        self._emit(XmppConnected())
        await self._enable_carbons()
        chats = self._build_roster_chats()
        self._emit(RosterLoaded(chats))
        asyncio.create_task(self.load_recent_activity({chat.jid for chat in chats}))
        asyncio.create_task(self.load_inbox())

    def _on_disconnected(self, _event: object) -> None:
        self._emit(XmppDisconnected())
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _on_failed_auth(self, _event: object) -> None:
        self._emit(XmppError("No se pudo autenticar con el servidor XMPP."))

    def _on_message(self, msg: object) -> None:
        self._emit_inbox_entry(msg)
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

    def _on_carbon_received(self, msg: object) -> None:
        self._emit_message_from_stanza(msg["carbon_received"], outgoing=False)

    def _on_carbon_sent(self, msg: object) -> None:
        self._emit_message_from_stanza(msg["carbon_sent"], outgoing=True)

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

    async def load_recent_activity(self, roster_jids: set[str], limit: int = 1000) -> None:
        loaded_chat_jids: set[str] = set()
        try:
            mam = self["xep_0313"]
            async for result in mam.iterate(reverse=True, rsm={"max": 50}, total=limit):
                preview = self._message_body_from_mam_result(result)
                if not preview:
                    continue

                chat_jid = self._chat_jid_from_mam_result(result)
                sent_at = self._sent_at_from_mam_result(result)
                if not chat_jid or not sent_at or chat_jid in loaded_chat_jids:
                    continue

                if roster_jids and chat_jid not in roster_jids:
                    continue

                loaded_chat_jids.add(chat_jid)
                self._emit(
                    ChatActivityLoaded(
                        chat_jid=chat_jid,
                        sent_at=sent_at,
                        preview=preview,
                    )
                )

                if loaded_chat_jids == roster_jids:
                    break
        except Exception:
            pass
        finally:
            self._emit(ChatActivityLoadFinished(loaded_count=len(loaded_chat_jids)))

    async def load_inbox(self) -> None:
        try:
            iq = self.make_iq_get(ito=self.boundjid.bare)
            inbox = ET.Element(f"{{{INBOX_NS}}}inbox", {"messages": "true"})
            iq.append(inbox)
            await iq.send(timeout=10)
        except Exception:
            pass

    async def _enable_carbons(self) -> None:
        try:
            await self["xep_0280"].enable()
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

    def _emit_message_from_stanza(self, stanza: object, outgoing: bool) -> None:
        if stanza["type"] not in ("chat", "normal"):
            return

        body = str(stanza["body"] or "").strip()
        if not body:
            return

        chat_jid = str(stanza["to"].bare if outgoing else stanza["from"].bare)
        sender_jid = "Yo" if outgoing else chat_jid
        self._emit(
            MessageReceived(
                Message(
                    chat_jid=chat_jid,
                    sender_jid=sender_jid,
                    body=body,
                    outgoing=outgoing,
                )
            )
        )

    def _emit_inbox_entry(self, msg: object) -> None:
        entry = self._inbox_entry_from_stanza(msg)
        if entry is None:
            return

        chat_jid, unread_count, preview, sent_at = entry
        self._emit(
            ChatActivityLoaded(
                chat_jid=chat_jid,
                sent_at=sent_at,
                preview=preview,
                unread_count=unread_count,
            )
        )

    def _inbox_entry_from_stanza(self, msg: object) -> tuple[str, int, str, datetime | None] | None:
        xml = msg.xml
        entry = xml.find(f"{{{INBOX_NS}}}entry")
        if entry is None:
            return None

        chat_jid = entry.attrib.get("jid", "")
        if not chat_jid:
            return None

        unread_count = self._int_or_zero(entry.attrib.get("unread", "0"))
        result = entry.find(f"{{{MAM_NS}}}result")
        if result is None:
            result = xml.find(f"{{{MAM_NS}}}result")

        preview = ""
        sent_at = None
        if result is not None:
            message = self._forwarded_message_from_xml(result)
            if message is not None:
                body = message.find(f"{{{CLIENT_NS}}}body")
                preview = (body.text or "").strip() if body is not None else ""
            sent_at = self._forwarded_delay_from_xml(result)

        return chat_jid, unread_count, preview, sent_at

    @staticmethod
    def _forwarded_message_from_xml(result: ET.Element) -> ET.Element | None:
        forwarded = result.find(f"{{{FORWARD_NS}}}forwarded")
        if forwarded is None:
            return None

        return forwarded.find(f"{{{CLIENT_NS}}}message")

    @staticmethod
    def _forwarded_delay_from_xml(result: ET.Element) -> datetime | None:
        forwarded = result.find(f"{{{FORWARD_NS}}}forwarded")
        if forwarded is None:
            return None

        for child in forwarded:
            if child.tag.endswith("}delay"):
                stamp = child.attrib.get("stamp", "")
                if not stamp:
                    return None
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    return None

        return None

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
    def _int_or_zero(value: str) -> int:
        try:
            return int(value)
        except ValueError:
            return 0

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

    def load_recent_activity(self, roster_jids: set[str] | None = None, limit: int = 1000) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(self._client.load_recent_activity(roster_jids or set(), limit))

        self._loop.call_soon_threadsafe(load)

    def _run_client(self, settings: ConnectionSettings, password: str) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._client = BridgeXmppClient(settings, password, self._emit)
            plugins = ("xep_0030", "xep_0199", "xep_0297", "xep_0280", "xep_0313")
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
