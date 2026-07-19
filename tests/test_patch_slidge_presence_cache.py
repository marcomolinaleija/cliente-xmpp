from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_presence_cache import patch_contact

CONTACT_SOURCE = '''class Contact:
    async def update_info(self) -> None:
        # If we receive presences, the status will be updated accordingly. But presences do not
        # work reliably, and having contacts offline has annoying side effects.
        self.online()
'''


class PatchSlidgePresenceCacheTests(unittest.TestCase):
    def test_metadata_refresh_reuses_cached_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            contact_path = Path(temp_dir) / "contact.py"
            contact_path.write_text(CONTACT_SOURCE, encoding="utf-8")

            self.assertTrue(patch_contact(contact_path, backup=False))

            updated = contact_path.read_text(encoding="utf-8")
            self.assertIn(
                "self.send_last_presence(force=True, no_cache_online=True)",
                updated,
            )
            self.assertNotIn("        self.online()\n", updated)
            self.assertFalse(patch_contact(contact_path, backup=False))

    def test_unexpected_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            contact_path = Path(temp_dir) / "contact.py"
            contact_path.write_text("class Contact:\n    pass\n", encoding="utf-8")

            with self.assertRaises(SystemExit):
                patch_contact(contact_path, backup=False)


if __name__ == "__main__":
    unittest.main()
