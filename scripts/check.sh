#!/usr/bin/env bash
# Runs every local check, in sequence, and prints a summary of the stages.
#
#     scripts/check.sh              every stage
#     scripts/check.sh --quick      the stages a change should pass before a commit
#     scripts/check.sh --list       the stages, and which are quick
#     scripts/check.sh --only NAME  one stage, repeatable
#
# Needs CC and CXX, or a compiler on PATH, plus cmake, ninja, python3, clang-format and
# clang-tidy. The wasm and demo stages need EMSDK, and the demo stage opens the page in the
# headless browser ITCH_BROWSER names: a Chrome or Chromium binary, such as the Chrome for
# Testing that `python3 -m playwright install chromium` fetches. Without one the demo stage
# fails, unless --without-browser is given, when it runs the rest and the summary says
# "partial: the page was not opened". It never reads data/: real-day checks live in
# scripts/check_real_day.sh.
#
#     scripts/check.sh --without-browser   the demo stage without the page, said so
#
# Stages run one at a time and a build uses at most ITCH_CHECK_JOBS jobs, so that no two
# sanitizer or clang-tidy builds ever overlap. A failed stage does not stop the rest.
# The exit status is non-zero if any stage failed. Each stage's output is kept in
# build/check-logs/<stage>.log. A stage that did only part of its work prints a line
# `partial: <what it left out>`, and the summary carries that line in place of a bare ok.
#
# To add a stage: write a function named stage_<name>, then add one `register` line.

set -u -o pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root" || exit 2

jobs=${ITCH_CHECK_JOBS:-6}
log_dir=build/check-logs
ctest_preset=release
without_browser=0

python_test() { python3 -B "$@"; }

build_preset() {
  cmake --preset "$1" && cmake --build --preset "$1" --parallel "$jobs"
}

# A preset's build and its whole ctest suite.
preset_with_tests() {
  build_preset "$1" && ctest --preset "$1"
}

# The tests ctest knows, one a line: ctest_preset's, or the build directory's it runs in when
# ctest_preset is empty.
ctest_names() {
  ctest ${ctest_preset:+--preset "$ctest_preset"} -N | sed -n 's/^ *Test *#[0-9]*: //p'
}

# Fails, naming each, when a test a stage names is not one ctest knows: a regex that names
# several still matches when one is renamed or removed, so long as another is left.
ctest_listed() { # name...
  local known name missing=0
  known=$(ctest_names) || return 1
  for name in "$@"; do
    case $'\n'"$known"$'\n' in
      *$'\n'"$name"$'\n'*) ;;
      *) echo "ctest knows no test named $name"; missing=1 ;;
    esac
  done
  [ "$missing" -eq 0 ]
}

# The tests named and no others, once each is known to exist.
ctest_only() { # name...
  ctest_listed "$@" || return 1
  local IFS='|'
  ctest --preset "$ctest_preset" -R "^($*)\$"
}

# The results files each pattern matches, one a line. Fails, naming the pattern, when one
# matches none, so that a check over them cannot pass by reading nothing.
results_files() { # pattern...
  local pattern
  for pattern in "$@"; do
    compgen -G "results/$pattern" || { echo "no results file matches results/$pattern" >&2; return 1; }
  done
}

stage_toolchain() {
  for tool in cmake ninja python3 clang-format clang-tidy; do
    command -v "$tool" || { echo "missing tool: $tool"; return 1; }
  done
  local version
  # Each version is read whole and its first line printed: a pipe into head would hide a
  # compiler that is not there.
  version=$("${CXX:-c++}" --version) || { echo "the compiler ${CXX:-c++} does not run"; return 1; }
  echo "${version%%$'\n'*}"
  version=$(cmake --version) || return 1
  echo "${version%%$'\n'*}"
}

stage_format() {
  local files
  files=$(find include src tests tools fuzz bench -type f \( -name '*.cpp' -o -name '*.hpp' \)) || return 1
  [ -n "$files" ] || { echo "no C++ files found"; return 1; }
  # shellcheck disable=SC2086
  clang-format --dry-run -Werror $files
}

stage_gates() { python_test scripts/gates.py && python_test scripts/test_gates.py; }

stage_script_tests() {
  python_test scripts/test_fetch_data.py &&
    python_test scripts/test_check_census.py &&
    python_test scripts/test_oracle_report.py &&
    python_test scripts/test_oracle_sessions.py &&
    python_test scripts/test_trades_report.py &&
    python_test scripts/test_fault_matrix.py &&
    python_test scripts/test_fetch_tool.py &&
    python_test scripts/test_check_sh.py &&
    python_test scripts/test_check_benchmark_sources.py
}

# RESULTS.md's tables and the README's headline against a fresh run over results/, and the
# figures on both pages against those files, and T-ORACLE-2's sum of the oracle's results files
# against a fresh one.
stage_readme() {
  python_test scripts/oracle_sessions.py --check &&
    python_test scripts/results_table.py --check &&
    python_test scripts/test_results_table.py &&
    python_test scripts/gate_numbers.py --page README.md --page RESULTS.md --page WRITEUP.md
}

# Reads .github/workflows with the pinned actionlint, fetched into the build tree. No
# workflow has ever run on GitHub, and this does not run one.
stage_actionlint() { python_test scripts/lint_workflows.py; }

stage_corpus() {
  python_test scripts/gen_fuzz_corpus.py --check &&
    python_test scripts/gen_oracle_fixtures.py --check &&
    python_test scripts/gen_trade_fixtures.py --check
}
stage_release() { preset_with_tests release; }
stage_asan_ubsan() { preset_with_tests asan-ubsan; }
# The oracle on its fixtures, named apart from the rest of the suite: the book scenarios,
# the diff and invariants through the tool, the allocation test, the node book of T-ARENA-1
# on every allocator arm, and the book corpus.
stage_oracle() {
  build_preset release &&
    ctest_only test_book test_alloc test_node_book test_oracle_cli corpus_fuzz_book
}
# The trade-print checks on their fixtures: both reference parsers and their corpora, the
# faulty-book tests, and every check through the tool. Needs nothing from data/.
stage_trades() {
  build_preset release &&
    ctest_only test_reference_files test_trade_checks test_trades_cli corpus_fuzz_crossstats \
      corpus_fuzz_midas
}
# The benchmark harness: each check on its fixtures, the gates on the committed results,
# and the tests of the harness itself. Needs nothing from data/.
stage_bench() {
  local results
  python_test scripts/test_machine_check.py &&
    python_test scripts/test_gate_numbers.py &&
    python_test scripts/test_check_predictions.py &&
    python_test scripts/test_bench_results.py &&
    python_test scripts/test_backtest_results.py &&
    python_test scripts/test_linux_sampler.py &&
    python_test scripts/test_spsc_results.py &&
    python_test scripts/test_alloc_results.py &&
    python_test scripts/test_chase_report.py &&
    python_test scripts/test_counts_report.py &&
    python_test scripts/test_flame_report.py &&
    python_test scripts/test_variants_report.py &&
    python_test scripts/test_types_report.py &&
    python_test scripts/test_window_report.py &&
    python_test scripts/test_simd_report.py &&
    python_test scripts/test_simd_driver.py &&
    python_test scripts/test_udp_report.py &&
    python_test scripts/test_udp_driver.py &&
    python_test scripts/test_walk_report.py &&
    python_test scripts/test_simd_asm_audit.py &&
    python_test scripts/test_zen1_events.py &&
    python_test scripts/gate_numbers.py &&
    python_test scripts/check_predictions.py &&
    python_test scripts/check_results.py --bars BENCHMARKS.md || return 1
  # Each pattern must match a file, or the check it feeds has read nothing.
  local file
  results=$(results_files 'bench_*.md' 'backtest_*.md' 'spsc_*.md' 'alloc_*.md' \
    'walks_ladder_*.md') || return 1
  # shellcheck disable=SC2086
  python_test scripts/check_results.py $results || return 1
  results=$(results_files chase_debian.md) || return 1
  python_test scripts/chase_report.py --check $results || return 1
  results=$(results_files 'counters_by_type_*.md' 'counters_by_age_*.md') || return 1
  for file in $results; do
    python_test scripts/counts_report.py --check "$file" || return 1
  done
  results=$(results_files counters_zen1_events_debian.md) || return 1
  python_test scripts/zen1_events.py --check $results || return 1
  results=$(results_files 'flamegraph_*.md' 'profile_*.md') || return 1
  for file in $results; do
    python_test scripts/flame_report.py --check "$file" || return 1
  done
  results=$(results_files 'lookup_*.md' 'prefetch_*.md' 'dispatch_*.md' 'layout_*.md' \
    'structure_*.md') || return 1
  for file in $results; do
    python_test scripts/variants_report.py --check "$file" || return 1
  done
  # No types_ file is published yet, so this pattern alone may match none; one that is
  # published is checked.
  for file in results/types_*.md; do
    [ -f "$file" ] || continue
    python_test scripts/types_report.py --check "$file" || return 1
  done
  results=$(results_files 'window_*.md') || return 1
  for file in $results; do
    python_test scripts/window_report.py --check "$file" || return 1
  done
  results_files 'simd_asm_*.md' >/dev/null || return 1
  results=$(results_files 'simd_*.md') || return 1
  for file in $results; do
    case $file in
      results/simd_asm_*) python_test scripts/simd_asm_audit.py --check "$file" || return 1 ;;
      *) python_test scripts/simd_report.py --check "$file" || return 1 ;;
    esac
  done
  results=$(results_files 'udp_*.md') || return 1
  for file in $results; do
    python_test scripts/udp_report.py --check "$file" || return 1
  done
  results=$(results_files 'walks_*.md') || return 1
  for file in $results; do
    python_test scripts/walk_report.py check "$file" || return 1
  done
  build_preset release &&
    ctest_only test_bench_platform test_bench_replay test_bench_cli test_bench_backtest_cli \
      test_bench_counts_cli test_bench_variants_cli test_bench_spsc_cli test_bench_kernels_cli \
      test_bench_udp_cli test_bench_walks_cli test_walk_counts
}
# The market maker: T-MM-2's report on its fixtures and its results file against a rerun; the
# report day's report on its fixtures and its results file against the tool output it carries;
# then the attribution, model, allocation, calibration, selection and run-tool tests. Needs
# nothing from data/.
stage_market_maker() {
  python_test scripts/test_as_tables_report.py &&
    python_test scripts/test_mm_report.py &&
    python_test scripts/mm_report.py check &&
    build_preset release &&
    python_test scripts/as_tables_report.py check --tool build/release/tools/itch_as_tables &&
    ctest_only test_attribution test_market_maker test_alloc test_calibrate_cli test_select_cli \
      test_mm_cli
}
stage_tsan() { preset_with_tests tsan; }
# From clean: ninja does not track .clang-tidy, so an incremental run could be a stale pass.
stage_tidy() {
  cmake --preset tidy && cmake --build --preset tidy --parallel "$jobs" --clean-first
}
stage_fuzz_smoke() { build_preset fuzz && ctest --preset fuzz-smoke; }
stage_wasm() { preset_with_tests wasm; }
# The browser demo: its scripts' tests, docs/numbers.json against results/, T-WASM-1's results
# file against itself, the replay natively, then the engine in node against the native build on
# an extract cut at test time, and the page in the headless browser ITCH_BROWSER names. With no
# browser it fails before it builds, unless --without-browser was given: then it does the rest
# and says it did not open the page. Needs EMSDK, and nothing from data/.
stage_demo() {
  local browser=()
  if [ -n "${ITCH_BROWSER:-}" ]; then
    [ -x "$ITCH_BROWSER" ] || { echo "demo: ITCH_BROWSER names no program that runs: $ITCH_BROWSER"; return 1; }
    browser=(--browser "$ITCH_BROWSER")
  elif [ "$without_browser" -eq 0 ]; then
    echo "demo: no browser to open the page in. Set ITCH_BROWSER to a Chrome or Chromium binary,"
    echo "such as the Chrome for Testing that \`python3 -m playwright install chromium\` fetches"
    echo "(on macOS, .../Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing),"
    echo "or pass --without-browser to run the rest of the stage and have the summary say so."
    return 1
  fi
  python_test scripts/test_demo.py &&
    python_test scripts/demo.py numbers --check &&
    python_test scripts/wasm_report.py check &&
    build_preset release &&
    ctest_only test_demo_replay test_demo_cli test_alloc &&
    build_preset wasm &&
    python_test scripts/test_demo_engine.py --release build/release --wasm build/wasm \
      ${browser[@]+"${browser[@]}"} || return 1
  if [ ${#browser[@]} -eq 0 ]; then
    echo "partial: the page was not opened"
  fi
}
stage_fault_matrix() {
  python_test scripts/fault_matrix.py --log-dir "$log_dir/faults"
}

stage_names=""
quick_names=""
register() { # name [quick]
  stage_names="$stage_names $1"
  if [ "${2:-}" = quick ]; then
    quick_names="$quick_names $1"
  fi
}

register toolchain quick
register format quick
register gates quick
register actionlint quick
register readme quick
register script_tests quick
register corpus quick
register release quick
register oracle quick
register trades quick
register bench quick
register market_maker quick
register asan_ubsan quick
register tidy
register tsan
register fuzz_smoke
register wasm
register demo
register fault_matrix

selected=$stage_names
only=""
while [ $# -gt 0 ]; do
  case $1 in
    --quick) selected=$quick_names ;;
    --only)
      [ $# -ge 2 ] || { echo "--only needs a stage name" >&2; exit 2; }
      case " $stage_names " in
        *" $2 "*) only="$only $2" ;;
        *) echo "no stage named $2" >&2; exit 2 ;;
      esac
      shift
      ;;
    --without-browser) without_browser=1 ;;
    --list)
      for name in $stage_names; do
        case " $quick_names " in
          *" $name "*) echo "$name (quick)" ;;
          *) echo "$name" ;;
        esac
      done
      exit 0
      ;;
    *) echo "usage: scripts/check.sh [--quick] [--list] [--only NAME]... [--without-browser]" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$only" ]; then
  selected=$only
fi

mkdir -p "$log_dir"
summary=""
failed=0
partial=""
for name in $selected; do
  log=$log_dir/$name.log
  printf '%-14s ... ' "$name"
  started=$SECONDS
  # A stage is judged by its function's exit status, and its output goes to the log.
  if "stage_$name" >"$log" 2>&1; then
    verdict=ok
  else
    verdict=FAIL
    failed=$((failed + 1))
  fi
  elapsed=$((SECONDS - started))
  # A stage that passed but left part of its work undone says what, and is not a bare ok.
  note=""
  if [ "$verdict" = ok ]; then
    note=$(grep -m 1 '^partial: ' "$log") || note=""
  fi
  if [ -n "$note" ]; then
    verdict=partial
    partial="$partial $name"
  fi
  printf '%s (%d s)%s\n' "$verdict" "$elapsed" "${note:+, $note}"
  if [ "$verdict" = FAIL ]; then
    tail -n 20 "$log"
  fi
  summary="$summary$(printf '%-14s %-7s %5d s  %s%s' "$name" "$verdict" "$elapsed" "$log" \
    "${note:+  $note}")"$'\n'
done

echo
echo "stage          verdict    time  log"
printf '%s' "$summary"
if [ "$failed" -ne 0 ]; then
  echo "check: FAILED, $failed of $(echo $selected | wc -w | tr -d ' ') stages"
  exit 1
fi
if [ -n "$partial" ]; then
  echo "check: all stages passed, in part only:$partial"
  exit 0
fi
echo "check: all stages passed"
