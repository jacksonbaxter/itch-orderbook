// Stands in for libFuzzer in ordinary builds: runs a target once on each file of the
// corpus directories named on its command line, with no mutation. It fails if it
// finds nothing, because a replay of no inputs would pass and show nothing.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <print>
#include <span>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);

namespace {

int replay(std::span<const char* const> directories) {
  std::vector<std::filesystem::path> inputs;
  for (const char* directory : directories) {
    for (const auto& entry : std::filesystem::directory_iterator{directory}) {
      if (entry.is_regular_file()) {
        inputs.push_back(entry.path());
      }
    }
  }
  std::ranges::sort(inputs);

  std::uint64_t bytes = 0;
  for (const auto& path : inputs) {
    std::ifstream file{path, std::ios::binary};
    const std::vector<char> raw{std::istreambuf_iterator<char>{file},
                                std::istreambuf_iterator<char>{}};
    // An exact-size heap copy, as libFuzzer gives a target, so that ASan sees over-reads.
    const std::vector<std::uint8_t> input(raw.begin(), raw.end());
    LLVMFuzzerTestOneInput(input.data(), input.size());
    bytes += input.size();
  }

  std::println("replayed {} inputs, {} bytes", inputs.size(), bytes);
  if (inputs.empty()) {
    std::println(stderr, "no corpus inputs found");
    return 1;
  }
  return 0;
}

}  // namespace

int main(int argc, const char* const* argv) {
  try {
    return replay(std::span{argv + 1, static_cast<std::size_t>(argc - 1)});
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 1;
  }
}
