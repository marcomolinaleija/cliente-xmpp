from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cliente_xmpp import __version__
from cliente_xmpp.app import smoke_test
from cliente_xmpp.app.main import main
from tools.ci import smoke_windows_release as runner


class PackagedSmokeTests(unittest.TestCase):
    def test_environment_excludes_credentials_and_development_tools(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "GH_TOKEN": "fixture-secret", "PYTHONPATH": "fixture-python",
            "IMAGEIO_FFMPEG_EXE": "fixture-system-ffmpeg", "PATH": "fixture-conda",
        }):
            root = Path(folder)
            environment = runner.isolated_environment(root)
            for name in ("GH_TOKEN", "PYTHONPATH", "IMAGEIO_FFMPEG_EXE"):
                self.assertNotIn(name, environment)
            self.assertNotIn("fixture-conda", environment["PATH"])
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(smoke_test.isolated_root(), root.resolve())
                (root / "profile/.cliente-xmpp").mkdir()
                with self.assertRaisesRegex(RuntimeError, "perfil nuevo"):
                    smoke_test.isolated_root()
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "perfil temporal"):
                    smoke_test.isolated_root()

    def test_only_a_complete_report_from_the_expected_frozen_version_passes(self):
        report = {"schema": 1, "version": "1.2.4", "frozen": True,
                  "status": "passed", "checks": sorted(runner.REQUIRED_CHECKS)}
        runner.verify_report(report, "1.2.4")
        variants = [{**report, field: value} for field, value in (
            ("version", "1.2.3"), ("frozen", False), ("status", "failed"), ("schema", 2),
        )]
        variants.extend({**report, "checks": sorted(runner.REQUIRED_CHECKS - {name})}
                        for name in runner.REQUIRED_CHECKS)
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(RuntimeError):
                runner.verify_report(variant, "1.2.4")

    def test_smoke_argument_bypasses_normal_startup_and_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "report.json"
            with patch("cliente_xmpp.app.smoke_test.run_smoke_test", return_value=1) as probe, \
                    patch("cliente_xmpp.app.main.SingleInstanceGuard") as guard:
                with self.assertRaises(SystemExit) as result:
                    main(["--smoke-test", str(destination)])
            self.assertEqual(result.exception.code, 1)
            probe.assert_called_once_with(str(destination))
            guard.assert_not_called()
            with patch.object(smoke_test, "isolated_root", side_effect=RuntimeError("fixture")), \
                    patch.object(smoke_test, "check_gui") as gui:
                self.assertEqual(smoke_test.run_smoke_test(str(destination)), 1)
            gui.assert_not_called()
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["status"],
                             "failed")

    def test_failed_exit_timeout_or_missing_report_blocks_the_packaged_probe(self):
        for failure in ("exit", "timeout", "report"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                destination = root / "report.json"
                with patch.object(runner, "validate_release"), \
                        patch.object(runner.update, "safe_extract"), \
                        patch.object(runner.update, "payload_root", return_value=root), \
                        patch.object(runner.subprocess, "run") as execute:
                    if failure == "timeout":
                        execute.side_effect = subprocess.TimeoutExpired("fixture", 90)
                    else:
                        execute.return_value.returncode = 1 if failure == "exit" else 0
                    with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                        runner.smoke_release(root / "fixture.zip", destination, "1.2.4")
                report = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "failed")
                self.assertIn("error", report)


class NativeStartupSmokeTests(unittest.TestCase):
    def test_source_startup_with_real_gui_and_native_runtime_in_a_fresh_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "report.json"
            process = subprocess.run(
                [sys.executable, "-m", "cliente_xmpp.app.main", "--smoke-test", str(destination)],
                cwd=runner.ROOT, env=runner.isolated_environment(root), timeout=45,
                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertTrue(destination.is_file(), process.stderr.decode(errors="replace")[-2000:])
            report = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(process.returncode, 0, report)
            self.assertEqual(report["version"], __version__)
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual(set(report["checks"]), runner.REQUIRED_CHECKS)
            self.assertFalse(report["frozen"])


if __name__ == "__main__":
    unittest.main()
