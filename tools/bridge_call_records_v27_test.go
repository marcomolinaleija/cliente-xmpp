package whatsapp

import (
	"testing"
	"time"

	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waSyncAction"
	"google.golang.org/protobuf/proto"
)

func TestCallLogContractKeepsAuthoritativeFacts(t *testing.T) {
	record := &waSyncAction.CallLogRecord{
		CallResult:     waSyncAction.CallLogRecord_CONNECTED.Enum(),
		Duration:       proto.Int64(73),
		StartTime:      proto.Int64(1788179696000),
		IsIncoming:     proto.Bool(false),
		IsVideo:        proto.Bool(true),
		CallID:         proto.String("outgoing-call"),
		CallCreatorJID: proto.String("owner@example.org"),
		GroupJID:       proto.String("group@example.org"),
		Participants: []*waSyncAction.CallLogRecord_ParticipantInfo{
			{UserJID: proto.String("contact@example.org")},
		},
	}

	contract, peer, timestamp := newCallLogContract(nil, record, "history_sync")
	if contract == nil {
		t.Fatal("expected a call log contract")
	}
	if contract.CallID != "outgoing-call" || contract.Sequence != callRecordSequence {
		t.Fatalf("unexpected record identity: %#v", contract)
	}
	if contract.Direction != callDirectionOutgoing || contract.Kind != callKindVideo {
		t.Fatalf("unexpected direction or kind: %#v", contract)
	}
	if contract.Outcome != "connected" || contract.State != callStateEnded {
		t.Fatalf("unexpected outcome mapping: %#v", contract)
	}
	if contract.DurationSeconds == nil || *contract.DurationSeconds != 73 {
		t.Fatalf("duration was not preserved as seconds: %#v", contract)
	}
	if contract.Source != "history_sync" || contract.GroupJID != "group@example.org" {
		t.Fatalf("source or group route was lost: %#v", contract)
	}
	if peer.String() != "contact@example.org" {
		t.Fatalf("outgoing peer was not selected from participants: %s", peer)
	}
	wantTime := time.Date(2026, time.August, 31, 12, 34, 56, 0, time.UTC)
	if !timestamp.Equal(wantTime) || contract.EventTimestamp != "2026-08-31T12:34:56Z" {
		t.Fatalf("millisecond timestamp was not normalized: %s / %s", timestamp, contract.EventTimestamp)
	}
}

func TestCallLogContractAcceptsSecondsAndPreservesUnknownFields(t *testing.T) {
	record := &waSyncAction.CallLogRecord{
		StartTime:      proto.Int64(1788179696),
		CallID:         proto.String("partial-call"),
		CallCreatorJID: proto.String("contact@example.org"),
	}
	contract, peer, timestamp := newCallLogContract(nil, record, "app_state")
	if contract == nil {
		t.Fatal("expected a partial contract")
	}
	if contract.Direction != callDirectionUnknown || contract.Kind != callKindUnknown {
		t.Fatalf("missing booleans must remain unknown: %#v", contract)
	}
	if contract.Outcome != "" || contract.DurationSeconds != nil {
		t.Fatalf("missing result and duration must remain absent: %#v", contract)
	}
	if peer.String() != "contact@example.org" {
		t.Fatalf("incoming fallback creator was lost: %s", peer)
	}
	if timestamp.Unix() != 1788179696 {
		t.Fatalf("second timestamp changed: %s", timestamp)
	}
}

func TestGroupCallWithoutParticipantStillKeepsAUsableRoute(t *testing.T) {
	record := &waSyncAction.CallLogRecord{
		CallID:   proto.String("group-call"),
		GroupJID: proto.String("group@example.org"),
	}
	contract, peer, _ := newCallLogContract(nil, record, "history_sync")
	if contract == nil {
		t.Fatal("expected a group call contract")
	}
	if contract.GroupJID != "group@example.org" || contract.PeerJID != contract.GroupJID {
		t.Fatalf("group fallback route was lost: %#v", contract)
	}
	if peer.String() != contract.GroupJID {
		t.Fatalf("group fallback peer differs: %s", peer)
	}
}

func TestCallLogOutcomesAreLossless(t *testing.T) {
	cases := map[waSyncAction.CallLogRecord_CallResult]string{
		waSyncAction.CallLogRecord_CONNECTED:         "connected",
		waSyncAction.CallLogRecord_REJECTED:          "rejected",
		waSyncAction.CallLogRecord_CANCELLED:         "cancelled",
		waSyncAction.CallLogRecord_ACCEPTEDELSEWHERE: "accepted_elsewhere",
		waSyncAction.CallLogRecord_MISSED:            "missed",
		waSyncAction.CallLogRecord_INVALID:           "invalid",
		waSyncAction.CallLogRecord_UNAVAILABLE:       "unavailable",
		waSyncAction.CallLogRecord_UPCOMING:          "upcoming",
		waSyncAction.CallLogRecord_FAILED:            "failed",
		waSyncAction.CallLogRecord_ABANDONED:         "abandoned",
		waSyncAction.CallLogRecord_ONGOING:           "ongoing",
	}
	for input, want := range cases {
		if got := callLogOutcome(input); got != want {
			t.Errorf("callLogOutcome(%s) = %q, want %q", input, got, want)
		}
	}
}

func TestE2ECallLogOutcomesIncludeSilencedCalls(t *testing.T) {
	cases := map[waE2E.CallLogMessage_CallOutcome]string{
		waE2E.CallLogMessage_CONNECTED:               "connected",
		waE2E.CallLogMessage_MISSED:                  "missed",
		waE2E.CallLogMessage_FAILED:                  "failed",
		waE2E.CallLogMessage_REJECTED:                "rejected",
		waE2E.CallLogMessage_ACCEPTED_ELSEWHERE:      "accepted_elsewhere",
		waE2E.CallLogMessage_ONGOING:                 "ongoing",
		waE2E.CallLogMessage_SILENCED_BY_DND:         "silenced_by_dnd",
		waE2E.CallLogMessage_SILENCED_UNKNOWN_CALLER: "silenced_unknown_caller",
	}
	for input, want := range cases {
		if got := callLogMessageOutcome(input); got != want {
			t.Errorf("callLogMessageOutcome(%s) = %q, want %q", input, got, want)
		}
	}
}

func TestAcceptedElsewhereIsAnAcceptancePhase(t *testing.T) {
	if got := callStateFromTerminateReason("accepted_elsewhere"); got != callStateAccepted {
		t.Fatalf("accepted_elsewhere state = %q, want %q", got, callStateAccepted)
	}
	if got := callSequenceFromTerminateReason("accepted_elsewhere"); got != 2 {
		t.Fatalf("accepted_elsewhere sequence = %d, want 2", got)
	}
}
