from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.media.links import is_link_preview, link_description
from cliente_xmpp.media.stickers import looks_like_bridge_sticker
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.models.mentions import GroupParticipant
from cliente_xmpp.models.names import is_fallback_chat_name

DATABASE_PATH = APP_DIR / "messages.sqlite3"
SCHEMA_VERSION = 15
MESSAGE_DUPLICATE_WINDOW_SECONDS = 3
OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS = 120


class MessageStore:
    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def load_chats(self, account_jid: str) -> list[Chat]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    jid, name, custom_name, is_group, notifications_muted,
                    notification_settings_known, group_member_count, is_self_group,
                    unread_count, last_message_preview, last_message_at
                FROM chats
                WHERE account_jid = ?
                ORDER BY COALESCE(last_message_at, '') DESC, name COLLATE NOCASE
                """,
                (account_jid,),
            ).fetchall()

        return [
            Chat(
                jid=str(row["jid"]),
                name=str(row["custom_name"] or row["name"] or row["jid"]),
                custom_name=str(row["custom_name"] or ""),
                is_group=bool(row["is_group"]),
                notifications_muted=bool(row["notifications_muted"]),
                notification_settings_known=bool(row["notification_settings_known"]),
                group_member_count=int(row["group_member_count"] or 0),
                is_self_group=bool(row["is_self_group"]),
                unread_count=int(row["unread_count"] or 0),
                last_message_preview=str(row["last_message_preview"] or ""),
                last_message_at=_datetime_from_db(row["last_message_at"]),
            )
            for row in rows
        ]

    def load_recent_messages(
        self,
        account_jid: str,
        chat_jid: str,
        limit: int = 80,
    ) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM messages
                WHERE account_jid = ? AND chat_jid = ?
                ORDER BY julianday(sent_at) DESC, rowid DESC
                LIMIT ?
                """,
                (account_jid, chat_jid, limit),
            ).fetchall()

        return [_message_from_row(row) for row in reversed(rows)]

    def load_latest_messages(self, account_jid: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM messages
                WHERE rowid IN (
                    SELECT (
                        SELECT latest.rowid
                        FROM messages AS latest
                        WHERE latest.account_jid = ?
                            AND latest.chat_jid = grouped.chat_jid
                        ORDER BY julianday(latest.sent_at) DESC, latest.rowid DESC
                        LIMIT 1
                    )
                    FROM (
                        SELECT DISTINCT chat_jid
                        FROM messages
                        WHERE account_jid = ?
                    ) AS grouped
                )
                """,
                (account_jid, account_jid),
            ).fetchall()

        return [_message_from_row(row) for row in rows]

    def search_messages(
        self,
        account_jid: str,
        query: str,
        limit: int = 200,
    ) -> list[Message]:
        terms = _search_terms(query)
        if not terms:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT messages.*, chats.name AS chat_name, chats.custom_name AS chat_custom_name
                FROM messages
                LEFT JOIN chats
                    ON chats.account_jid = messages.account_jid
                    AND chats.jid = messages.chat_jid
                WHERE messages.account_jid = ?
                ORDER BY julianday(messages.sent_at) DESC, messages.rowid DESC
                """,
                (account_jid,),
            ).fetchall()

        matches: list[Message] = []
        for row in rows:
            message = _message_from_row(row)
            haystack = _normalize_search_text(
                " ".join(
                    (
                        message.body,
                        message.reply_quote,
                        message.media_url,
                        message.media_filename,
                        message.sender_name,
                        message.sender_jid,
                        message.chat_jid,
                        str(row["chat_name"] or ""),
                        str(row["chat_custom_name"] or ""),
                    )
                )
            )
            if all(term in haystack for term in terms):
                matches.append(message)
                if len(matches) >= limit:
                    break

        return list(reversed(matches))

    def upsert_chat(self, account_jid: str, chat: Chat) -> None:
        with self._connect() as conn:
            self._upsert_chat(conn, account_jid, chat)

    def upsert_chats(self, account_jid: str, chats: list[Chat]) -> None:
        with self._connect() as conn:
            for chat in chats:
                self._upsert_chat(conn, account_jid, chat)

    def rename_chat(self, account_jid: str, chat_jid: str, name: str) -> None:
        now = _datetime_to_db(datetime.now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO chats (
                    account_jid, jid, name, custom_name, unread_count,
                    last_message_preview, last_message_at, updated_at
                )
                VALUES (?, ?, ?, ?, 0, '', NULL, ?)
                ON CONFLICT(account_jid, jid) DO UPDATE SET
                    custom_name = excluded.custom_name,
                    updated_at = excluded.updated_at
                """,
                (account_jid, chat_jid, name, name, now),
            )

    def set_chat_group_flag(self, account_jid: str, chat_jid: str, is_group: bool) -> None:
        now = _datetime_to_db(datetime.now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chats
                SET is_group = ?, updated_at = ?
                WHERE account_jid = ? AND jid = ?
                """,
                (int(is_group), now, account_jid, chat_jid),
            )

    def set_chat_notifications_muted(
        self,
        account_jid: str,
        chat_jid: str,
        muted: bool,
    ) -> None:
        now = _datetime_to_db(datetime.now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chats
                SET notifications_muted = ?, notification_settings_known = 1, updated_at = ?
                WHERE account_jid = ? AND jid = ?
                """,
                (int(muted), now, account_jid, chat_jid),
            )

    def load_group_participants(self, account_jid: str, group_jid: str) -> list[GroupParticipant]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT group_jid, participant_jid, nick
                FROM group_participants
                WHERE account_jid = ? AND group_jid = ?
                ORDER BY nick COLLATE NOCASE
                """,
                (account_jid, group_jid),
            ).fetchall()

        return [
            GroupParticipant(
                group_jid=str(row["group_jid"]),
                jid=str(row["participant_jid"]),
                nick=str(row["nick"]),
            )
            for row in rows
        ]

    def upsert_group_participant(self, account_jid: str, participant: GroupParticipant) -> None:
        self.upsert_group_participants(account_jid, [participant])

    def upsert_group_participants(
        self,
        account_jid: str,
        participants: list[GroupParticipant],
    ) -> None:
        valid_participants = [
            participant
            for participant in participants
            if participant.group_jid and participant.jid and participant.nick
        ]
        if not valid_participants:
            return

        with self._connect() as conn:
            now = _datetime_to_db(datetime.now())
            conn.executemany(
                """
                INSERT INTO group_participants (
                    account_jid, group_jid, participant_jid, nick, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_jid, group_jid, participant_jid) DO UPDATE SET
                    nick = excluded.nick,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        account_jid,
                        participant.group_jid,
                        participant.jid,
                        participant.nick,
                        now,
                    )
                    for participant in valid_participants
                ],
            )

    def update_message_media_local_path(
        self,
        account_jid: str,
        message: Message,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE messages
                SET media_local_path = ?, media_size = ?,
                    media_duration_seconds = COALESCE(
                        NULLIF(?, 0),
                        media_duration_seconds
                    ),
                    media_mime = COALESCE(
                        NULLIF(?, ''),
                        media_mime
                    ),
                    media_filename = COALESCE(NULLIF(?, ''), media_filename),
                    is_sticker = CASE
                        WHEN ? = 1 OR is_sticker = 1 THEN 1
                        ELSE 0
                    END
                WHERE account_jid = ? AND chat_jid = ?
                    AND (
                        message_key = ?
                        OR (? != '' AND media_url = ?)
                    )
                """,
                (
                    message.media_local_path,
                    message.media_size,
                    message.media_duration_seconds,
                    message.media_mime,
                    message.media_filename,
                    int(message.is_sticker),
                    account_jid,
                    message.chat_jid,
                    _message_key(message),
                    message.media_url,
                    message.media_url,
                ),
            )
            self._upsert_message_chat_summary(conn, account_jid, message)

    def upsert_messages(self, account_jid: str, messages: list[Message]) -> None:
        if not messages:
            return

        with self._connect() as conn:
            for message in messages:
                self._upsert_message(conn, account_jid, message)
                self._upsert_message_chat_summary(conn, account_jid, message)

    def _initialize(self) -> None:
        with self._connect() as conn:
            previous_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    account_jid TEXT NOT NULL,
                    jid TEXT NOT NULL,
                    name TEXT NOT NULL,
                    custom_name TEXT NOT NULL DEFAULT '',
                    is_group INTEGER NOT NULL DEFAULT 0,
                    notifications_muted INTEGER NOT NULL DEFAULT 0,
                    notification_settings_known INTEGER NOT NULL DEFAULT 0,
                    group_member_count INTEGER NOT NULL DEFAULT 0,
                    is_self_group INTEGER NOT NULL DEFAULT 0,
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    last_message_preview TEXT NOT NULL DEFAULT '',
                    last_message_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_jid, jid)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    account_jid TEXT NOT NULL,
                    chat_jid TEXT NOT NULL,
                    message_key TEXT NOT NULL,
                    message_id TEXT NOT NULL DEFAULT '',
                    displayed_marker_id TEXT NOT NULL DEFAULT '',
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
                    is_sticker INTEGER NOT NULL DEFAULT 0,
                    is_forwarded INTEGER NOT NULL DEFAULT 0,
                    chat_is_group INTEGER NOT NULL DEFAULT 0,
                    starred INTEGER NOT NULL DEFAULT 0,
                    reactions_json TEXT NOT NULL DEFAULT '[]',
                    reply_quote TEXT NOT NULL DEFAULT '',
                    reply_to_jid TEXT NOT NULL DEFAULT '',
                    reply_to_id TEXT NOT NULL DEFAULT '',
                    retracted INTEGER NOT NULL DEFAULT 0,
                    edited INTEGER NOT NULL DEFAULT 0,
                    delivery_state TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (account_jid, chat_jid, message_key)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_chat_sent
                ON messages (account_jid, chat_jid, sent_at);

                CREATE TABLE IF NOT EXISTS group_participants (
                    account_jid TEXT NOT NULL,
                    group_jid TEXT NOT NULL,
                    participant_jid TEXT NOT NULL,
                    nick TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_jid, group_jid, participant_jid)
                );
                """
            )
            self._ensure_message_columns(conn)
            self._ensure_chat_columns(conn)
            if previous_version < SCHEMA_VERSION:
                self._normalize_datetime_columns(conn)
                self._backfill_sticker_flags(conn)
                self._compact_duplicate_messages(conn)
                self._rebuild_chat_summaries(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if previous_version >= SCHEMA_VERSION:
                self._compact_duplicate_messages(conn)

    def _normalize_datetime_columns(self, conn: sqlite3.Connection) -> None:
        for table, column in (
            ("messages", "sent_at"),
            ("messages", "received_at"),
            ("chats", "last_message_at"),
            ("chats", "updated_at"),
        ):
            rows = conn.execute(
                f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for row in rows:
                value = _datetime_from_db(row[column])
                normalized = _datetime_to_db(value)
                if normalized and normalized != row[column]:
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                        (normalized, row["rowid"]),
                    )

    def _backfill_sticker_flags(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT rowid, media_url, media_kind, media_mime, media_filename
            FROM messages
            WHERE is_sticker = 0
                AND lower(media_kind) = 'image'
                AND lower(media_mime) LIKE 'image/webp%'
            """
        ).fetchall()
        sticker_rowids = [
            row["rowid"]
            for row in rows
            if looks_like_bridge_sticker(
                media_kind=str(row["media_kind"] or ""),
                media_mime=str(row["media_mime"] or ""),
                media_filename=str(row["media_filename"] or ""),
                media_url=str(row["media_url"] or ""),
            )
        ]
        conn.executemany(
            "UPDATE messages SET is_sticker = 1 WHERE rowid = ?",
            ((rowid,) for rowid in sticker_rowids),
        )

    def _rebuild_chat_summaries(self, conn: sqlite3.Connection) -> None:
        chats = conn.execute(
            "SELECT account_jid, jid FROM chats"
        ).fetchall()
        for chat in chats:
            latest = conn.execute(
                """
                SELECT *
                FROM messages
                WHERE account_jid = ? AND chat_jid = ?
                ORDER BY julianday(sent_at) DESC, rowid DESC
                LIMIT 1
                """,
                (chat["account_jid"], chat["jid"]),
            ).fetchone()
            if latest is None:
                continue

            message = _message_from_row(latest)
            conn.execute(
                """
                UPDATE chats
                SET last_message_preview = ?, last_message_at = ?, updated_at = ?
                WHERE account_jid = ? AND jid = ?
                """,
                (
                    _message_preview(message),
                    _datetime_to_db(message.sent_at),
                    _datetime_to_db(datetime.now()),
                    chat["account_jid"],
                    chat["jid"],
                ),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_message_columns(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        columns = {
            "displayed_marker_id": "TEXT NOT NULL DEFAULT ''",
            "media_url": "TEXT NOT NULL DEFAULT ''",
            "media_kind": "TEXT NOT NULL DEFAULT ''",
            "media_mime": "TEXT NOT NULL DEFAULT ''",
            "media_filename": "TEXT NOT NULL DEFAULT ''",
            "media_size": "INTEGER NOT NULL DEFAULT 0",
            "media_duration_seconds": "REAL NOT NULL DEFAULT 0",
            "media_local_path": "TEXT NOT NULL DEFAULT ''",
            "is_sticker": "INTEGER NOT NULL DEFAULT 0",
            "is_forwarded": "INTEGER NOT NULL DEFAULT 0",
            "sender_name": "TEXT NOT NULL DEFAULT ''",
            "chat_is_group": "INTEGER NOT NULL DEFAULT 0",
            "reply_quote": "TEXT NOT NULL DEFAULT ''",
            "reply_to_jid": "TEXT NOT NULL DEFAULT ''",
            "reply_to_id": "TEXT NOT NULL DEFAULT ''",
            "retracted": "INTEGER NOT NULL DEFAULT 0",
            "edited": "INTEGER NOT NULL DEFAULT 0",
            "delivery_state": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {definition}")

    def _ensure_chat_columns(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(chats)").fetchall()
        }
        if "custom_name" not in existing_columns:
            conn.execute("ALTER TABLE chats ADD COLUMN custom_name TEXT NOT NULL DEFAULT ''")
        if "is_group" not in existing_columns:
            conn.execute("ALTER TABLE chats ADD COLUMN is_group INTEGER NOT NULL DEFAULT 0")
        if "notifications_muted" not in existing_columns:
            conn.execute(
                "ALTER TABLE chats ADD COLUMN notifications_muted INTEGER NOT NULL DEFAULT 0"
            )
        if "notification_settings_known" not in existing_columns:
            conn.execute(
                "ALTER TABLE chats ADD COLUMN notification_settings_known "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "group_member_count" not in existing_columns:
            conn.execute(
                "ALTER TABLE chats ADD COLUMN group_member_count INTEGER NOT NULL DEFAULT 0"
            )
        if "is_self_group" not in existing_columns:
            conn.execute("ALTER TABLE chats ADD COLUMN is_self_group INTEGER NOT NULL DEFAULT 0")

    def _compact_duplicate_messages(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT rowid AS db_rowid, *
            FROM messages
            WHERE outgoing = 1
            ORDER BY account_jid, chat_jid, sent_at, rowid
            """
        ).fetchall()
        kept_by_content: dict[tuple[object, ...], list[sqlite3.Row]] = {}
        delete_rowids: set[int] = set()

        for row in rows:
            key = _duplicate_content_key(row)
            candidates = kept_by_content.setdefault(key, [])
            duplicate_index = _matching_duplicate_row_index(row, candidates)
            if duplicate_index is None:
                candidates.append(row)
                continue

            survivor = candidates[duplicate_index]
            duplicate = row
            if _duplicate_row_prefer_current(row, survivor):
                survivor, duplicate = row, survivor
                candidates[duplicate_index] = row

            self._merge_duplicate_message_rows(conn, survivor, duplicate)
            delete_rowids.add(int(duplicate["db_rowid"]))

        for rowid in delete_rowids:
            conn.execute("DELETE FROM messages WHERE rowid = ?", (rowid,))

    @staticmethod
    def _merge_duplicate_message_rows(
        conn: sqlite3.Connection,
        survivor: sqlite3.Row,
        duplicate: sqlite3.Row,
    ) -> None:
        conn.execute(
            """
            UPDATE messages
            SET
                message_id = COALESCE(NULLIF(message_id, ''), ?),
                displayed_marker_id = COALESCE(NULLIF(displayed_marker_id, ''), ?),
                audio_url = COALESCE(NULLIF(audio_url, ''), ?),
                media_url = COALESCE(NULLIF(media_url, ''), ?),
                media_kind = COALESCE(NULLIF(media_kind, ''), ?),
                media_mime = COALESCE(NULLIF(media_mime, ''), ?),
                media_filename = COALESCE(NULLIF(media_filename, ''), ?),
                media_size = COALESCE(NULLIF(media_size, 0), ?),
                media_duration_seconds = COALESCE(NULLIF(media_duration_seconds, 0), ?),
                media_local_path = COALESCE(NULLIF(media_local_path, ''), ?),
                is_sticker = CASE WHEN is_sticker = 1 OR ? = 1 THEN 1 ELSE 0 END,
                is_forwarded = CASE WHEN is_forwarded = 1 OR ? = 1 THEN 1 ELSE 0 END,
                reply_quote = COALESCE(NULLIF(reply_quote, ''), ?),
                reply_to_jid = COALESCE(NULLIF(reply_to_jid, ''), ?),
                reply_to_id = COALESCE(NULLIF(reply_to_id, ''), ?),
                retracted = CASE
                    WHEN retracted = 1 OR ? = 1 THEN 1
                    ELSE 0
                END
            WHERE rowid = ?
            """,
            (
                duplicate["message_id"],
                duplicate["displayed_marker_id"],
                duplicate["audio_url"],
                duplicate["media_url"],
                duplicate["media_kind"],
                duplicate["media_mime"],
                duplicate["media_filename"],
                duplicate["media_size"],
                duplicate["media_duration_seconds"],
                duplicate["media_local_path"],
                duplicate["is_sticker"],
                duplicate["is_forwarded"],
                duplicate["reply_quote"],
                duplicate["reply_to_jid"],
                duplicate["reply_to_id"],
                duplicate["retracted"],
                survivor["db_rowid"],
            ),
        )

    def _upsert_chat(self, conn: sqlite3.Connection, account_jid: str, chat: Chat) -> None:
        now = _datetime_to_db(datetime.now())
        display_name = chat.custom_name or chat.name
        last_message_preview = chat.last_message_preview
        last_message_at = chat.last_message_at
        existing = conn.execute(
            """
            SELECT name, last_message_preview, last_message_at, custom_name, is_group,
                notifications_muted, notification_settings_known, group_member_count,
                is_self_group
            FROM chats
            WHERE account_jid = ? AND jid = ?
            """,
            (account_jid, chat.jid),
        ).fetchone()
        if existing:
            existing_name = str(existing["custom_name"] or existing["name"] or "")
            if (
                not chat.custom_name
                and is_fallback_chat_name(chat.jid, display_name)
                and not is_fallback_chat_name(chat.jid, existing_name)
            ):
                display_name = existing_name
            existing_preview = str(existing["last_message_preview"] or "")
            existing_at = _datetime_from_db(existing["last_message_at"])
            if not _should_replace_summary(last_message_at, existing_at):
                last_message_preview = existing_preview
                last_message_at = existing_at
            elif not last_message_preview:
                last_message_preview = existing_preview
            is_group = chat.is_group or bool(existing["is_group"])
            if chat.notification_settings_known:
                notifications_muted = chat.notifications_muted
                notification_settings_known = True
            else:
                notifications_muted = bool(existing["notifications_muted"])
                notification_settings_known = bool(existing["notification_settings_known"])
            group_member_count = chat.group_member_count or int(
                existing["group_member_count"] or 0
            )
            is_self_group = chat.is_self_group or bool(existing["is_self_group"])
        else:
            is_group = chat.is_group
            notifications_muted = chat.notifications_muted
            notification_settings_known = chat.notification_settings_known
            group_member_count = chat.group_member_count
            is_self_group = chat.is_self_group

        conn.execute(
            """
            INSERT INTO chats (
                account_jid, jid, name, custom_name, is_group, notifications_muted,
                notification_settings_known, group_member_count, is_self_group,
                unread_count, last_message_preview, last_message_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_jid, jid) DO UPDATE SET
                name = excluded.name,
                custom_name = COALESCE(NULLIF(excluded.custom_name, ''), chats.custom_name),
                is_group = CASE
                    WHEN excluded.is_group = 1 OR chats.is_group = 1 THEN 1
                    ELSE 0
                END,
                notifications_muted = excluded.notifications_muted,
                notification_settings_known = CASE
                    WHEN excluded.notification_settings_known = 1
                        OR chats.notification_settings_known = 1 THEN 1
                    ELSE 0
                END,
                group_member_count = COALESCE(
                    NULLIF(excluded.group_member_count, 0),
                    chats.group_member_count
                ),
                is_self_group = CASE
                    WHEN excluded.is_self_group = 1 OR chats.is_self_group = 1 THEN 1
                    ELSE 0
                END,
                unread_count = excluded.unread_count,
                last_message_preview = COALESCE(
                    NULLIF(excluded.last_message_preview, ''),
                    chats.last_message_preview
                ),
                last_message_at = COALESCE(excluded.last_message_at, chats.last_message_at),
                updated_at = excluded.updated_at
            """,
            (
                account_jid,
                chat.jid,
                display_name,
                chat.custom_name,
                int(is_group),
                int(notifications_muted),
                int(notification_settings_known),
                group_member_count,
                int(is_self_group),
                chat.unread_count,
                last_message_preview,
                _datetime_to_db(last_message_at),
                now,
            ),
        )

    def _upsert_message(
        self,
        conn: sqlite3.Connection,
        account_jid: str,
        message: Message,
    ) -> None:
        now = _datetime_to_db(datetime.now())
        message_key = _message_key_for_upsert(conn, account_jid, message)
        conn.execute(
            """
            INSERT INTO messages (
                account_jid, chat_jid, message_key, message_id, displayed_marker_id, sender_jid,
                sender_name, body, sent_at, outgoing, audio_url, media_url, media_kind,
                media_mime, media_filename, media_size, media_duration_seconds,
                media_local_path, is_sticker, is_forwarded, chat_is_group, starred,
                reactions_json, reply_quote,
                reply_to_jid, reply_to_id, retracted, edited, delivery_state, received_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(account_jid, chat_jid, message_key) DO UPDATE SET
                message_id = COALESCE(NULLIF(excluded.message_id, ''), messages.message_id),
                displayed_marker_id = COALESCE(
                    NULLIF(excluded.displayed_marker_id, ''),
                    messages.displayed_marker_id
                ),
                sender_jid = excluded.sender_jid,
                sender_name = COALESCE(NULLIF(excluded.sender_name, ''), messages.sender_name),
                body = CASE
                    WHEN excluded.retracted = 1 THEN ''
                    WHEN messages.retracted = 1 THEN messages.body
                    WHEN excluded.reply_quote != '' THEN excluded.body
                    WHEN messages.reply_quote != '' THEN messages.body
                    ELSE excluded.body
                END,
                sent_at = excluded.sent_at,
                outgoing = excluded.outgoing,
                audio_url = COALESCE(NULLIF(excluded.audio_url, ''), messages.audio_url),
                media_url = COALESCE(NULLIF(excluded.media_url, ''), messages.media_url),
                media_kind = COALESCE(NULLIF(excluded.media_kind, ''), messages.media_kind),
                media_mime = COALESCE(NULLIF(excluded.media_mime, ''), messages.media_mime),
                media_filename = COALESCE(
                    NULLIF(excluded.media_filename, ''),
                    messages.media_filename
                ),
                media_size = COALESCE(NULLIF(excluded.media_size, 0), messages.media_size),
                media_duration_seconds = COALESCE(
                    NULLIF(excluded.media_duration_seconds, 0),
                    messages.media_duration_seconds
                ),
                media_local_path = COALESCE(
                    NULLIF(excluded.media_local_path, ''),
                    messages.media_local_path
                ),
                is_sticker = CASE
                    WHEN excluded.is_sticker = 1 OR messages.is_sticker = 1 THEN 1
                    ELSE 0
                END,
                is_forwarded = CASE
                    WHEN excluded.is_forwarded = 1 OR messages.is_forwarded = 1 THEN 1
                    ELSE 0
                END,
                chat_is_group = CASE
                    WHEN excluded.chat_is_group = 1 OR messages.chat_is_group = 1 THEN 1
                    ELSE 0
                END,
                starred = excluded.starred,
                reactions_json = excluded.reactions_json,
                reply_quote = CASE
                    WHEN excluded.retracted = 1 THEN ''
                    ELSE COALESCE(NULLIF(excluded.reply_quote, ''), messages.reply_quote)
                END,
                reply_to_jid = COALESCE(NULLIF(excluded.reply_to_jid, ''), messages.reply_to_jid),
                reply_to_id = COALESCE(NULLIF(excluded.reply_to_id, ''), messages.reply_to_id),
                retracted = CASE
                    WHEN excluded.retracted = 1 OR messages.retracted = 1 THEN 1
                    ELSE 0
                END,
                edited = CASE
                    WHEN excluded.edited = 1 OR messages.edited = 1 THEN 1
                    ELSE 0
                END,
                delivery_state = CASE
                    WHEN excluded.delivery_state = 'failed' THEN 'failed'
                    WHEN messages.delivery_state = 'failed' THEN 'failed'
                    WHEN excluded.delivery_state IN ('read', 'displayed')
                        AND messages.delivery_state NOT IN ('read', 'displayed')
                        THEN excluded.delivery_state
                    WHEN excluded.delivery_state IN ('delivered', 'received')
                        AND messages.delivery_state NOT IN (
                            'delivered', 'received', 'read', 'displayed'
                        )
                        THEN excluded.delivery_state
                    WHEN excluded.delivery_state = 'sent'
                        AND COALESCE(messages.delivery_state, '') IN ('', 'pending')
                        THEN 'sent'
                    WHEN COALESCE(messages.delivery_state, '') = ''
                        THEN excluded.delivery_state
                    ELSE messages.delivery_state
                END
            """,
            (
                account_jid,
                message.chat_jid,
                message_key,
                message.message_id,
                message.displayed_marker_id,
                message.sender_jid,
                message.sender_name,
                message.body,
                _datetime_to_db(message.sent_at) or _datetime_to_db(datetime.now()),
                int(message.outgoing),
                message.audio_url,
                message.media_url,
                message.media_kind,
                message.media_mime,
                message.media_filename,
                message.media_size,
                message.media_duration_seconds,
                message.media_local_path,
                int(message.is_sticker),
                int(message.is_forwarded),
                int(message.chat_is_group),
                int(message.starred),
                json.dumps(list(message.reactions), ensure_ascii=False),
                message.reply_quote,
                message.reply_to_jid,
                message.reply_to_id,
                int(message.retracted),
                int(message.edited),
                message.delivery_state,
                now,
            ),
        )

    def _upsert_message_chat_summary(
        self,
        conn: sqlite3.Connection,
        account_jid: str,
        message: Message,
    ) -> None:
        existing = conn.execute(
            """
            SELECT name, unread_count, last_message_at, is_group, notifications_muted,
                notification_settings_known, group_member_count, is_self_group
            FROM chats
            WHERE account_jid = ? AND jid = ?
            """,
            (account_jid, message.chat_jid),
        ).fetchone()

        latest = conn.execute(
            """
            SELECT *
            FROM messages
            WHERE account_jid = ? AND chat_jid = ?
            ORDER BY julianday(sent_at) DESC, rowid DESC
            LIMIT 1
            """,
            (account_jid, message.chat_jid),
        ).fetchone()
        if latest is None:
            return

        latest_message = _message_from_row(latest)

        now = _datetime_to_db(datetime.now())
        name = str(existing["name"] or message.chat_jid) if existing else message.chat_jid
        unread_count = int(existing["unread_count"] or 0) if existing else 0
        is_group = bool(existing["is_group"]) if existing else message.chat_is_group
        notifications_muted = bool(existing["notifications_muted"]) if existing else False
        notification_settings_known = (
            bool(existing["notification_settings_known"]) if existing else False
        )
        group_member_count = int(existing["group_member_count"] or 0) if existing else 0
        is_self_group = bool(existing["is_self_group"]) if existing else False
        conn.execute(
            """
            INSERT INTO chats (
                account_jid, jid, name, is_group, notifications_muted,
                notification_settings_known, group_member_count, is_self_group,
                unread_count, last_message_preview, last_message_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_jid, jid) DO UPDATE SET
                name = chats.name,
                is_group = CASE
                    WHEN excluded.is_group = 1 OR chats.is_group = 1 THEN 1
                    ELSE 0
                END,
                notifications_muted = chats.notifications_muted,
                notification_settings_known = chats.notification_settings_known,
                group_member_count = chats.group_member_count,
                is_self_group = chats.is_self_group,
                unread_count = chats.unread_count,
                last_message_preview = excluded.last_message_preview,
                last_message_at = excluded.last_message_at,
                updated_at = excluded.updated_at
            """,
            (
                account_jid,
                message.chat_jid,
                name,
                int(is_group),
                int(notifications_muted),
                int(notification_settings_known),
                group_member_count,
                int(is_self_group),
                unread_count,
                _message_preview(latest_message),
                _datetime_to_db(latest_message.sent_at),
                now,
            ),
        )


def _message_key_for_upsert(
    conn: sqlite3.Connection,
    account_jid: str,
    message: Message,
) -> str:
    if message.message_id:
        existing = conn.execute(
            """
            SELECT message_key
            FROM messages
            WHERE account_jid = ? AND chat_jid = ? AND message_id = ?
            LIMIT 1
            """,
            (account_jid, message.chat_jid, message.message_id),
        ).fetchone()
        if existing is not None:
            return str(existing["message_key"])

    duplicate = _find_duplicate_message_row(conn, account_jid, message)
    if duplicate is not None:
        return str(duplicate["message_key"])

    return _message_key(message)


def _duplicate_content_key(row: sqlite3.Row) -> tuple[object, ...]:
    outgoing = bool(row["outgoing"])
    return (
        row["account_jid"],
        row["chat_jid"],
        "outgoing" if outgoing else row["sender_jid"],
        row["body"],
        outgoing,
        row["audio_url"],
        row["media_url"],
        row["media_kind"],
        bool(row["is_sticker"]),
        bool(row["is_forwarded"]),
    )


def _matching_duplicate_row_index(
    row: sqlite3.Row,
    candidates: list[sqlite3.Row],
) -> int | None:
    row_sent_at = _datetime_from_db(row["sent_at"])
    if row_sent_at is None:
        return None
    row_timestamp = _datetime_timestamp(row_sent_at)
    if row_timestamp is None:
        return None

    for index, candidate in enumerate(candidates):
        if not row["message_id"] and not candidate["message_id"]:
            continue
        if not _duplicate_rows_have_compatible_reply_quotes(row, candidate):
            continue

        candidate_sent_at = _datetime_from_db(candidate["sent_at"])
        if candidate_sent_at is None:
            continue
        candidate_timestamp = _datetime_timestamp(candidate_sent_at)
        if candidate_timestamp is None:
            continue

        duplicate_window = _duplicate_row_window_seconds(row, candidate)
        delta = abs(row_timestamp - candidate_timestamp)
        if delta <= duplicate_window:
            return index

    return None


def _duplicate_row_prefer_current(current: sqlite3.Row, existing: sqlite3.Row) -> bool:
    if current["message_id"] and not existing["message_id"]:
        return True
    if current["reply_quote"] and not existing["reply_quote"]:
        return True
    if current["media_local_path"] and not existing["media_local_path"]:
        return True
    return False


def _duplicate_row_window_seconds(first: sqlite3.Row, second: sqlite3.Row) -> int:
    if first["message_id"] and second["message_id"]:
        return MESSAGE_DUPLICATE_WINDOW_SECONDS
    if bool(first["outgoing"]) and bool(second["outgoing"]):
        return OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS
    return MESSAGE_DUPLICATE_WINDOW_SECONDS


def _duplicate_rows_have_compatible_reply_quotes(
    first: sqlite3.Row,
    second: sqlite3.Row,
) -> bool:
    return (
        not first["reply_quote"]
        or not second["reply_quote"]
        or first["reply_quote"] == second["reply_quote"]
    )


def _find_duplicate_message_row(
    conn: sqlite3.Connection,
    account_jid: str,
    message: Message,
) -> sqlite3.Row | None:
    duplicate_window = (
        OUTGOING_MESSAGE_DUPLICATE_WINDOW_SECONDS
        if message.outgoing
        else MESSAGE_DUPLICATE_WINDOW_SECONDS
    )
    message_timestamp = _datetime_timestamp(message.sent_at)
    if message_timestamp is None:
        return None

    rows = conn.execute(
        """
        SELECT message_key, message_id, sent_at
        FROM messages
        WHERE account_jid = ?
            AND chat_jid = ?
            AND (? = 1 OR sender_jid = ?)
            AND body = ?
            AND outgoing = ?
            AND audio_url = ?
            AND media_url = ?
            AND media_kind = ?
            AND is_sticker = ?
            AND is_forwarded = ?
            AND (? = '' OR reply_quote = '' OR reply_quote = ?)
            AND (? != '' OR message_id != '')
        """,
        (
            account_jid,
            message.chat_jid,
            int(message.outgoing),
            message.sender_jid,
            message.body,
            int(message.outgoing),
            message.audio_url,
            message.media_url,
            message.media_kind,
            int(message.is_sticker),
            int(message.is_forwarded),
            message.reply_quote,
            message.reply_quote,
            message.message_id,
        ),
    ).fetchall()

    closest_row: sqlite3.Row | None = None
    closest_delta = duplicate_window + 1.0
    for row in rows:
        row_sent_at = _datetime_from_db(row["sent_at"])
        if row_sent_at is None:
            continue
        row_timestamp = _datetime_timestamp(row_sent_at)
        if row_timestamp is None:
            continue
        row_window = (
            MESSAGE_DUPLICATE_WINDOW_SECONDS
            if row["message_id"] and message.message_id
            else duplicate_window
        )
        delta = abs(row_timestamp - message_timestamp)
        if delta <= row_window and delta < closest_delta:
            closest_row = row
            closest_delta = delta

    return closest_row


def _message_key(message: Message) -> str:
    if message.message_id:
        return f"id:{message.message_id}"

    payload_parts = (
        message.sent_at.isoformat(),
        message.sender_jid,
        message.body,
        str(message.outgoing),
        message.audio_url,
        message.media_url,
        message.media_kind,
        message.reply_quote,
    )
    feature_parts = (
        "sticker" if message.is_sticker else "",
        "forwarded" if message.is_forwarded else "",
    )
    payload = (
        "|".join(payload_parts + feature_parts)
        if any(feature_parts)
        else "|".join(payload_parts)
    )
    return f"hash:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _message_preview(message: Message) -> str:
    if message.retracted:
        return "Eliminaste este mensaje" if message.outgoing else "Este mensaje fue eliminado"

    if message.is_sticker:
        preview = "Sticker"
    elif not message.media_url:
        preview = message.body
    elif is_link_preview(message):
        preview = link_description(message)
    elif message.media_kind == "audio":
        if message.media_duration_seconds > 0:
            preview = f"voz, {_format_duration(message.media_duration_seconds)}"
        else:
            preview = "voz"
    else:
        label = {
            "image": "foto",
            "video": "video",
            "file": "archivo",
        }.get(message.media_kind, "archivo")
        details = [label]
        if message.media_kind == "audio" and message.media_duration_seconds > 0:
            details.append(_format_duration(message.media_duration_seconds))
        if message.media_filename:
            details.append(message.media_filename)
        if message.media_size > 0:
            details.append(_format_size(message.media_size))
        preview = ", ".join(details)

    if message.is_forwarded:
        preview = f"Reenviado. {preview}"

    if message.outgoing and message.delivery_state in {"delivered", "received"}:
        return f"{preview} | Entregado"
    if message.outgoing and message.delivery_state in {"displayed", "read"}:
        return f"{preview} | Leído"
    if message.outgoing and message.delivery_state == "pending":
        return f"{preview} | Enviando"
    if message.outgoing and message.delivery_state == "failed":
        return f"{preview} | No enviado"
    return preview


def _format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{size} B"


def _format_duration(duration_seconds: float) -> str:
    total_seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    parts: list[str] = []
    if minutes == 1:
        parts.append("1 minuto")
    elif minutes > 1:
        parts.append(f"{minutes} minutos")

    if seconds == 1:
        parts.append("1 segundo")
    elif seconds > 1 or not parts:
        parts.append(f"{seconds} segundos")

    return " ".join(parts)


def _message_from_row(row: sqlite3.Row) -> Message:
    media_url = str(row["media_url"] or "")
    media_kind = str(row["media_kind"] or "")
    media_mime = str(row["media_mime"] or "")
    media_filename = str(row["media_filename"] or "")
    is_sticker = bool(row["is_sticker"]) or looks_like_bridge_sticker(
        media_kind=media_kind,
        media_mime=media_mime,
        media_filename=media_filename,
        media_url=media_url,
    )
    return Message(
        chat_jid=str(row["chat_jid"]),
        sender_jid=str(row["sender_jid"]),
        body=str(row["body"] or ""),
        sender_name=str(row["sender_name"] or ""),
        sent_at=_datetime_from_db(row["sent_at"]) or datetime.now(),
        outgoing=bool(row["outgoing"]),
        audio_url=str(row["audio_url"] or ""),
        media_url=media_url,
        media_kind=media_kind,
        media_mime=media_mime,
        media_filename=media_filename,
        media_size=int(row["media_size"] or 0),
        media_duration_seconds=float(row["media_duration_seconds"] or 0),
        media_local_path=str(row["media_local_path"] or ""),
        is_sticker=is_sticker,
        is_forwarded=bool(row["is_forwarded"]),
        message_id=str(row["message_id"] or ""),
        displayed_marker_id=str(row["displayed_marker_id"] or ""),
        chat_is_group=bool(row["chat_is_group"]),
        starred=bool(row["starred"]),
        reactions=tuple(json.loads(str(row["reactions_json"] or "[]"))),
        reply_quote=str(row["reply_quote"] or ""),
        reply_to_jid=str(row["reply_to_jid"] or ""),
        reply_to_id=str(row["reply_to_id"] or ""),
        retracted=bool(row["retracted"]),
        edited=bool(row["edited"]),
        delivery_state=str(row["delivery_state"] or ""),
    )


def _datetime_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.astimezone()

    return value.astimezone(UTC).isoformat()


def _datetime_from_db(value: object) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _search_terms(query: str) -> list[str]:
    return [
        term
        for term in _normalize_search_text(query).split()
        if term
    ]


def _normalize_search_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _datetime_timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None

    try:
        return value.timestamp()
    except (OSError, ValueError):
        return None


def _should_replace_summary(new_at: datetime | None, current_at: datetime | None) -> bool:
    if new_at is None:
        return current_at is None

    current_timestamp = _datetime_timestamp(current_at)
    if current_timestamp is None:
        return True

    new_timestamp = _datetime_timestamp(new_at)
    if new_timestamp is None:
        return False

    return new_timestamp >= current_timestamp
