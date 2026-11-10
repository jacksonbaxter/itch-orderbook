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
- [x] CI calls presets only.
- [x] C++23. Warnings: `-Wall -Wextra -Werror -Wconversion -Wsign-conversion -Wshadow -Wold-style-cast`.
- [x] Take the compiler from the environment. No hardcoded toolchain path in repo scripts.
- [x] Pinned FetchContent, by hash, for GoogleTest, Google Benchmark and HdrHistogram_c. Each commit resolves to its release tag and each archive matches its hash. Nothing calls the benchmark dependencies yet.
- [x] Pinned FetchContent, by hash, for the comparison queues. liburing on Linux only: use the 2.15 build in `.toolchain` on `debian`. macOS builds compile with it off, with no code change.
- [x] `.clang-tidy` with warnings as errors, at a pinned version.
- [x] Avoid `<generator>` and `<stacktrace>`, which do not exist in this libc++.

### Data
- [x] `scripts/fetch_data.py`: chunked Range download into `data/`. Each chunk must be status 206 with the exact `Content-Range`, same ETag and exact body length. `curl --continue-at` is known to corrupt these files (DS-9).
- [x] Verify length, ETag, gzip CRC and ISIZE from the trailer, a SHA-256 pinned in the repo, unpacked byte count, message count, and last message `S`/`C`. There is no zero-length terminator in real files (DS-61). Record that Nasdaq publishes no usable checksum, so the first-download hash is trust-on-first-use, and say which files have a third-party hash to compare with.
- [x] Start the 12302019 and 10302019 downloads early. Third-party reports of throttling to 0.3 to 0.8 MB/s (DS-9). Keep the `.gz`, because the archive is not stable (DS-8).
- [x] Fetch external reference files (CrossStats, MIDAS) by script into `data/`. Never commit them.
- [x] `scripts/check_census.py`: requires `itch_census` to agree exactly with the manifest on the per-type counts, total and last message, shown failing on a mutated count. `scripts/check_real_day.sh` runs it on each pinned day.
- [x] Same scripts on `debian` under `~/quant-projects/itch-orderbook/data`. `fetch_data.py` fetched 12302019 there and `--verify-only` passed against the pin. 10302019 was not fetched there because no Phase 2 job needs it, and `check_real_day.sh` stays on the Mac because the per-message oracle replay does.

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
- [x] README results table generated from results files.
- [x] Gate: every figure in the README and the demo page must appear in its results file, with a negative test that breaks one figure at a time (criterion 30).

### Running on `debian`
- [x] Sync with `.orchestrator/debian_sync.sh itch-orderbook` from committed `HEAD`. Build in `~/quant-projects/itch-orderbook/build`, keep data in `~/quant-projects/itch-orderbook/data`. Work only inside `~/quant-projects`.
- [x] Reported runs: `~/quant-projects/bin/measure.sh taskset -c 4 <benchmark>`, with the benchmark as its direct child. Heavy jobs go through `~/quant-projects/bin/heavy.sh`.
- [x] Keep `cpu5` idle. A second thread goes on `cpu6`. Avoid cores 0, 1 and 3.
- [x] Write output to a file on `debian`, never through SSH. A run with no complete output file is lost: check `~/quant-projects/.logs/measure.log` and rerun it.
- [x] Record core, governor, frequency and turbo state in the header. Calibrate the TSC on every run. Batch or average anything under about 100 ns. `perf` cycles and TSC ticks are different units.
- [ ] Use the liburing 2.15 already built in `.toolchain`. Do not run its `configure`. Open: `-DITCH_LIBURING_ROOT` configures against the 2.15 build on `debian`, and no Phase 2 code calls it. The first use is the io_uring work in Phase 3.
- [ ] If `perf` starts failing, `perf_event_paranoid` may have reset on a reboot. Report it, do not change it. Open: a standing rule with nothing to finish. It has not fired: the last `debian` run recorded `perf_event_paranoid=2`.

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
- [x] `.github/workflows/ci.yml`: `ubuntu-24.04` (x86-64, GCC and Clang), `ubuntu-24.04-arm` (arm64), `macos-15` (arm64), an Emscripten job. Actions pinned to full SHAs. CI produces no benchmark numbers.
- [x] Validate CI locally with actionlint from repo tooling. Record that it has never executed on GitHub.
- [x] `scripts/check.sh` runs every check.
- [x] Grep gate: no pointer cast to a multi-byte type over wire bytes, no bare branch hints.
- [x] `scripts/fault_matrix.py`: each check shown failing on an injected fault, with one JSON file per area under `scripts/faults/`, a clean control and a self-test. Results in `results/fault_matrix.md`.
- [x] Phase 2 report

## Phase 3: Implementation

### Parser
- [x] `read_be<T>`: `memcpy` plus `std::byteswap`. `read_be48` reads exactly 6 bytes. No `start_lifetime_as`, no `std::simd`.
- [x] Frame length checked before decode, typed failures, unknown types skipped by length, wire-controlled indexes bounded. No assert, throw or abort reachable from wire content.
- [x] Handle what the spec omits: Market Category `M` (DS-64), test symbols (DS-65), round lots other than 100 (DS-66), zero-share and zero-price `Q` (DS-69), `X` after system event `E` (DS-71). Counted on both days by `itch_census`. `M` and post-`E` `X` occur on neither, so fixtures alone exercise them. See NOTES.md, 2026-11-02.

### SIMD (both architectures, same CMake, no code change)
- [ ] One kernel header per ISA in `include/itch/kernels/`: `scalar`, `neon`, `ssse3`, `avx2`, `avx512vbmi`, `wasm`, each in its own namespace. Tests compare every kernel with `scalar` on random and real bytes. Built, with every header and `tests/test_kernels.cpp` (one ctest entry for each kernel; a kernel the CPU lacks shows as skipped). `scalar` and `neon` pass on the Mac, and `wasm` passes against scalar in node (`itch_wasm_kernels_smoke`). Left: the x86 entries have never run, since there is no Rosetta and no GCC on the Mac. They run first in CI's two x86-64 Linux jobs, and on `debian`, where `avx512vbmi` is always skipped (NOTES.md, "SIMD parsing kernels").
- [x] arm64: compile-time only, NEON guaranteed, no dispatch code. CMake's arch directory (`include/itch/kernels/arch/arm64/`) names scalar and NEON, and there is no `#if` and no runtime check.
- [ ] x86-64: whole hot loop inside a `target` region, selected once at start with `__builtin_cpu_supports`. No `-march=native` in the default build. Built: each kernel's `run()` sits under `[[gnu::target]]` with the work `always_inline`, and clang's x86-64 output shows the loop inlined with no call. Left: it has never run (see above).
- [x] `--kernel={scalar,neon,ssse3,avx2,avx512vbmi,wasm,auto}`: all comparisons in one binary on one dataset. An unsupported kernel exits with an error, never a silent fallback. `bench_kernels` exits 2 with the reason (unknown, not built, or what the CPU lacks), writes no record, and has `--list-kernels`; `tests/test_bench_kernels_cli.py` checks it.
- [x] Kernels declare `kOverread`. The parser takes the scalar path near the end of the mapped range. Every kernel declares 0 except AVX-512 VBMI, which declares 45. `parse_ops()` hands a message within `kOverread` of the end to scalar and counts it, and a guard-page test covers each op type as the last frame.
- [x] WASM kernel uses `wasm_simd128.h` directly. It has no byte-reverse in core, so the sign of the result may flip (E11). E11 (P-0609): SIMD128 is 2.08 times scalar in node. The sign matches native E2, and is flipped against native's Add Order inside the parse loop (`results/simd_e11_mac_12302019.md`).
- [ ] Experiments E1 to E9, E11 to E13 from `simd.md`. Record the instruction set of every number. NEON and AVX2 are different experiments. Done on the Mac: E1 to E6, E9, E12 and E13, NEON against scalar, and E11 in node (P-0601 to P-0610, `results/simd_*_mac*.md`). E7 and E8 were done earlier. Left: the x86 kernels (SSSE3, AVX2) on `debian`, and E12's GCC column. The commands are in NOTES.md.
- [x] E10 needs a Skylake-family part. Zen 1 cannot show it. Report as not measured. Reported as not measured in NOTES.md, "SIMD parsing kernels".
- [ ] Say which wins, and explain with counters. A tie or a SIMD loss is the finding. On the Mac:
  - NEON wins a decode into memory (E2, 1.94 times).
  - The gain shrinks to 7.6% in integer registers (E3) and 6.6% for the whole parser (E4), while NEON is slower on every op type alone.
  - Scalar wins the four-wide batch (E5). The bitset beats any compare (E9).
  - With cycles, instructions and E12's listings, NOTES.md has the explanation.
  - The default stays scalar.

  Left: the x86 answer, on `debian`, with branch and cache counters.

### Book
- [x] Order lookup: hash and paged direct index, measured apart from level search. References fit 32 bits on observed days, which is not a guarantee (DS-72). Both indexes are measured as arms on both hosts with each level arm held fixed: the paged index is 1.11 and 1.19 times the hash on `debian`, 1.15 and 1.24 on the Mac. Done: the lookup's own cost where it sits, a check pass against the real index less an answer pass from a recorded script (P-0020): the paged index 30.71 ns and the hash 32.50 ns a lookup on `debian`, 8.95 and 5.03 ns on the Mac (`results/lookup_{debian,mac}_12302019.md`).
- [x] Levels: flat with best price at the end; FIFO chains; `std::map` levels on fresh and aged heaps as baseline. The `std::map` baseline is built (`include/itch/map_book.hpp`, aged by `include/itch/heap_ager.hpp`) and agrees with the fast book on the fixtures and both days. Every level arm and both heaps are measured on the Mac on 12302019 (`results/bench_book_*_mac_12302019.md`), and every level arm on `debian`, mapped with the top tracked (`results/bench_book_*_debian_12302019.md`). Done: the map baseline on `debian` ran in P-0015 with the day in memory, throughput only (`results/bench_book_map-*_ram_debian_12302019.md`).
- [x] Sparse handling for stub quotes (DS-73). Neither level arm indexes a level by its price: both search by comparing prices, and a far quote is one more level at the far end. `BookScenario.AFarStubQuoteIsTheFarthestLevel` runs a $199,999.99 ask and a $0.0001 bid on every arm.
- [ ] Replace, delete and add measured on 8.7% `U` (2019) and 27.7% `U` (2026) mixes.
- [x] Prefetch lookahead D in {0, 4, 8, 16, 32, 64} (E8), and dispatch form (E7). Each hint keeps a measured reason in a comment. E8 (P-0021): not refuted on `debian`, +16.41% at D = 16; refuted on the Mac, slower at every D; kept as an option, off by default. E7 (P-0022): not refuted by the letter on `debian` (branch misses spread 0.0702 a message, the indirect forms higher), every form within 1% of `switch` on both hosts; the `switch` stays (`results/{prefetch,dispatch}_{debian,mac}_12302019.md`).
- [ ] Repeat carteret's layout experiments on our book: hash policy, open addressing against chaining, window width against overflow. Attribute last-level misses by message type, order age and structure before claiming a bottleneck.
- [x] Hot structs: `static_assert` on size, alignment, trivial copyability. `BookOrder`, `LinkedLevel`, `FlatLevel` and each arm's per-side record.
- [x] Measure the four book arms (hash or paged index, linked or flat levels) against each other and pick the default that ships. Done: `paged-flat` is fastest on both hosts, on the Mac in memory and mapped with the top tracked, and on `debian` mapped with the top tracked, and holds 0.69 GB, under T-BOOK-3. `FastBook<>` is now the paged index with flat levels. NOTES.md, "The default arm". `itch_oracle` and `itch_alloc` take `--index` and `--levels`.
- [x] Full-day oracle and T-ALLOC-1 on 12302019 for the three arms other than the default, under `heavy.sh`. Done on both days, with the trade checks, every arm line for line the same as the default: `scripts/check_real_day.sh --arms all`, `results/oracle_*.md` and `results/trades_*.md`.
- [x] Size `PagedIndex` for a day before it is measured. Done: 428,032 pages (418 MiB of slots), the larger of both days' peaks, 340,939 on 12302019, with a quarter more, and a reach of 2^30 (16 MiB of directory), past three times the largest reference on either day. Both paged arms pass the oracle and T-ALLOC-1 on both days with no fallback. NOTES.md, "Sizing the paged index".
- [ ] T-BOOK-1 to T-BOOK-4, T-LAT-1 to T-LAT-5, T-DESIGN-1 on the harness. `bench_replay` and the driver measure every design and every one of these but T-BOOK-4. The Mac runs on 12302019 are in `results/` and NOTES.md, "Measuring every book design". `debian` ran the four fast arms mapped with the top tracked (P-0011, and P-0013 with counters): T-BOOK-2 and T-LAT-1, 2, 4 and 5 missed on that host, T-LAT-3's p99.9 missed on every arm, T-BOOK-3 met. T-BOOK-1 and T-DESIGN-1 on `debian` came from P-0015 with the day in memory: T-BOOK-1 missed by every arm, `paged-flat` at 5.15 M messages/s against 16.4 M; T-DESIGN-1 missed by every arm, the best `paged-flat` at 2.54× over `map-fresh` against 4.0× (NOTES.md, "P-0015"). Left: T-BOOK-4, which needs 01302019.
- [x] Make the in-memory mode fit on `debian`: copy the day in pieces and drop each piece's page cache as it goes, so that the file and its copy are never both whole in memory, then run P-0010 again for T-BOOK-1 and T-DESIGN-1 there. The copy is done (64 MiB pieces, `POSIX_FADV_DONTNEED` each, an arena its reads fault in) and the day's cache stayed empty, but P-0012 was refused after one page was swapped out during `map-aged`, and P-0014, the fast arms alone, never got `measure.sh`'s lock before the 04:02 window. NOTES.md, "The day in memory on `debian`". Done: P-0015 ran every design with the day in memory and published, `pswpout` still from start to end, after a first attempt refused for 2 pages; NOTES.md, "P-0015".
- [x] The counter pass on `debian` for the book designs, once `perf_event_paranoid` is 2 again. Done for the four fast arms, mapped with the top tracked (P-0013, `results/bench_book_*_mapped_top_counters_debian_12302019.md`): every event measured, `rdpmc` agreeing with `read()`. The map designs have no `debian` counters, since they ran only in refused runs.
- [x] `bench_driver.py` writes no run-log row when it is signalled. Write the row before exiting, with a fault. Done: SIGTERM, SIGINT and SIGHUP are passed to the bench, the row is written with where the run was, and the exit is 128 plus the signal; three faults. The driver also refuses a run during which `pswpout` moves, with two faults.
- [x] Strict crossed-book run, full day. Count and characterise each violation by symbol, time and trading state. Report both published counts' populations: all observations, and market open with state `T`. The user decides the scope. Done on the Phase 2 stub: `results/oracle_12302019.md`, strict result FAIL.
- [x] T-INV-3, the scope the user approved after seeing the strict result: a book in normal trading may only be crossed if the cross is left over from a halt and has not cleared. Separate from T-INV-2, which stays strict. Done on the Phase 2 stub on both days: `results/oracle_12302019.md` and `results/oracle_10302019.md`, 0 violations on each.
- [x] Oracle replay on the Mac under `heavy.sh`, all 268,744,780 messages (T-ORACLE-1). Done on the Phase 2 stub: `results/oracle_12302019.md`.
- [x] Repeat T-ORACLE-1, T-INV-1, T-INV-2, T-INV-3 and T-ALLOC-1 on 12302019 with the real book, once it replaces the stub. Done on both days and all four arms, with T-TRADE-1, 2, 3, 5 and PRINTS: the same verdicts and counts as the stub, T-INV-2 strict still failing.
- [x] Repeat the oracle on every session the archive will serve, up to ten, keeping raw output for each (T-ORACLE-2). Check free disk first. State any shortfall as a gap. Done: carteret's ten sessions, 0 divergences in 3,081,392,329 messages, on `paged_flat` for the eight new ones (`results/oracle_sessions.md`, NOTES.md, "T-ORACLE-2 on carteret's ten sessions"). T-TRADE-3 failed on four of the new days and T-TRADE-5 on two, which NOTES.md describes.

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
- [x] `E + C_Y + P` against MIDAS (T-TRADE-3): exact per-symbol equality once the window is pinned. If it cannot be pinned, report the per-symbol differences as an open gap with the reason. An inequality is never a pass. Add `P` against `HiddenVol`. Done on the Phase 2 stub on both days at 09:35 to 16:00, every column on every symbol, with odd lots on the SEC's round-lot rule, which the user chose after the failures recorded in NOTES.md. See `results/trades_12302019.md` and `results/trades_10302019.md`.
- [ ] Cboe per-tape check (T-TRADE-4): held. No Cboe data in this project until the user decides. Scripts do not fetch it, nothing depends on it, and no further joins are run.
- [x] `B` reverses a stored match, hard on 10302019, fixtures otherwise.
- [x] Match numbers unique across `E`, `C`, `P`, `Q`. Measure first. Measured on both days: unique across the print classes. A far-leg `C_N` shares its number with one print, by design, and is checked against it.
- [ ] Surface class-level counts in the phase report: test symbols, zero-share `Q`, `C_N`, `P`, `B`.

### Low-latency components
Each benchmarked alone, then measured inside the book or backtester.
- [x] SPSC ring: padding from one constant that is 128 on Apple Silicon. Cached indices, mask, batch publish. `include/itch/spsc.hpp`, the constant from `cmake/ItchCacheLine.cmake` (128 on Apple silicon, 64 on x86-64 and other arm64), each technique a compile-time switch, no allocation after construction. NOTES.md, "The SPSC ring".
- [x] SPSC comparison: project queue, rigtorp, boost::lockfree, moodycamel, folly, atomic_queue, xenium Vyukov as MPMC control, mutex ring as floor. Pinned behind `ITCH_COMPARISON_QUEUES`; measured on the Mac, unpinned and under the sampler rule (P-0301, `results/spsc_queues_mac_12302019.md`), and on `debian` pinned to cores 4 and 6 (P-0306, `results/spsc_queues_debian_12302019.md`), where all three T-SPSC-1 bars are missed.
- [x] SPSC ablation, one change at a time (T-SPSC-2). Protocols: ping-pong RTT, saturated one-way, paced one-way with percentiles, bursty replay. Measured on the Mac, unpinned and under the sampler rule (P-0302, `results/spsc_ablation_mac_12302019.md`), and on `debian` pinned (P-0307, `results/spsc_ablation_debian_12302019.md`), where all four bars are missed.
- [x] Correctness: TSan on both hosts, arm64 stress without TSan, negative control with weakened ordering. It is unconfirmed whether stress catches this on M-series (LC-37), so report what happens. Done on the Mac: TSan on the shipped orders, and each of the four weakened orders caught by TSan on the word handover; the arm64 stress catches a weakened publish or observe and not a weakened return or reclaim (NOTES.md, "The SPSC ring"). Done on `debian` (P-0309): TSan passes the shipped orders and catches each weakened order on both the word and the item test; the x86 stress catches none of the four, as TSO predicts; and the cross-core check of the TSC between cores 4 and 6 (P-0305, `bench_probe --cross-core`) agrees within about 40 ns before every one-way latency there (NOTES.md, "SPSC under the sampler rule, and on `debian`").
- [x] Parser thread → queue → book thread on a real day, against single-threaded replay (T-SPSC-3). If single-threaded wins, that is the finding. `bench/pipeline.hpp`, `bench_pipeline`, on 12302019 on the Mac, unpinned and under the sampler rule (P-0304, `results/spsc_pipeline_mac_12302019.md`), and on `debian` with the book on core 4 and the parser on core 6 (P-0308, `results/spsc_pipeline_debian_12302019.md`), met at 0.95×; the book equal by hash at the close and at a mid-day checkpoint, since the closing book is empty.
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
- [x] Event ordering with `L_md`, `L_oe`, `L_resp`, `L_compute`. A simulated order joins at arrival, not at decision. `include/itch/backtest.hpp`; base values 25 µs each way and 5 µs compute, all in `Config::latency`. S10 to S17, S36.
- [x] Queue model from real L3 order references: `ahead` set from the real queue at arrival, falling only with depletion ahead. S01 to S09, and S08's property test against a recount after every message.
- [x] Partial fills from real executions behind S. Share ledger prevents a real order filling S twice. S18 to S29, S41.
- [x] Fees keyed by date, per fill, in integers. Base tier stated. Tape comes from Stock Directory Market Category. carteret's mapping is Q, G, S to C; N to A; A, P, Z, V to B. Check it against the spec first. `include/itch/fees.hpp`, picodollars, 20191030, 20191230 and 20260612. The spec gives listing markets, not tapes; Regulation NMS gives tapes by listing market, and the two confirm the mapping. NOTES.md, "The backtester".
- [x] All 48 scenarios from `backtester.md` as tests (T-BT-2). `tests/test_backtest.cpp`, `tests/test_fees.cpp`, `tests/test_backtest_validity.cpp`, each with hand-written expected fills.
- [x] Lookahead: prefix invariance, future perturbation, oracle control must be caught. S42 to S44: the oracle is caught at 20 of 20 cuts by each check; two injected engine lookahead faults are caught (`scripts/faults/backtester.json`).
- [x] T-BT-1 and T-BT-3 on the harness, on the Mac. T-BT-3 reproduces queuetruth's workload: AAPL, MSFT, SPY, TSLA, 100,000 shadow orders. `bench/bench_backtest.cpp` and `scripts/backtest_driver.py`: T-BT-1 16.79 M messages/s of the whole day with the quoter, host differs from the x86 bar; T-BT-3 7.80 M messages/s of the four-symbol extract, met. `results/backtest_*_mac_12302019.md`, NOTES.md, "T-BT-1 and T-BT-3 on the Mac".
- [ ] T-BT-1 on `debian`, the headline host, through `scripts/backtest_driver.py --workload quoting --core 4` under `measure.sh taskset -c 4`, with its own prediction, once the host is free. The mapped day fits there, as the mapped book runs did (peak RSS 9.15 to 9.59 GiB). T-BT-3 there too, as context, since its bar was set on a Mac.
- [x] State the limit: no market impact. In the header comment of `include/itch/backtest.hpp` and NOTES.md.
- [x] Replayed book identical with and without simulated orders (S46, per message, and `itch_backtest` on 12302019), identical event-log hash on repeat (S47), and no allocation after construction (`test_alloc`, and `itch_backtest` on 12302019).
- [x] Faults in `scripts/faults/backtester.json`, 22, for the queue rule, ledger, latency order, lookahead, replay identity, fees, allocation and the tool's checks. Each caught.
- [x] `itch_backtest`: a whole day through the engine with a trivial strategy, every check passing on 12302019 under `heavy.sh`. Not a benchmark.
- [x] Queue anomalies on real data: on 12302019, 1,347 executions on the traded symbols hit an order that joined after a resting simulated order while earlier orders at that price still rested, by a recount from the real book. Settled by `tools/itch_queue_jumps` on both days: every execution hit the lowest reference at its level, every jump was an order held for the open and shown late (Rules 4752(c), 4757(a)(1)), and every printable `C` traded at a price better for the aggressor than shown. A late add now goes ahead of every simulated order, and a printable `C` reaches only its trade price: 0 anomalies on the smoke. S49, S50, seven faults. NOTES.md, "Queue jumps".
- [ ] T-BT-3's 100,000 shadow orders: the simulated levels of a side are a list walked from the best, so many levels on one side cost a walk each. At T-BT-3's schedule about 257 orders are live at once over eight sides, mostly at one price each, and the run is 128 ns a message; the walk's share of that is not measured. Measure before changing it, with a schedule that holds far more live orders.
- [x] Report `hidden_prints_at_sim_price` with T-BT-1 as a bound on fills the model cannot see (6,331 on the 12302019 smoke). In each results file's counts: 5,068 in T-BT-1 and 7,109 in T-BT-3.

### Market maker
- [x] Avellaneda-Stoikov as the demo, with the finite-horizon form as named by the user. GLFT stationary form as a labelled variant, because the evidence recommends it on tick-constrained stocks. `include/itch/mm/quoting.hpp` and `strategy.hpp`, on the backtester's strategy interface, integer prices from the rounding on, no allocation after construction (`test_alloc`). Run on 12302019, P-0202, `results/mm_pnl_12302019.md`. NOTES.md, "The market maker".
- [x] Commit symbols, γ, h = 15 s and order size before the first evaluation run. Selection cannot look at the result. AMD and BYND by MK-75 on 10302019 (`tools/itch_select`), γ = 1/(σ²·21,600 s), h = 15 s, 100 shares, at most 5 lots: `scripts/mm_evaluation.json`, c65c24c, before any run on 12302019. NOTES.md, "The market maker's evaluation".
- [x] Calibration tool: `tools/itch_calibrate` reads a day and a symbol list and writes σ (on a clock and from jumps), A and k (deepest aggressor depth per interval, on the depths a quote could take, OLS on log λ) with every count behind them. Tested on a hand-built day with every figure worked by hand (`tests/test_calibrate_cli.py`), eight faults. Run on 10302019 for the evaluation.
- [x] Calibrate σ, A, k from the market tape on 10302019 only. Report on 12302019. Label both. 10302019 is an FOMC day, and 12302019 is a late-December session. Committed in c65c24c; the report is `results/mm_pnl_12302019.md`, which labels both.
- [x] Attribution in a module that never reads the ledger. Integers, `==`, no tolerance (T-MM-1). `include/itch/mm/attribution.hpp`, per-fill and path forms; the gate `attribution-without-ledger` holds it and its includes away from `ledger.hpp`.
- [x] Property tests 1 to 16 from `market-maker.md`, including the negative test. `tests/test_attribution.cpp` (1 to 14 and the worked example), `tests/test_market_maker.cpp` (15 and 16); each caught a fault of its own in `scripts/faults/marketmaker.json`. Test 15 covers both Q-off rules, L-off, the model and γ: one mark-path hash in all 24 runs.
- [x] Replication of the AS tables (T-MM-2). P-0201, `results/mm_as_tables.md`: average spread 1.4918, 1.3490, 3.0317 against 1.49, 1.35, 3.02, so FAIL at γ = 1 by 0.01, from the time grid as predicted (the 201-point grid gives 3.0217); 23 of 24 other columns within two standard errors.
- [x] Four runs, one switch at a time: Base, Q-off, L-off, Both-off. Same mark path hash in all. Q-off must name its fill rule. Published bias runs from −21.2% (conservative) to −0.7% (proportional), so report both as separate rows. P-0202 on 12302019: AS under Base, Q-off conservative, Q-off proportional, L-off and both off with each rule, and GLFT Base, on AMD and BYND; one hash a symbol in its seven runs. `Config::queue`, `Config::latency_off`, `tools/itch_mm`.
- [x] Report: S gross, fees, A, I, total, in dollars, per share and in ticks. Markout curve. No return, no Sharpe. Fixed sentence: "One symbol-day. No edge is claimed." `results/mm_pnl_12302019.md`, written by `scripts/mm_report.py` and held to its tool output by `mm_report.py check` in check.sh, with the identity checked with == in all 14 runs.

### Counters and flamegraphs
- [x] `perf` on `debian`, `:u` events. Publish which counters are real hardware events on Zen 1, with what each counts. P-0016, results/counters_zen1_events_debian.md.
- [x] Confirm `cache-misses` against AMD's documentation: it counts L2 misses. Any write-up must call it that.
- [x] DRAM fills per message with `r0843:u`, a floor on DRAM demand fills, by message type, once `bench_chase` validates it (T-PERF-1). P-0019: 2.002 per book message on `paged-flat`, FAIL against 0.623, with the cost of counting beside it.
- [x] Branch misses per message, reported with no target (T-PERF-2). P-0019: 2.102, over the 2.04 floor.
- [x] Separate frame-pointer build, with its throughput delta published. P-0017: 0.9755 times the normal build.
- [x] `perf record -g --user-callchains -e cycles:u`, then FlameGraph, for each hot path. User-space only at `perf_event_paranoid=2`: kernel stacks are a listed gap, and no system setting is changed. `perf annotate` beside it.
- [x] Mac: Apple tooling without sudo where it works, labelled macOS. Xcode is absent, so `xctrace` is unavailable now. It does not replace the `perf` data.

### Browser demo
- [x] Replay engine with `em++`, `-O3 -msimd128`. Fixed initial memory. `FastBook<>`, the shipped `paged-flat` arm at its default capacity, in a fixed 1 GiB that never grows; the same digest as native. NOTES.md, "The browser demo".
- [x] Static page in `docs/`: animated depth and trades for one symbol, messages/sec from batches.
- [x] Banned styling: cream or off-white background, italic accent words in headings, numbered "01 / 02 / 03" labels, pill buttons, gradient heroes. Five gates in `scripts/gates.py`, each with a fault.
- [x] Extract generated by script into a gitignored path. The page says what to run if it is missing. No market data in git, real or synthetic. No public deployment of real extracts without Nasdaq's written approval.
- [x] Native Linux numbers from a `debian` results file. The page shows them as missing until they exist. No Mac numbers in that slot.
- [x] State plainly that WASM cannot show true low latency, and why.
- [x] Runs locally from `docs/` over a local HTTP server, because `file://` blocks loading.
- [x] T-WASM-1, labelled macOS. 1.19× native in Google Chrome for Testing 153.0.8010.12, headless: Google Chrome is not installed, so the verdict is "browser differs". The first pair was withdrawn under the sampler rule. `results/wasm_replay_mac_12302019.md`.

### Write-up and iteration
- [x] CI green on GitHub. The first run, at 2810183, failed both GCC jobs on a `-Wconversion` error only Linux shows, and the x86-64 Clang job on the LLVM archive's 1 GiB zstd window. Fixed, and every job passed at da1a707. NOTES.md, "CI's first runs on GitHub".
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
