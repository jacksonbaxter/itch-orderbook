// Any other architecture: the scalar kernel alone. Every vector kernel is "not built" here.
#pragma once

#include <array>
#include <optional>
#include <string>
#include <string_view>

#include "itch/kernels/kernels.hpp"
#include "itch/kernels/scalar.hpp"

namespace itch::kernels {

inline constexpr std::string_view kArch = "generic";
inline constexpr std::array kBuilt{KernelId::scalar};

[[nodiscard]] inline std::optional<std::string> cpu_lacks(KernelId /*id*/) { return std::nullopt; }

[[nodiscard]] inline KernelId widest() noexcept { return KernelId::scalar; }

template <class Work>
decltype(auto) run(Selected /*kernel*/, Work& work) {
  return work.template operator()<scalar::Kernel>();
}

}  // namespace itch::kernels
