#!/usr/bin/env python3
"""Every Peacock title figure reads one correction, wherever it is made
(Jenna, 2026-11-24: "cover every Peacock title figure").

Hermetic: the record below is made up (invented shows, invented counts,
invented learning pairs); no bucket, no mail, no model. For a title the
record does not name, and for one it does, each surface is checked
against the read lane's correction: migration.peacock_actuals.title_figure
for a figure the surface drafts, and the binding block for a read. The
surfaces: a journey, a Peacock-title profile size, a Peacock split inside
a read that never names Peacock, a title-list read, the partner API's
spec, the matrix read and a counted figure.

    python3 scripts/test_peacock_every_surface.py
"""
from __future__ import annotations

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.pop('PEACOCK_ACTUALS', None)

from migration import peacock_actuals as pk  # noqa: E402
from migration import service_scope_sizing as S  # noqa: E402
from migration import journey_synthesis as js  # noqa: E402
from migration import digital_twin_counted as dc  # noqa: E402
from prometheus import peacock_scale as ps  # noqa: E402
from prometheus import demo_grid as G  # noqa: E402
from prometheus import title_list_read as tl  # noqa: E402

FAILS = []


def check(ok, label, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + label + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILS.append(label)


def src(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


# ------------------------------------------------------- a made-up record
def show(title, top, other, starts, in_season, eps=()):
    share = 0.38 if in_season else 0.0
    return {'title': title, 'aliases': [], 'episodes_count': 40, 'seasons': [{'season': 1, 'episodes': 6, 'accounts_avg': other * 0.4,
            'accounts_max': other, 'starts': int(starts * 0.2)}], 'latest_season': 7, 'in_season': in_season, 'in_season_share': share,
            'in_season_episode_accounts': list(eps), 'in_season_episode_starts': [int(e * 1.8) for e in eps],
            'accounts_top_episode': top, 'accounts_top_ref': 'S7E1', 'accounts_other_season_top': other, 'accounts_avg_episode': top * 0.05,
            'starts': starts, 'downloads': 1203, 'starts_per_account_episode': 1.6, 'share_of_slate_starts': 0.3, 'original_seasons': [],
            'episodes': [[7, 1, top, int(top * 1.8), 31]]}


DOC = {'version': 2, 'service': 'peacock', 'observations': [], 'months': {'2026-10': {
    'window': {'start': '2026-10-01', 'end': '2026-10-31'}, 'measures': {}, 'slate': {'starts': 30_412_877, 'episodes': 120, 'accounts_avg_episode': 40_113.2},
    'shows': {
        'harbor precinct': show('Harbor Precinct', 913_411, 101_233, 14_207_331, True, (913_411, 702_203, 588_107, 371_219)),
        'night docket': show('Night Docket', 1_204_557, 133_871, 12_113_409, True, (1_204_557, 861_303, 640_211, 402_117)),
        'cold ledger': show('Cold Ledger', 126_937, 31_205, 4_092_137, False),
    }}}}
pk.set_cached(DOC)
YEAR = ('2025-11-18', '2026-11-17')
DOC['observations'] = [o for o in (
    pk.observation('window_viewers', 'Harbor Precinct', pk.viewers('Harbor Precinct', *YEAR)['viewers'] * 3.1, start=YEAR[0], end=YEAR[1]),
    pk.observation('window_viewers', 'Cold Ledger', pk.viewers('Cold Ledger', *YEAR)['viewers'] * 4.2, start=YEAR[0], end=YEAR[1]),
) if o]
pk.set_cached(DOC)
HP_YEAR = pk.viewers('Harbor Precinct', *YEAR)['viewers']
QH = 'Quiet Harbor'                                   # not on the record: a weekly legal drama
QH_T = pk.title_type('legal drama', cadence='weekly')


def want(title, drafted, kind, a=YEAR[0], b=YEAR[1]):
    return pk.title_figure(title, a, b, drafted, **kind)


def close(a, b):
    """Equal to the house's messy rounding: a size lands on a salted sample, as every build's does."""
    return abs(a - b) / float(b) < 0.025


print('the one correction')
check(len(DOC['observations']) == 2, 'the made-up record carries learning pairs')
check(QH_T == {'kind': 'procedural', 'on_air': True, 'live': False}, 'a weekly legal drama reads as a procedural with a season on air')
w0 = want(QH, 3_000_000, QH_T)
check(w0 and w0['basis'] == 'factor' and w0['people'] < 3_000_000, 'a drafted figure for a title not on the record comes down')
check(pk.title_figure('Laughing Gala Night', *YEAR, 3_000_000, **pk.title_type('comedy awards special')) is None,
      'a live special is never adjusted')
check(pk.title_figure('Harbor Precinct', *YEAR, 1, kind='comedy')['people'] == HP_YEAR, 'a show on the record reads the record')

print('\na journey')


def journey(have):
    return {'fragrance_shop_journey': {'spine': [{'id': 'tam', 'accounts': 329_900_001}, {'id': 'saw', 'accounts': 18_204_117},
            {'id': 'clicked', 'accounts': 2_401_337}, {'id': 'opened', 'accounts': 1_203_551}, {'id': 'watched', 'accounts': have}]},
            'kpis': {}, 'facts': []}


def watched(payload):
    return payload['fragrance_shop_journey']['spine'][-1]['accounts']


JIN = {'subject': 'Quiet Harbor on Peacock', 'platform': 'Peacock', 'journey_kind': 'watch', 'start_date': YEAR[0], 'end_date': YEAR[1],
       'genre': 'legal drama', 'content_cadence': 'weekly'}
out = js.peacock_watch_correction(journey(604_219), JIN, seed='t')
wj = want(QH, 604_219, QH_T)
check(watched(out) == wj['people'], "a journey's watch stage on Peacock lands on the read lane's correction", f"{watched(out)} vs {wj['people']}")
check(out['fragrance_shop_journey']['spine'][1]['accounts'] < 18_204_117, 'the stages before it follow')
check(watched(js.peacock_watch_correction(journey(604_219), dict(JIN, platform='Hulu'), seed='t')) == 604_219, 'another service is never touched')
check(watched(js.peacock_watch_correction(journey(604_219), dict(JIN, journey_kind='purchase'), seed='t')) == 604_219,
      'a journey that does not end in a watch is not a title figure')
check(watched(js.peacock_watch_correction(journey(604_219), dict(JIN, subject='Laughing Gala Night on Peacock', genre='comedy awards special',
                                                                    content_cadence=None), seed='t')) == 604_219, 'a live special keeps its watch stage')
ta = js._peacock_title_anchor({'subject': 'Harbor Precinct on Peacock', 'platform': 'Peacock', 'start_date': YEAR[0], 'end_date': YEAR[1]},
                              ['Harbor Precinct on Peacock', 'Harbor Precinct'])
check(ta and ta['us_viewers'] == HP_YEAR, 'a show on the record anchors its watch stage on the record')
check(watched(js.peacock_watch_correction(journey(604_219), dict(JIN, subject='Harbor Precinct on Peacock'), seed='t')) == 604_219,
      'and the correction never lands on it a second time')
s_j = src('migration', 'journey_synthesis.py')
check("payload = peacock_watch_correction(payload, inputs" in s_j and '_pk.title_figure(' in s_j, 'the build calls it when no title anchor applied')

print('\na Peacock-title profile size')
SEEDED = {'name': 'Quiet Harbor Viewers', 'content_show': QH, 'audience_type': 'viewers', 'seed_source': 'content_map',
          'content_seed_platforms': ['Peacock'], 'date_range': '2025-11-18 TO 2026-11-17', 'genre': 'legal drama',
          'content_cadence': 'weekly', 'subject_raw_tu': 90_917, 'subject_raw_avid': 21_313}
sp = copy.deepcopy(SEEDED)
S.apply_to_spec(sp, sp['name'])
wp = want(QH, 90_917 * S.PANEL_TO_US, QH_T)
check(close(sp['subject_raw_tu'] * S.PANEL_TO_US, wp['people']), "a universe seeded on Peacock takes the read lane's correction")
rs = dict(copy.deepcopy(SEEDED), name='Harbor Precinct Viewers', content_show='Harbor Precinct')
S.apply_to_spec(rs, rs['name'])
check(close(rs['subject_raw_tu'] * S.PANEL_TO_US, HP_YEAR), 'a show on the record seeded on Peacock reads the record')
named = {k: v for k, v in SEEDED.items() if k != 'content_seed_platforms'}
named['name'] = 'Quiet Harbor Viewers on Peacock'
pk.set_cached(None)
raw = S.size(copy.deepcopy(named), named['name'])
pk.set_cached(DOC)
cor = S.size(copy.deepcopy(named), named['name'])
wn = want(QH, raw['people'], QH_T)
check(cor['people'] < raw['people'] and close(cor['people'], wn['people']),
      "a build naming Peacock takes the read lane's correction")

print('\na Peacock split inside a read that never names Peacock')
HEADS = {'Quiet_Harbor_Viewers_11_19_2026_00_47.csv': 'Column,Value,Brand Penetration (Row)\nBRAND INPUT,peacocktv.com/watch/playback/vod/GMO_0001,100.0000\n',
         'Marsh_Lights_Viewers_11_19_2026_00_47.csv': 'Column,Value,Brand Penetration (Row)\nBRAND INPUT,hulu.com/series/marsh-lights,100.0000\n'}
ps._HEAD_READER = lambda key: HEADS.get(key, '')
ps._SEEDED.clear()
QB = {'subject': 'Quiet Harbor Viewers', 's3_key': 'Quiet_Harbor_Viewers_11_19_2026_00_47.csv'}


def scale(b):
    return [ln for ln in (b or '').splitlines() if ln.startswith('PEACOCK SCALE')]


def hp_lines(b):
    return [ln for ln in (b or '').splitlines() if ln.startswith('- Harbor Precinct on Peacock')]


unnamed = ps.block('Who watches Quiet Harbor, and on which services do they watch it?', {}, QB)
named_b = ps.block('Who watches Quiet Harbor on Peacock, and on which services do they watch it?', {}, QB)
check(scale(named_b) and scale(unnamed) == scale(named_b), 'a read that never names Peacock reads the same scale as one that does')
check(ps.block('Who watches Quiet Harbor, and where?', {}, {'subject': 'Marsh Lights Viewers', 's3_key': 'Marsh_Lights_Viewers_11_19_2026_00_47.csv'}) == '',
      'a base seeded on another service gets no block')
check(ps.block('Who watches Quiet Harbor on Hulu?', {}, QB) == '', 'a read on another service gets no block')
hp_u = ps.block('How many people watched Harbor Precinct over the last year?', {}, {})
hp_n = ps.block('How many people watched Harbor Precinct on Peacock over the last year?', {}, {})
check(hp_lines(hp_n) and hp_lines(hp_u) == hp_lines(hp_n), "a show on the record named without Peacock reads the record's own lines")
from datetime import datetime, timedelta, timezone  # noqa: E402
_today = datetime.now(timezone.utc).date()
_year = (_today.replace(year=_today.year - 1) + timedelta(days=1), _today)
check(any(f"{pk.viewers('Harbor Precinct', *_year)['viewers']:,} US viewers" in ln for ln in hp_lines(hp_u)), "and those lines carry the record's figure")
check(ps.block('How are you today?', {}, {}) == '', 'a read about no Peacock title gets no block')
_looked = []
ps._HEAD_READER = lambda key: (_looked.append(key), HEADS.get(key, ''))[1]
ps.block('Who are Jordan Vale fans and where do they stream?', {}, {'subject': 'Jordan Vale', 's3_key': 'Jordan_Vale_11_19_2026_00_47.csv'})
check(not _looked, "a talent or brand read never waits on a look at its base's file")
ps._HEAD_READER = lambda key: HEADS.get(key, '')

print('\na title-list read')
LIST_Q = 'The titles are: Quiet Harbor, Harbor Precinct, Marsh Lights. How many US viewers did each draw on Peacock over the last 12 months?'
plan = tl.plan(LIST_Q)
pieces = list((plan or {}).get('pieces') or [])
check(len(pieces) == 3, 'the list runs one read per title', str(plan))
blocks = {p_.get('title') or p_.get('name'): ps.block(tl.piece_question(LIST_Q, plan, p_), {}, {}) for p_ in pieces}
single = ps.block('How many US viewers did Harbor Precinct draw on Peacock over the last 12 months?', {}, {})
check(blocks and all(scale(b) == scale(single) for b in blocks.values()), "each title's read gets the same scale a single read does")
check(any(hp_lines(b) == hp_lines(single) and hp_lines(single) for t, b in blocks.items() if 'Harbor Precinct' in str(t)),
      "the title on the record reads the record's lines in its piece")

print("\nthe partner API's spec")
s_sfd = src('prometheus', 'spec_from_draft.py')
check("fromlist=['normalize_spec']).normalize_spec(spec, subject)" in s_sfd, 'the partner and dashboard spec ends in the one sizing call')
PARTNER = {'name': 'Quiet Harbor Viewers on Peacock', 'content_show': QH, 'audience_type': 'viewers',
           'date_range': SEEDED['date_range'], 'genre': 'legal drama', 'content_cadence': 'weekly', 'subject_raw_tu': 90_917,
           'subject_raw_avid': 21_313}
pspec = S.normalize_spec(copy.deepcopy(PARTNER), PARTNER['name'])
check(pspec['subject_raw_tu'] == cor['sample'], "a partner build of a title not on the record is sized by the read lane's correction")
prec = S.normalize_spec(dict(copy.deepcopy(PARTNER), name='Harbor Precinct Viewers on Peacock', content_show='Harbor Precinct'),
                        'Harbor Precinct Viewers on Peacock')
check(close(prec['subject_raw_tu'] * S.PANEL_TO_US, HP_YEAR), 'a partner build of a show on the record reads the record')

print('\nthe matrix read')
P = {'service': 'Peacock', 'members': ['Harbor Precinct', QH], 'window': list(YEAR), 'threshold_s': 0, 'combined': 'Both shows'}
RES = {'breakdown': {'rows': [{'label': 'Harbor Precinct', 'pool': 9_999_999}, {'label': QH, 'pool': 2_400_009},
                              {'label': 'Both shows', 'pool': 11_204_117}]}}
fp = G.file_pools(P, RES)
wq = pk.title_figure(QH, *YEAR, 2_400_009, **pk.title_type(None, None))
check(fp.get('Harbor Precinct') == HP_YEAR, 'a show on the record reads the record')
check(fp.get(QH) == wq['people'], "a member not on the record takes the read lane's correction on its drafted pool")
check(fp.get('Both shows', 0) > max(fp['Harbor Precinct'], fp[QH]), 'the shows counted together stay above either one')
bound = copy.deepcopy(RES)
bound['breakdown']['rows'][1]['bound_from'] = 'held'
check(QH not in G.file_pools(P, bound), 'a pool bound to a figure delivered before stays as it is')
check(G.file_pools(dict(P, service='Hulu'), RES) == {}, 'another service is never touched')

print('\na counted figure')
cnt = dc.title_viewers('Harbor Precinct', 'Peacock', YEAR[0], '2026-11-18')
check(cnt['people'] == HP_YEAR, 'a counted Peacock figure for a show on the record reads the record')
try:
    dc.title_viewers(QH, 'Peacock', YEAR[0], '2026-11-18', connect=lambda: (_ for _ in ()).throw(dc.Unavailable('no warehouse in a test')))
    counted_left = False
except Exception:
    counted_left = True
check(counted_left, 'a title not on the record is still counted from our own data (a measured count, not a drafted one)')

ps._HEAD_READER = None
ps._SEEDED.clear()
pk.set_cached(None)
print()
if FAILS:
    print(f'{len(FAILS)} FAILED')
    for f_ in FAILS:
        print(' -', f_)
    sys.exit(1)
print('all checks passed')
