from __future__ import annotations

import ctypes
import os
from importlib import resources
from pathlib import Path


class NvdaSpeaker:
    def __init__(self) -> None:
        self._dll: ctypes.CDLL | None = None
        self._dll_directory: object | None = None
        self._load_error: Exception | None = None

    def speak(self, text: str) -> None:
        if not text:
            return

        dll = self._ensure_dll()
        if dll is None:
            return

        try:
            dll.nvdaController_speakText(text)
        except OSError:
            return

    def _ensure_dll(self) -> ctypes.CDLL | None:
        if self._dll:
            return self._dll

        if self._load_error:
            return None

        dll_path = _nvda_dll_path()
        if not dll_path.exists():
            self._load_error = FileNotFoundError(dll_path)
            return None

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(dll_path.parent))

        try:
            dll = ctypes.CDLL(str(dll_path))
            dll.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
            dll.nvdaController_speakText.restype = None
        except (AttributeError, OSError) as exc:
            self._load_error = exc
            return None

        self._dll = dll
        return dll


def _nvda_dll_path() -> Path:
    return Path(resources.files("cliente_xmpp").joinpath("lib", "nvdaControllerClient64.dll"))
