from __future__ import annotations

import unittest
from types import SimpleNamespace

import wx

from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.chat_message_dialogs import StarredMessagesDialog


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
    )


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


if __name__ == "__main__":
    unittest.main()
