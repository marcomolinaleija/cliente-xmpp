from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from re import escape, finditer


@dataclass(frozen=True, slots=True)
class GroupParticipant:
    group_jid: str
    jid: str
    nick: str


@dataclass(frozen=True, slots=True)
class MentionCandidate:
    participant_jid: str
    display_name: str
    mention_text: str

    @property
    def label(self) -> str:
        if self.display_name == self.mention_text:
            return self.display_name
        return f"{self.display_name} | WhatsApp: {self.mention_text}"


@dataclass(frozen=True, slots=True)
class MentionReference:
    participant_jid: str
    start: int
    end: int


def active_mention_query(text: str, insertion_point: int) -> tuple[int, int, str] | None:
    """Return the active @query at the caret, without treating email addresses as mentions."""
    insertion_point = max(0, min(insertion_point, len(text)))
    start = insertion_point
    while start > 0 and not text[start - 1].isspace():
        start -= 1

    token = text[start:insertion_point]
    if not token.startswith("@") or "@" in token[1:]:
        return None

    return start, insertion_point, token[1:]


def matching_mention_candidates(
    candidates: list[MentionCandidate],
    query: str,
    limit: int = 8,
) -> list[MentionCandidate]:
    normalized_query = _normalize(query)
    if not candidates:
        return []

    matches = [
        candidate
        for candidate in candidates
        if not normalized_query
        or normalized_query in _normalize(candidate.display_name)
        or normalized_query in _normalize(candidate.mention_text)
    ]
    return sorted(
        matches,
        key=lambda candidate: (
            not _normalize(candidate.display_name).startswith(normalized_query),
            not _normalize(candidate.mention_text).startswith(normalized_query),
            _normalize(candidate.display_name),
        ),
    )[:limit]


def mention_references_in_text(
    text: str,
    candidates: list[MentionCandidate],
) -> list[MentionReference]:
    """Locate selected MUC nicks so the XMPP stanza can carry their real JIDs."""
    matches: list[tuple[int, int, MentionCandidate]] = []
    boundary_characters = "!\"'(),.:;?@_"
    for candidate in sorted(candidates, key=lambda item: len(item.mention_text), reverse=True):
        if not candidate.mention_text:
            continue
        for match in finditer(escape(candidate.mention_text), text):
            start, end = match.span()
            if start and not (text[start - 1].isspace() or text[start - 1] in boundary_characters):
                continue
            if end < len(text) and not (text[end].isspace() or text[end] in boundary_characters):
                continue
            matches.append((start, end, candidate))

    references: list[MentionReference] = []
    occupied_until = 0
    for start, end, candidate in sorted(matches, key=lambda match: (match[0], -match[1])):
        if start < occupied_until:
            continue
        references.append(MentionReference(candidate.participant_jid, start, end))
        occupied_until = end
    return references


def _normalize(value: str) -> str:
    folded = value.casefold()
    return "".join(
        character
        for character in unicodedata.normalize("NFD", folded)
        if unicodedata.category(character) != "Mn"
    )
