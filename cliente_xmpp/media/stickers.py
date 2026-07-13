from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

_BRIDGE_STICKER_FILENAME = re.compile(
    r"^[0-9a-f]{64}(?: \(\d+\))?\.webp$",
    re.IGNORECASE,
)


def looks_like_bridge_sticker(
    *,
    media_kind: str,
    media_mime: str,
    media_filename: str,
    media_url: str = "",
) -> bool:
    """Recognize converted WhatsApp stickers when XEP-0449 was lost in transit."""
    if media_kind.casefold() != "image":
        return False

    mime = media_mime.partition(";")[0].strip().casefold()
    if mime != "image/webp":
        return False

    filename = media_filename.strip()
    if not filename and media_url:
        filename = PurePosixPath(unquote(urlparse(media_url).path)).name

    return _BRIDGE_STICKER_FILENAME.fullmatch(filename) is not None
