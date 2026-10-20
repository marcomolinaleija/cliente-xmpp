"""Live voice data; independent of stored call history and message unread state."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceDevice:
    index: int
    name: str
    input: bool
    output: bool
    identity: str = ""


@dataclass(frozen=True, slots=True)
class VoiceAudioSettings:
    input_device: int | None = None
    output_device: int | None = None
    muted: bool = False
    input_identity: str = ""
    output_identity: str = ""


@dataclass(frozen=True, slots=True)
class VoiceAudioStats:
    capture_frames: int = 0
    capture_dropped: int = 0
    playback_gaps: int = 0
    playback_missing_ms: int = 0
    playback_dropped_ms: int = 0
    playback_buffer_ms: int = 0
    playback_prefill_ms: int = 60
    driver_input_overflows: int = 0
    driver_output_underflows: int = 0
    callback_lock_misses: int = 0
    transport_capture_dropped: int = 0
    transport_playback_dropped: int = 0
    receive_gap_max_ms: int = 0
    send_max_ms: int = 0
    pump_late_ticks: int = 0
    pump_late_max_ms: int = 0
