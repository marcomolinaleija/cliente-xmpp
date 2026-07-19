import inspect

from slidge_whatsapp.session import Session

session_source = inspect.getsource(Session)

assert "online(last_seen=datetime.now())" not in session_source
assert "async def on_wa_message(" in session_source
assert "message.Chat, message.Actor" in session_source
assert "match message.Kind:" in session_source

print("message presence runtime smoke: ok")
