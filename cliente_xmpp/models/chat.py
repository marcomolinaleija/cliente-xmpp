from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class Chat:
    jid: str
    name: str
    unread_count: int = 0
    last_message_preview: str = ""
    last_message_at: datetime | None = None


@dataclass(slots=True)
class Message:
    chat_jid: str
    sender_jid: str
    body: str
    sent_at: datetime = field(default_factory=datetime.now)
    outgoing: bool = False
    audio_url: str = ""
