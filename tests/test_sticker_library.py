from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from cliente_xmpp.config.settings import SettingsStore
from cliente_xmpp.media.sticker_creation import can_create_sticker, source_from_message
from cliente_xmpp.media.sticker_packs import export_pack, import_pack
from cliente_xmpp.models.chat import Message
from cliente_xmpp.storage.sticker_library import StickerLibrary


class StickerLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = StickerLibrary(self.root / "library")

    def image(self, name: str = "photo.png", color: str = "red") -> Path:
        path = self.root / name
        Image.new("RGBA", (328, 300), color).save(path)
        return path

    def test_library_owns_copy_and_preserves_original(self) -> None:
        path = self.image()
        original = path.read_bytes()
        entry = self.library.add(path, name="Sonrisa", description="Una cara sonríe.")
        self.assertEqual(path.read_bytes(), original)
        self.assertNotEqual(Path(entry.path), path)
        self.assertEqual(Path(entry.path).parent, self.library.files)
        reopened = StickerLibrary(self.library.root).get(entry.id)
        self.assertEqual(reopened, entry)
        self.library.delete(entry.id)
        self.assertTrue(path.exists())
        self.assertFalse(Path(entry.path).exists())
        self.assertEqual(self.library.list_stickers(), [])

    def test_deduplication_preserves_manual_description_and_favorite(self) -> None:
        path = self.image()
        first = self.library.add(path, description="Descripción manual")
        self.library.edit(first.id, favorite=True)
        second = self.library.add(path, name="Duplicado", description="Descripción automática")
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.description, "Descripción manual")
        self.assertTrue(second.favorite)
        self.assertEqual(len(list(self.library.files.glob("*.webp"))), 1)

    def test_packs_are_multiple_memberships_not_file_copies(self) -> None:
        entry = self.library.add(self.image())
        first = self.library.create_pack("Sonrisas")
        second = self.library.create_pack("Reacciones")
        for pack in (first, first, second):
            self.library.assign_pack(entry.id, pack)
        self.assertEqual(self.library.get(entry.id).pack_ids, (first, second))
        self.library.delete_pack(first)
        self.assertTrue(Path(entry.path).exists())
        self.assertEqual(self.library.get(entry.id).pack_ids, (second,))
        self.library.assign_pack(entry.id, second, remove=True)
        self.assertEqual(self.library.get(entry.id).pack_ids, ())

    def test_search_is_literal_and_unicode_case_insensitive(self) -> None:
        entry = self.library.add(self.image(), name="NIÑO %", description="ÑANDÚ _")
        self.assertEqual(self.library.list_stickers(query="niño")[0].id, entry.id)
        self.assertEqual(self.library.list_stickers(query="ñandú")[0].id, entry.id)
        self.assertEqual(self.library.list_stickers(query="%")[0].id, entry.id)
        self.assertEqual(self.library.list_stickers(query="other"), [])

    def test_stale_rayoai_result_cannot_overwrite_manual_edit(self) -> None:
        entry = self.library.add(self.image())
        self.library.edit(entry.id, description="Manual", expected_revision=entry.revision)
        with self.assertRaises(ValueError):
            self.library.edit(entry.id, description="Late AI", expected_revision=entry.revision)
        self.assertEqual(self.library.get(entry.id).description, "Manual")

    def test_failed_batch_import_rolls_back_records_and_new_files(self) -> None:
        first = self.image()
        invalid = self.root / "invalid.png"
        invalid.write_text("not an image")
        with self.assertRaises(ValueError):
            self.library.add_many(
                [(first, "First", ""), (invalid, "Second", "")], pack_name="Failed import"
            )
        self.assertEqual(self.library.packs(), [])
        self.assertEqual(self.library.list_stickers(), [])
        self.assertEqual(list(self.library.files.iterdir()), [])
        self.assertTrue(first.exists())

    def test_failed_delete_keeps_metadata_and_original(self) -> None:
        original = self.image()
        entry = self.library.add(original)
        with patch.object(Path, "unlink", side_effect=PermissionError("in use")):
            with self.assertRaises(PermissionError):
                self.library.delete(entry.id)
        self.assertEqual(self.library.get(entry.id).id, entry.id)
        self.assertTrue(original.exists())

    def test_tampered_filename_cannot_delete_outside_library(self) -> None:
        original = self.image()
        entry = self.library.add(original)
        with closing(sqlite3.connect(self.library.path)) as conn, conn:
            conn.execute("UPDATE stickers SET filename=? WHERE id=?", ("../photo.png", entry.id))
        with self.assertRaises(ValueError):
            self.library.delete(entry.id)
        self.assertTrue(original.exists())

    def test_invalid_text_and_future_schema_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.library.create_pack("")
        with self.assertRaises(ValueError):
            self.library.add(self.image(), description="Invalid\x01")
        self.library.packs()
        with closing(sqlite3.connect(self.library.path)) as conn, conn:
            conn.execute("PRAGMA user_version=9")
        with self.assertRaises(ValueError):
            self.library.list_stickers()

    def test_filters_pagination_and_pack_rename(self) -> None:
        first = self.library.add(self.image("first.png"))
        second = self.library.add(self.image("second.png", "blue"))
        pack = self.library.create_pack("Before")
        self.library.rename_pack(pack, "After")
        self.library.assign_pack(first.id, pack)
        self.library.edit(second.id, favorite=True)
        self.assertEqual(self.library.packs()[0].name, "After")
        self.assertEqual(self.library.list_stickers(pack_id=pack)[0].id, first.id)
        self.assertEqual(self.library.list_stickers(favorites=True)[0].id, second.id)
        self.assertEqual(len(self.library.list_stickers(limit=1, offset=1)), 1)

    def test_can_pack_round_trip_preserves_names_and_descriptions_not_favorites(self) -> None:
        entry = self.library.add(self.image(), name="Alegría", description="Una cara sonríe.")
        self.library.edit(entry.id, favorite=True)
        pack = self.library.create_pack("Expresiones", "Autor de prueba")
        self.library.assign_pack(entry.id, pack)
        output = export_pack(self.library, pack, self.root / "pack.canstickers")
        receiver = StickerLibrary(self.root / "receiver")
        imported = import_pack(receiver, output)
        received = receiver.list_stickers(pack_id=imported)[0]
        self.assertEqual(received.name, "Alegría")
        self.assertEqual(received.description, entry.description)
        self.assertEqual(Path(received.path).read_bytes(), Path(entry.path).read_bytes())
        self.assertFalse(received.favorite)

    def test_wastickers_exports_flat_zip_and_requires_three_homogeneous_items(self) -> None:
        entry = self.library.add(self.image())
        pack = self.library.create_pack("Test pack")
        self.library.assign_pack(entry.id, pack)
        output = self.root / "pack.wastickers"
        with self.assertRaises(ValueError):
            export_pack(self.library, pack, output)
        self.assertFalse(output.exists())
        for color in ("blue", "green"):
            added = self.library.add(self.image(f"{color}.png", color))
            self.library.assign_pack(added.id, pack)
        export_pack(self.library, pack, output)
        with zipfile.ZipFile(output) as archive:
            self.assertIn("author.txt", archive.namelist())
            self.assertIn("title.txt", archive.namelist())
            self.assertIn("cover.png", archive.namelist())
            self.assertNotIn("manifest.json", archive.namelist())
            self.assertEqual(len([n for n in archive.namelist() if n.endswith(".webp")]), 3)
        receiver = StickerLibrary(self.root / "receiver")
        imported = import_pack(receiver, output)
        self.assertEqual(len(receiver.list_stickers(pack_id=imported)), 3)

    def test_zip_traversal_duplicate_and_invalid_manifest_do_not_import(self) -> None:
        for members in (("../outside.webp",), ("X.webp", "x.webp"), ("manifest.json",)):
            with self.subTest(members=members):
                archive_path = self.root / "bad.canstickers"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for name in members:
                        archive.writestr(name, "{}")
                with self.assertRaises(ValueError):
                    import_pack(self.library, archive_path)
                self.assertEqual(self.library.list_stickers(), [])

    def test_malformed_last_image_does_not_partially_import(self) -> None:
        data = self.image().read_bytes()
        manifest = {
            "format": "can-stickers",
            "version": 1,
            "name": "Bad",
            "author": "CAN",
            "stickers": [
                {"file": name, "name": name, "description": ""} for name in ("valid.png", "bad.png")
            ],
        }
        source = self.root / "broken.canstickers"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("valid.png", data)
            archive.writestr("bad.png", "not an image")
        with self.assertRaises(ValueError):
            import_pack(self.library, source)
        self.assertEqual(self.library.list_stickers(), [])
        self.assertEqual(self.library.packs(), [])
        self.assertEqual(list(self.library.files.iterdir()), [])

    def test_export_cannot_overwrite_internal_library_files(self) -> None:
        pack = self.library.create_pack("Pack")
        entry = self.library.add(self.image())
        self.library.assign_pack(entry.id, pack)
        with self.assertRaises(ValueError):
            export_pack(self.library, pack, self.library.root / "internal.canstickers")

    def test_export_rejects_mixed_types_or_tampered_image_and_preserves_destination(self) -> None:
        pack = self.library.create_pack("Fixture pack")
        entries = []
        for color in ("red", "blue", "green"):
            entry = self.library.add(self.image(f"{color}.png", color))
            entries.append(entry)
            self.library.assign_pack(entry.id, pack)
        animation = self.root / "animated.webp"
        Image.new("RGBA", (512, 512), "red").save(
            animation,
            save_all=True,
            append_images=[Image.new("RGBA", (512, 512), "blue")],
            duration=[100, 100],
        )
        animated = self.library.add(animation)
        self.library.assign_pack(animated.id, pack)
        output = self.root / "mixed.wastickers"
        output.write_bytes(b"existing export")
        with self.assertRaises(ValueError):
            export_pack(self.library, pack, output)
        self.assertEqual(output.read_bytes(), b"existing export")
        self.library.assign_pack(animated.id, pack, remove=True)
        Image.new("RGBA", (32, 32), "red").save(entries[-1].path, format="WEBP")
        with self.assertRaises(ValueError):
            export_pack(self.library, pack, output)
        self.assertEqual(output.read_bytes(), b"existing export")


class StickerCreationPolicyTests(unittest.TestCase):
    def message(self, **kwargs) -> Message:
        return Message(
            chat_jid="chat@example.test",
            sender_jid="sender@example.test",
            body="",
            media_url="https://upload.example.test/image.png",
            **kwargs,
        )

    def test_context_action_only_for_supported_candidates_not_generic_binaries(self) -> None:
        self.assertTrue(can_create_sticker(self.message(media_kind="image")))
        self.assertTrue(
            can_create_sticker(self.message(media_kind="file", media_filename="photo.png"))
        )
        self.assertFalse(
            can_create_sticker(self.message(media_kind="file", media_filename="file.bin"))
        )
        self.assertFalse(
            can_create_sticker(self.message(media_kind="audio", media_filename="audio.ogg"))
        )
        self.assertFalse(can_create_sticker(self.message(media_kind="image", retracted=True)))
        self.assertFalse(
            can_create_sticker(self.message(media_kind="image", media_mime="image/svg+xml"))
        )
        self.assertFalse(
            can_create_sticker(self.message(media_kind="image", media_filename="animation.gif"))
        )

    def test_existing_local_source_never_downloads_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "photo.png"
            source.write_bytes(b"fixture")
            message = self.message(media_kind="image", media_local_path=str(source))
            with patch("cliente_xmpp.media.sticker_creation.download_media") as download:
                self.assertEqual(source_from_message(message, "account@example.test"), source)
            download.assert_not_called()

    def test_auto_description_is_explicit_persistent_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(Path(directory) / "settings.json")
            self.assertFalse(settings.load_sticker_auto_describe())
            settings.save_sticker_auto_describe(True)
            self.assertTrue(SettingsStore(settings.path).load_sticker_auto_describe())
            settings.save_sticker_auto_describe(False)
            self.assertFalse(settings.load_sticker_auto_describe())
