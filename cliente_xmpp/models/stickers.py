from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LibrarySticker:
    id: str
    name: str
    description: str
    path: str
    favorite: bool
    animated: bool
    revision: int
    pack_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class StickerPack:
    id: int
    name: str
    author: str
    count: int
