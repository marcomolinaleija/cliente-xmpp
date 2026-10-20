// This executable proves only an offline media pipeline and adapter construction.
// It cannot pair an account, connect to WhatsApp, dial, or expose a listening socket.
package main

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"os"

	"go.mau.fi/whatsmeow"
	"wacalls/internal/voip/call"
	"wacalls/internal/voip/core"
	"wacalls/internal/voip/media"
	"wacalls/internal/wa"
)

var _ core.VoipSocket = (*wa.Socket)(nil)

// Constructing this adapter must not create a second linked device or call Connect.
// Production lifecycle ownership remains with Slidge; this probe does not install it.
func managerForExistingClient(client *whatsmeow.Client) (*call.CallManager, error) {
	if client == nil {
		return nil, errors.New("an existing WhatsMeow client is required")
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return call.NewCallManager(wa.NewSocket(client), logger), nil
}

type Result struct {
	BridgeVersion     int     `json:"bridge_version"`
	Mode              string  `json:"mode"`
	Directions        int     `json:"directions"`
	Frames            int     `json:"frames"`
	SamplesDecoded    int     `json:"samples_decoded"`
	DecodedEnergy     float64 `json:"decoded_energy"`
	TamperingRejected bool    `json:"tampering_rejected"`
	LiveCallVerified  bool    `json:"live_call_verified"`
}

func runProbe() (Result, error) {
	result := Result{BridgeVersion: 35, Mode: "offline-synthetic"}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return result, err
	}
	left, err := media.DerivePerJidSrtpKey(key, "fixture-left:0@lid")
	if err != nil {
		return result, err
	}
	right, err := media.DerivePerJidSrtpKey(key, "fixture-right:0@lid")
	if err != nil {
		return result, err
	}
	for direction := 0; direction < 2; direction++ {
		send, err := media.NewSrtpSession(left, right, core.SRTPSendAuthTagLen, core.SRTPRecvAuthTagLen)
		if err != nil {
			return result, err
		}
		recv, err := media.NewSrtpSession(right, left, core.SRTPSendAuthTagLen, core.SRTPRecvAuthTagLen)
		if err != nil {
			return result, err
		}
		codec, err := media.NewMLowCodec(media.DefaultCodecOptions)
		if err != nil {
			return result, err
		}
		rtp := media.NewWhatsAppOpusSession(uint32(0x10203040 + direction))
		var directionEnergy float64
		for frame := 0; frame < 20; frame++ {
			pcm := make([]float32, codec.FrameSize())
			for i := range pcm {
				phase := float64(frame*len(pcm) + i)
				pcm[i] = float32(0.2 * math.Sin(2*math.Pi*float64(440+direction*220)*phase/float64(codec.SampleRate())))
			}
			encoded, err := codec.Encode(pcm)
			if err != nil || len(encoded) == 0 {
				codec.Close()
				return result, fmt.Errorf("MLow produced no frame: %v", err)
			}
			packet, err := send.Protect(rtp.CreatePacketWithDuration(encoded, len(pcm), frame == 0))
			if err != nil {
				codec.Close()
				return result, err
			}
			if frame == 0 {
				tampered := append([]byte(nil), packet...)
				tampered[len(tampered)-1] ^= 0x01
				if _, err := recv.Unprotect(tampered); err == nil {
					codec.Close()
					return result, errors.New("SRTP accepted a modified packet")
				}
			}
			plain, err := recv.Unprotect(packet)
			if err != nil || !bytes.Equal(plain.Payload, encoded) {
				codec.Close()
				return result, fmt.Errorf("SRTP round trip failed: %v", err)
			}
			decoded, err := codec.Decode(plain.Payload)
			if err != nil || len(decoded) != len(pcm) {
				codec.Close()
				return result, fmt.Errorf("MLow decoded unexpected frame size: %v", err)
			}
			for _, sample := range decoded {
				v := float64(sample)
				if math.IsNaN(v) || math.IsInf(v, 0) {
					codec.Close()
					return result, errors.New("non-finite decoded audio")
				}
				directionEnergy += v * v
			}
			result.Frames++
			result.SamplesDecoded += len(decoded)
		}
		codec.Close()
		if directionEnergy == 0 {
			return result, errors.New("codec only returned silence")
		}
		result.DecodedEnergy += directionEnergy
		result.Directions++
		left, right = right, left
	}
	result.TamperingRejected = true
	return result, nil
}

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "this offline probe accepts no account or network arguments")
		os.Exit(2)
	}
	result, err := runProbe()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
