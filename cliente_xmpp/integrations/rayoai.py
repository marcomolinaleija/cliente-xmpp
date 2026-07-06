from __future__ import annotations

import json
import socket
from pathlib import Path

RAYOAI_HOST = "127.0.0.1"
RAYOAI_PORT = 16180
RAYOAI_TIMEOUT_SECONDS = 1.5


def send_open_path(path: str | Path) -> bool:
    return send_payload({"cmd": "open", "path": str(Path(path).resolve())})


def send_focus() -> bool:
    return send_payload({"cmd": "focus"})


def send_payload(payload: dict[str, object]) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        with socket.create_connection(
            (RAYOAI_HOST, RAYOAI_PORT),
            timeout=RAYOAI_TIMEOUT_SECONDS,
        ) as conn:
            conn.sendall(data)
        return True
    except OSError:
        return False
