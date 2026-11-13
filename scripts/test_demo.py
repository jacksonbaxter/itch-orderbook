#!/usr/bin/env python3
"""Tests of the demo's scripts: demo.py's numbers and server, wasm_bench.py's two sides with
stand-ins for bench_demo and the browser, and wasm_report.py's file and its checker.

Every record, results file and extract here is made up at test time. Nothing is read from
data/ or docs/local/.
"""

import contextlib
import datetime
import http.client
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo  # noqa: E402
import run_record  # noqa: E402
import wasm_bench  # noqa: E402
import wasm_report  # noqa: E402

SCRATCH = demo.REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

LINUX_FILE = textwrap.dedent("""\
    # Book design paged-flat on debian, 12302019, the file in memory

    ## Header

    - host: {host}
    - cpu: AMD Ryzen Embedded V1605B with Radeon Vega Gfx
    - kernel: {kernel}
    - compiler: Clang 23.1.4
    - flags: -O3 -DNDEBUG -std=c++23
    - commit: e0f9bb8101a2 (exported by debian_sync.sh)
    - dataset: 12302019.NASDAQ_ITCH50
    - pinning: pinned to core 4
    - design: paged-flat: FastBook, PagedIndex<8>
    - prediction: P-0015
    - run_id: P-0015-20261105T023320Z

    ## Summary

    | Figure | Median | Min | Max | CV |
    |---|---|---|---|---|
    | Throughput, messages/s | 5,145,430 | 5,008,710 | 5,333,708 | 3.16% |
    | Book memory, MiB | 662.8 | 662.8 | 662.8 | 0.00% |

    ## Latency

    | Percentile | Median ns | Min ns | Max ns | CV | Status | Median ns, pair subtracted |
    |---|---|---|---|---|---|---|
    | p50 | 140.1 | 140.1 | 150.2 | 4.03% | resolved | 120.3 |
    | p99 | 840.9 | 810.8 | 860.9 | 3.01% | resolved | 820.8 |
    | p99.9 | 1472.0 | 1442.0 | 1482.0 | 1.42% | resolved | 1452.0 |
    """)


def quietly(function, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = function(*args)
    return code, out.getvalue()


class Scratch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class Numbers(Scratch):
    def setUp(self):
        super().setUp()
        (self.root / "results").mkdir()

    def test_the_linux_slot_reads_the_debian_file(self):
        self.write("results/bench_book_paged-flat_ram_debian_12302019.md",
                   LINUX_FILE.format(host="debian", kernel="Linux 6.12.115+deb13.1-amd64"))
        linux = demo.numbers(self.root)["linux"]
        self.assertEqual(linux["throughput_messages_per_s"], "5,145,430")
        self.assertEqual(linux["latency"]["p99.9"], {"median_ns": "1472.0", "status": "resolved"})
        self.assertEqual(linux["book_memory_mib"], "662.8")
        self.assertEqual(linux["commit"], "e0f9bb8101a2 (exported by debian_sync.sh)")
        self.assertEqual(linux["design"], "paged-flat")

    def test_each_slot_is_missing_until_its_file_exists(self):
        found = demo.numbers(self.root)
        self.assertIn("missing", found["linux"])
        self.assertIn("missing", found["wasm_macos"])

    def test_a_mac_file_never_fills_the_linux_slot(self):
        self.write("results/bench_book_paged-flat_ram_debian_12302019.md",
                   LINUX_FILE.format(host="macOS (M4 Max)", kernel="Darwin 27.2.0"))
        with self.assertRaisesRegex(demo.DemoError, "not debian on Linux"):
            demo.numbers(self.root)

    def test_check_fails_on_a_stale_file(self):
        self.write("docs/numbers.json", "{}\n")
        code, output = quietly(demo.main, ["numbers", "--check", "--root", str(self.root)])
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL numbers-stale", output)
        self.assertEqual(quietly(demo.main, ["numbers", "--root", str(self.root)])[0], 0)
        code, output = quietly(demo.main, ["numbers", "--check", "--root", str(self.root)])
        self.assertEqual(code, 0, output)

    def test_the_repository_matches_its_results(self):
        code, output = quietly(demo.main, ["numbers", "--check"])
        self.assertEqual(code, 0, output)


class Server(Scratch):
    def test_it_serves_docs_isolated_with_module_types(self):
        for name in ("index.html", "local/itch_demo.mjs", "local/itch_demo.wasm"):
            self.write(f"docs/{name}", "x")
        server = demo.make_server(0, root=self.root / "docs")
        server.quiet = True
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        for name, kind in (("index.html", "text/html"), ("local/itch_demo.mjs", "text/javascript"),
                           ("local/itch_demo.wasm", "application/wasm")):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            connection.request("GET", f"/{name}")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200, name)
            self.assertEqual(response.getheader("Content-Type").split(";")[0], kind, name)
            self.assertEqual(response.getheader("Cross-Origin-Opener-Policy"), "same-origin")
            self.assertEqual(response.getheader("Cross-Origin-Embedder-Policy"), "require-corp")
            connection.close()


# Stand-ins for bench_demo and the browser, written into the scratch folder at test time.
FAKE_BENCH = textwrap.dedent("""\
    #!{python}
    import json, sys
    args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    record = {{"header": {{"host": "macOS (M4 Max)"}}, "digest": "{digest}", "refusals": [],
               "repetitions": [{{"ns": 1000000, "messages": 10}}] * int(args["--repetitions"]),
               "warmup": {{"ns": 1000000}}}}
    open(args["--out"], "w").write(json.dumps(record))
    """)

FAKE_BROWSER = textwrap.dedent("""\
    #!{python}
    import hashlib, json, sys, time, urllib.request
    if sys.argv[1:] == ["--version"]:
        print("Fake Browser 1.0")
        sys.exit(0)
    url = sys.argv[-1]
    base = url.split("index.html")[0]
    urllib.request.urlopen(url).read()
    extract = json.loads(urllib.request.urlopen(base + "local/extract.json").read())
    wasm = urllib.request.urlopen(base + "local/itch_demo.wasm").read()
    runs = int(url.split("bench=")[1])
    record = {{"engine": "wasm", "user_agent": "Fake", "cross_origin_isolated": True,
               "clock_step_ms": 0.005, "refusals": {refusals}, "wasm_sha256": hashlib.sha256(wasm).hexdigest(),
               "memory_bytes": 1073741824, "memory_bytes_after": {after},
               "extract": {{"sha256": extract["extract_sha256"], "messages": 10}},
               "warmup": {{"ms": 1.2, "messages": 10}}, "digest": extract["digest"],
               "repetitions": [{{"ms": 1.5, "messages": 10, "digest": extract["digest"]}}] * runs}}
    request = urllib.request.Request(base + "result", data=json.dumps(record).encode(),
                                     headers={{"Content-Type": "application/json"}})
    urllib.request.urlopen(request).read()
    time.sleep(120)
    """)

FAKE_CHECK = "#!/bin/bash\necho 'machine_check: pass fake: a stand-in'\n"


class Bench(Scratch):
    """wasm_bench.py end to end, each side with a stand-in for the program it runs."""

    def setUp(self):
        super().setUp()
        commit = wasm_bench.bench_driver.git_commit(demo.REPO_ROOT, True)
        extract = self.write("docs/local/extract.itch", "frames")
        self.manifest = {"symbol": "TEST", "day": "00000000.NASDAQ_ITCH50", "messages": 10,
                         "bytes": 6, "digest": "00000000000000aa", "extract_file": "extract.itch",
                         "extract_sha256": demo.sha256_of(extract), "day_sha256": "none"}
        self.write("docs/local/extract.json", json.dumps(self.manifest))
        self.write("docs/local/itch_demo.mjs", "export default 0;")
        wasm = self.write("docs/local/itch_demo.wasm", "wasm")
        self.write("docs/local/engine.json", json.dumps(
            {"commit": commit, "wasm_sha256": demo.sha256_of(wasm), "compiler": "em++",
             "flags": "-O3"}))
        self.write("docs/index.html", "<!doctype html>")
        self.write("results/predictions.md", "# Predictions\n\n## P-0401: a test\n\n- Experiment.\n")
        self.log = self.root / "results" / "run_log.md"
        self.check_script = self.write("machine_check.sh", FAKE_CHECK)
        self.foreign = []
        real = wasm_bench.processes
        wasm_bench.processes = lambda: [(os.getpid(), 1, 0.0, "python3 wasm_bench.py"),
                                        *self.foreign]
        self.addCleanup(setattr, wasm_bench, "processes", real)
        # A file the driver hashes for its record (gate item 8); the fake check never runs it.
        probe = self.write("probe", "a probe\n")
        self.common = ["--probe", str(probe), "--prediction", "P-0401", "--raw-dir",
                       str(self.root / "raw"), "--repetitions", "3", "--allow-dirty",
                       "--run-log", str(self.log), "--docs", str(self.root / "docs"),
                       "--predictions-root", str(self.root),
                       "--machine-check", str(self.check_script)]

    def run_folder(self):
        """The one folder, named by the run id, that the run wrote under --raw-dir."""
        folders = list((self.root / "raw").iterdir())
        self.assertEqual(len(folders), 1, folders)
        return folders[0]

    def executable(self, name, text):
        path = self.write(name, text)
        os.chmod(path, 0o755)
        return path

    def test_native_writes_its_record_and_a_row(self):
        bench = self.executable("bench.py", FAKE_BENCH.format(python=sys.executable,
                                                              digest=self.manifest["digest"]))
        code, output = quietly(wasm_bench.main, ["native", "--bench", str(bench), *self.common])
        self.assertEqual(code, 0, output)
        record = json.loads((self.run_folder() / "native.json").read_text())
        self.assertEqual(record["side"], "native")
        self.assertEqual(record["extract_manifest"]["digest"], self.manifest["digest"])
        self.assertIn("recorded: T-WASM-1's native side", self.log.read_text())
        self.assertIn(record["run_id"], self.log.read_text())
        self.assertRegex(record["run_id"], r"^P-0401-\w+$")
        # What the run keeps of itself, and wasm_report's refusal until it is whole (item 8).
        folder = self.run_folder()
        self.assertEqual(folder.name, record["run_id"])
        needs = ("machine_check.txt", "native.sampler.txt")
        self.assertEqual(run_record.missing(folder, needs, record["run_id"]),
                         ["measure.stderr, untouched"])
        kept = json.loads((folder / "run.json").read_text())
        probe = self.root / "probe"
        self.assertEqual(kept["binaries"], {str(bench): demo.sha256_of(bench),
                                            str(probe): demo.sha256_of(probe)})
        self.assertEqual(kept["dataset"]["name"], "extract.itch")
        self.assertEqual(kept["dataset"]["sha256"], self.manifest["extract_sha256"])
        stderr = self.write("native.stderr", "measure.sh: start\n")
        code, output = quietly(wasm_report.main, [
            "render", "--native", str(folder / "native.json"), "--native-stderr", str(stderr),
            "--browser", str(folder / "native.json"), "--browser-stderr", str(stderr),
            "--out", str(self.root / "out.md")])
        self.assertEqual(code, 1, output)
        self.assertIn("lacks measure.stderr, untouched", output)

    def test_a_foreign_process_withdraws_the_run(self):
        self.foreign = [(999_999, 1, 97.5, "build/fuzz/bin/some_fuzzer -max_total_time=300")]
        bench = self.executable("bench.py", FAKE_BENCH.format(python=sys.executable,
                                                              digest=self.manifest["digest"]))
        code, output = quietly(wasm_bench.main, ["native", "--bench", str(bench), *self.common])
        self.assertEqual(code, wasm_bench.WITHDRAWN, output)
        self.assertIn("withdrawn: T-WASM-1's native side; the CPU sampler saw some_fuzzer at "
                      "97.5% CPU", self.log.read_text())
        record = json.loads((self.run_folder() / "native.json").read_text())
        self.assertTrue(any("FOREIGN 97.5" in line for line in record["sampler"]["lines"]))
        self.assertIn("FOREIGN 97.5", (self.run_folder() / "native.sampler.txt").read_text())
        with self.assertRaisesRegex(wasm_report.ReportError, "native: not published under the "
                                                             "sampler rule: the CPU sampler saw"):
            wasm_report.sampler_line(record, "native")

    def test_native_refuses_another_book(self):
        bench = self.executable("bench.py", FAKE_BENCH.format(python=sys.executable,
                                                              digest="ffffffffffffffff"))
        code, output = quietly(wasm_bench.main, ["native", "--bench", str(bench), *self.common])
        self.assertEqual(code, 1, output)
        self.assertIn("refused at bench_demo: digest ffffffffffffffff", self.log.read_text())

    def test_a_changed_extract_is_refused_before_anything_runs(self):
        self.write("docs/local/extract.itch", "other frames")
        bench = self.executable("bench.py", FAKE_BENCH.format(python=sys.executable,
                                                              digest=self.manifest["digest"]))
        code, output = quietly(wasm_bench.main, ["native", "--bench", str(bench), *self.common])
        self.assertEqual(code, 1, output)
        self.assertIn("refused at extract", self.log.read_text())

    def test_an_unrecorded_prediction_is_refused(self):
        self.write("results/predictions.md", "# Predictions\n")
        bench = self.executable("bench.py", FAKE_BENCH.format(python=sys.executable,
                                                              digest=self.manifest["digest"]))
        code, output = quietly(wasm_bench.main, ["native", "--bench", str(bench), *self.common])
        self.assertEqual(code, 1, output)
        self.assertIn("refused at prediction", self.log.read_text())

    def browser(self, refusals="[]", after=1073741824):
        browser = self.executable("browser.py", FAKE_BROWSER.format(
            python=sys.executable, refusals=refusals, after=after))
        return quietly(wasm_bench.main, ["browser", "--browser", str(browser), "--timeout", "60",
                                         *self.common])

    def test_browser_takes_the_page_record_and_stops_the_browser(self):
        code, output = self.browser()
        self.assertEqual(code, 0, output)
        record = json.loads((self.run_folder() / "browser.json").read_text())
        self.assertEqual(record["browser"], "Fake Browser 1.0")
        self.assertEqual(len(record["repetitions"]), 3)
        self.assertIn("recorded: T-WASM-1's browser side", self.log.read_text())

    def test_browser_refuses_what_the_page_refused(self):
        code, output = self.browser(refusals='["the page failed: a test"]')
        self.assertEqual(code, 1, output)
        self.assertIn("refused at page: the page failed: a test", self.log.read_text())

    def test_browser_refuses_memory_that_grew(self):
        code, output = self.browser(after=2147483648)
        self.assertEqual(code, 1, output)
        self.assertIn("the engine's memory grew", self.log.read_text())

    def test_browser_refuses_an_engine_from_another_commit(self):
        info = json.loads((self.root / "docs/local/engine.json").read_text())
        info["commit"] = "000000000000 (clean)"
        self.write("docs/local/engine.json", json.dumps(info))
        code, output = self.browser()
        self.assertEqual(code, 1, output)
        self.assertIn("refused at engine: the engine was built from 000000000000", self.log.read_text())


def quiet_samples(first="2026-11-05T09:59:58Z", last="2026-11-05T10:05:02Z"):
    """A sampler's lines every 2 s from first to last, with nothing foreign."""
    at, end = wasm_bench.at(first), wasm_bench.at(last)
    lines = []
    while at <= end:
        lines.append(f"{at:%Y-%m-%dT%H:%M:%SZ} sample")
        at += datetime.timedelta(seconds=2)
    return lines


def records(browser_name="Google Chrome for Testing 153.0.8010.12", wasm_ms=None):
    """A native and a browser record, as wasm_bench.py writes them, and their stderr."""
    native_ns = [40_000_000 + 100_000 * i for i in range(5)]
    wasm_ms = wasm_ms or [52.0 + 0.2 * i for i in range(5)]
    manifest = {"symbol": "TEST", "day": "12302019.NASDAQ_ITCH50", "messages": 1_000_000,
                "bytes": 30_000_000, "digest": "00000000000000aa", "extract_sha256": "ab" * 32,
                "day_sha256": "cd" * 32}
    common = {"commit": "0123456789ab (clean)", "prediction": "P-0401",
              "run_started": "2026-11-05T10:00:00Z", "run_finished": "2026-11-05T10:05:00Z",
              "machine_check": "machine_check: pass", "extract_manifest": manifest,
              "digest": manifest["digest"]}
    def sampler():
        return {"every_s": 2.0, "foreign_cpu": 20.0, "longest_gap_s": 5, "lines": quiet_samples()}

    native = {**common, "sampler": sampler(), "side": "native", "run_id": "P-0401-20261105T100000Z_native",
              "command": "python3 -B scripts/wasm_bench.py native ...",
              "header": {"host": "macOS (M4 Max)", "cpu": "Apple M4 Max", "kernel": "Darwin 27.2.0",
                         "compiler": "Clang 23.1.4 (x)", "flags": "-O3 -DNDEBUG -std=c++23",
                         "timer": "mach_absolute_time", "quantum": "1 ticks, 41.667 ns",
                         "pinning": "not pinned"},
              "warmup": {"ns": 45_000_000},
              "repetitions": [{"ns": ns, "messages": 1_000_000} for ns in native_ns]}
    browser = {**common, "sampler": sampler(), "side": "browser", "run_id": "P-0401-20261105T100100Z_browser",
               "command": "python3 -B scripts/wasm_bench.py browser ...",
               "browser": browser_name, "browser_flags": ["--headless=new"],
               "user_agent": "Mozilla/5.0 HeadlessChrome/153.0.0.0", "cross_origin_isolated": True,
               "clock_step_ms": 0.005, "memory_bytes": 1 << 30, "memory_bytes_after": 1 << 30,
               "wasm_sha256": "ef" * 32,
               "engine_built": {"compiler": "em++, Emscripten 6.0.13", "flags": "-O3 -msimd128"},
               "extract": {"sha256": manifest["extract_sha256"], "messages": 1_000_000},
               "warmup": {"ms": 60.0},
               "repetitions": [{"ms": ms, "messages": 1_000_000} for ms in wasm_ms]}
    stderr = ("measure.sh: start load1=1.20 host=somewhere at=2026-11-05T10:00:00Z\n"
              "        734003200  maximum resident set size\n"
              "measure.sh: end load1=1.30 exit=0 at=2026-11-05T10:01:00Z\n")
    return native, browser, stderr


class Report(unittest.TestCase):
    LOG = "P-0401-20261105T100000Z_native P-0401-20261105T100100Z_browser"

    def render(self, **kwargs):
        native, browser, stderr = records(**kwargs)
        return wasm_report.render(native, browser, stderr, stderr)

    def violations(self, text):
        return list(wasm_report.check_text(text, self.LOG))

    def test_a_rendered_file_passes_its_check(self):
        text = self.render()
        self.assertEqual(self.violations(text), [])
        self.assertIn("| T-WASM-1 | ≤ 1.55× native, in Google Chrome | 1.30× | browser differs: "
                      "Google Chrome for Testing 153.0.8010.12, not Google Chrome", text)
        self.assertNotIn("somewhere", text)
        self.assertIn("measure.sh: start load1=1.20 at=2026-11-05T10:00:00Z", text)

    def test_times_past_the_microsecond_render_a_file_that_passes(self):
        native, browser, stderr = records(wasm_ms=[52.0001234 + 0.2 * i for i in range(5)])
        for i, repetition in enumerate(native["repetitions"]):
            repetition["ns"] = 40_000_000 + 100_000 * i + 377
        text = wasm_report.render(native, browser, stderr, stderr)
        self.assertEqual(self.violations(text), [])

    def test_rates_come_from_the_raw_times_and_are_rounded_last(self):
        # 40,000,377 ns and on: the table prints 40.000 ms, but the rate is 1,000,000 messages
        # over the time measured, 24,999,764 messages/s, not 25,000,000.
        native, browser, stderr = records()
        for i, repetition in enumerate(native["repetitions"]):
            repetition["ns"] = 40_000_377 + 100_000 * i
        text = wasm_report.render(native, browser, stderr, stderr)
        self.assertIn("| Native, messages/s | 24,875,389 | 24,752,244 | 24,999,764 | ", text)
        self.assertIn("| 1 | 40.000 | 52.000 |", text)
        self.assertEqual(self.violations(text), [])
        again = wasm_report.render(native, browser, stderr, stderr, again="why.")
        self.assertIn("\n\nRendered again from the run's own raw records, unchanged: why.\n\n",
                      again)
        self.assertEqual(self.violations(again), [])

    def test_render_refuses_a_side_the_sampler_saw_shared(self):
        native, browser, stderr = records()
        browser["sampler"]["lines"].insert(
            60, "2026-11-05T10:01:58Z FOREIGN 99.0 build/fuzz/bin/some_fuzzer -runs=0")
        with self.assertRaisesRegex(wasm_report.ReportError,
                                    "browser: not published under the sampler rule: the CPU "
                                    "sampler saw some_fuzzer at 99.0% CPU"):
            wasm_report.render(native, browser, stderr, stderr)

    def test_a_foreign_process_outside_the_span_does_not_count(self):
        native, browser, stderr = records()
        native["sampler"]["lines"].insert(0, "2026-11-05T09:59:58Z FOREIGN 99.0 some_fuzzer")
        self.assertEqual(self.violations(wasm_report.render(native, browser, stderr, stderr)), [])

    def test_render_refuses_a_gap_in_the_sampler(self):
        native, browser, stderr = records()
        del native["sampler"]["lines"][40:44]
        with self.assertRaisesRegex(wasm_report.ReportError,
                                    "native: not published under the sampler rule: the CPU "
                                    "sampler went 10 s without a sample"):
            wasm_report.render(native, browser, stderr, stderr)

    def test_render_refuses_a_side_with_no_sampler_or_one_that_stopped_early(self):
        native, browser, stderr = records()
        del native["sampler"]
        with self.assertRaisesRegex(wasm_report.ReportError, "native: no CPU sampler"):
            wasm_report.render(native, browser, stderr, stderr)
        native, browser, stderr = records()
        browser["sampler"]["lines"] = quiet_samples(last="2026-11-05T10:04:58Z")
        with self.assertRaisesRegex(wasm_report.ReportError, "did not cover the whole span"):
            wasm_report.render(native, browser, stderr, stderr)

    def test_check_catches_a_sampler_line_that_is_not_quiet(self):
        text = self.render()
        self.assertIn(wasm_report.SAMPLER_RULE, text)
        self.assertIn("- browser_sampler: quiet: 153 samples from 2026-11-05T09:59:58Z", text)
        edited = text.replace("- browser_sampler: quiet: ", "- browser_sampler: shared: ")
        self.assertIn("sampler-not-quiet", {c for c, _ in self.violations(edited)})
        dropped = text.replace(wasm_report.SAMPLER_RULE, "")
        self.assertIn("sampler-rule-missing", {c for c, _ in self.violations(dropped)})

    def test_own_processes_and_their_wrappers_are_not_foreign(self):
        rows = [(1, 0, 0.0, "launchd"),
                (10, 1, 0.1, "bash measure.sh /usr/bin/time -l python3 wasm_bench.py"),
                (11, 10, 0.0, "/usr/bin/time -l python3 wasm_bench.py"),
                (12, 11, 30.0, "python3 wasm_bench.py browser"),
                (13, 12, 99.0, "Google Chrome for Testing --headless=new"),
                (14, 13, 98.0, "Google Chrome for Testing Helper (Renderer) --type=renderer"),
                (20, 1, 45.0, "build/fuzz/bin/other_fuzzer"),
                (21, 1, 15.0, "below the bar")]
        lines = wasm_bench.sample_lines(rows, 12, "2026-11-05T10:00:00Z")
        self.assertEqual(lines, ["2026-11-05T10:00:00Z sample",
                                 "2026-11-05T10:00:00Z FOREIGN 45.0 build/fuzz/bin/other_fuzzer"])

    def test_google_chrome_itself_is_held_to_the_bar(self):
        met = self.render(browser_name="Google Chrome 153.0.8010.12")
        self.assertRegex(met, r"\| 1\.30× \| met \|")
        missed = self.render(browser_name="Google Chrome 153.0.8010.12", wasm_ms=[80.0] * 5)
        self.assertRegex(missed, r"\| 1\.99× \| missed: 1\.99× native, over the 1\.55× bar")
        self.assertEqual(self.violations(met), [])
        self.assertEqual(self.violations(missed), [])

    def test_check_catches_an_edited_repetition(self):
        text = self.render().replace("| 3 | 40.200 | 52.400 |", "| 3 | 40.200 | 50.400 |")
        codes = {code for code, _ in self.violations(text)}
        self.assertIn("summary-differs", codes)

    def test_check_catches_an_edited_verdict_or_ratio(self):
        text = self.render()
        self.assertIn("verdict-differs", {c for c, _ in self.violations(
            text.replace("| browser differs: ", "| met: "))})
        self.assertIn("ratio-differs", {c for c, _ in self.violations(
            text.replace("| 1.30× |", "| 1.20× |"))})

    def test_check_catches_a_percentile_row_and_unstamped_load(self):
        text = self.render()
        with_row = text.replace("## Target", "| p50 | 1 | 1 | 1 | 0% |\n\n## Target")
        self.assertIn("per-message-figure", {c for c, _ in self.violations(with_row)})
        pending = text.replace("- browser_load_end: measure.sh:", "- browser_load_end: pending")
        self.assertIn("load-not-stamped", {c for c, _ in self.violations(pending)})
        self.assertIn("run-missing-from-log",
                      {c for c, _ in wasm_report.check_text(text, "")})

    def test_render_refuses_records_that_do_not_pair(self):
        native, browser, stderr = records()
        browser["digest"] = "ffffffffffffffff"
        with self.assertRaisesRegex(wasm_report.ReportError, "different books"):
            wasm_report.render(native, browser, stderr, stderr)
        native, browser, stderr = records()
        browser["extract"]["sha256"] = "00" * 32
        with self.assertRaisesRegex(wasm_report.ReportError, "different extracts"):
            wasm_report.render(native, browser, stderr, stderr)
        native, browser, stderr = records()
        native["commit"] = browser["commit"] = "0123456789ab (with uncommitted changes)"
        with self.assertRaisesRegex(wasm_report.ReportError, "not one clean commit"):
            wasm_report.render(native, browser, stderr, stderr)
        native, browser, stderr = records()
        with self.assertRaisesRegex(wasm_report.ReportError, "exit=0"):
            wasm_report.render(native, browser, stderr.replace("exit=0", "exit=1"), stderr)

    def test_the_committed_files_pass(self):
        code, output = quietly(wasm_report.main, ["check"])
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
