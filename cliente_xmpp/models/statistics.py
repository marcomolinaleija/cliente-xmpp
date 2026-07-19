from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DailyMessageStatistics:
    day: date
    sent: int
    received: int

    @property
    def total(self) -> int:
        return self.sent + self.received


@dataclass(frozen=True, slots=True)
class ChatMessageStatistics:
    chat_jid: str
    name: str
    is_group: bool
    sent: int
    received: int
    current_received_streak: int
    maximum_received_streak: int
    current_sent_streak: int
    median_my_response_seconds: float | None
    median_their_response_seconds: float | None
    active_days: int
    first_message_at: datetime
    last_message_at: datetime
    busiest_hour: int | None
    stickers: int
    audio_messages: int
    image_messages: int
    video_messages: int
    file_messages: int
    positive_weight: float
    negative_weight: float
    sentiment_messages: int

    @property
    def total(self) -> int:
        return self.sent + self.received

    @property
    def emotional_net(self) -> float:
        return self.positive_weight - self.negative_weight

    @property
    def emotional_balance(self) -> float:
        total_weight = self.positive_weight + self.negative_weight
        if total_weight == 0:
            return 0.0
        return (self.emotional_net / total_weight) * 100


@dataclass(frozen=True, slots=True)
class MessageStatistics:
    period_days: int | None
    from_date: date | None
    to_date: date
    first_message_at: datetime | None
    last_message_at: datetime | None
    total_sent: int
    total_received: int
    active_days: int
    calendar_days: int
    stickers: int
    audio_messages: int
    image_messages: int
    video_messages: int
    file_messages: int
    busiest_hour: int | None
    median_my_response_seconds: float | None
    median_their_response_seconds: float | None
    positive_weight: float
    negative_weight: float
    sentiment_messages: int
    daily: tuple[DailyMessageStatistics, ...]
    chats: tuple[ChatMessageStatistics, ...]

    @property
    def total(self) -> int:
        return self.total_sent + self.total_received
