from pathlib import Path

import slidge_whatsapp
from slidge_whatsapp.generated import _whatsapp as _binding  # noqa: F401

root = Path(slidge_whatsapp.__file__).parent
session_go = (root / "session.go").read_text(encoding="utf-8")

assert "func passiveWhatsAppPresence(_ PresenceKind) types.Presence" in session_go
assert "return types.PresenceUnavailable" in session_go
assert "client.SendPresence(s.ctx, types.PresenceAvailable)" not in session_go
assert session_go.count("client.SendPresence(s.ctx, passiveWhatsAppPresence(") == 3
assert "return s.client.MarkRead(" in session_go
print("passive presence runtime smoke: ok")
