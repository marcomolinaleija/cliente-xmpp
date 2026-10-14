from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import update  # noqa: E402


def expected_hash(checksum_path: Path) -> str:
    text = checksum_path.read_text(encoding="utf-8")
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise RuntimeError(f"Checksum inválido: {checksum_path}")
    return match.group(1).lower()


def validate_runtime(payload: Path) -> None:
    libraries = payload / "_internal" / "cliente_xmpp" / "lib"
    for name in ("libmpv-2.dll", "nvdaControllerClient64.dll"):
        path = libraries / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"El paquete final no contiene {name}.")

    ffmpeg_candidates = [payload / "bin" / "ffmpeg.exe"]
    ffmpeg_candidates.extend(
        (payload / "_internal" / "imageio_ffmpeg" / "binaries").glob("ffmpeg*.exe")
    )
    if not any(path.is_file() and path.stat().st_size > 0 for path in ffmpeg_candidates):
        raise RuntimeError("El paquete final no contiene un ejecutable FFmpeg para Windows.")


def validate_release(zip_path: Path, checksum_path: Path) -> None:
    if checksum_path.name != f"{zip_path.name}.sha256":
        raise RuntimeError("El checksum debe llamarse <ZIP>.sha256.")
    update.verify_sha256(zip_path, expected_hash(checksum_path))

    temp_dir = Path(tempfile.mkdtemp(prefix="whatsapp-can-release-validation-"))
    try:
        extracted = temp_dir / "extracted"
        extracted.mkdir()
        update.safe_extract(zip_path, extracted)
        payload = update.payload_root(extracted, "WhatsApp-CAN.exe")
        if not (payload / "update.exe").is_file():
            raise RuntimeError("El paquete final no contiene update.exe.")
        validate_runtime(payload)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida una release de WhatsApp CAN.")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("checksum_path", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_release(args.zip_path.resolve(), args.checksum_path.resolve())
    print(f"Release válida: {args.zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
