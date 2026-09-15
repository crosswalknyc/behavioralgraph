"""Concurrency sweep for headline_estimates, run against the live key.

Prices the SAME fixed slice of real headlines at several worker counts
and reports wall clock, throughput, and transport errors at each level.
The point is to pick the default from a measurement rather than from a
guess, and to find the level where throughput stops improving.

    python3 -m scripts.trends_scrapers.bench_concurrency --n 60 --levels 8,16,32,48

Every level re-prices the same N headlines, so a sweep costs N x levels
web_search calls. Keep N small.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import headline_estimates as he  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=60)
    ap.add_argument('--levels', default='8,16,32,48')
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    items = he._collect_headlines()[:args.n]
    print(f"sweep over {len(items)} real headlines\n")
    print(f"{'workers':>8} {'wall_s':>9} {'items/min':>10} "
          f"{'priced':>7} {'missing':>8}")

    for lvl in [int(x) for x in args.levels.split(',') if x.strip()]:
        os.environ[he._CONCURRENCY_ENV] = str(lvl)
        t0 = time.time()
        out = he._research_all(items)
        dt = time.time() - t0
        print(f"{lvl:>8} {dt:>9.1f} {60.0 * len(out) / max(dt, 1e-9):>10.1f} "
              f"{len(out):>7} {len(items) - len(out):>8}")
    os.environ.pop(he._CONCURRENCY_ENV, None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
