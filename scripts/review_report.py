#!/usr/bin/env python3
"""The final review's record: results/final_review.md, written from scripts/final_review.json.

    review_report.py write [--root DIR]
    review_report.py check [--root DIR]

The data file holds every reviewer, what it read and the commits it read, the counts of its
findings by severity as its own findings said them, one look at a time, and its last line where
it ended on one; every finding, with its severity, the finding in one line, its outcome and the
evidence for it: the commits that settled it, the tests and faults that hold it, and the
docs/NOTES.md sections that give it; the outcomes checked again at a later commit, and where;
what is still to come; and whether the record is complete.

A reviewer here is a subagent session that did not write the code it read, given the code and
not the author's conclusions. A finding's severity is BLOCK (it would block a merge), FIX (should
be fixed) or NOTE. Its outcome is one of OUTCOMES. `write` refuses, and writes nothing, on:

- severity: a severity not in SEVERITIES;
- outcome: an outcome not in OUTCOMES;
- id: a finding id that is not `<reviewer>-<number>` of a listed reviewer, or is given twice;
- count: a reviewer whose findings, counted by severity, are not the counts its looks state;
- evidence: a finding fixed with no commit, unless its `no_commit` says in words why there is
  none, or a known limit with no docs/NOTES.md section for its reason;
- complete: a record said to be complete while a finding is open, partly done or left for the
  owner, while a reviewer is still to come, or while a reviewer's fixes are on a branch not
  merged yet;
- data: anything else the file should have and does not, or a `waits_on` or `waits_for` on a
  finding that is settled.

The Result line names every finding not settled, grouped by whom it waits on (its `waits_on`,
or the owner for one left for the owner) and with what waits (its `waits_for`) where given.

The file gives the commit the outcomes were checked at, and any checked again later with the
commit they were checked at, the reviewers, the counts by reviewer, by
severity and by outcome with their totals, every finding, what is still open, and what is still to
come; it says "complete" only when the data does and `write` lets it.

`check` reads the data file again and refuses as `write` does, then refuses:

- stale: results/final_review.md is not what `write` makes of the data file now;
- fault: a fault named is not in scripts/faults/*.json;
- test: a test named is in no file under tests/, scripts/, fuzz/, bench/ or tools/;
- notes: a docs/NOTES.md section named is not a heading there;
- commit: a commit named, or one an outcome was checked again at, is not in the history, checked only where git has the full history
  (not in a shallow clone, and not in a tree copied without .git, which it says).

What neither step can check is that the evidence named is the right evidence: that a commit
named fixed that finding, that a test or fault named holds it, or that a section named gives
its reason. The record's author checked that for every finding against the tree, and the last
reviewer sampled it; the page says so.

The evidence of a reviewer whose fixes are on a branch not merged yet is not looked for in this
tree; the record cannot be complete until that branch is merged.

Each refusal prints as `review_report: FAIL <code> <where>: <detail>`.

Exit codes: 0 written, or check found nothing; 1 refused; 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = Path("scripts") / "final_review.json"
OUT = Path("results") / "final_review.md"
SEVERITIES = ("BLOCK", "FIX", "NOTE")
OUTCOMES = ("fixed", "known limit", "no change needed", "left for the owner", "partly", "open")
# Outcomes that leave a finding unsettled: the record cannot be complete while one stands.
UNSETTLED = ("open", "partly", "left for the owner")
FINDING_ID = re.compile(r"^(r\d+)-(\d+)$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
TEST_PLACES = ("tests", "scripts", "fuzz", "bench", "tools")
TEXT_FIELDS = ("finding", "detail")
WHAT_A_REVIEWER_IS = (
    "A reviewer here is a subagent session that did not write the code it read. It was given the "
    "code, not the author's conclusions, and asked for anything it would block a merge for: for "
    "each problem the file and line, why it is wrong, how to show it fails, and a fix. A finding "
    "is BLOCK (the reviewer would block the merge), FIX (should be fixed, would not block) or NOTE "
    "(worth knowing). Every outcome below was checked in the tree at the commit above, not taken "
    "from the fixer's report: each commit named is in the history, each test and fault named is "
    "in the tree, and each docs/NOTES.md section named is there. `scripts/review_report.py check` "
    "checks them again each time it runs.")
OUTCOME_WORDS = (
    "Fixed: changed, with the commit that changed it, and the tests and faults that hold the "
    "fix where there are any. Known limit: left as it is, with the reason in the docs/NOTES.md "
    "section named. No change needed: checked, and the code was right or the point was taken as "
    "it stands. Left for the owner: the owner decides. Partly: part of it is still to do. Open: "
    "not done yet.")
WHAT_CHECK_CANNOT = (
    "What the script checks, and what it cannot. `scripts/review_report.py` refuses a finding "
    "fixed with no commit, unless it says in words why there is none, and a known limit with no "
    "docs/NOTES.md section; and `check` refuses any commit, test, fault or section named that is "
    "not in the tree. It cannot tell whether the evidence named is the right evidence: that the "
    "commit fixed that finding, that the test or fault holds it, or that the section gives its "
    "reason. The record's author checked that for every finding, against the tree at the commits "
    "above, and the last reviewer checked a sample of them again.")


class Refused(Exception):
    def __init__(self, code, where, detail):
        super().__init__(f"review_report: FAIL {code} {where}: {detail}")
        self.code = code


def load(root):
    path = root / DATA
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused("data", str(DATA), f"cannot be read: {error}") from None


def need(record, key, kind, where):
    value = record.get(key) if isinstance(record, dict) else None
    if not isinstance(value, kind):
        raise Refused("data", where, f"no {key} of type {kind.__name__}")
    return value


def reviewer_stated(reviewer):
    """The counts a reviewer's looks state, summed over its looks, by severity."""
    looks = need(reviewer, "looks", list, reviewer.get("id", "?"))
    if not looks:
        raise Refused("data", reviewer["id"], "no look")
    total = dict.fromkeys(SEVERITIES, 0)
    for look in looks:
        if not isinstance(look, dict) or set(look) != set(SEVERITIES) or not all(
                isinstance(look[s], int) and look[s] >= 0 for s in SEVERITIES):
            raise Refused("data", reviewer["id"], f"a look is not a count of each of {SEVERITIES}")
        for severity in SEVERITIES:
            total[severity] += look[severity]
    return total


def validate(data):
    """The data, held to every rule `write` has; Refused on the first broken."""
    reviewers = need(data, "reviewers", list, str(DATA))
    findings = need(data, "findings", list, str(DATA))
    need(data, "checked_at", str, str(DATA))
    need(data, "to_come", list, str(DATA))
    for item in need(data, "rechecked", list, str(DATA)):
        what = need(item, "what", str, "rechecked")
        if not what or "|" in what or "\n" in what:
            raise Refused("data", "rechecked", f"{what!r} does not say what was checked again")
        if not COMMIT.match(need(item, "at", str, "rechecked")):
            raise Refused("data", "rechecked", f"{item['at']!r} is not a commit")
    complete = need(data, "complete", bool, str(DATA))
    if not COMMIT.match(data["checked_at"]):
        raise Refused("data", "checked_at", f"{data['checked_at']!r} is not a commit")
    known = {}
    for reviewer in reviewers:
        rid = need(reviewer, "id", str, "a reviewer")
        for key, kind in (("read", str), ("commits", list), ("last_words", str), ("branch", str)):
            need(reviewer, key, kind, rid)
        if rid in known:
            raise Refused("id", rid, "a reviewer given twice")
        known[rid] = reviewer_stated(reviewer)
    seen = set()
    counted = {rid: dict.fromkeys(SEVERITIES, 0) for rid in known}
    for finding in findings:
        fid = need(finding, "id", str, "a finding")
        match = FINDING_ID.match(fid)
        if match is None or match[1] not in known:
            raise Refused("id", fid, "not <reviewer>-<number> of a listed reviewer")
        if fid in seen:
            raise Refused("id", fid, "given twice")
        seen.add(fid)
        for key in ("severity", "outcome", *TEXT_FIELDS):
            text = need(finding, key, str, fid)
            if "|" in text or "\n" in text:
                raise Refused("data", fid, f"its {key} holds a | or a line break")
        for key in ("commits", "tests", "faults", "notes"):
            need(finding, key, list, fid)
        if not finding["finding"]:
            raise Refused("data", fid, "the finding is empty")
        if finding["severity"] not in SEVERITIES:
            raise Refused("severity", fid, f"{finding['severity']!r} is not one of {SEVERITIES}")
        if finding["outcome"] not in OUTCOMES:
            raise Refused("outcome", fid, f"{finding['outcome']!r} is not one of {OUTCOMES}")
        for key in ("waits_on", "waits_for"):
            words = finding.get(key, "")
            if not isinstance(words, str) or "|" in words or "\n" in words or ";" in words:
                raise Refused("data", fid, f"its {key} is not one line of words")
            if words and finding["outcome"] not in UNSETTLED:
                raise Refused("data", fid, f"its {key} is given, yet it is {finding['outcome']}")
        no_commit = finding.get("no_commit", "")
        if not isinstance(no_commit, str) or "|" in no_commit or "\n" in no_commit:
            raise Refused("data", fid, "its no_commit is not one line of words")
        if finding["outcome"] == "fixed" and not finding["commits"] and not no_commit:
            raise Refused("evidence", fid, "fixed with no commit, and no words on why there is none")
        if finding["outcome"] == "known limit" and not finding["notes"]:
            raise Refused("evidence", fid, "a known limit with no docs/NOTES.md section for its "
                          "reason")
        counted[match[1]][finding["severity"]] += 1
    for rid, stated in known.items():
        if counted[rid] != stated:
            raise Refused("count", rid, f"its looks state {stated}, its findings here count "
                          f"{counted[rid]}")
    if complete:
        unsettled = [f["id"] for f in findings if f["outcome"] in UNSETTLED]
        if unsettled:
            raise Refused("complete", str(DATA), f"said complete while {', '.join(unsettled)} "
                          "is open, partly done or left for the owner")
        if data["to_come"]:
            raise Refused("complete", str(DATA), "said complete while a reviewer is to come")
        branches = [r["id"] for r in reviewers if r["branch"]]
        if branches:
            raise Refused("complete", str(DATA), f"said complete while the fixes of "
                          f"{', '.join(branches)} are on a branch not merged yet")
    return data


def table(heads, rows):
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def tally(findings):
    """For each outcome, how many; the last entry the total."""
    counts = [sum(1 for f in findings if f["outcome"] == outcome) for outcome in OUTCOMES]
    return counts + [len(findings)]


def reviewer_of(finding):
    return FINDING_ID.match(finding["id"])[1]


def code_list(items):
    return ", ".join(f"`{item}`" for item in items)


def named(word, items, text):
    return f"{word}{'s' if len(items) > 1 else ''} {text}."


def evidence(finding):
    parts = [finding["detail"]] if finding["detail"] else []
    if finding["commits"]:
        parts.append(named("Commit", finding["commits"], ", ".join(finding["commits"])))
    if finding.get("no_commit"):
        parts.append(f"No commit: {finding['no_commit']}.")
    if finding["tests"]:
        parts.append(named("Test", finding["tests"], code_list(finding["tests"])))
    if finding["faults"]:
        parts.append(named("Fault", finding["faults"], code_list(finding["faults"])))
    if finding["notes"]:
        parts.append("docs/NOTES.md, " + "; ".join(f'"{name}"' for name in finding["notes"])
                     + ".")
    return " ".join(parts) if parts else "None needed."


def waiter(finding):
    """Whom an unsettled finding waits on: its waits_on, or the owner where it is left for the
    owner; "" where the data names no one."""
    return finding.get("waits_on", "") or ("the owner" if finding["outcome"] == "left for the owner"
                                             else "")


def and_list(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def status(data):
    """The Result line: complete, or what is not settled, grouped by whom it waits on, with what
    waits where the data says it, then any reviewer to come and any branch not merged."""
    if data["complete"]:
        return "complete: every finding settled, every reviewer reported"
    groups = {}
    for finding in data["findings"]:
        if finding["outcome"] in UNSETTLED:
            groups.setdefault(waiter(finding), {}).setdefault(finding["outcome"], []).append(finding)
    said = []
    for who, by_outcome in groups.items():
        parts = []
        for outcome in UNSETTLED:
            items = [f["id"] + (f" ({f['waits_for']})" if f.get("waits_for") else "")
                     for f in by_outcome.get(outcome, [])]
            if items:
                parts.append(f"{and_list(items)}, {outcome}")
        said.append((f"waiting on {who}: " if who else "") + "; ".join(parts))
    if data["to_come"]:
        said.append(f"{len(data['to_come'])} reviewer to come")
    branches = [r["id"] for r in data["reviewers"] if r["branch"]]
    if branches:
        said.append(f"{', '.join(branches)}'s fixes not merged yet")
    return "not complete: " + "; ".join(said) if said else "not complete"


def render(data):
    validate(data)
    reviewers, findings = data["reviewers"], data["findings"]
    total = tally(findings)
    again = [r["id"] for r in reviewers if len(r["looks"]) > 1]
    lines = [
        "# The final review: every finding and what became of it",
        "",
        f"Generated by `scripts/review_report.py` from `{DATA.as_posix()}`. Do not edit by hand.",
        "",
        f"- Commit: {data['checked_at']}, where every outcome was checked"
        + ("" if not data["rechecked"] else ", but these, checked again later: "
           + "; ".join(f"{item['what']} at {item['at']}" for item in data["rechecked"]))
        + "".join(f"; {r['id']}'s on {r['branch']}, not merged yet"
                  for r in reviewers if r["branch"]),
        "- Host: none",
        f"- Reviewers: {len(reviewers)}",
        f"- Findings: {len(findings)}",
        f"- Looked again: {len(again)} of {len(reviewers)} reviewers"
        + (f" ({', '.join(again)})" if again else "")
        + "; every other outcome was checked against the tree by the record's author",
        "- Severities: " + ", ".join(f"{sum(1 for f in findings if f['severity'] == s)} {s}"
                                     for s in SEVERITIES),
        "- Outcomes: " + ", ".join(f"{count} {outcome}" for outcome, count
                                     in zip(OUTCOMES, total) if count),
        f"- Result: {status(data)}",
        "",
        "## What a reviewer is",
        "",
        WHAT_A_REVIEWER_IS,
        "",
        OUTCOME_WORDS,
        "",
        WHAT_CHECK_CANNOT,
        "",
        "## The reviewers",
        "",
        "Counts are the reviewer's own, from its findings, one look at a time where it looked "
        "again at the fixes. Last words are quoted where a reviewer ended on such a line; the "
        "others did not.",
        "",
    ]
    rows = []
    for r in reviewers:
        stated = reviewer_stated(r)
        commits = ", ".join(r["commits"]) + (", not merged yet" if r["branch"] else "")
        words = f'"{r["last_words"]}"' if r["last_words"] else "none"
        rows.append((r["id"], r["read"], commits, *(stated[s] for s in SEVERITIES),
                     sum(stated.values()), words))
    lines += table(("Reviewer", "What it read", "Commits read", *SEVERITIES, "Findings",
                    "Last words"), rows)
    lines += ["", "How many reviewers looked again at the fixes. The outcomes of the others' findings "
              "were checked against the tree by the record's author.", ""]
    lines += table(("Reviewers", "Reviewers counted", "Reviewers in all"),
                   [("Looked again at the fixes", len(again), len(reviewers)),
                    ("Read once", len(reviewers) - len(again), len(reviewers))])
    lines += ["", "## Counts", "", "By reviewer and outcome.", ""]
    rows = [(r["id"], *tally([f for f in findings if reviewer_of(f) == r["id"]]))
            for r in reviewers]
    rows.append(("All", *total))
    lines += table(("Reviewer", *OUTCOMES, "Findings"), rows)
    lines += ["", "By severity and outcome.", ""]
    rows = [(s, *tally([f for f in findings if f["severity"] == s])) for s in SEVERITIES]
    rows.append(("All", *total))
    lines += table(("Severity", *OUTCOMES, "Findings"), rows)
    lines += ["", "## Every finding", ""]
    lines += table(("Finding", "Severity", "The finding", "Outcome", "Evidence"),
                   [(f["id"], f["severity"], f["finding"], f["outcome"], evidence(f))
                    for f in findings])
    lines += ["", "## Not settled", ""]
    unsettled = [f for f in findings if f["outcome"] in UNSETTLED]
    lines += [f"- {f['id']} ({f['severity']}, {f['outcome']}): {f['finding']}. {f['detail']}".rstrip()
              for f in unsettled]
    if not unsettled:
        lines.append("Every finding is settled.")
    lines += ["", "## Still to come", ""]
    lines += [f"- STILL TO COME: {item}" for item in data["to_come"]]
    lines += [f"- STILL TO COME: {r['id']}'s fixes are on {r['branch']}, not merged yet; its "
              "evidence is checked in this tree once they are." for r in reviewers if r["branch"]]
    if not data["to_come"] and not any(r["branch"] for r in reviewers):
        lines.append("Nothing.")
    return "\n".join(lines) + "\n"


def tree_text(root):
    """The text of every file a test may be named in."""
    texts = []
    for place in TEST_PLACES:
        for path in sorted((root / place).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".cpp", ".hpp", ".txt", ".cmake", ".sh"):
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


def history(root):
    """True where git has the whole history of the tree at root."""
    if not (root / ".git").exists():
        return False
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
                          capture_output=True, text=True, check=False)
    return done.returncode == 0 and done.stdout.strip() == "false"


def in_history(root, commit):
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"],
                          capture_output=True, check=False).returncode == 0


def evidence_problems(root, data):
    """(code, where, detail) for each piece of evidence not found in the tree at root."""
    faults = set()
    for path in sorted((root / "scripts" / "faults").glob("*.json")):
        faults |= {fault["id"] for fault in json.loads(path.read_text(encoding="utf-8"))["faults"]}
    text = tree_text(root)
    notes_path = root / "docs" / "NOTES.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
    whole = history(root)
    unmerged = {r["id"] for r in data["reviewers"] if r["branch"]}
    problems = []
    if whole and not in_history(root, data["checked_at"]):
        problems.append(("commit", "checked_at", f"{data['checked_at']} is not in the history"))
    for item in data["rechecked"]:
        if whole and not in_history(root, item["at"]):
            problems.append(("commit", "rechecked", f"{item['at']} is not in the history"))
    for finding in data["findings"]:
        fid = finding["id"]
        if reviewer_of(finding) in unmerged:
            continue
        for fault in finding["faults"]:
            if fault not in faults:
                problems.append(("fault", fid, f"{fault} is not in scripts/faults"))
        for test in finding["tests"]:
            if test.endswith((".py", ".cpp")):
                found = any(path.name == Path(test).name for place in TEST_PLACES
                            for path in (root / place).rglob(Path(test).name))
            else:
                name = re.split(r"[./]", test)[-1]
                found = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None
            if not found:
                problems.append(("test", fid, f"{test} is in no file under "
                                 f"{', '.join(TEST_PLACES)}"))
        for section in finding["notes"]:
            if not re.search(rf"^## {re.escape(section)}(?:, \d{{4}}-\d{{2}}-\d{{2}})?$", notes,
                             re.MULTILINE):
                problems.append(("notes", fid, f'"{section}" is not a heading of docs/NOTES.md'))
        if whole:
            for commit in finding["commits"]:
                if not COMMIT.match(commit) or not in_history(root, commit):
                    problems.append(("commit", fid, f"{commit} is not in the history"))
    return problems, whole


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("write", "check"))
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        data = load(root)
        text = render(data)
    except Refused as refused:
        print(refused)
        return 1
    out = root / OUT
    if args.step == "write":
        out.write_text(text, encoding="utf-8")
        print(f"review_report: wrote {OUT.as_posix()}, {status(data)}")
        return 0
    problems = []
    if not out.is_file() or out.read_text(encoding="utf-8") != text:
        problems.append(("stale", OUT.as_posix(), "not what write makes of the data file now"))
    found, whole = evidence_problems(root, data)
    problems += found
    for code, where, detail in problems:
        print(f"review_report: FAIL {code} {where}: {detail}")
    if problems:
        return 1
    commits = "commits checked" if whole else "commits not checked: no full git history here"
    print(f"review_report: ok, {len(data['findings'])} findings, {commits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
