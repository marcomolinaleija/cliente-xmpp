from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_transcription import patch_package

SESSION_SOURCE = '''from .group import MUC, Bookmarks, Participant

class Session:
    async def on_wa_msg_attachment(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        attachments = await Attachment.convert_list(message.Attachments, muc)
        await actor.send_files(
            attachments=attachments,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            is_forwarded=message.IsForwarded,
        )

    async def on_wa_msg_edit(
        self,
    ) -> None:
        pass
'''


MIXINS_SOURCE = '''from .generated import go, whatsapp

class RecipientMixin:
    async def _on_text(self, xmpp_msg: XMPPMessage) -> str:
        assert xmpp_msg.body
        message_id: str = self.wa.GenerateMessageID()  # type:ignore[no-untyped-call]
        return message_id
'''


class TranscriptionPatchTests(unittest.TestCase):
    def test_patch_adds_audio_pipeline_and_local_commands_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            site_packages = Path(temp_dir)
            package = site_packages / "slidge_whatsapp"
            package.mkdir()
            session_path = package / "session.py"
            mixins_path = package / "mixins.py"
            session_path.write_text(SESSION_SOURCE, encoding="utf-8")
            mixins_path.write_text(MIXINS_SOURCE, encoding="utf-8")

            self.assertTrue(patch_package(site_packages, backup=False))
            self.assertFalse(patch_package(site_packages, backup=False))

            session = session_path.read_text(encoding="utf-8")
            mixins = mixins_path.read_text(encoding="utf-8")
            self.assertIn("Skipping duplicate audio transcription", session)
            self.assertIn("inspect_audio", session)
            self.assertIn("format_audio_duration", session)
            self.assertIn("handle_transcription_command", mixins)
            command_position = mixins.index("handle_transcription_command(")
            whatsapp_position = mixins.index("message_id: str = self.wa.GenerateMessageID()")
            self.assertLess(command_position, whatsapp_position)
            self.assertIn("return self.wa.GenerateMessageID()", mixins)
            self.assertIn("await self.get_user_participant()", mixins)


if __name__ == "__main__":
    unittest.main()
