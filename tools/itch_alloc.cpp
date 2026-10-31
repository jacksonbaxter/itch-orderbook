// T-ALLOC-1 on a real day: the frame reader and the fast book allocate nothing between
// the end of warm-up and the end of the stream, and a control book replaying the same
// stream is seen allocating.
//
//     itch_alloc FILE [--warmup N] [--max-orders N] [--max-levels N]
//
// Warm-up is the first N messages, 0 by default, so the count starts at the first
// message after construction. Exit codes: 0 pass, 1 fail, 2 bad usage.
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <optional>
#include <print>
#include <span>
#include <string_view>
#include <system_error>

#include "itch/book.hpp"
#include "itch/reader.hpp"
#include "mapped_file.hpp"
#include "oracle/alloc_counter.hpp"
#include "oracle/reference_book.hpp"

namespace {

using itch::oracle::AllocCounts;

struct Options {
  const char* path = nullptr;
  std::uint64_t warmup = 0;
  itch::BookCapacity capacity;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

[[nodiscard]] std::optional<Options> parse_options(std::span<const char* const> args) {
  Options options;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string_view arg = args[i];
    if (!arg.starts_with("--")) {
      if (options.path != nullptr) {
        return std::nullopt;
      }
      options.path = args[i];
      continue;
    }
    if (i + 1 == args.size()) {
      return std::nullopt;
    }
    const auto number = parse_number(args[++i]);
    if (!number) {
      return std::nullopt;
    }
    if (arg == "--warmup") {
      options.warmup = *number;
    } else if (arg == "--max-orders") {
      options.capacity.orders = static_cast<std::uint32_t>(*number);
    } else if (arg == "--max-levels") {
      options.capacity.levels = static_cast<std::uint32_t>(*number);
    } else {
      return std::nullopt;
    }
  }
  return options.path == nullptr ? std::nullopt : std::optional{options};
}

struct Pass {
  std::uint64_t messages = 0;
  std::uint64_t refused = 0;
  AllocCounts allocations;
};

template <class Apply>
[[nodiscard]] Pass replay(std::span<const std::byte> bytes, std::uint64_t warmup, Apply apply) {
  Pass pass;
  itch::FrameReader reader{bytes};
  if (warmup == 0) {
    itch::oracle::reset_alloc_counts();
  }
  for (itch::Frame frame = reader.next(); !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    pass.refused += apply(frame.payload) ? 0U : 1U;
    ++pass.messages;
    if (pass.messages == warmup) {
      itch::oracle::reset_alloc_counts();
    }
  }
  pass.allocations = itch::oracle::read_alloc_counts();
  return pass;
}

void print_pass(std::string_view name, const Pass& pass) {
  std::println("{}_messages {}", name, pass.messages);
  std::println("{}_refused {}", name, pass.refused);
  std::println("{}_allocations {}", name, pass.allocations.total());
  for (std::size_t form = 0; form < pass.allocations.by_form.size(); ++form) {
    std::println("{}_allocations_form {} {}", name, itch::oracle::kAllocFormNames[form],
                 pass.allocations.by_form[form]);
  }
}

int run(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_alloc: cannot read {}", options.path);
    return 2;
  }
  std::println("warmup_messages {}", options.warmup);

  itch::FastBook<> fast{options.capacity};
  const Pass fast_pass =
      replay(file->bytes(), options.warmup, [&fast](std::span<const std::byte> payload) {
        const itch::ApplyStatus status = fast.apply(payload);
        return status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect;
      });
  print_pass("fast", fast_pass);

  itch::oracle::ReferenceBook control;
  const Pass control_pass =
      replay(file->bytes(), options.warmup, [&control](std::span<const std::byte> payload) {
        const itch::oracle::RefStatus status = control.apply(payload).status;
        return status == itch::oracle::RefStatus::applied ||
               status == itch::oracle::RefStatus::no_book_effect;
      });
  print_pass("control", control_pass);

  const bool fast_clean = fast_pass.allocations.total() == 0 && fast_pass.refused == 0;
  const bool control_seen = control_pass.allocations.total() != 0;
  const bool ran = fast_pass.messages == control_pass.messages && fast_pass.messages > 0;
  if (!control_seen) {
    std::println("itch_alloc: FAIL the control book made no allocation");
  }
  std::println("verdict T-ALLOC-1 {} fast={} control={}",
               fast_clean && control_seen && ran ? "PASS" : "FAIL", fast_pass.allocations.total(),
               control_pass.allocations.total());
  return fast_clean && control_seen && ran ? 0 : 1;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(std::fputs(
          "usage: itch_alloc FILE [--warmup N] [--max-orders N] [--max-levels N]\n", stderr));
      return 2;
    }
    return run(*options);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
