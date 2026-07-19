from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_roster_sync import (
    CONTACT_HELPERS,
    NEW_ROSTER,
    OLD_ROSTER,
    SESSION_CONNECT_NEW,
    SESSION_CONNECT_OLD,
    SESSION_INIT_NEW,
    SESSION_INIT_OLD,
    patch_contact,
    patch_session,
)


class RosterSyncPatchTests(unittest.TestCase):
    def test_patches_contact_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contact.py"
            path.write_text(
                'if TYPE_CHECKING:\n    from .session import Session\n\n' + OLD_ROSTER,
                encoding="utf-8",
            )

            self.assertTrue(patch_contact(path))
            patched = path.read_text(encoding="utf-8")
            self.assertIn(CONTACT_HELPERS.strip(), patched)
            self.assertIn(NEW_ROSTER, patched)
            self.assertNotIn(OLD_ROSTER, patched)
            self.assertFalse(patch_contact(path))

    def test_patches_session_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(
                SESSION_INIT_OLD + "\n" + SESSION_CONNECT_OLD,
                encoding="utf-8",
            )

            self.assertTrue(patch_session(path))
            patched = path.read_text(encoding="utf-8")
            self.assertIn(SESSION_INIT_NEW, patched)
            self.assertIn(SESSION_CONNECT_NEW, patched)
            self.assertFalse(patch_session(path))


if __name__ == "__main__":
    unittest.main()
