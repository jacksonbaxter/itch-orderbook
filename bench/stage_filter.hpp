// A look-ahead that starts only some of the book's loads, to tell which structure a DRAM fill
// comes from (NOTES.md, "Attributing the misses").
//
//     StageFilter<Book> filter{book, stages, kinds};
//     itch::LookaheadReader<StageFilter<Book>> reader{bytes, filter, distance};
//
// It stands in for the book in the reader: it has the book's stages, and passes a stage on to
// FastBook::prefetch_stage() only if the stage is in `stages` and the message is of `kinds`.
// Each stage of the book reads one structure: on the paged index, stage 0 the directory and
// stage 1 the page slot; then, for a message that names a live order, stage 2 its record and
// stage 3 its level's record, and for one that adds an order, stage 2 its side's record and
// stage 3 the best end of the side's key array. So a pass that starts one stage for one kind
// of message, against a pass that starts none, takes off the demand path the fills that
// stage's structure makes, and only those. The book is only read, as with every stage.
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>

#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch::bench {

// named: E, C, X, D and U, which name a live order. added: A and F, which add one.
enum class StageKinds : std::uint8_t { all, named, added };

[[nodiscard]] constexpr std::string_view to_string(StageKinds kinds) noexcept {
  switch (kinds) {
    case StageKinds::named: return "named";
    case StageKinds::added: return "added";
    case StageKinds::all: break;
  }
  return "all";
}

[[nodiscard]] constexpr std::optional<StageKinds> parse_stage_kinds(
    std::string_view name) noexcept {
  for (const StageKinds kinds : {StageKinds::all, StageKinds::named, StageKinds::added}) {
    if (name == to_string(kinds)) {
      return kinds;
    }
  }
  return std::nullopt;
}

// Digits, each a stage, at most once each: "2" or "0123". The bits of the stages named.
[[nodiscard]] constexpr std::optional<unsigned> parse_stages(std::string_view digits) noexcept {
  unsigned mask = 0;
  for (const char digit : digits) {
    if (digit < '0' || digit > '7') {
      return std::nullopt;
    }
    const auto bit = static_cast<unsigned>(digit - '0');
    if (((mask >> bit) & 1U) != 0) {
      return std::nullopt;
    }
    mask |= 1U << bit;
  }
  return mask == 0 ? std::nullopt : std::optional<unsigned>{mask};
}

[[nodiscard]] constexpr bool of_kinds(StageKinds kinds, char type) noexcept {
  const bool named = type == OrderExecuted::kType || type == OrderExecutedWithPrice::kType ||
                     type == OrderCancel::kType || type == OrderDelete::kType ||
                     type == OrderReplace::kType;
  const bool added = type == AddOrder::kType || type == AddOrderAttributed::kType;
  switch (kinds) {
    case StageKinds::named: return named;
    case StageKinds::added: return added;
    case StageKinds::all: break;
  }
  return named || added;
}

template <class Book>
class StageFilter {
 public:
  static constexpr unsigned kPrefetchStages = Book::kPrefetchStages;

  StageFilter(const Book& book, unsigned stages, StageKinds kinds) noexcept
      : book_{&book}, stages_{stages}, kinds_{kinds} {}

  void prefetch_stage(std::span<const std::byte> payload, unsigned stage) const noexcept {
    if (((stages_ >> stage) & 1U) == 0 || payload.empty() ||
        !of_kinds(kinds_, read_char(payload.data()))) {
      return;
    }
    book_->prefetch_stage(payload, stage);
  }

 private:
  const Book* book_;
  unsigned stages_;
  StageKinds kinds_;
};

}  // namespace itch::bench
