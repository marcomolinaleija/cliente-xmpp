from __future__ import annotations

import ast
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from slixmpp.exceptions import XMPPError

from tools.bridge_audio_modes import (
    AUDIO_MODE_NS,
    audio_attachment_caption,
    audio_attachment_mime,
)
from tools.patch_slidge_audio_modes_v31 import patch_package

# Narrow fixture for the pinned v30 contracts, not a substitute for image/runtime tests.
MIXINS = """import abc
class RecipientMixin(abc.ABC):
    async def _on_file(self, xmpp_msg):
        att = xmpp_msg.attachments[0]
        async with att.get() as resp:
            if resp.status == 200:
                if att.content_type == "application/x-can-sticker-pack":
                    data = await resp.read()
                else:
                    data = await resp.read()
            else:
                raise XMPPError("not-acceptable", "Download error")
            content_type = resp.content_type
        if att.content_type == "application/x-can-sticker-pack":
            content_type = "application/x-can-sticker-pack"
        attachment = SimpleNamespace(
            Caption="" if att.is_sticker else xmpp_msg.body or "",
            MIME=content_type, Data=data)
        self.send(attachment)
"""
EVENT = (
    "func uploadAttachment(ctx context.Context, client *whatsmeow.Client, "
    "attach *Attachment) (*waE2E.Message, error) {\n"
    "\tvar originalMIME = attach.MIME\n"
    "\tif attach.MIME == nativePackMIME {\n"
    "\t\treturn uploadNativeStickerPack(ctx, client, attach)\n\t}\n"
    "\tconvertAttachment(ctx, attach)\n}\n"
)
GATEWAY = """class Gateway:
    def __init__(self):
        super().__init__()
        self["xep_0030"].add_feature("urn:can:sticker-pack:0")
"""


def fixture(root):
    package = root / "slidge_whatsapp"
    package.mkdir()
    for name, source in (("mixins.py", MIXINS), ("event.go", EVENT), ("gateway.py", GATEWAY)):
        (package / name).write_text(source, encoding="utf-8")
    return package


class AudioModePatchTests(unittest.TestCase):
    def test_idempotent_complete_patch_with_helpers_and_early_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixture(root)
            self.assertTrue(patch_package(root))
            self.assertFalse(patch_package(root))
            event = (package / "event.go").read_text(encoding="utf-8")
            self.assertLess(
                event.index("uploadCANAudioAttachment"), event.index("convertAttachment")
            )
            self.assertIn(AUDIO_MODE_NS, (package / "gateway.py").read_text(encoding="utf-8"))
            self.assertTrue((package / "audio_modes.go").exists())
            self.assertTrue((package / "audio_modes.py").exists())

    def test_unknown_base_and_partial_patch_fail_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixture(root)
            for name, replacement in (
                ("event.go", "not v30"),
                ("mixins.py", MIXINS + "# from .audio_modes import"),
            ):
                path = package / name
                original = path.read_text(encoding="utf-8")
                path.write_text(replacement, encoding="utf-8")
                before = {p.name: p.read_bytes() for p in package.iterdir()}
                with self.assertRaises(SystemExit):
                    patch_package(root)
                self.assertEqual(before, {p.name: p.read_bytes() for p in package.iterdir()})
                path.write_text(original, encoding="utf-8")

    def test_drifted_helpers_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixture(root)
            patch_package(root)
            (package / "audio_modes.go").write_text("modified helper", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "helpers differ"):
                patch_package(root)


class AudioEnvelopeTests(unittest.TestCase):
    def test_url_fallback_not_used_as_caption_but_real_text_and_legacy_preserved(self):
        url = "https://upload.example.test/fixture.wav"
        thread = f"{AUDIO_MODE_NS}:document"
        self.assertEqual(audio_attachment_caption(thread, "  " + url + "  ", url), "")
        self.assertEqual(
            audio_attachment_caption(thread, "Fixture caption", url), "Fixture caption"
        )
        self.assertEqual(audio_attachment_caption(None, url, url), url)
        self.assertEqual(audio_attachment_caption(thread, None, url), "")

    def test_preserve_original_mime_and_legacy(self):
        for mode, mime in (
            ("audio", "audio/mpeg"),
            ("document", "audio/ogg; codecs=opus"),
            ("document", "audio/mp4"),
            ("document", "audio/flac"),
        ):
            self.assertEqual(
                audio_attachment_mime(f"{AUDIO_MODE_NS}:{mode}", mime),
                f"application/x-can-audio-mode;{mode};{mime}",
            )
        for mime in ("audio/ogg; codecs=opus", "image/webp", "application/x-can-sticker-pack"):
            self.assertEqual(audio_attachment_mime(None, mime), mime)
            self.assertEqual(audio_attachment_mime("urn:marco-ml:whatsapp:view-once:0", mime), mime)

    def test_bad_mode_conflicting_sticker_and_http_envelope_rejected(self):
        for thread, mime, sticker in (
            (f"{AUDIO_MODE_NS}:voice", "audio/mpeg", False),
            (f"{AUDIO_MODE_NS}:audio", "audio/wav", False),
            (f"{AUDIO_MODE_NS}:document", "application/pdf", False),
            (f"{AUDIO_MODE_NS}:document", "audio/ogg\r\nheader", False),
            (f"{AUDIO_MODE_NS}:audio", "audio/mpeg", True),
            (None, "application/x-can-audio-mode;document;audio/wav", False),
        ):
            with self.assertRaises(ValueError):
                audio_attachment_mime(thread, mime, is_sticker=sticker)


class PatchedAudioDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_patched_function_reads_bounded_chunks_and_preserves_bytes(self):
        await self.run_function(chunks=[b"orig", b"inal"], expected=b"original")

    async def test_chunked_or_declared_oversize_rejected_before_binding(self):
        await self.run_function(chunks=[b"1234", b"5678"], limit=7, expected_error=True)
        await self.run_function(chunks=[], declared=8, limit=7, expected_error=True)

    async def test_bad_intent_rejected_without_send(self):
        await self.run_function(chunks=[b"audio"], mode="voice", expected_error=True)

    async def run_function(
        self,
        *,
        chunks,
        expected=None,
        declared=None,
        limit=64,
        mode="document",
        expected_error=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = fixture(root)
            patch_package(root)
            tree = ast.parse((package / "mixins.py").read_text(encoding="utf-8"))
            tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
            namespace = dict(
                AUDIO_MODE_NS=AUDIO_MODE_NS,
                MAX_AUDIO_ATTACHMENT_BYTES=limit,
                audio_attachment_mime=audio_attachment_mime,
                audio_attachment_caption=audio_attachment_caption,
                XMPPError=XMPPError,
                SimpleNamespace=SimpleNamespace,
            )
            exec(compile(tree, "<patched-v30-fixture>", "exec"), namespace)

            class Content:
                async def iter_chunked(self, _size):
                    for chunk in chunks:
                        yield chunk

            unbounded_read = AsyncMock(side_effect=AssertionError("Unbounded read"))

            @asynccontextmanager
            async def get():
                yield SimpleNamespace(
                    status=200,
                    content_type="application/octet-stream",
                    content_length=declared,
                    content=Content(),
                    read=unbounded_read,
                )

            sender = SimpleNamespace(send=Mock())
            message = SimpleNamespace(
                thread=f"{AUDIO_MODE_NS}:{mode}",
                body="https://upload.example.test/fixture.wav",
                attachments=[
                    SimpleNamespace(
                        content_type="audio/wav",
                        is_sticker=False,
                        get=get,
                        url="https://upload.example.test/fixture.wav",
                    )
                ],
            )
            operation = namespace["RecipientMixin"]._on_file
            if expected_error:
                with self.assertRaises(XMPPError):
                    await operation(sender, message)
                sender.send.assert_not_called()
            else:
                await operation(sender, message)
                sent = sender.send.call_args.args[0]
                self.assertEqual(sent.Data, expected)
                self.assertEqual(sent.Caption, "")
                self.assertEqual(sent.MIME, "application/x-can-audio-mode;document;audio/wav")
            unbounded_read.assert_not_awaited()
