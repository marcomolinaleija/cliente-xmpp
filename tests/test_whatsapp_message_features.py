from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from cliente_xmpp.media.downloads import media_description
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.xmpp.client import (
    OOB_NS,
    STICKER_NS,
    WHATSAPP_FORWARDED_NS,
    BridgeXmppClient,
    XmppService,
)
from cliente_xmpp.xmpp.events import MessageDeliveryUpdated


class MessageFeatureParsingTests(unittest.TestCase):
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


class MessageFeatureStoreTests(unittest.TestCase):
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

    def __setitem__(self, key: str, value: object) -> None:
        if key == "id":
            self.xml.set("id", str(value))

    def append(self, node: ET.Element) -> None:
        self.xml.append(node)

    def enable(self, _plugin: str) -> None:
        raise KeyError

    def send(self) -> None:
        self.sent = True


class _FakeClient:
    def __init__(self) -> None:
        self.message: _FakeMessage | None = None

    def make_message(self, mto: str, mbody: str, mtype: str) -> _FakeMessage:
        self.message = _FakeMessage(mto, mbody, mtype)
        return self.message

    @staticmethod
    def _join_group_chat(_jid: str) -> None:
        return None

    _append_file_metadata = staticmethod(BridgeXmppClient._append_file_metadata)
    _filename_from_url = staticmethod(BridgeXmppClient._filename_from_url)


class ForwardSendContractTests(unittest.TestCase):
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
