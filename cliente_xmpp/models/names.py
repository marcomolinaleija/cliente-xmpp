from __future__ import annotations

import re

JID_ESCAPE_PATTERN = re.compile(r"\\([0-9a-fA-F]{2})")
JID_ESCAPE_CODES = {
    "20",
    "22",
    "26",
    "27",
    "2f",
    "3a",
    "3c",
    "3e",
    "40",
    "5c",
}


def jid_bare(jid: str) -> str:
    return jid.split("/", 1)[0]


def jid_local_part(jid: str) -> str:
    bare = jid_bare(jid)
    return bare.split("@", 1)[0]


def jid_resource(jid: str) -> str:
    if "/" not in jid:
        return ""

    return jid.rsplit("/", 1)[-1]


def unescape_jid_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        code = match.group(1).casefold()
        if code not in JID_ESCAPE_CODES:
            return match.group(0)
        return chr(int(code, 16))

    return JID_ESCAPE_PATTERN.sub(replace, text)


def display_label_from_jid(value: str) -> str:
    value = value.strip()
    if not value:
        return ""

    candidate = jid_resource(value) or jid_local_part(value) or value
    candidate = unescape_jid_text(candidate).strip()
    if candidate.startswith("+"):
        candidate = candidate.removeprefix("+")
    return " ".join(candidate.split()) or value


def normalize_chat_name(jid: str, name: str = "") -> str:
    name = " ".join(unescape_jid_text(name.strip()).split())
    if name and name != jid:
        return name

    return display_label_from_jid(jid) or jid


def is_fallback_chat_name(jid: str, name: str) -> bool:
    """Return whether *name* is only the technical label derived from *jid*."""
    normalized_name = " ".join(name.split())
    return not normalized_name or normalized_name in {
        jid,
        display_label_from_jid(jid),
    }
