# What an L3 book costs on a small x86 core, measured

On a small x86 core this order book is bound by memory latency: on an embedded Zen 1 the fast designs run about the same instructions per message, and their speed follows how often they wait on DRAM. The default design, flat price levels with an order index looked up by each order's reference number, rebuilds every symbol's book at 5,145,430 messages/s with 1.972 DRAM fills per message, 2.54× a `std::map` book on the same core ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)). Most fills left are first touches of orders over a second old, which the small shared L3 cannot keep; a software look-ahead that hides some is faster there and slower on an Apple M4 Max, so it is off by default.

Most speed targets in [BENCHMARKS.md](BENCHMARKS.md) are the best figures other open-source books and parsers have published, mostly on newer desktop and server x86 parts and a few on Macs, so on this host most fall short; each names its source, or why it has none.

## What was built, and on what

The project parses Nasdaq TotalView-ITCH 5.0 and rebuilds the full-depth (L3) book of every symbol, with a reference book that checks it, a backtester, a market maker, a single-producer single-consumer (SPSC) ring, a UDP replay and a browser demo. The data is Nasdaq's free sample files, named by day, MMDDYYYY. Ten full sessions carry the correctness checks, all from Nasdaq's main market except a day from its BX exchange; one day, 12302019, carries all performance work.

Every headline timing is from an AMD Ryzen Embedded V1605B, an embedded Zen 1 part with 4 cores, 8 threads and a 4 MB L3 shared by all, its timed thread pinned, with turbo and SMT on and no core isolated ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). An Apple M4 Max runs the correctness checks; macOS cannot pin a thread, so a Mac timing is context.

The Zen 1 host is shared with other projects, and I chose to change none of its root-only kernel settings but the CPU governor: no huge pages are reserved, memory compaction runs as configured, and profiles are user space only (`perf_event_paranoid` 2). So no TLB counter was read and no run used reserved huge pages, though the book's arrays sat on transparent ones; both are open.

## How a number is published

Each timing's prediction is committed first, and every run, refused and withdrawn ones included, has a row in the run log ([results/predictions.md](results/predictions.md)). A run is refused unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is in memory (the file in the page cache, or, for the parse rate on 10302019, every page of the bench's copy around each timed pass), and a sampler withdraws a timed process if other work ran beside it.

Every figure here links to the results file it comes from, and a script checks that each is in that file beside a word its sentence shares, and that each verdict agrees with its target's result; it cannot tell when a figure is copied from another place in its file that shares a word. What this page leaves out is in [docs/DETAILS.md](docs/DETAILS.md).

## Data layout

`FastBook` swaps two parts: the order index, `hash` (open addressing) or `paged` (slots indexed by the order reference itself, which Nasdaq hands out in rising order), and each side's levels, `linked` (a list) or `flat` (one sorted array, best at the end). Records live in arrays sized up front, linked by 32-bit indices, and a symbol's side is found directly by its stock locate code, one header per locate and side.

The records, with sizes from the headers' `static_assert`s ([include/itch/](include/itch/)):

| What | Bytes | Fields |
|---|---|---|
| `BookOrder` | `24` | reference; shares, level, older and newer order in the FIFO |
| `FlatLevel` | `32` | total shares; price, order count, FIFO front and back; locate, side |
| `LinkedLevel` | `40` | as `FlatLevel`, plus the better and worse level |
| flat side's entry | `4 + 4` | parallel arrays of keys and level slots, so a search reads keys only, `16` to a `64`-byte line; an ask's key is `~price`, so the best is the largest key on both sides |
| flat side's header | `12` | start, count and capacity of its block, a power of two from one arena |
| paged-index page | `256 × 4` | an order slot for each of `256` consecutive references, found through a `4`-byte directory entry |

A message that names an order makes a chain of dependent loads (directory entry, page slot, order record, level; for a new order the side's header and the end of its keys), so their misses cannot overlap. Cache lines per operation were not counted. What was counted is DRAM fills, demand fills into the L1 cache from local DRAM; it is a floor, since lines the hardware prefetcher fetched are not counted. Per message, a delete makes 2.5544, an execute 2.2520, a replace 3.1322 and an add 1.3051 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

The four designs and the usual baseline (`std::map` levels and an `unordered_map` index, on a fresh or an aged heap), on the Zen 1 host, the day in memory. Both counter columns include reading the counters, which an empty span measures at 308.0001 instructions and 0.0000 DRAM fills per message ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)):

| Arm | Messages/s | Book memory, MiB | Instructions per message | DRAM fills per message | File |
|---|---|---|---|---|---|
| `paged-flat` | 5,145,430 | 662.8 | 581.789 | 1.972 | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| `hash-flat` | 4,635,731 | 225.5 | 566.446 | 2.808 | [results/bench_book_hash-flat_ram_debian_12302019.md](results/bench_book_hash-flat_ram_debian_12302019.md) |
| `hash-linked` | 2,560,055 | 211.0 | 553.422 | 4.647 | [results/bench_book_hash-linked_ram_debian_12302019.md](results/bench_book_hash-linked_ram_debian_12302019.md) |
| `paged-linked` | 2,542,188 | 648.3 | 570.100 | 3.751 | [results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md) |
| `map-fresh` | 2,028,642 | 294.0 | 695.420 | 5.287 | [results/bench_book_map-fresh_ram_debian_12302019.md](results/bench_book_map-fresh_ram_debian_12302019.md) |
| `map-aged` | 1,832,255 | 393.0 | 683.516 | 5.711 | [results/bench_book_map-aged_ram_debian_12302019.md](results/bench_book_map-aged_ram_debian_12302019.md) |

The fast designs differ in how long they wait, not in how much they do: their instructions are close, and their speed falls as their fills rise. T-DESIGN-1, at least 4.0× the map book's throughput, is the ratio itch-book, an open-source C++ ITCH book, published between its fastest book and a naive one on one machine, a ratio that fell on a slower machine (BENCHMARKS.md, T-DESIGN-1); here T-DESIGN-1 is missed ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

Flat levels beat linked ones because a flat side is one array and most adds land near the best: 57.27% of add searches pass one better level or none ([results/window_12302019.md](results/window_12302019.md)). The paged index beats the hash and pays in memory; neither an identity hash nor chaining closed the gap.

The age of the order a message names decides its cost: a message naming an order under 1 ms old makes 0.0490 DRAM fills; a message naming an order 1 s old or more makes 4.3729; 54.5% of the messages that name an order name one 1 s old or more, and they take 90.66% of those messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

## Memory

The fast book made 0 allocations after warm-up in 268,744,780 messages on all four designs, counted at every `operator new` ([results/oracle_12302019.md](results/oracle_12302019.md)).

T-ARENA-1 asks what the allocator alone is worth: on a linked-level book, the index pool ran at 1.238 times glibc malloc's throughput on a fresh heap and mimalloc, an open-source allocator, at 1.128. T-ARENA-1's one hard check fails: a pool that touched every node first must take no page fault in a timed pass, and the index pool took 136, the pointer pool 200 and the oldest-first pool 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)). An unpublished diagnostic run, made while the host was busy, points to memory compaction moving pages; I chose not to turn compaction off on a shared host, so whether that removes the faults is open.

## Branches and the instruction stream

Prefetching is the largest gain left on the Zen 1 host: a look-ahead of D messages touches a later message's index slot, order record and level early, and D=16 gained 16.41%, 6,159,978 vs 5,291,518 messages/s; DRAM fills fell from 2.0139 to 0.8212 per message, paid for with 697.1053 instructions per message vs 397.7893. Those counts are not comparable with the table's: this harness maps the day and counts each whole timed pass, frame walk and look-ahead included, with no counter reads per message, while the table counts each `apply()` with the reads around it and the frame walk outside ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).

On the M4 Max the look-ahead lost at every distance, -18.01% at its best, D=4, so it is off by default; the Mac read no miss counters, so why is not known ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

SIMD wins a field decode and loses the whole parse, where scalar takes 35.099 cycles per message, SSSE3 46.282 and AVX2 48.105 on the Zen 1 host ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)); with the book behind the parser no kernel beats scalar, the default.

## The parse rate

Timed alone on the Zen 1 host over 12302019 in memory ([results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md)), the framing walk, T-PARSE-1, ran a median of 298,983,253 messages/s and is missed by 410.02 M msg/s; T-PARSE-2, itch-book's decode of the ten book-affecting types, ran a median of 140,969,135 messages/s and is missed by 58.03 M msg/s. Both bars were set on a Zen 5, and the parse bench reads no counters, so the host's share of either gap was not measured. T-PARSE-3, the strict decode of every field, ran a median of 117,739,800 messages/s, above its bar of 93.9 M msg/s, which is set on another day, so on 12302019 T-PARSE-3 has no verdict.

T-PARSE-3's own day is 10302019, and it has not been measured on that day yet; the prediction for that run is committed: on the Zen 1 host the file in the page cache and the bench's copy of it do not fit in memory together, so the run reads the day into the bench's own memory and checks every page of that copy before and after each timed pass ([results/predictions.md](results/predictions.md)).

No parse rate was timed on the Mac: the three bars are set on x86 Linux, which gives a macOS figure no verdict.

## Beyond the book

### The SPSC ring

Against open-source C++ queues on the Zen 1 host this ring's median round trip ranks sixth, ahead of only boost and a mutex: atomic_queue 133.5 ns, moodycamel 169.3, rigtorp 198.5, folly 220.9, xenium_vyukov 223.4, project 229.3 (this ring), boost 264.5 and mutex 781.3; so T-SPSC-1, this ring against three published ones, is missed ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)).

T-SPSC-2, what each technique is worth, is missed by far: the cached copy of the other side's index is worth 1.20× beside the mask and 0.89× beside a modulo, and the mask 1.60× beside the cached index, against bars of 3.90× and 4.20× set under WSL2 on an i7-9700 ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).

Queue latencies start at the push, so a wait behind a slow push is in no figure. No latency from the due time was measured: it needs the due time carried in each item, a change to the benchmark's measured code, which was not made before the runs; that measurement is open (BENCHMARKS.md, "What the paced and burst latencies measure").

With the parser on one core and the book on another, the median pipelined time over single-threaded time is 0.954 against a bar of at most 1.31, so T-SPSC-3 is met; but the single-threaded runs varied with a CV of 6.25%, so the saving is within their spread, and in the first repetition the parser's pushes found the ring full 22,248,425,075 times, the book's pops empty 843,507 times ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

### UDP receive: sockets vs io_uring

A paced sender replays the day as MoldUDP64, Nasdaq's UDP framing, over loopback on the Zen 1 host. S4, spinning on `recvmmsg` (many datagrams a call), carried the most with no loss at 1,400 bytes a datagram, 2,830,848 msg/s, just above blocking sockets and most modes of io_uring, Linux's shared-ring interface. T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak rate, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

### The backtester and the market maker

The backtester queues simulated orders among the real ones by order reference, with latency, Nasdaq's fees and partial fills. T-BT-1, the whole day with a quoting strategy, is met on the Zen 1 host at 4.68 M engine events/s vs a bar of 2.00 M; its median replay rate is 4,570,542 messages/s ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).

T-BT-3, four symbols with 100,000 simulated orders in real queues, is missed on the Zen 1 host at 3,509,307 messages/s; runs of the same build differed, and none read counters ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)). On the Mac, the kind of machine its bar was published on, T-BT-3 is met.

The market maker, the Avellaneda-Stoikov quoting model, splits its profit and loss into spread capture, adverse selection and inventory, summing exactly to the ledger's. Trading AMD's stock (the symbol, not the chip maker) on 12302019, its total was -25.96 dollars with the backtester's latency model and +1.23 with every latency set to zero: accounting, not an edge ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

## Correctness

### The reference book

A deliberately simple reference book replays each session beside the fast book, reading the wire at its own offsets. After every message the oracle compares what that message could change and checks every store's slot; it hashes the whole book every million messages, and a proof in [docs/NOTES.md](docs/NOTES.md) shows the rest cannot diverge.

Over all ten sessions there were 0 divergences in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

### The book is sometimes crossed, and that is the feed

T-INV-2, a book that stays uncrossed, fails strictly: on 12302019 there were 18,958 crossed observations, none in normal trading outside a reopening window. An open-source ITCH book, carteret, publishes 8,580 crossed book messages for this day. Its unit is a book message after which the book is crossed, not an observation after every message, and it counts one crossed replace twice; counted once per message, its unit gives 8,579 ([results/oracle_12302019.md](results/oracle_12302019.md)).

I chose a scoped rule as a separate check, T-INV-3: in normal trading a book may be crossed only by a cross left from a halt that has not yet cleared. T-INV-3 passes, and T-INV-2 strict stays beside it.

### The trade prints

Each print check is exact. T-TRADE-5, every execution in normal trading at the best level, held for all 5,683,178 executions on 12302019 ([results/trades_12302019.md](results/trades_12302019.md)). T-TRADE-5 fails on two sessions, each just after a resumption with that book crossed.

T-TRADE-3, each symbol's volume against the SEC's MIDAS file, fails on four of the ten sessions: on one MIDAS lists a symbol twice, and on the rest a few symbols' feed volume is above MIDAS, unexplained ([RESULTS.md](RESULTS.md)).

### The fault matrix and fuzzing

The fault matrix plants a bug in a scratch copy and requires the named check to catch it. Before planting anything it runs each check once on the clean tree, and those 316 control commands succeeded; the last full run on the Mac then detected 1608 of 1608 faults, and the 14 faults for Linux only were all detected on the Zen 1 host (docs/NOTES.md) ([results/fault_matrix.md](results/fault_matrix.md)).

Each of the nine fuzz targets ran for 1,800 s on the Mac under ASan and UBSan, the compiler's memory and undefined-behaviour checks, and every one was clean: 0 crashes and no input saved ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).

### The final review

Every finding of the final review, with its outcome and the commit, test or note that shows it, is in [results/final_review.md](results/final_review.md).

## What a reviewer would ask

- **Is the gap to T-BOOK-1, book throughput, the clock?** Partly. A message's mean service time here is 194.35 ns, on a core at 3,593,024 kHz, and T-BOOK-1's bar is at least 16.4 M msg/s, set on a Ryzen 7 9700X ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
  As an estimate from those inputs and AMD's rated boost of 5.5 GHz for that part, which is not a measured clock of the bar's run: about 698 cycles per message here against about 335 for the bar, so the clock would explain about 1.5× of the 3.2× gap and the rest is per cycle; the bar's host published no cycle count. <!-- figures: not measured -->
- **Did you run itch-book on this host?** No. As BENCHMARKS.md reads itch-book's README, the same code and file ran 3.6× slower on a 2.0 GHz EPYC VM than on the Ryzen 9700X its bar comes from, a division of the README's two rates ([BENCHMARKS.md](BENCHMARKS.md)). <!-- figures: not measured -->
- **What is the latency tail?** The slowest message of each repetition took about 4 ms (a median max of 4014071.4 ns, against a p99.99 of 3014.6 ns); what it waits on was not traced ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **Latency from the wire to the book?** Not measured: no run follows a message from its datagram into the book.
- **What does the book give a consumer?** Queries, not a feed: a side's best price, shares and order count, the level at a price, a walk of a side from the best, an order by reference, and a hook told of every level written ([include/itch/book.hpp](include/itch/book.hpp)).
- **Why is the ring slower than rigtorp's?** Not measured; here a side's index shares a block with its copy of the other's, and a line for each was not timed ([include/itch/spsc.hpp](include/itch/spsc.hpp)).
- **What happens at capacity?** `apply()` refuses the message as `capacity_exhausted`, and the book is wrong from there on; the busiest session peaked at 1,964,977 live orders and 805,782 live levels, against `1 << 22` and `1 << 21` sized ([results/oracle_07302019.md](results/oracle_07302019.md)).
- **How often does the paged index fall back to its hash?** Not measured; the index counts it, but no file gives the count.
- **How is a level found on a delete, and what does a deep insert cost?** Through the order record; a delete that empties the level searches the side again in `close()`, and an add at a new price shifts every better entry up one, the whole side at worst ([include/itch/book_levels.hpp](include/itch/book_levels.hpp)); neither cost is measured.
- **Is the framing walk one serial chain of loads?** Yes, each length gives the next offset; its cycles per frame are not measured.
- **Why no `-march=native`?** Every timing is Clang 23.1.4 at -O3 with no -march flag, and the vector kernels name their instruction sets per function; a `-march=native` build of the whole book was not timed ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

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
