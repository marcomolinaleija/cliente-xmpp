from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR = Path.home() / ".cliente-xmpp"
SETTINGS_PATH = APP_DIR / "settings.json"
DEFAULT_AUDIO_SPEED = 1.0
SUPPORTED_AUDIO_SPEEDS = (1.0, 1.5, 2.0)
DEFAULT_OPEN_CHAT_MESSAGE_SOUND_ENABLED = True
DEFAULT_SENT_MESSAGE_SOUND_ENABLED = True
DEFAULT_WINDOWS_NOTIFICATIONS_ENABLED = True
DEFAULT_WINDOWS_NOTIFICATION_PREVIEWS_ENABLED = True
DEFAULT_WINDOWS_NOTIFICATION_NVDA_ANNOUNCEMENTS_ENABLED = False
DEFAULT_MINIMIZE_TO_TRAY_ON_ALT_F4 = False
DEFAULT_NEW_CHAT_COUNTRY = "MX"
DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES = 20
UPDATE_CHECK_INTERVAL_CHOICES = (20, 30, 60, 300)


@dataclass(slots=True)
class ConnectionSettings:
    jid: str = ""
    host: str = ""
    port: int = 5222
    use_tls: bool = True
    remember_password: bool = False
    auto_connect: bool = False


@dataclass(frozen=True, slots=True)
class DesktopNotificationSettings:
    enabled: bool = DEFAULT_WINDOWS_NOTIFICATIONS_ENABLED
    show_preview: bool = DEFAULT_WINDOWS_NOTIFICATION_PREVIEWS_ENABLED
    announce_with_nvda: bool = DEFAULT_WINDOWS_NOTIFICATION_NVDA_ANNOUNCEMENTS_ENABLED


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path

    def load_connection(self) -> ConnectionSettings:
        data = self._load_payload()
        connection = data.get("connection", {})
        return ConnectionSettings(
            jid=str(connection.get("jid", "")),
            host=str(connection.get("host", "")),
            port=int(connection.get("port", 5222)),
            use_tls=bool(connection.get("use_tls", True)),
            remember_password=bool(connection.get("remember_password", False)),
            auto_connect=bool(connection.get("auto_connect", False)),
        )

    def save_connection(self, settings: ConnectionSettings) -> None:
        payload = self._load_payload()
        payload["connection"] = asdict(settings)
        self._save_payload(payload)

    def load_audio_speed(self) -> float:
        data = self._load_payload()
        speed = data.get("audio", {}).get("speed", DEFAULT_AUDIO_SPEED)
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return DEFAULT_AUDIO_SPEED

        return min(SUPPORTED_AUDIO_SPEEDS, key=lambda supported: abs(supported - speed))

    def save_audio_speed(self, speed: float) -> None:
        speed = min(SUPPORTED_AUDIO_SPEEDS, key=lambda supported: abs(supported - speed))
        payload = self._load_payload()
        audio = payload.get("audio", {})
        if not isinstance(audio, dict):
            audio = {}
        audio["speed"] = speed
        payload["audio"] = audio
        self._save_payload(payload)

    def load_new_chat_country(self) -> str:
        data = self._load_payload()
        new_chat = data.get("new_chat", {})
        if not isinstance(new_chat, dict):
            return DEFAULT_NEW_CHAT_COUNTRY

        country = str(new_chat.get("country", DEFAULT_NEW_CHAT_COUNTRY)).strip().upper()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            return DEFAULT_NEW_CHAT_COUNTRY
        return country

    def save_new_chat_country(self, country: str) -> None:
        country = country.strip().upper()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            country = DEFAULT_NEW_CHAT_COUNTRY
        payload = self._load_payload()
        payload["new_chat"] = {"country": country}
        self._save_payload(payload)

    def load_notification_sound_settings(self) -> tuple[bool, bool]:
        data = self._load_payload()
        sounds = data.get("notification_sounds", {})
        if not isinstance(sounds, dict):
            return (
                DEFAULT_OPEN_CHAT_MESSAGE_SOUND_ENABLED,
                DEFAULT_SENT_MESSAGE_SOUND_ENABLED,
            )

        return (
            bool(
                sounds.get(
                    "open_chat_message",
                    DEFAULT_OPEN_CHAT_MESSAGE_SOUND_ENABLED,
                )
            ),
            bool(sounds.get("sent_message", DEFAULT_SENT_MESSAGE_SOUND_ENABLED)),
        )

    def save_notification_sound_settings(
        self,
        *,
        open_chat_message_enabled: bool,
        sent_message_enabled: bool,
    ) -> None:
        payload = self._load_payload()
        payload["notification_sounds"] = {
            "open_chat_message": bool(open_chat_message_enabled),
            "sent_message": bool(sent_message_enabled),
        }
        self._save_payload(payload)

    def load_desktop_notification_settings(self) -> DesktopNotificationSettings:
        data = self._load_payload()
        notifications = data.get("windows_notifications", {})
        if not isinstance(notifications, dict):
            return DesktopNotificationSettings()

        return DesktopNotificationSettings(
            enabled=bool(
                notifications.get(
                    "enabled",
                    DEFAULT_WINDOWS_NOTIFICATIONS_ENABLED,
                )
            ),
            show_preview=bool(
                notifications.get(
                    "show_preview",
                    DEFAULT_WINDOWS_NOTIFICATION_PREVIEWS_ENABLED,
                )
            ),
            announce_with_nvda=bool(
                notifications.get(
                    "announce_with_nvda",
                    DEFAULT_WINDOWS_NOTIFICATION_NVDA_ANNOUNCEMENTS_ENABLED,
                )
            ),
        )

    def save_desktop_notification_settings(
        self,
        settings: DesktopNotificationSettings,
    ) -> None:
        payload = self._load_payload()
        payload["windows_notifications"] = asdict(settings)
        self._save_payload(payload)

    def load_minimize_to_tray_on_alt_f4(self) -> bool:
        data = self._load_payload()
        window = data.get("window", {})
        if not isinstance(window, dict):
            return DEFAULT_MINIMIZE_TO_TRAY_ON_ALT_F4
        return bool(
            window.get(
                "minimize_to_tray_on_alt_f4",
                DEFAULT_MINIMIZE_TO_TRAY_ON_ALT_F4,
            )
        )

    def save_minimize_to_tray_on_alt_f4(self, enabled: bool) -> None:
        payload = self._load_payload()
        window = payload.get("window", {})
        if not isinstance(window, dict):
            window = {}
        window["minimize_to_tray_on_alt_f4"] = bool(enabled)
        payload["window"] = window
        self._save_payload(payload)

    def load_update_check_interval_minutes(self) -> int | None:
        data = self._load_payload()
        updates = data.get("updates", {})
        if not isinstance(updates, dict):
            return DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES

        interval = updates.get(
            "check_interval_minutes",
            DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES,
        )
        if interval is None:
            return None
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            return DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES
        if interval not in UPDATE_CHECK_INTERVAL_CHOICES:
            return DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES
        return interval

    def save_update_check_interval_minutes(self, interval: int | None) -> None:
        if interval not in (*UPDATE_CHECK_INTERVAL_CHOICES, None):
            interval = DEFAULT_UPDATE_CHECK_INTERVAL_MINUTES
        payload = self._load_payload()
        updates = payload.get("updates", {})
        if not isinstance(updates, dict):
            updates = {}
        updates["check_interval_minutes"] = interval
        payload["updates"] = updates
        self._save_payload(payload)

    def _load_payload(self) -> dict[str, object]:
        if not self.path.exists():
            return {}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        return data if isinstance(data, dict) else {}

    def _save_payload(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
