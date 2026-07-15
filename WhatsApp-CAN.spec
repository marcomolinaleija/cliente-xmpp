from __future__ import annotations

import os
import shutil
from pathlib import Path

import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH)
DIAGNOSTIC_BUILD = os.environ.get("WHATSAPP_CAN_CONSOLE_BUILD") == "1"
APP_NAME = "WhatsApp-CAN-diagnostico" if DIAGNOSTIC_BUILD else "WhatsApp-CAN"
XMPP_PLUGINS = (
    "xep_0030",
    "xep_0045",
    "xep_0048",
    "xep_0049",
    "xep_0050",
    "xep_0060",
    "xep_0084",
    "xep_0085",
    "xep_0128",
    "xep_0163",
    "xep_0184",
    "xep_0199",
    "xep_0223",
    "xep_0249",
    "xep_0280",
    "xep_0297",
    "xep_0313",
    "xep_0333",
    "xep_0363",
    "xep_0402",
    "xep_0490",
    "xep_0492",
)


def required_file(path: Path, description: str) -> Path:
    if not path.is_file():
        raise SystemExit(f"Falta {description}: {path}")
    return path


libmpv = required_file(ROOT / "cliente_xmpp" / "lib" / "libmpv-2.dll", "libmpv")
nvda_controller = required_file(
    ROOT / "cliente_xmpp" / "lib" / "nvdaControllerClient64.dll",
    "el controlador de NVDA",
)
ffprobe = shutil.which("ffprobe")
ffmpeg = required_file(Path(imageio_ffmpeg.get_ffmpeg_exe()), "ffmpeg")
required_file(ROOT / "dist" / "update.exe", "update.exe")
if not ffprobe:
    raise SystemExit("No se encontró ffprobe en PATH; es obligatorio para el build de Windows.")

hiddenimports = collect_submodules("keyring.backends")
hiddenimports += collect_submodules("windows_toasts")
for plugin in XMPP_PLUGINS:
    hiddenimports += collect_submodules(f"slixmpp.plugins.{plugin}")

datas = collect_data_files("imageio_ffmpeg")
datas += collect_data_files("rlottie_python")
datas += collect_data_files("_sounddevice_data")
datas += [
    (str(path), "cliente_xmpp/assets/audio")
    for path in (ROOT / "cliente_xmpp" / "assets" / "audio").glob("*.mp3")
]
binaries = [
    (str(libmpv), "cliente_xmpp/lib"),
    (str(nvda_controller), "cliente_xmpp/lib"),
    (str(ffmpeg), "bin"),
    (str(ffprobe), "bin"),
]

a = Analysis(
    [str(ROOT / "cliente_xmpp" / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=DIAGNOSTIC_BUILD,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "windows_version_info.txt"),
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
