from __future__ import annotations

import asyncio
import mimetypes
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from slixmpp import ClientXMPP

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
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
OOB_NS = "jabber:x:oob"
REACTIONS_NS = "urn:xmpp:reactions:0"
REPLY_NS = "urn:xmpp:reply:0"
FALLBACK_NS = "urn:xmpp:fallback:0"
FILE_METADATA_NS = "urn:xmpp:file:metadata:0"
SFS_NS = "urn:xmpp:sfs:0"
URL_DATA_NS = "http://jabber.org/protocol/url-data"
AUDIO_EXTENSIONS = (".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".weba")
IMAGE_EXTENSIONS = (".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp")
VIDEO_EXTENSIONS = (".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm")
URL_PATTERN = re.compile(r"https?://\S+")


class BridgeXmppClient(ClientXMPP):
    def __init__(self, settings: ConnectionSettings, password: str, emit: EventHandler) -> None:
        super().__init__(settings.jid, password)
        self.settings = settings
        self._emit = emit
        self._history_preload_semaphore = asyncio.Semaphore(4)
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
        media_url, media_kind, media_mime, media_filename, media_size = self._media_from_stanza(msg)
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
                    outgoing=False,
                    audio_url=audio_url,
                    media_url=media_url,
                    media_kind=media_kind,
                    media_mime=media_mime,
                    media_filename=media_filename,
                    media_size=media_size,
                    message_id=str(msg["id"] or ""),
                    reply_quote=reply_quote,
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
            filtered_count = len(archived_messages)
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
            self._emit(
                MessageHistoryLoaded(
                    chat_jid=chat_jid,
                    messages=archived_messages,
                    older=older,
                    complete=limit is not None and filtered_count < limit,
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
        page_size = limit if with_jid_filter and limit is not None else 100
        total = limit if with_jid_filter else max((limit or 20) * 8, 100)

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
                    allow_unfiltered_fallback=False,
                    background=True,
                )

        await asyncio.gather(*(preload(chat_jid) for chat_jid in chat_jids))

    async def load_recent_activity(self, roster_jids: set[str], limit: int = 1000) -> None:
        loaded_chat_jids: set[str] = set()
        try:
            mam = self["xep_0313"]
            async for result in mam.iterate(reverse=True, rsm={"max": 50}, total=limit):
                preview = self._message_body_from_mam_result(result)
                media_url, media_kind, _, _, media_size = self._media_from_mam_result(result)
                if not preview and not media_url:
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
                        preview=self._message_body_for_display(
                            preview,
                            media_url,
                            media_kind,
                            "",
                            media_size,
                        ),
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
        media_url, media_kind, media_mime, media_filename, media_size = self._media_from_mam_result(
            result
        )
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return None

        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        sender_jid = str(stanza["from"].bare)
        outgoing = sender_jid == self.boundjid.bare
        display_body, reply_quote = self._message_display_parts(
            body,
            media_url,
            media_kind,
            media_filename,
            media_size,
            stanza.xml,
        )
        return Message(
            chat_jid=chat_jid,
            sender_jid="Yo" if outgoing else sender_jid,
            body=display_body,
            sent_at=self._sent_at_from_mam_result(result) or datetime.now(),
            outgoing=outgoing,
            audio_url=audio_url,
            media_url=media_url,
            media_kind=media_kind,
            media_mime=media_mime,
            media_filename=media_filename,
            media_size=media_size,
            message_id=str(stanza["id"] or result["mam_result"]["id"] or ""),
            reply_quote=reply_quote,
        )

    def _emit_message_from_stanza(self, stanza: object, outgoing: bool) -> None:
        if stanza["type"] not in ("chat", "normal"):
            return

        body = str(stanza["body"] or "").strip()
        media_url, media_kind, media_mime, media_filename, media_size = self._media_from_stanza(
            stanza
        )
        audio_url = media_url if media_kind == "audio" else ""
        if not body and not media_url:
            return

        chat_jid = str(stanza["to"].bare if outgoing else stanza["from"].bare)
        sender_jid = "Yo" if outgoing else chat_jid
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
                    outgoing=outgoing,
                    audio_url=audio_url,
                    media_url=media_url,
                    media_kind=media_kind,
                    media_mime=media_mime,
                    media_filename=media_filename,
                    media_size=media_size,
                    message_id=str(stanza["id"] or ""),
                    reply_quote=reply_quote,
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
                media_url, media_kind, _, _, media_size = self._media_from_xml(message)
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

    def _media_from_mam_result(self, result: object) -> tuple[str, str, str, str, int]:
        forwarded = result["mam_result"]["forwarded"]
        stanza = forwarded["stanza"]
        return self._media_from_stanza(stanza)

    def _media_from_stanza(self, stanza: object) -> tuple[str, str, str, str, int]:
        body = str(stanza["body"] or "")
        media_url, media_kind, media_mime, media_filename, media_size = self._media_from_xml(
            stanza.xml
        )
        if media_url:
            return media_url, media_kind, media_mime, media_filename, media_size

        for url in self._urls_from_text(body):
            media_kind = self._media_kind_from_url(url)
            if media_kind:
                return url, media_kind, "", self._filename_from_url(url), 0

        return "", "", "", "", 0

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
    def _media_from_xml(cls, xml: ET.Element) -> tuple[str, str, str, str, int]:
        media_mime = cls._media_mime_from_xml(xml)
        media_filename = cls._media_filename_from_xml(xml)
        media_size = cls._media_size_from_xml(xml)

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
                )

        for node in xml.iter():
            for attribute in ("uri", "url", "target", "src", "href"):
                url = node.attrib.get(attribute, "").strip()
                if not url.startswith(("http://", "https://")):
                    continue

                media_kind = cls._media_kind_from_mime_or_url(media_mime, url)
                if media_kind:
                    filename = media_filename or cls._filename_from_url(url)
                    return url, media_kind, media_mime, filename, media_size

        return "", "", media_mime, media_filename, media_size

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

    @staticmethod
    def _urls_from_text(text: str) -> list[str]:
        return [match.group(0).rstrip(").,;]") for match in URL_PATTERN.finditer(text)]

    @classmethod
    def _media_kind_from_mime_or_url(cls, mime: str, url: str) -> str:
        if mime.startswith("audio/"):
            return "audio"
        if mime.startswith("image/"):
            return "image"
        if mime.startswith("video/"):
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

    async def send_audio_file(self, to_jid: str, path: str) -> Message:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(path)

        size = file_path.stat().st_size
        mime = mimetypes.guess_type(file_path.name)[0] or "audio/ogg"
        get_url = await self["xep_0363"].upload_file(
            file_path,
            size=size,
            content_type=mime,
            timeout=60,
        )

        message = self.make_message(mto=to_jid, mbody=get_url, mtype="chat")
        message_id = str(message["id"] or "")
        self._append_file_metadata(
            message,
            url=get_url,
            filename=file_path.name,
            size=size,
            mime=mime,
        )
        message.send()
        return Message(
            chat_jid=to_jid,
            sender_jid="Yo",
            body="Mensaje de voz",
            sent_at=datetime.now(),
            outgoing=True,
            audio_url=get_url,
            media_url=get_url,
            media_kind="audio",
            media_mime=mime,
            media_filename=file_path.name,
            media_size=size,
            media_local_path=str(file_path),
            message_id=message_id,
        )

    @staticmethod
    def _append_file_metadata(
        message: object,
        url: str,
        filename: str,
        size: int,
        mime: str,
    ) -> None:
        oob = ET.Element(f"{{{OOB_NS}}}x")
        url_node = ET.SubElement(oob, f"{{{OOB_NS}}}url")
        url_node.text = url
        message.append(oob)

        file_sharing = ET.Element(f"{{{SFS_NS}}}file-sharing")
        file_node = ET.SubElement(file_sharing, f"{{{FILE_METADATA_NS}}}file")
        media_type = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}media-type")
        media_type.text = mime
        name = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}name")
        name.text = filename
        size_node = ET.SubElement(file_node, f"{{{FILE_METADATA_NS}}}size")
        size_node.text = str(size)
        sources = ET.SubElement(file_sharing, f"{{{SFS_NS}}}sources")
        ET.SubElement(sources, f"{{{URL_DATA_NS}}}url-data", {"target": url})
        message.append(file_sharing)


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

    def send_reply(
        self,
        to_jid: str,
        body: str,
        reply_to_jid: str,
        reply_to_id: str,
        fallback_end: int = 0,
    ) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        def send() -> None:
            if not self._client:
                return

            msg = self._client.make_message(mto=to_jid, mbody=body, mtype="chat")
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

    def send_reaction(self, to_jid: str, message_id: str, reaction: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        if not message_id:
            self._emit(XmppError("No se puede reaccionar: el mensaje no tiene ID XMPP."))
            return

        def send() -> None:
            if not self._client:
                return

            msg = self._client.make_message(mto=to_jid, mtype="chat")
            reactions = ET.Element(f"{{{REACTIONS_NS}}}reactions", {"id": message_id})
            reaction_node = ET.SubElement(reactions, f"{{{REACTIONS_NS}}}reaction")
            reaction_node.text = reaction
            msg.append(reactions)
            msg.send()

        self._loop.call_soon_threadsafe(send)

    def send_audio_file(self, to_jid: str, path: str) -> None:
        if not self._client or not self._loop:
            self._emit(XmppError("No hay una conexion XMPP activa."))
            return

        async def send() -> None:
            if not self._client:
                return

            try:
                message = await self._client.send_audio_file(to_jid, path)
            except Exception as exc:
                self._emit(XmppError(f"No se pudo enviar el audio: {exc}"))
                return

            self._emit(MessageReceived(message))

        def schedule() -> None:
            if self._loop:
                self._loop.create_task(send())

        self._loop.call_soon_threadsafe(schedule)

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
            self._emit(XmppError("No hay una conexion XMPP activa."))
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
            self._emit(XmppError("No hay una conexion XMPP activa."))
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
                "xep_0128",
                "xep_0199",
                "xep_0297",
                "xep_0280",
                "xep_0313",
                "xep_0363",
            )
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
