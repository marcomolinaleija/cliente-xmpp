from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_web_version import (
    LOG_LINE,
    LOGGER_LINE,
    gateway_source,
    patch_package,
    payload_source,
)

PAYLOAD = '''package store
var waVersion = WAVersionContainer{2, 3000, 1042386815}
// Preserve call history and custom voice/protocol fields.
SupportCallLogHistory: proto.Bool(true),
'''
GATEWAY = "package whatsapp\nfunc init() {\n" + LOGGER_LINE + "\n}\n"


class BridgeWebVersionTests(unittest.TestCase):
    def test_call_contract_accepts_both_contexts_but_not_missing_or_wrong_routes(self):
        path = Path(__file__).resolve().parents[1] / "tools/bridge_calls_v27_contract_test.py"
        tree = ast.parse(path.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "has_history_call_route")
        namespace = {"re": re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        check = namespace["has_history_call_route"]
        for context in ("ctx", "s.ctx"):
            with self.subTest(context=context):
                self.assertTrue(check(
                    f'newCallLogRecordEvent({context}, client, record, "history_sync")'))
        for source in ("", 'newCallLogRecordEvent(ctx, client, record, "app_state")',
                       'newCallLogRecordEvent(other, client, record, "history_sync")'):
            with self.subTest(source=source):
                self.assertFalse(check(source))

    def test_updates_only_version_and_is_idempotent(self):
        updated = payload_source(PAYLOAD)
        self.assertEqual(updated, PAYLOAD.replace("1042386815", "1052556849"))
        self.assertEqual(payload_source(updated), updated)

    def test_unknown_missing_and_duplicate_versions_fail_closed(self):
        for source in ("", PAYLOAD + PAYLOAD, PAYLOAD.replace("1042386815", "1")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                payload_source(source)

    def test_gateway_logs_version_once_before_sessions_exist(self):
        updated = gateway_source(GATEWAY)
        self.assertIn(LOGGER_LINE + "\n" + LOG_LINE, updated)
        self.assertEqual(gateway_source(updated), updated)
        for source in ("", GATEWAY + GATEWAY, updated + "\n" + LOG_LINE):
            with self.subTest(source=source), self.assertRaises(ValueError):
                gateway_source(source)

    def test_runtime_and_effective_fork_are_patched_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = (root /
                       "site/slidge_whatsapp/vendor/go.mau.fi/whatsmeow/store/clientpayload.go")
            gateway = root / "site/slidge_whatsapp/gateway.go"
            fork = root / "fork/store/clientpayload.go"
            for path, source in ((payload, PAYLOAD), (gateway, GATEWAY), (fork, "unknown")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            with self.assertRaises(ValueError):
                patch_package(root / "site", root / "fork")
            self.assertEqual(payload.read_text(), PAYLOAD)
            self.assertEqual(gateway.read_text(), GATEWAY)
            fork.write_text(PAYLOAD, encoding="utf-8")
            patch_package(root / "site", root / "fork")
            patch_package(root / "site", root / "fork")
            self.assertEqual(payload.read_text(), fork.read_text())
            self.assertIn("1052556849", fork.read_text())

    def test_build_uses_same_fork_and_offline_native_validation(self):
        source = (Path(__file__).resolve().parents[1] /
                  "tools/Dockerfile.bridge-web-version").read_text()
        for marker in ("sha256:d50d94cd", "--fork /opt/bridge-whatsmeow",
                       "-race . ./voicecore/...", 'tags "mupdf extlib"',
                       "--network=none", "bridge_calls_v27_contract_test.py"):
            self.assertIn(marker, source)
        for forbidden in ("pip install", "go mod tidy", "git clone", "docker push"):
            self.assertNotIn(forbidden, source)
