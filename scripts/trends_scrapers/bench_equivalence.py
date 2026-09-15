"""Output-equivalence check: sequential path vs parallel path.

Prices the SAME headlines twice, once with TRENDS_SCRAPERS_SEQUENTIAL=1
(one worker, no pool) and once at the shipped default, then compares
coverage, schema, and magnitude side by side.

The estimates themselves are a sampled model output with live
web_search behind them, so they are not bit-reproducible on any code
path, including the old one. What must hold is that the parallel path
loses nothing, invents nothing, keeps the same fields, and lands in
the same range on the same inputs.

    python3 -m scripts.trends_scrapers.bench_equivalence --n 25
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import _parallel                 # noqa: E402
from scripts.trends_scrapers import headline_estimates as he  # noqa: E402

_FIELDS = ('kind', 'display_title', 'source', 'url', 'us_estimate',
           'us_estimate_low', 'us_estimate_high', 'confidence',
           'unit_label', 'method', 'sources')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=25)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    items = he._collect_headlines()[:args.n]
    print(f"equivalence check over {len(items)} identical headlines\n")

    os.environ[_parallel.SEQUENTIAL_ENV] = '1'
    t0 = time.time()
    seq = he._research_all(items)
    seq_s = time.time() - t0
    os.environ.pop(_parallel.SEQUENTIAL_ENV)

    os.environ.pop(he._CONCURRENCY_ENV, None)
    t0 = time.time()
    par = he._research_all(items)
    par_s = time.time() - t0

    print(f"sequential : {len(seq):>3} priced in {seq_s:7.1f}s")
    print(f"parallel   : {len(par):>3} priced in {par_s:7.1f}s "
          f"({seq_s / max(par_s, 1e-9):.1f}x)\n")

    ks, kp = set(seq), set(par)
    print(f"same item count            : {len(ks) == len(kp)} "
          f"({len(ks)} vs {len(kp)})")
    print(f"same key set               : {ks == kp}")
    if ks - kp:
        print(f"  only in sequential: {sorted(ks - kp)[:5]}")
    if kp - ks:
        print(f"  only in parallel  : {sorted(kp - ks)[:5]}")

    exp = [he._lookup_key(i['display_title']) for i in items]
    print(f"parallel emits input order : "
          f"{[k for k in exp if k in par] == list(par)}")
    print(f"no duplicate work          : {len(par) == len(set(par))}")

    both = sorted(ks & kp)
    schema_ok = all(all(f in par[k] for f in _FIELDS) for k in both)
    print(f"schema fields all present  : {schema_ok}")
    ident_ok = all(seq[k]['display_title'] == par[k]['display_title']
                   and seq[k]['source'] == par[k]['source'] for k in both)
    print(f"identity fields identical  : {ident_ok}")
    order_ok = all(par[k]['us_estimate_low'] <= par[k]['us_estimate']
                   <= par[k]['us_estimate_high'] for k in both)
    print(f"low <= mid <= high holds   : {order_ok}")

    if both:
        sv = [seq[k]['us_estimate'] for k in both]
        pv = [par[k]['us_estimate'] for k in both]
        print(f"\nmedian estimate  seq={statistics.median(sv):>12,.0f}  "
              f"par={statistics.median(pv):>12,.0f}")
        ratios = sorted(max(a, b) / max(min(a, b), 1) for a, b in zip(sv, pv))
        within2 = sum(1 for r in ratios if r <= 2.0)
        print(f"within 2x of each other: {within2}/{len(ratios)} "
              f"({100.0 * within2 / len(ratios):.0f}%)   "
              f"median ratio={statistics.median(ratios):.2f}x")

    print(f"\n{'headline':<44} {'sequential':>13} {'parallel':>13}  conf")
    print('-' * 90)
    for k in both[:14]:
        print(f"{par[k]['display_title'][:44]:<44} "
              f"{seq[k]['us_estimate']:>13,} {par[k]['us_estimate']:>13,}  "
              f"{seq[k]['confidence']}/{par[k]['confidence']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
