from __future__ import annotations

import ctypes
from importlib import resources
from pathlib import Path


class NewMessageSound:
    def __init__(self) -> None:
        self._alias = "cliente_xmpp_new_message"
        self._sound_path = _new_message_sound_path()

    def play(self) -> None:
        if not self._sound_path.exists():
            return

        self._mci(f"close {self._alias}")
        self._mci(f'open "{self._sound_path}" type mpegvideo alias {self._alias}')
        self._mci(f"play {self._alias}")

    @staticmethod
    def _mci(command: str) -> None:
        try:
            ctypes.windll.winmm.mciSendStringW(command, None, 0, None)
        except AttributeError:
            return


def _new_message_sound_path() -> Path:
    return Path(resources.files("cliente_xmpp").joinpath("assets", "audio", "new-message.mp3"))
