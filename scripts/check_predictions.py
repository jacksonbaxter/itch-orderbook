#!/usr/bin/env python3
"""Each experiment's prediction was committed before it ran, and no run was lost (criterion 31).

    check_predictions.py [--root DIR] [--require ID] [--no-git]

results/predictions.md holds one `## P-NNNN: title` block per experiment. Every
results/bench_*.md, results/chase_*.md, results/backtest_*.md, results/mm_*.md and
results/spsc_*.md names one in its header.
With git history to read, the block must be committed, its commit must come before the
result's, and its text must be what that commit wrote: a prediction is never edited. results/run_log.md gets a row for every run,
failed ones included, and only ever grows.

--require ID checks only that one prediction is on record, before a run starts. --no-git
leaves out what needs history, for a tree that has none, such as a sync to debian.

Each violation prints as `check_predictions: FAIL <code> <where>: <detail>`.

Exit codes: 0 passed, 1 a violation, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS = "results/predictions.md"
RUN_LOG = "results/run_log.md"
RESULT_PATTERNS = ("bench_*.md", "chase_*.md", "backtest_*.md", "mm_*.md", "spsc_*.md")
BLOCK = re.compile(r"^## (P-\d{4}): .*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
FIELD = re.compile(r"^- (prediction|run_id): (.*)$", re.MULTILINE)


def blocks(text):
    return {match.group(1): match.group(0).rstrip() for match in BLOCK.finditer(text)}


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                            check=False)
    return result.stdout if result.returncode == 0 else None


def first_commit_of_block(root, identifier):
    """The oldest commit whose predictions.md holds this block, and its text there."""
    history = git(root, "log", "--format=%H", "--reverse", "--", PREDICTIONS)
    for commit in (history or "").split():
        text = git(root, "show", f"{commit}:{PREDICTIONS}")
        if text is not None and identifier in blocks(text):
            return commit, blocks(text)[identifier]
    return None, None


def first_commit_of_file(root, path):
    history = git(root, "log", "--diff-filter=A", "--format=%H", "--", path)
    commits = (history or "").split()
    return commits[-1] if commits else None


def check_history(root, identifier, result_path, known):
    commit, original = first_commit_of_block(root, identifier)
    if commit is None:
        yield "prediction-uncommitted", identifier, "it is not in any commit"
        return
    if original != known[identifier]:
        yield "prediction-edited", identifier, f"differs from what {commit[:10]} wrote"
    result_commit = first_commit_of_file(root, result_path)
    if result_commit is None:
        return
    if commit == result_commit or git(root, "merge-base", "--is-ancestor", commit,
                                      result_commit) is None:
        yield ("prediction-after-result", identifier,
               f"{commit[:10]} does not precede {result_commit[:10]}")


def check_log_history(root):
    """Every committed version of the log must be a prefix of the next."""
    history = git(root, "log", "--format=%H", "--reverse", "--", RUN_LOG)
    earlier = None
    for commit in (history or "").split():
        text = git(root, "show", f"{commit}:{RUN_LOG}") or ""
        if earlier is not None and not text.startswith(earlier):
            yield "run-log-rewritten", commit[:10], "an earlier row changed or went"
        earlier = text
    current = (root / RUN_LOG).read_text(encoding="utf-8") if (root / RUN_LOG).is_file() else ""
    if earlier is not None and not current.startswith(earlier):
        yield "run-log-rewritten", "working tree", "an earlier row changed or went"


def check_result(root, path, known, log_text, use_git):
    relative = path.relative_to(root).as_posix()
    fields = dict(FIELD.findall(path.read_text(encoding="utf-8")))
    identifier = fields.get("prediction")
    if not identifier:
        yield "prediction-missing", relative, "the file names no prediction"
        return
    if identifier not in known:
        yield "prediction-missing", relative, f"{identifier} is not in {PREDICTIONS}"
        return
    run_id = fields.get("run_id")
    if not run_id or run_id not in log_text:
        yield "run-missing-from-log", relative, f"{run_id} has no row in {RUN_LOG}"
    if use_git:
        yield from check_history(root, identifier, relative, known)


def find_violations(root, use_git):
    predictions = root / PREDICTIONS
    known = blocks(predictions.read_text(encoding="utf-8")) if predictions.is_file() else {}
    log_text = (root / RUN_LOG).read_text(encoding="utf-8") if (root / RUN_LOG).is_file() else ""
    violations = []
    for pattern in RESULT_PATTERNS:
        for path in sorted((root / "results").glob(pattern)):
            violations.extend(check_result(root, path, known, log_text, use_git))
    if use_git:
        violations.extend(check_log_history(root))
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--require", metavar="ID")
    parser.add_argument("--no-git", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    use_git = not args.no_git and (root / ".git").exists()

    if args.require:
        path = root / PREDICTIONS
        known = blocks(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if args.require not in known:
            print(f"check_predictions: FAIL prediction-missing {args.require}: not on record")
            return 1
        if use_git and first_commit_of_block(root, args.require)[0] is None:
            print(f"check_predictions: FAIL prediction-uncommitted {args.require}: commit it first")
            return 1
        print(f"check_predictions: ok {args.require}")
        return 0

    violations = find_violations(root, use_git)
    for code, where, detail in violations:
        print(f"check_predictions: FAIL {code} {where}: {detail}")
    if violations:
        return 1
    print("check_predictions: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
