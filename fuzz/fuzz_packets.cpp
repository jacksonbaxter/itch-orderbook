// A plan and a BinaryFILE stream through mold::build_packets, and the packets back through
// the decoder and a FeedHandler to the stream's frames.
//
// The input's first 22 bytes are the plan, big-endian, any value allowed, so that plans the
// builder must refuse come up too; bytes past the end of a short input read as zero:
//
//   0   2  max_datagram      6   4  flush_gap_ns       14  8  first_sequence
//   2   2  max_messages      10  4  heartbeat_gap_ns
//   4   1  first_frame
//   5   1  frame_limit, 255 for none
//
// The rest is the stream. A model written here from the plan's comments in mold_packets.hpp
// says, without the builder, whether the plan is refused, where the stream fails if it does,
// which frames make the slice and before which of them a heartbeat is due. Then every packet
// has to decode, carry the session and the next sequence, fit the cap and the count, and hold
// the next run of the slice's frames byte for byte; each packet has to close only when the
// next frame would pass the cap or the count, or the flush gap or a heartbeat calls for it;
// each heartbeat has to stand where the model says, and the array has to end on a heartbeat
// and the end of session. Last, one feed takes every packet in order and must hand on the
// slice's frames, each once, with every status and byte as the stream has it, no gap, no
// loss and no duplicate.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <span>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/mold.hpp"
#include "itch/mold_feed.hpp"
#include "itch/mold_packets.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace {

namespace mold = itch::mold;
using itch::Frame;
using itch::FrameStatus;
using mold::PacketStatus;

constexpr std::size_t kPlanBytes = 22;
constexpr std::uint8_t kNoLimit = 255;

// Big-endian, `width` bytes from `at`, reading zero past the end.
std::uint64_t field(std::span<const std::byte> bytes, std::size_t at, std::size_t width) {
  std::uint64_t value = 0;
  for (std::size_t i = at; i < at + width; ++i) {
    value = (value << 8U) | (i < bytes.size() ? std::to_integer<std::uint64_t>(bytes[i]) : 0U);
  }
  return value;
}

mold::PacketPlan plan_of(std::span<const std::byte> input) {
  const std::uint64_t limit = field(input, 5, 1);
  return {.session = mold::make_session("FUZZ000001"),
          .first_sequence = field(input, 14, 8),
          .max_datagram = static_cast<std::size_t>(field(input, 0, 2)),
          .max_messages = static_cast<std::uint16_t>(field(input, 2, 2)),
          .first_frame = field(input, 4, 1),
          .frame_limit = limit == kNoLimit ? std::numeric_limits<std::uint64_t>::max() : limit,
          .flush_gap_ns = field(input, 6, 4),
          .heartbeat_gap_ns = field(input, 10, 4)};
}

std::size_t block_of(const Frame& frame) { return mold::kBlockPrefix + frame.payload.size(); }

// One frame of the slice, with its ITCH time if it has one.
struct SliceFrame {
  Frame frame;
  bool has_ts = false;
  std::uint64_t ts = 0;
  bool have_before = false;  // Whether an earlier frame of the slice had a time,
  std::uint64_t before = 0;  // and the last such time.
};

// What the builder must do with the stream, worked out without it.
struct Model {
  std::optional<mold::BuildFailure> failure;
  std::vector<SliceFrame> slice;
  bool have_last = false;
  std::uint64_t last = 0;  // The slice's last time, or 0.
};

Model model(std::span<const std::byte> stream, const mold::PacketPlan& plan) {
  Model m;
  if (plan.max_datagram < mold::kHeaderBytes + mold::kBlockPrefix ||
      plan.max_datagram > mold::kMostDatagram || plan.max_messages == 0 ||
      plan.max_messages > mold::kMostBlocks ||
      plan.first_sequence > std::numeric_limits<std::uint64_t>::max() - stream.size()) {
    m.failure = mold::BuildFailure{.error = mold::BuildError::bad_plan, .offset = 0};
    return m;
  }
  itch::FrameReader reader{stream};
  for (std::uint64_t skipped = 0; skipped < plan.first_frame; ++skipped) {
    const Frame frame = reader.next();
    if (frame.status == FrameStatus::end_of_input) {
      return m;
    }
    if (itch::is_terminal(frame.status)) {
      m.failure =
          mold::BuildFailure{.error = mold::BuildError::truncated_input, .offset = frame.offset};
      return m;
    }
  }
  for (std::uint64_t taken = 0; taken < plan.frame_limit; ++taken) {
    const Frame frame = reader.next();
    if (frame.status == FrameStatus::end_of_input) {
      break;
    }
    if (itch::is_terminal(frame.status)) {
      m.failure =
          mold::BuildFailure{.error = mold::BuildError::truncated_input, .offset = frame.offset};
      return m;
    }
    if (mold::kHeaderBytes + block_of(frame) > plan.max_datagram) {
      m.failure =
          mold::BuildFailure{.error = mold::BuildError::oversized_frame, .offset = frame.offset};
      return m;
    }
    // Every known type has its 6-byte time at 5.
    SliceFrame s{.frame = frame, .have_before = m.have_last, .before = m.last};
    if (frame.status == FrameStatus::message && frame.payload.size() >= 11) {
      s.has_ts = true;
      s.ts = itch::read_be48(frame.payload.data() + 5);
      m.have_last = true;
      m.last = s.ts;
    }
    m.slice.push_back(s);
  }
  return m;
}

// Whether a heartbeat must come before frame j: it has a time at least the gap past the last.
bool heartbeat_due(const SliceFrame& s, const mold::PacketPlan& plan) {
  return s.has_ts && s.have_before && plan.heartbeat_gap_ns != 0 && s.ts > s.before &&
         s.ts - s.before >= plan.heartbeat_gap_ns;
}

// Whether frame k must start a new packet for the flush gap, the packet open since frame j.
bool flush_due(const Model& m, std::size_t j, std::size_t k, const mold::PacketPlan& plan) {
  const SliceFrame& s = m.slice[k];
  if (plan.flush_gap_ns == 0 || !s.has_ts) {
    return false;
  }
  for (std::size_t i = j; i < k; ++i) {
    if (m.slice[i].has_ts) {
      return s.ts > m.slice[i].ts && s.ts - m.slice[i].ts > plan.flush_gap_ns;
    }
  }
  return false;
}

// A data packet's time: its first frame's that has one, or the last before it.
std::uint64_t packet_ts(const Model& m, std::size_t j, std::size_t count) {
  for (std::size_t i = j; i < j + count; ++i) {
    if (m.slice[i].has_ts) {
      return m.slice[i].ts;
    }
  }
  return m.slice[j].before;
}

void check_data_packet(const Model& m, const mold::PacketPlan& plan,
                       std::span<const std::byte> stream, const mold::Packet& packet,
                       const mold::PacketRef& ref, std::size_t j) {
  const std::uint16_t count = packet.header.count;
  FUZZ_CHECK(count <= plan.max_messages);
  FUZZ_CHECK(ref.messages == count);
  FUZZ_CHECK(j + count <= m.slice.size());
  for (std::size_t k = j + 1; k < j + count; ++k) {
    FUZZ_CHECK(!heartbeat_due(m.slice[k], plan));
    FUZZ_CHECK(!flush_due(m, j, k, plan));
  }
  const Frame& first = m.slice[j].frame;
  const Frame& last = m.slice[j + count - 1].frame;
  const std::size_t bytes = last.offset + block_of(last) - first.offset;
  FUZZ_CHECK(std::ranges::equal(packet.blocks, stream.subspan(first.offset, bytes)));
  FUZZ_CHECK(ref.timestamp_ns == packet_ts(m, j, count));
  // It closed only because it had to.
  if (j + count < m.slice.size() && !heartbeat_due(m.slice[j + count], plan)) {
    const std::size_t next = block_of(m.slice[j + count].frame);
    FUZZ_CHECK(mold::kHeaderBytes + bytes + next > plan.max_datagram ||
               count == plan.max_messages || flush_due(m, j, j + count, plan));
  }
}

void check_packets(const Model& m, const mold::PacketPlan& plan, std::span<const std::byte> stream,
                   const mold::PacketArray& packets) {
  FUZZ_CHECK(packets.size() >= 2);
  FUZZ_CHECK(packets.messages() == m.slice.size());
  FUZZ_CHECK(packets.next_sequence() == plan.first_sequence + m.slice.size());
  std::size_t at = 0;
  std::size_t j = 0;  // The next frame of the slice.
  std::uint64_t heartbeats = 0;
  bool beat = false;  // A heartbeat came since the last data packet.
  for (std::size_t i = 0; i < packets.size(); ++i) {
    const mold::PacketRef& ref = packets.ref(i);
    const std::span<const std::byte> datagram = packets.packet(i);
    FUZZ_CHECK(ref.offset == at);
    FUZZ_CHECK(datagram.size() == ref.size);
    FUZZ_CHECK(ref.size <= plan.max_datagram);
    at += ref.size;
    const mold::Packet packet = mold::decode(datagram);
    FUZZ_CHECK(packet.header.session == plan.session);
    FUZZ_CHECK(packet.header.sequence == plan.first_sequence + j);
    if (i + 2 >= packets.size()) {
      // The close: a heartbeat, then the end of session, after the slice's last frame.
      FUZZ_CHECK(packet.status == (i + 1 == packets.size() ? PacketStatus::end_of_session
                                                           : PacketStatus::heartbeat));
      FUZZ_CHECK(j == m.slice.size());
      FUZZ_CHECK(ref.messages == 0);
      FUZZ_CHECK(ref.timestamp_ns == m.last);
      heartbeats += packet.status == PacketStatus::heartbeat ? 1U : 0U;
    } else if (packet.status == PacketStatus::heartbeat) {
      FUZZ_CHECK(!beat);
      FUZZ_CHECK(j < m.slice.size() && heartbeat_due(m.slice[j], plan));
      FUZZ_CHECK(ref.messages == 0);
      FUZZ_CHECK(ref.timestamp_ns == m.slice[j].before);
      beat = true;
      ++heartbeats;
    } else {
      FUZZ_CHECK(packet.status == PacketStatus::messages);
      FUZZ_CHECK(j < m.slice.size());
      FUZZ_CHECK(beat == heartbeat_due(m.slice[j], plan));
      check_data_packet(m, plan, stream, packet, ref, j);
      j += packet.header.count;
      beat = false;
    }
  }
  FUZZ_CHECK(at == packets.bytes().size());
  FUZZ_CHECK(heartbeats == packets.heartbeats());
}

struct Collect {
  std::vector<Frame> frames;
  void operator()(const Frame& frame) { frames.push_back(frame); }
};

// Every packet, in order, through one feed: the slice's frames come out, and nothing is lost.
void check_round_trip(const Model& m, const mold::PacketPlan& plan,
                      const mold::PacketArray& packets) {
  Collect sink;
  mold::FeedHandler feed{plan.session, plan.first_sequence, sink};
  std::uint64_t data_packets = 0;
  for (std::size_t i = 0; i < packets.size(); ++i) {
    feed.on_datagram(packets.packet(i));
    data_packets += packets.ref(i).messages != 0 ? 1U : 0U;
  }
  FUZZ_CHECK(sink.frames.size() == m.slice.size());
  for (std::size_t k = 0; k < sink.frames.size(); ++k) {
    FUZZ_CHECK(sink.frames[k].status == m.slice[k].frame.status);
    FUZZ_CHECK(std::ranges::equal(sink.frames[k].payload, m.slice[k].frame.payload));
  }
  const mold::SequenceCounts& sequence = feed.sequence().counts();
  FUZZ_CHECK(feed.ended());
  FUZZ_CHECK(feed.sequence().next_expected() == packets.next_sequence());
  FUZZ_CHECK(sequence.packets == packets.size());
  FUZZ_CHECK(sequence.admitted == m.slice.size());
  FUZZ_CHECK(sequence.gaps == 0 && sequence.lost == 0);
  FUZZ_CHECK(sequence.duplicate_packets == 0 && sequence.duplicate_messages == 0);
  FUZZ_CHECK(sequence.heartbeats == packets.heartbeats());
  FUZZ_CHECK(sequence.ends == 1 && sequence.after_end == 0);
  FUZZ_CHECK(sequence.foreign_session == 0 && sequence.out_of_range == 0);
  const mold::FeedCounts& counts = feed.counts();
  FUZZ_CHECK(counts.datagrams == packets.size());
  FUZZ_CHECK(counts.bytes == packets.bytes().size());
  FUZZ_CHECK(counts.frames == m.slice.size());
  FUZZ_CHECK(counts.by_status[static_cast<std::size_t>(PacketStatus::messages)] == data_packets);
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const auto input = std::as_bytes(std::span{data, size});
  const mold::PacketPlan plan = plan_of(input);
  const std::span<const std::byte> stream = input.subspan(std::min(size, kPlanBytes));

  const Model m = model(stream, plan);
  const auto packets = mold::build_packets(stream, plan);
  if (const std::optional<mold::BuildFailure>& failure = m.failure) {
    FUZZ_CHECK(!packets.has_value());
    FUZZ_CHECK(packets.error().error == failure->error);
    FUZZ_CHECK(packets.error().offset == failure->offset);
    return 0;
  }
  FUZZ_CHECK(packets.has_value());
  FUZZ_CHECK(packets->plan().max_datagram == plan.max_datagram);
  check_packets(m, plan, stream, *packets);
  check_round_trip(m, plan, *packets);
  return 0;
}
