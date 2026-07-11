from __future__ import annotations

import ctypes
from importlib import resources
from pathlib import Path


class AssetSound:
    def __init__(self, filename: str, alias: str) -> None:
        self._alias = alias
        self._sound_path = _audio_asset_path(filename)

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


class NewMessageSound(AssetSound):
    def __init__(self) -> None:
        super().__init__("new-message.mp3", "cliente_xmpp_new_message")


class OpenChatMessageSound(AssetSound):
    def __init__(self) -> None:
        super().__init__("message.mp3", "cliente_xmpp_open_chat_message")


class SentMessageSound(AssetSound):
    def __init__(self) -> None:
        super().__init__("sent-message.mp3", "cliente_xmpp_sent_message")


def _audio_asset_path(filename: str) -> Path:
    return Path(resources.files("cliente_xmpp").joinpath("assets", "audio", filename))
