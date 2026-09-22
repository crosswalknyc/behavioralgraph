#!/usr/bin/env python3
"""Which streaming / FAST rows render a number taken for another service.

A row on service X must render a reading researched for service X. The
annotator falls back to the title's cross-platform total when the stored
entry carries no block for X, so the row shows a real reading of a real
title that is not about the service the row claims. This walks the served
board and names every row in that state.

Derived rails are excluded by construction: a derived rail takes its value
from its parent by design and has no reading of its own to be missing.

Read-only. Never queries clickstream.

    python3 -m scripts.trends_scrapers.audit_service_provenance --json /tmp/a.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _rows(payload: dict):
    """(rail_root, panel_slug, bucket, index, row) for streaming + FAST."""
    cards = (payload or {}).get('cards') or {}
    for root in ('streaming_trending', 'fast_trending'):
        node = cards.get(root) or {}
        if not isinstance(node, dict):
            continue
        for slug, panel in node.items():
            if not isinstance(panel, dict):
                continue
            for bucket, lst in panel.items():
                if not isinstance(lst, list):
                    continue
                for i, row in enumerate(lst):
                    if isinstance(row, dict) and (row.get('title')
                                                  or row.get('name')):
                        yield root, slug, bucket, i, row


def audit(payload: dict) -> dict:
    import trends_iq as tiq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import derived_rails as dr

    items = (se._read_snapshot('stream_estimates') or {}).get('items') or {}

    out = {'defects': [], 'derived_skipped': 0, 'no_entry': [],
           'ok': 0, 'no_platform_key': 0, 'total': 0,
           'by_rail': defaultdict(lambda: {'total': 0, 'defect': 0,
                                           'no_entry': 0, 'derived': 0})}

    for root, slug, bucket, idx, row in _rows(payload):
        # Each row object is shared across buckets (items/films/tv), so
        # count the authoritative bucket only.
        if root == 'streaming_trending' and bucket not in (
                'items', 'global_films_en', 'global_films_nonen',
                'global_tv_en', 'global_tv_nonen'):
            continue
        if root == 'fast_trending' and bucket not in ('items', 'channels'):
            continue

        out['total'] += 1
        rail = out['by_rail'][slug]
        rail['total'] += 1

        if dr.is_derived_rail(slug):
            out['derived_skipped'] += 1
            rail['derived'] += 1
            continue

        title = (row.get('title') or row.get('name') or '').strip()
        cat = (row.get('category_display') or '').lower()
        norm = se._cp_normalize(title)
        if not norm:
            continue

        if bucket == 'channels':
            platform_key = slug
            order = [f'fast_channel:{slug}:{norm}']
        elif root == 'fast_trending':
            platform_key = tiq._FAST_PANEL_TO_PLATFORM.get(slug, '')
            order = ([f'fast_film:{norm}', f'fast_tv:{norm}'] if cat == 'film'
                     else [f'fast_tv:{norm}', f'fast_film:{norm}'])
        else:
            platform_key = tiq._STREAMING_PANEL_TO_PLATFORM.get(slug, '')
            if 'film' in cat:
                order = [f'film:{norm}', f'tv:{norm}', f'title:{norm}']
            elif 'tv' in cat:
                order = [f'tv:{norm}', f'film:{norm}', f'title:{norm}']
            else:
                order = [f'title:{norm}', f'film:{norm}', f'tv:{norm}']

        if not platform_key:
            out['no_platform_key'] += 1
            continue

        entry_key = next((k for k in order if k in items), '')
        blk = row.get('us_streams') or {}
        try:
            rendered = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            rendered = 0

        rec = {
            'rail': slug, 'root': root, 'bucket': bucket,
            'title': title, 'category': row.get('category_display') or '',
            'rank': row.get('rank') or (idx + 1),
            'platform_key': platform_key, 'entry_key': entry_key,
            'rendered': rendered,
            'est_basis': blk.get('est_basis') or '',
            'keys_tried': order,
        }

        if not entry_key:
            out['no_entry'].append(rec)
            rail['no_entry'] += 1
            continue

        entry = items.get(entry_key) or {}
        bp = entry.get('by_platform') or {}
        has = bp.get(platform_key)
        if isinstance(has, dict) and (has.get('us_estimate') or 0) > 0:
            out['ok'] += 1
            continue

        rec['entry_total'] = entry.get('us_estimate')
        rec['has_blocks'] = sorted(
            k for k, v in bp.items()
            if isinstance(v, dict) and (v.get('us_estimate') or 0) > 0)
        out['defects'].append(rec)
        rail['defect'] += 1

    out['by_rail'] = {k: v for k, v in sorted(out['by_rail'].items())}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    args = ap.parse_args(argv)

    import trends_iq as tiq
    payload = tiq.compute_view({'geo_type': 'National', 'geo_value': '',
                                'lookback_days': 1}, force_refresh=True)
    res = audit(payload)

    print(f"rows walked      : {res['total']}")
    print(f"correct service  : {res['ok']}")
    print(f"WRONG service    : {len(res['defects'])}")
    print(f"no stored entry  : {len(res['no_entry'])}")
    print(f"derived (skipped): {res['derived_skipped']}")
    print(f"no service key   : {res['no_platform_key']}")
    print()
    print(f"{'rail':<20} {'total':>6} {'defect':>7} {'noentry':>8} {'derived':>8}")
    for slug, v in res['by_rail'].items():
        if v['defect'] or v['no_entry'] or v['derived']:
            print(f"{slug:<20} {v['total']:>6} {v['defect']:>7} "
                  f"{v['no_entry']:>8} {v['derived']:>8}")

    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(res, fh, indent=1, default=str)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
