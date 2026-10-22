from __future__ import annotations

import asyncio
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree as ET

from slixmpp import Presence
from slixmpp.exceptions import PresenceError

from cliente_xmpp.xmpp.client import BridgeXmppClient
from cliente_xmpp.xmpp.events import WhatsAppLinkState, XmppDisconnected, XmppError
from cliente_xmpp.xmpp.linking import DISCO_NS, NAMESPACE, LinkingProtocol
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

    async def test_failed_first_probe_keeps_polling_and_recovers_initial_sync(self):
        node, _ = self.xml("connected")
        client, emitted, protocol = self.client([TimeoutError(), ET.tostring(node)])
        component = "whatsapp.example.invalid"
        protocol.supported[component] = True
        sleeps = []

        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 2:
                protocol.close()

        with patch("cliente_xmpp.xmpp.linking.asyncio.sleep", side_effect=sleep):
            with self.assertRaises(TimeoutError):
                await protocol.probe(component)
            task = protocol.pollers[component]
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(client._whatsapp_session_ready)
        self.assertEqual([e.status for e in emitted], ["connected"])
        client._start_initial_remote_sync_if_ready.assert_called_once()

    async def test_discovery_timeout_is_retried_without_legacy_fallback(self):
        component = "whatsapp.example.invalid"
        client, _, protocol = self.client([])
        info = SimpleNamespace(xml=ET.fromstring(
            f'<iq><query xmlns="{DISCO_NS}"><feature var="{NAMESPACE}"/></query></iq>'
        ))
        get_info = AsyncMock(side_effect=[TimeoutError(), info])
        class DiscoClient(SimpleNamespace):
            def __getitem__(self, _):
                return SimpleNamespace(get_info=get_info)

        client = DiscoClient(**vars(client))
        protocol.client = client
        client._linking = protocol
        client._adhoc_commands = AsyncMock()
        client._debug_whatsapp_commands = Mock()
        client._whatsapp_command_state = Mock(return_value="unknown")
        client._last_whatsapp_status_by_component = {}
        state = await BridgeXmppClient._debug_whatsapp_component_commands(client, component)
        self.assertEqual(state, "unknown")
        client._adhoc_commands.assert_not_awaited()
        self.assertNotIn(component, protocol.supported)
        self.assertTrue(await protocol.available(component))
        task = protocol.pollers[component]
        protocol.close()
        await asyncio.gather(task, return_exceptions=True)

    async def test_poll_backoff_recovers_and_stale_failure_cannot_revoke_new_session(self):
        component = "whatsapp.example.invalid"
        for stale in (False, True):
            with self.subTest(stale=stale):
                client, emitted, protocol = self.client([])
                protocol.supported[component] = True
                client._whatsapp_session_ready = True
                sleeps, requests = [], []

                async def sleep(delay, sleeps=sleeps):
                    sleeps.append(delay)

                async def request(_, requests=requests, stale=stale,
                                  client=client, protocol=protocol):
                    requests.append(1)
                    if len(requests) <= 3:
                        if stale and len(requests) == 3:
                            client._session_generation += 1
                        raise TimeoutError()
                    protocol.closed = True

                protocol.request = request
                with patch("cliente_xmpp.xmpp.linking.asyncio.sleep", side_effect=sleep):
                    await protocol.poll(component)
                self.assertEqual(sleeps[:3], [3, 6, 12])
                if stale:
                    self.assertEqual(emitted, [])
                    self.assertTrue(client._whatsapp_session_ready)
                else:
                    self.assertEqual(sleeps[-1], 24)
                    self.assertEqual(emitted[-1].reason, "status_unavailable")
                    self.assertFalse(client._whatsapp_session_ready)

    async def test_manual_actions_report_failed_discovery_without_using_legacy_commands(self):
        for action, args in (
            ("request_whatsapp_link_status", ()), ("request_whatsapp_relogin", ()),
            ("cancel_whatsapp_linking", ()), ("request_whatsapp_pair_code", ("+12025550100",)),
        ):
            with self.subTest(action=action):
                emitted = []
                client = SimpleNamespace(
                    _linking=SimpleNamespace(available=AsyncMock(side_effect=TimeoutError())),
                    _emit=emitted.append,
                )
                await getattr(BridgeXmppClient, action)(client, "whatsapp.example.invalid", *args)
                self.assertEqual(len(emitted), 1)
                self.assertIsInstance(emitted[0], XmppError)

    async def test_successful_discovery_without_feature_retains_legacy_support(self):
        component = "whatsapp.example.invalid"
        info = SimpleNamespace(xml=ET.fromstring(f'<query xmlns="{DISCO_NS}"/>'))
        get_info = AsyncMock(return_value=info)

        class Client(SimpleNamespace):
            def __getitem__(self, _):
                return SimpleNamespace(get_info=get_info)

        client = Client(
            _session_generation=1, _adhoc_commands=AsyncMock(return_value=[]),
            _debug_whatsapp_commands=Mock(), _whatsapp_command_state=Mock(return_value="unknown"),
            _last_whatsapp_status_by_component={},
        )
        client._linking = LinkingProtocol(client)
        result = await BridgeXmppClient._debug_whatsapp_component_commands(client, component)
        self.assertEqual(result, "unknown")
        client._adhoc_commands.assert_awaited_once_with(component)
        self.assertEqual(client._linking.pollers, {})


class GroupJoinRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(
            _session_generation=7, _group_join_retry_attempts={},
            _joined_group_chat_jids={"#fixture@whatsapp.example.invalid"},
            _group_roster_requests={"#fixture@whatsapp.example.invalid"},
            _schedule_group_rejoin=Mock(), _emit_group_participants_from_roster=Mock(),
        )

    def future(self, condition=""):
        future = asyncio.get_running_loop().create_future()
        if condition:
            presence = Presence()
            presence["type"] = "error"
            presence["error"]["condition"] = condition
            future.set_exception(PresenceError(presence))
        else:
            future.set_result(None)
        return future

    async def test_transient_join_retries_are_bounded_and_preserve_participant_request(self):
        jid = "#fixture@whatsapp.example.invalid"
        for condition in (
            "service-unavailable", "remote-server-not-found", "remote-server-timeout",
        ):
            with self.subTest(condition=condition):
                client = self.client()
                for delay in (2, 5, 15, 30):
                    BridgeXmppClient._finish_group_join(client, jid, self.future(condition), 7)
                    client._schedule_group_rejoin.assert_called_with(jid, delay=delay)
                    self.assertNotIn(jid, client._joined_group_chat_jids)
                    self.assertIn(jid, client._group_roster_requests)
                BridgeXmppClient._finish_group_join(client, jid, self.future(condition), 7)
                self.assertEqual(client._schedule_group_rejoin.call_count, 4)
                self.assertNotIn(jid, client._group_roster_requests)

    async def test_denials_cancelled_and_stale_joins_never_retry_or_retire_new_occupant(self):
        jid = "#fixture@whatsapp.example.invalid"
        for condition in (
            "forbidden", "not-authorized", "registration-required", "cancelled", "stale",
        ):
            with self.subTest(condition=condition):
                client = self.client()
                future = self.future("service-unavailable" if condition == "stale" else
                                     (condition if condition != "cancelled" else ""))
                if condition == "cancelled":
                    future = asyncio.get_running_loop().create_future()
                    future.cancel()
                BridgeXmppClient._finish_group_join(
                    client, jid, future, 6 if condition == "stale" else 7,
                )
                client._schedule_group_rejoin.assert_not_called()
                self.assertEqual(jid in client._joined_group_chat_jids, condition == "stale")

    async def test_success_resets_retry_budget_and_delivers_requested_participants(self):
        client, jid = self.client(), "#fixture@whatsapp.example.invalid"
        client._group_join_retry_attempts[jid] = 3
        BridgeXmppClient._finish_group_join(client, jid, self.future(), 7)
        self.assertNotIn(jid, client._group_join_retry_attempts)
        client._emit_group_participants_from_roster.assert_called_once_with(jid)

    async def test_real_join_callback_is_bound_to_its_session(self):
        jid = "#fixture@whatsapp.example.invalid"

        class Client(SimpleNamespace):
            def __getitem__(self, _):
                return self.muc

            def _finish_group_join(self, *args):
                BridgeXmppClient._finish_group_join(self, *args)

        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                client = Client(**vars(self.client()))
                client._joined_group_chat_jids.clear()
                future = asyncio.get_running_loop().create_future()
                client.muc = SimpleNamespace(join_muc=Mock(return_value=future))
                client._muc_nick = lambda: "fixture"
                BridgeXmppClient._join_group_chat(client, jid, request_roster=True)
                if replaced:
                    client._session_generation += 1
                presence = Presence()
                presence["error"]["condition"] = "service-unavailable"
                future.set_exception(PresenceError(presence))
                await asyncio.sleep(0)
                if replaced:
                    self.assertIn(jid, client._joined_group_chat_jids)
                    client._schedule_group_rejoin.assert_not_called()
                else:
                    client._schedule_group_rejoin.assert_called_once_with(jid, delay=2)


class LinkingLegacyAuthorityTests(unittest.TestCase):
    def test_disconnect_event_carries_the_closed_protocol_generation(self):
        emitted = []
        client = SimpleNamespace(
            _linking=SimpleNamespace(generation=10, close=Mock()),
            _disconnect_requested=True, loop=None, _emit=emitted.append,
            _clear_transient_message_retries=Mock(), _stop_group_membership_watchdog=Mock(),
        )
        BridgeXmppClient._on_disconnected(client, None)
        client._linking.close.assert_called_once()
        self.assertIsInstance(emitted[0], XmppDisconnected)
        self.assertEqual(emitted[0].generation, 10)

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
            whatsapp_verified=False, whatsapp_link_recovering=False,
            pending_roster_chats=[], connection_header=Mock(), status_bar=Mock(),
            _set_whatsapp_remote_actions_enabled=Mock(),
            _apply_pending_roster_if_ready=Mock(),
        )

    def test_transient_outage_keeps_chat_and_focus_but_revokes_send_until_authenticated(self):
        from cliente_xmpp.ui.main_window import MainWindow

        for reason, changed_roster in (
            ("disconnected", False), ("status_unavailable", False), ("disconnected", True),
        ):
            with self.subTest(reason=reason, changed_roster=changed_roster):
                window = self.window()
                window.whatsapp_verified = True
                event = WhatsAppLinkState(
                    window.current_jid, "whatsapp.example.invalid", 10,
                    "fixture-attempt", 3,
                    "connecting" if reason == "disconnected" else "connection_error",
                    False, reason,
                )
                with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
                    MainWindow._handle_whatsapp_link_state(window, event)
                    self.assertFalse(window.whatsapp_verified)
                    window._set_whatsapp_remote_actions_enabled.assert_called_once_with(False)
                    window._handle_whatsapp_bridge_status.assert_not_called()
                    if changed_roster:
                        window.pending_roster_chats = ["new roster fixture"]
                    event.status, event.authenticated, event.reason = "connected", True, ""
                    event.revision += 1
                    MainWindow._handle_whatsapp_link_state(window, event)
                    self.assertTrue(window.whatsapp_verified)
                    event.sync_phase, event.revision = "ready", event.revision + 1
                    MainWindow._handle_whatsapp_link_state(window, event)
                    window._set_whatsapp_remote_actions_enabled.assert_called_with(True)
                    window._handle_whatsapp_bridge_status.assert_not_called()
                    if changed_roster:
                        window._apply_pending_roster_if_ready.assert_called_once()
                    else:
                        window._apply_pending_roster_if_ready.assert_not_called()
                    call_after.assert_not_called()

    def test_real_logout_after_transient_outage_still_opens_linking(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        window.whatsapp_verified = True
        event = WhatsAppLinkState(window.current_jid, "whatsapp.example.invalid", 10,
                                  "fixture-attempt", 3, "connecting", False, "disconnected")
        MainWindow._handle_whatsapp_link_state(window, event)
        event.status, event.reason, event.revision = "logged_out", "device_removed", 4
        MainWindow._handle_whatsapp_link_state(window, event)
        self.assertFalse(window.whatsapp_verified)
        self.assertFalse(window.whatsapp_link_recovering)
        window._handle_whatsapp_bridge_status.assert_called_once_with(
            "logged_out", "whatsapp.example.invalid", "",
        )

    def test_delayed_disconnect_uses_producer_generation_and_accepts_next_session(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        window._storage_reset_in_progress = False
        window.development_mode = True
        window.login_panel = Mock()
        with patch("cliente_xmpp.ui.main_window.time.monotonic_ns", return_value=999999):
            MainWindow._handle_xmpp_event(window, XmppDisconnected("Reconectando...", 10))
        self.assertEqual(window.whatsapp_link_generation, 11)
        self.assertFalse(window.whatsapp_link_protocol)
        self.assertIsNone(window.whatsapp_link_state_key)
        for generation in (10, 20):
            event = WhatsAppLinkState(window.current_jid, "whatsapp.example.invalid", generation,
                                      "fixture-attempt", 3, "connected", True)
            MainWindow._handle_whatsapp_link_state(window, event)
        window._handle_whatsapp_bridge_status.assert_called_once()
        self.assertTrue(window.whatsapp_verified)
        self.assertEqual(window.whatsapp_link_generation, 20)
        window._set_whatsapp_remote_actions_enabled.reset_mock()
        MainWindow._handle_xmpp_event(window, XmppDisconnected("old connection", 10))
        window._set_whatsapp_remote_actions_enabled.assert_not_called()
        self.assertTrue(window.whatsapp_verified)

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
