// WebAssembly: the scalar kernel and the SIMD128 kernel. The module is built with -msimd128,
// so a runtime without SIMD refuses to load it at all, and once loaded both kernels run.
// CMake puts this directory on the include path of Emscripten builds only.
#pragma once

#include <array>
#include <optional>
#include <string>
#include <string_view>

#include "itch/kernels/kernels.hpp"
#include "itch/kernels/scalar.hpp"
#include "itch/kernels/wasm.hpp"

namespace itch::kernels {

inline constexpr std::string_view kArch = "wasm";
inline constexpr std::array kBuilt{KernelId::scalar, KernelId::wasm};

[[nodiscard]] inline std::optional<std::string> cpu_lacks(KernelId /*id*/) { return std::nullopt; }

[[nodiscard]] inline KernelId widest() noexcept { return KernelId::wasm; }

template <class Work>
decltype(auto) run(Selected kernel, Work& work) {
  if (kernel.id() == KernelId::wasm) {
    return work.template operator()<wasm::Kernel>();
  }
  // A Selected here is wasm or scalar: select_kernel() makes nothing else here.
  return work.template operator()<scalar::Kernel>();
}

}  // namespace itch::kernels
