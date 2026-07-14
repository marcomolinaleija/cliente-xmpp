from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cliente_xmpp.audio.duration import _ffprobe_path, media_duration_seconds
from cliente_xmpp.audio.opus import convert_to_voice_note, ffmpeg_path
from cliente_xmpp.audio.process import (
    bundled_tool_path,
    hidden_subprocess_kwargs,
    no_window_creation_flags,
)
from cliente_xmpp.audio.recorder import SoundDeviceAudioRecorder


class AudioProcessTests(unittest.TestCase):
    def test_frozen_app_uses_tool_from_its_bin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "WhatsApp-CAN.exe"
            tool = Path(temp_dir) / "bin" / "ffmpeg.exe"
            tool.parent.mkdir()
            tool.write_bytes(b"ffmpeg")
            with (
                patch("cliente_xmpp.audio.process.sys.frozen", True, create=True),
                patch("cliente_xmpp.audio.process.sys.executable", str(executable)),
            ):
                self.assertEqual(bundled_tool_path("ffmpeg.exe"), str(tool.resolve()))

    def test_audio_tools_prefer_the_frozen_bin_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "WhatsApp-CAN.exe"
            bin_dir = Path(temp_dir) / "bin"
            ffmpeg = bin_dir / "ffmpeg.exe"
            ffprobe = bin_dir / "ffprobe.exe"
            bin_dir.mkdir()
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            with (
                patch("cliente_xmpp.audio.process.sys.frozen", True, create=True),
                patch("cliente_xmpp.audio.process.sys.executable", str(executable)),
                patch("cliente_xmpp.audio.opus.shutil.which") as ffmpeg_which,
                patch("cliente_xmpp.audio.duration.shutil.which") as ffprobe_which,
            ):
                self.assertEqual(ffmpeg_path(), str(ffmpeg.resolve()))
                self.assertEqual(_ffprobe_path(), str(ffprobe.resolve()))
                ffmpeg_which.assert_not_called()
                ffprobe_which.assert_not_called()

    def test_windows_console_tools_use_create_no_window(self) -> None:
        with (
            patch("cliente_xmpp.audio.process.os.name", "nt"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            self.assertEqual(no_window_creation_flags(), 0x08000000)

    def test_other_platforms_do_not_receive_windows_flags(self) -> None:
        with patch("cliente_xmpp.audio.process.os.name", "posix"):
            self.assertEqual(no_window_creation_flags(), 0)
            self.assertEqual(hidden_subprocess_kwargs(), {})

    def test_windows_console_tools_also_receive_hidden_startup_info(self) -> None:
        startupinfo = SimpleNamespace(dwFlags=0, wShowWindow=None)
        with (
            patch("cliente_xmpp.audio.process.os.name", "nt"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(subprocess, "STARTF_USESHOWWINDOW", 0x00000001, create=True),
            patch.object(subprocess, "SW_HIDE", 0, create=True),
            patch.object(subprocess, "STARTUPINFO", return_value=startupinfo, create=True),
        ):
            options = hidden_subprocess_kwargs()

        self.assertEqual(options["creationflags"], 0x08000000)
        self.assertIs(options["startupinfo"], startupinfo)
        self.assertEqual(startupinfo.dwFlags, 0x00000001)
        self.assertEqual(startupinfo.wShowWindow, 0)

    def test_audio_duration_probe_receives_hidden_window_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "voice.m4a"
            source.write_bytes(b"audio")
            with (
                patch("cliente_xmpp.audio.duration._ffprobe_path", return_value="ffprobe.exe"),
                patch(
                    "cliente_xmpp.audio.duration.hidden_subprocess_kwargs",
                    return_value={"creationflags": 0x08000000, "startupinfo": "hidden"},
                ),
                patch(
                    "cliente_xmpp.audio.duration.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout='{"format": {"duration": "2.5"}}',
                    ),
                ) as run,
            ):
                duration = media_duration_seconds(source)

        self.assertEqual(duration, 2.5)
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(run.call_args.kwargs["startupinfo"], "hidden")

    def test_voice_note_conversion_receives_all_hidden_process_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "voice.wav"
            output = Path(temp_dir) / "voice.ogg"
            source.write_bytes(b"audio")
            output.write_bytes(b"encoded")
            with (
                patch("cliente_xmpp.audio.opus.ffmpeg_path", return_value="ffmpeg.exe"),
                patch("cliente_xmpp.audio.opus.voice_note_path", return_value=output),
                patch(
                    "cliente_xmpp.audio.opus.hidden_subprocess_kwargs",
                    return_value={"creationflags": 0x08000000, "startupinfo": "hidden"},
                ),
                patch(
                    "cliente_xmpp.audio.opus.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stderr="", stdout=""),
                ) as run,
            ):
                converted = convert_to_voice_note(source)

        self.assertEqual(converted, output)
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(run.call_args.kwargs["startupinfo"], "hidden")

    def test_recorder_encoder_receives_all_hidden_process_options(self) -> None:
        encoder = Mock()
        with (
            patch("cliente_xmpp.audio.recorder.ffmpeg_path", return_value="ffmpeg.exe"),
            patch(
                "cliente_xmpp.audio.recorder.hidden_subprocess_kwargs",
                return_value={"creationflags": 0x08000000, "startupinfo": "hidden"},
            ),
            patch("cliente_xmpp.audio.recorder.subprocess.Popen", return_value=encoder) as popen,
        ):
            result = SoundDeviceAudioRecorder._start_encoder(Path("voice.ogg"), 44_100, 1)

        self.assertIs(result, encoder)
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(popen.call_args.kwargs["startupinfo"], "hidden")


if __name__ == "__main__":
    unittest.main()
