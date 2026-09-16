from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OLD = "        await muc.add_to_bookmarks()\n"
NEW = "        await muc.add_to_bookmarks(auto_join=False)\n"


def patch_group(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        return False
    if source.count(OLD) != 1:
        raise SystemExit(f"Could not find the unique bookmark call in {path}.")
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-no-auto-join")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Disable automatic XMPP MUC joining for newly discovered WhatsApp groups."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    target = args.site_packages / "slidge_whatsapp" / "group.py"
    if not target.is_file():
        raise SystemExit(f"File not found: {target}")

    changed = patch_group(target, backup=not args.no_backup)
    print("No-auto-join patch applied." if changed else "No-auto-join patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
