#!/usr/bin/env python3
"""Tests for scripts/check.sh: a stage that cannot do its work fails, and says why.

Each test copies check.sh into a scratch repo whose tools are stubs on PATH (cmake, ctest,
ninja, clang-format, clang-tidy, a compiler and python3), so no stage builds or runs anything
real. The ctest stub lists the tests the test gives it and records every other call; the
python3 stub records its arguments and exits 0.

    python3 -B scripts/test_check_sh.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHECK_SH = Path(__file__).resolve().parent / "check.sh"

ORACLE_TESTS = ["test_book", "test_alloc", "test_node_book", "test_oracle_cli", "corpus_fuzz_book"]
BENCH_TESTS = ["test_bench_platform", "test_bench_platform_cross_core", "test_bench_replay",
               "test_bench_cli",
               "test_bench_backtest_cli", "test_bench_counts_cli", "test_bench_variants_cli",
               "test_bench_spsc_cli", "test_bench_kernels_cli", "test_bench_udp_cli",
               "test_bench_walks_cli", "test_walk_counts"]
DEMO_TESTS = ["test_demo_replay", "test_demo_cli", "test_alloc"]

# One file for each pattern the bench stage reads.
BENCH_RESULTS = ["bench_a.md", "backtest_a.md", "spsc_a.md", "alloc_a.md", "walks_ladder_a.md",
                 "chase_debian.md", "counters_by_type_a.md", "counters_by_age_a.md",
                 "counters_zen1_events_debian.md", "flamegraph_a.md", "profile_a.md",
                 "lookup_a.md", "prefetch_a.md", "dispatch_a.md", "layout_a.md",
                 "structure_a.md", "window_a.md", "simd_asm_a.md", "simd_a.md", "udp_a.md",
                 "walks_counts.md"]

CTEST_STUB = """#!/bin/sh
for arg in "$@"; do
  if [ "$arg" = -N ]; then
    echo "Test project $PWD"
    n=0
    while read -r name; do
      n=$((n + 1))
      printf '  Test #%d: %s\\n' "$n" "$name"
    done < "$STUB_DIR/ctest_names"
    echo "Total Tests: $n"
    exit 0
  fi
done
echo "ctest $*" >> "$STUB_DIR/calls"
"""

RECORDING_STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$STUB_DIR/calls"
case "$1" in --version) echo "$(basename "$0") version 1.0" ;; esac
"""


class CheckSh(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="test_check_sh_"))
        self.addCleanup(shutil.rmtree, self.scratch)
        self.repo = self.scratch / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "results").mkdir()
        shutil.copy(CHECK_SH, self.repo / "scripts" / "check.sh")
        self.stubs = self.scratch / "stubs"
        self.stubs.mkdir()
        for tool in ("cmake", "ninja", "clang-format", "clang-tidy", "c++", "python3"):
            self.stub(tool, RECORDING_STUB)
        self.stub("ctest", CTEST_STUB)
        self.browser = self.stub("browser", "#!/bin/sh\nexit 0\n")
        self.known_tests(ORACLE_TESTS + BENCH_TESTS + DEMO_TESTS)

    def stub(self, name, text):
        path = self.stubs / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def known_tests(self, names):
        (self.stubs / "ctest_names").write_text("".join(f"{n}\n" for n in names),
                                                encoding="utf-8")

    def results(self, names):
        for name in names:
            (self.repo / "results" / name).write_text("x\n", encoding="utf-8")

    def check(self, *args, **env):
        environment = {"PATH": f"{self.stubs}:/usr/bin:/bin", "STUB_DIR": str(self.stubs),
                       "HOME": str(self.scratch), "CXX": str(self.stubs / "c++")}
        environment.update(env)
        run = subprocess.run(["bash", str(self.repo / "scripts" / "check.sh"), *args],
                             env=environment, capture_output=True, text=True, timeout=60)
        return run

    def log(self, stage):
        return (self.repo / "build" / "check-logs" / f"{stage}.log").read_text(encoding="utf-8")

    def calls(self):
        path = self.stubs / "calls"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_compiler_that_is_not_there_fails_the_toolchain_stage(self):
        run = self.check("--only", "toolchain", CXX="/nonexistent/c++")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("check: FAILED, 1 of 1 stages", run.stdout)
        self.assertIn("the compiler /nonexistent/c++ does not run", self.log("toolchain"))

    def test_the_toolchain_stage_prints_the_first_line_of_each_version(self):
        run = self.check("--only", "toolchain")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("c++ version 1.0\ncmake version 1.0\n", self.log("toolchain"))

    def test_a_stage_that_names_a_test_ctest_does_not_know_fails(self):
        self.known_tests([n for n in ORACLE_TESTS if n != "test_node_book"])
        run = self.check("--only", "oracle")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("ctest knows no test named test_node_book", self.log("oracle"))
        self.assertNotIn("ctest --preset release -R", self.calls())

    def test_a_stage_runs_exactly_the_tests_it_names(self):
        run = self.check("--only", "oracle")
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("ctest --preset release -R ^(test_book|test_alloc|test_node_book|"
                      "test_oracle_cli|corpus_fuzz_book)$\n", self.calls())

    def test_a_test_name_is_matched_whole(self):
        self.known_tests(["test_book_variants", "test_alloc", "test_node_book",
                          "test_oracle_cli", "corpus_fuzz_book"])
        run = self.check("--only", "oracle")
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("ctest knows no test named test_book\n", self.log("oracle"))

    def test_a_results_pattern_that_matches_nothing_fails_the_bench_stage(self):
        for missing in ("udp_a.md", "chase_debian.md", "simd_asm_a.md", "profile_a.md"):
            with self.subTest(missing=missing):
                for path in (self.repo / "results").glob("*.md"):
                    path.unlink()
                self.results([n for n in BENCH_RESULTS if n != missing])
                run = self.check("--only", "bench")
                self.assertEqual(run.returncode, 1, run.stdout)
                pattern = {"udp_a.md": "udp_*.md", "chase_debian.md": "chase_debian.md",
                           "simd_asm_a.md": "simd_asm_*.md", "profile_a.md": "profile_*.md"}
                self.assertIn(f"no results file matches results/{pattern[missing]}",
                              self.log("bench"))

    def test_the_bench_stage_checks_each_results_file_once(self):
        self.results(BENCH_RESULTS)
        run = self.check("--only", "bench")
        self.assertEqual(run.returncode, 0, run.stdout + self.log("bench"))
        calls = self.calls()
        self.assertEqual(calls.count("results/simd_asm_a.md"), 1, calls)
        self.assertIn("python3 -B scripts/simd_asm_audit.py --check results/simd_asm_a.md\n",
                      calls)
        self.assertIn("python3 -B scripts/simd_report.py --check results/simd_a.md\n", calls)
        self.assertIn("python3 -B scripts/udp_report.py --check results/udp_a.md\n", calls)
        self.assertIn("ctest --preset release -R ^(" + "|".join(BENCH_TESTS) + ")$\n", calls)

    def test_the_demo_stage_fails_without_a_browser_and_says_how_to_name_one(self):
        run = self.check("--only", "demo")
        self.assertEqual(run.returncode, 1, run.stdout)
        log = self.log("demo")
        self.assertIn("Set ITCH_BROWSER to a Chrome or Chromium binary", log)
        self.assertIn("--without-browser", log)
        self.assertEqual(self.calls(), "", "the stage built or ran something first")

    def test_a_browser_that_does_not_run_fails_the_demo_stage(self):
        run = self.check("--only", "demo", ITCH_BROWSER=str(self.scratch / "no-browser"))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn("ITCH_BROWSER names no program that runs", self.log("demo"))

    def test_without_a_browser_by_request_the_summary_says_partial(self):
        run = self.check("--only", "demo", "--without-browser")
        self.assertEqual(run.returncode, 0, run.stdout)
        summary = [line for line in run.stdout.splitlines() if line.startswith("demo ")]
        self.assertEqual(len(summary), 2, run.stdout)
        self.assertRegex(summary[1], r"^demo +partial .*  partial: the page was not opened$")
        self.assertIn("check: all stages passed, in part only: demo", run.stdout)
        self.assertNotIn("check: all stages passed\n", run.stdout)
        self.assertNotIn("--browser", self.calls())

    def test_with_a_browser_the_page_is_opened_and_the_stage_is_ok(self):
        run = self.check("--only", "demo", ITCH_BROWSER=str(self.browser))
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn(f"--browser {self.browser}\n", self.calls())
        self.assertNotIn("partial", run.stdout)
        self.assertIn("check: all stages passed\n", run.stdout)


if __name__ == "__main__":
    unittest.main()
