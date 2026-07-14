from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from cliente_xmpp.audio.process import bundled_tool_path, hidden_subprocess_kwargs


def media_duration_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0

    ffprobe = _ffprobe_path()
    if not ffprobe:
        return 0.0

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception:
        return 0.0

    if result.returncode != 0:
        return 0.0

    try:
        payload = json.loads(result.stdout or "{}")
        duration = float(payload.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0

    return duration if duration > 0 else 0.0


def _ffprobe_path() -> str:
    bundled_ffprobe = bundled_tool_path("ffprobe.exe")
    if bundled_ffprobe:
        return bundled_ffprobe

    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe

    try:
        import imageio_ffmpeg
    except ImportError:
        return ""

    candidate = Path(imageio_ffmpeg.get_ffmpeg_exe()).with_name("ffprobe.exe")
    return str(candidate) if candidate.exists() else ""
