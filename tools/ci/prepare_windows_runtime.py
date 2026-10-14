"""Recover only the two ignored runtime DLLs from a hash-pinned public release."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("windows-runtime.json")
REQUIRED_FILES = {"libmpv-2.dll", "nvdaControllerClient64.dll"}


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def install_runtime(archive_path: Path, manifest: dict, root: Path) -> None:
    if set(manifest["files"]) != REQUIRED_FILES:
        raise ValueError("El manifiesto debe contener únicamente las dos DLL del runtime")
    if sha256(archive_path) != manifest["sha256"]:
        raise ValueError("El ZIP del runtime no coincide con su SHA-256 fijado")
    target = root / "cliente_xmpp" / "lib"
    if target.resolve() != root.resolve() / "cliente_xmpp" / "lib":
        raise ValueError("La raíz del runtime no puede seguir enlaces")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        # Verify every required member before writing any file.
        for name, item in manifest["files"].items():
            with archive.open(item["member"]) as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise ValueError(f"SHA-256 incorrecto para {name}")
            destination = target / name
            if destination.is_symlink():
                raise ValueError(f"No se seguirán enlaces del runtime: {name}")
            if destination.exists() and sha256(destination) != actual:
                raise ValueError(f"No se sobrescribirá una DLL existente distinta: {name}")
        for name, item in manifest["files"].items():
            destination = target / name
            if destination.exists():
                continue
            partial = destination.with_suffix(".dll.part")
            created = False
            try:
                with archive.open(item["member"]) as source, partial.open("xb") as output:
                    created = True
                    shutil.copyfileobj(source, output)
                if sha256(partial) != item["sha256"]:
                    raise ValueError(f"La copia de {name} no pasó su validación")
                partial.replace(destination)
            finally:
                if created:
                    partial.unlink(missing_ok=True)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not manifest["url"].startswith(
        "https://github.com/marcomolinaleija/cliente-xmpp/releases/download/"
    ):
        raise ValueError("El runtime debe proceder de una release HTTPS de este repositorio")
    with tempfile.TemporaryDirectory(prefix="can-ci-runtime-") as folder:
        archive = Path(folder) / "runtime.zip"
        with urlopen(manifest["url"], timeout=120) as response, archive.open("xb") as output:
            shutil.copyfileobj(response, output)
        install_runtime(archive, manifest, ROOT)
    print("Runtime Windows preparado y verificado por SHA-256")


if __name__ == "__main__":
    main()
