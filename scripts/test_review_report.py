#!/usr/bin/env python3
"""review_report.py on a made-up record of two reviewers, r1 and r12 (one id the start of the
other): the counts are the rows, an unknown outcome or severity is refused, an open finding
cannot be rendered as complete, evidence not in the tree is refused, and the committed file is a
fresh render whose evidence is in the tree; and every fault that plants a figure in the record's
sentences is still caught as the record stands. Scratch goes under build/.

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fault_matrix  # noqa: E402
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
        finding("r1-3", "NOTE", "no change needed", detail="the code was right as it is"),
        finding("r12-1", "FIX", "fixed", commits=["1234567"], tests=["test_a_page_line"]),
        finding("r12-2", "NOTE", "fixed", commits=["1234567"]),
    ],
    "gates": [{
        "name": "acceptance gate",
        "at": "abcdef1",
        "checkers": [{"id": "reader", "read": "the pages as a stranger would",
                      "count": {"BLOCK": 0, "FIX": 1, "NOTE": 0}, "not_listed": []},
                     {"id": "pages", "read": "every figure of the pages",
                      "count": {"BLOCK": 1, "FIX": 0, "NOTE": 0}, "not_listed": []}],
        "findings": [
            {"checker": "reader", "label": "1", "severity": "FIX", "finding": "too long",
             "outcome": "fixed", "detail": "", "commits": ["1234567"], "tests": [],
             "faults": ["parser-frame-skipped"], "notes": []},
            {"checker": "pages", "label": "B1", "severity": "BLOCK", "finding": "a stale claim",
             "outcome": "known limit", "detail": "", "commits": [], "tests": [], "faults": [],
             "notes": [NOTE_SECTION]},
        ],
    }],
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
        self.assertIn("- Result: not complete. 1 review still to come. Waiting on no one: r12-1, "
                      "open.\n", text)
        self.out.unlink()
        self.data["to_come"].append("a second reader")
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("not complete. 2 reviews still to come. Waiting", self.out.read_text(
            encoding="utf-8"))
        self.assertIn("- r12-1 (FIX, open): what r12-1 found.", text)
        self.assertIn("- STILL TO COME: the last reviewer reads the pages", text)

    def test_no_change_needed_needs_a_reason(self):
        # r18-28: an open finding set to no change needed, with no words, could make the record
        # complete; a reason in words is required, of a reviewer's finding and of the gate's.
        for detail in ("", "n/a", "as is"):
            with self.subTest(detail=detail):
                self.data["findings"][2]["detail"] = detail
                self.assertIn("r1-3: no change needed with no reason", self.refused("evidence"))
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][0].update(outcome="no change needed", commits=[], faults=[])
        self.assertIn("reader 1: no change needed with no reason", self.refused("evidence"))
        self.data["gates"][0]["findings"][0]["detail"] = "the page was right as it was"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)

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
        self.data["gates"][0]["findings"][0]["tests"] = ["scripts/run_record.py"]
        self.assertIn("reader 1: 'scripts/run_record.py' is given as a test",
                      self.refused("evidence"))
        self.data = copy.deepcopy(DATA)
        self.data["findings"][3]["tests"] = ["scripts/test_pages.py", "tests/test_reader.cpp",
                                             "tests/skip_probe.cpp", "fuzz/fuzz_kernels.cpp",
                                             "Reader.CountsEveryFrame", "Built/Kernel.Case",
                                             "test_pages.py", "ReviewReport.test_a_page_line"]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)

    def test_a_file_of_another_ending_or_folder_is_not_a_test(self):
        # r18-5: a name with an ending the rule does not list is a file, not a test's own name;
        # a file named test_ is a test only in a folder of tests, or given with no folder.
        for name in (".github/workflows/ci.yml", "ci.yml", "CMakePresets.json", "docs/test_x.md",
                     "results/test_x.md", "test_x.md", "test_x.json", "scripts/sub/test_x.py",
                     "../tests/test_reader.cpp", "tests/CMakeLists.txt", "Reader.counts"):
            with self.subTest(test=name):
                self.data = copy.deepcopy(DATA)
                self.data["findings"][3]["tests"] = ["test_a_page_line", name]
                self.assertIn(f"r12-1: '{name}' is given as a test", self.refused("evidence"))

    def test_a_test_file_named_with_its_folder_must_be_there(self):
        self.data["findings"][3]["tests"] = ["tests/test_pages.py"]
        self.assertEqual(self.run_step("write")[0], 0)
        code, output = self.run_step("check")
        self.assertEqual(code, 1, output)
        self.assertIn("review_report: FAIL test r12-1: tests/test_pages.py is in no file", output)
        self.data["findings"][3]["tests"] = ["scripts/test_pages.py", "test_pages.py"]
        self.assertEqual(self.run_step("write")[0], 0)
        code, output = self.run_step("check")
        self.assertEqual(code, 0, output)

    def test_the_gate_is_recorded_beside_the_reviewers(self):
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Acceptance gate: at abcdef1, 2 checkers, 2 findings made by its checkers "
                      "(1 BLOCK, 1 FIX, 0 NOTE), 2 listed (1 BLOCK, 1 FIX, 0 NOTE); of those "
                      "listed, 1 fixed, 1 known limit\n", text)
        self.assertIn("- Findings: 5\n", text)
        self.assertIn("| reader | the pages as a stranger would | 0 | 1 | 0 | 1 | 0 | 1 |\n", text)
        self.assertIn("| All |  | 1 | 1 | 0 | 2 | 0 | 2 |\n", text)
        self.assertIn("Every finding the checkers made is listed.", text)
        self.assertIn("| pages | B1 | BLOCK | a stale claim | known limit | docs/NOTES.md, ", text)
        self.assertEqual(self.counts_rows(text, "By checker and outcome.")["All"],
                         [1, 1, 0, 0, 0, 0, 2])

    def test_a_second_gate_is_recorded_after_the_first_under_its_own_name(self):
        second = copy.deepcopy(self.data["gates"][0])
        second.update(name="second gate", at="1234567", note="It read what no reviewer had.")
        second["findings"][0].update(label="FIX-1", outcome="open")
        second["findings"][1]["label"] = "BLOCK-1"
        self.data["gates"].append(second)
        self.data["gates"][0]["checkers"][0]["count"] = {"BLOCK": 1, "FIX": 1, "NOTE": 0}
        self.data["gates"][0]["checkers"][0]["not_listed"] = [
            {"severity": "BLOCK", "reason": "it is decided outside this record"}]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Acceptance gate: at abcdef1, 2 checkers, 3 findings made", text)
        self.assertIn("- Second gate: at 1234567, 2 checkers, 2 findings made", text)
        self.assertIn("Of the second gate's findings, 1 open.", text)
        self.assertIn("One finding of the acceptance gate's reader, a BLOCK, is not listed", text)
        self.assertIn("\n## The second gate\n\nThe second gate checked the public tree at 1234567 "
                      "in 2 parts, each with its own report. Its findings are recorded as the "
                      "acceptance gate's are, under the same rules.\n\nIt read what no reviewer "
                      "had.\n", text)
        self.assertIn("- The second gate's reader FIX-1 (FIX, open): too long.\n", text)
        self.assertLess(text.index("## The acceptance gate"), text.index("## The second gate"))
        # A finding's checker and label given in both gates is given twice; a gate's name, too.
        self.out.unlink()
        second["findings"][1]["label"] = "B1"
        self.assertIn("pages B1: given twice", self.refused("id"))
        second["findings"][1]["label"] = "BLOCK-1"
        second["name"] = "acceptance gate"
        self.assertIn("a gate's name given twice", self.refused("id"))
        second["name"] = "second gate"
        # A checker is held to its own gate's count, and a finding to its own gate's checkers.
        second["checkers"][1]["count"] = {"BLOCK": 2, "FIX": 0, "NOTE": 0}
        self.refused("count")
        second["checkers"][1]["count"] = {"BLOCK": 1, "FIX": 0, "NOTE": 0}
        second["checkers"][1]["id"] = "raw"
        self.assertIn("not a finding of a listed checker of the gate", self.refused("id"))
        second["checkers"][1]["id"] = "pages"
        # The second gate's open finding keeps the record from being complete (r24 FIX 2).
        self.data["complete"] = True
        self.assertIn("the second gate's reader FIX-1", self.refused("complete"))
        self.data["complete"] = False
        # The second gate's evidence is looked for in the tree, as the first gate's is.
        second["findings"][1]["faults"] = ["parser-frame-not-skipped"]
        self.assertEqual(self.run_step("write")[0], 0)
        code, output = self.run_step("check")
        self.assertEqual(code, 1, output)
        self.assertIn("review_report: FAIL fault pages BLOCK-1: parser-frame-not-skipped is not "
                      "in scripts/faults", output)

    def test_a_gate_finding_of_no_checker_or_given_twice_is_refused(self):
        self.data["gates"][0]["findings"][1]["checker"] = "code"
        self.assertIn("code B1: not a finding of a listed checker", self.refused("id"))
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][1].update(checker="reader", label="1")
        self.assertIn("reader 1: given twice", self.refused("id"))
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][1]["label"] = ""
        self.refused("id")
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][1]["outcome"] = "waived"
        self.assertIn("pages B1: 'waived'", self.refused("outcome"))
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][0]["commits"] = []
        self.assertIn("reader 1: fixed with no commit", self.refused("evidence"))

    def test_a_checkers_findings_are_held_to_its_reports_count(self):
        # r18-3: with the gate's findings dropped, the record is refused, not rendered complete.
        self.data["complete"] = True
        self.data["findings"][2]["outcome"] = "no change needed"
        self.data["gates"][0]["findings"] = []
        self.assertIn("reader: its report states {'BLOCK': 0, 'FIX': 1, 'NOTE': 0}, its findings "
                      "listed and not listed here count {'BLOCK': 0, 'FIX': 0, 'NOTE': 0}",
                      self.refused("count"))
        self.data = copy.deepcopy(DATA)
        self.data["gates"][0]["findings"][1]["severity"] = "NOTE"
        self.assertIn("pages: its report states", self.refused("count"))
        for count in (None, {"BLOCK": 1, "FIX": 0}, {"BLOCK": -1, "FIX": 0, "NOTE": 0},
                      {"BLOCK": True, "FIX": 0, "NOTE": 0}):
            with self.subTest(count=count):
                self.data = copy.deepcopy(DATA)
                self.data["gates"][0]["checkers"][1]["count"] = count
                self.assertIn("pages: its count is not a count", self.refused("data"))

    def test_a_blank_label_or_checker_is_refused(self):
        for label in ("", " ", " B1", "B1 "):
            with self.subTest(label=label):
                self.data = copy.deepcopy(DATA)
                self.data["gates"][0]["findings"][1]["label"] = label
                self.assertIn("a gate finding with no label of its own", self.refused("id"))
        for cid in ("", " ", "the pages"):
            with self.subTest(checker=cid):
                self.data = copy.deepcopy(DATA)
                self.data["gates"][0]["checkers"][1]["id"] = cid
                self.data["gates"][0]["findings"][1]["checker"] = cid
                self.assertIn("a checker with no id of its own", self.refused("id"))

    def test_a_finding_not_listed_is_counted_with_its_severity_and_reason(self):
        # r18-4: the report's count is the findings listed and those not listed, each of which
        # gives its severity and its reason; a finding left out without one is refused.
        self.data["gates"][0]["checkers"][0]["count"] = {"BLOCK": 1, "FIX": 1, "NOTE": 0}
        self.assertIn("reader: its report states {'BLOCK': 1, 'FIX': 1, 'NOTE': 0}",
                      self.refused("count"))
        reason = "it is decided outside this record"
        self.data["gates"][0]["checkers"][0]["not_listed"] = [{"severity": "BLOCK", "reason": reason}]
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        sentence = (f"One finding of the gate's reader, a BLOCK, is not listed here, because "
                    f"{reason}.")
        self.assertIn(f"- Result: not complete. {sentence}\n", text)
        self.assertIn(f"The severities are those of the findings listed. {sentence}\n", text)
        self.assertIn("2 checkers, 3 findings made by its checkers (2 BLOCK, 1 FIX, 0 NOTE), 2 "
                      "listed (1 BLOCK, 1 FIX, 0 NOTE)", text)
        self.assertIn("| reader | the pages as a stranger would | 0 | 1 | 0 | 1 | 1 | 2 |\n", text)
        self.assertIn("| All |  | 1 | 1 | 0 | 2 | 1 | 3 |\n", text)
        for item, code_word in (({"severity": "BLOCK", "reason": ""}, "evidence"),
                                ({"severity": "BLOCK", "reason": "not listed"}, "evidence"),
                                ({"severity": "BLOCK", "reason": "a | b and c d"}, "evidence"),
                                ({"severity": "MAJOR", "reason": reason}, "severity"),
                                ({"severity": "BLOCK"}, "data")):
            with self.subTest(item=item):
                self.out.unlink(missing_ok=True)
                self.data["gates"][0]["checkers"][0]["not_listed"] = [item]
                self.refused(code_word)
        self.out.unlink(missing_ok=True)
        del self.data["gates"][0]["checkers"][0]["not_listed"]
        self.refused("data")

    def test_complete_is_said_with_what_is_not_listed(self):
        reason = "it is decided outside this record"
        self.data["complete"] = True
        self.data["findings"][2]["outcome"] = "no change needed"
        self.data["gates"][0]["checkers"][0]["count"] = {"BLOCK": 2, "FIX": 1, "NOTE": 0}
        self.data["gates"][0]["checkers"][0]["not_listed"] = [{"severity": "BLOCK", "reason": reason}] * 2
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Result: complete: every finding listed is settled, every reviewer "
                      "reported. Two findings of the gate's reader, 2 BLOCK, are not listed here, "
                      f"because {reason}.\n", self.out.read_text(encoding="utf-8"))

    def test_an_open_gate_finding_cannot_be_rendered_complete(self):
        self.data["complete"] = True
        for outcome in review_report.UNSETTLED:
            with self.subTest(outcome=outcome):
                self.data["gates"][0]["findings"][1]["outcome"] = outcome
                self.assertIn("the gate's pages B1", self.refused("complete"))
        self.data["gates"][0]["findings"][1]["outcome"] = "known limit"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        self.assertIn("- Result: complete:", self.out.read_text(encoding="utf-8"))

    def test_the_result_counts_the_gates_findings_not_settled(self):
        self.data["gates"][0]["findings"][0]["outcome"] = "open"
        self.data["gates"][0]["findings"][1]["outcome"] = "open"
        self.data["findings"][4]["outcome"] = "open"
        code, output = self.run_step("write")
        self.assertEqual(code, 0, output)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("- Result: not complete. Of the acceptance gate's findings, 2 open. Waiting on "
                      "no one: r12-2, open.\n", text)
        self.assertIn("- The gate's pages B1 (BLOCK, open): a stale claim.\n", text)
        self.assertIn("| reader | 1 | FIX | too long | open | Commit 1234567. Fault "
                      "`parser-frame-skipped`. |", text)
        self.data["gates"][0]["findings"][0].update(commits=[], faults=[])
        self.assertEqual(self.run_step("write")[0], 0)
        self.assertIn("| reader | 1 | FIX | too long | open | None yet. |",
                      self.out.read_text(encoding="utf-8"))

    def test_the_gates_evidence_not_in_the_tree_is_refused(self):
        for key, value, code_word in (("faults", ["parser-frame-not-skipped"], "fault"),
                                      ("tests", ["Reader.CountsNoFrame"], "test"),
                                      ("notes", ["A section that is not there"], "notes")):
            with self.subTest(code=code_word):
                self.data = copy.deepcopy(DATA)
                self.data["gates"][0]["findings"][0][key] = value
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


# Each gate's checkers' counts and their findings not listed, as each checker's report
# states them. The reports are kept outside the repository, so the data file cannot be held to
# them by the script; they are pinned here instead (r18-27): a count edited to match a finding
# dropped, or a finding moved out of the list, fails this test.
GATE_COUNTS = {"acceptance gate": {"reader": (4, 17, 9), "pages": (2, 2, 11), "code": (0, 2, 4),
                                   "raw": (0, 0, 3), "findings": (0, 2, 3)}}
GATE_NOT_LISTED = {"acceptance gate": {"reader": ["BLOCK"], "pages": [], "code": [], "raw": [],
                                       "findings": []}}

# A fault that plants a figure in a sentence of the record and asks gate_numbers.py or
# review_report.py to refuse it goes blind when the record's own counts come to make the figure
# true: "192 findings" became the record's count of FIX findings at e2a304e, and the matrix,
# which runs only in a full check, was the first to see it (bee48dd). Each such fault is run
# here as the matrix runs it, against the record as it stands, so the commit that makes a plant
# true fails its own script tests. A plant is a fault whose command runs one of these scripts
# and which edits results/final_review.md, or a page's text that lies in WRITEUP's section on
# the final review or on a line citing results/final_review.md.
RECORD_CHECKS = ("gate_numbers.py", "review_report.py")
RECORD_FILE = review_report.OUT.as_posix()
PLANT_PAGES = ("README.md", "RESULTS.md", "WRITEUP.md", "docs/DETAILS.md")


def record_places(root):
    """(page, text) for each place of a page that speaks of the record."""
    places = []
    for page in PLANT_PAGES:
        path = root / page
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        places += [(page, line) for line in text.splitlines() if RECORD_FILE in line]
        start = text.find(f"\n{review_report.PAGE_HEADING}\n")
        if page == review_report.PAGE.as_posix() and start >= 0:
            body = text[start + 1:]
            end = body.find("\n#", len(review_report.PAGE_HEADING))
            places.append((page, body if end < 0 else body[:end]))
    return places


def record_plants(root):
    places = record_places(root)
    plants = []
    for fault in fault_matrix.load_faults(root / "scripts" / "faults"):
        if not any(word.endswith(f"/{name}") for word in fault.command for name in RECORD_CHECKS):
            continue
        edits_the_file = any(edit["file"] == RECORD_FILE for edit in fault.mutations + fault.inputs)
        edits_a_sentence = any(mutation["old"] in text for mutation in fault.mutations
                               for page, text in places if mutation["file"] == page)
        if edits_the_file or edits_a_sentence:
            plants.append(fault)
    return plants


class Committed(unittest.TestCase):
    def test_the_gates_checkers_keep_the_counts_of_their_reports(self):
        data = json.loads((REPO_ROOT / review_report.DATA).read_text(encoding="utf-8"))
        self.assertEqual({g["name"]: {c["id"]: tuple(c["count"][s] for s in review_report.SEVERITIES)
                                      for c in g["checkers"]} for g in data["gates"]}, GATE_COUNTS)
        self.assertEqual({g["name"]: {c["id"]: [n["severity"] for n in c["not_listed"]]
                                      for c in g["checkers"]} for g in data["gates"]},
                         GATE_NOT_LISTED)

    def test_the_committed_file_is_a_fresh_render_with_its_evidence_in_the_tree(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review_report.main(["check"])
        self.assertEqual(code, 0, out.getvalue())

    def test_every_figure_planted_in_the_records_sentences_is_still_caught(self):
        plants = record_plants(REPO_ROOT)
        self.assertIn("readme-writeup-review-figure-not-in-results", [f.id for f in plants])
        for fault in plants:
            self.assertIsNone(fault.build, f"{fault.id} builds, which this test does not")
            # A name the tree does not hold is left out of the copy, as when this test runs in
            # the copy of one fault area, which holds less than the repository.
            fault.tree = tuple(name for name in fault.tree if (REPO_ROOT / name).exists())
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        trees, logs = Path(scratch.name) / "trees", Path(scratch.name) / "logs"
        logs.mkdir()
        with ThreadPoolExecutor(max_workers=fault_matrix.MAX_JOBS) as pool:
            list(pool.map(lambda f: fault_matrix.run_fault(f, REPO_ROOT, trees, logs, False),
                          plants))
        missed = [f"{f.id}: {f.status}, {f.detail}" for f in plants
                  if f.status != fault_matrix.DETECTED]
        self.assertEqual(missed, [], "a planted figure is caught no longer; if the record's "
                         "own counts have made it true, plant one they cannot")


if __name__ == "__main__":
    unittest.main()
