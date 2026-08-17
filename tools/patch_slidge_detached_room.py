from __future__ import annotations

import argparse
import shutil
from pathlib import Path

MARKER = "Attaching an anonymous participant must keep the merged room usable"


def patch_room(source: str) -> str:
    if MARKER in source:
        return source

    old = """        if self.stored.id is not None:
            with self.xmpp.store.session() as orm:
                self.stored = orm.merge(self.stored)
                stored = (
"""
    new = f"""        if self.stored.id is not None:
            # {MARKER} after commit.
            with self.xmpp.store.session(expire_on_commit=False) as orm:
                self.stored = orm.merge(self.stored)
                stored = (
"""
    count = source.count(old)
    if count != 1:
        raise SystemExit(
            "Could not apply detached-room patch: expected one participant lookup "
            f"session, found {count}."
        )
    return source.replace(old, new, 1)


def patch_package(site_packages: Path, backup: bool = True) -> bool:
    path = site_packages / "slidge/group/room.py"
    source = path.read_text(encoding="utf-8")
    updated = patch_room(source)
    if updated == source:
        return False
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".detached-room.bak"))
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prevent Slidge rooms from expiring while linking participants."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.site_packages, not args.no_backup)
    print(
        "Detached-room patch applied."
        if changed
        else "Detached-room patch already applied."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
