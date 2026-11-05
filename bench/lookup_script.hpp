// The order lookup measured apart from everything else the book does with a message.
//
// ScriptedIndex wraps an index arm as FastBook's OrderIndex policy, so the book around it
// is the book as it ships, level work and all. Every insert and erase goes to the real
// index, so the index holds what it would at every message. Only find() changes, by mode:
//
//   record  the real find, its answer appended to the script;
//   check   the real find, then the script's next entry read and compared with it;
//   answer  the script's next entry, and no find.
//
// A check pass and an answer pass over the same day do the same work but the lookups:
// the same frames, the same level work, the same inserts and erases, the same order
// records read after each lookup, and the same sequential read of the script. So the
// difference in their time and counts is what the finds cost the book where they sit,
// with the caches the rest of the message leaves them. It is the index's own loads, the
// hash's slots or the paged index's directory and page slot, and the hash's reading of
// the order record to confirm its key, which the answer pass then makes itself when it
// first reads the order. The order record's own line is in both passes.
//
// A check pass also proves the script: any entry that differs from the real find is
// counted, and a run with one is refused. Nothing here is production code.
#pragma once

#include <cstddef>
#include <cstdint>
#include <span>

#include "itch/book_types.hpp"

namespace itch::bench {

enum class ScriptMode : std::uint8_t { record, check, answer };

// The answers of every find() on one day, in order, in memory the caller owns.
class LookupScript {
 public:
  explicit LookupScript(std::span<std::uint32_t> slots) noexcept : slots_{slots} {}

  // Back to the first entry, for another pass over the same day.
  void rewind() noexcept {
    cursor_ = 0;
    mismatches_ = 0;
    overrun_ = false;
  }
  // Recording again from the first entry.
  void clear() noexcept {
    rewind();
    recorded_ = 0;
  }

  void push(std::uint32_t slot) noexcept {
    if (recorded_ < slots_.size()) {
      slots_[recorded_] = slot;
      ++recorded_;
    } else {
      overrun_ = true;
    }
  }

  [[nodiscard]] std::uint32_t next() noexcept {
    if (cursor_ < recorded_) {
      return slots_[cursor_++];
    }
    overrun_ = true;
    return kNoIndex;
  }

  void note(std::uint32_t expected, std::uint32_t found) noexcept {
    mismatches_ += expected == found ? 0U : 1U;
  }

  [[nodiscard]] std::uint64_t recorded() const noexcept { return recorded_; }
  [[nodiscard]] std::uint64_t read() const noexcept { return cursor_; }
  [[nodiscard]] std::uint64_t mismatches() const noexcept { return mismatches_; }
  // A find past the end of the script, or a record past the end of its memory.
  [[nodiscard]] bool overrun() const noexcept { return overrun_; }
  // The pass read every entry, matched each, and ran past none.
  [[nodiscard]] bool consumed_exactly() const noexcept {
    return !overrun_ && mismatches_ == 0 && cursor_ == recorded_;
  }

 private:
  std::span<std::uint32_t> slots_;
  std::uint64_t recorded_ = 0;
  std::uint64_t cursor_ = 0;
  std::uint64_t mismatches_ = 0;
  bool overrun_ = false;
};

// The script every ScriptedIndex reads. A book builds its index itself from BookCapacity,
// so the script cannot reach it through the constructor; the pass sets this before it
// builds the book and clears it after.
[[nodiscard]] inline LookupScript*& current_script() noexcept {
  static LookupScript* script = nullptr;
  return script;
}

template <class Index, ScriptMode Mode>
class ScriptedIndex {
 public:
  static constexpr unsigned kPrefetchStages = Index::kPrefetchStages;

  explicit ScriptedIndex(const BookCapacity& capacity) : index_{capacity} {}

  [[nodiscard]] std::uint32_t find(std::uint64_t reference,
                                   std::span<const BookOrder> orders) const noexcept {
    LookupScript& script = *current_script();
    if constexpr (Mode == ScriptMode::answer) {
      return script.next();
    } else if constexpr (Mode == ScriptMode::check) {
      const std::uint32_t found = index_.find(reference, orders);
      script.note(script.next(), found);
      return found;
    } else {
      const std::uint32_t found = index_.find(reference, orders);
      script.push(found);
      return found;
    }
  }

  void insert(std::uint64_t reference, std::uint32_t order,
              std::span<const BookOrder> orders) noexcept {
    index_.insert(reference, order, orders);
  }
  void erase(std::uint64_t reference, std::span<const BookOrder> orders) noexcept {
    index_.erase(reference, orders);
  }

  void prefetch_stage(std::uint64_t reference, unsigned stage) const noexcept {
    index_.prefetch_stage(reference, stage);
  }
  [[nodiscard]] std::uint32_t peek(std::uint64_t reference) const noexcept {
    return index_.peek(reference);
  }

  [[nodiscard]] const Index& wrapped() const noexcept { return index_; }

  friend bool operator==(const ScriptedIndex&, const ScriptedIndex&) = default;

 private:
  Index index_;
};

}  // namespace itch::bench
