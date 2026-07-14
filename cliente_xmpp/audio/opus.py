from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from cliente_xmpp.audio.process import hidden_subprocess_kwargs
from cliente_xmpp.config.settings import APP_DIR

VOICE_NOTES_DIR = APP_DIR / "recordings"
VOICE_NOTE_MIME = "audio/ogg; codecs=opus"
VOICE_NOTE_UPLOAD_MIME = "audio/ogg"
VOICE_NOTE_BITRATE = "48k"
VOICE_NOTE_SAMPLE_RATE = 48_000


class AudioEncodingError(RuntimeError):
    pass


def convert_to_voice_note(source: Path) -> Path:
    if source.suffix.lower() == ".ogg" and _looks_like_voice_note(source):
        return source

    VOICE_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    output = voice_note_path()
    command = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(VOICE_NOTE_SAMPLE_RATE),
        "-c:a",
        "libopus",
        "-b:a",
        VOICE_NOTE_BITRATE,
        "-application",
        "voip",
        str(output),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:
        raise AudioEncodingError(f"No se pudo convertir el audio a OGG/Opus: {exc}") from exc

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise AudioEncodingError(f"No se pudo convertir el audio a OGG/Opus. {details}")

    if not output.exists() or output.stat().st_size == 0:
        raise AudioEncodingError("La conversion de audio no genero un archivo valido.")

    return output


def voice_note_path() -> Path:
    VOICE_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return VOICE_NOTES_DIR / f"ptt-{datetime.now():%Y%m%d-%H%M%S-%f}.ogg"


def ffmpeg_path() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise AudioEncodingError(
            "No se encontro ffmpeg. Instala ffmpeg o reinstala la app para incluir imageio-ffmpeg."
        ) from exc

    return imageio_ffmpeg.get_ffmpeg_exe()


def _looks_like_voice_note(source: Path) -> bool:
    return source.name.lower().startswith("ptt-")
