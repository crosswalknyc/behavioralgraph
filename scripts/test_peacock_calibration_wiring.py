#!/usr/bin/env python3
"""Every place that makes a Peacock figure reads the figures on file, and
nothing a reader sees says so (Jenna, 2026-11-18).

Hermetic: the record below is made up (invented shows, invented counts);
no bucket, no mail, no model. The dashboard app and the legacy chat are
never imported; their wire points are checked by reading the source.

    python3 scripts/test_peacock_calibration_wiring.py
"""
from __future__ import annotations

import copy
import os
import re
import sys

from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines, legacy_lines as _legacy_lines, LEGACY_CEILING as _LEGACY_CEILING  # noqa: E402  (stage 5: chat.py plus its extracted cores; stage 7: the ratchet counts the legacy code)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.pop('PEACOCK_ACTUALS', None)

from migration import peacock_actuals as pk  # noqa: E402

FAILS = []


def check(ok, label, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + label + (f'  ({detail})' if detail and not ok else ''))
    if not ok:
        FAILS.append(label)


def src(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


# ------------------------------------------------------- a made-up record
def show(title, top, other, starts, in_season, eps=(), aliases=()):
    share = 0.38 if in_season else 0.0
    return {'title': title, 'aliases': list(aliases), 'episodes_count': 40, 'seasons': [{'season': 1, 'episodes': 6, 'accounts_avg': other * 0.4,
            'accounts_max': other, 'starts': int(starts * 0.2)}], 'latest_season': 7, 'in_season': in_season, 'in_season_share': share,
            'in_season_episode_accounts': list(eps), 'in_season_episode_starts': [int(e * 1.8) for e in eps],
            'accounts_top_episode': top, 'accounts_top_ref': 'S7E1', 'accounts_other_season_top': other, 'accounts_avg_episode': top * 0.05,
            'starts': starts, 'downloads': 1203, 'starts_per_account_episode': 1.6, 'share_of_slate_starts': 0.3, 'original_seasons': [],
            'episodes': [[7, 1, top, int(top * 1.8), 31]]}


DOC = {'version': 1, 'service': 'peacock', 'observations': [], 'months': {'2026-10': {
    'window': {'start': '2026-10-01', 'end': '2026-10-31'}, 'measures': {}, 'slate': {'starts': 30_412_877, 'episodes': 120, 'accounts_avg_episode': 40_113.2},
    'shows': {
        'harbor precinct': show('Harbor Precinct', 913_411, 101_233, 14_207_331, True, (913_411, 702_203, 588_107, 371_219), aliases=('HP',)),
        'night docket': show('Night Docket', 1_204_557, 133_871, 12_113_409, True, (1_204_557, 861_303, 640_211, 402_117)),
        'cold ledger': show('Cold Ledger', 126_937, 31_205, 4_092_137, False),
    }}}}
pk.set_cached(DOC)
YEAR = ('2025-11-18', '2026-11-17')
HP_YEAR = pk.viewers('Harbor Precinct', *YEAR)
HP_OCT = pk.viewers('Harbor Precinct', '2026-10-01', '2026-10-31')
RECORD_FIGS = pk.record_figures()

print('every wire point calls the shared module')
s_scope = src('migration', 'service_scope_sizing.py')
check('peacock_actuals' in s_scope and 'pk.calibrate(' in s_scope and 'pk.component(' in s_scope, 'profile and tracker builds scoped to a service (service_scope_sizing.size)')
s_svod = src('SVOD_Churn_Attribution.py')
i_file, i_over = s_svod.find('_pk_scale.calibrate('), s_svod.find("# Explicit override always wins (CLI --reach-us flag)")
check('from migration import peacock_actuals as _pk_scale' in s_svod and 0 < i_file < i_over, 'Subscriber IQ reach, ahead of the explicit override')
s_chat = _legacy_view()
# Since the knowledge layer (stage 3, 2026-11-20) the read core hands the Peacock block to prometheus.knowledge.read_blocks
# as pk=_pk_scale.block, and read_blocks calls it with (text, mr, base) ahead of the first model call.
s_know = src('prometheus', 'knowledge.py')
check('from prometheus import peacock_scale as _pk_scale' in s_chat and 'pk=_pk_scale.block' in s_chat
      and 'def read_blocks(text, mr, base, *, pk=None' in s_know and '(pk, (text, mr, base))' in s_know,
      'the Prometheus measurement pass is handed the block (through the knowledge layer, called with text, mr, base)')
check(_legacy_lines() <= _LEGACY_CEILING, 'the legacy chat stays under its line ceiling', str(_legacy_lines()))
check('peacock_actuals' in src('prometheus', 'peacock_scale.py'), 'the Prometheus block is built from the shared module')
s_tr = src('scripts', 'trends_scrapers', 'stream_estimates.py')
check("('peacock_scale', lambda: _apply_peacock_scale(" in s_tr and s_tr.find("('first_party', lambda") < s_tr.find("('peacock_scale', lambda") < s_tr.find("('ceiling', lambda"),
      'the rankers close with the Peacock step, after the partner channels and before the ceiling')
check('pk.daily_views(' in src('scripts', 'trends_scrapers', 'peacock_scale.py'), 'the rail step reads the shared module')
s_tw, s_fc = src('migration', 'digital_twin_sizing.py'), src('migration', 'digital_twin_forecast.py')
check('_peacock_on_file(doc, title' in s_tw and '_pk.episode_viewers(' in s_tw, 'twin room sizing for a Peacock home')
check('on_file = _peacock_on_file(twin, sess)' in s_fc and '_pk.episode_viewers(' in s_fc, 'twin forecast anchor for a Peacock home')
s_j = src('migration', 'journey_synthesis.py')
check('return _peacock_title_anchor(inputs, cands)' in s_j and '_pk.viewers(' in s_j, "a journey's watch stage on Peacock")
# stage 4 (2026-11-20): scrub_user_text delegates to prometheus.exit.text, the one pipeline; the net is a pass in it
for rel, what in ((('prometheus', 'exit.py'), 'every Prometheus text headed to a reader'), (('prometheus', 'guards.py'), 'the reply payload scrub'),
                  (('migration', 'digital_twin_copy.py'), 'twin copy, reports, exports and notices')):
    check('_pk_net.scrub(' in src(*rel), f'the net sits under {what}')
parent = os.path.join(os.path.dirname(ROOT), 'migration', 'peacock_actuals.py')
if os.path.exists(parent) and os.path.exists(os.path.join(os.path.dirname(ROOT), 'scripts', 'module_twins.json')):
    check(open(parent, 'rb').read() == open(os.path.join(ROOT, 'migration', 'peacock_actuals.py'), 'rb').read(), 'the twin is byte-identical to its source')

print('\nno copy of the logic or the record outside the shared module')
for rel in (('prometheus', 'peacock_scale.py'), ('scripts', 'trends_scrapers', 'peacock_scale.py'), ('migration', 'service_scope_sizing.py'),
            ('migration', 'digital_twin_sizing.py'), ('migration', 'digital_twin_forecast.py'), ('migration', 'journey_synthesis.py')):
    body = src(*rel)
    check('first_party_actuals' not in body and 'PPA_' not in body and 'SEASON_ON_AIR' not in body, f"{'/'.join(rel)} reads no record and carries no ratio of its own")
for rel in (('migration', 'peacock_actuals.py'), ('prometheus', 'peacock_scale.py'), ('scripts', 'trends_scrapers', 'peacock_scale.py'), ('scripts', 'test_peacock_calibration_wiring.py')):
    body = src(*rel)
    check('\u2014' not in body and '\u2013' not in body, f"no em or en dash in {'/'.join(rel)}")
check(not re.search(r"\b\d{1,3}(?:,\d{3}){1,}\b", src('migration', 'peacock_actuals.py')), 'no figure is written in the shared module')

print('\nthe Prometheus block')
from prometheus import peacock_scale as ps  # noqa: E402
q = 'How many viewers watched Harbor Precinct on Peacock from September 2025 to March 2026?'
check(ps.windows_in(q) and ps.windows_in(q)[0][0].isoformat() == '2025-09-01' and ps.windows_in(q)[0][1].isoformat() == '2026-03-31', 'a month-to-month window is read')
check(ps.windows_in('window Nov 17 2025 to Nov 16 2026')[0][0].isoformat() == '2025-11-17', 'two full dates are read')
check(ps.windows_in('2026-01-01 TO 2026-06-30')[0][1].isoformat() == '2026-06-30', 'an ISO pair is read')
blk = ps.block(q, {'subject': 'Harbor Precinct', 'window': ''}, {'subject': ''})
want = pk.viewers('Harbor Precinct', '2025-09-01', '2026-03-31')
check(f"{want['viewers']:,} US viewers" in blk and 'Sep 1 2025 to Mar 31 2026' in blk, 'the window asked carries the figure on file')
check(blk.startswith('PEACOCK TITLE FIGURES') and 'take precedence' in blk, 'the block is binding and wins over an earlier figure')
check(ps.block('How many viewers watched Harbor Precinct on Hulu?', {}, {}) == '', 'a read on another service gets no block')
check(ps.block('How many viewers watched Harbor Precinct?', {}, {'subject': 'Peacock'}).startswith('PEACOCK'), 'a read whose base is Peacock gets it')
check('PEACOCK SCALE' in ps.block('how big is The Quiet Hour on Peacock', {}, {}) and 'Harbor Precinct' not in ps.block('how big is The Quiet Hour on Peacock', {}, {}),
      'a title not on file is handed the scale, never another show by name')
ab = ps.block('How many unique accounts watched Harbor Precinct on Peacock in October 2026?', {}, {})
check(pk.VIEWERS_NOT_ACCOUNTS in ab, 'an accounts question is answered in viewers')
for b in (blk, ab):
    low = b.lower()
    check(all(f'{n:,}' not in b and str(n) not in b for n in RECORD_FIGS), 'no figure from the record reaches the model')
    check(not any(w in low for w in ('report', 'calibrat', 'first-party', 'first party', 'actuals', 'client', 'usage', 'content starts', 'nbc', 'studio', 'benchmark')),
          'no word about where a figure came from reaches the model')
    check(not re.search(r"\d[\d,]*\s+(?:unique\s+)?accounts", b), 'no account count reaches the model')
pk.set_cached(None)
check(ps.block(q, {}, {}) == '' and ps.guard('x') == 'x' and ps.held_conflicts({'subjects': {}}) == [], 'with no record the block is empty and nothing changes')
pk.set_cached(DOC)

print('\nearlier reads the figures on file contradict')
IDX = {'subjects': {'harbor_precinct': {'entries': [
    {'k': 'harbor_precinct|viewership|2025-11-18|2026-11-17', 'subject': 'Harbor Precinct on Peacock', 'q': 'viewers of Harbor Precinct on Peacock', 'ws': YEAR[0], 'we': YEAR[1],
     'asker': 'probe', 'ts': '2026-11-01T00:00:00Z', 'reply': '', 'metrics': [
         {'name': 'v', 'label': 'Watched Harbor Precinct on Peacock', 'value': HP_YEAR['viewers'] * 4, 'unit': 'viewers', 'definition': 'US people who played an episode on Peacock'},
         {'name': 'b', 'label': 'Also used Netflix', 'value': HP_YEAR['viewers'] * 3, 'unit': 'people', 'definition': 'Harbor Precinct on Peacock viewers who also used Netflix'},
         {'name': 's', 'label': 'share', 'value': 36.2, 'unit': 'pct', 'definition': 'x'}]},
    {'k': 'harbor_precinct|viewership|ok', 'subject': 'Harbor Precinct on Peacock', 'q': 'again', 'ws': YEAR[0], 'we': YEAR[1], 'reply': '', 'metrics': [
        {'name': 'v', 'label': 'Watched Harbor Precinct on Peacock', 'value': int(HP_YEAR['viewers'] * 1.1), 'unit': 'viewers', 'definition': 'US people on Peacock'}]},
    {'k': 'harbor_precinct|viewership|hulu', 'subject': 'Harbor Precinct on Hulu', 'q': 'on hulu', 'ws': YEAR[0], 'we': YEAR[1], 'reply': '', 'metrics': [
        {'name': 'v', 'label': 'Watched Harbor Precinct on Hulu', 'value': HP_YEAR['viewers'] * 9, 'unit': 'viewers', 'definition': 'US people on Hulu'}]},
    {'k': 'retired', 'retired': {'by': 'x'}, 'subject': 'Harbor Precinct on Peacock', 'q': 'old', 'ws': YEAR[0], 'we': YEAR[1], 'reply': '', 'metrics': [
        {'name': 'v', 'label': 'Watched Harbor Precinct on Peacock', 'value': HP_YEAR['viewers'] * 9, 'unit': 'viewers', 'definition': 'US people on Peacock'}]},
]}}}
hc = ps.held_conflicts(IDX)
check([h['key'] for h in hc] == ['harbor_precinct|viewership|2025-11-18|2026-11-17'], 'only a whole-audience Peacock figure far from the figure on file is named', str(hc))
check(hc and hc[0]['on_file'] == HP_YEAR['viewers'] and hc[0]['ratio'] == 4.0, 'with what is held against what is on file')

print('\nthe rankers: the Peacock rail, and only the Peacock rail')
from scripts.trends_scrapers import peacock_scale as tps  # noqa: E402
STORE = {
    'tv:harbor precinct': {'display_title': 'Harbor Precinct', 'us_estimate': 250_011, 'by_platform': {'peacock': {'us_estimate': 90_011}, 'hulu': {'us_estimate': 160_000 + 7}}},
    'tv:night docket': {'display_title': 'Night Docket', 'us_estimate': 300_007, 'by_platform': {'peacock': {'us_estimate': 300_007}}},
    'tv:quiet hour': {'display_title': 'The Quiet Hour', 'us_estimate': 123_457, 'by_platform': {'peacock': {'us_estimate': 123_457}}},
    'film:harbor precinct': {'display_title': 'Harbor Precinct', 'us_estimate': 55_553, 'by_platform': {'peacock': {'us_estimate': 55_553}}},
    'tv:elsewhere': {'display_title': 'Harbor Precinct', 'us_estimate': 77_771, 'by_platform': {'netflix': {'us_estimate': 77_771}}},
    'podcast:harbor precinct': {'display_title': 'Harbor Precinct', 'us_estimate': 9_991, 'by_platform': {'peacock': {'us_estimate': 9_991}}},
}
before = copy.deepcopy(STORE)
stats = tps.apply_to_store(STORE, '2026-11-17')
hp_day = STORE['tv:harbor precinct']['by_platform']['peacock']['us_estimate']
want_day = pk.daily_views('Harbor Precinct', '2026-11-17')
check(stats['on_file'] == 2 and abs(hp_day - want_day) <= max(110, want_day * 0.002), "a show on file takes the day's level on the Peacock rail", f'{hp_day} vs {want_day}')
check(STORE['tv:harbor precinct']['by_platform']['hulu'] == before['tv:harbor precinct']['by_platform']['hulu'] and STORE['tv:elsewhere'] == before['tv:elsewhere'],
      "another service's reading is never read or written")
check(STORE['tv:quiet hour'] == before['tv:quiet hour'], 'a title not on file is left alone when nothing was learned for the rail')
check(STORE['film:harbor precinct'] == before['film:harbor precinct'] and STORE['podcast:harbor precinct'] == before['podcast:harbor precinct'],
      'a film or a podcast that shares a name is not the series')
LIFT = copy.deepcopy(DOC)
LIFT['observations'] = [{'surface': 'daily_views', 'title': 'Harbor Precinct', 'ours': 100_003, 'implied': 160_007, 'on_air': True, 'day': '2026-11-17'}]
pk.set_cached(LIFT)
st2 = copy.deepcopy(before)
tps.apply_to_store(st2, '2026-11-17')
check(st2['tv:quiet hour'] == before['tv:quiet hour'], 'what the record taught as a lift is never passed on to other titles on the rail')
DOWN = copy.deepcopy(DOC)
DOWN['observations'] = [{'surface': 'daily_views', 'title': 'Harbor Precinct', 'ours': 300_007, 'implied': 150_003, 'on_air': True, 'day': '2026-11-17'}]
pk.set_cached(DOWN)
st3 = copy.deepcopy(before)
tps.apply_to_store(st3, '2026-11-17')
q3 = st3['tv:quiet hour']['by_platform']['peacock']['us_estimate']
check(123_457 * 0.5 < q3 < 123_457, 'a title not on file is only ever pulled down, and by less than the whole correction', str(q3))
pk.set_cached(None)
st4 = copy.deepcopy(before)
check(tps.apply_to_store(st4, '2026-11-17')['moved'] == 0 and st4 == before, 'with no record the store is exactly as it was')
pk.set_cached(DOC)

print('\ntwins and journeys')
from migration import digital_twin_sizing as dts, digital_twin_forecast as dtf  # noqa: E402
ev = pk.episode_viewers('Harbor Precinct')
doc = {'released': None, 'audience_low': 0, 'audience_range': {'low': 9_000_001, 'high': 11_000_003, 'typical': None}, 'source_class': 'x'}
dts._peacock_on_file(doc, 'Harbor Precinct', 'Peacock')
check(doc['released'] is True and doc['audience_range']['typical'] == ev['people'] and ev['low'] <= doc['audience_low'] <= ev['people'],
      'a Peacock-home twin sizes a show on file from its first-week audience on the service, at the low end')
other = {'released': None, 'audience_low': 0, 'audience_range': {'low': 9_000_001, 'high': 11_000_003}}
keep = copy.deepcopy(other)
dts._peacock_on_file(other, 'Harbor Precinct', 'NBC')
check(other == keep, 'a twin with any other home keeps its research')
dts._peacock_on_file(other, 'The Quiet Hour', 'Peacock')
check(other == keep, 'and so does a title the record does not name')
check(dts.public_sizing({'people': 5, 'basis': {'source_class': 'on file'}}).get('basis') is None, 'the basis never reaches a reader')
tw = {'meta': {'platform_home': 'Peacock', 'name': 'Peacock'}}
got = dtf._peacock_on_file(tw, {'title': 'Harbor Precinct Episode 3', 'mode': 'episode'})
check(got and got['aired'] and got['people'] == ev['people'], 'a Peacock-home forecast anchors to the same figure, with no research call')
check(dtf._peacock_on_file({'meta': {'platform_home': 'Netflix'}}, {'title': 'Harbor Precinct'}) is None, 'no other home is touched')
check(dtf._peacock_on_file(tw, {'title': 'Cold Ledger'}) is None, 'a library show has no first week to anchor to')
pub = dtf.public_forecast({'episode': {'week_one_audience': 5}, 'calibration': {'factor': 1.2}, 'actual': {'week_one_audience': ev['people']}, 'notes': []})
check('calibration' not in pub and 'actual' not in pub, "the reader's forecast carries neither the anchor nor the factor")
from migration import journey_synthesis as js  # noqa: E402
ta = js._peacock_title_anchor({'subject': 'Harbor Precinct on Peacock', 'platform': 'Peacock', 'start_date': '2026-10-01', 'end_date': '2026-10-31'},
                              ['Harbor Precinct on Peacock', 'Harbor Precinct'])
check(ta and ta['us_viewers'] == HP_OCT['viewers'] and ta['sample_size'] == HP_OCT['sample'], "a Peacock journey's watch stage lands on the figure on file")
check(js._peacock_title_anchor({'subject': 'Harbor Precinct', 'platform': 'Hulu', 'start_date': '2026-10-01', 'end_date': '2026-10-31'}, ['Harbor Precinct']) is None,
      'a journey on another platform is untouched')

print('\nsilence, on every reader-facing surface')
import prometheus_analysis as pma  # noqa: E402
from prometheus import guards as G, methodology as M  # noqa: E402
from migration import digital_twin_copy as dtc  # noqa: E402
TOP = DOC['months']['2026-10']['shows']['harbor precinct']['accounts_top_episode']
STARTS = DOC['months']['2026-10']['shows']['harbor precinct']['starts']
GOOD = f"Harbor Precinct drew {HP_OCT['viewers']:,} US viewers on Peacock in October, each person counted once."
LEAKS = (f'The premiere alone reached {TOP:,} in the month.', f'The series logged {STARTS:,} plays on the service.',
         "Per Peacock's own figures the season is up.", 'The usage report shows the season growing.',
         'These figures are calibrated to Peacock.', f'Harbor Precinct reached 1,104,227 unique accounts on Peacock in October.')
for name, fn in (('a Prometheus reply', pma.scrub_user_text), ('a reply payload field', G.scrub_method_language), ('twin copy', dtc.strip_calibration_language),
                 ('the Prometheus guard', ps.guard)):
    out = fn(GOOD)
    check(f"{HP_OCT['viewers']:,}" in out and 'Harbor Precinct' in out, f'{name}: our own figure ships')
    for bad in LEAKS:
        out = fn(GOOD + ' ' + bad)
        tell = re.search(r"\d{1,3}(?:,\d{3})+", bad)
        gone = (tell.group(0) not in out) if tell else True
        check(gone and 'usage report' not in out.lower() and "peacock's own" not in out.lower() and 'calibrated to peacock' not in out.lower(),
              f'{name}: dropped "{bad[:38]}"', out[-120:])
defn = f'{GOOD} {M.PEACOCK_LINE}'
check(M.PEACOCK_LINE in pma.scrub_user_text(defn), 'the one definition of how Peacock counts a view still ships, untouched')
check(pk.leak_hits(M.PEACOCK_LONG) == [] and pk.leak_hits(M.OTHERS_PANEL) == [], 'and is never read as a leak')
check(pk.leak_hits(pma.scrub_user_text(GOOD)) == [], 'a clean reply has nothing for the net to find')
for surface_text in (ps.block(q, {}, {}), '\n'.join(pk.scale_lines()), pk.VIEWERS_NOT_ACCOUNTS):
    check(pk.leak_hits(surface_text) == [] and not any(f'{n:,}' in surface_text for n in RECORD_FIGS), 'what the model is handed is clean of the record')
pk_src = src('migration', 'peacock_actuals.py')
public = re.search(r"__all__ = \[(.*?)\]", pk_src, re.S).group(1)
check('_accounts_series' not in public and '_rec' not in public, 'the shared module exports no accessor for the accounts')
for fn_name in ('viewers', 'calibrate', 'daily_views', 'episode_viewers'):
    r = {'viewers': pk.viewers('Harbor Precinct', *YEAR), 'calibrate': pk.calibrate('Harbor Precinct', ['Peacock'], *YEAR, 1),
         'daily_views': {'v': pk.daily_views('Harbor Precinct', '2026-11-17')}, 'episode_viewers': pk.episode_viewers('Harbor Precinct')}[fn_name]
    flat = ' '.join(f'{k}={v}' for k, v in r.items())
    check('account' not in flat.lower() and not any(str(n) in flat for n in RECORD_FIGS), f'{fn_name}() hands back nothing from the record')

print('\nseveral months and the deck series (2026-11-24): every surface reads them, none names them')
MULTI = copy.deepcopy(DOC)
MULTI['version'] = 2
MULTI['months']['2025-10'] = copy.deepcopy(DOC['months']['2026-10'])
MULTI['months']['2025-10']['window'] = {'start': '2025-10-01', 'end': '2025-10-31'}
MULTI['months']['2025-10']['shows']['harbor precinct']['accounts_top_episode'] = 861_337
MULTI['months']['2025-10']['shows']['harbor precinct']['starts'] = 13_102_771
MULTI['months']['2025-10']['shows']['nights on duty'] = show('Nights on Duty', 88_121, 20_207, 2_301_557, False)
HP_D = {'2025-11': (12_277_303, 0.37), '2025-12': (8_101_907, 0.18), '2026-01': (12_919_441, 0.40), '2026-02': (11_301_229, 0.36),
        '2026-03': (12_007_117, 0.45), '2026-04': (11_513_903, 0.47), '2026-05': (11_087_211, 0.46), '2026-06': (7_403_119, 0.26), '2026-07': (7_009_337, 0.20)}
MULTI['series_months'] = {'date': '2026-10-22', 'span': ['2025-11', '2026-07'], 'shows': {'harbor precinct': {'title': 'Harbor Precinct', 'months': {
    ym: {'all': a, 'library': int(a * (1 - c)), 'current': a - int(a * (1 - c))} for ym, (a, c) in HP_D.items()}}}}
MULTI['show_policy'] = {'nights on duty': {'months': 'observed_only', 'why': 'listed in some months only'}}
pk.set_cached(MULTI)
MFIGS = pk.record_figures()
DECK_FIGS = {v for d in MULTI['series_months']['shows']['harbor precinct']['months'].values() for v in d.values() if v >= 100_000}
check(DECK_FIGS <= MFIGS, "the net knows the deck series' figures")
jq = 'How many viewers watched Harbor Precinct on Peacock from June 2026 to July 2026, and how many accounts?'
jb = ps.block(jq, {}, {})
jun = pk.viewers('Harbor Precinct', '2026-06-01', '2026-07-31')
check(jun and f"{jun['viewers']:,} US viewers" in jb, 'a read on months no report covers is handed the figure on file for them')
check(pk.leak_hits(jb) == [] and not any(f'{n:,}' in jb or str(n) in jb for n in MFIGS) and pk.VIEWERS_NOT_ACCOUNTS in jb,
      'and nothing from the record (report or deck) reaches the model; accounts are answered in viewers')
check(not any(w in jb.lower() for w in ('report', 'calibrat', 'first-party', 'actuals', 'deck', 'usage', 'content starts', 'studio', 'benchmark')),
      'and no word about where a figure came from')
check(ps.block('How many viewers watched Harbor Precinct on Hulu in June 2026?', {}, {}) == '', 'a read on another service still gets no block')
st5 = copy.deepcopy(before)
tps.apply_to_store(st5, '2026-06-17')
want5 = pk.daily_views('Harbor Precinct', '2026-06-17')
got5 = st5['tv:harbor precinct']['by_platform']['peacock']['us_estimate']
check(want5 and abs(got5 - want5) <= max(110, want5 * 0.002) and st5['tv:harbor precinct']['by_platform']['hulu'] == before['tv:harbor precinct']['by_platform']['hulu'],
      'the rail reads a month no report covers from the same months, and only on Peacock', f'{got5} vs {want5}')
ta5 = js._peacock_title_anchor({'subject': 'Harbor Precinct on Peacock', 'platform': 'Peacock', 'start_date': '2026-06-01', 'end_date': '2026-07-31'},
                               ['Harbor Precinct on Peacock', 'Harbor Precinct'])
check(ta5 and ta5['us_viewers'] == jun['viewers'], "a journey's watch stage over those months lands on the same figure")
check(pk.viewers('Nights on Duty', '2026-06-01', '2026-07-31') is None and pk.viewers('Nights on Duty', '2025-10-01', '2025-10-31'),
      'a show read from its own months only has no figure outside them')
D_TOP = MULTI['series_months']['shows']['harbor precinct']['months']['2026-06']['all']
DGOOD = f"Harbor Precinct drew {jun['viewers']:,} US viewers on Peacock in June and July, each person counted once."
for name, fn in (('a Prometheus reply', pma.scrub_user_text), ('a reply payload field', G.scrub_method_language), ('twin copy', dtc.strip_calibration_language),
                 ('the Prometheus guard', ps.guard)):
    out = fn(DGOOD + f' The series logged {D_TOP:,} plays in June.')
    check(f'{D_TOP:,}' not in out and f"{jun['viewers']:,}" in out, f'{name}: a figure from the deck series is dropped, ours ships')

pk.set_cached(None)
print()
if FAILS:
    print(f'{len(FAILS)} FAILED')
    for f_ in FAILS:
        print(' -', f_)
    sys.exit(1)
print('all checks passed')
