from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OLD_HISTORY_CONTACT = (
    "func newContactEventFromHistory(ctx context.Context, client *whatsmeow.Client, "
    "evt *waHistorySync.Pushname) (EventKind, *EventPayload) {\n"
    '''	jid, _ := types.ParseJID(evt.GetID())
	actor := newActor(ctx, client, jid)
	contact := newContact(client, actor, types.ContactInfo{PushName: evt.GetPushname()})
	return EventContact, &EventPayload{Contact: contact}
}
'''
)


OLD_LIVE_CONTACT = (
    "func newContactEvent(ctx context.Context, client *whatsmeow.Client, "
    "evt *events.Contact) (EventKind, *EventPayload) {\n"
    '''	lid, errlid := types.ParseJID(evt.Action.GetLidJID())
	jid, errjid := types.ParseJID(evt.Action.GetPnJID())

	if errlid != nil && errjid != nil {
		client.Log.Warnf("Ignoring contact event: %s (LID) %s (JID)", errlid, errjid)
		return EventUnknown, nil
	}

	actor := newActor(ctx, client, evt.JID, lid, jid)
	contact := newContact(client, actor, types.ContactInfo{
		FullName:  evt.Action.GetFullName(),
		FirstName: evt.Action.GetFirstName(),
		PushName:  evt.Action.GetUsername(), // Username === PushName?? maybe not
	})
	return EventContact, &EventPayload{Contact: contact}
}
'''
)


NEW_LIVE_CONTACT = '''func storedContactInfo(
	ctx context.Context,
	client *whatsmeow.Client,
	actor Actor,
	jids ...types.JID,
) types.ContactInfo {
	candidates := make([]types.JID, 0, len(jids)+1)
	if actor.JID != "" {
		if phoneJID, err := types.ParseJID(actor.JID); err == nil {
			candidates = append(candidates, phoneJID)
		}
	}
	candidates = append(candidates, jids...)

	seen := make(map[string]struct{}, len(candidates))
	var fallback types.ContactInfo
	for _, jid := range candidates {
		jid = jid.ToNonAD()
		key := jid.String()
		if jid.IsEmpty() || key == "" {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}

		info, err := client.Store.Contacts.GetContact(ctx, jid)
		if err != nil {
			client.Log.Warnf("Could not load stored contact info for %s: %v", jid, err)
			continue
		}
		if !info.Found {
			continue
		}
		if !fallback.Found {
			fallback = info
		}
		if info.FullName != "" || info.FirstName != "" {
			return info
		}
	}
	return fallback
}

func newContactEvent(
	ctx context.Context,
	client *whatsmeow.Client,
	evt *events.Contact,
) (EventKind, *EventPayload) {
	lid, errlid := types.ParseJID(evt.Action.GetLidJID())
	jid, errjid := types.ParseJID(evt.Action.GetPnJID())

	if errlid != nil && errjid != nil {
		client.Log.Warnf("Ignoring contact event: %s (LID) %s (JID)", errlid, errjid)
		return EventUnknown, nil
	}

	actor := newActor(ctx, client, evt.JID, lid, jid)
	contactInfo := storedContactInfo(ctx, client, actor, evt.JID, jid, lid)
	if fullName := evt.Action.GetFullName(); fullName != "" {
		contactInfo.FullName = fullName
	}
	if firstName := evt.Action.GetFirstName(); firstName != "" {
		contactInfo.FirstName = firstName
	}
	if pushName := evt.Action.GetUsername(); pushName != "" {
		contactInfo.PushName = pushName
	}
	contact := newContact(client, actor, contactInfo)
	return EventContact, &EventPayload{Contact: contact}
}
'''


NEW_HISTORY_CONTACT = '''func newContactEventFromHistory(
	ctx context.Context,
	client *whatsmeow.Client,
	evt *waHistorySync.Pushname,
) (EventKind, *EventPayload) {
	jid, _ := types.ParseJID(evt.GetID())
	actor := newActor(ctx, client, jid)
	contactInfo := storedContactInfo(ctx, client, actor, jid)
	contactInfo.PushName = evt.GetPushname()
	contact := newContact(client, actor, contactInfo)
	return EventContact, &EventPayload{Contact: contact}
}
'''


OLD_ROSTER_SYNC = '''    async def __sync_roster_after_connect(self) -> None:
        try:
            await self.contacts.ready
            result = await SyncContacts.sync(self, self, self.user_jid)  # type:ignore
            self.log.info("Automatic XMPP roster sync completed: %s", result)
'''


NEW_ROSTER_SYNC = '''    async def __sync_roster_after_connect(self) -> None:
        try:
            await self.contacts.ready
            wa_contacts = list(
                self.whatsapp.GetContacts(refresh=True)  # type:ignore
            )
            saved_contacts = sum(bool(contact.IsFriend) for contact in wa_contacts)
            self.log.info(
                "Loaded %d WhatsApp contacts, including %d saved contacts",
                len(wa_contacts),
                saved_contacts,
            )
            self.__authoritative_saved_contacts = {
                self.contacts._canonical_legacy_id(contact.Actor.JID): contact
                for contact in wa_contacts
                if contact.Actor.JID and contact.IsFriend
            }
            try:
                await asyncio.sleep(5)
                for wa_contact in wa_contacts:
                    await self.contacts.add_whatsapp_contact(wa_contact)
                result = await SyncContacts.sync(  # type:ignore
                    self, self, self.user_jid
                )
            finally:
                self.__authoritative_saved_contacts = {}
            self.log.info("Automatic XMPP roster sync completed: %s", result)
'''


OLD_CONTACT_HANDLER = '''    async def on_wa_contact(self, wa_contact: whatsapp.Contact) -> None:
        if wa_contact.Actor.JID:
            contact = await self.contacts.add_whatsapp_contact(wa_contact)
'''


NEW_CONTACT_HANDLER = '''    async def on_wa_contact(self, wa_contact: whatsapp.Contact) -> None:
        if wa_contact.Actor.JID:
            canonical_id = self.contacts._canonical_legacy_id(wa_contact.Actor.JID)
            authoritative_contacts = getattr(
                self, "_Session__authoritative_saved_contacts", {}
            )
            wa_contact = authoritative_contacts.get(canonical_id, wa_contact)
            contact = await self.contacts.add_whatsapp_contact(wa_contact)
'''


OLD_GET_CONTACTS = '''func (s *Session) GetContacts(refresh bool) ([]Contact, error) {
	if s.client == nil || s.client.Store.ID == nil {
		return nil, fmt.Errorf("cannot get contacts for unauthenticated session")
	}

	// Synchronize remote application state with local state if requested.
	if refresh {
		err := s.client.FetchAppState(s.ctx, appstate.WAPatchCriticalUnblockLow, false, false)
		if err != nil {
			s.gateway.logger.Warnf("Could not get app state from server: %s", err)
		}
	}

	// Synchronize local contact state with overarching gateway for all local contacts.
	data, err := s.client.Store.Contacts.GetAllContacts(s.ctx)
	if err != nil {
		return nil, fmt.Errorf("failed getting local contacts: %s", err)
	}

	var contacts []Contact
	for jid, info := range data {
		c := newContact(s.client, newActor(s.ctx, s.client, jid), info)
		contacts = append(contacts, c)
	}

	return contacts, nil
}
'''


NEW_GET_CONTACTS = '''func preferContactCandidate(current, candidate Contact) bool {
	if current.IsFriend != candidate.IsFriend {
		return candidate.IsFriend
	}
	return current.Name == "" && candidate.Name != ""
}

func (s *Session) GetContacts(refresh bool) ([]Contact, error) {
	if s.client == nil || s.client.Store.ID == nil {
		return nil, fmt.Errorf("cannot get contacts for unauthenticated session")
	}

	// Synchronize remote application state with local state if requested.
	if refresh {
		err := s.client.FetchAppState(s.ctx, appstate.WAPatchCriticalUnblockLow, false, false)
		if err != nil {
			s.gateway.logger.Warnf("Could not get app state from server: %s", err)
		}
	}

	// Synchronize local contact state with overarching gateway for all local contacts.
	data, err := s.client.Store.Contacts.GetAllContacts(s.ctx)
	if err != nil {
		return nil, fmt.Errorf("failed getting local contacts: %s", err)
	}

	contactsByJID := make(map[string]Contact)
	for jid, info := range data {
		candidate := newContact(s.client, newActor(s.ctx, s.client, jid), info)
		key := candidate.Actor.JID
		if key == "" {
			continue
		}
		current, found := contactsByJID[key]
		if !found || preferContactCandidate(current, candidate) {
			contactsByJID[key] = candidate
		}
	}

	contacts := make([]Contact, 0, len(contactsByJID))
	for _, contact := range contactsByJID {
		contacts = append(contacts, contact)
	}
	return contacts, nil
}
'''


def patch_source(
    path: Path,
    old_source: str,
    new_source: str,
    *,
    backup: bool,
) -> bool:
    text = path.read_text(encoding="utf-8")
    if new_source in text:
        return False
    if text.count(old_source) != 1:
        raise SystemExit(f"Could not find the expected contact-name block in {path}.")

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-contact-names")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    path.write_text(
        text.replace(old_source, new_source, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def patch_event_go(path: Path, *, backup: bool) -> bool:
    live_changed = patch_source(
        path,
        OLD_LIVE_CONTACT,
        NEW_LIVE_CONTACT,
        backup=backup,
    )
    history_changed = patch_source(
        path,
        OLD_HISTORY_CONTACT,
        NEW_HISTORY_CONTACT,
        backup=backup,
    )
    return live_changed or history_changed


def patch_session_py(path: Path, *, backup: bool) -> bool:
    roster_changed = patch_source(
        path,
        OLD_ROSTER_SYNC,
        NEW_ROSTER_SYNC,
        backup=backup,
    )
    handler_changed = patch_source(
        path,
        OLD_CONTACT_HANDLER,
        NEW_CONTACT_HANDLER,
        backup=backup,
    )
    return roster_changed or handler_changed


def patch_session_go(path: Path, *, backup: bool) -> bool:
    return patch_source(
        path,
        OLD_GET_CONTACTS,
        NEW_GET_CONTACTS,
        backup=backup,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so history push-name events preserve saved "
            "WhatsApp contact names."
        )
    )
    parser.add_argument(
        "slidge_whatsapp_tree",
        type=Path,
        help="Path to the root of a slidge-whatsapp checkout.",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    event_path = args.slidge_whatsapp_tree.resolve() / "slidge_whatsapp" / "event.go"
    session_path = (
        args.slidge_whatsapp_tree.resolve() / "slidge_whatsapp" / "session.py"
    )
    go_session_path = (
        args.slidge_whatsapp_tree.resolve() / "slidge_whatsapp" / "session.go"
    )
    for path in (event_path, session_path, go_session_path):
        if not path.is_file():
            raise SystemExit(f"File not found: {path}")
    event_changed = patch_event_go(event_path, backup=not args.no_backup)
    session_changed = patch_session_py(session_path, backup=not args.no_backup)
    go_session_changed = patch_session_go(
        go_session_path, backup=not args.no_backup
    )
    if event_changed or session_changed or go_session_changed:
        print("Contact-name preservation patch applied; rebuild the bridge image.")
    else:
        print("Contact-name preservation patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
