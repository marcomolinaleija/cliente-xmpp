from __future__ import annotations

import asyncio
import json
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from socket import EAI_NONAME, gaierror
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

import wx
from aiohttp import (
    ClientSession,
    TCPConnector,
    ThreadedResolver,
    WSMsgType,
    WSServerHandshakeError,
    web,
)
from slixmpp import JID, Iq
from slixmpp.exceptions import IqError, IqTimeout, XMPPError
from voice_fakes import AudioBackend

from cliente_xmpp.audio.live_call import SILENCE, LiveCallAudio
from cliente_xmpp.audio.process import hidden_subprocess_kwargs
from cliente_xmpp.audio.voice_timing import AudioCadence
from cliente_xmpp.models.chat import Chat
from cliente_xmpp.models.voice import VoiceAudioSettings, VoiceDevice
from cliente_xmpp.ui.events import EVT_XMPP_EVENT
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.voice_controller import VoiceController
from cliente_xmpp.ui.voice_dialog import VoiceCallDialog
from cliente_xmpp.xmpp.client import BridgeXmppClient, XmppService
from cliente_xmpp.xmpp.events import VoiceChannelChanged, VoiceStateChanged
from cliente_xmpp.xmpp.voice import NAMESPACE, PROTOCOL, VoiceTransport, validate_grant
from cliente_xmpp.xmpp.voice_worker import IsolatedVoiceTransport
from tools.bridge_voice import (
    CapturePacer,
    FlowMetrics,
    MediaLoop,
    Tokens,
    VoiceGateway,
    secure_url,
)
from tools.patch_slidge_voice import native_session, python_gateway, python_session

COMPONENT = "whatsapp.example.invalid"
RESOURCE = "fixture@example.invalid/windows"


class IQ(dict):
    def __init__(self, *, from_jid=RESOURCE, to=COMPONENT, kind="get"):
        super().__init__({"from": JID(from_jid), "to": JID(to), "type": kind})
        self.xml = ET.Element("iq")
        self.response = None

    def append(self, element):
        self.xml.append(element)

    def reply(self):
        self.response = IQ(from_jid=str(self["to"]), to=str(self["from"]), kind="result")
        return self.response

    def send(self):
        pass


def registered():
    contact = SimpleNamespace(legacy_id="fixture@s.whatsapp.net",
                              jid=JID("fixture@" + COMPONENT))
    async def username(legacy):
        return legacy.split("@", 1)[0]
    return SimpleNamespace(
        logged=True, user_jid=JID("fixture@example.invalid"),
        user=SimpleNamespace(legacy_module_data={"device_id": "fixture-device"}),
        contacts=SimpleNamespace(by_jid_only_if_exists=lambda jid: (
            contact if str(jid) == "fixture@" + COMPONENT else None
        ), legacy_id_to_jid_username=username),
    )


class VoiceFlowMetricsTests(unittest.TestCase):
    def test_boundaries_forwarding_delay_loop_stall_and_reset_without_samples(self):
        now = 0.
        reports = []
        metrics = FlowMetrics(clock=lambda: now, report=reports.append)
        metrics.received("client", 640)
        metrics.tick(20)
        self.assertEqual(reports, [])
        metrics.state("active")
        metrics.received("client", 640)
        metrics.forwarded("native", 640, .003)
        now = .2
        metrics.received("client", 640)
        metrics.received("native", 1920)
        metrics.forwarded("client", 1920, .007)
        now = 10.1
        metrics.tick(.12)
        report = reports[-1]
        self.assertEqual(report["client_received_bytes"], 1280)
        self.assertEqual(report["native_forwarded_bytes"], 640)
        self.assertEqual(report["native_received_bytes"], 1920)
        self.assertEqual(report["client_forwarded_bytes"], 1920)
        self.assertEqual(report["client_gap_max_ms"], 200)
        self.assertEqual(report["native_send_max_ms"], 3)
        self.assertEqual(report["client_send_max_ms"], 7)
        self.assertEqual(report["loop_delay_max_ms"], 120)
        self.assertEqual(report["native_idle_ms"], 9900)
        metrics.state("ended")
        self.assertTrue(reports[-1]["final"])
        self.assertEqual(len(reports), 2)
        metrics.state("ended")
        self.assertEqual(len(reports), 2)
        metrics.state("active")
        metrics.publish()
        self.assertEqual(reports[-1]["client_received_bytes"], 0)
        self.assertIsNone(reports[-1]["native_idle_ms"])
        self.assertTrue(all(isinstance(x, (int, str, bool, type(None)))
                            for r in reports for x in r.values()))


class VoiceCapturePacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_burst_keeps_every_block_and_native_cadence_is_bounded(self):
        now, sent = 0., []
        done = asyncio.Event()

        async def sleep(delay):
            nonlocal now
            now += delay

        async def send(data):
            sent.append((now, data))
            if len(sent) == 18:
                done.set()

        metrics = FlowMetrics(clock=lambda: now, report=lambda _: None)
        metrics.state("active")
        pacer = CapturePacer(send, metrics=metrics, clock=lambda: now, sleep=sleep)
        pacer.state("active", "fixture-call")
        expected = [bytes([i+1])*640 for i in range(18)]
        for data in expected:
            pacer.push(data)
        task = asyncio.create_task(pacer.run())
        try:
            await asyncio.wait_for(done.wait(), 2)
            self.assertEqual([data for _, data in sent], expected)
            self.assertAlmostEqual(sent[0][0], .06)
            self.assertTrue(all(b[0]-a[0] >= .017999
                                for a, b in zip(sent, sent[1:], strict=False)))
            self.assertEqual(metrics.counts["capture_queue_dropped_packets"], 0)
            self.assertEqual(metrics.counts["capture_queue_packets"], 0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(25):
            pacer.push(bytes(640))
        self.assertEqual(pacer.queue.qsize(), 20)
        self.assertEqual(metrics.counts["capture_queue_dropped_packets"], 5)
        pacer.state("ended")
        self.assertEqual(metrics.counts["capture_revoked_packets"], 20)
        self.assertEqual(metrics.counts["capture_queue_packets"], 0)

    async def test_repeated_slow_writes_recover_backlog_without_drops_or_bursts(self):
        now, next_input, count = 0., .02, 0
        sent, queues = [], []
        metrics = FlowMetrics(clock=lambda: now, report=lambda _: None)
        metrics.state("active")

        def advance(delay):
            nonlocal now, next_input
            end = now + delay
            while next_input <= end + 1e-9:
                now = next_input
                pacer.push(bytes(640))
                next_input += .02
            now = end

        async def sleep(delay):
            advance(delay)

        class Finished(Exception):
            pass

        async def send(data):
            nonlocal count
            count += 1
            sent.append(now)
            if count in range(50, 500, 50):
                advance(.1)
            if count % 50 == 49:
                queues.append(pacer.queue.qsize())
            if count == 500:
                raise Finished()

        pacer = CapturePacer(send, metrics=metrics, clock=lambda: now, sleep=sleep)
        pacer.state("active", "fixture-call")
        pacer.push(bytes(640))
        with self.assertRaises(Finished):
            await pacer.run()
        self.assertEqual(metrics.counts["capture_queue_dropped_packets"], 0)
        self.assertLessEqual(max(queues), 4)
        self.assertTrue(all(b-a >= .017999 for a, b in zip(sent, sent[1:], strict=False)))

    async def test_held_audio_is_revoked_on_hangup_logout_or_another_call(self):
        for operation in ("hangup", "logout", "new_call"):
            with self.subTest(operation=operation):
                entered, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
                clock = [0.]
                permitted = [True]
                sent = []

                async def sleep(delay, clock=clock, entered=entered, release=release):
                    entered.set()
                    await release.wait()
                    clock[0] += delay

                async def send(data, sent=sent, done=done):
                    sent.append(data)
                    done.set()

                pacer = CapturePacer(send, allowed=lambda p=permitted: p[0],
                                     clock=lambda c=clock: c[0], sleep=sleep)
                pacer.state("active", "old-call")
                pacer.push(bytes([1])*640)
                task = asyncio.create_task(pacer.run())
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    if operation == "logout":
                        permitted[0] = False
                    else:
                        pacer.state("ended")
                        if operation == "new_call":
                            pacer.state("active", "new-call")
                            pacer.push(bytes([2])*640)
                    release.set()
                    if operation == "new_call":
                        await asyncio.wait_for(done.wait(), 2)
                        self.assertEqual(sent, [bytes([2])*640])
                    elif operation == "logout":
                        await asyncio.wait_for(task, 2)
                        self.assertEqual(sent, [])
                    else:
                        await asyncio.sleep(0)
                        await asyncio.sleep(0)
                        self.assertEqual(sent, [])
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class VoiceAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_grant_failures_close_transport_and_report_without_unhandled_task(self):
        for error in (IqTimeout(Iq()), IqError(Iq()), TimeoutError(), ValueError()):
            with self.subTest(error=type(error).__name__):
                client = SimpleNamespace(
                    _voice=None, _whatsapp_session_ready=True, _emit=Mock(),
                    boundjid=JID("account@example.invalid"),
                    _configured_local_whatsapp_component=lambda: COMPONENT,
                    _configured_remote_whatsapp_component=lambda: "",
                )
                voice = SimpleNamespace(open=AsyncMock(side_effect=error), close=AsyncMock())
                with patch("cliente_xmpp.xmpp.client.VoiceTransport", return_value=voice):
                    await BridgeXmppClient.start_voice(client, "fixture-session")
                voice.close.assert_awaited_once()
                event = client._emit.call_args.args[0]
                self.assertFalse(event.connected)
                self.assertIsNone(client._voice)
                self.assertTrue(client._whatsapp_session_ready)

    async def test_old_grant_timeout_cannot_disable_replacement_voice(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail_later():
            entered.set()
            await release.wait()
            raise IqTimeout(Iq())

        client = SimpleNamespace(
            _voice=None, _whatsapp_session_ready=True, _emit=Mock(),
            _configured_local_whatsapp_component=lambda: COMPONENT,
            _configured_remote_whatsapp_component=lambda: "",
        )
        old = SimpleNamespace(open=fail_later, close=AsyncMock())
        new = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
        with patch("cliente_xmpp.xmpp.client.VoiceTransport", side_effect=[old, new]):
            task = asyncio.create_task(BridgeXmppClient.start_voice(client, "old"))
            await entered.wait()
            await BridgeXmppClient.start_voice(client, "new")
            release.set()
            await task
        self.assertIs(client._voice, new)
        client._emit.assert_not_called()

    async def test_pending_activation_cannot_survive_disable_disconnect_or_replacement(self):
        for operation in ("disable", "disconnect", "replace"):
            with self.subTest(operation=operation):
                entered, release = asyncio.Event(), asyncio.Event()

                async def close_old(entered=entered, release=release):
                    entered.set()
                    await release.wait()

                old = SimpleNamespace(close=close_old, session_id="old", task=None)
                client = SimpleNamespace(
                    _voice=old, _whatsapp_session_ready=True,
                    _configured_local_whatsapp_component=lambda: COMPONENT,
                    _configured_remote_whatsapp_component=lambda: "",
                    _emit=Mock(), disconnect=Mock(),
                )
                replacement = SimpleNamespace(open=AsyncMock())
                with patch("cliente_xmpp.xmpp.client.VoiceTransport",
                           return_value=replacement) as factory:
                    first = asyncio.create_task(BridgeXmppClient.start_voice(client, "first"))
                    await entered.wait()
                    second = None
                    try:
                        if operation == "disable":
                            service = XmppService(Mock())
                            service._client, service._loop = client, asyncio.get_running_loop()
                            service.stop_voice("first")
                            await asyncio.sleep(0)
                            await asyncio.sleep(0)
                            self.assertIsNone(client._voice_request)
                        elif operation == "disconnect":
                            BridgeXmppClient.request_disconnect(client)
                        else:
                            second = asyncio.create_task(
                                BridgeXmppClient.start_voice(client, "second"),
                            )
                            await asyncio.sleep(0)
                    finally:
                        release.set()
                        await asyncio.gather(first, *([second] if second else []))
                    if operation == "replace":
                        factory.assert_called_once_with(
                            client, COMPONENT, client._emit, session_id="second",
                        )
                        replacement.open.assert_awaited_once()
                    else:
                        factory.assert_not_called()
                        self.assertIs(client._voice, old)

    def test_old_connection_cleanup_cannot_close_new_profiles_voice(self):
        service = XmppService(Mock())
        closed = []

        class Voice:
            def __init__(self, name):
                self.name = name

            async def close(self):
                closed.append(self.name)

        old = Mock(_voice=Voice("old"))
        old.__getitem__ = Mock(return_value=Mock())
        newer = SimpleNamespace(_voice=Voice("new"))
        newer_loop = object()

        class Loop:
            def run_forever(self):
                service._client, service._loop = newer, newer_loop

            def run_until_complete(self, coroutine):
                try:
                    coroutine.send(None)
                except StopIteration:
                    pass

        loop = Loop()
        old_transfer, new_transfer = Mock(), Mock()
        old_transfer.get_loop.return_value = loop
        new_transfer.get_loop.return_value = newer_loop
        service._file_transfer_tasks = {old_transfer, new_transfer}

        async def gather(*tasks, **_kwargs):
            self.assertEqual(tasks, (old_transfer,))
            return []

        with patch("cliente_xmpp.xmpp.client.asyncio.new_event_loop", return_value=loop), \
                patch("cliente_xmpp.xmpp.client.asyncio.set_event_loop"), \
                patch("cliente_xmpp.xmpp.client.asyncio.gather", side_effect=gather), \
                patch("cliente_xmpp.xmpp.client.BridgeXmppClient", return_value=old):
            service._run_client(SimpleNamespace(host="", port=5222), "fixture-unused-password")
        self.assertEqual(closed, ["old"])
        self.assertIs(service._client, newer)
        self.assertIs(service._loop, newer_loop)
        old_transfer.cancel.assert_called_once()
        new_transfer.cancel.assert_not_called()

    def test_tokens_expire_rotate_once_and_revoke(self):
        now = [1.0]
        tokens = Tokens(lambda: now[0])
        session = registered()
        first = tokens.issue(session, RESOURCE)
        token = tokens.issue(session, RESOURCE)
        self.assertIsNone(tokens.consume(first))
        self.assertEqual(tokens.consume(token).resource, RESOURCE)
        self.assertIsNone(tokens.consume(token))
        token = tokens.issue(session, RESOURCE)
        now[0] += 31
        self.assertIsNone(tokens.consume(token))
        for change in ("logout", "device", "revoke"):
            with self.subTest(change=change):
                session = registered()
                token = tokens.issue(session, RESOURCE)
                if change == "logout":
                    session.logged = False
                elif change == "device":
                    session.user.legacy_module_data["device_id"] = "another-device"
                else:
                    tokens.revoke(session)
                self.assertIsNone(tokens.consume(token))
        other = registered()
        other.user_jid = JID("other@example.invalid")
        other.user.legacy_module_data["device_id"] = "other-device"
        left = tokens.issue(session, RESOURCE)
        right = tokens.issue(other, "other@example.invalid/mac")
        self.assertIs(tokens.consume(left).session, session)
        self.assertIs(tokens.consume(right).session, other)

    async def test_iq_requires_registered_owner_full_resource_and_component(self):
        session = registered()
        gateway = SimpleNamespace(boundjid=JID(COMPONENT),
                                  get_session_from_jid=lambda jid: session)
        voice = VoiceGateway(gateway, url="wss://localhost/voice/v1", socket=Path("unused"))
        voice.runner = object()
        for source, target, kind in (
            ("intruder@example.invalid/x", COMPONENT, "get"),
            ("fixture@example.invalid", COMPONENT, "get"),
            (RESOURCE, "fixture@" + COMPONENT, "get"),
            (RESOURCE, COMPONENT, "set"),
        ):
            with self.subTest(source=source, target=target, kind=kind):
                iq = IQ(from_jid=source, to=target, kind=kind)
                iq.append(ET.Element(f"{{{NAMESPACE}}}open"))
                with self.assertRaises(XMPPError):
                    await voice.authorize(iq)
        iq = IQ()
        iq.append(ET.Element(f"{{{NAMESPACE}}}open"))
        await voice.authorize(iq)
        url, token = validate_grant(iq.response, COMPONENT)
        self.assertEqual(url, voice.url)
        self.assertEqual(voice.tokens.consume(token).device, "fixture-device")
        self.assertNotIn("fixture-device", ET.tostring(iq.response.xml).decode())
        grant = voice.tokens.consume(voice.tokens.issue(session, RESOURCE))
        command = json.loads(await voice.translate(grant, json.dumps({
            "id": "fixture-command", "action": "dial", "peer": "fixture@" + COMPONENT,
        })))
        self.assertEqual(command["peer"], "fixture@s.whatsapp.net")
        for peer in ("fixture@s.whatsapp.net", "missing@" + COMPONENT,
                     "fixture@" + COMPONENT + "/resource"):
            with self.subTest(peer=peer), self.assertRaises(ValueError):
                await voice.translate(grant, json.dumps({"action": "dial", "peer": peer}))

    def test_grants_refuse_plaintext_redirect_style_urls_and_foreign_sender(self):
        for url in ("ws://localhost/voice/v1", "wss://user:pass@localhost/voice/v1",
                    "wss://localhost/voice/v1?token=secret", "wss://localhost/wrong"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                secure_url(url)
        result = IQ(from_jid="other.example.invalid")
        with self.assertRaises(ValueError):
            validate_grant(result, COMPONENT)

    def test_client_live_state_does_not_emit_history_and_audio_is_bounded(self):
        events = []
        client = SimpleNamespace(boundjid=JID(RESOURCE))
        voice = VoiceTransport(client, COMPONENT, events.append)
        voice.ws = object()
        state = {"type": "state", "revision": 3, "state": "active",
                 "direction": "incoming", "call_id": "fixture-call", "peer": "fixture@lid"}
        voice._message(json.dumps(state))
        voice._message(json.dumps({**state, "revision": 2, "state": "incoming_ringing"}))
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], VoiceStateChanged)
        for value in range(10):
            voice.feed(bytes([value]) * 640)
        self.assertEqual(voice.capture.qsize(), 6)
        self.assertEqual(voice.capture.get_nowait(), bytes([4]) * 640)
        voice._message(json.dumps({**state, "revision": 4, "state": "ended"}))
        self.assertTrue(voice.capture.empty())
        with self.assertRaises(ValueError):
            voice.feed(b"bad size")

    def test_lifecycle_patch_is_strict_and_idempotent(self):
        source = '''package whatsapp
import "codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp/media"
func (s *Session) setClient(client *whatsmeow.Client) {{
\ts.clientMutex.Lock()
\ts.client = client
\ts.clientMutex.Unlock()
}}
func (s *Session) clearClient(client *whatsmeow.Client) {{
\ts.clientMutex.Lock()
\tif s.client == client {{
\t\ts.canCancelCallRecoveries()
\t\ts.client = nil
\t}}
\ts.clientMutex.Unlock()
}}
func (s *Session) handleEvent(evt any) {{
\ts.canTraceCallSignal(evt)
}}
'''.replace("{{", "{").replace("}}", "}")
        for transform, original in (
            (native_session, source),
            (python_gateway, "from .generated import whatsapp\n        self.whatsapp.Init()\n"),
            (python_session, "from __future__ import annotations\n"
             "        self.whatsapp.Disconnect()  # type:ignore[no-untyped-call]\n"),
        ):
            with self.subTest(transform=transform.__name__):
                result = transform(original)
                self.assertEqual(result, transform(result))
                with self.assertRaises(ValueError):
                    transform("changed upstream")


class VoiceTLSIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cert, cls.key = root / "cert.pem", root / "key.pem"
        config = root / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="ascii")
        openssl = Path(sys.prefix) / "Library/bin/openssl.exe"
        subprocess.run([
            str(openssl) if openssl.is_file() else "openssl", "req", "-x509", "-newkey",
            "rsa:2048", "-nodes", "-days", "1", "-keyout", str(cls.key),
            "-config", str(config),
            "-out", str(cls.cert), "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            **hidden_subprocess_kwargs())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.session = registered()
        self.gateway = SimpleNamespace(boundjid=JID(COMPONENT),
                                       get_session_from_jid=lambda jid: self.session)
        self.voice = VoiceGateway(self.gateway, url="wss://localhost/voice/v1",
                                  socket=Path("not-used-by-test-adapter"))
        self.backend_headers = None
        self.native_audio = []

        async def backend(request):
            self.backend_headers = dict(request.headers)
            ws = web.WebSocketResponse(protocols=(PROTOCOL,))
            await ws.prepare(request)
            await ws.send_json({"type": "state", "revision": 0, "state": "idle"})
            revision = 0
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data["action"] == "dial":
                        self.assertEqual(data["peer"], "fixture@s.whatsapp.net")
                        state = "active"
                    else:
                        self.assertEqual(data["call_id"], "fixture-call")
                        state = "ended"
                    revision += 1
                    await ws.send_json({"type": "state", "revision": revision, "state": state,
                                        "call_id": "fixture-call", "direction": "outgoing",
                                        "peer_pn": "fixture@s.whatsapp.net"})
                    await ws.send_json({"type": "result", "id": data["id"]})
                elif message.type == WSMsgType.BINARY:
                    self.native_audio.append(message.data)
                    await ws.send_bytes(message.data)
            return ws

        app = web.Application()
        app.router.add_get("/session", backend)
        self.backend_runner = web.AppRunner(app, access_log=None)
        await self.backend_runner.setup()
        site = web.TCPSite(self.backend_runner, "127.0.0.1", 0)
        await site.start()
        backend_port = site._server.sockets[0].getsockname()[1]

        async def connect_native(http, grant):
            # Windows has no asyncio UnixConnector; exercise the same proxy against
            # a loopback fixture. The native Unix socket has separate Go coverage.
            return await http.ws_connect(f"http://127.0.0.1:{backend_port}/session",
                                         protocols=(PROTOCOL,),
                                         headers={"X-CAN-Device": grant.device,
                                                  "X-CAN-Resource": grant.resource})

        self.voice.connect_native = connect_native
        self.voice.native_http = ClientSession
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        await self.voice.start("127.0.0.1", 0, tls)
        port = next(iter(self.voice.runner.sites))._server.sockets[0].getsockname()[1]
        self.voice.url = f"wss://127.0.0.1:{port}/voice/v1"
        self.events = []
        fixture = self

        class Request(IQ):
            async def send(self, **_kwargs):
                await fixture.voice.authorize(self)
                return self.response

        client = SimpleNamespace(boundjid=JID(RESOURCE),
                                 settings=SimpleNamespace(use_tls=True, ca_file=str(self.cert)),
                                 Iq=Request)
        self.client = VoiceTransport(client, COMPONENT, self.events.append)

    async def asyncTearDown(self):
        await self.client.close()
        await self.voice.close()
        await self.backend_runner.cleanup()

    async def test_hostname_tls_uses_system_dns_even_when_optional_async_resolver_fails(self):
        self.voice.url = self.voice.url.replace("127.0.0.1", "localhost")
        # The fault belongs to the client: do not also poison the fixture proxy's connector.
        self.voice.native_http = lambda: ClientSession(
            connector=TCPConnector(resolver=ThreadedResolver()),
        )
        with patch("aiohttp.connector.DefaultResolver", side_effect=AssertionError(
            "optional async DNS must not choose voice resolution",
        )), patch("cliente_xmpp.xmpp.voice.ThreadedResolver", wraps=ThreadedResolver) as resolver:
            await self.client.open()
            resolver.assert_called_once_with()
            await self.client.command("dial", peer="fixture@" + COMPONENT)
            pcm = bytes([7]) * 640
            self.client.feed(pcm)
            self.assertEqual(await asyncio.wait_for(self.client.playback.get(), 2), pcm)

    async def test_system_dns_failure_is_specific_private_and_does_not_open_microphone(self):
        self.voice.url = self.voice.url.replace("127.0.0.1", "localhost")
        with patch.object(ThreadedResolver, "resolve", side_effect=gaierror(
            EAI_NONAME, "fixture-private-host-and-token-must-not-be-echoed",
        )), self.assertRaisesRegex(ValueError, "DNS"):
            await self.client.open()
        closed = [e for e in self.events if isinstance(e, VoiceChannelChanged) and not e.connected]
        self.assertTrue(closed)
        self.assertIn("DNS", closed[-1].detail)
        self.assertNotIn("fixture-private", closed[-1].detail)
        self.assertIsNone(self.backend_headers)
        self.assertIsNone(self.client.audio)
        self.assertIsNone(self.client.ws)

    async def test_tls_proxy_command_pcm_both_directions_and_logout(self):
        self.voice.metrics_enabled = True
        reports = []
        logging = patch.object(FlowMetrics, "_log", side_effect=reports.append)
        logging.start()
        self.addCleanup(logging.stop)
        await self.client.open()
        await self.client.command("dial", peer="fixture@" + COMPONENT)
        self.assertEqual(self.backend_headers["X-CAN-Device"], "fixture-device")
        self.assertEqual(self.backend_headers["X-CAN-Resource"], RESOURCE)
        pcm = bytes(range(128)) * 5
        self.client.feed(pcm)
        received = await asyncio.wait_for(self.client.playback.get(), 2)
        self.assertEqual(received, pcm)
        self.assertTrue(any(isinstance(e, VoiceStateChanged) for e in self.events))
        await self.voice.revoke(self.session)
        await asyncio.wait_for(self.client.task, 2)
        self.assertTrue(self.client.capture.empty())
        self.assertTrue(self.client.playback.empty())
        self.assertIsInstance(self.events[-1], VoiceChannelChanged)
        self.assertFalse(self.events[-1].connected)
        async with asyncio.timeout(2):
            while not reports:
                await asyncio.sleep(.005)
        self.assertTrue(reports[-1]["final"])
        for key in ("client_received_bytes", "native_forwarded_bytes",
                    "native_received_bytes", "client_forwarded_bytes"):
            self.assertEqual(reports[-1][key], len(pcm))
        self.assertNotIn("fixture", json.dumps(reports))

    async def test_native_dialog_service_thread_tls_and_synthetic_audio_end_to_end(self):
        app = wx.App.Get() or wx.App(False)
        main_loop = asyncio.get_running_loop()
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(await asyncio.to_thread(ready.wait, 2))
        backend = AudioBackend()
        window = MainWindow.__new__(MainWindow)
        wx.Frame.__init__(window, None)
        window._voice_dialog = None
        window._closing = False
        window.current_jid = "fixture@example.invalid"
        window.whatsapp_component_jid = COMPONENT
        window.whatsapp_verified = True
        window.speaker = Mock()
        window.audio_recorder = SimpleNamespace(is_recording=False)
        window.status_bar = Mock()
        window.conversation = SimpleNamespace(
            current_chat=Chat("fixture@" + COMPONENT, "Fixture contact"), close_audio=Mock(),
            messages=wx.TextCtrl(window), call_button=wx.Button(window, label="Llamar"),
        )
        window.conversation.call_button.Bind(wx.EVT_BUTTON, window._on_call_contact)
        window.Bind(EVT_XMPP_EVENT, window._on_xmpp_event)
        service = XmppService(window._post_xmpp_event)
        fixture = self

        class Request(IQ):
            async def send(self, **_kwargs):
                future = asyncio.run_coroutine_threadsafe(fixture.voice.authorize(self), main_loop)
                await asyncio.wrap_future(future)
                return self.response

        class Client:
            _voice = None
            boundjid = JID(RESOURCE)
            settings = SimpleNamespace(use_tls=True, ca_file=str(fixture.cert))
            Iq = Request

            async def start_voice(self, session_id):
                self._voice = VoiceTransport(
                    self, COMPONENT, service._emit, session_id=session_id,
                    audio_factory=lambda: LiveCallAudio(backend, clock=backend.clock),
                )
                await self._voice.open()

        client = Client()
        service._client, service._loop = client, loop
        c = VoiceController(service, "fixture@example.invalid", COMPONENT, window._render_voice,
                            Mock(), Mock(), lambda _jid: "Fixture contact",
                            available=window._voice_available,
                            before_call=window._voice_before_call)
        window._voice_controller = c
        c.diagnostics_ready = Mock()
        c.settings = VoiceAudioSettings(input_device=1, output_device=2)

        async def wait_for(predicate):
            async with asyncio.timeout(4):
                while not predicate():
                    app.ProcessPendingEvents()
                    wx.YieldIfNeeded()
                    await asyncio.sleep(.01)

        def click(button):
            button.Command(wx.CommandEvent(wx.wxEVT_BUTTON, button.GetId()))

        with patch("cliente_xmpp.xmpp.voice_audio.devices", return_value=(
            VoiceDevice(1, "Fixture mic", True, False),
            VoiceDevice(2, "Fixture output", False, True),
        )), patch("cliente_xmpp.ui.voice_integration.wx.MessageBox", return_value=wx.YES), \
                patch.object(VoiceCallDialog, "Show"):
            try:
                click(window.conversation.call_button)
                dialog = window._voice_dialog
                self.assertIsNotNone(dialog)
                self.assertFalse(backend.streams)
                await wait_for(lambda: c.state == "active" and c.audio_running)
                self.assertEqual(c.chat_jid, "fixture@" + COMPONENT)
                self.assertEqual(c.caller_name, "Fixture contact")
                stream = backend.streams[0]
                self.assertEqual(stream.kwargs["device"], (1, 2))
                pcm = bytes(range(128))*5
                await asyncio.to_thread(stream.frame, pcm)
                await wait_for(lambda: bool(self.native_audio))
                await wait_for(lambda: not client._voice.audio.native.playback.empty())
                backend.advance(.06)
                self.assertEqual(await asyncio.to_thread(stream.frame, SILENCE), pcm)
                dialog.mute.SetValue(True)
                checked = wx.CommandEvent(wx.wxEVT_CHECKBOX, dialog.mute.GetId())
                checked.SetInt(1)
                dialog.mute.Command(checked)
                self.assertTrue(c.settings.muted)
                await wait_for(lambda: client._voice.audio.settings.muted)
                old_count = len(self.native_audio)
                await asyncio.to_thread(stream.frame, pcm)
                await wait_for(lambda: len(self.native_audio)>old_count)
                self.assertEqual(self.native_audio[-1], SILENCE)
                click(dialog.diagnostics)
                await wait_for(lambda: c.diagnostics_ready.called)
                self.assertGreaterEqual(c.diagnostics_ready.call_args.args[0].capture_frames, 2)
                click(dialog.hangup)
                await wait_for(lambda: c.state == "ended" and stream.closed)
                self.assertFalse(c.audio_running)
                self.assertFalse(stream.started)
                self.assertIsNone(window._voice_dialog)
            finally:
                c.disable()
                if client._voice:
                    closing = asyncio.run_coroutine_threadsafe(client._voice.close(), loop)
                    await asyncio.wrap_future(closing)
                loop.call_soon_threadsafe(loop.stop)
                await asyncio.to_thread(thread.join, 2)
                self.assertFalse(thread.is_alive())
                loop.close()
                if window._voice_dialog is not None:
                    window._voice_dialog.Destroy()
                window.Destroy()

    async def test_untrusted_certificate_never_sends_token_to_backend(self):
        self.client.client.settings.ca_file = ""
        with self.assertRaises(ValueError):
            await self.client.open()
        self.assertIsNone(self.backend_headers)

    async def test_redirect_cannot_forward_authorization(self):
        app = web.Application()

        async def redirect(_request):
            raise web.HTTPFound("https://other.example.invalid/voice/v1")

        app.router.add_get("/voice/v1", redirect)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.voice.url = f"wss://127.0.0.1:{port}/voice/v1"
        try:
            with self.assertRaises(ValueError):
                await self.client.open()
            self.assertIsNone(self.backend_headers)
        finally:
            await runner.cleanup()

    async def test_reused_token_query_origin_and_cross_account_are_rejected(self):
        tls = ssl.create_default_context(cafile=str(self.cert))
        token = self.voice.tokens.issue(self.session, RESOURCE)
        async with ClientSession() as http:
            for kwargs in (
                {"headers": {"Origin": "https://other.example.invalid"}},
                {"params": {"token": token}},
                {"headers": {"Authorization": "Bearer missing"}},
            ):
                with self.subTest(kwargs=tuple(kwargs)):
                    with self.assertRaises(WSServerHandshakeError) as raised:
                        await http.ws_connect(self.voice.url, ssl=tls,
                                              protocols=(PROTOCOL,), **kwargs)
                    self.assertEqual(raised.exception.status, 403)
            ws = await http.ws_connect(self.voice.url, ssl=tls, protocols=(PROTOCOL,),
                                       headers={"Authorization": "Bearer " + token})
            with self.assertRaises(WSServerHandshakeError) as raised:
                await http.ws_connect(self.voice.url, ssl=tls, protocols=(PROTOCOL,),
                                      headers={"Authorization": "Bearer " + token})
            self.assertEqual(raised.exception.status, 403)
            await ws.close()


class VoiceMediaIsolationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        VoiceTLSIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        VoiceTLSIntegrationTests.tearDownClass.__func__(cls)

    def gateway(self):
        return VoiceGateway(SimpleNamespace(boundjid=JID(COMPONENT)),
                            url="wss://localhost/voice/v1", socket=Path("unused"))

    def server_tls(self):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        return tls

    async def test_failed_bind_cleans_worker_and_allows_retry(self):
        voice = self.gateway()
        workers = []
        original = MediaLoop.__init__

        def created(worker):
            original(worker)
            workers.append(worker)

        with patch.object(MediaLoop, "__init__", created), patch.object(
            web.TCPSite, "start", side_effect=OSError("fixture bind failure"),
        ):
            with self.assertRaises(OSError):
                await voice.start("127.0.0.1", 0, self.server_tls(), isolated=True)
        self.assertFalse(workers[0].thread.is_alive())
        self.assertIsNone(voice.media_loop)
        self.assertIsNone(voice.runner)
        self.assertFalse(voice.serving.is_set())
        try:
            await voice.start("127.0.0.1", 0, self.server_tls(), isolated=True)
            self.assertTrue(voice.serving.is_set())
        finally:
            await voice.close()

    async def test_disconnect_during_start_revokes_queued_start_without_thread_leak(self):
        voice = self.gateway()
        entered, release = threading.Event(), threading.Event()
        original = web.TCPSite.start

        async def delayed(site):
            entered.set()
            await asyncio.to_thread(release.wait, 3)
            await original(site)

        with patch.object(web.TCPSite, "start", delayed):
            first = asyncio.create_task(voice.start(
                "127.0.0.1", 0, self.server_tls(), isolated=True))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                worker = voice.media_loop
                queued = asyncio.create_task(voice.start(
                    "127.0.0.1", 0, self.server_tls(), isolated=True))
                await asyncio.sleep(0)
                closing = asyncio.create_task(voice.close())
                await asyncio.sleep(0)
            finally:
                release.set()
            results = await asyncio.wait_for(asyncio.gather(
                first, queued, closing, return_exceptions=True), 5)
        self.assertIsInstance(results[0], ValueError)
        self.assertIsInstance(results[1], ValueError)
        self.assertIsNone(results[2])
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(voice.media_loop)
        self.assertIsNone(voice.runner)
        self.assertFalse(voice.serving.is_set())

    async def test_consumed_but_abandoned_authorization_expires(self):
        voice = self.gateway()
        now = [0.]
        voice.tokens = Tokens(clock=lambda: now[0])
        grant = voice.tokens.consume(voice.tokens.issue(registered(), RESOURCE))
        now[0] = grant.expires
        task = asyncio.create_task(voice.validate_leases())
        try:
            await asyncio.sleep(0)
            self.assertTrue(grant.revoked.is_set())
            self.assertEqual(voice.tokens.leases, {})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def setup_flow(self, isolated):
        owner_thread = threading.get_ident()
        source = registered()

        class Session:
            def __getattr__(self, key):
                if threading.get_ident() != owner_thread:
                    raise AssertionError("mutable Slidge session read outside owner")
                return getattr(source, key)

        self.session = Session()
        self.io = MediaLoop()
        self.received = []
        self.replies = []
        self.primed = threading.Event()
        self.voice = VoiceGateway(SimpleNamespace(boundjid=JID(COMPONENT)),
                                  url="wss://localhost/voice/v1", socket=Path("unused"))
        self.voice.metrics_enabled = True

        async def backend(request):
            ws = web.WebSocketResponse(protocols=(PROTOCOL,))
            await ws.prepare(request)
            await ws.send_json({"type": "state", "revision": 0, "state": "idle"})
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    command = json.loads(message.data)
                    await ws.send_json({"type": "state", "revision": 1, "state": "active",
                                        "call_id": "fixture-call", "direction": "outgoing",
                                        "peer_pn": "fixture@s.whatsapp.net"})
                    await ws.send_json({"type": "result", "id": command["id"]})
                elif message.type == WSMsgType.BINARY:
                    self.received.append(message.data)
                    await ws.send_bytes(message.data)
            return ws

        async def start_backend():
            app = web.Application()
            app.router.add_get("/session", backend)
            self.backend_runner = web.AppRunner(app, access_log=None)
            await self.backend_runner.setup()
            site = web.TCPSite(self.backend_runner, "127.0.0.1", 0)
            await site.start()
            return site._server.sockets[0].getsockname()[1]

        backend_port = await self.io.call(start_backend())

        async def connect_native(http, grant):
            return await http.ws_connect(f"http://127.0.0.1:{backend_port}/session",
                                         protocols=(PROTOCOL,))

        self.voice.connect_native = connect_native
        self.voice.native_http = ClientSession
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        await self.voice.start("127.0.0.1", 0, tls, isolated=isolated)
        port = next(iter(self.voice.runner.sites))._server.sockets[0].getsockname()[1]
        token = self.voice.tokens.issue(self.session, RESOURCE)

        async def start_client():
            self.http = ClientSession()
            self.ws = await self.http.ws_connect(
                f"wss://127.0.0.1:{port}/voice/v1",
                ssl=ssl.create_default_context(cafile=self.cert),
                protocols=(PROTOCOL,), headers={"Authorization": "Bearer " + token})
            await self.ws.receive(timeout=3)
            await self.ws.send_json({"id": "fixture", "action": "dial",
                                     "peer": "fixture@"+COMPONENT})
            for _ in range(2):
                message = await self.ws.receive(timeout=3)
                if json.loads(message.data).get("type") == "state":
                    self.assertEqual(json.loads(message.data).get("state"), "active")

        await self.io.call(start_client())

    async def cleanup_flow(self):
        async def stop_client():
            if hasattr(self, "ws"):
                await self.ws.close()
            if hasattr(self, "http"):
                await self.http.close()

        try:
            await self.io.call(stop_client())
            await self.voice.close()
            await self.io.call(self.backend_runner.cleanup())
        finally:
            await self.io.stop()

    async def test_owner_stall_drops_pcm_on_shared_loop_but_not_on_isolated_tls_media(self):
        evidence = []
        for isolated in (False, True):
            with self.subTest(isolated=isolated), patch.dict(
                "os.environ", {"CAN_VOICE_PROFILE": "1"},
            ):
                reports = []
                with patch.object(FlowMetrics, "_log", side_effect=reports.append):
                    await self.setup_flow(isolated)
                    frames = [i.to_bytes(2, "little")*320 for i in range(120)]

                    async def exercise(frames=frames):
                        async def receive():
                            async for msg in self.ws:
                                if msg.type == WSMsgType.BINARY:
                                    self.replies.append((time.perf_counter(), msg.data))
                                    if len(self.replies) >= 5:
                                        self.primed.set()

                        reader = asyncio.create_task(receive())
                        try:
                            cadence = AudioCadence()
                            for frame in frames:
                                await cadence.wait()
                                await self.ws.send_bytes(frame)
                            await asyncio.sleep(.8)
                        finally:
                            reader.cancel()
                            await asyncio.gather(reader, return_exceptions=True)
                        return self.received[:], self.replies[:]

                    flow = asyncio.create_task(self.io.call(exercise()))
                    try:
                        self.assertTrue(await asyncio.to_thread(self.primed.wait, 3))
                        # Deliberate, controlled owner-loop stall; network peers run elsewhere.
                        time.sleep(.94)
                        received, replies = await asyncio.wait_for(flow, 8)
                    finally:
                        await self.cleanup_flow()
                    report = next(r for r in reversed(reports) if r["final"])
                    gap = max(b[0]-a[0] for a, b in zip(replies, replies[1:], strict=False))
                    self.assertEqual(report["client_packets"], len(frames))
                    self.assertNotIn(RESOURCE, json.dumps(report))
                    self.assertNotIn("fixture@s.whatsapp.net", json.dumps(report))
                    if isolated:
                        self.assertEqual(received, frames)
                        self.assertEqual([p for _, p in replies], frames)
                        self.assertEqual(report["capture_queue_dropped_packets"], 0)
                        self.assertGreater(report["owner_loop_delay_max_ms"], 800)
                        self.assertLess(gap, .7)
                    else:
                        self.assertLess(len(received), len(frames))
                        self.assertGreater(report["capture_queue_dropped_packets"], 0)
                        self.assertGreater(gap, .8)
                    evidence.append({"isolated": isolated, "input_blocks": len(frames),
                                     "native_blocks": len(received),
                                     "playback_gap_max_ms": round(gap*1000), "flow": report})
        path = Path(".test-results/voice-loop-isolation.json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    async def test_logout_and_device_change_revoke_worker_without_cross_thread_session_access(self):
        for operation in ("logout", "device_change"):
            with self.subTest(operation=operation):
                await self.setup_flow(True)
                worker = self.voice.media_loop
                grant = next(iter(self.voice.tokens.leases.values()))
                try:
                    await self.io.call(self.ws.send_bytes(bytes([7])*640))
                    if operation == "logout":
                        await self.voice.revoke(self.session)
                    else:
                        self.session.user.legacy_module_data["device_id"] = "other-device"
                    async def closed():
                        while True:
                            msg = await self.ws.receive(timeout=2)
                            if msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}:
                                return
                    await self.io.call(closed())
                    self.assertTrue(grant.revoked.is_set())
                    self.assertEqual(self.received, [])
                finally:
                    await self.cleanup_flow()
                self.assertFalse(worker.thread.is_alive())
                self.assertEqual(self.voice.tokens.leases, {})


class VoiceClientIsolationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        VoiceTLSIntegrationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        VoiceTLSIntegrationTests.tearDownClass.__func__(cls)

    async def test_tls_duplex_survives_xmpp_stall_and_preserves_mute_and_close(self):
        owner = threading.get_ident()
        backend, received, events = AudioBackend(), [], []
        backend.clock = time.perf_counter
        io = MediaLoop()

        async def serve():
            async def echo(request):
                ws = web.WebSocketResponse(protocols=(PROTOCOL,))
                await ws.prepare(request)
                await ws.send_json({"type": "state", "revision": 0, "state": "idle"})
                revision = 0
                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        command = json.loads(message.data)
                        revision += 1
                        state = "active" if command["action"] == "dial" else "ended"
                        await ws.send_json({"type": "state", "revision": revision,
                                            "state": state, "call_id": "fixture-call",
                                            "direction": "outgoing"})
                        await ws.send_json({"type": "result", "id": command["id"]})
                    elif message.type == WSMsgType.BINARY:
                        received.append(message.data)
                        await ws.send_bytes(message.data)
                return ws

            app = web.Application()
            app.router.add_get("/voice/v1", echo)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(self.cert, self.key)
            site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
            await site.start()
            return runner, site._server.sockets[0].getsockname()[1]

        runner, port = await io.call(serve())
        fixture = self

        class Request(IQ):
            async def send(self, **kwargs):
                fixture.assertEqual(threading.get_ident(), owner)
                result = IQ(from_jid=COMPONENT, kind="result")
                result.append(ET.Element(f"{{{NAMESPACE}}}open", {
                    "url": f"wss://localhost:{port}/voice/v1", "token": "a"*40,
                    "protocol": PROTOCOL, "format": "pcm16le", "rate": "16000",
                    "channels": "1", "expires-in": "30",
                }))
                return result

        def emit(event):
            self.assertEqual(threading.get_ident(), owner)
            events.append(event)

        client = SimpleNamespace(boundjid=JID(RESOURCE), Iq=Request,
                                 settings=SimpleNamespace(use_tls=True, ca_file=str(self.cert)))
        voice = IsolatedVoiceTransport(client, COMPONENT, emit,
                                       audio_factory=lambda: LiveCallAudio(backend))

        async def until(predicate):
            async with asyncio.timeout(4):
                while not predicate():
                    await asyncio.sleep(.01)

        source = None
        try:
            await voice.open()
            self.assertEqual(backend.streams, [])
            await voice.command("dial", peer="fixture@"+COMPONENT,
                                audio_settings=VoiceAudioSettings())
            await until(lambda: backend.streams and backend.streams[0].started)
            stream = backend.streams[0]
            played = []

            def capture():
                for _ in range(100):
                    played.append(stream.frame(bytes([7])*640))
                    time.sleep(.02)

            source = threading.Thread(target=capture)
            source.start()
            await until(lambda: len(received) >= 10)
            time.sleep(.5)  # Slixmpp stalled; devices and TLS peer remain independent.
            await asyncio.to_thread(source.join, 4)
            self.assertFalse(source.is_alive())
            await until(lambda: len(received) == 100)
            stats = await voice.diagnostics()
            self.assertEqual((stats.capture_dropped, stats.transport_capture_dropped), (0, 0))
            self.assertEqual(stats.transport_playback_dropped, 0)
            self.assertGreater(sum(bool(any(pcm)) for pcm in played), 70)
            await voice.configure_audio(muted=True)
            stream.frame(bytes([7])*640)
            await until(lambda: len(received) == 101)
            self.assertEqual(received[-1], SILENCE)
            await voice.command("hangup", call_id="fixture-call")
            await until(lambda: stream.closed)
            self.assertTrue(any(isinstance(e, VoiceStateChanged) for e in events))
        finally:
            if source:
                await asyncio.to_thread(source.join, 4)
            await voice.close()
            await voice.close()
            await io.call(runner.cleanup())
            await io.stop()
        self.assertFalse(voice.worker.thread.is_alive())

    async def test_open_failure_closes_media_worker_without_opening_microphone(self):
        backend = AudioBackend()
        client = SimpleNamespace(boundjid=JID(RESOURCE),
                                 settings=SimpleNamespace(use_tls=True, ca_file=str(self.cert)))
        voice = IsolatedVoiceTransport(client, COMPONENT, Mock(),
                                       audio_factory=lambda: LiveCallAudio(backend))
        with patch("cliente_xmpp.xmpp.voice_worker.request_voice_grant",
                   AsyncMock(return_value=("wss://localhost/voice/v1", "a"*40))), patch.object(
            VoiceTransport, "open", AsyncMock(side_effect=ValueError("fixture TLS failure")),
        ):
            with self.assertRaises(ValueError):
                await voice.open()
        self.assertEqual(backend.streams, [])
        self.assertFalse(voice.worker.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
