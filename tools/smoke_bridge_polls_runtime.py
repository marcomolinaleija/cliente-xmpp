from __future__ import annotations

# ruff: noqa: I001

from pathlib import Path

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401


package_dir = Path(slidge_whatsapp.__file__).parent
session_py = (package_dir / "session.py").read_text(encoding="utf-8")
mixins_py = (package_dir / "mixins.py").read_text(encoding="utf-8")
event_go = (package_dir / "event.go").read_text(encoding="utf-8")
session_go = (package_dir / "session.go").read_text(encoding="utf-8")
dispatcher_py = (
    package_dir.parent / "slidge/core/dispatcher/message/message.py"
).read_text(encoding="utf-8")
message_text_py = (
    package_dir.parent / "slidge/core/mixins/message_text.py"
).read_text(encoding="utf-8")

required = (
    "urn:marco-ml:whatsapp:poll:0",
    "extra_xml=poll_xml",
    "creator-lid",
    "max-selections",
    "extra_xml: ET.Element | None = None",
    "msg.xml.append(extra_xml)",
    "__dispatch_poll_vote",
    "await recipient.on_poll_vote(",
    "async def on_poll_vote(",
    "whatsapp.MessagePoll",
    "strconv.Itoa(int(p.GetSelectableOptionsCount()))",
    "func pollVoteInfo(message Message, chat types.JID)",
    "s.client.BuildPollVote(s.ctx, pollInfo, optionNames)",
)
combined = "\n".join((session_py, mixins_py, event_go, session_go, dispatcher_py, message_text_py))
for fragment in required:
    assert fragment in combined, f"missing poll bridge fragment: {fragment}"

binary = package_dir / "generated" / "_whatsapp.cpython-313-x86_64-linux-gnu.so"
assert binary.is_file(), "rebuilt Go binding is missing"
print("WhatsApp poll bridge runtime smoke: ok")
