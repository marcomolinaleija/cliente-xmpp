from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from rlottie_python import LottieAnimation

_BRIDGE_STICKER_FILENAME = re.compile(
    r"^[0-9a-f]{64}(?: \(\d+\))?\.webp$",
    re.IGNORECASE,
)
_BRIDGE_LOTTIE_FILENAME = re.compile(
    r"^[0-9a-f]{64}(?: \(\d+\))?\.bin$",
    re.IGNORECASE,
)
LOTTIE_JSON_PATH = "animation/animation.json"
MAX_LOTTIE_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_LOTTIE_JSON_BYTES = 5 * 1024 * 1024
MAX_LOTTIE_ARCHIVE_ENTRIES = 32
MAX_STICKER_DIMENSION = 512
REPRESENTATIVE_FRAME_POSITIONS = (0.1, 0.25, 0.5, 0.75, 0.9)


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


def looks_like_lottie_sticker_attachment(
    *,
    media_kind: str,
    media_mime: str,
    media_filename: str,
    media_url: str = "",
    media_size: int = 0,
) -> bool:
    """Identify small opaque bridge attachments worth inspecting as raw Lottie stickers."""
    if media_kind.casefold() not in {"", "file"}:
        return False

    mime = media_mime.partition(";")[0].strip().casefold()
    if mime not in {"", "application/octet-stream", "application/zip"}:
        return False
    if media_size > MAX_LOTTIE_PACKAGE_BYTES:
        return False

    filename = media_filename.strip()
    if not filename and media_url:
        filename = PurePosixPath(unquote(urlparse(media_url).path)).name
    return _BRIDGE_LOTTIE_FILENAME.fullmatch(filename) is not None


def convert_lottie_sticker_package(source: Path) -> Path | None:
    """Render a representative WebP frame from a bridge Lottie ZIP without extracting it."""
    try:
        lottie_json = _lottie_json_from_package(source)
        if lottie_json is None:
            return None

        destination = _webp_destination(source)
        if destination.exists() and _is_webp(destination):
            return destination

        animation = LottieAnimation.from_data(lottie_json)
        width, height = _bounded_dimensions(*animation.lottie_animation_get_size())
        best_image = None
        best_area = -1
        for position in REPRESENTATIVE_FRAME_POSITIONS:
            frame = animation.lottie_animation_get_frame_at_pos(position)
            image = animation.render_pillow_frame(
                frame_num=frame,
                width=width,
                height=height,
            )
            alpha_bounds = image.getchannel("A").getbbox()
            area = 0
            if alpha_bounds is not None:
                area = (alpha_bounds[2] - alpha_bounds[0]) * (
                    alpha_bounds[3] - alpha_bounds[1]
                )
            if area > best_area:
                best_image = image.copy()
                best_area = area

        if best_image is None:
            return None

        temp_path = destination.with_name(f"{destination.name}.part")
        try:
            best_image.save(temp_path, format="WEBP", lossless=True, method=4)
            temp_path.replace(destination)
        finally:
            temp_path.unlink(missing_ok=True)
        return destination if _is_webp(destination) else None
    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile):
        return None


def _lottie_json_from_package(source: Path) -> str | None:
    if not source.is_file() or source.stat().st_size > MAX_LOTTIE_PACKAGE_BYTES:
        return None
    if not zipfile.is_zipfile(source):
        return None

    with zipfile.ZipFile(source) as archive:
        if len(archive.infolist()) > MAX_LOTTIE_ARCHIVE_ENTRIES:
            return None
        try:
            info = archive.getinfo(LOTTIE_JSON_PATH)
        except KeyError:
            return None
        if info.file_size <= 0 or info.file_size > MAX_LOTTIE_JSON_BYTES:
            return None
        payload = archive.read(info)

    text = payload.decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        return None
    if not all(data.get(field) for field in ("w", "h", "fr", "op")):
        return None
    return text


def _bounded_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("El sticker Lottie no tiene dimensiones válidas.")
    scale = min(1.0, MAX_STICKER_DIMENSION / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _webp_destination(source: Path) -> Path:
    destination = source.with_suffix(".webp")
    if not destination.exists() or _is_webp(destination):
        return destination

    for index in range(1, 1000):
        candidate = source.with_name(f"{source.stem} ({index}).webp")
        if not candidate.exists() or _is_webp(candidate):
            return candidate
    raise FileExistsError(f"No se pudo generar un nombre WebP para {source}")


def _is_webp(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            header = source.read(12)
    except OSError:
        return False
    return len(header) == 12 and header[:4] == b"RIFF" and header[8:] == b"WEBP"
