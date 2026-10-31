#!/usr/bin/env python3
"""Every figure in the README and the demo page appears in a results file (criterion 30).

    gate_numbers.py [--root DIR] [--page PATH]...

A line that cites `results/<name>.md` is held to that file, and any other line to every
results file. predictions.md and run_log.md are not results: a figure predicted is not a
figure measured. A figure is a number with a decimal point, a thousands separator, three
or more digits, or a unit. Code spans, code blocks, URLs, HTML tags, dates and the names in
EXEMPT are left out, and so is any line ending in `<!-- figures: not measured -->`.

The gate runs on whichever pages exist, so a tree with neither passes and says so.

Each violation prints as `gate_numbers: FAIL <page>:<line>: <detail>`.

Exit codes: 0 passed, 1 a figure with no source, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAGES = ("README.md", "docs/index.html")
NOT_RESULTS = {"predictions.md", "run_log.md"}
EXEMPT = ("ITCH 5.0", "C++23", "BinaryFILE 1.0")
SKIP_MARK = "<!-- figures: not measured -->"

FENCE = re.compile(r"^(```|~~~)")
STRIPPED = re.compile(
    r"`[^`]*`|\]\([^)]*\)|https?://\S+|<script\b.*?</script>|<style\b.*?</style>|<[^>]*>"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b(?:phase|step|criterion) \d+\b", re.IGNORECASE | re.DOTALL)
UNIT = r"(?:%|×|\s?(?:ns|µs|us|ms|s|GB|GiB|MB|MiB|KB|KiB|TB|GHz|MHz|M|K|B|x)\b)"
FIGURE = re.compile(rf"(?<![\w.,/-])(\d[\d,]*(?:\.\d+)?)({UNIT}?)(?![\w%]|[.,]\d)")
CITATION = re.compile(r"results/([\w.-]+\.md)")
TOKEN = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?(?![\d]|[.,]\d)")


def is_figure(digits, unit):
    return bool(unit) or "." in digits or "," in digits or len(digits) >= 3


def normal(digits):
    return digits.replace(",", "")


def tokens_of(text):
    return {normal(match) for match in TOKEN.findall(text)}


def load_results(root):
    return {path.name: tokens_of(path.read_text(encoding="utf-8"))
            for path in sorted((root / "results").glob("*.md")) if path.name not in NOT_RESULTS}


def figures_in(text):
    """(line number, figure, citation or None) for every figure on the page."""
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence or line.rstrip().endswith(SKIP_MARK):
            continue
        cited = CITATION.search(line)
        for name in EXEMPT:
            line = line.replace(name, " ")
        prose = STRIPPED.sub(" ", line)
        for match in FIGURE.finditer(prose):
            if is_figure(match.group(1), match.group(2)):
                yield number, match.group(1), cited.group(1) if cited else None


def check_page(root, page, results):
    text = (root / page).read_text(encoding="utf-8")
    checked = 0
    violations = []
    for number, figure, cited in figures_in(text):
        checked += 1
        if cited is not None:
            if cited not in results:
                violations.append((page, number, f"{figure} cites {cited}, which does not exist"))
            elif normal(figure) not in results[cited]:
                violations.append((page, number, f"{figure} is not in results/{cited}"))
        elif not any(normal(figure) in tokens for tokens in results.values()):
            violations.append((page, number, f"{figure} is in no results file"))
    return checked, violations


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
