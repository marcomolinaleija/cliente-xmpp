from __future__ import annotations

import ctypes
import os
from importlib import resources
from pathlib import Path
from typing import Literal

PlaybackStatus = Literal["playing", "paused"]
MPV_FORMAT_FLAG = 3
MPV_FORMAT_DOUBLE = 5
AUDIO_SPEEDS = (1.0, 1.5, 2.0)


class MpvPlaybackError(RuntimeError):
    pass


class MpvAudioPlayer:
    def __init__(self, video: bool = False) -> None:
        self._video = video
        self._dll: ctypes.CDLL | None = None
        self._dll_directory: object | None = None
        self._handle: ctypes.c_void_p | None = None
        self._current_url = ""
        self._paused = False
        self._speed = 1.0

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

    def cycle_speed(self, url: str) -> float:
        if not url:
            raise MpvPlaybackError("No hay URL de audio para cambiar la velocidad.")

        handle = self._ensure_handle()
        if url != self._current_url or self._playback_finished(handle):
            self._load_url(handle, url)

        current_index = min(
            range(len(AUDIO_SPEEDS)),
            key=lambda index: abs(AUDIO_SPEEDS[index] - self._speed),
        )
        next_speed = AUDIO_SPEEDS[(current_index + 1) % len(AUDIO_SPEEDS)]
        self._set_double_property(handle, "speed", next_speed)
        self._speed = next_speed
        return next_speed

    def stop(self) -> None:
        if self._handle:
            self._command(self._handle, ["stop"])
        self._current_url = ""
        self._paused = False
        self._speed = 1.0

    def current_duration_seconds(self, url: str) -> float | None:
        if not self._handle or url != self._current_url:
            return None

        return self._get_double_property(self._handle, "duration")

    def close(self) -> None:
        if self._handle and self._dll:
            self._dll.mpv_terminate_destroy(self._handle)
        self._handle = None
        self._current_url = ""
        self._paused = False
        self._speed = 1.0

    def _ensure_handle(self) -> ctypes.c_void_p:
        if self._handle:
            return self._handle

        dll = self._ensure_dll()
        handle = dll.mpv_create()
        if not handle:
            raise MpvPlaybackError("No se pudo crear el reproductor MPV.")

        if not self._video:
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
        dll.mpv_set_property.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        dll.mpv_set_property.restype = ctypes.c_int
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
        self._speed = 1.0
        self._set_double_property(handle, "speed", self._speed)

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

    def _get_double_property(self, handle: ctypes.c_void_p, name: str) -> float | None:
        value = ctypes.c_double()
        code = self._ensure_dll().mpv_get_property(
            handle,
            name.encode("utf-8"),
            MPV_FORMAT_DOUBLE,
            ctypes.byref(value),
        )
        if code < 0 or value.value <= 0:
            return None

        return float(value.value)

    def _set_double_property(self, handle: ctypes.c_void_p, name: str, value: float) -> None:
        property_value = ctypes.c_double(value)
        self._check_error(
            self._ensure_dll().mpv_set_property(
                handle,
                name.encode("utf-8"),
                MPV_FORMAT_DOUBLE,
                ctypes.byref(property_value),
            )
        )

    def _check_error(self, code: int) -> None:
        if code >= 0:
            return

        dll = self._ensure_dll()
        error = dll.mpv_error_string(code)
        message = error.decode("utf-8", errors="replace") if error else f"codigo {code}"
        raise MpvPlaybackError(f"MPV no pudo reproducir el audio: {message}")


def _mpv_dll_path() -> Path:
    return Path(resources.files("cliente_xmpp").joinpath("lib", "libmpv-2.dll"))
