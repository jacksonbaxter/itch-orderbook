// carteret's window-width experiment, as counts over a day of this book (NOTES.md, "carteret's
// layout experiments on our book"). Nothing here is timed, and every count is the same on any
// host.
//
//     itch_window FILE
//
// carteret keeps each side's levels in a flat array of W one-cent ticks that slides with the
// side's best price, and a level outside it in a std::map, the overflow. This book keeps no
// window: a side is one sorted array of its live levels, best at the end, searched from there.
// So two things are counted, at every message that adds an order (A, F, the new order of a U)
// or names a live one (E, C, X, D, the old order of a U), before the book applies it:
//
//   - the window: how far, in whole cents, the price sits behind its side's best, and so
//     whether a window of W ticks anchored on the best would hold it, for W from 64 to 4,096.
//     A price at or better than the best, or on an empty side, is inside: the window slides
//     to it. A price with a fraction of a cent is outside at every width, since no window of
//     cents can index it. carteret places the best a margin inside its window's edge and moves
//     the window with hysteresis; neither is modelled, so this is the overflow of a window
//     that follows the best exactly, which neither bounds carteret's at the same width nor
//     is bounded by it;
//   - this book's search: how many live levels on the side are better than the price, for
//     each search FlatLevels makes, which is at every add and at every message that removes a
//     level's last order. A search reads one key for each such level and one more, so this is
//     what a window of W entries, a search that walks W entries and no further, would leave
//     to the rest of the side.
//
// Exit codes: 0 the day walked and applied cleanly, 1 the book refused a message, 2 the file
// could not be read or the arguments are wrong.
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string_view>

#include "itch/book.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "mapped_file.hpp"

namespace {

constexpr std::array<std::uint32_t, 7> kWidths{64, 128, 256, 512, 1024, 2048, 4096};
constexpr std::uint32_t kCent = 100;  // Price4 is in ten-thousandths of a dollar.
// Levels better than a price: 0, 1, 2-3, 4-7, 8-15, 16-63, 64-255, 256 and more.
constexpr std::array<std::uint32_t, 8> kDepthFloors{0, 1, 2, 4, 8, 16, 64, 256};
constexpr std::array<std::string_view, 8> kDepthNames{"0",    "1",     "2-3",    "4-7",
                                                      "8-15", "16-63", "64-255", "256+"};

struct PriceCounts {
  std::uint64_t events = 0;
  std::uint64_t opens_side = 0;  // The side was empty.
  std::uint64_t improves = 0;    // Better than the side's best.
  std::uint64_t subcent = 0;     // A fraction of a cent: outside every window.
  std::array<std::uint64_t, kWidths.size()> outside{};
};

struct DepthCounts {
  std::uint64_t searches = 0;
  std::array<std::uint64_t, kDepthFloors.size()> buckets{};
};

using Book = itch::FastBook<>;

void count_price(const Book& book, std::uint16_t locate, itch::Side side, itch::Price4 price,
                 PriceCounts& counts) {
  ++counts.events;
  const itch::TopOfBook best = book.best(locate, side);
  const bool behind =
      best.present && (side == itch::Side::bid ? price < best.price : price > best.price);
  if (!best.present) {
    ++counts.opens_side;
  } else if (!behind && price != best.price) {
    ++counts.improves;
  }
  if (price % kCent != 0) {
    ++counts.subcent;
    for (std::uint64_t& outside : counts.outside) {
      ++outside;
    }
    return;
  }
  if (!behind) {
    return;
  }
  const std::uint32_t gap = side == itch::Side::bid ? best.price - price : price - best.price;
  const std::uint32_t ticks = (gap + kCent - 1) / kCent;
  for (std::size_t at = 0; at < kWidths.size(); ++at) {
    counts.outside.at(at) += ticks >= kWidths.at(at) ? 1U : 0U;
  }
}

void count_search(const Book& book, std::uint16_t locate, itch::Side side, itch::Price4 price,
                  DepthCounts& counts) {
  const itch::LevelId id{.locate = locate, .side = side, .price = price};
  const auto place = book.levels().search(id);
  const std::uint32_t count = book.sides()[itch::side_key(locate, side)].count;
  const std::uint32_t better = count - place.position - (place.level == itch::kNoIndex ? 0U : 1U);
  ++counts.searches;
  std::size_t bucket = 0;
  while (bucket + 1 < kDepthFloors.size() && better >= kDepthFloors.at(bucket + 1)) {
    ++bucket;
  }
  ++counts.buckets.at(bucket);
}

struct Counts {
  std::uint64_t messages = 0;
  std::uint64_t refused = 0;
  PriceCounts adds;
  PriceCounts named;
  DepthCounts add_searches;
  DepthCounts close_searches;
};

// A live order the message names, before the book applies it: its price counted, and a
// search counted if `removes` would take its level's last order.
void count_named(const Book& book, std::uint64_t reference, bool removes, Counts& counts) {
  const std::optional<itch::RestingOrder> order = book.resting_order(reference);
  if (!order) {
    return;
  }
  count_price(book, order->locate, order->side, order->price, counts.named);
  const auto* level = book.find_level(order->locate, order->side, order->price);
  if (removes && level != nullptr && level->order_count == 1) {
    count_search(book, order->locate, order->side, order->price, counts.close_searches);
  }
}

void count_add(const Book& book, std::uint16_t locate, itch::Side side, itch::Price4 price,
               Counts& counts) {
  count_price(book, locate, side, price, counts.adds);
  count_search(book, locate, side, price, counts.add_searches);
}

void observe(const Book& book, std::span<const std::byte> payload, Counts& counts) {
  const std::byte* const m = payload.data();
  const char type = itch::read_char(m);
  if (payload.size() != itch::message_length(type)) {
    return;
  }
  const auto side_of = [](char side) { return side == 'B' ? itch::Side::bid : itch::Side::ask; };
  switch (type) {
    case itch::AddOrder::kType: {
      const itch::AddOrder add = itch::AddOrder::from_wire(m);
      count_add(book, add.header.stock_locate, side_of(add.side), add.price, counts);
      return;
    }
    case itch::AddOrderAttributed::kType: {
      const itch::AddOrderAttributed add = itch::AddOrderAttributed::from_wire(m);
      count_add(book, add.header.stock_locate, side_of(add.side), add.price, counts);
      return;
    }
    case itch::OrderExecuted::kType: {
      const itch::OrderExecuted e = itch::OrderExecuted::from_wire(m);
      const auto order = book.resting_order(e.order_reference);
      count_named(book, e.order_reference, order && order->shares == e.executed_shares, counts);
      return;
    }
    case itch::OrderExecutedWithPrice::kType: {
      const itch::OrderExecutedWithPrice c = itch::OrderExecutedWithPrice::from_wire(m);
      const auto order = book.resting_order(c.order_reference);
      count_named(book, c.order_reference, order && order->shares == c.executed_shares, counts);
      return;
    }
    case itch::OrderCancel::kType: {
      const itch::OrderCancel x = itch::OrderCancel::from_wire(m);
      const auto order = book.resting_order(x.order_reference);
      count_named(book, x.order_reference, order && order->shares == x.cancelled_shares, counts);
      return;
    }
    case itch::OrderDelete::kType:
      count_named(book, itch::OrderDelete::from_wire(m).order_reference, true, counts);
      return;
    case itch::OrderReplace::kType: {
      const itch::OrderReplace u = itch::OrderReplace::from_wire(m);
      const auto order = book.resting_order(u.original_order_reference);
      count_named(book, u.original_order_reference, true, counts);
      if (order) {
        count_add(book, order->locate, order->side, u.price, counts);
      }
      return;
    }
    default: return;
  }
}

void print_prices(std::string_view kind, const PriceCounts& counts) {
  std::println("{} {}", kind, counts.events);
  std::println("{}_opens_side {}", kind, counts.opens_side);
  std::println("{}_improves {}", kind, counts.improves);
  std::println("{}_subcent {}", kind, counts.subcent);
  for (std::size_t at = 0; at < kWidths.size(); ++at) {
    std::println("outside {} {} {}", kind, kWidths.at(at), counts.outside.at(at));
  }
}

void print_depths(std::string_view kind, const DepthCounts& counts) {
  std::println("{}_searches {}", kind, counts.searches);
  for (std::size_t at = 0; at < kDepthFloors.size(); ++at) {
    std::println("better {} {} {}", kind, kDepthNames.at(at), counts.buckets.at(at));
  }
}

int run(int argc, char** argv) {
  const std::span args{argv, static_cast<std::size_t>(argc)};
  if (args.size() != 2) {
    std::println(stderr, "usage: itch_window FILE");
    return 2;
  }
  const std::optional<itch::tools::MappedFile> file = itch::tools::MappedFile::open(args[1]);
  if (!file) {
    std::println(stderr, "itch_window: cannot read {}", args[1]);
    return 2;
  }
  const auto book = std::make_unique<Book>();
  Counts counts;
  itch::FrameReader reader{file->bytes()};
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    ++counts.messages;
    observe(*book, frame.payload, counts);
    const itch::ApplyStatus status = book->apply(frame.payload);
    counts.refused +=
        status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect ? 0U
                                                                                            : 1U;
  }
  std::println("messages {}", counts.messages);
  std::println("refused {}", counts.refused);
  std::println("walk {}", frame.status == itch::FrameStatus::end_of_input ? "clean" : "not clean");
  print_prices("adds", counts.adds);
  print_prices("named", counts.named);
  print_depths("add", counts.add_searches);
  print_depths("close", counts.close_searches);
  return counts.refused == 0 && frame.status == itch::FrameStatus::end_of_input ? 0 : 1;
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
