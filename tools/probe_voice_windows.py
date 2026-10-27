"""Inspect the call window's real MSAA objects; optionally smoke muted local PCM.

No XMPP connection, WhatsApp account, stored samples or transmitted microphone
audio. --audio opens the default duplex device with capture muted and output zero.
Uses Windows SDK interfaces, without an additional COM package dependency.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                ("d", ctypes.c_ubyte*8)]


class _Record(ctypes.Structure):
    _fields_ = [("record", ctypes.c_void_p), ("info", ctypes.c_void_p)]


class _Value(ctypes.Union):
    _fields_ = [("integer", ctypes.c_int32), ("wide", ctypes.c_int64),
                ("pointer", ctypes.c_void_p), ("record", _Record)]


class _Variant(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16),
                ("r2", ctypes.c_uint16), ("r3", ctypes.c_uint16), ("value", _Value)]


def accessible(control) -> dict:
    """Read IAccessible for this owned HWND, never other applications' windows."""
    iid = _GUID.from_buffer_copy(uuid.UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le)
    obj = ctypes.c_void_p()
    dll = ctypes.OleDLL("oleacc")
    get = dll.AccessibleObjectFromWindow
    get.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_GUID),
                    ctypes.POINTER(ctypes.c_void_p)]
    get.restype = ctypes.HRESULT
    get(control.GetHandle(), 0xFFFFFFFC, ctypes.byref(iid), ctypes.byref(obj))  # OBJID_CLIENT
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    child = _Variant()
    child.vt, child.integer = 3, 0  # VT_I4 / CHILDID_SELF

    def integer(index):
        result = _Variant()
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _Variant,
                                ctypes.POINTER(_Variant))(table[index])
        code = fn(obj, child, ctypes.byref(result))
        if code < 0 or result.vt != 3:
            raise RuntimeError("Native accessibility did not return an integer")
        return result.integer

    try:
        name = ctypes.c_void_p()
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _Variant,
                                ctypes.POINTER(ctypes.c_void_p))(table[10])  # get_accName
        code = fn(obj, child, ctypes.byref(name))
        if code < 0:
            raise RuntimeError("Native accessibility name unavailable")
        try:
            label = ctypes.wstring_at(name.value) if name.value else ""
        finally:
            free = ctypes.OleDLL("oleaut32").SysFreeString
            free.argtypes = [ctypes.c_void_p]
            free.restype = None
            free(name)
        return {"name": label, "role": integer(13), "state": integer(14)}
    finally:
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(obj)  # Release


def window_probe() -> dict:
    import wx

    from cliente_xmpp.ui.voice_controller import VoiceController
    from cliente_xmpp.ui.voice_dialog import VoiceCallDialog

    app = wx.App.Get() or wx.App(False)
    holder = {}
    controller = VoiceController(
        None, "fixture@example.invalid", "whatsapp.example.invalid",
        lambda: holder["dialog"].render(), lambda *_: None, lambda *_: None, lambda _: "Fixture",
    )
    dialog = VoiceCallDialog(None, controller, lambda _: None)
    holder["dialog"] = dialog
    try:
        names = ("status", "peer", "answer", "reject", "hangup",
                 "mute", "input", "output")
        rows = {name: accessible(getattr(dialog, name)) for name in names}
        if any(not row["name"] for row in rows.values()):
            raise RuntimeError("An interactive/readable control has no native accessible name")
        if rows["mute"]["role"] != 44:
            raise RuntimeError("Mute is not exposed as a check box")
        dialog.mute.SetValue(True)
        checked = accessible(dialog.mute)
        if not checked["state"] & 0x10 or rows["mute"]["state"] & 0x10:
            raise RuntimeError("Mute checked state is not accessible")
        rows["mute"]["checked_state"] = checked["state"]
        return {"controls": rows, "wx": wx.version(), "no_account": True}
    finally:
        dialog.Destroy()
        app.ProcessPendingEvents()


def muted_audio_probe() -> dict:
    from cliente_xmpp.audio.live_call import SILENCE, LiveCallAudio
    from cliente_xmpp.models.voice import VoiceAudioSettings

    audio = LiveCallAudio()
    settings = VoiceAudioSettings(muted=True)
    permit = audio.permit(settings)
    frames = 0
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-audio-probe") as worker:
        try:
            if not worker.submit(audio.start, permit, settings).result(timeout=10):
                raise RuntimeError("Native stream did not start")
            deadline = time.monotonic()+0.4
            while time.monotonic() < deadline:
                data = audio.take_capture()
                if data is not None:
                    if data != SILENCE:
                        raise RuntimeError("Muted capture returned non-silent PCM")
                    frames += 1
                time.sleep(.005)
            if not frames or audio.failure:
                raise RuntimeError("Native callbacks did not deliver muted frames")
        finally:
            audio.invalidate()
            worker.submit(audio.stop).result(timeout=10)
    return {"muted_frames": frames, "rate": 16000, "channels": 1,
            "stored_samples": 0, "network": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()
    report = window_probe()
    path = ROOT / "cliente_xmpp/lib/nvdaControllerClient64.dll"
    if path.is_file():
        client = ctypes.WinDLL(str(path))
        check = client.nvdaController_testIfRunning
        check.argtypes, check.restype = [], ctypes.c_ulong
        report["nvda_running"] = check() == 0
    if args.audio:
        report["native_audio"] = muted_audio_probe()
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
