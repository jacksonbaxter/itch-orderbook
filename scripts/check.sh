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
# headless browser ITCH_BROWSER names, if it names one. It never reads data/: real-day checks
# live in scripts/check_real_day.sh.
#
# Stages run one at a time and a build uses at most ITCH_CHECK_JOBS jobs, so that no two
# sanitizer or clang-tidy builds ever overlap. A failed stage does not stop the rest.
# The exit status is non-zero if any stage failed. Each stage's output is kept in
# build/check-logs/<stage>.log.
#
# To add a stage: write a function named stage_<name>, then add one `register` line.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root" || exit 2

jobs=${ITCH_CHECK_JOBS:-6}
log_dir=build/check-logs

python_test() { python3 -B "$@"; }

build_preset() {
  cmake --preset "$1" && cmake --build --preset "$1" --parallel "$jobs"
}

# A preset's build and its whole ctest suite.
preset_with_tests() {
  build_preset "$1" && ctest --preset "$1"
}

stage_toolchain() {
  for tool in cmake ninja python3 clang-format clang-tidy; do
    command -v "$tool" || { echo "missing tool: $tool"; return 1; }
  done
  "${CXX:-c++}" --version | head -n 1
  cmake --version | head -n 1
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
    python_test scripts/test_fetch_tool.py
}

# RESULTS.md's tables and the README's headline against a fresh run over results/, and the
# figures on both pages against those files, and T-ORACLE-2's sum of the oracle's results files
# against a fresh one.
stage_readme() {
  python_test scripts/oracle_sessions.py --check &&
    python_test scripts/results_table.py --check &&
    python_test scripts/test_results_table.py &&
    python_test scripts/gate_numbers.py --page README.md --page RESULTS.md
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
    ctest --preset release -R '^(test_book|test_alloc|test_node_book|test_oracle_cli|corpus_fuzz_book)$'
}
# The trade-print checks on their fixtures: both reference parsers and their corpora, the
# faulty-book tests, and every check through the tool. Needs nothing from data/.
stage_trades() {
  build_preset release &&
    ctest --preset release \
      -R '^(test_reference_files|test_trade_checks|test_trades_cli|corpus_fuzz_crossstats|corpus_fuzz_midas)$'
}
# The benchmark harness: each check on its fixtures, the gates on the committed results,
# and the tests of the harness itself. Needs nothing from data/.
stage_bench() {
  local results
  python_test scripts/test_machine_check.py &&
    python_test scripts/test_gate_numbers.py &&
    python_test scripts/test_check_predictions.py &&
    python_test scripts/test_bench_results.py &&
    python_test scripts/test_driver_records.py &&
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
    python_test scripts/check_predictions.py || return 1
  results=$(find results -name 'bench_*.md' -o -name 'backtest_*.md' -o -name 'spsc_*.md' \
    -o -name 'alloc_*.md' -o -name 'walks_ladder_*.md')
  if [ -n "$results" ]; then
    # shellcheck disable=SC2086
    python_test scripts/check_results.py $results || return 1
  fi
  if [ -f results/chase_debian.md ]; then
    python_test scripts/chase_report.py --check results/chase_debian.md || return 1
  fi
  for counts in results/counters_by_type_*.md results/counters_by_age_*.md; do
    [ -f "$counts" ] || continue
    python_test scripts/counts_report.py --check "$counts" || return 1
  done
  if [ -f results/counters_zen1_events_debian.md ]; then
    python_test scripts/zen1_events.py --check results/counters_zen1_events_debian.md || return 1
  fi
  for graph in results/flamegraph_*.md results/profile_*.md; do
    [ -f "$graph" ] || continue
    python_test scripts/flame_report.py --check "$graph" || return 1
  done
  for variants in results/lookup_*.md results/prefetch_*.md results/dispatch_*.md \
    results/layout_*.md results/structure_*.md; do
    [ -f "$variants" ] || continue
    python_test scripts/variants_report.py --check "$variants" || return 1
  done
  for types in results/types_*.md; do
    [ -f "$types" ] || continue
    python_test scripts/types_report.py --check "$types" || return 1
  done
  for window in results/window_*.md; do
    [ -f "$window" ] || continue
    python_test scripts/window_report.py --check "$window" || return 1
  done
  for simd in results/simd_*.md; do
    [ -f "$simd" ] || continue
    case $simd in
      results/simd_asm_*) python_test scripts/simd_asm_audit.py --check "$simd" || return 1 ;;
      *) python_test scripts/simd_report.py --check "$simd" || return 1 ;;
    esac
  done
  for udp in results/udp_*.md; do
    [ -f "$udp" ] || continue
    python_test scripts/udp_report.py --check "$udp" || return 1
  done
  for walks in results/walks_*.md; do
    [ -f "$walks" ] || continue
    python_test scripts/walk_report.py check "$walks" || return 1
  done
  build_preset release && ctest --preset release -R '^(test_bench|test_walk_counts$)'
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
    ctest --preset release -R '^(test_attribution|test_market_maker|test_alloc|test_calibrate_cli|test_select_cli|test_mm_cli)$'
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
# an extract cut at test time, and the page in a headless browser when ITCH_BROWSER names one.
# Needs EMSDK, and nothing from data/.
stage_demo() {
  local browser=()
  python_test scripts/test_demo.py &&
    python_test scripts/demo.py numbers --check &&
    python_test scripts/wasm_report.py check &&
    build_preset release &&
    ctest --preset release -R '^(test_demo_replay|test_demo_cli|test_alloc)$' &&
    build_preset wasm || return 1
  if [ -n "${ITCH_BROWSER:-}" ]; then
    browser=(--browser "$ITCH_BROWSER")
  fi
  python_test scripts/test_demo_engine.py --release build/release --wasm build/wasm \
    ${browser[@]+"${browser[@]}"}
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
    --list)
      for name in $stage_names; do
        case " $quick_names " in
          *" $name "*) echo "$name (quick)" ;;
          *) echo "$name" ;;
        esac
      done
      exit 0
      ;;
    *) echo "usage: scripts/check.sh [--quick] [--list] [--only NAME]..." >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$only" ]; then
  selected=$only
fi

mkdir -p "$log_dir"
summary=""
failed=0
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
  printf '%s (%d s)\n' "$verdict" "$elapsed"
  if [ "$verdict" = FAIL ]; then
    tail -n 20 "$log"
  fi
  summary="$summary$(printf '%-14s %-5s %5d s  %s' "$name" "$verdict" "$elapsed" "$log")"$'\n'
done

echo
echo "stage          verdict  time  log"
printf '%s' "$summary"
if [ "$failed" -ne 0 ]; then
  echo "check: FAILED, $failed of $(echo $selected | wc -w | tr -d ' ') stages"
  exit 1
fi
echo "check: all stages passed"
