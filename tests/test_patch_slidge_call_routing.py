from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_call_routing_v2 import patch_session


class CallRoutingPatchTests(unittest.TestCase):
    def test_adds_optional_xmpp_route_without_changing_whatsapp_peer(self) -> None:
        source = '''
def make_call_extension(metadata):
    for source, target in (
        ("peer_jid", "peer-jid"),
        ("group_jid", "group-jid"),
        ("event_timestamp", "event-timestamp"),
    ):
        pass

class Session:
    async def on_wa_call(self, call, contact, metadata, message_kwargs):
        if metadata:
            message_kwargs["extra_xml"] = make_call_extension(metadata)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.py"
            path.write_text(source, encoding="utf-8")
            self.assertTrue(patch_session(path, backup=False))
            patched = path.read_text(encoding="utf-8")
            self.assertIn('("peer_jid", "peer-jid")', patched)
            self.assertIn('("chat_jid", "chat-jid")', patched)
            self.assertIn("contact.jid.bare", patched)
            self.assertFalse(patch_session(path, backup=False))


if __name__ == "__main__":
    unittest.main()