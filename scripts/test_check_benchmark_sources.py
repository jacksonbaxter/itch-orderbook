#!/usr/bin/env python3
"""Tests for check_benchmark_sources.py that need no network: the arguments it refuses and
the needles that must say what the claim says.

    python3 -B scripts/test_check_benchmark_sources.py
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_benchmark_sources  # noqa: E402

NEEDLES = {target: needles for target, _, needles in check_benchmark_sources.CHECKS}


class Arguments(unittest.TestCase):
    def test_an_unknown_only_is_refused_before_anything_is_fetched(self):
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch) / "cache"
            run = subprocess.run(
                [sys.executable, "-I", str(HERE / "check_benchmark_sources.py"), "--cache",
                 str(cache), "--log", str(Path(scratch) / "log.txt"), "--only", "T-NOPE"],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("--only T-NOPE: no source is checked for that ID", run.stderr)
            self.assertIn("T-WASM-1", run.stderr)
            self.assertFalse(cache.exists(), "it fetched before it refused")
            self.assertFalse((Path(scratch) / "log.txt").exists())


class Needles(unittest.TestCase):
    """A bare number or a single word is found in many sources; each of these needles names
    what its figure is, in the source's words."""

    def test_the_wasm_slowdowns_are_named_with_their_browser(self):
        needles = NEEDLES["T-WASM-1"]
        self.assertIn("WebAssembly runs on average 1.55× slower in Chrome", needles)
        self.assertIn("and 1.45× slower in Firefox than native code", needles)
        self.assertIn("Slowdown: geomean – 1.55× 1.45×", needles)

    def test_the_decomposition_is_named_whole(self):
        needles = NEEDLES["ctx-menkveld"]
        self.assertTrue(any("decomposition of its trading profit into a spread and a ‘positioning’"
                            in needle for needle in needles), needles)
        self.assertTrue(any("positioning profit and net spread" in needle for needle in needles),
                        needles)

    def test_the_memory_rate_is_on_the_v1605b_row(self):
        needles = NEEDLES["ctx-v1605b"]
        self.assertTrue(any(needle.startswith("V1605B ") and " 2.0 " in needle
                            and " 2400 " in needle for needle in needles), needles)
        self.assertIn("(MT/s)", needles)

    def test_no_needle_for_these_claims_is_a_bare_number_or_word(self):
        for target in ("T-WASM-1", "ctx-menkveld"):
            for needle in NEEDLES[target]:
                with self.subTest(target=target, needle=needle):
                    self.assertGreaterEqual(len(needle.split()), 5)
        row = [n for n in NEEDLES["ctx-v1605b"] if n.startswith("V1605B")]
        self.assertEqual(len(row), 1)
        self.assertGreaterEqual(len(row[0].split()), 10)


if __name__ == "__main__":
    unittest.main()
