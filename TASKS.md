# Tasks

Full plan through to the done criteria. Tick items when done, and add anything new found along the way. Target IDs refer to BENCHMARKS.md.

## Phase 1: Research

- [x] Published throughput and latency from OSS ITCH projects (`oss-throughput.md`)
- [x] Firm talks, papers, vendor figures (`firm-talks.md`)
- [x] Sample files, terms, ITCH 5.0 spec (`data-spec.md`)
- [x] SIMD against scalar, instruction costs, cross-platform plan (`simd.md`)
- [x] Code-quality bar (`code-quality.md`)
- [x] Timers, counters, pinning, WASM limits (`measurement.md`)
- [x] Trade-print verification references (`trade-verification.md`)
- [x] Backtester models, fees, latency (`backtester.md`)
- [x] Avellaneda-Stoikov and PnL attribution (`market-maker.md`)
- [x] SPSC queues and allocators (`components.md`)
- [x] io_uring, flamegraphs, Zen 1 PMU (`io-uring-flamegraph.md`)
- [x] Write BENCHMARKS.md, TASKS.md, NOTES.md
- [ ] Session opens the source of every number that becomes a target; all `SUBAGENT` entries resolved
- [ ] Phase report

## Phase 2: Harness (before any implementation)

### Repo and build
- [ ] CMake with presets for every configuration: release, asan-ubsan, tsan, fuzz, tidy, wasm. CI calls presets only.
- [ ] C++23. Warnings: `-Wall -Wextra -Werror -Wconversion -Wsign-conversion -Wshadow -Wold-style-cast`.
- [ ] Take the compiler from the environment. No hardcoded toolchain path in repo scripts.
- [ ] Pinned FetchContent, by hash, for Google Benchmark, HdrHistogram_c, and the comparison queues. liburing on Linux only: use the 2.15 build in `.toolchain` on `debian`. macOS builds compile with it off, with no code change.
- [ ] `.clang-tidy` with warnings as errors, at a pinned version.
- [ ] Avoid `<generator>` and `<stacktrace>`, which do not exist in this libc++.

### Data
- [ ] `scripts/fetch_data.sh`: chunked Range download into `data/`. Each chunk must be status 206 with the exact `Content-Range`, same ETag and exact body length. `curl --continue-at` is known to corrupt these files (DS-9).
- [ ] Verify length, ETag, gzip CRC and ISIZE from the trailer, a SHA-256 pinned in the repo, unpacked byte count, message count, and last message `S`/`C`. There is no zero-length terminator in real files (DS-61). Record that Nasdaq publishes no usable checksum, so the first-download hash is trust-on-first-use, and say which files have a third-party hash to compare with.
- [ ] Start the 12302019 and 10302019 downloads early. Third-party reports of throttling to 0.3 to 0.8 MB/s (DS-9). Keep the `.gz`, because the archive is not stable (DS-8).
- [ ] Fetch external reference files (CrossStats, MIDAS) by script into `data/`. Never commit them.
- [ ] Same scripts on `debian` under `~/quant-projects/itch-orderbook/data`.

### Platform layer (`bench/platform/`, no `#ifdef` in book or parser)
All of this is written for arm64 and x86-64, Linux and macOS, so that a Linux run needs no code change.
- [ ] `timer.hpp`: `lfence; rdtsc` and `rdtscp; lfence` on x86-64; `isb; cntvct_el0` on Linux arm64; `mach_absolute_time` on macOS; `emscripten_get_now` on WASM, batches only. Probe measures the quantum and the empty-pair cost at start of each run. Calibrate on every run, and record the quantum measured rather than the nominal rate.
- [ ] `counters.hpp`: `perf_event_open` group with `exclude_kernel` on Linux. `proc_pid_rusage` and `thread_selfcounts` on macOS, cycles and instructions only. Each event ends as measured, unavailable with reason, or multiplexed with ratio. Never fails the build or the run.
- [ ] `affinity.hpp`: `pthread_setaffinity_np` with read-back on Linux; `thread_policy_set` tried and its code 46 recorded on macOS, plus `QOS_CLASS_USER_INTERACTIVE`. Returns what happened, and tracks migrations.
- [ ] `memory.hpp`: arena allocation with hugepage hint and pre-fault, reporting the page size obtained. `madvise(MADV_HUGEPAGE)` on Linux, 16 KB pages on macOS.
- [ ] `sysinfo.hpp`: fills the results header. A field that cannot be read is written as `unavailable: <reason>`.
- [ ] Residency check: confirm the replay input is resident before timing on `debian`, or refuse to publish the run.

### Benchmarks
- [ ] Google Benchmark plus HdrHistogram, with warm-up and repetitions in fresh processes. Report median, min, max and CV, never best-of alone.
- [ ] Results header on every file: CPU, kernel, compiler, flags, commit, dataset and hash, timer info, pinning, governor, frequency, turbo, core, counter status, `measure.sh` load lines. Host always named.
- [ ] Separate throughput and latency passes. Publish instrumentation cost as a ratio.
- [ ] `bench_probe` target, run at the start of each session.
- [ ] Reported Mac numbers go through `.orchestrator/measure.sh`, and `debian` numbers through `~/quant-projects/bin/measure.sh` with `taskset -c 4` and `cpu5` idle. Output goes to a file on `debian`, not through SSH.
- [ ] README results table generated from results files.

### Tests and oracles
- [ ] Parser tests assert field values against hand-written bytes for all 23 types. `B`, `W`, `h`, `N`, `O` need byte fixtures, because real days lack them (DS-77).
- [ ] Reader safety tests: truncate a valid stream at every byte offset; final message against a `PROT_NONE` guard page; locate 65535; reference 2^64−1.
- [ ] libFuzzer target on every parser and decoder, with committed seed corpus replayed by ctest. Book fuzzer has an oracle: invariants plus the reference book after each message. A planted book bug must be caught.
- [ ] Reference book: deliberately simple and independently written. Diff design: per-message coverage of the state that can have changed, plus a proof that unchanged state cannot have diverged. No sampling.
- [ ] Oracle control: inject a bug into the fast book and show the diff catches it.
- [ ] Invariants, strict: book never crossed; every execute or cancel references a live order.
- [ ] Allocation test: fails on any allocation in the hot path, counting aligned, nothrow and array `new`, with a control book that allocates.
- [ ] Negative controls: a planted race and a planted logic bug, and the run fails if the detector stays green.

### Checks
- [ ] ASan and UBSan with no recovery and alignment checks on. TSan for all concurrent code. A sanitizer bug exits 134 on macOS.
- [ ] clang-tidy, and `-Wall -Wextra -Werror`.
- [ ] `.github/workflows/ci.yml`: `ubuntu-24.04` (x86-64, GCC and Clang), `ubuntu-24.04-arm` (arm64), `macos-15` (arm64), an Emscripten job. Actions pinned to full SHAs. CI produces no benchmark numbers.
- [ ] Validate CI locally with actionlint from repo tooling. Record that it has never executed on GitHub.
- [ ] `scripts/check.sh` runs every check.
- [ ] Grep gate: no pointer cast to a multi-byte type over wire bytes, no bare branch hints.
- [ ] Phase 2 report

## Phase 3: Implementation

### Parser
- [ ] `read_be<T>`: `memcpy` plus `std::byteswap`. `read_be48` reads exactly 6 bytes. No `start_lifetime_as`, no `std::simd`.
- [ ] Frame length checked before decode, typed failures, unknown types skipped by length, wire-controlled indexes bounded. No assert, throw or abort reachable from wire content.
- [ ] Handle what the spec omits: Market Category `M` (DS-64), test symbols (DS-65), round lots other than 100 (DS-66), zero-share and zero-price `Q` (DS-69), `X` after system event `E` (DS-71).

### SIMD (both architectures, same CMake, no code change)
- [ ] One kernel header per ISA in `include/itch/kernels/`: `scalar`, `neon`, `ssse3`, `avx2`, `avx512vbmi`, `wasm`, each in its own namespace. Tests compare every kernel with `scalar` on random and real bytes.
- [ ] arm64: compile-time only, NEON guaranteed, no dispatch code.
- [ ] x86-64: whole hot loop inside a `target` region, selected once at start with `__builtin_cpu_supports`. No `-march=native` in the default build.
- [ ] `--kernel={scalar,neon,ssse3,avx2,avx512vbmi,wasm,auto}`: all comparisons in one binary on one dataset. An unsupported kernel exits with an error, never a silent fallback.
- [ ] Kernels declare `kOverread`. The parser takes the scalar path near the end of the mapped range.
- [ ] WASM kernel uses `wasm_simd128.h` directly. It has no byte-reverse in core, so the sign of the result may flip (E11).
- [ ] Experiments E1 to E9, E11 to E13 from `simd.md`. Record the instruction set of every number. NEON and AVX2 are different experiments.
- [ ] E10 needs a Skylake-family part. Zen 1 cannot show it. Report as not measured.
- [ ] Say which wins, and explain with counters. A tie or a SIMD loss is the finding.

### Book
- [ ] Order lookup: hash and paged direct index, measured apart from level search. References fit 32 bits on observed days, which is not a guarantee (DS-72).
- [ ] Levels: flat with best price at the end; FIFO chains; `std::map` levels on fresh and aged heaps as baseline.
- [ ] Sparse handling for stub quotes (DS-73).
- [ ] Replace, delete and add measured on 8.7% `U` (2019) and 27.7% `U` (2026) mixes.
- [ ] Prefetch lookahead D in {0, 4, 8, 16, 32, 64} (E8), and dispatch form (E7). Each hint keeps a measured reason in a comment.
- [ ] Hot structs: `static_assert` on size, alignment, trivial copyability.
- [ ] T-BOOK-1 to T-BOOK-4, T-LAT-1 to T-LAT-3, T-DESIGN-1 on the harness.
- [ ] Strict crossed-book run, full day. Count and characterise each violation by symbol, time and trading state. Report. The user decides the scope.
- [ ] Oracle replay on the Mac under `heavy.sh`, all 268,744,780 messages.

### Trade-print verification
Hard means any mismatch fails. Each check needs an injected bug that trips it. Check 17 in the evidence table only tests our aggregator, so it must not be cited as an independent result.
- [ ] `E`, `C` reference a live order, on this locate, `shares <= remaining`. Hard.
- [ ] `X`, `D`, `U` same liveness, and `U`'s new reference unused. Hard.
- [ ] Per-order `Add` shares equal the sum of deductions; book empty at `S/C` (T-TRADE-1). Hard.
- [ ] `E` at best price on its side while the book is uncrossed. Measure first, then hard on that set. Count the rest.
- [ ] `E` at queue front: reported as a rate. Published 99.82% and 99.914%, unexplained remainder.
- [ ] `C` deducts from the order and leaves its price unchanged. Hard for the deduction, with counts by `Printable`.
- [ ] `P`: reference 0, side `B`, no book change. Hard. Whole class excluded from "hits a resting order": count and surface.
- [ ] `Q` per symbol against CrossStats (T-TRADE-2). Hard. Halt crosses: sum of `Q` outside CrossStats equals type `H` sum.
- [ ] `C_N` against `Q`: per symbol and side `<=`, equal price. Measure first, because no one has confirmed this on real data.
- [ ] `E + C_Y + P` against MIDAS (T-TRADE-3): `<=` now, equality after the window is measured. Add `P` against `HiddenVol`.
- [ ] Cboe per-tape check (T-TRADE-4): only after the user's decision on its terms.
- [ ] `B` reverses a stored match, hard on 10302019, fixtures otherwise.
- [ ] Match numbers unique across `E`, `C`, `P`, `Q`. Measure first.
- [ ] Surface class-level counts in the phase report: test symbols, zero-share `Q`, `C_N`, `P`, `B`.

### Low-latency components
Each benchmarked alone, then measured inside the book or backtester.
- [ ] SPSC ring: padding from one constant that is 128 on Apple Silicon. Cached indices, mask, batch publish.
- [ ] SPSC comparison: project queue, rigtorp, boost::lockfree, moodycamel, folly, atomic_queue, xenium Vyukov as MPMC control, mutex ring as floor.
- [ ] SPSC ablation, one change at a time (T-SPSC-2). Protocols: ping-pong RTT, saturated one-way, paced one-way with percentiles, bursty replay.
- [ ] Correctness: TSan on both hosts, arm64 stress without TSan, negative control with weakened ordering. It is unconfirmed whether stress catches this on M-series (LC-37), so report what happens.
- [ ] Parser thread → queue → book thread on a real day, against single-threaded replay.
- [ ] Pool allocator: LIFO free list, 32-bit indices, sized from the counted peak, pre-faulted, 2 MiB pages where offered.
- [ ] Allocator arms with only the allocator changed: system malloc fresh and aged, mimalloc, `unsynchronized_pool_resource`, `monotonic_buffer_resource`, pool with index, pool with pointer, FIFO free list (T-ARENA-1). Crossed with page sizes on `debian`.

### io_uring (Linux only, on `debian`)
- [ ] MoldUDP64 framing, replay sender from a prebuilt packet array: unicast `127.0.0.1`, connected, sender on its own physical core, spin pacing.
- [ ] Modes S1 to S4 and U1 to U5 from `io-uring-flamegraph.md`. U2 and S3 are the headline pair. Same parser in each.
- [ ] Smoke test: record kernel, `io_uring_disabled`, `rmem_max`, `ulimit -l`, mitigations, feature probes.
- [ ] Find the sender's ceiling first. A run near it is invalid.
- [ ] Max lossless rate by bisection, feed-shaped replay, latency at fixed rates including a low rate, CPU split across sender, receiver, SQ thread, softirq (T-URING-2).
- [ ] Results state that loopback runs softirq on the sender's CPU, and that this is not a network benchmark.
- [ ] macOS builds with no receiver backend for this mode and no code change. Nothing substitutes for it.

### Backtester
- [ ] Event ordering with `L_md`, `L_oe`, `L_resp`, `L_compute`. A simulated order joins at arrival, not at decision.
- [ ] Queue model from real L3 order references: `ahead` set from the real queue at arrival, falling only with depletion ahead.
- [ ] Partial fills from real executions behind S. Share ledger prevents a real order filling S twice.
- [ ] Fees keyed by date, per fill, in integers. Base tier stated.
- [ ] All 48 scenarios from `backtester.md` as tests (T-BT-2).
- [ ] Lookahead: prefix invariance, future perturbation, oracle control must be caught.
- [ ] T-BT-1 on the harness.
- [ ] State the limit: no market impact.

### Market maker
- [ ] Avellaneda-Stoikov as the demo, with the finite-horizon form as named by the user. GLFT stationary form as a labelled variant, because the evidence recommends it on tick-constrained stocks.
- [ ] Commit symbols, γ, h = 15 s and order size before the first evaluation run. Selection cannot look at the result.
- [ ] Calibrate σ, A, k from the market tape on 10302019 only. Report on 12302019. Label both. 10302019 is an FOMC day, and 12302019 is a late-December session.
- [ ] Attribution in a module that never reads the ledger. Integers, `==`, no tolerance (T-MM-1).
- [ ] Property tests 1 to 16 from `market-maker.md`, including the negative test.
- [ ] Replication of the AS tables (T-MM-2).
- [ ] Four runs, one switch at a time: Base, Q-off, L-off, Both-off. Same mark path hash in all.
- [ ] Report: S gross, fees, A, I, total, in dollars, per share and in ticks. Markout curve. No return, no Sharpe. Fixed sentence: "One symbol-day. No edge is claimed."

### Counters and flamegraphs
- [ ] `perf` on `debian`, `:u` events. Publish which counters are real hardware events on Zen 1, with what each counts.
- [ ] Confirm `cache-misses` against AMD's documentation and cite it before quoting (T-PERF-1).
- [ ] Separate frame-pointer build, with its throughput delta published.
- [ ] `perf record -g --user-callchains -e cycles:u`, then FlameGraph, for each hot path. `perf annotate` beside it.
- [ ] Mac: Apple tooling without sudo where it works, labelled macOS. Xcode is absent, so `xctrace` is unavailable now. It does not replace the `perf` data.

### Browser demo
- [ ] Replay engine with `em++`, `-O3 -msimd128`. Fixed initial memory.
- [ ] Static page in `docs/`: animated depth and trades for one symbol, messages/sec from batches.
- [ ] Banned styling: cream or off-white background, italic accent words in headings, numbered "01 / 02 / 03" labels, pill buttons, gradient heroes.
- [ ] Extract generated by script into a gitignored path. The page says what to run if it is missing. No market data in git, real or synthetic. No public deployment of real extracts without Nasdaq's written approval.
- [ ] Native Linux numbers from a `debian` results file. The page shows them as missing until they exist. No Mac numbers in that slot.
- [ ] State plainly that WASM cannot show true low latency, and why.
- [ ] Runs locally from `docs/` over a local HTTP server, because `file://` blocks loading.
- [ ] T-WASM-1, labelled macOS.

### Write-up and iteration
- [ ] NOTES.md kept current, including what didn't help.
- [ ] Phase 3 report, with the crossed-book findings and trade-print class counts.

## Phase 4: Final review

- [ ] Every target in BENCHMARKS.md met, or measured number, gap and reason stated.
- [ ] Local check script passes.
- [ ] Each fuzzer clean for 30 minutes, under `heavy.sh`, on the Mac.
- [ ] README: what it is, how to build and run, generated results table.
- [ ] WRITEUP.md: blog-post length, with numbers. Data layout, memory, branch prediction, what was tried, what didn't work.
- [ ] WASM demo page builds and runs locally from `docs/`.
- [ ] Full-repo review. For each problem: file and line, why it's wrong, how to show it fails, then fix it.
- [ ] Every Linux number cites its commit. Sync through `debian_sync.sh` from committed `HEAD`.
- [ ] Final report
