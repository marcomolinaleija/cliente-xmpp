from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.media.downloads import DOWNLOADS_DIR, download_media
from cliente_xmpp.media.links import is_link_preview
from cliente_xmpp.models.chat import Message

MAXIMUM_BYTES = 100 * 1024 * 1024


class AssistantMediaStore:
    """Account-scoped received attachments; never exposes paths or network URLs."""

    def __init__(self, path: Path, *, roots: tuple[Path, ...] | None = None) -> None:
        self.path = path.resolve()
        self.roots = roots or (DOWNLOADS_DIR, APP_DIR / "clipboard")

    @staticmethod
    def _where() -> str:
        return (
            "m.account_jid=? AND m.outgoing=0 AND m.retracted=0 "
            "AND m.media_kind IN ('image','audio','video') "
            "AND m.is_sticker=0 "
            "AND NOT EXISTS (SELECT 1 FROM deleted_messages d WHERE "
            "d.account_jid=m.account_jid AND d.chat_jid=m.chat_jid AND "
            "(d.message_id=m.message_id OR d.message_id=m.displayed_marker_id))"
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> Message:
        return Message(
            chat_jid=row["chat_jid"],
            sender_jid="",
            body=row["body"],
            media_kind=row["media_kind"],
            media_url=row["media_url"],
            audio_url=row["audio_url"],
            media_mime=row["media_mime"],
            media_filename=row["media_filename"],
            media_local_path=row["media_local_path"],
            media_size=row["media_size"],
            message_id=row["message_id"],
            chat_is_group=bool(row["chat_is_group"]),
        )

    def list_received(self, account: str, chats: list[dict], kind: str, count: int) -> list[dict]:
        if kind not in {"image", "audio", "video", "all"} or not 1 <= count <= 10:
            raise ValueError("Tipo o cantidad de adjuntos no válido.")
        records = []
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            # Batches keep SQLite expression depth and bind counts bounded for large accounts.
            for start in range(0, len(chats), 200):
                batch = chats[start : start + 200]
                where = (
                    self._where()
                    + " AND ("
                    + " OR ".join("(m.chat_jid=? AND m.chat_is_group=?)" for _ in batch)
                    + ")"
                )
                args = [account]
                for chat in batch:
                    args.extend((chat["jid"], int(chat["is_group"])))
                if kind != "all":
                    where += " AND m.media_kind=?"
                    args.append(kind)
                rows = db.execute(
                    "SELECT m.chat_jid,m.chat_is_group,m.message_key,m.media_kind,"
                    "m.sent_at,m.media_filename,m.media_size,m.rowid AS media_rowid,"
                    "COALESCE(julianday(m.sent_at),0) AS sort_date "
                    f"FROM messages m WHERE {where} ORDER BY sort_date DESC,m.rowid DESC LIMIT ?",
                    [*args, count],
                ).fetchall()
                for row in rows:
                    records.append(dict(row))
        return sorted(records, key=lambda r: (r["sort_date"], r["media_rowid"]), reverse=True)[
            :count
        ]

    def find(self, account: str, jid: str, group: bool, key: str) -> sqlite3.Row:
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                f"SELECT m.* FROM messages m WHERE {self._where()} "
                "AND m.chat_jid=? AND m.chat_is_group=? AND m.message_key=?",
                (account, jid, int(group), key),
            ).fetchone()
        if row is None or is_link_preview(self._message(row)):
            raise ValueError("El adjunto fue eliminado o dejó de estar disponible.")
        return row

    def read(self, account: str, jid: str, group: bool, key: str) -> tuple[bytes, str]:
        row = self.find(account, jid, group, key)
        message = self._message(row)
        path = Path(message.media_local_path) if message.media_local_path else None
        if path is not None and path.is_file():
            self._validate_path(path)
        else:
            downloaded = download_media(message, account, maximum_bytes=MAXIMUM_BYTES)
            path = downloaded.path
            self._validate_path(path)
            message.media_mime = downloaded.mime or message.media_mime
            # Only enrich a still-present message, never resurrect retracted media.
            try:
                self.find(account, jid, group, key)
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute(
                        "UPDATE messages SET media_local_path=?,media_size=?,"
                        "media_mime=CASE WHEN media_mime='' THEN ? ELSE media_mime END WHERE "
                        "account_jid=? AND chat_jid=? AND chat_is_group=? "
                        "AND message_key=? AND retracted=0",
                        (
                            str(path),
                            downloaded.size,
                            downloaded.mime,
                            account,
                            jid,
                            int(group),
                            key,
                        ),
                    )
                    db.commit()
            except Exception:
                path.unlink(missing_ok=True)
                raise
        if path.stat().st_size > MAXIMUM_BYTES:
            raise ValueError("El adjunto supera 100 MiB.")
        with path.open("rb") as stream:
            data = stream.read(MAXIMUM_BYTES + 1)
        if not data or len(data) > MAXIMUM_BYTES:
            raise ValueError("El adjunto está vacío o supera 100 MiB.")
        self.find(account, jid, group, key)
        return data, message.media_mime

    def _validate_path(self, path: Path) -> None:
        resolved = path.resolve()
        if not any(root.resolve() in resolved.parents for root in self.roots):
            raise ValueError("La copia del adjunto está fuera del almacenamiento administrado.")
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise ValueError("La copia local del adjunto contiene enlaces.")
