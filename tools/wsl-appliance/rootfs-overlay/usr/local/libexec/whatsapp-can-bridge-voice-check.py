"""Voice capability check run inside the bridge image, without accounts or network.

The appliance passes this file on stdin so it does not depend on where each
bridge image keeps its own test tools.
"""
import os

from slidge_whatsapp.generated import whatsapp
from slidge_whatsapp.voice import Tokens, secure_url

assert os.environ.get("CAN_VOICE_ENABLE") == "0"
assert secure_url("wss://voice.example.invalid/voice/v1")
assert not Tokens().consume("missing")
gateway = whatsapp.NewGateway()
assert gateway is not None
print("PASS native binding + voice import; no account or network")
