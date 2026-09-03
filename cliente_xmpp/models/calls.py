from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

CALL_DIRECTIONS = frozenset({"incoming", "outgoing", "unknown"})
CALL_KINDS = frozenset({"voice", "video", "unknown"})
CALL_STATES = frozenset(
    {"offered", "accepted", "missed", "rejected", "ended", "failed", "unknown"}
)
CALL_OUTCOMES = frozenset(
    {
        "",
        "connected",
        "rejected",
        "cancelled",
        "accepted_elsewhere",
        "missed",
        "invalid",
        "unavailable",
        "upcoming",
        "failed",
        "abandoned",
        "ongoing",
        "silenced_by_dnd",
        "silenced_unknown_caller",
    }
)
CALL_SOURCES = frozenset({"", "signaling", "history_sync", "app_state", "message"})


@dataclass(frozen=True, slots=True)
class CallEvent:
    """One validated, versioned call-contract phase carried by a message."""

    call_id: str
    peer_jid: str
    direction: str
    kind: str
    state: str
    event_timestamp: datetime
    sequence: int
    group_jid: str = ""
    # Optional canonical XMPP chat target supplied by the bridge. It is never
    # inferred from the human-readable fallback body.
    chat_jid: str = ""
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    outcome: str = ""
    source: str = ""
    terminal_reason: str = ""
    contract_version: int = 1

    def __post_init__(self) -> None:
        if not self.call_id or not self.peer_jid:
            raise ValueError("call_id and peer_jid are required")
        if self.contract_version != 1 or self.sequence < 1:
            raise ValueError("unsupported call contract")
        if self.direction not in CALL_DIRECTIONS:
            raise ValueError("unknown call direction")
        if self.kind not in CALL_KINDS:
            raise ValueError("unknown call kind")
        if self.state not in CALL_STATES:
            raise ValueError("unknown call state")
        if self.outcome not in CALL_OUTCOMES:
            raise ValueError("unknown call outcome")
        if self.source not in CALL_SOURCES:
            raise ValueError("unknown call source")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("invalid call duration")
        for value in (self.event_timestamp, self.answered_at, self.ended_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("call timestamps must be timezone-aware")
        if self.answered_at and self.ended_at and self.ended_at < self.answered_at:
            raise ValueError("call ended before it was answered")


@dataclass(frozen=True, slots=True)
class CallSummary:
    """Canonical view of all known phases for one call identifier."""

    call_id: str
    peer_jid: str
    direction: str
    kind: str
    state: str
    event_timestamp: datetime
    group_jid: str = ""
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    recorded_duration_seconds: float | None = None
    outcome: str = ""
    source: str = ""
    terminal_reason: str = ""

    @property
    def answered(self) -> bool:
        return (
            self.answered_at is not None
            or self.state == "accepted"
            or self.outcome in {"connected", "accepted_elsewhere"}
        )

    @property
    def duration_seconds(self) -> float | None:
        if self.recorded_duration_seconds is not None:
            return self.recorded_duration_seconds
        if self.answered_at is None or self.ended_at is None:
            return None
        duration = (self.ended_at - self.answered_at).total_seconds()
        return duration if duration >= 0 else None


def aggregate_call_events(events: Iterable[CallEvent]) -> tuple[CallSummary, ...]:
    """Merge delivery duplicates/phases without deriving facts that were not sent."""

    grouped: dict[str, list[CallEvent]] = {}
    for event in events:
        grouped.setdefault(event.call_id, []).append(event)

    summaries: list[CallSummary] = []
    for call_id, phases in grouped.items():
        ordered = sorted(phases, key=lambda event: (event.sequence, event.event_timestamp))
        first = ordered[0]
        latest = ordered[-1]
        direction = next(
            (event.direction for event in reversed(ordered) if event.direction != "unknown"),
            "unknown",
        )
        kind = next(
            (event.kind for event in reversed(ordered) if event.kind != "unknown"), "unknown"
        )
        group_jid = next((event.group_jid for event in reversed(ordered) if event.group_jid), "")
        peer_jid = next(
            (event.peer_jid for event in reversed(ordered) if event.peer_jid), first.peer_jid
        )
        accepted = next(
            (event for event in ordered if event.state == "accepted"),
            None,
        )
        terminal = next(
            (
                event
                for event in reversed(ordered)
                if event.state in {"missed", "rejected", "ended", "failed"}
                or event.terminal_reason
            ),
            None,
        )
        answered_at = next((event.answered_at for event in ordered if event.answered_at), None)
        ended_at = next(
            (event.ended_at for event in reversed(ordered) if event.ended_at),
            None,
        )
        recorded_duration = next(
            (
                event.duration_seconds
                for event in reversed(ordered)
                if event.duration_seconds is not None
            ),
            None,
        )
        outcome = next((event.outcome for event in reversed(ordered) if event.outcome), "")
        source = next((event.source for event in reversed(ordered) if event.source), "")
        authoritative = next(
            (
                event
                for event in reversed(ordered)
                if event.source in {"history_sync", "app_state", "message"}
            ),
            None,
        )
        state = (
            terminal.state if terminal is not None else ("accepted" if accepted else latest.state)
        )
        summaries.append(
            CallSummary(
                call_id=call_id,
                peer_jid=peer_jid,
                direction=direction,
                kind=kind,
                state=state,
                event_timestamp=(
                    authoritative.event_timestamp
                    if authoritative is not None
                    else min(event.event_timestamp for event in ordered)
                ).astimezone(UTC),
                group_jid=group_jid,
                answered_at=answered_at,
                ended_at=ended_at,
                recorded_duration_seconds=recorded_duration,
                outcome=outcome,
                source=source,
                terminal_reason=(terminal.terminal_reason if terminal else ""),
            )
        )
    return tuple(sorted(summaries, key=lambda summary: (summary.event_timestamp, summary.call_id)))
