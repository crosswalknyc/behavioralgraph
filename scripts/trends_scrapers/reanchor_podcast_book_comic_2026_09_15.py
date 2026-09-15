#!/usr/bin/env python3
"""Re-anchor the podcast / book / comic per-platform levels to published
US figures, in place, across the dated estimate history.

WHY
---
Two defects put per-platform numbers on these three families that no
published figure supports.

1. A cross-platform total was being handed to a single platform. The
   Apple Podcasts guidance quoted Podtrac's all-app weekly figure
   ("#1 typically 5-8M weekly US listeners") and described Apple as
   "~45-55% of total US podcast listenership". Edison Podcast Metrics
   Q1 2026 puts Apple at 12% of US weekly podcast consumers, Spotify
   at 21% and YouTube at 37%. Apple was over-weighted about four-fold
   and YouTube under-weighted about three-fold.

2. Rows with no per-platform block fell back to the item's
   cross-platform aggregate and rendered it as if it were that one
   platform's audience. Because an aggregate is always at least as
   large as any single platform, those rows sorted to the top of the
   list: a Netflix podcast row reading 1,176,426, an Audible row
   reading 991,865, an Apple Books row carrying an Audible audiobook
   number.

WHAT THIS DOES
--------------
Podcasts: re-apportions `by_platform` across the six rendered apps
using the published Edison shares, holding each item's cross-platform
aggregate. Apple comes down, YouTube goes up, the aggregate is
untouched. The rendered apps sum to 75.5% of the aggregate; the
remainder sits on apps with no panel (iHeart, Pandora, Pocket Casts
and the rest of Edison's "other" bucket).

Books and comics: a buyer on Amazon is not a share of the same
audience as a borrower on Libby, so these are not apportioned off an
aggregate. Rows missing a per-platform block get one derived from the
published weekly band for that panel at that chart rank.

Every write keeps the standing invariants: natural last-digit
distribution via `_natural_last_digits`, no placeholder literals, no
value equal to the same item's adjacent day, and deterministic
per-(title, platform) jitter so no two rows pin together.

USAGE
-----
    python3 reanchor_podcast_book_comic_2026_09_15.py --dry-run
    python3 reanchor_podcast_book_comic_2026_09_15.py --days 60
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from scripts.trends_scrapers import stream_estimates as _se   # noqa: E402
from scripts.trends_scrapers.stream_estimates import (   # noqa: E402
    _natural_last_digits,
    _per_title_jitter_factor,
)


def _daily_caps() -> dict:
    """{platform key: daily cap} from the module's own weekly ceilings.

    The data pass enforces exactly the cap the estimator now enforces,
    so today's corrected board and tomorrow's native run agree instead
    of drifting apart for a day.
    """
    out = {}
    for attr in dir(_se):
        if 'PLATFORM' not in attr:
            continue
        val = getattr(_se, attr)
        if not isinstance(val, list):
            continue
        for p in val:
            if isinstance(p, dict) and 'key' in p and 'ceiling' in p:
                out[p['key']] = max(1, int(p['ceiling'] / 7))
    return out


DAILY_CAP = _daily_caps()

BUCKET = 'dashboard-inputs'
LATEST_KEY = 'trends_iq_snapshots/latest/stream_estimates.json'
DATED_KEY = 'trends_iq_snapshots/{d}/stream_estimates.json'
BACKUP_KEY = 'trends_iq_snapshots/_backups/{d}.stream_estimates.pre_reanchor.json'

# ------------------------------------------------------------------
# Published platform shares.
#
# Podcasts: Edison Podcast Metrics Q1 2026, "service used most",
# US weekly podcast consumers 13+ (YouTube 37, Spotify 21, Apple 12,
# other 30). The 30% other bucket is not broken out publicly; the
# Amazon / Audible / Netflix slices below are the most defensible
# proxy available for the three apps we render out of that bucket and
# are deliberately small.
# ------------------------------------------------------------------
PODCAST_SHARE = {
    'youtube_podcasts': 0.370,
    'spotify':          0.210,
    'apple':            0.120,
    'amazon':           0.030,
    'audible':          0.015,
    'netflix':          0.005,
}

# The share the retired guidance implied, which is what the stored
# values were built on: Apple "~45-55% of total US podcast
# listenership", Spotify "~25-35%", YouTube "~31% of monthly listeners
# use it as their primary surface", Amazon "<10%", Audible small,
# Netflix a new 2026 format.
PODCAST_SHARE_RETIRED = {
    'youtube_podcasts': 0.310,
    'spotify':          0.300,
    'apple':            0.500,
    'amazon':           0.080,
    'audible':          0.030,
    'netflix':          0.020,
}

# Correcting the prior rather than the row. A show's own platform mix
# carries real signal - Joe Rogan lives on Spotify and YouTube, NPR
# lives on Apple, Kill Tony is YouTube-native - and a flat market
# share would erase it. So each platform is moved by the ratio between
# the published share and the retired share it was built on. That
# fixes the systematic tilt and leaves the show-level skew intact.
PODCAST_PRIOR_CORRECTION = {
    k: PODCAST_SHARE[k] / PODCAST_SHARE_RETIRED[k] for k in PODCAST_SHARE
}

# Published weekly US band for the #1 slot on each book / comic panel,
# and the rank decay applied down the chart. Bands come from Circana
# BookScan weekly print units (observed #1 40-56K, #10 16-26K),
# retailer share of the US trade market, OverDrive US public-library
# circulation, and Comichron / ICv2 monthly comic units.
PANEL_TOP_WEEKLY = {
    'amazon':         (20_000, 28_000),   # Circana #1 x Amazon ~50%
    'apple':          (1_000, 1_400),     # 10% of ebook x 25% ebook share
    'audible':        (2_400, 3_360),     # 60% of audiobook x 10% share
    'libby_ebook':    (15_000, 40_000),   # OverDrive ~11.5M weekly loans
    'libby_audio':    (8_000, 25_000),    # OverDrive ~2.7M weekly loans
    'libby_magazine': (5_000, 15_000),    # OverDrive ~0.8-1.15M weekly
    'amazon_kindle':  (4_000, 12_000),    # top GN x Amazon ~40% of GN units
    'apple_comics':   (1_500, 4_000),     # US digital comics x Apple ~12%
    'libby_comics':   (4_000, 12_000),    # ~1.7-2.3M weekly comics borrows
}

# Rank decay, expressed as a fraction of the #1 band. Mirrors the
# observed Circana ladder (#1 55K, #2-5 24-42K, #6-10 15-26K, ...).
RANK_DECAY = [
    (1, 1, 1.00), (2, 5, 0.66), (6, 10, 0.42), (11, 25, 0.24),
    (26, 50, 0.13), (51, 100, 0.06), (101, 10_000, 0.022),
]

# Panel membership is read from the same chart snapshots the view
# reads, and resolved with the same key normalizer, so the backfill
# lands on exactly the rows that would otherwise fall back. The
# estimator's own `chart_labels` are NOT sufficient: an item can
# render on a panel whose label it never recorded (Shameless carries
# only a Netflix label yet renders on the BritBox panel).
CHART_SOURCES = [
    # (snapshot source, kind, {panel slug: platform key})
    ('podcast_charts', 'podcast', {
        'apple': 'apple', 'spotify': 'spotify',
        'youtube_podcasts': 'youtube_podcasts', 'netflix': 'netflix',
        'amazon': 'amazon', 'audible': 'audible'}),
    ('book_charts', 'book', {
        'amazon': 'amazon', 'apple': 'apple', 'audible': 'audible'}),
    ('libby_trends', 'book', {
        'ebook': 'libby_ebook', 'audiobook': 'libby_audio',
        'magazine': 'libby_magazine'}),
    ('comics_charts', 'comic', {
        'amazon_kindle': 'amazon_kindle', 'apple_comics': 'apple_comics',
        'libby_comics': 'libby_comics'}),
]

# Titles keyed on title alone for podcasts, title + artist for books
# and comics (matches `_annotate_*_with_streams`).
KEY_USES_ARTIST = {'podcast': False, 'book': True, 'comic': True}

# chart_label prefix -> platform key. Both label vocabularies the
# estimator emits are covered. Retained as a secondary signal for
# rank when the chart snapshot for a past day is unavailable.
LABEL_TO_PLATFORM = {
    'podcasts_trending.apple':            ('podcast', 'apple'),
    'podcasts_trending.spotify':          ('podcast', 'spotify'),
    'podcasts_trending.youtube_podcasts': ('podcast', 'youtube_podcasts'),
    'podcasts_trending.amazon':           ('podcast', 'amazon'),
    'podcasts_trending.audible':          ('podcast', 'audible'),
    'podcasts_trending.netflix':          ('podcast', 'netflix'),
    'Apple Podcasts Top 100 (US)':        ('podcast', 'apple'),
    'Spotify Podcast Charts (US)':        ('podcast', 'spotify'),
    'YouTube Popular Podcasts (US)':      ('podcast', 'youtube_podcasts'),
    'Amazon Music Podcasts (US)':         ('podcast', 'amazon'),
    'Audible Podcasts (US)':              ('podcast', 'audible'),
    'Netflix video podcasts':             ('podcast', 'netflix'),
    'books_trending.amazon':              ('book', 'amazon'),
    'books_trending.apple':               ('book', 'apple'),
    'books_trending.audible':             ('book', 'audible'),
    'Amazon Books':                       ('book', 'amazon'),
    'Apple Books':                        ('book', 'apple'),
    'Audible Books':                      ('book', 'audible'),
    'libby_trending.ebook':               ('book', 'libby_ebook'),
    'libby_trending.audiobook':           ('book', 'libby_audio'),
    'libby_trending.magazine':            ('book', 'libby_magazine'),
    'Libby: Popular eBooks':              ('book', 'libby_ebook'),
    'Libby: Popular Audiobooks':          ('book', 'libby_audio'),
    'comics_trending.amazon_kindle':      ('comic', 'amazon_kindle'),
    'comics_trending.apple_comics':       ('comic', 'apple_comics'),
    'comics_trending.libby_comics':       ('comic', 'libby_comics'),
    'Amazon Comics':                      ('comic', 'amazon_kindle'),
    'Apple Books Comics':                 ('comic', 'apple_comics'),
    'Libby Comics':                       ('comic', 'libby_comics'),
}

NOTE_PODCAST = ('Apportioned to this platform by its published share of '
                'US weekly podcast listening.')
NOTE_PANEL = ('Levelled to the published weekly US band for this list at '
              'this chart position.')


def load_stopwords() -> frozenset:
    """Pull `_CP_STOPWORDS` out of trends_iq without importing it, so
    the key normalizer here stays a true twin and this script never
    drags the view module's import side effects into a data job."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'trends_iq.py')
    try:
        src = open(path, encoding='utf-8').read()
        m = re.search(r'^_CP_STOPWORDS\s*=\s*(frozenset\()?\{(.*?)\}',
                      src, re.S | re.M)
        if not m:
            return frozenset()
        return frozenset(re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(2))
                         and [a or b for a, b in
                              re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(2))])
    except Exception:
        return frozenset()


def _decay(rank: int) -> float:
    for lo, hi, f in RANK_DECAY:
        if lo <= rank <= hi:
            return f
    return RANK_DECAY[-1][2]


_CP_STOPWORDS_RE = re.compile(r'[^\w\s]+')


def cp_normalize(text: str, stopwords: frozenset) -> str:
    """Byte-for-byte twin of `trends_iq._cp_normalize`."""
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = _CP_STOPWORDS_RE.sub(' ', s)
    return ' '.join(t for t in s.split() if t and t not in stopwords)


def build_membership(s3, day: str, stopwords: frozenset) -> dict:
    """{item_key: {platform_key: rank}} from the day's chart snapshots.

    Mirrors how `_annotate_podcasts_with_streams`,
    `_annotate_books_with_streams` and `_annotate_comics_with_streams`
    resolve a chart row to an estimate entry.
    """
    out: dict[str, dict[str, int]] = {}
    for source, kind, slug_map in CHART_SOURCES:
        # Union the dated read with `latest/`. A chart can refresh
        # between the dated stamp and the moment the view fetched it,
        # and a row the view renders from the fresher list still needs
        # a per-platform block. Backfilling a row that turns out not
        # to render costs nothing.
        snaps = []
        for key in (f'trends_iq_snapshots/{day}/{source}.json',
                    f'trends_iq_snapshots/latest/{source}.json'):
            try:
                snaps.append(json.loads(
                    s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()))
            except Exception:
                continue
        panels = {}
        for snap in snaps:
            if not isinstance(snap, dict):
                continue
            node = snap.get('sources')
            if not isinstance(node, dict):
                node = snap
            for slug, panel in node.items():
                if isinstance(panel, dict) and isinstance(panel.get('items'), list):
                    panels.setdefault(slug, {'items': []})
                    panels[slug]['items'].extend(panel['items'])
        for slug, panel in panels.items():
            pkey = slug_map.get(slug)
            if not pkey or not isinstance(panel, dict):
                continue
            for i, row in enumerate(panel.get('items') or [], start=1):
                if not isinstance(row, dict):
                    continue
                title = (row.get('title') or '').strip()
                if not title:
                    continue
                if KEY_USES_ARTIST[kind]:
                    artist = (row.get('artist') or '').strip()
                    norm = cp_normalize(f'{title} {artist}', stopwords)
                else:
                    norm = cp_normalize(title, stopwords)
                ik = f'{kind}:{norm}'
                rank = row.get('rank')
                rank = rank if isinstance(rank, int) and rank > 0 else i
                slot = out.setdefault(ik, {})
                if pkey not in slot or rank < slot[pkey]:
                    slot[pkey] = rank
    return out


def _panel_memberships(entry: dict) -> dict:
    """{platform_key: rank} for every panel this item charts on,
    from the entry's own chart labels. Secondary to the chart
    snapshots above."""
    out = {}
    for label in (entry.get('chart_labels') or []):
        if not isinstance(label, str):
            continue
        rank = None
        if '#' in label:
            head, _, tail = label.rpartition('#')
            try:
                rank = int(tail.strip())
            except Exception:
                rank = None
            label_head = head.strip().rstrip('.').strip()
        else:
            label_head = label.strip()
        for prefix, (_kind, pkey) in LABEL_TO_PLATFORM.items():
            if label_head == prefix or label_head.startswith(prefix + '.') \
                    or label_head.startswith(prefix):
                r = rank if isinstance(rank, int) and rank > 0 else 999
                if pkey not in out or r < out[pkey]:
                    out[pkey] = r
                break
    return out


def _block(mid: int, title: str, pkey: str, confidence: str, note: str) -> dict:
    """Build one per-platform block with the house invariants applied."""
    cap = DAILY_CAP.get(pkey)
    if cap and mid > cap:
        mid = cap * 0.92          # same bias-down posture as the estimator
        confidence = 'low'
    mid = max(1, int(round(mid * _per_title_jitter_factor(title, pkey))))
    mid = _natural_last_digits(mid, title, f'{pkey}|mid')
    low = _natural_last_digits(max(1, int(mid * 0.78)), title, f'{pkey}|low')
    high = _natural_last_digits(max(mid + 1, int(mid * 1.22)), title, f'{pkey}|high')
    if low > mid:
        low = mid
    if high < mid:
        high = mid
    return {'us_estimate': mid, 'us_estimate_low': low,
            'us_estimate_high': high, 'confidence': confidence, 'note': note}


def reanchor_entry(entry: dict, chart_members: dict | None = None) -> tuple[bool, dict]:
    """Return (changed, stats) after rewriting `entry['by_platform']`."""
    kind = entry.get('kind')
    if kind not in ('podcast', 'book', 'comic'):
        return False, {}
    title = entry.get('display_title') or ''
    members = dict(_panel_memberships(entry))
    for pkey, rank in (chart_members or {}).items():
        if pkey not in members or rank < members[pkey]:
            members[pkey] = rank
    bp = entry.get('by_platform')
    if not isinstance(bp, dict):
        bp = {}
    stats = {'reapportioned': 0, 'backfilled': 0}

    if kind == 'podcast':
        agg = entry.get('us_estimate')
        if not isinstance(agg, (int, float)) or agg <= 0:
            return False, {}
        targets = set(members) | {k for k in bp if k in PODCAST_SHARE}
        new_bp = {}
        for pkey in targets:
            share = PODCAST_SHARE.get(pkey)
            if share is None:
                continue
            prev = bp.get(pkey) if isinstance(bp.get(pkey), dict) else None
            prev_mid = (prev or {}).get('us_estimate')
            conf = (prev or {}).get('confidence') or 'medium'
            if conf == 'high':
                conf = 'medium'      # the old level was not a cited daily figure
            if isinstance(prev_mid, (int, float)) and prev_mid > 0:
                # Move the row by the ratio between the published share
                # and the retired share it was built on, so this show's
                # own platform skew survives the correction.
                target = prev_mid * PODCAST_PRIOR_CORRECTION[pkey]
                stats['reapportioned'] += 1
            else:
                # No prior reasoning for this platform, so the market
                # share is the only defensible basis.
                target = agg * share
                stats['backfilled'] += 1
            new_bp[pkey] = _block(target, title, pkey, conf, NOTE_PODCAST)
        for pkey, blk in bp.items():
            if pkey not in new_bp:
                new_bp[pkey] = blk
        entry['by_platform'] = new_bp
        return bool(new_bp), stats

    # book / comic: only fill the gaps, off the published panel band.
    changed = False
    for pkey, rank in members.items():
        band = PANEL_TOP_WEEKLY.get(pkey)
        if not band:
            continue
        existing = bp.get(pkey)
        if isinstance(existing, dict) and (existing.get('us_estimate') or 0) > 0:
            continue
        f = _decay(rank)
        weekly = (band[0] + band[1]) / 2.0 * f
        bp[pkey] = _block(weekly / 7.0, title, pkey, 'low', NOTE_PANEL)
        stats['backfilled'] += 1
        changed = True
    if changed:
        entry['by_platform'] = bp
    return changed, stats


_PLACEHOLDER_LITERALS = {2001, 12345, 54321, 99999, 88888, 77777, 22222,
                         123456, 654321}


def _sweep_placeholder_values(items: dict) -> int:
    """Standing rule `no-round-numbers-in-deliverables`: no count may be
    a placeholder literal, divisible by 10,000, or a sub-million
    multiple of 1,000. Swept across every kind, not just the three
    families, because the rule is board-wide.
    """
    fixed = 0
    for key, entry in items.items():
        title = entry.get('display_title') or key
        for pkey, blk in (entry.get('by_platform') or {}).items():
            if not isinstance(blk, dict):
                continue
            v = blk.get('us_estimate')
            if not isinstance(v, int) or v <= 0:
                continue
            if v in _PLACEHOLDER_LITERALS or v % 10_000 == 0 or \
                    (v % 1_000 == 0 and v < 1_000_000):
                blk['us_estimate'] = _natural_last_digits(
                    v + 1, title, f'{pkey}|mid|sweep')
                fixed += 1
    return fixed


def _break_adjacent_collisions(items: dict, prev_items: dict) -> int:
    """No value may equal the same item's value on the adjacent day."""
    fixed = 0
    for key, entry in items.items():
        prev = prev_items.get(key)
        if not isinstance(prev, dict):
            continue
        pbp = prev.get('by_platform') or {}
        for pkey, blk in (entry.get('by_platform') or {}).items():
            pblk = pbp.get(pkey)
            if not isinstance(pblk, dict) or not isinstance(blk, dict):
                continue
            if blk.get('us_estimate') and \
                    blk['us_estimate'] == pblk.get('us_estimate'):
                title = entry.get('display_title') or key
                blk['us_estimate'] = _natural_last_digits(
                    blk['us_estimate'] + 1, title, f'{pkey}|mid|adj')
                fixed += 1
    return fixed


def process_day(s3, day_key: str, label: str, prev_items: dict | None,
                dry_run: bool, membership: dict | None = None) -> dict | None:
    membership = membership or {}
    try:
        body = s3.get_object(Bucket=BUCKET, Key=day_key)['Body'].read()
    except Exception as e:
        print(f'  {label}: unreadable ({type(e).__name__}), skipped')
        return None
    snap = json.loads(body)
    items = snap.get('items')
    if not isinstance(items, dict):
        print(f'  {label}: no items, skipped')
        return None

    tot = {'reapportioned': 0, 'backfilled': 0, 'entries': 0}
    for ikey, entry in items.items():
        changed, st = reanchor_entry(entry, membership.get(ikey))
        if changed:
            tot['entries'] += 1
            tot['reapportioned'] += st.get('reapportioned', 0)
            tot['backfilled'] += st.get('backfilled', 0)

    swept = _sweep_placeholder_values(items)
    coll = _break_adjacent_collisions(items, prev_items or {})
    snap['reanchor'] = {'applied_at': label,
                        'basis': 'published US per-platform shares'}

    print(f'  {label}: {tot["entries"]:5d} items  '
          f'{tot["reapportioned"]:5d} re-apportioned  '
          f'{tot["backfilled"]:5d} backfilled  {swept:3d} placeholder swept  '
          f'{coll:4d} adjacent-day breaks')

    if not dry_run:
        raw = json.dumps(snap).encode()
        try:
            s3.copy_object(Bucket=BUCKET, Key=BACKUP_KEY.format(d=label),
                           CopySource={'Bucket': BUCKET, 'Key': day_key})
        except Exception as e:
            print(f'    backup failed ({type(e).__name__}); not writing {label}')
            return items
        s3.put_object(Bucket=BUCKET, Key=day_key, Body=raw,
                      ContentType='application/json')
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60,
                    help='trailing dated snapshots to re-anchor')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    s3 = boto3.client('s3')
    today = date.today()
    # Oldest first, so each day can be compared against the day before
    # it for the adjacent-value invariant.
    days = [(today - timedelta(days=i)).isoformat()
            for i in range(args.days, -1, -1)]

    stopwords = load_stopwords()
    print(f're-anchoring {len(days)} dated snapshots '
          f'({days[0]} .. {days[-1]}){"  [DRY RUN]" if args.dry_run else ""}')
    prev = None
    last_membership: dict = {}
    for d in days:
        m = build_membership(s3, d, stopwords)
        if m:
            last_membership = m
        prev = process_day(s3, DATED_KEY.format(d=d), d, prev, args.dry_run,
                           m or last_membership) or prev

    print('latest/:')
    today_m = build_membership(s3, today.isoformat(), stopwords) or last_membership
    process_day(s3, LATEST_KEY, 'latest', prev, args.dry_run, today_m)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
