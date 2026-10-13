from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from cliente_xmpp.models.chat import Message
from cliente_xmpp.models.message_info import GroupMessageInfo, ParticipantReceipt
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.message_info_dialog import (
    REFRESH_INTERVAL_MS,
    SECTION_TITLES,
    DirectMessageInfoDialog,
    MessageInfoDialog,
    receipt_time,
)


class MessageInfoNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    @staticmethod
    def person_index(dialog, *, lid="", jid=""):
        return next(i for i, row in enumerate(dialog._view_rows)
                    if isinstance(row, ParticipantReceipt)
                    and ((lid and row.lid == lid) or (jid and row.jid == jid)))

    def test_three_sections_keep_order_empty_headings_and_both_times(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            dialog.refresh()
            people = (
                ParticipantReceipt(lid="3@lid", name="Pending"),
                ParticipantReceipt(lid="2@lid", name="Delivered", delivered_at=100),
                ParticipantReceipt(lid="1@lid", name="Read", delivered_at=100, read_at=200),
                ParticipantReceipt(lid="4@lid", name="Read without delivery", read_at=200),
            )
            callbacks[-1](GroupMessageInfo("room@test", "id", participants=people), "", True)
            self.assertEqual(
                [r for r in dialog._view_rows if isinstance(r, str)], list(SECTION_TITLES),
            )
            self.assertEqual([dialog.people.GetItemText(i) for i in range(7)], [
                "Leído (2)", "Read", "Read without delivery", "Entregado (1)", "Delivered",
                "Sin entrega confirmada (1)", "Pending",
            ])
            self.assertEqual(dialog.people.GetItemText(1, 2), receipt_time(100))
            self.assertEqual(dialog.people.GetItemText(1, 3), receipt_time(200))
            self.assertEqual(dialog.people.GetItemText(2, 2), "Sin confirmación")
            with patch("cliente_xmpp.ui.message_info_dialog.wx.MessageBox") as details:
                dialog.people.Select(0)
                dialog._details(None)
                details.assert_not_called()
                dialog.people.Select(1)
                dialog._details(None)
                self.assertIn(receipt_time(200), details.call_args.args[0])
        finally:
            dialog.deactivate()
            dialog.Destroy()

        empty = MessageInfoDialog(None, callbacks.append)
        try:
            empty.refresh()
            callbacks[-1](GroupMessageInfo("room@test", "id"), "", True)
            self.assertEqual(empty.people.GetItemCount(), 3)
            self.assertEqual([empty.people.GetItemText(i) for i in range(3)], [
                title + " (0)" for title in SECTION_TITLES
            ])
        finally:
            empty.deactivate()
            empty.Destroy()

    def test_two_second_refresh_never_overlaps_or_regresses_read_receipts(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            dialog.refresh()
            dialog.refresh()
            self.assertEqual(len(callbacks), 1)
            cached = GroupMessageInfo("room@test", "id", "native", participants=(
                ParticipantReceipt(lid="100@lid", read_at=200),
            ))
            callbacks[-1](cached, "", False)
            dialog.refresh()
            self.assertEqual(len(callbacks), 1)
            older = GroupMessageInfo("room@test", "id", "native", participants=(
                ParticipantReceipt(lid="100@lid", delivered_at=100),
            ))
            callbacks[-1](older, "", True)
            self.assertEqual(dialog.people.GetItemText(1, 1), "Leído")
            self.assertEqual(dialog.timer.GetInterval(), REFRESH_INTERVAL_MS)
            self.assertEqual(REFRESH_INTERVAL_MS, 2000)
        finally:
            dialog.deactivate()
            dialog.Destroy()

    def test_large_group_keeps_last_selected_row_and_bounded_columns(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            rows = tuple(
                ParticipantReceipt(lid=f"{i}@lid", name=f"Fixture {i:04}") for i in range(512)
            )
            dialog.refresh()
            callbacks[-1](GroupMessageInfo("room@test", "id", "native", True, rows), "", True)
            last = self.person_index(dialog, lid="511@lid")
            dialog.people.Select(last)
            dialog.people.Focus(last)
            dialog.people.EnsureVisible(last)
            width = dialog.people.GetColumnWidth(0)
            updated = rows[:-1] + (
                ParticipantReceipt(lid="511@lid", name="Fixture 0511", read_at=200),
            )
            dialog.refresh()
            callbacks[-1](GroupMessageInfo("room@test", "id", "native", True, updated), "", True)
            self.assertEqual(dialog.people.GetFirstSelected(), 1)
            self.assertEqual(dialog.people.GetItemText(1), "Fixture 0511")
            self.assertEqual(dialog.people.GetColumnWidth(0), width)
            self.assertFalse(dialog.people.IsFrozen())
        finally:
            dialog.deactivate()
            dialog.Destroy()

    def test_selection_follows_phone_lid_alias_when_display_name_reorders_rows(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            dialog.refresh()
            old = GroupMessageInfo("room@test", "id", participants=(
                ParticipantReceipt(jid="alpha@example.test", name="Zed"),
                ParticipantReceipt(lid="200@lid", name="Middle"),
            ))
            callbacks[-1](old, "", True)
            selected = self.person_index(dialog, jid="alpha@example.test")
            dialog.people.Select(selected)
            dialog.people.Focus(selected)
            dialog.refresh()
            new = GroupMessageInfo("room@test", "id", participants=(
                ParticipantReceipt(jid="alpha@example.test", lid="100@lid", name="Aardvark"),
                old.participants[1],
            ))
            callbacks[-1](new, "", True)
            self.assertEqual(dialog.people.GetFirstSelected(), 3)
            self.assertEqual(dialog._rows[0].jid, "alpha@example.test")
        finally:
            dialog.deactivate()
            dialog.Destroy()

    def test_refresh_preserves_selection_and_late_callback_after_close_is_ignored(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            dialog.refresh()
            rows = (
                ParticipantReceipt(lid="100@lid", name="Alpha"),
                ParticipantReceipt(lid="200@lid", name="Beta", delivered_at=100),
            )
            info = GroupMessageInfo("room@test", "id", "native", True, rows)
            callbacks[-1](info, "", True)
            self.assertEqual(dialog.people.GetItemCount(), 5)
            selected = self.person_index(dialog, lid="200@lid")
            dialog.people.Select(selected)
            dialog.people.Focus(selected)
            dialog.refresh()
            read = GroupMessageInfo(
                "room@test",
                "id",
                "native",
                True,
                (
                    rows[0],
                    ParticipantReceipt(lid="200@lid", name="Beta", delivered_at=100, read_at=200),
                ),
            )
            callbacks[-1](read, "", True)
            self.assertEqual(dialog.people.GetFirstSelected(), 1)
            self.assertEqual(dialog.people.GetItemText(1, 1), "Leído")
            dialog.refresh()
            callback = callbacks[-1]
            dialog.deactivate()
            callback(info, "", True)
            self.assertEqual(dialog.people.GetItemText(1, 1), "Leído")
        finally:
            dialog.deactivate()
            dialog.Destroy()

    def test_cached_partial_info_and_error_keep_rows_without_inventing_pending_people(self):
        callbacks = []
        dialog = MessageInfoDialog(None, callbacks.append)
        try:
            dialog.refresh()
            info = GroupMessageInfo(
                "room@test",
                "id",
                participants=(ParticipantReceipt(lid="100@lid", name="Alpha", read_at=200),),
            )
            callbacks[-1](info, "", False)
            self.assertIn("Lista incompleta", dialog.status.GetLabel())
            self.assertFalse(dialog.refresh_button.IsEnabled())
            callbacks[-1](None, "Sin conexión", True)
            self.assertEqual(dialog.people.GetItemCount(), 4)
            self.assertTrue(dialog.refresh_button.IsEnabled())
            self.assertFalse(dialog.timer.IsRunning())
        finally:
            dialog.deactivate()
            dialog.Destroy()


class DirectMessageInfoNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def test_context_menu_offers_own_direct_info_and_preserves_group_and_foreign_guards(self):
        window = MainWindow.__new__(MainWindow)
        window.conversation = SimpleNamespace(selected_messages=lambda: [])
        window._private_message_recipient = Mock(return_value=None)
        window._message_can_be_edited = Mock(return_value=False)
        window._message_can_be_deleted = Mock(return_value=True)
        for group, own, state, expected in (
            (False, True, "sent", True), (False, True, "pending", True),
            (False, True, "failed", True), (False, False, "", None),
            (True, True, "sent", True), (True, True, "pending", False),
        ):
            with self.subTest(group=group, own=own, state=state):
                items = {}

                def popup(menu, items=items):
                    items.update({i.GetItemLabelText(): i.IsEnabled() for i in menu.GetMenuItems()})

                owner = SimpleNamespace(Bind=Mock(), PopupMenu=popup)
                message = Message("chat@example.test", "Yo", "Fixture", message_id="id",
                                  chat_is_group=group, outgoing=own, delivery_state=state)
                MainWindow._show_message_context_menu(window, message, popup_parent=owner)
                self.assertEqual(items.get("Info..."), expected)
                self.assertEqual("Eliminar mensaje" in items, own)

    def test_individual_info_dispatch_never_calls_group_loader(self):
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "owner@example.test"
        window._display_name_for_jid = Mock(return_value="Fixture recipient")
        window.xmpp = SimpleNamespace(load_group_message_info=Mock())
        message = Message("contact@example.test", "Yo", "Fixture", message_id="id", outgoing=True)
        window.messages_by_chat = {message.chat_jid: [message]}
        with (
            patch("cliente_xmpp.ui.main_window.DirectMessageInfoDialog") as direct,
            patch("cliente_xmpp.ui.main_window.MessageInfoDialog") as group,
        ):
            window._open_message_info(message)
            group.assert_not_called()
            loader = direct.call_args.args[1]
            self.assertEqual(loader(), (message, ""))
            direct.return_value.ShowModal.assert_called_once()
            direct.return_value.deactivate.assert_called_once()
            direct.return_value.Destroy.assert_called_once()
            window.xmpp.load_group_message_info.assert_not_called()

    def test_local_refresh_preserves_selection_and_escape_stops_updates(self):
        message = Message("contact@example.test", "Yo", "Fixture", outgoing=True,
                          message_id="id", delivery_state="sent")
        calls = []

        def load():
            calls.append(True)
            return message, ""

        dialog = DirectMessageInfoDialog(None, load, "Fixture recipient")
        try:
            self.assertIn("Estado: Enviado", dialog.details.GetValue())
            self.assertIn("Hora de lectura: No registrada", dialog.details.GetValue())
            self.assertEqual(dialog.timer.GetInterval(), REFRESH_INTERVAL_MS)
            dialog.details.SetSelection(0, 10)
            message.delivery_state = "read"
            dialog.refresh()
            self.assertIn("Estado: Leído", dialog.details.GetValue())
            self.assertEqual(dialog.details.GetSelection(), (0, 10))
            with patch.object(dialog, "Hide") as hide:
                event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
                event.SetKeyCode(wx.WXK_ESCAPE)
                dialog._key(event)
                hide.assert_called_once()
            self.assertFalse(dialog.timer.IsRunning())
            count = len(calls)
            dialog.refresh()
            self.assertEqual(len(calls), count)
        finally:
            dialog.deactivate()
            dialog.Destroy()

    def test_account_switch_hides_old_information_and_stops_timer(self):
        message = Message("contact@example.test", "Yo", "Fixture", outgoing=True)
        result = [message, ""]
        dialog = DirectMessageInfoDialog(None, lambda: tuple(result), "Fixture recipient")
        try:
            result[:] = [None, "La cuenta activa cambió."]
            dialog.refresh()
            self.assertEqual(dialog.details.GetValue(), "La cuenta activa cambió.")
            self.assertFalse(dialog.timer.IsRunning())
        finally:
            dialog.deactivate()
            dialog.Destroy()


if __name__ == "__main__":
    unittest.main()
