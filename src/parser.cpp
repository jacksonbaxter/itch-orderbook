// The out-of-line part of the parser. Everything else is in the headers, so that a
// caller can inline the decode.
#include <string_view>

#include "itch/messages.hpp"
#include "itch/reader.hpp"

namespace itch {

std::string_view to_string(FrameStatus status) noexcept {
  switch (status) {
    case FrameStatus::message: return "message";
    case FrameStatus::unknown_type: return "unknown_type";
    case FrameStatus::bad_length: return "bad_length";
    case FrameStatus::zero_length: return "zero_length";
    case FrameStatus::end_of_input: return "end_of_input";
    case FrameStatus::truncated_prefix: return "truncated_prefix";
    case FrameStatus::truncated_payload: return "truncated_payload";
  }
  return "invalid";
}

std::string_view to_string(DecodeError error) noexcept {
  switch (error) {
    case DecodeError::empty_payload: return "empty_payload";
    case DecodeError::unknown_type: return "unknown_type";
    case DecodeError::bad_length: return "bad_length";
  }
  return "invalid";
}

}  // namespace itch
