from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_sticker_accessibility import patch_package

EVENT_SOURCE = '''import (
\t// Standard library.
\t"context"
\t"encoding/hex"
\t"strings"
)

// GetMessageAttachments fetches and decrypts attachments (images, audio, video, or documents) sent
func getMessageAttachments() {
\t\tcase *waE2E.StickerMessage:
\t\t\ta.MIME = msg.GetMimetype()
\t\t\tinfo = msg.GetContextInfo()

\t\ta.Data = data

\t\t// Set filename from SHA256 checksum and MIME type, if none is already set.
}
'''


class StickerAccessibilityPatchTests(unittest.TestCase):
    def test_patch_preserves_descriptions_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event_go = Path(temp_dir) / "slidge_whatsapp/event.go"
            event_go.parent.mkdir()
            event_go.write_text(EVENT_SOURCE, encoding="utf-8")

            session = event_go.with_name("session.py")
            session.write_text(
                '''class Attachment(LegacyAttachment):
    @staticmethod
    async def convert(wa_attachment, muc=None):
        return Attachment(
            content_type=wa_attachment.MIME,
            data=bytes(wa_attachment.Data),
            caption=(
                wa_attachment.Caption
                if muc is None
                else await muc.replace_mentions(wa_attachment.Caption)
            ),
            name=wa_attachment.Filename,
        )
''',
                encoding="utf-8",
            )
            core_attachment = Path(temp_dir) / "slidge/core/mixins/attachment.py"
            core_attachment.parent.mkdir(parents=True)
            core_attachment.write_text(
                '''        msgs = self.__send_url(
            msg,
            legacy_msg_id,
            uploaded_url=new_url,
            caption=attachment.caption,
''',
                encoding="utf-8",
            )

            self.assertTrue(patch_package(Path(temp_dir), backup=False))
            self.assertFalse(patch_package(Path(temp_dir), backup=False))

            patched = event_go.read_text(encoding="utf-8")
            self.assertIn('"archive/zip"', patched)
            self.assertIn("msg.GetAccessibilityLabel()", patched)
            self.assertIn("msg.GetEmojis()", patched)
            self.assertIn("lottieStickerAccessibilityCaption(data)", patched)
            self.assertIn("io.LimitReader", patched)
            self.assertIn("stickerCaptionMarker", patched)
            self.assertIn(
                "is_sticker = caption.startswith(STICKER_CAPTION_MARKER)",
                session.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "caption=None if attachment.is_sticker else attachment.caption",
                core_attachment.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
