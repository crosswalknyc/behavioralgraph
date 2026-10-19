#!/usr/bin/env python3
"""Journey payloads leave every pass with shares that agree with their
counts and one copy of the read (2026-10-19, Ghost Energy Drink).

The shipped Ghost file carried 14 breakdown lines whose stored share
sat up to 0.07pp off the line's own count (the gold pass rescaled the
counts onto the row and kept the old shares), and the blob's copy of
the_read still quoted pre-reseat numbers while the top-level copy had
moved. These checks run the real helpers on small payloads; no network.
"""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import journey_gold as G  # noqa: E402
from migration import journey_iq as JIQ  # noqa: E402
from migration import journey_synthesis as JS  # noqa: E402

FAIL = 0


def check(name, ok, detail=''):
    global FAIL
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))
    if not ok:
        FAIL += 1


def payload():
    """A purchase journey with every derived field deliberately stale."""
    spine = [
        {'id': 'tam', 'label': 'US population', 'accounts': 329_900_000, 'kept': 100.0, 'dropped': 0, 'ofUs': 100.0},
        {'id': 'saw', 'label': 'Saw it', 'accounts': 1_000_003, 'kept': 9.9, 'dropped': 1, 'ofUs': 9.9},
        {'id': 'cart', 'label': 'Cart', 'accounts': 10_007, 'kept': 9.9, 'dropped': 1, 'ofUs': 9.9},
        {'id': 'paid', 'label': 'Paid', 'accounts': 5_003, 'kept': 9.9, 'dropped': 1, 'ofUs': 9.9},
    ]
    fork = [
        {'id': 'paid_first', 'label': 'Same session', 'accounts': 4_801, 'kept': 1.0, 'dropped': 1},
        {'id': 'abandoned', 'label': 'Left', 'accounts': 5_206, 'kept': 1.0, 'dropped': 1},
        {'id': 'retargeted', 'label': 'Retargeted', 'accounts': 2_603, 'kept': 1.0, 'dropped': 1},
        {'id': 'returned', 'label': 'Came back', 'accounts': 907, 'kept': 1.0, 'dropped': 1},
        {'id': 'paid_return', 'label': 'Paid later', 'accounts': 202, 'kept': 1.0, 'dropped': 1},
    ]
    detours = [
        {'title': 'Last touch', 'kind': 'partition', 'of': 'end', 'base': 5_003, 'source': 'research',
         'rows': [
             {'label': 'Revisit', 'accounts': 3_002, 'pct': 50.0,
              'breakdown': [{'label': 'A', 'accounts': 2_001, 'pct': 70.0},
                            {'label': 'B', 'accounts': 1_001, 'pct': 30.0}]},
             {'label': 'Ad', 'accounts': 2_001, 'pct': 50.0}]},
        {'title': 'Extra table', 'note': 'Rows sum to the parent count.',
         'rows': [{'label': 'X', 'accounts': 3_000, 'pct': 1.0}, {'label': 'Y', 'accounts': 2_003, 'pct': 1.0}]},
        {'title': 'Overlap no base', 'note': 'Overlap allowed. Rows do not sum.',
         'rows': [{'label': 'P', 'accounts': 400, 'pct': 7.9955}]},
    ]
    read = {'cards': [{'title': 't', 'body': 'new numbers 5,003'}], 'moves': []}
    stale = {'cards': [{'title': 't', 'body': 'old numbers 4,990'}], 'moves': []}
    return {
        'meta': {'story_mode': 'fragrance_shop_journey', 'journey_kind': 'purchase', 'target_type': 'purchase_journey'},
        'fragrance_shop_journey': {'spine': spine, 'fork': fork, 'detours': detours, 'the_read': stale,
                                   'who_they_are': {'gender': [{'label': 'Men', 'pct': 60.0}]}},
        'the_read': read,
        'kpis': {'total_users': 1, 'conversion_pct': 1.0, 'share_of_us_pct': 1.0, 'also_watching': 777},
    }


def test_reconcile():
    p = payload()
    before = copy.deepcopy(p)
    out = JS.reconcile_payload(p)
    check('reconcile returns the same object', out is p)
    j = p['fragrance_shop_journey']
    sp = {s['id']: s for s in j['spine']}
    # counts never move
    check('spine counts untouched', [s['accounts'] for s in j['spine']] ==
          [s['accounts'] for s in before['fragrance_shop_journey']['spine']])
    check('spine kept of prior', sp['saw']['kept'] == round(1_000_003 / 329_900_000 * 100, 4)
          and sp['paid']['kept'] == round(5_003 / 10_007 * 100, 4))
    check('spine dropped', sp['cart']['dropped'] == 1_000_003 - 10_007 and sp['tam']['dropped'] == 0)
    check('spine share of US', sp['paid']['ofUs'] == round(5_003 / 329_900_000 * 100, 4) and sp['tam']['ofUs'] == 100.0)
    fk = {f['id']: f for f in j['fork']}
    check('fork kept off its base', fk['paid_first']['kept'] == round(4_801 / 10_007 * 100, 4)
          and fk['retargeted']['kept'] == round(2_603 / 5_206 * 100, 4)
          and fk['paid_return']['kept'] == round(202 / 907 * 100, 4))
    check('fork branches are each other\'s drop', fk['paid_first']['dropped'] == 5_206 and fk['abandoned']['dropped'] == 4_801)
    check('fork dropped down the branch', fk['retargeted']['dropped'] == 5_206 - 2_603 and fk['returned']['dropped'] == 2_603 - 907)
    d0 = j['detours'][0]
    check('row share of the table base', d0['rows'][0]['pct'] == round(3_002 / 5_003 * 100, 4)
          and d0['rows'][1]['pct'] == round(2_001 / 5_003 * 100, 4))
    bd = d0['rows'][0]['breakdown']
    check('breakdown share of its row', bd[0]['pct'] == round(2_001 / 3_002 * 100, 4) and bd[1]['pct'] == round(1_001 / 3_002 * 100, 4))
    d1 = j['detours'][1]
    check('partition without a base uses the row sum', d1['rows'][0]['pct'] == round(3_000 / 5_003 * 100, 4))
    d2 = j['detours'][2]
    check('overlap without a base keeps its share', d2['rows'][0]['pct'] == 7.9955)
    check('counts in detours untouched', d0['rows'][0]['accounts'] == 3_002 and bd[0]['accounts'] == 2_001)
    check('blob read mirrors the top-level read', j['the_read'] == p['the_read']
          and j['the_read']['cards'][0]['body'] == 'new numbers 5,003')
    check('mirror is a copy, not an alias', j['the_read'] is not p['the_read'])
    check('blob-only block lifts to the top level', p.get('who_they_are') == j['who_they_are'])
    check('kpis follow the end count', p['kpis']['total_users'] == 5_003
          and p['kpis']['conversion_pct'] == round(5_003 / 1_000_003 * 100, 4)
          and p['kpis']['share_of_us_pct'] == round(5_003 / 329_900_000 * 100, 4)
          and p['kpis']['also_watching'] == 777)
    check('source tag left for the exporter to drop (payload unchanged there)', d0.get('source') == 'research')
    # idempotent
    again = copy.deepcopy(p)
    JS.reconcile_payload(again)
    check('second pass is a no-op', again == p)
    # shapes it must leave alone
    check('no blob: untouched', JS.reconcile_payload({'meta': {}}) == {'meta': {}})
    check('not a dict: untouched', JS.reconcile_payload(None) is None)
    mw = payload()
    mw['meta']['journey_kind'] = 'media_week'
    mw_before = copy.deepcopy(mw)
    JS.reconcile_payload(mw)
    check('media-week journey: untouched (its kpis mean something else)', mw == mw_before)


def test_gold_rescale():
    rows = [{'label': 'Promo', 'accounts': 7_324,
             'breakdown': [{'label': 'RetailMeNot', 'accounts': 2_539, 'pct': 34.6007},
                           {'label': 'Slickdeals', 'accounts': 2_046, 'pct': 27.8823},
                           {'label': 'Reddit', 'accounts': 1_409, 'pct': 19.2014},
                           {'label': 'Coupons', 'accounts': 1_358, 'pct': 18.5064}]},
            {'label': 'Other', 'accounts': 47_740}]
    out = G._rescale(rows, 55_172, 'partition', ('t',))
    check('partition rows sum to the base', sum(r['accounts'] for r in out) == 55_172)
    bd = out[0]['breakdown']
    check('breakdown lines sum exactly to the row', sum(b['accounts'] for b in bd) == out[0]['accounts'])
    check('breakdown shares read off the new counts',
          all(b['pct'] == round(b['accounts'] / out[0]['accounts'] * 100, 4) for b in bd))
    check('breakdown shares sum to ~100', abs(sum(b['pct'] for b in bd) - 100) < 0.01)


def test_breakdown_words():
    base = {'fragrance_shop_journey': {'spine': [
        {'id': 'tam', 'label': 'US population', 'accounts': 329_900_000, 'kept': 100, 'dropped': 0, 'ofUs': 100},
        {'id': 'saw', 'label': 'Saw it', 'accounts': 1_003, 'kept': 0.0003, 'dropped': 1, 'ofUs': 0.0003}]},
        'kpis': {'also_watching': 501}}
    buy = copy.deepcopy(base)
    buy['meta'] = {'journey_kind': 'purchase', 'target_type': 'purchase_journey'}
    rows = JIQ.breakdown_rows(buy)
    line = next((r for r in rows if r[2] == 501), None)
    check('purchase journey says customers', bool(line) and line[0] == 'Already customers' and 'customers' in line[1], str(line))
    watch = copy.deepcopy(base)
    watch['meta'] = {'journey_kind': 'watch', 'target_type': 'watch_journey'}
    rows = JIQ.breakdown_rows(watch)
    line = next((r for r in rows if r[2] == 501), None)
    check('watch journey still says watching', bool(line) and line[0] == 'Already watching', str(line))


if __name__ == '__main__':
    test_reconcile()
    test_gold_rescale()
    test_breakdown_words()
    print(f"\n{'ALL PASS' if not FAIL else str(FAIL) + ' FAILED'}")
    sys.exit(1 if FAIL else 0)
