#!/usr/bin/env python3
"""Every figure in the README, RESULTS.md, WRITEUP.md, docs/DETAILS.md and the demo page is
held to the results file its line cites, and to a place in that file that its words name, and
every verdict word to the result of the target it names (criterion 30).

    gate_numbers.py [--root DIR] [--page PATH]...

What a figure is. A number with a decimal point, a thousands separator, three or more digits, a
unit or a sign: a `-`, `−` or `+` straight before it, or before its `$`, is read as part of
it, so -27.50 and -$27.50 are not 27.50, and +11.1% is 11.1%. On a line that cites
`results/<name>.md`, an integer of one or two digits is a figure too, except straight after a
name in NAMED_BEFORE (Zen 1, Ryzen 7) or straight before a word in NAMED_AFTER (4 cores, 8
threads): those name hardware, they count nothing measured. Left out: code spans and code
blocks (and in HTML the text of <pre>, <code> and <option>), URLs, the text of a link to a URL
(a source's title), HTML tags, dates (2026-11-12, and eight digits that read as a day,
12302019), the names in EXEMPT, a number glued to a word (S4, p50, T-BOOK-1, x86-64), and any
line ending in `<!-- figures: not measured -->`. A count written as a word, zero and two to
twenty, and one straight before `of` and a count ("one of three"), is a short integer: read on
a line that cites a results file, except in a block results_table.py generates, whose counts
its --check holds.

Which file. A figure is held to the files cited in its clause (a clause of prose ends at `;` or
at the end of a sentence; a table row is one clause); if its clause cites none, to those its
sentence cites; if that cites none, to those its line cites. A line of prose that holds a
figure must cite a results file. A table row that cites none is held to every results file,
by the same rules. predictions.md and run_log.md are not results: a figure predicted is not a
figure measured. Of fault_matrix.md only the header above its table is a source: the table
quotes each fault's output, and so the figures a fault made up to be caught.

Which place. In a results file a figure stands in places: a cell of a table row, or a segment
of any other line (a `key: value` line or prose), ending at `;` or at the end of a sentence. A
figure on the page is held only by a place in a cited file that holds the same figure, sign
included, and whose words its clause shares. A cell's words are its column head's and its
own, and, when the clause names no label (below), its row label's and its table's caption's
too (the last line of text above the table); a cell is shared too when the page's column head
is the same as its own. A segment's words are its own. The words of a page table row are the
row's and its table's head row's. A word is a run of letters and digits with at least one
letter, not in STOPWORDS, with a plural or tense ending taken off; two words are shared when
they are equal or, both five letters or more, one begins the other.

Which row. A row's labels are its first cell, and the part of it before a comma, each when it
is a phrase of two to eight words or a word holding a digit, `-` or `_` (S4, pool-index,
T-BOOK-1; not max or add). A figure's label is a label of a row of a table in the cited file
that holds the figure: the one in brackets straight after it, "187,539 (S2)", else the one
nearest before it in its clause (in a page table row, in its cell after the last `;`). A figure
with a label is held only by a cell in that label's row or a segment where the label stands
before it. When a clause lists labels and then as many figures, each list joined by `,`, `and`
or `or` ("`pool-index`, `pool-pointer` and `pool-fifo` take 136, 200 and 146"), the k-th
figure's label is the k-th label; and if a `key: value` line of a cited file whose key the
clause names (`fault_check` by "fault check") gives that label figures, as "pool-index 136",
the figure must be one of them, in at least one cited file that gives the label any.

A one-word row label (rigtorp, `switch`) is not a label above, since a lone word names too
much, but it holds a figure that stands beside it, when a table of the cited file that holds
the figure has a row of that one word (its first cell, or the part before a comma): the word
straight before the figure ("`switch` 2.0303"), or, for the first figure of a page table
row's second cell, the row's first cell ("| rigtorp | 198.5 |"). Such a figure is held only
by a cell in a row of that word, or a cell or segment where the word stands straight before
it; the word straight before a figure is nearer than any label further back in its clause.

The two integers of an `N of M` ("22 of 33") must stand together in one cited file: each held
by the rules above in one table, or in one line outside tables, and twice there if N is M.

Verdicts. Outside code blocks and the blocks results_table.py generates (held by its --check),
a verdict word is read in a sentence of prose or a table row: met, meets, missed, fails,
failed, failing, passed, PASS and FAIL anywhere; misses, missing and passes only straight after
a target id or after its gloss between commas or in brackets ("T-PERF-2, the branch-miss rate,
misses"), since elsewhere each is a noun (branch misses, timed passes); and "a miss by" and the
phrases of no verdict ("no verdict", "neither a pass nor a fail", "neither met nor missed",
"neither meets nor misses", "neither passes nor fails") only where a target is named before
them. Each is tied to the target ids its sentence names after the verdict word before it, or,
if none, to those that word was tied to: "T-SPSC-1 and T-SPSC-2 are missed and T-SPSC-3 is met".
The word must agree with each tied target's one verdict, results_table.one_verdict() of its
rows in RESULTS.md's tables (missed if any row its bar judges is missed or FAIL, met if every
such row is met or PASS, none if no row is judged; a target with no row, from
results_table.unrowed_verdict()). When the sentence names a host (Mac, macOS, M4, Apple, Darwin;
debian, Zen 1, V1605B), a day (12302019, 01302019_BX) or a design (`paged-flat`, base) that
some of the target's rows have, only those rows count, so "T-BT-3 is met on the Mac" passes
where "T-BT-3 is met" fails. A target id that is neither BENCHMARKS.md's nor in a row fails. A
count straight after the verdict word, "fails on four of the ten sessions" or "fails on two",
must be the number of the target's rows with that verdict, and of all its rows; such a count
is held here and not read as a figure. A verdict word read anywhere that ties to no target
fails, unless it is in a heading, beside the word target ("every missed target"), or on a line
that ends in `<!-- verdicts: no target named -->`.

What it cannot do. It does not read units, so 5.15 M against a cell of 5.15 ms passes. It
reads words, not meaning, so a clause that shares a word with the wrong place by chance passes,
and a clause that names no label is held by any place that shares a word. It does not follow
arithmetic, so a figure the page derives (a sum, a difference, a rounding) must stand as
written in the file. Two figures of one clause that both share its words, with no label or
list to tell them apart, can trade places unseen; the generated tables are held cell by cell
by `results_table.py --check` instead. So the gate holds that each figure is in its cited file
beside a word its clause shares, and, where a label, a one-word row label or an `N of M` ties
it, in that row or beside that partner; it does not hold that it is the figure for that place
when the file has the same figure elsewhere under a shared word. Which short integers and
counts in words count is decided by the words next to them, not by what they count, and on a
line that cites no results file none is read: there a short integer is as likely a count of
something built (48 scenarios) as a measurement, so a line whose only numbers are short
integers needs no citation. A verdict is read by its word, not its meaning: one said in other
words ("short of its bar", "clean") is not read, nor one left out ("and T-TRADE-5 on two"),
and a target with rows of both verdicts on the host, day and design its sentence names is
held to the miss.

The gate runs on whichever pages exist, so a tree with none passes and says so. A page
named with --page must exist.

Each violation prints as `gate_numbers: FAIL <page>:<line>: <detail>`.

Exit codes: 0 passed, 1 a figure with no source or a verdict that disagrees, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import results_table  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAGES = ("README.md", "RESULTS.md", "WRITEUP.md", "docs/DETAILS.md", "docs/index.html")
NOT_RESULTS = {"predictions.md", "run_log.md"}
HEADER_ONLY = {"fault_matrix.md"}
EXEMPT = ("ITCH 5.0", "C++23", "BinaryFILE 1.0")
NAMED_BEFORE = ("Zen", "Ryzen", "GCC", "Clang", "Python", "Layer")
NAMED_AFTER = ("cores", "threads")
SKIP_MARK = "<!-- figures: not measured -->"

FENCE = re.compile(r"^(```|~~~)")
DAY = r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:19|20)\d\d"
STRIPPED = re.compile(
    r"`[^`\n]*`|\[[^\]\n]*\]\(https?://[^)\n]*\)|\]\([^)\n]*\)|https?://\S+"
    r"|<a\s[^>]*href=\"https?://[^\"]*\"[^>]*>.*?</a>"
    r"|<(script|style|pre|code|option)\b.*?</\1>|<[^>]*>"
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b|(?<![\d,.]){DAY}(?!\d|[.,]\d)"
    r"|\b(?:phase|step|criterion) \d+\b", re.IGNORECASE | re.DOTALL)
UNIT = r"(?:%|×|\s?(?:ns|µs|us|ms|s|GB|GiB|MB|MiB|KB|KiB|TB|GHz|MHz|M|K|B|x)\b)"
FIGURE = re.compile(
    rf"(?<![\w.,/+−-])([-−+](?=\$?\d))?\$?(\d(?:,?\d)*(?:\.\d+)?)({UNIT}?)(?![\w%]|[.,]\d)")
TOKEN = re.compile(r"(?<![\w.,/+−-])([-−+](?=\$?\d))?\$?(\d(?:,?\d)*(?:\.\d+)?)(?!\d|[.,]\d)")
CITATION = re.compile(r"results/([\w.-]+\.md)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
WORD = re.compile(r"[a-z0-9µ][\w'+/.-]*[a-z0-9]|[a-z]")
CLAUSE_END = re.compile(r";|(?<=[.!?])\s+(?=[A-Z\[(*`])")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[(*`])")
KEY_LINE = re.compile(r"^\s*(?:[-*]\s+)?\**([A-Za-z][\w .'/()-]{0,48}?)\**:\s")
SEPARATOR = re.compile(r"\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*")
JOIN = r"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)"
STOPWORDS = frozenset("""
a an and are as at be been but by each for from had has have in into is it its itself last
least more most no not of on one or over per s than that the their them then there these this
those to under up was were what when where which while with without all any both every
ns ms us µs m k b x gb gib mb mib kb kib
""".split())
RATES = {"messages/s": "msg/s", "msgs/s": "msg/s", "message": "msg", "messages": "msg",
         "msgs": "msg"}

# Counts written as words, read as figures on a line that cites a results file; `one` only
# straight before `of` and a count ("one of three"), since it is also a pronoun.
COUNTS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen", "twenty")
COUNT = r"(?:\d+|" + "|".join(COUNTS) + r")"
COUNT_WORD = re.compile(
    rf"(?<![\w-])(?i:(one)(?=\s+of\s+{COUNT}(?![\w-]))|({'|'.join(COUNTS[0:1] + COUNTS[2:])}))"
    r"(?![\w-])")
GENERATED_BEGIN = re.compile(r"^<!-- [\w ]+: begin, generated by scripts/results_table\.py")
GENERATED_END = re.compile(r"^<!-- [\w ]+: end -->$")

# Verdict words. Read anywhere: these. Read only straight after a target id or its gloss,
# since each is also a noun (branch misses, timed passes): STRAIGHT. A miss by a gap, and the
# phrases of no verdict, are read only where a target is named before them.
VERDICT_MARK = "<!-- verdicts: no target named -->"
ANYWHERE = re.compile(r"(?<![\w-])(?:(?i:met|meets|missed|fails|failed|failing|passed)|PASS|FAIL)"
                      r"(?![\w-])")
STRAIGHT = re.compile(r"(T-[A-Z0-9]+-\d+)(?:\s*\([^()]*\))?(?:,[^,;]*,)?\s+(?:also\s+|still\s+)?"
                      r"((?i:misses|missing|passes))(?![\w-])")
TIED_ONLY = re.compile(
    r"(?<![\w-])(?i:(a miss by)|(no verdict|neither a pass nor a fail|neither met nor missed"
    r"|neither meets nor misses|neither passes nor fails))(?![\w-])")
NEGATIVE = {"missed", "misses", "missing", "fails", "failed", "failing", "fail", "a miss by"}
TARGET = re.compile(r"(?<![\w-])T-[A-Z0-9]+-\d+(?![\w-])")
BESIDE_TARGET = re.compile(r"(?i:targets?\s+$)")
BEFORE_TARGET = re.compile(r"(?i:^\s+targets?\b)")
AFTER_VERDICT = re.compile(
    rf"^\s+(?:strictly\s+)?on\s+({COUNT})(?:\s+of\s+(?:the\s+)?({COUNT})\s+(?:sessions|days)\b"
    r"|(?=\s+(?:sessions|days)\b|\s*(?:[,;.)]|$)))", re.I)
MAC_WORDS = re.compile(r"(?i)(?<![\w-])(?:mac|macos|m4|m4 max|apple|darwin)(?![\w-])")
LINUX_WORDS = re.compile(r"(?i)(?<![\w-])(?:debian|zen 1|v1605b|linux host)(?![\w-])")
DAY_WORD = re.compile(r"(?<!\d)(\d{8}(?:_[A-Z]+)?)(?![\d\w])")


def unique(names):
    return list(dict.fromkeys(names))


def normal(sign, digits):
    """A figure as compared: separators dropped, `−` read as `-`, a `+` as no sign."""
    value = digits.rstrip(",").replace(",", "")
    return ("-" + value) if sign in ("-", "−") else value


def plain(text):
    """Text as its words are read: links to their text, markup dropped, lower case."""
    return re.sub(r"[`*]", "", LINK.sub(r"\1", text)).lower()


def flat(text):
    """Text in lower case with its markup blanked, each character where it was."""
    return re.sub(r"[`*\[\]]", " ", text).lower()


def stem(word):
    """A word with a plural or tense ending taken off: misses, missed and miss are one."""
    word = RATES.get(word, word)
    if re.search(r"[\d/-]", word):
        return word
    for suffix in ("sses", "ies", "es", "ed", "ing", "s"):
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        if suffix == "es" and not re.search(r"(?:x|z|ch|sh)es$", word):
            continue
        if suffix == "s" and word.endswith("ss"):
            continue
        word = word[:-len(suffix)] + {"sses": "ss", "ies": "y"}.get(suffix, "")
        break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiousl":
        word = word[:-1]
    return word


def words_of(text):
    found = set()
    for word in WORD.findall(plain(text)):
        word = re.sub(r"'s$", "", word).strip(".'")
        if word and re.search(r"[a-zµ]", word) and word not in STOPWORDS:
            found.add(stem(word))
    return found


def shares(ours, theirs):
    """Whether two sets of words share one: equal, or one begins the other (five letters on)."""
    if ours & theirs:
        return True
    return any(len(a) >= 5 and len(b) >= 5 and (a.startswith(b) or b.startswith(a))
               for a in ours for b in theirs)


def phrase(label):
    return re.compile(rf"(?<![\w-]){re.escape(label)}(?![\w-])")


def tokens_in(text):
    for match in TOKEN.finditer(text):
        yield match.start(), normal(match.group(1), match.group(2))


def cells_of(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def labels_of(cell):
    """A row's labels: its first cell, and the part of it before a comma."""
    label = re.sub(r"\s+", " ", plain(cell)).strip()
    return {part for part in (label, label.split(",")[0].strip()) if distinct(part)}


def one_word(cell):
    """A row's one-word labels: its first cell, or the part of it before a comma, when that is
    one plain word (rigtorp, `switch`), which distinct() leaves out."""
    label = re.sub(r"\s+", " ", plain(cell)).strip()
    return {part for part in (label, label.split(",")[0].strip())
            if re.fullmatch(r"[a-z]+", part)}


def distinct(label):
    """Whether a label is one: a phrase of two to eight words, or a word holding a digit, `-`
    or `_` (S4, pool-index, T-BOOK-1); a lone plain word (max, add) names too much."""
    words = label.split()
    if not re.search(r"[a-z]", label) or len(words) > 8:
        return False
    return len(words) >= 2 or bool(re.search(r"[a-z].*[\d_-]|[\d_-].*[a-z]", label))


class Source:
    """A results file's places: where each figure stands, with the words around it."""

    def __init__(self, text):
        self.places = defaultdict(list)    # figure -> [Place]
        self.tables = defaultdict(set)     # table number -> its rows' labels
        self.words = defaultdict(set)      # table number -> its rows' one-word labels
        self.groups = defaultdict(list)    # group -> the figures in it, as often as they stand
        self.keyed = []                    # (key words, {label: {figures}}), key: value lines
        heads, table, caption, said = [], 0, set(), set()
        lines = text.splitlines()
        for number, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("|"):
                said = words_of(stripped)
            if stripped.startswith("|"):
                if SEPARATOR.fullmatch(stripped):
                    continue
                cells = cells_of(stripped)
                following = lines[number + 1].strip() if number + 1 < len(lines) else ""
                if SEPARATOR.fullmatch(following):
                    heads, table, caption = cells, table + 1, said
                    continue
                labels = labels_of(cells[0])
                word = one_word(cells[0])
                self.tables[table] |= labels
                self.words[table] |= word
                for index, cell in enumerate(cells):
                    head = heads[index] if index < len(heads) else ""
                    words = words_of(head) | (words_of(cell) if index else set())
                    for position, figure in tokens_in(cell):
                        self.places[figure].append(
                            ("cell", labels, words, table, words_of(cells[0]) | caption,
                             plain(head).strip(), word, ("table", table),
                             flat(cell)[:position]))
                        self.groups[("table", table)].append(figure)
                continue
            self.read_line(line, number)

    def read_line(self, line, number):
        key = KEY_LINE.match(line)
        given = defaultdict(set)
        for segment in CLAUSE_END.split(line):
            words = words_of(segment)
            text = flat(segment)
            for position, figure in tokens_in(segment):
                self.places[figure].append(("segment", set(), words, None, text[:position], "",
                                            set(), ("line", number), text[:position]))
                self.groups[("line", number)].append(figure)
                before = re.search(r"([\w.'-]+)\W*$", text[:position])
                if key and before:
                    given[before.group(1).strip(".'")].add(figure)
        if key and given:
            self.keyed.append((words_of(key.group(1).replace("_", " ")), given))

    def labels_holding(self, figure):
        """The labels of every row of every table that holds the figure somewhere."""
        tables = {place[3] for place in self.places.get(figure, ()) if place[0] == "cell"}
        return set().union(*(self.tables[table] for table in tables)) if tables else set()

    def words_holding(self, figure):
        """The one-word labels of every row of every table that holds the figure somewhere."""
        tables = {place[3] for place in self.places.get(figure, ()) if place[0] == "cell"}
        return set().union(*(self.words[table] for table in tables)) if tables else set()


def source_text(path):
    """The file's text, or for a file in HEADER_ONLY the lines above its first table row."""
    text = path.read_text(encoding="utf-8")
    if path.name in HEADER_ONLY:
        table = re.search(r"^\|", text, re.MULTILINE)
        text = text[:table.start()] if table else text
    return text


def load_results(root):
    return {path.name: Source(source_text(path))
            for path in sorted((root / "results").glob("*.md")) if path.name not in NOT_RESULTS}


class Figure:
    """A figure on the page, with the clause it stands in."""

    def __init__(self, line, figure, value, cited, context, row, span, position):
        self.line = line              # the page's line number
        self.figure = figure          # as printed on the page
        self.value = value            # as compared
        self.cited = cited            # the files it is held to, or None for every file
        self.context = context        # its clause's words
        self.row = row                # whether it stands in a table row
        self.span = span              # where its clause stands on the line
        self.position = position      # where it stands on the line
        self.label = None             # its label, when a list pairs it with one
        self.cell = span              # where its labels are looked for: in a table row, its
                                      # cell from the last `;` before it
        self.head = ""                # in a table row, its column's head, plain
        self.first = ""               # in a table row, its row's first cell
        self.second = False           # whether it stands in a table row's second cell
        self.partner = None           # the other figure of an `N of M`

    def __iter__(self):
        return iter((self.line, self.figure, self.cited))


def clauses_of(line):
    """(start, end, sentence start, sentence end) of each clause of a line of prose."""
    sentences, start = [], 0
    for sentence in SENTENCE_END.split(line):
        begin = line.index(sentence, start)
        sentences.append((begin, begin + len(sentence)))
        start = begin + len(sentence)
    clauses, start = [], 0
    for clause in CLAUSE_END.split(line):
        begin = line.index(clause, start)
        end = begin + len(clause)
        sentence = next((s for s in sentences if s[0] <= begin < s[1]), (begin, end))
        clauses.append((begin, end, *sentence))
        start = end
    return clauses


def named(masked, start, digits):
    """Whether a short integer names hardware or software rather than counting."""
    before = re.search(r"(\w+)\s+$", masked[:start])
    after = re.match(r"\s+(\w+)", masked[start + len(digits):])
    return bool(before and before.group(1) in NAMED_BEFORE
                or after and after.group(1) in NAMED_AFTER)


def figures_in(text):
    """Each figure on the page, as a Figure that reads as (line number, figure, cited)."""
    in_fence = False
    head = ""
    lines = text.splitlines()
    masked_text = "\n".join(lines)
    for name in EXEMPT:
        masked_text = masked_text.replace(name, " " * len(name))
    masked_text = STRIPPED.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), masked_text)
    masked_lines = masked_text.split("\n")
    generated = generated_lines(lines)
    for number, line in enumerate(lines, start=1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or SKIP_MARK in trailing_marks(line):
            continue
        masked = masked_lines[number - 1]
        cited_line = unique(CITATION.findall(line))
        row = line.strip().startswith("|")
        if row:
            following = lines[number].strip() if number < len(lines) else ""
            if SEPARATOR.fullmatch(following):
                head = line
            if SEPARATOR.fullmatch(line.strip()) or line == head:
                continue
            spans = [(0, len(line), 0, len(line))]
        else:
            spans = clauses_of(line)
        for begin, end, sentence_begin, sentence_end in spans:
            cited = (unique(CITATION.findall(line[begin:end]))
                     or unique(CITATION.findall(line[sentence_begin:sentence_end])) or cited_line)
            context = words_of(line[begin:end] + (" " + head if row else ""))
            found = []
            for match in FIGURE.finditer(masked, begin, end):
                sign, digits, unit = match.groups()
                short = not (sign or unit or "." in digits or "," in digits or len(digits) >= 3)
                if short and (not cited_line or named(masked, match.start(2), digits)):
                    continue
                found.append((match.start(), (sign or "") + digits, normal(sign, digits)))
            # A count in words is a short integer: read only where the line cites a results
            # file, and not in a generated block, whose counts results_table.py --check holds.
            if cited_line and number not in generated:
                for match in COUNT_WORD.finditer(masked, begin, end):
                    if not named(masked, match.start(), match.group(0)):
                        found.append((match.start(), match.group(0),
                                      str(COUNTS.index(match.group(0).lower()))))
            for position, printed, value in sorted(found):
                figure = Figure(number, printed, value, cited or None, context, row, (begin, end),
                                position)
                if row:
                    bars = [index for index, char in enumerate(line) if char == "|"]
                    left = [index for index in bars if index < position]
                    right = [index for index in bars if index > position]
                    start = left[-1] + 1 if left else 0
                    start = max([start] + [index + 1 for index, char in enumerate(line)
                                           if char == ";" and start <= index < position])
                    figure.cell = (start, right[0] if right else len(line))
                    heads = cells_of(head)
                    column = len(left) - (1 if line.lstrip().startswith("|") else 0)
                    if 0 <= column < len(heads):
                        figure.head = plain(heads[column]).strip()
                    if column > 0:
                        figure.first = cells_of(line)[0]
                        figure.second = column == 1
                yield figure


def runs_of(spans, text):
    """Spans (start, end, item) grouped into lists: those joined only by `,`, `and` or `or`."""
    runs = []
    for span in sorted(spans, key=lambda span: span[:2]):
        if runs and re.fullmatch(JOIN, text[runs[-1][-1][1]:span[0]]):
            runs[-1].append(span)
        elif not runs or span[0] >= runs[-1][-1][1]:
            runs.append([span])
    return runs


def pair_lists(figures, line, labels):
    """Give each figure of a listed run its label, when as many labels are listed just before
    it in its clause."""
    text = flat(line)
    by_clause = defaultdict(list)
    for figure in figures:
        if not figure.row:
            by_clause[figure.span].append(
                (figure.position, figure.position + len(figure.figure), figure))
    for (begin, end), spans in by_clause.items():
        found = [(begin + match.start(), begin + match.end(), label) for label in labels
                 for match in phrase(label).finditer(text[begin:end])]
        listed = runs_of(found, text)
        for run in runs_of(spans, text):
            before = [labels_ for labels_ in listed if labels_[-1][1] <= run[0][0]]
            if len(run) >= 2 and before and len(before[-1]) == len(run):
                for (_, _, figure), (_, _, label) in zip(run, before[-1]):
                    figure.label = label


def nearest_label(source, figure, text):
    """The label of a row of a table that holds the figure: one in brackets straight after it,
    else the nearest before it in its clause."""
    before = text[figure.cell[0]:figure.position]
    after = re.match(r"\s*\(\s*([^()]*?)\s*\)", text[figure.position + len(figure.figure):])
    labels = source.labels_holding(figure.value)
    if after and after.group(1) in labels:
        return after.group(1)
    best = None
    for label in labels:
        for match in phrase(label).finditer(before):
            if best is None or match.end() > best[0] or (match.end() == best[0]
                                                         and len(label) > len(best[1])):
                best = (match.end(), label)
    return best[1] if best else None


def beside(source, figure, text):
    """The one-word row label the figure stands beside, when a table of the file that holds the
    figure has it as a row label, and whether it is the word straight before the figure: the
    word straight before it (`switch` 2.0303), else, for the first figure of a page table row's
    second cell, the row's first cell (| rigtorp | 198.5 |)."""
    words = source.words_holding(figure.value)
    before = re.search(r"(?<![\w-])([a-z]+)[\s:]*$", text[figure.cell[0]:figure.position])
    if before and before.group(1) in words:
        return before.group(1), True
    first = one_word(figure.first) & words
    if first and figure.second and not re.search(r"\d", text[figure.cell[0]:figure.position]):
        return next(iter(first)), False
    return None, False


def held_where(source, figure, text):
    """The groups (a table, or a line outside tables) of every place in one results file that
    holds the figure where its clause names it; empty if none does."""
    label = figure.label
    if label is not None:
        given = [figures[label] for key, figures in source.keyed
                 if key and key <= figure.context and label in figures]
        if given:
            return {("key", label)} if any(figure.value in figures for figures in given) \
                else set()
    else:
        label = nearest_label(source, figure, text)
    word, straight = beside(source, figure, text)
    if straight and figure.label is None:
        label = None    # The word straight before the figure is nearer than any label.
    found = set()
    for kind, labels, words, _, extra, head, row_words, group, ahead in \
            source.places.get(figure.value, ()):
        if label is not None:
            if kind == "cell" and label not in labels:
                continue
            if kind == "segment" and not phrase(label).search(extra):
                continue
        elif kind == "cell":
            words = words | extra
        if word is not None and word not in row_words \
                and not re.search(rf"(?<![\w-]){word}[\s:]*$", ahead):
            continue
        if shares(words, figure.context) or (head and head == figure.head):
            found.add(group)
    return found


def held_in(source, figure, text):
    """Whether one results file holds the figure in a place its clause names."""
    return bool(held_where(source, figure, text))


def together(source, figure, text):
    """Whether one results file holds an `N of M`'s two figures together: each where its
    clause names it, both in one table or one line, and twice if N is M."""
    partner = figure.partner
    for group in held_where(source, figure, text) & held_where(source, partner, text):
        if group[0] != "key" and (figure.value != partner.value
                                  or source.groups[group].count(figure.value) >= 2):
            return True
    return False


def pair_of_of(figures, line):
    """Pair the two integers of each `N of M` on a line."""
    ordered = sorted(figures, key=lambda figure: figure.position)
    for first, second in zip(ordered, ordered[1:]):
        if not all(re.fullmatch(r"\d+", figure.value) for figure in (first, second)):
            continue
        if re.fullmatch(r"\s+of\s+", line[first.position + len(first.figure):second.position]):
            first.partner, second.partner = second, first


def generated_lines(lines):
    """The numbers, from 1, of the lines inside a block that results_table.py generates."""
    inside, found = False, set()
    for number, line in enumerate(lines, start=1):
        if GENERATED_BEGIN.match(line):
            inside = True
        elif GENERATED_END.match(line):
            inside = False
        elif inside:
            found.add(number)
    return found


def trailing_marks(line):
    """The marks a line ends with, SKIP_MARK and VERDICT_MARK, in either order."""
    marks, rest = set(), line.rstrip()
    while True:
        for mark in (SKIP_MARK, VERDICT_MARK):
            if rest.endswith(mark):
                marks.add(mark)
                rest = rest[:-len(mark)].rstrip()
                break
        else:
            return marks


def readable(line):
    """The line with its markup blanked, each character where it was: emphasis, code ticks,
    brackets, a link's target, HTML tags and comments."""
    blank = lambda match: " " * len(match.group(0))  # noqa: E731
    text = re.sub(r"\]\([^)\n]*\)", blank, line)
    text = re.sub(r"<!--.*?-->|<[^>]*>", blank, text)
    return re.sub(r"[`*\[\]]", " ", text)


class Verdicts:
    """Every target's rows as results_table.py reads them from the results files, made the first
    time a page asks, and BENCHMARKS.md's targets."""

    def __init__(self, root):
        self.root = root
        self.rows = None
        self.error = None
        self.targets = set()

    def load(self):
        if self.rows is None and self.error is None:
            try:
                rows = results_table.target_rows(self.root)
                self.targets = set(results_table.benchmark_targets(self.root)[1])
            except (results_table.TableError, OSError) as error:
                self.error = str(error)
                return
            self.rows = defaultdict(list)
            for row in rows:
                self.rows[row[0]].append(row)

    def judge(self, target, sentence):
        """(verdict, rows) for a target, from the rows of it that the sentence names by host, day
        or design when it names any it has; ("unknown", []) for a target that is not one."""
        self.load()
        rows = self.rows.get(target, [])
        if not rows:
            if target not in self.targets:
                return "unknown", []
            try:
                return results_table.unrowed_verdict(target), []
            except KeyError:
                return None, []
        hosts = {host for host, words in (("macOS", MAC_WORDS), ("debian", LINUX_WORDS))
                 if words.search(sentence)}
        days = set(DAY_WORD.findall(sentence))
        lowered = sentence.lower()
        for keep in (lambda row: len(hosts) == 1 and row[2] in hosts,
                     lambda row: row[1] in days,
                     lambda row: bool(row[3]) and phrase(row[3].lower()).search(lowered)):
            narrowed = [row for row in rows if keep(row)]
            if narrowed:
                rows = narrowed
        return results_table.one_verdict(rows), rows


def verdict_class(word):
    return "missed" if word.lower() in NEGATIVE else "met"


def verdicts_in(text, begin, end):
    """(start, end, word, class, how) of each verdict word in text[begin:end], in order; how is
    "anywhere", "straight" or "tied" (read only where a target is named before it)."""
    found = [(match.start(), match.end(), match.group(0),
              "missed" if match.group(1) else None, "tied")
             for match in TIED_ONLY.finditer(text, begin, end)]
    phrases = [(start, stop) for start, stop, *_ in found]
    for match in ANYWHERE.finditer(text, begin, end):
        if not any(start <= match.start() < stop for start, stop in phrases):
            found.append((match.start(), match.end(), match.group(0),
                          verdict_class(match.group(0)), "anywhere"))
    for match in STRAIGHT.finditer(text, begin, end):
        found.append((match.start(2), match.end(2), match.group(2),
                      verdict_class(match.group(2)), "straight"))
    return sorted(found)


def count_of(word):
    return int(word) if word.isdigit() else COUNTS.index(word.lower())


def check_verdicts(page, lines, verdicts):
    """Each verdict word in prose tied to the targets its sentence names and held to their rows;
    returns the violations and the (line, position) of each count a verdict holds."""
    violations, held = [], set()
    generated = generated_lines(lines)
    in_fence = False
    for number, line in enumerate(lines, start=1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or number in generated:
            continue
        marked = VERDICT_MARK in trailing_marks(line)
        heading = bool(re.match(r"\s*(?:#|<h[1-6]\b)", line))
        text = readable(line)
        row = line.strip().startswith("|")
        spans = [(0, len(text))] if row else [
            (begin, stop) for begin, stop, *_ in
            {(sb, se, 0, 0) for _, _, sb, se in clauses_of(text)}]
        for begin, end in sorted(spans):
            sentence = text[begin:end]
            ids = [(match.start(), match.group(0)) for match in TARGET.finditer(text, begin, end)]
            after, tied = begin, []
            for start, stop, word, kind, how in verdicts_in(text, begin, end):
                named_here = unique(target for position, target in ids if after <= position < start)
                tied = named_here or tied
                after = stop
                if not tied:
                    beside = (BESIDE_TARGET.search(text[begin:start])
                              or BEFORE_TARGET.match(text[stop:end]))
                    if how == "anywhere" and not (marked or heading or beside):
                        violations.append((page, number, f"'{word}' names no target: name its "
                                           "target in the sentence, reword it, or end the line "
                                           f"with {VERDICT_MARK}"))
                    continue
                counted = AFTER_VERDICT.match(text[stop:end]) if kind else None
                for target in tied:
                    verdicts.load()
                    if verdicts.error is not None:
                        violations.append((page, number, f"'{word}' for {target} cannot be "
                                           f"held: {verdicts.error}"))
                        break
                    verdict, rows = verdicts.judge(target, sentence)
                    if verdict == "unknown":
                        violations.append((page, number, f"'{word}' for {target}: no target of "
                                           "BENCHMARKS.md's and no row in the results"))
                        continue
                    if verdict != kind:
                        violations.append((page, number, f"'{word}' for {target}: its result is "
                                           f"{verdict or 'no verdict'} on the rows its sentence "
                                           "names"))
                        continue
                    if counted:
                        said = [count_of(counted.group(1))]
                        have = [sum(row[-1] == kind for row in rows)]
                        if counted.group(2):
                            said.append(count_of(counted.group(2)))
                            have.append(len(rows))
                        if said != have:
                            violations.append((page, number, f"'{word} on "
                                               f"{' of '.join(map(str, said))}' for {target}: its "
                                               f"rows give {' of '.join(map(str, have))}"))
                if counted:
                    held |= {(number, stop + counted.start(group))
                             for group in (1, 2) if counted.group(group)}
    return violations, held


def check_page(root, page, results, verdicts=None):
    text = (root / page).read_text(encoding="utf-8")
    lines = text.splitlines()
    verdicts = Verdicts(root) if verdicts is None else verdicts
    violations, held = check_verdicts(page, lines, verdicts)
    found = [figure for figure in figures_in(text) if (figure.line, figure.position) not in held]
    every = sorted(results)
    by_line = defaultdict(list)
    for figure in found:
        by_line[figure.line].append(figure)
    for number, members in by_line.items():
        cited = {name for figure in members for name in figure.cited or every}
        labels = set()
        for name in cited & set(results):
            labels |= set().union(*results[name].tables.values()) if results[name].tables \
                else set()
        pair_lists(members, lines[number - 1], labels)
        pair_of_of(members, lines[number - 1])
    for figure in found:
        if figure.cited is None and not figure.row:
            violations.append((page, figure.line,
                               f"{figure.figure} is in no results file: its line of prose "
                               "cites none"))
            continue
        names = figure.cited if figure.cited is not None else every
        missing = [name for name in names if name not in results]
        if missing:
            violations.append((page, figure.line,
                               f"{figure.figure} cites {missing[0]}, which does not exist"))
            continue
        line = flat(lines[figure.line - 1])
        if any(held_in(results[name], figure, line) for name in names):
            partner = figure.partner
            if (partner is None or partner.position < figure.position
                    or any(together(results[name], figure, line) for name in names)):
                continue
            where = (f"results/{names[0]}" if len(names) == 1 and figure.cited is not None
                     else "any file it cites" if figure.cited is not None else "any results file")
            violations.append((page, figure.line,
                               f"{figure.figure} of {partner.figure} is not in {where} together, "
                               "in one table or one line"))
            continue
        whose = f" as {figure.label}'s" if figure.label else ""
        if figure.cited is None:
            detail = f"is in no results file{whose} in a place its words name"
        elif len(names) == 1:
            detail = f"is not in results/{names[0]}{whose} in a place its words name"
        else:
            detail = f"is not in any file it cites{whose} in a place its words name"
        violations.append((page, figure.line, f"{figure.figure} {detail}"))
    return len(found), violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--page", action="append", default=[], metavar="PATH")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    pages = [page for page in (args.page or DEFAULT_PAGES) if (root / page).is_file()]
    if args.page and len(pages) != len(args.page):
        print(f"gate_numbers: FAIL a page named is not there: {args.page}")
        return 1
    if not pages:
        print(f"gate_numbers: ok, nothing to gate: none of {', '.join(DEFAULT_PAGES)} exists")
        return 0
    results = load_results(root)
    verdicts = Verdicts(root)
    total = 0
    failed = False
    for page in pages:
        checked, violations = check_page(root, page, results, verdicts)
        total += checked
        for name, line, detail in violations:
            print(f"gate_numbers: FAIL {name}:{line}: {detail}")
            failed = True
    if failed:
        return 1
    print(f"gate_numbers: ok, {total} figures across {len(pages)} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
