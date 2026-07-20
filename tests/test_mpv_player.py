from __future__ import annotations

import unittest
from unittest.mock import patch

from cliente_xmpp.audio.player import MpvAudioPlayer


class _FakeMpvDll:
    def __init__(self) -> None:
        self.options: list[tuple[bytes, bytes]] = []
        self.commands: list[list[bytes]] = []
        self.destroyed_handles: list[int] = []

    def mpv_create(self) -> int:
        return 1

    def mpv_set_option_string(self, _handle: int, name: bytes, value: bytes) -> int:
        self.options.append((name, value))
        return 0

    def mpv_initialize(self, _handle: int) -> int:
        return 0

    def mpv_terminate_destroy(self, handle: int) -> None:
        self.destroyed_handles.append(handle)

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
                [b"keybind", b"ESC", b"quit"],
            ],
        )

    def test_shutdown_destroys_the_native_video_handle(self) -> None:
        dll = _FakeMpvDll()
        player = MpvAudioPlayer(video=True)
        player._dll = dll  # type: ignore[assignment]
        player._handle = 123  # type: ignore[assignment]
        player._current_url = "video.mp4"

        player._handle_video_shutdown(123)  # type: ignore[arg-type]

        self.assertIsNone(player._handle)
        self.assertEqual(player._current_url, "")
        self.assertEqual(dll.destroyed_handles, [123])

    def test_audio_seek_moves_five_percent_and_reports_target(self) -> None:
        dll = _FakeMpvDll()
        player = MpvAudioPlayer()
        player._dll = dll  # type: ignore[assignment]
        player._handle = 1  # type: ignore[assignment]
        player._current_url = "voice.ogg"

        with (
            patch.object(player, "_playback_finished", return_value=False),
            patch.object(player, "_get_double_property", return_value=5.0),
        ):
            percent = player.seek_percent("voice.ogg", 5)

        self.assertEqual(percent, 10)
        self.assertEqual(
            dll.commands[-1],
            [b"seek", b"10", b"absolute-percent", b"exact"],
        )
