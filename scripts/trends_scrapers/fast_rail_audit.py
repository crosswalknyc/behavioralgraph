#!/usr/bin/env python3
"""Is a FAST rail ranking its channels, or ranking the alphabet?

Four platforms publish no schedule, so the lineup order they emit is
the source page's own, which is alphabetical. When that position leaks
into the research step as a rank, the board opens on whatever the
platform happens to list first. This measures whether it has.

Per rail: the top ten, the spread across the field and across the top
five, and the mean first-letter position of the top twenty against the
same statistic over the whole rail. The rail-wide figure is the null.
A top twenty sitting near it means position in the alphabet is not
driving the order; a top twenty far below it means it still is.

Reads the value the board renders, which is the channel's own
per-platform reading, falling back to the aggregate the way the render
path does.

Read-only. Never queries clickstream
(`.cursor/rules/trends-rankers-never-clickstream.mdc`).

    python3 -m scripts.trends_scrapers.fast_rail_audit --scheduled
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

SCHEDULELESS = ('philo', 'plex', 'sling_freestream', 'directv_myfree')
SCHEDULED = ('roku', 'vizio', 'pluto', 'tubi', 'amazon', 'lg')


def letter_pos(name: str) -> int:
    """1 for A through 26 for Z; 0 for a digit or a symbol, which sort
    ahead of A on the pages these lineups come from."""
    for ch in (name or '').strip():
        if ch.isalpha():
            o = ord(ch.lower()) - 96
            return o if 1 <= o <= 26 else 0
        if ch.isdigit():
            return 0
    return 0


def rendered_value(entry: dict, slug: str) -> int:
    """What the board shows: the channel's own reading on its own
    platform, or the aggregate when no such block exists."""
    blk = (entry.get('by_platform') or {}).get(slug) or {}
    for src in (blk, entry):
        try:
            v = int(src.get('us_estimate') or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v
    return 0


def rail_rows(items: dict, slug: str) -> list:
    prefix = f'fast_channel:{slug}:'
    out = [((e.get('display_title') or '').strip(), rendered_value(e, slug))
           for k, e in (items or {}).items()
           if k.startswith(prefix) and isinstance(e, dict)]
    out.sort(key=lambda r: -r[1])
    return out


def lineup_counts(se) -> dict:
    """Channels the collector actually returns per rail, so overflow
    carrying no value is visible rather than merely absent."""
    counts: dict = {}
    for it in se._collect_fast_channels():
        counts[it['fast_platform']] = counts.get(it['fast_platform'], 0) + 1
    return counts


def audit(slugs) -> dict:
    from scripts.trends_scrapers import stream_estimates as se
    items = (se._read_snapshot('stream_estimates') or {}).get('items') or {}
    collected = lineup_counts(se)
    ceilings = {p['key']: p['ceiling']
                for p in se._FAST_CHANNEL_PLATFORMS_META}
    report: dict = {}
    for slug in slugs:
        rows = rail_rows(items, slug)
        priced = [r for r in rows if r[1] > 0]
        if not priced:
            report[slug] = {'priced': 0, 'collected': collected.get(slug, 0)}
            continue
        vals = [v for _n, v in priced]
        top5, top20 = vals[:5], priced[:20]
        daily_ceiling = max(1, int(ceilings.get(slug, 0) / 7))
        report[slug] = {
            'collected':       collected.get(slug, 0),
            'priced':          len(priced),
            'unpriced':        max(0, collected.get(slug, 0) - len(priced)),
            'top10':           priced[:10],
            'max':             vals[0],
            'min':             vals[-1],
            'field_ratio':     round(vals[0] / max(1, vals[-1]), 1),
            'top5_hi':         top5[0],
            'top5_lo':         top5[-1],
            'top5_ratio':      round(top5[0] / max(1, top5[-1]), 3),
            'top5_spread_pct': round(
                100.0 * (top5[0] - top5[-1]) / max(1, top5[0]), 1),
            'alpha_top20':     round(statistics.fmean(
                letter_pos(n) for n, _v in top20), 2),
            'alpha_rail':      round(statistics.fmean(
                letter_pos(n) for n, _v in priced), 2),
            'daily_ceiling':   daily_ceiling,
            'over_ceiling':    sum(1 for v in vals if v > daily_ceiling),
        }
    return report


def render(report: dict, title: str) -> None:
    print(f'\n{"=" * 76}\n{title}\n{"=" * 76}')
    for slug, r in report.items():
        if not r.get('priced'):
            print(f'\n--- {slug}: nothing priced '
                  f'({r.get("collected", 0)} collected) ---')
            continue
        print(f'\n--- {slug} --- {r["priced"]} priced of {r["collected"]} '
              f'collected ({r["unpriced"]} unpriced)')
        for i, (name, v) in enumerate(r['top10'], 1):
            print(f'   {i:>2}. {v:>9,}  {name}')
        print(f'   field       : {r["max"]:,} .. {r["min"]:,} '
              f'({r["field_ratio"]}x)')
        print(f'   top five    : {r["top5_hi"]:,} .. {r["top5_lo"]:,} '
              f'({r["top5_ratio"]}x, {r["top5_spread_pct"]}% spread)')
        print(f'   alphabet    : top20 mean {r["alpha_top20"]}   '
              f'rail mean {r["alpha_rail"]}')
        print(f'   ceiling     : {r["daily_ceiling"]:,}/day, '
              f'{r["over_ceiling"]} above it')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    ap.add_argument('--label', default='FAST rail audit')
    ap.add_argument('--scheduled', action='store_true',
                    help='include the rails that do publish a schedule, '
                         'as a control')
    args = ap.parse_args(argv)

    slugs = list(SCHEDULELESS) + (list(SCHEDULED) if args.scheduled else [])
    report = audit(slugs)
    render(report, args.label)
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(report, fh, indent=1)
        print(f'\njson -> {args.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
