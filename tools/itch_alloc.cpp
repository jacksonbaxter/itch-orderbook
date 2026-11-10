// T-ALLOC-1 on a real day: the frame reader and the fast book allocate nothing between
// the end of warm-up and the end of the stream, and a control book replaying the same
// stream is seen allocating.
//
//     itch_alloc FILE [--warmup N] [--max-orders N] [--max-levels N] [--index hash|paged]
//                [--levels linked|flat] [--max-pages N] [--index-bits N] [--max-slots N]
//                [--prefetch D] [--allocator ARM] [--pages base|huge-if-offered|...]
//
// Warm-up is the first N messages, 0 by default, so the count starts at the first
// message after construction. --index and --levels pick the arm of the fast book, the
// book's default arm if left out, and the capacities are as itch_oracle's. --prefetch
// walks the fast book's pass with itch/lookahead.hpp's reader, D messages ahead, so that
// the reader's ring and the book's look-ahead are inside the count. Exit codes: 0 pass,
// 1 fail, 2 bad usage.
//
// --allocator runs the node book on one of T-ARENA-1's allocator arms (tools/alloc_arms.hpp)
// in place of the fast book, its pools on the pages --pages asks for, huge-if-offered by
// default, and the capacities as --max-orders and --max-levels say. A pool arm passes as
// the fast book does, on no allocation at all. Any other arm allocates by design, so its
// verdict is REPORT, with its allocations per message, and fails only if it refused a
// message or the count cannot be right: an arm the counter sees that made no allocation.
// The counter sees the global operator new alone, so mimalloc's calls are counted by the
// book, one per node made, and an aged heap's ballast calls by the ager, and taken off.
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

#include "alloc_arms.hpp"
#include "book_arms.hpp"
#include "itch/book.hpp"
#include "itch/lookahead.hpp"
#include "itch/page_map.hpp"
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
  itch::tools::BookArm arm;
  std::uint64_t prefetch = 0;  // Messages the fast book looks ahead.
  std::optional<itch::tools::AllocArm> allocator;
  itch::NodeCapacity node_capacity;
};

[[nodiscard]] std::optional<std::uint64_t> parse_number(std::string_view text) {
  std::uint64_t value = 0;
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || end != text.data() + text.size()) {
    return std::nullopt;
  }
  return value;
}

enum class Taken : std::uint8_t { yes, bad_value, not_this };

// --allocator and --pages, the node book's options.
[[nodiscard]] Taken take_node_option(std::string_view arg, const char* value, Options& options) {
  if (arg == "--allocator") {
    options.allocator = itch::tools::parse_alloc_arm(value);
    return options.allocator ? Taken::yes : Taken::bad_value;
  }
  if (arg == "--pages") {
    const std::optional<itch::PageRequest> pages = itch::parse_page_request(value);
    if (!pages) {
      return Taken::bad_value;
    }
    options.node_capacity.pages = *pages;
    return Taken::yes;
  }
  return Taken::not_this;
}

// The options that take a number. --max-orders and --max-levels size both books.
[[nodiscard]] bool take_number_option(std::string_view arg, std::uint64_t number,
                                      Options& options) {
  if (arg == "--warmup") {
    options.warmup = number;
  } else if (arg == "--prefetch") {
    options.prefetch = number;
  } else if (arg == "--max-orders") {
    options.capacity.orders = static_cast<std::uint32_t>(number);
    options.node_capacity.orders = options.capacity.orders;
  } else if (arg == "--max-levels") {
    options.capacity.levels = static_cast<std::uint32_t>(number);
    options.node_capacity.levels = options.capacity.levels;
  } else {
    return false;
  }
  return true;
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
    ++i;
    switch (take_node_option(arg, args[i], options)) {
      case Taken::yes: continue;
      case Taken::bad_value: return std::nullopt;
      case Taken::not_this: break;
    }
    switch (itch::tools::parse_arm_option(arg, args[i], options.arm, options.capacity)) {
      case itch::tools::ArmOption::taken: continue;
      case itch::tools::ArmOption::bad_value: return std::nullopt;
      case itch::tools::ArmOption::not_an_arm_option: break;
    }
    const auto number = parse_number(args[i]);
    if (!number || !take_number_option(arg, *number, options)) {
      return std::nullopt;
    }
  }
  // The look-ahead reader drives the fast book alone.
  if (options.allocator && options.prefetch != 0) {
    return std::nullopt;
  }
  return options.path == nullptr ? std::nullopt : std::optional{options};
}

struct Pass {
  std::uint64_t messages = 0;
  std::uint64_t refused = 0;
  AllocCounts allocations;
};

template <class Reader, class Apply>
[[nodiscard]] Pass replay(Reader reader, std::uint64_t warmup, Apply apply) {
  Pass pass;
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

// The control: the reference book, which takes a node from the heap for every order.
[[nodiscard]] Pass control_replay(std::span<const std::byte> bytes, std::uint64_t warmup) {
  itch::oracle::ReferenceBook control;
  return replay(itch::FrameReader{bytes}, warmup, [&control](std::span<const std::byte> payload) {
    const itch::oracle::RefStatus status = control.apply(payload).status;
    return status == itch::oracle::RefStatus::applied ||
           status == itch::oracle::RefStatus::no_book_effect;
  });
}

template <class Index, class Levels>
int run(const Options& options) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_alloc: cannot read {}", options.path);
    return 2;
  }
  std::println("book_index {}", to_string(options.arm.index));
  std::println("book_levels {}", to_string(options.arm.levels));
  std::println("warmup_messages {}", options.warmup);
  std::println("book_prefetch {}", options.prefetch);

  using Fast = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
  Fast fast{options.capacity};
  const Pass fast_pass = replay(
      itch::LookaheadReader<Fast>{file->bytes(), fast, static_cast<unsigned>(options.prefetch)},
      options.warmup, [&fast](std::span<const std::byte> payload) {
        const itch::ApplyStatus status = fast.apply(payload);
        return status == itch::ApplyStatus::applied || status == itch::ApplyStatus::no_book_effect;
      });
  print_pass("fast", fast_pass);

  const Pass control_pass = control_replay(file->bytes(), options.warmup);
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

template <class Node>
void print_node_pages(const Node& node) {
  if (const itch::PageReport* pages = node.heap().pages(); pages != nullptr) {
    std::println("node_pages {}", pages->describe());
  } else {
    std::println("node_pages none: this arm takes its nodes from a heap");
  }
}

// A pool arm passes or fails on its allocations; a heap arm is reported, unless it failed.
[[nodiscard]] std::string_view arm_verdict(bool pass, bool pool) {
  if (!pass) {
    return "FAIL";
  }
  return pool ? "PASS" : "REPORT";
}

// The node book on one allocator arm, then the control, and the verdict for that arm.
template <class Book>
int run_node(const Options& options, itch::tools::AllocArm arm) {
  const auto file = itch::tools::MappedFile::open(options.path);
  if (!file) {
    std::println(stderr, "itch_alloc: cannot read {}", options.path);
    return 2;
  }
  const bool pool = itch::tools::is_pool_arm(arm);
  std::println("node_allocator {}", to_string(arm));
  std::println("node_capacity orders {} levels {}", options.node_capacity.orders,
               options.node_capacity.levels);
  std::println("warmup_messages {}", options.warmup);

  Book book{options.node_capacity};
  const auto& node = itch::tools::node_book_of(book);
  print_node_pages(node);
  std::uint64_t ager_before = 0;
  if constexpr (requires { book.ager(); }) {
    ager_before = book.ager().allocations();
  }
  const Pass node_pass = replay(itch::FrameReader{file->bytes()}, options.warmup,
                                [&book](std::span<const std::byte> payload) {
                                  if constexpr (requires { book.before_message(payload); }) {
                                    book.before_message(payload);
                                  }
                                  const itch::ApplyStatus status = book.apply(payload);
                                  return status == itch::ApplyStatus::applied ||
                                         status == itch::ApplyStatus::no_book_effect;
                                });
  print_pass("node", node_pass);
  // The ager's calls since the count began, which are the ballast's and not the book's. A
  // warm-up longer than zero would put some of them before the count, so they are only
  // taken off when the count began at the first message.
  std::uint64_t ager_calls = 0;
  if constexpr (requires { book.ager(); }) {
    ager_calls = options.warmup == 0 ? book.ager().allocations() - ager_before : 0;
    std::println("node_ager_allocations {}", ager_calls);
  }
  const std::uint64_t counted = node_pass.allocations.total();
  const std::uint64_t book_calls = counted >= ager_calls ? counted - ager_calls : 0;
  std::println("node_nodes_made {}", node.nodes_made());
  std::println("node_peak_live orders {} levels {}", node.peak_live_orders(),
               node.peak_live_levels());
  // What the arm's allocations are counted by: the counter, or the book for mimalloc.
  const bool by_counter = itch::tools::counter_sees(arm) || pool;
  const std::uint64_t arm_calls = by_counter ? book_calls : node.nodes_made();
  std::println("node_allocations_counted_by {}",
               by_counter ? "the counter, the global operator new"
                          : "the book, one call per node made, since mi_new is not the global "
                            "operator new the counter sees");
  std::println("node_book_allocations {}", arm_calls);
  std::println("node_allocations_per_message {:.6f}",
               node_pass.messages == 0
                   ? 0.0
                   : static_cast<double>(arm_calls) / static_cast<double>(node_pass.messages));

  const Pass control_pass = control_replay(file->bytes(), options.warmup);
  print_pass("control", control_pass);

  const bool control_seen = control_pass.allocations.total() != 0;
  const bool ran = node_pass.messages == control_pass.messages && node_pass.messages > 0;
  const bool clean = node_pass.refused == 0;
  if (!control_seen) {
    std::println("itch_alloc: FAIL the control book made no allocation");
  }
  if (!clean) {
    std::println("itch_alloc: FAIL the node book refused {} messages", node_pass.refused);
  }
  bool pass = control_seen && ran && clean;
  if (pool) {
    pass = pass && counted == 0;
  } else if (itch::tools::counter_sees(arm) && arm_calls == 0 && options.warmup == 0) {
    std::println("itch_alloc: FAIL a heap arm the counter sees made no allocation");
    pass = false;
  }
  std::println("verdict T-ARENA-1-ALLOC {} arm={} book={} control={}", arm_verdict(pass, pool),
               to_string(arm), arm_calls, control_pass.allocations.total());
  return pass ? 0 : 1;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    const auto options = parse_options(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
    if (!options) {
      static_cast<void>(
          std::fputs("usage: itch_alloc FILE [--warmup N] [--max-orders N] [--max-levels N] "
                     "[--index hash|paged] [--levels linked|flat] [--max-pages N] "
                     "[--index-bits N] [--max-slots N] [--prefetch D] [--allocator ARM] "
                     "[--pages base|huge-if-offered|transparent|explicit]\n",
                     stderr));
      return 2;
    }
    if (options->allocator) {
      const itch::tools::AllocArm arm = *options->allocator;
      return itch::tools::with_alloc_arm(
          arm, [&options, arm]<class Book> { return run_node<Book>(*options, arm); },
          [] {
            std::println(stderr, "itch_alloc: this build has no mimalloc (ITCH_MIMALLOC off)");
            return 2;
          });
    }
    return itch::tools::with_book_arm(options->arm, [&options]<class Index, class Levels> {
      using Fast = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
      if (options->prefetch > itch::LookaheadReader<Fast>::kMostAhead ||
          !itch::LookaheadReader<Fast>::valid_distance(static_cast<unsigned>(options->prefetch))) {
        std::println(stderr, "itch_alloc: --prefetch {} is neither 0 nor {} to {} for this arm",
                     options->prefetch, Fast::kPrefetchStages,
                     itch::LookaheadReader<Fast>::kMostAhead);
        return 2;
      }
      return run<Index, Levels>(*options);
    });
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
