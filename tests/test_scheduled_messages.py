from __future__ import annotations

import asyncio
import ctypes
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import Future
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import wx
from aiohttp.test_utils import TestClient, TestServer

from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.integrations.scheduled_messages import ScheduledMessageService, dispatch_due
from cliente_xmpp.models.scheduled_message import (
    MAX_SCHEDULED_MESSAGE_CHARS,
    ScheduleRequest,
    format_due,
    parse_local_schedule,
    validate_due,
    validate_text,
)
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore
from cliente_xmpp.ui.atajos_integration import AtajosIntegrationMixin
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.scheduled_message_dialog import (
    ScheduledMessagesDialog,
    ScheduleMessageDialog,
    ScheduleReviewDialog,
)
from cliente_xmpp.ui.scheduled_messages import ScheduledMessagesMixin
from cliente_xmpp.xmpp.client import XmppService

ACCOUNT, JID = "owner@example.test", "contact@example.test"
CONTACTS = [(JID, "Contacto ficticio", False)]


def windows_accessible_name(control):
    """Read the native MSAA name, not wx's independent GetName property."""
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    class Values(ctypes.Union):
        # VARIANT's record member has two pointers, including on 64-bit Windows.
        _fields_ = [("lVal", ctypes.c_long), ("record", ctypes.c_void_p * 2)]

    class Variant(ctypes.Structure):
        _fields_ = [("vt", wintypes.WORD), ("r1", wintypes.WORD),
                    ("r2", wintypes.WORD), ("r3", wintypes.WORD), ("value", Values)]

    api = ctypes.WinDLL("oleacc")
    api.AccessibleObjectFromWindow.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)
    ]
    api.AccessibleObjectFromWindow.restype = ctypes.c_long
    iid = GUID((ctypes.c_ubyte * 16).from_buffer_copy(
        uuid.UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le
    ))
    accessible = ctypes.c_void_p()
    result = api.AccessibleObjectFromWindow(
        control.GetHandle(), 0xfffffffc, ctypes.byref(iid), ctypes.byref(accessible)
    )
    if result != 0:
        raise AssertionError(f"AccessibleObjectFromWindow HRESULT: {result}")
    table = ctypes.cast(
        accessible, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    name = ctypes.c_void_p()
    try:
        get_name = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, Variant, ctypes.POINTER(ctypes.c_void_p)
        )(table[10])
        child = Variant()
        child.vt = 3  # VT_I4, CHILDID_SELF=0
        result = get_name(accessible, child, ctypes.byref(name))
        if result != 0 or not name.value:
            raise AssertionError(f"Missing native accessible name, HRESULT: {result}")
        return ctypes.wstring_at(name)
    finally:
        if name.value:
            free = ctypes.WinDLL("oleaut32").SysFreeString
            free.argtypes = [ctypes.c_void_p]
            free(name)
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(accessible)


class ScheduleValidationTests(unittest.TestCase):
    def test_text_and_dates_have_strict_shared_limits(self):
        self.assertEqual(validate_text("Mensaje ficticio\nDos"), "Mensaje ficticio\nDos")
        self.assertEqual(MAX_SCHEDULED_MESSAGE_CHARS, 10_000)
        self.assertEqual(validate_text("a" * 10_000), "a" * 10_000)
        for body in ("", "   ", "a" * 10_001, "bad\x00text", "/stats"):
            with self.subTest(body=body[:15]), self.assertRaises(ValueError):
                validate_text(body)
        for due in (100, 99, float("nan"), float("inf"), 100 + 367 * 86400):
            with self.subTest(due=due), self.assertRaises(ValueError):
                validate_due(due, "hold", 100)
        validate_due(101, "hold", 100)
        validate_due(100, "send-when-connected", 100, grace=30)

    def test_local_date_is_round_tripped_and_format_is_explicit(self):
        date = parse_local_schedule("15/01/27", "12:30")
        self.assertEqual(date.strftime("%Y-%m-%d %H:%M"), "2027-01-15 12:30")
        self.assertTrue(format_due(date.timestamp()).startswith("15/01/27 12:30"))
        self.assertIsNotNone(date.tzinfo)
        for day, hour in (("15/01/2027", "12:30"), ("2027-01-15", "12:30"),
                          ("30/02/27", "12:30"), ("29/02/27", "12:30"),
                          ("15/01/27", "25:00"), ("15/01/27", "9:30"),
                          ("6/10/26", "12:30")):
            with self.subTest(day=day, hour=hour), self.assertRaises(ValueError):
                parse_local_schedule(day, hour)

    def test_short_dates_use_day_month_and_explicit_twenty_first_century(self):
        for value, expected in (("06/10/26", (2026, 10, 6)),
                                ("12/11/26", (2026, 11, 12)),
                                ("29/02/28", (2028, 2, 29)),
                                ("01/01/69", (2069, 1, 1))):
            with self.subTest(date=value):
                parsed = parse_local_schedule(value, "12:30")
                self.assertEqual((parsed.year, parsed.month, parsed.day), expected)

    def test_nonexistent_and_ambiguous_local_hours_are_rejected(self):
        naive = datetime(2027, 1, 15, 12, 30)
        with patch("cliente_xmpp.models.scheduled_message.time.mktime", return_value=10):
            with self.assertRaisesRegex(ValueError, "no existe"):
                parse_local_schedule("15/01/27", "12:30")
        with (
            patch("cliente_xmpp.models.scheduled_message.time.mktime", side_effect=[10, 20, 10]),
            patch("cliente_xmpp.models.scheduled_message.datetime") as dates,
        ):
            dates.strptime.return_value = naive
            dates.fromtimestamp.return_value = naive
            with self.assertRaisesRegex(ValueError, "se repite"):
                parse_local_schedule("15/01/27", "12:30")


class OutboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ScheduledMessageStore(Path(self.temp.name) / "outbox.sqlite3")
        self.now = 1800000000.0
        self.sent = []
        self.account, self.connected = ACCOUNT, True
        self.contacts = {JID: {"name": "Contacto ficticio", "is_group": False}}

    def row(self, *, origin="native", due=None, policy="hold", account=ACCOUNT):
        return self.store.create(
            str(uuid.uuid4()), account,
            [{"jid": JID, "name": "Contacto ficticio", "text": "Prueba"}],
            self.now if due is None else due, policy, origin=origin,
        )[0]

    def snapshot(self):
        return self.account, self.connected, self.contacts

    async def tick(self, origins=("native",)):
        await dispatch_due(self.store, self.snapshot, self.sent.append, lambda: self.now,
                           lambda: False, origins)

    def state(self, row):
        return self.store.request(row["request_id"], row["account"])[0]["state"]

    async def test_native_queue_does_not_dispatch_assistant_or_another_account(self):
        for _ in range(25):
            self.row(origin="atajos")
        other = self.row(account="other@example.test")
        native = self.row()
        await self.tick()
        await self.tick()
        self.assertEqual([row["id"] for row in self.sent], [native["id"]])
        self.assertEqual(self.state(other), "pending")

    async def test_hold_and_reconnect_have_explicit_different_outcomes(self):
        held = self.row()
        wait = self.row(policy="send-when-connected")
        self.connected = False
        await self.tick()
        self.assertEqual(self.state(held), "held")
        self.assertEqual(self.state(wait), "pending")
        self.now += 3600
        self.connected = True
        await self.tick()
        self.assertEqual([row["id"] for row in self.sent], [wait["id"]])

    async def test_missing_contact_and_changed_group_identity_never_send(self):
        row = self.row(policy="send-when-connected")
        self.contacts[JID]["is_group"] = True
        await self.tick()
        self.assertEqual(self.state(row), "held")
        row = self.row()
        self.contacts.clear()
        await self.tick()
        self.assertEqual(self.state(row), "held")
        self.assertFalse(self.sent)

    async def test_late_hold_does_not_send_and_clock_backward_rechecks_before_claim(self):
        late = self.row(due=self.now - 31)
        await self.tick()
        self.assertEqual(self.state(late), "held")
        future = self.row(due=self.now + 60)
        await self.tick()
        self.assertEqual(self.state(future), "pending")
        self.assertFalse(self.sent)

    async def test_cancel_before_claim_wins_and_cancel_after_claim_loses(self):
        row = self.row()
        old = self.store.transition

        def race(identifier, expected, target, detail=""):
            if target == "dispatching":
                self.assertTrue(self.store.cancel(identifier, ACCOUNT))
            return old(identifier, expected, target, detail)

        with patch.object(self.store, "transition", side_effect=race):
            await self.tick()
        self.assertFalse(self.sent)
        row = self.row()
        await self.tick()
        self.assertFalse(self.store.cancel(row["id"], ACCOUNT))

    async def test_restart_recovers_native_inflight_but_never_duplicates_it(self):
        row = self.row()
        await self.tick()
        self.store = ScheduledMessageStore(self.store.path)
        self.assertEqual(self.state(row), "uncertain")
        await self.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_api_only_dispatches_lists_and_cancels_its_own_origin(self):
        native, assistant = self.row(), self.row(origin="atajos")
        api = LocalAssistantAPI("a" * 43, self.sent.append, store=self.store,
                                clock=lambda: self.now)
        api.update(ACCOUNT, True, CONTACTS)
        client = TestClient(TestServer(api.application(), host="127.0.0.1"))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {"Authorization": "Bearer " + "a" * 43}
        response = await client.get("/v1/messages", headers=headers)
        data = await response.json()
        self.assertEqual([row["id"] for row in data["messages"]], [assistant["id"]])
        response = await client.post(
            f"/v1/messages/{native['id']}/cancel", json={}, headers=headers
        )
        self.assertEqual(response.status, 409)
        await api.tick()
        self.assertEqual([row["id"] for row in self.sent], [assistant["id"]])
        self.assertEqual(self.state(native), "pending")

    async def test_shared_store_is_not_recovered_again_when_enabling_assistant(self):
        row = self.row()
        await self.tick()
        scheduler = SimpleNamespace(ready=Future())
        scheduler.ready.set_result(self.store)
        api = LocalAssistantAPI("a" * 43, self.sent.append, scheduler=scheduler)
        await api._ensure_store()
        self.assertIs(api._store, self.store)
        self.assertEqual(self.state(row), "dispatching")

    async def test_assistant_api_keeps_its_original_four_thousand_character_limit(self):
        api = LocalAssistantAPI("a" * 43, self.sent.append, store=self.store,
                                clock=lambda: self.now)
        api.update(ACCOUNT, True, CONTACTS)
        client = TestClient(TestServer(api.application(), host="127.0.0.1"))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        headers = {"Authorization": "Bearer " + "a" * 43}
        response = await client.get("/v1/contacts", headers=headers)
        contacts = await response.json()
        for length, expected in ((4001, 400), (4000, 202)):
            with self.subTest(length=length):
                response = await client.post("/v1/messages", headers=headers, json={
                    "request_id": str(uuid.uuid4()), "account_id": contacts["account_id"],
                    "messages": [{"contact_id": contacts["contacts"][0]["id"],
                                  "text": "x" * length}],
                    "send_at": datetime.fromtimestamp(self.now + 900, UTC).isoformat(),
                    "late_policy": "hold",
                })
                self.assertEqual(response.status, expected, await response.text())
        rows, total = self.store.list(ACCOUNT)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["body"], "x" * 4000)

    async def test_coordinator_runs_assistant_and_native_with_one_shared_store(self):
        native, assistant = self.row(), self.row(origin="atajos")
        service = ScheduledMessageService(self.sent.append, clock=lambda: self.now)
        service.update(ACCOUNT, True, CONTACTS)
        api = LocalAssistantAPI("a" * 43, self.sent.append, store=self.store,
                                clock=lambda: self.now, scheduler=service)
        api.update(ACCOUNT, True, CONTACTS)
        api._loop = asyncio.get_running_loop()
        api._dispatch_ready.set()
        service.set_assistant(api)
        await service.tick(self.store)
        self.assertEqual({row["id"] for row in self.sent}, {native["id"], assistant["id"]})
        api.close()
        service.set_assistant(None)
        self.sent.clear()
        native, assistant = self.row(), self.row(origin="atajos")
        await service.tick(self.store)
        self.assertEqual([row["id"] for row in self.sent], [native["id"]])
        self.assertEqual(self.state(assistant), "pending")

    async def test_claim_then_callback_exception_is_uncertain_not_retried(self):
        row = self.row()
        send = Mock(side_effect=RuntimeError("fake"))
        await dispatch_due(self.store, self.snapshot, send, lambda: self.now,
                           lambda: False, ("native",))
        await self.tick()
        self.assertEqual(self.state(row), "uncertain")
        send.assert_called_once()
        self.assertFalse(self.sent)

    async def test_coordinator_does_not_dispatch_assistant_before_http_is_ready(self):
        row = self.row(origin="atajos")
        service = ScheduledMessageService(self.sent.append, clock=lambda: self.now)
        service.update(ACCOUNT, True, CONTACTS)
        api = LocalAssistantAPI("a" * 43, self.sent.append, store=self.store)
        api._loop = asyncio.get_running_loop()
        service.set_assistant(api)
        await service.tick(self.store)
        self.assertEqual(self.state(row), "pending")
        self.assertFalse(self.sent)

    async def test_closing_assistant_cannot_cancel_native_coordinator(self):
        service = ScheduledMessageService(self.sent.append, clock=lambda: self.now)
        service.update(ACCOUNT, True, CONTACTS)
        api = LocalAssistantAPI("a" * 43, self.sent.append, store=self.store)
        api._loop = asyncio.get_running_loop()
        api._dispatch_ready.set()

        async def closing():
            api._closed.set()
            raise asyncio.CancelledError()

        api.tick = closing
        service.set_assistant(api)
        first = self.row()
        await service.tick(self.store)
        second = self.row()
        await service.tick(self.store)
        self.assertEqual([row["id"] for row in self.sent], [first["id"], second["id"]])
        self.assertFalse(service._closed.is_set())

    async def test_native_capacity_and_paging_are_bounded_and_account_scoped(self):
        messages = [{"jid": JID, "name": "Ficticio", "text": f"Texto {i}"} for i in range(500)]
        self.store.create(str(uuid.uuid4()), ACCOUNT, messages, self.now + 3600,
                          "hold", origin="native")
        self.assertEqual(len(self.store.list(ACCOUNT)[0]), 50)
        self.assertEqual(self.store.list(ACCOUNT)[1], 500)
        self.assertEqual(self.store.list("other@example.test")[1], 0)
        with self.assertRaisesRegex(ValueError, "demasiados"):
            self.row()

    async def test_legacy_migration_keeps_rows_and_defaults_them_to_assistant(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as database, database:
            database.execute(
                "CREATE TABLE assistant_outbox (id TEXT PRIMARY KEY, request_id TEXT, "
                "recipient_index INTEGER, account TEXT, jid TEXT, name TEXT, body TEXT, "
                "due REAL, late_policy TEXT, state TEXT, detail TEXT)"
            )
            database.execute(
                "INSERT INTO assistant_outbox VALUES ('old','request',0,?,?,?, ?,?,'hold',"
                "'pending','')", (ACCOUNT, JID, "Ficticio", "Texto ficticio", self.now + 500),
            )
        migrated = ScheduledMessageStore(path)
        row = migrated.list(ACCOUNT)[0][0]
        self.assertEqual((row["origin"], row["body"], row["state"]),
                         ("atajos", "Texto ficticio", "pending"))
        self.assertFalse(migrated.due(self.now + 500, ACCOUNT, ("native",)))


class NativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1800000000.0
        self.sent = []
        self.service = ScheduledMessageService(
            self.sent.append, path=Path(self.temp.name) / "outbox.sqlite3", clock=lambda: self.now
        )
        self.service.update(ACCOUNT, True, CONTACTS)
        self.service.start()
        self.service.ready.result(timeout=5)

    def tearDown(self):
        self.service.close()
        self.service._thread.join(timeout=5)
        self.assertFalse(self.service._thread.is_alive())
        self.temp.cleanup()

    def request(self, **changes):
        data = dict(request_id=str(uuid.uuid4()), account=ACCOUNT, jid=JID,
                    text="Texto ficticio", due=self.now + 300, late_policy="hold")
        data.update(changes)
        return ScheduleRequest(**data)

    def test_persists_idempotently_and_retries_same_request_after_time_has_passed(self):
        request = self.request()
        first = self.service.create(request).result(timeout=5)
        self.now += 600
        second = self.service.create(request).result(timeout=5)
        self.assertEqual(first["id"], second["id"])
        changed = ScheduleRequest(**(vars(request) | {"text": "Otro texto"}))
        with self.assertRaises(ValueError):
            self.service.create(changed).result(timeout=5)

    def test_rejects_invalid_contact_account_policy_text_and_date(self):
        for data in ({"account": "other@example.test"}, {"jid": "unknown@example.test"},
                     {"text": "/stats"}, {"due": self.now}, {"late_policy": "other"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.create(self.request(**data)).result(timeout=5)
        self.assertEqual(self.service.ready.result().list(ACCOUNT)[1], 0)

    def test_native_ten_thousand_character_message_is_persisted_without_truncation(self):
        body = ("Línea ficticia ñ\n" * 700)[:10_000]
        self.assertEqual(len(body), 10_000)
        row = self.service.create(self.request(text=body)).result(timeout=5)
        self.assertEqual(row["body"], body)
        self.assertEqual(self.service.ready.result().list(ACCOUNT)[0][0]["body"], body)
        with self.assertRaisesRegex(ValueError, "10000"):
            self.service.create(self.request(text=body + "x")).result(timeout=5)
        self.assertEqual(self.service.ready.result().list(ACCOUNT)[1], 1)
        self.assertFalse(self.sent)

    def test_protocol_gate_rejects_disconnect_close_change_account_and_lateness(self):
        row = self.service.create(self.request(due=self.now + 60)).result(timeout=5)
        self.assertFalse(self.service.can_dispatch_on_ui(row))
        self.now += 60
        self.assertTrue(self.service.can_dispatch_on_ui(row))
        self.service.update(ACCOUNT, False, CONTACTS)
        self.assertFalse(self.service.can_dispatch_on_ui(row))
        self.service.update("other@example.test", True, CONTACTS)
        self.assertFalse(self.service.can_dispatch_on_ui(row))
        self.service.update(ACCOUNT, True, CONTACTS)
        self.now += 31
        self.assertFalse(self.service.can_dispatch_on_ui(row))
        self.service.close()
        self.assertFalse(self.service.can_dispatch_on_ui(row))

    def test_database_failure_is_reported_without_echo_or_network_send(self):
        with patch.object(
            self.service.ready.result(), "create", side_effect=sqlite3.OperationalError
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.service.create(self.request()).result(timeout=5)
        self.assertFalse(self.sent)


class ScheduledUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self):
        self.frame = wx.Frame(None)
        self.dialogs = []
        self.contacts = [{"jid": JID, "name": "Contacto ficticio", "is_group": False}]

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.deactivate()
            dialog.Destroy()
        self.frame.Destroy()
        self.app.Yield()

    def wait(self, condition):
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            self.app.Yield()
            if condition():
                return
            time.sleep(0.01)
        self.fail("GUI callback did not complete")

    def form(self, save=None):
        dialog = ScheduleMessageDialog(self.frame, ACCOUNT, self.contacts, save or Mock())
        self.dialogs.append(dialog)
        return dialog

    def row(self, identifier="row", state="pending"):
        return dict(id=identifier, name="Contacto ficticio", account=ACCOUNT,
                    due=datetime.now(UTC).timestamp() + 3600, state=state, origin="native",
                    body="Texto completo ficticio\nSegunda línea", late_policy="hold", detail="")

    def manager(self, rows, load=None, cancel=None):
        future = Future()
        future.set_result((rows, len(rows)))
        dialog = ScheduledMessagesDialog(self.frame, ACCOUNT, load or Mock(return_value=future),
                                         cancel or Mock())
        self.dialogs.append(dialog)
        self.wait(lambda: bool(dialog._rows) or not dialog._busy and dialog._generation > 0)
        return dialog

    def test_form_defaults_safe_policy_and_preserves_contact_focus_on_filtering(self):
        dialog = self.form()
        dialog.Show()
        dialog.contacts.SetFocus()
        dialog.contacts.SetSelection(0)
        dialog._filter(None)
        self.assertEqual(dialog.policy.GetSelection(), 0)
        self.assertEqual(dialog.contacts.GetSelection(), 0)
        self.assertIs(wx.Window.FindFocus(), dialog.contacts)
        dialog.message.SetValue("Texto ficticio")
        request = dialog._request()
        self.assertEqual(request.jid, JID)
        self.assertGreater(request.due, time.time())
        self.assertEqual(request.late_policy, "hold")

    def test_form_labels_precede_their_controls_in_native_order(self):
        dialog = self.form()
        children = list(dialog.GetChildren())
        for field in ("search", "contacts", "message", "date", "hour", "policy"):
            with self.subTest(field=field):
                control = getattr(dialog, field)
                label = getattr(dialog, field + "_label")
                self.assertEqual(children.index(label) + 1, children.index(control))
        self.assertTrue(dialog.message.HasFlag(wx.TE_MULTILINE))

    def test_date_is_prefilled_with_short_local_date_and_midnight_rollover_is_safe(self):
        cases = ((datetime(2026, 10, 6, 12), "06/10/26", "12:15"),
                 (datetime(2026, 10, 6, 23, 55), "07/10/26", "00:10"))
        for now, expected_date, expected_hour in cases:
            with self.subTest(now=now), patch(
                "cliente_xmpp.ui.scheduled_message_dialog.datetime", wraps=datetime
            ) as dates:
                dates.now.return_value = now
                dialog = self.form()
                self.assertEqual(dialog.date.GetValue(), expected_date)
                self.assertEqual(dialog.hour.GetValue(), expected_hour)
                self.assertIn(expected_date, dialog.date_label.GetLabel())
                self.assertNotIn("AAAA", dialog.date_label.GetLabel())

    def test_form_accepts_ten_thousand_characters_in_a_short_date_request(self):
        dialog = self.form()
        dialog.message.SetValue("x" * 10_000)
        self.assertEqual(dialog._request().text, "x" * 10_000)
        dialog.message.SetValue("x" * 10_001)
        with self.assertRaisesRegex(ValueError, "10000"):
            dialog._request()

    @unittest.skipUnless(sys.platform == "win32", "Windows MSAA regression")
    def test_message_date_and_hour_expose_native_accessible_labels(self):
        dialog = self.form()
        date_example = dialog.date.GetValue()
        dialog.message.SetValue("Contenido ficticio\nSegunda línea")
        dialog.date.SetValue("15/01/27")
        dialog.hour.SetValue("12:30")
        dialog.Move((-10000, -10000))
        dialog.Show()
        self.app.Yield()
        for field, parts in (("message", ("Mensaje", "multilínea", "10000")),
                             ("date", ("Fecha", "ejemplo:", date_example)),
                             ("hour", ("Hora", "HH:MM", "24 horas"))):
            with self.subTest(field=field):
                name = windows_accessible_name(getattr(dialog, field))
                for part in parts:
                    self.assertIn(part, name)
                self.assertNotEqual(name, getattr(dialog, field).GetValue())

    def test_escape_is_local_in_form_manager_and_review_modals(self):
        parent_hook = Mock()
        self.frame.Bind(wx.EVT_CHAR_HOOK, parent_hook)
        form = self.form()
        manager = self.manager([])
        form.message.SetValue("Texto ficticio")
        review = ScheduleReviewDialog(form, "Contacto ficticio", form._request())
        try:
            for dialog in (form, manager, review):
                with self.subTest(dialog=type(dialog).__name__):
                    dialog.Move((-10000, -10000))

                    def escape(dialog=dialog):
                        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
                        event.KeyCode = wx.WXK_ESCAPE
                        dialog.ProcessWindowEvent(event)

                    timer = wx.CallLater(50, escape)
                    watchdog = wx.CallLater(1000, dialog.EndModal, wx.ID_CANCEL)
                    try:
                        self.assertEqual(dialog.ShowModal(), wx.ID_CANCEL)
                    finally:
                        timer.Stop()
                        watchdog.Stop()
                    parent_hook.assert_not_called()
        finally:
            review.Destroy()

    def test_escape_while_saving_is_consumed_without_canceling_persistence(self):
        dialog = self.form()
        dialog._busy = True
        event = SimpleNamespace(GetKeyCode=lambda: wx.WXK_ESCAPE, AltDown=lambda: False,
                                ControlDown=lambda: False, ShiftDown=lambda: False, Skip=Mock())
        with patch.object(dialog, "EndModal") as end:
            dialog._shortcut(event)
        end.assert_not_called()
        event.Skip.assert_not_called()
        self.assertTrue(dialog._active)
        self.assertTrue(dialog._busy)

    def test_closing_scheduling_modals_restores_only_visible_origin_focus(self):
        window = self.frame
        window._closing = False
        window.current_jid = ACCOUNT
        window._sync_scheduled_service = Mock()
        window._restore_scheduled_focus = lambda previous: (
            ScheduledMessagesMixin._restore_scheduled_focus(window, previous)
        )
        window._scheduled_service = Mock()
        window._scheduled_service.snapshot.return_value = (ACCOUNT, True, {JID: self.contacts[0]})
        loaded = Future()
        loaded.set_result(([], 0))
        window._scheduled_service.submit.return_value = loaded
        chats, conversation = wx.Panel(window), wx.Panel(window)
        chats.list_box = wx.ListBox(chats, choices=["Contacto ficticio", "Otro ficticio"])
        chats.search = wx.TextCtrl(chats)
        chats.focus = chats.list_box.SetFocus
        chats.list_box.SetSelection(1)
        conversation.current_chat = None
        conversation.compose = wx.TextCtrl(conversation, value="Borrador ficticio")
        conversation.messages = wx.ListBox(conversation, choices=["Mensaje ficticio"])
        window.chat_list, window.conversation = chats, conversation
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(chats)
        box.Add(conversation)
        window.SetSizer(box)
        window.Move((-10000, -10000))
        window.Show()
        timers = []

        def auto_escape(factory):
            def create(*args, **kwargs):
                dialog = factory(*args, **kwargs)
                dialog.Move((-10000, -10000))

                def escape():
                    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
                    event.KeyCode = wx.WXK_ESCAPE
                    dialog.ProcessWindowEvent(event)

                timers.append(wx.CallLater(50, escape))
                return dialog
            return create

        try:
            with (
                patch("cliente_xmpp.ui.scheduled_messages.ScheduleMessageDialog",
                      side_effect=auto_escape(ScheduleMessageDialog)),
                patch("cliente_xmpp.ui.scheduled_messages.ScheduledMessagesDialog",
                      side_effect=auto_escape(ScheduledMessagesDialog)),
            ):
                for handler in (ScheduledMessagesMixin._on_schedule_message,
                                ScheduledMessagesMixin._on_scheduled_messages):
                    for origin in (chats.list_box, chats.search,
                                   conversation.compose, conversation.messages):
                        with self.subTest(handler=handler.__name__, origin=origin):
                            in_chats = origin.GetParent() is chats
                            chats.Show(in_chats)
                            conversation.Show(not in_chats)
                            window.Layout()
                            origin.SetFocus()
                            self.app.Yield()
                            self.assertIs(wx.Window.FindFocus(), origin)
                            handler(window, None)
                            self.app.Yield()
                            self.assertIs(wx.Window.FindFocus(), origin)
                            self.assertEqual(chats.IsShown(), in_chats)
                            self.assertEqual(conversation.IsShown(), not in_chats)
                            self.assertEqual(chats.list_box.GetSelection(), 1)
                            self.assertEqual(conversation.compose.GetValue(), "Borrador ficticio")
            window._scheduled_service.create.assert_not_called()
            conversation.Hide()
            chats.Show()
            with patch.object(conversation.compose, "SetFocus") as hidden_focus:
                window._restore_scheduled_focus(conversation.compose)
                hidden_focus.assert_not_called()
            self.assertIs(wx.Window.FindFocus(), chats.list_box)
            window._closing = True
            with patch.object(chats.list_box, "SetFocus") as closing_focus:
                window._restore_scheduled_focus(chats.list_box)
                closing_focus.assert_not_called()
        finally:
            for timer in timers:
                timer.Stop()

    def test_duplicate_names_are_disambiguated_and_filtering_no_match_rejects_save(self):
        self.contacts += [{"jid": "other@example.test", "name": "Contacto ficticio"}]
        dialog = self.form()
        self.assertIn(JID, dialog.contacts.GetString(0))
        dialog.search.SetValue("no matches")
        self.app.Yield()
        with self.assertRaisesRegex(ValueError, "Selecciona"):
            dialog._request()

    def test_manager_selection_refresh_keeps_identity_and_full_copyable_text(self):
        dialog = self.manager([self.row("a"), self.row("b")])
        dialog.items.Select(0, False)
        dialog.items.Select(1)
        dialog._selected(None)
        self.assertIn("Segunda línea", dialog.details.GetValue())
        update = Future()
        update.set_result(([self.row("b"), self.row("a")], 2))
        dialog._loaded(dialog._generation, update)
        self.assertEqual(dialog._selection()["id"], "b")
        self.assertTrue(dialog.cancel_message.IsEnabled())
        dialog._rows[0]["state"] = "dispatching"
        dialog._selected(None)
        self.assertFalse(dialog.cancel_message.IsEnabled())

    def test_manager_keyboard_alt_m_and_callbacks_after_destroy_are_safe(self):
        dialog = self.manager([self.row()])
        event = SimpleNamespace(AltDown=lambda: True, ControlDown=lambda: False,
                                GetKeyCode=lambda: ord("M"), ShiftDown=lambda: False, Skip=Mock())
        with patch.object(dialog.items, "SetFocus") as focus:
            dialog._shortcut(event)
        focus.assert_called_once()
        dialog.deactivate()
        dialog._loaded(dialog._generation, Mock())
        dialog._canceled(Mock())
        dialog._load.assert_called_once()
        form = self.form()
        form.deactivate()
        form._saved(Mock())

    def test_cancel_no_is_read_only_and_yes_calls_cancel_once(self):
        result = Future()
        result.set_result(True)
        cancel = Mock(return_value=result)
        dialog = self.manager([self.row()], cancel=cancel)
        with patch("cliente_xmpp.ui.scheduled_message_dialog.wx.MessageBox", return_value=wx.NO):
            dialog._cancel_selected(None)
        cancel.assert_not_called()
        with patch("cliente_xmpp.ui.scheduled_message_dialog.wx.MessageBox", return_value=wx.YES):
            dialog._cancel_selected(None)
            dialog._cancel_selected(None)
        cancel.assert_called_once_with("row")
        self.wait(lambda: not dialog._busy)

    def test_filter_focus_survives_slow_refresh_and_latest_filter_wins(self):
        slow, latest = Future(), Future()
        loader = Mock(side_effect=[slow, latest])
        dialog = ScheduledMessagesDialog(self.frame, ACCOUNT, loader, Mock())
        self.dialogs.append(dialog)
        dialog.Show()
        self.wait(lambda: dialog._busy)
        dialog.filter.SetFocus()
        dialog.filter.SetSelection(3)
        dialog._filtered(None)
        self.assertTrue(dialog.filter.IsEnabled())
        slow.set_result(([self.row("old")], 1))
        self.wait(lambda: loader.call_count == 2)
        latest.set_result(([self.row("held", "held")], 1))
        self.wait(lambda: not dialog._busy)
        self.assertEqual(dialog._rows[0]["id"], "held")
        self.assertEqual(loader.call_args.args, ("held", 0))
        self.assertIs(wx.Window.FindFocus(), dialog.filter)

    def test_native_alt_m_stays_local_to_form_and_does_not_mark_chats_read(self):
        dialog = self.form()
        event = SimpleNamespace(AltDown=lambda: True, ControlDown=lambda: False,
                                ShiftDown=lambda: False, GetKeyCode=lambda: ord("M"), Skip=Mock())
        with patch.object(dialog.message, "SetFocus") as focus:
            dialog._shortcut(event)
        focus.assert_called_once()
        event.Skip.assert_not_called()

    def test_total_cleanup_aborts_until_outbox_workers_have_stopped(self):
        worker = []
        service = SimpleNamespace(wait_closed=Mock(return_value=False))
        window = SimpleNamespace(
            _storage_reset_in_progress=False, _atajos_api=None, _scheduled_service=service,
            _close_atajos_api=Mock(), _close_scheduled_messages=Mock(),
            windows_notification_service=Mock(), conversation=Mock(), audio_recorder=Mock(),
            xmpp=Mock(), current_jid=ACCOUNT, connection_settings=SimpleNamespace(jid=ACCOUNT),
            storage_manager=Mock(), credential_store=Mock(), _finish_total_storage_deletion=Mock(),
            _submit_storage_manager_worker=lambda operation, callback: worker.append(operation),
        )
        callback = Mock()
        MainWindow._delete_all_storage_async(window, callback)
        window._close_atajos_api.assert_called_once()
        window._close_scheduled_messages.assert_called_once()
        with patch("cliente_xmpp.ui.main_window.wx.CallAfter") as report:
            worker[0]()
        service.wait_closed.assert_called_once()
        window.storage_manager.delete_all_data.assert_not_called()
        report.assert_called_once()

    def test_native_dispatch_uses_guarded_normal_sender_without_touching_draft(self):
        row = self.row()
        row.update(jid=JID, is_group=False, rule_id="", body="x" * 10_000)
        service = Mock()
        window = SimpleNamespace(
            _scheduled_service=service, _atajos_api=None, current_jid=ACCOUNT,
            _closing=False, whatsapp_verified=True, roster_jids={JID},
            searchable_chats_by_jid={JID: SimpleNamespace(is_group=False)},
            _add_pending_outgoing_message=Mock(), xmpp=Mock(), _defer_atajos_message=Mock(),
            draft="Un borrador ajeno",
        )
        AtajosIntegrationMixin._send_atajos_message(window, row)
        self.assertEqual(window.draft, "Un borrador ajeno")
        args = window.xmpp.send_message.call_args
        self.assertEqual(args.args[1], "x" * 10_000)
        self.assertTrue(args.kwargs["retry_with_authorization"])
        self.assertTrue(callable(args.kwargs["authorization"]))
        self.assertEqual(args.kwargs["expected_account"], ACCOUNT)

    def test_modal_save_confirms_once_and_closes_only_after_persistence(self):
        pending = Future()
        save = Mock(return_value=pending)
        dialog = self.form(save)
        dialog.message.SetValue("Texto ficticio")
        dialog.Move((-10000, -10000))
        review = SimpleNamespace(ShowModal=Mock(return_value=wx.ID_OK), Destroy=Mock())

        def confirm():
            dialog._review(None)
            dialog._review(None)
            self.assertTrue(dialog._busy)
            self.assertFalse(dialog.cancel.IsEnabled())
            pending.set_result(self.row())

        with patch("cliente_xmpp.ui.scheduled_message_dialog.ScheduleReviewDialog",
                   return_value=review):
            timer = wx.CallLater(50, confirm)
            try:
                self.assertEqual(dialog.ShowModal(), wx.ID_OK)
            finally:
                timer.Stop()
        save.assert_called_once()
        review.ShowModal.assert_called_once()
        self.assertIsNotNone(dialog.saved)

    def test_failed_save_preserves_fields_identity_and_allows_retry(self):
        dialog = self.form()
        dialog.message.SetValue("Mi texto ficticio")
        identifier = dialog._request_id
        dialog._busy = True
        failed = Future()
        failed.set_exception(sqlite3.OperationalError("fake"))
        with patch("cliente_xmpp.ui.scheduled_message_dialog.wx.MessageBox"):
            dialog._saved(failed)
        self.assertEqual(dialog.message.GetValue(), "Mi texto ficticio")
        self.assertEqual(dialog._request_id, identifier)
        self.assertFalse(dialog._busy)
        self.assertTrue(dialog.review.IsEnabled())

    def test_real_modal_form_focuses_contact_list_and_escape_cancels(self):
        dialog = self.form()
        dialog.Move((-10000, -10000))
        focused = []

        def inspect():
            focused.append(wx.Window.FindFocus())
            dialog._close(None)

        timer = wx.CallLater(100, inspect)
        try:
            self.assertEqual(dialog.ShowModal(), wx.ID_CANCEL)
        finally:
            timer.Stop()
        self.assertEqual(focused, [dialog.contacts])


class NativeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_retry_uses_same_identity_and_rechecks_permission(self):
        allowed = True
        msg = MagicMock()
        retry = Mock()
        service = XmppService(lambda _event: None)
        service._loop = asyncio.get_running_loop()
        service._client = SimpleNamespace(
            boundjid=SimpleNamespace(bare=ACCOUNT), is_connected=lambda: True,
            make_message=Mock(return_value=msg), track_transient_message_retry=retry,
        )
        service.send_message(JID, "Ficticio", message_id="cliente-xmpp-api-test",
                             expected_account=ACCOUNT, on_deferred=Mock(),
                             authorization=lambda: allowed, retry_with_authorization=True)
        await asyncio.sleep(0)
        self.assertEqual(retry.call_args.args[1], "cliente-xmpp-api-test")
        callback = retry.call_args.args[2]
        callback()
        self.assertEqual(msg.send.call_count, 2)
        allowed = False
        with self.assertRaises(ValueError):
            callback()
        self.assertEqual(msg.send.call_count, 2)
