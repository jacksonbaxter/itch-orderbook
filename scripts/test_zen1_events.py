#!/usr/bin/env python3
"""Tests for zen1_events.py on `perf stat` output invented here: how each event's state is
read, the per-message figures, and that --check catches a file that leaks or drops a name."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zen1_events  # noqa: E402

MESSAGES = 1000
CHECK = "machine_check: pass governor: core 4 runs performance\nmachine_check: passed\n"


def csv_line(event, value, running="100.00"):
    return f"# started on a day\n\n{value},,{event},1000,{running},,\n"


class Zen1Events(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / "build"
        base.mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=base)
        root = self.root = Path(self.scratch.name)
        a, b = root / "a", root / "b"
        a.mkdir()
        b.mkdir()
        exits = []
        for spec, value, code in (("cycles:u", "5000", 0), ("r0843:u", "0", 0),
                                  ("ref-cycles:u", "<not supported>", 0),
                                  ("cycles:k", "", 255), ("cycles:upp", "", 255)):
            name = spec.replace(":", "_")
            if value:
                (a / f"{name}.csv").write_text(csv_line(spec, value), encoding="utf-8")
            (a / f"{name}.err").write_text("measure.sh: start\nNo permission to enable event\n",
                                           encoding="utf-8")
            exits.append(f"{spec} exit {code}")
        exits.append("lbr exit 255")
        (a / "lbr.err").write_text("PMU Hardware doesn't support sampling/overflow-interrupts\n",
                                   encoding="utf-8")
        (a / "exits.txt").write_text("\n".join(exits) + "\n", encoding="utf-8")
        (b / "group1.csv").write_text(
            csv_line("r0843:u", 2 * MESSAGES * 2) + "5000,,cache-misses:u,1000,100.00,,\n"
            + "5001,,r0964:u,1000,100.00,,\n" + f"{2 * MESSAGES * 2},,branch-misses:u,1000,"
            "99.50,,\n" + "300,,dTLB-load-misses,1000,100.00,,\n", encoding="utf-8")
        record = {"header": {"host": "debian", "cpu": "AMD Ryzen Embedded V1605B",
                             "kernel": "Linux 6.12", "commit": "0123456789ab (clean)",
                             "dataset": "12302019.NASDAQ_ITCH50"},
                  "mix": {"messages": MESSAGES}}
        (root / "run.json").write_text(json.dumps(record), encoding="utf-8")
        for name in ("before", "after"):
            (root / f"{name}.out").write_text(CHECK, encoding="utf-8")
        self.out = root / "counters_zen1_events_debian.md"

    def tearDown(self):
        self.scratch.cleanup()

    def write(self):
        root = self.root
        return zen1_events.main(["--part-a", str(root / "a"), "--part-b", str(root / "b"),
                                 "--record", str(root / "run.json"), "--machine-check",
                                 str(root / "before.out"), str(root / "after.out"),
                                 "--prediction", "P-0016", "--command", "run1.sh",
                                 "--commit", "0123456789ab (exported by debian_sync.sh)",
                                 "--dataset", "12302019.NASDAQ_ITCH50",
                                 "--out", str(self.out)])

    def row(self, event):
        for line in self.out.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"| `{event}` |"):
                return [cell.strip() for cell in line.strip("|").split("|")]
        self.fail(f"no row for {event}")

    def test_each_state_is_read_from_the_run(self):
        self.assertEqual(self.write(), 0)
        self.assertEqual(self.row("cycles")[2:4], ["counts", "5,000"])
        self.assertEqual(self.row("r0843")[2], "counts zero")
        self.assertEqual(self.row("ref-cycles")[2], "not supported")
        self.assertEqual(self.row("cycles:k")[2], "refused, exit 255")
        self.assertEqual(self.row("lbr")[2], "refused, exit 255")
        self.assertEqual(self.row("instructions")[2], "not run")

    def test_per_message_figures_divide_by_both_replays(self):
        self.assertEqual(self.write(), 0)
        self.assertEqual(self.row("r0843")[4], "2.0000")
        self.assertEqual(self.row("dTLB-load-misses")[4], "0.1500")
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("| `r0964:u` against `cache-misses:u`, both L2 misses, relative gap "
                      "| 0.020% | under 0.1% | yes |", text)
        self.assertIn("| every Part B event at 100% running | branch-misses:u | all | no |",
                      text)

    def test_the_written_file_passes_its_check_and_a_dropped_name_does_not(self):
        self.assertEqual(self.write(), 0)
        self.assertEqual(zen1_events.main(["--check", str(self.out)]), 0)
        text = self.out.read_text(encoding="utf-8")
        self.out.write_text(text.replace("L2 misses: data cache", "Misses: data cache"),
                            encoding="utf-8")
        self.assertEqual(zen1_events.main(["--check", str(self.out)]), 1)

    def test_check_catches_a_state_it_never_writes(self):
        self.assertEqual(self.write(), 0)
        text = self.out.read_text(encoding="utf-8")
        self.out.write_text(text.replace("| not supported |", "| works |", 1), encoding="utf-8")
        codes = [code for code, _ in zen1_events.check_published(self.out.read_text("utf-8"))]
        self.assertIn("state-unknown", codes)


if __name__ == "__main__":
    unittest.main()
