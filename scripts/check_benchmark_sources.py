#!/usr/bin/env python3
"""Open the source behind each BENCHMARKS.md number and check it says what we claim.

Downloads a fresh copy of every source, independent of any research snapshot, and
looks for the exact text. Run from anywhere:

    python3 -I scripts/check_benchmark_sources.py --cache DIR --log FILE [--only ID]

DIR holds the downloads and must be outside the repo, because they are untrusted
data. Each source gets its own directory. Exit status is non-zero on any failure.
"""

import argparse
import hashlib
import html
import pathlib
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ITCH_BOOK = "https://raw.githubusercontent.com/groovg/itch-book/b502b002abf650760b79c010b3ae3a8f82896ae8/README.md"
TAHSINJ = "https://raw.githubusercontent.com/tahsinj/nasdaq-feed-handler/e65b8408c1665ecce2f4c01ff5e2bb49aa21c6e5"
KAREEM = "https://raw.githubusercontent.com/KareemJandali/itchbook/3c646f9da182136125059243b475f7b8baa53773"
CARTERET = "https://raw.githubusercontent.com/EkanshSingh401/carteret/3df0e8757c979731b07a991de4ec7e81e81c8c08"
PARSERITCH = "https://raw.githubusercontent.com/rwxmai/parseritch/fbee276143b132dfa76ff57f7ed092e607f8153e"
BANDWIDTH = (
    "https://web.archive.org/web/20250123185610/https://www.nasdaqtrader.com/"
    "content/technicalsupport/specifications/dataproducts/bandwidthreport.xls"
)
PRICE_LIST_DEC_2019 = (
    "https://web.archive.org/web/20191207142614id_/"
    "http://www.nasdaqtrader.com/Trader.aspx?id=PriceListTrading2"
)
PRICE_LIST_FEB_2020 = (
    "https://web.archive.org/web/20200201153207id_/"
    "http://www.nasdaqtrader.com/Trader.aspx?id=PriceListTrading2"
)
ATOMIC_QUEUE = (
    "https://raw.githubusercontent.com/max0x7ba/atomic_queue/"
    "b7f1befe89446752767243c5e5550779a9a5eed2/html/results.js"
)
ATOMIC_QUEUE_ROWS = {
    "cc_latency_ryzen_5825u": [
        '"OptimistAtomicQueue": [138, 139, 138, 0]',
        '"boost::lockfree::spsc_queue": [159, 162, 160, 0]',
        '"moodycamel::ReaderWriterQueue": [136, 137, 136, 0]',
    ],
    "cc_latency_ryzen_5950x": ['"OptimistAtomicQueueB": [76, 94, 87, 3]'],
}
MIDAS = (
    "https://web.archive.org/web/20260312180908id_/https://www.sec.gov/files/node/add/"
    "data_distribution/individual_security_exchange_2019_q4.zip"
)
CPPCON_2017 = (
    "https://raw.githubusercontent.com/CppCon/CppCon2017/master/Presentations/"
    "When%20a%20Microsecond%20Is%20an%20Eternity/"
    "When%20a%20Microsecond%20Is%20an%20Eternity%20-%20Carl%20Cook%20-%20CppCon%202017.pdf"
)
PPR = "https://web.archive.org/web/2021id_/https://developer.amd.com/wp-content/resources/55570-B1_PUB.zip"
KERNEL = "https://raw.githubusercontent.com/torvalds/linux/v6.12"

# (target id, url, exact strings). Whitespace runs compare as one space.
CHECKS = [
    ("T-PARSE-1", ITCH_BOOK, ["709 M msg/s (~21.8 GB/s)", "Ryzen 7 9700X", "isolcpus=2-7"]),
    ("T-PARSE-2", ITCH_BOOK, ["199 M msg/s (~6.1 GB/s)"]),
    ("T-PARSE-3", "https://lunyn.com/benchmarks", ["93.9 M msg/s", "2.89 GB/s", "8481C"]),
    ("T-BOOK-1", ITCH_BOOK, ["16.4 M msg/s (61 ns)", "~1.4 GB"]),
    ("T-BOOK-2", ITCH_BOOK, ["12.5 M msg/s"]),
    ("T-BOOK-3", ITCH_BOOK, ["16.4 M msg/s (61 ns)** | **17.3 M msg/s (58 ns)** | **~1.4 GB**"]),
    (
        "T-BOOK-4",
        "https://raw.githubusercontent.com/rpgr18/matching-engine/"
        "b7bab44b2a71f924be46fff9459a21c69bf4de96/README.md",
        ["**14.4 M** | **69** | **25.5 s**", "M2 Pro"],
    ),
    (
        "T-LAT-1",
        ITCH_BOOK,
        [
            "| add | 118.6M | 60 | 130 | 447 | 683 |",
            "| delete | 114.4M | 70 | 230 | 599 | 768 |",
            "| replace | 21.6M | 110 | 350 | 709 | 945 |",
            "| reduce (E/C/X) | 8.6M | 50 | 179 | 548 | 734 |",
            "| timestamp-pair floor | 200k | 10 | 20 | 20 | 20 |",
        ],
    ),
    ("T-LAT-2", f"{TAHSINJ}/README.md", ["| 121 ns | 282 ns | 552 ns | 874 ns |", "i5-12400", "WSL2"]),
    ("T-LAT-3a", BANDWIDTH, ["2547190", "1305440"]),
    ("T-LAT-3b", "https://www.janestreet.com/tech-talks/safe-at-any-speed/", ["below 750ns"]),
    ("T-DESIGN-1", ITCH_BOOK, ["naive book (`std::map` + `unordered_map`) | 4.1 M msg/s (243 ns)", "pooled 4.6 M msg/s"]),
    (
        "T-ALLOC-1",
        f"{TAHSINJ}/results/01302020_results.txt",
        ["0 heap allocations", "295,669,870 heap allocations"],
    ),
    (
        "T-ORACLE-1",
        "https://raw.githubusercontent.com/mohith-reddy-seelam/nasdaq-itch-feed-handler/"
        "053a8d9b1e975eafc86ca9ed19648fe952ae6ec5/README.md",
        ["plus 50 M real ones"],
    ),
    (
        "T-TRADE-1",
        f"{KAREEM}/validation/census-2019-12-30.json",
        ['"adds": 118631456', '"deletes": 114360997', '"full_executions": 4270459', '"final": 0'],
    ),
    ("T-INV-1", f"{KAREEM}/validation/census-2019-12-30.json", ['"unknown_refs": 0']),
    ("T-INV-2", f"{CARTERET}/docs/correctness.md", ["8,580 | 70"]),
    (
        "T-ORACLE-1-carteret",
        f"{CARTERET}/docs/correctness.md",
        ["after **every** message, compares:", "| `12302019.NASDAQ_ITCH50` | 264,496,253 | identical |"],
    ),
    ("T-ORACLE-1-data", f"{CARTERET}/docs/data.md", ["| 268,744,780 | 4,248,527 | **264,496,253** |"]),
    ("T-ORACLE-2", f"{CARTERET}/docs/correctness.md", ["| **ten sessions** | **3,037,078,470** | **no divergence** |"]),
    (
        "T-LAT-4",
        f"{CARTERET}/docs/benchmarks.md",
        [
            "| hash: identity | **142.36** [139.59, 143.39] | 100 | 620 | 1080 | 0.623 |",
            "| baseline: multiply-shift, 24 B, 256 ticks | **167.78** [165.46, 168.92] | 120 | 660 | 1140 | 0.983 |",
            "264,478,375 book messages",
            "warmup 2,000,000 messages",
        ],
    ),
    (
        "T-PERF-1",
        f"{CARTERET}/docs/benchmarks.md",
        ["By type (exact, per message): `A` 1.16 DRAM fills, `D` 0.58, `E` 0.79, `U` 2.30."],
    ),
    (
        "T-PERF-1-event",
        f"{CARTERET}/docs/design.md",
        ["`ls_dmnd_fills_from_sys.mem_io_local` — event `0x43`, umask `0x08`, raw"],
    ),
    (
        "T-SPSC-3",
        f"{CARTERET}/docs/benchmarks.md",
        ["| SPSC (book on cpu8, parse on cpu9) | **219.93** [219.12, 224.68] | 130 | 690* | 1180 | — |"],
    ),
    (
        "ctx-design-carteret",
        f"{CARTERET}/docs/benchmarks.md",
        ["| index: chained | **179.84**", "418 were served from DRAM"],
    ),
    (
        "ctx-queue-bias",
        f"{CARTERET}/README.md",
        ["93,525", "57.21%", "−21.2%", "| 50,000+ | 2,295 | 13.4% | **−63.8%** |"],
    ),
    (
        "ctx-maker",
        f"{CARTERET}/README.md",
        ["**The strategy loses money in every arm at the base tier.**", "gross **−0.34 to −0.57 half-spreads per fill**"],
    ),
    ("ctx-fuzz", f"{CARTERET}/README.md", ["407,989,301"]),
    ("ctx-quality-chase", f"{CARTERET}/bench/chase.cpp", ["validates the miss counters against a workload"]),
    ("ctx-quality-machine", f"{CARTERET}/tools/machine_check.sh", ["defensible latency measurement"]),
    ("ctx-quality-machine-neg", f"{CARTERET}/tests/machine_check_negative.sh", ["proves the machine check can fail"]),
    ("ctx-quality-numbers", f"{CARTERET}/tools/check_numbers.py", ["Fail if a document quotes a number"]),
    ("ctx-quality-numbers-neg", f"{CARTERET}/tests/numbers_negative.sh", ["proves the numbers gate can fail"]),
    (
        "T-INV-2-design",
        f"{CARTERET}/docs/design.md",
        ["within 50 ms", "8,372 of the 8,580 crossings", "13 distinct symbols", "| Symbol not in state `T` | 7,945 | 62 |"],
    ),
    (
        "T-TRADE-2",
        "ftp://ftp.nasdaqtrader.com/files/crosses/CrossStats20191230.txt",
        ["12/30/2019,TOTAL ,NULL , 19851021, 93930336, 0,"],
    ),
    (
        "T-BT-1",
        "https://raw.githubusercontent.com/Jaspinder2003/LobSim/"
        "1cc2b12d27e161459da725bc394c3ebae09b8912/README.md",
        ["full replay + strategy", "507.6 ns/op", "2.0 M/s"],
    ),
    (
        "T-BT-fees-add-remove",
        PRICE_LIST_DEC_2019,
        ["All other firms $0.0020 for Tape A & B Securities $0.0015 for Tape C Securities", "All MPIDs $0.0030"],
    ),
    (
        "T-BT-fees-after",
        PRICE_LIST_FEB_2020,
        ["All other firms $0.0020 for Tape A & B Securities $0.0015 for Tape C Securities", "All MPIDs $0.0030"],
    ),
    (
        "T-BT-sec31",
        "https://www.govinfo.gov/content/pkg/FR-2019-03-15/pdf/2019-04800.pdf",
        ["$20.70 per million"],
    ),
    (
        "T-BT-sec31-until-2020",
        "https://www.govinfo.gov/content/pkg/FR-2020-01-14/pdf/2020-00423.pdf",
        ["effective on February 18, 2020"],
    ),
    (
        "T-BT-taf",
        "https://www.govinfo.gov/content/pkg/FR-2020-10-20/pdf/2020-23141.pdf",
        ["$0.000119 per share", "maximum charge of $5.95"],
    ),
    ("T-BT-colo", "https://www.nasdaqtrader.com/trader.aspx?id=colo", ["sub-50 microseconds"]),
    (
        "T-BT-race",
        "https://www.nber.org/system/files/working_papers/w29011/w29011.pdf",
        ["5-10 microseconds", "is 79 microseconds"],
    ),
    ("T-MM-2", "https://math.nyu.edu/~avellane/HighFrequencyTrading.pdf", ["dt \u00bc 0.005", "A \u00bc 140", "Inventory 1.49", "Inventory 1.35", "Inventory 3.02"]),
    (
        "T-MM-queue",
        "https://moallemi.com/ciamac/papers/queue-value-2016.pdf",
        ["comparable to the bid ask spread"],
    ),
    (
        "T-SPSC-2",
        "https://raw.githubusercontent.com/CppCon/CppCon2023/main/Presentations/"
        "SPSC_Lock-free_Wait-Free_Fifo_from_the_Ground_Up_CppCon_2023.pdf",
        ["41,859,740", "165,138,887", "690,662,074", "i7-9700"],
    ),
    (
        "T-SPSC-rigtorp",
        "https://raw.githubusercontent.com/rigtorp/SPSCQueue/"
        "59a6a938513ea5004817383711ed35d32385d3ee/README.md",
        ["| SPSCQueue | 362723 | 133 |", "| boost::lockfree::spsc | 209877 | 222 |"],
    ),
    ("T-SPSC-disruptor", "https://lmax-exchange.github.io/disruptor/files/Disruptor-1.0.pdf", ["Mean Latency 32,757 52"]),
    ("T-ARENA-1", "https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2015/n4468.pdf", ["39.577s", "4.89s"]),
    ("T-URING-brouer2016", "https://lists.openwall.net/netdev/2016/12/08/212", ["435.88", "600.09"]),
    ("T-URING-brouer2016b", "https://lists.openwall.net/netdev/2016/12/08/229", ["391.07"]),
    ("T-URING-brouer2019", "https://lists.openwall.net/netdev/2019/11/27/122", ["1461.41", "889.82"]),
    (
        "T-URING-thesis",
        "https://www.diva-portal.org/smash/get/diva2:1789103/FULLTEXT01.pdf",
        ["about 460,000 packets", "300,000 packets"],
    ),
    ("T-URING-2", BANDWIDTH, ["2547190"]),
    ("T-WASM-1", "https://www.usenix.org/system/files/atc19-jangda.pdf", ["1.55", "1.45"]),
    ("T-PERF-cook", CPPCON_2017, ["0.22% of all branches", "0.067% of all cache refs"]),
    ("T-PERF-kernel", f"{KERNEL}/arch/x86/events/amd/core.c", ["[PERF_COUNT_HW_CACHE_MISSES] = 0x0964,"]),
    (
        "T-PERF-amd",
        PPR,
        [
            "Data Cache Req Miss in L2",
            "Instruction Cache Req Miss in L2",
            "PMCx043 [Data Cache Refills from System]",
            "LS_MABRESP_LCL_DRAM. Read-write. Reset: 0. From DRAM (home node local).",
        ],
    ),
    ("ctx-jane-street", "https://www.janestreet.com/tech-talks/building-an-exchange/", ["about 30% of their time"]),
    ("ctx-liburing-844", "https://github.com/axboe/liburing/issues/844", ["475 000", "430 000"]),
    ("ctx-terms-cboe", "https://www.cboe.com/terms", ["to verify or correct other data or information"]),
    ("ctx-menkveld", "https://papers.tinbergen.nl/11076.pdf", ["positioning"]),
    (
        "ctx-chrome-clamp",
        "https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/renderer/core/timing/time_clamper.h",
        ["kCoarseResolutionMicroseconds = 100", "kFineResolutionMicroseconds = 5"],
    ),
    (
        "ctx-v1605b",
        "https://www.amd.com/content/dam/amd/en/documents/products/embedded/ryzen/v1000-family-product-brief.pdf",
        ["V1605B", "2400"],
    ),
    (
        "ctx-zen1-sog",
        "https://web.archive.org/web/2020id_/https://developer.amd.com/wordpress/media/2013/12/55723_SOG_Fam_17h_Processors_3.00.pdf",
        ["1536", "two 128-bit"],
    ),
    (
        "ctx-charles-cooper",
        f"{PARSERITCH}/bench-results/compare_01302019/after/table.md",
        ["| ccooper | book | arm64 | 368,366,634 | 14.30 | 38.8 | 25.8 |", "| parseritch | book_pf16 | x86_64 | 368,366,634 | 13.76 | 37.4 | 26.8 |"],
    ),
    (
        "ctx-cpptrader",
        "https://raw.githubusercontent.com/chronoxor/CppTrader/"
        "3815893630abe5cd2281c9fd3c6775808820b763/README.md",
        ["ITCH message throughput: 3232727 msg/s"],
    ),
    (
        "ctx-simd",
        "https://raw.githubusercontent.com/ademismkv/mp-orderbook/"
        "ab10d2ce4eddc682b6133ded90f72c2310ef1b6a/devlog/2026-08-02-day-16-neon-parser-honest-result.md",
        ["0.58x", "1.51x"],
    ),
]


def fetch(url, cache):
    """Download url once into its own directory and return the file path."""
    slot = cache / hashlib.sha256(url.encode()).hexdigest()[:16]
    slot.mkdir(parents=True, exist_ok=True)
    path = slot / "body"
    if path.exists() and path.stat().st_size > 0:
        return path
    subprocess.run(
        ["curl", "-sSL", "--fail", "--retry", "4", "--retry-all-errors", "--retry-delay", "20", "--max-time", "600", "-A", "Mozilla/5.0", "-o", str(path), url],
        check=True,
    )
    (slot / "url").write_text(url + "\n")
    return path


def pdf_text(path):
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True
    ).stdout.decode("utf-8", "replace")


def xlsx_cells(path):
    """Return every cell value in a workbook, using only the standard library."""
    out = []
    with zipfile.ZipFile(path) as book:
        for name in book.namelist():
            if name.endswith(".xml") and "worksheets" in name:
                for element in ET.fromstring(book.read(name)).iter():
                    if element.tag.endswith("}v") and element.text:
                        out.append(element.text)
    return " ".join(out)


def extract(path):
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return pdf_text(path)
    if head.startswith(b"PK"):
        with zipfile.ZipFile(path) as bundle:
            pdfs = [n for n in bundle.namelist() if n.lower().endswith(".pdf")]
            if pdfs:
                extracted = path.with_suffix(".pdf")
                extracted.write_bytes(bundle.read(pdfs[0]))
                return pdf_text(extracted)
        return xlsx_cells(path)
    text = path.read_text("utf-8", "replace")
    if "<html" in text[:2000].lower():
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return text


def normalise(text):
    return " ".join(text.replace("–", "-").split())


def check_midas(cache):
    """Sum Nasdaq rows for 2019-12-30 in the SEC's file."""
    path = fetch(MIDAS, cache)
    totals = {"symbols": 0, "TradeVol": 0.0, "HiddenVol": 0.0}  # volumes are in thousands
    with zipfile.ZipFile(path) as bundle:
        with bundle.open("dec19.csv") as rows:
            header = rows.readline().decode().strip().split(",")
            date, exchange = header.index("Date"), header.index("Exchange")
            trade, hidden = header.index("TradeVol('000)"), header.index("HiddenVol('000)")
            for raw in rows:
                cells = raw.decode().strip().split(",")
                if cells[date] == "20191230" and cells[exchange] == "Nasdaq":
                    totals["symbols"] += 1
                    totals["TradeVol"] += float(cells[trade])
                    totals["HiddenVol"] += float(cells[hidden])
    good = (
        round(totals["TradeVol"] * 1000) == 620148444
        and round(totals["HiddenVol"] * 1000) == 105453046
    )
    return good, totals


def check_atomic_queue(cache):
    """The cells must sit on the named machine's row, not on another machine's."""
    rows = fetch(ATOMIC_QUEUE, cache).read_text().splitlines()
    results = []
    for name, cells in ATOMIC_QUEUE_ROWS.items():
        row = next(line for line in rows if line.lstrip().startswith(name))
        results += [(cell, cell in row) for cell in cells]
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=pathlib.Path)
    parser.add_argument("--log", required=True, type=pathlib.Path)
    parser.add_argument("--only")
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)

    failures = 0
    with args.log.open("w") as log:
        for target, url, needles in CHECKS:
            if args.only and target != args.only:
                continue
            try:
                text = normalise(extract(fetch(url, args.cache)))
            except (subprocess.CalledProcessError, OSError, zipfile.BadZipFile, ET.ParseError) as error:
                failures += 1
                print(f"FAIL {target} {url}: {error}", file=log)
                continue
            for needle in needles:
                found = normalise(needle) in text
                failures += not found
                print(f"{'ok  ' if found else 'FAIL'} {target} {needle!r} {url}", file=log)
        if not args.only or args.only == "T-SPSC-1":
            for cell, found in check_atomic_queue(args.cache):
                failures += not found
                print(f"{'ok  ' if found else 'FAIL'} T-SPSC-1 {cell!r} {ATOMIC_QUEUE}", file=log)
        if not args.only or args.only == "T-TRADE-3":
            try:
                good, totals = check_midas(args.cache)
            except (subprocess.CalledProcessError, OSError, zipfile.BadZipFile, ValueError, KeyError) as error:
                good, totals = False, error
            failures += not good
            print(f"{'ok  ' if good else 'FAIL'} T-TRADE-3 {totals} {MIDAS}", file=log)
    print(f"{failures} failure(s). Log: {args.log}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
