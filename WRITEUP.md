# What an L3 book costs on a small x86 core, measured

This is the long form of [README.md](README.md): what was built, which design choices were measured, what each one cost, and which targets were missed and why. Every figure on this page is quoted from a file in [results/](results/), and the line that carries it links that file. `scripts/gate_numbers.py` fails the check if a figure is not in the file its line cites. Where a cause was not measured, the page says "not established" and names what was ruled out. [BENCHMARKS.md](BENCHMARKS.md) gives every target and its source, and [docs/NOTES.md](docs/NOTES.md) the history in order, including what did not help.

## What was built, and on what

The project parses Nasdaq TotalView-ITCH 5.0 and rebuilds the full-depth (L3) book of every symbol: each order, in time priority, at each price level. Around the book there is a reference book used as an oracle, trade-print checks against two outside sources, an event-driven backtester with a market maker on it, an SPSC ring, a pool allocator, a UDP replay with nine receive modes, and the book compiled to WebAssembly for a browser demo.

The data is Nasdaq's free sample files. Ten full sessions are checked for correctness, nine from Nasdaq and one from BX. All performance work uses one day, 12302019 ([results/oracle_12302019.md](results/oracle_12302019.md)).

- The oracle compared the books after every message of all ten sessions: 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).
- The performance day, 12302019, is 268,744,780 messages ([results/oracle_12302019.md](results/oracle_12302019.md)).

There are two hosts, and their numbers are never mixed.

- `debian` is an AMD Ryzen Embedded V1605B, a Zen 1 part with 4 cores and 8 threads. Each timed thread is pinned, here to core 4, with turbo and SMT on and no core isolation. Its timer's quantum is 10.010 ns, and the floor under which no figure is published is 40.041 ns ([results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md)).
- The Mac is an Apple M4 Max, and macOS offers no way to pin a thread. The harness's proof run, a stub book with nothing in it, varied with a CV of 67.35% across its repetitions there ([results/bench_replay_stub_mac_12302019.md](results/bench_replay_stub_mac_12302019.md)).

So the Mac carries the correctness runs, which are counts, and `debian` carries every headline timing. A Mac timing on this page is labelled macOS and is never set against a Linux bar.

Zen 1 limits what `debian` can show, and each limit shapes a section below:

- It has no AVX-512, so the AVX-512 VBMI kernel is built and tested but runs on no host here.
- Its `cache-misses` event counts L2 misses, not last-level misses. A pointer chase showed it: at 1 MB, between L2 and L3, `cache-misses` reads 0.7628 a load while the DRAM fill event `r0843:u` reads 0.0000 ([results/chase_debian.md](results/chase_debian.md)).
- So `r0843:u` stands in for last-level misses, as a floor: a line the prefetcher brought in is not counted.
- `perf_event_paranoid` is 2 there, and profiles are user space only. No kernel frame appears in any flamegraph.
- No huge pages are reserved (`vm.nr_hugepages` is 0), and reserving them is a root step outside the project. Transparent huge pages are on.
- Its L3 is 4 MB, shared by four cores ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)). Most bars were set on desktop parts with far more cache.

### How a number gets published

1. A prediction is committed before the run, in [results/predictions.md](results/predictions.md), with what would refute it.
2. The run is pinned, and `machine_check` refuses it unless the pin reads back, the governor is `performance`, the core's SMT sibling is idle and every page of the input is resident. A latency figure must not be a disk figure.
3. On `debian` a run that swapped is refused. On the Mac a sampler reads every other process every 2 s, and a timed process during which another passed 20% CPU is withdrawn ([results/layout_mac_12302019.md](results/layout_mac_12302019.md)).
4. Every run, failed and withdrawn ones included, gets a row in [results/run_log.md](results/run_log.md), which is never edited.
5. Each check is tested by the fault matrix, which plants a bug in a scratch copy of the tree and requires the named check to catch it. Its last full run caught 1063 of 1063 faults, after 196 control commands passed on the clean tree ([results/fault_matrix.md](results/fault_matrix.md)).

The sampler rule withdrew many Mac runs, and it is why two Mac targets have no result at all.

## Data layout

The fast book, `FastBook`, has two parts that can each be swapped, and the four combinations were measured as arms.

- The order index maps an order reference to its record. `hash` is an open-addressed table. `paged` uses the fact that Nasdaq hands out order references in rising order: it is a directory of pages, each a flat array of slots, indexed by the reference itself.
- The levels of each side are either `linked`, a list of level nodes walked from the best price, or `flat`, one sorted array of the side's live levels with the best at the end.

Beside them runs a baseline built the way most people would start: `std::map` for the levels and `unordered_map` for the index, on a fresh heap (`map-fresh`) or on a heap aged as it goes (`map-aged`).

With the day held in memory on `debian`, each row from its own file:

| Arm | Messages/s | Book memory, MiB | Instructions a message | DRAM fills a message | File |
|---|---|---|---|---|---|
| `paged-flat` | 5,145,430 | 662.8 | 581.789 | 1.972 | [results/bench_book_paged-flat_ram_debian_12302019.md](results/bench_book_paged-flat_ram_debian_12302019.md) |
| `hash-flat` | 4,635,731 | 225.5 | 566.446 | 2.808 | [results/bench_book_hash-flat_ram_debian_12302019.md](results/bench_book_hash-flat_ram_debian_12302019.md) |
| `hash-linked` | 2,560,055 | 211.0 | 553.422 | 4.647 | [results/bench_book_hash-linked_ram_debian_12302019.md](results/bench_book_hash-linked_ram_debian_12302019.md) |
| `paged-linked` | 2,542,188 | 648.3 | 570.100 | 3.751 | [results/bench_book_paged-linked_ram_debian_12302019.md](results/bench_book_paged-linked_ram_debian_12302019.md) |
| `map-fresh` | 2,028,642 | 294.0 | 695.420 | 5.287 | [results/bench_book_map-fresh_ram_debian_12302019.md](results/bench_book_map-fresh_ram_debian_12302019.md) |
| `map-aged` | 1,832,255 | 393.0 | 683.516 | 5.711 | [results/bench_book_map-aged_ram_debian_12302019.md](results/bench_book_map-aged_ram_debian_12302019.md) |

This table holds the main finding. The four fast arms run almost the same instructions a message, and their speed follows their DRAM fills. They differ in how long they wait, not in how much work they do. The map book does run more instructions, but its larger cost is its fills. Branch misses barely move between the fast arms. A separate counter run of the four fast arms, with the file mapped and the top of book tracked, gives the same order ([results/bench_book_paged-flat_mapped_top_counters_debian_12302019.md](results/bench_book_paged-flat_mapped_top_counters_debian_12302019.md) and the three files beside it).

On the Mac the six designs rank the same way ([results/bench_book_paged-flat_ram_mac_12302019.md](results/bench_book_paged-flat_ram_mac_12302019.md) and the files beside it). The Mac reads no cache counters in these runs, so why the gap between the hosts is so wide is not established.

The flamegraph shows where the waiting is. `FastBook::apply` is the widest frame at 42.14% self, and about four fifths of its samples sit on three instructions, each just after a load that depends on the one before: the page slot, then the order record, then its level ([results/flamegraph_paged-flat_debian_12302019.md](results/flamegraph_paged-flat_debian_12302019.md)). Nothing outside the book shows above 0.1%: no allocation, no system call ([results/flamegraph_paged-flat_debian_12302019.md](results/flamegraph_paged-flat_debian_12302019.md)).

### Flat levels against linked

Flat levels beat linked ones on either index by a wide margin, and they make fewer DRAM fills (the table above). A linked side is walked from the best price to find where an add goes, and each step is a node that may be anywhere in memory. A flat side is searched in one array. The cost is that an add or a delete inside the array moves the entries behind it. That move is short, because most adds land near the best:

- On 12302019, 57.10% of add searches pass one better level or none, and 10.79% pass 16 or more ([results/window_12302019.md](results/window_12302019.md)).
- 8.337% of adds land 256 or more whole-cent ticks behind the best on their side, so a fixed window of ticks, as some books use, would often overflow on this day ([results/window_12302019.md](results/window_12302019.md)).

The search was then varied. Capping the linear search at 16 entries before a binary search, `paged-flat-scan16`, made no difference: -0.18%, with the ranges over the repetitions overlapping. A pure binary search, `paged-flat-bisect`, lost 4.09% and missed more branches, 2.808 a message against 2.027 ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)). Starting from the best end is right, because that is where the data puts most adds.

### The paged index against the hash

The paged index is faster than the hash, and it pays in memory. `hash-flat` runs at 0.8381 of `paged-flat`'s rate in the layout run ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)), with far less book memory (the table above). The paged index pays for its reach up front: its pages are sized for the span of the day's order references, not for the live orders.

The lookup alone does not explain the gap:

- Timed alone on `debian`, a paged lookup costs 30.71 ns and a hash lookup 32.50 ns, and their ranges overlap. The counters do tell them apart: 0.024 DRAM fills a paged lookup against 0.227 a hash one. The lookups are 17.3% and 15.3% of the pass ([results/lookup_debian_12302019.md](results/lookup_debian_12302019.md)).
- On the Mac the hash lookup is the faster one, 5.03 ns against 8.95 ns, at 9.1% and 19.6% of the pass, yet `paged-flat` is still the faster book there ([results/lookup_mac_12302019.md](results/lookup_mac_12302019.md)).

So which index looks up faster is not the same question as which book is faster.

The hash itself was varied:

- An identity hash keeps neighbouring references in neighbouring slots, since references rise over the day. It ran 10.79% faster than the mixing hash, with DRAM fills down from 2.879 to 2.443 a message and L2 misses up from 6.446 to 8.183 ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- Chaining ran 7.93% faster than open addressing, the opposite of the prediction ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).

The counters point at deletion, where open addressing shifts entries back. No counter was split by operation, so that is consistent with the data and not proven. Neither variant caught `paged-flat`.

### What the age of an order costs

Messages that name an order (executes, cancels, deletes, replaces) were counted by the age of the order they name, on `debian`:

- A message naming an order under 1 ms old makes 0.0490 DRAM fills; one naming an order 1 s old or more makes 4.3729 ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).
- 54.5% of those messages name an order 1 s old or more, and they take 90.66% of the named messages' fills ([results/counters_by_age_paged-flat_debian_12302019.md](results/counters_by_age_paged-flat_debian_12302019.md)).

So most of the book's DRAM traffic is the first touch of an order that has sat long enough for its lines to leave a small L3. A larger cache would keep more of them. That is the host's share of the gap to the bars. How large a share was not measured: no larger-cache x86 host was available.

A look-ahead experiment then asked which structure the fills come from. Prefetching one structure at a time, the level record took 22.7% of the fills off the demand path, the order record 17.8% and the index slot 13.6%. The rule set before the run named a bottleneck only at 40% or more, so none is claimed ([results/structure_debian_12302019.md](results/structure_debian_12302019.md)). The misses are spread along the chain of loads, not held in one place.

## Memory

### No allocation on the hot path

`FastBook` has no allocator. Its arrays are sized up front, and its links are 32-bit indices, not pointers. A test counts every `operator new` after warm-up, the aligned, nothrow and array forms included, beside a control book that does allocate, which proves the counter works. Over the reference day the fast book made 0 allocations after warm-up in 268,744,780 messages, on all four arms ([results/oracle_12302019.md](results/oracle_12302019.md)). The counter sees `operator new` and not `malloc`, which docs/NOTES.md records as its limit.

### The allocator arms

T-ARENA-1 asks what the allocator alone is worth. So a separate book, `NodeBook`, was built in which only the allocator changes. It has linked levels and FIFOs of nodes, the shape where a node allocator matters. The arms are glibc's malloc on a fresh heap and on an aged one, mimalloc, `std::pmr::unsynchronized_pool_resource`, and three pools of this project's: one linked through indices, one through pointers, and one that hands back its oldest free node instead of its newest. Each pool maps and touches every node before the timed pass.

With the pools on base pages, on `debian`:

- `pool-index` ran at 1.238 times malloc-fresh's throughput, `pmr-pool` at 1.195, `mimalloc` at 1.128 and `malloc-aged` at 0.901 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- In messages a second, `pool-index` ran 2,513,655 and `malloc-fresh` 2,032,184 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- The pools hold the replace tail down: replace p99.9 is 58587.2 ns on `pool-index` against 69894.8 ns on `malloc-fresh` ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- `pool-index` holds 221.1 MiB of book memory against `malloc-fresh`'s 264.9 MiB, since its links are 32-bit indices ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).

With the pools on transparent huge pages, `pool-index` ran at 1.390 times malloc-fresh, 2,834,723 messages a second ([results/alloc_arms_transparent_debian_12302019.md](results/alloc_arms_transparent_debian_12302019.md)). The heap arms barely moved between the two runs. TLB reach is the obvious suspect, since the pools span far more memory than base pages can map at once, but no TLB counter was read, so the cause is not established.

The index pool changes the width of a link as well as the allocator, so its lead over the pointer pool is not the allocator's alone. And even the fastest node book is slower than both flat-level arms of `FastBook` in the table above. The arrays-and-indices design matters more than any allocator under a node design.

### The page-fault check fails as published

T-ARENA-1 has no numeric bar, because no published book changes only its allocator. Its one hard check is that a pool arm, which touched every node before the pass, takes no page fault in a timed pass. That check fails in both files, and the files say so:

- On base pages: page faults in the timed passes of pool-index 136, pool-pointer 200, pool-fifo 146 ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).
- On transparent huge pages: pool-index 4, pool-pointer 1 ([results/alloc_arms_transparent_debian_12302019.md](results/alloc_arms_transparent_debian_12302019.md)).

A diagnostic run afterwards (P-0703) recorded each minor fault with its address and time, and found the cause. The faults came in bursts during the kernel's memory compaction, which migrates base pages under the running book, so that the book's next touch of a moved page faults. A few more came from the kernel collapsing the index into huge pages. That run was a diagnostic on a host doing other work, so its counts are in docs/NOTES.md and not in a results file, and none is quoted here. Stopping it needs root settings outside the project, so the check stays failing.

## Branches and the instruction stream

### Branch misses by message type

T-PERF-2's bar is 2.04 branch misses a message, a floor taken from another book's published counts, and `paged-flat` makes 2.102, a miss by 0.062 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

The split by type says where they are. A replace makes 4.1840 a message and an add 2.4141, and adds, the most common message, take 50.07% of all misses ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). Which branch inside an add misses most was not traced, so the reason for the gap is not established.

The same run holds T-PERF-1, DRAM fills per book message, at 2.002 against a bar of 0.623 ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). That bar was set on a Zen 3 with 32 MB of L3 to one core, against this host's 4 MB shared by four, and the same file gives the source's own book 0.983 on its own host ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). By type, a delete makes 2.5544 fills, an execute 2.2520 and a replace 3.1322, against 1.3051 for an add ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)). The messages that name an old order are the ones that miss.

Counting has a cost, and it is shown, not hidden. One empty span costs 308.0001 instructions and 444.0739 cycles, but 0.0000 DRAM fills and 0.0001 branch misses, so the fill and branch figures need no correction ([results/counters_by_type_paged-flat_debian_12302019.md](results/counters_by_type_paged-flat_debian_12302019.md)).

### Dispatch: switch, table, threaded

Three ways to dispatch on the message type were timed on `debian`:

- Against the `switch`, a table of function pointers ran -0.91% and computed-goto threading +0.66% ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- The indirect forms missed more branches, not fewer: `switch` 2.0303 a message, `table` 2.1005, `threaded` 2.0905 ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- A pass that only frames and classifies each message, with no book, puts the floor at 7.77 ns a message, and the book forms stand 182.87, 184.62 and 181.61 ns a message above it ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).

The prediction was that threading would cut misses, and it was wrong. Dispatch is not where the time goes, and the `switch` stays.

### Prefetching

If the book waits on dependent loads, issuing them early should help. A look-ahead of D messages touches a later message's index slot, order record and level before they are needed.

- On `debian` it gained 16.41% at D=16, 6,159,978 against 5,291,518 messages/s ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).
- It cut DRAM fills from 2.0139 to 0.8212 a message, and paid with 697.1053 instructions a message against 397.7893, and 4.7279 branch misses against 2.0432 ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)).
- On the Mac it lost at every distance, -18.01% at its best, D=4 ([results/prefetch_mac_12302019.md](results/prefetch_mac_12302019.md)).

Why it loses on the Mac is not established, since the Mac reads no miss counters. It is off by default, because a default that loses on one host is not a default. It is the largest single gain left for the Linux numbers.

### Scalar against SIMD parsing

ITCH is fixed-offset binary with big-endian fields, so a scalar parser is a run of loads and byte swaps. The question was whether a shuffle-based SIMD decode beats it, per instruction set. On both hosts the parser's default stays scalar.

On `debian`, decoding an Add Order into its record alone, SSSE3 wins: 0.80 cycles against scalar's 9.94, with AVX2 at 1.78 ([results/simd_decode_debian_12302019.md](results/simd_decode_debian_12302019.md)). AVX2 is slower than SSSE3 on the same shuffle plan. The candidate reasons (Zen 1 splitting each 256-bit operation in two, clock changes, line-split stores) were not tested apart, so the cause is not established.

Inside the whole parse the order reverses. Over the day scalar takes 35.099 cycles a message, SSSE3 46.282 and AVX2 48.105, so SSSE3 runs at 0.693 of scalar's speed and AVX2 at 0.688 ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)). The work around the decode, the type dispatch and moving vector results back to integer registers, costs more than the shuffle saves. That reading comes from a diagnostic counter run recorded in docs/NOTES.md, not from a results file, and none of its figures is quoted here.

End to end with the book behind it, the kernels do not matter: scalar 187.966 ns a message, SSSE3 188.132 and AVX2 190.228, with the book making 2.008 DRAM fills a message on scalar ([results/simd_book_debian_12302019.md](results/simd_book_debian_12302019.md)). The book's waiting sets the pace.

On the Mac, NEON decodes an Add Order in 0.514 times scalar's cycles ([results/simd_decode_mac_12302019.md](results/simd_decode_mac_12302019.md)).

It wins the whole parse by less, 17.574 cycles a message against 18.790, under the 10% set before the run as the bar for changing the default ([results/simd_parse_mac_12302019.md](results/simd_parse_mac_12302019.md)).

For WebAssembly, SIMD does win. Core WebAssembly has no byte-reverse instruction, so the scalar path is shifts and ORs, and SIMD128 decodes an Add Order in 1.113 ns against 2.314 ns, on macOS, in node's V8 ([results/simd_e11_mac_12302019.md](results/simd_e11_mac_12302019.md)).

Two things could not be run. The AVX-512 VBMI kernel needs a CPU with VBMI, and neither host has one; its unit test skips on `debian` with the missing features named. The experiment on AVX2's first-burst cost needs a Skylake-family Intel server, and there is none here.

## Beyond the book

### The SPSC ring

The ring uses free-running 64-bit indices, a cached copy of the other side's index, a mask instead of a modulo, and batched publishing, with acquire and release ordering only. Its padding is 128 bytes on the Mac, the M-series cache line ([results/spsc_queues_mac_12302019.md](results/spsc_queues_mac_12302019.md)), and 64 on x86. ThreadSanitizer and a stress test run together, since the Mac's weaker memory model shows orderings that x86 hides.

On `debian`, with one thread pinned to core 4 and the other to core 6, the round trip against the other queues:

- atomic_queue 133.5 ns, moodycamel 169.3 ns, rigtorp 198.5 ns, folly 220.9 ns, this ring 229.3 ns, boost 264.5 ns, and a mutex queue 781.3 ns ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)).
- So T-SPSC-1 is missed on all three comparisons: boost over this ring 1.15× against a bar of 1.16×, and this ring 1.35× moodycamel's and 1.16× rigtorp's, where the bar is to match them ([results/spsc_queues_debian_12302019.md](results/spsc_queues_debian_12302019.md)).

Why this ring's round trip is slower than rigtorp's and atomic_queue's on this host is not established. No counters were read on the queue runs.

The ablation, T-SPSC-2, removed one technique at a time. The cached index gave 0.89× and 1.20× against bars of 3.90×, and the mask over a modulo 1.60× and 1.19× against 4.20×; the bars were set on an i7-9700 under WSL2 ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)). On this host the techniques are worth far less, and why was not measured.

Inside the replay the ring does its job. With the parser on one core and the book on another, the day takes 0.954 of the single-threaded time, against a bar of at most 1.31, so T-SPSC-3 is met ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)). The single-threaded runs varied with a CV of 6.25% and the pipelined ones with 4.57%, so the saving is small beside the noise ([results/spsc_pipeline_debian_12302019.md](results/spsc_pipeline_debian_12302019.md)). The parse is too small a part of a message for a second core to hide much.

### UDP receive: sockets against io_uring

A paced sender replays the day as MoldUDP64 over loopback on `debian`, and nine receive modes take it: S1 to S4 on sockets, U1 to U5 on io_uring. Loopback measures the system calls and copies of one host, not a network or a network card. Each mode's largest lossless rate was found by bisection at 1,400 bytes a datagram ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)):

- S4, `recvmmsg` spinning, won at 2,830,848 msg/s ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- S1, S3, U1, U2 and U3 all reached 2,735,896, the same bisection step, so the blocking socket and io_uring modes are not told apart here ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- U4 reached 1,945,047 and U5 1,912,149 ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)). Their trials above that lost no datagram. They counted as losses because the provided-buffer ring ran dry and a receive ended with ENOBUFS, which the rule fixed before the runs counts as a loss (docs/NOTES.md). A larger ring is the untested next step.
- T-URING-2 is met: S4 was lossless at 2,547,190 msg/s, Nasdaq's 1 ms peak, in 10 of 10 repetitions ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).

T-URING-1 has no bar, because no published head-to-head of io_uring and `recvmmsg` for UDP was found. The sender alone reaches 9,863,736 msg/s, so the receive side set these limits ([results/udp_ceiling_debian_12302019.md](results/udp_ceiling_debian_12302019.md)).

At a low rate the spinning modes answer much sooner: p50 6.06 µs for S4 against 32.56 µs for S1 ([results/udp_latency_debian_12302019.md](results/udp_latency_debian_12302019.md)). That is the cost of waking a blocked thread, and the spinning modes pay for it with a whole core.

At 72 bytes a datagram, the smallest that carries every message alone, no mode lost a datagram. The sender was the limit for every mode but U4, which stopped at 220,621 msg/s when its ring ran dry, so no mode can be ranked there ([results/udp_bisect_72b_debian_12302019.md](results/udp_bisect_72b_debian_12302019.md)).

### The backtester and the market maker

The backtester replays the real book and queues simulated orders among the real ones by order reference. Only depletion ahead of an order advances it; cancels behind it do not. It models latency on both market data and order entry, fees from a dated Nasdaq schedule at the base tier, and partial fills. A replayed feed cannot react to the strategy, so there is no market impact, and none is claimed. It passes 48 hand-built scenarios, and a planted lookahead bug is caught.

- T-BT-1 is met on `debian`: the whole day with a quoting strategy and its queue model at 4,570,542 messages/s, against a bar of 2.00 M ([results/backtest_quoting_debian_12302019.md](results/backtest_quoting_debian_12302019.md)).
- T-BT-3 is missed on `debian`: four symbols' messages with 100,000 shadow orders in real FIFO queues at 3,354,119 messages/s, 1.26 M under the 4.61 M bar ([results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md)).
- That bar was set on a Mac, and the Mac meets it ([results/backtest_shadow_mac_12302019.md](results/backtest_shadow_mac_12302019.md)).

The obvious suspect for T-BT-3 was the walk over simulated levels and orders. A ladder of schedules timed it at 4.969 ns a walk step, and on T-BT-3's schedule the walk is 3.51% of a message's 323.88 ns ([results/walks_ladder_debian_12302019.md](results/walks_ladder_debian_12302019.md)). The walk is not why T-BT-3 misses, and the rest of the gap is not established. The ladder's own T-BT-3 time also differs from the published file's, and why is not known, since neither run read counters.

The market maker is Avellaneda-Stoikov, tuned on 10302019 and reported on 12302019, so the report day is out of sample. Its PnL splits into spread capture, adverse selection and inventory, which sum to the ledger's total exactly, tested with `==` on integers ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

- On AMD, macOS, the base run made +15.90 dollars of spread capture, -27.50 of adverse selection and -14.36 of inventory, a total of -25.96, over 11 fills ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).
- With the latency model off, the same symbol-day totals +1.23 ([results/mm_pnl_12302019.md](results/mm_pnl_12302019.md)).

That is one symbol-day, a sample of one. It shows that the latency model changes the sign of the result, and nothing about an edge.

T-MM-2, reproducing the paper's average spreads, fails on one of three: 3.0317 at γ = 1, which is 3.03 at two decimals against the paper's 3.02. The file shows the simulation's time grid moving that figure, to 3.0217 and 3.0117 on two other grids, and keeps the failing grid as the measurement ([results/mm_as_tables.md](results/mm_as_tables.md)).

### The browser replay

The same `FastBook`, compiled to WebAssembly, replays one symbol's extract in the browser demo. On the Mac it ran 13,819,561 messages/s natively and 11,661,713 in the browser, 1.19× native ([results/wasm_replay_mac_12302019.md](results/wasm_replay_mac_12302019.md)). The bar names Google Chrome, which is not installed here. The run used Chrome for Testing, so the file gives no verdict. A browser clamps its clock and WebAssembly has no cycle counter, so the page shows messages per second from batches and no per-message latency.

## Correctness

### The oracle, and why it covers every message

A deliberately simple reference book replays the same day beside the fast book, and the two are compared after every message. Comparing both whole books after every message would take far too long. So the oracle compares exactly the state the message could have changed: every level it touched, in full (FIFO, aggregate and count), the best bid and offer, and the order's status. The fast book writes a level only through one function, `write_level()`, which records each write, and the oracle checks that every write fell inside the touched set. A level the book moves in memory is reported and compared too. The proof, by induction over messages, that state outside that set cannot have diverged is in docs/NOTES.md, and a whole-book hash every 1,000,000 messages backs it up ([results/oracle_12302019.md](results/oracle_12302019.md)).

- On 12302019 it compared 268,744,780 messages, 240,658,248 touched levels and 372,604,158 choke-point writes, with 0 write-set violations and 0 divergences ([results/oracle_12302019.md](results/oracle_12302019.md)).
- Over all ten sessions the divergences were 0 in 3,081,392,329 messages ([results/oracle_sessions.md](results/oracle_sessions.md)).

Planted bugs in the fast book are caught, each a row of the fault matrix.

The oracle has one known limit: it walks each touched level's whole FIFO. On a much busier recent sample day the run was stopped by hand after hours of CPU, nearly all in that walk, and never finished; its row is in [results/run_log.md](results/run_log.md). A per-message check without the whole-FIFO walk, with its own proof, is an open design change.

### The book is sometimes crossed, and that is the feed

T-INV-2 says the book is never crossed. Run strictly after every message, it fails, and it is reported failing. On 12302019 there were 18,958 crossed and 1,018 locked observations ([results/oracle_12302019.md](results/oracle_12302019.md)). It fails on nine of the ten sessions; the BX day passes ([results/oracle_01302019_BX.md](results/oracle_01302019_BX.md)).

Every violation was counted by symbol, time and trading state:

- On 12302019, 18,304 crossed observations came with the symbol not in normal trading, and the other 654 inside a reopening window after a halt, all within 50 ms of the resumption. None came in normal trading outside such a window ([results/oracle_12302019.md](results/oracle_12302019.md)).
- The counts match carteret's published figures for the same day exactly: 8,580 crossed book messages against 8,580 ([results/oracle_12302019.md](results/oracle_12302019.md)).

The crosses are the feed's, during halts, auctions and quoting periods, not the book's.

The user then chose a scoped rule, kept as a separate check, T-INV-3: in normal trading a book may be crossed only if the cross is left over from a halt and has not cleared yet. It passes with 0 violations. On 12302019, 22 of 33 resumptions found the book crossed or locked, and every one of those leftovers cleared ([results/oracle_12302019.md](results/oracle_12302019.md)). T-INV-2 strict stays failing beside it.

### The trade prints

Each message type is checked for what it can support, with no tolerance anywhere. T-TRADE-1 balances every order's shares from add to end, an internal pair of independent counts. T-TRADE-2 holds the cross prints against Nasdaq's CrossStats file. T-TRADE-3 holds each symbol's continuous volume against SEC MIDAS, an outside source. An `E` executes a resting order at its price, so T-TRADE-5 requires every `E` in normal trading to hit the best level.

- On 12302019, 5,683,178 of 5,683,178 such `E` messages did ([results/trades_12302019.md](results/trades_12302019.md)).

They do not all pass. T-TRADE-3 fails on four days and T-TRADE-5 on two, each listed with its file in the table below:

- On 01302020 MIDAS lists TAP twice, and the tool sums both rows ([results/trades_01302020.md](results/trades_01302020.md)).
- On 03272019 two `E` on RBZ executed away from the best just after a resumption, with that book crossed (docs/NOTES.md); the oracle agrees with the reference book that day ([results/trades_03272019.md](results/trades_03272019.md)).
- The other T-TRADE-3 differences, a few trades on a few symbols where the feed is above MIDAS, are not explained. Each stays a failure.

### The fault matrix

Every check above is held to the same standard as the code. The fault matrix plants a specific bug in a scratch copy and requires the named check to fail with the expected message. That includes the number gate that holds this page.

> **Final review, placeholder.** This paragraph is filled in at the end of the final review, with what the review found in the code, the checks and the documents, and what was fixed. It is not written yet.

## What was tried and did not work

Each of these was measured, and each lost or did not help. The full numbers are in the sections above.

- Prefetching the next message's loads gained 16.41% on `debian` ([results/prefetch_debian_12302019.md](results/prefetch_debian_12302019.md)), and lost on the Mac at every distance, so it is off by default.
- A table of function pointers and computed-goto threading did not beat the `switch`, and both missed more branches ([results/dispatch_debian_12302019.md](results/dispatch_debian_12302019.md)).
- SSSE3 and AVX2 parse kernels lost the whole parse on `debian`, at 0.693 and 0.688 of scalar's speed, though SSSE3 wins the decode alone ([results/simd_parse_debian_12302019.md](results/simd_parse_debian_12302019.md)).
- AVX2 lost to SSSE3 wherever both ran the same shuffle plan ([results/simd_decode_debian_12302019.md](results/simd_decode_debian_12302019.md)).
- NEON won the Mac's parse by less than the bar for changing the default ([results/simd_parse_mac_12302019.md](results/simd_parse_mac_12302019.md)).
- Over flat levels, a capped linear search tied, and a binary search lost 4.09% ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- Linked levels, on either index, lost by a wide margin to flat ones (the table in [Data layout](#data-layout)).
- The hash index, with an identity hash or with chaining, did not catch the paged index ([results/layout_debian_12302019.md](results/layout_debian_12302019.md)).
- On this host the SPSC ring's cached index and mask are worth a fraction of what the bars expected ([results/spsc_ablation_debian_12302019.md](results/spsc_ablation_debian_12302019.md)).
- io_uring's buffer-ring modes ran last at 1,400 bytes, stopped by their rings running dry ([results/udp_bisect_debian_12302019.md](results/udp_bisect_debian_12302019.md)).
- The pools' page-fault check could not be made to pass without root settings ([results/alloc_arms_base_debian_12302019.md](results/alloc_arms_base_debian_12302019.md)).

### Predictions that were wrong

Each experiment's prediction was committed first, and [results/predictions.md](results/predictions.md) keeps each one beside its outcome. The figures on these lines are predictions, not measurements; each outcome is in the section named.

- T-DESIGN-1 (P-0015): `paged-flat` over `map-fresh` predicted between 3.3 and 5.0, most likely near 4.1. It came in under that range ([Data layout](#data-layout) and the table below). <!-- figures: not measured -->
- The map book (P-0015): `map-fresh` predicted between 1.1 and 1.9 million messages a second. It ran faster than that, while `paged-flat` ran under its predicted 5.3 to 6.1 million. <!-- figures: not measured -->
- Chaining (P-0023): predicted 3% to 15% slower than open addressing. It was faster. <!-- figures: not measured -->
- Prefetching (P-0021): instructions a message predicted to rise by 15 to 60. They rose by far more, and the Mac, predicted to gain up to 30%, lost. <!-- figures: not measured -->
- Threading (P-0022): predicted to miss between 0.05 and 0.4 fewer branches a message than the `switch`. It missed more. <!-- figures: not measured -->
- The pools' page faults (P-0702): predicted 0 in every timed pass, and the crossing to huge pages predicted to gain 1% to 10%, most likely near 4%. The pools faulted, and the crossing gained more than that range. <!-- figures: not measured -->
- The UDP modes (P-0901): the nine predicted within 15% of one another, with the spinning modes on top. Two spinning io_uring modes came last. <!-- figures: not measured -->

### Never done or never published

- The replace, delete and add costs on an older and a recent day's message mix (P-0801, P-0802) have no result. Every Mac attempt was withdrawn under the load rules, stopped, or left too few quiet processes to publish, and the recent day needs a larger book capacity than the default (docs/NOTES.md).
- T-BOOK-4 and T-ARENA-1 on the Mac have no result, for the same reason. The table below counts their runs.
- T-PARSE-1 to T-PARSE-3, the parse rate in messages and GB a second, were never measured against their bars. The E4 parse is timed in ns a message, and no rate in those units was ever produced.
- The fuzzers have had only short smoke runs, not the 30-minute runs their target asks for.

## Every target missed or not measured

The table below is generated by `scripts/results_table.py` from the results files, and `scripts/check.sh` fails if it differs from a fresh run. A row whose file stops giving the verdict the row stands for is refused. So is a target in BENCHMARKS.md that has no row here and is not accounted for in the script as met, held or without a bar. The "Why" words are written in the script and carry no figure; the rest of each row is read from its file. Bars are as BENCHMARKS.md sets them, with the host each was set on. Book targets are the default arm's; every arm's verdicts are in [RESULTS.md](RESULTS.md). A "not measured" row has no figure from a run, so the number gate skips it, and its count of runs comes from [results/run_log.md](results/run_log.md).

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
| T-BT-3 | ≥ 4.61 M messages/s | 3.35 M messages/s | 1.26 M messages/s under the bar | debian | the bar was set on a Mac; the queue walk is not the cause, since without it the rate is still under the bar; the rest is not established: [Beyond the book](#beyond-the-book) | [results/backtest_shadow_debian_12302019.md](results/backtest_shadow_debian_12302019.md) |
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
| Fuzzing | each fuzzer runs clean for 30 minutes | not measured | not measured | none | the fuzz runs so far are short smoke runs; no 30-minute run is in results/ | none | <!-- figures: not measured -->
<!-- missed targets: end -->

Targets with no numeric bar, T-SIMD-1, T-URING-1 and T-ARENA-1's timings, are reported in the sections above. T-TRADE-4 is held: no Cboe data is used until the user decides.

## What this does not show

- **One host of each kind.** One Zen 1 embedded part and one Apple laptop. Most bars were set on desktop and server parts with more cache and newer cores, and no such host was available to measure how much of each gap is the host's. Every gap in the table is this host's and this design's together.
- **One venue.** Nine Nasdaq days and one BX day, all from Nasdaq's sample files, and all performance work on one day. Other venues' feeds and other days' message mixes may rank the designs differently.
- **No network.** The UDP replay runs over loopback on one host, so it measures system calls and copies. There is no kernel-bypass network card, no NIC timestamping and no real packet loss.
- **No market impact.** The backtester replays a feed that cannot react to the strategy. Its PnL says what the accounting does with one symbol-day, not what a strategy would earn.
- **No kernel view.** Profiles are user space only, so nothing here shows time in the kernel, the UDP receive path's included.
- **A noisy Mac.** The Mac cannot pin threads and shares its cores with other work, so its timings are context, and several Mac runs were never published.
- **No reserved huge pages.** The pools' explicit huge-page arm never ran, since reserving the pages is a root step.
- **No last-level miss count.** Zen 1's user-space events give a floor on DRAM demand fills and a count of L2 misses, not last-level misses.
- **One compiler build per host for the timings.** Clang 23 at `-O3`, with no `-march`; other compilers and flags were audited for code shape, not timed.
