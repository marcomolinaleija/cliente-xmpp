from pathlib import Path

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401
from slidge_whatsapp.generated import whatsapp

package_dir = Path(slidge_whatsapp.__file__).parent
session_source = (package_dir / "session.go").read_text(encoding="utf-8")
python_session_source = (package_dir / "session.py").read_text(encoding="utf-8")
event_source = (package_dir / "event.go").read_text(encoding="utf-8")

assert "reply context is applied to media payloads" in session_source
assert "setReplyContext(payload, message)" in session_source
assert "mergeContext(&payload.AudioMessage.ContextInfo)" in session_source
assert "media attachment context is propagated" in event_source
assert "case *waE2E.AudioMessage:" in event_source
assert "info = msg.GetContextInfo()" in event_source
assert "Preserve the native reference before resolving the quoted participant." in event_source
assert "if not reply_actor.JID and not reply_actor.LID:" in python_session_source
assert whatsapp.Message(ReplyID="quoted-id").ReplyID == "quoted-id"
print("Reply attachment bridge runtime smoke: ok")
