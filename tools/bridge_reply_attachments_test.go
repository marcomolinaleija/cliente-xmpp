package whatsapp

import (
	"context"
	"testing"

	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
)

func TestGetMessageWithContextKeepsReplyWithoutParticipant(t *testing.T) {
	message := getMessageWithContext(
		context.Background(),
		nil,
		Message{Kind: MessageAttachment, ID: "audio-message-id"},
		&waE2E.ContextInfo{
			StanzaID:      ptrTo("quoted-message-id"),
			QuotedMessage: &waE2E.Message{Conversation: ptrTo("mensaje citado")},
		},
	)

	if message.ReplyID != "quoted-message-id" {
		t.Fatalf("expected reply ID to survive missing participant, got %q", message.ReplyID)
	}
	if message.ReplyBody != "mensaje citado" {
		t.Fatalf("expected quoted body to survive missing participant, got %q", message.ReplyBody)
	}
}

func TestSetReplyContextAppliesToAudioAttachment(t *testing.T) {
	message := Message{
		ReplyID:   "quoted-audio-id",
		ReplyBody: "audio citado",
		Chat:      Chat{JID: "5215587654321@s.whatsapp.net", IsGroup: false},
		OriginActor: Actor{
			JID: "5215587654321@s.whatsapp.net",
		},
	}
	payload := &waE2E.Message{AudioMessage: &waE2E.AudioMessage{}}

	setReplyContext(payload, message)

	contextInfo := payload.GetAudioMessage().GetContextInfo()
	if contextInfo.GetStanzaID() != message.ReplyID {
		t.Fatalf("expected quoted ID %q, got %q", message.ReplyID, contextInfo.GetStanzaID())
	}
	if contextInfo.GetQuotedMessage().GetConversation() != message.ReplyBody {
		t.Fatalf("expected quoted body %q, got %q", message.ReplyBody, contextInfo.GetQuotedMessage().GetConversation())
	}
	if contextInfo.GetParticipant() != message.OriginActor.JID {
		t.Fatalf("expected participant %q, got %q", message.OriginActor.JID, contextInfo.GetParticipant())
	}
}

func TestSetReplyContextPreservesOriginalGroupForPrivateAudioReply(t *testing.T) {
	message := Message{
		ReplyID: "group-message-id",
		Chat:    Chat{JID: "5215587654321@s.whatsapp.net", IsGroup: false},
		OriginActor: Actor{
			JID: "5215587654321@s.whatsapp.net",
			LID: "123456789012345@lid" + privateReplyGroupSeparator + "120363000000000000@g.us",
		},
	}
	payload := &waE2E.Message{AudioMessage: &waE2E.AudioMessage{}}

	setReplyContext(payload, message)

	contextInfo := payload.GetAudioMessage().GetContextInfo()
	if contextInfo.GetParticipant() != "123456789012345@lid" {
		t.Fatalf("expected participant %q, got %q", "123456789012345@lid", contextInfo.GetParticipant())
	}
	if contextInfo.GetRemoteJID() != "120363000000000000@g.us" {
		t.Fatalf("expected original group, got %q", contextInfo.GetRemoteJID())
	}
}
