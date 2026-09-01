from __future__ import annotations

from importlib import import_module
from pathlib import Path
from xml.etree import ElementTree as ET

session = import_module("slidge_whatsapp.session")
source = Path(session.__file__).read_text(encoding="utf-8")
for marker in (
    '("chat_jid", "chat-jid")',
    'canonical_chat_jid = str(contact.jid.bare).strip()',
    'metadata["chat_jid"] = canonical_chat_jid',
):
    if marker not in source:
        raise SystemExit(f"missing deployed v2 routing marker: {marker}")

metadata = {
    "version": 1,
    "call_id": "smoke-call",
    "peer_jid": "opaque-lid@lid",
    "chat_jid": "contact@whatsapp.example.test",
    "direction": "incoming",
    "kind": "unknown",
    "state": "offered",
    "event_timestamp": "2026-08-31T12:34:56Z",
    "sequence": 1,
}
extension = session.make_call_extension(metadata)
attributes = ET.fromstring(ET.tostring(extension)).attrib
if attributes.get("peer-jid") != metadata["peer_jid"]:
    raise SystemExit("peer-jid was not preserved through the v2 runtime serializer")
if attributes.get("chat-jid") != metadata["chat_jid"]:
    raise SystemExit("chat-jid was not preserved through the v2 runtime serializer")
if attributes.get("contract-version") != "1":
    raise SystemExit("v2 routing must remain compatible with v1 consumers")
print("calls routing runtime smoke: ok")