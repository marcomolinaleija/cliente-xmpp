"""Bounded PCM reserve for ordered WebSocket audio; no devices or network."""
from __future__ import annotations

BYTES_PER_MS = 32  # PCM16 mono, 16 kHz.


class PcmPlayout:
    def __init__(self):
        self.data = bytearray()
        self.first_at = None
        self.ready = False
        self.played_once = False
        self.prefill_ms = 60
        self.gaps = 0
        self.missing_bytes = 0
        self.dropped_bytes = 0

    def empty(self):
        return not self.data

    def clear(self):
        self.data.clear()
        self.first_at = None
        self.ready = False
        self.played_once = False

    def push(self, data, now):
        if self.first_at is None:
            self.first_at = now
        self.data.extend(data)
        excess = max(0, len(self.data) - 240 * BYTES_PER_MS)
        if excess:
            del self.data[:excess]
            self.dropped_bytes += excess

    def pop(self, size, now):
        if not self.ready:
            if (self.first_at is None or now + 1e-9 < self.first_at + self.prefill_ms / 1000
                    or len(self.data) < size):
                if self.played_once:
                    self.missing_bytes += size
                return b""
            self.ready = True
            self.played_once = True
        count = min(size, len(self.data))
        data = bytes(self.data[:count])
        del self.data[:count]
        if count < size:
            self.gaps += 1
            self.missing_bytes += size - count
            self.ready = False
            self.first_at = None
            self.prefill_ms = min(120, self.prefill_ms + 20)
        return data
