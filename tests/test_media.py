from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media import downloads
from cliente_xmpp.media.downloads import (
    album_photo_count,
    album_photo_messages,
    delete_local_media_file,
    download_media,
    media_description,
)
from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.conversation_panel import ConversationPanel


class MediaDescriptionTests(unittest.TestCase):
    def test_hides_bridge_id_for_image(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            media_url="https://example.test/165892937462819.jpg",
            media_kind="image",
            media_filename="165892937462819.jpg",
            media_size=2048,
        )

        self.assertEqual(media_description(message), "foto, 2.0 KB")

    def test_collects_all_photos_announced_by_album_marker(self) -> None:
        sent_at = datetime(2026, 7, 22, 18, 1)
        album = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="Album: 3 photos",
            sent_at=sent_at,
            message_id="album-1",
        )
        photos = [
            Message(
                chat_jid=album.chat_jid,
                sender_jid=album.sender_jid,
                body="Foto",
                sent_at=sent_at + timedelta(seconds=3 + index),
                media_url=f"https://example.test/photo-{index}.jpg",
                media_kind="image",
            )
            for index in range(3)
        ]

        self.assertEqual(album_photo_count(album), 3)
        self.assertEqual(album_photo_messages([album, *photos], album), photos)

    def test_rejects_incomplete_or_interleaved_album(self) -> None:
        sent_at = datetime(2026, 7, 22, 18, 1)
        album = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="Álbum: 2 fotos",
            sent_at=sent_at,
        )
        photo = Message(
            chat_jid=album.chat_jid,
            sender_jid=album.sender_jid,
            body="Foto",
            sent_at=sent_at + timedelta(seconds=3),
            media_url="https://example.test/photo.jpg",
            media_kind="image",
        )
        interleaved = Message(
            chat_jid=album.chat_jid,
            sender_jid=album.sender_jid,
            body="Otro mensaje",
            sent_at=sent_at + timedelta(seconds=4),
        )

        self.assertEqual(album_photo_count(album), 2)
        self.assertEqual(album_photo_messages([album, photo], album), [])
        self.assertEqual(album_photo_messages([album, photo, interleaved], album), [])

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

    def test_space_does_not_replay_a_retracted_audio_with_a_stale_path(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            audio_url="https://upload.example.test/voice.ogg",
            media_kind="audio",
            media_local_path="stale-voice.ogg",
            retracted=True,
        )
        panel = SimpleNamespace(
            messages=SimpleNamespace(GetFirstSelected=lambda: 0),
            _message_rows=[message],
            _message_at_row=lambda _index: message,
        )

        self.assertFalse(ConversationPanel.play_selected_audio(panel))

    def test_percent_seek_ignores_a_focused_text_message(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="hola",
        )
        panel = SimpleNamespace(
            messages=SimpleNamespace(GetFirstSelected=lambda: 0),
            _message_rows=[message],
            _message_at_row=lambda _index: message,
        )

        self.assertIsNone(ConversationPanel.seek_selected_audio_percent(panel, 5))


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

    def test_retracted_media_file_is_deleted_and_model_path_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.ogg"
            path.write_bytes(b"audio")
            message = self._audio_message()
            message.media_local_path = str(path)

            deleted_path, error = delete_local_media_file(message)

            self.assertEqual(deleted_path, path)
            self.assertIsNone(error)
            self.assertFalse(path.exists())
            self.assertEqual(message.media_local_path, "")


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
