"""Audit US Audience coverage across every Trends IQ panel.

Walks `compute_view({}, force_refresh=True)` and iterates every panel
in every tab. For each panel, counts rows and priced rows, then
prints per-panel coverage plus the list of unpriced item titles.

A row counts as priced when it carries any of:
  - it.us_streams.us_estimate  (integer > 0)
  - it.us_readers.us_estimate  (integer > 0)
  - it.holds                   (integer > 0)  # Libby raw signal fallback

Films panels are EXCLUDED from the "must be 100%" bar (per the ask).

Usage:
    cd bg-webapp
    python3 -m scripts.audit_trends_iq_us_audience_coverage \
        [--output /tmp/us_audience_coverage.txt] [--no-refresh]
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Callable


def _is_priced(it: dict) -> bool:
    if not isinstance(it, dict):
        return False
    s = it.get('us_streams') or {}
    if isinstance(s, dict):
        try:
            if int(s.get('us_estimate') or 0) > 0:
                return True
        except Exception:
            pass
    r = it.get('us_readers') or {}
    if isinstance(r, dict):
        try:
            if int(r.get('us_estimate') or 0) > 0:
                return True
        except Exception:
            pass
    try:
        if int(it.get('holds') or 0) > 0:
            return True
    except Exception:
        pass
    return False


def _row_title(it: dict) -> str:
    if not isinstance(it, dict):
        return str(it)
    for k in ('title', 'term', 'name', 'query', 'display_title', 'headline'):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return '(untitled)'


def _audit_panel_list(
    panel_id: str, rows: list, out_lines: list[str],
    exclude: bool = False,
) -> tuple[int, int]:
    """Return (total, priced) counts. Appends coverage line + unpriced
    titles to out_lines."""
    if not isinstance(rows, list):
        rows = list(rows or [])
    total = len(rows)
    priced = sum(1 for r in rows if _is_priced(r))
    unpriced = [_row_title(r) for r in rows if not _is_priced(r)]
    pct = (100.0 * priced / total) if total else 0.0
    tag = ' [EXCLUDED - films]' if exclude else ''
    out_lines.append(
        f'{panel_id}: {priced}/{total} ({pct:.1f}%){tag}'
    )
    if unpriced and total <= 200:
        # Show up to 40 unpriced titles per panel to keep output scannable.
        for t in unpriced[:40]:
            out_lines.append(f'    - {t}')
        if len(unpriced) > 40:
            out_lines.append(f'    ... and {len(unpriced) - 40} more')
    elif unpriced:
        out_lines.append(f'    ({len(unpriced)} unpriced titles omitted for brevity)')
    return total, priced


def _iter_source_panels(container: Any) -> list[tuple[str, list]]:
    """Return [(panel_slug, items_list), ...] for a container shaped
    like {'sources': {slug: {items: [...]}}} OR a flat
    {slug: {items: [...]}} dict."""
    if not isinstance(container, dict):
        return []
    src = container.get('sources')
    if isinstance(src, dict):
        parent = src
    else:
        parent = container
    out: list[tuple[str, list]] = []
    for slug, panel in parent.items():
        if not isinstance(panel, dict):
            continue
        items = panel.get('items') or []
        out.append((slug, items))
    return out


def audit(refresh: bool = True) -> tuple[list[str], dict]:
    """Return (report_lines, summary_dict)."""
    # Import here so command-line failures without bg-webapp on path
    # print a clean error instead of stack trace.
    from trends_iq import compute_view

    lines: list[str] = []
    lines.append('=' * 72)
    lines.append('Trends IQ US Audience coverage audit')
    lines.append('=' * 72)
    lines.append('')

    lines.append('Fetching compute_view({}, force_refresh=%s) ...' % refresh)
    payload = compute_view({}, force_refresh=refresh)
    cards = (payload or {}).get('cards') or {}
    lines.append('OK')
    lines.append('')

    totals = {'grand_total': 0, 'grand_priced': 0,
              'grand_total_ex_films': 0, 'grand_priced_ex_films': 0}
    def _acc(t: int, p: int, is_films: bool):
        totals['grand_total']  += t
        totals['grand_priced'] += p
        if not is_films:
            totals['grand_total_ex_films']  += t
            totals['grand_priced_ex_films'] += p

    # ---------- Trending overall (single-item fused card) ----------
    lines.append('## Trending Overall (fused)')
    fused = cards.get('fused_trending') or []
    if fused:
        # Single-panel treatment - the whole rail is one panel.
        t, p = _audit_panel_list('trending_overall/fused', fused, lines)
        _acc(t, p, False)
    else:
        lines.append('trending_overall/fused: 0/0 (empty)')
    lines.append('')

    # ---------- Trending searches (Search tab) ----------
    lines.append('## Search (Google Trends)')
    ts = cards.get('trending_searches') or []
    t, p = _audit_panel_list('search/trending_searches', ts, lines)
    _acc(t, p, False)
    # Per-category buckets from _bucket_searches_by_category
    tsc = cards.get('trending_searches_by_category') or {}
    for bucket, rows in sorted((tsc or {}).items()):
        t, p = _audit_panel_list(f'search/by_category/{bucket}', rows, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Movers (breakout / rising / falling / sustained) ----------
    lines.append('## Movers')
    movers = cards.get('movers') or {}
    for bucket in ('breakout', 'rising', 'falling', 'sustained'):
        rows = movers.get(bucket) or []
        t, p = _audit_panel_list(f'movers/{bucket}', rows, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Trending People / Wikipedia ----------
    lines.append('## Trending People')
    people = cards.get('trending_people') or []
    t, p = _audit_panel_list('trending_people', people, lines)
    _acc(t, p, False)
    wiki = cards.get('wikipedia_trending') or []
    t, p = _audit_panel_list('wikipedia_trending', wiki, lines)
    _acc(t, p, False)
    lines.append('')

    # ---------- Headlines ----------
    lines.append('## Headlines')
    heads = cards.get('trending_headlines') or []
    t, p = _audit_panel_list('headlines/overall', heads, lines)
    _acc(t, p, False)
    by_source = cards.get('articles_by_source') or []
    for src in by_source or []:
        if not isinstance(src, dict):
            continue
        slug = src.get('source') or src.get('slug') or 'unknown'
        arts = src.get('articles') or []
        t, p = _audit_panel_list(f'headlines/by_source/{slug}', arts, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Business / Wall Street / Philanthropy ----------
    lines.append('## Business')
    biz = cards.get('business_news') or []
    t, p = _audit_panel_list('business/national', biz, lines)
    _acc(t, p, False)
    biz_by = cards.get('business_news_by_source') or {}
    for slug, rows in sorted((biz_by or {}).items()):
        t, p = _audit_panel_list(f'business/by_source/{slug}', rows, lines)
        _acc(t, p, False)
    lines.append('')

    lines.append('## Wall Street')
    ws = cards.get('wall_street_news') or []
    t, p = _audit_panel_list('wall_street/national', ws, lines)
    _acc(t, p, False)
    ws_by = cards.get('wall_street_news_by_source') or {}
    for slug, rows in sorted((ws_by or {}).items()):
        t, p = _audit_panel_list(f'wall_street/by_source/{slug}', rows, lines)
        _acc(t, p, False)
    lines.append('')

    lines.append('## Philanthropy')
    phil = cards.get('philanthropy_news') or []
    t, p = _audit_panel_list('philanthropy/national', phil, lines)
    _acc(t, p, False)
    phil_by = cards.get('philanthropy_news_by_source') or {}
    for slug, rows in sorted((phil_by or {}).items()):
        t, p = _audit_panel_list(f'philanthropy/by_source/{slug}', rows, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Music ----------
    lines.append('## Music')
    for slug, items in _iter_source_panels(cards.get('music_trending') or {}):
        t, p = _audit_panel_list(f'music/{slug}', items, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Podcasts ----------
    lines.append('## Podcasts')
    for slug, items in _iter_source_panels(cards.get('podcasts_trending') or {}):
        t, p = _audit_panel_list(f'podcasts/{slug}', items, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Books ----------
    lines.append('## Books')
    for slug, items in _iter_source_panels(cards.get('books_trending') or {}):
        t, p = _audit_panel_list(f'books/{slug}', items, lines)
        _acc(t, p, False)
    # Libby folds into Books tab as sibling cards
    for slug, items in _iter_source_panels(cards.get('libby_trending') or {}):
        t, p = _audit_panel_list(f'books/libby_{slug}', items, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Comics ----------
    lines.append('## Comics')
    for slug, items in _iter_source_panels(cards.get('comics_trending') or {}):
        t, p = _audit_panel_list(f'comics/{slug}', items, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Streaming (per platform, films+tv+items buckets) ----------
    lines.append('## Streaming (SVOD)')
    for slug, panel in (cards.get('streaming_trending') or {}).items():
        if not isinstance(panel, dict):
            continue
        # Merge dedup by title so an item that appears in both `items`
        # and `films`/`tv` isn't double-counted.
        seen = set()
        merged = []
        for bucket in ('items', 'films', 'tv'):
            for r in panel.get(bucket) or []:
                if not isinstance(r, dict):
                    continue
                key = (r.get('title') or '').strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(r)
        t, p = _audit_panel_list(f'streaming/{slug}', merged, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- FAST titles ----------
    lines.append('## FAST (titles)')
    for slug, panel in (cards.get('fast_trending') or {}).items():
        if not isinstance(panel, dict):
            continue
        seen = set()
        merged = []
        for bucket in ('items', 'films', 'tv'):
            for r in panel.get(bucket) or []:
                if not isinstance(r, dict):
                    continue
                key = (r.get('title') or '').strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(r)
        t, p = _audit_panel_list(f'fast/{slug}/titles', merged, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- FAST channel ranker ----------
    lines.append('## FAST (channel ranker)')
    for slug, panel in (cards.get('fast_trending') or {}).items():
        if not isinstance(panel, dict):
            continue
        channels = panel.get('channels') or []
        # Channels use `name`, not `title`; wrap _row_title picks it up.
        t, p = _audit_panel_list(f'fast/{slug}/channels', channels, lines)
        _acc(t, p, False)
    lines.append('')

    # ---------- Gaming ----------
    lines.append('## Gaming')
    for slug, panel in (cards.get('gaming_trending') or {}).items():
        if not isinstance(panel, dict):
            continue
        # Some panels split into buckets (meta_quest -> free/paid,
        # steam -> most_played/top_sellers). Walk all list-valued keys.
        found_bucket = False
        for bkey, rows in panel.items():
            if not isinstance(rows, list):
                continue
            found_bucket = True
            t, p = _audit_panel_list(f'gaming/{slug}/{bkey}', rows, lines)
            _acc(t, p, False)
        if not found_bucket:
            t, p = _audit_panel_list(f'gaming/{slug}', [], lines)
    lines.append('')

    # ---------- Films (ticketing) - EXCLUDED from must-be-100% ----------
    lines.append('## Films (EXCLUDED - ticketing rank only, no chip)')
    for slug, panel in (cards.get('films_ticketing') or {}).items():
        if not isinstance(panel, dict):
            continue
        items = panel.get('items') or []
        t, p = _audit_panel_list(f'films/{slug}', items, lines, exclude=True)
        _acc(t, p, True)
    lines.append('')

    # ---------- Summary ----------
    lines.append('=' * 72)
    lines.append('SUMMARY')
    lines.append('=' * 72)
    gt = totals['grand_total']
    gp = totals['grand_priced']
    gt_ex = totals['grand_total_ex_films']
    gp_ex = totals['grand_priced_ex_films']
    def _pct(a, b):
        return (100.0 * a / b) if b else 0.0
    lines.append(f'All panels (incl films):        {gp}/{gt} ({_pct(gp, gt):.1f}%)')
    lines.append(f'Excluding films:                {gp_ex}/{gt_ex} ({_pct(gp_ex, gt_ex):.1f}%)')
    lines.append('')

    return lines, totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='/tmp/us_audience_coverage.txt',
                        help='Write audit report to this path (default '
                             '/tmp/us_audience_coverage.txt). Also printed '
                             'to stdout.')
    parser.add_argument('--no-refresh', action='store_true',
                        help='Read the cached compute_view instead of forcing '
                             'a refresh.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    lines, totals = audit(refresh=not args.no_refresh)
    body = '\n'.join(lines) + '\n'
    print(body)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(body)
        print(f'Wrote audit report to {args.output}', file=sys.stderr)
    # Exit 0 always - this is a diagnostic, not a gate.
    sys.exit(0)


if __name__ == '__main__':
    main()
