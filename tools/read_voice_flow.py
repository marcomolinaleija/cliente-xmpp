"""Filter a bounded log stream into strictly anonymous voice counters.

Reads stdin only. Never echoes an unrecognized line or JSON field. Run at the
source of logs; only its filtered output should leave that host. Proxy/native
tags are independent and MUST NOT be joined by duration alone.
"""
from __future__ import annotations

import json
import re
import sys
from collections import deque

MAX_LINE = 16384
MAX_INPUT = 16 * 1024 * 1024
FLOW_FIELDS = frozenset("""
client_received_bytes native_forwarded_bytes native_received_bytes client_forwarded_bytes
client_packets native_packets client_gap_max_ms native_gap_max_ms native_send_max_ms
client_send_max_ms loop_delay_max_ms owner_loop_delay_max_ms capture_queue_dropped_packets
capture_revoked_packets capture_queue_packets capture_delay_max_ms
""".split())
NATIVE_FIELDS = frozenset("""
socket_capture_packets capture_queue_dropped_packets capture_inactive_packets
pcm_gap_max_us pcm_consumed_blocks pcm_consumed_samples pcm_not_ready_blocks pcm_not_ready_samples
rtp_gap_max_us rtp_packets rtp_timestamp_samples rtp_sequence_discontinuities
rtp_clock_discontinuities protect_error relay_unmeasured relay_broadcast_packets
relay_no_targets_packets relay_untracked_outcomes relay_unknown_outcomes
""".split()) | frozenset(
    prefix + suffix for prefix in ("encode", "keepalive_encode")
    for suffix in ("_frames", "_errors", "_total_us", "_max_us", "_over_60ms")
)
LOCATION = re.compile(
    r"(?:(?:slidge|slidge_whatsapp|asyncio|aiohttp)\.[A-Za-z0-9_.]{1,100}"
    r"|selectors):[A-Za-z0-9_]{1,64}:[0-9]{1,8}|unavailable"
)


def number(value):
    return type(value) is int and 0 <= value <= (1 << 64) - 1


def counters(value, allowed):
    return (isinstance(value, dict) and not value.keys() - allowed
            and all(number(count) for count in value.values()))


def parse_report(line):
    if len(line) > MAX_LINE:
        return None
    for marker, kind, length in (("CAN voice flow ", "flow", 8),
                                 ("CAN_VOICE_NATIVE ", "native", 16)):
        position = line.find(marker)
        if position < 0:
            continue
        try:
            value = json.loads(line[position + len(marker):])
        except (ValueError, RecursionError):
            return None
        if (not isinstance(value, dict) or type(value.get("final")) is not bool
                or not number(value.get("elapsed_ms"))
                or not isinstance(value.get("tag"), str)
                or not re.fullmatch("[0-9a-f]{" + str(length) + "}", value["tag"])):
            return None
        extra = {key: item for key, item in value.items()
                 if key not in {"tag", "final", "elapsed_ms"}}
        if kind == "flow":
            idle = {key: extra.pop(key) for key in ("client_idle_ms", "native_idle_ms")
                    if key in extra}
            if any(item is not None and not number(item) for item in idle.values()):
                return None
            hotspots = extra.pop("owner_hotspots", [])
            if not isinstance(hotspots, list) or len(hotspots) > 16:
                return None
            for item in hotspots:
                if (not isinstance(item, dict) or set(item) != {"location", "samples"}
                        or not isinstance(item["location"], str)
                        or not LOCATION.fullmatch(item["location"])
                        or not number(item["samples"])):
                    return None
            if not counters(extra, FLOW_FIELDS):
                return None
        else:
            if (set(extra) != {"counters", "relays", "reports_dropped"}
                    or not counters(extra["counters"], NATIVE_FIELDS)
                    or not number(extra["reports_dropped"])
                    or not isinstance(extra["relays"], list) or len(extra["relays"]) > 32):
                return None
            for relay in extra["relays"]:
                if (not isinstance(relay, dict)
                        or set(relay) != {"slot", "accepted", "failed", "not_open", "send_max_us"}
                        or not all(number(item) for item in relay.values())):
                    return None
        return {"kind": kind, **value}
    return None


def read_reports(stream):
    reports = deque(maxlen=64)
    consumed = rejected = 0
    discard = False
    for _ in range(10000):
        line = stream.readline(min(MAX_LINE + 1, MAX_INPUT - consumed))
        if not line:
            break
        consumed += len(line)
        oversized = len(line) > MAX_LINE or not line.endswith(b"\n")
        if discard or oversized:
            discard = not line.endswith(b"\n")
            rejected += 1
        else:
            report = parse_report(line.decode("utf-8", errors="replace"))
            if report is None:
                rejected += 1
            else:
                reports.append(report)
        if consumed >= MAX_INPUT:
            break
    return {"reports": list(reports), "rejected_chunks": rejected,
            "bytes_scanned": consumed}


def main():
    result = read_reports(sys.stdin.buffer)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["reports"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
