#!/usr/bin/env python3
"""Tests of check_real_day.sh's handling of each tool's exit status, with no data/.

The tools here are stand-ins in a build tree of their own: each prints a line and exits
with the status a test gives it. cmake is a stand-in too, so nothing is built. The day is a
real key of the manifest, since the script reads the manifest, but no file of it is read.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_real_day.sh"
SCRATCH = REPO_ROOT / "build"
SCRATCH.mkdir(exist_ok=True)
DAY = "12302019"
TOOLS = ("itch_census", "itch_oracle", "itch_alloc", "itch_trades", "itch_map_compare")


def executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class CheckRealDay(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.build = root / "build"
        self.logs = root / "logs"
        self.results = root / "results"
        bin_dir = root / "bin"
        for folder in (self.build / "tools", bin_dir):
            folder.mkdir(parents=True)
        executable(bin_dir / "cmake", "#!/bin/sh\nexit 0\n")
        compiler = root / "fake-clang++"
        executable(compiler, "#!/bin/sh\necho 'clang version 99.1.0 (stand-in)'\n")
        other = root / "other-c++"
        executable(other, "#!/bin/sh\necho 'g++ (Other) 1.0'\n")
        self.other = other
        (self.build / "CMakeCache.txt").write_text(
            f"CMAKE_BUILD_TYPE:STRING=Release\nCMAKE_CXX_COMPILER:FILEPATH={compiler}\n",
            encoding="utf-8")
        (self.build / "compile_commands.json").write_text(json.dumps([
            {"file": f"/x/tools/{tool}.cpp", "command": f"c++ -O3 -c /x/tools/{tool}.cpp"}
            for tool in TOOLS]), encoding="utf-8")
        for tool in TOOLS:
            variable = f"FAKE_{tool.upper()}_EXIT"
            executable(self.build / "tools" / tool,
                       f"#!/bin/sh\necho 'stand-in {tool}'\nexit ${{{variable}:-0}}\n")
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                        ITCH_REAL_DAY_BUILD=str(self.build), ITCH_REAL_DAY_LOGS=str(self.logs),
                        ITCH_REAL_DAY_RESULTS=str(self.results), CXX=str(other))
        self.env.pop("ITCH_HEAVY_WRAPPER", None)

    def run_day(self, check, **exits):
        env = dict(self.env)
        for tool, status in exits.items():
            env[f"FAKE_{tool.upper()}_EXIT"] = str(status)
        return subprocess.run(["bash", str(SCRIPT), "--only", check, DAY], cwd=REPO_ROOT,
                              env=env, capture_output=True, text=True, check=False)

    def kept(self, name):
        return (self.logs / f"{name}.exit").read_text(encoding="utf-8").strip()

    def test_a_tool_that_did_not_finish_fails_the_day(self):
        done = self.run_day("map", itch_map_compare=0)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"real day: {DAY} ok", done.stdout)
        self.assertEqual(self.kept(f"map_{DAY}_fresh"), "0")
        done = self.run_day("map", itch_map_compare=2)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"real day: {DAY} FAILED", done.stdout)
        self.assertIn("itch_map_compare exited 2, which is no verdict", done.stderr)
        self.assertEqual(self.kept(f"map_{DAY}_aged"), "2")

    def test_each_oracle_and_alloc_exit_is_kept_and_judged(self):
        done = self.run_day("oracle", itch_oracle=3, itch_alloc=1)
        self.assertNotIn("which is no verdict", done.stderr)
        self.assertIn(f"real day: {DAY} default itch_oracle exited 3", done.stdout)
        self.assertEqual(self.kept(f"oracle_{DAY}_default"), "3")
        self.assertEqual(self.kept(f"alloc_{DAY}_default"), "1")
        for tool, status in (("itch_oracle", 2), ("itch_oracle", 139), ("itch_alloc", 2)):
            done = self.run_day("oracle", **{tool: status})
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn(f"{tool} exited {status}, which is no verdict", done.stderr)
            self.assertIn(f"real day: {DAY} FAILED", done.stdout)

    def test_the_compiler_is_the_build_trees(self):
        self.run_day("oracle")
        header = (self.logs / f"oracle_{DAY}_default.log").read_text(encoding="utf-8")
        self.assertIn("run_compiler clang version 99.1.0\n", header)
        self.assertNotIn("Other", header)
        self.assertIn("run_flags -O3\n", header)


if __name__ == "__main__":
    sys.exit(unittest.main())
