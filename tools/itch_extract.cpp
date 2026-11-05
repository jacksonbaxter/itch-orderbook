// Cuts one symbol's extract out of a day, for the demo page, and checks it.
//
//     itch_extract DAY SYMBOL OUT
//
// The extract is every frame on the symbol's locate, from its Stock Directory message on,
// and every System Event, framed as in the day's file, so that the frame reader walks it
// as it walks the day. It is market data: OUT belongs in a gitignored folder, and
// scripts/demo.py puts it in docs/local/.
//
// Then it replays the extract on the page's own replay (include/itch/demo_replay.hpp), at
// the book's default capacity, and prints one JSON object on stdout: the counts, each
// status, and the digest the page's engine must reach after replaying the same extract.
//
// Exit codes: 0 written and checked, 1 the symbol is not in the day, the extract is larger
// than the engine takes, or the replay of it refused a message or ran out of room;
// 2 bad usage, an unreadable day, a day that does not end on a frame boundary, or an
// unwritable OUT.
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <fstream>
#include <memory>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "demo_engine.hpp"
#include "itch/book_types.hpp"
#include "itch/demo_replay.hpp"
#include "itch/messages.hpp"
#include "itch/reader.hpp"
#include "itch/wire.hpp"
#include "mapped_file.hpp"

namespace {

constexpr std::size_t kLocates = 65536;

struct Cut {
  std::uint64_t day_messages = 0;
  std::uint64_t messages = 0;
  std::uint64_t bytes = 0;
  std::uint16_t locate = 0;
  bool found = false;
  bool clean = false;  // The day ended on a frame boundary.
};

// Kept: every System Event, and every frame on the symbol's locate once its Stock Directory
// has named it.
Cut cut(std::span<const std::byte> day, const itch::Stock& symbol, std::ofstream& out) {
  Cut result;
  std::vector<std::uint8_t> ours(kLocates, 0);
  itch::FrameReader reader{day};
  itch::Frame frame = reader.next();
  for (; !itch::is_terminal(frame.status); frame = reader.next()) {
    if (frame.status != itch::FrameStatus::message) {
      continue;
    }
    ++result.day_messages;
    const std::byte* m = frame.payload.data();
    const char type = itch::read_char(m);
    const auto locate = itch::read_be<std::uint16_t>(m + 1);
    if (type == itch::StockDirectory::kType) {
      const bool named = itch::StockDirectory::from_wire(m).stock == symbol;
      ours[locate] = named ? 1U : 0U;
      if (named) {
        result.found = true;
        result.locate = locate;
      }
    }
    if (type != itch::SystemEvent::kType && ours[locate] == 0) {
      continue;
    }
    const std::array<char, 2> length{static_cast<char>(frame.payload.size() >> 8U),
                                     static_cast<char>(frame.payload.size() & 0xFFU)};
    out.write(length.data(), length.size());
    out.write(
        reinterpret_cast<const char*>(m),  // NOLINT(cppcoreguidelines-pro-type-reinterpret-cast)
        static_cast<std::streamsize>(frame.payload.size()));
    ++result.messages;
    result.bytes += frame.payload.size() + 2;
  }
  result.clean = frame.status == itch::FrameStatus::end_of_input;
  return result;
}

std::vector<std::byte> read_whole(const std::string& path) {
  std::ifstream in{path, std::ios::binary | std::ios::ate};
  if (!in) {
    return {};
  }
  std::vector<std::byte> bytes(static_cast<std::size_t>(in.tellg()));
  in.seekg(0);
  in.read(
      reinterpret_cast<char*>(bytes.data()),  // NOLINT(cppcoreguidelines-pro-type-reinterpret-cast)
      static_cast<std::streamsize>(bytes.size()));
  return in ? bytes : std::vector<std::byte>{};
}

int run(const char* day_path, std::string_view name, const std::string& out_path) {
  if (name.empty() || name.size() > 8) {
    std::println(stderr, "itch_extract: a symbol is 1 to 8 characters");
    return 2;
  }
  itch::Stock symbol{};
  symbol.fill(' ');
  std::ranges::copy(name, symbol.begin());

  const auto day = itch::tools::MappedFile::open(day_path);
  if (!day) {
    std::println(stderr, "itch_extract: cannot map {}", day_path);
    return 2;
  }
  Cut result;
  {
    std::ofstream out{out_path, std::ios::binary | std::ios::trunc};
    if (!out) {
      std::println(stderr, "itch_extract: cannot write {}", out_path);
      return 2;
    }
    result = cut(day->bytes(), symbol, out);
    out.close();
    if (!out) {
      std::println(stderr, "itch_extract: writing {} failed", out_path);
      return 2;
    }
  }
  if (!result.clean) {
    std::println(stderr, "itch_extract: the day does not end on a frame boundary");
    return 2;
  }
  if (!result.found) {
    std::println(stderr, "itch_extract: no Stock Directory message names {}", name);
    return 1;
  }
  if (result.bytes > itch::demo::kMaxInputBytes) {
    std::println(stderr, "itch_extract: {} bytes, more than the engine's {} bytes of input",
                 result.bytes, itch::demo::kMaxInputBytes);
    return 1;
  }

  const std::vector<std::byte> extract = read_whole(out_path);
  if (extract.size() != result.bytes) {
    std::println(stderr, "itch_extract: read back {} bytes of {}", extract.size(), result.bytes);
    return 2;
  }
  const auto replay = std::make_unique<itch::demo::Replay>(extract);
  const std::uint64_t applied = replay->replay();
  const auto status = [&replay](itch::ApplyStatus s) { return replay->status_count(s); };
  const std::uint64_t refused =
      applied - status(itch::ApplyStatus::applied) - status(itch::ApplyStatus::no_book_effect);
  const auto& index = replay->book().index();

  std::print(
      "{{\"symbol\": \"{}\", \"locate\": {}, \"day_messages\": {}, \"messages\": {}, "
      "\"bytes\": {}, \"extract_status\": \"{}\", \"applied\": {}, \"statuses\": {{",
      name, result.locate, result.day_messages, result.messages, result.bytes,
      itch::demo::to_string(replay->extract_status()), applied);
  for (std::size_t s = 0; s < itch::demo::kStatusCount; ++s) {
    const auto which = static_cast<itch::ApplyStatus>(s);
    std::print("{}\"{}\": {}", s == 0 ? "" : ", ", itch::to_string(which), status(which));
  }
  std::println(
      "}}, \"peak_live_orders\": {}, \"peak_live_levels\": {}, \"peak_live_pages\": {}, "
      "\"fallback_inserts\": {}, \"first_timestamp_ns\": {}, \"digest\": \"{:016x}\"}}",
      replay->book().peak_live_orders(), replay->book().peak_live_levels(), index.peak_live_pages(),
      index.fallback_inserts(), replay->first_timestamp_ns(), replay->digest());

  const bool good = replay->extract_status() == itch::demo::ExtractStatus::ok &&
                    applied == result.messages && refused == 0;
  if (!good) {
    std::println(stderr, "itch_extract: the replay of the extract was not clean: {} refused, {}",
                 refused, itch::demo::to_string(replay->extract_status()));
  }
  return good ? 0 : 1;
}

}  // namespace

int main(int argc, const char* const* argv) {
  const std::span<const char* const> args{argv, static_cast<std::size_t>(argc)};
  if (args.size() != 4) {
    static_cast<void>(std::fputs("usage: itch_extract DAY SYMBOL OUT\n", stderr));
    return 2;
  }
  try {
    return run(args[1], args[2], args[3]);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
