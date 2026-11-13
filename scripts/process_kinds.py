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

tried in that order. A map whose kinds are not all in KINDS is refused.

cause_breaks() is the run log's grammar. Wherever a row's cause gives a process with its CPU
share ("saw X at 41.2% CPU", "the first, X, at 100.0%", "X ran at 92% to 100% CPU", "(X, 28.4%)"),
or as the busiest other work ("the most of it X"), X must be a kind. It holds no name of any
program: it knows only the kinds.

A library, not a command.
"""

from __future__ import annotations

import json
import os
import re
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


class MapError(ValueError):
    pass


def load(path=None):
    """The map at path, or at $ITCH_PROCESS_KINDS, or None if neither is set."""
    path = path or os.environ.get(MAP_ENV)
    if not path:
        return None
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MapError(f"{MAP_ENV}: cannot read {path}: {error}") from error
    programs = found.get("programs", {})
    prefixes = [tuple(pair) for pair in found.get("prefixes", [])]
    patterns = [(re.compile(pattern), kind) for pattern, kind in found.get("patterns", [])]
    unknown = sorted({kind for kind in [*programs.values(), *(k for _, k in prefixes),
                                        *(k for _, k in patterns)] if kind not in KINDS})
    if unknown:
        raise MapError(f"{MAP_ENV}: {path} gives kinds that are not in process_kinds.KINDS: "
                       f"{unknown}")
    return {"programs": programs, "prefixes": prefixes, "patterns": patterns}


_LOADED = {}


def default_map():
    """load() once per value of $ITCH_PROCESS_KINDS."""
    key = os.environ.get(MAP_ENV)
    if key not in _LOADED:
        _LOADED[key] = load(key)
    return _LOADED[key]


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


# The run log's grammar.

def _core(phrase):
    """A kind without its article, so that "its backup tool" reads as "a backup tool"."""
    return re.sub(r"^(?:a|an|the|its)\s+", "", phrase.strip())


KIND_CORES = frozenset(_core(kind) for kind in KINDS)
SHARE = re.compile(r"(?<![\w.+-])(\d+(?:\.\d+)?)%")
# What may stand between a process and its share: "X at 41.2%", "X at up to 193.1%",
# "X up to 199.2%", "X ran at", "X were seen at", "the first, X, at".
BEFORE_SHARE = re.compile(r"(?:,?\s+(?:ran|were seen|were running|was seen|was running))?"
                          r",?\s+(?:at up to|at|up to)\s*$")
# Where the words that give a process begin.
CLAUSE_START = re.compile(r".*(?:[;:,(]|\b(?:saw|ran|and|because|while)\b|\bthe first\b)\s*",
                          re.DOTALL)
MOST_OF_IT = re.compile(r"\bthe most of it ([^,;|]+)")


def _subject(text):
    """The words that give a process, from the end of text back to where its clause starts."""
    text = text.rstrip().rstrip(")").rstrip()
    match = CLAUSE_START.match(text)
    subject = text[match.end():] if match else text
    return subject.strip().strip("`").strip()


def cited(cause):
    """Every phrase in a cause that gives a process with its CPU share, or as the busiest
    other work, in order."""
    found = []
    for match in SHARE.finditer(cause):
        before, after = cause[:match.start()], cause[match.end():]
        if after.startswith(" of") or re.search(r"\babove\s*$", before):
            continue  # A share of something else, or the rule's bar.
        if re.search(r"\(\s*[^()]*,\s*$", before) and after.startswith(")"):
            found.append(_subject(before.rstrip().rstrip(",")))  # "(X, 28.4%)"
            continue
        if re.search(r"%\s+to\s*$", before):
            continue  # The top of a range: the process came with its bottom.
        trimmed = BEFORE_SHARE.sub("", before)
        if trimmed != before:
            found.append(_subject(trimmed))
    found += [match.group(1).strip() for match in MOST_OF_IT.finditer(cause)]
    return found


def cause_breaks(cause):
    """Each phrase in a run-log cause that gives a process by something other than a kind."""
    return [subject for subject in cited(cause) if _core(subject) not in KIND_CORES]


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
