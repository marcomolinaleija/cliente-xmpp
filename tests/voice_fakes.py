"""Synthetic PortAudio fixture: never queries or opens system audio devices."""
from __future__ import annotations

import threading


class AudioBackend:
    def __init__(self):
        self.streams = []
        self.block_open = None
        self.opening = threading.Event()
        self.error = False
        self.now = 0.0

    def clock(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def check_input_settings(self, **_kwargs):
        if self.error:
            raise RuntimeError("fixture incompatible input")

    def check_output_settings(self, **_kwargs):
        if self.error:
            raise RuntimeError("fixture incompatible output")

    def RawStream(self, **kwargs):
        stream = Stream(kwargs)
        self.streams.append(stream)
        self.opening.set()
        if self.block_open:
            if not self.block_open.wait(3):
                raise RuntimeError("fixture open timeout")
        return stream


class Stream:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def abort(self):
        self.started = False
        self.kwargs["finished_callback"]()

    def close(self):
        self.closed = True

    def frame(self, data):
        output = bytearray(len(data))
        self.kwargs["callback"](data, output, len(data)//2, None, None)
        return bytes(output)
