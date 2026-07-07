from __future__ import annotations

import os
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import wx

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.audio.duration import media_duration_seconds
from cliente_xmpp.audio.notification import NewMessageSound
from cliente_xmpp.audio.recorder import AudioRecordingError, MciAudioRecorder
from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media.downloads import (
    DownloadedMedia,
    download_media,
    has_media,
    local_media_path,
    media_description,
)
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.connection_header_panel import ConnectionHeaderPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.events import EVT_XMPP_EVENT, WxXmppEvent
from cliente_xmpp.ui.login_panel import LoginData, LoginPanel
from cliente_xmpp.ui.theme import apply_theme
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
    ChatsDiscovered,
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
BACKGROUND_SYNC_DELAY_MS = 350


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Cliente XMPP", size=(980, 700))

        self.settings_store = SettingsStore()
        self.credential_store = CredentialStore()
        self.connection_settings = self.settings_store.load_connection()
        self.speaker = NvdaSpeaker()
        self.new_message_sound = NewMessageSound()
        self.message_store = MessageStore()
        self.xmpp = XmppService(self._post_xmpp_event)
        self.messages_by_chat: dict[str, list[Message]] = {}
        self.latest_message_timestamps_by_chat: dict[str, float] = {}
        self.history_loaded_chats: set[str] = set()
        self.history_exhausted_chats: set[str] = set()
        self.history_loading_chats: set[str] = set()
        self.background_history_queue: deque[str] = deque()
        self.background_history_queued_chats: set[str] = set()
        self.background_history_loading_chat = ""
        self.preloaded_history_chats: set[str] = set()
        self.auto_downloading_audio_keys: set[tuple[str, str]] = set()
        self.chat_names_by_jid: dict[str, str] = {}
        self.roster_jids: set[str] = set()
        self.loaded_chat_summaries = 0
        self.reply_context: Message | None = None
        self.current_jid = ""
        self.audio_recorder = MciAudioRecorder()

        self.login_panel = LoginPanel(self, self.connection_settings)
        self.workspace_panel: wx.Panel
        self.content_panel: wx.Panel
        self.content_box: wx.BoxSizer
        self.connection_header: ConnectionHeaderPanel
        self.chat_list: ChatListPanel
        self.conversation: ConversationPanel
        self.status_bar = self.CreateStatusBar()

        self._layout()
        apply_theme(self)
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
        self.conversation = ConversationPanel(
            self.content_panel,
            self._display_name_for_jid,
            initial_audio_speed=self.settings_store.load_audio_speed(),
            on_audio_speed_changed=self._save_audio_speed,
        )
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
        self.chat_list.list_box.Bind(wx.EVT_KEY_DOWN, self._on_chat_list_key_down)
        self.chat_list.open_button.Bind(wx.EVT_BUTTON, self._on_open_selected_chat)
        self.conversation.load_older_button.Bind(wx.EVT_BUTTON, self._on_load_older_messages)
        self.conversation.back_button.Bind(wx.EVT_BUTTON, self._on_back_to_chat_list)
        self.conversation.send_button.Bind(wx.EVT_BUTTON, self._on_primary_send_action)
        self.conversation.attach_button.Bind(wx.EVT_BUTTON, self._on_attach_file)
        self.conversation.pause_recording_button.Bind(wx.EVT_BUTTON, self._on_pause_recording)
        self.conversation.cancel_recording_button.Bind(wx.EVT_BUTTON, self._on_cancel_recording)
        self.conversation.compose.Bind(wx.EVT_TEXT, self._on_composer_text_changed)
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

    def _save_audio_speed(self, speed: float) -> None:
        try:
            self.settings_store.save_audio_speed(speed)
        except Exception:
            return

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

    def _on_chat_list_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_F2:
            self._rename_selected_chat()
            return

        event.Skip()

    def _rename_selected_chat(self) -> None:
        chat = self.chat_list.selected_chat()
        if not chat:
            self.status_bar.SetStatusText("Selecciona un chat para renombrarlo")
            return

        dialog = wx.TextEntryDialog(
            self,
            "Nuevo nombre para este contacto:",
            "Renombrar contacto",
            chat.name,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetValue().strip()
        finally:
            dialog.Destroy()

        if not name:
            self.status_bar.SetStatusText("El nombre no puede quedar vacío")
            return

        renamed_chat = Chat(
            jid=chat.jid,
            name=name,
            custom_name=name,
            is_group=chat.is_group,
            notifications_muted=chat.notifications_muted,
            unread_count=chat.unread_count,
            last_message_preview=chat.last_message_preview,
            last_message_at=chat.last_message_at,
        )
        self.chat_names_by_jid[chat.jid] = name
        self.chat_list.upsert_chat(renamed_chat)
        self._refresh_chat_order(selected_jid=chat.jid)
        if self.conversation.current_chat and self.conversation.current_chat.jid == chat.jid:
            self.conversation.set_chat(renamed_chat)
            self.conversation.set_messages(self.messages_by_chat.get(chat.jid, []))
        if self.current_jid:
            try:
                self.message_store.rename_chat(self.current_jid, chat.jid, name)
            except Exception:
                self.status_bar.SetStatusText("No se pudo guardar el nombre del contacto")
                return

        self.status_bar.SetStatusText(f"Contacto renombrado: {name}")

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

    def _on_primary_send_action(self, _event: wx.CommandEvent) -> None:
        if self.audio_recorder.is_recording:
            self._stop_recording_and_send()
            return

        if self.conversation.has_composed_text():
            self._on_send_message(wx.CommandEvent())
            return

        self._start_recording()

    def _on_send_message(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        body = self.conversation.consume_composed_message()
        if not chat or not body:
            return

        fallback_end = self.conversation.reply_fallback_end(body) if self.reply_context else 0
        display_body = body[fallback_end:].lstrip("\r\n") if fallback_end else body
        message = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            body=display_body,
            outgoing=True,
            chat_is_group=chat.is_group,
            reply_quote=self.reply_context.body if self.reply_context else "",
        )
        self._store_message(message)
        self._update_chat_from_message(message)
        self.conversation.append_message(message)
        self._refresh_chat_order(chat.jid)
        if self.reply_context:
            reply_to_jid = (
                self.current_jid if self.reply_context.outgoing else self.reply_context.sender_jid
            )
            self.xmpp.send_reply(
                chat.jid,
                body,
                reply_to_jid,
                self.reply_context.message_id,
                fallback_end=fallback_end,
                is_group=chat.is_group,
            )
            self.reply_context = None
            self.conversation.clear_reply_quote()
        else:
            self.xmpp.send_message(chat.jid, body, is_group=chat.is_group)

    def _on_composer_text_changed(self, event: wx.CommandEvent) -> None:
        self.conversation.update_send_button_state(self.audio_recorder.is_recording)
        event.Skip()

    def _on_load_older_messages(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        if chat.jid in self.history_exhausted_chats:
            self.status_bar.SetStatusText("No hay mensajes anteriores")
            return

        self._request_history_page(chat.jid, older=True)

    def _on_attach_file(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        dialog = wx.FileDialog(
            self,
            "Selecciona un archivo para adjuntar",
            wildcard="Todos los archivos (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
        finally:
            dialog.Destroy()

        self._send_files_to_chat(chat, [Path(path)])

    def _attach_clipboard_files(self) -> bool:
        paths = self._clipboard_file_paths()
        if not paths:
            return False

        chat = self.conversation.current_chat
        if not chat:
            self.status_bar.SetStatusText("Selecciona un chat para adjuntar archivos")
            return True

        self._send_files_to_chat(chat, paths)
        return True

    def _send_files_to_chat(self, chat: Chat, paths: list[Path]) -> None:
        files = [path for path in paths if path.is_file()]
        if not files:
            self.status_bar.SetStatusText("El portapapeles no contiene archivos validos")
            return

        if len(files) == 1:
            self.status_bar.SetStatusText("Subiendo archivo...")
        else:
            self.status_bar.SetStatusText(f"Subiendo {len(files)} archivos...")

        for path in files:
            self.xmpp.send_file(chat.jid, str(path), is_group=chat.is_group)

    @staticmethod
    def _clipboard_file_paths() -> list[Path]:
        if not wx.TheClipboard.Open():
            return []

        try:
            if not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
                return []

            data = wx.FileDataObject()
            if not wx.TheClipboard.GetData(data):
                return []

            return [Path(filename) for filename in data.GetFilenames()]
        finally:
            wx.TheClipboard.Close()

    def _start_recording(self) -> None:
        if not self.conversation.current_chat:
            return

        try:
            self.audio_recorder.start()
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabacion")
            return

        self.conversation.set_recording_state(True)
        self.status_bar.SetStatusText("Grabando audio...")
        self.speaker.speak("Grabando audio")

    def _on_pause_recording(self, _event: wx.CommandEvent) -> None:
        if not self.audio_recorder.is_recording:
            return

        try:
            if self.audio_recorder.is_paused:
                self.audio_recorder.resume()
                self.status_bar.SetStatusText("Grabando audio...")
                self.speaker.speak("Grabando")
            else:
                self.audio_recorder.pause()
                self.status_bar.SetStatusText("Grabacion pausada")
                self.speaker.speak("Pausado")
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabacion")
            return

        self.conversation.set_recording_state(True, self.audio_recorder.is_paused)

    def _on_cancel_recording(self, _event: wx.CommandEvent) -> None:
        self.audio_recorder.cancel()
        self.conversation.set_recording_state(False)
        self.status_bar.SetStatusText("Grabacion cancelada")
        self.speaker.speak("Cancelado")

    def _stop_recording_and_send(self) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        try:
            path = self.audio_recorder.stop_and_save()
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabacion")
            self.conversation.set_recording_state(False)
            return

        self.conversation.set_recording_state(False)
        self.status_bar.SetStatusText("Subiendo audio...")
        self.xmpp.send_file(chat.jid, str(path), is_group=chat.is_group)

    def _on_composer_key_down(self, event: wx.KeyEvent) -> None:
        if (
            event.ControlDown()
            and not event.AltDown()
            and event.GetKeyCode() == ord("V")
            and self._attach_clipboard_files()
        ):
            return

        if event.GetKeyCode() == wx.WXK_RETURN and not event.ShiftDown():
            self._on_primary_send_action(wx.CommandEvent())
            return

        event.Skip()

    def _on_messages_key_down(self, event: wx.KeyEvent) -> None:
        if (
            event.GetKeyCode() == wx.WXK_LEFT
            and self.conversation.speak_selected_text_message()
        ):
            return

        if event.GetKeyCode() == ord("S"):
            speed = self.conversation.cycle_selected_audio_speed()
            if speed is not None:
                self.status_bar.SetStatusText(f"Velocidad de audio: {speed:g}x")
                return

        if event.GetKeyCode() == wx.WXK_SPACE and self.conversation.play_selected_video():
            return

        if event.GetKeyCode() in (wx.WXK_SPACE, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            message = self.conversation.selected_message()
            if (
                message
                and has_media(message)
                and message.media_kind not in {"audio", "video"}
            ):
                self._open_or_download_media(message)
                return

        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self.conversation.open_selected_message_reader():
                return

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
        media_item: wx.MenuItem | None = None
        copy_file_item: wx.MenuItem | None = None
        describe_item: wx.MenuItem | None = None
        play_item: wx.MenuItem | None = None
        if has_media(message):
            media_label = "Abrir archivo" if local_media_path(message) else "Descargar archivo"
            if message.media_kind == "image":
                media_label = "Abrir foto" if local_media_path(message) else "Descargar foto"
            elif message.media_kind == "video":
                media_label = "Abrir video" if local_media_path(message) else "Descargar video"
            elif message.media_kind == "audio":
                media_label = "Reproducir audio" if message.audio_url else media_label
            media_item = menu.Append(wx.ID_ANY, media_label)
            copy_file_item = menu.Append(wx.ID_ANY, "Copiar archivo")
            copy_file_item.Enable(local_media_path(message) is not None)
            if message.media_kind in {"image", "video"}:
                describe_item = menu.Append(wx.ID_ANY, "Describir con RayoAI")
        else:
            play_item = menu.Append(wx.ID_ANY, "Reproducir audio")
            play_item.Enable(False)

        reaction_menu = wx.Menu()
        reaction_items: list[tuple[wx.MenuItem, str]] = []
        for reaction in ("👍", "❤️", "😂", "😮", "😢", "🙏"):
            reaction_items.append((reaction_menu.Append(wx.ID_ANY, reaction), reaction))
        menu.AppendSubMenu(reaction_menu, "Reaccionar")

        star_label = "No destacar" if message.starred else "Destacar"
        star_item = menu.Append(wx.ID_ANY, star_label)

        self.Bind(wx.EVT_MENU, lambda _event: self._reply_to_message(message), reply_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._copy_message_text(message), copy_item)
        if media_item:
            if message.media_kind == "audio":
                self.Bind(
                    wx.EVT_MENU,
                    lambda _event: self.conversation.play_selected_audio(),
                    media_item,
                )
            else:
                self.Bind(
                    wx.EVT_MENU,
                    lambda _event: self._open_or_download_media(message),
                    media_item,
                )
        if copy_file_item:
            self.Bind(wx.EVT_MENU, lambda _event: self._copy_media_file(message), copy_file_item)
        if describe_item:
            self.Bind(
                wx.EVT_MENU,
                lambda _event: self._describe_media_with_rayoai(message),
                describe_item,
            )
        if play_item:
            self.Bind(
                wx.EVT_MENU,
                lambda _event: self.conversation.play_selected_audio(),
                play_item,
            )
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

    def _open_or_download_media(self, message: Message) -> None:
        path = local_media_path(message)
        if path:
            self._open_media_path(path)
            return

        self._download_media(message)

    def _download_media(
        self,
        message: Message,
        send_to_rayoai: bool = False,
        silent: bool = False,
    ) -> None:
        if not self.current_jid:
            if not silent:
                self.status_bar.SetStatusText("No hay cuenta conectada para guardar la descarga")
            return

        if not silent:
            self.status_bar.SetStatusText(f"Descargando {media_description(message)}...")

        def worker() -> None:
            try:
                downloaded = download_media(message, self.current_jid)
            except Exception as exc:
                if not silent:
                    wx.CallAfter(
                        self.status_bar.SetStatusText,
                        f"No se pudo descargar el archivo: {exc}",
                    )
                if silent:
                    wx.CallAfter(self._discard_auto_audio_download, message)
                return

            wx.CallAfter(
                self._finish_media_download,
                message,
                downloaded,
                send_to_rayoai,
                silent,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_media_download(
        self,
        message: Message,
        downloaded: DownloadedMedia,
        send_to_rayoai: bool,
        silent: bool = False,
    ) -> None:
        message.media_local_path = str(downloaded.path)
        message.media_size = downloaded.size
        message.media_mime = downloaded.mime or message.media_mime
        message.media_filename = downloaded.filename or message.media_filename
        if message.media_kind == "audio":
            message.media_duration_seconds = media_duration_seconds(downloaded.path)
        self._persist_message_media_path(message)
        self.conversation.refresh_message(message)
        self._update_chat_from_message(message)
        self._refresh_chat_order(message.chat_jid)
        if silent:
            self._discard_auto_audio_download(message)
        else:
            self.status_bar.SetStatusText(f"Archivo descargado: {downloaded.path}")
        if send_to_rayoai:
            self._send_media_to_rayoai(message)

    def _discard_auto_audio_download(self, message: Message) -> None:
        self.auto_downloading_audio_keys.discard(self._auto_audio_download_key(message))

    def _persist_message_media_path(self, message: Message) -> None:
        if not self.current_jid:
            return

        try:
            self.message_store.update_message_media_local_path(self.current_jid, message)
        except Exception:
            return

    def _auto_download_audio_messages(self, messages: list[Message]) -> None:
        for message in messages:
            self._auto_download_audio_message(message)

    def _auto_download_audio_message(self, message: Message) -> None:
        if message.media_kind != "audio" or not message.audio_url:
            return

        if message.outgoing or local_media_path(message) is not None:
            return

        key = self._auto_audio_download_key(message)
        if key in self.auto_downloading_audio_keys:
            return

        self.auto_downloading_audio_keys.add(key)
        self._download_media(message, silent=True)

    @staticmethod
    def _auto_audio_download_key(message: Message) -> tuple[str, str]:
        stable_id = message.message_id or message.audio_url
        return message.chat_jid, stable_id

    def _copy_media_file(self, message: Message) -> None:
        path = local_media_path(message)
        if path is None:
            self.status_bar.SetStatusText("Descarga el archivo antes de copiarlo")
            return

        if not wx.TheClipboard.Open():
            return

        try:
            data = wx.FileDataObject()
            data.AddFile(str(path))
            wx.TheClipboard.SetData(data)
            self.status_bar.SetStatusText(f"Archivo copiado: {path.name}")
        finally:
            wx.TheClipboard.Close()

    def _describe_media_with_rayoai(self, message: Message) -> None:
        if local_media_path(message) is None:
            self._download_media(message, send_to_rayoai=True)
            return

        self._send_media_to_rayoai(message)

    def _send_media_to_rayoai(self, message: Message) -> None:
        path = local_media_path(message)
        if path is None:
            self.status_bar.SetStatusText("Descarga el archivo antes de enviarlo a RayoAI")
            return

        if rayoai.send_open_path(path):
            self.status_bar.SetStatusText("Archivo enviado a RayoAI")
            return

        self.status_bar.SetStatusText("No se pudo enviar a RayoAI. Verifica que esté abierto.")

    def _open_media_path(self, path: object) -> None:
        try:
            os.startfile(path)
        except OSError as exc:
            self.status_bar.SetStatusText(f"No se pudo abrir el archivo: {exc}")

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
        self.xmpp.send_reaction(
            chat.jid,
            message.message_id,
            reaction,
            is_group=chat.is_group,
        )

    def _on_xmpp_event(self, event: WxXmppEvent) -> None:
        self._handle_xmpp_event(event.event)

    def _on_close(self, event: wx.CloseEvent) -> None:
        self.conversation.close_audio()
        self.audio_recorder.cancel()
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
                if not self.workspace_panel.IsShown():
                    self._set_connected_ui(True)
                self.status_bar.SetStatusText("Conectado")
            case XmppDisconnected(reason=reason):
                self.login_panel.set_connecting(False)
                if reason:
                    self.connection_header.set_status(reason)
                    self.status_bar.SetStatusText(reason)
                else:
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
                cached_chats = self._load_cached_chats()
                self.xmpp.monitor_group_chats(
                    [chat.jid for chat in cached_chats if chat.is_group]
                )
                self.loaded_chat_summaries = len(cached_chats)
                self.chat_list.set_chats(self._sort_chats_by_recency(cached_chats))
                self._select_first_chat_if_needed()
                self.status_bar.SetStatusText(
                    f"{self.loaded_chat_summaries} chats cacheados. Buscando actualizaciones..."
                )
            case ChatsDiscovered(chats=chats):
                self._upsert_discovered_chats(chats)
                self._preload_recent_histories()
            case MessageReceived(message=message):
                message, added_message = self._store_message(message)
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
                    if added_message:
                        self.conversation.append_message(message)
                    else:
                        self.conversation.refresh_message(message)
                self._auto_download_audio_message(message)
                self._select_first_chat_if_needed()
                self._speak_incoming_message(message)
                self._play_new_message_sound(message)
            case MessageHistoryLoaded(
                chat_jid=chat_jid,
                messages=messages,
                older=older,
                complete=complete,
                background=background,
            ):
                self._handle_message_history_loaded(chat_jid, messages, older, complete, background)
            case ChatActivityLoaded(
                chat_jid=chat_jid,
                sent_at=sent_at,
                preview=preview,
                unread_count=unread_count,
                is_group=is_group,
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
                        is_group=is_group,
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
        background: bool = False,
    ) -> None:
        if background:
            self.background_history_loading_chat = ""
            self.background_history_queued_chats.discard(chat_jid)
        else:
            self.history_loading_chats.discard(chat_jid)
        empty_preview_chat = not messages and self._chat_has_preview(chat_jid)
        if empty_preview_chat:
            self.history_loaded_chats.discard(chat_jid)
            self.history_exhausted_chats.discard(chat_jid)
            self.preloaded_history_chats.discard(chat_jid)
        else:
            self.history_loaded_chats.add(chat_jid)
        if complete and not empty_preview_chat:
            self.history_exhausted_chats.add(chat_jid)

        self._normalize_audio_metadata_for_messages(messages)
        self._merge_messages(chat_jid, messages)
        self._persist_messages(messages)
        if messages:
            self._update_chat_activity_from_messages(chat_jid, messages)
            self._update_chat_preview_from_messages(chat_jid, messages)
            self._auto_download_audio_messages(messages)
        self._refresh_chat_order()
        if (
            not background
            and self.conversation.current_chat
            and self.conversation.current_chat.jid == chat_jid
        ):
            self._load_conversation(
                self.conversation.current_chat,
                unread_count=self.conversation.unread_marker_count(),
            )
            self._refresh_load_older_button(chat_jid)

        if background:
            if messages and not complete:
                self._enqueue_background_history_sync([chat_jid])
            wx.CallLater(BACKGROUND_SYNC_DELAY_MS, self._pump_background_history_sync)
            return

        if older:
            loaded_count = len(messages)
            self.status_bar.SetStatusText(f"{loaded_count} mensajes anteriores cargados")
        elif chat_jid in self.preloaded_history_chats:
            self.status_bar.SetStatusText("Historial reciente precargado")
        else:
            self.status_bar.SetStatusText(f"{len(messages)} mensajes cargados")

    def _merge_messages(self, chat_jid: str, messages: list[Message]) -> None:
        merged = self.messages_by_chat.get(chat_jid, []) + messages
        indexes_by_key: dict[tuple[object, ...], int] = {}
        unique_messages: list[Message] = []
        for message in sorted(merged, key=self._message_timestamp):
            key = self._message_merge_key(message)
            existing_index = indexes_by_key.get(key)
            if existing_index is not None:
                self._merge_message_metadata(unique_messages[existing_index], message)
                continue

            indexes_by_key[key] = len(unique_messages)
            unique_messages.append(message)

        self.messages_by_chat[chat_jid] = unique_messages

    @staticmethod
    def _message_merge_key(message: Message) -> tuple[object, ...]:
        if message.message_id:
            return "id", message.message_id

        return (
            "payload",
            message.sent_at.isoformat(),
            message.sender_jid,
            message.body,
            message.outgoing,
            message.audio_url,
            message.media_url,
            message.reply_quote,
        )

    @staticmethod
    def _merge_message_metadata(target: Message, incoming: Message) -> None:
        if not target.body and incoming.body:
            target.body = incoming.body
        if not target.audio_url and incoming.audio_url:
            target.audio_url = incoming.audio_url
        if not target.media_url and incoming.media_url:
            target.media_url = incoming.media_url
        if not target.media_kind and incoming.media_kind:
            target.media_kind = incoming.media_kind
        if not target.media_mime and incoming.media_mime:
            target.media_mime = incoming.media_mime
        if not target.media_filename and incoming.media_filename:
            target.media_filename = incoming.media_filename
        if target.media_size <= 0 and incoming.media_size > 0:
            target.media_size = incoming.media_size
        if target.media_duration_seconds <= 0 and incoming.media_duration_seconds > 0:
            target.media_duration_seconds = incoming.media_duration_seconds
        if not target.media_local_path and incoming.media_local_path:
            target.media_local_path = incoming.media_local_path
        if not target.reply_quote and incoming.reply_quote:
            target.reply_quote = incoming.reply_quote

    def _request_history_page(
        self,
        chat_jid: str,
        older: bool = False,
        background: bool = False,
    ) -> None:
        if not background and chat_jid in self.history_loading_chats:
            return

        before = self._oldest_message_time(chat_jid) if older else None
        if older and before is None:
            older = False

        if before:
            before = before - timedelta(microseconds=1)

        if not background:
            self.history_loading_chats.add(chat_jid)
            self.preloaded_history_chats.discard(chat_jid)
            self._refresh_load_older_button(chat_jid)
        self.xmpp.load_history(
            chat_jid,
            limit=HISTORY_PAGE_SIZE,
            before=before,
            older=older,
            allow_unfiltered_fallback=not background,
            background=background,
        )

    def _chat_has_preview(self, chat_jid: str) -> bool:
        for chat in self.chat_list.chats():
            if chat.jid == chat_jid:
                return bool(chat.last_message_preview or chat.last_message_at)

        return False

    def _chat_history_needs_reload(self, chat_jid: str) -> bool:
        if not self._chat_has_preview(chat_jid):
            return False

        messages = self.messages_by_chat.get(chat_jid, [])
        if not messages:
            return True

        return chat_jid in self.preloaded_history_chats and all(
            message.outgoing for message in messages
        )

    def _enqueue_background_history_sync(self, chat_jids: list[str]) -> None:
        for chat_jid in chat_jids:
            if chat_jid in self.history_exhausted_chats:
                continue
            if chat_jid == self.background_history_loading_chat:
                continue
            if chat_jid in self.background_history_queued_chats:
                continue

            self.background_history_queue.append(chat_jid)
            self.background_history_queued_chats.add(chat_jid)

        self._pump_background_history_sync()

    def _pump_background_history_sync(self) -> None:
        if self.background_history_loading_chat:
            return

        while self.background_history_queue:
            chat_jid = self.background_history_queue.popleft()
            self.background_history_queued_chats.discard(chat_jid)
            if chat_jid in self.history_exhausted_chats:
                continue

            self.background_history_loading_chat = chat_jid
            self._request_history_page(
                chat_jid,
                older=bool(self._oldest_message_time(chat_jid)),
                background=True,
            )
            return

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
        self._enqueue_background_history_sync(chat_jids)

    def _oldest_message_time(self, chat_jid: str) -> datetime | None:
        messages = self.messages_by_chat.get(chat_jid, [])
        if not messages:
            return None

        return min(messages, key=self._message_timestamp).sent_at

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
        if message.outgoing or self._message_notifications_muted(message):
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

    def _store_message(self, message: Message) -> tuple[Message, bool]:
        self._normalize_audio_metadata(message)
        existing_keys = {
            self._message_merge_key(existing)
            for existing in self.messages_by_chat.get(message.chat_jid, [])
        }
        message_key = self._message_merge_key(message)
        self._merge_messages(message.chat_jid, [message])
        stored_message = self._message_by_merge_key(message.chat_jid, message_key) or message
        self._update_chat_activity(message.chat_jid, self._message_timestamp(message))
        self._persist_messages([stored_message])
        return stored_message, message_key not in existing_keys

    def _message_by_merge_key(
        self,
        chat_jid: str,
        key: tuple[object, ...],
    ) -> Message | None:
        for message in self.messages_by_chat.get(chat_jid, []):
            if self._message_merge_key(message) == key:
                return message

        return None

    def _ensure_chat_for_message(self, message: Message) -> None:
        if self.chat_list.has_chat(message.chat_jid):
            return

        name = self._display_name_for_jid(message.chat_jid)
        preview = media_description(message) if has_media(message) else message.body
        self.chat_list.upsert_chat(
            Chat(
                jid=message.chat_jid,
                name=name,
                is_group=message.chat_is_group,
                notifications_muted=self._chat_notifications_muted(message.chat_jid),
                last_message_preview=preview,
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
        if message.outgoing or self._message_notifications_muted(message):
            return

        sender = self._speakable_chat_name(message.chat_jid)
        if message.chat_is_group and message.sender_jid:
            sender = f"{self._display_name_for_jid(message.sender_jid)} en {sender}"
        preview = media_description(message) if has_media(message) else message.body
        preview = " ".join(preview.split())
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

    def _load_cached_chats(self) -> list[Chat]:
        if not self.current_jid:
            return []

        try:
            chats = self.message_store.load_chats(self.current_jid)
            latest_messages = self.message_store.load_latest_messages(self.current_jid)
        except Exception:
            return []

        chats_by_jid = {chat.jid: chat for chat in chats}
        for message in latest_messages:
            if self._normalize_audio_metadata(message):
                self._persist_messages([message])
            chat = chats_by_jid.get(message.chat_jid)
            if chat is None:
                chat = Chat(
                    jid=message.chat_jid,
                    name=self._display_name_for_jid(message.chat_jid),
                    is_group=message.chat_is_group,
                )
                chats.append(chat)
                chats_by_jid[message.chat_jid] = chat

            if self._summary_preview_can_update(
                chat.last_message_at,
                message.sent_at,
                allow_equal=True,
            ):
                chat.last_message_preview = (
                    media_description(message) if has_media(message) else message.body
                )
                chat.last_message_at = message.sent_at
                self._persist_chat(chat)
            self._update_chat_activity(message.chat_jid, self._message_timestamp(message))

        for chat in chats:
            if chat.is_group and not self._jid_may_be_group_chat(chat.jid):
                chat.is_group = False
                self.message_store.set_chat_group_flag(self.current_jid, chat.jid, False)
            if chat.custom_name:
                chat.name = chat.custom_name
                self.chat_names_by_jid[chat.jid] = chat.custom_name
            elif chat.jid in self.chat_names_by_jid:
                chat.name = self._display_name_for_jid(chat.jid)
        return chats

    def _load_cached_messages_for_chat(self, chat_jid: str) -> None:
        if not self.current_jid:
            return

        try:
            cached_messages = self.message_store.load_recent_messages(self.current_jid, chat_jid)
        except Exception:
            return

        if cached_messages:
            self._normalize_audio_metadata_for_messages(cached_messages)
            self._merge_messages(chat_jid, cached_messages)
            self._update_chat_activity_from_messages(chat_jid, cached_messages)
            self._update_chat_preview_from_messages(chat_jid, cached_messages)

    def _persist_chat(self, chat: Chat) -> None:
        if not self.current_jid:
            return

        try:
            self.message_store.upsert_chat(self.current_jid, chat)
        except Exception:
            return

    def _persist_messages(self, messages: list[Message]) -> None:
        if not self.current_jid or not messages:
            return

        try:
            self.message_store.upsert_messages(self.current_jid, messages)
        except Exception:
            return

    def _normalize_audio_metadata_for_messages(self, messages: list[Message]) -> None:
        changed_messages = [
            message for message in messages if self._normalize_audio_metadata(message)
        ]
        self._persist_messages(changed_messages)

    def _normalize_audio_metadata(self, message: Message) -> bool:
        if message.media_kind != "audio" or message.media_duration_seconds > 0:
            return False

        path = local_media_path(message)
        if path is None:
            return False

        duration = media_duration_seconds(path)
        if duration <= 0:
            return False

        message.media_duration_seconds = duration
        return True

    def _load_conversation(self, chat: Chat, unread_count: int = 0) -> None:
        self._load_cached_messages_for_chat(chat.jid)
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
            no_preview_rank = 1 if chat.is_group else 2
            return (no_preview_rank, 0, chat.name.casefold())

        return (0, -latest, chat.name.casefold())

    @staticmethod
    def _jid_may_be_group_chat(jid: str) -> bool:
        bare_jid = jid.split("/", 1)[0]
        if "@" not in bare_jid:
            return False

        local_part = bare_jid.split("@", 1)[0]
        return bool(local_part and not local_part.startswith("+"))

    def _latest_message_timestamp(self, chat_jid: str) -> float | None:
        latest = self.latest_message_timestamps_by_chat.get(chat_jid)
        messages = self.messages_by_chat.get(chat_jid, [])
        if not messages:
            return latest

        message_latest = max(self._message_timestamp(message) for message in messages)
        if latest is None:
            return message_latest

        return max(latest, message_latest)

    @staticmethod
    def _message_timestamp(message: Message) -> float:
        try:
            return message.sent_at.timestamp()
        except (OSError, ValueError):
            return 0

    def _update_chat_activity_from_messages(self, chat_jid: str, messages: list[Message]) -> None:
        if not messages:
            return

        self._update_chat_activity(
            chat_jid,
            max(self._message_timestamp(message) for message in messages),
        )

    def _update_chat_activity(self, chat_jid: str, timestamp: float) -> None:
        current = self.latest_message_timestamps_by_chat.get(chat_jid)
        if current is None or timestamp > current:
            self.latest_message_timestamps_by_chat[chat_jid] = timestamp

    def _update_chat_preview_from_messages(self, chat_jid: str, messages: list[Message]) -> None:
        if not messages:
            return

        latest_message = self._latest_message_from_sequence(messages)
        self._update_chat_from_message(latest_message)

    def _latest_message_from_sequence(self, messages: list[Message]) -> Message:
        return max(
            enumerate(messages),
            key=lambda item: (self._message_timestamp(item[1]), item[0]),
        )[1]

    def _update_chat_from_message(self, message: Message, mark_unread: bool = False) -> None:
        preview = media_description(message) if has_media(message) else message.body
        self._update_chat_summary(
            message.chat_jid,
            preview=preview,
            sent_at=message.sent_at,
            unread_delta=1 if mark_unread else 0,
            force_preview=True,
            is_group=message.chat_is_group,
        )

    def _update_chat_summary(
        self,
        chat_jid: str,
        preview: str = "",
        sent_at: datetime | None = None,
        unread_delta: int = 0,
        unread_count: int | None = None,
        mark_read: bool = False,
        force_preview: bool = False,
        is_group: bool = False,
    ) -> None:
        chats = self.chat_list.chats()
        for chat in chats:
            if chat.jid != chat_jid:
                continue

            updated_chat = Chat(
                jid=chat.jid,
                name=chat.name,
                custom_name=chat.custom_name,
                is_group=chat.is_group or is_group,
                notifications_muted=chat.notifications_muted,
                unread_count=self._next_unread_count(
                    chat.unread_count,
                    unread_delta=unread_delta,
                    unread_count=unread_count,
                    mark_read=mark_read,
                ),
                last_message_preview=self._next_chat_preview(
                    chat,
                    preview,
                    sent_at,
                    force_preview=force_preview,
                ),
                last_message_at=self._next_chat_timestamp(
                    chat,
                    sent_at,
                    force_preview=force_preview,
                ),
            )
            self.chat_list.upsert_chat(updated_chat)
            self._persist_chat(updated_chat)
            return

        updated_chat = Chat(
            jid=chat_jid,
            name=self._display_name_for_jid(chat_jid),
            is_group=is_group,
            notifications_muted=self._chat_notifications_muted(chat_jid),
            unread_count=self._next_unread_count(
                0,
                unread_delta=unread_delta,
                unread_count=unread_count,
                mark_read=mark_read,
            ),
            last_message_preview=preview,
            last_message_at=sent_at,
        )
        self.chat_list.upsert_chat(updated_chat)
        self._persist_chat(updated_chat)
        self.chat_names_by_jid.setdefault(chat_jid, self._display_name_for_jid(chat_jid))

    @classmethod
    def _next_chat_preview(
        cls,
        chat: Chat,
        preview: str,
        sent_at: datetime | None,
        force_preview: bool = False,
    ) -> str:
        if not preview:
            return chat.last_message_preview

        if force_preview and cls._summary_preview_can_update(
            chat.last_message_at,
            sent_at,
            allow_equal=True,
        ):
            return preview

        if cls._summary_preview_can_update(chat.last_message_at, sent_at):
            return preview

        return chat.last_message_preview

    @classmethod
    def _next_chat_timestamp(
        cls,
        chat: Chat,
        sent_at: datetime | None,
        force_preview: bool = False,
    ) -> datetime | None:
        if sent_at is None:
            return chat.last_message_at

        if cls._summary_preview_can_update(
            chat.last_message_at,
            sent_at,
            allow_equal=force_preview,
        ):
            return sent_at

        return chat.last_message_at

    @classmethod
    def _summary_preview_can_update(
        cls,
        current_sent_at: datetime | None,
        incoming_sent_at: datetime | None,
        allow_equal: bool = False,
    ) -> bool:
        if incoming_sent_at is None:
            return current_sent_at is None

        current_timestamp = cls._datetime_timestamp(current_sent_at)
        if current_timestamp is None:
            return True

        incoming_timestamp = cls._datetime_timestamp(incoming_sent_at)
        if incoming_timestamp is None:
            return False

        if allow_equal:
            return incoming_timestamp >= current_timestamp

        return incoming_timestamp > current_timestamp

    @staticmethod
    def _datetime_timestamp(value: datetime | None) -> float | None:
        if value is None:
            return None

        try:
            return value.timestamp()
        except (OSError, ValueError):
            return None

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

        if chat.is_group:
            self.xmpp.join_group_chat(chat.jid)
        self._load_conversation(chat, unread_count=chat.unread_count)
        self._update_chat_summary(chat.jid, mark_read=True)
        self.chat_list.Hide()
        self.conversation.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self.conversation.focus_composer()
        self.status_bar.SetStatusText(f"Chat abierto: {chat.name}")
        needs_history = (
            chat.jid not in self.history_loaded_chats
            or self._chat_history_needs_reload(chat.jid)
        )
        if needs_history:
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

    def _upsert_discovered_chats(self, chats: list[Chat]) -> None:
        if not chats:
            return

        added = 0
        for chat in chats:
            existing = self._chat_by_jid(chat.jid)
            if existing is not None:
                merged_chat = Chat(
                    jid=existing.jid,
                    name=existing.custom_name or chat.name or existing.name,
                    custom_name=existing.custom_name,
                    is_group=existing.is_group or chat.is_group,
                    notifications_muted=existing.notifications_muted
                    or chat.notifications_muted,
                    unread_count=existing.unread_count,
                    last_message_preview=existing.last_message_preview,
                    last_message_at=existing.last_message_at,
                )
            else:
                merged_chat = chat
                added += 1

            self.chat_names_by_jid[merged_chat.jid] = merged_chat.name
            self.chat_list.upsert_chat(merged_chat)
            self._persist_chat(merged_chat)

        self.loaded_chat_summaries += added
        self._refresh_chat_order()
        self._select_first_chat_if_needed()
        self.status_bar.SetStatusText(f"{self.loaded_chat_summaries} chats disponibles")

    def _chat_by_jid(self, jid: str) -> Chat | None:
        for chat in self.chat_list.chats():
            if chat.jid == jid:
                return chat

        return None

    def _chat_notifications_muted(self, jid: str) -> bool:
        chat = self._chat_by_jid(jid)
        return bool(chat and chat.notifications_muted)

    def _message_notifications_muted(self, message: Message) -> bool:
        chat = self._chat_by_jid(message.chat_jid)
        if chat is not None and chat.is_group:
            return True

        return message.chat_is_group or self._chat_notifications_muted(message.chat_jid)

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
        bare_jid, separator, resource = jid.partition("/")
        if separator and resource:
            return resource

        local_part = bare_jid.split("@", 1)[0]
        if local_part.startswith("+"):
            return local_part.removeprefix("+")

        return bare_jid
