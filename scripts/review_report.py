#!/usr/bin/env python3
"""The final review's record: results/final_review.md, written from scripts/final_review.json.

    review_report.py write [--root DIR]
    review_report.py check [--root DIR]

The data file holds every reviewer, what it read and the commits it read, the counts of its
findings by severity as its own findings said them, one look at a time, and its last line where
it ended on one; every finding, with its severity, the finding in one line, its outcome and the
evidence for it: the commits that settled it, the tests and faults that hold it, and the
docs/NOTES.md sections that give it; the outcomes checked again at a later commit, and where;
what is still to come; the acceptance gate (`gate`): the commit it checked, its checkers, what
each read and the counts of its findings by severity as its own report states them (`count`),
each checker's findings by the checker's own label, held to the same rules as a reviewer's, and
any finding of a checker that is not listed (`not_listed`), each with its severity and the reason
in words; and whether the record is complete. A checker's counts and its findings not listed are
typed from its report, which is kept outside the repository, so nothing here can hold them to it:
`test_review_report.py`'s Committed tests pin each checker's counts and its findings not listed
(r18-27).

A reviewer here is a subagent session that did not write the code it read, given the code and
not the author's conclusions. A finding's severity is BLOCK (it would block a merge), FIX (should
be fixed) or NOTE. Its outcome is one of OUTCOMES. `write` refuses, and writes nothing, on:

- severity: a severity not in SEVERITIES;
- outcome: an outcome not in OUTCOMES;
- id: a finding id that is not `<reviewer>-<number>` of a listed reviewer, a gate finding of no
  listed checker or with a blank label, a blank checker, or any of them given twice;
- count: a reviewer whose findings, counted by severity, are not the counts its looks state, or a
  checker whose findings listed and not listed, counted by severity, are not its report's;
- evidence: a finding fixed with no commit, unless its `no_commit` says in words why there is
  none (at least REASON_WORDS words of two letters or more), a known limit with no
  docs/NOTES.md section for its reason, no change needed with no reason in its detail (at least
  REASON_WORDS words), a test that names a file which is not a test (a file is a test only when
  it ends in one of TEST_ENDINGS and lies under one of TEST_DIRS, or its name starts `test_` and
  it lies in one of TEST_PLACES or is named with no folder), or a finding not listed with no
  reason in words;
- complete: a record said to be complete while a finding, the gate's included, is open, partly
  done or left for the owner, while a reviewer is still to come, or while a reviewer's fixes are
  on a branch not merged yet;
- data: anything else the file should have and does not, or a `waits_on` or `waits_for` on a
  finding that is settled.

The Result line says how many of the gate's findings are not settled, by outcome, each of them
named under "Not settled", and how many reviews are still to come; then it names every
reviewer's finding not settled, in one group for each one it waits on (its `waits_on`, or the
owner for one left for the owner), each group starting "waiting on" and whom, or "waiting on no
one", and ending with a full stop, with what waits (its `waits_for`) where given. The counts come
first because RESULTS.md's row carries the line, and the number gate takes a finding's id before
a count there for the count's row label. Whether it says complete or not, it then says how many
findings of each checker are not listed, of what severity, and why. A finding not listed is
outside the record: it does not keep the record from being complete, and the page says so where
it says complete.

The file gives the commit the outcomes were checked at, and any checked again later with the
commit they were checked at, the reviewers, the counts by reviewer, by severity and by outcome
with their totals, every finding, the acceptance gate's checkers and findings with their counts,
what is still open, what is still to come, and what no reviewer read (`unread`); it says
"complete" only when the data does and `write` lets it.

`check` reads the data file again and refuses as `write` does, then refuses:

- stale: results/final_review.md is not what `write` makes of the data file now;
- fault: a fault named is not in scripts/faults/*.json;
- test: a test named is in no file under tests/, scripts/, fuzz/, bench/ or tools/, or a test
  file named with its folder is not at that path;
- notes: a docs/NOTES.md section named is not a heading there;
- page: a count that WRITEUP.md's paragraph on the final review gives is not the record's own
  total, a total is not given there while the paragraph gives another, or the paragraph gives an
  integer that is none of them; a paragraph that gives no count at all is not refused;
- commit: a commit named, or one an outcome was checked again at, is not in the history,
  checked only where git has the full history (not in a shallow clone, and not in a tree
  copied without .git, which it says).

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
# A no_commit must say why a fixed finding has no commit. A reason names what was done and why
# that needs no commit ("the fix was to a file kept outside the repository"): it cannot be said
# in fewer than three words, while a mark, a letter or a lone word ("x", "n/a", "none") can.
REASON_WORD = re.compile(r"[A-Za-z]{2,}")
REASON_WORDS = 3
TEST_PLACES = ("tests", "scripts", "fuzz", "bench", "tools")
# A test is named by its own name or as a file. Its own name is words joined by dots, perhaps
# after one capitalised word and a slash ("Reader.CountsEveryFrame", "test_a_page_line",
# "Built/KernelTest.Case"), the last of them a test's: one that starts "test" or a capital. Any
# other entry names a file ("ci.yml", "tests/test_book.cpp"). A file is a test only when it ends
# in one of TEST_ENDINGS and lies under one of TEST_DIRS, or its name starts test_ and it lies in
# one of TEST_PLACES or is given with no folder; a source, script, workflow or results file is
# not one, though a test may read it.
TEST_DIRS = ("tests/", "fuzz/")
TEST_ENDINGS = (".py", ".cpp", ".sh")
TEST_NAME = re.compile(r"(?:[A-Z]\w*/)?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
TEST_OF_ITS_OWN = re.compile(r"test|[A-Z]")
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
    "above, and two later reviewers checked them again: r15 a sample of them, and r16 every "
    "outcome of r15's findings.")
GATE_WORDS = (
    "The acceptance gate checked the public tree at {at} in {parts} parts, each with its own "
    "report, which states how many findings of each severity it made. Those findings are "
    "recorded here beside the reviewers', each checker's held to its report's counts, and a "
    "finding not listed is counted below the table, with its severity and the reason. Each "
    "finding keeps its checker's label or, where a checker gave none, its number in the order of "
    "that checker's summary. The severity is the checker's own, BLOCK, FIX or NOTE, and the "
    "outcomes are the six above, held to the same rules as a reviewer's finding: the record is "
    "not complete while one of these is open, partly done or left for the owner.")
NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


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


def gate_id(finding):
    """A gate finding's name, its checker and its label: "pages B1"."""
    checker = need(finding, "checker", str, "a finding of the gate")
    label = need(finding, "label", str, f"a finding of {checker} at the gate")
    if not label.strip() or label != label.strip() or "|" in label or "\n" in label or "|" in checker:
        raise Refused("id", f"{checker} {label}".strip(), "a gate finding with no label of its own")
    return f"{checker} {label}"


def severity_count(record, where):
    """A count of each of SEVERITIES, held to its form."""
    if not isinstance(record, dict) or set(record) != set(SEVERITIES) or not all(
            isinstance(record[s], int) and not isinstance(record[s], bool) and record[s] >= 0
            for s in SEVERITIES):
        raise Refused("data", where, f"its count is not a count of each of {SEVERITIES}")
    return dict(record)


def gate_checkers(data):
    """The gate's header held to its rules: for each checker's id, the counts its report states
    and the counts of its findings not listed, by severity."""
    gate = need(data, "gate", dict, str(DATA))
    if not COMMIT.match(need(gate, "at", str, "gate")):
        raise Refused("data", "gate", f"{gate['at']!r} is not a commit")
    need(gate, "findings", list, "gate")
    checkers = {}
    for checker in need(gate, "checkers", list, "gate"):
        cid = need(checker, "id", str, "a checker of the gate")
        read = need(checker, "read", str, cid)
        if not cid.strip() or cid != cid.strip() or " " in cid:
            raise Refused("id", repr(cid), "a checker with no id of its own")
        if not read or "|" in cid + read or "\n" in cid + read:
            raise Refused("data", "gate", f"checker {cid!r} does not say in one line what it read")
        if cid in checkers:
            raise Refused("id", cid, "a checker given twice")
        stated = severity_count(checker.get("count"), cid)
        unlisted = dict.fromkeys(SEVERITIES, 0)
        for item in need(checker, "not_listed", list, cid):
            severity = need(item, "severity", str, f"{cid}'s finding not listed")
            reason = need(item, "reason", str, f"{cid}'s finding not listed")
            if severity not in SEVERITIES:
                raise Refused("severity", cid, f"a finding not listed is {severity!r}, not one of "
                              f"{SEVERITIES}")
            if "|" in reason or "\n" in reason or len(REASON_WORD.findall(reason)) < REASON_WORDS:
                raise Refused("evidence", cid, f"a finding not listed gives {reason!r} as its "
                              f"reason: a reason is one line of at least {REASON_WORDS} words")
            unlisted[severity] += 1
        checkers[cid] = (stated, unlisted)
    if not checkers:
        raise Refused("data", "gate", "no checker")
    return checkers


def names_a_file(test):
    """Whether a test entry names a file rather than a test's own name (TEST_NAME)."""
    if not TEST_NAME.fullmatch(test):
        return True
    last = re.split(r"[./]", test)[-1]
    return "." in test and not TEST_OF_ITS_OWN.match(last)


def is_test_name(test):
    """A test's own name, or a file that is a test: under TEST_DIRS, or named test_ in one of
    TEST_PLACES or with no folder, with one of TEST_ENDINGS either way."""
    if not names_a_file(test):
        return True
    parts = Path(test).parts
    if not test.endswith(TEST_ENDINGS) or test.startswith("/") or ".." in parts:
        return False
    if test.startswith(TEST_DIRS):
        return True
    return parts[-1].startswith("test_") and (len(parts) == 1 or (len(parts) == 2
                                                                   and parts[0] in TEST_PLACES))


def validate(data):
    """The data, held to every rule `write` has; Refused on the first broken."""
    reviewers = need(data, "reviewers", list, str(DATA))
    findings = need(data, "findings", list, str(DATA))
    checkers = gate_checkers(data)
    at_gate = data["gate"]["findings"]
    need(data, "checked_at", str, str(DATA))
    need(data, "to_come", list, str(DATA))
    for item in need(data, "unread", list, str(DATA)):
        if not isinstance(item, str) or not item or "\n" in item:
            raise Refused("data", "unread", f"{item!r} is not one line saying what no reviewer read")
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
    by_checker = {cid: dict(unlisted) for cid, (_, unlisted) in checkers.items()}
    for finding, of_gate in [(f, False) for f in findings] + [(f, True) for f in at_gate]:
        fid = gate_id(finding) if of_gate else need(finding, "id", str, "a finding")
        match = None if of_gate else FINDING_ID.match(fid)
        if of_gate and finding["checker"] not in checkers:
            raise Refused("id", fid, "not a finding of a listed checker of the gate")
        if not of_gate and (match is None or match[1] not in known):
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
        for test in finding["tests"]:
            if not isinstance(test, str) or not is_test_name(test):
                raise Refused("evidence", fid, f"{test!r} is given as a test and is a file that is "
                              f"not one: a test file is named test_* or lies under "
                              f"{' or '.join(TEST_DIRS)}")
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
        if no_commit and len(REASON_WORD.findall(no_commit)) < REASON_WORDS:
            raise Refused("evidence", fid, f"its no_commit {no_commit!r} is not a reason: a reason "
                          f"is at least {REASON_WORDS} words")
        if finding["outcome"] == "fixed" and not finding["commits"] and not no_commit:
            raise Refused("evidence", fid, "fixed with no commit, and no words on why there is none")
        if finding["outcome"] == "known limit" and not finding["notes"]:
            raise Refused("evidence", fid, "a known limit with no docs/NOTES.md section for its "
                          "reason")
        if (finding["outcome"] == "no change needed"
                and len(REASON_WORD.findall(finding["detail"])) < REASON_WORDS):
            raise Refused("evidence", fid, "no change needed with no reason: its detail must say "
                          f"why, in at least {REASON_WORDS} words (r18-28)")
        if match:
            counted[match[1]][finding["severity"]] += 1
        if of_gate and finding["checker"] in by_checker:
            by_checker[finding["checker"]][finding["severity"]] += 1
    for rid, stated in known.items():
        if counted[rid] != stated:
            raise Refused("count", rid, f"its looks state {stated}, its findings here count "
                          f"{counted[rid]}")
    for cid, (stated, _) in checkers.items():
        if by_checker[cid] != stated:
            raise Refused("count", cid, f"its report states {stated}, its findings listed and not "
                          f"listed here count {by_checker[cid]}")
    if complete:
        unsettled = [f["id"] for f in findings if f["outcome"] in UNSETTLED]
        if unsettled:
            raise Refused("complete", str(DATA), f"said complete while {', '.join(unsettled)} "
                          "is open, partly done or left for the owner")
        unsettled_at_gate = [gate_id(f) for f in at_gate if f["outcome"] in UNSETTLED]
        if unsettled_at_gate:
            raise Refused("complete", str(DATA), f"said complete while the gate's "
                          f"{', '.join(unsettled_at_gate)} is open, partly done or left for the "
                          "owner")
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
    if parts:
        return " ".join(parts)
    return "None yet." if finding["outcome"] == "open" else "None needed."


def waiter(finding):
    """Whom an unsettled finding waits on: its waits_on, or the owner where it is left for the
    owner; "" where the data names no one."""
    return finding.get("waits_on", "") or ("the owner" if finding["outcome"] == "left for the owner"
                                             else "")


def and_list(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def not_listed_words(data):
    """For each checker with findings not listed, one sentence: how many, of what severity, why."""
    said = []
    for checker in data["gate"]["checkers"]:
        reasons = {}
        for item in checker["not_listed"]:
            reasons.setdefault(item["reason"], []).append(item["severity"])
        for reason, severities in reasons.items():
            count = len(severities)
            many = NUMBER_WORDS[count] if count < len(NUMBER_WORDS) else str(count)
            kinds = and_list([f"{severities.count(s)} {s}" if count > 1 else f"a {s}"
                              for s in SEVERITIES if s in severities])
            said.append(f"{many[0].upper()}{many[1:]} finding{'s' if count > 1 else ''} of the "
                        f"gate's {checker['id']}, {kinds}, {'are' if count > 1 else 'is'} not "
                        f"listed here, because {reason}.")
    return said


def status(data):
    """The Result line: complete, or what is not settled: how many of the gate's findings, by
    outcome, and how many reviews are to come, then one group for each one a reviewer's finding
    waits on, each group starting with whom it waits on ("waiting on no one" where the data names
    no one), then any branch not merged. Groups end with a full stop; the parts inside
    a group are joined with "; ". Either way it ends with the gate's findings not listed."""
    unlisted = "".join(f" {sentence}" for sentence in not_listed_words(data))
    if data["complete"]:
        return ("complete: every finding listed is settled, every reviewer reported."
                + unlisted if unlisted else "complete: every finding settled, every reviewer "
                "reported")
    # The counts come first, so that on RESULTS.md's row, where the number gate reads the line as
    # one clause, no finding's id stands before a count as if it were the count's row label.
    said = []
    at_gate = [sum(1 for f in data["gate"]["findings"] if f["outcome"] == outcome)
               for outcome in UNSETTLED]
    if any(at_gate):
        said.append("of the acceptance gate's findings, " + ", ".join(
            f"{count} {outcome}" for outcome, count in zip(UNSETTLED, at_gate) if count))
    if data["to_come"]:
        count = len(data["to_come"])
        said.append(f"{count} {'review' if count == 1 else 'reviews'} still to come")
    groups = {}
    for finding in data["findings"]:
        if finding["outcome"] in UNSETTLED:
            groups.setdefault(waiter(finding), {}).setdefault(finding["outcome"], []).append(finding)
    for who in sorted(groups, key=lambda name: (name == "", name)):
        by_outcome = groups[who]
        parts = []
        for outcome in UNSETTLED:
            items = [f["id"] + (f" ({f['waits_for']})" if f.get("waits_for") else "")
                     for f in by_outcome.get(outcome, [])]
            if items:
                parts.append(f"{and_list(items)}, {outcome}")
        said.append(f"waiting on {who or 'no one'}: " + "; ".join(parts))
    branches = [r["id"] for r in data["reviewers"] if r["branch"]]
    if branches:
        said.append(f"{', '.join(branches)}'s fixes not merged yet")
    said = [part[0].upper() + part[1:] if part.startswith(("waiting", "of the")) else part
            for part in said]
    return ("not complete" + "".join(f". {part}" for part in said)
            + ("." if said or unlisted else "") + unlisted)


def render(data):
    validate(data)
    reviewers, findings = data["reviewers"], data["findings"]
    gate = data["gate"]
    at_gate = gate["findings"]
    made = {s: sum(c["count"][s] for c in gate["checkers"]) for s in SEVERITIES}
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
        f"- Looked again at the fixes of their own findings: {len(again)} of {len(reviewers)} reviewers"
        + (f" ({', '.join(again)})" if again else "")
        + "; every other outcome was checked against the tree by the record's author",
        "- Severities: " + ", ".join(f"{sum(1 for f in findings if f['severity'] == s)} {s}"
                                     for s in SEVERITIES),
        "- Outcomes: " + ", ".join(f"{count} {outcome}" for outcome, count
                                     in zip(OUTCOMES, total) if count),
        f"- Acceptance gate: at {gate['at']}, {len(gate['checkers'])} checkers, "
        f"{sum(made.values())} findings made by its checkers ("
        + ", ".join(f"{made[s]} {s}" for s in SEVERITIES)
        + f"), {len(at_gate)} listed ("
        + ", ".join(f"{sum(1 for f in at_gate if f['severity'] == s)} {s}" for s in SEVERITIES)
        + "); of those listed, " + ", ".join(f"{count} {outcome}" for outcome, count
                                              in zip(OUTCOMES, tally(at_gate)) if count),
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
    lines += ["", "## The acceptance gate", "",
              GATE_WORDS.format(at=gate["at"], parts=len(gate["checkers"])), ""]
    rows = []
    for checker in gate["checkers"]:
        found = [f for f in at_gate if f["checker"] == checker["id"]]
        rows.append((checker["id"], checker["read"],
                     *(sum(1 for f in found if f["severity"] == s) for s in SEVERITIES),
                     len(found), len(checker["not_listed"]), sum(checker["count"].values())))
    rows.append(("All", "", *(sum(1 for f in at_gate if f["severity"] == s) for s in SEVERITIES),
                 len(at_gate), sum(len(c["not_listed"]) for c in gate["checkers"]),
                 sum(made.values())))
    lines += table(("Checker", "What it read", *SEVERITIES, "Listed", "Not listed",
                    "Made, by its report"), rows)
    lines += ["", "The severities are those of the findings listed. "
              + (" ".join(not_listed_words(data)) or "Every finding the checkers made is listed.")]
    lines += ["", "By checker and outcome.", ""]
    rows = [(checker["id"], *tally([f for f in at_gate if f["checker"] == checker["id"]]))
            for checker in gate["checkers"]]
    rows.append(("All", *tally(at_gate)))
    lines += table(("Checker", *OUTCOMES, "Findings"), rows)
    lines += ["", "Every finding of the gate.", ""]
    lines += table(("Checker", "Label", "Severity", "The finding", "Outcome", "Evidence"),
                   [(f["checker"], f["label"], f["severity"], f["finding"], f["outcome"],
                     evidence(f)) for f in at_gate])
    lines += ["", "## Not settled", ""]
    unsettled = [f for f in findings if f["outcome"] in UNSETTLED]
    lines += [f"- {f['id']} ({f['severity']}, {f['outcome']}): {f['finding']}. {f['detail']}".rstrip()
              for f in unsettled]
    unsettled_at_gate = [f for f in at_gate if f["outcome"] in UNSETTLED]
    lines += [f"- The gate's {gate_id(f)} ({f['severity']}, {f['outcome']}): {f['finding']}. "
              f"{f['detail']}".rstrip() for f in unsettled_at_gate]
    if not unsettled and not unsettled_at_gate:
        lines.append("Every finding is settled.")
    lines += ["", "## Still to come", ""]
    lines += [f"- STILL TO COME: {item}" for item in data["to_come"]]
    lines += [f"- STILL TO COME: {r['id']}'s fixes are on {r['branch']}, not merged yet; its "
              "evidence is checked in this tree once they are." for r in reviewers if r["branch"]]
    if not data["to_come"] and not any(r["branch"] for r in reviewers):
        lines.append("Nothing.")
    lines += ["", "## Read by no reviewer", ""]
    lines += [f"- {item}" for item in data["unread"]] or ["Nothing: every change was read by a "
                                                          "reviewer who did not write it."]
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
    every = [(f, f["id"], reviewer_of(f) in unmerged) for f in data["findings"]]
    every += [(f, gate_id(f), False) for f in data["gate"]["findings"]]
    for finding, fid, on_a_branch in every:
        if on_a_branch:
            continue
        for fault in finding["faults"]:
            if fault not in faults:
                problems.append(("fault", fid, f"{fault} is not in scripts/faults"))
        for test in finding["tests"]:
            if names_a_file(test) and "/" in test:
                found = (root / test).is_file()
            elif names_a_file(test):
                found = any(path.name == test for place in TEST_PLACES
                            for path in (root / place).rglob(test))
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


PAGE = Path("WRITEUP.md")
PAGE_HEADING = "### The final review"
# Each count WRITEUP's paragraph on the final review gives, in the words it gives it, with the
# record's total it must equal. gate_numbers.py holds a figure to any place of the record that
# shares a word with it, and the record holds the same small numbers in many rows; these are
# its headline counts, so they are held here to the totals themselves (r16-8).
PAGE_COUNTS = (
    (r"There were (\d+) reviewers and (\d+) findings: (\d+) BLOCK, (\d+) FIX and (\d+) NOTE",
     lambda totals: (totals["reviewers"], totals["findings"], *totals["severities"]), True),
    (r"looked again at the fixes were (\d+) of (\d+)",
     lambda totals: (totals["again"], totals["reviewers"]), True),
    (r"(\d+) (?:is|are) fixed", lambda totals: (totals["fixed"],), False),
    (r"(\d+) (?:is|are) a known limit", lambda totals: (totals["known limit"],), False),
    (r"(\d+) needed no change", lambda totals: (totals["no change needed"],), False),
    (r"(\d+) (?:findings? )?(?:is|are) partly done", lambda totals: (totals["partly"],), False),
    (r"(\d+) (?:is|are) left for the owner", lambda totals: (totals["left for the owner"],), False),
    (r"(\d+) (?:findings? )?(?:is|are) open", lambda totals: (totals["open"],), False),
)
PAGE_INTEGER = re.compile(r"(?<![\w.,/-])\d[\d,]*(?![\w/-])")


def page_problems(root, data):
    """(code, where, detail) for each count of WRITEUP's paragraph on the final review that is
    not the record's own total, each total the paragraph does not give, and each integer in it
    that is none of these counts. Nothing when the tree has no WRITEUP.md."""
    path = root / PAGE
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    start = text.find(f"\n{PAGE_HEADING}\n")
    if start < 0:
        return [("page", PAGE.as_posix(), f"no {PAGE_HEADING!r} section")]
    body = text[start + len(PAGE_HEADING) + 2:]
    end = body.find("\n#")
    paragraph = body if end < 0 else body[:end]
    reviewers, findings = data["reviewers"], data["findings"]
    totals = dict(zip(OUTCOMES, tally(findings)))
    totals.update(reviewers=len(reviewers), findings=len(findings),
                  severities=[sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES],
                  again=sum(1 for r in reviewers if len(r["looks"]) > 1))
    problems, rest = [], paragraph
    # A paragraph may give the counts or none of them: one that gives any must give them all,
    # and one that gives none must give no integer either (below).
    counted = any(re.search(pattern, paragraph) for pattern, _, _ in PAGE_COUNTS)
    for pattern, wanted, always in PAGE_COUNTS:
        found = list(re.finditer(pattern, paragraph))
        want = tuple(wanted(totals))
        if len(found) > 1:
            problems.append(("page", PAGE.as_posix(), f"{pattern!r} is said {len(found)} times"))
        elif not found and counted and (always or any(want)):
            problems.append(("page", PAGE.as_posix(), f"does not give {want} as {pattern!r}"))
        for match in found:
            given = tuple(int(group.replace(",", "")) for group in match.groups())
            if given != want:
                problems.append(("page", PAGE.as_posix(), f"gives {match[0]!r}; the record's "
                                 f"totals are {want}"))
            rest = rest.replace(match[0], " ")
    for number in PAGE_INTEGER.findall(rest):
        problems.append(("page", PAGE.as_posix(), f"gives {number}, which is not one of the "
                         "record's counts"))
    return problems


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
    problems += found + page_problems(root, data)
    for code, where, detail in problems:
        print(f"review_report: FAIL {code} {where}: {detail}")
    if problems:
        return 1
    commits = "commits checked" if whole else "commits not checked: no full git history here"
    print(f"review_report: ok, {len(data['findings'])} findings, {commits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
