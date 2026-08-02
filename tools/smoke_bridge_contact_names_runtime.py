from __future__ import annotations

from pathlib import Path

import slidge_whatsapp

event_source = Path(slidge_whatsapp.__file__).with_name("event.go").read_text(
    encoding="utf-8"
)

live_start = event_source.index("func newContactEvent(")
live_end = event_source.index("\n}\n", live_start) + 3
live_source = event_source[live_start:live_end]

assert "storedContactInfo(ctx, client, actor, evt.JID, jid, lid)" in live_source
assert "contactInfo.FullName = fullName" in live_source

history_start = event_source.index("func newContactEventFromHistory(")
history_end = event_source.index("\n}\n", history_start) + 3
history_source = event_source[history_start:history_end]

assert "storedContactInfo(ctx, client, actor, jid)" in history_source
assert "contactInfo.PushName = evt.GetPushname()" in history_source
assert "types.ContactInfo{PushName: evt.GetPushname()}" not in history_source

session_source = Path(slidge_whatsapp.__file__).with_name("session.py").read_text(
    encoding="utf-8"
)
sync_start = session_source.index("    async def __sync_roster_after_connect(")
sync_end = session_source.index("\n    async def ", sync_start + 1)
sync_source = session_source[sync_start:sync_end]

assert "wa_contacts = list(" in sync_source
assert "self.whatsapp.GetContacts(refresh=True)" in sync_source
assert "await asyncio.sleep(5)" in sync_source
assert "self.__authoritative_saved_contacts" in sync_source
assert "self.contacts.add_whatsapp_contact(wa_contact)" in sync_source

handler_start = session_source.index("    async def on_wa_contact(")
handler_end = session_source.index("\n    async def ", handler_start + 1)
handler_source = session_source[handler_start:handler_end]

assert "authoritative_contacts.get(canonical_id, wa_contact)" in handler_source

go_session_source = Path(slidge_whatsapp.__file__).with_name("session.go").read_text(
    encoding="utf-8"
)
assert "func preferContactCandidate(" in go_session_source
assert "contactsByJID := make(map[string]Contact)" in go_session_source
assert "preferContactCandidate(current, candidate)" in go_session_source

print("saved WhatsApp contact names runtime smoke: ok")
