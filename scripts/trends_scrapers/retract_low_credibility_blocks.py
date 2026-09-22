#!/usr/bin/env python3
"""Retract service readings that landed below their own service's floor.

One-off. The narrow per-service merge now refuses a reading that lands
an order of magnitude below the bottom of that service's own priced
rows, because a title cannot chart on a service and read far under
everything else charting there. This takes the blocks written before
that floor existed back out, so the rows fall to a number about their
own service and tonight's pass researches them again.

Read-only against everything except the blocks it names.

    python3 -m scripts.trends_scrapers.retract_low_credibility_blocks --dry-run
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--trail', default='/tmp/service_provenance_trail.json')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base
    from scripts.trends_scrapers.coverage_gate import \
        _rail_credibility_floors

    with open(args.trail) as fh:
        trail = json.load(fh)

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    floors = _rail_credibility_floors(items)

    retract = []
    for row in trail:
        key, plat = row['entry_key'], row['platform']
        entry = items.get(key)
        if not isinstance(entry, dict):
            continue
        blk = (entry.get('by_platform') or {}).get(plat)
        if not isinstance(blk, dict):
            continue
        try:
            v = int(blk.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        floor = max(100, floors.get(plat, 0))
        if v < floor:
            retract.append((key, plat, row['title'], v, floor,
                            row.get('stored_was')))

    print(f'service readings written : {len(trail)}')
    print(f'below their service floor: {len(retract)}')
    for key, plat, title, v, floor, was in retract:
        print(f'   {title[:34]:<34} {plat:<14} {v:>9,}  floor {floor:>9,}'
              f'   (no earlier reading)' if not was else
              f'   {title[:34]:<34} {plat:<14} {v:>9,}  floor {floor:>9,}'
              f'   was {was:,}')
    if not retract or args.dry_run:
        print('dry-run: nothing written' if args.dry_run else
              'nothing to retract')
        return 0

    for key, plat, _t, _v, _f, was in retract:
        entry = items[key]
        blocks = dict(entry.get('by_platform') or {})
        if was:
            # There was a reading here before this pass. Put it back
            # rather than leaving the service blank.
            prev = blocks.get(plat)
            if isinstance(prev, dict):
                prev = dict(prev)
                prev['us_estimate'] = int(was)
                blocks[plat] = prev
        else:
            blocks.pop(plat, None)
        entry['by_platform'] = blocks
        items[key] = entry

    snap['items'] = items
    snap['count'] = len(items)
    _base.write_snapshot('stream_estimates', snap)
    print(f'retracted {len(retract)} reading(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
