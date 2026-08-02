from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path


def backup_database(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError(f"Integrity check failed for {destination.name}: {result}")
    finally:
        destination_connection.close()
        source_connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stamp")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", args.stamp):
        raise SystemExit("Invalid backup timestamp.")

    backup_root = (
        Path("/var/lib/slidge/contact-name-test-backups") / args.stamp
    )
    backup_root.mkdir(parents=True, exist_ok=False)
    backup_database(
        Path("/var/lib/slidge/slidge.sqlite"), backup_root / "slidge.sqlite"
    )
    backup_database(
        Path("/var/lib/slidge/whatsapp/whatsapp.db"),
        backup_root / "whatsapp.db",
    )
    print(f"Verified database backup: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
