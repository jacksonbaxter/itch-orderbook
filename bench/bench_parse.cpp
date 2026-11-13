// Times the three parse passes of T-PARSE-1 to T-PARSE-3 (bench/parse_passes.hpp) over a day
// held in memory. One process is one repetition: scripts/parse_driver.py starts a fresh one for
// each and writes the results file.
//
//     bench_parse --input FILE --out RUN.json [--rounds N] [--core N] [--commit TEXT]
//                 [--dataset NAME] [--dataset-sha256 HEX]
//
// The day is read into anonymous memory on base pages before anything runs, and never mapped,
// as itch-book's parse_throughput reads it into a vector. Then, untimed, a FrameReader count of
// the frames and one warm-up of each pass; then --rounds rounds (5 unless told), each one timed
// pass of each, framing, decode and strict, in an order turned by one place each round, so that
// a drift over the process falls on all three alike. Each timed span is one whole pass, every
// page of the input read and found resident before it and found resident after it.
//
// A pass is refused unless it walked every frame the FrameReader counted to the end of the
// input and agrees with its warm-up in every count and in its fold; the decode must have
// decoded every message of its ten types, and the strict decode every message, rejecting none.
//
// Exit codes: 0 publishable, 3 refused, with the record still written, 2 bad usage.
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "itch/reader.hpp"
#include "parse_passes.hpp"
#include "platform/affinity.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;
using itch::bench::parse::Totals;

enum class Pass : std::uint8_t { framing, decode, strict };

struct PassSpec {
  Pass pass;
  std::string_view name;
  std::string_view target;
};

constexpr std::array<PassSpec, 3> kPasses{{
    {.pass = Pass::framing, .name = "framing", .target = "T-PARSE-1"},
    {.pass = Pass::decode, .name = "decode", .target = "T-PARSE-2"},
    {.pass = Pass::strict, .name = "strict", .target = "T-PARSE-3"},
}};

// The ten types T-PARSE-2's decode takes, as itch-book's Checksum handler does.
constexpr std::array<char, 10> kDecodeTypes{'S', 'R', 'A', 'F', 'E', 'C', 'X', 'D', 'U', 'P'};

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  unsigned rounds = 5;
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
  } else if (arg == "--rounds") {
    return parse_number(value, options.rounds) && options.rounds > 0;
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

struct Input {
  std::optional<Arena> copy;
  std::span<const std::byte> bytes;
  std::string description;
};

// The whole file read into an arena of base pages, with nothing left mapped, as bench_replay's
// ram mode reads it: on Linux each piece's page cache is dropped once it is copied, so that the
// copy and the cache together hold about one day and one piece.
std::optional<Input> read_into_memory(const std::string& path) {
  const int fd = ::open(path.c_str(), O_RDONLY);  // NOLINT(cppcoreguidelines-pro-type-vararg)
  if (fd < 0) {
    return std::nullopt;
  }
  struct stat info{};
  if (::fstat(fd, &info) != 0 || info.st_size <= 0) {
    ::close(fd);
    return std::nullopt;
  }
  const auto size = static_cast<std::size_t>(info.st_size);
  std::optional<Arena> copy = Arena::make(size, PageHint::base, Prefault::by_caller);
  // Small enough that one piece is the most held twice, and under the INT_MAX bytes that
  // macOS refuses in a single read.
  constexpr std::size_t kPiece = std::size_t{64} << 20U;
  std::size_t done = 0;
  while (copy && done < size) {
    const ssize_t got = ::read(fd, copy->data() + done, std::min(size - done, kPiece));
    if (got <= 0) {
      break;
    }
#ifdef __linux__
    static_cast<void>(::posix_fadvise(fd, static_cast<off_t>(done), static_cast<off_t>(got),
                                      POSIX_FADV_DONTNEED));
#endif
    done += static_cast<std::size_t>(got);
  }
  std::string description = std::format(
      "ram: read into anonymous memory before the warm-up, in pieces of {} MiB, ", kPiece >> 20U);
#ifdef __linux__
  // Read-ahead may have cached a little past the last piece dropped.
  static_cast<void>(::posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED));
  description +=
      "each piece's page cache dropped once it is copied (posix_fadvise POSIX_FADV_DONTNEED), ";
#else
  description += "the file's page cache left to the kernel, since this host has no posix_fadvise, ";
#endif
  ::close(fd);
  if (!copy || done != size) {
    return std::nullopt;
  }
  description += std::format(
      "never mapped; {}; every page read and found resident before each timed pass, and found "
      "resident after it",
      copy->report().hint);
  const std::span<const std::byte> bytes{copy->data(), size};
  return Input{.copy = std::move(copy), .bytes = bytes, .description = std::move(description)};
}

void fault_in(std::span<const std::byte> bytes) {
  static std::atomic<std::byte> seen{std::byte{0}};
  std::byte any{0};
  for (std::size_t at = 0; at < bytes.size(); at += base_page_size()) {
    any |= bytes[at];
  }
  seen = any;
}

[[nodiscard]] Totals run_pass(Pass pass, std::span<const std::byte> bytes) {
  switch (pass) {
    case Pass::framing: return parse::frame_walk(bytes);
    case Pass::decode: return parse::book_decode(bytes);
    case Pass::strict: return parse::strict_decode(bytes);
  }
  return {};
}

// What the FrameReader counts, untimed, that every pass is held to.
struct Reference {
  std::uint64_t frames = 0;  // Every frame, zero-length ones included.
  std::uint64_t messages = 0;
  std::uint64_t unknown_type = 0;
  std::uint64_t bad_length = 0;
  std::uint64_t zero_length = 0;
  std::uint64_t decode_types = 0;  // Messages of kDecodeTypes.
  std::size_t consumed = 0;
  bool whole = false;  // The reader ended on end_of_input.
};

[[nodiscard]] Reference count_frames(std::span<const std::byte> bytes) {
  itch::FrameReader reader{bytes};
  Reference ref;
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::message &&
        std::ranges::find(kDecodeTypes, static_cast<char>(frame.payload[0])) !=
            kDecodeTypes.end()) {
      ++ref.decode_types;
    }
  }
  const itch::ReaderCounters& counters = reader.counters();
  ref.frames = counters.frames + counters.zero_length;
  ref.messages = counters.messages;
  ref.unknown_type = counters.unknown_type;
  ref.bad_length = counters.bad_length;
  ref.zero_length = counters.zero_length;
  ref.consumed = reader.consumed();
  ref.whole = frame.status == itch::FrameStatus::end_of_input;
  return ref;
}

struct TimedPass {
  Pass pass = Pass::framing;
  unsigned round = 0;
  Totals totals;
  std::uint64_t ticks = 0;
  std::string residency_before;
  std::string residency_after;
};

struct Record {
  Reference reference;
  std::array<Totals, kPasses.size()> warmup{};
  std::vector<TimedPass> passes;
  std::uint64_t migrations = 0;
  std::vector<std::string> refusals;
};

[[nodiscard]] std::string_view name_of(Pass pass) {
  return kPasses[static_cast<std::size_t>(pass)].name;
}

// Each round's passes, in kPasses' order turned by one place a round.
void time_rounds(const Options& options, std::span<const std::byte> bytes, Record& record) {
  for (unsigned round = 0; round < options.rounds; ++round) {
    for (std::size_t i = 0; i < kPasses.size(); ++i) {
      TimedPass timed;
      timed.pass = kPasses[(round + i) % kPasses.size()].pass;
      timed.round = round + 1;
      fault_in(bytes);
      const Residency before = resident(bytes);
      timed.residency_before = before.describe();
      if (!before.complete()) {
        record.refusals.push_back("input not resident before a timed pass: " + before.describe());
        return;
      }
      const std::uint64_t started = ticks_start();
      timed.totals = run_pass(timed.pass, bytes);
      timed.ticks = ticks_stop() - started;
      const Residency after = resident(bytes);
      timed.residency_after = after.describe();
      if (!after.complete()) {
        record.refusals.push_back("input left memory during a timed pass: " + after.describe());
      }
      record.passes.push_back(std::move(timed));
    }
  }
}

// The checks every record gets, after its passes.
void judge(std::span<const std::byte> bytes, Record& record) {
  const Reference& ref = record.reference;
  if (!ref.whole || ref.consumed != bytes.size()) {
    record.refusals.push_back(
        std::format("the frame reader stopped at byte {} of {}", ref.consumed, bytes.size()));
  }
  if (ref.unknown_type != 0 || ref.bad_length != 0 || ref.zero_length != 0) {
    record.refusals.push_back(std::format(
        "the input has {} frames of unknown type, {} of a wrong length and {} of none, so not "
        "every frame is a message",
        ref.unknown_type, ref.bad_length, ref.zero_length));
  }
  for (const PassSpec& spec : kPasses) {
    const Totals& warm = record.warmup[static_cast<std::size_t>(spec.pass)];
    if (warm.messages != ref.frames || warm.stopped_at != bytes.size()) {
      record.refusals.push_back(
          std::format("the {} warm-up walked {} frames of {} to byte {} of {}", spec.name,
                      warm.messages, ref.frames, warm.stopped_at, bytes.size()));
    }
  }
  const Totals& decode = record.warmup[static_cast<std::size_t>(Pass::decode)];
  if (decode.decoded != ref.decode_types) {
    record.refusals.push_back(
        std::format("the decode decoded {} messages of the {} of its ten types", decode.decoded,
                    ref.decode_types));
  }
  const Totals& strict = record.warmup[static_cast<std::size_t>(Pass::strict)];
  if (strict.decoded != ref.messages || strict.rejected != 0) {
    record.refusals.push_back(
        std::format("the strict decode decoded {} messages of {} and rejected {}", strict.decoded,
                    ref.messages, strict.rejected));
  }
  // Every timed pass computes what its warm-up did.
  for (const TimedPass& timed : record.passes) {
    if (timed.totals != record.warmup[static_cast<std::size_t>(timed.pass)]) {
      record.refusals.push_back(std::format("a timed {} pass in round {} differs from its warm-up",
                                            name_of(timed.pass), timed.round));
    }
  }
}

std::string totals_json(const Totals& totals) {
  return std::format(
      R"({{"messages": {}, "decoded": {}, "rejected": {}, "fold": {}, "stopped_at": {}}})",
      totals.messages, totals.decoded, totals.rejected, totals.fold, totals.stopped_at);
}

std::string record_json(const std::vector<HeaderField>& header, const Options& options,
                        const Input& input, const Record& record, const TimerInfo& timer,
                        bool pinned) {
  std::string out = "{\n  \"header\": {";
  for (std::size_t i = 0; i < header.size(); ++i) {
    out += std::format("{}\n    {}: {}", i == 0 ? "" : ",", json_string(header[i].first),
                       json_string(header[i].second));
  }
  out += "\n  },\n";
  const Reference& ref = record.reference;
  out += std::format(
      "  \"input\": {},\n  \"bytes\": {},\n  \"rounds\": {},\n  \"calibrated_hz\": {:.3f},\n"
      "  \"reference\": {{\"frames\": {}, \"messages\": {}, \"unknown_type\": {}, "
      "\"bad_length\": {}, \"zero_length\": {}, \"decode_types\": {}, \"consumed\": {}}},\n",
      json_string(scrub_private(input.description)), input.bytes.size(), options.rounds,
      timer.calibrated_hz, ref.frames, ref.messages, ref.unknown_type, ref.bad_length,
      ref.zero_length, ref.decode_types, ref.consumed);
  out += "  \"warmup\": {";
  for (std::size_t i = 0; i < kPasses.size(); ++i) {
    out += std::format("{}{}: {}", i == 0 ? "" : ", ", json_string(kPasses[i].name),
                       totals_json(record.warmup[i]));
  }
  out += "},\n  \"passes\": [";
  for (std::size_t i = 0; i < record.passes.size(); ++i) {
    const TimedPass& p = record.passes[i];
    const double seconds = timer.ticks_to_ns(static_cast<double>(p.ticks)) / 1e9;
    out += std::format(
        "{}\n    {{\"pass\": {}, \"target\": {}, \"round\": {}, \"ticks\": {}, \"seconds\": "
        "{:.9f}, "
        "\"totals\": {}, \"residency_before\": {}, \"residency_after\": {}}}",
        i == 0 ? "" : ",", json_string(name_of(p.pass)),
        json_string(kPasses[static_cast<std::size_t>(p.pass)].target), p.round, p.ticks, seconds,
        totals_json(p.totals), json_string(p.residency_before), json_string(p.residency_after));
  }
  out += "\n  ],\n";
  out += std::format("  \"pinned\": {},\n  \"migrations\": {},\n  \"peak_rss_bytes\": {},\n",
                     pinned ? "true" : "false", record.migrations, peak_rss_bytes());
  out += "  \"refusals\": [";
  for (std::size_t i = 0; i < record.refusals.size(); ++i) {
    out += std::format("{}{}", i == 0 ? "" : ", ", json_string(record.refusals[i]));
  }
  return out + "]\n}\n";
}

int run(int argc, char** argv) {
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_parse --input FILE --out RUN.json [--rounds N] [--core N] "
                 "[--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX]");
    return 2;
  }
  const std::optional<Input> input = read_into_memory(options.input);
  if (!input) {
    std::println(stderr, "bench_parse: cannot read {} into memory", options.input);
    return 2;
  }
  const std::span<const std::byte> bytes = input->bytes;
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();

  Record record;
  if (kPinningRequired && !pin.pinned) {
    record.refusals.push_back(pinning_field(pin));
  }
  fault_in(bytes);
  record.reference = count_frames(bytes);
  for (const PassSpec& spec : kPasses) {
    record.warmup[static_cast<std::size_t>(spec.pass)] = run_pass(spec.pass, bytes);
  }
  migrations.sample();
  if (record.refusals.empty()) {
    time_rounds(options, bytes, record);
  }
  migrations.sample();
  record.migrations = migrations.migrations();
  judge(bytes, record);

  const Residency after = resident(bytes);
  const std::vector<HeaderField> header = build_header(
      live_source(),
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
       .counters = unavailable("bench_parse reads no counters"),
       .rdpmc = unavailable("bench_parse reads no counters"),
       .page_size = std::format("{} bytes, base pages", base_page_size()),
       .residency = after.describe()});

  std::ofstream out{options.out};
  out << record_json(header, options, *input, record, timer, pin.pinned);
  if (!out) {
    std::println(stderr, "bench_parse: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : record.refusals) {
    std::println(stderr, "bench_parse: refused: {}", refusal);
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
