from __future__ import annotations

import json
import locale
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
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
BRIDGE_UPDATE_TIMEOUT_SECONDS = 20 * 60
BRIDGE_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/marcomolinaleija/cliente-xmpp/main/"
    "tools/wsl-appliance/bridge-update-manifest.json"
)
BRIDGE_IMAGE_MANAGER = "/usr/local/libexec/whatsapp-can-bridge-image"
ALLOWED_BRIDGE_REPOSITORY = "ghcr.io/marcomolinaleija/cliente-xmpp-bridge"
BRIDGE_IMAGE_PATTERN = re.compile(
    rf"^{re.escape(ALLOWED_BRIDGE_REPOSITORY)}:v(?P<version>[1-9][0-9]*)$"
)
BRIDGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_UPDATE_MANIFEST_BYTES = 64 * 1024


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


@dataclass(frozen=True, slots=True)
class LocalBridgeUpdateStatus:
    supports_updates: bool
    current_version: int | None = None
    target_version: int | None = None
    current_image: str = ""
    current_digest: str = ""
    target_image: str = ""
    target_digest: str = ""
    update_available: bool = False


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
        update_manifest_url: str = BRIDGE_UPDATE_MANIFEST_URL,
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
        self.update_manifest_url = update_manifest_url
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

    def check_for_update(self) -> LocalBridgeUpdateStatus:
        installed = self.installed_update_status()
        if not installed.supports_updates:
            return installed

        manifest = self._load_update_manifest()
        target_image, target_version, target_digest = self._validated_image(manifest)
        declared_version = manifest.get("bridge_version")
        if type(declared_version) is not int or declared_version != target_version:
            raise LocalBridgeError("El manifiesto publicó una versión de puente inconsistente.")
        if manifest.get("schema_version") != 1 or manifest.get("channel") != "stable":
            raise LocalBridgeError("El manifiesto estable del puente no es compatible.")
        current_version = installed.current_version
        if current_version is None:
            raise LocalBridgeError("El appliance no informó la versión instalada del puente.")
        if target_version < current_version:
            raise LocalBridgeError("El canal estable intentó ofrecer una versión anterior.")
        if target_version == current_version and target_digest != installed.current_digest:
            raise LocalBridgeError("El canal estable cambió el digest de la versión instalada.")
        return LocalBridgeUpdateStatus(
            supports_updates=True,
            current_version=current_version,
            target_version=target_version,
            current_image=installed.current_image,
            current_digest=installed.current_digest,
            target_image=target_image,
            target_digest=target_digest,
            update_available=target_version > current_version,
        )

    def installed_update_status(self) -> LocalBridgeUpdateStatus:
        if self.platform_name != "nt" or not self.has_connection_contract():
            return LocalBridgeUpdateStatus(supports_updates=False)
        if self.distro_name not in self._distribution_names():
            raise LocalBridgeError(f"No existe la distribución local {self.distro_name}.")
        capability = self._run_command(
            (
                "wsl.exe",
                "-d",
                self.distro_name,
                "-u",
                "root",
                "--",
                "test",
                "-x",
                BRIDGE_IMAGE_MANAGER,
            ),
            allow_failure=True,
        )
        if capability.returncode != 0:
            return LocalBridgeUpdateStatus(supports_updates=False)

        completed = self._run_wsl_bridge_action("status")
        status = self._json_object(completed.stdout, "estado del puente")
        bridge = status.get("bridge")
        if not isinstance(bridge, dict):
            raise LocalBridgeError("El appliance no informó la imagen activa del puente.")
        current_image, current_version, current_digest = self._validated_image(bridge)
        return LocalBridgeUpdateStatus(
            supports_updates=True,
            current_version=current_version,
            current_image=current_image,
            current_digest=current_digest,
        )

    def update_bridge(self) -> str:
        completed = self._run_wsl_bridge_action(
            "update",
            timeout=BRIDGE_UPDATE_TIMEOUT_SECONDS,
        )
        return _decode_command_output(completed.stdout).strip()

    def _run_wsl_bridge_action(
        self,
        action: str,
        *,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._run_command(
            (
                "wsl.exe",
                "-d",
                self.distro_name,
                "-u",
                "root",
                "--",
                BRIDGE_CONTROL,
                action,
            ),
            timeout=timeout,
        )

    def _run_command(
        self,
        command: Sequence[str],
        *,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = self._runner(
                list(command),
                check=False,
                capture_output=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise LocalBridgeError("WSL2 no está disponible en este equipo.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LocalBridgeError("El puente local tardó demasiado en responder.") from exc
        except OSError as exc:
            raise LocalBridgeError(f"No se pudo ejecutar WSL2: {exc}") from exc

        if completed.returncode != 0 and not allow_failure:
            detail = _decode_command_output(completed.stderr).strip()
            if not detail:
                detail = _decode_command_output(completed.stdout).strip()
            detail = detail.splitlines()[-1] if detail else "sin detalle adicional"
            raise LocalBridgeError(f"El puente local rechazó la operación: {detail}")
        return completed

    def _load_update_manifest(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.update_manifest_url,
            headers={"User-Agent": "WhatsApp-CAN-Bridge-Updater"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = response.read(MAX_UPDATE_MANIFEST_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LocalBridgeError(
                f"El canal del puente respondió con HTTP {exc.code}."
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise LocalBridgeError("No se pudo consultar el canal estable del puente.") from exc
        if len(payload) > MAX_UPDATE_MANIFEST_BYTES:
            raise LocalBridgeError("El manifiesto del puente es demasiado grande.")
        return self._json_object(payload, "manifiesto del puente")

    @staticmethod
    def _json_object(value: bytes | str | None, description: str) -> dict[str, Any]:
        try:
            payload = json.loads(_decode_command_output(value))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LocalBridgeError(f"El {description} no contiene JSON válido.") from exc
        if not isinstance(payload, dict):
            raise LocalBridgeError(f"El {description} tiene un formato inválido.")
        return payload

    @staticmethod
    def _validated_image(payload: dict[str, Any]) -> tuple[str, int, str]:
        image = str(payload.get("image", "")).strip()
        digest = str(payload.get("digest", "")).strip()
        match = BRIDGE_IMAGE_PATTERN.fullmatch(image)
        if match is None or BRIDGE_DIGEST_PATTERN.fullmatch(digest) is None:
            raise LocalBridgeError("La identidad de la imagen del puente no es segura.")
        return image, int(match.group("version")), digest

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
