#!/usr/bin/env python3
"""One set of figures across products (Jenna 2026-11-23, round 2, item 2).

Profile IQ, Brand Partnership IQ, Attribution IQ, Journey IQ, Subscriber IQ
and the generated read consult the same held figures, decision-of-record
entries and saved subject sizes for a subject before computing
(`knowledge.subject_facts`), and a product's figure for the subject is
held to them (`knowledge.cross_hold`): a record for the same window is
the figure; a people count never exceeds the subject's whole audience; a
figure of the same kind on a nearby window sits inside the near-twin
band of the one another product published, unless a dated event in the
changed part of the window moved it. Fixtures: the Anne Hathaway x
Shiseido and Leatherman x Matchbox figures.

Hermetic: the in-memory test ledger, a fake corpus catalog, no model, no
mail, no app boot."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')
os.environ.setdefault('PM_STORE_URL', '')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

import insights_ledger as IL  # noqa: E402
from prometheus import consistency as X  # noqa: E402
from prometheus import knowledge as K  # noqa: E402
from migration import corpus_catalog as CC  # noqa: E402
from migration import saved_subject_size as SSS  # noqa: E402
from migration import journey_synthesis as JS  # noqa: E402

IL._SYNC_FOR_TESTS = True
FAIL = 0

ANNE_TU = 92_615_994                      # her Profile IQ Total Universe, Jan to Dec 2025
ANNE_RECORD_REACH = 92_669_721            # the combined valuation of record, Aug 27 to Sep 8, 2026
ANNE_RECORD_VALUE = 104_140_107.40
LEATHERMAN_REACH = 173_284                # Brand Partnership IQ, Sep 29 to Nov 13, 2026
LEATHERMAN_VALUE = 3_246_461.86


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    return '\u2014' not in low and '\u2013' not in low and not any(w in low for w in ('synth', 'modeled', 'claude', 'pipeline', 'hostmap'))


def fake_catalog(subject, window=None, **kw):
    s = str(subject or '').lower()
    if 'anne hathaway' in s and ' x ' not in s:
        return {'subject': subject, 'facts': [
            {'kind': 'audience_size', 'product': 'profile', 'label': 'US audience (projected US people)', 'value': 16_670_738, 'unit': 'people',
             'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'source': {'key': 'ANNE HATHAWAY - Avid Fan.csv'}, 'cut': True, 'as_of': '2026-06-01'},
            {'kind': 'audience_size', 'product': 'profile', 'label': 'US audience (projected US people)', 'value': ANNE_TU, 'unit': 'people',
             'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'source': {'key': 'Anne_Hathaway_06_01_2026_10_00.csv'}, 'cut': False, 'as_of': '2026-06-01'},
            {'kind': 'stated_number', 'product': 'chat', 'label': 'noise', 'value': 1000, 'unit': 'people', 'window': None, 'source': {'key': 'thread/x'}},
        ], 'marks': []}
    if 'matchbox' in s or 'leatherman' in s:
        return {'subject': subject, 'facts': [
            {'kind': 'valuation', 'product': 'bpiq', 'label': 'total brand value, USD', 'value': LEATHERMAN_VALUE, 'unit': 'usd',
             'window': {'start': '2026-09-29', 'end': '2026-11-13'}, 'source': {'key': 'brand-partnership-iq/Matchbox_x_Leatherman_11_13.json'}, 'as_of': '2026-11-13'},
            {'kind': 'audience_size', 'product': 'bpiq', 'label': 'US audience reached by the partnership', 'value': LEATHERMAN_REACH, 'unit': 'people',
             'window': {'start': '2026-09-29', 'end': '2026-11-13'}, 'source': {'key': 'brand-partnership-iq/Matchbox_x_Leatherman_11_13.json'}, 'as_of': '2026-11-13'},
        ], 'marks': []}
    return {'subject': subject, 'facts': [], 'marks': []}


def setup_ledger():
    IL.use_test_ledger({})
    IL.bank_record(subject='Anne Hathaway x Shiseido', source='bpiq_combined',
                   metrics=[{'name': 'total_brand_value', 'label': 'Total brand value', 'value': ANNE_RECORD_VALUE, 'unit': 'usd'},
                            {'name': 'us_people_reached', 'label': 'US people the partnership reached', 'value': ANNE_RECORD_REACH, 'unit': 'people'}],
                   question='What is Anne Hathaway x Shiseido worth for Aug 27 to Sep 8, 2026?',
                   reply='Anne Hathaway x Shiseido, August 27 to September 8, 2026: total brand value $104,140,107.40 on 92,669,721 US people reached.',
                   window_start='2026-08-27', window_end='2026-09-08', metric_family='audience', asker='jenna')


# ------------------------------------------------------------ 1. kinds and facts

def test_kinds():
    print('figure kinds')
    check('a reach label is reach_people', K.figure_kind('US audience reached by the partnership', 'people') == 'reach_people')
    check('a viewers label is viewers_people', K.figure_kind('US viewers of the title', 'people') == 'viewers_people')
    check('an audience size is audience_people', K.figure_kind('US audience (projected US people)', 'people') == 'audience_people')
    check('dollars are value_usd, a percent is share_pct', K.figure_kind('total brand value', 'usd') == 'value_usd' and K.figure_kind('share who are men', 'pct') == 'share_pct')
    check('orders are orders', K.figure_kind('orders', 'orders') == 'orders' and K.figure_kind('US buyers who ordered', 'people') == 'orders')


def test_subject_facts():
    print('the facts one subject carries')
    setup_ledger()
    K._FACTS_CACHE.clear()
    with mock.patch.object(CC, 'anchors_for', fake_catalog), mock.patch.object(SSS, 'lookup', lambda s: None):
        sf = K.subject_facts('Anne Hathaway', window={'start': '2026-09-01', 'end': '2026-09-08'}, attribution=False)
    kinds = [(f['product'], f['kind'], f['value']) for f in sf['figures']]
    check('the Profile IQ sizes are on file, the chat noise is not', ('profile', 'audience_people', ANNE_TU) in kinds and len(kinds) == 2)
    check("the ceiling is her whole audience, never the cut's", sf['ceiling'] == ANNE_TU)
    check('the block lists Profile IQ with its window and the rules', 'FIGURES ON FILE FOR Anne Hathaway' in sf['block'] and '92,615,994 US people' in sf['block']
          and 'January 1, 2025 to December 31, 2025' in sf['block'] and 'never exceeds the audience' in sf['block'])
    with mock.patch.object(CC, 'anchors_for', fake_catalog), mock.patch.object(SSS, 'lookup', lambda s: None):
        sfp = K.subject_facts('Anne Hathaway x Shiseido', window={'start': '2026-09-01', 'end': '2026-09-08'}, attribution=False)
    recs = [f for f in sfp['figures'] if f.get('record')]
    check('the pairing carries the decision of record: the value and the people reached, Aug 27 to Sep 8',
          any(f['kind'] == 'value_usd' and abs(f['value'] - ANNE_RECORD_VALUE) < 0.01 for f in recs)
          and any(f['kind'] == 'reach_people' and f['value'] == ANNE_RECORD_REACH and f['window'] == {'start': '2026-08-27', 'end': '2026-09-08'} for f in recs))
    check('the block says on record', 'On record, August 27, 2026 to September 8, 2026: $104,140,107.40' in sfp['block'])
    check('the block is plain', clean(sf['block']) and clean(sfp['block']))
    # the saved size leads the block and sets the ceiling
    with mock.patch.object(CC, 'anchors_for', fake_catalog), \
            mock.patch.object(SSS, 'lookup', lambda s: {'tu': 48_172, 'us': 1_589_194, 'source': 'ledger_record', 'ref': 'k', 'window': ('2025-11-20', '2026-11-20'), 'subject': s}):
        sfv = K.subject_facts('VICE Sports', attribution=False)
    check('a saved size leads the block and is the ceiling', sfv['size']['us'] == 1_589_194 and sfv['ceiling'] == 1_589_194
          and sfv['block'].splitlines()[1].startswith('- Audience size: 1,589,194 US people'))
    check('the facts are cached for a short while', K.subject_facts('VICE Sports', attribution=False)['ceiling'] == 1_589_194)
    K._FACTS_CACHE.clear()


# ------------------------------------------------------------ 2. the cross-product hold

def anne_pair_facts():
    setup_ledger()
    K._FACTS_CACHE.clear()
    with mock.patch.object(CC, 'anchors_for', fake_catalog), mock.patch.object(SSS, 'lookup', lambda s: None):
        sf = K.subject_facts('Anne Hathaway x Shiseido', window={'start': '2026-09-01', 'end': '2026-09-08'}, attribution=False)
        base = K.subject_facts('Anne Hathaway', attribution=False)
    sf['ceiling'] = base['ceiling']            # the talent's whole audience caps the pairing's people
    sf['figures'] += [f for f in base['figures'] if f['kind'] == 'audience_people']
    return sf


def test_cross_hold_anne():
    print('Anne Hathaway x Shiseido across products')
    sf = anne_pair_facts()
    v, rec = K.cross_hold(120_000_000, kind='reach_people', facts=sf, window={'start': '2026-09-01', 'end': '2026-09-08'}, salt='t1', label='reach', product='journey')
    check('120M people reached by an 8-day campaign comes under her whole audience (92,615,994), messy, never pinned',
          rec['why'] == 'ceiling' and v < ANNE_TU and v > ANNE_TU * 0.96 and v % 10 != 0)
    v2, rec2 = K.cross_hold(40_000_000, kind='reach_people', facts=sf, window={'start': '2026-08-27', 'end': '2026-09-08'}, salt='t2', label='reach', product='journey')
    check('the same window as the record takes the record exactly', rec2['why'] == 'record' and v2 == ANNE_RECORD_REACH)
    v3, rec3 = K.cross_hold(40_000_000, kind='reach_people', facts=sf, window={'start': '2026-08-29', 'end': '2026-09-08'}, salt='t3', label='reach', product='attribution')
    check('a nearby window (two days in) is held inside the band of the record', rec3['why'] == 'held' and ANNE_RECORD_REACH * 0.70 < v3 < ANNE_RECORD_REACH * 0.80)
    v4, rec4 = K.cross_hold(90_000_000, kind='reach_people', facts=sf, window={'start': '2026-08-29', 'end': '2026-09-08'}, salt='t4', label='reach', product='attribution')
    check('a figure inside the band is left alone', rec4['why'] == 'kept' and v4 == 90_000_000)
    ev = [{'date': '2026-08-28', 'kind': 'launch', 'name': 'the fall campaign launch', 'where': 'New York'}]
    v5, rec5 = K.cross_hold(40_000_000, kind='reach_people', facts=sf, window={'start': '2026-08-29', 'end': '2026-09-08'}, salt='t5', label='reach', product='attribution', events=ev)
    check('a dated event inside the changed part of the window lifts the band (the figure stands)', rec5['why'] == 'lifted' and v5 == 40_000_000 and rec5['event']['date'] == '2026-08-28')
    ev2 = [{'date': '2026-09-20', 'kind': 'launch', 'name': 'later', 'where': ''}]
    v6, rec6 = K.cross_hold(40_000_000, kind='reach_people', facts=sf, window={'start': '2026-08-29', 'end': '2026-09-08'}, salt='t6', label='reach', product='attribution', events=ev2)
    check('an event outside the changed part lifts nothing', rec6['why'] == 'held')
    v7, rec7 = K.cross_hold(9_000_000.0, kind='value_usd', facts=sf, window={'start': '2026-08-27', 'end': '2026-09-08'}, salt='t7', label='total', product='read')
    check('a dollar figure for the record window is the record to the cent', rec7['why'] == 'record' and abs(v7 - ANNE_RECORD_VALUE) < 0.01)
    v8, rec8 = K.cross_hold(2_000_000, kind='reach_people', facts=sf, window={'start': '2022-01-01', 'end': '2022-02-01'}, salt='t8', label='reach', product='journey')
    check('a window touching nothing on file is left alone', rec8['why'] == 'kept')
    os.environ['PM_SHARED_FIGURES'] = '0'
    try:
        check('off: nothing moves', K.cross_hold(120_000_000, kind='reach_people', facts=sf, window={'start': '2026-09-01', 'end': '2026-09-08'})[1]['why'] == 'kept')
    finally:
        os.environ.pop('PM_SHARED_FIGURES', None)


def test_cross_hold_leatherman():
    print('Leatherman x Matchbox across products')
    IL.use_test_ledger({})
    K._FACTS_CACHE.clear()
    with mock.patch.object(CC, 'anchors_for', fake_catalog), mock.patch.object(SSS, 'lookup', lambda s: None):
        sf = K.subject_facts('Matchbox the Film on Apple TV x Leatherman', window={'start': '2026-10-09', 'end': '2026-11-19'}, attribution=False)
    check('the Nov 13 valuation is on file for the pairing', any(f['kind'] == 'reach_people' and f['value'] == LEATHERMAN_REACH for f in sf['figures']))
    v, rec = K.cross_hold(400_000, kind='reach_people', facts=sf, window={'start': '2026-10-09', 'end': '2026-11-19'}, salt='l1', label='journey audience stage', product='journey', exclude_products=('journey',))
    check("a journey's partnership stage for Oct 9 to Nov 19 is held inside the band of the Sep 29 to Nov 13 reach (windows near)",
          rec['why'] == 'held' and LEATHERMAN_REACH * 1.15 < v < LEATHERMAN_REACH * 1.25 and rec['anchor']['product'] == 'bpiq')
    v2, rec2 = K.cross_hold(9_000_000.0, kind='value_usd', facts=sf, window={'start': '2026-10-09', 'end': '2026-11-19'}, salt='l2', label='total', product='read')
    check('a value read on the nearby window is held inside a quarter of the valuation on file', rec2['why'] == 'held' and LEATHERMAN_VALUE * 1.15 < v2 < LEATHERMAN_VALUE * 1.25)
    v3, rec3 = K.cross_hold(400_000, kind='reach_people', facts=sf, window={'start': '2026-10-09', 'end': '2026-11-19'}, salt='l3', product='bpiq', exclude_products=('bpiq',))
    check("a valuation's own product figures are not its cross-product twin (its own twin check holds those)", rec3['why'] == 'kept')
    v4, rec4 = K.cross_hold(400_000, kind='reach_people', facts=sf, window={'start': '2026-01-01', 'end': '2026-02-15'}, salt='l4', product='journey')
    check('a far window is not near: kept', rec4['why'] == 'kept')


# ------------------------------------------------------------ 3. the products

def test_journey_apply():
    print('a journey holds to the figures on file')
    sf = anne_pair_facts()
    tam = JS.US_GEN_POP

    def payload(top, mid, last):
        return {'fragrance_shop_journey': {'spine': [{'id': 's0', 'accounts': tam}, {'id': 's1', 'accounts': top}, {'id': 's2', 'accounts': mid}, {'id': 's3', 'accounts': last}],
                                           'fork': [], 'detours': [], 'copy': {'lead': f"{top:,} people in the US saw it."}},
                'kpis': {'total_users': last}, 'facts': [{'value': f"{top:,} people in the US"}], 'meta': {}}
    p = payload(120_000_003, 30_000_001, 5_000_007)
    out = JS.apply_shared_figures(p, sf, {'subject': 'Anne Hathaway', 'platform': 'Prime Video'}, seed='s')
    spine = out['fragrance_shop_journey']['spine']
    check("a brand or title journey's first stage comes under the whole audience on file (the ceiling only, no band)",
          spine[1]['accounts'] < ANNE_TU and out['meta']['shared_figures']['why'] == 'ceiling' and 'title_anchor' not in out['meta'])
    check('every count scaled by one factor, messy, the copy follows',
          spine[2]['accounts'] < 30_000_001 and spine[3]['accounts'] < 5_000_007 and all(int(s['accounts']) % 10 != 0 for s in spine[1:])
          and f"{spine[1]['accounts']:,}" in out['fragrance_shop_journey']['copy']['lead'] and '120,000,003' not in json.dumps(out))
    p2 = payload(60_000_001, 15_000_001, 3_000_001)
    out2 = JS.apply_shared_figures(p2, sf, {'subject': 'Anne Hathaway x Shiseido', 'platform': 'Instagram', 'start_date': '2026-08-29', 'end_date': '2026-09-08'}, seed='s2')
    s2 = out2['fragrance_shop_journey']['spine']
    check("a partnership journey's first stage is the people reached: held inside the band of the record on the nearby window",
          out2['meta']['shared_figures']['kind'] == 'reach_people' and out2['meta']['shared_figures']['why'] == 'held'
          and ANNE_RECORD_REACH * 0.70 < s2[1]['accounts'] < ANNE_RECORD_REACH * 0.80 and s2[3]['accounts'] > 3_000_001)
    p2b = payload(400_000, 100_000, 20_000)
    out2b = JS.apply_shared_figures(p2b, sf, {'subject': 'Anne Hathaway x Shiseido', 'platform': 'Instagram', 'start_date': '2026-08-29', 'end_date': '2026-09-08'}, seed='s2b')
    check('a journey on another basis entirely (400,000 against a 92 million record) stands and the mismatch is recorded, never scaled 180x',
          out2b['meta']['shared_figures']['why'] == 'mismatch' and out2b['fragrance_shop_journey']['spine'][1]['accounts'] == 400_000)
    p3 = payload(50_000_001, 10_000_001, 1_000_001)
    out3 = JS.apply_shared_figures(p3, sf, {'subject': 'Anne Hathaway', 'platform': 'Prime Video'}, seed='s3')
    check('a journey already inside the audience is untouched', out3['fragrance_shop_journey']['spine'][1]['accounts'] == 50_000_001 and 'shared_figures' not in out3['meta'])
    src = Path(JS.__file__).read_text(encoding='utf-8')
    check('corpus_anchors carries the shared facts and applies them after the title anchor',
          "out['shared'] = shared" in src and "apply_shared_figures(payload, anchors['shared']" in src)


def test_bpiq_wired():
    print('the valuation builder reads the other products')
    src = Path(WEB / 'migration' / 'bpiq_synthesis.py').read_text(encoding='utf-8')
    i_twin, i_cross, i_rebuild = src.find('_twin.apply_valuation('), src.find('cross_hold(_reach0'), src.find('_rebuild_held(inputs, prim, campaign, {"audience_reached": int(_held)}')
    check('the research prompt carries the shared block', 'published["shared_figures"]' in src)
    check('after its own twin check the reach is held across products and the money rebuilt from the held reach',
          0 < i_twin < i_cross < i_rebuild and 'exclude_products=("bpiq",)' in src and '"cross"' in src)


def test_saved_size_title_anchor():
    print('a title universe sizes a Profile IQ build of the title')
    SSS.clear_cache()
    ta = {'title': 'Dark Matter', 'us_viewers': 3_412_907, 'sample_size': 103_453, 'source_product': 'subscriber_iq', 'window': {'start': '2026-05-01', 'end': '2026-06-30'}}
    with mock.patch.object(SSS, 'TITLE_ANCHOR_FOR_TESTS', lambda t, season=None, window=None: ta if 'dark matter' in str(t).lower() else None), \
            mock.patch.object(SSS, 'from_ledger', lambda s: None), mock.patch.object(SSS, 'from_estimates', lambda s: None), mock.patch.object(SSS, 'from_saved_table', lambda s: None):
        got = SSS.lookup('Dark Matter')
        check('the title anchor is a saved-size source: the build starts on the title universe',
              bool(got) and got['source'] == 'title_anchor' and got['us'] == 3_412_907 and got['tu'] == 103_453 and got['window'] == ('2026-05-01', '2026-06-30'))
        check('a cut name never takes a title size', SSS.from_title_anchor('Dark Matter - Avid Fan') is None)
    SSS.clear_cache()
    src = Path(SSS.__file__).read_text(encoding='utf-8')
    check('the source sits before the anchor store and after the saved table', 'from_saved_table, from_title_anchor, from_anchor_store' in src)


def test_read_carries_shared_block():
    print('the generated read carries the figures on file and takes the ceiling')
    sf = anne_pair_facts()
    base = {'subject': 'Anne Hathaway', 'source': 'catalog', 's3_key': 'Anne_Hathaway_06_01_2026_10_00.csv'}
    q = 'How many US people did Anne Hathaway reach this September?'
    with mock.patch.object(K, 'subject_facts', lambda *a, **k: sf), mock.patch.object(K, 'held', lambda *a, **k: {'block': '', 'profile': None, 'marks': []}):
        blk = K.read_blocks(q, {}, base, pk=None, saved=None, catalog=None, claude_json=None, tools=None, history=[], body=None,
                            led={'block': '', 'exact': None, 'entries': []})
    check('the read\'s prompt carries the figures on file', 'FIGURES ON FILE FOR Anne Hathaway' in blk and '$104,140,107.40' in blk)
    sp = K.peek_size('Anne Hathaway', q)
    check('the whole audience on file is the size gate\'s ceiling for the read', sp and sp['cap'] == ANNE_TU and sp['source'] == 'shared')
    K.take_twin('Anne Hathaway', q)


def test_words():
    print('plain copy')
    src = Path(K.__file__).read_text(encoding='utf-8')
    check('no em dash in the knowledge layer', '\u2014' not in src and '\u2013' not in src)
    check('the switch is env only', 'PM_SHARED_FIGURES' in src and 'request.json' not in src)


if __name__ == '__main__':
    test_kinds()
    test_subject_facts()
    test_cross_hold_anne()
    test_cross_hold_leatherman()
    test_journey_apply()
    test_bpiq_wired()
    test_saved_size_title_anchor()
    test_read_carries_shared_block()
    test_words()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
