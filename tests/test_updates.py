from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import update
from cliente_xmpp.updates import (
    UpdateCheckError,
    UpdateInfo,
    _offer_update,
    comparable_version,
    is_newer_version,
    release_from_payload,
    should_check_at_startup,
)


def release_payload(version: str = "1.1.0") -> dict[str, object]:
    zip_name = f"WhatsApp-CAN-{version}.zip"
    return {
        "tag_name": f"v{version}",
        "name": f"WhatsApp CAN {version}",
        "body": "Cambios visibles para el usuario.",
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/example/project/releases/tag/v{version}",
        "assets": [
            {
                "name": zip_name,
                "browser_download_url": (
                    f"https://github.com/example/project/releases/download/v{version}/{zip_name}"
                ),
            },
            {
                "name": f"{zip_name}.sha256",
                "browser_download_url": (
                    "https://github.com/example/project/releases/download/"
                    f"v{version}/{zip_name}.sha256"
                ),
            },
        ],
    }


class UpdateCheckTests(unittest.TestCase):
    def test_declining_update_keeps_the_app_open_and_does_not_launch_updater(self) -> None:
        parent = MagicMock()
        parent.IsBeingDeleted.return_value = False
        update_info = UpdateInfo(
            version="1.1.0",
            tag="v1.1.0",
            notes="",
            download_url="https://example.test/WhatsApp-CAN-1.1.0.zip",
            checksum_url="https://example.test/WhatsApp-CAN-1.1.0.zip.sha256",
            release_url="https://example.test/releases/v1.1.0",
        )
        with (
            patch("cliente_xmpp.updates._show_update_dialog", return_value=False),
            patch("cliente_xmpp.updates._launch_updater") as launch_updater,
        ):
            _offer_update(parent, update_info)

        launch_updater.assert_not_called()
        parent.Close.assert_not_called()

    def test_version_comparison_accepts_v_and_stable_suffix(self) -> None:
        self.assertEqual(comparable_version("v1.2.3-stable"), (1, 2, 3, 0))
        self.assertTrue(is_newer_version("1.2.4", "1.2.3"))
        self.assertFalse(is_newer_version("1.2.3", "1.2.3"))
        self.assertFalse(is_newer_version("sin-version", "1.2.3"))

    def test_release_requires_matching_zip_and_checksum(self) -> None:
        info = release_from_payload(release_payload(), "1.0.0")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.version, "1.1.0")
        self.assertTrue(info.download_url.endswith("WhatsApp-CAN-1.1.0.zip"))
        self.assertTrue(info.checksum_url.endswith("WhatsApp-CAN-1.1.0.zip.sha256"))

    def test_current_prerelease_or_incomplete_release_is_not_offered(self) -> None:
        self.assertIsNone(release_from_payload(release_payload("1.0.0"), "1.0.0"))
        prerelease = release_payload()
        prerelease["prerelease"] = True
        self.assertIsNone(release_from_payload(prerelease, "1.0.0"))
        missing_checksum = release_payload()
        missing_checksum["assets"] = missing_checksum["assets"][:1]  # type: ignore[index]
        with self.assertRaises(UpdateCheckError):
            release_from_payload(missing_checksum, "1.0.0")

    def test_source_checkout_does_not_check_without_explicit_override(self) -> None:
        with (
            patch("cliente_xmpp.updates.sys.platform", "win32"),
            patch("cliente_xmpp.updates.sys.frozen", False, create=True),
            patch.dict("cliente_xmpp.updates.os.environ", {}, clear=True),
        ):
            self.assertFalse(should_check_at_startup())


class UpdateExecutableTests(unittest.TestCase):
    def test_safe_extract_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "bad")
            destination = root / "extracted"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "insegura"):
                update.safe_extract(archive, destination)
            self.assertFalse((root / "outside.txt").exists())

    def test_safe_extract_rejects_alternate_stream_and_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "extracted"
            destination.mkdir()

            alternate_stream = root / "alternate-stream.zip"
            with zipfile.ZipFile(alternate_stream, "w") as output:
                output.writestr("file.txt:secret", "bad")
            with self.assertRaisesRegex(RuntimeError, "insegura"):
                update.safe_extract(alternate_stream, destination)

            duplicate = root / "duplicate.zip"
            first = zipfile.ZipInfo("same.txt")
            second = zipfile.ZipInfo("SAME.TXT")
            with zipfile.ZipFile(duplicate, "w") as output:
                output.writestr(first, "one")
                output.writestr(second, "two")
            with self.assertRaisesRegex(RuntimeError, "repite"):
                update.safe_extract(duplicate, destination)

    def test_safe_extract_rejects_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "bad-link.zip"
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(link, "target")
            destination = root / "extracted"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "enlace"):
                update.safe_extract(archive, destination)

    def test_safe_extract_accepts_windows_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "windows.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("WhatsApp-CAN\\_internal\\", "")
                output.writestr("WhatsApp-CAN\\_internal\\library.zip", "content")
            destination = root / "extracted"
            destination.mkdir()
            update.safe_extract(archive, destination)
            self.assertEqual(
                (destination / "WhatsApp-CAN" / "_internal" / "library.zip").read_text(),
                "content",
            )

    def test_payload_root_and_directory_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extracted = root / "extracted"
            payload = extracted / "WhatsApp-CAN"
            (payload / "_internal").mkdir(parents=True)
            (payload / "WhatsApp-CAN.exe").write_bytes(b"new-app")
            (payload / "update.exe").write_bytes(b"new-updater")
            self.assertEqual(update.payload_root(extracted, "WhatsApp-CAN.exe"), payload)

            installation = root / "installed" / "WhatsApp-CAN"
            installation.mkdir(parents=True)
            (installation / "old.txt").write_text("old", encoding="utf-8")
            update.replace_installation(payload, installation)
            self.assertEqual((installation / "WhatsApp-CAN.exe").read_bytes(), b"new-app")
            self.assertFalse((installation / "old.txt").exists())


if __name__ == "__main__":
    unittest.main()
