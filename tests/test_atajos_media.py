from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from cliente_xmpp.integrations.atajos_api import LocalAssistantAPI
from cliente_xmpp.media.downloads import DownloadedMedia, download_media
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.assistant_media import MAXIMUM_BYTES, AssistantMediaStore
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.storage.scheduled_messages import ScheduledMessageStore

ACCOUNT = "owner@example.test"
CONTACT = "ana@example.test"
GROUP = "group@rooms.example.test"
TOKEN = "a" * 43
DATE = datetime(2026, 10, 4, tzinfo=UTC)


class AtajosMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.path = self.root / "messages.sqlite3"
        self.store = MessageStore(self.path)
        self.media = AssistantMediaStore(self.path, roots=(self.downloads,))
        self.now = 1800000000.0
        self.sent = []
        self.api = LocalAssistantAPI(
            TOKEN,
            self.sent.append,
            store=ScheduledMessageStore(self.root / "outbox.sqlite3"),
            clock=lambda: self.now,
            media_store=self.media,
        )
        self.contacts = [(CONTACT, "Ana ficticia"), (GROUP, "Grupo ficticio", True)]
        self.api.update(ACCOUNT, False, self.contacts)
        self.client = TestClient(TestServer(self.api.application(), host="127.0.0.1"))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + TOKEN}
        response = await self.client.get("/v1/contacts?kind=all", headers=self.headers)
        data = await response.json()
        self.account_id = data["account_id"]
        self.contact_id = next(c["id"] for c in data["contacts"] if not c["is_group"])
        self.group_id = next(c["id"] for c in data["contacts"] if c["is_group"])

    def add(self, key, *, account=ACCOUNT, jid=CONTACT, group=False, **kwargs):
        path = self.downloads / (key + ".png")
        path.write_bytes(b"fictitious-image-bytes")
        args = {
            "sent_at": DATE,
            "media_kind": "image",
            "media_mime": "image/png",
            "media_filename": "foto.png",
            "media_local_path": str(path),
            "media_size": path.stat().st_size,
            "media_url": "https://files.example.test/private.png",
        }
        args.update(kwargs)
        self.store.upsert_messages(
            account,
            [
                Message(
                    jid,
                    "sender@example.test",
                    "Pie ficticio privado",
                    message_id=key,
                    chat_is_group=group,
                    **args,
                )
            ],
        )
        return path

    async def list_media(self, *, contact="", kind="image", count=1, **extra):
        return await self.client.post(
            "/v1/media",
            json={
                "account_id": self.account_id,
                "contact_id": contact,
                "kind": kind,
                "count": count,
                **extra,
            },
            headers=self.headers,
        )

    async def selection(self, **kwargs):
        response = await self.list_media(**kwargs)
        self.assertEqual(response.status, 200, await response.text())
        return (await response.json())["media"]

    async def content(self, media_id):
        return await self.client.post(
            "/v1/media/content",
            json={"account_id": self.account_id, "media_id": media_id},
            headers=self.headers,
        )

    async def test_latest_received_account_chat_and_kind_without_private_paths(self):
        self.add("first")
        self.add("group", jid=GROUP, group=True, sent_at=DATE + timedelta(seconds=1))
        self.add("outgoing", outgoing=True, sent_at=DATE + timedelta(seconds=10))
        self.add("sticker", is_sticker=True, sent_at=DATE + timedelta(seconds=11))
        self.add("retracted", retracted=True, sent_at=DATE + timedelta(seconds=20))
        self.add("other-account", account="another@example.test")
        self.add("unknown", jid="unknown@example.test")
        items = await self.selection(count=10)
        self.assertEqual([i["chat"] for i in items], ["Grupo ficticio", "Ana ficticia"])
        text = json.dumps(items)
        for secret in (str(self.root), "example.test", "Pie ficticio", "https://", TOKEN):
            self.assertNotIn(secret, text)
        self.assertEqual((await self.selection())[0]["chat"], "Grupo ficticio")
        self.assertEqual(len(await self.selection(contact=self.contact_id)), 1)
        self.assertEqual(len(await self.selection(contact=self.group_id)), 1)
        self.assertEqual(await self.selection(kind="audio"), [])
        self.assertFalse(self.sent)

    async def test_cached_content_original_mime_and_no_download(self):
        self.add("audio", media_kind="audio", media_mime="audio/mp4", media_filename="nota.m4a")
        item = (await self.selection(kind="audio"))[0]
        with patch("cliente_xmpp.storage.assistant_media.download_media") as download:
            response = await self.content(item["id"])
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), b"fictitious-image-bytes")
            self.assertEqual(response.headers["X-Atajos-Media-Mime"], "audio/mp4")
            self.assertEqual(response.headers["X-Atajos-Account"], self.account_id)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            download.assert_not_called()
        self.assertFalse(self.sent)

    async def test_retraction_after_selection_never_reads_or_downloads(self):
        self.add("retract-later")
        item = (await self.selection())[0]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE messages SET retracted=1 WHERE message_id='retract-later'")
        with patch("cliente_xmpp.storage.assistant_media.download_media") as download:
            response = await self.content(item["id"])
            self.assertEqual(response.status, 422)
            download.assert_not_called()
        self.assertEqual(await self.selection(), [])

    async def test_deleted_locally_or_linked_attachment_not_read(self):
        self.add("deleted-later")
        item = (await self.selection())[0]
        self.store.delete_cached_message(ACCOUNT, CONTACT, "deleted-later")
        self.assertEqual((await self.content(item["id"])).status, 422)
        self.assertEqual(await self.selection(), [])
        self.add("linked", sent_at=DATE + timedelta(seconds=1))
        item = (await self.selection())[0]
        with patch.object(Path, "is_symlink", return_value=True):
            self.assertEqual((await self.content(item["id"])).status, 422)

    async def test_expired_unknown_account_changed_or_removed_contact_rejected(self):
        self.add("test")
        item = (await self.selection())[0]
        self.assertEqual((await self.content(str(uuid.uuid4()))).status, 409)
        self.now += 601
        self.assertEqual((await self.content(item["id"])).status, 409)
        item = (await self.selection())[0]
        self.api.update("another@example.test", False, self.contacts)
        self.assertEqual((await self.content(item["id"])).status, 409)
        self.api.update(ACCOUNT, False, [])
        self.assertEqual((await self.content(item["id"])).status, 409)

    async def test_paths_outside_storage_and_oversize_not_read(self):
        outside = self.root / "private.png"
        outside.write_bytes(b"must-never-leave")
        self.add("outside", media_local_path=str(outside))
        item = (await self.selection())[0]
        response = await self.content(item["id"])
        self.assertEqual(response.status, 422)
        self.assertNotIn("must-never-leave", await response.text())
        path = self.add("oversize", sent_at=DATE + timedelta(seconds=1))
        with path.open("r+b") as file:
            file.truncate(MAXIMUM_BYTES + 1)
        item = (await self.selection())[0]
        self.assertEqual((await self.content(item["id"])).status, 422)
        self.assertTrue(outside.exists())

    async def test_missing_local_copy_downloaded_bounded_and_persisted(self):
        self.add("remote", media_local_path="")
        item = (await self.selection())[0]
        local = self.downloads / "downloaded.png"
        local.write_bytes(b"downloaded-fictitious")
        downloaded = DownloadedMedia(local, local.stat().st_size, "image/png", local.name)
        with patch(
            "cliente_xmpp.storage.assistant_media.download_media", return_value=downloaded
        ) as download:
            self.assertEqual((await self.content(item["id"])).status, 200)
            self.assertEqual((await self.content(item["id"])).status, 200)
            download.assert_called_once()
            self.assertEqual(download.call_args.kwargs, {"maximum_bytes": MAXIMUM_BYTES})
        with closing(sqlite3.connect(self.path)) as db, db:
            saved = db.execute(
                "SELECT media_local_path FROM messages WHERE message_id='remote'"
            ).fetchone()[0]
        self.assertEqual(saved, str(local))

    async def test_retraction_during_download_removes_only_new_download(self):
        original = self.add("remote-retracted", media_local_path="")
        item = (await self.selection())[0]
        local = self.downloads / "new-download.png"
        local.write_bytes(b"fictitious")

        def download(*args, **kwargs):
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("UPDATE messages SET retracted=1 WHERE message_id='remote-retracted'")
            return DownloadedMedia(local, 10, "image/png", local.name)

        with patch("cliente_xmpp.storage.assistant_media.download_media", side_effect=download):
            self.assertEqual((await self.content(item["id"])).status, 422)
        self.assertFalse(local.exists())
        self.assertTrue(original.exists())

    async def test_invalid_params_and_origin_are_rejected(self):
        for args in ({"count": True}, {"count": 11}, {"kind": {}}, {"path": "arbitrary"}):
            response = await self.list_media(**args)
            self.assertEqual(response.status, 400)
        response = await self.client.post(
            "/v1/media", json={}, headers={**self.headers, "Origin": "https://example.test"}
        )
        self.assertEqual(response.status, 403)
        self.assertFalse(self.sent)


class BoundedDownloadTests(unittest.TestCase):
    def test_local_file_urls_and_linked_storage_never_download(self):
        message = Message(CONTACT, CONTACT, "", media_url="file:///private.png")
        with patch("cliente_xmpp.media.downloads.urlopen") as opener:
            with self.assertRaises(ValueError):
                download_media(message, ACCOUNT, maximum_bytes=10)
            message.media_url = "https://example.test/f.png"
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    download_media(message, ACCOUNT, maximum_bytes=10)
            opener.assert_not_called()

    def test_declared_or_streamed_size_over_limit_cleans_partial_file(self):
        for length in ("11", ""):
            with tempfile.TemporaryDirectory() as directory:
                response = io.BytesIO(b"01234567890")
                response.headers = {"Content-Type": "image/png", "Content-Length": length}
                with (
                    patch("cliente_xmpp.media.downloads.DOWNLOADS_DIR", Path(directory)),
                    patch("cliente_xmpp.media.downloads.urlopen", return_value=response),
                ):
                    with self.assertRaises(ValueError):
                        download_media(
                            Message(
                                CONTACT,
                                CONTACT,
                                "",
                                media_url="https://example.test/f.png",
                                media_filename="f.png",
                            ),
                            ACCOUNT,
                            maximum_bytes=10,
                        )
                self.assertFalse([p for p in Path(directory).rglob("*") if p.is_file()])
