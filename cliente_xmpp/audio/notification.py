from __future__ import annotations

import ctypes
from importlib import resources
from pathlib import Path


class AssetSound:
    def __init__(self, filename: str, alias: str) -> None:
        self._alias = alias
        self._sound_path = _audio_asset_path(filename)

    def play(self) -> None:
        play_sound_path(self._sound_path, self._alias)

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


def play_sound_path(path: str | Path, alias: str = "cliente_xmpp_custom_message") -> bool:
    """Play a local notification file without depending on a toast notification."""
    sound_path = Path(path)
    if not sound_path.is_file():
        return False
    try:
        AssetSound._mci(f"close {alias}")
        AssetSound._mci(f'open "{sound_path}" type mpegvideo alias {alias}')
        AssetSound._mci(f"play {alias}")
    except OSError:
        return False
    return True
