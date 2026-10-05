from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OLD = '''        session.logged = True
        session.send_gateway_status(msg or "Re-connected", show="chat")
        session.send_gateway_message(msg or "Re-connected")
        return msg
'''
NEW = '''        # login_wrap already updates logged; it can also return a failure message.
        if not session.logged:
            return msg
        session.send_gateway_status(msg or "Re-connected", show="chat")
        session.send_gateway_message(msg or "Re-connected")
        return msg
'''


def patch_login(source: str) -> str:
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise SystemExit("Could not patch Slidge re-login: expected one Login.run success block.")
    return source.replace(OLD, NEW, 1)


def patch_package(site_packages: Path, *, backup: bool = True) -> bool:
    path = site_packages / "slidge" / "command" / "user.py"
    source = path.read_text(encoding="utf-8")
    updated = patch_login(source)
    if updated == source:
        return False
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-relogin-state"))
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep Slidge logged out when WhatsApp re-login fails."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print("Re-login state patch applied." if changed else "Re-login state patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
