from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.conversation_panel import DATE_SEPARATOR_PREFIX, ConversationPanel
from cliente_xmpp.ui.main_window import (
    MANUAL_HISTORY_BATCH_DELAY_MS,
    SEARCH_MEMORY_MESSAGE_LIMIT,
    SEARCH_RESULT_LIMIT,
    MainWindow,
    ManualHistoryLoad,
)


def make_messages(count: int, *, same_time: bool = False) -> list[Message]:
    start = datetime(2026, 1, 1, 12, tzinfo=UTC)
    return [
        Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            message_id=f"message-{index}",
            body=f"Texto de prueba {index}",
            sent_at=start if same_time else start + timedelta(seconds=index),
        )
        for index in range(count)
    ]


class ManualHistoryLoadingTests(unittest.TestCase):
    def make_window(self) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "me@example.test"
        window.whatsapp_verified = True
        window._closing = False
        window.history_loading_chats = set()
        window.local_history_loading_chats = set()
        window.local_history_exhausted_chats = set()
        window.history_exhausted_chats = set()
        window.history_loaded_chats = set()
        window.preloaded_history_chats = set()
        window.local_history_before_by_chat = {}
        window.local_history_cursor_by_chat = {}
        window.messages_by_chat = {"chat@example.test": []}
        window.manual_history_load = None
        window.conversation = SimpleNamespace(
            current_chat=Chat(jid="chat@example.test", name="Chat de prueba"),
            IsShown=lambda: True,
            unread_marker_count=lambda: 0,
            refresh_message=Mock(),
            load_older_button=Mock(),
        )
        window.status_bar = SimpleNamespace(SetStatusText=Mock())
        window.speaker = SimpleNamespace(speak=Mock())
        window._load_conversation = Mock()
        window._normalize_audio_metadata_for_messages = Mock()
        window._flush_pending_reaction_updates = Mock()
        window._persist_messages = Mock()
        window._chat_has_preview = Mock(return_value=False)
        window._update_chat_activity_from_messages = Mock()
        window._update_chat_preview_from_messages = Mock()
        window._auto_download_media_messages = Mock()
        window._apply_synced_chat_displayed = Mock()
        window._refresh_chat_order = Mock()
        window._mark_current_chat_displayed = Mock()
        window.xmpp = SimpleNamespace(load_history=Mock())
        return window

    def test_amount_accepts_positive_integers_and_all_without_an_artificial_cap(self) -> None:
        self.assertEqual(MainWindow._parse_history_amount(" 100 "), 100)
        self.assertEqual(MainWindow._parse_history_amount("1000000"), 1000000)
        self.assertIsNone(MainWindow._parse_history_amount(" TODOS "))
        for value in ("", "0", "-1", "1.5", "abc", "1e3", "+1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MainWindow._parse_history_amount(value)

    def test_dialog_defaults_to_one_hundred_and_cancel_does_not_load(self) -> None:
        window = self.make_window()
        window._require_whatsapp_connection = Mock(return_value=True)
        window._continue_manual_history_load = Mock()
        dialog = Mock()
        dialog.ShowModal.return_value = wx.ID_CANCEL
        with patch(
            "cliente_xmpp.ui.main_window.wx.TextEntryDialog", return_value=dialog
        ) as factory:
            window._on_load_older_messages(Mock())
        self.assertEqual(factory.call_args.args[-1], "100")
        window._continue_manual_history_load.assert_not_called()
        dialog.Destroy.assert_called_once_with()

    def test_dialog_rejects_invalid_amount_before_starting(self) -> None:
        window = self.make_window()
        window._require_whatsapp_connection = Mock(return_value=True)
        window._continue_manual_history_load = Mock()
        dialog = Mock()
        dialog.ShowModal.side_effect = [wx.ID_OK, wx.ID_OK]
        dialog.GetValue.side_effect = ["0", "650"]
        with (
            patch("cliente_xmpp.ui.main_window.wx.TextEntryDialog", return_value=dialog),
            patch("cliente_xmpp.ui.main_window.wx.MessageBox") as warning,
        ):
            window._on_load_older_messages(Mock())
        warning.assert_called_once()
        self.assertEqual(window.manual_history_load.remaining, 650)
        window._continue_manual_history_load.assert_called_once_with(window.manual_history_load)

    def run_cached_load(self, requested: int | None, *, same_time: bool = False) -> MainWindow:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = self.make_window()
            window.message_store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            messages = make_messages(1200, same_time=same_time)
            window.message_store.upsert_messages(window.current_jid, messages)
            recent = window.message_store.load_recent_messages(
                window.current_jid, messages[0].chat_jid, limit=500
            )
            chat_jid = messages[0].chat_jid
            window.messages_by_chat[chat_jid] = recent
            window.local_history_before_by_chat[chat_jid] = recent[0].sent_at
            window.local_history_cursor_by_chat[chat_jid] = recent[0]
            load = ManualHistoryLoad(window.current_jid, chat_jid, requested)
            window.manual_history_load = load
            queued: list[tuple[object, tuple[object, ...]]] = []
            page_sizes: list[int] = []
            original_query = window.message_store.load_messages_before

            def query(*args: object, **kwargs: object) -> list[Message]:
                page_sizes.append(kwargs["limit"])
                return original_query(*args, **kwargs)

            def later(delay: int, callback: object, *args: object) -> None:
                if delay == MANUAL_HISTORY_BATCH_DELAY_MS:
                    queued.append((callback, args))

            def thread(*, target: object, **_kwargs: object) -> object:
                return SimpleNamespace(start=lambda: queued.append((target, ())))

            with (
                patch.object(window.message_store, "load_messages_before", side_effect=query),
                patch("cliente_xmpp.ui.main_window.threading.Thread", side_effect=thread),
                patch(
                    "cliente_xmpp.ui.main_window.wx.CallAfter",
                    side_effect=lambda callback, *args: queued.append((callback, args)),
                ),
                patch("cliente_xmpp.ui.main_window.wx.CallLater", side_effect=later),
            ):
                window._continue_manual_history_load(load)
                self.assertEqual(page_sizes, [], "SQLite must be queried by the worker")
                for _ in range(100):
                    if not queued:
                        break
                    callback, args = queued.pop(0)
                    callback(*args)
                self.assertEqual(queued, [], "The bounded load must not loop forever")
            self.assertTrue(page_sizes)
            self.assertLessEqual(max(page_sizes), 100)
            if requested is not None:
                self.assertEqual(load.loaded, requested)
                self.assertEqual(len(window.messages_by_chat[chat_jid]), 500 + requested)
                self.assertIsNone(window.manual_history_load)
                window.xmpp.load_history.assert_not_called()
                self.assertEqual(page_sizes[-1], requested % 100 or 100)
            else:
                self.assertEqual(load.loaded, 700)
                self.assertEqual(len(window.messages_by_chat[chat_jid]), 1200)
                window.xmpp.load_history.assert_called_once()
                window._handle_message_history_loaded(chat_jid, [], older=True, complete=True)
                self.assertIsNone(window.manual_history_load)
            self.assertTrue(
                all(
                    call.kwargs["incremental_history"]
                    for call in window._load_conversation.call_args_list
                )
            )
            return window

    def test_large_numeric_load_is_sequential_and_honors_the_last_partial_page(self) -> None:
        self.run_cached_load(650)

    def test_all_drains_local_history_before_requesting_remote_and_stops_on_empty(self) -> None:
        self.run_cached_load(None)

    def test_all_preserves_messages_sharing_the_local_page_boundary_timestamp(self) -> None:
        self.run_cached_load(None, same_time=True)

    def test_cancellation_prevents_queued_continuation(self) -> None:
        window = self.make_window()
        load = ManualHistoryLoad(window.current_jid, "chat@example.test", None)
        window.manual_history_load = load
        window._request_older_history_page = Mock()
        window._on_load_older_messages(Mock())
        window._continue_manual_history_load(load)
        window._request_older_history_page.assert_not_called()
        self.assertIsNone(window.manual_history_load)

    def test_cancelled_local_page_cannot_start_remote_or_continue(self) -> None:
        window = self.make_window()
        load = ManualHistoryLoad(window.current_jid, "chat@example.test", None, source="local")
        window.local_history_loading_chats.add(load.chat_jid)
        with patch("cliente_xmpp.ui.main_window.wx.CallLater") as later:
            window._finish_loading_local_history_page(
                window.current_jid, load.chat_jid, [], "", load
            )
        later.assert_not_called()
        window.xmpp.load_history.assert_not_called()

    def test_changed_account_chat_hidden_view_and_closing_stop_continuations(self) -> None:
        for condition in ("account", "chat", "hidden", "closing", "maintenance", "reset"):
            with self.subTest(condition=condition):
                window = self.make_window()
                load = ManualHistoryLoad(window.current_jid, "chat@example.test", None)
                window.manual_history_load = load
                window._request_older_history_page = Mock()
                if condition == "account":
                    window.current_jid = "other@example.test"
                elif condition == "chat":
                    window.conversation.current_chat = Chat(jid="other@example.test", name="Otro")
                elif condition == "hidden":
                    window.conversation.IsShown = lambda: False
                elif condition == "closing":
                    window._closing = True
                elif condition == "maintenance":
                    window._storage_maintenance_in_progress = True
                else:
                    window._storage_reset_in_progress = True
                window._continue_manual_history_load(load)
                self.assertIsNone(window.manual_history_load)
                window._request_older_history_page.assert_not_called()

    def test_remote_no_progress_and_errors_stop_instead_of_looping(self) -> None:
        for error in ("", "No se pudo consultar el historial local."):
            with self.subTest(error=error):
                window = self.make_window()
                message = make_messages(1)[0]
                window.messages_by_chat[message.chat_jid] = [message]
                load = ManualHistoryLoad(
                    window.current_jid,
                    message.chat_jid,
                    None,
                    source="remote",
                    before=message.sent_at,
                )
                window.manual_history_load = load
                with patch("cliente_xmpp.ui.main_window.wx.CallLater") as later:
                    window._advance_manual_history_load(load, error=error)
                self.assertIsNone(window.manual_history_load)
                later.assert_not_called()

    def test_remote_fallback_cannot_exceed_the_requested_page(self) -> None:
        window = self.make_window()
        messages = make_messages(201)
        chat_jid = messages[0].chat_jid
        window.messages_by_chat[chat_jid] = [messages[-1]]
        load = ManualHistoryLoad(
            window.current_jid,
            chat_jid,
            25,
            source="remote",
            before=messages[-1].sent_at,
            page_size=25,
        )
        window.manual_history_load = load
        window._handle_message_history_loaded(chat_jid, messages[:-1], older=True, complete=False)
        self.assertEqual(load.loaded, 25)
        self.assertEqual(len(window.messages_by_chat[chat_jid]), 26)
        self.assertIsNone(window.manual_history_load)

    def test_bulk_history_does_not_fan_out_downloads_or_audio_probes(self) -> None:
        for amount, bulk in ((100, False), (499, False), (500, True), (None, True)):
            with self.subTest(amount=amount):
                window = self.make_window()
                messages = make_messages(2)
                chat_jid = messages[0].chat_jid
                window.messages_by_chat[chat_jid] = [messages[1]]
                load = ManualHistoryLoad(
                    window.current_jid,
                    chat_jid,
                    amount,
                    source="remote",
                    before=messages[1].sent_at,
                )
                window.manual_history_load = load
                with patch("cliente_xmpp.ui.main_window.wx.CallLater"):
                    window._handle_message_history_loaded(
                        chat_jid, [messages[0]], older=True, complete=False
                    )
                if bulk:
                    window._auto_download_media_messages.assert_not_called()
                    window._normalize_audio_metadata_for_messages.assert_not_called()
                else:
                    window._auto_download_media_messages.assert_called_once_with([messages[0]])
                    window._normalize_audio_metadata_for_messages.assert_called_once_with(
                        [messages[0]]
                    )

    def test_home_does_not_start_an_extra_page_during_a_manual_load(self) -> None:
        window = self.make_window()
        window.manual_history_load = ManualHistoryLoad(
            window.current_jid, "chat@example.test", None
        )
        window._request_older_history_page("chat@example.test")
        window.xmpp.load_history.assert_not_called()

    def test_search_limit_is_five_hundred_without_enlarging_python_memory_scan(self) -> None:
        self.assertEqual(SEARCH_RESULT_LIMIT, 500)
        self.assertEqual(SEARCH_MEMORY_MESSAGE_LIMIT, 200)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            store.upsert_messages("me@example.test", make_messages(550))
            results = store.search_messages(
                "me@example.test", "Texto", chat_jid="chat@example.test"
            )
        self.assertEqual(len(results), 500)
        self.assertEqual(results[0].message_id, "message-50")


class _HistoryList:
    def __init__(self, rows: list[Message | str], focus: int) -> None:
        self.rows = list(rows)
        self.selected = focus
        self.focused = focus
        self.scroll = 0
        self.insertions = 0
        self.freeze_count = 0
        self.thaw_count = 0

    def GetTopItem(self) -> int:
        return 0

    def GetItemRect(self, index: int) -> object:
        return SimpleNamespace(y=index * 20 - self.scroll)

    def ScrollList(self, _dx: int, dy: int) -> None:
        self.scroll += dy

    def GetItemState(self, index: int, mask: int) -> int:
        state = (wx.LIST_STATE_SELECTED if self.selected == index else 0) | (
            wx.LIST_STATE_FOCUSED if self.focused == index else 0
        )
        return state & mask

    def SetItemState(self, index: int, state: int, _mask: int) -> None:
        if state & wx.LIST_STATE_SELECTED:
            self.selected = index
        if state & wx.LIST_STATE_FOCUSED:
            self.focused = index

    def Freeze(self) -> None:
        self.freeze_count += 1

    def Thaw(self) -> None:
        self.thaw_count += 1

    def DeleteItem(self, index: int) -> None:
        del self.rows[index]
        self.selected -= int(self.selected > index)
        self.focused -= int(self.focused > index)

    def InsertItem(self, index: int, text: str, _image: int) -> None:
        self.rows.insert(index, text)
        self.insertions += 1
        self.selected += int(self.selected >= index)
        self.focused += int(self.focused >= index)

    def SetItemTextColour(self, _index: int, _colour: object) -> None:
        pass

    def SetItemBackgroundColour(self, _index: int, _colour: object) -> None:
        pass


class HistoryPrependTests(unittest.TestCase):
    def make_panel(self) -> tuple[ConversationPanel, list[Message]]:
        messages = make_messages(5100)
        recent = messages[100:]
        panel = ConversationPanel.__new__(ConversationPanel)
        panel._messages = recent
        date_key = panel._message_local_datetime(recent[0]).date().isoformat()
        panel._message_rows = [f"{DATE_SEPARATOR_PREFIX}{date_key}", *recent]
        panel._message_row_indexes = {
            id(message): index + 1 for index, message in enumerate(recent)
        }
        panel._unread_marker_count = 0
        panel._unread_marker_index = None
        panel._focus_target_index = 251
        panel._focused_message_row_index = 251
        panel._current_audio_row_index = 251
        panel._pending_audio_row_index = None
        panel._selected_message_keys = {panel._message_focus_key(recent[250])}
        panel.messages = _HistoryList(panel._message_rows, focus=251)
        panel._format_message_row_for_list = lambda _index, message: message.message_id
        panel._thumbnail_index_for_message = Mock(return_value=-1)
        panel._style_message_item = Mock()
        panel._update_message_action_buttons = Mock()
        return panel, messages

    def test_large_prepend_inserts_only_new_rows_and_keeps_focus_selection_and_scroll(self) -> None:
        panel, messages = self.make_panel()
        old_key = panel._selected_message_keys.copy()
        old_focused_message = panel._message_rows[251]
        self.assertTrue(panel.prepend_history_messages(messages))
        self.assertEqual(panel.messages.insertions, 101)
        self.assertEqual(len(panel._message_rows), 5101)
        self.assertIs(panel._message_rows[351], old_focused_message)
        self.assertEqual(panel.messages.focused, 351)
        self.assertEqual(panel.messages.selected, 351)
        self.assertEqual(panel._focus_target_index, 351)
        self.assertEqual(panel._current_audio_row_index, 351)
        self.assertEqual(panel._selected_message_keys, old_key)
        self.assertEqual(panel.messages.scroll, 2000)
        self.assertEqual(panel._message_row_indexes[id(old_focused_message)], 351)
        self.assertEqual(panel.messages.freeze_count, panel.messages.thaw_count)

    def test_reordering_is_not_mistaken_for_a_pure_prepend(self) -> None:
        panel, messages = self.make_panel()
        messages[-2], messages[-1] = messages[-1], messages[-2]
        self.assertFalse(panel.prepend_history_messages(messages))
        self.assertEqual(panel.messages.insertions, 0)

    def test_thaw_is_guaranteed_even_when_a_native_insertion_fails(self) -> None:
        panel, messages = self.make_panel()
        with patch.object(panel.messages, "InsertItem", side_effect=RuntimeError("test error")):
            with self.assertRaises(RuntimeError):
                panel.prepend_history_messages(messages)
        self.assertEqual(panel.messages.freeze_count, 1)
        self.assertEqual(panel.messages.thaw_count, 1)

    def test_prepend_keeps_a_focused_date_separator_when_merging_same_day(self) -> None:
        panel, messages = self.make_panel()
        panel.messages.focused = panel.messages.selected = 0
        panel._focus_target_index = panel._focused_message_row_index = 0
        self.assertTrue(panel.prepend_history_messages(messages))
        self.assertEqual(panel.messages.focused, 0)
        self.assertEqual(panel.messages.selected, 0)
        self.assertEqual(panel._focus_target_index, 0)

    def test_prepend_keeps_native_windows_list_selection_without_showing_a_window(self) -> None:
        app = wx.App.Get() or wx.App(False)
        frame = wx.Frame(None, size=(800, 500))
        try:
            panel, messages = self.make_panel()
            native_list = wx.ListCtrl(frame, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
            native_list.InsertColumn(0, "Mensaje", width=700)
            native_list.SetSize((780, 450))
            for index, row in enumerate(panel._message_rows):
                text = row.message_id if isinstance(row, Message) else row
                native_list.InsertItem(index, text)
            native_list.SetItemState(
                251,
                wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
                wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            )
            panel.messages = native_list
            self.assertTrue(panel.prepend_history_messages(messages))
            self.assertEqual(native_list.GetFirstSelected(), 351)
            self.assertEqual(native_list.GetNextItem(-1, state=wx.LIST_STATE_FOCUSED), 351)
            self.assertEqual(native_list.GetItemText(351), messages[350].message_id)
            self.assertFalse(native_list.IsFrozen())
            self.assertFalse(frame.IsShown())
        finally:
            frame.Destroy()
            app.ProcessPendingEvents()


if __name__ == "__main__":
    unittest.main()
