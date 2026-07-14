from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media import downloads
from cliente_xmpp.media.downloads import download_media, media_description
from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.conversation_panel import ConversationPanel


class MediaDescriptionTests(unittest.TestCase):
    def test_hides_bridge_hash_for_video_note(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            media_url="https://example.test/a72c8a794c297abf149839cc92675eac15645f78082cb9906aa0b52918bcd745.mp4",
            media_kind="video",
            media_filename="a72c8a794c297abf149839cc92675eac15645f78082cb9906aa0b52918bcd745.mp4",
            media_size=464754,
        )

        self.assertEqual(media_description(message), "Nota de video, 453.9 KB")

    def test_keeps_human_video_filename(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            media_url="https://example.test/cumpleanos.mp4",
            media_kind="video",
            media_filename="cumpleanos.mp4",
            media_size=1024,
        )

        self.assertEqual(media_description(message), "video, cumpleanos.mp4, 1.0 KB")


class _DownloadResponse(io.BytesIO):
    def __init__(self, content: bytes, content_length: int) -> None:
        super().__init__(content)
        self.headers = {
            "Content-Type": "audio/mp4",
            "Content-Length": str(content_length),
        }

    def __enter__(self) -> _DownloadResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class MediaDownloadTests(unittest.TestCase):
    @staticmethod
    def _audio_message() -> Message:
        return Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            audio_url="https://upload.example.test/voice.m4a",
            media_url="https://upload.example.test/voice.m4a",
            media_kind="audio",
            media_mime="audio/mp4",
            media_filename="voice.m4a",
        )

    def test_complete_audio_download_is_published_without_part_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            content = b"audio data"
            with (
                patch.object(downloads, "DOWNLOADS_DIR", Path(temp_dir)),
                patch.object(
                    downloads,
                    "urlopen",
                    return_value=_DownloadResponse(content, len(content)),
                ),
            ):
                result = download_media(self._audio_message(), "me@example.test")

            self.assertEqual(result.path.read_bytes(), content)
            self.assertFalse(result.path.with_name(f"{result.path.name}.part").exists())

    def test_incomplete_audio_download_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(downloads, "DOWNLOADS_DIR", Path(temp_dir)),
                patch.object(
                    downloads,
                    "urlopen",
                    return_value=_DownloadResponse(b"short", 100),
                ),
            ):
                with self.assertRaisesRegex(OSError, "descarga quedo incompleta"):
                    download_media(self._audio_message(), "me@example.test")

            remaining_files = [path for path in Path(temp_dir).rglob("*") if path.is_file()]
            self.assertEqual(remaining_files, [])


class RayoAiMediaTests(unittest.TestCase):
    def test_sends_original_webp_path_without_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sticker.webp"
            source.write_bytes(b"webp")
            with patch.object(rayoai, "send_payload", return_value=True) as send_payload:
                sent = rayoai.send_open_path(source)

        self.assertTrue(sent)
        self.assertEqual(
            send_payload.call_args.args[0],
            {"cmd": "open", "path": str(source.resolve())},
        )


class _FakeListItem:
    def __init__(self) -> None:
        self.image: int | None = None

    def SetImage(self, image: int) -> None:
        self.image = image


class _FakeMessageList:
    def __init__(self) -> None:
        self.insert_args: tuple[object, ...] | None = None
        self.item = _FakeListItem()

    @staticmethod
    def GetItemCount() -> int:
        return 0

    def InsertItem(self, *args: object) -> None:
        self.insert_args = args

    def SetItem(self, *args: object) -> None:
        return None

    def GetItem(self, _index: int) -> _FakeListItem:
        return self.item


class MessageThumbnailTests(unittest.TestCase):
    def test_text_row_explicitly_clears_image_index(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="",
        )
        messages = _FakeMessageList()
        panel = SimpleNamespace(
            messages=messages,
            _message_rows=[],
            _thumbnail_index_for_message=lambda _message: -1,
            _format_message_row=lambda _message: "texto",
            _style_message_item=lambda _index: None,
        )

        ConversationPanel._append_message_row(panel, message)

        self.assertEqual(messages.insert_args, (0, "texto", -1))

    def test_refresh_clears_previous_thumbnail(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="contact@example.test",
            body="",
        )
        messages = _FakeMessageList()
        panel = SimpleNamespace(
            messages=messages,
            _message_rows=[message],
            _thumbnail_index_for_message=lambda _message: -1,
            _format_message_row=lambda _message: "texto",
            _style_message_item=lambda _index: None,
        )

        ConversationPanel.refresh_message(panel, message)

        self.assertEqual(messages.item.image, -1)


class RecordingControlsTests(unittest.TestCase):
    def test_view_once_checkbox_only_shows_during_recording_and_resets(self) -> None:
        class Control:
            def __init__(self) -> None:
                self.enabled: bool | None = None
                self.visible: bool | None = None
                self.value = False

            def Enable(self, value: bool) -> None:
                self.enabled = value

            def Show(self, value: bool) -> None:
                self.visible = value

            def SetValue(self, value: bool) -> None:
                self.value = value

        compose = Control()
        attach = Control()
        sticker = Control()
        pause = Control()
        cancel = Control()
        view_once = Control()
        panel = SimpleNamespace(
            compose=compose,
            attach_button=attach,
            sticker_button=sticker,
            pause_recording_button=pause,
            cancel_recording_button=cancel,
            view_once_audio=view_once,
            update_send_button_state=lambda *_args: None,
            Layout=lambda: None,
        )

        ConversationPanel.set_recording_state(panel, True)
        self.assertTrue(view_once.visible)
        self.assertFalse(view_once.value)
        self.assertFalse(sticker.enabled)

        view_once.value = True
        ConversationPanel.set_recording_state(panel, False)
        self.assertFalse(view_once.visible)
        self.assertFalse(view_once.value)
        self.assertTrue(sticker.enabled)


if __name__ == "__main__":
    unittest.main()
