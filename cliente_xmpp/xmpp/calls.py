from __future__ import annotations

from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from cliente_xmpp.models.calls import (
    CALL_DIRECTIONS,
    CALL_KINDS,
    CALL_STATES,
    CallEvent,
)

CALL_NAMESPACE = "urn:marco-ml:whatsapp:call:1"
_CALL_TAG = f"{{{CALL_NAMESPACE}}}call"


def call_event_from_xml(xml: ET.Element | None) -> CallEvent | None:
    """Return only a complete v1 call envelope; text notices are never inferred."""

    if xml is None:
        return None
    element = xml.find(f".//{_CALL_TAG}")
    if element is None:
        return None
    attributes = element.attrib
    try:
        version = int(attributes.get("contract-version", ""))
        sequence = int(attributes.get("sequence", ""))
    except ValueError:
        return None
    call_id = attributes.get("call-id", "").strip()
    peer_jid = attributes.get("peer-jid", "").strip()
    direction = attributes.get("direction", "")
    kind = attributes.get("kind", "")
    state = attributes.get("state", "")
    if (
        version != 1
        or sequence < 1
        or not call_id
        or not peer_jid
        or direction not in CALL_DIRECTIONS
        or kind not in CALL_KINDS
        or state not in CALL_STATES
    ):
        return None
    event_timestamp = _parse_utc_timestamp(attributes.get("event-timestamp"))
    answered_at = _parse_utc_timestamp(attributes.get("answered-at"), optional=True)
    ended_at = _parse_utc_timestamp(attributes.get("ended-at"), optional=True)
    if event_timestamp is None:
        return None
    if attributes.get("answered-at") is not None and answered_at is None:
        return None
    if attributes.get("ended-at") is not None and ended_at is None:
        return None
    try:
        return CallEvent(
            call_id=call_id,
            peer_jid=peer_jid,
            group_jid=attributes.get("group-jid", "").strip(),
            chat_jid=attributes.get("chat-jid", "").strip(),
            direction=direction,
            kind=kind,
            state=state,
            event_timestamp=event_timestamp,
            answered_at=answered_at,
            ended_at=ended_at,
            terminal_reason=attributes.get("terminal-reason", "").strip(),
            sequence=sequence,
            contract_version=version,
        )
    except ValueError:
        return None


def routed_chat_jid(default_chat_jid: str, event: CallEvent | None) -> str:
    """Use only the bridge's explicit canonical route; preserve old envelopes."""

    if event is not None and event.chat_jid:
        return event.chat_jid
    return default_chat_jid


def _parse_utc_timestamp(value: str | None, *, optional: bool = False) -> datetime | None:
    if value is None or not value.strip():
        return None if optional else None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)
