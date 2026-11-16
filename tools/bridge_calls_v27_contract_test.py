from __future__ import annotations

import re
from importlib import import_module
from pathlib import Path


def has_history_call_route(source: str) -> bool:
    return bool(re.search(
        r'\bnewCallLogRecordEvent\((?:s\.)?ctx, client, record, "history_sync"\)', source,
    ))


session = import_module("slidge_whatsapp.session")
root = Path(session.__file__).parent
session_source = Path(session.__file__).read_text(encoding="utf-8")
event_source = (root / "event.go").read_text(encoding="utf-8")
go_session_source = (root / "session.go").read_text(encoding="utf-8")
inbound_path = root / "can_inbound.go"
go_message_source = go_session_source + (
    inbound_path.read_text(encoding="utf-8") if inbound_path.exists() else ""
)
payload_source = (
    root / "vendor/go.mau.fi/whatsmeow/store/clientpayload.go"
).read_text(encoding="utf-8")

required = (
    (session_source, '("outcome", "outcome")'),
    (session_source, '("source", "source")'),
    (session_source, 'attributes["duration-seconds"]'),
    (session_source, "self.bookmarks.by_legacy_id(group_jid)"),
    (event_source, "func newCallLogContract("),
    (event_source, "func newCallLogMessageEvent("),
    (event_source, "callRecordSequence = 4"),
    (go_session_source, 'newCallLogRecordEvent(s.ctx, client, record, "app_state")'),
    (go_message_source, "evt.Message.GetCallLogMesssage()"),
    (payload_source, "SupportCallLogHistory:                    proto.Bool(true)"),
    # Web version has its own native regression test; it is not a call capability.
)
for source, marker in required:
    if marker not in source:
        raise SystemExit(f"v27 call record marker missing: {marker}")

# Inbound dispatch moved history work to a helper that receives ctx explicitly.
if not has_history_call_route(go_session_source):
    raise SystemExit("v27 call record history routing missing")

metadata = {
    "version": 1,
    "call_id": "outgoing-call",
    "peer_jid": "contact@example.org",
    "chat_jid": "contact@whatsapp.example.test",
    "group_jid": "",
    "direction": "outgoing",
    "kind": "video",
    "state": "ended",
    "event_timestamp": "2026-08-31T12:34:56Z",
    "sequence": 4,
    "duration_seconds": 73,
    "outcome": "connected",
    "source": "app_state",
}
extension = session.make_call_extension(metadata)
expected = {
    "call-id": "outgoing-call",
    "chat-jid": "contact@whatsapp.example.test",
    "direction": "outgoing",
    "kind": "video",
    "state": "ended",
    "sequence": "4",
    "duration-seconds": "73",
    "outcome": "connected",
    "source": "app_state",
}
for name, value in expected.items():
    if extension.attrib.get(name) != value:
        raise SystemExit(
            f"v27 XML attribute {name!r} is {extension.attrib.get(name)!r}, expected {value!r}"
        )

print("calls v27 bridge contract: ok")
