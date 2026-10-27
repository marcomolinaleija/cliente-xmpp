"""PCM cadence independent of Windows' coarse asyncio clock; no catch-up bursts."""
from __future__ import annotations

import asyncio
from time import perf_counter


class AudioCadence:
    def __init__(self, *, clock=perf_counter, sleep=asyncio.sleep):
        self.clock = clock
        self.sleep = sleep
        self.deadline = None

    async def wait(self) -> float:
        now = self.clock()
        if self.deadline is None:
            self.deadline = now
        # asyncio may wake early on Windows. Always recheck the precise clock.
        while self.deadline - now > 1e-9:
            await self.sleep(max(.001, self.deadline - now))
            now = self.clock()
        late = max(0., now - self.deadline)
        # Anchor to the sample rate, rather than accumulating each wakeup delay.
        # After a whole missed block, resume once and abandon the old deadline.
        self.deadline = now + .02 if late >= .02 else self.deadline + .02
        return late
