from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AliasPair:
    user_account_id: int
    legacy_id: int
    modern_id: int
    legacy_jid: str
    modern_jid: str


def find_alias_pairs(conn: sqlite3.Connection) -> list[AliasPair]:
    rows = conn.execute(
        """
        SELECT
            legacy.user_account_id,
            legacy.id,
            modern.id,
            legacy.jid,
            modern.jid
        FROM contact AS legacy
        JOIN contact AS modern
          ON modern.user_account_id = legacy.user_account_id
         AND modern.jid = substr(legacy.jid, 1, 3) || substr(legacy.jid, 5)
        WHERE legacy.jid LIKE '+521%@%'
          AND length(substr(legacy.jid, 1, instr(legacy.jid, '@') - 1)) = 14
        ORDER BY legacy.user_account_id, legacy.id
        """
    ).fetchall()
    return [AliasPair(*row) for row in rows]


def _merge_contact_metadata(
    conn: sqlite3.Connection, pair: AliasPair
) -> None:
    conn.execute(
        """
        UPDATE contact
        SET
            avatar_id = COALESCE(avatar_id, (SELECT avatar_id FROM contact WHERE id = ?)),
            nick = COALESCE(NULLIF(nick, ''), (SELECT nick FROM contact WHERE id = ?)),
            cached_presence = MAX(
                cached_presence,
                (SELECT cached_presence FROM contact WHERE id = ?)
            ),
            last_seen = COALESCE(
                MAX(last_seen, (SELECT last_seen FROM contact WHERE id = ?)),
                last_seen,
                (SELECT last_seen FROM contact WHERE id = ?)
            ),
            ptype = COALESCE(ptype, (SELECT ptype FROM contact WHERE id = ?)),
            pstatus = COALESCE(NULLIF(pstatus, ''), (SELECT pstatus FROM contact WHERE id = ?)),
            pshow = COALESCE(pshow, (SELECT pshow FROM contact WHERE id = ?)),
            caps_ver = COALESCE(caps_ver, (SELECT caps_ver FROM contact WHERE id = ?)),
            is_friend = MAX(is_friend, (SELECT is_friend FROM contact WHERE id = ?)),
            added_to_roster = MAX(
                added_to_roster,
                (SELECT added_to_roster FROM contact WHERE id = ?)
            ),
            extra_attributes = COALESCE(
                extra_attributes,
                (SELECT extra_attributes FROM contact WHERE id = ?)
            ),
            updated = MAX(updated, (SELECT updated FROM contact WHERE id = ?)),
            vcard = COALESCE(vcard, (SELECT vcard FROM contact WHERE id = ?)),
            vcard_fetched = MAX(vcard_fetched, (SELECT vcard_fetched FROM contact WHERE id = ?))
        WHERE id = ?
        """,
        (pair.legacy_id,) * 15 + (pair.modern_id,),
    )


def migrate_pair(conn: sqlite3.Connection, pair: AliasPair) -> None:
    _merge_contact_metadata(conn, pair)
    for table in ("direct_thread", "direct_msg"):
        conn.execute(
            f"UPDATE {table} SET foreign_key = ? WHERE foreign_key = ?",  # noqa: S608
            (pair.modern_id, pair.legacy_id),
        )
    conn.execute(
        """
        DELETE FROM contact_sent
        WHERE contact_id = ?
          AND msg_id IN (SELECT msg_id FROM contact_sent WHERE contact_id = ?)
        """,
        (pair.legacy_id, pair.modern_id),
    )
    conn.execute(
        "UPDATE contact_sent SET contact_id = ? WHERE contact_id = ?",
        (pair.modern_id, pair.legacy_id),
    )
    conn.execute("DELETE FROM contact WHERE id = ?", (pair.legacy_id,))


def migrate(path: Path, *, apply: bool) -> list[AliasPair]:
    mode = "rwc" if apply else "ro"
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode={mode}", uri=True, timeout=20)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        pairs = find_alias_pairs(conn)
        if not apply:
            return pairs
        with conn:
            for pair in pairs:
                migrate_pair(conn, pair)
        remaining = find_alias_pairs(conn)
        if remaining:
            raise RuntimeError(f"Alias pairs remain after migration: {len(remaining)}")
        return pairs
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge duplicate +521/+52 Slidge contacts when both aliases exist."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only report the number of pairs.",
    )
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        raise SystemExit(f"Database not found: {path}")
    pairs = migrate(path, apply=args.apply)
    action = "Merged" if args.apply else "Would merge"
    print(f"{action} {len(pairs)} Mexican contact alias pair(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
