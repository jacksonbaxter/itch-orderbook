#!/usr/bin/env python3
"""oracle_sessions.py on synthetic oracle results files.

Each case breaks one thing in a set of sessions that meets T-ORACLE-2, and requires the
verdict, the count or the gap that follows from it.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import oracle_sessions  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)

DATASETS = list(oracle_sessions.CARTERET)


def oracle_file(dataset, messages=310_000_000, host="Darwin arm64 Apple M4 Max", verdict="PASS",
                divergences=0, arms=(("paged_flat", "PASS"),), diffed=None):
    """An oracle results file in oracle_report.py's layout, as far as the summary reads it."""
    diffed = messages if diffed is None else diffed
    rows = "\n".join(
        f"| `{arm}` | {arm_verdict} | PASS | FAIL, 9 crossed and 1 locked | PASS | PASS | "
        f"{diffed:,} | `9e3779b97f4a7c15` | 1 | 1 | 1.0 | 1.00 | 1.00 | `raw_{arm}.log` |"
        for arm, arm_verdict in arms)
    return f"""# Oracle replay of X

- Commit: abc1234 (clean)
- Host: {host}. Mac numbers stay labelled macOS.
- Dataset: `{dataset}`, SHA-256 of the unpacked file `00`

## Every arm of the fast book

| Arm | T-ORACLE-1 | T-INV-1 | T-INV-2 strict | T-INV-3 | T-ALLOC-1 | Messages diffed | Last hash | Moves reported | Moved levels compared | Replay, s | Maximum RSS, GiB | Peak footprint, GiB | Raw log |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
{rows}

## T-ORACLE-1: the reference book agrees after every message

Verdict: **{verdict}**

| Check | Count |
|---|---|
| Messages in the file (manifest) | {messages:,} |
| Messages diffed | {diffed:,} |
| Divergences | {divergences:,} |

## T-INV-1: every execute, cancel, delete and replace names a live order
"""


class Sessions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "results").mkdir()
        self.files = {f"day{index:02d}": oracle_file(dataset)
                      for index, dataset in enumerate(DATASETS)}

    def write(self):
        for day, text in self.files.items():
            (self.root / "results" / f"oracle_{day}.md").write_text(text, encoding="utf-8")

    def run_tool(self, *flags):
        self.write()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = oracle_sessions.main(["--root", str(self.root), *flags])
        return code, out.getvalue()

    def verdict(self):
        code, text = self.run_tool()
        self.assertEqual(code, 0, text)
        return next(line for line in text.splitlines() if line.startswith("Verdict:"))

    def test_ten_clean_sessions_over_the_bar_pass(self):
        self.assertEqual(self.verdict(), "Verdict: **PASS**, 0 divergences in 3,100,000,000 "
                         "messages over 10 sessions, 62,921,530 messages over the bar.")

    def test_nine_sessions_fail_with_the_gap(self):
        del self.files["day03"]
        self.assertIn("**FAIL**", self.verdict())
        self.assertIn("the gap: 9 sessions of 10; 2,790,000,000 messages of 3,037,078,470",
                      self.verdict())
        self.assertIn(f"- `{DATASETS[3]}`", self.run_tool()[1])

    def test_nine_sessions_over_the_messages_still_fail(self):
        del self.files["day03"]
        for day in self.files:
            dataset = DATASETS[int(day[3:])]
            self.files[day] = oracle_file(dataset, messages=340_000_000)
        line = self.verdict()
        self.assertIn("**FAIL**, 0 divergences in 3,060,000,000 messages over 9 sessions; the gap: "
                      "9 sessions of 10.", line)

    def test_ten_sessions_under_the_messages_fail(self):
        self.files["day00"] = oracle_file(DATASETS[0], messages=200_000_000)
        self.assertIn("**FAIL**", self.verdict())
        self.assertIn("2,990,000,000 messages of 3,037,078,470, 47,078,470 short", self.verdict())

    def test_a_divergence_fails_and_the_session_is_not_counted(self):
        self.files["day01"] = oracle_file(DATASETS[1], verdict="FAIL", divergences=1)
        line = self.verdict()
        self.assertIn("**FAIL**, 1 divergences in 2,790,000,000 messages over 9 sessions", line)
        self.assertIn("- day01: T-ORACLE-1 FAIL, arms failing: none; 1 divergences.",
                      self.run_tool()[1])

    def test_an_arm_that_failed_keeps_the_session_out(self):
        self.files["day02"] = oracle_file(DATASETS[2], arms=(("hash_linked", "PASS"),
                                                             ("paged_flat", "FAIL")))
        self.assertIn("over 9 sessions", self.verdict())
        self.assertIn("arms failing: paged_flat", self.run_tool()[1])

    def test_part_of_a_day_keeps_the_session_out(self):
        self.files["day04"] = oracle_file(DATASETS[4], diffed=309_999_999)
        self.assertIn("over 9 sessions", self.verdict())
        self.assertIn("309,999,999 of 310,000,000 messages diffed", self.run_tool()[1])

    def test_a_linux_session_keeps_the_session_out(self):
        self.files["day05"] = oracle_file(DATASETS[5], host="Linux x86_64 AMD Ryzen")
        self.assertIn("over 9 sessions", self.verdict())
        self.assertIn("not macOS", self.run_tool()[1])

    def test_a_session_counted_twice_is_refused(self):
        self.files["day99"] = oracle_file(DATASETS[0])
        code, text = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn(f"both replay {DATASETS[0]}", text)

    def test_check_passes_on_a_fresh_file_and_fails_on_an_edit(self):
        self.assertEqual(self.run_tool("--write")[0], 0)
        self.assertEqual(self.run_tool("--check")[0], 0)
        out = self.root / "results" / oracle_sessions.OUT
        out.write_text(out.read_text(encoding="utf-8").replace("**PASS**", "**FAIL**"),
                       encoding="utf-8")
        code, text = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn("differs from a fresh run", text)

    def test_its_own_file_is_not_a_session(self):
        self.run_tool("--write")
        self.assertIn("over 10 sessions", self.verdict())


if __name__ == "__main__":
    unittest.main()
