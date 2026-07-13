package whatsapp

import (
	"testing"

	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
)

func TestSetForwardedContextConvertsPlainText(t *testing.T) {
	body := "mensaje"
	payload := &waE2E.Message{Conversation: &body}

	setForwardedContext(payload)

	if payload.Conversation != nil {
		t.Fatal("plain text was not converted to ExtendedTextMessage")
	}
	if payload.GetExtendedTextMessage().GetText() != body {
		t.Fatal("message body was not preserved")
	}
	if !payload.GetExtendedTextMessage().GetContextInfo().GetIsForwarded() {
		t.Fatal("forwarded flag was not set on text")
	}
}

func TestSetForwardedContextCoversMedia(t *testing.T) {
	tests := map[string]*waE2E.Message{
		"image":    {ImageMessage: &waE2E.ImageMessage{}},
		"audio":    {AudioMessage: &waE2E.AudioMessage{}},
		"video":    {VideoMessage: &waE2E.VideoMessage{}},
		"document": {DocumentMessage: &waE2E.DocumentMessage{}},
		"location": {LocationMessage: &waE2E.LocationMessage{}},
	}

	for name, payload := range tests {
		t.Run(name, func(t *testing.T) {
			setForwardedContext(payload)
			var forwarded bool
			switch {
			case payload.ImageMessage != nil:
				forwarded = payload.ImageMessage.GetContextInfo().GetIsForwarded()
			case payload.AudioMessage != nil:
				forwarded = payload.AudioMessage.GetContextInfo().GetIsForwarded()
			case payload.VideoMessage != nil:
				forwarded = payload.VideoMessage.GetContextInfo().GetIsForwarded()
			case payload.DocumentMessage != nil:
				forwarded = payload.DocumentMessage.GetContextInfo().GetIsForwarded()
			case payload.LocationMessage != nil:
				forwarded = payload.LocationMessage.GetContextInfo().GetIsForwarded()
			}
			if !forwarded {
				t.Fatal("forwarded flag was not set")
			}
		})
	}
}
