// One message per input, per type.
//
// The first bytes of the input fill a buffer of each type's exact length, so that an
// input exercises all 23 decoders. Each buffer is its own heap block, and ASan sees
// any read past the end. Every decode has to re-encode to the same bytes, which shows
// that each byte landed in exactly one field. The input itself is also decoded as it
// stands, and the verdict has to match the length table.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

#include "fuzz_check.hpp"
#include "itch/messages.hpp"
#include "support/encode.hpp"

namespace {

void check_as_received(std::span<const std::byte> payload) {
  const auto decoded = itch::decode(payload);
  if (payload.empty()) {
    FUZZ_CHECK(decoded.error() == itch::DecodeError::empty_payload);
    return;
  }
  const std::size_t wanted = itch::message_length(static_cast<char>(payload[0]));
  if (wanted == 0) {
    FUZZ_CHECK(decoded.error() == itch::DecodeError::unknown_type);
  } else if (wanted != payload.size()) {
    FUZZ_CHECK(decoded.error() == itch::DecodeError::bad_length);
  } else {
    FUZZ_CHECK(decoded.has_value());
    const std::vector<std::byte> again = itch::testing::encode(*decoded);
    FUZZ_CHECK(std::ranges::equal(again, payload));
  }
}

void check_as_type(const itch::MessageSpec& spec, std::span<const std::byte> input) {
  std::vector<std::byte> buffer(spec.length);
  std::ranges::copy(input.first(std::min(input.size(), buffer.size())), buffer.begin());
  buffer[0] = static_cast<std::byte>(spec.type);

  const auto decoded = itch::decode(buffer);
  FUZZ_CHECK(decoded.has_value());
  const std::vector<std::byte> again = itch::testing::encode(*decoded);
  FUZZ_CHECK(again == buffer);

  // One byte short: the same bytes must now be refused, with no field read.
  const auto short_by_one = itch::decode(std::span{buffer}.first(buffer.size() - 1));
  FUZZ_CHECK(!short_by_one.has_value());
  FUZZ_CHECK(short_by_one.error() == itch::DecodeError::bad_length);
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  const auto input = std::as_bytes(std::span{data, size});
  check_as_received(input);
  for (const itch::MessageSpec& spec : itch::kMessageSpecs) {
    check_as_type(spec, input);
  }
  return 0;
}
