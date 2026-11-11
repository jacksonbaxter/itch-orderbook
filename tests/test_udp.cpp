// The UDP replay: the sender's pacing and accounting on a clock moved by hand, the
// receive loop on a scripted mode, S1 on a real socket, and the two on loopback, where
// every fixture must arrive whole and leave the book as the file does. Functional
// checks only: nothing here times anything.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "itch/book.hpp"
#include "itch/mold.hpp"
#include "itch/mold_feed.hpp"
#include "itch/mold_packets.hpp"
#include "support/churn.hpp"
#include "support/fixtures.hpp"
#include "udp/digest.hpp"
#include "udp/kernel_counters.hpp"
#include "udp/loopback.hpp"
#include "udp/modes.hpp"
#include "udp/receive_mode.hpp"
#include "udp/sender.hpp"
#include "udp/socket.hpp"

// ASSERT_TRUE(x.has_value()) guards each access, which the check cannot see.
// NOLINTBEGIN(bugprone-unchecked-optional-access)
namespace {

namespace mold = itch::mold;
namespace udp = itch::bench::udp;
using Bytes = std::vector<std::byte>;

constexpr itch::BookCapacity kCapacity{
    .orders = 4096, .levels = 4096, .index_pages = 16, .index_bits = 20, .level_slots = 8192};

// A clock that moves `step` ns on each read, and `jump` ns more on read number `jump_at`.
class StepClock {
 public:
  explicit StepClock(std::uint64_t step, std::uint64_t jump_at = 0, std::uint64_t jump = 0)
      : step_{step}, jump_at_{jump_at}, jump_{jump} {}

  [[nodiscard]] std::uint64_t now_ns() noexcept {
    ++reads_;
    now_ += step_ + (reads_ == jump_at_ ? jump_ : 0);
    return now_;
  }
  [[nodiscard]] std::uint64_t peek() const noexcept { return now_; }
  [[nodiscard]] std::uint64_t reads() const noexcept { return reads_; }

 private:
  std::uint64_t now_ = 1'000'000;
  std::uint64_t reads_ = 0;
  std::uint64_t step_;
  std::uint64_t jump_at_;
  std::uint64_t jump_;
};

// Records the clock at each send, and fails the sends it is told to.
struct RecordingSocket {
  explicit RecordingSocket(const StepClock& clock) : source{&clock} {}

  [[nodiscard]] udp::SendResult send(std::span<const std::byte> datagram) {
    const std::size_t index = times.size();
    times.push_back(source->peek());
    sizes.push_back(datagram.size());
    for (const auto& [at, error] : failures) {
      if (at == index) {
        return {.sent = false, .error = error};
      }
    }
    return {.sent = true, .error = 0};
  }

  std::vector<std::uint64_t> times;
  std::vector<std::size_t> sizes;
  std::vector<std::pair<std::size_t, int>> failures;
  const StepClock* source;
};

mold::PacketArray churn_packets(std::size_t cap = mold::kDefaultDatagram) {
  const Bytes stream = itch::testing::build_stream();
  auto packets = mold::build_packets(stream, {.max_datagram = cap});
  EXPECT_TRUE(packets.has_value());
  return std::move(*packets);
}

// The due time of each packet, from the schedule's start: the messages before it at the
// rate.
std::vector<std::uint64_t> due_times(const mold::PacketArray& packets, std::uint64_t start,
                                     std::uint64_t rate) {
  std::vector<std::uint64_t> due;
  std::uint64_t before = 0;
  for (const mold::PacketRef& ref : packets.refs()) {
    due.push_back(start + (before * 1'000'000'000 / rate));
    before += ref.messages;
  }
  return due;
}

// --- The sender -----------------------------------------------------------------------

TEST(Sender, NoPacketGoesBeforeItsTime) {
  const mold::PacketArray packets = churn_packets();
  constexpr std::uint64_t kRate = 5'000'000;
  constexpr std::uint64_t kStep = 7;
  StepClock clock{kStep};
  RecordingSocket socket{clock};
  const auto report = udp::send_packets(packets, {.messages_per_second = kRate}, clock, socket);
  ASSERT_TRUE(report.has_value());
  ASSERT_EQ(socket.times.size(), packets.size());
  const std::vector<std::uint64_t> due = due_times(packets, report->start_ns, kRate);
  std::uint64_t late_total = 0;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    ASSERT_GE(socket.times[i], due[i]) << "packet " << i << " went early";
    // Each send starts at the first read at or past both its due time and the send
    // before it.
    const std::uint64_t ready = std::max(due[i], i == 0 ? 0 : socket.times[i - 1]);
    ASSERT_LE(socket.times[i], ready + kStep) << "packet " << i << " waited too long";
    late_total += socket.times[i] - due[i];
  }
  EXPECT_TRUE(report->paced);
  EXPECT_EQ(report->late_total_ns, late_total);
  EXPECT_EQ(report->late_max_ns, std::ranges::max(std::views::iota(std::size_t{0}, packets.size()) |
                                                  std::views::transform([&](std::size_t i) {
                                                    return socket.times[i] - due[i];
                                                  })));
  EXPECT_EQ(report->sent_packets, packets.size());
  EXPECT_EQ(report->sent_messages, packets.messages());
  EXPECT_EQ(report->sent_bytes, packets.bytes().size());
  // The run spans the schedule: the last packet's due time and its send.
  EXPECT_GE(report->end_ns, due.back());
  std::uint64_t buckets = 0;
  for (const std::uint64_t count : report->lateness) {
    buckets += count;
  }
  EXPECT_EQ(buckets, packets.size());
}

// A stall is charged to the sends behind it, against the schedule, not forgiven by
// starting the schedule again.
TEST(Sender, AStallIsChargedAgainstTheSchedule) {
  const mold::PacketArray packets = churn_packets();
  constexpr std::uint64_t kRate = 2'000'000;
  constexpr std::uint64_t kStall = 3'000'000;
  StepClock clock{5, 5000, kStall};
  RecordingSocket socket{clock};
  const auto report = udp::send_packets(packets, {.messages_per_second = kRate}, clock, socket);
  ASSERT_TRUE(report.has_value());
  const std::vector<std::uint64_t> due = due_times(packets, report->start_ns, kRate);
  std::size_t stalled = 0;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    ASSERT_GE(socket.times[i], due[i]);
    if (socket.times[i] - due[i] > socket.times[stalled] - due[stalled]) {
      stalled = i;
    }
  }
  // The stall came in a spin, at most one packet's schedule before its due time.
  const std::uint64_t worst = socket.times[stalled] - due[stalled];
  EXPECT_EQ(report->late_max_ns, worst);
  EXPECT_GT(worst, kStall - 50'000);
  EXPECT_LE(worst, kStall + 5);
  EXPECT_EQ(report->lateness[udp::lateness_bucket(worst)] > 0, true);
  // The sends after the stall go at once until the schedule is met again.
  EXPECT_LT(socket.times[stalled + 1] - socket.times[stalled], 100U);
  EXPECT_GT(socket.times[stalled + 1] - due[stalled + 1], 0U);
  const std::uint64_t last = packets.size() - 1;
  EXPECT_LE(socket.times[last] - due[last], 5U);
}

TEST(Sender, HeartbeatsTakeNoScheduleTime) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_messages = 2});
  ASSERT_TRUE(packets.has_value());
  ASSERT_GT(packets->heartbeats(), 1U);
  constexpr std::uint64_t kRate = 1'000'000;
  StepClock clock{3};
  RecordingSocket socket{clock};
  const auto report = udp::send_packets(*packets, {.messages_per_second = kRate}, clock, socket);
  ASSERT_TRUE(report.has_value());
  // Each heartbeat is due when the packet after it is, and the end of session when the
  // closing heartbeat is.
  const std::vector<std::uint64_t> due = due_times(*packets, report->start_ns, kRate);
  for (std::size_t i = 0; i + 1 < packets->size(); ++i) {
    if (packets->ref(i).messages == 0) {
      EXPECT_EQ(due[i], due[i + 1]) << i;
    }
  }
  EXPECT_EQ(due.back(), report->start_ns + (packets->messages() * 1'000'000'000 / kRate));
}

TEST(Sender, TheAchievedRateIsWhatWasSentOverTheRun) {
  const mold::PacketArray packets = churn_packets();
  constexpr std::uint64_t kRate = 10'000'000;
  StepClock clock{11};
  RecordingSocket socket{clock};
  socket.failures = {{3, ENOBUFS}};
  const auto report = udp::send_packets(packets, {.messages_per_second = kRate}, clock, socket);
  ASSERT_TRUE(report.has_value());
  const std::uint64_t sent = packets.messages() - packets.ref(3).messages;
  EXPECT_EQ(report->sent_messages, sent);
  const auto elapsed = static_cast<double>(report->end_ns - report->start_ns);
  EXPECT_DOUBLE_EQ(report->achieved_rate(), static_cast<double>(sent) * 1e9 / elapsed);
  // Paced, the run takes the schedule's length and no longer than one step past it.
  const std::vector<std::uint64_t> due = due_times(packets, report->start_ns, kRate);
  EXPECT_GE(report->end_ns, due.back());
  EXPECT_LE(report->end_ns, socket.times.back() + 11);
}

TEST(Sender, UnpacedItReadsTheClockOnlyAtTheEnds) {
  const mold::PacketArray packets = churn_packets();
  StepClock clock{13};
  RecordingSocket socket{clock};
  const auto report = udp::send_packets(packets, {.messages_per_second = 0}, clock, socket);
  ASSERT_TRUE(report.has_value());
  EXPECT_FALSE(report->paced);
  EXPECT_EQ(clock.reads(), 2U);
  EXPECT_EQ(report->sent_packets, packets.size());
  EXPECT_EQ(report->late_total_ns, 0U);
}

TEST(Sender, AFailedSendIsCountedByItsErrorAndNotRetried) {
  const mold::PacketArray packets = churn_packets();
  StepClock clock{1};
  RecordingSocket socket{clock};
  socket.failures = {{2, ENOBUFS}, {5, ECONNREFUSED}, {7, EPERM}, {9, ENOBUFS}};
  const auto report = udp::send_packets(packets, {}, clock, socket);
  ASSERT_TRUE(report.has_value());
  EXPECT_EQ(socket.times.size(), packets.size());
  EXPECT_EQ(report->packets, packets.size());
  EXPECT_EQ(report->failed_packets, 4U);
  EXPECT_EQ(report->sent_packets, packets.size() - 4);
  EXPECT_EQ(report->no_buffers, 2U);
  EXPECT_EQ(report->refused, 1U);
  EXPECT_EQ(report->other_errors, 1U);
  EXPECT_EQ(report->first_error, ENOBUFS);
  // Each count is widened on its own: two uint16_t add up to an int.
  const std::uint64_t failed =
      std::uint64_t{packets.ref(2).messages} + std::uint64_t{packets.ref(5).messages} +
      std::uint64_t{packets.ref(7).messages} + std::uint64_t{packets.ref(9).messages};
  EXPECT_EQ(report->failed_messages, failed);
  EXPECT_EQ(report->sent_messages + report->failed_messages, packets.messages());
}

TEST(Sender, LatenessBucketsArePowersOfTwo) {
  EXPECT_EQ(udp::lateness_bucket(0), 0U);
  EXPECT_EQ(udp::lateness_bucket(1), 1U);
  EXPECT_EQ(udp::lateness_bucket(2), 2U);
  EXPECT_EQ(udp::lateness_bucket(3), 2U);
  EXPECT_EQ(udp::lateness_bucket(1024), 11U);
  EXPECT_EQ(udp::lateness_bucket(~0ULL), udp::kLatenessBuckets - 1);
}

// Pinning is Linux only. Elsewhere the sender says it did not pin, and why.
TEST(Sender, PinningIsReportedAsItIs) {
  const itch::bench::PinOutcome none = udp::pin_thread(-1);
  EXPECT_FALSE(none.pinned);
  EXPECT_EQ(none.attempt, "no core was requested");
  itch::bench::PinOutcome asked;
  int core = -1;
  std::thread pinned{[&] {
    core = std::max(0, itch::bench::current_core());
    asked = udp::pin_thread(core);
  }};
  pinned.join();
  EXPECT_EQ(asked.requested_core, core);
  EXPECT_FALSE(asked.attempt.empty());
  if constexpr (udp::kCanPin) {
    EXPECT_TRUE(asked.pinned) << asked.attempt;
  } else {
    EXPECT_FALSE(asked.pinned) << asked.attempt;
  }
}

// --- The receive loop, on a scripted mode ---------------------------------------------

// Serves scripted batches, and records what each release() hands back.
class ScriptedMode {
 public:
  static constexpr std::string_view kId = "scripted";

  explicit ScriptedMode(std::vector<std::vector<Bytes>> batches,
                        udp::WaitStatus last = udp::WaitStatus::idle_timeout)
      : batches_{std::move(batches)}, last_{last} {}

  [[nodiscard]] udp::WaitResult wait(std::span<udp::Datagram> batch) {
    ++counters_.waits;
    if (next_ == batches_.size()) {
      return {.status = last_, .count = 0, .error = last_ == udp::WaitStatus::failed ? EIO : 0};
    }
    const std::vector<Bytes>& serve = batches_[next_++];
    for (std::size_t i = 0; i < serve.size(); ++i) {
      batch[i] = {.bytes = serve[i]};
    }
    return {.status = udp::WaitStatus::datagrams, .count = serve.size(), .error = 0};
  }
  void release(std::span<const udp::Datagram> done) { released_.push_back(done.size()); }
  [[nodiscard]] const udp::ModeCounters& counters() const noexcept { return counters_; }
  // The size of each release(), in order.
  [[nodiscard]] const std::vector<std::size_t>& released() const noexcept { return released_; }

 private:
  std::vector<std::size_t> released_;
  std::vector<std::vector<Bytes>> batches_;
  udp::WaitStatus last_;
  std::size_t next_ = 0;
  udp::ModeCounters counters_;
};
static_assert(udp::ReceiveMode<ScriptedMode>);

struct CountFrames {
  std::uint64_t frames = 0;
  void operator()(const itch::Frame& /*frame*/) noexcept { ++frames; }
};

std::vector<Bytes> datagrams_of(const mold::PacketArray& packets) {
  std::vector<Bytes> out;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    const std::span<const std::byte> bytes = packets.packet(i);
    out.emplace_back(bytes.begin(), bytes.end());
  }
  return out;
}

TEST(ReceiveLoop, BatchesAreHandedOnInOrderAndReleasedWhole) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {.max_messages = 2});
  ASSERT_TRUE(packets.has_value());
  const std::vector<Bytes> all = datagrams_of(*packets);
  // Batches of 1, 3, 5, ... datagrams, the last ending with the end of session.
  std::vector<std::vector<Bytes>> batches;
  for (std::size_t at = 0, width = 1; at < all.size(); at += width, width += 2) {
    const std::size_t end = std::min(all.size(), at + width);
    batches.emplace_back(all.begin() + static_cast<std::ptrdiff_t>(at),
                         all.begin() + static_cast<std::ptrdiff_t>(end));
  }
  ScriptedMode mode{batches};
  CountFrames sink;
  mold::FeedHandler feed{packets->plan().session, 1, sink};
  std::array<udp::Datagram, udp::kMostBatch> batch{};
  const udp::ReceiveOutcome outcome = udp::receive_until_end(mode, feed, batch);
  EXPECT_EQ(outcome.end, udp::ReceiveEnd::end_of_session);
  EXPECT_EQ(sink.frames, packets->messages());
  ASSERT_EQ(mode.released().size(), batches.size());
  for (std::size_t i = 0; i < batches.size(); ++i) {
    EXPECT_EQ(mode.released()[i], batches[i].size());
  }
}

TEST(ReceiveLoop, ItStopsAtTheEndOfSessionWithTheRestUnread) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  const std::vector<std::vector<Bytes>> batches{datagrams_of(*packets), datagrams_of(*packets)};
  ScriptedMode mode{batches};
  CountFrames sink;
  mold::FeedHandler feed{packets->plan().session, 1, sink};
  std::array<udp::Datagram, udp::kMostBatch> batch{};
  const udp::ReceiveOutcome outcome = udp::receive_until_end(mode, feed, batch);
  EXPECT_EQ(outcome.end, udp::ReceiveEnd::end_of_session);
  EXPECT_EQ(mode.released().size(), 1U);
}

TEST(ReceiveLoop, AnIdleTimeoutOrAFailureEndsItShort) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  std::vector<Bytes> all = datagrams_of(*packets);
  all.pop_back();  // No end of session.
  for (const udp::WaitStatus last : {udp::WaitStatus::idle_timeout, udp::WaitStatus::failed}) {
    ScriptedMode mode{{all}, last};
    CountFrames sink;
    mold::FeedHandler feed{packets->plan().session, 1, sink};
    std::array<udp::Datagram, udp::kMostBatch> batch{};
    const udp::ReceiveOutcome outcome = udp::receive_until_end(mode, feed, batch);
    EXPECT_EQ(outcome.end, last == udp::WaitStatus::failed ? udp::ReceiveEnd::failed
                                                           : udp::ReceiveEnd::idle_timeout);
    EXPECT_EQ(sink.frames, packets->messages());
    EXPECT_FALSE(feed.ended());
  }
}

// --- S1 on a real socket --------------------------------------------------------------

TEST(RecvfromMode, ItTimesOutWhenNothingComes) {
  auto pair = udp::open_loopback_pair();
  ASSERT_TRUE(pair.has_value()) << pair.error().call << " errno " << pair.error().error;
  ASSERT_TRUE(udp::set_receive_timeout(pair->receiver.get(), std::chrono::milliseconds{50}));
  udp::RecvfromMode mode{pair->receiver.get()};
  std::array<udp::Datagram, 1> batch{};
  const udp::WaitResult result = mode.wait(batch);
  EXPECT_EQ(result.status, udp::WaitStatus::idle_timeout);
  EXPECT_EQ(mode.counters().timeouts, 1U);
  EXPECT_EQ(mode.counters().datagrams, 0U);
}

TEST(RecvfromMode, EachDatagramArrivesWholeAndInOrder) {
  auto pair = udp::open_loopback_pair();
  ASSERT_TRUE(pair.has_value());
  ASSERT_TRUE(udp::set_receive_timeout(pair->receiver.get(), std::chrono::milliseconds{2000}));
  EXPECT_EQ(pair->receiver_at.host, "127.0.0.1");
  EXPECT_NE(pair->receiver_at.port, pair->sender_at.port);
  const udp::ConnectedSocket sender{pair->sender.get()};
  std::vector<Bytes> sent;
  for (const std::size_t size :
       {std::size_t{0}, std::size_t{20}, std::size_t{1400}, std::size_t{9000}}) {
    Bytes datagram(size);
    for (std::size_t i = 0; i < size; ++i) {
      datagram[i] = static_cast<std::byte>((i * 7) + size);
    }
    const udp::SendResult result = sender.send(datagram);
    ASSERT_TRUE(result.sent) << size << " errno " << result.error;
    sent.push_back(std::move(datagram));
  }
  udp::RecvfromMode mode{pair->receiver.get()};
  std::array<udp::Datagram, 4> batch{};
  for (const Bytes& want : sent) {
    const udp::WaitResult result = mode.wait(batch);
    ASSERT_EQ(result.status, udp::WaitStatus::datagrams);
    ASSERT_EQ(result.count, 1U);
    EXPECT_TRUE(std::ranges::equal(batch[0].bytes, want)) << want.size();
  }
  EXPECT_EQ(mode.counters().datagrams, sent.size());
  EXPECT_EQ(mode.counters().bytes, 0U + 20U + 1400U + 9000U);
}

TEST(Sockets, TheReceiveBufferIsReadBack) {
  auto pair = udp::open_loopback_pair();
  ASSERT_TRUE(pair.has_value());
  constexpr int kAsked = 1024 * 1024;
  const auto size = udp::set_receive_buffer(pair->receiver.get(), kAsked);
  ASSERT_TRUE(size.has_value());
  EXPECT_EQ(size->requested, kAsked);
  EXPECT_GT(size->read_back, 0);
}

// --- On loopback ----------------------------------------------------------------------

Bytes read_file(const std::filesystem::path& path) {
  std::ifstream in{path, std::ios::binary};
  const std::vector<char> text{std::istreambuf_iterator<char>{in}, {}};
  Bytes bytes(text.size());
  std::memcpy(bytes.data(), text.data(), text.size());
  return bytes;
}

// The books are on the heap: the analyzer takes a summary returned past a book on the
// stack for the book's address escaping (clang-analyzer-core.StackAddressEscape).
udp::ReplaySummary by_file(std::span<const std::byte> stream, std::uint64_t checkpoint) {
  const auto book = std::make_unique<itch::FastBook<>>(kCapacity);
  udp::BookDigest digest{*book, checkpoint};
  udp::replay_frames(stream, 0, ~0ULL, digest);
  return digest.finish();
}

template <class Wrap = udp::PlainSocket>
std::pair<udp::LoopbackRun, udp::ReplaySummary> by_loopback(const mold::PacketArray& packets,
                                                            const udp::LoopbackConfig& config,
                                                            std::uint64_t checkpoint,
                                                            Wrap wrap = {}) {
  const auto book = std::make_unique<itch::FastBook<>>(kCapacity);
  udp::BookDigest digest{*book, checkpoint};
  auto run = udp::run_loopback_s1(packets, config, digest, [] {}, wrap);
  EXPECT_TRUE(run.has_value());
  return {std::move(*run), digest.finish()};
}

void expect_whole(const udp::LoopbackRun& run, const mold::PacketArray& packets) {
  ASSERT_TRUE(run.sender.report.has_value());
  EXPECT_EQ(run.sender.report->sent_packets, packets.size());
  EXPECT_EQ(run.received.end, udp::ReceiveEnd::end_of_session);
  EXPECT_EQ(run.feed.malformed(), 0U);
  EXPECT_EQ(run.sequence.gaps, 0U);
  EXPECT_EQ(run.sequence.lost, 0U);
  EXPECT_EQ(run.sequence.duplicate_messages, 0U);
  EXPECT_EQ(run.sequence.admitted, packets.messages());
  EXPECT_EQ(run.next_expected, packets.next_sequence());
  EXPECT_EQ(run.mode.datagrams, packets.size());
}

TEST(Loopback, EveryOracleFixtureArrivesAndLeavesTheBookAsTheFileDoes) {
  std::size_t fixtures = 0;
  for (const auto& entry : std::filesystem::directory_iterator{ITCH_ORACLE_FIXTURES}) {
    if (entry.path().extension() != ".itch") {
      continue;
    }
    SCOPED_TRACE(entry.path().filename().string());
    const Bytes stream = read_file(entry.path());
    const auto packets = mold::build_packets(stream, {});
    ASSERT_TRUE(packets.has_value());
    const std::uint64_t checkpoint = (packets->messages() + 1) / 2;
    const auto [run, summary] = by_loopback(*packets, {}, checkpoint);
    expect_whole(run, *packets);
    const udp::ReplaySummary want = by_file(stream, checkpoint);
    EXPECT_EQ(summary, want);
    EXPECT_TRUE(summary.checkpoint_reached);
    ++fixtures;
  }
  EXPECT_GE(fixtures, 6U);
}

TEST(Loopback, PacedAndInSmallPacketsItStillArrivesWhole) {
  const Bytes stream = read_file(std::filesystem::path{ITCH_ORACLE_FIXTURES} / "session.itch");
  const auto packets = mold::build_packets(stream, {.max_datagram = mold::kSmallestDayCap});
  ASSERT_TRUE(packets.has_value());
  const std::uint64_t checkpoint = packets->messages() / 3;
  const auto [run, summary] =
      by_loopback(*packets, {.send = {.messages_per_second = 2'000'000}}, checkpoint);
  expect_whole(run, *packets);
  EXPECT_EQ(summary, by_file(stream, checkpoint));
  EXPECT_TRUE(run.sender.report->paced);
}

// S1 with a count of the releases it is owed, to show a mode of the caller's own runs.
class CountingMode {
 public:
  static constexpr std::string_view kId = "counting S1";
  explicit CountingMode(int fd) : inner_{fd} {}
  [[nodiscard]] udp::WaitResult wait(std::span<udp::Datagram> batch) noexcept {
    ++releases_owed_;
    return inner_.wait(batch);
  }
  void release(std::span<const udp::Datagram> done) noexcept {
    --releases_owed_;
    inner_.release(done);
  }
  [[nodiscard]] const udp::ModeCounters& counters() const noexcept { return inner_.counters(); }
  [[nodiscard]] int releases_owed() const noexcept { return releases_owed_; }

 private:
  udp::RecvfromMode inner_;
  int releases_owed_ = 0;
};
static_assert(udp::ReceiveMode<CountingMode>);

// Any ReceiveMode runs on the pair, made by the caller's factory on the receiver's socket.
TEST(Loopback, AnyModeRunsOnThePairThroughItsFactory) {
  const Bytes stream = read_file(std::filesystem::path{ITCH_ORACLE_FIXTURES} / "session.itch");
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  itch::FastBook<> book{kCapacity};
  udp::BookDigest digest{book, packets->messages() / 2};
  int fd_seen = -1;
  const auto run = udp::run_loopback(
      *packets, {.send = {}, .sender_core = -1, .receiver_core = -1},
      [&fd_seen](int fd) {
        fd_seen = fd;
        return CountingMode{fd};
      },
      digest, [] {});
  ASSERT_TRUE(run.has_value());
  expect_whole(*run, *packets);
  EXPECT_GE(fd_seen, 0);
  EXPECT_EQ(digest.finish(), by_file(stream, packets->messages() / 2));
  EXPECT_FALSE(run->receiver_pin.pinned);
  EXPECT_EQ(run->receiver_pin.attempt, "no core was requested");
}

// The negative control: one packet lost on the way is a gap of its messages, and the
// book no longer matches the file's.
TEST(Loopback, ALostPacketIsSeenAndTheBookDiffers) {
  const Bytes stream = read_file(std::filesystem::path{ITCH_ORACLE_FIXTURES} / "session.itch");
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  constexpr std::size_t kLost = 7;
  struct Losing {
    explicit Losing(udp::ConnectedSocket through) noexcept : socket{through} {}
    udp::ConnectedSocket socket;
    std::size_t sends = 0;
    udp::SendResult send(std::span<const std::byte> datagram) {
      return sends++ == kLost ? udp::SendResult{.sent = true, .error = 0} : socket.send(datagram);
    }
  };
  const std::uint64_t checkpoint = packets->messages() - 1;
  const auto [run, summary] = by_loopback(
      *packets, {}, checkpoint, [](udp::ConnectedSocket socket) { return Losing{socket}; });
  EXPECT_EQ(run.sequence.gaps, 1U);
  EXPECT_EQ(run.sequence.lost, packets->ref(kLost).messages);
  EXPECT_NE(summary, by_file(stream, checkpoint));
  EXPECT_NE(summary.frame_hash, by_file(stream, checkpoint).frame_hash);
}

// --- Schedules ------------------------------------------------------------------------

TEST(Schedule, TheRateTableIsTheSendersOwnArithmetic) {
  const mold::PacketArray packets = churn_packets();
  constexpr std::uint64_t kRate = 1'000'000;
  const std::vector<std::uint64_t> table = udp::rate_schedule(packets, kRate);
  EXPECT_EQ(table, due_times(packets, 0, kRate));
}

TEST(Schedule, AFeedScheduleFollowsTheItchClockDividedBySpeedup) {
  const Bytes stream = read_file(std::filesystem::path{ITCH_ORACLE_FIXTURES} / "session.itch");
  const auto packets = mold::build_packets(stream, {.max_messages = 3});
  ASSERT_TRUE(packets.has_value());
  const std::vector<std::uint64_t> real = udp::feed_schedule(*packets, 1.0);
  const std::vector<std::uint64_t> fast = udp::feed_schedule(*packets, 4.0);
  ASSERT_EQ(real.size(), packets->size());
  ASSERT_EQ(fast.size(), packets->size());
  EXPECT_EQ(real.front(), 0U);
  for (std::size_t i = 0; i < packets->size(); ++i) {
    const std::uint64_t since = packets->ref(i).timestamp_ns - packets->ref(0).timestamp_ns;
    EXPECT_EQ(real[i], std::max(since, i == 0 ? 0 : real[i - 1])) << i;
    EXPECT_EQ(fast[i], std::max(static_cast<std::uint64_t>(static_cast<double>(since) / 4.0),
                                i == 0 ? 0 : fast[i - 1]))
        << i;
  }
  EXPECT_GT(real.back(), 0U);
  EXPECT_TRUE(udp::feed_schedule(*packets, 0.0).empty());
}

// A message stamped earlier than the one before it is held at the one before, so that no
// packet is due ahead of a packet sent before it.
TEST(Schedule, AFeedScheduleNeverRunsBackwards) {
  Bytes stream = read_file(std::filesystem::path{ITCH_ORACLE_FIXTURES} / "session.itch");
  const auto length_at = [&](std::size_t at) {
    return (std::to_integer<std::size_t>(stream[at]) << 8U) |
           std::to_integer<std::size_t>(stream[at + 1]);
  };
  constexpr std::size_t kStamp = 2 + 5;  // A frame's length, then the message's timestamp.
  constexpr std::size_t kStampBytes = 6;
  const auto stamp_at = [&](std::size_t at) {
    return Bytes(stream.begin() + static_cast<std::ptrdiff_t>(at + kStamp),
                 stream.begin() + static_cast<std::ptrdiff_t>(at + kStamp + kStampBytes));
  };
  // The first frame stamped later than the first, then the frame after it given the first
  // frame's stamp, earlier than its own.
  const Bytes first = stamp_at(0);
  std::size_t at = 0;
  while (at + 2 < stream.size() && stamp_at(at) <= first) {
    at += 2 + length_at(at);
  }
  ASSERT_LT(at + 2, stream.size());
  at += 2 + length_at(at);
  ASSERT_LT(at + kStamp + kStampBytes, stream.size());
  std::ranges::copy(first, stream.begin() + static_cast<std::ptrdiff_t>(at + kStamp));
  const auto packets = mold::build_packets(stream, {.max_messages = 1});
  ASSERT_TRUE(packets.has_value());
  bool backwards = false;
  for (std::size_t i = 1; i < packets->size(); ++i) {
    backwards = backwards || packets->ref(i).timestamp_ns < packets->ref(i - 1).timestamp_ns;
  }
  ASSERT_TRUE(backwards) << "the stream has no stamp earlier than the one before it";
  const std::vector<std::uint64_t> due = udp::feed_schedule(*packets, 1.0);
  ASSERT_EQ(due.size(), packets->size());
  for (std::size_t i = 1; i < due.size(); ++i) {
    EXPECT_GE(due[i], due[i - 1]) << i;
  }
}

TEST(Schedule, TheSenderKeepsADueTableAndRefusesOneOfTheWrongLength) {
  const mold::PacketArray packets = churn_packets();
  std::vector<std::uint64_t> due(packets.size());
  for (std::size_t i = 0; i < due.size(); ++i) {
    due[i] = (40 * i) + (i % 2 == 0 ? 0 : 25);  // Any non-decreasing schedule.
  }
  StepClock clock{3};
  RecordingSocket socket{clock};
  const auto report = udp::send_packets(packets, {.due_ns = due}, clock, socket);
  ASSERT_TRUE(report.has_value());
  EXPECT_TRUE(report->paced);
  EXPECT_EQ(report->target_rate, 0U);
  for (std::size_t i = 0; i < packets.size(); ++i) {
    ASSERT_GE(socket.times[i], report->start_ns + due[i]) << i;
    EXPECT_LT(socket.times[i], report->start_ns + due[i] + 10) << i;
  }
  const std::vector<std::uint64_t> short_table(packets.size() - 1);
  StepClock again{3};
  RecordingSocket unused{again};
  const auto refused = udp::send_packets(packets, {.due_ns = short_table}, again, unused);
  ASSERT_FALSE(refused.has_value());
  EXPECT_EQ(refused.error(), udp::SendError::schedule_mismatch);
  EXPECT_TRUE(unused.times.empty());
}

// --- The kernel's counts, parsed from text --------------------------------------------

TEST(KernelCounters, TheUdpLinesOfSnmpAreReadByName) {
  constexpr std::string_view kSnmp =
      "Tcp: RtoAlgorithm RtoMin\nTcp: 1 200\n"
      "Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors "
      "IgnoredMulti MemErrors\n"
      "Udp: 1000 2 30 4000 25 6 7 8 9\n"
      "UdpLite: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors\n"
      "UdpLite: 1 1 1 1 1 1\n";
  const auto snmp = udp::parse_udp_snmp(kSnmp);
  ASSERT_TRUE(snmp.has_value());
  EXPECT_EQ(snmp.value().in_datagrams, 1000U);
  EXPECT_EQ(snmp.value().no_ports, 2U);
  EXPECT_EQ(snmp.value().in_errors, 30U);
  EXPECT_EQ(snmp.value().out_datagrams, 4000U);
  EXPECT_EQ(snmp.value().rcvbuf_errors, 25U);
  EXPECT_EQ(snmp.value().sndbuf_errors, 6U);
  EXPECT_FALSE(udp::parse_udp_snmp("Udp: InDatagrams NoPorts\nUdp: 1 2\n").has_value())
      << "a missing column must not read as 0";
  EXPECT_FALSE(udp::parse_udp_snmp("Udp: InDatagrams NoPorts InErrors OutDatagrams "
                                   "RcvbufErrors SndbufErrors\nUdp: 1 2 x 4 5 6\n")
                   .has_value());
}

// A count that could not be read is never taken for a zero, so no run is called lossless on
// a counter nobody read.
TEST(KernelCounters, ACountNotReadIsNeverZero) {
  EXPECT_FALSE(udp::Reading{}.read_zero());
  EXPECT_FALSE((udp::Reading{.read = false, .value = 0}).read_zero());
  EXPECT_FALSE((udp::Reading{.read = true, .value = 3}).read_zero());
  EXPECT_TRUE((udp::Reading{.read = true, .value = 0}).read_zero());
}

TEST(KernelCounters, SoftnetColumnsAreHexAndTheSecondIsTheDrops) {
  const auto rows = udp::parse_softnet(
      "0000a0f1 00000000 00000003 00000000 00000000\n00000010 0000001f 00000000 00000000\n");
  ASSERT_TRUE(rows.has_value());
  ASSERT_EQ(rows.value().size(), 2U);
  EXPECT_EQ(rows.value()[0].processed, 0xa0f1U);
  EXPECT_EQ(rows.value()[0].dropped, 0U);
  EXPECT_EQ(rows.value()[0].squeezed, 3U);
  EXPECT_EQ(rows.value()[1].dropped, 0x1fU);
  EXPECT_FALSE(udp::parse_softnet("00000010 zz 00000000\n").has_value());
}

TEST(KernelCounters, EachCpusSoftirqIsItsSeventhColumn) {
  const auto cpus = udp::parse_proc_stat(
      "cpu  100 1 50 9000 3 4 70 0 0 0\ncpu0 10 1 5 900 3 4 7 0 0 0\n"
      "cpu6 20 0 6 800 0 2 33 0 0 0\nintr 12345\n");
  ASSERT_TRUE(cpus.has_value());
  ASSERT_EQ(cpus.value().size(), 2U) << "the all-CPU line is left out";
  EXPECT_EQ(cpus.value()[1].cpu, 6);
  EXPECT_EQ(cpus.value()[1].softirq, 33U);
  EXPECT_EQ(cpus.value()[1].irq, 2U);
  EXPECT_EQ(cpus.value()[1].user, 20U);
  EXPECT_EQ(cpus.value()[1].system, 6U);
  EXPECT_EQ(cpus.value()[0].user, 11U) << "user and nice together";
}

TEST(KernelCounters, ATasksTimesAreCountedFromItsLastParenthesis) {
  // A command name with a space and a parenthesis of its own, as a thread may have.
  // Fields 4 to 13, utime and stime (14, 15), 16 to 38 as zeros, then processor (39).
  std::string line = "4242 (iou-sqp (4) x) S 1 2 3 4 5 6 7 8 9 10 111 222";
  for (int field = 16; field <= 38; ++field) {
    line += " 0";
  }
  line += " 6 0 0\n";
  const auto task = udp::parse_task_stat(line);
  ASSERT_TRUE(task.has_value());
  EXPECT_EQ(task.value().tid, 4242);
  EXPECT_EQ(task.value().comm, "iou-sqp (4) x");
  EXPECT_EQ(task.value().utime, 111U);
  EXPECT_EQ(task.value().stime, 222U);
  EXPECT_EQ(task.value().processor, 6);
  EXPECT_FALSE(udp::parse_task_stat("4242 (short) S 1 2").has_value());
}

TEST(KernelCounters, LinuxReadsEveryLayerAndOtherHostsReadNone) {
  const udp::KernelSnapshot snapshot = udp::read_kernel_snapshot();
  auto pair = udp::open_loopback_pair();
  ASSERT_TRUE(pair.has_value());
#ifdef __linux__
  EXPECT_TRUE(snapshot.udp.has_value());
  EXPECT_TRUE(snapshot.softnet.has_value());
  EXPECT_TRUE(snapshot.cpus.has_value());
  EXPECT_FALSE(snapshot.tasks.empty());
  EXPECT_TRUE(udp::socket_drops(pair->receiver.get()).read);
  EXPECT_TRUE(udp::thread_cpu().read);
  EXPECT_GT(udp::ticks_per_second(), 0);
#else
  EXPECT_FALSE(snapshot.udp.has_value());
  EXPECT_FALSE(snapshot.cpus.has_value());
  EXPECT_FALSE(udp::socket_drops(pair->receiver.get()).read);
  EXPECT_FALSE(udp::thread_cpu().read);
#endif
}

// --- A mode's setup ---------------------------------------------------------------------

// A mode whose setup failed, as a ring's can.
class FailedSetupMode {
 public:
  static constexpr std::string_view kId = "failed setup";
  explicit FailedSetupMode(int /*fd*/) {
    counters_.setup_call = "io_uring_queue_init_params";
    counters_.setup_error = EPERM;
  }
  [[nodiscard]] static udp::WaitResult wait(std::span<udp::Datagram> /*batch*/) noexcept {
    return {.status = udp::WaitStatus::failed, .count = 0, .error = EPERM};
  }
  void release(std::span<const udp::Datagram> /*done*/) noexcept {}
  [[nodiscard]] const udp::ModeCounters& counters() const noexcept { return counters_; }

 private:
  udp::ModeCounters counters_;
};
static_assert(udp::ReceiveMode<FailedSetupMode>);

TEST(Loopback, AModeWhoseSetupFailedIsRefusedBeforeTheSenderStarts) {
  const mold::PacketArray packets = churn_packets();
  CountFrames sink;
  bool started = false;
  const auto run = udp::run_loopback(
      packets, {}, [](int fd) { return FailedSetupMode{fd}; }, sink,
      [&started] { started = true; });
  ASSERT_FALSE(run.has_value());
  EXPECT_EQ(run.error().call, "io_uring_queue_init_params");
  EXPECT_EQ(run.error().error, EPERM);
  EXPECT_FALSE(started);
  EXPECT_EQ(sink.frames, 0U);
}

// --- Every mode this build has ------------------------------------------------------------

TEST(Modes, EachIdIsKnownAndS1IsBuiltEverywhere) {
  EXPECT_TRUE(udp::mode_built("S1"));
  EXPECT_FALSE(udp::is_mode("U6"));
  EXPECT_FALSE(udp::mode_built("U6"));
  for (const std::string_view id : udp::kModeIds) {
    EXPECT_TRUE(udp::is_mode(id));
    if (!udp::mode_built(id)) {
      EXPECT_FALSE(udp::with_mode(id, {}, [](auto /*make*/) { return 1; }).has_value()) << id;
      EXPECT_FALSE(udp::why_not_built(id).empty()) << id;
    }
  }
#ifdef __linux__
  EXPECT_TRUE(udp::mode_built("S2") && udp::mode_built("S3") && udp::mode_built("S4"));
#else
  EXPECT_FALSE(udp::mode_built("S2") || udp::mode_built("S3") || udp::mode_built("S4"));
  EXPECT_FALSE(udp::mode_built("U1") || udp::mode_built("U2") || udp::mode_built("U5"));
#endif
}

std::vector<std::string_view> built_modes() {
  std::vector<std::string_view> ids;
  for (const std::string_view id : udp::kModeIds) {
    if (udp::mode_built(id)) {
      ids.push_back(id);
    }
  }
  return ids;
}

// Each built mode on the pair, through with_mode()'s factory, every fixture arriving whole
// and leaving the book as the file does; paced too.
TEST(Modes, EveryBuiltModeDeliversEveryFixtureWhole) {
  for (const std::string_view id : built_modes()) {
    for (const auto& entry : std::filesystem::directory_iterator{ITCH_ORACLE_FIXTURES}) {
      if (entry.path().extension() != ".itch") {
        continue;
      }
      SCOPED_TRACE(std::string{id} + " " + entry.path().filename().string());
      const Bytes stream = read_file(entry.path());
      const auto packets = mold::build_packets(stream, {});
      ASSERT_TRUE(packets.has_value());
      const std::uint64_t checkpoint = (packets->messages() + 1) / 2;
      const auto book = std::make_unique<itch::FastBook<>>(kCapacity);
      udp::BookDigest digest{*book, checkpoint};
      const auto run = udp::with_mode(id, {}, [&](auto make) {
        return udp::run_loopback(*packets, {.send = {.messages_per_second = 4'000'000}}, make,
                                 digest, [] {});
      });
      ASSERT_TRUE(run.has_value());
      ASSERT_TRUE(run->has_value()) << (*run).error().call << " errno " << (*run).error().error;
      expect_whole(**run, *packets);
      EXPECT_EQ((*run)->mode.truncated, 0U);
      EXPECT_EQ((*run)->mode.failures, 0U);
      EXPECT_EQ(digest.finish(), by_file(stream, checkpoint));
    }
  }
}

TEST(Modes, EveryBuiltModeTimesOutWhenNothingComes) {
  for (const std::string_view id : built_modes()) {
    SCOPED_TRACE(id);
    auto pair = udp::open_loopback_pair();
    ASSERT_TRUE(pair.has_value());
    constexpr std::chrono::milliseconds kTimeout{50};
    ASSERT_TRUE(udp::set_receive_timeout(pair->receiver.get(), kTimeout));
    const auto said = udp::with_mode(id, {.idle_timeout = kTimeout}, [&](auto make) {
      auto mode = make(pair->receiver.get());
      EXPECT_EQ(mode.counters().setup_error, 0) << mode.counters().setup_call;
      std::array<udp::Datagram, udp::kMostBatch> batch{};
      const auto started = std::chrono::steady_clock::now();
      const udp::WaitResult result = mode.wait(batch);
      const auto waited = std::chrono::steady_clock::now() - started;
      EXPECT_GE(waited, kTimeout / 2);
      EXPECT_LT(waited, std::chrono::seconds{5});
      EXPECT_EQ(mode.counters().timeouts, 1U);
      return result.status;
    });
    ASSERT_TRUE(said.has_value());
    EXPECT_EQ(*said, udp::WaitStatus::idle_timeout);
  }
}

// Sends each datagram of `sizes`, numbered in its first byte, then drains the mode until
// it times out, releasing each batch; returns the bytes in arrival order, and the largest
// batch one wait returned.
struct Drained {
  std::vector<Bytes> datagrams;
  std::size_t largest_batch = 0;
};

template <class Mode>
Drained send_then_drain(Mode& mode, const udp::ConnectedSocket& sender,
                        const std::vector<std::size_t>& sizes) {
  for (std::size_t i = 0; i < sizes.size(); ++i) {
    Bytes datagram(sizes[i], static_cast<std::byte>(i));
    EXPECT_TRUE(sender.send(datagram).sent) << i;
  }
  Drained out;
  std::array<udp::Datagram, udp::kMostBatch> batch{};
  for (;;) {
    const udp::WaitResult result = mode.wait(batch);
    if (result.status != udp::WaitStatus::datagrams) {
      EXPECT_EQ(result.status, udp::WaitStatus::idle_timeout) << "errno " << result.error;
      return out;
    }
    out.largest_batch = std::max(out.largest_batch, result.count);
    for (const udp::Datagram& datagram : std::span{batch}.first(result.count)) {
      out.datagrams.emplace_back(datagram.bytes.begin(), datagram.bytes.end());
    }
    mode.release(std::span<const udp::Datagram>{batch}.first(result.count));
  }
}

struct Pair {
  udp::LoopbackPair sockets;
  udp::ConnectedSocket sender;
};

std::optional<Pair> pair_for(std::chrono::milliseconds timeout, int receive_buffer = 1'048'576) {
  auto pair = udp::open_loopback_pair();
  if (!pair || !udp::set_receive_timeout(pair->receiver.get(), timeout) ||
      !udp::set_receive_buffer(pair->receiver.get(), receive_buffer)) {
    return std::nullopt;
  }
  const int fd = pair->sender.get();
  return Pair{.sockets = std::move(*pair), .sender = udp::ConnectedSocket{fd}};
}

// What was queued before the first wait arrives whole and in order, and a mode that holds
// many takes more than one in a wait.
TEST(Modes, QueuedDatagramsArriveInOrderAndTheBatchingModesTakeManyAtOnce) {
  constexpr std::chrono::milliseconds kTimeout{100};
  const std::vector<std::size_t> sizes{20, 1400, 600, 1400, 20, 900, 1, 1400, 300, 1200};
  for (const std::string_view id : built_modes()) {
    SCOPED_TRACE(id);
    auto pair = pair_for(kTimeout);
    ASSERT_TRUE(pair.has_value());
    const auto drained = udp::with_mode(id, {.idle_timeout = kTimeout}, [&](auto make) {
      auto mode = make(pair->sockets.receiver.get());
      Drained out = send_then_drain(mode, pair->sender, sizes);
      EXPECT_EQ(mode.counters().datagrams, sizes.size());
      EXPECT_EQ(mode.counters().truncated, 0U);
      return out;
    });
    ASSERT_TRUE(drained.has_value());
    ASSERT_EQ(drained->datagrams.size(), sizes.size());
    for (std::size_t i = 0; i < sizes.size(); ++i) {
      EXPECT_EQ(drained->datagrams[i], Bytes(sizes[i], static_cast<std::byte>(i))) << i;
    }
    if (id == "S3" || id == "S4") {
      EXPECT_EQ(drained->largest_batch, sizes.size()) << "recvmmsg took them one at a time";
    } else if (!udp::uses_slots(id)) {
      EXPECT_EQ(drained->largest_batch, 1U);
    }
  }
}

// A datagram larger than a mode's buffer is counted as cut short, never passed off whole.
TEST(Modes, ADatagramPastTheSlotIsCountedTruncated) {
  constexpr std::chrono::milliseconds kTimeout{100};
  constexpr std::size_t kSlot = 512;
  for (const std::string_view id : built_modes()) {
    if (!udp::uses_slots(id)) {
      continue;
    }
    SCOPED_TRACE(id);
    auto pair = pair_for(kTimeout);
    ASSERT_TRUE(pair.has_value());
    const auto counted =
        udp::with_mode(id, {.slot_bytes = kSlot, .idle_timeout = kTimeout}, [&](auto make) {
          auto mode = make(pair->sockets.receiver.get());
          const Drained out = send_then_drain(mode, pair->sender, {100, 2000, 100});
          EXPECT_EQ(out.datagrams.size(), 3U);
          for (const Bytes& datagram : out.datagrams) {
            EXPECT_LE(datagram.size(), kSlot);
          }
          return mode.counters().truncated;
        });
    ASSERT_TRUE(counted.has_value());
    EXPECT_EQ(*counted, 1U);
  }
}

// S2 and U3 read SO_RXQ_OVFL: a socket that has dropped says so. The kernel stamps each
// datagram with the drops before it was queued, so a flood that overflows the socket is
// read out, and then a few more are sent, which carry the count.
TEST(Modes, TheModesThatReadControlMessagesSeeTheSocketsDrops) {
  constexpr std::chrono::milliseconds kTimeout{100};
  std::size_t checked = 0;
  for (const std::string_view id : built_modes()) {
    SCOPED_TRACE(id);
    auto pair = pair_for(kTimeout, 4096);  // Room for a few datagrams; the rest drop.
    ASSERT_TRUE(pair.has_value());
    const auto seen = udp::with_mode(id, {.idle_timeout = kTimeout}, [&](auto make) {
      auto mode = make(pair->sockets.receiver.get());
      if (!mode.counters().rxq_overflow_set) {
        return std::optional<std::uint64_t>{};
      }
      const Drained flood =
          send_then_drain(mode, pair->sender, std::vector<std::size_t>(200, 1000));
      EXPECT_LT(flood.datagrams.size(), 200U);
      const Drained after = send_then_drain(mode, pair->sender, {100, 100, 100});
      EXPECT_EQ(after.datagrams.size(), 3U);
      return std::optional<std::uint64_t>{mode.counters().rxq_overflow};
    });
    ASSERT_TRUE(seen.has_value());
    if (!seen->has_value()) {
      EXPECT_TRUE(id != "S2" && id != "U3") << "S2 and U3 must read SO_RXQ_OVFL";
      continue;
    }
    ++checked;
    EXPECT_GT(**seen, 0U);
    const udp::Reading socket = udp::socket_drops(pair->sockets.receiver.get());
    EXPECT_EQ(socket.read ? socket.value : **seen, **seen)
        << "SO_RXQ_OVFL and the socket's own count disagree";
  }
  EXPECT_EQ(checked != 0, udp::mode_built("S2") || udp::mode_built("U3"));
}

// A multishot mode that runs out of provided buffers ends its request with -ENOBUFS,
// arms it again, and still takes every datagram the socket held (IU-30).
TEST(Modes, AMultishotModeOutOfBuffersRearmsAndLosesNothing) {
  constexpr std::chrono::milliseconds kTimeout{200};
  for (const std::string_view id : built_modes()) {
    if (!id.starts_with('U') || id == "U1") {
      continue;
    }
    SCOPED_TRACE(id);
    auto pair = pair_for(kTimeout);
    ASSERT_TRUE(pair.has_value());
    const std::vector<std::size_t> sizes(40, 700);
    const auto drained =
        udp::with_mode(id, {.ring_buffers = 4, .idle_timeout = kTimeout}, [&](auto make) {
          auto mode = make(pair->sockets.receiver.get());
          Drained out = send_then_drain(mode, pair->sender, sizes);
          EXPECT_GE(mode.counters().no_buffers, 1U);
          EXPECT_GE(mode.counters().rearms, 1U);
          EXPECT_EQ(mode.counters().cq_overflow, 0U);
          return out;
        });
    ASSERT_TRUE(drained.has_value());
    ASSERT_EQ(drained->datagrams.size(), sizes.size());
    for (std::size_t i = 0; i < sizes.size(); ++i) {
      EXPECT_EQ(drained->datagrams[i].front(), static_cast<std::byte>(i)) << i;
    }
  }
}

// A ring mode with a buffer count the kernel would refuse fails its setup, and the loopback
// refuses the run.
TEST(Modes, ARingModeWithABadBufferCountFailsItsSetup) {
  for (const std::string_view id : built_modes()) {
    if (!id.starts_with('U') || id == "U1") {
      continue;
    }
    SCOPED_TRACE(id);
    const mold::PacketArray packets = churn_packets();
    CountFrames sink;
    const auto run = udp::with_mode(id, {.ring_buffers = 3}, [&](auto make) {
      return udp::run_loopback(packets, {}, make, sink, [] {});
    });
    ASSERT_TRUE(run.has_value());
    ASSERT_FALSE(run->has_value());
    EXPECT_EQ(run->error().call, "ModeOptions");
    EXPECT_EQ(run->error().error, EINVAL);
  }
}

}  // namespace
// NOLINTEND(bugprone-unchecked-optional-access)
