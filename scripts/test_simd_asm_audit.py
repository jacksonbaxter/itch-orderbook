#!/usr/bin/env python3
"""Tests for simd_asm_audit.py, on compiler output written here: how it cuts functions out of
-S output and counts their instructions on each target, how it judges E12, and that --check
catches a published file whose table or finding is not what its listings give."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import simd_asm_audit  # noqa: E402
import test_simd_report  # noqa: E402

ARM = """\
_e12_scalar_add_fields:                 ; @e12_scalar_add_fields
\t.cfi_startproc
; %bb.0:
\tldur\tx8, [x0, #11]
\trev\tx8, x8
Lloh0:
\tldr\tq3, [x8, lCPI0_0@PAGEOFF]
\ttbl.16b\tv3, { v0, v1 }, v3
\tstp\tx8, x9, [x1]
\tret
\t.cfi_endproc
lCPI0_0:
\t.byte\t1
_OUTLINED_FUNCTION_0:
\tret
"""
X86 = """\
_e12_scalar_field6:                 ## @e12_scalar_field6
\t.cfi_startproc
\tmovl\t5(%rdi), %ecx
\tbswapq\t%rdx
\trolw\t$8, %ax
\tpshufb\tLCPI0_0(%rip), %xmm0
\tmovq\t%rax, (%rsi)
\tleaq\t4(%rdi), %rax
\tretq
\t.cfi_endproc
"""
WASM = """\
e12_wasm_add_fields:                  # @e12_wasm_add_fields
\t.functype\te12_wasm_add_fields (i32, i32) -> ()
# %bb.0:
\tlocal.get\t0
\tv128.load\t1:p2align=0
\ti8x16.shuffle\t0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
\ti64.load\t11:p2align=0
\ti32.store16\t26
\tend_function
"""


def function(loads, simd=0, instructions=20):
    lines = [f"ldr w{i}, [x0, #{i}]" for i in range(loads)]
    lines += ["tbl.16b v0, { v0 }, v1"] * simd
    lines += ["rev w1, w1"] * (instructions - len(lines))
    return {"lines": lines, **simd_asm_audit.count(lines, "arm64")}


def configs(loads_o3=9, change_without_slp=False):
    out = []
    for opt in ("-O2", "-O3"):
        for slp in (True, False):
            loads = loads_o3 if opt == "-O3" else 9
            if change_without_slp and not slp:
                loads = 9
            name = f"scalar clang arm64 {opt}{'' if slp else ' no-slp'}"
            out.append({"name": name, "target": "arm64", "flags": f"-std=c++23 {opt}",
                        "functions": {"e12_scalar_add_fields": function(loads),
                                      "e12_scalar_book_op_add": function(9, instructions=22),
                                      "e12_scalar_field6": function(2, instructions=5)}})
    out.append({"name": "neon clang arm64 -O3", "target": "arm64", "flags": "-O3",
                "functions": {"e12_neon_add_fields": function(4, simd=2, instructions=10)}})
    return out


def record(configs_list):
    return {"header": {"host": simd_asm_audit.MAC, "commit": "0123456789ab (clean)",
                       "compilers": "clang: clang version 23.1.4",
                       "gcc": "unavailable: g++ on this machine is not GCC"},
            "configs": configs_list, "refusals": []}


def manifest():
    return {"run_id": "P-0612-20261105T090000Z", "prediction": "P-0612", "host_os": "Darwin",
            "processes": [], "sampler": "sampler.txt"}


class SimdAsmAuditTest(unittest.TestCase):
    def render(self, configs_list):
        return simd_asm_audit.render(manifest(), record(configs_list),
                                     test_simd_report.stderr_for(0),
                                     test_simd_report.sampler(1))

    def violations(self, text):
        return [code for code, _ in simd_asm_audit.check_published(text, "simd_asm_mac.md")]

    def test_functions_are_cut_from_each_target_s_output(self):
        arm = simd_asm_audit.functions_of(ARM, "arm64")
        self.assertEqual(list(arm), ["e12_scalar_add_fields"])
        self.assertEqual(len(arm["e12_scalar_add_fields"]), 6)
        self.assertEqual(simd_asm_audit.count(arm["e12_scalar_add_fields"], "arm64"),
                         {"instructions": 6, "loads": 2, "stores": 1, "byte_reverses": 1,
                          "simd": 2})
        x86 = simd_asm_audit.functions_of(X86, "x86-64")["e12_scalar_field6"]
        self.assertEqual(simd_asm_audit.count(x86, "x86-64"),
                         {"instructions": 7, "loads": 2, "stores": 1, "byte_reverses": 2,
                          "simd": 1})
        wasm = simd_asm_audit.functions_of(WASM, "wasm32")["e12_wasm_add_fields"]
        self.assertEqual(simd_asm_audit.count(wasm, "wasm32"),
                         {"instructions": 5, "loads": 2, "stores": 1, "byte_reverses": 0,
                          "simd": 2})

    def test_e12_is_refuted_only_if_nothing_is_merged_or_vectorised(self):
        _, summary = simd_asm_audit.finding(configs())
        self.assertIn("merged or vectorised in 0 of 4", summary)
        self.assertTrue(summary.endswith("E12 refuted"))
        _, summary = simd_asm_audit.finding(configs(loads_o3=7, change_without_slp=True))
        self.assertIn("merged or vectorised in 1 of 4", summary)
        self.assertIn("SLP off changes 1 scalar functions", summary)
        self.assertTrue(summary.endswith("E12 not refuted"))

    def test_the_published_file_passes_and_tampering_is_caught(self):
        text, _ = self.render(configs(loads_o3=7))
        self.assertEqual(self.violations(text), [])
        row = "| scalar clang arm64 -O3 | `e12_scalar_add_fields` | 20 | 7 | 0 | 13 | 0 |"
        self.assertIn(row, text)
        self.assertIn("counts-differ", self.violations(
            text.replace(row, row.replace("| 7 |", "| 9 |"), 1)))
        listing = "ldr w6, [x0, #6]\n"
        self.assertIn("counts-differ", self.violations(text.replace(listing, "", 1)))
        self.assertIn("finding-differs", self.violations(
            text.replace("**not refuted**", "**refuted**", 1)))
        unsaid = text.replace("- interference: the sampler rule", "- interference: no", 1)
        self.assertIn("sampler-rule-unsaid", self.violations(unsaid))

    def test_an_unquiet_or_refused_run_is_not_published(self):
        refused = record(configs())
        refused["refusals"] = ["no"]
        with self.assertRaises(simd_asm_audit.AuditError):
            simd_asm_audit.render(manifest(), refused, test_simd_report.stderr_for(0),
                                  test_simd_report.sampler(1))
        with self.assertRaisesRegex(simd_asm_audit.AuditError, "not quiet"):
            simd_asm_audit.render(manifest(), record(copy.deepcopy(configs())),
                                  test_simd_report.stderr_for(0),
                                  test_simd_report.sampler(1, foreign_at=4))


if __name__ == "__main__":
    unittest.main()
