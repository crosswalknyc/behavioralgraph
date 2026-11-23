#!/usr/bin/env python3
"""Three read-side fixtures from 2026-11-20 (Jenna), fixed for new runs.

1. The named subject over the open view (Emma Crouchen, VICE Sports on
   the open Pinterest profile): when the words name a subject the base
   does not describe, the read researches the named subject (its US
   audience size from the anchors, anchor low), the ledger is consulted
   for the named subject, the base's rows never ride the prompt as the
   subject's, and a decision of record on the named subject replays.
2. The size gate: a projected count never implies an audience larger
   than the subject could have (208,073,835 buyers at 73.9% implied
   about 281M people); the count is reduced to the anchored size times
   its share before anything ships; a stale calendar-year window on a
   researched read becomes the trailing twelve months.
3. A resumed read lands in the thread that asked (Liz, job 1fb22513ad09:
   two resumes posted 'Working on it' into another thread) and a chain
   that ends in error replaces its own 'Working on it' lines with one
   held line, never leaves them.

Hermetic: no model, no live store, no mail, no app boot; figures only."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_KNOWLEDGE', '1')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import consistency as X  # noqa: E402
from prometheus import knowledge as K  # noqa: E402
from prometheus import read_landing as RL  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402

FAIL = 0
BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'anthropic',
          'calibrated', 'nielsen', 'statista', 'according to', 'source:')

EMMA = ("I want to know if VICE Sports audience has interest in beauty and grooming products? "
        "I need stats for a sales proposal for Just for Men")
PINTEREST = {'subject': 'Pinterest', 's3_key': 'Pinterest_06_14_2026_01_37.csv', 'source': 'catalog', 'bound': True}
CALM = "Working on it. Confirmed your task is in progress and I will email you when it completes."
LIZ_Q = 'give me a 3-5 word description for CW prometheus versus AI'
ROOT, C1, C2 = '1fb22513ad09', '9ab736d9d2cc', '8408e0fa5135'
ASKED_IN, OPENED_LATER = 'api-8b09f13b-chat-1120-cefe', '8f1ddb09e3'


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    return '\u2014' not in low and '\u2013' not in low and not any(w in low for w in BANNED)


def T(ts, role, text, **meta):
    t = {'role': role, 'text': text, 'ts': ts}
    if meta:
        t['meta'] = meta
    return t


class MemoryStore:
    def __init__(self):
        self.docs = {}

    def load(self, key):
        return json.loads(json.dumps(self.docs[key])) if key in self.docs else None

    def save(self, key, doc):
        self.docs[key] = json.loads(json.dumps(doc))


# ------------------------------------------------------------ 1: the named subject over the open view

def test_named_subject():
    print('the subject the words name')
    check("Emma's words name VICE Sports with Pinterest open", K.named_subject(EMMA, {}, PINTEREST) == 'VICE Sports')
    check('and the Pinterest base is borrowed: another subject\'s profile', K.base_is_borrowed(EMMA, 'VICE Sports', PINTEREST))
    n = K.named_subject('what share of Pinterest users shop for beauty', {}, PINTEREST)
    check("'Pinterest users' names the base itself: not borrowed", not K.base_is_borrowed('what share of Pinterest users shop for beauty', n, PINTEREST))
    check("a comparison that names the base first ('how does Pinterest compare to TikTok') stays on the base",
          not K.base_is_borrowed('how does Pinterest compare to TikTok on beauty', K.named_subject('how does Pinterest compare to TikTok on beauty', {}, PINTEREST), PINTEREST))
    check("'this audience' names nothing: the base stands", K.named_subject('what percent of this audience buys beauty', {}, PINTEREST) == '')
    check("a question about our own product names no subject (Liz's words)", K.named_subject(LIZ_Q, {}, PINTEREST) == '')
    y = {'subject': 'Yellowstone - Avid Fan', 'source': 'page'}
    check('the page\'s own family is never borrowed (Yellowstone on the Yellowstone avid cut)',
          not K.base_is_borrowed('Does the Yellowstone audience over-index on trucks', K.named_subject('Does the Yellowstone audience over-index on trucks', {}, y), y))
    check('a panel base is never borrowed', not K.base_is_borrowed('who is the audience for Josephine', 'Josephine', {'subject': 'Josephine', 'source': 'panel'}))
    anne = {'subject': 'Anne Hathaway x Shiseido', 'source': 'catalog'}
    check("the talent of a valuation on file is inside the base's own name: 'Anne Hathaway' on Anne Hathaway x Shiseido is not borrowed",
          not K.base_is_borrowed('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26', K.named_subject('Anne Hathaway x Shiseido: Date: 8/27/26-9/8/26', {}, anne), anne))
    check('a base the reader chose by its words is never borrowed',
          not K.base_is_borrowed(EMMA, 'VICE Sports', dict(PINTEREST, chosen_by_words=True)))


def test_borrowed_base_is_researched():
    print('the read on a borrowed base researches the named subject')
    store = MemoryStore()
    K.STORE_FOR_TESTS = store
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    calls = []

    def claude_json(system, user, **kw):
        doc = json.loads(user)
        calls.append(doc)
        return {'success': True, 'data': {
            'anchors': [
                {'name': 'US people who engaged with VICE Sports in the window', 'value': 14_200_000, 'unit': 'people',
                 'window': {'start': '2025-11-20', 'end': '2026-11-20'}, 'klass': 'audience', 'source_type': 'tracker', 'as_of': '2026-11-01'},
                {'name': 'VICE Sports social followers (global)', 'value': 2_900_000, 'unit': 'followers',
                 'window': {'start': None, 'end': None}, 'klass': 'footprint', 'source_type': 'platform', 'as_of': '2026-11-01'},
                {'name': 'US men who bought a grooming product online', 'value': 61_000_000, 'unit': 'people',
                 'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'klass': 'reach', 'source_type': 'survey', 'as_of': '2026-02-01'},
            ],
            'events': [], 'sources': []}}

    record_block = 'FIGURES ON RECORD FOR VICE SPORTS: 11.4% of the audience bought a beauty or grooming product in the window.'
    consulted = []

    def fake_consult(subject=None, question=None, metric_family=None, history=None, **kw):
        consulted.append(subject)
        if subject and 'vice' in str(subject).lower():
            return {'subject': 'VICE Sports', 'skey': 'vicesports', 'entries': [{'k': 'vice|beauty', 'ts': 1.0, 'route': 'decision_of_record_proposal_just_for_men',
                                                                              'figures': {'beauty_share_pct': 11.4}}],
                    'block': record_block, 'exact': None}
        return {'subject': subject, 'skey': None, 'entries': [], 'block': '', 'exact': None}

    try:
        import insights_ledger as _il
        base = dict(PINTEREST)
        led = {'block': 'PINTEREST STORED FIGURES: 48% of Pinterest users shop beauty', 'exact': None,
               'entries': [{'k': 'pinterest|beauty', 'ts': 1.0, 'route': 'read'}]}
        # the research path on its own: a size saved for the subject binds the read instead
        # (2026-11-23), held by scripts/test_unheld_subjects.py in the parent repo
        with mock.patch.object(_il, 'consult', fake_consult), mock.patch.object(K, 'saved_people', lambda *a, **k: None):
            blk = K.read_blocks(EMMA, {}, base, pk=None, saved=None, catalog=lambda s: f'CATALOG ROWS FOR {s}',
                                claude_json=claude_json, tools=None, history=[], body=None, led=led)
        kn = base.get('_knowledge') or {}
        check('the named subject rides the read, the base is marked borrowed',
              kn.get('named_subject') == 'VICE Sports' and kn.get('borrowed_base') == 'Pinterest')
        check('the research was asked for the audience size first, on the named subject',
              calls and calls[0].get('subject') == 'VICE Sports' and 'audience_size_rule' in calls[0] and 'LOW end' in calls[0]['audience_size_rule'])
        check('the research window defaulted to the trailing twelve months, never a calendar year',
              (calls[0].get('window') or {}).get('start') and (calls[0]['window']['start'], calls[0]['window']['end']) == __import__('prometheus.window_scope', fromlist=['standing_default']).standing_default())
        check('the audience size is the low audience anchor (14,200,000), not the follower footprint and not the grooming reach',
              kn.get('size_cap') == 14_200_000 and kn.get('size_source') == 'research')
        check('the block fixes the size as binding, anchor low, and says every count sits inside it',
              'AUDIENCE SIZE (binding, anchor low). VICE Sports: 14,200,000 US people' in blk and 'no count implies an audience larger' in blk)
        check("the block says the rows in the prompt are Pinterest's, never VICE Sports' figures",
              "describe Pinterest, not VICE Sports" in blk and "no figure of Pinterest's is stated as VICE Sports'" in blk)
        check("Pinterest's catalog rows block is not appended for a borrowed base", 'CATALOG ROWS FOR' not in blk)
        check('the ledger was consulted for the named subject and the core\'s consult object now carries its record',
              'VICE Sports' in consulted and led.get('block') == record_block and led['entries'][0]['route'].startswith('decision_of_record'))
        check("Pinterest's stored figures left the consult object (they never bind this read)", 'PINTEREST STORED' not in json.dumps(led))
        sp = K.peek_size('VICE Sports', EMMA)
        check('the size and the default window are left for the gate after the model', sp and sp['cap'] == 14_200_000 and sp['window'].get('start'))
        check('the same size is left under the base subject too (the core may key the hold on it)', K.peek_size('Pinterest', EMMA) is not None)
        check('the prompt block is plain', clean(blk))
    finally:
        K.STORE_FOR_TESTS = None
        X.RECORDS_FOR_TESTS = None
        K.take_twin('VICE Sports', EMMA)
        K.take_twin('Pinterest', EMMA)


def test_banked_size_serves_a_dated_ask():
    print('a size banked for every future pull serves a dated ask without new research')
    store = MemoryStore()
    K.STORE_FOR_TESTS = store
    calls = []
    try:
        ask = K.ask_from(None, EMMA, {'subject': 'VICE Sports', 'source': 'panel'}, {'metric_family': 'purchases'})
        open_key = K.ask_key(dict(ask, window=None))
        store.save(K._anchor_doc_key(open_key), {
            'key': open_key, 'saved_ts': __import__('time').time(),
            'anchors': [{'name': 'VICE Sports fans in the US, trailing 12 months', 'value': 1_589_194, 'unit': 'people', 'klass': 'audience',
                         'window': {'start': '2025-11-20', 'end': '2026-11-20'}, 'source_type': 'platform', 'as_of': '2026-11-20'},
                        {'name': 'VICE Sports YouTube channel subscribers', 'value': 1_140_000, 'unit': 'people', 'klass': 'footprint',
                         'window': {'start': None, 'end': None}, 'source_type': 'platform', 'as_of': '2026-11-20'}],
            'events': []})
        s0, e0 = __import__('prometheus.window_scope', fromlist=['standing_default']).standing_default()
        dated = dict(ask, window={'start': s0, 'end': e0})
        r = K.research(dated, lambda *a, **k: calls.append(1) or {'anchors': [], 'events': []}, None, store=store)
        check('the dated ask read the open-window document back and made no research call', r.get('cached') and not calls and r['anchors'])
        check('its audience size is the banked one, 1,589,194 US people, the low audience anchor', X.anchor_low_people(r['anchors']) == 1_589_194)
    finally:
        K.STORE_FOR_TESTS = None


def test_held_profile_is_not_borrowed_but_still_capped():
    print('a subject we hold caps on its own profile size')
    cap = X.anchor_low_people([{'klass': 'audience', 'value': 14_200_000, 'unit': 'people'},
                               {'klass': 'audience', 'value': 22_000_000, 'unit': 'people'},
                               {'klass': 'footprint', 'value': 900_000, 'unit': 'followers'}])
    check('the low audience anchor wins over a larger one and over a follower footprint', cap == 14_200_000)
    check('with no audience anchor the reach class is next', X.anchor_low_people([{'klass': 'reach', 'value': 5_000_000, 'unit': 'people'}, {'klass': 'footprint', 'value': 100, 'unit': 'followers'}]) == 5_000_000)
    check('a footprint alone still gives a ceiling', X.anchor_low_people([{'klass': 'footprint', 'value': 900_000, 'unit': 'followers'}]) == 900_000)
    check('money and shares are never a size', X.anchor_low_people([{'klass': 'sales', 'value': 9e9, 'unit': 'usd'}, {'klass': 'share', 'value': 12.5, 'unit': 'pct'}]) is None)


# ------------------------------------------------------------ 2: the size gate

def emma_read():
    return {'subject': 'VICE Sports', 'headline': 'VICE Sports: 73.9% beauty buyers, 208,073,835 US people in the window.',
            'metrics': [{'name': 'beauty_category_buyers', 'label': 'Beauty category buyers', 'value': 208_073_835, 'unit': 'buyers'},
                        {'name': 'beauty_category_buyers_pct', 'label': 'Beauty category buyers share', 'value': 73.9, 'unit': 'pct'},
                        {'name': 'beauty_interest_audience', 'label': 'Beauty interest audience', 'value': 208_085_382, 'unit': 'people'},
                        {'name': 'men_share', 'label': 'Men', 'value': 27.3, 'unit': 'pct'},
                        {'name': 'sports_interest_index', 'label': 'Sports interest index', 'value': 26, 'unit': 'index'}],
            'reads': ['208,073,835 of them bought a beauty product in the window.', '27.3% are men.'],
            'window_start': '2025-01-01', 'window_end': '2025-12-31', 'window_label': 'Jan 1 2025 to Dec 31 2025'}


def test_size_gate():
    print('the size gate')
    win = {'start': '2025-11-20', 'end': '2026-11-20'}
    out, rec = X.size_gate(emma_read(), 14_200_000, salt='size|vicesports', window=win)
    v = {m['name']: m['value'] for m in out['metrics']}
    check('208,073,835 buyers at 73.9% implied about 281M people: reduced to the anchored size times the share',
          rec['moves'] and rec['moves'][0]['implied'] > 280_000_000 and 10_000_000 < v['beauty_category_buyers'] < 10_600_000)
    check('the reduced count is messy and under the ceiling', v['beauty_category_buyers'] % 10 != 0 and v['beauty_category_buyers'] < 14_200_000)
    check('the companion audience count moved with it and the two stay distinct', 10_000_000 < v['beauty_interest_audience'] < 10_600_000 and v['beauty_interest_audience'] != v['beauty_category_buyers'])
    check('shares and the index are untouched', v['beauty_category_buyers_pct'] == 73.9 and v['men_share'] == 27.3 and v['sports_interest_index'] == 26)
    check('the headline and the reads state the moved figure, not the old one',
          '208,073,835' not in out['headline'] and f"{v['beauty_category_buyers']:,}" in out['headline'] and '208,073,835' not in out['reads'][0])
    check('the stale calendar year became the trailing twelve months',
          rec['window_set'] and out['window_start'] == '2025-11-20' and out['window_end'] == '2026-11-20' and '2025' in out['window_label'] and '2026' in out['window_label'])
    # a read already inside its audience is untouched
    small = emma_read()
    small['metrics'][0]['value'] = 1_049_380
    small['metrics'][2]['value'] = 1_049_381
    small['headline'] = 'VICE Sports: 73.9% beauty buyers, 1,049,380 US people.'
    out2, rec2 = X.size_gate(small, 14_200_000, salt='x', window=None)
    check('counts inside the audience are left alone and a window the read states stands when none is given',
          not rec2['moves'] and not rec2['window_set'] and out2['window_start'] == '2025-01-01')
    # a count with no companion share above the ceiling comes under it
    bare = {'metrics': [{'name': 'people_reached', 'label': 'US people reached', 'value': 30_000_000, 'unit': 'people'}], 'headline': '30,000,000 reached.'}
    out3, rec3 = X.size_gate(bare, 14_200_000, salt='y')
    check('a bare count above the ceiling comes under it, never pinned on it',
          13_000_000 < out3['metrics'][0]['value'] < 14_200_000 and out3['metrics'][0]['value'] % 10 != 0 and '30,000,000' not in out3['headline'])
    # a window the question named (not a calendar year) is never replaced
    q = dict(emma_read(), window_start='2026-03-01', window_end='2026-05-31')
    out4, rec4 = X.size_gate(q, 14_200_000, salt='z', window=win)
    check('a window that is not a stale calendar year stands', not rec4['window_set'] and out4['window_start'] == '2026-03-01')
    check('no cap: nothing moves', X.size_gate(emma_read(), None, salt='n')[1].get('moves') == [])


def test_gate_runs_through_the_hold():
    print('the gate runs after the model through the hold')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    try:
        win = {'start': '2025-11-20', 'end': '2026-11-20'}
        K.leave_size('VICE Sports', EMMA, 14_200_000, window=win, source='research')
        data = K.hold_pending(emma_read(), question=EMMA, subject='VICE Sports')
        v = {m['name']: m['value'] for m in data['metrics']}
        check('hold_pending reduced the counts and set the window', v['beauty_category_buyers'] < 14_200_000 and data['window_start'] == '2025-11-20')
        recs = (X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or []
        check('a staff record of the gate was written, internal only', any(r.get('verdict') == 'size_gate' for r in recs))
        res = K.bind_after(emma_read(), question=EMMA, subject='VICE Sports')
        check('bind_after (after the coherence pass) applies the same gate', {m['name']: m['value'] for m in res['metrics']}['beauty_category_buyers'] < 14_200_000)
        K.take_twin('VICE Sports', EMMA)
        check('the pending size clears with the twin', K.peek_size('VICE Sports', EMMA) is None)
    finally:
        X.RECORDS_FOR_TESTS = None


# ------------------------------------------------------------ 3: a resumed read lands where it was asked

def liz_thread():
    return [T('2026-11-20T20:41:00.000Z', 'user', LIZ_Q),
            T('2026-11-20T20:41:03.000Z', 'agent', 'On it: reading Prometheus now', read_job_id=ROOT, kind='job', surface='analyze'),
            T('2026-11-20T20:45:00.000Z', 'user', 'another question'),
            T('2026-11-20T20:45:04.000Z', 'agent', 'another answer'),
            T('2026-11-20T20:49:26.000Z', 'agent', CALM, read_job_id=C1, kind='read'),
            T('2026-11-20T20:54:23.000Z', 'agent', CALM, read_job_id=C2, kind='read')]


def test_chain_lands_once():
    print('a chain lands once, in the ack that asked')
    out, how = RL.land(liz_thread(), 'read_job_id', C2, CALM, {'read_job_id': C2, 'kind': 'read', 'faulted': 'orphaned', 'held_ref': 'h1'},
                       chain=[ROOT, C1, C2])
    check("the chain's final held line fills the first ack and the two resume 'Working on it' turns come off",
          how == 'filled' and len(out) == 4 and sum(1 for t in out if 'Working on it' in t['text']) == 1)
    m = out[1]['meta']
    check('the turn keeps the id the reader\'s screen knows, carries the chain and the member that landed',
          m['read_job_id'] == ROOT and m['landed_job_id'] == C2 and m['read_job_chain'] == [ROOT, C1, C2] and m.get('held_ref') == 'h1' and m.get('landed_at'))
    check('the same held line again writes nothing', RL.land(out, 'read_job_id', C2, CALM, {'read_job_id': C2}, chain=[ROOT, C1, C2])[1] == 'already')
    answer = 'Here are three, each five words or fewer: ...'
    out2, how2 = RL.land(out, 'read_job_id', C2, answer, RL.read_meta(C2, {'reply': answer}), chain=[ROOT, C1, C2])
    check('a real answer later fills the held line in place (a progress line is a promise, never an answer)',
          how2 == 'filled' and out2[1]['text'] == answer and len(out2) == 4)
    check('every member reads landed afterwards',
          all(RL.landing_state(out2, 'read_job_id', j)[0] == 'landed' for j in (ROOT, C1, C2)))
    check('the answer again, from another member, writes nothing', RL.land(out2, 'read_job_id', C1, answer, RL.read_meta(C1, {'reply': answer}), chain=[ROOT, C1, C2])[1] == 'already')
    # a held calm line that already carries the landed mark is filled by the fresh pass, not left above the answer
    held = [T('1', 'user', 'q'), T('2', 'agent', CALM, read_job_id='j1', kind='read', landed_at='2026-11-20T00:00:00Z')]
    o3, h3 = RL.land(held, 'read_job_id', 'j2', 'The answer.', RL.read_meta('j2', {'reply': 'The answer.'}), chain=['j1', 'j2'])
    check("the fresh pass's answer replaces the held 'Working on it' line", h3 == 'filled' and len(o3) == 2 and o3[1]['text'] == 'The answer.')
    check('the single-job landing is unchanged: an ack fills, a landed turn is left alone',
          RL.land([T('1', 'user', 'q'), T('2', 'agent', 'On it', read_job_id='j9')], 'read_job_id', 'j9', 'A.', RL.read_meta('j9', {'reply': 'A.'}))[1] == 'filled'
          and RL.land([T('1', 'user', 'q'), T('2', 'agent', 'A.', read_job_id='j9', landed_at='x')], 'read_job_id', 'j9', 'B.', RL.read_meta('j9', {'reply': 'B.'}))[1] == 'already')


def test_chain_ids_and_thread():
    print('the chain and the thread that asked')
    docs = {ROOT: {'job_id': ROOT, 'thread_id': ASKED_IN, 'resumed_as': C1, 'status': 'working'},
            C1: {'job_id': C1, 'resumed_as': C2, 'status': 'working'},
            C2: {'job_id': C2, 'status': 'error'}}
    markers = {C1: {'root': ROOT, 'parent': ROOT, 'depth': 1, 'thread_id': ASKED_IN},
               C2: {'root': ROOT, 'parent': C1, 'depth': 2, 'thread_id': ASKED_IN}}
    with mock.patch.object(RR, '_status_doc', lambda j: docs.get(j)), mock.patch.object(RR, 'chain_marker', lambda j: markers.get(j, {})):
        check('the chain from the last member runs root first', RR.chain_ids(C2) == [ROOT, C1, C2])
        check('and from the root', RR.chain_ids(ROOT) == [ROOT, C1, C2])
        check('a read never resumed is a chain of one', RR.chain_ids('solo') == ['solo'])
        check("the thread is the one the question was asked on, from the member's record, its marker or the root's record",
              RR.chain_thread(ROOT) == ASKED_IN and RR.chain_thread(C2) == ASKED_IN and RR.chain_thread(C1) == ASKED_IN)
    # a marker written for a child carries the asking thread
    puts = {}

    class S3:
        def put_object(self, Bucket, Key, Body, ContentType):
            puts[Key] = json.loads(Body)

        def get_object(self, Bucket, Key):
            raise KeyError(Key)
    with mock.patch.object(RR, '_s3', lambda: (S3(), 'b')):
        doc = RR._write_chain(C1, ROOT, docs[ROOT], held_retry=False)
    check('the child\'s marker carries the asking thread', doc.get('thread_id') == ASKED_IN and doc.get('root') == ROOT and doc.get('depth') == 1)


def test_recover_scopes_the_resume_to_the_asking_thread():
    print('the resume runs on the thread that asked')
    import inspect
    src = inspect.getsource(RR.recover)
    i_scope, i_gen = src.find('set_request_thread(_ask_tid)'), src.find('c._pm_generate_metrics_response(')
    check('recover() sets the request thread to the asking thread before the fresh pass is launched',
          0 < i_scope < i_gen and 'chain_thread(job_id, claimed)' in src and 'set_current_thread(_ask_tid)' in src)
    src_f = inspect.getsource(RR._finalize)
    check('a chain that ends in error lands through the chain, in the asking thread',
          'chain=chain_ids(job_id, status)' in src_f and 'thread_id=chain_thread(job_id, status)' in src_f)
    src_ff = inspect.getsource(RR.finalize_faulted)
    check('a faulted resume lands the same way', 'chain=chain_ids(job_id, status)' in src_ff and 'tid = chain_thread(job_id, status) or' in src_ff)


def test_finalize_lands_one_held_line_in_the_asking_thread():
    print('the final error lands one held line where the question was asked')
    landed = []
    writes = []

    class FakeChat:
        def _pm_panel_refund(self, charge):
            pass

        def _pm_read_status_write(self, job_id, doc):
            writes.append((job_id, doc))

        def _pm_append_read_to_history(self, uname, job_id, payload, **kw):
            landed.append((uname, job_id, payload, kw))
            return 'filled'

        def _pm_notify_delete(self, job_id):
            pass

    class FakeHost:
        def _chatbot_error_email(self, *a, **k):
            raise AssertionError('no mail for a probe')

    status = {'job_id': C2, 'user': 'canary:liz', 'question': LIZ_Q, 'thread_id': '', 'resume': {'text': LIZ_Q}}
    with mock.patch.object(RR, '_chat', lambda: FakeChat()), mock.patch.object(RR, '_host', lambda: FakeHost()), \
            mock.patch.object(RR, 'chain_ids', lambda j, s=None: [ROOT, C1, C2]), mock.patch.object(RR, 'chain_thread', lambda j, s=None: ASKED_IN):
        RR._finalize(C2, status, 'orphaned 3 times')
    check('the status reads error with the plain line as its payload', writes and writes[0][1]['status'] == 'error' and writes[0][1]['payload']['reply'] == RR.FINAL_NOTE)
    check('the landing carries the whole chain and the thread that asked, never the one open now',
          landed and landed[0][3].get('chain') == [ROOT, C1, C2] and landed[0][3].get('thread_id') == ASKED_IN)
    check('the line the reader gets is the held line with the email promise, plain',
          landed[0][2]['reply'] == CALM and clean(landed[0][2]['reply']))


def test_the_landing_helper_reads_the_chain_itself():
    print('the thread landing helper finds the chain on its own')
    from prometheus.legacy import land as LD
    calls = {}

    class C:
        def _pm_thread_for_job(self, u, j):
            return None                       # a child the launch process never saw: no thread on the job

        def _pm_load_thread_or_active(self, u, tid):
            calls['tid'] = tid
            return liz_thread()

        def _pm_save_thread_or_active(self, u, tid, history):
            calls['saved'] = (tid, history)

        def _pm_iso_now(self):
            return '2026-11-20T21:00:00.000Z'

    with mock.patch.object(LD, '_C', C()), mock.patch.object(LD, '_pm_settle_ask', lambda *a, **k: None), \
            mock.patch.object(RR, 'chain_ids', lambda j, s=None: [ROOT, C1, C2]), mock.patch.object(RR, 'chain_thread', lambda j, s=None: ASKED_IN):
        how = LD._pm_append_read_to_history('liz', C2, {'reply': 'Here are three, each five words or fewer: ...', 'followups': []})
    check('the answer of the last member landed into the first ack, in the asking thread, and the other acks came off',
          how == 'filled' and calls.get('tid') == ASKED_IN and calls['saved'][0] == ASKED_IN and len(calls['saved'][1]) == 4
          and calls['saved'][1][1]['text'].startswith('Here are three') and calls['saved'][1][1]['meta']['read_job_id'] == ROOT)


def test_lines_plain():
    print('plain copy')
    check('the size block and the held line are plain', clean(X.size_block('VICE Sports', 14_200_000, researched=True, borrowed_from='Pinterest', window={'start': '2025-11-20', 'end': '2026-11-20'})) and clean(CALM))
    check('the research rule never reaches a reader (prompt text only) but is still free of dashes', '\u2014' not in K.RESEARCH_PROMPT)


if __name__ == '__main__':
    test_named_subject()
    test_borrowed_base_is_researched()
    test_banked_size_serves_a_dated_ask()
    test_held_profile_is_not_borrowed_but_still_capped()
    test_size_gate()
    test_gate_runs_through_the_hold()
    test_chain_lands_once()
    test_chain_ids_and_thread()
    test_recover_scopes_the_resume_to_the_asking_thread()
    test_finalize_lands_one_held_line_in_the_asking_thread()
    test_the_landing_helper_reads_the_chain_itself()
    test_lines_plain()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
