#!/usr/bin/env python3
"""check_predictions.py on a real, throwaway git history (criterion 31)."""

import contextlib
import io
import os
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
        # Every commit is a minute after the last, from 08:00 on the fixture run's day, so
        # each comes before the fixture run, which began at 10:00:05.
        self.clock = 1793433600
        self.git("init", "-q")

    def git(self, *args, at=None):
        when = {} if at is None else {"GIT_AUTHOR_DATE": f"@{at} +0000",
                                       "GIT_COMMITTER_DATE": f"@{at} +0000"}
        return subprocess.run(["git", "-C", str(self.root), "-c", "user.name=fixture",
                               "-c", "user.email=fixture@example.org", "-c",
                               "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True, env={**os.environ, **when}).stdout

    def copy(self, *names):
        for name in names:
            shutil.copy(FIXTURE / name, self.results / name)

    def commit(self, *names, at=None):
        """Commits the files, at the next minute of the clock or at the time given, and
        returns the commit."""
        self.clock += 60
        self.git("add", *[f"results/{name}" for name in names])
        self.git("commit", "-q", "--allow-empty", "-m", "fixture",
                 at=self.clock if at is None else at)
        return self.git("rev-parse", "HEAD").strip()

    def result(self, *lines):
        (self.results / "bench_fixture.md").write_text(
            "# A fixture result\n\n" + "".join(f"{line}\n" for line in lines), encoding="utf-8")

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

    def test_a_book_variants_result_naming_no_prediction(self):
        self.in_order()
        for name in ("lookup_fixture.md", "prefetch_fixture.md", "dispatch_fixture.md"):
            with self.subTest(name=name):
                path = self.results / name
                path.write_text("# nothing\n", encoding="utf-8")
                self.assert_fails("prediction-missing")
                path.unlink()

    def test_an_spsc_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "spsc_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_an_alloc_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "alloc_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_wasm_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "wasm_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_simd_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "simd_fixture.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_layout_result_naming_no_prediction(self):
        self.in_order()
        for name in ("layout_fixture.md", "structure_fixture.md", "window_fixture.md",
                     "types_fixture.md"):
            with self.subTest(name=name):
                (self.results / name).write_text("# nothing\n", encoding="utf-8")
                self.assert_fails("prediction-missing")
                (self.results / name).unlink()

    def test_a_udp_result_naming_no_prediction(self):
        self.in_order()
        (self.results / "udp_bisect_debian_12302019.md").write_text("# nothing\n",
                                                                    encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_walks_ladder_naming_no_prediction(self):
        self.in_order()
        (self.results / "walks_ladder_debian_12302019.md").write_text("# nothing\n",
                                                                      encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_a_walks_counts_file_needs_no_prediction(self):
        # walk_report.py's counts are untimed, so they are no experiment.
        self.in_order()
        (self.results / "walks_counts_12302019.md").write_text("# nothing\n", encoding="utf-8")
        self.commit("walks_counts_12302019.md")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_the_oracle_sessions_naming_no_prediction(self):
        self.in_order()
        (self.results / "oracle_sessions.md").write_text("# nothing\n", encoding="utf-8")
        self.assert_fails("prediction-missing")

    def test_the_oracle_sessions_need_no_run_row(self):
        self.in_order()
        (self.results / "oracle_sessions.md").write_text("- prediction: P-0001\n",
                                                         encoding="utf-8")
        self.commit("oracle_sessions.md")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_the_oracle_sessions_before_their_prediction(self):
        (self.results / "oracle_sessions.md").write_text("- prediction: P-0001\n",
                                                         encoding="utf-8")
        self.commit("oracle_sessions.md")
        self.in_order()
        self.assert_fails("prediction-after-result")

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

    def run_again(self, prediction_first):
        """bench_fixture.md run again under P-0002, its prediction committed first or after."""
        self.in_order()
        predictions = self.results / "predictions.md"
        fixture = self.results / "bench_fixture.md"
        log = self.results / "run_log.md"

        def predict():
            predictions.write_text(predictions.read_text(encoding="utf-8") +
                                   "\n## P-0002: again\n\n- Prediction: it runs again.\n",
                                   encoding="utf-8")
            self.commit("predictions.md")
        if prediction_first:
            predict()
        fixture.write_text("# A fixture result\n\n- prediction: P-0002\n"
                           "- run_id: P-0002-20261101T100005Z\n", encoding="utf-8")
        log.write_text(log.read_text(encoding="utf-8") +
                       "| P-0002-20261101T100005Z | P-0002 | abc (clean) | Darwin | published |\n",
                       encoding="utf-8")
        self.commit("bench_fixture.md", "run_log.md")
        if not prediction_first:
            predict()

    def test_a_file_run_again_under_a_later_prediction(self):
        self.run_again(prediction_first=True)
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_file_run_again_before_its_prediction(self):
        self.run_again(prediction_first=False)
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

    def test_a_prediction_after_the_tree_its_run_measured(self):
        # r7-4: the run measured C0 and was logged; the prediction came after it, and the
        # result after that. The file was still first committed after the prediction.
        measured = self.commit()
        self.copy("run_log.md")
        self.commit("run_log.md")
        self.copy("predictions.md")
        self.commit("predictions.md")
        self.result("- commit: " + measured[:12] + " (clean)", "- prediction: P-0001",
                    "- run_id: P-0001-20261101T000000Z")
        self.commit("bench_fixture.md")
        code, output = check(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn(f"FAIL prediction-after-run results/bench_fixture.md: ", output)
        self.assertIn(f"is not in the tree the run measured, {measured[:12]}", output)

    def test_a_prediction_in_the_tree_its_run_measured(self):
        self.copy("predictions.md")
        self.commit("predictions.md")
        measured = self.commit()
        self.copy("run_log.md")
        self.result("- commit: " + measured[:12] + " (exported by debian_sync.sh)",
                    "- prediction: P-0001", "- run_id: P-0001-20261031T100005Z")
        self.commit("run_log.md", "bench_fixture.md")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_measured_commit_the_history_does_not_hold(self):
        self.in_order()
        self.result("- commit: 0123456789ab (clean)", "- prediction: P-0001",
                    "- run_id: P-0001-20261031T100005Z")
        self.assert_fails("measured-commit-unknown")

    def test_a_prediction_committed_after_its_run_began(self):
        began = 1793440805  # 2026-10-31T10:00:05Z, the fixture run's start.
        self.copy("predictions.md")
        self.commit("predictions.md", at=began + 1)
        self.copy("run_log.md", "bench_fixture.md")
        self.commit("run_log.md", "bench_fixture.md", at=began + 60)
        code, output = check(self.root)
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL prediction-after-run results/bench_fixture.md: ", output)
        self.assertIn("not before the run began, 20261031T100005Z", output)

    def test_with_no_measured_commit_the_same_second_does_not_order_them(self):
        began = 1793440805
        self.copy("predictions.md")
        self.commit("predictions.md", at=began)
        self.copy("run_log.md", "bench_fixture.md")
        self.commit("run_log.md", "bench_fixture.md", at=began + 60)
        self.assert_fails("prediction-after-run")

    def test_with_the_measured_commit_its_ancestry_orders_them(self):
        began = 1793440805
        self.copy("predictions.md")
        measured = self.commit("predictions.md", at=began)
        self.copy("run_log.md")
        self.result("- commit: " + measured[:12] + " (clean)", "- prediction: P-0001",
                    "- run_id: P-0001-20261031T100005Z")
        self.commit("run_log.md", "bench_fixture.md", at=began + 60)
        code, output = check(self.root)
        self.assertEqual(code, 0, output)

    def test_a_run_id_the_log_holds_only_as_words(self):
        self.in_order()
        self.result("- prediction: P-0001", "- run_id: published")
        self.assert_fails("run-missing-from-log")
        self.result("- prediction: P-0001", "- run_id: P-0001-20261031T10")
        self.assert_fails("run-missing-from-log")

    def test_a_result_that_places_its_run_nowhere(self):
        self.in_order()
        with open(self.results / "run_log.md", "a", encoding="utf-8") as log:
            log.write("| P-0001-first | P-0001 | abc | Darwin | published |\n")
        self.result("- prediction: P-0001", "- run_id: P-0001-first")
        self.assert_fails("run-unplaced")

    def test_no_git_says_the_history_was_not_checked(self):
        self.copy("predictions.md", "run_log.md", "bench_fixture.md")
        code, output = check(self.root, "--no-git")
        self.assertEqual(code, 0, output)
        self.assertIn("check_predictions: ok, history not checked: --no-git was given", output)
        shutil.rmtree(self.root / ".git")
        code, output = check(self.root)
        self.assertEqual(code, 0, output)
        self.assertIn(f"check_predictions: ok, history not checked: {self.root.resolve()} has no "
                      ".git", output)
        self.assertNotIn("check_predictions: ok\n", output)

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
