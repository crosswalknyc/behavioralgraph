#!/usr/bin/env python3
"""Learn from every correction (Jenna 2026-11-23).

A reader says the answer was wrong (with or without the right figure), a
reader pushes back on a replayed answer, or staff correct a thread turn:
the wrong ledger row is retired, the wrong words are set aside on every
replay path, a corrected figure is recorded for the subject and window
(a staff correction as the decision of record, a reader's figure as a
correction row), and a sanitized fixture candidate reaches the staff-only
queue. The staff helper does the thread correction, merge-safe with
corrected_at, plus the ledger and fixture steps in one call.

Hermetic: the in-memory test ledger, no model, no mail, no app boot."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('PM_STORE_URL', '')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

import insights_ledger as IL  # noqa: E402
from prometheus import corrections as C  # noqa: E402
from prometheus import read_registry as RG  # noqa: E402
from prometheus import thread_merge as TM  # noqa: E402

C.SYNC_FOR_TESTS = True
IL._SYNC_FOR_TESTS = True
FAIL = 0

WRONG = ("On VICE Sports: 73.9% of the audience bought a beauty or grooming product in the window, 208,073,835 US people. "
         "Men are 27.3% of the audience. The sports interest index sits at 26.")
RIGHT = ("On VICE Sports: 11.4% of the audience bought a beauty or grooming product in the window, 181,168 US people. "
         "Men are 68.2% of the audience.")
Q = "I want to know if VICE Sports audience has interest in beauty and grooming products? I need stats for a sales proposal for Just for Men"


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def T(ts, role, text, **meta):
    t = {'role': role, 'text': text, 'ts': ts}
    if meta:
        t['meta'] = meta
    return t


def fresh_ledger(with_entry=True):
    led = IL.use_test_ledger({})
    if with_entry:
        IL.persist(subject='VICE Sports', metric_family='purchases', question=Q, route='reasoned_metrics',
                   metrics=[{'name': 'beauty_buyers_pct', 'label': 'Beauty buyers share', 'value': 73.9, 'unit': 'pct'},
                            {'name': 'beauty_buyers', 'label': 'Beauty buyers', 'value': 208073835, 'unit': 'people'}],
                   window_start='2025-01-01', window_end='2025-12-31', window_label='Jan to Dec 2025', reply=WRONG, sync=True,
                   asker='emma.crouchen@vice.com')
    return led


def live_entries(subject='VICE Sports'):
    doc = IL._load_index(force=True)
    out = []
    for sk, sub in (doc.get('subjects') or {}).items():
        for e in (sub.get('entries') or []):
            if isinstance(e, dict) and C._fold(e.get('subject')) == C._fold(subject):
                out.append(e)
    return out


def objects():
    return IL._client().objects


# ------------------------------------------------------------ 1. figures in a correction

def test_figures_in():
    print('figures a correction states')
    f = C.figures_in("no, it's about 1.6M people, not 208M")
    check("'1.6M, not 208M' keeps the stated figure and drops the rejected one", [x['value'] for x in f] == [1_600_000.0])
    f = C.figures_in("the right number is 11.4%")
    check('a percent reads as pct', f and f[0]['unit'] == 'pct' and f[0]['value'] == 11.4)
    f = C.figures_in("total brand value should be $104,140,107.40")
    check('a dollar figure reads as usd with cents', f and f[0]['unit'] == 'usd' and abs(f[0]['value'] - 104140107.40) < 0.001)
    check('a year alone is not a figure', C.figures_in("that was in 2025, not 2024") == [])
    check('a list length is not a figure', C.figures_in("give me a 3-5 word description") == [])
    check('a plain complaint states no figure', not C.states_a_figure("that is wrong") and not C.figures_in("that is wrong, try again"))
    check("'should be 1,589,194' states a figure", C.states_a_figure("should be 1,589,194 people") and C.figures_in("should be 1,589,194 people")[0]['value'] == 1_589_194)


def test_wrong_turn():
    print('the turn a complaint points at')
    hist = [T('1', 'user', Q), T('2', 'agent', 'On it: reading VICE Sports now', read_job_id='j1'),
            T('3', 'agent', WRONG, read_job_id='j1', kind='read', subject='VICE Sports', landed_at='x',
              figures=[{'value': 208073835, 'kind': 'count', 'label': 'Beauty buyers'}, {'value': 73.9, 'kind': 'pct', 'label': 'Beauty buyers share'}]),
            T('4', 'user', "that's wrong, VICE Sports is about 1.6M people")]
    i, t = C.wrong_turn(hist, hist[-1]['text'])
    check('the landed answer, not the progress line, not the complaint itself', i == 2 and t['text'] == WRONG)
    hist2 = [T('1', 'user', 'q'), T('2', 'agent', 'Which audience should I read for this?'), T('3', 'user', 'no that is wrong')]
    check('a bare question back is never the wrong turn (nothing to retire)', C.wrong_turn(hist2, 'no that is wrong') == (None, None))
    hist2b = [T('1', 'user', 'q'), T('2', 'agent', 'The audience leans young and urban.'), T('3', 'user', 'no that is wrong')]
    check('a prose answer with no figure is the fallback', C.wrong_turn(hist2b, 'no that is wrong')[0] == 1)
    hist3 = [T('1', 'user', 'q'), T('2', 'agent', WRONG, corrected_at='2026-11-20T00:00:00Z'), T('3', 'user', 'wrong')]
    check('an already corrected turn is never the wrong turn again', C.wrong_turn(hist3, 'wrong') == (None, None))


# ------------------------------------------------------------ 2. a reader's correction

def test_reader_correction_with_figure():
    print("a reader's correction with the right figure")
    fresh_ledger()
    check('the wrong row is live before', any(not e.get('retired') for e in live_entries()))
    hist = [T('1', 'user', Q),
            T('2', 'agent', WRONG, read_job_id='j1', kind='read', subject='VICE Sports', landed_at='x',
              figures=[{'value': 208073835, 'kind': 'count', 'label': 'Beauty buyers', 'source': 'read'},
                       {'value': 73.9, 'kind': 'pct', 'label': 'Beauty buyers share', 'source': 'read'}]),
            T('3', 'user', "this is wrong, the VICE Sports audience is about 1.6M people, not 208M. Email me at emma@example.com")]
    job = {'job_id': 'j1', 'question': Q, 'payload': {'reply': WRONG, 'window_start': '2025-01-01', 'window_end': '2025-12-31',
                                                      'window_label': 'Jan to Dec 2025', 'profile': 'VICE Sports',
                                                      'metrics': [{'name': 'beauty_buyers', 'label': 'Beauty buyers', 'value': 208073835, 'unit': 'people'},
                                                                  {'name': 'beauty_buyers_pct', 'label': 'Beauty buyers share', 'value': 73.9, 'unit': 'pct'}]}}
    done = C.learn_from_reader('emma.crouchen@vice.com', 'a0b18b21ba', hist, hist[-1]['text'], view='profileIQ', job_doc_loader=lambda j: job)
    check('the wrong row is retired by its words', done and len(done['retired']) == 1 and all(e.get('retired') for e in live_entries() if e.get('reply') == WRONG))
    check('the words are set aside', done['set_aside'] >= 1 and IL.reply_set_aside(WRONG))
    check('and still set aside with a replay lead in front', IL.reply_set_aside('Same read as earlier (Nov 20).\n' + WRONG))
    led = IL.consult(subject='VICE Sports', question=Q)
    check('the exact replay is gone', not led.get('exact'))
    rows = [e for e in live_entries() if e.get('route') == C.READER_ROUTE and not e.get('retired')]
    check("the reader's figure is recorded as a correction row for the subject and window",
          rows and rows[0]['ws'] == '2025-01-01' and rows[0]['we'] == '2025-12-31'
          and any(abs(float(m['value']) - 1_600_000) < 1 and m['name'] == 'beauty_buyers' for m in rows[0]['metrics']))
    check("a reader's figure is never a decision of record", not any(IL.is_record_entry(e) for e in rows))
    check('the recorded row rides the prompt block for the next read', '1,600,000' in (led.get('block') or '') or '1.6' in (led.get('block') or ''))
    key = done['candidate']
    check('a fixture candidate was queued under the staff-only prefix', key.startswith(C.CANDIDATES_PREFIX) and key in objects())
    case = json.loads(objects()[key][0])
    flat = json.dumps(case)
    check('the candidate carries no seat, no address and no thread id',
          'emma.crouchen' not in flat and 'emma@example.com' not in flat and 'a0b18b21ba' not in flat and case['seat'] == 'fx')
    check('and says what it expects: never the wrong words, the correction, the figures',
          case['expected']['never_says'].startswith('On VICE Sports: 73.9%') and '1.6M' in case['expected']['correction']
          and case['expected']['figures'][0]['value'] == 1_600_000.0 and case['needs_invented_names'] and 'VICE Sports' in case['names_to_invent'])
    idx = json.loads(objects()[C.CANDIDATES_INDEX][0])
    check('the queue index lists it as pending', any(r['id'] == case['id'] and r['status'] == 'pending' for r in idx['candidates']))
    check('listed through the helper', any(r['id'] == case['id'] for r in C.list_candidates()))
    # the same correction again writes nothing new
    done2 = C.learn_from_reader('emma.crouchen@vice.com', 'a0b18b21ba', hist, hist[-1]['text'], job_doc_loader=lambda j: job)
    check('a second pass is idempotent on the set-aside and the queue', done2['set_aside'] == 0 and done2['candidate'] == key)


def test_reader_complaint_without_figure():
    print('a plain complaint: retire and set aside, nothing recorded')
    fresh_ledger()
    hist = [T('1', 'user', Q), T('2', 'agent', WRONG, kind='read', subject='VICE Sports'), T('3', 'user', "that's not right, try again")]
    done = C.learn_from_reader('emma.crouchen@vice.com', 't1', hist, hist[-1]['text'])
    check('retired and set aside', len(done['retired']) == 1 and IL.reply_set_aside(WRONG))
    check('no figure recorded', done['recorded'] == [])
    check('a candidate still queued', done['candidate'])


def test_rejected_replay():
    print('a stored answer the reader pushed back on')
    fresh_ledger()
    entry = next(e for e in live_entries() if e.get('reply') == WRONG)
    hist = [T('1', 'user', Q), T('2', 'agent', WRONG), T('3', 'user', "this doesn't make sense, Pinterest isn't the subject")]
    done = C.learn_from_rejected_entry('emma.crouchen@vice.com', 't1', hist, entry)
    check('the row is retired and its words set aside', len(done['retired']) == 1 and IL.reply_set_aside(WRONG))
    check('the exact replay never serves a set-aside reply', IL._exact_if_fresh(dict(entry)) is None)


def test_set_aside_blocks_every_replay_path():
    print('the set-aside check on every replay path')
    fresh_ledger(with_entry=False)
    IL.set_aside_reply(WRONG, subject='VICE Sports', by='test')
    check('a reply set aside with no ledger row behind it is still set aside', IL.reply_set_aside(WRONG))

    class S3:
        def __init__(self, docs):
            self.docs = docs

        def get_object(self, Bucket=None, Key=None):
            if Key not in self.docs:
                raise KeyError(Key)
            return {'Body': io.BytesIO(json.dumps(self.docs[Key]).encode('utf-8'))}

    import time as _t
    from prometheus import question_key as _qk
    reg = {_qk.key(Q): {'job_id': 'j9', 'started_at': _t.time(), 'question': Q, 'subject': 'VICE Sports'}}
    s3 = S3({RG.doc_key('liz'): reg, 'system/prometheus_reads/j9.json': {'job_id': 'j9', 'status': 'done', 'payload': {'reply': WRONG}}})
    check('the read registry never hands back a set-aside payload', RG.recent(s3, 'b', 'system/prometheus_reads/', 'liz', Q) is None)
    s3b = S3({RG.doc_key('liz'): reg, 'system/prometheus_reads/j9.json': {'job_id': 'j9', 'status': 'done', 'payload': {'reply': RIGHT}}})
    check('and still replays a payload that was never set aside', (RG.recent(s3b, 'b', 'system/prometheus_reads/', 'liz', Q) or {}).get('reply') == RIGHT)


# ------------------------------------------------------------ 3. the staff helper

def test_staff_correct_turn():
    print('the staff helper: thread, job, ledger and fixture in one call')
    fresh_ledger()
    stored = {('skolton', 'dcc24fb595'): [T('2026-11-20T18:00:00.000Z', 'user', Q),
                                          dict(T('2026-11-20T18:00:40.000Z', 'agent', WRONG, read_job_id='j7', kind='read', subject='VICE Sports',
                                                 landed_at='2026-11-20T18:00:40.000Z'), turn_id='t7')]}
    jobs = {'j7': {'job_id': 'j7', 'user': 'skolton', 'question': Q, 'status': 'done', 'thread_id': 'dcc24fb595',
                   'payload': {'reply': WRONG, 'window_start': '2025-11-20', 'window_end': '2026-11-20', 'profile': 'VICE Sports',
                               'metrics': [{'name': 'beauty_buyers', 'label': 'Beauty buyers', 'value': 208073835, 'unit': 'people'}]}}}
    saves = []

    def load(u, t):
        return [dict(x) for x in stored[(u, t)]]

    def save(u, t, history):
        # the merge the chat module runs: stored + incoming, corrected_at wins
        merged = TM.merge_turns(stored[(u, t)], history)
        stored[(u, t)] = merged
        saves.append(merged)
        return merged

    report = C.correct_turn('skolton', 'dcc24fb595', RIGHT, by='jenna', job_id='j7',
                            figures=['beauty_buyers=181168 people', 'beauty_buyers_pct=11.4 pct'], subject='VICE Sports',
                            window={'start': '2025-11-20', 'end': '2026-11-20'}, note='proposal figures',
                            load=load, save=save, job_doc=lambda j: jobs.get(j), job_write=lambda j, d: jobs.__setitem__(j, d))
    turns = stored[('skolton', 'dcc24fb595')]
    t = next(x for x in turns if (x.get('meta') or {}).get('read_job_id') == 'j7')
    check('the turn carries the new words and the correction stamp, the old words kept for audit',
          t['text'] == RIGHT and t['meta'].get('corrected_at') == report['corrected_at'] and t['meta']['corrected_by'] == 'staff:jenna'
          and t['meta']['retired'].startswith('On VICE Sports: 73.9%') and t['meta'].get('server_note') and len(turns) == 2)
    check('the turn keeps its id and moment', t.get('turn_id') == 't7' and t['ts'] == '2026-11-20T18:00:40.000Z')
    check('the job document carries the corrected reply and the stamp (the registry never replays it)',
          report['job_corrected'] and jobs['j7']['payload']['reply'] == RIGHT and jobs['j7'].get('corrected_at') == report['corrected_at']
          and jobs['j7']['payload']['retired_reply'].startswith('On VICE Sports'))
    learned = report['learned'] or {}
    check('the old words are retired in the ledger and set aside', len(learned.get('retired') or []) == 1 and IL.reply_set_aside(WRONG))
    recs = [e for e in live_entries() if IL.is_record_entry(e) and not e.get('retired')]
    check('the figures are banked as the decision of record for the subject and window, with the new words as the reply',
          recs and recs[0]['route'] == 'decision_of_record_' + C.STAFF_SOURCE and recs[0]['ws'] == '2025-11-20' and recs[0]['we'] == '2026-11-20'
          and {m['name']: m['value'] for m in recs[0]['metrics']} == {'beauty_buyers': 181168.0, 'beauty_buyers_pct': 11.4}
          and recs[0]['reply'].startswith('On VICE Sports: 11.4%'))
    led = IL.consult(subject='VICE Sports', question=Q)
    check('the exact re-ask replays the corrected words, never the old', (led.get('exact') or {}).get('reply') == RIGHT)
    key = learned.get('candidate') or ''
    case = json.loads(objects()[key][0]) if key in objects() else {}
    check('a staff candidate is queued, marked staff, without the seat', case.get('by') == 'staff' and 'skolton' not in json.dumps(case))
    # merge safety: a stale client copy of the thread (the old words) cannot write over the correction
    stale = [dict(x) for x in stored[('skolton', 'dcc24fb595')]]
    stale[1] = dict(stale[1], text=WRONG, meta={k: v for k, v in stale[1]['meta'].items() if k not in ('corrected_at', 'corrected_by', 'retired', 'server_note')})
    merged = TM.merge_turns(stored[('skolton', 'dcc24fb595')], stale)
    t2 = next(x for x in merged if (x.get('meta') or {}).get('read_job_id') == 'j7')
    check("a stale copy of the old words merged in later never wins over the correction (merge-safe with corrected_at)", t2['text'] == RIGHT and len(merged) == 2)
    # a second correction with a newer stamp wins
    report2 = C.correct_turn('skolton', 'dcc24fb595', RIGHT + ' Updated.', by='jessie', job_id='j7', load=load, save=save,
                             job_doc=lambda j: jobs.get(j), job_write=lambda j, d: jobs.__setitem__(j, d), learn=False)
    t3 = next(x for x in stored[('skolton', 'dcc24fb595')] if (x.get('meta') or {}).get('read_job_id') == 'j7')
    check('a newer correction replaces an older one', t3['text'].endswith('Updated.') and t3['meta']['corrected_by'] == 'staff:jessie'
          and report2['corrected_at'] >= report['corrected_at'])
    try:
        C.correct_turn('skolton', 'dcc24fb595', 'x', by='jenna', job_id='nope', load=load, save=save, job_doc=lambda j: None, job_write=lambda j, d: None)
        check('a turn that cannot be found raises', False)
    except LookupError:
        check('a turn that cannot be found raises', True)


def test_cli_refuses_in_test_mode():
    print('the CLI')
    env = dict(os.environ, REGRESSION_TEST_MODE='1')
    p = subprocess.run([sys.executable, str(WEB / 'scripts' / 'pm_correct_turn.py'), '--user', 'x', '--thread', 'y', '--job', 'z', '--text', 'a', '--by', 'me'],
                       capture_output=True, text=True, env=env, timeout=120)
    check('refuses to run inside a test process', p.returncode == 2 and 'refusing' in (p.stdout + p.stderr))
    p2 = subprocess.run([sys.executable, str(WEB / 'scripts' / 'pm_correct_turn.py'), '--help'], capture_output=True, text=True, env=env, timeout=120)
    check('--help names the four steps', p2.returncode == 0 and 'decision of record' in p2.stdout and 'corrected_at' in p2.stdout)


def test_switch_and_plain():
    print('the switch and the words')
    os.environ['PM_CORRECTIONS'] = '0'
    try:
        check('off: a reader complaint learns nothing', C.learn_from_reader('u', 't', [T('1', 'user', 'q'), T('2', 'agent', WRONG), T('3', 'user', 'wrong')], 'wrong') is None)
    finally:
        os.environ.pop('PM_CORRECTIONS', None)
    src = Path(C.__file__).read_text(encoding='utf-8')
    check('no em dash in the module', '\u2014' not in src and '\u2013' not in src)
    check('nothing is mailed and nobody is told', 'send_email' not in src and "client('ses'" not in src and 'smtplib' not in src)


if __name__ == '__main__':
    test_figures_in()
    test_wrong_turn()
    test_reader_correction_with_figure()
    test_reader_complaint_without_figure()
    test_rejected_replay()
    test_set_aside_blocks_every_replay_path()
    test_staff_correct_turn()
    test_cli_refuses_in_test_mode()
    test_switch_and_plain()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
