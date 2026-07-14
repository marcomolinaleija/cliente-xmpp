from __future__ import annotations

import argparse
from pathlib import Path

PUBSUB_PRIVILEGES = (
    '        ["http://jabber.org/protocol/pubsub"] = "both";\n'
    '        ["http://jabber.org/protocol/pubsub#owner"] = "set";\n'
)
HTTP_FILES_DIR = 'http_files_dir = "/var/lib/prosody/http_upload"\n'


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grant Slidge the Prosody PubSub privileges required for XEP-0490."
    )
    parser.add_argument("prosody_config", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    changed = patch_config(
        args.prosody_config.resolve(),
        backup=not args.no_backup,
    )
    if changed:
        print("Prosody read-sync configuration updated. Validate and restart Prosody.")
    else:
        print("Prosody read-sync configuration already present; no files changed.")
    return 0


def patch_config(path: Path, *, backup: bool) -> bool:
    if not path.is_file():
        raise SystemExit(f"Prosody config not found: {path}")
    text = path.read_text(encoding="utf-8")
    updated = text

    if PUBSUB_PRIVILEGES not in updated:
        anchor = '        ["jabber:iq:roster"] = "both";\n'
        count = updated.count(anchor)
        if count != 1:
            raise SystemExit(
                f"Could not patch {path}: expected one roster privilege, found {count}."
            )
        updated = updated.replace(anchor, anchor + PUBSUB_PRIVILEGES, 1)

    if HTTP_FILES_DIR not in updated:
        anchor = 'plugin_paths = { "/etc/prosody/modules" }\n'
        count = updated.count(anchor)
        if count != 1:
            raise SystemExit(
                f"Could not patch {path}: expected one plugin_paths line, found {count}."
            )
        updated = updated.replace(anchor, anchor + HTTP_FILES_DIR, 1)

    if updated == text:
        return False

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-read-sync")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8")
    path.write_text(
        updated,
        encoding="utf-8",
        newline="\n",
    )
    print(f"patched {path}")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
