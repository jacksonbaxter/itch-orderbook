#!/usr/bin/env python3
"""Tests for udp_report.py, on runs invented here and written under build/: the sender's
ceiling, each mode's largest lossless rate, T-URING-1's ratios and T-URING-2's verdict worked
out by hand; the feed's speedups and the latency and CPU tables; results_table.py's rows
from a file; and every refusal of --check. The record builders here are the ones
test_udp_driver.py's stand-in bench_udp writes with."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import results_table  # noqa: E402
import udp_driver  # noqa: E402
import udp_report  # noqa: E402

SCRATCH = udp_driver.REPO_ROOT / "build" / "scratch-udp"
CORES = {"sender": 4, "receiver": 6, "sq": 2}
CHECK = ("machine_check: record host debian\nmachine_check: pass pin-readback: allowed on "
         "exactly core 4\nmachine_check: passed")
NAME = "udp_bisect_debian_12302019.md"
TARGET = udp_driver.TARGET_1MS


def scratch():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def header():
    return {"host": "debian", "cpu": "AMD Ryzen Embedded V1605B with Radeon Vega Gfx",
            "kernel": "Linux 6.12.48+deb13-amd64", "compiler": "Clang 21.1.0",
            "flags": "-O3 -DNDEBUG -std=c++23",
            "commit": "0123456789ab (exported by debian_sync.sh)",
            "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "ab" * 32,
            "governor": "performance", "frequency": "3600000 kHz", "turbo": "boost=1",
            "smt": "active=1"}


def plan(messages, first=0):
    return {"first_frame": first, "messages": messages, "packets": messages // 40 + 2,
            "heartbeats": 1, "bytes": messages * 35, "max_datagram": 1400,
            "max_messages": 65534, "flush_gap_ns": 0, "heartbeat_gap_ns": 1_000_000_000,
            "destination": udp_report.DESTINATION}


def pin(core, pinned=True):
    return {"requested_core": core, "pinned": pinned,
            "attempt": f"pthread_setaffinity_np returned 0, read back [{core}]"}


def send(rate, achieved, messages, paced=True):
    seconds = messages / achieved
    return {"paced": paced, "target_rate": rate if paced else 0, "packets": messages // 40 + 2,
            "sent_packets": messages // 40 + 2, "sent_messages": messages,
            "failed_packets": 0, "elapsed_ns": int(seconds * 1e9),
            "achieved_messages_per_s": achieved,
            "achieved_packets_per_s": achieved / 40, "late_max_ns": 1500}


def sender_record(achieved, messages=1_000_000):
    return {"tool": "bench_udp", "kind": "sender", "header": header(), "plan": plan(messages),
            "pins": {"sender": pin(4)},
            "receive_buffer": {"requested": 4_194_304, "read_back": 425_984},
            "send": send(0, achieved, messages, paced=False),
            "drops": {"rcvbuf_errors": 9, "in_errors": 9, "softnet_dropped": 0,
                      "socket_drops": 9},
            "cpu": {"sender": {"user_us": 100_000, "system_us": 200_000},
                    "kernel": {"ticks_per_second": 100}},
            "verdict": "PASS"}


def receive_record(mode, rate, achieved=None, lossless=True, drops=None, claim=None,
                   speedup=0.0, verdict="PASS", latency_us=20.0, pinned=True, messages=None):
    """A receive run: lossless unless told otherwise, the flag what its counters say unless
    `claim` overrides it."""
    achieved = rate if achieved is None else achieved
    messages = messages or max(1, int((rate or 100_000) * 2))
    counters = {name: 0 for name in udp_driver.LOSS_COUNTERS}
    counters["rxq_overflow"] = 0 if mode in ("S2", "U3") else None
    if not lossless:
        counters["rcvbuf_errors"] = 17
    counters.update(drops or {})
    record = {"tool": "bench_udp", "kind": "receive", "mode": mode, "header": header(),
              "plan": plan(messages),
              "schedule": {"kind": "feed" if speedup else "rate", "rate": 0 if speedup else rate,
                           "feed_speedup": speedup, "span_ns": 1},
              "pins": {"sender": pin(4, pinned), "receiver": pin(6, pinned),
                       "sq_core": 2 if mode == "U5" else -1},
              "receive_buffer": {"requested": 4_194_304, "read_back": 425_984},
              "send": send(rate, achieved, messages),
              "receive": {"mode": {"rearms": 3 if mode.startswith("U") else 0}},
              "drops": counters, "delivered": True, "book": {"match": True},
              "latency": {"samples": 100, "p50_ns": int(latency_us * 1000),
                          "p99_ns": int(latency_us * 3000), "p99_9_ns": int(latency_us * 5000),
                          "max_ns": int(latency_us * 9000)},
              "cpu": {"sender": {"user_us": 300_000, "system_us": 500_000},
                      "sender_window_ns": 2_000_000_000,
                      "receiver": {"user_us": 200_000, "system_us": 400_000},
                      "window_ns": 2_000_000_000,
                      "kernel": {"ticks_per_second": 100, "softirq": {"cpu4": 60, "cpu6": 2},
                                 "threads": {"100": {"comm": "bench_udp", "processor": 6,
                                                     "utime": 1, "stime": 1}}}},
              "verdict": verdict}
    if mode == "U5":
        record["cpu"]["kernel"]["threads"]["101"] = {"comm": "iou-sqp-99", "processor": 2,
                                                     "utime": 0, "stime": 200}
    record["lossless"] = udp_driver.lossless_of(record) if claim is None else claim
    return record


def stderr_at(second):
    def clock(s):
        return f"2026-11-11T10:{s // 60:02d}:{s % 60:02d}Z"
    return (f"measure.sh: start load1=0.10 host=debianhost at={clock(second)}\n"
            f"bench_udp_verdict PASS\n"
            f"measure.sh: end load1=0.20 exit=0 at={clock(second + 5)}\n")


def readings(paranoid="2"):
    return {"perf_event_paranoid": paranoid, "uptime_s": "1000.5", "pswpout": "0"}


def manifest(step, run_id, **more):
    out = {"run_id": run_id, "prediction": "P-0700", "step": step,
           "commit": "0123456789ab (exported by debian_sync.sh)", "host_os": "Linux",
           "cores": dict(CORES), "modes": list(udp_driver.MODES), "repetitions": 2, "seed": 7,
           "readings_before": readings(), "readings_after": readings(),
           "machine_check_before": CHECK, "machine_check_after": CHECK, "processes": []}
    out.update(more)
    return out


class Written:
    """A run directory being written: each process's entry, record and stderr."""

    def __init__(self, folder, manifest_data, start=0):
        self.folder = folder
        self.manifest = manifest_data
        self.second = start
        folder.mkdir(parents=True, exist_ok=True)

    def add(self, mode, role, record, target=None, extra=None, repetition=1):
        stem = f"{self.manifest['step']}-{mode}-{len(self.manifest['processes']) + 1:04d}"
        entry = {"stem": stem, "mode": mode, "role": role, "repetition": repetition,
                 "target_rate": target, "extra": extra or ["--rate", str(target or 0)],
                 "exit": 0 if record and record["verdict"] == "PASS" else 1,
                 "outcome": "result" if record else "failed",
                 "why": None if record else "exited 2: setup failed"}
        self.manifest["processes"].append(entry)
        if record is not None:
            (self.folder / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")
            (self.folder / f"{stem}.err").write_text(stderr_at(self.second), encoding="utf-8")
            self.second += 10
        return entry

    def save(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        return self.folder


def ceiling_run(folder, achieved=(3_000_100, 2_999_900, 3_000_000)):
    run = Written(folder, manifest("ceiling", "P-0700-20261111T090000Z"))
    for value in achieved:
        run.add("sender", "ceiling", sender_record(value), extra=["--frames", "1000000"])
    run.manifest["ceiling"] = {"messages_per_s": udp_driver.ceiling_of(achieved),
                               "source": "run P-0700-20261111T090000Z"}
    return run.save()


THRESHOLDS = {"S3": 1_500_000, "U2": 1_800_000, "U5": 9_000_000}


def bisect_run(folder, ceiling=3_000_000, thresholds=None, confirm=(True, True), start=300):
    """A bisection run as the driver would leave it, each mode lossless up to its threshold."""
    thresholds = thresholds or THRESHOLDS
    cap = udp_driver.cap_of(ceiling)
    run = Written(folder, manifest("bisect", "P-0700-20261111T100000Z",
                                   ceiling={"messages_per_s": ceiling,
                                            "source": "run P-0700-20261111T090000Z"},
                                   rate_cap=cap), start=start)
    results = {}
    for mode, limit in thresholds.items():
        search = udp_driver.Bisection(cap, 100_000, 1_000)
        while (rate := search.next_rate()) is not None:
            ok = rate <= limit
            run.add(mode, "trial", receive_record(mode, rate, lossless=ok), target=rate)
            search.record(rate, ok)
        results[mode] = search.result()
    run.manifest["bisect"] = results
    best = udp_driver.best_mode(results)
    run.manifest["best_mode"] = best
    if TARGET <= cap:
        for repetition, ok in enumerate(confirm, 1):
            run.add(best, "confirm", receive_record(best, TARGET, lossless=ok), target=TARGET,
                    repetition=repetition)
    return run.save()


def smoke_run(folder, irq="y"):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"readings": {"io_uring_disabled": "0", "io_uring_group": "-1",
                         "rmem_max": "212992", "rmem_default": "212992", "ulimit_l": "8192",
                         "vulnerabilities": {"spectre_v2": "Mitigation: Retpolines",
                                             "retbleed": "Mitigation: untrained return thunk"},
                         "lo_mtu": "65536", "irq_time_accounting": irq, "ip_early_demux": "1",
                         "clocksource": "tsc"},
            "loopback": {"S1": {"exit": 0, "verdict": "PASS"}}}
    (folder / "smoke.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest("smoke", "P-0700-20261111T080000Z")),
                                          encoding="utf-8")
    return folder


def edit_cell(text, run, head, value):
    """The text with one cell of the table of every process changed."""
    index = [h for h, _, _ in udp_report.COLUMNS].index(head)
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if line.startswith(f"| {run} |"):
            cells = line.strip().strip("|").split("|")
            cells[index] = f" {value} "
            lines[number] = "|" + "|".join(cells) + "|"
            return "\n".join(lines) + "\n"
    raise AssertionError(f"no row {run}")


def first_run(text, role, mode=None):
    for row in udp_report.parse_runs_table(text):
        if row["role"] == role and (mode is None or row["mode"] == mode):
            return row["run"]
    raise AssertionError(f"no {role} row")


class UdpReportTest(unittest.TestCase):
    def render(self, dirs, name=NAME):
        timed, smoke = udp_report.read_runs(dirs)
        return udp_report.render(timed, smoke, name)

    def bisect_text(self, **kwargs):
        with scratch() as folder:
            root = Path(folder)
            dirs = [smoke_run(root / "smoke"), ceiling_run(root / "ceiling"),
                    bisect_run(root / "bisect", **kwargs)]
            return self.render(dirs)[0]

    def codes(self, text, name=NAME):
        return [code for code, _ in udp_report.check_published(text, name)]

    def test_a_bisection_file_passes_its_own_check_and_says_what_loopback_is(self):
        text = self.bisect_text()
        self.assertEqual(udp_report.check_published(text, NAME), [])
        self.assertIn("\n" + udp_report.COMPARABILITY + "\n", text)
        fields = udp_report.check_results.parse_header(text)
        self.assertEqual(fields["sender_ceiling"],
                         "3,000,000 msg/s, the median of the sender rows below")
        self.assertEqual(fields["rate_cap"], "2,700,000 msg/s, 0.9 × the sender's ceiling")
        rows = len(udp_report.parse_runs_table(text))
        self.assertEqual(fields["pin_outcomes"], f"{rows} of {rows} processes with every thread "
                                                 "asked for pinned and read back")
        self.assertEqual(fields["processes"], f"{rows} with a record, 0 without")
        self.assertEqual(fields["irq_time_accounting"], "y")
        self.assertEqual(fields["mitigations"], "spectre_v2: Mitigation: Retpolines; retbleed: "
                                                "Mitigation: untrained return thunk")
        self.assertEqual(fields["machine_checks"], "4 of 4 passed, before and after each run")
        self.assertEqual(fields["prediction"], "P-0700")
        self.assertEqual(fields["run_id"], "P-0700-20261111T100000Z")
        self.assertEqual(fields["runs"], "P-0700-20261111T090000Z, P-0700-20261111T100000Z")
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("debianhost", text)

    def test_the_largest_lossless_rates_and_t_uring_1_by_hand(self):
        text = self.bisect_text()
        lossless = udp_report.check_results.section(text, "Largest lossless rate")
        found = {}
        for mode, limit in THRESHOLDS.items():
            search = udp_driver.Bisection(2_700_000, 100_000, 1_000)
            while (rate := search.next_rate()) is not None:
                search.record(rate, rate <= limit)
            found[mode] = search.result()["rate"]
        self.assertLessEqual(found["S3"], 1_500_000)
        self.assertGreater(found["S3"] * 1.02, 1_500_000 * 0.98)
        self.assertIn(f"| S3 | {found['S3']:,} |", lossless)
        self.assertIn("| U5 | at least 2,700,000, sender limited | – |", lossless)
        turing1 = udp_report.check_results.section(text, "T-URING-1: io_uring against sockets")
        self.assertIn(f"| U2 | {found['U2']:,} | {found['U2'] / found['S3']:.3f} |", turing1)
        self.assertIn(f"| U5 | at least 2,700,000, sender limited | "
                      f"≥ {2_700_000 / found['S3']:.3f} |", turing1)
        self.assertIn("- Won: U5, lossless up to at least 2,700,000 msg/s, sender limited.",
                      turing1)
        self.assertIn("won in this regime, 1,400 bytes a datagram, header included, rate-paced",
                      turing1)
        self.assertEqual(len(results_table.VERDICT_MARK.findall(turing1)), 0)

    def test_t_uring_2_passes_only_on_every_confirmation_with_its_gap(self):
        text = self.bisect_text()
        self.assertIn("Verdict: **PASS**, U5 lossless at 2,547,190 msg/s in 2 of 2 repetitions; "
                      "U5's largest lossless rate in the bisection is at least 2,700,000 msg/s, "
                      "sender limited; gap at least +152,810 msg/s (+6.0%) against the target.",
                      text)
        text = self.bisect_text(confirm=(True, False))
        self.assertIn("Verdict: **FAIL**, U5 lossless at 2,547,190 msg/s in 1 of 2 repetitions",
                      text)
        self.assertEqual(udp_report.check_published(text, NAME), [])

    def test_t_uring_2_above_the_cap_is_not_shown_and_fails(self):
        with scratch() as folder:
            root = Path(folder)
            dirs = [ceiling_run(root / "ceiling", achieved=(2_500_000,) * 3),
                    bisect_run(root / "bisect", ceiling=2_500_000)]
            text = self.render(dirs)[0]
        self.assertIn("Verdict: **FAIL**, not shown: 2,547,190 msg/s is above the cap 2,250,000 "
                      "msg/s, 0.9 × the sender's ceiling 2,500,000, so no trial may run there",
                      text)
        self.assertEqual(udp_report.check_published(text, NAME), [])

    def test_a_mode_never_lossless_is_below_the_lowest_rate_tried(self):
        text = self.bisect_text(thresholds={"S3": 500, "U2": 1_800_000}, confirm=(False, False))
        self.assertIn("| S3 | below 1,562 | – |", text)
        self.assertIn("| U2 | 1,7", text)
        self.assertIn("| U2 | 1,7", udp_report.check_results.section(
            text, "T-URING-1: io_uring against sockets"))
        self.assertIn("Verdict: **FAIL**, U2 lossless at 2,547,190 msg/s in 0 of 2 repetitions",
                      text)
        self.assertEqual(udp_report.check_published(text, NAME), [])

    def test_the_driver_and_the_table_must_agree(self):
        with scratch() as folder:
            root = Path(folder)
            dirs = [ceiling_run(root / "ceiling"), bisect_run(root / "bisect")]
            data = json.loads((dirs[1] / "manifest.json").read_text(encoding="utf-8"))
            data["bisect"]["S3"]["rate"] += 1
            (dirs[1] / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(udp_report.ReportError, "S3: the driver found"):
                self.render(dirs)

    def test_only_debian_is_written(self):
        with scratch() as folder:
            root = Path(folder)
            dirs = [ceiling_run(root / "ceiling")]
            with self.assertRaisesRegex(udp_report.ReportError, "only debian"):
                self.render(dirs, "udp_ceiling_mac_12302019.md")
            stem = json.loads((dirs[0] / "manifest.json").read_text())["processes"][0]["stem"]
            record = json.loads((dirs[0] / f"{stem}.json").read_text())
            for each in dirs[0].glob("*.json"):
                if each.name != "manifest.json":
                    one = json.loads(each.read_text())
                    one["header"]["host"] = "macOS (M4 Max)"
                    each.write_text(json.dumps(one))
            self.assertEqual(record["header"]["host"], "debian")
            with self.assertRaisesRegex(udp_report.ReportError, "only debian"):
                self.render(dirs, "udp_ceiling_debian_12302019.md")

    # --check's refusals, one at a time

    def test_check_refuses_a_file_without_the_comparability_sentence(self):
        text = self.bisect_text()
        self.assertIn("comparability-unsaid",
                      self.codes(text.replace(udp_report.COMPARABILITY + "\n", "")))
        self.assertIn("comparability-unsaid",
                      self.codes(text.replace("not a network benchmark", "a network benchmark")))

    def test_check_refuses_a_host_label_the_name_does_not_match(self):
        text = self.bisect_text()
        self.assertIn("host-label", self.codes(text, "udp_bisect_mac_12302019.md"))
        self.assertIn("host-label", self.codes(text, "udp_feed_debian_12302019.md"))
        self.assertIn("host-label", self.codes(text, "udp_bisect_debian_10302019.md"))
        self.assertIn("host-label", self.codes(text.replace("- host: debian",
                                                            "- host: macOS (M4 Max)")))

    def test_check_refuses_a_prediction_check_predictions_cannot_read(self):
        text = self.bisect_text()
        self.assertIn("prediction-missing", self.codes(
            text.replace("- prediction: P-0700", "- prediction: P-0700, P-0701")))
        self.assertIn("prediction-missing", self.codes(
            text.replace("- run_id: P-0700-20261111T100000Z", "- run_id: P-0700-x")))

    def test_check_refuses_an_unpinned_row(self):
        text = self.bisect_text()
        run = first_run(text, "trial", "S3")
        self.assertIn("unpinned", self.codes(edit_cell(text, run, "Pins",
                                                       "sender not pinned, receiver 6")))
        self.assertIn("unpinned", self.codes(edit_cell(text, run, "Pins", "sender 4")))

    def test_check_refuses_a_failed_or_missing_machine_check(self):
        text = self.bisect_text()
        broken = text.replace("machine_check: passed", "machine_check: refused, 1 failed", 1)
        self.assertIn("machine-check-failed", self.codes(broken))
        self.assertIn("machine-check-failed", self.codes(
            text.replace("machine_check: record host debian",
                         "machine_check: FAIL governor: core 4 runs 'powersave'", 1)))

    def test_check_refuses_a_rate_above_the_cap(self):
        text = self.bisect_text()
        run = first_run(text, "trial", "U2")
        codes = self.codes(edit_cell(text, run, "Target msgs/s", "2,800,000"))
        self.assertIn("above-ceiling", codes)
        codes = self.codes(text.replace("- rate_cap: 2,700,000", "- rate_cap: 3,000,000"))
        self.assertIn("above-ceiling", codes)

    def test_check_refuses_an_edited_ratio_or_rate(self):
        text = self.bisect_text()
        section = udp_report.check_results.section(text, "T-URING-1: io_uring against sockets")
        line = next(one for one in section.splitlines() if one.startswith("| U2 |"))
        edited = line.rsplit("|", 2)[0] + "| 9.999 |"
        self.assertIn("derived-differs", self.codes(text.replace(line, edited)))
        self.assertIn("derived-differs", self.codes(text.replace("in 2 of 2 repetitions",
                                                                 "in 3 of 3 repetitions")))

    def test_check_refuses_a_lossless_cell_its_counters_contradict(self):
        text = self.bisect_text()
        run = first_run(text, "confirm")
        self.assertIn("lossless-differs", self.codes(edit_cell(text, run, "RcvbufErrors", "3")))
        self.assertIn("lossless-differs", self.codes(edit_cell(text, run, "Socket drops", "–")))
        self.assertNotIn("lossless-differs",
                         self.codes(edit_cell(text, run, "RXQ overflow", "–")))

    def test_check_refuses_a_ceiling_that_is_not_the_rows(self):
        text = self.bisect_text()
        run = first_run(text, "ceiling")
        self.assertIn("ceiling-differs", self.codes(edit_cell(text, run, "Achieved msgs/s",
                                                              "2,000,000")))

    def test_check_refuses_anything_private(self):
        text = self.bisect_text()
        planted = "/" + "home" + "/someone/raw"  # In two pieces, so that the gate passes.
        self.assertIn("private-text", self.codes(text.replace("<raw>", planted, 1)))
        self.assertIn("private-text", self.codes(text.replace("loopback, unicast",
                                                              "127.0.0.1, unicast", 1)))

    def test_check_refuses_a_verdict_mark_too_many_or_a_bad_governor_or_load_line(self):
        text = self.bisect_text()
        self.assertIn("verdict-marks", self.codes(text + "\n**PASS**\n"))
        self.assertIn("governor", self.codes(text.replace("- governor: performance",
                                                          "- governor: powersave")))
        self.assertIn("load-line-invalid", self.codes(
            text.replace("- measure_load_start: measure.sh: start", "- measure_load_start: x")))
        self.assertIn("summary-differs", self.codes(text.replace("T-URING-2 PASS",
                                                                 "T-URING-2 FAIL")))

    # the other steps

    def feed_text(self, lossy_mode=None):
        with scratch() as folder:
            root = Path(folder)
            window = {"1s": [400_000, 5_000_000, 34_200_000_000_000],
                      "1ms": [1_000, 5_100_000, 34_200_100_000_000]}
            run = Written(root / "feed", manifest(
                "feed", "P-0701-20261111T110000Z",
                feed={"day_peaks": window, "window_peaks": window, "flush_us": 100.0,
                      "window": {"first": 4_000_000, "frames": 2_000_000,
                                 "lead_frames": 1_000_000, "span_ns": 1,
                                 "longest_gap_ns": 3_000_000}}))
            speedups = {"feed-x1": 1.0,
                        "feed-1s": udp_driver.speedup_for(udp_driver.TARGET_1S, 400_000, 1.0),
                        "feed-1ms": udp_driver.speedup_for(TARGET, 1_000, 0.001)}
            for repetition in (1, 2):
                for mode in ("S3", "U2"):
                    for role, speedup in speedups.items():
                        ok = not (mode == lossy_mode and role == "feed-1ms" and repetition == 2)
                        run.add(mode, role, receive_record(mode, 0, achieved=300_000,
                                                           speedup=round(speedup, 3),
                                                           lossless=ok),
                                extra=["--feed-speedup", repr(speedup)], repetition=repetition)
            return self.render([run.save()], "udp_feed_debian_12302019.md")[0]

    def test_a_feed_file_scales_each_window_by_its_own_peak(self):
        text = self.feed_text(lossy_mode="U2")
        name = "udp_feed_debian_12302019.md"
        self.assertEqual(udp_report.check_published(text, name), [])
        self.assertIn("feed ×3.264", text)
        self.assertIn("feed ×2.547", text)
        feed = udp_report.check_results.section(text, "Feed-shaped replay")
        self.assertIn("| S3 | pass, 2 of 2 lossless | pass, 2 of 2 lossless | pass, 2 of 2 "
                      "lossless |", feed)
        self.assertIn("| U2 | pass, 2 of 2 lossless | pass, 2 of 2 lossless | fail, 1 of 2 "
                      "lossless |", feed)
        self.assertEqual(len(results_table.VERDICT_MARK.findall(text)), 0)
        self.assertIn("speedup-differs", self.codes(
            text.replace("- window_peak_1s: 400,000", "- window_peak_1s: 500,000"), name))
        self.assertIn("speedup-differs", self.codes(text.replace("feed ×2.547", "feed ×3.000", 1),
                                                    name))

    def test_a_latency_file_gives_medians_and_the_cpu_split(self):
        with scratch() as folder:
            root = Path(folder)
            run = Written(root / "latency", manifest(
                "latency", "P-0702-20261111T120000Z",
                ceiling={"messages_per_s": 3_000_000, "source": "run P-0700-20261111T090000Z"},
                rate_cap=2_700_000))
            for repetition, scale in ((1, 1.0), (2, 2.0), (3, 3.0)):
                for mode in ("S3", "U5"):
                    run.add(mode, "latency-low", receive_record(mode, 40_000,
                                                                latency_us=10 * scale),
                            target=40_000, repetition=repetition)
                    run.add(mode, "latency-0.5", receive_record(mode, 750_000,
                                                                latency_us=20 * scale),
                            target=750_000, repetition=repetition)
                    run.add(mode, "cpu", receive_record(mode, 500_000), target=500_000,
                            repetition=repetition)
            dirs = [smoke_run(root / "smoke", irq="not set"), run.save()]
            text = self.render(dirs, "udp_latency_debian_12302019.md")[0]
        self.assertEqual(udp_report.check_published(text, "udp_latency_debian_12302019.md"), [])
        latency = udp_report.check_results.section(text, "Latency")
        self.assertIn("| S3 | latency-low | 40,000 | 3 | 20.00 | 60.00 | 100.00 | 180.00 |",
                      latency)
        self.assertIn("| U5 | latency-0.5 | 750,000 | 3 | 40.00 |", latency)
        cpu = udp_report.check_results.section(text, "CPU split at the common rate")
        # Sender 0.8 s of 2 s, receiver 0.6 s, SQ 200 ticks of 10 ms, softirq 60 and 2 ticks.
        self.assertIn("| S3 | 500,000 | 40.0 | 30.0 | – | 30.0 | 1.0 | 101.0 |  |", cpu)
        self.assertIn("| U5 | 500,000 | 40.0 | 30.0 | 100.0 | 30.0 | 1.0 | 201.0 | the receiver "
                      "and the SQ thread spin: 100% by construction |", cpu)
        self.assertIn("CONFIG_IRQ_TIME_ACCOUNTING on this host: not set.", cpu)
        self.assertIn("softirq ticks may also appear in the sender thread's stime", cpu)
        self.assertIn("SQ thread asked for 2, last on 2", text)

    # results_table.py's reader

    def test_results_table_takes_the_verdict_and_the_modes(self):
        with scratch() as folder:
            root = Path(folder)
            (root / "results").mkdir()
            (root / "results" / "run_log.md").write_text(
                "| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n",
                encoding="utf-8")
            plain = results_table.generate(root)
            self.assertFalse(any("T-URING" in line for line in plain))
            text = self.bisect_text()
            (root / "results" / NAME).write_text(text, encoding="utf-8")
            lines = results_table.generate(root)
            verdict = next(line for line in lines if line.startswith("| T-URING-2 |"))
            self.assertIn("| T-URING-2 | 12302019 | debian | PASS | U5 lossless at 2,547,190 "
                          "msg/s in 2 of 2 repetitions;", verdict)
            self.assertIn("`0123456789ab`", verdict)
            self.assertTrue(any(line.startswith("| U2 | 1,") for line in lines))
            self.assertTrue(any(line.startswith("| U5 | at least 2,700,000, sender limited | ≥ ")
                                for line in lines))
            (root / "results" / NAME).write_text(text + "\n**FAIL**\n", encoding="utf-8")
            with self.assertRaisesRegex(results_table.TableError, "2 verdicts in the file, 1 rows"):
                results_table.generate(root)

    def test_results_table_takes_the_modes_from_the_bisect_file_alone(self):
        with scratch() as folder:
            root = Path(folder)
            (root / "results").mkdir()
            (root / "results" / "run_log.md").write_text(
                "| Run | Prediction | Commit | OS | Outcome |\n|---|---|---|---|---|\n",
                encoding="utf-8")
            text = self.bisect_text()
            (root / "results" / NAME).write_text(text, encoding="utf-8")
            once = results_table.generate(root)
            # A later step's file repeats the bisection's T-URING-1 table, not its verdict.
            head, rest = text.split("\n## T-URING-2: ", 1)
            later = head + "\n## " + rest.split("\n## ", 1)[1]
            later = later.replace("\n- step: bisect\n", "\n- step: latency\n")
            self.assertIn("## T-URING-1: ", later)
            (root / "results" / "udp_latency_debian_12302019.md").write_text(later,
                                                                           encoding="utf-8")
            twice = results_table.generate(root)
            for mode in ("U2", "U5"):
                self.assertEqual(sum(line.startswith(f"| {mode} | ") for line in once), 1)
                self.assertEqual(sum(line.startswith(f"| {mode} | ") for line in twice), 1)

    def test_the_main_writes_then_checks_and_check_fails_a_broken_file(self):
        with scratch() as folder:
            root = Path(folder)
            dirs = [ceiling_run(root / "ceiling"), bisect_run(root / "bisect")]
            out = root / "results" / NAME
            log = root / "run_log.md"
            log.write_text("", encoding="utf-8")
            argv = [arg for d in dirs for arg in ("--run-dir", str(d))]
            self.assertEqual(udp_report.main([*argv, "--out", str(out), "--run-log", str(log)]), 0)
            self.assertIn("P-0700-20261111T100000Z_report", log.read_text(encoding="utf-8"))
            self.assertEqual(udp_report.main(["--check", str(out)]), 0)
            out.write_text(out.read_text(encoding="utf-8").replace(udp_report.COMPARABILITY, ""),
                           encoding="utf-8")
            self.assertEqual(udp_report.main(["--check", str(out)]), 1)
            self.assertEqual(udp_report.main(["--run-dir", str(dirs[0]), "--out",
                                              str(root / "udp_bisect_debian_12302019.md")]), 2)


if __name__ == "__main__":
    unittest.main()
