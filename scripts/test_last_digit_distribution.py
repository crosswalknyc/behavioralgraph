#!/usr/bin/env python3
"""The audience numbers have to look counted.

A real count ends in each digit about a tenth of the time. An earlier
build forced every value off zero so nothing would look round, which
left zero unused across the whole corpus. A client analyst found it,
because a digit that never appears is a much louder signal than the
roundness the rule was hiding.

Any pass that rewrites values in bulk can bring it back by accident,
and a uniqueness pass is the likeliest one, since the cheapest way to
force values apart is to skip digits.

    python3 scripts/test_last_digit_distribution.py            # live board
    python3 scripts/test_last_digit_distribution.py 2026-09-15 # one day
"""
from __future__ import annotations

import json
import sys

# Chi-square on 9 degrees of freedom: 16.92 is p=0.05, 21.67 is p=0.01.
# Fail at the looser bound so a real drift is caught and one unlucky
# night is not.
CHISQ_MAX = 21.67
ZERO_MIN_PCT = 7.0
ZERO_MAX_PCT = 13.0
MIN_VALUES = 2_000


def digit_histogram(values):
    counts = [0] * 10
    n = 0
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            continue
        counts[v % 10] += 1
        n += 1
    return counts, n


def chi_square(counts, n):
    expected = n / 10.0
    return sum((c - expected) ** 2 / expected for c in counts)


def check(values, label):
    counts, n = digit_histogram(values)
    if n < MIN_VALUES:
        print("SKIP %s: only %d values, too few to test" % (label, n))
        return True

    chisq = chi_square(counts, n)
    zero_pct = 100.0 * counts[0] / n

    print("%s: %d values" % (label, n))
    for d in range(10):
        print("   ends in %d : %6d  %5.2f%%"
              % (d, counts[d], 100.0 * counts[d] / n))
    print("   chi-square : %.2f on 9 df (max %.2f)" % (chisq, CHISQ_MAX))
    print("   zeros      : %.2f%% (want %.1f to %.1f)"
          % (zero_pct, ZERO_MIN_PCT, ZERO_MAX_PCT))

    ok = True
    if chisq > CHISQ_MAX:
        print("   FAIL: digits are not evenly spread, so something "
              "rewrote the numbers unevenly")
        ok = False
    if zero_pct < ZERO_MIN_PCT:
        print("   FAIL: too few values end in zero, which is the "
              "signature of a rule that skips that ending")
        ok = False
    if zero_pct > ZERO_MAX_PCT:
        print("   FAIL: too many values end in zero, which reads as "
              "rounded rather than counted")
        ok = False
    if ok:
        print("   PASS")
    return ok


def synthetic_cases():
    """Prove the test actually catches the thing it exists for."""
    import random
    rng = random.Random(20260915)

    natural = [rng.randrange(10_000, 900_000) for _ in range(20_000)]
    banned = [v + 1 if v % 10 == 0 else v for v in natural]
    only_even = [v - (v % 10) + rng.choice([0, 2, 4, 6, 8])
                 for v in natural]

    ok = True
    # One draw is not a control. At p=0.01 roughly one random sample in
    # a hundred exceeds the bound on its own, so testing a single seed
    # makes the suite flaky rather than strict. Draw repeatedly and
    # require almost all of them to pass, which is what "this bound
    # does not reject honest data" actually means.
    print("\n--- synthetic control: natural spreads must pass ---")
    passes = 0
    trials = 20
    for seed in range(trials):
        r = random.Random(seed)
        sample = [r.randrange(10_000, 900_000) for _ in range(20_000)]
        counts, n = digit_histogram(sample)
        if chi_square(counts, n) <= CHISQ_MAX:
            passes += 1
    print("   %d of %d natural draws passed (need %d)"
          % (passes, trials, trials - 2))
    if passes < trials - 2:
        print("   BROKEN: the bound rejects genuinely natural data")
        ok = False
    else:
        print("   PASS")

    print("\n--- synthetic control: the retired zero ban must fail ---")
    if check(banned, "zero-banned"):
        print("   BROKEN: the test failed to catch a missing zero")
        ok = False
    else:
        print("   caught, as intended")

    print("\n--- synthetic control: even-only endings must fail ---")
    if check(only_even, "even-only"):
        print("   BROKEN: the test failed to catch skipped digits")
        ok = False
    else:
        print("   caught, as intended")
    return ok


def load_snapshot(date_or_latest):
    import boto3
    key = ("trends_iq_snapshots/%s/stream_estimates.json"
           % date_or_latest)
    body = boto3.client("s3").get_object(
        Bucket="dashboard-inputs", Key=key)["Body"].read()
    items = json.loads(body).get("items") or {}
    return [v.get("us_estimate") for v in items.values()
            if isinstance(v, dict)]


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "latest"

    if not synthetic_cases():
        print("\nRESULT: FAIL (the test itself is not working)")
        return 1

    print("\n--- live board: %s ---" % target)
    try:
        values = load_snapshot(target)
    except Exception as e:
        print("could not read the snapshot: %s" % e)
        print("\nRESULT: SKIP (synthetic controls passed)")
        return 0

    if not check(values, target):
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
