from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import wx

from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.chat_message_dialogs import ChatFilesDialog, StarredMessagesDialog


class _KeyEvent:
    def __init__(self, key_code: int) -> None:
        self._key_code = key_code
        self.skipped = False

    def GetKeyCode(self) -> int:
        return self._key_code

    def Skip(self) -> None:
        self.skipped = True


def _dialog_for(message: Message, **actions: object) -> SimpleNamespace:
    return SimpleNamespace(
        _selected_message=lambda: message,
        _on_open_message=actions.get("open"),
        _on_speak_message=actions.get("speak"),
        _on_play_audio=actions.get("play"),
        _on_describe=actions.get("describe"),
    )


class _MenuItem:
    def Enable(self, _enabled: bool) -> None:
        return None


class _Menu:
    labels: list[str] = []

    def Append(self, _item_id: int, label: str) -> _MenuItem:
        self.labels.append(label)
        return _MenuItem()

    def Destroy(self) -> None:
        return None


class StarredMessagesDialogKeyboardTests(unittest.TestCase):
    def test_space_opens_full_reader_for_text(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="Texto largo",
        )
        actions: list[str] = []
        dialog = _dialog_for(
            message,
            open=lambda _message: actions.append("open") or True,
            play=lambda _message: actions.append("play") or True,
        )
        event = _KeyEvent(wx.WXK_SPACE)

        StarredMessagesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(actions, ["open"])
        self.assertFalse(event.skipped)

    def test_space_plays_audio_instead_of_opening_reader(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            audio_url="https://example.test/audio.ogg",
        )
        actions: list[str] = []
        dialog = _dialog_for(
            message,
            open=lambda _message: actions.append("open") or True,
            play=lambda _message: actions.append("play") or True,
        )
        event = _KeyEvent(wx.WXK_SPACE)

        StarredMessagesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(actions, ["play"])
        self.assertFalse(event.skipped)

    def test_left_speaks_selected_text_with_nvda(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="Texto",
        )
        actions: list[str] = []
        dialog = _dialog_for(
            message,
            speak=lambda _message: actions.append("speak") or True,
        )
        event = _KeyEvent(wx.WXK_LEFT)

        StarredMessagesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(actions, ["speak"])
        self.assertFalse(event.skipped)

    def test_context_menu_offers_rayoai_for_supported_media(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/photo.jpg",
            media_kind="image",
        )
        dialog = _dialog_for(message, describe=lambda _message: None)
        dialog._on_go_to_message = lambda _event: None
        dialog.Bind = lambda *_args: None
        dialog.PopupMenu = lambda _menu: None

        with patch("cliente_xmpp.ui.chat_message_dialogs.wx.Menu", _Menu):
            _Menu.labels = []
            StarredMessagesDialog._show_menu(dialog, None)  # type: ignore[arg-type]

        self.assertIn("Describir con RayoAI", _Menu.labels)

    def test_files_context_menu_offers_rayoai_for_supported_media(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/photo.jpg",
            media_kind="image",
        )
        control = SimpleNamespace(GetFirstSelected=lambda: 0)
        dialog = SimpleNamespace(
            _messages_by_list={id(control): [message]},
            _on_describe=lambda _message: None,
            _selected_message=lambda selected_control: message,
            Bind=lambda *_args: None,
            PopupMenu=lambda _menu: None,
        )

        with patch("cliente_xmpp.ui.chat_message_dialogs.wx.Menu", _Menu):
            _Menu.labels = []
            ChatFilesDialog._show_menu(dialog, control)  # type: ignore[arg-type]

        self.assertIn("Describir con RayoAI", _Menu.labels)

    def test_files_left_arrow_reads_saved_alt_text(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/photo.jpg",
            media_kind="image",
            media_alt_text="Descripción completa de la foto.",
        )
        control = SimpleNamespace(GetFirstSelected=lambda: 0)
        page = SimpleNamespace(GetChildren=lambda: [control])
        spoken: list[Message] = []
        dialog = SimpleNamespace(
            notebook=SimpleNamespace(GetCurrentPage=lambda: page),
            _messages_by_list={id(control): [message]},
            _selected_message=lambda selected_control: message,
            _on_speak_message=lambda selected_message: spoken.append(selected_message) or True,
        )
        event = _KeyEvent(wx.WXK_LEFT)

        ChatFilesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(spoken, [message])
        self.assertFalse(event.skipped)

    def test_files_space_plays_video_with_integrated_viewer(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/video.mp4",
            media_kind="video",
        )
        control = SimpleNamespace(GetFirstSelected=lambda: 0)
        page = SimpleNamespace(GetChildren=lambda: [control])
        played: list[Message] = []
        dialog = SimpleNamespace(
            notebook=SimpleNamespace(GetCurrentPage=lambda: page),
            _messages_by_list={id(control): [message]},
            _selected_message=lambda selected_control: message,
            _on_play_video=lambda selected_message: played.append(selected_message) or True,
            _on_play_audio=None,
            _on_open_message=None,
            _on_speak_message=None,
        )
        event = _KeyEvent(wx.WXK_SPACE)

        ChatFilesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(played, [message])
        self.assertFalse(event.skipped)

    def test_files_space_downloads_non_playable_media(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/document.pdf",
            media_kind="file",
        )
        control = SimpleNamespace(GetFirstSelected=lambda: 0)
        page = SimpleNamespace(GetChildren=lambda: [control])
        opened: list[Message] = []
        read: list[Message] = []
        dialog = SimpleNamespace(
            notebook=SimpleNamespace(GetCurrentPage=lambda: page),
            _messages_by_list={id(control): [message]},
            _selected_message=lambda selected_control: message,
            _on_open=lambda selected_message: opened.append(selected_message) or True,
            _on_open_message=lambda selected_message: read.append(selected_message) or True,
            _on_play_video=None,
            _on_play_audio=None,
            _on_speak_message=None,
        )
        event = _KeyEvent(wx.WXK_SPACE)

        ChatFilesDialog._on_key_down(dialog, event)  # type: ignore[arg-type]

        self.assertEqual(opened, [message])
        self.assertEqual(read, [])
        self.assertFalse(event.skipped)

    def test_files_enter_goes_to_message_in_chat(self) -> None:
        message = Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://example.test/photo.jpg",
            media_kind="image",
        )
        control = SimpleNamespace(GetFirstSelected=lambda: 0)
        closed_with: list[int] = []
        dialog = SimpleNamespace(
            _messages_by_list={id(control): [message]},
            _selected_message=lambda selected_control: message,
            selected_message=None,
            EndModal=lambda result: closed_with.append(result),
        )

        ChatFilesDialog._go_to_message(dialog, control)  # type: ignore[arg-type]

        self.assertIs(dialog.selected_message, message)
        self.assertEqual(closed_with, [wx.ID_OK])


if __name__ == "__main__":
    unittest.main()
