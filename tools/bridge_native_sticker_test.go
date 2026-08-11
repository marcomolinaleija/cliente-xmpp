package whatsapp

import (
	"testing"

	"codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp/media"
	"go.mau.fi/whatsmeow"
)

func TestXMPPStickerBuildsNativeWhatsAppSticker(t *testing.T) {
	attachment := &Attachment{
		MIME:      nativeStickerMIME,
		Data:      []byte("webp-data"),
	}
	message := buildStickerMessage(
		attachment,
		whatsmeow.UploadResponse{
			URL:           "https://media.example/sticker",
			DirectPath:    "/v/t62/sticker",
			MediaKey:      []byte("key"),
			FileEncSHA256: []byte("encrypted-hash"),
			FileSHA256:    []byte("hash"),
		},
		&media.Spec{ImageWidth: 512, ImageHeight: 512, ImageFrameRate: 30},
	)
	if message.StickerMessage == nil || message.ImageMessage != nil {
		t.Fatalf("expected StickerMessage only, got %#v", message)
	}
	if message.StickerMessage.GetMimetype() != "image/webp" {
		t.Fatalf("unexpected sticker MIME: %q", message.StickerMessage.GetMimetype())
	}
	if !message.StickerMessage.GetIsAnimated() {
		t.Fatal("expected animated sticker flag")
	}
	if message.StickerMessage.GetWidth() != 512 || message.StickerMessage.GetHeight() != 512 {
		t.Fatalf("unexpected sticker dimensions: %dx%d", message.StickerMessage.GetWidth(), message.StickerMessage.GetHeight())
	}
}
