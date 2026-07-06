from __future__ import annotations

import ctypes
from datetime import datetime
from pathlib import Path

from cliente_xmpp.config.settings import APP_DIR

RECORDINGS_DIR = APP_DIR / "recordings"
SAMPLES_PER_SECOND = 48_000
BITS_PER_SAMPLE = 16
CHANNELS = 1
BLOCK_ALIGN = CHANNELS * BITS_PER_SAMPLE // 8
BYTES_PER_SECOND = SAMPLES_PER_SECOND * BLOCK_ALIGN


class AudioRecordingError(RuntimeError):
    pass


class MciAudioRecorder:
    def __init__(self) -> None:
        self._alias = "cliente_xmpp_recording"
        self._is_open = False
        self._is_recording = False
        self._is_paused = False

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def start(self) -> None:
        self.cancel()
        self._send(f"open new type waveaudio alias {self._alias}")
        self._is_open = True
        self._configure_quality()
        self._send(f"record {self._alias}")
        self._is_recording = True
        self._is_paused = False

    def pause(self) -> None:
        if not self._is_recording or self._is_paused:
            return

        self._send(f"pause {self._alias}")
        self._is_paused = True

    def resume(self) -> None:
        if not self._is_recording or not self._is_paused:
            return

        self._send(f"resume {self._alias}")
        self._is_paused = False

    def stop_and_save(self) -> Path:
        if not self._is_recording:
            raise AudioRecordingError("No hay una grabacion activa.")

        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        path = RECORDINGS_DIR / f"audio-{datetime.now():%Y%m%d-%H%M%S}.wav"
        self._send(f"stop {self._alias}")
        self._send(f'save {self._alias} "{path}"')
        self._close()
        if not path.exists() or path.stat().st_size <= 0:
            raise AudioRecordingError("No se pudo guardar la grabacion.")

        return path

    def cancel(self) -> None:
        if self._is_open:
            try:
                self._send(f"stop {self._alias}")
            except AudioRecordingError:
                pass
            self._close()

    def _close(self) -> None:
        if not self._is_open:
            return

        try:
            self._send(f"close {self._alias}")
        finally:
            self._is_open = False
            self._is_recording = False
            self._is_paused = False

    def _configure_quality(self) -> None:
        self._send(f"set {self._alias} time format samples")
        self._send(f"set {self._alias} channels {CHANNELS}")
        self._send(f"set {self._alias} samplespersec {SAMPLES_PER_SECOND}")
        self._send(f"set {self._alias} bitspersample {BITS_PER_SAMPLE}")
        self._send(f"set {self._alias} alignment {BLOCK_ALIGN}")
        self._send(f"set {self._alias} bytespersec {BYTES_PER_SECOND}")

    @staticmethod
    def _send(command: str) -> None:
        buffer = ctypes.create_unicode_buffer(512)
        code = ctypes.windll.winmm.mciSendStringW(command, buffer, len(buffer), None)
        if code == 0:
            return

        message = ctypes.create_unicode_buffer(512)
        ctypes.windll.winmm.mciGetErrorStringW(code, message, len(message))
        detail = message.value or f"codigo {code}"
        raise AudioRecordingError(f"No se pudo grabar audio: {detail}")
