// x86-64: the scalar kernel and three vector kernels, each compiled under its own target
// attribute, with no -m flag and no -march=native, so the build runs on any x86-64.
//
// A vector kernel only pays if the loop around it is compiled for the same ISA, since a
// function under target("avx2") is never inlined into one without it. So run() calls the
// work through a function of that target, and the work's operator() is always_inline: the
// whole hot loop, frame walk, kernel and consumer, lands inside the target region, as
// simdjson builds its parsers (simd.md, SIMD-39). Which kernel may run is decided once, at
// select_kernel(), by __builtin_cpu_supports.
#pragma once

#include <array>
#include <optional>
#include <string>
#include <string_view>

#include "itch/kernels/avx2.hpp"
#include "itch/kernels/avx512vbmi.hpp"
#include "itch/kernels/kernels.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/kernels/ssse3.hpp"

namespace itch::kernels {

inline constexpr std::string_view kArch = "x86_64";
inline constexpr std::array kBuilt{KernelId::scalar, KernelId::ssse3, KernelId::avx2,
                                   KernelId::avx512vbmi};

// What this CPU lacks for a kernel, by __builtin_cpu_supports, which also checks that the OS
// saves the wider registers. Nothing for a kernel it can run.
[[nodiscard]] inline std::optional<std::string> cpu_lacks(KernelId id) {
  std::string missing;
  const auto need = [&missing](bool present, std::string_view feature) {
    if (!present) {
      missing += missing.empty() ? "" : ", ";
      missing += feature;
    }
  };
  switch (id) {
    case KernelId::ssse3: need(__builtin_cpu_supports("ssse3") != 0, "ssse3"); break;
    case KernelId::avx2: need(__builtin_cpu_supports("avx2") != 0, "avx2"); break;
    case KernelId::avx512vbmi:
      need(__builtin_cpu_supports("avx512f") != 0, "avx512f");
      need(__builtin_cpu_supports("avx512bw") != 0, "avx512bw");
      need(__builtin_cpu_supports("avx512vl") != 0, "avx512vl");
      need(__builtin_cpu_supports("avx512vbmi") != 0, "avx512vbmi");
      break;
    default: break;
  }
  if (missing.empty()) {
    return std::nullopt;
  }
  return missing;
}

// The widest kernel this CPU runs, found once.
[[nodiscard]] inline KernelId widest() {
  static const KernelId found = [] {
    for (const KernelId id : {KernelId::avx512vbmi, KernelId::avx2, KernelId::ssse3}) {
      if (!cpu_lacks(id)) {
        return id;
      }
    }
    return KernelId::scalar;
  }();
  return found;
}

namespace detail {

template <class Work>
[[gnu::target("ssse3")]] decltype(auto) run_ssse3(Work& work) {
  return work.template operator()<ssse3::Kernel>();
}

template <class Work>
[[gnu::target("avx2")]] decltype(auto) run_avx2(Work& work) {
  return work.template operator()<avx2::Kernel>();
}

template <class Work>
[[gnu::target("avx512f,avx512bw,avx512vl,avx512vbmi")]] decltype(auto) run_avx512vbmi(
    Work& work) {
  return work.template operator()<avx512vbmi::Kernel>();
}

}  // namespace detail

template <class Work>
decltype(auto) run(Selected kernel, Work& work) {
  switch (kernel.id()) {
    case KernelId::ssse3: return detail::run_ssse3(work);
    case KernelId::avx2: return detail::run_avx2(work);
    case KernelId::avx512vbmi: return detail::run_avx512vbmi(work);
    default: break;
  }
  // A Selected here is scalar: select_kernel() makes nothing else on x86-64.
  return work.template operator()<scalar::Kernel>();
}

}  // namespace itch::kernels
