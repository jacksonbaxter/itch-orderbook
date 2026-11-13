#!/usr/bin/env python3
"""Every figure in the README, RESULTS.md and the demo page is held to the results file its
line cites, and to a place in that file that its words name (criterion 30).

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
line ending in `<!-- figures: not measured -->`.

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

What it cannot do. It does not read units, so 5.15 M against a cell of 5.15 ms passes. It
reads words, not meaning, so a clause that shares a word with the wrong place by chance passes,
and a clause that names no label is held by any place that shares a word. It does not follow
arithmetic, so a figure the page derives (a sum, a difference, a rounding) must stand as
written in the file. Two figures of one clause that both share its words, with no label or
list to tell them apart, can trade places unseen; the generated tables are held cell by cell
by `results_table.py --check` instead. Which short integers count is decided by the words
next to them, not by what they count, and on a line that cites no results file none is read:
there a short integer is as likely a count of something built (48 scenarios) as a
measurement, so a line whose only numbers are short integers needs no citation.

The gate runs on whichever pages exist, so a tree with none passes and says so. A page
named with --page must exist.

Each violation prints as `gate_numbers: FAIL <page>:<line>: <detail>`.

Exit codes: 0 passed, 1 a figure with no source, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAGES = ("README.md", "RESULTS.md", "docs/index.html")
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
                self.tables[table] |= labels
                for index, cell in enumerate(cells):
                    head = heads[index] if index < len(heads) else ""
                    words = words_of(head) | (words_of(cell) if index else set())
                    place = ("cell", labels, words, table, words_of(cells[0]) | caption,
                             plain(head).strip())
                    for _, figure in tokens_in(cell):
                        self.places[figure].append(place)
                continue
            self.read_line(line)

    def read_line(self, line):
        key = KEY_LINE.match(line)
        given = defaultdict(set)
        for segment in CLAUSE_END.split(line):
            words = words_of(segment)
            text = flat(segment)
            for position, figure in tokens_in(segment):
                self.places[figure].append(("segment", set(), words, None, text[:position], ""))
                before = re.search(r"([\w.'-]+)\W*$", text[:position])
                if key and before:
                    given[before.group(1).strip(".'")].add(figure)
        if key and given:
            self.keyed.append((words_of(key.group(1).replace("_", " ")), given))

    def labels_holding(self, figure):
        """The labels of every row of every table that holds the figure somewhere."""
        tables = {place[3] for place in self.places.get(figure, ()) if place[0] == "cell"}
        return set().union(*(self.tables[table] for table in tables)) if tables else set()


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
    for number, line in enumerate(lines, start=1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or line.rstrip().endswith(SKIP_MARK):
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
            for match in FIGURE.finditer(masked, begin, end):
                sign, digits, unit = match.groups()
                short = not (sign or unit or "." in digits or "," in digits or len(digits) >= 3)
                if short and (not cited_line or named(masked, match.start(2), digits)):
                    continue
                figure = Figure(number, (sign or "") + digits, normal(sign, digits),
                                cited or None, context, row, (begin, end), match.start())
                if row:
                    bars = [index for index, char in enumerate(line) if char == "|"]
                    left = [index for index in bars if index < match.start()]
                    right = [index for index in bars if index > match.start()]
                    start = left[-1] + 1 if left else 0
                    start = max([start] + [index + 1 for index, char in enumerate(line)
                                           if char == ";" and start <= index < match.start()])
                    figure.cell = (start, right[0] if right else len(line))
                    heads = cells_of(head)
                    column = len(left) - (1 if line.lstrip().startswith("|") else 0)
                    if 0 <= column < len(heads):
                        figure.head = plain(heads[column]).strip()
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


def held_in(source, figure, text):
    """Whether one results file holds the figure in a place its clause names."""
    label = figure.label
    if label is not None:
        given = [figures[label] for key, figures in source.keyed
                 if key and key <= figure.context and label in figures]
        if given:
            return any(figure.value in figures for figures in given)
    else:
        label = nearest_label(source, figure, text)
    for kind, labels, words, _, extra, head in source.places.get(figure.value, ()):
        if label is not None:
            if kind == "cell" and label not in labels:
                continue
            if kind == "segment" and not phrase(label).search(extra):
                continue
        elif kind == "cell":
            words = words | extra
        if shares(words, figure.context) or (head and head == figure.head):
            return True
    return False


def check_page(root, page, results):
    text = (root / page).read_text(encoding="utf-8")
    lines = text.splitlines()
    found = list(figures_in(text))
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
    violations = []
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
    total = 0
    failed = False
    for page in pages:
        checked, violations = check_page(root, page, results)
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
