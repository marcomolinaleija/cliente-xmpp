"""Local, asynchronous call sounds; independent of microphone PCM."""
from __future__ import annotations

try:
    import winsound
except ImportError:
    winsound = None

from cliente_xmpp.audio.notification import _audio_asset_path


class RingbackSound:
    def __init__(self, backend=None):
        self.backend = winsound if backend is None else backend
        self.path = _audio_asset_path("call-ringback.wav")
        self.playing = False

    def start(self):
        if self.playing or self.backend is None or not self.path.is_file():
            return
        b = self.backend
        try:
            b.PlaySound(str(self.path), b.SND_FILENAME | b.SND_ASYNC | b.SND_LOOP | b.SND_NODEFAULT)
        except (OSError, RuntimeError):
            return
        self.playing = True

    def stop(self):
        if not self.playing:
            return
        self.playing = False
        try:
            self.backend.PlaySound(None, 0)
        except (OSError, RuntimeError):
            pass

    def connected(self):
        self._play_once("call-connected.wav")

    def ended(self):
        self._play_once("call-ended.wav")

    def _play_once(self, filename):
        self.stop()
        path = _audio_asset_path(filename)
        if self.backend is None or not path.is_file():
            return
        b = self.backend
        try:
            b.PlaySound(str(path), b.SND_FILENAME | b.SND_ASYNC | b.SND_NODEFAULT)
        except (OSError, RuntimeError):
            pass
