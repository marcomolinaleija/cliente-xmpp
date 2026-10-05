from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from cliente_xmpp.media.downloads import download_media, local_media_path
from cliente_xmpp.media.links import is_link_preview
from cliente_xmpp.media.stickers import convert_lottie_sticker_package
from cliente_xmpp.models.chat import Message

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}


def can_create_sticker(message: Message) -> bool:
    if message.retracted or is_link_preview(message):
        return False
    if not (message.media_url or message.media_local_path):
        return False
    if message.is_sticker:
        return True
    suffix = Path(message.media_filename or urlparse(message.media_url).path).suffix.lower()
    mime = message.media_mime.partition(";")[0].strip().casefold()
    if message.media_kind == "file":
        return suffix in IMAGE_EXTENSIONS or mime in IMAGE_MIMES
    if message.media_kind != "image":
        return False
    # Avoid offering creation for known SVG/GIF formats that the converter rejects.
    # Missing metadata still permits inspection of an explicitly identified image.
    if mime.startswith("image/"):
        return mime in IMAGE_MIMES
    return not suffix or suffix in IMAGE_EXTENSIONS


def message_sticker_description(message: Message) -> str:
    if message.media_alt_text.strip():
        return message.media_alt_text.strip()
    if message.is_sticker and message.body.casefold().startswith("sticker: "):
        return message.body[8:].strip()
    return ""


def source_from_message(message: Message, account_jid: str) -> Path:
    """Run in a worker; preserve remote metadata and never overwrite the source media."""
    if not can_create_sticker(message):
        raise ValueError("Este mensaje no puede convertirse en sticker.")
    path = local_media_path(message)
    if path is None:
        if not account_jid:
            raise ValueError("Conecta una cuenta para descargar el archivo primero.")
        path = download_media(message, account_jid).path
    if message.is_sticker and path.suffix.lower() == ".bin":
        converted = convert_lottie_sticker_package(path)
        if converted is None:
            raise ValueError("No se pudo convertir este sticker Lottie. El original se conserva.")
        # The existing Lottie adapter makes a representative static frame, not a new animation.
        path = converted
    if message.retracted:
        raise ValueError("El mensaje se retiró durante la descarga; no se creó el sticker.")
    return path
