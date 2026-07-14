from __future__ import annotations

import unittest
from types import SimpleNamespace

from cliente_xmpp.models.chat import Message
from cliente_xmpp.notifications.windows import (
    ACTION_MARK_READ,
    MAX_NOTIFICATION_MESSAGE_LENGTH,
    MAX_NOTIFICATION_TITLE_LENGTH,
    WindowsNotificationService,
    format_windows_notification,
)
from cliente_xmpp.ui.main_window import MainWindow


class WindowsNotificationFormattingTests(unittest.TestCase):
    def test_collapses_whitespace_and_uses_fallbacks(self) -> None:
        content = format_windows_notification("  ", "\n\t")

        self.assertEqual(content.title, "WhatsApp CAN")
        self.assertEqual(content.message, "Nuevo mensaje")

    def test_respects_windows_text_limits(self) -> None:
        content = format_windows_notification("t" * 100, "m" * 400)

        self.assertEqual(len(content.title), MAX_NOTIFICATION_TITLE_LENGTH)
        self.assertEqual(len(content.message), MAX_NOTIFICATION_MESSAGE_LENGTH)
        self.assertTrue(content.title.endswith("..."))
        self.assertTrue(content.message.endswith("..."))


class WindowsNotificationActionTests(unittest.TestCase):
    def test_notification_body_and_reply_action_open_the_chat(self) -> None:
        opened: list[str] = []
        service = WindowsNotificationService(
            on_open_chat=opened.append,
            on_mark_read=lambda _jid: None,
        )
        toast = object()
        service._active.append(toast)

        service._handle_activation(toast, "contact@example.org", None)

        self.assertEqual(opened, ["contact@example.org"])
        self.assertEqual(service._active, [])

    def test_mark_read_action_uses_the_dedicated_callback(self) -> None:
        marked_read: list[str] = []
        service = WindowsNotificationService(
            on_open_chat=lambda _jid: None,
            on_mark_read=marked_read.append,
        )

        service._handle_activation(
            object(),
            "contact@example.org",
            ACTION_MARK_READ,
        )

        self.assertEqual(marked_read, ["contact@example.org"])


class IncomingWindowsNotificationTests(unittest.TestCase):
    @staticmethod
    def _message(**changes: object) -> Message:
        values: dict[str, object] = {
            "chat_jid": "room@example.org",
            "sender_jid": "member@example.org",
            "sender_name": "Ana",
            "body": "Hola desde el grupo",
            "chat_is_group": True,
        }
        values.update(changes)
        return Message(**values)

    @staticmethod
    def _window(*, enabled: bool = True, preview: bool = True, muted: bool = False):
        shown: list[dict[str, str]] = []
        return SimpleNamespace(
            windows_notifications_enabled=enabled,
            windows_notification_previews_enabled=preview,
            IsActive=lambda: True,
            _message_notifications_muted=lambda _message: muted,
            _speakable_chat_name=lambda _jid: "Familia",
            _display_name_for_jid=lambda _jid: "Participante",
            windows_notification_service=SimpleNamespace(
                show_message=lambda **content: shown.append(content) or True
            ),
            shown=shown,
        )

    def test_shows_participant_chat_and_preview_for_an_inactive_group_chat(self) -> None:
        window = self._window()

        result = MainWindow._show_windows_notification(
            window,
            self._message(),
            current_chat_is_open=False,
        )

        self.assertTrue(result)
        self.assertEqual(window.shown, [{
            "title": "Ana en Familia",
            "message": "Hola desde el grupo",
            "chat_jid": "room@example.org",
        }])

    def test_hides_message_content_when_preview_is_disabled(self) -> None:
        window = self._window(preview=False)

        MainWindow._show_windows_notification(
            window,
            self._message(),
            current_chat_is_open=False,
        )

        self.assertEqual(window.shown[0]["message"], "Nuevo mensaje")

    def test_does_not_show_for_active_chat_muted_chat_or_outgoing_message(self) -> None:
        cases = (
            (self._window(), self._message(), True),
            (self._window(muted=True), self._message(), False),
            (self._window(), self._message(outgoing=True), False),
            (self._window(enabled=False), self._message(), False),
        )

        for window, message, current_chat_is_open in cases:
            with self.subTest(
                muted=window._message_notifications_muted(message),
                outgoing=message.outgoing,
                current_chat_is_open=current_chat_is_open,
            ):
                self.assertFalse(
                    MainWindow._show_windows_notification(
                        window,
                        message,
                        current_chat_is_open=current_chat_is_open,
                    )
                )
                self.assertEqual(window.shown, [])

    def test_native_notification_replaces_the_custom_incoming_sound(self) -> None:
        played: list[str] = []
        window = SimpleNamespace(
            IsActive=lambda: False,
            _message_notifications_muted=lambda _message: False,
            open_chat_message_sound_enabled=True,
            open_chat_message_sound=SimpleNamespace(play=lambda: played.append("open")),
            new_message_sound=SimpleNamespace(play=lambda: played.append("new")),
        )

        MainWindow._play_incoming_message_sound(
            window,
            self._message(),
            current_chat_is_open=False,
            windows_notification_shown=True,
        )

        self.assertEqual(played, [])


if __name__ == "__main__":
    unittest.main()
