// Three experiments on the book's hot loop, each variant picked at the command line, all in
// one binary, over one day mapped from the page cache:
//
//     bench_variants --input FILE --out RUN.json [--pass replay|lookup] [--design NAME]
//                    [--dispatch switch|table|threaded|predicate] [--prefetch D]
//                    [--core N] [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]
//                    [--orders N] [--levels N]
//
// --pass replay, the default: a warm-up pass and then one timed pass of the whole day, both
//               with the same --dispatch form (dispatch.hpp, E7) and the same --prefetch
//               look-ahead (itch/lookahead.hpp, E8), each on a fresh book.
// --pass lookup: the order lookup apart from the rest of the message (lookup_script.hpp).
//               A recording pass, which is also the warm-up, then four timed passes on
//               fresh books in the order check, answer, answer, check, so that a drift
//               over the process falls on both modes alike.
// --design      one of the four arms of the fast book; paged-flat, the default arm, if left
//               out. The predicate form builds no book.
// --prefetch    0, the default, or a look-ahead the arm takes: from its number of stages
//               to 64. Only with --dispatch switch and --pass replay.
//
// Every timed pass runs between a start and a stop of one counter group, outside the
// clock: on debian cycles, instructions, branch misses, cache-misses (L2 misses on Zen 1)
// and r0843:u (a floor on DRAM demand fills), all :u; on macOS the cycles and instructions
// thread_selfcounts gives, user and kernel together. Every page of the input is read and
// found resident before each timed pass and found resident after it.
//
// A variant this build or this arm cannot run, the threaded form without
// [[clang::musttail]] among them, exits 2 with the reason. It never runs another variant
// in its place. Exit codes: 0 publishable, 3 refused with the record written, 2 bad usage.
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
#include "dispatch.hpp"
#include "itch/book.hpp"
#include "itch/lookahead.hpp"
#include "lookup_script.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "replay.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;

enum class PassKind : std::uint8_t { replay, lookup };

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  itch::BookCapacity capacity;
  itch::tools::BookArm arm;
  PassKind pass = PassKind::replay;
  DispatchForm dispatch = DispatchForm::switch_case;
  std::uint64_t prefetch = 0;
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
  } else if (arg == "--orders") {
    return parse_number(value, options.capacity.orders);
  } else if (arg == "--levels") {
    return parse_number(value, options.capacity.levels);
  } else if (arg == "--prefetch") {
    return parse_number(value, options.prefetch);
  } else if (arg == "--design") {
    const std::optional<Design> design = parse_design(value);
    if (!design || design->kind != DesignKind::fast) {
      return false;
    }
    options.arm = design->arm;
  } else if (arg == "--pass") {
    if (value != "replay" && value != "lookup") {
      return false;
    }
    options.pass = value == "replay" ? PassKind::replay : PassKind::lookup;
  } else if (arg == "--dispatch") {
    const std::optional<DispatchForm> form = parse_dispatch(value);
    options.dispatch = form.value_or(options.dispatch);
    return form.has_value();
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

// Why this variant cannot run here, or nothing. Asked before anything is mapped.
template <class Book>
std::optional<std::string> unsupported(const Options& options) {
  if (options.dispatch == DispatchForm::threaded && !kThreadedBuilt) {
    return "--dispatch threaded needs [[clang::musttail]], which this compiler does not have, "
           "so the form is not built";
  }
  if (options.prefetch != 0 &&
      (options.dispatch != DispatchForm::switch_case || options.pass != PassKind::replay)) {
    return "--prefetch runs only with --dispatch switch and --pass replay";
  }
  if (options.pass == PassKind::lookup && options.dispatch != DispatchForm::switch_case) {
    return "--pass lookup runs only with --dispatch switch";
  }
  if (options.prefetch > itch::LookaheadReader<Book>::kMostAhead ||
      !itch::LookaheadReader<Book>::valid_distance(static_cast<unsigned>(options.prefetch))) {
    return std::format("--prefetch {} is neither 0 nor {} to {} for this arm", options.prefetch,
                       Book::kPrefetchStages, itch::LookaheadReader<Book>::kMostAhead);
  }
  return std::nullopt;
}

// One timed pass: its span, what it did, and the counter group over the same span.
struct TimedPass {
  std::string mode;  // replay, or check or answer for a lookup pass.
  std::uint64_t messages = 0;
  std::uint64_t ticks = 0;
  std::uint64_t rejected = 0;
  std::uint64_t fold = 0;
  std::uint64_t lookups = 0;     // Script entries the pass read.
  std::uint64_t mismatches = 0;  // Entries that differed from the real find.
  bool script_exact = true;
  std::uint64_t live_orders = 0;  // The book at the end of the pass.
  std::uint64_t live_levels = 0;
  std::vector<CounterStatus> counters;
  std::string residency_before;
  std::string residency_after;
};

struct Record {
  std::vector<TimedPass> passes;
  MessageMix mix;
  std::uint64_t warmup_messages = 0;
  std::uint64_t script_entries = 0;  // Lookups the recording pass made.
  std::uint64_t script_capacity = 0;
  std::string script_arena;
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

// Runs `work` as one timed pass, between the counter group's start and stop, with the input
// checked resident on both sides of it.
template <class Work>
TimedPass timed(std::span<const std::byte> bytes, CounterGroup& group, Record& record,
                std::string mode, const Work& work) {
  TimedPass pass;
  pass.mode = std::move(mode);
  fault_in(bytes);
  const Residency before = resident(bytes);
  pass.residency_before = before.describe();
  if (!before.complete()) {
    record.refusals.push_back("input not resident before a timed pass: " + before.describe());
    return pass;
  }
  group.start();
  const std::uint64_t started = ticks_start();
  const FormTotals totals = work(pass);
  const std::uint64_t stopped = ticks_stop();
  group.stop();
  pass.ticks = stopped - started;
  pass.counters = group.statuses();
  pass.messages = totals.messages;
  pass.rejected = totals.rejected;
  pass.fold = totals.fold;
  const Residency after = resident(bytes);
  pass.residency_after = after.describe();
  if (!after.complete()) {
    record.refusals.push_back("input left memory during a timed pass: " + after.describe());
  }
  if (pass.rejected != 0) {
    record.refusals.push_back(std::format("the book rejected {} messages", pass.rejected));
  }
  return pass;
}

template <class Book>
FormTotals form_pass(DispatchForm form, std::span<const std::byte> bytes, Book& book) {
  switch (form) {
    case DispatchForm::switch_case: return switch_pass(bytes, book);
    case DispatchForm::table: return TableForm<Book>::pass(bytes, book);
    case DispatchForm::threaded:
#ifdef __has_cpp_attribute
#if __has_cpp_attribute(clang::musttail)
      return ThreadedForm<Book>::pass(bytes, book);
#endif
#endif
      break;
    case DispatchForm::predicate: return predicate_pass(bytes);
  }
  return {};
}

// The switch form through the look-ahead reader. With distance 0 it is switch_pass().
template <class Book>
FormTotals lookahead_pass(std::span<const std::byte> bytes, Book& book, unsigned distance) {
  itch::LookaheadReader<Book> reader{bytes, book, distance};
  FormTotals totals;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::message) {
      ++totals.messages;
      totals.rejected += refused(book.apply(frame.payload)) ? 1U : 0U;
    }
  }
  return totals;
}

template <class Book>
void run_replay(const Options& options, std::span<const std::byte> bytes, CounterGroup& group,
                Record& record) {
  if (options.dispatch == DispatchForm::predicate) {
    record.warmup_messages = predicate_pass(bytes).messages;
    record.passes.push_back(
        timed(bytes, group, record, "replay", [&](TimedPass&) { return predicate_pass(bytes); }));
    return;
  }
  const auto distance = static_cast<unsigned>(options.prefetch);
  const auto one_pass = [&](Book& book) {
    return distance != 0 ? lookahead_pass(bytes, book, distance)
                         : form_pass(options.dispatch, bytes, book);
  };
  {
    const auto book = std::make_unique<Book>(options.capacity);
    record.warmup_messages = one_pass(*book).messages;
  }
  const auto book = std::make_unique<Book>(options.capacity);
  TimedPass pass =
      timed(bytes, group, record, "replay", [&](TimedPass&) { return one_pass(*book); });
  pass.live_orders = book->live_orders();
  pass.live_levels = book->live_levels();
  record.passes.push_back(std::move(pass));
}

// Every find() a day can ask for: one for each add, delete and reduce, two for a replace.
std::uint64_t most_lookups(const MessageMix& mix) {
  const auto ops = [&mix](OpClass op) { return mix.by_op[static_cast<std::size_t>(op)]; };
  return ops(OpClass::add) + ops(OpClass::del) + ops(OpClass::reduce) + (2 * ops(OpClass::replace));
}

template <class Index, class Levels>
void run_lookup(const Options& options, std::span<const std::byte> bytes, CounterGroup& group,
                Record& record) {
  const std::uint64_t capacity = most_lookups(record.mix);
  const std::optional<Arena> arena = Arena::make(capacity * sizeof(std::uint32_t));
  if (!arena) {
    record.refusals.emplace_back("no arena for the lookup script");
    return;
  }
  record.script_capacity = capacity;
  record.script_arena = describe_arena(arena->report());
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
  LookupScript script{{reinterpret_cast<std::uint32_t*>(arena->data()), capacity}};
  current_script() = &script;
  using Recording =
      itch::FastBook<itch::NoWriteObserver, ScriptedIndex<Index, ScriptMode::record>, Levels>;
  {
    const auto book = std::make_unique<Recording>(options.capacity);
    record.warmup_messages = switch_pass(bytes, *book).messages;
  }
  record.script_entries = script.recorded();
  if (script.overrun()) {
    record.refusals.emplace_back("the day made more lookups than the script could hold");
  }
  const auto scripted = [&]<ScriptMode Mode>(std::string mode) {
    using Book = itch::FastBook<itch::NoWriteObserver, ScriptedIndex<Index, Mode>, Levels>;
    const auto book = std::make_unique<Book>(options.capacity);
    script.rewind();
    TimedPass pass = timed(bytes, group, record, std::move(mode),
                           [&](TimedPass&) { return switch_pass(bytes, *book); });
    pass.lookups = script.read();
    pass.mismatches = script.mismatches();
    pass.script_exact = script.consumed_exactly();
    pass.live_orders = book->live_orders();
    pass.live_levels = book->live_levels();
    if (!pass.script_exact) {
      record.refusals.push_back(std::format(
          "a {} pass read {} of {} script entries with {} mismatches{}", pass.mode, pass.lookups,
          script.recorded(), pass.mismatches, script.overrun() ? ", and ran past the end" : ""));
    }
    record.passes.push_back(std::move(pass));
  };
  if (record.refusals.empty()) {
    scripted.template operator()<ScriptMode::check>("check");
    scripted.template operator()<ScriptMode::answer>("answer");
    scripted.template operator()<ScriptMode::answer>("answer");
    scripted.template operator()<ScriptMode::check>("check");
  }
  current_script() = nullptr;
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

std::string record_json(const std::vector<HeaderField>& header, const Options& options,
                        const Record& record, const TimerInfo& timer, bool pinned,
                        std::uint64_t migrations) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(header[i].first),
                       json_string(header[i].second));
  }
  out += "\n  },\n";
  out += std::format(
      "  \"variant\": {{\"pass\": {}, \"design\": {}, \"dispatch\": {}, \"prefetch\": {}, "
      "\"threaded_built\": {}}},\n",
      json_string(options.pass == PassKind::replay ? "replay" : "lookup"),
      json_string(design_name({.kind = DesignKind::fast, .arm = options.arm})),
      json_string(to_string(options.dispatch)), options.prefetch,
      kThreadedBuilt ? "true" : "false");
  out += std::format(R"(  "mix": {{"messages": {}, "book_messages": {}}},)", record.mix.messages,
                     record.mix.book_messages);
  out += std::format("\n  \"warmup_messages\": {},\n", record.warmup_messages);
  out +=
      std::format("  \"script\": {{\"entries\": {}, \"capacity\": {}, \"arena\": {}}},\n",
                  record.script_entries, record.script_capacity, json_string(record.script_arena));
  out += "  \"passes\": [";
  for (std::size_t i = 0; i < record.passes.size(); ++i) {
    const TimedPass& p = record.passes[i];
    const double seconds = timer.ticks_to_ns(static_cast<double>(p.ticks)) / 1e9;
    out += std::format(
        "{}\n    {{\"mode\": {}, \"messages\": {}, \"seconds\": {:.6f}, \"rejected\": {}, "
        "\"fold\": {}, \"lookups\": {}, \"mismatches\": {}, \"script_exact\": {}, "
        "\"live_orders\": {}, \"live_levels\": {}, \"residency_before\": {}, "
        "\"residency_after\": {}, \"counters\": {}}}",
        i == 0 ? "" : ",", json_string(p.mode), p.messages, seconds, p.rejected, p.fold, p.lookups,
        p.mismatches, p.script_exact ? "true" : "false", p.live_orders, p.live_levels,
        json_string(p.residency_before), json_string(p.residency_after), counters_json(p.counters));
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

std::string describe_variant(const Options& options) {
  const std::string design = design_name({.kind = DesignKind::fast, .arm = options.arm});
  if (options.pass == PassKind::lookup) {
    return std::format(
        "{}: FastBook with its index wrapped by ScriptedIndex; a recording pass as the warm-up, "
        "then check, answer, answer, check, each on a fresh book",
        design);
  }
  if (options.dispatch == DispatchForm::predicate) {
    return "predicate: no book; the frame walk and every message's header fields";
  }
  return std::format(
      "{}: FastBook, {} dispatch, look-ahead {}; a warm-up pass and a timed pass, "
      "each on a fresh book",
      design, to_string(options.dispatch), options.prefetch);
}

template <class Index, class Levels>
int run_variant(const Options& options) {
  using Book = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
  if (const std::optional<std::string> why = unsupported<Book>(options)) {
    std::println(stderr, "bench_variants: {}", *why);
    return 2;
  }
  std::optional<itch::tools::MappedFile> file =
      itch::tools::MappedFile::open(options.input.c_str());
  if (!file) {
    std::println(stderr, "bench_variants: cannot read {}", options.input);
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
  CounterGroup group{host};
  if (record.refusals.empty()) {
    if (options.pass == PassKind::lookup) {
      run_lookup<Index, Levels>(options, bytes, group, record);
    } else {
      run_replay<Book>(options, bytes, group, record);
    }
  }
  migrations.sample();
  for (const TimedPass& pass : record.passes) {
    if (pass.messages != record.mix.messages) {
      record.refusals.push_back(std::format("a {} pass walked {} messages of {}", pass.mode,
                                            pass.messages, record.mix.messages));
    }
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
       .page_size = record.script_arena.empty() ? "not applicable: the input is mapped from the "
                                                  "page cache"
                                                : "lookup script: " + record.script_arena,
       .residency = after.describe()});
  header.emplace_back("design", scrub_private(describe_variant(options)));
  header.emplace_back("input",
                      "mapped: read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL); every "
                      "page read and found resident before each timed pass, and found resident "
                      "after it");

  std::ofstream out{options.out};
  out << record_json(header, options, record, timer, pin.pinned, migrations.migrations());
  if (!out) {
    std::println(stderr, "bench_variants: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : record.refusals) {
    std::println(stderr, "bench_variants: refused: {}", refusal);
  }
  return record.refusals.empty() ? 0 : 3;
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_variants --input FILE --out RUN.json [--pass replay|lookup] "
                 "[--design hash-linked|hash-flat|paged-linked|paged-flat] "
                 "[--dispatch switch|table|threaded|predicate] [--prefetch D] [--core N] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX] [--orders N] "
                 "[--levels N]");
    return 2;
  }
  return itch::tools::with_book_arm(
      options.arm, [&]<class Index, class Levels> { return run_variant<Index, Levels>(options); });
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
