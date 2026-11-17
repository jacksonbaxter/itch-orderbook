# What a full-depth order book costs on a small x86 core, measured

On a small x86 core this order book is bound by memory latency: on an embedded Zen 1 the fast designs run about the same instructions per message, and from the fastest design to the slowest, speed falls as DRAM fills (cache lines from DRAM) rise, except between the two linked-level designs. The default design, flat price levels with an order index looked up by each order's reference number, rebuilds every symbol's book at 5,145,430 messages/s with 1.972 DRAM fills per message, 2.54× a `std::map` book on the same core ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)). Most fills left come from messages naming orders a second old or more, whose lines the small shared L3 cache no longer holds; a software look-ahead that hides some is faster there and slower on an Apple M4 Max, so it is off by default.

Most speed targets in [BENCHMARKS.md](BENCHMARKS.md) are the best figures other open-source projects have published, mostly on x86 desktops and laptops and two on Macs, so on this host most fall short; each names its source, or why it has none.

## What was built, and on what

The project parses Nasdaq TotalView-ITCH 5.0 and rebuilds the full-depth order book of every symbol, with a reference book that checks it, a backtester, a market maker, a single-producer single-consumer (SPSC) ring, a UDP replay and a browser demo. The data is Nasdaq's free sample files, named by day, MMDDYYYY. Ten full sessions carry the correctness checks, all from Nasdaq's main market except a day from its BX exchange; one day, 12302019, carries all performance work but one parse run.

Every headline timing is from an AMD Ryzen Embedded V1605B, an embedded Zen 1 part with 4 cores, 8 threads and a 4 MB L3 shared by all, its timed thread pinned, with turbo and SMT on and no core isolated ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). An Apple M4 Max runs the correctness checks; macOS cannot pin a thread, so a Mac timing is context, with no verdict unless the target's bar was itself published on a Mac.

The Zen 1 host is shared with other projects, and I chose to change none of its root-only kernel settings but the CPU governor: no huge pages are reserved, memory compaction runs as configured, and profiles are user space only (`perf_event_paranoid` 2). No TLB counter was read; the book's arrays sat on transparent huge pages.

## How a number is published

Each timing's prediction is committed first ([results/predictions.md](results/predictions.md)), and every driver's run, refused and withdrawn ones included, has a row in the run log ([results/run_log.md](results/run_log.md)). A run is refused unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is in memory (the file in the page cache, or, for the parse rate on 10302019, every page of the bench's copy around each timed pass). A sampler that withdraws a timed process if other work ran beside it came later: on the Zen 1 host only the parse rates and the T-BT-3 backtest were timed under it, and every other timing there rests on these refusals alone.

Every figure here links to the results file it comes from, and a script checks that each is in that file beside a word its sentence shares and that each verdict agrees with its target's result. It cannot catch a figure copied from elsewhere in its file, a count in words on a line that cites no file, or a derived claim such as a rank. What this page leaves out is in [docs/DETAILS.md](docs/DETAILS.md).

## Data layout

`FastBook` swaps two parts: the order index, `hash` (open addressing) or `paged` (slots indexed by the order reference itself, which Nasdaq hands out in rising order, with a counted fallback hash for a reference it cannot page), and each side's levels, `linked` (a list) or `flat` (one sorted array, best at the end). Records live in arrays sized up front, linked by 32-bit indices, and a symbol's side is found directly by its stock locate code, one header per locate and side.

The records, with sizes from the headers' `static_assert`s ([include/itch/](include/itch/)):

| What | Bytes | Fields |
|---|---|---|
| `BookOrder` | `24` | reference; shares, level, older and newer order in the FIFO |
| `FlatLevel` | `32` | total shares; price, order count, FIFO front and back; locate, side |
| `LinkedLevel` | `40` | as `FlatLevel`, plus the better and worse level |
| flat side's entry | `4 + 4` | parallel arrays of keys and level slots, so a search reads keys only, `16` to a `64`-byte line; an ask's key is `~price`, so the best is the largest key on both sides |
| flat side's header | `12` | start, count and capacity of its block, a power of two from one arena |
| paged-index page | `256 × 4` | an order slot for each of `256` consecutive references, found through a `4`-byte directory entry |

A message that names an order makes a chain of dependent loads (directory entry, page slot, order record, level; for a new order the side's header and the end of its keys), so their misses cannot overlap within a message. What was counted is DRAM fills, demand fills into the L1 cache from local DRAM; it is a floor, since lines the hardware prefetcher fetched are not counted. Per message, a delete makes 2.5544, an execute 2.2520, a replace 3.1322 and an add 1.3051 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

The four designs and the usual baseline (`std::map` levels and an `unordered_map` index, on a fresh or an aged heap), on the Zen 1 host, the day in memory. Both counter columns include reading the counters, which an empty span in a separate pass measures at 308.0001 instructions and 0.0000 DRAM fills per message, more than half of each fast design's instruction figure ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)):

| Arm | Messages/s | Book memory, MiB | Instructions per message | DRAM fills per message | File |
|---|---|---|---|---|---|
| `paged-flat` | 5,145,430 | 662.8 | 581.789 | 1.972 | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| `hash-flat` | 4,635,731 | 225.5 | 566.446 | 2.808 | [results/bench_book_hash-flat_ram_debian_12302019.md](results/bench_book_hash-flat_ram_debian_12302019.md) |
| `hash-linked` | 2,560,055 | 211.0 | 553.422 | 4.647 | [results/bench_book_hash-linked_ram_debian_12302019.md](results/bench_book_hash-linked_ram_debian_12302019.md) |
| `paged-linked` | 2,542,188 | 648.3 | 570.100 | 3.751 | [results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md) |
| `map-fresh` | 2,028,642 | 294.0 | 695.420 | 5.287 | [results/bench_book_map-fresh_ram_debian_12302019.md](results/bench_book_map-fresh_ram_debian_12302019.md) |
| `map-aged` | 1,832,255 | 393.0 | 683.516 | 5.711 | [results/bench_book_map-aged_ram_debian_12302019.md](results/bench_book_map-aged_ram_debian_12302019.md) |

The fast designs differ in how often they wait on DRAM, not in how much they do. Every row includes the same counter reads, so taking them off would keep each difference between rows and make each ratio larger. T-DESIGN-1 asks for at least 4.0× the map book's throughput, a ratio derived from the rates itch-book, an open-source C++ ITCH book, published for its fastest book and a naive one on one machine; here T-DESIGN-1 is missed ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

Speed falls as fills rise in every row but the linked pair: `paged-linked` makes 3.751 fills per message, fewer than `hash-linked`, yet is no faster, at 2,542,188 messages/s; why was not measured ([results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md)).

Flat levels beat linked ones because a flat side is one array and most adds land near the best: 57.27% of add searches pass one better level or none ([results/window_12302019.md](results/window_12302019.md)).

The paged index beats the hash and pays in memory; in the layout run an identity hash, at 5,077,675 messages/s, and chaining, at 4,946,760, both stayed below the paged index's 5,468,695 ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)). This run and the prefetch run below map the day, unlike the table's, and each gives `paged-flat` its own rate, so compare within one run; why the rates differ was not measured.

The age of the order a message names decides its cost: a message naming an order under 1 ms old makes 0.0490 DRAM fills; a message naming an order 1 s old or more makes 4.3729; 54.5% of the messages that name an order name one 1 s old or more, and they take 90.66% of those messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

## Memory

The fast book made 0 allocations after warm-up in 268,744,780 messages on all four designs, counted at every `operator new` ([results/oracle_12302019.md](results/oracle_12302019.md)).

T-ARENA-1 asks what the allocator alone is worth, on a linked-level book of nodes: the index pool, which names nodes by index and reuses the last one freed, ran at 1.238 times the throughput of glibc malloc on a fresh heap, and mimalloc, an open-source allocator, at 1.128 times it. T-ARENA-1's one hard check fails: a pool that touched every node first must take no page fault in a timed pass, and the index pool took 136, the pointer pool 200 and the oldest-first pool 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).

Why the pools fault is not established. One diagnostic run, made while the host did other work and so not published, is in the working record ([docs/NOTES.md](docs/NOTES.md#t-arena-1s-timed-arms-on-debian-crossed-with-the-pools-pages-2026-11-11)); it points to the kernel's memory compaction moving pages under the running book. I chose not to turn compaction off on a shared host, so whether that removes the faults is open.

## Prefetching and SIMD

Of the changes tried on the default design, prefetching gained the most on the Zen 1 host: a look-ahead of D messages touches a later message's index slot, order record and level early, and D=16 gained 16.41%, 6,159,978 vs 5,291,518 messages/s; DRAM fills fell from 2.0139 to 0.8212 per message, over whole passes, not comparable with the table's. The counter counts only fills the core waited for, and a line the look-ahead fetched in time is not one, so the fall is in those waits, not in memory traffic, which was not counted, and the gain is far smaller than the fall; time spent waiting was not measured ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).

On the M4 Max the look-ahead lost at every distance, -18.01% at its best, D=4; the Mac read no miss counters, so why is not known ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

The rule, fixed before either run, was to make the look-ahead the default only if both hosts gained; so it is off, and a replay from a file on the Zen 1 host would turn it on at D=16.

SIMD wins a field decode and loses the whole parse, where scalar takes 35.099 cycles per message, SSSE3 46.282 and AVX2 48.105 on the Zen 1 host ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)); with the book behind the parser no kernel beats scalar, the default.

## The parse rate

Timed alone on the Zen 1 host over 12302019 in memory ([results/parse_rate_debian_12302019.md](results/parse_rate_debian_12302019.md)), the framing walk, T-PARSE-1, ran a median of 298,983,253 messages/s and is missed by 410.02 M msg/s; T-PARSE-2, itch-book's decode of its ten types, copied into this project's bench, ran a median of 140,969,135 messages/s and is missed by 58.03 M msg/s. Both bars are itch-book's, set on the Ryzen 7 9700X, a Zen 5. T-PARSE-3, the strict decode of every field, ran a median of 117,739,800 messages/s that day.

T-PARSE-3's bar, 93.9 M msg/s and 2.89 GB/s, is set on 10302019, and there T-PARSE-3 is met at a median of 115.76 M msg/s and 3.558 GB/s. Its passes varied more than on 12302019, a CV of 4.95%, and the slowest, at 100,002,653 messages/s, is still above the bar; the input checked was the bench's own copy of the day, since file and copy do not fit in memory together ([results/parse_rate_debian_10302019.md](results/parse_rate_debian_10302019.md)).

No parse rate was timed on the Mac: the three bars are set on x86 Linux, which gives a macOS figure no verdict.

## Beyond the book

### The SPSC ring

Against open-source C++ queues on the Zen 1 host this ring's median round trip ranks sixth of eight, its range overlapping the fourth's and fifth's, ahead of only boost and a mutex: this project's ring 229.3 ns vs atomic_queue 133.5 and rigtorp 198.5; so T-SPSC-1, this ring vs three published ones, is missed ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)).

T-SPSC-2, what each technique is worth in saturated throughput, is missed: the mask over a modulo gave 1.60× vs a bar of 4.20×, and caching the other side's index 1.20× with the mask and 0.89× with a modulo, vs 3.90×, bars from Frasch's CppCon talk, measured under WSL2 on an i7-9700. The cached ratios lie within the spread of the uncached runs, CVs of 34.72% with the mask and 17.90% with a modulo ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).

Queue latencies start at the push, not at the due time, when the replay's schedule says an item should be sent, so a wait behind a slow push is in no figure. No latency from the due time was measured: it needs the due time carried in each item, a change to the benchmark's measured code, which was not made before the runs (BENCHMARKS.md, "What the paced and burst latencies measure").

With the parser on one core and the book on another, the median pipelined time over single-threaded time is 0.954 vs a bar of at most 1.31, so T-SPSC-3 is met; but the single-threaded runs varied with a CV of 6.25%, so the saving is within their spread ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

### UDP receive: sockets vs io_uring

A paced sender replays the day as MoldUDP64, Nasdaq's UDP framing, over loopback on the Zen 1 host, so no network card is in the path. S4, spinning on `recvmmsg` (many datagrams per call), carried the most with no loss at 1,400 bytes per datagram, 2,830,848 msg/s, 1.035 times blocking `recvmmsg` (S3) and one bisection step above most other modes, socket and io_uring (Linux's shared-ring interface). T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak rate, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

### The backtester and the market maker

The backtester queues simulated orders among the real ones by order reference, with latency, Nasdaq's fees and partial fills. T-BT-1, the whole day with a quoting strategy, is met on the Zen 1 host at 4.68 M engine events/s vs a bar of 2.00 M, a small project's figure on a synthetic feed; its median replay rate is 4,570,542 messages/s ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).

T-BT-3, four symbols with 100,000 simulated orders in real queues, is missed on the Zen 1 host at 3,509,307 messages/s; runs at three commits, with the same flags and changes only around the timed code, differed, and none read counters ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)). Its bar was published on an Apple M1 Pro, so on the Mac T-BT-3 has a verdict, and is met. With the Zen 1 host's miss, RESULTS.md's summary gives T-BT-3 missed.

The market maker, the Avellaneda-Stoikov quoting model, splits its profit and loss into spread capture, adverse selection and inventory, summing exactly to the ledger's. Trading the stock with the ticker AMD on 12302019, which is unrelated to the host's CPU, its total was -25.96 dollars with the backtester's latency model and +1.23 with every latency set to zero: accounting, not an edge ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

## Correctness

### The reference book

A simple reference book replays each session beside the fast book, reading the wire at its own offsets. After every message the oracle compares what that message could change and checks each level or order slot the fast book wrote; it hashes the whole book every million messages, and a proof in the working record ([docs/NOTES.md](docs/NOTES.md#the-oracles-proof)) shows the rest cannot diverge.

Over all ten sessions there were 0 divergences in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

### The book is sometimes crossed, and that is the feed

T-INV-2, a book that stays uncrossed, fails strictly: on 12302019 there were 18,958 crossed observations, none in normal trading outside a reopening window. An open-source ITCH book, carteret, publishes 8,580 crossed book messages for this day, counting book messages after which the book is crossed, a crossed replace twice, rather than observations after every message. Counted so, this book also gives 8,580, and 8,579 counting each once ([results/oracle_12302019.md](results/oracle_12302019.md)).

I chose a scoped rule as a separate check, T-INV-3: in normal trading a book may be crossed only by a cross left from a halt that has not yet cleared. T-INV-3 passes, and T-INV-2 strict stays beside it.

### The trade prints

Each print check is exact. T-TRADE-5, every execution in normal trading at the best level, held for all 5,683,178 executions on 12302019 ([results/trades_12302019.md](results/trades_12302019.md)). T-TRADE-5 fails on two sessions, both other days, each just after a resumption with that book crossed.

### The fault matrix and fuzzing

The fault matrix plants a bug in a scratch copy and requires the named check to catch it. Before planting anything it runs each check once on the clean tree, and those 371 control commands succeeded; the last full run on the Mac then detected 1771 of 1771 faults ([results/fault_matrix.md](results/fault_matrix.md)).

The matrix page is that run, at da5650a; six faults added since were each run by id and detected. The 14 for Linux only were all detected on the Zen 1 host at an earlier commit, fbd32a8, as the working record shows ([docs/NOTES.md](docs/NOTES.md#the-rounds-full-checks-and-the-linux-only-faults-2026-11-17)).

Each of the nine fuzz targets ran for 1,800 s on the Mac under ASan and UBSan, the compiler's memory and undefined-behaviour checks, and every one was clean: 0 crashes and no input saved ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).

### The final review

Every finding of the final review, with its outcome and the commit, test or note that shows it, is in [results/final_review.md](results/final_review.md).

## What a reviewer would ask

- **Is the gap to T-BOOK-1, book throughput, the clock?** Not known. Its bar, at least 16.4 M msg/s, was set on a Ryzen 7 9700X whose clock was not published, and a faster clock does not shorten a wait on DRAM, so the clock explains at most the ratio of the bar's clock to this one's. A message takes 194.35 ns in the timing pass and 1141.088 cycles in the counter pass, which reads counters around each message; an empty span from a separate run takes 444.0739 cycles, and the file subtracts nothing for it. Of four readings of the core's clock, none during a timed pass, the lowest is 2,723,232 kHz, at the start, and the highest 3,593,024 kHz ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **Did you run itch-book on this host?** No; it is the run that would split the host's share of each gap from the design's. As BENCHMARKS.md reads itch-book's README, the same code and file ran 3.6× slower on a 2.0 GHz EPYC VM than on the Ryzen 7 9700X its bar comes from, a division of the README's two rates ([BENCHMARKS.md](BENCHMARKS.md)). <!-- figures: not measured -->
- **Would the look-ahead work on a live feed?** Not known: it reads D messages ahead in a file, so a live feed would hold D messages before applying the first; that was neither built nor measured ([include/itch/lookahead.hpp](include/itch/lookahead.hpp)).
- **What is the latency tail?** The slowest message of each repetition took about 4 ms, 4001771.1 to 4018171.8 ns with a CV of 0.21%, so it recurs. What it waits on was not traced. The median p99.99 is 3014.6 ns ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- **Latency from the wire to the book?** Not measured: no run follows a message from its datagram into the book.
- **How often does the paged index fall back to its hash?** Not measured; the index counts it, but no file gives the count.
- **More answers**, from what the book gives a consumer to `-march=native`, are in [docs/DETAILS.md](docs/DETAILS.md#what-a-reviewer-would-ask).

## Every target missed or not measured

T-SIMD-1 (SIMD vs scalar parsing), T-URING-1 (io_uring vs sockets) and T-ARENA-1's timings have no numeric bar. T-TRADE-4, volume against Cboe's figures, is held back and not run: Cboe's terms bar using its data to check other data without consent.

<!-- missed targets: begin, generated by scripts/results_table.py, do not edit -->
Each line is on the Zen 1 host unless it names the M4 Max, and gives the deciding row, or the worst: for latency, the largest gap in ns. Where a bar is another host's published figure, the host's share of the gap was not measured. In the book's runs, messages naming an order a second old or more make most DRAM fills.

- **T-BOOK-1, every symbol's book from a whole day, vs itch-book, an open-source book:** 5.15 M msg/s vs 16.4 M msg/s, 11.25 M msg/s short. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-BOOK-2, the same, tracking the best bid and offer, vs itch-book:** 5.09 M msg/s vs 12.5 M msg/s, 7.41 M msg/s short. [file](results/bench_book_paged-flat_mapped_top_debian_12302019.md)
- **T-LAT-1, each operation's latency, vs itch-book:** At p99.9 a replace takes 2,734.28 ns vs 945 ns, 1,789.28 ns over; eleven more in RESULTS.md. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-2, every message's latency, vs nasdaq-feed-handler, an open-source book, under WSL2:** At p99.9 a message takes 1,472.00 ns vs 874 ns, 598.00 ns over; two more in RESULTS.md. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-3, the budget Nasdaq's peak rate sets, on any host:** At p99.9 a message takes 1,472.00 ns vs 750 ns, 722.00 ns over. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-4, every message's latency, vs carteret, an open-source FIFO book, same day, timer cost subtracted:** At p99.9 a message takes 1,451.96 ns vs 1,080 ns, 371.96 ns over; three more in RESULTS.md. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-LAT-5, the tightest tail published, PitchFork's, an open-source book, nothing subtracted:** At p99.9 a message takes 1,472.00 ns vs 778 ns, 694.00 ns over; two more in RESULTS.md. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-DESIGN-1, speed over a `std::map` book in the same run:** Measured at 2.54× the speed of a `std::map` book on a fresh heap vs 4.0×: missed, 1.46 below it; two more in RESULTS.md; the map book's counter pass: 2272.754 cycles and 5.287 DRAM fills a message ([file](results/bench_book_map-fresh_ram_debian_12302019.md)); the fast book's: 1141.088 cycles and 1.972 fills ([file](results/bench_book_paged-flat_ram_debian_12302019.md)); the gap as DRAM waits is inferred. [file](results/bench_book_paged-flat_ram_debian_12302019.md)
- **T-PERF-1, last-level misses, a floor on DRAM fills, per book message (one that can change a book), vs carteret on a Zen 3:** 2.002 per book message vs at most 0.623, 1.379 over. [file](results/counters_by_type_paged-flat_debian_12302019.md)
- **T-PERF-2, branch misses per message, vs Market-Core, an open-source project:** 2.102 per message vs at most 2.04, 0.062 over. [file](results/counters_by_type_paged-flat_debian_12302019.md)
- **T-INV-2, the never-crossed rule:** On the M4 Max it fails strictly on nine of ten sessions; every cross is the feed's, outside normal trading or inside a reopening window. [file](results/oracle_12302019.md)
- **T-TRADE-3, each symbol's continuous volume vs SEC MIDAS:** On the M4 Max, 38 violations on 30 May 2019, none allowed; three more in RESULTS.md; the feed counts more shares than MIDAS. [file](results/trades_05302019.md)
- **T-TRADE-5, every continuous execution at the best price:** On the M4 Max, 2 violations on 27 March 2019, none allowed; one more in RESULTS.md; both just after a crossed book resumed. [file](results/trades_03272019.md)
- **T-PARSE-1, the parser's framing walk, vs itch-book:** 298.98 M msg/s vs 709 M msg/s, 410.02 M msg/s short. [file](results/parse_rate_debian_12302019.md)
- **T-PARSE-2, decoding the types that change the book, vs itch-book:** 140.97 M msg/s vs 199 M msg/s, 58.03 M msg/s short. [file](results/parse_rate_debian_12302019.md)
- **T-BT-3, the backtester's shadow-order replay, vs queuetruth, an open-source project, on a Mac:** 3.51 M messages/s vs 4.61 M messages/s, 1.10 M messages/s short; not the queue walk, a small share of the time. [file](results/backtest_shadow_debian_12302019.md)
- **T-MM-2, Avellaneda and Stoikov's own average spreads:** On the M4 Max the spread at γ = 1 is 3.0317, 3.03 at two decimals vs the paper's 3.02; the simulation's time grid moves it. [file](results/mm_as_tables.md)
- **T-SPSC-1, the ring's round trip vs boost, moodycamel and rigtorp:** This ring's round trip over moodycamel's is measured at 1.35× vs a bar of at most 1.00×; two more in RESULTS.md. [file](results/spsc_queues_debian_12302019.md)
- **T-SPSC-2, the known ring techniques' gains, vs Frasch's talk:** Caching the indices, with `%` wrapping, is measured at 0.89× vs a bar of at least 3.90×; three more in RESULTS.md. [file](results/spsc_ablation_debian_12302019.md)
- **T-ARENA-1, the pool allocator, with no published bar:** Its fault check allows no page fault in a timed pass; on base pages `pool-pointer` takes 200; five more in RESULTS.md. [file](results/alloc_arms_base_debian_12302019.md)
- **T-WASM-1, the browser replay vs native:** On the M4 Max, 1.19× native vs at most 1.55×, but in Google Chrome for Testing, not Google Chrome: no verdict. [file](results/wasm_replay_mac_12302019.md)
- **T-BOOK-4, the rebuild on Apple silicon on a busier day:** Not measured: its bar, 14.4 M msg/s on 30 January 2019, is an Apple M2 Pro's; every M4 Max run was dropped while other work ran. [file](results/bench_book_paged-flat_ram_mac_12302019.md) <!-- figures: not measured -->
- **T-TRADE-2, each symbol's cross volume vs Nasdaq's CrossStats:** Not measured on 30 January 2019, a BX day: CrossStats lists only Nasdaq's own crosses. [file](results/trades_01302019_BX.md) <!-- figures: not measured -->

Every row, with its why in full: [RESULTS.md](RESULTS.md#every-target-missed-or-not-measured).
<!-- missed targets: end -->
