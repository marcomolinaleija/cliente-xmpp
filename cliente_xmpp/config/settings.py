from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


APP_DIR = Path.home() / ".cliente-xmpp"
SETTINGS_PATH = APP_DIR / "settings.json"


@dataclass(slots=True)
class ConnectionSettings:
    jid: str = ""
    host: str = ""
    port: int = 5222
    use_tls: bool = True


class SettingsStore:
    def __init__(self, path: Path = SETTINGS_PATH) -> None:
        self.path = path

    def load_connection(self) -> ConnectionSettings:
        if not self.path.exists():
            return ConnectionSettings()

        data = json.loads(self.path.read_text(encoding="utf-8"))
        connection = data.get("connection", {})
        return ConnectionSettings(
            jid=str(connection.get("jid", "")),
            host=str(connection.get("host", "")),
            port=int(connection.get("port", 5222)),
            use_tls=bool(connection.get("use_tls", True)),
        )

    def save_connection(self, settings: ConnectionSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"connection": asdict(settings)}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

