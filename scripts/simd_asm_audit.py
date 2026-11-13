#!/usr/bin/env python3
"""E12 (simd.md): the kernels' decode as each compiler emits it, counted.

    simd_asm_audit.py collect --out RECORD.json [--commit TEXT]
    simd_asm_audit.py report --run-dir DIR --out results/simd_asm_HOST.md [--run-log FILE]
    simd_asm_audit.py --check FILE

collect compiles bench/asm/ with -S: the scalar kernel (scalar.cpp) under every compiler on
this machine that can, for arm64, x86-64 and wasm32, at -O2 and -O3, each with the SLP
vectorizer on and off (-fno-slp-vectorize); and each vector kernel once, at the build's -O3.
x86-64 is compiled against the macOS SDK with -march=x86-64, the baseline a Linux build
without -march gets (SSE2, no SSSE3); x86-64-macos is clang's macOS default, penryn, which
has SSSE3. On Linux (debian) every compiler targets its own x86-64 Linux at -march=x86-64, and
the x86 vector kernels are compiled under each compiler there, GCC included; the arm64, macOS
and wasm32 targets are the Mac's.
Each function's instructions are counted: all of them, loads, stores, byte reverses (REV,
REV16, REV32 on arm64; BSWAP, MOVBE and an 8-bit rotate of a 16-bit word on x86-64; core
WebAssembly has none) and those that use a SIMD register. GCC is used where a real one is on
PATH; on this Mac g++ is Apple clang, and the record says so. collect runs as one process of
scripts/simd_driver.py --experiment asm, so measure.sh and the sampler rule hold it as they
hold every timed run, though no number here comes from a clock.

report writes the results file from the driver's run directory. --check recounts every
listing in the file, rebuilds the table and the finding from them, and fails on any
difference, on a load line that is not measure.sh's, on a macOS file that does not say the
sampler rule was applied, and on anything private.

The kill criterion (simd.md, E12): refuted if no compiler merges or vectorises the scalar
decode. Merged means fewer loads than the eight reads the Add Order's fields take one at a
time (reference, the timestamp's 4 and 2 bytes, shares, price, locate, tracking number,
side); vectorised means any instruction on a SIMD register.

Each violation prints as `simd_asm_audit: FAIL <code>: <detail>`.

Exit codes: 0 done, or --check found nothing, 1 --check found something, 2 bad usage or
unusable input.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_results  # noqa: E402
import process_kinds  # noqa: E402
import simd_report  # noqa: E402
import stamp_load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC = simd_report.MAC
FLAGS = ("-std=c++23", "-DNDEBUG")
X86 = "--target=x86_64-apple-macos13"
FUNCTIONS = ("add_fields", "book_op_add", "field6", "batch4")
LISTED = ("add_fields", "book_op_add")
UNMERGED_READS = 8
FILE_NAME = re.compile(r"^simd_asm_(mac|debian)\.md$")
ARM_LOAD = re.compile(r"^ld")
ARM_STORE = re.compile(r"^st")
ARM_SIMD = re.compile(r"\b[vqd]\d+\b|\.(?:16b|8b|8h|4h|4s|2s|2d|1d)\b")
X86_SIMD = re.compile(r"%[xyz]mm\d+|%k[0-7]\b")
WASM_SIMD = re.compile(r"^(?:v128|i8x16|i16x8|i32x4|i64x2|f32x4|f64x2)\.")
COUNTS = ("instructions", "loads", "stores", "byte_reverses", "simd")
INTERFERENCE = simd_report.INTERFERENCE


class AuditError(Exception):
    """The input cannot become a results file."""


def compilers():
    """(name, command, version) for each compiler this machine has, and why GCC is absent."""
    found = []
    for name, command in (("clang", "clang++"), ("apple-clang", "/usr/bin/clang++"),
                          ("emscripten", "em++"), ("gcc", "g++")):
        path = shutil.which(command)
        if path is None:
            continue
        version = subprocess.run([path, "--version"], capture_output=True, text=True,
                                 check=False).stdout.splitlines()
        first = version[0] if version else "unknown"
        if name == "gcc" and "Free Software Foundation" not in "\n".join(version):
            continue
        if name == "apple-clang" and "Apple" not in first:
            continue
        found.append((name, command, first))
    gcc = next((v for n, _, v in found if n == "gcc"), None)
    return found, gcc or "unavailable: g++ on this machine is not GCC, and none other is on PATH"


def configurations(found, system=None):
    """(name, compiler command, source, target, flags) for every compile."""
    if (system or platform.system()) == "Linux":
        return linux_configurations(found)
    out = []
    for name, command, _ in found:
        targets = {"clang": (("arm64", []), ("x86-64", [X86, "-march=x86-64"]),
                             ("x86-64-macos", [X86])),
                   "apple-clang": (("arm64", []),
                                   ("x86-64", ["-target", "x86_64-apple-macos13",
                                               "-march=x86-64"])),
                   "gcc": (("native", []),),
                   "emscripten": (("wasm32", ["-msimd128"]),)}[name]
        for target, extra in targets:
            for opt in ("-O2", "-O3"):
                for slp in (True, False):
                    flags = [*FLAGS, opt, *extra, *([] if slp else ["-fno-slp-vectorize"])]
                    if name == "gcc" and not slp:
                        flags[-1] = "-fno-tree-slp-vectorize"
                    label = f"scalar {name} {target} {opt}{'' if slp else ' no-slp'}"
                    out.append((label, command, "scalar.cpp", target.split("-macos")[0],
                                flags))
    names = [name for name, _, _ in found]
    if "clang" in names:
        out.append(("neon clang arm64 -O3", "clang++", "neon.cpp", "arm64", [*FLAGS, "-O3"]))
        out.append(("x86 clang x86-64 -O3", "clang++", "x86.cpp", "x86-64", [*FLAGS, "-O3", X86]))
    if "emscripten" in names:
        out.append(("wasm emscripten wasm32 -O3", "em++", "wasm.cpp", "wasm32",
                    [*FLAGS, "-O3", "-msimd128"]))
    return out


def linux_configurations(found):
    """On Linux, x86-64 only: each compiler's scalar at -march=x86-64, and its vector kernels."""
    out = []
    for name, command, _ in found:
        if name not in ("clang", "gcc"):
            continue
        for opt in ("-O2", "-O3"):
            for slp in (True, False):
                off = "-fno-tree-slp-vectorize" if name == "gcc" else "-fno-slp-vectorize"
                flags = [*FLAGS, opt, "-march=x86-64", *([] if slp else [off])]
                out.append((f"scalar {name} x86-64 {opt}{'' if slp else ' no-slp'}", command,
                            "scalar.cpp", "x86-64", flags))
        out.append((f"x86 {name} x86-64 -O3", command, "x86.cpp", "x86-64", [*FLAGS, "-O3"]))
    return out


def functions_of(text, target):
    """Each e12_ function's instruction lines, in order, from -S output."""
    out = {}
    current = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0] if target == "arm64" else raw.split("#", 1)[0]
        label = re.match(r"^_?(e12_[a-z0-9]+_[a-z0-9_]+):", line)
        if label:
            current = label.group(1)
            out[current] = []
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped in (".cfi_endproc", "end_function") or (
                re.match(r"^[A-Za-z_]\w*:", line) and not re.match(r"^[Ll]", line)):
            current = None
            continue
        if not stripped or stripped.startswith(".") or stripped.endswith(":"):
            continue
        out[current].append(re.sub(r"\s+", " ", stripped))
    return out


def operands(line):
    parts = line.split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def count(lines, target):
    counts = dict.fromkeys(COUNTS, 0)
    for line in lines:
        mnemonic, rest = operands(line)
        counts["instructions"] += 1
        if target == "wasm32":
            counts["loads"] += ".load" in mnemonic
            counts["stores"] += ".store" in mnemonic
            counts["simd"] += bool(WASM_SIMD.match(mnemonic))
        elif target == "arm64":
            base = mnemonic.split(".", 1)[0]
            counts["loads"] += bool(ARM_LOAD.match(base)) and "[" in rest
            counts["stores"] += bool(ARM_STORE.match(base)) and "[" in rest
            counts["byte_reverses"] += base in ("rev", "rev16", "rev32") and \
                not ARM_SIMD.search(line)
            counts["simd"] += bool(ARM_SIMD.search(rest) or "." in mnemonic)
        else:
            args = [a.strip() for a in re.split(r",(?![^(]*\))", rest)] if rest else []
            memory_first = bool(args) and "(" in args[0] and not mnemonic.startswith("lea")
            memory_last = len(args) > 1 and "(" in args[-1]
            counts["loads"] += memory_first
            counts["stores"] += memory_last
            counts["byte_reverses"] += mnemonic.startswith(("bswap", "movbe")) or \
                (mnemonic in ("rolw", "rorw") and args[:1] == ["$8"])
            counts["simd"] += bool(X86_SIMD.search(rest))
    return counts


def host_label():
    if platform.system() != "Darwin":
        return "debian" if platform.system() == "Linux" else "unknown"
    brand = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                           text=True, check=False).stdout.strip()
    return MAC if "Apple M4 Max" in brand else "unknown"


def collect(args):
    found, gcc = compilers()
    asm = REPO_ROOT / "bench" / "asm"
    configs = []
    for label, command, source, target, flags in configurations(found):
        done = subprocess.run([command, *flags, f"-I{REPO_ROOT / 'include'}", "-S", "-o", "-",
                               str(asm / source)], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            print(f"simd_asm_audit: {label} did not compile: {done.stderr[-2000:]}",
                  file=sys.stderr)
            return 2
        listed = functions_of(done.stdout, target)
        configs.append({"name": label, "source": f"bench/asm/{source}", "target": target,
                        "flags": " ".join(flags),
                        "functions": {name: {"lines": lines, **count(lines, target)}
                                      for name, lines in listed.items()}})
    record = {"header": {"host": host_label(), "commit": args.commit,
                         "compilers": "; ".join(f"{n}: {v}" for n, _, v in found),
                         "gcc": gcc},
              "configs": configs, "refusals": [],
              "variant": {"experiment": "asm", "kernel": "scalar"}}
    Path(args.out).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return 0


def table(configs):
    out = ["| Configuration | Function | Instructions | Loads | Stores | Byte reverses | "
           "SIMD-register instructions |", "|---|---|---|---|---|---|---|"]
    for config in configs:
        for name, f in config["functions"].items():
            out.append(f"| {config['name']} | `{name}` | " + " | ".join(
                str(f[key]) for key in COUNTS) + " |")
    return out


ROW = re.compile(r"^\| (.+?) \| `(e12_\w+)` \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$")


def short(name):
    return re.sub(r"^e12_[a-z0-9]+_", "", name)


def finding(configs):
    lines = []
    scalar = [c for c in configs if c["name"].startswith("scalar ")]
    merged = []
    for config in scalar:
        f = next(v for k, v in config["functions"].items() if short(k) == "add_fields")
        if f["loads"] < UNMERGED_READS or f["simd"] > 0:
            merged.append(config["name"])
    lines.append(f"- The scalar `add_fields` is merged or vectorised in {len(merged)} of "
                 f"{len(scalar)} configurations" + (": " + ", ".join(merged) if merged else "")
                 + ".")
    by_name = {c["name"]: c for c in configs}
    slp_changed = []
    for config in scalar:
        if config["name"].endswith(" no-slp"):
            continue
        other = by_name.get(config["name"] + " no-slp")
        if other is None:
            continue
        for name, f in config["functions"].items():
            g = other["functions"].get(name)
            if g is not None and any(g[key] != f[key] for key in COUNTS):
                slp_changed.append(f"{config['name']} `{short(name)}`")
    lines.append("- Turning the SLP vectorizer off changes the counts of "
                 + (", ".join(slp_changed) if slp_changed else "no scalar function in any "
                    "configuration") + ".")
    for config in configs:
        if config["name"].startswith("scalar ") and " -O3" in config["name"] and \
                not config["name"].endswith("no-slp"):
            f = next(v for k, v in config["functions"].items() if short(k) == "add_fields")
            lines.append(f"- {config['name']}: `add_fields` is {f['instructions']} instructions, "
                         f"{f['loads']} loads, {f['byte_reverses']} byte reverses, "
                         f"{f['simd']} on SIMD registers.")
    for config in configs:
        if config["name"].startswith("scalar "):
            continue
        for name, f in config["functions"].items():
            if short(name) == "add_fields":
                lines.append(f"- {config['name']}: `{name}` is {f['instructions']} "
                             f"instructions, {f['loads']} loads, {f['simd']} on SIMD registers.")
    refuted = not merged
    lines.append("- E12's kill criterion, refuted if no compiler merges or vectorises the scalar "
                 "decode: " + ("**refuted**." if refuted else "**not refuted**."))
    summary = (f"scalar add_fields merged or vectorised in {len(merged)} of {len(scalar)} "
               f"configurations; SLP off changes {len(slp_changed)} scalar functions; E12 "
               + ("refuted" if refuted else "not refuted"))
    return lines, summary


def listings(configs):
    out = []
    for config in configs:
        for name, f in config["functions"].items():
            if short(name) not in LISTED or (not config["name"].startswith("scalar ") and
                                              short(name) != "add_fields"):
                continue
            out += [f"### {config['name']}: `{name}`", "", f"Target {config['target']}, "
                    f"`{config['flags']}`.", "", "```"]
            out += f["lines"]
            out += ["```", ""]
    return out


def parse_listings(text):
    """Every listing in the file, recounted, as {(config, function): counts}."""
    found = {}
    body = check_results.section(text, "Listings") or ""
    for match in re.finditer(r"^### (.+?): `(e12_\w+)`\n\nTarget (\S+), `[^`]*`\.\n\n```\n"
                             r"(.*?)```$", body, re.MULTILINE | re.DOTALL):
        lines = [line for line in match.group(4).splitlines() if line]
        found[(match.group(1), match.group(2))] = {"lines": lines,
                                                   **count(lines, match.group(3))}
    return found


def configs_from_table(text):
    configs = {}
    for line in (check_results.section(text, "Results") or "").splitlines():
        match = ROW.match(line)
        if match is None:
            continue
        config = configs.setdefault(match.group(1), {"name": match.group(1), "functions": {}})
        config["functions"][match.group(2)] = dict(zip(COUNTS, map(int, match.groups()[2:])))
    return list(configs.values())


def what_was_run(host):
    """The method paragraph, in each host's own words: on the Mac each vector kernel is
    compiled once, by clang; on debian under each compiler, GCC included."""
    vector = ("each vector kernel once, at the build's -O3" if host == MAC else
              "each vector kernel at the build's -O3, on debian under each compiler")
    return ("`bench/asm/` holds one extern \"C\" function an operation for each kernel, the "
            "kernel inlined into it inside its target region: `add_fields` (an Add Order into "
            "the 32-byte record of E2), `book_op_add` (an Add Order into the book's op), "
            "`field6` (E1's 6-byte field) and `batch4` (E5). Each is compiled with -S and its "
            "instructions counted. The scalar kernel is compiled under every compiler here, at "
            f"-O2 and -O3, with the SLP vectorizer on and off; {vector}. These are counts of "
            "compiler output: no clock or counter is read.")


def render(manifest, record, stderr, sampler):
    header = record["header"]
    if record["refusals"]:
        raise AuditError(f"a refused record: {record['refusals']}")
    host = header["host"]
    start, end = simd_report.load_lines(stderr, host)
    if host == MAC:
        if sampler is None:
            raise AuditError("a macOS run needs the CPU sampler's log")
        why = simd_report.wasm_bench.unquiet(sampler.splitlines(), simd_report.timestamp(start),
                                             simd_report.timestamp(end))
        if why is not None:
            raise AuditError(f"the process is not quiet: {why}")
    configs = record["configs"]
    found, summary = finding(configs)
    where = simd_report.HOST_FILE.get(host, host)
    out = [f"# E12: the kernels' decode as each compiler emits it, {where}",
           "", "Generated by `scripts/simd_asm_audit.py` from a raw record that is not "
           "committed. Do not edit by hand.", "", "## Header", "",
           f"- host: {host}", f"- commit: {header['commit']}",
           f"- compilers: {header['compilers']}", f"- gcc: {header['gcc']}",
           "- experiment: asm", "- simd: E12", f"- prediction: {manifest['prediction']}",
           f"- run_id: {manifest['run_id']}",
           f"- measure_load_start: {start}", f"- measure_load_end: {end}",
           f"- run_started: {simd_report.timestamp(start)}",
           f"- run_finished: {simd_report.timestamp(end)}"]
    if host == MAC:
        out.append(f"- interference: {INTERFERENCE}")
    out += [f"- summary: {summary}", "", "## What was run", "", what_was_run(host), "",
            "## Results", ""]
    out += table(configs)
    out += ["", "## Finding", ""] + found + ["", "## Listings", ""]
    out += listings(configs)
    out += ["## measure.sh load lines", "", "```", start, end, "```", "", "## Command", "",
            "```", ("measure.sh /usr/bin/time -l" if host == MAC else "measure.sh taskset -c 4")
            + " python3 scripts/simd_asm_audit.py collect --out "
            "<raw>/audit-1.json --commit <commit>  (scripts/simd_driver.py --experiment asm)",
            "```", ""]
    return "\n".join(out), summary


def check_published(text, name=None):
    fields = check_results.parse_header(text)
    if not fields:
        return [("header-missing", "no Header section")]
    found = []
    host = fields.get("host")
    if name is not None:
        match = FILE_NAME.match(name)
        if match is None or simd_report.HOST_FILE.get(host) != match.group(1):
            found.append(("host-label", f"{name} holds {host}"))
    if not re.match(r"^[0-9a-f]{12} ", fields.get("commit", "")):
        found.append(("commit-missing", repr(fields.get("commit"))))
    found += list(check_results.check_load(fields))
    found += list(check_results.check_private(text, fields))
    if host == MAC and fields.get("interference") != INTERFERENCE:
        found.append(("sampler-rule-unsaid", repr(fields.get("interference"))))
    if check_results.section(text, "What was run") != "\n" + what_was_run(host) + "\n\n":
        found.append(("method-differs", f"What was run is not {host}'s own wording"))
    configs = configs_from_table(text)
    if not configs:
        return found + [("table-missing", "no counts in the table")]
    listed = parse_listings(text)
    for config in configs:
        for function, counts in config["functions"].items():
            key = (config["name"], function)
            if key in listed:
                if any(listed[key][k] != counts[k] for k in COUNTS):
                    found.append(("counts-differ", f"{key}: the table says {counts}, the "
                                                   f"listing counts {listed[key]}"))
            wanted = short(function) in LISTED and (config["name"].startswith("scalar ") or
                                                    short(function) == "add_fields")
            if wanted and key not in listed:
                found.append(("listing-missing", f"{key}"))
    try:
        lines, summary = finding(configs)
    except (StopIteration, KeyError) as error:
        return found + [("table-unusable", repr(error))]
    if check_results.section(text, "Finding") != "\n" + "\n".join(lines) + "\n\n":
        found.append(("finding-differs", "the finding is not what the table gives"))
    if fields.get("summary") != summary:
        found.append(("summary-differs", f"{fields.get('summary')!r} against {summary!r}"))
    return found


def report(args):
    manifest = json.loads((args.run_dir / "manifest.json").read_text(encoding="utf-8"))
    published = [e for e in manifest["processes"] if e["published"]]
    if len(published) != 1:
        raise AuditError(f"wanted one published process, found {len(published)}")
    stem = args.run_dir / published[0]["stem"]
    record = json.loads(Path(f"{stem}.json").read_text(encoding="utf-8"))
    stderr = Path(f"{stem}.err").read_text(encoding="utf-8", errors="replace")
    sampler = None
    if manifest.get("sampler"):
        sampler = (args.run_dir / manifest["sampler"]).read_text(encoding="utf-8")
    text, summary = render(manifest, record, stderr, sampler)
    violations = check_published(text, args.out.name)
    if violations:
        return report_violations(violations)
    args.out.write_text(text, encoding="utf-8")
    if args.run_log:
        with open(args.run_log, "a", encoding="utf-8") as log:
            log.write(f"| {manifest['run_id']}_report | {manifest['prediction']} | "
                      f"{record['header']['commit']} | {manifest['host_os']} | published: "
                      f"{summary} |\n")
    print(f"simd_asm_audit: wrote {args.out.name}")
    return 0


def report_violations(violations):
    for code, detail in violations:
        print(f"simd_asm_audit: FAIL {code}: {detail}")
    return 1 if violations else 0


def main(argv=None):
    process_kinds.check_map("simd_asm_audit")  # Stops here on a map set and unusable.
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--check"] and len(argv) == 2:
        path = Path(argv[1])
        violations = check_published(path.read_text(encoding="utf-8"), path.name)
        if violations:
            return report_violations(violations)
        print(f"simd_asm_audit: ok {path.name}")
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("collect")
    one.add_argument("--out", required=True)
    one.add_argument("--commit", default="unknown")
    two = sub.add_parser("report")
    two.add_argument("--run-dir", type=Path, required=True)
    two.add_argument("--out", type=Path, required=True)
    two.add_argument("--run-log", type=Path)
    args = parser.parse_args(argv)
    try:
        return collect(args) if args.command == "collect" else report(args)
    except (AuditError, simd_report.ReportError, OSError, ValueError, KeyError) as error:
        print(f"simd_asm_audit: unusable input: {error!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
