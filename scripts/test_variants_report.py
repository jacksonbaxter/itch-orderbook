#!/usr/bin/env python3
"""Tests for variants_report.py, on records invented here: the figures it works out by hand,
what it refuses, and that --check catches a published file that says more than its table of
timed passes."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import variants_report  # noqa: E402

DEBIAN = variants_report.DEBIAN
MAC = variants_report.MAC
MESSAGES = 1_000_000
START = ("measure.sh: start load1=1.10 host=box at=2026-11-05T09:0{n}:00Z cpus=4 "
         "governor=performance freq_khz=cpu4:2000000 perf_event_paranoid=2")
END = ("measure.sh: end load1=1.20 exit=0 at=2026-11-05T09:0{n}:30Z cpus=4 "
       "governor=performance freq_khz=cpu4:3500000 perf_event_paranoid=2")
CHECK = "machine_check: pass governor: core 4 runs performance\nmachine_check: passed\n"
STATE = ("uptime: 180000.00 700000.00 | up 2 days | perf_event_paranoid=2 | pswpout 41299 | "
         "MemAvailable: 14000000 kB | nice 0")


def header(host):
    pinning = ("pinned to core 4: pthread_setaffinity_np returned 0, read back [4]"
               if host == DEBIAN else "not pinned: macOS has no thread affinity")
    return {"host": host, "cpu": "a cpu", "kernel": "Linux 6.12" if host == DEBIAN else "Darwin",
            "compiler": "Clang 23", "flags": "-O3 -DNDEBUG -std=c++23",
            "commit": "0123456789ab (exported by debian_sync.sh)",
            "dataset": "12302019.NASDAQ_ITCH50", "pinning": pinning,
            "governor": "performance" if host == DEBIAN else "unavailable: macOS",
            "design": "paged-flat: FastBook"}


def counters(host, per_message, messages=MESSAGES):
    """The pass's counters, from a value a message for each event, in the host's order."""
    return [{"name": name, "state": "measured", "value": int(round(value * messages)),
             "ratio": 1.0, "reason": ""}
            for name, value in zip(variants_report.EVENTS[host], per_message)]


def replay_record(host, dispatch="switch", prefetch=0, seconds=0.2, per_message=None):
    per_message = per_message or [400, 300, 2.1, 6.0, 2.0][:len(variants_report.EVENTS[host])]
    return {"header": header(host),
            "variant": {"pass": "replay", "design": "paged-flat", "dispatch": dispatch,
                        "prefetch": prefetch, "threaded_built": True},
            "mix": {"messages": MESSAGES, "book_messages": MESSAGES},
            "warmup_messages": MESSAGES, "script": {"entries": 0, "capacity": 0, "arena": ""},
            "passes": [{"mode": "replay", "messages": MESSAGES, "seconds": seconds,
                        "rejected": 0, "fold": 0, "lookups": 0, "mismatches": 0,
                        "script_exact": True, "live_orders": 5, "live_levels": 3,
                        "residency_before": "", "residency_after": "",
                        "counters": counters(host, per_message)}],
            "pinned": host == DEBIAN, "migrations": 0, "peak_rss_bytes": 9 * 1024 ** 3,
            "refusals": []}


def lookup_record(host, design, check_s, answer_s, lookups=1_100_000):
    record = replay_record(host)
    record["variant"] = {"pass": "lookup", "design": design, "dispatch": "switch",
                         "prefetch": 0, "threaded_built": True}
    events = len(variants_report.EVENTS[host])
    passes = []
    for mode, seconds, scale in (("check", check_s, 2.0), ("answer", answer_s, 1.0),
                                 ("answer", answer_s, 1.0), ("check", check_s, 2.0)):
        one = copy.deepcopy(record["passes"][0])
        one.update(mode=mode, seconds=seconds, lookups=lookups,
                   counters=counters(host, [scale * (i + 1) for i in range(events)]))
        passes.append(one)
    record["passes"] = passes
    return record


class Variants(unittest.TestCase):
    def render(self, experiment, records, host=DEBIAN, checks=None, states=None):
        loads = [START.format(n=i) + "\n" + END.format(n=i) + "\n" for i in range(len(records))]
        checks = [CHECK, CHECK] if checks is None and host == DEBIAN else (checks or [])
        states = [STATE, STATE] if states is None and host == DEBIAN else (states or [])
        return variants_report.render(experiment, records, loads, "P-0021", checks, states)

    def name(self, experiment, host=DEBIAN):
        return f"{experiment}_{variants_report.HOST_FILE[host]}_12302019.md"

    def violations(self, text, experiment="prefetch", host=DEBIAN):
        return {code for code, _ in variants_report.check_published(text, self.name(experiment,
                                                                                     host))}

    def prefetch_records(self, gain):
        records = []
        for _ in range(3):
            for distance, seconds in ((0, 0.2), (8, 0.2 / (1 + gain)), (16, 0.2 / (1 + gain / 2))):
                records.append(replay_record(DEBIAN, prefetch=distance, seconds=seconds))
        return records

    def test_e8_is_refuted_below_ten_percent_and_not_at_it(self):
        text, run_id, summary = self.render("prefetch", self.prefetch_records(0.05))
        self.assertEqual(run_id, "P-0021-20261105T090000Z")
        self.assertIn("best look-ahead D=8 at +5.00% over D=0; E8 refuted", summary)
        self.assertIn("**refuted**: no look-ahead gains 10%.", text)
        self.assertEqual(self.violations(text), set())
        text, _, summary = self.render("prefetch", self.prefetch_records(0.12))
        self.assertIn("E8 not refuted", summary)
        self.assertIn("`D=8` +12.00%, `D=16` +6.00%", text)

    def test_replay_figures_are_medians_of_the_repetitions(self):
        records = [replay_record(DEBIAN, prefetch=0, seconds=s) for s in (0.25, 0.2, 0.4)]
        records += [replay_record(DEBIAN, prefetch=4, seconds=0.2)] * 3
        text, _, _ = self.render("prefetch", records)
        # Rates 4 M, 5 M and 2.5 M: the median is 4 M, the sample CV 32.83%, 250 ns a message.
        self.assertIn("| `D=0` | 3 | 4,000,000 | 2,500,000 | 5,000,000 | 32.83% | 250.00 "
                      "| 1.0000 |", text)
        self.assertIn("| `D=4` | 3 | 5,000,000 | 5,000,000 | 5,000,000 | 0.00% | 200.00 "
                      "| 1.2500 |", text)

    def test_lookup_is_check_less_answer_over_the_lookups(self):
        records = [lookup_record(DEBIAN, "paged-flat", 0.30, 0.25) for _ in range(3)]
        records += [lookup_record(DEBIAN, "hash-flat", 0.33, 0.25) for _ in range(3)]
        text, _, summary = self.render("lookup", records)
        # 0.05 s over 1.1 M lookups is 45.45 ns each, 50 ns a message, 16.7% of 0.30 s.
        self.assertIn("| `paged-flat` | 1,100,000 | 300.00 | 250.00 | 45.45 | 45.45 | 45.45 "
                      "| 50.00 | 16.7% |", text)
        self.assertIn("The hash's lookup over the paged index's: 1.600 times.", text)
        self.assertIn("paged-flat lookup 45.45 ns, 16.7% of the pass", summary)
        # Each event counts 2 a message in a check pass and 1 in an answer pass, times its
        # place, so the difference a lookup is (i + 1) / 1.1.
        self.assertIn("| 0.909 | 1.818 | 2.727 | 3.636 | 4.545 |", text)
        self.assertEqual(self.violations(text, "lookup"), set())

    def test_e7_judges_the_spread_of_branch_misses_and_not_on_the_mac(self):
        records = []
        for form, misses in (("switch", 2.10), ("table", 2.12), ("threaded", 2.13),
                             ("predicate", 0.01)):
            records.append(replay_record(DEBIAN, dispatch=form, per_message=[400, 300, misses,
                                                                             6.0, 2.0]))
        text, _, summary = self.render("dispatch", records)
        self.assertIn("spread 0.0300", text)
        self.assertIn("E7 refuted", summary)
        records[2]["passes"][0]["counters"] = counters(DEBIAN, [400, 300, 2.40, 6.0, 2.0])
        _, _, summary = self.render("dispatch", records)
        self.assertIn("E7 not refuted", summary)
        mac = [replay_record(MAC, dispatch=form) for form in ("switch", "table", "predicate")]
        text, _, summary = self.render("dispatch", mac, host=MAC)
        self.assertIn("E7 not judged on this host, no branch-miss counter", summary)
        self.assertEqual(self.violations(text, "dispatch", MAC), set())

    def test_a_result_or_finding_unlike_the_table_is_caught(self):
        text, _, _ = self.render("prefetch", self.prefetch_records(0.05))
        self.assertIn("results-differ", self.violations(text.replace("| 1.0500 |", "| 1.1500 |")))
        self.assertIn("finding-differs", self.violations(
            text.replace("**refuted**: no look-ahead", "**not refuted**: no look-ahead")))
        self.assertIn("summary-differs", self.violations(
            text.replace("; E8 refuted", "; E8 not refuted", 1)))
        row = next(line for line in text.splitlines() if line.startswith("| `D=8` | 1 | replay"))
        self.assertIn("results-differ", self.violations(text.replace(row, row.replace(
            "| 0.190476 |", "| 0.150000 |"))))

    def test_the_debian_host_must_be_fit_at_both_ends(self):
        records = self.prefetch_records(0.05)
        for states in ([STATE.replace("paranoid=2", "paranoid=3"), STATE],
                       [STATE, STATE.replace("nice 0", "nice 5")]):
            text, _, _ = self.render("prefetch", records, states=states)
            self.assertIn("host-state", self.violations(text))
        text, _, _ = self.render("prefetch", records,
                                 checks=[CHECK, "machine_check: FAIL governor\n"])
        self.assertIn("machine-check-failed", self.violations(text))
        text, _, _ = self.render("prefetch", records)
        self.assertIn("pin-failed", self.violations(text.replace("read back [4]", "read back [5]")))
        self.assertIn("governor", self.violations(text.replace("- governor: performance",
                                                               "- governor: schedutil")))
        with self.assertRaises(variants_report.ReportError):
            self.render("prefetch", records, checks=[CHECK], states=[STATE])

    def test_names_files_and_privacy(self):
        text, _, _ = self.render("prefetch", self.prefetch_records(0.05))
        self.assertIn("name-unsaid", self.violations(text + "\ncache-misses fell\n"))
        self.assertIn("name-unsaid", self.violations(text + "\nr0843 fell\n"))
        self.assertIn("host-label", {code for code, _ in variants_report.check_published(
            text, "prefetch_mac_12302019.md")})
        self.assertIn("host-label", {code for code, _ in variants_report.check_published(
            text, "dispatch_debian_12302019.md")})
        planted = "/" + "home" + "/someone/x"  # In two pieces, so that the gate passes this file.
        self.assertIn("private-text", self.violations(text + "\n" + planted + "\n"))
        self.assertIn("load-line-invalid", self.violations(text.replace("exit=0", "exit=3", 1)))
        self.assertNotIn("host=box", text)
        mac = [replay_record(MAC, prefetch=d) for d in (0, 4)]
        text, _, _ = self.render("prefetch", mac, host=MAC)
        self.assertIn("pin-claimed", self.violations(
            text.replace("- pinning: not pinned", "- pinning: pinned"), host=MAC))

    def test_bad_records_are_refused(self):
        refused = self.prefetch_records(0.05)
        refused[1]["refusals"] = ["input left memory"]
        with self.assertRaises(variants_report.ReportError):
            self.render("prefetch", refused)
        multiplexed = self.prefetch_records(0.05)
        multiplexed[0]["passes"][0]["counters"][4]["state"] = "multiplexed"
        with self.assertRaises(variants_report.ReportError):
            self.render("prefetch", multiplexed)
        mixed = self.prefetch_records(0.05)
        mixed[2]["header"]["commit"] = "fedcba987654 (exported by debian_sync.sh)"
        with self.assertRaises(variants_report.ReportError):
            self.render("prefetch", mixed)
        wrong = [lookup_record(DEBIAN, "paged-flat", 0.3, 0.25)]
        wrong[0]["passes"][1]["mismatches"] = 1
        with self.assertRaises(variants_report.ReportError):
            self.render("lookup", wrong)
        with self.assertRaises(variants_report.ReportError):
            self.render("dispatch", self.prefetch_records(0.05))

    def test_main_writes_the_file_and_its_run_log_row(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            records, loads = [], []
            for number, record in enumerate(self.prefetch_records(0.05)):
                path = root / f"run-{number}.json"
                path.write_text(json.dumps(record), encoding="utf-8")
                records.append(str(path))
                load = root / f"run-{number}.err"
                load.write_text(START.format(n=number) + "\n" + END.format(n=number) + "\n",
                                encoding="utf-8")
                loads.append(str(load))
            extra = []
            for flag, text in (("--machine-check", CHECK), ("--host-state", STATE)):
                paths = []
                for side in ("before", "after"):
                    path = root / f"{flag[2:]}-{side}.txt"
                    path.write_text(text, encoding="utf-8")
                    paths.append(str(path))
                extra += [flag, *paths]
            out = root / "prefetch_debian_12302019.md"
            log = root / "run_log.md"
            log.write_text("", encoding="utf-8")
            argv = ["--experiment", "prefetch", "--record", *records, "--load", *loads, *extra,
                    "--prediction", "P-0021", "--out", str(out), "--run-log", str(log)]
            self.assertEqual(variants_report.main(argv), 0)
            self.assertIn("| P-0021-20261105T090000Z | P-0021 | 0123456789ab (exported by "
                          "debian_sync.sh) | Linux | published: best look-ahead D=8",
                          log.read_text())
            self.assertEqual(variants_report.main(["--check", str(out)]), 0)
            out.write_text(out.read_text().replace("| 1.0500 |", "| 1.0600 |"), encoding="utf-8")
            self.assertEqual(variants_report.main(["--check", str(out)]), 1)


if __name__ == "__main__":
    unittest.main()
