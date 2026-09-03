"""Shared, locale-friendly formatting helpers for user-visible values."""

from __future__ import annotations

import re
from datetime import datetime

MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_CALL_DURATION_PATTERN = re.compile(
    r",\s*\d+(?:[.,]\d+)?\s+seconds?\b",
    re.IGNORECASE,
)
_CALL_TIMESTAMP_PATTERN = re.compile(
    r"\s+at\s+\d{4}-\d{2}-\d{2}(?:[ T]\S+)?",
    re.IGNORECASE,
)
_CALL_TECHNICAL_JID_PATTERN = re.compile(r"\s+\(xmpp:[^)]+\)", re.IGNORECASE)
_CALL_MODERN_PREFIX_PATTERN = re.compile(
    r"^(?P<direction>incoming|outgoing)\s+"
    r"(?P<kind>voice|video)\s+call:\s+"
    r"(?P<outcome>[a-z_ ]+?)\s+(?P<link>with|from)\s+",
    re.IGNORECASE,
)

_CALL_DIRECTION_LABELS = {
    "incoming": "entrante",
    "outgoing": "saliente",
}
_CALL_KIND_LABELS = {
    "voice": "de voz",
    "video": "de video",
}
_CALL_OUTCOME_LABELS = {
    "connected": "conectada",
    "rejected": "rechazada",
    "cancelled": "cancelada",
    "accepted elsewhere": "contestada en otro dispositivo",
    "missed": "perdida",
    "invalid": "inválida",
    "unavailable": "no disponible",
    "upcoming": "programada",
    "failed": "fallida",
    "abandoned": "abandonada",
    "ongoing": "en curso",
    "silenced by dnd": "silenciada por no molestar",
    "silenced unknown caller": "silenciada por número desconocido",
}
_CALL_LEGACY_PREFIXES = (
    ("Incoming call from ", "Llamada entrante de "),
    ("Incoming call with ", "Llamada entrante de "),
    ("Missed call from ", "Llamada perdida de "),
    ("Missed call with ", "Llamada perdida con "),
    ("Call accepted from ", "Llamada contestada por "),
    ("Call accepted with ", "Llamada contestada con "),
    ("Call rejected from ", "Llamada rechazada por "),
    ("Call rejected with ", "Llamada rechazada con "),
    ("Call cancelled from ", "Llamada cancelada por "),
    ("Call cancelled with ", "Llamada cancelada con "),
    ("Call unavailable from ", "Llamada no disponible de "),
    ("Call unavailable with ", "Llamada no disponible con "),
    ("Call failed from ", "Llamada fallida de "),
    ("Call failed with ", "Llamada fallida con "),
    ("Call ongoing from ", "Llamada en curso con "),
    ("Call ongoing with ", "Llamada en curso con "),
    ("Call upcoming from ", "Llamada programada con "),
    ("Call upcoming with ", "Llamada programada con "),
    ("Call ended from ", "Llamada finalizada por "),
    ("Call ended with ", "Llamada finalizada con "),
    ("Incoming call", "Llamada entrante"),
    ("Missed call", "Llamada perdida"),
    ("Call accepted", "Llamada contestada"),
    ("Call rejected", "Llamada rechazada"),
    ("Call cancelled", "Llamada cancelada"),
    ("Call unavailable", "Llamada no disponible"),
    ("Call failed", "Llamada fallida"),
    ("Call ended", "Llamada finalizada"),
    ("Call", "Llamada"),
)


def _unit(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value} {singular if value == 1 else (plural or singular + 's')}"


def format_duration(seconds: float | int | None) -> str:
    """Render a duration with useful units without losing the meaningful part."""

    if seconds is None:
        return "sin datos suficientes"
    try:
        total_seconds = max(0, round(float(seconds)))
    except (TypeError, ValueError, OverflowError):
        return "sin datos suficientes"

    if total_seconds < 60:
        return _unit(total_seconds, "segundo", "segundos")

    minutes, remaining_seconds = divmod(total_seconds, 60)
    if total_seconds < 3600:
        parts = [_unit(minutes, "minuto", "minutos")]
        if remaining_seconds:
            parts.append(_unit(remaining_seconds, "segundo", "segundos"))
        return _join_units(parts)

    hours, remaining_minutes = divmod(minutes, 60)
    parts = [_unit(hours, "hora", "horas")]
    if remaining_minutes:
        parts.append(_unit(remaining_minutes, "minuto", "minutos"))
    return _join_units(parts)


def _join_units(parts: list[str]) -> str:
    if len(parts) <= 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} y {parts[1]}"
    return f"{', '.join(parts[:-1])} y {parts[-1]}"


def _translate_call_prefix(body: str) -> str:
    modern = _CALL_MODERN_PREFIX_PATTERN.match(body)
    if modern is not None:
        direction = _CALL_DIRECTION_LABELS[modern["direction"].casefold()]
        kind = _CALL_KIND_LABELS[modern["kind"].casefold()]
        outcome_key = modern["outcome"].replace("_", " ").strip().casefold()
        outcome = _CALL_OUTCOME_LABELS.get(outcome_key, outcome_key)
        link = "con" if modern["link"].casefold() == "with" else "de"
        return (
            f"Llamada {direction} {kind}: {outcome} {link} "
            + body[modern.end() :]
        )

    for english, spanish in _CALL_LEGACY_PREFIXES:
        if body.casefold().startswith(english.casefold()):
            return spanish + body[len(english) :]
    return body


def format_datetime(value: object, *, now: datetime | None = None) -> str:
    """Render a timestamp in local Spanish, using relative day labels."""

    if not isinstance(value, datetime):
        return "sin datos"
    try:
        local = value.astimezone()
        reference = (
            now.astimezone() if now is not None and now.tzinfo else now
        ) or datetime.now().astimezone()
    except (AttributeError, OSError, OverflowError, ValueError):
        return "sin datos"

    hour = local.hour % 12 or 12
    suffix = "a. m." if local.hour < 12 else "p. m."
    clock = f"{hour}:{local.minute:02d} {suffix}"
    days = (reference.date() - local.date()).days
    if days == 0:
        return f"hoy a las {clock}"
    if days == 1:
        return f"ayer a las {clock}"

    label = f"{local.day} de {MONTH_NAMES[local.month - 1]}"
    if local.year != reference.year:
        label += f" de {local.year}"
    return f"{label} a las {clock}"


def format_call_body(
    body: str,
    *,
    duration_seconds: float | int | None = None,
    event_timestamp: datetime | None = None,
) -> str:
    """Replace the bridge's technical call suffixes with localized values.

    The structured call envelope gates this helper at call sites; the body is only
    used as the bridge's human-readable label and contact name.
    """

    result = _translate_call_prefix(body)
    # Older bridge notices append the routable XMPP JID after the human name.
    # It is transport metadata, not call content, so never expose it in UI text.
    result = _CALL_TECHNICAL_JID_PATTERN.sub("", result)
    if duration_seconds is not None:
        formatted_duration = format_duration(duration_seconds)
        result, replaced = _CALL_DURATION_PATTERN.subn(
            f", {formatted_duration}", result, count=1
        )
        if not replaced:
            timestamp_match = _CALL_TIMESTAMP_PATTERN.search(result)
            if timestamp_match:
                result = (
                    result[: timestamp_match.start()]
                    + f", {formatted_duration}"
                    + result[timestamp_match.start() :]
                )
            else:
                result = f"{result}, {formatted_duration}"

    if event_timestamp is not None:
        formatted_timestamp = format_datetime(event_timestamp)
        result, replaced = _CALL_TIMESTAMP_PATTERN.subn(
            f", {formatted_timestamp}", result, count=1
        )
        if not replaced:
            result = f"{result} ({formatted_timestamp})"
    return result
