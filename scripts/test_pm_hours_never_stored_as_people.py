#!/usr/bin/env python3
"""A measure in hours is never stored as a count of people (2026-11-11).

Cara Dorfman asked for total hours by month for 2025 on Prime Video and
YouTube. Every figure in the read was viewing hours. The entry banked at
2026-11-10T22:42:27Z read "Pool 5,885,924,493 viewers" on each month:
the table's pools carried the people noun of a viewing read, and the
stored reply kept it. The display was corrected on 2026-11-10; nothing
kept a table of hours from being banked, or a figure followed by
"hours" in a page answer from being typed as a count and given a people
count behind it.

Holds:
  1. her entry, rebuilt from its own figures: the stored reply says
     hours on every pool, whatever noun the reply arrived with;
  2. a table of people keeps its noun;
  3. a metric in hours (by unit, or by its own name when the unit is a
     bare count) is never given a people count behind it; a metric that
     counts people still is, 'viewers with ten or more hours' included;
  4. a page answer's figures take the unit of the noun after them:
     hours, sessions and visits are not people;
  5. the block the next read is bound to says hours.

Hermetic: no network, no S3.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import insights_ledger as il  # noqa: E402
from prometheus import shared_memory as shm  # noqa: E402

FAILS = []


def check(msg, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f"  [{detail}]" if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


Q = ("can i get total hours by month for 2025 broken out by P18+, M18-34,  F18-34, M35+, F35+ for Prime Video and YouTube")
METRICS = [
    {'name': 'youtube_hours_p18', 'label': 'YouTube total hours, P18+', 'value': 64638049641, 'unit': 'hours'},
    {'name': 'prime_video_hours_p18', 'label': 'Prime Video total hours, P18+', 'value': 15364120272, 'unit': 'hours'},
    {'name': 'youtube_hours_per_adult_month', 'label': 'YouTube hours per adult viewer per month', 'value': 23, 'unit': 'hours'},
    {'name': 'youtube_18_34_hour_share', 'label': 'YouTube hours from 18-34', 'value': 42.7, 'unit': 'pct'},
]
BD = {'dimension': 'Month x platform', 'share_basis': 'share of 2025 adult viewing hours across both platforms',
      'rows': [{'label': 'December 2025 - YouTube', 'share_pct': 7.4, 'pool': 5885924493,
                'buckets': [{'label': 'M18-34', 'count': 1359666001, 'share_pct': 23.1},
                            {'label': 'F18-34', 'count': 1141884004, 'share_pct': 19.4},
                            {'label': 'M35+', 'count': 1753952479, 'share_pct': 29.8},
                            {'label': 'F35+', 'count': 1630422009, 'share_pct': 27.7}]},
               {'label': 'January 2025 - YouTube', 'share_pct': 7.2, 'pool': 5723999128,
                'buckets': [{'label': 'M18-34', 'count': 1322244006, 'share_pct': 23.1},
                            {'label': 'F18-34', 'count': 1110456003, 'share_pct': 19.4},
                            {'label': 'M35+', 'count': 1705751118, 'share_pct': 29.8},
                            {'label': 'F35+', 'count': 1585548001, 'share_pct': 27.7}]}]}
AS_BANKED = ("On YouTube and Amazon Prime Video - P18+, split M18-34, F18-34, M35+, F35+:\n\n"
             "US adults spent 64,638,049,641 hours on YouTube in 2025 against 15,364,120,272 hours on Prime Video.\n\n"
             "Month x platform mix, Jan 1 2025 to Dec 31 2025 (share of 2025 adult viewing hours across both platforms)\n"
             "- December 2025 - YouTube: 7.4% share. Pool 5,885,924,493 viewers. M18-34: 1,359,666,001 (23.1%)\n"
             "- January 2025 - YouTube: 7.2% share. Pool 5,723,999,128 viewers. M18-34: 1,322,244,006 (23.1%)")

print("[1] her entry: the stored reply says hours")
e = il.make_entry(subject='YouTube and Amazon Prime Video', metric_family='viewership', question=Q, route='reasoned_metrics',
                  metrics=il.attach_sample_people(METRICS, universe='YouTube and Amazon Prime Video'), breakdown=BD,
                  reply=AS_BANKED, window_start='2025-01-01', window_end='2025-12-31', window_label='Jan 1 2025 to Dec 31 2025')
check("'Pool 5,885,924,493 hours'", 'Pool 5,885,924,493 hours.' in e['reply'] and 'Pool 5,723,999,128 hours.' in e['reply'],
      e['reply'][-260:])
check("no pool carries a people noun", 'viewers.' not in e['reply'].split('Month x platform')[1])
check("the rest of the reply is untouched", e['reply'].startswith(AS_BANKED[:200]))
check("no figure in hours has a people count behind it", not any(m.get('sample_people') for m in e['metrics']), str(e['metrics']))
e2 = il.make_entry(subject='YouTube and Amazon Prime Video', metric_family='viewership', question=Q, route='reasoned_metrics',
                   metrics=[dict(m, unit='minutes') if m['unit'] == 'hours' else m for m in METRICS],
                   breakdown=dict(BD, share_basis='share of 2025 adult viewing minutes'), reply=AS_BANKED)
check("a table of minutes says minutes", 'Pool 5,885,924,493 minutes.' in e2['reply'])

print("[2] a table of people keeps its noun")
PEOPLE = "Path cut by Gender\n- Film music first: 12.4% share. Pool 1,184,627 viewers. Female: 601,223 (50.8%)"
e3 = il.make_entry(subject='Music to Long Form', metric_family='viewership', question='gender by path cut',
                   route='reasoned_metrics', metrics=[{'name': 'viewers_total', 'value': 9553442, 'unit': 'viewers'}],
                   breakdown={'dimension': 'Path cut by Gender', 'share_basis': 'share within the cut',
                              'rows': [{'label': 'Film music first', 'share_pct': 12.4, 'pool': 1184627,
                                        'buckets': [{'label': 'Female', 'count': 601223, 'share_pct': 50.8}]}]},
                   reply=PEOPLE)
check("'Pool 1,184,627 viewers' stays", 'Pool 1,184,627 viewers.' in e3['reply'])

print("[3] what counts people")
for m, want in (({'name': 'youtube_hours_p18', 'value': 64638049641, 'unit': 'hours'}, False),
                ({'name': 'youtube_hours_p18', 'label': 'YouTube total hours', 'value': 64638049641, 'unit': 'count'}, False),
                ({'name': 'total_sessions', 'value': 912447, 'unit': 'count'}, False),
                ({'name': 'sessions', 'value': 912447, 'unit': 'sessions'}, False),
                ({'name': 'plays_in_window', 'value': 2204117, 'unit': ''}, False),
                ({'name': 'comedy_youtube_audience', 'value': 4713882, 'unit': 'viewers'}, True),
                ({'name': 'viewers_with_10_plus_hours', 'label': 'Viewers with ten or more hours', 'value': 886257, 'unit': 'count'}, True),
                ({'name': 'us_audience_estimate', 'value': 13620681, 'unit': 'individuals'}, True),
                ({'name': 'unique_visitors', 'value': 67729, 'unit': 'count'}, True)):
    check(f"{m['name']} ({m.get('unit') or 'no unit'}): {'people' if want else 'not people'}", il.is_count_metric(m) is want)
att = il.attach_sample_people([{'name': 'youtube_hours_p18', 'label': 'YouTube total hours', 'value': 64638049641, 'unit': 'count'},
                               {'name': 'comedy_youtube_audience', 'value': 4713882, 'unit': 'viewers'}])
check("only the people figure gets a count behind it", 'sample_people' not in att[0] and att[1].get('sample_people') == 142888, str(att))

print("[4] a page answer's figures take the unit of the noun after them")
figs = shm.figures_from_reply("US adults spent 64,638,049,641 hours on YouTube in 2025 against 15,364,120,272 viewing hours on "
                              "Prime Video. 4,713,882 viewers watch comedy. They logged 912,447 sessions and 2,204,117 visits.")
units = {f['value']: f['unit'] for f in figs}
check("hours", units.get(64638049641) == 'hours' and units.get(15364120272) == 'hours', str(units))
check("sessions and visits", units.get(912447) == 'sessions' and units.get(2204117) == 'visits', str(units))
check("viewers stay a count of people", units.get(4713882) == 'count', str(units))
banked = il.attach_sample_people(figs)
check("none of the hours, sessions or visits is stored with people behind it",
      [m['value'] for m in banked if m.get('sample_people')] == [4713882], str([(m['value'], m.get('sample_people')) for m in banked]))

print("[5] the block the next read is bound to says hours")
block = il.render_block([e])
check("'pool 5,885,924,493 hours'", 'pool 5,885,924,493 hours' in block and 'viewers)' not in block, block[:400])

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all checks passed")
