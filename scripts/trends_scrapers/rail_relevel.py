#!/usr/bin/env python3
"""Re-level a streaming rail to an engagement target, order preserved.

Why this exists (2026-10-06)
----------------------------
A partner's title-level streams for Lionsgate+ and MovieSphere+ showed
the reasoned chain behind the smaller services ran 5 to 10x high: too
many accounts assumed active on a day, and the #1 title assumed to take
too much of that day. The same chain built every other reasoned-only
rail, so the same test was run on all of them: views in the rail's top
100 per subscriber account per day. The three best-anchored majors sit
near 0.3 to 0.4; the add-ons sat at 1.0 to 1.7, which would have every
subscriber watching one or two titles from that one service every day.
The measured add-ons run nearer 0.1 to 0.2.

What it does
------------
For each rail in the plan, the top-100 sum is set to
`accounts x band` (views per account per day). Two ways to distribute
that across the rows:

  measured      the rows take the measured add-on curve (the average of
                the two partner feeds, normalised to #1: #10 at 0.36,
                #100 at 0.047) in the order the rail already has them.
                Film and series libraries.
  proportional  every row scaled by one factor; the rail keeps its own
                shape. Majors and event-driven services, whose curve is
                series-led and nothing like a film library's.

Both keep every row's position, so chart order, the catalog floor and
the sibling rails are untouched by construction; the finalize chain
then runs as usual. Salted +/-3% per row and natural last digits so no
two rails share a signature. Forward-only: today's board and today's
dated copy; history is never rewritten.

Run (from bg-webapp, with the trends env loaded):
    PYTHONPATH=. python3 -m scripts.trends_scrapers.rail_relevel --dry-run
    PYTHONPATH=. python3 -m scripts.trends_scrapers.rail_relevel
    PYTHONPATH=. python3 -m scripts.trends_scrapers.rail_relevel --slug starz
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)

# slug -> (US subscriber accounts, top-100 views per account per day, method)
PLAN_2026_10_06: dict[str, tuple[int, float, str]] = {
    'britbox':       (3_000_000, 0.20, 'measured'),
    'mgmplus':       (4_400_000, 0.20, 'measured'),
    'amcplus':       (11_000_000, 0.20, 'measured'),
    'starz':         (12_700_000, 0.22, 'measured'),
    'espnplus':      (24_100_000, 0.24, 'proportional'),
    'hulu':          (55_000_000, 0.40, 'proportional'),
    'disneyplus':    (57_000_000, 0.38, 'proportional'),
    'paramountplus': (42_000_000, 0.42, 'proportional'),
    'peacock':       (40_000_000, 0.40, 'proportional'),
}

ROW_SALT_BAND = 0.03


def _h01(salt: str) -> float:
    h = int(hashlib.sha256(salt.encode('utf-8')).hexdigest()[:12], 16)
    return h / float(0xFFFFFFFFFFFF + 1)


def measured_curve() -> list[float]:
    """The partner feeds' rank curve, normalised to #1 and averaged."""
    from scripts.trends_scrapers import first_party_calibration as fpc
    curves = []
    for slug in fpc.CALIBRATED_SLUGS:
        doc = fpc.load(slug)
        bands = (doc or {}).get('bands') or []
        if len(bands) >= 100 and bands[0]:
            curves.append([x / float(bands[0]) for x in bands])
    if not curves:
        # The shape the two feeds carried on 2026-10-06, so the tool
        # still works without S3.
        pts = {1: 1.0, 5: 0.648, 10: 0.360, 25: 0.195, 50: 0.104,
               100: 0.047, 200: 0.018, 400: 0.006, 800: 0.001}
        ks = sorted(pts)
        out = []
        for r in range(1, 801):
            lo = max(k for k in ks if k <= r)
            hi = min(k for k in ks if k >= r)
            if lo == hi:
                out.append(pts[lo])
            else:
                t = (r - lo) / float(hi - lo)
                out.append(pts[lo] * (pts[hi] / pts[lo]) ** t)
        return out
    n = min(len(c) for c in curves)
    return [sum(c[i] for c in curves) / len(curves) for i in range(n)]


def rail_rows(items: dict, slug: str) -> list[tuple[str, dict, int]]:
    rows = []
    for key, it in items.items():
        if not isinstance(it, dict):
            continue
        blk = (it.get('by_platform') or {}).get(slug)
        if isinstance(blk, dict) and blk.get('us_estimate'):
            v = int(blk['us_estimate'])
            if v > 0:
                rows.append((key, it, v))
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


def relevel(items: dict, slug: str, accounts: int, band: float, method: str,
            target_date_iso: str, *, curve: Optional[list[float]] = None,
            dry_run: bool = False) -> dict:
    from scripts.trends_scrapers import stream_estimates as se
    rows = rail_rows(items, slug)
    if not rows:
        return {'slug': slug, 'rows': 0, 'moved': 0}
    cur_vals = [v for _, _, v in rows]
    cur_sum = sum(cur_vals[:100])
    target_sum = accounts * band
    if method == 'measured':
        g = curve or measured_curve()
        v1 = target_sum / sum(g[:100])
        new_vals = [v1 * g[min(i, len(g) - 1)] for i in range(len(rows))]
    else:
        f = target_sum / float(cur_sum)
        new_vals = [v * f for v in cur_vals]
    moved = 0
    prev: Optional[int] = None
    for (key, it, cur), nv in zip(rows, new_vals):
        salt = f'{target_date_iso}|relevel|{slug}|{key}'
        t = int(round(nv * (1.0 + ROW_SALT_BAND * (2.0 * _h01(salt) - 1.0))))
        t = max(1, se._natural_last_digits(max(1, t), str(it.get('display_title') or key), salt))
        if prev is not None and t >= prev:
            t = max(1, prev - 1 - int(prev * 0.004 * _h01(salt + '|dn')))
        prev = t
        if t == cur:
            continue
        if not dry_run:
            if se._set_platform_reading(it, slug, t, key, salt):
                moved += 1
        else:
            moved += 1
    out = {'slug': slug, 'rows': len(rows), 'moved': moved,
           'accounts': accounts, 'band': band, 'method': method,
           'top100_before': cur_sum, 'top100_after': int(round(target_sum)),
           'cut_x': round(cur_sum / float(target_sum), 2),
           'n1_before': cur_vals[0], 'n1_after': int(new_vals[0]),
           'n10_before': cur_vals[9] if len(cur_vals) > 9 else None,
           'n10_after': int(new_vals[9]) if len(new_vals) > 9 else None,
           'n100_before': cur_vals[99] if len(cur_vals) > 99 else None,
           'n100_after': int(new_vals[99]) if len(new_vals) > 99 else None}
    logger.info('relevel: %s', out)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--slug', action='append', default=[])
    ap.add_argument('--plan', help='JSON file {slug: [accounts, band, method]}')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    plan = dict(PLAN_2026_10_06)
    if args.plan:
        with open(args.plan) as fh:
            plan = {k: tuple(v) for k, v in json.load(fh).items()}
    if args.slug:
        plan = {k: v for k, v in plan.items() if k in set(args.slug)}
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base
    from scripts.trends_scrapers.run_guard import BoardLock
    with BoardLock('Rail re-level'):
        board = se._read_snapshot('stream_estimates') or {}
        items = board.get('items') or {}
        if not items:
            print('no store'); return 2
        day = board.get('target_date') or ''
        curve = measured_curve()
        results = [relevel(items, slug, acc, band, method, day, curve=curve,
                           dry_run=args.dry_run)
                   for slug, (acc, band, method) in plan.items()]
        for r in results:
            print(json.dumps(r))
        if args.dry_run:
            print('dry-run: nothing written'); return 0
        fin = se._finalize_published_charts(items, day)
        print('finalize:', {k: (v.get('moved') if isinstance(v, dict) else v)
                            for k, v in fin.items()})
        _base.write_snapshot('stream_estimates', board)
        try:
            import trends_iq
            n = trends_iq.invalidate_live_compute_view_caches()
            print(f'purged {n} live cache entries')
        except Exception:  # noqa: BLE001
            logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
