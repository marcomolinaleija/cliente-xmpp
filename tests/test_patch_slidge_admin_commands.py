from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_admin_commands import patch_package

MIXINS_SOURCE = '''from .deepgram_transcription import handle_transcription_command

class RecipientMixin:
    async def _on_text(self, xmpp_msg: XMPPMessage) -> str:
        assert xmpp_msg.body
        command_response = await handle_transcription_command(
            self.session.http,
            str(self.session.user_jid.bare),
            xmpp_msg.body,
        )
        if command_response is not None:
            local_sender = getattr(self, "send_text", None)
            if local_sender is not None:
                local_sender(command_response)
            else:
                participant = await self.get_user_participant()
                participant.send_text(command_response)
            return self.wa.GenerateMessageID()  # type:ignore[no-untyped-call,no-any-return]
        message_id: str = self.wa.GenerateMessageID()  # type:ignore[no-untyped-call]
        return message_id
'''

SESSION_SOURCE = '''class Session:
    async def on_wa_msg_attachment(self, message, actor, muc):
        account = str(self.user_jid.bare)
        for dedupe_key, data, content_type, filename in transcription_jobs:
            if data and transcription_enabled(account):
                self.create_task(transcribe_audio(data))
'''


class AdminCommandPatchTests(unittest.TestCase):
    def test_patch_redirects_command_responses_to_gateway_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir)
            package = site_packages / "slidge_whatsapp"
            package.mkdir()
            mixins_path = package / "mixins.py"
            session_path = package / "session.py"
            mixins_path.write_text(MIXINS_SOURCE, encoding="utf-8")
            session_path.write_text(SESSION_SOURCE, encoding="utf-8")

            self.assertTrue(patch_package(site_packages, backup=False))
            self.assertFalse(patch_package(site_packages, backup=False))

            mixins = mixins_path.read_text(encoding="utf-8")
            self.assertIn(
                "self.session.send_gateway_message(command_response)", mixins
            )
            self.assertNotIn("local_sender", mixins)
            self.assertNotIn("get_user_participant", mixins)
            self.assertIn("handle_transcription_command", mixins)
            self.assertIn("chat=str(self.jid.bare)", mixins)
            self.assertLess(
                mixins.index("handle_transcription_command("),
                mixins.index("message_id: str = self.wa.GenerateMessageID()"),
            )

            session = session_path.read_text(encoding="utf-8")
            self.assertIn(
                "muc.jid.bare if muc is not None else actor.jid.bare", session
            )
            self.assertIn("transcription_enabled(account, chat)", session)


if __name__ == "__main__":
    unittest.main()
