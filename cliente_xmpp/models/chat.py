from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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
    sent_at: datetime = field(default_factory=datetime.now)
    outgoing: bool = False
    audio_url: str = ""
    media_url: str = ""
    media_kind: str = ""
    media_mime: str = ""
    media_filename: str = ""
    media_size: int = 0
    media_duration_seconds: float = 0.0
    media_local_path: str = ""
    message_id: str = ""
    chat_is_group: bool = False
    starred: bool = False
    reactions: tuple[str, ...] = ()
    reply_quote: str = ""
    delivery_state: str = ""
