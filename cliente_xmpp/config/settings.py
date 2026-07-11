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


@dataclass(slots=True)
class ConnectionSettings:
    jid: str = ""
    host: str = ""
    port: int = 5222
    use_tls: bool = True
    remember_password: bool = False
    auto_connect: bool = False


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
