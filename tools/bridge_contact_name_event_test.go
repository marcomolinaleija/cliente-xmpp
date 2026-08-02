package whatsapp

import (
	"testing"

	"go.mau.fi/whatsmeow/types"
)

func TestNewContactPrefersSavedNameOverPushName(t *testing.T) {
	contact := newContact(nil, Actor{}, types.ContactInfo{
		FullName: "Saved Name",
		FirstName: "Saved",
		PushName: "Profile Name",
	})
	if contact.Name != "Saved Name" {
		t.Fatalf("expected saved full name, got %q", contact.Name)
	}
	if !contact.IsFriend {
		t.Fatal("a contact with a saved full name must be recognized as a friend")
	}
}

func TestNewContactUsesPushNameForUnsavedContact(t *testing.T) {
	contact := newContact(nil, Actor{}, types.ContactInfo{PushName: "Profile Name"})
	if contact.Name != "Profile Name" {
		t.Fatalf("expected profile-name fallback, got %q", contact.Name)
	}
	if contact.IsFriend {
		t.Fatal("a push-name-only contact must not be recognized as a friend")
	}
}

func TestPreferSavedContactCandidate(t *testing.T) {
	profileOnly := Contact{Name: "Profile Name", IsFriend: false}
	saved := Contact{Name: "Saved Name", IsFriend: true}
	if !preferContactCandidate(profileOnly, saved) {
		t.Fatal("the saved contact variant must replace a profile-name-only variant")
	}
	if preferContactCandidate(saved, profileOnly) {
		t.Fatal("a profile-name-only variant must not replace a saved contact")
	}
}
