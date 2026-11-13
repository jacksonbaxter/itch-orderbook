#!/usr/bin/env python3
"""What each driver writes down about a run, with no benchmark run: its row in the run log,
and stamp_load.py's outcome row after it.

Each driver's main() runs with its run() replaced, so that what is tested is the row main()
writes for what run() did, not the run itself.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import types
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
import simd_driver  # noqa: E402
import stamp_load  # noqa: E402
import udp_driver  # noqa: E402
import walk_driver  # noqa: E402
import wasm_bench  # noqa: E402

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


def broken(*args, **kwargs):
    """A run() that dies as r5-8's fake bench_replay made bench_driver die: on a record whose
    warm-up section is null."""
    record = {"warmup": None}
    return record["warmup"]["messages"]


# Each driver's whole argument list, and the function its main() calls to do the run.
OTHER_DRIVERS = {
    "simd_driver": (simd_driver, "run", lambda root: [
        "--experiment", "framing", "--prediction", "P-0601", "--raw-dir", str(root / "raw"),
        "--input", "x", "--dataset", "d", "--dataset-sha256", "0" * 64, "--bench", "x",
        "--probe", "x", "--measure", "x", "--run-log", str(root / "run_log.md")]),
    "udp_driver": (udp_driver, "run_step", lambda root: [
        "--step", "ceiling", "--prediction", "P-0901", "--raw-dir", str(root / "raw"),
        "--input", "x", "--dataset", "d", "--dataset-sha256", "0" * 64, "--bench", "x",
        "--probe", "x", "--measure", "x", "--run-log", str(root / "run_log.md")]),
    "wasm_bench": (wasm_bench, "run", lambda root: [
        "native", "--bench", "x", "--probe", "x", "--prediction", "P-0401", "--raw-dir",
        str(root / "raw"), "--run-log", str(root / "run_log.md")]),
}


def dies_unexpectedly(name):
    """r5-8: the driver promises a row for every run, whatever became of it, so an exception
    nothing expected, a TypeError here, still gets its row, and exit 1."""

    def test(self):
        checked = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            try:
                if name in PENDING_DRIVERS:
                    code = run_main(name, root, broken)
                else:
                    module, function, argv = OTHER_DRIVERS[name]
                    with mock.patch.object(module, function, broken), \
                            mock.patch.object(bench_driver, "git_commit",
                                              lambda root, dirty: "abc123 (clean)"), \
                            mock.patch("subprocess.run", lambda *a, **k: checked), \
                            mock.patch("platform.system", lambda: "Linux"):
                        code = quiet(module.main, argv(root))
            except TypeError as error:
                self.fail(f"{name} let {error!r} out with no row")
            found = rows(root / "run_log.md")
        self.assertEqual(code, 1)
        self.assertEqual(len(found), 1, found)
        self.assertIn("failed: TypeError(", found[0][4])
        self.assertEqual(found[0][2], "abc123 (clean)")

    test.__doc__ = dies_unexpectedly.__doc__
    return test


class FailureRows(unittest.TestCase):
    """One test a driver, each named after it."""


for _name in [*PENDING_DRIVERS, *OTHER_DRIVERS]:
    setattr(FailureRows, f"test_{_name}_logs_a_run_that_dies_unexpectedly",
            dies_unexpectedly(_name))

BENCH_BASE = ["--bench", "x", "--probe", "x", "--input", "data/day.itch", "--prediction",
              "P-0999", "--raw-dir", "x", "--out", "results/bench_{design}.md"]


class Commands(unittest.TestCase):
    """r5-4: the Commands line records every argument that reaches a child process or
    changes the file."""

    def test_bench_driver_shows_every_argument_that_ran(self):
        plain = bench_driver.relative_command(bench_driver.parse_args(BENCH_BASE))
        for flag in ("--sibling", "--orders", "--levels", "--bench-arg", "--title", "--note"):
            self.assertNotIn(flag, plain)
        command = bench_driver.relative_command(bench_driver.parse_args(
            [*BENCH_BASE, "--core", "4", "--sibling", "5", "--orders", "1000", "--levels", "64",
             "--bench-arg=--shadow", "--bench-arg", "7", "--title", "Design {design}, a day",
             "--note", "A note."]))
        self.assertIn(" --core 4 --sibling 5 --orders 1000 --levels 64 --bench-arg=--shadow "
                      "--bench-arg=7 --title 'Design {design}, a day' --note 'A note.'", command)

    def test_backtest_driver_shows_every_argument_that_ran(self):
        base = ["--bench", "x", "--probe", "x", "--input", "x", "--workload", "quoting",
                "--date", "20191230", "--prediction", "P-0103", "--raw-dir", "x", "--out", "x"]
        plain = backtest_driver.relative_command(backtest_driver.parse_args(base))
        for flag in ("--bench-arg", "--sampler-cpu", "--quiet-before", "--quiet-wait",
                     "--most-in-a-row", "--most-withdrawn"):
            self.assertNotIn(flag, plain)
        command = backtest_driver.relative_command(backtest_driver.parse_args(
            [*base, "--core", "4", "--sibling", "5", "--sampler-cpu", "1", "--quiet-before", "5",
             "--quiet-wait", "30", "--most-in-a-row", "2", "--most-withdrawn", "9",
             "--bench-arg=--symbol", "--bench-arg=AAPL"]))
        self.assertIn(" --core 4 --sibling 5 --sampler-cpu 1 --quiet-before 5 --quiet-wait 30 "
                      "--most-in-a-row 2 --most-withdrawn 9 --bench-arg=--symbol "
                      "--bench-arg=AAPL", command)


GOOD_MAC = (FIXTURES / "good_mac.md").read_text(encoding="utf-8")
HEADER = check_results.parse_header(GOOD_MAC)
MACHINE = check_results.section(GOOD_MAC, "machine_check output").strip().strip("`").strip()
# The stub label spares a fixture file the book rules, which records this small cannot meet.
STUB_NOTE = ["--note", check_results.STUB_LABEL]


def raw_record(design=None, rate=100.0, untimed=False):
    """A record as bench_replay writes one: the stub's, or a design's timed or untimed one."""
    header = {name: HEADER[name] for name in bench_driver.HEADER_ORDER if name in HEADER}
    if design:
        header["design"] = f"{design}: a fixture"
    return {
        "header": header,
        "warmup": {"messages": 10},
        "throughput": {"messages": 10, "seconds": 0.1, "messages_per_s": rate},
        "latency_pass": {"messages": 10, "seconds": 0.15, "messages_per_s": rate / 1.5},
        "instrumentation_ratio": 1.5,
        "latency": {"percentiles": [{"percentile": 50, "value_ns": None},
                                    {"percentile": 99, "value_ns": 500.0}],
                    "mean_ns": 40.0, "max_ns": 900.0, "samples": 10},
        "counters": {"status": [{"name": "cycles", "state": "measured", "value": 5,
                                 "ratio": 1.0, "reason": ""}],
                     "agreement": "x", "per_message_published": False, "per_message": []},
        "timer": {"quantum_ns": 41.667, "pair_ns": 5.0, "floor_ns": 166.0,
                  "calibrated_hz": 24e6},
        "pinned": False, "migrations": 0, "peak_rss_bytes": 1024, "refusals": [],
        **({"memory": {"complete": True, "first_book": True}} if untimed else {}),
    }


class RenderAgain(unittest.TestCase):
    """r5-4: bench_driver.py render gives back a published run's files from its raw records,
    with the Commands line as it now records the run."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.root = Path(self.scratch.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.results = self.root / "results"
        self.results.mkdir()

    def tearDown(self):
        self.scratch.cleanup()

    def publish(self, argv, designs):
        """Each design's file as the driver wrote and stamp_load stamped it, its raw records
        in self.raw, with the given argv as its Commands line holds it."""
        args = bench_driver.parse_args(argv)
        runs = {}
        for index in (1, 2, 3):
            for design in designs:
                if design is None:
                    record = raw_record(rate=100.0 + index)
                    (self.raw / f"repetition-{index}.json").write_text(json.dumps(record))
                else:
                    timed = raw_record(design, 100.0 + index)
                    stem = self.raw / f"{design}-repetition-{index}"
                    Path(f"{stem}-timed.json").write_text(json.dumps(timed))
                    Path(f"{stem}-untimed.json").write_text(
                        json.dumps(raw_record(design, untimed=True)))
                    record = bench_driver.merged(timed, raw_record(design, untimed=True), index)
                runs.setdefault(design or "", []).append(record)
        command = bench_driver.relative_command(args)
        published = {}
        for design, records in runs.items():
            text = stamp_load.stamp(bench_driver.render(
                records, args, RUN_ID, "2026-10-31T10:00:01Z", "2026-10-31T10:00:09Z",
                MACHINE, command, runs), STDERR)
            name = Path(str(args.out).replace("{design}", design)).name
            (self.results / name).write_text(text, encoding="utf-8")
            published[name] = text
        return published

    def test_a_run_renders_again_to_the_same_files(self):
        published = self.publish([*BENCH_BASE, "--design", "hash-flat", "--design",
                                  "paged-flat", "--title", "Design {design}, a day"],
                                 ["hash-flat", "paged-flat"])
        first = published["bench_hash-flat.md"]
        self.assertEqual(bench_driver.render_again(self.raw, first, STDERR), published)
        again = bench_driver.render_again(self.raw, first, STDERR, again="the reason")
        self.assertIn("Do not edit by hand.\n\nRendered again from the run's own raw records, "
                      "unchanged: the reason\n\nHost: ", again["bench_paged-flat.md"])

    def render_main(self, *extra):
        out = self.root / "out"
        stderr = self.root / "stderr.txt"
        stderr.write_text(STDERR, encoding="utf-8")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            code = bench_driver.main(["render", "--raw-dir", str(self.raw), "--published",
                                      str(self.results / "bench_hash-flat.md"), "--stderr",
                                      str(stderr), "--out-dir", str(out), *extra])
        self.said = said.getvalue()
        return code, out

    def test_the_title_the_run_was_given_enters_the_command_line(self):
        """A file published before the Commands line held --title: the run's own --title,
        from the script that started it, renders the same title, and the Commands line now
        shows it; a title that does not give back the published one is refused."""
        published = self.publish([*BENCH_BASE, "--design", "hash-flat", *STUB_NOTE,
                                  "--title", "Design {design}, a day"], ["hash-flat"])
        text = published["bench_hash-flat.md"]
        command = bench_driver.relative_command(bench_driver.parse_args(
            [*BENCH_BASE, "--design", "hash-flat", *STUB_NOTE]))
        old = text.replace(" --title 'Design {design}, a day'", "")
        self.assertNotEqual(old, text)
        (self.results / "bench_hash-flat.md").write_text(old, encoding="utf-8")
        self.assertIn(command, old)
        for extra in (["--title", "Design {design}, another day"], []):
            code, out = self.render_main(*extra)
            self.assertEqual(code, 1)
            self.assertIn("give the run's own --title", self.said)
            self.assertFalse(out.exists())
        code, out = self.render_main("--title", "Design {design}, a day")
        self.assertEqual(code, 0)
        self.assertEqual(check_results.check_text(text), [])
        self.assertEqual((out / "bench_hash-flat.md").read_text(encoding="utf-8"), text)

    def test_a_flag_the_published_command_never_held_is_not_printed(self):
        published = self.publish([*BENCH_BASE, "--design", "hash-flat"], ["hash-flat"])
        text = published["bench_hash-flat.md"]
        old = text.replace(" --input-mode mapped", "")
        self.assertNotEqual(old, text)
        again = bench_driver.render_again(self.raw, old, STDERR)["bench_hash-flat.md"]
        self.assertEqual(again, old)

    def test_a_run_of_no_design_keeps_its_title_and_note_as_printed_and_says_so(self):
        published = self.publish([*BENCH_BASE[:-1], "results/bench_stub.md", "--title",
                                  "Stub, a day", "--note", "Stub-book harness proof, not a "
                                  "book result."], [None])
        text = published["bench_stub.md"]
        old = text.replace(" --title 'Stub, a day' --note 'Stub-book harness proof, not a book "
                           "result.'", "")
        self.assertNotEqual(old, text)
        with self.assertRaises(bench_driver.Refused):
            bench_driver.render_again(self.raw, old, STDERR, title="Stub, a day",
                                      as_published=True)
        again = bench_driver.render_again(self.raw, old, STDERR, as_published=True)
        self.assertEqual(again["bench_stub.md"], f"{old}\n{bench_driver.AS_PUBLISHED}\n")


if __name__ == "__main__":
    unittest.main()
