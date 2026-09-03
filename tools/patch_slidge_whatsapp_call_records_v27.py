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


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "newCallLogRecordEvent" in source:
        return False

    source = replace_once(
        source,
        '''func callStateFromTerminateReason(reason string) string {
\tif reason == "" || reason == "timeout" {
\t\treturn callStateMissed
\t}
\t// WhatsMeow has already classified this as a termination; preserve its reason separately.
\treturn callStateEnded
}
''',
        '''func callStateFromTerminateReason(reason string) string {
\tif reason == "accepted_elsewhere" {
\t\t// Another linked device answered; WhatsApp's terminate signal is not terminal.
\t\treturn callStateAccepted
\t}
\tif reason == "" || reason == "timeout" {
\t\treturn callStateMissed
\t}
\t// WhatsMeow has already classified this as a termination; preserve its reason separately.
\treturn callStateEnded
}

func callSequenceFromTerminateReason(reason string) int {
\tif reason == "accepted_elsewhere" {
\t\treturn 2
\t}
\treturn 3
}
''',
        "accepted elsewhere call state",
    )

    source = replace_once(
        source,
        '"go.mau.fi/whatsmeow/proto/waHistorySync"\n',
        '"go.mau.fi/whatsmeow/proto/waHistorySync"\n'
        '\t"go.mau.fi/whatsmeow/proto/waSyncAction"\n',
        "call log protobuf import",
    )
    source = replace_once(
        source,
        '''\tTerminalReason string `json:"terminal_reason,omitempty"`
\tSequence       int    `json:"sequence"`
''',
        '''\tTerminalReason  string  `json:"terminal_reason,omitempty"`
\tSequence        int     `json:"sequence"`
\tDurationSeconds *int64  `json:"duration_seconds,omitempty"`
\tOutcome         string  `json:"outcome,omitempty"`
\tSource          string  `json:"source,omitempty"`
''',
        "authoritative call contract fields",
    )
    insertion = r'''
const callRecordSequence = 4

func callLogJID(raw string) types.JID {
	jid, err := types.ParseJID(strings.TrimSpace(raw))
	if err != nil {
		return types.JID{}
	}
	return jid.ToNonAD()
}

func isOwnCallJID(client *whatsmeow.Client, jid types.JID) bool {
	if client == nil || client.Store == nil || jid.IsEmpty() {
		return false
	}
	jid = jid.ToNonAD()
	return jid == client.Store.GetJID().ToNonAD() || jid == client.Store.GetLID().ToNonAD()
}

func callLogPeer(client *whatsmeow.Client, record *waSyncAction.CallLogRecord) types.JID {
	if record == nil {
		return types.JID{}
	}
	creator := callLogJID(record.GetCallCreatorJID())
	if record.IsIncoming != nil && record.GetIsIncoming() &&
		!creator.IsEmpty() && !isOwnCallJID(client, creator) {
		return creator
	}
	for _, participant := range record.GetParticipants() {
		jid := callLogJID(participant.GetUserJID())
		if !jid.IsEmpty() && !isOwnCallJID(client, jid) {
			return jid
		}
	}
	if !creator.IsEmpty() && !isOwnCallJID(client, creator) {
		return creator
	}
	return types.JID{}
}

func callLogTimestamp(raw int64) time.Time {
	if raw <= 0 {
		return time.Time{}
	}
	// WhatsApp schemas do not declare a unit. Current call records use milliseconds,
	// while older fixtures exist in seconds; accept both without altering the value.
	if raw >= 100_000_000_000 {
		return time.UnixMilli(raw)
	}
	return time.Unix(raw, 0)
}

func callLogOutcome(result waSyncAction.CallLogRecord_CallResult) string {
	switch result {
	case waSyncAction.CallLogRecord_CONNECTED:
		return "connected"
	case waSyncAction.CallLogRecord_REJECTED:
		return "rejected"
	case waSyncAction.CallLogRecord_CANCELLED:
		return "cancelled"
	case waSyncAction.CallLogRecord_ACCEPTEDELSEWHERE:
		return "accepted_elsewhere"
	case waSyncAction.CallLogRecord_MISSED:
		return "missed"
	case waSyncAction.CallLogRecord_INVALID:
		return "invalid"
	case waSyncAction.CallLogRecord_UNAVAILABLE:
		return "unavailable"
	case waSyncAction.CallLogRecord_UPCOMING:
		return "upcoming"
	case waSyncAction.CallLogRecord_FAILED:
		return "failed"
	case waSyncAction.CallLogRecord_ABANDONED:
		return "abandoned"
	case waSyncAction.CallLogRecord_ONGOING:
		return "ongoing"
	default:
		return ""
	}
}

func callLogState(outcome string) string {
	switch outcome {
	case "missed":
		return callStateMissed
	case "rejected":
		return callStateRejected
	case "ongoing", "upcoming", "":
		return "unknown"
	default:
		return callStateEnded
	}
}

func newCallLogContract(
	client *whatsmeow.Client,
	record *waSyncAction.CallLogRecord,
	source string,
) (*CallContract, types.JID, time.Time) {
	if record == nil {
		return nil, types.JID{}, time.Time{}
	}
	callID := strings.TrimSpace(record.GetCallID())
	if callID == "" {
		callID = strings.TrimSpace(record.GetScheduledCallID())
	}
	if callID == "" {
		return nil, types.JID{}, time.Time{}
	}

	peer := callLogPeer(client, record)
	group := callLogJID(record.GetGroupJID())
	if peer.IsEmpty() && !group.IsEmpty() {
		peer = group
	}
	timestamp := callLogTimestamp(record.GetStartTime())
	direction := callDirectionUnknown
	if record.IsIncoming != nil {
		if record.GetIsIncoming() {
			direction = callDirectionIncoming
		} else {
			direction = callDirectionOutgoing
		}
	}
	kind := callKindUnknown
	if record.IsVideo != nil {
		if record.GetIsVideo() {
			kind = callKindVideo
		} else {
			kind = callKindVoice
		}
	}
	outcome := ""
	if record.CallResult != nil {
		outcome = callLogOutcome(record.GetCallResult())
	}
	contract := &CallContract{
		Version:        callContractVersion,
		CallID:         callID,
		PeerJID:        callJIDString(peer),
		GroupJID:       callJIDString(group),
		Direction:      direction,
		Kind:           kind,
		State:          callLogState(outcome),
		EventTimestamp: callTimestamp(timestamp),
		TerminalReason: outcome,
		Sequence:       callRecordSequence,
		Outcome:        outcome,
		Source:         source,
	}
	if record.Duration != nil && record.GetDuration() >= 0 {
		duration := record.GetDuration()
		contract.DurationSeconds = &duration
	}
	return contract, peer, timestamp
}

func newCallLogRecordEvent(
	ctx context.Context,
	client *whatsmeow.Client,
	record *waSyncAction.CallLogRecord,
	source string,
) (EventKind, *EventPayload) {
	contract, peer, timestamp := newCallLogContract(client, record, source)
	if contract == nil {
		return EventUnknown, nil
	}
	actor := newActor(ctx, client, peer)
	if transport := callContractTransport(contract); transport != "" {
		actor.LID = transport
	}
	return EventCall, &EventPayload{Call: Call{
		State:     legacyCallState(contract.State),
		Actor:     actor,
		Timestamp: timestamp.Unix(),
	}}
}

func callLogMessageOutcome(result waE2E.CallLogMessage_CallOutcome) string {
	switch result {
	case waE2E.CallLogMessage_CONNECTED:
		return "connected"
	case waE2E.CallLogMessage_MISSED:
		return "missed"
	case waE2E.CallLogMessage_FAILED:
		return "failed"
	case waE2E.CallLogMessage_REJECTED:
		return "rejected"
	case waE2E.CallLogMessage_ACCEPTED_ELSEWHERE:
		return "accepted_elsewhere"
	case waE2E.CallLogMessage_ONGOING:
		return "ongoing"
	case waE2E.CallLogMessage_SILENCED_BY_DND:
		return "silenced_by_dnd"
	case waE2E.CallLogMessage_SILENCED_UNKNOWN_CALLER:
		return "silenced_unknown_caller"
	default:
		return ""
	}
}

func newCallLogMessageEvent(
	ctx context.Context,
	client *whatsmeow.Client,
	evt *events.Message,
) (EventKind, *EventPayload) {
	if evt == nil || evt.Message == nil {
		return EventUnknown, nil
	}
	message := evt.Message.GetCallLogMesssage()
	if message == nil || strings.TrimSpace(string(evt.Info.ID)) == "" {
		return EventUnknown, nil
	}
	peer := evt.Info.Chat.ToNonAD()
	group := types.JID{}
	if evt.Info.IsGroup {
		group = evt.Info.Chat.ToNonAD()
		peer = evt.Info.Sender.ToNonAD()
		if isOwnCallJID(client, peer) {
			peer = types.JID{}
			for _, participant := range message.GetParticipants() {
				candidate := callLogJID(participant.GetJID())
				if !candidate.IsEmpty() && !isOwnCallJID(client, candidate) {
					peer = candidate
					break
				}
			}
		}
		if peer.IsEmpty() {
			peer = group
		}
	}
	direction := callDirectionIncoming
	if evt.Info.IsFromMe {
		direction = callDirectionOutgoing
	}
	kind := callKindUnknown
	if message.IsVideo != nil {
		if message.GetIsVideo() {
			kind = callKindVideo
		} else {
			kind = callKindVoice
		}
	}
	outcome := ""
	if message.CallOutcome != nil {
		outcome = callLogMessageOutcome(message.GetCallOutcome())
	}
	source := "message"
	if evt.SourceWebMsg != nil {
		source = "history_sync"
	}
	contract := &CallContract{
		Version:        callContractVersion,
		CallID:         strings.TrimSpace(string(evt.Info.ID)),
		PeerJID:        callJIDString(peer),
		GroupJID:       callJIDString(group),
		Direction:      direction,
		Kind:           kind,
		State:          callLogState(outcome),
		EventTimestamp: callTimestamp(evt.Info.Timestamp),
		TerminalReason: outcome,
		Sequence:       callRecordSequence,
		Outcome:        outcome,
		Source:         source,
	}
	if message.DurationSecs != nil && message.GetDurationSecs() >= 0 {
		duration := message.GetDurationSecs()
		contract.DurationSeconds = &duration
	}
	actor := newActor(ctx, client, peer, evt.Info.SenderAlt)
	if transport := callContractTransport(contract); transport != "" {
		actor.LID = transport
	}
	return EventCall, &EventPayload{Call: Call{
		State:     legacyCallState(contract.State),
		Actor:     actor,
		Timestamp: evt.Info.Timestamp.Unix(),
	}}
}

'''
    source = replace_once(
        source,
        "// NewActor returns a concrete [Actor]",
        insertion + "// NewActor returns a concrete [Actor]",
        "call log record conversion",
    )
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-records-v27"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_session_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if 'newCallLogRecordEvent(s.ctx, client, record, "history_sync")' in source:
        return False

    source = replace_once(
        source,
        '''\tswitch evt := evt.(type) {
\tcase *events.AppStateSyncComplete:
''',
        '''\tswitch evt := evt.(type) {
\tcase *events.AppState:
\t\tif action := evt.GetCallLogAction(); action != nil {
\t\t\tif record := action.GetCallLogRecord(); record != nil {
\t\t\t\tkind, payload := newCallLogRecordEvent(s.ctx, client, record, "app_state")
\t\t\t\tif payload != nil {
\t\t\t\t\ts.propagateEvent(kind, payload)
\t\t\t\t}
\t\t\t}
\t\t}
\tcase *events.AppStateSyncComplete:
''',
        "real-time app state call logs",
    )
    source = replace_once(
        source,
        '''\tcase *events.HistorySync:
\t\tswitch evt.Data.GetSyncType() {
''',
        '''\tcase *events.HistorySync:
\t\tfor _, record := range evt.Data.GetCallLogRecords() {
\t\t\tkind, payload := newCallLogRecordEvent(s.ctx, client, record, "history_sync")
\t\t\tif payload != nil {
\t\t\t\ts.propagateEvent(kind, payload)
\t\t\t}
\t\t}
\t\tswitch evt.Data.GetSyncType() {
''',
        "historical call log records",
    )
    source = replace_once(
        source,
        '''\tcase *events.Message:
\t\ts.propagateEvent(newMessageEvent(s.ctx, client, evt))
''',
        '''\tcase *events.Message:
\t\tif evt.Message != nil && evt.Message.GetCallLogMesssage() != nil {
\t\t\tkind, payload := newCallLogMessageEvent(s.ctx, client, evt)
\t\t\tif payload != nil {
\t\t\t\ts.propagateEvent(kind, payload)
\t\t\t}
\t\t} else {
\t\t\ts.propagateEvent(newMessageEvent(s.ctx, client, evt))
\t\t}
''',
        "E2E call log message fallback",
    )
    source = replace_once(source, '''\tcase *events.CallTerminate:\n\t\ts.propagateEvent(newCallEvent(\n\t\t\ts.ctx, client, callStateFromTerminateReason(evt.Reason), callDirectionFromMeta(client, evt.BasicCallMeta), callKindUnknown, 3, evt.Reason, evt.BasicCallMeta,\n\t\t))\n''', '''\tcase *events.CallTerminate:\n\t\treason := strings.TrimSpace(evt.Reason)\n\t\tstate := callStateFromTerminateReason(reason)\n\t\tsequence := 3\n\t\tif reason == "accepted_elsewhere" {\n\t\t\t// Another linked device answered; this is not a terminal event.\n\t\t\tstate = callStateAccepted\n\t\t\tsequence = 2\n\t\t\treason = ""\n\t\t}\n\t\ts.propagateEvent(newCallEvent(\n\t\t\ts.ctx, client, state, callDirectionFromMeta(client, evt.BasicCallMeta), callKindUnknown, sequence, reason, evt.BasicCallMeta,\n\t\t))\n''', "accepted elsewhere is non-terminal")
    source = source.replace("sequence := 3", "sequence := callSequenceFromTerminateReason(reason)", 1)
    source = replace_once(source, '''\tclient.AutomaticMessageRerequestFromPhone = true\n''', '''\tclient.AutomaticMessageRerequestFromPhone = true\n\tclient.EmitAppStateEventsOnFullSync = true\n''', "emit call log events during app-state backfill")
    source = replace_once(source, '''\tif client.Store.ID != nil {\n\t\treturn client.ConnectContext(s.ctx)\n\t}\n''', '''\tif client.Store.ID != nil {\n\t\tif err := client.ConnectContext(s.ctx); err != nil {\n\t\t\treturn err\n\t\t}\n\t\tgo s.syncCallLogAppState(client)\n\t\treturn nil\n\t}\n''', "call log app-state backfill after connect")
    source = replace_once(source, "func (s *Session) Login() error {\n", '''// syncCallLogAppState asks WhatsApp for the regular app-state snapshot. Call logs live
// under the regular `call_log` index and are otherwise only delivered as incremental updates.
func (s *Session) syncCallLogAppState(client *whatsmeow.Client) {
\tif client == nil {
\t\treturn
\t}
\tif err := client.FetchAppState(s.ctx, appstate.WAPatchRegular, true, false); err != nil {
\t\tclient.Log.Warnf("Failed to backfill WhatsApp call log app state: %v", err)
\t}
}

func (s *Session) Login() error {
''', "call log app-state backfill helper")
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-records-v27"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_session_python(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if '("source", "source")' in source:
        return False

    source = replace_once(
        source,
        '''        ("terminal_reason", "terminal-reason"),
    ):
''',
        '''        ("terminal_reason", "terminal-reason"),
        ("outcome", "outcome"),
        ("source", "source"),
    ):
''',
        "call outcome and source XML attributes",
    )
    source = replace_once(
        source,
        '''        if isinstance(value, str) and value:
            attributes[target] = value
    return ET.Element(f"{{{CALL_NAMESPACE}}}call", attributes)
''',
        '''        if isinstance(value, str) and value:
            attributes[target] = value
    duration = metadata.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        attributes["duration-seconds"] = str(duration)
    return ET.Element(f"{{{CALL_NAMESPACE}}}call", attributes)
''',
        "call duration XML attribute",
    )
    old_handler = '''    async def on_wa_call(self, call: whatsapp.Call) -> None:
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
            # Actor.JID is resolved through the bridge contact map. The resulting
            # bare JID is the canonical XMPP conversation target, unlike peer_jid
            # which remains the WhatsApp/LID-side identifier from the event.
            canonical_chat_jid = str(contact.jid.bare).strip()
            if canonical_chat_jid:
                metadata["chat_jid"] = canonical_chat_jid
            message_kwargs["extra_xml"] = make_call_extension(metadata)
        self.send_gateway_message(text, **message_kwargs)
'''
    new_handler = '''    async def on_wa_call(self, call: whatsapp.Call) -> None:
        metadata = parse_call_metadata(call.Actor.LID)
        group_jid = metadata.get("group_jid") if metadata else None
        contact = None
        recipient_name = ""
        canonical_chat_jid = ""
        if isinstance(group_jid, str) and group_jid:
            muc = await self.bookmarks.by_legacy_id(group_jid)
            recipient_name = muc.name or str(muc.jid.bare)
            canonical_chat_jid = str(muc.jid.bare).strip()
        else:
            peer_jid = call.Actor.JID
            if not peer_jid and metadata:
                raw_peer = metadata.get("peer_jid")
                if isinstance(raw_peer, str):
                    peer_jid = raw_peer
            if not peer_jid:
                warnings.warn("Ignoring a call without a resolvable peer or group")
                return
            contact = await self.contacts.by_legacy_id(peer_jid)
            recipient_name = contact.name or "tel:" + str(contact.jid.local)
            canonical_chat_jid = str(contact.jid.bare).strip()

        state = str(metadata.get("state")) if metadata else ""
        outcome = str(metadata.get("outcome") or "") if metadata else ""
        direction = str(metadata.get("direction") or "") if metadata else ""
        kind = str(metadata.get("kind") or "") if metadata else ""
        if outcome:
            text = f"{direction.capitalize() or 'Call'} {kind} call: {outcome.replace('_', ' ')}"
        else:
            text = _CALL_FALLBACK_LABELS.get(state, "Call")
            if not metadata:
                if call.State == whatsapp.CallIncoming:
                    text = "Incoming call"
                elif call.State == whatsapp.CallMissed:
                    text = "Missed call"
        text += f" with {recipient_name}"
        duration = metadata.get("duration_seconds") if metadata else None
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            text += f", {duration:g} seconds"

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
            if canonical_chat_jid:
                metadata["chat_jid"] = canonical_chat_jid
            message_kwargs["extra_xml"] = make_call_extension(metadata)
        self.send_gateway_message(text, **message_kwargs)
'''
    source = replace_once(source, old_handler, new_handler, "call log XMPP routing")
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-records-v27"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_client_payload(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    if "var waVersion = WAVersionContainer{2, 3000, 1041871181}" in source:
        source = replace_once(
            source,
            "var waVersion = WAVersionContainer{2, 3000, 1041871181}",
            "var waVersion = WAVersionContainer{2, 3000, 1042386815}",
            "WhatsMeow companion web version",
        )
        changed = True
    if "SupportCallLogHistory:                    proto.Bool(false)" in source:
        source = replace_once(
            source,
            "SupportCallLogHistory:                    proto.Bool(false)",
            "SupportCallLogHistory:                    proto.Bool(true)",
            "call log history capability",
        )
        changed = True
    if not changed:
        if (
            "var waVersion = WAVersionContainer{2, 3000, 1042386815}" in source
            and "SupportCallLogHistory:                    proto.Bool(true)" in source
        ):
            return False
        raise SystemExit("Could not patch the expected pinned WhatsMeow client payload.")
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-records-v27"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_package(site_packages: Path, *, backup: bool) -> bool:
    root = site_packages.resolve() / "slidge_whatsapp"
    paths = (
        root / "event.go",
        root / "session.go",
        root / "session.py",
        root / "vendor/go.mau.fi/whatsmeow/store/clientpayload.go",
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changes = (
        patch_event_go(paths[0], backup=backup),
        patch_session_go(paths[1], backup=backup),
        patch_session_python(paths[2], backup=backup),
        patch_client_payload(paths[3], backup=backup),
    )
    return any(changes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add authoritative WhatsApp call logs to the pinned v26 bridge."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print("Call records v27 patch applied." if changed else "Call records v27 already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
