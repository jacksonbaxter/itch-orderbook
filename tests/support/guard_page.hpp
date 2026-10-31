// Memory that faults one byte past either end, and a way to survive the fault.
#pragma once

#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <optional>
#include <span>
#include <string>

namespace itch::testing {

// One read-write region with a PROT_NONE page on each side.
class GuardedRegion {
 public:
  explicit GuardedRegion(std::size_t capacity)
      : page_{static_cast<std::size_t>(::sysconf(_SC_PAGESIZE))},
        usable_{((capacity / page_) + 1) * page_} {
    void* base = ::mmap(nullptr, usable_ + (2 * page_), PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (base == MAP_FAILED) {
      std::abort();
    }
    base_ = static_cast<std::byte*>(base);
    if (::mprotect(base_ + page_, usable_, PROT_READ | PROT_WRITE) != 0) {
      std::abort();
    }
  }
  GuardedRegion(const GuardedRegion&) = delete;
  GuardedRegion& operator=(const GuardedRegion&) = delete;
  ~GuardedRegion() { ::munmap(base_, usable_ + (2 * page_)); }

  // Copies bytes so that the last one is the last readable byte before the guard.
  [[nodiscard]] std::span<const std::byte> flush_against_end(std::span<const std::byte> bytes) {
    std::byte* start = base_ + page_ + usable_ - bytes.size();
    if (!bytes.empty()) {
      std::memcpy(start, bytes.data(), bytes.size());
    }
    return {start, bytes.size()};
  }

  // Copies bytes so that the first one is the first readable byte after the guard.
  [[nodiscard]] std::span<const std::byte> flush_against_start(std::span<const std::byte> bytes) {
    std::byte* start = base_ + page_;
    if (!bytes.empty()) {
      std::memcpy(start, bytes.data(), bytes.size());
    }
    return {start, bytes.size()};
  }

  // The first byte of the rear guard page.
  [[nodiscard]] const std::byte* rear_guard() const { return base_ + page_ + usable_; }

 private:
  std::size_t page_;
  std::size_t usable_;
  std::byte* base_ = nullptr;
};

struct ChildResult {
  std::optional<int> signal;  // The signal that killed the child, if one did.
  int exit_code = 0;

  [[nodiscard]] bool clean() const { return !signal && exit_code == 0; }
  [[nodiscard]] std::string describe() const {
    if (signal) {
      return "child died on signal " + std::to_string(*signal) +
             ": it read past the buffer into the PROT_NONE guard page";
    }
    return "child reported " + std::to_string(exit_code) + " failed checks";
  }
};

// Runs body in a forked child, so that a guard-page fault is a result the test can
// report and not the end of the test. body returns how many of its checks failed.
[[nodiscard]] inline ChildResult run_in_child(const std::function<int()>& body) {
  const pid_t child = ::fork();
  if (child < 0) {
    std::abort();
  }
  if (child == 0) {
    const int failures = body();
    ::_exit(failures > 125 ? 125 : failures);
  }
  int status = 0;
  if (::waitpid(child, &status, 0) != child) {
    std::abort();
  }
  if (WIFSIGNALED(status)) {
    return {.signal = WTERMSIG(status)};
  }
  return {.signal = std::nullopt, .exit_code = WEXITSTATUS(status)};
}

}  // namespace itch::testing
