// A functional check of the UDP replay on loopback: the sender and one receive mode, S1
// unless --mode names another, on one prebuilt packet array, every message checked to
// arrive once, in order, and to leave the book as the file path leaves it. It is never a
// measurement. It prints no rate or latency, and nothing it prints is a result; T-URING-1
// and T-URING-2 run on debian, through bench_udp.
//
//     itch_loopback FILE [--mode ID] [--first N] [--frames N] [--datagram BYTES]
//                   [--max-messages N] [--flush-gap-ns N] [--heartbeat-gap-ns N]
//                   [--rate MSGS_PER_S] [--core N] [--receiver-core N] [--sq-core N]
//                   [--receive-buffer BYTES] [--timeout-ms N] [--slot BYTES]
//                   [--ring-buffers N] [--checkpoint N] [--drop-packet I]
//                   [--duplicate-packet I] [--corrupt-packet I]
//                   [--max-orders N] [--max-levels N] [--max-pages N] [--max-slots N]
//
// --mode is S1 to S4 or U1 to U5 (bench/udp/modes.hpp); a mode this build lacks exits 2
// and says why. --slot and --ring-buffers size the buffers of the modes that hold many
// (ModeOptions), and a packing whose largest datagram does not fit a slot is refused.
// --sq-core is U5's SQ thread.
//
// The slice is frames --first to --first + --frames of FILE, the whole file by default,
// packed as mold_packets.hpp says. --rate paces the sender, 0 (the default) sends
// unpaced. --core and --receiver-core ask for the two threads' cores, which only Linux
// can grant. --checkpoint
// is the frame after which both paths hash the book, half the slice by default.
// The negative controls, each of which the check must fail: --drop-packet I loses packet I
// on the way, --duplicate-packet I sends it twice, and --corrupt-packet I flips the last
// byte of its last message, which leaves the framing and the sequence whole. The --max options size
// both books, which are the default arm at the default capacity unless they say otherwise.
//
// Exit codes: 0 pass, 1 fail, 2 bad usage, an input that cannot be packed, a mode not
// built here or whose setup fails, or no socket.
#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <limits>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "itch/book.hpp"
#include "itch/mold.hpp"
#include "itch/mold_packets.hpp"
#include "mapped_file.hpp"
#include "udp/digest.hpp"
#include "udp/loopback.hpp"
#include "udp/modes.hpp"
#include "udp/sender.hpp"
#include "udp/socket.hpp"

namespace {

namespace udp = itch::bench::udp;
namespace mold = itch::mold;

inline constexpr std::uint64_t kNone = std::numeric_limits<std::uint64_t>::max();

struct Options {
  const char* path = nullptr;
  std::string_view mode = "S1";
  mold::PacketPlan plan;
  udp::LoopbackConfig loopback;
  udp::ModeOptions mode_options;
  itch::BookCapacity capacity;
  std::uint64_t checkpoint = kNone;
  std::uint64_t drop_packet = kNone;
  std::uint64_t duplicate_packet = kNone;
  std::uint64_t corrupt_packet = kNone;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] bool take_capacity(std::string_view arg, std::uint64_t number,
                                 itch::BookCapacity& capacity) {
  if (number > std::numeric_limits<std::uint32_t>::max()) {
    return false;
  }
  const auto value = static_cast<std::uint32_t>(number);
  if (arg == "--max-orders") {
    capacity.orders = value;
  } else if (arg == "--max-levels") {
    capacity.levels = value;
  } else if (arg == "--max-pages") {
    capacity.index_pages = value;
  } else if (arg == "--max-slots") {
    capacity.level_slots = value;
  } else {
    return false;
  }
  return true;
}

// The checkpoint and the faults a test puts in the stream.
[[nodiscard]] bool take_check(std::string_view arg, std::uint64_t number, Options& options) {
  if (arg == "--checkpoint") {
    options.checkpoint = number;
  } else if (arg == "--drop-packet") {
    options.drop_packet = number;
  } else if (arg == "--duplicate-packet") {
    options.duplicate_packet = number;
  } else if (arg == "--corrupt-packet") {
    options.corrupt_packet = number;
  } else {
    return false;
  }
  return true;
}

[[nodiscard]] bool take_option(std::string_view arg, std::uint64_t number, Options& options) {
  constexpr std::uint64_t kMostInt = std::numeric_limits<int>::max();
  if (arg == "--first") {
    options.plan.first_frame = number;
  } else if (arg == "--frames") {
    options.plan.frame_limit = number;
  } else if (arg == "--datagram") {
    options.plan.max_datagram = static_cast<std::size_t>(number);
  } else if (arg == "--max-messages" && number <= mold::kMostBlocks) {
    options.plan.max_messages = static_cast<std::uint16_t>(number);
  } else if (arg == "--flush-gap-ns") {
    options.plan.flush_gap_ns = number;
  } else if (arg == "--heartbeat-gap-ns") {
    options.plan.heartbeat_gap_ns = number;
  } else if (arg == "--rate") {
    options.loopback.send.messages_per_second = number;
  } else if (arg == "--core" && number <= kMostInt) {
    options.loopback.sender_core = static_cast<int>(number);
  } else if (arg == "--receiver-core" && number <= kMostInt) {
    options.loopback.receiver_core = static_cast<int>(number);
  } else if (arg == "--sq-core" && number <= kMostInt) {
    options.mode_options.sq_core = static_cast<int>(number);
  } else if (arg == "--slot" && number != 0 && number <= udp::kDatagramBuffer) {
    options.mode_options.slot_bytes = static_cast<std::size_t>(number);
  } else if (arg == "--ring-buffers" && number != 0 && number <= udp::kMostRingBuffersAsked) {
    options.mode_options.ring_buffers = static_cast<std::uint32_t>(number);
  } else if (arg == "--receive-buffer" && number <= kMostInt) {
    options.loopback.receive_buffer = static_cast<int>(number);
  } else if (arg == "--timeout-ms" && number != 0 && number <= kMostInt) {
    options.loopback.idle_timeout = std::chrono::milliseconds{number};
  } else {
    return take_check(arg, number, options) || take_capacity(arg, number, options.capacity);
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
    if (arg == "--mode") {
      options.mode = args[++i];
      continue;
    }
    const std::optional<std::uint64_t> number = parse_number(args[++i]);
    if (!number || !take_option(arg, *number, options)) {
      return std::nullopt;
    }
  }
  if (options.path == nullptr || !udp::is_mode(options.mode)) {
    return std::nullopt;
  }
  options.mode_options.idle_timeout = options.loopback.idle_timeout;
  return options;
}

// The negative controls' socket: one packet reported sent and never sent, one sent twice,
// and one sent with its last byte flipped.
class FaultySocket {
 public:
  FaultySocket(udp::ConnectedSocket socket, const Options& options)
      : socket_{socket},
        drop_{options.drop_packet},
        duplicate_{options.duplicate_packet},
        corrupt_{options.corrupt_packet},
        copy_(mold::kMostDatagram) {}

  [[nodiscard]] udp::SendResult send(std::span<const std::byte> datagram) noexcept {
    const std::uint64_t index = sends_++;
    if (index == drop_) {
      return {.sent = true, .error = 0};
    }
    if (index == duplicate_) {
      static_cast<void>(socket_.send(datagram));
    }
    if (index == corrupt_ && !datagram.empty()) {
      std::ranges::copy(datagram, copy_.begin());
      copy_[datagram.size() - 1] ^= std::byte{0x01};
      return socket_.send(std::span<const std::byte>{copy_}.first(datagram.size()));
    }
    return socket_.send(datagram);
  }

 private:
  udp::ConnectedSocket socket_;
  std::uint64_t drop_;
  std::uint64_t duplicate_;
  std::uint64_t corrupt_;
  std::vector<std::byte> copy_;
  std::uint64_t sends_ = 0;
};

void print_summary(std::string_view path, const udp::ReplaySummary& summary) {
  std::println("loopback_{}_frames {}", path, summary.frames);
  std::println("loopback_{}_frame_hash {:016x}", path, summary.frame_hash);
  for (std::size_t i = 0; i < udp::kApplyStatuses; ++i) {
    std::println("loopback_{}_status_{} {}", path,
                 itch::to_string(static_cast<itch::ApplyStatus>(i)), summary.statuses[i]);
  }
  std::println("loopback_{}_checkpoint {}", path,
               summary.checkpoint_reached ? "reached" : "not reached");
  std::println("loopback_{}_checkpoint_hash {:016x}", path, summary.checkpoint_hash);
  std::println("loopback_{}_end_hash {:016x}", path, summary.end_hash);
}

int run(const Options& options) {
  if (!udp::mode_built(options.mode)) {
    std::println(stderr, "itch_loopback: mode {} is not built here: {}", options.mode,
                 udp::why_not_built(options.mode));
    return 2;
  }
  if (udp::uses_slots(options.mode) &&
      options.plan.max_datagram + udp::kSlotOverhead > options.mode_options.slot_bytes) {
    std::println(stderr, "itch_loopback: a {}-byte datagram does not fit a {}-byte slot",
                 options.plan.max_datagram, options.mode_options.slot_bytes);
    return 2;
  }
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_loopback: cannot open {}", options.path);
    return 2;
  }
  const auto packets = mold::build_packets(file->bytes(), options.plan);
  if (!packets) {
    std::println(stderr, "itch_loopback: cannot pack {}: {} at byte {}", options.path,
                 mold::to_string(packets.error().error), packets.error().offset);
    return 2;
  }
  const std::uint64_t checkpoint =
      options.checkpoint != kNone ? options.checkpoint : (packets->messages() + 1) / 2;

  std::println(
      "itch_loopback: a functional check on loopback, not a measurement. It reports no "
      "rate or latency, and no figure it prints is a result.");
  std::println("loopback_input {}", options.path);
  std::println("loopback_first_frame {}", options.plan.first_frame);
  std::println("loopback_packets {}", packets->size());
  std::println("loopback_messages {}", packets->messages());
  std::println("loopback_heartbeats {}", packets->heartbeats());
  std::println("loopback_bytes {}", packets->bytes().size());
  std::println("loopback_max_datagram {}", options.plan.max_datagram);
  std::println("loopback_max_messages {}", options.plan.max_messages);
  std::println("loopback_paced {}", options.loopback.send.messages_per_second != 0 ? "yes" : "no");
  if (options.drop_packet != kNone) {
    std::println("loopback_negative_control packet {} is never sent", options.drop_packet);
  }
  if (options.duplicate_packet != kNone) {
    std::println("loopback_negative_control packet {} is sent twice", options.duplicate_packet);
  }
  if (options.corrupt_packet != kNone) {
    std::println("loopback_negative_control packet {} has its last byte flipped",
                 options.corrupt_packet);
  }

  itch::FastBook<> file_book{options.capacity};
  udp::BookDigest file_digest{file_book, checkpoint};
  udp::replay_frames(file->bytes(), options.plan.first_frame, options.plan.frame_limit,
                     file_digest);
  const udp::ReplaySummary by_file = file_digest.finish();

  itch::FastBook<> udp_book{options.capacity};
  udp::BookDigest udp_digest{udp_book, checkpoint};
  const auto wrap = [&options](udp::ConnectedSocket socket) {
    return FaultySocket{socket, options};
  };
  const auto ran = udp::with_mode(options.mode, options.mode_options, [&](auto make) {
    return udp::run_loopback(*packets, options.loopback, make, udp_digest, [] {}, wrap);
  });
  if (!ran) {
    return 2;
  }
  const auto& loop = *ran;
  if (!loop) {
    std::println(stderr, "itch_loopback: {} failed: errno {}", loop.error().call,
                 loop.error().error);
    return 2;
  }
  const udp::ReplaySummary by_udp = udp_digest.finish();

  std::println("loopback_receiver_at {}:{}", loop->receiver_at.host, loop->receiver_at.port);
  std::println("loopback_sender_at {}:{}", loop->sender_at.host, loop->sender_at.port);
  std::println("loopback_receive_buffer requested {} read back {}", loop->receive_buffer.requested,
               loop->receive_buffer.read_back);
  std::println("loopback_sender_pinning {}{}", udp::kCanPin ? "" : "unsupported here, ",
               loop->sender.pin.pinned ? "pinned" : "not pinned");
  std::println("loopback_sender_pin_attempt {}", loop->sender.pin.attempt);
  std::println("loopback_receiver_pinning {}{}", udp::kCanPin ? "" : "unsupported here, ",
               loop->receiver_pin.pinned ? "pinned" : "not pinned");
  std::println("loopback_receiver_pin_attempt {}", loop->receiver_pin.attempt);
  bool pass = loop->sender.report.has_value();
  if (loop->sender.report) {
    const udp::SendReport& sent = *loop->sender.report;
    std::println("loopback_sent_packets {}", sent.sent_packets);
    std::println("loopback_sent_messages {}", sent.sent_messages);
    std::println("loopback_failed_sends {} (ENOBUFS {}, ECONNREFUSED {}, other {})",
                 sent.failed_packets, sent.no_buffers, sent.refused, sent.other_errors);
    pass = pass && sent.sent_packets == packets->size() && sent.failed_packets == 0;
  }
  const mold::SequenceCounts& sequence = loop->sequence;
  const udp::ModeCounters& mode = loop->mode;
  std::println("loopback_receiver_mode {}", options.mode);
  std::println("loopback_receiver_end {}", udp::to_string(loop->received.end));
  std::println("loopback_mode_failures {} (last errno {})", mode.failures, mode.last_error);
  std::println("loopback_truncated {}", mode.truncated);
  std::println("loopback_rearms {}", mode.rearms);
  std::println("loopback_no_buffers {}", mode.no_buffers);
  std::println("loopback_cq_overflow {}", mode.cq_overflow);
  std::println("loopback_rxq_overflow {}",
               mode.rxq_overflow_set ? std::format("{}", mode.rxq_overflow) : "not read");
  std::println("loopback_datagrams {}", loop->feed.datagrams);
  std::println("loopback_malformed {}", loop->feed.malformed());
  std::println("loopback_admitted {}", sequence.admitted);
  std::println("loopback_gaps {}", sequence.gaps);
  std::println("loopback_lost {}", sequence.lost);
  std::println("loopback_duplicate_messages {}", sequence.duplicate_messages);
  std::println("loopback_foreign_session {}", sequence.foreign_session);
  std::println("loopback_next_expected {}", loop->next_expected);
  print_summary("file", by_file);
  print_summary("udp", by_udp);

  pass = pass && loop->received.end == udp::ReceiveEnd::end_of_session && mode.truncated == 0 &&
         mode.rxq_overflow == 0 && loop->feed.malformed() == 0 && sequence.gaps == 0 &&
         sequence.lost == 0 && sequence.duplicate_messages == 0 && sequence.foreign_session == 0 &&
         sequence.out_of_range == 0 && sequence.admitted == packets->messages() &&
         loop->next_expected == packets->next_sequence() && by_file.frames == packets->messages() &&
         by_udp == by_file;
  std::println("loopback_verdict {}", pass ? "PASS" : "FAIL");
  return pass ? 0 : 1;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(std::fputs(
          "usage: itch_loopback FILE [--mode ID] [--first N] [--frames N] [--datagram BYTES] "
          "[--max-messages N] [--flush-gap-ns N] [--heartbeat-gap-ns N] [--rate MSGS_PER_S] "
          "[--core N] [--receiver-core N] [--sq-core N] [--receive-buffer BYTES] "
          "[--timeout-ms N] [--slot BYTES] [--ring-buffers N] [--checkpoint N] "
          "[--drop-packet I] [--duplicate-packet I] [--corrupt-packet I] [--max-orders N] "
          "[--max-levels N] [--max-pages N] [--max-slots N]\n",
          stderr));
      return 2;
    }
    return run(*options);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
