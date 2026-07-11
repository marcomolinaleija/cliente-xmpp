from __future__ import annotations

import unittest

from cliente_xmpp.audio.player import MpvAudioPlayer


class _FakeMpvDll:
    def __init__(self) -> None:
        self.options: list[tuple[bytes, bytes]] = []
        self.commands: list[list[bytes]] = []

    def mpv_create(self) -> int:
        return 1

    def mpv_set_option_string(self, _handle: int, name: bytes, value: bytes) -> int:
        self.options.append((name, value))
        return 0

    def mpv_initialize(self, _handle: int) -> int:
        return 0

    def mpv_command(self, _handle: int, command: object) -> int:
        self.commands.append([argument for argument in command if argument is not None])
        return 0


class MpvVideoInputTests(unittest.TestCase):
    def test_video_player_enables_native_default_keyboard_bindings(self) -> None:
        dll = _FakeMpvDll()
        player = MpvAudioPlayer(video=True)
        player._dll = dll  # type: ignore[assignment]

        player._ensure_handle()

        self.assertIn((b"input-default-bindings", b"yes"), dll.options)
        self.assertIn((b"input-vo-keyboard", b"yes"), dll.options)

        self.assertEqual(
            dll.commands,
            [
                [b"keybind", b"SPACE", b"cycle pause"],
                [b"keybind", b"UP", b"add volume 5"],
                [b"keybind", b"DOWN", b"add volume -5"],
                [b"keybind", b"LEFT", b"seek -5"],
                [b"keybind", b"RIGHT", b"seek 5"],
                [b"keybind", b"Alt+F4", b"quit"],
            ],
        )
