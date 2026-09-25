"""Blank-count report per rendered rail.

Read-only. Recomputes the payload the dashboard renders and counts the
rows the coverage gate would call blank, grouped by rail, so a fix can
be measured before and after.

    python3 -m scripts.trends_scrapers._blank_report
    python3 -m scripts.trends_scrapers._blank_report --cached
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def build(force_refresh: bool = True, lookback: int = 1) -> dict:
    import trends_iq
    return trends_iq.compute_view(
        {'geo_type': 'National', 'geo_value': '', 'lookback_days': lookback},
        force_refresh=force_refresh)


def report(payload: dict, show: int = 6) -> dict:
    from scripts.trends_scrapers.coverage_gate import (
        _walk_rendered, _audience_state, _item_title, _EXEMPT_PREFIXES,
        _fused_row_is_film_only)

    by_rail: dict[str, dict] = {}
    for path, rank, it in _walk_rendered((payload or {}).get('cards') or {}):
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        if path.startswith('fused_trending') and _fused_row_is_film_only(it):
            continue
        b = by_rail.setdefault(path, {'total': 0, 'blank': 0, 'rows': []})
        b['total'] += 1
        if _audience_state(it) == 'missing':
            b['blank'] += 1
            b['rows'].append((rank, _item_title(it)))
    return by_rail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cached', action='store_true',
                    help='reuse the cached payload instead of recomputing')
    ap.add_argument('--lookback', type=int, default=1)
    ap.add_argument('--filter', default='',
                    help='only print rails whose path contains this')
    ap.add_argument('--json-out', default='')
    ap.add_argument('--show', type=int, default=6)
    a = ap.parse_args(argv)

    payload = build(force_refresh=not a.cached, lookback=a.lookback)
    by_rail = report(payload)

    tot = sum(v['total'] for v in by_rail.values())
    bl = sum(v['blank'] for v in by_rail.values())
    print(f'TOTAL rows={tot} blank={bl} '
          f'covered={100.0 * (tot - bl) / tot:.2f}%' if tot else 'no rows')
    for path in sorted(by_rail):
        v = by_rail[path]
        if not v['blank']:
            continue
        if a.filter and a.filter not in path:
            continue
        print(f'  {path}: {v["blank"]}/{v["total"]} blank')
        for rank, title in v['rows'][:a.show]:
            print(f'      #{rank} {title!r}')
        if len(v['rows']) > a.show:
            print(f'      ... and {len(v["rows"]) - a.show} more')

    if a.json_out:
        with open(a.json_out, 'w') as fh:
            json.dump({k: {'total': v['total'], 'blank': v['blank'],
                           'rows': v['rows']} for k, v in by_rail.items()},
                      fh, indent=1)
        print(f'wrote {a.json_out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
