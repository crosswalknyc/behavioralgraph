#!/usr/bin/env python3
"""The knowledge layer (the Prometheus rebuild, stage 3, 2026-11-20): one
place every answer reads from, in a fixed order (held, stored, research),
with research as a step of its own and an anchor store; the three paths
wired through it (the generated read's block, the insight lane's blocks,
the valuation's pieces and near-twin check) and the kill switches that
hand back what each path had. Hermetic: no network, no model."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
for _k in ('PM_KNOWLEDGE', 'PM_KNOWLEDGE_RESEARCH', 'PM_TWIN_CHECK', 'PM_TWIN_SAY_HELD'):
    os.environ.pop(_k, None)

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import knowledge as K  # noqa: E402
from prometheus import consistency as X  # noqa: E402
from prometheus import pieces as P  # noqa: E402
from prometheus import read_repair as RR  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402

FAIL = 0


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def test_ask_and_key():
    print('the ask and its key')
    body = {'_pm_understanding': {'intent': 'measure', 'subjects': [{'as_written': 'anne hathaway', 'catalog': 'Anne Hathaway', 'kind': 'person', 'role': 'subject'},
                                                                    {'as_written': 'netflix', 'catalog': 'Netflix', 'kind': 'service', 'role': 'service'}],
                                  'named_title': '', 'window': '2026-09-01 to 2026-09-30', 'platform': 'Netflix'}}
    a = K.ask_from(body, 'how many watched', {'subject': 'The Page Profile'}, {'subject': 'ignored', 'family': 'viewership'})
    check("the Understanding's subject wins over the page and the request", a['subject'] == 'Anne Hathaway')
    check('the window is read from the Understanding', a['window'] == {'start': '2026-09-01', 'end': '2026-09-30'})
    check('the platform rides along; the family is the ledger family', a['platform'] == 'Netflix' and a['family'])
    b = K.ask_from(None, 'q', {'subject': 'A Title'}, {'window': '2026-01-01 to 2026-01-31'})
    check('without an Understanding the base and the request fill in', b['subject'] == 'A Title' and b['window']['end'] == '2026-01-31')
    k = K.ask_key(a)
    check(f'the key is subject | product | family | window | scope: {k}', k.startswith('anne_hathaway|read|') and '|2026-09-01|2026-09-30|' in k)
    k2 = K.ask_key(K.ask_from(None, '', {'subject': 'Anne  Hathaway'}, None, window=a['window']))
    check('a spelling difference makes the same key', k2.split('|')[0] == k.split('|')[0])


def test_research_step_and_store():
    print('the research step')
    calls = []

    def fake(system, user, **kw):
        calls.append((kw.get('surface'), json.loads(user)))
        return {'success': True, 'data': {
            'anchors': [{'name': 'US monthly users', 'value': 48_000_000, 'unit': 'people', 'window': {'start': '2026-09-01', 'end': '2026-09-30'},
                         'klass': 'footprint', 'source_type': 'earnings', 'as_of': '2026-10-15'},
                        {'name': 'bad', 'value': 'n/a'}],
            'events': [{'date': '2026-09-03', 'kind': 'premiere', 'name': 'season two', 'where': 'Los Angeles'},
                       {'date': 'sometime', 'kind': 'launch', 'name': 'undated'}],
            'sources': [{'title': 'x', 'url': 'https://example.com'}]}}

    store = P.MemoryStore()
    ask = K.ask_from(None, 'how many people use it', {'subject': 'Some Service'}, {'window': '2026-09-01 to 2026-09-30'})
    r = K.research(ask, fake, tools=[{'type': 'web_search'}], store=store)
    check('one call with the web search tool, strict JSON back', len(calls) == 1 and calls[0][0] == 'pm_knowledge_research')
    check('anchors keep value, unit, window, class, source type; an unusable anchor is dropped',
          len(r['anchors']) == 1 and r['anchors'][0]['value'] == 48_000_000 and r['anchors'][0]['source_type'] == 'earnings')
    check('events keep only dated events of a known kind', r['events'] == [{'date': '2026-09-03', 'kind': 'premiere', 'name': 'season two', 'where': 'Los Angeles'}])
    check('the block is prompt text that tells the model never to name a source',
          'RESEARCHED ANCHORS' in r['block'] and 'Never name a source' in r['block'] and '48' in r['block'] and 'EVENT 2026-09-03' in r['block'])
    check('marks say where each line came from (internal only)', r['marks'][0]['kind'] == 'research' and not r['cached'])
    saved = store.load(K._anchor_doc_key(r['key']))
    check('the anchors are saved under the ask key with their sources (internal)', saved and saved['anchors'] and saved['sources'])
    r2 = K.research(ask, fake, store=store)
    check('a second ask on the same key reads the store and makes no call', r2['cached'] and len(calls) == 1 and r2['anchors'] == r['anchors'])
    r3 = K.research(ask, fake, store=store, changed_windows=[{'start': '2026-09-01', 'end': '2026-09-04', 'change': 'added'}])
    check('a scoped ask (changed parts of a window) is its own key and carries the ranges',
          len(calls) == 2 and calls[1][1].get('date_ranges') == [{'start': '2026-09-01', 'end': '2026-09-04', 'change': 'added'}] and r3['key'] != r['key'])
    os.environ['PM_KNOWLEDGE_RESEARCH'] = '0'
    try:
        r4 = K.research(K.ask_from(None, 'other', {'subject': 'Other'}, {'window': '2026-09-01 to 2026-09-30'}), fake, store=store)
        check('PM_KNOWLEDGE_RESEARCH=0: no call, nothing new', len(calls) == 2 and r4['anchors'] == [] and r4['block'] == '')
        r5 = K.research(ask, fake, store=store)
        check('stored anchors are still read back with research off', r5['cached'] and r5['anchors'])
    finally:
        os.environ.pop('PM_KNOWLEDGE_RESEARCH', None)
    plain = K.research(ask, lambda s, u, **kw: {'anchors': [{'name': 'a', 'value': 5, 'unit': 'people'}], 'events': []}, store=P.MemoryStore())
    check('a model call that returns the object itself is read too', plain['anchors'][0]['value'] == 5)
    failed = K.research(ask, lambda s, u, **kw: {'success': False, 'error': 'down'}, store=P.MemoryStore())
    check('a failed call is an empty step, never an error', failed == {'anchors': [], 'events': [], 'block': '', 'marks': [], 'cached': False, 'key': failed['key']})


def test_facts_block_order():
    print('the facts block')
    held = {'block': 'PUBLISHED FIGURES ON THIS SUBJECT (binding). x'}
    twin = X.anchor_from_entry({'k': 'k1', 'ts': '2026-11-01T00:00:00', 'ws': '2026-09-01', 'we': '2026-09-30', 'fam': 'audience',
                                'metrics': [{'name': 'us_viewers', 'value': 4_100_000, 'unit': 'people'}]}, subject='A Title')
    stored = {'block': 'PUBLISHED MEASUREMENTS y', 'twin': twin}
    research = {'block': 'RESEARCHED ANCHORS z'}
    blk = K.facts_block({'window': {'start': '2026-09-05', 'end': '2026-10-02'}}, held, stored, research, extra=['EXTRA'])
    order = [blk.index(s) for s in ('PUBLISHED FIGURES', 'PUBLISHED MEASUREMENTS', 'NEAR-TWIN', 'RESEARCHED ANCHORS', 'EXTRA')]
    check('held, then stored, then the near twin, then the anchors, then what the path still carries', order == sorted(order))
    check('an empty layer is an empty block', K.facts_block({}, None, None, None) == '')


def test_read_blocks_paths():
    print("the generated read's block")
    legacy_calls = []

    def pk(text, mr, base):
        legacy_calls.append('pk')
        return 'PEACOCK'

    def saved(text):
        legacy_calls.append('saved')
        return 'SAVED VALUATION'

    def catalog(subject):
        legacy_calls.append('catalog')
        return f'CATALOG {subject}'

    os.environ['PM_KNOWLEDGE'] = '0'
    try:
        off = K.read_blocks('q', {}, {'subject': 'A Title'}, pk=pk, saved=saved, catalog=catalog)
        check('PM_KNOWLEDGE=0: exactly the block the path built before', off == 'PEACOCK\n\nSAVED VALUATION\n\nCATALOG A Title')
    finally:
        os.environ.pop('PM_KNOWLEDGE', None)
    legacy_calls.clear()
    stages, stage_notes, model_calls = {}, [], []

    def fake(system, user, **kw):
        model_calls.append(kw.get('surface'))
        return {'success': True, 'data': {'anchors': [{'name': 'US viewers of the title', 'value': 3_900_000, 'unit': 'people'}], 'events': []}}

    K.STORE_FOR_TESTS = P.MemoryStore()
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    try:
        led = {'subject': 'A Title', 'entries': [
            {'k': 'k-first', 'ts': '2026-11-01T10:00:00', 'ws': '2026-09-01', 'we': '2026-09-30', 'fam': 'audience',
             'metrics': [{'name': 'us_viewers', 'label': 'US viewers in the window', 'value': 4_100_000, 'unit': 'people'}]}]}
        base = {'subject': 'A Title', 'source': 'catalog'}
        blk = K.read_blocks('how many watched', {'window': '2026-09-05 to 2026-10-02', 'family': 'audience'}, base,
                            pk=pk, saved=saved, catalog=catalog, stage=stage_notes.append, stages=stages,
                            claude_json=fake, tools=[{'type': 'web_search'}], led=led)
        check('the path\'s own blocks still ride (Peacock, the saved valuation, the catalog when the catalog is off)',
              blk.startswith('PEACOCK\n\nSAVED VALUATION\n\nCATALOG A Title'))
        check('the near twin from the ledger consult the path already made rides as a binding block',
              'FIGURES ALREADY GIVEN ON A NEAR-TWIN ASK' in blk and '4100000' in blk.replace(',', ''))
        check('the research step ran once as its own step and its anchors ride the block',
              model_calls == ['pm_knowledge_research'] and 'RESEARCHED ANCHORS' in blk and 'US viewers of the title' in blk)
        check('the stage note says researching; the timing lands in stages', 'researching' in stage_notes and 'knowledge' in stages)
        check('the base carries the layer\'s marks for the ask log (internal)',
              base['_knowledge']['twin'] == 'k-first' and base['_knowledge']['anchors'] == 1 and base['_knowledge']['key'].startswith('a_title|read|'))
        data = {'metrics': [{'name': 'us_viewers', 'label': 'US viewers', 'value': 900_000, 'unit': 'people'}]}
        res, d = RR.coherent(json.loads(json.dumps(data)), enforce=lambda x: x, question='how many watched', subject='A Title')
        check('read_repair.coherent holds the fresh figure to the twin left for it (3,075,000 to 4,100,000 band)',
              3_075_000 < res['metrics'][0]['value'] < 4_100_000 and res.get('_consistency', {}).get('verdict') == 'held')
        recs = (X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or []
        check('the hold wrote a staff record for the read', any(r.get('product') == 'read' and r.get('verdict') == 'held' for r in recs))
        plain = RR.coherent(json.loads(json.dumps(data)), enforce=lambda x: x, question='how many watched', subject='A Title')[0]
        check('with nothing pending the read is untouched', plain['metrics'][0]['value'] == 900_000)
    finally:
        K.STORE_FOR_TESTS = None
        X.RECORDS_FOR_TESTS = None


def test_insight_blocks_paths():
    print("the insight lane's blocks")
    os.environ['PM_KNOWLEDGE'] = '0'
    try:
        led, anchors = K.insight_blocks('an insight', 'A Title', consult=lambda: 'LEDGER BLOCK')
        check("PM_KNOWLEDGE=0: the lane's own lookup, no anchors", led == 'LEDGER BLOCK' and anchors == '')
    finally:
        os.environ.pop('PM_KNOWLEDGE', None)
    calls = []

    def fake(system, user, **kw):
        calls.append(kw.get('surface'))
        return {'anchors': [{'name': 'US shoppers', 'value': 12_000_000, 'unit': 'people'}], 'events': []}

    K.STORE_FOR_TESTS = P.MemoryStore()
    try:
        led, anchors = K.insight_blocks('an insight', 'A Title', consult=lambda: 'LEDGER BLOCK', claude_json=fake,
                                        window='2026-09-01 to 2026-09-30')
        check('with the ledger thin the research step runs and its anchors come back as a block',
              calls == ['pm_knowledge_research'] and 'RESEARCHED ANCHORS' in anchors and 'US shoppers' in anchors)
    finally:
        K.STORE_FOR_TESTS = None


def test_valuation_end_to_end():
    print('the valuation, end to end')
    calls = []

    def fake(system, user, **kw):
        calls.append(kw.get('surface'))
        s = kw.get('surface')
        if s == 'bpiq_campaign_research':
            return dict(T.campaign(sold_out=False), drop_dates=['2026-10-22'])   # the drop sits inside both windows
        if s in ('bpiq_sellout_check', 'bpiq_campaign_footprint'):
            return {}
        if s == 'pm_knowledge_research':
            return {'anchors': [], 'events': []}
        p = T.prim()
        if fake.small:
            p.update({'audience_size': 212, 'projected_audience_size': 6_993,
                      'totals': {'pre_pen_pct': 20.1, 'post_pen_pct': 71.3},
                      'conversions': {'pre_users_pct': 1.1, 'post_users_pct': 9.4}})
        return p
    fake.small = False

    store = S.MemoryUnderpinnings()
    pieces_store = P.MemoryStore()
    P.STORE_FOR_TESTS = pieces_store
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    first_inp = T.inputs(start='2026-10-15', end='2026-10-28', post_end='2026-10-29')
    first = S.synthesize(first_inp, fake, underpinnings=store, job_id='first')
    X.META_FOR_TESTS = {'Jennifer_Beals_x_Lululemon_10_29_2026_00_00.json': {'display_name': 'Jennifer Beals x Lululemon'}}
    X.LOADER_FOR_TESTS = lambda key: dict(first, created_at='2026-10-29T00:00:00Z')
    try:
        fake.small = True
        calls.clear()
        second_inp = T.inputs(start='2026-10-20', end='2026-10-31', post_end='2026-11-01')
        second = S.synthesize(second_inp, fake, underpinnings=store, job_id='second')
        fd, sd = X.drivers_of(first), X.drivers_of(second)
        ff, sf = X.figures_of(first), X.figures_of(second)
        check(f"the second order, five days later and three days longer, is held to the first ({sd['audience_reached']:,} people against {fd['audience_reached']:,})",
              abs(sd['audience_reached'] / fd['audience_reached'] - 1.0) <= 0.25)
        check(f"and its total sits inside 25% (${sf['total_usd']:,.0f} against ${ff['total_usd']:,.0f})",
              abs(sf['total_usd'] / ff['total_usd'] - 1.0) <= 0.25)
        check('the research step was asked once about the changed parts of the window', calls.count('pm_knowledge_research') == 1)
        diag = (second.get('diagnostics') or {}).get('consistency') or {}
        check('the payload carries the record summary and the saved run carries the verdict',
              diag.get('verdict') == 'held' and (store.load(S.run_key(second_inp)) or {}).get('twin', {}).get('verdict') == 'held')
        check('nothing shipped to the reader by default (the held line is internal)', not (second.get('campaign') or {}).get('consistency_line'))
        os.environ['PM_TWIN_CHECK'] = '0'
        try:
            third = S.synthesize(T.inputs(start='2026-10-21', end='2026-10-31', post_end='2026-11-01'), fake, underpinnings=S.MemoryUnderpinnings(), job_id='third')
            check('PM_TWIN_CHECK=0: the valuation runs as the research gave it',
                  X.drivers_of(third)['audience_reached'] < 10_000 and 'consistency' not in (third.get('diagnostics') or {}))
        finally:
            os.environ.pop('PM_TWIN_CHECK', None)
    finally:
        X.META_FOR_TESTS = None
        X.LOADER_FOR_TESTS = None
        X.RECORDS_FOR_TESTS = None
        P.STORE_FOR_TESTS = None


if __name__ == '__main__':
    test_ask_and_key()
    test_research_step_and_store()
    test_facts_block_order()
    test_read_blocks_paths()
    test_insight_blocks_paths()
    test_valuation_end_to_end()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
