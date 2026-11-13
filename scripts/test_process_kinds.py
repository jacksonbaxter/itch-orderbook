#!/usr/bin/env python3
"""process_kinds.py: a process by its kind through a map kept outside the repository, and
another process with none. Every program named here is invented."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import process_kinds  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SCRATCH = SCRIPTS.parent / "build"
# Every tool that gives another process by its kind, through kind_of, into a row or a file.
WRITERS = ("alloc_driver", "backtest_driver", "bench_driver", "simd_asm_audit", "simd_driver",
           "simd_report", "spsc_driver", "stamp_load", "variants_report", "wasm_bench",
           "wasm_report")
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


class UnusableMap(unittest.TestCase):
    """A map that is set and cannot be used stops the tool before it writes a row, and no
    message gives the map's path (r14-3)."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH, prefix="secret-folder-")
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)

    def unusable(self):
        """(what, path) for each way a map can be unusable."""
        found = [("missing", self.dir / "missing-kinds.json"), ("a folder", self.dir)]
        for what, text in (("not JSON", "{"), ("a list", "[1, 2]"),
                           ("programs not a map", '{"programs": [1]}'),
                           ("a bad pattern", '{"patterns": [["(", "a backup tool"]]}'),
                           ("a kind outside KINDS", '{"programs": {"frobd": "a frob daemon"}}'),
                           ("a kind not a string", '{"programs": {"frobd": ["a"]}}')):
            path = self.dir / f"kinds-{len(found)}.json"
            path.write_text(text, encoding="utf-8")
            found.append((what, path))
        return found

    def assert_no_path(self, text):
        self.assertNotIn("secret-folder-", text)
        self.assertNotIn("kinds", text.replace("process_kinds", "").replace("name-to-kind", ""))

    def test_an_unusable_map_is_no_refusal_and_gives_no_path(self):
        for what, path in self.unusable():
            with self.subTest(what=what):
                with self.assertRaises(process_kinds.MapError) as caught:
                    process_kinds.load(path)
                self.assertNotIsInstance(caught.exception, ValueError)
                self.assert_no_path(str(caught.exception))

    def test_kind_of_with_an_unusable_map_stops_and_raises_nothing_a_tool_catches(self):
        for what, path in self.unusable():
            with self.subTest(what=what), process_map(path):
                process_kinds._LOADED.pop(str(path), None)
                with self.assertRaises(SystemExit) as caught, \
                        contextlib.redirect_stderr(io.StringIO()):
                    process_kinds.kind_of("/usr/local/bin/frobd")
                self.assertEqual(caught.exception.code, 2)

    def test_every_writer_stops_before_it_writes_anything(self):
        run_log = self.dir / "run_log.md"
        env = dict(os.environ, **{process_kinds.MAP_ENV: str(self.dir / "missing-kinds.json")})
        for tool in WRITERS:
            with self.subTest(tool=tool):
                done = subprocess.run(
                    [sys.executable, "-B", str(SCRIPTS / f"{tool}.py"), "--run-log",
                     str(run_log)], capture_output=True, text=True, env=env, timeout=60,
                    check=False)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(f"{tool}: {process_kinds.MAP_ENV}: the name-to-kind map it gives "
                              "cannot be read", done.stderr)
                self.assert_no_path(done.stderr + done.stdout)
                self.assertFalse(run_log.exists())

    def test_stamp_load_writes_no_row_for_a_busy_run_with_an_unusable_map(self):
        import stamp_load  # noqa: PLC0415
        lines = "\n".join(["2026-11-05T09:00:00Z sample", "2026-11-05T09:00:02Z sample",
                           "2026-11-05T09:00:02Z FOREIGN 31.0 /opt/fixture/frobd --all",
                           "2026-11-05T09:00:04Z sample", "2026-11-05T09:00:06Z sample"])
        with process_map(self.dir / "missing-kinds.json"), self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            process_kinds._LOADED.pop(str(self.dir / "missing-kinds.json"), None)
            stamp_load.sampler_line(lines, "2026-11-05T09:00:01Z", "2026-11-05T09:00:05Z")


if __name__ == "__main__":
    unittest.main()
