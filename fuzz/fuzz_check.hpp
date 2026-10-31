// The failure path of a fuzz target. It aborts with the condition named, so that
// libFuzzer saves the input, and the corpus replay fails in ordinary builds. It is
// not an assert, which NDEBUG would remove from the release replay.
#pragma once

#include <cstdlib>
#include <print>

namespace itch::fuzzing {

[[noreturn]] inline void check_failed(const char* condition, const char* file, int line) {
  std::println(stderr, "FUZZ_CHECK failed: {} at {}:{}", condition, file, line);
  std::abort();
}

}  // namespace itch::fuzzing

#define FUZZ_CHECK(condition) \
  ((condition) ? void() : ::itch::fuzzing::check_failed(#condition, __FILE__, __LINE__))
