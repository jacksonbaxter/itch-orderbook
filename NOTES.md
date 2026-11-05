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

**The claim.** Call a level `x = (locate, side, price)`, and `S(x)` what it holds: the FIFO of `(reference, shares)`, the aggregate shares and the order count. After every message, `S_fast(x) = S_ref(x)` for every `x`. The proof is the same on every arm of the fast book: the order index and the level arm are policies, and both arms of each feed the one choke point.

**The proof, by induction on messages.**

- Base: before the first message both books are empty.
- Step: assume the claim holds after message `n-1`. The reference applies message `n` and returns `T(n)`, the levels it can have changed, worked out from its own state. It is at most two levels.
  - For `x` in `T(n)`, the diff compares `S(x)` directly, in full, so the claim holds there by check, not by argument.
  - For `x` outside `T(n)`, the reference changed nothing by construction of `T(n)`, so `S_ref(x)` is unchanged. The fast book only changes a level inside `write_level()`, which reports each write to the policy object before making it. The oracle build records those writes, and the diff requires each one to be in `T(n)`. So `x` was not written, `S_fast(x)` is unchanged, and the two are equal by the assumption.
  - What finds `x` by its price can change without `x` being written. The linked arm relinks the neighbours of a level it opens or closes, and the flat arm shifts the entries after an insert or a removal and copies a whole array when it moves to a bigger block. Each of those is made inside `write_level()` too, by the level arm's `open()` and `close()`, and each reports the level whose link or entry it rewrites, as a move, before making it. The diff compares every moved level outside `T(n)` in full, through `find_level()`, after the message. So a level whose own link or entry was rewritten is checked at once, by the same compare as a touched one.

**What the proof rests on, and what covers each assumption.**

| Assumption | Covered by | Limit |
|---|---|---|
| The fast book writes a level only through `write_level()`, and rewrites what finds a level only from inside it, reporting each move | The hash and the exhaustive compare, which walk both whole books and never read the choke point's records | A bypassed write is seen at the next hash, up to 1,000,000 messages late, not at once. One undone before that hash escapes on a real day. The exhaustive compare on the fixtures and in the fuzzer has no such gap. The unit tests in `test_book.cpp` hold each arm's move reports to the levels it rewrites |
| `T(n)` holds everything the reference changed | The same whole-book walks, since a change outside `T(n)` appears as a difference. If the fast book made the same change, the write-set check fires | A wrong `T(n)` that both books agree with is the next row |
| The two books mean the same thing by a message | Nothing here. The reference decodes its own bytes at its own offsets, which rules out a shared misread offset, not a shared misunderstanding | This is carteret's stated gap. T-TRADE-1 to T-TRADE-3 reach outside both books and are what can close it. The fuzzer's model is a third opinion on prices and liveness, on random flow only |
| The hash is sound | It mixes every word of a canonical sequence, and the word counts must match as well | 64 bits and not cryptographic. A chance collision is about 2^-64 per comparison, and it is not meant to resist an adversary |
| The oracle build behaves like the build that ships | A default `FastBook<>` takes every message too, and its hash must equal the oracle build's at each hash point | At hash points only |

**What each piece of the diff covers.**

| Piece | When | Covers | Does not cover |
|---|---|---|---|
| Status | Every message | Both books apply or refuse alike, for the same reason | Whether either is right |
| Touched level | Every message, each level in `T(n)` | Existence, order count, stored aggregate against a sum from the FIFO, and the FIFO itself | Any other level |
| Best bid and offer | Every message, both sides of its locate | Price, shares and orders at the top of book: the linked arm's cached top, including a best level reached by a stale link, or the flat arm's last entry | Depth below the best, and other locates |
| Write set | Every message | Each choke-point write lies in `T(n)`, whether or not it changed anything | Writes that bypass the choke point, and whether the write was correct |
| Moved level | Every message, each level the arm reported as moved and that is not in `T(n)`, once | The same as a touched level, found through the structure as the message left it | A level whose own link or entry was not rewritten but whose path was: on the linked arm, one behind a relinked neighbour |
| Periodic hash | Every 1,000,000 messages | All of both books, in canonical order, with stored totals and the cached top | Which message did it, and anything undone before it runs |
| End-of-day hash | Once | The same, after the last message | Little on a real day: the book ends empty, so it says only that both are empty |
| Exhaustive compare | Every message, fixtures and fuzzer only | All of both books, with the first differing word | Too slow for a whole day |

Price-order structure (the linked arm's `better` and `worse`, the flat arm's sorted arrays) and the order index (hash or paged) are not level contents, so they are outside `S(x)` and the write set. Every rewrite of the price-order structure is reported as a move and its levels compared at once, which a link or entry gone wrong fails as a level-presence, aggregate, count or FIFO divergence with the detail `moved`. What the moved compare cannot see is the last row above. That shows at the best bid and offer if it reaches the best level, in `find_level()` when the touched compare goes looking, and in the canonical walk, which follows the structure. A broken index shows as a status divergence the next time that reference is named.

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

## Book arms, 2026-11-02

The Phase 2 stub is now `FastBook<WriteObserver, OrderIndex, Levels>`, with two arms of each policy, so that every pairing builds in one binary. `itch_oracle` and `itch_alloc` pick one with `--index hash|paged` and `--levels linked|flat`. Nothing here is a timing, and none is reported.

| Policy | Arm | What it is |
|---|---|---|
| Order lookup | `HashIndex` | The Phase 2 open-addressing table, unchanged: linear probing, at most half full, backward-shift deletion |
| Order lookup | `PagedIndex<8>` | A directory of pages of 256 slots for every reference below `2^index_bits`, pages from a pool and given back when their last reference goes. A reference past that reach, or one whose page the pool cannot supply, goes to a `HashIndex` sized for every order. Each such insert is counted (`index_fallback_inserts`) |
| Levels | `LinkedLevels` | The Phase 2 best-first list per side, with the best level's totals cached |
| Levels | `FlatLevels` | One sorted array per side, best at the end, keys apart from level slots. Arrays are blocks of 2^k entries from one arena, first block 8, doubling when full, with a free list per size. A side keeps its block when it empties. Level records stay in a pool, since orders point at them, so only entries move |

**The default is hash with linked levels, and it is provisional.** It is the simplest pair, the only one the oracle has run a whole day on, and the one with no capacity a day can run out of apart from orders and levels. The measurements of the four arms decide what ships.

**What a day asks of each arm.** A scratch probe read 12302019 under `heavy.sh` (34.9 s, 262 MB footprint): 140,270,523 adds, the largest reference 308,631,072 and none at or past 2^32, prices from 1 to 1,999,999,900, at most 1,924,078 live orders and 784,060 live levels. 17,783 sides hold a level at some point, the deepest 4,227 levels. The rest of the probe chose the sizes:

- **Page size.** The most pages pinned at once, by page bits: 8 → 340,939 (349 MB of slots), 10 → 189,644 (777 MB), 12 → 70,185 (1.15 GB), 16 → 4,684 (1.23 GB). A reference stays live for a while among neighbours that have gone, so a bigger page pins more memory, not less. 256 slots a page it is.
- **The arena.** Grow-only blocks hand out 1,642,464 entries at most, with 1,270,648 in use at the peak, 44,887 moves and 1,128,352 entries copied. Giving a block back when its side empties came to the same numbers (1,642,496 handed out) and would let a removal leave the next add short of room, so a side keeps its block.
- **Defaults.** 2^19 pages, a reach of 2^32 and 2^22 arena entries hold that day with room. The reach is a capacity field, `index_bits`, because a directory for 2^32 is 64 MiB, too much for every unit test and fuzz input.

**DS-72 and DS-73.** A reference the paged index cannot place falls back and is counted, never dropped, and a test runs it out of pages and past its reach. A full flat side whose block cannot grow returns `capacity_exhausted` and leaves the book as it was, and so does a replace onto it, which checks for room before its old order goes. Neither level arm indexes by price, so a far stub quote is only the farthest level.

**The proof needed one more report.** The flat arm moves entries of levels that no message touched, and the linked arm relinks the neighbours of a level it opens or closes. Neither changes what a level holds, but either can change what `find_level()` returns. So every such rewrite is reported to the write policy as a move, and the oracle compares every moved level outside `T(n)` in full. On `session.itch` with flat levels and an 8,192-entry arena, the moved compare runs and the arrays move to bigger blocks; `test_oracle_cli` holds that. The proof above says what it still cannot see at once. The oracle's record of moves is sized when the run is built, at three entries for each level the book can hold plus four, which no message can exceed: a replace on the flat arm shifts a side, moves it to a bigger block and shifts it again. More than that is a divergence, as more than 16 writes already was, so recording a move never allocates and never drops one.

**The default arm passes a whole day with the moved compare on.** `itch_oracle data/12302019.NASDAQ_ITCH50` under `heavy.sh`, hash with linked levels: 268,744,780 messages, 0 divergences, T-ORACLE-1, T-INV-1 and T-INV-3 pass, T-INV-2 strict fails at 18,958 crossed and 1,018 locked as before, and the peak depth is unchanged. 145,565,666 moves were reported and 122,021,707 moved levels outside `T(n)` were compared in full. Peak footprint 0.71 GiB, with the move record sized up front; 0.66 GiB on the run before it was. Raw output stays outside the repo; `results/oracle_12302019.md` is still the stub's run.

**Every arm gives the same answers.** On every oracle fixture, the four arms give the same verdicts, the same exit, and the same touched, BBO, write-set, hash and exhaustive counts, down to the last hash. Only the move counts differ. `test_book` runs every scenario on every arm and the reference book, and `test_alloc` sees 0 allocations on each arm over 200,018 messages that make the paged index run out of pages and fall back and make flat arrays move.

**`fuzz_book` runs each input through all four arms**, each against its own oracle and the model, with 2 pages, a reach of 2^16 and a 1,024-entry arena. 42,003 runs in 121 s on seed 1, 347 a second, 299 new units, peak 572 MB, clean.

**Faults.** Thirteen book faults went stale, because their anchor text moved into the arms, and three more still matched but no longer built, because they named members the arms took over. Each now targets the new code and is still caught. `oracle-write-untouched-level` finds its neighbour through the arm's own walk and search, so it builds on every arm. One changed what catches it: with the write-set check off, the bad write to a neighbour is now caught by the moved compare at the message that relinks it, not at the next hash, and `oracle-off-write-set` says so. The moved compare also caught `oracle-off-periodic-hash`'s bypassed write, at the last message of `session.itch`, before the end-of-day hash could run. That fault exists to show the end-of-day hash, so it now turns the moved compare off as well, and the end-of-day hash still catches it. Fourteen faults are new: an off-by-one in the flat insert position, a lookup on the wrong page, a FIFO broken on the flat arm only, a flat move pointing an entry at the wrong record (caught first by the moved compare, and by the hash with that compare off), the moved compare not run, the tools ignoring `--levels`, an unreported move on each arm, a dropped fallback, a replace that skips its room check, an allocation on each arm's rare path, and a move record too small for a message.

## Every arm on both days, 2026-11-02

The real book replaced the stub in `results/`. `scripts/check_real_day.sh --arms all` ran `itch_oracle` and `itch_alloc` and then `itch_trades` on each of the four arms, one job at a time under `heavy.sh`, each under `/usr/bin/time -l`, from a clean worktree at f09ea44. `oracle_report.py` and `trades_report.py` take the first arm's logs as before and each other arm's with `--arm-log`. They hold every arm to the same hard checks, then require every line of its logs to equal the first arm's, apart from the lines that name the arm, its moves, its own counters and its run time, and fail on any other difference. The script also requires each arm's file of crossed and locked observations to be byte for byte the first's. The README table reads the table of arms in each file and turns a row into a failure that names the arm if any arm gave another verdict. `itch_trades` took `--index` and `--levels` for this: it runs the fast book, so it ran on every arm too.

| Day | Result | Every arm |
|---|---|---|
| 12302019 | T-ORACLE-1 PASS, 268,744,780 messages, 0 divergences. T-INV-1 PASS. T-INV-2 strict FAIL, 18,958 crossed and 1,018 locked. T-INV-3 PASS. T-ALLOC-1 PASS, 0 against 324,511,069 in the control. Trades: T-TRADE-1, 2, 3, 5 and PRINTS PASS | The same, line for line, on all four |
| 10302019 | T-ORACLE-1 PASS, 293,989,079 messages, 0 divergences. T-INV-1 PASS. T-INV-2 strict FAIL, 4,240 crossed and 34 locked. T-INV-3 PASS. T-ALLOC-1 PASS, 0 against 361,109,587. Trades: T-TRADE-1, 3, 5 and PRINTS PASS, T-TRADE-2 not available | The same, line for line, on all four |

Every count matches the stub's runs, so the real book changed no answer. What differs between arms is what they moved and what they cost:

- **Moves.** The linked arm reported 145,565,666 moves on 12302019 and compared 122,021,707 moved levels outside `T(n)`. The flat arm reported 1,721,870,469 and compared 1,096,279,881, since an insert or removal shifts every entry after it. On 10302019, 174,162,078 and 146,926,464 linked, 1,174,990,546 and 840,041,978 flat.
- **Capacity.** The paged index never fell back on either day. It pinned at most 340,939 pages on 12302019, as the probe said, and 305,506 on 10302019. The flat arena reached 1,642,464 entries with 44,887 moves to bigger blocks on 12302019, also as the probe said, and 1,591,832 with 44,202 on 10302019.
- **Memory.** Peak footprint of the oracle run: 0.71 GiB hash with linked levels, 0.74 hash with flat, 1.84 paged with linked, 1.87 paged with flat, on 12302019, and within 0.03 GiB of each on 10302019. The paged arms carry 512 MiB of page slots and a 64 MiB directory sized up front, so this is the capacity, not the day: sizing `PagedIndex` stays open. Maximum RSS is 8.4 to 10.3 GiB, mostly the mapped file.
- **Time.** Each oracle run took 323 to 468 s of replay. These are not measurements: the runs shared the machine with builds and the fault matrix, and nothing here is reported as a timing.

## The map baseline for T-DESIGN-1, 2026-11-02

`include/itch/map_book.hpp` is the naive book that T-DESIGN-1 measures the fast book against: per (locate, side) a `std::map` from price to level, each level a `std::list` of orders oldest first with its total beside it, and a `std::unordered_map` from reference to the order's level and list node. It takes the same payloads and returns the same statuses as `FastBook`, and it allocates on every add and frees on every removal, which is the point. It is not part of the oracle: the oracle's reference book stays the independent one, with its own decoding. The map book uses the parser, as the fast book does, because the comparison it exists for is between two designs of the same production path. Nothing about it is timed yet.

**How the heap is aged, and where that comes from.** The Optiver talk (Gross, CppCon 2024; FT-1 and FT-2 in `evidence/firm-talks.md`) shows a `std::map` book at a median 33 ns and then at 63 ns on the same data, after "between the add operation I just added some memory locations not really to really do much with them but just to randomize the heap" (transcript 00:18:40 to 00:18:52). Its reason is that consecutive mallocs give a fresh benchmark contiguous memory, which production does not. That sentence is all the source says: no sizes, counts, order or seed. `include/itch/heap_ager.hpp` fills those in and fixes them by a seed:

1. Before the book takes a message, the ager allocates `warm_blocks` blocks (2^20 by default) of 16 to 256 bytes in steps of 16, which spans the map book's node sizes. It shuffles them with Fisher-Yates and frees the first half in that order. The other half stays as ballast, and the holes between it go back to the allocator in no address order.
2. Before each message that adds an order (`A`, `F` or `U`), it frees the block in one drawn slot of a ring of `ring_blocks` (2^16 by default) and allocates one of a drawn size in its place. This is the talk's "between the add operation", kept outside `apply()` so that a timed loop can leave it out of the span.

Every draw comes from splitmix64 on the seed, not from `<random>`, whose distributions differ between standard libraries. So a seed fixes every call, its size and its order. It does not fix the addresses: those depend on the allocator and on address-space randomisation, which is what ageing is for. `HeapAgeing.TheSeedAloneFixesEveryDraw` holds two agers on one seed to the same draws and two seeds apart, and faults that ignore the seed or mix in the ager's own address fail it. Every block goes through global `operator new`, so `test_alloc` counts them: one per add-type message, and none for anything else.

**How it is shown correct.** `test_book` runs every hand-built scenario on the map book, fresh and aged, beside the four fast arms and the reference book. `itch_map_compare` replays a stream through the map book and a fast arm side by side. The oracle's diff does not fit: it needs the choke point's write records and the reference's touched set, and the map book has neither. So after every message it compares the status and the top of book on both sides of that message's locate, and every 1,000,000 messages and at the end it hashes each whole book with the canonical walk the oracle uses, `emit_map` beside `emit_fast` in `tests/oracle/canonical.hpp`, so a match means the same words: every side, level, total, count and FIFO in order. At the end it also compares live orders and levels. `test_map_compare_cli` runs every oracle fixture and the trade fixture on all four arms and both heaps with a whole-book hash after every message, and the faults in `scripts/faults/mapbook.json` show each piece failing: a stale level total and a kept empty level on the top-of-book compare, a reversed FIFO on the periodic hash and on the end-of-run hash, a duplicate taken and a locate unchecked on the status compare, the top-of-book compare switched off with the stale total still caught by the end-of-run hash, the scenarios, and each part of the ager. `scripts/check_real_day.sh --only map` runs both heaps on a real day.

**What the per-message compare does not cover.** A level below the best, or another locate, that the map book gets wrong is seen at the next hash, up to 1,000,000 messages late, and one that is put right before it is not seen at all. On the fixtures the hash runs after every message, so there is no such gap.

**Both days agree.** `scripts/check_real_day.sh --only map` under `heavy.sh`, from a clean worktree at f46e877, hash with linked levels against the map book:

| Day | Heap | Messages compared | Top-of-book compares | Whole-book hashes | Divergences | Ager blocks |
|---|---|---|---|---|---|---|
| 12302019 | fresh | 268,744,780 | 537,489,560 | 268 and the final | 0 | none |
| 12302019 | aged | 268,744,780 | 537,489,560 | 268 and the final | 0 | 141,319,099 |
| 10302019 | fresh | 293,989,079 | 587,978,158 | 293 and the final | 0 | none |
| 10302019 | aged | 293,989,079 | 587,978,158 | 293 and the final | 0 | 155,200,784 |

The ager's count is a check of its own: 2^20 warm-up blocks plus one per `A`, `F` and `U`, which is 140,270,523 orders opened on 12302019 and 154,152,208 on 10302019, the figures `itch_trades` counts. Peak footprint was 0.50 GiB fresh and 0.66 GiB aged on 12302019, 0.49 and 0.65 on 10302019, both books together. Only the default fast arm was compared, because the oracle runs above show the four arms equal to each other line for line. The ends of both days are empty books, so the final hash says little; the periodic hashes do the work, as in the oracle. No time is reported.

## Sizing the paged index, 2026-11-03

`PagedIndex`'s default capacity was a round 2^19 pages and a reach of 2^32, set before either day had been counted. Both days have now been counted, so the capacity comes from those counts (commit 1ae701e):

- **Pages.** At most 340,939 pages are pinned at once on 12302019 and 305,506 on 10302019. The default takes the larger peak, adds a quarter, and rounds up to a multiple of 4,096: 428,032 pages, 418 MiB of slots, against 512 MiB before. A quarter is room for a busier day than either of these, and no more, since every page is held from the start.
- **Reach.** The largest reference is 308,631,072 on 12302019 and 316,375,126 on 10302019. 2^30 reaches more than three times past both, for a 16 MiB directory against 64 MiB at 2^32. A reach of 2^29 would be under twice the largest, close enough that a later day could pass it.
- **DS-72 still holds.** A reference past the reach, or one whose page the pool cannot supply, goes to the fallback hash and is counted. The unit tests that run the index out of pages and past its reach are unchanged and pass.

Whole-day runs of the two paged arms on the new default, binaries from 1ae701e, each under `heavy.sh` and `/usr/bin/time -l`:

| Day | Arm | Oracle | Fallback inserts | Peak pages | Oracle peak footprint | T-ALLOC-1 | `itch_alloc` peak footprint |
|---|---|---|---|---|---|---|---|
| 12302019 | paged, linked | T-ORACLE-1, T-INV-1, T-INV-3 PASS; T-INV-2 strict FAIL, 18,958 and 1,018 | 0 | 340,939 | 1.56 GiB | PASS, 0 against 324,511,069 | 0.88 GiB |
| 12302019 | paged, flat | the same | 0 | 340,939 | 1.59 GiB | PASS, the same | 0.89 GiB |
| 10302019 | paged, linked | T-ORACLE-1, T-INV-1, T-INV-3 PASS; T-INV-2 strict FAIL, 4,240 and 34 | 0 | 305,506 | 1.55 GiB | PASS, 0 against 361,109,587 | 0.87 GiB |
| 10302019 | paged, flat | the same | 0 | 305,506 | 1.58 GiB | PASS, the same | 0.88 GiB |

Every verdict and count is the one the earlier runs gave, and `itch_oracle` exits 3 for the strict T-INV-2 failure, as before. The oracle's peak footprint fell from 1.84 and 1.87 GiB to 1.56 and 1.59 GiB on 12302019. The smaller capacity accounts for 0.14 GiB of that 0.28 GiB fall; the rest was not traced. Maximum RSS stays at 9.2 to 10.7 GiB, mostly the mapped file. The run times are not measurements and are not reported. Raw logs stay in the research cache.

## Measuring every book design, 2026-11-03

`bench_replay` measures any design by name: `--design hash-linked|hash-flat|paged-linked|paged-flat|map-fresh|map-aged`, `--input-mode ram|mapped`, `--track-top on|off`. `scripts/bench_driver.py --design ...` runs every design named in each repetition, one after the other, so that every design of a run shares the day, the passes and the machine's state, and it writes one results file for each. The decisions, and why:

- **Two input modes, after itch-book's two methods.** `ram` reads the file into anonymous memory before the warm-up and never maps it, which is how itch-book's `book_throughput` times T-BOOK-1: a buffer in RAM, the book alone. `mapped` maps the file read-only, which is what its `itch-replay --book` does for T-BOOK-2. `--track-top on` adds that tool's check: after each message the book applied, read the best bid and ask on its locate and compare price and shares with the last seen there. Each results file says which of the two methods its run is, or that it is neither.
- **Book memory is measured in a process of its own.** The first try took the charged memory before and after the untimed pass's book, in the same process as the timed passes. It read close to zero for the fast book: the allocator handed back the pages the timed passes' books had freed, so the third book cost nothing new. So each repetition now runs two processes: the timed one (warm-up, throughput, latency), and an untimed one that faults the input in, builds no book, then builds one and runs the counter pass while sampling the charged memory every 2^20 messages. The book's memory is the peak less the reading taken before it was built. The charged figure is `phys_footprint` on macOS and `RssAnon` on Linux, so the mapped file, which is file-backed, is never in it, and the RAM copy is in the baseline. itch-book reports peak RSS less the input buffer, the same quantity.
- **The map book warms up on the fast book.** A map design's own warm-up would leave its freed nodes in the allocator, and its throughput pass would then run on a heap it had already shaped. Its warm-up pass runs the fast book's default arm instead, which takes no small blocks, so the timed pass's map book is the first the heap has seen. The record says what the warm-up ran on.
- **Ageing steps are outside the time.** For `map-aged`, the throughput pass stops its clock for each `HeapAger` step and counts it, and the latency pass takes the step before the span opens. The step is the talk's "between the add operation", not part of the book's work.
- **Ratios are paired by repetition.** T-DESIGN-1 is a ratio on one host in one run, so each repetition's fast throughput is divided by the map design's in the same repetition, and the median of those is reported.
- **Percentiles below the floor print "below floor".** Every latency table has a raw column and one with each repetition's own measured empty pair taken off, and a row below the floor shows no figure in either.
- **x86 bars are context on the Mac.** T-BOOK-1, T-BOOK-2, T-LAT-1, T-LAT-2, T-LAT-4 and T-LAT-5 were set on x86 hardware. On macOS the targets table gives the signed gap and says the host differs, and `check_results.py` refuses "met" or "missed" there. T-BOOK-3, T-LAT-3 and T-DESIGN-1 are host-independent and are judged.

**The Mac runs, 12302019.** Two `measure.sh` runs, each under `/usr/bin/time -l`, 3 repetitions, on `macOS (M4 Max)`, not pinned: macOS cannot pin a thread, so no figure below is a pinned one, and none stands for a Linux figure. Run A (P-0008) ran all six designs with the file in memory and the top not tracked, itch-book's T-BOOK-1 method, from 9934df5. Run B (P-0009) ran the four fast arms with the file mapped and the top tracked, its T-BOOK-2 method, from f3fe97a. Every repetition completed, every page was resident at both ends of every timed pass, no message was rejected, every design counted 264,478,375 book messages of 268,744,780, and in run B every arm saw the top move after the same 124,767,189 messages. Peak RSS was 9.59 GiB in each run, the input and the latency samples with the book.

| Design | Run A messages/s | CV | Batch mean ns a book message | Book memory MiB | Over map-fresh | Over map-aged | Run B messages/s | B over A |
|---|---|---|---|---|---|---|---|---|
| `paged-flat` | 21,873,432 | 0.76% | 46.46 | 662.8 | 3.79× | 4.08× | 19,335,339 | 0.88 |
| `hash-flat` | 17,705,490 | 0.55% | 57.39 | 225.2 | 3.07× | 3.31× | 15,408,545 | 0.87 |
| `paged-linked` | 11,934,176 | 5.28% | 85.14 | 648.7 | 2.07× | 2.23× | 11,070,036 | 0.93 |
| `hash-linked` | 10,410,417 | 0.32% | 97.61 | 211.2 | 1.80× | 1.94× | 9,626,384 | 0.92 |
| `map-fresh` | 5,772,425 | 0.65% | 176.03 | 260.8 | | 1.08× | | |
| `map-aged` | 5,353,839 | 1.27% | 189.79 | 410.1 | 0.93× | | | |

Each target against its bar, from the files' targets tables. On macOS a bar set on x86 is context: the gap is signed, and the host differs.

| Target | Bar | Measured, best arm (`paged-flat`) | Verdict |
|---|---|---|---|
| T-BOOK-1 | ≥ 16.4 M msg/s, Ryzen 7 9700X | 21.87 M msg/s; `hash-flat` 17.71 M, the linked arms 10.41 and 11.93 M | host differs: +5.47 M msg/s on the best arm, -5.99 M on `hash-linked` |
| T-BOOK-2 | ≥ 12.5 M msg/s, Ryzen 7 9700X | 19.34 M msg/s; `hash-flat` 15.41 M, the linked arms 9.63 and 11.07 M | host differs: +6.84 M msg/s on the best arm, -2.87 M on `hash-linked` |
| T-BOOK-3 | ≤ 1.4 GB of book | 0.69 GB; 0.22 to 0.69 GB on every arm | met on every arm |
| T-BOOK-4 | ≥ 14.4 M msg/s on 01302019 | | not measured: needs 01302019 |
| T-LAT-1 | per operation, Ryzen 7 9700X | every p50 below the floor; replace p99.9 1,125 ns against 945 | host differs: replace p99.9 +180 ns, every other resolved figure on the best arm inside its bar; `hash-flat` is over on replace p99.9 too, by 221.67 ns, and the linked arms' replace p99 is 4,458 to 4,500 ns |
| T-LAT-2 | p50 121, p99 552, p99.9 874 ns | p50 below floor, p99 291.67, p99.9 583.33 ns | host differs: p99 -260.33, p99.9 -290.67 ns |
| T-LAT-3 | mean ≤ 393 ns, p99.9 ≤ 750 ns | mean 45.72 ns, p99.9 583.33 ns | met on the flat arms; missed on the linked arms, p99.9 by 3,708 and 3,750 ns |
| T-LAT-4 | p50 100, p99 620, p99.9 1,080 ns less the pair; batch mean ≤ 142.36 ns | p50 below floor, p99 289.30, p99.9 580.97 ns; batch mean 46.46 ns | host differs: p99 -330.70, p99.9 -499.03 ns, batch mean -95.90 ns |
| T-LAT-5 | p50 127, p99 446, p99.9 778 ns | p50 below floor, p99 291.67, p99.9 583.33 ns | host differs: p99 -154.33, p99.9 -194.67 ns |
| T-DESIGN-1 | ≥ 4.0× the map book | 3.79× over `map-fresh`, 4.08× over `map-aged` | missed over the fresh heap by 0.21×, met over the aged one; every other arm misses both |

**P-0008 against what ran.** Held: `paged-flat` wins; `map-fresh` is between 2.5 and 6 million; every throughput CV is under 10%; the ratio over `map-fresh` is 3.79, near 3.5, and the bar is missed there; the ratio over `map-aged` is 4.08 and met; `hash-linked` holds 0.21 GiB and the paged arms 0.63 and 0.65 GiB; every fast-arm p50, all-message and per operation, is below the floor; p99.9 is resolved everywhere; the mean service time is under 125 ns on every fast arm and the batch mean is 1.6% above it; peak memory is under 10.5 GiB. Not held: `hash-flat` is second, not `paged-linked`, which is third; `paged-flat` runs at 21.87 million, past the 8 to 20 million range, and nowhere near 14; `map-aged` runs at 5.35 million, just past its 2 to 5; and `map-fresh` holds 0.25 GiB, under the 0.3 predicted. The level arm matters more than the index: flat levels are 1.7 to 1.8 times linked ones on either index, and the paged index adds 1.15 to 1.24 times on either level arm.

**P-0009 against what ran.** Held: `paged-flat` wins again; each arm runs at 87% to 93% of its run A rate; every arm saw the same moves of the top in both passes of every repetition; peak memory 9.59 GiB. Not held: the winner runs at 19.34 million, past the 7 to 18 predicted.

**Slow spots seen, left alone.** Tuning is out of scope here, so these are written down and not touched:

- **Linked levels on a deep side.** `replace` has a mean of 444 to 474 ns on the linked arms against 118 to 139 ns on the flat ones, and a p99 of 4.5 µs against 0.6 µs. `add` has a p99.9 of 2.4 µs against 0.3 µs. A side reaches 4,227 levels on this day, and the linked arm walks its list from the best to find a price that is not cached. That walk is what T-LAT-3's p99.9 misses on.
- **The map book's worst message.** Its max span is 72.9 ms fresh and 79.8 ms aged, against medians of 39 to 90 µs on the fast arms. Not traced. Growing the reference table's `unordered_map` past millions of entries, which rehashes it whole, is the likely cause.
- **The fast arms' max** has a median of 39 to 90 µs, with p99.99 at 1.2 to 9.2 µs. Not traced, and on an unpinned host a preemption is enough to explain it.

**What went wrong on the way.**

- The first try at book memory read the charged memory before and after the untimed pass's book, in the process that had just run the timed passes, and got close to zero for the fast book: its pages came back from the earlier books. Hence the separate untimed process.
- The map book first warmed up on itself, which shaped its own heap before the timed pass. Hence the fast-book warm-up for map designs.
- The first run of P-0008 was refused at its first process: `bench_replay` could not read the day into memory, because macOS refuses a single `read()` of more than INT_MAX bytes and the fixture is far too small to show it. It stays in the run log. Reading in pieces of 1 GiB fixed it, and P-0008 then ran as predicted, with no other change. Before that, the script that wraps the runs failed on its own empty argument list under bash 3.2 before it reached `measure.sh`, so that attempt measured nothing and is not in the run log.
- Raw records, stderr with the load lines, and `/usr/bin/time` output stay in the research cache. Only the generated files are in `results/`.

## The book designs on `debian`, and the default arm, 2026-11-03

Every figure here is from `debian`, pinned to core 4, binaries built there from bfdd4bb exported by `debian_sync.sh`, through `~/quant-projects/bin/measure.sh taskset -c 4` with the driver as its child, as the stub proof ran. Raw records, stderr with the load lines, memory samples and both `machine_check` outputs stay in the research cache. The predictions are P-0010 and P-0011, committed in eb72a15 before either ran.

**Only the mapped run is published.** Run A, all six designs with the day in memory (P-0010), was tried three times and published nothing; run B, the four fast arms with the file mapped and the top tracked (P-0011), ran once and was published. Each attempt has a row in `results/run_log.md`:

- The first attempt ran at nice 5, which zsh gives a job started in the background, and in its first minute the kernel swapped out 2,290 pages of other processes. Stopped by hand after 2 minutes. The later attempts were started from bash, at nice 0.
- The second was stopped by the host: unattended-upgrades reboots `debian` at 04:02 local time when an upgrade asks for it, and did so 8 minutes in. The reboot also put `perf_event_paranoid` back to 3, so no hardware counter can be opened by a user since: every counter in run B reads `unavailable: perf_event_open: Permission denied`. That is reported, not changed.
- The third was refused, after 13 minutes: **the day in memory does not fit on `debian` without swapping.** The driver hashes the day through a mapping, which fills the page cache with it, and then each `bench_replay` process copies the 7.68 GiB day into anonymous memory beside it. At each of the four process starts the kernel swapped out other processes' pages: 4, 1,590, 31,210 and 2,455, 35,259 in all (138 MiB), with `MemAvailable` never under 5.24 GiB, no page moving during a timed span, and every input page resident. The book fits; the copy beside a full page cache does not, on a host whose memory is shared with its services. So that mode was refused, as the brief asks, and the mapped mode kept.
- Run B: `pswpout` did not move in its 69 minutes, `MemAvailable` never fell below 12.54 GiB, every page was resident at both ends of every timed pass, every repetition completed, no migration was seen, and `machine_check` passed before and after.

**Run B, P-0011, 12302019, mapped, top tracked.** Three repetitions; every arm saw the top move after the same 124,767,189 messages, as on the Mac. Peak RSS 9.15 to 9.59 GiB from `getrusage`, the mapped file counted.

| Design | Messages/s | CV | Mean service ns | Batch mean ns a book message | p50 ns | p99 ns | p99.9 ns | Book memory MiB | Mac run B messages/s | Mac over `debian` |
|---|---|---|---|---|---|---|---|---|---|---|
| `paged-flat` | 5,092,461 | 0.06% | 196.37 | 199.54 | 130.1 | 800.8 | 1,411.9 | 662.8 | 19,335,339 | 3.80× |
| `hash-flat` | 4,268,175 | 0.24% | 234.29 | 238.07 | 200.2 | 830.8 | 1,552.1 | 225.5 | 15,408,545 | 3.61× |
| `paged-linked` | 2,732,821 | 0.09% | 365.92 | 371.83 | 140.1 | 4,267.8 | 18,786.6 | 648.3 | 11,070,036 | 4.05× |
| `hash-linked` | 2,469,451 | 0.34% | 404.95 | 411.48 | 220.2 | 4,368.0 | 19,347.2 | 211.0 | 9,626,384 | 3.90× |

The order is the Mac's. Flat levels are 1.73 times linked ones on the hash index and 1.86 times on the paged one; the paged index is 1.11 times the hash on linked levels and 1.19 times on flat ones. The flat arms' all-message p99.9 is 1.4 to 1.6 µs against 19 µs on the linked arms, which walk a deep side from the best, as on the Mac. Every arm runs 3.6 to 4.05 times slower than on the Mac; without counters, what that is made of is not measured here.

**Each target, from run B.** `debian` is a Ryzen Embedded V1605B: Zen 1 at up to 3.6 GHz, 512 KB of L2 a core and 4 MB of L3, a low-power embedded part. T-BOOK-1, T-BOOK-2, T-LAT-1 and T-LAT-4 were set on Zen 5 or Zen 3 desktop parts and T-LAT-2 and T-LAT-5 on Intel desktop and laptop parts, each with several times the cache and a faster core, so each of those misses below has the host as its reason. No bar is changed.

| Target | Bar | Measured, `paged-flat` | Verdict |
|---|---|---|---|
| T-BOOK-1 | ≥ 16.4 M msg/s, in memory | | not measured on `debian`: the in-memory run was refused for swapping |
| T-BOOK-2 | ≥ 12.5 M msg/s | 5.09 M; 2.47 to 4.27 M on the other arms | missed by 7.41 M msg/s on the best arm, by 10.03 M on `hash-linked`. Host: Ryzen 7 9700X against a V1605B |
| T-BOOK-3 | ≤ 1.4 GB of book | 0.69 GB; 0.22 to 0.69 GB | met on every arm |
| T-BOOK-4 | ≥ 14.4 M msg/s on 01302019 | | not measured: a Mac target on a day not held |
| T-LAT-1 | add 60, 447, 683; delete 70, 599, 768; replace 110, 709, 945; reduce 50, 548, 734 ns | add 90.1, 480.5, 720.7; delete 220.2, 740.8, 981.0; replace 360.4, 1,462.0, 2,594.1; reduce 140.1, 680.7, 890.9 ns | missed at every percentile of every operation, by 30 ns at add p50 up to 1,649 ns at replace p99.9; the linked arms miss replace p99.9 by 52.5 to 53.6 µs. Host: Ryzen 7 9700X |
| T-LAT-2 | p50 121, p99 552, p99.9 874 ns | 130.1, 800.8, 1,411.9 ns | missed by 9.1, 248.8 and 537.9 ns. Host: Core i5-12400 |
| T-LAT-3 | mean ≤ 393 ns, p99.9 ≤ 750 ns | mean 196.37 ns, p99.9 1,411.9 ns | mean met on three arms and missed on `hash-linked` by 11.95 ns (404.95); p99.9 missed on every arm, by 661.9 ns on the best and 18.0 to 18.6 µs on the linked arms. This bar holds on every host. A p99.9 span of 1.41 µs is about eleven DRAM round trips at the 123 to 127 ns the chase measured here, and what the slow messages wait on is not measured, since the counters are unavailable |
| T-LAT-4 | p50 100, p99 620, p99.9 1,080 ns less the pair; batch mean ≤ 142.36 ns | 110.9, 781.1, 1,392.7 ns; batch mean 199.54 ns | missed by 10.9, 161.1, 312.7 and 57.2 ns. Host: Ryzen 9 5950X on an isolated CCD with 32 MB of L3 |
| T-LAT-5 | p50 127, p99 446, p99.9 778 ns | 130.1, 800.8, 1,411.9 ns | missed by 3.1, 354.8 and 633.9 ns. Host: Core i7-13650HX |
| T-DESIGN-1 | ≥ 4.0× the map book | | not measured on `debian`: the map designs ran only in the refused in-memory run. On the Mac, 3.79× over `map-fresh` and 4.08× over `map-aged` |

The latency figures come from run B's latency pass, which reads the mapped file and checks the top outside each span, which is carteret's method for T-LAT-4. Every percentile here is above the 40.04 ns floor and resolved in all three repetitions.

**P-0010 against what ran.** Not held: the fit. The day in memory swapped other processes out, so no other part of P-0010 was tested, and it stands as written.

**P-0011 against what ran.** Held: every page resident and `pswpout` still, every repetition complete; the winner, `paged-flat`, at 5.09 million, inside the 4 to 9 million predicted; T-BOOK-2 missed by every arm; the arms in the Mac's order; 124,767,189 moves of the top in both passes of every repetition; peak RSS 9.15 to 9.59 GiB, inside 9.0 to 9.8. Not tested: each arm's share of its P-0010 rate, since P-0010 published nothing. The P-0010 estimate behind the 4 to 9 million was wrong in its reasoning: every arm is 3.6 to 4.05 times slower than on the Mac, not 2.5 to 3.5.

### The default arm

**`FastBook<>` is now the paged index with flat levels.** The measurements decide it, as the provisional default said they would:

- **Speed.** It is the fastest arm on `debian`, the headline host, 1.19 times `hash-flat` and 2.06 times the old default, `hash-linked`. The Mac agrees: 1.24 times `hash-flat` with the day in memory and 1.25 times mapped with the top tracked. It has the lowest p50, p99 and p99.9 on `debian`, and on `debian` the old default misses T-LAT-3's mean budget, at 404.95 ns against 393.
- **Memory.** It holds 662.8 MiB against 225.5 for `hash-flat`, 2.9 times as much. Nearly all of the 437 MiB difference is the paged index's capacity, 418 MiB of page slots and a 16 MiB directory, sized up front from both days' peaks with a quarter more, and charged whatever the day. That is 0.69 GB against T-BOOK-3's 1.4 GB, so the bar is met with room, and on `debian` the whole run with the mapped day peaked at 9.59 GiB of 15 GB. Memory would decide it on a host where 0.44 GB more cannot be spared, and there `hash-flat` gives 84% of the speed on `debian` (80 to 81% on the Mac) for a third of the memory. It is one template argument away.
- **What the default costs elsewhere.** A default-capacity book now takes 0.44 GB the moment it is built, however few orders it will hold. The tests that build `FastBook<>` with a handful of orders now give it 4 pages and a reach of 2^16 as well, and the tools' default arm, which follows `FastBook<>`, takes about 1.6 GB beyond the mapped file for the oracle on a real day, not 0.7. A day whose references pass 2^30, or that needs more than 428,032 pages at once, falls back to a hash table and counts every such insert (DS-72): slower, never wrong.
- **No new oracle run.** Every arm already passed the full-day oracle, T-ALLOC-1 and the trade checks on both days, line for line the same (above), so the switch changes no verdict.

**What the switch touched.** The default template arguments and the comment above them. `tests/test_book.cpp` keeps the default first in its scenario list, now `paged-flat`, with `hash-linked` named beside the others, and its hash-only test names `hash-linked` instead of the default. The map designs' warm-up in `bench_replay` was already named as `hash-linked` in bfdd4bb, before `debian` ran, so it stays the same arm whatever ships. Five faults that break the linked arm or the arm a tool runs had relied on the default being linked: `oracle-bbo-stale`, `oracle-off-bbo` and `oracle-off-touched` now run `itch_oracle --index hash --levels linked` (on the flat arm the moved compare catches `oracle-off-touched` first, which is a catch, but not the one it exists to show), `trades-src-book-best-is-the-second-level` runs `itch_trades` on that arm, and `test_trades_cli` now checks both ways round that the requested arm runs: a tiny flat arena refuses adds on `hash-flat` and not on `hash-linked`. `trades-cli-arm-ignored` was caught only by the first half while linked was the default; it is now caught by the second.

**Left open, and the next experiments.**

- **The in-memory mode on `debian`.** The copy needs the file's pages and its own at once. Reading the day in pieces and dropping each piece's cache as it is copied (`POSIX_FADV_DONTNEED` per piece, as the copy already does for the whole file once it ends), with the driver's hash and the residency check not leaving the whole file cached first, would hold the peak near one piece. Then P-0010 can run again for T-BOOK-1 and T-DESIGN-1 on the headline host.
- **Counters on `debian`.** They need `perf_event_paranoid` back at 2, which only the user can set, and it resets on every reboot. Then a counter pass, which runs in its own untimed process, can say what the 3.6 to 4.05 times is made of.
- **The driver writes no run-log row when it is signalled.** `forward_signal` exits straight away, so the three stopped attempts' rows were typed by hand. It should append the row first.
- **A 4 ms worst span on every arm.** The max is 3.84 to 4.02 ms on every arm, and was 4.02 ms on the stub book too, with p99.99 at 2.8 µs on `paged-flat`. A periodic event on core 4 is the likely cause; it is not traced.

## The backtester, 2026-11-03

The core is `include/itch/backtest.hpp` (`itch::backtest::Engine<Book = FastBook<>>`) and `include/itch/fees.hpp`. No strategy is in it beyond what the tests need: a strategy is any type with `on_market`, `on_report` and `on_wake` taking a `Context`, which gives it the time, a view book, and send, cancel, replace and wake. Nothing here ran on `debian`, and no time from it is reported.

**Design.** The feed is replayed through an exchange-side book exactly as a plain book replays it. Simulated orders never enter that book. Five kinds of event share one time axis, and at equal times run in this order: the real message at its ITCH timestamp t; a simulated order, cancel or replace arriving at decision + L_compute + L_oe; the message reaching the strategy at t + L_md; a report reaching it at exchange time + L_resp; a wake. Each kind is a FIFO ring sized in the constructor, so nothing is sorted and nothing allocates after construction. The strategy reads a second book built only from deliveries, so it sees the market L_md late, and has no handle to the exchange-side book or the engine: static assertions in `test_backtest.cpp` show that a `Context` reaches neither and cannot be built by a strategy. Base latencies are BENCHMARKS.md's 25 µs each way and 5 µs of compute, all in `Config::latency`.

**The limit: no market impact.** A replayed feed cannot react. Every real order arrives, trades and leaves as it did on the day, whatever the simulated orders do: a simulated fill takes nothing off the real book, a simulated quote draws no one in, and a race a real participant lost is not in the feed. The results say how simulated orders would have queued and filled against the day as it happened, for orders small enough not to have changed it. The same statement heads `backtest.hpp`.

**The queue rule.** Every real order on a traded symbol gets an arrival number when it is added; a simulated order gets one when it arrives, from the same counter. "R is ahead of S" means R's number is lower. `ahead` is set once, at arrival, to the shares resting at S's price then. After that it only falls, by the shares of an execute, cancel, delete or replace of an order ahead of S at S's price. A cancel behind S never moves it. An execution of R reaches S when S's price is better than R's, or equal with R behind S; S then fills at its own price, within that execution's shares. If S is reached while `ahead` is not zero, that is a queue anomaly: counted, and nothing fills.

An example, with L_compute 10 ns and L_oe 50 ns. The bid at $10.00 holds R1 100 and R2 100. The strategy sends S, buy 100 at $10.00, at 2,000; S arrives at 2,060 with `ahead` 200. R3 adds 100 at 3,000, behind S. R3 is deleted: `ahead` stays 200. R1 is cancelled 40: 160. R2 executes 100: 60. R1 executes its last 60: 0. R4 adds 100 behind S, and a sell executes 50 of R4: R4 is behind S, so the seller would have met S first, and S fills 50 at $10.00 as maker. The 50 shares of that execution are spent, so no other simulated order fills from it.

**The ledger.** `consumed[R]` counts R's shares a simulated order already traded against, as a taker or when R crossed a resting S. A later taker sees only R's shares less `consumed[R]`; a later execution of R draws on `consumed[R]` first, since those shares were already gone in the counterfactual. With the per-execution budget, no real share fills two simulated shares. S28 and the new `S28AnExecutionDrawsOnConsumedSharesFirst` show both halves.

**Fees.** Picodollars (10^-12 $) in `int64_t`, worked in `__int128`, one breakdown per fill: exchange fee or credit, Section 31, TAF, CAT. Each part over a quarter of `INT64_MAX` (about $2.3M) is refused, not wrapped. The base tier, as BENCHMARKS.md states: remove $0.0030; add credit $0.0020 on Tapes A and B and $0.0015 on Tape C for 20191030 and 20191230, $0.0018 and $0.0013 for 20260612; under $1, remove 0.30% of notional and no credit (20260612 outside regular hours: 0.15% remove, 5 bp credit); Section 31 $20.70 per million on sells ($20.60 in 2026); TAF $0.000119 a share sold, capped at $5.95 a trade ($0.000195 and $9.79 in 2026), applied per fill; CAT $0.000001 a share in 2026 only. A day with no schedule refuses every order (`closed`). The sources are in the header of `fees.hpp`.

**The tape, and what the spec says.** The ITCH 5.0 spec (April 28, 2023, p. 5) defines Market Category as the listing market: Q Nasdaq Global Select, G Nasdaq Global Market, S Nasdaq Capital Market, N NYSE, A NYSE American, P NYSE Arca, Z BATS Z, V Investors' Exchange, space not available. It says nothing about tapes. The tapes come from Regulation NMS, by listing market (SEC release 89 FR 81693, FR Doc. 2024-21867): "Tape A for securities listed on the New York Stock Exchange", "Tape B for securities listed on exchanges other than NYSE and Nasdaq", "Tape C for securities listed on Nasdaq". Together they give carteret's mapping exactly: Q, G, S to C; N to A; A, P, Z, V to B. A space, and the M seen in 2025 files (DS-64), map to no tape, and such a symbol is refused with `unknown_tape`, never guessed.

**Validity.** All 48 scenarios are tests with hand-written fills: `test_backtest` (44 tests), `test_fees` (7), `test_backtest_validity` (9). The lookahead tests: prefix invariance (S42, three seeds, 20 cuts each), future perturbation (S43), and an oracle strategy that peeks 100 µs ahead, caught at 20 of 20 cuts by each (S44). Two engine lookahead faults, market data delivered without L_md and the strategy reading the exchange-side book, are caught by S15, which mirrors the view book at exactly t + L_md. S46 hashes the replayed book after every message against a plain book and an idle engine, then compares whole books. S47 gives the same event-log hash on repeat. S45: over 32 seeds, a random taker's mean net is −2.2 × 10^14 pico$ and the oracle's +1.27 × 10^15, the oracle up on all 32. `test_alloc` sees 0 allocations over 228,531 messages and 20,111 fills.

**Faults.** `scripts/faults/backtester.json`, 22 faults: the queue rule (3), the ledger (4), latency order (3), lookahead (2), replay identity (2), a non-printable fill, fees (4), allocation (2) and the tool's traded check. All 22 are caught. The first run caught 20: the ledger draw could be removed with every test still passing, because no test executed shares a simulated taker had consumed while another simulated order stood ahead of them. That test is added. The tool's blind-check fault first failed to compile (`-Wtautological-compare`) and was rewritten. No new decoder or parser was written, so there is no new fuzz target.

**The 12302019 smoke.** `itch_backtest data/12302019.NASDAQ_ITCH50 --date 20191230` under `heavy.sh` and `/usr/bin/time -l`, at 8c05843, with the trivial strategy in `tests/backtest/joiner.hpp` on AAPL, MSFT, SPY and TSLA: 268,744,780 messages, 857,080 decisions, 346,265 fills (30,711 maker, 315,554 taker, 10,927 by crossing adds), 0 allocations after construction, no refused message, no ring overflow, no fee error, and the replayed book byte for byte equal to a plain book. Peak RSS 9,805,348,864 bytes, the mapped day counted; peak footprint 1,558,011,128 bytes. Repeated at 9d1108b, after the clang-tidy changes: every line the same, event hash `57fa4e60511e29e0` both times, peak footprint 1,557,994,744 bytes. No timing is reported: it is not a benchmark.

**Queue anomalies on the real day.** That run counted 1,347, on 810 simulated orders, all in state T, all at S's own price. A scratch build that printed each one (not committed) found `ahead` equal to an independent recount from the real L3 book in all 1,347: the engine's state is right, and in each case the feed executed an order that joined the level after S while orders that joined before S still rested there, 116 times with only odd lots ahead and 1,231 with round lots. Strict time priority among displayed orders does not explain that, and the cause is not found. The engine stays conservative and fills nothing on these. It is open in TASKS.md, to settle before T-BT-1 reports fill rates. Settled in "Queue jumps" below: they are orders held for the open and shown late, and printable `C` messages at an improved price.

**What didn't work.**

- The first oracle peeked too short a distance ahead and was caught at only 5 of 20 cuts. A 100 µs peek is caught at 20 of 20, and the test now asks for exactly 20.
- The validity tests hung twice: a strategy re-sent on every `closed` reject, and a strategy that always re-arms a wake kept `finish()` running for ever. `finish()` now runs wakes only up to the clock plus one full round trip of latencies, then closes the session; sends after that are refused and counted (`after_end`).
- The first view-book mirror for S15 reported 598 mismatches: a same-nanosecond burst reaches the strategy one message at a time, and the mirror applied the whole burst at once. It now applies up to each delivery's sequence number.
- A test fixture named `Run` hid gtest's `Test::Run`; it is `Replay`.
- `before()` first looked up arrival numbers only on symbols with a resting simulated order, so the ledger draw was missed on others. It now looks them up on every traded symbol.
- The first full `scripts/check.sh` failed format, the gates (an `EventHash::value()` read as a `.value()` on wire content) and clang-tidy, on code already committed: those commits built and passed their tests but not every check. The fixes are in later commits; the strategy-calling entry points (`feed`, `advance`, `finish`, `run`) are no longer `noexcept`, since a strategy may throw.
- The fault `check-release-stage` flips the wire's byte order and expects the release stage to fail. It timed out instead: `test_backtest` and `test_backtest_validity` hung, because a strategy that always sets a wake runs one event per wake period of simulated time, and garbled timestamps put about 10^12 ns between messages. The engine was not looping. Both tests now have a 60 s ctest timeout, and the fault is caught in 71 s.

## The day in memory on `debian`, the counters, and the driver's own rows, 2026-11-04

**Why the in-memory mode swapped.** P-0010's third attempt was refused for swapping other processes out (above). The cause was not the copy alone: `Arena::make` touched every page of the 7.68 GiB arena before the first read, so the whole copy was charged while the file, which the driver's hash had just read through a mapping, still filled the page cache. Then `bench_replay` dropped the file's cache only after the copy, and the next process read the day back into the cache beside its own fresh arena. Each process start asked for about two days of memory at once on a host with 15 GB.

**The fix.** `Arena::make` takes `Prefault::by_caller`, which leaves each page to the caller's first write. `read_into_memory` reads the day in pieces of 64 MiB into such an arena, so each page is faulted in by the read that fills it, and on Linux drops that piece's page cache with `posix_fadvise(POSIX_FADV_DONTNEED)` as soon as it is copied, then the whole file's once more for whatever read-ahead left. The copy grows by a piece while the cache shrinks by one, so the two together hold about one day and one piece. That call is user-level and affects only this file's clean pages; nothing outside the process is touched, and no `drop_caches` or root is needed. macOS has no `posix_fadvise`, so there the cache is left to the kernel and the input description says so; the pieces and the unprefaulted arena are the same code on both. Every page of the copy is still checked resident at both ends of every timed pass. On the Mac a full-day untimed process in this mode, under `heavy.sh`, read the day in 14.4 s, found all 503,626 pages resident and measured the same `paged-flat` book, 694,960,632 bytes, as before.

**The hash is left alone.** The driver still hashes the day through a mapping and leaves it cached. `machine_check.sh` refuses unless every page of the input is resident, and that check stays. In the in-memory mode the first copy then drains that cache a piece at a time, so the file and its copy are never both whole; every later process reads the day from disk, with each piece dropped as it goes.

**A swap check in the driver.** The driver reads the kernel's `pswpout` from `/proc/vmstat` when the run starts and after every process, and refuses the run, with its row, if it has moved: by whatever process, since a run that pushes the host's services into swap does not fit there. Each results file states both counts. A host with no `/proc/vmstat`, macOS among them, is not checked, and its file says so.

**The driver writes its own row when signalled.** SIGTERM, SIGINT and SIGHUP now pass the signal to the process running, wait up to 5 s for it (`measure.sh` sends SIGKILL 10 s after its SIGTERM), write the run-log row with where the run was, and exit 128 plus the signal. Once the results files are being written a signal is passed on but stops nothing, so no file is left half written. The three typed rows of P-0010 are the last typed rows.

**Counter names.** Every file from `debian` says, in its counters section, that `cache-misses` counts L2 misses on Zen 1, `r0843:u` is a floor on DRAM demand fills and `branch-misses` is branch misses, and the raw event is published as `r0843:u (a floor on DRAM demand fills)`. The stub's file from P-0006 keeps the old name, since it is generated and was not run again.

**New file names for the mapped run.** `check_predictions.py` dates a results file by the commit that first added it, so P-0013 cannot reuse P-0011's file names without being called later than its result. P-0013's files are `bench_book_{design}_mapped_top_counters_debian_12302019.md`, and P-0011's stay as they were.

**Faults.** Six in `scripts/faults/bench.json`: an arena left to its writer touched whole when made, the swap count ignored, the swap count not read after each process, the old exit with no row, the row dropped, and the signal not passed to the bench. The first matrix run caught all six but reported two as the wrong signature: the tests read a run log that was never written and errored instead of failing. They now assert that the log exists first, and the bench area is 108 of 108. The per-piece drop itself cannot be shown failing on the Mac, which has no `posix_fadvise`; the swap check is what catches it on `debian`.

**Run A, P-0012: refused for one page.** All six designs with the day in memory, from 2810183, started 04:40 UTC through `measure.sh taskset -c 4`. The fix held: the day's own page cache was empty while the copies were made (`fincore`: 0 of 7.7 GB), `MemAvailable` never fell below 4.09 GiB, and repetition 1 ran `hash-linked`, `hash-flat`, `paged-linked`, `paged-flat` and `map-fresh` with `pswpout` still. During `map-aged`'s timed process the kernel swapped out one page, 4 KiB, of some process; at that sample `MemFree` was 220 MB beside 4.2 GB of other processes' page cache, with this process at 9.33 GiB peak RSS. The driver refused at the end of that process, wrote its own row, and nothing is published. Peak RSS by process in repetition 1 was 9.15 to 9.59 GiB timed and 7.90 to 8.34 GiB untimed; the largest was `paged-flat`'s, which did not swap. So the one page is not plainly the copy's: on this host the multi-generational LRU will move an idle anonymous page to swap while it is reclaiming page cache, as run C's first attempt showed below with no copy at all. It stays a refusal, as the brief asks. T-DESIGN-1 needs a map design in the same run, so it is still unmeasured on `debian`.

**Run B, P-0013: the fast arms mapped with counters, published.** From 2810183, started 05:41 UTC, 85 minutes. `pswpout` 35,296 at both ends, `MemAvailable` never below 12.33 GiB, every page resident at both ends of every timed pass, 124,767,189 moves of the top in both passes of every repetition, `machine_check` passed before and after, `perf_event_paranoid` 2 at both ends. Every counter was measured at running ratio 1.0000, and `rdpmc` agreed with `read()` to 0.0000%.

| Design | Messages/s | CV | P-0011 messages/s | Mean service ns | p50 / p99 / p99.9 ns | Book MiB | Peak RSS GiB | Cycles | Instructions | `branch-misses` | `cache-misses` (L2 misses) | `r0843:u` (DRAM demand fills, a floor) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `paged-flat` | 5,041,776 | 0.21% | 5,092,461 | 198.34 | 130.1 / 780.8 / 1,331.9 | 662.8 | 9.59 | 1,119.2 | 581.8 | 2.00 | 5.38 | 1.95 |
| `hash-flat` | 4,276,950 | 0.68% | 4,268,175 | 233.81 | 200.2 / 830.8 / 1,432.0 | 225.5 | 9.17 | 1,313.9 | 566.4 | 2.51 | 6.09 | 2.84 |
| `paged-linked` | 2,741,042 | 0.28% | 2,732,821 | 364.82 | 150.2 / 4,275.9 / 18,962.8 | 648.3 | 9.58 | 1,774.7 | 570.1 | 1.90 | 8.99 | 3.73 |
| `hash-linked` | 2,476,479 | 0.21% | 2,469,451 | 403.80 | 220.2 / 4,376.0 / 19,395.3 | 211.0 | 9.15 | 1,956.5 | 555.4 | 2.25 | 9.67 | 4.65 |

The counter columns are per message over `apply()`, through `rdpmc`, from the untimed process, whose fenced reads around each message add their own cycles and instructions: the cycles a message there are not the timed pass's, which is why 1,119 cycles at about 3.3 GHz is longer than the 198 ns the timed pass takes.

**What the counters say.** Every arm runs about the same instructions a message, 555 to 582, so the arms differ in waiting, not in work. Cycles a message follow the DRAM fills: 1.95 on `paged-flat`, 2.84 on `hash-flat`, 3.73 and 4.65 on the linked arms, and `cache-misses`, L2 misses, 2.1 to 2.8 times those. At the 123 to 127 ns a DRAM round trip took in the chase, two demand fills a message are about the 198 ns `paged-flat` spends, if they do not overlap; the linked arms' extra fills are the walk down a deep side. Branch misses, 1.9 to 2.5 a message, barely move between arms. This is what the 3.6 to 4.05 times between the Mac and `debian` is made of: the same instructions, with every miss going to a DRAM that the Zen 1 part's 512 KB of L2 and 4 MB of L3 put in front of far more of the book than the Mac's caches do. The Mac cannot count misses, so the Mac's side of that is inferred from its time, not measured.

**Targets from run B.** T-BOOK-2 missed by every arm: 5.04 M messages/s on `paged-flat` against 12.5 M, a gap of 7.46 M; 10.02 M on `hash-linked`. T-BOOK-3 met on every arm, 0.22 to 0.69 GB. T-LAT-1 missed at every percentile of every operation on `paged-flat`, by 30.09 ns at add p50 up to 1,448.94 ns at replace p99.9. T-LAT-2 missed by 9.13, 228.80 and 457.86 ns. T-LAT-3's mean met on three arms and missed on `hash-linked` by 10.80 ns (403.80); its p99.9 missed on every arm, by 581.86 ns on `paged-flat`. T-LAT-4 missed by 10.60, 141.26 and 232.93 ns, and its batch mean by 59.18 ns (201.54). T-LAT-5 missed by 3.13, 334.80 and 553.86 ns. Every bar but T-BOOK-3 and T-LAT-3 was set on a Zen 3, Zen 5 or recent Intel desktop or laptop part with several times the cache; that is the reason given for each, and no bar is changed. T-LAT-3 holds on any host, and its p99.9 is the deep-side walk and DRAM misses the counters show.

**P-0013 against what ran.** Held: every arm within 3% of P-0011 (−1.0%, +0.2%, +0.3%, +0.3%), the same order, CV under 1%, the moves of the top, `pswpout` still, every page resident, peak RSS 9.15 to 9.59 GiB; every event measured at 1.0000 and agreeing; `r0843:u` 1.95 on `paged-flat`, inside 0.5 to 2.5; fewer on each flat arm than on the linked arm with its index; `cache-misses` 2.1 to 2.8 times `r0843:u`, inside 1.2 to 4; `branch-misses` 1.9 to 2.5, inside 0.3 to 3; cycles higher on each linked arm than the flat arm with its index. Not tested: within 25% of P-0012's figure, since P-0012 published none; its one `hash-linked` untimed process read 4.64 against run B's 4.65.

**P-0012 against what ran.** Not held: the fit, by one page. `MemAvailable` stayed above 3.5 GiB (4.09). Nothing else was tested.

**Run C, P-0014: the fast arms in memory, never measured.** After run A's refusal, P-0014 (committed in 5496d0c) asked for the four fast arms alone with the day in memory, which needs no map design and would give T-BOOK-1. It was tried from 0ffe738 straight after run B, and the driver refused at its own `machine_check`: `input-resident` failed, 1,378,612 of 2,014,504 pages resident. The sync and the rebuild had just filled the page cache to 14.6 GB, and while the run script and the driver read the day back in, the kernel evicted part of it again and swapped out 1,222 pages of other processes, with no copy made and this run's own anonymous memory at 0.6 GB. That is the evidence above that page-cache churn alone moves `pswpout` on this host. Nine more attempts from 00:13 to 01:45 local time never ran: another session's benchmarks held `measure.sh`'s lock, each `machine_check` before the run waited its 10 minutes and gave up with 75, and at 01:55 the run script would not start a run that might cross unattended-upgrades' 04:02 reboot. Only the first attempt reached the driver, so it alone has a row. T-BOOK-1 stays unmeasured on `debian`.

**P-0014 against what ran.** Nothing was tested: the one attempt that reached the driver was refused before any repetition.

**The default arm with the day in memory.** Not settled on `debian`: no in-memory run of the fast arms finished there. In run A's first repetition, which is refused and not published, every fast arm completed, but its figures are not reported here. The default stays `paged-flat`, which wins mapped with the top tracked on `debian` (above) and in both modes on the Mac.

**Left open.**

- T-BOOK-1 and T-DESIGN-1 on `debian`. Run P-0014 again when no other session holds `measure.sh`'s lock, well before 04:02, and not straight after a sync and rebuild. For T-DESIGN-1, either accept that the host's own reclaim may swap a page while any large process runs, which is the user's call and not this note's, or make the map designs' run smaller: the latency samples, 1 GiB, are the one large thing the timed process holds besides the copy.
- The swap check counts every process's swap-outs. It cannot tell this run's pressure from another session's or from page-cache churn, and it is meant to be conservative: a refusal is cheap, a number taken while the host's services were being pushed out is not.
- The results file of P-0006, the stub's proof run on `debian`, still names the raw event the old way; it is generated and was not run again.

## CI's first runs on GitHub, 2026-11-04

The repo went public on 2026-11-03, and `.github/workflows/ci.yml` ran on GitHub for the first time. Until then it had only been read by actionlint and its commands run by hand (above, "CI, the comparison queues and the README").

**Run 1, at 2810183: three of five jobs failed, none of them in a test.** arm64 macOS with Clang and Emscripten passed. The other three never reached ctest, so none of the Linux jobs had yet run a test on a runner.

- **x86-64 Linux GCC and arm64 Linux GCC, at Build.** `tests/backtest/market.hpp`, the backtester tests' synthetic market, stored `std::mt19937` draws in `std::uint32_t` in three places, and `-Werror=conversion` refused them. The engine yields 32-bit words, but its `result_type` is `uint_fast32_t`, which glibc makes 64 bits wide and macOS 32, so the narrowing exists only on Linux. The run by hand on `debian` at b12d05e had passed because the synthetic market came later. Ninja stopped after the three objects that include it, at 53 of 99 steps, so the rest of the tree had not yet been built by GCC either.
- **x86-64 Linux Clang, at "Fetch the pinned LLVM".** `scripts/fetch_tool.py` gave `LLVM-23.1.4-Linux-X64.tar.zst` to `tar -xf`, and tar's zstd refused it: its frame asks for a 1 GiB window, and zstd decodes at most 128 MiB unless given `--long` or `--memory`. On `debian` the archive had only been hashed, never unpacked, so this had not been seen.

**The fixes.** `market.hpp` draws through `draw()`, which casts the engine's output to `std::uint32_t`. Every value an `mt19937` produces is below 2^32, so the cast changes nothing, and the stream is the one it was on every platform: on macOS the cast is the identity, and on Linux it removes only high bits that are always zero. The backtester's tests pass unchanged, and no committed result uses the synthetic market (`itch_backtest` does not). The other uses of an engine were checked for the same pattern: `test_alloc.cpp` and `test_backtest_validity.cpp` cast each `mt19937` draw or keep it in a `std::size_t`, a `std::uint64_t` sum, a `switch` or a comparison, which is the same on both widths, and `bench/chase.hpp` uses `mt19937_64` with a cast; nothing else in the tree uses `<random>`. `fetch_tool.py` now unpacks a `.zst` archive by running `zstd -d --long=31` itself and piping it to tar, and checks both exit codes; the hash is still checked before either runs. Three tests in `scripts/test_fetch_tool.py` build a zstd frame by hand whose header asks for a 1 GiB window as LLVM's does: plain `zstd -d` refuses it, `fetch` unpacks it, and a truncated one fails with zstd's exit code and leaves nothing. Two faults in `scripts/faults/ci.json` put the old `tar -xf` back and drop `--long=31`, and the test catches each.

**Run 2, at da1a707: every job green.** <https://github.com/jacksonbaxter/itch-orderbook/actions/runs/41814742970>. Each native job built all 99 steps of the release preset and passed 24 of 24 ctest entries: x86-64 Linux on g++ 14.2.0, x86-64 Linux on Clang 23.1.4 (the pinned archive, fetched and unpacked), arm64 Linux on g++ 14.2.0, and arm64 macOS on Clang 23.1.4. Emscripten passed its 1 of 1. No test in the tree calls `GTEST_SKIP`, so a pass is not a skip. Nothing new failed once the build got past the first errors, so the GCC and Linux paths the two fixes unblocked, among them the Linux-only code in `bench/platform` and arm64 Linux's `cntvct_el0` timer, build clean under `-Wall -Wextra -Werror` on both GCC jobs.

**What CI still does not run.** It builds the release preset and runs ctest. The sanitizers, clang-tidy, the fuzz smoke runs, the Python script tests and the fault matrix run in `scripts/check.sh` on the Mac, and nothing there runs on a runner. CI produces no benchmark numbers, and the runners' PMUs are not used.

**Pushing.** GitHub refused the first push of da1a707 twice with `Permission denied (publickey)` and once with an Internal Server Error, with the same key that `ssh -T git@github.com` was accepting and refusing in turn; the fourth attempt went through.

## Queue jumps, 2026-11-04

The 12302019 smoke counted 1,347 queue anomalies on 810 simulated orders (above, "The backtester"): executions that reached a simulated order S while `ahead` was not zero. T-TRADE-5 sees the same thing from the feed's side, as the executions that did not hit the front of their level's queue, 0.19% of 5.68 M on 12302019 and 0.08% of 7.24 M on 10302019. This settles what they are, on both days, and changes the backtester's rule to match.

**The tool.** `tools/itch_queue_jumps` replays a day through the default fast book and, for T-TRADE-5's population (every `E` while the market is open, System Event `Q` to `M`, on a symbol in state `T`, read before the book applies it), asks two questions of each: is the order at the front of its level's FIFO, which is the order of the adds, and does it have the lowest order reference resting at its level. It classifies every jump by what is ahead of it and what became of the front, counts late adds and late replaces over the whole day, and compares every printable `C` in the population with the price its order showed. `--strict` exits 1 unless the finding below holds. Both days, under `heavy.sh` and `/usr/bin/time -l`, from the tree committed as e9d1049:

| | 12302019 | 10302019 |
|---|---|---|
| `E` in the day | 5,722,824 | 7,316,703 |
| Population (T-TRADE-5's) | 5,683,178 | 7,235,306 |
| At the front of the FIFO | 5,672,613 | 7,229,242 |
| Jumps | 10,565 | 6,064 |
| At the lowest reference at its level | 5,683,178 | 7,235,306 |
| Outside the population, at the lowest reference | 39,646 of 39,646 | 81,397 of 81,397 |
| Jumps whose order is a late add | 10,565 | 6,064 |
| Jumps whose reference is below every order ahead | 10,565 | 6,064 |
| Jumps with the front added before the open | 8,705 | 4,551 |
| Jumps with only odd lots ahead | 4,856 | 2,289 |
| Front executed in the same nanosecond / later / removed first / never named | 6,565 / 3,603 / 397 / 0 | 3,258 / 2,561 / 245 / 0 |
| Symbols with a jump | 436 | 278 |
| Adds / replaces | 118,631,456 / 21,639,067 | 128,638,557 / 25,513,651 |
| Late adds, on how many symbols | 945,542 on 8,857 | 895,718 on 8,851 |
| First and last late add | 09:30:00.000995 to 09:30:39.737 | 09:30:00.000627 to 16:00:00.411 |
| Late adds before the open / late replaces | 0 / 0 | 0 / 0 |
| Printable `C` in the population | 91,402 | 122,813 |
| Of those, at a price better for the aggressor than shown (front / behind) | 12,884 / 78,518 | 24,955 / 97,858 |
| At the shown price, or better for the resting side | 0 | 0 |
| `--strict` | pass | pass |
| Peak RSS / peak footprint, bytes | 8,990,556,160 / 742,742,712 | 9,772,941,312 / 741,891,296 |

The jumps per symbol on 12302019 run MSFT 1,075, AAPL 651, TSLA 572, BYND 541, AMD 411, AMZN 402, ROKU 351, GOOGL 244, NVDA 234, FCEL 184; on 10302019 AAPL 592, TSLA 334, AMD 313, MSFT 285. The jumps are T-TRADE-5's remainder exactly: 5,683,178 − 5,672,613 = 10,565 on 12302019 and 7,235,306 − 7,229,242 = 6,064 on 10302019.

**What the feed says.** Every execution in both days, inside the population or not, hit the lowest order reference resting at its level. The ITCH 5.0 spec (p. 12) defines the Order Reference Number as "The unique reference number assigned to the new order at the time of receipt", so the reference is the exchange's own receipt order, and the feed's add order is not. A late add is an `A` or `F` whose reference is below the highest its symbol has shown, an order received before one the feed showed first. Every jump was of a late add whose reference was below every order ahead of it in the FIFO: the order was ahead in time priority all along, and only shown late. Every late add on 12302019 came in the 40 s after 09:30:00, none before, and on 10302019 also 4 at 16:00:00.41, at the closing cross. None came before the open and no replace ever came late, so a `U`'s new order is always shown in order.

**What rule it is.** These are orders held for the open, shown when the symbol's opening cross releases them. Nasdaq's rules (Equity 4, from a capture of 2024-12-11; the 2019 text of these paragraphs is not in the cache): Rule 4703(a)(5), an order "designated to activate at any time during Market Hours" has a Time-in-Force of "Market Hours Day" or "MDAY"; Rule 4752(c), "System securities in which no Nasdaq Opening Cross occurs shall begin trading at 9:30 a.m. by integrating Market Hours orders into the book in time priority"; and Rule 4757(a)(1)(B), "Equally priced Orders with a Display Attribute will be ranked in time priority". An MDAY order entered before the open is received, and given its reference, then; it reaches the book at the open with that time priority, ahead of orders received after it that were shown before it. In 8,705 of the 10,565 jumps on 12302019 the front was itself added before the open, and in 6,565 the front executed in the same nanosecond as the jump, the sweep that took both. The 397 whose front was removed before it executed were cancelled or deleted.

**Printable `C`.** The other 1,309 of the smoke's 1,347 anomalies were printable `C` messages, Order Executed With Price. In both days every printable `C` in the population traded at a price better for the aggressor than the order showed: the order's executed part was not displayed at that price. Rule 4757(a)(1)(C) ranks "Orders with a Non-Display Attribute, including the Non-Displayed portion of an Order with Reserve Size" in time priority behind displayed ones at a price, so such an execution is not evidence about the displayed queue at the shown price.

**Ruled out.** A replace that re-entered the queue: no jump hit an order made by a `U`, and an exploratory count on 12302019 found all 345,214 executions of `U`-made orders in the population at the front. A reserve refresh: it gets a new and higher reference, so it would sit at the back, as the feed shows. Retail Price Improvement orders: non-displayed, with no add on the feed. Odd lots: 4,856 jumps had only odd lots ahead and 5,709 had round lots, and the rules give odd lots no priority of their own.

**The decision.** The backtester now ranks by receipt, as the feed does:

- **A late add goes ahead.** In `number_real`, an add on a traded locate whose reference is below the highest that locate has shown gets arrival number 0, ahead of every simulated order, and its shares join `ahead` of every simulated order resting at its price on that side. That is exact for a simulated order that arrived after the open, which is every order the strategy can send from a delivery, and conservative for one that arrived before the open, since a held order received after it would in truth be behind it. `Stats::late_adds` and `late_adds_ahead` count them.
- **A printable `C` reaches only its trade price.** An execution of R reaches S only if S's price is at or better for the aggressor than the price traded, `c.execution_price` for a `C` and R's price for an `E`. A `C` at an improved price takes nothing from a simulated order resting at R's shown price. That is conservative: it may miss a fill the real book gave, and never adds one.
- **Unchanged.** `ahead` is still never raised by anything else, and a reach with `ahead` above zero is still an anomaly that fills nothing.

The old rule was not conservative on these. With `ahead` at zero, a late add executing behind S filled S, which the feed says was behind the late add; and a printable `C` filled S at its shown price when the trade was at a better one.

**Tests and faults.** S49 (`QueuePosition.S49LateAddGoesAhead`, `S49LateAddDoesNotFillWhatIsAhead`) and S50 (`ItchEdges.S50PrintableCReachesOnlyItsPrice`) are hand-built: a late add of 50 shares raises `ahead` from 100 to 150, a late `F` at another price changes nothing, the recount agrees, and the executions fill only once `ahead` is spent; a printable `C` at $10.01 fills the order at $10.01 and not the one at $10.00. `tests/test_queue_jumps_cli.py` builds a stream with one jump and one printable `C` and works every line of the tool's output out by hand; four broken streams each fail `--strict`. Seven faults in `scripts/faults/backtester.json`: a late add left behind, a late add not counted ahead, a printable `C` reaching its shown price, the tool counting the FIFO front as the lowest reference, the tool never seeing a late add, and `--strict` blind to a late replace or to a `C` at its shown price.

**The smoke again.** `itch_backtest data/12302019.NASDAQ_ITCH50 --date 20191230` under `heavy.sh` and `/usr/bin/time -l`, from the tree committed as e9d1049: 268,744,780 messages, 857,009 decisions, 346,135 fills (30,581 maker, 315,554 taker, 10,929 by crossing adds), 0 queue anomalies, 52,373 late adds on the four symbols of which 46 joined ahead of a simulated order, 6,331 hidden prints at a simulated price, 0 allocations after construction, every check passing, event hash `c3fa26520a4e65ba` (was `57fa4e60511e29e0`). Before the change, with the same strategy: 1,347 anomalies and 30,711 maker fills. Peak RSS 9,805,889,536 bytes, peak footprint 1,558,519,032 bytes.

**What is left.** A held order whose reference is above every one its symbol had shown is not late by this test, and lands behind a simulated order that arrived after the open; that would be optimistic for the simulated order. The feed cannot show it: such an order looks the same as one received after the last order shown. Late adds were released over 40 s on 12302019, so a simulated order sent in that time can meet one. The count of late adds ahead of a simulated order, 46 on the smoke, is reported with every run.

**What didn't work.**

- The first version of the tool asked only whether each jump's order was below the front's reference. That held for every jump but said nothing about the executions at the front, so it now checks every `E` against the lowest reference at its level.
- With the late-add rule alone the smoke still counted 1,309 anomalies. A probe that counted each by message type found every one was a printable `C`, which led to the second rule.
- Two fault mutations first failed to build under `-Werror` (an unused `shares`, an unused `traded`), and the fault for `--strict`'s `C` condition first removed a variable; each now changes an expression.
- The gates' home-path check failed on the worktree's `.git`, a file naming the main repository's path. The walk now skips `.git` by name, file or directory, with a test.
- The first full `scripts/check.sh` on this branch failed only clang-tidy, on `itch_queue_jumps.cpp` as committed in e9d1049: nested conditional operators, a missing pair of parentheses, and `fopen` without the owning-memory suppression the other tools carry. The fix changes no output, and every other stage passed, the fault matrix at 594 of 594.

## T-BT-1 and T-BT-3 on the Mac, 2026-11-04

**The harness.** `bench/bench_backtest.cpp` times the engine's `run()` over a whole input, frame walk, both books, the queue model, every engine event, the strategy and `finish()` inside one span, after an untimed warm-up on a fresh engine and strategy; the two passes must agree on every count and the event hash, every page of the input must be resident before the warm-up and before and after the timed pass, and a run with a refused message, an overflow, a fee error or no fill is refused. `scripts/backtest_driver.py` runs 3 fresh processes, refuses if they disagree on any count, and writes the results file, which `check_results.py` holds to its own rules: what one event is and the denominator, the target's verdict against the summary's median, T-BT-1 never called met or missed on the Mac, and the queue anomaly count. Two workloads in `bench/backtest_workloads.hpp`:

- **quoting, T-BT-1.** The whole day mapped, base latencies, a quoter on AAPL, MSFT, SPY and TSLA that joins the view book's best bid and ask with 100 shares, follows each with a replace, stops a side at 1,000 shares of inventory and stands aside while the view book is locked or crossed. Not Avellaneda-Stoikov.
- **shadow, T-BT-3.** queuetruth's workload: before any timing, each process walks the whole day twice, to count and then copy the four symbols' messages and every System Event into anonymous memory, and only that extract is replayed. 100,000 orders of 100 shares at evenly spaced times over the regular session, round-robin over the symbols and sides, each at the view book's best on its side, each cancelled 60 s later, every latency zero.

**What an event is.** In T-BT-1 an event is one ITCH message of the day through the engine: the denominator is all 268,744,780, every symbol's, though the strategy sees only the 5,425,845 deliveries of its four symbols and the System Events; the other 98% cost the exchange-side book and a locate check. LobSim's op is one event of a synthetic L3 feed through replay and a market-making strategy, which may see every event, so the two rates count different events on different feeds. The engine-events rate, which adds deliveries, arrivals, reports and wakes, is 1.02 times the message rate. In T-BT-3 an event is one message of the extract, 5,425,845, against queuetruth's 5,425,849 ITCH messages; queuetruth's rate is over 6,103,262 decoded records, and T-BT-3's 4.61 M is that rate over messages. Neither side times the full day: queuetruth reads a Parquet file of the four symbols, and here the extract is made before the warm-up, in 1.84 s a process, reported and excluded.

**Run P-0101, T-BT-1.** From 4fa6701, `measure.sh /usr/bin/time -l` with `--workload quoting`, 11:33 to 11:35 UTC: median 16,792,852 messages/s (16,474,364 to 17,167,582, CV 2.06%), 59.55 ns a message, 16.0 s a timed pass; engine events 17,185,873/s. Against the 2.0 M bar, +14.79 M, on another host and event, so "host differs". The plain `paged-flat` book replays the same mapped day at 19.34 M (P-0009), so the engine, with the queue model and the strategy on four symbols, costs 1.15 times a plain replay. Every repetition gave the same counts: 412,923 arrivals, 38,022 fills (37,722 maker, 300 taker, 8,676 against a crossing add), 0 queue anomalies, 39 late adds ahead of a simulated order, 5,068 hidden prints at a simulated price, event hash `e91233bf30571f8e`. Peak RSS 9,145,204,736 bytes (8.52 GiB) from `/usr/bin/time -l`, the mapped day counted; each repetition's own `getrusage` the same, 8.52 GiB; the driver's own footprint 18,422,000 bytes.

**Run P-0102, T-BT-3.** From 4fa6701 with `--workload shadow`, 11:35 to 11:36 UTC: median 7,804,654 messages/s (7,774,466 to 7,809,622, CV 0.24%), 128.13 ns a message, 0.695 s a timed pass; engine events 16,661,541/s. T-BT-3 met, 1.69 times the 4.61 M bar, on an M4 Max against queuetruth's M1 Pro in Low Power Mode. 100,000 placed and none skipped, 100,000 cancels, 200,000 wakes, 131,519 fills of 7,531,024 shares, 61,990 of them against a real add that crossed a shadow order resting where the real level had gone, which is the replay's lack of market impact, 0 queue anomalies, 44 late adds ahead, event hash `06cbd8541ec86564`. A message costs about twice T-BT-1's because every one is on a traded symbol: it is applied to the exchange-side book and, with no latency, at once to the view book, numbered for the queue, and delivered to the placer. Peak RSS 9,308,012,544 bytes (8.67 GiB): the extract reads the whole mapped day.

**P-0101 against what ran.** Held: the rate, 16.79 M inside 12 to 22 M and near the 16 M expected; at most 1.3 times slower than the plain book (1.15); engine events 1.02 times the message rate; CV under 3%; identical counts and hash in every repetition; 268,744,780 messages and 5,425,845 deliveries; 0 queue anomalies; 39 late adds ahead, under 200. Not held: fills, 38,022 against 100,000 to 2,000,000, since the quoter only joins and never takes, where the smoke's joiner also took every 16th message; and peak RSS, 8.52 GiB against 8.8 to 9.8, since the smoke's 9.13 GiB included a second, plain book for its identity check.

**P-0102 against what ran.** Held: the extract's 5,425,845 messages, 4 fewer than queuetruth's; the rate, 7.80 M inside 4 to 12 M and near the 7 M expected, so met; CV under 5%; the extract in 1.84 s, inside 1 to 10; identical counts and hash; 0 queue anomalies; 100,000 placed with none skipped, each cancelled once; 131,519 fills, at least 10,000; peak RSS 8.67 GiB, inside 8.5 to 10.

**Left open.** T-BT-1 on `debian` (TASKS.md). The 4 messages between the extract and queuetruth's count are not traced; its count was taken from its results page, not reproduced.

## The market maker, 2026-11-04

Avellaneda-Stoikov as the demo, GLFT's stationary form as a labelled variant, the attribution of T-MM-1 with property tests 1 to 16, T-MM-2 by Monte Carlo, and a calibration tool tested on a hand-built day. No strategy has run on a real day, and nothing here chose symbols, γ, h or the order size.

**Units.** Prices are Price4 (10⁻⁴ $). A mark is mid2 = best bid + best ask, an integer in units of 5·10⁻⁵ $, so a mid never needs a half. Money is picodollars in `int64_t`, worked in `__int128`: one Price4 is 10⁸ pdollars, one mid2 unit 5·10⁷. Every component is refused beyond INT64_MAX/4, so that sums of four never overflow. Doubles appear only inside the quoting models; everything from the rounding of a target to a price on is an integer.

**The attribution as implemented** (`include/itch/mm/attribution.hpp`). For fills i with signed quantity qᵢ, price pᵢ and fee fᵢ (positive paid), opening position Q₀ and window [t₀, T]; m(t) is the last mark at or before t, m(t−) the last strictly before, and mʰᵢ = m(min(tᵢ + h, T)):

- S = Σ qᵢ(m(tᵢ−) − pᵢ) − Σ fᵢ, gross spread and the fee sub-line reported apart;
- A = Σ qᵢ(mʰᵢ − m(tᵢ−));
- I = Q₀(m_T − m₀) + Σ qᵢ(m_T − mʰᵢ).

The path form computes A and I a second way: one walk over the mark's jumps up to T, A = Σ Δm·Y and I = Σ Δm·O, where Y is the position from fills at or before the jump whose window has not closed and O is Q₀ plus the fills whose window closed before it. It shares only the input checks with the per-fill form. The ledger (`ledger.hpp`) keeps cash and position from the same fills and values the end at m_T, and `attribution.hpp` never includes it: a gate, `attribution-without-ledger`, holds that header and every header it includes to never include or name the ledger. S + A + I equals the ledger's total with `==`. The sum telescopes, so a changed mark inside the session is invisible to it; the path form is what catches that (test 14's second case).

**Property tests, all 16 passing.** `tests/test_attribution.cpp` runs 1 to 14 over generated cases (a 50 ms clock, 10-minute windows, Q₀ within ±1,000, up to 40 fills with fees, marks moving ±100 or ±200 mid2) and the horizons 0, 50 ms, 1 s, 15 s, 60 s, 300 s and past the close: 1 sum, 2 two forms, 3 h invariance, 4 past T, 5 split, 6 price shift, 7 mirror, 8 effective = realized + impact, 9 flat mark, 10 round trip, 11 flat end, 12 truncation at T, 13 rebates off (the 20191230 fee schedule with add credits set to zero, against the same run with them), 14 the negative test: deleting one fee, deleting one fill that contributes, or changing m_T when Q_T ≠ 0, in only one of the two computations, each makes test 1's check fail, in both directions. The evidence's worked example is a test of its own, S +2.68, A −0.80, I −0.20, Π +1.68, with its per-h table. `tests/test_market_maker.cpp` runs 15 and 16 through the backtester: 15 replays one synthetic day under every combination of latency (base and zero), model (AS and GLFT) and γ (0.01 and 0.1), and every run's mark path hash equals that of a plain replay of the same stream; 16 runs one configuration twice and compares the reports byte for byte. Test 15 uses the switches the engine has: Q-off, a fill rule without the queue, does not exist yet, so the four evaluation runs will need it added and test 15 extended. Each test has a fault in `scripts/faults/marketmaker.json` that it catches.

**The models** (`quoting.hpp`), in ticks and seconds. AS: r = s − qγσ²τ and ψ = γσ²τ + (2/γ) ln(1 + γ/k), τ = T − t, q in lots. GLFT stationary: the smallest eigenvalue λ₀ of the tridiagonal matrix with diagonal αq² and off-diagonal −η, α = (k/2)γσ², η = A(1 + γ/k)^−(1 + k/γ), q from −Q to Q, by Sturm-sequence bisection on [−2η, 0]; the ground state's ratios fₖ/fₖ₊₁ by its recurrence, run inwards from q = Q; then δᵇ(q) = (1/γ) ln(1 + γ/k) + (1/k) ln(f_q/f_{q+1}) and δᵃ(q) = δᵇ(−q). GLFT's closed-form approximation is kept beside it for comparison. With Q = 1 the exact quotes match a hand solution of the 3×3 problem.

**The strategy** (`strategy.hpp`). `Quoter<Model>` on the backtester's strategy interface: from the view book's mid, the model's bid target rounded down and its ask rounded up, then held one tick off the other side so it never takes. An order is replaced only when its rounded price moves. The inventory bound counts what an order already out can still fill: the engine's replace restores the full size, so a replace needs room for the remainder and a new lot, and where there is none the order is cancelled. The fill log is sized in the constructor, and `test_alloc` shows no allocation after it for both models. `marks.hpp` records the mark path from the exchange-side book after each real message, never from the strategy's late view book, so the path is the same in every run of a switch matrix.

**T-MM-2** (`tools/itch_as_tables.cpp`, `scripts/as_tables_report.py`, prediction P-0201 committed in 0172a56 before any path ran). Run P-0201-20261105T053946Z from 9fee6ab: `python3 -B scripts/as_tables_report.py run --tool build/release/tools/itch_as_tables --raw <raw> --prediction P-0201`. Average spread 1.4918, 1.3490 and 3.0317 against the paper's 1.49, 1.35 and 3.02: the first two match at the paper's two decimals and γ = 1 gives 3.03, so the verdict is FAIL. The reason is the time grid, as predicted: quoting at t = n·dt for n = 0 to 199 makes T − t average 0.5025, not 1/2, and at γ = 1 the slope γσ² = 4 turns the 0.0025 into 0.01, across a rounding boundary. Quoting also at t = T, 201 points, gives 3.0217, which rounds to 3.02; the paper does not state its grid. The committed grid is the measurement; the other grids are a labelled sensitivity. Profit, final q and their standard deviations: 23 of 24 within two standard errors of the difference plus half the paper's last digit; outside, Table 1's symmetric final q, −0.489 against 0.26, z = −2.03. `check` reruns the tool and requires the file's body to be what the rerun renders; the quick stage `market_maker` of `scripts/check.sh` runs it beside the attribution, model, allocation and calibration tests. The simulation is in doubles through `exp` and `log1p`, so the file holds on the host and compiler that wrote it, which its header names.

**Calibration** (`include/itch/mm/calibration.hpp`, `tools/itch_calibrate`). Built and tested on a hand-built day; not run on a real one. In regular hours, while the mid is valid (state `T`, both sides, bid ≤ ask), changes within a nanosecond collapsed, τ the tick:

- σ_clock = sqrt((1/n)·Σ(Δᵢ − Δ̄)²/S), Δᵢ the change of mid in ticks between consecutive valid samples on a clock of S (default 1 s) from the open;
- σ_jump = sqrt(ΣJ²/V), J each change of mid in ticks between consecutive valid points, none across a spell without a valid mid, V the valid seconds;
- A and k: in each interval (g, g + Δ] (default 100 ms) with a valid start mid, the deepest depth from the start mid an aggressor reached on each side, from every `E` and every printable `C` at its execution price. A depth δ is open to an interval only when start mid ± δ lies on the tick grid, so a one-tick spread has only half-tick depths. λ(δ) = hits/(2·exposure·Δ), both sides pooled, and log λ = log A − kδ by least squares over the depths with a hit, reported with the number of points and R², and called exact at two.

`tests/test_calibrate_cli.py` builds three symbols for eight seconds and states every expected line, worked by hand: on a one-tick spread λ = 0.5, 0.25, 0.125 at 0.5, 1.5 and 2.5 ticks, k = ln 2 = 0.693147, A = 0.707107; on a two-tick spread k = (ln 2)/2 = 0.346574, A = 2^(−5/3) = 0.314980, R² = 0.75; a moving mid with a halt gives σ_clock = sqrt(1/5) = 0.447214 and σ_jump = sqrt(1/7) = 0.377964. Eight faults show it catching the depth taken from the current mid, a non-printable `C` counted, no nanosecond collapse, a jump across a halt, whole-tick depths open on a one-tick spread, the interval boundary moved, σ not demeaned and a wrong R². No new parser or decoder: the tool reads the day through `FrameReader` and the fast book, both already fuzzed, so it has no fuzz target.

**What didn't work.**

- The inventory bound was first checked against the position alone. On the synthetic replay the position reached 583 shares against a bound of 500: a resting order filled part of its lot while its replace was in flight, and the replace then restored the full lot. The bound now counts what the order out can still fill.
- The first quoter test expected two replaces when the mid moved; the ask's rounded target had not moved, so one is right, and the test now says why.
- clang-tidy rejected `push_back` inside `noexcept` functions in the mark recorder and the fill log, an unchecked optional in the attribution, and implicit widening in the tests. The recorder and the log are now sized once with a count, which is also what keeps them allocation-free.
- The fault matrix found the calibration fixture blind to one bug: with the depth taken from the mid of the moment instead of the interval's start, the test still passed, because the mid in that interval moved up and the execution still counted at 0.5 ticks. The fixture now locks the book at the bid for that execution, so the wrong mid puts it at depth 0.
- clang-tidy found the calibrator's fields padded by 46 bytes, a throwing `.at()` in a `noexcept` function and the tool's argument loop over its complexity limit; the fields are ordered by width and the tool's `run` is split.
- The first T-MM-2 run, P-0201-20261105T053845Z, wrote "Compiler: unavailable": the script looked for the compiler with a pattern that the quoted path in `compile_commands.json` defeated. It now reads the entry for the tool. That run's raw output was identical to the second's, its file was not kept, and its row stays in the run log.

## The market maker's evaluation, 2026-11-05

The two switches of the with-and-without runs, then the symbols and parameters, chosen and committed on 10302019, an FOMC day, before any strategy ran on any real day. The report day is 12302019, a late-December session, so the result is out of sample by day.

**Q-off** (`Config::queue`, the header comment of `include/itch/backtest.hpp`). "Queue model off" is not one thing (BENCHMARKS.md, "Exact against modelled queue position"), so the switch names a rule, and the two are the two carteret compares on real ITCH: conservative, whose fill rate it found 21.2% below exact, and proportional, 0.7% below. Both are what a backtest on an aggregate feed must assume, since it cannot tell which order a message names. `ahead` is set on arrival as under the exact rule. Then at S's price every execution takes its shares from the front, whichever order it hits, `ahead` first and the rest to S; an add never goes ahead, a late add included; and a cancel, delete or replace of c shares from a level of d with a ahead takes:

- conservative: from behind S while shares rest behind it, a ← min(a, d − c). This is hftbacktest's RiskAverse model as `backtester.md` BT-2 quotes it, "your order queue position doesn't change" on a cancel, with its code's cap of the queue ahead at the level's new size (`queue.rs`, `RiskAdverseQueueModel::depth`), and carteret's conservative attribution, "assume every cancel came from behind" (its design record 030). The cap is what "from behind" means once nothing is left behind.
- proportional: c·a/d from ahead, carteret's proportional attribution ("a cancel of c from a level of d with a ahead removes c·a/d from in front", record 030), which is hftbacktest's ProbQueueModel with f(x) = x (BT-2: est_front = front − (1 − prob)·chg with prob = back/(back + front)). a is held in millionths of a share, each removal rounded down, and the shares that reach S are whole shares, rounded down.

Neither lets a pass the shares resting at S's price; a property test checks that, and that a never rises, after every message of four synthetic markets under each rule. Trade-throughs and crossing adds fill as under the exact rule. Hand-built tests in `tests/test_backtest.cpp` ("QueueOff") give the exact rule's figure beside each step where the rule departs from it.

**L-off** (`Config::latency_off`) sets all four latencies to zero whatever `Config::latency` holds; its test checks the accept, the fill report and every delivery at their exchange times, and that the run equals one with every latency set to 0. Property test 15 now runs every combination of the three queue rules, latency on and off, both models and two γ on one synthetic day: one mark-path hash in all 24 runs, and the fills of Base, each Q-off rule and L-off all differ, so the equal hashes are not vacuous. Ten faults in `scripts/faults/backtester.json` and one in `marketmaker.json`, each caught.

**The symbols.** The rule is the evidence's (market-maker.md, MK-75), applied by `tools/itch_select` to 10302019 only and written before it ran: Nasdaq-listed common stock (Market Category Q, G or S, Issue Classification C), not an ETP, not a test issue, not halted from the open to the close, a close from $5 to $500, and from 09:45 to 15:45 on the Nasdaq book either tick-constrained (time-weighted spread at most $0.015 and one tick at least 80% of the valid time, group A) or not (at least $0.05 and at most 5%, group B); each group's pick is its most traded symbol by `E` and printable `C` shares in regular hours. Reasons: it picks by book shape and volume, never by a strategy's output; volume ranking is deterministic, so anyone can redo it from the file; and the pair gives a contrast of mechanics, the one-tick queue against a wide book, which is what Q-off and L-off should move differently. One pair of one day supports statements about mechanics on that day and nothing more.

Run under `heavy.sh` as `itch_select data/10302019.NASDAQ_ITCH50 --top 15` (raw `select_10302019.out` in the research cache, 24.7 s, peak RSS 9.07 GiB, the mapped day counted): 8,886 directory symbols, 1,716 in the universe, 117 in group A and 1,092 in group B. **AMD** leads A with 11,386,492 shares (average spread $0.010918, one tick 91.1% of the time, close $33.145), and **BYND** leads B with 2,499,233 (average $0.094989, one tick 3.3%, close $88.94). The test of the tool builds a day of 19 symbols, one on each side of every bar, and states every figure by hand; seven faults.

**Calibration**, 10302019 only, under `heavy.sh`, `itch_calibrate data/10302019.NASDAQ_ITCH50 --symbols AMD,BYND` at its defaults (Δ = 100 ms, a 1 s clock for σ, the tick one cent, depths to 20 ticks; raw `calibrate_10302019.out`, 19.4 s, peak RSS 9.06 GiB). In ticks and seconds:

| | σ, 1 s clock | σ from jumps | k | A | Fit points | R² |
|---|---|---|---|---|---|---|
| AMD | 0.727768 | 0.694522 | 0.748212 | 0.096422 | 18 | 0.734962 |
| BYND | 2.592559 | 3.892782 | 0.243074 | 0.170163 | 40 | 0.991704 |

σ is the clock estimator; the jump one is reported beside it, and on BYND the two differ by half, since a wide book's mid jumps back and forth inside a second. AMD's fit is poor near the touch, as the evidence warns for one-tick names: odd half-tick depths and whole-tick depths sit on two different lines, because a whole-tick depth is open only while the spread is two ticks.

**The parameters**, the same for both models and every run, in `scripts/mm_evaluation.json`: h = 15 s; one round lot of 100 shares an order (zero impact needs small orders, MK-13); at most 5 lots of inventory either way, which the strategy enforces by not quoting a side that could pass it, so "q stays in bounds" holds by construction and cannot pin γ; quoting from 09:45:00 to 15:45:00, the PnL window to 16:00:00 from a flat start, AS's horizon T the stop; latencies 25 µs each way and 5 µs of compute (BENCHMARKS.md, "Latency assumptions"); fees from the 2019-12-30 schedule at the base tier. γ is chosen, not estimated (MK-18): γ = 1/(σ²·21,600 s), so that AS's risk term γσ²(T − t) is one tick at the start and falls to zero at the stop, and one lot of inventory moves the reservation price by one tick at the start. That gives γ = 8.74098 × 10⁻⁵ for AMD and 6.88793 × 10⁻⁶ for BYND, per tick per lot. AS's spread is then 3.67 ticks at the start and 2.67 at the stop on AMD, and 9.23 and 8.23 on BYND: on a one-tick AMD book the model's bid rounds to one tick under the best bid, so AMD will quote behind the touch, filling only when the touch is cleared. That is what the calibrated model says, and it was not adjusted.

The runs, each a whole day of 12302019 under `heavy.sh`: AS under Base, Q-off conservative, Q-off proportional, L-off, and both off once with each Q-off rule; GLFT under Base, a labelled variant.

**The runs on 12302019**, P-0202, through `scripts/mm_report.py run` at 0999cb4, clean, each `heavy.sh /usr/bin/time -l itch_mm data/12302019.NASDAQ_ITCH50 --date 20191230 ...` with the committed parameters, one at a time (raw `mm_12302019_<symbol>_<run>.out` and `.err` in the research cache, run P-0202-20261105T080745Z). `results/mm_pnl_12302019.md` carries every run's output, and check.sh renders its body again from that output. Every check of the tool passed in all 14 runs, and S + A + I == the ledger's total with == in each. One mark-path hash in a symbol's seven runs: AMD `f90a8d95ab621725` (23,405 points), BYND `3d8ea13a60ce71fd` (13,557). No allocation after construction, 0 queue anomalies, and no taker fill in any run. Peak RSS 8.76 to 8.77 GiB in every run, the mapped day counted. One symbol-day. No edge is claimed. These are 2 symbols on one day.

| Run | AMD fills, shares | AMD total, ticks a share | BYND fills, shares | BYND S gross, A, I, ticks a share | BYND total |
|---|---|---|---|---|---|
| AS Base | 11, 1,066 | −2.435 (−$25.96) | 53, 3,299 | +4.281, −5.005, −11.914 | −12.565 (−$414.51) |
| AS Q-off conservative | 10, 901 | −2.623 | 54, 3,299 | +4.281, −4.944, −11.914 | −12.504 |
| AS Q-off proportional | 12, 900 | −3.175 | 58, 3,271 | +4.278, −4.846, −12.022 | −12.518 |
| AS L-off | 7, 700 | +0.176 (+$1.23) | 53, 2,837 | +4.562, −5.542, −10.640 | −11.545 (−$327.53) |
| AS both off, conservative | 7, 700 | +0.176 | 53, 2,837 | +4.562, −5.542, −10.640 | −11.545 |
| AS both off, proportional | 12, 900 | −6.286 | 57, 2,980 | +4.653, −5.277, −12.598 | −13.146 |
| GLFT Base | 11, 1,066 | +0.463 | 66, 3,624 | +4.129, −5.086, −11.884 | −12.769 |

Against P-0202. Held: every check, the identity, one hash a symbol; AMD's fills (11, between 0 and 60), its S gross (+1.391 ticks a share), fee credit (0.100) and A (−2.580) inside their ranges, its total negative; BYND's S gross (+4.281), fees (0.073) and A (−5.005), larger in size than S gross; BYND's total in dollars (−$414.51, inside −$5,000 to $0); Q-off proportional within 10% of Base's fills on BYND (+9.4%) and every component within a tick; L-off with no taker fill, which says little, since Base had none either, and BYND's total a share higher and its fills within 40%; GLFT at 1.10 times AS's shares on BYND, S gross a share 0.15 ticks lower. Missed: BYND traded 53 times and 3,299 shares, far under 400 and 30,000; its I (−11.914) is larger in size than A, and its total a share (−12.565) is far under −3, both because a 217-share position was held into a falling close on few fills; AMD's largest component in dollars is A, not I; Q-off conservative did not cut BYND's fills (53 to 54) or make A a share more negative (−5.005 to −4.944); L-off made BYND's A a share more negative (−5.542), not less; the both-off runs did not move as Base did under each rule; and peak RSS was 8.76 GiB, under the 9.3 to 9.9 predicted, which was scaled from 10302019's larger file. The miss on BYND's activity is the largest: the mark path has 13,557 points on 12302019 against 52,846 in the in-sample check on 10302019, so the book moved a quarter as often, while the quotes kept the width calibrated on the FOMC day.

One switch at a time, on this day only. Q-off conservative moved little: +$2.32 on AMD and +$2.00 on BYND, and with latency off it changed nothing on either symbol, the same fills to the share as L-off. Q-off proportional moved AMD's total by −$2.61 and BYND's by +$5.05 with latency on, but −$57.81 and −$64.23 with latency off, nearly all through I: a few more fills changed the position held into the close. L-off moved the totals most, +$27.19 on AMD and +$86.98 on BYND, again mostly through I, with fewer shares traded on both symbols. With 11 and 53 fills, each of these differences is a few fills and the position they leave, and none supports a claim about the switches beyond this day. The direction carteret found for a conservative rule, fewer and worse fills, did not appear here, on 54 fills against its 93,525 placements.

Next to BENCHMARKS.md: 11 of 14 runs lost money at the base tier, as carteret's maker did in every arm on other days; the three that did not are AMD's L-off, both-off conservative and GLFT Base, at +$1.23, +$1.23 and +$4.93 on 700 to 1,066 shares. That is context and shows nothing from one day.


## The SPSC ring, 2026-11-05

`include/itch/spsc.hpp`, the comparison and ablation in `bench/bench_spsc.cpp`, the pipeline in `bench/pipeline.hpp` and `bench/bench_pipeline.cpp`, the stress test in `tests/spsc_stress.cpp`, and three results on the Mac. Nothing here ran on `debian`; what is left for it is in TASKS.md.

**The ring.** Free-running 64-bit indices, so a full ring uses every slot. Each side keeps a copy of the other's index and reads the shared one only when its copy says full, or empty. The capacity is rounded up to a power of two and a slot is `index & mask`. The batch calls write or read every slot they can and store their index once. Each of the four is a switch in `SpscConfig`, so T-SPSC-2 builds one variant per change; `modulo` divides by a capacity read at run time, so the compiler cannot turn it back into a mask. Four orders and no more: publish (release), observe (acquire), return (release) and reclaim (acquire), with no fence, seq_cst or read-modify-write. The disassembly shows them as two `stlr` and two `ldapr` in the producer and an `ldapr` and an `stlur` in the consumer. The slots are a vector allocated and zeroed in the constructor, and `test_alloc` counts no allocation in any call after it, the pipeline's included.

**The line size, from one constant.** `kCacheLine` is 128 on Apple silicon and 64 on x86-64 and on other arm64 cores. Apple's cores move 128-byte lines (`hw.cachelinesize`, and XNU's L2 line is 1 << 7, LC-21), so 64 would leave both indices on one line. `std::hardware_destructive_interference_size` is not used: it is the compiler's guess, 256 under Apple clang 21 and 128 under LLVM 23 on the same Mac, and GCC's documentation calls it unfit for a header (LC-17 to LC-20). The value comes from `cmake/ItchCacheLine.cmake`, by target architecture, as `ITCH_CACHE_LINE` on `itch_parser`, because `scripts/gates.py` keeps conditional compilation out of `include/` and `src/`. A target with no measured size leaves it undefined and the header does not compile. `spsc.hpp` asserts it is 64 or 128. `test_spsc` asserts the value per target at compile time, each with its reason, and against the line size the system reports at run time.

**The comparison queues** are pinned by commit in `cmake/ItchDependencies.cmake` behind `ITCH_COMPARISON_QUEUES`, off by default and off in check.sh. Each is used as shipped through its non-blocking single-item calls with its own padding (LC-22). folly is used from one header, `ProducerConsumerQueue.h`, with an empty generated `folly/folly-config.h`. Nothing it includes reads a configure result, so folly is not built, and a configure-time compile checks this. **LC-11 is out of date.** It says folly's queue has no cached index. At the pinned c8c7adb7, `write()` and `read()` each keep a `remoteIndexCache` and load the other side's index only when the cache says full or empty (`ProducerConsumerQueue.h`, lines 113 to 135). The mutex ring yields when it finds the ring full or empty, as a blocking queue would sleep. Without that, the spinning side retook the lock as soon as it let go, and an rtt took 2.2 ms on the Mac.

**TSan does not see a race through memcpy here.** The ring's first two-thread test hands over 256-byte stamped items. Under TSan (LLVM 23, macOS arm64), a build with any of the four orders weakened reported no race. Repros in the research cache show why. A slot copied as a 256-byte item goes through `__tsan_memcpy`, and that range access is not reported against the producer's write. An 8-byte item is reported. So is a local that SROA reduces to one 8-byte load. Popping into an array element is missed. `test_spsc` now also hands over 64-bit words in order (`TwoThreadsHandOverEveryWordInOrder`). Under TSan, each weakened build reported data races on it: 12, 12, 12 and 16 warnings for publish, observe, return and reclaim. TSan failed each process (exit 134), though the test's own order check passed every time. Each build still reported 0 races on the item test. The shipped orders pass `test_spsc`, `test_pipeline` and the stress smoke test under TSan. The faults `spsc-publish-relaxed` and `spsc-reclaim-relaxed` are caught by TSan on the word test.

**The negative control without TSan (LC-37).** `-DITCH_SPSC_NEGATIVE_CONTROL=<order>` builds `tests/support/spsc_ordering.hpp`'s `TestOrdering`, with that one order relaxed. The release `spsc_stress` checks every 256-byte item for place and tearing. It ran 1,000 rounds of 1,000,000 items at capacity 4, then 300 rounds with four noise threads, then 300 rounds at capacity 64. In each weakened build, the disassembly shows exactly that order's instruction gone.

| Order weakened | 1e9 items, capacity 4 | 3e8, 4 noise threads | 3e8, capacity 64 |
|---|---|---|---|
| none (shipped) | 0 wrong, 0 torn | 0, 0 | 0, 0 |
| publish | 571 wrong place, 84,659 torn, from round 0 | 82, 20,974 | 453, 19,702 |
| observe | 72,498, 78,020, from round 80 | 1, 14 | 46, 6,314 |
| return | 0, 0 | 0, 0 | 0, 0 |
| reclaim | 0, 0 | 0, 0 | 0, 0 |

So on the M4 Max, stress catches a weakened publish or observe within the first rounds. It does not catch a weakened return or reclaim in 1.6e9 items. Those two need the consumer's slot loads to pass its index store, or the producer's slot stores to pass its index load. The core did not show that here, and only TSan's word test catches them. These figures come from the code before the stall guard, at fcc07c7. `spsc_stress` and `test_spsc` now give up after 10 s without an item, so a fault that loses one fails instead of hanging. The stall guard changes no ordering. The shipped orders ran all three again on the guarded code at 950867c, with 0 wrong and 0 torn in each: 1e9 items at capacity 4 in 60.52 s, 3e8 with noise in 21.10 s, and 3e8 at capacity 64 in 6.08 s. The weakened builds were not rerun. 22 faults in `scripts/faults/spsc.json`, each detected: the orders, the mask, each cached index, batch publish, the padding constant, the default padding, the pipeline's last batch, the checkpoint, the bench's queue filter, the driver, the result checks, and check_predictions' pattern.

**T-SPSC-1 on the Mac**, P-0301, run P-0301-20261105T115340Z at f870418, clean. `results/spsc_queues_mac_12302019.md`, from `measure.sh /usr/bin/time -l python3 -B scripts/spsc_driver.py --suite queues --bench build/queues/bench/bench_spsc` with 3 repetitions. The element is an 8-byte integer in rtt, saturated and paced, and a 64-byte item in burst. Every queue was asked for 131,072 slots. The runs are unpinned on macOS, which is weak evidence (LC-28). Medians:

- rtt: project 88.98 ns (CV 62%), rigtorp 93.02, folly 87.08, boost 174.2, moodycamel 162.3, xenium 159.2, atomic_queue 245.9, mutex 433.5.
- Saturated: moodycamel 739.3 M items/s. The others: mutex 61.14, project 24.33, xenium 22.86, boost 21.01, atomic_queue 19.14, folly 17.40, rigtorp 13.93.
- The bars are set on x86, so the file only says whether this Mac would meet them: boost over project 1.96× (would meet ≥ 1.16), project over moodycamel 0.55× and over rigtorp 0.96× (each would meet ≤ 1.00).

Against P-0301:

- Held: every item moved in order and intact; the burst's 729,611 messages; each ratio inside its range and project rtt inside 70 to 130 ns; moodycamel the fastest saturated queue by more than 5×; the project, rigtorp, boost, folly and atomic_queue within 12 to 60 M items/s; the mutex's p99 the worst on every paced and burst row; p99.99 and max above the floor everywhere.
- Missed:
  - The mutex's rtt was 1.76× the next slowest, atomic_queue, not 2×.
  - atomic_queue's and boost's paced-10000ns p50 were 291.7 and 333.3 ns, not below the floor.
  - Late sends were under 1% on every paced row, but in the burst they were 186,330 to 204,732 on every lock-free row in every repetition, about 26%, not under 1%.
  - Peak RSS was 7.71 GiB, not under 1 GiB: the bench walks the mapped day up to 09:30 to find the burst, and the pages it reads count.

The late sends are the data's, not the queues'. A send counts as late when it goes after the next message was due. In the window, 201,138 of the 729,610 gaps between consecutive ITCH timestamps are 0 ns, and 303,339 are at most 500 ns. This comes from a scan of the window under `heavy.sh`, raw `gaps.out` in the research cache. A message that shares its timestamp with the next one is late as soon as one tick passes. So about 201,000 sends are late for any queue whose send takes a tick. The lock-free rows' 186,330 to 204,732 sit around that, and the mutex's 207,563 to 239,686 are above it.

**T-SPSC-2 on the Mac**, P-0302, run P-0302-20261105T115802Z at f870418, clean. `results/spsc_ablation_mac_12302019.md`, from the release build. Medians:

| Variant | rtt, ns | saturated, M items/s |
|---|---|---|
| shipped | 86.16 | 24.82 |
| uncached | 162.2 | 25.49 |
| modulo | 84.91 | 24.70 |
| uncached-modulo | 162.4 | 25.83 |
| padding-8 | 153.6 | 105.0 |
| padding-64 | 85.39 | 24.98 |
| padding-256 | 91.17 | 24.66 |

Through the batch calls, 32 items a call, batch publish moved 1,354 M items/s and publish-each 336.0, 4.0×. The four T-SPSC-2 ratios came out at 0.96×, 0.97×, 1.00× and 0.99×. Each is a miss this Mac would make against bars of 3.9× and 4.2× set on an i7-9700 under WSL2. Every P-0302 range held except peak RSS, which was 7.71 GiB for the same reason as above. The predicted reason holds as far as this run can say. With a single-item consumer that keeps up, each item costs one line transfer of about 40 ns. A cached index saves nothing there, and the division hides under the transfer. The cached index does show in rtt, where uncached is 1.88× shipped. Padding-8, both sides on one line, runs saturated 4.2× faster than shipped, with a CV of 61%, at 1.78× shipped's rtt. Why is not known: the Mac gives no counters, and nothing here measured it.

**T-SPSC-3.** The parser thread decodes each message the book acts on into a 32-byte `BookOp` and pushes them through the ring, 64 to a publish, to the book thread. The book thread applies them on a fresh paged-flat book. This is set against the shipped single-threaded replay, and against the pipeline's work done on one thread (decoded). P-0303's run, P-0303-20261105T120127Z at f870418, agreed by hash in every pass. But the closing book held 0 orders on 0 levels, so its hash, `9e3779b97f4a7c15`, is the empty book's. A real day's closing book is empty, as the oracle found ("The end-of-day hash is weak on a real day", in the Harness entry), so equality at the close shows nothing. Its file was withdrawn before commit, and its row in the run log says so. Each replay now also hashes the book once, untimed, on the book's thread, just after the K-th op the book acts on. That op is the same one in all three modes, since the decoded modes hand the book exactly the messages `apply()` acts on. `test_pipeline` checks the checkpoint against a book built one op at a time, at the first op, the middle and the last but one. bench_pipeline refuses a run where a pass misses the checkpoint or disagrees there. The driver also refuses one where the book at the checkpoint is empty. Five faults.

P-0303's run, for the record, before the withdrawal: single 15.81 s, pipelined 13.41 s and decoded 16.34 s. Pipelined over single was 0.963 (0.830 to 1.009), and decoded over single 1.033. The parser found the ring full 7.05 to 8.39 billion times and the book thread found it empty 649,000 to 822,000 times. Peak RSS was 8.34 GiB. These are inside P-0303's ranges except peak RSS, just under 8.5. None of them is published.

P-0304's first run, P-0304-20261105T131900Z at 660007e, is withdrawn too. Another full-day replay ran on the Mac outside measure.sh from about 13:18:55 to 13:19:30 UTC, the first 30 s of the run. Its row in the run log says so, and its file and raw records stay in the research cache.

**T-SPSC-3 on the Mac**, P-0304, run P-0304-20261105T132346Z at bb83ed3, clean. `results/spsc_pipeline_mac_12302019.md`, from `measure.sh /usr/bin/time -l python3 -B scripts/spsc_driver.py --suite pipeline --bench build/release/bench/bench_pipeline --checkpoint-ops 131000000`, 3 repetitions, unpinned, measure.sh's load 1.64 at the start and 1.95 at the end. Medians:

- Single-threaded: 12.69 s (21,173,349 messages/s, CV 0.38%).
- Pipelined: 12.47 s.
- Decoded: 13.23 s.
- Pipelined over single, paired: 0.983 (0.980 to 0.987). Against the 1.31 bar, set on a pinned Ryzen 9 5950X, this Mac would meet it, and the file says only that.
- Decoded over single: 1.042.

So on this Mac, splitting the parse onto a second thread saves under 2% of a day. Doing the pipeline's decode on one thread costs 4%. The book's work is most of the time either way.

The parser found the ring full 6.87 to 6.90 billion times, and the book thread found it empty 234,571 to 264,866 times. The parser is the faster side, and the ring of 65,536 ops sits full nearly all day. Every pass left the empty closing book. At the checkpoint, every pass held `92e373870f5f9e5e`, 1,769,801 orders on 749,484 levels, in 33.4 to 38.2 ms a walk. `apply()` returned applied 263,241,937 times, and the single replay's other 5,502,843 messages had no book effect. Peak RSS was 8.34 GiB, the mapped day counted.

Against P-0304:

- Held:
  - every check;
  - the checkpoint book inside 200,000 to 1,924,078 orders, and its walk inside 1 to 500 ms;
  - pipelined over single inside 0.80 to 1.10;
  - decoded over single inside 0.95 to 1.15;
  - full waits above empty waits;
  - peak RSS inside 8.0 to 9.0 GiB.
- Missed: the single-threaded time, 12.69 s, under the 13 to 18 s predicted from P-0303's 15.81 s. P-0303 started at load 3.86 and this run at 1.64. The 20% between them is not otherwise explained.

carteret found the pipeline 1.31 times slower on x86 and single-threaded replay the faster design. Here the two are within 2%, on an unpinned Mac, which is weak evidence. The pinned comparison is left for `debian`.

**Left open.** On `debian`:
- T-SPSC-1, T-SPSC-2 and T-SPSC-3 pinned, on two physical cores, for example 4 and 6.
- TSan with the four negative controls, and the x86 stress with each.
- A cross-core check of the timer before any one-way latency there.

On the Mac, the reason padding-8 runs faster saturated. TASKS.md lists each.
