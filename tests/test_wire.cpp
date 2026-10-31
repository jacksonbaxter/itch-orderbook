// read_be, read_be48 and the alpha reads, against hand-written bytes.
#include <gtest/gtest.h>

#include <array>
#include <cstddef>
#include <cstdint>

#include "itch/wire.hpp"

namespace {

template <std::size_t N>
constexpr std::array<std::byte, N> bytes(const std::array<std::uint8_t, N>& raw) {
  std::array<std::byte, N> out{};
  for (std::size_t i = 0; i < N; ++i) {
    out[i] = static_cast<std::byte>(raw[i]);
  }
  return out;
}

TEST(Wire, ReadBe16) {
  const auto wire = bytes<2>({0x12, 0x34});
  EXPECT_EQ(itch::read_be<std::uint16_t>(wire.data()), 0x1234U);
}

TEST(Wire, ReadBe32) {
  const auto wire = bytes<4>({0x12, 0x34, 0x56, 0x78});
  EXPECT_EQ(itch::read_be<std::uint32_t>(wire.data()), 0x12345678U);
}

TEST(Wire, ReadBe64) {
  const auto wire = bytes<8>({0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF});
  EXPECT_EQ(itch::read_be<std::uint64_t>(wire.data()), 0x0123456789ABCDEFULL);
}

TEST(Wire, ReadBe48) {
  const auto wire = bytes<6>({0x01, 0x23, 0x45, 0x67, 0x89, 0xAB});
  EXPECT_EQ(itch::read_be48(wire.data()), 0x0123456789ABULL);
}

TEST(Wire, ReadBe48LimitsOfTheField) {
  const auto low = bytes<6>({0x00, 0x00, 0x00, 0x00, 0x00, 0x01});
  const auto high = bytes<6>({0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF});
  EXPECT_EQ(itch::read_be48(low.data()), 1U);
  EXPECT_EQ(itch::read_be48(high.data()), 0xFFFFFFFFFFFFULL);
}

TEST(Wire, ReadBe48IgnoresTheBytesThatFollow) {
  const auto wire = bytes<8>({0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0xFF, 0xFF});
  EXPECT_EQ(itch::read_be48(wire.data()), 0x010203040506ULL);
}

// No multi-byte field has a guaranteed alignment, so every read must work at any
// address. Under UBSan this also shows no read goes through a misaligned pointer.
TEST(Wire, ReadsAtEveryAlignment) {
  const auto wire = bytes<16>({0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A,
                               0x1B, 0x1C, 0x1D, 0x1E, 0x1F});
  for (std::uint8_t at = 0; at < 8; ++at) {
    const std::uint64_t first = 0x10ULL + at;
    const auto expected_be = [first](unsigned width) {
      std::uint64_t value = 0;
      for (unsigned i = 0; i < width; ++i) {
        value = (value << 8U) | (first + i);
      }
      return value;
    };
    const std::byte* src = wire.data() + at;
    EXPECT_EQ(itch::read_be<std::uint16_t>(src), expected_be(2));
    EXPECT_EQ(itch::read_be<std::uint32_t>(src), expected_be(4));
    EXPECT_EQ(itch::read_be<std::uint64_t>(src), expected_be(8));
    EXPECT_EQ(itch::read_be48(src), expected_be(6));
  }
}

TEST(Wire, ReadChar) {
  const auto wire = bytes<2>({0x41, 0xFF});
  EXPECT_EQ(itch::read_char(wire.data()), 'A');
  EXPECT_EQ(static_cast<unsigned char>(itch::read_char(wire.data() + 1)), 0xFFU);
}

TEST(Wire, ReadAlphaKeepsPadding) {
  const auto wire = bytes<4>({0x41, 0x42, 0x20, 0x20});
  const std::array<char, 4> expected{'A', 'B', ' ', ' '};
  EXPECT_EQ(itch::read_alpha<4>(wire.data()), expected);
}

}  // namespace
