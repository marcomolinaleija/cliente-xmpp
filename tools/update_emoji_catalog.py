"""Vendor the pinned Emojibase dataset; never run during application startup."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "17.0.0"
LOCALE = "es-mx"
OUTPUT = Path(__file__).resolve().parents[1] / "cliente_xmpp/assets/emojis"


def main() -> None:
    registry_url = f"https://registry.npmjs.org/emojibase-data/{VERSION}"
    with urlopen(registry_url, timeout=30) as response:
        package = json.load(response)
    distribution = package["dist"]
    with urlopen(distribution["tarball"], timeout=30) as response:
        archive_bytes = response.read()
    algorithm, expected = distribution["integrity"].split("-", 1)
    if algorithm != "sha512":
        raise ValueError("Expected the npm SHA-512 integrity record")
    actual = base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode()
    if actual != expected:
        raise ValueError("Emojibase archive integrity mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        def read(name: str) -> bytes:
            member = archive.extractfile("package/" + name)
            if member is None:
                raise ValueError(f"Missing package file: {name}")
            return member.read()

        source = json.loads(read(f"{LOCALE}/data.json"))
        messages = json.loads(read(f"{LOCALE}/messages.json"))
        license_text = read("LICENSE").decode()
    entries = []

    def add(item: dict, inherited_tags: tuple = ()) -> None:
        # Exclude standalone components and regional indicators lacking a category.
        group = item.get("group")
        if group is None or group == 2:
            return
        tags = tuple(dict.fromkeys((*inherited_tags, *item.get("tags", ()))))
        entries.append({
            "emoji": item["emoji"], "label": item["label"],
            "tags": tags, "group": group, "order": item["order"],
        })
        for variant in item.get("skins", ()):
            add(variant, tags)

    for item in source:
        add(item)
    entries.sort(key=lambda item: item["order"])
    payload = {
        "source": "emojibase-data", "version": VERSION, "locale": LOCALE,
        "source_url": registry_url, "archive_integrity": distribution["integrity"],
        "groups": {str(g["order"]): g["message"] for g in messages["groups"] if g["order"] != 2},
        "entries": entries,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "catalog.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8",
    )
    (OUTPUT / "LICENSE.txt").write_text(license_text, encoding="utf-8")
    (OUTPUT / "README.txt").write_text(
        f"Emojibase data {VERSION}, locale {LOCALE}.\n"
        "https://github.com/milesj/emojibase\n"
        "Names and keywords originate from Unicode CLDR.\n"
        "https://cldr.unicode.org/\n"
        "Regenerate explicitly with: python tools/update_emoji_catalog.py\n"
        "The generator verifies npm SHA-512 and preserves emoji sequences.\n",
        encoding="utf-8",
    )
    print(f"Vendored Emojibase {VERSION}: {len(entries)} emoji sequences ({LOCALE})")


if __name__ == "__main__":
    main()
