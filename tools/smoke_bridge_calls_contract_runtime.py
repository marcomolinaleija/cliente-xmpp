from __future__ import annotations

from xml.etree import ElementTree as ET


def checkpoint(name: str) -> None:
    print(f"calls contract smoke: {name}", flush=True)

checkpoint("before session import")
from slidge_whatsapp import session as session_module  # noqa: E402, I001
checkpoint("after session import")
from slidge_whatsapp.generated import whatsapp  # noqa: E402, I001
checkpoint("after generated import")

package_dir = __import__("pathlib").Path(session_module.__file__).parent
for source_name, required in {
    "event.go": (
        "CallContract",
        "callContractTransportPrefix",
        "callContractTransport",
        "actor.LID = transport",
    ),
    "session.go": ("*events.CallOfferNotice", "*events.CallAccept", "*events.CallReject"),
    "session.py": ("CALL_TRANSPORT_PREFIX", "parse_call_metadata(call.Actor.LID)"),
}.items():
    source = (package_dir / source_name).read_text(encoding="utf-8")
    for marker in required:
        assert marker in source, f"{source_name} missing {marker}"

metadata = {
    "version": 1,
    "call_id": "opaque-call-id",
    "peer_jid": "peer@s.whatsapp.net",
    "group_jid": "group@g.us",
    "direction": "incoming",
    "kind": "voice",
    "state": "offered",
    "event_timestamp": "2026-08-31T12:34:56Z",
    "sequence": 1,
}
transport = "call-contract-v1:" + __import__("json").dumps(metadata)
checkpoint("before existing Actor.LID transport")
actor = whatsapp.Actor(LID=transport)  # type: ignore[no-untyped-call]
checkpoint("after existing Actor.LID setter")
call = whatsapp.Call(Actor=actor)  # type: ignore[no-untyped-call]
checkpoint("after existing Call.Actor constructor")
serialized_metadata = call.Actor.LID
checkpoint("after existing Actor.LID getter")
parsed = session_module.parse_call_metadata(serialized_metadata)
assert parsed == metadata
checkpoint("after metadata parser")
extension = session_module.make_call_extension(parsed)
checkpoint("after XML serializer")
assert extension.tag == "{urn:marco-ml:whatsapp:call:1}call"
assert extension.attrib == {
    "contract-version": "1",
    "call-id": "opaque-call-id",
    "direction": "incoming",
    "kind": "voice",
    "state": "offered",
    "sequence": "1",
    "peer-jid": "peer@s.whatsapp.net",
    "group-jid": "group@g.us",
    "event-timestamp": "2026-08-31T12:34:56Z",
}
assert session_module.parse_call_metadata("not-json") is None
assert session_module.parse_call_metadata("call-contract-v1:not-json") is None
assert ET.fromstring(ET.tostring(extension)).attrib["call-id"] == "opaque-call-id"
checkpoint("before completion")
print("calls contract runtime smoke: ok")
