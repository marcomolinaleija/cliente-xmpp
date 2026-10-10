from __future__ import annotations

import importlib
import io
import json
import sys
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image

from tools import bridge_sticker_pack
from tools.smoke_bridge_native_sticker_pack_runtime import fixture

with patch.dict(sys.modules, {"tools.sticker_pack": bridge_sticker_pack}):
    helper = importlib.import_module("tools.bridge_outgoing_sticker_pack")


class OutgoingNativePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Immutable archive bytes are safe to reuse; each mutation reparses a fresh dict.
        cls.fixture_bytes = fixture()

    def mutate(self, *, modify_metadata=lambda value: None, replace_files=None):
        with zipfile.ZipFile(io.BytesIO(self.fixture_bytes)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        metadata = json.loads(files["manifest.json"])
        modify_metadata(metadata)
        files["manifest.json"] = json.dumps(metadata).encode()
        files.update(replace_files or {})
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return output.getvalue()

    def test_native_envelope_preserves_all_sticker_bytes_labels_and_animation(self):
        raw = self.fixture_bytes
        result = helper.prepare_outgoing_pack_sync(raw)
        with (
            zipfile.ZipFile(io.BytesIO(raw)) as source,
            zipfile.ZipFile(io.BytesIO(result)) as archive,
        ):
            metadata = json.loads(archive.read("manifest.json"))
            self.assertEqual(metadata["format"], "can-native-sticker-pack")
            self.assertEqual(metadata["name"], "Fixture")
            self.assertEqual(metadata["author"], "CAN")
            for index, entry in enumerate(metadata["stickers"]):
                self.assertEqual(archive.read(entry["file"]), source.read(entry["file"]))
                self.assertEqual(entry["animated"], bool(index))
                self.assertTrue(entry["description"])
            for filename, size, image_format in (
                ("tray.webp", (96, 96), "WEBP"),
                ("thumbnail.jpg", (252, 252), "JPEG"),
            ):
                with Image.open(io.BytesIO(archive.read(filename))) as image:
                    self.assertEqual(image.size, size)
                    self.assertEqual(image.format, image_format)

    def test_invalid_metadata_and_limit_fail_without_partial_native_output(self):
        for mutation in (
            lambda m: m.update(version=2),
            lambda m: m.update(author=""),
            lambda m: m.update(stickers=m["stickers"] * 31),
            lambda m: m["stickers"][0].update(description="x" * 8001),
            lambda m: m["stickers"][0].update(file="missing.webp"),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                helper.prepare_outgoing_pack_sync(self.mutate(modify_metadata=mutation))

    def test_extra_or_incompatible_assets_are_rejected_not_converted(self):
        small = io.BytesIO()
        Image.new("RGBA", (32, 32), "red").save(small, format="WEBP", lossless=True)
        for replacements in (
            {"extra.webp": b"x"},
            {"000.webp": small.getvalue()},
            {"000.webp": b"not WebP"},
        ):
            with self.subTest(files=list(replacements)), self.assertRaises((ValueError, OSError)):
                helper.prepare_outgoing_pack_sync(self.mutate(replace_files=replacements))
