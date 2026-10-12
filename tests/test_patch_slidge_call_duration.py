from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# ruff: noqa: E501 -- exact native source fixture anchors
from tools.patch_slidge_call_duration_v33 import patch_package

EVENT_SOURCE = '''func newEventFromHistory(ctx context.Context, client *whatsmeow.Client, info *waWeb.WebMessageInfo) (EventKind, *EventPayload) {
    if j.Server != types.GroupServer { return EventUnknown, nil }
}
func direction() {
\tif !client.Store.LID.IsEmpty() && creator == client.Store.LID.ToNonAD() {
\t\treturn callDirectionOutgoing
\t}
\treturn callDirectionUnknown
}
func call() {
\tactor := newActor(ctx, client, meta.From, meta.CallCreator, meta.CallCreatorAlt)
}
'''
SESSION_SOURCE = '''func Login() {
\t\tgo s.syncCallLogAppState(client)
\t\treturn nil
}
func handleEvent() {
\tcase *events.Connected, *events.PushNameSetting:
}
'''


class CallDurationPatchTests(unittest.TestCase):
    def test_patch_is_idempotent_and_call_history_precedes_dm_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            (package / "event.go").write_text(EVENT_SOURCE, encoding="utf-8")
            (package / "session.go").write_text(SESSION_SOURCE, encoding="utf-8")
            self.assertTrue(patch_package(root))
            event = (package / "event.go").read_text(encoding="utf-8")
            session = (package / "session.go").read_text(encoding="utf-8")
            self.assertLess(event.index("canHistoricalCallEvent(ctx"), event.index("types.GroupServer"))
            self.assertIn("UnwrapRaw()", event)
            self.assertIn("client.ParseWebMessage(chat, info)", event)
            self.assertIn("canCallPeerMeta(client, meta, direction)", event)
            self.assertEqual(session.count("go s.syncCallLogAppState(client)"), 1)
            self.assertLess(session.index("case *events.Connected"),
                            session.index("go s.syncCallLogAppState(client)"))
            self.assertNotIn("go s.syncCallLogAppState", session.split("func handleEvent")[0])
            self.assertFalse(patch_package(root))
            self.assertEqual(event, (package / "event.go").read_text(encoding="utf-8"))

    def test_bad_session_anchor_does_not_partially_write_event_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            (package / "event.go").write_text(EVENT_SOURCE, encoding="utf-8")
            (package / "session.go").write_text("unknown source", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(EVENT_SOURCE, (package / "event.go").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
