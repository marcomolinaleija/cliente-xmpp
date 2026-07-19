from __future__ import annotations

import argparse
from pathlib import Path

SYNTHETIC_LAST_SEEN = "            contact.online(last_seen=datetime.now())\n"
EXPECTED_SYNTHETIC_UPDATES = 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so only WhatsApp presence events update "
            "a contact's last-seen timestamp."
        )
    )
    parser.add_argument(
        "source_tree",
        type=Path,
        help="Path containing the installed or source slidge_whatsapp package.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files before modifying sources.",
    )
    args = parser.parse_args()

    session_path = args.source_tree.resolve() / "slidge_whatsapp" / "session.py"
    if not session_path.is_file():
        raise SystemExit(f"Expected file not found: {session_path}")

    changed = patch_session(session_path, backup=not args.no_backup)
    if changed:
        print("Presence-source patch applied. Rebuild the bridge image.")
    else:
        print("Presence-source patch already present; no files changed.")
    return 0


def patch_session(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    synthetic_count = text.count(SYNTHETIC_LAST_SEEN)
    required_blocks_present = (
        "contact.composing(" in text
        and "contact.paused()" in text
        and "contact.displayed(legacy_msg_id=message_id, carbon=receipt.Actor.IsMe)"
        in text
    )
    if synthetic_count == 0 and required_blocks_present:
        return False
    if synthetic_count != EXPECTED_SYNTHETIC_UPDATES or not required_blocks_present:
        raise SystemExit(
            f"Could not patch {path}: expected {EXPECTED_SYNTHETIC_UPDATES} "
            f"synthetic last-seen updates and the chat-state/read-receipt blocks, "
            f"found updates={synthetic_count}, blocks={required_blocks_present}."
        )

    updated = text.replace(SYNTHETIC_LAST_SEEN, "")

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-presence-sources")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
