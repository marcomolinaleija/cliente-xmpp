from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import wx

from cliente_xmpp import __version__

LOGGER = logging.getLogger(__name__)
APP_EXECUTABLE = "WhatsApp-CAN.exe"
UPDATER_EXECUTABLE = "update.exe"
DEFAULT_RELEASE_API = (
    "https://api.github.com/repos/marcomolinaleija/cliente-xmpp/releases/latest"
)
UPDATE_API_ENV = "WHATSAPP_CAN_UPDATE_API_URL"
UPDATE_TOKEN_ENV = "WHATSAPP_CAN_GITHUB_TOKEN"
SOURCE_CHECK_ENV = "WHATSAPP_CAN_UPDATE_CHECK"
ASSET_PATTERN = re.compile(r"^WhatsApp-CAN-(?P<version>.+)\.zip$", re.IGNORECASE)
VERSION_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)")


class UpdateCheckError(RuntimeError):
    """Raised when a release is present but is not safe or usable."""


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    tag: str
    notes: str
    download_url: str
    checksum_url: str
    release_url: str


def comparable_version(value: str) -> tuple[int, int, int, int] | None:
    match = VERSION_PATTERN.search(value or "")
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_version = comparable_version(candidate)
    current_version = comparable_version(current)
    return bool(candidate_version and current_version and candidate_version > current_version)


def _request_headers(*, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": f"WhatsApp-CAN/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get(UPDATE_TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _load_release_json(api_url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(api_url, headers=_request_headers())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise UpdateCheckError(
                "El feed de actualizaciones no es público o no está disponible."
            ) from exc
        raise UpdateCheckError(f"GitHub respondió con HTTP {exc.code}.") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateCheckError("No se pudo consultar GitHub.") from exc

    if len(payload) > 2_000_000:
        raise UpdateCheckError("La respuesta de GitHub es demasiado grande.")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("GitHub devolvió una respuesta no válida.") from exc
    if not isinstance(data, dict):
        raise UpdateCheckError("La release de GitHub no tiene el formato esperado.")
    return data


def _https_url(value: object, description: str) -> str:
    url = str(value or "").strip()
    if not url.lower().startswith("https://"):
        raise UpdateCheckError(f"{description} no usa HTTPS.")
    return url


def release_from_payload(payload: dict[str, Any], current_version: str) -> UpdateInfo | None:
    if payload.get("draft") or payload.get("prerelease"):
        return None

    tag = str(payload.get("tag_name") or "").strip()
    if not is_newer_version(tag, current_version):
        return None

    release_version = comparable_version(tag)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateCheckError("La release no contiene una lista de archivos.")

    zip_candidates: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        match = ASSET_PATTERN.fullmatch(str(asset.get("name") or ""))
        if match and comparable_version(match.group("version")) == release_version:
            zip_candidates.append(asset)

    if len(zip_candidates) != 1:
        raise UpdateCheckError(
            "La release debe contener exactamente un WhatsApp-CAN-<versión>.zip."
        )

    zip_asset = zip_candidates[0]
    zip_name = str(zip_asset.get("name"))
    checksum_name = f"{zip_name}.sha256"
    checksum_assets = [
        asset
        for asset in assets
        if isinstance(asset, dict) and str(asset.get("name") or "") == checksum_name
    ]
    if len(checksum_assets) != 1:
        raise UpdateCheckError(f"Falta el archivo obligatorio {checksum_name}.")

    return UpdateInfo(
        version=".".join(str(part) for part in release_version[:3]),
        tag=tag,
        notes=str(payload.get("body") or "").strip()[:12_000],
        download_url=_https_url(zip_asset.get("browser_download_url"), "El ZIP"),
        checksum_url=_https_url(
            checksum_assets[0].get("browser_download_url"),
            "El checksum",
        ),
        release_url=_https_url(payload.get("html_url"), "La página de la release"),
    )


def check_for_update(
    current_version: str = __version__,
    *,
    api_url: str | None = None,
    timeout: float = 10.0,
) -> UpdateInfo | None:
    url = api_url or os.environ.get(UPDATE_API_ENV, "").strip() or DEFAULT_RELEASE_API
    return release_from_payload(_load_release_json(url, timeout), current_version)


def _runtime_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def should_check_at_startup() -> bool:
    return bool(
        (
            sys.platform == "win32"
            and getattr(sys, "frozen", False)
            and Path(sys.executable).name.lower() == APP_EXECUTABLE.lower()
        )
        or os.environ.get(SOURCE_CHECK_ENV) == "1"
    )


def _show_update_dialog(parent: wx.Window, update: UpdateInfo) -> bool:
    dialog = wx.Dialog(
        parent,
        title="Actualización disponible de WhatsApp CAN",
        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
    )
    layout = wx.BoxSizer(wx.VERTICAL)
    prompt = wx.StaticText(
        dialog,
        label=(
            f"Está disponible WhatsApp CAN {update.version}. "
            "¿Quieres descargarla e instalarla ahora?"
        ),
    )
    prompt.Wrap(620)
    layout.Add(prompt, 0, wx.ALL | wx.EXPAND, 12)

    notes = update.notes or "Esta release no incluye notas de versión."
    notes_box = wx.TextCtrl(
        dialog,
        value=notes,
        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        size=(640, 300),
    )
    notes_box.SetName("Notas de la actualización")
    layout.Add(notes_box, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
    buttons = dialog.CreateButtonSizer(wx.YES_NO)
    if buttons is not None:
        layout.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.ALIGN_RIGHT, 12)
    dialog.SetSizerAndFit(layout)
    dialog.SetMinSize((560, 360))
    dialog.CentreOnParent()
    yes_button = dialog.FindWindow(wx.ID_YES)
    if yes_button is not None:
        yes_button.SetLabel("Sí, actualizar")
        yes_button.SetDefault()
        yes_button.SetFocus()
    no_button = dialog.FindWindow(wx.ID_NO)
    if no_button is not None:
        no_button.SetLabel("Ahora no")
    try:
        return dialog.ShowModal() == wx.ID_YES
    finally:
        dialog.Destroy()


def _temporary_updater_copy(updater_path: Path) -> Path:
    target_dir = Path(tempfile.gettempdir()) / "WhatsApp-CAN" / "updater"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"update-{os.getpid()}-{uuid.uuid4().hex}.exe"
    shutil.copy2(updater_path, target)
    return target


def _launch_updater(parent: wx.Window, update: UpdateInfo) -> None:
    app_dir = _runtime_directory()
    installed_updater = app_dir / UPDATER_EXECUTABLE
    if not installed_updater.is_file():
        raise FileNotFoundError(f"No se encontró {UPDATER_EXECUTABLE} junto a la aplicación.")

    updater = _temporary_updater_copy(installed_updater)
    command = [
        str(updater),
        "--download-url",
        update.download_url,
        "--checksum-url",
        update.checksum_url,
        "--app-dir",
        str(app_dir),
        "--pid",
        str(os.getpid()),
        "--exe",
        APP_EXECUTABLE,
        "--release-url",
        update.release_url,
    ]
    subprocess.Popen(command, cwd=tempfile.gettempdir(), close_fds=True)
    parent.Close()


def _offer_update(parent: wx.Window, update: UpdateInfo) -> None:
    if not parent or parent.IsBeingDeleted():
        return
    if not _show_update_dialog(parent, update):
        return
    try:
        _launch_updater(parent, update)
    except Exception as exc:
        LOGGER.exception("No se pudo iniciar update.exe")
        wx.MessageBox(
            f"No se pudo iniciar el actualizador de WhatsApp CAN:\n{exc}",
            "Error de actualización",
            wx.OK | wx.ICON_ERROR,
            parent,
        )


def start_startup_update_check(parent: wx.Window) -> None:
    """Check once per process without delaying or interrupting the initial UI."""
    if not should_check_at_startup():
        return
    if not (_runtime_directory() / UPDATER_EXECUTABLE).is_file():
        LOGGER.info("Se omite la búsqueda de actualizaciones: falta update.exe")
        return

    def worker() -> None:
        try:
            update = check_for_update()
        except UpdateCheckError as exc:
            LOGGER.info("No se pudo comprobar actualizaciones: %s", exc)
            return
        except Exception:
            LOGGER.exception("Fallo inesperado al comprobar actualizaciones")
            return
        if update is not None:
            wx.CallAfter(_offer_update, parent, update)

    threading.Thread(
        target=worker,
        name="whatsapp-can-update-check",
        daemon=True,
    ).start()
