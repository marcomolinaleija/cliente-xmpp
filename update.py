from __future__ import annotations

import argparse
import ctypes
import hashlib
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath

import wx

APP_TITLE = "Actualizador de WhatsApp CAN"
TOKEN_ENV = "WHATSAPP_CAN_GITHUB_TOKEN"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ZIP_ENTRIES = 50_000
SHA256_PATTERN = re.compile(r"\b([0-9a-fA-F]{64})\b")


def _log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WhatsApp CAN" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "update.log"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            _log_path(),
            maxBytes=1_000_000,
            backupCount=1,
            encoding="utf-8",
        )
    ],
)


def request_headers(*, binary: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/octet-stream" if binary else "text/plain",
        "User-Agent": "WhatsApp-CAN-Updater/1.0",
    }
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def require_https(url: str) -> None:
    if not url.lower().startswith("https://"):
        raise RuntimeError("La actualización solo admite URLs HTTPS.")


def fetch_expected_sha256(url: str, timeout: float = 30.0) -> str:
    require_https(url)
    request = urllib.request.Request(url, headers=request_headers())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(4097)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("No se pudo descargar el checksum SHA-256.") from exc
    if len(text) > 4096:
        raise RuntimeError("El archivo SHA-256 es demasiado grande.")
    match = SHA256_PATTERN.search(text.decode("ascii", errors="ignore"))
    if not match:
        raise RuntimeError("El archivo de checksum no contiene un SHA-256 válido.")
    return match.group(1).lower()


def download_file(url: str, destination: Path, progress) -> None:
    require_https(url)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(url, headers=request_headers(binary=True))
            with urllib.request.urlopen(request, timeout=60) as response:
                raw_length = response.headers.get("Content-Length")
                expected_size = int(raw_length) if raw_length else 0
                if expected_size > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("El paquete supera el tamaño máximo permitido.")
                downloaded = 0
                with destination.open("wb") as output:
                    while chunk := response.read(1024 * 256):
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            raise RuntimeError("El paquete supera el tamaño máximo permitido.")
                        output.write(chunk)
                        if expected_size:
                            progress(min(100, int(downloaded * 100 / expected_size)))
            if expected_size and downloaded != expected_size:
                raise RuntimeError("La descarga quedó incompleta.")
            progress(100)
            return
        except Exception as exc:
            last_error = exc
            logging.warning("Descarga fallida, intento %s: %s", attempt, exc)
            destination.unlink(missing_ok=True)
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError("No se pudo descargar la actualización tras tres intentos.") from last_error


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError("El SHA-256 del paquete no coincide; no se aplicó la actualización.")


def _safe_member_path(destination: Path, member_name: str) -> Path:
    normalized_name = member_name.replace("\\", "/")
    pure_path = PurePosixPath(normalized_name)
    invalid_part = any(":" in part or "\0" in part for part in pure_path.parts)
    if (
        pure_path.is_absolute()
        or ".." in pure_path.parts
        or not pure_path.parts
        or invalid_part
    ):
        raise RuntimeError(f"Ruta ZIP insegura: {member_name}")
    target = destination.joinpath(*pure_path.parts).resolve()
    try:
        target.relative_to(destination.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Ruta ZIP insegura: {member_name}") from exc
    return target


def safe_extract(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise RuntimeError("El ZIP contiene demasiados archivos.")
        total_size = sum(member.file_size for member in members)
        if total_size > MAX_EXTRACTED_BYTES:
            raise RuntimeError("El contenido extraído supera el tamaño máximo permitido.")

        seen_paths: set[str] = set()
        for member in members:
            unix_mode = member.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise RuntimeError(f"El ZIP contiene un enlace no permitido: {member.filename}")
            target = _safe_member_path(destination, member.filename)
            normalized_target = str(target).casefold()
            if normalized_target in seen_paths:
                raise RuntimeError(f"El ZIP repite una ruta: {member.filename}")
            seen_paths.add(normalized_target)
            if member.is_dir() or member.filename.endswith(("/", "\\")):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def payload_root(extracted_dir: Path, executable_name: str) -> Path:
    current = extracted_dir
    for _ in range(4):
        if (current / executable_name).is_file():
            if not (current / "_internal").is_dir():
                raise RuntimeError("El paquete no contiene la carpeta _internal esperada.")
            if not (current / "update.exe").is_file():
                raise RuntimeError("El paquete no contiene update.exe.")
            return current
        entries = [entry for entry in current.iterdir() if entry.name != "__MACOSX"]
        if len(entries) != 1 or not entries[0].is_dir():
            break
        current = entries[0]
    raise RuntimeError(f"El paquete no contiene {executable_name} en su raíz.")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for attempt in range(6):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 5:
                raise
            time.sleep(0.5)


def replace_installation(payload: Path, app_dir: Path) -> None:
    if not app_dir.is_dir():
        raise RuntimeError(f"No existe el directorio de instalación: {app_dir}")

    suffix = uuid.uuid4().hex
    staging = app_dir.parent / f".{app_dir.name}.staging-{suffix}"
    backup = app_dir.parent / f".{app_dir.name}.backup-{suffix}"
    shutil.copytree(payload, staging)
    moved_old = False
    try:
        os.replace(app_dir, backup)
        moved_old = True
        os.replace(staging, app_dir)
    except Exception:
        if moved_old and backup.exists() and not app_dir.exists():
            os.replace(backup, app_dir)
        raise
    finally:
        if staging.exists():
            _remove_tree(staging)

    try:
        _remove_tree(backup)
    except OSError:
        logging.warning("No se pudo eliminar el respaldo temporal: %s", backup)


def _wait_for_pid_windows(pid: int, timeout_seconds: int) -> bool:
    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, timeout_seconds * 1000) == wait_object_0
    finally:
        kernel32.CloseHandle(handle)


def wait_for_main_process(pid: int, timeout_seconds: int = 30) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if sys.platform == "win32":
        exited = _wait_for_pid_windows(pid, timeout_seconds)
    else:
        deadline = time.monotonic() + timeout_seconds
        exited = False
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                exited = True
                break
            time.sleep(0.25)
    if exited:
        return
    logging.warning("El proceso principal no cerró; se forzará PID %s", pid)
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if sys.platform == "win32" and not _wait_for_pid_windows(pid, 10):
        raise RuntimeError("WhatsApp CAN no pudo cerrarse para aplicar la actualización.")


def can_replace_installation(app_dir: Path) -> bool:
    probe = app_dir.parent / f".whatsapp-can-write-test-{uuid.uuid4().hex}"
    try:
        probe.mkdir()
        probe.rmdir()
        return True
    except OSError:
        return False


def relaunch_elevated() -> bool:
    if sys.platform != "win32":
        return False
    original_args = [argument for argument in sys.argv[1:] if argument != "--elevated"]
    original_args.append("--elevated")
    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = subprocess.list2cmdline(original_args)
    else:
        executable = sys.executable
        params = subprocess.list2cmdline([str(Path(__file__).resolve()), *original_args])
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        params,
        tempfile.gettempdir(),
        1,
    )
    return result > 32


class UpdateFrame(wx.Frame):
    def __init__(self) -> None:
        style = wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        super().__init__(None, title=APP_TITLE, size=(560, 190), style=style)
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(panel, label="Preparando la actualización…")
        self.status.SetName("Estado de la actualización")
        self.progress = wx.Gauge(panel, range=100)
        layout.Add(self.status, 0, wx.ALL | wx.EXPAND, 16)
        layout.Add(self.progress, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 16)
        panel.SetSizer(layout)
        self.Centre()
        self.Show()
        self.status.SetFocus()

    def set_status(self, text: str) -> None:
        logging.info(text)
        wx.CallAfter(self.status.SetLabel, text)

    def set_progress(self, value: int) -> None:
        wx.CallAfter(self.progress.SetValue, max(0, min(100, int(value))))


def apply_update(args: argparse.Namespace, frame: UpdateFrame) -> None:
    work_dir = Path(tempfile.mkdtemp(prefix="whatsapp-can-update-"))
    try:
        frame.set_status("Esperando a que WhatsApp CAN se cierre…")
        wait_for_main_process(args.pid)

        frame.set_status("Descargando la actualización…")
        archive_path = work_dir / "update.zip"
        download_file(args.download_url, archive_path, frame.set_progress)

        frame.set_status("Verificando la integridad del paquete…")
        expected_hash = fetch_expected_sha256(args.checksum_url)
        verify_sha256(archive_path, expected_hash)

        frame.set_status("Validando los archivos…")
        extracted = work_dir / "extracted"
        extracted.mkdir()
        safe_extract(archive_path, extracted)
        payload = payload_root(extracted, args.exe)

        frame.set_status("Instalando la nueva versión…")
        replace_installation(payload, args.app_dir)

        executable = args.app_dir / args.exe
        if not executable.is_file():
            raise RuntimeError(f"La instalación final no contiene {args.exe}.")
        frame.set_status("Actualización completa. Abriendo WhatsApp CAN…")
        subprocess.Popen([str(executable)], cwd=args.app_dir, close_fds=True)
        time.sleep(1)
        wx.CallAfter(frame.Close)
    except Exception as exc:
        logging.exception("La actualización falló")

        def show_error(error: Exception = exc) -> None:
            wx.MessageBox(
                f"No se pudo actualizar WhatsApp CAN. La instalación anterior se conservó.\n\n"
                f"{error}\n\n"
                f"Registro: {_log_path()}",
                "Actualización fallida",
                wx.OK | wx.ICON_ERROR,
                frame,
            )
            frame.Close()

        wx.CallAfter(show_error)
    finally:
        try:
            _remove_tree(work_dir)
        except OSError:
            logging.warning("No se pudo limpiar %s", work_dir)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--checksum-url", required=True)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--exe", default="WhatsApp-CAN.exe")
    parser.add_argument("--release-url", default="")
    parser.add_argument("--elevated", action="store_true")
    args = parser.parse_args(argv)
    args.app_dir = args.app_dir.resolve()
    require_https(args.download_url)
    require_https(args.checksum_url)
    return args


def main() -> int:
    args = parse_args(sys.argv[1:])
    if not can_replace_installation(args.app_dir) and not args.elevated:
        if relaunch_elevated():
            return 0
        wx.App(False)
        wx.MessageBox(
            "WhatsApp CAN necesita permisos de administrador para actualizar esta instalación.",
            "Permisos necesarios",
            wx.OK | wx.ICON_ERROR,
        )
        return 2

    app = wx.App(False)
    frame = UpdateFrame()
    threading.Thread(target=apply_update, args=(args, frame), daemon=True).start()
    app.MainLoop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
