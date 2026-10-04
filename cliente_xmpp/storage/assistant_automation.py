from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path


class AssistantAutomationStore:
    """Persistent live journal and revocable leases in the assistant outbox database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='assistant_observations'"
            ).fetchone()
            if existing and "UNIQUE(account,jid,identity,kind)" in existing[0].replace(" ", ""):
                # Early local builds deduplicated corrections as well. Keep every
                # correction: a second edit must invalidate a later generation too.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "ALTER TABLE assistant_observations RENAME TO assistant_observations_previous"
                )
                connection.execute(self._observation_schema())
                connection.execute(
                    "INSERT INTO assistant_observations "
                    "SELECT * FROM assistant_observations_previous"
                )
                connection.execute("DROP TABLE assistant_observations_previous")
                connection.commit()
            connection.execute(self._observation_schema())
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS assistant_metadata (key TEXT PRIMARY KEY,value TEXT);"
                "CREATE UNIQUE INDEX IF NOT EXISTS assistant_observation_identity "
                "ON assistant_observations(account,jid,identity,kind) "
                "WHERE kind IN ('incoming','outgoing');"
                "CREATE INDEX IF NOT EXISTS assistant_observations_chat "
                "ON assistant_observations(account,jid,seq);"
                "CREATE TABLE IF NOT EXISTS assistant_leases ("
                "id TEXT PRIMARY KEY,account TEXT NOT NULL,scope TEXT NOT NULL,"
                "selected TEXT NOT NULL,"
                "excluded TEXT NOT NULL,expires REAL NOT NULL,baseline INTEGER NOT NULL);"
                "CREATE UNIQUE INDEX IF NOT EXISTS assistant_reply_trigger "
                "ON assistant_outbox(account,jid,trigger_seq) WHERE rule_id<>'';"
            )
            connection.execute(
                "INSERT OR IGNORE INTO assistant_metadata VALUES ('epoch',?)", (str(uuid.uuid4()),)
            )
            # A new client process never restores a background sender's old permission.
            connection.execute("DELETE FROM assistant_leases")
            self.epoch = connection.execute(
                "SELECT value FROM assistant_metadata WHERE key='epoch'"
            ).fetchone()[0]
        self._file_identity = (path.stat().st_dev, path.stat().st_ino)

    @staticmethod
    def _observation_schema() -> str:
        return (
            "CREATE TABLE IF NOT EXISTS assistant_observations ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,jid TEXT NOT NULL,"
            "identity TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,sender TEXT NOT NULL,"
            "is_group INTEGER NOT NULL,sent_at TEXT NOT NULL,received_at REAL NOT NULL)"
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def check_database(self) -> None:
        info = self.path.stat()
        if (info.st_dev, info.st_ino) != self._file_identity:
            raise sqlite3.DatabaseError("El diario se reemplazó; reinicia la integración.")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM assistant_metadata WHERE key='epoch'"
            ).fetchone()
            if not row or row[0] != self.epoch:
                raise sqlite3.DatabaseError("El diario cambió; reinicia la integración.")

    def observe(self, events: list[dict]) -> None:
        with closing(self._connect()) as connection, connection:
            for event in events:
                connection.execute(
                    "INSERT OR IGNORE INTO assistant_observations "
                    "(account,jid,identity,kind,body,sender,is_group,sent_at,received_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    tuple(
                        event[field]
                        for field in (
                            "account",
                            "jid",
                            "identity",
                            "kind",
                            "body",
                            "sender",
                            "is_group",
                            "sent_at",
                            "received_at",
                        )
                    ),
                )
                if event["kind"] == "cleared":
                    connection.execute(
                        "UPDATE assistant_observations SET body='' WHERE account=? AND jid=?",
                        (event["account"], event["jid"]),
                    )
                elif event["kind"] == "changed":
                    # Corrections invalidate the original trigger even on duplicate delivery.
                    connection.execute(
                        "UPDATE assistant_observations SET body='' WHERE account=? AND jid=? "
                        "AND identity=? AND kind='incoming'",
                        (event["account"], event["jid"], event["identity"]),
                    )

    def latest(self, account: str) -> int:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM assistant_observations WHERE account=?",
                (account,),
            ).fetchone()[0]

    def events(self, account: str, after: int, allowed: set[str], limit: int = 100) -> dict:
        with closing(self._connect()) as connection:
            latest = connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM assistant_observations WHERE account=?",
                (account,),
            ).fetchone()[0]
            if after > latest:
                raise ValueError("El cursor no pertenece al diario vigente.")
            rows = list(
                connection.execute(
                    "SELECT * FROM assistant_observations WHERE account=? AND seq>? "
                    "ORDER BY seq LIMIT ?",
                    (account, after, limit),
                )
            )
        next_after = after if rows else latest
        visible, size = [], 0
        for row in rows:
            if row["jid"] in allowed:
                item = dict(row)
                item_size = len(json.dumps(item, ensure_ascii=True))
                if visible and size + item_size > 480000:
                    break
                visible.append(item)
                size += item_size
            next_after = row["seq"]
        return {
            "events": visible,
            "next_after": next_after,
            "latest": latest,
            "has_more": next_after < latest,
        }

    def lease(
        self,
        rule: str,
        account: str,
        scope: str,
        selected: list[str],
        excluded: list[str],
        expires: float,
        now: float,
    ) -> int:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM assistant_leases WHERE id=?", (rule,)
            ).fetchone()
            encoded_selected, encoded_excluded = json.dumps(selected), json.dumps(excluded)
            if old and old["expires"] <= now:
                raise ValueError("El permiso caducó; revoca y reactiva la regla.")
            if old and (old["account"], old["scope"], old["selected"], old["excluded"]) != (
                account,
                scope,
                encoded_selected,
                encoded_excluded,
            ):
                raise ValueError("Revoca la regla antes de cambiar su alcance.")
            baseline = (
                old["baseline"]
                if old
                else connection.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM assistant_observations WHERE account=?",
                    (account,),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO assistant_leases VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET expires=excluded.expires",
                (rule, account, scope, encoded_selected, encoded_excluded, expires, baseline),
            )
        return baseline

    def revoke(self, rule: str, account: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM assistant_leases WHERE id=? AND account=?", (rule, account)
            )
            connection.execute(
                "UPDATE assistant_outbox SET state='canceled',detail=? "
                "WHERE rule_id=? AND account=? AND state='pending'",
                ("Respuesta cancelada al detener la regla.", rule, account),
            )

    @staticmethod
    def _permitted(
        lease: sqlite3.Row | None, account: str, jid: str, group: bool, now: float
    ) -> bool:
        return bool(
            lease
            and lease["account"] == account
            and lease["expires"] > now
            and jid not in json.loads(lease["excluded"])
            and (
                lease["scope"] == "all"
                or lease["scope"] == "contacts"
                and not group
                or lease["scope"] == "groups"
                and group
                or lease["scope"] == "selected"
                and jid in json.loads(lease["selected"])
            )
        )

    def revoke_all(self, account: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM assistant_leases WHERE account=?", (account,))
            connection.execute(
                "UPDATE assistant_outbox SET state='canceled',detail=? "
                "WHERE account=? AND rule_id<>'' AND state='pending'",
                ("Todas las reglas de esta cuenta se detuvieron.", account),
            )

    def accept_reply(
        self,
        request: str,
        rule: str,
        account: str,
        jid: str,
        name: str,
        group: bool,
        trigger: int,
        text: str,
        now: float,
        expires: float,
    ) -> dict:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM assistant_outbox WHERE request_id=?", (request,)
            ).fetchone()
            if old:
                if (
                    old["account"],
                    old["jid"],
                    old["rule_id"],
                    old["trigger_seq"],
                    old["body"],
                ) != (
                    account,
                    jid,
                    rule,
                    trigger,
                    text,
                ):
                    raise ValueError("La solicitud ya corresponde a otra respuesta.")
                return dict(old)
            lease = connection.execute(
                "SELECT * FROM assistant_leases WHERE id=?", (rule,)
            ).fetchone()
            last = connection.execute(
                "SELECT * FROM assistant_observations WHERE account=? AND jid=? "
                "ORDER BY seq DESC LIMIT 1",
                (account, jid),
            ).fetchone()
            if (
                not self._permitted(lease, account, jid, group, now)
                or not last
                or last["seq"] != trigger
                or last["kind"] != "incoming"
                or not last["body"]
                or trigger <= lease["baseline"]
                or expires <= now
                or last["received_at"] < now - 86400
            ):
                raise ValueError("La respuesta perdió su autorización o el chat cambió.")
            if connection.execute(
                "SELECT 1 FROM assistant_outbox WHERE account=? AND jid=? AND trigger_seq=? "
                "AND rule_id<>''",
                (account, jid, trigger),
            ).fetchone():
                raise ValueError("Ese mensaje ya tiene una respuesta automática registrada.")
            if (
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_outbox WHERE state IN "
                    "('pending','held','dispatching')"
                ).fetchone()[0]
                >= 500
            ):
                raise ValueError("La cola está llena.")
            identifier = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO assistant_outbox (id,request_id,recipient_index,account,jid,name,body,"
                "due,late_policy,state,detail,is_group,rule_id,trigger_seq,auto_expires) "
                "VALUES (?,?,0,?,?,?,?,?,'hold','pending','',?,?,?,?)",
                (
                    identifier,
                    request,
                    account,
                    jid,
                    name,
                    text,
                    now,
                    int(group),
                    rule,
                    trigger,
                    expires,
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM assistant_outbox WHERE id=?", (identifier,)
                ).fetchone()
            )

    def authorized(self, row: dict, now: float) -> bool:
        with closing(self._connect()) as connection:
            lease = connection.execute(
                "SELECT * FROM assistant_leases WHERE id=?", (row["rule_id"],)
            ).fetchone()
            last = connection.execute(
                "SELECT seq,kind,body FROM assistant_observations WHERE account=? AND jid=? "
                "ORDER BY seq DESC LIMIT 1",
                (row["account"], row["jid"]),
            ).fetchone()
            return bool(
                self._permitted(lease, row["account"], row["jid"], row["is_group"], now)
                and row["auto_expires"] > now
                and last
                and last["seq"] == row["trigger_seq"]
                and last["kind"] == "incoming"
                and last["body"]
            )

    def request(self, account: str, request: str) -> list[dict]:
        with closing(self._connect()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM assistant_outbox WHERE account=? AND request_id=?",
                    (account, request),
                )
            ]
