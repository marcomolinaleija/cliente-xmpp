from __future__ import annotations

from dataclasses import dataclass

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
class MessageReceived:
    message: Message


@dataclass(slots=True)
class MessageHistoryLoaded:
    chat_jid: str
    messages: list[Message]


XmppEvent = (
    XmppConnected
    | XmppDisconnected
    | XmppError
    | RosterLoaded
    | MessageReceived
    | MessageHistoryLoaded
)
