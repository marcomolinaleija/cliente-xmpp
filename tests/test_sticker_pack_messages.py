from __future__ import annotations

import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cliente_xmpp.media.sticker_packs import (
    import_pack,
    is_sticker_pack_attachment,
    sticker_pack_from_message,
)
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.sticker_library import StickerLibrary
from tests.test_bridge_sticker_pack import envelope
from tools.bridge_sticker_pack import normalize_pack_sync


class StickerPackMessageTests(unittest.TestCase):
    def messages(self):
        attachment = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="Archivo",
            message_id="attachment-id",
            media_kind="file",
            media_mime="application/zip",
            media_filename="stickers.canstickers",
            media_url="https://upload.example.test/file",
        )
        caption = replace(
            attachment,
            body="Paquete de stickers: Fixture",
            message_id="caption-id",
            media_kind="",
            media_mime="",
            media_filename="",
            media_url="",
        )
        return attachment, caption

    def test_direct_and_split_caption_resolve_same_attachment_without_mutation(self):
        attachment, caption = self.messages()
        for messages in ([attachment, caption], [caption, attachment]):
            self.assertIs(sticker_pack_from_message(messages, caption), attachment)
            self.assertIs(sticker_pack_from_message(messages, attachment), attachment)
        self.assertEqual(caption.media_url, "")
        self.assertNotEqual(caption.message_id, attachment.message_id)

    def test_caption_rejects_wrong_identity_timestamp_ambiguity_and_withdrawal(self):
        attachment, caption = self.messages()
        from datetime import timedelta

        for candidate in (
            replace(attachment, chat_jid="other@example.test"),
            replace(attachment, sender_jid="other@example.test"),
            replace(attachment, outgoing=True),
            replace(attachment, sent_at=attachment.sent_at + timedelta(seconds=1)),
            replace(attachment, retracted=True),
            replace(attachment, media_filename="ordinary.zip"),
        ):
            self.assertIsNone(sticker_pack_from_message([candidate, caption], caption))
        self.assertIsNone(sticker_pack_from_message([attachment, caption, attachment], caption))
        self.assertIsNone(sticker_pack_from_message([attachment], caption))
        self.assertIsNone(
            sticker_pack_from_message([attachment, caption], replace(caption, retracted=True))
        )

    def test_url_extension_and_local_copy_are_recognized_but_not_arbitrary_zip(self):
        attachment, _ = self.messages()
        self.assertTrue(
            is_sticker_pack_attachment(
                replace(
                    attachment,
                    media_filename="",
                    media_url="https://upload.example.test/pack.CANSTICKERS?x=1",
                )
            )
        )
        self.assertTrue(
            is_sticker_pack_attachment(
                replace(attachment, media_url="", media_local_path="fixture.canstickers")
            )
        )
        self.assertFalse(
            is_sticker_pack_attachment(replace(attachment, media_filename="ordinary.zip"))
        )

    def test_native_receiver_imports_real_library_static_and_animated_with_descriptions(self):
        from PIL import Image

        for animated in (False, True):
            with self.subTest(animated=animated), tempfile.TemporaryDirectory() as directory:
                raw, original = envelope(
                    animated=animated, filename="000.webp" if animated else "000.png"
                )
                source = Path(directory) / "fixture.canstickers"
                source.write_bytes(normalize_pack_sync(raw))
                library = StickerLibrary(Path(directory) / "library")
                pack_id = import_pack(library, source)
                entry = library.list_stickers(pack_id=pack_id)[0]
                self.assertEqual(entry.description, "Una figura saluda.")
                self.assertEqual(entry.animated, animated)
                payload = Path(entry.path).read_bytes()
                if animated:
                    self.assertEqual(payload, original)
                with Image.open(io.BytesIO(payload)) as image:
                    self.assertEqual(image.size, (512, 512))
