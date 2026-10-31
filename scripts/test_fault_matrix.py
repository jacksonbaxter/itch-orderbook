#!/usr/bin/env python3
"""Self-test of fault_matrix.py on a throwaway project.

The matrix decides what counts as proof, so its own verdicts need showing to fail.
Each test builds a tiny project: payload.txt, read by check.py, which passes when the
payload says "good". The real CMake path is used with a project that has no compiler
in it, so that a build failure is a real one and the whole file runs in seconds.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fault_matrix as fm  # noqa: E402

SCRATCH = fm.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

CHECK = """\
import sys
from pathlib import Path
text = Path(__file__).with_name("payload.txt").read_text()
print("payload:", text.strip())
if "hang" in text:
    import time
    time.sleep(60)
if "crash" in text:
    import os
    os.abort()
if "other" in text:
    print("something else went wrong")
    sys.exit(3)
if "good" not in text:
    print("CHECK FAILED: payload is broken")
    sys.exit(1)
"""

CMAKE = """\
cmake_minimum_required(VERSION 3.28)
project(selftest LANGUAGES NONE)
add_custom_target(payload ALL COMMAND ${CMAKE_COMMAND} -E true)
"""

PRESETS = {
    "version": 6,
    "configurePresets": [{"name": "plain", "generator": "Ninja",
                          "binaryDir": "${sourceDir}/build/${presetName}"}],
    "buildPresets": [{"name": "plain", "configurePreset": "plain"}],
}

TREE = ["CMakeLists.txt", "CMakePresets.json", "check.py", "payload.txt", "input.bin", "notes.txt"]


def good_fault(**changes):
    fault = {
        "id": "good",
        "breaks": "the payload",
        "check": "check.py",
        "mutations": [{"file": "payload.txt", "old": "good", "new": "bad"}],
        "command": ["{python}", "{tree}/check.py"],
        "expect": {"signature": "CHECK FAILED"},
    }
    fault.update(changes)
    return fault


class Matrix(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        (self.root / "faults").mkdir()
        (self.root / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        (self.root / "CMakePresets.json").write_text(json.dumps(PRESETS), encoding="utf-8")
        (self.root / "check.py").write_text(CHECK, encoding="utf-8")
        (self.root / "payload.txt").write_text("good\n", encoding="utf-8")
        (self.root / "input.bin").write_bytes(b"\x00\x01\x02\x03")
        (self.root / "notes.txt").write_text("twin twin\n", encoding="utf-8")
        self.output = ""

    def define(self, *faults, area="demo", tree=None):
        document = {"area": area, "tree": tree or TREE, "faults": list(faults)}
        path = self.root / "faults" / f"{area}.json"
        path.write_text(json.dumps(document), encoding="utf-8")

    def matrix(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fm.main(["--root", str(self.root), "--faults-dir", str(self.root / "faults"),
                            "--build-root", str(self.root / "work"),
                            "--out", str(self.root / "report.md"), *extra])
        self.output = out.getvalue() + err.getvalue()
        return code

    def verdict_row(self, fault_id="good"):
        for line in (self.root / "report.md").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"| `{fault_id}`"):
                return line
        self.fail(f"no row for {fault_id}")

    def assert_verdict(self, verdict, fault_id="good"):
        self.assertIn(f"| {verdict} |", self.verdict_row(fault_id))

    def test_a_caught_fault_is_detected(self):
        self.define(good_fault())
        self.assertEqual(self.matrix(), 0, self.output)
        self.assert_verdict(fm.DETECTED)
        self.assertIn("1 of 1 detected", (self.root / "report.md").read_text(encoding="utf-8"))

    def test_the_mutant_is_injected_only_into_the_copy(self):
        self.define(good_fault())
        self.matrix()
        self.assertEqual((self.root / "payload.txt").read_text(encoding="utf-8"), "good\n")

    def test_old_text_that_is_absent_is_stale(self):
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "gone", "new": "x"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.STALE)
        self.assertIn("occurs 0 times", self.output)

    def test_old_text_that_occurs_twice_is_stale(self):
        self.define(good_fault(mutations=[{"file": "notes.txt", "old": "twin", "new": "x"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.STALE)
        self.assertIn("occurs 2 times", self.output)

    def test_a_missing_file_is_stale(self):
        self.define(good_fault(mutations=[{"file": "nope.txt", "old": "a", "new": "b"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.STALE)

    def test_a_stale_fault_fails_the_run_even_when_the_rest_pass(self):
        self.define(good_fault(),
                    good_fault(id="old", mutations=[{"file": "payload.txt", "old": "x", "new": "y"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.DETECTED)
        self.assert_verdict(fm.STALE, "old")

    def test_an_input_mutation_that_changes_nothing_is_stale(self):
        self.define(good_fault(mutations=[],
                               inputs=[{"file": "input.bin", "op": "truncate", "size": 100}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.STALE)

    def test_a_fault_with_no_effect_is_undetected(self):
        self.define(good_fault(mutations=[{"file": "notes.txt", "old": "twin twin", "new": "x"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.UNDETECTED)

    def test_a_failing_control_fails_the_run_and_no_mutant_runs(self):
        (self.root / "payload.txt").write_text("broken before any fault\n", encoding="utf-8")
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "broken", "new": "b"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.NOT_RUN)
        self.assertIn("control FAILED", self.output)
        self.assertIn("exited 1 on the clean tree", self.output)

    def test_a_signature_seen_on_the_clean_tree_fails_the_control(self):
        self.define(good_fault(expect={"signature": "payload:"}))
        self.assertEqual(self.matrix(), 1)
        self.assertIn("signature appears on the clean tree", self.output)
        self.assert_verdict(fm.NOT_RUN)

    def test_a_failure_without_the_signature_is_not_a_detection(self):
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "good", "new": "good other"}]))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.WRONG_SIGNATURE)

    def test_a_crash_does_not_count_unless_the_signature_says_so(self):
        crash = [{"file": "payload.txt", "old": "good", "new": "good crash"}]
        self.define(good_fault(mutations=crash))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.WRONG_SIGNATURE)

        (self.root / "check.py").write_text(
            CHECK.replace('import os\n', 'import os\n    print("planted abort", flush=True)\n'),
            encoding="utf-8")
        self.define(good_fault(mutations=crash, expect={"signature": "planted abort"}))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assert_verdict(fm.DETECTED)

    def test_the_wrong_exit_code_is_not_a_detection(self):
        self.define(good_fault(expect={"signature": "CHECK FAILED", "exit": 2}))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.WRONG_SIGNATURE)

    def test_a_check_that_never_ends_is_a_timeout(self):
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "good", "new": "good hang"}],
                               timeout=1))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.TIMEOUT)

    def test_a_mutant_that_does_not_build_is_an_error_and_not_a_detection(self):
        self.define(good_fault(
            mutations=[{"file": "CMakeLists.txt", "old": "-E true", "new": "-E false"}],
            build={"preset": "plain", "targets": ["payload"], "gtest": False}))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.BUILD_ERROR)

    def test_a_mutant_that_does_not_configure_is_an_error_and_not_a_detection(self):
        self.define(good_fault(
            mutations=[{"file": "CMakeLists.txt", "old": "project(selftest", "new": "projec(selftest"}],
            build={"preset": "plain", "targets": ["payload"], "gtest": False}))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.BUILD_ERROR)

    def test_a_clean_build_that_fails_fails_the_control(self):
        cmake = self.root / "CMakeLists.txt"
        cmake.write_text(CMAKE.replace("-E true", "-E false"), encoding="utf-8")
        self.define(good_fault(build={"preset": "plain", "targets": ["payload"], "gtest": False}))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.NOT_RUN)

    def test_a_check_that_is_the_build_can_detect(self):
        self.define(good_fault(
            mutations=[{"file": "CMakeLists.txt", "old": "-E true", "new": "-E false"}],
            build={"preset": "plain", "targets": [], "gtest": False},
            command=["cmake", "--build", "--preset", "plain", "--target", "payload"],
            expect={"signature": "FAILED: "}))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assert_verdict(fm.DETECTED)

    def test_input_operations(self):
        data = b"\x00\x01\x02\x03"
        self.assertEqual(fm.flip_byte(data, {"offset": 1}), b"\x00\x00\x02\x03")
        self.assertEqual(fm.truncate(data, {"size": -1}), b"\x00\x01\x02")
        self.assertEqual(fm.delete_bytes(data, {"offset": 1, "length": 2}), b"\x00\x03")
        self.assertEqual(json.loads(fm.json_set(b'{"a": {"b": 1}}', {"path": ["a", "b"], "value": 2})),
                         {"a": {"b": 2}})

    def test_duplicate_bytes_repeats_a_span_in_place(self):
        self.assertEqual(fm.duplicate_bytes(b"abcd", {"offset": 1, "length": 2}), b"abcbcd")

    def test_replace_text_needs_exactly_one_occurrence(self):
        self.assertEqual(fm.replace_text(b"a,b,c", {"old": "b", "new": "x"}), b"a,x,c")
        with self.assertRaises(fm.DefinitionError):
            fm.replace_text(b"a,a", {"old": "a", "new": "x"})
        with self.assertRaises(fm.DefinitionError):
            fm.replace_text(b"a", {"old": "b", "new": "x"})

    def test_input_fault_is_applied_to_the_copy(self):
        (self.root / "check.py").write_text(
            CHECK + 'if Path(__file__).with_name("input.bin").read_bytes()[0] != 0:\n'
                    '    print("INPUT CHANGED")\n    sys.exit(1)\n',
            encoding="utf-8")
        self.define(good_fault(mutations=[], expect={"signature": "INPUT CHANGED"},
                               inputs=[{"file": "input.bin", "op": "flip_byte", "offset": 0}]))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assertEqual((self.root / "input.bin").read_bytes()[0], 0)

    def test_definition_errors_stop_the_run(self):
        cases = {
            "unknown key": good_fault(surprise=1),
            "no signature": good_fault(expect={}),
            "bad regex": good_fault(expect={"signature": "("}),
            "injects nothing": good_fault(mutations=[]),
            "build placeholder without a build": good_fault(command=["{build}/x"]),
            "no-op mutation": good_fault(mutations=[{"file": "a", "old": "b", "new": "b"}]),
        }
        for name, fault in cases.items():
            with self.subTest(name):
                self.define(fault)
                self.assertEqual(self.matrix(), 2, self.output)

    def test_duplicate_ids_are_refused_across_areas(self):
        self.define(good_fault(), area="one")
        self.define(good_fault(), area="two")
        self.assertEqual(self.matrix(), 2)
        self.assertIn("duplicate id", self.output)

    def test_area_must_match_its_file(self):
        (self.root / "faults" / "named.json").write_text(
            json.dumps({"area": "other", "faults": []}), encoding="utf-8")
        self.assertEqual(self.matrix(), 2)

    def test_selection_list_and_unknown_names(self):
        self.define(good_fault(), area="one")
        self.define(good_fault(id="second"), area="two")
        self.assertEqual(self.matrix("--list"), 0)
        self.assertIn("good", self.output)
        self.assertIn("second", self.output)
        self.assertEqual(self.matrix("--area", "two"), 0, self.output)
        self.assertNotIn("| `good`", (self.root / "report.md").read_text(encoding="utf-8"))
        self.assertEqual(self.matrix("--only", "good"), 0, self.output)
        self.assertNotIn("| `second`", (self.root / "report.md").read_text(encoding="utf-8"))
        self.assertEqual(self.matrix("--only", "nothing"), 2)
        self.assertEqual(self.matrix("--area", "nothing"), 2)

    def test_the_report_holds_no_scratch_path(self):
        (self.root / "check.py").write_text(
            CHECK.replace('print("CHECK FAILED: payload is broken")',
                          'print("CHECK FAILED in", __file__)'), encoding="utf-8")
        self.define(good_fault(expect={"signature": "CHECK FAILED in .*"}))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assertNotIn(str(self.root), self.verdict_row())
        self.assertIn("<tree>", self.verdict_row())


class CommittedFaults(unittest.TestCase):
    def test_every_committed_fault_loads_and_is_not_stale(self):
        faults = fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")
        self.assertGreater(len(faults), 0)
        for fault in faults:
            self.assertIsNone(fm.find_stale(fault, fm.REPO_ROOT), fault.id)

    def test_the_six_required_parser_faults_exist(self):
        ids = {fault.id for fault in fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")}
        required = {"parser-field-offset", "parser-read-be48-eight-bytes",
                    "parser-length-table-entry", "parser-truncated-frame-check-removed",
                    "parser-unknown-type-not-skipped", "parser-byte-order"}
        self.assertEqual(required - ids, set())


if __name__ == "__main__":
    unittest.main()
