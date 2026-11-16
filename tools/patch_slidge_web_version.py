"""Update the compiled WhatsApp Web baseline without replacing the voice fork."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Upstream whatsmeow 54038813c47f3f351eb6fcaafab0e1b99d676850 (2026-11-02).
WEB_VERSION = (2, 3000, 1052556849)
PREVIOUS_VERSION = (2, 3000, 1042386815)
VERSION_DECLARATION = re.compile(r"var waVersion = WAVersionContainer\{(\d+), (\d+), (\d+)\}")
LOG_LINE = '\tw.logger.Infof("CAN WhatsApp Web client version: %s", store.GetWAVersion())'
LOGGER_LINE = '\tw.logger = walog.Stdout("slidge", w.LogLevel, false)'


def payload_source(source: str) -> str:
    matches = list(VERSION_DECLARATION.finditer(source))
    if len(matches) != 1:
        raise ValueError("Expected one WhatsApp Web version declaration")
    match = matches[0]
    version = tuple(map(int, match.groups()))
    if version not in (PREVIOUS_VERSION, WEB_VERSION):
        raise ValueError(f"Unexpected WhatsApp Web baseline: {version}")
    replacement = "var waVersion = WAVersionContainer{" + ", ".join(map(str, WEB_VERSION)) + "}"
    return source[:match.start()] + replacement + source[match.end():]


def gateway_source(source: str) -> str:
    if source.count(LOGGER_LINE) != 1:
        raise ValueError("Expected one gateway logger initialization")
    if LOG_LINE in source:
        if source.count(LOG_LINE) != 1 or LOGGER_LINE + "\n" + LOG_LINE not in source:
            raise ValueError("Unexpected WhatsApp Web version logging placement")
        return source
    return source.replace(LOGGER_LINE, LOGGER_LINE + "\n" + LOG_LINE, 1)


def patch_package(site: Path, fork: Path | None = None) -> None:
    root = site / "slidge_whatsapp"
    transforms = {
        root / "vendor/go.mau.fi/whatsmeow/store/clientpayload.go": payload_source,
        root / "gateway.go": gateway_source,
    }
    if fork is not None:
        transforms[fork / "store/clientpayload.go"] = payload_source
    # Check both the shipped source and the actual go.mod replacement before writing.
    updates = {path: transform(path.read_text(encoding="utf-8"))
               for path, transform in transforms.items()}
    for path, source in updates.items():
        path.write_text(source, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("--fork", type=Path)
    args = parser.parse_args()
    patch_package(args.site, args.fork)
