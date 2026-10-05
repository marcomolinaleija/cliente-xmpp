package whatsapp

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"os"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"google.golang.org/protobuf/proto"
)

type fakePackUploader struct {
	calls   int
	archive []byte
	key     []byte
	fail    int
}

func (f *fakePackUploader) Upload(ctx context.Context, data []byte, kind whatsmeow.MediaType) (whatsmeow.UploadResponse, error) {
	f.calls++
	if f.fail == 1 {
		return whatsmeow.UploadResponse{}, errors.New("upload unavailable")
	}
	if kind != whatsmeow.MediaStickerPack {
		return whatsmeow.UploadResponse{}, errors.New("wrong media domain")
	}
	f.archive = append([]byte(nil), data...)
	f.key = bytes.Repeat([]byte{7}, 32)
	return whatsmeow.UploadResponse{MediaKey: f.key, DirectPath: "/synthetic-pack", FileSHA256: bytes.Repeat([]byte{1}, 32), FileEncSHA256: bytes.Repeat([]byte{2}, 32)}, nil
}
func (f *fakePackUploader) UploadStickerPackThumbnail(ctx context.Context, data, key []byte) (whatsmeow.UploadResponse, error) {
	f.calls++
	if f.fail == 2 {
		return whatsmeow.UploadResponse{}, errors.New("thumbnail unavailable")
	}
	if !bytes.Equal(key, f.key) || !bytes.HasPrefix(data, []byte{255, 216, 255}) {
		return whatsmeow.UploadResponse{}, errors.New("bad thumbnail")
	}
	return whatsmeow.UploadResponse{DirectPath: "/synthetic-thumbnail", FileSHA256: bytes.Repeat([]byte{3}, 32), FileEncSHA256: bytes.Repeat([]byte{4}, 32)}, nil
}
func outgoingPackFixture(t *testing.T) []byte {
	t.Helper()
	data, err := os.ReadFile("/tmp/native-outgoing-envelope.zip")
	if err != nil {
		t.Fatal(err)
	}
	return data
}
func TestNativePackWireAndReceiverRoundtrip(t *testing.T) {
	data := outgoingPackFixture(t)
	uploader := &fakePackUploader{}
	msg, err := uploadNativeStickerPack(context.Background(), uploader, &Attachment{MIME: nativePackMIME, Data: data})
	if err != nil {
		t.Fatal(err)
	}
	if uploader.calls != 2 || msg.DocumentMessage != nil || msg.StickerMessage != nil {
		t.Fatal("not a native pack")
	}
	wire, err := proto.Marshal(msg)
	if err != nil {
		t.Fatal(err)
	}
	decoded := &waE2E.Message{}
	if err = proto.Unmarshal(wire, decoded); err != nil {
		t.Fatal(err)
	}
	pack := decoded.GetStickerPackMessage()
	if pack.GetName() != "Fixture" || pack.GetPublisher() != "CAN" || pack.GetStickerPackOrigin() != waE2E.StickerPackMessage_USER_CREATED || len(pack.GetStickers()) != 2 || pack.GetStickers()[0].GetAccessibilityLabel() != "Una figura saluda." || !pack.GetStickers()[1].GetIsAnimated() {
		t.Fatal("native metadata lost")
	}
	if pack.GetThumbnailWidth() != 252 || pack.GetThumbnailDirectPath() == "" || len(pack.GetThumbnailSHA256()) != 32 || pack.GetFileLength() != uint64(len(uploader.archive)) {
		t.Fatal("native media envelope incomplete")
	}
	archive, err := zip.NewReader(bytes.NewReader(uploader.archive), int64(len(uploader.archive)))
	if err != nil {
		t.Fatal(err)
	}
	if len(archive.File) != 3 {
		t.Fatal("CAN manifest leaked into native ZIP")
	}
	received := receiveStickerPack(context.Background(), func(context.Context, whatsmeow.DownloadableMessage) ([]byte, error) { return uploader.archive, nil }, pack)
	if received.MIME != "application/x-whatsapp-sticker-pack" {
		t.Fatal("native pack not receivable")
	}
	if target := os.Getenv("PACK_ROUNDTRIP_OUTPUT"); target != "" {
		if err = os.WriteFile(target, received.Data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
func TestNativePackFailuresNeverBecomeDocuments(t *testing.T) {
	for _, stage := range []int{0, 1, 2} {
		t.Run(string(rune('0'+stage)), func(t *testing.T) {
			uploader := &fakePackUploader{fail: stage}
			data := outgoingPackFixture(t)
			if stage == 0 {
				data = []byte("invalid")
			}
			msg, err := uploadNativeStickerPack(context.Background(), uploader, &Attachment{Data: data})
			if err == nil || msg != nil {
				t.Fatal("failed native pack silently sent")
			}
			if stage == 0 && uploader.calls != 0 {
				t.Fatal("unvalidated archive uploaded")
			}
		})
	}
}
