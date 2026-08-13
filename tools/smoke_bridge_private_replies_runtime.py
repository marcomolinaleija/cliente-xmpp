from __future__ import annotations

from pathlib import Path

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401
from slidge_whatsapp.generated import whatsapp

package_dir = Path(slidge_whatsapp.__file__).parent
site_packages = package_dir.parent
event_source = (package_dir / "event.go").read_text(encoding="utf-8")
session_go_source = (package_dir / "session.go").read_text(encoding="utf-8")
session_py_source = (package_dir / "session.py").read_text(encoding="utf-8")
contact_source = (package_dir / "contact.py").read_text(encoding="utf-8")
dispatcher_source = (site_packages / "slidge/core/dispatcher/message/message.py").read_text(
    encoding="utf-8"
)

assert 'const privateReplyGroupSeparator = "\\x1f"' in event_source
assert "contextInfo.RemoteJID = &quotedGroupJID" in session_go_source
assert "private group reply retains the original WhatsApp group" in contact_source
assert "def _private_group_reply_context(" in session_py_source
assert "private group reply must reference its MUC" in dispatcher_source
assert whatsapp.Actor(LID="participant@lid\\x1f120363000000000000@g.us").LID.endswith("@g.us")
print("Private group reply bridge runtime smoke: ok")
