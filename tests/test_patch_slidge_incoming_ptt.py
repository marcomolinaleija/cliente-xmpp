from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_incoming_ptt import (
    NEW_AUDIO_CASE,
    OLD_AUDIO_CASE,
    OLD_CONVERT_DECLARATION,
    OLD_INCOMING_CONVERSION,
    OLD_INCOMING_CONVERSION_COMMENT,
    PATCH_MARKER,
    patch_event_go,
)


class IncomingPttPatchTests(unittest.TestCase):
    def test_incoming_ptt_keeps_original_payload_and_mime(self) -> None:
        source = (
            "package whatsapp\n\n"
            "func getMessageAttachments() {\n"
            "\tvar result []Attachment\n"
            + OLD_CONVERT_DECLARATION
            + OLD_AUDIO_CASE
            + "\n\t\ta.Data = data\n"
            + OLD_INCOMING_CONVERSION_COMMENT
            + OLD_INCOMING_CONVERSION
            + "}\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.go"
            path.write_text(source, encoding="utf-8")

            self.assertTrue(patch_event_go(path, backup=False))
            updated = path.read_text(encoding="utf-8")

            self.assertIn(NEW_AUDIO_CASE, updated)
            self.assertIn(PATCH_MARKER, updated)
            self.assertIn("a.MIME = msg.GetMimetype()", updated)
            self.assertIn("a.Data = data", updated)
            self.assertNotIn("convertSpec", updated)
            self.assertNotIn("media.Convert(ctx, a.Data", updated)
            self.assertNotIn("failed to convert incoming attachment", updated)
            self.assertFalse(patch_event_go(path, backup=False))

    def test_incompatible_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.go"
            path.write_text("package whatsapp\n", encoding="utf-8")

            with self.assertRaises(SystemExit):
                patch_event_go(path, backup=False)


if __name__ == "__main__":
    unittest.main()
