#!/usr/bin/env python3
"""A stored row that was set aside is handed back by nothing (Jenna
2026-11-18, Dark Matter Season 2).

Four stored rows from an older read put a season's first 30 days far
above the tracker that is held now. They were set aside with the
ledger's own stamp, which takes a row out of replay and out of the
published block. Three other places could still hand the same words
back, and none of them reads the stamp on the row:

  * the same question asked again in the thread it was first asked in
    ("Same read as earlier in this thread"),
  * a base held from the thread's earlier reads, which rewrites a new
    read's count to the old one,
  * the catalog of stated figures, which re-reads a thread every time it
    changes and banks every figure in it again.

What this holds:

  1. the ledger: a row set aside leaves replay and the published block,
     and `reply_set_aside` says so for its exact words only
  2. the thread shortcut: the earlier answer is handed back until its
     row is set aside, then the question runs again
  3. the thread's bases: a read whose row was set aside holds nothing,
     from the thread and from its job record
  4. the catalog: the reply's figures leave, and a second pass over the
     same thread does not bank them again
  5. the conversation is never changed by any of it

Hermetic: an in-memory ledger, a stub catalog store, no model, no mail,
no live read or write.
"""
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['CORPUS_CATALOG'] = '1'
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import insights_ledger as IL  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import thread_bases as TB  # noqa: E402
from migration import corpus_catalog as CC  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


Q1 = 'How many signups did Harbor Lights Season 2 drive on Northstar?'
R1 = ('Harbor Lights Season 2 drove 238,417 new US Northstar signups in the first 31 days of the season, '
      '12.7% of every signup the service took in that window.\n\n'
      '- Attributed new US signups: 238,417 (new paid US accounts whose first play was a Season 2 episode)\n'
      '- Unique US viewers of Season 2: 6,113,257')
Q2 = 'How many people watched Harbor Lights Season 1 on Northstar?'
R2 = ('Harbor Lights Season 1 reached 2,999,979 US viewers on Northstar across its release window.\n\n'
      '- Unique US viewers of Season 1: 2,999,979\n- New US signups in the window: 61,407')
K1 = 'harbor_lights_season_2|subscribers|2026-08-28|2026-09-27'
K2 = 'harbor_lights_season_1|viewership|2024-05-08|2024-06-26'


def row(subject, k, q, reply, fam, ws, we, metrics):
    return IL.subject_key(subject), {'subject': subject, 'entries': [{
        'k': k, 'subject': subject, 'family': fam, 'q': q, 'qn': IL.normalize_question(q), 'route': 'reasoned_metrics',
        'ts': '2026-09-25T16:59:11Z', 'ws': ws, 'we': we, 'wl': '', 'metrics': metrics, 'anchors': [], 'base': '',
        'cohort': '', 'universe': '', 'breakdown': None, 'reply': reply, 'followups': [], 'derivation': '',
        'prov': 'operator'}]}


def fresh_ledger():
    subjects = dict([
        row('Harbor Lights Season 2', K1, Q1, R1, 'subscribers', '2026-08-28', '2026-09-27',
            [{'name': 'signups', 'label': 'Attributed new US signups', 'value': 238417, 'unit': 'signups'},
             {'name': 'viewers', 'label': 'Unique US viewers of Season 2', 'value': 6113257, 'unit': 'viewers'}]),
        row('Harbor Lights Season 1', K2, Q2, R2, 'viewership', '2024-05-08', '2024-06-26',
            [{'name': 'viewers', 'label': 'Unique US viewers of Season 1', 'value': 2999979, 'unit': 'viewers'}]),
    ])
    IL.use_test_ledger(index={'subjects': subjects, 'updated': '2026-11-18T20:30:00Z'})


THREAD = [
    {'role': 'user', 'text': Q1, 'ts': '2026-09-25T16:58:40Z'},
    {'role': 'agent', 'text': R1, 'ts': '2026-09-25T16:59:11Z', 'meta': {'kind': 'read', 'read_job_id': '18e3dffb8f93',
                                                                        'options': ['Split by surface']}},
    {'role': 'user', 'text': Q2, 'ts': '2026-09-25T17:10:00Z'},
    {'role': 'agent', 'text': R2, 'ts': '2026-09-25T17:10:40Z', 'meta': {'kind': 'read', 'read_job_id': '400184b046f9',
                                                                        'options': []}},
]
BEFORE = copy.deepcopy(THREAD)

# ---------------------------------------------------------------------------
print('1. the ledger')
# ---------------------------------------------------------------------------
fresh_ledger()
led = IL.consult(question=Q1) or {}
check('before: the stored row answers its own question', (led.get('exact') or {}).get('reply') == R1
      and '238,417' in (led.get('block') or ''))
check('before: nothing is set aside', not IL.reply_set_aside(R1) and not IL.reply_set_aside(R2))
n = IL.retire_entries(keys=[K1], by='operator', note='set aside by decision')
check('one row is stamped, through the ledger\'s own retire', n == 1)
led = IL.consult(question=Q1) or {}
check('after: the row is not replayed', not led.get('exact'), str(led.get('match')))
check('after: its figures are out of the published block', '238,417' not in (led.get('block') or '')
      and '6,113,257' not in (led.get('block') or ''))
check('the row stays in the store for audit', any(
    e.get('k') == K1 and (e.get('retired') or {}).get('by') == 'operator'
    for v in IL._load_index()['subjects'].values() for e in v.get('entries') or []))
check('its exact words read as set aside', IL.reply_set_aside(R1))
check('whatever the spacing', IL.reply_set_aside('  ' + R1.replace('\n\n', '\n') + ' \n'))
check('other words do not', not IL.reply_set_aside(R2) and not IL.reply_set_aside(R1 + ' More.')
      and not IL.reply_set_aside('238,417') and not IL.reply_set_aside('') and not IL.reply_set_aside(None))
led = IL.consult(question=Q2) or {}
check('a live row is untouched', (led.get('exact') or {}).get('reply') == R2)
# the same words on a live row elsewhere are not set aside
_d = IL._load_index()
_d['subjects']['copy'] = {'subject': 'Copy', 'entries': [dict(next(iter(_d['subjects'].values()))['entries'][0],
                                                              k='copy|subscribers|a|b', retired=None)]}
IL.use_test_ledger(index=copy.deepcopy(_d))
check('words that also sit on a live row are not set aside', not IL.reply_set_aside(R1))
fresh_ledger()

# ---------------------------------------------------------------------------
print('2. the same question asked again in its thread')
# ---------------------------------------------------------------------------
tr = SM.thread_replay(Q1, THREAD)
check('before: the earlier answer is handed back', bool(tr) and R1 in tr['reply']
      and tr['reply'].startswith('Same read as earlier in this thread'))
IL.retire_entries(keys=[K1], by='operator', note='set aside by decision')
tr = SM.thread_replay(Q1, THREAD)
check('after: it is not, so the question runs again', tr is None, str(tr)[:120])
tr2 = SM.thread_replay(Q2, THREAD)
check('another answer in the same thread is still handed back', bool(tr2) and R2 in tr2['reply'])

# ---------------------------------------------------------------------------
print('3. a base held from the thread')
# ---------------------------------------------------------------------------
reads = TB.thread_reads(THREAD)
check('the read that was set aside holds nothing', [r['reply'] for r in reads] == [R2],
      str([r['reply'][:40] for r in reads]))
promise = [{'role': 'user', 'text': Q1},
           {'role': 'agent', 'text': 'On it: reading Harbor Lights Season 2 now. The read will land right here.',
            'meta': {'read_job_id': '18e3dffb8f93'}}]
docs = {'18e3dffb8f93': {'status': 'done', 'payload': {'reply': R1, 'metrics': []}, 'finished_at': '2026-09-25T16:59:11Z'}}
check('nor does its job record', TB.thread_reads(promise, docs.get) == [])
fresh_ledger()
check('while the row is live both hold as before', [r['reply'] for r in TB.thread_reads(THREAD)] == [R1, R2]
      and [r['reply'] for r in TB.thread_reads(promise, docs.get)] == [R1])
IL.retire_entries(keys=[K1], by='operator', note='set aside by decision')

# ---------------------------------------------------------------------------
print('4. the catalog of stated figures')
# ---------------------------------------------------------------------------
from botocore.exceptions import ClientError  # noqa: E402


def _err(code, status=404):
    return ClientError({'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}, 'Op')


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, body):
        return hashlib.md5(body).hexdigest()

    def get_object(self, Bucket, Key, **_kw):
        if Key not in self.objs:
            raise _err('NoSuchKey')
        return {'Body': _Body(self.objs[Key]), 'ETag': '"%s"' % self._etag(self.objs[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _err('404')
        return {'ETag': '"%s"' % self._etag(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None, **_kw):
        cur = self.objs.get(Key)
        if IfNoneMatch == '*' and cur is not None:
            raise _err('PreconditionFailed', 412)
        if IfMatch is not None and (cur is None or self._etag(cur) != IfMatch):
            raise _err('PreconditionFailed', 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else Body.encode('utf-8')
        return {}


fake = FakeS3()
CC.set_client(fake)
SUBJ, USER, TID = 'Northstar', 'seat_a', '959e2f9e5d'
KEY1 = f"thread/{USER}/{TID}/{hashlib.sha1(R1.encode('utf-8')).hexdigest()[:10]}"


def page_keys():
    page = CC.load_subject_page(CC.subject_key(SUBJ), force=True) or {}
    return [(f.get('source') or {}).get('key') for f in page.get('facts') or []]


def sweep():
    """What the catalog's pass over a thread does: every reply with a figure is banked under its subject."""
    banked = 0
    for t in THREAD:
        if t['role'] != 'user' and CC.record_answer(SUBJ, t['text'], user=USER, thread_id=TID):
            banked += 1
    return banked


check('first pass: both replies are banked', sweep() == 2 and KEY1 in page_keys())
blk = CC.anchors_block(CC.anchors_for(SUBJ, with_ledger=False))
check('and the old figure is in the binding block', '238,417' in blk or '238417' in blk, blk[:200])
removed = CC.retire_answer(USER, TID, R1, subject=SUBJ, by='operator')
check('the reply is set aside: its figures leave the page', removed > 0 and KEY1 not in page_keys(), str(removed))
check('and the other reply stays', len(page_keys()) > 0)
check('the index line follows the page', (CC.load_index(force=True)['subjects'].get(CC.subject_key(SUBJ)) or {}).get('n_facts')
      == len(page_keys()))
check('the catalog remembers it', CC.is_retired_source(KEY1)
      and (json.loads(fake.objs[CC.RETIRED_KEY]).get(KEY1) or {}).get('by') == 'operator')
check('the record holds no figure and no words of the reply',
      '238' not in fake.objs[CC.RETIRED_KEY].decode('utf-8') and 'signups' not in fake.objs[CC.RETIRED_KEY].decode('utf-8'))
check('a second pass over the same thread does not bank it again', sweep() == 1 and KEY1 not in page_keys())
blk = CC.anchors_block(CC.anchors_for(SUBJ, with_ledger=False))
check('so the binding block no longer carries it', '238,417' not in blk and '238417' not in blk and '6,113,257' not in blk
      and ('2,999,979' in blk or '2999979' in blk), blk[:300])
check('a new reply on the subject still banks', bool(CC.record_answer(
    SUBJ, 'Harbor Lights Season 2 drove 40,191 new US Northstar signups from Aug 28 to Oct 30, 2026.', user=USER, thread_id=TID)))
check('setting a reply aside before it was banked keeps it out', CC.retire_answer(USER, 't2', R1, subject=SUBJ) == 0
      and not CC.record_answer(SUBJ, R1, user=USER, thread_id='t2'))
CC.set_client(None)

# ---------------------------------------------------------------------------
print('5. the conversation')
# ---------------------------------------------------------------------------
check('the thread is exactly as it was: no mark, no edit', THREAD == BEFORE)
check('no turn carries a withdrawn or retired mark', not any(
    (t.get('meta') or {}).get(k) for t in THREAD for k in ('withdrawn', 'retired', 'corrected_at')))
for rel in ('scripts/test_pm_set_aside_rows.py',):
    src = (ROOT / rel).read_text(encoding='utf-8')
    check(f'no long dash in {rel}', '\u2014' not in src and '\u2013' not in src)
chat_lines = len(_legacy_view().splitlines())
check(f'the legacy chat module is under its ceiling ({chat_lines} lines)', chat_lines < 19900)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
