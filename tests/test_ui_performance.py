from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.conversation_panel import (
    MESSAGE_ROW_TEXT_LIMIT,
    ConversationPanel,
)
from cliente_xmpp.ui.main_window import MainWindow


class _CapturingExecutor:
    def __init__(self) -> None:
        self.pending: tuple[object, tuple[object, ...]] | None = None

    def submit(self, callback: object, *args: object) -> None:
        self.pending = callback, args

    def run(self) -> None:
        assert self.pending is not None
        callback, args = self.pending
        callback(*args)


class ConversationPerformanceTests(unittest.TestCase):
    def test_long_list_row_is_bounded_but_reader_keeps_full_body(self) -> None:
        body = "contenido " * 1000
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body=body,
        )
        panel = ConversationPanel.__new__(ConversationPanel)
        panel.resolve_display_name = lambda _jid: "Contacto"
        panel._audio_durations_by_url = {}

        row = panel._format_message_row(message)
        reader = panel._format_message_for_reader(message)

        self.assertLess(len(row), MESSAGE_ROW_TEXT_LIMIT + 100)
        self.assertIn("...", row)
        self.assertIn(body, reader)


class ChatListPerformanceTests(unittest.TestCase):
    def test_chat_index_supports_constant_time_lookup(self) -> None:
        panel = ChatListPanel.__new__(ChatListPanel)
        panel._chats = [
            Chat(jid="one@example.test", name="Uno"),
            Chat(jid="two@example.test", name="Dos"),
        ]

        panel._rebuild_chat_indexes()

        self.assertTrue(panel.has_chat("two@example.test"))
        self.assertEqual(panel.chat_by_jid("two@example.test").name, "Dos")


class MainWindowPerformanceTests(unittest.TestCase):
    def test_empty_roster_contacts_stay_out_of_the_visible_chat_list(self) -> None:
        empty_contact = Chat(jid="empty@example.test", name="Sin mensajes")
        preview_chat = Chat(
            jid="preview@example.test",
            name="Con preview",
            last_message_preview="hola",
        )
        dated_chat = Chat(
            jid="dated@example.test",
            name="Con fecha",
            last_message_at=Message(chat_jid="x", sender_jid="x", body="").sent_at,
        )
        unread_chat = Chat(
            jid="unread@example.test",
            name="No leido",
            unread_count=1,
        )

        visible = MainWindow._chats_with_activity(
            [empty_contact, preview_chat, dated_chat, unread_chat]
        )

        self.assertEqual(
            [chat.jid for chat in visible],
            [preview_chat.jid, dated_chat.jid, unread_chat.jid],
        )

    def test_cached_messages_are_loaded_only_once_per_account_and_chat(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "me@example.test"
        window.cached_message_loads = {(window.current_jid, "chat@example.test")}
        window.message_store = SimpleNamespace(
            load_recent_messages=lambda *_args, **_kwargs: self.fail("lectura repetida")
        )

        window._load_cached_messages_for_chat("chat@example.test")

    def test_async_message_persistence_uses_an_immutable_snapshot(self) -> None:
        stored: list[Message] = []
        executor = _CapturingExecutor()
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "me@example.test"
        window.storage_executor = executor
        window.message_store = SimpleNamespace(
            upsert_messages=lambda _account, messages: stored.extend(messages)
        )
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="original",
        )

        window._persist_messages([message])
        message.body = "modificado después de encolar"
        executor.run()

        self.assertEqual(stored[0].body, "original")

    def test_audio_probe_is_scheduled_outside_the_ui_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "audio.ogg"
            path.write_bytes(b"audio")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="contact@example.test",
                body="",
                media_kind="audio",
                media_local_path=str(path),
            )
            window = MainWindow.__new__(MainWindow)
            window.audio_metadata_in_progress = set()

            with (
                patch("cliente_xmpp.ui.main_window.media_duration_seconds") as probe,
                patch("cliente_xmpp.ui.main_window.threading.Thread") as thread,
            ):
                window._normalize_audio_metadata_for_messages([message])

            probe.assert_not_called()
            thread.return_value.start.assert_called_once_with()

    def test_individual_message_merge_skips_group_echo_scan(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="nuevo",
        )
        candidates = [
            Message(
                chat_jid=message.chat_jid,
                sender_jid=message.sender_jid,
                body=f"anterior {index}",
            )
            for index in range(100)
        ]

        with patch.object(
            MainWindow,
            "_messages_are_group_self_echo",
        ) as compare:
            result = MainWindow._matching_group_self_echo_index(message, candidates)

        self.assertIsNone(result)
        compare.assert_not_called()

    def test_performance_logging_is_disabled_by_default(self) -> None:
        with (
            patch("cliente_xmpp.ui.main_window.PERF_DEBUG_ENABLED", False),
            patch("builtins.print") as output,
        ):
            MainWindow._debug_perf("operación", 0.0, rows=100)

        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
