from __future__ import annotations

import argparse
import sqlite3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report aggregate contact-name consistency without exposing contacts."
    )
    parser.add_argument("slidge_database")
    parser.add_argument("whatsapp_database")
    parser.add_argument(
        "--require-consistent",
        action="store_true",
        help="Fail if a saved WhatsApp full name is not reflected in Slidge.",
    )
    args = parser.parse_args()

    slidge_uri = f"file:{args.slidge_database}?mode=ro"
    whatsapp_uri = f"file:{args.whatsapp_database}?mode=ro"
    connection = sqlite3.connect(slidge_uri, uri=True)
    connection.execute("ATTACH DATABASE ? AS whatsapp", (whatsapp_uri,))

    joined, saved_names, inconsistent = connection.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN COALESCE(w.full_name, '') <> '' THEN 1 ELSE 0 END),
            SUM(
                CASE
                    WHEN COALESCE(w.full_name, '') <> ''
                     AND COALESCE(c.nick, '') <> w.full_name
                    THEN 1
                    ELSE 0
                END
            )
        FROM contact AS c
        JOIN whatsapp.whatsmeow_contacts AS w
          ON w.their_jid = c.legacy_id
        """
    ).fetchone()

    print(f"joined contacts: {joined}")
    print(f"contacts with a saved WhatsApp name: {saved_names or 0}")
    print(f"saved-name mismatches: {inconsistent or 0}")

    mismatch_characteristics = connection.execute(
        """
        SELECT
            SUM(CASE WHEN c.nick = w.push_name THEN 1 ELSE 0 END),
            SUM(CASE WHEN c.is_friend THEN 1 ELSE 0 END),
            COUNT(DISTINCT c.user_account_id)
        FROM contact AS c
        JOIN whatsapp.whatsmeow_contacts AS w
          ON w.their_jid = c.legacy_id
        WHERE COALESCE(w.full_name, '') <> ''
          AND COALESCE(c.nick, '') <> w.full_name
        """
    ).fetchone()
    push_name_matches, friend_rows, affected_accounts = mismatch_characteristics
    print(f"mismatches currently using the profile name: {push_name_matches or 0}")
    print(f"mismatches marked as saved contacts: {friend_rows or 0}")
    print(f"accounts with mismatches: {affected_accounts or 0}")

    device_rows = connection.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN COALESCE(w.full_name, '') <> '' THEN 1 ELSE 0 END),
            SUM(
                CASE
                    WHEN COALESCE(w.full_name, '') <> ''
                     AND COALESCE(c.nick, '') <> w.full_name
                    THEN 1
                    ELSE 0
                END
            )
        FROM whatsapp.whatsmeow_contacts AS w
        JOIN contact AS c
          ON c.legacy_id = w.their_jid
        GROUP BY w.our_jid
        ORDER BY COUNT(*) DESC, SUM(
            CASE
                WHEN COALESCE(w.full_name, '') <> ''
                 AND COALESCE(c.nick, '') <> w.full_name
                THEN 1
                ELSE 0
            END
        ) ASC
        """
    ).fetchall()
    print(f"WhatsApp device contact sets: {len(device_rows)}")
    for index, (rows, saved, mismatches) in enumerate(device_rows, start=1):
        print(
            f"device set {index}: joined={rows}, saved={saved or 0}, "
            f"mismatches={mismatches or 0}"
        )

    account_rows = connection.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN COALESCE(w.full_name, '') <> '' THEN 1 ELSE 0 END),
            SUM(
                CASE
                    WHEN COALESCE(w.full_name, '') <> ''
                     AND COALESCE(c.nick, '') <> w.full_name
                    THEN 1
                    ELSE 0
                END
            )
        FROM contact AS c
        JOIN whatsapp.whatsmeow_contacts AS w
          ON w.their_jid = c.legacy_id
        GROUP BY c.user_account_id, w.our_jid
        ORDER BY COUNT(*) DESC, SUM(
            CASE
                WHEN COALESCE(w.full_name, '') <> ''
                 AND COALESCE(c.nick, '') <> w.full_name
                THEN 1
                ELSE 0
            END
        ) ASC
        """
    ).fetchall()
    print(f"Slidge account contact sets: {len(account_rows)}")
    for index, (rows, saved, mismatches) in enumerate(account_rows, start=1):
        print(
            f"account set {index}: joined={rows}, saved={saved or 0}, "
            f"mismatches={mismatches or 0}"
        )

    device_inventory = connection.execute(
        """
        SELECT
            COUNT(w.their_jid),
            SUM(CASE WHEN COALESCE(w.full_name, '') <> '' THEN 1 ELSE 0 END)
        FROM whatsapp.whatsmeow_device AS d
        LEFT JOIN whatsapp.whatsmeow_contacts AS w
          ON w.our_jid = d.jid
        GROUP BY d.jid
        ORDER BY COUNT(w.their_jid) DESC
        """
    ).fetchall()
    print(f"WhatsApp registered devices: {len(device_inventory)}")
    for index, (contacts, saved) in enumerate(device_inventory, start=1):
        print(
            f"registered device {index}: contacts={contacts}, saved={saved or 0}"
        )

    mexico_alias_mismatches = connection.execute(
        """
        SELECT COUNT(*)
        FROM contact AS c
        JOIN whatsapp.whatsmeow_contacts AS modern
          ON modern.their_jid = c.legacy_id
        WHERE COALESCE(modern.full_name, '') <> ''
          AND COALESCE(c.nick, '') <> modern.full_name
          AND c.legacy_id LIKE '52%@s.whatsapp.net'
          AND LENGTH(SUBSTR(c.legacy_id, 1, INSTR(c.legacy_id, '@') - 1)) = 12
          AND EXISTS (
              SELECT 1
              FROM whatsapp.whatsmeow_contacts AS legacy
              WHERE legacy.our_jid = modern.our_jid
                AND legacy.their_jid = (
                    '521' || SUBSTR(c.legacy_id, 3)
                )
          )
        """
    ).fetchone()[0]
    print(f"mismatches with a Mexican legacy alias: {mexico_alias_mismatches}")
    if args.require_consistent and inconsistent:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
