from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import wx

from cliente_xmpp.config.settings import (
    DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
    DesktopNotificationSettings,
    SettingsStore,
)
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.settings_panel import format_setting_state


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


class NewChatSettingsTests(unittest.TestCase):
    def test_country_defaults_to_mexico(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            self.assertEqual(store.load_new_chat_country(), "MX")

    def test_country_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            store.save_new_chat_country("gb")

            self.assertEqual(store.load_new_chat_country(), "GB")


class WindowSettingsTests(unittest.TestCase):
    def test_minimize_to_tray_defaults_to_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            self.assertFalse(store.load_minimize_to_tray_on_alt_f4())

    def test_minimize_to_tray_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            store.save_minimize_to_tray_on_alt_f4(True)

            self.assertTrue(store.load_minimize_to_tray_on_alt_f4())


class UpdateCheckSettingsTests(unittest.TestCase):
    def test_update_check_defaults_to_twenty_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            self.assertEqual(
                store.load_update_check_interval_minutes(),
                DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
            )

    def test_update_check_interval_round_trip_including_never(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")

            store.save_update_check_interval_minutes(300)
            self.assertEqual(store.load_update_check_interval_minutes(), 300)

            store.save_update_check_interval_minutes(None)
            self.assertIsNone(store.load_update_check_interval_minutes())

    def test_invalid_saved_update_interval_returns_the_safe_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                '{"updates": {"check_interval_minutes": 17}}',
                encoding="utf-8",
            )

            self.assertEqual(
                SettingsStore(path).load_update_check_interval_minutes(),
                DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
            )


class UpdateCheckWindowTests(unittest.TestCase):
    def test_update_timer_uses_the_selected_interval(self) -> None:
        calls: list[object] = []
        timer = SimpleNamespace(
            Stop=lambda: calls.append("stop"),
            Start=lambda milliseconds: calls.append(milliseconds),
        )
        window = SimpleNamespace(
            update_check_timer=timer,
            update_check_interval_minutes=30,
        )

        with patch("cliente_xmpp.ui.main_window.can_check_for_updates", return_value=True):
            MainWindow._restart_update_check_timer(window)

        self.assertEqual(calls, ["stop", 30 * 60_000])

    def test_never_stops_automatic_update_timer(self) -> None:
        calls: list[object] = []
        timer = SimpleNamespace(
            Stop=lambda: calls.append("stop"),
            Start=lambda milliseconds: calls.append(milliseconds),
        )
        window = SimpleNamespace(
            update_check_timer=timer,
            update_check_interval_minutes=None,
        )

        with patch("cliente_xmpp.ui.main_window.can_check_for_updates", return_value=True):
            MainWindow._restart_update_check_timer(window)

        self.assertEqual(calls, ["stop"])

    def test_manual_check_runs_in_background_and_announces_no_update(self) -> None:
        callbacks = []
        statuses: list[str] = []
        announcements: list[str] = []
        panel = SimpleNamespace(
            set_update_check_in_progress=lambda _value: None,
            set_update_check_status=statuses.append,
        )
        window = SimpleNamespace(
            update_check_in_progress=False,
            settings_panel=panel,
            status_bar=SimpleNamespace(SetStatusText=statuses.append),
            speaker=SimpleNamespace(speak=announcements.append),
            update_check_offered_tags=set(),
            IsBeingDeleted=lambda: False,
        )

        with (
            patch("cliente_xmpp.ui.main_window.can_check_for_updates", return_value=True),
            patch(
                "cliente_xmpp.ui.main_window.check_for_update_in_background",
                side_effect=callbacks.append,
            ),
        ):
            MainWindow._request_update_check(window, manual=True)

        self.assertTrue(window.update_check_in_progress)
        callbacks[0](None, "")

        self.assertFalse(window.update_check_in_progress)
        self.assertEqual(statuses[-2:], [
            "WhatsApp CAN ya está actualizado.",
            "WhatsApp CAN ya está actualizado.",
        ])
        self.assertEqual(announcements, ["WhatsApp CAN ya está actualizado."])


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

    def test_alt_f4_shortcut_requires_only_alt(self) -> None:
        event = SimpleNamespace(
            GetKeyCode=lambda: wx.WXK_F4,
            ShiftDown=lambda: False,
            ControlDown=lambda: False,
            AltDown=lambda: True,
        )
        self.assertTrue(MainWindow._is_alt_f4_shortcut(event))

        event.ControlDown = lambda: True
        self.assertFalse(MainWindow._is_alt_f4_shortcut(event))

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


class AccessibleSettingStateTests(unittest.TestCase):
    def test_setting_label_always_contains_its_state(self) -> None:
        self.assertEqual(
            format_setting_state("Mostrar notificaciones", True),
            "Mostrar notificaciones: activado",
        )
        self.assertEqual(
            format_setting_state("Mostrar notificaciones", False),
            "Mostrar notificaciones: desactivado",
        )

    def test_changed_checkbox_is_announced_and_written_to_the_status_bar(self) -> None:
        announcements: list[str] = []
        status_messages: list[str] = []
        changed_control = object()

        class CheckBox:
            def __init__(self, value: bool) -> None:
                self.value = value

            def GetValue(self) -> bool:
                return self.value

        panel = SimpleNamespace(
            windows_notifications=CheckBox(True),
            show_preview=CheckBox(False),
            announce_with_nvda=CheckBox(False),
            open_chat_sound=CheckBox(True),
            sent_message_sound=CheckBox(True),
            minimize_to_tray_on_alt_f4=CheckBox(False),
            apply_interactive_state=lambda: None,
            checkbox_state_text=lambda control: (
                "Mostrar el contenido del mensaje en la notificación: desactivado"
                if control is changed_control
                else "Configuración actualizada"
            ),
        )
        window = SimpleNamespace(
            settings_panel=panel,
            _save_desktop_notification_settings=lambda: None,
            _save_notification_sound_settings=lambda: None,
            _save_window_settings=lambda: None,
            status_bar=SimpleNamespace(SetStatusText=status_messages.append),
            speaker=SimpleNamespace(speak=announcements.append),
        )
        event = SimpleNamespace(GetEventObject=lambda: changed_control)

        MainWindow._on_settings_changed(window, event)

        expected = "Mostrar el contenido del mensaje en la notificación: desactivado"
        self.assertEqual(status_messages, [expected])
        self.assertEqual(announcements, [expected])

    def test_tray_setting_is_read_and_announced_when_changed(self) -> None:
        announcements: list[str] = []
        status_messages: list[str] = []

        class CheckBox:
            def __init__(self, value: bool) -> None:
                self.value = value

            def GetValue(self) -> bool:
                return self.value

        changed_control = CheckBox(True)
        panel = SimpleNamespace(
            windows_notifications=CheckBox(True),
            show_preview=CheckBox(True),
            announce_with_nvda=CheckBox(False),
            open_chat_sound=CheckBox(True),
            sent_message_sound=CheckBox(True),
            minimize_to_tray_on_alt_f4=changed_control,
            apply_interactive_state=lambda: None,
            checkbox_state_text=lambda control: (
                "Minimizar a la bandeja al usar Alt+F4: activado"
                if control is changed_control
                else "ConfiguraciÃ³n actualizada"
            ),
        )
        window = SimpleNamespace(
            settings_panel=panel,
            _save_desktop_notification_settings=lambda: None,
            _save_notification_sound_settings=lambda: None,
            _save_window_settings=lambda: None,
            status_bar=SimpleNamespace(SetStatusText=status_messages.append),
            speaker=SimpleNamespace(speak=announcements.append),
        )

        MainWindow._on_settings_changed(
            window,
            SimpleNamespace(GetEventObject=lambda: changed_control),
        )

        self.assertTrue(window.minimize_to_tray_on_alt_f4)
        expected = "Minimizar a la bandeja al usar Alt+F4: activado"
        self.assertEqual(status_messages, [expected])
        self.assertEqual(announcements, [expected])
