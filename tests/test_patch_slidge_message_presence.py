from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_message_presence import patch_session

ORIGINAL = """from datetime import datetime

class Session:
    async def on_wa_message(self, message):
        actor, muc = await self.__get_contact_or_participant(
            message.Chat, message.Actor
        )
        actor.online(last_seen=datetime.now())
        match message.Kind:
            case whatsapp.MessagePlain:
                await self.on_wa_msg_plain(message, actor, muc)
"""


class MessagePresencePatchTests(unittest.TestCase):
    def test_message_activity_does_not_update_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(ORIGINAL, encoding="utf-8")

            self.assertTrue(patch_session(path, backup=False))
            patched = path.read_text(encoding="utf-8")

            self.assertNotIn("actor.online(last_seen=datetime.now())", patched)
            self.assertIn("message.Chat, message.Actor", patched)
            self.assertIn("match message.Kind:", patched)
            self.assertFalse(patch_session(path, backup=False))


if __name__ == "__main__":
    unittest.main()
