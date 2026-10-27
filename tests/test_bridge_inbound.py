from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inbound = load("bridge_inbound")
patcher = load("patch_slidge_inbound")
KINDS = SimpleNamespace(EventQRCode=1, EventPairDeviceID=2, EventConnect=3,
                        EventLoggedOut=4, EventCall=5)


class BridgeInboundTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self):
        class Session:
            def __init__(self):
                loop = asyncio.get_running_loop()
                self.contacts = SimpleNamespace(ready=loop.create_future())
                self.bookmarks = SimpleNamespace(ready=loop.create_future())
                self.received = []

            async def handle_event(self, kind, ptr):
                self.received.append((kind, ptr))

            async def login(self):
                return "login"

            async def logout(self):
                return "logout"

            async def on_wa_logged_out(self, value):
                return value

        inbound.install(Session, KINDS)
        inbound.install(Session, KINDS)
        return Session()

    async def test_control_bypasses_unready_data_and_teardown_preserves_gates(self):
        session = self.make_session()
        task = asyncio.create_task(session.handle_event(10, 111))
        await asyncio.sleep(0)
        await session.handle_event(KINDS.EventConnect, 222)
        self.assertEqual(session.received, [(KINDS.EventConnect, 222)])
        self.assertEqual(await session.logout(), "logout")
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(session.contacts.ready.cancelled())
        self.assertFalse(session.bookmarks.ready.cancelled())
        self.assertEqual(session._can_inbound_tasks, set())

    async def test_account_and_lane_tasks_remain_independent(self):
        first, second = self.make_session(), self.make_session()
        second.contacts.ready.set_result(None)
        second.bookmarks.ready.set_result(None)
        blocked = asyncio.create_task(first.handle_event(10, 111))
        await asyncio.sleep(0)
        await second.handle_event(10, 222)
        await first.on_wa_logged_out("bye")
        with self.assertRaises(asyncio.CancelledError):
            await blocked
        self.assertEqual(second.received, [(10, 222)])

    async def test_data_is_not_dropped_when_gates_become_ready(self):
        session = self.make_session()
        task = asyncio.create_task(session.handle_event(10, 111))
        await asyncio.sleep(0)
        session.contacts.ready.set_result(None)
        session.bookmarks.ready.set_result(None)
        await task
        self.assertEqual(session.received, [(10, 111)])
        self.assertEqual(session._can_inbound_tasks, set())

    async def test_call_readiness_wait_is_not_a_linking_control(self):
        session = self.make_session()
        waiting = asyncio.create_task(session.handle_event(KINDS.EventCall, 111))
        await asyncio.sleep(0)
        await session.handle_event(KINDS.EventConnect, 222)
        self.assertEqual(session.received, [(KINDS.EventConnect, 222)])
        await session.logout()
        with self.assertRaises(asyncio.CancelledError):
            await waiting


class BridgeInboundPatchTests(unittest.TestCase):
    def test_join_history_patch_is_local_idempotent_and_preserves_mam(self):
        source = '''from asyncio import Lock
class Room:
    async def __old_school_history(self):
        if since is None:
            pass
        for h_msg in self.archive.get_all(
            start_date=start_date, end_date=None, last_page_n=maxstanzas
        ):
            msg = h_msg.stanza_component_ns
            self.xmpp.send(msg, False)
    async def send_mam(self):
        preserve_this_mam_handler()
'''
        result = patcher.room_source(source)
        self.assertEqual(patcher.room_source(result), result)
        self.assertIn("maxstanzas = 50", result)
        self.assertIn("await _can_inbound_yield(0)", result)
        self.assertIn("history = list(self.archive.get_all(", result)
        self.assertIn("for h_msg in history:", result)
        previous = result.replace("# CAN bounded join history v2", "# CAN bounded join history v1")
        previous = previous.replace("history = list(self.archive.get_all(",
                                    "for h_msg in self.archive.get_all(")
        previous = previous.replace("        ))\n        for h_msg in history:\n", "        ):\n")
        self.assertEqual(patcher.room_source(previous), result)
        self.assertEqual(result[result.index("    async def send_mam"):],
                         source[source.index("    async def send_mam"):])

    def test_python_patch_is_guarded_idempotent_and_parses(self):
        source = ("import asyncio\ndef make_sync():\n    def wrapper():\n"
                  "        return future.result()\n")
        changed = patcher.python_source(source)
        self.assertIn("except CallbackCancelled:", changed)
        self.assertIn("_install_inbound(Session, whatsapp)", changed)
        self.assertEqual(patcher.python_source(changed), changed)
        with self.assertRaises(ValueError):
            patcher.python_source("import asyncio\n")

    def test_failed_patch_does_not_write_partial_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            for name in ("session.go", "event.go", "session.py"):
                (package / name).write_text("unsupported", encoding="utf-8")
            with self.assertRaises((ValueError, FileNotFoundError)):
                patcher.patch(root, ROOT / "tools")
            self.assertEqual((package / "session.go").read_text(), "unsupported")
            self.assertFalse((package / "inbound.py").exists())
