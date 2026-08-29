from __future__ import annotations

LOCAL_BRIDGE_COMMANDS = frozenset({"/stats", "/status", "/transcribe"})


def is_local_bridge_command(body: str) -> bool:
    """Return whether text is consumed locally by the WhatsApp bridge."""
    parts = body.strip().casefold().split(maxsplit=1)
    return bool(parts and parts[0] in LOCAL_BRIDGE_COMMANDS)
