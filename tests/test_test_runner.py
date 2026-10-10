from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.run_tests import (
    MeasuredResult,
    flatten,
    main,
    matches,
    metadata,
    read_catalog,
    select_tests,
)
from tools.test_footprint import executed_lines


class RunnerTests(unittest.TestCase):
    def entry(self):
        return {"area": "media", "layer": "fast", "overrides": {
            "Sample": {"area": "history"},
            "Sample.test_slow": {"layer": "integration"},
        }}

    def test_specific_method_inherits_class_and_module_metadata(self):
        self.assertEqual(metadata("test_sample.Sample.test_slow", self.entry()),
                         {"area": "history", "layer": "integration"})
        self.assertEqual(metadata("test_sample.Other.test_fast", self.entry()),
                         {"area": "media", "layer": "fast"})

    def test_layer_and_area_union_do_not_enlarge_selection(self):
        row = {"area": "media", "layer": "integration"}
        for suite, areas, expected in (
            ("all", [], True), ("integration", ["media", "history"], True),
            ("fast", [], False), ("all", ["storage"], False),
        ):
            with self.subTest(suite=suite, areas=areas):
                self.assertEqual(matches(row, suite, areas), expected)

    def test_catalog_missing_stale_and_invalid_layers_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_sample.py").write_text("", encoding="utf-8")
            path = root / "suites.json"
            entry = {"area": "media", "layer": "fast"}
            for modules in ({}, {"test_stale": entry},
                            {"test_sample": {**entry, "layer": "typo"}}):
                with self.subTest(modules=modules):
                    path.write_text(json.dumps({"version": 1, "modules": modules}),
                                    encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_catalog(path)

    def test_unrelated_module_is_not_imported_and_empty_selection_fails(self):
        modules = {"test_other": {"area": "bridge", "layer": "contracts"}}
        with patch("tools.run_tests.unittest.TestLoader.loadTestsFromName") as load:
            with self.assertRaisesRegex(ValueError, "zero tests"):
                select_tests(modules, "fast", [])
            load.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Unknown area"):
                select_tests(modules, "all", ["typo"])

    def test_selection_and_nested_suites_preserve_each_identity_once(self):
        class Sample(unittest.TestCase):
            def test_fast(self):
                pass

            def test_slow(self):
                pass

            def id(self):
                return f"test_sample.Sample.{self._testMethodName}"

        nested = unittest.TestSuite([unittest.TestSuite([Sample("test_fast")]),
                                    Sample("test_slow")])
        self.assertEqual(len(list(flatten(nested))), 2)
        with patch("tools.run_tests.unittest.TestLoader.loadTestsFromName",
                   return_value=nested):
            chosen = select_tests({"test_sample": self.entry()}, "fast", ["history"])
            self.assertEqual([t.id() for t in chosen], ["test_sample.Sample.test_fast"])
            chosen = select_tests({"test_sample": self.entry()}, "all", [], "*slow")
            self.assertEqual([t.id() for t in chosen], ["test_sample.Sample.test_slow"])

    def test_stale_override_cannot_hide_a_removed_test(self):
        entry = {"area": "media", "layer": "fast", "overrides": {
            "Missing": {"layer": "integration"},
        }}
        with patch("tools.run_tests.unittest.TestLoader.loadTestsFromName",
                   return_value=unittest.TestSuite()):
            with self.assertRaisesRegex(ValueError, "Stale selectors"):
                select_tests({"test_sample": entry}, "all", [])

    def test_failed_import_is_not_reported_as_a_pass(self):
        with self.assertRaisesRegex(ValueError, "Failed to import"):
            select_tests({"test_module_that_does_not_exist": {
                "area": "media", "layer": "fast",
            }}, "all", [])

    def test_failed_subcase_counts_and_preserves_failure(self):
        class Broken(unittest.TestCase):
            def runTest(self):
                for expected in (True, False):
                    with self.subTest(expected=expected):
                        self.assertTrue(expected)

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=MeasuredResult,
                                        verbosity=0).run(Broken())
        self.assertEqual(result.subcases, 2)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures), 1)

    def test_parent_keeps_failed_exit_code_and_bounds_diagnostics(self):
        def fail(command, **_options):
            report = Path(command[-1])
            report.write_text(json.dumps({
                "tests": 1, "elapsed": 0.01, "failures": 1, "errors": 0,
                "problems": [{"id": "fixture.failure", "traceback": "x" * 20_000}],
            }), encoding="utf-8")
            self.assertNotIn("unexpected-unit-test-argument", command)
            return SimpleNamespace(returncode=1)

        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch("tools.run_tests.ROOT", Path(directory)),
                patch("tools.run_tests.subprocess.run", side_effect=fail),
                patch("tools.run_tests.sys.argv", ["runner", "unexpected-unit-test-argument"]),
                redirect_stdout(output),
            ):
                self.assertEqual(main([]), 1)
            self.assertIn("FAIL suite=fast", output.getvalue())
            self.assertLess(len(output.getvalue()), 4000)
            self.assertEqual(len(list((Path(directory) / ".test-results").glob("*.json"))), 1)

    def test_parent_keeps_discovery_error_without_a_json_report(self):
        def fail(_command, **options):
            options["stdout"].write("fixture import error\n")
            return SimpleNamespace(returncode=2)

        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch("tools.run_tests.ROOT", Path(directory)),
                patch("tools.run_tests.subprocess.run", side_effect=fail),
                redirect_stdout(output),
            ):
                self.assertEqual(main([]), 2)
            self.assertIn("ERROR: test discovery/runner failed", output.getvalue())
            self.assertIn("fixture import error", output.getvalue())
            self.assertIn("no JSON report", output.getvalue())
            self.assertNotIn("PASS", output.getvalue())

    def test_optional_footprint_cleans_up_on_error_without_changing_function(self):
        import sys

        from cliente_xmpp.models.names import unescape_jid_text
        from tools.run_tests import ROOT

        slots = [sys.monitoring.get_tool(i) for i in range(1, 6)]
        with self.assertRaisesRegex(RuntimeError, "fixture error"):
            with executed_lines(ROOT) as covered:
                self.assertEqual(unescape_jid_text("fixture\\20name"), "fixture name")
                raise RuntimeError("fixture error")
        self.assertTrue(covered["cliente_xmpp/models/names.py"])
        self.assertEqual([sys.monitoring.get_tool(i) for i in range(1, 6)], slots)


class CatalogContractTests(unittest.TestCase):
    def test_every_discovered_method_belongs_to_exactly_one_layer(self):
        modules = read_catalog()
        all_ids = {t.id() for t in select_tests(modules, "all", [])}
        layers = [{t.id() for t in select_tests(modules, layer, [])}
                  for layer in ("fast", "integration", "contracts")]
        self.assertEqual(all_ids, set.union(*layers))
        self.assertEqual(sum(map(len, layers)), len(all_ids))
