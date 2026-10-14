from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.ci import prepare_windows_runtime as runtime
from tools.ci import release_windows as release


class TaggedReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "cliente_xmpp").mkdir()
        (self.root / "pyproject.toml").write_text('[project]\nversion="1.2.4"\n')
        (self.root / "cliente_xmpp/__init__.py").write_text('__version__="1.2.4"\n')
        (self.root / "windows_version_info.txt").write_text(
            "StringStruct(u'FileVersion', u'1.2.4')\n"
            "StringStruct(u'ProductVersion', u'1.2.4')\n"
            "filevers=(1, 2, 4, 0)\nprodvers=(1, 2, 4, 0)\n"
        )
        (self.root / "release_notes.txt").write_text("WhatsApp CAN 1.2.4\n\nFixture notes\n")

    def artifacts(self, *, installer=False):
        folder = self.root / "release"
        folder.mkdir(exist_ok=True)
        archive = folder / "WhatsApp-CAN-1.2.4.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("WhatsApp-CAN/WhatsApp-CAN.exe", b"MZ-fixture-main")
            output.writestr("WhatsApp-CAN/update.exe", b"MZ-fixture-updater")
            output.writestr("WhatsApp-CAN/_internal/fixture.dat", b"fixture-resource")
            for name in runtime.REQUIRED_FILES:
                output.writestr("WhatsApp-CAN/_internal/cliente_xmpp/lib/" + name,
                                b"MZ-fixture-library")
            output.writestr(
                "WhatsApp-CAN/_internal/imageio_ffmpeg/binaries/ffmpeg-fixture.exe",
                b"MZ-fixture-ffmpeg",
            )
        names = [archive]
        if installer:
            setup = folder / "WhatsApp-CAN-1.2.4-Setup.exe"
            setup.write_bytes(b"MZ-fixture-setup")
            names.append(setup)
        for binary in names:
            binary.with_name(binary.name + ".sha256").write_text(
                f"{release.sha256(binary)}  {binary.name}\n"
            )

    def test_tag_version_and_all_metadata_must_agree(self):
        self.assertEqual(release.source_version("v1.2.4", self.root), "1.2.4")
        for tag in ("v33", "v1.2.4-rc1", "v01.2.4", "v1.2.4;echo", "v1.2.5"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.source_version(tag, self.root)
        for filename in ("cliente_xmpp/__init__.py", "windows_version_info.txt",
                         "release_notes.txt"):
            with self.subTest(filename=filename):
                path = self.root / filename
                previous = path.read_text()
                path.write_text(previous.replace("1.2.4", "1.2.3").replace("1, 2, 4", "1, 2, 3"))
                with self.assertRaises(ValueError):
                    release.source_version("v1.2.4", self.root)
                path.write_text(previous)

    def test_lightweight_and_annotated_tag_resolve_to_the_exact_commit(self):
        commit, obj = "a" * 40, "b" * 40
        for text in (f"{commit}\trefs/tags/v1.2.4", f"{obj}\trefs/tags/v1.2.4\n"
                     f"{commit}\trefs/tags/v1.2.4^{{}}"):
            with self.subTest(text=text):
                self.assertEqual(release.remote_tag_commit(text, "v1.2.4"), commit)
        with self.assertRaises(ValueError):
            release.remote_tag_commit("", "v1.2.4")

    def test_existing_draft_stable_release_and_downgrade_are_rejected(self):
        release.check_releases("v1.2.4", [
            {"tag_name": "v1.2.3", "draft": False, "prerelease": False},
            {"tag_name": "wsl-appliance-v99", "draft": False, "prerelease": False},
        ])
        for tag, draft in (("v1.2.4", False), ("v1.2.4", True), ("v1.2.5", False)):
            with self.subTest(tag=tag, draft=draft), self.assertRaises(ValueError):
                release.check_releases("v1.2.4", [
                    {"tag_name": tag, "draft": draft, "prerelease": False},
                ])

    def test_clean_tree_exact_built_commit_and_unchanged_remote_are_required(self):
        commit = "a" * 40
        for failure in (None, "dirty", "origin", "built", "local_tag", "remote"):
            with self.subTest(failure=failure):
                def command(args, _root, failure=failure):
                    if args[:2] == ["git", "status"]:
                        return " M fixture.py" if failure == "dirty" else ""
                    if args[:2] == ["git", "remote"]:
                        return "https://example.test/fixture.git" if failure == "origin" else (
                            f"https://github.com/{release.REPOSITORY}.git"
                        )
                    if args[:2] == ["git", "rev-parse"]:
                        return "b" * 40 if failure == "local_tag" and args[2] != "HEAD" else commit
                    if args[:2] == ["git", "ls-remote"]:
                        actual = "b" * 40 if failure == "remote" else commit
                        return f"{actual}\trefs/tags/v1.2.4"
                    return "[[]]"
                with patch.object(release, "command", side_effect=command), \
                        patch.object(release.subprocess, "run") as ancestor:
                    expected = "b" * 40 if failure == "built" else commit
                    if failure:
                        with self.assertRaises(ValueError):
                            release.preflight("v1.2.4", self.root, expected)
                    else:
                        self.assertEqual(release.preflight("v1.2.4", self.root, expected),
                                         ("1.2.4", commit))
                        self.assertIn("--is-ancestor", ancestor.call_args.args[0])

    def test_artifact_names_checksums_updater_and_installer_are_verified(self):
        self.artifacts(installer=True)
        self.assertEqual(len(release.verify_artifacts(self.root, "1.2.4", "zip")), 2)
        self.assertEqual(len(release.verify_artifacts(self.root, "1.2.4", "installer")), 4)
        checksum = self.root / "release/WhatsApp-CAN-1.2.4.zip.sha256"
        checksum.write_text(checksum.read_text().replace("WhatsApp-CAN-1.2.4.zip", "wrong.zip"))
        with self.assertRaises(ValueError):
            release.verify_artifacts(self.root, "1.2.4", "zip")
        self.artifacts()
        archive = self.root / "release/WhatsApp-CAN-1.2.4.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("WhatsApp-CAN/WhatsApp-CAN.exe", b"MZ-fixture-main")
        checksum.write_text(f"{release.sha256(archive)}  {archive.name}\n")
        with self.assertRaises(RuntimeError):
            release.verify_artifacts(self.root, "1.2.4", "zip")

    def test_missing_or_empty_native_runtime_prevents_release_creation(self):
        required = [
            "WhatsApp-CAN/_internal/cliente_xmpp/lib/libmpv-2.dll",
            "WhatsApp-CAN/_internal/cliente_xmpp/lib/nvdaControllerClient64.dll",
            "WhatsApp-CAN/_internal/imageio_ffmpeg/binaries/ffmpeg-fixture.exe",
        ]
        for missing in required:
            for empty in (False, True):
                with self.subTest(missing=missing, empty=empty):
                    self.artifacts()
                    archive = self.root / "release/WhatsApp-CAN-1.2.4.zip"
                    with zipfile.ZipFile(archive) as source:
                        entries = {name: source.read(name) for name in source.namelist()}
                    if empty:
                        entries[missing] = b""
                    else:
                        del entries[missing]
                    with zipfile.ZipFile(archive, "w") as output:
                        for name, content in entries.items():
                            output.writestr(name, content)
                    archive.with_name(archive.name + ".sha256").write_text(
                        f"{release.sha256(archive)}  {archive.name}\n"
                    )
                    with patch.object(release, "preflight",
                                      return_value=("1.2.4", "a" * 40)), \
                            patch.object(release, "command") as command:
                        with self.assertRaisesRegex(RuntimeError, "paquete final no contiene"):
                            release.publish("v1.2.4", self.root, "zip", "a" * 40)
                        command.assert_not_called()

    def test_draft_only_publishes_after_every_downloaded_asset_matches(self):
        self.artifacts()
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                calls = []
                def command(args, _root, corrupt=corrupt, calls=calls):
                    calls.append(args)
                    if args[1:3] == ["release", "download"]:
                        folder = Path(args[args.index("--dir") + 1])
                        for asset in (self.root / "release").iterdir():
                            shutil.copyfile(asset, folder / asset.name)
                        if corrupt:
                            (folder / "WhatsApp-CAN-1.2.4.zip").write_bytes(b"corrupt fixture")
                    return json.dumps({"isDraft": False, "isPrerelease": False,
                                       "url": "https://github.com/example/fixture/releases/v1.2.4"})
                with patch.object(release, "preflight", return_value=("1.2.4", "a" * 40)), \
                        patch.object(release, "command", side_effect=command):
                    if corrupt:
                        with self.assertRaises(ValueError):
                            release.publish("v1.2.4", self.root, "zip", "a" * 40)
                    else:
                        self.assertIn("v1.2.4", release.publish(
                            "v1.2.4", self.root, "zip", "a" * 40))
                create = next(args for args in calls if args[1:3] == ["release", "create"])
                self.assertIn("--verify-tag", create)
                self.assertIn("--draft", create)
                edited = any(args[1:3] == ["release", "edit"] for args in calls)
                self.assertEqual(edited, not corrupt)
                self.assertFalse(any(args[0] == "git" for args in calls))


class ReleaseProgressNativeTests(unittest.TestCase):
    def test_progress_preserves_native_exit_code_even_if_notification_fails(self):
        build_script = (release.ROOT / "build_release.ps1").as_posix().replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile(
    '{build_script}', [ref]$tokens, [ref]$errors
)
if ($errors.Count -gt 0) {{ throw 'Invalid build script' }}
$function = $tree.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Send-ReleaseProgress'
}}, $true)
Invoke-Expression $function.Extent.Text
$env:TELEGRAM_PROGRESS_ENABLED = '1'
$script:Calls = 0
function python {{
    $script:Calls++
    $global:LASTEXITCODE = 1
    if ($script:ThrowPython) {{ throw 'Fixture notification failure' }}
}}
foreach ($throwPython in @($false, $true)) {{
    $script:ThrowPython = $throwPython
    $global:LASTEXITCODE = 37
    Send-ReleaseProgress -Phase 'client'
    if ($global:LASTEXITCODE -ne 37) {{ throw 'Notification changed the build exit code' }}
}}
if ($script:Calls -ne 2) {{ throw 'Notification did not run twice' }}
"""
        process = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))


class WindowsRuntimeTests(unittest.TestCase):
    def test_runtime_is_hash_pinned_and_only_managed_dlls_are_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "fixture.zip"
            data = {name: b"MZ-fixture-" + name.encode() for name in runtime.REQUIRED_FILES}
            with zipfile.ZipFile(archive, "w") as output:
                for name, content in data.items():
                    output.writestr("fixture/" + name, content)
                output.writestr("../outside.txt", b"must not extract")
            manifest = {"sha256": runtime.sha256(archive), "files": {
                name: {"member": "fixture/" + name, "sha256": hashlib.sha256(content).hexdigest()}
                for name, content in data.items()
            }}
            bad = {**manifest, "sha256": "0" * 64}
            with self.assertRaises(ValueError):
                runtime.install_runtime(archive, bad, root)
            self.assertFalse((root / "cliente_xmpp/lib").exists())
            runtime.install_runtime(archive, manifest, root)
            runtime.install_runtime(archive, manifest, root)
            for name, content in data.items():
                self.assertEqual((root / "cliente_xmpp/lib" / name).read_bytes(), content)
            self.assertFalse((root.parent / "outside.txt").exists())
            destination = root / "cliente_xmpp/lib/libmpv-2.dll"
            destination.write_bytes(b"different existing fixture")
            with self.assertRaises(ValueError):
                runtime.install_runtime(archive, manifest, root)
            self.assertEqual(destination.read_bytes(), b"different existing fixture")

    def test_runtime_keeps_an_existing_partial_file_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for name in runtime.REQUIRED_FILES:
                    output.writestr(name, b"MZ-fixture")
            manifest = {"sha256": runtime.sha256(archive), "files": {
                name: {"member": name, "sha256": hashlib.sha256(b"MZ-fixture").hexdigest()}
                for name in runtime.REQUIRED_FILES
            }}
            target = root / "cliente_xmpp/lib"
            target.mkdir(parents=True)
            partial = target / "libmpv-2.dll.part"
            partial.write_bytes(b"existing partial fixture")
            with self.assertRaises(FileExistsError):
                runtime.install_runtime(archive, manifest, root)
            self.assertEqual(partial.read_bytes(), b"existing partial fixture")
