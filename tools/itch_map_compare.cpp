// Replays a BinaryFILE ITCH 5.0 stream through the fast book and the map book, the
// baseline of T-DESIGN-1, and compares them. Nothing here is timed.
//
//     itch_map_compare FILE [--heap fresh|aged] [--seed N] [--warm-blocks N]
//                      [--ring-blocks N] [--hash-every N] [--max-orders N] [--max-levels N]
//                      [--index hash|paged|identity|chained]
//                      [--levels linked|flat|flat-scan16|flat-bisect] [--max-pages N]
//                      [--index-bits N] [--max-slots N]
//
// After every message: both books gave the same status, and the top of book on both
// sides of the message's locate is the same. Every --hash-every messages, 1,000,000 by
// default, and once at the end: a hash of each whole book, from the canonical walk in
// tests/oracle/canonical.hpp, and its word count. 0 keeps only the final hash, and 1
// compares the whole of both books after every message. At the end, the live orders and
// levels.
//
// --heap aged runs the map book on a heap aged as include/itch/heap_ager.hpp says, with
// --seed, --warm-blocks and --ring-blocks as its knobs. fresh, the default, does not.
// --index, --levels and the capacities pick the fast book's arm, as in itch_oracle.
//
// Exit codes: 0 the books agreed throughout. 1 they did not, or the stream did not end
// cleanly. 2 bad usage or an unreadable file.
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>

#include "book_arms.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/map_book.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "oracle/canonical.hpp"

namespace {

constexpr std::uint64_t kPrintedDivergences = 20;

struct Options {
  const char* path = nullptr;
  bool aged = false;
  itch::HeapAgeing ageing;
  std::uint64_t hash_every = 1'000'000;
  itch::BookCapacity capacity;
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

[[nodiscard]] bool take_number(Options& options, std::string_view flag, std::uint64_t value) {
  if (flag == "--seed") {
    options.ageing.seed = value;
  } else if (flag == "--warm-blocks") {
    options.ageing.warm_blocks = static_cast<std::size_t>(value);
  } else if (flag == "--ring-blocks") {
    options.ageing.ring_blocks = static_cast<std::size_t>(value);
  } else if (flag == "--hash-every") {
    options.hash_every = value;
  } else if (flag == "--max-orders") {
    options.capacity.orders = static_cast<std::uint32_t>(value);
  } else if (flag == "--max-levels") {
    options.capacity.levels = static_cast<std::uint32_t>(value);
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
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
    const std::string_view value = args[i];
    if (arg == "--heap") {
      if (value != "fresh" && value != "aged") {
        return std::nullopt;
      }
      options.aged = value == "aged";
      continue;
    }
    switch (itch::tools::parse_arm_option(arg, value, options.arm, options.capacity)) {
      case itch::tools::ArmOption::taken: continue;
      case itch::tools::ArmOption::bad_value: return std::nullopt;
      case itch::tools::ArmOption::not_an_arm_option: break;
    }
    const auto number = parse_number(value);
    if (!number || !take_number(options, arg, *number)) {
      return std::nullopt;
    }
  }
  if (options.path == nullptr) {
    return std::nullopt;
  }
  return options;
}

struct WholeBook {
  std::uint64_t hash;
  std::uint64_t words;
  friend bool operator==(const WholeBook&, const WholeBook&) = default;
};

struct Counters {
  std::uint64_t messages = 0;
  std::uint64_t status_checks = 0;
  std::uint64_t top_checks = 0;
  std::uint64_t periodic_hash_checks = 0;
  std::uint64_t final_hash_checks = 0;
  std::uint64_t divergences = 0;
  std::uint64_t refused = 0;  // Messages both books refused alike.
};

// The two books and what has been compared so far.
template <class Fast>
class Comparison {
 public:
  Comparison(const Options& options, itch::HeapAger* ager)
      : fast_{std::make_unique<Fast>(options.capacity)},
        ager_{ager},
        hash_every_{options.hash_every} {}

  void on_message(std::span<const std::byte> payload) {
    ++counters_.messages;
    if (ager_ != nullptr) {
      ager_->before_message(payload);
    }
    const itch::ApplyStatus fast = fast_->apply(payload);
    const itch::ApplyStatus map = map_.apply(payload);
    ++counters_.status_checks;
    if (fast != map) {
      diverge("status", payload, std::format("fast={} map={}", to_string(fast), to_string(map)));
    } else if (fast != itch::ApplyStatus::applied && fast != itch::ApplyStatus::no_book_effect) {
      ++counters_.refused;
    }
    // Every type the book reads has a locate at offset 1. A payload too short for one
    // is compared on locate 0.
    const std::uint16_t locate =
        payload.size() >= 3 ? itch::read_be<std::uint16_t>(payload.data() + 1) : 0;
    compare_top(payload, locate, itch::Side::bid);
    compare_top(payload, locate, itch::Side::ask);
    if (hash_every_ != 0 && counters_.messages % hash_every_ == 0) {
      ++counters_.periodic_hash_checks;
      compare_whole(payload, "hash");
    }
  }

  void finish() {
    ++counters_.final_hash_checks;
    const WholeBook fast = whole_fast();
    const WholeBook map = whole_map();
    last_ = fast;
    if (fast != map) {
      diverge_at_end("final_hash", fast, map);
    }
    if (fast_->live_orders() != map_.live_orders() || fast_->live_levels() != map_.live_levels()) {
      ++counters_.divergences;
      std::println(
          "MAP_DIVERGENCE kind=live_end fast_orders={} map_orders={} fast_levels={} "
          "map_levels={}",
          fast_->live_orders(), map_.live_orders(), fast_->live_levels(), map_.live_levels());
    }
  }

  [[nodiscard]] const Counters& counters() const noexcept { return counters_; }
  [[nodiscard]] const WholeBook& last() const noexcept { return last_; }
  [[nodiscard]] const Fast& fast() const noexcept { return *fast_; }
  [[nodiscard]] const itch::MapBook& map() const noexcept { return map_; }

 private:
  void compare_top(std::span<const std::byte> payload, std::uint16_t locate, itch::Side side) {
    ++counters_.top_checks;
    const itch::TopOfBook fast = fast_->best(locate, side);
    const itch::TopOfBook map = map_.best(locate, side);
    // An empty side's other fields mean nothing, in either book.
    const bool same = fast.present == map.present && (!fast.present || fast == map);
    if (!same) {
      diverge("top", payload,
              std::format("locate={} side={} fast={}/{}/{}/{} map={}/{}/{}/{}", locate,
                          side == itch::Side::bid ? 'B' : 'S', fast.present ? 1 : 0, fast.price,
                          fast.shares, fast.orders, map.present ? 1 : 0, map.price, map.shares,
                          map.orders));
    }
  }

  void compare_whole(std::span<const std::byte> payload, std::string_view kind) {
    const WholeBook fast = whole_fast();
    const WholeBook map = whole_map();
    last_ = fast;
    if (fast != map) {
      diverge(kind, payload,
              std::format("fast={:016x}/{} map={:016x}/{}", fast.hash, fast.words, map.hash,
                          map.words));
    }
  }

  void diverge_at_end(std::string_view kind, const WholeBook& fast, const WholeBook& map) {
    ++counters_.divergences;
    std::println("MAP_DIVERGENCE kind={} message={} fast={:016x}/{} map={:016x}/{}", kind,
                 counters_.messages, fast.hash, fast.words, map.hash, map.words);
  }

  void diverge(std::string_view kind, std::span<const std::byte> payload,
               const std::string& detail) {
    ++counters_.divergences;
    if (counters_.divergences <= kPrintedDivergences) {
      std::println("MAP_DIVERGENCE kind={} message={} type={} {}", kind, counters_.messages,
                   itch::read_char(payload.data()), detail);
    }
  }

  [[nodiscard]] WholeBook whole_fast() const {
    itch::oracle::WordHash hash;
    itch::oracle::emit_fast(*fast_, hash);
    return {.hash = hash.value(), .words = hash.words()};
  }

  [[nodiscard]] WholeBook whole_map() const {
    itch::oracle::WordHash hash;
    itch::oracle::emit_map(map_, hash);
    return {.hash = hash.value(), .words = hash.words()};
  }

  std::unique_ptr<Fast> fast_;
  itch::MapBook map_;
  itch::HeapAger* ager_;
  std::uint64_t hash_every_;
  Counters counters_;
  WholeBook last_{};
};

template <class Index, class Levels>
int run(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_map_compare: cannot read {}", options.path);
    return 2;
  }
  std::println("compare_index {}", to_string(options.arm.index));
  std::println("compare_levels {}", to_string(options.arm.levels));
  std::println("compare_heap {}", options.aged ? "aged" : "fresh");
  if (options.aged) {
    std::println("compare_seed {}", options.ageing.seed);
    std::println("compare_warm_blocks {}", options.ageing.warm_blocks);
    std::println("compare_ring_blocks {}", options.ageing.ring_blocks);
  }
  std::println("compare_hash_every {}", options.hash_every);
  std::println("bytes {}", file->bytes().size());

  // The ager comes first, so that the map book's nodes go into the holes it leaves.
  std::unique_ptr<itch::HeapAger> ager;
  if (options.aged) {
    ager = std::make_unique<itch::HeapAger>(options.ageing);
  }
  Comparison<itch::FastBook<itch::NoWriteObserver, Index, Levels>> books{options, ager.get()};

  itch::FrameReader reader{file->bytes()};
  std::uint64_t malformed = 0;
  std::optional<char> last_event;
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::zero_length) {
      continue;
    }
    last_event.reset();
    if (frame.status != itch::FrameStatus::message) {
      ++malformed;
      continue;
    }
    if (itch::read_char(frame.payload.data()) == itch::SystemEvent::kType) {
      last_event = itch::SystemEvent::from_wire(frame.payload.data()).event_code;
    }
    books.on_message(frame.payload);
  }
  books.finish();

  const Counters& c = books.counters();
  std::println("frames {}", reader.counters().frames);
  std::println("messages_compared {}", c.messages);
  std::println("status_checks {}", c.status_checks);
  std::println("refused_alike {}", c.refused);
  std::println("top_checks {}", c.top_checks);
  std::println("periodic_hash_checks {}", c.periodic_hash_checks);
  std::println("final_hash_checks {}", c.final_hash_checks);
  std::println("last_hash {:016x}", books.last().hash);
  std::println("last_hash_words {}", books.last().words);
  std::println("live_orders_end {}", books.map().live_orders());
  std::println("live_levels_end {}", books.map().live_levels());
  if (ager) {
    std::println("ager_allocations {}", ager->allocations());
    std::println("ager_releases {}", ager->releases());
    std::println("ager_held_bytes {}", ager->held_bytes());
  }
  std::println("malformed_frames {}", malformed);
  std::println("end {}", itch::to_string(frame.status));
  std::println("divergences {}", c.divergences);

  const bool all_compared = c.messages == reader.counters().frames;
  const bool stream_ok =
      frame.status == itch::FrameStatus::end_of_input && malformed == 0 && last_event == 'C';
  if (!stream_ok) {
    std::println("compare_incomplete the stream did not end cleanly on S/C");
  }
  const bool pass = c.divergences == 0 && all_compared && stream_ok;
  std::println("verdict MAP-BOOK {}", pass ? "PASS" : "FAIL");
  return pass ? 0 : 1;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(std::fputs(
          "usage: itch_map_compare FILE [--heap fresh|aged] [--seed N] [--warm-blocks N] "
          "[--ring-blocks N] [--hash-every N] [--max-orders N] [--max-levels N] "
          "[--index hash|paged|identity|chained] [--levels linked|flat|flat-scan16|flat-bisect] "
          "[--max-pages N] [--index-bits N] [--max-slots N]\n",
          stderr));
      return 2;
    }
    return itch::tools::with_book_arm(options->arm, [&options]<class Index, class Levels> {
      return run<Index, Levels>(*options);
    });
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
