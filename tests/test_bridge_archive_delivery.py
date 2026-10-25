from __future__ import annotations

import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.bridge_archive_delivery import delivery_scope, track
from tools.bridge_health import respond
from tools.patch_slidge_archive_delivery import (
    archive_source,
    gateway_source,
    participant_source,
    patch_package,
    room_source,
    session_source,
)

ARCHIVE = '''class Archive:
    def get_all(
        self, last_page_n=None,
    ):
        with self.__store.session() as orm:
            yield from self.__store.get_messages(
                orm, self.room.id, last_page_n=last_page_n,
            )

    async def send_metadata(self, iq):
        pass
'''
PARTICIPANT = '''class Participant:
    def _send(self, stanza, archive_only=False, legacy_msg_id=None):
        if True:
            if True:
                self.muc.archive.add(stanza, self, archive_only, legacy_msg_id)
        return stanza

    def mucadmin_item(self):
        pass
'''
GATEWAY = '''class Gateway:
    def __init__(self):
        self.loop.set_exception_handler(self.__exception_handler)
'''
ROOM = '''import asyncio
class Room:
    async def echo(self):
        async with self.session.lock(("can-group-echo", str(self.jid))):
            await self.archive.add_async()
'''
SESSION = "import asyncio\nclass Session:\n" + "".join(
    f"    async def {name}(self):\n        pass\n"
    for name in ("on_wa_msg_plain", "on_wa_msg_attachment", "on_wa_msg_edit",
                 "on_wa_msg_revoke", "on_wa_msg_reaction", "on_wa_msg_poll",
                 "on_wa_receipt", "__transcribe_and_send")
)


class ArchiveDeliveryPatchTests(unittest.TestCase):
    def test_transformations_parse_and_are_idempotent(self):
        for transform, source in ((archive_source, ARCHIVE),
                                  (participant_source, PARTICIPANT),
                                  (room_source, ROOM),
                                  (session_source, SESSION),
                                  (gateway_source, GATEWAY)):
            with self.subTest(transform=transform.__name__):
                output = transform(source)
                ast.parse(output)
                self.assertEqual(transform(output), output)

    def test_reader_releases_session_before_yield_and_preserves_query(self):
        source = archive_source(ARCHIVE)
        self.assertIn("last_page_n=last_page_n", source)
        self.assertIn("            messages = list(", source)
        self.assertIn("        yield from messages", source)

    def test_delivery_shares_echo_lock_and_archives_before_send(self):
        source = participant_source(PARTICIPANT)
        self.assertIn('self.session.lock(("can-group-echo", str(self.muc.jid)))', source)
        self.assertLess(source.index("await self.muc.archive.add_async"),
                        source.index("outgoing.send()"))
        self.assertIn("snapshot = copy(stanza)", source)
        self.assertIn("task.add_done_callback(pending.discard)", source)
        self.assertIn("_can_archive_failed = True", source)

    def test_unknown_and_duplicate_shapes_fail_closed(self):
        for transform, source in ((archive_source, ARCHIVE),
                                  (participant_source, PARTICIPANT),
                                  (room_source, ROOM),
                                  (session_source, SESSION),
                                  (gateway_source, GATEWAY)):
            for value in ("", source + source):
                with self.subTest(transform=transform.__name__):
                    with self.assertRaises(ValueError):
                        transform(value)

    def test_validates_every_source_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session = root / "slidge_whatsapp/session.py"
            session.parent.mkdir()
            session.write_text(SESSION, encoding="utf-8")
            for name, source in (("group/archive.py", ARCHIVE),
                                 ("group/participant.py", PARTICIPANT),
                                 ("group/room.py", ROOM),
                                 ("core/gateway.py", "unknown")):
                path = root / "slidge" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual((root / "slidge/group/archive.py").read_text(), ARCHIVE)

    def test_image_is_exact_offline_overlay_with_real_baseline_and_healthcheck(self):
        source = (Path(__file__).resolve().parents[1] /
                  "tools/Dockerfile.bridge-archive-delivery").read_text()
        for anchor in ("sha256:a772d384", "RUN --network=none", '--expect-blocking',
                       "HEALTHCHECK --interval=30s", "--retries=3", "check(smoke)"):
            self.assertIn(anchor, source)
        for forbidden in ("pip install", "go build", "--network=host"):
            self.assertNotIn(forbidden, source)


class BridgeHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_probe_cannot_escape_to_fatal_gateway_handler(self):
        gateway = SimpleNamespace(is_connected=lambda: False)
        writer = SimpleNamespace(write=lambda _: None,
                                 drain=AsyncMock(side_effect=ConnectionResetError()),
                                 close=lambda: None,
                                 wait_closed=AsyncMock(side_effect=BrokenPipeError()))
        await respond(gateway, None, writer)
        writer.wait_closed.assert_awaited_once()

    async def test_callback_cancellation_joins_delivery_cleanup(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def job():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.set()

        @delivery_scope
        async def callback():
            track(asyncio.create_task(job()))

        task = asyncio.create_task(callback())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())

    async def test_callback_waits_for_its_own_jobs_without_blocking_other_callbacks(self):
        release = asyncio.Event()

        @delivery_scope
        async def slow():
            track(asyncio.create_task(release.wait()))

        @delivery_scope
        async def fast():
            track(asyncio.create_task(asyncio.sleep(0)))

        pending = asyncio.create_task(slow())
        await asyncio.sleep(0)
        await asyncio.wait_for(fast(), 1)
        self.assertFalse(pending.done())
        release.set()
        await pending

    async def test_callback_propagates_archive_failure_instead_of_silent_success(self):
        async def failed():
            raise RuntimeError("fixture archive failure")

        @delivery_scope
        async def callback():
            track(asyncio.create_task(failed()))

        with self.assertRaisesRegex(RuntimeError, "fixture archive failure"):
            await callback()

    async def test_health_requires_loop_and_xmpp_not_whatsapp(self):
        class Gateway(SimpleNamespace):
            def __getitem__(self, _):
                return self.plugin

        for state in ("healthy", "disconnected", "archive_failed", "timeout"):
            with self.subTest(state=state):
                ping = AsyncMock(side_effect=TimeoutError() if state == "timeout" else None)
                gateway = Gateway(is_connected=lambda state=state: state != "disconnected",
                                  infer_real_domain=lambda: "example.invalid",
                                  boundjid="bridge.example.invalid",
                                  plugin=SimpleNamespace(ping=ping),
                                  _can_archive_failed=state == "archive_failed")
                output = []
                writer = SimpleNamespace(write=output.append, drain=AsyncMock(),
                                         close=lambda: None, wait_closed=AsyncMock())
                await respond(gateway, None, writer)
                expected = {"healthy": b"healthy\n", "disconnected": b"xmpp_disconnected\n",
                            "archive_failed": b"archive_failed\n",
                            "timeout": b"xmpp_probe_failed\n"}
                self.assertEqual(output, [expected[state]])
                if state in ("healthy", "timeout"):
                    ping.assert_awaited_once()
                else:
                    ping.assert_not_awaited()
