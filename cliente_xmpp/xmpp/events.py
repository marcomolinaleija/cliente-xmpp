from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.mentions import GroupParticipant
from cliente_xmpp.models.message_info import GroupMessageInfo
from cliente_xmpp.models.reactions import ReactionUpdate
from cliente_xmpp.models.voice import VoiceAudioStats, VoiceDevice


@dataclass(slots=True)
class VoiceChannelChanged:
    account_jid: str
    component_jid: str
    connected: bool
    detail: str = ""
    session_id: str = ""


@dataclass(slots=True)
class VoiceStateChanged:
    account_jid: str
    component_jid: str
    call_id: str
    peer: str
    direction: str
    state: str
    reason: str
    revision: int
    session_id: str = ""
    chat_jid: str = ""


@dataclass(slots=True)
class VoiceAudioChanged:
    account_jid: str
    component_jid: str
    session_id: str
    running: bool
    muted: bool
    detail: str = ""


@dataclass(slots=True)
class VoiceDevicesLoaded:
    account_jid: str
    session_id: str
    devices: tuple[VoiceDevice, ...] = ()
    error: str = ""


@dataclass(slots=True)
class VoiceDiagnostics:
    account_jid: str
    component_jid: str
    session_id: str
    request_id: str
    stats: VoiceAudioStats


@dataclass(slots=True)
class VoiceActionFailed:
    account_jid: str
    component_jid: str
    session_id: str
    action: str
    call_id: str
    detail: str
    request_id: str = ""


@dataclass(slots=True)
class XmppConnected:
    pass


@dataclass(slots=True)
class XmppDisconnected:
    reason: str = ""
    generation: int = 0


@dataclass(slots=True)
class XmppError:
    message: str


@dataclass(slots=True)
class ChatReadFailed:
    chat_jid: str
    detail: str


@dataclass(slots=True)
class WhatsAppBridgeStatus:
    status: str
    component_jid: str = ""
    detail: str = ""


@dataclass(slots=True)
class WhatsAppLinkState:
    account_jid: str
    component_jid: str
    generation: int
    attempt: str
    revision: int
    status: str
    authenticated: bool
    reason: str = ""
    qr_revision: int = 0
    qr_deadline: float = 0.0
    image_data: bytes = b""
    code: str = ""
    sync_phase: str = "pending"


@dataclass(slots=True)
class WhatsAppPairingCodeReceived:
    component_jid: str
    code: str


@dataclass(slots=True)
class WhatsAppLinkSessionStarted:
    component_jid: str
    command_node: str
    session_id: str
    mode: str = ""


@dataclass(slots=True)
class WhatsAppLinkSessionEnded:
    component_jid: str
    command_node: str = ""
    session_id: str = ""
    canceled: bool = False
    detail: str = ""


@dataclass(slots=True)
class WhatsAppQrImageReceived:
    component_jid: str
    image_url: str
    mime: str = ""
    filename: str = ""


@dataclass(slots=True)
class WhatsAppQrImageDataReceived:
    component_jid: str
    image_data: bytes
    mime: str = ""
    filename: str = ""


@dataclass(slots=True)
class RosterLoaded:
    chats: list[Chat]


@dataclass(slots=True)
class ChatsDiscovered:
    chats: list[Chat]


@dataclass(slots=True)
class GroupParticipantUpdated:
    participant: GroupParticipant


@dataclass(slots=True)
class GroupParticipantsLoaded:
    group_jid: str
    participants: list[GroupParticipant]


@dataclass(slots=True)
class MessageReceived:
    message: Message
    notify: bool = True


@dataclass(slots=True)
class MessageReactionReceived:
    update: ReactionUpdate


@dataclass(slots=True)
class MessageHistoryLoaded:
    chat_jid: str
    messages: list[Message]
    older: bool = False
    complete: bool = False
    background: bool = False


@dataclass(slots=True)
class MessageDeliveryUpdated:
    chat_jid: str
    message_id: str
    delivery_state: str
    detail: str = ""


@dataclass(slots=True)
class GroupMessageInfoLoaded:
    request_id: str
    info: GroupMessageInfo | None = None
    error: str = ""


@dataclass(slots=True)
class FileBatchCompleted:
    chat_jid: str
    total: int
    succeeded: int
    failed: int
    detail: str = ""


@dataclass(slots=True)
class FileTransferUpdated:
    chat_jid: str
    transfer_id: str
    filename: str
    state: str
    percent: int = 0
    size: int = 0
    detail: str = ""
    is_group: bool = False
    account_jid: str = ""
    reply_to_jid: str = ""
    reply_to_id: str = ""
    reply_quote: str = ""


@dataclass(slots=True)
class ChatDisplayedSynced:
    chat_jid: str
    message_id: str


@dataclass(slots=True)
class ContactPresenceUpdated:
    chat_jid: str
    availability: str
    status: str = ""
    last_seen: datetime | None = None


@dataclass(slots=True)
class ContactAvatarReceived:
    chat_jid: str
    data: bytes
    mime: str = ""
    avatar_id: str = ""


@dataclass(slots=True)
class ContactAvatarUnavailable:
    chat_jid: str
    detail: str = ""


@dataclass(slots=True)
class ChatStateUpdated:
    chat_jid: str
    state: str
    media: str = ""


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
    | VoiceChannelChanged
    | VoiceStateChanged
    | VoiceAudioChanged
    | VoiceDiagnostics
    | VoiceDevicesLoaded
    | VoiceActionFailed
    | XmppDisconnected
    | XmppError
    | ChatReadFailed
    | WhatsAppBridgeStatus
    | WhatsAppLinkState
    | WhatsAppPairingCodeReceived
    | WhatsAppLinkSessionStarted
    | WhatsAppLinkSessionEnded
    | WhatsAppQrImageReceived
    | WhatsAppQrImageDataReceived
    | RosterLoaded
    | ChatsDiscovered
    | GroupParticipantUpdated
    | GroupParticipantsLoaded
    | MessageReceived
    | MessageReactionReceived
    | MessageHistoryLoaded
    | MessageDeliveryUpdated
    | GroupMessageInfoLoaded
    | FileBatchCompleted
    | FileTransferUpdated
    | ChatDisplayedSynced
    | ContactPresenceUpdated
    | ContactAvatarReceived
    | ContactAvatarUnavailable
    | ChatStateUpdated
    | ChatActivityLoaded
    | ChatActivityLoadFinished
)
