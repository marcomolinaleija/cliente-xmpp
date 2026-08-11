from __future__ import annotations

# ruff: noqa: E501, I001

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_polls import patch_package


class PollPatchTests(unittest.TestCase):
    def test_patch_is_idempotent_and_contains_vote_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = {
                "slidge/core/mixins/message_text.py": '''from collections.abc import Iterable\nfrom datetime import datetime\nfrom xml.etree import ElementTree as ET\n\ndef add_whatsapp_forwarded_flag(msg, is_forwarded): pass\n\nclass X:\n    def send_text(\n        self,\n        body: str,\n        legacy_msg_id: str | None = None,\n        *,\n        is_forwarded: bool = False,\n        **send_kwargs: object,\n    ):\n        msg = object()\n        add_whatsapp_forwarded_flag(msg, is_forwarded)\n        if correction:\n            pass\n''',
                "slidge/core/dispatcher/message/message.py": '''from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from slidge.util.types import AnyGateway\n\n\nclass X:\n    async def on_legacy_message(self, msg):\n        recipient, thread = await self._get_recipient_and_thread(msg)\n        replace = await self.__get_replace(msg, recipient)\n    def __get_xhtml_sticker_cid(self, msg: Message) -> str | None:\n        return None\n''',
                "slidge_whatsapp/session.py": '''from typing import Any, Concatenate, ParamSpec, TypeVar, cast\n\nRecipient = Contact | MUC\n\n\nclass Session:\n    async def on_wa_msg_poll(\n        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None\n    ) -> None:\n        body = f"🗳 {message.Poll.Title}"\n        for option in message.Poll.Options:\n            body = body + f"\\n☐ {option.Title}"\n        actor.send_text(\n            body=body,\n            legacy_msg_id=message.ID,\n            reply_to=await self.__get_reply_to(message, muc),\n            when=self.__get_timestamp(message),\n            carbon=message.Actor.IsMe,\n        )\n''',
                "slidge_whatsapp/mixins.py": '''class RecipientMixin:\n    async def _on_text(self, xmpp_msg: XMPPMessage) -> str:\n        pass\n''',
                "slidge_whatsapp/event.go": '''package whatsapp\n\nimport (\n\t"slices"\n\t"strings"\n)\n\nfunc f(p poll) {\n\t\tmessage.Kind = MessagePoll\n\t\tmessage.Poll = Poll{Title: p.GetName()}\n}\n''',
                "slidge_whatsapp/session.go": '''package whatsapp\n\nfunc (s *Session) SendMessage(message Message) error {\n\tswitch message.Kind {\n\tcase MessageReaction:\n\t\t// Send message as emoji reaction to a given message.\n\t}\n}\n\n// SendMessage processes the given Message and sends a WhatsApp message for the kind and contact JID\n''',
            }
            for relative, text in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")

            self.assertTrue(patch_package(root, backup=False))
            self.assertFalse(patch_package(root, backup=False))
            session_go = (root / "slidge_whatsapp/session.go").read_text(encoding="utf-8")
            dispatcher = (root / "slidge/core/dispatcher/message/message.py").read_text(encoding="utf-8")
            self.assertIn("BuildPollVote", session_go)
            self.assertIn("creator-lid", dispatcher)


if __name__ == "__main__":
    unittest.main()
