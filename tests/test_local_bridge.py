from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import (
    CONNECTION_MODE_LOCAL,
    ConnectionSettings,
    SettingsStore,
)
from cliente_xmpp.local_bridge import (
    LocalBridgeConnection,
    LocalBridgeError,
    LocalBridgeService,
)
from cliente_xmpp.ui.main_window import MainWindow
from cliente_xmpp.ui.whatsapp_link_panel import WhatsAppLinkPanel
from cliente_xmpp.xmpp.client import BridgeXmppClient


class WslApplianceBuildTests(unittest.TestCase):
    @staticmethod
    def _appliance_root() -> Path:
        return Path(__file__).resolve().parents[1] / "tools" / "wsl-appliance"

    def test_generic_image_does_not_trigger_interactive_systemd_firstboot(self) -> None:
        provision_script = (
            self._appliance_root()
            / "rootfs-overlay"
            / "opt"
            / "whatsapp-can-bridge"
            / "build"
            / "provision-rootfs.sh"
        ).read_text(encoding="utf-8")

        self.assertIn(": > /etc/machine-id", provision_script)
        self.assertIn(
            "ln -s /etc/machine-id /var/lib/dbus/machine-id",
            provision_script,
        )
        self.assertNotIn("rm -f /etc/machine-id", provision_script)

    def test_installer_supports_accents_and_can_replace_protected_ca(self) -> None:
        appliance_root = self._appliance_root()
        for script_name in (
            "build-appliance.ps1",
            "install-appliance.ps1",
            "manage-appliance.ps1",
        ):
            with self.subTest(script_name=script_name):
                self.assertTrue(
                    (appliance_root / script_name)
                    .read_bytes()
                    .startswith(b"\xef\xbb\xbf")
                )

        installer_path = appliance_root / "install-appliance.ps1"
        installer_bytes = installer_path.read_bytes()
        installer_script = installer_bytes.decode("utf-8-sig")

        grant_ca = (
            'Invoke-Native icacls.exe @($CaCertificateFile, "/grant:r", '
            '"${identity}:(F)")'
        )
        grant_position = installer_script.index(grant_ca)
        write_position = installer_script.index(
            "[IO.File]::WriteAllText($CaCertificateFile"
        )
        self.assertLess(grant_position, write_position)

    def test_online_setup_manifest_matches_the_local_appliance(self) -> None:
        appliance_root = self._appliance_root()
        manifest = json.loads(
            (appliance_root / "release-manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["schema_version"], 1)
        self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(manifest["download_url"].startswith("https://github.com/"))
        self.assertTrue(manifest["download_url"].endswith(manifest["asset_name"]))
        self.assertGreater(manifest["size_bytes"], 0)

        artifact = (
            Path(__file__).resolve().parents[1]
            / "dist"
            / "wsl"
            / manifest["asset_name"]
        )
        if artifact.is_file():
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(artifact.stat().st_size, manifest["size_bytes"])
            self.assertEqual(digest, manifest["sha256"])

    def test_public_legacy_updater_is_pinned_and_utf8_bom(self) -> None:
        appliance_root = self._appliance_root()
        updater_path = appliance_root / "actualizar-puente-local.ps1"
        updater_bytes = updater_path.read_bytes()
        self.assertTrue(updater_bytes.startswith(b"\xef\xbb\xbf"))
        updater = updater_bytes.decode("utf-8-sig")
        manifest = json.loads(
            (appliance_root / "release-manifest.json").read_text(encoding="utf-8")
        )
        installer_path = appliance_root / "install-appliance.ps1"
        installer_hash = hashlib.sha256(installer_path.read_bytes()).hexdigest()

        self.assertEqual(manifest["appliance_version"], "1.1.0")
        self.assertEqual(
            manifest["updater_asset_name"],
            updater_path.name,
        )
        self.assertIn(manifest["download_url"], updater)
        self.assertIn(manifest["sha256"], updater)
        self.assertIn(str(manifest["size_bytes"]), updater)
        self.assertIn(installer_hash, updater)
        self.assertIn("wsl-appliance-v1.1.0", updater)
        self.assertIn("-InstallOrResume", updater)
        self.assertIn("Start-BitsTransfer", updater)
        self.assertIn("Invoke-WebRequest", updater)
        self.assertIn("Escribe ACTUALIZAR para continuar", updater)
        self.assertIn("se restaurará automáticamente la anterior", updater)
        self.assertIn('$confirmation.Trim() -ine "ACTUALIZAR"', updater)
        self.assertNotIn("exit 0", updater)
        self.assertNotIn("--show-password", updater)

        publisher = (appliance_root / "publish-appliance-release.ps1").read_text(
            encoding="utf-8-sig"
        )
        builder = (appliance_root / "build-public-updater.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("$updaterAssetName", publisher)
        self.assertIn("$updaterChecksumPath", publisher)
        self.assertIn("$updaterHash", publisher)
        self.assertIn("build-public-updater.ps1", publisher)
        self.assertIn("FromBase64String", builder)
        self.assertIn("[Text.ASCIIEncoding]::new()", builder)
        self.assertIn("Start-Transcript", builder)
        self.assertIn("actualizacion-puente-{0}.log", builder)
        self.assertIn('[string]$ManifestPath = ""', publisher)
        self.assertIn('[string]$ArtifactDirectory = ""', publisher)
        self.assertLess(
            publisher.index("Set-StrictMode"),
            publisher.index("$ManifestPath = if ($ManifestPath)"),
        )

        with tempfile.TemporaryDirectory() as directory:
            public_updater = Path(directory) / updater_path.name
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(appliance_root / "build-public-updater.ps1"),
                    "-SourcePath",
                    str(updater_path),
                    "-DestinationPath",
                    str(public_updater),
                ],
                check=False,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            public_bytes = public_updater.read_bytes()
            self.assertTrue(public_bytes)
            self.assertTrue(all(byte < 128 for byte in public_bytes))
            encoded = re.search(
                rb"FromBase64String\('([A-Za-z0-9+/=]+)'\)",
                public_bytes,
            )
            self.assertIsNotNone(encoded)
            decoded = base64.b64decode(encoded.group(1))
            self.assertEqual(decoded, updater_bytes[3:])

    def test_bridge_updates_are_digest_pinned_and_independent_from_systemd(self) -> None:
        appliance_root = self._appliance_root()
        overlay = appliance_root / "rootfs-overlay"
        update_manifest = json.loads(
            (appliance_root / "bridge-update-manifest.json").read_text(encoding="utf-8")
        )
        version = json.loads(
            (
                overlay
                / "opt"
                / "whatsapp-can-bridge"
                / "version.json"
            ).read_text(encoding="utf-8")
        )
        service = (
            overlay
            / "etc"
            / "systemd"
            / "system"
            / "whatsapp-can-slidge.service"
        ).read_text(encoding="utf-8")
        updater = (
            overlay
            / "usr"
            / "local"
            / "libexec"
            / "whatsapp-can-bridge-image"
        ).read_text(encoding="utf-8")
        provision = (
            overlay
            / "opt"
            / "whatsapp-can-bridge"
            / "build"
            / "provision-rootfs.sh"
        ).read_text(encoding="utf-8")
        manager = (appliance_root / "manage-appliance.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertEqual(update_manifest["schema_version"], 1)
        self.assertRegex(
            update_manifest["image"],
            r"^ghcr\.io/marcomolinaleija/cliente-xmpp-bridge:v[1-9][0-9]*$",
        )
        self.assertRegex(update_manifest["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(update_manifest["channel"], "stable")
        self.assertEqual(
            update_manifest["bridge_version"],
            int(update_manifest["image"].rsplit(":v", 1)[1]),
        )
        self.assertRegex(
            version["bridge_image"],
            r"^ghcr\.io/marcomolinaleija/cliente-xmpp-bridge:v[1-9][0-9]*$",
        )
        self.assertRegex(version["bridge_digest"], r"^sha256:[0-9a-f]{64}$")
        bundled_bridge_version = int(version["bridge_image"].rsplit(":v", 1)[1])
        self.assertGreaterEqual(
            update_manifest["bridge_version"], bundled_bridge_version
        )
        self.assertTrue(version["bridge_updates"])

        self.assertIn("whatsapp-can-bridge-image run", service)
        self.assertNotIn("ghcr.io/", service)
        self.assertIn("create_state_backup", updater)
        self.assertIn("restore_state_backup", updater)
        self.assertIn("rollback_update", updater)
        self.assertIn("normalize_image_id", updater)
        self.assertIn("--proto '=https'", updater)
        self.assertIn('exec podman run --rm --name whatsapp-can-slidge', updater)
        self.assertIn('curl \\\n', provision)
        self.assertIn(
            f'{version["bridge_image"]}@{version["bridge_digest"]}', provision
        )
        self.assertTrue((appliance_root / "test-bridge-image-updater.sh").is_file())
        self.assertIn('"update" {', manager)
        self.assertIn('"--manifest-url"', manager)
        self.assertIn(
            "chmod 0644 /etc/systemd/system/whatsapp-can-slidge.service",
            provision,
        )
        save_position = provision.index("podman save --format oci-archive")
        load_position = provision.index('podman load -i "$image_directory/slidge-v19.oci"')
        inspect_position = provision.index(
            "podman image inspect \"$bridge_image\" --format '{{.Id}}'"
        )
        self.assertLess(save_position, load_position)
        self.assertLess(load_position, inspect_position)

    def test_local_http_upload_is_loopback_only_and_limited_to_200_mib(self) -> None:
        appliance_root = self._appliance_root()
        overlay = appliance_root / "rootfs-overlay"
        prosody = (
            overlay
            / "opt"
            / "whatsapp-can-bridge"
            / "templates"
            / "prosody.cfg.lua.in"
        ).read_text(encoding="utf-8")
        control = (
            overlay / "usr" / "local" / "sbin" / "whatsapp-can-bridge"
        ).read_text(encoding="utf-8")
        installer = (appliance_root / "install-appliance.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn('interfaces = { "127.0.0.1", "::1" }', prosody)
        self.assertIn("http_ports = { 5280 }", prosody)
        self.assertIn(
            'http_external_url = "http://127.0.0.1:5280/"',
            prosody,
        )
        self.assertIn(
            'Component "upload.@@XMPP_DOMAIN@@" "http_file_share"',
            prosody,
        )
        self.assertIn('http_host = "127.0.0.1"', prosody)
        self.assertIn(
            "http_file_share_size_limit = 200 * 1024 * 1024",
            prosody,
        )
        self.assertIn("http_file_share_expires_after = 7 * 24 * 60 * 60", prosody)
        self.assertIn("127\\.0\\.0\\.1:5280", control)
        self.assertIn("http://127.0.0.1:5280/file_share/", control)
        self.assertIn("foreach ($requiredPort in 5222, 5280, 8080)", installer)
        upload_smoke = appliance_root / "smoke_local_upload.py"
        self.assertTrue(upload_smoke.is_file())
        upload_smoke_source = upload_smoke.read_text(encoding="utf-8")
        self.assertIn("MAX_UPLOAD_BYTES = 200 * 1024 * 1024", upload_smoke_source)
        self.assertIn("MAX_UPLOAD_BYTES + 1", upload_smoke_source)
        self.assertIn("session.get(get_url", upload_smoke_source)

    def test_inno_setup_offers_local_or_remote_and_verifies_download(self) -> None:
        setup_script = (
            Path(__file__).resolve().parents[1] / "installer" / "WhatsApp-CAN.iss"
        ).read_text(encoding="utf-8")

        self.assertIn("¿Cómo quieres usar WhatsApp CAN?", setup_script)
        self.assertIn("Puente local en este equipo", setup_script)
        self.assertIn("Servidor XMPP o VPS", setup_script)
        self.assertIn("DownloadTemporaryFile(", setup_script)
        self.assertIn("'{#WslPackageSha256}'", setup_script)
        self.assertIn("-ExpectedPackageSha256", setup_script)
        self.assertIn("BridgeInstallReady", setup_script)
        self.assertIn("ResultCode <> 0", setup_script)
        self.assertIn("{sysnative}\\wsl.exe", setup_script)
        self.assertIn("--set-connection-mode", setup_script)

        install_script = (
            self._appliance_root() / "install-appliance.ps1"
        ).read_text(encoding="utf-8-sig")
        self.assertRegex(install_script, re.compile(r"\[string\]\$ExpectedPackageSha256"))
        self.assertIn("[switch]$InstallOrResume", install_script)
        self.assertIn("function Invoke-LegacyMigration", install_script)
        self.assertIn("function Restore-LegacyBackup", install_script)
        self.assertIn("function Recover-InterruptedMigration", install_script)
        self.assertIn('"--export", $Distribution, $fullBackup', install_script)
        self.assertIn("legacy-upgrade-journal.json", install_script)
        self.assertIn("etc/whatsapp-can-bridge/credentials", install_script)
        self.assertIn("var/lib/whatsapp-can-bridge/slidge", install_script)
        self.assertIn("var/lib/whatsapp-can-bridge/attachments", install_script)
        self.assertIn("var/lib/prosody", install_script)
        self.assertIn("Test-ModernAppliance", install_script)
        self.assertNotIn("Remove-Item -Recurse", install_script)


class LocalBridgeServiceTests(unittest.TestCase):
    def _contract(self, directory: str, **overrides: object) -> tuple[Path, Path]:
        root = Path(directory)
        ca_file = root / "bridge-ca.crt"
        ca_file.write_text("test ca", encoding="utf-8")
        payload: dict[str, object] = {
            "jid": "whatsappcan@xmpp.whatsappcan.local",
            "password": "local-secret",
            "host": "127.0.0.1",
            "port": 5222,
            "use_tls": True,
            "ca_file": str(ca_file),
        }
        payload.update(overrides)
        connection_file = root / "bridge-connection.json"
        connection_file.write_text(json.dumps(payload), encoding="utf-8")
        return connection_file, ca_file

    @staticmethod
    def _runner(commands: list[list[str]]) -> object:
        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            commands.append(command)
            stdout = b""
            if command[1:] == ["--list", "--quiet"]:
                stdout = "Ubuntu\nWhatsAppCAN-Bridge\n".encode("utf-16-le")
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

        return run

    @staticmethod
    def _process_factory(commands: list[list[str]]) -> tuple[object, Mock]:
        process = Mock()
        process.poll.return_value = None
        process.stdin = io.BytesIO()
        process.wait.return_value = 0

        def create(command: list[str], **_kwargs: object) -> Mock:
            commands.append(command)
            return process

        return create, process

    def test_prepare_starts_smokes_and_validates_local_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, ca_file = self._contract(directory)
            commands: list[list[str]] = []
            keepalive_commands: list[list[str]] = []
            process_factory, process = self._process_factory(keepalive_commands)
            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=self._runner(commands),
                process_factory=process_factory,
            )

            result = service.prepare()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.password, "local-secret")
            self.assertEqual(result.settings.ca_file, str(ca_file))
            self.assertTrue(result.settings.auto_connect)
            self.assertEqual(commands[0], ["wsl.exe", "--list", "--quiet"])
            self.assertEqual(commands[1][-1], "start")
            self.assertEqual(commands[2][-1], "smoke")
            self.assertEqual(keepalive_commands[0][-1], "keepalive")

            service.close()

            self.assertTrue(process.stdin.closed)
            process.wait.assert_called_once_with(timeout=3)

    def test_prepare_stops_keepalive_when_smoke_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, _ca_file = self._contract(directory)
            keepalive_commands: list[list[str]] = []
            process_factory, process = self._process_factory(keepalive_commands)

            def runner(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                if command[1:] == ["--list", "--quiet"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout="WhatsAppCAN-Bridge\n".encode("utf-16-le"),
                        stderr=b"",
                    )
                if command[-1] == "smoke":
                    return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"fallo")
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=runner,
                process_factory=process_factory,
            )

            with self.assertRaisesRegex(LocalBridgeError, "fallo"):
                service.prepare()

            self.assertEqual(keepalive_commands[0][-1], "keepalive")
            self.assertTrue(process.stdin.closed)

    def test_prepare_is_inactive_without_a_windows_contract(self) -> None:
        service = LocalBridgeService(
            connection_file=Path("missing.json"),
            platform_name="posix",
            runner=Mock(side_effect=AssertionError("runner should not be called")),
        )

        self.assertIsNone(service.prepare())

    def test_prepare_rejects_non_loopback_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, _ca_file = self._contract(directory, host="192.0.2.5")
            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=self._runner([]),
            )

            with self.assertRaisesRegex(LocalBridgeError, "loopback"):
                service.prepare()

    def test_prepare_rejects_contract_without_starttls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, _ca_file = self._contract(directory, use_tls=False)
            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=self._runner([]),
            )

            with self.assertRaisesRegex(LocalBridgeError, "STARTTLS"):
                service.prepare()

    def test_remove_plaintext_password_preserves_connection_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, _ca_file = self._contract(directory)
            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=self._runner([]),
            )

            service.remove_plaintext_password()

            payload = json.loads(connection_file.read_text(encoding="utf-8"))
            self.assertNotIn("password", payload)
            self.assertEqual(payload["jid"], "whatsappcan@xmpp.whatsappcan.local")

    def test_command_failure_surfaces_the_last_error_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection_file, _ca_file = self._contract(directory)

            def failing_runner(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout=b"",
                    stderr=b"detalle anterior\nfallo final",
                )

            service = LocalBridgeService(
                connection_file=connection_file,
                platform_name="nt",
                runner=failing_runner,
            )

            with self.assertRaisesRegex(LocalBridgeError, "fallo final"):
                service.prepare()


class CredentialStoreTests(unittest.TestCase):
    def test_save_password_reports_success(self) -> None:
        keyring = Mock()
        with patch("cliente_xmpp.config.credentials._load_keyring", return_value=keyring):
            saved = CredentialStore().save_password("user@example.test", "secret")

        self.assertTrue(saved)
        keyring.set_password.assert_called_once()

    def test_save_password_reports_keyring_failure(self) -> None:
        keyring = Mock()
        keyring.set_password.side_effect = RuntimeError("unavailable")
        with patch("cliente_xmpp.config.credentials._load_keyring", return_value=keyring):
            saved = CredentialStore().save_password("user@example.test", "secret")

        self.assertFalse(saved)


class LocalBridgeUiTests(unittest.TestCase):
    @staticmethod
    def _window(*, save_password: bool = True, stored_password: str = "") -> SimpleNamespace:
        credential_store = Mock()
        credential_store.save_password.return_value = save_password
        credential_store.get_password.return_value = stored_password
        return SimpleNamespace(
            local_bridge_startup_in_progress=True,
            IsBeingDeleted=Mock(return_value=False),
            credential_store=credential_store,
            local_bridge=Mock(),
            settings_store=Mock(),
            login_panel=Mock(),
            status_bar=Mock(),
            _on_connect=Mock(),
            _show_local_bridge_error=Mock(),
            connection_settings=ConnectionSettings(),
        )

    @staticmethod
    def _connection(password: str) -> LocalBridgeConnection:
        return LocalBridgeConnection(
            settings=ConnectionSettings(
                jid="whatsappcan@xmpp.whatsappcan.local",
                host="127.0.0.1",
                port=5222,
                use_tls=True,
                ca_file=r"C:\WhatsAppCAN\bridge-ca.crt",
                remember_password=True,
                auto_connect=True,
            ),
            password=password,
            connection_file=Path("bridge-connection.json"),
        )

    def test_ui_migrates_plaintext_then_connects(self) -> None:
        window = self._window()
        connection = self._connection("local-secret")

        MainWindow._finish_local_bridge_startup(window, connection, "")

        window.credential_store.save_password.assert_called_once_with(
            connection.settings.jid,
            "local-secret",
        )
        window.local_bridge.remove_plaintext_password.assert_called_once_with()
        window.settings_store.save_connection_profile.assert_called_once_with(
            CONNECTION_MODE_LOCAL,
            connection.settings,
        )
        window.login_panel.set_connection.assert_called_once_with(
            connection.settings,
            "local-secret",
        )
        window._on_connect.assert_called_once()

    def test_ui_uses_keyring_after_contract_was_migrated(self) -> None:
        window = self._window(stored_password="stored-secret")
        connection = self._connection("")

        MainWindow._finish_local_bridge_startup(window, connection, "")

        window.credential_store.save_password.assert_not_called()
        window.local_bridge.remove_plaintext_password.assert_not_called()
        window.login_panel.set_connection.assert_called_once_with(
            connection.settings,
            "stored-secret",
        )
        window._on_connect.assert_called_once()

    def test_ui_keeps_json_when_keyring_migration_fails(self) -> None:
        window = self._window(save_password=False)
        connection = self._connection("local-secret")

        MainWindow._finish_local_bridge_startup(window, connection, "")

        window.local_bridge.remove_plaintext_password.assert_not_called()
        window._show_local_bridge_error.assert_called_once()
        window._on_connect.assert_not_called()

    def test_link_panel_focuses_primary_action_when_visible(self) -> None:
        panel = SimpleNamespace(
            IsShownOnScreen=Mock(return_value=True),
            open_button=Mock(),
        )
        panel.open_button.IsEnabled.return_value = True

        WhatsAppLinkPanel.focus_action(panel)

        panel.open_button.SetFocus.assert_called_once_with()


class LocalBridgeXmppDiscoveryTests(unittest.TestCase):
    def test_local_contract_uses_direct_whatsapp_component(self) -> None:
        client = SimpleNamespace(
            settings=ConnectionSettings(
                jid="whatsappcan@xmpp.whatsappcan.local",
                host="127.0.0.1",
            )
        )

        component = BridgeXmppClient._configured_local_whatsapp_component(client)

        self.assertEqual(component, "whatsapp.xmpp.whatsappcan.local")

    def test_remote_connection_does_not_guess_component(self) -> None:
        client = SimpleNamespace(
            settings=ConnectionSettings(
                jid="user@example.org",
                host="xmpp.example.org",
            )
        )

        component = BridgeXmppClient._configured_local_whatsapp_component(client)

        self.assertEqual(component, "")


class LocalBridgeProfileMigrationTests(unittest.TestCase):
    def test_imports_remote_profile_from_pre_local_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_store = SettingsStore(root / "settings.json")
            current_store.save_connection(
                ConnectionSettings(
                    jid="whatsappcan@xmpp.whatsappcan.local",
                    host="127.0.0.1",
                )
            )
            backup_path = root / "settings-before-local-bridge.json"
            backup_store = SettingsStore(backup_path)
            remote = ConnectionSettings(
                jid="usuario@servidor.example",
                host="servidor.example",
                remember_password=True,
                auto_connect=True,
            )
            backup_store.save_connection(remote)
            window = SimpleNamespace(
                settings_store=current_store,
                local_bridge=SimpleNamespace(remote_settings_backup_file=backup_path),
            )

            loaded = MainWindow._load_connection_settings_for_mode(window, "remote")

            self.assertEqual(loaded, remote)
            self.assertEqual(current_store.load_connection_profile("remote"), remote)


if __name__ == "__main__":
    unittest.main()
