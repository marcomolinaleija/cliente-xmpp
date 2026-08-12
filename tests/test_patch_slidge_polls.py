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
                "slidge/core/dispatcher/message/message.py": '''# consolidated marker\n# creator-lid\nawait recipient.on_poll_vote(message)\n''',
                "slidge_whatsapp/session.py": '''# consolidated marker\nf"{{{POLL_NAMESPACE}}}poll-update"\n''',
                "slidge_whatsapp/mixins.py": '''class RecipientMixin:\n    async def on_poll_vote(self, message):\n        pass\n''',
                "slidge_whatsapp/event.go": '''package whatsapp\n\nfunc setPollUpdateMessage() {}\n''',
                "slidge_whatsapp/session.go": '''package whatsapp\n\nfunc pollVoteMessageInfo() {}\n// BuildPollVote keeps native WhatsApp poll encryption.\n''',
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
