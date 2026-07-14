from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.message_store import MessageStore


class MessageStoreTests(unittest.TestCase):
    def test_technical_group_name_does_not_replace_stored_human_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            group_jid = "#120363401567622156@whatsapp.example.test"
            store.upsert_chat(
                account_jid,
                Chat(jid=group_jid, name="Desarrollo ⌨️", is_group=True),
            )

            store.upsert_chat(
                account_jid,
                Chat(jid=group_jid, name="#120363401567622156", is_group=True),
            )

            loaded = store.load_chats(account_jid)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "Desarrollo ⌨️")

    def test_new_human_group_name_replaces_stored_human_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            group_jid = "#120363401567622156@whatsapp.example.test"
            store.upsert_chat(
                account_jid,
                Chat(jid=group_jid, name="Desarrollo ⌨️", is_group=True),
            )

            store.upsert_chat(
                account_jid,
                Chat(jid=group_jid, name="Desarrollo accesible", is_group=True),
            )

            loaded = store.load_chats(account_jid)
            self.assertEqual(loaded[0].name, "Desarrollo accesible")

    def test_retracted_message_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="Yo",
                body="",
                sent_at=datetime(2026, 7, 10, 12, 0),
                outgoing=True,
                message_id="wa-id-1",
                retracted=True,
            )

            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", "chat@example.test")
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].retracted)
            self.assertEqual(loaded[0].body, "")

    def test_existing_database_gets_retracted_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE messages (
                        account_jid TEXT NOT NULL,
                        chat_jid TEXT NOT NULL,
                        message_key TEXT NOT NULL,
                        message_id TEXT NOT NULL DEFAULT '',
                        sender_jid TEXT NOT NULL,
                        sender_name TEXT NOT NULL DEFAULT '',
                        body TEXT NOT NULL DEFAULT '',
                        sent_at TEXT NOT NULL,
                        outgoing INTEGER NOT NULL DEFAULT 0,
                        audio_url TEXT NOT NULL DEFAULT '',
                        media_url TEXT NOT NULL DEFAULT '',
                        media_kind TEXT NOT NULL DEFAULT '',
                        media_mime TEXT NOT NULL DEFAULT '',
                        media_filename TEXT NOT NULL DEFAULT '',
                        media_size INTEGER NOT NULL DEFAULT 0,
                        media_duration_seconds REAL NOT NULL DEFAULT 0,
                        media_local_path TEXT NOT NULL DEFAULT '',
                        chat_is_group INTEGER NOT NULL DEFAULT 0,
                        starred INTEGER NOT NULL DEFAULT 0,
                        reactions_json TEXT NOT NULL DEFAULT '[]',
                        reply_quote TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL,
                        PRIMARY KEY (account_jid, chat_jid, message_key)
                    );
                    """
                )

            MessageStore(path)

            with closing(sqlite3.connect(path)) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            self.assertIn("retracted", columns)
            self.assertIn("edited", columns)
            self.assertIn("delivery_state", columns)
            self.assertIn("reply_to_jid", columns)
            self.assertIn("reply_to_id", columns)
            self.assertIn("displayed_marker_id", columns)
            self.assertIn("is_sticker", columns)
            self.assertIn("is_forwarded", columns)

    def test_persists_group_displayed_marker_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="#group@example.test",
                sender_jid="member@example.test",
                body="Mensaje de grupo",
                sent_at=datetime(2026, 7, 12, 12, 0),
                message_id="bridge-id",
                displayed_marker_id="room-stanza-id",
                chat_is_group=True,
            )

            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", message.chat_jid)
            self.assertEqual(loaded[0].displayed_marker_id, "room-stanza-id")

    def test_edited_message_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="Yo",
                body="Texto corregido",
                sent_at=datetime(2026, 7, 10, 12, 0),
                outgoing=True,
                message_id="wa-id-1",
                edited=True,
                reply_to_jid="contact@example.test",
                reply_to_id="quoted-id",
            )

            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", "chat@example.test")
            self.assertTrue(loaded[0].edited)
            self.assertEqual(loaded[0].reply_to_jid, "contact@example.test")
            self.assertEqual(loaded[0].reply_to_id, "quoted-id")

    def test_delivery_state_is_persisted_without_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="chat@example.test",
                sender_jid="Yo",
                body="Texto",
                sent_at=datetime(2026, 7, 10, 12, 0),
                outgoing=True,
                message_id="wa-id-1",
                delivery_state="delivered",
            )
            store.upsert_messages("me@example.test", [message])

            message.delivery_state = "sent"
            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", "chat@example.test")
            self.assertEqual(loaded[0].delivery_state, "delivered")

    def test_migration_normalizes_dates_and_rebuilds_chat_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            store = MessageStore(path)
            local_latest = datetime.now().replace(microsecond=0)
            older_utc = (local_latest - timedelta(minutes=1)).astimezone(UTC)
            messages = [
                Message(
                    chat_jid="chat@example.test",
                    sender_jid="Yo",
                    body="local latest",
                    sent_at=local_latest,
                    outgoing=True,
                    message_id="latest",
                    delivery_state="delivered",
                ),
                Message(
                    chat_jid="chat@example.test",
                    sender_jid="Yo",
                    body="older",
                    sent_at=older_utc,
                    outgoing=True,
                    message_id="older",
                    delivery_state="sent",
                ),
            ]
            store.upsert_messages("me@example.test", messages)

            with closing(sqlite3.connect(path)) as conn:
                conn.execute(
                    "UPDATE messages SET sent_at = ? WHERE message_id = 'latest'",
                    (local_latest.isoformat(),),
                )
                conn.execute(
                    "UPDATE messages SET sent_at = ? WHERE message_id = 'older'",
                    (older_utc.isoformat(),),
                )
                conn.execute(
                    "UPDATE chats SET last_message_preview = 'stale', last_message_at = ?",
                    (older_utc.isoformat(),),
                )
                conn.execute("PRAGMA user_version = 9")

            migrated = MessageStore(path)
            loaded = migrated.load_recent_messages("me@example.test", "chat@example.test")
            chat = migrated.load_chats("me@example.test")[0]

            self.assertTrue(all(message.sent_at.tzinfo is not None for message in loaded))
            self.assertEqual(chat.last_message_preview, "local latest | Entregado")


if __name__ == "__main__":
    unittest.main()
