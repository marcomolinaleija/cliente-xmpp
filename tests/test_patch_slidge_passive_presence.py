from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_passive_presence import (
    ACTIVE_SEND,
    MARKER,
    OLD_SEND_PRESENCE,
    patch_session_go,
)

SESSION_V21 = f'''package whatsapp

func connectOne(s *Session, client *whatsmeow.Client) error {{
	return {ACTIVE_SEND}
}}

func connectTwo(s *Session, client *whatsmeow.Client) error {{
	return {ACTIVE_SEND}
}}

{OLD_SEND_PRESENCE}
'''


class PassivePresencePatchTests(unittest.TestCase):
    def test_all_v21_presence_paths_become_passive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.go"
            path.write_text(SESSION_V21, encoding="utf-8")

            self.assertTrue(patch_session_go(path, backup=False))
            patched = path.read_text(encoding="utf-8")

            self.assertIn(MARKER, patched)
            self.assertNotIn(ACTIVE_SEND, patched)
            self.assertEqual(patched.count("passiveWhatsAppPresence("), 4)
            self.assertIn("return types.PresenceUnavailable", patched)
            self.assertIn("s.queuePresenceRefresh(presence)", patched)
            self.assertIn("client.SetStatusMessage", patched)
            self.assertFalse(patch_session_go(path, backup=False))

    def test_refuses_an_unexpected_presence_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.go"
            path.write_text(OLD_SEND_PRESENCE, encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "Expected three active presence sends"):
                patch_session_go(path, backup=False)


if __name__ == "__main__":
    unittest.main()
