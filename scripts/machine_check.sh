#!/usr/bin/env bash
# Says whether this machine is fit to measure on, before every reported benchmark.
# scripts/bench_driver.py runs it and embeds the output in the results file.
#
#     machine_check.sh --input FILE --probe BENCH_PROBE [--core N] [--sibling N]
#                      [--os linux|darwin] [--root DIR] [--idle-window SECONDS]
#
# Each line is `machine_check: pass|FAIL|record <condition>: <detail>`. A pass or FAIL is
# enforced and a record only states what the machine is. The last line is the verdict.
#
# Linux enforces pin read-back, the governor, the SMT sibling being idle, the input being
# resident and the timer calibrating. debian has no isolated core, and turbo stays on, so
# those are recorded. macOS can enforce the last two only, and records the rest as what
# cannot be enforced there. Conditions that cannot be read fail: an unknown is not a pass.
#
# --root reads /proc and /sys, or fake sysctl values, from a tree instead, so that
# scripts/test_machine_check.py can break one condition at a time on any host.
#
# Exit codes: 0 every enforced condition passed, 1 at least one failed, 2 bad usage.

set -u

input=""
probe=""
core=4
sibling=5
root=""
idle_window=1
os=$(uname -s | tr '[:upper:]' '[:lower:]')
max_sibling_busy_percent=5

usage() {
  echo "usage: machine_check.sh --input FILE --probe BENCH_PROBE [--core N] [--sibling N]" \
    "[--os linux|darwin] [--root DIR] [--idle-window SECONDS]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || usage
  case $1 in
    --input) input=$2 ;;
    --probe) probe=$2 ;;
    --core) core=$2 ;;
    --sibling) sibling=$2 ;;
    --os) os=$2 ;;
    --root) root=$2 ;;
    --idle-window) idle_window=$2 ;;
    *) usage ;;
  esac
  shift 2
done
[ -n "$input" ] && [ -n "$probe" ] || usage

failures=0
pass() { echo "machine_check: pass $1: $2"; }
fail() {
  echo "machine_check: FAIL $1: $2"
  failures=$((failures + 1))
}
record() { echo "machine_check: record $1: $2"; }

read_or() { # path fallback
  if [ -r "$root$1" ]; then
    head -n 1 "$root$1"
  else
    echo "$2"
  fi
}

mac_sysctl() { # name
  if [ -n "$root" ]; then
    cat "$root/sysctl/$1" 2>/dev/null
  else
    sysctl -n "$1" 2>/dev/null
  fi
}

check_probe() {
  if [ ! -e "$input" ]; then
    fail input-resident "the input does not exist"
    fail timer-calibration "the probe needs an input and has none"
    return
  fi
  if [ ! -x "$probe" ]; then
    fail input-resident "no probe to ask"
    fail timer-calibration "no probe to ask"
    return
  fi
  local output line resident pages quantum
  if [ "$os" = linux ] && [ -z "$root" ]; then
    output=$("$probe" --check "$input" --core "$core" 2>&1)
  else
    output=$("$probe" --check "$input" 2>&1)
  fi

  line=$(printf '%s\n' "$output" | sed -n 's/^residency: //p')
  resident=$(printf '%s\n' "$line" | sed -n 's/^\([0-9][0-9]*\) of [0-9][0-9]* pages resident$/\1/p')
  pages=$(printf '%s\n' "$line" | sed -n 's/^[0-9][0-9]* of \([0-9][0-9]*\) pages resident$/\1/p')
  if [ -n "$pages" ] && [ "$pages" != 0 ] && [ "$resident" = "$pages" ]; then
    pass input-resident "$line"
  else
    fail input-resident "${line:-the probe gave no residency}"
  fi

  quantum=$(printf '%s\n' "$output" | sed -n 's/^timer_quantum_ns: //p')
  if [ -n "$quantum" ] && awk -v q="$quantum" 'BEGIN { exit !(q > 0) }'; then
    pass timer-calibration "quantum $quantum ns, calibrated on this run"
  else
    fail timer-calibration "the probe measured no quantum: ${quantum:-nothing}"
  fi
}

check_linux() {
  local allowed governor path stats_after busy
  local model
  model=$(grep -m 1 'model name' "$root/proc/cpuinfo" 2>/dev/null)
  case $model in
    *"Ryzen Embedded V1605B"*) record host debian ;;
    *) record host unknown ;;
  esac

  allowed=$(sed -n 's/^Cpus_allowed_list:[[:space:]]*//p' "$root/proc/self/status" 2>/dev/null)
  if [ "$allowed" = "$core" ]; then
    pass pin-readback "allowed on exactly core $core"
  else
    fail pin-readback "wanted core $core, read back '${allowed:-nothing}'"
  fi

  path=/sys/devices/system/cpu/cpu$core/cpufreq/scaling_governor
  governor=$(read_or "$path" "")
  if [ "$governor" = performance ]; then
    pass governor "core $core runs performance"
  else
    fail governor "core $core runs '${governor:-unreadable}', wanted performance"
  fi

  stats_after="$root/proc/stat"
  [ -r "$stats_after.after" ] && stats_after="$stats_after.after"
  local before after
  before=$(grep "^cpu$sibling " "$root/proc/stat" 2>/dev/null)
  sleep "$idle_window"
  after=$(grep "^cpu$sibling " "$stats_after" 2>/dev/null)
  busy=$(printf '%s\n%s\n' "$before" "$after" | awk '
    NF >= 9 {
      idle = $5 + $6
      total = 0
      for (i = 2; i <= 9; i++) total += $i
      if (NR == 1) { idle0 = idle; total0 = total } else { idle1 = idle; total1 = total }
    }
    END {
      if (NR != 2 || total1 <= total0) exit
      printf "%.1f", 100 * ((total1 - total0) - (idle1 - idle0)) / (total1 - total0)
    }')
  if [ -z "$busy" ]; then
    fail sibling-idle "no usable /proc/stat line for cpu$sibling"
  elif awk -v b="$busy" -v m="$max_sibling_busy_percent" 'BEGIN { exit !(b <= m) }'; then
    pass sibling-idle "cpu$sibling is $busy% busy over ${idle_window} s"
  else
    fail sibling-idle "cpu$sibling is $busy% busy over ${idle_window} s, over $max_sibling_busy_percent%"
  fi

  check_probe

  local isolated
  isolated=$(read_or /sys/devices/system/cpu/isolated unreadable)
  record isolation "${isolated:-none, so nothing keeps the kernel off core $core}"
  record turbo "boost=$(read_or /sys/devices/system/cpu/cpufreq/boost unreadable)"
  record smt "active=$(read_or /sys/devices/system/cpu/smt/active unreadable)"
  record frequency "$(read_or "/sys/devices/system/cpu/cpu$core/cpufreq/scaling_cur_freq" unreadable) kHz"
  record perf_event_paranoid "$(read_or /proc/sys/kernel/perf_event_paranoid unreadable)"
  record load "$(read_or /proc/loadavg unreadable)"
}

check_darwin() {
  case $(mac_sysctl machdep.cpu.brand_string) in
    "Apple M4 Max") record host "macOS (M4 Max)" ;;
    *) record host unknown ;;
  esac
  check_probe
  record pin "cannot be enforced: macOS offers no way to pin a thread"
  record governor "cannot be enforced: macOS has no cpufreq governor"
  record turbo "cannot be enforced: macOS exposes no boost control"
  record isolation "cannot be enforced: macOS has no isolated cores"
  record sibling-idle "cannot be enforced: this CPU has no SMT sibling, other cores stay in use"
  record load "$(mac_sysctl vm.loadavg)"
}

case $os in
  linux) check_linux ;;
  darwin) check_darwin ;;
  *) usage ;;
esac

if [ "$failures" -ne 0 ]; then
  echo "machine_check: refused, $failures failed"
  exit 1
fi
echo "machine_check: passed"
