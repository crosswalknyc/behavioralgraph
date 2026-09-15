"""Concurrency sweep for lens_relevance batch scoring.

Scores the SAME fixed slice of real collected items for ONE lens at
several worker counts. Lens batches are a plain Sonnet turn with no
tool use, so they behave differently from the web_search calls in
headline_estimates and need their own measurement.

    python3 -m scripts.trends_scrapers.bench_lens --n 1000 --levels 8,16,24,32

Cost is (n / batch_size) x len(levels) calls. Keep n modest.
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

from scripts.trends_scrapers import lens_relevance as lr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=1000)
    ap.add_argument('--lens', default='gen_z')
    ap.add_argument('--levels', default='8,16,24,32')
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    import anthropic  # type: ignore
    client = anthropic.Anthropic(
        api_key=(os.environ.get('ANTHROPIC_API_KEY') or '').strip())

    lens = next(l for l in lr._LENSES if l['id'] == args.lens)
    items = lr._collect_all_items()[:args.n]
    nb = (len(items) + lr._BATCH_SIZE - 1) // lr._BATCH_SIZE
    print(f"sweep: lens={args.lens} items={len(items)} batches={nb}\n")
    print(f"{'workers':>8} {'wall_s':>9} {'batches/min':>12} "
          f"{'scored':>7} {'missing':>8}")

    for lvl in [int(x) for x in args.levels.split(',') if x.strip()]:
        t0 = time.time()
        out = lr._score_all_lenses(client, [(lens, items)], workers=lvl)
        dt = time.time() - t0
        n = len(out.get(args.lens) or {})
        print(f"{lvl:>8} {dt:>9.1f} {60.0 * nb / max(dt, 1e-9):>12.1f} "
              f"{n:>7} {len(items) - n:>8}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
