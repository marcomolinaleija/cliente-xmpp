package whatsapp

import (
	"context"
	"sync"
	"testing"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store"
)

func presenceLifecycleClient() *whatsmeow.Client {
	return whatsmeow.NewClient(&store.Device{}, nil)
}

func TestSubscribeToPresencesToleratesMissingClient(t *testing.T) {
	session := &Session{ctx: context.Background()}
	if err := session.SubscribeToPresences(); err != nil {
		t.Fatalf("missing client should be ignored during teardown: %v", err)
	}
}

func TestPresenceRefreshStopsIdempotently(t *testing.T) {
	session := &Session{ctx: context.Background()}
	client := presenceLifecycleClient()
	session.startPresenceRefresh(session.ctx, client)
	session.stopPresenceRefresh()
	session.stopPresenceRefresh()

	session.presenceMutex.Lock()
	defer session.presenceMutex.Unlock()
	if session.presenceChan != nil || session.presenceCancel != nil || session.presenceDone != nil {
		t.Fatal("presence lifecycle was not fully cleared")
	}
}

func TestConcurrentPresenceRefreshAndClientCleanup(t *testing.T) {
	session := &Session{ctx: context.Background()}
	client := presenceLifecycleClient()

	for range 500 {
		session.setClient(client)
		var workers sync.WaitGroup
		workers.Add(2)
		go func() {
			defer workers.Done()
			if err := session.SubscribeToPresences(); err != nil {
				t.Errorf("presence refresh failed during cleanup: %v", err)
			}
		}()
		go func() {
			defer workers.Done()
			session.clearClient(client)
		}()
		workers.Wait()
	}
}
