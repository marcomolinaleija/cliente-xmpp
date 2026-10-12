from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class ParticipantReceipt:
    jid: str = ""
    lid: str = ""
    name: str = ""
    delivered_at: int = 0
    read_at: int = 0

    @property
    def key(self) -> str:
        return self.lid or self.jid

    @property
    def state(self) -> str:
        if self.read_at:
            return "Leído"
        if self.delivered_at:
            return "Entregado, sin lectura confirmada"
        return "Sin entrega confirmada"


@dataclass(frozen=True, slots=True)
class GroupMessageInfo:
    chat_jid: str
    message_id: str
    legacy_id: str = ""
    recipients_complete: bool = False
    participants: tuple[ParticipantReceipt, ...] = ()


def merge_message_info(old: GroupMessageInfo | None, new: GroupMessageInfo) -> GroupMessageInfo:
    """Merge identities and monotonic receipts, never use today's membership for old sends."""
    if old is None:
        return new
    if (old.chat_jid, old.message_id) != (new.chat_jid, new.message_id):
        raise ValueError("Message info belongs to another message")
    if old.legacy_id and new.legacy_id and old.legacy_id != new.legacy_id:
        raise ValueError("Message info refers to another WhatsApp message")
    rows = list(old.participants)
    for incoming in new.participants:
        matches = [
            i
            for i, row in enumerate(rows)
            if (incoming.lid and row.lid == incoming.lid)
            or (incoming.jid and row.jid == incoming.jid)
        ]
        merged = incoming
        for i in reversed(matches):
            previous = rows.pop(i)
            merged = ParticipantReceipt(
                jid=merged.jid or previous.jid,
                lid=merged.lid or previous.lid,
                name=merged.name or previous.name,
                delivered_at=_earliest(previous.delivered_at, merged.delivered_at),
                read_at=_earliest(previous.read_at, merged.read_at),
            )
        rows.append(merged)
    return replace(
        new,
        legacy_id=new.legacy_id or old.legacy_id,
        recipients_complete=new.recipients_complete or old.recipients_complete,
        participants=tuple(sorted(rows, key=lambda row: row.key)),
    )


def _earliest(left: int, right: int) -> int:
    return min(left, right) if left and right else left or right
