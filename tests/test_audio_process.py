from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cliente_xmpp.audio.duration import media_duration_seconds
from cliente_xmpp.audio.process import no_window_creation_flags


class AudioProcessTests(unittest.TestCase):
    def test_windows_console_tools_use_create_no_window(self) -> None:
        with (
            patch("cliente_xmpp.audio.process.os.name", "nt"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            self.assertEqual(no_window_creation_flags(), 0x08000000)

    def test_other_platforms_do_not_receive_windows_flags(self) -> None:
        with patch("cliente_xmpp.audio.process.os.name", "posix"):
            self.assertEqual(no_window_creation_flags(), 0)

    def test_audio_duration_probe_receives_hidden_window_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "voice.m4a"
            source.write_bytes(b"audio")
            with (
                patch("cliente_xmpp.audio.duration._ffprobe_path", return_value="ffprobe.exe"),
                patch(
                    "cliente_xmpp.audio.duration.no_window_creation_flags",
                    return_value=0x08000000,
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


if __name__ == "__main__":
    unittest.main()
