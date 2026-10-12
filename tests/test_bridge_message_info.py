from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.bridge_message_info import (
    NAMESPACE,
    InfoStore,
    install,
    merge_rows,
    prepare_outgoing,
    resolve_owned_message,
)
from tools.patch_slidge_message_info_v33 import (
    patch_package,
    patched_gateway,
    patched_mixins,
    patched_session,
)

ROOT = Path(__file__).resolve().parents[1]


class MessageInfoStoreTests(unittest.TestCase):
    def test_private_account_room_and_message_scope_and_monotonic_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = InfoStore(Path(directory) / "receipts.sqlite3")
            row = {"jid": "alpha@example.test", "name": "Alpha", "delivered-at": 100}
            store.update("me@test", "group@test", "id", True, True, [row])
            update = {"jid": "alpha@example.test", "lid": "100@lid", "read-at": 200}
            data = store.update("me@test", "group@test", "id", False, False, [update])
            self.assertTrue(data["complete"])
            self.assertTrue(data["own"])
            self.assertEqual(len(data["rows"]), 1)
            self.assertEqual(data["rows"][0]["delivered-at"], 100)
            self.assertEqual(data["rows"][0]["read-at"], 200)
            for account, room, message in (
                ("other@test", "group@test", "id"),
                ("me@test", "other@test", "id"),
                ("me@test", "group@test", "other"),
            ):
                self.assertIsNone(store.get(account, room, message))
            store.executor.shutdown(wait=True)
            again = InfoStore(store.path)
            self.assertEqual(again.get("me@test", "group@test", "id"), data)
            again.executor.shutdown(wait=True)

    def test_phone_lid_bridge_coalesces_previously_separate_aliases(self):
        rows = merge_rows(
            [
                {"jid": "alpha@example.test", "delivered-at": 100},
                {"lid": "100@lid", "read-at": 200},
            ],
            [{"jid": "alpha@example.test", "lid": "100@lid", "read-at": 300}],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["delivered-at"], rows[0]["read-at"]), (100, 200))


class RecipientSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_is_immutable_and_never_uses_future_members(self):
        people = [
            SimpleNamespace(is_user=True),
            SimpleNamespace(is_user=False, contact=None, occupant_id="100@lid", nickname="Alpha"),
        ]

        async def participants():
            for person in people:
                yield person

        recipient = SimpleNamespace(
            get_participants=participants, participants_filled=True, n_participants=2
        )
        message = SimpleNamespace(Chat=SimpleNamespace(IsGroup=True))
        complete, rows = await prepare_outgoing(recipient, message)
        people.append(
            SimpleNamespace(is_user=False, contact=None, occupant_id="200@lid", nickname="Beta")
        )
        self.assertTrue(complete)
        self.assertEqual([p["lid"] for p in rows], ["100@lid"])

    async def test_partial_or_unresolved_membership_is_explicitly_incomplete(self):
        async def participants():
            yield SimpleNamespace(is_user=False, contact=None, occupant_id="", nickname="Anonymous")

        recipient = SimpleNamespace(
            get_participants=participants, participants_filled=True, n_participants=2
        )
        complete, rows = await prepare_outgoing(
            recipient, SimpleNamespace(Chat=SimpleNamespace(IsGroup=True))
        )
        self.assertFalse(complete)
        self.assertEqual(rows, [])
        self.assertEqual(
            await prepare_outgoing(recipient, SimpleNamespace(Chat=SimpleNamespace(IsGroup=False))),
            (False, []),
        )

    async def test_unavailable_snapshot_does_not_prevent_sending(self):
        recipient = SimpleNamespace(get_participants=Mock(side_effect=RuntimeError("fixture")))
        complete, rows = await prepare_outgoing(
            recipient, SimpleNamespace(Chat=SimpleNamespace(IsGroup=True))
        )
        self.assertFalse(complete)
        self.assertEqual(rows, [])


class MessageInfoPatchTests(unittest.TestCase):
    def test_registration_uses_real_coroutine_callback_and_component_matcher(self):
        from xml.etree import ElementTree as ET

        from slixmpp.stanza import Iq

        class Gateway:
            default_ns = "jabber:component:accept"

            def __init__(self):
                self.handlers = []
                self.plugin = Mock()

            def __getitem__(self, key):
                return self.plugin

            def register_handler(self, handler):
                self.handlers.append(handler)

        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway()
            with patch.dict(
                sys.modules,
                {
                    "slidge": SimpleNamespace(
                        global_config=SimpleNamespace(HOME_DIR=Path(directory))
                    ),
                },
            ):
                install(gateway)
            try:
                iq = Iq()
                iq.xml.tag = "{jabber:component:accept}iq"
                iq.append(ET.Element(f"{{{NAMESPACE}}}info"))
                self.assertTrue(gateway.handlers[0].match(iq))
                gateway.plugin.add_feature.assert_called_once_with(NAMESPACE)
            finally:
                gateway._can_message_info_store.executor.shutdown(wait=True)

    def test_archived_own_lid_is_authorized_but_other_participants_are_not(self):
        @contextmanager
        def orm():
            yield object()

        maps = SimpleNamespace(get_legacy=lambda *args, **kwargs: None)
        participants = SimpleNamespace(
            get_all=lambda *args: [
                SimpleNamespace(occupant_id="self-fixture@lid", is_user=True),
                SimpleNamespace(occupant_id="peer-fixture@lid", is_user=False),
            ]
        )
        session = SimpleNamespace(
            xmpp=SimpleNamespace(
                store=SimpleNamespace(session=orm, id_map=maps, participants=participants)
            ),
            contacts=SimpleNamespace(user_legacy_id="self-fixture@example.test"),
            message_is_carbon=lambda *args: False,
        )
        room = SimpleNamespace(
            stored=SimpleNamespace(id=1),
            get_archived_messages=lambda _: [
                SimpleNamespace(occupant_id="self-fixture@lid", stanza={"id": "native"}),
            ],
        )
        self.assertEqual(resolve_owned_message(session, room, ["archive-id"]), "native")
        room.get_archived_messages = lambda _: [
            SimpleNamespace(occupant_id="peer-fixture@lid", stanza={"id": "native"}),
        ]
        self.assertIsNone(resolve_owned_message(session, room, ["archive-id"]))

    def test_python_hooks_preserve_send_order_and_history_ingestion(self):
        mixin = (
            "from __future__ import annotations\n"
            "from .generated import go, whatsapp\nclass Recipient:\n"
        )
        for method in ("text", "file", "sticker"):
            mixin += (
                f"    async def {method}(self):\n"
                "        self.wa.SendMessage(message)  # type:ignore[no-untyped-call]\n"
                "        return message_id\n"
            )
        result = patched_mixins(mixin)
        ast.parse(result)
        self.assertEqual(result.count("await prepare_outgoing"), 3)
        self.assertEqual(result.count("await outgoing_sent"), 3)
        for method in ("text", "file", "sticker"):
            body = result.split(f"async def {method}")[1].split("async def")[0]
            self.assertLess(body.index("prepare_outgoing"), body.index("SendMessage"))
            self.assertLess(body.index("SendMessage"), body.index("outgoing_sent"))
        session = (
            "from __future__ import annotations\nclass Session:\n"
            "    async def receipt(self):\n"
            "        for message_id in receipt.MessageIDs:\n            pass\n"
            "    async def message(self):\n"
            "        # Skip handing message that's already in our message archive.\n"
            "        if message.IsHistory:\n            return\n"
        )
        result = patched_session(session)
        ast.parse(result)
        self.assertLess(result.index("await ingest_message"), result.index("if message.IsHistory"))
        ast.parse(
            patched_gateway(
                "from .generated import whatsapp\nclass Gateway:\n"
                "    def init(self):\n        self.whatsapp.Init()\n"
            )
        )

    def test_all_anchors_validated_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "slidge_whatsapp"
            package.mkdir()
            for name in ("session.py", "mixins.py", "gateway.py", "event.go"):
                (package / name).write_text("invalid fixture", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(Path(directory))
            self.assertTrue(
                all(path.read_text() == "invalid fixture" for path in package.iterdir())
            )

    def test_native_patch_uses_individual_timestamps_and_rejects_unknown_types(self):
        source = (ROOT / "tools/patch_slidge_message_info_v33.py").read_text(encoding="utf-8")
        self.assertIn("evt.Type != types.ReceiptTypeDelivered", source)
        self.assertIn("canHistoryReceipt(r.GetReceiptTimestamp(), r.GetReadTimestamp())", source)
        self.assertIn("delivered.Timestamp = r.GetReceiptTimestamp()", source)


if __name__ == "__main__":
    unittest.main()
