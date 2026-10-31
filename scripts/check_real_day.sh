#!/usr/bin/env bash
# The checks that need a real trading day in data/. scripts/check.sh runs everything
# that does not.
#
#     scripts/check_real_day.sh [DAY...]
#
# With no DAY it checks every day whose counts are pinned in scripts/data_manifest.json.
# A pinned day with no file is a failure, because a skipped day would look like a pass.
# Set ITCH_HEAVY_WRAPPER to a command to run each check under it, on a shared machine.
#
# To add a real-day check: add a function taking the day, and call it from check_day.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root" || exit 2

wrapper=${ITCH_HEAVY_WRAPPER:-}

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

check_day() {
  census "$1"
}

if ! cmake --build --preset release --parallel 6 --target itch_census; then
  echo "real day: could not build itch_census. Run: cmake --preset release" >&2
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
