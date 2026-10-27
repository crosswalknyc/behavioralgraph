#!/usr/bin/env python3
"""Published-measurement anchors for the streaming rankers.

Why this exists (Jenna, 2026-10-26, after the Prime Video comparison for
Amazon): "should we use this research and findings to help with our daily
rankers? put all this to good use and do this kind of deep dive on the
other platforms so that we can project the best possible number every
day" and "remember in rankers we do daily views so duplication in views
can happen".

The unit
--------
A ranker reading is DAILY ACCOUNT-VIEWS: one account that played the
title on that calendar day counts once, the same account counts again on
every later day it returns, and the people on the couch are never
multiplied in. It is not a unique-viewer count over a window (a person
is duplicated across days) and it is not a play count (three episodes in
one sitting are still one account-day). Every anchor below is converted
into that unit before it is compared or applied.

What the audit of 2026-10-26 found (`--audit` on the live snapshots for
Sept 17, Sept 24, Oct 1, Oct 8 and Oct 22, against the Nielsen weekly
top tens for those weeks):

  * per title the rankers sat anywhere from 0.01x to 4.6x the level the
    published minutes imply. Library series were read far too low (The
    Big Bang Theory on HBO Max 8K to 73K a day against ~1.5M implied;
    Bluey, Ms. Rachel, Young Sheldon, Murder in a Small Town the same
    way), films held their launch level for weeks (Toy Story 5 4.4x in
    its third Disney+ week, The Love Hypothesis 4.6x, The Widower 3.4x),
    and one title moved 0.8x to 2.2x week to week because nothing tracks
    the published decay.
  * per platform the SUM of readings on Oct 22 was over the daily budget
    the Gauge share implies on almost every service (Paramount+ 3.5x,
    AMC+ 4.8x, HBO Max 1.8x, Disney+ 1.8x, Peacock 1.6x, Prime Video
    1.5x) because the catalog tail is too fat, while Netflix sat at 0.7x
    with its hits under-read.

Three anchors, in order of strength
-----------------------------------
1. TITLE anchors: Nielsen's weekly Streaming Top 10 (overall, original,
   acquired, movies; US, TV sets, persons 2+) and Luminate's weekly
   hours. Weekly persons-minutes convert to daily account-views:

       minutes x 1.12 (phones, tablets, computers)
               / persons-per-stream (co-viewing, by genre)
               / minutes-per-account-day (by format)
               / 7

   A title that charts carries that level for the days of that week,
   shaped by the day of week, and decays into the next week by the
   published week-over-week change when the next chart lands.
2. PLATFORM budgets: Nielsen's Gauge share of TV for the month (latest
   release) times total US TV time (~100B persons-minutes a day, from
   Nielsen's 16.7T streaming minutes in 2025 at a 44 to 47% streaming
   share), converted the same way, is the most account-views a platform
   can hold in a day across ALL titles. The sum of a platform's readings
   never exceeds it; the unanchored tail is scaled down to fit, in rank
   order, never the anchored titles.
3. CARRIAGE shares (Antenna, filings) for the "X on Amazon" rails live in
   `carriage_mix.py`; the 2026-10-26 research moved Paramount+ toward a
   fifth of its base (10M of ~48M US subscribers through Channels),
   FOX One to a fifth to a third, Apple TV to about a quarter, Starz
   stays near 44% (29% of Starz revenue is Amazon).

What this module does today
---------------------------
Read-only audit and a dry-run relevel. It is NOT wired into the nightly
run. Wiring plan (see MEASUREMENT_ANCHORS.md): weekly scrape of
nielsen.com/data-center/top-ten/ into the anchor registry, monthly Gauge
share update, then `relevel_items` in `run_all` after
`first_party_calibration.apply_to_store` behind TRENDS_MEASUREMENT_ANCHORS=1,
with the salt and distinctness rules the board already enforces.

Never queries clickstream (`trends-rankers-never-clickstream.mdc`).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import statistics
from collections import defaultdict
from typing import Any, Iterable, Optional

OTHER_SCREENS = 1.12          # Nielsen measures TV sets only

# genre -> (persons per stream, minutes per account-day)
GENRES: dict[str, tuple[float, float]] = {
    'bingedrama':  (1.35, 105),
    'weeklydrama': (1.35, 65),
    'drama':       (1.35, 95),
    'procedural':  (1.30, 85),
    'sitcom':      (1.30, 70),
    'kids':        (1.60, 55),
    'anim_adult':  (1.30, 60),
    'reality':     (1.20, 80),
    'cooking':     (1.25, 70),
    'film':        (1.25, 100),
    'kidsfilm':    (1.60, 95),
    'sports':      (1.75, 160),
    'news':        (1.20, 45),
}

# Total US TV persons-minutes per day. Nielsen: 16.7T streaming minutes in
# 2025 (ARTEY release, Jan 2026) at a 44 to 47% streaming share of TV
# => 36 to 38T total => ~100B a day. Cross-checked against Netflix's own
# engagement hours at its Gauge share, Love Island vs Peacock's share,
# The Pitt vs WBD's share, World Cup minutes vs the broadcast share.
TOTAL_TV_PERSONS_MINUTES_PER_DAY = 100e9

# Platform share of TV, Nielsen Gauge, by month. Add a row when Nielsen
# publishes; the latest month is used. Portfolio lines are split with
# the standing in-house read (Disney streaming = Disney+ and Hulu about
# evenly plus a sliver of ESPN+; Paramount = Paramount+ and Pluto; WBD =
# HBO Max plus discovery+).
GAUGE_SHARES: dict[str, dict[str, float]] = {
    '2026-08': {
        'youtube': 0.158, 'netflix': 0.077, 'disneyplus': 0.023, 'hulu': 0.023,
        'primevideo': 0.042, 'roku': 0.029, 'paramountplus': 0.011, 'pluto': 0.012,
        'tubi': 0.019, 'peacock': 0.016, 'max': 0.013, 'appletv': 0.006,
        'starz': 0.002, 'amcplus': 0.001, 'britbox': 0.001, 'mgmplus': 0.001,
        'espnplus': 0.002,
        # the two partner-calibrated channels run ~22K and ~44K streams a day
        # (first_party_calibration); their budget is set from that, not the Gauge
        'lionsgateplus': 0.00005, 'moviesphereplus': 0.0001,
    },
}

# Day-of-week shape for on-demand viewing (Mon=0). Sports are handled by
# their own event rows, not by this shape.
DOW_SHAPE = [0.93, 0.92, 0.93, 0.95, 1.02, 1.12, 1.13]

# Published weekly anchors. Seeded by hand from the Nielsen releases of
# Sept 7 to Oct 11, 2026 (US, TV sets, persons 2+, millions of minutes).
# Platform slugs match the board. Multi-platform titles name the
# platform the row is credited to in the board, with the share of the
# published minutes that platform plausibly carries.
ANCHOR_WEEKS: list[dict[str, Any]] = [
    {'week': '2026-09-07/2026-09-13', 'source': 'nielsen', 'titles': [
        ('reacher', 'primevideo', 1180, 'weeklydrama', 1.0),
        ('lioness', 'paramountplus', 834, 'weeklydrama', 1.0),
        ('ted lasso', 'appletv', 734, 'weeklydrama', 1.0),
        ('secret lives of mormon wives', 'hulu', 561, 'reality', 1.0),
        ('silo', 'appletv', 418, 'weeklydrama', 1.0),
        ('why did i get married again', 'netflix', 908, 'film', 1.0),
        ('supergirl', 'max', 304, 'film', 1.0),
        ('practical magic', 'max', 236, 'film', 1.0),
    ]},
    {'week': '2026-09-14/2026-09-20', 'source': 'nielsen', 'titles': [
        ('neagley', 'primevideo', 1776, 'bingedrama', 1.0),
        ('monster the lizzie borden story', 'netflix', 1481, 'bingedrama', 1.0),
        ('reacher', 'primevideo', 1420, 'weeklydrama', 1.0),
        ('the big bang theory', 'max', 950, 'sitcom', 1.0),
        ('lioness', 'paramountplus', 897, 'weeklydrama', 1.0),
        ('ted lasso', 'appletv', 767, 'weeklydrama', 1.0),
        ('bluey', 'disneyplus', 739, 'kids', 1.0),
        ('spongebob squarepants', 'paramountplus', 692, 'kids', 1.0),
        ('crew girl', 'netflix', 635, 'bingedrama', 1.0),
        ('the gentlemen', 'netflix', 544, 'bingedrama', 1.0),
        ('the perfect lie', 'netflix', 412, 'bingedrama', 1.0),
        ('secret lives of mormon wives', 'hulu', 360, 'reality', 1.0),
        ("bob's burgers", 'hulu', 569, 'sitcom', 1.0),
        ('lanterns', 'max', 534, 'weeklydrama', 1.0),
        ('family guy', 'hulu', 466, 'sitcom', 1.0),
        ('why did i get married again', 'netflix', 332, 'film', 1.0),
        ('the seemingly perfect family', 'netflix', 308, 'film', 1.0),
        ('puss in boots the last wish', 'netflix', 250, 'kidsfilm', 1.0),
        ('supergirl', 'max', 218, 'film', 1.0),
        ('star wars the mandalorian and grogu', 'disneyplus', 159, 'film', 1.0),
        ('practical magic', 'max', 139, 'film', 1.0),
        ('the super mario galaxy movie', 'peacock', 138, 'kidsfilm', 1.0),
        ('mayday', 'appletv', 131, 'film', 1.0),
    ]},
    {'week': '2026-09-21/2026-09-27', 'source': 'nielsen', 'titles': [
        ('toy story 5', 'disneyplus', 1540, 'kidsfilm', 1.0),
        ('monster the lizzie borden story', 'netflix', 1360, 'bingedrama', 1.0),
        ('neagley', 'primevideo', 1250, 'bingedrama', 1.0),
        ("wonka's the golden ticket", 'netflix', 854, 'bingedrama', 1.0),
        ('a different world', 'netflix', 751, 'sitcom', 1.0),
        ('reacher', 'primevideo', 707, 'weeklydrama', 1.0),
        ('american horror story', 'hulu', 763, 'drama', 1.0),
        ('gilmore girls', 'hulu', 512, 'drama', 0.85),
        ('mobland', 'paramountplus', 364, 'weeklydrama', 1.0),
        ('the love hypothesis', 'primevideo', 414, 'film', 1.0),
        ('unabomber', 'netflix', 407, 'film', 1.0),
    ]},
    {'week': '2026-09-28/2026-10-04', 'source': 'nielsen', 'titles': [
        ("wonka's the golden ticket", 'netflix', 1078, 'bingedrama', 1.0),
        ('the big bang theory', 'max', 953, 'sitcom', 1.0),
        ('east of eden', 'netflix', 782, 'bingedrama', 1.0),
        ('spongebob squarepants', 'paramountplus', 767, 'kids', 1.0),
        ('ted lasso', 'appletv', 750, 'weeklydrama', 1.0),
        ('american horror story', 'hulu', 712, 'drama', 1.0),
        ('bluey', 'disneyplus', 706, 'kids', 1.0),
        ('toy story 5', 'disneyplus', 687, 'kidsfilm', 1.0),
        ('neagley', 'primevideo', 661, 'bingedrama', 1.0),
        ('a different world', 'netflix', 585, 'sitcom', 1.0),
        ('monster the lizzie borden story', 'netflix', 550, 'bingedrama', 1.0),
        ('reacher', 'primevideo', 513, 'weeklydrama', 1.0),
        ('the great british baking show', 'netflix', 486, 'cooking', 1.0),
        ('ms rachel', 'netflix', 406, 'kids', 1.0),
        ('lioness', 'paramountplus', 390, 'weeklydrama', 1.0),
        ('doc', 'hulu', 611, 'procedural', 0.6),
        ('gilmore girls', 'hulu', 551, 'drama', 0.75),
        ("bob's burgers", 'hulu', 536, 'sitcom', 1.0),
        ('the breadwinner', 'netflix', 553, 'film', 1.0),
        ('unabomber', 'netflix', 481, 'film', 1.0),
        ("the widower til death do us part", 'netflix', 388, 'film', 1.0),
        ('doing life', 'netflix', 360, 'film', 1.0),
        ("now you see me now you don't", 'primevideo', 240, 'film', 1.0),
        ('backrooms', 'max', 231, 'film', 1.0),
        ('the love hypothesis', 'primevideo', 196, 'film', 1.0),
    ]},
    {'week': '2026-10-05/2026-10-11', 'source': 'nielsen', 'titles': [
        ('carrie', 'primevideo', 1264, 'bingedrama', 1.0),
        ('below', 'netflix', 1187, 'bingedrama', 1.0),
        ('east of eden', 'netflix', 1162, 'bingedrama', 1.0),
        ('ted lasso', 'appletv', 943, 'weeklydrama', 1.0),
        ('the big bang theory', 'max', 812, 'sitcom', 1.0),
        ('bluey', 'disneyplus', 764, 'kids', 1.0),
        ('spongebob squarepants', 'paramountplus', 737, 'kids', 1.0),
        ('animals', 'netflix', 719, 'film', 1.0),
        ("bob's burgers", 'hulu', 626, 'sitcom', 1.0),
        ("wonka's the golden ticket", 'netflix', 600, 'bingedrama', 1.0),
        ('the great british baking show', 'netflix', 441, 'cooking', 1.0),
        ('reacher', 'primevideo', 433, 'weeklydrama', 1.0),
        ('ms rachel', 'netflix', 396, 'kids', 1.0),
        ('american horror story', 'hulu', 598, 'drama', 1.0),
        ('murder in a small town', 'hulu', 517, 'procedural', 0.6),
        ('young sheldon', 'max', 475, 'sitcom', 0.55),
        ('doing life', 'netflix', 428, 'film', 1.0),
        ('toy story 5', 'disneyplus', 357, 'kidsfilm', 1.0),
        ("the widower til death do us part", 'netflix', 273, 'film', 1.0),
        ('disclosure day', 'peacock', 272, 'film', 1.0),
        ('the breadwinner', 'netflix', 232, 'film', 1.0),
    ]},
]


# ---------------------------------------------------------------------------
# conversions
# ---------------------------------------------------------------------------
def implied_daily_account_views(weekly_minutes_m: float, genre: str,
                                platform_share: float = 1.0) -> float:
    """Daily account-views implied by a week of published persons-minutes."""
    cov, mpad = GENRES.get(genre, GENRES['drama'])
    return weekly_minutes_m * 1e6 * platform_share * OTHER_SCREENS / cov / mpad / 7.0


def latest_gauge_month() -> str:
    return sorted(GAUGE_SHARES)[-1]


def platform_budget(slug: str, month: Optional[str] = None,
                    cov: float = 1.35, mpad: float = 90.0) -> Optional[float]:
    """Most account-views a platform can hold in a day across all titles."""
    shares = GAUGE_SHARES.get(month or latest_gauge_month(), {})
    sh = shares.get(slug)
    if sh is None:
        return None
    return TOTAL_TV_PERSONS_MINUTES_PER_DAY * sh * OTHER_SCREENS / cov / mpad


def _norm(s: str) -> str:
    s = (s or '').lower().replace('&', ' and ')
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(the|a|an|of|and|in|to|on|for|with)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _h01(key: str) -> float:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def week_for(day_iso: str) -> Optional[dict[str, Any]]:
    d = _dt.date.fromisoformat(day_iso)
    for w in ANCHOR_WEEKS:
        a, b = w['week'].split('/')
        if _dt.date.fromisoformat(a) <= d <= _dt.date.fromisoformat(b):
            return w
    return None


def anchor_level(title: str, platform: str, day_iso: str) -> Optional[float]:
    """The anchored daily account-views for (title, platform) on `day_iso`,
    or None when no published week covers that day and title."""
    w = week_for(day_iso)
    if not w:
        return None
    nt = _norm(title)
    for t, plat, mins, genre, share in w['titles']:
        if plat != platform:
            continue
        if _norm(t) == nt:
            base = implied_daily_account_views(mins, genre, share)
            dow = DOW_SHAPE[_dt.date.fromisoformat(day_iso).weekday()]
            # a small salted move so two charting titles never share a value
            wobble = 1.0 + (_h01(f'{platform}|{nt}|{day_iso}') - 0.5) * 0.06
            return base * dow * wobble
    return None


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
_STREAMING_KINDS = ('film', 'tv', 'title')


def _platform_readings(items: dict) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for k, v in items.items():
        kind = k.split(':', 1)[0]
        if kind not in _STREAMING_KINDS:
            continue
        for p, bp in (v.get('by_platform') or {}).items():
            est = (bp or {}).get('us_estimate')
            if est:
                out[p].append((k, float(est)))
    return out


def audit_items(items: dict, target_date: str) -> dict[str, Any]:
    """Per-platform budget use and per-title anchor ratios for one day."""
    per = _platform_readings(items)
    report: dict[str, Any] = {'target_date': target_date, 'platforms': {}, 'titles': []}
    for plat, rows in sorted(per.items()):
        total = sum(v for _, v in rows)
        b = platform_budget(plat)
        vals = sorted((v for _, v in rows), reverse=True)
        report['platforms'][plat] = {
            'n_titles': len(rows), 'sum': total, 'budget': b,
            'sum_over_budget': (total / b) if b else None,
            'top': vals[0], 'median': statistics.median(vals),
        }
    w = week_for(target_date)
    if w:
        for t, plat, mins, genre, share in w['titles']:
            imp = implied_daily_account_views(mins, genre, share)
            nt = _norm(t)
            hit = None
            for k, v in per.get(plat, []):
                if _norm(k.split(':', 1)[1]) == nt:
                    hit = v
                    break
            report['titles'].append({'title': t, 'platform': plat, 'week': w['week'],
                                     'implied': imp, 'ranker': hit,
                                     'ratio': (hit / imp) if hit else None})
    return report


def print_audit(report: dict[str, Any]) -> None:
    print(f"audit for {report['target_date']}")
    print(f"{'platform':15s} {'budget/day':>13s} {'sum':>13s} {'n':>5s} {'sum/budget':>10s} {'top':>11s} {'median':>9s}")
    for p, r in report['platforms'].items():
        b = f"{r['budget']:,.0f}" if r['budget'] else 'n/a'
        sob = f"{r['sum_over_budget']:.2f}" if r['sum_over_budget'] else '   '
        print(f"{p:15s} {b:>13s} {r['sum']:13,.0f} {r['n_titles']:5d} {sob:>10s} {r['top']:11,.0f} {r['median']:9,.0f}")
    if report['titles']:
        print()
        print(f"{'title':34s} {'platform':14s} {'implied/day':>12s} {'ranker':>11s} {'ratio':>6s}")
        for t in report['titles']:
            rk = f"{t['ranker']:,.0f}" if t['ranker'] else 'missing'
            ra = f"{t['ratio']:.2f}" if t['ratio'] else ''
            print(f"{t['title'][:34]:34s} {t['platform']:14s} {t['implied']:12,.0f} {rk:>11s} {ra:>6s}")


# ---------------------------------------------------------------------------
# dry-run relevel
# ---------------------------------------------------------------------------
def relevel_items(items: dict, target_date: str, headroom: float = 0.90) -> dict[str, Any]:
    """Return {key: {platform: new_value}} for a day.

    1. A title that charts in the published week takes its anchored level.
    2. On every platform whose readings sum above `headroom` x budget, the
       UNANCHORED readings are scaled down by one factor so the platform
       lands at the headroom, order preserved; anchored titles are never
       scaled. Nothing is ever scaled up here: an under-read hit is lifted
       only by its own anchor, never by a platform factor.
    """
    per = _platform_readings(items)
    changes: dict[str, dict[str, float]] = defaultdict(dict)
    stats: dict[str, Any] = {'anchored': 0, 'scaled_platforms': {}}
    for plat, rows in per.items():
        anchored: dict[str, float] = {}
        for k, v in rows:
            title = k.split(':', 1)[1]
            a = anchor_level(title, plat, target_date)
            if a is not None:
                anchored[k] = a
        stats['anchored'] += len(anchored)
        b = platform_budget(plat)
        if not b:
            for k, a in anchored.items():
                changes[k][plat] = a
            continue
        anchored_sum = sum(anchored.values())
        # salts first (so a flat factor never produces identical values),
        # then one factor solved against the salted sum so the platform
        # lands exactly at the headroom, never above it
        tail = [(k, v, v * (1.0 + (_h01(f'{plat}|{k}|{target_date}|tail') - 0.5) * 0.02))
                for k, v in rows if k not in anchored]
        tail_salted_sum = sum(sv for _, _, sv in tail)
        room = max(0.0, headroom * b - anchored_sum)
        factor = min(1.0, room / tail_salted_sum) if tail_salted_sum > 0 else 1.0
        if factor < 1.0:
            stats['scaled_platforms'][plat] = round(factor, 4)
        for k, a in anchored.items():
            changes[k][plat] = a
        if factor < 1.0:
            for k, v, sv in tail:
                changes[k][plat] = sv * factor
    stats['changes'] = sum(len(c) for c in changes.values())
    return {'changes': changes, 'stats': stats}


def _main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--audit', metavar='SNAPSHOT_JSON', help='stream_estimates snapshot to audit')
    ap.add_argument('--relevel', nargs=2, metavar=('IN_JSON', 'OUT_JSON'),
                    help='dry run: write a releveled copy of a snapshot')
    ap.add_argument('--date', help='target date (defaults to the snapshot target_date)')
    a = ap.parse_args(list(argv) if argv is not None else None)
    if a.audit:
        snap = json.load(open(a.audit))
        items = snap.get('items') or {}
        print_audit(audit_items(items, a.date or snap.get('target_date')))
        return 0
    if a.relevel:
        snap = json.load(open(a.relevel[0]))
        items = snap.get('items') or {}
        day = a.date or snap.get('target_date')
        res = relevel_items(items, day)
        for k, plats in res['changes'].items():
            for p, nv in plats.items():
                items[k]['by_platform'][p]['us_estimate'] = int(round(nv))
        snap['measurement_anchors'] = {'applied_for': day, **res['stats']}
        json.dump(snap, open(a.relevel[1], 'w'))
        print(json.dumps(res['stats'], indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    raise SystemExit(_main())
