// Offline comparison with pinned synthetic packets/PCM derived from WhatsApp's codec.
// No sockets, account arguments, device access or audio output.
package main

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"time"
	"wacalls/internal/voip/media/mlow"
)

type referenceManifest struct {
	Commit string `json:"commit"`
	Files  map[string]struct {
		Bytes  int    `json:"bytes"`
		SHA256 string `json:"sha256"`
	} `json:"files"`
}
type signalComparison struct {
	Samples           int     `json:"samples"`
	RMSE              float64 `json:"rmse_normalized"`
	Correlation       float64 `json:"correlation"`
	MaxAbsoluteError  float64 `json:"max_absolute_error"`
	ExactPCM16Samples int     `json:"exact_pcm16_samples"`
}
type comparison struct {
	Mode                              string           `json:"mode"`
	ReferenceCommit                   string           `json:"reference_commit"`
	Frames                            int              `json:"frames"`
	ReferenceTOCs                     map[string]int   `json:"reference_tocs"`
	LocalTOCs                         map[string]int   `json:"local_encoder_tocs"`
	ActivityMismatches                int              `json:"encoder_activity_mismatches"`
	EncodeMaxUS                       int64            `json:"encode_max_us"`
	DecodeReferenceStream             signalComparison `json:"decode_reference_stream"`
	DecodeAfterInactive               signalComparison `json:"decode_first_frames_after_inactive"`
	OutgoingIndependentDecodeVerified bool             `json:"outgoing_independent_decode_verified"`
	LiveCallVerified                  bool             `json:"live_call_verified"`
}

func compareSignal(a, b []float64) signalComparison {
	out := signalComparison{Samples: len(a)}
	var se, aa, bb, ab float64
	for i, v := range a {
		d := v - b[i]
		se += d * d
		aa += v * v
		bb += b[i] * b[i]
		ab += v * b[i]
		out.MaxAbsoluteError = math.Max(out.MaxAbsoluteError, math.Abs(d))
		if int16(math.Round(math.Max(-1, math.Min(32767.0/32768, v))*32768)) == int16(math.Round(b[i]*32768)) {
			out.ExactPCM16Samples++
		}
	}
	if len(a) > 0 {
		out.RMSE = math.Sqrt(se / float64(len(a)))
	}
	if aa*bb > 0 {
		out.Correlation = ab / math.Sqrt(aa*bb)
	}
	return out
}

func loadReference(root string) (referenceManifest, map[string][]byte, error) {
	var manifest referenceManifest
	b, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		return manifest, nil, err
	}
	if err = json.Unmarshal(b, &manifest); err != nil {
		return manifest, nil, err
	}
	files := make(map[string][]byte)
	for _, name := range []string{"synth_mic.raw", "wasm_derived_ref.raw", "wasm_derived_frames.json"} {
		meta, ok := manifest.Files[name]
		if !ok {
			return manifest, nil, errors.New("missing reference file pin")
		}
		data, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			return manifest, nil, err
		}
		sum := sha256.Sum256(data)
		if len(data) != meta.Bytes || hex.EncodeToString(sum[:]) != meta.SHA256 {
			return manifest, nil, errors.New("reference checksum mismatch")
		}
		files[name] = data
	}
	return manifest, files, nil
}

func run(root string) (comparison, error) {
	result := comparison{Mode: "offline-reference-comparison", ReferenceTOCs: map[string]int{}, LocalTOCs: map[string]int{}}
	manifest, files, err := loadReference(root)
	if err != nil {
		return result, err
	}
	result.ReferenceCommit = manifest.Commit
	var frames []string
	if err = json.Unmarshal(files["wasm_derived_frames.json"], &frames); err != nil {
		return result, err
	}
	if len(frames) != 110 || len(files["synth_mic.raw"]) != len(frames)*960*2 || len(files["wasm_derived_ref.raw"]) != len(frames)*960*2 {
		return result, errors.New("reference shape mismatch")
	}
	encoder := mlow.NewMlowEncoder()
	decoder := mlow.NewMlowDecoder()
	var local, reference, onsetLocal, onsetReference []float64
	var previous byte
	for f, encoded := range frames {
		wire, err := hex.DecodeString(encoded)
		if err != nil || len(wire) == 0 {
			return result, errors.New("invalid reference frame")
		}
		result.ReferenceTOCs[fmt.Sprintf("%02x", wire[0])]++
		pcm := make([]float32, 960)
		for i := range pcm {
			pcm[i] = float32(int16(binary.LittleEndian.Uint16(files["synth_mic.raw"][(f*960+i)*2:]))) / 32768
		}
		started := time.Now()
		own, err := encoder.Encode(pcm)
		result.EncodeMaxUS = max(result.EncodeMaxUS, time.Since(started).Microseconds())
		if err != nil || len(own) == 0 {
			return result, errors.New("local encoding failed")
		}
		result.LocalTOCs[fmt.Sprintf("%02x", own[0])]++
		if own[0] != wire[0] {
			result.ActivityMismatches++
		}
		decoded := decoder.Decode(wire)
		if len(decoded) != 960 {
			return result, errors.New("wrong reference decode length")
		}
		onset := previous == 0x10 && wire[0] != 0x10
		for i, v := range decoded {
			if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
				return result, errors.New("nonfinite decode")
			}
			ref := float64(int16(binary.LittleEndian.Uint16(files["wasm_derived_ref.raw"][(f*960+i)*2:]))) / 32768
			local = append(local, float64(v))
			reference = append(reference, ref)
			if onset {
				onsetLocal = append(onsetLocal, float64(v))
				onsetReference = append(onsetReference, ref)
			}
		}
		previous = wire[0]
		result.Frames++
	}
	if len(onsetLocal) == 0 {
		return result, errors.New("reference has no post-pause transition")
	}
	result.DecodeReferenceStream = compareSignal(local, reference)
	result.DecodeAfterInactive = compareSignal(onsetLocal, onsetReference)
	return result, nil
}

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "offline probe accepts no account or network arguments")
		os.Exit(2)
	}
	result, err := run("/opt/can-codec-reference")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err = json.NewEncoder(os.Stdout).Encode(result); err != nil {
		os.Exit(1)
	}
}
