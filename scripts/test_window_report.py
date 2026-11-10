#!/usr/bin/env python3
"""Tests for window_report.py, on itch_window output invented here: what it publishes, what it
refuses, and that --check catches a published file whose tables or summary were edited."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import window_report  # noqa: E402

TIME = "  9000000000  maximum resident set size\n"
COMMIT = "0123456789ab (clean)"


def window_output(adds_outside=(400, 300, 200, 120, 80, 40, 30), walk="clean", refused=0):
    lines = ["messages 10000", f"refused {refused}", f"walk {walk}"]
    for kind, total, outside in (("adds", 4000, adds_outside),
                                 ("named", 3000, (300, 200, 100, 60, 40, 25, 20))):
        lines += [f"{kind} {total}", f"{kind}_opens_side 10", f"{kind}_improves 500",
                  f"{kind}_subcent 20"]
        lines += [f"outside {kind} {width} {count}"
                  for width, count in zip(window_report.WIDTHS, outside)]
    for kind, buckets in (("add", (2000, 800, 500, 300, 200, 150, 40, 10)),
                          ("close", (900, 50, 30, 10, 5, 4, 1, 0))):
        lines.append(f"{kind}_searches {sum(buckets)}")
        lines += [f"better {kind} {depth} {count}"
                  for depth, count in zip(window_report.DEPTHS, buckets)]
    return "\n".join(lines) + "\n"


def render(text=None):
    return window_report.render(text or window_output(), TIME, COMMIT, "12302019", "P-0024")


def codes(text):
    return {code for code, _ in window_report.check_published(text)}


class WindowReport(unittest.TestCase):
    def test_a_clean_report_passes_its_own_check(self):
        text = render()
        self.assertEqual(codes(text), set())
        self.assertIn("- summary: adds outside a window of 256 ticks 5.000%, of 2,048 1.000% "
                      "(carteret 4.90% and 1.03%); sub-cent 0.500%; add searches past 2 or "
                      "fewer better levels 82.50%, past 16 or more 5.00%\n", text)
        self.assertIn("| 256 | 200 | 100 | 5.000% | 3.333% |", text)

    def test_a_walk_that_was_not_clean_is_refused(self):
        with self.assertRaises(window_report.ReportError):
            render(window_output(walk="not clean"))
        with self.assertRaises(window_report.ReportError):
            render(window_output(refused=1))

    def test_output_with_a_count_missing_is_refused(self):
        text = window_output().replace("better close 256+ 0\n", "")
        with self.assertRaises(window_report.ReportError):
            render(text)

    def test_check_catches_a_count_edited_after_the_fact(self):
        text = render().replace("| 256 | 200 | 100 | 5.000%", "| 256 | 150 | 100 | 5.000%", 1)
        self.assertIn("share-differs", codes(text))

    def test_check_catches_a_wider_window_holding_less(self):
        self.assertIn("not-monotone", codes(render(window_output(
            adds_outside=(400, 300, 200, 220, 80, 40, 30)))))

    def test_check_catches_depths_that_do_not_add_up(self):
        text = render()
        edited = text.replace("| all | 4,000 |", "| all | 4,001 |", 1)
        self.assertIn("depths-not-tiled", codes(edited))

    def test_check_catches_a_summary_unlike_the_tables(self):
        text = render().replace("window of 256 ticks 5.000%", "window of 256 ticks 4.000%", 1)
        self.assertIn("summary-differs", codes(text))

    def test_check_catches_a_home_path(self):
        self.assertIn("private-text", codes(render() + "\n/Users/someone/data\n"))

    def test_the_command_line_writes_and_checks(self):
        base = Path(__file__).resolve().parent.parent / "build"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as scratch:
            root = Path(scratch)
            counts, time, log = root / "window.out", root / "time.err", root / "run_log.md"
            counts.write_text(window_output(), encoding="utf-8")
            time.write_text(TIME, encoding="utf-8")
            log.write_text("", encoding="utf-8")
            out = root / "window_12302019.md"
            raw = ["--counts", str(counts), "--time", str(time), "--commit", COMMIT,
                   "--prediction", "P-0024"]
            self.assertEqual(window_report.main([*raw, "--out", str(out), "--run-log", str(log)]),
                             0)
            self.assertIn("| P-0024-window-12302019 | P-0024 |", log.read_text())
            self.assertEqual(window_report.main(["--check", str(out), *raw]), 0)
            out.write_text(out.read_text() + "\nextra\n", encoding="utf-8")
            self.assertEqual(window_report.main(["--check", str(out), *raw]), 1)


if __name__ == "__main__":
    unittest.main()
