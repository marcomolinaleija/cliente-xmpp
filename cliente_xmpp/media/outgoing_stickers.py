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
MAX_ANIMATION_PIXELS = 64 * 1024 * 1024


def prepare_outgoing_sticker(
    source: Path, *, output_dir: Path | None = None, copy_compatible: bool = False
) -> Path:
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
                payload = _prepare_animation(image, source)
                if payload is not None:
                    return _write_sticker(payload, output_dir)
                return (
                    _write_sticker(source.read_bytes(), output_dir) if copy_compatible else source
                )

            image.load()
            if (
                image.format == "WEBP"
                and image.size == STICKER_SIZE
                and source.suffix.lower() == ".webp"
                and source.stat().st_size <= limit
            ):
                # Preserve native EXIF/pack/accessibility metadata byte-for-byte.
                return (
                    _write_sticker(source.read_bytes(), output_dir) if copy_compatible else source
                )
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

    return _write_sticker(payload.getvalue(), output_dir)


def _prepare_animation(image: Image.Image, source: Path) -> bytes | None:
    """Keep native bytes, or resize every frame without cropping or lossy encoding."""
    if image.format != "WEBP":
        raise ValueError("El sticker animado debe ser WebP.")
    normalize = (
        image.size != STICKER_SIZE
        or source.stat().st_size > MAX_ANIMATED_BYTES
        or source.suffix.lower() != ".webp"
    )
    if normalize and image.n_frames * STICKER_SIZE[0] * STICKER_SIZE[1] > MAX_ANIMATION_PIXELS:
        raise ValueError("El sticker animado necesita demasiada memoria para ajustarlo.")
    metadata = {key: image.info[key] for key in ("exif", "icc_profile", "xmp") if key in image.info}
    loop = image.info.get("loop", 0)
    frames, durations = [], []
    elapsed = 0
    try:
        for index in range(image.n_frames):
            image.seek(index)
            image.load()
            duration = image.info.get("duration", 0)
            if duration < 8:
                raise ValueError("Cada fotograma del sticker debe durar al menos 8 ms.")
            elapsed += duration
            if elapsed > 10_000:
                raise ValueError("El sticker animado supera 10 segundos.")
            if normalize:
                frames.append(ImageOps.pad(
                    image.convert("RGBA"), STICKER_SIZE,
                    method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0),
                ))
                durations.append(duration)
        if not normalize:
            return None
        payload = io.BytesIO()
        frames[0].save(
            payload, format="WEBP", save_all=True, append_images=frames[1:],
            duration=durations, loop=loop, lossless=True, method=4, **metadata,
        )
        if payload.tell() > MAX_ANIMATED_BYTES:
            raise ValueError("No se pudo ajustar el sticker animado a 500 KB sin perder calidad.")
        return payload.getvalue()
    finally:
        for frame in frames:
            frame.close()


def _write_sticker(payload: bytes, output_dir: Path | None) -> Path:
    directory = output_dir if output_dir is not None else DOWNLOADS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"sticker-{uuid.uuid4().hex}.webp"
    partial = destination.with_suffix(".webp.part")
    try:
        partial.write_bytes(payload)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination
