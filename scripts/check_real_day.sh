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
# to results/oracle_<day>.md and results/trades_<day>.md.
#
# The oracle takes ten minutes a day and about 0.7 GB beyond the mapped file. T-INV-2 is strict
# and expected to fail on real data: that result is reported as a failure and keeps this
# script's exit status non-zero, and it does not stop the other checks. Exit status 3 from
# oracle_report.py separates that from a broken book, which is status 1.
#
# The trade-print checks take a few minutes a day and a few hundred MB beyond the mapped file.
# They read the SEC MIDAS zip and data/CrossStats<YYYYMMDD>.txt. A day with no cross file is
# reported as T-TRADE-2 not available, and says so in its results. A missing MIDAS zip is a
# failure.
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
  echo "run_flags $(python3 -B scripts/oracle_report.py --print-flags build/release/compile_commands.json "${2:-tools/itch_oracle.cpp}")"
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

# The Nasdaq rows of a day in MIDAS, as pinned in the manifest.
midas_rows() {
  python3 -B -c '
import json, sys
midas = json.load(open("scripts/data_manifest.json"))["references"]["midas_2019q4"]
print(midas["nasdaq_rows"][sys.argv[1]])
' "$1"
}

trades() {
  local day=$1 date=${1:4:4}${1:0:4} log=$log_dir/trades_$day.log status
  local file=data/$day.NASDAQ_ITCH50 cross=data/CrossStats${date}.txt
  local symbols=$log_dir/trades_symbols_$day.txt member rows
  local months=(jan feb mar apr may jun jul aug sep oct nov dec)
  local month=${months[$((10#${day:0:2} - 1))]}
  member=$month${day:6:2}.csv
  if ! python3 -B -I scripts/extract_midas.py --member "$member"; then
    echo "real day: $day has no MIDAS member $member" >&2
    return 1
  fi
  rows=$(midas_rows "$date") || return 1
  local args=("$file" --date "$date" --midas "build/reference/$member" --midas-rows "$rows"
    --symbols "$symbols")
  local report_args=()
  if [ -f "$cross" ]; then
    args+=(--crossstats "$cross")
  else
    echo "real day: $day has no cross file, so T-TRADE-2 is not available"
    report_args=(--no-cross-file)
  fi
  run_header "itch_trades ${args[*]/#$symbols/<raw log>}" tools/itch_trades.cpp >"$log"
  # shellcheck disable=SC2086
  $(timer) $wrapper build/release/tools/itch_trades "${args[@]}" >>"$log" 2>&1
  status=$?
  echo "real day: $day itch_trades exited $status"
  python3 -B scripts/trades_report.py --day "$day" --log "$log" ${report_args[@]+"${report_args[@]}"} \
    --out "results/trades_$day.md"
}

check_day() {
  local failed=0
  census "$1" || failed=1
  oracle "$1" || failed=1
  trades "$1" || failed=1
  return "$failed"
}

if ! cmake --build --preset release --parallel 6 --target itch_census itch_oracle itch_alloc itch_trades; then
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
