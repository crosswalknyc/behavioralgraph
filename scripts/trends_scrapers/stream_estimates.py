"""
US audience-size estimator for podcasts, songs, and streaming titles.

For every top item on the Podcasts / Music / Streaming tabs, we ask
Claude Sonnet 4.5 (with the native `web_search` tool) to painstakingly
research the item and reason about its current weekly US audience:

  - Podcasts: US weekly listeners (Edison Podcast Metrics, Podtrac,
              Chartable, publisher press releases, Nielsen Podcast
              Ratings, MRC Podcast Ratings).
  - Songs:    US weekly streams across all DSPs (Luminate week-over-
              week reports, Spotify for Artists screenshots, Billboard
              charts + Chart Beat, Chartmetric snapshots).
  - Films/TV: US weekly viewers or household views (Nielsen streaming
              top-10, Whip Media, Samba TV, Parrot Analytics, Variety
              Insight, Deadline chart, Netflix Tudum, Prime Video
              Roll Call, Max Weekly Top 10).

If no direct citation is available, Claude reasons from ADJACENT
signals — chart position × typical audience for that tier, historic
week-1 vs week-2 curves for the same franchise, comparable-title
benchmarks. Claude always returns a range (low / mid / high) plus a
confidence tag so the dashboard reader can tell "solid Nielsen data"
apart from "inferred from chart position".

Day-over-day trend: we snapshot to a dated S3 key each run; on the
next run we look up yesterday's estimate for the same normalized
title and compute (delta_pct, direction) so the dashboard can render
an up / down / stable arrow next to each item.

Output shape (kind='meta'):

    {
      "source":     "stream_estimates",
      "kind":       "meta",
      "fetched_at": "...",
      "generated_at": "...",
      "items": {
        "podcast:crime junkie": {
          "kind":            "podcast",
          "display_title":   "Crime Junkie",
          "artist":          "Audiochuck",
          "us_estimate":     12_500_000,
          "us_estimate_low":  8_000_000,
          "us_estimate_high": 18_000_000,
          "confidence":      "high",
          "unit_label":      "weekly US listeners",
          "method":          "Edison Podcast Metrics puts it at #3...",
          "sources":         ["https://...", "https://..."],
          "delta_pct":       0.204,
          "direction":       "up"
        },
        ...
      },
      "count": 45
    }

Standalone:

    python3 -m scripts.trends_scrapers.stream_estimates
    python3 -m scripts.trends_scrapers.stream_estimates --only podcast
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/'
_S3_DATED  = 'trends_iq_snapshots/{date}/'


# -------------------------------------------------------------------------
# How many items to research per category. Sonnet 4.5 + web_search costs
# ~$0.02/item, so 60 podcasts + 100 songs + 60 streaming + 100 books = 320
# items/day ≈ $6.50/day. Well within budget.
#
# Caps raised 2026-08-04 (Jenna: "make sure there are numbers on
# everything") so every row the Trends IQ dashboard actually renders
# carries a US-projected engagement estimate, not just the top slice.
# Each cap targets union-of-top-N across that kind's panels post-dedup:
#   podcasts:  5 platforms x top 50 unique     = 150 (bumped 2026-08-07
#              from 60 - Apple/Spotify/Audible/Netflix panels each ship
#              50-100 rows and were being culled to ~18 estimates each).
#   songs:     5 music panels x top 20 unique  = 100
#   streaming: 6 platforms x top 25 unique     = 200 (bumped 2026-08-05
#              from 60 - was leaving Disney+ / Hulu with only ~5 estimates
#              each because the sort-by-best_rank cap culled everything
#              below rank 10 across platforms).
#   books:     6 book+libby panels x top 50    = 220 (bumped 2026-08-07
#              from 100 - Apple Books ships 100 rows / Audible 78, and
#              the previous cap only surfaced the top ~30 of each panel;
#              rows below that rendered without a reader-count badge).
# -------------------------------------------------------------------------
# Bumped 2026-08-20 (Jenna: "ensuring there are metrics for all") to
# match the dashboard's rendered row count per panel. Prior caps were
# leaving ranks 30-100 of each Music / Podcast / Book / Streaming
# panel without a chip, which read as "why does the top row have a
# number and the rest don't?" Coverage on the top-N of each panel is
# now the SLA rather than "top-N cross-platform after global dedup".
_MAX_PODCAST_ITEMS   = 300   # was 150 - 4 panels x top ~60 unique
_MAX_SONG_ITEMS      = 250   # was 100 - 4 panels x top ~60 unique
_MAX_STREAMING_ITEMS = 300   # was 200 - 9 platforms (netflix, disneyplus,
                              # hulu, max, primevideo, espnplus, britbox,
                              # mgmplus, starz) x top ~30-40 unique
_MAX_BOOK_ITEMS      = 400   # was 220 - 3 book + 3 libby panels each
                              # ship 30-100 unique-per-panel
# Wattpad: 6 rails (Hot 50 + Originals 25 + 4 genre rails 25 each =
# 175 gross). Cross-rail dedup collapses a lot (a Wattpad Original
# tagged Romance can appear on both `wattpad_originals` and
# `wattpad_romance`). 200 gives headroom for days with minimal
# cross-rail overlap.
_MAX_WATTPAD_ITEMS   = 200
# Goodreads Most-Read-This-Week ships ~50 titles on one rail. 60 leaves
# a small buffer if Goodreads grows the panel or we later add a second
# rail (Choice Awards / genre-specific). Kept as its own kind
# ('goodreads_book') so anchor tiers don't cross-contaminate the
# Amazon / Apple / Audible / Libby platform ceilings.
_MAX_GOODREADS_ITEMS = 60
# FAST-channels: 4 platforms x top 100 = 400 gross, ~250-300 after
# cross-platform dedup (Alone / Everybody Loves Raymond / etc. appear
# on 2-3 platforms). Cap at 350 for safety headroom on days there is
# little cross-platform overlap. ~$6-7/day added to the daily Claude
# spend at full 100-row coverage.
_MAX_FAST_ITEMS      = 350
# Gaming: 3 panels today - Xbox Game Pass Ultimate (25) + Meta Quest
# Top Free (~20) + Meta Quest Top Paid (~10). Cross-panel dedup
# collapses any title that charts on multiple providers (e.g. Beat
# Saber if it ever landed on Game Pass). Room to grow when we add
# PlayStation Plus / Nintendo Switch Online / Steam later.
_MAX_GAMING_ITEMS    = 140
# FAST-CHANNEL RANKER: micro-channels inside each FAST platform
# (LaurenZSide, Mythical 24/7, Nick Jr. Pluto TV, Forensic Files
# 24/7, ...). Roku ships ~619, Amazon ~655, Pluto ~410, Tubi ~169
# = ~1,850 total, so we cap at top-100 by airings/wk per platform
# and DO NOT cross-platform dedup: the same channel name on Pluto
# vs Roku vs Amazon represents entirely different audiences on
# different distribution rails, so each platform's copy gets its
# own Claude call. Added 2026-08-21 (Jenna: "channel ranker" sub-
# tab, "ranks based on views and give an estimate of how many
# views each channel had"). ~$8/day added spend at 400 items.
_MAX_FAST_CHANNEL_ITEMS = 400

_WEBSEARCH_MODEL      = (os.environ.get('STREAM_ESTIMATES_MODEL')
                          or 'claude-sonnet-4-5')
_WEBSEARCH_MAX_TOKENS = 1200
_WEBSEARCH_MAX_USES   = 3        # per-item web_search calls (some tier-1
                                  # research warrants 2-3 queries)
_WEBSEARCH_TIMEOUT_S  = 60
_CONCURRENCY          = 6


# MUST stay in lock-step with `trends_iq._CP_STOPWORDS` - the app
# side looks up entries by these keys and any mismatch causes a
# silent lookup miss (item ranks fine, no stream number).
_STOPWORDS = {
    'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
    'trending', 'today', 'now', 'news', 'latest', 'best',
}


def _cp_normalize(text: str) -> str:
    """Case-fold, strip punctuation, drop stopwords, collapse spaces.
    Mirrors `trends_iq._cp_normalize` byte-for-byte."""
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def _s3():
    return boto3.client('s3',
                         region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _read_snapshot(source: str) -> Optional[dict]:
    """Return the S3 snapshot at latest/{source}.json or None."""
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=f'{_S3_LATEST}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _read_dated_snapshot(source: str, days_back: int) -> Optional[dict]:
    """Return the dated snapshot from N days ago, or None."""
    d = date.today() - timedelta(days=days_back)
    key = f'{_S3_DATED.format(date=d.isoformat())}{source}.json'
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


# -------------------------------------------------------------------------
# Collect unique items across per-platform snapshots
# -------------------------------------------------------------------------
def _dedupe_by_key(items: list[dict], key_fn) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        k = key_fn(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _collect_podcasts(max_items: int = _MAX_PODCAST_ITEMS) -> list[dict]:
    """Union top items across Apple/Spotify/Amazon/Audible/Netflix
    podcast panels, deduped by normalized title. Preserves best rank
    across platforms so #1-on-Apple beats #5-on-Spotify."""
    snap = _read_snapshot('podcast_charts')
    if not snap:
        return []
    per: dict[str, dict] = {}
    for src_slug, panel in (snap.get('sources') or {}).items():
        # Bumped 2026-08-20 from [:50] to [:80] to cover every visible
        # row on the dashboard (each podcast panel renders up to
        # 80-100 rows and Jenna wants a US-listeners chip on all of
        # them). Post-dedup + best-rank sort still tops out at
        # _MAX_PODCAST_ITEMS (300 as of the same day).
        for i, it in enumerate((panel.get('items') or [])[:80]):
            title = (it.get('title') or '').strip()
            key   = _cp_normalize(title)
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'podcast',
                'display_title': title,
                'artist':        (it.get('artist') or '').strip(),
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            label = panel.get('label') or src_slug
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_songs(max_items: int = _MAX_SONG_ITEMS) -> list[dict]:
    """Union top tracks across Spotify/Apple/YouTube/Shazam/TikTok/
    Amazon music panels, keyed by (title + artist) so different tracks
    named 'Home' don't collide."""
    snap = _read_snapshot('music_charts')
    if not snap:
        return []
    per: dict[str, dict] = {}
    for src_slug, panel in (snap.get('sources') or {}).items():
        # Bumped 2026-08-20 from [:30] to [:80] so ranks 31-80 of each
        # music panel (Spotify, Apple, YouTube, Shazam ship 100 rows
        # each) surface with a US-streams chip.
        for i, it in enumerate((panel.get('items') or [])[:80]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'song',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            label = panel.get('label') or src_slug
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


_STREAMING_SLUGS = (
    ('netflix',    'Netflix'),
    ('disneyplus', 'Disney+'),
    ('hulu',       'Hulu'),
    ('max',        'Max'),
    ('primevideo', 'Prime Video'),
    ('espnplus',   'ESPN+'),
    # 2026-08-20: BritBox (BBC + ITV premium British TV) and MGM+
    # (Amazon-owned premium, formerly Epix). Anchors + per-platform
    # ceilings live in _STREAMING_PLATFORMS_META below.
    ('britbox',    'BritBox'),
    ('mgmplus',    'MGM+'),
    # 2026-08-20: Starz (Lionsgate premium, ~12M US subs; Power +
    # Outlander + Spartacus + Starz Originals). See META entry.
    ('starz',      'Starz'),
)


# FAST-channel platforms. The 4 platforms live inside a SINGLE
# `fast_channels` snapshot under `sources[<slug>].items[]` (mirrors
# music's shape, not streaming's one-file-per-platform shape). Chart
# labels feed the `_CHART_LABEL_TO_PLATFORM` matcher so Claude knows
# which FAST platform an item charts on.
_FAST_SLUGS = (
    ('roku',   'Roku Channel'),
    ('tubi',   'Tubi'),
    ('pluto',  'Pluto TV'),
    ('amazon', 'Amazon Live TV'),
)


# Gaming platforms. Snapshot layout is heterogenous: Xbox writes one
# file per platform (`xbox_gamepass.json`) with items on the top-level
# `national` key, while Meta Quest writes one file (`meta_quest.json`)
# with two panels under `sources.{meta_quest_free,meta_quest_paid}` -
# same pattern FAST channels use. `_collect_gaming` handles both.
# Chart labels feed `_CHART_LABEL_TO_PLATFORM`.
#
# Tuple shape: (panel_key, panel_label, snapshot_slug, source_key_or_None)
# When source_key is None -> read `national` off the snapshot named
#   `snapshot_slug`. When set -> read `sources[source_key].items`.
_GAMING_SLUGS = (
    ('xbox_gamepass',      'Xbox Game Pass Ultimate', 'xbox_gamepass', None),
    ('meta_quest_free',    'Meta Quest - Top Free',   'meta_quest',    'meta_quest_free'),
    ('meta_quest_paid',    'Meta Quest - Top Paid',   'meta_quest',    'meta_quest_paid'),
    # Steam. Two rails packed in one snapshot. Most Played is anchored
    # to Valve's live concurrent-player integer (published by the JSON
    # API), Top Sellers is a pure research pass.
    ('steam_most_played',  'Steam - Most Played',     'steam_charts',  'steam_most_played'),
    ('steam_top_sellers',  'Steam - Top Sellers',     'steam_charts',  'steam_top_sellers'),
)


def _collect_streaming(max_items: int = _MAX_STREAMING_ITEMS) -> list[dict]:
    """Union top titles across the 6 streaming platform snapshots,
    keyed by normalized title. Preserves film/tv distinction (from
    `category_display`) so 'Barbie' the film and 'Barbie' the show
    don't collide."""
    per: dict[str, dict] = {}
    for slug, label in _STREAMING_SLUGS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
        buckets: list[tuple[str, list]] = []
        if slug == 'netflix':
            buckets.append(('film', snap.get('us_films') or []))
            buckets.append(('tv',   snap.get('us_tv')    or []))
        else:
            buckets.append(('mixed', snap.get('national') or []))
        # Bumped 2026-08-20 from 30 to 40 per bucket so the full
        # dashboard rail (up to 20 films + 20 tv shown per platform,
        # plus the historic "sustained" second-page rows) is covered.
        # Was 15 -> 30 (2026-08-05) -> 40 (2026-08-20).
        for kind, items in buckets:
            for i, it in enumerate(items[:40]):
                title  = (it.get('title') or '').strip()
                cat    = (it.get('category_display') or kind or '').lower()
                item_kind = 'film' if cat == 'film' else ('tv' if 'tv' in cat else 'title')
                key = f'{item_kind}:{_cp_normalize(title)}'
                if not _cp_normalize(title):
                    continue
                rank = i + 1
                e = per.setdefault(key, {
                    'kind':          item_kind,
                    'display_title': title,
                    'artist':        '',
                    'best_rank':     rank,
                    'chart_labels':  [],
                    'image':         it.get('image'),
                    'url':           it.get('url'),
                })
                e['chart_labels'].append(f'{label} #{rank}')
                if rank < e['best_rank']:
                    e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_fast(max_items: int = _MAX_FAST_ITEMS) -> list[dict]:
    """Union top titles across the 4 FAST-channel snapshots (Roku
    Channel, Tubi, Pluto TV, Amazon Live TV), keyed by
    `fast_film:<norm>` or `fast_tv:<norm>` so estimates don't collide
    with paid-SVOD estimates for the same title (e.g. Interview with
    the Vampire runs on both HBO Max and Amazon Live TV, but the
    ad-supported weekly audience is a totally different number).

    Unlike streaming, all 4 FAST platforms sit inside ONE snapshot
    file (`fast_channels.json`) under `sources[<slug>].items` because
    the scraper hits a single JustWatch GraphQL endpoint."""
    snap = _read_snapshot('fast_channels')
    if not snap:
        return []
    per: dict[str, dict] = {}
    sources = (snap.get('sources') or {})
    for slug, label in _FAST_SLUGS:
        platform_block = sources.get(slug) or {}
        items = platform_block.get('items') or []
        # Top-100 per platform: the FAST tab renders the full 100-row
        # top-list per platform, so we research every row to guarantee
        # every visible chip has a number. Cross-platform dedup
        # collapses the 400 gross to ~250-300 unique after the incremental
        # `as_of_date == today` gate in `fetch()` skips items already
        # covered by an earlier intra-day run.
        for i, it in enumerate(items[:100]):
            title = (it.get('title') or '').strip()
            if not _cp_normalize(title):
                continue
            cat = (it.get('category_display') or '').lower()
            item_kind = 'fast_film' if cat == 'film' else 'fast_tv'
            key = f'{item_kind}:{_cp_normalize(title)}'
            rank = int(it.get('rank') or (i + 1))
            e = per.setdefault(key, {
                'kind':          item_kind,
                'display_title': title,
                'artist':        '',
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_gaming(max_items: int = _MAX_GAMING_ITEMS) -> list[dict]:
    """Union top games across every gaming-platform panel, keyed by
    `game:<norm_title>`. Games don't collide by title the way songs
    do (there's only one 'Baldur's Gate 3'), so no artist qualifier
    in the key. Publisher rides along on `artist` for prompt context
    only.

    Handles both snapshot layouts:
      - Direct-national (Xbox): items live on `snap['national']`.
      - Sources-keyed (Meta Quest): items live on
        `snap['sources'][source_key]['items']`. Same snapshot may
        back multiple panel keys; cached per-run.
    """
    per: dict[str, dict] = {}
    snap_cache: dict[str, dict] = {}
    for panel_key, label, snapshot_slug, source_key in _GAMING_SLUGS:
        snap = snap_cache.get(snapshot_slug)
        if snap is None:
            snap = _read_snapshot(snapshot_slug) or {}
            snap_cache[snapshot_slug] = snap
        if not snap:
            continue
        if source_key:
            block = ((snap.get('sources') or {}).get(source_key) or {})
            items = block.get('items') or []
        else:
            items = snap.get('national') or snap.get('items') or []
        for i, it in enumerate(items[:25]):
            title = (it.get('title') or '').strip()
            if not _cp_normalize(title):
                continue
            key = f'game:{_cp_normalize(title)}'
            rank = int(it.get('rank') or (i + 1))
            e = per.setdefault(key, {
                'kind':          'game',
                'display_title': title,
                'artist':        (it.get('publisher') or ''),
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
            # Steam Most Played rows carry Valve's live 24-hour peak
            # concurrent players as a hard prior. `_build_prompt` for
            # kind=game surfaces this as `steam_peak_in_game` so the
            # Claude research call only has to reason the weekly
            # multiplier + US share, not the raw active-player count.
            cp = it.get('current_players')
            if cp and 'steam_peak_in_game' not in e:
                try:
                    e['steam_peak_in_game'] = int(cp)
                except (TypeError, ValueError):
                    pass
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_fast_channels(max_items: int = _MAX_FAST_CHANNEL_ITEMS) -> list[dict]:
    """Collect the top-N-by-airings micro-channels on each FAST
    platform. Keyed by `fast_channel:<platform_slug>:<norm_name>`
    with NO cross-platform dedup: the same channel name on Pluto vs.
    Roku vs. Amazon represents distinct audiences on distinct
    distribution rails, so each copy earns its own Claude call.

    Snapshot layout (from build_fast_channel_lineups):
        sources.<platform>.channels = [
          {name, airings, content_type}, ...
        ]

    `airings` is the raw signal we pass to Claude as an intra-
    platform popularity hint - a channel with 631 airings/wk on
    Amazon is clearly a top-tier channel; one with 12 airings/wk
    is fringe. Claude reasons from airings + channel prominence +
    platform MAU to a weekly-viewers number."""
    snap = _read_snapshot('fast_channel_lineups')
    if not snap:
        return []
    sources = (snap.get('sources') or {})

    # Per-platform cap: divide the budget across platforms so no
    # single platform (Amazon at 655 channels) starves the others.
    # Roku ~619 / Amazon ~655 / Pluto ~410 / Tubi ~169. Cap at
    # top-100 per platform so the ranker still surfaces the whole
    # top page of each platform.
    per_platform_cap = max(50, max_items // 4)

    out: list[dict] = []
    for slug, label in _FAST_SLUGS:
        block = sources.get(slug) or {}
        channels = (block.get('channels') or [])
        # Assume the scraper already emits channels sorted by
        # airings desc (build_fast_channel_lineups does this
        # explicitly). Cap defensively.
        for i, ch in enumerate(channels[:per_platform_cap]):
            name = (ch.get('name') or '').strip()
            if not _cp_normalize(name):
                continue
            airings = int(ch.get('airings') or 0)
            content_type = (ch.get('content_type') or '').strip()
            rank = i + 1
            # Encode platform in the key so cross-platform
            # duplicates DON'T collapse. Also stash the platform
            # on the item so the prompt can call it out and the
            # sanitizer knows which platform's ceiling applies.
            key_hint = f'{slug}:{_cp_normalize(name)}'
            out.append({
                'kind':           'fast_channel',
                'display_title':  name,
                # `artist` carries the platform slug so
                # `_lookup_key('fast_channel', name, slug)` produces
                # a platform-scoped key that both this collector AND
                # `trends_iq._annotate_fast_channels_with_views` will
                # emit for lookup. See `_lookup_key` for the
                # exact format.
                'artist':         slug,
                'fast_platform':  slug,
                'airings':        airings,
                'content_type':   content_type,
                'best_rank':      rank,
                'chart_labels':   [f'{label} channel rank #{rank} '
                                    f'({airings:,} airings/wk)'],
                'image':          '',
                'url':            '',
            })
    # Sort by airings desc across all platforms so the highest-
    # signal channels get researched first (parallelism doesn't
    # care about order, but the caller's log is more informative
    # this way).
    out.sort(key=lambda e: e.get('airings') or 0, reverse=True)
    return out[:max_items]


def _collect_books(max_items: int = _MAX_BOOK_ITEMS) -> list[dict]:
    """Union top items across the book_charts snapshot (Amazon /
    Apple / Audible) AND the libby_trends snapshot (LA County
    Library popular ebook + audiobook lists), deduped by
    (normalized title + author).

    Libby rows are folded into the SAME item as their store-panel
    counterparts so a book that appears on both Amazon Best-Sellers
    and Libby Popular eBooks gets a SINGLE Claude call that reasons
    across both platforms. The libby hold_count travels via
    `libby_holds_by_type` so the prompt can call it out."""
    book_snap  = _read_snapshot('book_charts')  or {}
    libby_snap = _read_snapshot('libby_trends') or {}
    per: dict[str, dict] = {}

    # 1. Book stores (Amazon, Apple, Audible).
    # Bumped 2026-08-20 from [:50] to [:100] so every visible row on
    # the Books tab (Apple ships 100, Audible ships 77-78) gets an
    # estimate, not just the top half.
    for src_slug, panel in (book_snap.get('sources') or {}).items():
        for i, it in enumerate((panel.get('items') or [])[:100]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            if not title:
                continue
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'book',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
                'libby_holds_by_type': {},  # populated below
            })
            label = panel.get('label') or src_slug
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank

    # 2. Libby (LA County) - ebook + audiobook + magazine. Fold onto
    #    existing items when the title+author match; create standalone
    #    items when they don't.
    #
    # Magazines added 2026-08-07 - previously the collector iterated
    # only ('ebook', 'audiobook') so the Popular Magazines panel
    # (30 titles) rendered without any reader-count metric even though
    # the Libby snapshot carried the data.
    _LIBBY_SLUG_META = {
        'ebook':     ('libby_ebook',    'Libby: Popular eBooks'),
        'audiobook': ('libby_audio',    'Libby: Popular Audiobooks'),
        'magazine':  ('libby_magazine', 'Libby: Popular Magazines'),
    }
    for src_slug, (plat, chart_prefix) in _LIBBY_SLUG_META.items():
        panel = (libby_snap.get('sources') or {}).get(src_slug) or {}
        for i, it in enumerate((panel.get('items') or [])[:50]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            holds  = int(it.get('holds') or 0)
            if not title:
                continue
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'book',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
                'libby_holds_by_type': {},
            })
            e['chart_labels'].append(f'{chart_prefix} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
            # Preserve the LA County hold count so the prompt can
            # cite it (and reason to project it up to US-wide
            # borrows). Map ebook->libby_ebook / audio->libby_audio /
            # magazine->libby_magazine.
            e['libby_holds_by_type'][plat] = holds

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_goodreads(max_items: int = _MAX_GOODREADS_ITEMS) -> list[dict]:
    """Union top items across the goodreads_charts snapshot (one rail
    today: Most Read This Week), deduped by (normalized title +
    author). Every row carries the Goodreads-side native priors -
    `currently_reading_count` (the community weekly-read count printed
    on the tile), cumulative `avg_rating` + `ratings_count`, and
    whether the book looks like a recent release - so the Claude
    prompt can lean on the ground-truth community signal rather than
    reason from chart position alone. Kept as its own kind
    ('goodreads_book') rather than folded into 'book' because the
    anchor tier is different: Goodreads reflects the broader US
    reading audience (including people who read outside Goodreads
    apps) while Amazon / Apple / Audible / Libby anchor to their own
    platform's US buyer / listener / borrower base.
    """
    snap = _read_snapshot('goodreads_charts') or {}
    per: dict[str, dict] = {}

    _PANEL_META = [
        ('goodreads_most_read', 'Goodreads - Most Read This Week'),
    ]

    sources = snap.get('sources') or {}
    for panel_slug, chart_prefix in _PANEL_META:
        panel = sources.get(panel_slug) or {}
        for i, it in enumerate((panel.get('items') or [])[:50]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or it.get('author') or '').strip()
            if not title:
                continue
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            currently_reading = int(it.get('currently_reading_count') or 0)
            ratings_count = int(it.get('ratings_count') or 0)
            try:
                avg_rating = float(it.get('avg_rating') or 0.0)
            except Exception:
                avg_rating = 0.0
            try:
                published_year = int(it.get('published_year') or 0)
            except Exception:
                published_year = 0
            is_new_release = bool(it.get('is_new_release'))
            e = per.setdefault(key, {
                'kind':          'goodreads_book',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image') or it.get('cover_url'),
                'url':           it.get('book_url') or it.get('url'),
                'goodreads_priors': {
                    'currently_reading_count': currently_reading,
                    'avg_rating':              avg_rating,
                    'ratings_count':           ratings_count,
                    'published_year':          published_year,
                    'is_new_release':          is_new_release,
                },
            })
            e['chart_labels'].append(f'{chart_prefix} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_wattpad(max_items: int = _MAX_WATTPAD_ITEMS) -> list[dict]:
    """Union top items across the wattpad_charts snapshot's 6 rails
    (Hot / Originals / Romance / Teen Fiction / Fanfiction / Fantasy),
    deduped by (normalized title + author). A story that appears on
    multiple rails gets a SINGLE Claude call whose prompt sees every
    rail label + rank the story earned, plus the native Wattpad
    signals (cumulative reads, votes, chapters, Originals flag) so
    per-item reasoning is well-anchored.

    Wattpad exposes native `reads` counts on every story - these are
    CUMULATIVE all-time reads across the platform (not weekly).
    Passed through `native_priors` so Claude can convert them to
    weekly US readers using the platform's ~90M global MAU, ~40-50%
    US share, and a decay factor accounting for tapering read
    velocity over time.
    """
    snap = _read_snapshot('wattpad_charts') or {}
    per: dict[str, dict] = {}

    # Panel slug -> (chart label prefix, us_share_boost). The
    # us_share_boost is passed to Claude as a hint: Wattpad Originals
    # is North-America-heavy per Wattpad Studios public coverage, so
    # per-row US share bumps to ~0.55 vs the platform default ~0.42.
    _PANEL_META = [
        ('wattpad_hot',          'Wattpad - Hot Stories',   1.00),
        ('wattpad_originals',    'Wattpad - Originals',     1.30),
        ('wattpad_romance',      'Wattpad - Romance',       1.00),
        ('wattpad_teen_fiction', 'Wattpad - Teen Fiction',  1.00),
        ('wattpad_fanfiction',   'Wattpad - Fanfiction',    1.00),
        ('wattpad_fantasy',      'Wattpad - Fantasy',       1.00),
    ]

    sources = snap.get('sources') or {}
    for panel_slug, chart_prefix, us_share_boost in _PANEL_META:
        panel = sources.get(panel_slug) or {}
        for i, it in enumerate((panel.get('items') or [])[:50]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or it.get('author') or '').strip()
            if not title:
                continue
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            reads = int(it.get('reads') or 0)
            votes = int(it.get('votes') or 0)
            chapters = int(it.get('chapters') or 0)
            genre = (it.get('genre_primary') or '').strip()
            originals_flag = bool(it.get('wattpad_originals_flag'))
            is_new = bool(it.get('is_new'))
            completed = bool(it.get('is_completed'))
            e = per.setdefault(key, {
                'kind':          'wattpad_story',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('cover_url') or it.get('image'),
                'url':           it.get('story_url') or it.get('url'),
                # Native Wattpad-side priors travel with the item so
                # the Claude prompt can cite them directly.
                'native_priors': {
                    'wattpad_reads_cumulative':    reads,
                    'wattpad_votes':               votes,
                    'wattpad_chapters':            chapters,
                    'wattpad_genre_primary':       genre,
                    'wattpad_originals':           originals_flag,
                    'wattpad_is_completed':        completed,
                    'wattpad_is_new_14d':          is_new,
                    # `us_share_hint` picks the max across rails a
                    # story appears on (Originals rail wins).
                    'us_share_hint':               us_share_boost,
                },
            })
            e['chart_labels'].append(f'{chart_prefix} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
            # If this rail says the story is an Original, elevate the
            # flag on the merged item (an Original tagged Romance
            # appears on both `wattpad_originals` and
            # `wattpad_romance`; only the Originals rail sets the
            # flag).
            if originals_flag:
                e['native_priors']['wattpad_originals'] = True
                if us_share_boost > e['native_priors'].get('us_share_hint',
                                                             1.0):
                    e['native_priors']['us_share_hint'] = us_share_boost

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


# Comics coverage cap: 3 panels * ~50 top rows each, post-dedup by
# (title + author). Amazon Comics ships 60, Apple Books Comics 50,
# Libby Comics 35 = 145 gross; cross-panel dedup collapses maybe
# 5-15 (a title on Amazon Best-Sellers AND Libby, or Amazon AND
# Apple), so 150 covers every visible row with a small ceiling.
_MAX_COMIC_ITEMS = 150


def _collect_comics(max_items: int = _MAX_COMIC_ITEMS) -> list[dict]:
    """Union top items across the comics_charts snapshot (Amazon
    Comics + Apple Books Comics + Libby Comics), deduped by
    (normalized title + author).

    Same fold-onto-existing-item pattern as `_collect_books`: a
    title that appears on both the Amazon Best-Sellers list AND
    Libby's popular-comics list gets a SINGLE Claude call that
    reasons across both platforms, with the LA County hold count
    surfaced via `libby_holds` so the prompt can project it up.

    Panel-source spelling comes from `comics_charts.fetch`:
      amazon_kindle -> Amazon Comics (physical bestsellers)
      apple_comics  -> Apple Books Comics (digital paid)
      libby_comics  -> Libby Comics (US public-library digital)
    Each panel slug matches the platform key in
    `_COMICS_PLATFORMS` so `_focus_keys_from_charts` can highlight
    the on-chart platforms in the prompt.
    """
    comics_snap = _read_snapshot('comics_charts') or {}
    per: dict[str, dict] = {}

    _COMIC_PANEL_META = {
        'amazon_kindle': 'Amazon Comics',
        'apple_comics':  'Apple Books Comics',
        'libby_comics':  'Libby Comics',
    }
    for src_slug, chart_prefix in _COMIC_PANEL_META.items():
        panel = (comics_snap.get('sources') or {}).get(src_slug) or {}
        for i, it in enumerate((panel.get('items') or [])[:60]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            if not title:
                continue
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'comic',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
                'libby_holds':   0,
            })
            e['chart_labels'].append(f'{chart_prefix} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
            # Libby comics carry a native `holds` count (LA County
            # only). Preserve it so the prompt can cite the raw
            # signal and project it up to US-wide library borrows.
            if src_slug == 'libby_comics':
                h = int(it.get('holds') or 0)
                if h > e['libby_holds']:
                    e['libby_holds'] = h

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


# Bumped 2026-08-31 (Jenna: "everything should have a value in US Audience
# except films"). Cap now sized to accommodate the union of compute_view's
# rendered rows (Search tab ~300 + per-category ~350 + Movers ~100 = ~750
# unique) AND the wider google_wide snapshot's ~800 tail so both sources
# fit comfortably. Collectors seed compute_view rows FIRST so the actually-
# rendered items always land in the cap even on days the snapshot outsizes.
_MAX_SEARCH_TERM_ITEMS     = 1200
_MAX_TRENDING_PERSON_ITEMS = 120
_MAX_WIKI_TOPIC_ITEMS      = 120


def _score_to_interest_100(score: int) -> int:
    """The `google_wide` snapshot's `score` field is an internal
    proxy (weighted mention count, can run into the hundreds of
    thousands). The dashboard chip is anchored to Google Trends'
    canonical 0-100 interest score. Compress the score into that
    band via a log-tapered mapping so low-signal queries stay near
    the bottom of the band and top-of-day breakouts hit 90-100."""
    try:
        s = int(score or 0)
    except (TypeError, ValueError):
        return 0
    if s <= 0:
        return 0
    if s >= 200_000:
        return 100
    if s >= 100_000:
        return 90
    if s >= 40_000:
        return 75
    if s >= 15_000:
        return 60
    if s >= 5_000:
        return 45
    if s >= 1_500:
        return 30
    if s >= 500:
        return 20
    if s >= 100:
        return 10
    return 5


# ─────────────────────────────────────────────────────────────────────────
# compute_view augmenter (Search / People / Wiki)
# ─────────────────────────────────────────────────────────────────────────
# The Search / People / Wiki cards in trends_iq render a LIVE list of
# entities dynamically mined from headlines + searches + social every
# time compute_view runs. Those lists diverge from the daily
# `gdelt-people.json` / `wikipedia_trending.json` / `google_wide.json`
# snapshots the daily scrapers write, so a collector that only reads
# snapshots will price entities the dashboard never surfaces (and miss
# the ones it does).
#
# The lazy augmenter below imports trends_iq.compute_view on demand and
# extracts the currently-rendered entities so every rendered row can
# actually be priced. The import is intentionally deferred to the first
# call so unrelated scraper runs (song / podcast / book / etc.) don't
# pay the compute_view warm-up cost.
_COMPUTE_VIEW_CACHE: Optional[dict] = None


def _compute_view_cards() -> dict:
    """Return `cards` from a compute_view() call, cached for the
    duration of this process. Prefers the warm S3 cache (matches what
    the dashboard is currently rendering); falls back to a fresh
    build if that returns empty. Best-effort: returns {} on total
    failure."""
    global _COMPUTE_VIEW_CACHE
    if _COMPUTE_VIEW_CACHE is not None:
        return _COMPUTE_VIEW_CACHE
    try:
        import sys as _sys
        _here = os.path.dirname(os.path.abspath(__file__))
        _webapp = os.path.abspath(os.path.join(_here, '..', '..'))
        if _webapp not in _sys.path:
            _sys.path.insert(0, _webapp)
        from trends_iq import compute_view  # type: ignore
        # Warm-cache read first (0.1s S3 read, matches dashboard state).
        try:
            payload = compute_view({}, force_refresh=False) or {}
        except Exception as e_warm:
            logger.warning("compute_view warm-cache read failed (%s), "
                            "retrying with force_refresh=True", e_warm)
            payload = {}
        cards = payload.get('cards') or {}
        # If cache was cold or shape looked empty, try a full rebuild.
        if not cards or not (cards.get('trending_searches')
                              or cards.get('trending_people')):
            try:
                payload = compute_view({}, force_refresh=True) or {}
                cards = payload.get('cards') or {}
            except Exception as e_fresh:
                logger.warning("compute_view force_refresh failed (%s); "
                                "using whatever the warm read returned",
                                e_fresh)
        _COMPUTE_VIEW_CACHE = cards or {}
        logger.info("compute_view augmenter: cached %d card keys "
                     "(trending_searches=%d, trending_people=%d, wiki=%d)",
                     len(_COMPUTE_VIEW_CACHE),
                     len(_COMPUTE_VIEW_CACHE.get('trending_searches') or []),
                     len(_COMPUTE_VIEW_CACHE.get('trending_people') or []),
                     len(_COMPUTE_VIEW_CACHE.get('wikipedia_trending') or []))
    except Exception as e:
        logger.warning("compute_view augmenter: import/call failed (%s), "
                        "falling back to snapshot-only collection", e)
        _COMPUTE_VIEW_CACHE = {}
    return _COMPUTE_VIEW_CACHE


def _collect_search_terms(max_items: int = _MAX_SEARCH_TERM_ITEMS) -> list[dict]:
    """Union trending search queries from what compute_view is
    currently rendering (Search tab + per-category buckets + Movers)
    PLUS the wider `google_wide` snapshot tail. compute_view rows go
    FIRST so the dashboard-visible items always land inside the cap
    even on days the snapshot outsizes; snapshot rows backfill the
    long tail so tomorrow's compute_view rebuild still has coverage
    for terms that trended overnight without a fresh scraper run."""
    per: dict[str, dict] = {}
    next_rank = 1

    def _add(term: str, source_label: str, row: dict, category: str = '') -> None:
        nonlocal next_rank
        t = (term or '').strip()
        if not t:
            return
        key = _cp_normalize(t)
        if not key or key in per:
            return
        per[key] = {
            'kind':               'search_term',
            'display_title':      t,
            'artist':             '',
            'best_rank':          next_rank,
            'chart_labels':       [source_label],
            'interest_score':     _score_to_interest_100(
                row.get('score_today') or row.get('score')),
            'volume_growth_pct':  int(row.get('volume_growth_pct') or 0),
            'category':           category or row.get('category') or '',
            'related':            list(row.get('related') or [])[:5],
        }
        next_rank += 1

    # 1) compute_view live pass FIRST - every row here is currently
    #    rendered on the dashboard. Pricing these guarantees the chip
    #    shows for what the user sees.
    cards = _compute_view_cards()
    if cards:
        for row in (cards.get('trending_searches') or []):
            _add(row.get('term') or row.get('title') or '',
                  'Google Trends (US)', row)
        for _bucket, bucket_rows in (cards.get('trending_searches_by_category') or {}).items():
            for row in (bucket_rows or []):
                _add(row.get('term') or row.get('title') or '',
                      'Google Trends (US)', row, category=_bucket)
        movers = cards.get('movers') or {}
        for _bkey in ('breakout', 'rising', 'falling', 'sustained'):
            for row in (movers.get(_bkey) or []):
                _add(row.get('term') or row.get('title') or '',
                      'Google Trends Movers (US)', row, category=_bkey)

    # 2) Snapshot backfill - fills the tail with google_wide items so
    #    tomorrow's compute_view still has ready-to-serve estimates
    #    for terms that trended overnight without a fresh scraper run.
    snap = _read_snapshot('google_wide')
    if snap:
        for r in (snap.get('national') or []):
            _add(r.get('term') or '', 'Google Trends (US)', r)

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_trending_people(max_items: int = _MAX_TRENDING_PERSON_ITEMS) -> list[dict]:
    """Union trending people from compute_view's live `trending_people`
    list (what the dashboard is rendering RIGHT NOW) PLUS today's
    `gdelt-people` snapshot as tail backfill. trends_iq mints
    trending_people dynamically at request time from headlines +
    searches + wiki + articles so its list can diverge from the daily
    snapshot; compute_view rows go FIRST so the visible chip coverage
    stays aligned with what the user sees."""
    per: dict[str, dict] = {}
    next_rank = 1

    def _add(name: str, row: dict) -> None:
        nonlocal next_rank
        n = (name or '').strip()
        if not n:
            return
        key = _cp_normalize(n)
        if not key or key in per:
            return
        # `pageviews` is a 7-day total on the snapshot rows; some
        # compute_view rows store a `views_today` (daily) instead.
        pv_total = int(row.get('pageviews') or 0)
        wiki_daily = (int(row.get('views_today'))
                       if row.get('views_today') is not None
                       else (int(pv_total / 7) if pv_total else 0))
        per[key] = {
            'kind':                 'trending_person',
            'display_title':        n,
            'artist':               '',
            'best_rank':            next_rank,
            'chart_labels':         ['Trending People (US)'],
            'wiki_daily_pageviews': wiki_daily,
            'news_mentions':        int(row.get('mentions') or 0),
        }
        next_rank += 1

    # 1) compute_view live pass FIRST.
    cards = _compute_view_cards()
    if cards:
        for row in (cards.get('trending_people') or []):
            _add(row.get('name') or '', row)

    # 2) gdelt-people snapshot backfill (last 8 days).
    people_rows: list[dict] = []
    for days_back in range(0, 8):
        d = date.today() - timedelta(days=days_back)
        key = f'{_S3_DATED.format(date=d.isoformat())}gdelt-people.json'
        try:
            obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
            data = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
        if isinstance(data, dict):
            rows = data.get('national') or []
        else:
            rows = data or []
        if rows:
            people_rows = rows
            break
    for r in people_rows:
        _add(r.get('name') or '', r)

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_wiki_topics(max_items: int = _MAX_WIKI_TOPIC_ITEMS) -> list[dict]:
    """Union compute_view's live Wikipedia rail PLUS the snapshot
    tail. compute_view rows first so the visible chip coverage stays
    aligned; snapshot backfills the tail."""
    per: dict[str, dict] = {}
    next_rank = 1

    def _add(title: str, row: dict) -> None:
        nonlocal next_rank
        t = (title or '').strip()
        if not t:
            return
        key = _cp_normalize(t)
        if not key or key in per:
            return
        desc = (row.get('description') or row.get('extract') or '').strip()
        per[key] = {
            'kind':                 'wiki_topic',
            'display_title':        t,
            'artist':               desc,
            'best_rank':            next_rank,
            'chart_labels':         ['Wikipedia Trending (US)'],
            'wiki_daily_pageviews': int(row.get('views_today')
                                         or row.get('pageviews') or 0),
            'wiki_description':     desc,
        }
        next_rank += 1

    cards = _compute_view_cards()
    if cards:
        for row in (cards.get('wikipedia_trending') or []):
            _add(row.get('title') or '', row)

    snap = _read_snapshot('wikipedia_trending')
    if snap:
        for r in (snap.get('national') or []):
            _add(r.get('title') or '', r)

    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _lookup_key(kind: str, display_title: str, artist: str = '') -> str:
    """Storage key for an item. Podcasts/streaming key by title;
    songs key by (title + artist) because titles collide across
    artists."""
    if kind == 'song':
        return f'song:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'book':
        return f'book:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'goodreads_book':
        # Distinct kind so a title that ALSO charts on Amazon / Apple /
        # Audible / Libby (as most Goodreads bestsellers do) doesn't
        # collide with those platforms' anchor tiers. `_annotate_
        # goodreads_with_streams` uses the same key shape on the
        # trends_iq side.
        return f'goodreads_book:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'wattpad_story':
        # Wattpad stories collide by title alone (multiple authors can
        # use the same title, and fanfic titles overlap heavily across
        # authors). Key by normalized title+author so 'The Dating
        # Deal' by author A and 'The Dating Deal' by author B don't
        # cross-contaminate estimates.
        return f'wattpad_story:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'comic':
        return f'comic:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'game':
        # Games don't collide by title (no two AAA releases share a
        # name in the same window). Publisher rides along on `artist`
        # for prompt context but isn't part of the key.
        return f'game:{_cp_normalize(display_title)}'
    if kind == 'fast_channel':
        # Platform-scoped: 'Mythical 24/7' on Pluto vs Roku vs Tubi
        # are three separate rows with three separate weekly view
        # numbers. `artist` carries the platform slug (roku/tubi/
        # pluto/amazon) - both sides (collector + trends_iq
        # annotator) pass it in the same slot.
        plat = (artist or '').strip().lower()
        return f'fast_channel:{plat}:{_cp_normalize(display_title)}'
    if kind in ('film', 'tv', 'title'):
        return f'{kind}:{_cp_normalize(display_title)}'
    if kind == 'search_term':
        # Search queries collide by title only in rare cases (e.g.
        # multiple sources ranking the same query); key by normalized
        # query text so cross-source rows dedupe.
        return f'search_term:{_cp_normalize(display_title)}'
    if kind == 'trending_person':
        # Person names collide by title only in rare cases (e.g.
        # actor + politician named "Chris Cooper"). Keying by
        # normalized full name is fine for the currently-trending
        # window: at most one Chris Cooper is trending in a week.
        return f'trending_person:{_cp_normalize(display_title)}'
    if kind == 'wiki_topic':
        # Wikipedia entry titles are already unique globally, so a
        # normalized entry title is a safe key.
        return f'wiki_topic:{_cp_normalize(display_title)}'
    return f'{kind}:{_cp_normalize(display_title)}'


# -------------------------------------------------------------------------
# Claude Sonnet + web_search per-item research
# -------------------------------------------------------------------------

_PROMPT_HEADER = (
    "You are a senior media analyst estimating the CURRENT weekly US "
    "audience size for a piece of content, BROKEN OUT PER PLATFORM. "
    "Use the web_search tool AT LEAST ONCE (up to 3 times) to find the "
    "freshest data before you reason.\n"
    "\n"
    "GUIDING PRINCIPLE - CONSERVATIVE AND DEFENSIBLE:\n"
    "  Every number you return must be one you could defend in a room "
    "with an ad-agency data-lead who asks 'where did that come from?'. "
    "When in doubt, choose the LOWER defensible estimate. It is much "
    "worse to overstate reach than to understate it. Prefer numbers "
    "you can trace to a specific published source over numbers you "
    "extrapolated. If you cannot find a defensible anchor for a "
    "platform, return 0 for that platform - do NOT guess.\n"
    "\n"
    "US GEN POP CALIBRATION (HARD RULE):\n"
    "  Every number you return is a WEEKLY US COUNT. It must be "
    "commensurate with the fraction of the ~332M US population "
    "(~260M adults 18+, ~130M households) that actually engages "
    "with this platform in a typical week. A #1 song on Spotify "
    "cannot outreach Spotify's total weekly US actives; a #1 TV "
    "series on Netflix cannot outreach Netflix's total weekly US "
    "viewer households. Use these platform-wide US weekly-active "
    "pools as CEILINGS on how many people COULD engage with this "
    "item at all in the window:\n"
    "     Music streaming (weekly US MAU): Spotify ~110M, Apple "
    "Music ~55M, YouTube Music (+free) ~95M, Amazon Music ~65M. "
    "A single track's weekly US streams is a SUBSET of the "
    "platform's MAU (a MAU streams many tracks). Sanity: even a "
    "mega-hit rarely engages more than 10-15% of the platform's "
    "weekly MAU in a given week.\n"
    "     Podcasts (weekly US listener pool): total US weekly "
    "podcast listeners ~130M (Edison Podcast Consumer 2026). Apple "
    "Podcasts ~60M weekly US actives; Spotify Podcasts ~45M; "
    "Amazon Music Podcasts ~15M; Audible ~10M. A #1 podcast's "
    "weekly US listeners is a SUBSET of the platform's weekly "
    "actives.\n"
    "     Streaming video (weekly US viewer households/week): "
    "Netflix ~65-70M HH, Disney+ ~48M, Hulu ~46M, HBO Max ~50M, "
    "Prime Video ~90-100M (Prime members). A single title's "
    "weekly US HH cannot exceed the service's weekly reach.\n"
    "     Books: total weekly US book buyers ~4-6M (Circana); US "
    "Audible subs ~10M; US weekly public-library digital borrowers "
    "~4-6M (OverDrive). A single title's weekly US buyers/"
    "borrowers must sit INSIDE those aggregate weekly pools.\n"
    "  If your per-platform estimate exceeds a plausible fraction "
    "(e.g. >10% of the platform's weekly US MAU on a single item), "
    "reduce it. It is a red flag - the pool cannot bear that much "
    "concentration on one title.\n"
    "\n"
    "RANKING OF SOURCES (prefer higher-tier when available):\n"
    "  Tier 1: Nielsen streaming top-10 US, Luminate week-over-week US "
    "(Billboard reports Wednesday), Edison Podcast Metrics US, Podtrac "
    "US Top 20 Ranker, MRC Podcast Ratings US, Chartmetric US streams, "
    "official platform press releases with a real US number.\n"
    "  Tier 2: Variety, Deadline, Hollywood Reporter, The Verge, "
    "Billboard Chart Beat, publisher press releases with real numbers.\n"
    "  Tier 3: Third-party aggregators (Whip Media, Samba TV, Parrot "
    "Analytics), Chartmetric per-DSP estimates, Spotify for Artists "
    "screenshots.\n"
    "  AVOID: SEO listicles, YouTube reaction videos, unattributed blogs.\n"
    "\n"
    "CHART LABELS ARE REAL-TIME TIER-1 SIGNAL:\n"
    "  The chart labels supplied for each item are CURRENT trending-rail "
    "positions this week on those specific platforms (scraped today "
    "from the platform's own charts / editorial rails). That means: if "
    "the item is labeled 'Netflix #3' or 'Spotify Daily Top 200 (US) #7', "
    "you can trust that the item is IN TIER for that platform this "
    "week. Chart position alone is a defensible Tier-1 anchor: apply "
    "the per-platform anchor tier corresponding to the rank and return "
    "a non-zero estimate. Only return 0 for a platform if the item is "
    "NOT in that platform's chart labels AND you have no other data.\n"
    "\n"
    "DIFFERENTIATE WITHIN A TIER (HARD RULE):\n"
    "  When two items share the same chart tier (both are 'top-100 "
    "Libby ebooks' or both are 'steady-state Netflix top-10'), you "
    "must still return DIFFERENT numbers for them. Anchor the tier, "
    "then differentiate WITHIN the anchor range using: exact chart "
    "rank, release recency (newer = higher recency demand), publisher "
    "/ label size, prior-week momentum, mentions in press coverage, "
    "genre popularity, and any specific data you find in web_search. "
    "Never return the same integer for two different titles just "
    "because they both sit in the same anchor band. If you find "
    "yourself typing an obvious round number (12000, 5000, 25000), "
    "adjust to a defensible non-round value that reflects that "
    "specific item's characteristics (e.g. 11,400 for rank 87 vs. "
    "13,200 for rank 62 of the same tier).\n"
    "\n"
    "REASONING RULES:\n"
    "  1. For each platform in TARGET_PLATFORMS below:\n"
    "     - If the item has a chart label on this platform: use the "
    "per-platform anchors + rank to place it in-tier, biased LOW. "
    "Always return a non-zero estimate in this case.\n"
    "     - If the item has NO chart label on this platform: return "
    "0 unless you find Tier-1/Tier-2 press specifically citing that "
    "platform's US weekly reach for this item.\n"
    "  2. If only a global number exists, apply a US share benchmark "
    "(US = 35-45% of Spotify global streams, 30-40% of Apple Music "
    "global, 20-30% of YouTube Music global for English-language "
    "songs, 35-45% of Netflix global views for English-language "
    "titles). Bias to the low end. State the share used.\n"
    "  3. Chart-position sizing (bias LOW, not to the middle): "
    "#1 = anchor high-third; #2-5 = anchor middle-third; #6-20 = "
    "anchor low-third; #21+ = below the anchor's low.\n"
    "  4. Return a RANGE (low, mid, high) that reflects real "
    "uncertainty. Low = worst-case defensible, High = best-case "
    "defensible. Mid = your best-guess conservative number (closer to "
    "low than to high in ambiguous cases).\n"
    "  5. `confidence` tag per platform: 'high' if you cited a Tier-1 "
    "US number this week; 'medium' if extrapolated from Tier-1 chart "
    "rank OR Tier-2 press; 'low' if inferred from Tier-3 or bare chart "
    "position without a fresh press cite. Aggregate confidence = min "
    "of per-platform confidences you actually reported.\n"
    "  6. NEVER exceed the per-platform sanity ceilings listed in "
    "TARGET_PLATFORMS. Ceilings represent the historical US peak for "
    "the platform's #1 slot - your item cannot outrank the peak.\n"
    "\n"
    "OUTPUT FORMAT: Return ONLY a JSON object with these exact keys, "
    "no prose, no markdown fence:\n"
    "  {\n"
    "    \"by_platform\": {\n"
    "      \"<platform_key>\": {\n"
    "        \"us_estimate\":       <int, US weekly on THIS platform>,\n"
    "        \"us_estimate_low\":   <int, defensible low>,\n"
    "        \"us_estimate_high\":  <int, defensible high>,\n"
    "        \"confidence\":        \"high\" | \"medium\" | \"low\",\n"
    "        \"note\":              <string, 1 short sentence: what "
    "you found or how you inferred it. If you couldn't find data, say "
    "so explicitly.>\n"
    "      },\n"
    "      ...\n"
    "    },\n"
    "    \"us_estimate\":       <int, all-platforms US total (sum of "
    "per-platform mids, or a defensible aggregate)>,\n"
    "    \"us_estimate_low\":   <int, defensible aggregate low>,\n"
    "    \"us_estimate_high\":  <int, defensible aggregate high>,\n"
    "    \"unit_label\":        <string, e.g. \"weekly US streams\">,\n"
    "    \"confidence\":        \"high\" | \"medium\" | \"low\",\n"
    "    \"method\":            <string, 2-4 sentences: what you found "
    "for each platform, how you handled gaps, how conservative your "
    "final number is>,\n"
    "    \"sources\":           [<url1>, <url2>, ...]   // 1-4 URLs actually consulted\n"
    "  }\n"
)


# ------------------------------------------------------------------
# Per-platform benchmarks passed into the prompt.
#
# `key`      -> the JSON key we expect back from Claude (must match
#               the panel slug in music_charts / podcast_charts /
#               streaming so the annotate side can look them up)
# `label`    -> human-readable label in the prompt
# `ceiling`  -> conservative US-weekly hard cap for the platform's
#               #1 slot. Used both in the prompt as a boundary and
#               post-parse to clamp hallucinations.
# `anchors`  -> paragraph text summarising real-world reference
#               points for that platform's audience tiers.
#
# Ceilings are anchored to public benchmarks (Luminate US, Nielsen
# streaming top-10, Podtrac Ranker, Edison Q2 2026, official platform
# press releases) and biased conservative - deliberately below the
# aggressive-case high so a #1 hit doesn't get inflated. When in
# doubt these read low.
# ------------------------------------------------------------------
_SONG_PLATFORMS = [
    {'key': 'spotify',
     'label': 'Spotify',
     'ceiling': 25_000_000,
     'anchors': (
         'Luminate US weekly on-demand audio: Spotify #1 US typically '
         '8-14M weekly US streams; top-10 5-9M; top-50 2-4M; top-200 '
         '0.6-1.5M. Spotify is ~55-60% of US on-demand audio streams.'
     )},
    {'key': 'apple',
     'label': 'Apple Music',
     'ceiling': 8_000_000,
     'anchors': (
         'Apple Music US = ~15-20% of on-demand audio. #1 Apple Music '
         'US typically 2-5M weekly; top-10 1-3M; top-100 0.2-0.6M. '
         'Rarely exceeds 5M weekly except for a Drake / Taylor / '
         'Kendrick blockbuster week.'
     )},
    {'key': 'youtube',
     'label': 'YouTube Music',
     'ceiling': 12_000_000,
     'anchors': (
         'YouTube Music US = ~10-15% of on-demand audio, but YouTube '
         'video views (which YT Music aggregates) push totals higher. '
         'Big #1 song: 2-6M weekly YT Music US audio streams; music '
         'video views add another 3-8M weekly US. Combined ceiling '
         '~12M weekly US on a mega-hit week.'
     )},
    {'key': 'amazon',
     'label': 'Amazon Music',
     'ceiling': 5_000_000,
     'anchors': (
         'Amazon Music US = ~10-13% of on-demand audio. #1 Amazon '
         'Music US typically 1-3M weekly; top-10 0.5-1.5M; top-100 '
         '<0.3M. Amazon does not publish per-track US numbers so '
         'estimates rely on share benchmarks from Chartmetric / MIDiA.'
     )},
]

_PODCAST_PLATFORMS = [
    {'key': 'apple',
     'label': 'Apple Podcasts',
     'ceiling': 8_000_000,
     'anchors': (
         'Podtrac Ranker US weekly downloads (Apple + web): #1 '
         'typically 5-8M weekly US listeners (Rogan / Daily / Crime '
         'Junkie tier); top-10 2-4M; top-50 0.5-1.5M. Apple Podcasts '
         'itself is ~45-55% of total US podcast listenership.'
     )},
    {'key': 'spotify',
     'label': 'Spotify Podcasts',
     'ceiling': 8_000_000,
     'anchors': (
         'Spotify Podcasts US ~25-35% share. Rogan alone (Spotify-'
         'exclusive era) was 5-8M weekly US on Spotify. Non-exclusive '
         '#1: 2-5M weekly US on Spotify; top-10 1-2.5M; top-50 <1M.'
     )},
    {'key': 'youtube_podcasts',
     'label': 'YouTube Podcasts',
     'ceiling': 7_000_000,
     'anchors': (
         'YouTube is now the #1 US podcast platform by weekly reach '
         "per Edison Research's Infinite Dial 2025 - ~31% of US "
         'monthly podcast listeners use YouTube as their primary '
         'listening surface, ~50-80M individuals reached weekly with '
         'video-podcast content on the platform. Top video-podcast '
         'shows on YouTube (Joe Rogan Experience YouTube channel, '
         'Kill Tony, This Past Weekend w/ Theo Von, Shawn Ryan Show, '
         'Rotten Mango, MeidasTouch) reach 4-7M weekly US individuals '
         'each; tier-two shows (Diary of a CEO, Financial Audit, '
         'Good Mythical Morning) 500K-2M; long-tail YouTube podcast '
         'shows 10K-100K. Anchor per-show numbers off the channel '
         'subscriber base and reported weekly video views on the '
         'canonical show channel; a channel with N subscribers and M '
         'weekly video views on new podcast uploads sees roughly '
         '~0.15-0.30 x M unique US viewers per week. Bias to the '
         'middle of these tiers unless a specific press cite or '
         'Podnews / Podtrac YouTube-inclusive ranker exists for the '
         'exact show.'
     )},
    {'key': 'netflix',
     'label': 'Netflix Video Podcasts',
     'ceiling': 3_000_000,
     'anchors': (
         'Netflix video podcasts are a new format (2026). Netflix does '
         'not publish per-podcast reach. Estimate from Nielsen Tudum '
         'video views (video podcast episodes are counted as short-'
         'form watches): #1 ~ 1-3M weekly US views; long tail <0.5M. '
         'Prefer 0 unless a specific press release exists.'
     )},
    {'key': 'amazon',
     'label': 'Amazon Music Podcasts',
     'ceiling': 2_000_000,
     'anchors': (
         'Amazon Music US podcast share <10%. #1 podcast on Amazon '
         'Music: 0.3-1M weekly US; top-10 <0.5M. Amazon does not '
         'publish per-podcast numbers; bias LOW.'
     )},
    {'key': 'audible',
     'label': 'Audible Podcasts',
     'ceiling': 1_500_000,
     'anchors': (
         'Audible podcast tier is small (Audible is primarily audio-'
         'book). Audible Originals top podcasts: 0.1-0.5M weekly US '
         'downloads. Bias LOW - if no press release exists, return '
         '0 or minimal.'
     )},
]

_BOOK_PLATFORMS = [
    {'key': 'amazon',
     'label': 'Amazon Best-Sellers (Kindle + Print)',
     'ceiling': 500_000,
     'anchors': (
         "NPD BookScan / Circana US weekly print+ebook units. Top-10 "
         "trade book typically 15-50K weekly US buyers; #1 in a "
         "release week 100-300K (rare political memoir / celebrity "
         "release). Amazon is ~55-65% of US ebook sales and ~40-50% "
         "of print. Bias LOW: prefer the tier's low anchor unless a "
         "publisher/Circana press cite backs a higher number for the "
         "specific week. Steady-state top-10 = 8-25K weekly US buyers."
     )},
    {'key': 'apple',
     'label': 'Apple Books Top 100 (Paid US)',
     'ceiling': 60_000,
     'anchors': (
         "Apple Books is ~8-12% of US ebook market. Top-10 Apple Books "
         "US typically 1-4K weekly US buyers; #1 3-10K. Rarely exceeds "
         "10K weekly except for a mega-launch week. If no press data "
         "exists, use chart-position * Apple's share of US ebook (~10%) "
         "of the Amazon anchor - and bias LOW."
     )},
    {'key': 'audible',
     'label': 'Audible Best-Sellers (Audiobook)',
     'ceiling': 80_000,
     'anchors': (
         "Audible has ~10M US members. Top audiobook titles do 5-15K "
         "weekly US listens/purchases; #1 20-50K in a big release week. "
         "Audible dominates US audiobook (~55-65% share). Steady-state "
         "top-10 = 3-10K weekly US listeners. Bias LOW."
     )},
    {'key': 'libby_ebook',
     'label': 'Libby Popular eBooks (US public-library projection)',
     'ceiling': 60_000,
     'anchors': (
         "OverDrive/Libby powers ~90% of US public library digital "
         "circulation. Total US public library digital circulation = "
         "~600M annual loans (OverDrive 2024-2025 press releases) = "
         "~11.5M weekly. Top ~5K digital library titles get most "
         "loans; #1 title ~15-40K US weekly library borrows; top-10 "
         "5-15K; top-100 1-4K. THE RAW SIGNAL IS LA COUNTY LIBRARY "
         "HOLDS - LA County ~10M residents = ~3-4% of US library-"
         "served population. If a book has N holds at LA County, "
         "the US weekly borrow rate is roughly N * 25-35x, but "
         "cross-reference against OverDrive's National Digital Book "
         "Awards / weekly bestsellers press when available. Bias LOW."
     )},
    {'key': 'libby_audio',
     'label': 'Libby Popular Audiobooks (US public-library projection)',
     'ceiling': 40_000,
     'anchors': (
         "US public library audiobook digital circulation = ~140M "
         "annual loans (OverDrive 2025) = ~2.7M weekly. Top #1 "
         "audiobook ~8-25K US weekly library borrows; top-10 2-8K; "
         "top-100 <1K. RAW SIGNAL IS LA COUNTY LIBRARY HOLDS - "
         "project up ~25-35x (LA County share of US library "
         "audiobook demand), cross-check against OverDrive/AudioFile "
         "quarterly reports when available. Bias LOW."
     )},
    {'key': 'libby_magazine',
     'label': 'Libby Popular Magazines (US public-library projection)',
     'ceiling': 30_000,
     'anchors': (
         "US public library digital magazine circulation = ~40-60M "
         "annual issue-downloads (OverDrive Magazines / Flipster). "
         "Weekly = ~800K-1.15M US library magazine reads across all "
         "titles. Top #1 magazine ~5-15K US weekly library reads; "
         "top-10 1.5-5K; top-100 <500. RAW SIGNAL IS LA COUNTY "
         "LIBRARY HOLDS - project up ~25-35x. Note: magazine "
         "'holds' behave differently from books (issues auto-renew "
         "and are often unlimited-simultaneous-use), so LA holds "
         "may under-represent actual reader demand. Cross-reference "
         "with any OverDrive Magazines press or the specific "
         "publisher's audited circulation (MPA / AAM) when a "
         "magazine title matches a known print/digital brand. Bias LOW."
     )},
]


# Comics / graphic novels / manga. Distinct kind='comic' rather than
# folding into 'book' because the addressable US audience is much
# smaller (comics ~30-40M annual US readers vs ~180M annual US
# book buyers), the price / unit conventions differ (single issues
# vs full novels), and the anchor tiers per source diverge sharply:
# Amazon Comics ships PHYSICAL bestsellers; Apple ships DIGITAL
# per-issue paid catalog; Libby ships public-library digital
# borrows. Same three-panel layout the Books tab uses.
_COMICS_PLATFORMS = [
    {'key': 'amazon_kindle',
     'label': 'Amazon Comics & Graphic Novels (physical bestsellers)',
     'ceiling': 50_000,
     'anchors': (
         "Amazon's Best Sellers list for Comics & Graphic Novels is "
         "the physical print + trade-paperback bestseller list (not "
         "the Kindle-only chart, which doesn't server-render). Total "
         "US comics + graphic-novel readership ~30-40M adults "
         "annually (Comichron / ICv2 / NPD BookScan). Weekly US "
         "buyers of a single title concentrate hard on the top tier: "
         "a #1 Best-Seller in a big release week (Fourth Wing GN, "
         "Absolute Batman #1) 8-25K weekly US buyers; steady-state "
         "top-10 = 2-6K weekly US buyers; top-30 = 500-1.5K; long-"
         "tail (rank 30-60) = 100-500. Anchor: Circana BookScan "
         "graphic-novel weekly units + Diamond Comic Distributors "
         "monthly market share reports. Amazon is ~35-45% of US "
         "graphic-novel unit sales. Bias LOW - a title without a "
         "specific press cite defaults to the LOW anchor for its "
         "chart tier."
     )},
    {'key': 'apple_comics',
     'label': 'Apple Books Comics & Graphic Novels (digital paid)',
     'ceiling': 15_000,
     'anchors': (
         "Apple Books Comics genre = digital single-issue + digital "
         "graphic-novel / manga bestseller list. US digital comics "
         "market ~$200-260M/yr (Comichron 2024); ComiXology (Amazon) "
         "captures ~60-70%, Apple Books ~10-15%, Google Play + "
         "publisher direct the remainder. Top-10 Apple Books Comics "
         "US typically 500-2K weekly US buyers per title; #1 "
         "1.5-4K in a launch week (new Berserk volume, Chainsaw Man "
         "chapter drop, Attack on Titan compendium); top-30 "
         "150-500; long-tail <200. Manga-heavy: Berserk / Chainsaw "
         "Man / Attack on Titan / Jujutsu Kaisen dominate the list, "
         "and manga readers concentrate on Apple Books more than on "
         "Kindle. Anchor: Circana BookScan digital graphic-novel + "
         "any Apple Books press disclosure. Bias LOW."
     )},
    {'key': 'libby_comics',
     'label': 'Libby Comics (US public-library projection)',
     'ceiling': 20_000,
     'anchors': (
         "OverDrive/Libby comics borrows are a subset of digital "
         "public-library circulation. Graphic novels are ~15-20% of "
         "OverDrive's total US juvenile + YA digital lending; "
         "comics-specific US annual library borrows ~90-120M "
         "(OverDrive 2024-2025) = ~1.7-2.3M weekly US library "
         "comics borrows. #1 comics title on OverDrive ~4-12K US "
         "weekly library borrows; top-10 1-4K; top-30 300-1K; "
         "long-tail <300. RAW SIGNAL IS LA COUNTY LIBRARY HOLDS "
         "(same as Libby ebooks/audio) - project up ~25-35x for LA "
         "County's share of US library-served population, cross-"
         "reference against OverDrive's Big Library Read + American "
         "Libraries digital-loan press. Bias LOW: many comics titles "
         "carry small hold counts (single-digit) because comics "
         "borrows are dominated by walk-ins vs holds queues."
     )},
]


# ---------------------------------------------------------------------------
# Wattpad. Distinct kind='wattpad_story' rather than folding into
# 'book' because Wattpad is a fundamentally different platform:
# serialized, user-generated fiction with cumulative all-time reads
# (not weekly units), a ~90M global MAU / ~15-20M US weekly reader
# base, and a much longer per-story reach curve than a published
# book. The chart signal is native `reads` + `votes` + Wattpad
# Originals flag; the estimator's job is to convert cumulative reads
# to weekly US readers using platform-level anchors.
_WATTPAD_PLATFORMS = [
    {'key': 'wattpad',
     'label': 'Wattpad (serialized fiction)',
     'ceiling': 20_000_000,
     'anchors': (
         "Wattpad reports ~90M global monthly active users (public "
         "2024). US share of MAU: ~40-50% (Wattpad Studios public "
         "coverage; skews higher on Originals rail, ~55%). Weekly "
         "US readers on the platform overall: ~15-20M peak. "
         "Per-story weekly US readers estimation: cumulative "
         "all-time READS (shown as `wattpad_reads_cumulative` in "
         "CHART CONTEXT) is the strongest single anchor; convert to "
         "weekly by taking a decay-adjusted fraction of the cumulative "
         "total * US share. Practical brackets: top-of-Hot mega-hits "
         "(cumulative reads >100M, votes >1M) land ~200K-800K weekly "
         "US readers on the current trending cohort; steady-state "
         "top-10 (cum reads 5-50M) ~15K-150K weekly US readers; long "
         "tail newly-charting (cum reads <1M) ~200-2000 weekly US "
         "readers. Wattpad Originals rail (paid / studio-invested "
         "serialized fiction, `wattpad_originals=True` in the native "
         "priors) skews HIGHER US share (~0.55 vs ~0.42 platform "
         "default) and higher engagement per reader, so bump the "
         "converted number ~20-30%. `wattpad_is_new_14d=True` = "
         "story first-published in the last 14 days = fresh-cohort "
         "multiplier ~2-5x on the tail-decay math (early reader "
         "velocity peaks in the first 30 days). `wattpad_votes` is "
         "a floor signal: >1M votes over any window guarantees a "
         "mega-hit tier regardless of the reads number. NEVER "
         "return a weekly US number that implies more readers than "
         "the total cumulative reads (a story with 50K cumulative "
         "reads cannot have 60K weekly US readers). Bias LOW when "
         "signals are thin."
     )},
]


# Goodreads community-driven weekly read chart. Distinct kind
# ('goodreads_book') rather than folded into 'book' because Goodreads
# reflects the broader US reading audience (including people who read
# outside Goodreads apps): the metric is unique US readers who read a
# given book this week across ALL surfaces (Kindle, print, audio,
# library, Goodreads-native), projected from the community weekly-
# read signal Goodreads exposes on each tile. Single platform key
# because there is one rail (Most Read This Week).
_GOODREADS_PLATFORMS = [
    {'key': 'goodreads_most_read',
     'label': 'Goodreads Most Read This Week (US)',
     'ceiling': 20_000_000,
     'anchors': (
         "Goodreads reports ~150M global users; US share ~35-40% "
         "(~50-60M US Goodreads users). Weekly US actives on the "
         "platform: ~15-20M readers. RAW ANCHOR: Goodreads's own "
         "`currently_reading_count` surfaced on the tile as 'X "
         "people read it' - counts US Goodreads users who logged "
         "this book as read/reading in the past 7 days and drives "
         "the Most-Read-This-Week ordering itself. Cross-platform "
         "scale: Goodreads-active adults are ~10-20% of all US "
         "book readers (Pew Research 2024 - ~72% of US adults read "
         "at least one book/year, only a subset log activity on "
         "Goodreads), so total US weekly readers for a title = "
         "roughly currently_reading_count * 6-10x depending on "
         "tier: mass-market #1 title (currently_reading >15K) "
         "-> 300K-800K weekly US readers; steady-state top-10 "
         "(currently_reading 5-15K) -> 60K-200K; mid-tier ranks "
         "20-50 (currently_reading 3-6K) -> 20K-70K. Multiplier "
         "BIASES DOWN for genre-fiction and up for literary + "
         "book-club releases (which over-index on Goodreads). The "
         "`is_new_release=True` flag = published in the last "
         "~30 days = fresh-cohort multiplier ~1.3-1.8x on the base "
         "conversion. `avg_rating * ratings_count` is a cumulative-"
         "popularity floor signal: a title with >500K lifetime "
         "ratings can never be at long-tail weekly readers even if "
         "this week's community count is thin (there is a durable "
         "base of returning readers). NEVER return a weekly US "
         "number LESS than `currently_reading_count` itself - that "
         "is the community-observed floor. Ceiling for the panel "
         "is 20M weekly US readers (roughly = US Goodreads weekly "
         "actives). Bias LOW when signals are thin, and NEVER "
         "guess if `currently_reading_count` is 0 - return 0 and "
         "let the next daily fetch pick it up."
     )},
]


_STREAMING_PLATFORMS_META = [
    {'key': 'netflix',
     'label': 'Netflix',
     'ceiling': 30_000_000,
     'anchors': (
         'Nielsen US Streaming Top-10 households/week. Netflix top-2 '
         'title in a big week: 10-20M households (Squid Game 2, '
         'Wednesday). Steady-state top-10: 3-8M households/week. '
         'Netflix Tudum publishes global weekly views; US = ~35-45% '
         'of global views for English-language titles.'
     )},
    {'key': 'disneyplus',
     'label': 'Disney+',
     'ceiling': 15_000_000,
     'anchors': (
         'Nielsen: Disney+ #1 (Mandalorian / Loki / Marvel tentpole) '
         '3-8M US households/week; steady-state top-10 1-3M. Disney '
         'does not disclose per-title numbers - estimates from '
         'Whip Media / Samba TV / Nielsen Top-10.'
     )},
    {'key': 'hulu',
     'label': 'Hulu',
     'ceiling': 15_000_000,
     'anchors': (
         'Nielsen: Hulu #1 (Bear, Only Murders): 3-6M US households/'
         'week. Ad-tier bumps reach but not necessarily views. Long '
         'tail 0.5-2M. Hulu is ~15% of US streaming minutes.'
     )},
    {'key': 'max',
     'label': 'HBO Max',
     'ceiling': 12_000_000,
     'anchors': (
         'Nielsen: Max #1 (House of the Dragon, White Lotus): 3-7M '
         'US households/week. Long tail 0.5-2M. Max/HBO combined = '
         '~8-10% of US streaming minutes.'
     )},
    {'key': 'primevideo',
     'label': 'Prime Video',
     'ceiling': 15_000_000,
     'anchors': (
         'Nielsen: Prime #1 (Reacher, Boys, Rings of Power): 4-8M '
         'US households/week. Ads-tier launch inflated 2024 numbers. '
         'Steady-state 1-3M for top-10.'
     )},
    {'key': 'espnplus',
     'label': 'ESPN+',
     'ceiling': 3_000_000,
     'anchors': (
         'ESPN+ per-title reach is small (sport-specific, event-'
         'driven). Big UFC PPV weekend: 1-2.5M US buyers. Non-event '
         'programming <0.5M weekly US.'
     )},
    {'key': 'britbox',
     'label': 'BritBox',
     'ceiling': 1_500_000,
     'anchors': (
         "BritBox US is BBC + ITV's joint premium subscription "
         'streamer. Antenna / Parrot Analytics US subscriber estimates '
         '~2.5-3.5M as of 2026; that is the ADDRESSABLE ceiling. Weekly '
         'per-title reach is much lower: top flagship series '
         '(Shetland, Father Brown, Death in Paradise, Doctor Who back '
         'catalog) hit 300K-700K US households/week. Steady-state '
         'top-10 typically 100K-350K. Anchor: ITV Q2 2026 investor '
         'update + Antenna monthly SVOD engagement reports. British-'
         'skewed audience: older-female Anglophile fan, extremely '
         'loyal but small absolute base.'
     )},
    {'key': 'mgmplus',
     'label': 'MGM+',
     'ceiling': 2_000_000,
     'anchors': (
         'MGM+ (formerly Epix, rebranded Jan 2023, Amazon-owned since '
         'the MGM acquisition closed 2022). Antenna / Nielsen: US '
         'subscribers ~4.0-4.8M as of 2026 (majority via cable-'
         'bundle carriage, minority direct-to-consumer). Top original '
         'series (FROM, Godfather of Harlem, American Rust) reach '
         '400K-900K US households/week. Big theatrical windows (Mission '
         'Impossible, Gladiator II, Bond back-catalog on MGM+) briefly '
         'spike 1.0-1.8M/week during their exclusive window. Steady-'
         'state top-10 300K-700K. Anchor: Amazon Q2 2026 earnings + '
         'Antenna monthly SVOD reports + Nielsen Streaming Content '
         'Ratings originals list.'
     )},
    {'key': 'starz',
     'label': 'Starz',
     'ceiling': 5_000_000,
     'anchors': (
         "Starz (Lionsgate's premium subscription streamer, US "
         'subscribers ~12M as of 2026 - the largest of the "premium '
         'niche" services after HBO Max). Nielsen Streaming Content '
         'Ratings: flagship originals reach real scale. Power '
         'Universe episodes (Power Book II: Ghost, Raising Kanan, '
         'Force) hit 1.5-3.0M US households/week during a live '
         'season; Outlander mid-season 1.2-2.5M/week; BMF 800K-1.5M. '
         'Big Lionsgate theatrical windows (John Wick, Saw, Now You '
         'See Me back-catalog) briefly spike 1.5-3.5M/week during '
         'their exclusive window. Steady-state top-10 without a '
         'flagship air-window 400K-1.0M. Anchor: Lionsgate Q2 2026 '
         'earnings + Antenna monthly SVOD reports + Nielsen Streaming '
         'Content Ratings originals list. Audience skews female-adult '
         'for Outlander, male-25-54 for Power Universe.'
     )},
]


# FAST (Free Ad-Supported Streaming TV) platform anchors. FAST
# audiences are AD-SUPPORTED and free, so the frame is "who watched
# for free on this platform this week" not "who paid for a
# subscription and watched". Ceilings are per-platform per-title
# weekly. Anchor language points Claude at Nielsen FAST monthly
# reports, TVREV, S&P Global Ampere Analysis, Antenna FAST reports,
# and platform-owned press releases (Fox for Tubi, Paramount for
# Pluto, Roku Inc. earnings for Roku Channel, Amazon Fire TV
# reports for Amazon Live TV).
_FAST_PLATFORMS_META = [
    {'key': 'roku',
     'label': 'Roku Channel',
     'ceiling': 6_000_000,
     'anchors': (
         'Nielsen FAST Gauge: Roku Channel = ~2.5-3.5% of total US TV '
         'usage (top-3 FAST platform). Top titles (Everybody Loves '
         'Raymond, Interview with the Vampire seasons, Roku Originals '
         'like Weird Al biopic) reach 2-5M US households/week. Middle '
         'of top-100 typically 300K-1M weekly. Anchor: Roku Inc. Q2 '
         '2026 earnings + TVREV monthly FAST rankings.'
     )},
    {'key': 'tubi',
     'label': 'Tubi',
     'ceiling': 6_000_000,
     'anchors': (
         'Fox Corp. reports Tubi ~97M MAU (Q1 2026). Nielsen FAST '
         'Gauge: Tubi = ~2.0-2.4% of total US TV usage. Top licensed '
         'catalog (Sons of Anarchy, The Bear reruns, WWE Speed) hits '
         '3-5M weekly viewers. Tubi Originals reach 1-3M. Middle of '
         'top-100 typically 200K-800K. Anchor: Fox Q2 2026 earnings '
         'call + Antenna FAST engagement reports.'
     )},
    {'key': 'pluto',
     'label': 'Pluto TV',
     'ceiling': 5_000_000,
     'anchors': (
         'Paramount Global reports Pluto ~80M MAU globally (~50M US). '
         'Nielsen FAST Gauge: Pluto = ~1.4-1.8% of total US TV usage. '
         'Top titles (CSI reruns, MTV catalog, Star Trek channels) '
         '1.5-3M weekly viewers. The X-Files-tier IP: 2-4M weekly. '
         'Middle of top-100 typically 150K-600K. Anchor: Paramount Q2 '
         '2026 earnings + S&P Global Ampere Analysis FAST reports.'
     )},
    {'key': 'amazon',
     'label': 'Amazon Live TV',
     'ceiling': 4_000_000,
     'anchors': (
         "Amazon's dedicated FAST/live-TV UI (formerly Freevee-branded "
         'channels, absorbed into Prime Video ad-tier navigation in '
         "2024). Nielsen FAST Gauge: Amazon FAST = ~0.6-1.0% of total "
         'US TV usage (smaller than Roku/Tubi/Pluto because most Prime '
         'audience defaults to on-demand). Top FAST-only titles 1-2.5M '
         'weekly viewers. Middle of top-100 typically 100K-400K. '
         'Anchor: Amazon Q2 2026 shareholder letter + TVREV monthly '
         'FAST rankings. IMPORTANT: this is Amazon Live TV / FAST '
         'channels ONLY - do NOT reason from Prime Video paid catalog '
         'numbers (Ted Lasso, Reacher) even if the title has a free '
         'pilot on Prime.'
     )},
]


# FAST-channel platforms. Unlike the FAST-title anchors above
# (which reason about weekly-viewers-per-title on the platform),
# these are anchors for weekly-viewers-per-CHANNEL. A channel is a
# 24/7 linear feed inside the platform (Nick Jr. Pluto TV, Mythical
# 24/7, Forensic Files 24/7, ...). Ceilings are the historical
# weekly-viewers peak for a top-tier channel on that platform per
# Nielsen FAST Gauge / TVREV monthly channel-ranker reports /
# Antenna FAST engagement.
_FAST_CHANNEL_PLATFORMS_META = [
    {'key': 'roku',
     'label': 'Roku Channel',
     'ceiling': 4_000_000,
     'anchors': (
         "Roku Channel channel-level weekly viewership. Roku is a "
         'top-3 FAST platform with ~85M weekly US actives across its '
         'FAST grid. Top channels (Roku Originals hub, ABC News Live, '
         'Fox Weather, Reuters Now, live sports pop-ups) get '
         '1.5-3.5M US weekly viewers. Mid-tier IP channels (Judge '
         'Faith, The Bernie Mac Show 24/7, Personal Injury Court) '
         '200K-700K. Long-tail niche channels (Cougar Town, single-'
         'show reruns) 30-120K weekly. Anchor: Roku Inc. Q2 2026 '
         'earnings + TVREV monthly channel rankings + Antenna FAST '
         'engagement reports.'
     )},
    {'key': 'tubi',
     'label': 'Tubi',
     'ceiling': 4_000_000,
     'anchors': (
         "Tubi channel-level weekly viewership. Fox Corp. Q2 2026: "
         'Tubi ~97M MAU. Top channels (Forensic Files, Cheaters, '
         'Game Show Central, Euronews Live) 1.5-3M US weekly '
         'viewers. Mid-tier (Mythical 24/7, MasterChef 24/7) '
         '250K-800K. Long-tail 40-150K. Tubi has fewer channels '
         '(~170) so each channel gets meaningfully more traffic '
         'than Amazon/Roku equivalents. Anchor: Fox Q2 2026 '
         'earnings + Nielsen FAST Gauge + TVREV Tubi channel '
         'rankings.'
     )},
    {'key': 'pluto',
     'label': 'Pluto TV',
     'ceiling': 3_500_000,
     'anchors': (
         "Pluto TV channel-level weekly viewership. Paramount Q2 "
         '2026: Pluto ~80M MAU global / ~50M US. Top channels '
         '(Nick Jr. Pluto TV, MTV, CBS News, Star Trek, Home '
         'Cooking, PowerNation) 1-2.5M US weekly viewers. Mid-'
         'tier IP channels (Judge Faith 24/7, LEGO Channel, CSI '
         '24/7) 200K-700K. Long-tail niche channels 30-120K weekly. '
         'Anchor: Paramount Q2 2026 earnings + Nielsen FAST Gauge '
         '+ S&P Ampere Analysis + Antenna Pluto channel rankings.'
     )},
    {'key': 'amazon',
     'label': 'Amazon Live TV',
     'ceiling': 2_500_000,
     'anchors': (
         "Amazon Live TV (formerly Freevee-branded FAST channels, "
         'now inside Prime Video ad-tier navigation). Amazon FAST '
         'is smaller per-channel than Roku/Tubi/Pluto because most '
         'Prime audience defaults to on-demand. Top channels '
         '(Mr. Bean 24/7, The Three Stooges+, LaurenZSide branded '
         'channel, ABC News Live, curated pop-ups) 800K-1.8M US '
         'weekly viewers. Mid-tier 150K-500K. Long-tail 20-80K. '
         'Anchor: Amazon Q2 2026 shareholder letter + TVREV '
         'monthly rankings. Do NOT reason from Prime paid-catalog '
         'numbers.'
     )},
]


# Gaming platforms. Ceilings + anchor language for Xbox Game Pass
# Ultimate: Microsoft's cloud + console library subscription (~25M
# US subs mid-2026 per Ampere Analysis + Microsoft Q4 FY26 supplement).
# "Weekly US plays" = unique US subscribers who launched the title
# on Xbox / PC Game Pass / cloud in a rolling 7-day window. Anchor:
# Microsoft first-party engagement disclosures (rare, opt-in), Xbox
# Wire / Newzoo Cloud Gaming Insights, Circana US Games Tracker,
# GamesIndustry.biz weekly Steam+Xbox concurrency reports.
_GAMING_PLATFORMS_META = [
    {'key': 'xbox_gamepass',
     'label': 'Xbox Game Pass Ultimate',
     'ceiling': 6_000_000,
     'anchors': (
         'Xbox Game Pass Ultimate US subscriber base ~25M (mid-2026, '
         'Ampere Analysis + Microsoft cloud-gaming disclosures). '
         'Flagship first-party launch weeks (Starfield launch, Diablo '
         'IV Day-1-on-Game-Pass, Forza Motorsport, Indiana Jones + the '
         'Great Circle launch) hit 3-5M unique US weekly players. '
         'Steady-state top-3 typically 500K-2M weekly. Middle of the '
         'top-25 rail 100K-500K weekly. Long-tail cloud-only Retro / '
         'EA Play catalog games 15-80K weekly. Big AAA arrivals from '
         'a third-party publisher (Baldur\'s Gate 3 on GP, Persona 5 '
         'Royal, Like a Dragon: Infinite Wealth) sit 300K-1.2M in '
         'their first month, then decay to 80-250K weekly steady state. '
         '"Recently added" games routinely spike 3-6x their steady '
         'state in their launch week. Anchor: Microsoft first-party '
         'engagement disclosures, Newzoo Cloud Gaming Insights, '
         'Circana US Games Tracker.'
     )},
    # Meta Horizon Store (Meta Quest 2 / 3 / 3S + Quest Pro headset
    # base). US install base ~14-16M active headsets mid-2026
    # (Ampere + IDC AR/VR unit tracker; ~30M cumulative shipments
    # globally, ~55-60% US share on active-use panels). Weekly US
    # active players on the top free games (Gorilla Tag, VRChat,
    # Roblox VR) sit 800K-2.5M; mid-tier free 150K-500K; long-tail
    # free 20-80K. Paid catalog top titles (Beat Saber, BONELAB,
    # Blade & Sorcery: Nomad, GOLF+) 200K-800K weekly; mid-tier paid
    # 60K-200K; long-tail paid 10K-40K. Free rail carries the mass-
    # engagement scale; paid rail runs 3-5x smaller unique-user
    # counts but with heavier per-session time.
    {'key': 'meta_quest_free',
     'label': 'Meta Quest - Top Free',
     'ceiling': 3_500_000,
     'anchors': (
         'Meta Quest US active headset base ~14-16M mid-2026 (Ampere '
         'Analysis + IDC AR/VR shipment tracker + Meta Reality Labs '
         'quarterly engagement disclosures). Top-3 free games (Gorilla '
         'Tag, VRChat, Roblox VR) sit 800K-2.5M unique US weekly '
         'players; the store\'s "Most popular" trending rail is a '
         'reliable read on this tier. Mid-tier free (ranks 4-10) '
         '150K-500K weekly. Long-tail free (ranks 11-20) 20-80K '
         'weekly. Free apps skew heavier per-session time than paid '
         'because there is no purchase gate; social sandboxes like '
         'Gorilla Tag / VRChat run 45-90 min average session vs. '
         '20-30 for single-player paid. Big launches or viral '
         'moments (a new Gorilla Tag mode, a Roblox VR crossover '
         'weekend) spike a title 2-4x its steady state. Anchor: '
         'Meta Reality Labs disclosures, Newzoo VR/AR Games Tracker, '
         'Sensor Tower Meta Quest app analytics.'
     )},
    {'key': 'meta_quest_paid',
     'label': 'Meta Quest - Top Paid',
     'ceiling': 1_200_000,
     'anchors': (
         'Meta Quest US paid-catalog top sellers run 3-5x smaller '
         'weekly-user counts than the free rail because the purchase '
         'gate compresses the funnel. Beat Saber (evergreen top '
         'seller, $19.99 base + DLC packs) sits 400K-800K unique US '
         'weekly players; BONELAB / Blade & Sorcery: Nomad / GOLF+ / '
         'I Am Cat 200K-500K weekly. Mid-tier paid (ranks 4-10) '
         '60K-200K weekly. Long-tail paid (ranks 11-20) 10K-40K '
         'weekly. Big paid launches (Marvel\'s Deadpool VR, Assassin\'s '
         'Creed Nexus, Batman: Arkham Shadow) hit 300K-600K in their '
         'first month then decay to 40-120K steady state within 6 '
         'weeks. Bundle promotions during holiday windows spike '
         'Beat Saber / BONELAB 2-3x normal. Anchor: Ampere VR '
         'revenue charts, UploadVR weekly revenue tracker, Meta '
         'Reality Labs paid-catalog disclosures.'
     )},
    # Steam. Valve's public JSON APIs deliver two rails: Most Played
    # (24-hour peak concurrent players, an integer we treat as a hard
    # prior) and Top Sellers (weekly revenue rank, no concurrent
    # anchor, pure research). Steam active US user base ~24-29M
    # weekly (Steam 2023 recap = ~132M global MAU x ~18-22% US share
    # per Newzoo PC gaming distribution). Global peak concurrent ~35-
    # 40M; US peak concurrent proportionally ~5-8M. Ceiling for any
    # single title's weekly US unique-player count is ~24M (the whole
    # weekly-active US Steam base). Realistic per-title top-of-chart
    # weekly US 1-3M for a viral moment (Counter-Strike 2 launch,
    # Palworld launch week), 200K-800K for a steady top-10 title
    # (Dota 2, PUBG steady state), 40K-200K middle-of-rail, 5K-25K
    # long-tail. Titles with heavier international skew (Dota 2,
    # PUBG: BATTLEGROUNDS, PlayerUnknown's Battlegrounds Mobile,
    # Chinese Path of Exile competitors) run US share 12-16% instead
    # of the ~20% average; US-native indies + heavy-US-marketing
    # AAAs (Marvel Rivals, Palworld, Baldur's Gate 3) run 25-30%.
    {'key': 'steam_most_played',
     'label': 'Steam - Most Played',
     'ceiling': 5_000_000,
     'anchors': (
         'Steam Most Played rows carry Valve\'s live 24-hour peak '
         'concurrent US+global player integer as `steam_peak_in_game`. '
         'Convert to weekly US uniques via: weekly_us = peak_concurrent '
         '* peak_to_weekly_multiplier * us_share. peak_to_weekly_'
         'multiplier ~= 6-10x (a single instantaneous peak underrepres'
         'ents the weekly unique-player pool because players cycle in '
         'and out over the week). us_share defaults to ~0.20 (Steam '
         'weekly US audience ~24M vs. ~132M global MAU). Titles skew '
         'us_share by publisher / audience: Dota 2 / PUBG / Chinese '
         'indies 0.12-0.16, mainstream AAAs (Marvel Rivals, Palworld, '
         'Baldur\'s Gate 3, Cyberpunk 2077, GTA V) 0.22-0.30, US-first '
         'indies 0.30-0.40. The concurrent-player integer is a hard '
         'prior - the returned weekly US number must be within '
         '6-10x * (0.12-0.40) of the concurrent count. Ceiling ~5M '
         'weekly US for the biggest concurrent-league titles '
         '(Counter-Strike 2 peak windows). Anchor: Steam 2023 recap '
         'MAU disclosure, Newzoo PC gaming distribution, Circana US '
         'Games Tracker, GamesIndustry.biz Steam concurrency reports.'
     )},
    {'key': 'steam_top_sellers',
     'label': 'Steam - Top Sellers',
     'ceiling': 4_500_000,
     'anchors': (
         'Steam Top Sellers is ranked by weekly gross revenue (Valve '
         'does not publish unit counts). No live concurrent anchor - '
         'a title on this rail may or may not appear on Most Played. '
         'Convert weekly revenue rank to weekly US unique players by '
         'reasoning: chart position, base price ($0-$70), average '
         'discount, publisher category, expected buyer-to-player '
         'ratio (~1:1 for new releases in launch week, ~1:1.2 for '
         'evergreen bundles, ~1:3 for gift-heavy holiday windows). '
         'Big launch week (Baldur\'s Gate 3 week 1, Palworld launch, '
         'Cyberpunk 2077 relaunch) 800K-3M US weekly players. Steady '
         'top-10 evergreen (Counter-Strike 2, Dota 2, Rust) 300K-1M '
         'weekly. Mid-tier top-25 (Helldivers 2 quiet-week steady '
         'state, PUBG mid-week, EA Sports FC 25 off-week) 100K-400K '
         'weekly. Long-tail top-50 (Terraria evergreen, Stardew '
         'Valley evergreen, Rimworld) 40K-150K weekly. Where a title '
         'ALSO appears on Steam Most Played, cap the Top Sellers '
         'estimate at the Most Played estimate for that title '
         '(revenue rank is a subset of the weekly active pool). '
         'Anchor: Steam 2023 recap, SteamDB weekly revenue leader'
         'board, Circana US Games Tracker, GamesIndustry.biz.'
     )},
]


# Search terms + trending people + wiki topics + fused-trending headline
# rows. These are audience-interest counts, NOT sales/streams. The
# metric is "weekly US individuals who searched for OR read about the
# subject in the past 7 days" - a blend of Google search volume, news-
# mention exposure, and Wikipedia pageview interest that all funnel
# into the same underlying "engaged audience of the topic" number.
#
# One platform per kind because there's only one measurable surface for
# each: Google/Bing/DuckDuckGo aggregate to one search-audience count;
# news pickup + Wikipedia pageviews aggregate to one people-audience
# count. Ceilings match the US adult population (~260M) so a genuinely
# universal topic (Grand Canyon flood, Trump ruling) has headroom.
_SEARCH_TERM_PLATFORMS = [
    {'key': 'search_audience',
     'label': 'Weekly US searchers',
     'ceiling': 60_000_000,
     'anchors': (
         "Weekly US individuals who searched for this query on Google "
         "or a comparable engine in the past 7 days. Anchor to the "
         "trendspy Google Trends interest-score band (0-100, where 100 "
         "= a top-of-day breakout query) and typical US weekly search "
         "volume tiers: score 90-100 + volume 500K+ -> 8-30M weekly US "
         "searchers (Super Bowl weekend, iPhone launch tier); score "
         "70-89 -> 2-8M; score 50-69 -> 500K-2M; score 30-49 -> "
         "100-500K; score <30 -> 20-100K. Multiply UP by volume_growth_pct "
         "when a query is a fresh breakout (200%+ growth = 1.4-1.8x the "
         "band midpoint). Multiply DOWN when a query is stale (score "
         "trending flat + no news pickup = 0.6-0.8x). Category matters: "
         "sports team matchups spike hard for one weekend then decay "
         "(overweight); political/court-ruling queries have longer "
         "readership tails; celebrity-death queries spike then fade "
         "within 48h. Anchor sources: Google Trends US volume bands "
         "(SimilarWeb + Semrush cross-check), Comscore Media Metrix "
         "search-audience reports, Chartbeat + Parse.ly homepage-refer "
         "shares for the top news queries."
     )},
]

_TRENDING_PERSON_PLATFORMS = [
    {'key': 'audience_interest',
     'label': 'Weekly US audience interest',
     'ceiling': 80_000_000,
     'anchors': (
         "Weekly US individuals who searched for, read a news article "
         "about, watched a video of, or read the Wikipedia entry for "
         "this person in the past 7 days. This is an interest funnel "
         "count, not a fan count. Anchor to Wikipedia English-project "
         "US pageview share (~55-70% of English-Wikipedia traffic is "
         "US); Comscore Media Metrix person-topic reach; Chartbeat + "
         "Parse.ly per-name reader counts from the daily news pickup; "
         "SimilarWeb search-audience for the person's name query. "
         "Tiers: household-name celebrity in a major news moment "
         "(Trump, Elon, Taylor Swift album drop, world-champion athlete "
         "in their sport's peak week) -> 25-80M weekly US individuals; "
         "top-tier trending in their category (Aaron Judge in October, "
         "Novak Djokovic at a Grand Slam, a Supreme Court justice on a "
         "ruling day, an actor with a major theatrical release) -> "
         "5-25M; mid-tier trending person (a niche podcast host with a "
         "viral moment, a state-level politician in a national news "
         "cycle, a chef with a New York Times profile) -> 800K-5M; "
         "long-tail trending (a local-news figure, a business exec in "
         "their industry press only, an athlete off-season) -> 100K-800K. "
         "Wikipedia pageview lookups (accessible via the pageviews API) "
         "are the strongest single anchor for lesser-known names - "
         "convert daily pageviews to weekly US individuals via: "
         "weekly_us = daily_pageviews * 7 * 0.60 (US share) * 0.85 "
         "(unique-per-week share of gross views)."
     )},
]

_WIKI_TOPIC_PLATFORMS = [
    {'key': 'audience_interest',
     'label': 'Weekly US audience interest',
     'ceiling': 60_000_000,
     'anchors': (
         "Weekly US individuals who visited this Wikipedia entry, "
         "searched for the entity, or read a news article about it "
         "in the past 7 days. Wikipedia trending surfaces both people "
         "and non-people entities (events, places, organizations, "
         "franchises, songs, films), so anchor by category: household-"
         "name topic in a peak news moment (a chart-topping film's "
         "Wikipedia entry, a state visit, a Supreme Court ruling) -> "
         "10-40M weekly US; a well-known topic mid-cycle (Yellowstone, "
         "Everest, NASA mission) -> 1-8M; niche or historical topic "
         "with fresh news pickup (a scientist rediscovered in a news "
         "cycle, a lesser-known battle referenced in current events) -> "
         "300K-2M; long-tail topics with limited outside coverage -> "
         "40-300K. Anchor: Wikipedia pageview API US-share (~55-70%), "
         "Wikipedia Statistics dashboards, Chartbeat topic-reader data. "
         "Weekly conversion: weekly_us_readers = daily_pageviews * 7 * "
         "0.60 (US share) * 0.85 (unique-per-week share of gross views)."
     )},
]


def _platforms_for_kind(kind: str) -> list[dict]:
    if kind == 'song':
        return _SONG_PLATFORMS
    if kind == 'podcast':
        return _PODCAST_PLATFORMS
    if kind in ('film', 'tv', 'title'):
        return _STREAMING_PLATFORMS_META
    if kind in ('fast_film', 'fast_tv'):
        return _FAST_PLATFORMS_META
    if kind == 'fast_channel':
        return _FAST_CHANNEL_PLATFORMS_META
    if kind == 'game':
        return _GAMING_PLATFORMS_META
    if kind == 'book':
        return _BOOK_PLATFORMS
    if kind == 'goodreads_book':
        return _GOODREADS_PLATFORMS
    if kind == 'wattpad_story':
        return _WATTPAD_PLATFORMS
    if kind == 'comic':
        return _COMICS_PLATFORMS
    if kind == 'search_term':
        return _SEARCH_TERM_PLATFORMS
    if kind == 'trending_person':
        return _TRENDING_PERSON_PLATFORMS
    if kind == 'wiki_topic':
        return _WIKI_TOPIC_PLATFORMS
    return []


def _format_target_platforms(platforms: list[dict], focus_keys: set[str]) -> str:
    """Format the TARGET_PLATFORMS section of the prompt. `focus_keys`
    is the subset of platform keys the item actually charts on (based
    on chart_labels); those platforms get marked *[on chart]* so
    Claude prioritises returning numbers for them. Non-chart
    platforms still appear so the aggregate makes sense - Claude
    returns 0 for them if it can't defend a number."""
    lines = []
    for p in platforms:
        marker = ' *[on chart]*' if p['key'] in focus_keys else ''
        lines.append(
            f'  - "{p["key"]}"{marker}: {p["label"]} - '
            f'ceiling {p["ceiling"]:,} US weekly. {p["anchors"]}'
        )
    return '\n'.join(lines)


# Map chart-label prefix -> platform key. Used to convert
# `chart_labels` (e.g. ['Spotify Daily Top 200 (US) #3', 'Apple '
# 'Music Top 100 (US) #7']) into the set of platform keys the item
# actually appears on. Kept case-insensitive and forgiving so a label
# reword doesn't silently break the highlight.
_CHART_LABEL_TO_PLATFORM = (
    # Comics - most specific first so 'libby comics' doesn't get eaten
    # by the shorter 'libby' book prefixes further down. Each panel
    # label routes to its own comics-only platform key so a comics
    # row never lands in a book anchor tier.
    ('amazon comics',          'amazon_kindle'),
    ('apple books comics',     'apple_comics'),
    ('libby comics',           'libby_comics'),

    # Books - most specific first so 'libby: popular ebooks' isn't
    # eaten by the shorter 'libby' prefix.
    ('libby: popular ebooks',     'libby_ebook'),
    ('libby: popular audiobooks', 'libby_audio'),
    ('libby popular ebooks',      'libby_ebook'),
    ('libby popular audiobooks',  'libby_audio'),
    # Goodreads community weekly-read rail. One panel label today
    # ('Goodreads - Most Read This Week') that routes to the single
    # `goodreads_most_read` platform key. Kept as its own kind
    # ('goodreads_book') so the anchor tier is 20M US Goodreads
    # weekly actives, not the Amazon / Apple / Audible / Libby
    # per-store buyer / borrower base.
    ('goodreads - most read this week', 'goodreads_most_read'),
    ('goodreads most read this week',   'goodreads_most_read'),
    ('goodreads',                       'goodreads_most_read'),

    # Wattpad. All 6 rails route to the single `wattpad` platform key
    # (the anchor block reasons per-item, not per-rail; the rail
    # labels ride along in CHART CONTEXT for Claude's reference).
    ('wattpad - hot stories',   'wattpad'),
    ('wattpad - originals',     'wattpad'),
    ('wattpad - romance',       'wattpad'),
    ('wattpad - teen fiction',  'wattpad'),
    ('wattpad - fanfiction',    'wattpad'),
    ('wattpad - fantasy',       'wattpad'),
    ('wattpad',                 'wattpad'),
    ('apple books',       'apple'),
    ('amazon best-sellers (books)', 'amazon'),
    ('audible best-sellers',  'audible'),

    # Podcast / music (order matters - longer / more-specific first)
    ('spotify podcast',  'spotify'),   # podcast panel - Spotify Podcast Charts (US)
    ('spotify',          'spotify'),   # Spotify Daily Top 200 (US), Spotify Podcast Charts (US)
    ('apple podcasts',   'apple'),
    ('apple music',      'apple'),
    ('apple',            'apple'),
    # 2026-08-31: YouTube Popular Podcasts (US) - podcast platform key
    # is `youtube_podcasts`, distinct from the `youtube` key used for
    # YouTube Music song rankers. Must beat both `youtube music` and
    # the bare `youtube` catch-all below.
    ('youtube popular podcasts', 'youtube_podcasts'),
    ('youtube podcasts', 'youtube_podcasts'),
    ('youtube music',    'youtube'),
    ('youtube',          'youtube'),
    ('amazon music podcasts', 'amazon'),
    ('amazon music',     'amazon'),
    ('amazon live tv',   'amazon'),   # FAST channel; must come before bare 'amazon'
    ('amazon',           'amazon'),
    ('audible',          'audible'),
    ('netflix',          'netflix'),
    ('disney',           'disneyplus'),
    ('hulu',             'hulu'),
    ('hbo max',          'max'),
    ('max',              'max'),
    ('prime video',      'primevideo'),
    ('prime',            'primevideo'),
    ('espn+',            'espnplus'),
    ('espn',             'espnplus'),
    # 2026-08-20: BritBox + MGM+ + Starz streaming platforms.
    ('britbox',          'britbox'),
    ('mgm+',             'mgmplus'),
    ('mgm plus',         'mgmplus'),
    ('mgmplus',          'mgmplus'),
    ('starz',            'starz'),
    # FAST-channel platforms (chart-label prefixes from `_FAST_SLUGS`).
    # `amazon live tv` is handled up above alongside `amazon music` to
    # win the match before the bare `amazon` catch-all.
    ('roku channel',     'roku'),
    ('roku',             'roku'),
    ('tubi',             'tubi'),
    ('pluto tv',         'pluto'),
    ('pluto',            'pluto'),
    ('shazam',           'shazam'),
    ('tiktok',           'tiktok'),
    # Gaming (order matters - specific pill labels first so the
    # 'Top Free' / 'Top Paid' suffixes route to the right Meta
    # Quest panel key). `_GAMING_SLUGS` labels are 'Xbox Game Pass
    # Ultimate', 'Meta Quest - Top Free', 'Meta Quest - Top Paid'.
    ('meta quest - top free', 'meta_quest_free'),
    ('meta quest - top paid', 'meta_quest_paid'),
    ('meta quest top free',   'meta_quest_free'),
    ('meta quest top paid',   'meta_quest_paid'),
    ('xbox game pass ultimate', 'xbox_gamepass'),
    ('xbox game pass',   'xbox_gamepass'),
    ('xbox',             'xbox_gamepass'),
    # Steam (Valve). Two rails packed in one snapshot; each rail
    # gets its own platform key so anchors + ceilings apply per-rail.
    ('steam - most played', 'steam_most_played'),
    ('steam - top sellers', 'steam_top_sellers'),
    ('steam most played',   'steam_most_played'),
    ('steam top sellers',   'steam_top_sellers'),
)


def _focus_keys_from_charts(chart_labels: list[str]) -> set[str]:
    """Which platform keys does this item actually chart on? Returns
    a set of platform keys matching `_CHART_LABEL_TO_PLATFORM`."""
    out: set[str] = set()
    for label in chart_labels or []:
        lo = label.lower()
        for prefix, key in _CHART_LABEL_TO_PLATFORM:
            if lo.startswith(prefix) or prefix in lo:
                out.add(key)
                break
    return out


_LIBBY_PROJECTION_NOTE = (
    "LIBBY PROJECTION RULE (only applies to libby_ebook and "
    "libby_audio platforms):\n"
    "  The raw signal in CHART CONTEXT for Libby rows is the HOLD COUNT "
    "at LA County Library (a single US public library system serving "
    "~10M residents, roughly 3-4% of the US public-library-served "
    "population). LA County holds are NOT the answer - you must project "
    "them up to US-wide weekly library borrows.\n"
    "  Preferred approach (in order):\n"
    "    a) Cite OverDrive's public 'Big Library Read' / 'Popular "
    "Reads This Week' data for the specific title if available.\n"
    "    b) Cite Publishers Weekly / American Libraries digital-loan "
    "reports.\n"
    "    c) If neither exists, project holds -> weekly US borrows "
    "using the ~25-35x LA County -> US library patrons scale, biased "
    "LOW: divide by 7 to get a weekly rate if the holds are current-"
    "queue rather than weekly circulation, and cap at the platform's "
    "ceiling.\n"
    "  Always state the projection method used in `note` for the "
    "libby_* platforms. Do NOT return the raw LA County number.\n"
)


def _build_prompt(item: dict) -> str:
    kind          = item['kind']
    display_title = item['display_title']
    artist        = item.get('artist') or ''
    charts        = item.get('chart_labels') or []
    chart_str     = ', '.join(charts[:6]) if charts else '(no chart context)'
    focus_keys    = _focus_keys_from_charts(charts)

    libby_note = ''

    if kind == 'podcast':
        unit  = 'weekly US listeners'
        query = (f'"{display_title} podcast" Podtrac US weekly '
                 f'listeners Edison Podcast Metrics 2026')
        item_line = f'PODCAST TITLE: {display_title}\nPUBLISHER: {artist or "(unknown)"}'
        # If a podcast row's chart labels come from YouTube ONLY (no
        # Apple / Spotify / Amazon Music / Audible / Netflix in the
        # label set), treat it as a YouTube-native video podcast. Many
        # of these (Rotten Mango, Dr. Insanity, Lawyer You Know,
        # Nightcap, MeidasTouch, Kill Tony, Diary of a CEO, ShxtsNGigs,
        # ...) never appear on Podtrac or Edison rankings because those
        # rankers were audio-only for years; the show is real and
        # measurable via its YouTube channel. Anchor to the channel
        # subscriber count + weekly views on the podcast-episode
        # uploads, using the youtube_podcasts platform block below. Do
        # NOT return 0 for youtube_podcasts on these rows just because
        # Podtrac/Edison have no entry.
        yt_only = ('youtube_podcasts' in focus_keys
                   and not (focus_keys & {'apple', 'spotify', 'amazon',
                                          'audible', 'netflix'}))
        if yt_only:
            query = (f'"{display_title}" YouTube channel subscribers '
                     f'weekly views podcast episodes 2026')
            item_line += (
                '\nYOUTUBE-EXCLUSIVE ROUTING: this show appears ONLY on '
                'the YouTube Popular Podcasts ranker. Anchor the '
                'youtube_podcasts number to the show\'s YouTube channel '
                'subscriber base + typical weekly views on new episode '
                'uploads (weekly_us ~= 0.15-0.30 x weekly video views on '
                'the channel, then multiply by US share ~0.55-0.75 for '
                'US-native shows). Return 0 for apple/spotify/amazon/'
                'audible/netflix - those platforms don\'t carry this '
                'show - but DO return a defensible number for '
                'youtube_podcasts. Do not return 0 across the board just '
                'because Podtrac / Edison don\'t list this show; the '
                'ranker itself is proof the show has meaningful US reach.'
            )
    elif kind == 'song':
        unit  = 'weekly US streams'
        query = (f'"{display_title}" "{artist}" Luminate US streams '
                 f'weekly Chartmetric per DSP')
        item_line = f'SONG TITLE: {display_title}\nARTIST: {artist or "(unknown)"}'
    elif kind == 'film':
        unit  = 'weekly US views'
        query = (f'"{display_title}" Nielsen streaming top 10 US '
                 f'households weekly 2026')
        item_line = f'FILM TITLE: {display_title}'
    elif kind == 'tv':
        unit  = 'weekly US views'
        query = (f'"{display_title}" Nielsen streaming top 10 US TV '
                 f'series weekly 2026')
        item_line = f'TV SERIES TITLE: {display_title}'
    elif kind in ('fast_film', 'fast_tv'):
        # FAST = Free Ad-Supported Streaming TV. Frame the ask around
        # "who watched for free this week on an ad-supported linear-
        # style channel" -- NOT paid SVOD weekly views, and NOT
        # aggregate MAU. Anchor Claude to Nielsen FAST Gauge / TVREV /
        # Antenna FAST reports and per-platform earnings disclosures.
        # For Amazon specifically, reinforce that the number must
        # reflect the Amazon Live TV UI only (not Prime paid catalog),
        # because JustWatch's `amp` package occasionally surfaces
        # titles that also exist on paid Prime.
        unit  = 'weekly US views'
        noun  = 'FILM' if kind == 'fast_film' else 'TV SERIES'
        query = (f'"{display_title}" Tubi Pluto Roku Channel FAST '
                 f'Nielsen Gauge TVREV weekly US ad-supported viewers 2026')
        item_line = (f'{noun} TITLE (FAST / ad-supported free tier): '
                     f'{display_title}')
    elif kind == 'game':
        # Weekly US plays / players = unique US subs / headset owners
        # / Steam players who LAUNCHED the title in the past 7 days.
        # The specific platform anchor depends on which gaming rail
        # this title actually charts on - each rail has a very
        # different user base and per-title reach curve, so we route
        # to the right anchor language + search query per platform.
        plat_keys = focus_keys & {'xbox_gamepass',
                                   'meta_quest_free', 'meta_quest_paid',
                                   'steam_most_played', 'steam_top_sellers'}
        pub_str = f'\nPUBLISHER: {artist}' if artist else ''
        # Steam Most Played rows carry Valve's live 24-hour peak
        # concurrent-player integer as a hard prior. Surface it in the
        # prompt so Claude reasons the multiplier + US share instead
        # of guessing the raw active-player count.
        peak = item.get('steam_peak_in_game')
        peak_line = ''
        if peak:
            peak_line = (f'\nSTEAM PEAK CONCURRENT (24hr): {int(peak):,} '
                         'players (Valve public JSON API). '
                         'weekly_us = peak_concurrent * '
                         'peak_to_weekly_multiplier * us_share; '
                         'multiplier ~6-10x, us_share ~0.12-0.30 '
                         'depending on title.')
        if 'xbox_gamepass' in plat_keys:
            unit  = ('weekly US plays (unique US Game Pass Ultimate '
                     'subscribers who launched the game in the past 7 days)')
            query = (f'"{display_title}" Xbox Game Pass weekly US players '
                     f'Newzoo Circana Ampere Analysis 2026')
            plat_line = ('Xbox Game Pass Ultimate (~25M US subs, includes '
                         'console + PC + Xbox Cloud Gaming)')
        elif 'meta_quest_free' in plat_keys:
            unit  = ('weekly US players (unique US Meta Quest headset '
                     'owners who launched this free title in the past 7 days)')
            query = (f'"{display_title}" Meta Quest VR weekly US players '
                     f'Newzoo Ampere Sensor Tower Reality Labs 2026')
            plat_line = ('Meta Quest - Top Free (~14-16M US active headset '
                         'base mid-2026; free titles reach the majority of '
                         'the installed base with longer per-session times '
                         'than paid rail because there is no purchase gate)')
        elif 'meta_quest_paid' in plat_keys:
            unit  = ('weekly US players (unique US Meta Quest headset '
                     'owners who launched this paid title in the past 7 days)')
            query = (f'"{display_title}" Meta Quest VR weekly US players '
                     f'UploadVR Ampere Reality Labs revenue 2026')
            plat_line = ('Meta Quest - Top Paid (~14-16M US active headset '
                         'base mid-2026; paid catalog runs 3-5x smaller '
                         'unique-user counts than the free rail because the '
                         'purchase gate compresses the funnel)')
        elif 'steam_most_played' in plat_keys:
            unit  = ('weekly US players (unique US Steam accounts that '
                     'launched the game in the past 7 days)')
            query = (f'"{display_title}" Steam weekly US players concurrent '
                     f'SteamDB Newzoo Circana 2026')
            plat_line = ('Steam - Most Played (~24-29M US weekly active '
                         'Steam users mid-2026; the concurrent-player '
                         'integer above is a hard prior; reason the '
                         'weekly multiplier + US share, not the raw '
                         'player count)')
        elif 'steam_top_sellers' in plat_keys:
            unit  = ('weekly US players (unique US Steam accounts that '
                     'launched or purchased the game in the past 7 days)')
            query = (f'"{display_title}" Steam weekly sales US players '
                     f'SteamDB revenue Circana 2026')
            plat_line = ('Steam - Top Sellers (~24-29M US weekly active '
                         'Steam users mid-2026; ranked by weekly revenue, '
                         'no live concurrent anchor; reason from chart '
                         'position + base price + expected buyer-to-'
                         'player ratio; where the title ALSO appears on '
                         'Most Played cap at that estimate)')
        else:
            unit  = ('weekly US plays (unique US subscribers / headset '
                     'owners / Steam accounts who launched the game in the '
                     'past 7 days)')
            query = (f'"{display_title}" weekly US players Xbox Game Pass '
                     f'Meta Quest VR Steam Newzoo Circana Ampere 2026')
            plat_line = ('one of Xbox Game Pass Ultimate, Meta Quest Top '
                         'Free, Meta Quest Top Paid, Steam Most Played, or '
                         'Steam Top Sellers; use the chart labels above to '
                         'identify which rail this title actually lives on')
        item_line = (f'GAME TITLE: {display_title}{pub_str}{peak_line}\n'
                     f'PLATFORM: {plat_line}')
    elif kind == 'fast_channel':
        # "Weekly US viewers" = unique US TVs / households that tuned
        # to this 24/7 linear channel on this specific FAST platform
        # for at least one minute in the past 7 days. Airings/week is
        # the strongest intra-platform signal we have -- Claude
        # reasons from airings + channel-IP recognizability + the
        # platform's total weekly actives to a defensible number.
        # `artist` carries the platform slug (roku/tubi/pluto/amazon).
        plat_slug = (item.get('fast_platform') or artist or '').lower()
        plat_label_map = {
            'roku':   'Roku Channel',
            'tubi':   'Tubi',
            'pluto':  'Pluto TV',
            'amazon': 'Amazon Live TV',
        }
        plat_label = plat_label_map.get(plat_slug, plat_slug or '(unknown)')
        unit = ('weekly US viewers (unique US households tuning to '
                'this 24/7 linear FAST channel for at least one '
                'minute in the past 7 days)')
        query = (f'"{display_title}" "{plat_label}" FAST channel '
                 f'weekly US viewers Nielsen Gauge TVREV 2026')
        airings   = int(item.get('airings') or 0)
        ctype     = item.get('content_type') or ''
        ctype_str = f'\nCONTENT TYPE: {ctype}' if ctype else ''
        item_line = (
            f'FAST CHANNEL NAME: {display_title}\n'
            f'FAST PLATFORM: {plat_label}\n'
            f'INTRA-PLATFORM AIRINGS/WEEK (raw signal, higher = more '
            f'popular within this platform): {airings:,}'
            f'{ctype_str}\n'
            f'REASONING GUIDANCE: airings/wk is an intra-platform '
            f'popularity signal only - use it to rank this channel '
            f'RELATIVE to other channels on {plat_label}, but the '
            f'absolute weekly-viewers number comes from '
            f"{plat_label}'s total weekly US actives times a share "
            f'that reflects the channel\'s prominence + IP '
            f'recognition + programming appeal. A no-name single-'
            f'show reruns channel with 400 airings/wk gets far '
            f'fewer viewers than a branded flagship (Nick Jr., '
            f'CBS News, Fox Weather, Mr. Bean) even at the same '
            f'airings count.'
        )
    elif kind == 'book':
        unit  = ('weekly US audience (readers/listeners/borrowers per '
                 'platform - amazon+apple = readers, audible = '
                 'listeners, libby_* = library borrows PROJECTED to '
                 'the US public-library ecosystem, not local holds)')
        query = (f'"{display_title}" "{artist}" Circana BookScan US '
                 f'weekly sales OverDrive Libby US borrows 2026')
        item_line = f'BOOK TITLE: {display_title}\nAUTHOR: {artist or "(unknown)"}'
        # Surface any Libby LA County hold count so Claude has the raw
        # local signal it must project upward. The prompt already
        # tells it how to convert.
        holds_by_plat = item.get('libby_holds_by_type') or {}
        if holds_by_plat:
            parts = [f'{p} raw LA County holds: {n:,}'
                      for p, n in holds_by_plat.items() if n > 0]
            if parts:
                item_line += ('\nLIBBY RAW SIGNAL (LA County only, '
                              'must be projected up):\n  '
                              + '\n  '.join(parts))
        libby_note = '\n' + _LIBBY_PROJECTION_NOTE
    elif kind == 'goodreads_book':
        unit = ('weekly US readers (unique US readers who read this '
                'book in the past 7 days, projected from the '
                'Goodreads community weekly-read signal)')
        query = (f'"{display_title}" "{artist}" Goodreads currently '
                 f'reading US weekly readers 2026')
        priors = item.get('goodreads_priors') or {}
        try:
            currently_reading = int(priors.get('currently_reading_count') or 0)
        except Exception:
            currently_reading = 0
        try:
            ratings_count = int(priors.get('ratings_count') or 0)
        except Exception:
            ratings_count = 0
        try:
            avg_rating = float(priors.get('avg_rating') or 0.0)
        except Exception:
            avg_rating = 0.0
        try:
            published_year = int(priors.get('published_year') or 0)
        except Exception:
            published_year = 0
        is_new_release = bool(priors.get('is_new_release'))

        signal_lines = [
            (f'GOODREADS COMMUNITY WEEKLY READ COUNT: '
             f'{currently_reading:,} (native Goodreads, THE strongest '
             f'single anchor - this is the count of US Goodreads users '
             f'who logged this book as read/reading in the past 7 days '
             f'and drives the Most-Read-This-Week ordering itself)'),
            (f'CUMULATIVE RATINGS COUNT: {ratings_count:,} (lifetime, '
             f'proxies durable-base popularity)'),
            (f'AVERAGE RATING: {avg_rating:.2f} / 5'),
        ]
        if published_year:
            signal_lines.append(f'PUBLISHED YEAR: {published_year}')
        if is_new_release:
            signal_lines.append(
                'RECENTLY PUBLISHED (<=~30d): YES (fresh-cohort '
                'multiplier ~1.3-1.8x on the base Goodreads-to-US '
                'conversion; early reader velocity peaks in the '
                'first ~30 days)'
            )
        signal_str = '\n'.join(signal_lines)
        item_line = (
            f'BOOK TITLE: {display_title}\n'
            f'AUTHOR: {artist or "(unknown)"}\n'
            f'{signal_str}\n'
            f'REASONING GUIDANCE: the metric is unique US readers who '
            f'read this book in the past 7 days across ALL surfaces '
            f'(Kindle, print, audio, library, Goodreads-native), '
            f'projected from the Goodreads community signal. Goodreads '
            f'reports ~150M global users, ~35-40% US share (~50-60M US '
            f'Goodreads users); weekly US actives ~15-20M readers. '
            f'Goodreads-active adults are ~10-20% of all US book '
            f'readers per Pew, so total US weekly readers ~= '
            f'goodreads_currently_reading * 6-10x depending on tier: '
            f'mass-market #1 (currently_reading >15K) -> 300K-800K '
            f'weekly US; steady-state top-10 (currently_reading '
            f'5-15K) -> 60K-200K; mid ranks 20-50 (currently_reading '
            f'3-6K) -> 20K-70K. Bias DOWN for genre-fiction, UP for '
            f'literary + book-club releases (they over-index on '
            f'Goodreads). Apply the fresh-release multiplier when the '
            f'flag is set. Sanity-check bounds: weekly_us MUST be >= '
            f'currently_reading_count (Goodreads-observed floor) AND '
            f'<= 20M platform ceiling. If currently_reading is 0, '
            f'return 0 - never guess. Bias LOW when signals are thin.'
        )
    elif kind == 'wattpad_story':
        # Weekly US readers = unique US Wattpad users who opened at
        # least one chapter of this story in the past 7 days.
        # Cumulative all-time reads is the single strongest per-item
        # anchor - Wattpad exposes it natively on every story - and
        # the Originals flag / recently-published flag shape the
        # decay math. Aim for a defensible fraction of the cumulative,
        # not a raw pick.
        unit  = ('weekly US readers (unique US Wattpad users who '
                 'opened at least one chapter of this story in the '
                 'past 7 days)')
        query = (f'"{display_title}" "{artist}" Wattpad reads votes '
                 f'US weekly readers 2026')
        priors = item.get('native_priors') or {}
        cum_reads = int(priors.get('wattpad_reads_cumulative') or 0)
        votes = int(priors.get('wattpad_votes') or 0)
        chapters = int(priors.get('wattpad_chapters') or 0)
        genre = priors.get('wattpad_genre_primary') or ''
        is_originals = bool(priors.get('wattpad_originals'))
        is_new_14d = bool(priors.get('wattpad_is_new_14d'))
        is_completed = bool(priors.get('wattpad_is_completed'))

        signal_lines = [
            f'CUMULATIVE ALL-TIME READS: {cum_reads:,} (native Wattpad, '
            f'strongest single anchor)',
            f'CUMULATIVE VOTES / LIKES: {votes:,}',
            f'CHAPTERS: {chapters}',
        ]
        if genre:
            signal_lines.append(f'PRIMARY GENRE: {genre}')
        if is_originals:
            signal_lines.append(
                'WATTPAD ORIGINALS: YES (paid / studio-invested '
                'serialized fiction; US share skews HIGHER on this '
                'rail, ~0.55 vs platform default ~0.42; bump the '
                'converted weekly-US-readers ~20-30% vs a comparable '
                'non-Originals story with the same cumulative reads)'
            )
        if is_new_14d:
            signal_lines.append(
                'FIRST-PUBLISHED IN LAST 14 DAYS: YES (fresh-cohort '
                'multiplier ~2-5x on the tail-decay math - early '
                'reader velocity peaks in the first ~30 days after '
                'publication)'
            )
        if is_completed:
            signal_lines.append(
                'STORY STATUS: completed (ongoing tail readership; '
                'no new-chapter reader-spike, level curve)'
            )
        else:
            signal_lines.append(
                'STORY STATUS: ongoing (new-chapter drops trigger '
                'reader-spike windows; current weekly readership '
                'runs above the long-run tail)'
            )
        signal_str = '\n'.join(signal_lines)
        item_line = (
            f'WATTPAD STORY: {display_title}\n'
            f'AUTHOR: {artist or "(unknown)"}\n'
            f'{signal_str}\n'
            f'REASONING GUIDANCE: the metric is unique US Wattpad '
            f'users who opened at least one chapter in the past 7 '
            f'days, NOT total chapter opens or session count. '
            f'Wattpad total: ~90M global MAU, ~40-50% US share '
            f'(platform default), ~15-20M weekly US readers overall. '
            f'Convert cumulative reads to weekly US using a '
            f'decay-adjusted fraction of the cumulative total * US '
            f'share; use the per-item flags above to shape the '
            f'decay + share. Sanity-check bounds: weekly_us must be '
            f'< cumulative_reads (impossible to have more weekly '
            f'readers than lifetime reads). Ceiling for the '
            f'platform is 20M weekly US readers, but a real story '
            f'top-of-Hot mega-hit lands ~200K-800K weekly US on '
            f'current-trending fresh cohorts; steady-state top-10 '
            f'~15K-150K; long-tail newly-charting 200-2000. Bias '
            f'LOW when signals are thin.'
        )
    elif kind == 'comic':
        unit  = ('weekly US audience per platform (amazon_kindle = '
                 'weekly US buyers of the physical / trade edition, '
                 'apple_comics = weekly US buyers of the digital '
                 'edition, libby_comics = weekly US library borrows '
                 'PROJECTED to the US public-library ecosystem - '
                 'never the raw LA County hold count)')
        query = (f'"{display_title}" "{artist}" Circana BookScan '
                 f'graphic novel US weekly sales Comichron ICv2 '
                 f'OverDrive Libby comics US borrows 2026')
        item_line = (f'COMIC / GRAPHIC NOVEL TITLE: {display_title}\n'
                      f'AUTHOR / CREATOR: {artist or "(unknown)"}')
        # Surface any Libby LA County hold count so Claude has the
        # raw local signal it must project upward. Comics Libby
        # panel carries the same `holds` field books use, so the
        # 25-35x projection rule applies here too.
        raw_holds = int(item.get('libby_holds') or 0)
        if raw_holds > 0:
            item_line += ('\nLIBBY RAW SIGNAL (LA County only, must '
                          f'be projected up):\n  libby_comics raw '
                          f'LA County holds: {raw_holds:,}')
        libby_note = '\n' + _LIBBY_PROJECTION_NOTE.replace(
            'libby_ebook and libby_audio', 'libby_comics')
    elif kind == 'search_term':
        unit  = ('weekly US searchers (unique US individuals who '
                 'searched for this query on Google or a comparable '
                 'engine in the past 7 days)')
        query = (f'"{display_title}" Google Trends US weekly search '
                 f'volume Semrush SimilarWeb 2026')
        interest = item.get('interest_score')
        growth = item.get('volume_growth_pct')
        category = item.get('category') or ''
        signal_lines = []
        if interest is not None:
            signal_lines.append(
                f'GOOGLE TRENDS INTEREST SCORE (0-100, higher = hotter '
                f'right now): {interest}'
            )
        if growth is not None:
            signal_lines.append(
                f'WEEK-OVER-WEEK VOLUME GROWTH: {growth}% '
                f'(triple-digit growth = fresh breakout, apply the '
                f'1.4-1.8x multiplier from the anchor guidance)'
            )
        if category:
            signal_lines.append(f'CATEGORY: {category}')
        signal_str = ('\n' + '\n'.join(signal_lines)) if signal_lines else ''
        item_line = (
            f'SEARCH QUERY: {display_title}{signal_str}\n'
            f'REASONING GUIDANCE: this is a trending search query. '
            f'The metric is unique US individuals who searched for '
            f'this query in the past 7 days, NOT total impressions or '
            f'clicks. Anchor to the interest-score band above; use '
            f'the category and growth to shape the multiplier. Sports '
            f'matchups spike hard for one weekend then decay. Political '
            f'/ court-ruling queries have longer reader tails. '
            f'Celebrity-death queries spike then fade within 48h.'
        )
    elif kind == 'trending_person':
        unit  = ('weekly US audience interest (unique US individuals '
                 'who searched for, read a news article about, watched '
                 'a video of, or read the Wikipedia entry for this '
                 'person in the past 7 days)')
        query = (f'"{display_title}" Wikipedia pageviews US weekly news '
                 f'coverage Chartbeat Parse.ly 2026')
        role = artist or item.get('role') or ''
        wiki_daily = item.get('wiki_daily_pageviews')
        news_count = item.get('news_mentions')
        signal_lines = []
        if role:
            signal_lines.append(f'ROLE / CATEGORY: {role}')
        if wiki_daily:
            signal_lines.append(
                f'WIKIPEDIA DAILY PAGEVIEWS (English project, last 7-day '
                f'avg): {int(wiki_daily):,}. Convert: weekly_us = '
                f'daily * 7 * 0.60 (US share) * 0.85 (unique-per-week '
                f'share of gross views).'
            )
        if news_count:
            signal_lines.append(
                f'NEWS MENTIONS (last 7 days, aggregated across US '
                f'outlets): {int(news_count):,}. Use as a secondary '
                f'signal - high mentions + high pageviews = broad '
                f'news-cycle audience; low mentions + high pageviews '
                f'= fan-driven or evergreen interest.'
            )
        signal_str = ('\n' + '\n'.join(signal_lines)) if signal_lines else ''
        item_line = (
            f'PERSON NAME: {display_title}{signal_str}\n'
            f'REASONING GUIDANCE: this is a currently-trending person. '
            f'The metric is US individuals who engaged with any '
            f'information about them in the past 7 days (search + news '
            f'read + Wikipedia lookup + video view - deduped to unique '
            f'individuals). This is an interest funnel, not a fan count. '
            f'Use the Wikipedia pageview signal as the strongest single '
            f'anchor when present; otherwise anchor to the role/category '
            f'tiers in the platform guidance.'
        )
    elif kind == 'wiki_topic':
        unit  = ('weekly US audience interest (unique US individuals '
                 'who visited this Wikipedia entry, searched for the '
                 'entity, or read a news article about it in the past '
                 '7 days)')
        query = (f'"{display_title}" Wikipedia pageviews US weekly news '
                 f'coverage 2026')
        wiki_daily = item.get('wiki_daily_pageviews')
        wiki_desc = artist or item.get('wiki_description') or ''
        signal_lines = []
        if wiki_desc:
            signal_lines.append(f'WIKIPEDIA SHORT DESCRIPTION: {wiki_desc}')
        if wiki_daily:
            signal_lines.append(
                f'WIKIPEDIA DAILY PAGEVIEWS (English project, last 7-day '
                f'avg): {int(wiki_daily):,}. Convert: weekly_us = '
                f'daily * 7 * 0.60 (US share) * 0.85 (unique-per-week '
                f'share of gross views). Adjust down if the topic is '
                f'primarily non-US in scope; adjust up if the topic is '
                f'in a peak news moment where non-Wikipedia readership '
                f'(news + search) exceeds Wikipedia readership.'
            )
        signal_str = ('\n' + '\n'.join(signal_lines)) if signal_lines else ''
        item_line = (
            f'WIKIPEDIA TRENDING TOPIC: {display_title}{signal_str}\n'
            f'REASONING GUIDANCE: this is a trending Wikipedia entry. '
            f'The metric is US individuals who read the entry, searched '
            f'for the entity, or read a news article about it in the '
            f'past 7 days - a blended interest funnel. Use the Wikipedia '
            f'pageview conversion above as the strongest single anchor; '
            f'add news + search overlay based on the topic category.'
        )
    else:
        unit  = 'weekly US views'
        query = f'"{display_title}" weekly viewers US streaming 2026'
        item_line = f'TITLE: {display_title}'

    platforms = _platforms_for_kind(kind)
    # For FAST channels, restrict the prompt to the ONE platform the
    # channel actually lives on - the same channel name on Pluto vs
    # Roku vs Tubi are three separate items with three separate keys,
    # so each Claude call should reason about exactly one platform.
    # This also cuts prompt cost by ~3x on this kind.
    if kind == 'fast_channel':
        plat_slug = (item.get('fast_platform') or artist or '').lower()
        platforms = [p for p in platforms if p['key'] == plat_slug]
        focus_keys = {plat_slug} if platforms else set()
    if not platforms:
        # Fallback - shouldn't happen with current callers but keep
        # graceful degrade.
        target_section = ''
    else:
        target_section = (
            '\nTARGET_PLATFORMS (return one entry in by_platform for '
            'each; platforms marked *[on chart]* are where this item '
            'actually appears - those numbers matter MOST):\n'
            + _format_target_platforms(platforms, focus_keys)
            + '\n'
        )

    return (
        _PROMPT_HEADER
        + f'\nTARGET METRIC (per platform): {unit}\n'
        + target_section
        + libby_note
        + '\n' + item_line
        + f'\nCHART CONTEXT: {chart_str}\n'
        + f'\nSUGGESTED SEARCH QUERY (feel free to refine): {query}\n\n'
        + 'JSON output:'
    )


def _extract_json_blob(text: str) -> Optional[dict]:
    if not text:
        return None
    # Look for the first {..} object, tolerating markdown fences.
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None
    raw = m.group(0)
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Sometimes Claude includes trailing commentary; strip trailing
    # non-JSON after the last `}`.
    last_close = raw.rfind('}')
    if last_close > 0:
        try:
            return json.loads(raw[:last_close + 1])
        except Exception:
            return None
    return None


# AGGREGATE per-kind ceilings on the all-platforms US weekly total.
# Sum of the per-platform ceilings for the kind, biased slightly
# conservative (we never expect a real item to peg every platform at
# its historical peak simultaneously). Aggressive hallucinations get
# clamped + flagged 'low' confidence.
#
# Individual platform ceilings live on the platform records in
# _SONG_PLATFORMS / _PODCAST_PLATFORMS / _STREAMING_PLATFORMS_META.
_MAX_ESTIMATE_BY_KIND = {
    'podcast': 20_000_000,     # Podtrac #1 US ~ 8M; ceiling generous 2x
    'song':    50_000_000,     # Sum of song platform ceilings (~50M)
    'film':    75_000_000,     # Sum of streaming platform ceilings (~90M) biased down
    'tv':      75_000_000,
    'title':   75_000_000,
    # `book` covers Amazon + Apple + Audible + Libby (ebook + audio).
    # Sum of per-platform ceilings ~740K; aggregate ceiling biased
    # down to 500K. A weekly release-week best-seller might hit this;
    # steady-state top-10 books read far below.
    'book':    500_000,
    # Goodreads community weekly-read audience: the metric is unique
    # US readers who read a given book this week across ALL surfaces
    # (Kindle, print, audio, library, Goodreads-native), projected
    # from the Goodreads community weekly-read signal. Ceiling
    # ~= US Goodreads weekly actives (~15-20M) with a small buffer
    # for the projected-outside-Goodreads slice on a mega title.
    'goodreads_book': 20_000_000,
    # Wattpad: single-platform (all 6 rails route to the `wattpad`
    # key). Ceiling matches the platform ceiling: US Wattpad weekly
    # reader base ~20M. A mega-hit at peak on the current Hot rail
    # could plausibly reach ~500-800K weekly US readers; ceiling of
    # 20M is a hard-limit safety, not a target.
    'wattpad_story': 20_000_000,
    # `comic` covers Amazon Comics (physical) + Apple Books Comics
    # (digital) + Libby Comics (public-library digital). Sum of
    # per-platform ceilings ~85K; aggregate ceiling biased down to
    # 60K. A once-in-a-decade launch-week hit (Fourth Wing GN /
    # Absolute Batman #1) might hit this on release week; steady-
    # state top-10 comics read far below.
    'comic':   60_000,
    # FAST aggregate: sum of Roku 6M + Tubi 6M + Pluto 5M + Amazon 4M
    # = 21M weekly. Biased conservative to 18M; even the biggest
    # cross-platform FAST hit (Everybody Loves Raymond simultaneously
    # on Roku Channel + Pluto + Tubi) wouldn't peg every ceiling
    # simultaneously.
    'fast_film': 18_000_000,
    'fast_tv':   18_000_000,
    # Gaming: Xbox Game Pass Ultimate. Ceiling matches the per-platform
    # ceiling because there is only one platform today. Bumps once we
    # add PS Plus / Nintendo Switch Online / Steam.
    'game':      6_000_000,
    # FAST channel: aggregate ceiling matches the highest per-platform
    # ceiling (Roku 4M) because each channel row lives on exactly ONE
    # platform - there's no cross-platform aggregation for channels
    # the way there is for a title that runs on Roku + Tubi + Pluto.
    'fast_channel': 4_000_000,
    # Search / person / wiki-topic - single-platform kinds. Ceiling
    # matches the platform ceiling (search 60M / person 80M / topic
    # 60M) because there is exactly one measurable surface per row.
    'search_term':      60_000_000,
    'trending_person':  80_000_000,
    'wiki_topic':       60_000_000,
}
_CLAMP_TO_FRACTION = 0.4         # Bias clamped values conservative (was 0.5)


def _default_unit_for_kind(kind: str) -> str:
    """Default unit label per kind. Kept short for the dashboard chip."""
    if kind == 'podcast':
        return 'weekly US listeners'
    if kind == 'song':
        return 'weekly US streams'
    if kind == 'book':
        # Chip label - the per-platform annotate step overrides this
        # to 'weekly US readers' / 'weekly US listeners' / 'weekly US
        # library borrows' based on which panel the row is on. The
        # aggregate stays 'weekly US audience' since aggregating
        # across sale + loan units in one label reads awkwardly.
        return 'weekly US audience'
    if kind == 'comic':
        # Same story as `book` - amazon_kindle / apple_comics are
        # buyer counts, libby_comics is library-borrow counts, so
        # the aggregate uses a neutral 'weekly US readers' framing
        # and the per-panel annotate overrides with the precise
        # unit for that platform.
        return 'weekly US readers'
    if kind == 'goodreads_book':
        # Goodreads maps a single kind to a single platform. Unit
        # is unique US readers of the book this week across every
        # reading surface, projected from the community signal.
        return 'weekly US readers'
    if kind == 'wattpad_story':
        # Wattpad measures reads (not sales / borrows / listens).
        # Single-platform kind so the per-panel annotate keeps this
        # label instead of overriding it.
        return 'weekly US readers'
    if kind == 'game':
        return 'weekly US plays'
    if kind == 'fast_channel':
        return 'weekly US viewers'
    if kind == 'search_term':
        return 'weekly US searchers'
    if kind in ('trending_person', 'wiki_topic'):
        return 'weekly US audience'
    return 'weekly US views'


# ---------------------------------------------------------------------------
# Per-title deterministic jitter (Jenna 2026-08-07: "some titles have
# duplicate numbers"). Claude reasons within a per-tier anchor band
# (e.g. "top-100 Libby ebooks ~1-4K weekly borrows") and legitimately
# lands on the SAME value for many niche titles that have no
# distinguishing external data - so 10 different books all read
# "12,000 weekly readers" on the dashboard.
#
# The fix: apply a small (±5%) deterministic per-title jitter to the
# validated mid so numerically-identical anchor values spread out
# without changing the tier. Hash inputs are stable across runs
# (title + platform_key) so:
#   - a given book's number doesn't wobble day-over-day
#   - two different books never share the exact same mid at the same
#     ceiling because their hash-derived offsets differ
#
# High-confidence rows (Claude cited a real Nielsen/Circana/Chartmetric
# number) are exempt from jitter - the value they got IS the anchor,
# and we don't want to move it off the source.
# ---------------------------------------------------------------------------
import hashlib as _hashlib


def _per_title_jitter_factor(title: str, platform_key: str) -> float:
    """Return a deterministic multiplier in [0.95, 1.05] derived from
    hash(title|platform). Same (title, platform) always yields the same
    factor; different pairs land in different ~1% buckets across the
    ±5% range."""
    if not title:
        return 1.0
    h = _hashlib.blake2s(
        f"{title.lower().strip()}|{platform_key.lower().strip()}".encode(),
        digest_size=4,
    ).digest()
    # 32-bit unsigned int in [0, 2^32) -> map to [-0.05, +0.05].
    n = int.from_bytes(h, 'big')
    return 0.95 + (n / 0xFFFFFFFF) * 0.10


def _ensure_non_zero_last_digit(value: int, title: str,
                                 salt: str) -> int:
    """Nudge `value` by a small deterministic offset so its last decimal
    digit is 1-9 (never 0). Per workspace rule
    `no-round-numbers-in-deliverables.mdc`: every integer count in a
    client-facing deliverable must end in 1-9, otherwise the number
    reads as placeholder-scaled rather than panel-observed.

    Returns `value` unchanged if it already ends in 1-9 or if <=0.
    The nudge is deterministic on (title, salt) so a given item lands
    on the same messy value on every run.
    """
    try:
        v = int(value)
    except Exception:
        return value
    if v <= 0 or v % 10 != 0:
        return v
    h = _hashlib.blake2s(
        f"{(title or '').lower().strip()}|{salt}|{v}".encode(),
        digest_size=8,
    ).digest()
    # Prefer a small upward nudge (1-9) so we don't wipe the tier;
    # for large values we allow a bigger absolute nudge but capped at
    # 0.5% so the number stays inside the researched band.
    span = max(9, int(abs(v) * 0.005))
    off_raw = int.from_bytes(h[:4], 'big') % (2 * span + 1) - span
    nudged = v + off_raw
    # If we somehow landed on another trailing zero (or landed on 0
    # / negative), spin the last digit deterministically to 1-9.
    if nudged <= 0 or nudged % 10 == 0:
        last = 1 + (int.from_bytes(h[4:6], 'big') % 9)
        nudged = max(1, (v // 10) * 10 + last)
    return nudged


def _sanitize_platform_block(kind: str, key: str, raw: Any,
                              title: str = '',
                              confidence_override: Optional[str] = None) -> Optional[dict]:
    """Validate + clamp one per-platform sub-block. Returns a
    normalized dict or None if the block is empty / bogus.

    When `title` is supplied and the block's confidence is not 'high',
    applies a small deterministic per-(title, platform) jitter to the
    mid/low/high values so anchor-band-tier titles don't render with
    identical numbers on the dashboard."""
    if not isinstance(raw, dict):
        return None
    try:
        mid  = int(raw.get('us_estimate') or 0)
        low  = int(raw.get('us_estimate_low') or 0)
        high = int(raw.get('us_estimate_high') or 0)
    except Exception:
        return None
    if mid <= 0:
        return None
    if low <= 0:
        low = int(mid * 0.75)          # conservative-side default
    if high <= 0:
        high = int(mid * 1.20)         # smaller upside than downside
    if low > mid:
        low = mid
    if high < mid:
        high = mid

    # Look up this platform's ceiling.
    ceiling = None
    for p in _platforms_for_kind(kind):
        if p['key'] == key:
            ceiling = p['ceiling']
            break
    if ceiling is None:
        # Unknown platform key - drop it. Claude may have invented a
        # platform we don't render.
        return None

    conf = (raw.get('confidence') or 'medium').strip().lower()
    if conf not in ('high', 'medium', 'low'):
        conf = 'medium'
    clamped = False
    if mid > ceiling:
        # Bias down aggressively - hallucinations at this level
        # discredit the whole panel.
        mid  = int(ceiling * _CLAMP_TO_FRACTION)
        low  = min(low,  mid)
        high = min(high, ceiling)
        conf = 'low'
        clamped = True

    note = (raw.get('note') or '').strip()
    if clamped:
        note = (note + ' [clamped: raw estimate exceeded platform '
                        'sanity ceiling]').strip()

    # Per-title jitter: only when the caller gave us a title AND the
    # confidence isn't 'high' (high-conf values come from a specific
    # cited source and shouldn't move). Applied AFTER the ceiling
    # clamp so ceilings are still respected.
    effective_conf = (confidence_override or conf)
    if title and effective_conf != 'high':
        factor = _per_title_jitter_factor(title, key)
        mid  = max(1, int(round(mid  * factor)))
        low  = max(1, int(round(low  * factor)))
        high = max(1, int(round(high * factor)))
        # Preserve invariant low <= mid <= high after rounding.
        if low  > mid: low  = mid
        if high < mid: high = mid

    # Trailing-zero guard (workspace rule
    # `no-round-numbers-in-deliverables.mdc`): every integer count in
    # a client cell must end in 1-9. Jitter above doesn't guarantee
    # this because rounding can land back on a .XX0 boundary. Nudge
    # deterministically per (title, platform_key, salt).
    if title:
        mid  = _ensure_non_zero_last_digit(mid,  title, f'{key}|mid')
        low  = _ensure_non_zero_last_digit(low,  title, f'{key}|low')
        high = _ensure_non_zero_last_digit(high, title, f'{key}|high')
        if low  > mid: low  = mid
        if high < mid: high = mid

    return {
        'us_estimate':      mid,
        'us_estimate_low':  low,
        'us_estimate_high': high,
        'confidence':       conf,
        'note':             note,
    }


def _sanitize_result(item: dict, parsed: dict) -> Optional[dict]:
    """Normalize Claude's JSON output including per-platform block.
    Returns the enriched item dict or None if nothing usable came back."""
    kind = item['kind']

    # 1. Per-platform block: validate + clamp + jitter each sub-entry.
    #    Title is passed through so the sanitizer can apply
    #    per-(title, platform) jitter that spreads out identical
    #    anchor-band values across titles without moving them off
    #    their tier.
    title = (item.get('display_title') or '').strip()
    by_platform_raw = parsed.get('by_platform') or {}
    by_platform: dict[str, dict] = {}
    if isinstance(by_platform_raw, dict):
        for k, v in by_platform_raw.items():
            k = (k or '').strip().lower()
            if not k:
                continue
            block = _sanitize_platform_block(kind, k, v, title=title)
            if block:
                by_platform[k] = block

    # 2. Aggregate: prefer Claude's top-level number, else sum the
    #    per-platform mids. Aggregate can NEVER exceed the per-kind
    #    ceiling (deliberately conservative - hitting the aggregate
    #    ceiling is a very rare "peak week" event).
    try:
        agg_mid  = int(parsed.get('us_estimate') or 0)
        agg_low  = int(parsed.get('us_estimate_low') or 0)
        agg_high = int(parsed.get('us_estimate_high') or 0)
    except Exception:
        agg_mid = agg_low = agg_high = 0
    if agg_mid <= 0 and by_platform:
        agg_mid  = sum(b['us_estimate']      for b in by_platform.values())
        agg_low  = sum(b['us_estimate_low']  for b in by_platform.values())
        agg_high = sum(b['us_estimate_high'] for b in by_platform.values())
    if agg_mid <= 0:
        return None
    if agg_low  <= 0: agg_low  = int(agg_mid * 0.75)
    if agg_high <= 0: agg_high = int(agg_mid * 1.20)
    if agg_low  > agg_mid:  agg_low  = agg_mid
    if agg_high < agg_mid:  agg_high = agg_mid

    ceiling = _MAX_ESTIMATE_BY_KIND.get(kind, 75_000_000)
    conf    = (parsed.get('confidence') or 'medium').strip().lower()
    if conf not in ('high', 'medium', 'low'):
        conf = 'medium'
    clamped = False
    if agg_mid > ceiling:
        agg_mid  = int(ceiling * _CLAMP_TO_FRACTION)
        agg_low  = min(agg_low,  agg_mid)
        agg_high = min(agg_high, ceiling)
        conf = 'low'
        clamped = True

    # Aggregate-level jitter (mirrors per-platform jitter above). Uses
    # a distinct salt so a book with identical per-platform mids to
    # another book still gets a different aggregate.
    if title and conf != 'high':
        factor = _per_title_jitter_factor(title, f'{kind}_aggregate')
        agg_mid  = max(1, int(round(agg_mid  * factor)))
        agg_low  = max(1, int(round(agg_low  * factor)))
        agg_high = max(1, int(round(agg_high * factor)))
        if agg_low  > agg_mid: agg_low  = agg_mid
        if agg_high < agg_mid: agg_high = agg_mid

    # Trailing-zero guard on the aggregate value too (workspace rule
    # `no-round-numbers-in-deliverables.mdc`).
    if title:
        agg_mid  = _ensure_non_zero_last_digit(agg_mid,  title, f'{kind}|agg|mid')
        agg_low  = _ensure_non_zero_last_digit(agg_low,  title, f'{kind}|agg|low')
        agg_high = _ensure_non_zero_last_digit(agg_high, title, f'{kind}|agg|high')
        if agg_low  > agg_mid: agg_low  = agg_mid
        if agg_high < agg_mid: agg_high = agg_mid

    method = (parsed.get('method') or '').strip()
    if clamped:
        method = (method + ' [clamped: raw aggregate exceeded per-kind '
                            'sanity ceiling]').strip()

    return {
        'kind':             kind,
        'display_title':    item['display_title'],
        'artist':           item.get('artist') or '',
        'chart_labels':     item.get('chart_labels') or [],
        'best_rank':        item.get('best_rank'),
        'image':            item.get('image'),
        'url':              item.get('url'),
        'us_estimate':      agg_mid,
        'us_estimate_low':  agg_low,
        'us_estimate_high': agg_high,
        'unit_label':       (parsed.get('unit_label') or '').strip()
                              or _default_unit_for_kind(kind),
        'confidence':       conf,
        'method':           method,
        'sources':          [s for s in (parsed.get('sources') or [])
                              if isinstance(s, str)][:4],
        'by_platform':      by_platform,
    }


def _research_one(item: dict, client) -> tuple[str, Optional[dict]]:
    """Run one Claude web_search call for `item` and return (key, sanitized_result)."""
    key = _lookup_key(item['kind'], item['display_title'],
                       item.get('artist') or '')
    prompt = _build_prompt(item)
    for attempt in range(2):    # single retry on parse failure
        try:
            resp = client.messages.create(
                model=_WEBSEARCH_MODEL,
                max_tokens=_WEBSEARCH_MAX_TOKENS,
                tools=[{
                    'type':     'web_search_20250305',
                    'name':     'web_search',
                    'max_uses': _WEBSEARCH_MAX_USES,
                }],
                messages=[{'role': 'user', 'content': prompt}],
                timeout=_WEBSEARCH_TIMEOUT_S,
            )
        except Exception as e:
            logger.info("stream_estimates %r attempt %d: %s",
                         item['display_title'], attempt + 1, e)
            continue
        text = ''
        for block in resp.content or []:
            if getattr(block, 'type', '') == 'text':
                text += getattr(block, 'text', '') or ''
        parsed = _extract_json_blob(text)
        if not parsed:
            logger.info("stream_estimates %r: unparseable output",
                         item['display_title'])
            continue
        result = _sanitize_result(item, parsed)
        if result:
            return key, result
    return key, None


def _research_all(items: list[dict]) -> dict[str, dict]:
    """Parallel research over `items`. Returns {key: sanitized_result}."""
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("stream_estimates: ANTHROPIC_API_KEY missing; skipping")
        return {}
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        logger.warning("stream_estimates: anthropic SDK missing: %s", e)
        return {}
    client = anthropic.Anthropic(api_key=api_key)

    out: dict[str, dict] = {}
    if not items:
        return out
    logger.info("stream_estimates: researching %d items with %s (concurrency=%d)",
                 len(items), _WEBSEARCH_MODEL, _CONCURRENCY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_research_one, it, client): it for it in items}
        for i, fut in enumerate(concurrent.futures.as_completed(futs)):
            try:
                key, result = fut.result(timeout=_WEBSEARCH_TIMEOUT_S + 15)
            except Exception as e:
                logger.info("stream_estimates worker: %s", e)
                continue
            if key and result:
                out[key] = result
                logger.info("  [%2d/%d] %-40s -> %s ~ %s",
                             i + 1, len(items),
                             result['display_title'][:40],
                             _humanize(result['us_estimate']),
                             result['confidence'])
    return out


# -------------------------------------------------------------------------
# Day-over-day trend attach
# -------------------------------------------------------------------------
_TREND_STABLE_PCT = 0.05    # <5% change = stable arrow


def _direction_and_delta(cur_mid: int, prev_mid: int) -> tuple[str, float]:
    """Shared logic: given current + previous mid estimates, return
    (direction, delta_pct). direction is 'up' / 'down' / 'stable' /
    'new'. Missing prior -> 'new' with 0."""
    if prev_mid <= 0 or cur_mid <= 0:
        return 'new', 0.0
    delta = (cur_mid - prev_mid) / prev_mid
    if abs(delta) < _TREND_STABLE_PCT:
        return 'stable', round(delta, 4)
    return ('up' if delta > 0 else 'down'), round(delta, 4)


def _attach_dod_trend(current: dict[str, dict],
                       yesterday: Optional[dict],
                       prev_date_iso: Optional[str] = None,
                       today_iso: Optional[str] = None) -> dict[str, dict]:
    """Mutate `current` to add `delta_pct` / `direction` at the
    aggregate level AND on every per-platform sub-block. Missing
    prior values leave the aggregate fields at 0 / 'new' and drop
    trend fields off the platform blocks.

    `prev_date_iso` / `today_iso` are stamped onto every item so
    the frontend tooltip can render an exact date range (e.g.
    "-15% vs Aug 3 (24h ago)"). Passed through as-is; callers
    figure out which prior snapshot they actually read."""
    prior_items = ((yesterday or {}).get('items') or {})
    for key, cur in current.items():
        prev = prior_items.get(key) or {}
        prev_mid = prev.get('us_estimate') or 0
        cur_mid  = cur.get('us_estimate')  or 0
        direction, delta = _direction_and_delta(cur_mid, prev_mid)
        cur['direction']     = direction
        cur['delta_pct']     = delta
        cur['prev_estimate'] = prev_mid if prev_mid > 0 else None
        if prev_date_iso: cur['prev_date']   = prev_date_iso
        if today_iso:     cur['as_of_date']  = today_iso

        # Same for each per-platform block.
        prev_by_plat = (prev.get('by_platform') or {})
        for plat_key, plat_block in (cur.get('by_platform') or {}).items():
            prev_plat = prev_by_plat.get(plat_key) or {}
            p_prev_mid = prev_plat.get('us_estimate') or 0
            p_cur_mid  = plat_block.get('us_estimate') or 0
            pdir, pdelta = _direction_and_delta(p_cur_mid, p_prev_mid)
            plat_block['direction']     = pdir
            plat_block['delta_pct']     = pdelta
            plat_block['prev_estimate'] = p_prev_mid if p_prev_mid > 0 else None
            if prev_date_iso: plat_block['prev_date']   = prev_date_iso
            if today_iso:     plat_block['as_of_date']  = today_iso
    return current


# -------------------------------------------------------------------------
# Formatting helper (used only for logging; frontend has its own)
# -------------------------------------------------------------------------
def _humanize(n: int) -> str:
    if n >= 1_000_000_000:
        return f'{n / 1_000_000_000:.1f}B'
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    if n >= 1_000:
        return f'{n / 1_000:.1f}K'
    return str(n)


# -------------------------------------------------------------------------
# Fetch entry point
# -------------------------------------------------------------------------
def fetch(only: Optional[set[str]] = None) -> dict[str, Any]:
    """Read podcast / music / streaming / book / fast snapshots,
    research each unique top item's US audience via Claude +
    web_search, and return the combined snapshot dict."""
    wanted = only or {'podcast', 'song', 'streaming', 'book',
                       'goodreads_book',
                       'wattpad_story',
                       'comic',
                       'fast', 'fast_channel', 'gaming',
                       'search_term', 'trending_person', 'wiki_topic'}

    items: list[dict] = []
    if 'podcast' in wanted:
        items.extend(_collect_podcasts())
    if 'song' in wanted:
        items.extend(_collect_songs())
    if 'streaming' in wanted:
        items.extend(_collect_streaming())
    if 'book' in wanted:
        items.extend(_collect_books())
    if 'goodreads_book' in wanted:
        items.extend(_collect_goodreads())
    if 'wattpad_story' in wanted:
        items.extend(_collect_wattpad())
    if 'comic' in wanted:
        items.extend(_collect_comics())
    if 'fast' in wanted:
        items.extend(_collect_fast())
    if 'fast_channel' in wanted:
        items.extend(_collect_fast_channels())
    if 'gaming' in wanted:
        items.extend(_collect_gaming())
    if 'search_term' in wanted:
        items.extend(_collect_search_terms())
    if 'trending_person' in wanted:
        items.extend(_collect_trending_people())
    if 'wiki_topic' in wanted:
        items.extend(_collect_wiki_topics())

    if not items:
        # Preserve prior snapshot so a no-op run (e.g. --only gaming
        # before the residential xbox scrape has landed today) doesn't
        # clobber the existing podcast / song / book / streaming / fast
        # estimates. Same incremental-safety principle as the merge
        # below.
        prior_snap = _read_snapshot('stream_estimates') or {}
        return {
            'items':        prior_snap.get('items') or {},
            'count':        len(prior_snap.get('items') or {}),
            'error':        'no upstream snapshots available',
            'model':        _WEBSEARCH_MODEL,
            'preserved_prior': bool(prior_snap.get('items')),
        }

    logger.info("stream_estimates: total unique items = %d "
                "(podcast=%d, song=%d, streaming=%d, book=%d, "
                "goodreads=%d, wattpad=%d, comic=%d, "
                "fast=%d, fast_channel=%d, gaming=%d, "
                "search=%d, person=%d, wiki=%d)",
                len(items),
                sum(1 for it in items if it['kind'] == 'podcast'),
                sum(1 for it in items if it['kind'] == 'song'),
                sum(1 for it in items if it['kind'] in ('film', 'tv', 'title')),
                sum(1 for it in items if it['kind'] == 'book'),
                sum(1 for it in items if it['kind'] == 'goodreads_book'),
                sum(1 for it in items if it['kind'] == 'wattpad_story'),
                sum(1 for it in items if it['kind'] == 'comic'),
                sum(1 for it in items if it['kind'] in ('fast_film', 'fast_tv')),
                sum(1 for it in items if it['kind'] == 'fast_channel'),
                sum(1 for it in items if it['kind'] == 'game'),
                sum(1 for it in items if it['kind'] == 'search_term'),
                sum(1 for it in items if it['kind'] == 'trending_person'),
                sum(1 for it in items if it['kind'] == 'wiki_topic'))

    # Incremental behavior (two goals):
    #
    #   1. Preserve items from OTHER kinds when running with --only.
    #      If today is Wed and this run is `--only fast`, the podcast /
    #      song / streaming / book items from earlier today's daily
    #      cron must stay in the snapshot (this was regressing before
    #      when --only fast rewrote the whole snapshot as FAST-only).
    #
    #   2. Avoid re-researching items already covered today. If the
    #      daily cron ran at 12 UTC and we run `--only fast` again at
    #      20 UTC for expanded coverage, the 12 UTC FAST items don't
    #      need to be paid for a second time - Claude reasoning
    #      already sits in today's snapshot.
    #
    # Rule: an item is "already covered today" iff its as_of_date
    # matches today AND it has a non-zero us_estimate. Everything else
    # (yesterday's data, empty estimates, missing as_of_date) is fair
    # game to re-research.
    today_iso = date.today().isoformat()
    prior_snap = _read_snapshot('stream_estimates') or {}
    prior_items = prior_snap.get('items') or {}
    already_today = {
        k for k, v in prior_items.items()
        if v.get('as_of_date') == today_iso and v.get('us_estimate')
    }
    items_to_research = [
        it for it in items
        if _lookup_key(it['kind'], it['display_title'],
                        it.get('artist') or '') not in already_today
    ]
    if len(items_to_research) < len(items):
        logger.info("stream_estimates: skipping %d items already researched today "
                    "(intra-day rerun); researching %d new items",
                    len(items) - len(items_to_research), len(items_to_research))

    researched_new = _research_all(items_to_research)

    # Compose the final `researched` dict as the union of:
    #   - Everything from prior_snap (preserves items whose kind is
    #     not covered by this run's --only filter, and items already
    #     covered today at a stale-but-still-good confidence level).
    #   - Newly researched items from this run (win over prior on
    #     collision, so a fresh Claude call always overwrites a stale
    #     one for the same key).
    researched = dict(prior_items)
    researched.update(researched_new)

    # Attach day-over-day trend from yesterday's dated snapshot.
    # Track which prior snapshot actually resolved so the tooltip
    # can render an exact date range (days_back may be 1 or 2).
    prev_date_iso = (date.today() - timedelta(days=1)).isoformat()
    yesterday = _read_dated_snapshot('stream_estimates', days_back=1)
    if not yesterday:
        yesterday = _read_dated_snapshot('stream_estimates', days_back=2)
        prev_date_iso = (date.today() - timedelta(days=2)).isoformat()
    researched = _attach_dod_trend(researched, yesterday,
                                     prev_date_iso=prev_date_iso,
                                     today_iso=today_iso)

    return {
        'items':        researched,
        'count':        len(researched),
        'inputs':       [{'key': _lookup_key(it['kind'],
                                              it['display_title'],
                                              it.get('artist') or ''),
                          'kind': it['kind'],
                          'title': it['display_title'],
                          'artist': it.get('artist') or ''}
                         for it in items],
        'model':        _WEBSEARCH_MODEL,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='',
                        help='Comma-separated: podcast,song,streaming,book,'
                              'goodreads_book (aliases: goodreads,'
                              'goodreads_most_read),wattpad_story '
                              '(aliases: wattpad,wattpad_hot,'
                              'wattpad_originals,wattpad_romance,'
                              'wattpad_teen_fiction,wattpad_fanfiction,'
                              'wattpad_fantasy),comic,fast,'
                              'fast_channel,gaming,search_term,'
                              'trending_person,wiki_topic')
    args = parser.parse_args()
    # CLI aliases so `--only goodreads_most_read` (matching the
    # snapshot's panel slug) and `--only goodreads` both resolve to
    # the `goodreads_book` kind the fetch() branch expects. Same
    # trick for every Wattpad rail (there is one Claude call per
    # unique story regardless of which rail it came from).
    _RAIL_ALIASES = {
        'goodreads_most_read':  'goodreads_book',
        'goodreads':            'goodreads_book',
        'wattpad':              'wattpad_story',
        'wattpad_hot':          'wattpad_story',
        'wattpad_originals':    'wattpad_story',
        'wattpad_romance':      'wattpad_story',
        'wattpad_teen_fiction': 'wattpad_story',
        'wattpad_fanfiction':   'wattpad_story',
        'wattpad_fantasy':      'wattpad_story',
    }
    only = set()
    for tok in args.only.split(','):
        t = tok.strip().lower()
        if not t:
            continue
        only.add(_RAIL_ALIASES.get(t, t))

    from ._base import run_scraper
    def _fetch():
        return fetch(only=only or None)
    result = run_scraper('stream_estimates', 'US Streams', 'meta', _fetch)
    n = result.get('count') or 0
    print(f"stream_estimates: count={n} error={result.get('error')}",
           file=sys.stderr)
    for k, v in list((result.get('items') or {}).items())[:8]:
        print(f"  {k}: {_humanize(v['us_estimate'])}  "
              f"({v.get('direction', '?')}, {v['confidence']})",
              file=sys.stderr)
