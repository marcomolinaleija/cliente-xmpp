import inspect

from slidge_whatsapp.session import Session

session_source = inspect.getsource(Session)

assert "contact.online(last_seen=datetime.now())" not in session_source
assert "contact.composing(media=state.Media)" in session_source
assert "contact.paused()" in session_source
assert "contact.displayed(legacy_msg_id=message_id, carbon=receipt.Actor.IsMe)" in session_source

print("presence sources runtime smoke: ok")
