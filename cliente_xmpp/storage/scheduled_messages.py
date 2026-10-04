from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR


class ScheduledMessageStore:
    """Separate outbox; no changes to the conversation database or its migrations."""

    def __init__(self, path: Path = APP_DIR / "assistant-outbox.sqlite3") -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS assistant_outbox ("
                "id TEXT PRIMARY KEY, request_id TEXT NOT NULL, recipient_index INTEGER NOT NULL, "
                "account TEXT NOT NULL, jid TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, "
                "due REAL NOT NULL, late_policy TEXT NOT NULL, state TEXT NOT NULL, "
                "detail TEXT NOT NULL, "
                "UNIQUE(request_id, recipient_index))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS assistant_outbox_due "
                "ON assistant_outbox(account, state, due)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(assistant_outbox)")}
            for name, definition in {
                "is_group": "INTEGER NOT NULL DEFAULT 0",
                "rule_id": "TEXT NOT NULL DEFAULT ''",
                "trigger_seq": "INTEGER NOT NULL DEFAULT 0",
                "auto_expires": "REAL NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE assistant_outbox ADD COLUMN {name} {definition}"
                    )
            # Never repeat a send whose acknowledgement was lost during a process restart.
            connection.execute(
                "UPDATE assistant_outbox SET state='uncertain', detail=? WHERE state='dispatching'",
                ("El cliente se cerró durante el envío; comprueba el chat antes de repetir.",),
            )
            connection.execute(
                "UPDATE assistant_outbox SET state='held', detail=? "
                "WHERE rule_id<>'' AND state='pending'",
                ("Automatización detenida al reiniciar el cliente; reactívala desde Atajos.",),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self,
        request_id: str,
        account: str,
        messages: list[dict[str, str]],
        due: float,
        late_policy: str,
    ) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM assistant_outbox WHERE request_id=? ORDER BY recipient_index",
                (request_id,),
            ).fetchall()
            if old:
                same = len(old) == len(messages) and all(
                    row["account"] == account
                    and row["jid"] == message["jid"]
                    and row["body"] == message["text"]
                    and row["due"] == due
                    and row["late_policy"] == late_policy
                    and bool(row["is_group"]) == bool(message.get("is_group", False))
                    for row, message in zip(old, messages, strict=True)
                )
                if not same:
                    raise ValueError("El identificador ya corresponde a otra solicitud.")
                return [dict(row) for row in old]
            count = connection.execute(
                "SELECT COUNT(*) FROM assistant_outbox "
                "WHERE state IN ('pending','held','dispatching')"
            ).fetchone()[0]
            if count + len(messages) > 500:
                raise ValueError("Hay demasiados mensajes pendientes; revisa o cancela algunos.")
            for index, message in enumerate(messages):
                connection.execute(
                    "INSERT INTO assistant_outbox "
                    "(id,request_id,recipient_index,account,jid,name,body,due,late_policy,is_group,"
                    "state,detail) VALUES (?,?,?,?,?,?,?,?,?,?,'pending','')",
                    (
                        str(uuid.uuid4()),
                        request_id,
                        index,
                        account,
                        message["jid"],
                        message["name"],
                        message["text"],
                        due,
                        late_policy,
                        int(bool(message.get("is_group", False))),
                    ),
                )
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM assistant_outbox WHERE request_id=? ORDER BY recipient_index",
                    (request_id,),
                )
            ]

    def list(self, account: str, state: str = "all", offset: int = 0) -> tuple[list, int]:
        where = "account=?"
        args: list[object] = [account]
        if state != "all":
            where += " AND state=?"
            args.append(state)
        with closing(self._connect()) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM assistant_outbox WHERE {where}", args
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM assistant_outbox WHERE {where} ORDER BY due,id LIMIT 50 OFFSET ?",
                [*args, offset],
            )
            return [dict(row) for row in rows], total

    def due(self, now: float, account: str) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM assistant_outbox WHERE state='pending' AND due<=? AND account=? "
                    "ORDER BY due,id LIMIT 20",
                    (now, account),
                )
            ]

    def transition(self, item_id: str, expected: str, state: str, detail: str = "") -> bool:
        with closing(self._connect()) as connection, connection:
            return (
                connection.execute(
                    "UPDATE assistant_outbox SET state=?,detail=? WHERE id=? AND state=?",
                    (state, detail, item_id, expected),
                ).rowcount
                == 1
            )

    def cancel(self, item_id: str, account: str) -> bool:
        with closing(self._connect()) as connection, connection:
            return (
                connection.execute(
                    "UPDATE assistant_outbox SET state='canceled',detail='' "
                    "WHERE id=? AND account=? AND state IN ('pending','held')",
                    (item_id, account),
                ).rowcount
                == 1
            )

    def finish_delivery(self, message_id: str, state: str) -> None:
        prefix = "cliente-xmpp-api-"
        if not message_id.startswith(prefix):
            return
        states = {
            "sent": "submitted",
            "delivered": "delivered",
            "received": "delivered",
            "read": "read",
            "displayed": "read",
            "failed": "failed",
        }
        target = states.get(state)
        if target is None:
            return
        order = {"dispatching": 0, "uncertain": 0, "submitted": 1, "delivered": 2, "read": 3}
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM assistant_outbox WHERE id=?", (message_id[len(prefix) :],)
            ).fetchone()
            if row is None or row["state"] in {"canceled", "held", "pending"}:
                return
            if row["state"] == "failed" and target not in {"delivered", "read"}:
                return
            if target == "failed" and row["state"] in {"delivered", "read"}:
                return
            if target == "failed" or order.get(target, 0) >= order.get(row["state"], 0):
                connection.execute(
                    "UPDATE assistant_outbox SET state=?,detail=? WHERE id=?",
                    (
                        target,
                        "El servicio informó un fallo; no se reintentó."
                        if target == "failed"
                        else "",
                        message_id[len(prefix) :],
                    ),
                )

    @staticmethod
    def public(row: dict[str, object]) -> dict[str, object]:
        return {
            "id": row["id"],
            "request_id": row["request_id"],
            "name": row["name"],
            "text": row["body"],
            "send_at": datetime.fromtimestamp(float(row["due"]), UTC).isoformat(),
            "state": row["state"],
            "detail": row["detail"],
            "late_policy": row["late_policy"],
            "is_group": bool(row["is_group"]),
            "rule_id": row["rule_id"],
        }
