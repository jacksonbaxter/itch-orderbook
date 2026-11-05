// Hardware counts by message type over a whole day, for T-PERF-1 and T-PERF-2. Untimed: the
// counter reads are fenced and the pass's time means nothing.
//
//     bench_counts --input FILE --out RUN.json [--design NAME] [--core N] [--commit TEXT]
//                  [--dataset NAME] [--dataset-sha256 HEX] [--empty-spans N]
//                  [--orders N] [--levels N]
//
// One process is one repetition. It maps the day, reads every page and checks they are all
// resident, builds the book, then runs, with every event open in one group:
//
//   1. --empty-spans spans with nothing in them, to measure what counting costs;
//   2. the pass of typed_counts.hpp, between a read() and an rdpmc of every event on each
//      side, so that the whole pass is counted by the kernel as well;
//   3. the empty spans again, so that a drift in the cost would show.
//
// The spans of step 2 tile the pass, so by type they must add up to the whole pass by
// rdpmc exactly; the record says whether they did, and a run where they did not is refused.
// --design takes any design whose heap does not age: map-aged takes its ageing steps between
// messages, which would land inside the spans. scripts/counts_report.py reads the records.
//
// Exit codes: 0 publishable, 3 refused, with the record still written, 2 bad usage.
#include <sys/mman.h>

#include <atomic>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "designs.hpp"
#include "itch/book.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "replay.hpp"
#include "run_record.hpp"
#include "typed_counts.hpp"

namespace {

using namespace itch::bench;

constexpr std::uint64_t kDefaultEmptySpans = std::uint64_t{1} << 22U;

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  std::uint64_t empty_spans = kDefaultEmptySpans;
  itch::BookCapacity capacity;
  Design design;
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
  } else if (arg == "--core") {
    return parse_number(value, options.core);
  } else if (arg == "--empty-spans") {
    return parse_number(value, options.empty_spans) && options.empty_spans != 0;
  } else if (arg == "--orders") {
    return parse_number(value, options.capacity.orders);
  } else if (arg == "--levels") {
    return parse_number(value, options.capacity.levels);
  } else if (arg == "--design") {
    const std::optional<Design> design = parse_design(value);
    options.design = design.value_or(options.design);
    return design.has_value() && design->kind != DesignKind::map_aged;
  } else {
    return false;
  }
  return true;
}

bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    if (!parse_option(args[i], args[i + 1], options)) {
      return false;
    }
  }
  return args.size() % 2 == 0 && !options.input.empty() && !options.out.empty();
}

// Everything the record holds besides the header.
struct CountsRecord {
  std::vector<std::string> names;
  std::vector<CounterStatus> statuses;
  Agreement agreement;  // read() and rdpmc around the typed pass.
  EmptySpans before;
  EmptySpans after;
  std::optional<TypedPass> pass;
  std::optional<TypeTally> tally;
  itch::bench::MessageMix mix;
  bool tiled = false;
  std::vector<std::string> refusals;
};

std::string list_json(std::span<const std::uint64_t> values) {
  std::string out = "[";
  for (std::size_t i = 0; i < values.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", values[i]);
  }
  return out + "]";
}

std::string empty_json(const EmptySpans& empty) {
  return std::format(R"({{"spans": {}, "missed": {}, "sums": {}}})", empty.spans, empty.missed,
                     list_json(empty.sums));
}

std::string types_json(const TypeTally& tally) {
  std::string out = "[";
  bool first = true;
  std::vector<std::uint64_t> sums(tally.events());
  for (std::size_t slot = 0; slot < kTypeSlots; ++slot) {
    const auto at = static_cast<std::uint8_t>(slot);
    if (tally.spans(at) == 0) {
      continue;
    }
    for (std::size_t event = 0; event < tally.events(); ++event) {
      sums[event] = tally.sum(at, event);
    }
    const std::string type =
        at == kNoMessageSlot ? std::string{"none"} : std::string(1, static_cast<char>(at));
    out += std::format(R"({}{{"type": {}, "spans": {}, "sums": {}}})", first ? "" : ", ",
                       json_string(type), tally.spans(at), list_json(sums));
    first = false;
  }
  return out + "]";
}

std::string record_json(const std::vector<HeaderField>& header, const CountsRecord& record,
                        bool pinned, std::uint64_t migrations) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(header[i].first),
                       json_string(header[i].second));
  }
  out += "\n  },\n  \"events\": [";
  for (std::size_t i = 0; i < record.names.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(record.names[i]));
  }
  out += "],\n  \"status\": [";
  for (std::size_t i = 0; i < record.statuses.size(); ++i) {
    const CounterStatus& s = record.statuses[i];
    out += std::format(R"({}{{"name": {}, "state": {}, "ratio": {:.6f}, "reason": {}}})",
                       i == 0 ? "" : ", ", json_string(s.name), json_string(to_string(s.state)),
                       s.running_ratio, json_string(s.reason));
  }
  out +=
      std::format("],\n  \"agreement\": {},\n", json_string(describe_agreement(record.agreement)));
  std::vector<std::uint64_t> by_read;
  std::vector<std::uint64_t> by_rdpmc;
  for (const EventAgreement& event : record.agreement.events) {
    by_read.push_back(event.by_read);
    by_rdpmc.push_back(event.by_rdpmc);
  }
  out += std::format("  \"around_pass\": {{\"read\": {}, \"rdpmc\": {}}},\n", list_json(by_read),
                     list_json(by_rdpmc));
  out += std::format("  \"mix\": {{\"messages\": {}, \"book_messages\": {}}},\n",
                     record.mix.messages, record.mix.book_messages);
  out += std::format("  \"empty_before\": {},\n  \"empty_after\": {},\n", empty_json(record.before),
                     empty_json(record.after));
  if (record.pass && record.tally) {
    const TypedPass& pass = *record.pass;
    std::vector<std::uint64_t> whole(record.names.size());
    std::vector<std::uint64_t> totals(record.names.size());
    for (std::size_t event = 0; event < record.names.size(); ++event) {
      whole[event] = pass.whole(event);
      totals[event] = record.tally->total(event);
    }
    out += std::format(
        "  \"pass\": {{\"frames\": {}, \"missed\": {}, \"rejected\": {}, \"whole\": {}, "
        "\"by_type_total\": {}, \"tiled\": {}}},\n",
        pass.frames, pass.missed, pass.rejected, list_json(whole), list_json(totals),
        record.tiled ? "true" : "false");
    out += std::format("  \"types\": {},\n", types_json(*record.tally));
  } else {
    out += "  \"pass\": null,\n  \"types\": [],\n";
  }
  out += std::format("  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     pinned ? "true" : "false", migrations, peak_rss_bytes());
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < record.refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(record.refusals[i]));
  }
  return out + "]\n}\n";
}

// Reads every page, so that the residency asked next is the file's and not the reader's.
void fault_in(std::span<const std::byte> bytes) {
  static std::atomic<std::byte> seen{std::byte{0}};
  std::byte any{0};
  for (std::size_t at = 0; at < bytes.size(); at += base_page_size()) {
    any |= bytes[at];
  }
  seen = any;
}

// Every refusal the counts give, after the pass.
void judge(CountsRecord& record) {
  const auto refuse = [&record](std::string why) { record.refusals.push_back(std::move(why)); };
  if (record.names.empty()) {
    refuse("no counter opened: no user-mode rdpmc on this host");
    return;
  }
  for (const CounterStatus& status : record.statuses) {
    if (status.state != CounterState::measured) {
      refuse(std::format("{} was not measured for the whole span: {}", status.name,
                         describe_counter(status)));
    }
  }
  if (record.agreement.state != AgreementState::agreed) {
    refuse("rdpmc and read() around the pass: " + describe_agreement(record.agreement));
  }
  if (record.before.missed + record.after.missed != 0) {
    refuse("an empty span's read gave no value");
  }
  if (!record.pass) {
    refuse("the typed pass did not run");
    return;
  }
  if (record.pass->missed != 0) {
    refuse(std::format("{} reads in the typed pass gave no value", record.pass->missed));
  }
  if (record.pass->rejected != 0) {
    refuse(std::format("the book rejected {} messages", record.pass->rejected));
  }
  if (!record.tiled) {
    refuse("the spans by type do not add up to the whole pass");
  }
}

template <class Book, class MakeBook>
void count_design(std::span<const std::byte> bytes, const MakeBook& make_book, CounterGroup& group,
                  std::uint64_t empty, CountsRecord& record) {
  const std::unique_ptr<Book> book = make_book();
  record.names = group.event_names();
  TypeTally tally{group.event_count()};
  TypedPass pass;
  group.start();
  record.before = empty_spans(group, empty);
  record.agreement =
      cross_check(group, [&] { pass = typed_counts_pass(bytes, *book, group, tally); });
  record.after = empty_spans(group, empty);
  group.stop();
  record.statuses = group.statuses();
  record.tiled = spans_tile(pass, tally);
  record.pass = std::move(pass);
  record.tally = std::move(tally);
}

std::string describe_design(const Options& options) {
  if (options.design.kind == DesignKind::map_fresh) {
    return "map-fresh: MapBook, std::map levels with std::list FIFOs and a std::unordered_map "
           "index, on the process's heap as it comes; no other book built in this process";
  }
  const itch::BookCapacity& capacity = options.capacity;
  return std::format("{}: FastBook, {} orders and {} levels; no other book built in this process",
                     design_name(options.design), capacity.orders, capacity.levels);
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_counts --input FILE --out RUN.json [--design NAME] [--core N] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX] [--empty-spans N] "
                 "[--orders N] [--levels N]; map-aged is not offered");
    return 2;
  }
  std::optional<itch::tools::MappedFile> file =
      itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_counts: cannot read {}", options.input);
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

  CountsRecord record;
  if (kPinningRequired && !pin.pinned) {
    record.refusals.push_back(pinning_field(pin));
  }
  fault_in(bytes);
  record.mix = count_mix(bytes);
  const Residency before = resident(bytes);
  if (!before.complete()) {
    record.refusals.push_back("input not resident: " + before.describe());
  }
  CounterGroup group{host};
  if (record.refusals.empty()) {
    if (options.design.kind == DesignKind::map_fresh) {
      count_design<itch::MapBook>(
          bytes, [] { return std::make_unique<itch::MapBook>(); }, group, options.empty_spans,
          record);
    } else {
      itch::tools::with_book_arm(options.design.arm, [&]<class Index, class Levels> {
        using Book = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
        count_design<Book>(
            bytes, [&] { return std::make_unique<Book>(options.capacity); }, group,
            options.empty_spans, record);
      });
    }
    judge(record);
  }
  migrations.sample();
  const Residency after = resident(bytes);
  if (record.refusals.empty() && !after.complete()) {
    record.refusals.push_back("input left memory during the pass: " + after.describe());
  }

  std::vector<HeaderField> header = build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, not used: this pass is not timed",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = pin.pinned ? std::format("{}", pin.requested_core) : "not pinned",
       .core_number = options.core,
       .counters = describe_counters(record.statuses),
       .rdpmc = describe_agreement(record.agreement),
       .page_size = "not applicable: the input is mapped from the page cache",
       .residency = after.describe()});
  header.emplace_back("design", scrub_private(describe_design(options)));
  header.emplace_back("input",
                      "mapped: read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL); every "
                      "page read and found resident before the pass, and found resident after it");

  std::ofstream out{options.out};
  out << record_json(header, record, pin.pinned, migrations.migrations());
  if (!out) {
    std::println(stderr, "bench_counts: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : record.refusals) {
    std::println(stderr, "bench_counts: refused: {}", refusal);
  }
  return record.refusals.empty() ? 0 : 3;
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
