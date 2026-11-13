#!/usr/bin/env python3
"""process_kinds.py: a process by its kind through a map kept outside the repository, and
another process with none. Every program named here is invented."""

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import process_kinds  # noqa: E402

SCRATCH = Path(__file__).resolve().parent.parent / "build"
SCRATCH.mkdir(exist_ok=True)


@contextlib.contextmanager
def process_map(path):
    """$ITCH_PROCESS_KINDS set to path, or unset for None, for the block."""
    before = os.environ.get(process_kinds.MAP_ENV)
    if path is None:
        os.environ.pop(process_kinds.MAP_ENV, None)
    else:
        os.environ[process_kinds.MAP_ENV] = str(path)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(process_kinds.MAP_ENV, None)
        else:
            os.environ[process_kinds.MAP_ENV] = before


MAP = {"programs": {"frobd": process_kinds.BACKUP_TOOL, "quux-helper": process_kinds.BROWSER},
       "prefixes": [["/opt/vendor/", process_kinds.MACOS_DAEMON]],
       "patterns": [["^zork_", process_kinds.OTHER_PROJECT_TESTS],
                    ["^kfrob/", process_kinds.KERNEL_THREAD]]}


class Kinds(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "kinds.json"
        self.path.write_text(json.dumps(MAP), encoding="utf-8")

    def test_a_map_gives_each_program_its_kind(self):
        kinds = process_kinds.load(self.path)
        for command, kind in (("/usr/local/bin/frobd --all", process_kinds.BACKUP_TOOL),
                              ("(frobd)", process_kinds.BACKUP_TOOL),
                              ("/Applications/Quux.app/Contents/MacOS/quux-helper -x",
                               process_kinds.BROWSER),
                              ("/opt/vendor/bin/thing", process_kinds.MACOS_DAEMON),
                              ("build/bin/zork_unit_tests --filter=a", process_kinds.OTHER_PROJECT_TESTS),
                              ("kfrob/3:1", process_kinds.KERNEL_THREAD),
                              ("/opt/other/unknown_job", process_kinds.OTHER),
                              ("", process_kinds.OTHER)):
            with self.subTest(command=command):
                self.assertEqual(process_kinds.kind_of(command, kinds), kind)

    def test_with_no_map_every_process_is_another_process(self):
        with process_map(None):
            self.assertEqual(process_kinds.kind_of("/usr/local/bin/frobd"), process_kinds.OTHER)
        with process_map(self.path):
            self.assertEqual(process_kinds.kind_of("/usr/local/bin/frobd"),
                             process_kinds.BACKUP_TOOL)

    def test_a_map_with_a_kind_outside_the_fixed_set_is_refused(self):
        self.path.write_text(json.dumps({"programs": {"frobd": "a frob daemon"}}),
                             encoding="utf-8")
        with self.assertRaisesRegex(process_kinds.MapError, "not in process_kinds.KINDS"):
            process_kinds.load(self.path)

    def test_a_reason_written_with_a_name_is_given_by_kind(self):
        kinds = process_kinds.load(self.path)
        said = "the CPU sampler saw {} at 31.0% CPU at 2026-11-06T03:16:50Z"
        self.assertEqual(process_kinds.reworded(said.format("frobd"), kinds),
                         said.format(process_kinds.BACKUP_TOOL))
        self.assertEqual(process_kinds.reworded(said.format("zork_x"), kinds),
                         said.format(process_kinds.OTHER_PROJECT_TESTS))
        for kind in (process_kinds.OTHER, process_kinds.MACOS_DAEMON):
            self.assertEqual(process_kinds.reworded(said.format(kind), kinds), said.format(kind))

    def test_every_kind_passes_the_run_log_grammar(self):
        for kind in process_kinds.KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(process_kinds.cause_breaks(
                    f"withdrawn: the CPU sampler saw {kind} at 31.0% CPU at 2026-11-06T03:16:50Z"),
                    [])


if __name__ == "__main__":
    unittest.main()
