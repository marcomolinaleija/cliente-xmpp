package whatsapp

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
)

type audioModeUploadFixture struct {
	data      []byte
	mediaType whatsmeow.MediaType
	calls     int
	err       error
}

func (f *audioModeUploadFixture) Upload(_ context.Context, data []byte, kind whatsmeow.MediaType) (whatsmeow.UploadResponse, error) {
	f.calls++
	f.data = append([]byte(nil), data...)
	f.mediaType = kind
	return whatsmeow.UploadResponse{URL: "https://upload.example.test/audio", DirectPath: "/fixture",
		MediaKey: []byte("key"), FileSHA256: []byte("hash"), FileEncSHA256: []byte("encrypted")}, f.err
}

func TestCANAudioModesPreserveOriginalBytesAndWireType(t *testing.T) {
	for _, tc := range []struct{ mode, mime, filename string }{
		{"audio", "audio/mpeg", "fixture.mp3"},
		{"document", "audio/wav", "fixture.wav"},
		{"document", "audio/ogg; codecs=opus", "fixture.ogg"},
		{"document", "audio/mp4", "fixture.m4a"},
		{"document", "audio/flac", "fixture.flac"},
	} {
		t.Run(tc.mime, func(t *testing.T) {
			// Opaque bytes must never be decoded or recoded by explicit modes.
			original := []byte("original opaque audio")
			attach := &Attachment{Data: original, MIME: canAudioModeMIME + ";" + tc.mode + ";" + tc.mime,
				Filename: tc.filename, Caption: "  Fixture caption  "}
			client := &audioModeUploadFixture{}
			handled, payload, err := uploadCANAudioAttachment(context.Background(), client, attach)
			if !handled || err != nil || client.calls != 1 || !bytes.Equal(client.data, original) || !bytes.Equal(attach.Data, original) {
				t.Fatalf("changed bytes or skipped explicit intent: %v", err)
			}
			wire, err := proto.Marshal(payload)
			if err != nil {
				t.Fatal(err)
			}
			decoded := &waE2E.Message{}
			if err := proto.Unmarshal(wire, decoded); err != nil {
				t.Fatal(err)
			}
			if tc.mode == "audio" {
				audio := decoded.GetAudioMessage()
				if client.mediaType != whatsmeow.MediaAudio || audio == nil || audio.GetPTT() || audio.GetMimetype() != tc.mime ||
					audio.GetFileLength() != uint64(len(original)) || decoded.DocumentMessage != nil || !bytes.Equal(audio.FileSHA256, []byte("hash")) {
					t.Fatal("MP3 not encoded as normal audio")
				}
			} else {
				document := decoded.GetDocumentMessage()
				if client.mediaType != whatsmeow.MediaDocument || document == nil || decoded.AudioMessage != nil ||
					document.GetMimetype() != tc.mime || document.GetFileName() != tc.filename ||
					document.GetFileLength() != uint64(len(original)) || document.GetCaption() != "Fixture caption" ||
					!bytes.Equal(document.FileEncSHA256, []byte("encrypted")) {
					t.Fatal("audio document lost original metadata")
				}
			}
		})
	}
}

func TestCANAudioModeInvalidIntentNeverUploads(t *testing.T) {
	for _, tc := range []struct {
		mime                       string
		empty, viewOnce, oversized bool
	}{
		{mime: canAudioModeMIME},
		{mime: canAudioModeMIME + ";voice;audio/mpeg"},
		{mime: canAudioModeMIME + ";audio;audio/wav"},
		{mime: canAudioModeMIME + ";document;application/pdf"},
		{mime: canAudioModeMIME + ";document;audio/wav;invalid"},
		{mime: canAudioModeMIME + ";document;audio/wav", empty: true},
		{mime: canAudioModeMIME + ";audio;audio/mpeg", viewOnce: true},
		{mime: canAudioModeMIME + ";document;audio/wav", oversized: true},
	} {
		client := &audioModeUploadFixture{}
		data := []byte("fixture")
		if tc.empty {
			data = nil
		}
		if tc.oversized {
			data = make([]byte, maxCANAudioAttachmentBytes+1)
		}
		attach := &Attachment{MIME: tc.mime, Filename: "fixture", Data: data, ViewOnce: tc.viewOnce}
		handled, _, err := uploadCANAudioAttachment(context.Background(), client, attach)
		if !handled || err == nil || client.calls != 0 {
			t.Fatalf("invalid intent uploaded: %s", tc.mime)
		}
	}
}

func TestCANAudioModeLegacyAndUploadFailure(t *testing.T) {
	client := &audioModeUploadFixture{}
	for _, mime := range []string{"audio/ogg; codecs=opus", "image/png", nativePackMIME} {
		handled, _, err := uploadCANAudioAttachment(context.Background(), client, &Attachment{MIME: mime})
		if handled || err != nil || client.calls != 0 {
			t.Fatal("legacy media changed")
		}
	}
	client.err = errors.New("upload failed")
	handled, payload, err := uploadCANAudioAttachment(context.Background(), client,
		&Attachment{MIME: canAudioModeMIME + ";audio;audio/mpeg", Filename: "fixture.mp3", Data: []byte("fixture")})
	if !handled || payload != nil || !errors.Is(err, client.err) || client.calls != 1 {
		t.Fatal("failure hidden or retried")
	}
}
