#!/usr/bin/env python3
"""Tests for udp_driver.py's own decisions, with no real benchmark: the bisection and its cap,
its floor and its step limit; when a trial counts as lossless; the feed's speedups, flush gaps
and timeouts; the latency step's rates; the readings; each process's command; and, with a
stand-in measure.sh and bench_udp written under build/, the manifest a step leaves, its
retries on exit 75 and the run log's row for a process that failed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_udp_report as records  # noqa: E402
import run_record  # noqa: E402
import simd_driver  # noqa: E402
import udp_driver  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent

# measure.sh's stand-in: drops taskset's mask (macOS has no taskset), exits 75 once if
# FAKE_BUSY names a file not yet there, and writes the start and end lines around the rest.
FAKE_MEASURE = """#!/usr/bin/env python3
import datetime, os, subprocess, sys
args = sys.argv[1:]
if args[:1] == ["taskset"]:
    args = args[3:]
busy = os.environ.get("FAKE_BUSY")
if busy and not os.path.exists(busy):
    open(busy, "w").close()
    print("measure.sh: lock busy for 1s; not measuring", file=sys.stderr)
    sys.exit(75)
def now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"measure.sh: start load1=0.10 host=box at={now()}", file=sys.stderr, flush=True)
code = subprocess.run(args).returncode
print(f"measure.sh: end load1=0.10 exit={code} at={now()}", file=sys.stderr)
sys.exit(code)
"""

# bench_udp's stand-in: lossless up to FAKE_THRESHOLD msg/s, the sender at FAKE_PACE of the
# target, the mode FAKE_BROKEN failing to set up, and the sender alone at FAKE_CEILING.
FAKE_BENCH = """#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, {scripts!r})
import test_udp_report as records
args = sys.argv[2:]
options = dict(zip(args[::2], args[1::2]))
mode = options["--mode"]
if mode == os.environ.get("FAKE_BROKEN"):
    print("bench_udp: io_uring_queue_init_params failed: errno 12", file=sys.stderr)
    sys.exit(2)
if mode == "sender":
    record = records.sender_record(float(os.environ.get("FAKE_CEILING", "3000000")))
    if options["--out"].endswith(os.environ.get("FAKE_SENDER_FAIL", "none") + ".json"):
        record["verdict"] = "FAIL"
else:
    rate = int(options.get("--rate", "0"))
    ok = rate <= int(os.environ.get("FAKE_THRESHOLD", "1000000"))
    achieved = rate * float(os.environ.get("FAKE_PACE", "1.0"))
    record = records.receive_record(mode, rate, achieved=achieved, lossless=ok)
with open(options["--out"], "w") as out:
    json.dump(record, out)
print("bench_udp_verdict", record["verdict"])
sys.exit(0 if record["verdict"] == "PASS" else 1)
"""


def write_script(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


class Environment:
    """Sets environment variables for one test and puts the old ones back."""

    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = str(value)

    def __exit__(self, *_):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def searched(limit, cap=2_700_000, start=100_000, floor=1_000, most_steps=10):
    search = udp_driver.Bisection(cap, start, floor, most_steps)
    while (rate := search.next_rate()) is not None:
        search.record(rate, rate <= limit)
    return search.result()


class BisectionTest(unittest.TestCase):
    def test_it_closes_in_on_the_largest_lossless_rate(self):
        result = searched(1_234_567)
        self.assertEqual(result["result"], "max")
        self.assertLessEqual(result["rate"], 1_234_567)
        self.assertGreater(result["lossy_above"], 1_234_567)
        self.assertTrue(result["lossy_above"] / result["rate"] <= udp_driver.TOLERANCE
                        or result["steps"] == 10)
        self.assertEqual(result["tried"][:2], [[100_000, True], [2_700_000, False]])

    def test_no_trial_runs_above_the_cap_and_lossless_there_is_sender_limited(self):
        result = searched(10_000_000)
        self.assertEqual(result["result"], "sender limited")
        self.assertEqual(result["rate"], 2_700_000)
        self.assertEqual([rate for rate, _ in result["tried"]], [100_000, 2_700_000])
        result = searched(10_000_000, start=5_000_000)
        self.assertEqual(result["tried"], [[2_700_000, True]])
        self.assertEqual(result["result"], "sender limited")
        for limit in (50_000, 700_000, 2_699_999):
            self.assertTrue(all(rate <= 2_700_000 for rate, _ in searched(limit)["tried"]))

    def test_a_lossy_start_is_halved_down_to_the_floor(self):
        result = searched(30_000)
        self.assertEqual(result["tried"][:3], [[100_000, False], [50_000, False],
                                               [25_000, True]])
        self.assertEqual(result["result"], "max")
        self.assertTrue(25_000 <= result["rate"] <= 30_000 < result["lossy_above"] <= 50_000)
        result = searched(500)
        self.assertEqual(result["result"], "below")
        self.assertEqual(result["rate"], 1_562)
        self.assertEqual([rate for rate, _ in result["tried"]][-1], 1_562)
        self.assertTrue(all(rate >= 1_000 for rate, _ in result["tried"]))

    def test_it_stops_after_the_most_steps(self):
        search = udp_driver.Bisection(2_700_000, 100_000, 1_000, most_steps=3, tolerance=1.0)
        while (rate := search.next_rate()) is not None:
            search.record(rate, rate <= 1_000_000)
        self.assertEqual(search.result()["steps"], 3)
        self.assertEqual(len(search.result()["tried"]), 5)

    def test_the_best_mode_counts_the_cap_and_breaks_ties_in_order(self):
        results = {"U2": {"result": "max", "rate": 900}, "S3": {"result": "max", "rate": 900},
                   "S1": {"result": "below", "rate": 5_000},
                   "U5": {"result": "failed", "rate": None}}
        self.assertEqual(udp_driver.best_mode(results), "S3")
        results["U4"] = {"result": "sender limited", "rate": 2_700}
        self.assertEqual(udp_driver.best_mode(results), "U4")
        self.assertIsNone(udp_driver.best_mode({"S1": {"result": "below", "rate": 1}}))

    def test_the_ceiling_and_its_cap(self):
        # The median of the unrounded rates: rounding each first would give 3,000,000 and 10.
        self.assertEqual(udp_driver.ceiling_of([3_000_000.4, 2_999_999.6, 1.0]), 2_999_999.6)
        self.assertEqual(udp_driver.ceiling_of([10.25, 11.5]), 10.875)
        self.assertEqual(udp_driver.cap_of(3_000_001), 2_700_000)
        self.assertEqual(udp_driver.cap_of(2_999_999.6), 2_699_999)
        self.assertEqual(udp_driver.frames_for(2_547_190, 60, 50_000_000), 50_000_000)
        self.assertEqual(udp_driver.frames_for(1_000, 60, 50_000_000), 60_000)


class TrialTest(unittest.TestCase):
    def test_a_lossless_record_with_the_sender_in_pace_counts(self):
        self.assertEqual(udp_driver.trial_lossless(records.receive_record("U2", 1_000_000),
                                                   1_000_000), (True, None))
        record = records.receive_record("U2", 1_000_000, achieved=990_000)
        self.assertTrue(udp_driver.trial_lossless(record, 1_000_000)[0])

    def test_a_sender_that_fell_behind_does_not_count(self):
        record = records.receive_record("U2", 1_000_000, achieved=989_000)
        ok, why = udp_driver.trial_lossless(record, 1_000_000)
        self.assertFalse(ok)
        self.assertIn("the sender fell behind: 989,000 of 1,000,000", why)

    def test_every_counter_is_read_not_the_flag_alone(self):
        # Every layer of IU-43, named here rather than taken from the driver's own list.
        for name in ("failed_sends", "tracker_gaps", "tracker_lost", "tracker_duplicates",
                     "truncated", "no_buffers", "cq_overflow", "socket_drops", "rcvbuf_errors",
                     "in_errors", "softnet_dropped"):
            record = records.receive_record("S3", 500_000, drops={name: 3}, claim=True)
            ok, why = udp_driver.trial_lossless(record, 500_000)
            self.assertFalse(ok, name)
            self.assertIn("calls itself lossless", why)
        record = records.receive_record("S3", 500_000, drops={"socket_drops": None}, claim=True)
        self.assertFalse(udp_driver.trial_lossless(record, 500_000)[0])
        record = records.receive_record("U3", 500_000, drops={"rxq_overflow": 4}, claim=True)
        self.assertFalse(udp_driver.trial_lossless(record, 500_000)[0])
        record = records.receive_record("S3", 500_000)
        self.assertIsNone(record["drops"]["rxq_overflow"])
        self.assertTrue(udp_driver.trial_lossless(record, 500_000)[0])

    def test_a_record_not_lossless_or_failed_does_not_count(self):
        self.assertFalse(udp_driver.trial_lossless(
            records.receive_record("S3", 500_000, lossless=False), 500_000)[0])
        self.assertFalse(udp_driver.trial_lossless(
            records.receive_record("S3", 500_000, verdict="FAIL"), 500_000)[0])
        record = records.receive_record("S3", 500_000)
        record["delivered"] = False
        self.assertFalse(udp_driver.trial_lossless(record, 500_000)[0])
        self.assertFalse(udp_driver.trial_lossless(records.sender_record(1e6), 1)[0])


class FeedAndLatencyTest(unittest.TestCase):
    def test_the_speedup_is_the_target_over_the_peak_rate(self):
        self.assertAlmostEqual(udp_driver.speedup_for(1_305_440, 400_000, 1.0), 3.2636)
        self.assertAlmostEqual(udp_driver.speedup_for(2_547_190, 1_000, 0.001), 2.54719)
        self.assertAlmostEqual(udp_driver.speedup_for(2_547_190, 5_000, 0.001), 0.509438)
        with self.assertRaises(udp_driver.Refused):
            udp_driver.speedup_for(1, 0, 1.0)

    def test_the_flush_gap_and_timeout_scale_with_the_speedup(self):
        self.assertEqual(udp_driver.flush_gap_ns(100, 1.0), 100_000)
        self.assertEqual(udp_driver.flush_gap_ns(100, 3.2636), 326_360)
        self.assertEqual(udp_driver.timeout_ms(3_000_000, 1.0), 2000)
        self.assertEqual(udp_driver.timeout_ms(30_000_000_000, 1.0), 61_000)
        self.assertEqual(udp_driver.timeout_ms(30_000_000_000, 10.0), 7_000)

    def test_the_peaks_are_read_from_bench_udp(self):
        text = ("bench_udp_peaks_messages 3330\n"
                "bench_udp_peak_1s 3313 messages from frame 14 at 34200000001000 ns\n"
                "bench_udp_peak_1ms 1000 messages from frame 15 at 34200000002000 ns\n")
        self.assertEqual(udp_driver.parse_peaks(text),
                         {"1s": (3313, 14, 34200000001000), "1ms": (1000, 15, 34200000002000)})
        with self.assertRaises(udp_driver.Refused):
            udp_driver.parse_peaks(text.splitlines()[1])

    def test_the_window_s_gaps_are_walked_as_the_file_is(self):
        def frame(stamp):
            payload = b"A" + b"\0" * 4 + stamp.to_bytes(6, "big") + b"\0" * 25
            return len(payload).to_bytes(2, "big") + payload
        stamps = [1_000, 2_000, 9_000, 10_000, 50_000, 51_000]
        data = b"".join(frame(stamp) for stamp in stamps[:3]) + b"\0\0" + \
            b"".join(frame(stamp) for stamp in stamps[3:])
        with records.scratch() as folder:
            day = Path(folder) / "day.itch"
            day.write_bytes(data)
            self.assertEqual(udp_driver.window_gaps(day, 0, 100), (50_000, 40_000))
            # Frame 3 is the zero-length one: it counts as a frame and has no time.
            self.assertEqual(udp_driver.window_gaps(day, 1, 4), (8_000, 7_000))
            self.assertEqual(udp_driver.window_gaps(day, 4, 2), (40_000, 40_000))

    def test_the_latency_rates(self):
        results = {"S3": {"result": "max", "rate": 1_000_000},
                   "U5": {"result": "sender limited", "rate": 2_700_000},
                   "S1": {"result": "below", "rate": 1_562}}
        rates = udp_driver.latency_rates(results, [0.1, 0.5, 0.8], 1000, 40.5, 500_000,
                                         2_700_000)
        self.assertEqual(rates["S3"], [("latency-low", 40_500), ("latency-0.1", 100_000),
                                       ("latency-0.5", 500_000), ("latency-0.8", 800_000),
                                       ("cpu", 500_000)])
        self.assertEqual(rates["U5"][3], ("latency-0.8", 2_160_000))
        self.assertEqual(rates["S1"], [("latency-low", 40_500), ("cpu", 500_000)])
        with self.assertRaisesRegex(udp_driver.Refused, "above the cap"):
            udp_driver.latency_rates(results, [0.5], 1000, 40.5, 2_800_000, 2_700_000)
        no_cpu = udp_driver.latency_rates(results, [0.5], 1000, 40.5, None, 2_700_000)
        self.assertEqual(no_cpu["S1"], [("latency-low", 40_500)])

    def test_messages_a_packet_leave_out_heartbeats_and_the_end(self):
        one = records.receive_record("S3", 100_000, messages=4_000)
        one["plan"].update(messages=4_000, packets=102, heartbeats=1)
        self.assertEqual(udp_driver.messages_per_packet([one]), 40.0)
        with self.assertRaises(udp_driver.Refused):
            udp_driver.messages_per_packet([])


class ReadingsTest(unittest.TestCase):
    def test_a_reading_that_cannot_be_had_is_recorded_so(self):
        with records.scratch() as folder:
            root = Path(folder)
            (root / "proc/sys/kernel").mkdir(parents=True)
            (root / "proc/sys/kernel/perf_event_paranoid").write_text("2\n")
            (root / "proc/sys/kernel/io_uring_disabled").write_text("0\n")
            (root / "proc/uptime").write_text("12345.67 99999.00\n")
            (root / "proc/vmstat").write_text("pswpin 3\npswpout 17\n")
            (root / "boot").mkdir()
            (root / "boot/config-6.12.0").write_text("# CONFIG_IRQ_TIME_ACCOUNTING is not set\n")
            vulnerabilities = root / "sys/devices/system/cpu/vulnerabilities"
            vulnerabilities.mkdir(parents=True)
            (vulnerabilities / "retbleed").write_text("Mitigation: untrained return thunk\n")
            self.assertEqual(udp_driver.state_readings(root),
                             {"perf_event_paranoid": "2", "uptime_s": "12345.67",
                              "pswpout": "17"})
            out = udp_driver.smoke_readings({"sender": 4, "receiver": 6, "sq": 2}, root,
                                            release="6.12.0")
            self.assertEqual(out["io_uring_disabled"], "0")
            self.assertTrue(out["io_uring_group"].startswith("unreadable: "))
            self.assertTrue(out["rmem_max"].startswith("unreadable: "))
            self.assertTrue(out["siblings_cpu6"].startswith("unreadable: "))
            self.assertEqual(out["irq_time_accounting"], "not set")
            self.assertEqual(out["vulnerabilities"],
                             {"retbleed": "Mitigation: untrained return thunk"})
            out = udp_driver.smoke_readings({"sender": 4}, root, release="6.13.0")
            self.assertEqual(out["irq_time_accounting"], "unreadable")
        self.assertEqual(udp_driver.irq_time_accounting("CONFIG_IRQ_TIME_ACCOUNTING=y\n"), "y")
        self.assertEqual(udp_driver.irq_time_accounting("CONFIG_X=y\n"), "not in the config")
        self.assertEqual(udp_driver.vmstat_value("pswpin 1\n", "pswpout"),
                         "unreadable: no pswpout line")


def arguments(folder, **more):
    values = {"step": "bisect", "prediction": "P-0700", "raw_dir": folder / "raw",
              "input": folder / "day.itch", "dataset": "12302019.NASDAQ_ITCH50",
              "dataset_sha256": "ab" * 32, "bench": folder / "bench_udp",
              "probe": folder / "bench_probe", "measure": folder / "measure.sh",
              "heavy": None, "loopback": None, "modes": ["S3", "U2", "U5"], "repetitions": 2,
              "seed": 11, "core": 4, "receiver_core": 6, "sq_core": 2, "first": 0,
              "frames_cap": 50_000_000, "trial_seconds": 60.0, "datagram": 1400,
              "ceiling": 3_000_000, "ceiling_from": None, "start_rate": 100_000,
              "floor_rate": 1_000, "most_steps": 10, "run_log": folder / "run_log.md"}
    values.update(more)
    return argparse.Namespace(**values)


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.saved_wait = udp_driver.RETRY_BUSY_S
        udp_driver.RETRY_BUSY_S = 0

    def tearDown(self):
        udp_driver.RETRY_BUSY_S = self.saved_wait

    def stand_ins(self, folder):
        write_script(folder / "measure.sh", FAKE_MEASURE)
        write_script(folder / "bench_udp", FAKE_BENCH.format(scripts=str(SCRIPTS)))
        # A file the run hashes for its record (gate item 8); the stand-ins never run it.
        (folder / "bench_probe").write_text("a probe\n", encoding="utf-8")
        (folder / "run_log.md").write_text("", encoding="utf-8")

    def test_each_process_is_pinned_under_measure_sh(self):
        args = arguments(Path("/q"), measure=Path("/q/bin/measure.sh"),
                         bench=Path("/q/bench_udp"), input=Path("/q/day"))
        words = udp_driver.bench_words(args, "U5", ["--rate", "1000"], "/q/raw/x", "abc (clean)")
        self.assertEqual(words[:12], ["/q/bin/measure.sh", "taskset", "-c", "4,6,2",
                                      "/q/bench_udp", "/q/day", "--mode", "U5", "--rate", "1000",
                                      "--core", "4"])
        self.assertEqual(words[12:16], ["--receiver-core", "6", "--sq-core", "2"])
        self.assertEqual(words[-8:], ["--commit", "abc (clean)", "--dataset",
                                      "12302019.NASDAQ_ITCH50", "--dataset-sha256", "ab" * 32,
                                      "--out", "/q/raw/x.json"])
        words = udp_driver.bench_words(args, "S3", [], "/q/raw/x", "abc")
        self.assertEqual(words[3], "4,6")
        self.assertNotIn("--sq-core", words)
        words = udp_driver.bench_words(args, "sender", [], "/q/raw/x", "abc")
        self.assertEqual(words[3], "4,6")
        self.assertNotIn("--receiver-core", words)

    def test_the_smoke_runs_are_paced_under_heavy_sh(self):
        # Unpaced, a sender outruns a receiver that applies every message to the book, and a
        # mode that delivers would fail the smoke for want of pace.
        args = arguments(Path("/q"), heavy=Path("/q/bin/heavy.sh"),
                         loopback=Path("/q/itch_loopback"), input=Path("/q/day"),
                         smoke_frames=1_000_000, smoke_rate=1_000_000)
        words = udp_driver.smoke_words(args, "U5")
        self.assertEqual(words[:5], ["/q/bin/heavy.sh", "/q/itch_loopback", "/q/day", "--mode",
                                     "U5"])
        self.assertEqual(words[words.index("--rate") + 1], "1000000")
        self.assertEqual(words[-2:], ["--sq-core", "2"])
        self.assertNotIn("--sq-core", udp_driver.smoke_words(args, "S3"))

    def test_stdout_is_the_run_id_alone(self):
        # A launcher takes the run id from the driver's stdout, so check_predictions' line
        # goes to stderr.
        source = (SCRIPTS / "udp_driver.py").read_text(encoding="utf-8")
        call = source[source.index('checker = REPO_ROOT / "scripts" / "check_predictions.py"'):]
        call = call[:call.index("run = run_step(")]
        self.assertIn("capture_output=True", call)
        self.assertIn("file=sys.stderr", call)

    def test_a_step_keeps_its_record_and_will_not_write_over_another(self):
        """Gate item 8: the run's folder is named by its run id and holds run.json."""
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            (folder / "day.itch").write_bytes(b"a day")
            digest = run_record.sha256_of(folder / "day.itch")
            args = arguments(folder, dataset_sha256=digest)
            run = udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")
            saved = simd_driver.machine_check
            simd_driver.machine_check = lambda args: "machine_check: passed"
            try:
                run.check_machine("before")
            finally:
                simd_driver.machine_check = saved
            self.assertEqual(run.dir.name, "P-0700-20261111T100000Z")
            kept = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(kept["binaries"], {
                str(folder / name): run_record.sha256_of(folder / name)
                for name in ("bench_udp", "bench_probe", "measure.sh")})
            self.assertEqual(kept["dataset"], {"name": "day.itch", "sha256": digest,
                                               "manifest": "not pinned in "
                                                           "scripts/data_manifest.json"})
            self.assertEqual(run_record.kept_output(run.dir, "machine_check_before.txt"),
                             "machine_check: passed")
            with self.assertRaisesRegex(run_record.Incomplete, "exists and is not empty"):
                udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")

    def test_a_bisect_step_leaves_its_manifest_and_confirms_t_uring_2(self):
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            args = arguments(folder)
            with Environment(FAKE_THRESHOLD=2_600_000, FAKE_BUSY=folder / "busy"):
                run = udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")
                udp_driver.step_bisect(run)
            manifest = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rate_cap"], 2_700_000)
            self.assertEqual(manifest["seed"], 11)
            self.assertEqual(manifest["ceiling"]["source"], "given on the command line")
            for mode in ("S3", "U2", "U5"):
                self.assertEqual(manifest["bisect"][mode]["result"], "max")
                self.assertLessEqual(manifest["bisect"][mode]["rate"], 2_600_000)
            self.assertEqual(manifest["best_mode"], "S3")
            self.assertEqual(manifest["t_uring_2"], {"tested": True, "mode": "S3",
                                                     "rate": 2_547_190, "lossless": 2,
                                                     "repetitions": 2})
            first = manifest["processes"][0]
            self.assertEqual(first["exit"], 0)
            self.assertEqual(first["outcome"], "result")
            self.assertTrue(first["trial_lossless"])
            self.assertTrue(all(p["target_rate"] <= 2_700_000 for p in manifest["processes"]))
            confirms = [p for p in manifest["processes"] if p["role"] == "confirm"]
            self.assertEqual(len(confirms), 2)
            self.assertIn("--frames", confirms[0]["extra"])
            self.assertEqual(confirms[0]["extra"][confirms[0]["extra"].index("--frames") + 1],
                             "50000000")
            self.assertTrue((folder / "busy").exists())
            self.assertEqual((folder / "run_log.md").read_text(encoding="utf-8"), "")
            stems = [p["stem"] for p in manifest["processes"]]
            self.assertEqual(len(set(stems)), len(stems))

    def test_a_bisection_off_t_uring_2_s_packet_size_confirms_nothing(self):
        # T-URING-2 is judged at 1,400 bytes a datagram (P-0902): the small-packet arm of
        # T-URING-1 searches as the 1,400-byte one does, and runs no confirmation.
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            args = arguments(folder, datagram=72)
            with Environment(FAKE_THRESHOLD=2_600_000):
                run = udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")
                udp_driver.step_bisect(run)
            manifest = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["datagram"], 72)
            self.assertEqual(manifest["best_mode"], "S3")
            self.assertEqual(manifest["t_uring_2"]["tested"], False)
            self.assertIn("judged at 1,400 bytes a datagram", manifest["t_uring_2"]["why"])
            self.assertEqual([p for p in manifest["processes"] if p["role"] == "confirm"], [])
            trial = manifest["processes"][0]["extra"]
            self.assertEqual(trial[trial.index("--datagram") + 1], "72")

    def test_a_sender_behind_never_counts_and_a_failed_mode_is_logged(self):
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            args = arguments(folder)
            with Environment(FAKE_THRESHOLD=10_000_000, FAKE_PACE=0.98, FAKE_BROKEN="U5"):
                run = udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")
                udp_driver.step_bisect(run)
            manifest = run.manifest
            self.assertEqual(manifest["bisect"]["S3"]["result"], "below")
            self.assertIn("the sender fell behind",
                          manifest["processes"][0]["trial_why"])
            self.assertEqual(manifest["bisect"]["U5"]["result"], "failed")
            self.assertIn("errno 12", manifest["bisect"]["U5"]["why"])
            self.assertIsNone(manifest["best_mode"])
            self.assertEqual(manifest["t_uring_2"]["tested"], False)
            log = (folder / "run_log.md").read_text(encoding="utf-8")
            self.assertEqual(log.count("| P-0700-20261111T100000Z_bisect-U5-"), 1)
            self.assertIn("failed: udp bisect, U5 trial", log)
            self.assertEqual(run.failed, 1)

    def test_the_target_above_the_cap_is_never_run(self):
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            args = arguments(folder, ceiling=2_500_000, modes=["S3"])
            with Environment(FAKE_THRESHOLD=10_000_000):
                run = udp_driver.Run(args, "P-0700-20261111T100000Z", "0123456789ab (clean)")
                udp_driver.step_bisect(run)
            self.assertEqual(run.manifest["bisect"]["S3"]["result"], "sender limited")
            self.assertEqual(run.manifest["bisect"]["S3"]["rate"], 2_250_000)
            self.assertFalse(run.manifest["t_uring_2"]["tested"])
            self.assertIn("above the cap 2,250,000", run.manifest["t_uring_2"]["why"])
            self.assertFalse(any(p["role"] == "confirm" for p in run.manifest["processes"]))

    def test_a_ceiling_step_records_the_median(self):
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            args = arguments(folder, step="ceiling", repetitions=3)
            with Environment(FAKE_CEILING=3_100_000.4):
                run = udp_driver.Run(args, "P-0700-20261111T090000Z", "0123456789ab (clean)")
                udp_driver.step_ceiling(run)
            self.assertEqual(run.manifest["ceiling"]["messages_per_s"], 3_100_000.4)
            self.assertEqual(run.manifest["ceiling"]["runs"], 3)
            self.assertEqual([p["mode"] for p in run.manifest["processes"]], ["sender"] * 3)
            later = arguments(folder, ceiling=None, ceiling_from=run.dir)
            self.assertEqual(udp_driver.given_ceiling(later)["messages_per_s"], 3_100_000.4)
            self.assertEqual(udp_driver.cap_of(3_100_000.4), 2_790_000)
            with self.assertRaises(udp_driver.Refused):
                udp_driver.given_ceiling(arguments(folder, ceiling=None))

    def test_a_sender_run_that_did_not_pass_refuses_the_ceiling_step(self):
        """r5-14: never a median of the runs that passed; the step is refused, and the run
        log's row says which run and why."""
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            (folder / "day.itch").write_bytes(b"a day")
            argv = ["--step", "ceiling", "--prediction", "P-0901", "--raw-dir",
                    str(folder / "raw"), "--input", str(folder / "day.itch"), "--dataset", "d",
                    "--dataset-sha256", run_record.sha256_of(folder / "day.itch"), "--bench",
                    str(folder / "bench_udp"), "--probe", str(folder / "bench_probe"),
                    "--measure", str(folder / "measure.sh"), "--repetitions", "3",
                    "--run-log", str(folder / "run_log.md"), "--allow-dirty"]
            system, check = udp_driver.platform.system, simd_driver.machine_check
            udp_driver.platform.system = lambda: "Linux"
            simd_driver.machine_check = lambda args: "machine_check: passed"
            try:
                with Environment(FAKE_SENDER_FAIL="0002"):
                    code = udp_driver.main(argv)
            finally:
                udp_driver.platform.system, simd_driver.machine_check = system, check
            log = (folder / "run_log.md").read_text(encoding="utf-8")
            manifest = json.loads(next((folder / "raw").glob("*/manifest.json")).read_text(
                encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertIn("refused: udp ceiling: sender run 2 (ceiling-sender-0002) gave "
                      "the verdict 'FAIL', not PASS; the ceiling is the median of every run, so "
                      "the step is refused", log)
        self.assertNotIn("ceiling", manifest)
        self.assertEqual(len(manifest["processes"]), 2, "no run after the one that failed")

    def test_a_warning_heads_the_manifest_when_perf_is_closed(self):
        with records.scratch() as folder:
            folder = Path(folder)
            self.stand_ins(folder)
            run = udp_driver.Run(arguments(folder), "P-0700-20261111T100000Z", "x")
            saved = udp_driver.state_readings
            udp_driver.state_readings = lambda: {"perf_event_paranoid": "3", "uptime_s": "1",
                                                 "pswpout": "0"}
            try:
                run.read_state("after")
            finally:
                udp_driver.state_readings = saved
            run.save()
            text = (run.dir / "manifest.json").read_text(encoding="utf-8")
            self.assertEqual(next(iter(json.loads(text))), "warning")
            self.assertIn("perf_event_paranoid read 3 after the step", text)

    def test_the_driver_refuses_to_run_off_linux(self):
        if udp_driver.platform.system() == "Linux":
            self.skipTest("this host is Linux")
        with records.scratch() as folder:
            folder = Path(folder)
            argv = ["--step", "ceiling", "--prediction", "P-0700", "--raw-dir", str(folder),
                    "--input", "day", "--dataset", "d", "--dataset-sha256", "h", "--bench", "b",
                    "--probe", "p", "--measure", "m"]
            self.assertEqual(udp_driver.main(argv), 2)
            self.assertEqual(list(folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
