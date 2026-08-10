from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ruff: noqa: E501


OLD_SESSION_FIELDS = '''\tdevice  LinkedDevice      // The linked device this session corresponds to.
\tclient  *whatsmeow.Client // The concrete client connection to WhatsApp for this session.
\tgateway *Gateway          // The Gateway this Session is attached to.

\tctx       context.Context         // A shared context for all top-level [Session] functions.
\tctxCancel context.CancelCauseFunc // The function to call when cancelling the [Session] context.

\teventHandler HandleEventFunc   // The handler function to use for propagating events to the adapter.
\tpresenceChan chan PresenceKind // A channel used for periodically refreshing contact presences.
'''

NEW_SESSION_FIELDS = '''\tdevice      LinkedDevice      // The linked device this session corresponds to.
\tclient      *whatsmeow.Client // The concrete client connection to WhatsApp for this session.
\tclientMutex sync.RWMutex      // Protects client snapshots during concurrent session cleanup.
\tgateway     *Gateway          // The Gateway this Session is attached to.

\tctx       context.Context         // A shared context for all top-level [Session] functions.
\tctxCancel context.CancelCauseFunc // The function to call when cancelling the [Session] context.

\teventHandler  HandleEventFunc   // The handler function used for propagating adapter events.
\tpresenceMutex sync.Mutex        // Protects the current presence-refresh lifecycle.
\tpresenceChan  chan PresenceKind // Receives availability changes for the current refresher.
\tpresenceCancel context.CancelFunc
\tpresenceDone   chan struct{}
'''

LOGIN_ANCHOR = '''// Login attempts to authenticate the given [Session], either by re-using the [LinkedDevice] attached
// or by initiating a pairing session for a new linked device. Callers are expected to have set an
// event handler in order to receive any incoming events from the underlying WhatsApp session.
'''

LIFECYCLE_HELPERS = r'''func (s *Session) currentClient() *whatsmeow.Client {
	s.clientMutex.RLock()
	defer s.clientMutex.RUnlock()
	return s.client
}

func (s *Session) setClient(client *whatsmeow.Client) {
	s.clientMutex.Lock()
	s.client = client
	s.clientMutex.Unlock()
}

func (s *Session) clearClient(client *whatsmeow.Client) {
	s.clientMutex.Lock()
	if s.client == client {
		s.client = nil
	}
	s.clientMutex.Unlock()
}

func (s *Session) startPresenceRefresh(ctx context.Context, client *whatsmeow.Client) {
	s.stopPresenceRefresh()

	refreshCtx, cancel := context.WithCancel(ctx)
	presenceChan := make(chan PresenceKind, 1)
	done := make(chan struct{})

	s.presenceMutex.Lock()
	s.presenceChan = presenceChan
	s.presenceCancel = cancel
	s.presenceDone = done
	s.presenceMutex.Unlock()

	go func() {
		defer close(done)
		newTimer := func(d time.Duration) *time.Timer {
			return time.NewTimer(d + time.Duration(rand.Int63n(int64(d))-int64(d/2)))
		}
		timer := newTimer(presenceRefreshInterval)
		timerStopped := false
		presence := PresenceAvailable
		defer func() {
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
		}()

		for {
			select {
			case <-refreshCtx.Done():
				return
			case <-timer.C:
				if presence == PresenceAvailable {
					_ = s.subscribeToPresences(refreshCtx, client)
					timer = newTimer(presenceRefreshInterval)
					timerStopped = false
				} else {
					timerStopped = true
				}
			case presence = <-presenceChan:
				if timerStopped && presence == PresenceAvailable {
					_ = s.subscribeToPresences(refreshCtx, client)
					timer = newTimer(presenceRefreshInterval)
					timerStopped = false
				}
			}
		}
	}()
}

func (s *Session) stopPresenceRefresh() {
	s.presenceMutex.Lock()
	cancel := s.presenceCancel
	done := s.presenceDone
	s.presenceChan = nil
	s.presenceCancel = nil
	s.presenceDone = nil
	s.presenceMutex.Unlock()

	if cancel != nil {
		cancel()
	}
	if done != nil {
		<-done
	}
}

func (s *Session) queuePresenceRefresh(presence PresenceKind) {
	s.presenceMutex.Lock()
	presenceChan := s.presenceChan
	s.presenceMutex.Unlock()
	if presenceChan == nil {
		return
	}

	select {
	case presenceChan <- presence:
	default:
		// Only the latest availability matters to the refresh scheduler.
		select {
		case <-presenceChan:
		default:
		}
		select {
		case presenceChan <- presence:
		default:
		}
	}
}

'''

OLD_LOGIN_SETUP = '''\ts.client = whatsmeow.NewClient(store, s.gateway.logger)
\ts.client.AddEventHandler(s.handleEvent)
\ts.client.AutomaticMessageRerequestFromPhone = true

\t// Refresh contact presences on a set interval, to avoid issues with WhatsApp dropping them
\t// entirely. Contact presences are refreshed only if our current status is set to "available";
\t// otherwise, a refresh is queued up for whenever our status changes back to "available".
\ts.presenceChan = make(chan PresenceKind, 1)
\tgo func() {
\t\tvar newTimer = func(d time.Duration) *time.Timer {
\t\t\treturn time.NewTimer(d + time.Duration(rand.Int63n(int64(d))-int64(d/2)))
\t\t}
\t\tvar timer, timerStopped = newTimer(presenceRefreshInterval), false
\t\tvar presence = PresenceAvailable
\t\tfor {
\t\t\tselect {
\t\t\tcase <-timer.C:
\t\t\t\tif presence == PresenceAvailable {
\t\t\t\t\ts.SubscribeToPresences()
\t\t\t\t\ttimer, timerStopped = newTimer(presenceRefreshInterval), false
\t\t\t\t} else {
\t\t\t\t\ttimerStopped = true
\t\t\t\t}
\t\t\tcase p, ok := <-s.presenceChan:
\t\t\t\tif !ok && !timerStopped {
\t\t\t\t\tif !timer.Stop() {
\t\t\t\t\t\t<-timer.C
\t\t\t\t\t}
\t\t\t\t\treturn
\t\t\t\t} else if timerStopped && p == PresenceAvailable {
\t\t\t\t\ts.SubscribeToPresences()
\t\t\t\t\ttimer, timerStopped = newTimer(presenceRefreshInterval), false
\t\t\t\t}
\t\t\t\tpresence = p
\t\t\t}
\t\t}
\t}()
'''

NEW_LOGIN_SETUP = '''\tclient := whatsmeow.NewClient(store, s.gateway.logger)
\ts.setClient(client)
\tclient.AddEventHandler(s.handleEvent)
\tclient.AutomaticMessageRerequestFromPhone = true

\t// Refresh contact presences on a set interval, to avoid issues with WhatsApp dropping them.
\t// The refresher owns a client snapshot and is stopped before session cleanup.
\ts.startPresenceRefresh(s.ctx, client)
'''

OLD_LOGIN_CONNECTION = '''\t// Simply connect our client if already registered.
\tif s.client.Store.ID != nil {
\t\treturn s.client.ConnectContext(s.ctx)
\t}

\t// Attempt out-of-band registration of client via QR code.
\tqrChan, _ := s.client.GetQRChannel(s.ctx)
\tif err = s.client.ConnectContext(s.ctx); err != nil {
\t\treturn err
\t}
'''

NEW_LOGIN_CONNECTION = '''\t// Simply connect our client if already registered.
\tif client.Store.ID != nil {
\t\treturn client.ConnectContext(s.ctx)
\t}

\t// Attempt out-of-band registration of client via QR code.
\tqrChan, _ := client.GetQRChannel(s.ctx)
\tif err = client.ConnectContext(s.ctx); err != nil {
\t\ts.stopPresenceRefresh()
\t\ts.clearClient(client)
\t\ts.ctxCancel(err)
\t\treturn err
\t}
'''

OLD_TEARDOWN = '''func (s *Session) Logout() error {
\tif s.client == nil || s.client.Store.ID == nil {
\t\treturn nil
\t}

\terr := s.client.Logout(s.ctx)
\ts.client = nil
\ts.ctxCancel(nil)
\tclose(s.presenceChan)

\treturn err
}

// Disconnects detaches the current connection to WhatsApp without removing any linked device state.
func (s *Session) Disconnect() error {
\tif s.client == nil {
\t\treturn nil
\t}

\ts.client.Disconnect()
\ts.ctxCancel(nil)
\ts.client = nil
\tclose(s.presenceChan)

\treturn nil
}
'''

NEW_TEARDOWN = '''func (s *Session) Logout() error {
\tclient := s.currentClient()
\ts.stopPresenceRefresh()
\tif client == nil || client.Store == nil || client.Store.ID == nil {
\t\treturn nil
\t}

\terr := client.Logout(s.ctx)
\ts.ctxCancel(nil)
\ts.clearClient(client)
\treturn err
}

// Disconnects detaches the current connection to WhatsApp without removing any linked device state.
func (s *Session) Disconnect() error {
\tclient := s.currentClient()
\ts.stopPresenceRefresh()
\tif client == nil {
\t\treturn nil
\t}

\tclient.Disconnect()
\ts.ctxCancel(nil)
\ts.clearClient(client)
\treturn nil
}
'''

OLD_SEND_PRESENCE = '''func (s *Session) SendPresence(presence PresenceKind, statusMessage string) error {
\tif s.client == nil || s.client.Store.ID == nil {
\t\treturn fmt.Errorf("cannot send presence for unauthenticated session")
\t}

\tvar err error
\ts.presenceChan <- presence

\tswitch presence {
\tcase PresenceAvailable:
\t\terr = s.client.SendPresence(s.ctx, types.PresenceAvailable)
\tcase PresenceUnavailable:
\t\terr = s.client.SendPresence(s.ctx, types.PresenceUnavailable)
\t}

\tif err == nil && statusMessage != "" {
\t\terr = s.client.SetStatusMessage(s.ctx, statusMessage)
\t}

\treturn err
}
'''

NEW_SEND_PRESENCE = '''func (s *Session) SendPresence(presence PresenceKind, statusMessage string) error {
\tclient := s.currentClient()
\tif client == nil || client.Store == nil || client.Store.ID == nil {
\t\treturn fmt.Errorf("cannot send presence for unauthenticated session")
\t}

\tvar err error
\ts.queuePresenceRefresh(presence)

\tswitch presence {
\tcase PresenceAvailable:
\t\terr = client.SendPresence(s.ctx, types.PresenceAvailable)
\tcase PresenceUnavailable:
\t\terr = client.SendPresence(s.ctx, types.PresenceUnavailable)
\t}

\tif err == nil && statusMessage != "" {
\t\terr = client.SetStatusMessage(s.ctx, statusMessage)
\t}

\treturn err
}
'''

OLD_SUBSCRIBE = '''func (s *Session) SubscribeToPresences() error {
\tdata, err := s.client.Store.Contacts.GetAllContacts(s.ctx)
\tif err != nil {
\t\treturn fmt.Errorf("failed getting local contacts: %s", err)
\t}
\tfor jid := range data {
\t\tif jid.Server != types.DefaultUserServer {
\t\t\tcontinue
\t\t}

\t\tif err = s.client.SubscribePresence(s.ctx, jid); err != nil {
\t\t\ts.gateway.logger.Debugf("Failed to subscribe to presence for %s", jid)
\t\t}
\t}
\treturn nil
}
'''

NEW_SUBSCRIBE = '''func (s *Session) SubscribeToPresences() error {
\treturn s.subscribeToPresences(s.ctx, s.currentClient())
}

func (s *Session) subscribeToPresences(ctx context.Context, client *whatsmeow.Client) error {
\tif client == nil || client.Store == nil || client.Store.Contacts == nil {
\t\treturn nil
\t}

\tdata, err := client.Store.Contacts.GetAllContacts(ctx)
\tif err != nil {
\t\treturn fmt.Errorf("failed getting local contacts: %s", err)
\t}
\tfor jid := range data {
\t\tif jid.Server != types.DefaultUserServer {
\t\t\tcontinue
\t\t}

\t\tif err = client.SubscribePresence(ctx, jid); err != nil {
\t\t\ts.gateway.logger.Debugf("Failed to subscribe to presence for %s", jid)
\t\t}
\t}
\treturn nil
}
'''

OLD_LOGGED_OUT = '''\tcase *events.LoggedOut:
\t\ts.client.Disconnect()
\t\tif err := s.client.Store.Delete(s.ctx); err != nil {
\t\t\ts.gateway.logger.Warnf("Unable to delete local device state on logout: %s", err)
\t\t}
\t\ts.client = nil
\t\ts.propagateEvent(EventLoggedOut, &EventPayload{LoggedOut: LoggedOut{Reason: evt.Reason.String()}})
'''

NEW_LOGGED_OUT = '''\tcase *events.LoggedOut:
\t\ts.stopPresenceRefresh()
\t\tif client != nil {
\t\t\tclient.Disconnect()
\t\t\tif client.Store != nil {
\t\t\t\tif err := client.Store.Delete(s.ctx); err != nil {
\t\t\t\t\ts.gateway.logger.Warnf("Unable to delete local device state on logout: %s", err)
\t\t\t\t}
\t\t\t}
\t\t\ts.clearClient(client)
\t\t}
\t\ts.ctxCancel(nil)
\t\ts.propagateEvent(EventLoggedOut, &EventPayload{LoggedOut: LoggedOut{Reason: evt.Reason.String()}})
'''

OLD_HANDLE_EVENT_START = '''func (s *Session) handleEvent(evt any) {
\ts.gateway.logger.Debugf("Handling event '%T': %+v", evt, jsonStringer{evt})
'''

NEW_HANDLE_EVENT_START = '''func (s *Session) handleEvent(evt any) {
\tclient := s.currentClient()
\tif client == nil {
\t\treturn
\t}
\ts.gateway.logger.Debugf("Handling event '%T': %+v", evt, jsonStringer{evt})
'''

HANDLE_EVENT_END = "\n}\n\n// a JSONStringer"

PATCH_MARKER = "func (s *Session) startPresenceRefresh("


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not patch session.go: expected one {description}, found {count}."
        )
    return text.replace(old, new, 1)


def patch_session(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return False

    updated = replace_once(
        text, OLD_SESSION_FIELDS, NEW_SESSION_FIELDS, "Session field block"
    )
    updated = replace_once(
        updated,
        LOGIN_ANCHOR,
        LIFECYCLE_HELPERS + LOGIN_ANCHOR,
        "Login comment anchor",
    )
    updated = replace_once(
        updated, OLD_LOGIN_SETUP, NEW_LOGIN_SETUP, "presence refresh goroutine"
    )
    updated = replace_once(
        updated, OLD_LOGIN_CONNECTION, NEW_LOGIN_CONNECTION, "Login connection block"
    )
    updated = replace_once(updated, OLD_TEARDOWN, NEW_TEARDOWN, "teardown block")
    updated = replace_once(
        updated, OLD_SEND_PRESENCE, NEW_SEND_PRESENCE, "SendPresence function"
    )
    updated = replace_once(
        updated, OLD_SUBSCRIBE, NEW_SUBSCRIBE, "SubscribeToPresences function"
    )
    updated = replace_once(
        updated, OLD_LOGGED_OUT, NEW_LOGGED_OUT, "LoggedOut event block"
    )
    updated = replace_once(
        updated,
        OLD_HANDLE_EVENT_START,
        NEW_HANDLE_EVENT_START,
        "handleEvent client snapshot",
    )

    handle_start = updated.index(NEW_HANDLE_EVENT_START)
    handle_end = updated.index(HANDLE_EVENT_END, handle_start)
    handle_source = updated[handle_start:handle_end]
    direct_client_uses = handle_source.count("s.client")
    if direct_client_uses < 1:
        raise SystemExit(
            "Could not patch session.go: handleEvent had no direct client uses."
        )
    handle_source = handle_source.replace("s.client", "client")
    updated = updated[:handle_start] + handle_source + updated[handle_end:]

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-presence-lifecycle")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so periodic presence refresh cannot race "
            "with session teardown."
        )
    )
    parser.add_argument(
        "package_root",
        type=Path,
        help="Path containing the installed slidge_whatsapp package.",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    target = args.package_root.resolve() / "slidge_whatsapp" / "session.go"
    if not target.is_file():
        raise SystemExit(f"File not found: {target}")

    changed = patch_session(target, backup=not args.no_backup)
    print(
        "Presence refresh lifecycle patch applied; rebuild the bridge."
        if changed
        else "Presence refresh lifecycle patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
