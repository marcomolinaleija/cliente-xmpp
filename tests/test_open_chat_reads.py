from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.events import MessageReceived


class OpenChatReadTests(unittest.TestCase):
    def window(self, group: bool = False):
        chat = Chat(jid="chat@example.test", name="Fixture", is_group=group, unread_count=3)
        window = SimpleNamespace(
            whatsapp_verified=True,
            loading_initial_chat_activity=False,
            messages_by_chat={chat.jid: []},
            displayed_marker_ids_by_chat={},
            conversation=SimpleNamespace(
                current_chat=chat, IsShown=Mock(return_value=True),
                insert_message_sorted=Mock(), refresh_message=Mock(),
            ),
            chat_list=SimpleNamespace(IsShown=lambda: False),
            xmpp=SimpleNamespace(mark_chat_displayed=Mock()),
            _message_timestamp=MainWindow._message_timestamp,
            _is_locally_deleted_message=Mock(return_value=False),
            _cleared_chat_blocks_timestamp=Mock(return_value=False),
            _set_chat_state=Mock(), _ensure_chat_for_message=Mock(),
            _observe_atajos_message=Mock(), _refresh_chat_order=Mock(),
            _auto_download_media_message=Mock(), _select_first_chat_if_needed=Mock(),
            _show_windows_notification=Mock(return_value=False),
            _speak_incoming_message=Mock(), _play_incoming_message_sound=Mock(),
        )

        def store(message):
            items = window.messages_by_chat.setdefault(message.chat_jid, [])
            added = not any(m.message_id == message.message_id for m in items)
            if added:
                items.append(message)
            return message, added, []

        def summary(_jid, *, mark_read=False):
            if mark_read:
                chat.unread_count = 0

        def update(message, *, mark_unread):
            if mark_unread and message.chat_jid == chat.jid:
                chat.unread_count += 1

        window._store_message = store
        window._update_chat_summary = Mock(side_effect=summary)
        window._update_chat_from_message = Mock(side_effect=update)
        window._mark_chat_displayed = MethodType(MainWindow._mark_chat_displayed, window)
        window._mark_current_chat_displayed = MethodType(
            MainWindow._mark_current_chat_displayed, window,
        )
        return window, chat

    def message(self, chat, number, **kwargs):
        return Message(
            chat_jid=chat.jid, sender_jid="member@example.test", body="Fixture",
            message_id=f"incoming-{number}",
            displayed_marker_id=f"room-{number}" if chat.is_group else "",
            chat_is_group=chat.is_group,
            sent_at=datetime(2026, 1, 1).astimezone() + timedelta(seconds=number),
            **kwargs,
        )

    def test_open_read_horizon_advances_for_each_new_message_and_stops_when_closed(self):
        for group in (False, True):
            with self.subTest(group=group):
                window, chat = self.window(group)
                window.messages_by_chat[chat.jid] = [self.message(chat, i) for i in range(3)]
                # Opening uses this same helper after showing the conversation.
                window._mark_current_chat_displayed(chat.jid)
                self.assertEqual(chat.unread_count, 0)
                for i in (3, 4):
                    MainWindow._handle_xmpp_event(window, MessageReceived(self.message(chat, i)))
                expected = [
                    ((chat.jid, f"room-{i}" if group else f"incoming-{i}"), {"is_group": group})
                    for i in (2, 3, 4)
                ]
                self.assertEqual(window.xmpp.mark_chat_displayed.call_args_list, expected)
                self.assertEqual(chat.unread_count, 0)
                # A duplicate delivery does not resend the same read marker.
                MainWindow._handle_xmpp_event(window, MessageReceived(self.message(chat, 4)))
                self.assertEqual(window.xmpp.mark_chat_displayed.call_count, 3)
                window.conversation.IsShown.return_value = False
                MainWindow._handle_xmpp_event(window, MessageReceived(self.message(chat, 5)))
                self.assertEqual(chat.unread_count, 1)
                self.assertEqual(window.xmpp.mark_chat_displayed.call_count, 3)

    def test_history_own_messages_and_other_chats_do_not_trigger_open_chat_read(self):
        for case in ("history", "own", "other", "hidden", "unverified"):
            with self.subTest(case=case):
                window, chat = self.window(group=True)
                if case == "hidden":
                    window.conversation.IsShown.return_value = False
                if case == "unverified":
                    window.whatsapp_verified = False
                source = Chat(jid="other@example.test", name="Other") if case == "other" else chat
                message = self.message(source, 4, outgoing=case == "own")
                MainWindow._handle_xmpp_event(
                    window, MessageReceived(message, notify=case != "history"),
                )
                window.xmpp.mark_chat_displayed.assert_not_called()
                window._update_chat_summary.assert_not_called()

    def test_wrong_current_chat_does_not_clear_unread(self):
        window, chat = self.window()
        window._mark_current_chat_displayed("other@example.test")
        self.assertEqual(chat.unread_count, 3)
        window._update_chat_summary.assert_not_called()

    def test_phone_own_text_and_audio_do_not_increase_closed_chat_unread(self):
        for group in (False, True):
            for media in (False, True):
                with self.subTest(group=group, audio=media):
                    window, chat = self.window(group)
                    window.conversation.IsShown.return_value = False
                    message = self.message(
                        chat, 4, outgoing=True,
                        media_kind="audio" if media else "",
                        audio_url="https://example.test/fixture.ogg" if media else "",
                    )
                    MainWindow._handle_xmpp_event(window, MessageReceived(message))
                    self.assertEqual(chat.unread_count, 3)
                    self.assertFalse(window._update_chat_from_message.call_args.kwargs["mark_unread"])
                    window.xmpp.mark_chat_displayed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
