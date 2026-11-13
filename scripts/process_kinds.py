#!/usr/bin/env python3
"""What another process is, in anything committed: its kind, never its name.

A committed file, and a run-log row above all, gives a process that is not this project's by
one of KINDS, never by its program's name, its unit's name or a path of its package; parts of
the operating system included, so that every row reads the same way. The raw records outside
the repository keep the names. This module is the one place the kinds are written down: the
drivers, samplers and report scripts take their words from it, and the run-log-kinds gate
(scripts/gates.py) holds every row of results/run_log.md to it.

kind_of() turns a command line, as a sampler logs it, into a kind through a map from names to
kinds that is kept outside the repository and found through $ITCH_PROCESS_KINDS (a JSON file).
With no map, or a program the map does not hold, a process is "another process". The map holds

    "programs": {program name: kind}             the name with its folder stripped
    "prefixes": [[start of the command, kind]]   a folder a family of programs runs from
    "patterns": [[regular expression, kind]]     searched in the program name, then in the
                                                 command's first word (a kernel thread's
                                                 name may hold a slash)

tried in that order. A map whose kinds are not all in KINDS is refused. A map that is set and
cannot be used stops the tool that reads it (check_map, default_map), before it writes a row, and
no message gives the map's path.

cause_breaks() is the run log's grammar, and it is closed. Every CPU share in a row's outcome
must sit in a known form: a process form, whose subject is one of KINDS, with its share and,
where a tool wrote it ("saw X at 41.2% CPU at <time>", "the first, X, at 100.0% at <time>", the
Linux sampler's "the most of it X, at 38.5% of a CPU"), its time; or a figure form, a share that
is about no process (the rule's bar, a share of a pass). Anything else is refused, and so is a
kind the sampler saw with no share. It holds no name of any program: it knows only the kinds
and the forms. A name in prose with no share is beyond it.

A library, not a command.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAP_ENV = "ITCH_PROCESS_KINDS"

BROWSER = "a browser"
BROWSER_CONTENT = "a browser's content process"
BACKUP_TOOL = "a backup tool"
BACKUP_TIMER = "the host's backup timer"
FILE_SYNC = "a file-sync tool"
VPN_DAEMON = "a VPN daemon"
ASSISTANT = "another assistant session"
OTHER_PROJECT = "another project's process"
OTHER_PROJECT_TESTS = "another project's tests"
OTHER_PROJECT_FUZZER = "another project's fuzzer"
MACOS_DAEMON = "a macOS system daemon"
INDEXER = "the system indexer"
LINUX_DAEMON = "a Linux system daemon"
KERNEL_THREAD = "a kernel thread"
SYSTEM_TOOL = "a system tool"
LOGIN_SHELL = "the login shell"
THIS_PROJECT = "another process of this project"
OTHER = "another process"

KINDS = (BROWSER, BROWSER_CONTENT, BACKUP_TOOL, BACKUP_TIMER, FILE_SYNC, VPN_DAEMON, ASSISTANT,
         OTHER_PROJECT, OTHER_PROJECT_TESTS, OTHER_PROJECT_FUZZER, MACOS_DAEMON, INDEXER,
         LINUX_DAEMON, KERNEL_THREAD, SYSTEM_TOOL, LOGIN_SHELL, THIS_PROJECT, OTHER)
# Where the Linux sampler saw CPU busy with no process to give: no process at all.
UNNAMED = "no process to name, so kernel or interrupt work"


class MapError(Exception):
    """A map that cannot be used. Its message never holds the map's path or anything read from
    the map, and it is not a ValueError, so that no tool's refusal handler, which writes what it
    catches into a run-log row, takes it for a refusal."""


def load(path=None):
    """The map at path, or at $ITCH_PROCESS_KINDS, or None if neither is set."""
    path = path or os.environ.get(MAP_ENV)
    if not path:
        return None
    unusable = f"{MAP_ENV}: the name-to-kind map it gives"
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise MapError(f"{unusable} cannot be read ({error.strerror or 'an OS error'})") from None
    except ValueError:
        raise MapError(f"{unusable} is not JSON") from None
    try:
        programs = dict(found.get("programs", {}))
        prefixes = [(str(prefix), kind) for prefix, kind in found.get("prefixes", [])]
        patterns = [(re.compile(pattern), kind) for pattern, kind in found.get("patterns", [])]
    except (AttributeError, TypeError, ValueError, re.error):
        raise MapError(f"{unusable} is not a map of programs, prefixes and patterns") from None
    unknown = [kind for kind in [*programs.values(), *(k for _, k in prefixes),
                                 *(k for _, k in patterns)]
               if not isinstance(kind, str) or kind not in KINDS]
    if unknown:
        raise MapError(f"{unusable} gives {len(unknown)} kind(s) that are not in "
                       "process_kinds.KINDS")
    return {"programs": programs, "prefixes": prefixes, "patterns": patterns}


_LOADED = {}


def default_map(tool="process_kinds"):
    """load() once per value of $ITCH_PROCESS_KINDS. A map that is set and cannot be used stops
    the tool (exit 2) with a message that gives no path: it never becomes a row's text."""
    key = os.environ.get(MAP_ENV)
    if key not in _LOADED:
        try:
            _LOADED[key] = load(key)
        except MapError as error:
            print(f"{tool}: {error}. Stopped, with no row written for it: point it at a readable "
                  "map, or unset it to give every process as another process.", file=sys.stderr)
            raise SystemExit(2) from None
    return _LOADED[key]


def check_map(tool):
    """For a tool's main, before it runs or writes anything: default_map(), so that a map that
    is set and cannot be used stops the tool there."""
    default_map(tool)


def program_of(command):
    """The program's name: the command's first word with its folder stripped, and the
    parentheses ps puts around a process that has no command line taken off."""
    words = command.split()
    if not words:
        return ""
    first = words[0]
    if first.startswith("(") and first.endswith(")"):
        first = first[1:-1]
    return first.rstrip("/").rsplit("/", 1)[-1]


def kind_of(command, kinds=None):
    """The kind of the process a sampler logged as command. kinds is a map from load(); with
    none given, $ITCH_PROCESS_KINDS's, and with none there, every process is OTHER."""
    kinds = default_map() if kinds is None else kinds
    if not kinds:
        return OTHER
    program = program_of(command)
    if program in kinds["programs"]:
        return kinds["programs"][program]
    for prefix, kind in kinds["prefixes"]:
        if command.startswith(prefix):
            return kind
    first = command.split()[0] if command.split() else ""
    for pattern, kind in kinds["patterns"]:
        if pattern.search(program) or pattern.search(first):
            return kind
    return OTHER


# A reason a driver wrote before 2026-11-13 into a raw record: "the CPU sampler saw <program> at
# 87.5% CPU at ...", with the program's name where its kind now goes.
NAMED_REASON = re.compile(r"\bsaw (\S+) at (?=\d)")


def reworded(reason, kinds=None):
    """A sampler's reason with the program it names, if any, given by its kind (kind_of)."""
    return NAMED_REASON.sub(lambda match: f"saw {kind_of(match.group(1), kinds)} at ", reason)


# The run log's grammar. It is closed: every CPU share in an outcome must sit in one of the
# forms below, and anything else is refused. A process form gives a process by one of KINDS,
# with its share, and where a tool wrote it (the sampler's "saw", "the first"), with its time;
# a figure form is a share that is about no process. Nothing here names a program.

def _kinds(kinds):
    return "|".join(re.escape(kind) for kind in sorted(kinds, key=len, reverse=True))


KIND = f"(?:{_kinds(KINDS)})"
# "a backup tool and its file-sync tool": two kinds, the second by "its".
KIND_PAIR = (f"{KIND} and its (?:{_kinds(re.sub(r'^(?:a|an|the) ', '', kind) for kind in KINDS)})")
SHARE = r"\d+(?:\.\d+)?%"
SHARES = rf"{SHARE}(?: to {SHARE})?"
ISO = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z"
HMS = r"\d\d:\d\d:\d\d UTC"
# When a process was seen, after its share.
WHEN = (rf"(?: CPU)?(?: at {ISO}| inside its span| inside it| in it| during it"
        rf"(?: \(sampler, \d\d:\d\d:\d\d and {HMS}\))?| at its first second| from its first second"
        rf"| from {HMS}| \({HMS}\)| in every \d+ s sample of the other piece's CPU sampler from "
        rf"\d\d:\d\d:\d\d to {HMS})")
VERB = r"(?: at| at up to| up to| ran at| ran at up to| were seen at| were running at)"
# The Linux sampler's: what was busy at a moment, and the most of it, by kind, at its own share.
LINUX_WHAT = (rf"(?:the measured core: its process waited {SHARE} of a second on the run queue"
              rf"|the core's SMT sibling: {SHARE} busy|the other cores: {SHARE} of a CPU busy)")
PROCESS_FORMS = (
    # A tool's: the sampler saw a kind at its share and time.
    rf"\bsaw {KIND} at {SHARE}{WHEN}",
    rf"\bthe first, {KIND}, at {SHARE}{WHEN}",
    rf"\bthe first {KIND} at {SHARE}{WHEN}",
    rf"\bsaw {LINUX_WHAT} at {ISO}, the most of it (?:{KIND}, at {SHARE} of a CPU|{KIND}"
    rf"|{re.escape(UNNAMED)})",
    # Typed rows' lists and clauses: a kind, or two, right after where a clause starts.
    rf"(?:[;:,] |\band |\bbecause |\bthe host's backup timer ran )(?:{KIND_PAIR}|{KIND})"
    rf"{VERB} {SHARES}{WHEN}?",
)
FIGURE_FORMS = (
    # The sampler rule's bar.
    rf"\b(?:no|any|\d+) other process(?:es)? (?:went )?above {SHARE} CPU",
    rf"\blogged other processes above {SHARE} CPU",
    rf"\bat most {SHARE} run-queue wait on core \d+, {SHARE} busy on cpu\d+, {SHARE} of a CPU on "
    r"the other cores",
    # Shares of a pass, of a floor, of messages, of a run's speed.
    rf"\b{SHARE} of the pass\b",
    rf"\bat {SHARE} of the floor\b",
    rf"\breaches the {SHARE} rule\b",
    rf"; {SHARE} of those messages name\b",
    rf"[+-]{SHARE} over D=\d+",
    rf"\bunder {SHARE} at every D\b",
    rf"[+-]{SHARE} \([\w-]+ over [\w-]+(?:, ranges overlap)?\)",
    rf"\bwindow of \d+ ticks {SHARE}, of [\d,]+ {SHARE} \(carteret {SHARE} and {SHARE}\); "
    rf"sub-cent {SHARE}; add searches past \d+ or fewer better levels {SHARE}, past \d+ or more "
    rf"{SHARE}",
    rf"\bcpu\d+ and cpu\d+ each {SHARE} busy\b",
    rf"\brun about {SHARE} and {SHARE} above the other\b",
)
_PROCESS = [re.compile(form) for form in PROCESS_FORMS]
_FIGURE = [re.compile(form) for form in FIGURE_FORMS]
# Any share at all, as written: "41.2%", "41.2 %", "41.2 percent".
ANY_SHARE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:%|percent\b)")
# A kind the sampler saw, or the first or the most of it, must come with its share: a kind
# here outside a process form is refused.
SEEN = re.compile(rf"\b(?:saw|the first,?|the most of it) ({KIND})")


def cause_breaks(cause):
    """What in a run-log outcome breaks the grammar: each share in no known form, with the
    words before it, and each kind seen by the sampler with no share and time."""
    covered = []
    for form in (*_PROCESS, *_FIGURE):
        covered += [match.span() for match in form.finditer(cause)]
    def inside(span):
        return any(begin <= span[0] and span[1] <= end for begin, end in covered)
    breaks = [f"{cause[max(0, match.start() - 40):match.end()].strip()!r}: a share in no form"
              for match in ANY_SHARE.finditer(cause) if not inside(match.span())]
    breaks += [f"{cause[match.start():match.end() + 30].strip()!r}: a kind seen with no share "
               "and time" for match in SEEN.finditer(cause) if not inside(match.span())]
    return breaks


def run_log_rows(text):
    """(line number, outcome) for each run of a run log's table."""
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 5:
            continue
        if cells[0] == "Run" or all(re.fullmatch(r"-+", cell) for cell in cells):
            continue
        rows.append((number, " | ".join(cells[4:])))
    return rows
