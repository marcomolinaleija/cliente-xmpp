from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from aiohttp import ClientSession, ClientTimeout, TCPConnector, ThreadedResolver
from aiohttp.payload import Payload
from slixmpp import __version__ as slixmpp_version
from slixmpp.plugins.xep_0363.http_upload import FileTooBig, HTTPError

UploadProgress = Callable[[int, int], None]
CHUNK_BYTES = 256 * 1024
UPLOAD_IDLE_SECONDS = 90.0
UPLOAD_MAX_SECONDS = 24 * 60 * 60.0


class UploadBudget:
    """Estimate remaining time from written bytes, independently of the IQ timeout."""

    def __init__(self, size: int, now: float) -> None:
        self.size = size
        self.started = now
        self.sent = 0

    def deadline(self, now: float) -> float:
        elapsed = max(0.0, now - self.started)
        rate = self.sent / elapsed if self.sent and elapsed >= 1.0 else CHUNK_BYTES
        remaining = max(0, self.size - self.sent) / rate
        estimated = now + remaining * 1.5 + 40.0
        return min(
            max(self.started + UPLOAD_IDLE_SECONDS, estimated),
            self.started + UPLOAD_MAX_SECONDS,
        )

    def advance(self, count: int, now: float) -> None:
        self.sent += count


class _ProgressPayload(Payload):
    """Read off-loop and count bytes only after the HTTP writer accepts them."""

    def __init__(
        self, stream: BinaryIO, budget: UploadBudget, timer: asyncio.Timeout,
        idle_timer: asyncio.Timeout, progress: UploadProgress | None,
    ) -> None:
        super().__init__(stream, content_type="application/octet-stream")
        self._size = budget.size
        self.budget = budget
        self.timer = timer
        self.idle_timer = idle_timer
        self.progress = progress
        self.last_report = -1.0
        self.last_percent = -1

    async def write(self, writer: Any) -> None:
        loop = asyncio.get_running_loop()
        while chunk := await asyncio.to_thread(self._value.read, CHUNK_BYTES):
            await writer.write(chunk)
            now = loop.time()
            self.budget.advance(len(chunk), now)
            self.timer.reschedule(self.budget.deadline(now))
            self.idle_timer.reschedule(now + UPLOAD_IDLE_SECONDS)
            percent = min(99, self.budget.sent * 100 // max(1, self.budget.size))
            if self.progress and percent != self.last_percent and now - self.last_report >= 0.5:
                self.progress(self.budget.sent, self.budget.size)
                self.last_report, self.last_percent = now, percent

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        raise TypeError("A streaming upload cannot be decoded as text.")


def is_dns_resolution_error(exc: BaseException) -> bool:
    """Return whether an aiohttp connection failure originated in DNS resolution."""
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        os_error = getattr(current, "os_error", None)
        if isinstance(os_error, socket.gaierror):
            return True
        if type(current).__name__ == "ClientConnectorDNSError":
            return True

        detail = str(current).casefold()
        if "dns" in detail and any(
            marker in detail
            for marker in ("contact", "lookup", "name resolution", "resolve", "resolver")
        ):
            return True
        current = current.__cause__ or current.__context__

    return False


async def upload_file_with_system_resolver(
    upload: Any,
    file_path: Path,
    *,
    size: int,
    content_type: str,
    timeout: int,
    progress: UploadProgress | None = None,
    system_resolver: bool = True,
) -> str:
    """Request a bounded slot; stream with an adaptive deadline and optional DNS fallback."""
    if size > upload.max_file_size:
        raise FileTooBig(size, upload.max_file_size)

    slot_iq = await upload.request_slot(
        upload.upload_service,
        file_path.name,
        size,
        content_type,
        timeout=timeout,
    )
    slot = slot_iq["http_upload_slot"]
    headers = {
        "Content-Length": str(size),
        "Content-Type": content_type or upload.default_content_type,
        **{
            header["name"]: header["value"]
            for header in slot["put"]["headers"]
        },
    }

    connector = TCPConnector(resolver=ThreadedResolver()) if system_resolver else TCPConnector()
    async with ClientSession(
        connector=connector,
        headers={"User-Agent": f"slixmpp {slixmpp_version}"},
        timeout=ClientTimeout(total=None, connect=30, sock_read=UPLOAD_IDLE_SECONDS),
    ) as session:
        with file_path.open("rb") as input_file:
            loop = asyncio.get_running_loop()
            budget = UploadBudget(size, loop.time())
            async with asyncio.timeout_at(budget.deadline(loop.time())) as timer:
                async with asyncio.timeout(UPLOAD_IDLE_SECONDS) as idle_timer:
                    if progress:
                        progress(0, size)
                    response = await session.put(
                        slot["put"]["url"],
                        data=_ProgressPayload(input_file, budget, timer, idle_timer, progress),
                        headers=headers,
                    )
                    try:
                        if response.status >= 400:
                            # Never propagate a token-bearing response body into UI/logs.
                            raise HTTPError(response.status, "")
                        if progress:
                            progress(size, size)
                        return str(slot["get"]["url"])
                    finally:
                        response.close()
