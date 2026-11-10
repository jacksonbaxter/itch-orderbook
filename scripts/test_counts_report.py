#!/usr/bin/env python3
"""Tests for counts_report.py, on records invented here: what it publishes, what it refuses,
and that --check catches a published file that says more than its tables."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import counts_report  # noqa: E402

EVENTS = ["cycles", "instructions", "branch-misses", "cache-misses (L2 misses on Zen 1)",
          "r0843:u (a floor on DRAM demand fills)"]
# Per type: messages, then per message cycles, instructions, branch misses, L2, fills.
TYPES = {"A": (400, 700, 300, 1, 6, 2), "D": (380, 650, 280, 2, 5, 2),
         "U": (70, 1200, 500, 3, 9, 4), "E": (30, 600, 250, 2, 5, 2),
         "S": (2, 300, 120, 1, 1, 0), "R": (8, 400, 150, 1, 2, 1)}
EMPTY = (180, 60, 0, 0, 0)
START = ("measure.sh: start load1=1.10 host=debian at=2026-11-05T09:00:00Z cpus=4 "
         "governor=performance freq_khz=cpu4:2000000 perf_event_paranoid=2")
END = ("measure.sh: end load1=1.20 exit=0 at=2026-11-05T09:05:00Z cpus=4 "
       "governor=performance freq_khz=cpu4:3500000 perf_event_paranoid=2")
CHECK = "machine_check: pass governor: core 4 runs performance\nmachine_check: passed\n"


def record(scale=1):
    types = []
    for code, (count, *per) in TYPES.items():
        types.append({"type": code, "spans": count, "sums": [count * value * scale
                                                             for value in per]})
    whole = [sum(entry["sums"][i] for entry in types) for i in range(len(EVENTS))]
    frames = sum(entry["spans"] for entry in types)
    book = sum(entry["spans"] for entry in types if entry["type"] in counts_report.BOOK_TYPES)
    spans = 1000
    return {
        "header": {"host": "debian", "cpu": "AMD Ryzen Embedded V1605B with Radeon Vega Gfx",
                   "commit": "0123456789ab (exported by debian_sync.sh)",
                   "dataset": "12302019.NASDAQ_ITCH50",
                   "pinning": "pinned to core 4: pthread_setaffinity_np returned 0, read back [4]",
                   "governor": "performance",
                   "design": "paged-flat: FastBook, 4194304 orders and 2097152 levels"},
        "events": EVENTS, "status": [], "agreement": "agreed",
        "around_pass": {"read": [value + 50 for value in whole],
                        "rdpmc": [value + 20 for value in whole]},
        "mix": {"messages": frames, "book_messages": book},
        "empty_before": {"spans": spans, "missed": 0, "sums": [v * spans for v in EMPTY]},
        "empty_after": {"spans": spans, "missed": 0, "sums": [v * spans for v in EMPTY]},
        "pass": {"frames": frames, "missed": 0, "rejected": 0, "whole": whole,
                 "by_type_total": whole, "tiled": True},
        "types": types, "pinned": True, "migrations": 0, "peak_rss_bytes": 9 * 1024 ** 3,
        "refusals": []}


# Of the E and U messages, how many fall in each age, and fills a message at that age.
AGE_SPLIT = {"E": {"under 1 ms": (20, 1), "1 s and over": (10, 4)},
             "D": {"under 1 ms": (180, 2), "1 to 10 ms": (100, 2), "1 s and over": (100, 3)},
             "U": {"under 1 ms": (30, 2), "100 ms to 1 s": (25, 3), "1 s and over": (15, 6)}}


def age_record(scale=1):
    """A record counted by type and age: E and U split by age, fills set per age, the other
    events spread as in TYPES, so the slots still tile the pass."""
    base = record(scale)
    types = []
    fills = EVENTS.index("r0843:u (a floor on DRAM demand fills)")
    for entry in base["types"]:
        if entry["type"] not in AGE_SPLIT:
            types.append(entry)
            continue
        count, *per = TYPES[entry["type"]]
        for age, (spans, fill) in AGE_SPLIT[entry["type"]].items():
            sums = [spans * value * scale for value in per]
            sums[fills] = spans * fill * scale
            types.append({"type": entry["type"], "age": age, "spans": spans, "sums": sums})
    whole = [sum(entry["sums"][i] for entry in types) for i in range(len(EVENTS))]
    base["types"] = types
    base["pass"]["whole"] = whole
    base["pass"]["by_type_total"] = whole
    base["by"] = "type-age"
    base["age_frames"] = base["pass"]["frames"]
    return base


class CountsByAge(unittest.TestCase):
    def render(self, records, plain=None):
        loads = [f"{START}\n{END}\n"] * len(records)
        return counts_report.render_ages(records, loads, [CHECK, CHECK], "P-0025", plain,
                                         [f"{START}\n{END}\n"] * len(plain or []))[0]

    def test_a_clean_report_passes_its_own_check(self):
        text = self.render([age_record(), age_record()], [record()])
        self.assertEqual(counts_report.check_ages(text), [])
        self.assertTrue(counts_report.is_age_file(text))
        self.assertIn("- summary: floor on DRAM demand fills a message naming an order: "
                      "1.913 under 1 ms old, 3.440 at 1 s and over", text)
        self.assertIn("## What the age walk costs", text)
        self.assertIsNone(counts_report.VERDICT.search(text))

    def test_what_each_counter_can_attribute_is_said(self):
        text = self.render([age_record()])
        section = counts_report.check_results.section(text, "What each counter can attribute")
        self.assertIn("does not say which structure", section)
        self.assertIn("not a last-level", section)

    def test_a_named_span_with_no_age_is_refused(self):
        bad = age_record()
        bad["types"].append({"type": "X", "spans": 0, "sums": [0] * len(EVENTS)})
        with self.assertRaises(counts_report.ReportError):
            self.render([bad])

    def test_an_age_walk_that_missed_a_frame_is_refused(self):
        bad = age_record()
        bad["age_frames"] -= 1
        with self.assertRaises(counts_report.ReportError):
            self.render([bad])

    def test_a_plain_record_among_the_aged_is_refused(self):
        with self.assertRaises(counts_report.ReportError):
            self.render([age_record(), record()])

    def test_check_catches_a_slot_edited_after_the_fact(self):
        text = self.render([age_record()])
        edited = text.replace("| `U` 1 s and over | ", "| `U` 1 s and over | 1", 1)
        self.assertIn("not-tiled", [code for code, _ in counts_report.check_ages(edited)])

    def test_check_catches_a_summary_that_differs_from_the_table(self):
        text = self.render([age_record()])
        edited = text.replace("3.440 at 1 s and over", "2.440 at 1 s and over", 1)
        self.assertIn("summary-differs", [code for code, _ in counts_report.check_ages(edited)])

    def test_check_catches_a_name_left_unsaid(self):
        text = self.render([age_record()]) + "\nThe cache-misses fell.\n"
        self.assertIn("name-unsaid", [code for code, _ in counts_report.check_ages(text)])


class CountsReport(unittest.TestCase):
    def render(self, records):
        return counts_report.render(records, [f"{START}\n{END}\n"] * len(records),
                                    [CHECK, CHECK], "P-0019")[0]

    def test_a_clean_report_passes_its_own_check(self):
        text = self.render([record(), record(), record()])
        self.assertEqual(counts_report.check_published(text), [])
        self.assertIn("T-PERF-1, `r0843:u` per book message, a floor on DRAM demand fills, "
                      "against ≤ 0.623: **FAIL**. Measured 2.159 raw", text)
        self.assertIn("**PASS**. Measured 1.618 raw", text)

    def test_the_cost_of_counting_is_beside_every_figure_and_never_quietly_taken_off(self):
        text = self.render([record()])
        self.assertIn("| `A` | Add Order | 400 | 700.0000 | 700.0000 | 700.0000 | 180.0000 "
                      "| 520.0000 |", text)

    def test_spans_that_do_not_add_up_to_the_whole_pass_are_refused(self):
        broken = record()
        broken["types"][0]["sums"][4] += 1
        with self.assertRaisesRegex(counts_report.ReportError, "do not add up"):
            self.render([broken])

    def test_a_pass_the_binary_found_untiled_is_refused(self):
        broken = record()
        broken["pass"]["tiled"] = False
        with self.assertRaisesRegex(counts_report.ReportError, "do not add up"):
            self.render([broken])

    def test_a_refused_record_is_refused(self):
        broken = record()
        broken["refusals"] = ["input not resident"]
        with self.assertRaisesRegex(counts_report.ReportError, "refused record"):
            self.render([broken])

    def test_book_spans_must_match_the_warm_up_count(self):
        broken = record()
        broken["mix"]["book_messages"] += 1
        with self.assertRaisesRegex(counts_report.ReportError, "book messages"):
            self.render([broken])

    def test_check_catches_a_total_edited_after_the_fact(self):
        text = self.render([record()])
        edited = text.replace("| `A` | 800 | 400 |", "| `A` | 801 | 400 |", 1)
        self.assertTrue(edited != text)
        codes = [code for code, _ in counts_report.check_published(edited)]
        self.assertIn("not-tiled", codes)

    def test_check_catches_a_faked_verdict(self):
        text = self.render([record()]).replace(
            "against ≤ 0.623: **FAIL**", "against ≤ 0.623: **PASS**")
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertIn("verdict-faked", codes)

    def test_a_figure_rounded_to_three_places_beside_its_four_place_median_is_not_faked(self):
        text = self.render([record()]).replace("Measured 2.159 raw", "Measured 2.002 raw", 1)
        text = text.replace("| 2.1591 | 2.1591 | 2.1591 | 0.0000 | 2.1591 |",
                            "| 2.0025 | 2.0025 | 2.0025 | 0.0000 | 2.0025 |", 1)
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertNotIn("verdict-faked", codes)

    def test_check_catches_a_name_left_unsaid(self):
        text = self.render([record()]) + "\nThe cache-misses rose.\n"
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertIn("name-unsaid", codes)
        text = self.render([record()]) + "\nr0843 rose.\n"
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertIn("name-unsaid", codes)

    def test_check_catches_a_home_path(self):
        text = self.render([record()]) + "\n/" + "home" + "/someone/x\n"
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertIn("private-text", codes)

    def test_check_catches_the_cost_column_dropped(self):
        text = self.render([record()]).replace("| Empty span |", "| Spare |")
        codes = [code for code, _ in counts_report.check_published(text)]
        self.assertIn("cost-unshown", codes)

    def test_the_command_line_writes_and_checks(self):
        base = Path(__file__).resolve().parent.parent / "build"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as scratch:
            root = Path(scratch)
            records, loads = [], []
            for number in range(2):
                path = root / f"run-{number}.json"
                path.write_text(json.dumps(copy.deepcopy(record())), encoding="utf-8")
                records.append(str(path))
                load = root / f"run-{number}.err"
                load.write_text(f"{START}\n{END}\n", encoding="utf-8")
                loads.append(str(load))
            checks = []
            for name in ("before", "after"):
                path = root / f"{name}.out"
                path.write_text(CHECK, encoding="utf-8")
                checks.append(str(path))
            out = root / "counters_by_type_paged-flat_debian_12302019.md"
            log = root / "run_log.md"
            log.write_text("", encoding="utf-8")
            raw = ["--record", *records, "--load", *loads, "--machine-check", *checks,
                   "--prediction", "P-0019"]
            self.assertEqual(counts_report.main([*raw, "--out", str(out), "--run-log", str(log)]),
                             0)
            self.assertIn("published: T-PERF-1 FAIL, T-PERF-2 PASS", log.read_text())
            self.assertEqual(counts_report.main(["--check", str(out), *raw]), 0)
            out.write_text(out.read_text() + "\nextra\n", encoding="utf-8")
            self.assertEqual(counts_report.main(["--check", str(out), *raw]), 1)


if __name__ == "__main__":
    unittest.main()
