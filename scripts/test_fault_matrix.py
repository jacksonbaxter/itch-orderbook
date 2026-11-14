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
import re
import signal
import subprocess
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
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
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

    def test_a_fault_for_another_system_is_listed_and_not_run(self):
        # The fault would be undetected if it ran; on another system it is not run.
        self.define(good_fault(),
                    good_fault(id="linux", system="Linux",
                               mutations=[{"file": "notes.txt", "old": "twin twin", "new": "x"}]))
        self.assertEqual(self.matrix("--system", "Darwin"), 0, self.output)
        self.assert_verdict(fm.DETECTED)
        self.assert_verdict(fm.OTHER_HOST, "linux")
        report = (self.root / "report.md").read_text(encoding="utf-8")
        self.assertIn("1 of 1 detected", report)
        self.assertIn("Not run on this host: 1, for Linux only", report)
        self.assertIn("1 for another system not run", self.output)

    def test_a_run_in_which_no_fault_ran_fails(self):
        self.define(good_fault(id="linux", system="Linux"))
        self.assertEqual(self.matrix("--system", "Darwin"), 1, self.output)
        self.assert_verdict(fm.OTHER_HOST, "linux")
        self.assertIn("fault matrix: FAIL no fault ran", self.output)

    def test_a_later_edit_an_earlier_one_disturbed_is_stale(self):
        # Each old text occurs once in the file, but the second is gone once the first is made.
        self.define(good_fault(mutations=[
            {"file": "payload.txt", "old": "good", "new": "bad"},
            {"file": "payload.txt", "old": "good\n", "new": "worse\n"}]))
        self.assertEqual(self.matrix(), 1, self.output)
        self.assert_verdict(fm.STALE)
        self.assertIn("occurs 0 times, not once, after the edits before it", self.output)

    def test_a_later_edit_of_the_text_an_earlier_one_made_is_not_stale(self):
        self.define(good_fault(mutations=[
            {"file": "payload.txt", "old": "good", "new": "nearly so"},
            {"file": "payload.txt", "old": "nearly so", "new": "bad"}]))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assert_verdict(fm.DETECTED)

    def test_an_input_after_a_mutation_of_its_file_is_tried_on_the_mutated_text(self):
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "good", "new": "gone"}],
                               inputs=[{"file": "payload.txt", "op": "replace_text",
                                        "old": "good", "new": "bad"}]))
        # The input's old text is gone once the mutation is made, so the fault is stale: had it
        # not been found so, inject() would stop the run on it.
        (fault,) = fm.load_faults(self.root / "faults")
        self.assertIn("payload.txt: replace_text cannot apply", fm.find_stale(fault, self.root) or "")
        self.assertEqual(self.matrix(), 1, self.output)
        self.assert_verdict(fm.STALE)

    def test_a_fault_for_this_system_runs(self):
        self.define(good_fault(id="linux", system="Linux",
                               mutations=[{"file": "notes.txt", "old": "twin twin", "new": "x"}]))
        self.assertEqual(self.matrix("--system", "Linux"), 1)
        self.assert_verdict(fm.UNDETECTED, "linux")

    def test_a_fault_for_another_system_is_still_checked_for_staleness(self):
        self.define(good_fault(system="Linux",
                               mutations=[{"file": "payload.txt", "old": "gone", "new": "x"}]))
        self.assertEqual(self.matrix("--system", "Darwin"), 1)
        self.assert_verdict(fm.STALE)

    def test_an_unknown_system_is_a_definition_error(self):
        self.define(good_fault(system="Plan9"))
        self.assertEqual(self.matrix(), 2)

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

    def test_a_control_runs_on_the_faults_own_tree_and_not_a_wider_one(self):
        self.define(good_fault(), area="wide")
        narrow = [name for name in TREE if name != "payload.txt"]
        self.define(good_fault(id="narrow", expect={"signature": "CHECK FAILED"},
                               mutations=[{"file": "notes.txt", "old": "twin twin", "new": "t"}]),
                    area="narrow", tree=narrow)
        self.assertEqual(self.matrix(), 1)
        self.assertIn("exited 1 on the clean tree", self.output)
        self.assert_verdict(fm.NOT_RUN, "narrow")

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
        # The child dies by SIGTERM, not os.abort(), which macOS reports as a crash of Python.app
        # with a dialog. SIGTERM is not the SIGKILL a timeout gets, so the exit column tells the
        # two apart: a timeout has none.
        crash = [{"file": "payload.txt", "old": "good", "new": "good crash"}]
        self.define(good_fault(mutations=crash))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.WRONG_SIGNATURE)
        self.assertIn(f"| {-signal.SIGTERM} |", self.verdict_row())

        (self.root / "check.py").write_text(
            CHECK.replace('import os\n', 'import os\n    print("planted crash", flush=True)\n'),
            encoding="utf-8")
        self.define(good_fault(mutations=crash, expect={"signature": "planted crash"}))
        self.assertEqual(self.matrix(), 0, self.output)
        self.assert_verdict(fm.DETECTED)
        self.assertIn(f"| {-signal.SIGTERM} |", self.verdict_row())

    def test_the_wrong_exit_code_is_not_a_detection(self):
        self.define(good_fault(expect={"signature": "CHECK FAILED", "exit": 2}))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.WRONG_SIGNATURE)

    def test_a_check_that_never_ends_is_a_timeout(self):
        self.define(good_fault(mutations=[{"file": "payload.txt", "old": "good", "new": "good hang"}],
                               timeout=1))
        self.assertEqual(self.matrix(), 1)
        self.assert_verdict(fm.TIMEOUT)
        self.assertIn(f"| {fm.TIMEOUT} |  |", self.verdict_row())

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


# Each libFuzzer search among the faults, by target: the fewest executions it may run, and the
# slowest rate, in executions a second, that its timeout must allow for. A search with a budget
# in seconds finds less on a busy machine, and libFuzzer's corpus reload, once a second, re-runs
# files at a point the clock sets and so changes the path of the search: the final full check at
# 6508b0d missed simd-fuzz-kernels-oracle that way. A count of runs with the reload off makes the
# count at which a search finds its fault independent of the machine's load (shown for one build
# on one Mac, docs/NOTES.md). At this code, each floor is no fewer runs than any control of its
# old budget in seconds ran; older fuzz_book code ran about 20,800 in 121 s. Over the largest
# count at which an earlier search, under the old command, found its fault (the last progress
# line before the failure, so a lower bound), each floor is: fuzz_book 10.3 times 486,
# fuzz_decode 391 times 1,788, fuzz_lookahead 10.0 times 29,979, fuzz_reader 37.2 times 40,369,
# fuzz_packets about 8.2 times 1,221,985 (the gate's run at 6508b0d, after this floor was set),
# and fuzz_kernels 4.9 times 205,000 and 4.0 times the gate's miss at 251,901, since it slows to
# about 1,000 runs a second as its inputs grow. Each rate is below both averages measured for
# the target: its control at this floor in the locked run at 9cef38c, and its 30-minute run
# (fuzz_book 38.8 and 37.4, fuzz_decode 21,875 and 20,990, fuzz_kernels 2,674 and 1,165,
# fuzz_lookahead 771 and 752, fuzz_packets 119,047 and 101,761, fuzz_reader 30,612 and 25,972).
FUZZ_SEARCHES = {
    "fuzz_book": (5_000, 30),
    "fuzz_decode": (700_000, 20_000),
    "fuzz_kernels": (1_000_000, 1_000),
    "fuzz_lookahead": (300_000, 700),
    "fuzz_packets": (10_000_000, 80_000),
    "fuzz_reader": (1_500_000, 25_000),
}
# The only flags a search may take, each at most once. A value of None is checked in the test
# (-seed at least 1, since 0 asks libFuzzer to make one up; -runs at or above the floor); any
# other flag fails, so one that reads the clock or unfixes the seed (-max_total_time, -timeout,
# -entropic_scale_per_exec_time, -jobs, -fork, ...) cannot slip in, nor a dictionary.
SEARCH_FLAGS = {
    "-seed": None,
    "-runs": None,
    "-reload": "0",
    "-print_final_stats": "1",
    "-artifact_prefix": "{build}/fuzz/artifacts/",
}
REQUIRED_SEARCH_FLAGS = {"-seed", "-runs", "-reload"}

class CommittedFaults(unittest.TestCase):
    def test_every_committed_fault_loads_and_is_not_stale(self):
        faults = fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")
        self.assertGreater(len(faults), 0)
        for fault in faults:
            self.assertIsNone(fm.find_stale(fault, fm.REPO_ROOT), fault.id)

    def test_the_matrix_area_copies_every_file_a_fault_injects_into(self):
        # The test above runs again in the matrix area's copy, as that area's control, and a
        # file a fault edits or reads that the copy lacks fails it only hours into a full run.
        faults = fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")
        trees = {fault.tree for fault in faults if fault.area == "matrix"}
        self.assertEqual(len(trees), 1)
        tree = trees.pop()
        for fault in faults:
            for name in [m["file"] for m in fault.mutations] + [i["file"] for i in fault.inputs]:
                inside = any(name == entry or name.startswith(entry.rstrip("/") + "/")
                             for entry in tree)
                self.assertTrue(inside, f"{fault.id}: {name} is not in the matrix area's tree")

    def test_every_fuzz_search_counts_executions_and_reads_no_clock(self):
        # Closed, like the run log's grammar: every word of a fault's command that names a fuzz
        # target (a fuzz/fuzz_*.cpp) must be in one of three forms, and anything else fails.
        #   A search: the command is {build}/fuzz/<target>, the flags SEARCH_FLAGS allows, then
        #   exactly the output corpus {build}/fuzz/corpus/<target> and the seeds
        #   {tree}/fuzz/corpus/<target>. It needs one -seed of at least 1, -runs at or above
        #   the floor, -reload=0, and a timeout the count fits in at a third of the target's
        #   slowest rate, so a slow machine takes longer and never turns a find into undetected.
        #   A replay: ctest --preset <p> -R ^corpus_<target>$, the standalone runner, which runs
        #   the target once on each committed seed with no mutation, so no clock decides what runs.
        #   A corpus path, {build} or {tree}/fuzz/corpus/<target>[/<file>]: data, not a binary.
        # A search wrapped in a shell, run under another program or spelled another way is none
        # of these, and fails.
        targets = {path.stem for path in (fm.REPO_ROOT / "fuzz").glob("fuzz_*.cpp")}
        self.assertGreaterEqual(len(targets), 9)
        mention = re.compile(r"fuzz_[a-z0-9]+")
        corpus = re.compile(r"\{(?:build|tree)\}/fuzz/corpus/(fuzz_[a-z0-9]+)(?:/[^/]+)?")
        searches = 0
        for fault in fm.load_faults(fm.REPO_ROOT / "scripts" / "faults"):
            command = fault.command
            replay = (len(command) == 5 and command[:2] == ["ctest", "--preset"]
                      and command[3] == "-R")
            search = None
            for at, word in enumerate(command):
                for name in set(mention.findall(word)) & targets:
                    if at == 0 and word == f"{{build}}/fuzz/{name}":
                        search = name
                    elif not (corpus.fullmatch(word) and corpus.fullmatch(word).group(1) == name
                              or replay and at == 4 and word == f"^corpus_{name}$"):
                        self.fail(f"{fault.id}: {word!r} names fuzz target {name} in no form "
                                  "this rule knows")
            if search is None:
                continue
            searches += 1
            self.assertIn(search, FUZZ_SEARCHES, f"{fault.id}: no floor for {search} here")
            floor, rate = FUZZ_SEARCHES[search]
            flags = [word for word in command[1:] if word.startswith("-")]
            self.assertEqual([word for word in command[1:] if not word.startswith("-")],
                             [f"{{build}}/fuzz/corpus/{search}", f"{{tree}}/fuzz/corpus/{search}"],
                             f"{fault.id}: a search reads its output corpus and its seeds, no more")
            values = {}
            for flag in flags:
                name, _, value = flag.partition("=")
                self.assertIn(name, SEARCH_FLAGS,
                              f"{fault.id}: {flag} is not a flag a fuzz search may take")
                self.assertNotIn(name, values, f"{fault.id}: {name} given twice")
                values[name] = value
            self.assertEqual(REQUIRED_SEARCH_FLAGS - set(values), set(),
                             f"{fault.id}: a search needs -seed, -runs and -reload")
            for name, fixed in SEARCH_FLAGS.items():
                if fixed is not None and name in values:
                    self.assertEqual(values[name], fixed, f"{fault.id}: {name} must be {fixed}")
            self.assertRegex(values["-seed"], r"^[1-9][0-9]*$",
                             f"{fault.id}: -seed must be a fixed seed of at least 1")
            self.assertRegex(values["-runs"], r"^[0-9]+$", f"{fault.id}: -runs=N, N a count")
            runs = int(values["-runs"])
            self.assertGreaterEqual(runs, floor, f"{fault.id}: -runs below {search}'s floor")
            self.assertGreaterEqual(fault.timeout, 3 * runs / rate,
                                    f"{fault.id}: timeout too short for -runs={runs}")
        # Seven when this test was written; every one added since is held to the same rule.
        self.assertGreaterEqual(searches, 7)

    def test_a_tool_that_only_fails_detects_no_tool_fault(self):
        # Each mm-select and mm-cli fault breaks one rule of its tool; a tool that just exits 1
        # breaks every case, and its output must match none of their signatures.
        faults = [fault for fault in fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")
                  if fault.id.startswith(("mm-select-", "mm-cli-"))]
        # Ten when this test was written; every one added since is held to the same rule.
        self.assertGreaterEqual(len(faults), 10)
        broken = "/usr/bin/false" if Path("/usr/bin/false").exists() else "/bin/false"
        with tempfile.TemporaryDirectory(dir=SCRATCH) as scratch:
            outputs = {}
            oracle = str(fm.REPO_ROOT / "tests" / "fixtures" / "oracle")
            for prefix, script, extra in (("mm-select-", "test_select_cli.py", scratch),
                                          ("mm-cli-", "test_mm_cli.py", oracle)):
                done = subprocess.run([sys.executable, "-B", str(fm.REPO_ROOT / "tests" / script),
                                       broken, extra], capture_output=True, text=True,
                                      check=False, timeout=120)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                outputs[prefix] = done.stdout + done.stderr
        for fault in faults:
            output = outputs["mm-select-" if fault.id.startswith("mm-select-") else "mm-cli-"]
            self.assertNotEqual(fm.judge(fault, 1, output)[0], fm.DETECTED, fault.id)

    def test_the_six_required_parser_faults_exist(self):
        ids = {fault.id for fault in fm.load_faults(fm.REPO_ROOT / "scripts" / "faults")}
        required = {"parser-field-offset", "parser-read-be48-eight-bytes",
                    "parser-length-table-entry", "parser-truncated-frame-check-removed",
                    "parser-unknown-type-not-skipped", "parser-byte-order"}
        self.assertEqual(required - ids, set())


if __name__ == "__main__":
    unittest.main()
