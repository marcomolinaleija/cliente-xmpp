package whatsapp

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"image"
	"image/color"
	"image/png"
	"os"
	"testing"

	"codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp/media"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
)

func packFixture(t *testing.T, name string) (*waE2E.StickerPackMessage, []byte) {
	t.Helper()
	bitmap := image.NewNRGBA(image.Rect(0, 0, 32, 32))
	bitmap.Set(12, 12, color.NRGBA{R: 255, A: 255})
	var pngData, output bytes.Buffer
	if err := png.Encode(&pngData, bitmap); err != nil { t.Fatal(err) }
	writer := zip.NewWriter(&output)
	entry, err := writer.Create(name)
	if err != nil { t.Fatal(err) }
	if _, err = entry.Write(pngData.Bytes()); err != nil { t.Fatal(err) }
	if err = writer.Close(); err != nil { t.Fatal(err) }
	return &waE2E.StickerPackMessage{Name: ptrTo("Fixture"), Publisher: ptrTo("CAN"),
		Stickers: []*waE2E.StickerPackMessage_Sticker{{FileName: ptrTo(name), Mimetype: ptrTo("image/png"),
			AccessibilityLabel: ptrTo("Una figura saluda."), Emojis: []string{"🙂"}}}}, output.Bytes()
}

func TestPackDownloadAndPortableEnvelope(t *testing.T) {
	pack, data := packFixture(t, "00_fixture.png")
	calls := 0
	attachment := receiveStickerPack(context.Background(), func(ctx context.Context, msg whatsmeow.DownloadableMessage) ([]byte, error) {
		calls++
		if msg != pack || whatsmeow.GetMediaType(msg) != whatsmeow.MediaStickerPack { t.Fatal("wrong encrypted media domain") }
		return data, nil
	}, pack)
	if calls != 1 || attachment.MIME != "application/x-whatsapp-sticker-pack" || attachment.Filename != "stickers.canstickers" { t.Fatal("pack was not received") }
	archive, err := zip.NewReader(bytes.NewReader(attachment.Data), int64(len(attachment.Data)))
	if err != nil { t.Fatal(err) }
	var manifest packManifest
	for _, file := range archive.File {
		if file.Name != "manifest.json" { continue }
		reader, err := file.Open(); if err != nil { t.Fatal(err) }
		if err = json.NewDecoder(reader).Decode(&manifest); err != nil { t.Fatal(err) }
		reader.Close()
	}
	if manifest.Name != "Fixture" || len(manifest.Stickers) != 1 || manifest.Stickers[0].Description != "Una figura saluda." { t.Fatalf("lost metadata: %#v", manifest) }
	if destination := os.Getenv("PACK_FIXTURE_OUTPUT"); destination != "" {
		if err = os.WriteFile(destination, attachment.Data, 0600); err != nil { t.Fatal(err) }
	}
}

func TestPackFailuresAreVisible(t *testing.T) {
	for _, scenario := range []string{"download", "corrupt", "unsafe", "missing", "duplicate", "oversize"} {
		t.Run(scenario, func(t *testing.T) {
			pack, data := packFixture(t, "fixture.png")
			if scenario == "unsafe" { pack, data = packFixture(t, "../fixture.png") }
			if scenario == "corrupt" { data = []byte("not a zip") }
			if scenario == "missing" { pack.Stickers[0].FileName = ptrTo("missing.png") }
			if scenario == "duplicate" { pack.Stickers = append(pack.Stickers, pack.Stickers[0]) }
			if scenario == "oversize" { pack.FileLength = ptrTo(uint64(maxStickerPackBytes + 1)) }
			calls := 0
			attachment := receiveStickerPack(context.Background(), func(context.Context, whatsmeow.DownloadableMessage) ([]byte, error) {
				calls++
				if scenario == "download" { return nil, errors.New("private credentials must not be exposed") }
				return data, nil
			}, pack)
			if attachment.MIME != "text/plain" || attachment.Caption == "" || bytes.Contains(attachment.Data, []byte("credentials")) { t.Fatal("failure disappeared or leaked diagnostics") }
			if scenario == "oversize" && calls != 0 { t.Fatal("oversize pack downloaded") }
		})
	}
}

func TestNativePackDispatchPreservesReplyContext(t *testing.T) {
	pack, _ := packFixture(t, "fixture.png")
	pack.FileLength = ptrTo(uint64(maxStickerPackBytes + 1))
	pack.ContextInfo = &waE2E.ContextInfo{StanzaID: ptrTo("quoted-fixture")}
	attachments, contextInfo, err := getMessageAttachments(context.Background(), nil, &waE2E.Message{StickerPackMessage: pack})
	if err != nil || len(attachments) != 1 || contextInfo.GetStanzaID() != "quoted-fixture" { t.Fatal("native pack silently dropped or reply lost") }
}

func TestNativeStickerAccessibleLabel(t *testing.T) {
	for _, label := range []string{"Una figura saluda.", ""} {
		t.Run(label, func(t *testing.T) {
			message := buildStickerMessage(&Attachment{Data: []byte("webp"), Caption: "  " + label + "  "},
				whatsmeow.UploadResponse{}, &media.Spec{ImageWidth: 512, ImageHeight: 512})
			if message.StickerMessage.GetAccessibilityLabel() != label || message.ImageMessage != nil { t.Fatal("native accessible label lost") }
			wire, err := proto.Marshal(message)
			if err != nil { t.Fatal(err) }
			var decoded waE2E.Message
			if err = proto.Unmarshal(wire, &decoded); err != nil { t.Fatal(err) }
			if decoded.GetStickerMessage().GetAccessibilityLabel() != label { t.Fatal("label missing on wire") }
		})
	}
}
