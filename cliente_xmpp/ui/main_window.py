from __future__ import annotations

from datetime import datetime, timedelta

import wx

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.audio.notification import NewMessageSound
from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.connection_header_panel import ConnectionHeaderPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.events import EVT_XMPP_EVENT, WxXmppEvent
from cliente_xmpp.ui.login_panel import LoginData, LoginPanel
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
    MessageHistoryLoaded,
    MessageReceived,
    RosterLoaded,
    XmppConnected,
    XmppDisconnected,
    XmppError,
    XmppEvent,
)

HISTORY_PAGE_SIZE = 20
PRELOAD_CHAT_LIMIT = 20
PRELOAD_CONCURRENCY = 4


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Cliente XMPP", size=(980, 700))

        self.settings_store = SettingsStore()
        self.credential_store = CredentialStore()
        self.connection_settings = self.settings_store.load_connection()
        self.speaker = NvdaSpeaker()
        self.new_message_sound = NewMessageSound()
        self.xmpp = XmppService(self._post_xmpp_event)
        self.messages_by_chat: dict[str, list[Message]] = {}
        self.latest_message_timestamps_by_chat: dict[str, float] = {}
        self.history_loaded_chats: set[str] = set()
        self.history_exhausted_chats: set[str] = set()
        self.history_loading_chats: set[str] = set()
        self.preloaded_history_chats: set[str] = set()
        self.chat_names_by_jid: dict[str, str] = {}
        self.roster_jids: set[str] = set()
        self.loaded_chat_summaries = 0
        self.reply_context: Message | None = None
        self.current_jid = ""

        self.login_panel = LoginPanel(self, self.connection_settings)
        self.workspace_panel: wx.Panel
        self.content_panel: wx.Panel
        self.content_box: wx.BoxSizer
        self.connection_header: ConnectionHeaderPanel
        self.chat_list: ChatListPanel
        self.conversation: ConversationPanel
        self.status_bar = self.CreateStatusBar()

        self._layout()
        self._bind_events()
        self._load_saved_password()
        self._set_connected_ui(False)
        self.status_bar.SetStatusText("Desconectado")
        self._schedule_auto_connect()

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
        self.conversation.load_older_button.Bind(wx.EVT_BUTTON, self._on_load_older_messages)
        self.conversation.back_button.Bind(wx.EVT_BUTTON, self._on_back_to_chat_list)
        self.conversation.send_button.Bind(wx.EVT_BUTTON, self._on_send_message)
        self.conversation.compose.Bind(wx.EVT_KEY_DOWN, self._on_composer_key_down)
        self.conversation.messages.Bind(wx.EVT_KEY_DOWN, self._on_messages_key_down)
        self.conversation.messages.Bind(wx.EVT_CONTEXT_MENU, self._on_message_context_menu)
        self.conversation.messages.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_message_right_click)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)
        self.Bind(EVT_XMPP_EVENT, self._on_xmpp_event)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_connect(self, _event: wx.CommandEvent) -> None:
        login = self.login_panel.get_login_data()
        if not login.settings.jid or not login.password:
            wx.MessageBox("El JID y el password son obligatorios.", "Datos incompletos")
            return

        self.settings_store.save_connection(login.settings)
        self._save_login_password(login)
        self.current_jid = login.settings.jid
        self.login_panel.set_connecting(True)
        self.status_bar.SetStatusText("Conectando...")
        self.xmpp.connect(login.settings, login.password)

    def _load_saved_password(self) -> None:
        if not self.connection_settings.remember_password:
            return

        password = self.credential_store.get_password(self.connection_settings.jid)
        if password:
            self.login_panel.set_password(password)

    def _save_login_password(self, login: LoginData) -> None:
        if login.remember_password:
            self.credential_store.save_password(login.settings.jid, login.password)
            return

        self.credential_store.delete_password(login.settings.jid)

    def _schedule_auto_connect(self) -> None:
        if not self.connection_settings.auto_connect:
            return

        if not self.connection_settings.remember_password:
            return

        if not self.login_panel.get_login_data().password:
            self.status_bar.SetStatusText(
                "No hay contraseña guardada para conectar automáticamente"
            )
            return

        wx.CallAfter(self._on_connect, wx.CommandEvent())

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
        if self.reply_context:
            reply_to_jid = (
                self.current_jid if self.reply_context.outgoing else self.reply_context.sender_jid
            )
            fallback_end = self.conversation.reply_fallback_end(body)
            self.xmpp.send_reply(
                chat.jid,
                body,
                reply_to_jid,
                self.reply_context.message_id,
                fallback_end=fallback_end,
            )
            self.reply_context = None
            self.conversation.clear_reply_quote()
        else:
            self.xmpp.send_message(chat.jid, body)

    def _on_load_older_messages(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        if chat.jid in self.history_exhausted_chats:
            self.status_bar.SetStatusText("No hay mensajes anteriores")
            return

        self._request_history_page(chat.jid, older=True)

    def _on_composer_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_RETURN and not event.ShiftDown():
            self._on_send_message(wx.CommandEvent())
            return

        event.Skip()

    def _on_messages_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_SPACE and self.conversation.play_selected_audio():
            return

        event.Skip()

    def _on_message_context_menu(self, event: wx.ContextMenuEvent) -> None:
        self._show_message_context_menu()

    def _on_message_right_click(self, event: wx.ListEvent) -> None:
        self.conversation.messages.Select(event.GetIndex())
        self._show_message_context_menu()

    def _show_message_context_menu(self) -> None:
        message = self.conversation.selected_message()
        if not message:
            return

        menu = wx.Menu()
        reply_item = menu.Append(wx.ID_ANY, "Responder")
        copy_item = menu.Append(wx.ID_ANY, "Copiar texto")
        play_item = menu.Append(wx.ID_ANY, "Reproducir audio")
        play_item.Enable(bool(message.audio_url))

        reaction_menu = wx.Menu()
        reaction_items: list[tuple[wx.MenuItem, str]] = []
        for reaction in ("👍", "❤️", "😂", "😮", "😢", "🙏"):
            reaction_items.append((reaction_menu.Append(wx.ID_ANY, reaction), reaction))
        menu.AppendSubMenu(reaction_menu, "Reaccionar")

        star_label = "No destacar" if message.starred else "Destacar"
        star_item = menu.Append(wx.ID_ANY, star_label)

        self.Bind(wx.EVT_MENU, lambda _event: self._reply_to_message(message), reply_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._copy_message_text(message), copy_item)
        self.Bind(wx.EVT_MENU, lambda _event: self.conversation.play_selected_audio(), play_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._toggle_starred_message(message), star_item)
        for item, reaction in reaction_items:
            self.Bind(
                wx.EVT_MENU,
                lambda _event, selected_reaction=reaction: self._react_to_message(
                    message,
                    selected_reaction,
                ),
                item,
            )

        self.PopupMenu(menu)
        menu.Destroy()

    def _reply_to_message(self, message: Message) -> None:
        self.reply_context = message
        self.conversation.insert_reply_quote(message)

    def _copy_message_text(self, message: Message) -> None:
        if not wx.TheClipboard.Open():
            return

        try:
            wx.TheClipboard.SetData(wx.TextDataObject(message.body))
        finally:
            wx.TheClipboard.Close()

    def _toggle_starred_message(self, message: Message) -> None:
        message.starred = not message.starred
        self.conversation.refresh_message(message)

    def _react_to_message(self, message: Message, reaction: str) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        if not message.message_id:
            self.status_bar.SetStatusText("No se puede reaccionar: el mensaje no tiene ID XMPP")
            return

        if reaction not in message.reactions:
            message.reactions = (*message.reactions, reaction)
        self.conversation.refresh_message(message)
        self.xmpp.send_reaction(chat.jid, message.message_id, reaction)

    def _on_xmpp_event(self, event: WxXmppEvent) -> None:
        self._handle_xmpp_event(event.event)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.conversation.close_audio()
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
                self._select_first_chat_if_needed()
                self._speak_incoming_message(message)
                self._play_new_message_sound(message)
            case MessageHistoryLoaded(
                chat_jid=chat_jid,
                messages=messages,
                older=older,
                complete=complete,
            ):
                self._handle_message_history_loaded(chat_jid, messages, older, complete)
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
                    self._select_first_chat_if_needed()
                    self._preload_recent_histories()
                    self.status_bar.SetStatusText(
                        f"{self.loaded_chat_summaries} chats con mensajes cargados"
                    )
            case ChatActivityLoadFinished(loaded_count=loaded_count):
                self.loaded_chat_summaries = max(self.loaded_chat_summaries, loaded_count)
                self._preload_recent_histories()
                self.status_bar.SetStatusText(
                    f"{self.loaded_chat_summaries} chats con mensajes cargados"
                )

    def _handle_message_history_loaded(
        self,
        chat_jid: str,
        messages: list[Message],
        older: bool,
        complete: bool,
    ) -> None:
        self.history_loaded_chats.add(chat_jid)
        self.history_loading_chats.discard(chat_jid)
        if complete:
            self.history_exhausted_chats.add(chat_jid)

        self._merge_messages(chat_jid, messages)
        if messages:
            self._update_chat_activity_from_messages(chat_jid, messages)
            self._update_chat_preview_from_messages(chat_jid, messages)
        self._refresh_chat_order()
        if self.conversation.current_chat and self.conversation.current_chat.jid == chat_jid:
            self._load_conversation(
                self.conversation.current_chat,
                unread_count=self.conversation.unread_marker_count(),
            )
            self._refresh_load_older_button(chat_jid)

        if older:
            loaded_count = len(messages)
            self.status_bar.SetStatusText(f"{loaded_count} mensajes anteriores cargados")
        elif chat_jid in self.preloaded_history_chats:
            self.status_bar.SetStatusText("Historial reciente precargado")
        else:
            self.status_bar.SetStatusText(f"{len(messages)} mensajes cargados")

    def _merge_messages(self, chat_jid: str, messages: list[Message]) -> None:
        merged = self.messages_by_chat.get(chat_jid, []) + messages
        seen: set[tuple[str, str, str, bool, str]] = set()
        unique_messages: list[Message] = []
        for message in sorted(merged, key=lambda current: current.sent_at):
            key = (
                message.sent_at.isoformat(),
                message.sender_jid,
                message.body,
                message.outgoing,
                message.audio_url,
            )
            if key in seen:
                continue

            seen.add(key)
            unique_messages.append(message)

        self.messages_by_chat[chat_jid] = unique_messages

    def _request_history_page(self, chat_jid: str, older: bool = False) -> None:
        if chat_jid in self.history_loading_chats:
            return

        before = self._oldest_message_time(chat_jid) if older else None
        if older and before is None:
            older = False

        if before:
            before = before - timedelta(microseconds=1)

        self.history_loading_chats.add(chat_jid)
        self._refresh_load_older_button(chat_jid)
        self.xmpp.load_history(
            chat_jid,
            limit=HISTORY_PAGE_SIZE,
            before=before,
            older=older,
        )

    def _preload_recent_histories(self) -> None:
        chats = self._sort_chats_by_recency(self.chat_list.chats())[:PRELOAD_CHAT_LIMIT]
        chat_jids = [
            chat.jid
            for chat in chats
            if chat.jid not in self.preloaded_history_chats
            and chat.jid not in self.history_loading_chats
            and chat.jid not in self.history_loaded_chats
        ]
        if not chat_jids:
            return

        self.preloaded_history_chats.update(chat_jids)
        self.history_loading_chats.update(chat_jids)
        self.xmpp.preload_histories(
            chat_jids,
            limit=HISTORY_PAGE_SIZE,
            concurrency=PRELOAD_CONCURRENCY,
        )

    def _oldest_message_time(self, chat_jid: str) -> datetime | None:
        messages = self.messages_by_chat.get(chat_jid, [])
        if not messages:
            return None

        return min(message.sent_at for message in messages)

    def _refresh_load_older_button(self, chat_jid: str) -> None:
        loading = chat_jid in self.history_loading_chats
        exhausted = chat_jid in self.history_exhausted_chats
        self.conversation.load_older_button.Enable(not loading and not exhausted)
        if loading:
            self.conversation.load_older_button.SetLabel("Cargando mensajes...")
        elif exhausted:
            self.conversation.load_older_button.SetLabel("No hay mensajes anteriores")
        else:
            self.conversation.load_older_button.SetLabel("Cargar mensajes anteriores...")

    def _play_new_message_sound(self, message: Message) -> None:
        if message.outgoing:
            return

        self.new_message_sound.play()

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

        name = self._display_name_for_jid(message.chat_jid)
        self.chat_list.upsert_chat(
            Chat(
                jid=message.chat_jid,
                name=name,
                last_message_preview=message.body,
                last_message_at=message.sent_at,
            )
        )
        self.chat_names_by_jid.setdefault(message.chat_jid, name)

    def _select_first_chat(self) -> None:
        chat = self.chat_list.select_first()
        if chat:
            self._load_conversation(chat)
            self.chat_list.focus()

    def _select_first_chat_if_needed(self) -> None:
        if not self.chat_list.IsShown():
            return

        if self.chat_list.selected_chat():
            return

        self._select_first_chat()

    def _speak_incoming_message(self, message: Message) -> None:
        if message.outgoing:
            return

        sender = self._speakable_chat_name(message.chat_jid)
        preview = " ".join(message.body.split())
        if len(preview) > 160:
            preview = f"{preview[:157]}..."
        self.speaker.speak(f"Mensaje de {sender}: {preview}")

    def _speakable_chat_name(self, jid: str) -> str:
        for chat in self.chat_list.chats():
            if chat.jid == jid and chat.name and chat.name != jid:
                return chat.name

        name = self.chat_names_by_jid.get(jid, "")
        if name and name != jid:
            return name

        return self._fallback_display_name_for_jid(jid)

    def _load_conversation(self, chat: Chat, unread_count: int = 0) -> None:
        self.conversation.set_chat(chat)
        self.conversation.set_messages(
            self.messages_by_chat.get(chat.jid, []),
            unread_count=unread_count,
        )
        self._refresh_load_older_button(chat.jid)

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

        self._load_conversation(chat, unread_count=chat.unread_count)
        self._update_chat_summary(chat.jid, mark_read=True)
        self.chat_list.Hide()
        self.conversation.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self.conversation.focus_composer()
        self.status_bar.SetStatusText(f"Chat abierto: {chat.name}")
        if chat.jid not in self.history_loaded_chats:
            self.status_bar.SetStatusText(f"Cargando los últimos {HISTORY_PAGE_SIZE} mensajes...")
            self._request_history_page(chat.jid)

    def _show_chat_list(self) -> None:
        self.reply_context = None
        self.conversation.clear_reply_quote()
        self.conversation.clear_unread_marker()
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
            name = self.chat_names_by_jid[jid]
            if name and name != jid:
                return name

        return self._fallback_display_name_for_jid(jid)

    @staticmethod
    def _fallback_display_name_for_jid(jid: str) -> str:
        bare_jid = jid.split("/", 1)[0]
        local_part = bare_jid.split("@", 1)[0]
        if bare_jid.endswith("@whatsapp.xmpp.marco-ml.com") and local_part:
            return local_part.removeprefix("+")

        return bare_jid
