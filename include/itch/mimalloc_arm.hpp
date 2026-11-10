// mimalloc as an arm of the node book (node_book.hpp), from the pinned release that
// cmake/ItchDependencies.cmake fetches when ITCH_MIMALLOC is on, built with its override
// of malloc off, so that only this arm's nodes come from it. One mi_new per node made, one
// mi_free per node given back, on the default heap of the one thread that owns the book.
// mi_new throws std::bad_alloc on failure, as operator new does.
#pragma once

#include <mimalloc.h>

#include <new>
#include <string_view>

#include "itch/node_arms.hpp"
#include "itch/node_book.hpp"

namespace itch {

struct MimallocArm {
  static constexpr std::string_view kName = "mimalloc";
  template <class T>
  using Handle = T*;

  template <class Order, class Level>
  class Heap : public PointerHandles {
   public:
    explicit Heap(const NodeCapacity& /*capacity*/) {}

    template <class T>
    [[nodiscard]] T* make() {
      return ::new (mi_new(sizeof(T))) T;
    }
    template <class T>
    void drop(T* node) noexcept {
      mi_free(node);
    }
    [[nodiscard]] static const PageReport* pages() noexcept { return nullptr; }
  };
};

}  // namespace itch
