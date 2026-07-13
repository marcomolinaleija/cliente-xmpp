from __future__ import annotations

import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from cliente_xmpp.config.settings import APP_DIR
from cliente_xmpp.media.links import is_link_preview, link_description
from cliente_xmpp.models.chat import Message

DOWNLOADS_DIR = APP_DIR / "downloads"
CHUNK_SIZE = 1024 * 256
DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    size: int
    mime: str
    filename: str


def has_media(message: Message) -> bool:
    return bool(message.media_url or message.audio_url)


def media_url(message: Message) -> str:
    return message.media_url or message.audio_url


def media_filename(message: Message) -> str:
    if message.media_filename:
        return sanitize_filename(message.media_filename)

    url = media_url(message)
    parsed_name = unquote(Path(urlparse(url).path).name) if url else ""
    if parsed_name:
        return sanitize_filename(parsed_name)

    extension = mimetypes.guess_extension(message.media_mime or "") or ""
    prefix = message.media_kind or "archivo"
    return sanitize_filename(f"{prefix}{extension}")


def media_display_name(message: Message) -> str:
    filename = media_filename(message)
    return filename or "archivo"


def is_opaque_media_filename(filename: str) -> bool:
    """Reconoce los nombres hash que el bridge genera para notas de video."""
    path = Path(filename)
    return bool(re.fullmatch(r"[0-9a-f]{64}", path.stem, flags=re.IGNORECASE))


def media_size_label(size: int) -> str:
    if size <= 0:
        return "peso desconocido"

    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{size} B"


def media_description(message: Message) -> str:
    if is_link_preview(message):
        return link_description(message)

    if message.is_sticker:
        return "Sticker"

    if message.media_kind == "audio":
        if message.media_duration_seconds > 0:
            return f"voz, {format_duration(message.media_duration_seconds)}"

        return "voz"

    if message.media_kind == "video" and is_opaque_media_filename(media_filename(message)):
        return f"Nota de video, {media_size_label(message.media_size)}"

    kind = {
        "audio": "audio",
        "image": "foto",
        "video": "video",
        "file": "archivo",
    }.get(message.media_kind, "archivo")
    return f"{kind}, {media_display_name(message)}, {media_size_label(message.media_size)}"


def audio_description(message: Message) -> str:
    if message.media_duration_seconds > 0:
        return f"Mensaje de voz, {format_duration(message.media_duration_seconds)}"

    return "Mensaje de voz"


def format_duration(duration_seconds: float) -> str:
    total_seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    parts: list[str] = []
    if minutes == 1:
        parts.append("1 minuto")
    elif minutes > 1:
        parts.append(f"{minutes} minutos")

    if seconds == 1:
        parts.append("1 segundo")
    elif seconds > 1 or not parts:
        parts.append(f"{seconds} segundos")

    return " ".join(parts)


def local_media_path(message: Message) -> Path | None:
    if not message.media_local_path:
        return None

    path = Path(message.media_local_path)
    if path.exists():
        return path

    return None


def download_media(message: Message, account_jid: str) -> DownloadedMedia:
    url = media_url(message)
    if not url:
        raise ValueError("El mensaje no tiene URL de archivo.")

    target_dir = DOWNLOADS_DIR / sanitize_filename(account_jid or "cuenta") / sanitize_filename(
        message.chat_jid or "chat"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = unique_path(target_dir / media_filename(message))
    temp_path = target_path.with_name(f"{target_path.name}.part")

    request = Request(url, headers={"User-Agent": "cliente-xmpp/0.1"})
    with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        mime = str(response.headers.get("Content-Type") or message.media_mime or "")
        size = int(response.headers.get("Content-Length") or 0)
        with temp_path.open("wb") as target:
            shutil.copyfileobj(response, target, CHUNK_SIZE)

    temp_path.replace(target_path)
    actual_size = target_path.stat().st_size
    return DownloadedMedia(
        path=target_path,
        size=size or actual_size,
        mime=mime,
        filename=target_path.name,
    )


def sanitize_filename(value: str) -> str:
    value = unquote(value).strip().replace("\\", "_").replace("/", "_")
    value = re.sub(r"[\x00-\x1f<>:\"|?*]+", "_", value)
    value = value.strip(" .")
    return value[:180] or "archivo"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"No se pudo generar un nombre libre para {path}")
