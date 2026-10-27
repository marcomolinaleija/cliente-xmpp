"""Guarded patch/image contracts; installed SQL/async behavior is tested in the image."""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_group_echo import archive_source, patch_package, room_source

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = '''class MessageArchive:
    def add(
        self, msg, participant=None, archive_only=False, legacy_msg_id=None,
    ) -> None:
        """Add an archive message."""
        if not archivable(msg):
            return
        new_msg = copy(msg)
        if "replace" in msg:
            legacy_msg_id = None
        with self.__store.session(expire_on_commit=False) as orm:
            if self.room.id is None:
                self.room = self.__rooms_store.get(
                    orm, self.room.user_account_id, self.room.legacy_id
                )
            self.__store.add_message(
                orm, self.room.id, HistoryMessage(new_msg), archive_only, legacy_msg_id,
            )
            orm.commit()

    def __iter__(self):
        return iter(self.get_all())
'''
ROOM = '''class Room:
    async def echo(self, msg, legacy_msg_id=None):
        user_part = await self.get_user_participant()
        self.archive.add(msg, user_part)

        for resource in self.user_full_jids():
            msg.send()

        return msg["stanza_id"]["id"]

    def _post_avatar_update(self, avatar):
        pass
'''


class GroupEchoPatchTests(unittest.TestCase):
    def test_transforms_parse_are_idempotent_and_preserve_unrelated_methods(self):
        for transform, original, tail in ((archive_source, ARCHIVE, "    def __iter__("),
                                          (room_source, ROOM, "    def _post_avatar_update(")):
            with self.subTest(transform=transform.__name__):
                result = transform(original)
                ast.parse(result)
                self.assertEqual(result, transform(result))
                self.assertEqual(result[result.index(tail):], original[original.index(tail):])

    def test_workers_are_awaited_without_sharing_mutable_room_or_participant(self):
        result = archive_source(ARCHIVE)
        worker = result[result.index("        def commit():"):result.index("        task =")]
        self.assertNotIn("self.", worker)
        self.assertNotIn("participant", worker)
        self.assertIn("with store.session(expire_on_commit=False) as orm:", worker)
        self.assertIn("HistoryMessage(xml, when=when)", worker)
        self.assertIn("await asyncio.shield(task)", result)
        self.assertIn("await asyncio.gather(task, return_exceptions=True)", result)
        echo = room_source(ROOM)
        self.assertIn('self.session.lock(("can-group-echo", str(self.jid)))', echo)
        self.assertLess(echo.index("await self.archive.add_async"), echo.index("msg.send()"))

    def test_unknown_or_duplicate_anchors_fail_closed(self):
        for transform, original in ((archive_source, ARCHIVE), (room_source, ROOM)):
            sources = ("", original + original, original.replace("    def ", "    def changed_"))
            for source in sources:
                with self.subTest(transform=transform.__name__, source=source[:20]):
                    with self.assertRaises(ValueError):
                        transform(source)

    def test_package_validates_both_files_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "slidge/group/archive.py"
            room = root / "slidge/group/room.py"
            archive.parent.mkdir(parents=True)
            archive.write_text(ARCHIVE, encoding="utf-8")
            room.write_text("unknown", encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root)
            self.assertEqual(archive.read_text(encoding="utf-8"), ARCHIVE)
            room.write_text(ROOM, encoding="utf-8")
            self.assertTrue(patch_package(root))
            self.assertFalse(patch_package(root))

    def test_image_requires_baseline_and_all_existing_smokes_offline_as_runtime_user(self):
        source = (ROOT / "tools/Dockerfile.bridge-group-echo").read_text(encoding="utf-8")
        self.assertIn("ARG BRIDGE_IMAGE=cliente-xmpp-bridge:muc-xml-r5-flat-20261022", source)
        self.assertIn('check(smoke, "--expect-blocking")', source)
        self.assertIn("user=user.pw_uid, group=user.pw_gid", source)
        self.assertIn("for _ in range(2):", source)
        self.assertEqual(source.count("RUN --network=none"), 1)
        for name in ("read_markers", "recovery", "linking", "muc_history"):
            self.assertIn(f"smoke_bridge_{name}_runtime.py", source)
        for forbidden in ("pip install", "go build", "--network=host"):
            self.assertNotIn(forbidden, source)
