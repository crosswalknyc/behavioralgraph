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
  3. Any non-Film item whose audience value is missing, or which is
     showing its own earlier reading carried forward
     (`est_basis='carried_forward'`), or which had no reading anywhere
     and took a rank-tier value (`est_basis='rank_tier'`), or which is
     rendering a cap correction rather than a reading taken for that
     service (`est_basis='platform_cap'`, 2026-09-22), is priced through
     the SAME research machinery as the nightly pass (tiering intact:
     Sonnet for top-ranked, Haiku for long-tail). There is NO budget cap
     on this pass (Jenna 2026-09-09: "im okay with it exceeding a price
     cap if we need it to ensure each item has numbers") - spend is
     metered and reported, never used to stop.
     A capped row is wrong about ONE service, so its result is merged
     narrowly: only that service's block moves, and the title's rows on
     services that were reading correctly stay where they are.
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


# How a rendered row came by its number, worst first. `carried` is a
# real reading of that title from an earlier day walked to today;
# `rank_tier` is the last resort for a title with no reading anywhere
# and is the one that should stay rare. `chart_baseline` is the
# retired name for the rank tier and is still read so a payload cached
# from before the change is counted correctly.
_CARRIED_BASES = ('carried_forward',)
_RANK_TIER_BASES = ('rank_tier', 'chart_baseline')

# A row the cap pass had to correct (2026-09-22). `_enforce_platform_
# caps` stamps this when the number a rail was about to render sat
# above that service's published ceiling, which happens when the row
# fell back to the title's cross-platform total because the research
# returned no block for that service. The correction seats the row on
# its own previous reading or just under the cap, so whichever way it
# lands the row is not showing a reading taken FOR that title ON that
# service. That is the same condition the other three bases describe,
# so it is priced here with them.
#
# Before this, a capped row read as researched and the gate walked
# past it. On a large rail the ceiling barely bites and the seat is
# close to the real level, which is why it went unseen; on a small
# rail it put the correction at the top of the board. Nine of the 120
# Lionsgate+ rows rendered a cap seat in the rail's top ten.
_CAP_BASES = ('platform_cap',)


def _audience_state(it: dict) -> str:
    """'researched' | 'carried' | 'rank_tier' | 'platform_cap' |
    'missing' for a rendered row. Sub-100 estimates count as missing
    (credibility floor, 2026-09-09) so a degenerate research value
    gets re-priced instead of passing."""
    for f in ('us_streams', 'us_readers'):
        blk = it.get(f)
        if isinstance(blk, dict):
            try:
                if float(blk.get('us_estimate') or 0) >= 100:
                    basis = blk.get('est_basis')
                    if basis in _RANK_TIER_BASES:
                        return 'rank_tier'
                    if basis in _CARRIED_BASES:
                        return 'carried'
                    if basis in _CAP_BASES:
                        return 'platform_cap'
                    return 'researched'
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


# ---------------------------------------------------------------------
# Capped rows: pricing one service's reading without touching the rest
# ---------------------------------------------------------------------
# A capped row is wrong about ONE service. The title's rows on the
# services that were reading correctly are not wrong, so the pricing
# below is written back into just the service block the capped row
# reads. Replacing the stored entry wholesale would move those other
# rows as a side effect, which is a different change from the one
# being made here.


def _cap_platform_key(path: str) -> str:
    """The `by_platform` key the rail at `path` reads, or '' when the
    rail has no per-service block (the row reads the total instead).

    Resolved through the same tables the annotators stamp from, so a
    rail that gains a panel gains this with it. A rail that is one
    distribution path through another service resolves to the PARENT
    key, which is the reading that has to move; the derived pass
    recomputes the child from it.
    """
    slug = _platform_slug_from_path(path)
    if not slug:
        return ''
    try:
        import trends_iq
    except Exception:
        return ''
    if path.startswith('fast_trending'):
        return (getattr(trends_iq, '_FAST_PANEL_TO_PLATFORM', {})
                or {}).get(slug, '')
    if path.startswith('streaming_trending'):
        return (getattr(trends_iq, '_STREAMING_PANEL_TO_PLATFORM', {})
                or {}).get(slug, '')
    return ''


def _entry_key_candidates(se, kind: str, title: str, artist: str) -> list:
    """Stored keys a row could resolve to, in the order the annotator
    tries them.

    A streaming row prefers the key matching its own Film / TV label
    and falls back to the other two, so a film:/tv:/title: sibling
    already holding the title is the entry the row is reading. Every
    other kind has exactly one key.
    """
    if kind in ('film', 'tv', 'title'):
        norm = se._cp_normalize(title)
        order = {
            'film':  ('film', 'tv', 'title'),
            'tv':    ('tv', 'film', 'title'),
            'title': ('title', 'film', 'tv'),
        }[kind]
        return [f'{k}:{norm}' for k in order]
    return [se._lookup_key(kind, title, artist)]


def _platform_chart_label(se, kind: str, platform_key: str) -> str:
    """The service's own name, for the prompt's chart context. Marks
    the service as one this title actually appears on, which is what
    makes the research return a block for it."""
    try:
        for p in se._platforms_for_kind(kind) or []:
            if p.get('key') == platform_key:
                return str(p.get('label') or platform_key)
    except Exception:
        pass
    return platform_key


def collect_missing(payload: dict) -> tuple[list[dict], list[dict],
                                             int, int, int, list[dict]]:
    """Walk the rendered payload. Returns (stream_items,
    headline_items, total_nonfilm, researched_count, baseline_count,
    cap_targets).

    The first two are rows needing RESEARCH-grade pricing (missing or
    baseline-stamped), mapped to estimator item dicts and deduped by
    lookup key. `cap_targets` is the capped population, deduped by the
    stored entry the row reads and carrying the set of service keys
    that need a reading of their own."""
    from scripts.trends_scrapers import stream_estimates as se

    cards = (payload or {}).get('cards') or {}
    stream_by_key: dict[str, dict] = {}
    headline_by_key: dict[str, dict] = {}
    cap_by_key: dict[str, dict] = {}
    total = researched = baseline = 0

    # Read lazily: the stored keys are only needed to resolve a capped
    # row onto the entry it reads, and the snapshot is a large object.
    _keys: dict = {}

    def stored_keys() -> set:
        if 'v' not in _keys:
            _keys['v'] = set(((se._read_snapshot('stream_estimates') or {})
                              .get('items') or {}))
        return _keys['v']

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
        # A carried reading and a rank-tier one both still want
        # pricing today; both are counted here so the gate's before
        # figure stays comparable to its after figure.
        if state in ('carried', 'rank_tier'):
            baseline += 1
        title = _item_title(it)
        if state == 'platform_cap':
            _collect_cap_target(se, stored_keys, cap_by_key, path, rank, it,
                                 title)
            continue
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

    # A title that is capped on one service AND unpriced on another is
    # already covered by the full re-price, so it is dropped from the
    # capped population to keep the two passes from pricing it twice.
    priced_keys = set(stream_by_key)
    cap_targets = [t for t in cap_by_key.values()
                   if t['entry_key'] not in priced_keys]

    return (list(stream_by_key.values()), list(headline_by_key.values()),
            total, researched, baseline, cap_targets)


def _collect_cap_target(se, stored_keys, cap_by_key: dict,
                         path: str, rank: int, it: dict,
                         title: str) -> None:
    """Fold one capped row into the per-entry capped population."""
    if not title:
        return
    kind = _estimator_kind_for(path, it)
    if kind is None:
        return
    platform_key = _cap_platform_key(path)
    if not platform_key:
        # No per-service block to write. The row is reading the
        # title's total by construction, so there is nothing service-
        # scoped to give it and the cap correction stands.
        return
    artist = (it.get('artist') or it.get('author') or '').strip()
    if kind == 'fast_channel':
        artist = _platform_slug_from_path(path)

    candidates = _entry_key_candidates(se, kind, title, artist)
    known = stored_keys()
    entry_key = next((k for k in candidates if k in known), candidates[0])
    # Price under the kind of the entry the row actually reads, so the
    # result merges into that entry instead of creating a sibling key
    # that would then win the annotator's lookup.
    entry_kind = entry_key.split(':', 1)[0]

    tgt = cap_by_key.get(entry_key)
    if tgt is None:
        tgt = cap_by_key[entry_key] = {
            'entry_key':     entry_key,
            'kind':          entry_kind,
            'display_title': title,
            'artist':        artist,
            'best_rank':     rank,
            'platforms':     set(),
            'rows':          [],
        }
    tgt['platforms'].add(platform_key)
    tgt['rows'].append(path)
    if rank < tgt['best_rank']:
        tgt['best_rank'] = rank


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


def _cap_research_items(se, cap_targets: list[dict]) -> list[dict]:
    """Estimator items for the capped population, one per stored
    entry, naming every service that needs a reading of its own so the
    research returns a block for each."""
    items = []
    for t in cap_targets:
        labels = [f'{_platform_chart_label(se, t["kind"], p)} '
                  f'#{t["best_rank"]}'
                  for p in sorted(t['platforms'])]
        items.append({
            'kind':          t['kind'],
            'display_title': t['display_title'],
            'artist':        t['artist'],
            'best_rank':     t['best_rank'],
            'chart_labels':  labels,
        })
    return items


def _merge_cap_platform_blocks(results: dict[str, dict],
                                cap_targets: list[dict],
                                target_date_iso: str) -> dict[str, Any]:
    """Write the freshly priced per-service blocks into the stored
    entries, and nothing else.

    Only `by_platform[<service>]` moves. The entry's total, its blocks
    for every other service, and its reasoning all stay exactly as
    they were, so the same title's rows on services that were reading
    correctly do not move. Returns a counter plus the per-row trail.
    """
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base

    stats: dict[str, Any] = {'entries': 0, 'blocks': 0, 'no_result': [],
                              'no_block': [], 'trail': []}
    if not results or not cap_targets:
        return stats

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}

    for t in cap_targets:
        res = results.get(t['entry_key'])
        if not isinstance(res, dict):
            stats['no_result'].append(t['entry_key'])
            continue
        fresh = res.get('by_platform') or {}
        entry = items.get(t['entry_key'])
        if not isinstance(entry, dict):
            # The row was reading a key the snapshot does not carry,
            # so there is nothing to merge into without inventing an
            # entry. Leave it; the nightly pass owns creating one.
            stats['no_result'].append(t['entry_key'])
            continue
        blocks = dict(entry.get('by_platform') or {})
        wrote = 0
        for p in sorted(t['platforms']):
            blk = fresh.get(p)
            if not isinstance(blk, dict):
                stats['no_block'].append(f'{t["entry_key"]}@{p}')
                continue
            try:
                v = int(blk.get('us_estimate') or 0)
            except (TypeError, ValueError):
                v = 0
            if v < 100:
                # Same credibility floor the merge above applies: a
                # sub-100 reading on a charting row is a failed call,
                # not an audience. The cap correction stands and the
                # nightly pass retries.
                stats['no_block'].append(f'{t["entry_key"]}@{p}')
                continue
            prev = (entry.get('by_platform') or {}).get(p) or {}
            try:
                was = int(prev.get('us_estimate') or 0)
            except (TypeError, ValueError):
                was = 0
            blocks[p] = blk
            wrote += 1
            stats['trail'].append({
                'entry_key': t['entry_key'],
                'title':     t['display_title'],
                'platform':  p,
                'stored_was': was or None,
                'stored_now': v,
                'rows':      list(t['rows']),
            })
        if not wrote:
            continue
        entry['by_platform'] = blocks
        items[t['entry_key']] = entry
        stats['entries'] += 1
        stats['blocks'] += wrote

    if not stats['blocks']:
        return stats

    snap['items'] = items
    snap['count'] = len(items)
    snap.setdefault('target_date', target_date_iso)
    snap['coverage_gate_at'] = datetime.now(timezone.utc).isoformat()
    _base.write_snapshot('stream_estimates', snap)
    return stats


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


# Above this many items to re-price, the gate submits to the discounted
# batch lane instead of pricing one item at a time. The per-item lane
# runs at concurrency 6 and costs roughly 2.5 seconds an item, so a few
# hundred stragglers clear in minutes and batch's ~60 minute floor is
# not worth paying. Several thousand is a different job: on 2026-09-15
# the gate inherited 6,769 items after the estimator's accumulator was
# wiped and spent 4 hours 40 minutes on them, which is what pushed the
# nightly run past nine hours and left rank-derived numbers on the
# board all day.
_GATE_BATCH_THRESHOLD = 400


def _price_stream_items(se, stream_items: list[dict], *,
                         target_date_iso: str,
                         meter: Any) -> dict[str, dict]:
    """Price the gate's stream-kind remainder, choosing the lane by size.

    Falls back to the per-item lane when batch comes back with less
    than `_BATCH_FALLBACK_MIN_SHARE` of what was asked for, mirroring
    the estimator's own fallback so a refused or unusable batch never
    strands the board on baselines.
    """
    n = len(stream_items)
    if n < _GATE_BATCH_THRESHOLD:
        return se._research_all(stream_items,
                                 target_date_iso=target_date_iso,
                                 spend_monitor=meter)

    logger.info("coverage_gate: %d items to price; using the batch lane", n)
    try:
        results = se._research_all_batch(stream_items,
                                          target_date_iso=target_date_iso,
                                          spend_monitor=meter)
    except Exception:
        logger.exception("coverage_gate: batch lane raised; falling back "
                          "to the per-item lane")
        results = {}

    if len(results) >= se._BATCH_FALLBACK_MIN_SHARE * n:
        remaining = [it for it in stream_items
                     if se._lookup_key(it['kind'], it['display_title'],
                                        it.get('artist') or '') not in results]
        if remaining:
            logger.info("coverage_gate: batch priced %d/%d; pricing the "
                        "%d straggler(s) per item", len(results), n,
                        len(remaining))
            results.update(se._research_all(remaining,
                                             target_date_iso=target_date_iso,
                                             spend_monitor=meter))
        return results

    logger.error("coverage_gate: batch lane returned %d/%d; pricing the "
                  "remainder per item", len(results), n)
    remaining = [it for it in stream_items
                 if se._lookup_key(it['kind'], it['display_title'],
                                    it.get('artist') or '') not in results]
    results.update(se._research_all(remaining,
                                     target_date_iso=target_date_iso,
                                     spend_monitor=meter))
    return results


def run_gate(dry_run: bool = False) -> dict[str, Any]:
    """Run the full coverage gate. Returns a summary dict:
    {total, researched_before, researched_after, rendered_after_pct,
     priced_stream, priced_headline, capped_before, cap_titles,
     cap_blocks_written, capped_after, spend_usd, still_missing}."""
    import trends_iq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import headline_estimates as he
    from scripts.trends_scrapers._spend_monitor import SpendMonitor

    target_date_iso = (datetime.now(timezone.utc).date()
                       - timedelta(days=1)).isoformat()

    payload = trends_iq.compute_view(dict(_DEFAULT_FILTERS),
                                      force_refresh=True)
    (stream_items, headline_items,
     total, researched, baseline, cap_targets) = collect_missing(payload)
    cap_rows = sum(len(t['rows']) for t in cap_targets)
    cap_blocks = sum(len(t['platforms']) for t in cap_targets)
    pct_before = (100.0 * researched / total) if total else 100.0
    logger.info("coverage_gate: %d rendered non-Film items, %d researched "
                "(%.2f%%), %d need pricing (%d stream-kind, %d headline), "
                "%d row(s) on a cap correction across %d service "
                "reading(s) on %d title(s)",
                total, researched, pct_before,
                len(stream_items) + len(headline_items),
                len(stream_items), len(headline_items),
                cap_rows, cap_blocks, len(cap_targets))

    summary: dict[str, Any] = {
        'total': total,
        'researched_before': researched,
        'researched_before_pct': round(pct_before, 2),
        'priced_stream': 0,
        'priced_headline': 0,
        'capped_before': cap_rows,
        'cap_titles': len(cap_targets),
        'cap_blocks_written': 0,
        'capped_after': 0,
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
        results = _price_stream_items(se, stream_items,
                                       target_date_iso=target_date_iso,
                                       meter=meter)
        try:
            se._apply_continuity_guard(results, target_date_iso)
        except Exception:
            logger.exception("coverage_gate: continuity guard failed "
                              "(non-fatal)")
        summary['priced_stream'] = _merge_stream_results(
            results, target_date_iso)
        logger.info("coverage_gate: priced + merged %d/%d stream-kind "
                    "items", len(results), len(stream_items))

    # Capped rows. Priced the same way, merged narrowly: only the
    # service block the capped row reads is written, so the title's
    # rows on other services keep the numbers they already had.
    if cap_targets:
        cap_items = _cap_research_items(se, cap_targets)
        cap_results = _price_stream_items(se, cap_items,
                                           target_date_iso=target_date_iso,
                                           meter=meter)
        cap_stats = _merge_cap_platform_blocks(cap_results, cap_targets,
                                                target_date_iso)
        summary['cap_blocks_written'] = cap_stats['blocks']
        summary['cap_trail'] = cap_stats['trail']
        logger.info("coverage_gate: priced %d/%d capped title(s), wrote "
                    "%d service reading(s) into %d entry(ies)",
                    len(cap_results), len(cap_items), cap_stats['blocks'],
                    cap_stats['entries'])
        for miss in cap_stats['no_result'] + cap_stats['no_block']:
            logger.info("coverage_gate: no usable reading for %s; its cap "
                        "correction stands and tonight's pass retries",
                        miss)
        for row in cap_stats['trail']:
            logger.info("coverage_gate cap re-price: %s on %s %s -> %s",
                        row['title'], row['platform'],
                        f'{row["stored_was"]:,}' if row['stored_was']
                        else '(no reading)',
                        f'{row["stored_now"]:,}')

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
    total2 = researched2 = rendered2 = carried2 = rank_tier2 = 0
    capped2 = 0
    still_missing: list[tuple[str, str]] = []
    # Per-list tally. A board-wide percentage says something is wrong;
    # the per-list split says where, which is what makes the alert
    # actionable. Carried and rank-tier are counted apart: a carried
    # row is a real reading of that title going slightly stale, a
    # rank-tier row is a number that says nothing about the title.
    per_list: dict[str, dict[str, int]] = {}
    for path, _rank, it in _walk_rendered(cards2):
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        if path.startswith('fused_trending') and _fused_row_is_film_only(it):
            continue
        total2 += 1
        bucket = per_list.setdefault(path, {'total': 0, 'carried': 0,
                                             'rank_tier': 0,
                                             'platform_cap': 0})
        bucket['total'] += 1
        state = _audience_state(it)
        if state == 'researched':
            researched2 += 1
            rendered2 += 1
        elif state == 'carried':
            rendered2 += 1
            carried2 += 1
            bucket['carried'] += 1
        elif state == 'rank_tier':
            rendered2 += 1
            rank_tier2 += 1
            bucket['rank_tier'] += 1
        elif state == 'platform_cap':
            # Still on a cap correction: the research came back with
            # nothing usable for that service, so the seat stands and
            # tonight's pass tries again.
            rendered2 += 1
            capped2 += 1
            bucket['platform_cap'] += 1
        else:
            still_missing.append((path, _item_title(it)))

    summary['researched_after_pct'] = round(
        (100.0 * researched2 / total2) if total2 else 100.0, 2)
    summary['rendered_after_pct'] = round(
        (100.0 * rendered2 / total2) if total2 else 100.0, 2)
    summary['still_missing'] = len(still_missing)
    summary['total'] = total2
    summary['carried_after'] = carried2
    summary['carried_after_pct'] = round(
        (100.0 * carried2 / total2) if total2 else 0.0, 2)
    summary['rank_tier_after'] = rank_tier2
    summary['rank_tier_after_pct'] = round(
        (100.0 * rank_tier2 / total2) if total2 else 0.0, 2)
    summary['capped_after'] = capped2
    summary['by_list'] = {
        name: {
            'total': v['total'],
            'carried': v['carried'],
            'rank_tier': v['rank_tier'],
            'platform_cap': v['platform_cap'],
            'carried_pct': round(100.0 * v['carried'] / v['total'], 2),
            'rank_tier_pct': round(100.0 * v['rank_tier'] / v['total'], 2),
        }
        for name, v in sorted(per_list.items())
        if v['carried'] or v['rank_tier'] or v['platform_cap']
    }

    logger.info("coverage_gate: FINAL coverage researched=%.2f%% "
                "rendered=%.2f%% carried=%.2f%% rank_tier=%.2f%% "
                "(total=%d, still_missing=%d, cap corrections %d -> %d, "
                "spend=$%.2f)",
                summary['researched_after_pct'],
                summary['rendered_after_pct'],
                summary['carried_after_pct'],
                summary['rank_tier_after_pct'],
                total2, len(still_missing),
                summary['capped_before'], capped2, summary['spend_usd'])
    for name, v in sorted(summary['by_list'].items(),
                          key=lambda kv: (kv[1]['rank_tier_pct'],
                                          kv[1]['carried_pct']),
                          reverse=True)[:20]:
        logger.info("coverage_gate:   carried %5.1f%% rank-tier %5.1f%% "
                    "(%d/%d) %s", v['carried_pct'], v['rank_tier_pct'],
                    v['carried'] + v['rank_tier'], v['total'], name)

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
