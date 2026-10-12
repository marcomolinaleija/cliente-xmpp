from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from slixmpp import JID

from cliente_xmpp.models.chat import Message
from cliente_xmpp.models.message_info import (
    GroupMessageInfo,
    ParticipantReceipt,
    merge_message_info,
)
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.xmpp.client import BridgeXmppClient, XmppService
from cliente_xmpp.xmpp.events import GroupMessageInfoLoaded
from cliente_xmpp.xmpp.message_info import MESSAGE_INFO_NS, parse_message_info

ROOM = "room@bridge.example.test"


class MessageInfoLatencyTests(unittest.TestCase):
    def window(self):
        window = MainWindow.__new__(MainWindow)
        window.current_jid = "me@example.test"
        self.jobs = []
        window.storage_executor = SimpleNamespace(submit=lambda worker: self.jobs.append(worker))
        window.xmpp = SimpleNamespace(load_group_message_info=Mock())
        window.message_store = SimpleNamespace(
            load_group_message_info=Mock(return_value=None),
            save_group_message_info=Mock(side_effect=lambda _account, info: info),
        )
        return window

    def test_network_and_live_rows_do_not_wait_for_storage_queue_and_late_cache_is_ignored(self):
        window = self.window()
        callback = Mock()
        message = Message(chat_jid=ROOM, sender_jid="me@test", body="Fixture", message_id="id")
        info = GroupMessageInfo(ROOM, "id", "native", participants=(
            ParticipantReceipt(lid="100@lid", read_at=200),
        ))
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter", side_effect=lambda fn, *a: fn(*a)):
            window._load_message_info_async(window.current_jid, message, callback)
            window.xmpp.load_group_message_info.assert_called_once()
            window.message_store.load_group_message_info.assert_not_called()
            request_id = window.xmpp.load_group_message_info.call_args.args[2]
            window._handle_group_message_info_loaded(GroupMessageInfoLoaded(request_id, info))
            callback.assert_called_once_with(info, "", True)
            window.message_store.save_group_message_info.assert_not_called()
            # The old cache job completes after the live response: it cannot replace it.
            self.jobs.pop(0)()
            callback.assert_called_once_with(info, "", True)
            self.jobs.pop(0)()
            window.message_store.save_group_message_info.assert_called_once_with(
                window.current_jid, info,
            )
            self.assertEqual(callback.call_args.args, (info, "", True))

    def test_unrelated_response_is_rejected_before_render_or_save(self):
        window = self.window()
        callback = Mock()
        window._message_info_requests = {"req": (window.current_jid, ROOM, "id", callback)}
        window._handle_group_message_info_loaded(GroupMessageInfoLoaded(
            "req", GroupMessageInfo("other@example.test", "id"),
        ))
        self.assertIsNone(callback.call_args.args[0])
        self.assertTrue(callback.call_args.args[1])
        self.assertFalse(self.jobs)
        window.message_store.save_group_message_info.assert_not_called()

    def test_cache_write_error_keeps_live_response(self):
        window = self.window()
        callback = Mock()
        info = GroupMessageInfo(ROOM, "id", "native")
        window._message_info_requests = {"req": (window.current_jid, ROOM, "id", callback)}
        window.message_store.save_group_message_info.side_effect = OSError("Fixture")
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter", side_effect=lambda fn, *a: fn(*a)):
            window._handle_group_message_info_loaded(GroupMessageInfoLoaded("req", info))
            callback.assert_called_once_with(info, "", True)
            self.jobs.pop(0)()
        self.assertIs(callback.call_args.args[0], info)
        self.assertTrue(callback.call_args.args[1])


class MessageInfoModelTests(unittest.TestCase):
    def test_all_states_do_not_infer_delivery_from_absence_or_read_time(self):
        self.assertEqual(ParticipantReceipt(lid="1@lid").state, "Sin entrega confirmada")
        self.assertEqual(
            ParticipantReceipt(lid="1@lid", delivered_at=100).state,
            "Entregado, sin lectura confirmada",
        )
        row = ParticipantReceipt(lid="1@lid", read_at=200)
        self.assertEqual(row.state, "Leído")
        self.assertEqual(row.delivered_at, 0)

    def test_receipts_merge_monotonically_across_phone_lid_and_out_of_order(self):
        old = GroupMessageInfo(
            ROOM,
            "id",
            "native",
            True,
            (
                ParticipantReceipt(jid="alpha@example.test", name="Alpha", delivered_at=100),
                ParticipantReceipt(lid="100@lid", read_at=200),
            ),
        )
        new = GroupMessageInfo(
            ROOM,
            "id",
            "native",
            False,
            (ParticipantReceipt(jid="alpha@example.test", lid="100@lid", delivered_at=150),),
        )
        merged = merge_message_info(old, new)
        self.assertEqual(len(merged.participants), 1)
        row = merged.participants[0]
        self.assertEqual((row.name, row.delivered_at, row.read_at), ("Alpha", 100, 200))
        self.assertTrue(merged.recipients_complete)
        self.assertEqual(merge_message_info(merged, new), merged)

    def test_different_rooms_message_and_native_ids_are_rejected(self):
        old = GroupMessageInfo(ROOM, "id", "native")
        for new in (
            GroupMessageInfo("other@example.test", "id"),
            GroupMessageInfo(ROOM, "other"),
            GroupMessageInfo(ROOM, "id", "other"),
        ):
            with self.subTest(new=new), self.assertRaises(ValueError):
                merge_message_info(old, new)

    def test_parser_requires_matching_message_identity_and_valid_times(self):
        xml = ET.Element("iq")
        node = ET.SubElement(xml, f"{{{MESSAGE_INFO_NS}}}info", {"id": "id"})
        person = ET.SubElement(node, f"{{{MESSAGE_INFO_NS}}}participant", {"lid": "100@lid"})
        self.assertFalse(parse_message_info(xml, ROOM, "id").recipients_complete)
        for attr, value in (("read-at", "-1"), ("read-at", "junk"), ("read-at", "253402300800")):
            person.set(attr, value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_message_info(xml, ROOM, "id")
        with self.assertRaises(ValueError):
            parse_message_info(xml, ROOM, "another-id")


class MessageInfoStorageTests(unittest.TestCase):
    def test_cache_survives_origin_echo_native_id_changes_and_alias_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            info = GroupMessageInfo(
                ROOM,
                "client-id",
                "native",
                True,
                (ParticipantReceipt(lid="100@lid", delivered_at=100, read_at=200),),
            )
            store.save_group_message_info("me@test", info)
            alias = store.load_group_message_info("me@test", ROOM, "native")
            self.assertEqual(alias.message_id, "native")
            self.assertEqual(alias.participants, info.participants)
            marker = store.load_group_message_info("me@test", ROOM, "archive-id", "native")
            self.assertEqual(marker.message_id, "archive-id")
            merged = store.save_group_message_info(
                "me@test", GroupMessageInfo(ROOM, "native", "native")
            )
            self.assertEqual(merged.participants, info.participants)
            self.assertTrue(merged.recipients_complete)
            store.delete_cached_message("me@test", ROOM, "native")
            self.assertIsNone(store.load_group_message_info("me@test", ROOM, "client-id"))

    def test_isolation_restart_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.sqlite3"
            store = MessageStore(path)
            info = GroupMessageInfo(
                ROOM, "id", "native", True, (ParticipantReceipt(lid="100@lid", delivered_at=100),)
            )
            store.save_group_message_info("me@example.test", info)
            reopened = MessageStore(path)
            self.assertEqual(reopened.load_group_message_info("me@example.test", ROOM, "id"), info)
            self.assertIsNone(reopened.load_group_message_info("other@example.test", ROOM, "id"))
            self.assertIsNone(
                reopened.load_group_message_info("me@example.test", "other@test", "id")
            )
            new = GroupMessageInfo(
                ROOM, "id", "native", False, (ParticipantReceipt(lid="100@lid", read_at=200),)
            )
            merged = reopened.save_group_message_info("me@example.test", new)
            self.assertEqual(merged.participants[0].delivered_at, 100)
            self.assertTrue(merged.recipients_complete)
            reopened.delete_cached_message("me@example.test", ROOM, "id")
            self.assertIsNone(reopened.load_group_message_info("me@example.test", ROOM, "id"))
            reopened.save_group_message_info("me@example.test", info)
            reopened.delete_chat("me@example.test", ROOM)
            self.assertIsNone(reopened.load_group_message_info("me@example.test", ROOM, "id"))


class MessageInfoProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_iq_response_emits_typed_event_and_keeps_reference(self):
        xml = ET.Element("iq")
        ET.SubElement(xml, f"{{{MESSAGE_INFO_NS}}}info", {"id": "id", "legacy-id": "native"})

        class Result:
            def __getitem__(self, key):
                return JID(ROOM)

        response = Result()
        response.xml = xml
        from slixmpp.stanza import Iq

        iq = Iq()
        iq.send = AsyncMock(return_value=response)
        emitted = []
        client = SimpleNamespace(Iq=lambda: iq, _emit=emitted.append)
        await BridgeXmppClient.load_group_message_info(client, ROOM, "id", "request", "marker")
        self.assertEqual(iq["to"].bare, ROOM)
        self.assertEqual(iq.xml.find(f"{{{MESSAGE_INFO_NS}}}info").get("reference"), "marker")
        self.assertEqual(emitted[0].info.legacy_id, "native")
        self.assertEqual(emitted[0].request_id, "request")

    async def test_wrong_chat_and_malformed_response_are_errors_not_receipts(self):
        class Result:
            xml = ET.Element("iq")

            def __getitem__(self, key):
                return JID("other@bridge.example.test")

        from slixmpp.stanza import Iq

        iq = Iq()
        iq.send = AsyncMock(return_value=Result())
        emitted = []
        await BridgeXmppClient.load_group_message_info(
            SimpleNamespace(Iq=lambda: iq, _emit=emitted.append),
            ROOM,
            "id",
            "request",
        )
        self.assertIsNone(emitted[0].info)
        self.assertTrue(emitted[0].error)

    def test_offline_service_still_emits_correlated_result(self):
        emitted = []
        service = SimpleNamespace(_client=None, _loop=None, _emit=emitted.append)
        XmppService.load_group_message_info(service, ROOM, "id", "request")
        self.assertIsInstance(emitted[0], GroupMessageInfoLoaded)
        self.assertEqual(emitted[0].request_id, "request")
        self.assertIn("Sin conexión", emitted[0].error)


if __name__ == "__main__":
    unittest.main()
