from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def no_window_creation_flags() -> int:
    """Evita que herramientas de consola muestren una ventana en Windows."""
    if os.name != "nt":
        return 0

    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def hidden_subprocess_kwargs() -> dict[str, object]:
    """Return Windows process options that prevent console-window flashes."""
    if os.name != "nt":
        return {}

    options: dict[str, object] = {
        "creationflags": no_window_creation_flags(),
    }
    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is None:
        return options

    startupinfo = startupinfo_factory()
    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
    startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    options["startupinfo"] = startupinfo
    return options


def bundled_tool_path(filename: str) -> str:
    """Return a bundled executable placed beside the frozen app, when available."""
    if not getattr(sys, "frozen", False):
        return ""

    candidate = Path(sys.executable).resolve().parent / "bin" / filename
    return str(candidate) if candidate.is_file() else ""
