# What an L3 book costs on a small x86 core, measured

On a small x86 core this order book is bound by memory latency: on an embedded Zen 1 the fast designs run about the same instructions per message, and their speed follows their DRAM fills. The default, flat price levels with an order index paged by reference, makes 1.972 fills per message and rebuilds every symbol's book at 5,145,430 messages/s, 2.54× a `std::map` book on the same core, so T-DESIGN-1, at least 4.0× the map book's throughput, is missed on the Zen 1 host. Most fills left are first touches of orders over a second old, which the small shared L3 cannot keep; a software look-ahead that hides some is faster there and slower on an Apple M4 Max, so it is off by default ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

Most speed targets in [BENCHMARKS.md](BENCHMARKS.md) are the best figures other open-source books and parsers have published on newer desktop and server x86 parts, so on this host most fall short; each names its source, or why it has none.

Every figure links to the results file it comes from, and a script checks that the figure is in that file beside a word its sentence shares, and that each "met", "missed", "passes" or "fails" names a target and agrees with that target's result; it cannot tell when two figures of one sentence swap places, or when a figure is copied from another place in its file that shares a word, and it does not read a short number on a line that cites no file. What this page leaves out is in [docs/DETAILS.md](docs/DETAILS.md). <!-- verdicts: no target named -->

## What was built, and on what

The project parses Nasdaq TotalView-ITCH 5.0 and rebuilds the full-depth (L3) book of every symbol, with a reference book that checks it, a backtester, a market maker, a single-producer single-consumer (SPSC) ring, a UDP replay and a browser demo. The data is Nasdaq's free sample files, named by day, MMDDYYYY. Ten full sessions, all Nasdaq's but one from its BX exchange, carry the correctness checks, 3,081,392,329 messages in all; one day, 12302019, carries all performance work ([results/oracle_sessions.md](results/oracle_sessions.md)).

Every headline timing is from an AMD Ryzen Embedded V1605B, an embedded Zen 1 part with 4 cores, 8 threads and a 4 MB L3 shared by all, timed threads pinned ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). An Apple M4 Max runs the correctness checks; macOS cannot pin a thread, so a Mac timing is context.

The Zen 1 host is shared with other projects, so I chose to change none of its root-only kernel settings but the CPU governor: no huge pages are reserved (transparent ones are on), memory compaction runs as configured, and profiles are user space only (`perf_event_paranoid` 2).

## How a number is published

- A prediction is committed first, in [results/predictions.md](results/predictions.md), with what would refute it.
- A run is refused unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is in memory (the file in the page cache, or, for the parse rate on 10302019, every page of the bench's own copy around each timed pass). A sampler withdraws a timed process if other work ran beside it.
- Every run, refused and withdrawn ones included, has a row in [results/run_log.md](results/run_log.md); the raw records are not committed.

## Data layout

`FastBook` swaps two parts: the order index, `hash` (open addressing) or `paged` (slots indexed by the reference, which Nasdaq hands out in rising order), and each side's levels, `linked` (a list) or `flat` (one sorted array, best at the end). Records live in arrays sized up front, linked by 32-bit indices.

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

The four designs and the usual baseline (`std::map` levels and an `unordered_map` index, on a fresh or an aged heap), on the Zen 1 host, the day in memory:

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

The age of the order a message names decides its cost: a message naming an order under 1 ms old makes 0.0490 DRAM fills; a message naming an order 1 s old or more makes 4.3729; 54.5% of the messages that name an order name one 1 s old or more, and they take 90.66% of those messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

## Memory

The fast book made 0 allocations after warm-up in 268,744,780 messages on all four designs, counted at every `operator new` and checked against a control book that allocates ([results/oracle_12302019.md](results/oracle_12302019.md)).

T-ARENA-1 asks what the allocator alone is worth: on a linked-level book that changes only its allocator, the index pool ran at 1.238 times glibc malloc's throughput on a fresh heap and mimalloc, an open-source allocator, at 1.128. T-ARENA-1's one hard check fails: a pool that touched every node first must take no page fault in a timed pass, and the index pool took 136, the pointer pool 200 and the oldest-first pool 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)). A diagnostic run, unpublished because the host was busy, points to memory compaction moving pages under the book; stopping it needs root.

## Branches and the instruction stream

T-PERF-2, the branch-miss rate, is missed by 0.062: `paged-flat` makes 2.102 branch misses per message against a floor of 2.04, and adds, the most common message, take 50.07% of them; which branch inside an add misses was not traced ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

Prefetching is the largest gain left on the Zen 1 host. A look-ahead of D messages touches a later message's index slot, order record and level early; D=16 gained 16.41%, 6,159,978 vs 5,291,518 messages/s. DRAM fills fell from 2.0139 to 0.8212 per message, paid for with 697.1053 instructions per message vs 397.7893. That D=0 baseline is not the table's `paged-flat` row: another harness, the day mapped, counters read once a pass, a CV of 10.83% ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).

On the M4 Max the look-ahead lost at every distance, -18.01% at its best, D=4, so it is off by default; the Mac read no miss counters, so why is not known ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

SIMD wins a field decode and loses the parse: SSSE3 decodes an Add Order faster, but over the whole parse on the Zen 1 host scalar takes 35.099 cycles per message, SSSE3 46.282 and AVX2 48.105 ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)). An unpublished counter run points to the type dispatch and the moves from vector to integer registers. With the book behind the parser SSSE3 ties scalar, and AVX2 takes 1.012 times scalar's cycles, 190.228 ns per message vs 187.966, so the default stays scalar ([results/simd_book_debian_12302019.md](results/simd_book_debian_12302019.md)).

## The parse rate

Timed alone on the Zen 1 host over 12302019 in memory ([results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md)), the framing walk, T-PARSE-1, ran a median of 298,983,253 messages/s and is missed by 410.02 M msg/s; T-PARSE-2, the decode of the ten book-affecting types as itch-book (an open-source C++ ITCH book) does it, ran a median of 140,969,135 messages/s and is missed by 58.03 M msg/s. Both bars were set on a Zen 5 with DDR5, and the parse bench reads no counters, so the host's share of either gap was not measured. T-PARSE-3, the strict decode of every field, ran a median of 117,739,800 messages/s, above its bar of 93.9 M msg/s, which is set on another day, so on 12302019 T-PARSE-3 has no verdict.

T-PARSE-3 is not measured on its bar's own day, 10302019, and a run is registered: on the Zen 1 host the file in the page cache and the bench's copy of it do not fit in memory together, so the run reads the day into the bench's own memory and checks every page of that copy before and after each timed pass ([results/predictions.md](results/predictions.md)).

No parse rate was timed on the Mac: the three bars are set on x86 Linux, which gives a macOS figure no verdict.

## Beyond the book

### The SPSC ring

Against open-source C++ queues on the Zen 1 host this ring's median round trip sits mid-field: atomic_queue 133.5 ns, moodycamel 169.3, rigtorp 198.5, folly 220.9, xenium_vyukov 223.4, project 229.3 (this ring), boost 264.5 and mutex 781.3; so T-SPSC-1, this ring against three published ones, is missed ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)). T-SPSC-2, what each technique is worth, is missed by far: the cached copy of the other side's index gave 1.20× and the mask for the modulo 1.60× against bars of 3.90× and 4.20× set under WSL2 on an i7-9700 ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).

The queue files' one-way latencies start at the push, so a send held back behind a slow push has its wait in no figure. No latency from the due time was measured: it needs the due time carried in each item, a change to the benchmark's measured code that no run has had (BENCHMARKS.md, "What the paced and burst latencies measure").

With the parser on one core and the book on another, the median pipelined time over single-threaded time is 0.954 against a bar of at most 1.31, so T-SPSC-3 is met; but the single-threaded runs varied with a CV of 6.25%, so the saving is within their spread ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

### UDP receive: sockets vs io_uring

A paced sender replays the day as MoldUDP64, Nasdaq's UDP framing, over loopback on the Zen 1 host. S4, spinning on `recvmmsg` (many datagrams a call), carried the most with no loss at 1,400 bytes a datagram, 2,830,848 msg/s; blocking sockets and most modes of io_uring, Linux's shared-ring interface, tied one bisection step below, and the two that add task running or a kernel polling thread came last, their buffer rings running dry. T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak rate, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

### The backtester and the market maker

The backtester queues simulated orders among the real ones by order reference, so only depletion ahead of an order advances it, with latency, dated Nasdaq fees and partial fills. T-BT-1, the whole day with a quoting strategy, is met on the Zen 1 host at 4.68 M engine events/s vs a bar of 2.00 M; its median replay rate is 4,570,542 messages/s ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).

T-BT-3, four symbols with 100,000 simulated orders in real queues, is missed on the Zen 1 host at 3,509,307 messages/s, 1.10 M messages/s under the bar; runs of the same build differed and read no counters, so the cause is not known ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)). On the Mac, where its bar was set, T-BT-3 is met.

The market maker, the Avellaneda-Stoikov quoting model, splits its profit and loss into spread capture, adverse selection and inventory, summing exactly to the ledger's; on AMD zero latency turned -25.96 dollars into +1.23: accounting, not an edge ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

Compiled to WebAssembly, `FastBook` replays one symbol in the browser at 1.19× native's time; the bar names Google Chrome and the run used Chrome for Testing, so T-WASM-1 has no verdict ([results/wasm_replay_mac_12302019.md](results/wasm_replay_mac_12302019.md)).

## Correctness

### The reference book

A deliberately simple reference book replays each session beside the fast book, reading the wire at its own offsets. After every message the oracle compares what that message could change and checks every store's slot; it hashes the whole book every million messages, and a proof in [docs/NOTES.md](docs/NOTES.md) shows the rest cannot diverge.

Over all ten sessions there were 0 divergences in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

### The book is sometimes crossed, and that is the feed

T-INV-2, the book is never crossed, fails strictly: on 12302019 there were 18,958 crossed observations, 18,304 with the symbol not in normal trading and the other 654 inside a reopening window. An open-source ITCH book, carteret, publishes 8,580 crossed book messages for this day. Its unit is a book message after which the book is crossed, not an observation after every message, and it counts one crossed replace twice; counted once per message, its unit gives 8,579 ([results/oracle_12302019.md](results/oracle_12302019.md)).

I chose a scoped rule as a separate check, T-INV-3: in normal trading a book may be crossed only by a cross left from a halt that has not yet cleared. T-INV-3 passes, and T-INV-2 strict stays beside it.

### The trade prints

Each print check is exact. T-TRADE-5 requires every execution in normal trading to hit the best level, and on 12302019 all 5,683,178 did ([results/trades_12302019.md](results/trades_12302019.md)). T-TRADE-5 fails on two sessions, each just after a resumption with that book crossed. T-TRADE-3, each symbol's volume against the SEC's MIDAS file, fails on four of the ten sessions: on one MIDAS lists a symbol twice, and on the rest a few symbols' feed volume is above MIDAS, unexplained ([RESULTS.md](RESULTS.md)).

### The fault matrix and fuzzing

The fault matrix plants a bug in a scratch copy and requires the named check, this page's number gate included, to catch it.

The last full run on the Mac detected 1608 of 1608 faults, after 316 control commands succeeded on the clean tree; 14 faults for Linux only are not run on the Mac, and their run on the Zen 1 host is recorded in docs/NOTES.md ([results/fault_matrix.md](results/fault_matrix.md)).

Each of the nine fuzz targets ran for 1,800 s on the Mac under ASan and UBSan, the compiler's memory and undefined-behaviour checks, and every one was clean: exit 0, 0 crashes and no input saved ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).

### The final review

Every finding of the final review, with its outcome and the commit, test or note that shows it, is in [results/final_review.md](results/final_review.md).

## What a reviewer would ask

- **Is the gap to T-BOOK-1, book throughput, the clock?** Not measured per cycle: a message takes a median of 1141.088 cycles, an empty counting span 444.0739, and the bar's host published none ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **Why is the ring slower than rigtorp's?** Not measured: no counter was read, and a line for each index and cached copy was not timed; here a side's index shares a block with its copy of the other's ([include/itch/spsc.hpp](include/itch/spsc.hpp)).
- **Why is the look-ahead off, not on for one target?** On the M4 Max it raises instructions per message from 384.4524 (D=0) to 640.4915 (D=4); a per-target default and a cheaper look-ahead were not tried ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).
- **What happens at capacity?** `apply()` refuses the message as `capacity_exhausted`, and the book is wrong from there on, with no growth or resync; the busiest session peaked at 1,964,977 live orders and 805,782 live levels, against `1 << 22` and `1 << 21` sized ([results/oracle_07302019.md](results/oracle_07302019.md)).
- **How often does the paged index fall back to its hash?** Not measured: the index counts it, and no results file gives the count.
- **Does a delete that empties a level search its side twice?** Yes, `close()` searches again ([include/itch/book_levels.hpp](include/itch/book_levels.hpp)); its cost is not measured.
- **Is the framing walk one serial chain of loads?** Yes, each length gives the next offset; its cycles per frame are not measured.
- **Is the pipeline worth having?** Not here: the saving is within the noise, and in the first repetition the parser found the ring full 22,248,425,075 times, and the book thread found it empty 843,507 times ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

## Every target missed or not measured

T-SIMD-1 (SIMD vs scalar parsing), T-URING-1 (io_uring vs sockets) and T-ARENA-1's timings have no numeric bar. T-TRADE-4, volume against Cboe's figures, is held: Cboe's terms bar using its data to check other data without consent.

<!-- missed targets: begin, generated by scripts/results_table.py, do not edit -->
Every line is on the Zen 1 host unless it names the M4 Max.

- **T-BOOK-1, true L3 rebuild, all symbols:** rate 5.15 M msg/s against ≥ 16.4 M msg/s, rate by 11.25 M msg/s; speed follows DRAM fills; the host's share not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-BOOK-2, L3 rebuild with BBO tracking through mmap:** rate 5.09 M msg/s against ≥ 12.5 M msg/s, rate by 7.41 M msg/s; speed follows DRAM fills; the host's share not established. [file](results/bench_book_paged-flat_mapped_top_debian_12302019.md)
- **T-LAT-1, per-operation latency, FIFO book:** add p50 100.10 ns … against add 60, 447, 683 …, add p50 by 40.10 ns …; old orders take most fills; the tail's cause not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-2, all-message latency distribution:** p50 140.14 ns … against p50 ≤ 121 …, p50 by 19.14 ns …; old orders take most fills; the tail's cause not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-3, rate budgets:** mean 194.35 ns … against mean service ≤ 393 ns a message …, p99.9 by 722.00 ns; old orders take most fills; the tail's cause not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-4, all-message distribution, FIFO book, on our reference day:** p50 120.35 ns … against p50 ≤ 100 …, p50 by 20.35 ns …; old orders take most fills; the tail's cause not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-5, tightest all-message tail found:** p50 140.14 ns … against p50 ≤ 127 …, p50 by 13.14 ns …; old orders take most fills; the tail's cause not established. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-DESIGN-1, fast book against a map-based book:** 2.54× over map-fresh … against ≥ 4.0× the map book's throughput …, over map-fresh by 1.46× …; one more in RESULTS.md; the map's extra time is DRAM misses. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-PERF-1, last-level misses per message:** 2.002 raw, `paged-flat` against `r0843:u` per book message ≤ 0.623, by 1.379; old orders take most fills; the host's share not established. [file](results/counters_by_type_paged-flat_debian_12302019.md)
- **T-PERF-2, branch misses per message:** 2.102 raw, `paged-flat` against branch misses per message ≤ 2.04, by 0.062; cause not established. [file](results/counters_by_type_paged-flat_debian_12302019.md)
- **T-INV-2, the book is never crossed:** bar never crossed …, M4 Max, fails strictly on nine of ten sessions; the feed's crosses, outside normal trading. [file](results/oracle_12302019.md)
- **T-TRADE-3, continuous volume against SEC MIDAS:** 6 violations against exact, with no tolerance, M4 Max, shares …; three more in RESULTS.md; TAP listed twice in MIDAS. [file](results/trades_01302020.md)
- **T-TRADE-5, continuous executions hit the best price:** 2 violations against exact, with no tolerance, M4 Max, each `E`'s price against the best …; one more in RESULTS.md; just after a crossed resumption; cause not established. [file](results/trades_03272019.md)
- **T-PARSE-1, framing walk:** 298.98 M msg/s … against ≥ 709 M msg/s and 21.8 GB/s, 410.02 M msg/s and 12.62 GB/s under the bar; the host's share not established. [file](results/parse_rate_debian_12302019.md)
- **T-PARSE-2, decode of the book-affecting types:** 140.97 M msg/s … against ≥ 199 M msg/s and 6.1 GB/s, 58.03 M msg/s and 1.77 GB/s under the bar; the host's share not established. [file](results/parse_rate_debian_12302019.md)
- **T-PARSE-3, strict decode of every field:** 117.74 M msg/s … against ≥ 93.9 M msg/s and 2.89 GB/s …, above the bar on 12302019 …; not measured on that day; a run is registered. [file](results/parse_rate_debian_12302019.md)
- **T-BT-3, real-day replay with shadow orders in real queues:** 3.51 M messages/s against ≥ 4.61 M messages/s, 1.10 M messages/s under the bar; not the queue walk; cause not established. [file](results/backtest_shadow_debian_12302019.md)
- **T-MM-2, reproduce Avellaneda and Stoikov's own tables:** 1.4918, 1.3490, 3.0317 against 1.49 at γ = 0.1 …, M4 Max, γ = 1: 3.03 at two decimals against 3.02; the simulation's time grid. [file](results/mm_as_tables.md)
- **T-SPSC-1, round trip against boost, moodycamel and rigtorp:** 1.15× against ≥ 1.16×; two more in RESULTS.md; no counters read; cause not established. [file](results/spsc_queues_debian_12302019.md)
- **T-SPSC-2, throughput gains from the known techniques:** 0.89× against ≥ 3.90×; three more in RESULTS.md; no counters read; cause not established. [file](results/spsc_ablation_debian_12302019.md)
- **T-ARENA-1, measured effect on book latency:** page faults in the timed passes of pool-index 136 … against none: UNCONFIRMED in BENCHMARKS.md …, the check allows none; two more in RESULTS.md; cause not established. [file](results/alloc_arms_base_debian_12302019.md)
- **T-WASM-1, replay speed against native:** 1.19× against ≤ 1.55× native …, M4 Max, no verdict …; the bar's browser is not installed. [file](results/wasm_replay_mac_12302019.md)
- **T-BOOK-4, L3 rebuild on Apple silicon, busier day:** not measured, M4 Max, against ≥ 14.4 M msg/s on 01302019; macOS only; every try withdrawn or stopped. [file](results/bench_book_paged-flat_ram_mac_12302019.md) <!-- figures: not measured -->
- **T-TRADE-2, cross volume per symbol against Nasdaq's CrossStats:** not measured, M4 Max, against exact per symbol against CrossStats; CrossStats is Nasdaq's file of its own crosses, and this is a BX feed. [file](results/trades_01302019_BX.md) <!-- figures: not measured -->

Every row, with its why in full: [RESULTS.md](RESULTS.md#every-target-missed-or-not-measured).
<!-- missed targets: end -->

## What this does not show

- **One host of each kind.** No x86 host with more cache was available to split each gap into the host's share and the design's.
- **One venue, one timing day, one build.** Other feeds and message mixes may rank the designs differently; every timing is Clang 23.1.4, -O3, no -march flag ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **No network, no market impact.** Loopback only; the backtester's feed cannot react to the strategy.
