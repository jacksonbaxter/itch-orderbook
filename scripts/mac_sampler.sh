#!/bin/bash
# The Mac's CPU sampler for a run whose driver samples nothing itself, as the variants'
# runs were made (docs/NOTES.md, "The Mac rule for other work"):
#
#   mac_sampler.sh LOG [EVERY]
#
# Every EVERY seconds (2 unless given) one "<UTC time> sample" line, then one
# "<UTC time> FOREIGN <cpu> <command>" line for each process above 20% CPU that is not one of
# the measured commands (bench_variants, machine_check, bench_probe, /usr/bin/time), appended
# to LOG until it is killed. variants_report.py --sampler reads LOG. This is the script the
# published variants runs used, brought here from the research cache unchanged but for EVERY.
out=$1
every=${2:-2}
while true; do
  t=$(date -u +%FT%TZ)
  echo "$t sample" >> "$out"
  ps -Ao pcpu,args -r | awk -v t="$t" 'NR > 1 && $1 > 20 && $0 !~ /bench_variants|machine_check|bench_probe|\/usr\/bin\/time/ {
    print t, "FOREIGN", $1, substr($0, index($0, $2), 150) }' >> "$out"
  sleep "$every"
done
