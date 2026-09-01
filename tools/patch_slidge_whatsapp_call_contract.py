from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not patch {description}: expected one match, found {count}."
        )
    return text.replace(old, new, 1)


def replace_region(text: str, start_marker: str, end_marker: str, new: str, description: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0 or text.find(start_marker, start + 1) >= 0:
        raise SystemExit(f"Could not patch {description}: source layout is not v25.")
    return text[:start] + new + text[end:]


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "callContractTransportPrefix" in source:
        return False

    replacement = r'''// CallState represents the legacy fallback state used by the Python adapter.
type CallState int

const (
	CallUnknown CallState = iota
	CallIncoming
	CallMissed
)

const (
	callContractVersion = 1
	callContractTransportPrefix = "call-contract-v1:"
	callDirectionIncoming = "incoming"
	callDirectionOutgoing = "outgoing"
	callDirectionUnknown  = "unknown"
	callKindUnknown       = "unknown"
	callKindVoice         = "voice"
	callKindVideo         = "video"
	callStateOffered      = "offered"
	callStateAccepted     = "accepted"
	callStateMissed       = "missed"
	callStateRejected     = "rejected"
	callStateEnded        = "ended"
)

// CallContract is the versioned, lossless-as-available call metadata transported to Python.
// Empty optional fields mean that WhatsApp did not provide that fact.
type CallContract struct {
	Version         int    `json:"version"`
	CallID          string `json:"call_id"`
	PeerJID         string `json:"peer_jid"`
	GroupJID        string `json:"group_jid,omitempty"`
	Direction       string `json:"direction"`
	Kind            string `json:"kind"`
	State           string `json:"state"`
	EventTimestamp  string `json:"event_timestamp,omitempty"`
	AnsweredAt      string `json:"answered_at,omitempty"`
	EndedAt         string `json:"ended_at,omitempty"`
	TerminalReason  string `json:"terminal_reason,omitempty"`
	Sequence        int    `json:"sequence"`
}

// A Call represents a WhatsApp call notification. The accessible text fallback remains
// available when the upstream notification has no CallID.
type Call struct {
	State     CallState
	Actor     Actor
	Timestamp int64
}

func callJIDString(jid types.JID) string {
	if jid.IsEmpty() {
		return ""
	}
	return jid.String()
}

func callTimestamp(timestamp time.Time) string {
	if timestamp.IsZero() {
		return ""
	}
	return timestamp.UTC().Format(time.RFC3339Nano)
}

func callStateFromTerminateReason(reason string) string {
	if reason == "" || reason == "timeout" {
		return callStateMissed
	}
	// WhatsMeow has already classified this as a termination; preserve its reason separately.
	return callStateEnded
}

func callKindFromMedia(media string) string {
	switch media {
	case "audio":
		return callKindVoice
	case "video":
		return callKindVideo
	default:
		return callKindUnknown
	}
}

func callDirectionFromMeta(client *whatsmeow.Client, meta types.BasicCallMeta) string {
	if client == nil || client.Store == nil || meta.CallCreator.IsEmpty() {
		return callDirectionUnknown
	}
	creator := meta.CallCreator.ToNonAD()
	if client.Store.ID != nil && creator == client.Store.ID.ToNonAD() {
		return callDirectionOutgoing
	}
	if !client.Store.LID.IsEmpty() && creator == client.Store.LID.ToNonAD() {
		return callDirectionOutgoing
	}
	return callDirectionUnknown
}

func newCallContract(
	state, direction, kind string,
	sequence int,
	terminalReason string,
	meta types.BasicCallMeta,
) *CallContract {
	callID := strings.TrimSpace(meta.CallID)
	if callID == "" {
		return nil
	}
	contract := &CallContract{
		Version:        callContractVersion,
		CallID:         callID,
		PeerJID:        callJIDString(meta.From),
		GroupJID:       callJIDString(meta.GroupJID),
		Direction:      direction,
		Kind:           kind,
		State:          state,
		EventTimestamp: callTimestamp(meta.Timestamp),
		Sequence:       sequence,
	}
	switch state {
	case callStateAccepted:
		contract.AnsweredAt = contract.EventTimestamp
	case callStateMissed, callStateRejected, callStateEnded:
		contract.EndedAt = contract.EventTimestamp
		contract.TerminalReason = strings.TrimSpace(terminalReason)
	}
	return contract
}

func callContractTransport(contract *CallContract) string {
	if contract == nil {
		return ""
	}
	payload, err := json.Marshal(contract)
	if err != nil {
		return ""
	}
	return callContractTransportPrefix + string(payload)
}

func legacyCallState(state string) CallState {
	switch state {
	case callStateOffered:
		return CallIncoming
	case callStateMissed:
		return CallMissed
	default:
		return CallUnknown
	}
}

// newCallEvent transports only facts provided by the WhatsApp event. Sequence is the stable
// contract phase (offer=1, accept=2, terminal=3), so consumers deduplicate by call_id+sequence.
func newCallEvent(
	ctx context.Context,
	client *whatsmeow.Client,
	state, direction, kind string,
	sequence int,
	terminalReason string,
	meta types.BasicCallMeta,
) (EventKind, *EventPayload) {
	actor := newActor(ctx, client, meta.From, meta.CallCreator, meta.CallCreatorAlt)
	if transport := callContractTransport(newCallContract(state, direction, kind, sequence, terminalReason, meta)); transport != "" {
		// Actor.LID already crosses the generated v25 gopy ABI as an owned string.
		// Call events do not consume it in Python; retain contract metadata there rather
		// than adding a manually maintained C getter for a new Go field.
		actor.LID = transport
	}
	return EventCall, &EventPayload{Call: Call{
		State:     legacyCallState(state),
		Actor:     actor,
		Timestamp: meta.Timestamp.Unix(),
	}}
}

'''
    source = replace_region(
        source,
        "// CallState represents the state of the call to synchronize with.",
        "// NewActor returns a concrete [Actor]",
        replacement,
        "call contract event model",
    )
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-contract"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_session_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "case *events.CallAccept:" in source:
        return False
    source = replace_once(
        source,
        '''\tcase *events.CallOffer:
\t\ts.propagateEvent(newCallEvent(s.ctx, client, CallIncoming, evt.BasicCallMeta))
\tcase *events.CallTerminate:
\t\ts.propagateEvent(newCallEvent(s.ctx, client, callStateFromReason(evt.Reason), evt.BasicCallMeta))
''',
        '''\tcase *events.CallOffer:
\t\ts.propagateEvent(newCallEvent(
\t\t\ts.ctx, client, callStateOffered, callDirectionIncoming, callKindUnknown, 1, "", evt.BasicCallMeta,
\t\t))
\tcase *events.CallOfferNotice:
\t\ts.propagateEvent(newCallEvent(
\t\t\ts.ctx, client, callStateOffered, callDirectionIncoming, callKindFromMedia(evt.Media), 1, "", evt.BasicCallMeta,
\t\t))
\tcase *events.CallAccept:
\t\ts.propagateEvent(newCallEvent(
\t\t\ts.ctx, client, callStateAccepted, callDirectionFromMeta(client, evt.BasicCallMeta), callKindUnknown, 2, "", evt.BasicCallMeta,
\t\t))
\tcase *events.CallReject:
\t\ts.propagateEvent(newCallEvent(
\t\t\ts.ctx, client, callStateRejected, callDirectionOutgoing, callKindUnknown, 3, "", evt.BasicCallMeta,
\t\t))
\tcase *events.CallTerminate:
\t\ts.propagateEvent(newCallEvent(
\t\t\ts.ctx, client, callStateFromTerminateReason(evt.Reason), callDirectionFromMeta(client, evt.BasicCallMeta), callKindUnknown, 3, evt.Reason, evt.BasicCallMeta,
\t\t))
''',
        "call event dispatch",
    )
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-contract"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_session_python(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if 'CALL_NAMESPACE = "urn:marco-ml:whatsapp:call:1"' in source:
        return False
    source = replace_once(
        source,
        "import asyncio\n",
        "import asyncio\nimport json\n",
        "JSON import",
    )
    source = replace_once(
        source,
        'POLL_NAMESPACE = "urn:marco-ml:whatsapp:poll:0"\n',
        '''POLL_NAMESPACE = "urn:marco-ml:whatsapp:poll:0"
CALL_NAMESPACE = "urn:marco-ml:whatsapp:call:1"
CALL_CONTRACT_VERSION = 1
CALL_TRANSPORT_PREFIX = "call-contract-v1:"
_CALL_FALLBACK_LABELS = {
    "offered": "Incoming call",
    "accepted": "Call accepted",
    "missed": "Missed call",
    "rejected": "Call rejected",
    "ended": "Call ended",
    "failed": "Call failed",
}


def parse_call_metadata(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str) or not raw.startswith(CALL_TRANSPORT_PREFIX):
        return None
    try:
        metadata = json.loads(raw.removeprefix(CALL_TRANSPORT_PREFIX))
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("version") != CALL_CONTRACT_VERSION:
        return None
    if not isinstance(metadata.get("call_id"), str) or not metadata["call_id"]:
        return None
    if not isinstance(metadata.get("sequence"), int) or metadata["sequence"] < 1:
        return None
    return metadata


def make_call_extension(metadata: dict[str, object]) -> ET.Element:
    attributes = {
        "contract-version": str(CALL_CONTRACT_VERSION),
        "call-id": str(metadata["call_id"]),
        "direction": str(metadata.get("direction") or "unknown"),
        "kind": str(metadata.get("kind") or "unknown"),
        "state": str(metadata.get("state") or "unknown"),
        "sequence": str(metadata["sequence"]),
    }
    for source, target in (
        ("peer_jid", "peer-jid"),
        ("group_jid", "group-jid"),
        ("event_timestamp", "event-timestamp"),
        ("answered_at", "answered-at"),
        ("ended_at", "ended-at"),
        ("terminal_reason", "terminal-reason"),
    ):
        value = metadata.get(source)
        if isinstance(value, str) and value:
            attributes[target] = value
    return ET.Element(f"{{{CALL_NAMESPACE}}}call", attributes)


''',
        "call contract helpers",
    )
    source = replace_once(
        source,
        '''    async def on_wa_call(self, call: whatsapp.Call) -> None:
        if not call.Actor.JID:
            warnings.warn(f"Ignoring a call: {call}")
            return
        contact = await self.contacts.by_legacy_id(call.Actor.JID)
        text = f"from {contact.name or 'tel:' + str(contact.jid.local)} (xmpp:{contact.jid.bare})"
        if call.State == whatsapp.CallIncoming:
            text = "Incoming call " + text
        elif call.State == whatsapp.CallMissed:
            text = "Missed call " + text
        else:
            text = "Call " + text
        if call.Timestamp > 0:
            call_at = datetime.fromtimestamp(call.Timestamp, tz=UTC)
            text = text + f" at {call_at}"
        self.send_gateway_message(text)
''',
        '''    async def on_wa_call(self, call: whatsapp.Call) -> None:
        if not call.Actor.JID:
            warnings.warn("Ignoring a call without a resolvable peer")
            return
        contact = await self.contacts.by_legacy_id(call.Actor.JID)
        metadata = parse_call_metadata(call.Actor.LID)
        state = str(metadata.get("state")) if metadata else ""
        text = _CALL_FALLBACK_LABELS.get(state, "Call")
        if not metadata:
            if call.State == whatsapp.CallIncoming:
                text = "Incoming call"
            elif call.State == whatsapp.CallMissed:
                text = "Missed call"
        text += f" from {contact.name or 'tel:' + str(contact.jid.local)} (xmpp:{contact.jid.bare})"
        call_at: datetime | None = None
        timestamp = metadata.get("event_timestamp") if metadata else None
        if isinstance(timestamp, str) and timestamp:
            try:
                call_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                call_at = None
        elif call.Timestamp > 0:
            call_at = datetime.fromtimestamp(call.Timestamp, tz=UTC)
        if call_at is not None:
            text += f" at {call_at.astimezone(UTC)}"
        message_kwargs: dict[str, object] = {}
        if call_at is not None:
            message_kwargs["when"] = call_at
        if metadata:
            message_kwargs["extra_xml"] = make_call_extension(metadata)
        self.send_gateway_message(text, **message_kwargs)
''',
        "call XMPP emission",
    )
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-contract"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_package(site_packages: Path, *, backup: bool) -> bool:
    site_packages = site_packages.resolve()
    paths = (
        site_packages / "slidge_whatsapp/event.go",
        site_packages / "slidge_whatsapp/session.go",
        site_packages / "slidge_whatsapp/session.py",
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changes = (
        patch_event_go(paths[0], backup=backup),
        patch_session_go(paths[1], backup=backup),
        patch_session_python(paths[2], backup=backup),
    )
    return any(changes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the v1 structured XMPP call contract to the v25 bridge."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print("Call contract patch applied." if changed else "Call contract patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
