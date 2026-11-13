#!/usr/bin/env python3
"""review_report.py on a made-up record of two reviewers, r1 and r12 (one id the start of the
other): the counts are the rows, an unknown outcome or severity is refused, an open finding
cannot be rendered as complete, evidence not in the tree is refused, and the committed file is a
fresh render whose evidence is in the tree. Scratch goes under build/.

    python3 -B scripts/test_review_report.py
"""

import contextlib
import copy
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_report  # noqa: E402

REPO_ROOT = review_report.REPO_ROOT
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
NOTE_SECTION = "Found by the review and left as it is"


def finding(fid, severity, outcome, **evidence):
    record = {"id": fid, "severity": severity, "finding": f"what {fid} found", "outcome": outcome,
              "detail": "", "commits": [], "tests": [], "faults": [], "notes": []}
    record.update(evidence)
    return record


DATA = {
    "checked_at": "abcdef1",
    "rechecked": [],
    "complete": False,
    "reviewers": [
        {"id": "r1", "read": "the parser", "commits": ["1234567"],
         "looks": [{"BLOCK": 1, "FIX": 1, "NOTE": 0}, {"BLOCK": 0, "FIX": 0, "NOTE": 1}],
         "last_words": "", "branch": ""},
        {"id": "r12", "read": "the pages", "commits": ["1234567"],
         "looks": [{"BLOCK": 0, "FIX": 1, "NOTE": 1}],
         "last_words": "Nothing is left that I would block a merge for.", "branch": ""},
    ],
    "to_come": [],
    "findings": [
        finding("r1-1", "BLOCK", "fixed", commits=["1234567"], tests=["Reader.CountsEveryFrame"],
                faults=["parser-frame-skipped"]),
        finding("r1-2", "FIX", "known limit", notes=[NOTE_SECTION]),
        finding("r1-3", "NOTE", "no change needed"),
        finding("r12-1", "FIX", "fixed", commits=["1234567"], tests=["test_a_page_line"]),
        finding("r12-2", "NOTE", "fixed", commits=["1234567"]),
    ],
}


class ReviewReport(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        for folder in ("scripts/faults", "results", "docs", "tests"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "scripts" / "faults" / "parser.json").write_text(json.dumps(
            {"area": "parser", "faults": [{"id": "parser-frame-skipped"}]}), encoding="utf-8")
        (self.root / "tests" / "test_reader.cpp").write_text(
            "TEST(Reader, CountsEveryFrame) {}\n", encoding="utf-8")
        (self.root / "scripts" / "test_pages.py").write_text(
            "def test_a_page_line(self):\n    pass\n", encoding="utf-8")
        (self.root / "docs" / "NOTES.md").write_text(
            f"# Notes\n\n## {NOTE_SECTION}, 2026-11-13\n\nText.\n", encoding="utf-8")
        self.data = copy.deepcopy(DATA)
        self.out = self.root / review_report.OUT

    def run_step(self, step):
        (self.root / review_report.DATA).write_text(json.dumps(self.data), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review_report.main([step, "--root", str(self.root)])
        return code, out.getvalue()

    def refused(self, code_word):
        code, output = self.run_step("write")
        self.assertEqual(code, 1, output)
        self.assertIn(f"review_report: FAIL {code_word} ", output)
        self.assertFalse(self.out.exists(), "a refused record was written")
        return output

    def counts_rows(self, text, heading):
        part = text.split(heading, 1)[1].split("\n\n", 2)[1]
        return {line.split(" | ")[0].strip("| "): [int(cell) for cell in
                                                   line.strip("| ").split(" | ")[1:]]
                for line in part.splitlines()[2:]}

    def test_the_counts_are_the_rows(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        rows = len(re.findall(r"^\| r\d+-\d+ \|", text, re.MULTILINE))
        self.assertEqual(rows, len(self.data["findings"]))
        self.assertIn(f"- Findings: {rows}\n", text)
        self.assertIn("- Severities: 1 BLOCK, 2 FIX, 2 NOTE\n", text)
        self.assertIn("- Outcomes: 3 fixed, 1 known limit, 1 no change needed\n", text)
        by_reviewer = self.counts_rows(text, "By reviewer and outcome.")
        by_severity = self.counts_rows(text, "By severity and outcome.")
        self.assertEqual(by_reviewer["All"][-1], rows)
        self.assertEqual(by_severity["All"], by_reviewer["All"])
        self.assertEqual([by_reviewer["r1"][-1], by_reviewer["r12"][-1]], [3, 2])
        self.assertEqual(sum(by_reviewer["All"][:-1]), rows)
        self.assertEqual([by_severity[s][-1] for s in review_report.SEVERITIES], [1, 2, 2])

    def test_only_a_reviewer_with_more_than_one_look_is_said_to_have_looked_again(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Looked again: 1 of 2 reviewers (r1); every other outcome was checked "
                      "against the tree by the record's author\n", text)
        self.assertIn("| Looked again at the fixes | 1 | 2 |\n| Read once | 1 | 2 |\n", text)

    def test_counts_that_are_not_the_rows_are_refused(self):
        self.data["reviewers"][0]["looks"][1]["NOTE"] = 2
        self.assertIn("r1: its looks state", self.refused("count"))
        self.data = copy.deepcopy(DATA)
        del self.data["findings"][-1]
        self.refused("count")

    def test_an_unknown_outcome_is_refused(self):
        self.data["findings"][1]["outcome"] = "deferred"
        self.assertIn("r1-2: 'deferred'", self.refused("outcome"))

    def test_an_unknown_severity_is_refused(self):
        self.data["findings"][2]["severity"] = "MINOR"
        self.assertIn("r1-3: 'MINOR'", self.refused("severity"))

    def test_a_fixed_finding_with_no_commit_is_refused_unless_it_says_why(self):
        self.data["findings"][4]["commits"] = []
        self.assertIn("r12-2: fixed with no commit", self.refused("evidence"))
        self.data["findings"][4]["no_commit"] = "the fix was to a file kept outside the repository"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("No commit: the fix was to a file kept outside the repository.",
                      self.out.read_text(encoding="utf-8"))

    def test_a_known_limit_with_no_notes_section_is_refused(self):
        self.data["findings"][1]["notes"] = []
        self.assertIn("r1-2: a known limit with no docs/NOTES.md section", self.refused("evidence"))

    def test_the_page_says_what_the_script_cannot_check(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("It cannot tell whether the evidence named is the right evidence",
                      self.out.read_text(encoding="utf-8"))

    def test_an_id_given_twice_or_of_no_reviewer_is_refused(self):
        self.data["findings"][4]["id"] = "r12-1"
        self.refused("id")
        self.data = copy.deepcopy(DATA)
        self.data["findings"][4]["id"] = "r3-1"
        self.refused("id")

    def test_an_open_finding_cannot_be_rendered_complete(self):
        self.data["complete"] = True
        for outcome in review_report.UNSETTLED:
            with self.subTest(outcome=outcome):
                self.data["findings"][3]["outcome"] = outcome
                self.assertIn("r12-1", self.refused("complete"))
        self.data["findings"][3]["outcome"] = "fixed"
        self.data["to_come"] = ["the last reviewer"]
        self.refused("complete")
        self.data["to_come"] = []
        self.data["reviewers"][1]["branch"] = "a branch at 1234567"
        self.refused("complete")
        self.data["reviewers"][1]["branch"] = ""
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Result: complete:", self.out.read_text(encoding="utf-8"))

    def test_a_record_not_complete_says_what_is_left(self):
        self.data["findings"][3]["outcome"] = "open"
        self.data["to_come"] = ["the last reviewer reads the pages"]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Result: not complete: 1 open, 1 reviewer to come\n", text)
        self.assertIn("- r12-1 (FIX, open): what r12-1 found.", text)
        self.assertIn("- STILL TO COME: the last reviewer reads the pages", text)

    def test_an_outcome_checked_again_is_named_with_its_commit(self):
        self.data["rechecked"] = [{"what": "r1-2", "at": "fedcba9"}]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Commit: abcdef1, where every outcome was checked, but these, checked again "
                      "later: r1-2 at fedcba9\n", self.out.read_text(encoding="utf-8"))
        self.out.unlink()
        self.data["rechecked"] = [{"what": "r1-2", "at": "the merge"}]
        self.assertIn("'the merge' is not a commit", self.refused("data"))

    def test_check_passes_a_fresh_render(self):
        self.assertEqual(self.run_step("write")[0], 0)
        code, output = self.run_step("check")
        self.assertEqual(code, 0, output)
        self.assertIn("commits not checked", output)

    def test_a_hand_edit_is_caught(self):
        self.assertEqual(self.run_step("write")[0], 0)
        text = self.out.read_text(encoding="utf-8")
        self.out.write_text(text.replace("| r1-2 | FIX | what r1-2 found | known limit |",
                                         "| r1-2 | FIX | what r1-2 found | fixed |"),
                            encoding="utf-8")
        code, output = self.run_step("check")
        self.assertEqual(code, 1, output)
        self.assertIn("review_report: FAIL stale results/final_review.md", output)

    def test_evidence_not_in_the_tree_is_refused(self):
        self.assertEqual(self.run_step("write")[0], 0)
        for key, value, code_word in (("faults", ["parser-frame-not-skipped"], "fault"),
                                      ("tests", ["Reader.CountsNoFrame"], "test"),
                                      ("notes", ["A section that is not there"], "notes")):
            with self.subTest(code=code_word):
                self.data = copy.deepcopy(DATA)
                self.data["findings"][0][key] = value
                self.assertEqual(self.run_step("write")[0], 0)
                code, output = self.run_step("check")
                self.assertEqual(code, 1, output)
                self.assertIn(f"review_report: FAIL {code_word} r1-1: ", output)

    def test_the_evidence_of_a_branch_not_merged_is_not_looked_for(self):
        self.data["reviewers"][0]["branch"] = "a branch at 1234567"
        self.data["findings"][0]["faults"] = ["a-fault-on-that-branch"]
        self.assertEqual(self.run_step("write")[0], 0)
        self.assertEqual(self.run_step("check")[0], 0)
        self.assertIn("r1's fixes not merged yet", self.out.read_text(encoding="utf-8"))


class Committed(unittest.TestCase):
    def test_the_committed_file_is_a_fresh_render_with_its_evidence_in_the_tree(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review_report.main(["check"])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
