from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from cliente_xmpp.app.main import _parse_arguments, main
from cliente_xmpp.models.chat import Chat
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.events import XmppDisconnected


class DevelopmentModeTests(unittest.TestCase):
    def test_development_argument_has_short_and_long_forms(self) -> None:
        self.assertTrue(_parse_arguments(["-d"]).develop)
        self.assertTrue(_parse_arguments(["--develop"]).develop)
        self.assertFalse(_parse_arguments([]).develop)

    def test_setup_can_select_connection_mode_without_starting_wx(self) -> None:
        with (
            patch("cliente_xmpp.app.main.SettingsStore") as settings_store,
            patch("cliente_xmpp.app.main.SingleInstanceGuard") as single_instance,
        ):
            main(["--set-connection-mode", "remote"])

        settings_store.return_value.save_connection_mode.assert_called_once_with("remote")
        single_instance.assert_not_called()

    def test_development_mode_loads_cache_before_background_verification(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.connection_settings = SimpleNamespace(jid="me@example.test")
        window.current_jid = ""
        window.loaded_chat_summaries = 0
        window.connection_header = Mock()
        window.status_bar = Mock()
        window.speaker = Mock()
        window.chat_list = Mock()
        window._set_connected_ui = Mock()
        window._set_whatsapp_remote_actions_enabled = Mock()
        window._load_cached_chats = Mock(
            return_value=[Chat(jid="chat@example.test", name="Chat", last_message_preview="Hola")]
        )
        window._set_searchable_chats = Mock()
        window._chats_with_activity = Mock(side_effect=lambda chats: chats)
        window._sort_chats_by_recency = Mock(side_effect=lambda chats: chats)
        window._show_chat_placeholder = Mock()
        window._can_auto_connect = Mock(return_value=True)
        window._on_connect = Mock()

        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
            window._start_development_mode()

        window._load_cached_chats.assert_called_once_with()
        window.chat_list.set_chats.assert_called_once()
        window._set_whatsapp_remote_actions_enabled.assert_called_once_with(False)
        self.assertEqual(window.current_jid, "me@example.test")
        status = window.status_bar.SetStatusText.call_args.args[0]
        self.assertIn("Verificando en segundo plano", status)
        call_after.assert_any_call(window._on_connect, ANY)

    def test_development_mode_keeps_cache_visible_after_background_disconnect(self) -> None:
        window = SimpleNamespace(
            development_mode=True,
            _storage_reset_in_progress=False,
            whatsapp_verified=True,
            whatsapp_link_status="connected",
            pending_roster_chats=[],
            _set_whatsapp_remote_actions_enabled=Mock(),
            _show_chat_placeholder=Mock(),
            login_panel=Mock(),
            connection_header=Mock(),
            status_bar=Mock(),
        )

        MainWindow._handle_xmpp_event(window, XmppDisconnected(reason="Sin red"))

        window._show_chat_placeholder.assert_not_called()
        window._set_whatsapp_remote_actions_enabled.assert_called_once_with(False)
        window.connection_header.set_status.assert_called_once_with("Sin red")
