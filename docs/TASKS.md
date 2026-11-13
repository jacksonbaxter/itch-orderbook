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
- [x] Write BENCHMARKS.md, docs/TASKS.md, docs/NOTES.md
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
- [x] Use the liburing 2.15 already built in `.toolchain`. Do not run its `configure`. Done: `-DITCH_LIBURING_ROOT`, or `ITCH_LIBURING_ROOT` in the environment, configures against the 2.15 build on `debian`, its `configure` never run, and the io_uring modes U1 to U5 are its first use. docs/NOTES.md, "T-URING-1 and T-URING-2 on `debian`".
- [ ] If `perf` starts failing, `perf_event_paranoid` may have reset on a reboot. Report it, do not change it. Open: a standing rule with nothing to finish. It has not fired: the last `debian` run recorded `perf_event_paranoid=2`. Closed, not done, by the user's ruling of 2026-11-13: no host change is approved beyond the governor write in `measure.sh`, so `perf_event_paranoid` stays 2 and kernel stacks stay a listed gap (each flamegraph file's "What it cannot show").

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
- [x] Handle what the spec omits: Market Category `M` (DS-64), test symbols (DS-65), round lots other than 100 (DS-66), zero-share and zero-price `Q` (DS-69), `X` after system event `E` (DS-71). Counted on both days by `itch_census`. `M` and post-`E` `X` occur on neither, so fixtures alone exercise them. See docs/NOTES.md, 2026-11-02.

### SIMD (both architectures, same CMake, no code change)
- [x] One kernel header per ISA in `include/itch/kernels/`: `scalar`, `neon`, `ssse3`, `avx2`, `avx512vbmi`, `wasm`, each in its own namespace. Tests compare every kernel with `scalar` on random and real bytes. Built, with every header and `tests/test_kernels.cpp` (one ctest entry for each kernel; a kernel the CPU lacks shows as skipped). `scalar` and `neon` pass on the Mac, and `wasm` passes against scalar in node (`itch_wasm_kernels_smoke`). The x86 entries ran first in CI's two x86-64 Linux jobs, and on `debian` (Zen 1) `test_kernels_scalar`, `_ssse3` and `_avx2` pass under clang 23 and g++ 14, and `_avx512vbmi` is skipped with the CPU's missing features as the reason (docs/NOTES.md, "SIMD on `debian` (Zen 1)").
- [x] arm64: compile-time only, NEON guaranteed, no dispatch code. CMake's arch directory (`include/itch/kernels/arch/arm64/`) names scalar and NEON, and there is no `#if` and no runtime check.
- [x] x86-64: whole hot loop inside a `target` region, selected once at start with `__builtin_cpu_supports`. No `-march=native` in the default build. Built: each kernel's `run()` sits under `[[gnu::target]]` with the work `always_inline`, and clang's x86-64 output shows the loop inlined with no call. It runs on `debian`, where `--list-kernels` gives scalar, ssse3 and avx2 as runnable and `auto` picks avx2, and `--kernel avx512vbmi` exits 2 with the reason.
- [x] `--kernel={scalar,neon,ssse3,avx2,avx512vbmi,wasm,auto}`: all comparisons in one binary on one dataset. An unsupported kernel exits with an error, never a silent fallback. `bench_kernels` exits 2 with the reason (unknown, not built, or what the CPU lacks), writes no record, and has `--list-kernels`; `tests/test_bench_kernels_cli.py` checks it.
- [x] Kernels declare `kOverread`. The parser takes the scalar path near the end of the mapped range. Every kernel declares 0 except AVX-512 VBMI, which declares 45. `parse_ops()` hands a message within `kOverread` of the end to scalar and counts it, and a guard-page test covers each op type as the last frame.
- [x] WASM kernel uses `wasm_simd128.h` directly. It has no byte-reverse in core, so the sign of the result may flip (E11). E11 (P-0609): SIMD128 is 2.08 times scalar in node. The sign matches native E2, and is flipped against native's Add Order inside the parse loop (`results/simd_e11_mac_12302019.md`).
- [x] Experiments E1 to E9, E11 to E13 from `simd.md`. Record the instruction set of every number. NEON and AVX2 are different experiments. Done on the Mac: E1 to E6, E9, E12 and E13, NEON against scalar, and E11 in node (P-0601 to P-0610, `results/simd_*_mac*.md`). E7 and E8 were done earlier. Done on `debian` (Zen 1): E1 to E6 (E4 with the book end to end), E9, E13 and E12 with its GCC column, SSSE3 and AVX2 against scalar (P-0611 to P-0620, `results/simd_*_debian*.md`).
- [x] E10 needs a Skylake-family part. Zen 1 cannot show it. Report as not measured. Reported as not measured in docs/NOTES.md, "SIMD parsing kernels".
- [x] Say which wins, and explain with counters. A tie or a SIMD loss is the finding. On the Mac:
  - NEON wins a decode into memory (E2, 1.94 times).
  - The gain shrinks to 7.6% in integer registers (E3) and 6.6% for the whole parser (E4), while NEON is slower on every op type alone.
  - Scalar wins the four-wide batch (E5). The bitset beats any compare (E9).
  - With cycles, instructions and E12's listings, docs/NOTES.md has the explanation.
  - The default stays scalar.

  On `debian` (Zen 1), with branch and L2 counters and the release binary's listing:
  - SSSE3 wins a decode into memory (E2, 12.4 times scalar's cycles); AVX2 wins it too, at 2.2 times SSSE3's cycles.
  - Scalar wins in integer registers (E3, 1.5 to 1.8 times) and the whole parser (E4, about 30%). The book end to end moves by 1.2% at most.
  - Scalar wins the four-wide batch (E5). The bitset beats any compare (E9); AVX2's compare beats SSSE3's by its 64-bit compare, not its width.
  - AVX2 loses or ties SSSE3 wherever both run the same shuffle plan, as Zen 1's two-halves execution predicts.
  - The default stays scalar on x86 (docs/NOTES.md, "SIMD on `debian` (Zen 1)").

### Book
- [x] Order lookup: hash and paged direct index, measured apart from level search. References fit 32 bits on observed days, which is not a guarantee (DS-72). Both indexes are measured as arms on both hosts with each level arm held fixed: the paged index is 1.11 and 1.19 times the hash on `debian`, 1.15 and 1.24 on the Mac. Done: the lookup's own cost where it sits, a check pass against the real index less an answer pass from a recorded script (P-0020): the paged index 30.71 ns and the hash 32.50 ns a lookup on `debian`, 8.95 and 5.03 ns on the Mac (`results/lookup_{debian,mac}_12302019.md`).
- [x] Levels: flat with best price at the end; FIFO chains; `std::map` levels on fresh and aged heaps as baseline. The `std::map` baseline is built (`include/itch/map_book.hpp`, aged by `include/itch/heap_ager.hpp`) and agrees with the fast book on the fixtures and both days. Every level arm and both heaps are measured on the Mac on 12302019 (`results/bench_book_*_mac_12302019.md`), and every level arm on `debian`, mapped with the top tracked (`results/bench_book_*_debian_12302019.md`). Done: the map baseline on `debian` ran in P-0015 with the day in memory, throughput only (`results/bench_book_map-*_ram_debian_12302019.md`).
- [x] Sparse handling for stub quotes (DS-73). Neither level arm indexes a level by its price: both search by comparing prices, and a far quote is one more level at the far end. `BookScenario.AFarStubQuoteIsTheFarthestLevel` runs a $199,999.99 ask and a $0.0001 bid on every arm.
- [ ] Replace, delete and add measured on 8.7% `U` (2019) and 27.7% `U` (2026) mixes. Closed, not done, by the user's ruling of 2026-11-13: not measured, since no quiet Mac could be had. The 2019 mix, P-0801 on 12302019: 15 processes withdrawn under the sampler rule and 2 quiet ones not published, since P-0801 needs three quiet processes of each kind and four sequences gave two and none. The 2026 mix, P-0802 on 06122026: no process ran, so none was withdrawn, since P-0801 used the four sequences; the full-day oracle on 06122026 that P-0802 waited on was stopped by hand, and the ruling leaves it not run (results/run_log.md).
- [x] Prefetch lookahead D in {0, 4, 8, 16, 32, 64} (E8), and dispatch form (E7). Each hint keeps a measured reason in a comment. E8 (P-0021): not refuted on `debian`, +16.41% at D = 16; refuted on the Mac, slower at every D; kept as an option, off by default. E7 (P-0022): not refuted by the letter on `debian` (branch misses spread 0.0702 a message, the indirect forms higher), every form within 1% of `switch` on both hosts; the `switch` stays (`results/{prefetch,dispatch}_{debian,mac}_12302019.md`).
- [x] Repeat carteret's layout experiments on our book: hash policy, open addressing against chaining, window width against overflow. Attribute last-level misses by message type, order age and structure before claiming a bottleneck. Done (docs/NOTES.md, "carteret's layout experiments on our book" and "Attributing the misses"): on `debian` the identity hash +10.79% over multiply-shift and chaining +7.93% over open addressing, a 16-key search window ties the full walk and a bisect loses 4.09%, every arm through the full-day oracle; the paged default stays fastest on both hosts (`results/layout_{debian,mac}_12302019.md`). The window this book has no use for, counted: 8.34% of adds sit 256 or more ticks behind the best (`results/window_12302019.md`). `debian` cannot count last-level misses, so the floor on DRAM demand fills is attributed: by type and order age, 90.7% of named messages' fills on orders 1 s old or more (`paged-flat`, two processes of the three planned) (`results/counters_by_age_*_debian_12302019.md`); by structure, no structure reaches the 40% rule, the largest the level record at 22.7%, so no bottleneck is claimed (`results/structure_debian_12302019.md`).
- [x] Hot structs: `static_assert` on size, alignment, trivial copyability. `BookOrder`, `LinkedLevel`, `FlatLevel` and each arm's per-side record.
- [x] Measure the four book arms (hash or paged index, linked or flat levels) against each other and pick the default that ships. Done: `paged-flat` is fastest on both hosts, on the Mac in memory and mapped with the top tracked, and on `debian` mapped with the top tracked, and holds 0.69 GB, under T-BOOK-3. `FastBook<>` is now the paged index with flat levels. docs/NOTES.md, "The default arm". `itch_oracle` and `itch_alloc` take `--index` and `--levels`.
- [x] Full-day oracle and T-ALLOC-1 on 12302019 for the three arms other than the default, under `heavy.sh`. Done on both days, with the trade checks, every arm line for line the same as the default: `scripts/check_real_day.sh --arms all`, `results/oracle_*.md` and `results/trades_*.md`.
- [x] Size `PagedIndex` for a day before it is measured. Done: 428,032 pages (418 MiB of slots), the larger of both days' peaks, 340,939 on 12302019, with a quarter more, and a reach of 2^30 (16 MiB of directory), past three times the largest reference on either day. Both paged arms pass the oracle and T-ALLOC-1 on both days with no fallback. docs/NOTES.md, "Sizing the paged index".
- [ ] T-BOOK-1 to T-BOOK-4, T-LAT-1 to T-LAT-5, T-DESIGN-1 on the harness. `bench_replay` and the driver measure every design and every one of these but T-BOOK-4. The Mac runs on 12302019 are in `results/` and docs/NOTES.md, "Measuring every book design". `debian` ran the four fast arms mapped with the top tracked (P-0011, and P-0013 with counters): T-BOOK-2 and T-LAT-1, 2, 4 and 5 missed on that host, T-LAT-3's p99.9 missed on every arm, T-BOOK-3 met. T-BOOK-1 and T-DESIGN-1 on `debian` came from P-0015 with the day in memory: T-BOOK-1 missed by every arm, `paged-flat` at 5.15 M messages/s against 16.4 M; T-DESIGN-1 missed by every arm, the best `paged-flat` at 2.54× over `map-fresh` against 4.0× (docs/NOTES.md, "P-0015"). Left: T-BOOK-4, which needs 01302019. Closed, not done, by the user's ruling of 2026-11-13: T-BOOK-4 is not measured, since no quiet Mac could be had. P-0502, `paged-flat` on 01302019 with the day in memory, ran three times, and each ended under the sampler rule: two withdrawn and one stopped by hand (results/run_log.md).
- [x] Make the in-memory mode fit on `debian`: copy the day in pieces and drop each piece's page cache as it goes, so that the file and its copy are never both whole in memory, then run P-0010 again for T-BOOK-1 and T-DESIGN-1 there. The copy is done (64 MiB pieces, `POSIX_FADV_DONTNEED` each, an arena its reads fault in) and the day's cache stayed empty, but P-0012 was refused after one page was swapped out during `map-aged`, and P-0014, the fast arms alone, never got `measure.sh`'s lock before the 04:02 window. docs/NOTES.md, "The day in memory on `debian`". Done: P-0015 ran every design with the day in memory and published, `pswpout` still from start to end, after a first attempt refused for 2 pages; docs/NOTES.md, "P-0015".
- [x] The counter pass on `debian` for the book designs, once `perf_event_paranoid` is 2 again. Done for the four fast arms, mapped with the top tracked (P-0013, `results/bench_book_*_mapped_top_counters_debian_12302019.md`): every event measured, `rdpmc` agreeing with `read()`. The map designs have no `debian` counters, since they ran only in refused runs.
- [x] `bench_driver.py` writes no run-log row when it is signalled. Write the row before exiting, with a fault. Done: SIGTERM, SIGINT and SIGHUP are passed to the bench, the row is written with where the run was, and the exit is 128 plus the signal; three faults. The driver also refuses a run during which `pswpout` moves, with two faults.
- [x] Strict crossed-book run, full day. Count and characterise each violation by symbol, time and trading state. Report both published counts' populations: all observations, and market open with state `T`. The user decides the scope. Done on the Phase 2 stub: `results/oracle_12302019.md`, strict result FAIL.
- [x] T-INV-3, the scope the user approved after seeing the strict result: a book in normal trading may only be crossed if the cross is left over from a halt and has not cleared. Separate from T-INV-2, which stays strict. Done on the Phase 2 stub on both days: `results/oracle_12302019.md` and `results/oracle_10302019.md`, 0 violations on each.
- [x] Oracle replay on the Mac under `heavy.sh`, all 268,744,780 messages (T-ORACLE-1). Done on the Phase 2 stub: `results/oracle_12302019.md`.
- [x] Repeat T-ORACLE-1, T-INV-1, T-INV-2, T-INV-3 and T-ALLOC-1 on 12302019 with the real book, once it replaces the stub. Done on both days and all four arms, with T-TRADE-1, 2, 3, 5 and PRINTS: the same verdicts and counts as the stub, T-INV-2 strict still failing.
- [x] Repeat the oracle on every session the archive will serve, up to ten, keeping raw output for each (T-ORACLE-2). Check free disk first. State any shortfall as a gap. Done: carteret's ten sessions, 0 divergences in 3,081,392,329 messages, on `paged_flat` for the eight new ones (`results/oracle_sessions.md`, docs/NOTES.md, "T-ORACLE-2 on carteret's ten sessions"). T-TRADE-3 failed on four of the new days and T-TRADE-5 on two, which docs/NOTES.md describes.

### Trade-print verification
Hard means any mismatch fails. Each check needs an injected bug that trips it. Check 17 in the evidence table only tests our aggregator, so it must not be cited as an independent result.
- [x] `E`, `C` reference a live order, on this locate, `shares <= remaining`. Hard. T-INV-1.
- [x] `X`, `D`, `U` same liveness, and `U`'s new reference unused. Hard. T-INV-1.
- [x] Per-order `Add` shares equal the sum of deductions; book empty at `S/C` (T-TRADE-1). Hard. Done on the Phase 2 stub, both days.
- [x] `E` at best price on its side, with market open and state `T` (T-TRADE-5). Hard. Count the rest. Done on the Phase 2 stub, both days.
- [x] `E` at queue front: reported as a rate. Published 99.8141%, 99.82% and 99.914%, unexplained remainder.
- [x] `C` deducts from the order and leaves its price unchanged. Hard for the deduction, with counts by `Printable`.
- [x] `P`: reference 0, side `B`, no book change. Hard. Whole class excluded from "hits a resting order": count and surface.
- [x] `Q` per symbol against CrossStats (T-TRADE-2). Hard. Opening shares against Opening, closing against Closing. Halt crosses: sum of `Q` outside CrossStats equals type `H` sum. Test symbols have no row, and are counted apart. Done on 12302019, on the seven Nasdaq sessions of P-0501, and on 10302019 (P-0503, all four arms, `results/trades_10302019.md`) against `CrossStats20191030.txt`, which P-0501's fetch pinned: 3,180 rows equal, and the 1,327 `Q` shares outside the file equal to the type `H` sum.
- [x] `C_N` against `Q`: per symbol and side `<=`, equal price. Measure first, because no one has confirmed this on real data.
- [x] `E + C_Y + P` against MIDAS (T-TRADE-3): exact per-symbol equality once the window is pinned. If it cannot be pinned, report the per-symbol differences as an open gap with the reason. An inequality is never a pass. Add `P` against `HiddenVol`. Done on the Phase 2 stub on both days at 09:35 to 16:00, every column on every symbol, with odd lots on the SEC's round-lot rule, which the user chose after the failures recorded in docs/NOTES.md. See `results/trades_12302019.md` and `results/trades_10302019.md`.
- [ ] Cboe per-tape check (T-TRADE-4): held. No Cboe data in this project until the user decides. Scripts do not fetch it, nothing depends on it, and no further joins are run. Held, unchanged, by the user's ruling of 2026-11-13; the user decides at the end.
- [x] `B` reverses a stored match, hard on 10302019, fixtures otherwise.
- [x] Match numbers unique across `E`, `C`, `P`, `Q`. Measure first. Measured on both days: unique across the print classes. A far-leg `C_N` shares its number with one print, by design, and is checked against it.
- [x] Surface class-level counts in the phase report: test symbols, zero-share `Q`, `C_N`, `P`, `B`. In section 2.6 of the Phase 3 report, accepted at 3f8bacd on 2026-11-13.

### Low-latency components
Each benchmarked alone, then measured inside the book or backtester.
- [x] SPSC ring: padding from one constant that is 128 on Apple Silicon. Cached indices, mask, batch publish. `include/itch/spsc.hpp`, the constant from `cmake/ItchCacheLine.cmake` (128 on Apple silicon, 64 on x86-64 and other arm64), each technique a compile-time switch, no allocation after construction. docs/NOTES.md, "The SPSC ring".
- [x] SPSC comparison: project queue, rigtorp, boost::lockfree, moodycamel, folly, atomic_queue, xenium Vyukov as MPMC control, mutex ring as floor. Pinned behind `ITCH_COMPARISON_QUEUES`; measured on the Mac, unpinned and under the sampler rule (P-0301, `results/spsc_queues_mac_12302019.md`), and on `debian` pinned to cores 4 and 6 (P-0306, `results/spsc_queues_debian_12302019.md`), where all three T-SPSC-1 bars are missed.
- [x] SPSC ablation, one change at a time (T-SPSC-2). Protocols: ping-pong RTT, saturated one-way, paced one-way with percentiles, bursty replay. Measured on the Mac, unpinned and under the sampler rule (P-0302, `results/spsc_ablation_mac_12302019.md`), and on `debian` pinned (P-0307, `results/spsc_ablation_debian_12302019.md`), where all four bars are missed.
- [x] Correctness: TSan on both hosts, arm64 stress without TSan, negative control with weakened ordering. It is unconfirmed whether stress catches this on M-series (LC-37), so report what happens. Done on the Mac: TSan on the shipped orders, and each of the four weakened orders caught by TSan on the word handover; the arm64 stress catches a weakened publish or observe and not a weakened return or reclaim (docs/NOTES.md, "The SPSC ring"). Done on `debian` (P-0309): TSan passes the shipped orders and catches each weakened order on both the word and the item test; the x86 stress catches none of the four, as TSO predicts; and the cross-core check of the TSC between cores 4 and 6 (P-0305, `bench_probe --cross-core`) agrees within about 40 ns before every one-way latency there (docs/NOTES.md, "SPSC under the sampler rule, and on `debian`").
- [x] Parser thread → queue → book thread on a real day, against single-threaded replay (T-SPSC-3). If single-threaded wins, that is the finding. `bench/pipeline.hpp`, `bench_pipeline`, on 12302019 on the Mac, unpinned and under the sampler rule (P-0304, `results/spsc_pipeline_mac_12302019.md`), and on `debian` with the book on core 4 and the parser on core 6 (P-0308, `results/spsc_pipeline_debian_12302019.md`), met at 0.95×; the book equal by hash at the close and at a mid-day checkpoint, since the closing book is empty.
- [x] Pool allocator: LIFO free list, 32-bit indices, sized from the counted peak, pre-faulted, 2 MiB pages where offered. `include/itch/node_pool.hpp` on `include/itch/page_map.hpp`: 2,408,448 orders and 983,040 levels, the larger day's peak with a quarter more, every node touched before the book runs, and the page size obtained recorded from what the kernel says, never from what was asked. On the Mac the 2 MiB superpage is refused (errno 22) and the pool runs on 16,384-byte base pages, which it records; the Linux path (`madvise(MADV_HUGEPAGE)`, `MAP_HUGETLB`, smaps) has not yet been built on Linux: CI builds and tests it once the branch is pushed, and `debian` first runs it in the page-size crossing below. docs/NOTES.md, "The pool allocator and T-ARENA-1's allocator arms".
- [ ] Allocator arms with only the allocator changed: system malloc fresh and aged, mimalloc, `unsynchronized_pool_resource`, `monotonic_buffer_resource`, pool with index, pool with pointer, FIFO free list (T-ARENA-1). Crossed with page sizes on `debian`. Built: the eight arms run as `bench_replay`'s `alloc-ARM` designs through `scripts/alloc_driver.py`, every arm holds the map book's hash after every message of the fixtures (`pool-index` also on the whole of 12302019, outside `measure.sh`), and the pool arms allocate nothing. P-0701 has no published Mac run: five tries ended under the sampler rule (docs/NOTES.md, "The pool allocator and T-ARENA-1's allocator arms"). On `debian` the crossing ran for two of its three page sizes (P-0702, docs/NOTES.md, "T-ARENA-1's timed arms on `debian`, crossed with the pools' pages"): `base` and `transparent` published, `results/alloc_arms_base_debian_12302019.md` and `results/alloc_arms_transparent_debian_12302019.md`, the pool arms 12.8% to 13.9% faster on 2 MiB pages and the heap arms unmoved, every arm's book agreeing with the fast book and the pool arms on 0 allocations over the whole day. Not finished: the fault check FAILS in both files, with 136, 200 and 146 page faults in the timed passes of `pool-index`, `pool-pointer` and `pool-fifo` under `base` and 4, 1 and 0 under `transparent`, against none, which P-0703 traced to the kernel moving 4 KiB pages under the running book, kcompactd's compaction of the pools and the heap index and khugepaged's collapses of the index; `explicit` is blocked on the user, since `vm.nr_hugepages` is 0 on `debian` and the pointer book needs at least 73 pages of 2 MiB (160 to be safe), a root step; `pmr-monotonic` was not run on `debian`, since its book alone would hold about 7.5 GiB of nodes beside the 7.68 GiB day in 15.3 GiB of RAM. Closed, not done, by the user's ruling of 2026-11-13: no host change. The fault check stays FAIL as published, with P-0703's cause beside it: the kernel moving 4 KiB pages under the running book (docs/NOTES.md, "Where the faults come from: P-0703, a diagnostic"). `explicit` stays not measured, since a change to `vm.nr_hugepages` is ruled out, and `pmr-monotonic` stays a gap. The Mac stays not measured, since no quiet Mac could be had: P-0701's five runs there published nothing, four refused after three processes in a row were withdrawn under the sampler rule and one stopped while it waited for a quiet Mac, 22 processes withdrawn in all (results/run_log.md).

### io_uring (Linux only, on `debian`)
- [x] MoldUDP64 framing, replay sender from a prebuilt packet array: unicast `127.0.0.1`, connected, sender on its own physical core, spin pacing. The framing, the sequence check, the prebuilt array, the connected pair on `127.0.0.1` and the spin-paced sender are built and tested (`include/itch/mold*.hpp`, `bench/udp/`, `test_mold`, `test_udp`, `test_alloc`, `fuzz_mold`, the faults in `scripts/faults/udp.json`). On `debian` the sender ran pinned to core 4 and the receiver to core 6, two physical cores, every pin read back in every record. docs/NOTES.md, "The UDP replay's portable groundwork", and docs/NOTES.md, "T-URING-1 and T-URING-2 on `debian`".
- [x] Gap and duplicate detection on the receive side from the Mold sequence numbers: gaps, messages lost, duplicates, heartbeats ahead of the next sequence, end of session, foreign sessions. `mold::SequenceTracker`. The kernel and socket counters of IU-43 come with the Linux modes.
- [x] A loopback functional check on the Mac, the sender and S1 on one prebuilt fixture, every message arriving once and leaving the book as the file path does: `tools/itch_loopback`, `test_loopback_cli.py`, three negative controls. A functional check only. It prints no rate, and no Mac loopback figure is reported.
- [x] Modes S1 to S4 and U1 to U5 from `io-uring-flamegraph.md`. U2 and S3 are the headline pair. Same parser in each. All nine are built on the one `ReceiveMode` interface, and every mode's datagrams go through the same `mold::FeedHandler`, `FrameReader` and book. S1 builds everywhere, S2 to S4 on Linux, CI included, and U1 to U5 with liburing, on `debian`, where every mode passes `test_udp` and `test_alloc` and the Linux faults of `scripts/faults/udp_modes.json`. docs/NOTES.md, "T-URING-1 and T-URING-2 on `debian`".
- [x] Smoke test: record kernel, `io_uring_disabled`, `rmem_max`, `ulimit -l`, mitigations, feature probes. P-0901's smoke run, in the header of each `results/udp_*_debian_12302019.md`, with `rmem_default`, `lo`'s MTU, `ip_early_demux`, the clock source and whether IRQ time is accounted. docs/NOTES.md, "T-URING-1 and T-URING-2 on `debian`".
- [x] Find the sender's ceiling first. A run near it is invalid. 9,863,736 msg/s, the median of 10 (`results/udp_ceiling_debian_12302019.md`); no trial ran above 0.9 × it.
- [x] Max lossless rate by bisection, feed-shaped replay, latency at fixed rates including a low rate, CPU split across sender, receiver, SQ thread, softirq (T-URING-2). P-0901 and P-0902 on `debian`: `results/udp_bisect_debian_12302019.md` (T-URING-1 measured, no bar; T-URING-2 PASS for S4), `results/udp_feed_debian_12302019.md`, `results/udp_latency_debian_12302019.md`. docs/NOTES.md, "T-URING-1 and T-URING-2 on `debian`".
- [x] Results state that loopback runs softirq on the sender's CPU, and that this is not a network benchmark. Every `results/udp_*` file says it on one line, and `udp_report.py --check` fails a file without it.
- [x] macOS builds with no receiver backend for this mode and no code change. Nothing substitutes for it. The Mac builds S1 alone: S2 to S4 and U1 to U5 are compiled only where `ITCH_UDP_SOCKET_MODES` and `ITCH_UDP_RING_MODES` are set, Linux and Linux with liburing, and the Mac's build and CI's macOS job pass with the Linux modes in the tree.
- [x] The small-packet arm: a 64-byte cap cannot carry Net Order Imbalance, 50 bytes. The least cap a whole day packs under is 72 (`kSmallestDayCap`). Choose 72, or a slice with no `I`, and state it. Chose 72: a slice with no `I` does not rescue 64, since Trade (`P`) is 44 bytes and 44-byte frames come before the day's first `I`. P-0903 on `debian`, the ceiling and the bisection only, by P-0902's method: `results/udp_ceiling_72b_debian_12302019.md`, 270,231 msg/s, 233,903 packets/s; `results/udp_bisect_72b_debian_12302019.md`, every mode but U4 sender limited below the cap with nothing lost, 187,539 (S2) to 232,897 (S4), and U4 220,621, stopped by its ring running dry. No latency, feed or T-URING-2 at this size; T-URING-2 is judged at 1,400 bytes. docs/NOTES.md, "T-URING-1's small-packet arm on `debian`".

### Backtester
- [x] Event ordering with `L_md`, `L_oe`, `L_resp`, `L_compute`. A simulated order joins at arrival, not at decision. `include/itch/backtest.hpp`; base values 25 µs each way and 5 µs compute, all in `Config::latency`. S10 to S17, S36.
- [x] Queue model from real L3 order references: `ahead` set from the real queue at arrival, falling only with depletion ahead. S01 to S09, and S08's property test against a recount after every message.
- [x] Partial fills from real executions behind S. Share ledger prevents a real order filling S twice. S18 to S29, S41.
- [x] Fees keyed by date, per fill, in integers. Base tier stated. Tape comes from Stock Directory Market Category. carteret's mapping is Q, G, S to C; N to A; A, P, Z, V to B. Check it against the spec first. `include/itch/fees.hpp`, picodollars, 20191030, 20191230 and 20260612. The spec gives listing markets, not tapes; Regulation NMS gives tapes by listing market, and the two confirm the mapping. docs/NOTES.md, "The backtester".
- [x] All 48 scenarios from `backtester.md` as tests (T-BT-2). `tests/test_backtest.cpp`, `tests/test_fees.cpp`, `tests/test_backtest_validity.cpp`, each with hand-written expected fills.
- [x] Lookahead: prefix invariance, future perturbation, oracle control must be caught. S42 to S44: the oracle is caught at 20 of 20 cuts by each check; two injected engine lookahead faults are caught (`scripts/faults/backtester.json`).
- [x] T-BT-1 and T-BT-3 on the harness, on the Mac. T-BT-3 reproduces queuetruth's workload: AAPL, MSFT, SPY, TSLA, 100,000 shadow orders. `bench/bench_backtest.cpp` and `scripts/backtest_driver.py`: T-BT-1 17.19 M engine events/s (16.79 M messages/s) of the whole day with the quoter, host differs from the x86 bar, which is in events/s; T-BT-3 7.80 M messages/s of the four-symbol extract, met. `results/backtest_*_mac_12302019.md`, docs/NOTES.md, "T-BT-1 and T-BT-3 on the Mac".
- [x] T-BT-1 on `debian`, the headline host, through `scripts/backtest_driver.py --workload quoting --core 4` under `measure.sh taskset -c 4`, with its own prediction (P-0103). T-BT-3 there too, as context, since its bar was set on a Mac (P-0104). T-BT-1 4.68 M engine events/s against the 2.0 M events/s bar, met (4.57 M messages/s); T-BT-3 3.35 M, missed by 1.26 M on this host's hardware; counts and hashes the Mac's. `results/backtest_*_debian_12302019.md`, docs/NOTES.md, "T-BT-1 and T-BT-3 on `debian`".
- [x] State the limit: no market impact. In the header comment of `include/itch/backtest.hpp` and docs/NOTES.md.
- [x] Replayed book identical with and without simulated orders (S46, per message, and `itch_backtest` on 12302019), identical event-log hash on repeat (S47), and no allocation after construction (`test_alloc`, and `itch_backtest` on 12302019).
- [x] Faults in `scripts/faults/backtester.json`, 22, for the queue rule, ledger, latency order, lookahead, replay identity, fees, allocation and the tool's checks. Each caught.
- [x] `itch_backtest`: a whole day through the engine with a trivial strategy, every check passing on 12302019 under `heavy.sh`. Not a benchmark.
- [x] Queue anomalies on real data: on 12302019, 1,347 executions on the traded symbols hit an order that joined after a resting simulated order while earlier orders at that price still rested, by a recount from the real book. Settled by `tools/itch_queue_jumps` on both days: every execution hit the lowest reference at its level, every jump was an order held for the open and shown late (Rules 4752(c), 4757(a)(1)), and every printable `C` traded at a price better for the aggressor than shown. A late add now goes ahead of every simulated order, and a printable `C` reaches only its trade price: 0 anomalies on the smoke. S49, S50, seven faults. docs/NOTES.md, "Queue jumps".
- [x] T-BT-3's 100,000 shadow orders: the simulated levels of a side are a list walked from the best, so many levels on one side cost a walk each. Measure the walk's share before changing it, with schedules that hold far more live orders. Counted, untimed (`bench_walks`, a build-time copy of the engine with a count at each walk, held to the engine's stats and hash; `results/walks_counts_12302019.md`): T-BT-3 holds 86.4 orders on average over its eight sides and 191 at most, not about 257, and a message makes 1.08 walks and 2.29 walk steps, on sides 4.37 levels deep on average and 24 at most; depletion finds take 67% to 89% of the level steps. Timed on `debian` on a ladder of lives 60 to 14,400 s (`scripts/walk_driver.py`, P-0105, `results/walks_ladder_debian_12302019.md`): 4.97 ns a walk step, the walk 3.5% of T-BT-3's 323.88 ns a message and 10.4% at 14,400 s. The walk is not why T-BT-3 misses on `debian` (3.20 M messages/s with it taken off, against 4.61 M), so the list stays. P-0105 refuted on one criterion, a negative slope in round 1. docs/NOTES.md, "P-0105: the walk's share of T-BT-3 on `debian`".
- [x] Report `hidden_prints_at_sim_price` with T-BT-1 as a bound on fills the model cannot see (6,331 on the 12302019 smoke). In each results file's counts: 5,068 in T-BT-1 and 7,109 in T-BT-3.

### Market maker
- [x] Avellaneda-Stoikov as the demo, with the finite-horizon form as named by the user. GLFT stationary form as a labelled variant, because the evidence recommends it on tick-constrained stocks. `include/itch/mm/quoting.hpp` and `strategy.hpp`, on the backtester's strategy interface, integer prices from the rounding on, no allocation after construction (`test_alloc`). Run on 12302019, P-0202, `results/mm_pnl_12302019.md`. docs/NOTES.md, "The market maker".
- [x] Commit symbols, γ, h = 15 s and order size before the first evaluation run. Selection cannot look at the result. AMD and BYND by MK-75 on 10302019 (`tools/itch_select`), γ = 1/(σ²·21,600 s), h = 15 s, 100 shares, at most 5 lots: `scripts/mm_evaluation.json`, c65c24c, before any run on 12302019. docs/NOTES.md, "The market maker's evaluation".
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
- [x] Replay engine with `em++`, `-O3 -msimd128`. Fixed initial memory. `FastBook<>`, the shipped `paged-flat` arm at its default capacity, in a fixed 1 GiB that never grows; the same digest as native. docs/NOTES.md, "The browser demo".
- [x] Static page in `docs/`: animated depth and trades for one symbol, messages/sec from batches.
- [x] Banned styling: cream or off-white background, italic accent words in headings, numbered "01 / 02 / 03" labels, pill buttons, gradient heroes. Five gates in `scripts/gates.py`, each with a fault.
- [x] Extract generated by script into a gitignored path. The page says what to run if it is missing. No market data in git, real or synthetic. No public deployment of real extracts without Nasdaq's written approval.
- [x] Native Linux numbers from a `debian` results file. The page shows them as missing until they exist. No Mac numbers in that slot.
- [x] State plainly that WASM cannot show true low latency, and why.
- [x] Runs locally from `docs/` over a local HTTP server, because `file://` blocks loading.
- [x] T-WASM-1, labelled macOS. 1.19× native in Google Chrome for Testing 153.0.8010.12, headless: Google Chrome is not installed, so the verdict is "browser differs". The first pair was withdrawn under the sampler rule. `results/wasm_replay_mac_12302019.md`.

### Write-up and iteration
- [x] CI green on GitHub. The first run, at 2810183, failed both GCC jobs on a `-Wconversion` error only Linux shows, and the x86-64 Clang job on the LLVM archive's 1 GiB zstd window. Fixed, and every job passed at da1a707. docs/NOTES.md, "CI's first runs on GitHub".
- [ ] docs/NOTES.md kept current, including what didn't help.
- [x] Phase 3 report, with the crossed-book findings and trade-print class counts. Accepted at 3f8bacd on 2026-11-13; the crossed-book findings and the class counts are in its section 2.6.

## Phase 4: Final review

- [ ] Every target in BENCHMARKS.md met, or measured number, gap and reason stated.
- [ ] Local check script passes.
- [ ] Each fuzzer clean for 30 minutes, under `heavy.sh`, on the Mac.
- [ ] README: what it is, how to build and run, generated results table.
- [x] README's top: a short pitch and a generated headline of 5 to 6 targets, at least one missed; the full results and run tables in RESULTS.md, generated and checked the same way.
- [x] Repo layout: CLAUDE.md untracked and ignored, the working files moved to docs/NOTES.md and docs/TASKS.md with every reference following, and an MIT LICENSE with the third-party terms named in the README.
- [x] WRITEUP.md: blog-post length, with numbers. Data layout, memory, branch prediction, what was tried, what didn't work.
- [ ] WASM demo page builds and runs locally from `docs/`.
- [ ] Full-repo review. For each problem: file and line, why it's wrong, how to show it fails, then fix it.
- [ ] Every Linux number cites its commit. Sync through `debian_sync.sh` from committed `HEAD`.
- [ ] Final report
- [x] The Linux sampler rule, fixed before any new number: `scripts/linux_sampler.py`, held by `backtest_driver.py`, `stamp_load.py` and `check_results.py` for every `debian` run from 2026-11-13T03:00Z; docs/NOTES.md, "The Linux sampler rule", and BENCHMARKS.md's method section. The other report checkers do not read it yet.
- [x] P-0106: T-BT-3 on `debian` again under the Linux sampler rule, P-0104's schedule, gave 284.96 ns a message, inside neither earlier figure's band, so the prediction failed, and it is published as failed with T-BT-3 missed by 1.10 M messages/s (`results/backtest_shadow_debian_12302019.md`, with the miss's host reason; docs/NOTES.md, "P-0106: T-BT-3 on `debian` under the Linux sampler rule").
- [x] T-BT-1 held in its bar's unit, engine events/s, with messages/s beside it; both T-BT-1 files rendered again from their raw records (docs/NOTES.md, "T-BT-1 in its bar's unit").

### Review finding r1-3: fuzz targets for the kernels, the packet builder and the look-ahead reader
- [x] `fuzz_kernels`: every kernel the host runs against the scalar kernel and `decode_book_op`, through `parse_ops` inside `run()` and message by message, each input in an exact-size heap block and each message with only its kernel's `kOverread` after it. Seeds, faults in `simd.json`.
- [x] `fuzz_packets`: `mold::build_packets` on a fuzzer-chosen plan against a model of the plan, and back through the decoder and a `FeedHandler` to the stream's frames. Seeds, faults in `udp.json`.
- [x] `fuzz_lookahead`, its own target: `LookaheadReader` against `FrameReader` at a fuzzer-chosen distance on a real book, with its prefetch schedule. Seeds, faults in `bookvariants.json`.
- [x] A 5-minute first run of each, under `heavy.sh`, on the Mac.
- [ ] The 30-minute runs of all nine targets at the final code (the Phase 4 item above). The x86-64 kernels were only compiled here; their first replay is CI's.

### Review findings r2-1, r2-3, r2-5, r3-1, r3-9 and r10-8: the tools' input checks
- [x] r2-1: `itch_alloc`, `itch_trades` and `itch_map_compare` refuse a capacity of 0 or past 32 bits (c995a9d, `tools/capacity.hpp`); the index headers state the precondition in a comment that preprocesses to the same code.
- [x] r2-3: `itch_window` counts a U's add search after its old order has gone (9ca0d3a); `results/window_12302019.md` rendered again under P-0027, only the add-search buckets moved, as predicted (docs/NOTES.md, "Against P-0027").
- [x] r2-5: an `itch_trades --symbols` file that cannot be opened, written or closed fails the run (21d3f28).
- [x] r3-1: `itch_backtest`, `itch_mm`, `itch_select`, `itch_calibrate` and `itch_queue_jumps` exit 1 unless the file's framing ends on `end_of_input`, naming the status and the skipped frames (c50b472, `tools/framing.hpp`).
- [x] r3-9: `itch_as_tables` and `itch_calibrate` parse numbers whole with `from_chars`, inside bounds (75b227e).
- [x] r10-8: `fetch_data.py` stops every worker on any error and raises it (c507d29).
- [x] P-0028, the published outputs of these tools run again: missed as worded, 24 of 72 differ from their raw logs, every local one identical from the base's binaries (docs/NOTES.md, "Against P-0028").
- [ ] `itch_queue_jumps` ignores `fclose`'s result for `--details`; left as it is, no published file affected (docs/NOTES.md, "Found by the final review and left as it is").
