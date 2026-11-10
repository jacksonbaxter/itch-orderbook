// --kernel=NAME: the kernel itself, the widest one here for auto, or the reason it cannot run.
#pragma once

#include <algorithm>
#include <expected>
#include <format>
#include <string>
#include <string_view>

#include "itch/kernels/kernels.hpp"
#include "kernels_built.hpp"

namespace itch::kernels {

[[nodiscard]] inline bool built(KernelId id) noexcept {
  return std::ranges::any_of(kBuilt, [id](KernelId b) { return b == id; });
}

[[nodiscard]] inline std::string built_names() {
  std::string names;
  for (const KernelId b : kBuilt) {
    names += names.empty() ? "" : ", ";
    names += to_string(b);
  }
  return names;
}

[[nodiscard]] inline std::expected<Selected, std::string> select_kernel(std::string_view name) {
  if (name == "auto") {
    return Selected{widest(), true};
  }
  const std::optional<KernelId> id = parse_kernel(name);
  if (!id) {
    return std::unexpected(std::format(
        "unknown kernel {}: the kernels are scalar, neon, ssse3, avx2, avx512vbmi, wasm and auto",
        name));
  }
  if (!built(*id)) {
    return std::unexpected(std::format("kernel {} is not built: this is an {} build, with {}", name,
                                       kArch, built_names()));
  }
  if (const std::optional<std::string> lacks = cpu_lacks(*id)) {
    return std::unexpected(std::format(
        "kernel {} cannot run here: this CPU lacks {} (__builtin_cpu_supports)", name, *lacks));
  }
  return Selected{*id, false};
}

}  // namespace itch::kernels
