#!/usr/bin/env python3
"""Runs the pinned actionlint over .github/workflows.

    lint_workflows.py [--root DIR] [--tools-dir DIR]

This reads the workflows and runs none of them. It checks their syntax, keys, expressions
and runner labels, which is as far as a repo with no remote can go. shellcheck and pyflakes
stay off, whether or not a host has them, so that every host gives one result.

Each finding prints as actionlint gives it, followed by `lint_workflows: FAIL`.

Exit codes: 0 clean, 1 a finding or no workflow to read, 2 bad usage.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_tool  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=fetch_tool.REPO_ROOT)
    parser.add_argument("--tools-dir", type=Path, default=fetch_tool.DEFAULT_DIR)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    workflows = sorted(path.relative_to(root).as_posix()
                       for path in (root / ".github" / "workflows").glob("*.yml"))
    if not workflows:
        print("lint_workflows: FAIL no workflow under .github/workflows")
        return 1
    try:
        bin_dir = fetch_tool.fetch("actionlint", args.tools_dir.resolve())
    except fetch_tool.FetchError as error:
        print(f"lint_workflows: FAIL {error}")
        return 1
    linted = subprocess.run([str(bin_dir / "actionlint"), "-no-color", "-shellcheck=", "-pyflakes=",
                             *workflows], cwd=root, check=False)
    if linted.returncode != 0:
        print(f"lint_workflows: FAIL actionlint exited {linted.returncode}")
        return 1
    print(f"lint_workflows: ok, {len(workflows)} workflows, actionlint "
          f"{fetch_tool.TOOLS['actionlint'].version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
