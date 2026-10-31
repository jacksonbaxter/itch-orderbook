# A fixture README

An ITCH 5.0 parser in C++23, with 3 hosts and 2 passes. See phase 2 of the plan.

| Host | Messages/s | p99 | Source |
|---|---|---|---|
| debian | 20,677,835 | 291.7 ns | results/bench_one.md |
| macOS | 12.5 M | 3.06% | results/bench_two.md |

The day holds 268,744,780 messages, dated 2019-12-30.

Run `bench_replay --orders 4194304` to repeat it, as on [this page](https://example.org/9999.5).

```
sample output 123456 that is not a figure
```

The chip costs $400 at 10 GHz. <!-- figures: not measured -->
