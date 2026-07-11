from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout

from cliente_xmpp.audio.duration import media_duration_seconds
from cliente_xmpp.audio.opus import (
    VOICE_NOTE_MIME,
    VOICE_NOTE_UPLOAD_MIME,
    convert_to_voice_note,
)
from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.names import display_label_from_jid, normalize_chat_name
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
    ChatStateUpdated,
    ChatsDiscovered,
    ContactAvatarReceived,
    ContactAvatarUnavailable,
    ContactPresenceUpdated,
    MessageDeliveryUpdated,
    MessageHistoryLoaded,
    MessageReceived,
    RosterLoaded,
    WhatsAppBridgeStatus,
    WhatsAppLinkSessionEnded,
    WhatsAppLinkSessionStarted,
    WhatsAppPairingCodeReceived,
    WhatsAppQrImageDataReceived,
    WhatsAppQrImageReceived,
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
DISCO_INFO_NS = "http://jabber.org/protocol/disco#info"
DISCO_ITEMS_NS = "http://jabber.org/protocol/disco#items"
MUC_NS = "http://jabber.org/protocol/muc"
MUC_USER_NS = "http://jabber.org/protocol/muc#user"
BOOKMARKS_NS = "urn:xmpp:bookmarks:1"
LEGACY_BOOKMARKS_NS = "storage:bookmarks"
PRIVATE_XML_NS = "jabber:iq:private"
PUBSUB_EVENT_NS = "http://jabber.org/protocol/pubsub#event"
NOTIFICATION_SETTINGS_NAMESPACES = (
    "urn:xmpp:notification-settings:1",
    "urn:xmpp:notification-settings:0",
)
DATA_FORMS_NS = "jabber:x:data"
DIRECT_INVITE_NS = "jabber:x:conference"
OOB_NS = "jabber:x:oob"
REACTIONS_NS = "urn:xmpp:reactions:0"
REPLY_NS = "urn:xmpp:reply:0"
FALLBACK_NS = "urn:xmpp:fallback:0"
MESSAGE_RETRACT_NS = "urn:xmpp:message-retract:1"
DELAY_NS = "urn:xmpp:delay"
FILE_METADATA_NS = "urn:xmpp:file:metadata:0"
SFS_NS = "urn:xmpp:sfs:0"
SIMS_NS = "urn:xmpp:sims:1"
REFERENCE_NS = "urn:xmpp:reference:0"
JINGLE_FILE_TRANSFER_NS = "urn:xmpp:jingle:apps:file-transfer:5"
URL_DATA_NS = "http://jabber.org/protocol/url-data"
BOB_NS = "urn:xmpp:bob"
AVATAR_METADATA_NS = "urn:xmpp:avatar:metadata"
AVATAR_DATA_NS = "urn:xmpp:avatar:data"
SLIDGE_GROUPS_COMMAND = "https://slidge.im/command/core/groups/groups"
SLIDGE_REINVITE_GROUPS_COMMAND = "https://slidge.im/command/core/groups/re-invite"
SLIDGE_RELOGIN_COMMAND = "https://slidge.im/command/core/re-login"
SLIDGE_PAIR_PHONE_COMMAND = "wa_pair_phone"
WHATSAPP_DEBUG_PREFIX = "[cliente-xmpp][whatsapp]"
JID_PATTERN = re.compile(r"(?<![\w.+-])(?:xmpp:)?([^\s<>\"']+@[^\s<>\"']+)")
AUDIO_EXTENSIONS = (".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".weba")
IMAGE_EXTENSIONS = (".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp")
VIDEO_EXTENSIONS = (".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm")
CHAT_MARKERS_NS = "urn:xmpp:chat-markers:0"
CHATSTATES_NS = "http://jabber.org/protocol/chatstates"
IDLE_NS = "urn:xmpp:idle:1"
EXPLICIT_MIME_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": VOICE_NOTE_MIME,
    ".opus": "audio/ogg; codecs=opus",
    ".wav": "audio/wav",
    ".weba": "audio/webm",
}
URL_PATTERN = re.compile(r"https?://\S+")


class BridgeXmppClient(ClientXMPP):
    def __init__(self, settings: ConnectionSettings, password: str, emit: EventHandler) -> None:
        super().__init__(settings.jid, password)
        self.settings = settings
        self._emit = emit
        self._history_preload_semaphore = asyncio.Semaphore(4)
        self._group_chat_jids: set[str] = set()
        self._joined_group_chat_jids: set[str] = set()
        self._presence_subscription_jids: set[str] = set()
        self._session_started_at: datetime | None = None
        self._disconnect_requested = False
        self._reconnect_scheduled = False
        self._last_whatsapp_status_by_component: dict[str, str] = {}
        self._whatsapp_link_sessions: dict[str, tuple[str, str]] = {}
        self.force_starttls = settings.use_tls

        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.add_event_handler("failed_auth", self._on_failed_auth)
        self.add_event_handler("message", self._on_message)
        self.add_event_handler("groupchat_message", self._on_groupchat_message)
        self.add_event_handler("carbon_received", self._on_carbon_received)
        self.add_event_handler("carbon_sent", self._on_carbon_sent)
        self.add_event_handler("receipt_received", self._on_receipt_received)
        self.add_event_handler("marker_received", self._on_marker_received)
        self.add_event_handler("marker_displayed", self._on_marker_displayed)
        self.add_event_handler("chatstate", self._on_chatstate)
        self.add_event_handler("presence_available", self._on_presence_debug)
        self.add_event_handler("presence_unavailable", self._on_presence_debug)
        self.add_event_handler("presence_error", self._on_presence_debug)
        self.add_event_handler("changed_status", self._on_presence_debug)

    async def _on_session_start(self, _event: object) -> None:
        self._session_started_at = datetime.now().astimezone()
        self.send_presence()
        await self.get_roster()
        self._emit(XmppConnected())
        await self._enable_carbons()
        chats = self._build_roster_chats()
        self._emit(RosterLoaded(chats))
        asyncio.create_task(self._debug_whatsapp_bridge_state(chats))
        asyncio.create_task(self.load_recent_activity({chat.jid for chat in chats}))
        asyncio.create_task(self._discover_group_chats_with_retries(chats))
        asyncio.create_task(self.load_inbox())

    def _on_disconnected(self, _event: object) -> None:
        if self._disconnect_requested:
            self._emit(XmppDisconnected())
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            return

        self._emit(XmppDisconnected("Reconectando..."))
        self._schedule_reconnect()

    def _schedule_reconnect(self, delay: float = 3.0) -> None:
        if self._reconnect_scheduled or self._disconnect_requested:
            return

        self._reconnect_scheduled = True

        def reconnect() -> None:
            self._reconnect_scheduled = False
            if self._disconnect_requested:
                return
            if self.is_connected() or self.is_connecting():
                return
            if self.settings.host:
                self.connect(self.settings.host, self.settings.port)
            else:
                self.connect()

        self.loop.call_later(delay, reconnect)

    def request_disconnect(self) -> None:
        self._disconnect_requested = True
        self.disconnect()

    def _stop_after_failed_auth(self) -> None:
        self._disconnect_requested = True
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _on_failed_auth(self, _event: object) -> None:
        self._stop_after_failed_auth()
        self._emit(XmppError("No se pudo autenticar con el servidor XMPP."))

    def _on_message(self, msg: object) -> None:
        try:
            message_type = msg["type"]
            body = str(msg["body"] or "").strip()
            bare_jid = str(msg["from"].bare)
        except Exception:
            message_type = ""
            body = ""
            bare_jid = ""

        self._emit_chat_state_from_message(bare_jid, message_type, msg)

        if message_type in ("chat", "normal"):
            self._debug_whatsapp_admin_message(bare_jid, body)
            if self._is_whatsapp_component_admin_message(bare_jid, body):
                return

        self._emit_inbox_entry(msg)
        group_chats = self._group_chats_from_bookmark_event_stanza(msg)
        if group_chats:
            self._monitor_discovered_group_chats(group_chats)
            return

        group_chat = self._group_chat_from_invite_stanza(msg)
        if group_chat is not None:
            self._monitor_discovered_group_chats([group_chat])
            return

        if message_type not in ("chat", "normal"):
            return

        retraction = self._message_retraction_from_stanza(msg, outgoing=False)
        if retraction is not None:
            self._emit(MessageReceived(retraction))
            return

        (
            media_url,
            media_kind,
            media_mime,
            media_filename,
            media_size,
            media_duration,
        ) = self._media_from_stanza(msg)
        audio_url = media_url if media_kind == "audio" else ""
        qr_image_data = self._whatsapp_qr_image_data_from_xml(bare_jid, body, msg.xml)
        if qr_image_data is not None:
            image_data, image_mime, image_filename = qr_image_data
            self._debug_whatsapp(
                "qr embedded image received "
                f"from={bare_jid} mime={image_mime or '-'} bytes={len(image_data)}"
            )
            self._emit(
                WhatsAppQrImageDataReceived(
                    component_jid=bare_jid,
                    image_data=image_data,
                    mime=image_mime,
                    filename=image_filename,
                )
            )
        elif self._is_whatsapp_qr_image(bare_jid, body, media_url, media_kind):
            self._emit(
                WhatsAppQrImageReceived(
                    component_jid=bare_jid,
                    image_url=media_url,
                    mime=media_mime,
                    filename=media_filename,
                )
            )
        elif self._is_whatsapp_qr_candidate_message(bare_jid, body, msg.xml, media_url, media_kind):
            self._debug_whatsapp_qr_candidate(bare_jid, body, msg.xml, media_url, media_kind)
        if not body and not media_url:
            return

        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            msg.xml,
        )
        self._emit(
            MessageReceived(
                Message(
                    chat_jid=bare_jid,
                    sender_jid=bare_jid,
                    body=display_body,
                    sent_at=self._sent_at_from_stanza_delay(msg) or datetime.now(),
                    outgoing=False,
                    audio_url=audio_url,
                    media_url=media_url,
                    media_kind=media_kind,
                    media_mime=media_mime,
                    media_filename=media_filename,
                    media_size=media_size,
                    media_duration_seconds=media_duration,
                    message_id=str(msg["id"] or ""),
                    reply_quote=reply_quote,
                )
            )
        )

    def _on_groupchat_message(self, msg: object) -> None:
        if msg["type"] != "groupchat":
            return

        message = self._message_from_groupchat_stanza(msg)
        if message is not None:
            self._group_chat_jids.add(message.chat_jid)
            notify = not self._message_predates_session(message)
            self._emit(MessageReceived(message, notify=notify))

    def _message_predates_session(self, message: Message) -> bool:
        if self._session_started_at is None:
            return False

        return message.sent_at.timestamp() < self._session_started_at.timestamp()

    def _on_carbon_received(self, msg: object) -> None:
        self._emit_message_from_stanza(msg["carbon_received"], outgoing=False)

    def _on_carbon_sent(self, msg: object) -> None:
        self._emit_message_from_stanza(msg["carbon_sent"], outgoing=True)

    def _on_receipt_received(self, msg: object) -> None:
        self._emit_delivery_update_from_marker(msg, "delivered")

    def _on_marker_received(self, msg: object) -> None:
        self._emit_delivery_update_from_marker(msg, "delivered")

    def _on_marker_displayed(self, msg: object) -> None:
        self._emit_delivery_update_from_marker(msg, "read")

    def _emit_delivery_update_from_marker(self, msg: object, delivery_state: str) -> None:
        message_id = self._delivery_marker_id(msg)
        if not message_id:
            return

        self._emit(
            MessageDeliveryUpdated(
                chat_jid=str(msg["from"].bare),
                message_id=message_id,
                delivery_state=delivery_state,
            )
        )

    @staticmethod
    def _delivery_marker_id(msg: object) -> str:
        for plugin_name in ("receipt", "displayed", "received", "acknowledged"):
            try:
                value = msg[plugin_name]
                if isinstance(value, str) and value:
                    return value
                marker_id = str(value["id"] or "")
                if marker_id:
                    return marker_id
            except Exception:
                pass

        xml = getattr(msg, "xml", None)
        if xml is None:
            return ""

        for node in xml.iter():
            if node.tag in {
                "{urn:xmpp:receipts}received",
                f"{{{CHAT_MARKERS_NS}}}received",
                f"{{{CHAT_MARKERS_NS}}}displayed",
                f"{{{CHAT_MARKERS_NS}}}acknowledged",
            }:
                return node.attrib.get("id", "")

        return ""

    def _on_presence_debug(self, presence: object) -> None:
        try:
            from_jid = str(presence["from"].bare)
            presence_type = str(presence["type"] or "available")
            show = str(presence["show"] or "")
            status = str(presence["status"] or "")
        except Exception as exc:
            self._debug_whatsapp(f"presence parse error: {exc}")
            return

        if self._is_probable_whatsapp_bridge_jid(from_jid) or self._is_whatsapp_state_text(status):
            self._debug_whatsapp(
                "presence "
                f"from={from_jid} type={presence_type or 'available'} "
                f"show={show or '-'} status={self._safe_debug_text(status) or '-'}"
            )
            state = self._whatsapp_state_hint(status)
            if state != "unknown":
                self._emit_whatsapp_status(from_jid, state, status)
                return

        self._emit_contact_presence(from_jid, presence_type, show, status, presence)

    def _emit_contact_presence(
        self,
        from_jid: str,
        presence_type: str,
        show: str,
        status: str,
        presence: object,
    ) -> None:
        if (
            not from_jid
            or from_jid == str(self.boundjid.bare)
            or self._is_probable_whatsapp_bridge_jid(from_jid)
            or self._jid_may_be_group_chat(from_jid)
        ):
            return

        availability = self._presence_availability(presence_type, show)
        if not availability:
            return

        self._emit(
            ContactPresenceUpdated(
                chat_jid=from_jid,
                availability=availability,
                status=status,
                last_seen=self._idle_datetime_from_xml(getattr(presence, "xml", None)),
            )
        )

    @staticmethod
    def _presence_availability(presence_type: str, show: str) -> str:
        if presence_type == "unavailable":
            return "offline"
        if presence_type in {"error", "subscribe", "subscribed", "unsubscribe", "unsubscribed"}:
            return ""
        if show in {"away", "xa"}:
            return "away"
        if show == "dnd":
            return "busy"
        return "online"

    @classmethod
    def _idle_datetime_from_xml(cls, xml: ET.Element | None) -> datetime | None:
        if xml is None:
            return None

        for node in xml.iter():
            if node.tag == f"{{{IDLE_NS}}}idle":
                return cls._datetime_from_xmpp_value(node.attrib.get("since", ""))

        return None

    @staticmethod
    def _datetime_from_xmpp_value(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _emit_chat_state_from_message(self, from_jid: str, message_type: str, msg: object) -> None:
        if message_type not in {"chat", "normal"} or not from_jid:
            return

        state, media = self._chat_state_from_xml(getattr(msg, "xml", None))
        if not state:
            return

        self._emit_chat_state_update(from_jid, state, media)

    def _on_chatstate(self, msg: object) -> None:
        try:
            from_jid = str(msg["from"].bare)
            state = str(msg["chat_state"] or "")
            _xml_state, media = self._chat_state_from_xml(getattr(msg, "xml", None))
        except Exception as exc:
            self._debug_whatsapp(f"chatstate parse error: {exc}")
            return

        self._emit_chat_state_update(from_jid, state, media)

    def _emit_chat_state_update(self, from_jid: str, state: str, media: str = "") -> None:
        if (
            not from_jid
            or not state
            or self._is_probable_whatsapp_bridge_jid(from_jid)
            or self._jid_may_be_group_chat(from_jid)
        ):
            return

        self._debug_whatsapp(f"chatstate from={from_jid} state={state} media={media or '-'}")
        self._emit(ChatStateUpdated(chat_jid=from_jid, state=state, media=media))

    @staticmethod
    def _chat_state_from_xml(xml: ET.Element | None) -> tuple[str, str]:
        if xml is None:
            return "", ""

        for node in xml.iter():
            namespace, _, local_name = (
                node.tag[1:].partition("}") if node.tag.startswith("{") else ("", "", node.tag)
            )
            if namespace == CHATSTATES_NS and local_name in {
                "active",
                "composing",
                "paused",
                "inactive",
                "gone",
            }:
                return local_name, node.attrib.get("media", "").strip().casefold()

        return "", ""

    def _build_roster_chats(self) -> list[Chat]:
        chats: list[Chat] = []
        for jid in sorted(self.client_roster.keys()):
            item = self.client_roster[jid]
            name = normalize_chat_name(jid, self._roster_item_name(item))
            chats.append(Chat(jid=jid, name=name))
        return chats

    async def _discover_group_chats(self, roster_chats: list[Chat]) -> None:
        group_chats: dict[str, Chat] = {}

        for group_chat in await self._bookmarked_group_chats():
            group_chats[group_chat.jid] = group_chat
        for chat in roster_chats:
            if not self._jid_may_be_group_chat(chat.jid):
                continue

            group_chats[chat.jid] = Chat(
                jid=chat.jid,
                name=normalize_chat_name(chat.jid, chat.name),
                is_group=True,
                notifications_muted=chat.notifications_muted,
                notification_settings_known=chat.notification_settings_known,
                group_member_count=chat.group_member_count,
                is_self_group=chat.is_self_group,
                unread_count=chat.unread_count,
                last_message_preview=chat.last_message_preview,
                last_message_at=chat.last_message_at,
            )

        if group_chats:
            chats = sorted(group_chats.values(), key=lambda chat: chat.name.casefold())
            self._monitor_discovered_group_chats(chats)

        component_domains = await self._group_service_candidates(roster_chats)
        enriched_group_chats: dict[str, Chat] = {}
        for chat in group_chats.values():
            group_chat = await self._group_chat_from_disco(chat.jid, fallback_name=chat.name)
            if group_chat is not None:
                enriched_group_chats[group_chat.jid] = group_chat

        for domain in component_domains:
            for item_jid, item_name in await self._disco_items(domain):
                if item_jid in group_chats:
                    continue

                if self._jid_is_hash_group_chat(item_jid):
                    enriched_group_chats[item_jid] = Chat(
                        jid=item_jid,
                        name=normalize_chat_name(item_jid, item_name),
                        is_group=True,
                    )
                    continue

                group_chat = await self._group_chat_from_disco(
                    item_jid,
                    fallback_name=item_name or item_jid,
                )
                if group_chat is not None:
                    enriched_group_chats[group_chat.jid] = group_chat

        for group_chat in await self._adhoc_group_chats(component_domains):
            enriched_group_chats[group_chat.jid] = group_chat

        if not group_chats and not enriched_group_chats:
            await self._request_group_reinvite(component_domains)
            return

        chats = sorted(enriched_group_chats.values(), key=lambda chat: chat.name.casefold())
        if not chats:
            return

        self._monitor_discovered_group_chats(chats)

    async def _discover_group_chats_with_retries(self, roster_chats: list[Chat]) -> None:
        for delay in (0, 5, 15, 45):
            if delay:
                await asyncio.sleep(delay)
            await self._discover_group_chats(roster_chats)

    def _monitor_discovered_group_chats(self, chats: list[Chat]) -> None:
        chats = [chat for chat in chats if self._jid_may_be_group_chat(chat.jid)]
        if not chats:
            return

        self._group_chat_jids.update(chat.jid for chat in chats)
        self._emit(ChatsDiscovered(chats))
        for chat in chats:
            self._join_group_chat(chat.jid)
        asyncio.create_task(self.load_recent_activity({chat.jid for chat in chats}))

    async def _group_service_candidates(self, roster_chats: list[Chat]) -> set[str]:
        candidates = self._component_domains_from_chats(roster_chats)
        if self.boundjid.domain:
            server_domain = str(self.boundjid.domain)
            candidates.add(server_domain)
        else:
            server_domain = ""

        if server_domain:
            for jid, _name in await self._disco_items(server_domain):
                if not self._jid_is_hash_group_chat(jid):
                    candidates.add(jid)

        for jid in list(candidates):
            for child_jid, _name in await self._disco_items(jid):
                if not self._jid_is_hash_group_chat(child_jid):
                    candidates.add(child_jid)

        return candidates

    async def _debug_whatsapp_bridge_state(self, roster_chats: list[Chat]) -> None:
        try:
            candidates = await self._group_service_candidates(roster_chats)
        except Exception as exc:
            self._debug_whatsapp(f"state discovery failed: {exc}")
            return

        probable = sorted(jid for jid in candidates if self._is_probable_whatsapp_bridge_jid(jid))
        self._debug_whatsapp(
            "component candidates "
            f"probable={probable or 'none'} total={sorted(candidates) or 'none'}"
        )

        for jid in probable:
            await self._debug_whatsapp_component(jid)

    async def _debug_whatsapp_component(self, jid: str) -> None:
        try:
            info = await self["xep_0030"].get_info(jid=jid, timeout=10)
        except Exception as exc:
            self._debug_whatsapp(f"component {jid} disco_info error: {_format_xmpp_error(exc)}")
            return

        identities = [
            "category="
            f"{identity.attrib.get('category', '')},type={identity.attrib.get('type', '')},"
            f"name={self._safe_debug_text(identity.attrib.get('name', ''))}"
            for identity in info.xml.findall(f".//{{{DISCO_INFO_NS}}}identity")
        ]
        features = sorted(
            feature.attrib.get("var", "")
            for feature in info.xml.findall(f".//{{{DISCO_INFO_NS}}}feature")
            if feature.attrib.get("var", "")
        )
        self._debug_whatsapp(
            f"component {jid} identities={identities or 'none'} "
            f"features={features or 'none'}"
        )

        asyncio.create_task(self._debug_whatsapp_component_commands(jid))

    async def _debug_whatsapp_component_commands(self, jid: str) -> None:
        commands = await self._adhoc_commands(jid)
        self._debug_whatsapp_commands(jid, commands)
        state = self._whatsapp_command_state(commands)
        if state not in ("unknown", "connected"):
            self._emit_whatsapp_status(jid, state)

    @staticmethod
    def _component_domains_from_chats(chats: list[Chat]) -> set[str]:
        domains: set[str] = set()
        for chat in chats:
            bare_jid = chat.jid.split("/", 1)[0]
            if "@" not in bare_jid:
                if bare_jid:
                    domains.add(bare_jid)
                continue
            local_part, domain = bare_jid.split("@", 1)
            if local_part and domain:
                domains.add(domain)
        return domains

    @staticmethod
    def _jid_may_be_group_chat(jid: str) -> bool:
        bare_jid = jid.split("/", 1)[0]
        if "@" not in bare_jid:
            return False

        local_part = bare_jid.split("@", 1)[0]
        return bool(local_part and not local_part.startswith("+"))

    @staticmethod
    def _jid_is_hash_group_chat(jid: str) -> bool:
        bare_jid = jid.split("/", 1)[0]
        if "@" not in bare_jid:
            return False

        return bare_jid.split("@", 1)[0].startswith("#")

    async def _group_chat_from_disco(
        self,
        jid: str,
        fallback_name: str = "",
    ) -> Chat | None:
        try:
            info = await self["xep_0030"].get_info(jid=jid, timeout=10)
        except Exception:
            return None

        features = {
            feature.attrib.get("var", "")
            for feature in info.xml.findall(f".//{{{DISCO_INFO_NS}}}feature")
        }
        identities = info.xml.findall(f".//{{{DISCO_INFO_NS}}}identity")
        is_group = MUC_NS in features or any(
            identity.attrib.get("category") == "conference" for identity in identities
        )
        if not is_group:
            return None

        name = fallback_name or jid
        for identity in identities:
            identity_name = identity.attrib.get("name", "").strip()
            if identity_name:
                name = identity_name
                break

        room_items = await self._disco_items(jid)
        member_count = len(room_items)
        return Chat(
            jid=jid,
            name=normalize_chat_name(jid, name),
            is_group=True,
            group_member_count=member_count,
            is_self_group=member_count == 1,
        )

    async def _disco_items(self, jid: str) -> list[tuple[str, str]]:
        try:
            items = await self["xep_0030"].get_items(jid=jid, timeout=30)
        except Exception:
            return []

        discovered: list[tuple[str, str]] = []
        for item in items.xml.findall(f".//{{{DISCO_ITEMS_NS}}}item"):
            item_jid = item.attrib.get("jid", "").strip()
            if item_jid:
                discovered.append((item_jid, item.attrib.get("name", "").strip()))
        return discovered

    async def _bookmarked_group_chats(self) -> list[Chat]:
        group_chats: dict[str, Chat] = {}
        for jid in (self.boundjid.bare, None):
            try:
                items = await self["xep_0060"].get_items(
                    jid,
                    BOOKMARKS_NS,
                    timeout=10,
                )
            except Exception:
                continue

            for chat in self._group_chats_from_bookmark_xml(items.xml):
                group_chats[chat.jid] = chat

        for chat in await self._xep_0048_bookmarked_group_chats("xep_0049"):
            group_chats.setdefault(chat.jid, chat)
        for chat in await self._xep_0048_bookmarked_group_chats("xep_0223"):
            group_chats.setdefault(chat.jid, chat)
        for chat in await self._legacy_pubsub_bookmarked_group_chats():
            group_chats.setdefault(chat.jid, chat)
        for chat in await self._private_xml_bookmarked_group_chats():
            group_chats.setdefault(chat.jid, chat)

        return list(group_chats.values())

    async def _xep_0048_bookmarked_group_chats(self, method: str) -> list[Chat]:
        try:
            result = await self["xep_0048"].get_bookmarks(method=method, timeout=10)
        except Exception:
            return []

        try:
            if method == "xep_0223":
                bookmarks = result["pubsub"]["items"]["item"]["bookmarks"]
            else:
                bookmarks = result["private"]["bookmarks"]
            conferences = bookmarks["conferences"]
        except Exception:
            return self._group_chats_from_legacy_bookmark_xml(result.xml)

        chats: list[Chat] = []
        for conference in conferences:
            jid = str(conference["jid"] or "").strip()
            if not jid:
                continue

            name = str(conference["name"] or "").strip() or jid
            chats.append(
                Chat(
                    jid=jid,
                    name=normalize_chat_name(jid, name),
                    is_group=True,
                )
            )
        return chats

    async def _legacy_pubsub_bookmarked_group_chats(self) -> list[Chat]:
        for jid in (self.boundjid.bare, None):
            try:
                items = await self["xep_0060"].get_items(
                    jid,
                    LEGACY_BOOKMARKS_NS,
                    timeout=10,
                )
            except Exception:
                continue

            chats = self._group_chats_from_legacy_bookmark_xml(items.xml)
            if chats:
                return chats

        return []

    async def _private_xml_bookmarked_group_chats(self) -> list[Chat]:
        try:
            iq = self.make_iq_get()
            query = ET.SubElement(iq.xml, f"{{{PRIVATE_XML_NS}}}query")
            ET.SubElement(query, f"{{{LEGACY_BOOKMARKS_NS}}}storage")
            result = await iq.send(timeout=10)
        except Exception:
            return []

        return self._group_chats_from_legacy_bookmark_xml(result.xml)

    async def _adhoc_group_chats(self, component_domains: set[str]) -> list[Chat]:
        group_chats: dict[str, Chat] = {}
        for domain in component_domains:
            commands = await self._adhoc_commands(domain)
            group_nodes = {
                node
                for node, name in commands
                if node == SLIDGE_GROUPS_COMMAND or self._is_groups_command(node, name)
            }
            group_nodes.add(SLIDGE_GROUPS_COMMAND)
            for node in group_nodes:
                try:
                    result = await self["xep_0050"].send_command(
                        domain,
                        node,
                        timeout=15,
                    )
                except Exception:
                    continue

                for chat in self._group_chats_from_command_xml(result.xml):
                    group_chats[chat.jid] = chat
        return list(group_chats.values())

    async def _adhoc_commands(self, jid: str) -> list[tuple[str, str]]:
        try:
            commands = await self["xep_0050"].get_commands(jid, timeout=10)
        except Exception:
            if self._is_probable_whatsapp_bridge_jid(jid):
                self._debug_whatsapp(f"commands {jid}: unavailable")
            return []

        discovered: list[tuple[str, str]] = []
        for item in commands.xml.findall(f".//{{{DISCO_ITEMS_NS}}}item"):
            node = item.attrib.get("node", "").strip()
            name = item.attrib.get("name", "").strip()
            if node:
                discovered.append((node, name))
        return discovered

    async def request_whatsapp_relogin(
        self,
        component_jid: str,
        *,
        allow_recovery: bool = True,
    ) -> None:
        await self.cancel_whatsapp_linking(component_jid, silent=True)
        try:
            command = await self["xep_0050"].send_command(
                component_jid,
                SLIDGE_RELOGIN_COMMAND,
                timeout=15,
            )
            command_status = str(command["command"]["status"] or "")
            command_text = self._command_result_text(command.xml)
            if command_status == "executing":
                self._remember_whatsapp_link_session(
                    component_jid,
                    SLIDGE_RELOGIN_COMMAND,
                    str(command["command"]["sessionid"] or ""),
                    "qr",
                )
                self._emit_whatsapp_status(
                    component_jid,
                    "needs_qr",
                    command_text or "Se solicito un nuevo QR de vinculacion.",
                )
                return

            if self._means_already_connected(command_text):
                self._emit_whatsapp_status(component_jid, "connected", command_text)
                return
        except IqTimeout:
            self._emit_whatsapp_status(
                component_jid,
                "needs_qr",
                "Se solicito un nuevo QR de vinculacion.",
            )
            return
        except Exception as exc:
            error_text = _format_xmpp_error(exc)
            if "already logging in" in error_text.casefold():
                self._emit_whatsapp_status(
                    component_jid,
                    "needs_qr",
                    (
                        "Ya hay una vinculacion por QR en curso. "
                        "Si no ves el QR, usa codigo por telefono."
                    ),
                )
                return
            if self._means_pairing_command_forbidden(error_text):
                if self._means_already_connected(error_text):
                    self._emit_whatsapp_status(component_jid, "connected", error_text)
                    return
                if allow_recovery and await self._request_whatsapp_logout(component_jid):
                    await asyncio.sleep(1)
                    await self.request_whatsapp_relogin(
                        component_jid,
                        allow_recovery=False,
                    )
                    return
                self._emit_whatsapp_status(
                    component_jid,
                    "needs_pairing",
                    (
                        "Slidge rechazo el comando de vinculacion aunque no hay confirmacion "
                        "de conexion. Usa codigo por telefono o espera unos segundos y reintenta."
                    ),
                )
                return

            self._emit(
                XmppError(f"No se pudo iniciar la revinculacion por QR: {error_text}")
            )
            return

        self._emit_whatsapp_status(
            component_jid,
            "needs_qr",
            command_text or "Se solicito un nuevo QR de vinculacion.",
        )

    def _remember_whatsapp_link_session(
        self,
        component_jid: str,
        command_node: str,
        session_id: str,
        mode: str,
    ) -> None:
        if not session_id:
            return

        self._whatsapp_link_sessions[component_jid] = (command_node, session_id)
        self._emit(
            WhatsAppLinkSessionStarted(
                component_jid=component_jid,
                command_node=command_node,
                session_id=session_id,
                mode=mode,
            )
        )

    def _clear_whatsapp_link_session(
        self,
        component_jid: str,
        command_node: str = "",
        session_id: str = "",
        *,
        canceled: bool = False,
        detail: str = "",
    ) -> None:
        existing = self._whatsapp_link_sessions.get(component_jid)
        if existing is not None and (
            (not command_node or existing[0] == command_node)
            and (not session_id or existing[1] == session_id)
        ):
            self._whatsapp_link_sessions.pop(component_jid, None)

        self._emit(
            WhatsAppLinkSessionEnded(
                component_jid=component_jid,
                command_node=command_node,
                session_id=session_id,
                canceled=canceled,
                detail=detail,
            )
        )

    async def cancel_whatsapp_linking(self, component_jid: str, *, silent: bool = False) -> bool:
        session = self._whatsapp_link_sessions.get(component_jid)
        if session is None:
            if not silent:
                self._emit_whatsapp_status(
                    component_jid,
                    self._last_whatsapp_status_by_component.get(component_jid, "needs_pairing"),
                    "No hay una vinculacion en curso para cancelar.",
                )
            return False

        command_node, session_id = session
        try:
            await self["xep_0050"].send_command(
                component_jid,
                command_node,
                action="cancel",
                sessionid=session_id,
                timeout=10,
            )
        except Exception as exc:
            error_text = _format_xmpp_error(exc)
            self._debug_whatsapp(
                f"cancel linking failed for {component_jid}: {error_text}"
            )
            self._clear_whatsapp_link_session(
                component_jid,
                command_node,
                session_id,
                detail=error_text,
            )
            if not silent:
                self._emit(XmppError(f"No se pudo cancelar la vinculacion: {error_text}"))
            return False

        self._clear_whatsapp_link_session(
            component_jid,
            command_node,
            session_id,
            canceled=True,
            detail="Vinculacion cancelada.",
        )
        if not silent:
            self._emit_whatsapp_status(component_jid, "needs_pairing", "Vinculacion cancelada.")
        return True

    async def _request_whatsapp_logout(self, component_jid: str) -> bool:
        try:
            await self["xep_0050"].send_command(component_jid, "wa_logout", timeout=15)
        except Exception as exc:
            self._debug_whatsapp(
                f"logout recovery failed for {component_jid}: {_format_xmpp_error(exc)}"
            )
            return False

        self._emit_whatsapp_status(
            component_jid,
            "logged_out",
            "Se limpio una sesion de WhatsApp incompleta.",
        )
        return True

    async def request_whatsapp_pair_code(
        self,
        component_jid: str,
        phone: str,
        *,
        allow_recovery: bool = True,
    ) -> None:
        await self.cancel_whatsapp_linking(component_jid, silent=True)
        phone = phone.strip()
        if not phone:
            self._emit(XmppError("Escribe el telefono de WhatsApp en formato internacional."))
            return

        try:
            command = await self["xep_0050"].send_command(
                component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                timeout=15,
            )
            session_id = str(command["command"]["sessionid"] or "")
            self._remember_whatsapp_link_session(
                component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                session_id,
                "code",
            )
            form = command.xml.find(f".//{{{DATA_FORMS_NS}}}x")
            if form is None:
                raise RuntimeError("El comando no devolvio formulario para telefono.")

            form_stanza = self._form_reply_with_values(form, {"phone": phone})
            result = await self["xep_0050"].send_command(
                component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                action="complete",
                payload=form_stanza,
                sessionid=session_id or None,
                timeout=20,
            )
            self._clear_whatsapp_link_session(
                component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                session_id,
            )
        except Exception as exc:
            error_text = _format_xmpp_error(exc)
            if self._means_pairing_command_forbidden(error_text):
                if self._means_already_connected(error_text):
                    self._emit_whatsapp_status(component_jid, "connected", error_text)
                    return
                if allow_recovery and await self._request_whatsapp_logout(component_jid):
                    await asyncio.sleep(1)
                    await self.request_whatsapp_pair_code(
                        component_jid,
                        phone,
                        allow_recovery=False,
                    )
                    return
                self._emit_whatsapp_status(
                    component_jid,
                    "needs_pairing",
                    (
                        "Slidge rechazo el comando de codigo aunque no hay confirmacion "
                        "de conexion. Espera unos segundos y reintenta."
                    ),
                )
                return

            self._emit(
                XmppError(
                    f"No se pudo obtener el codigo de vinculacion: {error_text}"
                )
            )
            return

        text = self._command_result_text(result.xml)
        code = self._pairing_code_from_text(text)
        if not code:
            self._emit(
                XmppError(
                    "Slidge respondio, pero no pude detectar el codigo de vinculacion."
                )
            )
            if text:
                self._emit_whatsapp_status(component_jid, "needs_pair_code", text)
            return

        self._emit(WhatsAppPairingCodeReceived(component_jid=component_jid, code=code))
        self._emit_whatsapp_status(component_jid, "needs_pair_code", text)

    @staticmethod
    def _form_reply_with_values(form_xml: ET.Element, values: dict[str, str]) -> object:
        from slixmpp.plugins.xep_0004.stanza import Form

        form = Form(xml=ET.fromstring(ET.tostring(form_xml, encoding="unicode")))
        form.reply()
        form["values"] = values
        return form

    @staticmethod
    def _command_result_text(xml: ET.Element) -> str:
        texts: list[str] = []
        for element in xml.iter():
            if element.text and element.text.strip():
                texts.append(element.text.strip())
        return " ".join(texts)

    @staticmethod
    def _pairing_code_from_text(text: str) -> str:
        normalized = text.upper()
        match = re.search(
            r"\b(?:CODE|CODIGO)\b[^A-Z0-9]{0,80}([A-Z0-9]{4}[-\s]?[A-Z0-9]{4})\b",
            normalized,
        )
        if match is None:
            matches = re.findall(r"\b([A-Z0-9]{4}[-\s][A-Z0-9]{4})\b", normalized)
            match = re.match(r"(.+)", matches[-1]) if matches else None
        return match.group(1).replace(" ", "-") if match else ""

    @staticmethod
    def _means_pairing_command_forbidden(error_text: str) -> bool:
        normalized = error_text.casefold()
        return (
            "only available for users that are not logged" in normalized
            or "already logged" in normalized
            or "already connected" in normalized
            or "refusing to pair for connected session" in normalized
        )

    @staticmethod
    def _means_already_connected(error_text: str) -> bool:
        normalized = error_text.casefold()
        return (
            "already logged" in normalized
            or "already connected" in normalized
            or "refusing to pair for connected session" in normalized
        )

    def _debug_whatsapp_commands(self, jid: str, commands: list[tuple[str, str]]) -> None:
        if not commands:
            self._debug_whatsapp(f"commands {jid}: none")
            return

        rendered = [
            f"{node} ({self._safe_debug_text(name) or 'no name'})" for node, name in commands
        ]
        state_hints: list[str] = []
        for node, name in commands:
            normalized = f"{node} {name}".casefold()
            if self._is_register_command(node, name):
                state_hints.append("register_available")
            if node == SLIDGE_PAIR_PHONE_COMMAND or "pair-phone" in normalized:
                state_hints.append("pair_phone_available")
            if "re-login" in normalized or "relogin" in normalized:
                state_hints.append("relogin_available")
            if "logout" in normalized:
                state_hints.append("logged_in_command_seen")

        self._debug_whatsapp(
            f"commands {jid}: {rendered}; state={self._whatsapp_command_state(commands)} "
            f"hints={sorted(set(state_hints)) or 'none'}"
        )

    def _debug_whatsapp_admin_message(self, from_jid: str, body: str) -> None:
        if not body:
            return
        if not self._is_probable_whatsapp_bridge_jid(from_jid) and not self._is_whatsapp_state_text(
            body
        ):
            return

        self._debug_whatsapp(
            f"admin message from={from_jid} state={self._whatsapp_state_hint(body)} "
            f"body={self._safe_debug_text(body)}"
        )
        state = self._whatsapp_state_hint(body)
        if state != "unknown":
            self._emit_whatsapp_status(from_jid, state, body)

    @classmethod
    def _is_whatsapp_component_admin_message(cls, from_jid: str, body: str) -> bool:
        bare_jid = from_jid.split("/", 1)[0]
        return cls._is_probable_whatsapp_bridge_jid(bare_jid) and cls._is_whatsapp_state_text(body)

    @staticmethod
    def _debug_whatsapp(message: str) -> None:
        print(f"{WHATSAPP_DEBUG_PREFIX} {message}", flush=True)

    def _emit_whatsapp_status(self, component_jid: str, status: str, detail: str = "") -> None:
        component_jid = component_jid.split("/", 1)[0]
        key = f"{status}\n{detail}"
        if self._last_whatsapp_status_by_component.get(component_jid) == key:
            return

        self._last_whatsapp_status_by_component[component_jid] = key
        self._emit(
            WhatsAppBridgeStatus(
                status=status,
                component_jid=component_jid,
                detail=self._safe_debug_text(detail, limit=500),
            )
        )

    def _is_whatsapp_qr_image(
        self,
        from_jid: str,
        body: str,
        media_url: str,
        media_kind: str,
    ) -> bool:
        if media_kind != "image" or not media_url:
            return False
        if not self._is_probable_whatsapp_bridge_jid(from_jid):
            return False
        if "qr" in body.casefold():
            return True

        status = self._last_whatsapp_status_by_component.get(from_jid.split("/", 1)[0], "")
        if BridgeXmppClient._is_slidge_attachment_image_url(media_url) and not status.startswith("connected"):
            return True

        return any(
            status.startswith(candidate)
            for candidate in (
                "needs_qr",
                "needs_pairing",
                "needs_pair_code",
                "needs_relogin",
                "logged_out",
            )
        )

    @staticmethod
    def _is_slidge_attachment_image_url(url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url)
        path = parsed.path.casefold()
        return "/slidge-attachments/" in path and path.endswith(IMAGE_EXTENSIONS)

    @classmethod
    def _whatsapp_qr_image_data_from_xml(
        cls,
        from_jid: str,
        body: str,
        xml: ET.Element,
    ) -> tuple[bytes, str, str] | None:
        if not cls._is_probable_whatsapp_bridge_jid(from_jid):
            return None
        if not cls._message_may_carry_whatsapp_qr(body, xml):
            return None

        embedded = cls._embedded_image_data_from_xml(xml)
        if embedded is None:
            return None

        image_data, mime = embedded
        return image_data, mime, cls._qr_filename_from_mime(mime)

    @classmethod
    def _embedded_image_data_from_xml(cls, xml: ET.Element) -> tuple[bytes, str] | None:
        for node in xml.iter():
            for attribute in ("src", "uri", "url", "href"):
                value = node.attrib.get(attribute, "").strip()
                if not value.startswith("data:image/"):
                    continue
                decoded = cls._image_data_from_data_uri(value)
                if decoded is not None:
                    return decoded

            if not cls._is_bob_data_node(node):
                continue

            mime = cls._node_mime(node)
            if not cls._is_raster_image_mime(mime):
                continue

            encoded = (node.text or "").strip()
            decoded = cls._decode_base64_image(encoded)
            if decoded is not None:
                return decoded, mime

        return None

    @staticmethod
    def _is_bob_data_node(node: ET.Element) -> bool:
        namespace = ""
        if node.tag.startswith("{"):
            namespace = node.tag.split("}", 1)[0][1:]
        return namespace == BOB_NS and node.tag.rsplit("}", 1)[-1] == "data"

    @staticmethod
    def _node_mime(node: ET.Element) -> str:
        for attribute in ("type", "media-type", "mime-type", "content-type"):
            value = node.attrib.get(attribute, "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _image_data_from_data_uri(cls, value: str) -> tuple[bytes, str] | None:
        header, separator, payload = value.partition(",")
        if not separator:
            return None
        mime = header.removeprefix("data:").split(";", 1)[0]
        if not cls._is_raster_image_mime(mime):
            return None
        decoded = cls._decode_base64_image(payload.strip())
        if decoded is None:
            return None
        return decoded, mime

    @staticmethod
    def _is_raster_image_mime(mime: str) -> bool:
        return mime.split(";", 1)[0].strip().casefold() in {
            "image/avif",
            "image/bmp",
            "image/gif",
            "image/heic",
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
        }

    @staticmethod
    def _decode_base64_image(value: str) -> bytes | None:
        if not value:
            return None
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None

    @staticmethod
    def _qr_filename_from_mime(mime: str) -> str:
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(mime.casefold(), ".png")
        return f"qr-whatsapp{extension}"

    @classmethod
    def _message_may_carry_whatsapp_qr(cls, body: str, xml: ET.Element) -> bool:
        normalized = body.casefold()
        if "qr" in normalized or "scan" in normalized or "vincul" in normalized:
            return True
        for node in xml.iter():
            if cls._is_bob_data_node(node) and cls._is_raster_image_mime(cls._node_mime(node)):
                return True
            for attribute in ("src", "uri", "url", "href"):
                image_data = cls._image_data_from_data_uri(node.attrib.get(attribute, "").strip())
                if image_data is not None:
                    return True
        return False

    @classmethod
    def _is_whatsapp_qr_candidate_message(
        cls,
        from_jid: str,
        body: str,
        xml: ET.Element,
        media_url: str,
        media_kind: str,
    ) -> bool:
        if not cls._is_probable_whatsapp_bridge_jid(from_jid):
            return False
        if media_kind == "image" or media_url:
            return True
        return cls._message_may_carry_whatsapp_qr(body, xml)

    @classmethod
    def _debug_whatsapp_qr_candidate(
        cls,
        from_jid: str,
        body: str,
        xml: ET.Element,
        media_url: str,
        media_kind: str,
    ) -> None:
        nodes: list[str] = []
        for node in xml.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            attrs = {}
            for key, value in node.attrib.items():
                if key in {"src", "uri", "url", "href", "cid", "type", "media-type"}:
                    attrs[key] = cls._safe_debug_text(value, limit=90)
            if attrs:
                nodes.append(f"{local_name}{attrs}")
            elif local_name in {"data", "image", "thumbnail", "file", "url", "x"}:
                nodes.append(local_name)
            if len(nodes) >= 12:
                break

        cls._debug_whatsapp(
            "qr candidate not handled "
            f"from={from_jid} body={cls._safe_debug_text(body, limit=180) or '-'} "
            f"media_url={cls._safe_debug_text(media_url, limit=180) or '-'} "
            f"media_kind={media_kind or '-'} nodes={nodes or 'none'}"
        )

    @staticmethod
    def _is_probable_whatsapp_bridge_jid(jid: str) -> bool:
        bare_jid = jid.split("/", 1)[0].casefold()
        if not bare_jid:
            return False
        local_part = bare_jid.split("@", 1)[0]
        domain = bare_jid.split("@", 1)[-1]
        if local_part.startswith(("+", "#")):
            return False
        return "whatsapp" in bare_jid or "slidge" in bare_jid or domain.startswith("wa.")

    @classmethod
    def _is_whatsapp_state_text(cls, text: str) -> bool:
        return cls._whatsapp_state_hint(text) != "unknown"

    @staticmethod
    def _whatsapp_state_hint(text: str) -> str:
        normalized = text.casefold()
        if not normalized:
            return "unknown"
        if normalized.startswith("connected as ") or " connected as +" in normalized:
            return "connected"
        if "qr scan needed" in normalized or "scan the following qr" in normalized:
            return "needs_qr"
        if "pair-phone" in normalized or "input the following code" in normalized:
            return "needs_pair_code"
        if "pairing successful" in normalized:
            return "paired"
        if "you are not connected to this gateway" in normalized:
            return "needs_relogin"
        if "did not flash the qr code in time" in normalized:
            return "needs_relogin"
        if "logged out" in normalized or "re-login" in normalized or "re-scan" in normalized:
            return "logged_out"
        if "connection error" in normalized:
            return "connection_error"
        if "register" in normalized and "whatsapp" in normalized:
            return "needs_registration"
        return "unknown"

    @classmethod
    def _whatsapp_command_state(cls, commands: list[tuple[str, str]]) -> str:
        has_logout = False
        has_pair_phone = False
        has_relogin = False
        has_register = False
        for node, name in commands:
            normalized = f"{node} {name}".casefold()
            has_logout = has_logout or node == "wa_logout" or " logout" in f" {normalized}"
            has_pair_phone = (
                has_pair_phone
                or node == SLIDGE_PAIR_PHONE_COMMAND
                or "pair-phone" in normalized
            )
            has_relogin = has_relogin or "re-login" in normalized or "relogin" in normalized
            has_register = has_register or cls._is_register_command(node, name)

        if has_logout:
            return "connected"
        if has_pair_phone:
            return "needs_pairing"
        if has_relogin:
            return "needs_relogin"
        if has_register:
            return "needs_registration"
        return "unknown"

    @staticmethod
    def _is_register_command(node: str, name: str) -> bool:
        normalized_node = node.casefold().strip()
        normalized_name = name.casefold().strip()
        if "unregister" in normalized_node or "unregister" in normalized_name:
            return False
        return (
            normalized_node.endswith("/register")
            or normalized_node.endswith(":register")
            or normalized_node == "register"
            or normalized_name == "register"
            or normalized_name.startswith("register ")
        )

    @staticmethod
    def _safe_debug_text(value: str, limit: int = 300) -> str:
        single_line = " ".join(str(value).split())
        if len(single_line) > limit:
            single_line = single_line[: limit - 3] + "..."
        return single_line.encode("ascii", errors="backslashreplace").decode("ascii")

    @staticmethod
    def _is_groups_command(node: str, name: str) -> bool:
        normalized = f"{node} {name}".casefold()
        return "groups" in normalized and "list" in normalized

    async def _request_group_reinvite(self, component_domains: set[str]) -> None:
        for domain in component_domains:
            try:
                await self["xep_0050"].send_command(
                    domain,
                    SLIDGE_REINVITE_GROUPS_COMMAND,
                    timeout=15,
                )
            except Exception:
                continue

    def _group_chat_from_invite_stanza(self, stanza: object) -> Chat | None:
        try:
            invite = stanza["groupchat_invite"]
            jid = str(invite["jid"] or "").strip()
            reason = str(invite["reason"] or "").strip()
        except Exception:
            jid = ""
            reason = ""

        if not jid:
            invite_xml = stanza.xml.find(f".//{{{DIRECT_INVITE_NS}}}x")
            if invite_xml is not None:
                jid = invite_xml.attrib.get("jid", "").strip()
                reason = invite_xml.attrib.get("reason", "").strip()

        if not jid:
            return None

        name = self._group_name_from_invite_reason(reason) or jid
        return Chat(jid=jid, name=normalize_chat_name(jid, name), is_group=True)

    @staticmethod
    def _group_name_from_invite_reason(reason: str) -> str:
        reason = " ".join(reason.split())
        if not reason or "gateway is configured" in reason:
            return ""

        return reason

    def _group_chats_from_bookmark_xml(self, xml: ET.Element) -> list[Chat]:
        chats: list[Chat] = []
        for item in xml.findall(f".//{{{BOOKMARKS_NS}}}conference/.."):
            jid = item.attrib.get("id", "").strip()
            conference = item.find(f"{{{BOOKMARKS_NS}}}conference")
            if conference is None:
                continue

            jid = jid or conference.attrib.get("jid", "").strip()
            if not jid:
                continue

            name = conference.attrib.get("name", "").strip() or jid
            notifications_muted, notification_settings_known = (
                self._bookmark_notification_settings(conference)
            )
            chats.append(
                Chat(
                    jid=jid,
                    name=normalize_chat_name(jid, name),
                    is_group=True,
                    notifications_muted=notifications_muted,
                    notification_settings_known=notification_settings_known,
                )
            )
        return chats

    @classmethod
    def _group_chats_from_legacy_bookmark_xml(cls, xml: ET.Element) -> list[Chat]:
        chats: list[Chat] = []
        for conference in xml.findall(f".//{{{LEGACY_BOOKMARKS_NS}}}conference"):
            jid = conference.attrib.get("jid", "").strip()
            if not jid:
                continue

            notifications_muted, notification_settings_known = (
                cls._bookmark_notification_settings(conference)
            )
            name = conference.attrib.get("name", "").strip() or jid
            chats.append(
                Chat(
                    jid=jid,
                    name=normalize_chat_name(jid, name),
                    is_group=True,
                    notifications_muted=notifications_muted,
                    notification_settings_known=notification_settings_known,
                )
            )
        return chats

    def _group_chats_from_bookmark_event_stanza(self, stanza: object) -> list[Chat]:
        try:
            xml = stanza.xml
        except Exception:
            return []

        chats = self._group_chats_from_bookmark_xml(xml)
        chats.extend(self._group_chats_from_legacy_bookmark_xml(xml))
        return chats

    @staticmethod
    def _bookmark_notification_settings(conference: ET.Element) -> tuple[bool, bool]:
        notify = None
        for namespace in NOTIFICATION_SETTINGS_NAMESPACES:
            notify = conference.find(f".//{{{namespace}}}notify")
            if notify is not None:
                break
        if notify is None:
            return False, False

        for namespace in NOTIFICATION_SETTINGS_NAMESPACES:
            if notify.find(f"{{{namespace}}}never") is not None:
                return True, True
            if (
                notify.find(f"{{{namespace}}}always") is not None
                or notify.find(f"{{{namespace}}}on-mention") is not None
            ):
                return False, True

        for child in notify:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "never":
                return True, True
            if local_name in {"always", "on-mention"}:
                return False, True

        return False, False

    def _group_chats_from_command_xml(self, xml: ET.Element) -> list[Chat]:
        group_chats: dict[str, Chat] = {}
        for item in xml.findall(f".//{{{DATA_FORMS_NS}}}item"):
            values = self._data_form_item_values(item)
            jid = values.get("jid", "").strip()
            if not jid:
                continue

            group_chats[jid] = self._group_chat_from_command_values(jid, values)

        for jid in self._jids_from_xml_text(xml):
            group_chats.setdefault(
                jid,
                Chat(jid=jid, name=normalize_chat_name(jid), is_group=True),
            )
        return list(group_chats.values())

    @classmethod
    def _group_chat_from_command_values(cls, jid: str, values: dict[str, str]) -> Chat:
        name = (
            values.get("name", "")
            or values.get("title", "")
            or values.get("display-name", "")
            or jid
        )
        member_count = cls._int_or_zero(
            values.get("participants", "")
            or values.get("participant_count", "")
            or values.get("member_count", "")
            or values.get("members", "")
        )
        muted_value = (
            values.get("muted", "")
            or values.get("notifications_muted", "")
            or values.get("notifications-muted", "")
        )
        return Chat(
            jid=jid,
            name=normalize_chat_name(jid, name),
            is_group=True,
            notifications_muted=cls._truthy_value(muted_value),
            notification_settings_known=bool(muted_value),
            group_member_count=member_count,
            is_self_group=member_count == 1,
        )

    @staticmethod
    def _truthy_value(value: str) -> bool:
        return value.strip().casefold() in {"1", "true", "yes", "si", "sí", "muted"}

    @staticmethod
    def _data_form_item_values(item: ET.Element) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in item.findall(f"{{{DATA_FORMS_NS}}}field"):
            var = field.attrib.get("var", "")
            value = field.findtext(f"{{{DATA_FORMS_NS}}}value", default="") or ""
            if var:
                values[var] = value
        return values

    @staticmethod
    def _jids_from_xml_text(xml: ET.Element) -> list[str]:
        text = " ".join(part for part in xml.itertext() if part)
        return [match.group(1).rstrip(").,;]") for match in JID_PATTERN.finditer(text)]

    def _join_group_chat(self, jid: str) -> None:
        if jid in self._joined_group_chat_jids:
            return

        self._joined_group_chat_jids.add(jid)
        try:
            future = self["xep_0045"].join_muc(jid, self._muc_nick(), maxhistory="0")
            future.add_done_callback(lambda task, room_jid=jid: self._finish_group_join(room_jid, task))
        except Exception:
            self._joined_group_chat_jids.discard(jid)

    def _finish_group_join(self, jid: str, task: asyncio.Future) -> None:
        try:
            task.result()
        except (asyncio.TimeoutError, IqError, IqTimeout):
            self._joined_group_chat_jids.discard(jid)
        except Exception:
            self._joined_group_chat_jids.discard(jid)

    def _muc_nick(self) -> str:
        return str(self.boundjid.user or self.boundjid.bare or self.settings.jid)

    def join_group_chat(self, chat_jid: str) -> None:
        if not self._jid_may_be_group_chat(chat_jid):
            return

        self._group_chat_jids.add(chat_jid)
        self._join_group_chat(chat_jid)

    def request_contact_presence_subscription(self, chat_jid: str) -> None:
        bare_jid = chat_jid.split("/", 1)[0]
        if (
            not bare_jid
            or bare_jid in self._presence_subscription_jids
            or self._jid_may_be_group_chat(bare_jid)
            or self._is_probable_whatsapp_bridge_jid(bare_jid)
        ):
            return

        self._presence_subscription_jids.add(bare_jid)
        try:
            self.send_presence_subscription(pto=bare_jid, ptype="subscribe")
        except Exception:
            self._presence_subscription_jids.discard(bare_jid)

    def monitor_group_chats(self, chat_jids: Iterable[str]) -> None:
        group_jids = {chat_jid for chat_jid in chat_jids if self._jid_may_be_group_chat(chat_jid)}
        if not group_jids:
            return

        self._group_chat_jids.update(group_jids)
        for group_jid in group_jids:
            self._join_group_chat(group_jid)
        asyncio.create_task(self._enrich_monitored_group_chats(group_jids))
        asyncio.create_task(self.load_recent_activity(group_jids))

    async def _enrich_monitored_group_chats(self, group_jids: set[str]) -> None:
        chats: list[Chat] = []
        for group_jid in group_jids:
            chat = await self._group_chat_from_disco(group_jid, fallback_name=group_jid)
            if chat is not None:
                chats.append(chat)

        if chats:
            self._emit(ChatsDiscovered(chats))

    async def load_history(
        self,
        chat_jid: str,
        limit: int | None = None,
        before: datetime | None = None,
        older: bool = False,
        allow_unfiltered_fallback: bool = True,
        background: bool = False,
    ) -> None:
        try:
            archived_messages = await self._load_history_page(
                chat_jid,
                limit=limit,
                before=before,
                with_jid_filter=True,
            )
            if (
                allow_unfiltered_fallback
                and not older
                and self._history_page_needs_unfiltered_fallback(archived_messages, limit)
            ):
                unfiltered_messages = await self._load_history_page(
                    chat_jid,
                    limit=limit,
                    before=before,
                    with_jid_filter=False,
                )
                archived_messages = self._deduplicate_messages(
                    archived_messages + unfiltered_messages
                )

            archived_messages.sort(key=lambda message: message.sent_at)
            loaded_count = len(archived_messages)
            self._emit(
                MessageHistoryLoaded(
                    chat_jid=chat_jid,
                    messages=archived_messages,
                    older=older,
                    complete=limit is not None and loaded_count == 0,
                    background=background,
                )
            )
        except Exception as exc:
            self._emit(XmppError(f"No se pudo cargar el historial de {chat_jid}: {exc}"))

    async def _load_history_page(
        self,
        chat_jid: str,
        limit: int | None,
        before: datetime | None,
        with_jid_filter: bool,
    ) -> list[Message]:
        mam = self["xep_0313"]
        messages: list[Message] = []
        page_size = 100
        total = 500 if limit is None else max(limit * 10, 100)

        async for result in mam.iterate(
            jid=chat_jid if self._uses_room_archive(chat_jid) else None,
            with_jid=(
                None
                if self._uses_room_archive(chat_jid)
                else chat_jid if with_jid_filter else None
            ),
            end=before,
            reverse=True,
            rsm={"max": min(page_size, 100)},
            total=total,
        ):
            result_chat_jid = chat_jid if with_jid_filter else self._chat_jid_from_mam_result(
                result
            )
            if result_chat_jid != chat_jid:
                continue

            message = self._message_from_mam_result(result_chat_jid, result)
            if message and message.chat_jid == chat_jid:
                messages.append(message)

            if limit is not None and len(messages) >= limit:
                break

        return messages

    def _uses_room_archive(self, jid: str) -> bool:
        if jid in self._group_chat_jids:
            return True

        return BridgeXmppClient._jid_is_hash_group_chat(jid)

    @staticmethod
    def _history_page_needs_unfiltered_fallback(
        messages: list[Message],
        limit: int | None,
    ) -> bool:
        if limit is None:
            return False

        if len(messages) < limit:
            return True

        return all(message.outgoing for message in messages)

    @staticmethod
    def _deduplicate_messages(messages: list[Message]) -> list[Message]:
        seen: set[tuple[str, str, str, str, bool, str, str]] = set()
        unique_messages: list[Message] = []
        for message in messages:
            key = (
                message.message_id,
                message.sent_at.isoformat(),
                message.sender_jid,
                message.body,
                message.outgoing,
                message.audio_url,
                message.reply_quote,
            )
            if key in seen:
                continue

            seen.add(key)
            unique_messages.append(message)

        return unique_messages

    async def preload_histories(
        self,
        chat_jids: list[str],
        limit: int = 20,
        concurrency: int = 4,
    ) -> None:
        async def preload(chat_jid: str) -> None:
            async with self._history_preload_semaphore:
                await self.load_history(
                    chat_jid,
                    limit=limit,
                    allow_unfiltered_fallback=True,
                    background=True,
                )

        await asyncio.gather(*(preload(chat_jid) for chat_jid in chat_jids))

    async def load_recent_activity(self, roster_jids: set[str], limit: int = 1000) -> None:
        loaded_chat_jids: set[str] = set()
        messages_by_chat: dict[str, list[Message]] = {}
        try:
            group_jids = {jid for jid in roster_jids if self._uses_room_archive(jid)}
            for chat_jid in group_jids:
                group_messages = await self._load_history_page(
                    chat_jid,
                    limit=1,
                    before=None,
                    with_jid_filter=True,
                )
                if not group_messages:
                    continue

                message = group_messages[-1]
                messages_by_chat.setdefault(chat_jid, []).append(message)
                loaded_chat_jids.add(chat_jid)
                self._emit(
                    ChatActivityLoaded(
                        chat_jid=chat_jid,
                        sent_at=message.sent_at,
                        preview=self._message_body_for_display(
                            message.body,
                            message.media_url,
                            message.media_kind,
                            message.media_filename,
                            message.media_size,
                        ),
                        is_group=True,
                    )
                )

            mam = self["xep_0313"]
            async for result in mam.iterate(reverse=True, rsm={"max": 50}, total=limit):
                chat_jid = self._chat_jid_from_mam_result(result)
                if not chat_jid:
                    continue

                if roster_jids and chat_jid not in roster_jids:
                    continue

                message = self._message_from_mam_result(chat_jid, result)
                if message:
                    messages_by_chat.setdefault(chat_jid, []).append(message)

                if chat_jid in loaded_chat_jids:
                    continue

                preview = self._message_body_from_mam_result(result)
                media_url, media_kind, _, _, media_size, _ = self._media_from_mam_result(result)
                if not preview and not media_url:
                    continue

                sent_at = self._sent_at_from_mam_result(result)
                if not sent_at:
                    continue

                is_group = (
                    self._mam_result_is_groupchat(result)
                    or chat_jid in self._group_chat_jids
                )
                loaded_chat_jids.add(chat_jid)
                self._emit(
                    ChatActivityLoaded(
                        chat_jid=chat_jid,
                        sent_at=sent_at,
                        preview=self._message_body_for_display(
                            preview,
                            media_url,
                            media_kind,
                            "",
                            media_size,
                        ),
                        is_group=is_group,
                    )
                )

            for chat_jid, messages in messages_by_chat.items():
                self._emit(
                    MessageHistoryLoaded(
                        chat_jid=chat_jid,
                        messages=list(reversed(messages)),
                        older=False,
                        background=True,
                    )
                )
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

    async def fetch_contact_avatar(self, chat_jid: str) -> None:
        metadata = await self["xep_0060"].get_items(
            chat_jid,
            AVATAR_METADATA_NS,
            max_items=1,
            timeout=10,
        )
        avatar_id, mime = self._avatar_metadata_from_iq(metadata)
        if not avatar_id:
            self._emit(
                ContactAvatarUnavailable(
                    chat_jid=chat_jid,
                    detail="Este contacto no tiene foto de perfil disponible.",
                )
            )
            return

        avatar = await self["xep_0084"].retrieve_avatar(
            chat_jid,
            avatar_id,
            timeout=10,
        )
        data = self._avatar_data_from_iq(avatar)
        if not data:
            self._emit(
                ContactAvatarUnavailable(
                    chat_jid=chat_jid,
                    detail="No se pudo descargar la foto de perfil.",
                )
            )
            return

        self._emit(
            ContactAvatarReceived(
                chat_jid=chat_jid,
                data=data,
                mime=mime,
                avatar_id=avatar_id,
            )
        )

    @staticmethod
    def _avatar_metadata_from_iq(iq: object) -> tuple[str, str]:
        xml = getattr(iq, "xml", None)
        if xml is None:
            return "", ""

        info = xml.find(f".//{{{AVATAR_METADATA_NS}}}info")
        if info is None:
            return "", ""

        return info.attrib.get("id", "").strip(), info.attrib.get("type", "").strip()

    @staticmethod
    def _avatar_data_from_iq(iq: object) -> bytes:
        xml = getattr(iq, "xml", None)
        if xml is None:
            return b""

        data = xml.find(f".//{{{AVATAR_DATA_NS}}}data")
        if data is None or not data.text:
            return b""

        try:
            return base64.b64decode(data.text.strip(), validate=True)
        except (binascii.Error, ValueError):
            return b""

    def _message_from_mam_result(self, chat_jid: str, result: object) -> Message | None:
        retraction = self._message_retraction_from_mam_result(chat_jid, result)
        if retraction is not None:
            return retraction

        body = self._message_body_from_mam_result(result)
        (
            media_url,
            media_kind,
            media_mime,
            media_filename,
            media_size,
            media_duration,
        ) = self._media_from_mam_result(result)
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return None

        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        is_group = self._stanza_is_groupchat(stanza)
        message_chat_jid = self._chat_jid_from_mam_result(result) if is_group else chat_jid
        sender_jid = self._sender_jid_from_stanza(stanza, is_group=is_group)
        sender_name = self._sender_name_from_stanza(stanza, is_group=is_group)
        outgoing = self._message_is_outgoing(stanza, sender_jid, is_group=is_group)
        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            stanza.xml,
        )
        sent_at = self._sent_at_from_mam_result(result) or self._sent_at_from_stanza_delay(stanza)
        if sent_at is None:
            return None

        return Message(
            chat_jid=message_chat_jid,
            sender_jid="Yo" if outgoing else sender_jid,
            sender_name="" if outgoing else sender_name,
            body=display_body,
            sent_at=sent_at,
            outgoing=outgoing,
            audio_url=audio_url,
            media_url=media_url,
            media_kind=media_kind,
            media_mime=media_mime,
            media_filename=media_filename,
            media_size=media_size,
            media_duration_seconds=media_duration,
            message_id=str(stanza["id"] or result["mam_result"]["id"] or ""),
            chat_is_group=is_group or message_chat_jid in self._group_chat_jids,
            reply_quote=reply_quote,
            delivery_state="sent" if outgoing else "",
        )

    def _emit_message_from_stanza(self, stanza: object, outgoing: bool) -> None:
        if stanza["type"] not in ("chat", "normal", "groupchat"):
            return

        retraction = self._message_retraction_from_stanza(stanza, outgoing=outgoing)
        if retraction is not None:
            self._emit(MessageReceived(retraction))
            return

        is_group = self._stanza_is_groupchat(stanza)
        body = str(stanza["body"] or "").strip()
        (
            media_url,
            media_kind,
            media_mime,
            media_filename,
            media_size,
            media_duration,
        ) = self._media_from_stanza(stanza)
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return

        if is_group and outgoing and str(stanza["from"].bare) == self.boundjid.bare:
            chat_jid = str(stanza["to"].bare)
        elif is_group:
            chat_jid = str(stanza["from"].bare)
        elif outgoing:
            chat_jid = str(stanza["to"].bare)
        else:
            chat_jid = str(stanza["from"].bare)
        sender_jid = "Yo" if outgoing else self._sender_jid_from_stanza(stanza, is_group=is_group)
        sender_name = "" if outgoing else self._sender_name_from_stanza(stanza, is_group=is_group)
        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            stanza.xml,
        )
        self._emit(
            MessageReceived(
                Message(
                    chat_jid=chat_jid,
                    sender_jid=sender_jid,
                    sender_name=sender_name,
                    body=display_body,
                    sent_at=self._sent_at_from_stanza_delay(stanza) or datetime.now(),
                    outgoing=outgoing,
                    audio_url=audio_url,
                    media_url=media_url,
                    media_kind=media_kind,
                    media_mime=media_mime,
                    media_filename=media_filename,
                    media_size=media_size,
                    media_duration_seconds=media_duration,
                    message_id=str(stanza["id"] or ""),
                    chat_is_group=is_group,
                    reply_quote=reply_quote,
                    delivery_state="sent" if outgoing else "",
                )
            )
        )

    def _message_from_groupchat_stanza(self, stanza: object) -> Message | None:
        retraction = self._message_retraction_from_stanza(stanza, outgoing=False)
        if retraction is not None:
            return retraction

        body = str(stanza["body"] or "").strip()
        (
            media_url,
            media_kind,
            media_mime,
            media_filename,
            media_size,
            media_duration,
        ) = self._media_from_stanza(stanza)
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return None

        sender_jid = self._sender_jid_from_stanza(stanza, is_group=True)
        sender_name = self._sender_name_from_stanza(stanza, is_group=True)
        outgoing = self._message_is_outgoing(stanza, sender_jid, is_group=True)
        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            stanza.xml,
        )
        return Message(
            chat_jid=str(stanza["from"].bare),
            sender_jid="Yo" if outgoing else sender_jid,
            sender_name="" if outgoing else sender_name,
            body=display_body,
            sent_at=self._sent_at_from_stanza_delay(stanza) or datetime.now(),
            outgoing=outgoing,
            audio_url=audio_url,
            media_url=media_url,
            media_kind=media_kind,
            media_mime=media_mime,
            media_filename=media_filename,
            media_size=media_size,
            media_duration_seconds=media_duration,
            message_id=str(stanza["id"] or ""),
            chat_is_group=True,
            reply_quote=reply_quote,
            delivery_state="sent" if outgoing else "",
        )

    def _emit_inbox_entry(self, msg: object) -> None:
        entry = self._inbox_entry_from_stanza(msg)
        if entry is None:
            return

        chat_jid, unread_count, preview, sent_at, message = entry
        if message is not None:
            self._emit(MessageReceived(message, notify=False))
        self._emit(
            ChatActivityLoaded(
                chat_jid=chat_jid,
                sent_at=sent_at,
                preview=preview,
                unread_count=unread_count,
                is_group=bool(message and message.chat_is_group)
                or chat_jid in self._group_chat_jids,
            )
        )

    def _inbox_entry_from_stanza(
        self,
        msg: object,
    ) -> tuple[str, int, str, datetime | None, Message | None] | None:
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
        message_model = None
        if result is not None:
            message = self._forwarded_message_from_xml(result)
            if message is not None:
                body = message.find(f"{{{CLIENT_NS}}}body")
                preview = (body.text or "").strip() if body is not None else ""
                media_url, media_kind, _, _, media_size, _ = self._media_from_xml(message)
                if media_url:
                    preview = self._message_body_for_display(
                        preview,
                        media_url,
                        media_kind,
                        "",
                        media_size,
                    )
                message_model = self._message_from_forwarded_xml(chat_jid, result)
                if message_model is not None and message_model.retracted:
                    preview = (
                        "Eliminaste este mensaje"
                        if message_model.outgoing
                        else "Este mensaje fue eliminado"
                    )
                elif message_model is not None and not preview:
                    preview = message_model.body
            sent_at = self._forwarded_delay_from_xml(result)

        return chat_jid, unread_count, preview, sent_at, message_model

    def _message_from_forwarded_xml(
        self,
        chat_jid: str,
        result: ET.Element,
    ) -> Message | None:
        message = self._forwarded_message_from_xml(result)
        if message is None:
            return None

        retraction = self._message_retraction_from_xml(chat_jid, message, result)
        if retraction is not None:
            return retraction

        body_node = message.find(f"{{{CLIENT_NS}}}body")
        body = (body_node.text or "").strip() if body_node is not None else ""
        media_url, media_kind, media_mime, media_filename, media_size, media_duration = (
            self._media_from_xml(message)
        )
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return None

        is_group = (
            message.attrib.get("type", "") == "groupchat"
            or self._xml_message_addresses_groupchat(message)
        )
        sender_jid = self._sender_jid_from_message_xml(message, is_group=is_group)
        sender_name = self._sender_name_from_message_xml(message, is_group=is_group)
        outgoing = self._message_xml_is_outgoing(message, is_group=is_group)
        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            message,
        )
        return Message(
            chat_jid=chat_jid,
            sender_jid="Yo" if outgoing else sender_jid,
            sender_name="" if outgoing else sender_name,
            body=display_body,
            sent_at=self._forwarded_delay_from_xml(result) or datetime.now(),
            outgoing=outgoing,
            audio_url=audio_url,
            media_url=media_url,
            media_kind=media_kind,
            media_mime=media_mime,
            media_filename=media_filename,
            media_size=media_size,
            media_duration_seconds=media_duration,
            message_id=message.attrib.get("id", "") or result.attrib.get("id", ""),
            chat_is_group=is_group or chat_jid in self._group_chat_jids,
            reply_quote=reply_quote,
            delivery_state="sent" if outgoing else "",
        )

    def _message_retraction_from_mam_result(
        self,
        chat_jid: str,
        result: object,
    ) -> Message | None:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        target_id = self._retracted_message_id_from_xml(stanza.xml)
        if not target_id:
            return None

        is_group = self._stanza_is_groupchat(stanza)
        message_chat_jid = self._chat_jid_from_mam_result(result) if is_group else chat_jid
        sender_jid = self._sender_jid_from_stanza(stanza, is_group=is_group)
        outgoing = self._message_is_outgoing(stanza, sender_jid, is_group=is_group)
        sent_at = self._sent_at_from_mam_result(result) or self._sent_at_from_stanza_delay(stanza)
        return self._retracted_message(
            chat_jid=message_chat_jid,
            message_id=target_id,
            sent_at=sent_at or datetime.now(),
            outgoing=outgoing,
            is_group=is_group or message_chat_jid in self._group_chat_jids,
        )

    def _message_retraction_from_stanza(self, stanza: object, outgoing: bool) -> Message | None:
        target_id = self._retracted_message_id_from_xml(stanza.xml)
        if not target_id:
            return None

        is_group = self._stanza_is_groupchat(stanza)
        if is_group and outgoing and str(stanza["from"].bare) == self.boundjid.bare:
            chat_jid = str(stanza["to"].bare)
        elif is_group:
            chat_jid = str(stanza["from"].bare)
        elif outgoing:
            chat_jid = str(stanza["to"].bare)
        else:
            chat_jid = str(stanza["from"].bare)
        if is_group:
            self._group_chat_jids.add(chat_jid)
        return self._retracted_message(
            chat_jid=chat_jid,
            message_id=target_id,
            sent_at=self._sent_at_from_stanza_delay(stanza) or datetime.now(),
            outgoing=outgoing,
            is_group=is_group,
        )

    def _message_retraction_from_xml(
        self,
        chat_jid: str,
        message: ET.Element,
        result: ET.Element,
    ) -> Message | None:
        target_id = self._retracted_message_id_from_xml(message)
        if not target_id:
            return None

        is_group = (
            message.attrib.get("type", "") == "groupchat"
            or self._xml_message_addresses_groupchat(message)
        )
        outgoing = self._message_xml_is_outgoing(message, is_group=is_group)
        return self._retracted_message(
            chat_jid=chat_jid,
            message_id=target_id,
            sent_at=self._forwarded_delay_from_xml(result) or datetime.now(),
            outgoing=outgoing,
            is_group=is_group or chat_jid in self._group_chat_jids,
        )

    @staticmethod
    def _retracted_message(
        chat_jid: str,
        message_id: str,
        sent_at: datetime,
        outgoing: bool,
        is_group: bool,
    ) -> Message:
        return Message(
            chat_jid=chat_jid,
            sender_jid="Yo" if outgoing else "",
            body="",
            sent_at=sent_at,
            outgoing=outgoing,
            message_id=message_id,
            chat_is_group=is_group,
            delivery_state="sent" if outgoing else "",
            retracted=True,
        )

    @staticmethod
    def _retracted_message_id_from_xml(xml: ET.Element) -> str:
        retract = xml.find(f".//{{{MESSAGE_RETRACT_NS}}}retract")
        if retract is None:
            return ""

        return retract.attrib.get("id", "").strip()

    def _message_xml_is_outgoing(self, message: ET.Element, is_group: bool = False) -> bool:
        from_jid = message.attrib.get("from", "")
        if is_group:
            if self._group_sender_matches_local(
                self._muc_user_item_jid(message),
                self._jid_resource(from_jid),
            ):
                return True

            return False

        return self._bare_jid(from_jid) == str(self.boundjid.bare)

    @classmethod
    def _sender_jid_from_message_xml(cls, message: ET.Element, is_group: bool = False) -> str:
        from_jid = message.attrib.get("from", "")
        if not is_group:
            return cls._bare_jid(from_jid)

        participant_jid = cls._muc_user_item_jid(message)
        if participant_jid:
            return participant_jid

        return from_jid or cls._bare_jid(from_jid)

    @classmethod
    def _sender_name_from_message_xml(cls, message: ET.Element, is_group: bool = False) -> str:
        from_jid = message.attrib.get("from", "")
        if not is_group:
            return ""

        return display_label_from_jid(cls._jid_resource(from_jid) or from_jid)

    @staticmethod
    def _muc_user_item_jid(xml: ET.Element) -> str:
        item = xml.find(f".//{{{MUC_USER_NS}}}item")
        if item is None:
            return ""

        return item.attrib.get("jid", "").strip()

    @staticmethod
    def _bare_jid(jid: str) -> str:
        return jid.split("/", 1)[0]

    @staticmethod
    def _jid_resource(jid: str) -> str:
        if "/" not in jid:
            return ""

        return jid.rsplit("/", 1)[-1]

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

        if self._stanza_is_groupchat(stanza):
            return str(from_jid.bare)

        return str(from_jid.bare)

    @classmethod
    def _stanza_is_groupchat(cls, stanza: object) -> bool:
        try:
            if stanza["type"] == "groupchat":
                return True
        except Exception:
            pass

        try:
            return cls._jid_is_hash_group_chat(
                str(stanza["from"].bare)
            ) or cls._jid_is_hash_group_chat(
                str(stanza["to"].bare)
            )
        except Exception:
            return False

    @classmethod
    def _xml_message_addresses_groupchat(cls, message: ET.Element) -> bool:
        return cls._jid_is_hash_group_chat(
            cls._bare_jid(message.attrib.get("from", ""))
        ) or cls._jid_is_hash_group_chat(cls._bare_jid(message.attrib.get("to", "")))

    @classmethod
    def _sender_jid_from_stanza(cls, stanza: object, is_group: bool = False) -> str:
        if not is_group:
            return str(stanza["from"].bare)

        try:
            participant_jid = cls._muc_user_item_jid(stanza.xml)
        except Exception:
            participant_jid = ""
        if participant_jid:
            return participant_jid

        full_jid = str(stanza["from"] or "")
        if "/" in full_jid:
            return full_jid

        nick = cls._group_sender_nick_from_stanza(stanza)
        if nick:
            return nick

        return str(stanza["from"].bare)

    @classmethod
    def _sender_name_from_stanza(cls, stanza: object, is_group: bool = False) -> str:
        if not is_group:
            return ""

        nick = cls._group_sender_nick_from_stanza(stanza)
        if nick:
            return display_label_from_jid(nick)

        full_jid = str(stanza["from"] or "")
        return display_label_from_jid(full_jid)

    @staticmethod
    def _group_sender_nick_from_stanza(stanza: object) -> str:
        try:
            muc_nick = str(stanza["mucnick"] or "")
        except Exception:
            muc_nick = ""
        if muc_nick:
            return muc_nick

        resource = str(stanza["from"].resource or "")
        if resource:
            return resource

        return ""

    def _message_is_outgoing(
        self,
        stanza: object,
        sender_jid: str,
        is_group: bool = False,
    ) -> bool:
        if is_group:
            return self._group_sender_matches_local(
                sender_jid,
                self._group_sender_nick_from_stanza(stanza),
            )

        return str(stanza["from"].bare) == self.boundjid.bare

    def _group_sender_matches_local(self, sender_jid: str, sender_nick: str) -> bool:
        local_bare = str(self.boundjid.bare)
        if sender_jid and self._bare_jid(sender_jid) == local_bare:
            return True

        return BridgeXmppClient._same_display_label(sender_nick, self._muc_nick())

    @staticmethod
    def _same_display_label(first: str, second: str) -> bool:
        def normalize(value: str) -> str:
            folded = display_label_from_jid(value).casefold()
            return "".join(
                character
                for character in unicodedata.normalize("NFD", folded)
                if unicodedata.category(character) != "Mn"
            )

        return bool(normalize(first)) and normalize(first) == normalize(second)

    @staticmethod
    def _sent_at_from_mam_result(result: object) -> datetime | None:
        try:
            stamp = result["mam_result"]["forwarded"]["delay"]["stamp"]
        except Exception:
            return None

        if isinstance(stamp, datetime):
            return stamp

        if not stamp:
            return None

        try:
            return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _sent_at_from_stanza_delay(stanza: object) -> datetime | None:
        for delay in stanza.xml.findall(f".//{{{DELAY_NS}}}delay"):
            stamp = delay.attrib.get("stamp", "")
            if not stamp:
                continue
            try:
                return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue

        return None

    @staticmethod
    def _message_body_from_mam_result(result: object) -> str:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        return str(stanza["body"] or "").strip()

    def _media_from_mam_result(self, result: object) -> tuple[str, str, str, str, int, float]:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        return self._media_from_stanza(stanza)

    def _mam_result_is_groupchat(self, result: object) -> bool:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        return self._stanza_is_groupchat(stanza)

    def _media_from_stanza(self, stanza: object) -> tuple[str, str, str, str, int, float]:
        body = str(stanza["body"] or "")
        media_url, media_kind, media_mime, media_filename, media_size, media_duration = (
            self._media_from_xml(stanza.xml)
        )
        if media_url:
            return media_url, media_kind, media_mime, media_filename, media_size, media_duration

        for url in self._urls_from_text(body):
            media_kind = self._media_kind_from_url(url)
            if media_kind:
                return url, media_kind, "", self._filename_from_url(url), 0, 0.0

        return "", "", "", "", 0, 0.0

    @classmethod
    def _message_body_for_display(
        cls,
        body: str,
        media_url: str,
        media_kind: str,
        media_filename: str = "",
        media_size: int = 0,
    ) -> str:
        body = body.strip()
        if not media_url:
            return body

        if body and media_url not in cls._urls_from_text(body):
            return body

        match media_kind:
            case "audio":
                return "Mensaje de voz"
            case "image":
                label = "Foto"
            case "video":
                label = "Video"
            case _:
                label = "Archivo"

        metadata = []
        if media_filename:
            metadata.append(media_filename)
        if media_size > 0:
            metadata.append(cls._format_size(media_size))
        if metadata:
            return f"{label}: {', '.join(metadata)}"
        return label

    @classmethod
    def _message_display_parts(
        cls,
        body: str,
        media_url: str,
        media_kind: str,
        media_filename: str,
        media_size: int,
        xml: ET.Element,
    ) -> tuple[str, str]:
        reply_quote = ""
        display_body = body
        fallback_bounds = cls._reply_fallback_bounds_from_xml(xml)
        if fallback_bounds is not None:
            start, end = fallback_bounds
            start = max(0, min(start, len(body)))
            end = max(start, min(end, len(body)))
            reply_quote = cls._reply_quote_from_fallback(body[start:end])
            display_body = f"{body[:start]}{body[end:]}".strip()
        else:
            quoted_body = cls._reply_parts_from_quoted_body(body)
            if quoted_body is not None:
                display_body, reply_quote = quoted_body

        return cls._message_body_for_display(
            display_body,
            media_url,
            media_kind,
            media_filename,
            media_size,
        ), reply_quote

    @staticmethod
    def _reply_fallback_bounds_from_xml(xml: ET.Element) -> tuple[int, int] | None:
        for fallback in xml.findall(f".//{{{FALLBACK_NS}}}fallback"):
            if fallback.attrib.get("for") != REPLY_NS:
                continue

            body = fallback.find(f"{{{FALLBACK_NS}}}body")
            if body is None:
                continue

            try:
                start = int(body.attrib.get("start", "0"))
                end = int(body.attrib["end"])
            except (KeyError, ValueError):
                continue

            return start, end

        return None

    @staticmethod
    def _reply_quote_from_fallback(fallback_text: str) -> str:
        quote_lines: list[str] = []
        for line in fallback_text.splitlines():
            line = line.strip()
            if line.startswith(">"):
                line = line[1:].lstrip()
            if line:
                quote_lines.append(line)

        if len(quote_lines) > 1 and quote_lines[0].endswith(":"):
            quote_lines = quote_lines[1:]

        return " ".join(quote_lines).strip()

    @classmethod
    def _reply_parts_from_quoted_body(cls, body: str) -> tuple[str, str] | None:
        lines = body.splitlines()
        quote_lines: list[str] = []
        body_start = 0
        saw_quote = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                if saw_quote:
                    body_start = index + 1
                    break
                continue
            if stripped.startswith(">"):
                saw_quote = True
                quote_lines.append(stripped[1:].lstrip())
                body_start = index + 1
                continue
            if saw_quote:
                body_start = index
            break

        quote = cls._reply_quote_from_fallback("\n".join(quote_lines))
        if not quote:
            return None

        display_body = "\n".join(lines[body_start:]).strip()
        return display_body or body, quote

    @classmethod
    def _media_from_xml(cls, xml: ET.Element) -> tuple[str, str, str, str, int, float]:
        media_mime = cls._media_mime_from_xml(xml)
        media_filename = cls._media_filename_from_xml(xml)
        media_size = cls._media_size_from_xml(xml)
        media_duration = cls._media_duration_from_xml(xml)

        for url_node in xml.findall(f".//{{{OOB_NS}}}url"):
            url = (url_node.text or "").strip()
            media_kind = cls._media_kind_from_mime_or_url(media_mime, url)
            if media_kind:
                return (
                    url,
                    media_kind,
                    media_mime,
                    media_filename or cls._filename_from_url(url),
                    media_size,
                    media_duration,
                )

        for node in xml.iter():
            for attribute in ("uri", "url", "target", "src", "href"):
                url = node.attrib.get(attribute, "").strip()
                if not url.startswith(("http://", "https://")):
                    continue

                media_kind = cls._media_kind_from_mime_or_url(media_mime, url)
                if media_kind:
                    filename = media_filename or cls._filename_from_url(url)
                    return url, media_kind, media_mime, filename, media_size, media_duration

        return "", "", media_mime, media_filename, media_size, media_duration

    @staticmethod
    def _media_mime_from_xml(xml: ET.Element) -> str:
        for node in xml.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            if local_name in {"media-type", "mime-type"} and node.text:
                return node.text.strip()
            for attribute in ("media-type", "mime-type", "content-type"):
                value = node.attrib.get(attribute, "").strip()
                if value:
                    return value

        return ""

    @staticmethod
    def _media_filename_from_xml(xml: ET.Element) -> str:
        for node in xml.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            if local_name == "name" and node.text:
                return node.text.strip()
            for attribute in ("name", "filename"):
                value = node.attrib.get(attribute, "").strip()
                if value:
                    return value

        return ""

    @classmethod
    def _media_size_from_xml(cls, xml: ET.Element) -> int:
        for node in xml.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            if local_name in {"size", "file-size", "length"} and node.text:
                size = cls._int_or_zero(node.text.strip())
                if size > 0:
                    return size
            for attribute in ("size", "file-size", "length", "content-length"):
                size = cls._int_or_zero(node.attrib.get(attribute, "").strip())
                if size > 0:
                    return size

        return 0

    @classmethod
    def _media_duration_from_xml(cls, xml: ET.Element) -> float:
        for node in xml.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            if local_name in {"duration", "playtime"} and node.text:
                duration = cls._float_or_zero(node.text.strip())
                if duration > 0:
                    return duration
            for attribute in ("duration", "playtime"):
                duration = cls._float_or_zero(node.attrib.get(attribute, "").strip())
                if duration > 0:
                    return duration

        return 0.0

    @staticmethod
    def _float_or_zero(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return 0.0

    @staticmethod
    def _urls_from_text(text: str) -> list[str]:
        return [match.group(0).rstrip(").,;]") for match in URL_PATTERN.finditer(text)]

    @classmethod
    def _media_kind_from_mime_or_url(cls, mime: str, url: str) -> str:
        normalized_mime = mime.split(";", 1)[0].strip().lower()
        if normalized_mime.startswith("audio/"):
            return "audio"
        if normalized_mime.startswith("image/"):
            return "image"
        if normalized_mime.startswith("video/"):
            return "video"

        return cls._media_kind_from_url(url)

    @staticmethod
    def _media_kind_from_url(url: str) -> str:
        path = urlparse(url).path.lower()
        if path.endswith(AUDIO_EXTENSIONS):
            return "audio"
        if path.endswith(IMAGE_EXTENSIONS):
            return "image"
        if path.endswith(VIDEO_EXTENSIONS):
            return "video"

        return "file" if path else ""

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        if not path:
            return ""

        return path.rsplit("/", 1)[-1]

    @staticmethod
    def _format_size(size: int) -> str:
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} B"
                return f"{value:.1f} {unit}"
            value /= 1024

        return f"{size} B"

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

    @staticmethod
    def _mime_type_for_file(file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in EXPLICIT_MIME_TYPES:
            return EXPLICIT_MIME_TYPES[suffix]

        return mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    async def send_file(self, to_jid: str, path: str, is_group: bool = False) -> Message:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(path)

        if is_group:
            self._join_group_chat(to_jid)
        mime = self._mime_type_for_file(file_path)
        media_kind = self._media_kind_from_mime_or_url(mime, file_path.name) or "file"
        upload_mime = mime
        if media_kind == "audio":
            file_path = convert_to_voice_note(file_path)
            mime = VOICE_NOTE_MIME
            upload_mime = VOICE_NOTE_UPLOAD_MIME

        size = file_path.stat().st_size
        duration = media_duration_seconds(file_path) if media_kind == "audio" else 0.0
        get_url = await self._upload_file(
            file_path,
            size=size,
            content_type=upload_mime,
            timeout=60,
        )

        message_type = "groupchat" if is_group else "chat"
        message = self.make_message(mto=to_jid, mbody=get_url, mtype=message_type)
        message_id = str(message["id"] or "")
        self._append_file_metadata(
            message,
            url=get_url,
            filename=file_path.name,
            size=size,
            mime=mime,
            media_kind=media_kind,
            duration=duration,
        )
        message.send()
        body = self._message_body_for_display("", get_url, media_kind, file_path.name, size)
        return Message(
            chat_jid=to_jid,
            sender_jid="Yo",
            body=body,
            sent_at=datetime.now(),
            outgoing=True,
            audio_url=get_url if media_kind == "audio" else "",
            media_url=get_url,
            media_kind=media_kind,
            media_mime=mime,
            media_filename=file_path.name,
            media_size=size,
            media_duration_seconds=duration,
            media_local_path=str(file_path),
            message_id=message_id,
            chat_is_group=is_group,
            delivery_state="sent",
        )

    async def send_audio_file(self, to_jid: str, path: str, is_group: bool = False) -> Message:
        return await self.send_file(to_jid, path, is_group=is_group)

    async def _upload_file(
        self,
        file_path: Path,
        size: int,
        content_type: str,
        timeout: int,
    ) -> str:
        upload = self["xep_0363"]
        if upload.upload_service is None:
            upload.upload_service = self._preferred_upload_service()

        try:
            return await upload.upload_file(
                file_path,
                size=size,
                content_type=content_type,
                timeout=timeout,
            )
        except (IqError, IqTimeout):
            return await upload.upload_file(
                file_path,
                size=size,
                content_type=content_type,
                timeout=timeout,
            )

    def _preferred_upload_service(self) -> str:
        domain = str(self.boundjid.bare).split("@", 1)[-1] or self.settings.jid.split("@", 1)[-1]
        return f"upload.{domain}"

    @staticmethod
    def _append_file_metadata(
        message: object,
        url: str,
        filename: str,
        size: int,
        mime: str,
        media_kind: str,
        duration: float = 0.0,
    ) -> None:
        oob = ET.Element(f"{{{OOB_NS}}}x")
        url_node = ET.SubElement(oob, f"{{{OOB_NS}}}url")
        url_node.text = url
        message.append(oob)

        disposition = "inline" if media_kind in {"audio", "image", "video"} else "attachment"
        file_sharing = ET.Element(f"{{{SFS_NS}}}file-sharing", {"disposition": disposition})
        file_node = ET.SubElement(file_sharing, f"{{{FILE_METADATA_NS}}}file")
        media_type = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}media-type")
        media_type.text = mime
        name = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}name")
        name.text = filename
        if media_kind == "audio":
            desc = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}desc")
            desc.text = "Voice message"
            if duration > 0:
                duration_node = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}duration")
                duration_node.text = str(round(duration, 3))
        size_node = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}size")
        size_node.text = str(size)
        sources = ET.SubElement(file_sharing, f"{{{SFS_NS}}}sources")
        ET.SubElement(sources, f"{{{URL_DATA_NS}}}url-data", {"target": url})
        message.append(file_sharing)

        fallback = ET.Element(f"{{{FALLBACK_NS}}}fallback", {"for": SFS_NS})
        ET.SubElement(fallback, f"{{{FALLBACK_NS}}}body")
        message.append(fallback)

        sims_reference = ET.Element(
            f"{{{REFERENCE_NS}}}reference",
            {"type": "data", "uri": url},
        )
        media_sharing = ET.SubElement(sims_reference, f"{{{SIMS_NS}}}media-sharing")
        sims_file = ET.SubElement(media_sharing, f"{{{JINGLE_FILE_TRANSFER_NS}}}file")
        sims_media_type = ET.SubElement(sims_file, f"{{{JINGLE_FILE_TRANSFER_NS}}}media-type")
        sims_media_type.text = mime
        sims_name = ET.SubElement(sims_file, f"{{{JINGLE_FILE_TRANSFER_NS}}}name")
        sims_name.text = filename
        if media_kind == "audio":
            sims_desc = ET.SubElement(sims_file, f"{{{JINGLE_FILE_TRANSFER_NS}}}desc")
            sims_desc.text = "Voice message"
            if duration > 0:
                sims_duration = ET.SubElement(
                    sims_file,
                    f"{{{JINGLE_FILE_TRANSFER_NS}}}duration",
                )
                sims_duration.text = str(round(duration, 3))
        sims_size = ET.SubElement(sims_file, f"{{{JINGLE_FILE_TRANSFER_NS}}}size")
        sims_size.text = str(size)
        sims_sources = ET.SubElement(media_sharing, f"{{{SIMS_NS}}}sources")
        ET.SubElement(
            sims_sources,
            f"{{{REFERENCE_NS}}}reference",
            {"type": "data", "uri": url},
        )
        message.append(sims_reference)


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
            self._loop.call_soon_threadsafe(self._client.request_disconnect)

    def send_message(
        self,
        to_jid: str,
        body: str,
        is_group: bool = False,
        message_id: str = "",
    ) -> None:
        if not self._client or not self._loop:
            if message_id:
                self._emit(
                    MessageDeliveryUpdated(
                        chat_jid=to_jid,
                        message_id=message_id,
                        delivery_state="failed",
                        detail="No hay una conexión XMPP activa.",
                    )
                )
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def send() -> None:
            if self._client:
                try:
                    if is_group:
                        self._client._join_group_chat(to_jid)
                    message_type = "groupchat" if is_group else "chat"
                    msg = self._client.make_message(mto=to_jid, mbody=body, mtype=message_type)
                    if message_id:
                        msg["id"] = message_id
                    self._request_delivery_updates(msg, message_type)
                    msg.send()
                    if message_id:
                        self._emit(
                            MessageDeliveryUpdated(
                                chat_jid=to_jid,
                                message_id=message_id,
                                delivery_state="sent",
                            )
                        )
                except Exception as exc:
                    if message_id:
                        self._emit(
                            MessageDeliveryUpdated(
                                chat_jid=to_jid,
                                message_id=message_id,
                                delivery_state="failed",
                                detail=f"No se pudo enviar el mensaje: {exc}",
                            )
                        )
                    self._emit(XmppError(f"No se pudo enviar el mensaje: {exc}"))

        self._loop.call_soon_threadsafe(send)

    def send_reply(
        self,
        to_jid: str,
        body: str,
        reply_to_jid: str,
        reply_to_id: str,
        fallback_end: int = 0,
        is_group: bool = False,
        message_id: str = "",
    ) -> None:
        if not self._client or not self._loop:
            if message_id:
                self._emit(
                    MessageDeliveryUpdated(
                        chat_jid=to_jid,
                        message_id=message_id,
                        delivery_state="failed",
                        detail="No hay una conexión XMPP activa.",
                    )
                )
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def send() -> None:
            if not self._client:
                return

            try:
                if is_group:
                    self._client._join_group_chat(to_jid)
                message_type = "groupchat" if is_group else "chat"
                msg = self._client.make_message(mto=to_jid, mbody=body, mtype=message_type)
                if message_id:
                    msg["id"] = message_id
                self._request_delivery_updates(msg, message_type)
                if reply_to_id:
                    msg.append(
                        ET.Element(
                            f"{{{REPLY_NS}}}reply",
                            {
                                "to": reply_to_jid,
                                "id": reply_to_id,
                            },
                        )
                    )
                if fallback_end > 0:
                    fallback = ET.Element(
                        f"{{{FALLBACK_NS}}}fallback",
                        {"for": REPLY_NS},
                    )
                    ET.SubElement(
                        fallback,
                        f"{{{FALLBACK_NS}}}body",
                        {"start": "0", "end": str(fallback_end)},
                    )
                    msg.append(fallback)
                msg.send()
                if message_id:
                    self._emit(
                        MessageDeliveryUpdated(
                            chat_jid=to_jid,
                            message_id=message_id,
                            delivery_state="sent",
                        )
                    )
            except Exception as exc:
                if message_id:
                    self._emit(
                        MessageDeliveryUpdated(
                            chat_jid=to_jid,
                            message_id=message_id,
                            delivery_state="failed",
                            detail=f"No se pudo enviar la respuesta: {exc}",
                        )
                    )
                self._emit(XmppError(f"No se pudo enviar la respuesta: {exc}"))

        self._loop.call_soon_threadsafe(send)

    @staticmethod
    def _request_delivery_updates(msg: object, message_type: str) -> None:
        if message_type != "groupchat":
            msg["request_receipt"] = True
        try:
            msg.enable("markable")
        except Exception:
            marker = ET.Element(f"{{{CHAT_MARKERS_NS}}}markable")
            msg.append(marker)

    def send_reaction(
        self,
        to_jid: str,
        message_id: str,
        reaction: str,
        is_group: bool = False,
    ) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        if not message_id:
            self._emit(XmppError("No se puede reaccionar: el mensaje no tiene ID XMPP."))
            return

        def send() -> None:
            if not self._client:
                return

            if is_group:
                self._client._join_group_chat(to_jid)
            message_type = "groupchat" if is_group else "chat"
            msg = self._client.make_message(mto=to_jid, mtype=message_type)
            reactions = ET.Element(f"{{{REACTIONS_NS}}}reactions", {"id": message_id})
            reaction_node = ET.SubElement(reactions, f"{{{REACTIONS_NS}}}reaction")
            reaction_node.text = reaction
            msg.append(reactions)
            msg.send()

        self._loop.call_soon_threadsafe(send)

    def retract_message(
        self,
        to_jid: str,
        message_id: str,
        is_group: bool = False,
    ) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        if not message_id:
            self._emit(XmppError("No se puede eliminar: el mensaje no tiene ID XMPP."))
            return

        def send() -> None:
            if not self._client:
                return

            try:
                if is_group:
                    self._client._join_group_chat(to_jid)
                message_type = "groupchat" if is_group else "chat"
                msg = self._client.make_message(mto=to_jid, mtype=message_type)
                msg.append(ET.Element(f"{{{MESSAGE_RETRACT_NS}}}retract", {"id": message_id}))
                msg.send()
            except Exception as exc:
                self._emit(XmppError(f"No se pudo eliminar el mensaje: {exc}"))

        self._loop.call_soon_threadsafe(send)

    def send_file(self, to_jid: str, path: str, is_group: bool = False) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        async def send() -> None:
            if not self._client:
                return

            try:
                message = await self._client.send_file(to_jid, path, is_group=is_group)
            except Exception as exc:
                self._emit(XmppError(f"No se pudo enviar el archivo: {_format_xmpp_error(exc)}"))
                return

            self._emit(MessageReceived(message))

        def schedule() -> None:
            if self._loop:
                self._loop.create_task(send())

        self._loop.call_soon_threadsafe(schedule)

    def send_audio_file(self, to_jid: str, path: str, is_group: bool = False) -> None:
        self.send_file(to_jid, path, is_group=is_group)

    def request_whatsapp_relogin(self, component_jid: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def request() -> None:
            if self._client:
                self._loop.create_task(self._client.request_whatsapp_relogin(component_jid))

        self._loop.call_soon_threadsafe(request)

    def request_whatsapp_pair_code(self, component_jid: str, phone: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def request() -> None:
            if self._client:
                self._loop.create_task(
                    self._client.request_whatsapp_pair_code(component_jid, phone)
                )

        self._loop.call_soon_threadsafe(request)

    def cancel_whatsapp_linking(self, component_jid: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def request() -> None:
            if self._client:
                self._loop.create_task(self._client.cancel_whatsapp_linking(component_jid))

        self._loop.call_soon_threadsafe(request)

    def monitor_group_chats(self, chat_jids: Iterable[str]) -> None:
        if not self._client or not self._loop:
            return

        group_jids = tuple(dict.fromkeys(chat_jid for chat_jid in chat_jids if chat_jid))
        if not group_jids:
            return

        def monitor() -> None:
            if self._client:
                self._client.monitor_group_chats(group_jids)

        self._loop.call_soon_threadsafe(monitor)

    def join_group_chat(self, chat_jid: str) -> None:
        if not self._client or not self._loop or not chat_jid:
            return

        def join() -> None:
            if self._client:
                self._client.join_group_chat(chat_jid)

        self._loop.call_soon_threadsafe(join)

    def request_contact_presence_subscription(self, chat_jid: str) -> None:
        if not self._client or not self._loop or not chat_jid:
            return

        def request() -> None:
            if self._client:
                self._client.request_contact_presence_subscription(chat_jid)

        self._loop.call_soon_threadsafe(request)

    def load_history(
        self,
        chat_jid: str,
        limit: int | None = None,
        before: datetime | None = None,
        older: bool = False,
        allow_unfiltered_fallback: bool = True,
        background: bool = False,
    ) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(
                    self._client.load_history(
                        chat_jid,
                        limit,
                        before,
                        older,
                        allow_unfiltered_fallback,
                        background,
                    )
                )

        self._loop.call_soon_threadsafe(load)

    def preload_histories(
        self,
        chat_jids: list[str],
        limit: int = 20,
        concurrency: int = 4,
    ) -> None:
        if not self._client or not self._loop:
            return

        def preload() -> None:
            if self._client:
                self._loop.create_task(
                    self._client.preload_histories(chat_jids, limit, concurrency)
                )

        self._loop.call_soon_threadsafe(preload)

    def load_recent_activity(self, roster_jids: set[str] | None = None, limit: int = 1000) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(
                    self._client.load_recent_activity(roster_jids or set(), limit)
                )

        self._loop.call_soon_threadsafe(load)

    def load_inbox(self) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexiÃ³n XMPP activa."))
            return

        def load() -> None:
            if self._client:
                self._loop.create_task(self._client.load_inbox())

        self._loop.call_soon_threadsafe(load)

    def fetch_contact_avatar(self, chat_jid: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def fetch() -> None:
            if self._client:
                self._loop.create_task(self._fetch_contact_avatar(chat_jid))

        self._loop.call_soon_threadsafe(fetch)

    async def _fetch_contact_avatar(self, chat_jid: str) -> None:
        if not self._client:
            return

        try:
            await self._client.fetch_contact_avatar(chat_jid)
        except Exception as exc:
            self._emit(
                ContactAvatarUnavailable(
                    chat_jid=chat_jid,
                    detail=f"No se pudo obtener la foto de perfil: {_format_xmpp_error(exc)}",
                )
            )

    def _run_client(self, settings: ConnectionSettings, password: str) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._client = BridgeXmppClient(settings, password, self._emit)
            plugins = (
                "xep_0030",
                "xep_0049",
                "xep_0050",
                "xep_0060",
                "xep_0163",
                "xep_0084",
                "xep_0085",
                "xep_0128",
                "xep_0184",
                "xep_0199",
                "xep_0223",
                "xep_0048",
                "xep_0249",
                "xep_0297",
                "xep_0280",
                "xep_0313",
                "xep_0333",
                "xep_0363",
                "xep_0402",
                "xep_0045",
                "xep_0492",
            )
            for plugin in plugins:
                if plugin == "xep_0199":
                    self._client.register_plugin(
                        plugin,
                        {"keepalive": True, "interval": 60, "timeout": 20},
                    )
                else:
                    self._client.register_plugin(plugin)

            if settings.host:
                self._client.connect(settings.host, settings.port)
            else:
                self._client.connect()

            self._loop.run_forever()
        except Exception as exc:
            self._emit(XmppError(f"Error en la conexión XMPP: {exc}"))
        finally:
            self._client = None
            self._loop = None


def _format_xmpp_error(exc: Exception) -> str:
    iq = getattr(exc, "iq", None)
    if iq is None:
        return str(exc)

    to_jid = str(iq["to"] or "")
    error = iq["error"]
    condition = str(error["condition"] or "")
    text = str(error["text"] or "")
    if isinstance(exc, IqTimeout):
        message = "tiempo de espera agotado"
    else:
        message = condition or type(exc).__name__

    details = [message]
    if to_jid:
        details.append(f"servicio: {to_jid}")
    if text:
        details.append(text)
    return "; ".join(details)
