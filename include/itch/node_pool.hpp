// A fixed pool of nodes of one type, the allocator under the pool arms of T-ARENA-1.
//
//     NodePool<T, HandleKind::index, FreeOrder::lifo> pool{capacity, PageRequest::...};
//
// Every node it will ever hand out is mapped and touched in the constructor
// (itch/page_map.hpp), so that taking one faults nothing in, and nothing after
// construction allocates. A node comes from the free list first, and from the untouched
// end of the pool once the list is empty.
//
// A handle is what a node's owner keeps to reach it:
//   index    a 32-bit slot number, so a link costs 4 bytes and a read adds it to the
//            pool's base. The pool's capacity must stay under kNoIndex.
//   pointer  the node's address, so a link costs 8 bytes and a read adds nothing.
//
// The free list is threaded through the free nodes themselves, the next handle written
// over the first bytes of each, so it costs no memory of its own:
//   lifo     a node given back is the next taken: the slot freed most recently, which is
//            the most likely still to be in cache. The shared practice (LC-56).
//   fifo     a node given back is taken after every node freed before it: the slot freed
//            longest ago. Only the order of reuse differs from lifo (LC-56, the
//            comparison plan's reuse-recency arm).
//
// A pool whose mapping failed has no room, and its report says why.
#pragma once

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <new>
#include <type_traits>

#include "itch/book_types.hpp"
#include "itch/page_map.hpp"

namespace itch {

enum class HandleKind : std::uint8_t { index, pointer };
enum class FreeOrder : std::uint8_t { lifo, fifo };

template <class T, HandleKind Kind>
using PoolHandle = std::conditional_t<Kind == HandleKind::index, std::uint32_t, T*>;

// The handle of no node: kNoIndex, or the null pointer.
template <class T, HandleKind Kind>
struct NullHandle;
template <class T>
struct NullHandle<T, HandleKind::index> {
  static constexpr std::uint32_t kValue = kNoIndex;
};
template <class T>
struct NullHandle<T, HandleKind::pointer> {
  static constexpr T* kValue = nullptr;
};

template <class T, HandleKind Kind, FreeOrder Order>
class NodePool {
 public:
  using Handle = PoolHandle<T, Kind>;
  static constexpr Handle kNull = NullHandle<T, Kind>::kValue;

  static_assert(std::is_trivially_copyable_v<T> && std::is_trivially_destructible_v<T>,
                "a free node's bytes are overwritten with a link");
  static_assert(sizeof(T) >= sizeof(Handle), "a free node holds the next free handle");

  NodePool(std::uint32_t capacity, PageRequest pages)
      : mapping_{std::size_t{capacity} * sizeof(T), pages},
        // Default-initialised: nothing is written, the mapping is already zero and touched.
        nodes_{mapping_.data() == nullptr ? nullptr : ::new (mapping_.data()) T[capacity]},
        capacity_{nodes_ == nullptr ? 0U : capacity} {}

  NodePool(const NodePool&) = delete;
  NodePool& operator=(const NodePool&) = delete;
  NodePool(NodePool&&) = delete;
  NodePool& operator=(NodePool&&) = delete;
  ~NodePool() = default;

  [[nodiscard]] bool has_room() const noexcept { return head_ != kNull || fresh_ < capacity_; }

  // A node's storage, holding whatever it last held. Only when has_room().
  [[nodiscard]] Handle take() noexcept {
    ++live_;
    peak_ = peak_ < live_ ? live_ : peak_;
    if (head_ == kNull) {
      return handle_of(fresh_++);
    }
    const Handle node = head_;
    head_ = link_of(node);
    if constexpr (Order == FreeOrder::fifo) {
      tail_ = head_ == kNull ? kNull : tail_;
    }
    return node;
  }

  void give_back(Handle node) noexcept {
    --live_;
    if constexpr (Order == FreeOrder::lifo) {
      set_link(node, head_);
      head_ = node;
    } else {
      set_link(node, kNull);
      if (tail_ == kNull) {
        head_ = node;
      } else {
        set_link(tail_, node);
      }
      tail_ = node;
    }
  }

  [[nodiscard]] T& at(Handle node) noexcept {
    if constexpr (Kind == HandleKind::index) {
      return nodes_[node];
    } else {
      return *node;
    }
  }
  [[nodiscard]] const T& at(Handle node) const noexcept {
    if constexpr (Kind == HandleKind::index) {
      return nodes_[node];
    } else {
      return *node;
    }
  }

  [[nodiscard]] std::uint32_t capacity() const noexcept { return capacity_; }
  [[nodiscard]] std::uint64_t live() const noexcept { return live_; }
  [[nodiscard]] std::uint64_t peak() const noexcept { return peak_; }
  [[nodiscard]] const PageReport& pages() const noexcept { return mapping_.report(); }

 private:
  [[nodiscard]] Handle handle_of(std::uint32_t slot) noexcept {
    if constexpr (Kind == HandleKind::index) {
      return slot;
    } else {
      return &nodes_[slot];
    }
  }

  static constexpr std::size_t kLinkBytes = sizeof(Handle);

  using LinkBytes = std::array<std::byte, kLinkBytes>;

  [[nodiscard]] Handle link_of(Handle node) const noexcept {
    LinkBytes next{};
    std::memcpy(next.data(), &at(node), kLinkBytes);
    return std::bit_cast<Handle>(next);
  }

  void set_link(Handle node, Handle next) noexcept {
    const auto bytes = std::bit_cast<LinkBytes>(next);
    std::memcpy(&at(node), bytes.data(), kLinkBytes);
  }

  PageMapping mapping_;
  T* nodes_;
  std::uint32_t capacity_;
  std::uint32_t fresh_ = 0;
  Handle head_ = kNull;  // The next node taken once the list has any.
  Handle tail_ = kNull;  // fifo only: the node given back last.
  std::uint64_t live_ = 0;
  std::uint64_t peak_ = 0;
};

}  // namespace itch
