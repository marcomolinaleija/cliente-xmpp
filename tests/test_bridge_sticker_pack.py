from __future__ import annotations

import io
import json
import stat
import unittest
import zipfile

from PIL import Image

from tools.bridge_sticker_pack import archive_files, normalize_pack_sync


def envelope(*, animated=False, filename="000.png", description="Una figura saluda."):
    image = Image.new("RGBA", (512 if animated else 40, 512 if animated else 40), "red")
    payload = io.BytesIO()
    if animated:
        image.save(
            payload,
            format="WEBP",
            save_all=True,
            append_images=[Image.new("RGBA", (512, 512), "blue")],
            duration=[100, 200],
            lossless=True,
        )
    else:
        image.save(payload, format="PNG")
    metadata = {
        "format": "whatsapp-sticker-pack",
        "version": 1,
        "name": "Fixture",
        "author": "CAN",
        "stickers": [
            {"file": filename, "name": "Fixture", "description": description, "lottie": False}
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(metadata))
        archive.writestr(filename, payload.getvalue())
    return output.getvalue(), payload.getvalue()


class NativePackTests(unittest.TestCase):
    def test_received_pack_matches_can_v1_with_alt_text(self):
        for animated in (False, True):
            with self.subTest(animated=animated):
                raw, original = envelope(
                    animated=animated, filename="000.webp" if animated else "000.png"
                )
                normalized = normalize_pack_sync(raw)
                with zipfile.ZipFile(io.BytesIO(normalized)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(manifest["format"], "can-stickers")
                    self.assertEqual(manifest["version"], 1)
                    self.assertEqual(manifest["name"], "Fixture")
                    self.assertEqual(manifest["author"], "CAN")
                    entry = manifest["stickers"][0]
                    self.assertEqual(entry["description"], "Una figura saluda.")
                    asset = archive.read(entry["file"])
                    if animated:
                        self.assertEqual(asset, original)
                    with Image.open(io.BytesIO(asset)) as image:
                        self.assertEqual(image.size, (512, 512))
                        self.assertEqual(image.n_frames > 1, animated)

    def test_bad_image_is_not_a_partially_imported_pack(self):
        raw, _ = envelope()
        with zipfile.ZipFile(io.BytesIO(raw)) as original:
            metadata = original.read("manifest.json")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", metadata)
            archive.writestr("000.png", b"not an image")
        with self.assertRaises((ValueError, OSError)):
            normalize_pack_sync(output.getvalue())

    def test_unsafe_duplicate_and_oversize_archives_rejected(self):
        for name, link, oversized in (
            ("../escape", False, False),
            ("/absolute", False, False),
            ("safe", True, False),
            ("safe", False, True),
        ):
            with self.subTest(name=name, link=link, oversized=oversized):
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    info = zipfile.ZipInfo(name)
                    if link:
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, b"x" * (6 * 1024 * 1024 if oversized else 1))
                with self.assertRaises(ValueError):
                    archive_files(output.getvalue(), count=201, limit=120 * 1024 * 1024)

    def test_metadata_bounds_and_unknown_version_rejected(self):
        raw, _ = envelope(description="x" * 8001)
        with self.assertRaises(ValueError):
            normalize_pack_sync(raw)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", '{"format":"whatsapp-sticker-pack","version":2}')
        with self.assertRaises(ValueError):
            normalize_pack_sync(output.getvalue())

    def test_case_colliding_files_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("a.webp", b"a")
            archive.writestr("A.webp", b"a")
        with self.assertRaises(ValueError):
            archive_files(output.getvalue(), count=201, limit=1024)


if __name__ == "__main__":
    unittest.main()
