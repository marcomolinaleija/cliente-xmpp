from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def _encoded_size(item: dict) -> int:
    # Reserve space for .NET's HTML-safe escaping and the nested Gemini tool result.
    encoded = json.dumps(item, ensure_ascii=True)
    return len(encoded) + sum(encoded.count(char) * 5 for char in "<>&'") + encoded.count('\\"') * 4


class ConversationContextStore:
    """Read-only, account-scoped pages from the existing local conversation cache."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def read_page(
        self,
        account: str,
        jid: str,
        count: int,
        before: tuple[float, int] | None = None,
        anchor: int | None = None,
        *,
        is_group: bool = False,
    ) -> dict:
        if not 1 <= count <= 400:
            raise ValueError("Cantidad de contexto fuera del intervalo.")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            if anchor is None:
                anchor = connection.execute(
                    "SELECT COALESCE(MAX(rowid),0) FROM messages "
                    "WHERE account_jid=? AND chat_jid=?",
                    (account, jid),
                ).fetchone()[0]
            where = (
                "m.account_jid=? AND m.chat_jid=? AND m.rowid<=? AND m.chat_is_group=? "
                "AND NOT EXISTS (SELECT 1 FROM deleted_messages d WHERE "
                "d.account_jid=m.account_jid AND d.chat_jid=m.chat_jid AND "
                "(d.message_id=m.message_id OR d.message_id=m.displayed_marker_id))"
            )
            args: list[object] = [account, jid, anchor, int(is_group)]
            total = connection.execute(
                f"SELECT COUNT(*) FROM messages m WHERE {where}", args
            ).fetchone()[0]
            if before is not None:
                where += (
                    " AND (COALESCE(julianday(m.sent_at),0)<? OR "
                    "(COALESCE(julianday(m.sent_at),0)=? AND m.rowid<?))"
                )
                args.extend((before[0], before[0], before[1]))
            available = connection.execute(
                f"SELECT COUNT(*) FROM messages m WHERE {where}", args
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT m.rowid, COALESCE(julianday(m.sent_at),0) AS sort_date, "
                "CASE WHEN m.retracted=1 THEN '' ELSE substr(m.body,1,32000) END AS body, "
                "length(m.body) AS body_length, m.sent_at, m.outgoing, m.retracted, m.media_kind, "
                "m.message_key, m.sender_name "
                f"FROM messages m WHERE {where} ORDER BY sort_date DESC,m.rowid DESC LIMIT ?",
                [*args, count],
            ).fetchall()
        messages = []
        size = 0
        next_before = before
        for row in rows:
            retracted = bool(row["retracted"])
            item = {
                "identity": row["message_key"],
                "sender": ("Tú" if row["outgoing"] else row["sender_name"] or "Participante")
                if is_group
                else "",
                "text": "Mensaje eliminado" if retracted else row["body"],
                "sent_at": row["sent_at"],
                "outgoing": bool(row["outgoing"]),
                "kind": "media" if row["media_kind"] else "text",
                "retracted": retracted,
                "text_truncated": not retracted and row["body_length"] > 32000,
            }
            encoded_size = _encoded_size(item)
            if size + encoded_size > 48000:
                if messages:
                    break
                # An exceptionally long message still gets an explicit, bounded excerpt.
                original = item["text"]
                low, high = 0, len(original)
                item["text_truncated"] = True
                while low < high:
                    middle = (low + high + 1) // 2
                    item["text"] = original[:middle]
                    if _encoded_size(item) <= 48000:
                        low = middle
                    else:
                        high = middle - 1
                item["text"] = original[:low]
                encoded_size = _encoded_size(item)
            messages.append(item)
            size += encoded_size
            next_before = (float(row["sort_date"]), int(row["rowid"]))
        has_more = available > len(messages)
        return {
            "messages": list(reversed(messages)),
            "local_total": total,
            "returned_count": len(messages),
            "requested_count": count,
            "has_more": has_more,
            "page_limited": len(messages) < min(count, available),
            "next_before": next_before if has_more else None,
            "anchor": anchor,
        }
