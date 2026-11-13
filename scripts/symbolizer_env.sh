# Sourced by scripts/check.sh. On macOS a sanitizer report, and each NEW_FUNC line of
# libFuzzer, is symbolized by default by the system symbolizer, which attaches to the process
# and may wait on a system authorization dialog; a check must never wait on a dialog. So on
# macOS each sanitizer's options name the llvm-symbolizer beside $CXX, appended to whatever
# they hold, for every command the check runs, ctest's and those outside it. Where there is no
# llvm-symbolizer there the path is empty, which turns the external symbolizer off. Other
# systems are left as they are. ITCH_SYMBOLIZER_SYSTEM stands in for `uname -s` in
# scripts/test_sanitizer_tests.py. cmake/ItchSanitizerTests.cmake does the same for ctest, and
# scripts/fault_matrix.py for its commands.
# shellcheck shell=bash
if [ "${ITCH_SYMBOLIZER_SYSTEM:-$(uname -s)}" = Darwin ]; then
  itch_symbolizer=""
  if itch_compiler=$(command -v "${CXX:-c++}"); then
    itch_symbolizer="$(dirname "$itch_compiler")/llvm-symbolizer"
    [ -f "$itch_symbolizer" ] || itch_symbolizer=""
  fi
  export ASAN_OPTIONS="${ASAN_OPTIONS:-}:external_symbolizer_path=$itch_symbolizer"
  export UBSAN_OPTIONS="${UBSAN_OPTIONS:-}:external_symbolizer_path=$itch_symbolizer"
  export TSAN_OPTIONS="${TSAN_OPTIONS:-}:external_symbolizer_path=$itch_symbolizer"
  unset itch_symbolizer itch_compiler
fi
