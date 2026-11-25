"""Client LinkingProtocol wired in-process to the real bridge IQ handler.

Every account, number, code and image here is fictitious. No network, no
WhatsApp and no SQLite: the native WhatsMeow side is a scripted fake that
emits the same hooks Slidge receives from the Go bridge.
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from cliente_xmpp.xmpp.events import WhatsAppLinkState
from cliente_xmpp.xmpp.linking import NAMESPACE, LinkingProtocol
from tools import bridge_linking
from tools.bridge_linking import LinkingError, install, state

ACCOUNT = "fixture@example.invalid"
COMPONENT = "whatsapp.example.invalid"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def fake_qrcode(raw):
    # Distinct, valid-looking PNG per QR payload, so rotations are observable.
    return SimpleNamespace(save=lambda stream, **_: stream.write(PNG_HEADER + raw.encode()))


class NativeSession:
    """Stands in for slidge_whatsapp.Session before install_session wraps it."""

    def __init__(self, gateway):
        self.xmpp = gateway
        self.user_jid = SimpleNamespace(bare=ACCOUNT)
        self.whatsapp = SimpleNamespace(Disconnect=Mock(), PairPhone=Mock(return_value="FAKE-CODE"))
        self.logged = False
        self.is_logging_in = False
        self.native = None
        self.persisted_devices = []
        self.logins = 0

    async def login(self):
        self.logins += 1
        self.native = asyncio.get_running_loop().create_future()
        return await self.native

    async def on_wa_qr(self, value):
        pass

    async def on_wa_pair(self, device_id):
        self.persisted_devices.append(device_id)

    async def on_wa_connect(self, event):
        if self.native is not None and not self.native.done():
            if event.Error:
                self.native.set_exception(RuntimeError(event.Error))
            else:
                self.native.set_result("Logged in")

    async def logout(self):
        pass

    async def on_wa_logged_out(self, event):
        pass


class FakeIq:
    def __init__(self, xml, replies):
        self.xml = xml
        self.replies = replies

    def __getitem__(self, key):
        if key == "type":
            return self.xml.get("type")
        if key == "from":
            return SimpleNamespace(bare=ACCOUNT, resource="can")
        if key == "to":
            return SimpleNamespace(bare=COMPONENT)
        raise KeyError(key)

    def reply(self):
        replies = self.replies
        node = ET.Element("iq", {"type": "result", "from": COMPONENT})

        class Reply:
            xml = node

            def send(self):
                replies.append(node)

        return Reply()


class Harness:
    def __init__(self):
        self.messages = []
        self.drop_replies: set[str] = set()
        harness = self

        class Gateway(SimpleNamespace):
            def __getitem__(self, _):
                return SimpleNamespace(add_feature=Mock())

            def register_handler(self, handler):
                harness.handler = handler._pointer

            def make_message(self, mto, mfrom, mtype):
                node = ET.Element("message", {"to": mto, "type": mtype, "from": mfrom})
                return SimpleNamespace(xml=node, send=lambda: harness.messages.append(node))

        async def login_wrap(session):
            return await session.login()

        self.gateway = Gateway(
            default_ns="jabber:component:accept", GROUPS=False,
            boundjid=SimpleNamespace(bare=COMPONENT), login_wrap=login_wrap,
            raise_if_not_allowed_jid=Mock(),
            get_session_from_jid=lambda _: harness.session,
        )
        self.session_class = type("Session", (NativeSession,), {})
        with patch.dict("sys.modules", {"tools.session": SimpleNamespace(
                Session=self.session_class)}):
            install(self.gateway)
        self.session = self.session_class(self.gateway)
        self.owner = state(self.session)
        self.emitted = []

        def make_iq(kind):
            def factory():
                iq = SimpleNamespace(xml=ET.Element("iq", {"type": kind}))

                class Sender:
                    def __setitem__(self, key, value):
                        iq.xml.set(key, value)

                    async def send(self, timeout=None):
                        return await harness.deliver(iq.xml, timeout)

                sender = Sender()
                sender.xml = iq.xml
                return sender
            return factory

        self.client = SimpleNamespace(
            _session_generation=1, boundjid=SimpleNamespace(bare=ACCOUNT),
            make_iq_get=make_iq("get"), make_iq_set=make_iq("set"),
            _emit=self.emitted.append, _whatsapp_session_ready=False,
            _start_initial_remote_sync_if_ready=Mock(),
        )
        self.protocol = LinkingProtocol(self.client)
        self.protocol.supported[COMPONENT] = True

    async def deliver(self, xml, timeout):
        replies = []
        await asyncio.wait_for(self.handler(FakeIq(xml, replies)), timeout or 40)
        query = xml.find(f"{{{NAMESPACE}}}query")
        if query is not None and query.get("op", "status") in self.drop_replies:
            raise TimeoutError("fixture: reply lost")
        return SimpleNamespace(xml=replies[0])

    async def qr(self, code, ttl=20.0):
        await self.session.on_wa_qr(json.dumps({"code": code, "ttl": ttl}))

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    def states(self):
        return [e for e in self.emitted if isinstance(e, WhatsAppLinkState)]


@patch.dict("sys.modules", {"qrcode": SimpleNamespace(make=fake_qrcode)})
class LinkingContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = Harness()

    async def asyncTearDown(self):
        self.h.protocol.close()
        for task in (self.h.owner.task, self.h.owner.bootstrap, self.h.owner.sync_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.sleep(0)

    async def connect(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        event = await h.protocol.request(COMPONENT)
        self.assertEqual(event.status, "needs_qr")
        self.assertTrue(event.image_data.endswith(b"fictional-qr-1"))
        await h.session.on_wa_pair("fictional-device")
        await h.session.on_wa_connect(SimpleNamespace(Error="", JID=ACCOUNT))
        await h.settle()
        event = await h.protocol.request(COMPONENT)
        self.assertTrue(event.authenticated)
        return event

    async def test_full_qr_flow_then_relogin_after_logout_starts_a_new_attempt(self):
        h = self.h
        first = await self.connect()
        self.assertEqual(h.session.logins, 1)
        await h.session.logout()
        event = await h.protocol.request(COMPONENT)
        self.assertEqual(event.status, "needs_relogin")
        # Point 1: the token of the finished attempt must not turn start into a no-op.
        await h.protocol.start(COMPONENT)
        await h.settle()
        self.assertEqual(h.session.logins, 2)
        self.assertNotEqual(h.owner.attempt, first.attempt)
        self.assertEqual(h.owner.phase, "connecting")
        self.assertFalse(hasattr(h.session.whatsapp, "Logout"))

    async def test_replayed_old_token_is_refused_and_client_retries_with_a_new_one(self):
        h = self.h
        await self.connect()
        token = h.protocol.pending_requests.get(COMPONENT)
        self.assertIsNone(token)  # Cleared as soon as its attempt authenticated.
        await h.session.on_wa_logged_out(SimpleNamespace())
        # Simulate a client that still holds the old token (older builds did).
        old = next(iter(h.owner.requests))
        h.protocol.pending_requests[COMPONENT] = old
        await h.protocol.start(COMPONENT)
        await h.settle()
        self.assertEqual(h.session.logins, 2)
        self.assertIn("stale_request", [e.reason for e in h.states()])
        self.assertNotEqual(h.protocol.pending_requests.get(COMPONENT), old)

    async def test_phone_after_device_removal_starts_fresh_without_waiting(self):
        h = self.h
        await self.connect()
        await h.session.on_wa_logged_out(SimpleNamespace())
        self.assertEqual(h.owner.phase, "logged_out")
        started = time.monotonic()
        task = asyncio.create_task(h.protocol.phone(COMPONENT, "+12025550100"))
        for _ in range(50):
            await asyncio.sleep(0)
            if h.owner.phase == "connecting" and h.session.logins == 2:
                break
        self.assertEqual(h.session.logins, 2)
        await h.qr("fictional-qr-2")
        event = await asyncio.wait_for(task, 2)
        # Point 2: no 15-second readiness wait on an attempt that had ended.
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(event.status, "needs_pair_code")
        self.assertEqual(event.code, "FAKE-CODE")
        h.session.whatsapp.PairPhone.assert_called_once_with("+12025550100")

    async def test_bad_number_and_failed_pair_phone_keep_the_live_attempt(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        attempt = h.owner.attempt
        event = await h.protocol.phone(COMPONENT, "+1")
        self.assertEqual(event.reason, "invalid_phone")
        self.assertEqual(h.owner.phase, "needs_qr")
        h.session.whatsapp.PairPhone.side_effect = [RuntimeError("fixture rejection"),
                                                     "FAKE-CODE"]
        event = await h.protocol.phone(COMPONENT, "+12025550100")
        self.assertEqual(event.reason, "phone_pair_failed")
        # Point 3: the attempt survives; a corrected number is accepted.
        self.assertEqual((h.owner.attempt, h.owner.phase), (attempt, "needs_qr"))
        event = await h.protocol.phone(COMPONENT, "+12025550199")
        self.assertEqual((event.code, event.attempt), ("FAKE-CODE", attempt))

    async def test_rotation_never_serves_the_previous_png_under_the_new_revision(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        await h.protocol.request(COMPONENT)
        gate = asyncio.Event()
        original = asyncio.to_thread

        async def slow_render(fn, *args):
            if getattr(fn, "__name__", "") == "render":
                await gate.wait()
            return await original(fn, *args)

        with patch("tools.bridge_linking.asyncio.to_thread", side_effect=slow_render):
            rotation = asyncio.create_task(h.qr("fictional-qr-2"))
            await h.settle()
            during = await h.protocol.request(COMPONENT)
            # Point 4: the new revision is announced without any image yet.
            self.assertEqual(during.qr_revision, 2)
            self.assertEqual(during.image_data, b"")
            gate.set()
            await rotation
        after = await h.protocol.request(COMPONENT)
        self.assertTrue(after.image_data.endswith(b"fictional-qr-2"))

    async def test_client_shows_a_corrected_image_even_with_a_repeated_revision(self):
        # Defence for bridges that predate the fix above.
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        h.owner.qr = PNG_HEADER + b"stale"
        stale = await h.protocol.request(COMPONENT)
        self.assertTrue(stale.image_data.endswith(b"stale"))
        h.owner.qr = PNG_HEADER + b"fresh"
        fresh = await h.protocol.request(COMPONENT)
        self.assertTrue(fresh.image_data.endswith(b"fresh"))
        again = await h.protocol.request(COMPONENT)
        self.assertEqual(again.image_data, b"")

    async def test_qr_timeout_is_reported_as_expired_and_retry_needs_no_cancel(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        first = h.owner.attempt
        await h.session.on_wa_connect(SimpleNamespace(Error="can-link:expired", JID=""))
        await h.settle()  # The failed native login must not overwrite the reason.
        event = await h.protocol.request(COMPONENT)
        # Point 5: an expired QR is not a generic connection error.
        self.assertEqual((event.status, event.reason), ("expired", "expired"))
        await h.protocol.start(COMPONENT)
        await h.settle()
        self.assertNotEqual(h.owner.attempt, first)
        self.assertEqual(h.session.logins, 2)
        h.session.whatsapp.Disconnect.assert_called()  # Idempotent; never Logout.
        self.assertFalse(hasattr(h.session.whatsapp, "Logout"))

    async def test_scan_that_wins_the_cancel_race_is_reported_and_reused(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        await h.protocol.request(COMPONENT)

        def disconnect():
            # The phone accepts the scan while Disconnect is in flight.
            asyncio.run_coroutine_threadsafe(h.session.on_wa_pair("fictional-device"), loop)

        loop = asyncio.get_running_loop()
        h.session.whatsapp.Disconnect.side_effect = disconnect
        self.assertTrue(await h.protocol.cancel(COMPONENT))
        await h.settle()
        event = await h.protocol.request(COMPONENT)
        # Point 7: never "canceled" silently while a device was persisted.
        self.assertEqual((event.status, event.reason), ("canceled", "paired_after_cancel"))
        self.assertEqual(h.session.persisted_devices, ["fictional-device"])
        await h.protocol.start(COMPONENT)
        await h.settle()
        self.assertEqual(h.owner.reason, "")
        self.assertEqual(h.session.logins, 2)

    async def test_deadline_is_a_timer_and_status_reads_are_pure(self):
        h = self.h
        # Only the pairing deadline is short; the native login keeps its own wait,
        # so connection_timeout can only come from the bridge timer.
        with patch.object(bridge_linking, "LOGIN_TIMEOUT", 0.05):
            h.owner.reserve()
        await h.protocol.start(COMPONENT)
        revision = h.owner.revision
        for _ in range(3):
            h.owner.snapshot()
        self.assertEqual(h.owner.revision, revision)  # Point 8.
        await asyncio.sleep(0.15)
        self.assertEqual((h.owner.phase, h.owner.reason),
                         ("connection_error", "connection_timeout"))
        event = await h.protocol.request(COMPONENT)
        self.assertEqual(event.reason, "connection_timeout")

    async def test_lost_phone_reply_is_recovered_from_status_once(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        h.drop_replies = {"phone"}
        with self.assertRaises(TimeoutError):
            await h.protocol.phone(COMPONENT, "+12025550100")
        h.drop_replies = set()
        first = await h.protocol.request(COMPONENT)
        self.assertEqual(first.code, "FAKE-CODE")
        second = await h.protocol.request(COMPONENT)
        self.assertEqual(second.code, "")  # Announced once; the UI keeps it.
        await h.qr("fictional-qr-2")
        rotated = await h.protocol.request(COMPONENT)
        self.assertEqual(rotated.code, "")
        h.session.whatsapp.PairPhone.assert_called_once()

    async def test_manual_sync_retry_after_exhaustion(self):
        h = self.h
        await self.connect()
        h.gateway.GROUPS = False
        loop = asyncio.get_running_loop()
        contacts = SimpleNamespace(ready=loop.create_future(), _fill=Mock())
        h.session.contacts = contacts
        h.session.bookmarks = SimpleNamespace(ready=loop.create_future())
        h.session.send_gateway_status = Mock()
        entered, exited = [], []
        h.gateway.store = SimpleNamespace(session=lambda: SimpleNamespace(
            __enter__=None))

        class Orm:
            def __enter__(self):
                entered.append(1)
                return self

            def __exit__(self, *_):
                exited.append(1)
                return False

        h.gateway.store = SimpleNamespace(session=Orm)

        async def fill(_orm):
            # Point 9: exactly one connection is held, only during the fill itself.
            self.assertEqual((len(entered), len(exited)), (1, 0))

        contacts._fill = fill
        h.owner.sync_error = True
        sleeps = []
        real_sleep = asyncio.sleep

        async def sleep(delay):
            sleeps.append(delay)
            await real_sleep(0)

        with patch("tools.bridge_linking.asyncio.sleep", side_effect=sleep):
            event = await h.protocol.sync(COMPONENT)
            await h.owner.sync_task
        self.assertEqual(sleeps, [0])
        self.assertEqual((len(entered), len(exited)), (1, 1))
        self.assertFalse(h.owner.sync_error)
        self.assertEqual(event.reason, "")
        h.session.send_gateway_status.assert_called_once()

    async def test_sync_is_refused_while_not_connected(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        event = await h.protocol.sync(COMPONENT)
        self.assertEqual(event.reason, "not_connected")

    async def test_state_changes_send_a_hint_without_secrets_and_wake_polling(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        await h.settle()
        self.assertTrue(h.messages)
        for message in h.messages:
            self.assertEqual(message.get("type"), "normal")
            self.assertIsNone(message.find("body"))
            self.assertIsNotNone(message.find("{urn:xmpp:hints}no-store"))
            self.assertIsNotNone(message.find("{urn:xmpp:hints}no-permanent-store"))
            self.assertEqual(message.get("to"), ACCOUNT)
            self.assertEqual(message.get("from"), COMPONENT)
            hint = message.find(f"{{{NAMESPACE}}}state")
            self.assertEqual(set(hint.attrib), {"version", "attempt", "revision"})
            self.assertEqual(list(hint), [])  # No QR, no code.
        requests = []

        async def request(component):
            requests.append(component)
            h.protocol.closed = True

        h.protocol.request = request
        h.protocol.states[COMPONENT] = WhatsAppLinkState(
            ACCOUNT, COMPONENT, 1, "a", 1, "connected", True, sync_phase="ready")
        self.assertEqual(h.protocol.interval(COMPONENT, 0), 60.0)
        poller = asyncio.create_task(h.protocol.poll(COMPONENT))
        await h.settle()
        self.assertEqual(requests, [])  # Stable session: no 3-second polling.
        h.protocol.wake(COMPONENT)
        await asyncio.wait_for(poller, 1)
        self.assertEqual(requests, [COMPONENT])

    async def test_phone_reply_does_not_block_status_polls(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        # No QR yet: the bridge waits for native readiness inside the phone IQ.
        phone = asyncio.create_task(h.protocol.phone(COMPONENT, "+12025550100"))
        await h.settle()
        status = await asyncio.wait_for(h.protocol.request(COMPONENT), 1)
        self.assertEqual(status.status, "connecting")
        await h.qr("fictional-qr-1")
        self.assertEqual((await asyncio.wait_for(phone, 2)).code, "FAKE-CODE")

    async def test_paired_phase_refuses_phone_immediately(self):
        h = self.h
        await h.protocol.start(COMPONENT)
        await h.settle()
        await h.qr("fictional-qr-1")
        await h.session.on_wa_pair("fictional-device")
        with self.assertRaisesRegex(LinkingError, "already_paired"):
            await asyncio.wait_for(h.owner.phone(h.owner.attempt, "+12025550100"), 1)


class LinkingUiRegressionTests(unittest.TestCase):
    def window(self, **values):
        base = dict(
            current_jid=ACCOUNT, whatsapp_link_generation=10,
            whatsapp_link_attempt="fixture-attempt", whatsapp_link_revision=2,
            whatsapp_link_protocol=True, whatsapp_qr_restart_after_cancel=True,
            whatsapp_link_state_key=None, whatsapp_qr_deadline=0,
            whatsapp_pair_phone_pending="", whatsapp_pairing_bootstrap_generation=0,
            whatsapp_link_error=None, whatsapp_pair_code=None, whatsapp_code_dialog=None,
            whatsapp_sync_menu_item=Mock(), whatsapp_qr_request_in_flight=True,
            whatsapp_qr_dialog=None, whatsapp_link_session=None,
            whatsapp_verified=False, whatsapp_link_recovering=False,
            pending_roster_chats=[], connection_header=Mock(), status_bar=Mock(),
            whatsapp_link_panel=Mock(), workspace_panel=Mock(), speaker=Mock(),
            _close_whatsapp_qr_dialog=Mock(), _begin_whatsapp_qr_request=Mock(),
            _handle_whatsapp_bridge_status=Mock(), _handle_whatsapp_qr_image_data=Mock(),
            _handle_whatsapp_pairing_code=Mock(), _set_whatsapp_remote_actions_enabled=Mock(),
            _apply_pending_roster_if_ready=Mock(),
            _has_cancelable_whatsapp_link=Mock(return_value=True),
        )
        base.update(values)
        return SimpleNamespace(**base)

    def event(self, status, reason="", revision=3, **values):
        return WhatsAppLinkState(ACCOUNT, COMPONENT, 10, "fixture-attempt", revision,
                                 status, values.pop("authenticated", False), reason, **values)

    def test_every_bridge_reason_has_a_specific_message(self):
        from cliente_xmpp.ui.main_window import (
            WHATSAPP_LINK_OPERATION_ERRORS,
            WHATSAPP_LINK_REASON_MESSAGES,
            MainWindow,
        )

        reasons = {"expired", "device_removed", "already_connected", "already_paired",
                   "invalid_phone", "phone_pair_failed", "attempt_ended", "pair_failed",
                   "stale_request", "invalid_request", "paired_after_cancel",
                   "not_connected", "unsupported_operation", "invalid_qr_event"}
        self.assertLessEqual(reasons, set(WHATSAPP_LINK_REASON_MESSAGES))
        self.assertIn("expired", WHATSAPP_LINK_OPERATION_ERRORS)
        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter"):
            MainWindow._handle_whatsapp_link_state(window, self.event("expired", "expired"))
        # Point 5: expiry is explained, not shown as a generic connection error.
        window._handle_whatsapp_bridge_status.assert_called_once_with(
            "needs_relogin", COMPONENT, WHATSAPP_LINK_REASON_MESSAGES["expired"],
        )

    def test_operation_error_survives_the_next_identical_status_poll(self):
        from cliente_xmpp.ui.main_window import WHATSAPP_LINK_REASON_MESSAGES, MainWindow

        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter"):
            MainWindow._handle_whatsapp_link_state(
                window, self.event("needs_qr", "invalid_phone"))
            MainWindow._handle_whatsapp_link_state(window, self.event("needs_qr"))
            window._handle_whatsapp_bridge_status.assert_called_once_with(
                "needs_qr", COMPONENT, WHATSAPP_LINK_REASON_MESSAGES["invalid_phone"],
            )
            MainWindow._handle_whatsapp_link_state(window, self.event("needs_qr", revision=4))
        self.assertEqual(window._handle_whatsapp_bridge_status.call_count, 2)
        self.assertIsNone(window.whatsapp_link_error)

    def test_unconfirmed_cancel_drops_restart_intent(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
            MainWindow._handle_whatsapp_link_cancel_result(window, COMPONENT, False, False)
            # Point 6: a later, unrelated "canceled" must not start a QR.
            self.assertFalse(window.whatsapp_qr_restart_after_cancel)
            MainWindow._handle_whatsapp_link_state(window, self.event("canceled", revision=9))
        window._begin_whatsapp_qr_request.assert_not_called()
        self.assertNotIn(window._begin_whatsapp_qr_request,
                         [c.args[0] for c in call_after.call_args_list])

    def test_nothing_to_cancel_starts_directly(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
            MainWindow._handle_whatsapp_link_cancel_result(window, COMPONENT, False, True)
        self.assertFalse(window.whatsapp_qr_restart_after_cancel)
        call_after.assert_called_once_with(window._begin_whatsapp_qr_request)

    def test_paired_after_cancel_never_stacks_a_new_qr(self):
        from cliente_xmpp.ui.main_window import WHATSAPP_LINK_REASON_MESSAGES, MainWindow

        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as call_after:
            MainWindow._handle_whatsapp_link_state(
                window, self.event("canceled", "paired_after_cancel"))
        self.assertFalse(window.whatsapp_qr_restart_after_cancel)
        self.assertNotIn(window._begin_whatsapp_qr_request,
                         [c.args[0] for c in call_after.call_args_list])
        window._handle_whatsapp_bridge_status.assert_called_once_with(
            "needs_relogin", COMPONENT, WHATSAPP_LINK_REASON_MESSAGES["paired_after_cancel"],
        )

    def test_code_is_kept_for_redisplay_and_sync_retry_follows_sync_phase(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window(whatsapp_pair_phone_pending="+12025550100")
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter"):
            MainWindow._handle_whatsapp_link_state(
                window, self.event("needs_pair_code", code="FAKE-CODE"))
            self.assertEqual(window.whatsapp_pair_code, ("fixture-attempt", "FAKE-CODE"))
            window._handle_whatsapp_pairing_code.assert_called_once()
            MainWindow._handle_whatsapp_link_state(
                window, self.event("connected", revision=4, authenticated=True,
                                   sync_phase="error"))
        self.assertIsNone(window.whatsapp_pair_code)
        window.whatsapp_sync_menu_item.Enable.assert_called_with(True)

    def test_refused_phone_request_stops_suppressing_qr_images(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window(whatsapp_pair_phone_pending="+12025550100")
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter"):
            MainWindow._handle_whatsapp_link_state(
                window, self.event("needs_qr", "phone_pair_failed"))
        self.assertEqual(window.whatsapp_pair_phone_pending, "")

    def test_transient_disconnect_detail_is_not_a_logout_message(self):
        from cliente_xmpp.ui.main_window import MainWindow

        window = self.window()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter"):
            MainWindow._handle_whatsapp_link_state(
                window, self.event("connecting", "disconnected"))
        window._handle_whatsapp_bridge_status.assert_called_once_with(
            "connecting", COMPONENT, "",
        )


class LinkingHintRoutingTests(unittest.TestCase):
    def test_hint_never_reaches_chats_and_only_wakes_a_contract_component(self):
        from cliente_xmpp.xmpp.client import BridgeXmppClient

        for sender, supported, woken in (
            (COMPONENT, True, True),
            (COMPONENT, False, False),
            ("someone@" + COMPONENT, True, False),
        ):
            with self.subTest(sender=sender, supported=supported):
                node = ET.Element("message", {"type": "normal"})
                ET.SubElement(node, f"{{{NAMESPACE}}}state", {"version": "1"})
                linking = SimpleNamespace(supported={COMPONENT: supported}, wake=Mock())
                client = SimpleNamespace(_linking=linking)
                handled = BridgeXmppClient._handle_whatsapp_linking_hint(
                    client, SimpleNamespace(xml=node), sender)
                self.assertTrue(handled)
                self.assertEqual(linking.wake.called, woken)

    def test_bodyless_hint_reaches_the_client_through_its_own_handler(self):
        # slixmpp raises "message" only for stanzas with a body; the real client
        # must route the bodyless hint through a dedicated stream handler.
        from slixmpp import Message

        from cliente_xmpp.config.settings import ConnectionSettings
        from cliente_xmpp.xmpp.client import BridgeXmppClient

        client = BridgeXmppClient(ConnectionSettings(jid=ACCOUNT, host="127.0.0.1"),
                                  "fictional-password", Mock())
        client._linking = SimpleNamespace(supported={COMPONENT: True}, wake=Mock())
        stanza = Message(client, sfrom=COMPONENT, sto=ACCOUNT, stype="normal")
        stanza.xml.append(ET.Element(f"{{{NAMESPACE}}}state", {"version": "1"}))
        matching = [h for h in client._XMLStream__handlers if h.match(stanza)]
        self.assertEqual([h.name for h in matching], ["CAN WhatsApp linking hint"])
        matching[0].run(stanza)
        client._linking.wake.assert_called_once_with(COMPONENT)

    def test_ordinary_message_is_not_a_hint(self):
        from cliente_xmpp.xmpp.client import BridgeXmppClient

        node = ET.Element("message", {"type": "chat"})
        ET.SubElement(node, "body").text = "fictional text"
        client = SimpleNamespace(_linking=SimpleNamespace(supported={}, wake=Mock()))
        self.assertFalse(BridgeXmppClient._handle_whatsapp_linking_hint(
            client, SimpleNamespace(xml=node), COMPONENT))


if __name__ == "__main__":
    unittest.main()
