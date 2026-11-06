// The kernels' names, and how a name becomes a kernel that runs, or the reason it cannot.
//
// Which kernels a build has is decided by CMake, not by the preprocessor: cmake/ItchKernels.cmake
// puts one of include/itch/kernels/arch/<arch>/ on the include path, and its kernels_built.hpp
// says what that architecture builds and how each runs.
//
//   arm64    scalar and neon. NEON is part of AArch64, so nothing is checked at run time.
//   x86_64   scalar, ssse3, avx2 and avx512vbmi. Each vector kernel's loop runs inside a
//            function compiled for that ISA alone, picked once by __builtin_cpu_supports.
//   wasm     scalar and wasm (SIMD128, compiled in with -msimd128).
//   generic  scalar alone.
//
// select_kernel() is the only way to a Selected, and run() takes only a Selected, so a kernel this
// build lacks, or this CPU cannot run, is an error with its reason and never a quiet run of
// some other kernel.
#pragma once

#include <array>
#include <cstdint>
#include <expected>
#include <optional>
#include <string>
#include <string_view>

namespace itch::kernels {

enum class KernelId : std::uint8_t { scalar, neon, ssse3, avx2, avx512vbmi, wasm };

inline constexpr std::array kAllKernels{KernelId::scalar, KernelId::neon,       KernelId::ssse3,
                                        KernelId::avx2,   KernelId::avx512vbmi, KernelId::wasm};

[[nodiscard]] constexpr std::string_view to_string(KernelId id) noexcept {
  switch (id) {
    case KernelId::scalar: return "scalar";
    case KernelId::neon: return "neon";
    case KernelId::ssse3: return "ssse3";
    case KernelId::avx2: return "avx2";
    case KernelId::avx512vbmi: return "avx512vbmi";
    case KernelId::wasm: return "wasm";
  }
  return "unnamed";
}

[[nodiscard]] constexpr std::optional<KernelId> parse_kernel(std::string_view name) noexcept {
  for (const KernelId id : kAllKernels) {
    if (name == to_string(id)) {
      return id;
    }
  }
  return std::nullopt;
}

// A kernel select_kernel() has found built and runnable here.
class Selected {
 public:
  [[nodiscard]] KernelId id() const noexcept { return id_; }
  [[nodiscard]] bool from_auto() const noexcept { return from_auto_; }

 private:
  constexpr Selected(KernelId id, bool from_auto) noexcept : id_{id}, from_auto_{from_auto} {}
  KernelId id_;
  bool from_auto_;

  friend std::expected<Selected, std::string> select_kernel(std::string_view name);
};

}  // namespace itch::kernels
