from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cliente_xmpp.models.chat import Chat, Message, Poll, PollVote
from cliente_xmpp.storage.message_store import MessageStore


class MessageStoreTests(unittest.TestCase):
    def test_loads_starred_and_media_messages_for_one_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "chat@example.test"
            other_chat_jid = "other@example.test"
            starred = Message(
                chat_jid=chat_jid,
                sender_jid=chat_jid,
                body="Mensaje destacado",
                sent_at=datetime(2026, 7, 19, 8, tzinfo=UTC),
                message_id="starred-message",
                starred=True,
            )
            attachment = Message(
                chat_jid=chat_jid,
                sender_jid=chat_jid,
                body="Foto",
                sent_at=datetime(2026, 7, 19, 9, tzinfo=UTC),
                media_url="https://upload.example.test/photo.jpg",
                media_kind="image",
                message_id="media-message",
            )
            link = Message(
                chat_jid=chat_jid,
                sender_jid=chat_jid,
                body="https://example.test/article",
                sent_at=datetime(2026, 7, 19, 10, tzinfo=UTC),
                message_id="link-message",
            )
            other_chat = Message(
                chat_jid=other_chat_jid,
                sender_jid=other_chat_jid,
                body="Otro destacado",
                sent_at=datetime(2026, 7, 19, 11, tzinfo=UTC),
                message_id="other-starred",
                starred=True,
            )
            store.upsert_messages(account_jid, [starred, attachment, link, other_chat])

            self.assertEqual(
                [
                    message.message_id
                    for message in store.load_starred_messages(account_jid, chat_jid)
                ],
                ["starred-message"],
            )
            self.assertEqual(
                [
                    message.message_id
                    for message in store.load_media_messages(account_jid, chat_jid)
                ],
                ["media-message", "link-message"],
            )

            starred.starred = False
            store.update_message_starred(account_jid, starred)
            self.assertEqual(store.load_starred_messages(account_jid, chat_jid), [])

    def test_delete_chat_removes_its_messages_and_media_paths_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            deleted_chat = "deleted@example.test"
            kept_chat = "kept@example.test"
            media_path = str(Path(temp_dir) / "photo.jpg")
            store.upsert_chat(account_jid, Chat(jid=deleted_chat, name="Eliminar"))
            store.upsert_chat(account_jid, Chat(jid=kept_chat, name="Conservar"))
            store.upsert_messages(
                account_jid,
                [
                    Message(
                        chat_jid=deleted_chat,
                        sender_jid=deleted_chat,
                        body="Foto",
                        sent_at=datetime(2026, 8, 9, tzinfo=UTC),
                        message_id="deleted-message",
                        media_local_path=media_path,
                    ),
                    Message(
                        chat_jid=kept_chat,
                        sender_jid=kept_chat,
                        body="Conservar",
                        sent_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
                        message_id="kept-message",
                    ),
                ],
            )

            self.assertEqual(store.load_chat_media_paths(account_jid, deleted_chat), [media_path])
            store.delete_chat(account_jid, deleted_chat)

            self.assertEqual(store.load_chat_media_paths(account_jid, deleted_chat), [])
            self.assertEqual(store.load_recent_messages(account_jid, deleted_chat), [])
            cleared_chat = next(
                chat
                for chat in store.load_chats(account_jid)
                if chat.jid == deleted_chat
            )
            self.assertEqual(cleared_chat.last_message_preview, "")
            self.assertIsNone(cleared_chat.last_message_at)
            self.assertEqual(cleared_chat.unread_count, 0)
            self.assertEqual(
                [
                    message.message_id
                    for message in store.load_recent_messages(account_jid, kept_chat)
                ],
                ["kept-message"],
            )

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

    def test_retraction_clears_persisted_media_location_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            original = Message(
                chat_jid="chat@example.test",
                sender_jid="contact@example.test",
                body="voz",
                sent_at=datetime(2026, 7, 10, 12, 0),
                audio_url="https://upload.example.test/voice.ogg",
                media_url="https://upload.example.test/voice.ogg",
                media_kind="audio",
                media_mime="audio/ogg",
                media_filename="voice.ogg",
                media_size=123,
                media_duration_seconds=5,
                media_local_path=str(Path(temp_dir) / "voice.ogg"),
                message_id="wa-id-media",
            )
            store.upsert_messages("me@example.test", [original])

            retraction = Message(
                chat_jid=original.chat_jid,
                sender_jid=original.sender_jid,
                body="",
                sent_at=original.sent_at,
                message_id=original.message_id,
                retracted=True,
            )
            store.upsert_messages("me@example.test", [retraction])

            loaded = store.load_recent_messages("me@example.test", original.chat_jid)[0]
            self.assertTrue(loaded.retracted)
            self.assertEqual(loaded.audio_url, "")
            self.assertEqual(loaded.media_url, "")
            self.assertEqual(loaded.media_kind, "")
            self.assertEqual(loaded.media_local_path, "")

            store.upsert_messages("me@example.test", [original])
            loaded_again = store.load_recent_messages(
                "me@example.test",
                original.chat_jid,
            )[0]
            self.assertTrue(loaded_again.retracted)
            self.assertEqual(loaded_again.media_url, "")
            self.assertEqual(loaded_again.media_local_path, "")

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

    def test_persists_poll_metadata_for_cached_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            message = Message(
                chat_jid="contact@example.test",
                sender_jid="contact@example.test",
                body="🗳 ¿Café o té?\n☐ Café\n☐ Té",
                message_id="poll-1",
                poll=Poll(
                    poll_id="poll-1",
                    title="¿Café o té?",
                    options=("Café", "Té"),
                    creator_jid="123@s.whatsapp.net",
                    creator_lid="456@lid",
                    selectable_count=2,
                    allows_multiple=True,
                    votes=(
                        PollVote(
                            voter_jid="789@s.whatsapp.net",
                            voter_name="Ana",
                            option_hashes=("ab" * 32,),
                        ),
                    ),
                ),
            )

            store.upsert_messages("me@example.test", [message])

            loaded = store.load_recent_messages("me@example.test", message.chat_jid)
            self.assertEqual(loaded[0].poll, message.poll)

    def test_persists_latest_poll_vote_per_voter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            store.upsert_poll_vote(
                "me@example.test",
                "contact@example.test",
                "poll-1",
                "123@s.whatsapp.net",
                ("first",),
            )
            store.upsert_poll_vote(
                "me@example.test",
                "contact@example.test",
                "poll-1",
                "123@s.whatsapp.net",
                ("second", "third"),
            )

            self.assertEqual(
                store.load_poll_votes("me@example.test", "contact@example.test"),
                {("poll-1", "123@s.whatsapp.net"): ("second", "third")},
            )

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

    def test_delete_local_message_restores_previous_chat_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MessageStore(Path(temp_dir) / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "#room@example.test"
            older = Message(
                chat_jid=chat_jid,
                sender_jid=chat_jid,
                body="Mensaje anterior",
                sent_at=datetime(2026, 7, 10, 11, 59),
                message_id="remote-1",
            )
            failed = Message(
                chat_jid=chat_jid,
                sender_jid="me",
                body="No debe quedar",
                sent_at=datetime(2026, 7, 10, 12, 0),
                outgoing=True,
                chat_is_group=True,
                message_id="cliente-xmpp-failed-1",
                delivery_state="failed",
            )
            store.upsert_messages(account_jid, [older, failed])

            store.delete_local_message(account_jid, chat_jid, failed.message_id)

            loaded = store.load_recent_messages(account_jid, chat_jid)
            self.assertEqual([message.message_id for message in loaded], ["remote-1"])
            chat = store.load_chats(account_jid)[0]
            self.assertIn("Mensaje anterior", chat.last_message_preview)
            self.assertEqual(chat.last_message_at, older.sent_at.astimezone(UTC))

            store.delete_local_message(account_jid, chat_jid, older.message_id)

            loaded = store.load_recent_messages(account_jid, chat_jid)
            self.assertEqual([message.message_id for message in loaded], ["remote-1"])

    def test_startup_removes_only_failed_local_optimistic_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.sqlite3"
            store = MessageStore(path)
            chat_jid = "#room@example.test"
            failed_local = Message(
                chat_jid=chat_jid,
                sender_jid="me",
                body="Local",
                sent_at=datetime(2026, 7, 10, 12, 0),
                outgoing=True,
                chat_is_group=True,
                message_id="cliente-xmpp-failed-1",
                delivery_state="failed",
            )
            failed_remote = Message(
                chat_jid=chat_jid,
                sender_jid="Yo",
                body="Remoto",
                sent_at=datetime(2026, 7, 10, 12, 1),
                outgoing=True,
                chat_is_group=True,
                message_id="whatsapp-failed-1",
                delivery_state="failed",
            )
            sent_local_direct = Message(
                chat_jid="contact@example.test",
                sender_jid="me",
                body="Directo confirmado localmente",
                sent_at=datetime(2026, 7, 10, 12, 2),
                outgoing=True,
                message_id="cliente-xmpp-direct-1",
                delivery_state="sent",
            )
            only_failed_chat_jid = "#only-failed@example.test"
            only_failed_local = Message(
                chat_jid=only_failed_chat_jid,
                sender_jid="me",
                body="Único mensaje fallido",
                sent_at=datetime(2026, 7, 10, 12, 3),
                outgoing=True,
                chat_is_group=True,
                message_id="cliente-xmpp-only-failed-1",
                delivery_state="failed",
            )
            store.upsert_messages(
                "me@example.test",
                [failed_local, failed_remote, sent_local_direct, only_failed_local],
            )
            untouched_updated_at = "2000-01-01T00:00:00+00:00"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(
                    """
                    UPDATE chats
                    SET updated_at = ?
                    WHERE account_jid = ? AND jid = ?
                    """,
                    (
                        untouched_updated_at,
                        "me@example.test",
                        sent_local_direct.chat_jid,
                    ),
                )

            reopened = MessageStore(path)

            loaded = reopened.load_recent_messages("me@example.test", chat_jid)
            self.assertEqual([message.message_id for message in loaded], ["whatsapp-failed-1"])
            direct = reopened.load_recent_messages(
                "me@example.test",
                "contact@example.test",
            )
            self.assertEqual(
                [message.message_id for message in direct],
                ["cliente-xmpp-direct-1"],
            )
            chats = {
                chat.jid: chat for chat in reopened.load_chats("me@example.test")
            }
            self.assertEqual(chats[only_failed_chat_jid].last_message_preview, "")
            self.assertIsNone(chats[only_failed_chat_jid].last_message_at)
            with closing(sqlite3.connect(path)) as conn:
                actual_updated_at = conn.execute(
                    """
                    SELECT updated_at
                    FROM chats
                    WHERE account_jid = ? AND jid = ?
                    """,
                    ("me@example.test", sent_local_direct.chat_jid),
                ).fetchone()[0]
            self.assertEqual(actual_updated_at, untouched_updated_at)

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
