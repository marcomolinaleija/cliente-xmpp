from __future__ import annotations

import io
import json
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from PIL import Image, ImageOps

from cliente_xmpp.media.links import is_link_preview
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.sticker_library import StickerLibrary, bounded_text

PACK_EXTENSIONS = {".canstickers", ".wastickers"}
MAX_PACK_BYTES = 120 * 1024 * 1024
MAX_PACK_ITEMS = 200
MAX_METADATA_BYTES = 2 * 1024 * 1024
NATIVE_PACK_MIME = "application/x-can-sticker-pack"


def is_sticker_pack_attachment(message: Message) -> bool:
    if message.retracted or is_link_preview(message):
        return False
    if not (message.media_url or message.media_local_path):
        return False
    names = (
        message.media_filename,
        unquote(urlparse(message.media_url).path),
        message.media_local_path,
    )
    return message.media_mime == NATIVE_PACK_MIME or any(
        Path(name).suffix.lower() in PACK_EXTENSIONS for name in names if name
    )


def sticker_pack_from_message(messages: list[Message], message: Message) -> Message | None:
    """Resolve Slidge's separate caption without merging identities or changing history."""
    if is_sticker_pack_attachment(message):
        return message
    if (
        message.retracted
        or message.media_url
        or message.media_local_path
        or not message.body.strip().startswith("Paquete de stickers:")
    ):
        return None
    # Slidge emits attachment then caption with the same sender and exact delay stamp.
    # Consider only immediate neighbours and reject ambiguity, not merely matching text.
    index = next(
        (
            i
            for i, candidate in enumerate(messages)
            if candidate is message
            or (
                message.message_id
                and candidate.message_id == message.message_id
                and candidate.chat_jid == message.chat_jid
            )
        ),
        -1,
    )
    if index < 0:
        return None
    matches = [
        candidate
        for i in (index - 1, index + 1)
        if 0 <= i < len(messages)
        for candidate in (messages[i],)
        if candidate.chat_jid == message.chat_jid
        and candidate.sender_jid == message.sender_jid
        and candidate.outgoing == message.outgoing
        and candidate.sent_at == message.sent_at
        and is_sticker_pack_attachment(candidate)
    ]
    return matches[0] if len(matches) == 1 else None


def export_pack(library: StickerLibrary, pack_id: int, destination: Path) -> Path:
    packs = {pack.id: pack for pack in library.packs()}
    if pack_id not in packs:
        raise ValueError("Selecciona un paquete existente.")
    pack = packs[pack_id]
    entries = library.list_stickers(pack_id=pack_id)
    if not entries or pack.count > MAX_PACK_ITEMS:
        raise ValueError("El paquete debe contener entre 1 y 200 stickers.")
    whatsapp = destination.suffix.lower() == ".wastickers"
    if destination.suffix.lower() not in PACK_EXTENSIONS:
        raise ValueError("Usa la extensión .canstickers o .wastickers.")
    if destination.resolve().is_relative_to(library.root):
        raise ValueError("Exporta fuera de la carpeta interna de la biblioteca.")
    if whatsapp and (not 3 <= len(entries) <= 30 or len({x.animated for x in entries}) != 1):
        raise ValueError(
            "Para .wastickers se necesitan de 3 a 30 stickers, todos estáticos "
            "o todos animados. Puedes exportar este paquete en formato CAN."
        )
    # Fully validate before creating/replacing the user-selected export file.
    payloads = [(entry, Path(entry.path).read_bytes()) for entry in entries]
    for _, data in payloads:
        if len(data) > 500 * 1024 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
            raise ValueError("Un archivo de la biblioteca está dañado; vuelve a crearlo.")
    for entry, data in payloads:
        with Image.open(io.BytesIO(data)) as image:
            animated = image.n_frames > 1
            if (
                image.size != (512, 512)
                or animated != entry.animated
                or len(data) > (500 if animated else 100) * 1024
            ):
                raise ValueError("Un sticker ya no cumple los límites de WhatsApp.")
            elapsed = 0
            for index in range(image.n_frames):
                image.seek(index)
                image.load()
                if animated:
                    duration = image.info.get("duration", 0)
                    elapsed += duration
                    if duration < 8 or elapsed > 10_000:
                        raise ValueError("La animación ya no cumple los límites de WhatsApp.")
    with Image.open(io.BytesIO(payloads[0][1])) as first:
        cover = ImageOps.pad(first.convert("RGBA"), (96, 96), color=(0, 0, 0, 0))
        tray = io.BytesIO()
        cover.save(tray, format="PNG")
    manifest = {
        "format": "can-stickers",
        "version": 1,
        "name": pack.name,
        "author": pack.author,
        "stickers": [
            {"file": f"{index:03d}.webp", "name": entry.name, "description": entry.description}
            for index, (entry, _) in enumerate(payloads)
        ],
    }
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as temp:
        partial = Path(temp.name)
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("title.txt", pack.name)
            archive.writestr("author.txt", pack.author)
            archive.writestr("cover.png", tray.getvalue())
            if not whatsapp:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for index, (_, data) in enumerate(payloads):
                archive.writestr(f"{index:03d}.webp", data)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def import_pack(library: StickerLibrary, source: Path) -> int:
    """Validate a bounded flat ZIP in isolation; never extract arbitrary archive paths."""
    if source.suffix.lower() not in PACK_EXTENSIONS or source.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("Paquete no compatible o demasiado grande.")
    try:
        with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory() as directory:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_PACK_ITEMS + 4 or len({n.casefold() for n in names}) != len(names):
                raise ValueError("El paquete contiene demasiados archivos o nombres duplicados.")
            total = 0
            for info in infos:
                name = PurePosixPath(info.filename)
                if (
                    len(name.parts) != 1
                    or name.name in {"", ".", ".."}
                    or "\\" in info.filename
                    or ":" in info.filename
                    or name.is_absolute()
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                ):
                    raise ValueError("El paquete contiene rutas o enlaces no seguros.")
                if info.file_size > MAX_METADATA_BYTES:
                    raise ValueError("Un archivo del paquete es demasiado grande.")
                total += info.file_size
            if total > MAX_PACK_BYTES:
                raise ValueError("El paquete descomprimido es demasiado grande.")
            if "manifest.json" in names:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("format") != "can-stickers"
                    or manifest.get("version") != 1
                ):
                    raise ValueError("Versión de paquete CAN no compatible.")
                stickers = manifest.get("stickers")
                name, author = manifest.get("name"), manifest.get("author")
            else:
                if source.suffix.lower() == ".canstickers":
                    raise ValueError("Falta el manifiesto del paquete CAN.")
                name = archive.read("title.txt").decode("utf-8")
                author = archive.read("author.txt").decode("utf-8")
                stickers = [
                    {"file": n, "name": Path(n).stem, "description": ""}
                    for n in names
                    if Path(n).suffix.lower() in {".webp", ".png"} and n.lower() != "cover.png"
                ]
            if not isinstance(name, str) or not isinstance(author, str):
                raise ValueError("Nombre o autor de paquete no válido.")
            name = bounded_text(name, 128, "Nombre", required=True)
            author = bounded_text(author, 128, "Autor", required=True)
            if not isinstance(stickers, list) or not 1 <= len(stickers) <= MAX_PACK_ITEMS:
                raise ValueError("El paquete no tiene una lista válida de stickers.")
            entries, seen = [], set()
            for index, entry in enumerate(stickers):
                if not isinstance(entry, dict):
                    raise ValueError("Sticker del paquete no válido.")
                filename = entry.get("file")
                if (
                    not isinstance(filename, str)
                    or filename not in names
                    or filename in seen
                    or Path(filename).suffix.lower() not in {".webp", ".png"}
                ):
                    raise ValueError("Archivo de sticker no válido o duplicado.")
                seen.add(filename)
                title, description = entry.get("name"), entry.get("description", "")
                if not isinstance(title, str) or not isinstance(description, str):
                    raise ValueError("Texto del sticker no válido.")
                target = Path(directory) / f"{index}{Path(filename).suffix.lower()}"
                target.write_bytes(archive.read(filename))
                entries.append((target, title, description))
            # add_many commits membership and files as one logical import; a bad image rolls back.
            created = library.add_many(entries, pack_name=name, author=author)
            return created[0].pack_ids[-1]
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("El paquete de stickers está dañado o incompleto.") from exc
