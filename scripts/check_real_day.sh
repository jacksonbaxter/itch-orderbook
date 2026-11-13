#!/usr/bin/env bash
# The checks that need a real trading day in data/. scripts/check.sh runs everything
# that does not. A day is its key in scripts/data_manifest.json, MMDDYYYY with a suffix
# for a second venue on the same date, and its file is data/<the manifest's name>.
#
#     scripts/check_real_day.sh [--only census|oracle|trades|map]... [--arms default|all] [DAY...]
#
# With no DAY it checks every day whose counts are pinned in scripts/data_manifest.json.
# --only runs the named checks and leaves out the rest, repeatable. Default: all four.
# --arms picks the arms of the fast book that the oracle and trades checks run. default, the
# default, runs the book's own default arm. all runs each of the four in turn, one at a time,
# hash with linked levels first. Each report then holds every other arm to the first one's
# answers, and the oracle's crossed and locked observations have to be the same file.
# A pinned day with no file is a failure, because a skipped day would look like a pass.
# A day whose book the default capacity cannot hold gives its own as book_capacity in the
# manifest, and the oracle and the allocation check run with it.
# Set ITCH_HEAVY_WRAPPER to a command to run each check under it, on a shared machine.
# Raw logs go to ITCH_REAL_DAY_LOGS, default build/real-day-logs. The curated results go
# to results/oracle_<day>.md and results/trades_<day>.md, or under ITCH_REAL_DAY_RESULTS.
# The tools are the release build's, or ITCH_REAL_DAY_BUILD's, and the compiler a results
# file names is the one that build tree was configured with.
#
# Each tool's exit status is kept beside its raw log, as <log>.exit. A status the tool
# documents as a verdict leaves the day to the report, which holds each verdict to its
# counts: itch_oracle's 0, 1 and 3, itch_alloc's 0 and 1, itch_trades' 0 and 64 plus its
# failed results' bits, itch_map_compare's 0 and 1. Any other, such as bad usage, an
# unreadable file or a crash, means the tool did not finish its run, and fails the day
# whatever its report says.
#
# The oracle takes ten minutes a day and about 1.6 GB beyond the mapped file on the default
# arm, the paged index with flat levels, or 0.7 GB on the hash arms. T-INV-2 is strict
# and expected to fail on real data: that result is reported as a failure and keeps this
# script's exit status non-zero, and it does not stop the other checks. Exit status 3 from
# oracle_report.py separates that from a broken book, which is status 1.
#
# The trade-print checks take a few minutes a day and a few hundred MB beyond the mapped file.
# They read the SEC MIDAS zip of the day's quarter and the day's CrossStats file, which the
# manifest pins as crossstats_<YYYYMMDD>. A pinned cross file that is missing is a failure, as a
# missing day file is, and the file is checked against its pinned SHA-256 (fetch_data.py
# --verify-only) before the tool reads it. A day whose manifest pins no cross file is reported
# as T-TRADE-2 not available, and says so in its results, and so is a day of another venue than
# Nasdaq, since CrossStats lists Nasdaq's own crosses. A missing MIDAS zip is a failure.
#
# The map check replays the day through the map book, the T-DESIGN-1 baseline, beside the
# fast book's default arm, once on a fresh heap and once on an aged one, and passes only if
# itch_map_compare does. Its raw logs are the result: it times nothing.
#
# To add a real-day check: add a function taking the day, and call it from check_day.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root" || exit 2

wrapper=${ITCH_HEAVY_WRAPPER:-}
log_dir=${ITCH_REAL_DAY_LOGS:-build/real-day-logs}
results_dir=${ITCH_REAL_DAY_RESULTS:-results}
build_dir=${ITCH_REAL_DAY_BUILD:-build/release}
mkdir -p "$log_dir" "$results_dir"

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

# The day's unpacked file, by the name the manifest gives it (scripts/fetch_data.py's
# unpacked_name).
day_file() {
  python3 -B -c '
import json, sys
entry = json.load(open("scripts/data_manifest.json"))["days"][sys.argv[1]]
print("data/" + entry.get("unpacked_name", entry["name"]))
' "$1"
}

census() {
  # shellcheck disable=SC2086
  $wrapper python3 -B scripts/check_census.py --day "$1"
}

# The C++ compiler the build tree was configured with, as CMake recorded it.
build_compiler() {
  sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p' "$build_dir/CMakeCache.txt" 2>/dev/null | head -n 1
}

# What built the binaries, for the header of the results file.
run_header() {
  local tree=clean compiler
  [ -z "$(git status --porcelain -- include src tests tools fuzz scripts cmake CMakeLists.txt)" ] ||
    tree=dirty
  echo "run_commit $(git rev-parse --short HEAD) ($tree)"
  compiler=$(build_compiler)
  if [ -n "$compiler" ] && [ -x "$compiler" ]; then
    echo "run_compiler $("$compiler" --version | head -n 1 | sed 's/ (.*//')"
  else
    echo "run_compiler unavailable"
  fi
  echo "run_flags $(python3 -B scripts/oracle_report.py --print-flags "$build_dir/compile_commands.json" "${2:-tools/itch_oracle.cpp}")"
  echo "run_host $(uname -s) $(uname -m) $(sysctl -n machdep.cpu.brand_string 2>/dev/null)"
  echo "run_command $1"
}

# Whether a tool's exit status is one it documents as a verdict.
verdict_exit() {
  case $1:$2 in
    itch_oracle:0 | itch_oracle:1 | itch_oracle:3) return 0 ;;
    itch_alloc:0 | itch_alloc:1) return 0 ;;
    itch_map_compare:0 | itch_map_compare:1) return 0 ;;
    itch_trades:0) return 0 ;;
    itch_trades:*) [ "$2" -ge 64 ] && [ "$2" -le 95 ] ;;
    *) return 1 ;;
  esac
}

# Keeps a tool's exit status beside its raw log and says it. False for a status the tool
# does not document as a verdict.
note_exit() {
  local tool=$1 label=$2 status=$3 log=$4
  echo "$status" >"${log%.log}.exit"
  echo "real day: $label $tool exited $status"
  if ! verdict_exit "$tool" "$status"; then
    echo "real day: $label $tool exited $status, which is no verdict: it did not finish its run" >&2
    return 1
  fi
}

# The command-line flags that pick an arm, one word per line. default picks none.
arm_flags() {
  [ "$1" = default ] || printf '%s\n' --index "${1%_*}" --levels "${1#*_}"
}

# The book's capacity for a day the default cannot hold, from the manifest's book_capacity,
# one word per line. A day with none keeps the book's defaults.
capacity_flags() {
  python3 -B -c '
import json, sys
for flag, value in json.load(open("scripts/data_manifest.json"))["days"][sys.argv[1]].get(
        "book_capacity", {}).items():
    print(flag)
    print(value)
' "$1"
}

oracle() {
  local day=$1 file arm first= status mismatch=0 unfinished=0 report_args=()
  local oracle_log alloc_log observations flags word
  file=$(day_file "$day") || return 1
  for arm in "${arms[@]}"; do
    oracle_log=$log_dir/oracle_${day}_$arm.log
    alloc_log=$log_dir/alloc_${day}_$arm.log
    observations=$log_dir/observations_${day}_$arm.txt
    flags=()
    while read -r word; do flags+=("$word"); done < <(arm_flags "$arm")
    while read -r word; do flags+=("$word"); done < <(capacity_flags "$day")
    run_header "itch_oracle $file ${flags[*]-}${flags[*]:+ }--observations <raw log>" >"$oracle_log"
    # shellcheck disable=SC2086
    $(timer) $wrapper "$build_dir/tools/itch_oracle" "$file" ${flags[@]+"${flags[@]}"} \
      --observations "$observations" >>"$oracle_log" 2>&1
    note_exit itch_oracle "$day $arm" $? "$oracle_log" || unfinished=1
    run_header "itch_alloc $file${flags[*]:+ }${flags[*]-}" >"$alloc_log"
    # shellcheck disable=SC2086
    $(timer) $wrapper "$build_dir/tools/itch_alloc" "$file" ${flags[@]+"${flags[@]}"} \
      >>"$alloc_log" 2>&1
    note_exit itch_alloc "$day $arm" $? "$alloc_log" || unfinished=1
    if [ -z "$first" ]; then
      first=$arm
      report_args=(--log "$oracle_log" --alloc-log "$alloc_log")
    else
      report_args+=(--arm-log "$oracle_log" "$alloc_log")
      if ! cmp -s "$log_dir/observations_${day}_$first.txt" "$observations"; then
        echo "real day: $day $arm observed other crossed or locked books than $first"
        mismatch=1
      fi
    fi
  done
  python3 -B scripts/oracle_report.py --day "$day" "${report_args[@]}" \
    --out "$results_dir/oracle_$day.md"
  status=$?
  [ "$mismatch" -eq 0 ] && [ "$unfinished" -eq 0 ] || return 1
  return "$status"
}

# How MIDAS names the venue of a day's feed: Nasdaq unless the manifest says otherwise.
midas_exchange() {
  python3 -B -c '
import json, sys
print(json.load(open("scripts/data_manifest.json"))["days"][sys.argv[1]].get("midas_exchange", "Nasdaq"))
' "$1"
}

# A venue's rows on a date in MIDAS, as pinned in the manifest under its quarter's zip.
midas_rows() {
  python3 -B -c '
import json, sys
references = json.load(open("scripts/data_manifest.json"))["references"]
rows = sys.argv[2].lower() + "_rows"
found = [ref[rows][sys.argv[1]] for key, ref in sorted(references.items())
         if key.startswith("midas_") and sys.argv[1] in ref.get(rows, {})]
if len(found) != 1:
    sys.exit(f"real day: {len(found)} MIDAS zips pin {sys.argv[2]} rows for {sys.argv[1]}")
print(found[0])
' "$1" "$2"
}

# The day's CrossStats file as data/<its pinned filename>, or nothing if the manifest pins none.
cross_file() {
  python3 -B -c '
import json, sys
ref = json.load(open("scripts/data_manifest.json"))["references"].get("crossstats_" + sys.argv[1])
if ref is not None:
    print("data/" + ref["filename"])
' "$1"
}

trades() {
  local day=$1 date=${1:4:4}${1:0:4} log symbols
  local file cross member rows exchange
  local months=(jan feb mar apr may jun jul aug sep oct nov dec)
  local month=${months[$((10#${day:0:2} - 1))]}
  member=$month${day:6:2}.csv
  file=$(day_file "$day") || return 1
  if ! python3 -B -I scripts/extract_midas.py --member "$member"; then
    echo "real day: $day has no MIDAS member $member" >&2
    return 1
  fi
  exchange=$(midas_exchange "$day") || return 1
  rows=$(midas_rows "$date" "$exchange") || return 1
  cross=$(cross_file "$date") || return 1
  local args=("$file" --date "$date" --midas "build/reference/$member" --midas-rows "$rows")
  local report_args=() arm first= flags word unfinished=0 status
  if [ "$exchange" != Nasdaq ]; then
    # CrossStats is Nasdaq's file of its own crosses, so another venue has none to compare.
    args+=(--exchange "$exchange")
    echo "real day: $day is a feed of MIDAS's $exchange, so T-TRADE-2 is not available"
    report_args=(--no-cross-file)
  elif [ -n "$cross" ]; then
    if [ ! -f "$cross" ]; then
      echo "real day: $day has no cross file $cross, and the manifest pins crossstats_$date" >&2
      return 1
    fi
    if ! python3 -B -I scripts/fetch_data.py --ref "crossstats_$date" --verify-only; then
      echo "real day: $day's cross file $cross is not the one the manifest pins" >&2
      return 1
    fi
    args+=(--crossstats "$cross")
  else
    echo "real day: $day has no cross file in the manifest, so T-TRADE-2 is not available"
    report_args=(--no-cross-file)
  fi
  for arm in "${arms[@]}"; do
    log=$log_dir/trades_${day}_$arm.log
    symbols=$log_dir/trades_symbols_${day}_$arm.txt
    flags=()
    while read -r word; do flags+=("$word"); done < <(arm_flags "$arm")
    run_header "itch_trades ${args[*]} --symbols <raw log>${flags[*]:+ }${flags[*]-}" \
      tools/itch_trades.cpp >"$log"
    # shellcheck disable=SC2086
    $(timer) $wrapper "$build_dir/tools/itch_trades" "${args[@]}" --symbols "$symbols" \
      ${flags[@]+"${flags[@]}"} >>"$log" 2>&1
    note_exit itch_trades "$day $arm" $? "$log" || unfinished=1
    if [ -z "$first" ]; then
      first=$arm
      report_args+=(--log "$log")
    else
      report_args+=(--arm-log "$log")
    fi
  done
  python3 -B scripts/trades_report.py --day "$day" "${report_args[@]}" \
    --out "$results_dir/trades_$day.md"
  status=$?
  [ "$unfinished" -eq 0 ] || return 1
  return "$status"
}

map() {
  local day=$1 file heap log status failed=0
  file=$(day_file "$day") || return 1
  for heap in fresh aged; do
    log=$log_dir/map_${day}_$heap.log
    run_header "itch_map_compare $file --heap $heap" tools/itch_map_compare.cpp >"$log"
    # shellcheck disable=SC2086
    $(timer) $wrapper "$build_dir/tools/itch_map_compare" "$file" --heap "$heap" >>"$log" 2>&1
    status=$?
    note_exit itch_map_compare "$day $heap" "$status" "$log" || failed=1
    [ "$status" -eq 0 ] || failed=1
  done
  return "$failed"
}

check_day() {
  local failed=0 check
  for check in "${checks[@]}"; do
    "$check" "$1" || failed=1
  done
  return "$failed"
}

checks=()
arms=(default)
while [ "${1:-}" = --only ] || [ "${1:-}" = --arms ]; do
  case $1:${2:-} in
    --only:census | --only:oracle | --only:trades | --only:map) checks+=("$2") ;;
    --arms:default) arms=(default) ;;
    --arms:all) arms=(hash_linked hash_flat paged_linked paged_flat) ;;
    --only:*) echo "real day: --only takes census, oracle, trades or map" >&2; exit 2 ;;
    *) echo "real day: --arms takes default or all" >&2; exit 2 ;;
  esac
  shift 2
done
[ ${#checks[@]} -gt 0 ] || checks=(census oracle trades map)

if ! cmake --build "$build_dir" --parallel 6 --target itch_census itch_oracle itch_alloc itch_trades \
  itch_map_compare; then
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
