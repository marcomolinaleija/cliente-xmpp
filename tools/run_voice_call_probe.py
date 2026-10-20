"""Build and test the isolated v35 voice probe; never connect a WhatsApp account."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

try:
    from tools.patch_wacalls_voice_activity import TRANSFORMS as CODEC_TRANSFORMS
    from tools.patch_wacalls_voice_metrics import TRANSFORMS as MEDIA_TRANSFORMS
except ModuleNotFoundError:
    from patch_wacalls_voice_activity import TRANSFORMS as CODEC_TRANSFORMS
    from patch_wacalls_voice_metrics import TRANSFORMS as MEDIA_TRANSFORMS

if __package__:
    from tools.patch_wacalls_voice_lifecycle import DISPOSE, manager, relay, signaling
else:
    from patch_wacalls_voice_lifecycle import DISPOSE, manager, relay, signaling

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "voice_calls"
RESULTS = ROOT / ".test-results" / "voice-call-probe"
IMAGE = "cliente-xmpp-voice-probe:v35-metrics-offline"
INTEGRATION_IMAGE = "cliente-xmpp-bridge:v35-metrics-local"
PACKAGES = ("./internal/voip/...", "./internal/wa", "./canprobe", "./codecprobe", "./voicecore")


def verify_archive(path: Path, expected: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError(f"Upstream source checksum mismatch: {digest}")


def probe_fingerprint() -> str:
    sources = [EXPERIMENT / "Dockerfile", EXPERIMENT / "wacalls-srtp-auth.patch",
               EXPERIMENT / "upstream.json", EXPERIMENT / "codec-reference.json"]
    sources.append(ROOT / "tools/patch_wacalls_voice_lifecycle.py")
    sources.append(ROOT / "tools/patch_wacalls_voice_metrics.py")
    sources.append(ROOT / "tools/patch_wacalls_voice_activity.py")
    sources.extend(sorted((EXPERIMENT / "instrumentation").rglob("*.go")))
    for directory in ("canprobe", "codecprobe", "voicecore", "upstream_tests"):
        sources.extend(sorted((EXPERIMENT / directory).glob("*.go")))
    digest = hashlib.sha256()
    for source in sources:
        label = (source.relative_to(EXPERIMENT).as_posix()
                 if source.is_relative_to(EXPERIMENT) else "tools/" + source.name)
        digest.update(label.encode() + b"\0")
        digest.update(source.read_bytes() + b"\0")
    return digest.hexdigest()


def summarize_go_tests(lines) -> dict:
    passed, skipped, failed, packages = set(), set(), set(), set()
    started_packages, finished_packages, running_tests = set(), set(), set()
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Go test output is not a complete JSON event stream") from exc
        package, test = event.get("Package", ""), event.get("Test", "")
        action = event.get("Action")
        identity = f"{package}/{test}" if test else package
        if action == "start" and package:
            started_packages.add(package)
        elif action == "run" and test:
            running_tests.add(identity)
        if action in ("pass", "skip", "fail"):
            if test:
                running_tests.discard(identity)
            elif package:
                finished_packages.add(package)
        if action == "fail":
            failed.add(identity)
        elif action == "pass" and test:
            passed.add(identity)
        elif action == "skip" and test:
            skipped.add(identity)
        elif action == "pass" and package:
            packages.add(package)
    if failed:
        raise ValueError("Go tests failed: " + ", ".join(sorted(failed)[:12]))
    if not passed or not packages or running_tests or started_packages - finished_packages:
        raise ValueError("Go tests produced an empty or incomplete selection")
    return {"passed": len(passed), "skipped": len(skipped), "packages": sorted(packages)}


def isolated_run_args(image: str) -> list[str]:
    return [
        "run", "--rm", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--memory", "3g", "--cpus", "2", image,
    ]


def prepare_context(archive: Path, output: Path, manifest: dict) -> Path:
    verify_archive(archive, manifest["source_sha256"])
    context = output / "context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, context / "upstream.tar.gz")
    (context / "upstream.sha256").write_text(
        manifest["source_sha256"] + "  upstream.tar.gz\n", encoding="ascii"
    )
    shutil.copyfile(EXPERIMENT / "Dockerfile", context / "Dockerfile")
    (context / "probe-source.sha256").write_text(probe_fingerprint() + "\n", encoding="ascii")
    shutil.copyfile(EXPERIMENT / "Dockerfile.integration", context / "Dockerfile.integration")
    for name in ("patch_slidge_voice.py", "bridge_voice.py", "gopy_whatsapp_exports.map",
                 "smoke_voice_integration.py", "smoke_voice_activation.py", "read_voice_flow.py",
                 "bridge_voice_lifecycle_test.go", "voice_bridge_entrypoint.py"):
        shutil.copyfile(ROOT / "tools" / name, context / name)
    shutil.copyfile(EXPERIMENT / "wacalls-srtp-auth.patch", context / "wacalls-srtp-auth.patch")
    changes = []
    with tarfile.open(archive, "r:gz") as upstream:
        prefix = upstream.getmembers()[0].name.split("/")[0]
        for name, transform in (("callmanager.go", manager),
                                ("callmanager_relay.go", relay),
                                ("callmanager_signaling.go", signaling)):
            path = "internal/voip/call/" + name
            original = upstream.extractfile(prefix + "/" + path).read().decode()
            changes.extend(difflib.unified_diff(
                original.splitlines(keepends=True), transform(original).splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path,
            ))
    changes.extend(difflib.unified_diff(
        [], DISPOSE.splitlines(keepends=True), fromfile="/dev/null",
        tofile="b/internal/voip/call/can_dispose.go",
    ))
    (context / "wacalls-lifecycle.patch").write_text(
        "".join(changes), encoding="utf-8", newline="\n",
    )
    changes = []
    with tarfile.open(archive, "r:gz") as upstream:
        for path, transform in MEDIA_TRANSFORMS.items():
            original = upstream.extractfile(prefix + "/" + path).read().decode()
            changes.extend(difflib.unified_diff(
                original.splitlines(keepends=True), transform(original).splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path,
            ))
    (context / "wacalls-media.patch").write_text("".join(changes), encoding="utf-8", newline="\n")
    changes = []
    with tarfile.open(archive, "r:gz") as upstream:
        for path, transform in CODEC_TRANSFORMS.items():
            original = upstream.extractfile(prefix + "/" + path).read().decode()
            changes.extend(difflib.unified_diff(
                original.splitlines(keepends=True), transform(original).splitlines(keepends=True),
                fromfile="a/" + path, tofile="b/" + path,
            ))
    (context / "wacalls-codec.patch").write_text("".join(changes), encoding="utf-8", newline="\n")
    shutil.copytree(EXPERIMENT / "instrumentation", context / "instrumentation", dirs_exist_ok=True)
    reference = context / "codec-reference"
    reference.mkdir(exist_ok=True)
    shutil.copyfile(EXPERIMENT / "codec-reference.json", reference / "manifest.json")
    for name in ("canprobe", "codecprobe", "voicecore", "upstream_tests"):
        probe = context / name
        probe.mkdir(exist_ok=True)
        for source in (EXPERIMENT / name).glob("*.go"):
            shutil.copyfile(source, probe / source.name)
    return context


def prepare_codec_reference(context: Path) -> None:
    """Fetch only hash-pinned public synthetic fixtures, never user audio."""
    manifest = json.loads((EXPERIMENT / "codec-reference.json").read_text(encoding="utf-8"))
    for name, metadata in manifest["files"].items():
        target = context / "codec-reference" / name
        if not target.exists():
            part = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(manifest["base_url"] + name, timeout=30) as response:
                data = response.read(metadata["bytes"] + 1)
            if (len(data) != metadata["bytes"]
                    or hashlib.sha256(data).hexdigest() != metadata["sha256"]):
                raise ValueError("Synthetic codec reference checksum mismatch")
            part.write_bytes(data)
            part.replace(target)
        data = target.read_bytes()
        if len(data) != metadata["bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise ValueError("Synthetic codec reference checksum mismatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Explicitly build the isolated image")
    parser.add_argument("--codec-reference", action="store_true",
                        help="Compare against pinned public synthetic WhatsApp codec fixtures")
    parser.add_argument("--wsl", help="Run Docker in this existing WSL distribution")
    parser.add_argument(
        "--integration", action="store_true",
        help="Also build and smoke the isolated v35 binding with native voice lifecycle",
    )
    parser.add_argument(
        "--source-archive", type=Path, help="Use an already downloaded pinned archive"
    )
    args = parser.parse_args(argv)
    manifest = json.loads((EXPERIMENT / "upstream.json").read_text(encoding="utf-8"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    docker = ["wsl.exe", "-d", args.wsl, "--", "docker"] if args.wsl else ["docker"]
    report = {
        "bridge_image": manifest["bridge_image"],
        "upstream_commit": manifest["source_commit"],
        "live_call_verified": False,
        "status": "failed",
    }
    started = time.monotonic()

    def run(command: list[str], filename: str, timeout: int = 1200) -> Path:
        logfile = RESULTS / filename
        print(f"Running {filename}; full output: {logfile}", flush=True)
        with logfile.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(
                command, stdout=stream, stderr=subprocess.STDOUT,
                text=True, timeout=timeout, check=False,
            )
        if completed.returncode:
            tail = logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-35:]
            raise RuntimeError(f"{filename} exited {completed.returncode}\n" + "\n".join(tail))
        return logfile

    try:
        if args.build or args.integration:
            archive = args.source_archive or RESULTS / "upstream.tar.gz"
            if not archive.exists():
                if args.source_archive:
                    raise FileNotFoundError(archive)
                part = archive.with_suffix(".part")
                with urllib.request.urlopen(manifest["source_url"], timeout=60) as response:
                    with part.open("wb") as stream:
                        shutil.copyfileobj(response, stream)
                verify_archive(part, manifest["source_sha256"])
                part.replace(archive)
            context = prepare_context(archive, RESULTS, manifest)
            if args.codec_reference:
                prepare_codec_reference(context)
            context_arg = str(context)
            if args.wsl:
                translated = subprocess.run(
                    ["wsl.exe", "-d", args.wsl, "--", "wslpath", "-a", context.as_posix()],
                    capture_output=True, text=True, check=True,
                )
                context_arg = translated.stdout.strip()
            if args.build:
                run(docker + [
                    "build", "--progress", "plain", "--tag", IMAGE,
                    "--build-arg", "BRIDGE_IMAGE=" + manifest["bridge_image"],
                    "--build-arg", "GO_IMAGE=" + manifest["go_image"], context_arg,
                ], "build.log")
        inspection = run(
            docker + ["image", "inspect", "--format", "{{.Id}}", IMAGE], "image-id.log"
        )
        report["image_id"] = inspection.read_text(encoding="utf-8").strip()
        isolated = isolated_run_args(IMAGE)
        fingerprint = run(docker + isolated[:-1] + ["--entrypoint", "cat", isolated[-1],
                          "/opt/can-probe-source.sha256"], "probe-source.sha256")
        report["probe_source_sha256"] = fingerprint.read_text(encoding="ascii").strip()
        if report["probe_source_sha256"] != probe_fingerprint():
            raise ValueError("Probe image does not match current sources; rebuild with --build")
        for label, flags in (("go", []), ("race", ["-race"])):
            logfile = run(
                docker + isolated_run_args(IMAGE)
                + ["go", "test", "-json", "-count=1", "-timeout=180s", *flags, *PACKAGES],
                label + ".jsonl",
            )
            with logfile.open(encoding="utf-8") as stream:
                report[label] = summarize_go_tests(stream)
            print(f"PASS {label}: {report[label]['passed']} tests", flush=True)
        logfile = run(docker + isolated_run_args(IMAGE), "media.json")
        media = json.loads(logfile.read_text(encoding="utf-8"))
        if (
            media.get("bridge_version") != 35 or media.get("mode") != "offline-synthetic"
            or media.get("directions") != 2 or media.get("frames", 0) < 2
            or not media.get("tampering_rejected") or media.get("live_call_verified") is not False
        ):
            raise ValueError("Incomplete or mislabelled synthetic media result")
        report["media"] = media
        if args.codec_reference:
            isolated = isolated_run_args(IMAGE)
            logfile = run(docker + isolated[:-1] + [
                "--entrypoint", "/usr/local/bin/can-codec-probe", IMAGE,
            ], "codec-reference.json")
            comparison = json.loads(logfile.read_text(encoding="utf-8"))
            reference = json.loads(
                (EXPERIMENT / "codec-reference.json").read_text(encoding="utf-8"))
            if (comparison.get("mode") != "offline-reference-comparison"
                    or comparison.get("reference_commit") != reference["commit"]
                    or comparison.get("frames") != 110
                    or comparison.get("outgoing_independent_decode_verified") is not False
                    or comparison.get("live_call_verified") is not False):
                raise ValueError("Incomplete or mislabelled codec comparison")
            report["codec_reference"] = comparison
            print("Codec baseline completed; differences are NOT a quality pass", flush=True)
        if args.integration:
            run(docker + [
                "build", "--progress", "plain", "--file", context_arg + "/Dockerfile.integration",
                "--tag", INTEGRATION_IMAGE,
                "--build-arg", "BRIDGE_IMAGE=" + manifest["bridge_image"],
                "--build-arg", "PROBE_IMAGE=" + IMAGE, context_arg,
            ], "integration-build.log")
            inspection = run(docker + [
                "image", "inspect", "--format", "{{.Id}}", INTEGRATION_IMAGE,
            ], "integration-image-id.log")
            integration = {"image_id": inspection.read_text(encoding="utf-8").strip()}
            for mode in ("go", "race"):
                isolated = isolated_run_args(INTEGRATION_IMAGE)
                logfile = run(docker + isolated[:-1] + ["--entrypoint", "cat", isolated[-1],
                              f"/opt/voice-tools/voice-native-{mode}.jsonl"],
                              f"native-{mode}.jsonl")
                with logfile.open(encoding="utf-8") as stream:
                    integration[mode] = summarize_go_tests(stream)
                if "codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp" not in (
                    integration[mode]["packages"]
                ):
                    raise ValueError("The native Slidge session lifecycle test did not run")
            isolated = isolated_run_args(INTEGRATION_IMAGE)
            run(docker + isolated[:-1] + ["--entrypoint", "python", isolated[-1], "-B",
                "/opt/voice-tools/smoke_voice_integration.py"], "integration-smoke.log")
            report["integration"] = integration
        report["status"] = "passed"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
        print(f"FAIL voice probe: {exc}", file=sys.stderr)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        path = RESULTS / "summary.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Summary: {path}", flush=True)
    if report["status"] == "passed":
        print("PASS v35 offline media + adapter compatibility; live calls NOT verified")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
