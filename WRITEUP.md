# What an L3 book costs on a small x86 core, measured

On a small x86 core this order book is bound by memory latency: on an embedded Zen 1 the fast designs run about the same instructions per message, and their speed follows their DRAM fills. The default, flat price levels with an order index paged by reference, makes 1.972 fills per message and rebuilds every symbol's book at 5,145,430 messages/s, 2.54× a `std::map` book on the same core, so T-DESIGN-1, 4.0× over that map book, is missed on the Zen 1 host. Most fills left are first touches of orders over a second old, which the small shared L3 cannot keep; a software look-ahead that hides some is faster there and slower on an Apple M4 Max, so it is off by default ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

Most speed targets in [BENCHMARKS.md](BENCHMARKS.md) are the best figures other open-source books and parsers have published on newer desktop and server x86 parts, so on this host most fall short; each names its source, or why it has none.

Every figure links to the results file it comes from, and a script checks that the figure is in that file beside a word its sentence shares, and that each "met", "missed", "passes" or "fails" names a target and agrees with that target's result; it cannot tell when two figures of one sentence swap places, or when a figure is copied from another place in its file that shares a word, and it does not read a short number on a line that cites no file. What this page leaves out is in [docs/DETAILS.md](docs/DETAILS.md). <!-- verdicts: no target named -->

## What was built, and on what

The project parses Nasdaq TotalView-ITCH 5.0 and rebuilds the full-depth (L3) book of every symbol, with a reference book that checks it, a backtester, a market maker, a single-producer single-consumer (SPSC) ring, a UDP replay and a browser demo. The data is Nasdaq's free sample files, named by day as MMDDYYYY. Ten full sessions, all Nasdaq's but one from its BX exchange, carry the correctness checks, 3,081,392,329 messages in all; one day, 12302019, carries all performance work ([results/oracle_sessions.md](results/oracle_sessions.md)).

Every headline timing is from an AMD Ryzen Embedded V1605B, a Zen 1 part for embedded boards with 4 cores, 8 threads and a 4 MB L3 shared by all its cores, timed threads pinned ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). An Apple M4 Max runs the correctness checks; macOS cannot pin a thread, so a Mac timing is context.

The Zen 1 host is shared with other projects, so I chose to change none of its root-only kernel settings but the CPU governor: no huge pages are reserved (transparent ones are on), memory compaction runs as configured, and profiles are user space only (`perf_event_paranoid` 2).

## How a number is published

- A prediction is committed first, in [results/predictions.md](results/predictions.md), with what would refute it.
- A run is refused unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is in memory; a sampler withdraws a timed process if other work ran beside it.
- Every run, refused and withdrawn ones included, has a row in [results/run_log.md](results/run_log.md); the raw records are not committed.

## Data layout

`FastBook` swaps two parts: the order index, `hash` (open addressing) or `paged` (slots indexed by the reference itself, which Nasdaq hands out in rising order), and each side's levels, `linked` (a list from the best price) or `flat` (one sorted array, best at the end). Records live in arrays sized up front, linked by 32-bit indices.

The records, with sizes from the headers' `static_assert`s ([include/itch/](include/itch/)):

| What | Bytes | Fields |
|---|---|---|
| `BookOrder` | `24` | reference; shares, level, older and newer order in the FIFO |
| `FlatLevel` | `32` | total shares; price, order count, FIFO front and back; locate, side |
| `LinkedLevel` | `40` | as `FlatLevel`, plus the better and worse level |
| flat side's entry | `4 + 4` | parallel arrays of keys and level slots, so a search reads keys only, `16` to a `64`-byte line; an ask's key is `~price`, so the best is the largest key on both sides |
| flat side's header | `12` | start, count and capacity of its block, a power of two from one arena |
| paged-index page | `256 × 4` | an order slot for each of `256` consecutive references, found through a `4`-byte directory entry |

A message that names an order makes a chain of dependent loads (directory entry, page slot, order record, level; for a new order the side's header and the end of its keys), so their misses cannot overlap. Cache lines per operation were not counted; DRAM fills per message were: a delete 2.5544, an execute 2.2520, a replace 3.1322 and an add 1.3051 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

The four designs, and the baseline most people would start with (`std::map` levels, an `unordered_map` index, on a fresh or an aged heap), on the Zen 1 host with the day in memory:

| Arm | Messages/s | Book memory, MiB | Instructions per message | DRAM fills per message | File |
|---|---|---|---|---|---|
| `paged-flat` | 5,145,430 | 662.8 | 581.789 | 1.972 | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| `hash-flat` | 4,635,731 | 225.5 | 566.446 | 2.808 | [results/bench_book_hash-flat_ram_debian_12302019.md](results/bench_book_hash-flat_ram_debian_12302019.md) |
| `hash-linked` | 2,560,055 | 211.0 | 553.422 | 4.647 | [results/bench_book_hash-linked_ram_debian_12302019.md](results/bench_book_hash-linked_ram_debian_12302019.md) |
| `paged-linked` | 2,542,188 | 648.3 | 570.100 | 3.751 | [results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md) |
| `map-fresh` | 2,028,642 | 294.0 | 695.420 | 5.287 | [results/bench_book_map-fresh_ram_debian_12302019.md](results/bench_book_map-fresh_ram_debian_12302019.md) |
| `map-aged` | 1,832,255 | 393.0 | 683.516 | 5.711 | [results/bench_book_map-aged_ram_debian_12302019.md](results/bench_book_map-aged_ram_debian_12302019.md) |

The fast designs differ in how long they wait, not in how much they do: their instructions are close, and their speed falls as their fills rise. Both counter columns include the counter reads, which an empty span measures at 308.0001 instructions and 0.0000 DRAM fills, so instruction differences are larger in proportion than they look ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

Flat levels beat linked ones because a flat side is one array and most adds land near the best: 57.27% of add searches pass one better level or none ([results/window_12302019.md](results/window_12302019.md)). The paged index beats the hash and pays in memory, as the table shows; neither an identity hash nor chaining closed the gap.

The age of the order a message names decides its cost: a message naming an order under 1 ms old makes 0.0490 DRAM fills; a message naming an order 1 s old or more makes 4.3729; 54.5% of the messages that name an order name one 1 s old or more, and they take 90.66% of those messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). How much of the gap a larger cache would close was not measured.

## Memory

The fast book made 0 allocations after warm-up in 268,744,780 messages on all four designs, counted at every `operator new` and checked against a control book that allocates ([results/oracle_12302019.md](results/oracle_12302019.md)).

On a linked-level book that changes only its allocator, the index pool ran at 1.238 times glibc malloc's throughput on a fresh heap and mimalloc, an open-source allocator, at 1.128. T-ARENA-1's one hard check fails: a pool that touched every node first must take no page fault in a timed pass, and the index pool took 136, the pointer pool 200 and the oldest-first pool 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)). A diagnostic run, not published because the host was doing other work, points to memory compaction moving base pages under the running book; stopping it needs root on a shared host.

## Branches and the instruction stream

T-PERF-2, the branch-miss rate, is missed by 0.062: `paged-flat` makes 2.102 branch misses per message against a floor of 2.04, and adds, the most common message, take 50.07% of them; which branch inside an add misses was not traced ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

Prefetching is the largest gain left on the Zen 1 host. A look-ahead of D messages touches a later message's index slot, order record and level early; D=16 gained 16.41%, 6,159,978 vs 5,291,518 messages/s. DRAM fills fell from 2.0139 to 0.8212 per message, paid for with 697.1053 instructions per message vs 397.7893. That D=0 baseline is not the table's `paged-flat` row: another harness, the day mapped, counters read once a pass, a CV of 10.83% ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).

On the M4 Max the look-ahead lost at every distance, -18.01% at its best, D=4, so it is off by default; why was not established, since the Mac read no miss counters ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

SIMD wins a field decode and loses the parse: SSSE3 decodes an Add Order faster, but over the whole parse on the Zen 1 host scalar takes 35.099 cycles per message, SSSE3 46.282 and AVX2 48.105 ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)). A counter run that is not published points to the type dispatch and the moves from vector to integer registers. With the book behind the parser SSSE3 ties scalar, and AVX2 takes 1.012 times scalar's cycles, 190.228 ns per message vs 187.966, so the default stays scalar ([results/simd_book_debian_12302019.md](results/simd_book_debian_12302019.md)).

## The parse rate

Timed alone on the Zen 1 host over 12302019 in memory ([results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md)), the parser's T-PARSE-1, the framing walk, ran a median of 298,983,253 messages/s and is missed by 410.02 M msg/s; T-PARSE-2, the decode of the ten book-affecting types as itch-book (an open-source C++ ITCH book) does it, ran a median of 140,969,135 messages/s and is missed by 58.03 M msg/s. Both bars were set on a Zen 5 with DDR5, and the parse bench reads no counters, so the host's share of either gap was not measured. T-PARSE-3, the strict decode of every field, ran a median of 117,739,800 messages/s, above its bar of 93.9 M msg/s, but the bar is set on another day, so on 12302019 T-PARSE-3 has no verdict.

T-PARSE-3 on its bar's own day, 10302019, is not measured; a run is registered. The condition used so far asks for every page of the day's file in the page cache before the first process, and the host's 15,700.85 MiB of memory cannot hold the 8,616.46 MiB file cached and the bench's own copy at once, 17,232.93 MiB. The registered run checks what the timed passes read instead: every page of the bench's own copy, before and after each pass ([results/predictions.md](results/predictions.md), the entry for T-PARSE-3 "with the bench's own copy as the input condition"). <!-- figures: not measured -->

The Mac's parse rate is not measured: the parse driver runs only on Linux, where it can pin the thread, and every parse bar was set on x86.

## Beyond the book

### The SPSC ring

Against open-source C++ queues on the Zen 1 host this ring's median round trip sits mid-field: atomic_queue 133.5 ns, moodycamel 169.3, rigtorp 198.5, folly 220.9, xenium_vyukov 223.4, project 229.3 (this ring), boost 264.5 and mutex 781.3; so T-SPSC-1, this ring against three published ones, is missed ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)). T-SPSC-2, what each technique is worth, is missed by far: the cached copy of the other side's index gave 1.20× and the mask for the modulo 1.60× against bars of 3.90× and 4.20× set under WSL2 on an i7-9700 ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).

The queue files' one-way latencies are measured from the push, so a send held back behind a slow push has its wait in no figure. No latency from the due time was measured: it needs the due time carried in each item, a change to the benchmark's measured code that no run has had (BENCHMARKS.md, "What the paced and burst latencies measure").

With the parser on one core and the book on another, the median pipelined time over single-threaded time is 0.954 against a bar of at most 1.31, so T-SPSC-3 is met; but the single-threaded runs varied with a CV of 6.25%, so the saving is within their spread ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

### UDP receive: sockets vs io_uring

A paced sender replays the day as MoldUDP64, Nasdaq's UDP framing, over loopback on the Zen 1 host. S4, which spins on `recvmmsg`, the socket call that takes many datagrams at once, carried the most with no loss at 1,400 bytes a datagram, 2,830,848 msg/s; blocking sockets and io_uring, Linux's shared-ring interface, tied one bisection step below, and the provided-buffer modes came last, their rings running dry. T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak rate, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

### The backtester and the market maker

The backtester queues simulated orders among the real ones by order reference, so only depletion ahead of an order advances it, with latency, dated Nasdaq fees and partial fills. T-BT-1, the whole day with a quoting strategy, is met on the Zen 1 host at 4.68 M engine events/s vs a bar of 2.00 M; its median replay rate is 4,570,542 messages/s ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).

T-BT-3, four symbols with 100,000 simulated orders in real queues, is missed on the Zen 1 host at 3,509,307 messages/s, 1.10 M messages/s under the bar; runs of the same build differed, none read counters, and the cause was not established ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)). On the Mac, where its bar was set, T-BT-3 is met.

The market maker, the Avellaneda-Stoikov quoting model, splits its profit and loss into spread capture, adverse selection and inventory, which sum exactly to the ledger's total; on the AMD stock zero latency turned -25.96 dollars into +1.23, so it shows the accounting, not an edge ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

Compiled to WebAssembly, the same `FastBook` replays one symbol in the browser demo at 1.19× native's time; the bar names Google Chrome and the run used Chrome for Testing, so T-WASM-1 has no verdict ([results/wasm_replay_mac_12302019.md](results/wasm_replay_mac_12302019.md)).

## Correctness

### The reference book

A deliberately simple reference book replays each session beside the fast book, reading the wire at its own byte offsets. After every message the oracle compares what that message could change (each touched level, the best bid and offer, the order) and checks every store's slot; it hashes the whole book every million messages, and a proof in [docs/NOTES.md](docs/NOTES.md) shows the rest cannot diverge.

Over all ten sessions there were 0 divergences in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

### The book is sometimes crossed, and that is the feed

T-INV-2, the book is never crossed, fails strictly: on 12302019 there were 18,958 crossed observations, 18,304 with the symbol not in normal trading and the other 654 inside a reopening window. An open-source ITCH book, carteret, publishes 8,580 crossed book messages for this day. Its unit is a book message after which the book is crossed, not an observation after every message, and it counts one crossed replace twice; counted once per message, its unit gives 8,579 ([results/oracle_12302019.md](results/oracle_12302019.md)).

I chose a scoped rule as a separate check, T-INV-3: in normal trading a book may be crossed only by a cross left from a halt that has not yet cleared. T-INV-3 passes, and T-INV-2 strict stays beside it.

### The trade prints

Each print check is exact. T-TRADE-5 requires every execution in normal trading to hit the best level, and on 12302019 all 5,683,178 did ([results/trades_12302019.md](results/trades_12302019.md)). T-TRADE-5 fails on two sessions, each just after a resumption with that book crossed. T-TRADE-3, each symbol's volume against the SEC's MIDAS file, fails on four of the ten sessions: on one MIDAS lists a symbol twice, and on the rest a few symbols' feed volume is above MIDAS, unexplained ([RESULTS.md](RESULTS.md)).

### The fault matrix and fuzzing

The fault matrix plants a bug in a scratch copy and requires the named check to catch it, this page's number gate included.

The last full run on the Mac detected 1608 of 1608 faults, after 316 control commands succeeded on the clean tree; the 14 faults that run only on Linux ran on the Zen 1 host, and docs/NOTES.md gives their counts ([results/fault_matrix.md](results/fault_matrix.md)).

Each of the nine fuzz targets ran for 1,800 s on the Mac under ASan and UBSan, the compiler's memory and undefined-behaviour checks, from its committed seeds, and every one was clean: exit 0, 0 crashes and no input saved ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).

### The final review

Every finding of the final review, with its outcome and the commit, test or note that shows it, is in [results/final_review.md](results/final_review.md).

## What a reviewer would ask

- **Is the gap to T-BOOK-1, book throughput, the clock?** Not measured per cycle: a message takes a median of 1141.088 cycles in the counted pass, an empty counting span 444.0739, and the bar's host published no cycle count ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **Why is the ring slower than rigtorp's?** Not measured: no counter was read, and giving each index and cached copy its own line was not timed; here a side's index shares a block with its copy of the other's ([include/itch/spsc.hpp](include/itch/spsc.hpp)).
- **Why is the look-ahead off, not on for one target?** On the M4 Max it raises instructions per message from 384.4524 (D=0) to 640.4915 (D=4); a per-target default and a cheaper look-ahead were not tried ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).
- **What happens at capacity?** `apply()` refuses the message as `capacity_exhausted`, leaving the book unchanged and so wrong from there on; nothing grows or resynchronises, and the busiest session peaked at 1,964,977 live orders and 805,782 live levels, against `1 << 22` and `1 << 21` sized ([results/oracle_07302019.md](results/oracle_07302019.md)).
- **How often does the paged index fall back to its hash?** Not measured on a real day: the index counts it, and no results file gives the count.
- **Does a delete that empties a level search its side twice?** Yes, `close()` searches again ([include/itch/book_levels.hpp](include/itch/book_levels.hpp)); its cost is not measured.
- **Is the framing walk one serial chain of loads?** Yes, each length gives the next offset; its cycles per frame are not measured.
- **Is the pipeline worth having?** Not here: the saving is within the noise, and in the first repetition the parser found the ring full 22,248,425,075 times and the book thread found it empty 843,507 times ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

## Every target missed or not measured

[RESULTS.md](RESULTS.md) has every row, with its file and reasons. T-SIMD-1, T-URING-1 and T-ARENA-1's timings have no numeric bar. T-TRADE-4, volume against Cboe's figures, is held: Cboe's terms bar using its data to check other data without consent.

<!-- missed targets: begin, generated by scripts/results_table.py, do not edit -->
| Target | Bar | Measured | Gap | Host | Why | File |
|---|---|---|---|---|---|---|
| T-BOOK-1 | ≥ 16.4 M msg/s (Ryzen 7 9700X, x86 Linux) | rate 5.15 M msg/s | rate by 11.25 M msg/s | debian | the bar was set on the x86 host named beside it; on this Zen 1 host every fast arm runs about the same instructions a message and its speed follows its DRAM fills; how much of the gap is the host's is not established: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-BOOK-2 | ≥ 12.5 M msg/s (Ryzen 7 9700X, x86 Linux) | rate 5.09 M msg/s | rate by 7.41 M msg/s | debian | the bar was set on the x86 host named beside it; on this Zen 1 host every fast arm runs about the same instructions a message and its speed follows its DRAM fills; how much of the gap is the host's is not established: [Data layout](#data-layout) | [results/bench_book_paged-flat_mapped_top_debian_12302019.md](results/bench_book_paged-flat_mapped_top_debian_12302019.md) |
| T-LAT-1 | add 60, 447, 683; delete 70, 599, 768; replace 110, 709, 945; reduce 50, 548, 734 ns at p50, p99, p99.9, raw (Ryzen 7 9700X, x86 Linux) | add p50 100.10 ns; add p99 540.55 ns; add p99.9 790.81 ns; delete p50 230.23 ns; delete p99 780.80 ns; delete p99.9 1,041.56 ns; replace p50 380.39 ns; replace p99 1,522.05 ns; replace p99.9 2,734.28 ns; reduce p50 150.15 ns; reduce p99 720.73 ns; reduce p99.9 940.96 ns | add p50 by 40.10 ns, add p99 by 93.55 ns, add p99.9 by 107.81 ns, delete p50 by 160.23 ns, delete p99 by 181.80 ns, delete p99.9 by 273.56 ns, replace p50 by 270.39 ns, replace p99 by 813.05 ns, replace p99.9 by 1,789.28 ns, reduce p50 by 100.15 ns, reduce p99 by 172.73 ns, reduce p99.9 by 206.96 ns | debian | as T-BOOK-1, and the tail is not traced to a cause; messages that name an order older than a second take most of the DRAM fills: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-LAT-2 | p50 ≤ 121, p99 ≤ 552, p99.9 ≤ 874 ns, raw (Core i5-12400, x86 Linux under WSL2) | p50 140.14 ns; p99 840.86 ns; p99.9 1,472.00 ns | p50 by 19.14 ns, p99 by 288.86 ns, p99.9 by 598.00 ns | debian | as T-BOOK-1, and the tail is not traced to a cause; messages that name an order older than a second take most of the DRAM fills: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-LAT-3 | mean service ≤ 393 ns a message, p99.9 ≤ 750 ns (a rate budget, for every host) | mean 194.35 ns; p99.9 1,472.00 ns | p99.9 by 722.00 ns | debian | as T-BOOK-1, and the tail is not traced to a cause; messages that name an order older than a second take most of the DRAM fills: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-LAT-4 | p50 ≤ 100, p99 ≤ 620, p99.9 ≤ 1,080 ns, pair subtracted; batch mean ≤ 142.36 ns a book message (Ryzen 9 5950X, x86 Linux) | p50 120.35 ns; p99 820.82 ns; p99.9 1,451.96 ns; batch mean 197.48 ns | p50 by 20.35 ns, p99 by 200.82 ns, p99.9 by 371.96 ns, batch mean by 55.12 ns | debian | as T-BOOK-1, and the tail is not traced to a cause; messages that name an order older than a second take most of the DRAM fills: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-LAT-5 | p50 ≤ 127, p99 ≤ 446, p99.9 ≤ 778 ns, raw (Core i7-13650HX, x86) | p50 140.14 ns; p99 840.86 ns; p99.9 1,472.00 ns | p50 by 13.14 ns, p99 by 394.86 ns, p99.9 by 694.00 ns | debian | as T-BOOK-1, and the tail is not traced to a cause; messages that name an order older than a second take most of the DRAM fills: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-DESIGN-1 | ≥ 4.0× the map book's throughput, same run (a ratio on one host, comparable on any host) | over map-fresh 2.54×; over map-aged 2.81× | over map-fresh by 1.46×, over map-aged by 1.19× | debian | the map book's extra time here is DRAM misses, not instructions; why the fast book gains less over it on Zen 1 than on the Mac is not established: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| T-DESIGN-1 | ≥ 4.0× the map book's throughput, same run (a ratio on one host, comparable on any host) | over map-fresh 3.79×; over map-aged 4.08× | over map-fresh by 0.21× | macOS | not established; the Mac's counters give no misses: [Data layout](#data-layout) | [results/bench_book_paged-flat_ram_mac_12302019.md](results/bench_book_paged-flat_ram_mac_12302019.md) |
| T-PERF-1 | `r0843:u` per book message ≤ 0.623 | 2.002 raw, `paged-flat` | by 1.379 | debian | the bar was set on a Zen 3 with far more L3; messages naming an order a second old or more take most of the fills: [Branches](#branches-and-the-instruction-stream) | [results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md) |
| T-PERF-2 | branch misses per message ≤ 2.04 | 2.102 raw, `paged-flat` | by 0.062 | debian | not established; replaces miss most a message, and adds take the largest share of all misses: [Branches](#branches-and-the-instruction-stream) | [results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md) |
| T-INV-2 | never crossed, checked strictly after every message | 12302019, strict: 18,958 crossed and 1,018 locked of 268,354,200 two-sided observations; 654 and 10 of 259,196,890 with the market open and the symbol trading; the same on 4 arms | fails strictly on nine of ten sessions | macOS | the feed's, not the book's: every cross is in a symbol not in normal trading or inside a reopening window, and T-INV-3, the rule the user chose, passes: [Correctness](#correctness) | [results/oracle_12302019.md](results/oracle_12302019.md) |
| T-TRADE-3, 01302020 | exact, with no tolerance | 6 violations | shares, the feed's against MIDAS: TAP 219183 against 438366 | macOS | TAP alone: MIDAS lists it twice that day and the tool sums both rows, as docs/NOTES.md records: [Correctness](#correctness) | [results/trades_01302020.md](results/trades_01302020.md) |
| T-TRADE-3, 03272019 | exact, with no tolerance | 2 violations | shares, the feed's against MIDAS: PLAB 72996 against 72896 | macOS | PLAB alone, the feed above MIDAS; cause not established: [Correctness](#correctness) | [results/trades_03272019.md](results/trades_03272019.md) |
| T-TRADE-3, 05302019 | exact, with no tolerance | 38 violations | shares, the feed's against MIDAS: AGYS 17333 against 17332; CAR 325768 against 324966; CSFL 158347 against 157687; DWDP 1260875 against 1260775; RH 108363 against 108261; SO 403518 against 402808; TBT 839702 against 839102; TTC 83530 against 83429; UVV 22593 against 22592; ZS 850263 against 850163 | macOS | ten symbols, the feed above MIDAS; cause not established: [Correctness](#correctness) | [results/trades_05302019.md](results/trades_05302019.md) |
| T-TRADE-3, 08302019 | exact, with no tolerance | 6 violations | shares, the feed's against MIDAS: INTC 3644012 against 3644011 | macOS | INTC alone, the feed above MIDAS in every column; cause not established: [Correctness](#correctness) | [results/trades_08302019.md](results/trades_08302019.md) |
| T-TRADE-5, 03272019 | exact, with no tolerance | 2 violations | each `E`'s price against the best, as ITCH prints prices: RBZ at 216400 with the best at 180500; RBZ at 270000 with the best at 290500 | macOS | two `E` on RBZ away from the best, just after a resumption with its book crossed; the oracle agrees with the reference book that day: [Correctness](#correctness) | [results/trades_03272019.md](results/trades_03272019.md) |
| T-TRADE-5, 05302019 | exact, with no tolerance | 1 violation | each `E`'s price against the best, as ITCH prints prices: ELTK at 98700 with the best at 104200 | macOS | one `E` on ELTK, as with RBZ on the day above: [Correctness](#correctness) | [results/trades_05302019.md](results/trades_05302019.md) |
| T-PARSE-1 | ≥ 709 M msg/s and 21.8 GB/s | 298.98 M msg/s, 9.180 GB/s, the median | 410.02 M msg/s and 12.62 GB/s under the bar | debian | the bar was set on a Zen 5 with DDR5 and is the best of three passes; this is the median, and bench_parse reads no counters, so how much of the gap is the host's is not established: [The parse rate](#the-parse-rate) | [results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md) |
| T-PARSE-2 | ≥ 199 M msg/s and 6.1 GB/s | 140.97 M msg/s, 4.328 GB/s, the median | 58.03 M msg/s and 1.77 GB/s under the bar | debian | as T-PARSE-1: [The parse rate](#the-parse-rate) | [results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md) |
| T-PARSE-3 | ≥ 93.9 M msg/s and 2.89 GB/s, on 10302019 | 117.74 M msg/s, 3.615 GB/s, the median, on 12302019 | above the bar on 12302019, a day other than the bar's; not measured on 10302019 | debian | the bar is set on 10302019, by Lunary on a cloud VM with a Xeon Platinum 8481C, in Rust, over the day memory-mapped, as a median of ten runs; on this host the standalone machine check failed input-resident each time it ran on that day, so P-0030's run was never started; why the day would not stay wholly in the page cache is not established: [The parse rate](#the-parse-rate) | [results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md) |
| T-BT-3 | ≥ 4.61 M messages/s | 3.51 M messages/s | 1.10 M messages/s under the bar | debian | the bar was set on a Mac; the queue walk is a small share of a message's time, so it is not the cause; the rest is not established: [Beyond the book](#beyond-the-book) | [results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md) |
| T-MM-2 | 1.49 at γ = 0.1, 1.35 at γ = 0.01, 3.02 at γ = 1 | 1.4918, 1.3490, 3.0317 | γ = 1: 3.03 at two decimals against 3.02 | macOS | the file traces it to the simulation's time grid, and other grids move it: [Beyond the book](#beyond-the-book) | [results/mm_as_tables.md](results/mm_as_tables.md) |
| T-SPSC-1, boost rtt / project rtt | ≥ 1.16× | 1.15× | 1.15× against ≥ 1.16× | debian | not established: no counters were read: [Beyond the book](#beyond-the-book) | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| T-SPSC-1, project rtt / moodycamel rtt | ≤ 1.00× | 1.35× | 1.35× against ≤ 1.00× | debian | not established: no counters were read: [Beyond the book](#beyond-the-book) | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| T-SPSC-1, project rtt / rigtorp rtt | ≤ 1.00× | 1.16× | 1.16× against ≤ 1.00× | debian | not established: no counters were read: [Beyond the book](#beyond-the-book) | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| T-SPSC-2, cached over uncached, with %: modulo / uncached-modulo | ≥ 3.90× | 0.89× | 0.89× against ≥ 3.90× | debian | the bars were set under WSL2 on another host; here not established, no counters were read: [Beyond the book](#beyond-the-book) | [results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md) |
| T-SPSC-2, cached over uncached, with the mask: shipped / uncached | ≥ 3.90× | 1.20× | 1.20× against ≥ 3.90× | debian | as above: [Beyond the book](#beyond-the-book) | [results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md) |
| T-SPSC-2, mask over %, cached: shipped / modulo | ≥ 4.20× | 1.60× | 1.60× against ≥ 4.20× | debian | as above: [Beyond the book](#beyond-the-book) | [results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md) |
| T-SPSC-2, mask over %, uncached: uncached / uncached-modulo | ≥ 4.20× | 1.19× | 1.19× against ≥ 4.20× | debian | as above: [Beyond the book](#beyond-the-book) | [results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md) |
| T-ARENA-1, the fault check, pools' pages base | none: UNCONFIRMED in BENCHMARKS.md; the check: no page fault in a pool arm's timed passes | page faults in the timed passes of pool-index 136, pool-pointer 200, pool-fifo 146 | the check allows none | debian | a diagnostic found the kernel migrating small pages under the running book during compaction, and collapsing the index into huge pages; its counts are in docs/NOTES.md, not a results file: [Memory](#memory) | [results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md) |
| T-ARENA-1, the fault check, pools' pages transparent | none: UNCONFIRMED in BENCHMARKS.md; the check: no page fault in a pool arm's timed passes | page faults in the timed passes of pool-index 4, pool-pointer 1 | the check allows none | debian | as with base pages: [Memory](#memory) | [results/alloc_arms_transparent_debian_12302019.md](results/alloc_arms_transparent_debian_12302019.md) |
| T-WASM-1 | ≤ 1.55× native, in Google Chrome | 1.19× | no verdict, browser differs: Google Chrome for Testing 153.0.8010.12, not Google Chrome; neither a pass nor a fail; against the bar: 1.19× native, within the 1.55× bar by 0.36× | macOS | the bar names Google Chrome, which is not installed here: [Beyond the book](#beyond-the-book) | [results/wasm_replay_mac_12302019.md](results/wasm_replay_mac_12302019.md) |
| T-BOOK-4 | ≥ 14.4 M msg/s on 01302019 (Apple M2 Pro, macOS, day 01302019) | not measured | not measured | macOS | macOS only; every try was withdrawn or stopped under the rule that withdraws a process while another is busy; 3 runs logged under P-0502, none published: 2 withdrawn, 1 stopped or refused | [results/bench_book_paged-flat_ram_mac_12302019.md](results/bench_book_paged-flat_ram_mac_12302019.md) | <!-- figures: not measured -->
| T-ARENA-1, macOS | none: UNCONFIRMED in BENCHMARKS.md | not measured | not measured | macOS | every try was withdrawn or stopped under the same rule; debian's arms are above; 27 runs logged under P-0701, none published: 22 withdrawn, 5 stopped or refused | none | <!-- figures: not measured -->
| T-TRADE-2, 01302019_BX | exact per symbol against CrossStats | not measured | not measured | macOS | CrossStats is Nasdaq's file of its own crosses, and this is a BX feed | [results/trades_01302019_BX.md](results/trades_01302019_BX.md) | <!-- figures: not measured -->
<!-- missed targets: end -->

## What this does not show

- **One host of each kind.** No x86 host with more cache was available to split each gap into the host's share and the design's.
- **One venue, one timing day, one build.** Other feeds and message mixes may rank the designs differently; every timing is Clang 23.1.4, -O3, no -march flag ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **No network, no market impact.** Loopback only, and the backtester's feed cannot react to the strategy.
