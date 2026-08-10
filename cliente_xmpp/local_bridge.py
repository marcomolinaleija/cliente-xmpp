from __future__ import annotations

import json
import locale
import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cliente_xmpp.config.settings import ConnectionSettings

DEFAULT_DISTRO_NAME = "WhatsAppCAN-Bridge"
EXPECTED_LOCAL_JID = "whatsappcan@xmpp.whatsappcan.local"
EXPECTED_LOCAL_HOST = "127.0.0.1"
EXPECTED_LOCAL_PORT = 5222
BRIDGE_CONTROL = "/usr/local/sbin/whatsapp-can-bridge"
COMMAND_TIMEOUT_SECONDS = 45


class LocalBridgeError(RuntimeError):
    """An installed local bridge could not be prepared safely."""


@dataclass(frozen=True, slots=True)
class LocalBridgeConnection:
    settings: ConnectionSettings
    password: str
    connection_file: Path

    @property
    def needs_password_migration(self) -> bool:
        return bool(self.password)


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]
ProcessFactory = Callable[..., subprocess.Popen[bytes]]


class LocalBridgeService:
    def __init__(
        self,
        *,
        distro_name: str | None = None,
        connection_file: Path | None = None,
        remote_settings_backup_file: Path | None = None,
        platform_name: str | None = None,
        runner: CommandRunner = subprocess.run,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        configured_connection_file = os.environ.get(
            "WHATSAPP_CAN_BRIDGE_CONNECTION_FILE",
            "",
        ).strip()
        self.distro_name = (
            distro_name
            or os.environ.get("WHATSAPP_CAN_WSL_DISTRO", "").strip()
            or DEFAULT_DISTRO_NAME
        )
        self.connection_file = connection_file or (
            Path(configured_connection_file)
            if configured_connection_file
            else local_app_data / "WhatsAppCAN" / "bridge-connection.json"
        )
        self.remote_settings_backup_file = remote_settings_backup_file or (
            local_app_data
            / "WhatsAppCAN"
            / "migration-backups"
            / "settings-before-local-bridge.json"
        )
        self.platform_name = platform_name or os.name
        self._runner = runner
        self._process_factory = process_factory
        self._keepalive_lock = threading.Lock()
        self._keepalive_process: subprocess.Popen[bytes] | None = None
        self._closed = False

    def has_connection_contract(self) -> bool:
        return self.platform_name == "nt" and self.connection_file.is_file()

    def prepare(self) -> LocalBridgeConnection | None:
        if not self.has_connection_contract():
            return None
        connection = self._load_connection()
        if self.distro_name not in self._distribution_names():
            raise LocalBridgeError(
                f"No existe la distribución local {self.distro_name}. "
                "Repara o reinstala el puente WSL2."
            )

        self.start_keepalive()
        try:
            self._run_wsl_bridge_action("start")
            self._run_wsl_bridge_action("smoke")
        except Exception:
            self.stop_keepalive()
            raise
        return connection

    def start_keepalive(self) -> None:
        if self.platform_name != "nt":
            return
        with self._keepalive_lock:
            if self._closed:
                raise LocalBridgeError("El puente local ya se estÃ¡ cerrando.")
            if self._keepalive_process is not None:
                if self._keepalive_process.poll() is None:
                    return
                self._keepalive_process = None
            try:
                self._keepalive_process = self._process_factory(
                    [
                        "wsl.exe",
                        "-d",
                        self.distro_name,
                        "-u",
                        "root",
                        "--",
                        BRIDGE_CONTROL,
                        "keepalive",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (FileNotFoundError, OSError) as exc:
                raise LocalBridgeError("No se pudo mantener activo el puente WSL2.") from exc

    def stop_keepalive(self) -> None:
        with self._keepalive_lock:
            process = self._keepalive_process
            self._keepalive_process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def close(self) -> None:
        with self._keepalive_lock:
            self._closed = True
        self.stop_keepalive()

    def remove_plaintext_password(self) -> None:
        payload = self._load_payload()
        if "password" not in payload:
            return
        payload.pop("password", None)
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        # Truncating the existing file preserves the restrictive Windows ACL
        # applied by the appliance installer.
        with self.connection_file.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)

    def _distribution_names(self) -> set[str]:
        completed = self._run_command(("wsl.exe", "--list", "--quiet"))
        output = _decode_command_output(completed.stdout)
        return {line.replace("\x00", "").strip() for line in output.splitlines() if line.strip()}

    def _run_wsl_bridge_action(self, action: str) -> None:
        self._run_command(
            (
                "wsl.exe",
                "-d",
                self.distro_name,
                "-u",
                "root",
                "--",
                BRIDGE_CONTROL,
                action,
            )
        )

    def _run_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = self._runner(
                list(command),
                check=False,
                capture_output=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise LocalBridgeError("WSL2 no está disponible en este equipo.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalBridgeError("El puente local tardó demasiado en responder.") from exc
        except OSError as exc:
            raise LocalBridgeError(f"No se pudo ejecutar WSL2: {exc}") from exc

        if completed.returncode != 0:
            detail = _decode_command_output(completed.stderr).strip()
            if not detail:
                detail = _decode_command_output(completed.stdout).strip()
            detail = detail.splitlines()[-1] if detail else "sin detalle adicional"
            raise LocalBridgeError(f"El puente local rechazó la operación: {detail}")
        return completed

    def _load_connection(self) -> LocalBridgeConnection:
        payload = self._load_payload()
        jid = str(payload.get("jid", "")).strip().casefold()
        host = str(payload.get("host", "")).strip()
        try:
            port = int(payload.get("port", 0))
        except (TypeError, ValueError) as exc:
            raise LocalBridgeError("El contrato local contiene un puerto XMPP inválido.") from exc
        use_tls = payload.get("use_tls") is True
        ca_file = Path(str(payload.get("ca_file", "")).strip())

        if jid != EXPECTED_LOCAL_JID:
            raise LocalBridgeError("El contrato local contiene un JID XMPP inesperado.")
        if host != EXPECTED_LOCAL_HOST or port != EXPECTED_LOCAL_PORT:
            raise LocalBridgeError("El contrato local intenta usar un servidor que no es loopback.")
        if not use_tls:
            raise LocalBridgeError("El contrato local no exige STARTTLS.")
        if not ca_file.is_absolute() or not ca_file.is_file():
            raise LocalBridgeError("No se encontró la CA privada de la instalación local.")

        settings = ConnectionSettings(
            jid=EXPECTED_LOCAL_JID,
            host=EXPECTED_LOCAL_HOST,
            port=EXPECTED_LOCAL_PORT,
            use_tls=True,
            ca_file=str(ca_file),
            remember_password=True,
            auto_connect=True,
        )
        return LocalBridgeConnection(
            settings=settings,
            password=str(payload.get("password", "")),
            connection_file=self.connection_file,
        )

    def _load_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.connection_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LocalBridgeError("No se encontró el contrato de conexión local.") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LocalBridgeError("El contrato de conexión local está dañado.") from exc
        if not isinstance(payload, dict):
            raise LocalBridgeError("El contrato de conexión local tiene un formato inválido.")
        return payload


def _decode_command_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if not value:
        return ""
    if b"\x00" in value:
        return value.decode("utf-16-le", errors="replace").replace("\x00", "")
    for encoding in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")
