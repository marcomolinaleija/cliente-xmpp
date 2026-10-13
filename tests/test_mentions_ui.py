from __future__ import annotations

import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import wx

from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.mentions import GroupParticipant, MentionReference
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.main_window import MainWindow


class MentionCaretNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def test_native_suggestion_insertion_preserves_emoji_newlines_suffix_caret_and_target(self):
        frame = wx.Frame(None)
        compose = wx.TextCtrl(frame, style=wx.TE_MULTILINE)
        human = "Fixture Member 💙"
        raw = human.encode("punycode").decode("ascii")
        room, jid = "#fixture@example.test", "member@example.test"
        panel = SimpleNamespace(
            compose=compose, current_chat=Chat(room, "Fixture", is_group=True),
            selected_mention_suggestion_index=lambda: 0,
            hide_mention_suggestions=Mock(), show_mention_suggestions=Mock(),
        )
        panel.mention_insertion_point = MethodType(ConversationPanel.mention_insertion_point, panel)
        panel.replace_mention_query = MethodType(ConversationPanel.replace_mention_query, panel)
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "owner@example.test"
        window.conversation = panel
        window.group_participants_by_chat = {room: {jid: GroupParticipant(room, jid, raw)}}
        window._display_name_for_jid = Mock(return_value="member")
        window._fallback_display_name_for_jid = Mock(return_value="member")
        window.audio_recorder = SimpleNamespace(is_recording=False)
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        try:
            for text in ("@Fi", "💙\nHello @Fi end", "Fixture 💙 @Fi"):
                with self.subTest(text=text):
                    start = text.index("@Fi")
                    end = start + 3
                    prefix = text[:end]
                    native_position = len(prefix)
                    if wx.Platform == "__WXMSW__":
                        native_position = len(prefix.encode("utf-16-le")) // 2 + prefix.count("\n")
                    compose.SetValue(text)
                    compose.SetInsertionPoint(native_position)
                    window._refresh_mention_suggestions()
                    panel.show_mention_suggestions.assert_called_with([human])
                    self.assertTrue(window._accept_selected_mention())
                    insert = human + ("" if end < len(text) and text[end].isspace() else " ")
                    expected = text[:start] + insert + text[end:]
                    self.assertEqual(compose.GetValue(), expected)
                    caret_prefix = compose.GetRange(0, compose.GetInsertionPoint())
                    self.assertEqual(caret_prefix, text[:start] + insert)
                    self.assertEqual(panel.mention_insertion_point(), start + len(insert))
                    self.assertEqual(
                        window._mention_references_for_message(panel.current_chat, expected),
                        [MentionReference(jid, start, start + len(human))],
                    )
        finally:
            frame.Destroy()
