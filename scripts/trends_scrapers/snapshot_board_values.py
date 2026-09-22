#!/usr/bin/env python3
"""Every rendered value on the board, so a change can be proved exactly.

Walks the served payload the way the coverage gate does and writes one
record per row: where it renders, what it is, what it shows, how it came
by that number, and where it ranks. Diffing two of these says precisely
which rows moved and which did not.

Read-only. Never queries clickstream.

    python3 -m scripts.trends_scrapers.snapshot_board_values --out /tmp/b.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_FILTERS = {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1}


def snapshot(lookback_days: int = 1) -> dict:
    import trends_iq as tiq
    from scripts.trends_scrapers.coverage_gate import (
        _walk_rendered, _item_title, _audience_state)

    filters = dict(_FILTERS)
    filters['lookback_days'] = lookback_days
    payload = tiq.compute_view(filters, force_refresh=True)
    cards = (payload or {}).get('cards') or {}

    rows = {}
    dupes = 0
    for path, rank, it in _walk_rendered(cards):
        title = _item_title(it)
        blk = it.get('us_streams') or it.get('us_readers') or {}
        if not isinstance(blk, dict):
            blk = {}
        try:
            val = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            val = 0
        key = f'{path}|{title}'
        if key in rows:
            dupes += 1
            key = f'{key}|#{rank}'
        rows[key] = {
            'path': path,
            'title': title,
            'rank': rank,
            'value': val,
            'basis': blk.get('est_basis') or 'researched',
            'platform': blk.get('platform') or '',
            'direction': blk.get('direction') or '',
            'delta_pct': blk.get('delta_pct'),
            'category': it.get('category_display') or '',
        }
    return {'rows': rows, 'count': len(rows), 'dupe_keys': dupes,
            'lookback_days': lookback_days,
            'sections': sorted({v['path'] for v in rows.values()})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--lookback', type=int, default=1)
    args = ap.parse_args(argv)
    snap = snapshot(args.lookback)
    with open(args.out, 'w') as fh:
        json.dump(snap, fh, indent=0, default=str)
    print(f"rows={snap['count']} sections={len(snap['sections'])} "
          f"dupe_keys={snap['dupe_keys']} -> {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
