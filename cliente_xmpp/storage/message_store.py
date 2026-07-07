from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.models.chat import Chat, Message

DATABASE_PATH = APP_DIR / "messages.sqlite3"
SCHEMA_VERSION = 5


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
                ORDER BY sent_at DESC, rowid DESC
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
                        ORDER BY latest.sent_at DESC, latest.rowid DESC
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
                    media_filename = COALESCE(NULLIF(?, ''), media_filename)
                WHERE account_jid = ? AND chat_jid = ? AND message_key = ?
                """,
                (
                    message.media_local_path,
                    message.media_size,
                    message.media_duration_seconds,
                    message.media_mime,
                    message.media_filename,
                    account_jid,
                    message.chat_jid,
                    _message_key(message),
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
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    account_jid TEXT NOT NULL,
                    jid TEXT NOT NULL,
                    name TEXT NOT NULL,
                    custom_name TEXT NOT NULL DEFAULT '',
                    is_group INTEGER NOT NULL DEFAULT 0,
                    notifications_muted INTEGER NOT NULL DEFAULT 0,
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
                    sender_jid TEXT NOT NULL,
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
                    starred INTEGER NOT NULL DEFAULT 0,
                    reactions_json TEXT NOT NULL DEFAULT '[]',
                    reply_quote TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (account_jid, chat_jid, message_key)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_chat_sent
                ON messages (account_jid, chat_jid, sent_at);
                """
            )
            self._ensure_message_columns(conn)
            self._ensure_chat_columns(conn)

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
            "media_url": "TEXT NOT NULL DEFAULT ''",
            "media_kind": "TEXT NOT NULL DEFAULT ''",
            "media_mime": "TEXT NOT NULL DEFAULT ''",
            "media_filename": "TEXT NOT NULL DEFAULT ''",
            "media_size": "INTEGER NOT NULL DEFAULT 0",
            "media_duration_seconds": "REAL NOT NULL DEFAULT 0",
            "media_local_path": "TEXT NOT NULL DEFAULT ''",
            "reply_quote": "TEXT NOT NULL DEFAULT ''",
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

    def _upsert_chat(self, conn: sqlite3.Connection, account_jid: str, chat: Chat) -> None:
        now = _datetime_to_db(datetime.now())
        display_name = chat.custom_name or chat.name
        last_message_preview = chat.last_message_preview
        last_message_at = chat.last_message_at
        existing = conn.execute(
            """
            SELECT last_message_preview, last_message_at, custom_name, is_group,
                notifications_muted
            FROM chats
            WHERE account_jid = ? AND jid = ?
            """,
            (account_jid, chat.jid),
        ).fetchone()
        if existing:
            existing_preview = str(existing["last_message_preview"] or "")
            existing_at = _datetime_from_db(existing["last_message_at"])
            if not _should_replace_summary(last_message_at, existing_at):
                last_message_preview = existing_preview
                last_message_at = existing_at
            elif not last_message_preview:
                last_message_preview = existing_preview
            is_group = chat.is_group or bool(existing["is_group"])
            notifications_muted = chat.notifications_muted or bool(
                existing["notifications_muted"]
            )
        else:
            is_group = chat.is_group
            notifications_muted = chat.notifications_muted

        conn.execute(
            """
            INSERT INTO chats (
                account_jid, jid, name, custom_name, is_group, notifications_muted,
                unread_count, last_message_preview, last_message_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_jid, jid) DO UPDATE SET
                name = excluded.name,
                custom_name = COALESCE(NULLIF(excluded.custom_name, ''), chats.custom_name),
                is_group = CASE
                    WHEN excluded.is_group = 1 OR chats.is_group = 1 THEN 1
                    ELSE 0
                END,
                notifications_muted = CASE
                    WHEN excluded.notifications_muted = 1 OR chats.notifications_muted = 1 THEN 1
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
        conn.execute(
            """
            INSERT INTO messages (
                account_jid, chat_jid, message_key, message_id, sender_jid, body,
                sent_at, outgoing, audio_url, media_url, media_kind, media_mime,
                media_filename, media_size, media_duration_seconds, media_local_path,
                starred, reactions_json, reply_quote, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_jid, chat_jid, message_key) DO UPDATE SET
                message_id = COALESCE(NULLIF(excluded.message_id, ''), messages.message_id),
                sender_jid = excluded.sender_jid,
                body = excluded.body,
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
                starred = excluded.starred,
                reactions_json = excluded.reactions_json,
                reply_quote = COALESCE(NULLIF(excluded.reply_quote, ''), messages.reply_quote)
            """,
            (
                account_jid,
                message.chat_jid,
                _message_key(message),
                message.message_id,
                message.sender_jid,
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
                int(message.starred),
                json.dumps(list(message.reactions), ensure_ascii=False),
                message.reply_quote,
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
            SELECT name, unread_count, last_message_at, is_group, notifications_muted
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
            ORDER BY sent_at DESC, rowid DESC
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
        conn.execute(
            """
            INSERT INTO chats (
                account_jid, jid, name, is_group, notifications_muted, unread_count,
                last_message_preview, last_message_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_jid, jid) DO UPDATE SET
                name = chats.name,
                is_group = CASE
                    WHEN excluded.is_group = 1 OR chats.is_group = 1 THEN 1
                    ELSE 0
                END,
                notifications_muted = chats.notifications_muted,
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
                unread_count,
                _message_preview(latest_message),
                _datetime_to_db(latest_message.sent_at),
                now,
            ),
        )


def _message_key(message: Message) -> str:
    if message.message_id:
        return f"id:{message.message_id}"

    payload = "|".join(
        (
            message.sent_at.isoformat(),
            message.sender_jid,
            message.body,
            str(message.outgoing),
            message.audio_url,
            message.media_url,
            message.reply_quote,
        )
    )
    return f"hash:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _message_preview(message: Message) -> str:
    if not message.media_url:
        return message.body

    if message.media_kind == "audio":
        if message.media_duration_seconds > 0:
            return f"voz, {_format_duration(message.media_duration_seconds)}"

        return "voz"

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
    return ", ".join(details)


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
    return Message(
        chat_jid=str(row["chat_jid"]),
        sender_jid=str(row["sender_jid"]),
        body=str(row["body"] or ""),
        sent_at=_datetime_from_db(row["sent_at"]) or datetime.now(),
        outgoing=bool(row["outgoing"]),
        audio_url=str(row["audio_url"] or ""),
        media_url=str(row["media_url"] or ""),
        media_kind=str(row["media_kind"] or ""),
        media_mime=str(row["media_mime"] or ""),
        media_filename=str(row["media_filename"] or ""),
        media_size=int(row["media_size"] or 0),
        media_duration_seconds=float(row["media_duration_seconds"] or 0),
        media_local_path=str(row["media_local_path"] or ""),
        message_id=str(row["message_id"] or ""),
        starred=bool(row["starred"]),
        reactions=tuple(json.loads(str(row["reactions_json"] or "[]"))),
        reply_quote=str(row["reply_quote"] or ""),
    )


def _datetime_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def _datetime_from_db(value: object) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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
