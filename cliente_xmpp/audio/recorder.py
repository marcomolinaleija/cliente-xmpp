from __future__ import annotations

import queue
import subprocess
import threading
from pathlib import Path

import sounddevice as sd

from cliente_xmpp.audio.opus import (
    VOICE_NOTE_BITRATE,
    VOICE_NOTE_SAMPLE_RATE,
    ffmpeg_path,
    voice_note_path,
)
from cliente_xmpp.audio.process import hidden_subprocess_kwargs

SAMPLE_WIDTH_BYTES = 2
DEFAULT_CHANNELS = 1


class AudioRecordingError(RuntimeError):
    pass


class SoundDeviceAudioRecorder:
    def __init__(self) -> None:
        self._stream: sd.RawInputStream | None = None
        self._chunks: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._encoder: subprocess.Popen[bytes] | None = None
        self._writer_thread: threading.Thread | None = None
        self._output_path: Path | None = None
        self._writer_error: Exception | None = None
        self._is_recording = False
        self._is_paused = False
        self._sample_rate = 44_100
        self._channels = DEFAULT_CHANNELS
        self._bytes_recorded = 0

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def start(self) -> None:
        self.cancel()
        self._chunks = queue.SimpleQueue()
        self._writer_error = None
        self._bytes_recorded = 0
        self._sample_rate, self._channels = self._default_input_format()
        self._output_path = voice_note_path()
        self._encoder = self._start_encoder(self._output_path, self._sample_rate, self._channels)
        self._writer_thread = threading.Thread(target=self._write_chunks_to_encoder, daemon=True)
        self._writer_thread.start()
        try:
            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                callback=self._on_audio_data,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            self._finish_encoder(cancel=True)
            raise AudioRecordingError(f"No se pudo iniciar la grabacion: {exc}") from exc

        self._is_recording = True
        self._is_paused = False

    def pause(self) -> None:
        if not self._is_recording:
            return

        self._is_paused = True

    def resume(self) -> None:
        if not self._is_recording:
            return

        self._is_paused = False

    def stop_and_save(self) -> Path:
        if not self._is_recording:
            raise AudioRecordingError("No hay una grabacion activa.")

        self._stop_stream()
        if self._bytes_recorded == 0:
            self._finish_encoder(cancel=True)
            raise AudioRecordingError("No se capturo audio del microfono.")

        path = self._finish_encoder(cancel=False)
        if self._writer_error is not None:
            raise AudioRecordingError(f"No se pudo guardar la grabacion: {self._writer_error}")
        if not path.exists() or path.stat().st_size == 0:
            raise AudioRecordingError("No se pudo guardar la grabacion.")

        return path

    def cancel(self) -> None:
        try:
            self._stop_stream()
        finally:
            try:
                if self._encoder is not None or self._output_path is not None:
                    self._finish_encoder(cancel=True)
            finally:
                self._chunks = queue.SimpleQueue()

    def _stop_stream(self) -> None:
        stream = self._stream
        self._stream = None
        self._is_recording = False
        self._is_paused = False
        if stream is None:
            return

        try:
            stream.stop()
        finally:
            stream.close()

    def _on_audio_data(self, indata: bytes, _frames: int, _time: object, status: object) -> None:
        if status:
            # Keep recording; transient overflows should not abort the UI flow.
            pass
        if self._is_paused:
            return

        chunk = bytes(indata)
        self._bytes_recorded += len(chunk)
        self._chunks.put(chunk)

    def _write_chunks_to_encoder(self) -> None:
        encoder = self._encoder
        if encoder is None or encoder.stdin is None:
            return

        try:
            while True:
                chunk = self._chunks.get()
                if chunk is None:
                    break
                encoder.stdin.write(chunk)
        except Exception as exc:
            self._writer_error = exc
        finally:
            try:
                encoder.stdin.close()
            except OSError:
                pass

    def _finish_encoder(self, cancel: bool) -> Path:
        encoder = self._encoder
        writer_thread = self._writer_thread
        output_path = self._output_path
        self._encoder = None
        self._writer_thread = None
        self._output_path = None

        if encoder is None:
            if output_path is None:
                raise AudioRecordingError("No hay una grabacion activa.")
            return output_path

        self._chunks.put(None)

        if cancel:
            try:
                encoder.kill()
            except OSError:
                pass

        if writer_thread is not None:
            writer_thread.join(timeout=10)

        if not cancel:
            try:
                return_code = encoder.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                encoder.kill()
                raise AudioRecordingError("La codificacion de audio no termino a tiempo.") from exc

            if return_code != 0:
                raise AudioRecordingError("FFmpeg no pudo guardar la nota de voz.")
        else:
            try:
                encoder.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    encoder.kill()
                except OSError:
                    pass
            except OSError:
                pass
            finally:
                if output_path is not None:
                    try:
                        output_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        if output_path is None:
            raise AudioRecordingError("No se pudo preparar el archivo de audio.")

        return output_path

    @staticmethod
    def _start_encoder(path: Path, sample_rate: int, channels: int) -> subprocess.Popen[bytes]:
        command = [
            ffmpeg_path(),
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
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
            str(path),
        ]
        try:
            return subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **hidden_subprocess_kwargs(),
            )
        except Exception as exc:
            raise AudioRecordingError(f"No se pudo iniciar el codificador de audio: {exc}") from exc

    @staticmethod
    def _default_input_format() -> tuple[int, int]:
        try:
            device = sd.query_devices(kind="input")
        except Exception as exc:
            raise AudioRecordingError(
                f"No se pudo leer el microfono predeterminado: {exc}"
            ) from exc

        sample_rate = int(device.get("default_samplerate") or 44_100)
        max_channels = int(device.get("max_input_channels") or DEFAULT_CHANNELS)
        if max_channels <= 0:
            raise AudioRecordingError("El dispositivo predeterminado no tiene canales de entrada.")

        channels = 2 if max_channels >= 2 else 1
        return sample_rate, channels


MciAudioRecorder = SoundDeviceAudioRecorder
