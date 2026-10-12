from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_call_recovery_v33 import patch_package

# ruff: noqa: E501 -- exact native source fixture anchors
SOURCE = '''type Session struct {
}
func propagate() {
\tcallback := func() { s.eventHandler(kind, payload) }
\t\ts.client = nil
}
func handle() {
\t\tif _, connected := evt.(*events.Connected); connected {
}
\ts.gateway.logger.Debugf("Handling event '%T': %+v", evt, jsonStringer{evt})
    s.ctx, client, callStateOffered, callDirectionIncoming, callKindUnknown, 1, "", evt.BasicCallMeta,
}
'''


class CallRecoveryPatchTests(unittest.TestCase):
    def test_guarded_patch_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            session = package / "session.go"
            session.write_text(SOURCE, encoding="utf-8")
            self.assertTrue(patch_package(root))
            patched = session.read_text(encoding="utf-8")
            self.assertIn("canObserveCall(payload)", patched)
            self.assertIn("canCallOfferKind(evt.Data)", patched)
            self.assertFalse(patch_package(root))
            self.assertEqual(session.read_text(encoding="utf-8"), patched)
            helper = package / "can_call_recovery.go"
            original = helper.read_text(encoding="utf-8")
            helper.write_text(original + "\n", encoding="utf-8")
            self.assertFalse(patch_package(root))
            helper.write_text("unexpected", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(session.read_text(encoding="utf-8"), patched)

    def test_failed_anchor_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            session = package / "session.go"
            bad = SOURCE.replace("callKindUnknown", "other")
            session.write_text(bad, encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(session.read_text(encoding="utf-8"), bad)
            self.assertFalse((package / "can_call_recovery.go").exists())
