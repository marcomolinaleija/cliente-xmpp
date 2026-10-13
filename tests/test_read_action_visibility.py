from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.ui.main_window import MainWindow


class ReadActionVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self):
        self.window = MainWindow.__new__(MainWindow)
        wx.Frame.__init__(self.window, None, title="Fixture", size=(700, 500))
        self.window.login_panel = wx.Panel(self.window)
        self.window.settings_store = SimpleNamespace(load_audio_speed=lambda: 1.0)

        def panel(parent, *_args, **_kwargs):
            return wx.Panel(parent)

        with (
            patch("cliente_xmpp.ui.main_window.ChatListPanel", side_effect=panel),
            patch("cliente_xmpp.ui.main_window.ConversationPanel", side_effect=panel),
            patch("cliente_xmpp.ui.main_window.SettingsPanel", side_effect=panel),
        ):
            # Exercise the real layout's event binding, not a manually called handler.
            self.window._layout()

    def tearDown(self):
        self.window.Destroy()

    def test_button_follows_native_view_show_events_and_remote_state_does_not_reshow_it(self):
        window = self.window
        button = window.connection_header.mark_all_read_button
        self.assertTrue(button.IsShown())
        window.chat_list.Hide()
        window.conversation.Show()
        self.assertFalse(button.IsShown())
        self.assertFalse(window.connection_header.GetSizer().IsShown(button))
        button.Enable(False)
        button.Enable(True)
        self.assertFalse(button.IsShown())
        window.conversation.Hide()
        window.settings_panel.Show()
        self.assertFalse(button.IsShown())
        window.settings_panel.Hide()
        window.chat_list.Show()
        self.assertTrue(button.IsShown())
        self.assertTrue(window.connection_header.GetSizer().IsShown(button))

    def test_hidden_button_and_alt_m_cannot_trigger_mass_read_inside_chat(self):
        window = self.window
        window._mark_all_chats_read = Mock()
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(ord("M"))
        event.SetAltDown(True)
        window.chat_list.Hide()
        window.conversation.Show()
        window._on_mark_all_chats_read(wx.CommandEvent())
        window._on_key_down(event)
        window._mark_all_chats_read.assert_not_called()
        self.assertTrue(event.GetSkipped())
        window.conversation.Hide()
        window.chat_list.Show()
        window._on_mark_all_chats_read(wx.CommandEvent())
        window._on_key_down(event)
        self.assertEqual(window._mark_all_chats_read.call_count, 2)
