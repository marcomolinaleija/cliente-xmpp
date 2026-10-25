"""Summarize content-free R7 observations; no credentials, messages or raw logs printed."""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path


def summarize(root):
    deployment = json.loads((root / "deployment.json").read_text())
    begin = datetime.fromisoformat(deployment["started"].replace("Z", "+00:00"))
    samples = []
    for path in (root / "observation").glob("timeline.jsonl*"):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
                if datetime.fromisoformat(row["utc"]) >= begin:
                    samples.append(row)
            except (ValueError, KeyError):
                continue
    samples.sort(key=lambda row: row["utc"])
    errors, health, local_failures = Counter(), Counter(), []
    identities, collection_errors = set(), 0
    for row in samples:
        errors.update(row.get("errors", {}))
        bridge = row.get("bridge", {})
        identities.add((bridge.get("id"), bridge.get("started")))
        health.update([bridge.get("health", "missing")])
        collection_errors += "collection_error" in row
        for port, result in row.get("local_tcp", {}).items():
            if not result["ok"]:
                local_failures.append({"utc": row["utc"], "port": port})
    current = json.loads(subprocess.check_output(
        ["docker", "inspect", "slidge-whatsapp"], text=True))[0]
    times = [datetime.fromisoformat(row["utc"]).timestamp() for row in samples]
    return {
        "deployment": deployment,
        "samples": len(samples), "first": samples[0]["utc"] if samples else None,
        "last": samples[-1]["utc"] if samples else None,
        "observed_seconds": round(times[-1] - times[0]) if len(times) > 1 else 0,
        "largest_gap_seconds": round(max((b - a for a, b in zip(
            times, times[1:], strict=False)), default=0)),
        "health_counts": dict(health), "errors": dict(errors),
        "local_failures_count": len(local_failures), "local_failures_first": local_failures[:10],
        "identities_count": len(identities), "collection_errors": collection_errors,
        "current": {"id": current["Id"], "image": current["Image"],
                    "started": current["State"]["StartedAt"],
                    "running": current["State"]["Running"],
                    "health": current["State"].get("Health", {}).get("Status", "none"),
                    "restart_count": current["RestartCount"]},
        "last_network": {k: samples[-1].get(k) for k in (
            "conntrack", "incoming_syn_total", "capture_running", "interface")}
            if samples else {},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory), indent=2))
