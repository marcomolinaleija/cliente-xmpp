from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image, ImageChops, ImageOps
from slixmpp import Message as StanzaMessage

from cliente_xmpp.media.outgoing_stickers import MAX_STATIC_BYTES, prepare_outgoing_sticker
from cliente_xmpp.storage.sticker_library import StickerLibrary
from cliente_xmpp.xmpp.client import FILE_METADATA_NS, SFS_NS, STICKER_NS, BridgeXmppClient


class OutgoingStickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "downloads"
        self.patch = patch("cliente_xmpp.media.outgoing_stickers.DOWNLOADS_DIR", self.destination)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def image(self, name: str, size: tuple[int, int] = (328, 300)) -> Path:
        source = self.root / name
        Image.new("RGBA", size, (255, 0, 0, 128)).save(source)
        return source

    def test_png_becomes_bounded_transparent_webp_without_changing_original(self) -> None:
        source = self.image("blushing.png")
        original = source.read_bytes()
        result = prepare_outgoing_sticker(source)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(result.parent, self.destination)
        self.assertEqual(result.suffix, ".webp")
        self.assertLessEqual(result.stat().st_size, MAX_STATIC_BYTES)
        self.assertEqual(list(self.destination.glob("*.part")), [])
        with Image.open(result) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((256, 256))[3], 128)

    def test_jpeg_is_padded_not_cropped(self) -> None:
        source = self.root / "photo.jpg"
        Image.new("RGB", (1000, 500), "blue").save(source)
        with Image.open(prepare_outgoing_sticker(source)) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.getpixel((256, 0))[3], 0)
            self.assertEqual(image.getpixel((256, 256))[3], 255)

    def test_compatible_webp_keeps_original_bytes_and_metadata(self) -> None:
        source = self.root / "native.webp"
        Image.new("RGBA", (512, 512), "red").save(source, exif=b"test-pack-metadata")
        original = source.read_bytes()
        self.assertEqual(prepare_outgoing_sticker(source), source)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_animated_webp_is_not_flattened(self) -> None:
        source = self.root / "animated.webp"
        Image.new("RGBA", (512, 512), "red").save(
            source,
            save_all=True,
            append_images=[Image.new("RGBA", (512, 512), "blue")],
            duration=[100, 100],
            loop=0,
        )
        original = source.read_bytes()
        self.assertEqual(prepare_outgoing_sticker(source), source)
        self.assertEqual(source.read_bytes(), original)

    def test_library_native_sticker_is_copied_for_message_lifetime(self) -> None:
        source = self.image("native.webp", (512, 512))
        original = source.read_bytes()
        copied = prepare_outgoing_sticker(source, copy_compatible=True)
        self.assertNotEqual(copied, source)
        self.assertEqual(copied.parent, self.destination)
        source.unlink()
        self.assertEqual(copied.read_bytes(), original)

    def test_invalid_animation_is_rejected_without_uploadable_copy(self) -> None:
        for size, duration in (((520, 260), 5), ((512, 512), 5), ((512, 512), 6000)):
            with self.subTest(size=size, duration=duration):
                source = self.root / "animation.webp"
                Image.new("RGBA", size, "red").save(
                    source,
                    save_all=True,
                    append_images=[Image.new("RGBA", size, "blue")],
                    duration=[duration, duration],
                )
                with self.assertRaises(ValueError):
                    prepare_outgoing_sticker(source)
        self.assertFalse(self.destination.exists())

    def test_520_pixel_animation_preserves_design_timing_transparency_and_metadata(self) -> None:
        source = self.root / "oversize.webp"
        frames = [Image.new("RGBA", (520, 260), color) for color in ("red", "blue")]
        frames[0].putpixel((260, 130), (0, 255, 0, 128))
        frames[0].save(
            source, save_all=True, append_images=frames[1:], lossless=True,
            duration=[80, 160], loop=3, exif=b"pack-metadata", xmp=b"accessible-metadata",
        )
        original = source.read_bytes()
        result = prepare_outgoing_sticker(source)
        self.assertNotEqual(result, source)
        self.assertEqual(source.read_bytes(), original)
        with Image.open(source) as native, Image.open(result) as resized:
            self.assertEqual(resized.size, (512, 512))
            self.assertEqual(resized.n_frames, native.n_frames)
            self.assertEqual(resized.info["loop"], 3)
            self.assertEqual(resized.info["exif"], b"pack-metadata")
            self.assertEqual(resized.info["xmp"], b"accessible-metadata")
            for index, duration in enumerate((80, 160)):
                native.seek(index)
                resized.seek(index)
                resized.load()
                expected = ImageOps.pad(
                    native.convert("RGBA"), (512, 512),
                    method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0),
                )
                self.assertEqual(resized.info["duration"], duration)
                decoded = resized.convert("RGBA")
                self.assertIsNone(
                    ImageChops.difference(decoded.getchannel("A"), expected.getchannel("A"))
                    .getbbox()
                )
                # RGBA getbbox can ignore RGB errors when the alpha difference is zero.
                background = Image.new("RGBA", (512, 512), "white")
                self.assertIsNone(ImageChops.difference(
                    Image.alpha_composite(background, decoded).convert("RGB"),
                    Image.alpha_composite(background, expected).convert("RGB"),
                ).getbbox())
                self.assertEqual(resized.getpixel((256, 0))[3], 0)
        entry = StickerLibrary(self.root / "library").add(
            source, description="Texto alternativo recibido."
        )
        self.assertTrue(entry.animated)
        self.assertEqual(entry.description, "Texto alternativo recibido.")
        with Image.open(entry.path) as stored:
            self.assertEqual(stored.size, (512, 512))
            self.assertEqual(stored.n_frames, 2)

    def test_static_520_pixel_webp_is_adjusted_without_cropping(self) -> None:
        source = self.image("oversize-static.webp", (520, 260))
        original = source.read_bytes()
        with Image.open(prepare_outgoing_sticker(source)) as result:
            self.assertEqual(result.size, (512, 512))
            self.assertEqual(result.getpixel((256, 0))[3], 0)
            self.assertEqual(result.getpixel((256, 256))[3], 128)
        self.assertEqual(source.read_bytes(), original)

    def test_animation_normalization_respects_memory_and_lossless_size_limits(self) -> None:
        source = self.root / "oversize.webp"
        Image.new("RGBA", (520, 260), "red").save(
            source, save_all=True, append_images=[Image.new("RGBA", (520, 260), "blue")],
            duration=[100, 100], lossless=True,
        )
        original = source.read_bytes()
        for limit, message in (("MAX_ANIMATION_PIXELS", "memoria"),
                               ("MAX_ANIMATED_BYTES", "sin perder calidad")):
            with self.subTest(limit=limit), patch(
                f"cliente_xmpp.media.outgoing_stickers.{limit}", 1
            ):
                with self.assertRaisesRegex(ValueError, message):
                    prepare_outgoing_sticker(source)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_fake_image_is_rejected(self) -> None:
        source = self.root / "fake.png"
        source.write_text("not an image")
        with self.assertRaises(ValueError):
            prepare_outgoing_sticker(source)
        self.assertFalse(self.destination.exists())

    def test_complex_image_fits_static_limit(self) -> None:
        source = self.root / "noise.png"
        Image.effect_noise((512, 512), 100).convert("RGBA").save(source)
        result = prepare_outgoing_sticker(source)
        self.assertLessEqual(result.stat().st_size, MAX_STATIC_BYTES)


class StickerSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_animated_library_sticker_and_edited_description_survive_repeated_wire_send(self):
        """Validate CAN's wire output, not WhatsApp/Slidge's native delivery."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "animated.webp"
            Image.new("RGBA", (512, 512), "red").save(
                source,
                save_all=True,
                append_images=[Image.new("RGBA", (512, 512), "blue")],
                duration=[100, 100],
                loop=0,
            )
            library = StickerLibrary(root / "library")
            entry = library.add(source, description="Descripción inicial")
            description = "Descripción corregida: una figura saluda y cambia de color."
            library.edit(entry.id, description=description)
            entry = library.get(entry.id)
            upload = AsyncMock(return_value="https://upload.example.test/sticker.webp")
            stanzas = []

            def make_message(**kwargs):
                stanza = StanzaMessage()
                stanza["body"] = kwargs["mbody"]
                stanza["id"] = "fixture-id"
                stanza.send = Mock()
                stanzas.append(stanza)
                return stanza

            client = SimpleNamespace(
                _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
                _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
                _upload_file=upload,
                make_message=make_message,
                _append_file_metadata=BridgeXmppClient._append_file_metadata,
                _append_reply_metadata=BridgeXmppClient._append_reply_metadata,
                _message_body_for_display=BridgeXmppClient._message_body_for_display,
            )
            with patch("cliente_xmpp.media.outgoing_stickers.DOWNLOADS_DIR", root / "downloads"):
                for _ in range(2):
                    sent = await BridgeXmppClient.send_file(
                        client,
                        "chat@example.test",
                        entry.path,
                        as_sticker=True,
                        copy_sticker=True,
                        sticker_description=entry.description,
                    )
                    copy = Path(sent.media_local_path)
                    self.assertNotEqual(copy, Path(entry.path))
                    self.assertEqual(copy.read_bytes(), source.read_bytes())
                    with Image.open(copy) as image:
                        self.assertEqual(image.n_frames, 2)
                    self.assertEqual(sent.media_alt_text, description)
            for stanza in stanzas:
                self.assertEqual(
                    stanza.xml.findtext(
                        f"{{{SFS_NS}}}file-sharing/{{{FILE_METADATA_NS}}}file/"
                        f"{{{FILE_METADATA_NS}}}desc"
                    ),
                    description,
                )
                self.assertIsNotNone(stanza.xml.find(f"{{{STICKER_NS}}}sticker"))
            self.assertEqual(upload.await_count, 2)

    async def test_upload_and_stanza_use_real_webp_in_direct_and_group_chats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sticker.png"
            Image.new("RGBA", (328, 300), "red").save(source)
            for is_group in (False, True):
                with (
                    self.subTest(is_group=is_group),
                    patch(
                        "cliente_xmpp.media.outgoing_stickers.DOWNLOADS_DIR",
                        Path(directory) / "downloads",
                    ),
                ):
                    stanza = StanzaMessage()
                    stanza["id"] = "sticker-test"
                    stanza.send = Mock()
                    upload = AsyncMock(return_value="https://upload.example.test/sticker.webp")
                    client = SimpleNamespace(
                        _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
                        _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
                        _upload_file=upload,
                        _join_group_chat=Mock(),
                        make_message=Mock(return_value=stanza),
                        _append_file_metadata=BridgeXmppClient._append_file_metadata,
                        _append_reply_metadata=BridgeXmppClient._append_reply_metadata,
                        _message_body_for_display=BridgeXmppClient._message_body_for_display,
                    )
                    result = await BridgeXmppClient.send_file(
                        client,
                        "chat@example.test",
                        str(source),
                        is_group=is_group,
                        as_sticker=True,
                        reply_to_jid="room@example.test/member",
                        reply_to_id="quoted-id",
                        reply_quote="Quoted text",
                        sticker_description="Una figura saluda.",
                    )
                    uploaded = upload.call_args.args[0]
                    with Image.open(uploaded) as image:
                        self.assertEqual(image.format, "WEBP")
                    self.assertEqual(upload.call_args.kwargs["content_type"], "image/webp")
                    self.assertEqual(result.media_mime, "image/webp")
                    self.assertEqual(result.media_local_path, str(uploaded))
                    self.assertTrue(result.is_sticker)
                    self.assertEqual(result.media_alt_text, "Una figura saluda.")
                    self.assertEqual(
                        stanza.xml.findtext(
                            f"{{{SFS_NS}}}file-sharing/{{{FILE_METADATA_NS}}}file/"
                            f"{{{FILE_METADATA_NS}}}desc"
                        ),
                        result.media_alt_text,
                    )
                    self.assertIsNotNone(stanza.xml.find(f"{{{STICKER_NS}}}sticker"))
                    self.assertEqual(
                        stanza.xml.findtext(
                            f"{{{SFS_NS}}}file-sharing/{{{FILE_METADATA_NS}}}file/"
                            f"{{{FILE_METADATA_NS}}}media-type"
                        ),
                        "image/webp",
                    )
                    self.assertEqual(
                        stanza.xml.find("{urn:xmpp:reply:0}reply").get("id"), "quoted-id"
                    )
                    stanza.send.assert_called_once()

    async def test_failed_upload_removes_only_generated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sticker.png"
            Image.new("RGBA", (32, 32), "red").save(source)
            destination = Path(directory) / "downloads"
            client = SimpleNamespace(
                _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
                _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
                _upload_file=AsyncMock(side_effect=RuntimeError("upload failed")),
            )
            with patch("cliente_xmpp.media.outgoing_stickers.DOWNLOADS_DIR", destination):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    await BridgeXmppClient.send_file(
                        client, "chat@example.test", str(source), as_sticker=True
                    )
            self.assertTrue(source.exists())
            self.assertEqual(list(destination.iterdir()), [])

    async def test_failed_library_sticker_upload_keeps_owned_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "native.webp"
            Image.new("RGBA", (512, 512), "red").save(source)
            original = source.read_bytes()
            destination = Path(directory) / "downloads"
            upload = AsyncMock(side_effect=RuntimeError("upload failed"))
            client = SimpleNamespace(
                _mime_type_for_file=BridgeXmppClient._mime_type_for_file,
                _media_kind_from_mime_or_url=BridgeXmppClient._media_kind_from_mime_or_url,
                _upload_file=upload,
            )
            with patch("cliente_xmpp.media.outgoing_stickers.DOWNLOADS_DIR", destination):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    await BridgeXmppClient.send_file(
                        client,
                        "chat@example.test",
                        str(source),
                        as_sticker=True,
                        copy_sticker=True,
                    )
            self.assertNotEqual(upload.call_args.args[0], source)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
