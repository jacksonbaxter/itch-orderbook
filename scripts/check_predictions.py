#!/usr/bin/env python3
"""Each experiment's prediction was committed before it ran, and no run was lost (criterion 31).

    check_predictions.py [--root DIR] [--require ID] [--no-git]

results/predictions.md holds one `## P-NNNN: title` block per experiment. Every
results/bench_*.md, chase_*.md, backtest_*.md, counters_*.md, flamegraph_*.md, mm_*.md,
lookup_*.md, prefetch_*.md, dispatch_*.md, spsc_*.md, wasm_*.md, simd_*.md, alloc_*.md,
udp_*.md and walks_ladder_*.md names one in its header, and so does
results/oracle_sessions.md, which sums other results files and is no run, so it has no row
in the run log.
With git history to read, the block must be committed, its commit must come before the
result's, and its text must be what that commit wrote: a prediction is never edited. It must
also precede the run itself: the prediction's first commit is an ancestor of (or is) the
commit the result's `- commit:` field says it measured, and was committed no later than the
start time its `run_id` carries, and before it, to the second, when the file names no measured
commit, since then the times alone give the order. A result that gives neither is refused, and so is a measured commit
the history does not hold. Each result's `run_id` is a Run cell of results/run_log.md's table,
exactly. results/run_log.md gets a row for every run, failed ones included, and only ever
grows.

--require ID checks only that one prediction is on record, before a run starts. --no-git
leaves out what needs history, for a tree that has none, such as a sync to debian; so does a
tree with no .git, and then the last line says the history was not checked rather than a
bare ok.

Each violation prints as `check_predictions: FAIL <code> <where>: <detail>`.

Exit codes: 0 passed, 1 a violation, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS = "results/predictions.md"
RUN_LOG = "results/run_log.md"
RESULT_PATTERNS = ("alloc_*.md", "bench_*.md", "chase_*.md", "backtest_*.md", "counters_*.md",
                   "flamegraph_*.md", "mm_*.md", "lookup_*.md", "prefetch_*.md", "dispatch_*.md",
                   "spsc_*.md", "wasm_*.md", "simd_*.md", "layout_*.md", "structure_*.md",
                   "window_*.md", "types_*.md")
# scripts/udp_report.py's files (T-URING-1, T-URING-2).
RESULT_PATTERNS += ("udp_*.md",)
# scripts/walk_driver.py's timed ladder. walk_report.py's counts file is untimed and names no
# prediction.
RESULT_PATTERNS += ("walks_ladder_*.md",)
# Results summed from other results files: each names its prediction, but no run.
SUMMED = ("oracle_sessions.md",)
BLOCK = re.compile(r"^## (P-\d{4}): .*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
FIELD = re.compile(r"^- (prediction|run_id): (.*)$", re.MULTILINE)
MEASURED = re.compile(r"^- [Cc]ommit: ([0-9a-f]{7,40})\b", re.MULTILINE)
# A run's start, as its id carries it: P-0015-20261105T023320Z, maybe with what a row is of.
RUN_START = re.compile(r"P-\d+-(\d{8}T\d{6}Z)(?![0-9])")


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


def first_commit_of_file(root, path, identifier):
    """The first commit in which the file names the prediction. A file run again under a later
    prediction, such as T-BT-3 on debian under P-0106, is held to that one from the commit
    that first named it."""
    history = git(root, "log", "--reverse", "--format=%H", "--", path)
    for commit in (history or "").split():
        text = git(root, "show", f"{commit}:{path}")
        if text is not None and dict(FIELD.findall(text)).get("prediction") == identifier:
            return commit
    return None


def check_run_order(root, commit, relative, measured, run_id):
    """The prediction's first commit precedes the run: it is in the tree the run measured, and
    it was committed before the run began."""
    if measured is None and RUN_START.match(run_id or "") is None:
        yield ("run-unplaced", relative, "the file gives neither the commit it measured nor a "
               "run_id with its start time, so nothing shows the prediction came first")
    if measured is not None:
        resolved = git(root, "rev-parse", "--verify", "-q", f"{measured}^{{commit}}")
        if resolved is None:
            yield "measured-commit-unknown", relative, f"{measured} is not in the history"
        elif git(root, "merge-base", "--is-ancestor", commit, resolved.strip()) is None:
            yield ("prediction-after-run", relative,
                   f"{commit[:10]} is not in the tree the run measured, {measured}")
    started = RUN_START.match(run_id or "")
    if started is not None:
        when = int(git(root, "show", "-s", "--format=%ct", commit) or "0")
        began = datetime.strptime(started[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        # Both times are whole seconds. With no measured commit the time is all there is, and
        # the prediction must be the earlier second; with one, the ancestry above shows the
        # order, and the time may share the run's second but never follow it.
        if when > began.timestamp() or (measured is None and when == began.timestamp()):
            yield ("prediction-after-run", relative,
                   f"{commit[:10]} was committed at "
                   f"{datetime.fromtimestamp(when, timezone.utc):%Y-%m-%dT%H:%M:%SZ}, not before "
                   f"the run began, {started[1]}")


def check_history(root, identifier, result_path, known, measured=None, run_id=None, run=True):
    commit, original = first_commit_of_block(root, identifier)
    if commit is None:
        yield "prediction-uncommitted", identifier, "it is not in any commit"
        return
    if original != known[identifier]:
        yield "prediction-edited", identifier, f"differs from what {commit[:10]} wrote"
    if run:
        yield from check_run_order(root, commit, result_path, measured, run_id)
    result_commit = first_commit_of_file(root, result_path, identifier)
    if result_commit is None:
        return
    if commit == result_commit or git(root, "merge-base", "--is-ancestor", commit,
                                      result_commit) is None:
        yield ("prediction-after-result", identifier,
               f"{commit[:10]} does not precede {result_commit[:10]}")


def log_at(root, commit):
    return git(root, "show", f"{commit}:{RUN_LOG}") or ""


def dropped_rows(earlier, text):
    """The rows of an earlier log that this one no longer holds, each counted as often."""
    return Counter(earlier.splitlines()) - Counter(text.splitlines())


def check_log_history(root):
    """Every committed version of the log extends its parents': a commit appends to its first
    parent's log, and a merge also keeps every row of each branch it merges."""
    history = git(root, "log", "--full-history", "--reverse", "--format=%H %P", "--", RUN_LOG)
    for line in (history or "").splitlines():
        commit, *parents = line.split()
        text = log_at(root, commit)
        if parents and not text.startswith(log_at(root, parents[0])):
            yield "run-log-rewritten", commit[:10], "an earlier row changed or went"
        for parent in parents[1:]:
            if dropped_rows(log_at(root, parent), text):
                yield "run-log-rewritten", commit[:10], f"a row of merged {parent[:10]} went"
    committed = git(root, "show", f"HEAD:{RUN_LOG}")
    current = (root / RUN_LOG).read_text(encoding="utf-8") if (root / RUN_LOG).is_file() else ""
    if committed is not None and not current.startswith(committed):
        yield "run-log-rewritten", "working tree", "an earlier row changed or went"


def logged_runs(log_text):
    """The Run cell of every row of the run log's table."""
    runs = set()
    for line in log_text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) == 5:
            runs.add(cells[0])
    return runs


def check_result(root, path, known, log_text, use_git):
    """log_text is None for a result summed from others, which names no run."""
    relative = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    fields = dict(FIELD.findall(text))
    identifier = fields.get("prediction")
    if not identifier:
        yield "prediction-missing", relative, "the file names no prediction"
        return
    if identifier not in known:
        yield "prediction-missing", relative, f"{identifier} is not in {PREDICTIONS}"
        return
    run_id = fields.get("run_id")
    if log_text is not None and (not run_id or run_id not in logged_runs(log_text)):
        yield "run-missing-from-log", relative, f"{run_id} is no Run cell of {RUN_LOG}'s table"
    if use_git:
        measured = MEASURED.search(text)
        yield from check_history(root, identifier, relative, known,
                                 measured[1] if measured else None, run_id,
                                 run=log_text is not None)


def find_violations(root, use_git):
    predictions = root / PREDICTIONS
    known = blocks(predictions.read_text(encoding="utf-8")) if predictions.is_file() else {}
    log_text = (root / RUN_LOG).read_text(encoding="utf-8") if (root / RUN_LOG).is_file() else ""
    violations = []
    for pattern in RESULT_PATTERNS:
        for path in sorted((root / "results").glob(pattern)):
            violations.extend(check_result(root, path, known, log_text, use_git))
    for name in SUMMED:
        path = root / "results" / name
        if path.is_file():
            violations.extend(check_result(root, path, known, None, use_git))
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
        print(f"check_predictions: ok {args.require}" +
              ("" if use_git else f", on record; {unchecked(root, args)}"))
        return 0

    violations = find_violations(root, use_git)
    for code, where, detail in violations:
        print(f"check_predictions: FAIL {code} {where}: {detail}")
    if violations:
        return 1
    print("check_predictions: ok" if use_git else f"check_predictions: ok, {unchecked(root, args)}")
    return 0


def unchecked(root, args):
    why = "--no-git was given" if args.no_git else f"{root} has no .git"
    return (f"history not checked: {why}, so neither when each prediction was committed against "
            "its run nor the run log's history was read")


if __name__ == "__main__":
    sys.exit(main())
