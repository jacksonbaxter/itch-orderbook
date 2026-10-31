// Wire primitives for ITCH 5.0: big-endian integers and fixed-width alpha fields.
//
// Every read copies out of the byte buffer with memcpy. Nothing here casts a
// pointer over wire bytes, because no multi-byte field in a BinaryFILE stream has
// a guaranteed natural alignment. Each function reads exactly the bytes its name
// promises, and the caller is responsible for those bytes being in bounds.
#pragma once

#include <array>
#include <bit>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace itch {

// Reads sizeof(T) bytes in network byte order.
template <std::unsigned_integral T>
[[nodiscard]] inline T read_be(const std::byte* src) noexcept {
  T raw{};
  std::memcpy(&raw, src, sizeof(T));
  if constexpr (std::endian::native == std::endian::little) {
    return std::byteswap(raw);
  } else {
    return raw;
  }
}

// Reads exactly 6 bytes in network byte order, the width of every ITCH timestamp.
// An 8-byte load here would pass every value test and read past the end of a
// 12-byte message, so tests/test_guard_page.cpp holds it to the 6.
[[nodiscard]] inline std::uint64_t read_be48(const std::byte* src) noexcept {
  std::array<std::byte, sizeof(std::uint64_t)> wide{};
  std::memcpy(wide.data() + 2, src, 6);
  return read_be<std::uint64_t>(wide.data());
}

// Reads one byte as an ASCII code. Enumerated fields stay open sets: the feed has
// values the spec does not list.
[[nodiscard]] inline char read_char(const std::byte* src) noexcept {
  return static_cast<char>(*src);
}

// Reads N bytes of alpha field, padding included.
template <std::size_t N>
[[nodiscard]] inline std::array<char, N> read_alpha(const std::byte* src) noexcept {
  std::array<char, N> field{};
  std::memcpy(field.data(), src, N);
  return field;
}

}  // namespace itch
