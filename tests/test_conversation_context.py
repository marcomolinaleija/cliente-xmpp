from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.conversation_context import ConversationContextStore
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore

ACCOUNT = "owner@example.test"
CONTACT = "ana@example.test"
DATE = datetime(2026, 10, 3, tzinfo=UTC)


class ConversationContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "messages.sqlite3"
        self.store = MessageStore(self.path)
        self.store.upsert_chats(ACCOUNT, [Chat(CONTACT, "Ana ficticia", unread_count=7)])
        self.reader = ConversationContextStore(self.path)
        self.now = 1800000000.0
        self.sent = []
        self.api = LocalAssistantAPI(
            "a" * 43,
            self.sent.append,
            store=ScheduledMessageStore(Path(self.temp.name) / "outbox.sqlite3"),
            clock=lambda: self.now,
            read_context=self.reader.read_page,
        )
        self.api.update(
            ACCOUNT, False, [(CONTACT, "Ana ficticia"), ("other@example.test", "Otro ficticio")]
        )
        self.client = TestClient(TestServer(self.api.application(), host="127.0.0.1"))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + "a" * 43}
        response = await self.client.get("/v1/contacts?query=Ana", headers=self.headers)
        data = await response.json()
        self.body = {
            "account_id": data["account_id"],
            "contact_id": data["contacts"][0]["id"],
            "count": 5,
            "cursor": "",
        }

    def add_messages(self, start: int, stop: int, **kwargs) -> None:
        self.store.upsert_messages(
            ACCOUNT,
            [
                Message(
                    CONTACT,
                    CONTACT,
                    f"Ficticio {index}",
                    sent_at=DATE,
                    outgoing=bool(index % 2),
                    message_id=f"test-{index}",
                    **kwargs,
                )
                for index in range(start, stop)
            ],
        )

    async def test_group_context_keeps_participants_and_separates_individual_history(self):
        group = "group@rooms.example.test"
        self.store.upsert_messages(
            ACCOUNT,
            [
                Message(
                    group,
                    "participant@example.test",
                    "Texto grupal ficticio",
                    sender_name="Participante ficticio",
                    message_id="group-1",
                    chat_is_group=True,
                ),
                Message(
                    group,
                    "participant@example.test",
                    "Texto individual ficticio",
                    message_id="individual-1",
                    chat_is_group=False,
                ),
            ],
        )
        self.api.update(ACCOUNT, True, [(group, "Grupo ficticio", True)])
        response = await self.client.get("/v1/contacts?kind=groups", headers=self.headers)
        data = await response.json()
        response = await self.client.post(
            "/v1/context",
            headers=self.headers,
            json={
                "account_id": data["account_id"],
                "contact_id": data["contacts"][0]["id"],
                "count": 5,
                "cursor": "",
            },
        )
        self.assertEqual(response.status, 200)
        page = await response.json()
        self.assertTrue(page["is_group"])
        self.assertEqual(page["returned_count"], 1)
        self.assertEqual(page["messages"][0]["sender"], "Participante ficticio")
        self.assertEqual(page["messages"][0]["text"], "Texto grupal ficticio")
        self.assertNotIn("identity", page["messages"][0])
        self.assertNotIn("@", json.dumps(page))

    async def read(self, **kwargs) -> dict:
        response = await self.client.post(
            "/v1/context", json={**self.body, **kwargs}, headers=self.headers
        )
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_five_exactly_readonly_and_no_attachment_identifiers(self) -> None:
        self.add_messages(
            0,
            410,
            media_kind="image",
            media_url="https://private.example.test",
            media_local_path="C:/private/fictitious.jpg",
        )
        with closing(sqlite3.connect(self.path)) as connection:
            before = "\n".join(connection.iterdump())
        data = await self.read()
        self.assertEqual(data["returned_count"], 5)
        self.assertEqual(
            [m["text"] for m in data["messages"]], [f"Ficticio {i}" for i in range(405, 410)]
        )
        self.assertEqual(data["local_total"], 410)
        self.assertEqual(data["source"], "local-cache")
        self.assertFalse(data["marks_read"])
        self.assertTrue(data["has_more"])
        self.assertNotIn("example.test", json.dumps(data))
        self.assertNotIn("private", json.dumps(data))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(before, "\n".join(connection.iterdump()))
        self.assertFalse(self.sent)

    async def test_four_hundred_and_all_pages_tied_dates_snapshot_account_isolation(self) -> None:
        self.add_messages(0, 805)
        self.store.upsert_messages(
            "other-account@example.test",
            [Message(CONTACT, CONTACT, "Otro propietario", sent_at=DATE, message_id="other")],
        )
        self.store.upsert_messages(
            ACCOUNT, [Message("other@example.test", CONTACT, "Otro chat", message_id="other-chat")]
        )
        page = await self.read(count=400)
        self.assertEqual(page["local_total"], 805)
        texts = [item["text"] for item in page["messages"]]
        self.add_messages(805, 810)
        while page["has_more"]:
            page = await self.read(count=400, cursor=page["next_cursor"])
            texts = [item["text"] for item in page["messages"]] + texts
        self.assertEqual(texts, [f"Ficticio {i}" for i in range(805)])
        self.assertEqual(len(set(texts)), 805)
        self.assertFalse(page["page_limited"])

    async def test_cursor_bound_to_contact_and_expiration_and_invalid_count(self) -> None:
        self.add_messages(0, 10)
        first = await self.read()
        response = await self.client.get("/v1/contacts?query=Otro", headers=self.headers)
        other = (await response.json())["contacts"][0]["id"]
        for fields, status in [
            ({"contact_id": other, "cursor": first["next_cursor"]}, 409),
            ({"cursor": str(uuid.uuid4())}, 409),
            ({"account_id": str(uuid.uuid4())}, 409),
            ({"count": True}, 400),
            ({"count": 0}, 400),
            ({"count": 401}, 400),
        ]:
            response = await self.client.post(
                "/v1/context", json={**self.body, **fields}, headers=self.headers
            )
            self.assertEqual(response.status, status)
        self.now += 601
        response = await self.client.post(
            "/v1/context", json={**self.body, "cursor": first["next_cursor"]}, headers=self.headers
        )
        self.assertEqual(response.status, 409)

    async def test_account_changed_during_worker_does_not_return_conversation(self) -> None:
        self.add_messages(0, 5)

        def changed(*args):
            page = self.reader.read_page(*args)
            self.api.update("another@example.test", True, [(CONTACT, "Ana ficticia")])
            return page

        self.api._read_context = changed
        response = await self.client.post("/v1/context", json=self.body, headers=self.headers)
        self.assertEqual(response.status, 409)
        self.assertNotIn("Ficticio", await response.text())

    async def test_retracted_deleted_and_group_messages_excluded(self) -> None:
        self.add_messages(0, 3)
        self.store.upsert_messages(
            ACCOUNT,
            [
                Message(
                    CONTACT,
                    CONTACT,
                    "No revelar texto retraído",
                    message_id="retracted",
                    retracted=True,
                    sent_at=DATE,
                ),
                Message(
                    CONTACT,
                    CONTACT,
                    "Grupo ficticio",
                    message_id="group",
                    chat_is_group=True,
                    sent_at=DATE,
                ),
            ],
        )
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO deleted_messages VALUES (?,?,?,?)",
                (ACCOUNT, CONTACT, "test-1", DATE.isoformat()),
            )
            connection.commit()
        data = await self.read(count=400)
        self.assertEqual(data["local_total"], 3)
        self.assertEqual(
            [m["text"] for m in data["messages"]], ["Ficticio 0", "Ficticio 2", "Mensaje eliminado"]
        )
        self.assertNotIn("No revelar", json.dumps(data))

    async def test_large_message_reports_truncation_and_pages_without_skips(self) -> None:
        self.add_messages(0, 10)
        self.store.upsert_messages(
            ACCOUNT, [Message(CONTACT, CONTACT, "ñ<\"'&" * 20000, message_id="large", sent_at=DATE)]
        )
        first = await self.read(count=400)
        self.assertTrue(first["page_limited"])
        self.assertTrue(first["messages"][0]["text_truncated"])
        self.assertLess(len(json.dumps(first)), 50000)
        next_page = await self.read(count=10, cursor=first["next_cursor"])
        self.assertEqual(
            [m["text"] for m in next_page["messages"]], [f"Ficticio {i}" for i in range(10)]
        )
        self.assertFalse(next_page["has_more"])


if __name__ == "__main__":
    unittest.main()
