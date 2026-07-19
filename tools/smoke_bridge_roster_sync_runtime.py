from __future__ import annotations

import inspect

from slidge_whatsapp.contact import Roster, _modern_mexico_legacy_id
from slidge_whatsapp.session import Session

OLD = "5214491234567@s.whatsapp.net"
MODERN = "524491234567@s.whatsapp.net"


assert _modern_mexico_legacy_id(OLD) == MODERN
assert _modern_mexico_legacy_id(MODERN) is None
assert _modern_mexico_legacy_id("521123@s.whatsapp.net") is None

roster = object.__new__(Roster)
roster._mexico_aliases = {OLD: MODERN}
assert roster._canonical_legacy_id(OLD) == MODERN
assert roster._canonical_legacy_id(MODERN) == MODERN

contact_source = inspect.getsource(Roster)
session_source = inspect.getsource(Session)
assert "contacts_by_legacy_id" in contact_source
assert "refresh=config.ALWAYS_SYNC_ROSTER" in contact_source
assert "Automatic XMPP roster sync completed" in session_source
assert "await self.contacts.ready" in session_source
assert "SyncContacts.sync" in session_source

print("automatic roster sync runtime smoke: ok")
