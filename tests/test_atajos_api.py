from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.integrations.atajos_credentials import SERVICE, USERNAME, integration_token
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore
from cliente_xmpp.ui.atajos_integration import AtajosIntegrationMixin
from cliente_xmpp.xmpp.client import XmppService

TOKEN = "a" * 43
ACCOUNT = "owner@example.test"
CONTACTS = [("ana@example.test", "Ana ficticia"), ("otro@example.test", "Ángel ficticio")]


class AtajosAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "outbox.sqlite3"
        self.store = ScheduledMessageStore(self.path)
        self.now = 1800000000.0
        self.sent = []
        self.api = LocalAssistantAPI(
            TOKEN, self.sent.append, store=self.store, clock=lambda: self.now
        )
        self.api.update(ACCOUNT, True, CONTACTS)
        self.client = TestClient(TestServer(self.api.application(), host="127.0.0.1"))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + TOKEN}

    async def body(self, *, seconds: int = 0, policy: str = "send-when-connected") -> dict:
        response = await self.client.get("/v1/contacts", headers=self.headers)
        data = await response.json()
        return {
            "request_id": str(uuid.uuid4()),
            "account_id": data["account_id"],
            "messages": [
                {"contact_id": data["contacts"][0]["id"], "text": "Texto ficticio ñ\nDos"}
            ],
            "send_at": datetime.fromtimestamp(self.now + seconds, UTC).isoformat(),
            "late_policy": policy,
        }

    async def create(self, body: dict | None = None) -> dict:
        body = body or await self.body()
        response = await self.client.post("/v1/messages", json=body, headers=self.headers)
        self.assertEqual(response.status, 202, await response.text())
        return await response.json()

    def state(self, account: str = ACCOUNT) -> str:
        rows, _ = self.store.list(account)
        return rows[0]["state"]

    async def test_auth_rejects_missing_wrong_origin_foreign_host(self) -> None:
        for headers, status in [
            ({}, 401),
            ({"Authorization": "Bearer wrong"}, 401),
            ({**self.headers, "Origin": "http://127.0.0.1"}, 403),
            ({**self.headers, "Host": "foreign.example.test"}, 403),
        ]:
            response = await self.client.get("/v1/status", headers=headers)
            self.assertEqual(response.status, status)
        response = await self.client.get("/v1/status", headers=self.headers)
        self.assertEqual((await response.json())["late_default"], "send-when-connected")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_contacts_filter_accents_hide_jids_and_are_stable_account_specific(self) -> None:
        response = await self.client.get("/v1/contacts?query=angel", headers=self.headers)
        first = await response.json()
        self.assertEqual(len(first["contacts"]), 1)
        self.assertNotIn("@", json.dumps(first))
        self.api.update("another@example.test", True, CONTACTS)
        response = await self.client.get("/v1/contacts?query=angel", headers=self.headers)
        second = await response.json()
        self.assertNotEqual(first["account_id"], second["account_id"])
        self.assertNotEqual(first["contacts"][0]["id"], second["contacts"][0]["id"])

    async def test_program_does_not_send_before_due_and_sends_once(self) -> None:
        result = await self.create(await self.body(seconds=900))
        self.assertEqual(result["messages"][0]["state"], "pending")
        await self.api.tick()
        self.assertFalse(self.sent)
        self.now += 900
        await self.api.tick()
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["body"], "Texto ficticio ñ\nDos")
        self.assertEqual(self.sent[0]["account"], ACCOUNT)

    async def test_reconnection_and_restart_wait_original_account(self) -> None:
        await self.create()
        self.api.update(ACCOUNT, False, CONTACTS)
        self.now += 3600
        await self.api.tick()
        self.assertEqual(self.state(), "pending")
        self.store = ScheduledMessageStore(self.path)
        self.api = LocalAssistantAPI(
            TOKEN, self.sent.append, store=self.store, clock=lambda: self.now
        )
        self.api.update("another@example.test", True, CONTACTS)
        await self.api.tick()
        self.assertFalse(self.sent)
        self.api.update(ACCOUNT, True, CONTACTS)
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_other_account_backlog_does_not_starve_active_queue(self) -> None:
        for _ in range(25):
            self.store.create(
                str(uuid.uuid4()),
                "another@example.test",
                [{"jid": CONTACTS[0][0], "name": "Ficticio", "text": "Prueba"}],
                self.now - 3600,
                "send-when-connected",
            )
        await self.create()
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_deleted_contact_is_held_not_sent(self) -> None:
        await self.create()
        self.api.update(ACCOUNT, True, [])
        await self.api.tick()
        self.assertFalse(self.sent)
        self.assertEqual(self.state(), "held")

    async def test_idempotent_concurrent_posts_and_conflicting_payload_rejected(self) -> None:
        body = await self.body()
        first, second = await asyncio.gather(self.create(body), self.create(body))
        self.assertEqual(first["messages"][0]["id"], second["messages"][0]["id"])
        body["messages"][0]["text"] = "Distinto"
        response = await self.client.post("/v1/messages", json=body, headers=self.headers)
        self.assertEqual(response.status, 400)
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_cancel_pending_prevents_dispatch_and_cannot_cancel_submitted(self) -> None:
        row = (await self.create())["messages"][0]
        response = await self.client.post(
            f"/v1/messages/{row['id']}/cancel", json={}, headers=self.headers
        )
        self.assertEqual(response.status, 200)
        await self.api.tick()
        self.assertFalse(self.sent)
        row = (await self.create())["messages"][0]
        await self.api.tick()
        response = await self.client.post(
            f"/v1/messages/{row['id']}/cancel", json={}, headers=self.headers
        )
        self.assertEqual(response.status, 409)

    async def test_delivery_monotonic_and_lost_ack_restart_never_retries(self) -> None:
        row = (await self.create())["messages"][0]
        await self.api.tick()
        self.store = ScheduledMessageStore(self.path)
        self.assertEqual(self.state(), "uncertain")
        message_id = "cliente-xmpp-api-" + row["id"]
        for state, expected in [
            ("sent", "submitted"),
            ("read", "read"),
            ("delivered", "read"),
            ("failed", "read"),
        ]:
            self.store.finish_delivery(message_id, state)
            self.assertEqual(self.state(), expected)
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_concurrent_delivery_states_are_monotonic_and_receipt_resolves_failure(self):
        row = (await self.create())["messages"][0]
        await self.api.tick()
        message_id = "cliente-xmpp-api-" + row["id"]
        self.store.finish_delivery(message_id, "failed")
        self.assertEqual(self.state(), "failed")
        await asyncio.gather(
            *(
                asyncio.to_thread(self.store.finish_delivery, message_id, state)
                for state in ["read", "sent", "delivered", "failed"]
            )
        )
        self.assertEqual(self.state(), "read")

    async def test_invalid_messages_extra_fields_duplicates_commands_dates(self) -> None:
        original = await self.body()
        for change in [
            {"token": "should-not-exist"},
            {"account_id": str(uuid.uuid4())},
            {"messages": []},
            {"messages": original["messages"] * 2},
            {"messages": [{"contact_id": str(uuid.uuid4()), "text": "Prueba"}]},
            {"messages": [{**original["messages"][0], "text": "/stats"}]},
            {"messages": [{**original["messages"][0], "text": "x" * 4001}]},
            {"send_at": "2026-10-03T12:00:00"},
            {"late_policy": "unexpected"},
        ]:
            response = await self.client.post(
                "/v1/messages", json={**original, **change}, headers=self.headers
            )
            self.assertIn(response.status, {400, 409})
        duplicated = json.dumps(original)[:-1] + ',"request_id":"' + str(uuid.uuid4()) + '"}'
        response = await self.client.post(
            "/v1/messages",
            data=duplicated,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status, 400)
        self.assertFalse(self.store.list(ACCOUNT)[0])

    async def test_pagination_and_account_scoped_listing(self) -> None:
        self.api.update(
            ACCOUNT, True, [(f"contact{i}@example.test", f"Ficticio {i:03}") for i in range(61)]
        )
        response = await self.client.get("/v1/contacts", headers=self.headers)
        data = await response.json()
        self.assertEqual((len(data["contacts"]), data["total"], data["next_offset"]), (50, 61, 50))
        await self.create()
        self.api.update("another@example.test", True, CONTACTS)
        response = await self.client.get("/v1/messages", headers=self.headers)
        self.assertEqual((await response.json())["total"], 0)

    async def test_send_exception_uncertain_without_repeat(self) -> None:
        await self.create()
        self.api._send = Mock(side_effect=RuntimeError("fake network error"))
        await self.api.tick()
        await self.api.tick()
        self.assertEqual(self.state(), "uncertain")
        self.api._send.assert_called_once()

    async def test_protocol_defers_account_switch_and_disconnect_before_send(self) -> None:
        for actual_account, connected in [("another@example.test", True), (ACCOUNT, False)]:
            deferred = Mock()
            make_message = Mock()
            service = XmppService(lambda _event: None)
            service._loop = asyncio.get_running_loop()
            service._client = SimpleNamespace(
                boundjid=SimpleNamespace(bare=actual_account),
                is_connected=lambda connected=connected: connected,
                make_message=make_message,
            )
            service.send_message(
                CONTACTS[0][0],
                "Ficticio",
                message_id="cliente-xmpp-api-test",
                expected_account=ACCOUNT,
                on_deferred=deferred,
            )
            await asyncio.sleep(0)
            deferred.assert_called_once()
            make_message.assert_not_called()

    async def test_settings_flag_preserves_existing_profiles(self) -> None:
        path = Path(self.temp.name) / "settings.json"
        path.write_text('{"existing":"keep","atajos_api_enabled":"true"}', encoding="utf-8")
        store = SettingsStore(path)
        self.assertFalse(store.load_atajos_api_enabled())
        store.save_atajos_api_enabled(True)
        self.assertTrue(store.load_atajos_api_enabled())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["existing"], "keep")

    async def test_credential_missing_or_invalid_is_repaired_without_touching_other_settings(self):
        for saved in [None, "invalid", TOKEN]:
            with patch("keyring.backends.Windows.WinVaultKeyring") as backend:
                vault = backend.return_value
                vault.get_password.return_value = saved
                token = integration_token()
                self.assertEqual(len(token), 43)
                vault.get_password.assert_called_once_with(SERVICE, USERNAME)
                if saved == TOKEN:
                    self.assertEqual(token, TOKEN)
                    vault.set_password.assert_not_called()
                else:
                    vault.set_password.assert_called_once_with(SERVICE, USERNAME, token)

    async def test_ui_send_uses_normal_message_identity_without_editing_composer(self) -> None:
        row = (await self.create())["messages"][0]
        await self.api.tick()
        stored = self.sent[0]
        api = Mock()
        optimistic = Mock()
        xmpp = Mock()
        window = SimpleNamespace(
            _atajos_api=api,
            current_jid=ACCOUNT + "/fixture",
            _closing=False,
            whatsapp_verified=True,
            roster_jids={stored["jid"]},
            searchable_chats_by_jid={stored["jid"]: SimpleNamespace(is_group=False)},
            _add_pending_outgoing_message=optimistic,
            xmpp=xmpp,
            compositor_text="Un borrador ajeno",
            _defer_atajos_message=Mock(),
        )
        AtajosIntegrationMixin._send_atajos_message(window, stored)
        message = optimistic.call_args.args[0]
        self.assertEqual(message.message_id, "cliente-xmpp-api-" + row["id"])
        self.assertEqual(message.body, stored["body"])
        self.assertEqual(window.compositor_text, "Un borrador ajeno")
        self.assertEqual(xmpp.send_message.call_args.kwargs["expected_account"], ACCOUNT)
        self.assertTrue(callable(xmpp.send_message.call_args.kwargs["on_deferred"]))

    async def test_ui_disconnect_race_requeues_and_does_not_send(self) -> None:
        await self.create()
        await self.api.tick()
        api = Mock()
        window = SimpleNamespace(
            _atajos_api=api,
            current_jid=ACCOUNT,
            _closing=False,
            whatsapp_verified=False,
            searchable_chats_by_jid={},
            xmpp=Mock(),
        )
        AtajosIntegrationMixin._send_atajos_message(window, self.sent[0])
        window.xmpp.send_message.assert_not_called()
        self.assertTrue(api.rejected.call_args.kwargs["wait"])

    async def test_closed_api_stops_dispatch_and_hold_policy_does_not_send_late(self) -> None:
        await self.create(await self.body(policy="hold"))
        self.now += 3600
        await self.api.tick()
        self.assertEqual(self.state(), "held")
        self.assertFalse(self.sent)
        await self.create()
        self.api.close()
        await self.api.tick()
        self.assertFalse(self.sent)


if __name__ == "__main__":
    unittest.main()
