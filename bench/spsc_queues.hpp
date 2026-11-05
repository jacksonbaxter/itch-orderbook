// Every queue bench_spsc compares, behind one face, so that each protocol is one template
// run on each of them with the same element type and the same requested capacity.
//
//     Queue<T> q{capacity};  q.try_push(value) -> bool;  q.try_pop(out) -> bool
//
// The project's ring and a mutex ring, the floor, are always here. The third-party queues
// are here when the build fetched them (-DITCH_COMPARISON_QUEUES=ON), each used through
// its own non-blocking single-item calls as shipped, with its own padding: rigtorp and
// folly take std::hardware_destructive_interference_size, boost and atomic_queue pad to
// 128 on Apple arm64 and 64 elsewhere, and moodycamel pads to 64 everywhere (LC-22).
// xenium's Vyukov queue is multi-producer multi-consumer, a control with a
// compare-and-swap on every call (LC-8, LC-9).
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string_view>
#include <thread>
#include <vector>

#include "itch/spsc.hpp"

#ifdef ITCH_HAVE_COMPARISON_QUEUES
#include <atomic_queue/atomic_queue.h>
#include <folly/ProducerConsumerQueue.h>
#include <readerwriterqueue.h>
#include <rigtorp/SPSCQueue.h>

#include <boost/lockfree/spsc_queue.hpp>
#include <xenium/vyukov_bounded_queue.hpp>
#endif

namespace itch::bench {

// The project's ring in any of its configurations, single calls only.
template <class T, SpscConfig Config = SpscConfig{}>
class ProjectQueue {
 public:
  explicit ProjectQueue(std::size_t capacity) : ring_{capacity} {}
  [[nodiscard]] bool try_push(const T& value) noexcept { return ring_.try_push(value); }
  [[nodiscard]] bool try_pop(T& out) noexcept { return ring_.try_pop(out); }
  [[nodiscard]] SpscRing<T, Config>& ring() noexcept { return ring_; }

 private:
  SpscRing<T, Config> ring_;
};

// A ring under one std::mutex: the floor every lock-free queue should clear. A call that
// finds the ring full or empty yields before it returns, as a blocking queue would sleep:
// without that, a thread spinning on an empty ring retakes the lock as soon as it lets it
// go, and on macOS a round trip took 2.2 ms (NOTES.md, "The SPSC ring").
template <class T>
class MutexQueue {
 public:
  explicit MutexQueue(std::size_t capacity) : slots_(capacity == 0 ? 1 : capacity) {}
  [[nodiscard]] bool try_push(const T& value) {
    {
      const std::scoped_lock lock{mutex_};
      if (tail_ - head_ != slots_.size()) {
        slots_[tail_ % slots_.size()] = value;
        ++tail_;
        return true;
      }
    }
    std::this_thread::yield();
    return false;
  }
  [[nodiscard]] bool try_pop(T& out) {
    {
      const std::scoped_lock lock{mutex_};
      if (tail_ != head_) {
        out = slots_[head_ % slots_.size()];
        ++head_;
        return true;
      }
    }
    std::this_thread::yield();
    return false;
  }

 private:
  std::mutex mutex_;
  std::vector<T> slots_;
  std::uint64_t head_ = 0;
  std::uint64_t tail_ = 0;
};

#ifdef ITCH_HAVE_COMPARISON_QUEUES

template <class T>
class RigtorpQueue {
 public:
  explicit RigtorpQueue(std::size_t capacity) : queue_{capacity} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.try_push(value); }
  [[nodiscard]] bool try_pop(T& out) {
    T* const front = queue_.front();
    if (front == nullptr) {
      return false;
    }
    out = *front;
    queue_.pop();
    return true;
  }

 private:
  rigtorp::SPSCQueue<T> queue_;
};

template <class T>
class BoostQueue {
 public:
  explicit BoostQueue(std::size_t capacity) : queue_{capacity} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.push(value); }
  [[nodiscard]] bool try_pop(T& out) { return queue_.pop(out); }

 private:
  boost::lockfree::spsc_queue<T> queue_;
};

// try_enqueue never allocates: it fails when the blocks made at construction are full.
template <class T>
class MoodycamelQueue {
 public:
  explicit MoodycamelQueue(std::size_t capacity) : queue_{capacity} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.try_enqueue(value); }
  [[nodiscard]] bool try_pop(T& out) { return queue_.try_dequeue(out); }

 private:
  moodycamel::ReaderWriterQueue<T> queue_;
};

template <class T>
class FollyQueue {
 public:
  explicit FollyQueue(std::size_t capacity) : queue_{static_cast<std::uint32_t>(capacity)} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.write(value); }
  [[nodiscard]] bool try_pop(T& out) { return queue_.read(out); }

 private:
  folly::ProducerConsumerQueue<T> queue_;
};

// AtomicQueueB2: a capacity set at run time, any element type, and its SPSC mode on.
// MAXIMIZE_THROUGHPUT on and TOTAL_ORDER off are its defaults.
template <class T>
class AtomicQueue {
 public:
  explicit AtomicQueue(std::size_t capacity) : queue_{static_cast<unsigned>(capacity)} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.try_push(T{value}); }
  [[nodiscard]] bool try_pop(T& out) { return queue_.try_pop(out); }

 private:
  atomic_queue::AtomicQueueB2<T, std::allocator<T>, true, false, true> queue_;
};

template <class T>
class XeniumVyukovQueue {
 public:
  explicit XeniumVyukovQueue(std::size_t capacity) : queue_{capacity} {}
  [[nodiscard]] bool try_push(const T& value) { return queue_.try_push(value); }
  [[nodiscard]] bool try_pop(T& out) { return queue_.try_pop(out); }

 private:
  xenium::vyukov_bounded_queue<T> queue_;
};

#endif

// The comparison's queues by name, in the order the results file lists them.
inline constexpr std::array<std::string_view, 8> kQueueNames{
    "project", "rigtorp", "boost", "moodycamel", "folly", "atomic_queue", "xenium_vyukov", "mutex"};

#ifdef ITCH_HAVE_COMPARISON_QUEUES
inline constexpr bool kHaveComparisonQueues = true;
#else
inline constexpr bool kHaveComparisonQueues = false;
#endif

// Calls `run` with a null pointer of the named queue's type over T, so that the caller
// builds it; false for a name this build does not have.
template <class T, class Run>
bool with_queue(std::string_view name, const Run& run) {
  if (name == "project") {
    run(static_cast<ProjectQueue<T>*>(nullptr));
    return true;
  }
  if (name == "mutex") {
    run(static_cast<MutexQueue<T>*>(nullptr));
    return true;
  }
#ifdef ITCH_HAVE_COMPARISON_QUEUES
  if (name == "rigtorp") {
    run(static_cast<RigtorpQueue<T>*>(nullptr));
    return true;
  }
  if (name == "boost") {
    run(static_cast<BoostQueue<T>*>(nullptr));
    return true;
  }
  if (name == "moodycamel") {
    run(static_cast<MoodycamelQueue<T>*>(nullptr));
    return true;
  }
  if (name == "folly") {
    run(static_cast<FollyQueue<T>*>(nullptr));
    return true;
  }
  if (name == "atomic_queue") {
    run(static_cast<AtomicQueue<T>*>(nullptr));
    return true;
  }
  if (name == "xenium_vyukov") {
    run(static_cast<XeniumVyukovQueue<T>*>(nullptr));
    return true;
  }
#endif
  return false;
}

}  // namespace itch::bench
