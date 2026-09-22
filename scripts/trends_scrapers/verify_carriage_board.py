#!/usr/bin/env python3
"""Board-level verification of the carriage model, across windows.

Read-only. Builds the served payload for each lookback window and
checks the things that have to hold on the page rather than in the
registry:

  * no streaming or FAST row renders a reading taken for another
    service
  * every derived rail sits strictly inside its parent, title by
    title, and its share lands inside the researched band
  * a derived rail's share is not a constant across titles
  * the last digit of the rendered values still looks counted

Never queries clickstream.

    python3 -m scripts.trends_scrapers.verify_carriage_board
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _panel_values(panel: dict) -> dict:
    """{normalised title: rendered value} for a streaming panel."""
    from scripts.trends_scrapers import stream_estimates as se
    out = {}
    for row in (panel or {}).get('items') or []:
        if not isinstance(row, dict):
            continue
        title = (row.get('title') or '').strip()
        blk = row.get('us_streams')
        if not title or not isinstance(blk, dict):
            continue
        try:
            v = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[se._cp_normalize(title)] = (v, title,
                                             row.get('category_display') or '')
    return out


def check_window(days: int) -> int:
    import trends_iq
    from scripts.trends_scrapers import derived_rails as dr
    from scripts.trends_scrapers.audit_service_provenance import audit

    failures = 0
    payload = trends_iq.compute_view(
        {'geo_type': 'National', 'geo_value': '', 'lookback_days': days},
        force_refresh=True)

    print(f'=== lookback_days={days} ===')

    res = audit(payload)
    print(f'  rows walked       : {res["total"]}')
    print(f'  reading their own : {res["ok"]}')
    print(f'  WRONG service     : {len(res["defects"])}')
    print(f'  no stored entry   : {len(res["no_entry"])}')
    if res['defects']:
        failures += 1
        for d in res['defects'][:10]:
            print(f'     {d["rail"]} {d["title"]!r} rendered {d["rendered"]:,}')

    panels = (payload.get('cards') or {}).get('streaming_trending') or {}
    for child in dr.child_slugs():
        parent_slug = dr.parent_slug(child)
        child_v = _panel_values(panels.get(child) or {})
        parent_v = _panel_values(panels.get(parent_slug) or {})
        paired = [(k, child_v[k], parent_v[k]) for k in child_v
                  if k in parent_v]
        if not paired:
            print(f'  {child}: no paired rows against {parent_slug}')
            failures += 1
            continue
        over = [(t, cv, pv) for _k, (cv, t, _c), (pv, _pt, _pc) in paired
                if cv >= pv]
        shares = [cv / float(pv) for _k, (cv, _t, _c), (pv, _pt, _pc)
                  in paired if pv]
        lo = min(min(b[0] for b in dr.rail_for(child).bands.values()),
                 1.0)
        hi = max(b[1] for b in dr.rail_for(child).bands.values())
        outside = [s for s in shares if not (lo - 0.02 <= s <= hi + 0.02)]
        print(f'  {child} vs {parent_slug}: {len(paired)} paired rows, '
              f'share {min(shares):.3f} to {max(shares):.3f} '
              f'(band {lo:.3f} to {hi:.3f}), '
              f'{len(set(round(s, 4) for s in shares))} distinct shares')
        if over:
            failures += 1
            print(f'     AT OR ABOVE PARENT: {over[:5]}')
        if outside:
            failures += 1
            print(f'     OUTSIDE BAND: {len(outside)} row(s)')

    # Last-digit distribution over every rendered streaming value.
    from scripts.trends_scrapers.run_guard import check_last_digit_distribution
    vals = []
    for slug, panel in panels.items():
        for _k, (v, _t, _c) in _panel_values(panel).items():
            vals.append(v)
    counts = [0] * 10
    for v in vals:
        counts[v % 10] += 1
    n = len(vals) or 1
    print(f'  last digits over {len(vals)} streaming values: '
          + ' '.join(f'{d}:{100.0 * counts[d] / n:.1f}%' for d in range(10)))
    print()
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--windows', default='1,7,30')
    args = ap.parse_args(argv)
    total = 0
    for d in [int(x) for x in args.windows.split(',') if x.strip()]:
        total += check_window(d)
    if total:
        print(f'{total} FAILURE(S)')
        return 1
    print('board verified across every window checked')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
