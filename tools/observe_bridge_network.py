"""Bounded content-free server incident timeline; never changes services/firewall/data."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import re
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

PORTS = (12400, 5222, 443, 6837)


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=12)


def proc_counters(path):
    lines = Path(path).read_text().splitlines()
    result = {}
    for header, values in zip(lines[::2], lines[1::2], strict=False):
        keys, counts = header.split(), values.split()
        result.update({keys[0] + key: int(value)
                       for key, value in zip(keys[1:], counts[1:], strict=False)})
    return result


def tcp(host, port):
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=3):
            return {"ok": True, "ms": round((time.monotonic() - started) * 1000)}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def observe(directory, seconds):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = logging.getLogger("timeline")
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        directory / "timeline.jsonl", maxBytes=8_000_000, backupCount=4
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    lock = threading.Lock()
    packets = {str(port): 0 for port in PORTS}
    sniffer = None
    # Header-only SYN counts, no addresses, packet bodies or pcap files retained.
    interface = command("ip", "route", "show", "default").split(" dev ")[1].split()[0]
    try:
        sniffer = subprocess.Popen(
            ["tcpdump", "-n", "-l", "-s", "80", "-i", interface,
             "tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn and (" +
             " or ".join(f"dst port {port}" for port in PORTS) + ")"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )

        def count():
            for line in sniffer.stdout:
                match = re.search(r" > [^ ]+\.(\d+):", line)
                if match and match[1] in packets:
                    with lock:
                        packets[match[1]] += 1

        threading.Thread(target=count, daemon=True).start()
        start = time.monotonic()
        until = datetime.now(UTC).isoformat()
        while time.monotonic() - start < seconds:
            sample = {"utc": datetime.now(UTC).isoformat(),
                      "elapsed": round(time.monotonic() - start),
                      "local_tcp": {str(p): tcp("127.0.0.1", p) for p in PORTS}}
            try:
                state = json.loads(command("docker", "inspect", "slidge-whatsapp"))[0]
                sample["bridge"] = {
                    "id": state["Id"], "image": state["Image"],
                    "started": state["State"]["StartedAt"],
                    "running": state["State"]["Running"],
                    "oom": state["State"]["OOMKilled"], "restarts": state["RestartCount"],
                    "health": state["State"].get("Health", {}).get("Status", "none"),
                }
                end = sample["utc"]
                logs = subprocess.run(
                    ["docker", "logs", "--since", until, "--until", end, "slidge-whatsapp"],
                    capture_output=True, text=True, timeout=12,
                )
                combined = logs.stdout + logs.stderr
                sample["errors"] = {k: combined.count(k) for k in (
                    "database is locked", "CAN archive delivery failed", "Crash in", "panic:",
                    "recovery_failed", "ParseError", "status_unavailable")}
                until = end
                sample["tcp_counters"] = proc_counters("/proc/net/snmp")
                sample["tcp_ext"] = proc_counters("/proc/net/netstat")
                sample["conntrack"] = int(Path(
                    "/proc/sys/net/netfilter/nf_conntrack_count").read_text())
                sample["pressure"] = {k: Path(f"/proc/pressure/{k}").read_text().strip()
                                      for k in ("cpu", "memory", "io")}
                sample["cpu_ticks"] = [int(v) for v in Path("/proc/stat").read_text()
                                       .splitlines()[0].split()[1:]]
                sample["memory_kib"] = {
                    key.rstrip(":"): int(value)
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    for key, value, *_ in [line.split()]
                    if key in ("MemAvailable:", "SwapFree:", "Dirty:", "Writeback:")
                }
                sample["interface"] = {k: int(Path(
                    f"/sys/class/net/{interface}/statistics/{k}").read_text())
                    for k in ("rx_errors", "rx_dropped", "tx_errors", "tx_dropped")}
                with lock:
                    sample["incoming_syn_total"] = dict(packets)
                sample["capture_running"] = sniffer.poll() is None
            except Exception as exc:
                sample["collection_error"] = type(exc).__name__
            log.info(json.dumps(sample, separators=(",", ":")))
            time.sleep(30)
    finally:
        if sniffer:
            sniffer.terminate()
            sniffer.wait(timeout=5)
        handler.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--seconds", type=int, default=93600)
    args = parser.parse_args()
    observe(args.directory, args.seconds)
