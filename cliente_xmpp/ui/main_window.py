from __future__ import annotations

from datetime import datetime

import wx

from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.connection_header_panel import ConnectionHeaderPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.events import EVT_XMPP_EVENT, WxXmppEvent
from cliente_xmpp.ui.login_panel import LoginPanel
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import (
    ChatActivityLoadFinished,
    ChatActivityLoaded,
    MessageHistoryLoaded,
    MessageReceived,
    RosterLoaded,
    XmppConnected,
    XmppDisconnected,
    XmppError,
    XmppEvent,
)


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Cliente XMPP", size=(980, 700))

        self.settings_store = SettingsStore()
        self.xmpp = XmppService(self._post_xmpp_event)
        self.messages_by_chat: dict[str, list[Message]] = {}
        self.latest_message_timestamps_by_chat: dict[str, float] = {}
        self.history_loaded_chats: set[str] = set()
        self.chat_names_by_jid: dict[str, str] = {}
        self.roster_jids: set[str] = set()
        self.loaded_chat_summaries = 0
        self.current_jid = ""

        self.login_panel = LoginPanel(self, self.settings_store.load_connection())
        self.workspace_panel: wx.Panel
        self.content_panel: wx.Panel
        self.content_box: wx.BoxSizer
        self.connection_header: ConnectionHeaderPanel
        self.chat_list: ChatListPanel
        self.conversation: ConversationPanel
        self.status_bar = self.CreateStatusBar()

        self._layout()
        self._bind_events()
        self._set_connected_ui(False)
        self.status_bar.SetStatusText("Desconectado")

    def _layout(self) -> None:
        self.workspace_panel = wx.Panel(self)
        workspace_box = wx.BoxSizer(wx.VERTICAL)

        self.connection_header = ConnectionHeaderPanel(self.workspace_panel)

        self.content_panel = wx.Panel(self.workspace_panel)
        self.content_box = wx.BoxSizer(wx.VERTICAL)
        self.chat_list = ChatListPanel(self.content_panel)
        self.conversation = ConversationPanel(self.content_panel, self._display_name_for_jid)
        self.content_box.Add(self.chat_list, 1, wx.EXPAND)
        self.content_box.Add(self.conversation, 1, wx.EXPAND)
        self.content_panel.SetSizer(self.content_box)
        self.conversation.Hide()

        workspace_box.Add(self.connection_header, 0, wx.EXPAND)
        workspace_box.Add(self.content_panel, 1, wx.EXPAND)
        self.workspace_panel.SetSizer(workspace_box)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.login_panel, 0, wx.EXPAND)
        box.Add(self.workspace_panel, 1, wx.EXPAND)
        self.SetSizer(box)

    def _bind_events(self) -> None:
        self.login_panel.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)
        self.connection_header.disconnect_button.Bind(wx.EVT_BUTTON, self._on_disconnect)
        self.chat_list.list_box.Bind(wx.EVT_LISTBOX, self._on_chat_selected)
        self.chat_list.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self._on_open_selected_chat)
        self.chat_list.open_button.Bind(wx.EVT_BUTTON, self._on_open_selected_chat)
        self.conversation.back_button.Bind(wx.EVT_BUTTON, self._on_back_to_chat_list)
        self.conversation.send_button.Bind(wx.EVT_BUTTON, self._on_send_message)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        self.Bind(EVT_XMPP_EVENT, self._on_xmpp_event)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_connect(self, _event: wx.CommandEvent) -> None:
        login = self.login_panel.get_login_data()
        if not login.settings.jid or not login.password:
            wx.MessageBox("El JID y el password son obligatorios.", "Datos incompletos")
            return

        self.settings_store.save_connection(login.settings)
        self.current_jid = login.settings.jid
        self.login_panel.set_connecting(True)
        self.status_bar.SetStatusText("Conectando...")
        self.xmpp.connect(login.settings, login.password)

    def _on_disconnect(self, _event: wx.CommandEvent) -> None:
        self.connection_header.set_status("Desconectando...")
        self.status_bar.SetStatusText("Desconectando...")
        self.xmpp.disconnect()

    def _on_chat_selected(self, _event: wx.CommandEvent) -> None:
        chat = self.chat_list.selected_chat()
        if not chat:
            return

        self._load_conversation(chat)

    def _on_open_selected_chat(self, _event: wx.Event) -> None:
        self._show_selected_chat()

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        key_code = event.GetKeyCode()
        if key_code == wx.WXK_RETURN and self.chat_list.IsShown():
            self._show_selected_chat()
            return

        if key_code == wx.WXK_ESCAPE and self.conversation.IsShown():
            self._show_chat_list()
            return

        event.Skip()

    def _on_back_to_chat_list(self, _event: wx.CommandEvent) -> None:
        self._show_chat_list()

    def _on_send_message(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        body = self.conversation.consume_composed_message()
        if not chat or not body:
            return

        message = Message(chat_jid=chat.jid, sender_jid="me", body=body, outgoing=True)
        self._store_message(message)
        self._update_chat_from_message(message)
        self.conversation.append_message(message)
        self._refresh_chat_order(chat.jid)
        self.xmpp.send_message(chat.jid, body)

    def _on_xmpp_event(self, event: WxXmppEvent) -> None:
        self._handle_xmpp_event(event.event)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.xmpp.disconnect()
        event.Skip()

    def _post_xmpp_event(self, event: XmppEvent) -> None:
        wx.PostEvent(self, WxXmppEvent(event))

    def _handle_xmpp_event(self, event: XmppEvent) -> None:
        match event:
            case XmppConnected():
                self.login_panel.set_connecting(False)
                self.connection_header.set_account(self.current_jid)
                self.connection_header.set_status("Conectado")
                self._set_connected_ui(True)
                self.status_bar.SetStatusText("Conectado")
            case XmppDisconnected():
                self.login_panel.set_connecting(False)
                self.connection_header.set_status("Desconectado")
                self._set_connected_ui(False)
                self.status_bar.SetStatusText("Desconectado")
            case XmppError(message=message):
                self.login_panel.set_connecting(False)
                self.status_bar.SetStatusText(message)
                wx.MessageBox(message, "XMPP")
            case RosterLoaded(chats=chats):
                self._update_chat_names(chats)
                self.roster_jids = {chat.jid for chat in chats}
                self.loaded_chat_summaries = 0
                self.chat_list.set_chats([])
                self.status_bar.SetStatusText(
                    f"Buscando chats con mensajes entre {len(chats)} contactos..."
                )
            case MessageReceived(message=message):
                self._store_message(message)
                self._ensure_chat_for_message(message)
                current_chat_is_open = (
                    self.conversation.IsShown()
                    and self.conversation.current_chat
                    and self.conversation.current_chat.jid == message.chat_jid
                )
                self._update_chat_from_message(
                    message,
                    mark_unread=not message.outgoing and not current_chat_is_open,
                )
                self._refresh_chat_order()
                if current_chat_is_open:
                    self.conversation.append_message(message)
            case MessageHistoryLoaded(chat_jid=chat_jid, messages=messages):
                self.history_loaded_chats.add(chat_jid)
                self.messages_by_chat[chat_jid] = messages + self.messages_by_chat.get(chat_jid, [])
                self._update_chat_activity_from_messages(chat_jid, messages)
                self._update_chat_preview_from_messages(chat_jid, messages)
                self._refresh_chat_order()
                if self.conversation.current_chat and self.conversation.current_chat.jid == chat_jid:
                    self._load_conversation(self.conversation.current_chat)
                self.status_bar.SetStatusText(f"{len(messages)} mensajes cargados")
            case ChatActivityLoaded(
                chat_jid=chat_jid,
                sent_at=sent_at,
                preview=preview,
                unread_count=unread_count,
            ):
                if sent_at or preview or unread_count is not None:
                    if sent_at:
                        self._update_chat_activity(chat_jid, sent_at.timestamp())
                    added = not self.chat_list.has_chat(chat_jid)
                    self._update_chat_summary(
                        chat_jid,
                        preview=preview,
                        sent_at=sent_at,
                        unread_count=unread_count,
                    )
                    self._refresh_chat_order()
                    if added:
                        self.loaded_chat_summaries += 1
                    self.status_bar.SetStatusText(
                        f"{self.loaded_chat_summaries} chats con mensajes cargados"
                    )
            case ChatActivityLoadFinished(loaded_count=loaded_count):
                self.loaded_chat_summaries = max(self.loaded_chat_summaries, loaded_count)
                self.status_bar.SetStatusText(
                    f"{self.loaded_chat_summaries} chats con mensajes cargados"
                )

    def _set_connected_ui(self, connected: bool) -> None:
        self.login_panel.Show(not connected)
        self.workspace_panel.Show(connected)
        self.chat_list.Enable(connected)
        self.conversation.Enable(connected)
        if connected:
            self._show_chat_list()
        self.Layout()

    def _store_message(self, message: Message) -> None:
        self.messages_by_chat.setdefault(message.chat_jid, []).append(message)
        self._update_chat_activity(message.chat_jid, message.sent_at.timestamp())

    def _ensure_chat_for_message(self, message: Message) -> None:
        if self.chat_list.has_chat(message.chat_jid):
            return

        self.chat_list.upsert_chat(
            Chat(
                jid=message.chat_jid,
                name=message.chat_jid,
                last_message_preview=message.body,
                last_message_at=message.sent_at,
            )
        )
        self.chat_names_by_jid[message.chat_jid] = message.chat_jid

    def _select_first_chat(self) -> None:
        chat = self.chat_list.select_first()
        if chat:
            self._load_conversation(chat)
            self.chat_list.focus()

    def _load_conversation(self, chat: Chat) -> None:
        self.conversation.set_chat(chat)
        for message in self.messages_by_chat.get(chat.jid, []):
            self.conversation.append_message(message)

    def _refresh_chat_order(self, selected_jid: str = "") -> None:
        selected_chat = self.chat_list.selected_chat()
        selected_jid = selected_jid or (selected_chat.jid if selected_chat else "")
        if not selected_jid and self.conversation.current_chat:
            selected_jid = self.conversation.current_chat.jid
        self.chat_list.set_chats(
            self._sort_chats_by_recency(self.chat_list.chats()),
            selected_jid=selected_jid,
        )

    def _sort_chats_by_recency(self, chats: list[Chat]) -> list[Chat]:
        return sorted(chats, key=self._chat_recency_key)

    def _chat_recency_key(self, chat: Chat) -> tuple[int, float, str]:
        latest = self._latest_message_timestamp(chat.jid)
        if latest is None:
            return (1, 0, chat.name.casefold())

        return (0, -latest, chat.name.casefold())

    def _latest_message_timestamp(self, chat_jid: str) -> float | None:
        latest = self.latest_message_timestamps_by_chat.get(chat_jid)
        messages = self.messages_by_chat.get(chat_jid, [])
        if not messages:
            return latest

        message_latest = max(message.sent_at.timestamp() for message in messages)
        if latest is None:
            return message_latest

        return max(latest, message_latest)

    def _update_chat_activity_from_messages(self, chat_jid: str, messages: list[Message]) -> None:
        if not messages:
            return

        self._update_chat_activity(
            chat_jid,
            max(message.sent_at.timestamp() for message in messages),
        )

    def _update_chat_activity(self, chat_jid: str, timestamp: float) -> None:
        current = self.latest_message_timestamps_by_chat.get(chat_jid)
        if current is None or timestamp > current:
            self.latest_message_timestamps_by_chat[chat_jid] = timestamp

    def _update_chat_preview_from_messages(self, chat_jid: str, messages: list[Message]) -> None:
        if not messages:
            return

        latest_message = max(messages, key=lambda message: message.sent_at)
        self._update_chat_from_message(latest_message)

    def _update_chat_from_message(self, message: Message, mark_unread: bool = False) -> None:
        self._update_chat_summary(
            message.chat_jid,
            preview=message.body,
            sent_at=message.sent_at,
            unread_delta=1 if mark_unread else 0,
        )

    def _update_chat_summary(
        self,
        chat_jid: str,
        preview: str = "",
        sent_at: datetime | None = None,
        unread_delta: int = 0,
        unread_count: int | None = None,
        mark_read: bool = False,
    ) -> None:
        chats = self.chat_list.chats()
        for chat in chats:
            if chat.jid != chat_jid:
                continue

            self.chat_list.upsert_chat(
                Chat(
                    jid=chat.jid,
                    name=chat.name,
                    unread_count=self._next_unread_count(
                        chat.unread_count,
                        unread_delta=unread_delta,
                        unread_count=unread_count,
                        mark_read=mark_read,
                    ),
                    last_message_preview=preview or chat.last_message_preview,
                    last_message_at=sent_at or chat.last_message_at,
                )
            )
            return

        self.chat_list.upsert_chat(
            Chat(
                jid=chat_jid,
                name=self._display_name_for_jid(chat_jid),
                unread_count=self._next_unread_count(
                    0,
                    unread_delta=unread_delta,
                    unread_count=unread_count,
                    mark_read=mark_read,
                ),
                last_message_preview=preview,
                last_message_at=sent_at,
            )
        )
        self.chat_names_by_jid.setdefault(chat_jid, self._display_name_for_jid(chat_jid))

    @staticmethod
    def _next_unread_count(
        current: int,
        unread_delta: int = 0,
        unread_count: int | None = None,
        mark_read: bool = False,
    ) -> int:
        if mark_read:
            return 0

        if unread_count is not None:
            return unread_count

        return current + unread_delta

    def _show_selected_chat(self) -> None:
        chat = self.chat_list.selected_chat()
        if not chat:
            self.status_bar.SetStatusText("Selecciona un chat para abrirlo")
            return

        self._load_conversation(chat)
        self._update_chat_summary(chat.jid, mark_read=True)
        self.chat_list.Hide()
        self.conversation.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self.conversation.focus_composer()
        self.status_bar.SetStatusText(f"Chat abierto: {chat.name}")
        if chat.jid not in self.history_loaded_chats:
            self.status_bar.SetStatusText(f"Cargando todo el historial de {chat.name}...")
            self.xmpp.load_history(chat.jid)

    def _show_chat_list(self) -> None:
        self.conversation.Hide()
        self.chat_list.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self.chat_list.focus()

    def _update_chat_names(self, chats: list[Chat]) -> None:
        for chat in chats:
            self.chat_names_by_jid[chat.jid] = chat.name

    def _display_name_for_jid(self, jid: str) -> str:
        if jid in self.chat_names_by_jid:
            return self.chat_names_by_jid[jid]

        local_part = jid.split("@", 1)[0]
        if jid.endswith("@whatsapp.xmpp.marco-ml.com") and local_part:
            return local_part.removeprefix("+")

        return jid
