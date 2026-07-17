from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, TCPConnector, ThreadedResolver
from slixmpp import __version__ as slixmpp_version
from slixmpp.plugins.xep_0363.http_upload import FileTooBig, HTTPError


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
) -> str:
    """Request a fresh slot and upload using Windows' normal hostname resolver."""
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

    connector = TCPConnector(resolver=ThreadedResolver())
    async with ClientSession(
        connector=connector,
        headers={"User-Agent": f"slixmpp {slixmpp_version}"},
    ) as session:
        with file_path.open("rb") as input_file:
            response = await session.put(
                slot["put"]["url"],
                data=input_file,
                headers=headers,
                timeout=timeout,
            )
        try:
            if response.status >= 400:
                raise HTTPError(response.status, await response.text())
            return str(slot["get"]["url"])
        finally:
            response.close()
