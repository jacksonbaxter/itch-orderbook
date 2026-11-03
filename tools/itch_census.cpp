// Walks a BinaryFILE ITCH 5.0 file and prints total and per-type message counts.
//
// Exit codes: 0 when the walk reached the end of the file cleanly and the last
// message is System Event 'C', 1 when it did not, 2 when the file could not be read.
// A truncated download is a well-formed prefix of a good file, so a clean walk alone
// proves nothing: the last message is what shows the file is complete.
//
// It also counts what the spec leaves out, each case once over the day: directory values
// (DS-64, DS-65, DS-66), what the test symbols carry (DS-65), Cross Trades by shares and
// price (DS-69), and what follows System Event 'E' (DS-71). scripts/check_census.py ties
// the totals back to the manifest.
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <map>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"

namespace {

using ByteCounts = std::array<std::uint64_t, 256>;

// A wire byte as one token: the character, or hex where it would not read as one.
std::string byte_name(unsigned byte) {
  if (std::isprint(static_cast<int>(byte)) != 0 && byte != ' ') {
    return std::format("{:c}", static_cast<char>(byte));
  }
  return std::format("0x{:02x}", byte);
}

void print_byte_counts(std::string_view key, const ByteCounts& counts) {
  for (unsigned byte = 0; byte < counts.size(); ++byte) {
    if (counts[byte] != 0) {
      std::println("{} {} {}", key, byte_name(byte), counts[byte]);
    }
  }
}

[[nodiscard]] std::size_t index_of(char byte) noexcept { return static_cast<unsigned char>(byte); }

// The cases the spec leaves out. Nothing here decides anything: a count is only a count.
class OmissionCounts {
 public:
  void observe(const itch::Header& header, const itch::Message& message) {
    if (after_e_) {
      ++after_e_types_[index_of(header.message_type)];
    }
    if (const auto* directory = std::get_if<itch::StockDirectory>(&message)) {
      on_directory(*directory);
    } else if (const auto* cross = std::get_if<itch::CrossTrade>(&message)) {
      on_cross(*cross);
    } else if (const auto* event = std::get_if<itch::SystemEvent>(&message)) {
      after_e_ = after_e_ || event->event_code == 'E';
    }
    on_locate(header);
  }

  void print() const {
    std::println("directory_repeats {}", directory_repeats_);
    print_byte_counts("market_category", categories_);
    print_byte_counts("authenticity", authenticity_);
    for (const auto& [lot, count] : round_lots_) {
      std::println("round_lot {} {}", lot, count);
    }
    std::println("test_symbols {}", test_symbols_);
    std::println("test_symbols_with_orders {}", test_symbols_with_orders_);
    print_byte_counts("test_type", test_types_);
    for (unsigned byte = 0; byte < crosses_.size(); ++byte) {
      const Crosses& cross = crosses_[byte];
      if (cross.count != 0) {
        std::println("cross {} count={} zero_shares={} zero_price={} zero_both={}", byte_name(byte),
                     cross.count, cross.zero_shares, cross.zero_price, cross.zero_both);
      }
    }
    print_byte_counts("after_e_type", after_e_types_);
  }

 private:
  struct Crosses {
    std::uint64_t count = 0;
    std::uint64_t zero_shares = 0;
    std::uint64_t zero_price = 0;
    std::uint64_t zero_both = 0;
  };

  enum class Locate : std::uint8_t { unlisted, listed, test, test_with_orders };

  void on_directory(const itch::StockDirectory& m) {
    Locate& locate = locates_[m.header.stock_locate];
    if (locate != Locate::unlisted) {
      ++directory_repeats_;
    } else if (m.authenticity == 'T') {
      locate = Locate::test;
      ++test_symbols_;
    } else {
      locate = Locate::listed;
    }
    ++categories_[index_of(m.market_category)];
    ++authenticity_[index_of(m.authenticity)];
    ++round_lots_[m.round_lot_size];
  }

  void on_cross(const itch::CrossTrade& m) {
    Crosses& cross = crosses_[index_of(m.cross_type)];
    ++cross.count;
    cross.zero_shares += m.shares == 0 ? 1U : 0U;
    cross.zero_price += m.cross_price == 0 ? 1U : 0U;
    cross.zero_both += m.shares == 0 && m.cross_price == 0 ? 1U : 0U;
  }

  void on_locate(const itch::Header& header) {
    Locate& locate = locates_[header.stock_locate];
    if (locate != Locate::test && locate != Locate::test_with_orders) {
      return;
    }
    ++test_types_[index_of(header.message_type)];
    if (locate == Locate::test && (header.message_type == itch::AddOrder::kType ||
                                   header.message_type == itch::AddOrderAttributed::kType)) {
      locate = Locate::test_with_orders;
      ++test_symbols_with_orders_;
    }
  }

  std::vector<Locate> locates_ = std::vector<Locate>(65536, Locate::unlisted);
  std::map<std::uint32_t, std::uint64_t> round_lots_;
  std::array<Crosses, 256> crosses_{};
  ByteCounts categories_{};
  ByteCounts authenticity_{};
  ByteCounts test_types_{};
  ByteCounts after_e_types_{};
  std::uint64_t directory_repeats_ = 0;
  std::uint64_t test_symbols_ = 0;
  std::uint64_t test_symbols_with_orders_ = 0;
  bool after_e_ = false;
};

int run(const char* path) {
  const auto file = itch::tools::MappedFile::open(path);
  if (!file) {
    std::println(stderr, "itch_census: cannot read {}", path);
    return 2;
  }

  itch::FrameReader reader{file->bytes()};
  std::optional<itch::SystemEvent> last_event;
  std::optional<std::size_t> last_offset;
  std::uint64_t decode_failures = 0;
  OmissionCounts omissions;
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == itch::FrameStatus::zero_length) {
      continue;
    }
    last_offset = frame.offset;
    last_event.reset();
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    const auto decoded = itch::decode(frame.payload);
    if (!decoded) {
      ++decode_failures;
      continue;
    }
    omissions.observe(itch::read_header(frame.payload.data()), *decoded);
    if (const auto* event = std::get_if<itch::SystemEvent>(&*decoded)) {
      last_event = *event;
    }
  }

  const itch::ReaderCounters& counters = reader.counters();
  std::println("bytes {}", file->bytes().size());
  std::println("consumed {}", reader.consumed());
  std::println("messages {}", counters.frames);
  print_byte_counts("type", counters.frames_by_type);
  std::println("unknown_type {}", counters.unknown_type);
  std::println("bad_length {}", counters.bad_length);
  std::println("zero_length {}", counters.zero_length);
  std::println("decode_failures {}", decode_failures);
  std::println("end {}", itch::to_string(frame.status));
  if (last_event) {
    std::println("last_message S {:c} locate={} tracking={} timestamp_ns={} offset={}",
                 last_event->event_code, last_event->header.stock_locate,
                 last_event->header.tracking_number, last_event->header.timestamp_ns,
                 last_offset.value_or(0));
  }
  omissions.print();

  if (frame.status != itch::FrameStatus::end_of_input) {
    std::println(stderr, "itch_census: FAIL framing: {} at offset {}",
                 itch::to_string(frame.status), frame.offset);
    return 1;
  }
  if (decode_failures != 0) {
    std::println(stderr, "itch_census: FAIL {} messages did not decode", decode_failures);
    return 1;
  }
  if (!last_event || last_event->event_code != 'C') {
    std::println(stderr, "itch_census: FAIL last message is not S/C");
    return 1;
  }
  return 0;
}

}  // namespace

int main(int argc, const char* const* argv) {
  if (argc != 2) {
    static_cast<void>(std::fputs("usage: itch_census <file>\n", stderr));
    return 2;
  }
  try {
    return run(argv[1]);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
