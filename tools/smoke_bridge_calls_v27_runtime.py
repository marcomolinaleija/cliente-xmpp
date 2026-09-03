from __future__ import annotations

import json
from importlib import import_module
from xml.etree import ElementTree as ET

session = import_module("slidge_whatsapp.session")
whatsapp = import_module("slidge_whatsapp.generated.whatsapp")

metadata = {
    "version": 1,
    "call_id": "runtime-call",
    "peer_jid": "contact@example.org",
    "chat_jid": "contact@whatsapp.example.test",
    "direction": "outgoing",
    "kind": "voice",
    "state": "ended",
    "event_timestamp": "2026-08-31T12:34:56Z",
    "sequence": 4,
    "duration_seconds": 19,
    "outcome": "connected",
    "source": "history_sync",
}
actor = whatsapp.Actor()
actor.JID = metadata["peer_jid"]
actor.LID = session.CALL_TRANSPORT_PREFIX + json.dumps(metadata)
call = whatsapp.Call()
call.Actor = actor
call.Timestamp = 1788179696

parsed = session.parse_call_metadata(call.Actor.LID)
if parsed != metadata:
    raise SystemExit(f"generated ABI changed call metadata: {parsed!r}")
attributes = ET.fromstring(
    ET.tostring(session.make_call_extension(parsed))
).attrib
for name, value in {
    "duration-seconds": "19",
    "outcome": "connected",
    "source": "history_sync",
    "direction": "outgoing",
}.items():
    if attributes.get(name) != value:
        raise SystemExit(f"runtime serializer lost {name}: {attributes!r}")

print("calls v27 bridge runtime smoke: ok")
