// The SIMD experiments (simd.md, "Candidate experiments"), every kernel and every variant
// picked at the command line, all in one binary, over one day mapped from the page cache:
//
//     bench_kernels --input FILE --out RUN.json --experiment NAME [--kernel NAME]
//                   [--width 2|4|6|8] [--form NAME] [--watchlist N] [--core N]
//                   [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]
//     bench_kernels --list-kernels
//
// --kernel     scalar, neon, ssse3, avx2, avx512vbmi, wasm or auto; scalar if left out. A kernel
//              this build lacks, or this CPU cannot run, exits 2 with the reason, never runs
//              another in its place. auto is the widest this machine runs.
// --experiment one of (passes in the order they run; walk is the same loop without the
//              operation, so work less walk is the operation's cost):
//   field      E1: the field of --width bytes from every op message. walk, work, work, walk.
//   decode     E2: every Add Order into a 32-byte record in memory. walk, work, work, walk.
//   consume    E3: every op decoded, then hashed, added and compared in integer registers.
//              walk, work, work, walk.
//   parse      E4: the parser on the kernel, every op type, then one op type at a time, and
//              the walk. walk, all, A, F, E, C, X, D, U, all, walk.
//   book       E4 end to end: the parser on the kernel feeding FastBook<>, paged-flat. A
//              warm-up pass and a timed pass, each on a fresh book.
//   batch      E5: Add Orders four at a time where four come in a row. walk, work, work, walk.
//              Also, untimed, how many messages sit in runs of one type, by run length.
//   framing    E6: --form length|byte|table|constant, the frame walk alone. work, work.
//   filter     E9: --form compare|bitset, --watchlist N of the most active stocks by Add
//              Orders. walk, work, work, walk.
//   timestamp  E13: --form exact|split|overread|underread. walk, work, work, walk.
//   verify     the kernel against the scalar kernel on every message, untimed.
// framing, timestamp and filter's bitset form use no kernel, and take only --kernel scalar.
//
// Counters as bench_variants reads them: on debian cycles, instructions, branch misses,
// cache-misses (L2 misses on Zen 1) and r0843:u, all :u; on macOS the cycles and instructions
// thread_selfcounts gives, user and kernel together. Every page of the input is read and
// found resident before each timed pass and found resident after it.
//
// Exit codes: 0 publishable, 3 refused with the record written, 2 bad usage or a kernel or
// variant that cannot run here.
#include <sys/mman.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <expected>
#include <format>
#include <fstream>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/kernels/select.hpp"
#include "kernel_experiments.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;
using itch::kernels::KernelId;
namespace kx = itch::bench::kernel;

enum class Experiment : std::uint8_t {
  field,
  decode,
  consume,
  parse,
  book,
  batch,
  framing,
  filter,
  timestamp,
  verify
};

constexpr std::array<std::pair<std::string_view, Experiment>, 10> kExperiments{{
    {"field", Experiment::field},
    {"decode", Experiment::decode},
    {"consume", Experiment::consume},
    {"parse", Experiment::parse},
    {"book", Experiment::book},
    {"batch", Experiment::batch},
    {"framing", Experiment::framing},
    {"filter", Experiment::filter},
    {"timestamp", Experiment::timestamp},
    {"verify", Experiment::verify},
}};

std::string_view experiment_name(Experiment e) {
  for (const auto& [name, value] : kExperiments) {
    if (value == e) {
      return name;
    }
  }
  return "unnamed";
}

// simd.md's experiment each one is.
std::string_view simd_id(Experiment e) {
  switch (e) {
    case Experiment::field: return "E1";
    case Experiment::decode: return "E2";
    case Experiment::consume: return "E3";
    case Experiment::parse:
    case Experiment::book: return "E4";
    case Experiment::batch: return "E5";
    case Experiment::framing: return "E6";
    case Experiment::filter: return "E9";
    case Experiment::timestamp: return "E13";
    case Experiment::verify: return "none";
  }
  return "none";
}

// The instruction set each kernel's own instructions come from.
std::string_view isa_of(KernelId id) {
  switch (id) {
    case KernelId::scalar: return "scalar: general-purpose registers, REV or BSWAP";
    case KernelId::neon: return "NEON (AArch64 Advanced SIMD, 128-bit)";
    case KernelId::ssse3: return "SSSE3 (128-bit)";
    case KernelId::avx2: return "AVX2 (256-bit)";
    case KernelId::avx512vbmi: return "AVX-512 VBMI (512-bit)";
    case KernelId::wasm: return "WebAssembly SIMD128";
  }
  return "unnamed";
}

constexpr std::array<std::string_view, 4> kFramingForms{"length", "byte", "table", "constant"};
constexpr std::array<std::string_view, 2> kFilterForms{"compare", "bitset"};
constexpr std::array<std::string_view, 4> kStampForms{"exact", "split", "overread", "underread"};

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  std::string kernel = "scalar";
  std::string form;
  int core = -1;
  unsigned width = 0;
  std::size_t watchlist = 0;
  std::optional<Experiment> experiment;
  itch::BookCapacity capacity;
  bool list = false;
};

template <class Number>
bool parse_number(std::string_view text, Number& out) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out);
  return error == std::errc{} && end == text.data() + text.size();
}

bool parse_option(std::string_view arg, std::string_view value, Options& options) {
  if (arg == "--input") {
    options.input = value;
  } else if (arg == "--out") {
    options.out = value;
  } else if (arg == "--commit") {
    options.commit = value;
  } else if (arg == "--dataset") {
    options.dataset = value;
  } else if (arg == "--dataset-sha256") {
    options.dataset_sha256 = value;
  } else if (arg == "--kernel") {
    options.kernel = value;
  } else if (arg == "--form") {
    options.form = value;
  } else if (arg == "--core") {
    return parse_number(value, options.core);
  } else if (arg == "--width") {
    return parse_number(value, options.width);
  } else if (arg == "--watchlist") {
    return parse_number(value, options.watchlist);
  } else if (arg == "--experiment") {
    for (const auto& [name, e] : kExperiments) {
      if (value == name) {
        options.experiment = e;
      }
    }
    return options.experiment.has_value();
  } else {
    return false;
  }
  return true;
}

bool parse(std::span<char*> args, Options& options) {
  if (args.size() == 1 && std::string_view{args[0]} == "--list-kernels") {
    options.list = true;
    return true;
  }
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    if (!parse_option(args[i], args[i + 1], options)) {
      return false;
    }
  }
  return args.size() % 2 == 0 && !options.input.empty() && !options.out.empty() &&
         options.experiment.has_value();
}

bool one_of(std::string_view value, std::span<const std::string_view> allowed) {
  return std::ranges::find(allowed, value) != allowed.end();
}

// Why this variant cannot run, before anything is mapped, or nothing.
std::optional<std::string> bad_variant(const Options& options, KernelId kernel) {
  const Experiment e = *options.experiment;
  const bool takes_form =
      e == Experiment::framing || e == Experiment::filter || e == Experiment::timestamp;
  if (takes_form != !options.form.empty()) {
    return takes_form ? std::format("--experiment {} needs --form", experiment_name(e))
                      : std::format("--experiment {} takes no --form", experiment_name(e));
  }
  if ((e == Experiment::field) != (options.width != 0)) {
    return e == Experiment::field ? "--experiment field needs --width 2, 4, 6 or 8"
                                  : "only --experiment field takes --width";
  }
  if (e == Experiment::field && options.width != 2 && options.width != 4 && options.width != 6 &&
      options.width != 8) {
    return "--width is 2, 4, 6 or 8";
  }
  if ((e == Experiment::filter) != (options.watchlist != 0)) {
    return e == Experiment::filter ? "--experiment filter needs --watchlist N, N above 0"
                                   : "only --experiment filter takes --watchlist";
  }
  if (e == Experiment::framing && !one_of(options.form, kFramingForms)) {
    return "--form for framing is length, byte, table or constant";
  }
  if (e == Experiment::filter && !one_of(options.form, kFilterForms)) {
    return "--form for filter is compare or bitset";
  }
  if (e == Experiment::timestamp && !one_of(options.form, kStampForms)) {
    return "--form for timestamp is exact, split, overread or underread";
  }
  const bool kernel_free = e == Experiment::framing || e == Experiment::timestamp ||
                           (e == Experiment::filter && options.form == "bitset");
  if (kernel_free && kernel != KernelId::scalar) {
    return std::format("--experiment {}{} uses no kernel, so it takes only --kernel scalar",
                       experiment_name(e), e == Experiment::filter ? " --form bitset" : "");
  }
  return std::nullopt;
}

struct TimedPass {
  std::string mode;
  kx::Totals totals;
  std::uint64_t ticks = 0;
  std::vector<CounterStatus> counters;
  std::string residency_before;
  std::string residency_after;
};

struct Record {
  std::vector<TimedPass> passes;
  MessageMix mix;
  std::uint64_t warmup_messages = 0;
  std::optional<kx::RunLengths> runs;
  std::size_t watchlist_listed = 0;
  std::uint64_t compared = 0;
  std::uint64_t mismatches = 0;
  std::size_t framing_stopped_at = 0;
  std::uint64_t live_orders = 0;
  std::uint64_t live_levels = 0;
  std::vector<std::string> refusals;
};

void fault_in(std::span<const std::byte> bytes) {
  static std::atomic<std::byte> seen{std::byte{0}};
  std::byte any{0};
  for (std::size_t at = 0; at < bytes.size(); at += base_page_size()) {
    any |= bytes[at];
  }
  seen = any;
}

// The span of one timed pass. begin() and end() are plain calls around a loop that is
// inlined into the kernel's target region, so neither is inside the measured code's region.
class Clock {
 public:
  Clock(std::span<const std::byte> bytes, CounterGroup& group, Record& record)
      : bytes_{bytes}, group_{group}, record_{record} {}

  [[nodiscard]] bool begin(std::string mode) {
    pass_ = TimedPass{};
    pass_.mode = std::move(mode);
    fault_in(bytes_);
    const Residency before = resident(bytes_);
    pass_.residency_before = before.describe();
    if (!before.complete()) {
      record_.refusals.push_back("input not resident before a timed pass: " + before.describe());
      return false;
    }
    group_.start();
    started_ = ticks_start();
    return true;
  }

  void end(const kx::Totals& totals) {
    const std::uint64_t stopped = ticks_stop();
    group_.stop();
    pass_.ticks = stopped - started_;
    pass_.counters = group_.statuses();
    pass_.totals = totals;
    const Residency after = resident(bytes_);
    pass_.residency_after = after.describe();
    if (!after.complete()) {
      record_.refusals.push_back("input left memory during a timed pass: " + after.describe());
    }
    record_.passes.push_back(std::move(pass_));
  }

 private:
  std::span<const std::byte> bytes_;
  CounterGroup& group_;
  Record& record_;
  TimedPass pass_;
  std::uint64_t started_ = 0;
};

// Each experiment's passes on kernel K, run by itch::kernels::run() inside K's target region.
// operator() is always_inline so that the loops it inlines land in that region.
struct Passes {
  const Options& options;
  std::span<const std::byte> bytes;
  Clock& clock;
  Record& record;
  const kx::Watchlist* watchlist = nullptr;

  template <class K, bool Work>
  [[gnu::always_inline]] kx::Totals one(std::array<itch::kernels::AddFields, kx::kRing>& ring,
                                        std::span<std::uint32_t, kx::kSlots> slots,
                                        std::span<std::uint8_t> bits) const noexcept {
    switch (*options.experiment) {
      case Experiment::field:
        switch (options.width) {
          case 2: return kx::field_pass<K, 2, Work>(bytes);
          case 4: return kx::field_pass<K, 4, Work>(bytes);
          case 6: return kx::field_pass<K, 6, Work>(bytes);
          default: return kx::field_pass<K, 8, Work>(bytes);
        }
      case Experiment::decode: return kx::decode_pass<K, Work>(bytes, ring);
      case Experiment::consume: return kx::consume_pass<K, Work>(bytes, slots);
      case Experiment::batch: return kx::batch_pass<K, Work>(bytes);
      case Experiment::filter:
        return options.form == "compare"
                   ? kx::filter_pass<K, kx::Filter::compare, Work>(bytes, *watchlist, bits)
                   : kx::filter_pass<K, kx::Filter::bitset, Work>(bytes, *watchlist, bits);
      case Experiment::timestamp:
        if (options.form == "exact") {
          return kx::timestamp_pass<kx::Stamp::exact, Work>(bytes);
        }
        if (options.form == "split") {
          return kx::timestamp_pass<kx::Stamp::split, Work>(bytes);
        }
        if (options.form == "overread") {
          return kx::timestamp_pass<kx::Stamp::overread, Work>(bytes);
        }
        return kx::timestamp_pass<kx::Stamp::underread, Work>(bytes);
      default: return {};
    }
  }

  template <class K>
  [[gnu::always_inline]] kx::FramingTotals framing() const noexcept {
    if (options.form == "length") {
      return kx::framing_pass<kx::Framing::length>(bytes);
    }
    if (options.form == "byte") {
      return kx::framing_pass<kx::Framing::byte>(bytes);
    }
    if (options.form == "table") {
      return kx::framing_pass<kx::Framing::table>(bytes);
    }
    return kx::framing_pass<kx::Framing::constant>(bytes);
  }

  template <class K>
  [[gnu::always_inline]] void operator()() const {
    const Experiment e = *options.experiment;
    if (e == Experiment::verify) {
      const kx::VerifyTotals v = kx::verify_pass<K>(bytes);
      record.compared = v.compared;
      record.mismatches = v.mismatches;
      return;
    }
    if (e == Experiment::book) {
      using Book = itch::FastBook<>;
      {
        const auto warm = std::make_unique<Book>(options.capacity);
        record.warmup_messages = kx::book_pass<K>(bytes, *warm).messages;
      }
      const auto book = std::make_unique<Book>(options.capacity);
      if (clock.begin("work")) {
        clock.end(kx::book_pass<K>(bytes, *book));
      }
      record.live_orders = book->live_orders();
      record.live_levels = book->live_levels();
      return;
    }
    if (e == Experiment::framing) {
      record.warmup_messages = framing<K>().totals.messages;
      for (int i = 0; i < 2; ++i) {
        if (clock.begin("work")) {
          const kx::FramingTotals f = framing<K>();
          record.framing_stopped_at = f.stopped_at;
          clock.end(f.totals);
        }
      }
      return;
    }
    if (e == Experiment::parse) {
      record.warmup_messages = kx::parse_pass<K>(bytes).messages;
      const auto by_type = [this](std::string mode, char only) {
        if (clock.begin(std::move(mode))) {
          clock.end(kx::parse_one_type<K>(bytes, only));
        }
      };
      by_type("walk", 0);
      if (clock.begin("all")) {
        clock.end(kx::parse_pass<K>(bytes));
      }
      for (const char type : itch::kernels::kOpTypes) {
        by_type(std::string{type}, type);
      }
      if (clock.begin("all")) {
        clock.end(kx::parse_pass<K>(bytes));
      }
      by_type("walk", 0);
      return;
    }
    // walk, work, work, walk: a drift over the process falls on both alike.
    auto ring = std::make_unique<std::array<itch::kernels::AddFields, kx::kRing>>();
    auto slots = std::make_unique<std::array<std::uint32_t, kx::kSlots>>();
    std::vector<std::uint8_t> bits(std::size_t{1} << 16U);
    record.warmup_messages = one<K, true>(*ring, *slots, bits).messages;
    for (const bool work : {false, true, true, false}) {
      ring->fill({});
      slots->fill(0);
      std::ranges::fill(bits, std::uint8_t{0});
      if (clock.begin(work ? "work" : "walk")) {
        clock.end(work ? one<K, true>(*ring, *slots, bits) : one<K, false>(*ring, *slots, bits));
      }
    }
  }
};

// E9's watchlist: the n stocks with the most Add Orders in the input, ties to the lower
// locate, as each stock's 8 bytes from its Stock Directory message.
kx::Watchlist most_active(std::span<const std::byte> bytes, std::size_t n) {
  std::vector<std::uint64_t> adds(std::size_t{1} << 16U);
  std::vector<std::uint64_t> stock(std::size_t{1} << 16U);
  itch::FrameReader reader{bytes};
  for (auto f = reader.next(); !itch::is_terminal(f.status); f = reader.next()) {
    if (f.status != itch::FrameStatus::message) {
      continue;
    }
    const std::byte* m = f.payload.data();
    const auto locate = itch::read_be<std::uint16_t>(m + 1);
    const auto type = static_cast<char>(*m);
    if (type == itch::StockDirectory::kType) {
      stock[locate] = kx::stock_key(m + 11);
    } else if (type == itch::AddOrder::kType || type == itch::AddOrderAttributed::kType) {
      ++adds[locate];
    }
  }
  std::vector<std::size_t> order(adds.size());
  for (std::size_t i = 0; i < order.size(); ++i) {
    order[i] = i;
  }
  std::ranges::stable_sort(order, [&adds](std::size_t a, std::size_t b) { return adds[a] > adds[b]; });
  kx::Watchlist list;
  for (const std::size_t locate : order) {
    if (list.keys.size() == n || adds[locate] == 0) {
      break;
    }
    if (stock[locate] != 0) {
      list.keys.push_back(stock[locate]);
    }
  }
  list.listed = list.keys.size();
  list.keys.resize(((list.listed + 7) / 8) * 8, 0);
  return list;
}

std::string counters_json(const std::vector<CounterStatus>& statuses) {
  std::string out = "[";
  for (std::size_t i = 0; i < statuses.size(); ++i) {
    const CounterStatus& s = statuses[i];
    out +=
        std::format(R"({}{{"name": {}, "state": {}, "value": {}, "ratio": {:.6f}, "reason": {}}})",
                    i == 0 ? "" : ", ", json_string(s.name), json_string(to_string(s.state)),
                    s.value, s.running_ratio, json_string(s.reason));
  }
  return out + "]";
}

template <std::size_t N>
std::string array_json(const std::array<std::uint64_t, N>& values) {
  std::string out = "[";
  for (std::size_t i = 0; i < N; ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", values[i]);
  }
  return out + "]";
}

std::string record_json(const std::vector<HeaderField>& header, const Options& options,
                        itch::kernels::Selected kernel, std::size_t overread, const Record& record,
                        const TimerInfo& timer, bool pinned, std::uint64_t migrations) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(header[i].first),
                       json_string(header[i].second));
  }
  out += "\n  },\n";
  out += std::format(
      "  \"variant\": {{\"experiment\": {}, \"simd\": {}, \"kernel\": {}, \"isa\": {}, "
      "\"from_auto\": {}, \"arch\": {}, \"overread\": {}, \"width\": {}, \"form\": {}, "
      "\"watchlist\": {}, \"watchlist_listed\": {}}},\n",
      json_string(experiment_name(*options.experiment)),
      json_string(simd_id(*options.experiment)), json_string(to_string(kernel.id())),
      json_string(isa_of(kernel.id())), kernel.from_auto() ? "true" : "false",
      json_string(itch::kernels::kArch), overread, options.width, json_string(options.form),
      options.watchlist, record.watchlist_listed);
  out += std::format(R"(  "mix": {{"messages": {}, "book_messages": {}}},)", record.mix.messages,
                     record.mix.book_messages);
  out += std::format("\n  \"warmup_messages\": {},\n", record.warmup_messages);
  if (record.runs) {
    out += std::format(
        "  \"runs\": {{\"buckets\": [{}], \"any_type\": {}, \"add_order\": {}}},\n",
        [] {
          std::string names;
          for (std::size_t i = 0; i < kx::kRunBuckets.size(); ++i) {
            names += std::format("{}{}", i == 0 ? "" : ", ", json_string(kx::kRunBuckets[i]));
          }
          return names;
        }(),
        array_json(record.runs->any_type), array_json(record.runs->add_order));
  }
  out += std::format(
      "  \"verify\": {{\"compared\": {}, \"mismatches\": {}}},\n  \"framing_stopped_at\": {},\n"
      "  \"book\": {{\"live_orders\": {}, \"live_levels\": {}}},\n",
      record.compared, record.mismatches, record.framing_stopped_at, record.live_orders,
      record.live_levels);
  out += "  \"passes\": [";
  for (std::size_t i = 0; i < record.passes.size(); ++i) {
    const TimedPass& p = record.passes[i];
    const double seconds = timer.ticks_to_ns(static_cast<double>(p.ticks)) / 1e9;
    out += std::format(
        "{}\n    {{\"mode\": {}, \"messages\": {}, \"items\": {}, \"seconds\": {:.6f}, "
        "\"fold\": {}, \"matches\": {}, \"refused\": {}, \"residency_before\": {}, "
        "\"residency_after\": {}, \"counters\": {}}}",
        i == 0 ? "" : ",", json_string(p.mode), p.totals.messages, p.totals.items, seconds,
        p.totals.fold, p.totals.matches, p.totals.refused, json_string(p.residency_before),
        json_string(p.residency_after), counters_json(p.counters));
  }
  out += "\n  ],\n";
  out += std::format("  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     pinned ? "true" : "false", migrations, peak_rss_bytes());
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < record.refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(record.refusals[i]));
  }
  return out + "]\n}\n";
}

std::string describe_variant(const Options& options, itch::kernels::Selected kernel) {
  std::string text = std::format("{} ({}), kernel {}, {}", experiment_name(*options.experiment),
                                 simd_id(*options.experiment), to_string(kernel.id()),
                                 isa_of(kernel.id()));
  if (options.width != 0) {
    text += std::format(", width {}", options.width);
  }
  if (!options.form.empty()) {
    text += std::format(", form {}", options.form);
  }
  if (options.watchlist != 0) {
    text += std::format(", watchlist {}", options.watchlist);
  }
  return text;
}

// The checks every record gets, after its passes.
void judge(Record& record) {
  for (const TimedPass& pass : record.passes) {
    if (pass.totals.messages != record.mix.messages) {
      record.refusals.push_back(std::format("a {} pass walked {} messages of {}", pass.mode,
                                            pass.totals.messages, record.mix.messages));
    }
    if (pass.totals.refused != 0) {
      record.refusals.push_back(std::format("the book refused {} ops", pass.totals.refused));
    }
  }
  // Passes of one mode compute the same thing, so they must agree.
  for (std::size_t i = 0; i < record.passes.size(); ++i) {
    for (std::size_t j = i + 1; j < record.passes.size(); ++j) {
      const TimedPass& a = record.passes[i];
      const TimedPass& b = record.passes[j];
      if (a.mode == b.mode && (a.totals.fold != b.totals.fold || a.totals.items != b.totals.items)) {
        record.refusals.push_back(std::format("two {} passes disagree", a.mode));
      }
    }
  }
  if (record.mismatches != 0) {
    record.refusals.push_back(std::format("the kernel differed from the scalar kernel {} times in {}",
                                          record.mismatches, record.compared));
  }
}

int list_kernels() {
  for (const KernelId id : itch::kernels::kAllKernels) {
    const auto selected = itch::kernels::select_kernel(to_string(id));
    std::println("{}: {}", to_string(id), selected ? "runs" : selected.error());
  }
  std::println("auto: {}", to_string(itch::kernels::widest()));
  return 0;
}

int run_experiment(const Options& options) {
  const auto kernel = itch::kernels::select_kernel(options.kernel);
  if (!kernel) {
    std::println(stderr, "bench_kernels: {}", kernel.error());
    return 2;
  }
  if (const std::optional<std::string> why = bad_variant(options, kernel->id())) {
    std::println(stderr, "bench_kernels: {}", *why);
    return 2;
  }
  std::optional<itch::tools::MappedFile> file =
      itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_kernels: cannot read {}", options.input);
    return 2;
  }
  // MappedFile asks for sequential access, which lets a kernel drop pages behind the reader.
  ::posix_madvise(const_cast<std::byte*>(file->bytes().data()),  // NOLINT
                  file->bytes().size(), POSIX_MADV_NORMAL);
  const std::span<const std::byte> bytes = file->bytes();

  const SysSource source = live_source();
  const std::string_view host = host_label(source.os, read_cpu_model(source));
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();

  Record record;
  if (kPinningRequired && !pin.pinned) {
    record.refusals.push_back(pinning_field(pin));
  }
  fault_in(bytes);
  record.mix = count_mix(bytes);
  if (*options.experiment == Experiment::batch) {
    record.runs = kx::run_lengths(bytes);
  }
  std::optional<kx::Watchlist> watchlist;
  if (*options.experiment == Experiment::filter) {
    watchlist = most_active(bytes, options.watchlist);
    record.watchlist_listed = watchlist->listed;
    if (watchlist->listed != options.watchlist) {
      record.refusals.push_back(std::format("the input has {} stocks with Add Orders, not {}",
                                            watchlist->listed, options.watchlist));
    }
  }
  CounterGroup group{host};
  Clock clock{bytes, group, record};
  std::size_t overread = 0;
  if (record.refusals.empty()) {
    Passes passes{.options = options,
                  .bytes = bytes,
                  .clock = clock,
                  .record = record,
                  .watchlist = watchlist ? &*watchlist : nullptr};
    itch::kernels::run(*kernel, passes);
    struct Overread {
      template <class K>
      std::size_t operator()() const {
        return K::kOverread;
      }
    } ask;
    overread = itch::kernels::run(*kernel, ask);
  }
  migrations.sample();
  judge(record);
  if (*options.experiment == Experiment::framing && !record.passes.empty() &&
      record.framing_stopped_at != bytes.size()) {
    record.refusals.push_back(std::format("the {} walk stopped at byte {} of {}", options.form,
                                          record.framing_stopped_at, bytes.size()));
  }

  const Residency after = resident(bytes);
  std::vector<HeaderField> header = build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not subtracted: each pass is one span",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = pin.pinned ? std::format("{}", pin.requested_core) : "not pinned",
       .core_number = options.core,
       .counters = record.passes.empty() ? unavailable("no timed pass ran")
                                         : describe_counters(record.passes.back().counters),
       .rdpmc = "not used: each timed pass is counted by the group's read() at its start and stop",
       .page_size = "not applicable: the input is mapped from the page cache",
       .residency = after.describe()});
  header.emplace_back("design", scrub_private(describe_variant(options, *kernel)));
  header.emplace_back("input",
                      "mapped: read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL); every "
                      "page read and found resident before each timed pass, and found resident "
                      "after it");

  std::ofstream out{options.out};
  out << record_json(header, options, *kernel, overread, record, timer, pin.pinned,
                     migrations.migrations());
  if (!out) {
    std::println(stderr, "bench_kernels: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : record.refusals) {
    std::println(stderr, "bench_kernels: refused: {}", refusal);
  }
  return record.refusals.empty() ? 0 : 3;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_kernels --input FILE --out RUN.json --experiment "
                 "field|decode|consume|parse|book|batch|framing|filter|timestamp|verify "
                 "[--kernel scalar|neon|ssse3|avx2|avx512vbmi|wasm|auto] [--width 2|4|6|8] "
                 "[--form NAME] [--watchlist N] [--core N] [--commit TEXT] [--dataset NAME] "
                 "[--dataset-sha256 HEX]\n       bench_kernels --list-kernels");
    return 2;
  }
  return options.list ? list_kernels() : run_experiment(options);
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
