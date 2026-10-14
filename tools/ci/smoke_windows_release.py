"""Execute the packaged client with a fresh profile and no development tools on PATH."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import update  # noqa: E402
from tools.validate_release import validate_release  # noqa: E402

REQUIRED_CHECKS = {"ffmpeg", "libmpv", "nvda", "window", "emojis", "sqlite", "assets"}


def isolated_environment(root: Path) -> dict[str, str]:
    names = {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "OS", "PATHEXT"}
    environment = {name: value for name, value in os.environ.items() if name.upper() in names}
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    environment["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    for name, folder in (("USERPROFILE", "profile"), ("APPDATA", "roaming"),
                         ("LOCALAPPDATA", "local"), ("TEMP", "temp"), ("TMP", "temp")):
        path = root / folder
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    environment["HOME"] = environment["USERPROFILE"]
    environment["PYTHONNOUSERSITE"] = "1"
    environment["WHATSAPP_CAN_SMOKE_ROOT"] = str(root)
    return environment


def verify_report(report: dict, version: str) -> None:
    if (report.get("schema") != 1 or report.get("version") != version
            or report.get("frozen") is not True or report.get("status") != "passed"
            or set(report.get("checks", [])) != REQUIRED_CHECKS):
        raise RuntimeError("El ejecutable no confirmó todas las comprobaciones del smoke test")


def smoke_release(zip_path: Path, report_path: Path, version: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)
    diagnostic: dict[str, object] = {"schema": 1, "status": "failed", "version": version}
    try:
        validate_release(zip_path, zip_path.with_name(zip_path.name + ".sha256"))
        with tempfile.TemporaryDirectory(prefix="can-packaged-smoke-") as folder:
            root = Path(folder)
            extracted = root / "extracted"
            extracted.mkdir()
            update.safe_extract(zip_path, extracted)
            payload = update.payload_root(extracted, "WhatsApp-CAN.exe")
            child_report = root / "client-report.json"
            process = subprocess.run(
                [str(payload / "WhatsApp-CAN.exe"), "--smoke-test", str(child_report)],
                cwd=root, env=isolated_environment(root), timeout=90, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            diagnostic["exit_code"] = process.returncode
            if child_report.is_file():
                diagnostic["client"] = json.loads(child_report.read_text(encoding="utf-8"))
            if process.returncode != 0:
                raise RuntimeError(f"El cliente terminó con código {process.returncode}")
            verify_report(diagnostic.get("client", {}), version)
            diagnostic["status"] = "passed"
    except Exception as exc:
        diagnostic["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2),
                               encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from tools.ci.release_windows import source_version

    version = source_version(args.tag, ROOT)
    smoke_release(ROOT / "release" / f"WhatsApp-CAN-{version}.zip",
                  args.report.resolve(), version)
    print("Smoke test del ZIP aprobado: GUI, SQLite, recursos, FFmpeg, libmpv y DLL de NVDA")


if __name__ == "__main__":
    main()
