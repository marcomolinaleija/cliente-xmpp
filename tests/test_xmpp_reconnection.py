from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cliente_xmpp.config.settings import ConnectionSettings
from cliente_xmpp.xmpp.client import BridgeXmppClient


class XmppDiagnosticBackpressureTests(unittest.TestCase):
    def test_normal_diagnostics_do_not_block_loop_on_an_unread_stdout_pipe(self):
        script = '''import asyncio,sys
from pathlib import Path
from cliente_xmpp.xmpp.client import BridgeXmppClient
root = Path(sys.argv[1])
async def produce():
    (root / "ready").write_text("ready")
    for _ in range(128):
        BridgeXmppClient._debug_whatsapp("fictional diagnostic " * 512)
        await asyncio.sleep(0)
async def heartbeat():
    await asyncio.sleep(.05)
    (root / "heartbeat").write_text("alive")
async def run():
    await asyncio.gather(produce(), heartbeat())
asyncio.run(run())
'''
        with tempfile.TemporaryDirectory(prefix="can-stdout-fixture-") as temp:
            root = Path(temp)
            child = subprocess.Popen(
                [sys.executable, "-c", script, temp], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while not (root / "ready").exists() and child.poll() is None:
                    self.assertLess(time.monotonic(), deadline, "fixture did not initialize")
                    time.sleep(.01)
                self.assertTrue((root / "ready").exists(), "fixture failed before producing")
                deadline = time.monotonic() + 1
                while not (root / "heartbeat").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(
                    (root / "heartbeat").exists(),
                    "synchronous diagnostic output stalled the XMPP owner loop",
                )
                self.assertEqual(child.wait(timeout=3), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
                child.stdout.close()

    def test_diagnostics_remain_available_when_debug_logging_is_enabled(self):
        with self.assertLogs("cliente_xmpp.xmpp.client", level="DEBUG") as logs:
            BridgeXmppClient._debug_whatsapp("fictional diagnostic")
        self.assertIn("[cliente-xmpp][whatsapp] fictional diagnostic", logs.output[0])


class XmppReconnectionTests(unittest.IsolatedAsyncioTestCase):
    def client(self, host="example.invalid"):
        return BridgeXmppClient(
            ConnectionSettings(jid="fixture@example.invalid", host=host, port=5222),
            "fixture-unused-password", Mock(),
        )

    async def test_starttls_reset_does_not_leave_a_finished_attempt_blocking_reconnect(self):
        client = self.client()
        loop = asyncio.get_running_loop()
        client.transport = Mock()

        async def reset_during_tls(*_args, **_kwargs):
            # The real Slixmpp methods clear transport, then leave a failed TLS
            # Future installed when disconnect() runs without a transport.
            client.connection_lost(ConnectionResetError("fixture TLS peer reset"))
            raise ConnectionResetError("fixture TLS peer reset")

        with (
            patch.object(loop, "start_tls", side_effect=reset_during_tls),
            patch.object(loop, "call_later") as later,
            patch.object(client, "connect") as connect,
        ):
            self.assertFalse(await client.start_tls())
            await asyncio.sleep(0)
            self.assertFalse(client.is_connected())
            self.assertTrue(client._current_connection_attempt.done())
            later.assert_called_once()
            self.assertEqual(later.call_args.args[0], 3.0)
            later.call_args.args[1]()
            connect.assert_called_once_with("example.invalid", 5222)
            self.assertFalse(client._reconnect_scheduled)

    async def test_finished_cancelled_failed_or_missing_attempt_all_allow_retry(self):
        for host in ("example.invalid", ""):
            for outcome in ("success", "cancelled", "failed", "none"):
                with self.subTest(host=host, outcome=outcome):
                    client = self.client(host)
                    attempt = asyncio.get_running_loop().create_future()
                    if outcome == "cancelled":
                        attempt.cancel()
                    elif outcome == "failed":
                        attempt.set_exception(ConnectionResetError("fixture reset"))
                        attempt.exception()
                    else:
                        attempt.set_result(None)
                    client._current_connection_attempt = None if outcome == "none" else attempt
                    with (
                        patch.object(client.loop, "call_later") as later,
                        patch.object(client, "connect") as connect,
                    ):
                        client._schedule_reconnect()
                        later.call_args.args[1]()
                        if host:
                            connect.assert_called_once_with(host, 5222)
                        else:
                            connect.assert_called_once_with()

    async def test_pending_attempt_or_live_transport_does_not_open_a_duplicate(self):
        for connected in (False, True):
            with self.subTest(connected=connected):
                client = self.client()
                pending = asyncio.get_running_loop().create_future()
                client.transport = Mock() if connected else None
                client._current_connection_attempt = None if connected else pending
                with (
                    patch.object(client.loop, "call_later") as later,
                    patch.object(client, "connect") as connect,
                ):
                    client._schedule_reconnect()
                    later.call_args.args[1]()
                    connect.assert_not_called()
                self.assertFalse(pending.done())
                pending.cancel()

    async def test_user_disconnect_or_failed_auth_cannot_restart_from_an_old_timer(self):
        for stop in ("request_disconnect", "_on_failed_auth"):
            with self.subTest(stop=stop):
                client = self.client()
                with (
                    patch.object(client.loop, "call_later") as later,
                    patch.object(client.loop, "call_soon_threadsafe"),
                    patch.object(client, "connect") as connect,
                ):
                    client._schedule_reconnect()
                    retry = later.call_args.args[1]
                    if stop == "request_disconnect":
                        client.request_disconnect()
                    else:
                        client._on_failed_auth(None)
                    retry()
                    client._schedule_reconnect()
                    later.assert_called_once()
                    connect.assert_not_called()
                    self.assertTrue(client._disconnect_requested)

    async def test_disconnect_burst_schedules_only_one_retry(self):
        client = self.client()
        with patch.object(client.loop, "call_later") as later:
            client._on_disconnected(None)
            client._on_disconnected(None)
            later.assert_called_once()


class XmppTlsReconnectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_starttls_peer_reset_opens_another_tcp_connection(self):
        accepted = []
        reconnected, release = asyncio.Event(), asyncio.Event()
        handlers = set()

        async def peer(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                accepted.append(True)
                if len(accepted) > 1:
                    reconnected.set()
                    await release.wait()
                    return
                await reader.readuntil(b">")
                writer.write(
                    b"<stream:stream from='example.invalid' id='fixture' xmlns='jabber:client' "
                    b"xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>"
                    b"<stream:features><starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'>"
                    b"<required/></starttls></stream:features>"
                )
                await writer.drain()
                await reader.readuntil(b"/>")
                writer.write(b"<proceed xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>")
                await writer.drain()
                # Receive a real TLS ClientHello, then drop the peer mid-handshake.
                self.assertTrue(await reader.read(4096))
                writer.transport.abort()
            finally:
                writer.close()
                handlers.discard(task)

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = BridgeXmppClient(
            ConnectionSettings(jid="fixture@example.invalid", host="127.0.0.1", port=port),
            "fixture-unused-password", Mock(),
        )
        schedule = client._schedule_reconnect
        try:
            # Shorten only the existing timer; TCP, STARTTLS and connect() are real.
            with patch.object(client, "_schedule_reconnect", side_effect=lambda: schedule(0.02)):
                client.connect("127.0.0.1", port)
                await asyncio.wait_for(reconnected.wait(), 3)
                self.assertEqual(len(accepted), 2)
        finally:
            client.del_event_handler("disconnected", client._on_disconnected)
            client._disconnect_requested = True
            client.cancel_connection_attempt()
            await client.disconnect(wait=0, ignore_send_queue=True)
            if client._run_out_filters:
                client._run_out_filters.cancel()
                await asyncio.gather(client._run_out_filters, return_exceptions=True)
            release.set()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*handlers, return_exceptions=True)
