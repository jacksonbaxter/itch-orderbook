// The main of every GoogleTest binary here, in place of gtest_main.
//
// Run by hand, it is gtest_main: a skipped test passes. Run by ctest, which sets
// ITCH_CTEST_SKIPS for each entry and takes kSkipCode as SKIP_RETURN_CODE
// (tests/CMakeLists.txt), the entry shows as Skipped only when the binary passed and at least
// one test skipped for a stated reason, each printed. A failure always fails, whatever
// skipped beside it; a skip with no reason fails; and an entry whose filter matched no test
// fails. A skip regex cannot do this: ctest takes a match as Skipped even when the binary
// failed.
#include <gtest/gtest.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <print>
#include <string>
#include <string_view>

namespace {

constexpr int kSkipCode = 77;  // SKIP_RETURN_CODE of every GoogleTest entry.

// Why a skipped test skipped: the messages of its skip parts, each without the blank space
// gtest puts around it, joined; empty for a bare GTEST_SKIP(), whose message is a newline.
std::string skip_reason(const testing::TestResult& result) {
  constexpr std::string_view kBlank = " \t\r\n";
  std::string reason;
  for (int i = 0; i < result.total_part_count(); ++i) {
    const testing::TestPartResult& part = result.GetTestPartResult(i);
    if (!part.skipped() || part.message() == nullptr) {
      continue;
    }
    std::string_view message{part.message()};
    message.remove_prefix(std::min(message.find_first_not_of(kBlank), message.size()));
    message.remove_suffix(message.size() - (message.find_last_not_of(kBlank) + 1));
    if (!message.empty()) {
      reason += reason.empty() ? "" : "; ";
      reason += message;
    }
  }
  return reason;
}

// ctest's verdict on a run that passed: 0, kSkipCode, or 1 for a run that looks like a pass
// and either checked nothing or did not say why it skipped.
int verdict(const testing::UnitTest& unit) {
  if (unit.test_to_run_count() == 0) {
    std::println(stderr, "ctest entry: no test matched the filter");
    return 1;
  }
  if (unit.skipped_test_count() == 0) {
    return 0;
  }
  int unexplained = 0;
  for (int s = 0; s < unit.total_test_suite_count(); ++s) {
    const testing::TestSuite& suite = *unit.GetTestSuite(s);
    for (int t = 0; t < suite.total_test_count(); ++t) {
      const testing::TestInfo& test = *suite.GetTestInfo(t);
      if (!test.should_run() || !test.result()->Skipped()) {
        continue;
      }
      const std::string reason = skip_reason(*test.result());
      std::println("ctest entry: skipped {}.{}: {}", suite.name(), test.name(),
                   reason.empty() ? "NO REASON GIVEN" : reason);
      unexplained += reason.empty() ? 1 : 0;
    }
  }
  if (unexplained != 0) {
    std::println(stderr, "ctest entry: {} test(s) skipped without a reason", unexplained);
    return 1;
  }
  return kSkipCode;
}

int run(int argc, char** argv) {
  testing::InitGoogleTest(&argc, argv);
  const int result = RUN_ALL_TESTS();
  // One thread reads the environment here, after every test has finished.
  const bool under_ctest =
      std::getenv("ITCH_CTEST_SKIPS") != nullptr;  // NOLINT(concurrency-mt-unsafe)
  if (!under_ctest || result != 0) {
    return result;
  }
  return verdict(*testing::UnitTest::GetInstance());
}

}  // namespace

int main(int argc, char** argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception& error) {
    static_cast<void>(std::fputs(error.what(), stderr));
    return 2;
  }
}
