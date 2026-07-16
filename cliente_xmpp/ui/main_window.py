from __future__ import annotations

import hashlib
import os
import threading
import time
import unicodedata
import uuid
import webbrowser
from collections import Counter, deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import wx

from cliente_xmpp.accessibility.speaker import NvdaSpeaker
from cliente_xmpp.audio.duration import media_duration_seconds
from cliente_xmpp.audio.notification import (
    NewMessageSound,
    OpenChatMessageSound,
    SentMessageSound,
)
from cliente_xmpp.audio.recorder import AudioRecordingError, MciAudioRecorder
from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import APP_DIR, DesktopNotificationSettings, SettingsStore
from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media.downloads import (
    DownloadedMedia,
    download_media,
    has_media,
    local_media_path,
    media_description,
)
from cliente_xmpp.media.links import MessageLink, is_link_preview, message_links
from cliente_xmpp.media.stickers import (
    convert_lottie_sticker_package,
    looks_like_lottie_sticker_attachment,
)
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.mentions import (
    GroupParticipant,
    MentionCandidate,
    MentionReference,
    active_mention_query,
    matching_mention_candidates,
    mention_references_in_text,
)
from cliente_xmpp.models.names import (
    display_label_from_jid,
    is_fallback_chat_name,
    normalize_chat_name,
)
from cliente_xmpp.notifications.windows import WindowsNotificationService
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.chat_list_panel import ChatListItem, ChatListPanel
from cliente_xmpp.ui.connection_header_panel import ConnectionHeaderPanel
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.events import EVT_XMPP_EVENT, WxXmppEvent
from cliente_xmpp.ui.login_panel import LoginData, LoginPanel
from cliente_xmpp.ui.settings_panel import SettingsPanel
from cliente_xmpp.ui.theme import apply_theme
from cliente_xmpp.ui.whatsapp_link_panel import (
    WhatsAppLinkPanel,
    WhatsAppPairingCodeDialog,
    WhatsAppQrDialog,
)
from cliente_xmpp.xmpp.client import XmppService
from cliente_xmpp.xmpp.events import (
    ChatActivityLoaded,
    ChatActivityLoadFinished,
    ChatDisplayedSynced,
    ChatsDiscovered,
    ChatStateUpdated,
    ContactAvatarReceived,
    ContactAvatarUnavailable,
    ContactPresenceUpdated,
    GroupParticipantsLoaded,
    GroupParticipantUpdated,
    MessageDeliveryUpdated,
    MessageHistoryLoaded,
    MessageReceived,
    RosterLoaded,
    WhatsAppBridgeStatus,
    WhatsAppLinkSessionEnded,
    WhatsAppLinkSessionStarted,
    WhatsAppPairingCodeReceived,
    WhatsAppQrImageDataReceived,
    WhatsAppQrImageReceived,
    XmppConnected,
    XmppDisconnected,
    XmppError,
    XmppEvent,
)

HISTORY_PAGE_SIZE = 20
MARK_ALL_READ_DELAY_MS = 750
MARK_ALL_READ_HISTORY_TIMEOUT_MS = 8000
PRELOAD_CHAT_LIMIT = 20
BACKGROUND_SYNC_DELAY_MS = 350
MESSAGE_DUPLICATE_WINDOW_SECONDS = 3
OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS = 120
GROUP_SELF_ECHO_WINDOW_SECONDS = 10
CLIPBOARD_ATTACHMENTS_DIR = APP_DIR / "clipboard"
CONTACT_AVATARS_DIR = APP_DIR / "avatars"
SEARCH_RESULT_LIMIT = 200
INITIAL_CHAT_LOAD_FALLBACK_MS = 8000
SEARCH_DEBOUNCE_MS = 250
WHATSAPP_QR_TIMEOUT_SECONDS = 60
MESSAGE_EDIT_WINDOW = timedelta(minutes=15)
APP_WINDOW_TITLE = "whatsapp-CAN"
PERF_DEBUG_PREFIX = "[cliente-xmpp][perf]"
PERF_DEBUG_ENABLED = os.environ.get("CLIENTE_XMPP_PERF_DEBUG", "").casefold() in {
    "1",
    "true",
    "yes",
}


@dataclass(frozen=True, slots=True)
class ClipboardAttachment:
    paths: list[Path] | None = None
    source_label: str = "archivo"
    message: str = ""


class MainWindow(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title=APP_WINDOW_TITLE, size=(980, 700))

        self.settings_store = SettingsStore()
        self.credential_store = CredentialStore()
        self.connection_settings = self.settings_store.load_connection()
        self.speaker = NvdaSpeaker()
        self.new_message_sound = NewMessageSound()
        self.open_chat_message_sound = OpenChatMessageSound()
        self.sent_message_sound = SentMessageSound()
        (
            self.open_chat_message_sound_enabled,
            self.sent_message_sound_enabled,
        ) = self.settings_store.load_notification_sound_settings()
        desktop_notifications = self.settings_store.load_desktop_notification_settings()
        self.windows_notifications_enabled = desktop_notifications.enabled
        self.windows_notification_previews_enabled = desktop_notifications.show_preview
        self.windows_notification_nvda_announcements_enabled = (
            desktop_notifications.announce_with_nvda
        )
        self.message_store = MessageStore()
        self.storage_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cliente-xmpp-storage",
        )
        self.audio_metadata_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cliente-xmpp-audio-metadata",
        )
        self.xmpp = XmppService(self._post_xmpp_event)
        self.messages_by_chat: dict[str, list[Message]] = {}
        self.delivery_states_by_message: dict[tuple[str, str], str] = {}
        self.displayed_marker_ids_by_chat: dict[str, str] = {}
        self.synced_displayed_marker_ids_by_chat: dict[str, str] = {}
        self.mark_all_read_queue: deque[Chat] = deque()
        self.mark_all_read_waiting_chat_jid = ""
        self.latest_message_timestamps_by_chat: dict[str, float] = {}
        self.history_loaded_chats: set[str] = set()
        self.history_exhausted_chats: set[str] = set()
        self.history_loading_chats: set[str] = set()
        self.background_history_queue: deque[str] = deque()
        self.background_history_queued_chats: set[str] = set()
        self.background_history_loading_chat = ""
        self.preloaded_history_chats: set[str] = set()
        self.cached_message_loads: set[tuple[str, str]] = set()
        self.audio_metadata_in_progress: set[str] = set()
        self.auto_downloading_media_keys: set[tuple[str, str]] = set()
        self.chat_names_by_jid: dict[str, str] = {}
        self.group_participants_by_chat: dict[str, dict[str, GroupParticipant]] = {}
        self.mention_candidates: list[MentionCandidate] = []
        self.contact_presence_by_chat: dict[str, ContactPresenceUpdated] = {}
        self.contact_avatar_paths_by_chat: dict[str, Path] = {}
        self.contact_avatar_requests_in_progress: set[str] = set()
        self.chat_state_by_chat: dict[str, str] = {}
        self.searchable_chats_by_jid: dict[str, Chat] = {}
        self.roster_jids: set[str] = set()
        self.loading_initial_chat_activity = False
        self.pending_chat_activity: dict[str, ChatActivityLoaded] = {}
        self.loaded_chat_summaries = 0
        self.search_debounce_timer: wx.CallLater | None = None
        self.search_request_id = 0
        self.reply_context: Message | None = None
        self.edit_context: Message | None = None
        self.current_jid = ""
        self.whatsapp_component_jid = ""
        self.whatsapp_link_status = "unknown"
        self.whatsapp_link_detail = ""
        self.whatsapp_verified = False
        self.pending_roster_chats: list[Chat] | None = None
        self.whatsapp_link_session: tuple[str, str, str] | None = None
        self.whatsapp_qr_dialog: WhatsAppQrDialog | None = None
        self.whatsapp_qr_path = ""
        self.whatsapp_qr_deadline = 0.0
        self.whatsapp_qr_request_in_flight = False
        self.whatsapp_qr_restart_after_cancel = False
        self.whatsapp_qr_downloads_in_progress: set[str] = set()
        self.contact_info_dialog: ContactInfoDialog | None = None
        self.audio_recorder = MciAudioRecorder()
        self.settings_return_to_conversation = False
        self.windows_notification_service = WindowsNotificationService(
            on_open_chat=self._open_chat_from_windows_notification,
            on_mark_read=self._mark_chat_read_from_windows_notification,
        )

        self.startup_panel: wx.Panel
        self.startup_message: wx.TextCtrl
        self.login_panel = LoginPanel(self, self.connection_settings)
        self.workspace_panel: wx.Panel
        self.content_panel: wx.Panel
        self.content_box: wx.BoxSizer
        self.connection_header: ConnectionHeaderPanel
        self.whatsapp_link_panel: WhatsAppLinkPanel
        self.chat_list: ChatListPanel
        self.conversation: ConversationPanel
        self.settings_panel: SettingsPanel
        self.status_bar = self.CreateStatusBar()

        self._layout()
        apply_theme(self)
        self._bind_events()
        self._load_saved_password()
        if self._can_auto_connect():
            self._set_startup_wait_ui()
            self.status_bar.SetStatusText("Conectando automáticamente...")
            wx.CallAfter(self.speaker.speak, "Bienvenido a WhatsApp CAN. Espera por favor...")
        else:
            self._set_connected_ui(False)
            self.status_bar.SetStatusText("Desconectado")
        self._schedule_auto_connect()

    def _layout(self) -> None:
        self.startup_panel = wx.Panel(self)
        startup_box = wx.BoxSizer(wx.VERTICAL)
        self.startup_message = wx.TextCtrl(
            self.startup_panel,
            value="Bienvenido a WhatsApp CAN.\nEspera por favor...",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_NONE,
        )
        self.startup_message.SetToolTip("Conectando automáticamente.")
        startup_box.AddStretchSpacer(1)
        startup_box.Add(self.startup_message, 0, wx.ALL | wx.EXPAND, 32)
        startup_box.AddStretchSpacer(1)
        self.startup_panel.SetSizer(startup_box)

        self.workspace_panel = wx.Panel(self)
        workspace_box = wx.BoxSizer(wx.VERTICAL)

        self.connection_header = ConnectionHeaderPanel(self.workspace_panel)
        self.whatsapp_link_panel = WhatsAppLinkPanel(self.workspace_panel)

        self.content_panel = wx.Panel(self.workspace_panel)
        self.content_box = wx.BoxSizer(wx.VERTICAL)
        self.chat_list = ChatListPanel(self.content_panel)
        self.conversation = ConversationPanel(
            self.content_panel,
            self._display_name_for_jid,
            initial_audio_speed=self.settings_store.load_audio_speed(),
            on_audio_speed_changed=self._save_audio_speed,
            on_audio_download_requested=self._request_audio_download_for_playback,
        )
        self.settings_panel = SettingsPanel(self.content_panel)
        self.content_box.Add(self.chat_list, 1, wx.EXPAND)
        self.content_box.Add(self.conversation, 1, wx.EXPAND)
        self.content_box.Add(self.settings_panel, 1, wx.EXPAND)
        self.content_panel.SetSizer(self.content_box)
        self.conversation.Hide()
        self.settings_panel.Hide()

        workspace_box.Add(self.connection_header, 0, wx.EXPAND)
        workspace_box.Add(self.whatsapp_link_panel, 0, wx.EXPAND)
        workspace_box.Add(self.content_panel, 1, wx.EXPAND)
        self.workspace_panel.SetSizer(workspace_box)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.startup_panel, 1, wx.EXPAND)
        box.Add(self.login_panel, 0, wx.EXPAND)
        box.Add(self.workspace_panel, 1, wx.EXPAND)
        self.SetSizer(box)

    def _bind_events(self) -> None:
        self.login_panel.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)
        self.connection_header.disconnect_button.Bind(wx.EVT_BUTTON, self._on_disconnect)
        self.connection_header.mark_all_read_button.Bind(
            wx.EVT_BUTTON,
            self._on_mark_all_chats_read,
        )
        self.connection_header.settings_button.Bind(wx.EVT_BUTTON, self._on_open_settings)
        self.whatsapp_link_panel.open_button.Bind(wx.EVT_BUTTON, self._on_open_whatsapp_link)
        self.whatsapp_link_panel.cancel_button.Bind(
            wx.EVT_BUTTON,
            self._on_cancel_whatsapp_link,
        )
        self.chat_list.list_box.Bind(wx.EVT_LISTBOX, self._on_chat_selected)
        self.chat_list.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self._on_open_selected_chat)
        self.chat_list.list_box.Bind(wx.EVT_KEY_DOWN, self._on_chat_list_key_down)
        self.chat_list.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_text_changed)
        self.chat_list.search_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_search_key_down)
        self.conversation.load_older_button.Bind(wx.EVT_BUTTON, self._on_load_older_messages)
        self.conversation.back_button.Bind(wx.EVT_BUTTON, self._on_back_to_chat_list)
        self.conversation.contact_info_button.Bind(wx.EVT_BUTTON, self._on_contact_info)
        self.conversation.send_button.Bind(wx.EVT_BUTTON, self._on_primary_send_action)
        self.conversation.attach_button.Bind(wx.EVT_BUTTON, self._on_attach_file)
        self.conversation.sticker_button.Bind(wx.EVT_BUTTON, self._on_send_sticker)
        self.conversation.pause_recording_button.Bind(wx.EVT_BUTTON, self._on_pause_recording)
        self.conversation.cancel_recording_button.Bind(wx.EVT_BUTTON, self._on_cancel_recording)
        self.settings_panel.back_button.Bind(wx.EVT_BUTTON, self._on_close_settings)
        self.settings_panel.test_notification_button.Bind(
            wx.EVT_BUTTON,
            self._on_test_windows_notification,
        )
        for checkbox in (
            self.settings_panel.windows_notifications,
            self.settings_panel.show_preview,
            self.settings_panel.announce_with_nvda,
            self.settings_panel.open_chat_sound,
            self.settings_panel.sent_message_sound,
        ):
            checkbox.Bind(wx.EVT_CHECKBOX, self._on_settings_changed)
        self.conversation.compose.Bind(wx.EVT_TEXT, self._on_composer_text_changed)
        self.conversation.compose.Bind(wx.EVT_KEY_DOWN, self._on_composer_key_down)
        text_paste_event = getattr(wx, "EVT_TEXT_PASTE", None)
        if text_paste_event is not None:
            self.conversation.compose.Bind(text_paste_event, self._on_composer_paste)
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
        self.whatsapp_verified = False
        self.pending_roster_chats = None
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

    def _save_notification_sound_settings(self) -> None:
        try:
            self.settings_store.save_notification_sound_settings(
                open_chat_message_enabled=self.open_chat_message_sound_enabled,
                sent_message_enabled=self.sent_message_sound_enabled,
            )
        except Exception:
            return

    def _save_desktop_notification_settings(self) -> None:
        settings = DesktopNotificationSettings(
            enabled=self.windows_notifications_enabled,
            show_preview=self.windows_notification_previews_enabled,
            announce_with_nvda=self.windows_notification_nvda_announcements_enabled,
        )
        try:
            self.settings_store.save_desktop_notification_settings(settings)
        except Exception:
            return

    def _sync_settings_panel(self) -> None:
        self.settings_panel.set_values(
            windows_notifications=self.windows_notifications_enabled,
            show_preview=self.windows_notification_previews_enabled,
            announce_with_nvda=self.windows_notification_nvda_announcements_enabled,
            open_chat_sound=self.open_chat_message_sound_enabled,
            sent_message_sound=self.sent_message_sound_enabled,
        )

    def _on_settings_changed(self, event: wx.CommandEvent) -> None:
        self.windows_notifications_enabled = self.settings_panel.windows_notifications.GetValue()
        self.windows_notification_previews_enabled = self.settings_panel.show_preview.GetValue()
        self.windows_notification_nvda_announcements_enabled = (
            self.settings_panel.announce_with_nvda.GetValue()
        )
        self.open_chat_message_sound_enabled = self.settings_panel.open_chat_sound.GetValue()
        self.sent_message_sound_enabled = self.settings_panel.sent_message_sound.GetValue()
        self.settings_panel.apply_interactive_state()
        self._save_desktop_notification_settings()
        self._save_notification_sound_settings()
        changed_control = event.GetEventObject()
        announcement = self.settings_panel.checkbox_state_text(changed_control)
        self.status_bar.SetStatusText(announcement)
        self.speaker.speak(announcement)

    def _on_test_windows_notification(self, _event: wx.CommandEvent) -> None:
        shown = self.windows_notification_service.show_message(
            title="WhatsApp CAN",
            message="Las notificaciones nativas de Windows están funcionando.",
            chat_jid="",
        )
        if shown:
            if not self.windows_notification_service.native_toasts_enabled:
                self.status_bar.SetStatusText(
                    "Notificación enviada en modo de compatibilidad de Windows"
                )
                return
            self.status_bar.SetStatusText("Notificación de prueba enviada")
            return
        self.status_bar.SetStatusText("Windows no pudo mostrar la notificación de prueba")
        self.speaker.speak("Windows no pudo mostrar la notificación de prueba")

    def _schedule_auto_connect(self) -> None:
        if not self._auto_connect_enabled():
            return

        if not self._can_auto_connect():
            self.status_bar.SetStatusText(
                "No hay contraseña guardada para conectar automáticamente"
            )
            return

        wx.CallAfter(self._on_connect, wx.CommandEvent())

    def _auto_connect_enabled(self) -> bool:
        return (
            self.connection_settings.auto_connect
            and self.connection_settings.remember_password
        )

    def _can_auto_connect(self) -> bool:
        if not self._auto_connect_enabled():
            return False

        login = self.login_panel.get_login_data()
        return bool(login.settings.jid and login.password)

    @staticmethod
    def _debug_perf(label: str, started_at: float, **details: object) -> None:
        if not PERF_DEBUG_ENABLED:
            return

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        rendered_details = " ".join(
            f"{key}={value}" for key, value in details.items() if value is not None
        )
        suffix = f" {rendered_details}" if rendered_details else ""
        print(f"{PERF_DEBUG_PREFIX} {label} {elapsed_ms:.1f}ms{suffix}", flush=True)

    def _on_disconnect(self, _event: wx.CommandEvent) -> None:
        self.connection_header.set_status("Desconectando...")
        self.status_bar.SetStatusText("Desconectando...")
        self.xmpp.disconnect()

    def _handle_whatsapp_bridge_status(
        self,
        status: str,
        component_jid: str,
        detail: str,
    ) -> None:
        if component_jid:
            self.whatsapp_component_jid = component_jid
        self.whatsapp_link_status = status
        self.whatsapp_link_detail = detail

        if status in {"connected", "paired"}:
            started_at = time.perf_counter()
            self.whatsapp_verified = True
            self.whatsapp_link_session = None
            self.whatsapp_qr_request_in_flight = False
            self.whatsapp_qr_restart_after_cancel = False
            self.whatsapp_qr_deadline = 0.0
            self.whatsapp_link_panel.clear()
            self._close_whatsapp_qr_dialog()
            message = "WhatsApp vinculado" if status == "paired" else "WhatsApp conectado"
            self.connection_header.set_status(message)
            self.status_bar.SetStatusText(message)
            self._apply_pending_roster_if_ready()
            self.workspace_panel.Layout()
            self._debug_perf(
                "_handle_whatsapp_bridge_status.connected",
                started_at,
                status=status,
                has_pending_roster=self.pending_roster_chats is not None,
            )
            return

        if status == "needs_qr":
            self.whatsapp_verified = False
            self.whatsapp_qr_request_in_flight = True
            if self.whatsapp_qr_deadline <= time.monotonic():
                self.whatsapp_qr_deadline = (
                    time.monotonic() + WHATSAPP_QR_TIMEOUT_SECONDS
                )
            message = "WhatsApp esta preparando el QR."
        elif status in {"needs_pairing", "needs_relogin", "needs_pair_code", "logged_out"}:
            self.whatsapp_verified = False
            message = "WhatsApp todavia no esta vinculado."
        elif status == "needs_registration":
            self.whatsapp_verified = False
            message = "WhatsApp necesita registrarse en el bridge."
        elif status == "connection_error":
            self.whatsapp_verified = False
            message = "WhatsApp reporto un error de conexion."
        else:
            return

        if detail:
            message = f"{message} {detail}"
        if status == "needs_qr" and self.whatsapp_qr_dialog is not None:
            self.whatsapp_qr_dialog.set_pending(
                self.whatsapp_qr_deadline,
                can_cancel=self._has_cancelable_whatsapp_link(component_jid),
            )
        qr_finished = status in {"needs_pairing", "needs_relogin", "logged_out"} and (
            self.whatsapp_qr_request_in_flight or self.whatsapp_qr_deadline > 0
        )
        if qr_finished:
            self._mark_whatsapp_qr_expired(message)
        if not self.workspace_panel.IsShown():
            self.login_panel.set_connecting(False)
            self.connection_header.set_account(self.current_jid)
            self._set_connected_ui(True)
        self.whatsapp_link_panel.set_status(
            message,
            action_label=(
                "Generar nuevo QR"
                if qr_finished
                else self._whatsapp_link_action_label()
            ),
            can_cancel=self._has_cancelable_whatsapp_link(component_jid),
        )
        self._show_chat_placeholder("WhatsApp requiere vinculacion.")
        self.connection_header.set_status("WhatsApp requiere vinculacion")
        self.status_bar.SetStatusText(message)
        self.workspace_panel.Layout()
        wx.CallAfter(self.speaker.speak, message)

    def _handle_whatsapp_pairing_code(self, component_jid: str, code: str) -> None:
        self.whatsapp_component_jid = component_jid or self.whatsapp_component_jid
        self.status_bar.SetStatusText(f"Codigo de vinculacion: {code}")
        wx.CallAfter(self.speaker.speak, f"Codigo de vinculacion: {code}")
        dialog = WhatsAppPairingCodeDialog(self, code)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

    def _handle_whatsapp_link_session_started(
        self,
        component_jid: str,
        command_node: str,
        session_id: str,
    ) -> None:
        self.whatsapp_component_jid = component_jid or self.whatsapp_component_jid
        self.whatsapp_link_session = (component_jid, command_node, session_id)
        self.whatsapp_qr_request_in_flight = True
        self.whatsapp_link_panel.set_status(
            "Generando el QR de vinculacion de WhatsApp.",
            action_label="Ver estado",
            can_cancel=True,
        )
        if self.whatsapp_qr_dialog is not None:
            if self.whatsapp_qr_deadline <= time.monotonic():
                self.whatsapp_qr_deadline = (
                    time.monotonic() + WHATSAPP_QR_TIMEOUT_SECONDS
                )
            self.whatsapp_qr_dialog.set_pending(
                self.whatsapp_qr_deadline,
                can_cancel=True,
            )
        self.workspace_panel.Layout()

    def _handle_whatsapp_link_session_ended(
        self,
        component_jid: str,
        command_node: str,
        session_id: str,
        canceled: bool,
        detail: str,
    ) -> None:
        if self.whatsapp_link_session == (component_jid, command_node, session_id):
            self.whatsapp_link_session = None
        if self.whatsapp_qr_restart_after_cancel:
            self.whatsapp_qr_restart_after_cancel = False
            self.whatsapp_qr_request_in_flight = False
            self.whatsapp_qr_path = ""
            self.whatsapp_qr_deadline = 0.0
            wx.CallAfter(self._begin_whatsapp_qr_request)
            return

        self.whatsapp_qr_request_in_flight = False
        if canceled:
            self._close_whatsapp_qr_dialog()
            self.status_bar.SetStatusText(detail or "Vinculacion cancelada")
            wx.CallAfter(self.speaker.speak, "Vinculacion cancelada")
        elif detail and self.whatsapp_qr_dialog is not None:
            self.whatsapp_qr_dialog.set_error(
                f"La vinculacion no pudo continuar: {detail}",
                can_cancel=False,
            )
        if self.whatsapp_link_panel.IsShown():
            self.whatsapp_link_panel.set_status(
                detail or "WhatsApp todavia no esta vinculado.",
                action_label="Generar nuevo QR" if not canceled else "Generar QR",
                can_cancel=False,
            )
            self.workspace_panel.Layout()

    def _whatsapp_link_action_label(self) -> str:
        if self.whatsapp_qr_path:
            return (
                "Mostrar QR"
                if self.whatsapp_qr_deadline > time.monotonic()
                else "Generar nuevo QR"
            )
        if self.whatsapp_qr_request_in_flight or self.whatsapp_link_session is not None:
            return (
                "Ver estado"
                if self.whatsapp_qr_deadline > time.monotonic()
                else "Generar nuevo QR"
            )
        if self.whatsapp_qr_dialog is not None:
            return "Ver estado"
        return "Generar QR"

    def _refresh_whatsapp_link_panel_actions(self) -> None:
        if not self.whatsapp_link_panel.IsShown():
            return

        self.whatsapp_link_panel.set_status(
            self.whatsapp_link_panel.message.GetLabel(),
            action_label=self._whatsapp_link_action_label(),
            can_cancel=self._has_cancelable_whatsapp_link(self.whatsapp_component_jid),
        )
        if self.whatsapp_qr_dialog is not None:
            self.whatsapp_qr_dialog.set_can_cancel(
                self._has_cancelable_whatsapp_link(self.whatsapp_component_jid)
            )
        self.workspace_panel.Layout()

    def _has_cancelable_whatsapp_link(self, component_jid: str = "") -> bool:
        if self.whatsapp_link_session is None:
            return False
        if not component_jid:
            return True
        return self.whatsapp_link_session[0] == component_jid

    def _on_cancel_whatsapp_link(self, _event: wx.CommandEvent) -> None:
        self.whatsapp_qr_restart_after_cancel = False
        component_jid = (
            self.whatsapp_link_session[0]
            if self.whatsapp_link_session is not None
            else self.whatsapp_component_jid
        )
        if not component_jid:
            self.status_bar.SetStatusText("No hay vinculacion en curso para cancelar")
            return

        self.status_bar.SetStatusText("Cancelando vinculacion...")
        self.xmpp.cancel_whatsapp_linking(component_jid)

    def _show_whatsapp_qr_status(self) -> None:
        if self.whatsapp_qr_dialog is not None:
            self._focus_whatsapp_qr_dialog()
            return

        dialog = self._create_whatsapp_qr_dialog()
        can_cancel = self._has_cancelable_whatsapp_link(self.whatsapp_component_jid)
        if self.whatsapp_qr_path:
            if self.whatsapp_qr_deadline > time.monotonic():
                dialog.set_image(
                    self.whatsapp_qr_path,
                    self.whatsapp_qr_deadline,
                    can_cancel=can_cancel,
                )
            else:
                dialog.set_expired(
                    "El QR expiro. Genera uno nuevo para volver a intentarlo.",
                    can_cancel=can_cancel,
                )
        elif self.whatsapp_qr_request_in_flight:
            if self.whatsapp_qr_deadline > time.monotonic():
                dialog.set_pending(self.whatsapp_qr_deadline, can_cancel=can_cancel)
            else:
                self.whatsapp_qr_request_in_flight = False
                dialog.set_expired(
                    "La solicitud de QR expiro. Genera una nueva para continuar.",
                    can_cancel=can_cancel,
                )
        else:
            dialog.set_expired(
                "No hay un QR vigente. Genera uno nuevo para continuar.",
                can_cancel=can_cancel,
            )
        dialog.Show()
        self._focus_whatsapp_qr_dialog()

    def _handle_whatsapp_qr_image(
        self,
        component_jid: str,
        image_url: str,
        mime: str,
        filename: str,
    ) -> None:
        if self.whatsapp_verified:
            return
        if image_url in self.whatsapp_qr_downloads_in_progress:
            self._focus_whatsapp_qr_dialog()
            return

        self.whatsapp_component_jid = component_jid or self.whatsapp_component_jid
        self.whatsapp_qr_downloads_in_progress.add(image_url)
        self.status_bar.SetStatusText("Descargando QR de vinculacion...")
        message = Message(
            chat_jid=component_jid,
            sender_jid=component_jid,
            body="QR de vinculacion",
            media_url=image_url,
            media_kind="image",
            media_mime=mime,
            media_filename=filename or "qr-whatsapp.png",
        )

        def worker() -> None:
            try:
                downloaded = download_media(message, self.current_jid or "cuenta")
            except Exception as exc:
                wx.CallAfter(
                    self.status_bar.SetStatusText,
                    f"No se pudo descargar el QR: {exc}",
                )
                return
            finally:
                wx.CallAfter(self.whatsapp_qr_downloads_in_progress.discard, image_url)

            wx.CallAfter(self._show_whatsapp_qr, downloaded.path)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_whatsapp_qr_image_data(
        self,
        component_jid: str,
        image_data: bytes,
        mime: str,
        filename: str,
    ) -> None:
        if self.whatsapp_verified:
            return

        self.whatsapp_component_jid = component_jid or self.whatsapp_component_jid
        digest = hashlib.sha256(image_data).hexdigest()[:16]
        extension = self._image_extension_from_mime(mime)
        filename = Path(filename).name if filename else ""
        safe_filename = (
            filename if filename.endswith(extension) else f"qr-whatsapp-{digest}{extension}"
        )
        qr_dir = APP_DIR / "whatsapp-linking"
        qr_dir.mkdir(parents=True, exist_ok=True)
        path = qr_dir / safe_filename
        try:
            path.write_bytes(image_data)
        except OSError as exc:
            self.status_bar.SetStatusText(f"No se pudo guardar el QR: {exc}")
            return

        self._show_whatsapp_qr(path)

    @staticmethod
    def _image_extension_from_mime(mime: str) -> str:
        return {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(mime.casefold(), ".png")

    def _show_whatsapp_qr(self, path: Path) -> None:
        if self.whatsapp_verified:
            return

        path_text = str(path)
        if not wx.Image.CanRead(path_text):
            self.status_bar.SetStatusText("El QR recibido no es una imagen válida")
            return

        if self.whatsapp_qr_deadline <= 0:
            self.whatsapp_qr_deadline = time.monotonic() + WHATSAPP_QR_TIMEOUT_SECONDS
        if self.whatsapp_qr_deadline <= time.monotonic():
            self._mark_whatsapp_qr_expired(
                "El QR llego despues de expirar. Genera uno nuevo para continuar."
            )
            return

        self.status_bar.SetStatusText("QR de vinculacion listo")
        wx.CallAfter(self.speaker.speak, "QR de vinculacion listo")
        self.whatsapp_qr_path = path_text
        dialog = self.whatsapp_qr_dialog or self._create_whatsapp_qr_dialog()
        if not dialog.set_image(
            path_text,
            self.whatsapp_qr_deadline,
            can_cancel=self._has_cancelable_whatsapp_link(self.whatsapp_component_jid),
        ):
            self.status_bar.SetStatusText("El QR recibido no es una imagen valida")
            return
        if not dialog.IsShown():
            dialog.Show()
        self._refresh_whatsapp_link_panel_actions()
        self._focus_whatsapp_qr_dialog()

    def _create_whatsapp_qr_dialog(self) -> WhatsAppQrDialog:
        dialog = WhatsAppQrDialog(self, on_expired=self._on_whatsapp_qr_expired)
        self.whatsapp_qr_dialog = dialog
        dialog.Bind(wx.EVT_CLOSE, self._on_whatsapp_qr_dialog_close)
        dialog.retry_button.Bind(wx.EVT_BUTTON, self._on_retry_whatsapp_qr)
        dialog.cancel_link_button.Bind(wx.EVT_BUTTON, self._on_cancel_whatsapp_link)
        return dialog

    def _focus_whatsapp_qr_dialog(self) -> None:
        dialog = self.whatsapp_qr_dialog
        if dialog is None:
            return

        if dialog.IsIconized():
            dialog.Iconize(False)
        if not dialog.IsMaximized():
            dialog.Maximize(True)
        dialog.Raise()
        dialog.RequestUserAttention()
        dialog.close_button.SetFocus()

    def _close_whatsapp_qr_dialog(self) -> None:
        dialog = self.whatsapp_qr_dialog
        self.whatsapp_qr_dialog = None
        self.whatsapp_qr_path = ""
        self.whatsapp_qr_deadline = 0.0
        self.whatsapp_qr_downloads_in_progress.clear()
        if dialog is not None:
            dialog.Destroy()
        self._refresh_whatsapp_link_panel_actions()

    def _on_whatsapp_qr_dialog_close(self, event: wx.CloseEvent) -> None:
        dialog = self.whatsapp_qr_dialog
        self.whatsapp_qr_dialog = None
        if dialog is not None:
            dialog.Destroy()
        self._refresh_whatsapp_link_panel_actions()
        event.Skip(False)

    def _on_open_whatsapp_link(self, _event: wx.CommandEvent) -> None:
        if not self.whatsapp_component_jid:
            wx.MessageBox(
                "Todavia no detecte el componente de WhatsApp.",
                "Vincular WhatsApp",
            )
            return

        if (
            self.whatsapp_qr_dialog is not None
            or self.whatsapp_qr_path
            or self.whatsapp_qr_request_in_flight
        ):
            self._show_whatsapp_qr_status()
            return

        self._begin_whatsapp_qr_request()

    def _begin_whatsapp_qr_request(self) -> None:
        if not self.whatsapp_component_jid:
            return

        if self.whatsapp_link_session is not None:
            self.whatsapp_qr_restart_after_cancel = True
            self.status_bar.SetStatusText("Preparando una nueva vinculacion...")
            if self.whatsapp_qr_dialog is not None:
                self.whatsapp_qr_dialog.set_pending(
                    time.monotonic() + WHATSAPP_QR_TIMEOUT_SECONDS,
                    can_cancel=True,
                )
            self.xmpp.cancel_whatsapp_linking(self.whatsapp_link_session[0])
            return

        self.whatsapp_qr_path = ""
        self.whatsapp_qr_deadline = time.monotonic() + WHATSAPP_QR_TIMEOUT_SECONDS
        self.whatsapp_qr_request_in_flight = True
        dialog = self.whatsapp_qr_dialog or self._create_whatsapp_qr_dialog()
        dialog.set_pending(self.whatsapp_qr_deadline, can_cancel=False)
        if not dialog.IsShown():
            dialog.Show()
        self.whatsapp_link_panel.set_status(
            "Solicitando un QR a WhatsApp.",
            action_label="Ver estado",
            can_cancel=False,
        )
        self.workspace_panel.Layout()
        self.status_bar.SetStatusText("Solicitando QR de vinculacion...")
        wx.CallAfter(self.speaker.speak, "Solicitando QR de vinculacion")
        self.xmpp.request_whatsapp_relogin(self.whatsapp_component_jid)
        self._focus_whatsapp_qr_dialog()

    def _on_retry_whatsapp_qr(self, _event: wx.CommandEvent) -> None:
        self._begin_whatsapp_qr_request()

    def _on_whatsapp_qr_expired(self) -> None:
        message = "El QR expiro. Genera uno nuevo para volver a intentarlo."
        self._mark_whatsapp_qr_expired(message)
        wx.CallAfter(self.speaker.speak, message)

    def _mark_whatsapp_qr_expired(self, message: str) -> None:
        self.whatsapp_qr_request_in_flight = False
        self.whatsapp_qr_path = ""
        self.whatsapp_qr_deadline = 0.0
        can_cancel = self._has_cancelable_whatsapp_link(self.whatsapp_component_jid)
        if self.whatsapp_qr_dialog is not None:
            self.whatsapp_qr_dialog.set_expired(message, can_cancel=can_cancel)
        if self.whatsapp_link_panel.IsShown():
            self.whatsapp_link_panel.set_status(
                message,
                action_label="Generar nuevo QR",
                can_cancel=can_cancel,
            )
            self.workspace_panel.Layout()
        self.status_bar.SetStatusText(message)

    def _mark_whatsapp_qr_error(self, message: str) -> None:
        self.whatsapp_qr_request_in_flight = False
        self.whatsapp_qr_path = ""
        self.whatsapp_qr_deadline = 0.0
        can_cancel = self._has_cancelable_whatsapp_link(self.whatsapp_component_jid)
        if self.whatsapp_qr_dialog is not None:
            self.whatsapp_qr_dialog.set_error(message, can_cancel=can_cancel)
        if self.whatsapp_link_panel.IsShown():
            self.whatsapp_link_panel.set_status(
                message,
                action_label="Generar nuevo QR",
                can_cancel=can_cancel,
            )
            self.workspace_panel.Layout()

    def _apply_pending_roster_if_ready(self) -> None:
        started_at = time.perf_counter()
        if not self.whatsapp_verified:
            return
        if self.pending_roster_chats is None:
            self._show_chat_placeholder("Cargando chats...")
            self._debug_perf("_apply_pending_roster_if_ready.waiting", started_at)
            return

        self._apply_roster_chats(self.pending_roster_chats)
        self._debug_perf(
            "_apply_pending_roster_if_ready.applied",
            started_at,
            chats=len(self.pending_roster_chats),
        )

    def _apply_roster_chats(self, chats: list[Chat]) -> None:
        started_at = time.perf_counter()
        load_started_at = time.perf_counter()
        cached_chats = self._load_cached_chats()
        self._debug_perf(
            "_apply_roster_chats.load_cached_chats",
            load_started_at,
            cached=len(cached_chats),
            roster=len(chats),
        )
        merge_started_at = time.perf_counter()
        self._set_searchable_chats(self._merge_chat_lists(chats, cached_chats))
        self._debug_perf(
            "_apply_roster_chats.merge_searchable",
            merge_started_at,
            searchable=len(self.searchable_chats_by_jid),
        )
        self.xmpp.monitor_group_chats([chat.jid for chat in cached_chats if chat.is_group])
        self.loading_initial_chat_activity = True
        self.pending_chat_activity = {}
        visible_cached_chats = self._chats_with_activity(cached_chats)
        self.loaded_chat_summaries = len(visible_cached_chats)
        render_started_at = time.perf_counter()
        self.chat_list.set_chats(self._sort_chats_by_recency(visible_cached_chats))
        self._debug_perf(
            "_apply_roster_chats.render_local_list",
            render_started_at,
            cached=len(cached_chats),
            visible=len(visible_cached_chats),
        )
        if not self.chat_list.selected_chat():
            self.chat_list.select_first()
        self.chat_list.focus()
        self.status_bar.SetStatusText(
            f"{self.loaded_chat_summaries} chats locales. Cargando actualizaciones..."
        )
        self.xmpp.load_recent_activity(self.roster_jids)
        wx.CallLater(
            INITIAL_CHAT_LOAD_FALLBACK_MS,
            self._finish_initial_chat_loading_if_needed,
        )
        self._debug_perf(
            "_apply_roster_chats.total",
            started_at,
            cached=len(cached_chats),
            roster=len(chats),
        )

    def _show_chat_placeholder(self, text: str) -> None:
        self.chat_list.set_placeholder(text)
        if self.conversation.IsShown():
            self._show_chat_list()

    def _on_chat_selected(self, _event: wx.CommandEvent) -> None:
        if not self.chat_list.IsShown():
            return

        if self.chat_list.is_updating:
            return

        if self.chat_list.is_searching:
            return

        self.chat_list.selected_chat()

    def _on_open_selected_chat(self, _event: wx.Event) -> None:
        self._show_selected_chat()

    def _on_chat_list_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._clear_chat_search(focus_list=True)
            return

        if event.GetKeyCode() == wx.WXK_F2:
            self._rename_selected_chat()
            return

        if event.ControlDown() and event.GetKeyCode() == ord("M"):
            self._toggle_selected_chat_mute()
            return

        event.Skip()

    def _on_search_text_changed(self, event: wx.CommandEvent) -> None:
        self._schedule_chat_search()
        event.Skip()

    def _on_search_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._cancel_scheduled_chat_search()
            self._apply_chat_search()
            self._show_selected_chat()
            return

        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._cancel_scheduled_chat_search()
            self._clear_chat_search(focus_list=True)
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
            notification_settings_known=chat.notification_settings_known,
            group_member_count=chat.group_member_count,
            is_self_group=chat.is_self_group,
            unread_count=chat.unread_count,
            last_message_preview=chat.last_message_preview,
            last_message_at=chat.last_message_at,
        )
        self.chat_names_by_jid[chat.jid] = name
        self._upsert_searchable_chat(renamed_chat)
        self.chat_list.upsert_chat(renamed_chat)
        self._refresh_chat_order(selected_jid=chat.jid)
        if self.conversation.current_chat and self.conversation.current_chat.jid == chat.jid:
            self.conversation.set_chat(renamed_chat)
            self.conversation.set_messages(self.messages_by_chat.get(chat.jid, []))
            self._refresh_conversation_avatar(renamed_chat)
            self._sync_recording_ui()
        if self.current_jid:
            try:
                self.message_store.rename_chat(self.current_jid, chat.jid, name)
            except Exception:
                self.status_bar.SetStatusText("No se pudo guardar el nombre del contacto")
                return

        self.status_bar.SetStatusText(f"Contacto renombrado: {name}")
        self._apply_chat_search()

    def _toggle_selected_chat_mute(self) -> None:
        chat = self.chat_list.selected_chat()
        if not chat:
            self.status_bar.SetStatusText("Selecciona un chat para silenciarlo")
            return

        updated_chat = Chat(
            jid=chat.jid,
            name=chat.name,
            custom_name=chat.custom_name,
            is_group=chat.is_group,
            notifications_muted=not chat.notifications_muted,
            notification_settings_known=True,
            group_member_count=chat.group_member_count,
            is_self_group=chat.is_self_group,
            unread_count=chat.unread_count,
            last_message_preview=chat.last_message_preview,
            last_message_at=chat.last_message_at,
        )
        self._upsert_searchable_chat(updated_chat)
        self.chat_list.upsert_chat(updated_chat)
        self._refresh_chat_order(selected_jid=chat.jid)
        if self.current_jid:
            try:
                self.message_store.set_chat_notifications_muted(
                    self.current_jid,
                    chat.jid,
                    updated_chat.notifications_muted,
                )
            except Exception:
                self.status_bar.SetStatusText("No se pudo guardar el silencio del chat")
                return

        state = "silenciado" if updated_chat.notifications_muted else "con sonido"
        self.status_bar.SetStatusText(f"{updated_chat.name}: {state}")
        self._apply_chat_search()

    def _focus_chat_search(self) -> None:
        if self.conversation.IsShown():
            self._show_chat_list()
        self.chat_list.focus_search()
        self.status_bar.SetStatusText("Buscar chats y mensajes")

    def _clear_chat_search(self, focus_list: bool = False, selected_jid: str = "") -> None:
        self.search_request_id += 1
        self._cancel_scheduled_chat_search()
        if self.chat_list.search_ctrl.GetValue():
            self.chat_list.search_ctrl.ChangeValue("")
        self.chat_list.clear_search_results(selected_jid=selected_jid)
        if focus_list:
            self.chat_list.focus()
        self.status_bar.SetStatusText("Lista de chats")

    def _schedule_chat_search(self) -> None:
        if self.search_debounce_timer is not None and self.search_debounce_timer.IsRunning():
            self.search_debounce_timer.Stop()

        self.search_debounce_timer = wx.CallLater(SEARCH_DEBOUNCE_MS, self._apply_chat_search)

    def _cancel_scheduled_chat_search(self) -> None:
        if self.search_debounce_timer is None:
            return

        if self.search_debounce_timer.IsRunning():
            self.search_debounce_timer.Stop()

    def _apply_chat_search(self) -> None:
        self.search_request_id += 1
        request_id = self.search_request_id
        query = self.chat_list.search_ctrl.GetValue().strip()
        if not query:
            selected_chat = self.chat_list.selected_chat()
            self.chat_list.clear_search_results(
                selected_jid=selected_chat.jid if selected_chat else ""
            )
            return

        terms = self._search_terms(query)
        if not terms:
            self.chat_list.clear_search_results()
            return

        chats_by_jid = self._searchable_chats_by_jid()
        contact_results = [
            ChatListItem(chat=chat)
            for chat in self._sort_chats_for_search(chats_by_jid.values(), terms)
        ][:SEARCH_RESULT_LIMIT]
        self.chat_list.set_search_results(contact_results)
        if contact_results and self.chat_list.list_box.GetSelection() == wx.NOT_FOUND:
            self.chat_list.select_first()

        remaining_message_results = max(0, SEARCH_RESULT_LIMIT - len(contact_results))
        if not remaining_message_results:
            self.status_bar.SetStatusText(f"{len(contact_results)} resultados")
            return

        messages_snapshot = {
            chat_jid: tuple(messages)
            for chat_jid, messages in self.messages_by_chat.items()
        }
        self.status_bar.SetStatusText(
            f"{len(contact_results)} chats; buscando mensajes..."
        )
        threading.Thread(
            target=self._run_message_search,
            args=(
                request_id,
                query,
                terms,
                chats_by_jid,
                messages_snapshot,
                self.current_jid,
                remaining_message_results,
                contact_results,
            ),
            daemon=True,
        ).start()

    def _run_message_search(
        self,
        request_id: int,
        query: str,
        terms: list[str],
        chats_by_jid: dict[str, Chat],
        messages_by_chat: dict[str, tuple[Message, ...]],
        account_jid: str,
        limit: int,
        contact_results: list[ChatListItem],
    ) -> None:
        message_results = self._message_search_results(
            terms,
            chats_by_jid,
            limit=limit,
            messages_by_chat=messages_by_chat,
            account_jid=account_jid,
        )
        wx.CallAfter(
            self._finish_message_search,
            request_id,
            query,
            contact_results,
            message_results,
        )

    def _finish_message_search(
        self,
        request_id: int,
        query: str,
        contact_results: list[ChatListItem],
        message_results: list[ChatListItem],
    ) -> None:
        if request_id != self.search_request_id:
            return
        if (
            not self._search_is_active()
            or self.chat_list.search_ctrl.GetValue().strip() != query
        ):
            return

        results = contact_results + message_results
        self.chat_list.set_search_results(results)
        if results and self.chat_list.list_box.GetSelection() == wx.NOT_FOUND:
            self.chat_list.select_first()
        self.status_bar.SetStatusText(f"{len(results)} resultados")

    def _message_search_results(
        self,
        terms: list[str],
        chats_by_jid: dict[str, Chat],
        limit: int = SEARCH_RESULT_LIMIT,
        messages_by_chat: dict[str, tuple[Message, ...]] | None = None,
        account_jid: str = "",
    ) -> list[ChatListItem]:
        started_at = time.perf_counter()
        if limit <= 0:
            return []

        memory_started_at = time.perf_counter()
        messages_by_key: dict[tuple[object, ...], Message] = {}
        message_sources = (
            messages_by_chat
            if messages_by_chat is not None
            else {
                chat_jid: tuple(messages)
                for chat_jid, messages in self.messages_by_chat.items()
            }
        )
        for messages in message_sources.values():
            for message in messages:
                chat = chats_by_jid.get(message.chat_jid)
                if self._message_matches_search(message, terms, chat):
                    messages_by_key[self._message_search_key(message)] = message
        self._debug_perf(
            "_message_search_results.memory",
            memory_started_at,
            matches=len(messages_by_key),
        )

        account_jid = account_jid or self.current_jid
        if account_jid:
            try:
                sqlite_started_at = time.perf_counter()
                cached_messages = self.message_store.search_messages(
                    account_jid,
                    " ".join(terms),
                    limit=limit,
                )
                self._debug_perf(
                    "_message_search_results.sqlite",
                    sqlite_started_at,
                    cached=len(cached_messages),
                    limit=limit,
                )
            except Exception:
                cached_messages = []
            for message in cached_messages:
                chat = chats_by_jid.get(message.chat_jid)
                if self._message_matches_search(message, terms, chat):
                    messages_by_key.setdefault(self._message_search_key(message), message)

        messages = sorted(
            messages_by_key.values(),
            key=self._message_timestamp,
            reverse=True,
        )[:limit]
        results: list[ChatListItem] = []
        for message in messages:
            chat = chats_by_jid.get(message.chat_jid) or Chat(
                jid=message.chat_jid,
                name=self._fallback_display_name_for_jid(message.chat_jid),
                is_group=message.chat_is_group
                or MainWindow._jid_may_be_group_chat(message.chat_jid),
                last_message_preview=(
                    media_description(message) if has_media(message) else message.body
                ),
                last_message_at=message.sent_at,
            )
            results.append(ChatListItem(chat=chat, message=message))
        self._debug_perf(
            "_message_search_results.total",
            started_at,
            results=len(results),
            terms=len(terms),
        )
        return results

    def _message_search_key(self, message: Message) -> tuple[object, ...]:
        return message.chat_jid, *self._message_merge_key(message)

    def _chat_for_message(self, message: Message) -> Chat:
        name = self._display_name_for_jid(message.chat_jid)
        return Chat(
            jid=message.chat_jid,
            name=name,
            is_group=message.chat_is_group or self._message_jid_may_be_group_chat(message.chat_jid),
            notifications_muted=self._chat_notifications_muted(message.chat_jid),
            notification_settings_known=self._chat_notification_settings_known(message.chat_jid),
            last_message_preview=media_description(message) if has_media(message) else message.body,
            last_message_at=message.sent_at,
        )

    def _set_searchable_chats(self, chats: list[Chat]) -> None:
        self.searchable_chats_by_jid = {chat.jid: chat for chat in chats}

    def _searchable_chats_by_jid(self) -> dict[str, Chat]:
        chats = dict(self.searchable_chats_by_jid)
        for visible_chat in self.chat_list.chats():
            known_chat = chats.get(visible_chat.jid)
            if known_chat is None:
                chats[visible_chat.jid] = self._chat_with_search_name(visible_chat)
                continue

            chats[visible_chat.jid] = self._chat_with_search_name(
                known_chat,
                visible_chat=visible_chat,
            )
        return chats

    def _chat_with_search_name(
        self,
        chat: Chat,
        visible_chat: Chat | None = None,
    ) -> Chat:
        visible_chat = visible_chat or chat
        custom_name = visible_chat.custom_name or chat.custom_name
        known_name = self.chat_names_by_jid.get(chat.jid, "").strip()
        if custom_name:
            name = custom_name
        elif known_name and known_name != chat.jid:
            name = known_name
        elif not self._is_fallback_chat_name(chat.name, chat.jid):
            name = chat.name
        else:
            name = visible_chat.name or chat.name

        return replace(
            chat,
            name=name,
            custom_name=custom_name,
            last_message_preview=(
                visible_chat.last_message_preview or chat.last_message_preview
            ),
            last_message_at=visible_chat.last_message_at or chat.last_message_at,
        )

    def _upsert_searchable_chat(self, chat: Chat) -> None:
        self.searchable_chats_by_jid[chat.jid] = chat

    def _chat_matches_search(self, chat: Chat, terms: list[str]) -> bool:
        return self._chat_search_rank(chat, terms) is not None

    def _sort_chats_for_search(
        self,
        chats: Iterable[Chat],
        terms: list[str],
    ) -> list[Chat]:
        matching_chats = [
            chat for chat in chats if self._chat_search_rank(chat, terms) is not None
        ]
        return sorted(
            matching_chats,
            key=lambda chat: (
                self._chat_search_rank(chat, terms),
                *self._chat_recency_key(chat),
            ),
        )

    @staticmethod
    def _chat_search_rank(chat: Chat, terms: list[str]) -> int | None:
        """Rank a chat name ahead of identifiers and latest-message previews."""
        query = " ".join(terms)
        name_fields = [
            MainWindow._normalize_search_text(chat.name),
            MainWindow._normalize_search_text(chat.custom_name),
            MainWindow._normalize_search_text(
                MainWindow._fallback_display_name_for_jid(chat.jid)
            ),
        ]
        name_fields = [field for field in name_fields if field]

        if any(field == query for field in name_fields):
            return 0
        if any(field.startswith(query) for field in name_fields):
            return 1
        if any(
            query in field or all(term in field for term in terms)
            for field in name_fields
        ):
            return 2

        auxiliary_text = MainWindow._normalize_search_text(
            " ".join((chat.jid, chat.last_message_preview))
        )
        digits = MainWindow._digits_only(chat.jid)
        if all(term in auxiliary_text or term in digits for term in terms):
            return 3
        return None

    def _message_matches_search(
        self,
        message: Message,
        terms: list[str],
        chat: Chat | None = None,
    ) -> bool:
        haystack = self._normalize_search_text(
            " ".join(
                (
                    chat.name if chat else "",
                    chat.custom_name if chat else "",
                    message.body,
                    message.reply_quote,
                    message.media_filename,
                    message.sender_name,
                    message.sender_jid,
                    message.chat_jid,
                )
            )
        )
        digits = self._digits_only(f"{message.sender_jid} {message.chat_jid}")
        return all(term in haystack or term in digits for term in terms)

    @classmethod
    def _search_terms(cls, query: str) -> list[str]:
        return [
            term
            for term in cls._normalize_search_text(query).split()
            if term
        ]

    @staticmethod
    def _normalize_search_text(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.casefold())
        return "".join(
            character for character in decomposed if not unicodedata.combining(character)
        )

    @staticmethod
    def _digits_only(text: str) -> str:
        return "".join(character for character in text if character.isdigit())

    def _search_is_active(self) -> bool:
        return bool(self.chat_list.search_ctrl.GetValue().strip())

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        key_code = event.GetKeyCode()
        sound_shortcut = self._notification_sound_shortcut(event)
        if sound_shortcut == "open_chat_message":
            self._toggle_open_chat_message_sound()
            return
        if sound_shortcut == "sent_message":
            self._toggle_sent_message_sound()
            return

        if self._is_find_shortcut(event):
            self._focus_chat_search()
            return

        if self._is_mark_all_chats_read_shortcut(event):
            self._mark_all_chats_read()
            return

        if key_code == wx.WXK_F5:
            self._refresh_current_view()
            return

        if key_code == wx.WXK_ESCAPE and self.settings_panel.IsShown():
            self._close_settings()
            return

        if key_code == wx.WXK_RETURN and self.chat_list.IsShown():
            self._show_selected_chat()
            return

        if key_code == wx.WXK_ESCAPE and self.conversation.IsShown():
            if self.audio_recorder.is_recording:
                self.audio_recorder.cancel()
                self.conversation.set_recording_state(False)
                self.status_bar.SetStatusText("Grabación cancelada")
                return
            if self.reply_context:
                self._cancel_reply()
                return
            if self.edit_context:
                self._cancel_editing()
                return
            selected_jid = self._show_chat_list()
            self._clear_chat_search(focus_list=True, selected_jid=selected_jid)
            return

        if key_code == wx.WXK_ESCAPE and self.chat_list.IsShown():
            self._clear_chat_search(focus_list=True)
            return

        event.Skip()

    def _on_back_to_chat_list(self, _event: wx.CommandEvent) -> None:
        self._show_chat_list()

    def _on_open_settings(self, _event: wx.CommandEvent) -> None:
        self._show_settings()

    def _on_close_settings(self, _event: wx.CommandEvent) -> None:
        self._close_settings()

    def _show_settings(self) -> None:
        self.settings_return_to_conversation = bool(
            self.conversation.IsShown() and self.conversation.current_chat
        )
        self._sync_settings_panel()
        self.chat_list.Hide()
        self.conversation.Hide()
        self.settings_panel.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self.settings_panel.focus()
        self.status_bar.SetStatusText("Configuración")

    def _close_settings(self) -> None:
        if not self.settings_panel.IsShown():
            return
        self.settings_panel.Hide()
        if self.settings_return_to_conversation and self.conversation.current_chat:
            self.conversation.Show()
            self.conversation.focus_composer()
            self._refresh_current_chat_status_title()
        else:
            self.chat_list.Show()
            self.chat_list.refresh_visible_if_stale()
            self.chat_list.focus()
            self.status_bar.SetStatusText("Lista de chats")
        self.settings_return_to_conversation = False
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()

    def _on_mark_all_chats_read(self, _event: wx.CommandEvent) -> None:
        self._mark_all_chats_read()

    def _mark_all_chats_read(self) -> None:
        chats_by_jid = self._searchable_chats_by_jid()
        unread_chats = [chat for chat in chats_by_jid.values() if chat.unread_count > 0]
        if not unread_chats:
            self.status_bar.SetStatusText("No hay chats no leídos")
            return

        updated_by_jid = {
            chat_jid: replace(chat, unread_count=0)
            for chat_jid, chat in chats_by_jid.items()
        }
        visible_chats = [
            updated_by_jid.get(chat.jid, chat)
            for chat in self.chat_list.chats()
        ]
        selected_chat = self.chat_list.selected_chat()
        selected_jid = selected_chat.jid if selected_chat else ""

        self._set_searchable_chats(list(updated_by_jid.values()))
        self._persist_chats(list(updated_by_jid.values()))
        self.chat_list.set_chats(visible_chats, selected_jid=selected_jid)
        if self.chat_list.is_searching:
            self._apply_chat_search()
        else:
            self.chat_list.force_refresh_visible(selected_jid)

        self.mark_all_read_queue = deque(unread_chats)
        self.mark_all_read_waiting_chat_jid = ""
        self._process_next_mark_all_read_chat()
        count = len(unread_chats)
        message = f"Marcando {count} chats como leídos en segundo plano"
        self.status_bar.SetStatusText(message)
        self.speaker.speak(message)

    def _process_next_mark_all_read_chat(self) -> None:
        if self.mark_all_read_waiting_chat_jid:
            return
        if not self.mark_all_read_queue:
            self.status_bar.SetStatusText("Todos los chats fueron marcados como leídos")
            return

        chat = self.mark_all_read_queue.popleft()
        self.mark_all_read_waiting_chat_jid = chat.jid
        self.xmpp.load_history(chat.jid, limit=1, background=True)
        wx.CallLater(
            MARK_ALL_READ_HISTORY_TIMEOUT_MS,
            self._finish_mark_all_read_chat,
            chat.jid,
        )

    def _finish_mark_all_read_chat(self, chat_jid: str) -> None:
        if self.mark_all_read_waiting_chat_jid != chat_jid:
            return

        self.mark_all_read_waiting_chat_jid = ""
        chat = self._chat_by_jid(chat_jid)
        if chat is not None:
            self._mark_chat_displayed(chat)
        wx.CallLater(MARK_ALL_READ_DELAY_MS, self._process_next_mark_all_read_chat)

    def _refresh_current_view(self) -> None:
        if self.conversation.IsShown() and self.conversation.current_chat:
            self._refresh_current_chat()
            return

        if self.chat_list.IsShown():
            self._refresh_chat_list_messages()

    def _refresh_current_chat(self) -> None:
        chat = self.conversation.current_chat
        if chat is None:
            return

        self.status_bar.SetStatusText(f"Actualizando {chat.name}...")
        self._request_history_page(chat.jid)

    def _refresh_chat_list_messages(self) -> None:
        self.status_bar.SetStatusText("Actualizando chats...")
        self.xmpp.load_inbox()
        self.xmpp.load_recent_activity(self.roster_jids)
        chat_jids = [
            chat.jid
            for chat in self._sort_chats_by_recency(self.chat_list.chats())[:PRELOAD_CHAT_LIMIT]
        ]
        if chat_jids:
            self.xmpp.preload_histories(chat_jids, limit=HISTORY_PAGE_SIZE)

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

        if self.edit_context is not None:
            self._send_message_correction(self.edit_context, body)
            return

        message_id = f"cliente-xmpp-{uuid.uuid4().hex}"
        mentions = self._mention_references_for_message(chat, body)
        message = Message(
            chat_jid=chat.jid,
            sender_jid="me",
            sender_name="Tú",
            body=body,
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            chat_is_group=chat.is_group,
            message_id=message_id,
            reply_quote=self.reply_context.body if self.reply_context else "",
            reply_to_jid=(
                self.current_jid if self.reply_context and self.reply_context.outgoing
                else self.reply_context.sender_jid if self.reply_context else ""
            ),
            reply_to_id=self.reply_context.message_id if self.reply_context else "",
            delivery_state="pending",
        )
        self._add_pending_outgoing_message(message)
        self._mark_current_chat_displayed(chat.jid)
        self.status_bar.SetStatusText("Enviando mensaje...")
        if self.reply_context:
            reply_to_jid = (
                self.current_jid if self.reply_context.outgoing else self.reply_context.sender_jid
            )
            self.xmpp.send_reply(
                chat.jid,
                body,
                reply_to_jid,
                self.reply_context.message_id,
                fallback_end=0,
                is_group=chat.is_group,
                message_id=message_id,
                mentions=mentions,
            )
            self.reply_context = None
            self.conversation.clear_reply_quote()
        else:
            self.xmpp.send_message(
                chat.jid,
                body,
                is_group=chat.is_group,
                message_id=message_id,
                mentions=mentions,
            )

    def _on_composer_text_changed(self, event: wx.CommandEvent) -> None:
        self.conversation.update_send_button_state(
            self.audio_recorder.is_recording,
            self.audio_recorder.is_paused,
        )
        self._refresh_mention_suggestions()
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

    def _on_send_sticker(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        dialog = wx.FileDialog(
            self,
            "Selecciona una imagen para enviar como sticker",
            wildcard=(
                "Imágenes (*.webp;*.png;*.jpg;*.jpeg)|*.webp;*.png;*.jpg;*.jpeg|"
                "Todos los archivos (*.*)|*.*"
            ),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = Path(dialog.GetPath())
        finally:
            dialog.Destroy()

        self.status_bar.SetStatusText(f"Subiendo sticker: {path.name}")
        self.xmpp.send_file(chat.jid, str(path), is_group=chat.is_group, as_sticker=True)
        self._mark_current_chat_displayed(chat.jid)

    def _attach_clipboard_files(self, report_empty: bool = False) -> bool:
        result = self._clipboard_attachment_paths()
        if not result.paths:
            if result.message or report_empty:
                self._set_clipboard_status(
                    result.message
                    or "El portapapeles no contiene archivos ni una imagen adjuntable",
                )
            return False

        chat = self.conversation.current_chat
        if not chat:
            self._set_clipboard_status("Selecciona un chat para adjuntar archivos")
            return True

        self._send_files_to_chat(chat, result.paths, source_label=result.source_label)
        return True

    def _send_files_to_chat(
        self,
        chat: Chat,
        paths: list[Path],
        source_label: str = "archivo",
    ) -> None:
        files = [path for path in paths if path.is_file()]
        if not files:
            self._set_clipboard_status("No se pudo adjuntar: no hay archivos válidos")
            return

        if len(files) == 1:
            self._set_clipboard_status(f"Subiendo {source_label}: {files[0].name}")
        else:
            self._set_clipboard_status(f"Subiendo {len(files)} archivos...")

        for path in files:
            self.xmpp.send_file(chat.jid, str(path), is_group=chat.is_group)
        self._mark_current_chat_displayed(chat.jid)

    def _set_clipboard_status(self, message: str) -> None:
        self.status_bar.SetStatusText(message)
        self.speaker.speak(message)

    @classmethod
    def _clipboard_attachment_paths(cls) -> ClipboardAttachment:
        if not wx.TheClipboard.Open():
            return ClipboardAttachment(message="No se pudo abrir el portapapeles")

        try:
            paths = cls._clipboard_file_paths()
            if paths:
                return ClipboardAttachment(paths=paths, source_label="archivo")

            image_path, message = cls._clipboard_bitmap_path()
            if image_path is not None:
                return ClipboardAttachment(paths=[image_path], source_label="imagen")
            if message:
                return ClipboardAttachment(message=message)
        finally:
            wx.TheClipboard.Close()

        return ClipboardAttachment()

    @staticmethod
    def _clipboard_file_paths() -> list[Path]:
        if not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_FILENAME)):
            return []

        data = wx.FileDataObject()
        if not wx.TheClipboard.GetData(data):
            return []

        return [Path(filename) for filename in data.GetFilenames()]

    @classmethod
    def _clipboard_bitmap_path(cls) -> tuple[Path | None, str]:
        if not wx.TheClipboard.IsSupported(wx.DataFormat(wx.DF_BITMAP)):
            return None, ""

        data = wx.BitmapDataObject()
        if not wx.TheClipboard.GetData(data):
            return None, "No se pudo leer la imagen copiada"

        bitmap = data.GetBitmap()
        if not bitmap.IsOk():
            return None, "La imagen copiada no es válida"

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = CLIPBOARD_ATTACHMENTS_DIR / f"imagen-portapapeles-{timestamp}.png"
        try:
            CLIPBOARD_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
            image = bitmap.ConvertToImage()
            if not image.IsOk() or not image.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
                return None, "No se pudo preparar la imagen copiada"
        except OSError as exc:
            return None, f"No se pudo guardar la imagen copiada: {exc}"

        return path, ""

    def _start_recording(self) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        try:
            self.audio_recorder.start()
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabación")
            return

        self.xmpp.send_chat_state(chat.jid, "composing", is_group=chat.is_group, media="audio")
        self.conversation.set_recording_state(True)
        self.status_bar.SetStatusText("Grabando audio...")
        self.speaker.speak("Grabando audio")

    def _on_pause_recording(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if not self.audio_recorder.is_recording or not chat:
            return

        try:
            if self.audio_recorder.is_paused:
                self.audio_recorder.resume()
                self.xmpp.send_chat_state(
                    chat.jid,
                    "composing",
                    is_group=chat.is_group,
                    media="audio",
                )
                self.status_bar.SetStatusText("Grabando audio...")
                self.speaker.speak("Grabando")
            else:
                self.audio_recorder.pause()
                self.xmpp.send_chat_state(chat.jid, "paused", is_group=chat.is_group)
                self.status_bar.SetStatusText("Grabación pausada")
                self.speaker.speak("Pausado")
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabación")
            return

        self.conversation.set_recording_state(True, self.audio_recorder.is_paused)

    def _on_cancel_recording(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        self.audio_recorder.cancel()
        if chat:
            self.xmpp.send_chat_state(chat.jid, "paused", is_group=chat.is_group)
        self.conversation.set_recording_state(False)
        self.status_bar.SetStatusText("Grabación cancelada")
        self.speaker.speak("Cancelado")

    def _stop_recording_and_send(self) -> None:
        chat = self.conversation.current_chat
        if not chat:
            return

        try:
            path = self.audio_recorder.stop_and_save()
        except AudioRecordingError as exc:
            wx.MessageBox(str(exc), "Grabación")
            self.conversation.set_recording_state(False)
            return

        view_once = self.conversation.view_once_audio.GetValue()
        self.conversation.set_recording_state(False)
        self.xmpp.send_chat_state(chat.jid, "paused", is_group=chat.is_group)
        self.status_bar.SetStatusText("Subiendo audio...")
        self.xmpp.send_file(chat.jid, str(path), is_group=chat.is_group, view_once=view_once)
        self._mark_current_chat_displayed(chat.jid)

    def _on_composer_paste(self, event: wx.CommandEvent) -> None:
        if self._attach_clipboard_files():
            return

        event.Skip()

    def _on_composer_key_down(self, event: wx.KeyEvent) -> None:
        if self._is_paste_shortcut(event):
            if self._attach_clipboard_files(report_empty=True):
                return
            event.Skip()
            return

        if self._is_edit_last_message_shortcut(event) and self._edit_last_message():
            return

        if self._handle_mention_suggestion_key(event):
            return

        if self._is_enter_without_shift(event):
            if self.audio_recorder.is_recording or self.conversation.has_composed_text():
                self._on_primary_send_action(wx.CommandEvent())
            else:
                self.status_bar.SetStatusText("Escribe un mensaje o usa el boton Grabar audio")
            return

        event.Skip()

    def _handle_mention_suggestion_key(self, event: wx.KeyEvent) -> bool:
        if not self.conversation.has_mention_suggestions():
            return False

        key_code = event.GetKeyCode()
        if key_code == wx.WXK_ESCAPE:
            self.conversation.hide_mention_suggestions()
            self.mention_candidates = []
            return True
        if key_code in (wx.WXK_UP, wx.WXK_DOWN):
            offset = -1 if key_code == wx.WXK_UP else 1
            index = self.conversation.move_mention_suggestion(offset)
            if 0 <= index < len(self.mention_candidates):
                self.speaker.speak(self.mention_candidates[index].label)
            return True
        if key_code == wx.WXK_TAB or self._is_enter_without_shift(event):
            return self._accept_selected_mention()
        return False

    def _accept_selected_mention(self) -> bool:
        index = self.conversation.selected_mention_suggestion_index()
        if index < 0 or index >= len(self.mention_candidates):
            return False

        mention_query = active_mention_query(
            self.conversation.compose.GetValue(),
            self.conversation.compose.GetInsertionPoint(),
        )
        if mention_query is None:
            self.conversation.hide_mention_suggestions()
            return False

        start, end, _query = mention_query
        candidate = self.mention_candidates[index]
        text = self.conversation.compose.GetValue()
        suffix = "" if end < len(text) and text[end].isspace() else " "
        self.conversation.replace_mention_query(start, end, candidate.mention_text + suffix)
        self.status_bar.SetStatusText(f"Mención seleccionada: {candidate.display_name}")
        self.speaker.speak(f"Mención: {candidate.display_name}")
        self.mention_candidates = []
        return True

    def _refresh_mention_suggestions(self) -> None:
        chat = self.conversation.current_chat
        if chat is None or not chat.is_group or self.audio_recorder.is_recording:
            self.conversation.hide_mention_suggestions()
            self.mention_candidates = []
            return

        mention_query = active_mention_query(
            self.conversation.compose.GetValue(),
            self.conversation.compose.GetInsertionPoint(),
        )
        if mention_query is None:
            self.conversation.hide_mention_suggestions()
            self.mention_candidates = []
            return

        _start, _end, query = mention_query
        self.mention_candidates = matching_mention_candidates(
            self._mention_candidates_for_chat(chat.jid),
            query,
        )
        self.conversation.show_mention_suggestions(
            [candidate.label for candidate in self.mention_candidates]
        )

    def _mention_candidates_for_chat(self, group_jid: str) -> list[MentionCandidate]:
        participants = self.group_participants_by_chat.get(group_jid, {})
        candidates: list[MentionCandidate] = []
        for participant in participants.values():
            if participant.jid.split("/", 1)[0] == self.current_jid.split("/", 1)[0]:
                continue
            display_name = self._display_name_for_jid(participant.jid)
            if display_name == self._fallback_display_name_for_jid(participant.jid):
                display_name = participant.nick
            candidates.append(
                MentionCandidate(
                    participant_jid=participant.jid,
                    display_name=display_name,
                    mention_text=participant.nick,
                )
            )
        return candidates

    def _mention_references_for_message(self, chat: Chat, body: str) -> list[MentionReference]:
        if not chat.is_group:
            return []
        return mention_references_in_text(body, self._mention_candidates_for_chat(chat.jid))

    @staticmethod
    def _is_paste_shortcut(event: wx.KeyEvent) -> bool:
        if not event.ControlDown() or event.AltDown():
            return False

        key_code = event.GetKeyCode()
        unicode_key = event.GetUnicodeKey()
        return key_code in (ord("V"), ord("v")) or unicode_key in (ord("V"), ord("v"))

    @staticmethod
    def _is_find_shortcut(event: wx.KeyEvent) -> bool:
        if not event.ControlDown() or event.AltDown():
            return False

        key_code = event.GetKeyCode()
        unicode_key = event.GetUnicodeKey()
        return key_code in (ord("F"), ord("f")) or unicode_key in (ord("F"), ord("f"))

    @staticmethod
    def _is_mark_all_chats_read_shortcut(event: wx.KeyEvent) -> bool:
        if not event.AltDown() or event.ControlDown() or event.ShiftDown():
            return False

        return event.GetKeyCode() in (ord("M"), ord("m"))

    @staticmethod
    def _notification_sound_shortcut(event: wx.KeyEvent) -> str | None:
        if event.GetKeyCode() != wx.WXK_F8 or event.ControlDown() or event.AltDown():
            return None

        return "sent_message" if event.ShiftDown() else "open_chat_message"

    def _toggle_open_chat_message_sound(self) -> None:
        self.open_chat_message_sound_enabled = not self.open_chat_message_sound_enabled
        self._save_notification_sound_settings()
        state = "activado" if self.open_chat_message_sound_enabled else "desactivado"
        message = f"Sonido de mensajes en el chat abierto {state}"
        self.status_bar.SetStatusText(message)
        self.speaker.speak(message)

    def _toggle_sent_message_sound(self) -> None:
        self.sent_message_sound_enabled = not self.sent_message_sound_enabled
        self._save_notification_sound_settings()
        state = "activado" if self.sent_message_sound_enabled else "desactivado"
        message = f"Sonido al enviar mensajes {state}"
        self.status_bar.SetStatusText(message)
        self.speaker.speak(message)

    @staticmethod
    def _is_enter_without_shift(event: wx.KeyEvent) -> bool:
        return (
            event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and not event.ShiftDown()
        )

    @staticmethod
    def _is_edit_last_message_shortcut(event: wx.KeyEvent) -> bool:
        return (
            event.ControlDown()
            and not event.AltDown()
            and event.GetKeyCode() == wx.WXK_UP
        )

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

        if event.GetKeyCode() == wx.WXK_DELETE and self._delete_selected_message():
            return

        if event.GetKeyCode() == wx.WXK_SPACE and self.conversation.play_selected_video():
            return

        if event.GetKeyCode() in (ord("L"), ord("l")) and self._open_selected_message_link():
            return

        if event.GetKeyCode() in (wx.WXK_SPACE, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            message = self.conversation.selected_message()
            if (
                message
                and has_media(message)
                and not is_link_preview(message)
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
        forward_item = menu.Append(wx.ID_ANY, "Reenviar...")
        forward_item.Enable(
            not message.retracted and bool(message.body or message.media_url or message.audio_url)
        )
        media_item: wx.MenuItem | None = None
        link_item: wx.MenuItem | None = None
        copy_file_item: wx.MenuItem | None = None
        describe_item: wx.MenuItem | None = None
        links = message_links(message)
        if links:
            link_item = menu.Append(wx.ID_ANY, "Abrir enlace")
        if has_media(message):
            media_label = "Abrir archivo" if local_media_path(message) else "Descargar archivo"
            if message.media_kind == "image":
                media_label = "Abrir foto" if local_media_path(message) else "Descargar foto"
            elif message.media_kind == "video":
                media_label = "Abrir video" if local_media_path(message) else "Descargar video"
            if message.is_sticker:
                media_label = "Abrir sticker" if local_media_path(message) else "Descargar sticker"
            if not is_link_preview(message):
                if message.media_kind != "audio":
                    media_item = menu.Append(wx.ID_ANY, media_label)
                copy_file_item = menu.Append(wx.ID_ANY, "Copiar archivo")
                copy_file_item.Enable(local_media_path(message) is not None)
                if message.media_kind in {"image", "video"} or message.is_sticker:
                    describe_item = menu.Append(wx.ID_ANY, "Describir con RayoAI")

        reaction_menu = wx.Menu()
        reaction_items: list[tuple[wx.MenuItem, str]] = []
        for reaction in ("👍", "❤️", "😂", "😮", "😢", "🙏"):
            reaction_items.append((reaction_menu.Append(wx.ID_ANY, reaction), reaction))
        menu.AppendSubMenu(reaction_menu, "Reaccionar")

        star_label = "No destacar" if message.starred else "Destacar"
        star_item = menu.Append(wx.ID_ANY, star_label)
        delete_item: wx.MenuItem | None = None
        edit_item: wx.MenuItem | None = None
        if self._message_can_be_edited(message):
            edit_item = menu.Append(wx.ID_ANY, "Editar mensaje")
        if message.outgoing:
            delete_item = menu.Append(wx.ID_ANY, "Eliminar mensaje")
            delete_item.Enable(self._message_can_be_deleted(message))

        self.Bind(wx.EVT_MENU, lambda _event: self._reply_to_message(message), reply_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._copy_message_text(message), copy_item)
        self.Bind(wx.EVT_MENU, lambda _event: self._forward_message(message), forward_item)
        if edit_item:
            self.Bind(wx.EVT_MENU, lambda _event: self._begin_editing(message), edit_item)
        if link_item:
            self.Bind(wx.EVT_MENU, lambda _event: self._open_message_link(message), link_item)
        if media_item:
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
        if delete_item:
            self.Bind(wx.EVT_MENU, lambda _event: self._delete_message(message), delete_item)

        self.PopupMenu(menu)
        menu.Destroy()

    def _reply_to_message(self, message: Message) -> None:
        self.reply_context = message
        self.conversation.insert_reply_quote(message)

    def _cancel_reply(self) -> None:
        self.reply_context = None
        self.conversation.clear_reply_quote()

    def _message_can_be_edited(self, message: Message) -> bool:
        age = datetime.now(message.sent_at.tzinfo) - message.sent_at
        return bool(
            message.outgoing
            and message.message_id
            and message.body
            and not message.media_url
            and not message.audio_url
            and not message.retracted
            and message.delivery_state not in {"pending", "failed"}
            and timedelta() <= age <= MESSAGE_EDIT_WINDOW
        )

    def _edit_last_message(self) -> bool:
        chat = self.conversation.current_chat
        if chat is None or self.conversation.has_composed_text():
            return False

        messages = self.messages_by_chat.get(chat.jid, [])
        if not messages or not self._message_can_be_edited(messages[-1]):
            return False

        self._begin_editing(messages[-1])
        return True

    def _begin_editing(self, message: Message) -> None:
        if not self._message_can_be_edited(message):
            self.status_bar.SetStatusText("Ese mensaje ya no se puede editar")
            return

        if self.reply_context:
            self._cancel_reply()
        self.edit_context = message
        self.conversation.begin_editing(message)
        self.status_bar.SetStatusText("Editando mensaje")

    def _cancel_editing(self) -> None:
        self.edit_context = None
        self.conversation.clear_editing()
        self.conversation.compose.Clear()
        self.status_bar.SetStatusText("Edición cancelada")

    def _send_message_correction(self, message: Message, body: str) -> None:
        if not self._message_can_be_edited(message):
            self._cancel_editing()
            self.status_bar.SetStatusText("Ese mensaje ya no se puede editar")
            return

        message.body = body
        message.edited = True
        self._persist_messages([message])
        self.conversation.refresh_message(message)
        self._update_chat_from_message(message)
        self._refresh_chat_order(message.chat_jid)
        self.xmpp.correct_message(
            message.chat_jid,
            body,
            message.message_id,
            is_group=message.chat_is_group,
            reply_to_jid=message.reply_to_jid,
            reply_to_id=message.reply_to_id,
        )
        self.edit_context = None
        self.conversation.clear_editing()
        self.status_bar.SetStatusText("Mensaje editado")

    def _delete_selected_message(self) -> bool:
        message = self.conversation.selected_message()
        if message is None:
            return False

        return self._delete_message(message)

    def _message_can_be_deleted(self, message: Message) -> bool:
        return bool(
            message.outgoing
            and message.message_id
            and message.delivery_state not in {"pending", "failed"}
            and not message.retracted
        )

    def _delete_message(self, message: Message) -> bool:
        if not self._message_can_be_deleted(message):
            self.status_bar.SetStatusText("Solo se pueden eliminar mensajes propios ya enviados")
            return False

        if message.chat_jid not in self.messages_by_chat:
            return False

        result = wx.MessageBox(
            "¿Eliminar este mensaje para todos?",
            "Eliminar mensaje",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self,
        )
        if result != wx.YES:
            return True

        self.xmpp.retract_message(
            message.chat_jid,
            message.message_id,
            is_group=message.chat_is_group,
        )
        message.retracted = True
        message.body = ""
        message.audio_url = ""
        message.media_url = ""
        message.media_kind = ""
        message.media_mime = ""
        message.media_filename = ""
        message.media_size = 0
        message.media_duration_seconds = 0
        message.reply_quote = ""
        self._persist_messages([message])
        self.conversation.refresh_message(message)
        self._update_chat_from_message(message)
        self._refresh_chat_order(message.chat_jid)
        self.status_bar.SetStatusText("Mensaje eliminado")
        return True

    def _copy_message_text(self, message: Message) -> None:
        if not wx.TheClipboard.Open():
            return

        try:
            wx.TheClipboard.SetData(wx.TextDataObject(message.body))
        finally:
            wx.TheClipboard.Close()

    def _forward_message(self, source: Message) -> None:
        if source.retracted or not (source.body or source.media_url or source.audio_url):
            self.status_bar.SetStatusText("Ese mensaje no se puede reenviar")
            return

        chats = self._sort_chats_by_recency(list(self.searchable_chats_by_jid.values()))
        if not chats:
            self.status_bar.SetStatusText("No hay chats disponibles para reenviar")
            return

        base_choices = [
            f"{chat.name or chat.jid}{' (grupo)' if chat.is_group else ''}" for chat in chats
        ]
        choice_counts = Counter(base_choices)
        choices = [
            f"{label} — {chat.jid}" if choice_counts[label] > 1 else label
            for chat, label in zip(chats, base_choices, strict=True)
        ]
        dialog = wx.SingleChoiceDialog(
            self,
            "Elige el chat de destino:",
            "Reenviar mensaje",
            choices,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            selection = dialog.GetSelection()
        finally:
            dialog.Destroy()
            wx.CallAfter(self.conversation.messages.SetFocus)

        if selection == wx.NOT_FOUND:
            return
        target = chats[selection]
        forward_source = source
        if is_link_preview(source):
            forward_source = replace(
                source,
                audio_url="",
                media_url="",
                media_kind="",
                media_mime="",
                media_filename="",
                media_size=0,
                media_duration_seconds=0,
                media_local_path="",
                is_sticker=False,
            )
        message_id = f"cliente-xmpp-{uuid.uuid4().hex}"
        forwarded = Message(
            chat_jid=target.jid,
            sender_jid="me",
            sender_name="Tú",
            body="Sticker" if forward_source.is_sticker else forward_source.body,
            sent_at=datetime.now().astimezone(),
            outgoing=True,
            audio_url=forward_source.audio_url,
            media_url=forward_source.media_url,
            media_kind=forward_source.media_kind,
            media_mime=forward_source.media_mime,
            media_filename=forward_source.media_filename,
            media_size=forward_source.media_size,
            media_duration_seconds=forward_source.media_duration_seconds,
            media_local_path=forward_source.media_local_path,
            is_sticker=forward_source.is_sticker,
            is_forwarded=True,
            message_id=message_id,
            chat_is_group=target.is_group,
            delivery_state="pending",
        )
        self._add_pending_outgoing_message(forwarded)
        self.xmpp.send_forward(
            target.jid,
            forward_source,
            is_group=target.is_group,
            message_id=message_id,
        )
        status = f"Reenviando a {target.name or target.jid}"
        self.status_bar.SetStatusText(status)
        self.speaker.speak(status)

    def _open_selected_message_link(self) -> bool:
        message = self.conversation.selected_message()
        if message is None:
            return False

        return self._open_message_link(message)

    def _open_message_link(self, message: Message) -> bool:
        links = message_links(message)
        if not links:
            self.status_bar.SetStatusText("El mensaje no contiene enlaces")
            return False

        link = links[0] if len(links) == 1 else self._choose_message_link(links)
        if link is None:
            return True

        if webbrowser.open(link.url):
            self.status_bar.SetStatusText(f"Abriendo enlace: {link.url}")
        else:
            self.status_bar.SetStatusText("No se pudo abrir el enlace")
        return True

    def _choose_message_link(self, links: list[MessageLink]) -> MessageLink | None:
        choices = [
            f"{link.title} | {link.url}" if link.title else link.url
            for link in links
        ]
        dialog = wx.SingleChoiceDialog(
            self,
            "Elige el enlace que quieres abrir:",
            "Abrir enlace",
            choices,
        )
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            selection = dialog.GetSelection()
        finally:
            dialog.Destroy()

        if selection == wx.NOT_FOUND:
            return None
        return links[selection]

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
                lottie_sticker_path = None
                if self._message_may_be_lottie_sticker(message):
                    lottie_sticker_path = convert_lottie_sticker_package(downloaded.path)
                    if lottie_sticker_path is not None:
                        downloaded = DownloadedMedia(
                            path=lottie_sticker_path,
                            size=lottie_sticker_path.stat().st_size,
                            mime="image/webp",
                            filename=lottie_sticker_path.name,
                        )
                duration = (
                    media_duration_seconds(downloaded.path)
                    if message.media_kind == "audio"
                    else 0.0
                )
            except Exception as exc:
                if not silent:
                    wx.CallAfter(
                        self.status_bar.SetStatusText,
                        f"No se pudo descargar el archivo: {exc}",
                    )
                if silent:
                    wx.CallAfter(self._discard_auto_media_download, message)
                return

            wx.CallAfter(
                self._finish_media_download,
                message,
                downloaded,
                send_to_rayoai,
                silent,
                duration,
                lottie_sticker_path is not None,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_media_download(
        self,
        message: Message,
        downloaded: DownloadedMedia,
        send_to_rayoai: bool,
        silent: bool = False,
        duration_seconds: float = 0.0,
        normalized_lottie_sticker: bool = False,
    ) -> None:
        message.media_local_path = str(downloaded.path)
        if normalized_lottie_sticker:
            message.is_sticker = True
        else:
            message.media_size = downloaded.size
            message.media_mime = downloaded.mime or message.media_mime
            message.media_filename = downloaded.filename or message.media_filename
        if message.media_kind == "audio" and duration_seconds > 0:
            message.media_duration_seconds = duration_seconds
        self._persist_message_media_path(message)
        self.conversation.refresh_message(message)
        self.conversation.audio_download_completed(message)
        self._update_chat_from_message(message)
        self._refresh_chat_order(message.chat_jid)
        if silent:
            self._discard_auto_media_download(message)
        else:
            self.status_bar.SetStatusText(f"Archivo descargado: {downloaded.path}")
        if send_to_rayoai:
            self._send_media_to_rayoai(message)

    def _discard_auto_media_download(self, message: Message) -> None:
        self.auto_downloading_media_keys.discard(self._auto_media_download_key(message))

    def _persist_message_media_path(self, message: Message) -> None:
        if not self.current_jid:
            return

        self._queue_storage_write(
            self.message_store.update_message_media_local_path,
            self.current_jid,
            replace(message),
        )

    def _auto_download_media_messages(self, messages: list[Message]) -> None:
        for message in messages:
            self._auto_download_media_message(message)

    def _auto_download_media_message(self, message: Message) -> None:
        may_be_lottie_sticker = self._message_may_be_lottie_sticker(message)
        if not (message.media_kind == "audio" or message.is_sticker or may_be_lottie_sticker):
            return
        if not (message.audio_url or message.media_url):
            return

        path = local_media_path(message)
        if path is not None:
            if may_be_lottie_sticker and not message.is_sticker:
                self._normalize_cached_lottie_sticker(message, path)
            return

        if message.outgoing and not may_be_lottie_sticker:
            return

        key = self._auto_media_download_key(message)
        if key in self.auto_downloading_media_keys:
            return

        self.auto_downloading_media_keys.add(key)
        self._download_media(message, silent=True)

    @staticmethod
    def _message_may_be_lottie_sticker(message: Message) -> bool:
        return looks_like_lottie_sticker_attachment(
            media_kind=message.media_kind,
            media_mime=message.media_mime,
            media_filename=message.media_filename,
            media_url=message.media_url,
            media_size=message.media_size,
        )

    def _normalize_cached_lottie_sticker(self, message: Message, source: Path) -> None:
        key = self._auto_media_download_key(message)
        if key in self.auto_downloading_media_keys:
            return
        self.auto_downloading_media_keys.add(key)

        def worker() -> None:
            destination = convert_lottie_sticker_package(source)
            wx.CallAfter(
                self._finish_cached_lottie_sticker_normalization,
                message,
                destination,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_cached_lottie_sticker_normalization(
        self,
        message: Message,
        destination: Path | None,
    ) -> None:
        self._discard_auto_media_download(message)
        if destination is None:
            return

        message.media_local_path = str(destination)
        message.is_sticker = True
        self._persist_message_media_path(message)
        self.conversation.refresh_message(message)
        self._update_chat_from_message(message)
        self._refresh_chat_order(message.chat_jid)

    def _request_audio_download_for_playback(self, message: Message) -> None:
        if local_media_path(message) is not None:
            self.conversation.audio_download_completed(message)
            return

        key = self._auto_media_download_key(message)
        if key not in self.auto_downloading_media_keys:
            self.auto_downloading_media_keys.add(key)
            self._download_media(message, silent=True)
        self.status_bar.SetStatusText("Descargando audio para reproducir...")

    @staticmethod
    def _auto_media_download_key(message: Message) -> tuple[str, str]:
        stable_id = message.message_id or message.audio_url or message.media_url
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

        self.status_bar.SetStatusText("Enviando a RayoAI...")

        def worker() -> None:
            sent = rayoai.send_open_path(path)
            wx.CallAfter(self._finish_send_media_to_rayoai, sent)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_send_media_to_rayoai(self, sent: bool) -> None:
        if sent:
            self.status_bar.SetStatusText("Imagen enviada a RayoAI para describir")
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
        self.windows_notification_service.close_all()
        self.conversation.close_audio()
        self.audio_recorder.cancel()
        self.xmpp.disconnect()
        storage_executor = getattr(self, "storage_executor", None)
        if storage_executor is not None:
            storage_executor.shutdown(wait=False, cancel_futures=False)
        audio_metadata_executor = getattr(self, "audio_metadata_executor", None)
        if audio_metadata_executor is not None:
            audio_metadata_executor.shutdown(wait=False, cancel_futures=True)
        event.Skip()

    def _post_xmpp_event(self, event: XmppEvent) -> None:
        wx.PostEvent(self, WxXmppEvent(event))

    def _handle_xmpp_event(self, event: XmppEvent) -> None:
        match event:
            case XmppConnected():
                self.login_panel.set_connecting(False)
                self.connection_header.set_account(self.current_jid)
                self.connection_header.set_status("Verificando WhatsApp")
                if not self.workspace_panel.IsShown():
                    self._set_connected_ui(True)
                self._show_chat_placeholder("Verificando conexion de WhatsApp...")
                self.status_bar.SetStatusText("Verificando conexion de WhatsApp...")
            case XmppDisconnected(reason=reason):
                self.login_panel.set_connecting(False)
                if reason:
                    self.connection_header.set_status(reason)
                    self.status_bar.SetStatusText(reason)
                else:
                    self.connection_header.set_status("Desconectado (Reconectando...)")
                    self.status_bar.SetStatusText("Desconectado")
            case XmppError(message=message):
                self.login_panel.set_connecting(False)
                if self.startup_panel.IsShown():
                    self._set_connected_ui(False)
                if self.whatsapp_qr_request_in_flight and "vincul" in message.casefold():
                    self._mark_whatsapp_qr_error(message)
                self.status_bar.SetStatusText(message)
                wx.MessageBox(message, "XMPP")
            case WhatsAppBridgeStatus(status=status, component_jid=component_jid, detail=detail):
                self._handle_whatsapp_bridge_status(status, component_jid, detail)
            case WhatsAppPairingCodeReceived(component_jid=component_jid, code=code):
                self._handle_whatsapp_pairing_code(component_jid, code)
            case WhatsAppLinkSessionStarted(
                component_jid=component_jid,
                command_node=command_node,
                session_id=session_id,
            ):
                self._handle_whatsapp_link_session_started(
                    component_jid,
                    command_node,
                    session_id,
                )
            case WhatsAppLinkSessionEnded(
                component_jid=component_jid,
                command_node=command_node,
                session_id=session_id,
                canceled=canceled,
                detail=detail,
            ):
                self._handle_whatsapp_link_session_ended(
                    component_jid,
                    command_node,
                    session_id,
                    canceled,
                    detail,
                )
            case WhatsAppQrImageReceived(
                component_jid=component_jid,
                image_url=image_url,
                mime=mime,
                filename=filename,
            ):
                self._handle_whatsapp_qr_image(component_jid, image_url, mime, filename)
            case WhatsAppQrImageDataReceived(
                component_jid=component_jid,
                image_data=image_data,
                mime=mime,
                filename=filename,
            ):
                self._handle_whatsapp_qr_image_data(component_jid, image_data, mime, filename)
            case RosterLoaded(chats=chats):
                self.pending_roster_chats = chats
                self.roster_jids = {chat.jid for chat in chats}
                self._update_chat_names(chats)
                if self.whatsapp_verified:
                    self._apply_roster_chats(chats)
                else:
                    self._show_chat_placeholder("Verificando conexion de WhatsApp...")
            case ChatsDiscovered(chats=chats):
                if not self.whatsapp_verified:
                    return
                self._upsert_discovered_chats(chats)
                self._preload_recent_histories()
            case GroupParticipantUpdated(participant=participant):
                self._remember_group_participant(participant)
            case GroupParticipantsLoaded(participants=participants):
                self._remember_group_participants(participants)
            case MessageReceived(message=message, notify=notify):
                message, added_message = self._store_message(message)
                if not message.outgoing:
                    self._set_chat_state(message.chat_jid, "")
                suppress_notification = self.loading_initial_chat_activity or not notify
                if not self.whatsapp_verified:
                    return
                if self.loading_initial_chat_activity:
                    pending_activity = self.pending_chat_activity.get(message.chat_jid)
                    self.pending_chat_activity[message.chat_jid] = ChatActivityLoaded(
                        chat_jid=message.chat_jid,
                        sent_at=message.sent_at,
                        preview=self._chat_preview_for_message(message),
                        unread_count=(
                            pending_activity.unread_count if pending_activity is not None else None
                        ),
                        is_group=message.chat_is_group,
                    )

                self._ensure_chat_for_message(message)
                current_chat_is_open = (
                    self.conversation.IsShown()
                    and self.conversation.current_chat
                    and self.conversation.current_chat.jid == message.chat_jid
                )
                self._update_chat_from_message(
                    message,
                    mark_unread=notify and not message.outgoing and not current_chat_is_open,
                )
                self._refresh_chat_order(preserve_focused_order=False)
                if self.chat_list.IsShown() and not self.chat_list.is_searching:
                    selected_chat = self.chat_list.selected_chat()
                    self.chat_list.force_refresh_visible(
                        selected_chat.jid if selected_chat else ""
                    )
                if current_chat_is_open:
                    if added_message:
                        self.conversation.append_message(message)
                    else:
                        self.conversation.refresh_message(message)
                self._auto_download_media_message(message)
                self._select_first_chat_if_needed()
                if added_message and not suppress_notification:
                    windows_notification_shown = self._show_windows_notification(
                        message,
                        current_chat_is_open=current_chat_is_open,
                    )
                    if (
                        not windows_notification_shown
                        or self.windows_notification_nvda_announcements_enabled
                    ):
                        self._speak_incoming_message(message)
                    self._play_incoming_message_sound(
                        message,
                        current_chat_is_open,
                        windows_notification_shown=windows_notification_shown,
                    )
            case MessageHistoryLoaded(
                chat_jid=chat_jid,
                messages=messages,
                older=older,
                complete=complete,
                background=background,
            ):
                if not self.whatsapp_verified:
                    return
                self._handle_message_history_loaded(chat_jid, messages, older, complete, background)
            case MessageDeliveryUpdated(
                chat_jid=chat_jid,
                message_id=message_id,
                delivery_state=delivery_state,
                detail=detail,
            ):
                self._handle_message_delivery_updated(
                    chat_jid,
                    message_id,
                    delivery_state,
                    detail,
                )
            case ChatDisplayedSynced(chat_jid=chat_jid, message_id=message_id):
                self._handle_synced_chat_displayed(chat_jid, message_id)
            case ContactPresenceUpdated(chat_jid=chat_jid):
                self.contact_presence_by_chat[chat_jid] = event
                self._refresh_current_chat_status_title()
            case ContactAvatarReceived(
                chat_jid=chat_jid,
                data=data,
                mime=mime,
                avatar_id=avatar_id,
            ):
                self._handle_contact_avatar_received(chat_jid, data, mime, avatar_id)
            case ContactAvatarUnavailable(chat_jid=chat_jid, detail=detail):
                self._handle_contact_avatar_unavailable(chat_jid, detail)
            case ChatStateUpdated(chat_jid=chat_jid, state=state, media=media):
                self._set_chat_state(chat_jid, state, media)
            case ChatActivityLoaded(
                chat_jid=chat_jid,
                sent_at=sent_at,
                preview=preview,
                unread_count=unread_count,
                is_group=is_group,
            ):
                if not self.whatsapp_verified:
                    return
                if sent_at or preview or unread_count is not None:
                    if self.loading_initial_chat_activity:
                        self.pending_chat_activity[chat_jid] = event
                        if sent_at:
                            self._update_chat_activity(chat_jid, sent_at.timestamp())
                        return

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
                    self._apply_synced_chat_displayed(chat_jid)
                    self._refresh_chat_order()
                    if added:
                        self.loaded_chat_summaries += 1
                    self._select_first_chat_if_needed()
                    self._preload_recent_histories()
                    self.status_bar.SetStatusText(
                        f"{self.loaded_chat_summaries} chats con mensajes cargados"
                    )
            case ChatActivityLoadFinished(loaded_count=loaded_count):
                if not self.whatsapp_verified:
                    return
                if self.loading_initial_chat_activity:
                    self._finish_initial_chat_loading(loaded_count)
                    return

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
        elif not background:
            self.history_loaded_chats.add(chat_jid)
        if complete and not empty_preview_chat:
            self.history_exhausted_chats.add(chat_jid)

        self._normalize_audio_metadata_for_messages(messages)
        self._merge_messages(chat_jid, messages)
        corrections = {message.replaces_id for message in messages if message.replaces_id}
        corrected_messages = [
            message
            for message in self.messages_by_chat.get(chat_jid, [])
            if message.message_id in corrections
        ]
        self._persist_messages(
            [message for message in messages if not message.replaces_id] + corrected_messages
        )
        activity_messages = [message for message in messages if not message.replaces_id]
        activity_messages.extend(corrected_messages)
        if activity_messages:
            self._update_chat_activity_from_messages(chat_jid, activity_messages)
            self._update_chat_preview_from_messages(chat_jid, activity_messages)
            self._auto_download_media_messages(activity_messages)
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
            self._mark_current_chat_displayed(chat_jid)

        if background:
            self._finish_mark_all_read_chat(chat_jid)
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
        indexes_by_content: dict[tuple[object, ...], list[int]] = {}
        unique_messages: list[Message] = []
        for message in sorted(merged, key=self._message_timestamp):
            if MainWindow._apply_message_correction(unique_messages, message):
                continue
            key = self._message_merge_key(message)
            existing_index = self._matching_group_self_echo_index(message, unique_messages)
            if existing_index is None:
                existing_index = indexes_by_key.get(key)
            if existing_index is None:
                existing_index = self._matching_content_message_index(
                    message,
                    indexes_by_content,
                    unique_messages,
                )
            if existing_index is not None:
                self._merge_message_metadata(unique_messages[existing_index], message)
                indexes_by_key[key] = existing_index
                continue

            indexes_by_key[key] = len(unique_messages)
            content_key = self._message_content_key(message)
            indexes_by_content.setdefault(content_key, []).append(len(unique_messages))
            unique_messages.append(message)

        delivery_states = getattr(self, "delivery_states_by_message", {})
        for message in unique_messages:
            known_state = delivery_states.get((chat_jid, message.message_id))
            if known_state:
                message.delivery_state = MainWindow._merge_delivery_state(
                    message.delivery_state,
                    known_state,
                )

        self.messages_by_chat[chat_jid] = unique_messages

    @staticmethod
    def _merge_delivery_state(current: str, incoming: str) -> str:
        if not incoming or current == incoming:
            return current or incoming
        if current == "failed":
            return current
        if incoming == "failed":
            return incoming

        rank = {
            "pending": 0,
            "sent": 1,
            "delivered": 2,
            "received": 2,
            "read": 3,
            "displayed": 3,
        }
        return incoming if rank.get(incoming, 0) >= rank.get(current, 0) else current

    @staticmethod
    def _apply_message_correction(messages: list[Message], correction: Message) -> bool:
        if not correction.replaces_id:
            return False

        for target in messages:
            if (
                target.message_id != correction.replaces_id
                or target.outgoing != correction.outgoing
            ):
                continue
            if target.retracted:
                return True

            target.body = correction.body
            if correction.reply_quote:
                target.reply_quote = correction.reply_quote
            target.reply_to_jid = correction.reply_to_jid or target.reply_to_jid
            target.reply_to_id = correction.reply_to_id or target.reply_to_id
            target.edited = True
            return True

        return False

    @classmethod
    def _matching_group_self_echo_index(
        cls,
        message: Message,
        candidates: list[Message],
    ) -> int | None:
        if not message.chat_is_group:
            return None

        message_timestamp = cls._message_timestamp(message)
        for index in range(len(candidates) - 1, -1, -1):
            candidate = candidates[index]
            candidate_age = message_timestamp - cls._message_timestamp(candidate)
            if candidate_age > GROUP_SELF_ECHO_WINDOW_SECONDS:
                break
            if cls._messages_are_group_self_echo(candidate, message):
                return index

        return None

    @classmethod
    def _messages_are_group_self_echo(cls, first: Message, second: Message) -> bool:
        outgoing, incoming = (first, second) if first.outgoing else (second, first)
        if not outgoing.outgoing or incoming.outgoing:
            return False
        if not outgoing.chat_is_group or not incoming.chat_is_group:
            return False
        if outgoing.chat_jid != incoming.chat_jid:
            return False
        if not incoming.message_id or not incoming.sender_jid.startswith(
            f"{incoming.chat_jid}/"
        ):
            return False
        if (
            outgoing.body != incoming.body
            or outgoing.audio_url != incoming.audio_url
            or outgoing.media_url != incoming.media_url
            or outgoing.media_kind != incoming.media_kind
            or outgoing.is_sticker != incoming.is_sticker
            or outgoing.is_forwarded != incoming.is_forwarded
        ):
            return False
        if not cls._messages_have_compatible_reply_quotes(outgoing, incoming):
            return False

        return (
            abs(cls._message_timestamp(outgoing) - cls._message_timestamp(incoming))
            <= GROUP_SELF_ECHO_WINDOW_SECONDS
        )

    @staticmethod
    def _message_merge_key(message: Message) -> tuple[object, ...]:
        if message.message_id:
            return "id", message.message_id

        return "payload", message.sent_at.isoformat(), *MainWindow._message_content_key(message)

    @staticmethod
    def _message_content_key(message: Message) -> tuple[object, ...]:
        return (
            "outgoing" if message.outgoing else message.sender_jid,
            message.body,
            message.outgoing,
            message.audio_url,
            message.media_url,
            message.media_kind,
            message.is_sticker,
            message.is_forwarded,
        )

    @classmethod
    def _matching_content_message_index(
        cls,
        message: Message,
        indexes_by_content: dict[tuple[object, ...], list[int]],
        unique_messages: list[Message],
    ) -> int | None:
        for index in indexes_by_content.get(cls._message_content_key(message), []):
            candidate = unique_messages[index]
            if cls._messages_are_distinct_local_outgoing(candidate, message):
                continue
            if not message.message_id and not candidate.message_id:
                continue
            if not cls._messages_have_compatible_reply_quotes(candidate, message):
                continue
            duplicate_window = cls._message_duplicate_window_seconds(candidate, message)
            if (
                abs(cls._message_timestamp(candidate) - cls._message_timestamp(message))
                <= duplicate_window
            ):
                return index

        return None

    @classmethod
    def _messages_are_distinct_local_outgoing(cls, first: Message, second: Message) -> bool:
        return (
            first.outgoing
            and second.outgoing
            and cls._message_has_local_pending_id(first)
            and cls._message_has_local_pending_id(second)
            and first.message_id != second.message_id
        )

    @staticmethod
    def _message_duplicate_window_seconds(first: Message, second: Message) -> int:
        if (
            first.outgoing
            and second.outgoing
            and (
                MainWindow._message_has_local_pending_id(first)
                or MainWindow._message_has_local_pending_id(second)
            )
        ):
            return OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS
        if first.message_id and second.message_id:
            return MESSAGE_DUPLICATE_WINDOW_SECONDS
        if first.outgoing and second.outgoing:
            return OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS

        return MESSAGE_DUPLICATE_WINDOW_SECONDS

    @staticmethod
    def _message_has_local_pending_id(message: Message) -> bool:
        return message.message_id.startswith("cliente-xmpp-")

    @staticmethod
    def _messages_have_compatible_reply_quotes(first: Message, second: Message) -> bool:
        return (
            not first.reply_quote
            or not second.reply_quote
            or first.reply_quote == second.reply_quote
        )

    @staticmethod
    def _merge_message_metadata(target: Message, incoming: Message) -> None:
        if incoming.retracted:
            target.retracted = True
            target.body = ""
            target.audio_url = ""
            target.media_url = ""
            target.media_kind = ""
            target.media_mime = ""
            target.media_filename = ""
            target.media_size = 0
            target.media_duration_seconds = 0
            target.reply_quote = ""
            return
        if target.retracted:
            return
        if incoming.message_id and (
            not target.message_id or MainWindow._message_has_local_pending_id(target)
        ):
            target.message_id = incoming.message_id
        if incoming.displayed_marker_id and not target.displayed_marker_id:
            target.displayed_marker_id = incoming.displayed_marker_id
        incoming_state = incoming.delivery_state or ("sent" if incoming.outgoing else "")
        target.delivery_state = MainWindow._merge_delivery_state(
            target.delivery_state,
            incoming_state,
        )
        if incoming.sender_name and not target.sender_name:
            target.sender_name = incoming.sender_name
        if incoming.reply_quote and not target.reply_quote:
            target.body = incoming.body
            target.reply_quote = incoming.reply_quote
        if incoming.reply_to_jid and not target.reply_to_jid:
            target.reply_to_jid = incoming.reply_to_jid
        if incoming.reply_to_id and not target.reply_to_id:
            target.reply_to_id = incoming.reply_to_id
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
        target.is_sticker = target.is_sticker or incoming.is_sticker
        target.is_forwarded = target.is_forwarded or incoming.is_forwarded
        target.chat_is_group = target.chat_is_group or incoming.chat_is_group

    def _request_full_history(self, chat_jid: str) -> None:
        if chat_jid in self.history_loading_chats:
            return

        self.history_loading_chats.add(chat_jid)
        self.history_exhausted_chats.discard(chat_jid)
        self.preloaded_history_chats.discard(chat_jid)
        self._refresh_load_older_button(chat_jid)
        self.xmpp.load_history(chat_jid, limit=50)

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
        chat = self.chat_list.chat_by_jid(chat_jid)
        return bool(chat and (chat.last_message_preview or chat.last_message_at))

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

    def _play_incoming_message_sound(
        self,
        message: Message,
        current_chat_is_open: bool,
        *,
        windows_notification_shown: bool = False,
    ) -> None:
        if message.outgoing or self._message_notifications_muted(message):
            return

        if windows_notification_shown:
            return

        if current_chat_is_open and self.IsActive():
            if getattr(self, "open_chat_message_sound_enabled", True):
                self.open_chat_message_sound.play()
            return

        self.new_message_sound.play()

    def _set_connected_ui(self, connected: bool) -> None:
        self.startup_panel.Show(False)
        self.login_panel.Show(not connected)
        self.workspace_panel.Show(connected)
        self.chat_list.Enable(connected)
        self.conversation.Enable(connected)
        self.Layout()

    def _set_startup_wait_ui(self) -> None:
        self.login_panel.Show(False)
        self.workspace_panel.Show(False)
        self.startup_panel.Show(True)
        self.Layout()

    def _store_message(self, message: Message) -> tuple[Message, bool]:
        message.chat_is_group = message.chat_is_group or self._message_jid_may_be_group_chat(
            message.chat_jid
        )
        self._normalize_audio_metadata_for_messages([message])
        existing_messages = self.messages_by_chat.get(message.chat_jid, [])
        existing_keys = {
            self._message_merge_key(existing)
            for existing in existing_messages
        }
        message_key = self._message_merge_key(message)
        existing_group_echo = next(
            (
                existing
                for existing in self.messages_by_chat.get(message.chat_jid, [])
                if self._messages_are_group_self_echo(existing, message)
            ),
            None,
        )
        self._merge_messages(message.chat_jid, [message])
        stored_message = (
            existing_group_echo
            or self._message_by_id(message.chat_jid, message.replaces_id)
            or self._message_by_merge_key(message.chat_jid, message_key)
            or message
        )
        self._remember_message_sender(stored_message)
        self._update_chat_activity(message.chat_jid, self._message_timestamp(message))
        self._persist_messages([stored_message])
        added_message = (
            existing_group_echo is None
            and not message.replaces_id
            and message_key not in existing_keys
            and all(existing is not stored_message for existing in existing_messages)
        )
        return stored_message, added_message

    def _message_by_id(self, chat_jid: str, message_id: str) -> Message | None:
        if not message_id:
            return None

        return next(
            (
                message
                for message in self.messages_by_chat.get(chat_jid, [])
                if message.message_id == message_id
            ),
            None,
        )

    def _add_pending_outgoing_message(self, message: Message) -> None:
        message.chat_is_group = message.chat_is_group or self._message_jid_may_be_group_chat(
            message.chat_jid
        )
        self._normalize_audio_metadata_for_messages([message])
        self._merge_messages(message.chat_jid, [message])
        stored_message = self._message_by_merge_key(
            message.chat_jid,
            self._message_merge_key(message),
        ) or message
        self._update_chat_activity(stored_message.chat_jid, self._message_timestamp(stored_message))
        self._ensure_chat_for_message(stored_message)
        self._update_chat_from_message(stored_message)
        current_chat_is_open = (
            self.conversation.IsShown()
            and self.conversation.current_chat
            and self.conversation.current_chat.jid == stored_message.chat_jid
        )
        if current_chat_is_open:
            self.conversation.append_message(stored_message)
        self._refresh_chat_order(stored_message.chat_jid)

    def _remember_message_sender(self, message: Message) -> None:
        if not message.chat_is_group or message.outgoing or not message.sender_name:
            return

        remember_participant = getattr(self, "_remember_group_participant", None)
        if callable(remember_participant):
            remember_participant(
                GroupParticipant(
                    group_jid=message.chat_jid,
                    jid=message.sender_jid,
                    nick=message.sender_name,
                )
            )

    def _load_cached_group_participants(self, chat: Chat) -> None:
        if not chat.is_group or not self.current_jid or chat.jid in self.group_participants_by_chat:
            return

        try:
            participants = self.message_store.load_group_participants(self.current_jid, chat.jid)
        except Exception:
            return

        self.group_participants_by_chat[chat.jid] = {
            participant.jid: participant for participant in participants
        }

    def _remember_group_participant(self, participant: GroupParticipant) -> None:
        self._remember_group_participants([participant])

    def _remember_group_participants(self, participants: list[GroupParticipant]) -> None:
        valid_participants = [
            participant
            for participant in participants
            if participant.group_jid and participant.jid and participant.nick
        ]
        if not valid_participants:
            return

        changed_participants: list[GroupParticipant] = []
        for participant in valid_participants:
            known_participants = self.group_participants_by_chat.setdefault(
                participant.group_jid,
                {},
            )
            previous = known_participants.get(participant.jid)
            if previous == participant:
                continue
            known_participants[participant.jid] = participant
            changed_participants.append(participant)

        if not changed_participants or not self.current_jid:
            return

        self._queue_storage_write(
            self.message_store.upsert_group_participants,
            self.current_jid,
            list(changed_participants),
        )

    def _message_by_merge_key(
        self,
        chat_jid: str,
        key: tuple[object, ...],
    ) -> Message | None:
        for message in self.messages_by_chat.get(chat_jid, []):
            if self._message_merge_key(message) == key:
                return message

        return None

    def _handle_message_delivery_updated(
        self,
        chat_jid: str,
        message_id: str,
        delivery_state: str,
        detail: str = "",
    ) -> None:
        if not message_id:
            return

        state_key = (chat_jid, message_id)
        known_state = self.delivery_states_by_message.get(state_key, "")
        delivery_state = self._merge_delivery_state(known_state, delivery_state)
        self.delivery_states_by_message[state_key] = delivery_state

        for message in self.messages_by_chat.get(chat_jid, []):
            if message.message_id != message_id:
                continue
            previous_delivery_state = message.delivery_state
            message.delivery_state = self._merge_delivery_state(
                message.delivery_state,
                delivery_state,
            )
            self._persist_messages([message])
            self.conversation.refresh_message(message)
            self._update_chat_from_message(message)
            self._refresh_chat_order(chat_jid)
            if (
                message.outgoing
                and delivery_state == "sent"
                and previous_delivery_state != "sent"
                and getattr(self, "sent_message_sound_enabled", True)
            ):
                self.sent_message_sound.play()
            if detail:
                self.status_bar.SetStatusText(detail)
            return

    def _ensure_chat_for_message(self, message: Message) -> None:
        existing_chat = self._chat_by_jid(message.chat_jid)
        if existing_chat is not None:
            if not self.chat_list.has_chat(message.chat_jid):
                self.chat_list.upsert_chat(existing_chat)
            return

        is_group = message.chat_is_group or self._message_jid_may_be_group_chat(message.chat_jid)
        name = self._display_name_for_jid(message.chat_jid)
        preview = self._chat_preview_for_message(message)
        chat = Chat(
            jid=message.chat_jid,
            name=name,
            is_group=is_group,
            notifications_muted=self._chat_notifications_muted(message.chat_jid),
            notification_settings_known=self._chat_notification_settings_known(message.chat_jid),
            last_message_preview=preview,
            last_message_at=message.sent_at,
        )
        self._upsert_searchable_chat(chat)
        self.chat_list.upsert_chat(chat)
        if not is_group:
            self.chat_names_by_jid.setdefault(message.chat_jid, name)

    def _select_first_chat(self) -> None:
        chat = self.chat_list.select_first()
        if chat:
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
            participant = message.sender_name or self._display_name_for_jid(message.sender_jid)
            sender = f"{participant} en {sender}"
        preview = media_description(message) if has_media(message) else message.body
        if message.is_forwarded:
            preview = f"Reenviado. {preview}"
        preview = " ".join(preview.split())
        if len(preview) > 160:
            preview = f"{preview[:157]}..."
        self.speaker.speak(f"Mensaje de {sender}: {preview}")

    def _show_windows_notification(
        self,
        message: Message,
        *,
        current_chat_is_open: bool,
    ) -> bool:
        if (
            not self.windows_notifications_enabled
            or message.outgoing
            or self._message_notifications_muted(message)
            or (current_chat_is_open and self.IsActive())
        ):
            return False

        chat_name = self._speakable_chat_name(message.chat_jid)
        title = chat_name
        if message.chat_is_group and message.sender_jid:
            participant = message.sender_name or self._display_name_for_jid(message.sender_jid)
            title = f"{participant} en {chat_name}"

        preview = "Nuevo mensaje"
        if self.windows_notification_previews_enabled:
            preview = media_description(message) if has_media(message) else message.body
            if message.is_forwarded:
                preview = f"Reenviado. {preview}"

        return self.windows_notification_service.show_message(
            title=title,
            message=preview,
            chat_jid=message.chat_jid,
        )

    def _open_chat_from_windows_notification(self, chat_jid: str) -> None:
        wx.CallAfter(self._activate_chat_from_windows_notification, chat_jid)

    def _activate_chat_from_windows_notification(self, chat_jid: str) -> None:
        chat = self._chat_by_jid(chat_jid)
        if chat is None:
            self.status_bar.SetStatusText("El chat de la notificación ya no está disponible")
            return

        if self.IsIconized():
            self.Iconize(False)
        if not self.IsShown():
            self.Show()
        self.settings_panel.Hide()
        self.conversation.Hide()
        self.chat_list.Show()
        if self._search_is_active():
            self._clear_chat_search(selected_jid=chat_jid)
        if not self.chat_list.has_chat(chat_jid):
            self.chat_list.upsert_chat(chat)
        self.chat_list.force_refresh_visible(selected_jid=chat_jid)
        self.chat_list.select_chat_by_jid(chat_jid)
        self._show_selected_chat()
        self.Raise()
        self.RequestUserAttention()

    def _mark_chat_read_from_windows_notification(self, chat_jid: str) -> None:
        wx.CallAfter(self._apply_windows_notification_mark_read, chat_jid)

    def _apply_windows_notification_mark_read(self, chat_jid: str) -> None:
        chat = self._chat_by_jid(chat_jid)
        if chat is None:
            return
        self._update_chat_summary(chat_jid, mark_read=True)
        self._mark_chat_displayed(chat)
        if self.chat_list.IsShown() and not self.chat_list.is_searching:
            self.chat_list.force_refresh_visible(selected_jid=chat_jid)
        self.status_bar.SetStatusText(f"{chat.name}: marcado como leído")

    def _speakable_chat_name(self, jid: str) -> str:
        chat = self.chat_list.chat_by_jid(jid)
        if chat is not None and chat.name and chat.name != jid:
            return chat.name

        name = self.chat_names_by_jid.get(jid, "")
        if name and name != jid:
            return name

        return self._fallback_display_name_for_jid(jid)

    def _finish_initial_chat_loading(self, loaded_count: int) -> None:
        started_at = time.perf_counter()
        self.loading_initial_chat_activity = False
        load_started_at = time.perf_counter()
        cached_chats = self._load_cached_chats()
        self._debug_perf(
            "_finish_initial_chat_loading.load_cached_chats",
            load_started_at,
            cached=len(cached_chats),
            pending=len(self.pending_chat_activity),
        )
        chats_by_jid = {chat.jid: chat for chat in cached_chats}

        merge_started_at = time.perf_counter()
        pending_updated_chats: list[Chat] = []
        for activity in self.pending_chat_activity.values():
            chat = chats_by_jid.get(activity.chat_jid)
            if chat is None:
                chat = Chat(
                    jid=activity.chat_jid,
                    name=self._display_name_for_jid(activity.chat_jid),
                    is_group=activity.is_group,
                    notifications_muted=self._chat_notifications_muted(activity.chat_jid),
                )

            updated_chat = self._updated_chat_summary(
                chat,
                preview=activity.preview,
                sent_at=activity.sent_at,
                unread_count=activity.unread_count,
                is_group=activity.is_group,
            )
            chats_by_jid[activity.chat_jid] = updated_chat
            pending_updated_chats.append(updated_chat)
        self._persist_chats(pending_updated_chats)
        self._debug_perf(
            "_finish_initial_chat_loading.merge_pending",
            merge_started_at,
            pending=len(self.pending_chat_activity),
        )

        self.pending_chat_activity = {}
        all_chats = list(chats_by_jid.values())
        merged_chats = self._merge_initial_chat_state(all_chats)
        chats = self._sort_chats_by_recency(self._chats_with_activity(merged_chats))
        self.loaded_chat_summaries = max(len(chats), loaded_count)
        render_started_at = time.perf_counter()
        self._set_searchable_chats(merged_chats)
        self.chat_list.set_chats(chats, preserve_focused_order=False)
        self.chat_list.force_refresh_visible()
        self._debug_perf(
            "_finish_initial_chat_loading.render",
            render_started_at,
            chats=len(chats),
        )
        if not self.chat_list.selected_chat():
            self.chat_list.select_first()
            self.chat_list.focus()
        self._preload_recent_histories()
        self.status_bar.SetStatusText(f"{self.loaded_chat_summaries} chats cargados")
        self._debug_perf(
            "_finish_initial_chat_loading.total",
            started_at,
            chats=len(chats),
            loaded=loaded_count,
        )

    def _finish_initial_chat_loading_if_needed(self) -> None:
        if self.loading_initial_chat_activity:
            self._finish_initial_chat_loading(len(self.pending_chat_activity))

    def _load_cached_chats(self) -> list[Chat]:
        started_at = time.perf_counter()
        if not self.current_jid:
            return []

        try:
            load_chats_started_at = time.perf_counter()
            chats = self.message_store.load_chats(self.current_jid)
            self._debug_perf(
                "_load_cached_chats.load_chats",
                load_chats_started_at,
                chats=len(chats),
            )
            latest_started_at = time.perf_counter()
            latest_messages = self.message_store.load_latest_messages(self.current_jid)
            self._debug_perf(
                "_load_cached_chats.load_latest_messages",
                latest_started_at,
                messages=len(latest_messages),
            )
        except Exception:
            return []

        normalize_started_at = time.perf_counter()
        group_flag_writes = 0
        chats_by_jid = {chat.jid: chat for chat in chats}
        for message in latest_messages:
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
            self._update_chat_activity(message.chat_jid, self._message_timestamp(message))

        for chat in chats:
            if chat.jid == self.current_jid and chat.is_group:
                chat.is_group = False
                self.message_store.set_chat_group_flag(self.current_jid, chat.jid, False)
                group_flag_writes += 1
            elif chat.is_group and not self._jid_may_be_group_chat(chat.jid):
                chat.is_group = False
                self.message_store.set_chat_group_flag(self.current_jid, chat.jid, False)
                group_flag_writes += 1
            if chat.custom_name:
                chat.name = chat.custom_name
                self.chat_names_by_jid[chat.jid] = chat.custom_name
            elif chat.jid in self.chat_names_by_jid:
                chat.name = self._display_name_for_jid(chat.jid)
            else:
                chat.name = normalize_chat_name(chat.jid, chat.name)
            if not self._is_fallback_chat_name(chat.name, chat.jid):
                self.chat_names_by_jid.setdefault(chat.jid, chat.name)
        self._debug_perf(
            "_load_cached_chats.normalize",
            normalize_started_at,
            chats=len(chats),
            latest=len(latest_messages),
            group_flag_writes=group_flag_writes,
        )
        self._debug_perf(
            "_load_cached_chats.total",
            started_at,
            chats=len(chats),
            latest=len(latest_messages),
        )
        return chats

    @staticmethod
    def _merge_chat_lists(primary: list[Chat], secondary: list[Chat]) -> list[Chat]:
        chats_by_jid: dict[str, Chat] = {chat.jid: chat for chat in primary}
        for chat in secondary:
            existing = chats_by_jid.get(chat.jid)
            if existing is None:
                chats_by_jid[chat.jid] = chat
                continue

            chats_by_jid[chat.jid] = Chat(
                jid=chat.jid,
                name=MainWindow._preferred_chat_name(existing, chat),
                custom_name=chat.custom_name or existing.custom_name,
                is_group=chat.is_group or existing.is_group,
                notifications_muted=(
                    chat.notifications_muted
                    if chat.notification_settings_known
                    else existing.notifications_muted
                ),
                notification_settings_known=(
                    chat.notification_settings_known or existing.notification_settings_known
                ),
                group_member_count=chat.group_member_count or existing.group_member_count,
                is_self_group=chat.is_self_group or existing.is_self_group,
                unread_count=chat.unread_count,
                last_message_preview=chat.last_message_preview,
                last_message_at=chat.last_message_at,
            )

        return list(chats_by_jid.values())

    def _merge_initial_chat_state(self, refreshed_chats: list[Chat]) -> list[Chat]:
        """Merge activity into canonical chats before rebuilding the visible list."""
        return self._merge_chat_lists(
            list(self.searchable_chats_by_jid.values()),
            refreshed_chats,
        )

    @staticmethod
    def _is_fallback_chat_name(name: str, jid: str) -> bool:
        """Return whether *name* is only the technical label derived from a JID."""
        return is_fallback_chat_name(jid, name)

    @staticmethod
    def _preferred_chat_name(existing: Chat, incoming: Chat) -> str:
        """Keep a known group title when discovery has no title to contribute."""
        if existing.custom_name:
            return existing.custom_name

        incoming_name = incoming.name.strip()
        existing_name = existing.name.strip()
        is_group = existing.is_group or incoming.is_group
        if (
            is_group
            and MainWindow._is_fallback_chat_name(incoming_name, incoming.jid)
            and not MainWindow._is_fallback_chat_name(existing_name, existing.jid)
        ):
            return existing_name
        return incoming_name or existing_name

    def _load_cached_messages_for_chat(self, chat_jid: str) -> None:
        started_at = time.perf_counter()
        if not self.current_jid:
            return

        cache_key = (self.current_jid, chat_jid)
        if cache_key in self.cached_message_loads:
            return

        try:
            load_started_at = time.perf_counter()
            cached_messages = self.message_store.load_recent_messages(
                self.current_jid,
                chat_jid,
                limit=5000,
            )
            self._debug_perf(
                "_load_cached_messages_for_chat.load_recent_messages",
                load_started_at,
                chat=chat_jid,
                messages=len(cached_messages),
            )
        except Exception:
            return
        self.cached_message_loads.add(cache_key)

        if cached_messages:
            merge_started_at = time.perf_counter()
            self._normalize_audio_metadata_for_messages(cached_messages)
            self._merge_messages(chat_jid, cached_messages)
            self._update_chat_activity_from_messages(chat_jid, cached_messages)
            self._update_chat_preview_from_messages(chat_jid, cached_messages)
            for message in cached_messages:
                if self._message_may_be_lottie_sticker(message):
                    self._auto_download_media_message(message)
            self._debug_perf(
                "_load_cached_messages_for_chat.merge",
                merge_started_at,
                chat=chat_jid,
                messages=len(cached_messages),
            )
        self._debug_perf(
            "_load_cached_messages_for_chat.total",
            started_at,
            chat=chat_jid,
            messages=len(cached_messages),
        )

    def _persist_chat(self, chat: Chat) -> None:
        if not self.current_jid:
            return

        self._queue_storage_write(
            self.message_store.upsert_chat,
            self.current_jid,
            replace(chat),
        )

    def _persist_chats(self, chats: list[Chat]) -> None:
        if not self.current_jid or not chats:
            return

        self._queue_storage_write(
            self.message_store.upsert_chats,
            self.current_jid,
            [replace(chat) for chat in chats],
        )

    def _persist_messages(self, messages: list[Message]) -> None:
        if not self.current_jid or not messages:
            return

        self._queue_storage_write(
            self.message_store.upsert_messages,
            self.current_jid,
            [replace(message) for message in messages],
        )

    def _queue_storage_write(
        self,
        operation: Callable[..., object],
        *args: object,
    ) -> None:
        executor = getattr(self, "storage_executor", None)
        if executor is None:
            self._run_storage_write(operation, *args)
            return

        try:
            executor.submit(self._run_storage_write, operation, *args)
        except RuntimeError:
            return

    @staticmethod
    def _run_storage_write(operation: Callable[..., object], *args: object) -> None:
        try:
            operation(*args)
        except Exception:
            return

    def _normalize_audio_metadata_for_messages(self, messages: list[Message]) -> None:
        candidates: list[tuple[Message, Path, str]] = []
        for message in messages:
            if message.media_kind != "audio" or message.media_duration_seconds > 0:
                continue
            path = local_media_path(message)
            if path is None:
                continue
            key = str(path)
            if key in self.audio_metadata_in_progress:
                continue
            self.audio_metadata_in_progress.add(key)
            candidates.append((message, path, key))

        if not candidates:
            return

        def worker() -> None:
            results = [
                (message, media_duration_seconds(path), key)
                for message, path, key in candidates
            ]
            wx.CallAfter(self._finish_audio_metadata_normalization, results)

        executor = getattr(self, "audio_metadata_executor", None)
        if executor is None:
            threading.Thread(target=worker, daemon=True).start()
            return

        try:
            executor.submit(worker)
        except RuntimeError:
            for _message, _path, key in candidates:
                self.audio_metadata_in_progress.discard(key)

    def _finish_audio_metadata_normalization(
        self,
        results: list[tuple[Message, float, str]],
    ) -> None:
        changed_messages: list[Message] = []
        visible_chat_jid = (
            self.conversation.current_chat.jid
            if self.conversation.IsShown() and self.conversation.current_chat
            else ""
        )
        for message, duration, key in results:
            self.audio_metadata_in_progress.discard(key)
            if duration <= 0 or message.media_duration_seconds > 0:
                continue
            message.media_duration_seconds = duration
            changed_messages.append(message)
            if message.chat_jid == visible_chat_jid:
                self.conversation.refresh_message(message)
            self._update_chat_from_message(message)

        self._persist_messages(changed_messages)
        if changed_messages:
            self._refresh_chat_order()

    def _load_conversation(self, chat: Chat, unread_count: int = 0) -> None:
        started_at = time.perf_counter()
        self._load_cached_messages_for_chat(chat.jid)
        self._load_cached_group_participants(chat)
        preserving_visible_chat = bool(
            self.conversation.IsShown()
            and self.conversation.current_chat
            and self.conversation.current_chat.jid == chat.jid
        )
        if preserving_visible_chat:
            self.conversation.current_chat = chat
            self.conversation.set_contact_summary(chat.name, "")
        else:
            self.conversation.set_chat(chat)
        self._refresh_conversation_avatar(chat)
        render_started_at = time.perf_counter()
        self.conversation.set_messages(
            self.messages_by_chat.get(chat.jid, []),
            unread_count=unread_count,
        )
        self._debug_perf(
            "_load_conversation.render_messages",
            render_started_at,
            chat=chat.jid,
            messages=len(self.messages_by_chat.get(chat.jid, [])),
        )
        self._sync_recording_ui()
        self._refresh_load_older_button(chat.jid)
        self._debug_perf(
            "_load_conversation.total",
            started_at,
            chat=chat.jid,
            unread=unread_count,
        )

    def _sync_recording_ui(self) -> None:
        self.conversation.set_recording_state(
            self.audio_recorder.is_recording,
            self.audio_recorder.is_paused,
        )

    def _refresh_chat_order(
        self,
        selected_jid: str = "",
        preserve_focused_order: bool = True,
    ) -> None:
        selected_chat = self.chat_list.selected_chat()
        selected_jid = selected_jid or (selected_chat.jid if selected_chat else "")
        if not selected_jid and self.conversation.current_chat:
            selected_jid = self.conversation.current_chat.jid
        self.chat_list.set_chats(
            self._sort_chats_by_recency(self.chat_list.chats()),
            selected_jid=selected_jid,
            preserve_focused_order=preserve_focused_order,
        )
        if self._search_is_active() and self.chat_list.IsShown():
            self._apply_chat_search()

    def _sort_chats_by_recency(self, chats: list[Chat]) -> list[Chat]:
        return sorted(chats, key=self._chat_recency_key)

    @staticmethod
    def _chats_with_activity(chats: Iterable[Chat]) -> list[Chat]:
        """Keep roster-only contacts searchable without rendering empty chat rows."""
        return [
            chat
            for chat in chats
            if chat.last_message_at is not None
            or bool(chat.last_message_preview.strip())
            or chat.unread_count > 0
        ]

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

    def _message_jid_may_be_group_chat(self, jid: str) -> bool:
        return jid != self.current_jid and self._jid_may_be_group_chat(jid)

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
        preview = self._chat_preview_for_message(message)
        self._update_chat_summary(
            message.chat_jid,
            preview=preview,
            sent_at=message.sent_at,
            unread_delta=1 if mark_unread else 0,
            force_preview=True,
            is_group=message.chat_is_group,
        )
        self._apply_synced_chat_displayed(message.chat_jid)

    @staticmethod
    def _chat_preview_for_message(message: Message) -> str:
        if message.retracted:
            return "Eliminaste este mensaje" if message.outgoing else "Este mensaje fue eliminado"
        preview = media_description(message) if has_media(message) else message.body
        if message.is_forwarded:
            preview = f"Reenviado. {preview}"
        if message.outgoing and message.delivery_state == "pending":
            return f"{preview} | Enviando"
        if message.outgoing and message.delivery_state == "failed":
            return f"{preview} | No enviado"
        if message.outgoing and message.delivery_state in {"delivered", "received"}:
            return f"{preview} | Entregado"
        if message.outgoing and message.delivery_state in {"displayed", "read"}:
            return f"{preview} | Leído"
        return preview

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
        visible_chat = self._visible_chat_by_jid(chat_jid)
        chat = visible_chat or self.searchable_chats_by_jid.get(chat_jid)
        if chat is not None:
            updated_chat = self._updated_chat_summary(
                chat,
                preview=preview,
                sent_at=sent_at,
                unread_delta=unread_delta,
                unread_count=unread_count,
                mark_read=mark_read,
                force_preview=force_preview,
                is_group=is_group,
            )
            self._upsert_searchable_chat(updated_chat)
            if visible_chat is not None or preview or sent_at is not None:
                self.chat_list.upsert_chat(updated_chat)
            self._persist_chat(updated_chat)
            return

        updated_chat = Chat(
            jid=chat_jid,
            name=self._display_name_for_jid(chat_jid),
            is_group=is_group,
            notifications_muted=self._chat_notifications_muted(chat_jid),
            notification_settings_known=self._chat_notification_settings_known(chat_jid),
            unread_count=self._next_unread_count(
                0,
                unread_delta=unread_delta,
                unread_count=unread_count,
                mark_read=mark_read,
            ),
            last_message_preview=preview,
            last_message_at=sent_at,
        )
        self._upsert_searchable_chat(updated_chat)
        if preview or sent_at is not None:
            self.chat_list.upsert_chat(updated_chat)
        self._persist_chat(updated_chat)
        if not is_group:
            self.chat_names_by_jid.setdefault(chat_jid, self._display_name_for_jid(chat_jid))

    def _updated_chat_summary(
        self,
        chat: Chat,
        preview: str = "",
        sent_at: datetime | None = None,
        unread_delta: int = 0,
        unread_count: int | None = None,
        mark_read: bool = False,
        force_preview: bool = False,
        is_group: bool = False,
    ) -> Chat:
        return Chat(
            jid=chat.jid,
            name=chat.name,
            custom_name=chat.custom_name,
            is_group=chat.is_group or is_group,
            notifications_muted=chat.notifications_muted,
            notification_settings_known=chat.notification_settings_known,
            group_member_count=chat.group_member_count,
            is_self_group=chat.is_self_group,
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

    def _mark_current_chat_displayed(self, chat_jid: str) -> None:
        chat = self.conversation.current_chat
        if chat is None or chat.jid != chat_jid:
            return

        self._mark_chat_displayed(chat)

    def _handle_synced_chat_displayed(self, chat_jid: str, marker_id: str) -> None:
        if not chat_jid or not marker_id:
            return

        current_marker_id = self.synced_displayed_marker_ids_by_chat.get(chat_jid, "")
        if current_marker_id:
            current_position = self._displayed_marker_position(chat_jid, current_marker_id)
            incoming_position = self._displayed_marker_position(chat_jid, marker_id)
            if current_position is not None and (
                incoming_position is None or incoming_position[1] < current_position[1]
            ):
                return

        self.synced_displayed_marker_ids_by_chat[chat_jid] = marker_id
        self._apply_synced_chat_displayed(chat_jid)

    def _apply_synced_chat_displayed(self, chat_jid: str) -> None:
        marker_id = self.synced_displayed_marker_ids_by_chat.get(chat_jid, "")
        chat = self._chat_by_jid(chat_jid)
        position = self._displayed_marker_position(chat_jid, marker_id)
        if chat is None or position is None:
            return

        remaining_unread, marker_timestamp, latest_known_timestamp = position
        chat_timestamp = self._datetime_timestamp(chat.last_message_at)
        if (
            chat_timestamp is not None
            and chat_timestamp > latest_known_timestamp
            and chat_timestamp > marker_timestamp
        ):
            return

        unread_count = min(chat.unread_count, remaining_unread)
        if unread_count == chat.unread_count:
            return

        self._update_chat_summary(chat_jid, unread_count=unread_count)

    def _displayed_marker_position(
        self,
        chat_jid: str,
        marker_id: str,
    ) -> tuple[int, float, float] | None:
        chat = self._chat_by_jid(chat_jid)
        is_group = bool(chat and chat.is_group)
        ordered_messages = sorted(
            self.messages_by_chat.get(chat_jid, []),
            key=self._message_timestamp,
        )
        if not ordered_messages:
            return None

        marker_index = next(
            (
                index
                for index in range(len(ordered_messages) - 1, -1, -1)
                if (
                    ordered_messages[index].displayed_marker_id
                    if is_group
                    else ordered_messages[index].message_id
                )
                == marker_id
            ),
            None,
        )
        if marker_index is None:
            return None

        return (
            sum(not message.outgoing for message in ordered_messages[marker_index + 1 :]),
            self._message_timestamp(ordered_messages[marker_index]),
            self._message_timestamp(ordered_messages[-1]),
        )

    def _mark_chat_displayed(self, chat: Chat) -> None:
        chat_jid = chat.jid

        messages = self.messages_by_chat.get(chat_jid, [])
        received_messages = [
            message
            for message in messages
            if message.chat_jid == chat_jid
            and not message.outgoing
            and (message.displayed_marker_id if chat.is_group else message.message_id)
        ]
        if not received_messages:
            return

        latest_message = max(received_messages, key=self._message_timestamp)
        marker_id = (
            latest_message.displayed_marker_id if chat.is_group else latest_message.message_id
        )
        if self.displayed_marker_ids_by_chat.get(chat_jid) == marker_id:
            return

        self.xmpp.mark_chat_displayed(chat_jid, marker_id, is_group=chat.is_group)
        self.displayed_marker_ids_by_chat[chat_jid] = marker_id

    def _show_selected_chat(self) -> None:
        item = self.chat_list.selected_item()
        chat = item.chat if item is not None else self.chat_list.selected_chat()
        if not chat:
            self.status_bar.SetStatusText("Selecciona un chat para abrirlo")
            return

        target_message = item.message if item is not None else None
        if target_message is not None:
            self._merge_messages(chat.jid, [target_message])
            self._update_chat_activity(chat.jid, self._message_timestamp(target_message))

        if chat.is_group:
            self.xmpp.join_group_chat(chat.jid)
        else:
            self.xmpp.request_contact_presence_subscription(chat.jid)
        self._load_conversation(chat, unread_count=chat.unread_count)
        self._update_chat_summary(chat.jid, mark_read=True)
        self.settings_panel.Hide()
        self.chat_list.Hide()
        self.conversation.Show()
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        self._mark_current_chat_displayed(chat.jid)
        if target_message is not None:
            self.conversation.focus_message(target_message)
        else:
            self.conversation.focus_composer()
        self.status_bar.SetStatusText(f"Chat abierto: {chat.name}")
        self._refresh_current_chat_status_title()
        needs_history = (
            chat.jid not in self.history_loaded_chats
            or self._chat_history_needs_reload(chat.jid)
        )
        if needs_history:
            self.status_bar.SetStatusText(f"Cargando todo el historial de {chat.name}...")
            self._request_full_history(chat.jid)

    def _show_chat_list(self) -> str:
        selected_jid = self.conversation.current_chat.jid if self.conversation.current_chat else ""
        if self.audio_recorder.is_recording:
            self.audio_recorder.cancel()
            self.conversation.set_recording_state(False)
        self.reply_context = None
        self.conversation.clear_reply_quote()
        self.edit_context = None
        self.conversation.clear_editing()
        self.conversation.clear_unread_marker()
        self._reset_window_title()
        self.settings_panel.Hide()
        self.conversation.Hide()
        self._mark_current_chat_displayed(selected_jid)
        self.chat_list.Show()
        if self._search_is_active():
            self._apply_chat_search()
        else:
            self.chat_list.refresh_visible_if_stale()
        self._restore_chat_list_focus(selected_jid)
        self.content_panel.Layout()
        self.workspace_panel.Layout()
        self.Layout()
        wx.CallAfter(self._restore_chat_list_focus, selected_jid)
        return selected_jid

    def _restore_chat_list_focus(self, selected_jid: str) -> None:
        if selected_jid:
            self.chat_list.select_chat_by_jid(selected_jid)
        self.chat_list.focus()

    def _set_chat_state(self, chat_jid: str, state: str, media: str = "") -> None:
        previous_state = self.chat_state_by_chat.get(chat_jid, "")
        next_state = "recording_audio" if state == "composing" and media == "audio" else state
        if next_state in {"composing", "recording_audio"}:
            self.chat_state_by_chat[chat_jid] = next_state
        else:
            self.chat_state_by_chat.pop(chat_jid, None)
        self._refresh_current_chat_status_title()
        if next_state != previous_state:
            if next_state == "recording_audio":
                self._speak_chat_state(chat_jid, "está grabando audio")
            elif next_state == "composing":
                self._speak_chat_state(chat_jid, "está escribiendo")

    def _speak_chat_state(self, chat_jid: str, state_text: str) -> None:
        chat = self.conversation.current_chat
        if chat is None or not self.conversation.IsShown() or chat.jid != chat_jid:
            return

        self.speaker.speak(f"{chat.name} {state_text}")

    def _refresh_current_chat_status_title(self) -> None:
        chat = self.conversation.current_chat
        if chat is None or not self.conversation.IsShown():
            return

        self.conversation.set_contact_summary(
            chat.name,
            self._contact_connection_status_text(chat.jid),
        )
        self.SetTitle(f"{APP_WINDOW_TITLE} - {chat.name}")

    def _reset_window_title(self) -> None:
        self.SetTitle(APP_WINDOW_TITLE)

    def _conversation_status_text(self, chat_jid: str) -> str:
        if self.chat_state_by_chat.get(chat_jid) == "recording_audio":
            return "contacto grabando audio"
        if self.chat_state_by_chat.get(chat_jid) == "composing":
            return "contacto escribiendo"

        presence = self.contact_presence_by_chat.get(chat_jid)
        if presence is None:
            return ""

        if presence.availability == "online":
            return "contacto en línea"
        if presence.availability == "away":
            return "contacto ausente"
        if presence.availability == "busy":
            return "contacto ocupado"
        if presence.last_seen is not None:
            return f"últ. vez {self._format_presence_time(presence.last_seen)}"
        if presence.status:
            return presence.status
        return ""

    def _contact_connection_status_text(self, chat_jid: str) -> str:
        presence = self.contact_presence_by_chat.get(chat_jid)
        if presence is None:
            return ""

        if presence.availability == "online":
            return "en línea"
        if presence.availability == "away":
            return "ausente"
        if presence.availability == "busy":
            return "ocupado"
        if presence.last_seen is not None:
            return f"últ. vez {self._format_presence_time(presence.last_seen)}"
        if presence.status:
            return presence.status
        return ""

    def _on_contact_info(self, _event: wx.CommandEvent) -> None:
        chat = self.conversation.current_chat
        if chat is None:
            return

        avatar_path = self._contact_avatar_path(chat)
        dialog = ContactInfoDialog(
            self,
            chat=chat,
            status=self._contact_connection_status_text(chat.jid),
            avatar_path=avatar_path,
            on_describe_photo=self._send_contact_photo_to_rayoai,
        )
        self.contact_info_dialog = dialog
        if avatar_path is None:
            self._request_contact_avatar(chat.jid)
        try:
            dialog.ShowModal()
        finally:
            if self.contact_info_dialog is dialog:
                self.contact_info_dialog = None
            dialog.Destroy()

    def _contact_avatar_path(self, chat: Chat) -> Path | None:
        path = self.contact_avatar_paths_by_chat.get(chat.jid)
        if path is not None and path.exists():
            return path

        avatar_prefix = self._avatar_filename_prefix(chat.jid)
        existing_paths = sorted(CONTACT_AVATARS_DIR.glob(f"{avatar_prefix}.*"))
        for existing_path in existing_paths:
            if existing_path.is_file():
                self.contact_avatar_paths_by_chat[chat.jid] = existing_path
                return existing_path

        return None

    def _request_contact_avatar(self, chat_jid: str) -> None:
        if not self.whatsapp_verified:
            return
        if chat_jid in self.contact_avatar_requests_in_progress:
            return

        self.contact_avatar_requests_in_progress.add(chat_jid)
        self.status_bar.SetStatusText("Buscando foto de perfil...")
        self.xmpp.fetch_contact_avatar(chat_jid)

    def _handle_contact_avatar_received(
        self,
        chat_jid: str,
        data: bytes,
        mime: str,
        avatar_id: str,
    ) -> None:
        self.contact_avatar_requests_in_progress.discard(chat_jid)
        try:
            path = self._save_contact_avatar(chat_jid, data, mime, avatar_id)
        except OSError as exc:
            self.status_bar.SetStatusText(f"No se pudo guardar la foto de perfil: {exc}")
            return

        self.contact_avatar_paths_by_chat[chat_jid] = path
        current_chat = self.conversation.current_chat
        if (
            current_chat is not None
            and self.conversation.IsShown()
            and current_chat.jid == chat_jid
        ):
            self.conversation.set_contact_avatar(path)
        if (
            self.contact_info_dialog is not None
            and self.contact_info_dialog.chat_jid == chat_jid
        ):
            self.contact_info_dialog.set_avatar_path(path)
        self.status_bar.SetStatusText("Foto de perfil descargada")

    def _handle_contact_avatar_unavailable(self, chat_jid: str, detail: str) -> None:
        self.contact_avatar_requests_in_progress.discard(chat_jid)
        message = detail or "Foto de perfil no disponible."
        current_chat = self.conversation.current_chat
        if (
            current_chat is not None
            and self.conversation.IsShown()
            and current_chat.jid == chat_jid
        ):
            self.conversation.set_contact_avatar(None)
        if (
            self.contact_info_dialog is not None
            and self.contact_info_dialog.chat_jid == chat_jid
        ):
            self.contact_info_dialog.set_avatar_unavailable(message)
        self.status_bar.SetStatusText(message)

    def _refresh_conversation_avatar(self, chat: Chat) -> None:
        avatar_path = self._contact_avatar_path(chat)
        self.conversation.set_contact_avatar(avatar_path)
        if avatar_path is None:
            self._request_contact_avatar(chat.jid)

    def _save_contact_avatar(
        self,
        chat_jid: str,
        data: bytes,
        mime: str,
        avatar_id: str,
    ) -> Path:
        CONTACT_AVATARS_DIR.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{self._avatar_filename_prefix(chat_jid)}-"
            f"{self._safe_avatar_token(avatar_id or hashlib.sha256(data).hexdigest()[:16])}"
            f"{self._avatar_extension(mime)}"
        )
        path = CONTACT_AVATARS_DIR / filename
        path.write_bytes(data)
        return path

    @staticmethod
    def _avatar_filename_prefix(chat_jid: str) -> str:
        return hashlib.sha256(chat_jid.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _safe_avatar_token(value: str) -> str:
        cleaned = "".join(
            character if character.isalnum() or character in ("-", "_") else "-"
            for character in value.strip()
        ).strip("-")
        return cleaned[:48] or "avatar"

    @staticmethod
    def _avatar_extension(mime: str) -> str:
        normalized = mime.lower().split(";", 1)[0].strip()
        if normalized in {"image/jpeg", "image/jpg"}:
            return ".jpg"
        if normalized == "image/png":
            return ".png"
        if normalized == "image/webp":
            return ".webp"
        if normalized == "image/gif":
            return ".gif"
        return ".jpg"

    def _send_contact_photo_to_rayoai(self, path: Path) -> None:
        if rayoai.send_open_path(path):
            self.status_bar.SetStatusText("Foto enviada a RayoAI")
            return

        self.status_bar.SetStatusText("No se pudo enviar a RayoAI. Verifica que esté abierto.")

    @staticmethod
    def _format_presence_time(value: datetime) -> str:
        if value.tzinfo is not None:
            value = value.astimezone()

        hour = value.hour
        minute = value.minute
        suffix = "a. m." if hour < 12 else "p. m."
        hour_12 = hour % 12 or 12
        today = datetime.now(value.tzinfo).date() if value.tzinfo else datetime.now().date()
        if value.date() == today:
            return f"hoy {hour_12}:{minute:02d} {suffix}"
        return f"{value.day:02d}/{value.month:02d} {hour_12}:{minute:02d} {suffix}"

    def _upsert_discovered_chats(self, chats: list[Chat]) -> None:
        if not chats:
            return

        added = 0
        merged_chats: list[Chat] = []
        for chat in chats:
            existing = self._chat_by_jid(chat.jid)
            if existing is not None:
                merged_chat = Chat(
                    jid=existing.jid,
                    name=self._preferred_chat_name(existing, chat),
                    custom_name=existing.custom_name,
                    is_group=existing.is_group or chat.is_group,
                    notifications_muted=(
                        chat.notifications_muted
                        if chat.notification_settings_known
                        else existing.notifications_muted
                    ),
                    notification_settings_known=(
                        existing.notification_settings_known
                        or chat.notification_settings_known
                    ),
                    group_member_count=chat.group_member_count or existing.group_member_count,
                    is_self_group=chat.is_self_group or existing.is_self_group,
                    unread_count=existing.unread_count,
                    last_message_preview=existing.last_message_preview,
                    last_message_at=existing.last_message_at,
                )
            else:
                merged_chat = chat
                if not self.chat_list.has_chat(chat.jid):
                    added += 1

            if not (
                merged_chat.is_group
                and self._is_fallback_chat_name(merged_chat.name, merged_chat.jid)
            ):
                self.chat_names_by_jid[merged_chat.jid] = merged_chat.name
            self._upsert_searchable_chat(merged_chat)
            self.chat_list.upsert_chat(merged_chat)
            merged_chats.append(merged_chat)

        self._persist_chats(merged_chats)

        self.loaded_chat_summaries += added
        self._refresh_chat_order()
        if added and self.loading_initial_chat_activity and not self.chat_list.is_searching:
            self.chat_list.force_refresh_visible()
        self._select_first_chat_if_needed()
        self.status_bar.SetStatusText(f"{self.loaded_chat_summaries} chats disponibles")

    def _chat_by_jid(self, jid: str) -> Chat | None:
        return self._visible_chat_by_jid(jid) or self.searchable_chats_by_jid.get(jid)

    def _visible_chat_by_jid(self, jid: str) -> Chat | None:
        return self.chat_list.chat_by_jid(jid)

    def _chat_notifications_muted(self, jid: str) -> bool:
        chat = self._chat_by_jid(jid)
        return bool(chat and chat.notifications_muted)

    def _chat_notification_settings_known(self, jid: str) -> bool:
        chat = self._chat_by_jid(jid)
        return bool(chat and chat.notification_settings_known)

    def _message_notifications_muted(self, message: Message) -> bool:
        return self._chat_notifications_muted(message.chat_jid)

    def _update_chat_names(self, chats: list[Chat]) -> None:
        for chat in chats:
            name = normalize_chat_name(chat.jid, chat.name)
            if chat.is_group and self._is_fallback_chat_name(name, chat.jid):
                continue
            self.chat_names_by_jid[chat.jid] = name

    def _display_name_for_jid(self, jid: str) -> str:
        if jid in self.chat_names_by_jid:
            name = self.chat_names_by_jid[jid]
            if name and name != jid:
                return name

        return self._fallback_display_name_for_jid(jid)

    @staticmethod
    def _fallback_display_name_for_jid(jid: str) -> str:
        return display_label_from_jid(jid) or jid


class ContactInfoDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        chat: Chat,
        status: str,
        avatar_path: Path | None = None,
        on_describe_photo: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent, title=f"Información de {chat.name}", size=(620, 420))
        self._chat = chat
        self._status = status
        self._avatar_path = avatar_path
        self._avatar_status = ""
        self._on_describe_photo = on_describe_photo

        body = self._format_contact_info(chat, status, avatar_path, self._avatar_status)
        self._text = wx.TextCtrl(
            self,
            value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self._text.SetInsertionPoint(0)

        self._describe_button = wx.Button(self, label="Describir foto con RayoAI")
        self._describe_button.Enable(avatar_path is not None and on_describe_photo is not None)
        close_button = wx.Button(self, wx.ID_OK, "Cerrar")

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self._describe_button, 0, wx.RIGHT, 8)
        buttons.Add(close_button, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self._text, 1, wx.ALL | wx.EXPAND, 12)
        box.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 12)
        self.SetSizer(box)
        apply_theme(self)
        self.Bind(wx.EVT_BUTTON, self._on_describe_photo_clicked, self._describe_button)
        self.Bind(wx.EVT_BUTTON, lambda _event: self.EndModal(wx.ID_OK), close_button)
        wx.CallAfter(self._text.SetFocus)

    @property
    def chat_jid(self) -> str:
        return self._chat.jid

    def set_avatar_path(self, path: Path) -> None:
        self._avatar_path = path
        self._avatar_status = ""
        self._text.SetValue(
            self._format_contact_info(self._chat, self._status, path, self._avatar_status)
        )
        self._text.SetInsertionPoint(0)
        self._describe_button.Enable(self._on_describe_photo is not None)

    def set_avatar_unavailable(self, detail: str) -> None:
        self._avatar_path = None
        self._avatar_status = detail
        self._text.SetValue(
            self._format_contact_info(
                self._chat,
                self._status,
                self._avatar_path,
                self._avatar_status,
            )
        )
        self._text.SetInsertionPoint(0)
        self._describe_button.Enable(False)

    @classmethod
    def _format_contact_info(
        cls,
        chat: Chat,
        status: str,
        avatar_path: Path | None = None,
        avatar_status: str = "",
    ) -> str:
        lines = [
            f"Nombre: {chat.name}",
            f"Tipo: {'grupo' if chat.is_group else 'contacto'}",
        ]
        if status:
            lines.append(f"Estado: {status}")
        phone = cls._phone_from_jid(chat.jid)
        if phone:
            lines.append(f"Número: {phone}")
        if chat.custom_name:
            lines.append(f"Nombre personalizado: {chat.custom_name}")
        if chat.notifications_muted:
            lines.append("Notificaciones: silenciadas")
        if chat.is_group and chat.group_member_count:
            lines.append(f"Participantes: {chat.group_member_count}")

        lines.extend(
            (
                "",
                (
                    "Foto de perfil: lista para describir con RayoAI"
                    if avatar_path is not None
                    else f"Foto de perfil: {avatar_status or 'pendiente de consulta'}"
                ),
                "Mensaje de estado: no disponible por ahora",
            )
        )
        return "\n".join(lines)

    def _on_describe_photo_clicked(self, _event: wx.CommandEvent) -> None:
        if self._avatar_path is None or self._on_describe_photo is None:
            return

        self._on_describe_photo(self._avatar_path)

    @staticmethod
    def _phone_from_jid(jid: str) -> str:
        local = jid.split("@", 1)[0].strip()
        if not local.startswith("+"):
            return ""
        return local
