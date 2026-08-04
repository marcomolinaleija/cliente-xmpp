from __future__ import annotations

import inspect

from slidge_whatsapp.contact import Contact, Roster

LEGACY = "5214491234567@s.whatsapp.net"
MODERN = "524491234567@s.whatsapp.net"


roster = object.__new__(Roster)
roster._mexico_aliases = {LEGACY: MODERN}
roster._mexico_outbound_aliases = {MODERN: LEGACY}

assert roster._canonical_legacy_id(LEGACY) == MODERN
assert roster._canonical_legacy_id(MODERN) == MODERN
assert roster._outbound_legacy_id(MODERN) == LEGACY
assert roster._outbound_legacy_id(LEGACY) == LEGACY
assert roster._outbound_legacy_id("34622786982@s.whatsapp.net") == (
    "34622786982@s.whatsapp.net"
)

contact_source = inspect.getsource(Contact)
assert "def _wa_legacy_id(self)" in contact_source
assert "JID=self._wa_legacy_id()" in contact_source
assert "else self._wa_legacy_id()" in contact_source
assert "wa_msg.OriginActor.JID = self._wa_legacy_id()" in contact_source

print("Mexican outbound alias runtime smoke: ok")
