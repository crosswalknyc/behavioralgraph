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
# FAST-channels: 4 platforms x top 100 = 400 gross, ~250-300 after
# cross-platform dedup (Alone / Everybody Loves Raymond / etc. appear
# on 2-3 platforms). Cap at 350 for safety headroom on days there is
# little cross-platform overlap. ~$6-7/day added to the daily Claude
# spend at full 100-row coverage.
_MAX_FAST_ITEMS      = 350
# Gaming: 1 platform (Xbox Game Pass Ultimate) x top 25 = 25 gross,
# no cross-platform dedup needed today. Room to grow when we add
# PlayStation Plus / Nintendo Switch Online / Steam later without
# changing the cap.
_MAX_GAMING_ITEMS    = 30

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


# Gaming platforms. Snapshot layout mirrors streaming's one-file-per-
# platform pattern (fast_channels uses one merged file; gaming uses
# one per platform because each backend scraper is bespoke - Xbox
# hydrates via DisplayCatalog v7, PS Plus would hit a totally
# different endpoint). Chart labels feed `_CHART_LABEL_TO_PLATFORM`.
_GAMING_SLUGS = (
    ('xbox_gamepass', 'Xbox Game Pass Ultimate'),
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
    """Union top games across every gaming-platform snapshot (currently
    just xbox_gamepass), keyed by `game:<norm_title>`. Games don't
    collide by title the way songs do (there's only one 'Baldur's
    Gate 3'), so no artist qualifier in the key. Publisher rides
    along on `artist` for prompt context only."""
    per: dict[str, dict] = {}
    for slug, label in _GAMING_SLUGS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
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
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


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


def _lookup_key(kind: str, display_title: str, artist: str = '') -> str:
    """Storage key for an item. Podcasts/streaming key by title;
    songs key by (title + artist) because titles collide across
    artists."""
    if kind == 'song':
        return f'song:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'book':
        return f'book:{_cp_normalize(f"{display_title} {artist}")}'
    if kind == 'game':
        # Games don't collide by title (no two AAA releases share a
        # name in the same window). Publisher rides along on `artist`
        # for prompt context but isn't part of the key.
        return f'game:{_cp_normalize(display_title)}'
    if kind in ('film', 'tv', 'title'):
        return f'{kind}:{_cp_normalize(display_title)}'
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
    if kind == 'game':
        return _GAMING_PLATFORMS_META
    if kind == 'book':
        return _BOOK_PLATFORMS
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
    # Books - most specific first so 'libby: popular ebooks' isn't
    # eaten by the shorter 'libby' prefix.
    ('libby: popular ebooks',     'libby_ebook'),
    ('libby: popular audiobooks', 'libby_audio'),
    ('libby popular ebooks',      'libby_ebook'),
    ('libby popular audiobooks',  'libby_audio'),
    ('apple books',       'apple'),
    ('amazon best-sellers (books)', 'amazon'),
    ('audible best-sellers',  'audible'),

    # Podcast / music (order matters - longer / more-specific first)
    ('spotify podcast',  'spotify'),   # podcast panel - Spotify Podcast Charts (US)
    ('spotify',          'spotify'),   # Spotify Daily Top 200 (US), Spotify Podcast Charts (US)
    ('apple podcasts',   'apple'),
    ('apple music',      'apple'),
    ('apple',            'apple'),
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
        # "Weekly US plays" = unique US Game Pass Ultimate subs that
        # LAUNCHED the title (console, PC, cloud) in the past 7 days.
        # Anchor Claude to Microsoft first-party engagement, Newzoo
        # Cloud Gaming Insights, Circana US Games Tracker, and any
        # Xbox Wire / GamesIndustry.biz weekly-concurrency data.
        unit  = 'weekly US plays (unique US subscribers who launched the game in the past 7 days)'
        query = (f'"{display_title}" Xbox Game Pass weekly US players '
                 f'Newzoo Circana Ampere Analysis 2026')
        pub_str = f'\nPUBLISHER: {artist}' if artist else ''
        item_line = (f'GAME TITLE: {display_title}{pub_str}\n'
                     f'PLATFORM: Xbox Game Pass Ultimate (~25M US subs, '
                     f'includes console + PC + Xbox Cloud Gaming)')
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
    else:
        unit  = 'weekly US views'
        query = f'"{display_title}" weekly viewers US streaming 2026'
        item_line = f'TITLE: {display_title}'

    platforms = _platforms_for_kind(kind)
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
    if kind == 'game':
        return 'weekly US plays'
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
    wanted = only or {'podcast', 'song', 'streaming', 'book', 'fast', 'gaming'}

    items: list[dict] = []
    if 'podcast' in wanted:
        items.extend(_collect_podcasts())
    if 'song' in wanted:
        items.extend(_collect_songs())
    if 'streaming' in wanted:
        items.extend(_collect_streaming())
    if 'book' in wanted:
        items.extend(_collect_books())
    if 'fast' in wanted:
        items.extend(_collect_fast())
    if 'gaming' in wanted:
        items.extend(_collect_gaming())

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
                "(podcast=%d, song=%d, streaming=%d, book=%d, fast=%d, "
                "gaming=%d)",
                len(items),
                sum(1 for it in items if it['kind'] == 'podcast'),
                sum(1 for it in items if it['kind'] == 'song'),
                sum(1 for it in items if it['kind'] in ('film', 'tv', 'title')),
                sum(1 for it in items if it['kind'] == 'book'),
                sum(1 for it in items if it['kind'] in ('fast_film', 'fast_tv')),
                sum(1 for it in items if it['kind'] == 'game'))

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
                        help='Comma-separated: podcast,song,streaming,book,fast,gaming')
    args = parser.parse_args()
    only = set()
    for tok in args.only.split(','):
        t = tok.strip().lower()
        if t:
            only.add(t)

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
