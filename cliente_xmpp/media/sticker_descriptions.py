from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cliente_xmpp.storage.sticker_library import StickerLibrary


def generic_sticker_name(name: str) -> bool:
    return re.fullmatch(r"(?:sticker\s*\d+|\d+|[0-9a-f]{32,64})", name.strip(), re.I) is not None


@dataclass(frozen=True)
class PackDescriptionResult:
    updated: int
    failed: int
    cancelled: bool


def describe_pack(
    library: StickerLibrary,
    pack_id: int,
    describe: Callable[[Path], tuple[str, str] | None],
    *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda done, total: None,
) -> PackDescriptionResult:
    """Run serially after consent, off wx; never overwrite existing alternative text."""
    entries = library.list_stickers(pack_id=pack_id)
    updated = failed = 0
    for index, entry in enumerate(entries, 1):
        if cancelled():
            return PackDescriptionResult(updated, failed, True)
        try:
            generic = generic_sticker_name(entry.name)
            if generic or not entry.description:
                name, description = "", entry.description
                if not description:
                    generated = describe(Path(entry.path))
                    if generated is None:
                        raise ValueError("No description returned")
                    name, description = generated
                    if not description.strip():
                        raise ValueError("Empty description")
                if cancelled():
                    return PackDescriptionResult(updated, failed, True)
                if generic and not name.strip():
                    name = description.split(". ", 1)[0].strip()
                    name = name[:79].rstrip() + "…" if len(name) > 80 else name
                library.edit(
                    entry.id, name=name if generic else None,
                    description=description if not entry.description else None,
                    expected_revision=entry.revision,
                )
                updated += 1
        except Exception:
            # Keep the imported asset and any concurrent manual edit; proceed to the next.
            failed += 1
        progress(index, len(entries))
    return PackDescriptionResult(updated, failed, False)
