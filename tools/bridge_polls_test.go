package whatsapp

import (
	"bytes"
	"testing"

	"google.golang.org/protobuf/proto"
	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
)

func pollVoteOptions(names ...string) []PollOption {
	options := make([]PollOption, 0, len(names))
	for _, name := range names {
		options = append(options, PollOption{Title: name})
	}
	return options
}

func TestPollSelectableCountUsesPollVersionFallback(t *testing.T) {
	poll := &waE2E.PollCreationMessage{
		Options: []*waE2E.PollCreationMessage_Option{
			{OptionName: proto.String("Uno")},
			{OptionName: proto.String("Dos")},
			{OptionName: proto.String("Tres")},
		},
		SelectableOptionsCount: proto.Uint32(0),
	}
	if got := pollSelectableCount(poll, 1); got != 1 {
		t.Fatalf("single-select v3 fallback = %d, want 1", got)
	}
	if got := pollSelectableCount(poll, 0); got != 3 {
		t.Fatalf("multi-select v1 fallback = %d, want 3", got)
	}
	if got := pollSelectionMode(poll, 1); got != "single" {
		t.Fatalf("v3 mode = %q, want single", got)
	}
	if got := pollSelectionMode(poll, 0); got != "multiple" {
		t.Fatalf("v1 mode = %q, want multiple", got)
	}
	poll.SelectableOptionsCount = proto.Uint32(2)
	if got := pollSelectableCount(poll, 1); got != 2 {
		t.Fatalf("explicit selection limit = %d, want 2", got)
	}
	if got := pollSelectionMode(poll, 1); got != "multiple" {
		t.Fatalf("explicit multi-select mode = %q, want multiple", got)
	}
}

func TestPollVoteMessageInfoDirect(t *testing.T) {
	chat, _ := types.ParseJID("5215512345678@s.whatsapp.net")
	info, options, err := pollVoteMessageInfo(Message{
		ID:   "poll-direct",
		Chat: Chat{JID: chat.String()},
		Actor: Actor{
			JID:  "5215587654321@s.whatsapp.net",
			LID:  "123456789012345@lid",
			IsMe: false,
		},
		Poll: Poll{Options: pollVoteOptions("Café", "Té")},
	}, chat)
	if err != nil {
		t.Fatalf("direct poll vote metadata failed: %v", err)
	}
	creatorLID, _ := types.ParseJID("123456789012345@lid")
	if info.ID != "poll-direct" || info.Chat != creatorLID || info.IsGroup {
		t.Fatalf("unexpected direct poll info: %+v", info)
	}
	if info.Sender.String() != "123456789012345@lid" {
		t.Fatalf("unexpected direct creator: %s", info.Sender)
	}
	if len(options) != 2 || options[1] != "Té" {
		t.Fatalf("unexpected direct poll options: %#v", options)
	}
}

func TestPollVoteMessageInfoGroupUsesCreatorLID(t *testing.T) {
	chat, _ := types.ParseJID("120363012345678901@g.us")
	info, options, err := pollVoteMessageInfo(Message{
		ID:   "poll-group",
		Chat: Chat{JID: chat.String(), IsGroup: true},
		Actor: Actor{
			JID:  "5215587654321@s.whatsapp.net",
			LID:  "123456789012345@lid",
			IsMe: true,
		},
		Poll: Poll{Options: pollVoteOptions("Uno")},
	}, chat)
	if err != nil {
		t.Fatalf("group poll vote metadata failed: %v", err)
	}
	if !info.IsGroup || !info.IsFromMe {
		t.Fatalf("group flags were not preserved: %+v", info.MessageSource)
	}
	if info.Sender.String() != "123456789012345@lid" {
		t.Fatalf("group vote did not use creator LID: %s", info.Sender)
	}
	if len(options) != 1 || options[0] != "Uno" {
		t.Fatalf("unexpected group poll options: %#v", options)
	}
}

func TestPollVoteMessageInfoRejectsIncompleteOrDuplicateData(t *testing.T) {
	group, _ := types.ParseJID("120363012345678901@g.us")
	_, _, err := pollVoteMessageInfo(Message{
		ID:    "poll-group",
		Chat:  Chat{JID: group.String(), IsGroup: true},
		Actor: Actor{JID: "5215587654321@s.whatsapp.net"},
		Poll:  Poll{Options: pollVoteOptions("Uno")},
	}, group)
	if err == nil {
		t.Fatal("group poll vote without creator LID was accepted")
	}

	direct, _ := types.ParseJID("5215512345678@s.whatsapp.net")
	_, _, err = pollVoteMessageInfo(Message{
		ID:    "poll-direct",
		Chat:  Chat{JID: direct.String()},
		Actor: Actor{JID: "5215587654321@s.whatsapp.net"},
		Poll:  Poll{Options: pollVoteOptions("Uno", "Uno")},
	}, direct)
	if err == nil {
		t.Fatal("duplicate poll vote options were accepted")
	}
}

func TestSetPollUpdateMessageKeepsReferenceAndSelectedHashes(t *testing.T) {
	message := Message{ID: "vote-event"}
	selected := [][]byte{{0x01, 0x02, 0xab}, bytes.Repeat([]byte{0xff}, 32)}
	update := &waE2E.PollUpdateMessage{
		PollCreationMessageKey: &waCommon.MessageKey{ID: proto.String("poll-1")},
	}
	vote := &waE2E.PollVoteMessage{SelectedOptions: selected}

	if !setPollUpdateMessage(&message, update, vote) {
		t.Fatal("valid poll update was rejected")
	}
	if message.Kind != MessagePoll || message.ReferenceID != "poll-1" {
		t.Fatalf("unexpected poll update metadata: %+v", message)
	}
	if len(message.Poll.Options) != 2 {
		t.Fatalf("unexpected poll update options: %+v", message.Poll.Options)
	}
	if message.Poll.Options[0].Title != "0102ab" {
		t.Fatalf("selected option hash was not hex encoded: %q", message.Poll.Options[0].Title)
	}
}
