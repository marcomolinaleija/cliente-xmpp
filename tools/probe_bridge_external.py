"""External SSH/TLS/XMPP negotiation timeline, without login, pairing or messages."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

SERVER = "servidor.rayoscompany.com"
XMPP = "xmpp.rayoscompany.com"


def receive(sock, token):
    data = b""
    while token not in data:
        chunk = sock.recv(4096)
        if not chunk or len(data) > 65536:
            raise ConnectionError("Incomplete protocol response")
        data += chunk
    return data


def probe(port):
    started = time.monotonic()
    try:
        with socket.create_connection((SERVER, port), timeout=6) as sock:
            sock.settimeout(6)
            if port == 12400:
                if not sock.recv(128).startswith(b"SSH-"):
                    raise ValueError("Missing SSH banner")
            elif port == 443:
                with ssl.create_default_context().wrap_socket(sock, server_hostname=XMPP) as tls:
                    request = f"HEAD / HTTP/1.1\r\nHost: {XMPP}\r\nConnection: close\r\n\r\n"
                    tls.sendall(request.encode())
                    if not tls.recv(128).startswith(b"HTTP/"):
                        raise ValueError("Missing HTTPS response")
            elif port == 5222:
                stream = (f"<stream:stream to='{XMPP}' xmlns='jabber:client' "
                          "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>").encode()
                sock.sendall(stream)
                if b"starttls" not in receive(sock, b"</stream:features>"):
                    raise ValueError("Missing STARTTLS")
                sock.sendall(b"<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>")
                if b"proceed" not in receive(sock, b">"):
                    raise ValueError("STARTTLS refused")
                with ssl.create_default_context().wrap_socket(sock, server_hostname=XMPP) as tls:
                    tls.sendall(stream)
                    if b"mechanisms" not in receive(tls, b"</stream:features>"):
                        raise ValueError("Missing SASL mechanisms")
            # NVDA Remote: TCP only; do not join a channel or control a desktop.
        return {"port": port, "ok": True, "ms": round((time.monotonic() - started) * 1000)}
    except (OSError, ValueError) as exc:
        return {"port": port, "ok": False, "error": type(exc).__name__}


def sample():
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(probe, (12400, 443, 5222, 6837)))
    return {"utc": datetime.now(UTC).isoformat(), "probes": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=0)
    args = parser.parse_args()
    if not args.output:
        result = sample()
        print(json.dumps(result))
        return 0 if all(p["ok"] for p in result["probes"]) else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("external")
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(args.output, maxBytes=2_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    start = time.monotonic()
    while time.monotonic() - start < args.seconds:
        log.info(json.dumps(sample()))
        time.sleep(60)
    handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
