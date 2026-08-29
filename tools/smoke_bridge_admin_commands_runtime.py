from __future__ import annotations

from pathlib import Path

from slidge_whatsapp import mixins as mixins_module
from slidge_whatsapp import session as session_module

mixins_source = Path(mixins_module.__file__).read_text(encoding="utf-8")
session_source = Path(session_module.__file__).read_text(encoding="utf-8")
assert "handle_transcription_command(" in mixins_source
assert "self.session.send_gateway_message(command_response)" in mixins_source
assert "chat=str(self.jid.bare)" in mixins_source
assert 'local_sender = getattr(self, "send_text", None)' not in mixins_source
assert "participant.send_text(command_response)" not in mixins_source
assert "transcription_enabled(account, chat)" in session_source
assert "muc.jid.bare if muc is not None else actor.jid.bare" in session_source

command_position = mixins_source.index("handle_transcription_command(")
gateway_position = mixins_source.index(
    "self.session.send_gateway_message(command_response)"
)
whatsapp_position = mixins_source.index(
    "message_id: str = self.wa.GenerateMessageID()",
    gateway_position,
)
assert command_position < gateway_position < whatsapp_position

print("administrative transcription commands runtime smoke: ok")
