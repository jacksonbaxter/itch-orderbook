// One message the book acts on, decoded from the wire, so that a parser on one thread can
// hand it to a book on another (bench/pipeline.hpp, T-SPSC-3). FastBook::apply(BookOp)
// takes it through the same paths as apply() on the payload it came from, so the two
// leave the same book.
//
// 32 bytes, so two to a 64-byte line and four to Apple's 128-byte one.
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

#include "itch/messages.hpp"
#include "itch/wire.hpp"

namespace itch {

enum class BookOpKind : std::uint8_t {
  add,      // 'A' and 'F'.
  deduct,   // 'E', 'C' and 'X': shares off a live order.
  erase,    // 'D'.
  replace,  // 'U'.
};

struct BookOp {
  std::uint64_t reference = 0;      // The order's, or the original of a replace.
  std::uint64_t new_reference = 0;  // A replace's new order, else 0.
  std::uint32_t shares = 0;         // Added or taken off, or a replace's new total.
  Price4 price = 0;                 // An add's or a replace's, else 0.
  std::uint16_t locate = 0;
  char side = 0;  // An add's, as on the wire: 'B', 'S', or what the wire held.
  BookOpKind kind = BookOpKind::add;

  friend bool operator==(const BookOp&, const BookOp&) = default;
};
static_assert(sizeof(BookOp) == 32);

// The op for a whole payload of a type the book acts on. Nothing for any other type, or
// for a payload whose length is wrong for its type, which apply() also leaves alone.
[[nodiscard]] inline std::optional<BookOp> decode_book_op(
    std::span<const std::byte> payload) noexcept {
  if (payload.empty() || payload.size() != message_length(read_char(payload.data()))) {
    return std::nullopt;
  }
  const std::byte* m = payload.data();
  switch (read_char(m)) {
    case AddOrder::kType: {
      const AddOrder add = AddOrder::from_wire(m);
      return BookOp{.reference = add.order_reference,
                    .shares = add.shares,
                    .price = add.price,
                    .locate = add.header.stock_locate,
                    .side = add.side,
                    .kind = BookOpKind::add};
    }
    case AddOrderAttributed::kType: {
      const AddOrderAttributed add = AddOrderAttributed::from_wire(m);
      return BookOp{.reference = add.order_reference,
                    .shares = add.shares,
                    .price = add.price,
                    .locate = add.header.stock_locate,
                    .side = add.side,
                    .kind = BookOpKind::add};
    }
    case OrderExecuted::kType: {
      const OrderExecuted e = OrderExecuted::from_wire(m);
      return BookOp{.reference = e.order_reference,
                    .shares = e.executed_shares,
                    .locate = e.header.stock_locate,
                    .kind = BookOpKind::deduct};
    }
    case OrderExecutedWithPrice::kType: {
      const OrderExecutedWithPrice c = OrderExecutedWithPrice::from_wire(m);
      return BookOp{.reference = c.order_reference,
                    .shares = c.executed_shares,
                    .locate = c.header.stock_locate,
                    .kind = BookOpKind::deduct};
    }
    case OrderCancel::kType: {
      const OrderCancel x = OrderCancel::from_wire(m);
      return BookOp{.reference = x.order_reference,
                    .shares = x.cancelled_shares,
                    .locate = x.header.stock_locate,
                    .kind = BookOpKind::deduct};
    }
    case OrderDelete::kType: {
      const OrderDelete d = OrderDelete::from_wire(m);
      return BookOp{.reference = d.order_reference,
                    .locate = d.header.stock_locate,
                    .kind = BookOpKind::erase};
    }
    case OrderReplace::kType: {
      const OrderReplace u = OrderReplace::from_wire(m);
      return BookOp{.reference = u.original_order_reference,
                    .new_reference = u.new_order_reference,
                    .shares = u.shares,
                    .price = u.price,
                    .locate = u.header.stock_locate,
                    .kind = BookOpKind::replace};
    }
    default: return std::nullopt;
  }
}

}  // namespace itch
