#!/usr/bin/env python3
"""Read the served Trends IQ payload and report, per panel, how often a
row displaying an audience figure sits above a row showing a bigger one.

A list that puts a number in front of the reader has to read descending
by that number. This counts the pairs where it does not (adjacent
inversions), reports the share of rows still sitting on the last-resort
baseline, and flags any row above its own platform's published cap.

  python3 -m scripts.trends_scrapers.audit_panel_ordering            # 1 day
  python3 -m scripts.trends_scrapers.audit_panel_ordering --days 7
  python3 -m scripts.trends_scrapers.audit_panel_ordering --json OUT
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


# Every card family whose rows put an audience figure on the page.
_PANEL_KEYS = (
    'music_trending', 'podcasts_trending', 'books_trending',
    'comics_trending', 'libby_trending', 'gaming_trending',
    'broadway_trending', 'films_ticketing',
    'streaming_trending', 'fast_trending',
)

_VALUE_BLOCKS = ('us_streams', 'us_readers')


def row_value(row):
    if not isinstance(row, dict):
        return None
    for f in _VALUE_BLOCKS:
        blk = row.get(f)
        if isinstance(blk, dict):
            try:
                v = int(float(blk.get('us_estimate') or 0))
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
    return None


def row_basis(row):
    for f in _VALUE_BLOCKS:
        blk = row.get(f)
        if isinstance(blk, dict) and blk.get('us_estimate'):
            return str(blk.get('est_basis') or '')
    return ''


def iter_lists(node, path=''):
    """Yield (path, list-of-row-dicts) for every list of rows."""
    if isinstance(node, list):
        rows = [r for r in node if isinstance(r, dict)]
        if rows and any(row_value(r) is not None for r in rows):
            yield path, node
        for i, x in enumerate(node):
            if isinstance(x, dict):
                yield from iter_lists(x, f'{path}[{i}]')
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from iter_lists(v, f'{path}.{k}' if path else str(k))


def inversions(rows):
    """Adjacent pairs where a row sits above one showing a bigger
    number. Rows with no figure are skipped, not counted against the
    ordering."""
    vals = [row_value(r) for r in rows if isinstance(r, dict)]
    vals = [v for v in vals if v is not None]
    return sum(1 for a, b in zip(vals, vals[1:]) if b > a)


def rank_defects(rows):
    """(non_dense, duplicate) rank problems on a list."""
    ranks = [r.get('rank') for r in rows if isinstance(r, dict)]
    ints = [r for r in ranks if isinstance(r, int)]
    dup = len(ints) - len(set(ints))
    dense = ints == list(range(1, len(ints) + 1))
    return (0 if dense else 1), dup


def audit(cards):
    out = {}
    for panel in _PANEL_KEYS:
        block = (cards or {}).get(panel)
        if not isinstance(block, (dict, list)):
            continue
        per_source = {}
        for path, rows in iter_lists(block, panel):
            src = path.split('.')[1] if '.' in path else path
            d = per_source.setdefault(src, {
                'inversions': 0, 'rows': 0, 'valued': 0,
                'rank_tier': 0, 'carried': 0, 'lists': 0,
                'non_dense': 0, 'dup_ranks': 0,
            })
            rowdicts = [r for r in rows if isinstance(r, dict)]
            d['lists'] += 1
            d['inversions'] += inversions(rowdicts)
            d['rows'] += len(rowdicts)
            nd, dup = rank_defects(rowdicts)
            d['non_dense'] += nd
            d['dup_ranks'] += dup
            for r in rowdicts:
                if row_value(r) is not None:
                    d['valued'] += 1
                b = row_basis(r)
                if b == 'rank_tier':
                    d['rank_tier'] += 1
                elif b in ('carried_forward', 'carry_forward'):
                    d['carried'] += 1
        if per_source:
            out[panel] = per_source
    return out


def sweep_caps(cards):
    """Every row rendering above its own platform's published daily
    cap. Read-only: reports, never corrects."""
    import trends_iq as tq
    out = []
    roots = _PANEL_KEYS

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v, f'{path}.{k}' if path else k)
            return
        if isinstance(node, list):
            for x in node:
                if isinstance(x, dict):
                    check(x, path)
                    walk(x, path)

    def check(it, path):
        v = row_value(it)
        if not v:
            return
        kind = tq._coverage_kind_for_path(path, it)
        try:
            plat = tq._coverage_platform_for_path(path)
        except AttributeError:
            plat = tq._carry_platform_for_path(path)
        try:
            cap = tq._platform_daily_cap(kind, plat)
        except AttributeError:
            cap = _cap_fallback(kind, plat)
        if cap:
            # A window wider than a day sums the days the item
            # appeared, so the cap covers the same days.
            blk = it.get('us_streams') or it.get('us_readers') or {}
            days = 1
            for f in ('window_days_covered', 'window_days_total'):
                try:
                    n = int(blk.get(f) or 0)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    days = n
                    break
            cap *= days
        if cap and v > cap:
            out.append((path, it.get('title') or it.get('name') or '',
                        v, cap, round(v / cap, 2)))

    for root in roots:
        node = (cards or {}).get(root)
        if isinstance(node, (dict, list)):
            walk(node, root)
    out.sort(key=lambda t: -t[4])
    return out


def _cap_fallback(kind, plat):
    """Cap lookup for a build that predates the cap pass."""
    if not kind or not plat:
        return None
    try:
        from scripts.trends_scrapers.stream_estimates import \
            _platforms_for_kind
    except Exception:
        return None
    for p in _platforms_for_kind(kind) or []:
        if p.get('key') == plat:
            w = int(p.get('ceiling') or 0)
            return max(1, w // 7) if w else None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--json', dest='json_out', default='')
    args = ap.parse_args()

    import trends_iq
    payload = trends_iq.compute_view({
        'geo_type': 'National', 'geo_value': '',
        'lookback_days': args.days,
    })
    report = audit(payload.get('cards') or {})

    print('--- rows above their own platform cap ---')
    breaches = sweep_caps(payload.get('cards') or {})
    for path, title, v, cap, mult in breaches:
        print(f'  {mult}x  {path}  {title}  {v:,} vs cap {cap:,}')
    print(f'  total breaches: {len(breaches)}')

    print('--- last-resort share per panel source ---')
    for panel, sources in sorted(report.items()):
        for src, s in sorted(sources.items()):
            if not s['rows']:
                continue
            pct = 100.0 * s['rank_tier'] / s['rows']
            if s['rank_tier']:
                print(f"  {panel}.{src}: {s['rank_tier']}/{s['rows']} "
                      f"= {pct:.1f}%")
    print('--- ordering ---')

    total_inv = 0
    for panel, sources in sorted(report.items()):
        panel_inv = sum(s['inversions'] for s in sources.values())
        total_inv += panel_inv
        flag = 'OK ' if panel_inv == 0 else '>>>'
        print(f'{flag} {panel}: {panel_inv} inversion(s)')
        for src, s in sorted(sources.items(),
                             key=lambda t: -t[1]['inversions']):
            if not s['rows']:
                continue
            base = (f" rank_tier={s['rank_tier']}"
                    if s['rank_tier'] else '')
            carry = f" carried={s['carried']}" if s['carried'] else ''
            defect = ''
            if s['non_dense'] or s['dup_ranks']:
                defect = (f" non_dense={s['non_dense']}"
                          f" dup_ranks={s['dup_ranks']}")
            print(f"      {src}: inv={s['inversions']} "
                  f"rows={s['rows']} valued={s['valued']}"
                  f"{base}{carry}{defect}")
    print(f'TOTAL inversions (days={args.days}): {total_inv}')

    if args.json_out:
        with open(args.json_out, 'w') as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        print(f'wrote {args.json_out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
