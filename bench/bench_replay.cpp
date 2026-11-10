// Replays a day or a fixture through one book design, with Google Benchmark running the
// two timed passes and HdrHistogram holding the latency distribution.
//
//     bench_replay --input FILE --out RUN.json [--design NAME] [--input-mode ram|mapped]
//                  [--track-top on|off] [--passes all|timed|throughput|untimed] [--core N]
//                  [--commit TEXT]
//                  [--dataset NAME]
//                  [--dataset-sha256 HEX] [--orders N] [--levels N]
//                  [--pages base|huge-if-offered|transparent|explicit] [--benchmark_...]
//
// --design      hash-linked, hash-flat, paged-linked or paged-flat for an arm of the fast
//               book, map-fresh or map-aged for the map book, or alloc-ARM for the node
//               book on one of T-ARENA-1's allocator arms: malloc-fresh, malloc-aged,
//               mimalloc, pmr-pool, pmr-monotonic, pool-index, pool-pointer or pool-fifo.
//               The default is the fast book's default arm. Every design runs the same
//               passes.
// --input-mode  ram reads the file into anonymous memory before the warm-up and never maps
//               it, as itch-book's book_throughput does for T-BOOK-1. mapped, the default,
//               maps the file read-only, as its replay tool does for T-BOOK-2. Either way
//               every page is resident at both ends of each timed pass.
// --track-top   on reads the best bid and ask on the locate of every message the book
//               applied and compares them with the last seen, as that replay tool does.
// --passes      timed runs the warm-up, throughput and latency passes. throughput runs the
//               warm-up and the throughput pass alone, and makes no buffer for the latency
//               samples, five bytes a message, which on a full day is the one large thing
//               the process holds besides its input. untimed runs only the
//               pass of counters and memory, after a warm-up that builds no book, so that the
//               memory it is charged for is that one book's. all, the default, runs both in
//               one process, and its memory figure is marked as not the first book's.
// --orders, --levels  the book's order and level capacity, the fast book's or the node
//               book's, whose defaults are its own.
// --pages       the pages a pool arm's nodes are mapped on, huge-if-offered by default
//               (itch/page_map.hpp). What each pass's pools obtained goes in the header.
//
// Each timed pass records the page faults it took, from getrusage on each side of it.
//
// One process is one repetition: scripts/bench_driver.py starts a fresh one for each.
// The record goes to --out whatever happens. Exit codes: 0 publishable, 3 refused, 2 bad
// usage. A refused run is a record of a refusal, never a number.
#include <benchmark/benchmark.h>
#include <fcntl.h>
#include <hdr/hdr_histogram.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <format>
#include <fstream>
#include <memory>
#include <optional>
#include <print>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "designs.hpp"
#include "itch/book.hpp"
#include "itch/heap_ager.hpp"
#include "itch/node_book.hpp"
#include "itch/page_map.hpp"
#include "mapped_file.hpp"
#include "platform/affinity.hpp"
#include "platform/counters.hpp"
#include "platform/memory.hpp"
#include "platform/sysinfo.hpp"
#include "platform/timer.hpp"
#include "replay.hpp"
#include "run_record.hpp"

namespace {

using namespace itch::bench;

class ReplayInput {
 public:
  explicit ReplayInput(std::span<const std::byte> bytes) noexcept : bytes_{bytes} {}
  [[nodiscard]] std::span<const std::byte> bytes() const noexcept { return bytes_; }
  [[nodiscard]] Residency residency() const { return resident(bytes_); }

  // A read of every page, to bring back any that an allocation pushed out.
  void fault_in() const noexcept {
    std::byte seen{0};
    for (std::size_t at = 0; at < bytes_.size(); at += base_page_size()) {
      seen |= bytes_[at];
    }
    last_seen_ = seen;
  }

 private:
  std::span<const std::byte> bytes_;
  mutable std::atomic<std::byte> last_seen_;  // So that the reads are not dropped.
};

// A histogram of ticks. Wide enough for a span of four seconds at any clock in use.
class Histogram {
 public:
  Histogram() { hdr_init(1, 10'000'000'000, 3, &histogram_); }
  Histogram(const Histogram&) = delete;
  Histogram& operator=(const Histogram&) = delete;
  Histogram(Histogram&&) = delete;
  Histogram& operator=(Histogram&&) = delete;
  ~Histogram() {
    if (histogram_ != nullptr) {
      hdr_close(histogram_);
    }
  }
  void record(std::uint64_t value) noexcept {
    hdr_record_value(histogram_, static_cast<std::int64_t>(value));
  }
  [[nodiscard]] double at(double percentile) const noexcept {
    return static_cast<double>(hdr_value_at_percentile(histogram_, percentile));
  }
  [[nodiscard]] double highest() const noexcept { return static_cast<double>(hdr_max(histogram_)); }

 private:
  hdr_histogram* histogram_ = nullptr;
};

enum class InputMode : std::uint8_t { ram, mapped };
enum class Passes : std::uint8_t { all, timed, throughput, untimed };

struct Options {
  std::string input;
  std::string out;
  std::string commit;
  std::string dataset;
  std::string dataset_sha256;
  int core = -1;
  itch::BookCapacity capacity;
  itch::NodeCapacity node_capacity;
  Design design;
  InputMode input_mode = InputMode::mapped;
  bool track_top = false;
  Passes passes = Passes::all;
};

template <class Number>
bool parse_number(std::string_view text, Number& out) {
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out);
  return error == std::errc{} && end == text.data() + text.size();
}

template <class Value, std::size_t N>
bool parse_choice(std::string_view text,
                  const std::array<std::pair<std::string_view, Value>, N>& choices, Value& out) {
  for (const auto& [name, value] : choices) {
    if (text == name) {
      out = value;
      return true;
    }
  }
  return false;
}

constexpr std::array<std::pair<std::string_view, InputMode>, 2> kInputModes{
    {{"ram", InputMode::ram}, {"mapped", InputMode::mapped}}};
constexpr std::array<std::pair<std::string_view, bool>, 2> kOnOff{{{"on", true}, {"off", false}}};
constexpr std::array<std::pair<std::string_view, Passes>, 4> kPassChoices{
    {{"all", Passes::all},
     {"timed", Passes::timed},
     {"throughput", Passes::throughput},
     {"untimed", Passes::untimed}}};

// One option and its value. False for an unknown option or a value it does not take.
bool parse_option(std::string_view arg, std::string_view value, Options& options) {
  if (arg == "--input") {
    options.input = value;
    return true;
  }
  if (arg == "--out") {
    options.out = value;
    return true;
  }
  if (arg == "--commit") {
    options.commit = value;
    return true;
  }
  if (arg == "--dataset") {
    options.dataset = value;
    return true;
  }
  if (arg == "--dataset-sha256") {
    options.dataset_sha256 = value;
    return true;
  }
  if (arg == "--core") {
    return parse_number(value, options.core);
  }
  if (arg == "--orders") {
    return parse_number(value, options.capacity.orders) &&
           parse_number(value, options.node_capacity.orders);
  }
  if (arg == "--levels") {
    return parse_number(value, options.capacity.levels) &&
           parse_number(value, options.node_capacity.levels);
  }
  if (arg == "--pages") {
    const std::optional<itch::PageRequest> pages = itch::parse_page_request(value);
    options.node_capacity.pages = pages.value_or(options.node_capacity.pages);
    return pages.has_value();
  }
  if (arg == "--design") {
    const std::optional<Design> design = parse_design(value);
    options.design = design.value_or(options.design);
    return design.has_value();
  }
  if (arg == "--input-mode") {
    return parse_choice(value, kInputModes, options.input_mode);
  }
  if (arg == "--track-top") {
    return parse_choice(value, kOnOff, options.track_top);
  }
  if (arg == "--passes") {
    return parse_choice(value, kPassChoices, options.passes);
  }
  return false;
}

bool parse(std::span<char*> args, Options& options) {
  for (std::size_t i = 0; i + 1 < args.size(); i += 2) {
    if (!parse_option(args[i], args[i + 1], options)) {
      return false;
    }
  }
  return args.size() % 2 == 0 && !options.input.empty() && !options.out.empty();
}

// The input as one pass reads it: the file mapped, or a copy of it in anonymous memory.
class OwnedInput {
 public:
  [[nodiscard]] static std::optional<OwnedInput> open(const Options& options) {
    if (options.input_mode == InputMode::mapped) {
      std::optional<itch::tools::MappedFile> file =
          itch::tools::MappedFile::open(options.input.c_str());
      if (!file) {
        return std::nullopt;
      }
      // MappedFile asks for sequential access, and a kernel may then drop pages behind the
      // reader. Here every pass needs them to stay.
      ::posix_madvise(const_cast<std::byte*>(file->bytes().data()),  // NOLINT
                      file->bytes().size(), POSIX_MADV_NORMAL);
      const std::span<const std::byte> bytes = file->bytes();
      return OwnedInput{std::move(file), std::nullopt, bytes,
                        "mapped: read-only, MAP_PRIVATE, posix_madvise(POSIX_MADV_NORMAL); every "
                        "page resident in the page cache at both ends of each timed pass"};
    }
    return read_into_memory(options.input);
  }

  [[nodiscard]] std::span<const std::byte> bytes() const noexcept { return bytes_; }
  [[nodiscard]] const std::string& describe() const noexcept { return description_; }

 private:
  OwnedInput(std::optional<itch::tools::MappedFile> mapped, std::optional<Arena> copy,
             std::span<const std::byte> bytes, std::string description)
      : mapped_{std::move(mapped)},
        copy_{std::move(copy)},
        bytes_{bytes},
        description_{std::move(description)} {}

  // The whole file read into an arena of base pages, with nothing left mapped. The copy and
  // the file's page cache are never both whole: each piece's pages are faulted in by the read
  // that fills them, and on Linux that piece's page cache is dropped as soon as it is copied,
  // so the two together hold about one day and one piece. A day read beside its whole cache
  // swapped other processes out on debian, whose memory the host's services share.
  [[nodiscard]] static std::optional<OwnedInput> read_into_memory(const std::string& path) {
    const int fd = ::open(path.c_str(), O_RDONLY);  // NOLINT(cppcoreguidelines-pro-type-vararg)
    if (fd < 0) {
      return std::nullopt;
    }
    struct stat info{};
    if (::fstat(fd, &info) != 0 || info.st_size <= 0) {
      ::close(fd);
      return std::nullopt;
    }
    const auto size = static_cast<std::size_t>(info.st_size);
    std::optional<Arena> copy = Arena::make(size, PageHint::base, Prefault::by_caller);
    // Small enough that one piece is the most held twice, and under the INT_MAX bytes that
    // macOS refuses in a single read.
    constexpr std::size_t kPiece = std::size_t{64} << 20U;
    std::size_t done = 0;
    while (copy && done < size) {
      const ssize_t got = ::read(fd, copy->data() + done, std::min(size - done, kPiece));
      if (got <= 0) {
        break;
      }
#ifdef __linux__
      static_cast<void>(::posix_fadvise(fd, static_cast<off_t>(done), static_cast<off_t>(got),
                                        POSIX_FADV_DONTNEED));
#endif
      done += static_cast<std::size_t>(got);
    }
    std::string description = std::format(
        "ram: read into anonymous memory before the warm-up, in pieces of {} MiB, each page "
        "faulted in by the read that fills it, ",
        kPiece >> 20U);
#ifdef __linux__
    // Read-ahead may have cached a little past the last piece dropped.
    static_cast<void>(::posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED));
    description +=
        "each piece's page cache dropped once it is copied (posix_fadvise POSIX_FADV_DONTNEED), ";
#else
    description +=
        "the file's page cache left to the kernel, since this host has no "
        "posix_fadvise, ";
#endif
    ::close(fd);
    if (!copy || done != size) {
      return std::nullopt;
    }
    description += std::format("never mapped; {}", copy->report().hint);
    const std::span<const std::byte> bytes{copy->data(), size};
    return OwnedInput{std::nullopt, std::move(copy), bytes, std::move(description)};
  }

  std::optional<itch::tools::MappedFile> mapped_;
  std::optional<Arena> copy_;
  std::span<const std::byte> bytes_;
  std::string description_;
};

// The book operations each timed pass's code warms up on, for a design that warms it.
constexpr std::uint64_t kCodeWarmOperations = 10'000;

std::string describe_design(const Options& options) {
  const Design& design = options.design;
  const itch::BookCapacity& capacity = options.capacity;
  const std::string name = design_name(design);
  constexpr std::string_view kMapWarmUp =
      "; the warm-up runs the fast book's hash index with linked levels, so the throughput "
      "pass's book is the "
      "first to take small blocks from this heap, and the latency pass's heap is the one the "
      "throughput pass's book gave back";
  if (design.kind == DesignKind::map_fresh) {
    return std::format(
        "{}: MapBook, std::map levels with std::list FIFOs and a std::unordered_map index, on "
        "the process's heap as it comes{}",
        name, kMapWarmUp);
  }
  if (design.kind == DesignKind::alloc) {
    const itch::NodeCapacity& nodes = options.node_capacity;
    const itch::HeapAgeing ageing;
    const std::string aged =
        design.allocator == itch::tools::AllocArm::malloc_aged
            ? std::format(
                  ", on a heap aged by HeapAger: seed {:#x}, {} warm blocks, a ring of {}, one "
                  "step before each A, F and U, outside the timing",
                  ageing.seed, ageing.warm_blocks, ageing.ring_blocks)
            : std::string{};
    return std::format(
        "{}: NodeBook on the {} arm{}, linked levels and FIFOs of nodes, an open-addressed "
        "index of 16-byte slots, {} orders and {} levels, pool pages asked as {}; the warm-up "
        "runs the fast book's hash index with linked levels, so that every arm warms up "
        "alike, and then each timed pass's code runs, untimed, on a fresh book of this arm "
        "over the day up to its {}th book operation, so that no timed pass faults in its own "
        "instructions; a heap arm's heap has seen only those books' nodes before the "
        "throughput pass",
        name, to_string(design.allocator), aged, nodes.orders, nodes.levels, to_string(nodes.pages),
        kCodeWarmOperations);
  }
  if (design.kind == DesignKind::map_aged) {
    const itch::HeapAgeing ageing;
    return std::format(
        "{}: MapBook on a heap aged by HeapAger: seed {:#x}, {} warm blocks, a ring of {}, one "
        "step before each A, F and U, outside the timing{}",
        name, ageing.seed, ageing.warm_blocks, ageing.ring_blocks, kMapWarmUp);
  }
  const std::string index =
      design.arm.index == itch::tools::IndexArm::hash
          ? std::format("HashIndex for {} orders", capacity.orders)
          : std::format("PagedIndex<8>, {} pages of 256, reach 2^{}, a fallback hash for {} orders",
                        capacity.index_pages, capacity.index_bits, capacity.orders);
  const std::string levels = design.arm.levels == itch::tools::LevelsArm::linked
                                 ? std::string{"LinkedLevels"}
                                 : std::format("FlatLevels, {} slots", capacity.level_slots);
  return std::format("{}: FastBook, {}; {}; {} orders and {} levels", name, index, levels,
                     capacity.orders, capacity.levels);
}

template <class Session>
void run_throughput(benchmark::State& state, Session* session, const TimerInfo* timer) {
  for (const auto iteration : state) {
    static_cast<void>(iteration);
    if (!session->timed_throughput()) {
      state.SkipWithError("the run was refused");
      return;
    }
    const PassTotals pass = session->report().throughput.value_or(PassTotals{});
    const double seconds = timer->ticks_to_ns(static_cast<double>(pass.ticks)) / 1e9;
    state.SetIterationTime(seconds);
    state.counters["msg_per_s"] = static_cast<double>(pass.messages) / seconds;
  }
}

template <class Session>
void run_latency(benchmark::State& state, Session* session, const TimerInfo* timer,
                 SampleBuffer* samples) {
  for (const auto iteration : state) {
    static_cast<void>(iteration);
    if (!session->timed_latency(*samples)) {
      state.SkipWithError("the run was refused");
      return;
    }
    const PassTotals pass = session->report().latency.value_or(PassTotals{});
    state.SetIterationTime(timer->ticks_to_ns(static_cast<double>(pass.ticks)) / 1e9);
  }
}

constexpr std::array kPercentiles{50.0, 90.0, 99.0, 99.9, 99.99};

std::vector<Percentile> percentiles_of(const Histogram& histogram, const TimerInfo& timer) {
  std::vector<Percentile> out;
  out.reserve(kPercentiles.size());
  for (const double percentile : kPercentiles) {
    out.push_back(classify_percentile(percentile, histogram.at(percentile), timer));
  }
  return out;
}

LatencyRecord summarise(const SampleBuffer& samples, const TimerInfo& timer) {
  LatencyRecord record;
  record.samples = samples.samples().size();
  if (record.samples == 0) {
    return record;
  }
  Histogram histogram;
  std::array<Histogram, kOpClasses> by_op;
  std::array<std::uint64_t, kOpClasses> op_sums{};
  std::array<std::uint64_t, kOpClasses> op_counts{};
  std::uint64_t sum = 0;
  const std::span<const std::uint32_t> ticks = samples.samples();
  const std::span<const OpClass> ops = samples.ops();
  for (std::size_t i = 0; i < ticks.size(); ++i) {
    const auto op = static_cast<std::size_t>(ops[i]);
    histogram.record(ticks[i]);
    by_op[op].record(ticks[i]);
    sum += ticks[i];
    op_sums[op] += ticks[i];
    ++op_counts[op];
  }
  record.percentiles = percentiles_of(histogram, timer);
  record.max_ns = classify_percentile(100.0, histogram.highest(), timer).value_ns;
  record.mean_ns =
      timer.ticks_to_ns(static_cast<double>(sum) / static_cast<double>(record.samples));
  for (const OpClass op : {OpClass::add, OpClass::del, OpClass::replace, OpClass::reduce}) {
    const auto at = static_cast<std::size_t>(op);
    OpLatency entry{
        .op = kOpNames[at], .percentiles = {}, .mean_ns = 0.0, .samples = op_counts[at]};
    if (entry.samples != 0) {
      entry.percentiles = percentiles_of(by_op[at], timer);
      entry.mean_ns =
          timer.ticks_to_ns(static_cast<double>(op_sums[at]) / static_cast<double>(entry.samples));
    }
    record.operations.push_back(std::move(entry));
  }
  return record;
}

// Per-message counts on one histogram for each event.
class CountSink {
 public:
  explicit CountSink(std::size_t events) : histograms_(events), sums_(events, 0) {}
  void push(std::size_t event, std::uint64_t count) noexcept {
    histograms_[event].record(count);
    sums_[event] += count;
  }
  [[nodiscard]] std::vector<PerMessageCount> summary(std::span<const std::string> names,
                                                     std::uint64_t messages) const {
    std::vector<PerMessageCount> out;
    for (std::size_t event = 0; event < names.size() && messages != 0; ++event) {
      out.push_back({.name = names[event],
                     .mean = static_cast<double>(sums_[event]) / static_cast<double>(messages),
                     .p50 = static_cast<std::uint64_t>(histograms_[event].at(50.0)),
                     .p99 = static_cast<std::uint64_t>(histograms_[event].at(99.0)),
                     .sum = sums_[event]});
    }
    return out;
  }

 private:
  std::vector<Histogram> histograms_;
  std::vector<std::uint64_t> sums_;
};

// Untimed, and apart from the timing passes. Hardware events never fail a run. The
// memory baseline is read before the book is built, so the book's own allocations count.
template <class Book, class MakeBook>
CounterRecord counter_pass(std::span<const std::byte> input, const MakeBook& make_book,
                           CounterGroup& group, std::uint64_t messages, MemoryRecord& memory,
                           bool first_book) {
  CountSink sink{group.event_count()};
  memory.first_book = first_book;
  MemoryHighWater high_water{[] { return charged_bytes(); }};
  const std::unique_ptr<Book> book = make_book();
  high_water.sample();
  memory.built_bytes = high_water.peak();
  CounterRecord record;
  std::uint64_t missed = 0;
  group.start();
  const Agreement agreement =
      cross_check(group, [&] { missed = counts_pass(input, *book, group, sink, high_water); });
  group.stop();
  memory.source = std::string{kChargedSource};
  memory.complete = high_water.complete();
  memory.baseline_bytes = high_water.baseline();
  memory.peak_bytes = high_water.peak();
  memory.book_bytes = high_water.growth();
  memory.samples = high_water.samples();
  if constexpr (requires { book->ager_held_bytes(); }) {
    memory.ager_held_bytes = book->ager_held_bytes();
  }
  record.statuses = group.statuses();
  record.agreement_names = group.event_names();
  record.agreement_events = agreement.events;
  record.agreement = group.event_count() == 0 ? unavailable("no user-mode rdpmc on this host")
                                              : describe_agreement(agreement);
  record.per_message_published = agreement.state == AgreementState::agreed && missed == 0;
  if (record.per_message_published) {
    record.per_message = sink.summary(group.event_names(), messages);
  }
  return record;
}

std::string core_field(const PinOutcome& pin, const MigrationTracker& migrations) {
  if (pin.pinned) {
    return std::format("{}", pin.requested_core);
  }
  if (migrations.last_core() < 0) {
    return unavailable("the current core could not be read");
  }
  return std::format("unpinned, last ran on core {}", migrations.last_core());
}

// The warm-up of a map design: the fast book's hash index with linked levels, which takes no
// small blocks. It is named rather than taken as the default, so that every map run warms up
// on the same arm whichever arm ships.
PassTotals warm_with_fast_book(std::span<const std::byte> bytes) {
  const auto book = std::make_unique<
      itch::FastBook<itch::NoWriteObserver, itch::HashIndex, itch::LinkedLevels>>();
  PlatformClock clock;
  return throughput_pass(bytes, *book, clock);
}

// The timed passes through Google Benchmark: the throughput pass, then the latency pass
// unless the process was asked for throughput alone.
template <class Session>
void run_timed_passes(Session& session, const TimerInfo& timer, SampleBuffer& samples,
                      bool latency) {
  benchmark::RegisterBenchmark("replay/throughput", run_throughput<Session>, &session, &timer)
      ->UseManualTime()
      ->Iterations(1)
      ->Unit(benchmark::kMillisecond);
  if (latency) {
    benchmark::RegisterBenchmark("replay/latency", run_latency<Session>, &session, &timer, &samples)
        ->UseManualTime()
        ->Iterations(1)
        ->Unit(benchmark::kMillisecond);
  }
  benchmark::RunSpecifiedBenchmarks();
}

// One process's run of one design, from the warm-up to the record.
template <class Book, class MakeBook>
int run_design(const Options& options, const OwnedInput& owned, const MakeBook& make_book,
               const std::vector<std::string>* pages = nullptr) {
  using Session = ReplaySession<ReplayInput, PlatformClock, Book>;
  const bool timed = options.passes != Passes::untimed;
  const bool latency = options.passes == Passes::all || options.passes == Passes::timed;
  const bool untimed = options.passes == Passes::all || options.passes == Passes::untimed;
  const SysSource source = live_source();
  const std::string_view host = host_label(source.os, read_cpu_model(source));
  const PinOutcome pin = pin_current_thread(options.core);
  const TimerInfo timer = measure_timer();
  MigrationTracker migrations;
  migrations.sample();

  ReplayInput input{owned.bytes()};
  PlatformClock clock;
  Session session{input, clock, make_book, options.track_top};
  if (kPinningRequired && !pin.pinned) {
    session.refuse(pinning_field(pin));
  }
  if constexpr (!std::is_constructible_v<Book, itch::BookCapacity>) {
    session.warm_with("hash-linked", warm_with_fast_book);
  }
  if (timed) {
    session.warm_up();
    if (options.design.kind == DesignKind::alloc) {
      session.warm_code(kCodeWarmOperations);
    }
  } else {
    session.warm_up_without_book();
  }
  migrations.sample();

  const std::uint64_t messages = session.report().warmup_messages;
  const std::optional<Arena> arena =
      latency ? Arena::make(messages * SampleBuffer::kBytesPerSample) : std::nullopt;
  if (latency && !arena) {
    session.refuse("no arena for the latency samples");
  }
  SampleBuffer samples{arena ? std::span<std::byte>{arena->data(), arena->size()}
                             : std::span<std::byte>{}};

  if (timed) {
    run_timed_passes(session, timer, samples, latency);
  }
  benchmark::Shutdown();
  migrations.sample();

  CounterGroup group{host};
  RunRecord record;
  if (session.report().publishable()) {
    if (latency) {
      record.latency = summarise(samples, timer);
    }
    if (untimed) {
      record.counters =
          counter_pass<Book>(input.bytes(), make_book, group, messages, record.memory, !timed);
    } else {
      record.counters.agreement = "unavailable: no untimed pass in this process";
    }
  } else {
    record.counters.agreement = "unavailable: the run was refused";
  }
  migrations.sample();

  record.session = session.report();
  record.quantum_ns = timer.quantum_ns();
  record.pair_ns = timer.pair_ns();
  record.floor_ns = timer.floor_ns();
  record.calibrated_hz = timer.calibrated_hz;
  record.peak_rss_bytes = peak_rss_bytes();
  record.migrations = migrations.migrations();
  record.pinned = pin.pinned;
  record.header = build_header(
      source,
      {.compiler = __VERSION__,
       .flags = ITCH_BENCH_FLAGS,
       .commit = options.commit,
       .dataset = options.dataset,
       .dataset_sha256 = options.dataset_sha256,
       .timer = describe_rate(timer),
       .quantum = std::format("{} ticks, {:.3f} ns", timer.quantum_ticks, timer.quantum_ns()),
       .pair = std::format("{:.3f} ticks, {:.3f} ns, subtracted only where a column says so",
                           timer.pair_ticks, timer.pair_ns()),
       .floor = std::format("{:.3f} ns", timer.floor_ns()),
       .pinning = pinning_field(pin),
       .migrations = migrations.describe(),
       .core = core_field(pin, migrations),
       .core_number = options.core,
       .counters = describe_counters(record.counters.statuses),
       .rdpmc = record.counters.agreement,
       .page_size = arena ? describe_arena(arena->report())
                          : unavailable("no sample arena in a process with no latency pass"),
       .residency = session.report().residency});
  record.header.emplace_back("design", scrub_private(describe_design(options)));
  if (pages != nullptr) {
    std::string obtained = pages->empty() ? std::string{"no pool built"} : std::string{};
    for (std::size_t i = 0; i < pages->size(); ++i) {
      obtained += std::format("{}book {}: {}", i == 0 ? "" : " | ", i + 1, (*pages)[i]);
    }
    record.header.emplace_back("pool_pages", scrub_private(obtained));
  }
  if (options.design.kind == DesignKind::alloc) {
    record.header.emplace_back("node_capacity",
                               std::format("{} orders and {} levels", options.node_capacity.orders,
                                           options.node_capacity.levels));
  }
  record.header.emplace_back("input", scrub_private(owned.describe()));
  record.header.emplace_back(
      "top_of_book",
      options.track_top
          ? "tracked: after every message the book applied, the best bid and ask on its locate, "
            "price and shares, against the last seen there; inside the throughput pass's timing, "
            "outside each latency span"
          : "not tracked");

  std::ofstream out{options.out};
  out << to_json(record, timer);
  if (!out) {
    std::println(stderr, "bench_replay: cannot write {}", options.out);
    return 2;
  }
  for (const std::string& refusal : session.report().refusals) {
    std::println(stderr, "bench_replay: refused: {}", refusal);
  }
  return session.report().publishable() ? 0 : 3;
}

// The node book on one allocator arm. Every book built, the warm-up's aside, notes what
// its pools obtained, so that each pass's pages are on the record, not the first one's.
int run_alloc_design(const Options& options, const OwnedInput& owned) {
  return itch::tools::with_alloc_arm(
      options.design.allocator,
      [&]<class Book> {
        std::vector<std::string> pages;
        const auto make = [&options, &pages] {
          auto book = std::make_unique<Book>(options.node_capacity);
          const itch::PageReport* report = itch::tools::node_book_of(*book).heap().pages();
          if (report != nullptr) {
            pages.push_back(report->describe());
          }
          return book;
        };
        const bool pool = itch::tools::is_pool_arm(options.design.allocator);
        return run_design<Book>(options, owned, make, pool ? &pages : nullptr);
      },
      [] {
        std::println(stderr, "bench_replay: this build has no mimalloc (ITCH_MIMALLOC off)");
        return 2;
      });
}

int run(int argc, char** argv) {
  benchmark::Initialize(&argc, argv);
  Options options;
  if (!parse(std::span{argv, static_cast<std::size_t>(argc)}.subspan(1), options)) {
    std::println(stderr,
                 "usage: bench_replay --input FILE --out RUN.json [--design NAME] "
                 "[--input-mode ram|mapped] [--track-top on|off] "
                 "[--passes all|timed|throughput|untimed] "
                 "[--core N] [--commit TEXT] [--dataset NAME] [--dataset-sha256 HEX] "
                 "[--orders N] [--levels N] "
                 "[--pages base|huge-if-offered|transparent|explicit]");
    return 2;
  }
  const std::optional<OwnedInput> owned = OwnedInput::open(options);
  if (!owned) {
    std::println(stderr, "bench_replay: cannot read {}", options.input);
    return 2;
  }
  switch (options.design.kind) {
    case DesignKind::map_fresh:
      return run_design<itch::MapBook>(options, *owned,
                                       [] { return std::make_unique<itch::MapBook>(); });
    case DesignKind::map_aged:
      return run_design<AgedMapBook>(options, *owned,
                                     [] { return std::make_unique<AgedMapBook>(); });
    case DesignKind::alloc: return run_alloc_design(options, *owned);
    case DesignKind::fast: break;
  }
  return itch::tools::with_book_arm(options.design.arm, [&]<class Index, class Levels> {
    using Book = itch::FastBook<itch::NoWriteObserver, Index, Levels>;
    return run_design<Book>(options, *owned,
                            [&] { return std::make_unique<Book>(options.capacity); });
  });
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
