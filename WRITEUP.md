# What an L3 book costs on a small x86 core, measured

This is the long form of [README.md](README.md): which design choices were measured, what each cost, and which targets were missed and why. Every figure is quoted from a file in [results/](results/), and its line links that file; `scripts/gate_numbers.py` fails the check if a figure is not in the file its line cites. Where a cause was not measured, the page says "not established". [BENCHMARKS.md](BENCHMARKS.md) gives every target's source, and [docs/NOTES.md](docs/NOTES.md) the history.

## What was built, and on what

The project parses Nasdaq TotalView-ITCH and rebuilds the full-depth (L3) book of every symbol: each order, in time priority, at each price. Around it are an oracle, trade-print checks, a backtester with a market maker, an SPSC ring, a pool allocator, a UDP replay and a WebAssembly build for a browser demo.

The data is Nasdaq's free sample files: ten full sessions for correctness, nine from Nasdaq and one from BX, and one day for all performance work.

- The oracle compared the books after every message of all ten sessions, 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).
- The performance day, 12302019, is 268,744,780 messages ([results/oracle_12302019.md](results/oracle_12302019.md)).

There are two hosts, and their numbers are never mixed.

- `debian` is an AMD Ryzen Embedded V1605B, a Zen 1 part with 4 cores and 8 threads. Timed threads are pinned, with turbo and SMT on and no isolation. The timer's quantum is 10.010 ns, and no figure under its floor of 40.041 ns is published ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- The Mac is an Apple M4 Max, and macOS cannot pin a thread. The harness's proof run, a stub book with nothing in it, varied with a CV of 67.35% there ([results/bench_replay_stub_mac_12302019.md](results/bench_replay_stub_mac_12302019.md)).

So the Mac carries the correctness runs, which are counts, and `debian` every headline timing. A Mac timing is labelled macOS.

Zen 1 limits what `debian` can show:

- It has no AVX-512, so the AVX-512 VBMI kernel is built and tested but runs on no host here.
- Its `cache-misses` event counts L2 misses. A pointer chase at 1 MB, between L2 and L3, reads 0.7628 `cache-misses` a load and 0.0000 on the DRAM fill event `r0843:u` ([results/chase_debian.md](results/chase_debian.md)). `r0843:u` stands in for last-level misses, as a floor, since lines the prefetcher fetched are not counted.
- `perf_event_paranoid` is 2 there, so profiles are user space only.
- No huge pages are reserved; that is a root step outside the project. Transparent huge pages are on.
- Its L3 is 4 MB, shared by four cores ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

### How a number gets published

- A prediction is committed first, in [results/predictions.md](results/predictions.md), with what would refute it.
- `machine_check` refuses the run unless the pin reads back, the governor is `performance`, the SMT sibling is idle and the input is resident in memory.
- Other work beside a timed process withdraws it. On the Mac a sampler reads every other process every 2 s, and a process during which another passed 20% CPU is withdrawn ([results/layout_mac_12302019.md](results/layout_mac_12302019.md)). That rule is why two Mac targets have no result. Mac runs made before it, the book designs' and the backtester's among them, had no sampler beside them.
  On `debian` the same kind of rule started late in the project: a sampler reads /proc every 1 s and withdraws a process whose core, SMT sibling or other cores were busy; T-BT-3's rerun is the one published figure under it so far ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)).
- Each driver now writes a record of its run beside the raw output: the commit and tree state, each binary's hash, the dataset's hash and an untouched copy of every tool output the file quotes. Files published before that have no such record, and docs/NOTES.md lists what each lacks.
- Every run, failed and withdrawn ones included, gets a row in [results/run_log.md](results/run_log.md), which is never edited.
- The fault matrix plants bugs in a scratch copy and requires the named check to catch each. Its last full run caught 1063 of 1063, after 196 control commands passed on the clean tree ([results/fault_matrix.md](results/fault_matrix.md)).

## Data layout

`FastBook` has two parts that can each be swapped, and the four combinations were measured as arms.

- The order index maps an order reference to its record. `hash` is an open-addressed table. `paged` relies on Nasdaq handing out references in rising order: a directory of pages of slots, indexed by the reference itself.
- Each side's levels are `linked`, a list walked from the best price, or `flat`, one sorted array of live levels with the best at the end.

The baseline is the book most people would start with: `std::map` levels and an `unordered_map` index, on a fresh heap (`map-fresh`) or one aged as it runs (`map-aged`). With the day in memory on `debian`, each row from its own file:

| Arm | Messages/s | Book memory, MiB | Instructions a message | DRAM fills a message | File |
|---|---|---|---|---|---|
| `paged-flat` | 5,145,430 | 662.8 | 581.789 | 1.972 | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| `hash-flat` | 4,635,731 | 225.5 | 566.446 | 2.808 | [results/bench_book_hash-flat_ram_debian_12302019.md](results/bench_book_hash-flat_ram_debian_12302019.md) |
| `hash-linked` | 2,560,055 | 211.0 | 553.422 | 4.647 | [results/bench_book_hash-linked_ram_debian_12302019.md](results/bench_book_hash-linked_ram_debian_12302019.md) |
| `paged-linked` | 2,542,188 | 648.3 | 570.100 | 3.751 | [results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md) |
| `map-fresh` | 2,028,642 | 294.0 | 695.420 | 5.287 | [results/bench_book_map-fresh_ram_debian_12302019.md](results/bench_book_map-fresh_ram_debian_12302019.md) |
| `map-aged` | 1,832,255 | 393.0 | 683.516 | 5.711 | [results/bench_book_map-aged_ram_debian_12302019.md](results/bench_book_map-aged_ram_debian_12302019.md) |

The two counter columns are per-message means through `rdpmc`: each span runs from one read to the next, so it holds the counter reads, and no empty span is taken off ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
For scale, that file quotes the empty span measured on this host and design, one read of every event and nothing else, at instructions 308.0001, and at 0.0000 on the floor on DRAM demand fills, so the instruction column is not `apply()`'s own count ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).

This table is the main finding. The four fast arms run almost the same instructions a message, counter reads included, and their speed follows their DRAM fills: they differ in how long they wait, not in how much they do. On the Mac the six designs rank the same way ([results/bench_book_paged-flat_ram_mac_12302019.md](results/bench_book_paged-flat_ram_mac_12302019.md) and the files beside it), but the Mac read no cache counters, so why the hosts differ so much is not established. Those Mac runs were made before the sampler rule, and no sampler ran beside them, so nothing is known about other load during them.

The profile shows where the waiting is. `FastBook::apply` is the widest frame at 42.14% self, and about four fifths of its samples sit on three instructions, each just after a load that depends on the one before ([results/flamegraph_paged-flat_debian_12302019.md](results/flamegraph_paged-flat_debian_12302019.md)). Nothing outside the book, no allocation or system call, shows above 0.1% ([results/flamegraph_paged-flat_debian_12302019.md](results/flamegraph_paged-flat_debian_12302019.md)).

### Flat levels against linked

Flat levels beat linked ones on either index by a wide margin, with fewer DRAM fills (the table above). A linked side is walked node by node, and each node may be anywhere in memory. A flat side is one array; an insert moves the entries behind it, a short move because most adds land near the best:

- On 12302019, 57.27% of add searches pass one better level or none, and 10.75% pass 16 or more ([results/window_12302019.md](results/window_12302019.md)).
- 8.337% of adds land 256 or more whole-cent ticks behind the best, so a fixed window of ticks, as some books use, would often overflow ([results/window_12302019.md](results/window_12302019.md)).

Capping the linear search at 16 entries before a binary search made no difference, -0.18% with overlapping ranges. A pure binary search ran -4.09% and missed more branches, 2.808 a message against 2.027 ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).

### The paged index against the hash

The paged index is faster and pays in memory: in the layout run the hash index's rate is 0.8381 of the paged one's, `hash-flat` against the default ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)), with far less book memory (the table above).

The lookup alone does not explain the gap:

- Timed alone on `debian`, a paged lookup costs 30.71 ns and a hash lookup 32.50 ns, with overlapping ranges, though the paged one makes 0.024 DRAM fills against the hash's 0.227 ([results/lookup_debian_12302019.md](results/lookup_debian_12302019.md)).
- On the Mac the hash lookup is faster, 5.03 ns against 8.95 ns, yet `paged-flat` is still the faster book there ([results/lookup_mac_12302019.md](results/lookup_mac_12302019.md)).

The hash was varied too:

- An identity hash keeps neighbouring references in neighbouring slots. It ran 10.79% faster than the mixing hash, with DRAM fills down from 2.879 to 2.443 a message and L2 misses up from 6.446 to 8.183 ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- Chaining ran 7.93% faster than open addressing, the opposite of the prediction ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).

Deletion, where open addressing shifts entries back, is the suspect, but no counter was split by operation, so that is not established. Neither variant caught `paged-flat`.

### What the age of an order costs

Messages that name an order (executes, cancels, deletes, replaces) were counted by that order's age, each figure the median of 2 processes of the 3 planned ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)):

- A message naming an order under 1 ms old makes 0.0490 DRAM fills; a message naming an order 1 s old or more makes 4.3729 ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).
- 54.5% of those messages name an order 1 s old or more, and they take 90.66% of the named messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

So most of the book's DRAM traffic is the first touch of an order whose lines have left a small L3. A larger cache would keep more of them; how much of the gap to the bars that is, is not measured, since no larger-cache x86 host was available.

Prefetching one structure at a time took 22.7% of the fills off the demand path for the level record, 17.8% for the order record and 13.6% for the index slot. The rule set before the run named a bottleneck only at 40% or more, so none is claimed ([results/structure_debian_12302019.md](results/structure_debian_12302019.md)). The misses are spread along the chain of loads.

## Memory

### No allocation on the hot path

`FastBook` has no allocator: its arrays are sized up front, and its links are indices into them. A test counts every `operator new` after warm-up, all forms included, beside a control book that does allocate. Over the reference day the fast book made 0 allocations after warm-up in 268,744,780 messages, on all four arms ([results/oracle_12302019.md](results/oracle_12302019.md)). The counter sees `operator new`, not `malloc`.

### The allocator arms

T-ARENA-1 asks what the allocator alone is worth, so a separate linked-level book, `NodeBook`, changes only its allocator. The arms are glibc malloc on a fresh and an aged heap, mimalloc, `std::pmr::unsynchronized_pool_resource`, and three pools of this project's: index-linked, pointer-linked, and one that reuses its oldest free node. Each pool touches every node before the timed pass.

With the pools on base pages, on `debian`:

- Each arm's median ratio to malloc-fresh's throughput: pool-index 1.238, pmr-pool 1.195, malloc-aged 0.901; mimalloc's median ratio is 1.128 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- Median throughput, messages/s: pool-index 2,513,655, malloc-fresh 2,032,184 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- Replace p99.9: pool-index 58587.2 ns, malloc-fresh 69894.8 ns ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- `pool-index` holds 221.1 MiB of book against `malloc-fresh`'s 264.9 MiB ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).

On transparent huge pages, pool-index's median ratio to malloc-fresh is 1.390; pool-index's median throughput is 2,834,723 messages/s ([results/alloc_arms_transparent_debian_12302019.md](results/alloc_arms_transparent_debian_12302019.md)). TLB reach is the obvious suspect, but no TLB counter was read, so the cause is not established.

The index pool also narrows each link, so its lead over the pointer pool is not the allocator's alone. The fastest node book is still slower than both flat-level arms of `FastBook`: under a node design, arrays and indices matter more than any allocator.

### The page-fault check fails as published

T-ARENA-1 has no numeric bar, because no published book changes only its allocator. Its one hard check is that a pool arm, having touched every node first, takes no page fault in a timed pass. It fails in both files:

- On base pages: page faults in the timed passes of pool-index 136, pool-pointer 200, pool-fifo 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- On transparent huge pages: pool-index 4, pool-pointer 1 ([results/alloc_arms_transparent_debian_12302019.md](results/alloc_arms_transparent_debian_12302019.md)).

A diagnostic run afterwards (P-0703) recorded each minor fault with its address and time and found the cause. The faults came in bursts during the kernel's memory compaction, which migrates base pages under the running book, so the next touch of a moved page faults. A few more came from the kernel collapsing the index into huge pages. It ran on a host doing other work, so its counts are in docs/NOTES.md, not a results file, and none is quoted here. A fix needs root settings outside the project, so the check stays failing.

## Branches and the instruction stream

### Misses by message type

T-PERF-2's bar is 2.04 branch misses a message, a floor derived from published counts, and `paged-flat` makes 2.102, a miss by 0.062 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). A replace makes 4.1840 a message and an add 2.4141, and adds, the most common message, take 50.07% of all misses ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). Which branch inside an add misses was not traced, so the reason for the gap is not established.

The same run holds T-PERF-1, DRAM fills per book message, at 2.002 against a bar of 0.623 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). That bar was set on a Zen 3 with 32 MB of L3 to one core, against this host's 4 MB shared by four, and the source's own book gave 0.983 on its own host ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). A delete makes 2.5544 fills, an execute 2.2520 and a replace 3.1322, against 1.3051 for an add ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)): the messages that name an old order are the ones that miss.

An empty counting span costs 308.0001 instructions and 444.0739 cycles but 0.0000 DRAM fills and 0.0001 branch misses, so fills and branch misses need no correction ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

### Dispatch: switch, table, threaded

- Against the `switch`, a table of function pointers ran -0.91% and computed-goto threading +0.66% ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- The indirect forms missed more branches, not fewer: `switch` 2.0303 a message, `table` 2.1005, `threaded` 2.0905 ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- A pass that only frames and classifies messages puts the floor at 7.77 ns a message, and the book forms stand 182.87, 184.62 and 181.61 ns above it ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).

Dispatch is not where the time goes, and the `switch` stays.

### Prefetching

A look-ahead of D messages touches a later message's index slot, order record and level before they are needed.

- On `debian` it gained 16.41% at D=16, 6,159,978 against 5,291,518 messages/s ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).
- DRAM fills fell from 2.0139 to 0.8212 a message, paid for with 697.1053 instructions a message against 397.7893 and 4.7279 branch misses against 2.0432 ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).
- On the Mac it lost at every distance, -18.01% at its best, D=4 ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

Why the Mac loses is not established; it read no miss counters. The look-ahead is off by default, since it loses on one host, and it is the largest gain left for the Linux numbers.

### Scalar against SIMD parsing

ITCH fields are big-endian at fixed offsets, so a scalar parser is loads and byte swaps. A shuffle-based decode was tried against it; on both hosts the default stays scalar.

On `debian`, decoding an Add Order alone, SSSE3 wins: 0.80 cycles against scalar's 9.94, with AVX2 at 1.78 ([results/simd_decode_debian_12302019.md](results/simd_decode_debian_12302019.md)). Why AVX2 trails SSSE3 on the same shuffle plan is not established; Zen 1's split 256-bit operations, clock changes and line-split stores were not tested apart.

Over the whole parse the order reverses: scalar 35.099 cycles a message, SSSE3 46.282, AVX2 48.105, ; the speed-up over scalar in cycles is SSSE3 0.758 and AVX2 0.730 ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)). A diagnostic counter run, recorded in docs/NOTES.md and not quoted here, put the loss in the work around the decode: the type dispatch and moving vector results back to integer registers.

With the book behind it, the kernel does not matter: scalar 187.966 ns a message, SSSE3 188.132, AVX2 190.228 ([results/simd_book_debian_12302019.md](results/simd_book_debian_12302019.md)).

On the Mac, NEON decodes an Add Order in 0.514 times scalar's cycles ([results/simd_decode_mac_12302019.md](results/simd_decode_mac_12302019.md)).

It wins the whole parse by less, 17.574 cycles a message against 18.790, under the 10% set beforehand as the bar for changing the default ([results/simd_parse_mac_12302019.md](results/simd_parse_mac_12302019.md)).

In WebAssembly SIMD does win. Core WebAssembly has no byte-reverse instruction, so a scalar decode is shifts and ORs, and SIMD128 decodes an Add Order in 1.113 ns against 2.314 ns, on macOS in node's V8 ([results/simd_e11_mac_12302019.md](results/simd_e11_mac_12302019.md)).

Two experiments could not run. AVX-512 VBMI needs a CPU neither host has; its test skips on `debian`, naming the missing features. AVX2's first-burst cost needs a Skylake-family Intel server, and there is none here.

## Beyond the book

### The SPSC ring

The ring has free-running indices, a cached copy of the other side's index, a mask instead of a modulo, and batched publishing, with acquire and release ordering only. On `debian`, with each thread pinned:

- Median round trips, in the table below: atomic_queue is fastest and a mutex queue slowest, with this ring (`project`) between folly and boost.

| Queue | Median round trip, ns | File |
|---|---|---|
| atomic_queue | 133.5 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| moodycamel | 169.3 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| rigtorp | 198.5 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| folly | 220.9 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| project | 229.3 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| boost | 264.5 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |
| mutex | 781.3 | [results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md) |

- T-SPSC-1 is missed on all three comparisons: boost over this ring 1.15× against a bar of 1.16×, and this ring 1.35× moodycamel's and 1.16× rigtorp's where the bar is to match them ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)).
- T-SPSC-2, one technique removed at a time: the cached index gave 0.89× and 1.20× against bars of 3.90×, and the mask 1.60× and 1.19× against 4.20×; the bars were set on an i7-9700 under WSL2 ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).

No counters were read on the queue runs, so why this ring is slower here, and why the techniques are worth so much less, is not established.

The queue files also give one-way latencies of paced and burst sends, and this write-up quotes none of them. Each is measured from the push, not from the time the message was due, so a send held back behind a slow push has its wait in no latency figure, which favours a queue with slow pushes. No latency from the due time was measured: it needs the due time carried in each item, a change to the benchmark's measured code that no run has had (BENCHMARKS.md, "What the paced and burst latencies measure").

Inside the replay the ring does its job. With the parser on one core and the book on another, the day takes 0.954 of the single-threaded time against a bar of at most 1.31, so T-SPSC-3 is met; the single-threaded runs varied with a CV of 6.25%, so the saving is small beside the noise ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)).

### UDP receive: sockets against io_uring

A paced sender replays the day as MoldUDP64 over loopback on `debian`, which measures system calls and copies, not a network. Nine modes receive it: S1 to S4 on sockets, U1 to U5 on io_uring. Each mode's largest lossless rate was bisected at 1,400 bytes a datagram ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)):

- S4, `recvmmsg` spinning, won at 2,830,848 msg/s ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- S1, S3, U1, U2 and U3 all reached 2,735,896 msg/s, one bisection step, so blocking sockets and io_uring are not told apart here ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- U4 reached 1,945,047 msg/s and U5 1,912,149 msg/s ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)). Above that they lost no datagram, but their provided-buffer ring ran dry, which the rule fixed beforehand counts as a loss. A larger ring is untested.
- T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

Each run's machine check covered core 4 with its sibling 5 only; not checked, so not known to have been idle: the receiver's core 6, and for U5 the SQ thread's core 2 ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

T-URING-1 has no bar: no published head-to-head of io_uring and `recvmmsg` for UDP was found. The sender alone reaches 9,863,736 msg/s, so the receivers set these limits ([results/udp_ceiling_debian_12302019.md](results/udp_ceiling_debian_12302019.md)).

At a low rate the spinning modes answer far sooner, p50 for S4 6.06 µs against S1 32.56 µs ([results/udp_latency_debian_12302019.md](results/udp_latency_debian_12302019.md)): the cost of waking a blocked thread, which spinning pays for with a whole core.

At 72 bytes a datagram, the sender was the limit for every mode but U4, which stopped at 220,621 msg/s when its ring ran dry, so no mode can be ranked there ([results/udp_bisect_72b_debian_12302019.md](results/udp_bisect_72b_debian_12302019.md)).

### The backtester and the market maker

The backtester replays the real book and queues simulated orders among the real ones by order reference: only depletion ahead of an order advances it. It models latency on market data and order entry, fees from a dated Nasdaq schedule, and partial fills.

- T-BT-1 is met on `debian`: the whole day with a quoting strategy and its queue model at 4.68 M engine events/s against a bar of 2.00 M engine events/s; its median replay rate is 4,570,542 messages/s ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).
- T-BT-3 is missed on `debian`: four symbols with 100,000 shadow orders in real FIFO queues at 3,509,307 messages/s, 284.96 ns a message, 1.10 M under the 4.61 M bar ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)).
- That bar was set on a Mac, and the Mac meets it, in a run made before the sampler rule with no sampler beside it ([results/backtest_shadow_mac_12302019.md](results/backtest_shadow_mac_12302019.md)).

The suspect was the walk over simulated levels and orders. A ladder of schedules timed it at 4.969 ns a step, and on T-BT-3's schedule the walk is 3.51% of a message's 323.88 ns ([results/walks_ladder_debian_12302019.md](results/walks_ladder_debian_12302019.md)). So the walk is not the cause, and the rest of the gap is not established.
Three runs of the same process took a median of 298.14 ns a message (P-0104), 323.88 (P-0105) and 284.96 (P-0106); P-0105 is the ladder's, and P-0106 is the published run, the only one under the Linux sampler rule; it is outside both bands P-0106 named, so that prediction failed, and why the runs differ is not established, since none read counters ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)).

The market maker is Avellaneda-Stoikov, tuned on 10302019 and reported on 12302019, out of sample by day. Its PnL splits into spread capture, adverse selection and inventory, which sum exactly to the ledger's total, tested on integers ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

- On AMD, macOS, the base run made +15.90 dollars of spread capture, -27.50 of adverse selection and -14.36 of inventory, a total of -25.96, over 11 fills ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).
- With the latency model off, the same symbol-day totals +1.23 ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

One symbol-day shows the latency model can change the sign of the result, and nothing about an edge.

T-MM-2, reproducing the paper's average spreads, fails on one of three: 3.0317 at γ = 1, 3.03 at two decimals against the paper's 3.02. Other time grids move it to 3.0217 and 3.0117, and the file keeps the failing grid as the measurement ([results/mm_as_tables.md](results/mm_as_tables.md)).

### The browser replay

The same `FastBook`, compiled to WebAssembly, replays one symbol in the browser demo. On the Mac it ran 13,819,561 messages/s natively and 11,661,713 in the browser, 1.19× native ([results/wasm_replay_mac_12302019.md](results/wasm_replay_mac_12302019.md)). The bar names Google Chrome, which is not installed here; the run used Chrome for Testing, so there is no verdict.

## Correctness

### The oracle, and why it covers every message

A deliberately simple reference book replays the day beside the fast book. After every message the oracle compares the state that message could change: every level it touched, in full, the best bid and offer, and the order's status. The fast book stores into a level or an order slot only inside `write_level()` or its pools' hand-out and take-back, and reports each slot it goes through. The oracle checks, on every message:

- **Write set:** each level written for is in the touched set.
- **Slots:** each level and order slot a write or a pool goes through is live and holds the level or order it should, a slot handed out was free, one taken back was live, and every slot is inside its pool.
- **Index:** each order reference the message names is found by the fast index exactly when the reference book holds it, at a live slot with the same shares and level.
- **Arena**, on the flat arms: each side of the message's locate stays within its block, and a block it moves to is inside the arena and held by no other side.

The proof by induction that state outside the touched set cannot have diverged is in docs/NOTES.md, with what each check covers and what it leaves to the whole-book hash. The slot, index and arena checks were added in the final review, after a reviewer showed that the earlier proof took on trust that a write stores only into its own level's slots: a freed order's link stored into the wrong field left a live slot on the free list. Such a store is still seen only on the message that takes that slot again, not on the one that made it. The whole-book hash every 1,000,000 messages backs all of it up ([results/oracle_12302019.md](results/oracle_12302019.md)).

The counts below are from the ten sessions run again under P-0504 at the code that ships, with the slot, index and arena checks. P-0504 predicted, before the run, that every verdict and count the published oracle and trade files print would come out the same, on every arm, that each raw log would differ only by the new lines, and that the new checks would find nothing; it held on every part (docs/NOTES.md, "Against P-0504").

- On 12302019 the slot checks went through 460,327,312 level slots and 649,396,127 order slots, with a count of slot violations of 0 ([results/oracle_12302019.md](results/oracle_12302019.md)).
- The index check looked up 284,881,004 references named, each in both books: one for every reference the day's messages name ([results/oracle_12302019.md](results/oracle_12302019.md)).
- On 12302019 it compared 268,744,780 messages, 240,658,248 touched levels and 372,604,158 choke-point writes; the count of write-set violations is 0, and of divergences 0 ([results/oracle_12302019.md](results/oracle_12302019.md)).
- Over all ten sessions there were 0 divergences in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

Planted book bugs are caught, each a fault-matrix row. The oracle has one known limit: it walks each touched level's whole FIFO. On a much busier recent sample day it was stopped by hand after hours of CPU, nearly all in that walk; its row is in [results/run_log.md](results/run_log.md).

### The book is sometimes crossed, and that is the feed

T-INV-2 says the book is never crossed. Checked strictly after every message, it fails and is reported failing: on 12302019, 18,958 crossed and 1,018 locked observations ([results/oracle_12302019.md](results/oracle_12302019.md)).
It fails on nine of the ten sessions; the BX day passes ([results/oracle_01302019_BX.md](results/oracle_01302019_BX.md)).

Each violation was counted by symbol, time and trading state:

- On 12302019, 18,304 crossed observations came with the symbol not in normal trading, and the other 654 inside a reopening window, all within 50 ms of a resumption. None came in normal trading outside such a window ([results/oracle_12302019.md](results/oracle_12302019.md)).
- The counts match carteret's published figures for the day exactly: 8,580 crossed book messages against 8,580 ([results/oracle_12302019.md](results/oracle_12302019.md)).

The crosses are the feed's, in halts, auctions and quoting periods, not the book's. The user chose a scoped rule as a separate check, T-INV-3: in normal trading a book may be crossed only by a cross left from a halt that has not yet cleared. It passes with 0 violations; on 12302019, 22 of 33 resumptions found the book crossed or locked, and every one cleared ([results/oracle_12302019.md](results/oracle_12302019.md)). T-INV-2 strict stays failing beside it.

### The trade prints

Each check is exact, with no tolerance. T-TRADE-2 holds the cross prints against Nasdaq's CrossStats. T-TRADE-3 holds each symbol's continuous volume against SEC MIDAS. An `E` executes a resting order at its price, so T-TRADE-5 requires every `E` in normal trading to hit the best level.

- On 12302019, 5,683,178 of 5,683,178 such `E` messages did ([results/trades_12302019.md](results/trades_12302019.md)).

T-TRADE-3 fails on four days and T-TRADE-5 on two, each a row of the table below:

- On 01302020 MIDAS lists TAP twice, and the tool sums both rows ([results/trades_01302020.md](results/trades_01302020.md)).
- On 03272019 two `E` on RBZ executed away from the best just after a resumption, with that book crossed; the oracle agrees with the reference book that day ([results/trades_03272019.md](results/trades_03272019.md)).
- The other T-TRADE-3 differences, a few symbols where the feed is above MIDAS, are not explained, and each stays a failure.

### The fault matrix

Every check is tested like code: the fault matrix plants a bug and requires the named check to fail with the expected message, the number gate that holds this page included.

### Fuzzing

Each of the nine fuzz targets ran for 1,800 s on the Mac under ASan and UBSan, three at a time, from its committed seeds, on the code of commit aef684f, and every one was clean: exit 0, 0 crashes and no input saved ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)). The file says what clean means, and why the fuzzers used the toolchain's symbolizer.

- `fuzz_mold` ran 247,377,437 executions and `fuzz_midas` 212,887,115 ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).
- `fuzz_book` ran 67,300 executions, since each of its inputs is a stream of order messages replayed through every arm of the fast book and through the reference book, the two compared in full after every message ([results/fuzz_30min_mac.md](results/fuzz_30min_mac.md)).

### The final review

In the final review, reviewers who did not write the code went through it area by area. What they found was either fixed, with a test and a fault-matrix row that shows the fix holding, or is listed in docs/NOTES.md under "Found by the final review and left as it is", with the reason; the fixes are listed in the section above that one. The review of the documents themselves, this page included, comes after this pass.

## What was tried and did not work

Each was measured and lost or did not help.

- Prefetching gained 16.41% on `debian` ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).
- On the Mac it lost at every distance, -18.01% at its best, so it is off by default ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).
- A function-pointer table, -0.91%, and computed-goto threading, +0.66%, did not beat the `switch`, and both missed more branches ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- SSSE3 and AVX2 lost the whole parse on `debian`, at speed-ups of 0.758 and 0.730 over scalar in cycles, though SSSE3 wins the decode alone ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)).
- AVX2 lost to SSSE3 on the decode, 1.78 cycles against 0.80 ([results/simd_decode_debian_12302019.md](results/simd_decode_debian_12302019.md)).
- NEON won the Mac's parse, 17.574 cycles against 18.790, by less than the bar for changing the default ([results/simd_parse_mac_12302019.md](results/simd_parse_mac_12302019.md)).
- Over flat levels a capped linear search tied at -0.18%, and a binary search ran -4.09% ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- Linked levels lost to flat ones on either index: `paged-linked` ran 2,542,188 messages/s ([results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md)).
- No hash caught the paged index: the hash index's rate is 0.8381 of the paged one's, `hash-flat` against the default ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- The ring's cached index gave 1.20× against a bar of 3.90× ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).
- io_uring's buffer-ring modes came last at 1,400 bytes, U5 at 1,912,149 msg/s, held down by their rings running dry ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- The pools still took page faults in timed passes, 136 on `pool-index` ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).

Wrong predictions are kept beside their outcomes in [results/predictions.md](results/predictions.md). Among them: chaining was predicted slower than open addressing, threading was predicted to miss fewer branches, the pools were predicted to take no page fault, and the spinning io_uring modes were predicted near the top.

### Never done or never published

- The replace, delete and add costs on an older and a recent day's message mix (P-0801, P-0802): every Mac attempt was withdrawn under the load rule, stopped, or left too few quiet processes to publish.
- T-BOOK-4 and T-ARENA-1 on the Mac, for the same reason; the table below counts their runs.
- T-PARSE-1 to T-PARSE-3, the parse rate in messages and GB a second: the parse is timed in ns a message, and no rate in those units was produced.

## Every target missed or not measured

Generated by `scripts/results_table.py` from the results files; `scripts/check.sh` fails if it differs from a fresh run. The script refuses a row whose file no longer gives its verdict, and a BENCHMARKS.md target with no row that it does not list as met, held or without a bar. Only the "Why" words are written in the script, and they carry no figure. Bars are BENCHMARKS.md's, with the host each was set on. Book targets are the default arm's; the Book targets table in [RESULTS.md](RESULTS.md) has every arm's, each with its verdict. A "not measured" row has no measured figure, so the number gate skips it, and its run count is from [results/run_log.md](results/run_log.md).

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
| T-INV-2 | never crossed, checked strictly after every message | 12302019, strict: 18,958 crossed and 1,018 locked of 268,354,200 two-sided observations; 654 and 10 of 259,196,890 with the market open and the symbol trading; the same on 4 arms | fails strictly on 9 of 10 sessions | macOS | the feed's, not the book's: every cross is in a symbol not in normal trading or inside a reopening window, and T-INV-3, the rule the user chose, passes: [Correctness](#correctness) | [results/oracle_12302019.md](results/oracle_12302019.md) |
| T-TRADE-3, 01302020 | exact, with no tolerance | 6 violations | any violation fails | macOS | TAP alone: MIDAS lists it twice that day and the tool sums both rows, as docs/NOTES.md records: [Correctness](#correctness) | [results/trades_01302020.md](results/trades_01302020.md) |
| T-TRADE-3, 03272019 | exact, with no tolerance | 2 violations | any violation fails | macOS | PLAB alone, the feed above MIDAS; cause not established: [Correctness](#correctness) | [results/trades_03272019.md](results/trades_03272019.md) |
| T-TRADE-3, 05302019 | exact, with no tolerance | 38 violations | any violation fails | macOS | ten symbols, the feed above MIDAS; cause not established: [Correctness](#correctness) | [results/trades_05302019.md](results/trades_05302019.md) |
| T-TRADE-3, 08302019 | exact, with no tolerance | 6 violations | any violation fails | macOS | INTC alone, the feed above MIDAS in every column; cause not established: [Correctness](#correctness) | [results/trades_08302019.md](results/trades_08302019.md) |
| T-TRADE-5, 03272019 | exact, with no tolerance | 2 violations | any violation fails | macOS | two `E` on RBZ away from the best, just after a resumption with its book crossed; the oracle agrees with the reference book that day: [Correctness](#correctness) | [results/trades_03272019.md](results/trades_03272019.md) |
| T-TRADE-5, 05302019 | exact, with no tolerance | 1 violation | any violation fails | macOS | one `E` on ELTK, as with RBZ on the day above: [Correctness](#correctness) | [results/trades_05302019.md](results/trades_05302019.md) |
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
| T-PARSE-1 | ≥ 709 M msg/s, ~21.8 GB/s | not measured | not measured | none | no run was made against this bar: the E4 parse is timed in ns a message, never as msg/s or GB/s; no run logged | none | <!-- figures: not measured -->
| T-PARSE-2 | ≥ 199 M msg/s and ≥ ~6.1 GB/s | not measured | not measured | none | as T-PARSE-1; no run logged | none | <!-- figures: not measured -->
| T-PARSE-3 | ≥ 93.9 M msg/s and ≥ 2.89 GB/s on 10302019 | not measured | not measured | none | as T-PARSE-1, and its day, 10302019, was never timed; no run logged | none | <!-- figures: not measured -->
| T-BOOK-4 | ≥ 14.4 M msg/s on 01302019 (Apple M2 Pro, macOS, day 01302019) | not measured | not measured | macOS | macOS only; every try was withdrawn or stopped under the rule that withdraws a process while another is busy; 3 runs logged under P-0502, none published: 2 withdrawn, 1 stopped or refused | [results/bench_book_paged-flat_ram_mac_12302019.md](results/bench_book_paged-flat_ram_mac_12302019.md) | <!-- figures: not measured -->
| T-ARENA-1, macOS | none: UNCONFIRMED in BENCHMARKS.md | not measured | not measured | macOS | every try was withdrawn or stopped under the same rule; debian's arms are above; 27 runs logged under P-0701, none published: 22 withdrawn, 5 stopped or refused | none | <!-- figures: not measured -->
| T-TRADE-2, 01302019_BX | exact per symbol against CrossStats | not measured | not measured | macOS | CrossStats is Nasdaq's file of its own crosses, and this is a BX feed | [results/trades_01302019_BX.md](results/trades_01302019_BX.md) | <!-- figures: not measured -->
<!-- missed targets: end -->

T-SIMD-1, T-URING-1 and T-ARENA-1's timings have no numeric bar and are reported above. T-TRADE-4 is held: no Cboe data is used until the user decides.

## What this does not show

- **One host of each kind.** Most bars were set on desktop and server parts with more cache and newer cores, and no such host was available to split each gap into the host's share and the design's.
- **One venue.** Nine Nasdaq days and one BX day, and all timing on one day. Other feeds and other days' message mixes may rank the designs differently.
- **No network.** The UDP replay runs over loopback: system calls and copies, with no kernel-bypass card, no NIC timestamps and no real packet loss.
- **No market impact.** The backtester's feed cannot react to the strategy, so its PnL shows the accounting on one symbol-day, not what a strategy would earn.
- **No kernel view.** Profiles are user space only, so time in the kernel, the UDP receive path's included, is not shown.
- **A noisy Mac.** The Mac cannot pin threads and shares its cores with other work, so its timings are context.
- **No reserved huge pages.** The pools' explicit huge-page arm never ran, since reserving pages is a root step.
- **One build.** Timings are Clang 23.1.4 with -O3 and no -march flag ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)). Other compilers and flags were audited for the code they emit, not timed.
