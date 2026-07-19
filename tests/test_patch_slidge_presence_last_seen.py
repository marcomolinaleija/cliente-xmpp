from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_presence_last_seen import patch_contact

ORIGINAL = """from datetime import UTC, datetime

class Contact:
    async def update_presence(self, presence, last_seen_timestamp):
        last_seen = (
            datetime.fromtimestamp(last_seen_timestamp, tz=UTC)
            if last_seen_timestamp > 0
            else None
        )
        self.away(last_seen=last_seen)
"""


class PresenceLastSeenPatchTests(unittest.TestCase):
    def test_incomplete_presence_reuses_cached_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contact.py"
            path.write_text(ORIGINAL, encoding="utf-8")

            self.assertTrue(patch_contact(path, backup=False))

            patched = path.read_text(encoding="utf-8")
            self.assertIn("cached_presence = self._get_last_presence()", patched)
            self.assertIn("cached_presence.last_seen", patched)
            self.assertFalse(patch_contact(path, backup=False))


if __name__ == "__main__":
    unittest.main()
