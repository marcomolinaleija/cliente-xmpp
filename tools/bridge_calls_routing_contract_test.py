from __future__ import annotations

from importlib import import_module
from pathlib import Path

session = import_module("slidge_whatsapp.session")
source = Path(session.__file__).read_text(encoding="utf-8")

required = (
    '("chat_jid", "chat-jid")',
    'canonical_chat_jid = str(contact.jid.bare).strip()',
    'metadata["chat_jid"] = canonical_chat_jid',
    'call.Actor.JID',
)
for marker in required:
    if marker not in source:
        raise SystemExit(f"routing bridge contract marker missing: {marker}")

metadata = {
    "version": 1,
    "call_id": "opaque-call-id",
    "peer_jid": "12345@lid",
    "chat_jid": "contact@whatsapp.example.test",
    "direction": "incoming",
    "kind": "voice",
    "state": "offered",
    "event_timestamp": "2026-08-31T12:34:56Z",
    "sequence": 1,
}
extension = session.make_call_extension(metadata)
if extension.attrib.get("peer-jid") != "12345@lid":
    raise SystemExit("bridge contract changed the WhatsApp peer identifier")
if extension.attrib.get("chat-jid") != "contact@whatsapp.example.test":
    raise SystemExit("bridge contract did not serialize the canonical XMPP route")
print("calls routing bridge contract: ok")