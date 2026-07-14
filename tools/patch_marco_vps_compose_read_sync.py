from __future__ import annotations

import argparse
import re
from pathlib import Path


def replace_service_image(text: str, service: str, image: str) -> str:
    pattern = re.compile(
        rf"(?m)^(  {re.escape(service)}:\n(?:    .*\n)*?    image: )\S+$"
    )
    updated, count = pattern.subn(rf"\g<1>{image}", text, count=1)
    if count != 1:
        raise SystemExit(f"Could not find a unique image for service {service!r}.")
    return updated


def patch_compose(
    path: Path,
    *,
    prosody_image: str,
    bridge_image: str | None,
    backup: bool,
) -> bool:
    if not path.is_file():
        raise SystemExit(f"Compose file not found: {path}")
    text = path.read_text(encoding="utf-8")
    updated = replace_service_image(text, "prosody", prosody_image)
    if bridge_image is not None:
        updated = replace_service_image(updated, "slidge-whatsapp", bridge_image)
    if updated == text:
        return False
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-read-sync")
        if not backup_path.exists():
            backup_path.write_text(text, encoding="utf-8")
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select the validated Prosody and bridge images in compose.yml."
    )
    parser.add_argument("compose_file", type=Path)
    parser.add_argument("--prosody-image", default="prosodyim/prosody:0.12")
    parser.add_argument("--bridge-image")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_compose(
        args.compose_file.resolve(),
        prosody_image=args.prosody_image,
        bridge_image=args.bridge_image,
        backup=not args.no_backup,
    )
    print("Compose images updated." if changed else "Compose images already selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
