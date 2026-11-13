#!/usr/bin/env python3
"""fuzz_report.py on a made-up run of three targets: a clean run is written with its row in the
run log, and each way a run can fall short is refused by name. Scratch goes under build/.

    python3 -B scripts/test_fuzz_report.py
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fuzz_report  # noqa: E402

REPO_ROOT = fuzz_report.REPO_ROOT
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
TARGETS = ("fuzz_a", "fuzz_b", "fuzz_c")
SYMBOLIZER = "/opt/project/llvm/bin/llvm-symbolizer"
BUILD_DONE = ("2026-01-02T03:00:13Z build done, check lock released; flags "
              "CMAKE_BUILD_TYPE=Debug CMAKE_CXX_FLAGS_DEBUG=-g -O1 ITCH_FUZZ=ON "
              "ITCH_SANITIZE=address,undefined -fsanitize=fuzzer; seconds 1800 a target, 3 at once; "
              f"ASAN_OPTIONS=external_symbolizer_path={SYMBOLIZER} "
              f"UBSAN_OPTIONS=external_symbolizer_path={SYMBOLIZER}")


def log_text(runs, seconds=1801):
    return ("INFO: Seed: 1\n#2\tINITED cov: 10 ft: 10 corp: 1/1b exec/s: 0 rss: 30Mb\n"
            f"Done {runs} runs in {seconds} second(s)\n"
            f"stat::number_of_executed_units: {runs}\n")


class FuzzReport(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name).resolve()
        self.root = self.dir / "repo"
        (self.root / "fuzz").mkdir(parents=True)
        (self.root / "fuzz" / "CMakeLists.txt").write_text(
            "".join(f"itch_add_fuzzer({name})\n" for name in TARGETS), encoding="utf-8")
        self.logs = self.dir / "logs"
        self.logs.mkdir()
        self.found = self.dir / "found"
        self.found.mkdir()
        (self.logs / "build.log").write_text(
            "-- The CXX compiler identification is Clang 23.1.4\n"
            f"-- Sanitizer tests: symbolized by {SYMBOLIZER}\n", encoding="utf-8")
        self.lines = {"first": f"2026-01-02T03:00:00Z commit {'a' * 40} tree /scratch/tree",
                      "build": BUILD_DONE, "done": "ALL DONE 2026-01-02T03:30:15Z"}
        self.targets = {}
        for number, name in enumerate(TARGETS, 1):
            self.set_target(name, executions=1000 * number)
        self.out = self.dir / "fuzz_30min_test.md"
        self.run_log = self.dir / "run_log.md"
        self.run_log.write_text("| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n",
                                encoding="utf-8")

    def set_target(self, name, executions=1000, code=0, crashes=0, start="2026-01-02T03:00:13Z",
                   end="2026-01-02T03:30:14Z", log=None):
        self.targets[name] = (f"{start} {end} {name} exit {code} executions {executions} "
                              f"crashes {crashes}")
        (self.logs / f"{name}.log").write_text(log if log is not None else log_text(executions),
                                               encoding="utf-8")

    def write_status(self):
        lines = [self.lines["first"], "2026-01-02T03:00:00Z build started, check lock taken",
                 self.lines["build"], *self.targets.values(), self.lines["done"]]
        status = self.dir / "status.txt"
        status.write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")
        return status

    def run_write(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fuzz_report.main(["write", "--status", str(self.write_status()),
                                     "--logs", str(self.logs), "--found", str(self.found),
                                     "--host", "macOS (M4 Max)", "--out", str(self.out),
                                     "--run-log", str(self.run_log), "--root", str(self.root)])
        return code, out.getvalue()

    def refused(self, code_word):
        code, output = self.run_write()
        self.assertEqual(code, 1, output)
        self.assertIn(f"fuzz_report: FAIL {code_word} ", output)
        self.assertFalse(self.out.exists(), "a refused run wrote its file")
        self.assertNotIn("fuzz-30min", self.run_log.read_text(encoding="utf-8"))
        return output

    def test_a_clean_run_is_written_with_its_row(self):
        code, output = self.run_write()
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn(f"- Commit: {'a' * 12}, the code fuzzed:", text)
        self.assertIn("- Result: 3 of 3 clean", text)
        self.assertIn("| `fuzz_b` | 03:00:13Z | 03:30:14Z | 1,801 | 2,000 | 0 | 0 | 0 |", text)
        self.assertIn("- Sanitizers: address,undefined, with -fsanitize=fuzzer", text)
        self.assertIn("toolchain's `llvm-symbolizer`", text)
        self.assertNotIn("/scratch/tree", text)
        self.assertNotIn(SYMBOLIZER, text)
        self.assertIn(fuzz_report.CLEAN, text)
        self.assertTrue(self.run_log.read_text(encoding="utf-8").endswith(
            "| fuzz-30min-20260102T030000Z | none: a check, not a measurement | aaaaaaaaaaaa | "
            "Darwin | published: results/fuzz_30min_test.md; 3 of 3 targets clean, "
            "1,800 s each |\n"))

    def test_a_run_logged_already_is_refused(self):
        self.assertEqual(self.run_write()[0], 0)
        self.out.unlink()
        self.assertIn("is already logged", self.run_write()[1])

    def test_a_target_missing_is_refused(self):
        del self.targets["fuzz_b"]
        self.assertIn("no status line for ['fuzz_b']", self.refused("target-missing"))

    def test_a_target_the_build_does_not_add_is_refused(self):
        self.set_target("fuzz_z")
        self.assertIn("['fuzz_z']", self.refused("target-missing"))

    def test_a_crash_is_refused(self):
        self.set_target("fuzz_c", crashes=1)
        self.assertIn("fuzz_c: 1 crashes", self.refused("crashes"))

    def test_an_exit_other_than_0_is_refused(self):
        self.set_target("fuzz_a", code=1)
        self.assertIn("fuzz_a: exit 1", self.refused("exit"))

    def test_a_run_shorter_than_1800_s_is_refused(self):
        self.lines["build"] = BUILD_DONE.replace("seconds 1800 a target", "seconds 1799 a target")
        self.assertIn("1799 s a target, under 1800", self.refused("short"))

    def test_a_target_whose_log_ran_under_1800_s_is_refused(self):
        self.set_target("fuzz_b", log=log_text(1000, seconds=1799))
        self.assertIn("gives 1799 s, under 1800", self.refused("short"))

    def test_a_target_whose_span_is_under_1800_s_is_refused(self):
        self.set_target("fuzz_a", end="2026-01-02T03:30:12Z")
        self.assertIn("1799 s from start to end", self.refused("short"))

    def test_a_status_file_without_all_done_is_refused(self):
        self.lines["done"] = "STOPPED signal TERM 2026-01-02T03:10:00Z"
        self.assertIn("STOPPED signal TERM", self.refused("no-all-done"))

    def test_a_log_with_an_error_line_is_refused(self):
        self.set_target("fuzz_a", log=log_text(1000) + "==1==ERROR: AddressSanitizer: x\n")
        self.refused("log")

    def test_a_log_that_ran_other_executions_is_refused(self):
        self.set_target("fuzz_a", log=log_text(999))
        self.assertIn("999 runs", self.refused("log"))

    def test_an_input_saved_is_refused(self):
        (self.found / "fuzz_c-crash-0123").write_bytes(b"x")
        self.assertIn("fuzz_c-crash-0123", self.refused("found"))

    def test_a_fuzzer_without_the_toolchains_symbolizer_is_refused(self):
        self.lines["build"] = BUILD_DONE.replace(f"UBSAN_OPTIONS=external_symbolizer_path="
                                                 f"{SYMBOLIZER}", "UBSAN_OPTIONS=")
        self.assertIn("UBSAN_OPTIONS", self.refused("symbolizer"))

    def test_a_symbolizer_other_than_the_one_beside_the_compiler_is_refused(self):
        self.lines["build"] = BUILD_DONE.replace(f"ASAN_OPTIONS=external_symbolizer_path="
                                                 f"{SYMBOLIZER}", "ASAN_OPTIONS=external_"
                                                 "symbolizer_path=/usr/bin/system-symbolizer")
        self.assertIn("ASAN_OPTIONS", self.refused("symbolizer"))

    def run_check(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fuzz_report.main(["check", str(path), "--root", str(self.root)])
        return code, out.getvalue()

    def test_check_reads_a_written_file_again(self):
        self.assertEqual(self.run_write()[0], 0)
        self.assertEqual(self.run_check(self.out), (0, "fuzz_report: ok, 1 file(s)\n"))
        text = self.out.read_text(encoding="utf-8")
        for old, new, code in (("| 2,000 | 0 | 0 | 0 |", "| 2,000 | 0 | 1 | 0 |", "crashes"),
                               ("| 2,000 | 0 | 0 | 0 |", "| 2,000 | 2 | 0 | 0 |", "exit"),
                               ("| 1,801 | 2,000 |", "| 1,799 | 2,000 |", "short"),
                               ("- Result: 3 of 3 clean", "- Result: 4 of 4 clean", "status")):
            with self.subTest(code=code):
                self.out.write_text(text.replace(old, new), encoding="utf-8")
                found = self.run_check(self.out)
                self.assertEqual(found[0], 1, found[1])
                self.assertIn(f"fuzz_report: FAIL {code} ", found[1])
        lines = [line for line in text.splitlines() if not line.startswith("| `fuzz_c`")]
        self.out.write_text("\n".join(lines), encoding="utf-8")
        self.assertIn("FAIL target-missing", self.run_check(self.out)[1])

    def test_the_published_files_pass_check(self):
        published = sorted((REPO_ROOT / "results").glob("fuzz_*.md"))
        self.assertTrue(published, "no fuzz results file is published")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fuzz_report.main(["check", *map(str, published)])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
