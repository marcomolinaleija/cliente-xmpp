from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets/emojis/catalog.json"
SEARCH_ALIASES = {
    "😂": "risa riendo lol", "🤣": "risa riendo carcajada lol",
    "😆": "risa riendo", "😘": "beso", "🙏": "gracias por favor",
}


def normalize_emoji_search(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    ).strip()


@dataclass(frozen=True, slots=True)
class EmojiEntry:
    value: str
    label: str
    group: int
    search_text: str


@lru_cache(maxsize=1)
def emoji_catalog() -> tuple[tuple[EmojiEntry, ...], tuple[tuple[int, str], ...]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = tuple(
        EmojiEntry(
            item["emoji"], item["label"], item["group"],
            normalize_emoji_search(" ".join((
                item["emoji"], item["label"], *item["tags"],
                SEARCH_ALIASES.get(item["emoji"].replace("\ufe0f", ""), ""),
            ))),
        )
        for item in data["entries"]
    )
    groups = tuple((int(key), label.capitalize()) for key, label in data["groups"].items())
    return entries, groups


def matching_emojis(query: str, entries: tuple[EmojiEntry, ...]) -> list[EmojiEntry]:
    words = normalize_emoji_search(query).split()
    return [entry for entry in entries if all(word in entry.search_text for word in words)]
