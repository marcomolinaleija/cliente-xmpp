from __future__ import annotations

import os
import subprocess


def no_window_creation_flags() -> int:
    """Evita que herramientas de consola muestren una ventana en Windows."""
    if os.name != "nt":
        return 0

    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
