# Notes

Working notes, kept as we go. Includes what didn't help.

## Research, 2026-10-30

Eleven subagents ran, all on `claude-opus-5-5` at high effort, confirmed by `verify_models.py` from the API request records. The account usage limit stopped the second round at about 13:00. Those five subagents returned no final message, but all five evidence files had already reached disk with their end sections, so none of that work needed repeating. A twelfth, narrow one ran later to look for anything else missed (below).

Evidence is in `build-research/evidence/` (gitignored). Third-party clones and page snapshots are in `.orchestrator/research-cache/itch-orderbook/`.

### Verification

`scripts/check_benchmark_sources.py` downloads each source and matches the exact text behind each number. It runs from any directory and takes `--only ID`. It holds no Cboe figures, so T-TRADE-4 was checked by rerunning the join instead. The session also reran the PnL identity check (24,000 exact equalities) and the per-tape join for 2019-12-30.

Opening the sources myself found real faults in the first draft:

- The NBER link for Aquilina, Budish and O'Neill was written from memory, not from a page anyone opened. The paper opened is w29011.
- Cook's host was called a "2017 Xeon". The slide says 32 CPUs at 2892.9 MHz, with no model.
- The Brouer range was wrong (391 to 600 ns/packet, across two posts).
- The Moallemi range covered only six of nine names (0.05 to 0.17 ticks).
- LobSim was quoted without saying its feed is synthetic. T-BT-1 is a real-day target resting on a synthetic-feed number.
- Applying Jane Street's 750 ns at p99.9 is our own reading, and now says so.
- MIDAS gives volume in thousands. `sec.gov` returns 403 to curl, so the link now names the capture that was opened.
- The subagent's symbol count for MIDAS was 5,771, and I count 5,772 Nasdaq rows on 2019-12-30. BENCHMARKS.md doesn't use the count.
- CrossStats is not total cross volume. 13 symbols differ from a published per-symbol file, because halt crosses have no column and test symbols have no row. T-TRADE-2 now compares opening and closing only.

### What carteret changed

The largest fault wasn't a wrong number. `EkanshSingh401/carteret` was never reviewed. Only the data track read it, for crossed-book counts, and that track wasn't asked about performance or correctness. I found it by reading the passage behind a check I was adding, and that passage mentioned 264 million messages compared.

It had made three claims false: that 50 M messages was the strongest published differential, that no reviewed repo diffs a full day, and that no source gives per-message cache misses. A fourth claim, that branch misses have no usable source, fell to the gap search. It's the closest published comparison to this project: same reference day, FIFO queues, isolated core, fixed clock, exact `rdpmc` counts, ten sessions differentially tested.

- **The oracle design is already published.** Touched level and BBO after every message, with a periodic whole-book hash. Ours has to add the proof about unchanged state.
- **Its stated gap is one we can close.** A misunderstanding shared by both books passes every layer it has. Our trade-print checks reach outside the two books.
- **`r0843:u` was in our own Zen 1 evidence** and never reached BENCHMARKS.md, which said only that L3 counters were denied. Core-side DRAM fills need no L3 access. It still has to pass a pointer chase on `debian`.
- **It publishes no branch-miss figure.** Its method names them, but only IPC appears in the log.
- **Targets take one real row.** T-LAT-4 and T-PERF-1 use its best measured configuration. Taking the best cell of each column would describe a configuration that never ran.
- **Its own failures are useful.** Five of ten predictions failed, SPSC was 31% slower than direct, and a first layout run measured the baseline four times because the switches never reached the compiler. Print the configuration from inside the binary.

"Looked in all OSS repos reviewed" only ever meant the repos someone was told to review. An absence claim needs its search stated, which is why the gap search ran.

### What the gap search changed

One more narrow subagent looked for anything else missed. Evidence is `gap-search.md`. I opened every source below myself before using it.

- **Nothing beats T-BOOK-1.** The closest are Ironbook's 16.3 M on two threads and 16.7 M on the first 40 M messages.
- **T-LAT-2 was not the tightest.** I had called it that an hour earlier. PitchFork's 446 and 778 ns are tighter, with the same fenced timer as carteret, so it became T-LAT-5. Its levels are aggregated, like T-LAT-2's.
- **T-PERF-2 has a floor.** Market-Core publishes real-day branch misses with a message count, which gives 2.04 per message. It's whole-process, on a 2010 part, so it's a floor rather than a bar.
- **Market-Core's latency table wasn't used.** Add p99 is 298 ns at one commit and 1,048 ns at a later one, on the same day and hardware, with no explanation. An unexplained retraction by the author rules it out, even though it would be the lowest tail on the page.
- **queuetruth's table rates are synthetic.** Its section 7 table is on generated records. The real-day figures are in the prose and the table below it. It also counts records, not messages, so T-BT-3 converts to messages.
- **ChathukaM looked like it raised T-BOOK-2 and didn't.** It reports 13.10 M, above 12.5 M, in a repo the search listed but never opened. Its levels hold only totals, so it isn't L3.
- **Ironbook's 10 crossings don't contradict carteret's 8,580.** Its check runs only with the market open and the symbol in state `T`.
- **aahilafraz's README overstates its check.** It claims FIFO coverage after every message, but mid-day checks pass `queues=false`.
- **Prefetch evidence is mixed.** Ironbook gains 41% on a full day from batching with staged prefetch (derived: 10.9 / 7.75). carteret's rule stopped it running a prefetch sweep at all. So this stays an experiment for us, not an assumption.

The search's own warning stands: all code-search queries returned nothing, so absence claims are weak. The keyword sweep of the 14 unopened repos found one real number, and two repos returned no README.

### Decisions

- **Reference day is `12302019`, not `S061226`.** The data subagent recommended the newest file, a 41.7 GB day. Headline numbers must come from `debian`, which has about 11 GB free, and the replay input must be resident before timing. `12302019` is 8.25 GB, and the strongest public results were measured on it. Its volume also ties to outside data. `S061226` stays as a Mac-only stretch, because its mix is genuinely different: `U` is 27.7% of messages there against 8.7% on `10302019`.
- **Tune on `10302019`, report on `12302019`.** Parameters are fitted from the market tape, never from a run's own fills. `10302019` is an FOMC statement day, so it is not a typical tuning day. That is stated, not hidden.
- **Cboe figures are not in the repo yet.** Its terms bar use "to verify or correct other data or information" without consent, subject to fair use. Research compared published ITCH sums with Cboe in gitignored evidence only. The repo-side check waits for the user's decision.
- **Avellaneda-Stoikov stays the named strategy.** The evidence recommends GLFT stationary quotes on one-tick stocks, but the user asked for Avellaneda-Stoikov. GLFT runs as a labelled variant.
- **Fees sit inside spread capture,** so the user's three components remain the complete sum. They are always shown as a sub-line, since fees can decide the sign on a one-tick stock.
- **Inventory is computed, never a residual.** A residual would make the required test pass by construction.

### Book and throughput

- "Full book" often means no queue order. parseritch, charles-cooper and CppTrader's fast variants aggregate price levels. They are not L3, and they beat the L3 numbers by a wide margin. Every rate needs FIFO yes or no beside it.
- "Parse" covers at least four jobs. A length-prefix walk and a full decode differ by 3.6× on one machine.
- Prefer the most honest published number over the highest. Excluded after reading the method: bsinek (8 symbols), Apex-Wire (all symbols in one book), LobIQ (per-symbol store), Mohith's 124 M and 2.9 GB/s (two different runs).
- Absolute numbers don't travel. Same itch-book code: 16.4 M msg/s on a Ryzen 9700X, 4.6 M on a 2.0 GHz EPYC VM.
- Mapping the file and tracking BBO costs itch-book 24%. Report the realistic pipeline as well as the headline.

### Data

- Nasdaq publishes no usable checksum. All listed `.md5sum` and `.done` files return 404. Integrity rests on the gzip trailer, third-party census byte counts, and our own pinned hash.
- Real files lack BinaryFILE's zero-length terminator. Completeness means ending on System Event `C`.
- The archive isn't stable: 8 listed `.md5sum` entries have no data file beside them. Keep the `.gz`.
- One published count is provably mislabelled (`PIYUSH-KUMAR1809`, DS-16). A count is a claim until our harness reproduces it.
- Sparse price fields are real: stub quotes reach $199,999.99. A flat array per symbol can't work.
- `X` messages can arrive after End of System Hours, which the spec text doesn't list. The spec has at least nine internal errors (DS-76).

### Terms

No document found names the sample files or `emi.nasdaq.com`. No text grants or denies rights. The nearest text points away from a public page built on real extracts: display on the public internet doesn't qualify as a publication "unless specifically approved by Nasdaq". The demo therefore replays a locally generated, gitignored extract, and public deployment needs written approval from Nasdaq. This is a reading of documents, not legal advice.

### Scalar against SIMD

Nothing published settles this. The only native NEON measurement ranges from 0.58× to 1.51× over 5 runs. No M4 instruction table is public, and Apple's guide needs a developer login. Firestorm data is labelled as M1 wherever used. Framing is a serial dependency, so it can't be vectorised directly. Prefetch and dispatch likely matter more than decode.

WASM may reverse the result, because its core instruction set has no byte-reverse.

### Trade prints

- `P` and `Q` cannot satisfy "hits a resting order": that holds for a whole class of message, and it stays surfaced as a count.
- The independent internal pair is `Add` shares against the sum of deductions for orders that die with no `D`. Nasdaq's silence is the witness.
- Published per-symbol ITCH sums tie to Cboe's Nasdaq-venue totals exactly on 2019-12-30 and 2019-08-30, across all three tapes, after removing test symbols. On 2026-06-12 the residual is 28 times larger and unexplained, which is one more reason not to use that day as the reference.
- Queue-front rate cannot be a hard check. Published 99.82% and 99.914%, and nobody has attributed the remainder.

### Backtester and market maker

- ITCH gives the real queue ahead of a simulated order, so `ahead` is known rather than estimated. That is what separates this from L2 backtesters.
- A replay still can't contain races lost by others, so a low-latency row flatters a strategy.
- No published equity backtest replays real L3 data with strategy PnL both with and without queue and latency modelling. No published AS replay on TotalView was found either. carteret is the nearest: a maker on real ITCH with exact queue position, comparing fill rates between queue models rather than PnL.
- Attribution identity was checked by `identity_check.py` in the research cache: 4,000 random fill sequences at six horizons, exact. That's Python on a worked model. The C++ test is still to be written.

### Components

- SPSC: only ratios can carry across hardware, so the target is a ratio. `debian` has one CCX, so rigtorp's 20× cross-CCX gain can't be reproduced there.
- Allocators: the strongest published result is a locality result, not an allocator-speed result. N4468 also shows a pool losing where locality isn't the issue.
- A monotonic arena can't hold a 2026-class day on `debian`. Use a recycling pool.

### io_uring, counters and flamegraphs

- No published head-to-head with `recvmmsg` exists. It's not known that io_uring wins, and the two nearest published figures disagree with each other.
- Loopback runs UDP receive softirq on the sender's CPU unless root configures RPS. So it's a syscall-and-copy benchmark, not a network one.
- At `perf_event_paranoid=2` a flamegraph has no kernel frames, so it cannot show the io_uring against socket difference at all.
- On Zen 1, `LLC-load-misses` is unsupported, `cache-misses` counts L2 misses (confirmed against the kernel's event map and AMD's PPR), and L3 counters are denied.

### This machine (macOS)

From probes run in the measurement track (`probes-measurement/` in the research cache):

- `mach_absolute_time` costs about 4 ns, at a 41.67 ns quantum. `CNTFRQ_EL0` reads 1 GHz, but the step is still about 41.67 ns, so the harness must measure the step rather than read the register.
- `thread_policy_set` returns 46, and threads migrated about 8 times per second under QoS.
- Cycles and instructions are available without root, through a private syscall per thread or `proc_pid_rusage` per process. Branch and cache misses are not.
- About 600 scheduling gaps per second at or above 1 µs put p99.9 near the OS jitter floor here.
- `CLOCK_MONOTONIC` steps 1 µs here. Use `CLOCK_UPTIME_RAW`.
- Xcode is absent, so `xctrace` and CPU Counters are untested.

### Process notes

- `debian` was reported ready during this phase, but Research needed nothing from it, so it wasn't used. No benchmark ran in this phase.
- The session wrote and committed the documents itself, because the instruction was to do so before spawning anything, and because verification had to be the session's own.
- Criterion 17's first correction said no repo diffs a full real day. That was wrong too, and it has been rewritten against carteret.
- Two subagents briefly wrote scratch files to `/tmp` against their brief, and moved them under the project area. No project data was involved.
- Third-party clones reached the working tree in the first round, some carrying their own `CLAUDE.md` and `AGENTS.md`. All were moved to the research cache when the rule arrived. A `sed` loop for the path rewrite failed the first time because zsh doesn't split unquoted variables on newlines; re-run with `while read`.
- The SIMD subagent reported `reasoning_effort 25` from its runtime and couldn't map it to a level. The API request records show `effort=high` for that agent, so it was not a violation.

## Harness, 2026-10-30

### Phase 2 decisions

- **T-TRADE-3 is exact.** Per-symbol equality of `E + printable C + P` with MIDAS volume, once MIDAS's time window is pinned. If it can't be pinned, the differences are an open gap with the reason. An inequality is never a pass.
- **T-TRADE-4 is held.** No Cboe data in this project until the user decides. Scripts don't fetch it, nothing depends on it, and no further joins are run. The terms note stays.
- **Trade-print checks are T-TRADE-1, 2, 3 and 5.** `P` and `Q` are counted and reported per the per-type table, so the refinement of the user's sentence stays visible.
- **T-INV-2 stays strict.** A failing strict result is reported as failing. No scoped version is a pass condition, and the user decides scope after the Phase 3 run.
- **T-ORACLE-2 is a stated extension.** It runs after T-ORACLE-1 passes, on the Mac. A shortfall is written up as a gap and must not hold up other work.
- **Flamegraphs are user-space only** at `perf_event_paranoid=2`, using `perf record -g --user-callchains -e cycles:u`. Kernel stacks are a listed gap. No system setting is changed.
- **The browser demo builds and runs locally from `docs/`.** Any extract is generated by script and gitignored, so nothing derived from the Nasdaq files is committed. Public deployment is the user's call.
- Two paper PDFs from the Research gap search were left in the Claude tool-results folder by WebFetch, outside the cache path. They were left there on instruction.
- `check_benchmark_sources.py` used to fetch Cboe's terms page. That entry is gone, so the terms quote in T-TRADE-4 is no longer rechecked.

### Parser and harness foundation

- **The parser layouts were checked against the spec PDF, not the code's comments.** A script compared every field's offset and length in `messages.hpp` with the PDF's tables for all 23 types. All agree. `V` is three Price(8) fields, and `K`'s release time is a 4-byte count of seconds.
- **A pass counts only when the check has been seen to fail.** `scripts/fault_matrix.py` runs each check against a fault made for it, in a scratch copy. A clean run of the same command has to pass first, without printing the signature. That control caught one signature of mine that also appeared on a clean tree.
- **Exit status alone is not a detection.** One fault failed because its test crashed in setup: it made its scratch directory under `build/`, which a fresh tree lacks. The matrix called it `wrong-signature`, and the same bug would have broken `check.sh` on a fresh clone. All three script tests now create the directory.
- **An inherited option had never run.** `ITCH_PREBUILT_DEPS` pointed at a directory GoogleTest does not write to. Exporting each commit and building it found this. The working tree could not, because it had never used the option.
- **Corpus replay is not the fuzzer.** Mutants keyed on a frame length or a 32-bit value that no seed holds pass the replay and fall to libFuzzer in seconds. A first version of one such fault claimed no seed could reach it. Running it against the replay showed that was false: `fuzz_decode` spreads each seed over all 23 buffers, so odd bytes land in every field. The fault was replaced.
- **An 8-byte `read_be48` passes every value test.** `test_wire`, `test_messages` and `test_reader` all stay green on that mutant. Only `test_guard_page` and ASan catch it, and only on the two 12-byte types, `S` and `W`, because no other message ends within 2 bytes of its timestamp.
- **The maximum RSS of the census is the mapping, not an allocation.** 8.25 GB on 12302019 is the file's resident pages. Measured on the binary itself, its peak memory footprint is 9.6 MB.
- **The fuzz runs so far are 90 s each.** They show the targets work. They are not the 30-minute runs the Done criteria ask for.
- **The held-vendor gate covers code, scripts, configuration and results.** Markdown is left out on purpose, because `BENCHMARKS.md` and this file record the hold and cite the terms. `scripts/test_fetch_data.py` is exempt, since it checks that the data scripts do not name the vendor.

### The oracle's proof

The brief asks for coverage after every message of the state that can have changed, and a proof that the rest cannot have diverged. `tests/oracle/diff.hpp` is the code. This is the argument.

**The claim.** Call a level `x = (locate, side, price)`, and `S(x)` what it holds: the FIFO of `(reference, shares)`, the aggregate shares and the order count. After every message, `S_fast(x) = S_ref(x)` for every `x`.

**The proof, by induction on messages.**

- Base: before the first message both books are empty.
- Step: assume the claim holds after message `n-1`. The reference applies message `n` and returns `T(n)`, the levels it can have changed, worked out from its own state. It is at most two levels.
  - For `x` in `T(n)`, the diff compares `S(x)` directly, in full, so the claim holds there by check, not by argument.
  - For `x` outside `T(n)`, the reference changed nothing by construction of `T(n)`, so `S_ref(x)` is unchanged. The fast book only changes a level inside `write_level()`, which reports each write to the policy object before making it. The oracle build records those writes, and the diff requires each one to be in `T(n)`. So `x` was not written, `S_fast(x)` is unchanged, and the two are equal by the assumption.

**What the proof rests on, and what covers each assumption.**

| Assumption | Covered by | Limit |
|---|---|---|
| The fast book writes a level only through `write_level()` | The hash and the exhaustive compare, which walk both whole books and never read the choke point's records | A bypassed write is seen at the next hash, up to 1,000,000 messages late, not at once. One undone before that hash escapes on a real day. The exhaustive compare on the fixtures and in the fuzzer has no such gap |
| `T(n)` holds everything the reference changed | The same whole-book walks, since a change outside `T(n)` appears as a difference. If the fast book made the same change, the write-set check fires | A wrong `T(n)` that both books agree with is the next row |
| The two books mean the same thing by a message | Nothing here. The reference decodes its own bytes at its own offsets, which rules out a shared misread offset, not a shared misunderstanding | This is carteret's stated gap. T-TRADE-1 to T-TRADE-3 reach outside both books and are what can close it. The fuzzer's model is a third opinion on prices and liveness, on random flow only |
| The hash is sound | It mixes every word of a canonical sequence, and the word counts must match as well | 64 bits and not cryptographic. A chance collision is about 2^-64 per comparison, and it is not meant to resist an adversary |
| The oracle build behaves like the build that ships | A default `FastBook<>` takes every message too, and its hash must equal the oracle build's at each hash point | At hash points only |

**What each piece of the diff covers.**

| Piece | When | Covers | Does not cover |
|---|---|---|---|
| Status | Every message | Both books apply or refuse alike, for the same reason | Whether either is right |
| Touched level | Every message, each level in `T(n)` | Existence, order count, stored aggregate against a sum from the FIFO, and the FIFO itself | Any other level |
| Best bid and offer | Every message, both sides of its locate | Price, shares and orders in the cached top of book, including a best level reached by a stale link | Depth below the best, and other locates |
| Write set | Every message | Each choke-point write lies in `T(n)`, whether or not it changed anything | Writes that bypass the choke point, and whether the write was correct |
| Periodic hash | Every 1,000,000 messages | All of both books, in canonical order, with stored totals and the cached top | Which message did it, and anything undone before it runs |
| End-of-day hash | Once | The same, after the last message | Little on a real day: the book ends empty, so it says only that both are empty |
| Exhaustive compare | Every message, fixtures and fuzzer only | All of both books, with the first differing word | Too slow for a whole day |

Price-order links (`better`, `worse`) and the reference index are structure, not level contents, so they are outside `S(x)` and the write set. A broken link shows at the best bid and offer if it reaches the best level, in `find_level()` when the touched compare goes looking, and in the canonical walk, which follows the links. A broken index shows as a status divergence the next time that reference is named.

### Building the oracle

- **Everything passed on the stub, except strict T-INV-2.** 268,744,780 messages diffed, 0 divergences, 0 T-INV-1 violations, 0 allocations in the fast book against 324,511,069 in the control. T-INV-2 strict fails, 18,958 crossed and 1,018 locked, as expected. `results/oracle_12302019.md` has the rest.
- **We match carteret only by counting its replace twice.** After book messages we have 8,579 crossed, and it publishes 8,580. Pinned at commit 3df0e87, its `on(OrderReplace)` calls `check_bbo` inside `add_retained` and again afterwards, so the one crossed `U` of the day (MKD, 10:54:21.569608700) counts twice. The same `U` is the difference in the reopening window (634 against 635) and inside 09:30 to 16:00 (8,371 against 8,372). The report keeps both, and says which rows use carteret's count.
- **I had carteret's population wrong at first.** I assumed it observed after every message it dispatches. It observes after book messages only. Every-message counts are more than double: 10,320 of the 18,958 crossed observations come straight after an `I`, which changes nothing in the book but finds it still crossed.
- **Ironbook reproduces exactly.** 136,896,897 checks, 10 crossed, 0 locked, on `A`, `F` and `U` with market open and state `T`. All 654 crossed observations in state `T`, on every message, are in a reopening window, the longest 1.2 ms after resumption. The deepest crossing, 140,000 in price units, is on the test symbol ZWZZT, and every locked observation in state `Q` is on the test symbol ZJZZT.
- **Peak depth also agrees with Ironbook's published high water.** 1,924,078 live orders and 784,060 live levels.
- **The oracle caught a bug in my own fixture before it caught anything else.** A hand-written replace in `lifecycle.itch` really did cross the book, and T-INV-2 said so. The fixture was wrong, not the tool.
- **The end-of-day hash is weak on a real day.** Every order is gone by the end, so it compares two empty books. The 268 periodic hashes do the work. The fixtures end with live orders, so there it means something.
- **A periodic hash can miss a short-lived fault.** With the best bid and offer compare off and a cached top left stale by a close, `--hash-every 100` passed the whole of `session.itch`: the next write on that side repairs the top, and no hash point fell in the gap. At every message the hash catches it. So the matrix runs that fault at `--hash-every 1`, and the limit stands as written in the proof: a real-day hash at 1,000,000 messages cannot stand in for the per-message compare.
- **The allocation counter sees `operator new`, not `malloc`.** A direct `malloc` on the hot path would pass T-ALLOC-1.
- **`fuzz_book` runs at about 120 executions a second.** Each one drives up to hundreds of messages through both books with a whole-book compare after each, so that is slow by design: 14,195 runs in 121 s, 562 new units, peak 356 MB, clean. The exhaustive per-message compare covers the model's locates, and the whole-book hash covers the rest at the end of each input. The planted fault passes the corpus replay and falls to libFuzzer within 34 executions on seed 1.
- **A day takes about ten minutes and 0.7 GB.** 669 s and a peak footprint of 0.66 GiB beside a sibling project's build, 599 s on a quieter run. Maximum RSS moved from 8.34 GiB to 6.14 GiB between those two runs with the same binary logic, because it counts file pages still resident, so it says more about the machine than the oracle. Quote the footprint.

### Trade prints against outside references

Both days ran `itch_trades` on the Phase 2 stub: 12302019 and 10302019. `results/trades_12302019.md` and `results/trades_10302019.md` have the numbers. **T-TRADE-3 fails on both days, on two symbols.** Everything else that could run passes.

| Result | 12302019 | 10302019 |
|---|---|---|
| T-TRADE-1 | PASS | PASS |
| T-TRADE-2 | PASS | Not available: no CrossStats file for that day is pinned |
| T-TRADE-3 | FAIL, 4 violations on MKL and NVR | FAIL, 4 violations on MKL and NVR |
| T-TRADE-5 | PASS | PASS |
| PRINTS | PASS | PASS |

**What was compared with what, and why the two sides could disagree.**

- Inside the book: each order's deductions against the remainder the book holds, and each `E` against the best price. The book can lose a deduction or keep a stale top. T-TRADE-5 is not independent of the book. It tests the book's order, not the book's prices.
- `Q` against CrossStats: the file is Nasdaq's, parsed apart from the feed, so a misread share count, type or symbol shows as a difference.
- `E + C_Y + P` against MIDAS: the SEC built it from the same direct feed with its own code. A wrong window, a wrong class, a misread size or a misattributed symbol shows as a difference.

**The MIDAS window is 09:35:00 to 16:00:00 in feed timestamps, half open.** The lead was the SEC's method page for its Market Activity Report, which says its sample runs from 9:35 but does not say that of `TradeVol`. It was then pinned by measurement on 12302019, and 10302019 agrees without any refit: shares, hidden shares, trades and hidden trades are equal on 5,771 of 5,771 symbols and on 5,767 of 5,767. The next-best window, 09:30 to 16:00, gets 2,609 and 3,322 symbols on shares. The nearest prints leave a gap of 23.8 ms at the start and 0.7 ms at the end on 12302019, and 3.3 ms and 0.6 ms on 10302019, so equality says no more than that about the bounds.

**The odd-lot columns are not exact, and the failure is left as a failure.** The user has since chosen the SEC's rule, and T-TRADE-3 passes on it: see "Two decisions on the strict results" below. This paragraph is the state before that. A fixed 100 failed BH and SEB on the first run. Reading the round lot from the directory message, the fix we expected to clear it, moved the failure instead of ending it: BH and SEB now agree, and MKL and NVR fail. Those two are listed at 10, and MIDAS agrees with a lot of 100 on them, to the share, on both days.

| Symbol | Day | Directory lot | MIDAS odd lots / shares | Feed below directory lot | Feed below 100 |
|---|---|---|---|---|---|
| MKL | 12302019 | 10 | 402 / 3,115 | 264 / 645 | 402 / 3,115 |
| NVR | 12302019 | 10 | 389 / 1,441 | 335 / 842 | 389 / 1,441 |
| MKL | 10302019 | 10 | 1,443 / 8,014 | 1,131 / 2,716 | 1,443 / 8,014 |
| NVR | 10302019 | 10 | 694 / 2,734 | 582 / 1,562 | 694 / 2,734 |

The reason is on the SEC's method page: until Nov 3, 2025 a round lot is 100 shares for all but three tickers, BRK.A and SEB at one and BH at 10. So MIDAS holds its own list and does not read the directory, and that list would make all four cells above equal. BRK.A has no Nasdaq row on either day. Nothing was fitted to this. Changing the rule is the user's decision, because it would be chosen after seeing the failure, so the check still compares the directory's lot and fails. Both reports print the two rules side by side from the raw log.

**Two bugs came out of this.**

- **The odd-lot check used a fixed 100.** See above. The directory holds a round lot per symbol, and 11 listed symbols differ from 100 on each day.
- **CrossStats `bad_rule` named line 2 of a one-line file.** `fuzz_crossstats` found it through its assertion that every error names a line that exists. The fix reports the line the reader is on. The seed `header-only-no-newline` and a unit test keep it.

**`C_N` is not only the visible part of a cross.** Every `C_N` is one of two things, told apart by match number. On 12302019, 4,324 are far legs that share a match number with a print, and 4,185 are cross legs. On 10302019, 4,065 and 2,845. The legs of a side never exceed their `Q`, always at its price, and they fill the whole cross on only 116 of 1,622 sides and 137 of 1,509, so they do not reconstruct `Q`. The longest lag behind a cross is 1.2 ms, on a halt cross.

**The one `B` of the two days is on a test symbol.** 10302019 has one: match 3259000, an `E` of 2 shares on ZVZZT. It finds its print, on its locate, once. 12302019 has none. ZVZZT has no MIDAS row, so these days cannot show whether MIDAS nets a break out.

**Match numbers are unique across print classes.** 7,059,179 and 8,989,570 records, and none shared between two print classes on either day. The only numbers shared are a far leg's with its print.

**`P` has no order to check.** It names no visible order, so its size is only checked against HiddenVol, on the symbols MIDAS covers.

**What the checks do not prove.**

- MIDAS is independent of this code, not of the feed. A fault in the feed itself agrees with itself.
- Listed symbols with no MIDAS row, 3,101 on 12302019 with 194,964,219 shares inside the window, and 3,085 on 10302019 with 229,244,579. Nothing outside the feed checks them. T-TRADE-1 and T-TRADE-5 still do, from inside.
- T-TRADE-2 ran on one day. The crosses of 10302019 were compared with nothing.
- The window bounds are known only as far as the gaps above.
- Equal volumes do not prove equal order of prints or equal prices. MIDAS has no price in these columns.
- This was the stub. It repeats on the real book.
- `fuzz_crossstats` ran 15,319,361 executions and `fuzz_midas` 4,562,304, 90 s each, clean. These are not the 30-minute runs the Done criteria ask for.

**A hidden fixture gap.** `trades-src-unit-whole-part` went undetected because every `TradeVol` and `HiddenVol` the fixture compared was under a thousand shares, so the whole part of the MIDAS value was always 0 and the mutant really was invisible there. The mutant was not equivalent. A hidden print of 1,000 on HLTD gave it something to break. Two other faults, `trades-exit-always-zero` and `trades-src-odd-lot-fixed-at-100`, never built, because their mutants left a name unused and `-Werror` refused them, which the matrix calls build-error and not detected.

### Benchmark harness

`bench/platform/`, `bench/replay.hpp`, `bench_replay`, `bench_probe`, the driver, `machine_check.sh`, the numbers gate, the predictions check and 42 bench faults. All of it was built and run on the Mac. The Linux paths have only had a syntax check here, against musl headers and a hand-written `linux/perf_event.h`, because this machine has neither glibc nor kernel headers. That check found nothing in our code, but it is not a compile. The first real one is on `debian`.

**What the decisions were.**

- The quantum is the greatest common divisor of the observed steps, not the smallest step. On `debian` every read is a multiple of 20 ticks and a pair costs about 38, so the smallest step seen would be 40.
- A percentile is resolved only if it clears 4 quanta after the empty pair is taken off. That is 167 ns here and about 40 ns on `debian`. An unresolved one has no value in the record at all, so there is nothing to copy into a table by mistake. The mean is kept: averaging is what gets under the tick.
- Latency samples go to a pre-faulted arena, and HdrHistogram sees them after the pass. The sink is a template parameter, so a fake clock can prove the order of events.
- Counters run in a fourth, untimed pass, so no timing run has them on.
- measure.sh's start line carries the hostname. `stamp_load.py` swaps that one word for the fixed host label, since the header may never hold a hostname, and refuses lines that do not bracket the run.
- `/usr/bin/time -l` reports peak RSS across the whole process tree but peak footprint only for its direct child, which here is the Python driver. The file says so. The 0.02 GiB is the driver's.

**The harness proof on 12302019.** [results/bench_replay_stub_mac_12302019.md](results/bench_replay_stub_mac_12302019.md), P-0001. Stub book, macOS, not pinned.

| Prediction | What happened |
|---|---|
| Input resident before every pass, 3 repetitions complete, no pin claimed | Held. |
| Quantum 41.67 ns, floor 166.67 ns, p50 and p90 unresolved | Held. p90 resolved in 2 of 3 repetitions, so the table leaves it unresolved. |
| Between 3 and 15 million messages/s | Held, at 3.50 to 10.26 million. |
| CV under 5% | **Wrong.** 67.35%. |
| Latency pass 1.0 to 1.6 times the throughput pass | **Wrong in one repetition.** 1.03, 0.55 and 1.04. |
| Peak RSS near 9.6 GiB | **Wrong by 0.7 GiB,** 8.90 GiB. The file is 8.25 GB decimal, which is 7.68 GiB. |

The prediction stays as written. The CV is the finding: these three repetitions do not measure one thing. Two throughput passes took about 76 s and one 26 s. The second repetition then ran its latency pass in 41.6 s, which is why a ratio below 1 appeared, and that ratio means nothing. The counter pass of repetition 1 took 289 billion cycles for the 86.8 billion instructions that took 113.7 billion cycles in the other two. So the slow code was the same code at about 2.5 times the cycles. The empty pair also doubled in the slow repetitions, 6 ns against 3 ns. That fits an unpinned thread landing on an efficiency core, despite `QOS_CLASS_USER_INTERACTIVE`, but nothing here shows which class of core ran which pass, so it stays a guess. No Mac rate from this file should be quoted. It shows the harness works on a whole day, and that macOS gives it no way to hold still.

**Two bugs the harness work found in itself.**

- `cross_check()` returned early with no events, so on macOS the counter pass never ran and the fixture showed 12,025 cycles for a pass of 3,330 messages. The count looked plausible. A test now requires the work to run with no events.
- `clang-tidy --fix` only sees the macOS branch. It made `probe_rdpmc` const and turned `obtained` const, both of which break Linux, and rewrote instance calls as static ones. All three would have been found only on `debian`. Rerun the Linux syntax check after any tidy fix to `bench/`.

**`scripts/check.sh --only tidy` fails, and not on this work.** 45 findings in `tests/test_book.cpp`, `tests/test_reference_files.cpp`, `tests/trades/` and `tools/itch_trades.cpp`, none in `bench/`. None of those files changed here. Left alone, as outside this job.

### Harness proof on `debian`

Everything below is from `debian`, on the stub book where a book is involved. Commits are named with each result.

**Linux compile.** `bench_probe` and `bench_replay` built at the first attempt, and the Linux paths in `bench/platform` needed no change. Two test files did. `std::mt19937` returns a 64-bit word on Linux, which broke `test_alloc.cpp`. And `debian`'s hostname is `debian`, the same as its label, so the header test's check for the hostname matched the `host` field. That field is now checked to be one of the three fixed labels.

**`rdpmc`.** User-mode `rdpmc` works at `perf_event_paranoid=2`, for all five events in the group, with `nmi_watchdog=1`. It is fenced with `lfence` on both sides now, because `rdpmc` does not serialise and a count could otherwise land on the wrong message.

**The chase, P-0002: held in all 3 runs.** [results/chase_debian.md](results/chase_debian.md), commit `9392a9ba8a5b`. `r0843:u` is 0.0000 per load at 64 KB and 128 KB, and 0.97 and 0.99 at 64 MB and 256 MB. `cache-misses` is 0.76 to 0.92 between L2 and L3, where `r0843:u` is at most 0.03. So `r0843:u` may stand in for last-level misses here, and `cache-misses` counts L2 misses. The widest `rdpmc` gap against `read()` on those two events is 0.000008 per load. `r0964:u`, opened raw in the same group, matches `cache-misses` to the event. sysfs gives `event=0x64,umask=0x09`, which is the PPR's PMCx064 bits 3 and 0 (55570-B1 Rev 3.14, p. 170). That PPR is for Model 18h and this CPU is Model 11h, so the measurement is what ties the bits to this part.

Two things the chase showed that nobody asked it. `cache-misses` passes one per load above L3, 1.16 at 64 MB and 1.62 at 256 MB, with one demand load per step, so it counts more than demand data. The page walker is the likely source, on 4 KB pages, and that is unmeasured. And a chase says nothing about prefetched lines: `r0843:u` counts demand fills only, so a book number from it is a floor on last-level misses.

**The wrong event, P-0003: held.** `bench_chase --raw-event 0x0143`, fills from local L2, commit `20cdcde135b0`. The verdict is FAIL on three criteria: encoding, 1.0000 per load under L2, and 0.0006 to 0.0030 above L3. It also agrees with the good run from the other side: fills from L2 at 1 MB, 0.2476, plus `cache-misses`, 0.7528, make one. Raw output only, in the research cache. Its `machine_check` ran straight after the chase rather than before.

**The stub proof, first attempt, P-0004: refused.** Commit `20cdcde135b0`. The harness refused at the first timed pass, with 155 of 2,014,504 pages gone. So the prediction that every page stays resident was wrong, and its others went untested. It stands as written. The log row says `unknown` for the commit, because the driver only learned the commit once a run had succeeded. That is fixed for later rows.

The likely cause is the order inside the harness, though nothing recorded which allocation did it. The page cache was full, with 219 MB free, and the 1 GiB sample arena is allocated after the warm-up, so it would push a few file pages out. The check also ran before each pass built its book, so a page lost to that allocation would never have been seen. Now residency is asked after the pass's allocations, any page they pushed out is read back first, and residency is asked again when the pass ends, with no touching, so a page lost in a timed span is a refusal. The refusal itself never moved.

**The stub proof, P-0006: held.** [results/bench_replay_stub_debian_12302019.md](results/bench_replay_stub_debian_12302019.md), commit `92e3f74c5766`. Every page was resident at each end of each pass, in 3 repetitions, pinned to core 4, no migrations. 2.28 to 2.49 million messages/s at CV 4.34%, which is close to the 5% limit. Ratio 1.09 to 1.15. p50 resolved. Peak RSS 8.90 GiB, from `getrusage`: `/usr/bin/time` is not installed on `debian`, and GNU time does not pass SIGTERM on, so it would break `measure.sh`'s signal path anyway. Over the whole counter pass, the widest `rdpmc` gap against `read()` is 11,150 of 687.8 billion cycles, and 13 of 1.28 billion on `r0843:u`.

The per-message means include the counter reads themselves, and no empty span is measured or subtracted. Each span from the first event's read to its second holds the other four reads, each with two `lfence`s. So `instructions` and `cycles` per message are too high by an amount not yet known, and the fill counts probably much less so. That has to be measured before T-PERF-1 or T-PERF-2 quotes a figure.

The driver is `measure.sh`'s child here and the benchmark is the driver's, which forwards signals. The chase and the flamegraph ran under `measure.sh` directly.

**Flamegraph, P-0005: held.** `perf record -g --user-callchains -e cycles:u` on a separate frame-pointer build, commit `92e3f74c5766`, 83,232 samples at 199 Hz, folded to 97 stacks. Stacks run from `main` to `FastBook::apply`, which is 93% of the cycles. No kernel frame appears, and none can: `cycles:k` is refused at `perf_event_paranoid=2`, exit 255, so time inside the kernel shows only as a gap. [results/flamegraph_stub_debian_12302019.svg](results/flamegraph_stub_debian_12302019.svg) has no home paths or addresses, at 32 KB. It says nothing about the real book. The frame-pointer throughput delta is not published: one run is not a measurement of it.

**Faults.** Fourteen new ones in `scripts/faults/bench.json`, and two for the residency order. The chase verdict, its table, the event encoding, the `rdpmc` agreement and the kernel mapping all run on the Mac from invented records in `tests/fixtures/chase`. What the PMU does can only be shown on `debian`, which is what P-0003 is for.

### CI, the comparison queues and the README

**CI has never executed on GitHub.** There is no remote, so `.github/workflows/ci.yml` has not run on any runner. `scripts/lint_workflows.py` reads it with actionlint at a pinned hash, with shellcheck off, so the shell in its steps is not checked. `gates.py pinned-actions` holds every `uses:` to a 40-hex SHA with its tag.

What was run by hand instead, at commit `b12d05e`, on presets or the same commands in a separate build dir:

| Job | How far | On |
|---|---|---|
| x86-64 Linux, GCC | release build and ctest, 19 of 19 | g++ 14.2 on `debian` |
| x86-64 Linux, Clang | release build and ctest, 19 of 19, with libstdc++ 14 as CI does and again with libc++ | Clang 23.1.4 on `debian` |
| arm64 macOS, Clang | `scripts/check.sh` stages | Clang 23.1.4 on the Mac |
| Emscripten | `scripts/check.sh --only wasm` | Emscripten 6.0.13 on the Mac |
| arm64 Linux, GCC | Not run. `-funsigned-char` on `debian`, 19 of 19, shows that one difference and no other | no arm64 Linux host |

That leaves these untested: the Ubuntu and macOS runner images, the `apt` and `brew` steps, the Linux archive through `scripts/fetch_tool.py` (its hash is the file on `debian`, and the macOS archive was fetched and unpacked for real), `emsdk install`, and any test that behaves differently on a VM with no PMU. CI's LLVM is the same release the repo was tested on, because nothing has shown the code builds on the Clang a runner ships. That costs about a 2 GB download per Clang job.

**GCC found three real bugs, and Clang on libc++ had hidden all three.** libstdc++ 14 has no `{:?}` format, so the trade checks print a wire byte through `wire_char`. GCC turns an unknown `[[clang::no_sanitize]]` into an error, so GCC now ignores the `clang::` namespace. And libstdc++ 14 declares `std::ranges::iota` in `<algorithm>`, not `<numeric>`.

**The comparison queues** are `ITCH_COMPARISON_QUEUES`, off by default, and no default configure unpacks them. Their build scripts never run, because `SOURCE_SUBDIR` names a directory they lack. One generated file under the build tree instantiates all three and is compiled, never linked or run. Boost's URL names the tag and not the commit: the tree with its submodules exists only as a release asset. `rigtorp` is pinned to the tag v1.1, and BENCHMARKS.md cites its later `HEAD`, so a Phase 3 result has to say which it ran. liburing comes from `ITCH_LIBURING_ROOT`, and CMake refuses any version but 2.15.

**README tables.** `scripts/results_table.py` writes them between two markers and fails on any difference, on a results file it has no reader for, and on a verdict no row took up. `results/fault_matrix.md` is still the run at `162b602`: the faults added here ran by area and are not in that file.

## Two decisions on the strict results, 2026-11-02

Both decisions are the user's. Both came after seeing real data. Prediction P-0007 was committed first and said T-INV-3 would have 0 violations and T-TRADE-3 would pass, on both days. It held.

| Result | 12302019 | 10302019 |
|---|---|---|
| T-ORACLE-1 | PASS, 268,744,780 messages, 0 divergences | PASS, 293,989,079 messages, 0 divergences. First oracle run on this day |
| T-INV-1 | PASS, 0 violations | PASS, 0 violations |
| T-INV-2 strict | FAIL, 18,958 crossed and 1,018 locked | FAIL, 4,240 crossed and 34 locked |
| T-INV-3 | PASS, 0 violations | PASS, 0 violations |
| T-ALLOC-1 | PASS | PASS |
| T-TRADE-3 | PASS, 0 violations | PASS, 0 violations |

### T-INV-3

**The rule, in the words the user approved:** "During normal trading, a book may only be crossed if the cross is left over from a halt and hasn't cleared yet. Any new cross is a failure." The approval had conditions. T-INV-2 strict stays as it is and is still reported as failing. The new rule is a second, separately named check. It had to hold on 10302019 before it counted as adopted, and it did. The definition is in BENCHMARKS.md. No time limit enters it, so the 50 ms of the published window plays no part.

**It held on 10302019 in the plainest way, and that limits what it shows.** That day has no crossed or locked observation at all with the market open and the symbol in state `T`. Its 8 resumptions found 2 books crossed, CYCN at 08:00:00 and TTMI at 16:35:00, both with the market closed. So the rule's leftover clause was not exercised on that day: nothing needed excusing. 12302019 is the only held day that exercises it, with 654 crossed and 10 locked observations all inside a leftover, 22 leftovers from 33 resumptions, and the longest lasting 1.2 ms.

**The old window already behaved almost like this.** I was told it opened on every resumption, and it does, which is why it counts 33. But it also closes on the first observation that is not crossed or locked, and the resumption's own `H` is such an observation when the book is clear. So on an uncrossed book it closes at once, and a new cross right after a resumption was already outside it. The difference is in what is reported: 22 real leftovers rather than 33 windows, and a stated rule with its own verdict. It also means the fault that opens a leftover on every resumption cannot move the verdict. It is caught on the count of leftovers, and that is the only place it could be.

**A cross message ends a leftover even if the book stays crossed.** The observation after that `Q` is then a violation, as the rule says. No held day has one: every leftover ended on the book.

**The exit codes changed for one fixture.** `crossed.itch` holds a new cross by design, so it now exits 1. `leftover.itch` is the fixture that exits 3. A mutated `lifecycle.itch` exits 1 for the same reason, and `oracle-inv2-crossed-input` now says so.

### The SEC's round lot in T-TRADE-3

**The history, plainly.** T-TRADE-3 first failed on odd lots with a fixed 100, on BH and SEB. The directory's lot cleared those and failed on MKL and NVR. The SEC's rule was chosen after both failures, from the SEC's published text, and nothing was fitted: it comes from the page, and no list was built from the data. Its text is "Until Nov 3, 2025, a round lot is defined as 100 shares for all but three tickers. BRK.A and SEB have a round lot of one. BH has a round lot of 10."

**The page's next sentence does not change the rule, and MIDAS shows why.** It says "All trades involving nine or fewer shares are considered odd lot trades." At a lot of 100 or 10 that adds nothing. At a lot of one it would make small prints odd, and MIDAS does not do that: SEB has 14 prints below 10 shares on 12302019 and 43 on 10302019, and MIDAS gives it no odd lots on either day. So the check takes a print below the lot, and a fault holds that reading.

**BRK.A cannot be exercised on these days.** MIDAS spells it `BRK.A`, with rows on other venues, and no Nasdaq row on either day. The feed spells its sibling `BRK.B` the same way, and Nasdaq's directory does not list BRK.A on either day. So the entry stands in the table under that spelling, the run reports it as not listed, and only a fixture exercises it. BH.A is a separate symbol at the common 100: the directory lists it at 10 and it has no Nasdaq row.

**The date is a guard, not a note.** For a date on or after 2025-11-03 the odd-lot columns are not compared, the tool says `t3_odd_lot_columns not_compared_rule_ended`, and the report and the README table say so. The report holds the date itself as well, so a late day compared on the old rule fails there. Shares and trades are still compared on such a day. No held day reaches this path, so only fixtures do.

**The directory's lot stays as information.** The `t3_round_lot` table still shows what it would give, beside the SEC's lot and below 100 and below 10.

## What the spec leaves out, 2026-11-02

The parser line in Phase 3 named five cases. None needed a change to the parser or the book: decode never validates an enumerated field, and the book gives `R` and `Q` `no_book_effect` and applies `X` wherever it comes. What was missing was proof and counts. The book scenarios in `test_book.cpp` now run each case on both books, `SpecOmissions.AStreamOfThemWalksDecodesAndApplies` runs all five through reader, decode and book as one stream, and `test_messages.cpp` covers round lots and Authenticity. Each test has a fault in `scripts/faults/parser.json` that writes the plausible wrong version: a closed category list, a book that drops test symbols, a fixed lot of 100, a refused or resting zero cross, and `X` ignored after `E` as the current spec text reads.

`itch_census` now counts each case, and `check_census.py` ties every split back to the manifest's `R` and `Q` counts, which `fetch_data.py` counted in Python. A second, separate Python recount gave the same lines on both days. The census takes about 12 s a day.

| Case | 12302019 | 10302019 | What it turned out to be |
|---|---|---|---|
| Market Category `M` (DS-64) | 0 | 0 | Not on either day. DS-64 says it starts in 2025 files. Fixture only. |
| Test symbols, Authenticity `T` (DS-65) | 34, 20 with orders | 34, 20 with orders | Real order flow: 6,546 and 13,185 adds. Prints 3,921 and 13,941, the same as `results/trades_*.md`. |
| Round lots other than 100 (DS-66) | 10 at 10, 1 at 1, 8,895 at 100 | 10 at 10, 1 at 1, 8,876 at 100 | As DS-66 says for 2019-12-30. No lot 40 here: that is 2026. |
| `Q` of zero shares (DS-69) | 12,189 of 17,836 | 12,085 of 17,775 | Zero shares and zero price are the same messages on both days: no `Q` has only one of them. All are type `O` or `C`. |
| Messages after System Event `E` (DS-71) | 51,200 `D`, no `X` | 44,214 `D`, no `X` | The 2026 `X` does not occur on these days. The fixture is the only place it runs. |

**One finding no entry predicted.** On 10302019 the directory lists HERO twice, at 03:07 and again at 05:05, with every field the same but tracking number and timestamp. The book does not read the directory, and every harness consumer overwrites with the same values, so it changes nothing. The census counts it as `directory_repeats`.

**What stays open.** The book holds test symbols exactly like live ones, as it should. Leaving them out of any statistic is the consumer's job, and no production consumer exists yet. The backtester and the demo will need it.
