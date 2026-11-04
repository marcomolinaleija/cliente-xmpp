from __future__ import annotations

import io
import json
import stat
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from cliente_xmpp.integrations import rayoai
from cliente_xmpp.media.sticker_descriptions import describe_pack
from cliente_xmpp.media.sticker_packs import import_pack
from cliente_xmpp.storage.sticker_library import StickerLibrary


class StickerImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = StickerLibrary(self.root / "library")

    def bitmap(self, color, size=(512, 512), kind="WEBP"):
        data = io.BytesIO()
        Image.new("RGBA", size, color).save(data, format=kind)
        return data.getvalue()

    def pack(self, *, prefix="", cover="099.png", overrides=None):
        files = {"title.txt": b"Fixture", "author.txt": b"CAN"}
        files.update({f"{100 + i}.webp": self.bitmap(color)
                      for i, color in enumerate(("red", "blue", "green"))})
        files[cover] = self.bitmap("yellow", (96, 96), "PNG")
        files.update(overrides or {})
        path = self.root / "fixture.wastickers"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(prefix + name, data)
        return path

    def test_mobile_flat_prefix_and_cover_variants_keep_only_stickers_and_are_idempotent(self):
        for prefix, cover in (("", "cover.png"), ("/", "099.png"), ("", "099.png")):
            with self.subTest(prefix=prefix, cover=cover):
                source = self.pack(prefix=prefix, cover=cover)
                original = source.read_bytes()
                pack_id = import_pack(self.library, source)
                entries = self.library.list_stickers(pack_id=pack_id)
                self.assertEqual(len(entries), 3)
                self.assertEqual({e.name for e in entries}, {"Sticker 1", "Sticker 2", "Sticker 3"})
                # The reused pack need not be the highest membership of its first sticker.
                other = self.library.create_pack("Other")
                self.library.assign_pack(entries[0].id, other)
                self.assertEqual(import_pack(self.library, source), pack_id)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(len(list(self.library.files.glob("*.webp"))), 3)
        self.assertEqual(len([p for p in self.library.packs() if p.name == "Fixture"]), 1)

    def test_unsafe_paths_normalized_collisions_and_links_never_import(self):
        for name in ("../evil.webp", "/../evil.webp", "//evil.webp", "/dir/file.webp",
                     "dir/file.webp", "x\\y.webp", "C:evil.webp", "/100.webp", "100.WEBP"):
            with self.subTest(name=name):
                source = self.pack(overrides={name: self.bitmap("black")})
                with self.assertRaises(ValueError):
                    import_pack(self.library, source)
                self.assertEqual(self.library.packs(), [])
        source = self.pack()
        with zipfile.ZipFile(source, "a") as archive:
            link = zipfile.ZipInfo("link.webp")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "100.webp")
        with self.assertRaises(ValueError):
            import_pack(self.library, source)

    def test_can_paths_remain_strict_and_ambiguous_mobile_cover_is_rejected(self):
        source = self.pack(prefix="/")
        can = source.with_suffix(".canstickers")
        can.write_bytes(source.read_bytes())
        with self.assertRaises(ValueError):
            import_pack(self.library, can)
        source = self.pack(overrides={"098.png": self.bitmap("black", (96, 96), "PNG")})
        with self.assertRaisesRegex(ValueError, "varias posibles portadas"):
            import_pack(self.library, source)
        self.assertEqual(self.library.packs(), [])

    def test_same_title_with_different_members_is_not_destructively_merged(self):
        first = import_pack(self.library, self.pack())
        second = import_pack(self.library, self.pack(overrides={"100.webp": self.bitmap("black")}))
        self.assertNotEqual(first, second)
        self.assertEqual([p.count for p in self.library.packs()], [3, 3])

    def test_concurrent_imports_reuse_the_same_pack_and_keep_owned_files(self):
        source = self.pack()
        self.library.packs()  # Initialize the isolated schema before concurrent requests.
        ready = threading.Barrier(2)

        def receive(_index):
            ready.wait(timeout=5)
            return import_pack(self.library, source)

        with ThreadPoolExecutor(max_workers=2) as workers:
            identifiers = list(workers.map(receive, range(2)))
        self.assertEqual(identifiers[0], identifiers[1])
        self.assertEqual(len(self.library.packs()), 1)
        self.assertEqual(len(list(self.library.files.glob("*.webp"))), 3)

    def test_batch_labels_are_serial_and_preserve_existing_metadata(self):
        pack_id = import_pack(self.library, self.pack())
        first, second, third = self.library.list_stickers(pack_id=pack_id)
        self.library.edit(first.id, name="Manual", description="Texto recibido.", favorite=True)
        self.library.edit(second.id, description="Un gato sonríe. Fondo azul.")
        self.library.edit(third.id, name="Nombre manual")
        describe = Mock(return_value=("Nombre automático", "Una figura saluda."))
        progress = Mock()
        result = describe_pack(self.library, pack_id, describe, progress=progress)
        self.assertEqual((result.updated, result.failed, result.cancelled), (2, 0, False))
        describe.assert_called_once_with(Path(third.path))
        self.assertEqual(self.library.get(first.id).description, "Texto recibido.")
        self.assertTrue(self.library.get(first.id).favorite)
        self.assertEqual(self.library.get(second.id).name, "Un gato sonríe")
        self.assertEqual(self.library.get(second.id).description, "Un gato sonríe. Fondo azul.")
        self.assertEqual(self.library.get(third.id).name, "Nombre manual")
        self.assertEqual(progress.call_count, 3)

    def test_batch_failure_stale_edit_and_cancellation_keep_imported_assets(self):
        pack_id = import_pack(self.library, self.pack())
        first, second, third = self.library.list_stickers(pack_id=pack_id)

        def generate(path):
            if str(path) == first.path:
                raise OSError("provider unavailable")
            if str(path) == second.path:
                self.library.edit(second.id, description="Manual concurrente")
            return "Nuevo", "Descripción nueva"

        result = describe_pack(self.library, pack_id, generate)
        self.assertEqual((result.updated, result.failed), (1, 2))
        self.assertEqual(self.library.get(second.id).description, "Manual concurrente")
        self.assertEqual(self.library.get(third.id).description, "Descripción nueva")
        stop = threading.Event()

        def cancel_request(path):
            stop.set()
            return "Tardío", "No guardar"

        result = describe_pack(self.library, pack_id, cancel_request, cancelled=stop.is_set)
        self.assertTrue(result.cancelled)
        self.assertEqual(self.library.get(first.id).description, "")
        self.assertTrue(all(
            Path(e.path).exists() for e in self.library.list_stickers(pack_id=pack_id)
        ))

    def test_rayoai_label_response_json_plain_text_and_invalid_types(self):
        for raw, expected in (
            (json.dumps({"name": "Saludo", "description": "Una mano saluda."}),
             ("Saludo", "Una mano saluda.")),
            ("Una mano saluda.", ("", "Una mano saluda.")),
            ('{"name": "Respuesta incompleta"', None),
            ('{"name": 4, "description": "Texto"}', None),
            (None, None),
        ):
            with self.subTest(response=raw), patch.object(
                rayoai, "request_description", return_value=raw
            ) as request:
                self.assertEqual(rayoai.request_sticker_labels("fixture.webp"), expected)
                request.assert_called_once_with(
                    "fixture.webp", instruction=rayoai.STICKER_LABEL_INSTRUCTION
                )
