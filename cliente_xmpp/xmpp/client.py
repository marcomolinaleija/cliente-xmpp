from __future__ import annotations

import asyncio
import mimetypes
import re
import threading
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
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
    ChatsDiscovered,
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
DISCO_INFO_NS = "http://jabber.org/protocol/disco#info"
DISCO_ITEMS_NS = "http://jabber.org/protocol/disco#items"
MUC_NS = "http://jabber.org/protocol/muc"
BOOKMARKS_NS = "urn:xmpp:bookmarks:1"
NOTIFICATION_SETTINGS_NS = "urn:xmpp:notification-settings:0"
DATA_FORMS_NS = "jabber:x:data"
DIRECT_INVITE_NS = "jabber:x:conference"
OOB_NS = "jabber:x:oob"
REACTIONS_NS = "urn:xmpp:reactions:0"
REPLY_NS = "urn:xmpp:reply:0"
FALLBACK_NS = "urn:xmpp:fallback:0"
DELAY_NS = "urn:xmpp:delay"
FILE_METADATA_NS = "urn:xmpp:file:metadata:0"
SFS_NS = "urn:xmpp:sfs:0"
SIMS_NS = "urn:xmpp:sims:1"
REFERENCE_NS = "urn:xmpp:reference:0"
JINGLE_FILE_TRANSFER_NS = "urn:xmpp:jingle:apps:file-transfer:5"
URL_DATA_NS = "http://jabber.org/protocol/url-data"
SLIDGE_GROUPS_COMMAND = "https://slidge.im/command/core/groups/groups"
SLIDGE_REINVITE_GROUPS_COMMAND = "https://slidge.im/command/core/groups/re-invite"
JID_PATTERN = re.compile(r"(?<![\w.+-])(?:xmpp:)?([^\s<>\"']+@[^\s<>\"']+)")
AUDIO_EXTENSIONS = (".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".weba")
IMAGE_EXTENSIONS = (".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp")
VIDEO_EXTENSIONS = (".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm")
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
        self._disconnect_requested = False
        self._reconnect_scheduled = False
        self.force_starttls = settings.use_tls

        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("disconnected", self._on_disconnected)
        self.add_event_handler("failed_auth", self._on_failed_auth)
        self.add_event_handler("message", self._on_message)
        self.add_event_handler("groupchat_message", self._on_groupchat_message)
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
        asyncio.create_task(self._discover_group_chats(chats))
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
        self._emit_inbox_entry(msg)
        group_chat = self._group_chat_from_invite_stanza(msg)
        if group_chat is not None:
            self._monitor_discovered_group_chats([group_chat])
            return

        if msg["type"] not in ("chat", "normal"):
            return

        body = str(msg["body"] or "").strip()
        (
            media_url,
            media_kind,
            media_mime,
            media_filename,
            media_size,
            media_duration,
        ) = self._media_from_stanza(msg)
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return

        bare_jid = str(msg["from"].bare)
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
            self._emit(MessageReceived(message))

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

    async def _discover_group_chats(self, roster_chats: list[Chat]) -> None:
        group_chats: dict[str, Chat] = {}

        for group_chat in await self._bookmarked_group_chats():
            group_chats[group_chat.jid] = group_chat
        for chat in roster_chats:
            if not self._jid_may_be_group_chat(chat.jid):
                continue

            group_chats[chat.jid] = Chat(
                jid=chat.jid,
                name=chat.name,
                is_group=True,
                notifications_muted=chat.notifications_muted,
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

    def _monitor_discovered_group_chats(self, chats: list[Chat]) -> None:
        chats = [chat for chat in chats if self._jid_may_be_group_chat(chat.jid)]
        if not chats:
            return

        self._group_chat_jids.update(chat.jid for chat in chats)
        self._emit(ChatsDiscovered(chats))
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
                candidates.add(jid)

        for jid in list(candidates):
            for child_jid, _name in await self._disco_items(jid):
                candidates.add(child_jid)

        return candidates

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

        return Chat(jid=jid, name=name, is_group=True)

    async def _disco_items(self, jid: str) -> list[tuple[str, str]]:
        try:
            items = await self["xep_0030"].get_items(jid=jid, timeout=10)
        except Exception:
            return []

        discovered: list[tuple[str, str]] = []
        for item in items.xml.findall(f".//{{{DISCO_ITEMS_NS}}}item"):
            item_jid = item.attrib.get("jid", "").strip()
            if item_jid:
                discovered.append((item_jid, item.attrib.get("name", "").strip()))
        return discovered

    async def _bookmarked_group_chats(self) -> list[Chat]:
        try:
            items = await self["xep_0060"].get_items(
                self.boundjid.bare,
                BOOKMARKS_NS,
                timeout=10,
            )
        except Exception:
            return []

        return self._group_chats_from_bookmark_xml(items.xml)

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
            return []

        discovered: list[tuple[str, str]] = []
        for item in commands.xml.findall(f".//{{{DISCO_ITEMS_NS}}}item"):
            node = item.attrib.get("node", "").strip()
            name = item.attrib.get("name", "").strip()
            if node:
                discovered.append((node, name))
        return discovered

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
        return Chat(jid=jid, name=name, is_group=True)

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
            notifications_muted = self._bookmark_notifications_muted(conference)
            chats.append(
                Chat(
                    jid=jid,
                    name=name,
                    is_group=True,
                    notifications_muted=notifications_muted,
                )
            )
        return chats

    @staticmethod
    def _bookmark_notifications_muted(conference: ET.Element) -> bool:
        notify = conference.find(f".//{{{NOTIFICATION_SETTINGS_NS}}}notify")
        if notify is None:
            return False

        return notify.find(f"{{{NOTIFICATION_SETTINGS_NS}}}never") is not None

    def _group_chats_from_command_xml(self, xml: ET.Element) -> list[Chat]:
        group_chats: dict[str, Chat] = {}
        for item in xml.findall(f".//{{{DATA_FORMS_NS}}}item"):
            values = self._data_form_item_values(item)
            jid = values.get("jid", "").strip()
            if not jid:
                continue

            name = values.get("name", "").strip() or jid
            group_chats[jid] = Chat(jid=jid, name=name, is_group=True)

        for jid in self._jids_from_xml_text(xml):
            group_chats.setdefault(jid, Chat(jid=jid, name=jid, is_group=True))
        return list(group_chats.values())

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
            self["xep_0045"].join_muc(jid, self._muc_nick(), maxhistory="0")
        except Exception:
            self._joined_group_chat_jids.discard(jid)

    def _muc_nick(self) -> str:
        return str(self.boundjid.user or self.boundjid.bare or self.settings.jid)

    def join_group_chat(self, chat_jid: str) -> None:
        if not self._jid_may_be_group_chat(chat_jid):
            return

        self._group_chat_jids.add(chat_jid)
        self._join_group_chat(chat_jid)

    def monitor_group_chats(self, chat_jids: Iterable[str]) -> None:
        group_jids = {chat_jid for chat_jid in chat_jids if self._jid_may_be_group_chat(chat_jid)}
        if not group_jids:
            return

        self._group_chat_jids.update(group_jids)
        asyncio.create_task(self.load_recent_activity(group_jids))

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
        total = None if limit is None else max(limit * 10, 100)

        async for result in mam.iterate(
            with_jid=chat_jid if with_jid_filter else None,
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
            if message:
                messages.append(message)

            if limit is not None and len(messages) >= limit:
                break

        return messages

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
        try:
            mam = self["xep_0313"]
            async for result in mam.iterate(reverse=True, rsm={"max": 50}, total=limit):
                preview = self._message_body_from_mam_result(result)
                media_url, media_kind, _, _, media_size, _ = self._media_from_mam_result(result)
                if not preview and not media_url:
                    continue

                chat_jid = self._chat_jid_from_mam_result(result)
                sent_at = self._sent_at_from_mam_result(result)
                if not chat_jid or not sent_at or chat_jid in loaded_chat_jids:
                    continue

                if roster_jids and chat_jid not in roster_jids:
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
        sender_jid = self._sender_jid_from_stanza(stanza, is_group=is_group)
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
            chat_jid=chat_jid,
            sender_jid="Yo" if outgoing else sender_jid,
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
            chat_is_group=is_group or chat_jid in self._group_chat_jids,
            reply_quote=reply_quote,
        )

    def _emit_message_from_stanza(self, stanza: object, outgoing: bool) -> None:
        if stanza["type"] not in ("chat", "normal", "groupchat"):
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
                )
            )
        )

    def _message_from_groupchat_stanza(self, stanza: object) -> Message | None:
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
                media_url, media_kind, _, _, media_size, _ = self._media_from_xml(message)
                if media_url:
                    preview = self._message_body_for_display(
                        preview,
                        media_url,
                        media_kind,
                        "",
                        media_size,
                    )
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

        if self._stanza_is_groupchat(stanza):
            return str(from_jid.bare)

        return str(from_jid.bare)

    @staticmethod
    def _stanza_is_groupchat(stanza: object) -> bool:
        try:
            return stanza["type"] == "groupchat"
        except Exception:
            return False

    @classmethod
    def _sender_jid_from_stanza(cls, stanza: object, is_group: bool = False) -> str:
        if not is_group:
            return str(stanza["from"].bare)

        full_jid = str(stanza["from"] or "")
        if "/" in full_jid:
            return full_jid

        nick = cls._group_sender_nick_from_stanza(stanza)
        if nick:
            return nick

        return str(stanza["from"].bare)

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
            return self._group_sender_nick_from_stanza(stanza) == self._muc_nick()

        return str(stanza["from"].bare) == self.boundjid.bare

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

    def send_message(self, to_jid: str, body: str, is_group: bool = False) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def send() -> None:
            if self._client:
                if is_group:
                    self._client._join_group_chat(to_jid)
                message_type = "groupchat" if is_group else "chat"
                self._client.send_message(mto=to_jid, mbody=body, mtype=message_type)

        self._loop.call_soon_threadsafe(send)

    def send_reply(
        self,
        to_jid: str,
        body: str,
        reply_to_jid: str,
        reply_to_id: str,
        fallback_end: int = 0,
        is_group: bool = False,
    ) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexión XMPP activa."))
            return

        def send() -> None:
            if not self._client:
                return

            if is_group:
                self._client._join_group_chat(to_jid)
            message_type = "groupchat" if is_group else "chat"
            msg = self._client.make_message(mto=to_jid, mbody=body, mtype=message_type)
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

        self._loop.call_soon_threadsafe(send)

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

    def _run_client(self, settings: ConnectionSettings, password: str) -> None:
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._client = BridgeXmppClient(settings, password, self._emit)
            plugins = (
                "xep_0030",
                "xep_0050",
                "xep_0060",
                "xep_0128",
                "xep_0199",
                "xep_0249",
                "xep_0297",
                "xep_0280",
                "xep_0313",
                "xep_0363",
                "xep_0402",
                "xep_0045",
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
