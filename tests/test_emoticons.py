from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import wx

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.conversation_panel import ConversationPanel
from cliente_xmpp.ui.emoticons import (
    EmoticonTextCtrl,
    convert_standalone_emoticon,
    emoticon_before_space,
)
from cliente_xmpp.ui.main_window import MainWindow


class EmoticonTests(unittest.TestCase):
    def test_complete_tokens_and_variants(self) -> None:
        for token, emoji in (
            (":)", "🙂"), (":-)", "🙂"), (";)", "😉"), (";-)", "😉"),
            (":D", "😃"), (":-D", "😃"), ("xD", "😆"), ("XD", "😆"),
            (":(", "🙁"), (":-(", "🙁"), (":P", "😛"), (":-p", "😛"),
            (";-P", "😜"), (":o", "😮"), (":-O", "😮"), (":'(", "😢"),
            (":'-(", "😢"), (";(", "😢"), (";-(", "😢"),
            (":-*", "😘"), ("8-)", "😎"), (":-|", "😐"),
            (":-/", "😕"), ("<3", "❤️"), ("</3", "💔"),
        ):
            with self.subTest(token=token):
                self.assertEqual(emoticon_before_space(token), (token, emoji))
                self.assertEqual(emoticon_before_space("👋 Hola\n" + token), (token, emoji))
                self.assertEqual(convert_standalone_emoticon(token), emoji)

    def test_send_conversion_keeps_mixed_text_urls_code_and_actual_emojis(self) -> None:
        for text in ("", "Hola :)", ":) Hola", ":) :(", "`:)`", "```\n:)\n```",
                     "https://example.test/:)", "hola:)", "🙂", "👨‍👩‍👧‍👦"):
            with self.subTest(text=text):
                self.assertEqual(convert_standalone_emoticon(text), text)

    def test_literals_partial_tokens_urls_and_code_are_unchanged(self) -> None:
        for before, after in (
            ("", ""), ("   ", ""), (":) ", ""), ("hola:)", ""),
            ("https://example.test/:)", ""), ("correo:D", ""), (":))", ""),
            (":)", "palabra"), ("`codigo :)", "`"), ("` :)", ""),
            ("```python\n:)", ""), ("``codigo ` :)", ""), ("\\:)", ""),
        ):
            with self.subTest(before=before, after=after):
                self.assertIsNone(emoticon_before_space(before, after))
        self.assertEqual(emoticon_before_space("`literal` :)"), (":)", "🙂"))
        self.assertEqual(emoticon_before_space("```\ncode\n```\n:)"), (":)", "🙂"))


class NativeEmoticonComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self) -> None:
        self.frame = wx.Frame(None)
        self.compose = EmoticonTextCtrl(self.frame, style=wx.TE_MULTILINE)
        self.addCleanup(self.frame.Destroy)

    @staticmethod
    def event(key=" ", *, control=False, alt=False, meta=False):
        return SimpleNamespace(
            GetUnicodeKey=lambda: ord(key), ControlDown=lambda: control,
            AltDown=lambda: alt, MetaDown=lambda: meta, Skip=Mock(),
        )

    def test_native_unicode_caret_suffix_and_single_text_event(self) -> None:
        self.compose.ChangeValue("👋\nHola :) resto")
        end = self.compose.GetLastPosition() - len(" resto")
        self.compose.SetInsertionPoint(end)
        changes = []
        self.compose.Bind(wx.EVT_TEXT, lambda _: changes.append(self.compose.GetValue()))
        event = self.event()
        self.compose._on_char(event)
        self.assertEqual(self.compose.GetValue(), "👋\nHola 🙂  resto")
        self.assertEqual(self.compose.GetRange(0, self.compose.GetInsertionPoint()), "👋\nHola 🙂 ")
        self.assertEqual(self.compose.GetSelection(), (self.compose.GetInsertionPoint(),) * 2)
        self.assertEqual(changes, ["👋\nHola 🙂  resto"])
        event.Skip.assert_not_called()

    def test_conversion_is_native_undoable(self) -> None:
        self.compose.ChangeValue("Hola <3")
        self.compose.SetInsertionPointEnd()
        self.compose._on_char(self.event())
        self.assertEqual(self.compose.GetValue(), "Hola ❤️ ")
        self.assertTrue(self.compose.CanUndo())
        self.compose.Undo()
        self.assertEqual(self.compose.GetValue(), "Hola <3")

        for token in (":'(", ";(", ";-("):
            with self.subTest(token=token):
                self.compose.ChangeValue("Hola " + token)
                self.compose.SetInsertionPointEnd()
                self.compose._on_char(self.event())
                self.assertEqual(self.compose.GetValue(), "Hola 😢 ")
                self.compose.Undo()
                self.assertEqual(self.compose.GetValue(), "Hola " + token)

    def test_standalone_send_normal_reply_and_edit_use_the_converted_body(self) -> None:
        for mode, text, expected in (
            ("normal", ":)", "🙂"), ("reply", ";(", "😢"), ("edit", ":'(", "😢"),
            ("normal", "\n :'( \n", "😢"), ("normal", "Hola :)", "Hola :)"),
        ):
            with self.subTest(mode=mode, text=text):
                self.compose.ChangeValue(text)
                window = MainWindow.__new__(MainWindow)
                chat = Chat("fixture@example.test", "Fixture")
                target = Message(chat_jid=chat.jid, sender_jid=chat.jid, body="Fixture",
                                 message_id="fixture-native-id")
                window.reply_context = target if mode == "reply" else None
                window.edit_context = target if mode == "edit" else None
                window.current_jid = "me@example.test"
                conversation = SimpleNamespace(
                    compose=self.compose, current_chat=chat,
                    has_reply_context=lambda mode=mode: mode == "reply",
                    hide_mention_suggestions=Mock(),
                    update_send_button_state=Mock(),
                )
                conversation.consume_composed_message = lambda conversation=conversation: (
                    ConversationPanel.consume_composed_message(conversation)
                )
                window.conversation = conversation
                window.status_bar = SimpleNamespace(SetStatusText=Mock())
                window.xmpp = SimpleNamespace(send_message=Mock(), send_reply=Mock())
                window._require_whatsapp_connection = lambda: True
                window._mention_references_for_message = lambda _chat, _body: []
                pending = []
                window._add_pending_outgoing_message = pending.append
                window._mark_current_chat_displayed = Mock()
                window._cancel_reply = Mock()
                window._send_message_correction = Mock()
                MainWindow._on_send_message(window, None)
                if mode == "edit":
                    window._send_message_correction.assert_called_once_with(target, expected)
                    window.xmpp.send_message.assert_not_called()
                else:
                    send = window.xmpp.send_reply if mode == "reply" else window.xmpp.send_message
                    self.assertEqual(send.call_args.args[1], expected)
                    self.assertEqual(pending[0].body, expected)
                self.assertEqual(self.compose.GetValue(), "")

    def test_paste_selection_modifiers_and_other_keys_stay_native(self) -> None:
        self.compose.WriteText(":) ")
        self.assertEqual(self.compose.GetValue(), ":) ")
        for kwargs in ({"control": True}, {"alt": True}, {"meta": True}, {"key": "\n"}):
            with self.subTest(kwargs=kwargs):
                self.compose.ChangeValue(":)")
                self.compose.SetInsertionPointEnd()
                event = self.event(**kwargs)
                self.compose._on_char(event)
                self.assertEqual(self.compose.GetValue(), ":)")
                event.Skip.assert_called_once()
        self.compose.SetSelection(0, 2)
        event = self.event()
        self.compose._on_char(event)
        self.assertEqual(self.compose.GetSelection(), (0, 2))
        event.Skip.assert_called_once()
        self.compose.SetInsertionPointEnd()
        self.compose.SetEditable(False)
        event = self.event()
        self.compose._on_char(event)
        event.Skip.assert_called_once()

    def test_long_multiline_draft_preserves_code_and_earlier_emoticons(self) -> None:
        prefix = "Texto ficticio :)\n" * 5000
        self.compose.ChangeValue(prefix + "```\n:)")
        self.compose.SetInsertionPointEnd()
        event = self.event()
        self.compose._on_char(event)
        self.assertEqual(self.compose.GetValue(), prefix + "```\n:)")
        event.Skip.assert_called_once()
        self.compose.ChangeValue(prefix + ":)")
        self.compose.SetInsertionPointEnd()
        self.compose._on_char(self.event())
        self.assertEqual(self.compose.GetValue(), prefix + "🙂 ")
        for before in ("`literal` :)", "```\ncode\n```\n:)"):
            with self.subTest(before=before):
                self.compose.ChangeValue(before)
                self.compose.SetInsertionPointEnd()
                self.compose._on_char(self.event())
                self.assertEqual(self.compose.GetValue(), before[:-2] + "🙂 ")
