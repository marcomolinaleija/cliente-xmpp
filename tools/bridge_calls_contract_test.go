package whatsapp

import (
    "strings"
    "encoding/json"
    "testing"
    "time"

    "go.mau.fi/whatsmeow/types"
)

func TestNewCallContract(t *testing.T) {
    timestamp := time.Date(2026, time.August, 31, 12, 34, 56, 0, time.UTC)
    meta := types.BasicCallMeta{
        From:      types.JID{User: "peer", Server: types.DefaultUserServer},
        Timestamp: timestamp,
        CallID:    "opaque-call-id",
        GroupJID:  types.JID{User: "group", Server: "g.us"},
    }
    cases := []struct {
        name           string
        state          string
        direction      string
        kind           string
        sequence       int
        terminalReason string
        answeredAt     bool
        endedAt        bool
    }{
        {"offer", callStateOffered, callDirectionIncoming, callKindVoice, 1, "", false, false},
        {"accepted", callStateAccepted, callDirectionOutgoing, callKindUnknown, 2, "", true, false},
        {"missed", callStateMissed, callDirectionIncoming, callKindUnknown, 3, "timeout", false, true},
        {"rejected", callStateRejected, callDirectionOutgoing, callKindUnknown, 3, "", false, true},
        {"ended", callStateEnded, callDirectionUnknown, callKindVideo, 3, "hangup", false, true},
    }
    for _, tt := range cases {
        t.Run(tt.name, func(t *testing.T) {
            contract := newCallContract(tt.state, tt.direction, tt.kind, tt.sequence, tt.terminalReason, meta)
            if contract == nil {
                t.Fatal("expected a contract")
            }
            if contract.Version != callContractVersion || contract.CallID != meta.CallID || contract.Sequence != tt.sequence {
                t.Fatalf("unexpected identity: %#v", contract)
            }
            if contract.PeerJID != "peer@s.whatsapp.net" || contract.GroupJID != "group@g.us" {
                t.Fatalf("unexpected JIDs: %#v", contract)
            }
            if contract.Direction != tt.direction || contract.Kind != tt.kind || contract.State != tt.state {
                t.Fatalf("unexpected enum values: %#v", contract)
            }
            if (contract.AnsweredAt != "") != tt.answeredAt || (contract.EndedAt != "") != tt.endedAt {
                t.Fatalf("unexpected timestamps: %#v", contract)
            }
            if contract.TerminalReason != tt.terminalReason {
                t.Fatalf("unexpected terminal reason: %q", contract.TerminalReason)
            }
            encoded, err := json.Marshal(contract)
            if err != nil {
                t.Fatal(err)
            }
            var decoded map[string]any
            if err := json.Unmarshal(encoded, &decoded); err != nil {
                t.Fatal(err)
            }
            if decoded["event_timestamp"] != "2026-08-31T12:34:56Z" {
                t.Fatalf("timestamp is not UTC RFC3339: %v", decoded["event_timestamp"])
            }
        })
    }
}

func TestCallContractMissingCallIDAndTerminateState(t *testing.T) {
    meta := types.BasicCallMeta{From: types.JID{User: "peer", Server: types.DefaultUserServer}}
    if contract := newCallContract(callStateOffered, callDirectionIncoming, callKindUnknown, 1, "", meta); contract != nil {
        t.Fatalf("contract without upstream CallID: %#v", contract)
    }
    cases := []struct {
        reason string
        want   string
    }{
        {"", callStateMissed},
        {"timeout", callStateMissed},
        {"hangup", callStateEnded},
    }
    for _, tt := range cases {
        t.Run(tt.reason, func(t *testing.T) {
            if got := callStateFromTerminateReason(tt.reason); got != tt.want {
                t.Fatalf("callStateFromTerminateReason(%q) = %q, want %q", tt.reason, got, tt.want)
            }
        })
    }
}

func TestCallKindFromMedia(t *testing.T) {
    cases := []struct {
        media string
        want  string
    }{
        {"audio", callKindVoice},
        {"video", callKindVideo},
        {"", callKindUnknown},
        {"screen", callKindUnknown},
    }
    for _, tt := range cases {
        t.Run(tt.media, func(t *testing.T) {
            if got := callKindFromMedia(tt.media); got != tt.want {
                t.Fatalf("callKindFromMedia(%q) = %q, want %q", tt.media, got, tt.want)
            }
        })
    }
}

func TestCallContractTransport(t *testing.T) {
    timestamp := time.Date(2026, time.August, 31, 12, 34, 56, 0, time.UTC)
    meta := types.BasicCallMeta{
        From:      types.JID{User: "peer", Server: types.DefaultUserServer},
        Timestamp: timestamp,
        CallID:    "opaque-call-id",
    }
    cases := []struct {
        name  string
        input *CallContract
        want  bool
    }{
        {"missing contract", nil, false},
        {"versioned contract", newCallContract(callStateOffered, callDirectionIncoming, callKindVoice, 1, "", meta), true},
    }
    for _, tt := range cases {
        t.Run(tt.name, func(t *testing.T) {
            got := callContractTransport(tt.input)
            if !tt.want {
                if got != "" {
                    t.Fatalf("unexpected transport: %q", got)
                }
                return
            }
            if !strings.HasPrefix(got, callContractTransportPrefix) {
                t.Fatalf("transport prefix missing: %q", got)
            }
            var decoded map[string]any
            if err := json.Unmarshal([]byte(strings.TrimPrefix(got, callContractTransportPrefix)), &decoded); err != nil {
                t.Fatalf("transport JSON: %v", err)
            }
            if decoded["call_id"] != "opaque-call-id" || decoded["sequence"] != float64(1) {
                t.Fatalf("transport changed contract identity: %#v", decoded)
            }
        })
    }
}
