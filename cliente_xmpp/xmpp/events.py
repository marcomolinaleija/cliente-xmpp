from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cliente_xmpp.models.chat import Chat, Message


@dataclass(slots=True)
class XmppConnected:
    pass


@dataclass(slots=True)
class XmppDisconnected:
    reason: str = ""


@dataclass(slots=True)
class XmppError:
    message: str


@dataclass(slots=True)
class RosterLoaded:
    chats: list[Chat]


@dataclass(slots=True)
class ChatsDiscovered:
    chats: list[Chat]


@dataclass(slots=True)
class MessageReceived:
    message: Message
    notify: bool = True


@dataclass(slots=True)
class MessageHistoryLoaded:
    chat_jid: str
    messages: list[Message]
    older: bool = False
    complete: bool = False
    background: bool = False


@dataclass(slots=True)
class ChatActivityLoaded:
    chat_jid: str
    sent_at: datetime | None
    preview: str = ""
    unread_count: int | None = None
    is_group: bool = False


@dataclass(slots=True)
class ChatActivityLoadFinished:
    loaded_count: int


XmppEvent = (
    XmppConnected
    | XmppDisconnected
    | XmppError
    | RosterLoaded
    | ChatsDiscovered
    | MessageReceived
    | MessageHistoryLoaded
    | ChatActivityLoaded
    | ChatActivityLoadFinished
)
