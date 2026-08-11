package whatsapp

import (
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestPollVoteInfoForDirectChat(t *testing.T) {
	chat, err := types.ParseJID("123456789@s.whatsapp.net")
	if err != nil {
		t.Fatal(err)
	}
	info, options, err := pollVoteInfo(Message{
		ID: "POLL-1",
		Chat: Chat{JID: chat.String()},
		Actor: Actor{JID: "123456789@s.whatsapp.net"},
		Poll: Poll{Options: []PollOption{{Title: "Sí"}}},
	}, chat)
	if err != nil {
		t.Fatal(err)
	}
	if info.Sender.String() != "123456789@s.whatsapp.net" || info.IsGroup || len(options) != 1 {
		t.Fatalf("unexpected direct poll info: %#v, %#v", info, options)
	}
}

func TestPollVoteInfoForOwnDirectChatUsesCreatorLIDSecret(t *testing.T) {
	chat, err := types.ParseJID("123456789@s.whatsapp.net")
	if err != nil {
		t.Fatal(err)
	}
	info, _, err := pollVoteInfo(Message{
		ID: "POLL-OWN-DIRECT",
		Chat: Chat{JID: chat.String()},
		Actor: Actor{
			JID:  "111111111@s.whatsapp.net",
			LID:  "222222222@lid",
			IsMe: true,
		},
		Poll: Poll{Options: []PollOption{{Title: "SÃ­"}}},
	}, chat)
	if err != nil {
		t.Fatal(err)
	}
	if info.Chat.String() != "222222222@lid" || info.Sender.String() != "222222222@lid" {
		t.Fatalf("own direct poll must use its LID secret: %#v", info)
	}
}

func TestPollVoteInfoForGroupUsesCreatorLID(t *testing.T) {
	chat, err := types.ParseJID("123456789@g.us")
	if err != nil {
		t.Fatal(err)
	}
	info, _, err := pollVoteInfo(Message{
		ID: "POLL-2",
		Chat: Chat{JID: chat.String(), IsGroup: true},
		Actor: Actor{JID: "123456789@s.whatsapp.net", LID: "987654321@lid"},
		Poll: Poll{Options: []PollOption{{Title: "Una"}}},
	}, chat)
	if err != nil {
		t.Fatal(err)
	}
	if info.Sender.String() != "987654321@lid" || !info.IsGroup {
		t.Fatalf("unexpected group poll info: %#v", info)
	}
}

func TestPollVoteInfoRejectsGroupWithoutCreatorLID(t *testing.T) {
	chat, err := types.ParseJID("123456789@g.us")
	if err != nil {
		t.Fatal(err)
	}
	_, _, err = pollVoteInfo(Message{
		ID: "POLL-3",
		Chat: Chat{JID: chat.String(), IsGroup: true},
		Actor: Actor{JID: "123456789@s.whatsapp.net"},
		Poll: Poll{Options: []PollOption{{Title: "Una"}}},
	}, chat)
	if err == nil {
		t.Fatal("expected missing group LID to be rejected")
	}
}
