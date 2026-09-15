"""Is the run-to-run spread in headline estimates caused by the
parallel path, or is it inherent to the estimator?

Prices the same headlines four times: twice on the sequential path and
twice on the parallel path. If sequential-vs-sequential disagrees by
about as much as sequential-vs-parallel, then the spread belongs to
the estimator (a sampled model turn over live web_search results that
move between calls) and the fan-out is not what moved the numbers.

    python3 -m scripts.trends_scrapers.bench_variance --n 12
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import _parallel                 # noqa: E402
from scripts.trends_scrapers import headline_estimates as he  # noqa: E402


def run(items, sequential: bool):
    if sequential:
        os.environ[_parallel.SEQUENTIAL_ENV] = '1'
    else:
        os.environ.pop(_parallel.SEQUENTIAL_ENV, None)
    try:
        return he._research_all(items)
    finally:
        os.environ.pop(_parallel.SEQUENTIAL_ENV, None)


def spread(a: dict, b: dict, label: str) -> None:
    both = sorted(set(a) & set(b))
    if not both:
        print(f"{label:<28} no overlap")
        return
    ratios = sorted(max(a[k]['us_estimate'], b[k]['us_estimate'])
                    / max(min(a[k]['us_estimate'], b[k]['us_estimate']), 1)
                    for k in both)
    ident = sum(1 for k in both
                if a[k]['us_estimate'] == b[k]['us_estimate'])
    within2 = sum(1 for r in ratios if r <= 2.0)
    print(f"{label:<28} n={len(both):<3} identical={ident:<3} "
          f"within2x={within2}/{len(both)}  median={statistics.median(ratios):.2f}x  "
          f"max={ratios[-1]:.1f}x")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=12)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    items = he._collect_headlines()[:args.n]
    print(f"four passes over the same {len(items)} headlines\n")

    seq_a = run(items, True)
    seq_b = run(items, True)
    par_a = run(items, False)
    par_b = run(items, False)

    print("pass                         agreement")
    print('-' * 78)
    spread(seq_a, seq_b, 'sequential vs sequential')
    spread(par_a, par_b, 'parallel   vs parallel')
    spread(seq_a, par_a, 'sequential vs parallel')
    spread(seq_b, par_b, 'sequential vs parallel (2)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
