// Three experiments on the book's hot loop, each variant picked at the command line, all in
// one binary, over one day mapped from the page cache:
//
//     bench_variants --input FILE --out RUN.json [--pass replay|lookup|types] [--design NAME]
//                    [--dispatch switch|table|threaded|predicate] [--prefetch D]
//                    [--stages DIGITS] [--kinds all|named|added] [--block N] [--messages N]
//                    [--core N] [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]
//                    [--orders N] [--levels N] [--max-pages N] [--index-bits N] [--max-slots N]
//
// --pass replay, the default: a warm-up pass and then one timed pass of the whole day, both
//               with the same --dispatch form (dispatch.hpp, E7) and the same --prefetch
//               look-ahead (itch/lookahead.hpp, E8), each on a fresh book.
// --pass lookup: the order lookup apart from the rest of the message (lookup_script.hpp).
//               A recording pass, which is also the warm-up, then four timed passes on
//               fresh books in the order check, answer, answer, check, so that a drift
//               over the process falls on both modes alike. With --block, each of the four
//               is timed by blocks as --pass types times its typed passes.
// --pass types: the cost of each message type (typed_time.hpp). A warm-up pass, then four
//               timed passes on fresh books in the order replay, typed, typed, replay: a
//               replay pass is one span, as --pass replay's; a typed pass reads the clock
//               after every --block frames, 64 if left out, and keeps the sums a fit of
//               each block's ticks on its counts by type needs. The replay passes less the
//               typed ones is what the blocks' reads and counts cost.
// --messages    only the first N messages of the input, the book built from its start, for
//               a day too large to hold in memory with its book. Every page of those
//               messages is read and found resident, and no page after them is read.
// --design      any arm of the fast book (designs.hpp), carteret's layout experiments'
//               among them; paged-flat, the default arm, if left out. The predicate form
//               builds no book.
// --orders, --levels, --max-pages, --index-bits, --max-slots  the book's capacity, as
//               itch_oracle takes it: BookCapacity's defaults unless given.
// --prefetch    0, the default, or a look-ahead the arm takes: from its number of stages
//               to 64. Only with --dispatch switch and --pass replay.
// --stages      with --prefetch, start only these stages, each a digit below the arm's
//               number of stages, through StageFilter (stage_filter.hpp), and with
// --kinds       only for these messages: named (E, C, X, D, U), added (A, F) or all. Either
//               one sends the look-ahead through the filter, so that a pass of every stage
//               for every kind pays what a pass of one stage pays to be filtered.
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
#include "stage_filter.hpp"
#include "typed_time.hpp"

namespace {

using namespace itch::bench;

enum class PassKind : std::uint8_t { replay, lookup, types };

constexpr std::uint32_t kDefaultBlock = 64;

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
  std::string stages;  // Empty: no filter.
  StageKinds kinds = StageKinds::all;
  bool filtered = false;
  std::uint32_t block = 0;    // Frames a typed pass reads the clock after. 0: whole passes.
  std::uint64_t messages = 0;  // Messages of the input to run. 0: all of them.
};

std::string_view to_string(PassKind kind) {
  switch (kind) {
    case PassKind::replay: return "replay";
    case PassKind::lookup: return "lookup";
    case PassKind::types: return "types";
  }
  return "replay";
}

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
  } else if (arg == "--max-pages") {
    return parse_number(value, options.capacity.index_pages);
  } else if (arg == "--index-bits") {
    return parse_number(value, options.capacity.index_bits);
  } else if (arg == "--max-slots") {
    return parse_number(value, options.capacity.level_slots);
  } else if (arg == "--block") {
    return parse_number(value, options.block) && options.block != 0;
  } else if (arg == "--messages") {
    return parse_number(value, options.messages) && options.messages != 0;
  } else if (arg == "--prefetch") {
    return parse_number(value, options.prefetch);
  } else if (arg == "--stages") {
    options.stages = value;
    options.filtered = true;
    return parse_stages(value).has_value();
  } else if (arg == "--kinds") {
    const std::optional<StageKinds> kinds = parse_stage_kinds(value);
    options.kinds = kinds.value_or(options.kinds);
    options.filtered = true;
    return kinds.has_value();
  } else if (arg == "--design") {
    const std::optional<Design> design = parse_design(value);
    if (!design || design->kind != DesignKind::fast) {
      return false;
    }
    options.arm = design->arm;
  } else if (arg == "--pass") {
    if (value == "replay") {
      options.pass = PassKind::replay;
    } else if (value == "lookup") {
      options.pass = PassKind::lookup;
    } else if (value == "types") {
      options.pass = PassKind::types;
    } else {
      return false;
    }
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
  if (options.pass == PassKind::types && options.block == 0) {
    options.block = kDefaultBlock;
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
  if (options.pass != PassKind::replay && options.dispatch != DispatchForm::switch_case) {
    return std::format("--pass {} runs only with --dispatch switch", to_string(options.pass));
  }
  if (options.block != 0 && options.pass == PassKind::replay) {
    return "--block runs only with --pass types or --pass lookup";
  }
  if (options.filtered && options.pass != PassKind::replay) {
    return std::format("--stages and --kinds run only with --pass replay, not --pass {}",
                       to_string(options.pass));
  }
  if (options.filtered && options.prefetch == 0) {
    return "--stages and --kinds run only with a --prefetch look-ahead";
  }
  if (options.filtered && !options.stages.empty() &&
      parse_stages(options.stages).value_or(0U) >= (1U << Book::kPrefetchStages)) {
    return std::format("--stages {} names a stage this arm does not have: it has {}",
                       options.stages, Book::kPrefetchStages);
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
  std::optional<TypedTime> typed;  // A pass timed by blocks.
  std::vector<CounterStatus> counters;
  std::string residency_before;
  std::string residency_after;
};

struct Record {
  std::vector<TimedPass> passes;
  MessageMix mix;
  std::uint64_t frames = 0;
  std::array<std::uint64_t, 256> by_type{};  // Messages by type byte.
  std::uint64_t input_bytes = 0;             // Of the input run: all of it, or its first --messages.
  std::uint64_t file_bytes = 0;
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
  if (pass.typed && !pass.typed->tiled()) {
    record.refusals.push_back(std::format("a {} pass's blocks add up to {} ticks of {}",
                                          pass.mode, pass.typed->block_ticks(),
                                          pass.typed->whole()));
  }
  return pass;
}

// A pass of the switch form timed by blocks (typed_time.hpp), as FormTotals for timed().
template <class Book>
FormTotals typed_pass(std::span<const std::byte> bytes, Book& book, const Options& options,
                      const Record& record, const TimerInfo& timer, TimedPass& pass) {
  auto clock = [] { return ticks_start(); };
  pass.typed = typed_time_pass(bytes, book, options.block, record.frames,
                               static_cast<std::uint64_t>(timer.floor_ticks()), clock);
  return {.messages = pass.typed->messages, .rejected = pass.typed->rejected, .fold = 0};
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

// The switch form through the look-ahead reader. With distance 0 it is switch_pass(). Ahead
// is the book, or a StageFilter of it.
template <class Book, class Ahead = Book>
FormTotals lookahead_pass(std::span<const std::byte> bytes, Book& book, const Ahead& ahead,
                          unsigned distance) {
  itch::LookaheadReader<Ahead> reader{bytes, ahead, distance};
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
  const unsigned stages = options.stages.empty() ? (1U << Book::kPrefetchStages) - 1U
                                                 : parse_stages(options.stages).value_or(0U);
  const auto one_pass = [&](Book& book) {
    if (distance != 0 && options.filtered) {
      return lookahead_pass(bytes, book, StageFilter<Book>{book, stages, options.kinds}, distance);
    }
    return distance != 0 ? lookahead_pass(bytes, book, book, distance)
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

// The cost of each type: a warm-up, then replay, typed, typed, replay, each on a fresh book,
// so that a drift over the process falls on both kinds of pass alike.
template <class Book>
void run_types(const Options& options, std::span<const std::byte> bytes, CounterGroup& group,
               const TimerInfo& timer, Record& record) {
  {
    const auto book = std::make_unique<Book>(options.capacity);
    record.warmup_messages = switch_pass(bytes, *book).messages;
  }
  for (const bool by_blocks : {false, true, true, false}) {
    const auto book = std::make_unique<Book>(options.capacity);
    TimedPass pass = timed(bytes, group, record, by_blocks ? "typed" : "replay",
                           [&](TimedPass& one) {
                             return by_blocks ? typed_pass(bytes, *book, options, record, timer, one)
                                              : switch_pass(bytes, *book);
                           });
    pass.live_orders = book->live_orders();
    pass.live_levels = book->live_levels();
    record.passes.push_back(std::move(pass));
  }
}

// Every find() a day can ask for: one for each add, delete and reduce, two for a replace.
std::uint64_t most_lookups(const MessageMix& mix) {
  const auto ops = [&mix](OpClass op) { return mix.by_op[static_cast<std::size_t>(op)]; };
  return ops(OpClass::add) + ops(OpClass::del) + ops(OpClass::reduce) + (2 * ops(OpClass::replace));
}

template <class Index, class Levels>
void run_lookup(const Options& options, std::span<const std::byte> bytes, CounterGroup& group,
                const TimerInfo& timer, Record& record) {
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
    TimedPass pass = timed(bytes, group, record, std::move(mode), [&](TimedPass& one) {
      return options.block == 0 ? switch_pass(bytes, *book)
                                : typed_pass(bytes, *book, options, record, timer, one);
    });
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

std::string list_json(std::span<const std::uint64_t> values) {
  std::string out = "[";
  for (std::size_t i = 0; i < values.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", values[i]);
  }
  return out + "]";
}

// A typed pass's sums, quarter by quarter, every one an exact integer.
std::string typed_json(const TypedTime& typed) {
  std::string out = std::format(
      R"({{"block": {}, "columns": {}, "first": {}, "last": {}, "frames": {}, "quarters": [)",
      typed.block, json_string(std::format("{}*", kTimedTypes)), typed.first, typed.last,
      typed.frames);
  for (std::size_t i = 0; i < typed.quarters.size(); ++i) {
    const BlockSums& q = typed.quarters[i];
    out += std::format(
        R"({}{{"blocks": {}, "ticks": {}, "ticks_squared": {}, "under_floor": {}, )"
        R"("most_ticks": {}, "counts": {}, "cross": {}, "gram": {}}})",
        i == 0 ? "" : ", ", q.blocks, q.ticks, q.ticks_squared, q.under_floor, q.most_ticks,
        list_json(q.counts), list_json(q.cross), list_json(q.gram));
  }
  return out + "]}";
}

// The messages of each type the input holds, by type character.
std::string types_json(const std::array<std::uint64_t, 256>& by_type) {
  std::string out = "{";
  bool first = true;
  for (std::size_t type = 0; type < by_type.size(); ++type) {
    if (by_type[type] != 0) {
      out += std::format("{}{}: {}", first ? "" : ", ",
                         json_string(std::string(1, static_cast<char>(type))), by_type[type]);
      first = false;
    }
  }
  return out + "}";
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
  // The filter's fields only when it ran, so that an unfiltered record reads as before.
  const std::string filter =
      options.filtered ? std::format(R"("filtered": true, "stages": {}, "kinds": {}, )",
                                     json_string(options.stages.empty() ? "all" : options.stages),
                                     json_string(to_string(options.kinds)))
                       : std::string{};
  out += std::format(
      "  \"variant\": {{\"pass\": {}, \"design\": {}, \"dispatch\": {}, \"prefetch\": {}, "
      "{}\"threaded_built\": {}{}}},\n",
      json_string(to_string(options.pass)),
      json_string(design_name({.kind = DesignKind::fast, .arm = options.arm})),
      json_string(to_string(options.dispatch)), options.prefetch, filter,
      kThreadedBuilt ? "true" : "false",
      options.block == 0 && options.messages == 0
          ? std::string{}
          : std::format(R"(, "block": {}, "first_messages": {})", options.block, options.messages));
  out += std::format(R"(  "mix": {{"messages": {}, "book_messages": {}}},)", record.mix.messages,
                     record.mix.book_messages);
  out += std::format("\n  \"warmup_messages\": {},\n", record.warmup_messages);
  out += std::format(
      "  \"input\": {{\"frames\": {}, \"bytes\": {}, \"file_bytes\": {}, \"types\": {}}},\n",
      record.frames, record.input_bytes, record.file_bytes, types_json(record.by_type));
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
        "\"residency_after\": {}, \"counters\": {}{}}}",
        i == 0 ? "" : ",", json_string(p.mode), p.messages, seconds, p.rejected, p.fold, p.lookups,
        p.mismatches, p.script_exact ? "true" : "false", p.live_orders, p.live_levels,
        json_string(p.residency_before), json_string(p.residency_after), counters_json(p.counters),
        p.typed ? ", \"typed\": " + typed_json(*p.typed) : std::string{});
  }
  out += "\n  ],\n";
  out += std::format("  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     pinned ? "true" : "false", migrations, peak_rss_bytes());
  out += std::format("  \"ns_per_tick\": {:.9f},\n", timer.ticks_to_ns(1.0));
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < record.refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(record.refusals[i]));
  }
  return out + "]\n}\n";
}

std::string describe_variant(const Options& options) {
  const std::string design = design_name({.kind = DesignKind::fast, .arm = options.arm});
  const itch::BookCapacity& c = options.capacity;
  const std::string capacity =
      std::format("{} orders, {} levels, {} index pages, index reach 2^{}, {} level slots",
                  c.orders, c.levels, c.index_pages, c.index_bits, c.level_slots);
  const std::string blocks =
      options.block == 0 ? std::string{}
                         : std::format(", each timed by blocks of {} frames", options.block);
  if (options.pass == PassKind::lookup) {
    return std::format(
        "{}: FastBook with its index wrapped by ScriptedIndex, {}; a recording pass as the "
        "warm-up, then check, answer, answer, check, each on a fresh book{}",
        design, capacity, blocks);
  }
  if (options.pass == PassKind::types) {
    return std::format(
        "{}: FastBook, {}; a warm-up pass, then replay, typed, typed, replay, each on a fresh "
        "book, the typed passes timed by blocks of {} frames",
        design, capacity, options.block);
  }
  if (options.dispatch == DispatchForm::predicate) {
    return "predicate: no book; the frame walk and every message's header fields";
  }
  const std::string filter =
      options.filtered
          ? std::format(" through StageFilter, stages {}, kinds {}",
                        options.stages.empty() ? "all" : options.stages, to_string(options.kinds))
          : "";
  return std::format(
      "{}: FastBook, {} dispatch, look-ahead {}{}; a warm-up pass and a timed pass, "
      "each on a fresh book",
      design, to_string(options.dispatch), options.prefetch, filter);
}

// The input up to the end of its first `messages` messages, all of it for 0, or nothing if it
// holds fewer. Walks the frames, so it reads each page of what it returns.
std::span<const std::byte> first_messages(std::span<const std::byte> bytes,
                                          std::uint64_t messages) {
  if (messages == 0) {
    return bytes;
  }
  itch::FrameReader reader{bytes};
  std::uint64_t seen = 0;
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status);
       frame = reader.next()) {
    seen += frame.status == itch::FrameStatus::message ? 1U : 0U;
    if (seen == messages) {
      return bytes.first(reader.consumed());
    }
  }
  return {};
}

// Frames and messages by type byte, of the input as run.
void count_types(std::span<const std::byte> bytes, Record& record) {
  itch::FrameReader reader{bytes};
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status);
       frame = reader.next()) {
    ++record.frames;
    if (frame.status == itch::FrameStatus::message) {
      ++record.by_type[static_cast<std::uint8_t>(itch::read_char(frame.payload.data()))];
    }
  }
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
  const std::span<const std::byte> bytes = first_messages(file->bytes(), options.messages);
  if (bytes.empty()) {
    std::println(stderr, "bench_variants: {} holds fewer than {} messages", options.input,
                 options.messages);
    return 2;
  }

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
  count_types(bytes, record);
  record.input_bytes = bytes.size();
  record.file_bytes = file->bytes().size();
  CounterGroup group{host};
  if (record.refusals.empty()) {
    if (options.pass == PassKind::lookup) {
      run_lookup<Index, Levels>(options, bytes, group, timer, record);
    } else if (options.pass == PassKind::types) {
      run_types<Book>(options, bytes, group, timer, record);
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
  header.emplace_back(
      "input",
      std::format("mapped: read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL); {}every "
                  "page read and found resident before each timed pass, and found resident "
                  "after it",
                  options.messages == 0
                      ? std::string{}
                      : std::format("the first {} messages, {} of the file's {} bytes, the "
                                    "book built from the start; ",
                                    options.messages, bytes.size(), file->bytes().size())));

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
                 "usage: bench_variants --input FILE --out RUN.json "
                 "[--pass replay|lookup|types] [--design INDEX-LEVELS] "
                 "[--dispatch switch|table|threaded|predicate] [--prefetch D] [--stages DIGITS] "
                 "[--kinds all|named|added] [--block N] [--messages N] [--core N] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX] [--orders N] "
                 "[--levels N] [--max-pages N] [--index-bits N] [--max-slots N]");
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
