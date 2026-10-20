"""Lossless FIFO delivery in bounded UI turns, with one outstanding wakeup."""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class UiEventPump[T]:
    def __init__(
        self, handle: Callable[[T], None], wake: Callable[[], None],
        resume: Callable[[], None], *, max_events: int = 25,
        budget_seconds: float = 0.008, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._handle, self._wake, self._resume = handle, wake, resume
        self._max_events, self._budget, self._clock = max_events, budget_seconds, clock
        self._queue: deque[T] = deque()
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False

    def submit(self, event: T) -> None:
        with self._lock:
            if self._closed:
                return
            self._queue.append(event)
            wake = not self._pending
            self._pending = True
        if wake:
            self._wake()

    def drain(self) -> None:
        """Called only on the UI thread; never holds the lock while handling."""
        deadline = self._clock() + self._budget
        try:
            for _ in range(self._max_events):
                with self._lock:
                    if self._closed or not self._queue:
                        break
                    event = self._queue.popleft()
                self._handle(event)
                if self._clock() >= deadline:
                    break
        finally:
            # Producers may enqueue while a callback runs. Keep ownership of the
            # next wakeup until the queue is empty, even if a handler raised.
            with self._lock:
                again = bool(self._queue) and not self._closed
                self._pending = again
            if again:
                self._resume()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._queue.clear()
