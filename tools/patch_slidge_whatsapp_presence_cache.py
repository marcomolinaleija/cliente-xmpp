from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so contact metadata refreshes preserve a "
            "cached WhatsApp last-seen presence."
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

    source_tree = args.source_tree.resolve()
    contact_path = source_tree / "slidge_whatsapp" / "contact.py"
    if not contact_path.exists():
        raise SystemExit(f"Expected file not found: {contact_path}")

    changed = patch_contact(contact_path, backup=not args.no_backup)
    if changed:
        print("Presence-cache patch applied. Rebuild the bridge image before deployment.")
    else:
        print("Presence-cache patch already present; no files changed.")
    return 0


def patch_contact(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    marker = "self.send_last_presence(force=True, no_cache_online=True)"
    if marker in text:
        return False

    old = "        self.online()\n"
    if text.count(old) != 1:
        raise SystemExit(
            f"Could not apply presence-cache patch to {path}: "
            f"expected one fallback online call, found {text.count(old)}."
        )

    updated = text.replace(
        old,
        f"        {marker}\n",
        1,
    )
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8", newline="\n")
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
