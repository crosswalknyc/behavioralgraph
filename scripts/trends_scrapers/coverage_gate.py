"""
Trends IQ post-run coverage gate (2026-09-09).

Standing requirement (Jenna, repeated since 2026-08): EVERY item rendered
anywhere on the Trends dashboard and its CSV exports carries a US Audience
value, with the Films tab as the only exception.

This gate runs at the end of the daily orchestrator (run_all.py), AFTER
stream_estimates + headline_estimates have landed:

  1. Recompute the live payload exactly as the dashboard renders it
     (trends_iq.compute_view, force_refresh).
  2. Walk every rendered section generically (payload-derived universe -
     a tab added next month is covered by construction, no hand-
     maintained kind list).
  3. Any non-Film item whose audience value is missing OR carries the
     render-time `est_basis='chart_baseline'` marker gets priced through
     the SAME research machinery as the nightly pass (tiering intact:
     Sonnet for top-ranked, Haiku for long-tail). There is NO budget cap
     on this pass (Jenna 2026-09-09: "im okay with it exceeding a price
     cap if we need it to ensure each item has numbers") - spend is
     metered and reported, never used to stop.
  4. Results merge into `latest/` AND today's dated snapshot so window
     math, deltas, and tomorrow's continuity guard stay coherent.
  5. Live compute_view caches are purged and the payload recomputed; the
     final coverage percentage lands in the run summary log line.
  6. If any non-Film row is STILL missing a value after all that, an
     email goes to jenna@ + jessie@ (never liz@) with the exact items.
     The dashboard renders such rows neutrally (blank chip, no error).

CLI:
    python3 -m scripts.trends_scrapers.coverage_gate            # full run
    python3 -m scripts.trends_scrapers.coverage_gate --dry-run  # audit only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ALERT_TO = ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com']
_ALERT_FROM = 'BehavioralGraph <jenna@crosswalknyc.com>'

_DEFAULT_FILTERS = {'geo_type': 'National', 'geo_value': '',
                    'lookback_days': 1}

_TITLE_KEYS = ('title', 'term', 'name', 'display_name', 'query', 'show',
               'channel_name', 'headline')
_SKIP_CARD_KEYS = {'lens_config', 'lens_scores', 'lens_cutoffs'}
_EXEMPT_PREFIXES = ('films_ticketing',)
_READER_PREFIXES = ('trending_headlines', 'articles_by_source',
                    'philanthropy_news', 'business_news',
                    'wall_street_news')


def _item_title(it: dict) -> str:
    for k in _TITLE_KEYS:
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _audience_state(it: dict) -> str:
    """'researched' | 'baseline' | 'missing' for a rendered row.
    Sub-100 estimates count as missing (credibility floor, 2026-09-09)
    so a degenerate research value gets re-priced instead of passing."""
    for f in ('us_streams', 'us_readers'):
        blk = it.get(f)
        if isinstance(blk, dict):
            try:
                if float(blk.get('us_estimate') or 0) >= 100:
                    return ('baseline'
                            if blk.get('est_basis') == 'chart_baseline'
                            else 'researched')
            except (TypeError, ValueError):
                pass
    try:
        if float(it.get('holds') or 0) > 0:
            return 'researched'   # Libby native hold count
    except (TypeError, ValueError):
        pass
    return 'missing'


def _walk_rendered(cards: dict):
    """Yield (path, rank_pos, item) for every rendered item row."""
    def _walk(node, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                if not path and k in _SKIP_CARD_KEYS:
                    continue
                yield from _walk(v, f'{path}.{k}' if path else k)
            return
        if not isinstance(node, list):
            return
        items = [x for x in node
                 if isinstance(x, dict) and _item_title(x)]
        if items:
            for i, it in enumerate(items):
                try:
                    rank = int(it.get('rank') or (i + 1))
                except (TypeError, ValueError):
                    rank = i + 1
                yield path, rank, it
            return
        for x in node:
            if isinstance(x, (dict, list)):
                yield from _walk(x, path)
    yield from _walk(cards or {}, '')


def _fused_row_is_film_only(row: dict) -> bool:
    sources = row.get('sources') or []
    if not sources:
        return False
    tabs = {(s.get('tab') or '').lower() for s in sources
            if isinstance(s, dict)}
    return bool(tabs) and tabs <= {'films', 'film'}


def _estimator_kind_for(path: str, it: dict) -> Optional[str]:
    """Map a payload path + row to the stream_estimates kind. None =
    headline-family (priced via headline_estimates instead)."""
    if any(path.startswith(p) for p in _READER_PREFIXES):
        return None
    if path.startswith('fast_trending'):
        if '.channels' in path:
            return 'fast_channel'
        cat = (it.get('category_display') or '').lower()
        if cat == 'film' or path.endswith('.films'):
            return 'fast_film'
        return 'fast_tv'
    if path.startswith('streaming_trending'):
        cat = (it.get('category_display') or '').lower()
        if cat == 'film' or path.endswith('.films'):
            return 'film'
        if 'tv' in cat or path.endswith('.tv'):
            return 'tv'
        return 'title'
    for prefix, kind in (
            ('music_trending',           'song'),
            ('podcasts_trending',        'podcast'),
            ('books_trending.wattpad',   'wattpad_story'),
            ('books_trending.goodreads', 'goodreads_book'),
            ('books_trending',           'book'),
            ('libby_trending',           'book'),
            ('comics_trending',          'comic'),
            ('gaming_trending',          'game'),
            ('broadway_trending',        'title'),
            ('trending_searches',        'search_term'),
            ('movers',                   'search_term'),
            ('trending_people',          'trending_person'),
            ('wikipedia_trending',       'wiki_topic'),
            ('fused_trending',           'search_term'),
    ):
        if path.startswith(prefix):
            return kind
    return 'search_term'


def _platform_slug_from_path(path: str) -> str:
    """fast_trending.<slug>.channels -> <slug>."""
    parts = path.split('.')
    return parts[1] if len(parts) >= 2 else ''


def collect_missing(payload: dict) -> tuple[list[dict], list[dict],
                                             int, int, int]:
    """Walk the rendered payload. Returns (stream_items,
    headline_items, total_nonfilm, researched_count, baseline_count).
    Items returned are the rows needing RESEARCH-grade pricing
    (missing or baseline-stamped), mapped to estimator item dicts and
    deduped by lookup key."""
    from scripts.trends_scrapers import stream_estimates as se

    cards = (payload or {}).get('cards') or {}
    stream_by_key: dict[str, dict] = {}
    headline_by_key: dict[str, dict] = {}
    total = researched = baseline = 0

    for path, rank, it in _walk_rendered(cards):
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        if path.startswith('fused_trending') and _fused_row_is_film_only(it):
            continue
        total += 1
        state = _audience_state(it)
        if state == 'researched':
            researched += 1
            continue
        if state == 'baseline':
            baseline += 1
        title = _item_title(it)
        kind = _estimator_kind_for(path, it)
        if kind is None:
            key = se._cp_normalize(title)
            if key and key not in headline_by_key:
                headline_by_key[key] = {
                    'kind':          'headline',
                    'display_title': title[:200],
                    'source':        (it.get('source')
                                      or it.get('source_label') or ''),
                    'domain':        '',
                    'url':           it.get('url') or '',
                    'image':         it.get('image') or '',
                    'seendate':      (it.get('published')
                                      or it.get('seendate') or ''),
                    'best_rank':     rank,
                    'chart_labels':  [f'{path} #{rank}'],
                }
            continue
        artist = (it.get('artist') or it.get('author') or '').strip()
        if kind == 'fast_channel':
            artist = _platform_slug_from_path(path)
        item = {
            'kind':          kind,
            'display_title': title,
            'artist':        artist,
            'best_rank':     rank,
            'chart_labels':  [f'{path} #{rank}'],
            'image':         it.get('image'),
            'url':           it.get('url'),
        }
        key = se._lookup_key(kind, title, artist)
        prev = stream_by_key.get(key)
        if prev is None or rank < prev['best_rank']:
            stream_by_key[key] = item

    return (list(stream_by_key.values()), list(headline_by_key.values()),
            total, researched, baseline)


def _merge_stream_results(results: dict[str, dict],
                           target_date_iso: str) -> int:
    """Merge freshly researched stream estimates into latest/ + today's
    dated snapshot. Returns number of items merged."""
    if not results:
        return 0
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base

    # Degenerate-value guard: a research call occasionally returns an
    # implausibly tiny number (e.g. 8 for a charting Audible sleep
    # podcast on the 2026-09-09 run). No real US-audience chip should
    # read under 100; dropping the result keeps the row on its
    # chart-tier baseline and the nightly estimator retries tomorrow.
    degenerate = [k for k, v in results.items()
                  if (v.get('us_estimate') or 0) < 100]
    for k in degenerate:
        logger.info("coverage_gate: dropping degenerate estimate %s "
                    "(us_estimate=%s); baseline stays", k,
                    results[k].get('us_estimate'))
        results.pop(k, None)
    if not results:
        return 0

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    items.update(results)
    snap['items'] = items
    snap['count'] = len(items)
    snap.setdefault('target_date', target_date_iso)
    snap['coverage_gate_at'] = datetime.now(timezone.utc).isoformat()
    _base.write_snapshot('stream_estimates', snap)
    return len(results)


def _merge_headline_results(results: dict[str, dict]) -> int:
    if not results:
        return 0
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base

    snap = se._read_snapshot('headline_estimates') or {}
    items = snap.get('items') or {}
    items.update(results)
    snap['items'] = items
    snap['count'] = len(items)
    snap['coverage_gate_at'] = datetime.now(timezone.utc).isoformat()
    _base.write_snapshot('headline_estimates', snap)
    return len(results)


def _send_still_missing_alert(missing_rows: list[tuple[str, str]]) -> None:
    """Best-effort SES alert listing rows that still lack a value after
    the fallback pricing pass. Never raises."""
    try:
        import boto3
        body_lines = [
            'These Trends items are still missing a US Audience value '
            'after the nightly research pass and the follow-up pricing '
            'pass. The dashboard shows them without an audience chip '
            'until the next run.',
            '',
        ]
        for path, title in missing_rows[:200]:
            body_lines.append(f'  {path}: {title}')
        if len(missing_rows) > 200:
            body_lines.append(f'  ... and {len(missing_rows) - 200} more')
        body_lines += ['',
                       f'UTC: {datetime.now(timezone.utc).isoformat()}']
        ses = boto3.client('ses', region_name='us-east-2')
        ses.send_email(
            Source=_ALERT_FROM,
            Destination={'ToAddresses': _ALERT_TO},
            Message={
                'Subject': {'Data': 'Trends: items missing US Audience '
                                     'after coverage pass'},
                'Body': {'Text': {'Data': '\n'.join(body_lines)}},
            })
        logger.info("coverage_gate: alert email sent (%d items)",
                     len(missing_rows))
    except Exception:
        logger.exception("coverage_gate: alert email failed (non-fatal)")


def run_gate(dry_run: bool = False) -> dict[str, Any]:
    """Run the full coverage gate. Returns a summary dict:
    {total, researched_before, researched_after, rendered_after_pct,
     priced_stream, priced_headline, spend_usd, still_missing}."""
    import trends_iq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import headline_estimates as he
    from scripts.trends_scrapers._spend_monitor import SpendMonitor

    target_date_iso = (datetime.now(timezone.utc).date()
                       - timedelta(days=1)).isoformat()

    payload = trends_iq.compute_view(dict(_DEFAULT_FILTERS),
                                      force_refresh=True)
    (stream_items, headline_items,
     total, researched, baseline) = collect_missing(payload)
    pct_before = (100.0 * researched / total) if total else 100.0
    logger.info("coverage_gate: %d rendered non-Film items, %d researched "
                "(%.2f%%), %d need pricing (%d stream-kind, %d headline)",
                total, researched, pct_before,
                len(stream_items) + len(headline_items),
                len(stream_items), len(headline_items))

    summary: dict[str, Any] = {
        'total': total,
        'researched_before': researched,
        'researched_before_pct': round(pct_before, 2),
        'priced_stream': 0,
        'priced_headline': 0,
        'spend_usd': 0.0,
        'still_missing': 0,
        'rendered_after_pct': None,
        'researched_after_pct': None,
    }
    if dry_run:
        logger.info("coverage_gate: dry-run, not pricing")
        return summary

    # Meter-only monitor: effectively uncapped (Jenna 2026-09-09:
    # completeness wins over cost). Used purely to REPORT actual spend.
    meter = SpendMonitor(cap_usd=1e9, prefix='coverage_gate')

    if stream_items:
        results = se._research_all(stream_items,
                                    target_date_iso=target_date_iso,
                                    spend_monitor=meter)
        try:
            se._apply_continuity_guard(results, target_date_iso)
        except Exception:
            logger.exception("coverage_gate: continuity guard failed "
                              "(non-fatal)")
        summary['priced_stream'] = _merge_stream_results(
            results, target_date_iso)
        logger.info("coverage_gate: priced + merged %d/%d stream-kind "
                    "items", len(results), len(stream_items))

    if headline_items:
        h_results = he._research_all(headline_items)
        summary['priced_headline'] = _merge_headline_results(h_results)
        logger.info("coverage_gate: priced + merged %d/%d headline "
                    "items", len(h_results), len(headline_items))

    summary['spend_usd'] = round(meter.total(), 2)

    # Purge live caches + recompute so the dashboard serves the new
    # values immediately, then re-audit.
    try:
        n = trends_iq.invalidate_live_compute_view_caches()
        logger.info("coverage_gate: purged %d live cache entries", n)
    except Exception:
        logger.exception("coverage_gate: cache purge failed (non-fatal)")

    payload2 = trends_iq.compute_view(dict(_DEFAULT_FILTERS),
                                       force_refresh=True)
    cards2 = (payload2 or {}).get('cards') or {}
    total2 = researched2 = rendered2 = 0
    still_missing: list[tuple[str, str]] = []
    for path, _rank, it in _walk_rendered(cards2):
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        if path.startswith('fused_trending') and _fused_row_is_film_only(it):
            continue
        total2 += 1
        state = _audience_state(it)
        if state == 'researched':
            researched2 += 1
            rendered2 += 1
        elif state == 'baseline':
            rendered2 += 1
        else:
            still_missing.append((path, _item_title(it)))

    summary['researched_after_pct'] = round(
        (100.0 * researched2 / total2) if total2 else 100.0, 2)
    summary['rendered_after_pct'] = round(
        (100.0 * rendered2 / total2) if total2 else 100.0, 2)
    summary['still_missing'] = len(still_missing)

    logger.info("coverage_gate: FINAL coverage researched=%.2f%% "
                "rendered=%.2f%% (total=%d, still_missing=%d, "
                "spend=$%.2f)",
                summary['researched_after_pct'],
                summary['rendered_after_pct'],
                total2, len(still_missing), summary['spend_usd'])

    if still_missing:
        _send_still_missing_alert(still_missing)

    return summary


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Trends IQ coverage gate')
    ap.add_argument('--dry-run', action='store_true',
                    help='audit coverage only; price nothing')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    summary = run_gate(dry_run=args.dry_run)
    print(f"coverage_gate summary: {summary}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
