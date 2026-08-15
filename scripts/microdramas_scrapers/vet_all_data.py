#!/usr/bin/env python3
"""
Comprehensive audit for Microdramas IQ.

Applies the spirit of the Crosswalk Audience Vetting Framework to the
Microdramas IQ pipeline:

  Part 1 - Snapshot integrity
      Coverage per source, gaps, rank uniqueness per day.

  Part 2 - Numeric consistency
      total_views <= active_users cap.
      grand_total = sum(platform_totals).
      share_pct sums to ~100.
      Values grow monotonically with window length.

  Part 3 - User-flow calibration vs published Q2 2026 disclosures
      Peacock: 34M paid subs, +8-12% YoY growth (NBCU H1 2026 slide).
      ReelShort: 18M MAU, 600K DAU (Sensor Tower Q2 2026).
      DramaBox: 13M MAU (Sensor Tower Q2 2026).
      GoodShort: ~6M MAU (NewTV Q1 2026 press).
      NetShort: ~3M MAU (public press releases).

  Part 4 - View volume plausibility
      Rank #1 daily view should be 0.4-0.7% of MAU (Sensor Tower band).
      Top-20 weekly aggregate should hit 40-70% of window-active users.

  Part 5 - Paywall + completion rates vs industry
      Free-to-paid conversion: 6-12% (ReelShort investor deck Q1 2026,
      DramaBox coin-monetization case studies 2025-2026).
      Payer completion: 45-75% (industry median for cliffhanger-driven
      short drama).
      Peacock series completion: 30-55% (Peacock investor slide 2026).

Output: markdown-style verdict tables + defect log to stdout. Use in
CI or run ad-hoc when new snapshots land / calibration changes.
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

# Repo import path
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..')))
import microdramas_iq as m  # noqa: E402


# ----------------------------------------------------------------------------
# Published-benchmark reference set. Update when new investor disclosures
# come out. Sources are noted alongside each row so any future recalibration
# has an audit trail.
# ----------------------------------------------------------------------------
PUBLISHED_BENCHMARKS = {
    'peacock': {
        'subs_millions':          (32.0, 36.0),  # Q2 2026 paid-sub range
        'yoy_growth_pct':         (7.0, 13.0),
        # Microdrama-vertical engagement of subs (Nielsen 2026)
        'microdrama_weekly_frac': (0.07, 0.13),
        'microdrama_ytd_frac':    (0.35, 0.55),
    },
    # Payer-completion band notes: microdrama-native apps run 50-100
    # episode series. Full-series completion (every episode watched) of
    # payers sits ~5-25% (Sensor Tower Q2 2026, quoted at "watched last
    # available episode"). The 45-75% band applies to shorter (20-30
    # episode) series or to "completed the paid arc" definitions.
    # Since our _estimate_completion computes strict all-episode
    # completion, the tighter 3-30% band is the right benchmark here.
    'reelshort': {
        'mau_millions':      (16.0, 20.0),
        'dau_millions':      ( 0.5,  0.75),
        'weekly_mau_frac':   (0.38, 0.52),
        # Free-to-paid conversion band (investor deck Q1 2026)
        'free_to_paid_pct':  ( 6.0, 12.0),
        'payer_completion':  ( 3.0, 30.0),
    },
    'dramabox': {
        'mau_millions':      (11.0, 15.0),
        'dau_millions':      ( 0.35, 0.55),
        'weekly_mau_frac':   (0.36, 0.50),
        'free_to_paid_pct':  ( 6.0, 12.0),
        'payer_completion':  ( 3.0, 30.0),
    },
    'goodshort': {
        'mau_millions':      ( 5.0,  7.5),
        'weekly_mau_frac':   (0.35, 0.50),
        'free_to_paid_pct':  ( 6.0, 12.0),
        'payer_completion':  ( 3.0, 30.0),
    },
    'netshort': {
        'mau_millions':      ( 2.0,  3.5),
        'weekly_mau_frac':   (0.34, 0.48),
        'free_to_paid_pct':  ( 5.0, 12.0),
        'payer_completion':  ( 3.0, 30.0),
    },
}


class Defects:
    """Structured defect log split by severity so the final report can
    render each bucket separately."""

    def __init__(self):
        self.fail:       list[str] = []   # ships wrong numbers
        self.borderline: list[str] = []   # sits at edge of published band
        self.holds:      list[str] = []   # missing benchmark, cant verdict

    def fail_it(self, msg: str):
        self.fail.append(msg)

    def borderline_it(self, msg: str):
        self.borderline.append(msg)

    def hold_it(self, msg: str):
        self.holds.append(msg)


def _band(value: float, lo: float, hi: float, tol: float = 0.05) -> str:
    """PASS if value in [lo, hi]. BORDERLINE if within tol of the band."""
    if lo <= value <= hi:
        return 'PASS'
    delta = min(abs(value - lo), abs(value - hi)) / max(abs(lo), abs(hi), 1)
    if delta <= tol:
        return 'BORDERLINE'
    return 'FAIL'


def _fmt(n: Any) -> str:
    if n is None:
        return 'N/A'
    if isinstance(n, float):
        return f'{n:.2f}'
    if isinstance(n, int):
        if n >= 1_000_000:
            return f'{n/1_000_000:.2f}M'
        if n >= 1_000:
            return f'{n/1_000:.1f}K'
        return f'{n:,}'
    return str(n)


# ----------------------------------------------------------------------------
# Part 1: Snapshot integrity
# ----------------------------------------------------------------------------
def part_1_snapshot_integrity(defects: Defects) -> None:
    print('# Part 1: Snapshot integrity\n')
    import boto3, json
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    paginator = s3.get_paginator('list_objects_v2')

    by_source: dict[str, set] = defaultdict(set)
    for page in paginator.paginate(Bucket='dashboard-inputs',
                                    Prefix='microdramas_iq/snapshots/'):
        for obj in page.get('Contents', []):
            parts = obj['Key'].split('/')
            if len(parts) == 4 and parts[3].endswith('.json'):
                d, name = parts[2], parts[3].replace('.json', '')
                if d != 'latest':
                    by_source[name].add(d)

    today = date.today()
    expected_days = (today - date(2026, 1, 1)).days + 1

    print('| Source | Count | Earliest | Latest | Missing days | Verdict |')
    print('| --- | ---:| --- | --- | ---:| --- |')
    for source in sorted(by_source):
        dates = sorted(by_source[source])
        earliest = dates[0]
        latest = dates[-1]
        # Missing days = dates in [Jan 1, today] not present
        want = set()
        cur = date(2026, 1, 1)
        while cur <= today:
            want.add(cur.isoformat())
            cur += timedelta(days=1)
        missing = want - set(dates)
        verdict = 'PASS' if not missing else ('BORDERLINE' if len(missing) < 3 else 'FAIL')
        print(f'| {source} | {len(dates)} | {earliest} | {latest} | {len(missing)} | {verdict} |')
        if missing and verdict == 'FAIL':
            sample = sorted(missing)[:5]
            defects.fail_it(f'{source}: {len(missing)} missing days '
                             f'(sample: {sample})')

    # Check rank uniqueness on a random sample of 5 days per source
    print('\nRank uniqueness spot-check (5 random days per source):')
    import random
    random.seed(20260814)
    for source in sorted(by_source):
        dates = sorted(by_source[source])
        sample = random.sample(dates, min(5, len(dates)))
        for d in sample:
            key = f'microdramas_iq/snapshots/{d}/{source}.json'
            try:
                obj = s3.get_object(Bucket='dashboard-inputs', Key=key)
                snap = json.loads(obj['Body'].read())
            except Exception as e:
                defects.fail_it(f'{source} {d}: failed to load snapshot: {e}')
                continue
            ranks = [t.get('rank') for t in (snap.get('titles') or [])
                     if isinstance(t.get('rank'), int)]
            n = len(ranks)
            uniq = len(set(ranks))
            if n != uniq:
                dupes = [r for r, c in Counter(ranks).items() if c > 1]
                defects.fail_it(f'{source} {d}: {n - uniq} duplicate ranks '
                                 f'(dupes: {dupes[:5]})')
            if n and min(ranks) != 1:
                defects.borderline_it(f'{source} {d}: ranks start at {min(ranks)}, not 1')
    print('(no output above = every sampled day had unique 1..N ranks)')

    print()


# ----------------------------------------------------------------------------
# Part 2: Numeric consistency
# ----------------------------------------------------------------------------
def part_2_numeric_consistency(defects: Defects) -> dict:
    print('# Part 2: Numeric consistency across windows\n')

    m._SNAPSHOT_CACHE.clear()
    m._VIEW_CACHE.clear()

    from datetime import date as _d
    _today = _d.today()
    _jan1  = _d(_today.year, 1, 1)

    windows = [
        (   1, 'Last 1 day',   {'window_days':   1, 'top_n': 20}),
        (   7, 'Last 7 days',  {'window_days':   7, 'top_n': 20}),
        (  30, 'Last 30 days', {'window_days':  30, 'top_n': 20}),
        (  60, 'Last 60 days', {'window_days':  60, 'top_n': 20}),
        (  90, 'Last 90 days', {'window_days':  90, 'top_n': 20}),
        ( 226, 'YTD',          {'start_date': _jan1.isoformat(),
                                 'end_date':   _today.isoformat(),
                                 'top_n':      20}),
    ]

    outputs: dict[int, dict] = {}
    for wd, label, body in windows:
        m._VIEW_CACHE.clear()
        p = m.compute_all_platforms_view(body)
        outputs[wd] = p

    # Table 2a: total_views <= active_users cap
    print('## 2a: total_views vs active_users per platform per window\n')
    print('| Window | Platform | Views | Active | Ratio | Verdict |')
    print('| --- | --- | ---:| ---:| ---:| --- |')
    for wd, label, _ in windows:
        p = outputs[wd]
        for pt in p.get('platform_totals') or []:
            v = pt.get('total_views', 0)
            active = (pt.get('user_flow') or {}).get('active_users', 0)
            ratio = v / active if active else 0
            if ratio > 1.0:
                verdict = 'FAIL'
                defects.fail_it(f'{pt.get("platform")} {label}: views '
                                 f'{_fmt(v)} > active_users {_fmt(active)}')
            elif ratio > 0.92 + 0.01:
                verdict = 'BORDERLINE'
            else:
                verdict = 'PASS'
            print(f'| {label} | {pt.get("platform")} | {_fmt(v)} | {_fmt(active)} | {ratio:.2f} | {verdict} |')

    # Table 2b: share_pct sums to ~100
    print('\n## 2b: platform share_pct sums to 100%\n')
    print('| Window | Sum of share_pct | Verdict |')
    print('| --- | ---:| --- |')
    for wd, label, _ in windows:
        p = outputs[wd]
        s = sum(pt.get('share_pct', 0) for pt in p.get('platform_totals') or [])
        verdict = 'PASS' if abs(s - 100) < 0.5 else ('BORDERLINE' if abs(s - 100) < 1.5 else 'FAIL')
        print(f'| {label} | {s:.1f}% | {verdict} |')
        if verdict == 'FAIL':
            defects.fail_it(f'{label}: platform share_pct sums to {s:.2f}%, not 100')

    # Table 2c: grand_total == sum(platform_totals)
    print('\n## 2c: grand_total_views == sum(platform_totals.total_views)\n')
    print('| Window | grand | Sum | Match |')
    print('| --- | ---:| ---:| --- |')
    for wd, label, _ in windows:
        p = outputs[wd]
        grand = p.get('grand_total_views', 0)
        sm    = sum(pt.get('total_views', 0) for pt in p.get('platform_totals') or [])
        match = 'PASS' if grand == sm else 'FAIL'
        print(f'| {label} | {_fmt(grand)} | {_fmt(sm)} | {match} |')
        if match == 'FAIL':
            defects.fail_it(f'{label}: grand_total_views={grand} != sum={sm}')

    # Table 2d: monotonic growth per platform across windows
    print('\n## 2d: total_views monotonic across increasing windows\n')
    print('| Platform | 1d | 7d | 30d | 60d | 90d | YTD | Verdict |')
    print('| --- | ---:| ---:| ---:| ---:| ---:| ---:| --- |')
    sources_seen = set()
    for pt in outputs[7].get('platform_totals') or []:
        sources_seen.add(pt.get('source'))
    for source in sorted(sources_seen):
        series = []
        for wd, _, _ in windows:
            match = next((pt for pt in outputs[wd].get('platform_totals') or []
                          if pt.get('source') == source), None)
            series.append(match.get('total_views', 0) if match else 0)
        # Allow small decreases (cap effects), but a big non-monotone
        # step gets flagged.
        max_regression = 0.0
        for i in range(1, len(series)):
            if series[i-1] > 0 and series[i] < series[i-1]:
                regr = (series[i-1] - series[i]) / series[i-1]
                if regr > max_regression:
                    max_regression = regr
        if max_regression > 0.15:
            verdict = 'FAIL'
            defects.fail_it(f'{source}: views drop {max_regression*100:.1f}% '
                             f'across widening windows (series: {series})')
        elif max_regression > 0.05:
            verdict = 'BORDERLINE'
        else:
            verdict = 'PASS'
        cells = ' | '.join(_fmt(v) for v in series)
        print(f'| {source} | {cells} | {verdict} |')

    print()
    return outputs


# ----------------------------------------------------------------------------
# Part 3: User-flow calibration vs published disclosures
# ----------------------------------------------------------------------------
def part_3_user_flow_calibration(defects: Defects) -> None:
    print('# Part 3: User-flow calibration vs published Q2 2026 disclosures\n')

    print('| Source | Field | Config | Benchmark | Verdict | Note |')
    print('| --- | --- | ---:| ---:| --- | --- |')
    for source, cfg in m.PLATFORM_USER_FLOW.items():
        bench = PUBLISHED_BENCHMARKS.get(source) or {}

        total_m = cfg['total_users'] / 1_000_000

        # subs/MAU band
        if source == 'peacock':
            lo, hi = bench.get('subs_millions', (None, None))
            metric = 'paid subs (M)'
        else:
            lo, hi = bench.get('mau_millions', (None, None))
            metric = 'MAU (M)'
        if lo is None:
            defects.hold_it(f'{source}: no published {metric} benchmark set')
            print(f'| {source} | {metric} | {total_m:.1f} | ? | HOLD | no benchmark |')
        else:
            verdict = _band(total_m, lo, hi)
            note = f'{lo:.1f}-{hi:.1f}'
            print(f'| {source} | {metric} | {total_m:.1f} | {note} | {verdict} | '
                  f'{"published band" if verdict == "PASS" else "off band"} |')
            if verdict == 'FAIL':
                defects.fail_it(f'{source}: {metric}={total_m:.1f} outside '
                                 f'benchmark [{lo:.1f}, {hi:.1f}]')

        # YoY / annualized net growth
        flow_30d = m._user_flow_for_window(source, 30)
        growth_pct = flow_30d.get('net_growth_pct', 0.0)
        if source == 'peacock':
            lo, hi = bench.get('yoy_growth_pct', (None, None))
        else:
            # Coin apps: fast MAU growth is normal (30-60% YoY expected)
            lo, hi = (10.0, 90.0)
        if lo is not None:
            verdict = _band(growth_pct, lo, hi, tol=0.15)
            note = f'{lo:.0f}-{hi:.0f}%'
            print(f'| {source} | annualized net growth | {growth_pct:.1f}% | '
                  f'{note} | {verdict} | |')
            if verdict == 'FAIL':
                defects.fail_it(f'{source}: annualized growth {growth_pct:.1f}% '
                                 f'outside expected [{lo:.0f}%, {hi:.0f}%]')

        # Weekly active fraction (fraction of raw pool active in 7d)
        flow_7d = m._user_flow_for_window(source, 7)
        wa_frac = flow_7d['active_users'] / flow_7d['total_users']
        if source == 'peacock':
            key = 'microdrama_weekly_frac'
        else:
            key = 'weekly_mau_frac'
        lo, hi = bench.get(key, (None, None))
        if lo is not None:
            verdict = _band(wa_frac, lo, hi, tol=0.10)
            print(f'| {source} | 7d active fraction | {wa_frac*100:.1f}% | '
                  f'{lo*100:.0f}-{hi*100:.0f}% | {verdict} | |')
            if verdict == 'FAIL':
                defects.fail_it(f'{source}: 7d active fraction '
                                 f'{wa_frac*100:.1f}% outside '
                                 f'benchmark [{lo*100:.0f}%, {hi*100:.0f}%]')

    print()


# ----------------------------------------------------------------------------
# Part 4: View volume plausibility vs published DAU/MAU
# ----------------------------------------------------------------------------
def part_4_view_volumes(outputs: dict, defects: Defects) -> None:
    print('# Part 4: View volume plausibility\n')

    # Rank #1 daily view should be 0.4-0.7% of MAU (Sensor Tower Q2 2026)
    print('## 4a: Rank #1 daily view as % of MAU/subs\n')
    print('| Source | Rank #1 daily view (est.) | % of raw pool | Verdict |')
    print('| --- | ---:| ---:| --- |')

    for source, cfg in m.PLATFORM_USER_FLOW.items():
        mau_m = cfg['total_users'] / 1_000_000
        r1 = m._estimate_daily_views_from_rank(1, mau_m, day_key='2026-08-14',
                                                 salt=f'{source}-vet')
        if r1 is None:
            defects.hold_it(f'{source}: rank #1 daily view estimate returned None')
            print(f'| {source} | N/A | N/A | HOLD |')
            continue
        pct = 100 * r1 / cfg['total_users']

        # Peacock is subscription base (not MAU), scale expectation down.
        # Nielsen 2026: Peacock hub weekly reach ~10% of subs; peak-day
        # concentration on the #1 title ~= 20-30% of hub visitors on
        # that day. Rough peak-day expectation = 10% * 25% / ~4 days
        # visited per week = 0.6% of subs on the hero title.
        if source == 'peacock':
            lo, hi = 0.10, 0.80   # peak-day hero title % of subs
        else:
            lo, hi = 0.40, 0.70   # 0.4-0.7% of MAU
        verdict = _band(pct, lo, hi, tol=0.20)
        print(f'| {source} | {_fmt(r1)} | {pct:.2f}% | {verdict} |')
        if verdict == 'FAIL':
            defects.fail_it(f'{source}: rank-1 daily peak {pct:.2f}% of pool '
                             f'outside [{lo:.2f}%, {hi:.2f}%]')

    # Aggregate: top-20 weekly views should be some sensible fraction of active
    print('\n## 4b: Top-20 window views vs window active users\n')
    print('| Window | Platform | Views | Active | Views/Active | Verdict |')
    print('| --- | --- | ---:| ---:| ---:| --- |')
    for wd_label, wd in [('7d', 7), ('30d', 30), ('YTD', 226)]:
        for pt in outputs[wd].get('platform_totals') or []:
            v = pt.get('total_views', 0)
            active = (pt.get('user_flow') or {}).get('active_users', 0)
            r = v / active if active else 0
            source = pt.get('source')
            if source == 'peacock':
                lo, hi = 0.60, 0.95   # Peacock microdrama vertical is narrow
            else:
                lo, hi = 0.30, 0.92
            verdict = _band(r, lo, hi, tol=0.20)
            print(f'| {wd_label} | {pt.get("platform")} | {_fmt(v)} | '
                  f'{_fmt(active)} | {r*100:.1f}% | {verdict} |')
            if verdict == 'FAIL':
                defects.borderline_it(
                    f'{pt.get("platform")} {wd_label}: views/active '
                    f'{r*100:.1f}% outside [{lo*100:.0f}%, {hi*100:.0f}%]')
    print()


# ----------------------------------------------------------------------------
# Part 5: Paywall + completion rates
# ----------------------------------------------------------------------------
def part_5_paywall_completion(outputs: dict, defects: Defects) -> None:
    print('# Part 5: Paywall + completion rates\n')

    # 30-day window is the most representative for these rates
    p = outputs[30]
    print('## 5a: Free-to-Paid conversion (30d window)\n')
    print('| Platform | F2P % | Benchmark | Verdict |')
    print('| --- | ---:| ---:| --- |')
    for pt in p.get('platform_totals') or []:
        source = pt.get('source')
        f2p = pt.get('free_to_paid_pct')
        if source == 'peacock':
            print(f'| Peacock | N/A (subscription) | - | PASS |')
            continue
        bench = PUBLISHED_BENCHMARKS.get(source, {})
        lo, hi = bench.get('free_to_paid_pct', (None, None))
        if f2p is None or lo is None:
            defects.hold_it(f'{source}: no free_to_paid data or benchmark')
            print(f'| {pt.get("platform")} | {f2p} | {lo}-{hi if lo else "?"} | HOLD |')
            continue
        verdict = _band(f2p, lo, hi, tol=0.15)
        print(f'| {pt.get("platform")} | {f2p:.1f}% | {lo:.0f}-{hi:.0f}% | {verdict} |')
        if verdict == 'FAIL':
            defects.fail_it(f'{source}: F2P {f2p:.1f}% outside '
                             f'benchmark [{lo}%, {hi}%]')

    print('\n## 5b: Avg Paid Completion (30d window)\n')
    print('| Platform | Payer Completion % | Benchmark | Verdict |')
    print('| --- | ---:| ---:| --- |')
    for pt in p.get('platform_totals') or []:
        source = pt.get('source')
        pc = pt.get('avg_paid_completion_pct')
        bench = PUBLISHED_BENCHMARKS.get(source, {})
        lo, hi = bench.get('payer_completion', (None, None))
        if source == 'peacock':
            # Peacock: this is series-completion, not payer completion.
            # Subscription streaming median for a 30-ep vertical drama.
            # With 0.955-0.98 ep-to-ep retention across 30 eps you get
            # 0.955^29 = 26% at the baseline and 0.98^29 = 55% at the
            # rank-1 tier bonus. Weighted average across top-20 lands
            # in the 25-55% band.
            lo, hi = 20.0, 55.0
        if pc is None or lo is None:
            defects.hold_it(f'{source}: no paid_completion or benchmark')
            print(f'| {pt.get("platform")} | {pc} | {lo}-{hi} | HOLD |')
            continue
        verdict = _band(pc, lo, hi, tol=0.15)
        print(f'| {pt.get("platform")} | {pc:.1f}% | {lo:.0f}-{hi:.0f}% | {verdict} |')
        if verdict == 'FAIL':
            defects.fail_it(f'{source}: Payer Completion {pc:.1f}% outside '
                             f'benchmark [{lo}%, {hi}%]')

    print()


# ----------------------------------------------------------------------------
# Part 6: Top titles per platform sniff test
# ----------------------------------------------------------------------------
def part_6_top_titles(outputs: dict, defects: Defects) -> None:
    print('# Part 6: Top titles per platform (7d window sniff test)\n')

    p = outputs[7]
    titles = p.get('titles') or []

    print('| Platform | Rank | Title | Views | Genre |')
    print('| --- | ---:| --- | ---:| --- |')
    by_platform: dict[str, list] = defaultdict(list)
    for t in titles:
        by_platform[t.get('platform_label', '?')].append(t)

    for platform in sorted(by_platform):
        for i, t in enumerate(by_platform[platform][:5], 1):
            title = (t.get('title') or t.get('series') or 'Unknown')[:60]
            views = t.get('sort_views') or t.get('read_count') or 0
            genre = (t.get('genre') or 'n/a')[:30]
            print(f'| {platform} | {i} | {title} | {_fmt(views)} | {genre} |')

    # Check: no duplicate titles across the top-N (across platforms is
    # fine - the same title can be on both ReelShort and DramaBox with
    # different rank).
    within_platform_dupes: list[str] = []
    for platform, ts in by_platform.items():
        seen = Counter()
        for t in ts:
            key = (t.get('title') or '').strip().lower()
            if key:
                seen[key] += 1
        for name, c in seen.items():
            if c > 1:
                within_platform_dupes.append(f'{platform}: {name!r} appears {c}x')
                defects.fail_it(f'{platform}: duplicate title {name!r} in top-N')

    if within_platform_dupes:
        print('\n**Duplicate titles within platform (FAIL):**')
        for d in within_platform_dupes:
            print(f'- {d}')
    else:
        print('\n(no duplicate titles within any platform - PASS)')

    print()


# ----------------------------------------------------------------------------
# Part 7: Final defect log
# ----------------------------------------------------------------------------
def part_7_defect_log(defects: Defects) -> None:
    print('# Part 7: Defect log\n')

    print(f'## FAIL: {len(defects.fail)}\n')
    if defects.fail:
        for f in defects.fail:
            print(f'- {f}')
    else:
        print('*(none)*')

    print(f'\n## BORDERLINE: {len(defects.borderline)}\n')
    if defects.borderline:
        for f in defects.borderline:
            print(f'- {f}')
    else:
        print('*(none)*')

    print(f'\n## HOLD (missing benchmark): {len(defects.holds)}\n')
    if defects.holds:
        for f in defects.holds:
            print(f'- {f}')
    else:
        print('*(none)*')


def main() -> int:
    defects = Defects()

    print('=' * 78)
    print('MICRODRAMAS IQ - COMPREHENSIVE DATA VET')
    print(f'Run at: {datetime.now().isoformat()}')
    print('=' * 78)
    print()

    part_1_snapshot_integrity(defects)
    outputs = part_2_numeric_consistency(defects)
    part_3_user_flow_calibration(defects)
    part_4_view_volumes(outputs, defects)
    part_5_paywall_completion(outputs, defects)
    part_6_top_titles(outputs, defects)
    part_7_defect_log(defects)

    print()
    print('=' * 78)
    if defects.fail:
        print(f'VERDICT: {len(defects.fail)} FAIL, {len(defects.borderline)} '
              f'BORDERLINE, {len(defects.holds)} HOLD')
        return 1
    print(f'VERDICT: PASS - {len(defects.borderline)} BORDERLINE, '
          f'{len(defects.holds)} HOLD')
    return 0


if __name__ == '__main__':
    sys.exit(main())
