#!/usr/bin/env bash
# Stands in for bench_probe --check: prints what probe_output.txt beside it says.
cat "$(dirname "$0")/probe_output.txt"
