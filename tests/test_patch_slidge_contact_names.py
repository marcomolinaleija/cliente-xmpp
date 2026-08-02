from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_contact_names import (
    NEW_CONTACT_HANDLER,
    NEW_GET_CONTACTS,
    NEW_HISTORY_CONTACT,
    NEW_LIVE_CONTACT,
    NEW_ROSTER_SYNC,
    OLD_CONTACT_HANDLER,
    OLD_GET_CONTACTS,
    OLD_HISTORY_CONTACT,
    OLD_LIVE_CONTACT,
    OLD_ROSTER_SYNC,
    patch_event_go,
    patch_session_go,
    patch_session_py,
)


class ContactNamePatchTests(unittest.TestCase):
    def test_history_event_merges_saved_contact_info_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.go"
            path.write_text(
                "package whatsapp\n\n" + OLD_LIVE_CONTACT + "\n" + OLD_HISTORY_CONTACT,
                encoding="utf-8",
            )

            self.assertTrue(patch_event_go(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn(NEW_HISTORY_CONTACT, updated)
            self.assertIn(NEW_LIVE_CONTACT, updated)
            self.assertIn("storedContactInfo(ctx, client, actor, jid)", updated)
            self.assertIn("contactInfo.PushName = evt.GetPushname()", updated)
            self.assertIn("client.Store.Contacts.GetContact(ctx, jid)", updated)
            self.assertNotIn(OLD_LIVE_CONTACT, updated)
            self.assertNotIn(OLD_HISTORY_CONTACT, updated)
            self.assertFalse(patch_event_go(path, backup=False))

    def test_roster_sync_refreshes_whatsapp_names_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(
                OLD_ROSTER_SYNC + "\n" + OLD_CONTACT_HANDLER,
                encoding="utf-8",
            )

            self.assertTrue(patch_session_py(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn(NEW_ROSTER_SYNC, updated)
            self.assertIn("GetContacts(refresh=True)", updated)
            self.assertIn("add_whatsapp_contact(wa_contact)", updated)
            self.assertIn(NEW_CONTACT_HANDLER, updated)
            self.assertIn("__authoritative_saved_contacts", updated)
            self.assertIn("await asyncio.sleep(5)", updated)
            self.assertNotIn(OLD_ROSTER_SYNC, updated)
            self.assertNotIn(OLD_CONTACT_HANDLER, updated)
            self.assertFalse(patch_session_py(path, backup=False))

    def test_go_roster_prefers_saved_contact_variant_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.go"
            path.write_text(OLD_GET_CONTACTS, encoding="utf-8")

            self.assertTrue(patch_session_go(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn(NEW_GET_CONTACTS, updated)
            self.assertIn("preferContactCandidate", updated)
            self.assertIn("contactsByJID", updated)
            self.assertNotIn(OLD_GET_CONTACTS, updated)
            self.assertFalse(patch_session_go(path, backup=False))

    def test_incompatible_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.go"
            path.write_text("package whatsapp\n", encoding="utf-8")

            with self.assertRaises(SystemExit):
                patch_event_go(path, backup=False)


if __name__ == "__main__":
    unittest.main()
