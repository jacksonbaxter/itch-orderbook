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
#include <ranges>
#include <span>
#include <string>
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
#include "udp/loopback.hpp"
#include "udp/receive_mode.hpp"
#include "udp/sender.hpp"
#include "udp/socket.hpp"

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
class RecordingSocket {
 public:
  explicit RecordingSocket(const StepClock& clock) : clock_{&clock} {}

  [[nodiscard]] udp::SendResult send(std::span<const std::byte> datagram) {
    const std::size_t index = times.size();
    times.push_back(clock_->peek());
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

 private:
  const StepClock* clock_;
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
  const double elapsed = static_cast<double>(report->end_ns - report->start_ns);
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
  const std::uint64_t failed = packets.ref(2).messages + packets.ref(5).messages +
                               packets.ref(7).messages + packets.ref(9).messages;
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
  void release(std::span<const udp::Datagram> done) { released.push_back(done.size()); }
  [[nodiscard]] const udp::ModeCounters& counters() const noexcept { return counters_; }

  std::vector<std::size_t> released;

 private:
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
  ASSERT_EQ(mode.released.size(), batches.size());
  for (std::size_t i = 0; i < batches.size(); ++i) {
    EXPECT_EQ(mode.released[i], batches[i].size());
  }
}

TEST(ReceiveLoop, ItStopsAtTheEndOfSessionWithTheRestUnread) {
  const Bytes stream = itch::fixtures::all_messages_stream();
  const auto packets = mold::build_packets(stream, {});
  ASSERT_TRUE(packets.has_value());
  std::vector<std::vector<Bytes>> batches{datagrams_of(*packets), datagrams_of(*packets)};
  ScriptedMode mode{batches};
  CountFrames sink;
  mold::FeedHandler feed{packets->plan().session, 1, sink};
  std::array<udp::Datagram, udp::kMostBatch> batch{};
  const udp::ReceiveOutcome outcome = udp::receive_until_end(mode, feed, batch);
  EXPECT_EQ(outcome.end, udp::ReceiveEnd::end_of_session);
  EXPECT_EQ(mode.released.size(), 1U);
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
  const auto size = udp::set_receive_buffer(pair->receiver.get(), 1 << 20);
  ASSERT_TRUE(size.has_value());
  EXPECT_EQ(size->requested, 1 << 20);
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

udp::ReplaySummary by_file(std::span<const std::byte> stream, std::uint64_t checkpoint) {
  itch::FastBook<> book{kCapacity};
  udp::BookDigest digest{book, checkpoint};
  udp::replay_frames(stream, 0, ~0ULL, digest);
  return digest.finish();
}

template <class Wrap = udp::PlainSocket>
std::pair<udp::LoopbackRun, udp::ReplaySummary> by_loopback(const mold::PacketArray& packets,
                                                            const udp::LoopbackConfig& config,
                                                            std::uint64_t checkpoint,
                                                            Wrap wrap = {}) {
  itch::FastBook<> book{kCapacity};
  udp::BookDigest digest{book, checkpoint};
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
    udp::ConnectedSocket socket;
    std::size_t sends = 0;
    udp::SendResult send(std::span<const std::byte> datagram) {
      return sends++ == kLost ? udp::SendResult{.sent = true, .error = 0} : socket.send(datagram);
    }
  };
  const std::uint64_t checkpoint = packets->messages() - 1;
  const auto [run, summary] =
      by_loopback(*packets, {}, checkpoint,
                  [](udp::ConnectedSocket socket) { return Losing{.socket = socket}; });
  EXPECT_EQ(run.sequence.gaps, 1U);
  EXPECT_EQ(run.sequence.lost, packets->ref(kLost).messages);
  EXPECT_NE(summary, by_file(stream, checkpoint));
  EXPECT_NE(summary.frame_hash, by_file(stream, checkpoint).frame_hash);
}

}  // namespace
