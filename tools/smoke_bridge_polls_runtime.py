from __future__ import annotations

import inspect
from pathlib import Path
from xml.etree import ElementTree as ET

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401
from slidge.core.dispatcher.message.message import MessageContentMixin
from slidge.core.mixins.message_text import TextMessageMixin
from slidge_whatsapp.mixins import RecipientMixin

POLL_NAMESPACE = "urn:marco-ml:whatsapp:poll:0"
package_dir = Path(slidge_whatsapp.__file__).parent
site_packages = package_dir.parent

event_source = (package_dir / "event.go").read_text(encoding="utf-8")
session_go_source = (package_dir / "session.go").read_text(encoding="utf-8")
session_py_source = (package_dir / "session.py").read_text(encoding="utf-8")
mixins_source = (package_dir / "mixins.py").read_text(encoding="utf-8")
dispatcher_source = (
    site_packages / "slidge/core/dispatcher/message/message.py"
).read_text(encoding="utf-8")

assert "pollSelectableCount(p, version.zeroValueFallback)" in event_source
assert "pollSelectionMode(p, version.zeroValueFallback)" in event_source
assert 'fmt.Sprintf("%s:%d", selectionMode, selectableCount)' in event_source
assert "{evt.Message.GetPollCreationMessageV3(), 1}" in event_source
assert "{evt.Message.GetPollCreationMessage(), 0}" in event_source
assert "client.DecryptPollVote(ctx, evt)" in event_source
assert "Received WhatsApp poll update event=%s" in event_source
assert "func setPollUpdateMessage(" in event_source
assert "client.ParseWebMessage(chatJID, info)" in event_source
assert "func pollVoteMessageInfo(" in session_go_source
assert "direct poll recipient LID" in session_go_source
assert "s.client.BuildPollVote(s.ctx, pollInfo, optionNames)" in session_go_source
assert "jid = pollInfo.Chat" in session_go_source
assert f'POLL_NAMESPACE = "{POLL_NAMESPACE}"' in session_py_source
assert 'f"{{{POLL_NAMESPACE}}}poll-update"' in session_py_source
assert '"selection-mode": selection_mode' in session_py_source
assert 'body=" "' in session_py_source
assert f'WHATSAPP_POLL_NAMESPACE = "{POLL_NAMESPACE}"' in dispatcher_source
assert "Received poll vote id=%s" in dispatcher_source
assert "await recipient.on_poll_vote(" in dispatcher_source
assert "Confirmed poll vote id=%s stanza_id=%s" in dispatcher_source
assert 'confirmation["body"] = " "' in dispatcher_source
assert '"voter-is-me": "true"' in dispatcher_source
assert 'hashlib.sha256(option.encode("utf-8")).hexdigest()' in dispatcher_source
assert "extra_xml" in inspect.signature(TextMessageMixin.send_text).parameters
assert hasattr(RecipientMixin, "on_poll_vote")
assert "WhatsApp poll vote accepted id=%s" in mixins_source
assert hasattr(MessageContentMixin, "on_legacy_message")

poll = ET.Element(
    f"{{{POLL_NAMESPACE}}}poll",
    {
        "id": "poll-smoke",
        "title": "¿Café o té?",
        "creator": "5215587654321@s.whatsapp.net",
        "creator-lid": "123456789012345@lid",
        "creator-is-me": "false",
        "max-selections": "1",
    },
)
ET.SubElement(poll, f"{{{POLL_NAMESPACE}}}option").text = "Café"
assert poll.tag == f"{{{POLL_NAMESPACE}}}poll"
assert poll.find(f"{{{POLL_NAMESPACE}}}option").text == "Café"

update = ET.Element(
    f"{{{POLL_NAMESPACE}}}poll-update",
    {
        "id": "poll-smoke",
        "voter": "5215587654321@s.whatsapp.net",
        "voter-lid": "123456789012345@lid",
        "voter-is-me": "false",
    },
)
ET.SubElement(update, f"{{{POLL_NAMESPACE}}}option", {"hash": "ab" * 32})
assert update.attrib["id"] == "poll-smoke"
assert update.find(f"{{{POLL_NAMESPACE}}}option").attrib["hash"] == "ab" * 32

binary = package_dir / "generated" / "_whatsapp.cpython-313-x86_64-linux-gnu.so"
assert binary.is_file(), "rebuilt Go binding is missing"

print("WhatsApp poll bridge runtime smoke: ok")
