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
        out = self.root / "results" / ("flamegraph_x_debian_12302019.md" if host == "debian"
                                       else "profile_x_mac_12302019.md")
        # The two builds' files sit beside the graph, as in results/, where its table links them.
        out.parent.mkdir(exist_ok=True)
        (out.parent / "normal.md").write_text(driver_file(5_000_000, "-O3 -DNDEBUG"),
                                              encoding="utf-8")
        (out.parent / "fp.md").write_text(driver_file(4_950_000, FP_FLAGS), encoding="utf-8")
        args = ["render", "--folded", str(self.root / "stacks.folded"), "--flamegraph",
                str(self.stub), "--record", str(self.root / "run.json"), "--source",
                "perf" if host == "debian" else "sample", "--workload", "replay", "--day",
                "12302019", "--command", "perf record -g", "--prediction", "P-0018", "--notes",
                str(self.root / "notes.md"), "--out", str(out), *extra]
        if host == "debian":
            args += ["--run-id", "P-0018-20261105T090000Z"]
            args += ["--annotate", str(self.root / "annotate.txt"), "--annotate-symbol", "apply",
                     "--normal", str(out.parent / "normal.md"), "--frame-pointer",
                     str(out.parent / "fp.md")]
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

    def test_instantiations_printed_alike_are_ranked_apart_and_the_merge_is_said(self):
        # Sixteen frames of 10 self each, then two instantiations of pass<> at 6 and 5:
        # each is out of the top 15, merged at 11 they would rank first, and f14 would drop.
        folded = "".join(f"main;f{i:02d} 10\n" for i in range(16))
        folded += "main;pass<A> 6\nmain;pass<B> 5\nmain;pass<B>;g<A> 4\nmain;g<B> 3\n"
        stacks = flame_report.read_folded(folded)
        frames, total = flame_report.hottest(stacks)
        self.assertEqual(total, 178)
        self.assertNotIn("pass<A>", [frame for frame, _, _ in frames])
        lines = flame_report.alike_lines(stacks)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1], "`pass<…>` has 2 instantiations, ranked apart at 3.37% and "
                                   "2.81% self, neither in the list; merged, at 6.18% self and "
                                   "8.43% total, it would rank 1st and `f14` would drop out of "
                                   "the list.")
        code, out = self.render(folded=folded)
        self.assertEqual(code, 0)
        self.assertIn(f"- {lines[1]}", out.read_text(encoding="utf-8"))
        self.assertEqual(flame_report.alike_lines(flame_report.read_folded("main;a<x> 5\n")), [])

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

    def test_check_works_the_shares_out_again_from_the_folded_stacks(self):
        code, out = self.render(folded="main;apply 70\nmain;walk 20\nmain;apply;walk 10\n")
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        folded = out.with_suffix(".folded")
        self.assertEqual(folded.read_text(encoding="utf-8"),
                         "main;apply 70\nmain;apply;walk 10\nmain;walk 20\n")
        self.assertIn("| `apply` | 70.00% | 80.00% |", text)
        out.write_text(text.replace("| `apply` | 70.00% | 80.00% |",
                                    "| `apply` | 70.00% | 85.00% |"), "utf-8")
        self.assertEqual(self.codes(out), ["figures-differ"])
        out.write_text(text.replace("at 70.00% self", "at 75.00% self"), "utf-8")
        self.assertEqual(self.codes(out), ["figures-differ"])
        out.write_text(text, "utf-8")
        folded.write_text("main;apply 60\nmain;apply;walk 10\nmain;walk 30\n", "utf-8")
        self.assertIn("figures-differ", self.codes(out))
        folded.unlink()
        self.assertEqual(self.codes(out), ["folded-missing"])

    def test_check_works_the_ratio_out_again_from_the_two_builds(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        for old, new in (("- frame_pointer_ratio: 0.9900", "- frame_pointer_ratio: 0.9990"),
                         ("ran at 0.9900 times", "ran at 0.9990 times"),
                         ("| 4,950,000 |", "| 4,995,000 |")):
            out.write_text(text.replace(old, new), "utf-8")
            self.assertIn("figures-differ", self.codes(out), old)
        out.write_text(text, "utf-8")
        fp = out.parent / "fp.md"
        fp.write_text(driver_file(4_995_000, FP_FLAGS), encoding="utf-8")
        self.assertIn("figures-differ", self.codes(out))
        fp.unlink()
        self.assertIn("delta-unreadable", self.codes(out))

    def test_every_frame_the_svg_draws_is_a_node_of_the_folded_stacks(self):
        stacks = flame_report.parse_folded("main;a<b> 70\nmain;walk 20\nmain;a<b>;walk 10\n")
        drawn = ("<title>all (100 samples, 100%)</title><title>main (100 samples, 100%)</title>"
                 "<title>a&lt;b&gt; (80 samples, 80.00%)</title>"
                 "<title>walk (20 samples, 20.00%)</title><title>walk (10 samples, 10.00%)</title>")
        self.assertEqual(flame_report.svg_mismatches(drawn, stacks), [])
        self.assertEqual(flame_report.svg_mismatches(drawn.replace("(80 samples", "(81 samples"),
                                                     stacks), ["a<b> (81 samples)"])
        self.assertEqual(flame_report.svg_mismatches(drawn + "<title>walk (20 samples, 20.00%)"
                                                     "</title>", stacks), ["walk (20 samples)"])

    def test_check_catches_a_private_name_in_the_folded_stacks(self):
        code, out = self.render()
        self.assertEqual(code, 0)
        folded = out.with_suffix(".folded")
        folded.write_text(folded.read_text(encoding="utf-8") + f"main;{PLANTED} 1\n", "utf-8")
        os.environ["ITCH_PRIVATE_NAMES"] = PLANTED
        self.assertIn("private-text", self.codes(out))

    def test_a_macos_profile_says_it_does_not_replace_perf(self):
        code, out = self.render(host="macOS (M4 Max)", flags="-O3 -DNDEBUG")
        self.assertEqual(code, 0)
        self.assertEqual(self.codes(out), [])
        text = out.read_text(encoding="utf-8")
        out.write_text(text.replace("does not replace the `perf` data", "is"), "utf-8")
        self.assertIn("host-unsaid", self.codes(out))


if __name__ == "__main__":
    unittest.main()
