package whatsapp

import (
	"context"
	"database/sql"
	"os"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
)

func TestStoredContactInfoAgainstDatabase(t *testing.T) {
	databasePath := os.Getenv("WHATSAPP_TEST_DATABASE")
	if databasePath == "" {
		t.Skip("WHATSAPP_TEST_DATABASE is not set")
	}

	database, err := sql.Open("sqlite3", "file:"+databasePath+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	container := sqlstore.NewWithDB(database, "sqlite3", nil)
	devices, err := container.GetAllDevices(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	var savedContacts int
	var mismatches int
	var duplicateActorRows int
	var deduplicatedMismatches int
	for _, device := range devices {
		client := whatsmeow.NewClient(device, nil)
		contacts, getErr := device.Contacts.GetAllContacts(context.Background())
		if getErr != nil {
			t.Fatal(getErr)
		}
		contactsByJID := make(map[string]Contact)
		savedActors := make(map[string]bool)
		for jid, info := range contacts {
			actor := newActor(context.Background(), client, jid)
			candidate := newContact(client, actor, info)
			if actor.JID != "" {
				current, found := contactsByJID[actor.JID]
				if found {
					duplicateActorRows++
				}
				if !found || preferContactCandidate(current, candidate) {
					contactsByJID[actor.JID] = candidate
				}
			}

			if info.FullName != "" {
				savedContacts++
				savedActors[actor.JID] = true
				stored := storedContactInfo(
					context.Background(), client, actor, jid,
				)
				if stored.FullName != info.FullName {
					mismatches++
				}
			}
		}
		for actorJID := range savedActors {
			if selected, found := contactsByJID[actorJID]; !found || !selected.IsFriend {
				deduplicatedMismatches++
			}
		}
	}

	if savedContacts == 0 {
		t.Fatal("database has no saved contacts")
	}
	if mismatches != 0 {
		t.Fatalf(
			"stored contact lookup mismatched %d of %d saved contacts",
			mismatches,
			savedContacts,
		)
	}
	if deduplicatedMismatches != 0 {
		t.Fatalf(
			"deduplicated roster lost %d saved contact variants",
			deduplicatedMismatches,
		)
	}
	t.Logf("stored contact lookup verified for %d saved contacts", savedContacts)
	t.Logf("deduplication resolved %d duplicate actor rows", duplicateActorRows)
}
