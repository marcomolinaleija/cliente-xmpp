from __future__ import annotations

# ruff: noqa: E501, I001

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_stickers import patch_package


class NativeStickerPatchTests(unittest.TestCase):
    def test_patch_preserves_sticker_intent_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mixins = root / "slidge_whatsapp/mixins.py"
            event_go = root / "slidge_whatsapp/event.go"
            mixins.parent.mkdir(parents=True)
            mixins.write_text(
                '''message_attachment = whatsapp.Attachment(\n            MIME=content_type,\n            Filename=basename(att.url),\n            Data=go.Slice_byte.from_bytes(data),  # type:ignore[no-untyped-call]\n            Caption=xmpp_msg.body or "",\n            ViewOnce=xmpp_msg.thread == VIEW_ONCE_THREAD,\n)\n''',
                encoding="utf-8",
            )
            event_go.write_text(
                '''var knownMediaTypes = map[string]whatsmeow.MediaType{\n}\n\n// UploadAttachment attempts to push the given attachment data to WhatsApp according to the MIME\nfunc uploadAttachment(ctx context.Context, client *whatsmeow.Client, attach *Attachment) (*waE2E.Message, error) {\n\tvar originalMIME = attach.MIME\n}\n''',
                encoding="utf-8",
            )

            self.assertTrue(patch_package(root, backup=False))
            self.assertFalse(patch_package(root, backup=False))
            self.assertIn(
                "application/x-whatsapp-can-sticker",
                mixins.read_text(encoding="utf-8"),
            )
            patched_go = event_go.read_text(encoding="utf-8")
            self.assertIn("func uploadStickerAttachment", patched_go)
            self.assertIn("StickerMessage", patched_go)


if __name__ == "__main__":
    unittest.main()
