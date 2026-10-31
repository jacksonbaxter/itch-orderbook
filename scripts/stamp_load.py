#!/usr/bin/env python3
"""Stamps a run's measure.sh load lines into its results file.

    stamp_load.py --results FILE --stderr FILE [--out FILE]

measure.sh prints its end line after the benchmark exits, so the file is finished from
outside the lock, from the stderr that measure.sh wrote:

    measure.sh bench_driver.py ... 2> STDERR
    stamp_load.py --results RESULTS --stderr STDERR

The lines are copied, never written from nothing, and refused if they are missing, doubled,
or do not bracket the run. measure.sh's start line names the machine's hostname, which
never goes into a repo, so that one word is replaced by the header's fixed host label.
If `/usr/bin/time -l` wrapped the run, its peak memory lines are stamped in as well.

Exit codes: 0 stamped and the file passes check_results.py, 1 refused, 2 bad usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402

START = re.compile(r"^measure\.sh: start .*$", re.MULTILINE)
END = re.compile(r"^measure\.sh: end .*$", re.MULTILINE)
HOST = re.compile(r"\bhost=\S+")
MAX_RSS = re.compile(r"^\s*(\d+)\s+maximum resident set size$", re.MULTILINE)
FOOTPRINT = re.compile(r"^\s*(\d+)\s+peak memory footprint$", re.MULTILINE)


def gib(bytes_text):
    return f"{int(bytes_text) / 1024 ** 3:.2f} GiB"


def one_line(pattern, text, name):
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise ValueError(f"wanted one measure.sh {name} line, found {len(matches)}")
    return matches[0]


def stamp(results_text, stderr_text):
    """The results text with the lines stamped in. Raises ValueError to refuse."""
    fields = check_results.parse_header(results_text)
    host = fields.get("host")
    if host is None:
        raise ValueError("the results file has no host field")
    start = HOST.sub(f"host={host}", one_line(START, stderr_text, "start"))
    end = HOST.sub(f"host={host}", one_line(END, stderr_text, "end"))

    pending = check_results.PENDING
    for name, line in (("measure_load_start", start), ("measure_load_end", end)):
        old = f"- {name}: {pending}"
        if old not in results_text:
            raise ValueError(f"{name} is not pending in the results file")
        results_text = results_text.replace(old, f"- {name}: {line}")
    for name, pattern, note in (("peak_rss", MAX_RSS, ", the largest process in the run"),
                                ("peak_footprint", FOOTPRINT,
                                 ", of the driver alone: time sees its direct child")):
        match = pattern.search(stderr_text)
        if match:
            old = f"- {name}: unavailable: /usr/bin/time did not wrap the run"
            results_text = results_text.replace(old, f"- {name}: {gib(match.group(1))}{note}")
    return results_text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="default: stamp --results in place")
    args = parser.parse_args(argv)
    try:
        stamped = stamp(args.results.read_text(encoding="utf-8"),
                        args.stderr.read_text(encoding="utf-8", errors="replace"))
    except ValueError as error:
        print(f"stamp_load: refused: {error}", file=sys.stderr)
        return 1
    violations = check_results.check_text(stamped)
    for code, detail in violations:
        print(f"stamp_load: FAIL {code}: {detail}", file=sys.stderr)
    if violations:
        return 1
    target = args.out or args.results
    target.write_text(stamped, encoding="utf-8")
    print(f"stamp_load: stamped {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
