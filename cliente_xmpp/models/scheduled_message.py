from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from cliente_xmpp.models.local_commands import is_local_bridge_command

MAX_SCHEDULED_MESSAGE_CHARS = 10_000

POLICIES = {
    "hold": "Retener si no se puede enviar a tiempo (recomendado)",
    "send-when-connected": "Enviar al volver a conectar, aunque la hora haya pasado",
}
STATES = {
    "pending": "Pendiente",
    "held": "Retenido; requiere revisión",
    "dispatching": "Envío en curso; ya no se puede cancelar",
    "submitted": "Enviado al servicio; entrega no confirmada",
    "delivered": "Entregado",
    "read": "Leído",
    "failed": "Fallido; no se repite automáticamente",
    "uncertain": "Resultado incierto; comprueba el chat antes de repetir",
    "canceled": "Cancelado",
}


@dataclass(frozen=True)
class ScheduleRequest:
    request_id: str
    account: str
    jid: str
    text: str
    due: float
    late_policy: str


def validate_text(text: str, *, max_length: int = MAX_SCHEDULED_MESSAGE_CHARS) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > max_length:
        raise ValueError(f"Escribe un mensaje de 1 a {max_length} caracteres.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise ValueError("El mensaje contiene caracteres de control no permitidos.")
    if is_local_bridge_command(text):
        raise ValueError("No se pueden programar comandos locales del puente.")
    return text


def validate_due(due: float, policy: str, now: float, *, grace: int = 0) -> None:
    if policy not in POLICIES:
        raise ValueError("Selecciona qué hacer si el envío se retrasa.")
    # This also rejects NaN and infinities.
    if not now - grace < due <= now + 366 * 86400:
        raise ValueError("Elige una fecha futura, con un máximo de un año de anticipación.")


def parse_local_schedule(date: str, hour: str) -> datetime:
    if not re.fullmatch(r"\d{2}/\d{2}/\d{2}", date):
        example = datetime.now().strftime("%d/%m/%y")
        raise ValueError(f"Escribe día/mes/año con dos dígitos; por ejemplo, {example}.")
    if not re.fullmatch(r"\d{2}:\d{2}", hour):
        raise ValueError("Escribe la hora en formato de 24 horas, HH:MM; por ejemplo, 18:30.")
    try:
        day, month, year = map(int, date.split("/"))
        # Explicit 2000-2099 range: never use strptime's implicit two-digit-year pivot.
        local = datetime.strptime(
            f"{2000 + year:04d}-{month:02d}-{day:02d} {hour}", "%Y-%m-%d %H:%M"
        )
        # Use the OS rules for the chosen date, not today's fixed UTC offset.
        candidates = set()
        for daylight in (-1, 0, 1):
            stamp = time.mktime((*local.timetuple()[:8], daylight))
            if datetime.fromtimestamp(stamp) == local:
                candidates.add(stamp)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("La fecha u hora no es válida.") from exc
    if not candidates:
        raise ValueError("Esa hora no existe por un cambio de horario; elige otra.")
    if len(candidates) != 1:
        raise ValueError("Esa hora se repite por un cambio de horario; elige otra no ambigua.")
    return datetime.fromtimestamp(candidates.pop(), UTC).astimezone()


def format_due(due: float) -> str:
    return datetime.fromtimestamp(due, UTC).astimezone().strftime("%d/%m/%y %H:%M %Z (%z)")
