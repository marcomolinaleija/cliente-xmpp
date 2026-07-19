from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.migrate_slidge_mexico_aliases import migrate

SCHEMA = """
CREATE TABLE contact (
    id INTEGER PRIMARY KEY,
    user_account_id INTEGER NOT NULL,
    legacy_id TEXT NOT NULL,
    jid TEXT NOT NULL,
    avatar_id INTEGER,
    nick TEXT,
    cached_presence INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    ptype TEXT,
    pstatus TEXT,
    pshow TEXT,
    caps_ver TEXT,
    is_friend INTEGER NOT NULL DEFAULT 0,
    added_to_roster INTEGER NOT NULL DEFAULT 0,
    extra_attributes TEXT,
    updated INTEGER NOT NULL DEFAULT 0,
    vcard TEXT,
    vcard_fetched INTEGER NOT NULL DEFAULT 0,
    client_type TEXT NOT NULL DEFAULT 'pc',
    UNIQUE(user_account_id, legacy_id),
    UNIQUE(user_account_id, jid)
);
CREATE TABLE direct_thread (
    id INTEGER PRIMARY KEY,
    foreign_key INTEGER NOT NULL REFERENCES contact(id),
    legacy_id TEXT NOT NULL,
    xmpp_id TEXT NOT NULL
);
CREATE TABLE direct_msg (
    id INTEGER PRIMARY KEY,
    foreign_key INTEGER NOT NULL REFERENCES contact(id),
    legacy_id TEXT NOT NULL,
    xmpp_id TEXT NOT NULL
);
CREATE TABLE contact_sent (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contact(id),
    msg_id TEXT NOT NULL,
    UNIQUE(contact_id, msg_id)
);
"""


class MexicoAliasMigrationTests(unittest.TestCase):
    def test_dry_run_then_merges_metadata_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "slidge.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript(SCHEMA)
            conn.execute(
                """
                INSERT INTO contact (
                    id,user_account_id,legacy_id,jid,nick,cached_presence,last_seen,
                    is_friend,added_to_roster,updated,vcard_fetched
                ) VALUES (1,1,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "5214491234567@s.whatsapp.net",
                    "+5214491234567@whatsapp.example.org",
                    "Nombre",
                    1,
                    "2026-07-18 10:00:00",
                    1,
                    1,
                    1,
                    1,
                ),
            )
            conn.execute(
                """
                INSERT INTO contact (
                    id,user_account_id,legacy_id,jid,nick,cached_presence,
                    is_friend,added_to_roster,updated,vcard_fetched
                ) VALUES (2,1,?,?,?,?,?,?,?,?)
                """,
                (
                    "524491234567@s.whatsapp.net",
                    "+524491234567@whatsapp.example.org",
                    "",
                    0,
                    1,
                    1,
                    1,
                    0,
                ),
            )
            conn.execute(
                "INSERT INTO direct_msg VALUES (1,1,'legacy-message','xmpp-message')"
            )
            conn.execute("INSERT INTO contact_sent VALUES (1,1,'same')")
            conn.execute("INSERT INTO contact_sent VALUES (2,2,'same')")
            conn.commit()
            conn.close()

            self.assertEqual(len(migrate(path, apply=False)), 1)
            self.assertEqual(len(migrate(path, apply=True)), 1)

            conn = sqlite3.connect(path)
            contact_count = conn.execute("SELECT count(*) FROM contact").fetchone()[0]
            contact = conn.execute(
                "SELECT id,nick,cached_presence,last_seen FROM contact"
            ).fetchone()
            message_contact = conn.execute(
                "SELECT foreign_key FROM direct_msg"
            ).fetchone()[0]
            sent_count = conn.execute("SELECT count(*) FROM contact_sent").fetchone()[0]
            conn.close()

            self.assertEqual(contact_count, 1)
            self.assertEqual(contact, (2, "Nombre", 1, "2026-07-18 10:00:00"))
            self.assertEqual(message_contact, 2)
            self.assertEqual(sent_count, 1)


if __name__ == "__main__":
    unittest.main()
