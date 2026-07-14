from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.patch_marco_vps_compose_read_sync import patch_compose
from tools.patch_prosody_read_sync_privileges import patch_config
from tools.patch_slidge_whatsapp_read_sync import (
    patch_event_go,
    patch_session_go,
    patch_session_py,
)


class PatchSlidgeReadSyncTests(unittest.TestCase):
    def test_event_converter_is_inserted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.go"
            path.write_text(
                "package whatsapp\n\n"
                "// GroupAffiliation represents the set of privilidges given to a "
                "specific participant in a group.\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_event_go(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn("func newMarkChatAsReadEvent(", updated)
            self.assertIn("message.GetTimestamp() >= latestTimestamp", updated)
            self.assertIn("Actor:      actor", updated)
            self.assertFalse(patch_event_go(path, backup=False))

    def test_session_switch_is_patched_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.go"
            path.write_text(
                "\tcase *events.Receipt:\n"
                "\t\ts.propagateEvent(newReceiptEvent(s.ctx, s.client, evt))\n"
                "\tcase *events.Presence:\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_session_go(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn("case *events.MarkChatAsRead:", updated)
            self.assertFalse(patch_session_go(path, backup=False))

    def test_attachment_cleanup_is_compatible_with_new_slidge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(
                "        for attachment in attachments:\n"
                '            if global_config.NO_UPLOAD_METHOD != "symlink":\n'
                '                self.log.debug("Removing \'%s\' from disk", attachment.path)\n'
                "                if attachment.path is None:\n"
                "                    continue\n"
                "                Path(attachment.path).unlink(missing_ok=True)\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_session_py(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn(
                'if getattr(global_config, "NO_UPLOAD_PATH", None):',
                updated,
            )
            self.assertIn(
                'getattr(global_config, "NO_UPLOAD_METHOD", None) == "symlink"',
                updated,
            )
            self.assertNotIn('NO_UPLOAD_METHOD != "symlink"', updated)
            self.assertFalse(patch_session_py(path, backup=False))

    def test_attachment_cleanup_repairs_previous_compatibility_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.py"
            path.write_text(
                "        for attachment in attachments:\n"
                "            if getattr("
                'global_config, "NO_UPLOAD_METHOD", None) != "symlink":\n'
                '                self.log.debug("Removing \'%s\' from disk", attachment.path)\n'
                "                if attachment.path is None:\n"
                "                    continue\n"
                "                Path(attachment.path).unlink(missing_ok=True)\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_session_py(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn('getattr(global_config, "NO_UPLOAD_PATH", None)', updated)
            self.assertNotIn('NO_UPLOAD_METHOD", None) !=', updated)

    def test_prosody_pubsub_privileges_are_inserted_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "prosody.cfg.lua"
            path.write_text(
                'plugin_paths = { "/etc/prosody/modules" }\n'
                "local slidge_privileges = {\n"
                "    iq = {\n"
                '        ["jabber:iq:roster"] = "both";\n'
                "    };\n"
                "}\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_config(path, backup=False))
            updated = path.read_text(encoding="utf-8")
            self.assertIn(
                '["http://jabber.org/protocol/pubsub"] = "both";',
                updated,
            )
            self.assertIn(
                '["http://jabber.org/protocol/pubsub#owner"] = "set";',
                updated,
            )
            self.assertIn(
                'http_files_dir = "/var/lib/prosody/http_upload"',
                updated,
            )
            self.assertFalse(patch_config(path, backup=False))

    def test_compose_images_are_updated_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "compose.yml"
            path.write_text(
                "services:\n"
                "  prosody:\n"
                "    image: prosody/prosody:latest\n"
                "  slidge-whatsapp:\n"
                "    image: marco/slidge-whatsapp:candidate\n",
                encoding="utf-8",
            )

            args = {
                "prosody_image": "prosodyim/prosody:0.12",
                "bridge_image": "ghcr.io/example/bridge:read-sync",
                "backup": False,
            }
            self.assertTrue(patch_compose(path, **args))
            updated = path.read_text(encoding="utf-8")
            self.assertIn("image: prosodyim/prosody:0.12", updated)
            self.assertIn("image: ghcr.io/example/bridge:read-sync", updated)
            self.assertFalse(patch_compose(path, **args))


if __name__ == "__main__":
    unittest.main()
