from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_presence_sources import patch_session

ORIGINAL = """from datetime import datetime

class Session:
    async def on_wa_chat_state(self, state):
        contact, _muc = await self.__get_contact_or_participant(state.Chat, state.Actor)
        if state.Kind == whatsapp.ChatStateComposing:
            contact.composing(media=state.Media)
            contact.online(last_seen=datetime.now())
        elif state.Kind == whatsapp.ChatStatePaused:
            contact.paused()

    async def on_wa_receipt(self, receipt):
        for message_id in receipt.MessageIDs:
            if receipt.Kind == whatsapp.ReceiptDelivered:
                contact.received(message_id)
            elif receipt.Kind == whatsapp.ReceiptRead:
                contact.displayed(legacy_msg_id=message_id, carbon=receipt.Actor.IsMe)
                contact.online(last_seen=datetime.now())
"""


class PresenceSourcePatchTests(unittest.TestCase):
    def test_only_presence_events_can_update_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(ORIGINAL, encoding="utf-8")

            self.assertTrue(patch_session(path, backup=False))
            patched = path.read_text(encoding="utf-8")

            self.assertNotIn("contact.online(last_seen=datetime.now())", patched)
            self.assertIn("contact.composing(media=state.Media)", patched)
            self.assertIn("contact.paused()", patched)
            self.assertIn(
                "contact.displayed(legacy_msg_id=message_id, carbon=receipt.Actor.IsMe)",
                patched,
            )
            self.assertFalse(patch_session(path, backup=False))


if __name__ == "__main__":
    unittest.main()
