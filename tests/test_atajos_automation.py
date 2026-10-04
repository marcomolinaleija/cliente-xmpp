from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from aiohttp.test_utils import TestClient, TestServer

from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.assistant_automation import AssistantAutomationStore
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore
from cliente_xmpp.ui.atajos_integration import AtajosIntegrationMixin
from cliente_xmpp.xmpp.client import XmppService

ACCOUNT = "owner@example.test"
PEOPLE = [
    ("ana@example.test", "Ana ficticia", False),
    ("group@rooms.example.test", "Grupo ficticio", True),
]


class AutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ScheduledMessageStore(Path(self.temp.name) / "outbox.sqlite3")
        self.now = 1800000000.0
        self.sent = []
        self.api = LocalAssistantAPI(
            "a" * 43, self.sent.append, store=self.store, clock=lambda: self.now
        )
        self.api.update(ACCOUNT, True, PEOPLE)
        self.client = TestClient(TestServer(self.api.application(), host="127.0.0.1"))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + "a" * 43}
        status = await self.get("/v1/status")
        self.account = status["account_id"]
        self.epoch = status["journal_epoch"]
        catalogue = await self.get("/v1/contacts?kind=all")
        self.chats = catalogue["contacts"]
        self.rule = str(uuid.uuid4())

    async def get(self, path):
        response = await self.client.get(path, headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def post(self, path, body, status=200):
        response = await self.client.post(path, headers=self.headers, json=body)
        self.assertEqual(response.status, status, await response.text())
        return await response.json() if response.content_type == "application/json" else {}

    def fields(self):
        return {"account_id": self.account, "epoch": self.epoch}

    def date(self, seconds=100):
        return datetime.fromtimestamp(self.now + seconds, UTC).isoformat()

    async def lease(self, scope="all", excluded=None):
        return await self.post(
            "/v1/automation/lease",
            {
                **self.fields(),
                "rule_id": self.rule,
                "scope": scope,
                "chat_ids": [],
                "excluded_ids": excluded or [],
                "expires_at": self.date(),
            },
        )

    def incoming(self, identity="m1", group=False, kind="incoming"):
        message = Message(
            chat_jid=PEOPLE[int(group)][0],
            sender_jid="sender@example.test",
            sender_name="Participante ficticio",
            body="Texto de prueba",
            message_id=identity,
            chat_is_group=group,
            sent_at=datetime.fromtimestamp(self.now, UTC),
        )
        self.api.observe_message(ACCOUNT, message, kind)

    async def events(self, after=0):
        return await self.post("/v1/automation/events", {**self.fields(), "after_seq": after})

    async def reply_body(self, group=False):
        events = (await self.events())["events"]
        event = next(item for item in reversed(events) if bool(item["is_group"]) == group)
        return {
            **self.fields(),
            "rule_id": self.rule,
            "request_id": str(uuid.uuid4()),
            "chat_id": event["chat_id"],
            "trigger_seq": event["seq"],
            "text": "Respuesta ficticia",
            "expires_at": self.date(),
        }

    async def test_activation_skips_old_messages_and_ids_survive_duplicates(self):
        self.incoming()
        baseline = (await self.lease())["baseline"]
        body = await self.reply_body()
        await self.post("/v1/automation/reply", body, 409)
        self.incoming()
        page = await self.events()
        self.assertEqual(len(page["events"]), 1)
        self.assertEqual(page["next_after"], baseline)
        self.assertNotIn("@", str(page))

    async def test_acceptance_idempotent_and_trigger_unique_across_rules(self):
        await self.lease()
        self.incoming()
        body = await self.reply_body()
        one = await self.post("/v1/automation/reply", body, 202)
        two = await self.post("/v1/automation/reply", body, 202)
        self.assertEqual(one["messages"][0]["id"], two["messages"][0]["id"])
        body["request_id"] = str(uuid.uuid4())
        await self.post("/v1/automation/reply", body, 409)
        await self.api.tick()
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)

    async def test_pause_revokes_queued_ui_permission_and_cancels_pending(self):
        await self.lease()
        self.incoming()
        await self.post("/v1/automation/reply", await self.reply_body(), 202)
        await self.api.tick()
        row = self.sent[0]
        self.assertTrue(self.api.can_dispatch_on_ui(row))
        await self.post("/v1/automation/revoke", {"account_id": self.account, "rule_id": self.rule})
        self.assertFalse(self.api.can_dispatch_on_ui(row))

    async def test_new_message_or_manual_reply_invalidates_before_dispatch(self):
        for kind in ("incoming", "outgoing", "changed"):
            await self.lease()
            self.incoming(str(uuid.uuid4()))
            result = await self.post("/v1/automation/reply", await self.reply_body(), 202)
            self.incoming(str(uuid.uuid4()), kind=kind)
            await self.api.tick()
            rows, _ = self.store.list(ACCOUNT)
            self.assertEqual(
                next(row["state"] for row in rows if row["id"] == result["messages"][0]["id"]),
                "canceled",
            )
        self.assertFalse(self.sent)

    async def test_changes_between_db_validation_and_ui_dispatch_are_blocked(self):
        await self.lease()
        self.incoming()
        await self.post("/v1/automation/reply", await self.reply_body(), 202)
        await self.api.tick()
        self.incoming("new-message")  # Queued in memory, not yet written to SQLite.
        self.assertFalse(self.api.can_dispatch_on_ui(self.sent[0]))

    async def test_expiry_blocks_and_renew_requires_reactivation(self):
        await self.lease()
        self.incoming()
        await self.post("/v1/automation/reply", await self.reply_body(), 202)
        self.now += 101
        await self.api.tick()
        self.assertFalse(self.sent)
        await self.post(
            "/v1/automation/lease",
            {
                **self.fields(),
                "rule_id": self.rule,
                "scope": "all",
                "chat_ids": [],
                "excluded_ids": [],
                "expires_at": self.date(),
            },
            400,
        )

    async def test_groups_supported_and_contact_only_scope_rejects_group(self):
        await self.lease("contacts")
        self.incoming(group=True)
        await self.post("/v1/automation/reply", await self.reply_body(group=True), 409)
        await self.post("/v1/automation/revoke", {"account_id": self.account, "rule_id": self.rule})
        await self.lease("groups")
        self.incoming("group2", group=True)
        result = await self.post("/v1/automation/reply", await self.reply_body(group=True), 202)
        self.assertTrue(result["messages"][0]["is_group"])
        await self.api.tick()
        self.assertTrue(self.sent[0]["is_group"])

    async def test_excluded_chat_is_never_authorized(self):
        await self.lease(excluded=[self.chats[0]["id"]])
        self.incoming()
        await self.post("/v1/automation/reply", await self.reply_body(), 409)

    async def test_account_epoch_mismatch_and_disconnection_reject(self):
        await self.lease()
        self.incoming()
        body = await self.reply_body()
        self.api.update(ACCOUNT, False, PEOPLE)
        await self.post("/v1/automation/reply", body, 409)
        self.api.update("other@example.test", True, PEOPLE)
        await self.post("/v1/automation/reply", body, 409)
        self.api.update(ACCOUNT, True, PEOPLE)
        body["epoch"] = str(uuid.uuid4())
        await self.post("/v1/automation/reply", body, 409)

    async def test_restart_pauses_automatic_outbox_preserves_manual_and_idempotence(self):
        await self.lease()
        self.incoming()
        body = await self.reply_body()
        await self.post("/v1/automation/reply", body, 202)
        self.store.create(
            str(uuid.uuid4()),
            ACCOUNT,
            [{"jid": PEOPLE[0][0], "name": "Ficticio", "text": "Manual"}],
            self.now,
            "send-when-connected",
        )
        restarted = ScheduledMessageStore(self.store.path)
        rows, _ = restarted.list(ACCOUNT)
        self.assertEqual({row["state"] for row in rows}, {"pending", "held"})
        state = await self.get(f"/v1/requests/{body['request_id']}?account_id={self.account}")
        self.assertEqual(state["messages"][0]["state"], "held")

    async def test_history_does_not_trigger_but_edits_invalidate_and_empty_media_does_not_reply(
        self,
    ):
        fake = SimpleNamespace(_atajos_api=Mock(), current_jid=ACCOUNT)
        message = Message(
            chat_jid=PEOPLE[0][0],
            sender_jid="sender@example.test",
            body="Historia",
            message_id="history",
        )
        observe = AtajosIntegrationMixin._observe_atajos_message
        observe(fake, message, live=False, added=True)
        observe(fake, message, live=True, added=False)
        fake._atajos_api.observe_message.assert_not_called()
        message.edited = True
        observe(fake, message, live=False, added=False)
        self.assertEqual(fake._atajos_api.observe_message.call_args.args[2], "changed")
        message.edited = False
        message.media_kind = "audio"
        observe(fake, message, live=True, added=True)
        self.assertEqual(fake._atajos_api.observe_message.call_args.args[2], "changed")

    async def test_pagination_advances_past_removed_chats(self):
        for index in range(105):
            self.incoming(f"m{index}")
        self.incoming("group-last", group=True)
        self.api.update(ACCOUNT, True, [PEOPLE[1]])
        page = await self.events()
        self.assertEqual(page["events"], [])
        self.assertTrue(page["has_more"])
        page2 = await self.events(page["next_after"])
        self.assertEqual(len(page2["events"]), 1)
        self.assertFalse(page2["has_more"])

    async def test_group_monitoring_respects_scope_exclusions_and_revoke(self):
        await self.lease("groups")
        self.assertEqual(self.api.monitored_group_jids(ACCOUNT), {PEOPLE[1][0]})
        await self.post("/v1/automation/revoke", {"account_id": self.account, "rule_id": self.rule})
        self.assertFalse(self.api.monitored_group_jids(ACCOUNT))

    async def test_revoke_all_cancels_automatic_and_keeps_manual_queue(self):
        await self.lease()
        self.incoming()
        self.incoming("group-new", group=True)
        await self.post("/v1/automation/reply", await self.reply_body(), 202)
        await self.post("/v1/automation/reply", await self.reply_body(group=True), 202)
        self.store.create(
            str(uuid.uuid4()),
            ACCOUNT,
            [{"jid": PEOPLE[0][0], "name": "Ficticio", "text": "Manual"}],
            self.now,
            "send-when-connected",
        )
        await self.post("/v1/automation/revoke-all", {"account_id": self.account})
        await self.api.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["body"], "Manual")
        rows, _ = self.store.list(ACCOUNT)
        self.assertEqual(sum(row["state"] == "canceled" for row in rows), 2)
        await self.lease("all", excluded=[self.chats[1]["id"]])
        self.assertFalse(self.api.monitored_group_jids(ACCOUNT))

    async def test_second_edit_of_an_old_message_invalidates_a_newer_trigger(self):
        await self.lease()
        self.incoming("old", kind="changed")
        self.incoming("new-trigger")
        body = await self.reply_body()
        self.incoming("old", kind="changed")
        await self.post("/v1/automation/reply", body, 409)
        page = await self.events()
        self.assertEqual(page["events"][-1]["kind"], "changed")
        self.assertEqual(len(page["events"]), 3)

    async def test_live_message_without_identity_invalidates_but_never_triggers(self):
        await self.lease()
        self.incoming()
        body = await self.reply_body()
        self.api.observe_message(
            ACCOUNT,
            Message(
                chat_jid=PEOPLE[0][0],
                sender_jid="sender@example.test",
                body="Sin identidad ficticia",
            ),
            "incoming",
        )
        await self.post("/v1/automation/reply", body, 409)
        self.assertEqual((await self.events())["events"][-1]["kind"], "changed")

    async def test_replaced_diary_revokes_in_memory_and_reports_storage_error(self):
        await self.lease()
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute(
                "UPDATE assistant_metadata SET value=? WHERE key='epoch'", (str(uuid.uuid4()),)
            )
        response = await self.client.get("/v1/status", headers=self.headers)
        self.assertEqual(response.status, 503)
        self.assertFalse(self.api.monitored_group_jids(ACCOUNT))

    async def test_large_unicode_feed_pages_fit_without_skipping_identities(self):
        for index in range(35):
            message = Message(
                chat_jid=PEOPLE[0][0],
                sender_jid="sender@example.test",
                body="🙂" * 4000,
                message_id=f"wide-{index}",
            )
            self.api.observe_message(ACCOUNT, message, "incoming")
        after, identities = 0, set()
        while True:
            response = await self.client.post(
                "/v1/automation/events",
                headers=self.headers,
                json={**self.fields(), "after_seq": after},
            )
            self.assertEqual(response.status, 200)
            self.assertLess(len(await response.read()), 1024 * 1024)
            page = await response.json()
            identities.update(item["message_id"] for item in page["events"])
            after = page["next_after"]
            if not page["has_more"]:
                break
        self.assertEqual(len(identities), 35)

    async def test_early_diary_migration_preserves_sequence_and_multiple_edits(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        ScheduledMessageStore(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TABLE assistant_observations ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,jid TEXT NOT NULL,"
                "identity TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,sender TEXT NOT NULL,"
                "is_group INTEGER NOT NULL,sent_at TEXT NOT NULL,received_at REAL NOT NULL,"
                "UNIQUE(account,jid,identity,kind))"
            )
            connection.execute(
                "INSERT INTO assistant_observations VALUES (7,?,?,?,?,?,?,?,?,?)",
                (
                    ACCOUNT,
                    PEOPLE[0][0],
                    "original",
                    "incoming",
                    "Ficticio",
                    "Ficticio",
                    0,
                    self.date(),
                    self.now,
                ),
            )
        store = AssistantAutomationStore(path)
        self.assertEqual(store.latest(ACCOUNT), 7)
        event = {
            "account": ACCOUNT,
            "jid": PEOPLE[0][0],
            "identity": "original",
            "kind": "changed",
            "body": "",
            "sender": "Ficticio",
            "is_group": 0,
            "sent_at": self.date(),
            "received_at": self.now,
        }
        store.observe([event, event])
        self.assertEqual(store.latest(ACCOUNT), 9)
        self.assertEqual(store.events(ACCOUNT, 0, {PEOPLE[0][0]})["events"][0]["body"], "")

    async def test_optimistic_own_message_retains_only_its_dispatch_permission(self):
        await self.lease()
        self.incoming()
        await self.post("/v1/automation/reply", await self.reply_body(), 202)
        await self.api.tick()
        row = self.sent[0]
        optimistic = Message(
            chat_jid=row["jid"],
            sender_jid="me",
            body=row["body"],
            outgoing=True,
            message_id="cliente-xmpp-api-" + row["id"],
        )
        self.api.observe_message(ACCOUNT, optimistic, "outgoing")
        self.assertTrue(self.api.can_dispatch_on_ui(row))
        self.api.observe_message(ACCOUNT, optimistic, "outgoing")
        self.assertTrue(self.api.can_dispatch_on_ui(row))
        self.incoming("new-manual", kind="outgoing")
        self.assertFalse(self.api.can_dispatch_on_ui(row))

    async def test_protocol_checks_permission_before_handoff_and_disables_transient_retry(self):
        import asyncio

        for permitted in (False, True):
            deferred, retry, send = Mock(), Mock(), Mock()
            msg = MagicMock()
            msg.send = send
            service = XmppService(lambda _event: None)
            service._loop = asyncio.get_running_loop()
            service._client = SimpleNamespace(
                boundjid=SimpleNamespace(bare=ACCOUNT),
                is_connected=lambda: True,
                make_message=Mock(return_value=msg),
                track_transient_message_retry=retry,
            )
            service.send_message(
                PEOPLE[0][0],
                "Ficticio",
                expected_account=ACCOUNT,
                on_deferred=deferred,
                authorization=lambda permitted=permitted: permitted,
            )
            await asyncio.sleep(0)
            self.assertEqual(send.call_count, int(permitted))
            self.assertEqual(deferred.call_count, int(not permitted))
            retry.assert_not_called()
