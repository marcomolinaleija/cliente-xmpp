from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path

RAYOAI_HOST = "127.0.0.1"
RAYOAI_PORT = 16180
RAYOAI_TIMEOUT_SECONDS = 1.5
RAYOAI_DESCRIPTION_TIMEOUT_SECONDS = 60.0
MAX_RESPONSE_BYTES = 64 * 1024
DESCRIPTION_INSTRUCTION = (
    "Describe correctamente el contenido visible para una persona ciega. "
    "Devuelve solo una descripción breve y precisa en español; no inventes detalles, "
    "no menciones que estás describiendo una imagen y no incluyas formato ni explicaciones."
)


def send_open_path(path: str | Path) -> bool:
    return send_payload({"cmd": "open", "path": str(Path(path).resolve())})


def request_description(path: str | Path) -> str | None:
    """Request a description and wait for the matching local IPC response."""
    request_id = uuid.uuid4().hex
    try:
        payload = {
            "cmd": "describe",
            "request_id": request_id,
            "path": str(Path(path).resolve()),
            "instruction": DESCRIPTION_INSTRUCTION,
        }
        with socket.create_connection(
            (RAYOAI_HOST, RAYOAI_PORT),
            timeout=RAYOAI_DESCRIPTION_TIMEOUT_SECONDS,
        ) as conn:
            conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            response = _receive_json_line(conn)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None

    if response.get("request_id") != request_id or response.get("ok") is not True:
        return None
    description = response.get("description")
    if not isinstance(description, str):
        return None
    description = " ".join(description.split()).strip()
    return description or None


def _receive_json_line(conn: socket.socket) -> dict[str, object]:
    data = bytearray()
    while len(data) <= MAX_RESPONSE_BYTES:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("RayoAI response too large")
        if b"\n" in chunk:
            break
    line = bytes(data).split(b"\n", 1)[0].strip()
    if not line:
        raise ValueError("empty RayoAI response")
    response = json.loads(line.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("invalid RayoAI response")
    return response


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
