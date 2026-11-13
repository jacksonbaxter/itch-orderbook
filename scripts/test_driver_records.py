#!/usr/bin/env python3
"""What each driver writes down about a run, with no benchmark run: its row in the run log,
and stamp_load.py's outcome row after it.

Each driver's main() runs with its run() replaced, so that what is tested is the row main()
writes for what run() did, not the run itself.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alloc_driver  # noqa: E402
import backtest_driver  # noqa: E402
import bench_driver  # noqa: E402
import check_results  # noqa: E402
import spsc_driver  # noqa: E402
import stamp_load  # noqa: E402
import walk_driver  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "bench_results"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
PENDING = (FIXTURES / "pending_mac.md").read_text(encoding="utf-8")
STDERR = (FIXTURES / "measure.stderr").read_text(encoding="utf-8")
RUN_ID = "P-0999-20261031T100005Z"


def rows(log):
    """The run log's rows, as lists of cells."""
    if not log.is_file():
        return []
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.startswith("| P-")]


def quiet(function, *args):
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        return function(*args)


class StampRows(unittest.TestCase):
    """r5-3: stamp_load.py appends the outcome row; the driver's row only says the file is
    written, pending the stamp."""

    def stamp(self, directory, stderr_text, results_text=PENDING):
        results = directory / "bench_fixture.md"
        stderr = directory / "stderr.txt"
        log = directory / "run_log.md"
        if not results.is_file():
            results.write_text(results_text, encoding="utf-8")
        stderr.write_text(stderr_text, encoding="utf-8")
        code = quiet(stamp_load.main, ["--results", str(results), "--stderr", str(stderr),
                                       "--run-log", str(log)])
        return code, results, log

    def test_a_stamped_file_gets_its_published_row(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            code, results, log = self.stamp(Path(directory), STDERR)
            self.assertEqual(code, 0)
            found = rows(log)
            self.assertEqual(len(found), 1, found)
            self.assertEqual(found[0][:4], [RUN_ID, "P-0999", "abcdef012345 (clean)", "Darwin"])
            self.assertTrue(found[0][4].startswith("published: bench_fixture.md, its load "
                                                   "lines stamped"), found)
            self.assertNotIn(check_results.PENDING, results.read_text(encoding="utf-8"))
            # Stamped once: a second stamp is refused, and writes no second row.
            code, _, log = self.stamp(Path(directory), STDERR)
            self.assertEqual(code, 1)
            self.assertEqual(len(rows(log)), 1)

    def test_a_refused_stamp_gets_its_withdrawn_row_and_stays_withdrawn(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            broken = STDERR.replace("at=2026-10-31T10:00:10Z", "at=2026-10-31T10:00:06Z")
            code, results, log = self.stamp(Path(directory), broken)
            self.assertEqual(code, 1)
            self.assertEqual(results.read_text(encoding="utf-8"), PENDING)
            self.assertEqual(len(rows(log)), 1)
            outcome = rows(log)[0][4]
            self.assertTrue(outcome.startswith("withdrawn: bench_fixture.md: "
                                               "scripts/stamp_load.py refused it: "
                                               "load-line-invalid"), outcome)
            self.assertIn("nothing from it is published", outcome)
            # Once withdrawn, the same run is never stamped published after it.
            code, results, log = self.stamp(Path(directory), STDERR)
            self.assertEqual(code, 1)
            self.assertEqual(len(rows(log)), 1)
            self.assertEqual(results.read_text(encoding="utf-8"), PENDING)

    def test_a_debian_file_is_logged_as_linux(self):
        self.assertEqual(stamp_load.system_of("debian"), "Linux")
        self.assertEqual(stamp_load.system_of(check_results.MAC_HOST), "Darwin")


def driver_argv(name, root):
    """The least each driver's parse_args takes, its raw folder and run log in root."""
    common = ["--probe", "x", "--input", "x", "--prediction", "P-0999", "--raw-dir",
              str(root / "raw"), "--out", str(root / "out.md"), "--run-log",
              str(root / "run_log.md")]
    return {"bench_driver": ["--bench", "x", *common],
            "backtest_driver": ["--bench", "x", "--workload", "quoting", "--date", "20191230",
                                *common],
            "walk_driver": ["--bench", "x", "--walks", "x", "--date", "20191230", *common],
            "spsc_driver": ["--suite", "queues", "--bench", "x", *common],
            "alloc_driver": ["--bench", "x", "--alloc-tool", "x", "--compare-tool", "x",
                             *common]}[name]


PENDING_DRIVERS = {"bench_driver": bench_driver, "backtest_driver": backtest_driver,
                   "walk_driver": walk_driver, "spsc_driver": spsc_driver,
                   "alloc_driver": alloc_driver}


def run_main(name, root, run):
    """The driver's main() with run() replaced by `run`, on Linux so that no Mac sampler
    starts, and the commit fixed. Its exit code."""
    module = PENDING_DRIVERS[name]
    with mock.patch.object(module, "run", run), \
            mock.patch.object(bench_driver, "git_commit", lambda root, dirty: "abc123 (clean)"), \
            mock.patch("platform.system", lambda: "Linux"):
        return quiet(module.main, driver_argv(name, root))


class PendingRows(unittest.TestCase):
    def test_no_driver_writes_published_before_the_stamp(self):
        """r5-3: bench, backtest, walk, spsc and alloc leave their load lines pending, so their
        row says written, pending the load stamp, and names the files."""
        for name in PENDING_DRIVERS:
            with self.subTest(driver=name), \
                    tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
                root = Path(directory)
                out = root / "out.md"
                written = [out] if name == "bench_driver" else out
                code = run_main(name, root, lambda *args, **kwargs: written)
                self.assertEqual(code, 0)
                found = rows(root / "run_log.md")
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0][4].startswith(f"{bench_driver.PENDING_ROW}: out.md;"),
                                found[0][4])
                self.assertNotIn("published:", found[0][4].split(";")[0])


if __name__ == "__main__":
    unittest.main()
