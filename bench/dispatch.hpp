// The four forms of message dispatch E7 compares (simd.md's experiments), each a whole pass
// over the input that walks the frames and hands every message to the book its own way:
//
//   switch     FastBook::apply(), one switch on the type byte. What the book ships.
//   table      an array of 256 handlers indexed by the type byte, one indirect call each.
//   threaded   each handler ends by walking to the next frame and jumping to that frame's
//              handler, a tail call through the same table, so that every handler has its
//              own indirect branch. Needs [[clang::musttail]] to be a jump and not a call,
//              since a day of nested calls would overflow any stack.
//   predicate  no dispatch and no book: the frame walk and the header fields every message
//              has, folded into a sum. The floor the other three stand on.
//
// The first three hand the same messages to the same book and leave it in the same state,
// and the tests hold them to that. Nothing here is production code.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string_view>
#include <utility>

#include "itch/book_types.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"

namespace itch::bench {

enum class DispatchForm : std::uint8_t { switch_case, table, threaded, predicate };

// Whether this compiler can build the threaded form. Without a guaranteed tail call it is
// not built at all, and asking for it is an error, never a quiet fallback to another form.
#if defined(__has_cpp_attribute)
#if __has_cpp_attribute(clang::musttail)
inline constexpr bool kThreadedBuilt = true;
#else
inline constexpr bool kThreadedBuilt = false;
#endif
#else
inline constexpr bool kThreadedBuilt = false;
#endif

[[nodiscard]] constexpr std::string_view to_string(DispatchForm form) noexcept {
  switch (form) {
    case DispatchForm::switch_case: return "switch";
    case DispatchForm::table: return "table";
    case DispatchForm::threaded: return "threaded";
    case DispatchForm::predicate: return "predicate";
  }
  return "unnamed";
}

[[nodiscard]] constexpr std::optional<DispatchForm> parse_dispatch(std::string_view name) noexcept {
  for (const DispatchForm form : {DispatchForm::switch_case, DispatchForm::table,
                                  DispatchForm::threaded, DispatchForm::predicate}) {
    if (name == to_string(form)) {
      return form;
    }
  }
  return std::nullopt;
}

struct FormTotals {
  std::uint64_t messages = 0;
  std::uint64_t rejected = 0;  // The book refused a message. A clean day has none.
  std::uint64_t fold = 0;      // The predicate form's sum of header fields, so it is kept.
};

[[nodiscard]] constexpr bool refused(ApplyStatus status) noexcept {
  return status != ApplyStatus::applied && status != ApplyStatus::no_book_effect;
}

// The next frame that is a message, or nothing once the input ends.
[[nodiscard]] inline std::span<const std::byte> next_message(FrameReader& reader) noexcept {
  for (Frame frame = reader.next(); !is_terminal(frame.status); frame = reader.next()) {
    if (frame.status == FrameStatus::message) {
      return frame.payload;
    }
  }
  return {};
}

template <class Book>
[[nodiscard]] FormTotals switch_pass(std::span<const std::byte> input, Book& book) noexcept {
  FrameReader reader{input};
  FormTotals totals;
  for (std::span<const std::byte> m = next_message(reader); !m.empty(); m = next_message(reader)) {
    ++totals.messages;
    totals.rejected += refused(book.apply(m)) ? 1U : 0U;
  }
  return totals;
}

// The types FastBook::apply_as() takes. Every other type is a message with no book effect.
inline constexpr std::array kBookTypes{AddOrder::kType,      AddOrderAttributed::kType,
                                       OrderExecuted::kType, OrderExecutedWithPrice::kType,
                                       OrderCancel::kType,   OrderDelete::kType,
                                       OrderReplace::kType};

template <class Book>
class TableForm {
 public:
  using Handler = ApplyStatus (*)(Book&, std::span<const std::byte>) noexcept;

  [[nodiscard]] static FormTotals pass(std::span<const std::byte> input, Book& book) noexcept {
    FrameReader reader{input};
    FormTotals totals;
    for (std::span<const std::byte> m = next_message(reader); !m.empty();
         m = next_message(reader)) {
      ++totals.messages;
      totals.rejected += refused(kTable[static_cast<unsigned char>(m[0])](book, m)) ? 1U : 0U;
    }
    return totals;
  }

 private:
  template <char Type>
  static ApplyStatus apply(Book& book, std::span<const std::byte> payload) noexcept {
    return book.template apply_as<Type>(payload);
  }
  static ApplyStatus no_effect(Book& /*book*/, std::span<const std::byte> /*payload*/) noexcept {
    return ApplyStatus::no_book_effect;
  }

  static constexpr std::array<Handler, 256> make() noexcept {
    std::array<Handler, 256> table{};
    table.fill(&no_effect);
    [&table]<std::size_t... I>(std::index_sequence<I...>) {
      ((table[static_cast<unsigned char>(kBookTypes[I])] = &apply<kBookTypes[I]>), ...);
    }(std::make_index_sequence<kBookTypes.size()>{});
    return table;
  }

  static constexpr std::array<Handler, 256> kTable = make();
};

#if defined(__has_cpp_attribute)
#if __has_cpp_attribute(clang::musttail)
template <class Book>
class ThreadedForm {
 public:
  [[nodiscard]] static FormTotals pass(std::span<const std::byte> input, Book& book) noexcept {
    State state{.reader = FrameReader{input}, .book = &book, .totals = {}};
    const std::span<const std::byte> first = next_message(state.reader);
    if (!first.empty()) {
      kTable[static_cast<unsigned char>(first[0])](state, first);
    }
    return state.totals;
  }

 private:
  struct State {
    FrameReader reader;
    Book* book;
    FormTotals totals;
  };
  using Handler = void (*)(State&, std::span<const std::byte>) noexcept;

  template <char Type>
  static void apply(State& state, std::span<const std::byte> payload) noexcept {
    ++state.totals.messages;
    state.totals.rejected += refused(state.book->template apply_as<Type>(payload)) ? 1U : 0U;
    const std::span<const std::byte> m = next_message(state.reader);
    if (m.empty()) {
      return;
    }
    [[clang::musttail]] return kTable[static_cast<unsigned char>(m[0])](state, m);
  }

  static void no_effect(State& state, std::span<const std::byte> /*payload*/) noexcept {
    ++state.totals.messages;
    const std::span<const std::byte> m = next_message(state.reader);
    if (m.empty()) {
      return;
    }
    [[clang::musttail]] return kTable[static_cast<unsigned char>(m[0])](state, m);
  }

  static constexpr std::array<Handler, 256> make() noexcept {
    std::array<Handler, 256> table{};
    table.fill(&no_effect);
    [&table]<std::size_t... I>(std::index_sequence<I...>) {
      ((table[static_cast<unsigned char>(kBookTypes[I])] = &apply<kBookTypes[I]>), ...);
    }(std::make_index_sequence<kBookTypes.size()>{});
    return table;
  }

  static constexpr std::array<Handler, 256> kTable = make();
};
#endif
#endif

// No book: the type, locate, tracking number and timestamp of every message, the fields
// every type has at the same offsets, folded so that none of the reads can be dropped.
[[nodiscard]] inline FormTotals predicate_pass(std::span<const std::byte> input) noexcept {
  FrameReader reader{input};
  FormTotals totals;
  for (std::span<const std::byte> m = next_message(reader); !m.empty(); m = next_message(reader)) {
    ++totals.messages;
    const Header header = read_header(m.data());
    totals.fold += static_cast<std::uint64_t>(static_cast<unsigned char>(header.message_type)) ^
                   header.stock_locate ^ header.tracking_number ^ header.timestamp_ns;
  }
  return totals;
}

}  // namespace itch::bench
