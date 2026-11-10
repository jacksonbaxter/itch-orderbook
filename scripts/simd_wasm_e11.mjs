// E11 (simd.md): every Add Order of the day decoded on the scalar kernel or on the SIMD128
// kernel of the Emscripten build's itch_e11 module, in node, wall clock only.
//
//     node scripts/simd_wasm_e11.mjs --module BUILD/bench/wasm/itch_e11.mjs --input DAY
//         --out RUN.json --kernel scalar|wasm [--commit TEXT] [--dataset NAME]
//         [--dataset-sha256 HEX]
//
// The day is read into the module's 64 MiB window a piece at a time; a frame cut by the
// window's end is carried to the start of the next piece. One untimed decode pass, then four
// timed passes over the whole day in the order walk, work, work, walk, as bench_kernels runs
// its paired experiments; each pass's time is the sum of performance.now() around every
// piece's e11_pass(), so the reads from the file are outside it. The record has the shape of
// bench_kernels's, with no counters: node has none.
//
// Exit codes: 0 publishable, 3 refused with the record written, 2 bad usage.
import { closeSync, existsSync, openSync, readFileSync, readSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { pathToFileURL } from "node:url";

const KERNELS = { scalar: 0, wasm: 1 };
const ISA = {
  scalar: "scalar: general-purpose registers, REV or BSWAP",
  wasm: "WebAssembly SIMD128",
};

function usage(why) {
  process.stderr.write(`simd_wasm_e11: ${why}\nusage: node simd_wasm_e11.mjs --module ` +
    "ITCH_E11_MJS --input DAY --out RUN.json --kernel scalar|wasm [--commit TEXT] " +
    "[--dataset NAME] [--dataset-sha256 HEX]\n");
  process.exit(2);
}

function parse(argv) {
  const options = { commit: "unknown", dataset: "unknown", "dataset-sha256": "unknown" };
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith("--") || i + 1 >= argv.length) usage(`bad argument ${argv[i]}`);
    options[argv[i].slice(2)] = argv[i + 1];
  }
  for (const name of ["module", "input", "out", "kernel"]) {
    if (!(name in options)) usage(`--${name} is required`);
  }
  if (!(options.kernel in KERNELS)) {
    usage(`kernel ${options.kernel} is not in this module: it has scalar and wasm`);
  }
  return options;
}

function hostLabel(cpu) {
  if (os.platform() === "darwin" && cpu.includes("Apple M4 Max")) return "macOS (M4 Max)";
  return "unknown";
}

// One pass over the whole file: every piece through the window, timed around e11_pass alone.
function pass(m, file, kernel, work) {
  const window = m._e11_window();
  const size = m._e11_window_bytes();
  let carried = 0;
  let offset = 0;
  let seconds = 0;
  for (;;) {
    const read = readSync(file, m.HEAPU8, window + carried, size - carried, offset);
    offset += read;
    const length = carried + read;
    if (length === 0) break;
    const started = performance.now();
    const used = m._e11_pass(kernel, work, length);
    seconds += (performance.now() - started) / 1000;
    carried = length - used;
    if (read === 0) break;
    m.HEAPU8.copyWithin(window, window + used, window + length);
  }
  return { seconds, leftover: carried };
}

function fold(m) {
  return (BigInt(m._e11_fold_high() >>> 0) << 32n) | BigInt(m._e11_fold_low() >>> 0);
}

async function main() {
  const options = parse(process.argv.slice(2));
  const { default: create } = await import(pathToFileURL(path.resolve(options.module)).href);
  const m = await create();
  const kernel = KERNELS[options.kernel];
  const file = openSync(options.input, "r");
  const refusals = [];
  const passes = [];
  let messages = 0;
  try {
    m._e11_reset();
    const warm = pass(m, file, kernel, 1);
    messages = m._e11_messages();
    if (warm.leftover !== 0) refusals.push(`the day ends in a cut frame, ${warm.leftover} bytes`);
    for (const work of [0, 1, 1, 0]) {
      m._e11_reset();
      const timed = pass(m, file, kernel, work);
      passes.push({ mode: work ? "work" : "walk", messages: m._e11_messages(),
        items: m._e11_items(), seconds: timed.seconds, fold: fold(m) });
    }
  } finally {
    closeSync(file);
  }
  for (const one of passes) {
    if (one.messages !== messages) {
      refusals.push(`a ${one.mode} pass walked ${one.messages} messages of ${messages}`);
    }
  }
  for (const mode of ["walk", "work"]) {
    const same = passes.filter((one) => one.mode === mode);
    if (same.some((one) => one.fold !== same[0].fold || one.items !== same[0].items)) {
      refusals.push(`two ${mode} passes disagree`);
    }
  }
  const buildJson = path.join(path.dirname(path.resolve(options.module)), "itch_e11_build.json");
  const build = existsSync(buildJson) ? JSON.parse(readFileSync(buildJson, "utf8"))
    : { compiler: "unavailable: no itch_e11_build.json", flags: "unavailable" };
  const cpu = os.cpus()[0]?.model ?? "unknown";
  const header = {
    host: hostLabel(cpu),
    cpu,
    kernel: `${os.type()} ${os.release()}`,
    compiler: build.compiler,
    flags: build.flags,
    // V8's four-part version reads as an IPv4 address to the privacy checks: its parts by name.
    runtime: `node ${process.version}, V8 ${process.versions.v8.split(".").slice(0, 2).join(".")} ` +
      `build ${process.versions.v8.split(".").slice(2).join("-")}`,
    commit: options.commit,
    dataset: options.dataset,
    dataset_sha256: options["dataset-sha256"],
    timer: "performance.now() in node, wall clock, summed over the window's pieces",
    counters: "unavailable: node exposes no hardware counters",
    pinning: "not pinned: node on macOS has no thread affinity",
    input: "read into a fixed 64 MiB window of linear memory a piece at a time; the reads " +
      "are outside the timed spans",
  };
  const record = {
    header,
    variant: { experiment: "e11", simd: "E11", kernel: options.kernel, isa: ISA[options.kernel],
      from_auto: false, arch: "wasm32", overread: 0, width: 0, form: "", watchlist: 0,
      watchlist_listed: 0 },
    mix: { messages },
    passes: passes.map((one) => ({ ...one, fold: `@${one.fold}@`, counters: [] })),
    peak_rss_bytes: process.resourceUsage().maxRSS * 1024,
    refusals,
  };
  // The folds are 64-bit, past what a JSON number holds in JavaScript: written as digits.
  writeFileSync(options.out, JSON.stringify(record, null, 2).replace(/"@(\d+)@"/g, "$1") + "\n");
  for (const why of refusals) process.stderr.write(`simd_wasm_e11: refused: ${why}\n`);
  process.exit(refusals.length === 0 ? 0 : 3);
}

main().catch((error) => {
  process.stderr.write(`simd_wasm_e11: ${error.stack ?? error}\n`);
  process.exit(2);
});
