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
ALBUM_PHOTO_WINDOW_SECONDS = 30
ALBUM_PHOTO_PATTERN = re.compile(
    r"^\s*[aá]lbum:\s*(\d+)\s+(?:photos|fotos)\s*$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    size: int
    mime: str
    filename: str


def has_media(message: Message) -> bool:
    return bool(message.media_url or message.audio_url)


def album_photo_count(message: Message) -> int:
    match = ALBUM_PHOTO_PATTERN.fullmatch(message.body)
    if match is None:
        return 0
    return int(match.group(1))


def album_photo_messages(messages: list[Message], album: Message) -> list[Message]:
    """Return the consecutive photos announced by a Slidge album marker."""
    expected_count = album_photo_count(album)
    if expected_count <= 0:
        return []

    album_index = next(
        (
            index
            for index, candidate in enumerate(messages)
            if candidate is album
            or (
                album.message_id
                and candidate.message_id == album.message_id
                and candidate.chat_jid == album.chat_jid
            )
        ),
        -1,
    )
    if album_index < 0:
        return []

    photos: list[Message] = []
    for candidate in messages[album_index + 1 :]:
        elapsed = (candidate.sent_at - album.sent_at).total_seconds()
        if elapsed < 0:
            continue
        if elapsed > ALBUM_PHOTO_WINDOW_SECONDS:
            break
        if (
            candidate.chat_jid != album.chat_jid
            or candidate.outgoing != album.outgoing
            or candidate.sender_jid.casefold() != album.sender_jid.casefold()
            or candidate.media_kind != "image"
            or candidate.is_sticker
            or candidate.retracted
            or not candidate.media_url
        ):
            break

        photos.append(candidate)
        if len(photos) == expected_count:
            return photos

    return []


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

    # WhatsApp/Slidge suele asignar a las fotos nombres tecnicos (hashes o IDs)
    # que no aportan nada al usuario. El nombre remoto se conserva en Message
    # para descargar, reenviar y deduplicar, pero no forma parte de la etiqueta
    # visible ni accesible de una imagen.
    if message.media_kind == "image":
        return f"foto, {media_size_label(message.media_size)}"

    kind = {
        "audio": "audio",
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


def delete_local_media_file(message: Message) -> tuple[Path | None, OSError | None]:
    """Delete the exact local file associated with a retracted message.

    The model path is cleared even when the file has already disappeared or Windows
    refuses the deletion.  That prevents a deleted message from remaining playable.
    """
    raw_path = message.media_local_path
    message.media_local_path = ""
    if not raw_path:
        return None, None

    path = Path(raw_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return path, exc
    return path, None


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
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            mime = str(response.headers.get("Content-Type") or message.media_mime or "")
            size = int(response.headers.get("Content-Length") or 0)
            with temp_path.open("wb") as target:
                shutil.copyfileobj(response, target, CHUNK_SIZE)

        actual_size = temp_path.stat().st_size
        if actual_size <= 0:
            raise OSError("El servidor devolvio un archivo vacio.")
        if size > 0 and actual_size != size:
            raise OSError(
                f"La descarga quedo incompleta: se esperaban {size} bytes y llegaron "
                f"{actual_size}."
            )
        temp_path.replace(target_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

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
