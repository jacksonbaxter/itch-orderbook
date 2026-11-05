#!/usr/bin/env python3
"""Tests for flame_report.py: folding a macOS sample tree, the names it keeps, what render
refuses, and that --check catches a results file or SVG that leaks or says too little.
flamegraph.pl is stood in for by a few lines of Perl written here."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flame_report  # noqa: E402

SAMPLE = """\
Analysis of sampling bench_replay (pid 1) every 1 millisecond
Call graph:
    100 Thread_1   DispatchQueue_1: com.apple.main-thread  (serial)
      100 start  (in dyld) + 6652  [0x18283cd7c]
        100 main  (in bench_replay) + 2752  [0x1028a1600]
          90 itch::pass<itch::Book<int, long>>(std::span<std::byte const, 18446744073709551615ul>)  (in bench_replay) + 1  [0x1]
          + 60 itch::Book<int, long>::apply(std::span<std::byte const>)  (in bench_replay) + 528,340,...  [0x2,0x3]
          + ! 25 itch::Book<int, long>::write_level(itch::LevelId const&)  (in bench_replay) + 4  [0x4]
          + 20 itch::Book<int, long>::apply(std::span<std::byte const>)  (in bench_replay) + 452  [0x5]
          10 itch::count_mix(std::span<std::byte const>) const  (in bench_replay) + 9  [0x6]

Total number in stack (recursive counted multiple, when >=5):
"""
STUB = r"""
my $total = 0;
while (<STDIN>) { $total += $1 if /\s(\d+)$/; print "<g><title>$_</title></g>\n"; }
1 while $total =~ s/^(\d+)(\d{3})/$1,$2/;
print "<title>all ($total samples, 100%)</title>\n";
"""
PLANTED = "planted" + "-host"  # In two pieces, so that the private-name gate does not see it.
FP_FLAGS = "-O3 -DNDEBUG -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -g -std=c++23"


def header(host="debian", flags=FP_FLAGS):
    return {"host": host, "cpu": "a CPU", "kernel": "Linux 6.1" if host == "debian" else "Darwin",
            "compiler": "Clang 23", "flags": flags, "commit": "0123456789ab (clean)",
            "pinning": "pinned to core 4"}


def driver_file(median, flags):
    return (f"# Book design\n\n## Header\n\n- flags: {flags}\n- commit: 0123456789ab (exported)\n"
            "- repetitions: 3\n\n## Summary\n\n| Measure | Median | Min | Max | CV |\n"
            f"|---|---|---|---|---|\n| Throughput, messages/s | {median:,} | {median - 9:,} "
            f"| {median + 9:,} | 0.20% |\n")


class FlameReport(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parent.parent / "build"
        base.mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.scratch.name)
        self.stub = self.root / "flamegraph.pl"
        self.stub.write_text(STUB, encoding="utf-8")
        (self.root / "notes.md").write_text("It shows the book.\n", encoding="utf-8")
        self.saved = os.environ.get("ITCH_PRIVATE_NAMES")

    def tearDown(self):
        self.scratch.cleanup()
        if self.saved is None:
            os.environ.pop("ITCH_PRIVATE_NAMES", None)
        else:
            os.environ["ITCH_PRIVATE_NAMES"] = self.saved

    def render(self, host="debian", flags=FP_FLAGS, folded="main;apply 70\nmain;walk 30\n",
               extra=()):
        (self.root / "stacks.folded").write_text(folded, encoding="utf-8")
        (self.root / "run.json").write_text(json.dumps({"header": header(host, flags)}),
                                            encoding="utf-8")
        (self.root / "annotate.txt").write_text(" 40.00 :   mov (%rax),%rdx\n",
                                                encoding="utf-8")
        (self.root / "normal.md").write_text(driver_file(5_000_000, "-O3 -DNDEBUG"),
                                             encoding="utf-8")
        (self.root / "fp.md").write_text(driver_file(4_950_000, FP_FLAGS), encoding="utf-8")
        out = self.root / "results" / ("flamegraph_x_debian_12302019.md" if host == "debian"
                                       else "profile_x_mac_12302019.md")
        args = ["render", "--folded", str(self.root / "stacks.folded"), "--flamegraph",
                str(self.stub), "--record", str(self.root / "run.json"), "--source",
                "perf" if host == "debian" else "sample", "--workload", "replay", "--day",
                "12302019", "--command", "perf record -g", "--prediction", "P-0018", "--notes",
                str(self.root / "notes.md"), "--out", str(out), *extra]
        if host == "debian":
            args += ["--run-id", "P-0018-20261105T090000Z"]
            args += ["--annotate", str(self.root / "annotate.txt"), "--annotate-symbol", "apply",
                     "--normal", str(self.root / "normal.md"), "--frame-pointer",
                     str(self.root / "fp.md")]
        return flame_report.main(args), out

    def codes(self, path):
        return [code for code, _ in flame_report.check_file(path)]

    def test_a_sample_tree_folds_to_each_frames_own_samples(self):
        stacks = flame_report.fold_sample(SAMPLE)
        path = "Thread_1;start;main;itch::pass<itch::Book<int, long>>"
        self.assertEqual(stacks[f"{path};itch::Book<int, long>::apply"], 35 + 20)
        self.assertEqual(stacks[f"{path};itch::Book<int, long>::apply;"
                                "itch::Book<int, long>::write_level"], 25)
        self.assertEqual(stacks[path], 10)
        self.assertEqual(stacks["Thread_1;start;main;itch::count_mix"], 10)
        self.assertEqual(sum(stacks.values()), 100)

    def test_perf_script_folds_one_count_a_sample_with_whole_names(self):
        home = "/" + "home" + "/someone/build/bench_replay"
        text = (
            "bench_replay  1 2.0:     500 cycles:u: \n"
            "\t    55b4 int (anonymous namespace)::run<a::B<int>, (anonymous namespace)::f(int)"
            "::$_0>((anonymous namespace)::Options const&)+0x1db (" + home + ")\n"
            "\t    55b5 main+0x847 (" + home + ")\n"
            "\t    7fc2 [unknown] (/usr/lib/libc.so.6)\n\n"
            "bench_replay  1 2.1:     900 cycles:u: \n"
            "\t    55b5 main+0x84 (" + home + ")\n"
            "\t    7fc2 [unknown] (/usr/lib/libc.so.6)\n\n")
        stacks = flame_report.fold_perf(text.splitlines(keepends=True))
        self.assertEqual(stacks["bench_replay;[libc.so.6];main;(anonymous namespace)::run<a::B<"
                                "int>, (anonymous namespace)::f(int)::$_0>"], 1)
        self.assertEqual(stacks["bench_replay;[libc.so.6];main"], 1)
        self.assertEqual(sum(stacks.values()), 2)

    @unittest.skipUnless(shutil.which("llvm-cxxfilt") or shutil.which("c++filt"),
                         "no demangler on PATH")
    def test_a_name_perf_left_mangled_is_demangled(self):
        text = "bench_backtest  1 2.0:  5 cycles:u: \n\t    55b4 _ZN4itch3runEv+0x1 (/x/b)\n\n"
        self.assertEqual(dict(flame_report.fold_perf(text.splitlines(keepends=True))),
                         {"bench_backtest;itch::run": 1})

    def test_names_lose_their_parameters_and_keep_their_templates(self):
        self.assertEqual(flame_report.shorten("a::f<int>(std::span<int>) const"), "a::f<int>")
        self.assertEqual(flame_report.shorten("(anonymous namespace)::run(int, char**)"),
                         "(anonymous namespace)::run")
        self.assertEqual(flame_report.shorten("operator()"), "operator()")
        self.assertEqual(flame_report.brief("a::B<c::D<1>, e>::f"), "a::B<…>::f")

    def test_a_debian_graph_renders_and_passes_its_own_check(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("- samples: 100", text)
        self.assertIn("- frame_pointer_ratio: 0.9900", text)
        self.assertIn("- build: frame pointers", text)
        self.assertEqual(self.codes(out), [])

    def test_a_perf_graph_from_a_build_without_frame_pointers_is_refused(self):
        code, out = self.render(flags="-O3 -DNDEBUG")
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_render_scrubs_home_paths_and_private_names(self):
        os.environ["ITCH_PRIVATE_NAMES"] = PLANTED
        home = "/" + "home" + "/someone/lib.so"
        code, out = self.render(folded=f"main;{home};{PLANTED} 5\nmain;apply 5\n")
        self.assertEqual(code, 0)
        both = out.read_text(encoding="utf-8") + out.with_suffix(".svg").read_text("utf-8")
        self.assertNotIn("someone", both)
        self.assertNotIn(PLANTED, both)

    def test_check_catches_a_private_name_in_the_svg(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        svg = out.with_suffix(".svg")
        svg.write_text(svg.read_text(encoding="utf-8") + f"<title>{PLANTED}</title>\n",
                       encoding="utf-8")
        os.environ["ITCH_PRIVATE_NAMES"] = PLANTED
        self.assertIn("private-text", self.codes(out))

    def test_check_catches_a_sample_count_that_differs(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        out.write_text(out.read_text(encoding="utf-8").replace("- samples: 100", "- samples: 99"),
                       encoding="utf-8")
        self.assertIn("samples-differ", self.codes(out))

    def test_check_catches_the_limits_dropped(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        out.write_text(out.read_text(encoding="utf-8").replace("## What it cannot show", "## X"),
                       encoding="utf-8")
        self.assertIn("limits-unstated", self.codes(out))

    def test_check_catches_a_debian_graph_without_its_build_or_delta(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        out.write_text(text.replace("- build: frame pointers", "- build: normal"), "utf-8")
        self.assertIn("build-unnamed", self.codes(out))
        out.write_text(text.replace("- frame_pointer_ratio:", "- ratio:"), "utf-8")
        self.assertIn("delta-missing", self.codes(out))
        out.write_text(text.replace("## perf annotate, the hottest function", "## X"), "utf-8")
        self.assertIn("annotate-missing", self.codes(out))
        out.write_text(text.replace("- run_id:", "- run:"), "utf-8")
        self.assertIn("run-id-missing", self.codes(out))

    def test_a_macos_profile_says_it_does_not_replace_perf(self):
        code, out = self.render(host="macOS (M4 Max)", flags="-O3 -DNDEBUG")
        self.assertEqual(code, 0)
        self.assertEqual(self.codes(out), [])
        text = out.read_text(encoding="utf-8")
        out.write_text(text.replace("does not replace the `perf` data", "is"), "utf-8")
        self.assertIn("host-unsaid", self.codes(out))


if __name__ == "__main__":
    unittest.main()
