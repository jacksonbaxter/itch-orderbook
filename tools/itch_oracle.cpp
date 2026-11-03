// Replays a BinaryFILE ITCH 5.0 stream through the fast book and the reference book,
// diffs them after every message, and checks T-INV-1, T-INV-2 and T-INV-3.
//
//     itch_oracle FILE [--hash-every N] [--exhaustive] [--max-orders N] [--max-levels N]
//                 [--observations FILE] [--index hash|paged] [--levels linked|flat]
//                 [--max-pages N] [--index-bits N] [--max-slots N]
//
// --hash-every N  hashes both whole books every N messages. 0 keeps only the final hash.
// --exhaustive    compares the whole of both books after every message, as a control.
// --observations  writes one line per crossed or locked observation.
// --index, --levels  the arm of the fast book. The default is the book's default arm.
// --max-pages, --index-bits  the paged index's pool and reach. --max-slots  the flat
//                 levels' arena. Each arm reads only its own.
//
// Exit codes: 0 everything passed. 1 the diff, T-INV-1, T-INV-3 or the stream itself
// failed. 2 bad usage or an unreadable file. 3 only T-INV-2 failed: that result is strict
// and is reported as failing, and a separate code keeps it apart from a broken book. A
// new cross in normal trading breaks T-INV-3, so it is a hard failure, never code 3.
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <optional>
#include <print>
#include <span>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <vector>

#include "book_arms.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "oracle/diff.hpp"

namespace {

using itch::oracle::Divergence;
using itch::oracle::OracleConfig;

struct Options {
  const char* path = nullptr;
  const char* observations = nullptr;
  OracleConfig config;
  itch::tools::BookArm arm;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (arg == "--exhaustive") {
      options.config.exhaustive = true;
      continue;
    }
    if (!arg.starts_with("--")) {
      if (options.path != nullptr) {
        return std::nullopt;
      }
      options.path = args[i];
      continue;
    }
    if (i + 1 == args.size()) {
      return std::nullopt;
    }
    ++i;
    if (arg == "--observations") {
      options.observations = args[i];
      continue;
    }
    if (arg == "--index") {
      const auto index = itch::tools::parse_index_arm(args[i]);
      if (!index) {
        return std::nullopt;
      }
      options.arm.index = *index;
      continue;
    }
    if (arg == "--levels") {
      const auto levels = itch::tools::parse_levels_arm(args[i]);
      if (!levels) {
        return std::nullopt;
      }
      options.arm.levels = *levels;
      continue;
    }
    const auto number = parse_number(args[i]);
    if (!number) {
      return std::nullopt;
    }
    if (arg == "--hash-every") {
      options.config.hash_every = *number;
    } else if (arg == "--max-orders") {
      options.config.capacity.orders = static_cast<std::uint32_t>(*number);
    } else if (arg == "--max-levels") {
      options.config.capacity.levels = static_cast<std::uint32_t>(*number);
    } else if (arg == "--max-pages") {
      options.config.capacity.index_pages = static_cast<std::uint32_t>(*number);
    } else if (arg == "--index-bits") {
      options.config.capacity.index_bits = static_cast<std::uint32_t>(*number);
    } else if (arg == "--max-slots") {
      options.config.capacity.level_slots = static_cast<std::uint32_t>(*number);
    } else {
      return std::nullopt;
    }
  }
  if (options.path == nullptr) {
    return std::nullopt;
  }
  return options;
}

// How the walk of the stream ended, apart from anything the books said.
struct StreamEnd {
  itch::FrameStatus status = itch::FrameStatus::end_of_input;
  std::uint64_t malformed = 0;
  std::optional<char> last_event;
  bool stopped_early = false;
};

template <class Run>
[[nodiscard]] StreamEnd replay(Run& oracle, itch::FrameReader& reader) {
  StreamEnd end;
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::zero_length) {
      continue;
    }
    end.last_event.reset();
    if (frame.status != itch::FrameStatus::message) {
      ++end.malformed;
      continue;
    }
    if (itch::read_char(frame.payload.data()) == itch::SystemEvent::kType) {
      end.last_event = itch::SystemEvent::from_wire(frame.payload.data()).event_code;
    }
    if (!oracle.on_message(frame.payload)) {
      end.stopped_early = true;
      break;
    }
  }
  end.status = frame.status;
  return end;
}

// What only one arm has to say: how often the paged index fell back, and how far the
// flat levels' arena reached.
template <class Book>
void print_arm_counters(const Book& book) {
  if constexpr (std::is_same_v<typename Book::Index, itch::PagedIndex<>>) {
    std::println("index_fallback_inserts {}", book.index().fallback_inserts());
    std::println("index_fallback_live_end {}", book.index().fallback_live());
    std::println("index_peak_live_pages {}", book.index().peak_live_pages());
  }
  if constexpr (std::is_same_v<typename Book::LevelArm, itch::FlatLevels>) {
    std::println("level_slots_used {}", book.levels().slots_used());
    std::println("level_relocations {}", book.levels().relocations());
  }
}

template <class Run>
void print_counters(const Run& oracle, const itch::ReaderCounters& reader) {
  const itch::oracle::DiffCounters& c = oracle.counters();
  std::println("frames {}", reader.frames);
  for (unsigned type = 0; type < reader.frames_by_type.size(); ++type) {
    if (reader.frames_by_type[type] != 0) {
      std::println("type {:c} {}", static_cast<char>(type), reader.frames_by_type[type]);
    }
  }
  std::println("messages_diffed {}", c.messages_diffed);
  std::println("status_checks {}", c.status_checks);
  std::println("touched_checks {}", c.touched_checks);
  std::println("empty_touched_checks {}", c.empty_touched_checks);
  std::println("bbo_checks {}", c.bbo_checks);
  std::println("write_set_checks {}", c.write_set_checks);
  std::println("write_set_writes {}", c.write_set_writes);
  std::println("write_set_violations {}", c.write_set_violations);
  std::println("move_reports {}", c.move_reports);
  std::println("moved_checks {}", c.moved_checks);
  std::println("empty_moved_checks {}", c.empty_moved_checks);
  std::println("periodic_hash_checks {}", c.periodic_hash_checks);
  std::println("final_hash_checks {}", c.final_hash_checks);
  std::println("production_hash_checks {}", c.production_hash_checks);
  std::println("exhaustive_checks {}", c.exhaustive_checks);
  std::println("last_hash {:016x}", oracle.last_hash());
  std::println("live_orders_end {}", oracle.fast().live_orders());
  std::println("live_levels_end {}", oracle.fast().live_levels());
  std::println("peak_live_orders {}", oracle.fast().peak_live_orders());
  std::println("peak_live_levels {}", oracle.fast().peak_live_levels());
  print_arm_counters(oracle.fast());
}

template <class Index, class Levels>
int run(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_oracle: cannot read {}", options.path);
    return 2;
  }
  std::FILE* observations = nullptr;
  if (options.observations != nullptr) {
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
    observations = std::fopen(options.observations, "w");
    if (observations == nullptr) {
      std::println(stderr, "itch_oracle: cannot write {}", options.observations);
      return 2;
    }
  }

  std::println("oracle_index {}", to_string(options.arm.index));
  std::println("oracle_levels {}", to_string(options.arm.levels));
  std::println("oracle_hash_every {}", options.config.hash_every);
  std::println("oracle_exhaustive {}", options.config.exhaustive ? 1 : 0);
  std::println("oracle_max_orders {}", options.config.capacity.orders);
  std::println("oracle_max_levels {}", options.config.capacity.levels);
  if (options.arm.index == itch::tools::IndexArm::paged) {
    std::println("oracle_max_pages {}", options.config.capacity.index_pages);
    std::println("oracle_index_bits {}", options.config.capacity.index_bits);
  }
  if (options.arm.levels == itch::tools::LevelsArm::flat) {
    std::println("oracle_max_slots {}", options.config.capacity.level_slots);
  }
  std::println("bytes {}", file->bytes().size());

  itch::oracle::BasicOracleRun<Index, Levels> oracle{options.config};
  oracle.invariants().set_observation_log(observations);
  itch::FrameReader reader{file->bytes()};
  const auto started = std::chrono::steady_clock::now();
  const StreamEnd end = replay(oracle, reader);
  if (!end.stopped_early) {
    oracle.finish();
  }
  const std::chrono::duration<double> elapsed = std::chrono::steady_clock::now() - started;
  if (observations != nullptr) {
    static_cast<void>(std::fclose(observations));  // NOLINT(cppcoreguidelines-owning-memory)
  }

  std::println("elapsed_seconds {:.1f}", elapsed.count());
  print_counters(oracle, reader.counters());
  std::println("malformed_frames {}", end.malformed);
  std::println("end {}", itch::to_string(end.status));
  std::println("divergences {}", oracle.divergences().size());
  for (const Divergence& d : oracle.divergences()) {
    std::println("DIVERGENCE kind={} message={} type={} {}", to_string(d.kind), d.message, d.type,
                 d.detail);
  }
  oracle.invariants().print_report();

  const bool all_diffed = oracle.counters().messages_diffed == reader.counters().frames;
  const bool stream_ok = !end.stopped_early && end.status == itch::FrameStatus::end_of_input &&
                         end.malformed == 0 && end.last_event == 'C';
  if (!all_diffed) {
    std::println("oracle_incomplete diffed {} of {} frames", oracle.counters().messages_diffed,
                 reader.counters().frames);
  }
  if (!stream_ok) {
    std::println("oracle_incomplete the stream did not end cleanly on S/C");
  }
  const bool oracle_pass = oracle.divergences().empty() && all_diffed && stream_ok;
  const bool inv1_pass = oracle.invariants().inv1_violations() == 0;
  const bool inv2_pass = oracle.invariants().crossed() == 0;
  const bool inv3_pass = oracle.invariants().inv3_violations() == 0;
  std::println("verdict T-ORACLE-1 {}", oracle_pass ? "PASS" : "FAIL");
  std::println("verdict T-INV-1 {} violations={}", inv1_pass ? "PASS" : "FAIL",
               oracle.invariants().inv1_violations());
  std::println("verdict T-INV-2 {} crossed={} locked={}", inv2_pass ? "PASS" : "FAIL",
               oracle.invariants().crossed(), oracle.invariants().locked());
  std::println("verdict T-INV-3 {} violations={}", inv3_pass ? "PASS" : "FAIL",
               oracle.invariants().inv3_violations());
  if (!oracle_pass || !inv1_pass || !inv3_pass) {
    return 1;
  }
  return inv2_pass ? 0 : 3;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(
          std::fputs("usage: itch_oracle FILE [--hash-every N] [--exhaustive] [--max-orders N] "
                     "[--max-levels N] [--observations FILE] [--index hash|paged] "
                     "[--levels linked|flat] [--max-pages N] [--index-bits N] [--max-slots N]\n",
                     stderr));
      return 2;
    }
    return itch::tools::with_book_arm(options->arm, [&options]<class Index, class Levels>() {
      return run<Index, Levels>(*options);
    });
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
