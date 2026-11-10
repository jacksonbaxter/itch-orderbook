// The allocator arms of the node book (node_book.hpp), each a policy that decides where a
// node's storage comes from and nothing else. mimalloc's arm is in mimalloc_arm.hpp, since
// only a build with mimalloc fetched can include it.
//
//   MallocArm        the system allocator, through the global operator new, which libc++ and
//                    libstdc++ both pass to malloc. One call per node made and one per node
//                    given back. Its aged run is the same arm on a heap HeapAger has aged.
//   PmrPoolArm       std::pmr::unsynchronized_pool_resource over new_delete_resource(), called
//                    through a memory_resource pointer as a polymorphic_allocator would, so
//                    each call is a virtual one (LC-39, LC-54).
//   PmrMonotonicArm  std::pmr::monotonic_buffer_resource over new_delete_resource(): a node
//                    given back is never reused, so the resource only grows (LC-55).
//   PoolArm          node_pool.hpp's pool, one per node type, sized from the book's capacity
//                    and pre-faulted on the pages NodeCapacity::pages asks for: by index
//                    with a LIFO free list, by pointer with a LIFO free list, and by index
//                    with a FIFO free list.
//
// Every arm but the pools gives nodes by pointer, so every one of them holds the same node
// layout as the pool by pointer.
#pragma once

#include <cstddef>
#include <memory_resource>
#include <new>
#include <string_view>
#include <type_traits>

#include "itch/node_book.hpp"
#include "itch/node_pool.hpp"
#include "itch/page_map.hpp"

namespace itch {

// What every arm that hands out pointers shares: a handle is the node's address.
struct PointerHandles {
  template <class T>
  [[nodiscard]] static T& at(T* node) noexcept {
    return *node;
  }
  template <class T>
  [[nodiscard]] static constexpr T* null() noexcept {
    return nullptr;
  }
};

struct MallocArm {
  static constexpr std::string_view kName = "malloc";
  template <class T>
  using Handle = T*;

  template <class Order, class Level>
  class Heap : public PointerHandles {
   public:
    explicit Heap(const NodeCapacity& /*capacity*/) {}

    template <class T>
    [[nodiscard]] T* make() {
      // The book owns the node until it hands it to drop().
      return ::new (::operator new(sizeof(T))) T;  // NOLINT(cppcoreguidelines-owning-memory)
    }
    template <class T>
    void drop(T* node) noexcept {
      ::operator delete(node, sizeof(T));
    }
    [[nodiscard]] static const PageReport* pages() noexcept { return nullptr; }
  };
};

// One resource of the given kind, upstream new_delete_resource(), and every call through a
// memory_resource pointer to it.
template <class Resource>
class PmrHeap : public PointerHandles {
 public:
  explicit PmrHeap(const NodeCapacity& /*capacity*/) : resource_{&own_} {}
  PmrHeap(const PmrHeap&) = delete;
  PmrHeap& operator=(const PmrHeap&) = delete;
  PmrHeap(PmrHeap&&) = delete;
  PmrHeap& operator=(PmrHeap&&) = delete;
  ~PmrHeap() = default;

  template <class T>
  [[nodiscard]] T* make() {
    // The book owns the node until it hands it to drop().
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
    return ::new (resource_->allocate(sizeof(T), alignof(T))) T;
  }
  template <class T>
  void drop(T* node) noexcept {
    resource_->deallocate(node, sizeof(T), alignof(T));
  }
  [[nodiscard]] static const PageReport* pages() noexcept { return nullptr; }

 private:
  Resource own_{std::pmr::new_delete_resource()};
  std::pmr::memory_resource* resource_;
};

struct PmrPoolArm {
  static constexpr std::string_view kName = "pmr-pool";
  template <class T>
  using Handle = T*;
  template <class Order, class Level>
  using Heap = PmrHeap<std::pmr::unsynchronized_pool_resource>;
};

struct PmrMonotonicArm {
  static constexpr std::string_view kName = "pmr-monotonic";
  template <class T>
  using Handle = T*;
  template <class Order, class Level>
  using Heap = PmrHeap<std::pmr::monotonic_buffer_resource>;
};

template <HandleKind Kind, FreeOrder Free>
[[nodiscard]] consteval std::string_view pool_arm_name() noexcept {
  if (Kind == HandleKind::pointer) {
    return "pool-pointer";
  }
  return Free == FreeOrder::fifo ? "pool-fifo" : "pool-index";
}

template <HandleKind Kind, FreeOrder Free>
struct PoolArm {
  static constexpr std::string_view kName = pool_arm_name<Kind, Free>();
  template <class T>
  using Handle = PoolHandle<T, Kind>;

  template <class Order, class Level>
  class Heap {
   public:
    explicit Heap(const NodeCapacity& capacity)
        : orders_{capacity.orders, capacity.pages}, levels_{capacity.levels, capacity.pages} {}

    // The book checks its own capacity first, which is the pools' own, so there is room.
    template <class T>
    [[nodiscard]] Handle<T> make() noexcept {
      return pool<T>().take();
    }
    template <class T>
    void drop(Handle<T> node) noexcept {
      pool<T>().give_back(node);
    }
    template <class T>
    [[nodiscard]] T& at(Handle<T> node) noexcept {
      return pool<T>().at(node);
    }
    template <class T>
    [[nodiscard]] const T& at(Handle<T> node) const noexcept {
      return pool<T>().at(node);
    }
    template <class T>
    [[nodiscard]] static constexpr Handle<T> null() noexcept {
      return NullHandle<T, Kind>::kValue;
    }

    // Both pools ask for the same pages. The orders' pool is the larger, so its record
    // stands for both; levels() has the other's.
    [[nodiscard]] const PageReport* pages() const noexcept { return &orders_.pages(); }
    [[nodiscard]] const PageReport& level_pages() const noexcept { return levels_.pages(); }
    [[nodiscard]] bool ready() const noexcept {
      return orders_.pages().mapped && levels_.pages().mapped;
    }

   private:
    template <class T>
    [[nodiscard]] auto& pool() noexcept {
      if constexpr (std::is_same_v<T, Order>) {
        return orders_;
      } else {
        return levels_;
      }
    }
    template <class T>
    [[nodiscard]] const auto& pool() const noexcept {
      if constexpr (std::is_same_v<T, Order>) {
        return orders_;
      } else {
        return levels_;
      }
    }

    NodePool<Order, Kind, Free> orders_;
    NodePool<Level, Kind, Free> levels_;
  };
};

using PoolIndexArm = PoolArm<HandleKind::index, FreeOrder::lifo>;
using PoolPointerArm = PoolArm<HandleKind::pointer, FreeOrder::lifo>;
using PoolFifoArm = PoolArm<HandleKind::index, FreeOrder::fifo>;

}  // namespace itch
