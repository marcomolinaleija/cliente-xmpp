"""Bounded duplex PCM for voice calls. Device operations run on a serial worker.

PortAudio callbacks never touch wx, asyncio or the network. Construction and
device enumeration do not open a microphone. Every start requires a fresh permit.
"""
from __future__ import annotations

import queue
import threading
import time

import sounddevice as sd

from cliente_xmpp.audio.voice_playout import BYTES_PER_MS, PcmPlayout
from cliente_xmpp.models.voice import VoiceAudioSettings, VoiceAudioStats, VoiceDevice

RATE = 16000
FRAMES = 320
PCM_BYTES = FRAMES * 2
SILENCE = bytes(PCM_BYTES)
CAPTURE_FRAMES = 6


class LiveAudioError(RuntimeError):
    pass


def devices() -> tuple[VoiceDevice, ...]:
    try:
        apis = sd.query_hostapis()
        return tuple(VoiceDevice(
            index,
            (str(device["name"]) + " — " + str(apis[device["hostapi"]]["name"]))[:180],
            bool(device["max_input_channels"]), bool(device["max_output_channels"]),
            str(apis[device["hostapi"]]["name"]) + "\0" + str(device["name"]),
        ) for index, device in enumerate(sd.query_devices())
            if device["max_input_channels"] or device["max_output_channels"])
    except Exception:
        raise LiveAudioError("No se pudieron consultar los dispositivos de audio.") from None


def _latest(buffer: queue.Queue, data: bytes) -> bool:
    try:
        buffer.put_nowait(data)
        return False
    except queue.Full:
        pass
    try:
        buffer.get_nowait()
    except queue.Empty:
        pass
    try:
        buffer.put_nowait(data)
    except queue.Full:
        pass
    return True


def _clear(buffer: queue.Queue) -> None:
    while True:
        try:
            buffer.get_nowait()
        except queue.Empty:
            return


class LiveCallAudio:
    def __init__(self, backend=sd, *, clock=time.perf_counter):
        self.backend = backend
        self.clock = clock
        self.capture: queue.Queue[bytes] = queue.Queue(CAPTURE_FRAMES)
        self.playback = PcmPlayout()
        self._lock = threading.Lock()
        self._generation = 0
        self._wanted = False
        self._running = False
        self._muted = False
        self._failure = ""
        self._stream = None
        self._captured = self._capture_dropped = self._lock_misses = 0
        self._input_overflows = self._output_underflows = 0

    def permit(self, settings: VoiceAudioSettings) -> int:
        """Called by the loop after an explicit user action, never by a callback."""
        with self._lock:
            self._generation += 1
            self._wanted = True
            self._muted = settings.muted
            self._failure = ""
            _clear(self.capture)
            self.playback = PcmPlayout()
            self._captured = self._capture_dropped = self._lock_misses = 0
            self._input_overflows = self._output_underflows = 0
            return self._generation

    def invalidate(self) -> None:
        # Immediate privacy barrier, including while a worker is opening a device.
        with self._lock:
            self._generation += 1
            self._wanted = False
            self._running = False
            _clear(self.capture)
            self.playback.clear()

    def mute(self, value: bool) -> None:
        with self._lock:
            self._muted = value
            _clear(self.capture)

    @property
    def failure(self) -> str:
        with self._lock:
            return self._failure

    def check(self, settings: VoiceAudioSettings) -> None:
        try:
            for index, identity, kind in (
                (settings.input_device, settings.input_identity, "input"),
                (settings.output_device, settings.output_identity, "output"),
            ):
                if index is not None and identity:
                    info = self.backend.query_devices(index, kind)
                    api = self.backend.query_hostapis(info["hostapi"])
                    if str(api["name"]) + "\0" + str(info["name"]) != identity:
                        raise LiveAudioError(
                            "El dispositivo seleccionado cambió. Actualiza y vuelve a elegirlo."
                        )
            self.backend.check_input_settings(device=settings.input_device, channels=1,
                                              dtype="int16", samplerate=RATE)
            self.backend.check_output_settings(device=settings.output_device, channels=1,
                                               dtype="int16", samplerate=RATE)
        except LiveAudioError:
            raise
        except Exception:
            raise LiveAudioError(
                "Los dispositivos elegidos no admiten audio mono de 16 kHz."
            ) from None

    def start(self, permit: int, settings: VoiceAudioSettings) -> bool:
        """Serial worker only. A cancelled/obsolete permit cannot activate audio."""
        self._close_stream()
        with self._lock:
            if permit != self._generation or not self._wanted:
                return False
        stream = None
        try:
            self.check(settings)
            stream = self.backend.RawStream(
                samplerate=RATE, blocksize=FRAMES, channels=1, dtype="int16", latency="low",
                device=(settings.input_device, settings.output_device),
                callback=lambda *args: self._callback(permit, *args),
                finished_callback=lambda: self._finished(permit),
            )
            with self._lock:
                valid = permit == self._generation and self._wanted
            if not valid:
                stream.close()
                return False
            self._stream = stream
            stream.start()
            with self._lock:
                self._running = permit == self._generation and self._wanted
                valid = self._running
            if not valid:
                self._close_stream()
            return valid
        except Exception:
            if stream is not None and self._stream is None:
                stream.close()
            self._close_stream()
            self.invalidate()
            raise LiveAudioError(
                "No se pudo abrir audio mono de 16 kHz. Revisa los dispositivos y sus permisos."
            ) from None

    def stop(self) -> None:
        """Serial worker only; invalidate() must precede scheduling this operation."""
        try:
            self._close_stream()
        except Exception:
            raise LiveAudioError("No se pudo cerrar el dispositivo de audio.") from None

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()

    def _finished(self, permit: int) -> None:
        with self._lock:
            if permit == self._generation and self._wanted:
                self._running = False
                self._failure = "Se interrumpió el dispositivo de audio."

    def _callback(self, permit, indata, outdata, frames, _time, _status) -> None:
        # Never wait for another thread inside a PortAudio callback.
        output = memoryview(outdata).cast("B")
        output[:] = bytes(len(output))
        if not self._lock.acquire(blocking=False):
            if permit == self._generation and self._wanted:
                self._lock_misses += 1
            return
        try:
            if permit != self._generation or not self._wanted or not self._running:
                return
            if frames != FRAMES or len(indata) != PCM_BYTES or len(output) != PCM_BYTES:
                self._failure = "El dispositivo cambió el formato del audio."
                return
            self._captured += 1
            self._input_overflows += bool(getattr(_status, "input_overflow", False))
            self._output_underflows += bool(getattr(_status, "output_underflow", False))
            self._capture_dropped += _latest(
                self.capture, SILENCE if self._muted else bytes(indata),
            )
            data = self.playback.pop(len(output), self.clock())
            output[:len(data)] = data
        finally:
            self._lock.release()

    def take_capture(self) -> bytes | None:
        with self._lock:
            if not self._wanted or not self._running:
                return None
            try:
                data = self.capture.get_nowait()
            except queue.Empty:
                return None
            return SILENCE if self._muted else data

    def play(self, data: bytes) -> None:
        if not 0 < len(data) <= 3200 or len(data) % 2:
            raise LiveAudioError("Paquete PCM de reproducción inválido.")
        with self._lock:
            if self._wanted and self._running:
                self.playback.push(data, self.clock())

    def statistics(self) -> VoiceAudioStats:
        with self._lock:
            p = self.playback
            return VoiceAudioStats(
                capture_frames=self._captured, capture_dropped=self._capture_dropped,
                playback_gaps=p.gaps, playback_missing_ms=p.missing_bytes // BYTES_PER_MS,
                playback_dropped_ms=p.dropped_bytes // BYTES_PER_MS,
                playback_buffer_ms=len(p.data) // BYTES_PER_MS, playback_prefill_ms=p.prefill_ms,
                driver_input_overflows=self._input_overflows,
                driver_output_underflows=self._output_underflows,
                callback_lock_misses=self._lock_misses,
            )
