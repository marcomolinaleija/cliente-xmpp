from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so incomplete presence events preserve the "
            "last known last-seen timestamp."
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

    contact_path = args.source_tree.resolve() / "slidge_whatsapp" / "contact.py"
    if not contact_path.is_file():
        raise SystemExit(f"Expected file not found: {contact_path}")

    changed = patch_contact(contact_path, backup=not args.no_backup)
    if changed:
        print("Presence last-seen patch applied. Rebuild the bridge image.")
    else:
        print("Presence last-seen patch already present; no files changed.")
    return 0


def patch_contact(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    marker = "cached_presence = self._get_last_presence()"
    if marker in text:
        return False

    old = """        last_seen = (
            datetime.fromtimestamp(last_seen_timestamp, tz=UTC)
            if last_seen_timestamp > 0
            else None
        )
"""
    new = """        if last_seen_timestamp > 0:
            last_seen = datetime.fromtimestamp(last_seen_timestamp, tz=UTC)
        else:
            cached_presence = self._get_last_presence()
            last_seen = (
                cached_presence.last_seen if cached_presence is not None else None
            )
"""
    if text.count(old) != 1:
        raise SystemExit(
            f"Could not patch {path}: expected one last-seen block, "
            f"found {text.count(old)}."
        )

    updated = text.replace(old, new, 1)
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
