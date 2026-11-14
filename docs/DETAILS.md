# Measurements behind the write-up

[WRITEUP.md](../WRITEUP.md) gives the findings; this page holds the measurements it leaves out, section by section under the same headings. Every figure links to the results file it comes from and is checked against that file by the same script as the write-up's. Where a reader needs a prediction's or a run's id to find its entry in [results/predictions.md](../results/predictions.md) or [results/run_log.md](../results/run_log.md), the id is given.

## What was built, and on what

The Linux host is an AMD Ryzen Embedded V1605B, a Zen 1 part for embedded boards with 4 cores and 8 threads; its results files are named `debian`. Timed threads are pinned, with turbo and SMT on and no core isolated. Its timer's quantum is 10.010 ns, and no percentile under its floor of 40.041 ns is published, though a mean over many messages can be lower ([results/bench_book_paged-flat_ram_debian_12302019.md](../results/bench_book_paged-flat_ram_debian_12302019.md)). The performance day, 12302019, is 268,744,780 messages ([results/oracle_12302019.md](../results/oracle_12302019.md)).

The Mac is an Apple M4 Max, and macOS cannot pin a thread: the harness's proof run, a stub book with nothing in it, varied with a CV of 67.35% there ([results/bench_replay_stub_mac_12302019.md](../results/bench_replay_stub_mac_12302019.md)).

On the Zen 1 host the `cache-misses` event counts L2 misses: a pointer chase at 1 MB, between L2 and L3, reads 0.7628 `cache-misses` a load and 0.0000 on the DRAM fill event `r0843:u` ([results/chase_debian.md](../results/chase_debian.md)). `r0843:u` stands in for last-level misses as a floor, since lines the prefetcher fetched are not counted. The part has no AVX-512, so the AVX-512 VBMI kernel is built there but has run on neither host: its test skips on the Zen 1 host, naming the missing features.

## How a number is published

- On the Zen 1 host the machine check refuses a run unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is resident in memory: the file in the page cache, or, for the parse rate on 10302019, the bench's own copy of the day, every page of it checked before and after each timed pass. On the Mac it enforces only the input's residency and the timer's calibration, and records the rest as things it cannot enforce.
- On the Mac a sampler reads every other process every 2 s, and a process during which another rose above 20% CPU is withdrawn ([results/layout_mac_12302019.md](../results/layout_mac_12302019.md)). That rule is why T-BOOK-4 and T-ARENA-1 have no result on the Mac. Mac runs made before it, the book designs' and the backtester's among them, had no sampler beside them, so nothing is known about other load during them.
- On the Zen 1 host the same kind of rule came later: a sampler reads /proc every 1 s and withdraws a process whose core, SMT sibling or other cores were busy. Every Zen 1 results file made since carries the rule's line; T-BT-3's rerun and the parse rate are the timings published under it ([results/backtest_shadow_debian_12302019.md](../results/backtest_shadow_debian_12302019.md)).
- No row of [results/run_log.md](../results/run_log.md) is removed or rewritten; the one change, on 2026-11-13, gave other programs by their kinds.
- Each driver now writes a record of its run beside the raw output: the commit and tree state, each binary's hash, the dataset's hash and an untouched copy of every tool output the file quotes. Files published before that have no such record, and docs/NOTES.md lists what each lacks.

## Data layout

On the Mac the designs rank as on the Zen 1 host except the two linked arms, which swap: `paged-linked` is ahead of `hash-linked` there in every repetition, while on the Zen 1 host their ranges overlap. The Mac read no cache counters, so why the hosts differ is not established; those Mac runs were made before the Mac's sampler rule ([results/bench_book_paged-flat_ram_mac_12302019.md](../results/bench_book_paged-flat_ram_mac_12302019.md) and the files beside it).

The profile shows where the waiting is. `FastBook::apply` is the widest frame at 42.14% self, and nothing outside the pass, no allocation, system call or timer frame, shows above 0.1% ([results/flamegraph_paged-flat_debian_12302019.md](../results/flamegraph_paged-flat_debian_12302019.md)). The flamegraph target in BENCHMARKS.md sets no bar, so it has no verdict: it asks for a user-space profile of each hot path, and the frame-pointer build those profiles need ran at 0.9755 of the normal build's throughput.

About four fifths of `apply`'s samples sit on three instructions, each just after a load that depends on the one before: the page index's slot, then the order record, then its level.

On 12302019, 10.75% of add searches pass 16 or more better levels, and 8.337% of adds land 256 or more whole-cent ticks behind the best, so a fixed window of ticks would often overflow ([results/window_12302019.md](../results/window_12302019.md)).

Capping the linear search over flat levels at 16 entries before a binary search made no difference, -0.18% with overlapping ranges. A pure binary search ran -4.09% and made more branch misses, 2.808 per message vs 2.027 ([results/layout_debian_12302019.md](../results/layout_debian_12302019.md)).

In the layout run the hash index's rate is 0.8381 of the paged one's, `hash-flat` against the default ([results/layout_debian_12302019.md](../results/layout_debian_12302019.md)).

The lookup alone does not explain that gap. Timed alone on the Zen 1 host, a lookup on `paged-flat` costs 30.71 ns and on `hash-flat` 32.50 ns, with overlapping ranges, though `paged-flat` makes 0.024 DRAM fills a lookup and `hash-flat` 0.227 ([results/lookup_debian_12302019.md](../results/lookup_debian_12302019.md)). On the Mac the hash lookup is faster, 5.03 ns vs 8.95 ns, yet `paged-flat` is still the faster book there ([results/lookup_mac_12302019.md](../results/lookup_mac_12302019.md)).

The hash was varied too. An identity hash keeps neighbouring references in neighbouring slots: it ran 10.79% faster than the mixing hash, with DRAM fills down from 2.879 to 2.443 per message and L2 misses up from 6.446 to 8.183. Chaining ran 7.93% faster than open addressing, the opposite of the prediction ([results/layout_debian_12302019.md](../results/layout_debian_12302019.md)). Deletion, where open addressing shifts entries back, is the suspect, but no counter was split by operation, so that is not established. Neither variant caught `paged-flat`.

The counts by the age of the order named are each the median of 2 processes of the 3 planned ([results/counters_by_age_paged-flat_debian_12302019.md](../results/counters_by_age_paged-flat_debian_12302019.md)).

Prefetching one structure at a time took 22.7% of the fills off the demand path for the level record, 17.8% for the order record and 13.6% for the index slot. The rule set before the run named a bottleneck only at 40% or more, so none is claimed; the misses are spread along the chain of loads ([results/structure_debian_12302019.md](../results/structure_debian_12302019.md)).

## Memory

The allocation counter behind the write-up's 0 counts every `operator new`, not `malloc`, and is checked against a control book that allocates ([results/oracle_12302019.md](../results/oracle_12302019.md)).

T-ARENA-1 asks what the allocator alone is worth, so a separate linked-level book, `NodeBook`, changes only its allocator. The arms are glibc malloc on a fresh and an aged heap, mimalloc, `std::pmr::unsynchronized_pool_resource`, and three pools of this project's: index-linked, pointer-linked, and one that reuses its oldest free node. Each pool touches every node before the timed pass.

With the pools on base pages, on the Zen 1 host:

- Each arm's median ratio to malloc-fresh's throughput: pool-index 1.238, pmr-pool 1.195, malloc-aged 0.901; mimalloc's median ratio is 1.128 ([results/alloc_arms_base_debian_12302019.md](../results/alloc_arms_base_debian_12302019.md)).
- Median throughput, messages/s: pool-index 2,513,655, malloc-fresh 2,032,184 ([results/alloc_arms_base_debian_12302019.md](../results/alloc_arms_base_debian_12302019.md)).
- Replace p99.9: pool-index 58587.2 ns, malloc-fresh 69894.8 ns ([results/alloc_arms_base_debian_12302019.md](../results/alloc_arms_base_debian_12302019.md)).
- `pool-index` holds 221.1 MiB of book vs `malloc-fresh`'s 264.9 MiB ([results/alloc_arms_base_debian_12302019.md](../results/alloc_arms_base_debian_12302019.md)).

On transparent huge pages, pool-index's median ratio to malloc-fresh is 1.390; pool-index's median throughput is 2,834,723 messages/s ([results/alloc_arms_transparent_debian_12302019.md](../results/alloc_arms_transparent_debian_12302019.md)). TLB reach is the obvious suspect, but no TLB counter was read, so the cause is not established.

The index pool also narrows each link, so its lead over the pointer pool is not the allocator's alone. The fastest node book is still slower than both flat-level arms of `FastBook`; the two books were timed by different harnesses at different commits, so no finer ranking, of an allocator against a level layout, is drawn from them.

On transparent huge pages too, T-ARENA-1's page-fault check fails: page faults in the timed passes of pool-index 4, pool-pointer 1 ([results/alloc_arms_transparent_debian_12302019.md](../results/alloc_arms_transparent_debian_12302019.md)).

A diagnostic run afterwards (P-0703) recorded each minor fault with its address and time. Its records point to bursts during the kernel's memory compaction, which migrates base pages under the running book, so that the next touch of a moved page faults, and to a few more from the kernel collapsing the index into huge pages. It ran while the host did other work, so it is not published: its counts are in docs/NOTES.md, not a results file, and none is quoted here.

## Prefetching and SIMD

### The counter targets and dispatch

T-PERF-1, DRAM fills per book message, is missed at 2.002 against a bar of 0.623. That bar was set on a Zen 3 with 32 MB of L3 to one core, against this host's 4 MB shared by four, and the source's own book gave 0.983 on its own host ([results/counters_by_type_paged-flat_debian_12302019.md](../results/counters_by_type_paged-flat_debian_12302019.md)). The messages that name an old order are the ones that miss.

T-PERF-2, the branch-miss rate, is missed by 0.062: `paged-flat` makes 2.102 branch misses per message against a bar of at most 2.04, one published day's total on an older Intel Core i3 divided by its messages, and adds, the most common message, take 50.07% of the misses; which branch inside an add misses was not traced ([results/counters_by_type_paged-flat_debian_12302019.md](../results/counters_by_type_paged-flat_debian_12302019.md)).

By type, a replace makes 4.1840 branch misses per message and an add 2.4141 ([results/counters_by_type_paged-flat_debian_12302019.md](../results/counters_by_type_paged-flat_debian_12302019.md)).

An empty counting span costs 308.0001 instructions and 444.0739 cycles but 0.0000 DRAM fills and 0.0001 branch misses, so fills and branch misses need no correction ([results/counters_by_type_paged-flat_debian_12302019.md](../results/counters_by_type_paged-flat_debian_12302019.md)).

Dispatch, `switch` vs a table vs threaded:

- Against the `switch`, a table of function pointers ran -0.91% and computed-goto threading +0.66% ([results/dispatch_debian_12302019.md](../results/dispatch_debian_12302019.md)).
- The indirect forms made more branch misses, not fewer: `switch` 2.0303 per message, `table` 2.1005, `threaded` 2.0905 ([results/dispatch_debian_12302019.md](../results/dispatch_debian_12302019.md)).
- A pass that only frames and classifies messages puts the floor at 7.77 ns per message, and the book forms stand 182.87, 184.62 and 181.61 ns above it ([results/dispatch_debian_12302019.md](../results/dispatch_debian_12302019.md)).

### Prefetching

The look-ahead's gain on the Zen 1 host is paid for with 697.1053 instructions per message vs 397.7893 without it. Those counts are not comparable with the write-up's table: this harness maps the day and counts each whole timed pass, frame walk and look-ahead included, with no counter reads per message, while the table counts each `apply()` with the reads around it and the frame walk outside ([results/prefetch_debian_12302019.md](../results/prefetch_debian_12302019.md)).

It is paid for with branch misses too, 4.7279 per message vs 2.0432 ([results/prefetch_debian_12302019.md](../results/prefetch_debian_12302019.md)). In that harness the rate without look-ahead varied with a CV of 10.83% over its repetitions.

On the M4 Max the look-ahead raises instructions per message from 384.4524 (D=0) to 640.4915 (D=4), which is why it is off rather than on for one target; a default per target and a cheaper look-ahead were not tried ([results/prefetch_mac_12302019.md](../results/prefetch_mac_12302019.md)).

### SIMD parsing

SIMD parsing, scalar vs vector. ITCH fields are big-endian at fixed offsets, so a scalar decode is loads and byte swaps:

- On the Zen 1 host, decoding an Add Order alone, SSSE3 wins: 0.80 cycles vs scalar's 9.94, with AVX2 at 1.78 ([results/simd_decode_debian_12302019.md](../results/simd_decode_debian_12302019.md)). Why AVX2 trails SSSE3 on the same shuffle plan is not established; Zen 1's split 256-bit operations, clock changes and line-split stores were not tested apart.
- Over the whole parse the speed-up over scalar in cycles is SSSE3 0.758 and AVX2 0.730 ([results/simd_parse_debian_12302019.md](../results/simd_parse_debian_12302019.md)). The diagnostic counter run that points to the type dispatch and the moves back to integer registers is recorded in docs/NOTES.md and not published.
- With the book behind it, SSSE3 and scalar tie on cycles, and scalar beats AVX2 with ranges that do not overlap: AVX2 takes 1.012 times scalar's cycles, 190.228 ns per message vs 187.966, and SSSE3 188.132 ns ([results/simd_book_debian_12302019.md](../results/simd_book_debian_12302019.md)).
- On the Mac, NEON decodes an Add Order in 0.514 times scalar's cycles ([results/simd_decode_mac_12302019.md](../results/simd_decode_mac_12302019.md)), and wins the whole parse by less, 17.574 cycles per message vs 18.790, under the 10% set beforehand as the bar for changing the default ([results/simd_parse_mac_12302019.md](../results/simd_parse_mac_12302019.md)).
- In WebAssembly SIMD does win. Core WebAssembly has no byte-reverse instruction, so a scalar decode is shifts and ORs, and SIMD128 decodes an Add Order in 1.113 ns vs 2.314 ns, on macOS in node's V8 ([results/simd_e11_mac_12302019.md](../results/simd_e11_mac_12302019.md)).
- Two experiments could not run. AVX-512 VBMI needs a CPU neither host has. AVX2's first-burst cost needs a Skylake-family Intel server, and there is none here.

## The parse rate

Each pass ran on one pinned thread over 12302019 held in memory, and each rate is the median of every timed pass. GB/s counts the whole file's bytes, length prefixes included, as both sources count them: the framing walk reads 9.180 GB a second, the decode 4.328 and the strict decode 3.615 ([results/parse_rate_debian_12302019.md](../results/parse_rate_debian_12302019.md)).

T-PARSE-1 is missed by 410.02 M msg/s and 12.62 GB/s, and T-PARSE-2 is missed by 58.03 M msg/s and 1.77 GB/s. Both bars are each the best of three passes; the best passes here of T-PARSE-1 and T-PARSE-2 are 305,215,684 and 141,829,341 messages/s, still far under ([results/parse_rate_debian_12302019.md](../results/parse_rate_debian_12302019.md)).

T-PARSE-3's bar of 93.9 M msg/s and 2.89 GB/s was set by Lunary, a vendor of an ITCH parser written in Rust, on a cloud VM (a Google Cloud c3-standard-8 with a Xeon Platinum 8481C), over the day memory-mapped, as a median of 10 runs; here the day is read into memory before the clock starts. The two days differ little in bytes per message, 30.732 on 10302019 and 30.704 on 12302019, the day measured, and the results file gives each type's share on both ([results/parse_rate_debian_12302019.md](../results/parse_rate_debian_12302019.md)).

## Beyond the book

### The SPSC ring

- The median round trips on the Zen 1 host: atomic_queue 133.5 ns, moodycamel 169.3, rigtorp 198.5, folly 220.9, xenium_vyukov 223.4, project 229.3 (this ring), boost 264.5 and mutex 781.3 ([results/spsc_queues_debian_12302019.md](../results/spsc_queues_debian_12302019.md)).
- T-SPSC-1 is missed on all three comparisons: boost over this ring 1.15× against a bar of 1.16×, and this ring 1.35× moodycamel's and 1.16× rigtorp's where the bar is to match them ([results/spsc_queues_debian_12302019.md](../results/spsc_queues_debian_12302019.md)).
- T-SPSC-2, one technique removed at a time: the cached index gave 0.89× and 1.20× against bars of 3.90×, and the mask 1.60× and 1.19× against 4.20× ([results/spsc_ablation_debian_12302019.md](../results/spsc_ablation_debian_12302019.md)).

No counters were read on the queue runs, so why the techniques are worth so much less here is not established.

### UDP receive

The nine modes are S1 (`recvfrom`), S2 (`recvmsg`), S3 (`recvmmsg`, blocking) and S4 (`recvmmsg`, spinning) on sockets, and U1 (single-shot receive), U2 (multishot receive into a ring of provided buffers), U3 (multishot `recvmsg`), U4 (U2 with cooperative task running) and U5 (U2 with a kernel polling thread) on io_uring. Each mode's largest lossless rate was bisected at 1,400 bytes a datagram ([results/udp_bisect_debian_12302019.md](../results/udp_bisect_debian_12302019.md)):

- S1, S3, U1, U2 and U3 all reached 2,735,896 msg/s, one bisection step under S4, so blocking sockets and io_uring are not told apart here ([results/udp_bisect_debian_12302019.md](../results/udp_bisect_debian_12302019.md)).
- U4 reached 1,945,047 msg/s and U5 1,912,149 msg/s ([results/udp_bisect_debian_12302019.md](../results/udp_bisect_debian_12302019.md)). Above that they lost no datagram, but their provided-buffer ring ran dry, which the rule fixed beforehand counts as a loss. A larger ring is untested.
- Each run's machine check covered core 4 with its sibling 5 only; not checked, so not known to have been idle: the receiver's core 6, and for U5 the SQ thread's core 2 ([results/udp_bisect_debian_12302019.md](../results/udp_bisect_debian_12302019.md)).

T-URING-1 has no bar: no published head-to-head of io_uring and `recvmmsg` for UDP was found. The sender alone reaches 9,863,736 msg/s, so the receivers set these limits ([results/udp_ceiling_debian_12302019.md](../results/udp_ceiling_debian_12302019.md)).

At a low rate the spinning modes answer far sooner, p50 for S4 6.06 µs vs S1 32.56 µs ([results/udp_latency_debian_12302019.md](../results/udp_latency_debian_12302019.md)): the cost of waking a blocked thread, which spinning pays for with a whole core.

At 72 bytes a datagram the sender was the limit for every mode but U4, which stopped at 220,621 msg/s when its ring ran dry, so no mode can be ranked there ([results/udp_bisect_72b_debian_12302019.md](../results/udp_bisect_72b_debian_12302019.md)).

### The backtester

The backtester queues simulated orders among the real ones by order reference, so only depletion ahead of an order advances it, with Nasdaq's fees as dated for the day. On the Zen 1 host T-BT-3 is missed by 1.10 M messages/s ([results/backtest_shadow_debian_12302019.md](../results/backtest_shadow_debian_12302019.md)).

T-BT-3's bar was published for an Apple M1 Pro, and the Mac meets it, in a run made before the Mac's sampler rule with no sampler beside it ([results/backtest_shadow_mac_12302019.md](../results/backtest_shadow_mac_12302019.md)).

The suspect was the walk over simulated levels and orders. A ladder of schedules timed it at 4.969 ns a step, and on T-BT-3's schedule the walk is 3.51% of a message's 323.88 ns ([results/walks_ladder_debian_12302019.md](../results/walks_ladder_debian_12302019.md)). So the walk is not the cause.

Three runs of the same process took a median of 298.14 ns per message (P-0104), 323.88 (P-0105) and 284.96 (P-0106). P-0105 is the ladder's, and P-0106 is the published run, the only one of the three under the Linux sampler rule; it is outside both bands P-0106 named, so that prediction did not hold, and why the runs differ is not established, since none read counters ([results/backtest_shadow_debian_12302019.md](../results/backtest_shadow_debian_12302019.md)).

### The market maker

It was tuned on 10302019 and traded two symbols on 12302019, on macOS, each its own symbol-day. Q-off swaps the exact queue position for a conservative or a proportional rule, and L-off sets every latency to zero:

- On AMD, the base run made +15.90 dollars of spread capture, -27.50 of adverse selection and -14.36 of inventory, a total of -25.96; that run, AS Base, made 11 fills ([results/mm_pnl_12302019.md](../results/mm_pnl_12302019.md)).
- AMD's other totals, in dollars: AS Q-off conservative -23.64, AS Q-off proportional -28.57, AS L-off +1.23 ([results/mm_pnl_12302019.md](../results/mm_pnl_12302019.md)).
- BYND's totals, in dollars: AS Base -414.51, AS Q-off conservative -412.51, AS Q-off proportional -409.46, AS L-off -327.53 ([results/mm_pnl_12302019.md](../results/mm_pnl_12302019.md)).

On AMD the latency model changes the sign of the total; on BYND it does not.

T-MM-2, reproducing the paper's average spreads, fails at γ = 1: 3.0317, 3.03 at two decimals vs the paper's 3.02. Other time grids move it to 3.0217 and 3.0117, and the file keeps the original grid as the measurement ([results/mm_as_tables.md](../results/mm_as_tables.md)).

### The browser replay

Compiled to WebAssembly, the same `FastBook` replays one symbol in the browser demo at 1.19× native's time; the bar names Google Chrome and the run used Chrome for Testing, so T-WASM-1 has no verdict. On the Mac the book ran 13,819,561 messages/s natively and 11,661,713 in the browser ([results/wasm_replay_mac_12302019.md](../results/wasm_replay_mac_12302019.md)).

## Correctness

### The oracle's checks

The reference book reads the wire a byte at a time at offsets of its own, so a misread offset cannot hit both books, and after every message the oracle compares each touched level in full, the best bid and offer and the named order's status.

The fast book stores into a level or an order slot only inside `write_level()` or its pools' hand-out and take-back, and reports each slot it goes through. The oracle checks, on every message:

- **Write set:** each level written for is in the touched set.
- **Slots:** each level and order slot a write or a pool goes through is live and holds the level or order it should, a slot handed out was free, one taken back was live, and every slot is inside its pool.
- **Index:** each order reference the message names is found by the fast index exactly when the reference book holds it, at a live slot with the same shares and level.
- **Arena**, on the flat arms: each side of the message's locate stays within its block, and a block it moves to is inside the arena and held by no other side.

The proof in docs/NOTES.md says what each check covers and what it leaves to the whole-book hash. The slot, index and arena checks were added late, after the earlier proof was found to take on trust that a write stores only into its own level's slots: a freed order's link stored into the wrong field left a live slot on the free list. Such a store is still seen only on the message that takes that slot again, not on the one that made it. The whole-book hash every 1,000,000 messages backs all of it up ([results/oracle_12302019.md](../results/oracle_12302019.md)).

The ten sessions were run again with those checks at the code that ships, under P-0504, which predicted that every verdict and count the published oracle and trade files print would come out the same; every count did (docs/NOTES.md, "Against P-0504").

- On 12302019 the slot checks went through 460,327,312 level slots and 649,396,127 order slots, with a count of slot violations of 0 ([results/oracle_12302019.md](../results/oracle_12302019.md)).
- The index check looked up 284,881,004 references named, each in both books: one for every reference the day's messages name ([results/oracle_12302019.md](../results/oracle_12302019.md)).
- On 12302019 it compared 268,744,780 messages, 240,658,248 touched levels and 372,604,158 choke-point writes; the count of write-set violations is 0, and of divergences 0 ([results/oracle_12302019.md](../results/oracle_12302019.md)).

Planted book bugs are caught, each a fault-matrix row. The oracle has one known limit: it walks each touched level's whole FIFO. On a much busier recent sample day it was stopped by hand after hours of CPU, nearly all in that walk; its row is in [results/run_log.md](../results/run_log.md).

### The crossed book

On 12302019 the strict check counted 18,958 crossed and 1,018 locked observations ([results/oracle_12302019.md](../results/oracle_12302019.md)). T-INV-2 fails on nine of the ten sessions. The BX day, 01302019_BX, has no crossed observation ([results/oracle_01302019_BX.md](../results/oracle_01302019_BX.md)).

Of the 18,958 crossed observations on 12302019, 18,304 came with the symbol not in normal trading. All 654 crossed observations in normal trading came inside a reopening window, within 50 ms of a resumption, and none in normal trading outside such a window. T-INV-3 passes with 0 violations; on 12302019, 22 of 33 resumptions found the book crossed or locked, and every one cleared ([results/oracle_12302019.md](../results/oracle_12302019.md)).

### The trade prints

T-TRADE-2 holds the cross prints against Nasdaq's CrossStats file, and T-TRADE-3 each symbol's continuous volume against SEC MIDAS. An `E` executes a resting order at its price, so T-TRADE-5 requires every `E` in normal trading to hit the best level.

T-TRADE-3, each symbol's volume against the SEC's MIDAS file, fails on four of the ten sessions: on one MIDAS lists a symbol twice, and on the rest a few symbols' feed volume is above MIDAS, unexplained ([RESULTS.md](../RESULTS.md)).

- On 01302020 MIDAS lists TAP twice, and the tool sums both rows ([results/trades_01302020.md](../results/trades_01302020.md)).
- On 03272019 two `E` on RBZ executed away from the best just after a resumption, with that book crossed; the oracle agrees with the reference book that day ([results/trades_03272019.md](../results/trades_03272019.md)).

### Fuzzing

The fuzz runs were made on the code of commit aef684f, 3 targets at once. The fuzz results file says what clean means, and why the fuzzers used the toolchain's symbolizer ([results/fuzz_30min_mac.md](../results/fuzz_30min_mac.md)).

Since aef684f, only comments have changed in the fuzz targets and the code they build from (`fuzz src include cmake CMakeLists.txt CMakePresets.json`). Built from clean in one folder at 51f568d and after the comment pass (1066e4f), every object file is byte for byte the same, and so are the nine fuzz binaries once linked with the macOS linker's file times zeroed (`ZERO_AR_DATE=1`); as built, they differ only in the object times the linker records in their debug maps.

Of the 67 C++ files whose comments changed, the object comparison covers the 56 that the Mac's release and fuzz builds compile. A comparison with comments stripped, the same number of lines and the same code on every line, covers all 67, among them the 11 x86-64, WebAssembly and Linux-only files the Mac does not compile; three of those lie under the paths above, `include/itch/kernels/arch/x86_64/kernels_built.hpp`, `include/itch/kernels/wasm.hpp` and `src/page_map_linux.cpp`, so for them only the stripped comparison holds.

- `fuzz_mold` ran 247,377,437 executions and `fuzz_midas` 212,887,115 ([results/fuzz_30min_mac.md](../results/fuzz_30min_mac.md)).
- `fuzz_book` ran 67,300 executions, since each of its inputs is a stream of order messages replayed through every arm of the fast book and through the reference book, both compared in full after every message ([results/fuzz_30min_mac.md](../results/fuzz_30min_mac.md)).

## What a reviewer would ask

- **What does the book give a consumer?** Queries, not a feed: a side's best price, shares and order count, the level at a price, a walk of a side from the best, an order by reference, and a hook told of every level written ([include/itch/book.hpp](../include/itch/book.hpp)).
- **How is a level found on a delete, and what does a deep insert cost?** Through the order record; a delete that empties the level searches the side again to remove it, and an add at a new price shifts every better entry up one, the whole side at worst ([include/itch/book_levels.hpp](../include/itch/book_levels.hpp)); neither cost is measured.
- **Why is the ring slower than rigtorp's?** Not measured; here a side's index shares a block with its copy of the other's, and a line for each was not timed ([include/itch/spsc.hpp](../include/itch/spsc.hpp)).
- **What happens at capacity?** `apply()` refuses the message as `capacity_exhausted`, and the book is wrong from there on; the busiest session peaked at 1,964,977 live orders and 805,782 live levels, with room for `1 << 22` and `1 << 21` ([results/oracle_07302019.md](../results/oracle_07302019.md)).
- **Is the framing walk one serial chain of loads?** Yes, each length gives the next offset; its cycles per frame are not measured.
- **Why no `-march=native`?** Every timing is Clang 23.1.4 at -O3 with no -march flag, and the vector kernels name their instruction sets per function; a `-march=native` build of the whole book was not timed ([results/bench_book_paged-flat_ram_debian_12302019.md](../results/bench_book_paged-flat_ram_debian_12302019.md)).

## Tried and not made the default

Each was measured, with its figures above or in the write-up: prefetching; SSSE3, AVX2 and NEON decoding; a function-pointer table and computed-goto threading; linked levels, a capped or binary search, an identity hash and chaining; pool allocators; io_uring's buffer-ring modes.

## Predictions that did not hold

Wrong predictions are kept beside their outcomes in [results/predictions.md](../results/predictions.md). Among them: chaining was predicted slower than open addressing, threading was predicted to make fewer branch misses, the pools were predicted to take no page fault, and the spinning io_uring modes were predicted near the top.

## Never done or never published

- The replace, delete and add costs on an older and a recent day's message mix (P-0801, P-0802): every Mac attempt was withdrawn under the load rule, stopped, or left too few quiet processes to publish.
- T-BOOK-4 and T-ARENA-1 on the Mac, for the same reason; [RESULTS.md](../RESULTS.md) counts their runs.

## What this does not show, in full

- **One host of each kind.** No x86 host with more cache and newer cores was available to split each gap into the host's share and the design's.
- **One venue, one timing day.** Other feeds and message mixes may rank the designs differently.
- **A noisy Mac.** The Mac cannot pin threads and shares its cores with other work, so its timings are context.
- **No reserved huge pages.** The pools' explicit huge-page arm never ran, since reserving pages needs root on a shared host.
- **No network.** The UDP replay runs over loopback: system calls and copies, with no kernel-bypass card, no NIC timestamps and no real packet loss.
- **No market impact.** The backtester's profit and loss shows the accounting on two symbol-days, not what a strategy would earn.
- **No kernel view.** Time in the kernel, the UDP receive path's included, is not shown.
- **One build.** Every timing is Clang 23.1.4 with -O3 and no -march flag; other compilers and flags were audited for the code they emit, not timed ([results/bench_book_paged-flat_ram_debian_12302019.md](../results/bench_book_paged-flat_ram_debian_12302019.md)).
