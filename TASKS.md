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
- [x] Session opens the source of every number that becomes a target; remaining `SUBAGENT` entries are context only
- [x] Gap search for anything else missed the way carteret was (`gap-search.md`)
- [x] Phase report

## Phase 2: Harness (before any implementation)

### Repo and build
- [x] CMake with presets for every configuration: release, asan-ubsan, tsan, fuzz, tidy, wasm.
- [ ] CI calls presets only.
- [x] C++23. Warnings: `-Wall -Wextra -Werror -Wconversion -Wsign-conversion -Wshadow -Wold-style-cast`.
- [x] Take the compiler from the environment. No hardcoded toolchain path in repo scripts.
- [x] Pinned FetchContent, by hash, for GoogleTest, Google Benchmark and HdrHistogram_c. Each commit resolves to its release tag and each archive matches its hash. Nothing calls the benchmark dependencies yet.
- [ ] Pinned FetchContent, by hash, for the comparison queues. liburing on Linux only: use the 2.15 build in `.toolchain` on `debian`. macOS builds compile with it off, with no code change.
- [x] `.clang-tidy` with warnings as errors, at a pinned version.
- [x] Avoid `<generator>` and `<stacktrace>`, which do not exist in this libc++.

### Data
- [x] `scripts/fetch_data.py`: chunked Range download into `data/`. Each chunk must be status 206 with the exact `Content-Range`, same ETag and exact body length. `curl --continue-at` is known to corrupt these files (DS-9).
- [x] Verify length, ETag, gzip CRC and ISIZE from the trailer, a SHA-256 pinned in the repo, unpacked byte count, message count, and last message `S`/`C`. There is no zero-length terminator in real files (DS-61). Record that Nasdaq publishes no usable checksum, so the first-download hash is trust-on-first-use, and say which files have a third-party hash to compare with.
- [x] Start the 12302019 and 10302019 downloads early. Third-party reports of throttling to 0.3 to 0.8 MB/s (DS-9). Keep the `.gz`, because the archive is not stable (DS-8).
- [x] Fetch external reference files (CrossStats, MIDAS) by script into `data/`. Never commit them.
- [x] `scripts/check_census.py`: requires `itch_census` to agree exactly with the manifest on the per-type counts, total and last message, shown failing on a mutated count. `scripts/check_real_day.sh` runs it on each pinned day.
- [ ] Same scripts on `debian` under `~/quant-projects/itch-orderbook/data`.

### Platform layer (`bench/platform/`, no `#ifdef` in book or parser)
All of this is written for arm64 and x86-64, Linux and macOS, so that a Linux run needs no code change.
- [x] `timer.hpp`: `lfence; rdtsc` and `rdtscp; lfence` on x86-64; `isb; cntvct_el0` on Linux arm64; `mach_absolute_time` on macOS; `emscripten_get_now` on WASM, batches only. Probe measures the quantum and the empty-pair cost at start of each run. Calibrate on every run, and record the quantum measured rather than the nominal rate.
- [x] `counters.hpp`: `perf_event_open` group with `exclude_kernel` on Linux. `proc_pid_rusage` and `thread_selfcounts` on macOS, cycles and instructions only. Each event ends as measured, unavailable with reason, or multiplexed with ratio. Never fails the build or the run.
- [x] Per-message counts: user-mode `rdpmc` around each message, in a pass that is untimed and separate from timing runs. Also count through `read()` on the event descriptors, and require the two to agree.
- [x] `affinity.hpp`: `pthread_setaffinity_np` with read-back on Linux; `thread_policy_set` tried and its code 46 recorded on macOS, plus `QOS_CLASS_USER_INTERACTIVE`. Returns what happened, and tracks migrations.
- [x] `memory.hpp`: arena allocation with hugepage hint and pre-fault, reporting the page size obtained. `madvise(MADV_HUGEPAGE)` on Linux, 16 KB pages on macOS.
- [x] `sysinfo.hpp`: fills the results header. A field that cannot be read is written as `unavailable: <reason>`.
- [x] Residency check: confirm the replay input is resident before timing on `debian`, or refuse to publish the run.

### Benchmarks
- [x] Google Benchmark plus HdrHistogram, with warm-up and repetitions in fresh processes. Report median, min, max and CV, never best-of alone.
- [x] Results header on every file: CPU, kernel, compiler, flags, commit, dataset and hash, timer info, pinning, governor, frequency, turbo, core, counter status, `measure.sh` load lines. Host always named.
- [x] Separate throughput and latency passes. Publish instrumentation cost as a ratio.
- [x] `bench_probe` target, run at the start of each session. Includes whether user-mode `rdpmc` works at `perf_event_paranoid=2` on `debian`.
- [x] `bench_chase`: random pointer chase, buffers from below L2 to far above L3, to validate `r0843:u` on `debian` before any book number uses it (criterion 32). If it does not behave, publish no last-level number and state the gap.
- [x] `scripts/machine_check.sh`, run before every reported benchmark, with its output embedded in the results file, plus a negative test that breaks one condition at a time (criterion 29). `debian` has no isolated core, so the check records that and enforces what it can: pin read-back, governor, `cpu5` idle, input resident.
- [x] Each experiment's prediction is committed before it runs, and failed runs stay in the log (criterion 31).
- [x] Reported Mac numbers go through `.orchestrator/measure.sh`, and `debian` numbers through `~/quant-projects/bin/measure.sh` with `taskset -c 4` and `cpu5` idle. Output goes to a file on `debian`, not through SSH.
- [ ] README results table generated from results files.
- [x] Gate: every figure in the README and the demo page must appear in its results file, with a negative test that breaks one figure at a time (criterion 30).

### Running on `debian`
- [x] Sync with `.orchestrator/debian_sync.sh itch-orderbook` from committed `HEAD`. Build in `~/quant-projects/itch-orderbook/build`, keep data in `~/quant-projects/itch-orderbook/data`. Work only inside `~/quant-projects`.
- [x] Reported runs: `~/quant-projects/bin/measure.sh taskset -c 4 <benchmark>`, with the benchmark as its direct child. Heavy jobs go through `~/quant-projects/bin/heavy.sh`.
- [x] Keep `cpu5` idle. A second thread goes on `cpu6`. Avoid cores 0, 1 and 3.
- [x] Write output to a file on `debian`, never through SSH. A run with no complete output file is lost: check `~/quant-projects/.logs/measure.log` and rerun it.
- [x] Record core, governor, frequency and turbo state in the header. Calibrate the TSC on every run. Batch or average anything under about 100 ns. `perf` cycles and TSC ticks are different units.
- [ ] Use the liburing 2.15 already built in `.toolchain`. Do not run its `configure`.
- [ ] If `perf` starts failing, `perf_event_paranoid` may have reset on a reboot. Report it, do not change it.

### Tests and oracles
- [x] Parser tests assert field values against hand-written bytes for all 23 types. `B`, `W`, `h`, `N`, `O` need byte fixtures, because real days lack them (DS-77).
- [x] Reader safety tests: truncate a valid stream at every byte offset; final message against a `PROT_NONE` guard page; locate 65535; reference 2^64−1.
- [x] libFuzzer target on every parser and decoder, with committed seed corpus replayed by ctest.
- [x] Book fuzzer has an oracle: invariants plus the reference book after each message. A planted book bug must be caught.
- [x] Reference book: deliberately simple and independently written. Diff design: per-message coverage of the state that can have changed, plus a proof that unchanged state cannot have diverged. No sampling.
- [x] Oracle control: inject a bug into the fast book and show the diff catches it.
- [x] Invariants, strict: book never crossed; every execute or cancel references a live order.
- [x] Allocation test: fails on any allocation in the hot path, counting aligned, nothrow and array `new`, with a control book that allocates.
- [x] Negative controls: a planted race and a planted logic bug, and the run fails if the detector stays green.

### Checks
- [x] ASan and UBSan with no recovery and alignment checks on. TSan for all concurrent code. A sanitizer bug exits 134 on macOS.
- [x] clang-tidy, and `-Wall -Wextra -Werror`.
- [ ] `.github/workflows/ci.yml`: `ubuntu-24.04` (x86-64, GCC and Clang), `ubuntu-24.04-arm` (arm64), `macos-15` (arm64), an Emscripten job. Actions pinned to full SHAs. CI produces no benchmark numbers.
- [ ] Validate CI locally with actionlint from repo tooling. Record that it has never executed on GitHub.
- [x] `scripts/check.sh` runs every check.
- [x] Grep gate: no pointer cast to a multi-byte type over wire bytes, no bare branch hints.
- [x] `scripts/fault_matrix.py`: each check shown failing on an injected fault, with one JSON file per area under `scripts/faults/`, a clean control and a self-test. Results in `results/fault_matrix.md`.
- [ ] Phase 2 report

## Phase 3: Implementation

### Parser
- [x] `read_be<T>`: `memcpy` plus `std::byteswap`. `read_be48` reads exactly 6 bytes. No `start_lifetime_as`, no `std::simd`.
- [x] Frame length checked before decode, typed failures, unknown types skipped by length, wire-controlled indexes bounded. No assert, throw or abort reachable from wire content.
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
- [ ] Repeat carteret's layout experiments on our book: hash policy, open addressing against chaining, window width against overflow. Attribute last-level misses by message type, order age and structure before claiming a bottleneck.
- [ ] Hot structs: `static_assert` on size, alignment, trivial copyability.
- [ ] T-BOOK-1 to T-BOOK-4, T-LAT-1 to T-LAT-5, T-DESIGN-1 on the harness.
- [x] Strict crossed-book run, full day. Count and characterise each violation by symbol, time and trading state. Report both published counts' populations: all observations, and market open with state `T`. The user decides the scope. Done on the Phase 2 stub: `results/oracle_12302019.md`, strict result FAIL.
- [x] Oracle replay on the Mac under `heavy.sh`, all 268,744,780 messages (T-ORACLE-1). Done on the Phase 2 stub: `results/oracle_12302019.md`.
- [ ] Repeat T-ORACLE-1, T-INV-1, T-INV-2 and T-ALLOC-1 on 12302019 with the real book, once it replaces the stub.
- [ ] Repeat the oracle on every session the archive will serve, up to ten, keeping raw output for each (T-ORACLE-2). Check free disk first. State any shortfall as a gap.

### Trade-print verification
Hard means any mismatch fails. Each check needs an injected bug that trips it. Check 17 in the evidence table only tests our aggregator, so it must not be cited as an independent result.
- [x] `E`, `C` reference a live order, on this locate, `shares <= remaining`. Hard. T-INV-1.
- [x] `X`, `D`, `U` same liveness, and `U`'s new reference unused. Hard. T-INV-1.
- [x] Per-order `Add` shares equal the sum of deductions; book empty at `S/C` (T-TRADE-1). Hard. Done on the Phase 2 stub, both days.
- [x] `E` at best price on its side, with market open and state `T` (T-TRADE-5). Hard. Count the rest. Done on the Phase 2 stub, both days.
- [x] `E` at queue front: reported as a rate. Published 99.8141%, 99.82% and 99.914%, unexplained remainder.
- [x] `C` deducts from the order and leaves its price unchanged. Hard for the deduction, with counts by `Printable`.
- [x] `P`: reference 0, side `B`, no book change. Hard. Whole class excluded from "hits a resting order": count and surface.
- [x] `Q` per symbol against CrossStats (T-TRADE-2). Hard. Opening shares against Opening, closing against Closing. Halt crosses: sum of `Q` outside CrossStats equals type `H` sum. Test symbols have no row, and are counted apart. Done on 12302019. Not available on 10302019: no CrossStats file for it is pinned.
- [x] `C_N` against `Q`: per symbol and side `<=`, equal price. Measure first, because no one has confirmed this on real data.
- [ ] `E + C_Y + P` against MIDAS (T-TRADE-3): exact per-symbol equality once the window is pinned. If it cannot be pinned, report the per-symbol differences as an open gap with the reason. An inequality is never a pass. Add `P` against `HiddenVol`. Open: shares, trades, hidden shares and hidden trades are exact on every symbol on both days at 09:35 to 16:00, and P against HiddenVol with them, but the odd-lot columns fail on MKL and NVR on both days. See `results/trades_12302019.md`, `results/trades_10302019.md` and NOTES.md. The user decides which round lot the check compares.
- [ ] Cboe per-tape check (T-TRADE-4): held. No Cboe data in this project until the user decides. Scripts do not fetch it, nothing depends on it, and no further joins are run.
- [x] `B` reverses a stored match, hard on 10302019, fixtures otherwise.
- [x] Match numbers unique across `E`, `C`, `P`, `Q`. Measure first. Measured on both days: unique across the print classes. A far-leg `C_N` shares its number with one print, by design, and is checked against it.
- [ ] Surface class-level counts in the phase report: test symbols, zero-share `Q`, `C_N`, `P`, `B`.

### Low-latency components
Each benchmarked alone, then measured inside the book or backtester.
- [ ] SPSC ring: padding from one constant that is 128 on Apple Silicon. Cached indices, mask, batch publish.
- [ ] SPSC comparison: project queue, rigtorp, boost::lockfree, moodycamel, folly, atomic_queue, xenium Vyukov as MPMC control, mutex ring as floor.
- [ ] SPSC ablation, one change at a time (T-SPSC-2). Protocols: ping-pong RTT, saturated one-way, paced one-way with percentiles, bursty replay.
- [ ] Correctness: TSan on both hosts, arm64 stress without TSan, negative control with weakened ordering. It is unconfirmed whether stress catches this on M-series (LC-37), so report what happens.
- [ ] Parser thread → queue → book thread on a real day, against single-threaded replay (T-SPSC-3). If single-threaded wins, that is the finding.
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
- [ ] Fees keyed by date, per fill, in integers. Base tier stated. Tape comes from Stock Directory Market Category. carteret's mapping is Q, G, S to C; N to A; A, P, Z, V to B. Check it against the spec first.
- [ ] All 48 scenarios from `backtester.md` as tests (T-BT-2).
- [ ] Lookahead: prefix invariance, future perturbation, oracle control must be caught.
- [ ] T-BT-1 and T-BT-3 on the harness. T-BT-3 reproduces queuetruth's workload: AAPL, MSFT, SPY, TSLA, 100,000 shadow orders.
- [ ] State the limit: no market impact.

### Market maker
- [ ] Avellaneda-Stoikov as the demo, with the finite-horizon form as named by the user. GLFT stationary form as a labelled variant, because the evidence recommends it on tick-constrained stocks.
- [ ] Commit symbols, γ, h = 15 s and order size before the first evaluation run. Selection cannot look at the result.
- [ ] Calibrate σ, A, k from the market tape on 10302019 only. Report on 12302019. Label both. 10302019 is an FOMC day, and 12302019 is a late-December session.
- [ ] Attribution in a module that never reads the ledger. Integers, `==`, no tolerance (T-MM-1).
- [ ] Property tests 1 to 16 from `market-maker.md`, including the negative test.
- [ ] Replication of the AS tables (T-MM-2).
- [ ] Four runs, one switch at a time: Base, Q-off, L-off, Both-off. Same mark path hash in all. Q-off must name its fill rule. Published bias runs from −21.2% (conservative) to −0.7% (proportional), so report both as separate rows.
- [ ] Report: S gross, fees, A, I, total, in dollars, per share and in ticks. Markout curve. No return, no Sharpe. Fixed sentence: "One symbol-day. No edge is claimed."

### Counters and flamegraphs
- [ ] `perf` on `debian`, `:u` events. Publish which counters are real hardware events on Zen 1, with what each counts.
- [x] Confirm `cache-misses` against AMD's documentation: it counts L2 misses. Any write-up must call it that.
- [ ] DRAM fills per message with `r0843:u`, by message type, once `bench_chase` validates it (T-PERF-1).
- [ ] Branch misses per message, reported with no target (T-PERF-2).
- [ ] Separate frame-pointer build, with its throughput delta published.
- [ ] `perf record -g --user-callchains -e cycles:u`, then FlameGraph, for each hot path. User-space only at `perf_event_paranoid=2`: kernel stacks are a listed gap, and no system setting is changed. `perf annotate` beside it.
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
