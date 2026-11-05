// The book's hot-loop variants leave the book exactly as apply() alone does.
//
// The look-ahead reader (itch/lookahead.hpp) hands over the frames FrameReader does, in
// order, and asks the book for each stage of each frame once, nearer the frame stage by
// stage; and with it running ahead at any distance, every arm ends every message byte for
// byte the book it would have been without it. Each dispatch form of bench/dispatch.hpp
// gives the same statuses and the same book as apply(). The lookup script of
// bench/lookup_script.hpp records every find, answers with it, and catches an entry that
// is wrong; a book that runs on it is the book that ran without it.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <random>
#include <span>
#include <string_view>
#include <type_traits>
#include <vector>

#include "dispatch.hpp"
#include "itch/book.hpp"
#include "itch/lookahead.hpp"
#include "itch/reader.hpp"
#include "lookup_script.hpp"
#include "oracle/wire_builder.hpp"
#include "support/fixtures.hpp"

namespace {

namespace wire = itch::oracle::wire;
using itch::ApplyStatus;
using itch::Frame;
using itch::FrameStatus;

// Every fixture message, then orders churned through every order path on three locates,
// with one new reference in seven past the small reach the books below are given, so that
// the paged index falls back, and a few frames the reader walks past without a message.
std::vector<std::byte> churn_stream() {
  std::vector<std::byte> stream;
  const auto put = [&stream](const wire::Bytes& message) {
    itch::fixtures::append_frame(stream, message);
  };
  for (const auto& message : itch::fixtures::all_messages()) {
    put(message);
  }
  // The same stream every run.
  // NOLINTNEXTLINE(cert-msc32-c,cert-msc51-cpp,bugprone-random-generator-seed)
  std::mt19937 random{20191230};
  std::vector<std::pair<std::uint16_t, std::uint64_t>> live;
  std::uint64_t next_reference = 1;
  const auto fresh = [&next_reference] {
    const std::uint64_t reference = next_reference++;
    return reference % 7 == 0 ? reference | (std::uint64_t{1} << 40U) : reference;
  };
  for (int step = 0; step < 20000; ++step) {
    const auto price = static_cast<std::uint32_t>(1000 + ((random() % 48) * 100));
    const auto locate = static_cast<std::uint16_t>(1 + (random() % 3));
    if (step % 997 == 0) {
      put(wire::Bytes(5, std::byte{0x7A}));  // An unknown type, walked past.
    }
    if (live.size() < 200) {
      const std::uint64_t reference = fresh();
      put((step % 3 == 0 ? wire::add_attributed : wire::add)(
          locate, reference, step % 2 == 0 ? 'B' : 'S', 300, price, 1));
      live.emplace_back(locate, reference);
      continue;
    }
    const std::size_t pick = random() % live.size();
    const auto [on, reference] = live[pick];
    switch (random() % 5) {
      case 0: put(wire::execute(on, reference, 1)); break;
      case 1: put(wire::execute_with_price(on, reference, 1, 77)); break;
      case 2: put(wire::cancel(on, reference, 1)); break;
      case 3: {
        const std::uint64_t replaced = fresh();
        put(wire::replace(on, reference, replaced, 300, price));
        live[pick].second = replaced;
        break;
      }
      default:
        put(wire::erase(on, reference));
        live[pick] = live.back();
        live.pop_back();
        break;
    }
  }
  put(wire::execute(1, 999'999, 1));  // An unknown reference, refused.
  return stream;
}

constexpr itch::BookCapacity kCapacity{
    .orders = 1024, .levels = 1024, .index_pages = 4, .index_bits = 20, .level_slots = 4096};

template <class Index, class Levels>
using Book = itch::FastBook<itch::NoWriteObserver, Index, Levels>;

constexpr std::array kDistances{4U, 5U, 8U, 16U, 32U, 64U};

// A book that only notes which stage it was asked for on which frame.
struct StageLog {
  static constexpr unsigned kPrefetchStages = 4;
  struct Call {
    const std::byte* payload;
    unsigned stage;
  };
  void prefetch_stage(std::span<const std::byte> payload, unsigned stage) const noexcept {
    calls.push_back({payload.data(), stage});
  }
  mutable std::vector<Call> calls;
};

TEST(Lookahead, HandsOverEveryFrameFrameReaderDoesInOrder) {
  std::vector<std::byte> stream = churn_stream();
  stream.push_back(std::byte{0x00});  // A stray byte, so that the walk ends truncated.
  for (const unsigned distance : {0U, 4U, 9U, 64U}) {
    SCOPED_TRACE(distance);
    const StageLog log;
    itch::FrameReader plain{stream};
    itch::LookaheadReader<StageLog> ahead{stream, log, distance};
    std::uint64_t frames = 0;
    for (;;) {
      const Frame want = plain.next();
      const Frame got = ahead.next();
      ASSERT_EQ(got.status, want.status);
      ASSERT_EQ(got.offset, want.offset);
      ASSERT_EQ(got.payload.data(), want.payload.data());
      ASSERT_EQ(got.payload.size(), want.payload.size());
      if (itch::is_terminal(want.status)) {
        EXPECT_EQ(ahead.next().status, want.status) << "the terminal frame is handed over again";
        break;
      }
      ++frames;
    }
    EXPECT_EQ(ahead.counters().messages, plain.counters().messages);
    EXPECT_GT(frames, 20000U);
    if (distance == 0) {
      EXPECT_TRUE(log.calls.empty()) << "a distance of 0 looks nothing ahead";
    }
  }
}

// Each stage of each message, once, at max(stages - s, distance >> s) frames ahead, stage
// by stage nearer the frame; a frame too near the start for a stage never gets it.
TEST(Lookahead, AsksForEachStageOfEachMessageOnceNearerStageByStage) {
  const std::vector<std::byte> stream = churn_stream();
  for (const unsigned distance : kDistances) {
    SCOPED_TRACE(distance);
    const StageLog log;
    itch::LookaheadReader<StageLog> ahead{stream, log, distance};
    std::vector<const std::byte*> handed;
    std::vector<FrameStatus> statuses;
    for (Frame frame = ahead.next(); !itch::is_terminal(frame.status); frame = ahead.next()) {
      handed.push_back(frame.payload.data());
      statuses.push_back(frame.status);
    }
    // Where each call landed, as a frame number, from the payload it named.
    std::vector<std::array<int, StageLog::kPrefetchStages>> asked(handed.size());
    for (auto& row : asked) {
      row.fill(0);
    }
    std::vector<std::array<std::size_t, StageLog::kPrefetchStages>> when(handed.size());
    std::size_t call = 0;
    for (const StageLog::Call& c : log.calls) {
      std::size_t at = 0;
      while (at < handed.size() && handed[at] != c.payload) {
        ++at;
      }
      ASSERT_LT(at, handed.size()) << "a stage for a payload never handed over";
      ++asked[at][c.stage];
      when[at][c.stage] = call++;
    }
    for (std::size_t frame = 0; frame < handed.size(); ++frame) {
      for (unsigned stage = 0; stage < StageLog::kPrefetchStages; ++stage) {
        const unsigned lead = itch::LookaheadReader<StageLog>::ahead(distance, stage);
        const bool due = statuses[frame] == FrameStatus::message && frame >= lead;
        ASSERT_EQ(asked[frame][stage], due ? 1 : 0)
            << "frame " << frame << " stage " << stage << " lead " << lead;
        if (due && stage > 0 && asked[frame][stage - 1] == 1) {
          EXPECT_LT(when[frame][stage - 1], when[frame][stage]) << "frame " << frame;
        }
      }
    }
    for (unsigned stage = 1; stage < StageLog::kPrefetchStages; ++stage) {
      EXPECT_LT(itch::LookaheadReader<StageLog>::ahead(distance, stage),
                itch::LookaheadReader<StageLog>::ahead(distance, stage - 1));
    }
    EXPECT_EQ(itch::LookaheadReader<StageLog>::ahead(distance, 0), distance);
    EXPECT_GE(itch::LookaheadReader<StageLog>::ahead(distance, StageLog::kPrefetchStages - 1), 1U);
  }
}

TEST(Lookahead, TakesOnlyDistancesEachArmCanStage) {
  using Paged = Book<itch::PagedIndex<>, itch::FlatLevels>;
  using Hash = Book<itch::HashIndex, itch::LinkedLevels>;
  EXPECT_EQ(Paged::kPrefetchStages, 4U);
  EXPECT_EQ(Hash::kPrefetchStages, 3U);
  EXPECT_TRUE(itch::LookaheadReader<Paged>::valid_distance(0));
  EXPECT_FALSE(itch::LookaheadReader<Paged>::valid_distance(3));
  EXPECT_TRUE(itch::LookaheadReader<Paged>::valid_distance(4));
  EXPECT_TRUE(itch::LookaheadReader<Hash>::valid_distance(3));
  EXPECT_TRUE(itch::LookaheadReader<Paged>::valid_distance(64));
  EXPECT_FALSE(itch::LookaheadReader<Paged>::valid_distance(65));
}

// With the reader running ahead, the book after each message is the book without it,
// byte for byte, on every arm and at every distance.
template <class Index, class Levels>
void expect_lookahead_changes_nothing(std::string_view name) {
  SCOPED_TRACE(name);
  using B = Book<Index, Levels>;
  const std::vector<std::byte> stream = churn_stream();
  for (const unsigned distance : kDistances) {
    SCOPED_TRACE(distance);
    ASSERT_TRUE(itch::LookaheadReader<B>::valid_distance(distance));
    const auto plain_book = std::make_unique<B>(kCapacity);
    const auto ahead_book = std::make_unique<B>(kCapacity);
    itch::FrameReader plain{stream};
    itch::LookaheadReader<B> ahead{stream, *ahead_book, distance};
    std::uint64_t refused = 0;
    std::uint64_t messages = 0;
    for (Frame frame = plain.next(); !itch::is_terminal(frame.status); frame = plain.next()) {
      const Frame other = ahead.next();
      ASSERT_EQ(other.payload.data(), frame.payload.data());
      if (frame.status != FrameStatus::message) {
        continue;
      }
      const ApplyStatus want = plain_book->apply(frame.payload);
      ASSERT_EQ(ahead_book->apply(other.payload), want) << "message " << messages;
      refused += want == ApplyStatus::applied || want == ApplyStatus::no_book_effect ? 0U : 1U;
      if (messages % 512 == 0) {
        ASSERT_TRUE(*ahead_book == *plain_book) << "message " << messages;
      }
      ++messages;
    }
    EXPECT_TRUE(*ahead_book == *plain_book);
    EXPECT_GT(refused, 0U) << "the stream never had the book refuse a message";
    if constexpr (std::is_same_v<Index, itch::PagedIndex<>>) {
      EXPECT_GT(ahead_book->index().fallback_inserts(), 0U) << "the paged index never fell back";
    }
  }
}

TEST(Lookahead, EveryArmEndsEveryMessageAsWithoutIt) {
  expect_lookahead_changes_nothing<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  expect_lookahead_changes_nothing<itch::HashIndex, itch::FlatLevels>("hash, flat");
  expect_lookahead_changes_nothing<itch::PagedIndex<>, itch::LinkedLevels>("paged, linked");
  expect_lookahead_changes_nothing<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

// A stage asked for on any payload, garbage included, reads within the book and changes
// nothing.
TEST(Lookahead, AStageOnAnyPayloadChangesNothing) {
  using B = Book<itch::PagedIndex<>, itch::FlatLevels>;
  const std::vector<std::byte> stream = churn_stream();
  const auto book = std::make_unique<B>(kCapacity);
  itch::FrameReader reader{stream};
  for (Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    static_cast<void>(book->apply(frame.payload));
  }
  const auto before = std::make_unique<B>(*book);
  std::array<std::byte, 40> bytes{};
  for (const char type : {'A', 'F', 'E', 'C', 'X', 'D', 'U', 'S', 'P'}) {
    for (std::uint32_t fill = 0; fill < 256; fill += 51) {
      bytes.fill(static_cast<std::byte>(fill));
      bytes[0] = static_cast<std::byte>(type);
      const std::size_t length = itch::message_length(type);
      for (const std::size_t size : {std::size_t{0}, std::size_t{1}, length, bytes.size()}) {
        for (unsigned stage = 0; stage < B::kPrefetchStages + 2; ++stage) {
          book->prefetch_stage(std::span{bytes}.first(size), stage);
        }
      }
    }
  }
  EXPECT_TRUE(*book == *before);
}

// apply_as<T>() is apply() for a payload of type T, a wrong length included.
TEST(ApplyAs, IsApplyForItsType) {
  using B = Book<itch::PagedIndex<>, itch::FlatLevels>;
  const std::vector<std::byte> stream = churn_stream();
  const auto by_switch = std::make_unique<B>(kCapacity);
  const auto by_type = std::make_unique<B>(kCapacity);
  itch::FrameReader reader{stream};
  std::uint64_t typed = 0;
  for (Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != FrameStatus::message) {
      continue;
    }
    const ApplyStatus want = by_switch->apply(frame.payload);
    ApplyStatus got = ApplyStatus::no_book_effect;
    switch (static_cast<char>(frame.payload[0])) {
      case 'A': got = by_type->apply_as<'A'>(frame.payload); break;
      case 'F': got = by_type->apply_as<'F'>(frame.payload); break;
      case 'E': got = by_type->apply_as<'E'>(frame.payload); break;
      case 'C': got = by_type->apply_as<'C'>(frame.payload); break;
      case 'X': got = by_type->apply_as<'X'>(frame.payload); break;
      case 'D': got = by_type->apply_as<'D'>(frame.payload); break;
      case 'U': got = by_type->apply_as<'U'>(frame.payload); break;
      default: got = by_type->apply_as<'S'>(frame.payload); break;
    }
    ++typed;
    ASSERT_EQ(got, want) << "message " << typed;
    if (static_cast<char>(frame.payload[0]) == 'D') {
      const std::span<const std::byte> cut = frame.payload.first(frame.payload.size() - 1);
      ASSERT_EQ(by_type->apply_as<'D'>(cut), ApplyStatus::no_book_effect);
      ASSERT_EQ(by_switch->apply(cut), ApplyStatus::no_book_effect);
    }
  }
  EXPECT_TRUE(*by_type == *by_switch);
  EXPECT_GT(typed, 20000U);
}

// The same statuses and the same book from every form that has a book; the predicate form
// walks the same messages.
template <class Index, class Levels>
void expect_forms_agree(std::string_view name) {
  SCOPED_TRACE(name);
  using B = Book<Index, Levels>;
  using itch::bench::FormTotals;
  const std::vector<std::byte> stream = churn_stream();
  const auto by_switch = std::make_unique<B>(kCapacity);
  const FormTotals want = itch::bench::switch_pass(stream, *by_switch);
  EXPECT_GT(want.rejected, 0U);
  const auto by_table = std::make_unique<B>(kCapacity);
  const FormTotals table = itch::bench::TableForm<B>::pass(stream, *by_table);
  EXPECT_EQ(table.messages, want.messages);
  EXPECT_EQ(table.rejected, want.rejected);
  EXPECT_TRUE(*by_table == *by_switch) << "table";
#if defined(__has_cpp_attribute)
#if __has_cpp_attribute(clang::musttail)
  const auto by_thread = std::make_unique<B>(kCapacity);
  const FormTotals threaded = itch::bench::ThreadedForm<B>::pass(stream, *by_thread);
  EXPECT_EQ(threaded.messages, want.messages);
  EXPECT_EQ(threaded.rejected, want.rejected);
  EXPECT_TRUE(*by_thread == *by_switch) << "threaded";
#endif
#endif
  EXPECT_EQ(itch::bench::predicate_pass(stream).messages, want.messages);
}

TEST(DispatchForms, EveryFormGivesTheSwitchsBook) {
  expect_forms_agree<itch::HashIndex, itch::LinkedLevels>("hash, linked");
  expect_forms_agree<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

TEST(DispatchForms, TheThreadedFormIsBuiltOnlyWhereATailCallIsGuaranteed) {
#if defined(__has_cpp_attribute)
#if __has_cpp_attribute(clang::musttail)
  EXPECT_TRUE(itch::bench::kThreadedBuilt);
#else
  EXPECT_FALSE(itch::bench::kThreadedBuilt);
#endif
#endif
  EXPECT_EQ(itch::bench::parse_dispatch("threaded"), itch::bench::DispatchForm::threaded);
  EXPECT_FALSE(itch::bench::parse_dispatch("computed-goto").has_value());
}

TEST(DispatchForms, ThePredicateFoldReadsTheHeader) {
  std::vector<std::byte> one;
  itch::fixtures::append_frame(one, wire::add(7, 1, 'B', 100, 1000, 1));
  std::vector<std::byte> other;
  itch::fixtures::append_frame(other, wire::add(8, 1, 'B', 100, 1000, 1));
  EXPECT_NE(itch::bench::predicate_pass(one).fold, itch::bench::predicate_pass(other).fold);
}

// A book on the scripted index ends as the book without it, in every mode, and the script
// holds exactly the day's finds.
template <class Index, class Levels>
void expect_script_changes_nothing(std::string_view name) {
  SCOPED_TRACE(name);
  using itch::bench::ScriptedIndex;
  using itch::bench::ScriptMode;
  const std::vector<std::byte> stream = churn_stream();
  const auto plain = std::make_unique<Book<Index, Levels>>(kCapacity);
  const itch::bench::FormTotals want = itch::bench::switch_pass(stream, *plain);
  std::vector<std::uint32_t> memory(200000);
  itch::bench::LookupScript script{memory};
  itch::bench::current_script() = &script;
  const auto same = [&plain](const auto& book) {
    EXPECT_TRUE(book.index().wrapped() == plain->index());
    EXPECT_TRUE(book.levels() == plain->levels());
    EXPECT_EQ(book.live_orders(), plain->live_orders());
    for (std::uint32_t at = 0; at < plain->order_capacity(); ++at) {
      ASSERT_EQ(book.order_at(at), plain->order_at(at)) << "order slot " << at;
    }
  };
  const auto run = [&]<ScriptMode Mode> {
    const auto book =
        std::make_unique<itch::FastBook<itch::NoWriteObserver, ScriptedIndex<Index, Mode>, Levels>>(
            kCapacity);
    const itch::bench::FormTotals got = itch::bench::switch_pass(stream, *book);
    EXPECT_EQ(got.rejected, want.rejected);
    same(*book);
  };
  run.template operator()<ScriptMode::record>();
  EXPECT_GT(script.recorded(), want.messages / 2);
  EXPECT_FALSE(script.overrun());
  for (int pass = 0; pass < 2; ++pass) {
    script.rewind();
    run.template operator()<ScriptMode::check>();
    EXPECT_TRUE(script.consumed_exactly());
    script.rewind();
    run.template operator()<ScriptMode::answer>();
    EXPECT_TRUE(script.consumed_exactly());
  }
  itch::bench::current_script() = nullptr;
}

TEST(LookupScript, ABookOnTheScriptIsTheBookWithoutIt) {
  expect_script_changes_nothing<itch::HashIndex, itch::FlatLevels>("hash, flat");
  expect_script_changes_nothing<itch::PagedIndex<>, itch::FlatLevels>("paged, flat");
}

TEST(LookupScript, AWrongEntryOrAShortScriptIsCaught) {
  using itch::bench::ScriptedIndex;
  using itch::bench::ScriptMode;
  using Index = itch::PagedIndex<>;
  const std::vector<std::byte> stream = churn_stream();
  std::vector<std::uint32_t> memory(200000);
  itch::bench::LookupScript script{memory};
  itch::bench::current_script() = &script;
  {
    const auto book = std::make_unique<itch::FastBook<
        itch::NoWriteObserver, ScriptedIndex<Index, ScriptMode::record>, itch::FlatLevels>>(
        kCapacity);
    static_cast<void>(itch::bench::switch_pass(stream, *book));
  }
  std::size_t live = 0;
  while (memory[live] == itch::kNoIndex) {
    ++live;
  }
  memory[live] = itch::kNoIndex;  // A find that found an order now says it found none.
  script.rewind();
  {
    const auto book =
        std::make_unique<itch::FastBook<itch::NoWriteObserver,
                                        ScriptedIndex<Index, ScriptMode::check>, itch::FlatLevels>>(
            kCapacity);
    static_cast<void>(itch::bench::switch_pass(stream, *book));
  }
  EXPECT_EQ(script.mismatches(), 1U);
  EXPECT_FALSE(script.consumed_exactly());

  // A pass that stops early leaves entries unread, with no mismatch and no overrun.
  script.rewind();
  {
    const auto book =
        std::make_unique<itch::FastBook<itch::NoWriteObserver,
                                        ScriptedIndex<Index, ScriptMode::check>, itch::FlatLevels>>(
            kCapacity);
    static_cast<void>(itch::bench::switch_pass(std::span<const std::byte>{}, *book));
  }
  EXPECT_EQ(script.mismatches(), 0U);
  EXPECT_FALSE(script.overrun());
  EXPECT_FALSE(script.consumed_exactly());

  std::vector<std::uint32_t> small(16);
  itch::bench::LookupScript short_script{small};
  itch::bench::current_script() = &short_script;
  {
    const auto book = std::make_unique<itch::FastBook<
        itch::NoWriteObserver, ScriptedIndex<Index, ScriptMode::record>, itch::FlatLevels>>(
        kCapacity);
    static_cast<void>(itch::bench::switch_pass(stream, *book));
  }
  EXPECT_TRUE(short_script.overrun());
  EXPECT_FALSE(short_script.consumed_exactly());
  itch::bench::current_script() = nullptr;
}

}  // namespace
