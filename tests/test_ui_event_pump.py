from __future__ import annotations

import threading
import unittest
from collections import deque
from dataclasses import replace
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.ui.chat_list_panel import ChatListPanel
from cliente_xmpp.ui.event_pump import UiEventPump
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.events import MessageReceived, WhatsAppLinkState


class UiEventPumpTests(unittest.TestCase):
    def test_burst_is_fifo_lossless_and_yields_between_bounded_turns(self):
        received, scheduled = [], deque()
        wake = Mock()
        pump = UiEventPump(received.append, wake, lambda: scheduled.append(pump.drain),
                           max_events=25, clock=lambda: 0)
        for index in range(5000):
            pump.submit(index)
        wake.assert_called_once_with()
        pump.drain()
        self.assertEqual(received, list(range(25)))
        turns = 1
        while scheduled:
            scheduled.popleft()()
            turns += 1
        self.assertEqual(turns, 200)
        self.assertEqual(received, list(range(5000)))
        pump.submit(5000)
        self.assertEqual(wake.call_count, 2)

    def test_clock_budget_and_reentrant_producer_keep_remaining_events(self):
        clock, received, scheduled = [0.0], [], []

        def handle(value):
            received.append(value)
            clock[0] += 0.003
            if value == 0:
                pump.submit(5)

        pump = UiEventPump(handle, Mock(), lambda: scheduled.append(True),
                           clock=lambda: clock[0])
        for value in range(5):
            pump.submit(value)
        pump.drain()
        self.assertEqual(received, [0, 1, 2])
        self.assertEqual(len(scheduled), 1)
        pump.drain()
        self.assertEqual(received, list(range(6)))

    def test_concurrent_producers_are_delivered_once_in_each_producers_order(self):
        received = []
        pump = UiEventPump(received.append, Mock(), Mock(), max_events=1000,
                           clock=lambda: 0)

        def produce(producer):
            for index in range(100):
                pump.submit((producer, index))

        threads = [threading.Thread(target=produce, args=(p,)) for p in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        pump.drain()
        self.assertEqual(len(set(received)), 400)
        for producer in range(4):
            self.assertEqual([i for p, i in received if p == producer], list(range(100)))

    def test_handler_exception_reschedules_rest_and_shutdown_stops_callbacks(self):
        received, resume = [], Mock()

        def handle(value):
            if value == 0:
                raise RuntimeError("fictional handler failure")
            received.append(value)

        pump = UiEventPump(handle, Mock(), resume)
        for value in range(3):
            pump.submit(value)
        with self.assertRaises(RuntimeError):
            pump.drain()
        resume.assert_called_once_with()
        pump.drain()
        self.assertEqual(received, [1, 2])
        pump.submit(3)
        pump.close()
        pump.drain()
        pump.submit(4)
        self.assertEqual(received, [1, 2])

    def test_connection_controls_bypass_backlog_for_modal_and_authentication(self):
        window = SimpleNamespace(_xmpp_event_pump=SimpleNamespace(submit=Mock()))
        message = MessageReceived(Message(chat_jid="chat@example.invalid",
                                          sender_jid="sender@example.invalid", body="fixture"),
                                  notify=False)
        control = WhatsAppLinkState("account@example.invalid", "whatsapp.example.invalid",
                                    1, "fixture", 1, "connected", True)
        with patch("cliente_xmpp.ui.main_window.wx.PostEvent") as post:
            MainWindow._post_xmpp_event(window, message)
            MainWindow._post_xmpp_event(window, control)
        window._xmpp_event_pump.submit.assert_called_once_with(message)
        self.assertIs(post.call_args.args[1].event, control)

    def test_multiple_message_refreshes_are_sorted_and_rendered_once_per_turn(self):
        selected = Chat(jid="selected@example.invalid", name="Selected")
        panel = SimpleNamespace(IsShown=lambda: True, is_searching=False,
                                selected_chat=lambda: selected, force_refresh_visible=Mock())
        window = SimpleNamespace(_processing_xmpp_batch=True, chat_list=panel,
                                 _refresh_chat_order=Mock(), _select_first_chat_if_needed=Mock())
        for _ in range(25):
            MainWindow._request_chat_list_refresh(window)
        window._refresh_chat_order.assert_not_called()
        MainWindow._flush_chat_list_refresh(window)
        MainWindow._flush_chat_list_refresh(window)
        window._refresh_chat_order.assert_called_once_with(preserve_focused_order=True)
        panel.force_refresh_visible.assert_called_once_with(selected.jid,
                                                           preserve_focused_order=True)
        window._select_first_chat_if_needed.assert_called_once_with()

    def test_batch_keeps_every_message_update_and_preserves_notification_semantics(self):
        for notify in (False, True):
            with self.subTest(notify=notify):
                stored, scheduled = [], deque()
                window = SimpleNamespace(
                    messages_by_chat={}, _is_locally_deleted_message=lambda _: False,
                    _cleared_chat_blocks_timestamp=lambda *_: False,
                    _message_timestamp=lambda _: 0, _set_chat_state=Mock(),
                    _store_message=lambda m, stored=stored: (
                        stored.append(m.message_id) or m, True, [],
                    ),
                    whatsapp_verified=True, loading_initial_chat_activity=False,
                    _ensure_chat_for_message=Mock(), _observe_atajos_message=Mock(),
                    conversation=SimpleNamespace(IsShown=lambda: False, current_chat=None),
                    _update_chat_from_message=Mock(), _auto_download_media_message=Mock(),
                    _select_first_chat_if_needed=Mock(), _refresh_chat_order=Mock(),
                    _processing_xmpp_batch=True,
                    chat_list=SimpleNamespace(IsShown=lambda: False, is_searching=False),
                    _show_windows_notification=Mock(return_value=False),
                    _speak_incoming_message=Mock(), _play_incoming_message_sound=Mock(),
                )
                pump = UiEventPump(
                    lambda e, window=window: MainWindow._handle_xmpp_event(window, e),
                    Mock(), lambda scheduled=scheduled: scheduled.append(True), clock=lambda: 0,
                )
                for index in range(100):
                    pump.submit(MessageReceived(Message(
                        chat_jid="chat@example.invalid", sender_jid="sender@example.invalid",
                        body="fixture", message_id=str(index),
                    ), notify=notify))
                while True:
                    pump.drain()
                    MainWindow._flush_chat_list_refresh(window)
                    if not scheduled:
                        break
                    scheduled.popleft()
                self.assertEqual(stored, [str(i) for i in range(100)])
                self.assertEqual(window._update_chat_from_message.call_count, 100)
                self.assertEqual(window._refresh_chat_order.call_count, 4)
                self.assertEqual(window._show_windows_notification.call_count, 100 if notify else 0)
                self.assertEqual(window._speak_incoming_message.call_count, 100 if notify else 0)


class UiEventPumpNativeTests(unittest.TestCase):
    def test_native_wx_timer_runs_before_large_backlog_finishes(self):
        app = wx.App.Get() or wx.App(False)
        frame = wx.Frame(None)
        loop = wx.GUIEventLoop()
        activation = wx.EventLoopActivator(loop)
        received, heartbeat = [], []

        def handle(value):
            received.append(value)
            if len(received) == 1200:
                loop.Exit()

        pump = UiEventPump(handle, lambda: wx.CallAfter(pump.drain),
                           lambda: wx.CallLater(1, pump.drain), max_events=20)
        timeout = wx.CallLater(5000, loop.Exit)
        try:
            for index in range(1200):
                pump.submit(index)
            wx.CallLater(1, lambda: heartbeat.append(len(received)))
            loop.Run()
            self.assertEqual(received, list(range(1200)))
            self.assertTrue(heartbeat)
            self.assertLess(heartbeat[0], 1200)
        finally:
            timeout.Stop()
            pump.close()
            frame.Destroy()
            app.Yield()
            del activation


class ListBox:
    def __init__(self):
        self.rows, self.selection, self.focused = [], -1, False
        self.frozen = 0
        self.writes = 0

    def GetSelection(self): return self.selection
    def SetSelection(self, index): self.selection = index
    def HasFocus(self): return self.focused
    def GetCount(self): return len(self.rows)
    def GetString(self, index): return self.rows[index]
    def Freeze(self): self.frozen += 1
    def Thaw(self): self.frozen -= 1
    def Set(self, rows):
        self.rows = list(rows)
        self.writes += 1

    def SetString(self, index, row):
        self.rows[index] = row
        self.writes += 1

    def Insert(self, row, index): self.rows.insert(index, row)
    def Delete(self, index): self.rows.pop(index)


class ChatListBatchTests(unittest.TestCase):
    def panel(self):
        panel = SimpleNamespace(
            _chats=[], _chat_indexes_by_jid={}, _items=[], _last_selected_jid="",
            _visible_stale=False, _searching=False, _update_batch_depth=0,
            list_box=ListBox(), IsShown=lambda: True,
            _format_item_row=lambda item: item.chat.name + item.chat.last_message_preview,
            _item_key=ChatListPanel._item_key,
        )
        for name in ("set_chats", "begin_update_batch", "end_update_batch",
                     "force_refresh_visible", "upsert_chat", "_rebuild_chat_indexes",
                     "_set_items", "_preserve_current_order", "_sync_items_incrementally",
                     "_item_index", "_selected_chat_jid", "selected_chat", "selected_item",
                     "_chat_by_jid", "select_chat_by_jid"):
            setattr(panel, name, MethodType(getattr(ChatListPanel, name), panel))
        return panel

    def test_thousand_updates_render_once_and_keep_focused_order_and_selection(self):
        panel = self.panel()
        a, b = Chat(jid="a@example.invalid", name="A"), Chat(jid="b@example.invalid", name="B")
        panel.set_chats([a, b])
        panel.list_box.selection, panel.list_box.focused = 1, True
        panel.list_box.writes = 0
        panel.begin_update_batch()
        for index in range(1000):
            updated = replace(a, last_message_preview=str(index))
            panel.upsert_chat(updated)
            panel.set_chats([b, updated])
            panel.force_refresh_visible()
        self.assertEqual(panel.list_box.writes, 0)
        panel.end_update_batch()
        self.assertEqual(panel.list_box.writes, 1)
        self.assertEqual([i.chat.jid for i in panel._items], [a.jid, b.jid])
        self.assertEqual(panel.selected_chat().jid, b.jid)
        self.assertTrue(panel.list_box.focused)
        self.assertEqual(panel.list_box.frozen, 0)
        self.assertEqual(panel._items[0].chat.last_message_preview, "999")

    def test_hidden_searching_and_nested_batches_do_not_replace_visible_results(self):
        for state in ("hidden", "searching", "nested"):
            with self.subTest(state=state):
                panel = self.panel()
                panel.set_chats([Chat(jid="a@example.invalid", name="A")])
                panel.list_box.writes = 0
                if state == "hidden":
                    panel.IsShown = lambda: False
                if state == "searching":
                    panel._searching = True
                panel.begin_update_batch()
                if state == "nested":
                    panel.begin_update_batch()
                panel.upsert_chat(Chat(jid="a@example.invalid", name="Updated"))
                panel.end_update_batch()
                self.assertEqual(panel.list_box.writes, 0)
                if state == "nested":
                    panel.end_update_batch()
                    self.assertEqual(panel.list_box.writes, 1)
                else:
                    self.assertTrue(panel._visible_stale)

    def test_incremental_render_failure_always_thaws_native_control(self):
        panel = self.panel()
        chat = Chat(jid="a@example.invalid", name="A")
        panel.set_chats([chat])
        panel.list_box.focused = True
        panel.begin_update_batch()
        panel.upsert_chat(replace(chat, name="Updated"))
        panel._format_item_row = Mock(side_effect=RuntimeError("fixture rendering failure"))
        with self.assertRaises(RuntimeError):
            panel.end_update_batch()
        self.assertEqual(panel.list_box.frozen, 0)
        self.assertEqual(panel._update_batch_depth, 0)
