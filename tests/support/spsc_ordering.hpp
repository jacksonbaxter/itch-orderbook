// The memory orders the ring's two-thread tests and spsc_stress run with.
//
// By default, the ring's own SpscOrdering. Configured with
// -DITCH_SPSC_NEGATIVE_CONTROL=<role>, the same orders with that one role weakened to
// relaxed: publish (the producer's store of its index), observe (the consumer's load of
// it), return (the consumer's store of its index) or reclaim (the producer's load of it).
// That build is the negative control: TSan must report a race on the slots in it, and
// what spsc_stress sees in it without TSan is recorded either way (components.md, LC-37).
// The ring's header has no switch of its own; only these test builds change the orders.
#pragma once

#include <atomic>
#include <string_view>

#include "itch/spsc.hpp"

namespace itch::testing {

struct TestOrdering : SpscOrdering {
#if defined(ITCH_SPSC_WEAKEN_PUBLISH)
  static constexpr std::memory_order kPublish = std::memory_order_relaxed;
  static constexpr std::string_view kWeakened = "publish";
#elif defined(ITCH_SPSC_WEAKEN_OBSERVE)
  static constexpr std::memory_order kObserve = std::memory_order_relaxed;
  static constexpr std::string_view kWeakened = "observe";
#elif defined(ITCH_SPSC_WEAKEN_RETURN)
  static constexpr std::memory_order kReturn = std::memory_order_relaxed;
  static constexpr std::string_view kWeakened = "return";
#elif defined(ITCH_SPSC_WEAKEN_RECLAIM)
  static constexpr std::memory_order kReclaim = std::memory_order_relaxed;
  static constexpr std::string_view kWeakened = "reclaim";
#else
  static constexpr std::string_view kWeakened = "none";
#endif
};

#if defined(__SANITIZE_THREAD__)
inline constexpr bool kUnderTsan = true;
#elif defined(__has_feature)
#if __has_feature(thread_sanitizer)
inline constexpr bool kUnderTsan = true;
#else
inline constexpr bool kUnderTsan = false;
#endif
#else
inline constexpr bool kUnderTsan = false;
#endif

// What a log line says about the build.
[[nodiscard]] constexpr std::string_view ordering_label() noexcept {
  return TestOrdering::kWeakened == "none" ? "the shipped orders"
                                           : "NEGATIVE CONTROL, one order weakened to relaxed";
}

}  // namespace itch::testing
