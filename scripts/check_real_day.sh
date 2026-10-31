#!/usr/bin/env bash
# The checks that need a real trading day in data/. scripts/check.sh runs everything
# that does not.
#
#     scripts/check_real_day.sh [DAY...]
#
# With no DAY it checks every day whose counts are pinned in scripts/data_manifest.json.
# A pinned day with no file is a failure, because a skipped day would look like a pass.
# Set ITCH_HEAVY_WRAPPER to a command to run each check under it, on a shared machine.
# Raw logs go to ITCH_REAL_DAY_LOGS, default build/real-day-logs. The curated results go
# to results/oracle_<day>.md.
#
# The oracle takes a whole day and about 7 GB beyond the mapped file. T-INV-2 is strict
# and expected to fail on real data: that result is reported as a failure and keeps this
# script's exit status non-zero, and it does not stop the other checks. Exit status 3 from
# oracle_report.py separates that from a broken book, which is status 1.
#
# To add a real-day check: add a function taking the day, and call it from check_day.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root" || exit 2

wrapper=${ITCH_HEAVY_WRAPPER:-}
log_dir=${ITCH_REAL_DAY_LOGS:-build/real-day-logs}
mkdir -p "$log_dir"

# GNU time spells it -v and macOS -l. A missing one only loses the memory figures.
timer() {
  if [ -x /usr/bin/time ]; then
    case $(uname -s) in
      Darwin) echo "/usr/bin/time -l" ;;
      *) echo "/usr/bin/time -v" ;;
    esac
  fi
}

pinned_days() {
  python3 -B -c '
import json
for day, entry in json.load(open("scripts/data_manifest.json"))["days"].items():
    if entry.get("per_type_counts"):
        print(day)
'
}

census() {
  # shellcheck disable=SC2086
  $wrapper python3 -B scripts/check_census.py --day "$1"
}

# What built the binaries, for the header of the results file.
run_header() {
  local tree=clean
  [ -z "$(git status --porcelain -- include src tests tools fuzz scripts cmake CMakeLists.txt)" ] ||
    tree=dirty
  echo "run_commit $(git rev-parse --short HEAD) ($tree)"
  echo "run_compiler $("${CXX:-c++}" --version | head -n 1 | sed 's/ (.*//')"
  echo "run_flags $(python3 -B scripts/oracle_report.py --print-flags build/release/compile_commands.json)"
  echo "run_host $(uname -s) $(uname -m) $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
  echo "run_command $1"
}

oracle() {
  local day=$1 oracle_log=$log_dir/oracle_$day.log alloc_log=$log_dir/alloc_$day.log status
  local observations=$log_dir/observations_$day.txt
  local file=data/$day.NASDAQ_ITCH50
  run_header "itch_oracle $file --observations <raw log>" >"$oracle_log"
  # shellcheck disable=SC2086
  $(timer) $wrapper build/release/tools/itch_oracle "$file" --observations "$observations" \
    >>"$oracle_log" 2>&1
  status=$?
  echo "real day: $day itch_oracle exited $status"
  run_header "itch_alloc $file" >"$alloc_log"
  # shellcheck disable=SC2086
  $(timer) $wrapper build/release/tools/itch_alloc "$file" >>"$alloc_log" 2>&1
  echo "real day: $day itch_alloc exited $?"
  python3 -B scripts/oracle_report.py --day "$day" --log "$oracle_log" --alloc-log "$alloc_log" \
    --out "results/oracle_$day.md"
}

check_day() {
  local failed=0
  census "$1" || failed=1
  oracle "$1" || failed=1
  return "$failed"
}

if ! cmake --build --preset release --parallel 6 --target itch_census itch_oracle itch_alloc; then
  echo "real day: could not build the tools. Run: cmake --preset release" >&2
  exit 2
fi

days=("$@")
if [ ${#days[@]} -eq 0 ]; then
  read -r -a days <<<"$(pinned_days | tr '\n' ' ')"
fi
if [ ${#days[@]} -eq 0 ]; then
  echo "real day: no pinned days" >&2
  exit 2
fi

failed=0
for day in "${days[@]}"; do
  if check_day "$day"; then
    echo "real day: $day ok"
  else
    echo "real day: $day FAILED"
    failed=$((failed + 1))
  fi
done
[ "$failed" -eq 0 ] || { echo "real day: FAILED, $failed days"; exit 1; }
echo "real day: all ${#days[@]} days passed"
