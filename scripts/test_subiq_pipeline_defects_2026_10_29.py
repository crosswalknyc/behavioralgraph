#!/usr/bin/env python3
"""Subscriber IQ pipeline defects surfaced by the S.W.A.T. Exiles Season 1
to date read (2026-10-29). Hermetic: no network, no model calls.

b. platform overlap never sits below the service's US baseline (floor with a
   salted lift reasoned from the research ordering, never a pin)
c. a read carries its own conversion inside the tier band; two titles on the
   same service never share a 4dp conversion and neither is the prior
d. the finale share applies only when the finale aired inside the window
e. the monthly split parses string episode dates and follows the episode
   timing curves; it re-sums to the headline
"""
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import SVOD_Churn_Attribution as svod  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


BASE = {'netflix': 68.17, 'amazon prime video': 61.96, 'paramount+': 41.95, 'hbo max': 39.96,
        'peacock': 36.97, 'apple tv+': 14.08, 'starz': 10.38}

print('== b. overlap floor at the US baseline')
cfg = {'project_name': 'Fixture_Title', 'show_search_terms': ['Fixture Title'], 'platform_name': 'Starz',
       'baseline_overlap_pcts': BASE}
research = {'competitive_overlap': [
    {'platform': 'netflix', 'pct': 69.1}, {'platform': 'amazon prime video', 'pct': 50.3},
    {'platform': 'hulu', 'pct': 43.9}, {'platform': 'hbo max', 'pct': 34.6},
    {'platform': 'peacock', 'pct': 24.8}, {'platform': 'paramount+', 'pct': 19.7},
    {'platform': 'apple tv+', 'pct': 14.3}]}
df = svod._build_synthetic_competitive(cfg, 100_000, research=research)
got = {str(r['COMMON_NAME']): float(r['PERCENT']) for _, r in df.iterrows()}
check('Prime Video rose above its baseline', got['amazon prime video'] > BASE['amazon prime video'], got)
check('floor is not a pin: every lifted row sits 1.5% to 10% over the baseline',
      all(1.015 <= got[k] / BASE[k] <= 1.105 for k in ('amazon prime video', 'hbo max', 'peacock', 'paramount+')), got)
check('a row already above baseline keeps its research value (2dp)', abs(got['netflix'] - 69.1) < 0.3, got['netflix'])
check('a service with no baseline row is left alone', abs(got['hulu'] - 43.9) < 0.3, got['hulu'])
check('lift follows the research ordering (Prime Video, ranked higher, lifted more than Paramount+)',
      got['amazon prime video'] / BASE['amazon prime video'] > got['paramount+'] / BASE['paramount+'], got)
df2 = svod._build_synthetic_competitive(dict(cfg, project_name='Other_Title'), 100_000, research=research)
got2 = {str(r['COMMON_NAME']): float(r['PERCENT']) for _, r in df2.iterrows()}
check('two titles do not share the floored value', got2['amazon prime video'] != got['amazon prime video'])
excl = svod._build_synthetic_competitive(dict(cfg, is_platform_exclusive_cohort=True), 100_000, research=research)
check('an exclusive cohort run is left alone',
      abs(float(excl[excl['COMMON_NAME'] == 'amazon prime video']['PERCENT'].iloc[0]) - 50.3) < 0.3)
nobase = svod._build_synthetic_competitive(dict(cfg, baseline_overlap_pcts={'netflix': 68.17}), 100_000, research=research)
check('no baseline for a service means no floor on it',
      abs(float(nobase[nobase['COMMON_NAME'] == 'amazon prime video']['PERCENT'].iloc[0]) - 50.3) < 0.3)
check('values stay 2dp and off .X0 boundaries', all(int(round(v * 100)) % 10 for v in got.values()), got)

print('== c. per-read conversion inside the tier band')
prior = svod._SYNTHETIC_TIER_CONVERSION_PCT['niche']
common = {'platform_name': 'Starz', 'campaign_start': datetime(2026, 9, 25), 'campaign_end': datetime(2026, 10, 29),
          'genre': 'Action/Crime Drama', 'content_cadence': 'Weekly', 'is_new_show': True,
          'episode_dates': ['2026-09-25', '2026-10-02'], 'skip_external_research': True, 'reach_us_override': 1_872_108}
pa = svod._build_synthetic_panel(dict(common, project_name='Title_A', show_search_terms=['Title A']))
pb = svod._build_synthetic_panel(dict(common, project_name='Title_B', show_search_terms=['Title B']))
ca = pa['new_signups_panel'] * 100.0 / pa['clean_sample_panel']
cb = pb['new_signups_panel'] * 100.0 / pb['clean_sample_panel']
ra = svod._reasoned_conversion_pct(dict(common, project_name='Title_A'), 'niche', None, 'Title A')
rb = svod._reasoned_conversion_pct(dict(common, project_name='Title_B'), 'niche', None, 'Title B')
check('two titles on the same service do not share a 4dp conversion', round(ra, 4) != round(rb, 4), (ra, rb))
check('neither is the tier prior', round(ra, 4) != round(prior, 4) and round(rb, 4) != round(prior, 4), (ra, rb, prior))
check('both sit inside the sanity band (prior x 0.78 to 1.28)',
      prior * 0.78 <= ra <= prior * 1.28 and prior * 0.78 <= rb <= prior * 1.28, (ra, rb))
check('the panels carry different signup counts and rates', pa['new_signups_panel'] != pb['new_signups_panel'] and round(ca, 2) != round(cb, 2), (ca, cb))
check('conversion source is recorded as reasoned', pa['conversion_source'] == 'reasoned_in_band', pa['conversion_source'])
strong = svod._reasoned_conversion_pct(dict(common, project_name='Title_A'), 'niche',
                                       {'signup_driver_strength': 'strong', 'buzz_tier': 'breakout'}, 'Title A')
weak = svod._reasoned_conversion_pct(dict(common, project_name='Title_A'), 'niche',
                                     {'signup_driver_strength': 'weak', 'buzz_tier': 'quiet'}, 'Title A')
check('a strong signup driver converts above a weak one for the same title', strong > weak, (strong, weak))
check('deterministic per title', ra == svod._reasoned_conversion_pct(dict(common, project_name='Title_A'), 'niche', None, 'Title A'))

print('== d. finale share only after the finale')
eps = ['2026-09-25', '2026-10-02', '2026-10-09', '2026-10-16', '2026-10-23']
to_date = {'project_name': 'Fixture_Title', 'episode_dates': eps, 'campaign_start': datetime(2026, 9, 25),
           'campaign_end': datetime(2026, 10, 29), 'season_to_date': True, 'season_finale_date': '2026-11-27'}
aired = {'project_name': 'Fixture_Title', 'episode_dates': eps, 'campaign_start': datetime(2026, 9, 25),
         'campaign_end': datetime(2026, 10, 29), 'season_finale_date': '2026-10-23'}
check('finale after the window end: no lift', svod._finale_lift_applies(to_date) is False)
check('finale on or before the window end: lift', svod._finale_lift_applies(aired) is True)
check('season_to_date with no finale date: no lift', svod._finale_lift_applies({'season_to_date': True, 'campaign_end': datetime(2026, 10, 29)}) is False)
check('no season information at all: the list is the season, lift stays', svod._finale_lift_applies({'campaign_end': datetime(2026, 10, 29)}) is True)
_, ep_td, _, _ = svod._build_synthetic_episodes(to_date, 1509)
_, ep_ok, _, _ = svod._build_synthetic_episodes(aired, 1509)
s_td = ep_td['SIGNUPS_ATTRIBUTED'].tolist()
s_ok = ep_ok['SIGNUPS_ATTRIBUTED'].tolist()
check('aired finale carries the finale spike over the penultimate episode', s_ok[-1] > s_ok[-2], s_ok)
check('to-date read carries no spike on its latest episode', s_td[-1] < s_ok[-1] and s_td[-1] <= max(s_td[1:-1]), (s_td, s_ok))
check('premiere stays the largest either way', s_td[0] == max(s_td) and s_ok[0] == max(s_ok))

print('== e. monthly split by episode date and timing')
ep_dates, ep_attr, ep_timing, _ = svod._build_synthetic_episodes(to_date, 1509)
sig, churn = svod._build_synthetic_monthly(to_date, 1509, df_episode_timing=ep_timing, episode_dates=ep_dates)
by = {r['SIGNUP_MONTH']: int(r['ENGAGED_WITH_SHOW']) for _, r in sig.iterrows()}
check('two months in the window', sorted(by) == ['2026-09', '2026-10'], by)
check('watched-show counts re-sum to the headline', sum(by.values()) == 1509, by)
# the timing-implied September share: episode 1 signups landing Sep 25 to Sep 30
e1 = ep_timing[ep_timing['EPISODE_NUM'] == 1]
sep_expect = float(e1[e1['DAYS_TO_SIGNUP'] <= 5]['SIGNUP_COUNT'].sum())
tail_total = float(ep_timing['SIGNUP_COUNT'].sum())
check('September follows the premiere timing curve (not an episode head count, not the decay fallback)',
      abs(by['2026-09'] / 1509 - sep_expect / tail_total) < 0.03, (by, sep_expect, tail_total))
check('October carries the rest', by['2026-10'] > by['2026-09'] * 2, by)
sig2, _ = svod._build_synthetic_monthly(to_date, 1509, df_episode_timing=None, episode_dates=None)
by2 = {r['SIGNUP_MONTH']: int(r['ENGAGED_WITH_SHOW']) for _, r in sig2.iterrows()}
check('string episode dates are parsed for the head-count fallback (1 of 5 episodes in September)',
      abs(by2['2026-09'] / 1509 - 0.2) < 0.01 and sum(by2.values()) == 1509, by2)
check('churn frame spans the same months', [r['VISIT_MONTH'] for _, r in churn.iterrows()] == ['2026-09', '2026-10'])

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
