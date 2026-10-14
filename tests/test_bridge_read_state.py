from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.bridge_read_state import native_error_kind, native_with_retry, sync_group_read
from tools.patch_slidge_read_state_v33 import (
    NATIVE_METHOD,
    clone_binding,
    patch_marker,
    patch_package,
)


class BridgeReadStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_chats_do_not_mutate_app_state(self):
        recipient = SimpleNamespace(get_wa_chat=lambda: SimpleNamespace(IsGroup=False))
        with patch("tools.bridge_read_state._sync_group_read", new_callable=AsyncMock) as sync:
            await sync_group_read(recipient, "fixture")
        sync.assert_not_awaited()

    async def test_failed_account_cooldown_does_not_block_another_account(self):
        from slixmpp.exceptions import XMPPError

        session = SimpleNamespace(
            _can_read_state_retry_after=asyncio.get_running_loop().time() + 60
        )
        blocked = SimpleNamespace(
            session=session, get_wa_chat=lambda: SimpleNamespace(IsGroup=True)
        )
        healthy = SimpleNamespace(session=SimpleNamespace(), get_wa_chat=blocked.get_wa_chat)
        with patch("tools.bridge_read_state._sync_group_read", new_callable=AsyncMock) as sync:
            with self.assertRaises(XMPPError) as raised:
                await sync_group_read(blocked, "first")
            self.assertFalse(raised.exception.clear)
            await sync_group_read(healthy, "second")
            sync.assert_awaited_once_with(healthy, "second")
            session._can_read_state_retry_after = 0
            await sync_group_read(blocked, "first")
            self.assertEqual(sync.await_count, 2)

    async def test_chat_mutations_serialize_per_account_but_not_across_accounts(self):
        for shared in (True, False):
            with self.subTest(shared_account=shared):
                session = SimpleNamespace()
                recipients = [SimpleNamespace(
                    session=session if shared else SimpleNamespace(),
                    get_wa_chat=lambda: SimpleNamespace(IsGroup=True),
                ) for _ in range(2)]
                active = maximum = 0

                async def mutate(*_):
                    nonlocal active, maximum
                    active += 1
                    maximum = max(maximum, active)
                    await asyncio.sleep(0.01)
                    active -= 1

                with patch("tools.bridge_read_state._sync_group_read", side_effect=mutate):
                    await asyncio.gather(*(sync_group_read(r, "fixture") for r in recipients))
                self.assertEqual(maximum, 1 if shared else 2)

    async def test_native_retries_are_bounded_and_payload_is_unchanged(self):
        payload = object()
        method = Mock(side_effect=[RuntimeError("fixture"), None])
        with patch("tools.bridge_read_state.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await native_with_retry(method, payload)
        self.assertEqual(method.call_args_list, [((payload,), {}), ((payload,), {})])
        sleep.assert_awaited_once_with(0.2)
        method = Mock(side_effect=RuntimeError("fixture"))
        with patch("tools.bridge_read_state.asyncio.sleep", new_callable=AsyncMock), \
                self.assertRaisesRegex(RuntimeError, "fixture"):
            await native_with_retry(method, payload)
        self.assertEqual(method.call_count, 3)

    async def test_recovery_diagnostics_classify_only_the_last_error_without_private_details(self):
        for suffix, expected in (
            ("mismatching LTHash", "snapshot_hash"),
            ("didn't find app state key", "missing_key"),
            ("context deadline exceeded", "timeout"),
            ("failed to fetch snapshot", "snapshot_fetch"),
            ("fixture private value", "native_error"),
        ):
            with self.subTest(kind=expected):
                error = RuntimeError("fixture original mismatching LTHash\n" + suffix)
                self.assertEqual(native_error_kind(error), expected)

    async def test_slow_native_call_does_not_block_event_loop(self):
        import threading

        started, finish = threading.Event(), threading.Event()

        def native(_):
            started.set()
            finish.wait(5)

        task = asyncio.create_task(native_with_retry(native, None))
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                if started.is_set():
                    break
            self.assertTrue(started.is_set())
            self.assertFalse(task.done())
        finally:
            finish.set()
            await task

    async def test_marker_acknowledges_only_successful_messages_and_syncs_once(self):
        source = '''class MarkerMixin:
    async def on_marker_displayed(self, msg):
        recipient = self.recipient
        legacy_thread = None
        to_mark = self.__to_mark(recipient, msg["displayed"]["id"])
        for xmpp_id in to_mark:
            await recipient.on_displayed(
                self._xmpp_msg_id_to_legacy(xmpp_id, recipient), legacy_thread
            )
        if isinstance(recipient, LegacyMUC):
            await recipient.echo(msg, None)

    def __to_mark(self, recipient: AnyRecipient, msg_id: str) -> list[str]:
        return []

    async def on_mds(self, stanza_id):
        recipient = self.recipient
        to_mark = self.__to_mark(recipient, stanza_id)
        for xmpp_id in to_mark:
            await recipient.on_displayed(
                self._xmpp_msg_id_to_legacy(xmpp_id, recipient), None
            )
'''
        class Room:
            pass

        namespace = {"LegacyMUC": Room, "AnyRecipient": object}
        exec(patch_marker(source), namespace)
        room = Room()
        room.can_unread_up_to = AsyncMock(return_value=["first", "second", "third"])
        room.on_displayed = AsyncMock(side_effect=[None, RuntimeError("fixture")])
        room.can_ack_read = AsyncMock()
        room.on_chat_read = AsyncMock()
        room.echo = AsyncMock()
        handler = namespace["MarkerMixin"]()
        handler.recipient = room
        handler._xmpp_msg_id_to_legacy = lambda value, _: "native-" + value
        with self.assertRaises(RuntimeError):
            await handler.on_marker_displayed({"displayed": {"id": "third"}})
        room.can_ack_read.assert_awaited_once_with("first")
        room.on_chat_read.assert_not_awaited()
        room.on_displayed = AsyncMock()
        room.can_unread_up_to.return_value = ["second", "third"]
        await handler.on_marker_displayed({"displayed": {"id": "third"}})
        room.on_chat_read.assert_awaited_once_with("third")
        room.on_chat_read.reset_mock()
        room.can_unread_up_to.return_value = []
        await handler.on_mds("third")
        room.on_chat_read.assert_not_awaited()  # No reflected app-state loop.


class ReadStateBindingTests(unittest.TestCase):
    def test_existing_v33_native_method_is_upgraded_idempotently(self):
        import re

        old = re.sub(
            r"    return canSendGroupReadWithRecovery\(ctx, patch,.*?\n    \}\)",
            "    return client.SendAppState(ctx, patch)",
            NATIVE_METHOD.replace("45*time.Second", "15*time.Second"), flags=re.S,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            (package / "generated").mkdir(parents=True)
            dispatcher = root / "slidge/core/dispatcher/message"
            dispatcher.mkdir(parents=True)
            (package / "session.go").write_text("package whatsapp\n" + old, encoding="utf-8")
            (package / "mixins.py").write_text("can_ack_read", encoding="utf-8")
            (dispatcher / "marker.py").write_text("can_ack_read", encoding="utf-8")
            for name in ("whatsapp.go", "whatsapp.py", "whatsapp.c", "whatsapp_go.h"):
                (package / "generated" / name).write_text("MarkChatRead", encoding="utf-8")
            self.assertTrue(patch_package(root))
            updated = (package / "session.go").read_text(encoding="utf-8")
            self.assertIn("canSendGroupReadWithRecovery(ctx, patch", updated)
            self.assertIn("45*time.Second", updated)
            self.assertTrue((package / "can_read_state_recovery.go").exists())
            self.assertFalse(patch_package(root))
            self.assertEqual((package / "session.go").read_text(encoding="utf-8"), updated)

    def test_patch_failure_does_not_write_any_source_or_module(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            native = package / "session.go"
            native.write_text("package whatsapp\n", encoding="utf-8")
            (package / "mixins.py").write_text("unexpected source", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(native.read_text(encoding="utf-8"), "package whatsapp\n")
            self.assertFalse((package / "read_state.py").exists())

    def test_missing_or_duplicate_binding_anchors_fail_closed(self):
        for kind in ("go", "c", "py", "h"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                clone_binding("unexpected source", kind)
        line = ("extern char* whatsapp_Session_SendReceipt"
                "(long long int _handle, long long int receipt);\n")
        result = clone_binding(line, "h")
        self.assertIn("whatsapp_Session_MarkChatRead", result)
        with self.assertRaises(ValueError):
            clone_binding(line * 2, "h")


if __name__ == "__main__":
    unittest.main()
