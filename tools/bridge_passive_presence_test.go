package whatsapp

import (
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestPassiveWhatsAppPresence(t *testing.T) {
	for _, presence := range []PresenceKind{PresenceAvailable, PresenceUnavailable} {
		if got := passiveWhatsAppPresence(presence); got != types.PresenceUnavailable {
			t.Fatalf("presence %v mapped to %v", presence, got)
		}
	}
}
