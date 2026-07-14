from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import wx

from cliente_xmpp.config.settings import DesktopNotificationSettings, SettingsStore
from cliente_xmpp.ui.main_window import MainWindow


class NotificationSoundSettingsTests(unittest.TestCase):
    def test_notification_sound_settings_default_to_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            self.assertEqual(store.load_notification_sound_settings(), (True, True))

    def test_notification_sound_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.save_notification_sound_settings(
                open_chat_message_enabled=False,
                sent_message_enabled=True,
            )

            self.assertEqual(store.load_notification_sound_settings(), (False, True))


class NotificationSoundShortcutTests(unittest.TestCase):
    @staticmethod
    def _event(*, shift: bool = False, control: bool = False, alt: bool = False):
        return SimpleNamespace(
            GetKeyCode=lambda: wx.WXK_F8,
            ShiftDown=lambda: shift,
            ControlDown=lambda: control,
            AltDown=lambda: alt,
        )

    def test_f8_toggles_open_chat_message_sound(self) -> None:
        self.assertEqual(
            MainWindow._notification_sound_shortcut(self._event()),
            "open_chat_message",
        )

    def test_shift_f8_toggles_sent_message_sound(self) -> None:
        self.assertEqual(
            MainWindow._notification_sound_shortcut(self._event(shift=True)),
            "sent_message",
        )

    def test_modified_f8_with_control_or_alt_is_not_handled(self) -> None:
        self.assertIsNone(
            MainWindow._notification_sound_shortcut(self._event(control=True))
        )
        self.assertIsNone(MainWindow._notification_sound_shortcut(self._event(alt=True)))

    def test_toggles_announce_the_new_state(self) -> None:
        announcements: list[str] = []
        status_messages: list[str] = []
        window = SimpleNamespace(
            open_chat_message_sound_enabled=True,
            sent_message_sound_enabled=True,
            _save_notification_sound_settings=lambda: None,
            status_bar=SimpleNamespace(SetStatusText=status_messages.append),
            speaker=SimpleNamespace(speak=announcements.append),
        )

        MainWindow._toggle_open_chat_message_sound(window)
        MainWindow._toggle_sent_message_sound(window)

        self.assertEqual(announcements, [
            "Sonido de mensajes en el chat abierto desactivado",
            "Sonido al enviar mensajes desactivado",
        ])
        self.assertEqual(status_messages, announcements)


class DesktopNotificationSettingsTests(unittest.TestCase):
    def test_windows_notifications_have_accessible_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            self.assertEqual(
                store.load_desktop_notification_settings(),
                DesktopNotificationSettings(
                    enabled=True,
                    show_preview=True,
                    announce_with_nvda=False,
                ),
            )

    def test_windows_notification_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            expected = DesktopNotificationSettings(
                enabled=False,
                show_preview=False,
                announce_with_nvda=True,
            )

            store.save_desktop_notification_settings(expected)

            self.assertEqual(store.load_desktop_notification_settings(), expected)
