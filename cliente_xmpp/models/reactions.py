from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime

MAX_REACTION_CODEPOINTS = 16


@dataclass(frozen=True, slots=True)
class ReactionState:
    """The latest complete set of reactions contributed by one participant."""

    sender_id: str
    reactions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReactionUpdate:
    """A XEP-0444 reaction update referring to an existing message."""

    chat_jid: str
    target_id: str
    sender_id: str
    reactions: tuple[str, ...] = ()
    sender_name: str = ""
    sender_is_me: bool = False
    is_group: bool = False
    sent_at: datetime | None = None


def is_supported_reaction(value: str) -> bool:
    """Accept one compact Unicode emoji sequence suitable for XEP-0444."""

    reaction = value.strip()
    if (
        not reaction
        or len(reaction) > MAX_REACTION_CODEPOINTS
        or any(char.isspace() for char in reaction)
    ):
        return False

    has_emoji_base = False
    for char in reaction:
        category = unicodedata.category(char)
        if category.startswith("S") or char in "#*0123456789":
            has_emoji_base = True
            continue
        if category.startswith("M") or char in {"\u200d", "\ufe0e", "\ufe0f", "\u20e3"}:
            continue
        return False
    return has_emoji_base


def normalized_reactions(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Keep only distinct, protocol-safe emoji reactions in their received order."""

    result: list[str] = []
    for value in values:
        reaction = value.strip()
        if is_supported_reaction(reaction) and reaction not in result:
            result.append(reaction)
    return tuple(result)


def flattened_reactions(states: tuple[ReactionState, ...]) -> tuple[str, ...]:
    """Build the compact reaction list used by the conversation presentation."""

    return normalized_reactions(
        [reaction for state in states for reaction in state.reactions]
    )
