"""Offline startup probe for the executable extracted from a release ZIP."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cliente_xmpp import __version__


def isolated_root() -> Path:
    value = os.environ.get("WHATSAPP_CAN_SMOKE_ROOT", "")
    if not value:
        raise RuntimeError("El smoke test requiere un perfil temporal aislado")
    root = Path(value).resolve()
    if Path.home().resolve() != root / "profile":
        raise RuntimeError("El perfil del smoke test no está aislado")
    for name, relative in (("APPDATA", "roaming"), ("LOCALAPPDATA", "local")):
        if Path(os.environ.get(name, "")).resolve() != root / relative:
            raise RuntimeError(f"{name} no está aislado")
    if (root / "profile" / ".cliente-xmpp").exists():
        raise RuntimeError("El smoke test requiere un perfil nuevo")
    return root


def check_runtime() -> list[str]:
    from cliente_xmpp.accessibility.speaker import NvdaSpeaker
    from cliente_xmpp.audio.opus import ffmpeg_path
    from cliente_xmpp.audio.player import MpvAudioPlayer
    from cliente_xmpp.audio.process import hidden_subprocess_kwargs

    ffmpeg = Path(ffmpeg_path()).resolve()
    if getattr(sys, "frozen", False):
        ffmpeg.relative_to(Path(sys.executable).resolve().parent)
    result = subprocess.run(
        [str(ffmpeg), "-version"], check=True, capture_output=True, timeout=15,
        **hidden_subprocess_kwargs(),
    )
    if not result.stdout.startswith(b"ffmpeg version"):
        raise RuntimeError("FFmpeg no respondió con su versión")

    player = MpvAudioPlayer()
    dll = player._ensure_dll()
    handle = dll.mpv_create()
    if not handle:
        raise RuntimeError("libmpv no pudo crear un reproductor")
    try:
        for key in (b"ao", b"vo"):
            if dll.mpv_set_option_string(handle, key, b"null") < 0:
                raise RuntimeError("libmpv no aceptó la salida de prueba")
        if dll.mpv_initialize(handle) < 0:
            raise RuntimeError("libmpv no pudo inicializarse")
    finally:
        dll.mpv_terminate_destroy(handle)
    speaker = NvdaSpeaker()
    if speaker._ensure_dll() is None:
        raise RuntimeError(f"No se pudo cargar la DLL de NVDA: {speaker._load_error}")
    return ["ffmpeg", "libmpv", "nvda"]


def check_gui(root: Path) -> list[str]:
    import wx

    from cliente_xmpp.config.settings import APP_DIR
    from cliente_xmpp.ui.emoji_picker_dialog import EmojiPickerDialog
    from cliente_xmpp.ui.main_window import MainWindow

    if APP_DIR.resolve() != root / "profile" / ".cliente-xmpp":
        raise RuntimeError("Las rutas del cliente no apuntan al perfil temporal")
    app = wx.App(False)
    window = MainWindow(startup_checks=False)
    checks: list[str] = []
    errors: list[Exception] = []

    def probe() -> None:
        try:
            if window.conversation.compose is None:
                raise RuntimeError("No se creó el cuadro de edición")
            checks.append("window")
            dialog = EmojiPickerDialog(window, window.settings_store)
            try:
                if dialog.results.GetCount() == 0:
                    raise RuntimeError("El panel de emojis no contiene entradas")
            finally:
                dialog.Destroy()
            checks.append("emojis")
            if window.message_store.load_chats("smoke@example.test"):
                raise RuntimeError("El perfil temporal contiene conversaciones")
            checks.append("sqlite")
            assets = Path(__file__).resolve().parents[1] / "assets"
            if not (assets / "GUIA_USO.html").is_file() or not list(
                (assets / "audio").glob("*.mp3")
            ):
                raise RuntimeError("Faltan recursos de documentación o sonidos")
            checks.append("assets")
        except Exception as exc:
            errors.append(exc)
        finally:
            window.Close(force=True)
            app.ExitMainLoop()

    wx.CallLater(250, probe)
    try:
        app.MainLoop()
    finally:
        if window and not window.IsBeingDeleted():
            window.Close(force=True)
        app.ProcessPendingEvents()
    if errors:
        raise errors[0]
    return checks


def run_smoke_test(report_path: str) -> int:
    report: dict[str, object] = {
        "schema": 1, "version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
        "status": "failed", "checks": [],
    }
    try:
        root = isolated_root()
        # Native multimedia startup runs outside the wx thread and needs no audio device.
        with ThreadPoolExecutor(max_workers=1) as executor:
            checks = executor.submit(check_runtime).result()
        report["checks"] = checks + check_gui(root)
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["status"] == "passed" else 1
