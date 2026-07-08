from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from cliente_xmpp.models.chat import Message

URL_PATTERN = re.compile(r"https?://\S+")
LOCAL_ATTACHMENT_HINTS = (
    "/slidge-attachments/",
    "/upload/",
)


@dataclass(frozen=True, slots=True)
class MessageLink:
    url: str
    title: str = ""


def message_links(message: Message) -> list[MessageLink]:
    links: list[MessageLink] = []
    seen: set[str] = set()

    for url in _urls_from_text(message.body):
        _append_link(links, seen, url)

    if _is_link_preview_media(message):
        _append_link(links, seen, message.media_url, title=_link_title(message))

    return links


def has_links(message: Message) -> bool:
    return bool(message_links(message))


def is_link_preview(message: Message) -> bool:
    return _is_link_preview_media(message)


def link_description(message: Message) -> str:
    links = message_links(message)
    if not links:
        return message.body

    if _is_link_preview_media(message):
        link = links[0]
        title = link.title or _host_label(link.url)
        return f"enlace, {title}, {link.url}"

    return message.body


def _append_link(
    links: list[MessageLink],
    seen: set[str],
    url: str,
    title: str = "",
) -> None:
    url = url.strip()
    if not url or url in seen:
        if title:
            for index, link in enumerate(links):
                if link.url == url and not link.title:
                    links[index] = MessageLink(url=link.url, title=title.strip())
                    break
        return

    seen.add(url)
    links.append(MessageLink(url=url, title=title.strip()))


def _urls_from_text(text: str) -> list[str]:
    return [
        match.group(0).strip("<>()[]\"'").rstrip(".,;:")
        for match in URL_PATTERN.finditer(text)
    ]


def _is_link_preview_media(message: Message) -> bool:
    if message.media_kind != "file":
        return False
    if not message.media_url.startswith(("http://", "https://")):
        return False
    if _is_local_attachment_url(message.media_url):
        return False

    mime = message.media_mime.split(";", 1)[0].strip().lower()
    if mime and mime not in {"text/html", "application/xhtml+xml"}:
        return False
    if not mime and _looks_like_downloadable_file(message):
        return False

    return True


def _is_local_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(hint in path for hint in LOCAL_ATTACHMENT_HINTS):
        return True

    host = parsed.netloc.lower()
    return host.startswith("xmpp.") and "upload" in path


def _link_title(message: Message) -> str:
    title = message.media_filename.strip()
    if title:
        return title

    body = message.body.strip()
    if body.casefold().startswith("archivo:"):
        return body.split(":", 1)[1].strip()
    if body and not body.startswith(("http://", "https://")):
        return body

    return ""


def _host_label(url: str) -> str:
    host = urlparse(url).netloc
    return host.removeprefix("www.") or "enlace"


def _looks_like_downloadable_file(message: Message) -> bool:
    filename = message.media_filename.strip()
    if "." in PurePosixPath(filename).name:
        return True

    path_name = PurePosixPath(urlparse(message.media_url).path).name
    return "." in path_name
