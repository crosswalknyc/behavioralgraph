#!/usr/bin/env python3
"""A probe's ask never writes to the shared insights ledger (2026-11-11).

A `canary:` page-analysis probe asked "What share of Chime Banking
Customers engage with insurance brands overall, and what is the index?"
on production. Its answer ("392,312,555 insurance-active individuals")
was banked under the real subject and replayed to whoever asked next.
The row was pulled by hand at 21:43 UTC and a second probe banked it
again at 21:45: the ledger never knew who was asking.

Holds, on every write path (persist, bank_record, ingest_deck_anchors
and the last step before S3):
  1. a named probe asker (canary:*, replay, smoke, golden, a sandbox
     key's owner) writes nothing: no index put, no entries/<date>/ put;
  2. a probe request under a real seat's session (the caller header, a
     probe user agent, g._pm_probe) writes nothing, on the page-answer
     path, the sizing path and a cross;
  3. the asker rides onto a thread (the page answer banks off the
     request) and into a job body (the worker's engine and deck jobs),
     and a job flagged probe never banks whatever seat it runs as;
  4. a real person still banks, and the entry is stamped with who asked;
  5. an operator's script (nobody to name) still banks on purpose;
  6. the hooks that carry the asker are in place at every thread and
     job launch (source check).

Hermetic: S3 is an in-memory stand-in; no network.
"""
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['PM_PROBE_LOOKUP'] = '1'

from flask import Flask, g, session  # noqa: E402

import insights_ledger as il  # noqa: E402
from prometheus import ledger_gate as lg  # noqa: E402
from prometheus import probe_identity as pi  # noqa: E402
from prometheus import shared_memory as shm  # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    """Just enough of S3 for the ledger: get / put / head on one bucket."""

    def __init__(self):
        self.objs = {}
        self.puts = []

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            from botocore.exceptions import ClientError
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': _Body(self.objs[Key]), 'ETag': '"%d"' % len(self.objs[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            from botocore.exceptions import ClientError
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        return {'ETag': '"%d"' % len(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.puts.append(Key)


s3 = FakeS3()
il._s3 = s3
il._SYNC_FOR_TESTS = True
pi.set_sandbox_owners(['acme_sandbox'])

app = Flask(__name__)
app.secret_key = 'test'

CHIME_Q = "What share of Chime Banking Customers engage with insurance brands overall, and what is the index?"
CHIME_REPLY = ("Across Chime Banking Customers, 392,312,555 insurance-active individuals engage with at least one "
               "insurance brand, 61.4% of the audience, an index of 118.")
METRICS = [{'name': 'insurance_active', 'value': 392312555, 'unit': 'individuals'}]


def writes():
    return [k for k in s3.puts if k.startswith('system/insights_ledger/')]


def reset():
    s3.puts.clear()
    lg.clear_refusals()


def index_rows(subject_key):
    try:
        doc = json.loads(s3.objs[il.INDEX_KEY])
    except Exception:
        return []
    return ((doc.get('subjects') or {}).get(subject_key) or {}).get('entries') or []


def persist_chime(**kw):
    il.persist(subject='Chime Banking Customers', metric_family='audience', question=CHIME_Q, route='page_analysis',
               metrics=METRICS, reply=CHIME_REPLY, provenance='operator', **kw)


print("[1] a named probe asker writes nothing, on any write path")
for who in ('canary:probe', 'canary:golden', 'canary:ua-probe', 'smoke', 'golden', 'sandbox_partner', 'acme_sandbox'):
    reset()
    persist_chime(asker=who)
    check(not writes(), f"persist as {who!r}: no index put, no entry put")
reset()
out = il.bank_record(subject='Chime Banking Customers', source='probe', metrics=METRICS, question=CHIME_Q,
                     reply=CHIME_REPLY, asker='canary:probe')
check(out == [] and not writes(), "bank_record as a probe writes nothing")
reset()
il.ingest_deck_anchors(subject='Chime Banking Customers', metrics=METRICS, source_name='probe deck', asker='canary:probe')
check(not writes(), "ingest_deck_anchors as a probe writes nothing")
reset()
e = il.make_entry(subject='Chime Banking Customers', metric_family='audience', question=CHIME_Q, route='page_analysis',
                  metrics=METRICS, reply=CHIME_REPLY)
e['asker'] = 'canary:probe'
il._record_entry_now(e)
check(not writes(), "the last step before S3 refuses an entry stamped by a probe")
check(any(r['asker'].startswith('canary') for r in lg.refusals()), "the refusal is on the trail")

print("[2] a probe request under a real seat's session writes nothing")
PROBE_REQS = [
    ('the caller header', {'X-Prometheus-Caller': 'probe'}, False),
    ('a named caller', {'X-Prometheus-Caller': 'viewership-count-probe'}, False),
    ('a probe user agent', {'User-Agent': 'crosswalk-canary/1.0'}, False),
    ('the golden set flag', {}, True),
]
for label, headers, gflag in PROBE_REQS:
    reset()
    with app.test_request_context('/api/brief-chat/analyze', method='POST', headers=headers):
        session['username'] = 'admin'
        if gflag:
            g._pm_probe = True
        persist_chime()
        shm.persist_answer(il, subject='Chime Banking Customers', question=CHIME_Q, reply=CHIME_REPLY,
                           base_profile_key='Chime_Banking_Customers_05_12_2026_15_12.csv')
        shm.persist_sizing(il, subject='Chime Banking Customers', tu=412873, avid=90811, us_audience=13620681)
        il.persist(subject='Chime Banking Customers', metric_family='audience',
                   question='cross of behaviors on Chime Banking Customers shelf 1', route='crosstab',
                   metrics=METRICS, reply=CHIME_REPLY, provenance='operator')
        # a caller that passes the seat's name is still the probe's request
        persist_chime(asker='admin')
        check(lg.current() and lg.current()[1] is True, f"{label}: the request reads as a probe")
    check(not writes(), f"{label} under the admin session: page answer, sizing, cross and a named seat all write nothing")

reset()
with app.test_request_context('/api/v1/ask', method='POST'):
    g._pm_ask_user = 'acme_sandbox'
    g._pm_api_key_owner = 'acme_sandbox'
    persist_chime()
check(not writes(), "the partner API on a sandbox key writes nothing")

reset()
with app.test_request_context('/api/v1/ask', method='POST'):
    g._pm_ask_user = 'canary:golden'
    persist_chime()
check(not writes(), "the golden grader's gated identity writes nothing")

print("[3] the asker rides onto a thread and into a job")
reset()
_real_sync = shm._sync
shm._sync = lambda: False
started = []
_real_thread = threading.Thread


class _Joinable(_real_thread):
    def start(self):
        started.append(self)
        super().start()


shm.threading.Thread = _Joinable
try:
    with app.test_request_context('/api/brief-chat/analyze', method='POST', headers={'X-Prometheus-Caller': 'probe'}):
        session['username'] = 'admin'
        shm.after_answer(il, lambda *a, **k: {'notes': []}, user='admin', question=CHIME_Q, reply=CHIME_REPLY,
                         subject='Chime Banking Customers')
    for t in started:
        t.join(10)
    check(len(started) == 1 and not writes(), "the page answer banks off the request: a probe's writes nothing on the thread")
    started.clear()
    reset()
    with app.test_request_context('/api/brief-chat/analyze', method='POST'):
        session['username'] = 'jessie'
        shm.after_answer(il, lambda *a, **k: {'notes': []}, user='jessie', question=CHIME_Q, reply=CHIME_REPLY,
                         subject='Chime Banking Customers Real')
    for t in started:
        t.join(10)
    rows = index_rows('chime_banking_customers_real')
    check(len(rows) == 1 and rows[0].get('asker') == 'jessie', "a real reader's page answer banks on the thread, stamped jessie")
finally:
    shm._sync = _real_sync
    shm.threading.Thread = _real_thread

reset()
with lg.as_asker('sandbox_partner'):
    persist_chime()
check(not writes(), "a job run as a sandbox key's owner writes nothing")
reset()
with lg.as_asker('jessie', probe=True):
    persist_chime()
    il.ingest_deck_anchors(subject='Chime Banking Customers', metrics=METRICS, source_name='deck.pptx')
check(not writes(), "a job flagged probe writes nothing, whatever seat it runs as (read, deck anchors)")

reset()
hits = []


def _job():
    persist_chime()
    hits.append(lg.current())


with app.test_request_context('/api/brief-chat/analyze', method='POST', headers={'X-Prometheus-Caller': 'probe'}):
    session['username'] = 'admin'
    fn = lg.carry(_job, 'admin')
t = threading.Thread(target=fn)
t.start()
t.join(10)
check(not writes() and hits and hits[0] == ('canary:probe', True), f"carry puts the probe on the thread: {hits}")

reset()
hits.clear()
with app.test_request_context('/api/brief-chat/status', method='GET', headers={'X-Prometheus-Caller': 'probe'}):
    session['username'] = 'admin'
    fn = lg.carry(_job, 'liz', probe=False, from_request=False)
t = threading.Thread(target=fn)
t.start()
t.join(10)
check(hits and hits[0] == ('liz', False) and writes(), "a job restarted on someone else's request banks as its own owner")

reset()
seen = []


@lg.job_scope()
def _read_job(job_id, pm_user, probe=False):
    persist_chime()
    seen.append(lg.current())


with app.test_request_context('/api/brief-chat/analyze', method='POST', headers={'X-Prometheus-Caller': 'probe'}):
    session['username'] = 'admin'
    lg.note_job('job-probe', 'admin', False)
t = threading.Thread(target=_read_job, args=('job-probe', 'admin'))
t.start()
t.join(10)
check(not writes() and seen and seen[0] == ('canary:probe', True), f"a job noted at launch runs as the probe that asked: {seen}")
reset()
seen.clear()
_read_job('job-worker', 'jessie', probe=True)
check(not writes() and seen[0] == ('jessie', True), "a job body handed a probe flag never banks")
reset()
seen.clear()
_read_job('job-real', 'jessie')
check(bool(writes()) and seen[0] == ('jessie', False), "a job with nothing noted banks as its own username")
reset()
seen.clear()
with lg.as_asker('liz'):
    _read_job('job-inline', 'jessie')
check(seen[0] == ('liz', False), "a scope the worker already set is left alone")

print("[4] a real person still banks, stamped with who asked")
reset()
with app.test_request_context('/api/brief-chat/analyze', method='POST'):
    session['username'] = 'ameseldzhyan'
    il.persist(subject='Digital Original Content', metric_family='audience', question='how many watch comedy',
               route='reasoned_metrics', metrics=[{'name': 'comedy', 'value': 4712883, 'unit': 'individuals'}],
               reply='4,712,883 people watch comedy.')
rows = index_rows('digital_original_content')
check(len(rows) == 1 and rows[0].get('asker') == 'ameseldzhyan', "a session user's read banks and carries the asker")
check(any(k.startswith(il.ENTRY_PREFIX) for k in writes()), "its entry object landed under entries/<date>/")
reset()
with lg.as_asker('jessie'):
    il.persist(subject='Georgie and Mandy', metric_family='audience', question='how big is the audience',
               route='reasoned_metrics', metrics=[{'name': 'aud', 'value': 1285455, 'unit': 'individuals'}], reply='1,285,455.')
rows = index_rows('georgie_and_mandy')
check(len(rows) == 1 and rows[0].get('asker') == 'jessie', "a worker job banks as the person it runs for")

print("[5] an operator's script still banks on purpose")
reset()
il.persist(subject='Ops Correction', metric_family='audience', question='corrected read', route='reasoned_metrics',
           metrics=[{'name': 'aud', 'value': 5885924, 'unit': 'individuals'}], reply='5,885,924.', sync=True)
rows = index_rows('ops_correction')
check(len(rows) == 1 and 'asker' not in rows[0], "nobody to name: banked, no asker stamped")
reset()
out = il.bank_record(subject='The Read', source='newsletter', metrics=[{'name': 'subs', 'value': 913277, 'unit': 'individuals'}],
                     question='how many', reply='913,277.')
check(bool(out) and writes(), "a decision of record from a script still banks")
for who in ('', 'unknown', 'anon'):
    check(lg.may_bank(who), f"asker {who!r} is nobody, not a probe")
check(lg.may_bank('replay'), "the replay stand-in banks (it only ever runs against the harness's own store)")
check(not lg.may_bank('canary:probe') and not lg.may_bank('golden') and lg.may_bank('caradorf'), "may_bank by name")

print("[6] the hooks that carry the asker are in place")


def src(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


chat = src('prometheus/legacy/chat.py')
check("_ledger_gate.note_job(job_id, _pm_user, _probe)" in chat
      and chat.index("_ledger_gate.note_job(job_id, _pm_user, _probe)") < chat.index("threading.Thread(target=_pm_run_read_job"),
      "the read job thread is told who asked before it starts")
check("_ledger_gate.note_job(job_id, username)" in chat
      and chat.index("_ledger_gate.note_job(job_id, username)") < chat.index("threading.Thread(target=_pm_run_deck_job"),
      "the deck job thread too")
check("@_ledger_gate.job_scope()\ndef _pm_run_read_job(" in chat and "@_ledger_gate.job_scope(probe_kw='')\ndef _pm_run_deck_job(" in chat,
      "both job bodies run as their asker")
rr = src('prometheus/read_recovery.py')
check(rr.count("as_asker(uname, probe=bool(status.get('probe')))") == 2, "the worker's engine and deck jobs run as their asker")
check("head['probe'] = True" in rr and "rec['probe'] = True" in rr, "an engine or deck job launched by a probe is flagged at launch")
check("'X-Prometheus-Caller': 'resume:read-worker'" in rr, "a probe's queued read still runs as a probe on the worker")
check("_lg.carry(fn)" in src('prometheus/shared_memory.py'), "the page-answer thread carries the asker")
check("from_request=False" in src('prometheus/jobs_sweeper.py'), "a swept job restarts as its own owner")
led = src('insights_ledger.py')
for fn_name in ('def persist(', 'def bank_record(', 'def ingest_deck_anchors(', 'def _record_entry_now('):
    body = led[led.index(fn_name):][:3600]
    check('may_bank(' in body, f"{fn_name.strip('def (')} asks the gate")
direct = [ln for ln in led.splitlines() if '_put_entry_object(' in ln and 'def ' not in ln]
check(len(direct) == 2, f"entry objects are written from two places only, both behind the gate ({len(direct)})")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all checks passed")
