from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.read_voice_flow import MAX_INPUT, MAX_LINE, parse_report, read_reports
from tools.run_voice_call_probe import (
    isolated_run_args,
    prepare_codec_reference,
    prepare_context,
    probe_fingerprint,
    summarize_go_tests,
    verify_archive,
)


class VoiceCallProbeTests(unittest.TestCase):
    def test_codec_reference_rejects_modified_cached_fixture_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codec-reference").mkdir()
            (root / "codec-reference/synthetic.raw").write_bytes(b"changed")
            (root / "codec-reference.json").write_text(json.dumps({
                "base_url": "https://example.invalid/",
                "files": {"synthetic.raw": {"bytes": 7, "sha256": "0" * 64}},
            }), encoding="utf-8")
            with patch("tools.run_voice_call_probe.EXPERIMENT", root), \
                    patch("tools.run_voice_call_probe.urllib.request.urlopen") as network:
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    prepare_codec_reference(root)
            network.assert_not_called()

    def test_generated_patch_keeps_unix_newlines_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                for name in ("callmanager.go", "callmanager_relay.go", "callmanager_signaling.go"):
                    body = b"package call\n"
                    member = tarfile.TarInfo("fixture/internal/voip/call/" + name)
                    member.size = len(body)
                    output.addfile(member, io.BytesIO(body))
            manifest = {"source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            def changed(source):
                return source + "// fixture change\n"
            with patch("tools.run_voice_call_probe.manager", changed), \
                    patch("tools.run_voice_call_probe.relay", changed), \
                    patch("tools.run_voice_call_probe.signaling", changed), \
                    patch("tools.run_voice_call_probe.MEDIA_TRANSFORMS", {}):
                context = prepare_context(archive, root, manifest)
            generated = (context / "wacalls-lifecycle.patch").read_bytes()
            self.assertIn(b"+// fixture change\n", generated)
            self.assertNotIn(b"\r\n", generated)

    def test_old_image_fingerprint_changes_with_engine_or_pinned_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("canprobe", "voicecore"):
                (root / folder).mkdir()
            for name in ("Dockerfile", "wacalls-srtp-auth.patch", "upstream.json",
                         "codec-reference.json"):
                (root / name).write_text("fixture", encoding="utf-8")
            with patch("tools.run_voice_call_probe.EXPERIMENT", root):
                before = probe_fingerprint()
                (root / "voicecore/engine.go").write_text("new engine", encoding="utf-8")
                changed = probe_fingerprint()
                self.assertNotEqual(before, changed)
                (root / "upstream.json").write_text("new pinned base", encoding="utf-8")
                self.assertNotEqual(changed, probe_fingerprint())

    def test_checksum_rejects_modified_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.tar.gz"
            path.write_bytes(b"fixture source")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_archive(path, checksum)
            path.write_bytes(b"modified fixture")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_archive(path, checksum)

    def test_selection_must_finish_and_contain_tests(self):
        def events(*rows):
            return [json.dumps(row) for row in rows]

        test_pass = {"Action": "pass", "Package": "fixture", "Test": "TestMedia"}
        package_pass = {"Action": "pass", "Package": "fixture"}
        result = summarize_go_tests(events(test_pass, package_pass))
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["packages"], ["fixture"])
        for rows in (
            [], [package_pass], [test_pass],
            [test_pass, package_pass, {"Action": "start", "Package": "unfinished"}],
            [test_pass, package_pass, {"Action": "run", "Package": "fixture", "Test": "Hung"}],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                summarize_go_tests(events(*rows))

    def test_failed_or_invalid_test_stream_never_passes(self):
        for lines in (
            [json.dumps({"Action": "fail", "Package": "fixture", "Test": "TestMedia"})],
            [json.dumps({"Action": "pass", "Package": "fixture"}), "compiler failed"],
        ):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                summarize_go_tests(lines)

    def test_execution_has_no_network_mount_or_published_port(self):
        args = isolated_run_args("fixture-image")
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertIn("no-new-privileges", args)
        for forbidden in ("--mount", "-v", "--volume", "-p", "--publish", "--env-file"):
            self.assertNotIn(forbidden, args)


class VoiceMetricsReaderTests(unittest.TestCase):
    def test_accepts_both_proxy_schemas_and_nested_native_counters(self):
        flow = {"tag": "abcdef01", "final": True, "elapsed_ms": 2000,
                "client_packets": 100, "client_idle_ms": None}
        native = {"tag": "abcdef0123456789", "final": False, "elapsed_ms": 2000,
                  "counters": {"encode_frames": 30, "rtp_packets": 30},
                  "relays": [{"slot": 1, "accepted": 30, "failed": 0,
                              "not_open": 0, "send_max_us": 3}], "reports_dropped": 0}
        lines = []
        for profile in (False, True):
            if profile:
                flow.update(owner_loop_delay_max_ms=900, owner_hotspots=[
                    {"location": "asyncio.events:_run:88", "samples": 10}])
            lines.append("ignored prefix CAN voice flow " + json.dumps(flow))
        lines.append("CAN_VOICE_NATIVE " + json.dumps(native))
        result = read_reports(io.BytesIO(("\n".join(lines)+"\n").encode()))
        self.assertEqual([r["kind"] for r in result["reports"]], ["flow", "flow", "native"])
        self.assertEqual(result["rejected_chunks"], 0)

    def test_rejects_identity_unknown_nested_fields_and_invalid_numbers(self):
        baseline = {"tag": "abcdef01", "final": True, "elapsed_ms": 2000}
        for extra in ({"jid": "fixture@example.invalid"}, {"client_packets": True},
                      {"client_packets": -1}, {"client_packets": 1.2},
                      {"owner_hotspots": [{"location": "asyncio.events:_run:88",
                                           "samples": 2, "locals": "fixture-secret"}]},
                      {"owner_hotspots": [{"location": "fixture@example.invalid",
                                           "samples": 2}]}):
            with self.subTest(extra=extra):
                self.assertIsNone(parse_report("CAN voice flow " + json.dumps(baseline | extra)))
        malformed = ["fixture-secret", "CAN voice flow {broken", "CAN voice flow []",
                     "CAN voice flow " + "["*2000]
        result = read_reports(io.BytesIO(("\n".join(malformed)+"\n").encode()))
        self.assertEqual(result["reports"], [])
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_bounds_scan_output_and_oversized_line_suffix_is_not_a_new_record(self):
        report = b'CAN voice flow {"tag":"abcdef01","final":true,"elapsed_ms":1}\n'
        result = read_reports(io.BytesIO(b"x"*MAX_LINE + report + report*80))
        self.assertEqual(len(result["reports"]), 64)
        self.assertEqual(result["rejected_chunks"], 2)
        result = read_reports(io.BytesIO(b"x"*(MAX_INPUT+MAX_LINE)))
        self.assertEqual(result["bytes_scanned"], MAX_INPUT)
        self.assertEqual(result["reports"], [])


if __name__ == "__main__":
    unittest.main()
