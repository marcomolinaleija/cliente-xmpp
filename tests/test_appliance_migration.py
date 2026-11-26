from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tools" / "wsl-appliance"


def powershell_environment(**extra: str) -> dict[str, str]:
    # Do not inherit PowerShell Core's module path into Windows PowerShell 5.1.
    env = dict(os.environ, **extra)
    env["PSModulePath"] = os.pathsep.join(
        [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules"),
            str(Path(os.environ["ProgramFiles"]) / "WindowsPowerShell/Modules"),
        ]
    )
    return env


class ApplianceMigrationTests(unittest.TestCase):
    def run_ps(self, body: str, *, source: str = "install-appliance.ps1") -> str:
        with tempfile.TemporaryDirectory() as directory:
            env = powershell_environment(CAN_FIXTURE=directory, CAN_SOURCE=str(ROOT / source))
            script = (
                r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:CAN_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([ScriptBlock]::Create($node.Extent.Text))
    }
}
function Assert($condition, $message) { if (-not $condition) { throw $message } }
"""
                + body
            )
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-OutputFormat",
                    "Text",
                    "-EncodedCommand",
                    encoded,
                ],
                env=env,
                capture_output=True,
                timeout=40,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            return result.stdout.decode(errors="replace")

    def test_hidden_install_failure_is_recorded_in_the_log(self):
        # The Windows installer runs the script hidden; the log must explain the failure.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "fixture-package.txt"
            package.write_text("fictitious", encoding="utf-8")
            log = root / "logs" / "bridge-install-fixture.log"
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "install-appliance.ps1"),
                    "-PackagePath", str(package), "-InstallOrResume", "-LogPath", str(log),
                ],
                env=powershell_environment(),
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 15)  # Damaged or unexpected package.
            self.assertTrue(log.is_file())
            text = log.read_bytes().decode("utf-8", errors="replace").replace(chr(0), "")
            self.assertIn("Configuración del puente local", text)
            self.assertIn("ERROR: El paquete debe tener extensión .wsl.", text)
            self.assertIn("POSIBLE CAUSA (15):", text)
            self.assertIn("QUÉ HACER:", text)

    def test_install_failures_map_to_stable_codes_and_hints(self):
        output = self.run_ps(
            r"""
$cases = @(
    @(10, 'El puerto local 8080 ya está en uso. Detén la otra instancia.', @()),
    @(11, 'La carpeta de instalación no está vacía: C:/fixture/WSL', @()),
    @(15, 'El paquete .wsl no coincide con el SHA-256 esperado.', @()),
    @(16, 'Se rechazó reemplazar el appliance 1.3.0 por una versión anterior (1.2.0).', @()),
    @(17, 'Ya hay una instalación o migración activa para esta distribución.', @()),
    @(14, "El comando 'wsl.exe --install' terminó con código 1.", @('Error code: Wsl/0x80070070')),
    @(12, "El comando 'wsl.exe --install' terminó con código 1.", @('Error: 0x80370102 fixture')),
    @(13, 'Se requiere WSL 2.4.4.0 o posterior; está instalada la versión 2.0.9.0.', @()),
    @(18, 'La instalación no confirmó la versión y las capacidades requeridas, incluida voz.', @()),
    @(19, 'La distribución Fixture está registrada pero su disco no existe: C:/x/ext4.vhdx', @()),
    @(16, 'No se pudo leer la versión del appliance instalado; no se reemplazará.', @()),
    @(1, 'Algo inesperado del fixture.', @())
)
foreach ($case in $cases) {
    $failure = Get-InstallFailure -Message $case[1] -NativeOutput $case[2]
    Assert ($failure.Code -eq $case[0]) "expected $($case[0]) got $($failure.Code) for $($case[1])"
    Assert ([bool]$failure.Cause -and [bool]$failure.Action) "missing hint for $($case[0])"
}
'classified'
"""
        )
        self.assertIn("classified", output)

    def test_only_a_registration_whose_disk_is_missing_is_orphaned(self):
        output = self.run_ps(
            r"""
$present = Join-Path $env:CAN_FIXTURE 'present'
$custom = Join-Path $env:CAN_FIXTURE 'custom'
$missing = Join-Path $env:CAN_FIXTURE 'missing'
New-Item -ItemType Directory -Force $present, $custom | Out-Null
Set-Content (Join-Path $present 'ext4.vhdx') 'fixture'
Set-Content (Join-Path $custom 'disk.vhdx') 'fixture'
$entries = @(
    [pscustomobject]@{ DistributionName = 'Other'; BasePath = $missing },
    [pscustomobject]@{ DistributionName = 'Present'; BasePath = "\\?\$present" },
    [pscustomobject]@{ DistributionName = 'Custom'; BasePath = $custom; VhdFileName = 'disk.vhdx' },
    [pscustomobject]@{ DistributionName = 'Orphan'; BasePath = "\\?\$missing" }
)
function Find($name, $regs) {
    Find-OrphanedDistributionDisk -Distribution $name -Registrations $regs
}
Assert ((Find 'Present' $entries) -eq '') 'present disk'
Assert ((Find 'Custom' $entries) -eq '') 'custom disk'
Assert ((Find 'Unknown' $entries) -eq '') 'unregistered'
Assert ((Find 'Unknown' @()) -eq '') 'no registry'
$orphan = Find-OrphanedDistributionDisk -Distribution 'Orphan' -Registrations $entries
Assert ($orphan -eq (Join-Path $missing 'ext4.vhdx')) "orphan: $orphan"
'orphan-checked'
"""
        )
        self.assertIn("orphan-checked", output)

    def test_orphan_removal_needs_the_confirmed_switch_and_is_rechecked(self):
        script = (ROOT / "install-appliance.ps1").read_text(encoding="utf-8-sig")
        setup = (ROOT.parents[1] / "installer" / "WhatsApp-CAN.iss").read_text(encoding="utf-8")
        guard = script.index("if (-not $RemoveOrphanedDistribution) {")
        unregister = script.index('Invoke-Native wsl.exe @("--unregister", $DistroName)')
        self.assertLess(guard, unregister)
        self.assertIn("(ResultCode = 19) and ConfirmOrphanedBridgeRemoval", setup)
        self.assertIn("Result := (not WizardSilent) and (TaskDialogMsgBox(", setup)
        self.assertIn("RunBridgeInstall(' -RemoveOrphanedDistribution', ResultCode)", setup)

    def test_installer_hints_cover_every_script_failure_code(self):
        script = (ROOT / "install-appliance.ps1").read_text(encoding="utf-8-sig")
        setup = (ROOT.parents[1] / "installer" / "WhatsApp-CAN.iss").read_text(encoding="utf-8")
        codes = set(re.findall(r"@\{ Code = (\d+);", script))
        self.assertGreaterEqual(len(codes), 9)
        for code in codes:
            with self.subTest(code=code):
                self.assertRegex(setup, rf"(?m)^\s+{code}: Result :=")
        self.assertIn("'Copiar registro', 'Abrir registro'", setup)
        self.assertIn("Set-Clipboard", setup)
        self.assertIn("if WizardSilent or not FileExists(BridgeInstallLogPath)", setup)

    def test_native_output_is_logged_without_credentials(self):
        output = self.run_ps(
            r"""
Write-NativeLine 'wsl: fictitious failure 0x80370102'
Write-NativeLine '{"jid":"x@example.invalid","password":"fixture-secret"}'
Write-NativeLine ''
"""
        )
        self.assertIn("fictitious failure 0x80370102", output)
        self.assertNotIn("fixture-secret", output)
        self.assertIn("nea omitida del registro", output)

    def test_version_detection_rejects_unknown_and_requires_voice_capability(self):
        self.run_ps(
            (Path(__file__).parent / "fixtures/appliance/version-capabilities.ps1").read_text(
                encoding="utf-8-sig"
            )
        )

    def test_parallel_installer_is_rejected_and_lock_can_be_reused(self):
        fixture = Path(__file__).parent / "fixtures/appliance/concurrent-migration.ps1"
        self.run_ps(fixture.read_text(encoding="utf-8-sig"))

    def test_public_updater_handles_legacy_modern_current_and_downgrade(self):
        self.run_ps(
            (Path(__file__).parent / "fixtures/appliance/updater-versions.ps1").read_text(
                encoding="utf-8-sig"
            ),
            source="actualizar-puente-local.ps1",
        )

    def test_public_updater_runs_only_verified_installer_and_forwards_target(self):
        fixture = Path(__file__).parent / "fixtures/appliance/updater-flow.ps1"
        self.run_ps(fixture.read_text(encoding="utf-8-sig"), source="actualizar-puente-local.ps1")

    def test_migration_preserves_active_image_and_retains_failed_candidate(self):
        # Run the real PowerShell migration logic with a deterministic WSL boundary.
        self.run_ps(
            (Path(__file__).parent / "fixtures/appliance/migration-rollback.ps1").read_text(
                encoding="utf-8-sig"
            )
        )

    def test_backup_corruption_prevents_any_distribution_replacement(self):
        self.run_ps(
            (Path(__file__).parent / "fixtures/appliance/corrupt-backup.ps1").read_text(
                encoding="utf-8-sig"
            )
        )

    def test_interrupted_migration_recovers_without_discarding_candidate(self):
        fixture = Path(__file__).parent / "fixtures/appliance/interrupted-migration.ps1"
        self.run_ps(fixture.read_text(encoding="utf-8-sig"))

    def test_package_inspection_and_release_preparation_use_real_archive_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "version.json"
            payload.write_text(
                json.dumps(
                    {
                        "appliance_version": "1.2.0",
                        "bridge_image": "ghcr.io/marcomolinaleija/cliente-xmpp-bridge:v19",
                        "bridge_voice": True,
                        "voice_required": True,
                        "voice_configuration": 2,
                    }
                ),
                encoding="utf-8",
            )
            package = root / "Fixture.wsl"
            with tarfile.open(package, "w:gz") as archive:
                archive.add(payload, arcname="./opt/whatsapp-can-bridge/version.json")
            manifest = root / "candidate.json"
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "prepare-appliance-release.ps1"),
                    "-PackagePath",
                    str(package),
                    "-OutputManifestPath",
                    str(manifest),
                ],
                env=powershell_environment(),
                capture_output=True,
                timeout=40,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(data["sha256"], hashlib.sha256(package.read_bytes()).hexdigest())
            self.assertEqual(data["size_bytes"], package.stat().st_size)
            installer = root / "install-appliance.ps1"
            self.assertEqual(
                data["installer_sha256"], hashlib.sha256(installer.read_bytes()).hexdigest()
            )
            self.assertIn("/wsl-appliance-v1.2.0/", data["installer_url"])
            self.assertTrue(installer.read_bytes().startswith(b"\xef\xbb\xbf"))
            for field in ("bridge_voice", "voice_required"):
                with self.subTest(missing_capability=field):
                    invalid_root = root / field
                    invalid_root.mkdir()
                    metadata = json.loads(payload.read_text())
                    metadata.pop(field)
                    invalid_payload = invalid_root / "version.json"
                    invalid_payload.write_text(json.dumps(metadata))
                    invalid_package = invalid_root / "Invalid.wsl"
                    with tarfile.open(invalid_package, "w:gz") as archive:
                        archive.add(invalid_payload, arcname="opt/whatsapp-can-bridge/version.json")
                    rejected = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                         "Bypass", "-File", str(ROOT / "prepare-appliance-release.ps1"),
                         "-PackagePath", str(invalid_package), "-OutputManifestPath",
                         str(invalid_root / "candidate.json")],
                        env=powershell_environment(), capture_output=True, timeout=40,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("motor de voz", rejected.stderr.decode(errors="replace"))
                    self.assertFalse((invalid_root / "candidate.json").exists())
                    self.assertFalse((invalid_root / "install-appliance.ps1").exists())


class BridgeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "appliance_bridge_bundle",
            ROOT / "prepare_bridge_bundle.py",
        )
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def archive(self, path, *, architecture="amd64", count=1, tag=None, corrupt=False):
        image = self.tool.REPOSITORY + ":v36"
        config = json.dumps({"os": "linux", "architecture": architecture}).encode()
        config_id = "sha256:" + hashlib.sha256(config).hexdigest()
        manifest = json.dumps(
            {"schemaVersion": 2, "config": {"digest": config_id}, "layers": []}
        ).encode()
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        index = json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "digest": digest,
                        "annotations": {"org.opencontainers.image.ref.name": tag or image},
                    }
                ]
                * count,
            }
        ).encode()
        with tarfile.open(path, "w") as archive:
            for name, body in {
                "index.json": index,
                "blobs/sha256/" + digest[7:]: manifest,
                "blobs/sha256/" + config_id[7:]: config + (b" " if corrupt else b""),
            }.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
        return image, digest, config_id

    def test_bundle_pins_actual_oci_identity_and_never_overwrites_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "input.oci"
            image, digest, config = self.archive(archive)
            output = root / "bundle"
            metadata = self.tool.prepare_bundle(archive, image, output, voice=True)
            self.assertEqual(metadata["digest"], digest)
            self.assertEqual(metadata["image_id"], config)
            self.assertTrue(metadata["voice"])
            self.assertEqual((output / metadata["archive"]).read_bytes(), archive.read_bytes())
            self.assertEqual((output / "slidge-v36.image-id").read_text().strip(), config)
            before = (output / "bundled-image.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.tool.prepare_bundle(archive, image, output, voice=True)
            self.assertEqual((output / "bundled-image.json").read_bytes(), before)

    def test_bundle_rejects_ambiguous_foreign_wrong_architecture_and_corrupt_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "input.oci"
            for options in (
                {"count": 2},
                {"tag": "unexpected:v36"},
                {"architecture": "arm64"},
                {"corrupt": True},
            ):
                with self.subTest(options=options):
                    image, _, _ = self.archive(archive, **options)
                    with self.assertRaises(ValueError):
                        self.tool.bundle_identity(archive, image)
            with self.assertRaises(ValueError):
                self.tool.bundle_identity(archive, "ghcr.io/example/foreign:v36")

    def test_bundle_does_not_follow_metadata_links(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "input.oci"
            with tarfile.open(archive, "w") as output:
                member = tarfile.TarInfo("index.json")
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/fixture-private-file"
                output.addfile(member)
            with self.assertRaisesRegex(ValueError, "metadata"):
                self.tool.bundle_identity(archive, self.tool.REPOSITORY + ":v36")


if __name__ == "__main__":
    unittest.main()
