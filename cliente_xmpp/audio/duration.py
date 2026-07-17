from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cliente_xmpp.audio.opus import ffmpeg_path
from cliente_xmpp.audio.process import hidden_subprocess_kwargs

_DURATION_PATTERN = re.compile(
    r"Duration:\s*(?P<hours>\d+):(?P<minutes>\d{2}):(?P<seconds>\d+(?:\.\d+)?)"
)


def media_duration_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0

    try:
        result = subprocess.run(
            [
                ffmpeg_path(),
                "-hide_banner",
                "-i",
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

    match = _DURATION_PATTERN.search(f"{result.stderr or ''}\n{result.stdout or ''}")
    if match is None:
        return 0.0

    try:
        duration = (
            int(match["hours"]) * 3600
            + int(match["minutes"]) * 60
            + float(match["seconds"])
        )
    except (TypeError, ValueError):
        return 0.0

    return duration if duration > 0 else 0.0
