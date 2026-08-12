from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PollVote:
    """Latest known selection for one WhatsApp poll voter."""

    voter_jid: str
    voter_lid: str = ""
    voter_name: str = ""
    voter_is_me: bool = False
    option_hashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PollUpdate:
    """A vote update that must be merged into its poll instead of shown as a message."""

    poll_id: str
    voter_jid: str
    voter_lid: str = ""
    voter_name: str = ""
    voter_is_me: bool = False
    option_hashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Poll:
    """Metadata required to present and vote on a native WhatsApp poll."""

    poll_id: str
    title: str
    options: tuple[str, ...]
    creator_jid: str
    creator_lid: str = ""
    creator_is_me: bool = False
    selectable_count: int = 1
    allows_multiple: bool = False
    votes: tuple[PollVote, ...] = ()


def poll_option_hash(option: str) -> str:
    return hashlib.sha256(option.encode("utf-8")).hexdigest()


def poll_votes_match(first: PollVote | PollUpdate, second: PollVote | PollUpdate) -> bool:
    if first.voter_is_me or second.voter_is_me:
        return first.voter_is_me and second.voter_is_me
    if first.voter_lid and second.voter_lid:
        if first.voter_lid.casefold() == second.voter_lid.casefold():
            return True
    return bool(
        first.voter_jid
        and second.voter_jid
        and first.voter_jid.casefold() == second.voter_jid.casefold()
    )


def apply_poll_update(poll: Poll, update: PollUpdate) -> Poll:
    """Replace a voter's prior selection and preserve every other latest vote."""

    previous = next(
        (vote for vote in poll.votes if poll_votes_match(vote, update)),
        None,
    )
    votes = [vote for vote in poll.votes if not poll_votes_match(vote, update)]
    if update.option_hashes:
        votes.append(
            PollVote(
                voter_jid=update.voter_jid or (previous.voter_jid if previous else ""),
                voter_lid=update.voter_lid or (previous.voter_lid if previous else ""),
                voter_name=update.voter_name or (previous.voter_name if previous else ""),
                voter_is_me=update.voter_is_me,
                option_hashes=update.option_hashes,
            )
        )
    return Poll(
        poll_id=poll.poll_id,
        title=poll.title,
        options=poll.options,
        creator_jid=poll.creator_jid,
        creator_lid=poll.creator_lid,
        creator_is_me=poll.creator_is_me,
        selectable_count=poll.selectable_count,
        allows_multiple=poll.allows_multiple,
        votes=tuple(votes),
    )


def poll_option_counts(poll: Poll) -> tuple[int, ...]:
    counts = {poll_option_hash(option): 0 for option in poll.options}
    for vote in poll.votes:
        for option_hash in set(vote.option_hashes):
            if option_hash in counts:
                counts[option_hash] += 1
    return tuple(counts[poll_option_hash(option)] for option in poll.options)


def poll_selected_options(poll: Poll, *, voter_is_me: bool) -> tuple[str, ...]:
    selected_hashes = {
        option_hash
        for vote in poll.votes
        if vote.voter_is_me == voter_is_me
        for option_hash in vote.option_hashes
    }
    return tuple(
        option for option in poll.options if poll_option_hash(option) in selected_hashes
    )


def poll_display_text(poll: Poll) -> str:
    counts = poll_option_counts(poll)
    selected = set(poll_selected_options(poll, voter_is_me=True))
    lines = [f"🗳 {poll.title}"]
    for option, count in zip(poll.options, counts, strict=True):
        marker = "☑" if option in selected else "☐"
        suffix = f" — {count} voto" if count == 1 else f" — {count} votos"
        lines.append(f"{marker} {option}{suffix}")
    voters = len(poll.votes)
    voter_label = "1 persona votó" if voters == 1 else f"{voters} personas votaron"
    lines.append(voter_label)
    return "\n".join(lines)


@dataclass(slots=True)
class Chat:
    jid: str
    name: str
    custom_name: str = ""
    is_group: bool = False
    notifications_muted: bool = False
    notification_settings_known: bool = False
    group_member_count: int = 0
    is_self_group: bool = False
    unread_count: int = 0
    last_message_preview: str = ""
    last_message_at: datetime | None = None


@dataclass(slots=True)
class Message:
    chat_jid: str
    sender_jid: str
    body: str
    sender_name: str = ""
    sent_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    outgoing: bool = False
    audio_url: str = ""
    media_url: str = ""
    media_kind: str = ""
    media_mime: str = ""
    media_filename: str = ""
    media_size: int = 0
    media_duration_seconds: float = 0.0
    media_local_path: str = ""
    is_sticker: bool = False
    is_forwarded: bool = False
    poll: Poll | None = None
    poll_update: PollUpdate | None = None
    message_id: str = ""
    displayed_marker_id: str = ""
    chat_is_group: bool = False
    starred: bool = False
    reactions: tuple[str, ...] = ()
    reply_quote: str = ""
    reply_to_jid: str = ""
    reply_to_id: str = ""
    delivery_state: str = ""
    retracted: bool = False
    edited: bool = False
    replaces_id: str = ""
