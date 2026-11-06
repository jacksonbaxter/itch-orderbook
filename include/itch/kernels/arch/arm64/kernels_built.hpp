// arm64: the scalar and NEON kernels, both compiled in, both always runnable. NEON is part of
// AArch64, so there is no dispatch code: --kernel picks between the two for the experiments,
// and auto is NEON. CMake puts this directory on the include path of arm64 builds only.
#pragma once

#include <array>
#include <optional>
#include <string>
#include <string_view>

#include "itch/kernels/kernels.hpp"
#include "itch/kernels/neon.hpp"
#include "itch/kernels/scalar.hpp"

namespace itch::kernels {

inline constexpr std::string_view kArch = "arm64";
inline constexpr std::array kBuilt{KernelId::scalar, KernelId::neon};

[[nodiscard]] inline std::optional<std::string> cpu_lacks(KernelId /*id*/) { return std::nullopt; }

[[nodiscard]] inline KernelId widest() noexcept { return KernelId::neon; }

template <class Work>
decltype(auto) run(Selected kernel, Work& work) {
  if (kernel.id() == KernelId::neon) {
    return work.template operator()<neon::Kernel>();
  }
  // A Selected here is neon or scalar: select_kernel() makes nothing else on arm64.
  return work.template operator()<scalar::Kernel>();
}

}  // namespace itch::kernels
