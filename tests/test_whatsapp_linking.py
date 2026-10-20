from __future__ import annotations

import asyncio
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from cliente_xmpp.xmpp.client import BridgeXmppClient
from cliente_xmpp.xmpp.events import WhatsAppLinkState
from cliente_xmpp.xmpp.linking import NAMESPACE, LinkingProtocol
from tools.bridge_linking import LinkingError, LinkingSession, install_session, state

PNG = b"\x89PNG\r\n\x1a\nfictional"


class LinkingOwnerTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session = SimpleNamespace(
            whatsapp=SimpleNamespace(Disconnect=Mock(), PairPhone=Mock(return_value="ABCD-1234")),
            xmpp=SimpleNamespace(login_wrap=AsyncMock()), logged=False, is_logging_in=False,
        )
        return session, LinkingSession(session)

    async def test_start_is_idempotent_and_requires_cancel_for_failed_live_attempt(self):
        session, owner = self.session()
        pending = asyncio.Event()
        session.xmpp.login_wrap = AsyncMock(side_effect=lambda _: pending.wait())

        async def login(_):
            await pending.wait()

        session.xmpp.login_wrap.side_effect = login
        await owner.start("fixture-request")
        attempt = owner.attempt
        await owner.start("fixture-request")
        await owner.start("another-resource")
        self.assertEqual(owner.attempt, attempt)
        await asyncio.sleep(0)
        session.xmpp.login_wrap.assert_awaited_once()
        owner.change("connection_error", "fixture")
        with self.assertRaisesRegex(LinkingError, "cancel_required"):
            await owner.start("new-request")
        await owner.cancel(attempt)
        self.assertEqual(owner.phase, "canceled")
        session.whatsapp.Disconnect.assert_called_once()
        await owner.cancel(attempt)
        session.whatsapp.Disconnect.assert_called_once()

    async def test_failed_cancel_retains_attempt_and_never_logs_out(self):
        session, owner = self.session()
        owner.reserve()
        attempt = owner.attempt
        session.whatsapp.Disconnect.side_effect = RuntimeError("fixture failure")
        with self.assertRaisesRegex(LinkingError, "cancel_failed"):
            await owner.cancel(attempt)
        self.assertEqual(owner.attempt, attempt)
        self.assertEqual(owner.reason, "cancel_failed")
        self.assertFalse(hasattr(session.whatsapp, "Logout"))

    async def test_cancel_rejects_other_attempt_and_authenticated_session(self):
        session, owner = self.session()
        owner.reserve()
        with self.assertRaisesRegex(LinkingError, "stale_attempt"):
            await owner.cancel("other")
        owner.authenticated = True
        with self.assertRaisesRegex(LinkingError, "already_connected"):
            await owner.cancel(owner.attempt)
        session.whatsapp.Disconnect.assert_not_called()

    async def test_phone_waits_for_current_native_ready_and_rejects_expired_qr(self):
        session, owner = self.session()
        owner.reserve()
        task = asyncio.create_task(owner.phone(owner.attempt, "+12025550100"))
        await asyncio.sleep(0)
        session.whatsapp.PairPhone.assert_not_called()
        owner.qr_deadline = owner.clock() + 5
        owner.ready.set()
        self.assertEqual(await task, "ABCD-1234")
        session.whatsapp.PairPhone.assert_called_once_with("+12025550100")
        owner.qr_deadline = 0
        with self.assertRaisesRegex(LinkingError, "expired"):
            await owner.phone(owner.attempt, "+12025550100")

    async def test_qr_rotation_keeps_exact_expiry_and_cancel_ignores_late_qr(self):
        _, owner = self.session()
        owner.clock = lambda: 100.0
        image = SimpleNamespace(save=lambda stream, **_: stream.write(PNG))
        with patch.dict("sys.modules", {"qrcode": SimpleNamespace(make=lambda _: image)}):
            await owner.received_qr(json.dumps({"code": "fictional-one", "ttl": 60}))
            self.assertEqual(owner.qr_deadline, 160)
            await owner.received_qr(json.dumps({"code": "fictional-two", "ttl": 20}))
            self.assertEqual(owner.qr_deadline, 120)
            self.assertEqual(owner.qr_revision, 2)
            self.assertEqual(owner.qr, PNG)
            owner.change("canceled")
            await owner.received_qr(json.dumps({"code": "late", "ttl": 60}))
            self.assertEqual(owner.qr, b"")
            self.assertEqual(owner.phase, "canceled")

    async def test_phone_retry_reuses_code_without_changing_phone(self):
        session, owner = self.session()
        owner.reserve()
        owner.ready.set()
        owner.qr_deadline = owner.clock() + 60
        one = await owner.phone(owner.attempt, "+12025550100")
        two = await owner.phone(owner.attempt, "+12025550100")
        self.assertEqual(one, two)
        session.whatsapp.PairPhone.assert_called_once()
        with self.assertRaisesRegex(LinkingError, "cancel_required"):
            await owner.phone(owner.attempt, "+12025550101")

    async def test_cancel_is_not_blocked_by_phone_readiness_wait(self):
        _, owner = self.session()
        owner.reserve()
        pending = asyncio.create_task(owner.phone(owner.attempt, "+12025550100"))
        await asyncio.sleep(0)
        await asyncio.wait_for(owner.cancel(owner.attempt), 1)
        self.assertEqual(owner.phase, "canceled")
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

    async def test_sync_failure_does_not_revoke_authenticated_transport(self):
        _, owner = self.session()
        owner.authenticated = True
        owner.change("connected")
        owner.sync_error = True
        snapshot = owner.snapshot()
        self.assertEqual(snapshot["authenticated"], "1")
        self.assertEqual(snapshot["sync"], "error")

    async def test_authentication_wait_has_a_bounded_deadline(self):
        _, owner = self.session()
        owner.reserve()
        owner.clock = lambda: owner.changed_at + 191
        self.assertEqual(owner.snapshot()["phase"], "connection_error")
        self.assertEqual(owner.reason, "connection_timeout")

    async def test_pair_success_is_not_authentication_and_hooks_are_preserved(self):
        calls = []

        class Session:
            async def login(self):
                return "connected"

            async def on_wa_qr(self, value):
                calls.append("qr")

            async def on_wa_pair(self, value):
                calls.append("persist-device")

            async def on_wa_connect(self, value):
                calls.append("existing-contact-voice-hooks")

            async def logout(self):
                calls.append("existing-logout")

            async def on_wa_logged_out(self, value):
                calls.append("existing-unlinked")

        install_session(Session)
        session = Session()
        await session.on_wa_pair("fictional-device")
        self.assertEqual(state(session).phase, "paired")
        self.assertFalse(state(session).authenticated)
        self.assertEqual(calls, ["persist-device"])
        await session.on_wa_connect(SimpleNamespace(Error="", JID="fixture@example.invalid"))
        self.assertTrue(state(session).authenticated)
        self.assertIn("existing-contact-voice-hooks", calls)
        await session.on_wa_connect(SimpleNamespace(Error="can-link:disconnected", JID=""))
        self.assertFalse(state(session).authenticated)
        self.assertEqual(state(session).phase, "connecting")
        await session.on_wa_logged_out(SimpleNamespace())
        self.assertEqual(state(session).phase, "logged_out")
        self.assertEqual(calls[-1], "existing-unlinked")
        pending = state(session).task = asyncio.create_task(asyncio.Event().wait())
        await session.logout()
        self.assertTrue(pending.cancelled())
        self.assertEqual(calls[-1], "existing-logout")


class LinkingProtocolTests(unittest.IsolatedAsyncioTestCase):
    def client(self, values):
        emitted = []

        class IQ(dict):
            def __init__(self):
                super().__init__()
                self.xml = ET.Element("iq")

            async def send(self, **_):
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return SimpleNamespace(xml=ET.fromstring(value))

        client = SimpleNamespace(
            _session_generation=1, boundjid=SimpleNamespace(bare="account@example.invalid"),
            make_iq_get=IQ, make_iq_set=IQ, _emit=emitted.append,
            _whatsapp_session_ready=False, _start_initial_remote_sync_if_ready=Mock(),
        )
        return client, emitted, LinkingProtocol(client)

    def xml(self, phase="needs_qr", **attrs):
        node = ET.Element("iq", {"from": "whatsapp.example.invalid"})
        value = ET.SubElement(node, f"{{{NAMESPACE}}}state", {
            "version": "1", "phase": phase, "attempt": "fixture-attempt", "revision": "2",
            "authenticated": "1" if phase == "connected" else "0", **attrs,
        })
        return node, value

    async def test_scanned_does_not_enable_actions_but_connected_does(self):
        values = [ET.tostring(self.xml(p)[0]) for p in ("paired", "connected")]
        client, emitted, protocol = self.client(values)
        await protocol.request("whatsapp.example.invalid")
        self.assertFalse(client._whatsapp_session_ready)
        client._start_initial_remote_sync_if_ready.assert_not_called()
        await protocol.request("whatsapp.example.invalid")
        self.assertTrue(client._whatsapp_session_ready)
        self.assertEqual(emitted[-1].account_jid, "account@example.invalid")
        client._start_initial_remote_sync_if_ready.assert_called_once()

    async def test_qr_emits_once_and_never_renews_expiry(self):
        node, value = self.xml(qr_revision="1", qr_ttl="20")
        ET.SubElement(value, f"{{{NAMESPACE}}}qr").text = base64.b64encode(PNG).decode()
        client, emitted, protocol = self.client([ET.tostring(node), ET.tostring(node)])
        first = await protocol.request("whatsapp.example.invalid")
        self.assertEqual(first.image_data, PNG)
        self.assertLessEqual(first.qr_deadline, time.monotonic() + 20)
        second = await protocol.request("whatsapp.example.invalid")
        self.assertEqual(second.image_data, b"")
        self.assertEqual(len(emitted), 2)

    async def test_iq_cancel_timeout_keeps_idempotency_token(self):
        node, _ = self.xml()
        _, _, protocol = self.client([ET.tostring(node), TimeoutError()])
        protocol.pending_requests["whatsapp.example.invalid"] = "fixture-request"
        with self.assertRaises(TimeoutError):
            await protocol.cancel("whatsapp.example.invalid")
        self.assertEqual(protocol.pending_requests["whatsapp.example.invalid"], "fixture-request")

    async def test_old_connection_response_is_discarded(self):
        node, _ = self.xml("connected")
        client, emitted, protocol = self.client([ET.tostring(node)])
        client._session_generation = 2
        with self.assertRaises(asyncio.CancelledError):
            await protocol.request("whatsapp.example.invalid")
        self.assertEqual(emitted, [])
        self.assertFalse(client._whatsapp_session_ready)

    async def test_invalid_snapshot_is_rejected(self):
        for attrs in ({"authenticated": "1"}, {"qr_ttl": "not-a-number"}):
            with self.subTest(attrs=attrs):
                node, _ = self.xml(**attrs)
                _, emitted, protocol = self.client([ET.tostring(node)])
                with self.assertRaises(ValueError):
                    await protocol.request("whatsapp.example.invalid")
                self.assertEqual(emitted, [])

    async def test_snapshot_from_other_component_is_rejected(self):
        node, _ = self.xml("connected")
        node.set("from", "other.example.invalid")
        _, emitted, protocol = self.client([ET.tostring(node)])
        with self.assertRaisesRegex(ValueError, "otro componente"):
            await protocol.request("whatsapp.example.invalid")
        self.assertEqual(emitted, [])

    async def test_older_revision_cannot_revoke_newer_connection(self):
        connected, _ = self.xml("connected", revision="5")
        delayed, _ = self.xml("needs_qr", revision="4")
        client, emitted, protocol = self.client([ET.tostring(connected), ET.tostring(delayed)])
        await protocol.request("whatsapp.example.invalid")
        await protocol.request("whatsapp.example.invalid")
        self.assertTrue(client._whatsapp_session_ready)
        self.assertEqual(len(emitted), 1)


class LinkingLegacyAuthorityTests(unittest.TestCase):
    def test_command_list_and_pair_success_do_not_prove_authentication(self):
        self.assertEqual(BridgeXmppClient._whatsapp_command_state([("wa_logout", "Logout")]),
                         "connecting")
        client = SimpleNamespace(
            _last_whatsapp_status_by_component={}, _emit=lambda _: None,
            _safe_debug_text=lambda value, **_: value, _whatsapp_session_ready=False,
            _start_initial_remote_sync_if_ready=Mock(),
        )
        BridgeXmppClient._emit_whatsapp_status(client, "whatsapp.example.invalid", "paired")
        self.assertFalse(client._whatsapp_session_ready)
        client._start_initial_remote_sync_if_ready.assert_not_called()

    def test_authoritative_bridge_ignores_legacy_presence(self):
        emitted = []
        client = SimpleNamespace(
            _linking=SimpleNamespace(supported={"whatsapp.example.invalid": True}),
            _emit=emitted.append,
        )
        BridgeXmppClient._emit_whatsapp_status(client, "whatsapp.example.invalid", "connected")
        self.assertEqual(emitted, [])

    def test_state_event_has_account_attempt_and_qr_expiry(self):
        event = WhatsAppLinkState("account@example.invalid", "whatsapp.example.invalid", 1,
                                  "fixture-attempt", 2, "paired", False)
        self.assertEqual(event.attempt, "fixture-attempt")
        self.assertFalse(event.authenticated)


class LinkingPreflightTests(unittest.TestCase):
    def test_old_appliance_requires_consent_without_applying_update(self):
        from pathlib import Path

        from cliente_xmpp.config.settings import ConnectionSettings
        from cliente_xmpp.local_bridge import (
            LocalBridgeConnection,
            LocalBridgeService,
            LocalBridgeUpdateStatus,
        )

        service = LocalBridgeService()
        connection = LocalBridgeConnection(ConnectionSettings(), "", Path("fixture.json"))
        service.prepare = Mock(return_value=connection)
        service.installed_update_status = Mock(return_value=LocalBridgeUpdateStatus(
            supports_updates=True, current_version=19,
        ))
        update = LocalBridgeUpdateStatus(
            supports_updates=True, current_version=19, target_version=35, update_available=True,
        )
        service.check_for_update = Mock(return_value=update)
        service.update_bridge = Mock()
        self.assertIs(service.prepare_for_linking().update_status, update)
        service.update_bridge.assert_not_called()

    def test_unknown_appliance_version_is_not_assumed_compatible(self):
        from cliente_xmpp.local_bridge import (
            LocalBridgeError,
            LocalBridgeService,
            LocalBridgeUpdateStatus,
        )

        service = LocalBridgeService()
        service.prepare = Mock(return_value=object())
        service.installed_update_status = Mock(return_value=LocalBridgeUpdateStatus(False))
        with self.assertRaises(LocalBridgeError):
            service.prepare_for_linking()


class LinkingUiIsolationTests(unittest.TestCase):
    def window(self):
        return SimpleNamespace(
            current_jid="account@example.invalid", whatsapp_link_generation=10,
            whatsapp_link_attempt="fixture-attempt", whatsapp_link_revision=2,
            whatsapp_link_protocol=True, whatsapp_qr_restart_after_cancel=True,
            whatsapp_link_state_key=None, whatsapp_qr_deadline=0,
            whatsapp_pair_phone_pending="", whatsapp_pairing_bootstrap_generation=0,
            _close_whatsapp_qr_dialog=Mock(), _begin_whatsapp_qr_request=Mock(),
            _handle_whatsapp_bridge_status=Mock(), _handle_whatsapp_qr_image_data=Mock(),
            _handle_whatsapp_pairing_code=Mock(),
        )

    def test_foreign_account_or_old_generation_cannot_open_qr(self):
        from cliente_xmpp.ui.main_window import MainWindow

        for account, generation in (("other@example.invalid", 11), ("account@example.invalid", 9)):
            with self.subTest(account=account, generation=generation):
                window = self.window()
                event = WhatsAppLinkState(account, "whatsapp.example.invalid", generation,
                                          "fixture-attempt", 3, "needs_qr", False,
                                          image_data=PNG, qr_deadline=time.monotonic() + 30)
                MainWindow._handle_whatsapp_link_state(window, event)
                window._handle_whatsapp_qr_image_data.assert_not_called()
                window._handle_whatsapp_bridge_status.assert_not_called()
        window = self.window()
        window.current_jid += "/explicit-resource"
        event = WhatsAppLinkState("account@example.invalid", "whatsapp.example.invalid", 10,
                                  "fixture-attempt", 3, "paired", False)
        MainWindow._handle_whatsapp_link_state(window, event)
        window._handle_whatsapp_bridge_status.assert_called_once()

    def test_failed_cancel_clears_restart_intent_without_starting(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        window.whatsapp_verified = True
        event = WhatsAppLinkState("account@example.invalid", "whatsapp.example.invalid", 10,
                                  "fixture-attempt", 3, "connection_error", False, "cancel_failed")
        MainWindow._handle_whatsapp_link_state(window, event)
        self.assertFalse(window.whatsapp_qr_restart_after_cancel)
        self.assertFalse(window.whatsapp_verified)
        window._begin_whatsapp_qr_request.assert_not_called()

    def test_new_qr_uses_server_deadline_and_exact_attempt_context(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        deadline = time.monotonic() + 20
        event = WhatsAppLinkState("account@example.invalid", "whatsapp.example.invalid", 10,
                                  "fixture-attempt", 3, "needs_qr", False,
                                  qr_revision=7, image_data=PNG, qr_deadline=deadline)
        MainWindow._handle_whatsapp_link_state(window, event)
        self.assertEqual(window.whatsapp_qr_deadline, deadline)
        self.assertEqual(window._handle_whatsapp_qr_image_data.call_args.kwargs["context"],
                         ("account@example.invalid", 10, "fixture-attempt", 7))
        # A phone response can include a QR snapshot too; it must not launch
        # another phone request or a second modal while showing the code.
        window._handle_whatsapp_qr_image_data.reset_mock()
        window.whatsapp_pair_phone_pending = "+12025550100"
        event.status, event.code, event.revision = "needs_pair_code", "ABCD-1234", 4
        MainWindow._handle_whatsapp_link_state(window, event)
        window._handle_whatsapp_qr_image_data.assert_not_called()
        window._handle_whatsapp_pairing_code.assert_called_once_with(
            "whatsapp.example.invalid", "ABCD-1234",
        )
