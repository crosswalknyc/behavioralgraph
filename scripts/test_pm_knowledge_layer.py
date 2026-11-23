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
        if s == 'bpiq_sellout_check':
            return {}
        if s == 'bpiq_campaign_footprint':
            # the campaign's own pages and coverage: the people it reached are countable
            uniques = 1_400_000 if fake.small else 14_000_000
            return {'press': [{'outlet': 'Vogue', 'title': 'the drop', 'url': 'https://www.vogue.com/x', 'date': '2026-10-22'}],
                    'press_pieces_total': 1, 'site': {'brand_site_us_monthly_uniques': uniques, 'collection_page_share_pct': 7.0}}
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
                  X.drivers_of(third)['audience_reached'] < 120_000 and 'consistency' not in (third.get('diagnostics') or {}))
        finally:
            os.environ.pop('PM_TWIN_CHECK', None)
    finally:
        X.META_FOR_TESTS = None
        X.LOADER_FOR_TESTS = None
        X.RECORDS_FOR_TESTS = None
        P.STORE_FOR_TESTS = None


def test_no_whole_audience_fallback():
    print("no figure on the talent's whole audience")
    calls = []

    def fake(system, user, **kw):
        s = kw.get('surface')
        calls.append((s, json.loads(user)))
        if s == 'bpiq_campaign_research':
            return T.campaign(sold_out=False)
        if s in ('bpiq_sellout_check', 'bpiq_campaign_footprint'):
            return {}
        if s == 'pm_knowledge_research':
            return {'anchors': [], 'events': []}
        return T.prim()

    P.STORE_FOR_TESTS = P.MemoryStore()
    os.environ['BPIQ_WHOLE_AUDIENCE_FALLBACK'] = '0'     # the shared fixtures module keeps the old fallback on for itself
    try:
        raised = None
        try:
            S.synthesize(T.inputs(), fake, underpinnings=S.MemoryUnderpinnings(), job_id='empty')
        except S.NoCountableReach as e:
            raised = e
        check('a campaign with nothing countable after a second look does not ship a figure', raised is not None)
        fp_calls = [c for c in calls if c[0] == 'bpiq_campaign_footprint']
        check('the campaign coverage was looked for twice, the second time with the sharper ask',
              len(fp_calls) == 2 and any('Second look' in h for h in (fp_calls[1][1].get('verified_facts') or [])))
        check('no audience primitive was reasoned on the whole audience', not any(c[0] == 'bpiq_synthesis' for c in calls))
        line = str(getattr(raised, 'client_line', '') or '')
        check(f'the reader is asked plainly: {line[:90]!r}', line.startswith('We need to confirm which campaign to value for Jennifer Beals x Lululemon')
              and 'placement or link' in line and '\u2014' not in line
              and not any(w in line.lower() for w in ('synth', 'model', 'research', 'pipeline', 'footprint', 'fallback', 'estimat')))
        os.environ['BPIQ_WHOLE_AUDIENCE_FALLBACK'] = '1'
        p = S.synthesize(T.inputs(), fake, underpinnings=S.MemoryUnderpinnings(), job_id='old-way')
        check('the old fallback is behind an env switch', isinstance(p, dict) and p.get('projected_audience_size'))
    finally:
        os.environ.pop('BPIQ_WHOLE_AUDIENCE_FALLBACK', None)
        P.STORE_FOR_TESTS = None
    # the projection follows the sample at the house ratio
    fixed = S.hold_projection_weight({'audience_size': 375_429, 'projected_audience_size': 92_615_994}, 'anne')
    check(f"a projection of 246.69 people per sample person is recomputed at 32.99 ({fixed['projected_audience_size']:,})",
          abs(fixed['projected_audience_size'] / 375_429 - 32.99) < 0.01 and fixed['_projection_before_hold'] == 92_615_994)
    same = S.hold_projection_weight({'audience_size': 3_847, 'projected_audience_size': 126_907}, 'ok')
    check('a primitive at the house ratio is left alone', same['projected_audience_size'] == 126_907 and '_projection_before_hold' not in same)
    # the job holds the order and asks
    from prometheus import bpiq_job as BJ
    posted, statuses = [], []

    class FakeChat:
        def _pm_thread_for_job(self, u, j):
            return 't1'

        def _pm_load_thread_or_active(self, u, t):
            return []

        def _pm_history_has_job_turn(self, h, k, j):
            return False

        def _pm_iso_now(self):
            return '2026-11-20T18:00:00Z'

        def _pm_save_thread_or_active(self, u, t, h):
            posted.extend(h)

        def _pm_bpiq_status_write(self, j, d):
            statuses.append(d)

    BJ._chat = lambda: FakeChat()
    try:
        text = BJ.hold_and_ask('job-h', T.inputs(), str(raised.client_line), username='seat1', refund=lambda u, i, j: True)
        check('the plain question lands in the thread with the charge reversed',
              posted and posted[0]['text'].startswith('We need to confirm which') and posted[0]['text'].endswith('has been reversed.')
              and posted[0]['meta'].get('held_for_reader'))
        check("the job's status carries the same line for the status lane (a client failure line, relayed)",
              statuses and statuses[0]['status'] == 'error' and statuses[0]['error'] == text and statuses[0].get('held_for_reader'))
        from prometheus import status_lane as SL
        check('the status lane relays it as the reader-facing line', SL.client_failure_line(text) == text)
        posted.clear(); statuses.clear()
        BJ._job('job-h2')['user'] = 'seat1'          # prepare() leaves the seat on the job
        BJ.fail(None, T.inputs(), 'job-h2', raised)
        check('fail() on a held order goes the same way (the charge reversal is attempted; its outcome is said plainly)',
              posted and posted[0]['text'].startswith('We need to confirm which') and 'reversed' in posted[0]['text'])
        posted.clear()
        BJ.fail(None, T.inputs(), 'job-h3', RuntimeError('internal'))
        check('an ordinary error posts nothing to the reader', posted == [])
    finally:
        BJ._chat = lambda: __import__('prometheus.legacy.chat', fromlist=['x'])


def test_faster_answers():
    """Round 2, item 3 (Jenna 2026-11-23): the research call runs only when
    the held and recorded figures do not answer; anchors are read back
    across nearby windows and for longer; the independent lookups run side
    by side; the catalog is scanned once per read."""
    print('faster answers: the research gate')
    prof = {'profile': {'subject': 'X', 'audience_size': 1_000_001}, 'facts': [{'kind': 'audience_size'}]}
    check('a held subject with figures on file needs no research call', not K.research_needed('how big is X', researched=False, h=prof, led={'entries': []}, facts={'figures': [], 'size': None}))
    check('stored figures in the ledger answer too', not K.research_needed('how big is X', h={'profile': None, 'facts': []}, led={'entries': [{'metrics': [{'name': 'a', 'value': 1}]}]}, facts=None))
    check('figures across products answer too', not K.research_needed('how big is X', h={}, led={}, facts={'figures': [{'kind': 'reach_people', 'value': 5}], 'size': None}))
    check('nothing on file: research', K.research_needed('how big is X', h={'profile': None, 'facts': []}, led={'entries': []}, facts={'figures': [], 'size': None}))
    check('a subject we hold no profile on always researches', K.research_needed('how big is X', researched=True, h=prof, led={'entries': [{'metrics': [1]}]}, facts={'figures': [1]}))
    check('a question about the world always researches', K.research_needed('is there any awareness for the film Harbor Lights', h=prof, led={'entries': [{'metrics': [1]}]}, facts={'figures': [1]}))
    check('a near twin whose window changed researches (its dated events decide the band)', K.research_needed('how big is X', h=prof, led={'entries': [{'metrics': [1]}]}, facts={'figures': [1]}, twin={'ref': 'k'}, changed=[{'start': '2026-01-01', 'end': '2026-01-05'}]))
    os.environ['PM_KNOWLEDGE_RESEARCH_GATE'] = '0'
    try:
        check('gate off: every read researches, as before', K.research_needed('how big is X', h=prof, led={'entries': [{'metrics': [1]}]}, facts={'figures': [1]}))
    finally:
        os.environ.pop('PM_KNOWLEDGE_RESEARCH_GATE', None)
    print('faster answers: anchors read back across nearby windows, for longer')
    store = P.MemoryStore()
    K.STORE_FOR_TESTS = store
    calls = []

    def cj(system, user, **kw):
        calls.append(json.loads(user))
        return {'success': True, 'data': {'anchors': [{'name': 'US people who engaged with X', 'value': 2_345_679, 'unit': 'people', 'klass': 'audience',
                                                        'window': {'start': '2025-11-01', 'end': '2026-11-01'}, 'source_type': 'tracker', 'as_of': '2026-11-01'}],
                                          'events': [], 'sources': []}}
    try:
        ask = K.ask_from(None, 'how big is X', {'subject': 'X', 'source': 'panel'}, {'metric_family': 'audience'})
        ask['window'] = {'start': '2025-11-01', 'end': '2026-11-01'}
        r1 = K.research(ask, cj, None, store=store)
        check('the first ask researches and lands on the store with its subject index', len(calls) == 1 and r1['anchors'] and store.load(K._subject_index_key('X')) is not None)
        ask2 = dict(ask, window={'start': '2025-11-04', 'end': '2026-11-04'})
        r2 = K.research(ask2, cj, None, store=store)
        check('three days on, the same anchors are read back with no call', len(calls) == 1 and r2['cached'] and r2.get('near_window') and r2['anchors'][0]['value'] == 2_345_679)
        ask3 = dict(ask, window={'start': '2026-03-01', 'end': '2026-11-04'})
        r3 = K.research(ask3, cj, None, store=store)
        check('a window that is not near researches again', len(calls) == 2)
        ask4 = dict(ask, window={'start': '2025-11-02', 'end': '2026-11-02'}, family='purchases')
        r4 = K.research(ask4, cj, None, store=store)
        check('another family on the same subject is its own research', len(calls) == 3)
        r5 = K.research(dict(ask, window={'start': '2025-11-05', 'end': '2026-11-05'}), cj, None, store=store, call=False)
        check('with no call allowed, saved anchors still come back', r5['cached'] and not r5.get('skipped') and len(calls) == 3)
        r6 = K.research(dict(ask, subject='Never Seen Before Y'), cj, None, store=store, call=False)
        check('with no call allowed and nothing saved, the step is skipped quietly', r6.get('skipped') and not r6['anchors'] and len(calls) == 3)
        os.environ['PM_KNOWLEDGE_ANCHOR_TTL_DAYS'] = '45'
        try:
            check('the freshness window is thirty days by default and tunable from env', abs(K.anchor_ttl_s() - 45 * 86400) < 1)
        finally:
            os.environ.pop('PM_KNOWLEDGE_ANCHOR_TTL_DAYS', None)
        check('thirty days by default', abs(K.anchor_ttl_s() - 30 * 86400) < 1)
        check('a saved document twenty days old on an open window is still fresh', K._fresh({'saved_ts': __import__('time').time() - 20 * 86400, 'anchors': [1]}, {'start': '2026-01-01', 'end': '2026-12-31'}))
    finally:
        K.STORE_FOR_TESTS = None
    print('faster answers: side by side, the catalog scanned once')
    import time as _t
    t0 = _t.monotonic()
    out = K._side_by_side({'a': lambda: (_t.sleep(0.3), 'A')[1], 'b': lambda: (_t.sleep(0.3), 'B')[1], 'c': lambda: 1 / 0, 'd': lambda: (_t.sleep(0.3), 'D')[1]})
    dt = _t.monotonic() - t0
    check('four lookups run together: three answers, the one that raised contributes nothing, under a second in all', out.get('a') == 'A' and out.get('b') == 'B' and out.get('d') == 'D' and 'c' not in out and dt < 0.9)
    os.environ['PM_KNOWLEDGE_PARALLEL'] = '0'
    try:
        check('the switch runs them in turn with the same answers', K._side_by_side({'a': lambda: 'A', 'b': lambda: 'B'}) == {'a': 'A', 'b': 'B'})
    finally:
        os.environ.pop('PM_KNOWLEDGE_PARALLEL', None)
    from migration import corpus_catalog as CC
    from unittest import mock
    n = {'calls': 0}

    def fake_anchors_for(name, window=None, products=None, limit=60, with_ledger=True):
        n['calls'] += 1
        return {'subject': name, 'facts': [{'kind': 'audience_size', 'product': 'profile', 'label': 'US audience (projected US people)', 'value': 4_567_891, 'unit': 'people',
                                           'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'source': {'key': 'Z_01.csv'}, 'cut': False}], 'marks': []}
    K._CATALOG_MEMO.clear(); K._FACTS_CACHE.clear()
    calls2 = []
    with mock.patch.object(CC, 'anchors_for', fake_anchors_for), mock.patch.object(CC, 'anchors_block', lambda a, max_lines=40: 'CATALOG BLOCK Z'), \
            mock.patch.object(CC, 'profile_anchor', lambda a, w=None: {'subject': 'Z', 'audience_size': 4_567_891, 'sample_size': 138_462}):
        base = {'subject': 'Z', 'source': 'catalog', 's3_key': 'Z_01.csv'}
        blk = K.read_blocks('how many Z fans are women', {'metric_family': 'audience'}, base, pk=None, saved=None, catalog=None,
                            claude_json=lambda *a, **k: calls2.append(1) or {'anchors': [], 'events': []}, tools=None, history=[], body=None,
                            led={'block': '', 'exact': None, 'entries': []})
    kn = base.get('_knowledge') or {}
    check('a read on a held subject makes no research call and says so on the trail', not calls2 and kn.get('research_skipped') and not kn.get('research_call'))
    check('the catalog was scanned once for the read (held and the shared figures share it)', n['calls'] == 1)
    check('the read still carries the catalog block and the figures on file', 'CATALOG BLOCK Z' in blk and 'FIGURES ON FILE FOR Z' in blk)
    K.take_twin('Z', 'how many Z fans are women')
    K._CATALOG_MEMO.clear(); K._FACTS_CACHE.clear()
    calls3 = []
    with mock.patch.object(CC, 'anchors_for', lambda *a, **k: {'subject': 'Q', 'facts': [], 'marks': []}):
        base2 = {'subject': 'Harbor Lights', 'source': 'panel'}
        K.read_blocks('how many people watched Harbor Lights', {'metric_family': 'viewership'}, base2, pk=None, saved=None, catalog=None,
                      claude_json=lambda *a, **k: calls3.append(1) or {'anchors': [], 'events': []}, tools=None, history=[], body=None,
                      led={'block': '', 'exact': None, 'entries': []})
    check('a subject we hold no profile on still researches', len(calls3) == 1 and (base2.get('_knowledge') or {}).get('research_call'))
    K.take_twin('Harbor Lights', 'how many people watched Harbor Lights')


if __name__ == '__main__':
    test_ask_and_key()
    test_research_step_and_store()
    test_facts_block_order()
    test_read_blocks_paths()
    test_insight_blocks_paths()
    test_valuation_end_to_end()
    test_no_whole_audience_fallback()
    test_faster_answers()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
