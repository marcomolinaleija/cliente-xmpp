from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import wx

from cliente_xmpp.media.downloads import sanitize_filename
from cliente_xmpp.models.chat import Chat, Message
from cliente_xmpp.storage.manager import StorageManager
from cliente_xmpp.storage.message_store import MessageStore
from cliente_xmpp.ui.storage_manager_dialog import StorageManagerDialog, format_storage_size


class _FakeChecklist:
    def __init__(self, count: int) -> None:
        self.count = count
        self.focused = 0
        self.checked: set[int] = set()
        self.hit_flags = wx.LIST_HITTEST_ONITEMLABEL

    def GetItemCount(self) -> int:
        return self.count

    def IsItemChecked(self, index: int) -> bool:
        return index in self.checked

    def CheckItem(self, index: int, checked: bool = True) -> None:
        if checked:
            self.checked.add(index)
        else:
            self.checked.discard(index)

    def GetNextItem(self, *_args: object) -> int:
        return self.focused

    def GetFirstSelected(self) -> int:
        return self.focused

    def HitTest(self, _position: object) -> tuple[int, int]:
        return self.focused, self.hit_flags


class _FakeKeyEvent:
    def __init__(self, key_code: int, *, control: bool = False) -> None:
        self.key_code = key_code
        self.control = control
        self.skipped = False

    def GetKeyCode(self) -> int:
        return self.key_code

    def ControlDown(self) -> bool:
        return self.control

    def Skip(self) -> None:
        self.skipped = True


class _FakeMouseEvent:
    def __init__(self) -> None:
        self.skipped = False

    def GetPosition(self) -> tuple[int, int]:
        return 0, 0

    def Skip(self) -> None:
        self.skipped = True


class StorageManagerTests(unittest.TestCase):
    def test_snapshot_counts_real_files_and_attributes_downloads_to_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir) / ".cliente-xmpp"
            store = MessageStore(app_dir / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "friend@example.test"
            store.upsert_chat(account_jid, Chat(chat_jid, "Amistad"))
            chat_dir = (
                app_dir
                / "downloads"
                / sanitize_filename(account_jid)
                / sanitize_filename(chat_jid)
            )
            chat_dir.mkdir(parents=True)
            audio_path = chat_dir / "voice.ogg"
            audio_path.write_bytes(b"a" * 120)
            orphan_path = chat_dir / "old-copy.bin"
            orphan_path.write_bytes(b"b" * 80)
            store.upsert_messages(
                account_jid,
                [
                    Message(
                        chat_jid=chat_jid,
                        sender_jid=chat_jid,
                        body="voz",
                        sent_at=datetime(2026, 7, 21, tzinfo=UTC),
                        message_id="voice-1",
                        media_url="https://upload.example.test/voice.ogg",
                        media_kind="audio",
                        media_local_path=str(audio_path),
                    )
                ],
            )

            snapshot = StorageManager(store, app_dir=app_dir).build_snapshot()

            audio = snapshot.category("audio")
            orphan = snapshot.category("orphan_downloads")
            self.assertIsNotNone(audio)
            self.assertIsNotNone(orphan)
            self.assertEqual(audio.size_bytes, 120)
            self.assertEqual(orphan.size_bytes, 80)
            self.assertEqual(len(snapshot.chats), 1)
            chat = snapshot.chats[0]
            self.assertEqual(chat.name, "Amistad")
            self.assertEqual(chat.size_bytes, 200)
            self.assertEqual(chat.referenced_file_count, 1)
            self.assertEqual(chat.unreferenced_file_count, 1)
            self.assertEqual(chat.message_count, 1)

    def test_recent_temporary_files_are_counted_but_not_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir) / ".cliente-xmpp"
            store = MessageStore(app_dir / "messages.sqlite3")
            recordings = app_dir / "recordings"
            recordings.mkdir()
            old_path = recordings / "ptt-old.ogg"
            recent_path = recordings / "ptt-recent.ogg"
            old_path.write_bytes(b"o" * 50)
            recent_path.write_bytes(b"r" * 70)
            recent_qr = app_dir / "whatsapp-linking" / "qr.png"
            recent_qr.parent.mkdir()
            recent_qr.write_bytes(b"q" * 30)
            old_time = time.time() - 3600
            os.utime(old_path, (old_time, old_time))

            snapshot = StorageManager(
                store,
                app_dir=app_dir,
                active_file_grace_seconds=600,
            ).build_snapshot()

            recordings_usage = snapshot.category("recordings")
            self.assertIsNotNone(recordings_usage)
            self.assertEqual(recordings_usage.size_bytes, 120)
            self.assertEqual(recordings_usage.reclaimable_bytes, 50)
            self.assertEqual(recordings_usage.reclaimable_file_count, 1)
            self.assertEqual(recordings_usage.file_paths, (str(old_path),))
            qr_usage = snapshot.category("other_cache")
            self.assertIsNotNone(qr_usage)
            self.assertEqual(qr_usage.size_bytes, 30)
            self.assertEqual(qr_usage.reclaimable_bytes, 0)

    def test_cleanup_rejects_external_paths_and_clears_sqlite_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / ".cliente-xmpp"
            store = MessageStore(app_dir / "messages.sqlite3")
            account_jid = "me@example.test"
            chat_jid = "friend@example.test"
            media_path = app_dir / "downloads" / "account" / "chat" / "photo.jpg"
            media_path.parent.mkdir(parents=True)
            media_path.write_bytes(b"photo")
            external_path = root / "do-not-delete.txt"
            external_path.write_text("private", encoding="utf-8")
            message = Message(
                chat_jid=chat_jid,
                sender_jid=chat_jid,
                body="foto",
                message_id="photo-1",
                media_url="https://upload.example.test/photo.jpg",
                media_kind="image",
                media_local_path=str(media_path),
            )
            store.upsert_messages(account_jid, [message])

            result = StorageManager(store, app_dir=app_dir).delete_files(
                (str(media_path), str(external_path))
            )

            self.assertFalse(media_path.exists())
            self.assertTrue(external_path.exists())
            self.assertEqual(result.deleted_file_count, 1)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.cleared_database_references, 1)
            loaded = store.load_recent_messages(account_jid, chat_jid)
            self.assertEqual(loaded[0].media_local_path, "")

    def test_total_deletion_only_operates_on_valid_app_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unsafe_store = MessageStore(root / "messages.sqlite3")
            with self.assertRaises(ValueError):
                StorageManager(unsafe_store, app_dir=root).delete_all_data()

            app_dir = root / ".cliente-xmpp"
            store = MessageStore(app_dir / "messages.sqlite3")
            cache_file = app_dir / "downloads" / "account" / "chat" / "large.bin"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_bytes(b"x" * 25)
            (app_dir / "settings.json").write_text("{}", encoding="utf-8")

            result = StorageManager(store, app_dir=app_dir).delete_all_data()

            self.assertFalse(result.failures)
            self.assertFalse(app_dir.exists())

    def test_formats_large_sizes_for_accessible_ui(self) -> None:
        self.assertEqual(format_storage_size(0), "0 B")
        self.assertEqual(format_storage_size(1536), "1.5 KB")
        self.assertEqual(format_storage_size(12 * 1024**3), "12.0 GB")

    def test_space_toggles_focused_rows_without_losing_previous_checks(self) -> None:
        control = _FakeChecklist(3)
        dialog = SimpleNamespace(_update_marked_action_buttons=lambda: None)

        StorageManagerDialog._on_checklist_key_down(
            dialog,
            _FakeKeyEvent(wx.WXK_SPACE),
            control,
        )
        control.focused = 1
        StorageManagerDialog._on_checklist_key_down(
            dialog,
            _FakeKeyEvent(wx.WXK_SPACE),
            control,
        )

        self.assertEqual(control.checked, {0, 1})

    def test_mouse_click_on_row_toggles_check_and_keeps_native_event(self) -> None:
        control = _FakeChecklist(2)
        event = _FakeMouseEvent()
        dialog = SimpleNamespace(_update_marked_action_buttons=lambda: None)

        StorageManagerDialog._on_checklist_mouse_down(dialog, event, control)

        self.assertEqual(control.checked, {0})
        self.assertTrue(event.skipped)


if __name__ == "__main__":
    unittest.main()
