#!/usr/bin/env python3
"""Tests for simd_report.py, on runs invented here: the figures it works out by hand, how it
calls a winner and each kill criterion, what it refuses, and that --check catches a published
file that says more than its table of timed passes."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import simd_report  # noqa: E402

MAC = simd_report.MAC
MESSAGES = 1_000_000
ITEMS = 300_000
SAMPLER_START = "2026-11-05T08:59:58Z"


def clock(second):
    return f"2026-11-05T09:{second // 60:02d}:{second % 60:02d}Z"


def stderr_for(index):
    start, end = 20 * index, 20 * index + 10
    return (f"measure.sh: start load1=1.10 host=box at={clock(start)} cpus=16\n"
            f"bench output\n"
            f"  9663676416  maximum resident set size\n"
            f"measure.sh: end load1=1.20 exit=0 at={clock(end)}\n")


def sampler(processes, foreign_at=None):
    lines = [f"{SAMPLER_START} sample"]
    for second in range(0, 20 * processes + 2, 2):
        lines.append(f"{clock(second)} sample")
        if foreign_at == second:
            lines.append(f"{clock(second)} FOREIGN 93.6 /somewhere/else --busy")
    return "\n".join(lines) + "\n"


def header():
    return {"host": MAC, "cpu": "Apple M4 Max", "kernel": "Darwin 25", "compiler": "Clang 21",
            "flags": "-O3 -DNDEBUG -std=c++23", "commit": "0123456789ab (clean)",
            "dataset": "12302019.NASDAQ_ITCH50", "dataset_sha256": "ab" * 32,
            "pinning": "not pinned: macOS has no thread affinity",
            "design": "decode (E2), kernel neon"}


def counted(cycles, instructions):
    return [{"name": "cycles (thread_selfcounts, user+kernel)", "state": "measured",
             "value": int(cycles), "ratio": 1.0, "reason": ""},
            {"name": "instructions (thread_selfcounts, user+kernel)", "state": "measured",
             "value": int(instructions), "ratio": 1.0, "reason": ""}]


def one_pass(mode, seconds, items=ITEMS, fold=7, cycles=0.0, instructions=0.0, counters=True):
    return {"mode": mode, "messages": MESSAGES, "items": items, "seconds": seconds, "fold": fold,
            "matches": 0, "refused": 0, "residency_before": "all", "residency_after": "all",
            "counters": counted(cycles, instructions) if counters else []}


def record(experiment, name, kernel, repetition, passes):
    return {"header": header(),
            "variant": {"experiment": experiment, "kernel": kernel,
                        "isa": simd_report.ISA[kernel], "from_auto": False, "arch": "arm64"},
            "mix": {"messages": MESSAGES}, "passes": passes, "peak_rss_bytes": 9 * 1024 ** 3,
            "refusals": [], "_variant": name, "_repetition": repetition}


def paired(experiment, name, kernel, repetition, work_s, walk_s=0.100, work_cycles=None,
           walk_cycles=400e6, fold=7, counters=True):
    work_cycles = work_s * 4e9 if work_cycles is None else work_cycles
    passes = [one_pass("walk", walk_s, fold=1, cycles=walk_cycles, instructions=900e6,
                       counters=counters),
              one_pass("work", work_s, fold=fold, cycles=work_cycles, instructions=1000e6,
                       counters=counters),
              one_pass("work", work_s, fold=fold, cycles=work_cycles, instructions=1000e6,
                       counters=counters),
              one_pass("walk", walk_s, fold=1, cycles=walk_cycles, instructions=900e6,
                       counters=counters)]
    return record(experiment, name, kernel, repetition, passes)


def manifest(experiment, records, withdrawn=()):
    names = list(dict.fromkeys(r["_variant"] for r in records))
    processes = [{"variant": r["_variant"], "repetition": r["_repetition"], "published": True,
                  "why": None, "stem": f"{r['_variant']}-{r['_repetition']}"} for r in records]
    processes += [{"variant": name, "repetition": 9, "published": False, "why": why,
                   "stem": f"{name}-9"} for name, why in withdrawn]
    return {"run_id": "P-0601-20261105T090000Z", "prediction": "P-0601",
            "experiment": experiment, "host_os": "Darwin", "repetitions": 3,
            "variants": names, "processes": processes, "sampler": "sampler.txt"}


def decode_records(neon_work=0.115):
    out = []
    for repetition in (1, 2, 3):
        out.append(paired("decode", "scalar", "scalar", repetition, 0.130 + 0.001 * repetition))
        out.append(paired("decode", "neon", "neon", repetition, neon_work + 0.001 * repetition))
    return out


class SimdReportTest(unittest.TestCase):
    def render(self, experiment, records, withdrawn=(), foreign_at=None):
        stderrs = [stderr_for(i) for i in range(len(records))]
        text, summary = simd_report.render(manifest(experiment, records, withdrawn), records,
                                           stderrs, sampler(len(records), foreign_at))
        return text, summary

    def violations(self, text, experiment="decode"):
        return [code for code, _ in
                simd_report.check_published(text, f"simd_{experiment}_mac_12302019.md")]

    def test_the_operation_is_work_less_walk_over_the_items(self):
        text, summary = self.render("decode", decode_records())
        # scalar: (0.132 - 0.100) s over 300,000 Add Orders, the median repetition.
        self.assertIn("| `scalar` | scalar: general-purpose registers, REV or BSWAP | 3 | 300,000 "
                      "| 132.000 | 100.000 | 106.667 | 103.333 | 110.000 |", text)
        # cycles: 0.132 s at 4 GHz is 528 M, less 400 M, over 300,000.
        self.assertIn("| 426.667 | 333.333 |", text)
        self.assertIn("neon beats scalar", summary)
        self.assertIn("E2 not refuted", summary)
        self.assertEqual(self.violations(text), [])

    def test_overlapping_repetitions_are_a_tie_and_a_scalar_win_refutes_e2(self):
        near = decode_records(neon_work=0.1305)
        text, summary = self.render("decode", near)
        self.assertIn("neon and scalar tie", summary)
        self.assertIn("E2 not refuted", summary)
        slower = decode_records(neon_work=0.140)
        text, summary = self.render("decode", slower)
        self.assertIn("scalar beats neon", summary)
        self.assertIn("E2 refuted", summary)
        self.assertEqual(self.violations(text), [])

    def consume(self, gain):
        out = []
        for repetition in (1, 2, 3):
            scalar_work = 0.200 + 0.0001 * repetition
            out.append(paired("consume", "scalar", "scalar", repetition, scalar_work))
            neon_work = 0.100 + (scalar_work - 0.100) * (1 - gain)
            out.append(paired("consume", "neon", "neon", repetition, neon_work))
        return out

    def test_e3_is_refuted_at_a_ten_percent_gain_and_not_below(self):
        _, summary = self.render("consume", self.consume(0.09))
        self.assertIn("neon beats scalar", summary)
        self.assertIn("E3 not refuted", summary)
        _, summary = self.render("consume", self.consume(0.11))
        self.assertIn("E3 refuted", summary)

    def test_e1_compares_each_width_with_scalar_at_that_width(self):
        records = []
        for width in (2, 4, 6, 8):
            for repetition in (1, 2, 3):
                records.append(paired("field", f"scalar-w{width}", "scalar", repetition,
                                      0.110 + 0.001 * repetition, fold=width))
                neon = 0.105 if width == 8 else 0.111
                records.append(paired("field", f"neon-w{width}", "neon", repetition,
                                      neon + 0.001 * repetition, fold=width))
        text, summary = self.render("field", records)
        self.assertIn("- Width 2: `neon-w2` and `scalar-w2` tie on cycles", text)
        self.assertIn("- Width 8: `neon-w8` beats `scalar-w8`", text)
        self.assertEqual(summary, "E1 refuted: a vector kernel beat scalar at some width")
        self.assertEqual(self.violations(text, "field"), [])

    def test_e4_parse_by_type_and_the_ten_percent_bar(self):
        def parse_record(name, kernel, repetition, all_s):
            passes = [one_pass("walk", 0.5, items=0, fold=3, cycles=2e9, instructions=4e9),
                      one_pass("all", all_s, items=ITEMS, fold=9, cycles=all_s * 4e9,
                               instructions=6e9)]
            for t in simd_report.OP_TYPES:
                passes.append(one_pass(t, 0.6, items=1000, fold=ord(t), cycles=2.4e9,
                                       instructions=4.1e9))
            passes += [one_pass("all", all_s, items=ITEMS, fold=9, cycles=all_s * 4e9,
                                instructions=6e9),
                       one_pass("walk", 0.5, items=0, fold=3, cycles=2e9, instructions=4e9)]
            return record("parse", name, kernel, repetition, passes)

        records = []
        for repetition in (1, 2, 3):
            records.append(parse_record("scalar", "scalar", repetition, 1.00 + 0.01 * repetition))
            records.append(parse_record("neon", "neon", repetition, 0.85 + 0.01 * repetition))
        text, summary = self.render("parse", records)
        # A: (0.6 - 0.5) s over 1,000 ops.
        self.assertIn("| A | `scalar` | scalar: general-purpose registers, REV or BSWAP | 1,000 "
                      "| 100,000.000 |", text)
        self.assertIn("E4 refuted", summary)
        self.assertEqual(self.violations(text, "parse"), [])

    def test_e9_compares_each_vector_compare_with_the_bitset(self):
        records = []
        for repetition in (1, 2, 3):
            records.append(paired("filter", "compare-scalar-n8", "scalar", repetition,
                                  0.150 + 0.001 * repetition, fold=40))
            records.append(paired("filter", "compare-neon-n8", "neon", repetition,
                                  0.140 + 0.001 * repetition, fold=40))
            records.append(paired("filter", "bitset-n8", "scalar", repetition,
                                  0.110 + 0.001 * repetition, fold=40))
        text, summary = self.render("filter", records)
        self.assertIn("`bitset-n8` beats `compare-neon-n8`", text)
        self.assertIn("not refuted", summary)
        self.assertEqual(self.violations(text, "filter"), [])

    def test_e11_needs_no_counters_and_is_refuted_when_scalar_is_not_beaten(self):
        records = []
        for repetition in (1, 2, 3):
            records.append(paired("e11", "scalar", "scalar", repetition, 0.150, counters=False))
            records.append(paired("e11", "wasm", "wasm", repetition, 0.120 + 0.001 * repetition,
                                  counters=False))
        text, summary = self.render("e11", records)
        self.assertIn("wasm beats scalar", summary)
        self.assertIn("E11 not refuted", summary)
        self.assertEqual(self.violations(text, "e11"), [])

    def test_e5_reads_the_run_lengths(self):
        records = decode_records()
        for one in records:
            one["variant"]["experiment"] = "batch"
            one["runs"] = {"buckets": ["1", "2", "3", "4-7", "8-15", "16-31", "32+"],
                           "any_type": [500, 200, 100, 100, 50, 30, 20],
                           "add_order": [100, 50, 50, 40, 30, 20, 10]}
        text, _ = self.render("batch", records)
        self.assertIn("- 33.33% of the day's Add Orders sit in runs of 4 or more", text)
        self.assertEqual(self.violations(text, "batch"), [])

    def test_a_result_finding_or_answer_unlike_the_table_is_caught(self):
        text, _ = self.render("decode", decode_records())
        row = next(line for line in text.splitlines() if line.startswith("| `neon` | 2 | work"))
        cells = row.split(" | ")
        slower = row.replace(f" {cells[5]} ", f" {float(cells[5]) + 0.5:.6f} ", 1)
        self.assertIn("results-differ", self.violations(text.replace(row, slower, 1)))
        wrong = row.replace(f" | {cells[6]} | ", f" | {int(cells[6]) + 1} | ", 1)
        self.assertIn("table-unusable", self.violations(text.replace(row, wrong, 1)))
        said = text.replace("**not refuted**", "**refuted**", 1)
        self.assertIn("finding-differs", self.violations(said))

    def test_names_isa_sampler_rule_and_privacy(self):
        text, _ = self.render("decode", decode_records())
        self.assertIn("host-label", [code for code, _ in simd_report.check_published(
            text, "simd_decode_debian_12302019.md")])
        unsaid = text.replace("- interference: the sampler rule was applied", "- interference: x")
        self.assertIn("sampler-rule-unsaid", self.violations(unsaid))
        no_isa = text.replace("neon: NEON (AArch64 Advanced SIMD, 128-bit)", "neon: ?", 1)
        self.assertIn("isa-unsaid", self.violations(no_isa))
        planted = "/" + "Users" + "/someone/data"  # In two pieces, so that the gate passes.
        private = text.replace("## Command", "## Command\n\n" + planted, 1)
        self.assertIn("private-text", self.violations(private))

    def test_a_published_process_must_have_been_sampled_quiet(self):
        with self.assertRaisesRegex(simd_report.ReportError, "not quiet"):
            self.render("decode", decode_records(), foreign_at=24)
        text, _ = self.render("decode", decode_records(),
                              withdrawn=[("neon", "the CPU sampler saw mds_stores at 31.0% CPU")])
        self.assertIn("- `neon`, repetition 9: the CPU sampler saw mds_stores", text)
        self.assertIn("- processes: 6 published, 1 withdrawn", text)

    def test_bad_records_are_refused(self):
        refused = decode_records()
        refused[1]["refusals"] = ["two work passes disagree"]
        with self.assertRaisesRegex(simd_report.ReportError, "refused"):
            self.render("decode", refused)
        other = decode_records()
        other[1]["variant"]["kernel"] = "scalar"
        other[1]["variant"]["isa"] = simd_report.ISA["scalar"]
        with self.assertRaisesRegex(simd_report.ReportError, "ran scalar"):
            self.render("decode", other)
        disagree = decode_records()
        disagree[1]["passes"][1]["fold"] = 8
        disagree[1]["passes"][2]["fold"] = 8
        with self.assertRaisesRegex(simd_report.ReportError, "folded"):
            self.render("decode", disagree)

    def test_debian_needs_core_4_the_governor_and_both_machine_checks(self):
        records = []
        for repetition in (1, 2, 3):
            for name, work in (("scalar", 0.130), ("avx2", 0.120)):
                one = paired("decode", name, name, repetition, work + 0.001 * repetition)
                one["header"].update(host=simd_report.DEBIAN, governor="performance",
                                     pinning="pinned to core 4: pthread_setaffinity_np "
                                             "returned 0, read back [4]")
                one["pinned"] = True
                for item in one["passes"]:
                    mac = {c["name"].split(" ")[0]: c["value"] for c in item["counters"]}
                    item["counters"] = [{"name": event, "state": "measured",
                                         "value": mac.get(event, 1000), "ratio": 1.0,
                                         "reason": ""}
                                        for event in simd_report.EVENTS[simd_report.DEBIAN]]
                records.append(one)
        whole = manifest("decode", records)
        whole["sampler"] = None
        stderrs = [stderr_for(i) for i in range(len(records))]
        with self.assertRaisesRegex(simd_report.ReportError, "machine check"):
            simd_report.render(whole, records, stderrs, None)
        whole["machine_check_before"] = "machine_check: passed"
        whole["machine_check_after"] = "machine_check: passed"
        text, summary = simd_report.render(whole, records, stderrs, None)
        self.assertIn("avx2 beats scalar", summary)
        name = "simd_decode_debian_12302019.md"
        self.assertEqual(simd_report.check_published(text, name), [])
        unpinned = text.replace("read back [4]", "read back [0-15]", 1)
        self.assertIn("pin-failed", [c for c, _ in simd_report.check_published(unpinned, name)])
        failed = text.replace("machine_check: passed", "machine_check: FAIL governor", 1)
        self.assertIn("machine-check-failed",
                      [c for c, _ in simd_report.check_published(failed, name)])
        records[0]["pinned"] = False
        with self.assertRaisesRegex(simd_report.ReportError, "not pinned"):
            simd_report.render(whole, records, stderrs, None)

    def test_main_writes_the_file_and_its_run_log_row(self):
        with tempfile.TemporaryDirectory() as scratch:
            run = Path(scratch) / "run"
            run.mkdir()
            records = decode_records()
            whole = manifest("decode", records)
            for index, one in enumerate(records):
                stem = whole["processes"][index]["stem"]
                kept = copy.deepcopy(one)
                del kept["_variant"], kept["_repetition"]
                (run / f"{stem}.json").write_text(json.dumps(kept), encoding="utf-8")
                (run / f"{stem}.err").write_text(stderr_for(index), encoding="utf-8")
            (run / "sampler.txt").write_text(sampler(len(records)), encoding="utf-8")
            (run / "manifest.json").write_text(json.dumps(whole), encoding="utf-8")
            out = Path(scratch) / "simd_decode_mac_12302019.md"
            log = Path(scratch) / "run_log.md"
            argv = ["--run-dir", str(run), "--out", str(out), "--run-log", str(log)]
            self.assertEqual(simd_report.main(argv), 0)
            self.assertIn("| P-0601-20261105T090000Z_report | P-0601 |", log.read_text())
            self.assertEqual(simd_report.main(["--check", str(out)]), 0)
            out.write_text(out.read_text().replace("E2 not refuted", "E2 refuted"))
            self.assertEqual(simd_report.main(["--check", str(out)]), 1)
            whole["repetitions"] = 4
            (run / "manifest.json").write_text(json.dumps(whole), encoding="utf-8")
            self.assertEqual(simd_report.main(argv), 2)


if __name__ == "__main__":
    unittest.main()
