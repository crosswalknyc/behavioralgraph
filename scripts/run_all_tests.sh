#!/usr/bin/env bash
# Run every scripts/test_*.py hermetically with a per-suite timeout and
# print the pass / fail / hang table. See run_all_tests.py for flags.
#
#   scripts/run_all_tests.sh
#   scripts/run_all_tests.sh --only pm_ --jobs 6
#   PYTHON=/opt/homebrew/bin/python3.11 scripts/run_all_tests.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$HERE/run_all_tests.py" --python "${PYTHON:-python3}" "$@"
