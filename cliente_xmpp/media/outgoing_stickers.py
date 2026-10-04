from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from cliente_xmpp.media.downloads import DOWNLOADS_DIR

STICKER_SIZE = (512, 512)
MAX_STATIC_BYTES = 100 * 1024
MAX_ANIMATED_BYTES = 500 * 1024
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_PIXELS = 16 * 1024 * 1024


def prepare_outgoing_sticker(source: Path) -> Path:
    """Prepare a native WhatsApp WebP without changing the user's source file."""
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("La imagen del sticker supera 20 MB.")
    try:
        with Image.open(source) as image:
            if image.width * image.height > MAX_SOURCE_PIXELS:
                raise ValueError("La imagen del sticker tiene demasiados píxeles.")
            animated = getattr(image, "n_frames", 1) > 1
            limit = MAX_ANIMATED_BYTES if animated else MAX_STATIC_BYTES
            if animated:
                # Do not silently turn an animation into its first frame.
                if image.format != "WEBP" or image.size != STICKER_SIZE:
                    raise ValueError("El sticker animado debe ser WebP de 512 × 512 píxeles.")
                if source.stat().st_size > limit:
                    raise ValueError("El sticker animado supera 500 KB.")
                elapsed = 0
                for index in range(image.n_frames):
                    image.seek(index)
                    image.load()
                    duration = image.info.get("duration", 0)
                    if duration < 8:
                        raise ValueError("Cada fotograma del sticker debe durar al menos 8 ms.")
                    elapsed += duration
                    if elapsed > 10_000:
                        raise ValueError("El sticker animado supera 10 segundos.")
                return source

            image.load()
            if (
                image.format == "WEBP"
                and image.size == STICKER_SIZE
                and source.suffix.lower() == ".webp"
                and source.stat().st_size <= limit
            ):
                # Preserve native EXIF/pack/accessibility metadata byte-for-byte.
                return source
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Selecciona una imagen PNG, JPEG o WebP para el sticker.")
            canvas = ImageOps.pad(
                ImageOps.exif_transpose(image).convert("RGBA"),
                STICKER_SIZE,
                method=Image.Resampling.LANCZOS,
                color=(0, 0, 0, 0),
            )
            payload = io.BytesIO()
            canvas.save(payload, format="WEBP", lossless=True, method=4)
            # Lossless first; reduce quality only when WhatsApp's size limit requires it.
            for quality in (90, 80, 65, 50, 35, 20, 10):
                if payload.tell() <= limit:
                    break
                payload = io.BytesIO()
                canvas.save(payload, format="WEBP", quality=quality, method=4)
            if payload.tell() > limit:
                raise ValueError("No se pudo ajustar el sticker al límite de 100 KB.")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("No se pudo leer la imagen del sticker.") from exc

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOADS_DIR / f"sticker-{uuid.uuid4().hex}.webp"
    partial = destination.with_suffix(".webp.part")
    try:
        partial.write_bytes(payload.getvalue())
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination
