#!/usr/bin/env python3
"""Sums the oracle's results files into T-ORACLE-2's, and fails if that file differs.

    oracle_sessions.py [--root DIR] [--write | --check]

T-ORACLE-2 is 0 divergences over ten full sessions and at least 3,037,078,470 messages
compared, on the Mac. Each session's own file, results/oracle_<day>.md, was written by
scripts/oracle_report.py from that day's raw logs and holds the day to its census. This
reads those files only: a session counts once, by its dataset, with every message its file
says was diffed, and only if every arm of it passed T-ORACLE-1 on a macOS host. A day held
to less is a gap, never a smaller bar, and the file names it.

Our count is every message of the file, since the oracle diffs the books after each one.
carteret's published bar counts the messages its differential dispatches, which leaves out
the directory, system and administrative messages, so each session is a little larger
here than in its table. Both counts are shown where carteret gives one.

With neither flag it prints the file. --write puts it in results/oracle_sessions.md, and
--check fails on any difference from a fresh run.

Each violation prints as `oracle_sessions: FAIL <detail>`.

Exit codes: 0 written, printed or matching, 1 a results file cannot be read or --check
found a difference, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = "oracle_sessions.md"
SESSIONS = 10
MESSAGES = 3_037_078_470
# carteret's docs/data.md at 3df0e87, "messages compared" by session: the bar's own rows.
CARTERET = {
    "20190130.BX_ITCH_50": 74_525_587,
    "01302019.NASDAQ_ITCH50": 364_479_470,
    "03272019.NASDAQ_ITCH50": 418_387_627,
    "05302019.NASDAQ_ITCH50": 323_088_337,
    "07302019.NASDAQ_ITCH50": 278_286_223,
    "08302019.NASDAQ_ITCH50": 306_373_517,
    "S101819-v50.txt": 298_381_771,
    "12302019.NASDAQ_ITCH50": 264_496_253,
    "10302019.NASDAQ_ITCH50": 290_025_046,
    "01302020.NASDAQ_ITCH50": 419_034_639,
}
# The prediction this sum was made under, before the eight sessions past the two reference
# days were fetched: scripts/check_predictions.py holds the file to its history.
PREDICTION = "P-0501"


class SessionError(Exception):
    """A results file cannot be read as a session."""


def find(pattern, text, name):
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise SessionError(f"results/{name}: nothing matches {pattern}")
    return match


def figure(text):
    return int(text.replace(",", ""))


def session(name, text):
    """One session from its oracle results file."""
    dataset = find(r"^- Dataset: `([^`]+)`", text, name)[1]
    host = find(r"^- Host: (.*?)\. ", text, name)[1]
    commit = find(r"^- Commit: (\w+)", text, name)[1]
    section = find(r"^## T-ORACLE-1: .*?\n\nVerdict: \*\*(\w+)\*\*\n((?s:.*?))(?=^## |\Z)",
                   text, name)
    verdict, body = section[1], section[2]
    in_file = figure(find(r"^\| Messages in the file \(manifest\) \| ([\d,]+) \|$", body, name)[1])
    diffed = figure(find(r"^\| Messages diffed \| ([\d,]+) \|$", body, name)[1])
    divergences = figure(find(r"^\| Divergences \| ([\d,]+) \|$", body, name)[1])
    arms = re.findall(r"^\| `(\w+)` \| (\w+) \| .* \| ([\d,]+) \| `[0-9a-f]+` \|", text,
                      re.MULTILINE)
    if not arms:
        raise SessionError(f"results/{name}: the table of every arm has no rows")
    failed_arms = [arm for arm, arm_verdict, _ in arms if arm_verdict != "PASS"]
    short_arms = [arm for arm, _, count in arms if figure(count) != in_file]
    return {
        "name": name, "day": name[len("oracle_"):-len(".md")], "dataset": dataset,
        "host": host, "commit": commit, "verdict": verdict, "in_file": in_file,
        "diffed": diffed, "divergences": divergences, "arms": [arm for arm, _, _ in arms],
        "failed_arms": failed_arms, "short_arms": short_arms,
    }


def gaps_of(found):
    """Each reason a session does not count, by session."""
    gaps = {}
    for row in found:
        reasons = []
        if not row["host"].startswith("Darwin"):
            reasons.append(f"host {row['host']}, not macOS")
        if row["verdict"] != "PASS" or row["failed_arms"]:
            reasons.append(f"T-ORACLE-1 {row['verdict']}, arms failing: "
                           f"{', '.join(row['failed_arms']) or 'none'}")
        if row["diffed"] != row["in_file"] or row["short_arms"]:
            reasons.append(f"{row['diffed']:,} of {row['in_file']:,} messages diffed, arms "
                           f"short: {', '.join(row['short_arms']) or 'none'}")
        if row["divergences"]:
            reasons.append(f"{row['divergences']:,} divergences")
        if reasons:
            gaps[row["day"]] = reasons
    return gaps


def render(found):
    by_dataset = {}
    for row in found:
        if row["dataset"] in by_dataset:
            raise SessionError(f"results/{row['name']} and results/{by_dataset[row['dataset']]}"
                               f" both replay {row['dataset']}")
        by_dataset[row["dataset"]] = row["name"]
    gaps = gaps_of(found)
    counted = [row for row in found if row["day"] not in gaps]
    sessions = len(counted)
    messages = sum(row["diffed"] for row in counted)
    divergences = sum(row["divergences"] for row in found)
    met = sessions >= SESSIONS and messages >= MESSAGES and divergences == 0
    short = []
    if sessions < SESSIONS:
        short.append(f"{sessions} sessions of {SESSIONS}")
    if messages < MESSAGES:
        short.append(f"{messages:,} messages of {MESSAGES:,}, {MESSAGES - messages:,} short")
    if divergences:
        short.append(f"{divergences:,} divergences")
    detail = (f"{divergences:,} divergences in {messages:,} messages over {sessions} sessions"
              + (f"; the gap: {'; '.join(short)}" if short else
                 f", {messages - MESSAGES:,} messages over the bar"))
    commits = sorted({row["commit"] for row in counted})
    hosts = sorted({row["host"] for row in counted})
    lines = [
        "# T-ORACLE-2: the oracle on every session",
        "",
        "Generated by `scripts/oracle_sessions.py` from the oracle results files beside it. "
        "Do not edit by hand.",
        "",
        f"- Target: 0 divergences over {SESSIONS} full sessions and at least {MESSAGES:,} "
        "messages compared, on the Mac. BENCHMARKS.md, T-ORACLE-2, from carteret's Layer 4 "
        "table.",
        f"- Commits: {', '.join(commits) or 'none'}",
        f"- Host: {', '.join(hosts) or 'none'}. Mac numbers stay labelled macOS.",
        f"- prediction: {PREDICTION}",
        "",
        f"Verdict: **{'PASS' if met else 'FAIL'}**, {detail}.",
        "",
        "A session counts if every arm its file ran passed T-ORACLE-1 on macOS and diffed every "
        "message of the file, as the census pinned it. Our count is every message, since the "
        "oracle compares the books after each one; carteret's counts the messages its "
        "differential dispatches, so its column is a little smaller. A session not counted is "
        "listed under the gap with its reasons, and its messages are not in the total.",
        "",
        "| Session | Dataset | Messages compared | carteret's count | Divergences | "
        "T-ORACLE-1 | Arms | Counted | Commit | File |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in found:
        published = CARTERET.get(row["dataset"])
        lines.append(
            f"| {row['day']} | `{row['dataset']}` | {row['diffed']:,} | "
            f"{f'{published:,}' if published else 'not published'} | {row['divergences']:,} | "
            f"{row['verdict']} | {len(row['arms'])} | "
            f"{'no' if row['day'] in gaps else 'yes'} | `{row['commit']}` | "
            f"[results/{row['name']}]({row['name']}) |")
    lines.append(f"| All counted | {sessions} sessions | {messages:,} | | {divergences:,} | | | | "
                 "| |")
    if gaps:
        lines += ["", "## Sessions not counted", ""]
        lines += [f"- {day}: {'; '.join(reasons)}." for day, reasons in gaps.items()]
    missing = sorted(set(CARTERET) - set(by_dataset))
    if missing:
        lines += ["", "## carteret's sessions with no file here", "",
                  "Each is a gap: the archive served it to carteret, and no oracle run of it is "
                  "in results/.", ""]
        lines += [f"- `{dataset}`" for dataset in missing]
    return "\n".join(lines) + "\n"


def generate(root):
    found = []
    for path in sorted((root / "results").glob("oracle_*.md")):
        if path.name != OUT:
            found.append(session(path.name, path.read_text(encoding="utf-8")))
    if not found:
        raise SessionError("no oracle results files")
    return render(found)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        text = generate(args.root)
    except (OSError, ValueError, SessionError) as error:
        print(f"oracle_sessions: FAIL {error}")
        return 1
    out = args.root / "results" / OUT
    if args.write:
        out.write_text(text, encoding="utf-8")
        print(f"oracle_sessions: wrote results/{OUT}")
    elif args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        if current != text:
            print(f"oracle_sessions: FAIL results/{OUT} differs from a fresh run; "
                  "run scripts/oracle_sessions.py --write")
            return 1
        print(f"oracle_sessions: ok results/{OUT}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
