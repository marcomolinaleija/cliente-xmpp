"""Run explicit test layers/areas; retain noisy diagnostics locally, not in chat."""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "tests" / "suites.json"
LAYERS = ("fast", "integration", "contracts")


def read_catalog(path: Path = CATALOG) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("Unsupported test catalog version")
    modules = data["modules"]
    actual = {p.stem for p in path.parent.glob("test_*.py")}
    if actual != set(modules):
        raise ValueError(f"Catalog drift: missing={sorted(actual - set(modules))}, "
                         f"stale={sorted(set(modules) - actual)}")
    for name, entry in modules.items():
        if not name.startswith("test_") or entry.get("layer") not in LAYERS:
            raise ValueError(f"Invalid module/layer: {name}")
        if not isinstance(entry.get("area"), str) or not entry["area"]:
            raise ValueError(f"Missing area: {name}")
        for override in entry.get("overrides", {}).values():
            if set(override) - {"layer", "area"} or not override:
                raise ValueError(f"Invalid override: {name}")
            if "layer" in override and override["layer"] not in LAYERS:
                raise ValueError(f"Invalid override layer: {name}")
            if "area" in override and not override["area"]:
                raise ValueError(f"Invalid override area: {name}")
    return modules


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def metadata(test_id: str, entry: dict) -> dict:
    _module, cls, method = test_id.rsplit(".", 2)
    result = {key: entry[key] for key in ("layer", "area")}
    for selector in (cls, f"{cls}.{method}"):
        result.update(entry.get("overrides", {}).get(selector, {}))
    return result


def matches(info: dict, suite: str, areas: list[str]) -> bool:
    return (suite == "all" or info["layer"] == suite) and (
        not areas or info["area"] in areas
    )


def select_tests(modules: dict, suite: str, areas: list[str], match: str = "") -> list:
    available = {entry["area"] for entry in modules.values()}
    available.update(o["area"] for e in modules.values()
                     for o in e.get("overrides", {}).values() if "area" in o)
    if set(areas) - available:
        raise ValueError(f"Unknown area; choose from {sorted(available)}")
    selected = []
    loader = unittest.TestLoader()
    for name, entry in sorted(modules.items()):
        variants = [entry, *({**entry, **o} for o in entry.get("overrides", {}).values())]
        if not any(matches(v, suite, areas) for v in variants):
            continue  # Do not import unrelated GUI/integration modules.
        tests = list(flatten(loader.loadTestsFromName(name)))
        if loader.errors:
            raise ValueError("\n".join(loader.errors))
        selectors = {t.id().split(".", 1)[1] for t in tests}
        selectors.update(s.split(".", 1)[0] for s in tuple(selectors))
        stale = set(entry.get("overrides", {})) - selectors
        if stale:
            raise ValueError(f"Stale selectors in {name}: {sorted(stale)}")
        for test in tests:
            identity = test.id()
            name_matches = not match or (
                fnmatch.fnmatchcase(identity, match) if "*" in match else match in identity
            )
            if matches(metadata(identity, entry), suite, areas) and name_matches:
                selected.append(test)
    if not selected:
        raise ValueError("Selection contains zero tests; refusing a false pass")
    return selected


class MeasuredResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []
        self.subcases = 0

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append((test.id(), time.perf_counter() - self.started))
        super().stopTest(test)

    def addSubTest(self, test, subtest, err):
        self.subcases += 1
        super().addSubTest(test, subtest, err)


def worker(args, report_path: Path) -> int:
    started = time.perf_counter()
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    modules = read_catalog()
    selected = select_tests(modules, args.suite, args.area, args.match)
    inventory = [dict(id=t.id(), **metadata(t.id(), modules[t.id().split(".")[0]]))
                 for t in selected]
    report = {"selection": inventory, "suite": args.suite, "areas": args.area}
    if args.list:
        report.update(tests=len(selected), elapsed=time.perf_counter() - started)
    else:
        if args.footprint:
            from tools.test_footprint import executed_lines
            context = executed_lines(ROOT)
        else:
            context = nullcontext({})
        with context as covered:
            result = unittest.TextTestRunner(
                verbosity=0, resultclass=MeasuredResult, buffer=True,
            ).run(unittest.TestSuite(selected))
        report.update(tests=result.testsRun, subcases=result.subcases,
                      failures=len(result.failures), errors=len(result.errors),
                      skipped=len(result.skipped), elapsed=time.perf_counter() - started,
                      timings=sorted(result.timings, key=lambda pair: pair[1], reverse=True),
                      problems=[{"id": t.id(), "traceback": trace}
                                for t, trace in result.failures + result.errors])
        if args.footprint:
            report["covered"] = {path: sorted(lines) for path, lines in sorted(covered.items())}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if args.list or result.wasSuccessful() else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=(*LAYERS, "all"), default="fast")
    parser.add_argument("--area", action="append", default=[], help="Repeat to union areas")
    parser.add_argument("--match", default="", help="Substring or * pattern in test ID")
    parser.add_argument("--list", action="store_true", help="Inventory only; no tests run")
    parser.add_argument("--profile", action="store_true", help="Show ten slowest methods")
    parser.add_argument("--footprint", action="store_true",
                        help="Optional executed-line comparison (adds instrumentation cost)")
    parser.add_argument("--worker-report", type=Path, help=argparse.SUPPRESS)
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(arguments)
    if args.worker_report:
        try:
            return worker(args, args.worker_report)
        except Exception:
            import traceback
            traceback.print_exc()
            return 2
    directory = ROOT / ".test-results"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    stem = directory / f"{stamp}-{args.suite}"
    log_path, report_path = stem.with_suffix(".log"), stem.with_suffix(".json")
    command = [sys.executable, str(Path(__file__).resolve()), *arguments,
               "--worker-report", str(report_path)]
    with log_path.open("x", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   check=False)
    if not report_path.exists():
        print(f"ERROR: test discovery/runner failed (exit={completed.returncode})")
        print(log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
    else:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        label = "LIST (not executed)" if args.list else (
            "PASS" if completed.returncode == 0 else "FAIL"
        )
        print(f"{label} suite={args.suite} areas={','.join(args.area) or 'all'} "
              f"methods={report['tests']} subcases={report.get('subcases', 0)} "
              f"time={report['elapsed']:.3f}s failures={report.get('failures', 0)} "
              f"errors={report.get('errors', 0)} skipped={report.get('skipped', 0)}")
        if args.list:
            counts = {}
            for item in report["selection"]:
                key = f"{item['layer']}/{item['area']}"
                counts[key] = counts.get(key, 0) + 1
            for key, count in sorted(counts.items()):
                print(f"  {key}: {count}")
        for problem in report.get("problems", [])[:3]:
            print(f"{problem['id']}\n{problem['traceback'][:3000]}")
        if args.profile:
            for identity, seconds in report.get("timings", [])[:10]:
                print(f"  {seconds:.3f}s {identity}")
    detail = str(report_path.relative_to(ROOT)) if report_path.exists() else "no JSON report"
    print(f"Details: {detail}; log: {log_path.relative_to(ROOT)} "
          f"({log_path.stat().st_size} bytes)")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
