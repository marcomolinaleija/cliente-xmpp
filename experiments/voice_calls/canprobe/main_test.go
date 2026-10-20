package main

import (
	"bytes"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
	"wacalls/internal/voip/core"
	"wacalls/internal/voip/media"
)

func TestRealCodecAndSRTPDuplexPipeline(t *testing.T) {
	result, err := runProbe()
	if err != nil {
		t.Fatal(err)
	}
	if result.Directions != 2 || result.Frames != 40 || result.SamplesDecoded == 0 || !result.TamperingRejected {
		t.Fatalf("incomplete offline media evidence: %+v", result)
	}
	if result.LiveCallVerified || result.Mode != "offline-synthetic" {
		t.Fatal("synthetic media must never certify a live call")
	}
}

func TestSRTPRejectsHeaderPayloadAndTagChangesWithoutPoisoningState(t *testing.T) {
	key := bytes.Repeat([]byte{0x71}, 32) // Synthetic, not a linked-device key.
	left, err := media.DerivePerJidSrtpKey(key, "fixture-left:0@lid")
	if err != nil {
		t.Fatal(err)
	}
	right, err := media.DerivePerJidSrtpKey(key, "fixture-right:0@lid")
	if err != nil {
		t.Fatal(err)
	}
	sender, err := media.NewSrtpSession(left, right, core.SRTPSendAuthTagLen, core.SRTPRecvAuthTagLen)
	if err != nil {
		t.Fatal(err)
	}
	receiver, err := media.NewSrtpSession(right, left, core.SRTPSendAuthTagLen, core.SRTPRecvAuthTagLen)
	if err != nil {
		t.Fatal(err)
	}
	rtp := media.NewWhatsAppOpusSession(0x11223344)
	packet := rtp.CreatePacketWithDuration([]byte{1, 2, 3, 4}, 960, true)
	packet.Header.SequenceNumber = 65535
	protected, err := sender.Protect(packet)
	if err != nil {
		t.Fatal(err)
	}
	for _, index := range []int{2, packet.Header.Size(), len(protected) - 1} {
		tampered := append([]byte(nil), protected...)
		tampered[index] ^= 0x01
		if _, err := receiver.Unprotect(tampered); err == nil {
			t.Fatalf("accepted a modified packet at offset %d", index)
		}
	}
	if _, err := receiver.Unprotect(protected); err != nil {
		t.Fatalf("forgery poisoned the receiver state: %v", err)
	}
	packet.Header.SequenceNumber = 0
	protected, err = sender.Protect(packet)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.Unprotect(protected); err != nil {
		t.Fatalf("valid sequence rollover failed: %v", err)
	}
}

func TestExistingClientAdapterHasNoConnectionSideEffects(t *testing.T) {
	if _, err := managerForExistingClient(nil); err == nil {
		t.Fatal("missing existing session was accepted")
	}
	client := whatsmeow.NewClient(&store.Device{}, nil)
	manager, err := managerForExistingClient(client)
	if err != nil || manager == nil {
		t.Fatalf("adapter construction failed: %v", err)
	}
	if client.IsConnected() || manager.CurrentCall() != nil {
		t.Fatal("adapter construction started network activity or a call")
	}
}
