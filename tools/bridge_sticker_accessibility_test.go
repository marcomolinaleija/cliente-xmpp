package whatsapp

import (
	"archive/zip"
	"bytes"
	"strings"
	"testing"
)

func lottieAccessibilityPackage(t *testing.T, metadata string) []byte {
	t.Helper()
	var payload bytes.Buffer
	archive := zip.NewWriter(&payload)
	writer, err := archive.Create(lottieStickerMetadataPath)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = writer.Write([]byte(metadata)); err != nil {
		t.Fatal(err)
	}
	if err = archive.Close(); err != nil {
		t.Fatal(err)
	}
	return payload.Bytes()
}

func TestStickerAccessibilityCaptionPrefersDescription(t *testing.T) {
	if stickerCaptionMarker != "\x1eWHATSAPP_STICKER\x1f" {
		t.Fatalf("unexpected internal sticker marker: %q", stickerCaptionMarker)
	}
	got := stickerAccessibilityCaption(" Una tortuga levanta el pulgar. ", "🐢 👍")
	if got != "Una tortuga levanta el pulgar." {
		t.Fatalf("unexpected description: %q", got)
	}
	if fallback := stickerAccessibilityCaption("", "🐢 👍"); fallback != "🐢 👍" {
		t.Fatalf("unexpected emoji fallback: %q", fallback)
	}
}

func TestLottieStickerAccessibilityCaption(t *testing.T) {
	payload := lottieAccessibilityPackage(t, `{
		"sticker-pack-id":"IntrovertLife",
		"accessibility-text":"Una tortuga se esconde en su caparazón y levanta el pulgar.",
		"emojis":["🫣","🐢","👍"]
	}`)
	got := lottieStickerAccessibilityCaption(payload)
	if got != "Una tortuga se esconde en su caparazón y levanta el pulgar." {
		t.Fatalf("unexpected Lottie description: %q", got)
	}
}

func TestLottieStickerAccessibilityEmojiFallbackAndBounds(t *testing.T) {
	payload := lottieAccessibilityPackage(t, `{"emojis":["🫣","🐢","👍"]}`)
	if got := lottieStickerAccessibilityCaption(payload); got != "🫣 🐢 👍" {
		t.Fatalf("unexpected emoji fallback: %q", got)
	}
	oversized := []byte(strings.Repeat("x", maxLottieStickerPackageBytes+1))
	if got := lottieStickerAccessibilityCaption(oversized); got != "" {
		t.Fatalf("oversized payload was accepted: %q", got)
	}
}
