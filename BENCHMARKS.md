# Targets

What top-tier looks like for an ITCH 5.0 feed handler, L3 book and research stack, with one source per number. Measured results do not go here. They go in generated results files and the README table.

## How to read this file

**Status of each number**

| Status | Meaning |
|---|---|
| `VERIFIED` | The session downloaded a fresh copy of the source and matched the exact text. `python3 -I scripts/check_benchmark_sources.py --cache DIR --log FILE` repeats it. |
| `SUBAGENT` | A research subagent opened the source and quoted it, and the session did not re-open it. Used only for context, never for a target. |
| `UNCONFIRMED` | No source could be confirmed. The entry says where we looked. Measured values will be reported, with no target claimed. |
| `BRIEF` | The bar comes from the project requirements, not an outside source. |

**Comparing with a target.** Most numbers were measured on other hardware. Each one is the bar, and also context: it proves nothing about this project's hardware. "Comparability" says what differs. itch-book's own README is the warning here: the same code and file ran 3.6× slower on a 2.0 GHz EPYC VM than on a Ryzen 9700X (16.4 / 4.6, derived), and its pooled-versus-flat-hash ratio fell from 1.8× to 1.07×.

**Hosts.** Numbers from the two hosts are kept apart and never mixed.

| Host | Role | Limits |
|---|---|---|
| `debian`: Ryzen Embedded V1605B (Zen 1), 2.0 GHz base, 4 MB L3, 15 GB RAM, Linux 6.12 | Headline pinned, `perf` and io_uring numbers | Each 256-bit AVX2 operation runs as two 128-bit ones. TSC reads step by about 10 ns and `rdtscp` costs about 19 ns. About 11 GB free, so the replay input must be confirmed resident before timing. |
| Apple M4 Max, 64 GB RAM, macOS | Development, sanitizers, fuzzing, oracle replay. Mac numbers stay labelled macOS. | No pinning. 41.67 ns timer quantum. No branch or cache counters without root or Instruments. |

Published server-class and desktop figures will beat `debian`. Targets stay at the published bar, and any gap is reported with its hardware reason. Reasons we can already cite: base clock and memory topping out at 2400 MT/s ([AMD V1000 brief](https://www.amd.com/content/dam/amd/en/documents/products/embedded/ryzen/v1000-family-product-brief.pdf)), a 128-bit vector datapath, 512 KB L2 and a 1536-entry L2 DTLB, which reaches 6 MB with 4 KB pages ([AMD Family 17h optimization guide](https://web.archive.org/web/2020id_/https://developer.amd.com/wordpress/media/2013/12/55723_SOG_Fam_17h_Processors_3.00.pdf)), plus 4 MB L3 shared by four cores and Retbleed/SRSO mitigations on kernel entry (IU-75, IU-79).

**Evidence.** IDs such as `OSS-16` point to entries in `build-research/evidence/*.md` (gitignored), which hold the verbatim quotes. Snapshots of the sources are under `.orchestrator/research-cache/itch-orderbook/`.

## Reference days

Nasdaq publishes no usable checksum: all 15 listed `.md5sum` files and all 8 `.done` files return 404 (DS-5, DS-6). Message and byte counts below are published claims until our harness counts them. Byte counts were cross-checked against the gzip trailer on Nasdaq's file (DS-13 to DS-15).

| Role | File | Compressed bytes | Unpacked bytes | Messages | Why |
|---|---|---:|---:|---:|---|
| **Reference day** | `12302019.NASDAQ_ITCH50.gz` | 3,524,013,057 | 8,251,407,909 | 268,744,780 | Fits `debian`. The strongest rival numbers were measured on it. Volume ties to outside data. |
| **Second day** | `10302019.NASDAQ_ITCH50.gz` | 3,872,931,242 | 9,035,016,843 | 293,989,079 | Two independent counts agree. Holds the only `B` found. Backtest tuning day. FOMC statement day (BT notes). |
| Comparison day, Mac only | `01302019.NASDAQ_ITCH50.gz` | 4,764,426,091 | 11,245,883,092 (derived from ISIZE) | 368,366,634 | The busier day that rpgr18 and parseritch used. |
| Stretch day, Mac only | `S061226-v50.txt.gz` | 17,894,268,560 | 41,662,444,846 | 1,304,894,064 | Modern message mix: `U` is 27.7% of messages against 8.7% in 2019. Far too large for `debian`. |

Decision: the subagent recommended `S061226` as the reference day. Rejected, because 41.7 GB cannot be resident on `debian`, where headline numbers must come from, and no target below was measured on it. Its trade total also leaves a residual 28 times the 2019 one, unexplained (TV-16). It stays as a stretch run.

## Parse and book throughput

All on one thread, with the whole file resident before the clock starts, unless stated.

### T-PARSE-1: framing walk
- **Target:** ≥ 709 M msg/s, ~21.8 GB/s.
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-15.
- **Measured:** Ryzen 7 9700X (Zen 5, 32 MB L3), 96 GB DDR5, Ubuntu 24.04, core isolated, SMT off, gcc 13.3 `-O3`. Full day 12302019 in RAM. One pinned thread. Counts every framed message, with no fields read. Best of 3.
- **Comparability:** this measures memory bandwidth, as the README says. `debian` has far less of it. We report the median.
- **Status:** `VERIFIED`

### T-PARSE-2: decode of the book-affecting types
- **Target:** ≥ 199 M msg/s and ≥ ~6.1 GB/s.
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-16.
- **Measured:** as T-PARSE-1. All 10 book-affecting types decoded and folded into a checksum. The denominator is every framed message, so administrative messages cost less than a full decode would. Best of 3.
- **Comparability:** same day as ours, Zen 5 hardware. This is the main GB/s target.
- **Status:** `VERIFIED`

### T-PARSE-3: strict decode of every field
- **Target:** ≥ 93.9 M msg/s and ≥ 2.89 GB/s on 10302019.
- **Source:** [Lunary benchmarks](https://lunyn.com/benchmarks), OSS-34.
- **Measured:** Google Cloud c3-standard-8, Xeon Platinum 8481C 2.70 GHz, 32 GB DDR5, Ubuntu 24.04, Rust `-C target-cpu=native`. Full day 10302019, memory-mapped. Single thread. Every field of every supported type decoded and checksummed, median of 10.
- **Comparability:** a harder job than T-PARSE-2, under a VM, by a vendor on its own site.
- **Status:** `VERIFIED`

### T-BOOK-1: true L3 rebuild, all symbols
- **Target:** ≥ 16.4 M msg/s, ≤ 61 ns per message, with per-level FIFO queues kept.
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-17.
- **Measured:** as T-PARSE-1. All 8,907 symbols, 12302019 in RAM. Parse included, no BBO tracking. One timed pass, so no spread is published. FIFO chains are intrusive.
- **Comparability:** this is the strongest native true-L3 number found. Higher rates (26.8 M, 25.8 M) drop queue order, so they are not L3. See context below.
- **Status:** `VERIFIED`

### T-BOOK-2: L3 rebuild with BBO tracking through mmap
- **Target:** ≥ 12.5 M msg/s.
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-18.
- **Measured:** as T-BOOK-1, but the file is mapped and top-of-book is tracked. Threading is not stated for this tool.
- **Comparability:** the realistic pipeline. itch-book loses 24% going from T-BOOK-1 to this (derived: 1 − 12.5 / 16.4).
- **Status:** `VERIFIED`

### T-BOOK-3: book memory
- **Target:** ≤ ~1.4 GB for the book at T-BOOK-1 speed.
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-17.
- **Measured:** same row as T-BOOK-1.
- **Status:** `VERIFIED`

### T-BOOK-4: L3 rebuild on Apple silicon, busier day
- **Target:** ≥ 14.4 M msg/s, ≤ 69 ns per message, on 01302019. Mac host only.
- **Source:** [rpgr18/matching-engine README](https://github.com/rpgr18/matching-engine/blob/b7bab44b2a71f924be46fff9459a21c69bf4de96/README.md), OSS-24.
- **Measured:** Apple M2 Pro, 16 GB, macOS. Full day 01302019, 368,366,634 messages, 8,714 instruments. Single thread, one run. FIFO queues kept. Book time only: reading the file into memory is excluded.
- **Comparability:** the closest published hardware to the Mac host. An M4 Max should beat an M2 Pro, so this is a floor for that host.
- **Status:** `VERIFIED`

### Context, not targets

All `VERIFIED`.

| Result | Number | Why it is not a target |
|---|---|---|
| [charles-cooper, rerun by parseritch](https://github.com/rwxmai/parseritch/blob/fbee276143b132dfa76ff57f7ed092e607f8153e/bench-results/compare_01302019/after/table.md), OSS-8 | 25.8 M msg/s on Apple Silicon arm64, 01302019 | Aggregates levels, so no queue order. Not L3. |
| [parseritch](https://github.com/rwxmai/parseritch/blob/fbee276143b132dfa76ff57f7ed092e607f8153e/bench-results/compare_01302019/after/table.md), OSS-6 | 26.8 M msg/s | Aggregates levels, and ran x86 under Rosetta 2. |
| [CppTrader](https://github.com/chronoxor/CppTrader/blob/3815893630abe5cd2281c9fd3c6775808820b763/README.md), OSS-2 | 3,232,727 msg/s on i7-4790K | Older hardware. Disk read inside the timed loop. |
| [itch-book on a VM](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-19 | 4.6 M msg/s, 2.0 GHz EPYC 9645 KVM | The closest published clock speed to `debian`'s base clock. |

## Per-message latency

Timer rules apply to every row. On `debian`, resolution is about 10 ns and `rdtscp` costs about 19 ns, so anything under about 100 ns is batched or averaged. On the Mac the quantum is 41.67 ns, and a value under about 20 ticks (833 ns, derived) is a bound rather than a measurement. A percentile the timer cannot resolve is not published. Latency and throughput come from separate passes.

A replay running flat out measures service time, not response time to an arrival schedule. Results files must say so (MM section 2).

### T-LAT-1: per-operation latency, FIFO book
- **Target:** at or below these, in ns, uncorrected, with our timer floor shown alongside.

| Operation | p50 | p99 | p99.9 |
|---|---:|---:|---:|
| add | 60 | 447 | 683 |
| delete | 70 | 599 | 768 |
| replace | 110 | 709 | 945 |
| reduce (E/C/X) | 50 | 548 | 734 |

- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), OSS-61.
- **Measured:** as T-PARSE-1, full day 12302019, one isolated core. `rdtsc`, uncorrected, timestamp-pair floor 10 ns at p50 and 20 ns at p99 and p99.9.
- **Comparability:** per operation, not across all messages, so no all-message p99 can be derived from it.
- **Status:** `VERIFIED`

### T-LAT-2: all-message latency distribution
- **Target:** p50 ≤ 121 ns, p99 ≤ 552 ns, p99.9 ≤ 874 ns.
- **Source:** [tahsinj/nasdaq-feed-handler README](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/README.md), OSS-26.
- **Measured:** Intel Core i5-12400 under WSL2, GCC 13.3. Full day 01302020, 423,285,709 messages, memory-mapped. Pinned, single thread. Decode and apply, timer overhead included, across all messages.
- **Comparability:** the only whole-day, all-types distribution found. It is under a hypervisor on a different day, and its levels hold aggregates, not FIFO queues. The fenced timer stops cache misses overlapping, so its p50 is pessimistic. Ours is run on 12302019.
- **Status:** `VERIFIED`

### T-LAT-3: rate budgets
- **Target:** mean service time ≤ 393 ns per message on every host, and p99.9 ≤ 750 ns. Applying 750 ns at p99.9 is our choice, because the source gives a per-message bound, not a percentile.
- **Sources:** 393 ns is derived from Nasdaq's 1 ms peak of 2,547,190 msg/s for November 2024 ([Bandwidth Report, archived](https://web.archive.org/web/20250123185610/https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/bandwidthreport.xls), DS-78, DS-86). 750 ns is Jane Street's bound for a sequential packet processor ([Safe at Any Speed](https://www.janestreet.com/tech-talks/safe-at-any-speed/), FT-16).
- **Comparability:** floors, not top-tier bars. The Bandwidth Report's columns are undefined in the file, so "scaled to per second" is a reading. It is from 2024, the newest public copy. The Jane Street figure is a spoken queueing what-if on real data. A 2026 peak rate is `UNCONFIRMED`.
- **Status:** `VERIFIED`

### Context, not targets

- Optiver, CppCon 2024 medians per book operation: `std::map` 33 ns, rising to 63 ns on a randomized heap; sorted vector 34 ns; reversed vector 32 ns; branchless search 29 ns; linear search 22 ns ([slides](https://raw.githubusercontent.com/CppCon/CppCon2024/main/Presentations/When_Nanoseconds_Matter.pdf), FT-1 to FT-9). Hardware undisclosed, aggregated levels, no percentiles, charts clipped at 200 ns. `SUBAGENT`: read from chart images.
- [mohith-reddy-seelam](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/docs/RESULTS.md) per-type book-only figures (OSS-23) are lower, but exclude dispatch and prefetch, and were taken on a VM. `SUBAGENT`.
- **Firm-published software p99 or p99.9 for a book: `UNCONFIRMED`.** No firm, paper or vendor publishes one. Gross shows clipped histograms only. Looked in: Optiver (2022, 2024), Jane Street talks, HRT blog, Databento, academic searches.

## Book design comparison

### T-DESIGN-1: fast book against a map-based book
- **Target:** ≥ 4.0× the throughput of a `std::map` plus `unordered_map` book, same host, same day, same run (derived: 16.4 / 4.1).
- **Source:** [itch-book README](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md), LC-45.
- **Measured:** as T-BOOK-1. On one machine: pooled 16.4 M msg/s, inline paged 16.0, flat hash 9.2, `unordered_map` 6.9, naive 4.1.
- **Comparability:** on a 2.0 GHz EPYC VM the same pair gave 4.6 / 1.3 = 3.5× (derived, OSS-19), so the ratio depends on the machine. The Optiver result (FT-1, FT-2) means the map baseline must also run on a deliberately aged heap.
- **Status:** `VERIFIED`

**Designs to compare, at minimum.** Flat price levels with best price at the end, `std::map` levels on fresh and aged heaps, and order lookup by hash against paged direct index. Linear against binary search stays open: Gross and Cooper favour linear, and an [unreviewed replication on M4 Max](https://github.com/magnuls/lob-latency-research) finds the opposite (FT-6, FT-30). Order lookup is measured apart from level search, since [Jane Street](https://www.janestreet.com/tech-talks/building-an-exchange/) reports about 30% of a fast matching engine's time on that one dereference (FT-22). A naive flat array per symbol cannot work, because real stub quotes span almost the whole price field (DS-73).

## Perf counters

### T-PERF-1: cache and branch misses per message
- **Target:** `UNCONFIRMED`. No source gives per-message cache or branch misses for a book or feed handler. Looked in: Gross 2022 and 2024, Cook 2017, Jane Street, HRT, Databento, vendors, and all OSS repos reviewed. Measured values are reported with no target claimed.
- **Context:** [Cook's](https://raw.githubusercontent.com/CppCon/CppCon2017/master/Presentations/When%20a%20Microsecond%20Is%20an%20Eternity/When%20a%20Microsecond%20Is%20an%20Eternity%20-%20Carl%20Cook%20-%20CppCon%202017.pdf) `array_map` find benchmark, slide 35, shows 0.22% branch misses and 0.067% of cache references missing (FT-21, `VERIFIED`). It is a hash-table micro-benchmark on 32 CPUs at 2892.9 MHz, model not stated, with synthetic keys. [Gross's slides](https://raw.githubusercontent.com/CppCon/CppCon2024/main/Presentations/When_Nanoseconds_Matter.pdf), p. 50, show run totals of 48.24 M against 27.83 M branch misses with no message count, so no per-message figure can be derived (FT-7, `SUBAGENT`, chart image).
- **Event definitions on `debian`, confirmed:** `cache-misses` on Zen 1 counts **L2 misses, not last-level misses**. [Linux 6.12 `core.c`](https://github.com/torvalds/linux/blob/v6.12/arch/x86/events/amd/core.c) line 258 maps it to `0x0964` in `amd_zen1_perfmon_event_map`, which is event PMCx064 with bits 3 and 0 set. [AMD's PPR 55570-B1](https://web.archive.org/web/2021id_/https://developer.amd.com/wp-content/resources/55570-B1_PUB.zip) defines PMCx064 as "L2 Cache Request Outcomes (not including L2 Prefetch)", bit 3 as "Data Cache Req Miss in L2 (all types)" and bit 0 as "Instruction Cache Req Miss in L2". That PPR is not the one for the V1605B's exact model, and AMD's revision guide for Models 10h to 1Fh could not be found. `LLC-load-misses` is unsupported on Zen 1, and L3 counters are denied at `perf_event_paranoid=2`. The full table is in `io-uring-flamegraph.md`, "Zen 1 counter table". Any write-up must call this number L2 misses.
- **Status:** `UNCONFIRMED`

## Scalar against SIMD

### T-SIMD-1: which parser wins, and why
- **Target:** `UNCONFIRMED`. No controlled native-hardware ITCH comparison exists, so there is no numeric target. The requirement is to say which wins, per instruction set, and to explain it with counters.
- **Closest published:** mp-orderbook's NEON batch decode, 0.58× to 1.51× over 5 runs on 183,724 Add Orders, [no consistent direction](https://github.com/ademismkv/mp-orderbook/blob/main/devlog/2026-08-02-day-16-neon-parser-honest-result.md) (SIMD-5). parseritch ships kernels, but its numbers are under Rosetta. Nothing was found for SBE, FAST or OUCH either. Looked in: GitHub repo and code search, the web, academic search.
- **Hypothesis, not a target:** scalar ties or wins the whole parser on M4 and Zen 1. An isolated NEON decode may win, and that gain should vanish once fields reach integer registers. Prefetching moves the result more than decode does ([78.5 to 37.4 ns/msg, under Rosetta](https://github.com/rwxmai/parseritch/blob/fbee276143b132dfa76ff57f7ed092e607f8153e/bench-results/compare_01302019/after/table.md), SIMD-30).
- **Status:** `UNCONFIRMED`

## Allocation-free hot path

### T-ALLOC-1: zero allocations over a real day
- **Target:** 0 heap allocations between the end of warm-up and the end of the full reference day, counting aligned, nothrow and array `new`, with a control book that does allocate and proves the counter works.
- **Source:** [tahsinj results file](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/results/01302020_results.txt), lines 88 and 125.
- **Measured:** i5-12400 under WSL2, full day 01302020. Fast book 0 heap allocations. Control book 295,669,870.
- **Comparability:** zero is also a project requirement. The source supplies the method.
- **Status:** `VERIFIED`

## Book correctness

### T-ORACLE-1: reference book agrees after every message
- **Target:** 0 divergences across all 268,744,780 messages of the reference day, with coverage after every message. An injected bug must be caught. Run on the Mac.
- **Source:** `BRIEF`. The strongest published run found is 50 M real messages differentially tested after every message ([mohith README](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/README.md), line 95). A full day, full depth, per message would go beyond it.
- **Status:** `BRIEF`

### T-INV-1: every execute or cancel references a live order
- **Target:** 0 unknown references on the reference day, as a hard failure.
- **Source:** [KareemJandali census](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/validation/census-2019-12-30.json), `"unknown_refs": 0`, TV-36. Four other parties report the same for this day.
- **Measured:** 12302019 census, one reimplementation. A claim until our harness reproduces it. No Nasdaq statement on start-of-file book state was found, so the published zero is the only basis for treating an unknown reference as a failure.
- **Status:** `VERIFIED`

### T-INV-2: the book is never crossed
- **Target:** run the check strictly over the whole day. Count and characterise every violation by symbol, time and trading state. No scoping until the user decides.
- **Published expectation:** on 12302019, carteret reports 8,580 crossed and 70 locked observations, on 13 and 6 symbols, with the deepest crossing $14.00 ([correctness.md](https://github.com/EkanshSingh401/carteret/blob/3df0e8757c979731b07a991de4ec7e81e81c8c08/docs/correctness.md), [design.md](https://github.com/EkanshSingh401/carteret/blob/3df0e8757c979731b07a991de4ec7e81e81c8c08/docs/design.md), DS-68). Of the crossings, 7,945 are with the symbol not in state `T`, and the other 635 fall within 50 ms of its resumption. 8,372 of the 8,580 are inside 09:30 to 16:00, so clock time does not explain them. The strict invariant is therefore expected to fail, and that is a finding, not a bug. Our run has to reproduce or contradict this split.
- **Status:** `BRIEF` (the published count is `VERIFIED`)

## Trade-print verification

The user's sentence cannot hold for two whole classes. `P` references no visible order, and `Q` is a bulk print. Non-printable `C` volume is already inside `Q`, so summing it would double-count. The full per-type table is in `trade-verification.md` under "Proposed check definitions", and TASKS.md carries it. No tolerance applies anywhere.

Per type: `E` must hit a live order, deducting at the order's price. `C` must hit a live order, leaving the order price unchanged, and the print price may differ. `P` must have reference 0 and change nothing in the book. `Q` is compared with Nasdaq's own per-symbol cross file. `B` reverses a stored match.

### T-TRADE-1: order lifecycle balance (the independent internal pair)
- **Target:** for every order, `Add` shares equal the exact sum of later deductions. Book empty at end of day. On 12302019: adds 118,631,456 = deletes 114,360,997 + full executions 4,270,459, final live 0.
- **Source:** [KareemJandali census](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/validation/census-2019-12-30.json), TV-36.
- **Why independent:** Nasdaq sends no `D` for an order that reaches zero, so its silence is the witness. A lost or doubled deduction leaves an order alive forever. Summing `E` against "shares removed by replay" would prove nothing, because replay uses the same number.
- **Status:** `VERIFIED`

### T-TRADE-2: cross volume per symbol against Nasdaq's CrossStats
- **Target:** exact per-symbol equality, with opening `Q` shares against the file's Opening column and closing `Q` shares against its Closing column. On 12302019 the file totals are 19,851,021 opening and 93,930,336 closing shares. Halt crosses have no column here, so the `Q` shares outside the file must equal the type-`H` sum exactly. Test symbols have no row, and are counted apart.
- **Source:** [CrossStats20191230.txt](ftp://ftp.nasdaqtrader.com/files/crosses/CrossStats20191230.txt), TV-17.
- **Comparability:** Nasdaq's own file, separate from ITCH. No document defining its columns was found. Fetched by script, never committed. The session compared a published per-symbol file with it: 13 symbols differ (for example MKD, 227,736 against 1,243), and total cross volume is 114,191,631 against 113,781,357. Halt crosses and test symbols fit that, which is our reading until our harness splits `Q` by type.
- **Status:** `VERIFIED`

### T-TRADE-3: continuous volume against SEC MIDAS
- **Target:** per symbol, `E + printable C + P` ≤ MIDAS volume until the time window is pinned, then equality. On 12302019, Nasdaq rows total 620,148,444 shares, of which 105,453,046 are hidden. The file gives volume in thousands to three decimals.
- **Source:** [SEC MIDAS 2019 Q4, archived](https://web.archive.org/web/20260312180908id_/https://www.sec.gov/files/node/add/data_distribution/individual_security_exchange_2019_q4.zip), TV-21. `sec.gov` returns 403 to curl, so the capture is the copy that was opened.
- **Comparability:** the [SEC's terms](https://www.sec.gov/privacy) allow redistribution with citation (TV-27). It covers Stock and ETF names only, and its window is unknown.
- **Status:** `VERIFIED`

### T-TRADE-4: whole-day venue volume against Cboe
- **Target:** per tape, shares and trade count equal, and notional equal, on 12302019 and 10302019. **Held for a user decision.** The Cboe figures are deliberately not in this file.
- **Source:** [market_history_2019.csv](https://cdn.cboe.com/resources/us/equities/market-statistics/historical-market-volume/market_history_2019.csv), TV-13, TV-14.
- **Why it matters:** published per-symbol ITCH sums tie to this file exactly on 2019-12-30 and 2019-08-30 (TV-14), across all three tapes, once test symbols are removed. The session reran the 2019-12-30 join and got the same result. That is the strongest outside check found, and no prior project appears to have run it.
- **Terms:** [Cboe's terms](https://www.cboe.com/terms) bar using its materials "to verify or correct other data or information" without consent, subject to fair use (TV-26). A free consolidated per-symbol whole-day figure for Nasdaq does not exist (TV-30).
- **Status:** `VERIFIED` for the tie on 2019-12-30, by rerunning the join rather than by `check_benchmark_sources.py`, which holds no Cboe figures. All three tapes are equal on shares and trades, in `.orchestrator/research-cache/itch-orderbook/verify/cboe/tape_probe_20191230.log`. Use in this project is blocked on the user.

## Backtester

### T-BT-1: replay rate with queue simulation on
- **Target:** ≥ 2.0 M events/s on one thread: full replay plus strategy, with our queue model on, on a real day.
- **Source:** [Jaspinder2003/LobSim](https://github.com/Jaspinder2003/LobSim/blob/1cc2b12d27e161459da725bc394c3ebae09b8912/README.md), BT-20: "full replay + strategy 507.6 ns/op 2.0 M/s".
- **Measured:** Intel Core i9-13900H, release build, no `-march=native`. A **synthetic** L3 feed, with a market-making strategy. The README says the repo simulates queue position, but does not say whether the queue model is active in this row, or how many threads ran.
- **Comparability:** a small unreviewed repo on a synthetic feed, and the highest honest rate found in this class. No project publishes an ITCH-based L3 backtester rate with exact FIFO and partial fills, so the true top-tier number is `UNCONFIRMED`. Looked in: hftbacktest (no figure), NautilusTrader (0.92 M iterations/s on 2,048 iterations, derived, BT-14), LEAN (data-point rates), backtrader (candles), vectorbt (declines to publish).
- **Status:** `VERIFIED`

### T-BT-2: validity gates
- **Target:** all pass. 48 hand-built scenarios covering queue, latency, fills, ITCH edge cases, fees, and lookahead (`backtester.md`, "Test scenarios"). An injected lookahead bug is caught. The replayed book is identical with and without simulated orders. Identical event log hash on repeat.
- **Source:** `BRIEF`. No published test method for proving a tick backtest has no lookahead was found (BT-57).
- **Status:** `BRIEF`

### Fees for the report day (2019-12-30), base tier

Rates per share executed at $1 or more, displayed, non-retail. Base tier, because tiers need a share of consolidated volume that a small simulated participant would not have (BT-45).

| Item | Rate | Source | Status |
|---|---|---|---|
| Remove | $0.0030 | [Nasdaq price list, 2019-12-07 capture](https://web.archive.org/web/20191207142614id_/http://www.nasdaqtrader.com/Trader.aspx?id=PriceListTrading2), BT-33 | `VERIFIED` |
| Add, Tape A and B | $0.0020 credit | same | `VERIFIED` |
| Add, Tape C | $0.0015 credit | same | `VERIFIED` |
| Section 31, sells | $20.70 per $1M | [FR 2019-04800](https://www.govinfo.gov/content/pkg/FR-2019-03-15/pdf/2019-04800.pdf), BT-41 | `VERIFIED` |
| FINRA TAF, sells | $0.000119, maximum $5.95 | [FR 2020-23141](https://www.govinfo.gov/content/pkg/FR-2020-10-20/pdf/2020-23141.pdf), BT-42 | `VERIFIED` |

Rates for 2019-10-30 and 2026-06-12 are in `backtester.md`, "Fee table", and are `SUBAGENT` until those days are used for reporting. The add and remove rates were read from price-list captures on either side of the date (2019-12-07 and [2020-02-01](https://web.archive.org/web/20200201153207id_/http://www.nasdaqtrader.com/Trader.aspx?id=PriceListTrading2)), which agree. The consolidated 2019 rule text could not be obtained.

### Latency assumptions

The base run uses 25 µs each way plus 5 µs compute. This is an assumption derived from Nasdaq's ["sub-50 microseconds" round trip](https://www.nasdaqtrader.com/trader.aspx?id=colo) (BT-23), split evenly. Nothing here is a measured Nasdaq latency: Nasdaq publishes no distribution. The sweep straddles the modal race gap of 5 to 10 µs and the 79 µs mean from [Aquilina, Budish and O'Neill](https://www.nber.org/system/files/working_papers/w29011/w29011.pdf) (BT-29), which is LSE data from 2015. Both figures are `VERIFIED`.

**Stated limit:** a replayed feed cannot react to the strategy, so market impact is absent.

## Market maker

### T-MM-1: PnL components sum exactly
- **Target:** spread capture + adverse selection + inventory == total mark-to-market PnL, tested with `==` on integers for any fill sequence, with both forms of the computation agreeing.
- **Formulas:** S = Σ qᵢ(mᵢ⁻ − Pᵢ) − Σ fᵢ; A = Σ qᵢ(mᵢʰ − mᵢ⁻); I = Q₀(m_T − m₀) + Σ qᵢ(m_T − mᵢʰ). Fees and rebates sit inside S, shown as a sub-line. h = 15 s, with the curve shown at 50 ms, 1 s, 15 s, 1 min, 5 min ([amended Rule 605 grid](https://www.govinfo.gov/content/pkg/FR-2025-10-02/pdf/2025-19316.pdf), MK-40, `SUBAGENT`). Inventory is computed separately, never as a residual.
- **Source:** `BRIEF`. Proof and worked example are in `market-maker.md`, "Attribution identity". The split follows [Menkveld's](https://papers.tinbergen.nl/11076.pdf) net spread and positioning decomposition (MK-42). The session reran the subagent's check: 4,000 random fill sequences at six horizons, 24,000 exact equalities, in Python on a worked model. The C++ test is still to be written.
- **Status:** `BRIEF`

### T-MM-2: reproduce Avellaneda and Stoikov's own tables
- **Target:** with the paper's parameters (s = 100, T = 1, σ = 2, dt = 0.005, k = 1.5, A = 140, 1000 paths), the average spread matches 1.49 (γ = 0.1), 1.35 (γ = 0.01) and 3.02 (γ = 1).
- **Source:** [Avellaneda and Stoikov 2008](https://math.nyu.edu/~avellane/HighFrequencyTrading.pdf), pp. 222-223, MK-7 to MK-11.
- **Comparability:** synthetic Monte Carlo. This tests our implementation of the model and says nothing about real data.
- **Status:** `VERIFIED`

**No headline return, no Sharpe.** Published realised spreads and queue values are context only. Moallemi and Yuan's value gap between front and back of the queue ranges from 0.05 to 0.17 ticks (derived, simulation columns, nine large-tick names, [paper](https://moallemi.com/ciamac/papers/queue-value-2016.pdf) pp. 23-24, MK-58). That is per order, at zero latency, on older data, and it is not a strategy PnL. One day is one sample, so no edge is claimed.

## SPSC ring buffer

### T-SPSC-1: round trip against boost, moodycamel and rigtorp
- **Target:** on `debian`, in one harness: at least 1.16× better RTT than `boost::lockfree::spsc_queue` (derived: 160 / 138), and at or below moodycamel and rigtorp.
- **Source:** [atomic_queue results](https://github.com/max0x7ba/atomic_queue/blob/b7f1befe89446752767243c5e5550779a9a5eed2/html/results.js#L29), LC-1: OptimistAtomicQueue 138 ns, boost 160 ns, moodycamel 136 ns.
- **Measured:** Ryzen 5825U, pinned, cross-core, 4-byte elements, 33 runs, gcc-14.
- **Comparability:** a ratio, because Zen 3 and Zen 1 are not comparable in absolute terms. The 5825U is the closest published part. `debian` has one CCX, so cross-CCX results cannot be reproduced there.
- **Status:** `VERIFIED`

### T-SPSC-2: throughput gains from the known techniques
- **Target:** on one host, cached indices ≥ 3.9× over uncached (derived: 165,138,887 / 41,859,740), and mask ≥ 4.2× over `%` (derived: 690,662,074 / 165,138,887).
- **Source:** [Frasch, CppCon 2023](https://raw.githubusercontent.com/CppCon/CppCon2023/main/Presentations/SPSC_Lock-free_Wait-Free_Fifo_from_the_Ground_Up_CppCon_2023.pdf), pp. 41-42, LC-7.
- **Measured:** i7-9700, 8-byte elements, capacity 2^17, 100 runs, under WSL2.
- **Comparability:** a different microarchitecture, so the ablation may differ. Reported either way.
- **Status:** `VERIFIED`

**Context, all `VERIFIED`.** [rigtorp](https://github.com/rigtorp/SPSCQueue/blob/59a6a938513ea5004817383711ed35d32385d3ee/README.md): 362,723 ops/ms and 133 ns RTT on a 3900X, same chiplet (LC-5). [atomic_queue](https://github.com/max0x7ba/atomic_queue/blob/b7f1befe89446752767243c5e5550779a9a5eed2/html/results.js#L17) on a 5950X reaches 87 ns RTT (LC-2). The [Disruptor paper](https://lmax-exchange.github.io/disruptor/files/Disruptor-1.0.pdf) is the only queue source with tails: 52 ns mean per hop (LC-12).

**Mac.** Unpinned cross-core latency is weak evidence, and the results header must say so. The Mac's role is 128-byte padding and ordering under stress, combined with TSan.

## Arena or pool allocator

### T-ARENA-1: measured effect on book latency
- **Target:** `UNCONFIRMED`. No published book changes only the allocator, so no number can be a target. We report throughput, p50, p99 and p99.9 per operation for the same book with only the allocator changed. Zero page faults in the timed region for pool arms.
- **Context:** N4468 reports 39.577 s against 4.89 s on a fragmented workload, Bloomberg authors, i7-4770, and the same paper shows pools losing where locality is not the issue ([N4468](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2015/n4468.pdf), LC-38, LC-39). itch-book's pooled against inline comparison also changes the index, so it is not a clean allocator result (LC-45).
- **Looked in:** `oss-throughput.md`, `firm-talks.md`, all cached repo READMEs, web search.
- **Status:** `UNCONFIRMED`

## io_uring UDP receiver

### T-URING-1: io_uring against sockets
- **Target:** `UNCONFIRMED` as a ratio. No published head-to-head of io_uring and `recvmmsg` for UDP receive was found, and it is not known that io_uring wins. The report states which mode won under which regime. Looked in: Axboe's paper, the liburing wiki and issues, Cloudflare's UDP posts, LWN, the Linköping thesis, Envoy, moq-uring, web search.
- **Context, none of it on our setup:** Brouer shows the fastest call flipping with packet size and socket setup, across 391 to 600 ns/packet ([2016](https://lists.openwall.net/netdev/2016/12/08/212), [follow-up](https://lists.openwall.net/netdev/2016/12/08/229), IU-2), and `recvmmsg` nearly twice as slow when unpinned ([2019](https://lists.openwall.net/netdev/2019/11/27/122), IU-3). On a forwarding workload, one lab saw io_uring at about 460 kpps against sockets at about 300 kpps ([thesis](https://www.diva-portal.org/smash/get/diva2:1789103/FULLTEXT01.pdf), IU-8), while an earlier report from the same lab had sockets ahead at 475 kpps against 430 kpps ([liburing #844](https://github.com/axboe/liburing/issues/844), IU-9).
- **Status:** `UNCONFIRMED`

### T-URING-2: lossless at Nasdaq's peak message rate
- **Target:** zero loss at ≥ 2,547,190 msg/s on `debian`, for the winning mode.
- **Source:** [Bandwidth Report, archived](https://web.archive.org/web/20250123185610/https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/bandwidthreport.xls), DS-78, 1 ms column, November 2024.
- **Comparability:** Nasdaq's feed reaches firms over a NIC. Ours is loopback, where receive softirq runs on the sender's CPU, so this is a syscall-and-copy benchmark, not a network one (IU-47, IU-48). The report must say so. `SO_BUSY_POLL`, NAPI and zcrx do nothing on loopback.
- **Status:** `VERIFIED`

## Flamegraphs

- **Target:** a flamegraph for each hot path on `debian`, from `perf record -g --user-callchains -e cycles:u`, beside the counter data, with the frame-pointer build's throughput delta published. `BRIEF`.
- At `perf_event_paranoid=2` no kernel frames appear, so a flamegraph cannot show the io_uring against socket difference at all (IU-58). The counter data and CPU split have to carry that comparison.
- Zen 1 has no LBR. A flamegraph hides inlining in a tight 50 ns loop, so `perf annotate` and counter ratios go alongside.

## Browser demo

### T-WASM-1: replay speed against native
- **Target:** the same replay in WASM within 1.55× of native on the same host, in Chrome.
- **Source:** [Jangda et al., USENIX ATC 2019](https://www.usenix.org/system/files/atc19-jangda.pdf), MM-83: geomean 1.55× in Chrome, 1.45× in Firefox.
- **Comparability:** SPEC CPU, not a book replay. [Newer work](https://research-repository.st-andrews.ac.uk/handle/10023/27004) reports within 20% on x86-64 and 35% on Armv8 (MM-85, `SUBAGENT`). Wall clock only. `debian` has no browser, so this is a Mac comparison, labelled so.
- **Status:** `VERIFIED`

**What the page must state.** Browsers clamp `performance.now()` to 100 µs in [Chrome](https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/timing/time_clamper.h), or 5 µs when cross-origin isolated (MM-78, `VERIFIED`). [Firefox](https://github.com/mozilla-firefox/firefox/blob/main/modules/libpref/init/StaticPrefList.yaml) and [Safari](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/page/Performance.cpp) use 1 ms, or 20 µs (MM-79, MM-80, `SUBAGENT`). [GitHub Pages cannot set the isolation headers](https://github.com/orgs/community/discussions/13309) (MM-82). [WebAssembly](https://webassembly.github.io/spec/core/appendix/index-instructions.html) has no clock, counter or pinning instruction (MM-89). So messages/sec comes from batches only, and per-message latency cannot be shown at all. wasm32 is limited to [4 GB](https://v8.dev/blog/4gb-wasm-memory) and [Emscripten's](https://emscripten.org/docs/tools_reference/settings_reference.html) default maximum is 2 GB (MM-90, `SUBAGENT`), so the page replays an extract.

## Fuzzing

- **Target:** each fuzzer runs clean for 30 minutes. `BRIEF`.

## Targets that could not be confirmed

| Target | Where we looked |
|---|---|
| Per-message cache and branch misses (T-PERF-1) | Firm talks, HRT, Jane Street, Databento, vendors, OSS repos |
| Numeric SIMD target for ITCH (T-SIMD-1) | GitHub repo and code search, web, academic search |
| Firm-published software p99 or p99.9 for a book | Optiver, Jane Street, HRT, Databento, academic search |
| Allocator-only effect on a book (T-ARENA-1) | Evidence files, cached repos, web search |
| io_uring against `recvmmsg` ratio (T-URING-1) | Axboe, liburing, Cloudflare, LWN, thesis, Envoy, moq-uring |
| True L3 ITCH backtester rate (T-BT-1 context) | hftbacktest, NautilusTrader, LEAN, backtrader, vectorbt |
| 2026 TotalView peak rates | Nasdaq's live report is gone; newest public copy is December 2024 |
| Nasdaq order-entry latency distribution | Nasdaq co-location pages, OUCH docs, academic papers |

## Code-quality bar

Drawn from a review of 28 ITCH and order-book repos plus low-latency C++ libraries (`code-quality.md`). Each criterion can be tested. Links show where a reviewed repo meets it, and counterexamples show where a well-known repo does not. File-and-line claims here are from the code-quality subagent's clones and were not all re-read by the session. Two are marked `above the OSS bar`: no reviewed repo meets them.

**Wire parsing**

1. No pointer cast to a multi-byte type over wire bytes; decode with `memcpy` plus `std::byteswap`, or `start_lifetime_as` over byte-array structs. Test: CI grep plus UBSan. Trace: [`wire.hpp:12-18`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/include/itch/wire.hpp#L12-L18), [`parser.hpp:31`](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/include/itch/parser.hpp#L31). Counterexample: [`itch.h:91`](https://github.com/charles-cooper/itch-order-book/blob/095ac97ef46c72411a62dd9148d96d2e3ace3305/itch.h#L91).
2. UBSan runs with alignment checks on and no recovery. Test: flags in the preset. Trace: [`CMakePresets.json`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/CMakePresets.json), [`developer-options.cmake:15-19`](https://github.com/simdjson/simdjson/blob/de53ad66f7365c64b31f0d5cbb948063270add4f/cmake/developer-options.cmake#L15-L19). Counterexample: [`build.yml:196`](https://github.com/bbalouki/itchcpp/blob/c4c9152a0eccedcb587ac71e01fcd58b70adfbcf/.github/workflows/build.yml#L196).
3. u48 decode reads exactly 6 bytes. Test: decode a final message that ends against a `PROT_NONE` guard page. Trace: [`wire.hpp:24-29`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/include/itch/wire.hpp#L24-L29), [`README.md:294-296`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md#L294-L296).
4. Frame length is checked before any decode, failures are typed, and no input desynchronises the stream. Test: truncate a valid stream at every byte offset. Trace: [`parser.hpp:145-151`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/itch/include/itch/parser.hpp#L145-L151), [`fsm.rs:439-451`](https://github.com/databento/dbn/blob/a77693030595b8f29f6b4c58e63b1b1a9d899b3f/rust/dbn/src/decode/dbn/fsm.rs#L439-L451).
5. All 23 spec types are framed and unknown types skip by length. Test: per-type round trip, plus a synthetic unknown type. Trace: [`parser.hpp:44-113`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/include/itch/parser.hpp#L44-L113), [`tests.cpp:289`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/tests/tests.cpp#L289). Counterexample: [`main.cpp:148-152`](https://github.com/charles-cooper/itch-order-book/blob/095ac97ef46c72411a62dd9148d96d2e3ace3305/main.cpp#L148-L152).
6. Every wire-controlled index and memory growth is bounded. Test: replay locate 65535 and ref 2^64-1 under ASan. Trace: [`order_store.hpp:17`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/include/itch/order_store.hpp#L17), [`book_builder.hpp:73`](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/include/book/book_builder.hpp#L73). Counterexample: [`order_book.h:222`](https://github.com/charles-cooper/itch-order-book/blob/095ac97ef46c72411a62dd9148d96d2e3ace3305/order_book.h#L222).
7. Order events resolve their book from stored order state, and an unknown ref is counted, never dereferenced. Test: E, X, D and U with unknown refs and with a mismatched locate. Trace: [`README.md:562`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md#L562), [`tests.cpp:398`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/tests/tests.cpp#L398).
8. No `assert`, `throw` or abort is reachable from wire content. Errors are counters or values. Test: fuzz in Debug with asserts on. Trace: [`parser.hpp:126-133`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/include/itch/parser.hpp#L126-L133). Counterexamples: [`itch_handler.cpp:167`](https://github.com/chronoxor/CppTrader/blob/3815893630abe5cd2281c9fd3c6775808820b763/source/trader/providers/nasdaq/itch_handler.cpp#L167), [`fast_book.cpp:33-35`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/src/fast_book.cpp#L33-L35).

**Data layout and hot path**

9. Hot structs have `static_assert` on size, alignment and trivial copyability; shared state is padded to the destructive interference size. Test: compile. Trace: [`order.hpp:47-49`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/include/ob/order.hpp#L47-L49), [`SPSCQueue.h:210-235`](https://github.com/rigtorp/SPSCQueue/blob/59a6a938513ea5004817383711ed35d32385d3ee/include/rigtorp/SPSCQueue.h#L210-L235).
10. Large tables are prefaulted, use huge pages, and report the actual outcome in the run output. Test: results file records it. Trace: [`os_memory.cpp:76-77`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/src/os_memory.cpp#L76-L77), [`01302020_results.txt:78`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/results/01302020_results.txt#L78), [`HashMapBenchmark.cpp:29-58`](https://github.com/rigtorp/HashMap/blob/6af3605900a0c9237b8c6c0a0e081f3d57deee23/src/HashMapBenchmark.cpp#L29-L58).
11. Zero allocations after init, proven over a full real day, counting aligned, nothrow and array `new`, with a control book that does allocate. Test: tool exits non-zero on any allocation. Trace: [`alloc_counter.cpp:51-56`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/src/alloc_counter.cpp#L51-L56), [`verify.cpp:64-67`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/tools/verify.cpp#L64-L67), [`01302020_results.txt:125`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/results/01302020_results.txt#L125). Counterexamples: [`steady_state_test.cpp:21-31`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/test/steady_state_test.cpp#L21-L31), [`hashmap.inl:230`](https://github.com/chronoxor/CppCommon/blob/9eba9ba6034a2b408a50fb84593adb8730e5e954/include/containers/hashmap.inl#L230).
12. No virtual or indirect call per message; the core builds with `-fno-exceptions`. Test: CI job. Trace: [`main.cpp:9-24`](https://github.com/charles-cooper/itch-order-book/blob/095ac97ef46c72411a62dd9148d96d2e3ace3305/main.cpp#L9-L24), [`ubuntu24-noexcept.yml:32`](https://github.com/simdjson/simdjson/blob/de53ad66f7365c64b31f0d5cbb948063270add4f/.github/workflows/ubuntu24-noexcept.yml#L32). Counterexamples: [`itch_handler.h:418-439`](https://github.com/chronoxor/CppTrader/blob/3815893630abe5cd2281c9fd3c6775808820b763/include/trader/providers/nasdaq/itch_handler.h#L418-L439), [`parser.cpp:324`](https://github.com/bbalouki/itchcpp/blob/c4c9152a0eccedcb587ac71e01fcd58b70adfbcf/src/parser.cpp#L324).
13. Each branch hint or prefetch has a measured reason in a comment; error paths are `noinline` and cold. Test: review, with a tidy or grep gate for bare hints. Trace: [`unordered_dense.h:76-92`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/include/ankerl/unordered_dense.h#L76-L92), [`unordered_dense.h:194-207`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/include/ankerl/unordered_dense.h#L194-L207).

**Correctness**

14. libFuzzer drives the wire parser and book in CI under ASan and UBSan with asserts on. Test: job exists and fails on a planted crash. Trace: [`ci.yml:72-87`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/.github/workflows/ci.yml#L72-L87), [`fuzzers.yml:92-103`](https://github.com/simdjson/simdjson/blob/de53ad66f7365c64b31f0d5cbb948063270add4f/.github/workflows/fuzzers.yml#L92-L103). Counterexample: [`fuzz_differential.cpp:40-55`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/test/fuzz_differential.cpp#L40-L55).
15. The fuzz harness has an oracle: book invariants or a second implementation, checked after each message. Test: planted book bug is caught by the fuzzer. Trace: [`fuzz_differential.cpp:1-32`](https://github.com/Manav0559/itch-lob-engine/blob/c8b216121c33d6b67f387aea0f260095df7cc934/fuzz/fuzz_differential.cpp#L1-L32), [`fuzz_insert_erase.cpp:19-37`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/test/unit/fuzz_insert_erase.cpp#L19-L37). Counterexample: [`fuzz_parse.cpp:8-13`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/fuzz/fuzz_parse.cpp#L8-L13).
16. Seed corpus is committed and replayed by ctest; every crash uploads an artifact and leaves a regression input. Test: ctest target. Trace: [`run.h:30-54`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/test/fuzz/run.h#L30-L54), [`ci.yml:177-186`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/.github/workflows/ci.yml#L177-L186), [`hdr_histogram_log_test.c:1030`](https://github.com/HdrHistogram/HdrHistogram_c/blob/13a13ece0cf9246a36d8379698913fd5208e653f/test/hdr_histogram_log_test.c#L1030).
17. A slow independent reference book agrees at full depth, after every message, for a full real day; top of book is reconciled against an external source. Test: tool exit code, with message count printed. Trace: [`README.md:95`](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/README.md#L95), [`README.md:389-396`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md#L389-L396). Partial: [`README.md:108-109`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/README.md#L108-L109) (top of book only).
18. Parser tests assert field values against hand-written bytes, not message counts. Test: review. Trace: [`tests.cpp:105`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/tests/tests.cpp#L105), [`dbn_decoder_tests.cpp`](https://github.com/databento/databento-cpp/blob/9ce83c76c2337bf07705ddc893959707760f0a8d/tests/src/dbn_decoder_tests.cpp). Counterexample: [`test_itch_handler.cpp:79-80`](https://github.com/chronoxor/CppTrader/blob/3815893630abe5cd2281c9fd3c6775808820b763/tests/test_itch_handler.cpp#L79-L80).
19. Negative controls: CI plants a race and a logic bug and fails if the detector stays green; tests print how much they compared. Test: the control steps. Trace: [`ci.yml:108-126`](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/.github/workflows/ci.yml#L108-L126), [`ci.yml:492-505`](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/.github/workflows/ci.yml#L492-L505), [`RESULTS.md:161-163`](https://github.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/blob/053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/docs/RESULTS.md#L161-L163).
20. `above the OSS bar`: code mutation of decoder and book runs in CI against a kill-rate threshold. Test: job fails on a surviving mutant. Nearest: [`mutate.py:1-30`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/scripts/mutate/mutate.py#L1-L30) (tool, runs not in CI), [`mutation_check.py:1-9`](https://github.com/rpgr18/matching-engine/blob/b7bab44b2a71f924be46fff9459a21c69bf4de96/scripts/mutation_check.py#L1-L9) (eight mutants, not in CI), [`split-replay-gate.sh:12-21`](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/scripts/split-replay-gate.sh#L12-L21) (hand-applied).

**Tooling**

21. Warning set includes `-Wconversion -Wsign-conversion -Wshadow -Wold-style-cast` under `-Werror`. Test: build. Trace: [`CMakeLists.txt:42-54`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/CMakeLists.txt#L42-L54), [`CompilerWarnings.cmake:40-78`](https://github.com/databento/databento-cpp/blob/9ce83c76c2337bf07705ddc893959707760f0a8d/cmake/CompilerWarnings.cmake#L40-L78).
22. clang-tidy runs in CI as errors at a pinned version. Test: job. Trace: [`ci.yml:276`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/.github/workflows/ci.yml#L276), [`main.yml:41-45`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/.github/workflows/main.yml#L41-L45), [`.clang-tidy:27`](https://github.com/databento/databento-cpp/blob/9ce83c76c2337bf07705ddc893959707760f0a8d/.clang-tidy#L27).
23. ASan, UBSan and TSan run the whole suite with no recovery, and TSan covers real threaded code if any exists. Test: jobs. Trace: [`ci.yml:191-226`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/.github/workflows/ci.yml#L191-L226), [`sanitizer.yml:21-22`](https://github.com/google/benchmark/blob/fbeb9ce2c8812f76103640e46d843a410dcefb89/.github/workflows/sanitizer.yml#L21-L22).
24. Actions are pinned to full SHAs and dependencies by hash. Test: grep gate. Trace: [`ccpp.yml:48`](https://github.com/rigtorp/HashMap/blob/6af3605900a0c9237b8c6c0a0e081f3d57deee23/.github/workflows/ccpp.yml#L48), [`CMakeLists.txt:10-11`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/CMakeLists.txt#L10-L11), [`fmt.wrap:5`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/subprojects/fmt.wrap#L5). Field: 0/28 on actions.
25. CMake presets cover every configuration; CI invokes presets only; GCC and Clang with both libstdc++ and libc++. Test: CI uses `--preset`. Trace: [`ci.yml:83-94`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/.github/workflows/ci.yml#L83-L94), [`ci.yml:18-28`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/.github/workflows/ci.yml#L18-L28), [`main.yml:95-125`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/.github/workflows/main.yml#L95-L125).

**Benchmarks and docs**

26. CI gates regressions by paired same-run ratios, never absolute numbers. Test: gate fails on a planted slowdown. Trace: [`bench.yml:1-9`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/.github/workflows/bench.yml#L1-L9), [`ci.yml:73-115`](https://github.com/Manav0559/itch-lob-engine/blob/c8b216121c33d6b67f387aea0f260095df7cc934/.github/workflows/ci.yml#L73-L115).
27. Latency and throughput come from separate passes. Per-type percentiles to p99.99 and max are published for a full real day with the timer floor shown. Test: result file. Trace: [`BENCHMARKS.md:515-525`](https://github.com/Akash001uts/orderbook/blob/d7d511ac7d2c8ddd4cd55f808114fa78cb7be5d6/docs/BENCHMARKS.md#L515-L525), [`README.md:521-527`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md#L521-L527).
28. `above the OSS bar`: the same full-day run replays at recorded feed timing on a fixed schedule and reports both naive and coordinated-omission-corrected tails. Nearest: [`README.md:120-147`](https://github.com/Other-Now/tick2trade/blob/bdbb326713f1cc0c5e7589b6a4e0303f4c2c8e63/README.md#L120-L147), [`hdr_histogram.c:651-683`](https://github.com/HdrHistogram/HdrHistogram_c/blob/13a13ece0cf9246a36d8379698913fd5208e653f/src/hdr_histogram.c#L651-L683).
29. Environment is recorded and enforced: isolation, governor, boost, SMT, and timer calibration, with pin failure fatal. Test: harness refuses to publish otherwise. Trace: [`README.md:407-408`](https://github.com/groovg/itch-book/blob/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md#L407-L408), [`reducing_variance.md:103-131`](https://github.com/google/benchmark/blob/fbeb9ce2c8812f76103640e46d843a410dcefb89/docs/reducing_variance.md#L103-L131), [`README.md:100-116`](https://github.com/Other-Now/tick2trade/blob/bdbb326713f1cc0c5e7589b6a4e0303f4c2c8e63/README.md#L100-L116). Counterexamples: [`bench.cpp:210-211`](https://github.com/tahsinj/nasdaq-feed-handler/blob/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5/tools/bench.cpp#L210-L211), [`SPSCQueueBenchmark.cpp:43-44`](https://github.com/rigtorp/SPSCQueue/blob/59a6a938513ea5004817383711ed35d32385d3ee/src/SPSCQueueBenchmark.cpp#L43-L44).
30. Every published number is generated by a committed command and checked against its artifact in CI, with trade-offs stated beside it. Test: `--check` step. Trace: [`ci.yml:283-288`](https://github.com/KareemJandali/itchbook/blob/3c646f9da182136125059243b475f7b8baa53773/.github/workflows/ci.yml#L283-L288), [`README.md:16`](https://github.com/Manav0559/itch-lob-engine/blob/c8b216121c33d6b67f387aea0f260095df7cc934/README.md#L16), [`README.md:47-51`](https://github.com/martinus/unordered_dense/blob/7c58a8b7a48e790fe72f6508eb3b46317cbba9d5/README.md#L47-L51).

Correction to criterion 17: the mohith link covers 50 M real messages, not a full day. No reviewed repo diffs at full depth after every message of a full real day, so this project treats it as above the OSS bar.
