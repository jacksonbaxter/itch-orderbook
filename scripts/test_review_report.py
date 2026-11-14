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
    "unread": [],
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
    "gate": {
        "at": "abcdef1",
        "checkers": [{"id": "reader", "read": "the pages as a stranger would"},
                     {"id": "pages", "read": "every figure of the pages"}],
        "findings": [
            {"checker": "reader", "label": "1", "severity": "FIX", "finding": "too long",
             "outcome": "fixed", "detail": "", "commits": ["1234567"], "tests": [],
             "faults": ["parser-frame-skipped"], "notes": []},
            {"checker": "pages", "label": "B1", "severity": "BLOCK", "finding": "a stale claim",
             "outcome": "known limit", "detail": "", "commits": [], "tests": [], "faults": [],
             "notes": [NOTE_SECTION]},
        ],
    },
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
        self.assertIn("- Looked again at the fixes of their own findings: 1 of 2 reviewers (r1); "
                      "every other outcome was checked "
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
        for mark in (" ", "x", "n/a", "none", "not needed"):
            with self.subTest(no_commit=mark):
                self.data["findings"][4]["no_commit"] = mark
                self.assertIn("r12-2: ", self.refused("evidence"))
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
        self.assertIn("- Result: not complete. Waiting on no one: r12-1, open. 1 reviewer to "
                      "come.\n", text)
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

    def test_the_result_says_what_waits_and_on_whom(self):
        self.data["findings"][3].update(outcome="partly", waits_on="the owner",
                                        waits_for="a run on its own day")
        self.data["findings"][2]["outcome"] = "left for the owner"
        self.data["findings"][4]["outcome"] = "open"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Result: not complete. Waiting on the owner: r12-1 (a run on its own day), "
                      "partly; r1-3, left for the owner. Waiting on no one: r12-2, open.\n",
                      self.out.read_text(encoding="utf-8"))
        self.out.unlink()
        self.data["findings"][4]["outcome"] = "fixed"
        self.data["findings"][4]["waits_on"] = "the owner"
        self.assertIn("r12-2: its waits_on is given, yet it is fixed", self.refused("data"))

    PAGE = ("# A write-up\n\n### The final review\n\nThere were 2 reviewers and 5 findings: 1 BLOCK, 2 FIX "
            "and 2 NOTE ([results/final_review.md](results/final_review.md)). The reviewers who "
            "looked again at the fixes were 1 of 2, r1. Of the findings, 3 are fixed, 1 is a known "
            "limit and 1 needed no change, and T-PARSE-3 and r12 are named.\n\n## Next\n\n7 more.\n")

    def page_check(self, old=None, new=None):
        page = self.PAGE if old is None else self.PAGE.replace(old, new, 1)
        (self.root / "WRITEUP.md").write_text(page, encoding="utf-8")
        self.assertEqual(self.run_step("write")[0], 0)
        return self.run_step("check")

    def test_the_pages_review_counts_are_the_records_totals(self):
        code, output = self.page_check()
        self.assertEqual(code, 0, output)

    def test_a_page_count_that_is_not_the_records_total_is_refused(self):
        for old, new, said in (("There were 2 reviewers", "There were 3 reviewers", "There were 3"),
                               ("1 BLOCK, 2 FIX", "2 BLOCK, 2 FIX", "2 BLOCK"),
                               ("were 1 of 2", "were 2 of 2", "were 2 of 2"),
                               ("3 are fixed", "4 are fixed", "4 are fixed"),
                               ("1 is a known", "2 are a known", "2 are a known"),
                               ("1 needed no change", "1 needed no change, 1 is open",
                                "1 is open"),
                               ("1 needed no change", "1 needed no change, and 9 more",
                                "gives 9, which is not one of the record's counts"),
                               (" 3 are fixed,", " most are fixed,", "does not give (3,)")):
            with self.subTest(new=new):
                code, output = self.page_check(old, new)
                self.assertEqual(code, 1, output)
                self.assertIn("review_report: FAIL page WRITEUP.md: ", output)
                self.assertIn(said, output)

    ONE_SENTENCE = ("Reviewers who did not write the code read it area by area, and every finding "
                    "with what became of it is in [results/final_review.md]"
                    "(results/final_review.md).")

    def test_a_paragraph_that_gives_no_count_is_taken(self):
        paragraph = self.PAGE.split("### The final review\n\n", 1)[1].split("\n\n", 1)[0]
        code, output = self.page_check(paragraph, self.ONE_SENTENCE)
        self.assertEqual(code, 0, output)
        for new, said in ((self.ONE_SENTENCE + " There were 16 of them.",
                           "gives 16, which is not one of the record's counts"),
                          (self.ONE_SENTENCE.replace("Reviewers", "There were 3 reviewers and 5 "
                                                     "findings: 1 BLOCK, 2 FIX and 2 NOTE. "
                                                     "Reviewers"), "There were 3"),
                          (self.ONE_SENTENCE + " Of the findings, 3 are fixed.",
                           "does not give (2, 5, 1, 2, 2)")):
            with self.subTest(new=new):
                code, output = self.page_check(paragraph, new)
                self.assertEqual(code, 1, output)
                self.assertIn("review_report: FAIL page WRITEUP.md: ", output)
                self.assertIn(said, output)

    def test_what_no_reviewer_read_is_said(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("## Read by no reviewer\n\nNothing: every change was read by a reviewer",
                      self.out.read_text(encoding="utf-8"))
        self.data["unread"] = ["the fixes of r12's findings"]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("## Read by no reviewer\n\n- the fixes of r12's findings\n",
                      self.out.read_text(encoding="utf-8"))

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

    def test_a_file_given_as_a_test_must_be_a_test(self):
        for name in ("tools/capacity.hpp", "results/window_12302019.md", "docs/demo.js",
                     "scripts/mac_rule.py", "scripts/mac_sampler.sh", "bench/replay.hpp"):
            with self.subTest(test=name):
                self.data = copy.deepcopy(DATA)
                self.data["findings"][3]["tests"] = ["test_a_page_line", name]
                self.assertIn(f"r12-1: '{name}' is given as a test", self.refused("evidence"))
        self.data = copy.deepcopy(DATA)
        self.data["gate"]["findings"][0]["tests"] = ["scripts/run_record.py"]
        self.assertIn("reader 1: 'scripts/run_record.py' is given as a test",
                      self.refused("evidence"))
        self.data = copy.deepcopy(DATA)
        self.data["findings"][3]["tests"] = ["scripts/test_pages.py", "tests/test_reader.cpp",
                                             "tests/skip_probe.cpp", "fuzz/fuzz_kernels.cpp",
                                             "Reader.CountsEveryFrame", "Built/Kernel.Case"]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)

    def test_the_gate_is_recorded_beside_the_reviewers(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Acceptance gate: at abcdef1, 2 checkers, 2 findings (1 BLOCK, 1 FIX, "
                      "0 NOTE); 1 fixed, 1 known limit\n", text)
        self.assertIn("- Findings: 5\n", text)
        self.assertIn("| reader | the pages as a stranger would | 0 | 1 | 0 | 1 |\n", text)
        self.assertIn("| All |  | 1 | 1 | 0 | 2 |\n", text)
        self.assertIn("| pages | B1 | BLOCK | a stale claim | known limit | docs/NOTES.md, ", text)
        self.assertEqual(self.counts_rows(text, "By checker and outcome.")["All"],
                         [1, 1, 0, 0, 0, 0, 2])

    def test_a_gate_finding_of_no_checker_or_given_twice_is_refused(self):
        self.data["gate"]["findings"][1]["checker"] = "code"
        self.assertIn("code B1: not a finding of a listed checker", self.refused("id"))
        self.data = copy.deepcopy(DATA)
        self.data["gate"]["findings"][1].update(checker="reader", label="1")
        self.assertIn("reader 1: given twice", self.refused("id"))
        self.data = copy.deepcopy(DATA)
        self.data["gate"]["findings"][1]["label"] = ""
        self.refused("id")
        self.data = copy.deepcopy(DATA)
        self.data["gate"]["findings"][1]["outcome"] = "waived"
        self.assertIn("pages B1: 'waived'", self.refused("outcome"))
        self.data = copy.deepcopy(DATA)
        self.data["gate"]["findings"][0]["commits"] = []
        self.assertIn("reader 1: fixed with no commit", self.refused("evidence"))

    def test_an_open_gate_finding_cannot_be_rendered_complete(self):
        self.data["complete"] = True
        for outcome in review_report.UNSETTLED:
            with self.subTest(outcome=outcome):
                self.data["gate"]["findings"][1]["outcome"] = outcome
                self.assertIn("the gate's pages B1", self.refused("complete"))
        self.data["gate"]["findings"][1]["outcome"] = "known limit"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Result: complete:", self.out.read_text(encoding="utf-8"))

    def test_the_result_counts_the_gates_findings_not_settled(self):
        self.data["gate"]["findings"][0]["outcome"] = "open"
        self.data["gate"]["findings"][1]["outcome"] = "open"
        self.data["findings"][4]["outcome"] = "open"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Result: not complete. Waiting on no one: r12-2, open. Of the acceptance "
                      "gate's findings, 2 open.\n", text)
        self.assertIn("- The gate's pages B1 (BLOCK, open): a stale claim.\n", text)
        self.assertIn("| reader | 1 | FIX | too long | open | Commit 1234567. Fault "
                      "`parser-frame-skipped`. |", text)
        self.data["gate"]["findings"][0].update(commits=[], faults=[])
        self.assertEqual(self.run_step("write")[0], 0)
        self.assertIn("| reader | 1 | FIX | too long | open | None yet. |",
                      self.out.read_text(encoding="utf-8"))

    def test_the_gates_evidence_not_in_the_tree_is_refused(self):
        for key, value, code_word in (("faults", ["parser-frame-not-skipped"], "fault"),
                                      ("tests", ["Reader.CountsNoFrame"], "test"),
                                      ("notes", ["A section that is not there"], "notes")):
            with self.subTest(code=code_word):
                self.data = copy.deepcopy(DATA)
                self.data["gate"]["findings"][0][key] = value
                self.assertEqual(self.run_step("write")[0], 0)
                code, output = self.run_step("check")
                self.assertEqual(code, 1, output)
                self.assertIn(f"review_report: FAIL {code_word} reader 1: ", output)

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
