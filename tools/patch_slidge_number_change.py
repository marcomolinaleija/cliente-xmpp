from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ruff: noqa: E501 -- exact upstream source anchors


def replace_once(source: str, old: str, new: str) -> str:
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("Number-change patch: expected exactly one source anchor")
    return source.replace(old, new, 1)


def patch_room(source: str) -> str:
    source = replace_once(source, '''        p = await self.get_participant(
            self.user_nick,
            is_user=True,
''', '''        # CAN: own identity survives phone changes; nicknames are not unique identities.
        if not occupant_id:
            occupant_id = getattr(self.session, "_can_own_occupant_id", None)
        if not occupant_id and self.stored.id is not None:
            with self.xmpp.store.session() as orm:
                own_ids = [row[0] for row in orm.query(Participant.occupant_id).filter_by(
                    room_id=self.stored.id, is_user=True
                ).all()]
            # Keep old aliases and archives untouched. Never choose an arbitrary old row.
            if len(own_ids) == 1:
                occupant_id = own_ids[0]
        occupant_id = occupant_id or "slidge-user"
        p = await self.get_participant(
            self.user_nick,
            is_user=True,
''')
    return replace_once(source, '''        self.__store_participant(p)
        return p

    def __store_participant''', '''        # An exact own occupant lookup may enrich an earlier anonymous row.
        if not p.is_user:
            p.is_user = True
        self.__store_participant(p)
        return p

    def __store_participant''')


def patch_group(source: str) -> str:
    return replace_once(source, '''        if actor.IsMe:
            return await self.get_user_participant(occupant_id=actor.LID)
''', '''        if actor.IsMe:
            # CAN: cache only authoritative own identity from current group metadata.
            if actor.LID:
                self.session._can_own_occupant_id = actor.LID
            return await self.get_user_participant(occupant_id=actor.LID or None)
''')


def patch_session(source: str) -> str:
    old = '''        # On re-pair, Session.login() is not called by slidge core, so the status message is
        # not updated.
'''
    new = '''        # CAN: re-pair can change the phone identity even after the login future completed.
        if not connect.Error and connect.JID:
            if self.contacts.user_legacy_id != connect.JID:
                self._can_own_occupant_id = None
            self.contacts.user_legacy_id = connect.JID
            self.user_phone = "+" + connect.JID.split("@")[0]
        # On re-pair, Session.login() is not called by slidge core, so the status message is
        # not updated.
'''
    return replace_once(source, old, new)


def patch_event(source: str) -> str:
    source = replace_once(source, '''		actor.IsMe = anonID.ToNonAD() == client.Store.GetLID().ToNonAD()
''', '''		// CAN: a stale PN/LID mapping must not erase authenticated own-PN evidence.
		actor.IsMe = actor.IsMe || anonID.ToNonAD() == client.Store.GetLID().ToNonAD()
''')
    source = replace_once(source, '''	if actor.IsMe {
		if actor.JID == "" {
			actor.JID = client.Store.GetJID().ToNonAD().String()
		}
		if actor.LID == "" {
			actor.LID = client.Store.GetLID().ToNonAD().String()
		}
	}
''', '''	if actor.IsMe {
		// CAN: use the authenticated device identity, not an obsolete own alias.
		if own := client.Store.GetJID(); !own.IsEmpty() {
			actor.JID = own.ToNonAD().String()
		}
		if own := client.Store.GetLID(); !own.IsEmpty() {
			actor.LID = own.ToNonAD().String()
		}
	}
''')
    new_history = '''	// CAN: keep the room identity separate from the sender, including old-number history.
	chatJID, err := types.ParseJID(info.GetKey().GetRemoteJID())
	if err != nil {
		return EventUnknown, nil
	}
	message.Chat = newChat(ctx, client, chatJID, true)
	message.Actor, ok = canHistoricalActor(ctx, client, info.GetKey(), info.GetParticipant())
	if !ok {
		return EventUnknown, nil
	}
'''.replace("message.Actor, ok =", "var ok bool\n\tmessage.Actor, ok =")
    if "// CAN: keep the room identity separate" not in source:
        start = source.index("\tif info.Participant != nil {", source.index("func newEventFromHistory("))
        end = source.index("\n\t// Handle handle protocol messages", start)
        source = source[:start] + new_history + source[end:]
    source = replace_once(source, '''			jid, err := types.ParseJID(r.GetKey().GetParticipant())
			if err != nil {
				continue
			}
			message.Reactions = append(message.Reactions, Message{
''', '''			reactor, known := canHistoricalActor(ctx, client, r.GetKey(), "")
			if !known {
				// No sender evidence: do not fabricate an author or fail the parent message.
				continue
			}
			message.Reactions = append(message.Reactions, Message{
''')
    # This anchor occurs only in the historical reactions block.
    source = replace_once(source, '''				Actor:     newActor(ctx, client, jid),
				Body:      r.GetText(),
''', '''				Actor:     reactor,
				Body:      r.GetText(),
''')
    return source


def patch_package(site: Path, *, backup: bool = True) -> bool:
    transforms = {
        "slidge/group/room.py": patch_room,
        "slidge_whatsapp/group.py": patch_group,
        "slidge_whatsapp/session.py": patch_session,
        "slidge_whatsapp/event.go": patch_event,
    }
    # Validate all anchors before writing any of the files.
    updates = [(site / name, fn((site / name).read_text(encoding="utf-8")))
               for name, fn in transforms.items()]
    changed = False
    for path, updated in updates:
        if path.read_text(encoding="utf-8") == updated:
            continue
        if backup:
            shutil.copy2(path, path.with_suffix(path.suffix + ".before-number-change"))
        path.write_text(updated, encoding="utf-8", newline="\n")
        changed = True
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preserve own group identity after number changes")
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    print("Number-change patch:", patch_package(args.site_packages, backup=not args.no_backup))
