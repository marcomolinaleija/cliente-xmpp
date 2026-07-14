import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from slidge import global_config
from slidge_whatsapp.session import Attachment, Session


async def check_persisted_attachment_is_kept() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        persisted_path = Path(temp_dir) / "incoming-audio.ogg"
        persisted_path.write_bytes(b"OggS-test")
        attachment = SimpleNamespace(path=persisted_path)
        actor = SimpleNamespace(send_files=AsyncMock())
        message = SimpleNamespace(
            Attachments=[],
            Actor=SimpleNamespace(IsMe=False),
            ID="audio-persistence-smoke",
            IsForwarded=False,
        )

        previous_no_upload_path = getattr(global_config, "NO_UPLOAD_PATH", None)
        global_config.NO_UPLOAD_PATH = Path(temp_dir)
        try:
            with (
                patch.object(
                    Attachment,
                    "convert_list",
                    AsyncMock(return_value=[attachment]),
                ),
                patch.object(
                    Session,
                    "_Session__get_reply_to",
                    AsyncMock(return_value=None),
                ),
                patch.object(
                    Session,
                    "_Session__get_timestamp",
                    return_value=None,
                ),
            ):
                session = object.__new__(Session)
                await session.on_wa_msg_attachment(message, actor, None)
        finally:
            global_config.NO_UPLOAD_PATH = previous_no_upload_path

        actor.send_files.assert_awaited_once()
        assert persisted_path.is_file(), "The persisted attachment was deleted"


def main() -> None:
    asyncio.run(check_persisted_attachment_is_kept())
    print("attachment persistence runtime smoke: ok")


if __name__ == "__main__":
    main()
