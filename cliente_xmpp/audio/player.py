from __future__ import annotations

import ctypes
import os
from importlib import resources
from pathlib import Path
from typing import Literal

PlaybackStatus = Literal["playing", "paused"]
MPV_FORMAT_FLAG = 3


class MpvPlaybackError(RuntimeError):
    pass


class MpvAudioPlayer:
    def __init__(self) -> None:
        self._dll: ctypes.CDLL | None = None
        self._dll_directory: object | None = None
        self._handle: ctypes.c_void_p | None = None
        self._current_url = ""
        self._paused = False

    def play(self, url: str) -> PlaybackStatus:
        if not url:
            raise MpvPlaybackError("No hay URL de audio para reproducir.")

        handle = self._ensure_handle()
        if url == self._current_url:
            if self._playback_finished(handle):
                self._load_url(handle, url)
                return "playing"

            self._command(handle, ["cycle", "pause"])
            self._paused = self._get_flag_property(handle, "pause")
            return "paused" if self._paused else "playing"

        self._load_url(handle, url)
        return "playing"

    def stop(self) -> None:
        if self._handle:
            self._command(self._handle, ["stop"])
        self._current_url = ""
        self._paused = False

    def close(self) -> None:
        if self._handle and self._dll:
            self._dll.mpv_terminate_destroy(self._handle)
        self._handle = None
        self._current_url = ""
        self._paused = False

    def _ensure_handle(self) -> ctypes.c_void_p:
        if self._handle:
            return self._handle

        dll = self._ensure_dll()
        handle = dll.mpv_create()
        if not handle:
            raise MpvPlaybackError("No se pudo crear el reproductor MPV.")

        self._check_error(dll.mpv_set_option_string(handle, b"video", b"no"))
        self._check_error(dll.mpv_set_option_string(handle, b"terminal", b"no"))
        self._check_error(dll.mpv_initialize(handle))
        self._handle = handle
        return handle

    def _ensure_dll(self) -> ctypes.CDLL:
        if self._dll:
            return self._dll

        dll_path = _mpv_dll_path()
        if not dll_path.exists():
            raise MpvPlaybackError(f"No se encontro libmpv en {dll_path}.")

        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(dll_path.parent))

        try:
            dll = ctypes.CDLL(str(dll_path))
        except OSError as exc:
            raise MpvPlaybackError(f"No se pudo cargar libmpv: {exc}") from exc
        dll.mpv_create.restype = ctypes.c_void_p
        dll.mpv_initialize.argtypes = [ctypes.c_void_p]
        dll.mpv_initialize.restype = ctypes.c_int
        dll.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        dll.mpv_set_option_string.restype = ctypes.c_int
        dll.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        dll.mpv_command.restype = ctypes.c_int
        dll.mpv_get_property.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        dll.mpv_get_property.restype = ctypes.c_int
        dll.mpv_error_string.argtypes = [ctypes.c_int]
        dll.mpv_error_string.restype = ctypes.c_char_p
        dll.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        dll.mpv_terminate_destroy.restype = None
        self._dll = dll
        return dll

    def _command(self, handle: ctypes.c_void_p, args: list[str]) -> None:
        encoded_args = [arg.encode("utf-8") for arg in args]
        command = (ctypes.c_char_p * (len(encoded_args) + 1))()
        command[:-1] = encoded_args
        command[-1] = None
        self._check_error(self._ensure_dll().mpv_command(handle, command))

    def _load_url(self, handle: ctypes.c_void_p, url: str) -> None:
        self._command(handle, ["loadfile", url, "replace"])
        self._current_url = url
        self._paused = False

    def _playback_finished(self, handle: ctypes.c_void_p) -> bool:
        return self._get_flag_property(handle, "idle-active") or self._get_flag_property(
            handle,
            "eof-reached",
        )

    def _get_flag_property(self, handle: ctypes.c_void_p, name: str) -> bool:
        value = ctypes.c_int()
        code = self._ensure_dll().mpv_get_property(
            handle,
            name.encode("utf-8"),
            MPV_FORMAT_FLAG,
            ctypes.byref(value),
        )
        if code < 0:
            return False

        return bool(value.value)

    def _check_error(self, code: int) -> None:
        if code >= 0:
            return

        dll = self._ensure_dll()
        error = dll.mpv_error_string(code)
        message = error.decode("utf-8", errors="replace") if error else f"codigo {code}"
        raise MpvPlaybackError(f"MPV no pudo reproducir el audio: {message}")


def _mpv_dll_path() -> Path:
    return Path(resources.files("cliente_xmpp").joinpath("lib", "libmpv-2.dll"))
