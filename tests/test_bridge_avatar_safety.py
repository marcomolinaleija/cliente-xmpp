from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.bridge_avatar_safety import safe_avatar_preferences
from tools.patch_slidge_avatar_safety_v32 import (
    GATEWAY_CHANGES,
    SESSION_CHANGE,
    patch_package,
)


class AvatarSafetyTests(unittest.TestCase):
    def test_safe_defaults_preserve_other_preferences_and_base_fields(self):
        fields = [
            SimpleNamespace(var="sync_avatar", value="true"),
            SimpleNamespace(var="sync_presence", value="true"),
            SimpleNamespace(var="reaction_fallback", value="false"),
        ]
        safe = safe_avatar_preferences(fields)
        self.assertEqual([f.value for f in safe], ["false", "true", "false"])
        self.assertEqual(fields[0].value, "true")
        self.assertIsNot(safe[0], fields[0])

    def test_patch_is_complete_idempotent_and_rejects_drift_without_writes(self):
        # Minimal source contracts; the real pinned source is checked in the image build.
        gateway = """from . import config
class Gateway:
    PREFERENCES = [
        *BaseGateway.PREFERENCES,
    ]
    def __init__(self):
        super().__init__()
"""
        session = "class Session:\n    async def on_avatar(self):\n" + SESSION_CHANGE[0]
        sources = {"gateway.py": gateway, "session.py": session}
        hashes = {n: hashlib.sha256(s.encode()).hexdigest() for n, s in sources.items()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            for name, source in sources.items():
                (package / name).write_text(source, encoding="utf-8")
            with patch("tools.patch_slidge_avatar_safety_v32.BASE_SHA256", hashes):
                self.assertTrue(patch_package(root))
                self.assertFalse(patch_package(root))
                for name in ("gateway.py", "session.py", "avatar_safety.py"):
                    path = package / name
                    original = path.read_bytes()
                    path.write_bytes(original + b"\n# unexpected drift\n")
                    before = {p.name: p.read_bytes() for p in package.iterdir()}
                    with self.assertRaises(SystemExit):
                        patch_package(root)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in package.iterdir()})
                    path.write_bytes(original)

    def test_unknown_or_partial_base_is_rejected_before_helper_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "slidge_whatsapp"
            package.mkdir()
            (package / "gateway.py").write_text(GATEWAY_CHANGES[0][1], encoding="utf-8")
            (package / "session.py").write_text("unexpected base", encoding="utf-8")
            before = {p.name: p.read_bytes() for p in package.iterdir()}
            with self.assertRaises(SystemExit):
                patch_package(root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in package.iterdir()})


if __name__ == "__main__":
    unittest.main()
