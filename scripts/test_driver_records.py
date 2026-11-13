#!/usr/bin/env python3
"""What each driver writes down about a run, with no benchmark run: its row in the run log,
and stamp_load.py's outcome row after it.

Each driver's main() runs with its run() replaced, so that what is tested is the row main()
writes for what run() did, not the run itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import types
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alloc_driver  # noqa: E402
import backtest_driver  # noqa: E402
import bench_driver  # noqa: E402
import check_results  # noqa: E402
import run_record  # noqa: E402
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


def no_record(args, *rest):
    """start_record for a run() that is replaced: its binaries are not there to hash, and
    the record is RunRecord's to test."""
    args.record = None


def run_main(name, root, run):
    """The driver's main() with run() replaced by `run`, on Linux so that no Mac sampler
    starts, and the commit fixed. Its exit code."""
    module = PENDING_DRIVERS[name]
    with mock.patch.object(module, "run", run), \
            mock.patch.object(bench_driver, "git_commit", lambda root, dirty: "abc123 (clean)"), \
            mock.patch.object(run_record, "start_record", no_record), \
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
                            mock.patch.object(run_record, "start_record", no_record), \
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
# The stub's label begins the note, as the published stub files' notes do.
STUB_NOTE = ["--note", f"{check_results.STUB_LABEL} A fixture."]
# Some fixtures here are design runs that carry the stub's label, so that the book checks,
# which records this small cannot meet, stay off. check_results flags that label on a design
# run, as it should, with this one code, which those tests set aside.
STUB_ON_A_BOOK = "stub-label-on-a-book"


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

    def render_main(self, *extra, published="bench_hash-flat.md"):
        out = self.root / "out"
        stderr = self.root / "stderr.txt"
        stderr.write_text(STDERR, encoding="utf-8")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            code = bench_driver.main(["render", "--raw-dir", str(self.raw), "--published",
                                      str(self.results / published), "--stderr",
                                      str(stderr), "--out-dir", str(out), *extra])
        self.said = said.getvalue()
        return code, out

    def test_the_title_the_run_was_given_enters_the_command_line(self):
        """A file published before the Commands line held --title: the run's own --title,
        from the script that started it, renders the same title, and the Commands line now
        shows it; a title that does not give back the published one is refused."""
        # A stub run: check_results holds it to neither the book checks nor the stub rule.
        stub = [*BENCH_BASE[:-1], "results/bench_stub.md", *STUB_NOTE]
        published = self.publish([*stub, "--title", "Stub, a day"], [None])
        text = published["bench_stub.md"]
        command = bench_driver.relative_command(bench_driver.parse_args(stub))
        old = text.replace(" --title 'Stub, a day'", "")
        self.assertNotEqual(old, text)
        (self.results / "bench_stub.md").write_text(old, encoding="utf-8")
        self.assertIn(command, old)
        for extra in (["--title", "Stub, another day"], []):
            code, out = self.render_main(*extra, published="bench_stub.md")
            self.assertEqual(code, 1)
            self.assertIn("give the run's own --title", self.said)
            self.assertFalse(out.exists())
        code, out = self.render_main("--title", "Stub, a day", published="bench_stub.md")
        self.assertEqual(code, 0)
        self.assertEqual(check_results.check_text(text), [])
        self.assertEqual((out / "bench_stub.md").read_text(encoding="utf-8"), text)

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
        # Rendered again once more, it is the same file, with the sentence once.
        self.assertEqual(bench_driver.render_again(self.raw, again["bench_stub.md"], STDERR,
                                                   as_published=True), again)

    def test_each_render_stays_on_record(self):
        published = self.publish([*BENCH_BASE, "--design", "hash-flat"], ["hash-flat"])
        first = bench_driver.render_again(self.raw, published["bench_hash-flat.md"], STDERR,
                                          again="the first reason")["bench_hash-flat.md"]
        second = bench_driver.render_again(self.raw, first, STDERR,
                                           again="the second reason")["bench_hash-flat.md"]
        self.assertIn(f"\n\n{bench_driver.AGAIN}the first reason\n\n"
                      f"{bench_driver.AGAIN}the second reason\n\nHost: ", second)


class DriverSamplerCheck(unittest.TestCase):
    """r5-2: check_results holds a Mac bench or backtest file whose driver applied the
    sampler rule to its own Sampler section, and still wants one or the sampler line."""

    def text(self, sampled):
        args = bench_driver.parse_args([*BENCH_BASE, "--design", "hash-flat", *STUB_NOTE])
        records = [bench_driver.merged(raw_record("hash-flat", 100.0 + index),
                                       raw_record("hash-flat", untimed=True), index)
                   for index in (1, 2, 3)]
        text = bench_driver.render(records, args, RUN_ID, "2026-11-13T04:00:01Z",
                                   "2026-11-13T04:00:09Z", MACHINE, "command",
                                   {"hash-flat": records}, sampled)
        return text.replace("at=2026-10-31T10:00:00Z", "at=2026-11-13T04:00:00Z")

    def sampled(self):
        quiet = "published: 3 samples, the longest gap 2 s, no other process above 20% CPU"
        return {"every": 2.0, "withdrawn": [], "published": [
            (f"hash-flat, repetition {index}, {kind} process", "2026-11-13T04:00:02Z",
             "2026-11-13T04:00:03Z", quiet) for index in (1, 2, 3) for kind in ("timed", "untimed")]}

    def codes(self, text):
        return {code for code, _ in check_results.check_text(text)} - {"load-not-stamped",
                                                                       STUB_ON_A_BOOK}

    def test_a_driver_sampled_file_is_held_to_its_sampler_section(self):
        sampled = self.sampled()
        self.assertEqual(self.codes(self.text(sampled)), set())
        sampled["published"].pop()
        self.assertEqual(self.codes(self.text(sampled)), {"sampler-not-quiet"})
        text = self.text(self.sampled())
        self.assertEqual(self.codes(text.replace(check_results.DRIVER_SAMPLER_RULE, "Sampled.")),
                         {"sampler-rule-unsaid"})
        noisy = text.replace("| published: 3 samples", "| withdrawn: 3 samples", 1)
        self.assertEqual(self.codes(noisy), {"sampler-not-quiet"})

    def test_a_later_mac_file_with_neither_needs_the_sampler(self):
        text = self.text(None)
        with self.assertRaisesRegex(ValueError, "needs the CPU sampler's log"):
            stamp_load.stamp(text, STDERR)
        for name, line in (("start", "measure.sh: start load1=1.20 host=macOS (M4 Max) "
                                     "at=2026-11-13T04:00:00Z"),
                           ("end", "measure.sh: end load1=1.30 exit=0 at=2026-11-13T04:00:10Z")):
            text = text.replace(f"- measure_load_{name}: {check_results.PENDING}",
                                f"- measure_load_{name}: {line}")
        self.assertIn("sampler-missing", self.codes(text))


class RdpmcCaveat(unittest.TestCase):
    """r4-1: the per-message means through rdpmc hold the counter reads, and the file says so,
    with the empty span bench_counts measured on the same host, design and build quoted for
    scale from its counters_by_type file, and nothing taken off."""

    def records(self, flags=None):
        records = []
        for index in (1, 2, 3):
            record = bench_driver.merged(raw_record("hash-flat", 100.0 + index),
                                         raw_record("hash-flat", untimed=True), index)
            record["header"]["dataset"] = "12302019.NASDAQ_ITCH50"
            record["counters"]["per_message_published"] = True
            record["counters"]["per_message"] = [
                {"name": "cycles", "mean": 900.0 + index, "p50": 1, "p99": 2, "sum": 9000},
                {"name": "instructions", "mean": 580.0, "p50": 1, "p99": 2, "sum": 5800}]
            record["counters"]["agreement_events"] = [
                {"name": "cycles", "read": 10000, "rdpmc": 9990},
                {"name": "instructions", "read": 6000, "rdpmc": 6000}]
            records.append(record)
        return records

    def text(self, results_dir):
        args = bench_driver.parse_args([*BENCH_BASE, "--design", "hash-flat", *STUB_NOTE])
        records = self.records()
        return bench_driver.render(records, args, RUN_ID, "2026-10-31T10:00:01Z",
                                   "2026-10-31T10:00:09Z", MACHINE, "command",
                                   {"hash-flat": records}, results_dir=results_dir)

    def counts_file(self, directory, flags):
        name = "counters_by_type_hash-flat_macOS (M4 Max)_12302019.md"
        (directory / name).write_text(
            f"# Counts\n\n## Header\n\n- host: {HEADER['host']}\n- flags: {flags}\n"
            "- design: hash-flat: a fixture\n\n## The cost of counting\n\n"
            "| Event | Before the pass | After the pass | Used beside the figures |\n"
            "|---|---|---|---|\n| cycles | 444.0745 | 444.0757 | 444.0739 |\n"
            "| instructions | 308.0001 | 308.0001 | 308.0001 |\n", encoding="utf-8")
        return name

    def test_the_table_says_what_it_holds_and_quotes_the_empty_span(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            text = self.text(root)
            self.assertIn(check_results.RDPMC_CAVEAT + " No `counters_by_type` file is on "
                          "record for this host and design", text)
            self.assertIn("| Event | read() | rdpmc | Widest gap | Gap | Sum of the per-message "
                          "spans |", text)
            self.assertNotIn("Sum over apply()", text)
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped", STUB_ON_A_BOOK})
            name = self.counts_file(root, "-O3 -DNDEBUG -std=c++23 -g")
            self.assertIn("is of the build with flags `-O3 -DNDEBUG -std=c++23 -g`, not this "
                          "one's, so its empty span is not quoted.", self.text(root))
            self.counts_file(root, HEADER["flags"])
            quoted = self.text(root)
            self.assertIn(f"at cycles 444.0739, instructions 308.0001 ([{name}]({name}), "
                          "\"The cost of counting\"). Its spans are not this pass's, so nothing "
                          "here is taken off by it.", quoted)
            # The means are the raw ones: nothing is taken off.
            self.assertIn("| cycles | 902.000 | 901.000 | 903.000 |", quoted)

    def test_a_table_without_the_caveat_is_refused(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            text = self.text(Path(directory))
        dropped = text.replace(check_results.RDPMC_CAVEAT, "")
        self.assertIn("rdpmc-caveat-missing",
                      {code for code, _ in check_results.check_text(dropped)})


class StandInSampler(unittest.TestCase):
    """A test gives a driver a stand-in for `ps` (--ps), so that its result does not hang on
    what else the Mac is doing; the file says so, and check_results refuses it as a result,
    so that no run can have the rule switched off without a word."""

    def test_a_stand_in_is_said_and_refused_and_the_real_ps_is_not(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            path = Path(directory) / "sampler.txt"
            self.assertFalse(spsc_driver.Sampler(path, "ps", 2.0).stand_in)
            self.assertTrue(spsc_driver.Sampler(path, "/a/stand-in/ps.sh", 2.0).stand_in)
        quiet = "published: 3 samples, the longest gap 2 s, no other process above 20% CPU"
        for stand_in in (False, True):
            sampled = {"every": 2.0, "stand_in": stand_in, "withdrawn": [], "published": [
                ("repetition 1, timed process", "2026-11-13T04:00:02Z", "2026-11-13T04:00:03Z",
                 quiet)]}
            sections = {"bench and backtest": "\n".join(bench_driver_section(sampled)),
                        "spsc": "\n".join(spsc_driver.sampler_sections(
                            {**sampled, "published": [entry[1:] for entry in
                                                      sampled["published"]]})),
                        "alloc": "\n".join(alloc_driver.sampler_rows(sampled))}
            for name, section in sections.items():
                self.assertEqual(check_results.STAND_IN_SAMPLER in section, stand_in, name)
                found = dict(check_results.check_stand_in(section))
                self.assertEqual("sampler-stand-in" in found, stand_in, name)

    def test_a_run_of_no_design_keeps_every_process_it_sampled(self):
        """A run given no --design names its processes by repetition alone, though its records
        name bench_replay's default design: its one file takes them all."""
        given = bench_driver.parse_args([*BENCH_BASE])
        both = bench_driver.parse_args([*BENCH_BASE, "--design", "paged-flat"])
        mine = bench_driver.sampled_for(given, "paged-flat")
        self.assertTrue(mine("repetition 1, timed process"))
        theirs = bench_driver.sampled_for(both, "paged-flat")
        self.assertTrue(theirs("paged-flat, repetition 1, timed process"))
        self.assertFalse(theirs("map-fresh, repetition 1, timed process"))


def bench_driver_section(sampled):
    import mac_rule  # noqa: PLC0415  As bench_driver and backtest_driver do.

    return mac_rule.sampler_section(sampled)


class MacSamplerScript(unittest.TestCase):
    """r5-2: scripts/mac_sampler.sh, the sampler the published variants runs on the Mac used,
    now in the repository: its log as variants_report.py reads it."""

    def test_it_names_every_other_busy_process_and_no_measured_one(self):
        import variants_report  # noqa: PLC0415

        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            ps = root / "bin" / "ps"
            ps.write_text("#!/bin/sh\necho ' %CPU ARGS'\necho ' 55.0 /usr/bin/other_job --x'\n"
                          "echo ' 90.0 /some/bin/bench_variants --experiment lookup'\n"
                          "echo '  5.0 /usr/bin/idle_job'\n", encoding="utf-8")
            ps.chmod(0o755)
            log = root / "sampler.txt"
            process = subprocess.Popen(
                ["bash", str(REPO_ROOT / "scripts" / "mac_sampler.sh"), str(log), "0.2"],
                env={"PATH": f"{root / 'bin'}:/usr/bin:/bin"})
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and (not log.is_file() or log.read_text(
                        encoding="utf-8").count(" sample\n") < 3):
                    time.sleep(0.1)
            finally:
                process.kill()
                process.wait()
            lines = log.read_text(encoding="utf-8").splitlines()
        samples = [line for line in lines if line.endswith(" sample")]
        foreign = [line for line in lines if " FOREIGN " in line]
        self.assertGreaterEqual(len(samples), 3, lines)
        self.assertTrue(foreign, lines)
        for line in foreign:
            self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ FOREIGN 55\.0 "
                                   r"/usr/bin/other_job --x$")
        start = samples[0].split()[0]
        with self.assertRaisesRegex(variants_report.ReportError, "another process was busy"):
            variants_report.quiet_spans("\n".join(lines), [(f"x at={start}", f"y at={start}")])


FAKE_REPLAY = """#!{python}
import json, os, sys, time
argv = sys.argv[1:]
options = dict(zip(argv[::2], argv[1::2]))
record = json.loads(open({template!r}).read())
record["header"]["design"] = options["--design"] + ": a fixture"
if options["--passes"] == "timed" and {busy!r} != "none" and (
        {busy!r} == "always" or not os.path.exists({once!r})):
    open({once!r}, "w").close()
    open({busy_file!r}, "w").close()
    time.sleep(1)
    os.remove({busy_file!r})
open(options["--out"], "w").write(json.dumps(record))
"""


@unittest.skipUnless(sys.platform == "darwin", "the Mac's sampler rule runs on macOS only")
class MacSampler(unittest.TestCase):
    """r5-2: bench_driver applies the Mac's sampler rule to each process itself, so a later
    Mac run needs no outside sampler's log."""

    def run_driver(self, root, busy="none"):
        import test_alloc_results  # noqa: PLC0415

        (root / "results").mkdir()
        (root / "results" / "predictions.md").write_text("## P-0999: x\n\nIt runs.\n",
                                                         encoding="utf-8")
        template = root / "record.json"
        fake = test_alloc_results.record("malloc-fresh")
        fake["header"]["residency"] = "1 of 1 pages resident"
        template.write_text(json.dumps(fake), encoding="utf-8")
        bench = root / "bench.py"
        bench.write_text(FAKE_REPLAY.format(python=sys.executable, template=str(template),
                                            busy=busy, once=str(root / "once"),
                                            busy_file=str(root / "busy")), encoding="utf-8")
        bench.chmod(0o755)
        ps = root / "ps.sh"
        ps.write_text(f"#!/bin/sh\n[ -f '{root}/busy' ] && echo '99999 1 50.0 /usr/bin/other_job'"
                      "\nexit 0\n", encoding="utf-8")
        ps.chmod(0o755)
        machine = FIXTURES.parent / "machine_check"
        out = root / "results" / "bench_{design}.md"
        result = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "bench_driver.py"),
             "--bench", str(bench), "--probe", str(machine / "fake_probe.sh"),
             "--input", str(machine / "input.bin"), "--prediction", "P-0999",
             "--raw-dir", str(root / "raw"), "--out", str(out), "--design", "paged-flat",
             "--design", "map-fresh", "--input-mode", "ram", "--allow-dirty",
             "--predictions-root", str(root), "--run-log", str(root / "results" / "run_log.md"),
             "--ps", str(ps), "--sample-every", "0.2"],
            capture_output=True, text=True, timeout=300, check=False)
        log = root / "results" / "run_log.md"
        return result, root / "results", log.read_text(encoding="utf-8") if log.is_file() else ""

    def test_a_run_is_sampled_by_the_driver_and_passes_once_stamped(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, results, log = self.run_driver(Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            for design in ("paged-flat", "map-fresh"):
                text = (results / f"bench_{design}.md").read_text(encoding="utf-8")
                self.assertEqual({code for code, _ in check_results.check_text(text)},
                                 {"load-not-stamped", "sampler-stand-in"}, design)
                self.assertIn(f"- interference: {check_results.driver_interference(0.2)}; 0 "
                              "withdrawn under the rule\n", text)
                self.assertEqual(text.count(f"| {design}, repetition "), 6)
                stamped = stamp_load.stamp(text.replace(
                    "- run_started: ", "- run_started: 2026-11-13T04:00:00Z\n- was: "), STDERR)
                self.assertNotIn("- sampler:", stamped)

    def test_a_process_beside_another_busy_process_is_withdrawn_and_rerun(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, results, log = self.run_driver(Path(directory), busy="first")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("withdrawn: paged-flat, repetition 1, timed process on the Mac, exit 0; "
                          "the CPU sampler saw another process at 50.0% CPU", log)
            text = (results / "bench_paged-flat.md").read_text(encoding="utf-8")
            self.assertEqual({code for code, _ in check_results.check_text(text)},
                             {"load-not-stamped", "sampler-stand-in"})
            self.assertIn("; 1 withdrawn under the rule", text)
            other = (results / "bench_map-fresh.md").read_text(encoding="utf-8")
            self.assertIn("; 0 withdrawn under the rule", other)
            self.assertTrue((run_folder_of(Path(directory) / "raw") /
                             "paged-flat-repetition-1-timed-withdrawn-1.json").is_file())

    def test_a_process_withdrawn_three_times_running_ends_the_run(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            result, results, log = self.run_driver(Path(directory), busy="always")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse((results / "bench_paged-flat.md").exists())
            self.assertEqual(log.count("withdrawn: paged-flat, repetition 1, timed process"), 3)
            self.assertIn("refused at paged-flat, repetition 1, timed process: withdrawn 3 times "
                          "in a row under the sampler rule", log)


def run_folder_of(parent):
    """The one run folder a driver made under --raw-dir."""
    folders = [path for path in Path(parent).iterdir() if path.is_dir()]
    assert len(folders) == 1, folders
    return folders[0]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def untold_tree_state():
    """[] where the tests run in a checkout, or the gap run_record.missing() names where they
    run in a copy with no git and no COMMIT file, such as the fault matrix's, whose drivers
    cannot say whether the tree was clean."""
    commit = bench_driver.git_commit(bench_driver.REPO_ROOT, True)
    return [] if commit.endswith(("(clean)", "(with uncommitted changes)",
                                  "(exported by debian_sync.sh)")) else \
        ["whether the tree was clean"]


def state_the_tree(test, folder, render):
    """Where untold_tree_state() is not empty, shows the render refusing the record for it,
    then gives the record a tree state so that the rest of the test can run."""
    if not untold_tree_state():
        return
    with test.assertRaisesRegex(run_record.Incomplete, "whether the tree was clean"):
        render()
    path = Path(folder) / run_record.RECORD_NAME
    kept = json.loads(path.read_text(encoding="utf-8"))
    kept["commit"] = "000000000000 (exported by debian_sync.sh)"
    path.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")


def assert_run_record(test, parent, results_text, binaries, needs, dataset=None):
    """The run's raw folder holds the record of the new kind (gate item 8): named by the run
    id, with its commit and tree state, each binary's hash, the dataset hashed, and each tool
    output untouched; complete once measure.sh's stderr is added."""
    run_id = check_results.parse_header(results_text)["run_id"]
    folder = run_folder_of(parent)
    test.assertEqual(folder.name, run_id)
    record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    test.assertEqual(record["version"], run_record.RECORD_VERSION)
    test.assertEqual(record["run_id"], run_id)
    # As the driver read it from the tree it ran in: in a copy with no git and no COMMIT file,
    # such as the fault matrix's, it is unavailable, and the record lacks the tree state.
    test.assertEqual(record["commit"], bench_driver.git_commit(bench_driver.REPO_ROOT, True))
    untold = untold_tree_state()
    test.assertEqual(record["binaries"], {str(path): sha256(path) for path in binaries})
    if dataset is not None:
        test.assertEqual(record["dataset"]["name"], Path(dataset).name)
        test.assertEqual(record["dataset"]["sha256"], sha256(dataset))
    for name in needs:
        test.assertIn(name, record["outputs"])
    test.assertEqual(run_record.missing(folder, needs, run_id),
                     untold + ["measure.stderr, untouched"])
    stderr = Path(parent).parent / "measure.stderr"
    stderr.write_text(STDERR, encoding="utf-8")
    run_record.add_measure_stderr(folder, run_id, stderr)
    test.assertEqual(run_record.missing(folder, needs, run_id), untold)
    return folder, record


@unittest.skipUnless(sys.platform == "darwin", "the drivers' end-to-end runs are on macOS")
class RunRecord(unittest.TestCase):
    """Gate item 8: what every run keeps from now on, and the render's refusal."""

    def test_a_bench_run_keeps_its_record(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            result, results, _ = MacSampler.run_driver(None, root)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (results / "bench_paged-flat.md").read_text(encoding="utf-8")
            machine = FIXTURES.parent / "machine_check"
            folder, _ = assert_run_record(
                self, root / "raw", text, (root / "bench.py", machine / "fake_probe.sh"),
                (bench_driver.MACHINE_CHECK, "sampler.txt"), machine / "input.bin")
            block = check_results.section(text, "machine_check output").strip().strip("`").strip()
            self.assertEqual(run_record.kept_output(folder, bench_driver.MACHINE_CHECK), block)


class RunRecordParts(unittest.TestCase):
    """Gate item 8, piece by piece: the folder, each field, the tree state, the reports'
    refusals."""

    def test_a_raw_folder_another_run_wrote_in_is_refused(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            parent = Path(directory)
            self.assertEqual(run_record.run_folder(parent, "P-1-x"), parent / "P-1-x")
            (parent / "P-1-x" / "a.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(run_record.Incomplete, "exists and is not empty"):
                run_record.run_folder(parent, "P-1-x")

    def test_stamp_load_keeps_measure_stderr_and_refuses_another_runs_folder(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            folder = run_record.run_folder(Path(directory), RUN_ID)
            run_record.Record(folder, "x", RUN_ID, "0123456789ab (clean)").write()
            stderr = Path(directory) / "measure.stderr"
            stderr.write_bytes(STDERR.encode() + b"\xff\n")
            run_record.add_measure_stderr(folder, RUN_ID, stderr)
            self.assertEqual((folder / "outputs" / "measure.stderr").read_bytes(),
                             stderr.read_bytes())
            with self.assertRaisesRegex(run_record.Incomplete, "not P-0998"):
                run_record.add_measure_stderr(folder, "P-0998-x", stderr)

    def complete(self, parent):
        """A complete record of the new kind, built as a driver builds it."""
        folder = run_record.run_folder(parent, RUN_ID)
        binary = parent / "bench"
        binary.write_bytes(b"a binary")
        data = parent / "day.itch"
        data.write_bytes(b"a day")
        record = run_record.Record(folder, "bench_driver", RUN_ID, "0123456789ab (clean)")
        record.binary(binary)
        record.dataset(data, sha256(data), manifest=parent / "no-manifest.json")
        record.output("machine_check.txt", "machine_check: passed\n")
        stderr = parent / "measure.stderr"
        stderr.write_text(STDERR, encoding="utf-8")
        run_record.add_measure_stderr(folder, RUN_ID, stderr)
        return folder

    def test_a_record_lacking_any_field_is_refused(self):
        needs = ("machine_check.txt",)
        for field in run_record.FIELDS:
            with self.subTest(field=field), tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
                folder = self.complete(Path(directory))
                self.assertEqual(run_record.missing(folder, needs, RUN_ID), [])
                record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
                del record[field]
                (folder / "run.json").write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaisesRegex(run_record.Incomplete, "lacks"):
                    run_record.refuse_incomplete(folder, needs, RUN_ID)

    def test_a_record_lacking_a_part_of_a_field_is_refused(self):
        needs = ("machine_check.txt",)
        changes = {
            "the tree state": lambda r: r.update(commit="0123456789ab"),
            "a binary's hash": lambda r: r["binaries"].update({"bench": "unavailable"}),
            "the dataset hash": lambda r: r["dataset"].update(sha256=None),
            "an output": lambda r: r["outputs"].pop("machine_check.txt"),
            "measure.sh's stderr": lambda r: r["outputs"].pop("measure.stderr"),
            "another run id": lambda r: r.update(run_id="P-0999-20261031T100006Z"),
            "another version": lambda r: r.update(version=0),
        }
        for what, change in changes.items():
            with self.subTest(what=what), tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
                folder = self.complete(Path(directory))
                record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
                change(record)
                (folder / "run.json").write_text(json.dumps(record), encoding="utf-8")
                self.assertNotEqual(run_record.missing(folder, needs, RUN_ID), [], what)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            folder = self.complete(Path(directory))
            (folder / "outputs" / "machine_check.txt").write_text("edited\n", encoding="utf-8")
            self.assertIn("machine_check.txt as it was kept",
                          "; ".join(run_record.missing(folder, needs, RUN_ID)))

    def test_a_dataset_that_is_not_its_pin_is_refused(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            parent = Path(directory)
            manifest = parent / "manifest.json"
            manifest.write_text(json.dumps({"days": {"d": {"name": "day.itch",
                                                           "unpacked_sha256": "0" * 64}}}),
                                encoding="utf-8")
            folder = run_record.run_folder(parent, RUN_ID)
            record = run_record.Record(folder, "x", RUN_ID, "0123456789ab (clean)")
            with self.assertRaisesRegex(run_record.Incomplete, "not the 0000"):
                record.dataset(parent / "day.itch", "1" * 64, manifest=manifest)
            record.dataset(parent / "day.itch", "0" * 64, manifest=manifest)
            self.assertEqual(record.data["dataset"]["manifest"], "matches its pin")

    def test_an_old_record_renders_as_before_and_a_new_one_must_be_complete(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            self.assertEqual(run_record.missing(Path(directory)), [])
            folder = self.complete(Path(directory))
            (folder / "outputs" / "measure.stderr").unlink()
            with self.assertRaisesRegex(run_record.Incomplete, "measure.stderr"):
                bench_driver.render_again(folder, PENDING, STDERR)

    def test_the_tree_state_counts_cmake_presets(self):
        """r5-9: CMakePresets.json sets every build's flags, so a change to it is a dirty tree."""
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            root = Path(directory)
            git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "CMakePresets.json").write_text("{}\n", encoding="utf-8")
            subprocess.run([*git, "add", "CMakePresets.json"], check=True)
            subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
            self.assertTrue(bench_driver.git_commit(root, False).endswith("(clean)"))
            (root / "CMakePresets.json").write_text('{"x": 1}\n', encoding="utf-8")
            with self.assertRaisesRegex(bench_driver.Refused, "uncommitted changes"):
                bench_driver.git_commit(root, False)
            self.assertTrue(bench_driver.git_commit(root, True).endswith(
                "(with uncommitted changes)"))

    def incomplete_run_dir(self, parent, manifest):
        folder = run_record.run_folder(parent, RUN_ID)
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_record.Record(folder, "x", RUN_ID, "0123456789ab (clean)").write()
        return folder

    def test_the_simd_and_udp_reports_refuse_an_incomplete_record(self):
        import simd_report  # noqa: PLC0415
        import udp_report  # noqa: PLC0415

        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            folder = self.incomplete_run_dir(Path(directory), {
                "run_id": RUN_ID, "sampler": None, "processes": [], "step": "bisect"})
            with self.assertRaisesRegex(simd_report.ReportError, "machine_check_before.txt"):
                simd_report.read_run(folder)
            with self.assertRaisesRegex(udp_report.ReportError, "machine_check_after.txt"):
                udp_report.read_runs([folder])

    def test_spsc_keeps_the_cross_core_check_untouched(self):
        with tempfile.TemporaryDirectory(dir=SCRATCH) as directory:
            parent = Path(directory)
            output = ("cross_core_offset: [-80, 80] ticks, [-40.0, 40.0] ns\n"
                      "cross_core_trips: 10 started on each core\n"
                      "cross_core_fastest_round_trip: 40 ticks, 20.0 ns\n"
                      "cross_core_backwards: 0 of 20 trips read a later stamp below an earlier "
                      "one\ncross_core_verdict: agree: the bound holds 0\n")
            probe = parent / "probe.sh"
            probe.write_text(f"#!/bin/sh\ncat <<'END'\n{output}END\n", encoding="utf-8")
            probe.chmod(0o755)
            folder = run_record.run_folder(parent, RUN_ID)
            args = types.SimpleNamespace(peer_core=6, core=4, probe=probe, record=run_record.Record(
                folder, "spsc_driver", RUN_ID, "0123456789ab (clean)"))
            line = spsc_driver.cross_core_check(args)
            self.assertEqual(run_record.kept_output(folder, spsc_driver.CROSS_CORE), output)
            self.assertEqual(line, spsc_driver.cross_core_line(output))


if __name__ == "__main__":
    unittest.main()
