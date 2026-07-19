from __future__ import annotations

import argparse
from pathlib import Path

SYNTHETIC_MESSAGE_PRESENCE = "        actor.online(last_seen=datetime.now())\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so handling a message does not fabricate "
            "a last-seen timestamp for its resolved actor."
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
        print("Message-presence patch applied. Rebuild the bridge image.")
    else:
        print("Message-presence patch already present; no files changed.")
    return 0


def patch_session(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    synthetic_count = text.count(SYNTHETIC_MESSAGE_PRESENCE)
    message_flow_present = (
        "async def on_wa_message(" in text
        and "message.Chat, message.Actor" in text
        and "match message.Kind:" in text
    )
    if synthetic_count == 0 and message_flow_present:
        return False
    if synthetic_count != 1 or not message_flow_present:
        raise SystemExit(
            f"Could not patch {path}: expected one synthetic message presence "
            f"and the WhatsApp message flow, found updates={synthetic_count}, "
            f"flow={message_flow_present}."
        )

    updated = text.replace(SYNTHETIC_MESSAGE_PRESENCE, "", 1)
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-message-presence")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
