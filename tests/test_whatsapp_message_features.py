from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from cliente_xmpp.media.downloads import media_description
from cliente_xmpp.media.links import (
    copyable_message_text,
    forwardable_message_text,
    is_link_preview,
    message_links,
)
from cliente_xmpp.media.stickers import (
    convert_lottie_sticker_package,
    looks_like_bridge_sticker,
    looks_like_lottie_sticker_attachment,
)
from cliente_xmpp.models.chat import (
    Message,
    Poll,
    PollUpdate,
    apply_poll_update,
    poll_display_text,
    poll_option_counts,
    poll_option_hash,
)
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import (
    FALLBACK_NS,
    OOB_NS,
    REPLY_NS,
    STICKER_NS,
    WHATSAPP_FORWARDED_NS,
    WHATSAPP_POLL_NS,
    XMPP_HINTS_NS,
    BridgeXmppClient,
    XmppService,
)
from cliente_xmpp.xmpp.events import MessageDeliveryUpdated, MessageReceived


class MessageFeatureParsingTests(unittest.TestCase):
    def test_generic_binary_link_preview_keeps_its_destination(self) -> None:
        destination = "https://example.test/redirect"
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="Un enlace reenviado",
            media_url=destination,
            media_kind="file",
            media_mime="application/octet-stream",
            media_filename="Un enlace reenviado",
            is_forwarded=True,
        )

        self.assertTrue(is_link_preview(message))
        self.assertEqual(copyable_message_text(message), destination)
        self.assertEqual(
            media_description(message),
            f"enlace, {destination}, Un enlace reenviado",
        )

    def test_link_preview_keeps_caption_when_remote_title_differs(self) -> None:
        destination = "https://example.test/redirect"
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="Texto que acompaÃ±a al enlace",
            media_url=destination,
            media_kind="file",
            media_mime="application/octet-stream",
            media_filename="TÃ­tulo remoto",
        )
        expected = (
            f"enlace, {destination}, Texto que acompaÃ±a al enlace, tÃ­tulo: TÃ­tulo remoto"
        )

        self.assertEqual(media_description(message), expected)
        self.assertEqual(ConversationPanel._format_message_body(None, message), expected)
        self.assertEqual(message_links(message)[0].url, destination)
        self.assertEqual(
            forwardable_message_text(message),
            f"Texto que acompaÃ±a al enlace\n{destination}",
        )

    def test_text_with_drive_link_keeps_the_full_sender_caption(self) -> None:
        destination = "https://drive.google.com/drive/folders/example?usp=drive_link"
        body = f"Lee el documento y hablamos luego. {destination}"

        class Stanza:
            def __init__(self, text: str, xml: ET.Element) -> None:
                self._text = text
                self.xml = xml

            def __getitem__(self, key: str) -> str:
                if key == "body":
                    return self._text
                raise KeyError(key)

        stanza = Stanza(
            body,
            ET.fromstring(
                f"""
                <message xmlns="jabber:client">
                  <body>{body}</body>
                  <Description xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                               about="{destination}">
                    <title xmlns="https://ogp.me/ns#">drive.google.com</title>
                  </Description>
                </message>
                """
            ),
        )
        client = SimpleNamespace(
            _media_from_xml=BridgeXmppClient._media_from_xml,
            _urls_from_text=BridgeXmppClient._urls_from_text,
            _media_kind_from_url=BridgeXmppClient._media_kind_from_url,
            _filename_from_url=BridgeXmppClient._filename_from_url,
        )

        media_url, media_kind, *_ = BridgeXmppClient._media_from_stanza(client, stanza)

        self.assertEqual((media_url, media_kind), ("", ""))
        self.assertEqual(
            BridgeXmppClient._message_body_for_display(
                body,
                destination,
                "file",
                "example",
            ),
            body,
        )
        self.assertEqual(
            media_description(
                Message(
                    chat_jid="chat@example.test",
                    sender_jid="contact@example.test",
                    body=body,
                    media_url=destination,
                    media_kind="file",
                    media_filename="example",
                )
            ),
            body,
        )

    def test_merging_mam_text_replaces_old_generated_file_label(self) -> None:
        destination = "https://drive.google.com/drive/folders/example?usp=drive_link"
        target = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="Archivo: example",
            media_url=destination,
            media_kind="file",
            media_filename="example",
        )
        incoming = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body=f"Lee el documento y hablamos luego. {destination}",
        )

        MainWindow._merge_message_metadata(target, incoming)

        self.assertEqual(target.body, incoming.body)

    def test_open_selected_link_uses_preview_destination(self) -> None:
        destination = "https://example.test/redirect"
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="Texto que acompaÃ±a al enlace",
            media_url=destination,
            media_kind="file",
            media_mime="application/octet-stream",
            media_filename="TÃ­tulo remoto",
        )
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(selected_message=lambda: message)
        window.status_bar = Mock()

        with patch(
            "cliente_xmpp.ui.main_window.wx.LaunchDefaultBrowser",
            return_value=True,
        ) as open_browser:
            self.assertTrue(window._open_selected_message_link())

        open_browser.assert_called_once_with(destination)

    def test_generic_binary_attachment_stays_a_file(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="Archivo",
            media_url="https://upload.example/reporte.bin",
            media_kind="file",
            media_mime="application/octet-stream",
            media_filename="reporte.bin",
            media_size=1024,
        )

        self.assertFalse(is_link_preview(message))
        self.assertEqual(copyable_message_text(message), "Archivo")

    def test_recognizes_bridge_sticker_and_forwarded_markers(self) -> None:
        xml = ET.fromstring(
            """
            <message xmlns="jabber:client">
              <sticker xmlns="urn:xmpp:stickers:0" />
              <forwarded xmlns="urn:marco-ml:whatsapp:forwarded:0" />
            </message>
            """
        )

        self.assertTrue(BridgeXmppClient._message_is_sticker(xml))
        self.assertTrue(BridgeXmppClient._message_is_forwarded(xml))
        self.assertEqual(
            BridgeXmppClient._message_body_for_display(
                "https://upload.example/sticker.webp",
                "https://upload.example/sticker.webp",
                "image",
                "hash.webp",
                1024,
                is_sticker=True,
            ),
            "Sticker",
        )

    def test_appends_private_forwarded_flag_without_xep_0297(self) -> None:
        xml = ET.Element("message")

        XmppService._append_message_flags(xml, is_sticker=True, is_forwarded=True)

        self.assertIsNotNone(xml.find(f"{{{STICKER_NS}}}sticker"))
        self.assertIsNotNone(xml.find(f"{{{WHATSAPP_FORWARDED_NS}}}forwarded"))
        self.assertIsNone(xml.find("{urn:xmpp:forward:0}forwarded"))

    def test_parses_native_whatsapp_poll_metadata(self) -> None:
        xml = ET.fromstring(
            """
            <message xmlns="jabber:client">
              <poll xmlns="urn:marco-ml:whatsapp:poll:0"
                    id="poll-1" title="¿Café o té?" creator="123@s.whatsapp.net"
                    creator-lid="456@lid" creator-is-me="false" max-selections="2"
                    selection-mode="multiple">
                <option>Café</option><option>Té</option>
              </poll>
            </message>
            """
        )

        poll = BridgeXmppClient._poll_from_xml(xml)
        self.assertIsNotNone(poll)
        assert poll is not None
        self.assertEqual(poll.poll_id, "poll-1")
        self.assertEqual(len(poll.options), 2)
        self.assertEqual(poll.creator_jid, "123@s.whatsapp.net")
        self.assertEqual(poll.creator_lid, "456@lid")
        self.assertTrue(poll.allows_multiple)
        self.assertEqual(poll.selectable_count, 2)

    def test_legacy_poll_without_selection_mode_defaults_to_single_vote(self) -> None:
        xml = ET.fromstring(
            """
            <message xmlns="jabber:client">
              <poll xmlns="urn:marco-ml:whatsapp:poll:0"
                    id="poll-legacy" title="Una opción" creator="123@s.whatsapp.net"
                    max-selections="2">
                <option>Uno</option><option>Dos</option>
              </poll>
            </message>
            """
        )

        poll = BridgeXmppClient._poll_from_xml(xml)

        self.assertIsNotNone(poll)
        assert poll is not None
        self.assertFalse(poll.allows_multiple)
        self.assertEqual(poll.selectable_count, 1)

    def test_parses_poll_vote_update_and_replaces_previous_selection(self) -> None:
        coffee_hash = poll_option_hash("Coffee")
        tea_hash = poll_option_hash("Tea")
        xml = ET.fromstring(
            f"""
            <message xmlns="jabber:client">
              <poll-update xmlns="urn:marco-ml:whatsapp:poll:0"
                    id="poll-1" voter="123@s.whatsapp.net"
                    voter-lid="456@lid" voter-is-me="true">
                <option hash="{tea_hash}" />
              </poll-update>
            </message>
            """
        )

        update = BridgeXmppClient._poll_update_from_xml(xml, voter_name="Marco")

        self.assertEqual(
            update,
            PollUpdate(
                poll_id="poll-1",
                voter_jid="123@s.whatsapp.net",
                voter_lid="456@lid",
                voter_name="Marco",
                voter_is_me=True,
                option_hashes=(tea_hash,),
            ),
        )
        assert update is not None
        poll = Poll(
            poll_id="poll-1",
            title="Coffee or tea?",
            options=("Coffee", "Tea"),
            creator_jid="789@s.whatsapp.net",
        )
        poll = apply_poll_update(poll, update)
        poll = apply_poll_update(
            poll,
            PollUpdate(
                poll_id="poll-1",
                voter_jid="123@s.whatsapp.net",
                voter_lid="456@lid",
                voter_is_me=True,
                option_hashes=(coffee_hash,),
            ),
        )

        self.assertEqual(poll_option_counts(poll), (1, 0))
        self.assertEqual(len(poll.votes), 1)
        self.assertIn("☑ Coffee — 1 voto", poll_display_text(poll))

    def test_poll_update_is_merged_without_creating_a_chat_message(self) -> None:
        poll = Poll(
            poll_id="poll-1",
            title="Choose",
            options=("One", "Two"),
            creator_jid="123@s.whatsapp.net",
        )
        original = Message(
            chat_jid="#room@example.test",
            sender_jid="member@example.test",
            body=poll_display_text(poll),
            message_id="poll-message",
            chat_is_group=True,
            poll=poll,
        )
        update_message = Message(
            chat_jid=original.chat_jid,
            sender_jid="voter@example.test",
            body="",
            message_id="vote-event",
            chat_is_group=True,
            poll_update=PollUpdate(
                poll_id="poll-1",
                voter_jid="456@s.whatsapp.net",
                voter_name="Ana",
                option_hashes=(poll_option_hash("Two"),),
            ),
        )
        window = MainWindow.__new__(MainWindow)
        window.messages_by_chat = {original.chat_jid: [original]}

        updated = window._merge_messages(original.chat_jid, [update_message])

        self.assertEqual(window.messages_by_chat[original.chat_jid], [original])
        self.assertIn(original, updated)
        self.assertEqual(poll_option_counts(original.poll), (0, 1))

    def test_sticker_description_does_not_expose_opaque_filename(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="https://upload.example/0123456789.webp",
            media_url="https://upload.example/0123456789.webp",
            media_kind="image",
            media_mime="image/webp",
            media_filename="0123456789abcdef.webp",
            media_size=4096,
            is_sticker=True,
        )

        self.assertEqual(media_description(message), "Sticker")

    def test_recognizes_converted_bridge_sticker_without_xep_marker(self) -> None:
        hash_name = "4591049791d5593e12e82d0fa0e8236024150d6b397ac20c26c8dc0d823cd191"
        xml = ET.fromstring('<message xmlns="jabber:client" />')

        for filename in (f"{hash_name}.webp", f"{hash_name} (1).webp"):
            with self.subTest(filename=filename):
                self.assertTrue(
                    BridgeXmppClient._message_is_sticker(
                        xml,
                        media_kind="image",
                        media_mime="image/webp",
                        media_filename=filename,
                    )
                )

    def test_does_not_classify_regular_webp_image_as_sticker(self) -> None:
        self.assertFalse(
            looks_like_bridge_sticker(
                media_kind="image",
                media_mime="image/webp",
                media_filename="vacaciones.webp",
            )
        )
        self.assertFalse(
            looks_like_bridge_sticker(
                media_kind="image",
                media_mime="image/jpeg",
                media_filename="a" * 64 + ".webp",
            )
        )

    def test_recognizes_only_opaque_hash_bins_as_lottie_candidates(self) -> None:
        hash_name = "f033edb72c3926b34d9e29df2cb13b2d6c23a2f550b854edd4b4c5e97db56c06"

        for mime in (
            "application/octet-stream",
            "application/zip",
            "application/was",
            "application/x-bridge-specific",
        ):
            with self.subTest(mime=mime):
                self.assertTrue(
                    looks_like_lottie_sticker_attachment(
                        media_kind="file",
                        media_mime=mime,
                        media_filename=f"{hash_name}.bin",
                        media_size=66_944,
                    )
                )

        self.assertFalse(
            looks_like_lottie_sticker_attachment(
                media_kind="file",
                media_mime="application/was",
                media_filename="reporte.bin",
                media_size=66_944,
            )
        )
        self.assertFalse(
            looks_like_lottie_sticker_attachment(
                media_kind="file",
                media_mime="application/was",
                media_filename=f"{hash_name}.bin",
                media_size=5 * 1024 * 1024 + 1,
            )
        )

    def test_converts_lottie_zip_to_a_local_webp_frame(self) -> None:
        lottie = {
            "v": "5.7.4",
            "fr": 30,
            "ip": 0,
            "op": 1,
            "w": 32,
            "h": 32,
            "layers": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / ("a" * 64 + ".bin")
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("animation/animation.json", json.dumps(lottie))

            destination = convert_lottie_sticker_package(source)

            self.assertIsNotNone(destination)
            assert destination is not None
            self.assertEqual(destination.suffix, ".webp")
            payload = destination.read_bytes()
            self.assertEqual(payload[:4], b"RIFF")
            self.assertEqual(payload[8:12], b"WEBP")

    def test_does_not_convert_an_arbitrary_bin_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / ("b" * 64 + ".bin")
            source.write_bytes(b"not a sticker")

            self.assertIsNone(convert_lottie_sticker_package(source))


class MessageFeatureStoreTests(unittest.TestCase):
    def test_persists_local_lottie_normalization_without_changing_remote_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="me@example.test",
                body="Archivo",
                message_id="lottie-sticker-1",
                media_url="https://upload.example/raw.bin",
                media_kind="file",
                media_mime="application/octet-stream",
                media_filename="raw.bin",
            )
            store.upsert_messages("me@example.test", [message])
            message.media_local_path = str(Path(temp_dir) / "preview.webp")
            message.is_sticker = True

            store.update_message_media_local_path("me@example.test", message)

            loaded = store.load_recent_messages("me@example.test", message.chat_jid)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].is_sticker)
            self.assertEqual(loaded[0].media_kind, "file")
            self.assertEqual(loaded[0].media_mime, "application/octet-stream")
            self.assertEqual(loaded[0].media_filename, "raw.bin")
            self.assertEqual(loaded[0].media_local_path, message.media_local_path)

    def test_persists_enriched_flags_without_later_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="contact@example.test",
                body="Sticker",
                sent_at=datetime(2026, 7, 13, 12, 0),
                media_url="https://upload.example/sticker.webp",
                media_kind="image",
                message_id="wa-sticker-1",
                is_sticker=True,
                is_forwarded=True,
            )
            store.upsert_messages("me@example.test", [message])

            message.is_sticker = False
            message.is_forwarded = False
            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", message.chat_jid)
            self.assertTrue(loaded[0].is_sticker)
            self.assertTrue(loaded[0].is_forwarded)

    def test_plain_and_forwarded_messages_are_not_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            store = MessageStore(path)
            sent_at = datetime(2026, 7, 13, 12, 0)
            plain = Message(
                chat_jid="chat@example.test",
                sender_jid="contact@example.test",
                body="Mismo texto",
                sent_at=sent_at,
                message_id="plain-id",
            )
            forwarded = Message(
                chat_jid=plain.chat_jid,
                sender_jid=plain.sender_jid,
                body=plain.body,
                sent_at=sent_at + timedelta(seconds=1),
                message_id="forwarded-id",
                is_forwarded=True,
            )
            store.upsert_messages("me@example.test", [plain, forwarded])

            reopened = MessageStore(path)
            loaded = reopened.load_recent_messages("me@example.test", plain.chat_jid)
            self.assertEqual(len(loaded), 2)
            self.assertEqual([message.is_forwarded for message in loaded], [False, True])

    def test_migration_marks_cached_bridge_webp_as_sticker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            account_jid = "me@example.test"
            hash_name = "4591049791d5593e12e82d0fa0e8236024150d6b397ac20c26c8dc0d823cd191"
            store = MessageStore(path)
            message = Message(
                chat_jid="contact@example.test",
                sender_jid="contact@example.test",
                body=f"https://upload.example/{hash_name}.webp",
                sent_at=datetime(2026, 7, 13, 13, 33),
                media_url=f"https://upload.example/{hash_name}.webp",
                media_kind="image",
                media_mime="image/webp",
                media_filename=f"{hash_name} (1).webp",
                message_id="cached-sticker",
            )
            store.upsert_messages(account_jid, [message])
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("UPDATE messages SET is_sticker = 0")
                conn.execute("PRAGMA user_version = 14")

            reopened = MessageStore(path)
            loaded = reopened.load_recent_messages(account_jid, message.chat_jid)
            self.assertTrue(loaded[0].is_sticker)
            self.assertEqual(loaded[0].body, message.body)
            with closing(sqlite3.connect(path)) as conn:
                stored_flag = conn.execute("SELECT is_sticker FROM messages").fetchone()[0]
                preview = conn.execute(
                    "SELECT last_message_preview FROM chats"
                ).fetchone()[0]
            self.assertEqual(stored_flag, 1)
            self.assertEqual(preview, "Sticker")

    def test_normal_message_keeps_legacy_fallback_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            store = MessageStore(path)
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="contact@example.test",
                body="Foto",
                sent_at=datetime(2026, 7, 13, 12, 0),
                media_url="https://upload.example/photo.jpg",
                media_kind="image",
            )
            store.upsert_messages("me@example.test", [message])
            legacy_payload = "|".join(
                (
                    message.sent_at.isoformat(),
                    message.sender_jid,
                    message.body,
                    str(message.outgoing),
                    message.audio_url,
                    message.media_url,
                    message.media_kind,
                    message.reply_quote,
                )
            )
            legacy_key = f"hash:{hashlib.sha256(legacy_payload.encode('utf-8')).hexdigest()}"
            with closing(sqlite3.connect(path)) as conn:
                stored_key = conn.execute("SELECT message_key FROM messages").fetchone()[0]
            self.assertEqual(stored_key, legacy_key)

            message.media_local_path = str(Path(temp_dir) / "photo.jpg")
            store.update_message_media_local_path("me@example.test", message)
            loaded = store.load_recent_messages("me@example.test", message.chat_jid)
            self.assertEqual(loaded[0].media_local_path, message.media_local_path)


class _ImmediateLoop:
    @staticmethod
    def call_soon_threadsafe(callback: object) -> None:
        callback()


class _FakeMessage:
    def __init__(self, to_jid: str, body: str, message_type: str) -> None:
        self.xml = ET.Element("message", {"to": to_jid, "type": message_type})
        ET.SubElement(self.xml, "body").text = body
        self.sent = False

    def __getitem__(self, key: str) -> str:
        if key == "body":
            return self.xml.findtext("body") or ""
        raise KeyError(key)

    def __setitem__(self, key: str, value: object) -> None:
        if key == "id":
            self.xml.set("id", str(value))
        elif key == "body":
            body = self.xml.find("body")
            if body is not None:
                body.text = str(value)

    def append(self, node: ET.Element) -> None:
        self.xml.append(node)

    def enable(self, _plugin: str) -> None:
        raise KeyError

    def send(self) -> None:
        self.sent = True


class _FakeClient:
    def __init__(self) -> None:
        self.message: _FakeMessage | None = None
        self.raw_stanza: bytes | None = None
        self._pending_poll_votes: dict[str, object] = {}

    def make_message(self, mto: str, mbody: str, mtype: str) -> _FakeMessage:
        self.message = _FakeMessage(mto, mbody, mtype)
        return self.message

    def send_raw(self, data: bytes) -> None:
        self.raw_stanza = data

    @staticmethod
    def track_transient_message_retry(
        _chat_jid: str,
        _message_id: str,
        _send: object,
    ) -> None:
        return None

    @staticmethod
    def _join_group_chat(_jid: str) -> None:
        return None

    _append_file_metadata = staticmethod(BridgeXmppClient._append_file_metadata)
    _filename_from_url = staticmethod(BridgeXmppClient._filename_from_url)


class ForwardSendContractTests(unittest.TestCase):
    def test_ephemeral_message_requests_no_storage_or_carbon_copy(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()

        service.send_message(
            "contact@example.test",
            "/stats",
            ephemeral=True,
        )

        assert fake_client.message is not None
        self.assertIsNotNone(
            fake_client.message.xml.find(f"{{{XMPP_HINTS_NS}}}no-store")
        )
        self.assertIsNotNone(
            fake_client.message.xml.find(f"{{{XMPP_HINTS_NS}}}no-copy")
        )
        self.assertIsNone(fake_client.message.xml.find("{urn:xmpp:receipts}request"))
        self.assertTrue(fake_client.message.sent)
        self.assertEqual(emitted, [])

    def test_audio_upload_reply_metadata_uses_xep_0461_and_fallback(self) -> None:
        message = _FakeMessage("contact@example.test", "audio.ogg", "chat")

        BridgeXmppClient._append_reply_metadata(
            message,
            reply_to_jid="contact@example.test",
            reply_to_id="quoted-audio-id",
            reply_quote="audio citado",
        )

        reply = message.xml.find(f"{{{REPLY_NS}}}reply")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.attrib["to"], "contact@example.test")
        self.assertEqual(reply.attrib["id"], "quoted-audio-id")
        fallback = message.xml.find(f"{{{FALLBACK_NS}}}fallback")
        self.assertIsNotNone(fallback)
        assert fallback is not None
        self.assertEqual(fallback.attrib["for"], REPLY_NS)
        self.assertEqual(message.xml.findtext("body"), "> audio citado\n" + "audio.ogg")

    def test_reply_sends_xep_0461_target_with_remote_id(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()

        service.send_reply(
            "contact@example.test",
            "respuesta",
            "contact@example.test",
            "whatsapp-message-id",
            message_id="cliente-xmpp-reply-1",
        )

        assert fake_client.message is not None
        reply = fake_client.message.xml.find(f"{{{REPLY_NS}}}reply")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.attrib["to"], "contact@example.test")
        self.assertEqual(reply.attrib["id"], "whatsapp-message-id")
        self.assertTrue(fake_client.message.sent)

    def test_vote_sends_only_the_private_poll_extension(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()
        poll = Poll(
            poll_id="poll-1",
            title="¿Café o té?",
            options=("Café", "Té"),
            creator_jid="123@s.whatsapp.net",
            creator_lid="456@lid",
        )

        service.send_poll_vote("contact@example.test", poll, ["Té"])

        assert fake_client.message is not None
        vote = fake_client.message.xml.find(f"{{{WHATSAPP_POLL_NS}}}vote")
        self.assertIsNotNone(vote)
        assert vote is not None
        self.assertEqual(vote.attrib["id"], "poll-1")
        self.assertEqual(vote.attrib["creator"], "123@s.whatsapp.net")
        self.assertEqual(vote.attrib["creator-lid"], "456@lid")
        self.assertEqual(
            [node.text for node in vote.findall(f"{{{WHATSAPP_POLL_NS}}}option")],
            ["Té"],
        )
        self.assertEqual(fake_client.message.xml.findtext("body"), " ")
        self.assertIsNotNone(fake_client.message.xml.find(f"{{{XMPP_HINTS_NS}}}store"))
        self.assertIsNotNone(
            fake_client.message.xml.find("{urn:xmpp:receipts}request")
        )
        self.assertTrue(fake_client.message.xml.attrib.get("id"))
        self.assertEqual(len(fake_client._pending_poll_votes), 1)
        self.assertIsNotNone(fake_client.raw_stanza)

    def test_poll_vote_changes_locally_only_after_bridge_receipt(self) -> None:
        service = XmppService(lambda _event: None)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()
        poll = Poll(
            poll_id="poll-confirmed",
            title="Elige",
            options=("Uno", "Dos"),
            creator_jid="123@s.whatsapp.net",
        )
        service.send_poll_vote("contact@example.test", poll, ["Dos"])
        message_id = next(iter(fake_client._pending_poll_votes))

        emitted: list[object] = []
        client = SimpleNamespace(
            _pending_poll_votes=fake_client._pending_poll_votes,
            _emit=emitted.append,
            _debug_whatsapp=lambda _message: None,
        )

        self.assertTrue(
            BridgeXmppClient._confirm_pending_poll_vote(client, message_id)
        )
        self.assertEqual(client._pending_poll_votes, {})
        self.assertEqual(len(emitted), 1)
        self.assertIsInstance(emitted[0], MessageReceived)
        event = emitted[0]
        assert isinstance(event, MessageReceived)
        update = event.message.poll_update
        self.assertIsNotNone(update)
        assert update is not None
        self.assertTrue(update.voter_is_me)
        self.assertEqual(update.option_hashes, (poll_option_hash("Dos"),))

    def test_vote_uses_lid_when_own_creator_jid_is_missing(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()
        poll = Poll(
            poll_id="poll-own-group",
            title="Propia desde otro dispositivo",
            options=("A", "B"),
            creator_jid="",
            creator_lid="456@lid",
            creator_is_me=True,
        )

        service.send_poll_vote("#group@example.test", poll, ["A"], is_group=True)

        assert fake_client.message is not None
        vote = fake_client.message.xml.find(f"{{{WHATSAPP_POLL_NS}}}vote")
        self.assertIsNotNone(vote)
        assert vote is not None
        self.assertEqual(vote.attrib["creator"], "456@lid")
        self.assertEqual(vote.attrib["creator-lid"], "456@lid")
        self.assertIsNotNone(fake_client.raw_stanza)

    def test_forward_media_reuses_attachment_and_marks_sticker(self) -> None:
        emitted: list[object] = []
        service = XmppService(emitted.append)
        fake_client = _FakeClient()
        service._client = fake_client
        service._loop = _ImmediateLoop()
        source = Message(
            chat_jid="source@example.test",
            sender_jid="contact@example.test",
            body="Sticker",
            media_url="https://upload.example/sticker.webp",
            media_kind="image",
            media_mime="image/webp",
            media_filename="sticker.webp",
            media_size=2048,
            is_sticker=True,
        )

        service.send_forward(
            "target@example.test",
            source,
            message_id="cliente-xmpp-forward-1",
        )

        assert fake_client.message is not None
        self.assertTrue(fake_client.message.sent)
        self.assertIsNotNone(
            fake_client.message.xml.find(f"{{{WHATSAPP_FORWARDED_NS}}}forwarded")
        )
        self.assertIsNotNone(fake_client.message.xml.find(f"{{{STICKER_NS}}}sticker"))
        self.assertEqual(
            fake_client.message.xml.findtext(f"{{{OOB_NS}}}x/{{{OOB_NS}}}url"),
            source.media_url,
        )
        self.assertTrue(
            any(
                isinstance(event, MessageDeliveryUpdated)
                and event.delivery_state == "sent"
                for event in emitted
            )
        )


if __name__ == "__main__":
    unittest.main()
