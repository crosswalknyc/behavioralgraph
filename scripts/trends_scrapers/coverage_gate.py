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
     service (`est_basis='platform_cap'`, 2026-09-22), or which was
     rendering a reading taken for a DIFFERENT service
     (`est_basis='cross_service'`, 2026-09-22), is priced through
     the SAME research machinery as the nightly pass (tiering intact:
     Sonnet for top-ranked, Haiku for long-tail). There is NO budget cap
     on this pass (Jenna 2026-09-09: "im okay with it exceeding a price
     cap if we need it to ensure each item has numbers") - spend is
     metered and reported, never used to stop.
     Both of the last two are wrong about ONE service, so the result
     is merged narrowly: only that service's block moves, and the
     title's rows on services that were reading correctly stay where
     they are.
     Rows on the panels whose platform publishes a figure on the row
     (Wattpad reads and votes, Libby holds) or whose row is one
     volume of a series priced on the same chart (comics) are never
     sent to per-title research. They are derived from those figures
     (`first_party_derivation`, 2026-09-25), the same move that gave
     Netflix its chart from the views Netflix publishes.
  4. Results merge into `latest/` AND today's dated snapshot so window
     math, deltas, and tomorrow's continuity guard stay coherent.
  5. Live compute_view caches are purged and the payload recomputed; the
     final coverage percentage lands in the run summary log line.
  6. Any non-Film row STILL blank after all that is reasoned from the
     rows either side of it on its own list, for its own service, and
     written so it renders today (`terminal_bracket`, Jenna
     2026-09-25: "it should not have not found these but should have
     figured out how to reason answers to them"). Bracketed rows are
     counted apart from researched rows and re-enter tonight's
     research, so a real reading replaces the bracket as soon as one
     lands. The board is recomputed and re-tallied after the pass.
  7. Only a row that survives even the terminal pass is emailed to
     jenna@ + jessie@ (never liz@). That is a bug in the pass, not a
     wait for the next run, and the email says so.

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
                    'business_news', 'wall_street_news')


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

# A row that was rendering a reading taken for a DIFFERENT service
# (2026-09-22). A title on several services carries one reading per
# service it was measured on, plus a total across all of them; a rail
# holding no reading of its own used to render that total, which reads
# as a sound number about a service it was never measured on. Unlike a
# cap breach it can sit comfortably inside the service's ceiling, so
# neither the cap pass nor this gate saw it: 422 rows of 3,473 on the
# streaming and FAST rails were in that state, The Vampire Diaries on
# Max among them, showing 880,074 off a reading taken for Prime Video.
#
# `trends_iq._enforce_service_provenance` now takes such a row onto a
# number about its own service and leaves this mark on it. The mark is
# what asks for the real thing: a reading researched FOR that service,
# priced and merged below exactly the way a capped row is, because it
# is the same shape of defect. One service of one title is wrong, so
# one service block is written and the title's rows on services that
# were reading correctly do not move.
_CROSS_SERVICE_BASES = ('cross_service',)

# Both are priced through the narrow per-service path.
_PER_SERVICE_BASES = _CAP_BASES + _CROSS_SERVICE_BASES

# A reading derived from the platform's OWN published figure for the
# row (2026-09-25): a Wattpad story's cumulative reads, a Libby title's
# hold count, a comics volume placed inside its own series on the
# chart. See `first_party_derivation`. It is not a research call, so
# the sub-100 credibility floor below (which exists to catch a failed
# research call) does not apply: a story with 65 lifetime reads has a
# handful of readers a day and that is the honest reading.
_FIRST_PARTY_BASES = ('first_party',)

# A reading reasoned from the rows either side of it on its own list
# for its own service, written by the terminal pass when research
# returned nothing usable (`terminal_bracket`, 2026-09-25). It renders
# today, counts apart from researched rows, and goes back into
# tonight's research so a real reading replaces it. Honest at any
# positive value: it sits inside two readings that already passed.
_BRACKETED_BASES = ('bracketed',)

# Rendered lists whose rows carry a first-party figure the derivation
# reads from. A blank row here is derived, never sent to per-title web
# research, which is the path that kept failing on them.
_FIRST_PARTY_PREFIXES = ('books_trending.wattpad', 'comics_trending')


def _audience_state(it: dict) -> str:
    """'researched' | 'carried' | 'rank_tier' | 'platform_cap' |
    'cross_service' | 'bracketed' | 'missing' for a rendered row.
    Sub-100 estimates count as missing (credibility floor, 2026-09-09)
    so a degenerate research value gets re-priced instead of passing,
    except when the reading is a first-party derivation or a bracket,
    which are honest at any positive value."""
    for f in ('us_streams', 'us_readers'):
        blk = it.get(f)
        if isinstance(blk, dict):
            try:
                v = float(blk.get('us_estimate') or 0)
                basis = blk.get('est_basis')
                if v > 0 and basis in _FIRST_PARTY_BASES:
                    return 'researched'
                if v > 0 and basis in _BRACKETED_BASES:
                    return 'bracketed'
                if v >= 100:
                    if basis in _RANK_TIER_BASES:
                        return 'rank_tier'
                    if basis in _CARRIED_BASES:
                        return 'carried'
                    if basis in _CAP_BASES:
                        return 'platform_cap'
                    if basis in _CROSS_SERVICE_BASES:
                        return 'cross_service'
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


def _service_key_for_path(path: str) -> str:
    """The estimator platform key for ANY service rail at `path`, or
    '' for a cross-platform list.

    `_cap_platform_key` only knows the streaming and FAST rails, which
    is all the cap pass needs. The full re-price path needs the chart
    panels too: a row on Apple Books Comics used to be handed to the
    research with the label `comics_trending.apple_comics.items #82`,
    which the label matcher read as the Apple BOOKS store, so the
    answer came back with no block for the service the row is on and
    the row stayed blank. Resolved through the same tables the
    annotators stamp from, so a new panel gains this with it.
    """
    key = _cap_platform_key(path)
    if key:
        return key
    try:
        import trends_iq
        fn = getattr(trends_iq, '_coverage_platform_for_path', None)
        if callable(fn):
            return str(fn(path) or '')
    except Exception:
        pass
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


def collect_missing(payload: dict,
                    first_party_out: Optional[dict] = None,
                    ) -> tuple[list[dict], list[dict],
                               int, int, int, list[dict]]:
    """Walk the rendered payload. Returns (stream_items,
    headline_items, total_nonfilm, researched_count, baseline_count,
    cap_targets).

    The first two are rows needing RESEARCH-grade pricing (missing or
    baseline-stamped), mapped to estimator item dicts and deduped by
    lookup key. `cap_targets` is the capped population, deduped by the
    stored entry the row reads and carrying the set of service keys
    that need a reading of their own.

    Rows on the first-party panels (Wattpad, comics) never enter the
    research population: their reading is derived from the figures on
    the row (`first_party_derivation`). When `first_party_out` is
    given it is filled with `{'wattpad': [keys], 'comics': [keys]}` of
    the blank rows seen there, so the caller can run the derivation
    and report on exactly those rows."""
    from scripts.trends_scrapers import stream_estimates as se

    cards = (payload or {}).get('cards') or {}
    stream_by_key: dict[str, dict] = {}
    headline_by_key: dict[str, dict] = {}
    cap_by_key: dict[str, dict] = {}
    total = researched = baseline = 0
    fp_wattpad: list[str] = []
    fp_comics: list[str] = []

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
        # A carried reading, a rank-tier one and a bracketed one all
        # still want pricing today; all are counted here so the gate's
        # before figure stays comparable to its after figure.
        if state in ('carried', 'rank_tier', 'bracketed'):
            baseline += 1
        title = _item_title(it)
        if state in ('platform_cap', 'cross_service'):
            _collect_cap_target(se, stored_keys, cap_by_key, path, rank, it,
                                 title, state)
            continue
        kind = _estimator_kind_for(path, it)

        # First-party panels: the row carries the figure its reading
        # is derived from (Wattpad reads, Libby holds) or sits inside
        # a series that does (comics volumes). Per-title web research
        # is what kept failing here, so these never enter it.
        if kind is not None and any(path.startswith(p)
                                    for p in _FIRST_PARTY_PREFIXES):
            artist = (it.get('artist') or it.get('author') or '').strip()
            key = se._lookup_key(kind, title, artist)
            if key:
                (fp_wattpad if kind == 'wattpad_story'
                 else fp_comics).append(key)
            continue

        # A blank row on a service rail whose title the store already
        # holds wants ONE service block written into that entry, not a
        # whole new entry. The whole-item path replaced the entry and
        # with it every other service's reading of the title, and it
        # labelled the row by its payload path, which the research did
        # not read as the service. Republic of Doyle sat blank on Roku
        # through several passes that way while its Tubi reading was
        # fine. A rail that is one distribution path through another
        # service resolves to the PARENT here, so fixing the parent
        # is what fixes the breakout; the child is never priced on
        # its own (`derived_rails`).
        if (state in ('missing', 'bracketed') and kind is not None
                and (path.startswith('streaming_trending')
                     or path.startswith('fast_trending'))
                and _cap_platform_key(path)):
            artist = (it.get('artist') or it.get('author') or '').strip()
            if kind == 'fast_channel':
                artist = _platform_slug_from_path(path)
            cands = _entry_key_candidates(se, kind, title, artist)
            if any(c in stored_keys() for c in cands):
                _collect_cap_target(se, stored_keys, cap_by_key, path,
                                     rank, it, title, state)
                continue

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
        # The label names the SERVICE the row is on, the way the
        # nightly collectors label it, so the research returns a block
        # for that service. The payload path is kept alongside for the
        # trail; it is not what the label matcher reads.
        service_key = _service_key_for_path(path)
        if service_key:
            label = f'{_platform_chart_label(se, kind, service_key)} #{rank}'
        else:
            label = f'{path} #{rank}'
        item = {
            'kind':          kind,
            'display_title': title,
            'artist':        artist,
            'best_rank':     rank,
            'chart_labels':  [label],
            'image':         it.get('image'),
            'url':           it.get('url'),
            'gate_path':     path,
        }
        key = se._lookup_key(kind, title, artist)
        prev = stream_by_key.get(key)
        if prev is None or rank < prev['best_rank']:
            stream_by_key[key] = item
        elif label not in prev['chart_labels']:
            prev['chart_labels'].append(label)

    # A title that is capped on one service AND unpriced on another is
    # already covered by the full re-price, so it is dropped from the
    # capped population to keep the two passes from pricing it twice.
    # Its services ride along on the full re-price's labels, or the
    # research would return no block for them and the rows that put
    # the title here would stay exactly as they were: The Other Boleyn
    # Girl was blank on BritBox on Amazon and carried on Starz on
    # Amazon, the Starz row won, and the research was asked about
    # Starz alone.
    priced_keys = set(stream_by_key)
    cap_targets = []
    for t in cap_by_key.values():
        if t['entry_key'] not in priced_keys:
            cap_targets.append(t)
            continue
        item = stream_by_key[t['entry_key']]
        for p in sorted(t['platforms']):
            lab = f'{_platform_chart_label(se, t["kind"], p)} #{t["best_rank"]}'
            if lab not in item['chart_labels']:
                item['chart_labels'].append(lab)

    if first_party_out is not None:
        first_party_out['wattpad'] = sorted(set(fp_wattpad))
        first_party_out['comics'] = sorted(set(fp_comics))

    return (list(stream_by_key.values()), list(headline_by_key.values()),
            total, researched, baseline, cap_targets)


def _collect_cap_target(se, stored_keys, cap_by_key: dict,
                         path: str, rank: int, it: dict,
                         title: str, state: str = 'platform_cap') -> None:
    """Fold one row needing a service-scoped reading into the per-entry
    population. `state` is the condition that put it here, kept only so
    the run log can say how many of each there were."""
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
            'states':        set(),
            'identity':      {},
        }
    tgt['platforms'].add(platform_key)
    tgt['rows'].append(path)
    tgt['states'].add(state)
    if rank < tgt['best_rank']:
        tgt['best_rank'] = rank

    # What the rendered row knows about WHICH WORK this is on THIS
    # service. The panels carry a release year, a film-or-series
    # classification, the catalog's own path and a one-line synopsis,
    # and none of it used to travel with the target, so a re-pricing
    # pass saw nothing but a bare title string. That is why a Fargo
    # or an Alone or a Naked Gun could not be resolved and was held.
    # Collected for every target; only the disambiguation pass renders
    # it into the prompt (`_cap_research_items(with_identity=True)`),
    # so the nightly gate's prompts are byte-identical to before.
    #
    # Lowest rank wins per service, which is the row on the service's
    # full list rather than a short filtered view of it.
    ident = tgt['identity'].get(platform_key)
    if ident is None or rank < ident.get('rank', 10 ** 9):
        tgt['identity'][platform_key] = {
            'service':  platform_key,
            'rank':     rank,
            'category': (it.get('category_display') or '').strip(),
            'year':     it.get('year') or '',
            'path':     (it.get('url') or '').strip(),
            'synopsis': (it.get('description') or '').strip(),
        }


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

    # The same per-service credibility floor the narrow merge applies
    # (2026-09-25). A whole-item result carries one block per service
    # and each block is judged against ITS service's own priced rows:
    # a reading an order of magnitude under the bottom of what already
    # charts there is a failed call, not a quiet title. The Other
    # Boleyn Girl came through here at 144 a day on BritBox, whose
    # rows bottom out in the low thousands, and the derived Prime
    # Video breakout then computed 78 from it. Dropping the block
    # leaves the row blank for tonight's retry, which is the honest
    # state; a result with no block left is dropped whole.
    # Keyed by (kind, service): a service key can name different
    # stores for different kinds (`apple` is Apple Music for a song
    # and Apple Books for a book) and their levels are not comparable.
    floors = _rail_credibility_floors_by_kind(items)
    for k in list(results):
        res = results[k]
        blocks = res.get('by_platform')
        if not isinstance(blocks, dict) or not blocks:
            continue
        kind = str(res.get('kind') or k.split(':', 1)[0])
        kept: dict = {}
        for p, blk in blocks.items():
            try:
                v = int((blk or {}).get('us_estimate') or 0)
            except (TypeError, ValueError):
                v = 0
            floor = max(100, floors.get((kind, p), 0))
            if v < floor:
                logger.info("coverage_gate: dropping %s@%s at %d, under "
                            "the service's own floor of %d", k, p, v,
                            floor)
                continue
            kept[p] = blk
        if len(kept) == len(blocks):
            continue
        if not kept:
            results.pop(k, None)
            continue
        res['by_platform'] = kept
        agg = sum(int((b or {}).get('us_estimate') or 0)
                  for b in kept.values())
        if agg > 0:
            res['us_estimate'] = agg
            for f, mult in (('us_estimate_low', 0.75),
                            ('us_estimate_high', 1.20)):
                try:
                    cur = int(res.get(f) or 0)
                except (TypeError, ValueError):
                    cur = 0
                if f == 'us_estimate_low' and (cur <= 0 or cur > agg):
                    res[f] = int(agg * mult)
                if f == 'us_estimate_high' and (cur <= 0 or cur < agg):
                    res[f] = int(agg * mult)
    if not results:
        return 0

    # A fresh whole-item result replaces the stored entry, but it is
    # only fresh about the services it was asked about. A reading the
    # entry already held for another service is a reading taken for
    # that service and stays; blanking it would hand that rail a hole
    # to fill tomorrow with a number about nothing. Republic of Doyle
    # priced for Roku must not lose its Tubi reading on the way in.
    for k, res in results.items():
        prev = items.get(k)
        if not isinstance(prev, dict):
            continue
        old_blocks = prev.get('by_platform')
        new_blocks = res.get('by_platform')
        if not isinstance(old_blocks, dict) or not old_blocks:
            continue
        if not isinstance(new_blocks, dict):
            new_blocks = {}
        merged = dict(old_blocks)
        merged.update(new_blocks)
        if len(merged) == len(new_blocks):
            continue
        res['by_platform'] = merged
        agg = sum(int((b or {}).get('us_estimate') or 0)
                  for b in merged.values() if isinstance(b, dict))
        if agg > 0:
            res['us_estimate'] = agg
            res['us_estimate_low'] = min(
                agg, int(res.get('us_estimate_low') or 0) or agg)
            res['us_estimate_high'] = max(
                agg, int(res.get('us_estimate_high') or 0) or agg)

    items.update(results)
    snap['items'] = items
    snap['count'] = len(items)
    snap.setdefault('target_date', target_date_iso)
    snap['coverage_gate_at'] = datetime.now(timezone.utc).isoformat()
    _base.write_snapshot('stream_estimates', snap)
    return len(results)


def _cap_research_items(se, cap_targets: list[dict],
                         with_identity: bool = False,
                         force_tier: str = '',
                         force_search: Optional[bool] = None) -> list[dict]:
    """Estimator items for the capped population, one per stored
    entry, naming every service that needs a reading of its own so the
    research returns a block for each.

    `with_identity` attaches the per-service WHICH WORK detail the
    rendered rows carry (year, film or series, catalog path,
    synopsis). Off by default so the nightly gate's prompts do not
    move; the disambiguation pass turns it on, because a bare title
    string is exactly what leaves an ambiguous title unresolvable.
    `force_tier` and `force_search` ride onto the item for the same
    pass, which wants the deeper model and a live search on rows the
    rank rule would otherwise send to the light tier with no search.
    """
    items = []
    for t in cap_targets:
        labels = [f'{_platform_chart_label(se, t["kind"], p)} '
                  f'#{t["best_rank"]}'
                  for p in sorted(t['platforms'])]
        item = {
            'kind':          t['kind'],
            'display_title': t['display_title'],
            'artist':        t['artist'],
            'best_rank':     t['best_rank'],
            'chart_labels':  labels,
        }
        if force_tier:
            item['force_tier'] = force_tier
        if force_search is not None:
            item['force_search'] = bool(force_search)
        if with_identity:
            ident = t.get('identity') or {}
            rows = []
            for p in sorted(t['platforms']):
                rec = dict(ident.get(p) or {'service': p})
                rec['service'] = p
                rec['service_label'] = _platform_chart_label(se, t['kind'], p)
                rows.append(rec)
            if rows:
                item['service_identity'] = rows
        items.append(item)
    return items


# How far under a service's own priced rows a fresh reading may land
# before it reads as a failed call rather than a quiet title. A tenth
# of the service's fifth percentile: an order of magnitude below the
# bottom of what already charts there. Deliberately loose, because a
# deep-catalog title genuinely does read low and only an absurdity
# should be refused.
_RAIL_FLOOR_FRACTION = 0.10
_RAIL_FLOOR_MIN_ROWS = 20


def _rail_credibility_floors(items: dict) -> dict:
    """{service key: the lowest a fresh reading for it may be}.

    Built from the service's own priced rows, so each service is judged
    against itself: the rails span three orders of magnitude and one
    absolute floor cannot describe both a comics panel and Netflix. A
    service with too few priced rows to have a shape gets no floor.
    """
    pools: dict = {}
    for entry in (items or {}).values():
        if not isinstance(entry, dict):
            continue
        for key, blk in (entry.get('by_platform') or {}).items():
            if not isinstance(blk, dict):
                continue
            if blk.get('est_basis') in _FIRST_PARTY_BASES:
                continue     # honest at any level; not a research row
            try:
                v = int(blk.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                pools.setdefault(key, []).append(v)
    out: dict = {}
    for key, vals in pools.items():
        if len(vals) < _RAIL_FLOOR_MIN_ROWS:
            continue
        vals.sort()
        p05 = vals[int(len(vals) * 0.05)]
        out[key] = int(p05 * _RAIL_FLOOR_FRACTION)
    return out


def _rail_credibility_floors_by_kind(items: dict) -> dict:
    """`_rail_credibility_floors`, pooled per (kind, service key).
    Blocks carrying a first-party derivation are left out of the pool:
    they can honestly sit far under the researched rows and would
    otherwise pull the floor down to nothing."""
    pools: dict = {}
    for key, entry in (items or {}).items():
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get('kind') or str(key).split(':', 1)[0])
        for p, blk in (entry.get('by_platform') or {}).items():
            if not isinstance(blk, dict):
                continue
            if blk.get('est_basis') in _FIRST_PARTY_BASES:
                continue
            try:
                v = int(blk.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                pools.setdefault((kind, p), []).append(v)
    out: dict = {}
    for kp, vals in pools.items():
        if len(vals) < _RAIL_FLOOR_MIN_ROWS:
            continue
        vals.sort()
        p05 = vals[int(len(vals) * 0.05)]
        out[kp] = int(p05 * _RAIL_FLOOR_FRACTION)
    return out


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
    if not cap_targets:
        return stats
    if not results:
        # Every title held. Name them all so the run log says which
        # rows kept their correction rather than going quiet.
        stats['no_result'] = [t['entry_key'] for t in cap_targets]
        return stats

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    floors = _rail_credibility_floors(items)

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
            if v < max(100, floors.get(p, 0)):
                # Same credibility floor the merge above applies, in
                # the two forms it takes. A sub-100 reading on a
                # charting row is a failed call, not an audience. So is
                # one that lands an order of magnitude below the bottom
                # of the service's own priced rows: a title cannot
                # chart on a service and read far under everything else
                # that charts there. Rick and Morty came back at 548 on
                # Hulu, whose priced rows bottom out near 42,000, off a
                # working that cited a top-10 band and then used a
                # weekly anchor of 3,900. Held rather than written; the
                # row keeps a number about its own service and tonight's
                # pass tries again.
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
    the research pass AND the terminal bracket pass. Never raises.

    Reaching this means the terminal pass could not hold a bracket for
    the row (no valued row anywhere on its list and no ceiling on file
    for its service, or a gap too tight for two rows). That is a bug
    in the pass to fix the same day, not a row to wait on."""
    try:
        import boto3
        body_lines = [
            'These Trends items still have no US Audience value after '
            'the research pass and the terminal bracket pass. The '
            'bracket pass reasons a value for every blank row from the '
            'rows either side of it on its own list, so a row reaching '
            'this email means that pass could not hold a bracket for '
            'it. Treat as a defect in the pass, not a wait for the '
            'next run.',
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


def _run_first_party(target_date_iso: str, meter: Any,
                     wattpad_keys: list, comics_keys: list,
                     dry_run: bool = False) -> dict[str, Any]:
    """Derive the Wattpad and comics readings from the platforms' own
    figures and write them into the store.

    Wattpad is re-levelled as a whole chart set, not just the blank
    rows: the rate and the share are reasoned once for the set, and a
    set priced half from its own reads and half from per-title
    research would carry two levels on one rail. Comics: every row
    without a reading for its service is placed inside its series on
    that chart, or read from its Libby holds. Both write through the
    normal snapshot boundary so the 60-day distinctness backstop and
    natural digits apply.
    """
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import first_party_derivation as fp
    from scripts.trends_scrapers import _base

    out: dict[str, Any] = {'wattpad': {}, 'comics': {}}
    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    client = fp.anthropic_client()
    try:
        out['wattpad'] = fp.run_wattpad(items, target_date_iso,
                                        client=client, spend_monitor=meter)
    except Exception:
        logger.exception("coverage_gate: Wattpad first-party pass failed "
                         "(non-fatal)")
    try:
        out['comics'] = fp.run_comics(items, target_date_iso,
                                      client=client, spend_monitor=meter)
    except Exception:
        logger.exception("coverage_gate: comics first-party pass failed "
                         "(non-fatal)")
    wrote = (out['wattpad'].get('written') or 0) \
        + (out['comics'].get('holds') or 0) \
        + (out['comics'].get('series') or 0) \
        + (out['comics'].get('orphan_research') or 0)
    out['written'] = wrote
    if wrote and not dry_run:
        snap['items'] = items
        snap['count'] = len(items)
        snap.setdefault('target_date', target_date_iso)
        snap['coverage_gate_at'] = datetime.now(timezone.utc).isoformat()
        if out['wattpad'].get('params'):
            snap[fp.PARAMS_KEY] = out['wattpad']['params']
        _base.write_snapshot('stream_estimates', snap)
    logger.info("coverage_gate: first-party pass wrote %d reading(s): "
                "Wattpad %d of %d stories (%s parameters; %d blank rows "
                "were on the board), comics %d from holds, %d inside "
                "their series, %d under a sized series, %d could not be "
                "derived (%d blank comics rows were on the board)",
                wrote, out['wattpad'].get('written') or 0,
                out['wattpad'].get('rows') or 0,
                out['wattpad'].get('params_basis') or 'none',
                len(wattpad_keys),
                out['comics'].get('holds') or 0,
                out['comics'].get('series') or 0,
                out['comics'].get('orphan_research') or 0,
                len(out['comics'].get('cannot') or []), len(comics_keys))
    return out


def _tally_rendered(cards: dict) -> dict[str, Any]:
    """Count every rendered non-Film row by how it came by its number.

    Per-list as well as board-wide: a board-wide percentage says
    something is wrong, the per-list split says where. Carried,
    rank-tier and bracketed are counted apart from researched: a
    carried row is a real reading of that title going slightly stale,
    a rank-tier row is a number that says nothing about the title, a
    bracketed row is today's reasoned answer awaiting a real reading.
    `still_missing` is what none of those covered.
    """
    counts = {'total': 0, 'researched': 0, 'rendered': 0, 'carried': 0,
              'rank_tier': 0, 'platform_cap': 0, 'cross_service': 0,
              'bracketed': 0}
    per_list: dict[str, dict[str, int]] = {}
    still_missing: list[tuple[str, str]] = []
    for path, _rank, it in _walk_rendered(cards):
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            continue
        if path.startswith('fused_trending') and _fused_row_is_film_only(it):
            continue
        counts['total'] += 1
        bucket = per_list.setdefault(path, {'total': 0, 'carried': 0,
                                             'rank_tier': 0,
                                             'platform_cap': 0,
                                             'cross_service': 0,
                                             'bracketed': 0})
        bucket['total'] += 1
        state = _audience_state(it)
        if state == 'missing':
            still_missing.append((path, _item_title(it)))
            continue
        counts['rendered'] += 1
        counts[state] += 1
        if state != 'researched':
            bucket[state] += 1
    out: dict[str, Any] = dict(counts)
    out['per_list'] = per_list
    out['still_missing'] = still_missing
    return out


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
    first_party: dict = {}
    (stream_items, headline_items,
     total, researched, baseline, cap_targets) = collect_missing(
        payload, first_party_out=first_party)
    fp_wattpad = first_party.get('wattpad') or []
    fp_comics = first_party.get('comics') or []
    cap_rows = sum(len(t['rows']) for t in cap_targets)
    cap_blocks = sum(len(t['platforms']) for t in cap_targets)
    cross_titles = sum(1 for t in cap_targets
                       if 'cross_service' in (t.get('states') or ()))
    pct_before = (100.0 * researched / total) if total else 100.0
    logger.info("coverage_gate: %d rendered non-Film items, %d researched "
                "(%.2f%%), %d need pricing (%d stream-kind, %d headline), "
                "%d row(s) want a reading of their own service across %d "
                "service reading(s) on %d title(s), %d of which were "
                "showing another service's reading; %d Wattpad and %d "
                "comics row(s) blank, to be derived from their own figures",
                total, researched, pct_before,
                len(stream_items) + len(headline_items),
                len(stream_items), len(headline_items),
                cap_rows, cap_blocks, len(cap_targets), cross_titles,
                len(fp_wattpad), len(fp_comics))

    summary: dict[str, Any] = {
        'total': total,
        'researched_before': researched,
        'researched_before_pct': round(pct_before, 2),
        'first_party_wattpad_blank': len(fp_wattpad),
        'first_party_comics_blank': len(fp_comics),
        'first_party_written': 0,
        'priced_stream': 0,
        'priced_headline': 0,
        'capped_before': cap_rows,
        'cap_titles': len(cap_targets),
        'cross_service_titles': cross_titles,
        'cap_blocks_written': 0,
        'capped_after': 0,
        'cross_service_after': 0,
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

    # Wattpad and comics: derived from the platforms' own figures,
    # never per-title research. Runs every time, blank rows or not,
    # so the Wattpad set is always one level and a comics volume
    # never outlives its siblings' readings.
    try:
        fp_stats = _run_first_party(target_date_iso, meter,
                                    fp_wattpad, fp_comics)
        summary['first_party_written'] = fp_stats.get('written') or 0
        summary['first_party'] = {
            'wattpad': {k: v for k, v in (fp_stats.get('wattpad') or {})
                        .items() if k != 'no_reads'},
            'wattpad_no_reads': (fp_stats.get('wattpad') or {})
            .get('no_reads') or [],
            'comics': {k: v for k, v in (fp_stats.get('comics') or {})
                       .items() if k not in ('trail',)},
        }
    except Exception:
        logger.exception("coverage_gate: first-party pass failed "
                         "(non-fatal)")

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
    tally = _tally_rendered((payload2 or {}).get('cards') or {})
    still_missing = tally['still_missing']

    # Terminal pass (Jenna 2026-09-25: "it should not have not found
    # these but should have figured out how to reason answers to
    # them"). Whatever the research left blank is reasoned from the
    # rows either side of it on its own list, written as a reading for
    # its own service, and rendered today. Then the board is
    # recomputed and re-tallied; the alert below fires only for a row
    # that survives even this, which is a bug to fix, not a wait for
    # the next run.
    if still_missing:
        try:
            from scripts.trends_scrapers import terminal_bracket as tb
            tb_stats = tb.fill(payload2, still_missing, target_date_iso)
            summary['bracketed_written'] = tb_stats['written']
            summary['bracketed_entries_created'] = tb_stats['entries_created']
            summary['bracketed_skipped'] = tb_stats['skipped']
            logger.info("coverage_gate: terminal pass bracketed %d of %d "
                        "blank row(s) from their neighbours (%d new "
                        "entries, %d skipped)", tb_stats['written'],
                        len(still_missing), tb_stats['entries_created'],
                        len(tb_stats['skipped']))
            for row in tb_stats['trail']:
                logger.info("coverage_gate bracket: %s on %s -> %s  [%s]",
                            row['title'], row['service'] or 'list',
                            f'{row["value"]:,}', row['path'])
            for path, why in tb_stats['skipped']:
                logger.warning("coverage_gate bracket skipped: %s  [%s]",
                               why, path)
            if tb_stats['written']:
                try:
                    trends_iq.invalidate_live_compute_view_caches()
                except Exception:
                    logger.exception("coverage_gate: cache purge after "
                                     "bracket failed (non-fatal)")
                payload2 = trends_iq.compute_view(dict(_DEFAULT_FILTERS),
                                                   force_refresh=True)
                tally = _tally_rendered((payload2 or {}).get('cards') or {})
                still_missing = tally['still_missing']
        except Exception:
            logger.exception("coverage_gate: terminal bracket pass failed "
                             "(non-fatal)")

    total2 = tally['total']
    researched2 = tally['researched']
    rendered2 = tally['rendered']
    carried2 = tally['carried']
    rank_tier2 = tally['rank_tier']
    capped2 = tally['platform_cap']
    cross2 = tally['cross_service']
    bracketed2 = tally['bracketed']
    per_list = tally['per_list']
    summary['bracketed_after'] = bracketed2

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
    summary['cross_service_after'] = cross2
    summary['by_list'] = {
        name: {
            'total': v['total'],
            'carried': v['carried'],
            'rank_tier': v['rank_tier'],
            'platform_cap': v['platform_cap'],
            'cross_service': v['cross_service'],
            'bracketed': v['bracketed'],
            'carried_pct': round(100.0 * v['carried'] / v['total'], 2),
            'rank_tier_pct': round(100.0 * v['rank_tier'] / v['total'], 2),
        }
        for name, v in sorted(per_list.items())
        if (v['carried'] or v['rank_tier'] or v['platform_cap']
            or v['cross_service'] or v['bracketed'])
    }

    logger.info("coverage_gate: FINAL coverage researched=%.2f%% "
                "rendered=%.2f%% carried=%.2f%% rank_tier=%.2f%% "
                "bracketed=%d (total=%d, still_missing=%d, cap "
                "corrections %d -> %d, spend=$%.2f)",
                summary['researched_after_pct'],
                summary['rendered_after_pct'],
                summary['carried_after_pct'],
                summary['rank_tier_after_pct'],
                bracketed2, total2, len(still_missing),
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
