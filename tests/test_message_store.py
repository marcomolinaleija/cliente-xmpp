from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.message_store import MessageStore


class MessageStoreTests(unittest.TestCase):
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
            )

            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", "chat@example.test")
            self.assertTrue(loaded[0].edited)


if __name__ == "__main__":
    unittest.main()
