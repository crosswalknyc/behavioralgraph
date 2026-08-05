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
#   podcasts:  ~10 platforms x top 8-10 unique  = 60
#   songs:     5 music panels x top 20 unique  = 100
#   streaming: 6 platforms x top 25 unique     = 200 (bumped 2026-08-05
#              from 60 - was leaving Disney+ / Hulu with only ~5 estimates
#              each because the sort-by-best_rank cap culled everything
#              below rank 10 across platforms).
#   books:     6 book+libby panels x top 15   = 100
# -------------------------------------------------------------------------
_MAX_PODCAST_ITEMS   = 60
_MAX_SONG_ITEMS      = 100
_MAX_STREAMING_ITEMS = 200
_MAX_BOOK_ITEMS      = 100

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
        for i, it in enumerate((panel.get('items') or [])[:30]):
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
        for i, it in enumerate((panel.get('items') or [])[:30]):
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
        # Bumped 2026-08-05 from 15 to 30 per bucket so the dashboard's
        # top-20 films + top-20 tv per platform is fully covered with
        # estimates (previously the row below rank 15 rendered without
        # a stream badge, most visibly on Disney+ where the whole TV
        # rail below rank 5 was blank).
        for kind, items in buckets:
            for i, it in enumerate(items[:30]):
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
    for src_slug, panel in (book_snap.get('sources') or {}).items():
        for i, it in enumerate((panel.get('items') or [])[:30]):
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

    # 2. Libby (LA County) - ebook + audiobook. Fold onto existing
    #    items when the title+author match; create standalone items
    #    when they don't.
    for src_slug in ('ebook', 'audiobook'):
        panel = (libby_snap.get('sources') or {}).get(src_slug) or {}
        panel_label = panel.get('label') or f'Libby: Popular {src_slug.title()}s'
        # Panel-label -> chart-label prefix we render on the dashboard
        chart_prefix = ('Libby: Popular eBooks' if src_slug == 'ebook'
                         else 'Libby: Popular Audiobooks')
        for i, it in enumerate((panel.get('items') or [])[:30]):
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
            # borrows). Map ebook->libby_ebook / audiobook->libby_audio.
            plat = 'libby_ebook' if src_slug == 'ebook' else 'libby_audio'
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
]


def _platforms_for_kind(kind: str) -> list[dict]:
    if kind == 'song':
        return _SONG_PLATFORMS
    if kind == 'podcast':
        return _PODCAST_PLATFORMS
    if kind in ('film', 'tv', 'title'):
        return _STREAMING_PLATFORMS_META
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
    return 'weekly US views'


def _sanitize_platform_block(kind: str, key: str, raw: Any) -> Optional[dict]:
    """Validate + clamp one per-platform sub-block. Returns a
    normalized dict or None if the block is empty / bogus."""
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

    # 1. Per-platform block: validate + clamp each sub-entry.
    by_platform_raw = parsed.get('by_platform') or {}
    by_platform: dict[str, dict] = {}
    if isinstance(by_platform_raw, dict):
        for k, v in by_platform_raw.items():
            k = (k or '').strip().lower()
            if not k:
                continue
            block = _sanitize_platform_block(kind, k, v)
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
    """Read podcast / music / streaming / book snapshots, research
    each unique top item's US audience via Claude + web_search, and
    return the combined snapshot dict."""
    wanted = only or {'podcast', 'song', 'streaming', 'book'}

    items: list[dict] = []
    if 'podcast' in wanted:
        items.extend(_collect_podcasts())
    if 'song' in wanted:
        items.extend(_collect_songs())
    if 'streaming' in wanted:
        items.extend(_collect_streaming())
    if 'book' in wanted:
        items.extend(_collect_books())

    if not items:
        return {
            'items': {},
            'count': 0,
            'error': 'no upstream snapshots available',
            'model': _WEBSEARCH_MODEL,
        }

    logger.info("stream_estimates: total unique items = %d "
                "(podcast=%d, song=%d, streaming=%d, book=%d)",
                len(items),
                sum(1 for it in items if it['kind'] == 'podcast'),
                sum(1 for it in items if it['kind'] == 'song'),
                sum(1 for it in items if it['kind'] in ('film', 'tv', 'title')),
                sum(1 for it in items if it['kind'] == 'book'))

    researched = _research_all(items)

    # Attach day-over-day trend from yesterday's dated snapshot.
    # Track which prior snapshot actually resolved so the tooltip
    # can render an exact date range (days_back may be 1 or 2).
    today_iso = date.today().isoformat()
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
                        help='Comma-separated: podcast,song,streaming')
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
