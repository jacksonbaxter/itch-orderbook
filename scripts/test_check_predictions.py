#!/usr/bin/env python3
"""check_predictions.py on a real, throwaway git history (criterion 31)."""

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_predictions  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "predictions" / "results"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)


def check(root, *extra):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = check_predictions.main(["--root", str(root), *extra])
    return code, output.getvalue()


class History(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.results = self.root / "results"
        self.results.mkdir()
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=fixture",
                        "-c", "user.email=fixture@example.org", "-c", "commit.gpgsign=false",
                        *args], check=True, capture_output=True)

    def copy(self, *names):
        for name in names:
            shutil.copy(FIXTURE / name, self.results / name)

    def commit(self, *names):
        self.git("add", *[f"results/{name}" for name in names])
        self.git("commit", "-q", "-m", "fixture")

    def in_order(self):
        self.copy("predictions.md")
        self.commit("predictions.md")
        self.copy("run_log.md", "bench_fixture.md")
        self.commit("run_log.md", "bench_fixture.md")

    def assert_fails(self, code_word):
        code, output = check(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn(f"FAIL {code_word}", output)

    def test_prediction_before_result_passes(self):
        self.in_order()
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "bench_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_market_maker_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "mm_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_result_naming_an_unknown_prediction(self):
        self.in_order()
        (self.results / "predictions.md").write_text("## P-0002: other\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_prediction_committed_with_the_result(self):
        self.copy("predictions.md", "run_log.md", "bench_fixture.md")
        self.commit("predictions.md", "run_log.md", "bench_fixture.md")
        self.assert_fails("prediction-after-result")

    def test_prediction_after_result(self):
        self.copy("run_log.md", "bench_fixture.md")
        self.commit("run_log.md", "bench_fixture.md")
        self.copy("predictions.md")
        self.commit("predictions.md")
        self.assert_fails("prediction-after-result")

    def test_prediction_never_committed(self):
        self.copy("run_log.md", "bench_fixture.md")
        self.commit("run_log.md", "bench_fixture.md")
        self.copy("predictions.md")
        self.assert_fails("prediction-uncommitted")

    def test_prediction_edited_after_the_fact(self):
        self.in_order()
        path = self.results / "predictions.md"
        path.write_text(path.read_text(encoding="utf-8").replace("it runs", "it flew"),
                        encoding="utf-8")
        self.commit("predictions.md")
        self.assert_fails("prediction-edited")

    def test_a_new_prediction_edits_nothing(self):
        self.in_order()
        path = self.results / "predictions.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## P-0002: next\n\nLater.\n",
                        encoding="utf-8")
        self.commit("predictions.md")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_run_missing_from_the_log(self):
        self.in_order()
        path = self.results / "run_log.md"
        path.write_text("# Run log\n", encoding="utf-8")
        self.assert_fails("run-missing-from-log")

    def test_a_failed_run_removed_from_the_log(self):
        self.in_order()
        path = self.results / "run_log.md"
        kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                if "refused" not in line]
        path.write_text("".join(kept), encoding="utf-8")
        self.assert_fails("run-log-rewritten")
        self.commit("run_log.md")
        self.assert_fails("run-log-rewritten")

    def test_the_log_may_grow(self):
        self.in_order()
        with open(self.results / "run_log.md", "a", encoding="utf-8") as log:
            log.write("| P-0001-later | P-0001 | abc | Darwin | refused |\n")
        self.commit("run_log.md")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def merge_two_branches(self, keep_side):
        """A row appended on a branch and another on the trunk, then the branch merged."""
        self.in_order()
        log = self.results / "run_log.md"
        base = log.read_text(encoding="utf-8")
        side = "| P-0001-side | P-0001 | abc | Darwin | published |\n"
        trunk = "| P-0001-trunk | P-0001 | abc | Linux | published |\n"
        self.git("checkout", "-q", "-b", "side")
        log.write_text(base + side, encoding="utf-8")
        self.commit("run_log.md")
        self.git("checkout", "-q", "-")
        log.write_text(base + trunk, encoding="utf-8")
        self.commit("run_log.md")
        self.git("merge", "-q", "--no-ff", "--no-commit", "-s", "ours", "side")
        log.write_text(base + trunk + (side if keep_side else ""), encoding="utf-8")
        self.commit("run_log.md")

    def test_a_merge_keeps_both_branches_rows(self):
        self.merge_two_branches(keep_side=True)
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_merge_that_drops_a_branch_row(self):
        self.merge_two_branches(keep_side=False)
        self.assert_fails("run-log-rewritten")

    def test_require_before_a_run(self):
        code, output = check(self.root, "--require", "P-0001")
        self.assertEqual(code, 1, output)
        self.copy("predictions.md")
        code, output = check(self.root, "--require", "P-0001")
        self.assertEqual(code, 1, output)
        self.assertIn("prediction-uncommitted", output)
        self.commit("predictions.md")
        code, output = check(self.root, "--require", "P-0001")
        self.assertEqual(code, 0, output)

    def test_no_git_still_needs_the_prediction_on_record(self):
        self.copy("predictions.md", "run_log.md", "bench_fixture.md")
        code, output = check(self.root, "--no-git")
        self.assertEqual(code, 0, output)
        (self.results / "predictions.md").unlink()
        code, output = check(self.root, "--no-git")
        self.assertEqual(code, 1)
        self.assertIn("prediction-missing", output)


if __name__ == "__main__":
    unittest.main()
