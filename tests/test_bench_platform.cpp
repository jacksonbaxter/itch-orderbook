// The decisions in bench/platform, on every host. The Linux logic reads through mocks
// here, so what it does with a reading is tested on macOS as well.
#include <gtest/gtest.h>
#include <sys/mman.h>
#include <unistd.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"

// ASSERT_TRUE guards each dereference here, which the check cannot see.
// NOLINTBEGIN(bugprone-unchecked-optional-access)
namespace {

using namespace itch::bench;

// debian's TSC: every read is a multiple of 20, and a pair takes 38 ticks on average.
struct SteppedClock {
  std::uint64_t now = 1000;
  std::uint64_t advance = 38;
  std::uint64_t read() {
    now += advance;
    advance = advance == 38 ? 39 : 38;
    return now / 20 * 20;
  }
};

TEST(BenchTimer, QuantumIsTheDivisorOfTheStepsNotTheSmallestStep) {
  SteppedClock clock;
  const TimerProbe probe =
      probe_timer([&] { return clock.read(); }, [&] { return clock.read(); }, 1024);
  EXPECT_EQ(probe.quantum_ticks, 20U);
  EXPECT_GT(probe.pair_ticks, 30.0);
  EXPECT_LT(probe.pair_ticks, 45.0);
}

TEST(BenchTimer, AClockThatNeverMovesHasNoQuantum) {
  const TimerProbe probe = probe_timer([] { return 7U; }, [] { return 7U; }, 16);
  EXPECT_EQ(probe.quantum_ticks, 0U);
  EXPECT_DOUBLE_EQ(probe.zero_fraction, 1.0);
}

TEST(BenchTimer, TheLiveClockIsMeasuredNotAssumed) {
  const TimerInfo timer = measure_timer();
  EXPECT_GT(timer.quantum_ticks, 0U);
  EXPECT_GT(timer.calibrated_hz, 1e6);
  EXPECT_GT(timer.floor_ns(), timer.quantum_ns());
}

TEST(BenchCounters, AFullRunIsMeasured) {
  const CounterStatus status = classify_counter("cycles", 500, 1000, 1000);
  EXPECT_EQ(status.state, CounterState::measured);
  EXPECT_EQ(status.value, 500U);
}

TEST(BenchCounters, APartialRunIsMultiplexedNeverMeasured) {
  const CounterStatus status = classify_counter("cycles", 500, 1000, 999);
  EXPECT_EQ(status.state, CounterState::multiplexed);
  EXPECT_NEAR(status.running_ratio, 0.999, 1e-9);
  EXPECT_EQ(describe_counter(status), "cycles=multiplexed ratio 0.9990");
}

TEST(BenchCounters, AnUnscheduledEventIsUnavailableWithAReason) {
  const CounterStatus status = classify_counter("cycles", 0, 1000, 0);
  EXPECT_EQ(status.state, CounterState::unavailable);
  EXPECT_FALSE(status.reason.empty());
  EXPECT_TRUE(describe_counter(status).starts_with("cycles=unavailable: "));
}

TEST(BenchCounters, NoCountersIsStatedNotBlank) {
  EXPECT_TRUE(describe_counters({}).starts_with("unavailable: "));
}

TEST(BenchCounters, GroupReadFollowsTheIdsNotTheOrder) {
  const std::array<std::uint64_t, 7> words{2, 100, 90, 11, 77, 22, 55};
  const std::array<std::uint64_t, 2> ids{55, 77};
  const std::optional<GroupRead> read = parse_group_read(words, ids);
  ASSERT_TRUE(read.has_value());
  EXPECT_EQ(read->time_enabled, 100U);
  EXPECT_EQ(read->time_running, 90U);
  EXPECT_EQ(read->values, (std::vector<std::uint64_t>{22, 11}));
}

TEST(BenchCounters, AShortOrForeignGroupReadIsNoRead) {
  const std::array<std::uint64_t, 2> ids{55, 77};
  const std::array<std::uint64_t, 5> short_words{2, 100, 90, 11, 77};
  EXPECT_FALSE(parse_group_read(short_words, ids).has_value());
  const std::array<std::uint64_t, 7> foreign{2, 100, 90, 11, 77, 22, 66};
  EXPECT_FALSE(parse_group_read(foreign, ids).has_value());
}

// A counter whose two read paths can be made to differ.
struct MockSource {
  std::size_t events = 2;
  std::uint64_t by_descriptor = 0;
  std::uint64_t by_rdpmc = 0;
  std::uint64_t descriptor_step = 1'000'000;
  std::uint64_t rdpmc_step = 1'000'000;
  bool rdpmc_works = true;

  [[nodiscard]] std::size_t event_count() const { return events; }
  [[nodiscard]] bool read_descriptors(std::vector<std::uint64_t>& out) {
    out.assign(events, by_descriptor);
    by_descriptor += descriptor_step;
    return true;
  }
  [[nodiscard]] bool read_rdpmc(std::vector<std::uint64_t>& out) {
    out.assign(events, by_rdpmc);
    by_rdpmc += rdpmc_step;
    return rdpmc_works;
  }
};

TEST(BenchCounters, RdpmcAndReadAgreeWhenTheCountsMatch) {
  MockSource source;
  bool ran = false;
  const Agreement agreement = cross_check(source, [&] { ran = true; });
  EXPECT_TRUE(ran);
  EXPECT_EQ(agreement.state, AgreementState::agreed);
}

TEST(BenchCounters, RdpmcAndReadDisagreeingIsCaught) {
  MockSource source{.rdpmc_step = 1'100'000};
  const Agreement agreement = cross_check(source, [] {});
  EXPECT_EQ(agreement.state, AgreementState::disagreed);
  EXPECT_NEAR(agreement.worst_relative, 0.1, 1e-9);
  EXPECT_TRUE(describe_agreement(agreement).starts_with("disagreed: "));
}

TEST(BenchCounters, ADisagreementInsideTheSlackIsNotOne) {
  MockSource source{.descriptor_step = 1000, .rdpmc_step = 1500};
  EXPECT_EQ(cross_check(source, [] {}).state, AgreementState::agreed);
}

TEST(BenchCounters, NoRdpmcValueIsUnavailableAndTheWorkStillRuns) {
  MockSource source{.rdpmc_works = false};
  bool ran = false;
  const Agreement agreement = cross_check(source, [&] { ran = true; });
  EXPECT_TRUE(ran);
  EXPECT_EQ(agreement.state, AgreementState::unavailable);
}

TEST(BenchCounters, NoEventsAtAllIsUnavailableAndTheWorkStillRuns) {
  MockSource source{.events = 0};
  bool ran = false;
  const Agreement agreement = cross_check(source, [&] { ran = true; });
  EXPECT_TRUE(ran);
  EXPECT_EQ(agreement.state, AgreementState::unavailable);
}

TEST(BenchCounters, TheLiveGroupNeverFailsTheRun) {
  CounterGroup group{"unknown"};
  group.start();
  group.stop();
  for (const CounterStatus& status : group.statuses()) {
    EXPECT_FALSE(status.name.empty());
    if (status.state == CounterState::unavailable) {
      EXPECT_FALSE(status.reason.empty());
    }
  }
  EXPECT_FALSE(probe_rdpmc(group).empty());
}

TEST(BenchAffinity, ReadBackOfExactlyTheCoreIsAPin) {
  const std::array<int, 1> mask{4};
  const PinOutcome outcome = judge_pin(4, 0, mask);
  EXPECT_TRUE(outcome.pinned);
  EXPECT_TRUE(pinning_field(outcome).starts_with("pinned to core 4"));
}

TEST(BenchAffinity, AFailedCallIsNeverClaimed) {
  const std::array<int, 1> mask{4};
  const PinOutcome outcome = judge_pin(4, 22, mask);
  EXPECT_FALSE(outcome.pinned);
  EXPECT_TRUE(pinning_field(outcome).starts_with("not pinned: "));
}

TEST(BenchAffinity, AWiderOrWrongMaskIsNeverClaimed) {
  const std::array<int, 2> wide{4, 5};
  EXPECT_FALSE(judge_pin(4, 0, wide).pinned);
  const std::array<int, 1> wrong{6};
  EXPECT_FALSE(judge_pin(4, 0, wrong).pinned);
  EXPECT_FALSE(judge_pin(4, 0, {}).pinned);
}

TEST(BenchAffinity, NoRequestIsNoPin) {
  const std::array<int, 1> mask{0};
  EXPECT_FALSE(judge_pin(-1, 0, mask).pinned);
}

TEST(BenchAffinity, ALivePinIsRecordedWhateverItDid) {
  const PinOutcome outcome = pin_current_thread(-1);
  EXPECT_FALSE(outcome.pinned);
  EXPECT_TRUE(pinning_field(outcome).starts_with("not pinned: "));
}

TEST(BenchAffinity, MigrationsAreCountedBetweenSamples) {
  MigrationTracker tracker;
  EXPECT_TRUE(tracker.describe().starts_with("unavailable: "));
  tracker.sample(3);
  tracker.sample(3);
  tracker.sample(5);
  tracker.sample(-1);
  tracker.sample(3);
  EXPECT_EQ(tracker.migrations(), 2U);
  EXPECT_EQ(tracker.first_core(), 3);
  EXPECT_EQ(tracker.last_core(), 3);
}

class Mapping {
 public:
  explicit Mapping(std::size_t bytes)
      : bytes_{bytes},
        base_{::mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0)} {}
  Mapping(const Mapping&) = delete;
  Mapping& operator=(const Mapping&) = delete;
  Mapping(Mapping&&) = delete;
  Mapping& operator=(Mapping&&) = delete;
  ~Mapping() { ::munmap(base_, bytes_); }
  [[nodiscard]] std::span<std::byte> bytes() const {
    return {static_cast<std::byte*>(base_), bytes_};
  }

 private:
  std::size_t bytes_;
  void* base_;
};

TEST(BenchMemory, UntouchedPagesAreNotResident) {
  const std::size_t page = base_page_size();
  const Mapping mapping{page * 8};
  const Residency residency = resident(mapping.bytes());
  EXPECT_EQ(residency.pages, 8U);
  EXPECT_EQ(residency.resident_pages, 0U);
  EXPECT_FALSE(residency.complete());
}

TEST(BenchMemory, OneMissingPageIsNotComplete) {
  const std::size_t page = base_page_size();
  const Mapping mapping{page * 8};
  for (std::size_t i = 0; i < 7; ++i) {
    mapping.bytes()[i * page] = std::byte{1};
  }
  const Residency residency = resident(mapping.bytes());
  EXPECT_EQ(residency.resident_pages, 7U);
  EXPECT_FALSE(residency.complete());
  mapping.bytes()[7 * page] = std::byte{1};
  EXPECT_TRUE(resident(mapping.bytes()).complete());
}

TEST(BenchMemory, AnUnalignedSpanIsAnErrorNotAPass) {
  const std::size_t page = base_page_size();
  const Mapping mapping{page * 2};
  const Residency residency = resident(mapping.bytes().subspan(1));
  EXPECT_FALSE(residency.error.empty());
  EXPECT_FALSE(residency.complete());
  EXPECT_TRUE(residency.describe().starts_with("unavailable: "));
}

TEST(BenchMemory, TheArenaIsPrefaultedAndReportsItsPage) {
  const std::optional<Arena> arena = Arena::make(std::size_t{1} << 20U);
  ASSERT_TRUE(arena.has_value());
  EXPECT_GE(arena->size(), std::size_t{1} << 20U);
  EXPECT_TRUE(arena->report().prefaulted);
  EXPECT_GE(arena->report().page_size_obtained, base_page_size());
  EXPECT_TRUE(resident({arena->data(), arena->size()}).complete());
}

TEST(BenchMemory, SmapsAnswerIsForTheRightMapping) {
  const std::string smaps =
      "1000-2000 rw-p 00000000 00:00 0\nAnonHugePages:      8 kB\n"
      "7f00200000-7f00800000 rw-p 00000000 00:00 0\nSize: 6144 kB\n"
      "AnonHugePages:   6144 kB\n";
  EXPECT_EQ(hugepage_kb_from_smaps(smaps, 0x7f00200000), 6144U);
  EXPECT_EQ(hugepage_kb_from_smaps(smaps, 0x1000), 8U);
  EXPECT_EQ(hugepage_kb_from_smaps(smaps, 0x9000), std::nullopt);
}

SysSource mock_linux(std::map<std::string, std::string> files) {
  return {
      .os = kOsLinux,
      .file = [files = std::move(files)](const std::string& path) -> std::optional<std::string> {
        const auto at = files.find(path);
        return at == files.end() ? std::nullopt : std::optional<std::string>{at->second};
      },
      .sysctl = [](const std::string&) { return std::nullopt; }};
}

const std::map<std::string, std::string> kDebianFiles{
    {"/proc/cpuinfo", "processor : 0\nmodel name\t: AMD Ryzen Embedded V1605B with Radeon\n"},
    {"/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor", "performance\n"},
    {"/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq", "3500000\n"},
    {"/sys/devices/system/cpu/cpufreq/boost", "1\n"},
    {"/sys/devices/system/cpu/smt/active", "1\n"},
    {"/sys/devices/system/cpu/isolated", "\n"},
};

TEST(BenchSysinfo, LinuxFieldsAreRead) {
  const SysSource source = mock_linux(kDebianFiles);
  EXPECT_EQ(read_cpu_model(source), "AMD Ryzen Embedded V1605B with Radeon");
  EXPECT_EQ(read_governor(source, 4), "performance");
  EXPECT_EQ(read_frequency(source, 4), "3500000 kHz");
  EXPECT_EQ(read_turbo(source), "on");
  EXPECT_EQ(read_smt(source), "on");
  EXPECT_EQ(read_isolation(source), "none");
}

TEST(BenchSysinfo, AnUnreadableFieldIsUnavailableNeverAGuess) {
  const SysSource source = mock_linux({});
  EXPECT_TRUE(read_cpu_model(source).starts_with("unavailable: "));
  EXPECT_TRUE(read_governor(source, 4).starts_with("unavailable: "));
  EXPECT_TRUE(read_frequency(source, 4).starts_with("unavailable: "));
  EXPECT_TRUE(read_turbo(source).starts_with("unavailable: "));
  EXPECT_TRUE(read_smt(source).starts_with("unavailable: "));
  EXPECT_TRUE(read_isolation(source).starts_with("unavailable: "));
}

TEST(BenchSysinfo, MacOSCannotReadGovernorFrequencyOrTurbo) {
  const SysSource source{.os = kOsMac,
                         .file = [](const std::string&) { return std::nullopt; },
                         .sysctl = [](const std::string&) { return std::nullopt; }};
  EXPECT_TRUE(read_governor(source, -1).starts_with("unavailable: "));
  EXPECT_TRUE(read_frequency(source, -1).starts_with("unavailable: "));
  EXPECT_TRUE(read_turbo(source).starts_with("unavailable: "));
  EXPECT_TRUE(read_isolation(source).starts_with("unavailable: "));
}

TEST(BenchSysinfo, HostLabelIsOneOfThreeFixedStrings) {
  EXPECT_EQ(host_label(kOsMac, "Apple M4 Max"), "macOS (M4 Max)");
  EXPECT_EQ(host_label(kOsLinux, "AMD Ryzen Embedded V1605B with Radeon Vega Gfx"), "debian");
  EXPECT_EQ(host_label(kOsLinux, "Apple M4 Max"), "unknown");
  EXPECT_EQ(host_label(kOsMac, "Apple M1"), "unknown");
  EXPECT_EQ(host_label("FreeBSD", "AMD Ryzen Embedded V1605B"), "unknown");
}

TEST(BenchSysinfo, PrivateTextIsScrubbed) {
  const std::string home = "/" + std::string{"Users"} + "/someone/sdk/usr";
  const std::string linux_home = "/" + std::string{"home"} + "/someone/x";
  const std::string address = "100." + std::string{"64"} + ".0.1";
  EXPECT_EQ(scrub_private("-isysroot " + home + " -O3"), "-isysroot <path> -O3");
  EXPECT_EQ(scrub_private(linux_home), "<path>");
  EXPECT_EQ(scrub_private("from " + address + " ok"), "from <address> ok");
  EXPECT_EQ(scrub_private("clang version 23.1.4"), "clang version 23.1.4");
}

HeaderInputs complete_inputs() {
  return {.compiler = "clang version 23.1.4",
          .flags = "-O3",
          .commit = "abc",
          .dataset = "day",
          .dataset_sha256 = "00",
          .timer = "t",
          .quantum = "q",
          .pair = "p",
          .floor = "f",
          .pinning = "not pinned: x",
          .migrations = "0",
          .core = "4",
          .core_number = 4,
          .counters = "c",
          .rdpmc = "r",
          .page_size = "s",
          .residency = "1 of 1 pages resident"};
}

TEST(BenchSysinfo, TheHeaderHasEveryFieldInOrder) {
  const std::vector<HeaderField> header = build_header(mock_linux(kDebianFiles), complete_inputs());
  ASSERT_EQ(header.size(), kHeaderFields.size());
  for (std::size_t i = 0; i < header.size(); ++i) {
    EXPECT_EQ(header[i].first, kHeaderFields[i]);
    EXPECT_FALSE(header[i].second.empty()) << header[i].first;
  }
  EXPECT_EQ(header[0].second, "debian");
}

TEST(BenchSysinfo, ASuppliedFieldLeftEmptyIsUnavailable) {
  HeaderInputs inputs = complete_inputs();
  inputs.commit.clear();
  for (const HeaderField& field : build_header(mock_linux(kDebianFiles), inputs)) {
    if (field.first == "commit") {
      EXPECT_TRUE(field.second.starts_with("unavailable: "));
    }
  }
}

TEST(BenchSysinfo, TheHeaderNeverCarriesTheHostname) {
  std::array<char, 256> name{};
  ASSERT_EQ(::gethostname(name.data(), name.size()), 0);
  const std::string hostname{name.data()};
  ASSERT_FALSE(hostname.empty());
  for (const HeaderField& field : build_header(live_source(), complete_inputs())) {
    // The host field is one of three fixed strings, and debian's hostname is also its label.
    if (field.first == "host") {
      EXPECT_TRUE(field.second == kHostMac || field.second == kHostDebian ||
                  field.second == kHostUnknown)
          << field.second;
      continue;
    }
    EXPECT_FALSE(field.second.contains(hostname)) << field.first;
  }
}

}  // namespace
// NOLINTEND(bugprone-unchecked-optional-access)
