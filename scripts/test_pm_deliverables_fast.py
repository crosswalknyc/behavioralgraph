#!/usr/bin/env python3
"""Where a reader's deck or file is, found from the reader's own record
(2026-11-12).

"where is the howdy deck" took three minutes on prod (176 to 184 seconds
in ten replays; the nightly grader's limit is 180). With nothing on file
the reply tells the reader what they asked about the subject and what
each ask came to, and it found that by reading the whole ask log, every
user's rows, one object at a time: 120 listings and 1,961 reads on
2026-11-12, more with every ask anyone makes. The mail log (279 records
in a fortnight) and the job records were read one at a time as well.

Holds:
  1. every ask is written a second time, in short form, under the
     asker's own prefix, and the settle rewrites it; a probe's asks are
     not;
  2. the trail reads the reader's own rows and nobody else's, and says
     exactly what the walk said;
  3. before the rows are in place (no marker) the walk is a bounded
     fortnight, read in parallel;
  4. the records read in parallel come back in the order they were
     read one at a time, and a record that fails to read is skipped;
  5. the whole reply is what it was;
  6. the backfill plans a row for every ask a reader made and none for a
     probe's.

Hermetic: an in-memory store that counts every call. No S3, no model,
no app module.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import os
import re
import sys
import threading
import time
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import render_usage_log as RUL  # noqa: E402
from prometheus import deliverables as DL  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)
B = 'dashboard-inputs'


def _day(n):
    return (NOW - dt.timedelta(days=n)).strftime('%Y-%m-%d')


def _iso(days_ago, hh='10:00:00'):
    return f"{_day(days_ago)}T{hh}Z"


class Store:
    """An S3 stand-in that counts what is listed and what is read."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objs, self.at = {}, {}
        self.gets, self.lists, self.puts = [], [], []
        self._lock = threading.Lock()
        self.fail = set()

    def put(self, key, doc, days_ago=0):
        self.objs[key] = json.dumps(doc).encode('utf-8')
        self.at[key] = NOW - dt.timedelta(days=days_ago)

    def put_object(self, Bucket=None, Key=None, Body=b'', **kw):
        with self._lock:
            self.puts.append(Key)
            self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
            self.at[Key] = NOW
        return {}

    def get_object(self, Bucket=None, Key=None, **kw):
        with self._lock:
            self.gets.append(Key)
        if Key in self.fail or Key not in self.objs:
            raise self.exceptions.NoSuchKey(Key)
        return {'Body': io.BytesIO(self.objs[Key])}

    def list_objects_v2(self, Bucket=None, Prefix='', **kw):
        with self._lock:
            self.lists.append(Prefix)
        return {'Contents': [{'Key': k, 'LastModified': self.at[k]} for k in sorted(self.objs) if k.startswith(Prefix)],
                'IsTruncated': False}

    def get_paginator(self, name):
        store = self

        class _P:
            def paginate(self, **kw):
                yield store.list_objects_v2(**kw)
        return _P()

    def generate_presigned_url(self, method, Params=None, **kw):
        return f"https://{B}.s3.us-east-2.amazonaws.com/{(Params or {}).get('Key')}?signed"

    def reset_counts(self):
        self.gets, self.lists, self.puts = [], [], []


class Host:
    deck_prefix, read_prefix = 'system/prometheus_decks/', 'system/prometheus_reads/'

    def __init__(self, s3):
        self.s3_client, self.bucket = s3, B

    def load_threads_index(self, u):
        return {'threads': [{'id': 't1', 'updated': _iso(1)}, {'id': 't2', 'updated': _iso(3)}]}

    def thread_key(self, u, tid):
        return f'system/synth_chat_threads/{u}/{tid}.json'

    def s3_json(self, key, default):
        try:
            return json.loads(self.s3_client.get_object(Bucket=B, Key=key)['Body'].read())
        except Exception:
            return default


# the asks in the log: (days ago, hh:mm:ss, user, question, subject, route, outcome)
ASKS = [
    (0, '09:00:01', 'scott', 'build a howdy profile', 'Howdy', 'profile_build', 'new_build'),
    (0, '09:05:00', 'liz', 'where is the howdy deck', 'Howdy', 'deliverable_lookup', 'answered'),
    (1, '14:00:00', 'scott', 'stop the howdy build', 'Howdy', 'work_order', 'workorder_cancel'),
    (1, '14:10:00', 'scott', 'who watches howdy', 'Howdy', 'reasoned_metrics', 'landed'),               # no file word: not in a trail
    (1, '15:00:00', 'jessie', 'make a howdy deck', 'Howdy', 'deck', 'answered'),
    (9, '08:00:00', 'scott', 'pull the howdy report for me', 'Howdy', 'reasoned_metrics', 'clarified'),
    (9, '08:30:00', 'scott', 'build a deck on Starz', 'Starz', 'deck', 'answered'),                      # another subject
    (40, '11:00:00', 'scott', 'howdy journey please', 'Howdy', 'journey', 'declined'),
    (100, '12:00:00', 'scott', 'the howdy pdf', 'Howdy', 'page_analysis', 'answered'),
    (130, '12:00:00', 'scott', 'howdy deck from the spring', 'Howdy', 'deck', 'answered'),              # past the season
    (2, '10:00:00', 'canary:golden', 'where is the howdy deck', 'Howdy', 'deliverable_lookup', 'answered'),
    (3, '10:00:00', 'unknown', 'howdy profile', 'Howdy', 'profile_build', 'declined'),
]
for i in range(60):          # other readers' asks: what the walk used to read through
    ASKS.append((i % 20, f"16:{i:02d}:00", f"reader{i % 7}", f"build a profile on show {i}", f"Show {i}", 'profile_build', 'new_build'))


def _log_key(days_ago, hh, n):
    return f"{RUL.ASK_PREFIX}{_day(days_ago)}/{hh.replace(':', '')}_{n:010x}.json"


def _fill_log(store, by_user=True, marker=True):
    for n, (d, hh, user, q, subj, route, outcome) in enumerate(ASKS):
        rec = {'ts': _iso(d, hh), 'user': user, 'view': 'profileIQ', 'question': q, 'surface': 'analyze', 'route': route,
               'outcome': outcome, 'ms': 1200, 'subject': subj, 'reply': 'x' * 300, 'stages': {'model': 1000}}
        key = _log_key(d, hh, n)
        store.put(key, rec, d)
        if by_user and RUL._indexable_user(user):
            store.put(RUL.ask_user_key(user, key), RUL.ask_user_row(rec, key), d)
    if marker:
        store.put(RUL.ASK_BY_USER_READY_KEY, {'ready': True, 'log_rows': len(ASKS)})


def _walk_as_it_was(uname, subject, s3, bucket, since, limit=3):
    """The trail as it was written before 2026-11-12 (prometheus/deliverables.py at 0ea1c972), kept here as the
    reference the new one must agree with."""
    out = []
    u = str(uname or '')
    day = dt.datetime.now(dt.timezone.utc)
    ndays = min(int((time.time() - since) / 86400) + 1, DL.SUBJECT_LOOKBACK_DAYS)
    want = re.compile(r"\b(?:deck|pdf|slides?|journey|build|profile|report|pull)\b", re.I)
    for i in range(ndays):
        d = (day - dt.timedelta(days=i)).strftime('%Y-%m-%d')
        r = s3.list_objects_v2(Bucket=bucket, Prefix=f'system/usage/ask_log/{d}/')
        for o in r.get('Contents') or []:
            k = str(o.get('Key') or '')
            if '/_signals/' in k or not k.endswith('.json'):
                continue
            rec = DL._s3_json(s3, bucket, k) or {}
            if str(rec.get('user') or '') != u:
                continue
            q = str(rec.get('question') or '')
            if not want.search(q) or not DL._matches(subject, q, str(rec.get('subject') or '')):
                continue
            label, ts = DL._when(rec.get('ts'))
            out.append({'when': label, 'ts': ts, 'question': q[:140], 'outcome': str(rec.get('outcome') or ''),
                        'route': str(rec.get('route') or '')})
        if len(out) >= limit * 3:
            break
    out.sort(key=lambda it: -float(it.get('ts') or 0))
    return out[:limit]


class _Base(unittest.TestCase):
    def setUp(self):
        self.s3 = Store()
        DL._READY.update(at=0.0, ok=False)
        self.since = time.time() - DL.SUBJECT_LOOKBACK_DAYS * 86400


# ============================================================================ 1
class EveryAskIsKeptUnderItsAsker(_Base):
    def setUp(self):
        super().setUp()
        self._client, self._sync = RUL._client, RUL._SYNC_FOR_TESTS
        RUL._client = lambda: self.s3
        RUL._SYNC_FOR_TESTS = True

    def tearDown(self):
        RUL._client, RUL._SYNC_FOR_TESTS = self._client, self._sync

    def test_the_record_and_its_reader_keyed_row(self):
        RUL.record_ask(user='scott', view='profileIQ', question='where is the howdy deck', surface='analyze',
                       route='deliverable_lookup', outcome='answered', ms=1840, subject='Howdy', reply='I do not have a Howdy deck',
                       job_id='job123', stages={'model': 900})
        main = [k for k in self.s3.objs if re.match(r"system/usage/ask_log/\d{4}-\d\d-\d\d/", k)]
        mine = [k for k in self.s3.objs if k.startswith(RUL.ASK_BY_USER_PREFIX)]
        self.assertEqual((len(main), len(mine)), (1, 1))
        self.assertEqual(mine[0], RUL.ask_user_key('scott', main[0]))
        self.assertTrue(mine[0].startswith(f"{RUL.ASK_BY_USER_PREFIX}scott/{main[0].split('/')[3]}/"))      # the day is in the key
        row = json.loads(self.s3.objs[mine[0]])
        self.assertEqual((row['user'], row['question'], row['subject'], row['route'], row['outcome'], row['key']),
                         ('scott', 'where is the howdy deck', 'Howdy', 'deliverable_lookup', 'answered', main[0]))
        self.assertNotIn('reply', row)
        self.assertNotIn('stages', row)
        # what the ask came to is rewritten on both
        self.assertTrue(RUL.settle_ask('job123', outcome='landed', reply='On Howdy: 1,284,113 viewers.', ms=64000))
        self.assertEqual(json.loads(self.s3.objs[main[0]])['outcome'], 'landed')
        self.assertEqual(json.loads(self.s3.objs[mine[0]])['outcome'], 'landed')

    def test_a_name_maps_to_its_own_prefix_and_a_probe_has_none(self):
        self.assertEqual(RUL.ask_user_prefix('mark.valdez'), RUL.ASK_BY_USER_PREFIX + 'mark.valdez/')
        self.assertEqual(RUL.ask_user_prefix('a/b c@x.com'), RUL.ASK_BY_USER_PREFIX + 'a%2Fb%20c%40x.com/')
        self.assertNotEqual(RUL.ask_user_prefix('scott'), RUL.ask_user_prefix('scott2')[:len(RUL.ask_user_prefix('scott'))])
        for user in ('canary:golden', 'canary:probe', 'unknown', ''):
            RUL.record_ask(user=user, view='', question='where is the howdy deck', surface='analyze', route='x', outcome='answered', ms=1)
        self.assertEqual([k for k in self.s3.objs if k.startswith(RUL.ASK_BY_USER_PREFIX)], [])
        self.assertEqual(len([k for k in self.s3.objs if k.startswith(RUL.ASK_PREFIX)]), 4)                # the log itself is as it was

    def test_a_failed_row_never_loses_the_record(self):
        real_put = self.s3.put_object

        def put(Bucket=None, Key=None, Body=b'', **kw):
            if str(Key).startswith(RUL.ASK_BY_USER_PREFIX):
                raise RuntimeError('boom')
            return real_put(Bucket=Bucket, Key=Key, Body=Body, **kw)
        self.s3.put_object = put
        RUL.record_ask(user='scott', view='', question='where is the howdy deck', surface='analyze', route='x', outcome='answered', ms=1)
        self.assertEqual(len([k for k in self.s3.objs if k.startswith(RUL.ASK_PREFIX)]), 1)


# ============================================================================ 2, 3
class TheTrailReadsTheReadersOwnRows(_Base):
    def test_it_reads_one_readers_rows_and_says_what_the_walk_said(self):
        _fill_log(self.s3)
        want = _walk_as_it_was('scott', 'howdy', self.s3, B, self.since)
        walked = (len(self.s3.lists), len(self.s3.gets))
        self.assertEqual([t['question'] for t in want],
                         ['build a howdy profile', 'stop the howdy build', 'pull the howdy report for me'])
        self.s3.reset_counts()
        got = DL._ask_trail('scott', 'howdy', self.s3, B, self.since)
        self.assertEqual(got, want)
        # the walk listed every day of the season and read every row in it; the trail lists the reader and reads theirs
        self.assertEqual(walked[0], DL.SUBJECT_LOOKBACK_DAYS)
        self.assertGreaterEqual(walked[1], 70)
        self.assertEqual(self.s3.lists, [RUL.ask_user_prefix('scott')])
        read = [k for k in self.s3.gets if k != RUL.ASK_BY_USER_READY_KEY]
        self.assertEqual(len(read), 7)                                         # scott's rows inside the season, nobody else's
        self.assertTrue(all(k.startswith(RUL.ask_user_prefix('scott')) for k in read))
        self.assertFalse([k for k in read if _day(130) in k])                  # the row past the season is not read

    def test_every_reader_gets_the_same_trail_as_before(self):
        _fill_log(self.s3)
        for user, subject in (('scott', 'howdy'), ('scott', 'Starz'), ('jessie', 'howdy'), ('liz', 'howdy'), ('reader3', 'show 3'),
                              ('reader0', 'show'), ('nobody', 'howdy'), ('scott', 'nothing like it')):
            self.assertEqual(DL._ask_trail(user, subject, self.s3, B, self.since),
                             _walk_as_it_was(user, subject, self.s3, B, self.since), (user, subject))
        self.assertEqual(DL._ask_trail('scott', '', self.s3, B, self.since), [])
        self.assertEqual(DL._ask_trail('', 'howdy', self.s3, B, self.since), [])

    def test_a_probe_with_no_rows_reads_nothing_more(self):
        """The golden case: canary:golden has no rows of its own. It used to read all 1,961."""
        _fill_log(self.s3)
        self.s3.reset_counts()
        self.assertEqual(DL._ask_trail('canary:golden', 'howdy', self.s3, B, self.since), [])
        self.assertEqual(self.s3.lists, [RUL.ask_user_prefix('canary:golden')])
        self.assertEqual(self.s3.gets, [RUL.ASK_BY_USER_READY_KEY])

    def test_before_the_rows_are_in_place_the_walk_is_a_bounded_fortnight(self):
        _fill_log(self.s3, by_user=False, marker=False)
        self.s3.reset_counts()
        got = DL._ask_trail('scott', 'howdy', self.s3, B, self.since)
        self.assertEqual([t['question'] for t in got], ['build a howdy profile', 'stop the howdy build', 'pull the howdy report for me'])
        days_listed = [p for p in self.s3.lists if re.match(r"system/usage/ask_log/\d{4}-", p)]
        self.assertEqual(len(days_listed), DL.LOOKBACK_DAYS + 1)               # never the season
        self.assertFalse([k for k in self.s3.gets if _day(40) in k or _day(100) in k])
        # the marker is looked for once in five minutes, not on every ask
        self.s3.reset_counts()
        DL._ask_trail('scott', 'howdy', self.s3, B, self.since)
        self.assertNotIn(RUL.ASK_BY_USER_READY_KEY, self.s3.gets)

    def test_a_reader_whose_listing_fails_falls_back_to_the_bounded_walk(self):
        _fill_log(self.s3)
        real = self.s3.list_objects_v2

        def lst(Bucket=None, Prefix='', **kw):
            if Prefix.startswith(RUL.ASK_BY_USER_PREFIX):
                raise RuntimeError('boom')
            return real(Bucket=Bucket, Prefix=Prefix, **kw)
        self.s3.list_objects_v2 = lst
        got = DL._ask_trail('scott', 'howdy', self.s3, B, self.since)
        self.assertEqual(got[0]['question'], 'build a howdy profile')


# ============================================================================ 4, 5
class TheReplyIsWhatItWas(_Base):
    def _fill_everything(self):
        _fill_log(self.s3)
        s = self.s3
        s.put('system/prometheus_decks/d1.json', {'user': 'scott', 'status': 'done', 'url': f'https://{B}.s3.us-east-2.amazonaws.com/generated_decks/a/Starz.pptx?old',
                                                 'subject': 'Starz', 'title': 'Starz insights', 'filename': 'Starz_Insights.pptx', 'finished_at': _iso(2)}, 2)
        s.put('system/prometheus_decks/d2.json', {'user': 'liz', 'status': 'done', 'url': 'https://x/y.pptx', 'subject': 'Starz', 'filename': 'Liz.pptx',
                                                 'finished_at': _iso(1)}, 1)
        s.put('system/prometheus_decks/d3.json', {'user': 'scott', 'status': 'working', 'subject': 'Starz'}, 0)
        for i in range(12):
            s.put(f'system/prometheus_reads/r{i:02d}.json', {'user': 'scott' if i % 3 == 0 else 'liz', 'status': 'done', 'subject': 'Starz' if i < 6 else 'Howdy2',
                                                            'question': f'starz question {i}', 'finished_at': _iso(i, '07:00:00'),
                                                            'payload': {'link': {'url': f'https://{B}.s3.us-east-2.amazonaws.com/generated_data/x{i}/Starz {i}.csv?old',
                                                                                 'label': f'Download Starz {i}.csv'}}}, i)
        s.put('system/pm_deliveries/scott/a.json', {'subject': 'Starz', 'kind': 'profile', 'files': ['Starz.csv', 'Starz - Avid Fan.csv'],
                                                    'ts': _iso(5), 'where': 'In Select Profile.'}, 5)
        s.put('system/pm_deliveries/scott/b.json', {'subject': 'Will And Grace', 'kind': 'journey', 'files': ['WG.xlsx'], 'ts': _iso(6)}, 6)
        s.put('system/synth_chat_threads/scott/t1.json', [
            {'role': 'user', 'text': 'chart the starz audience', 'ts': _iso(1)},
            {'role': 'agent', 'text': 'On Starz: ...', 'ts': _iso(1, '10:01:00'),
             'meta': {'chart': {'url': f'https://{B}.s3.us-east-2.amazonaws.com/generated_data/c/Starz chart.png?old', 'alt': 'Starz chart'}}}], 1)
        s.put('system/synth_chat_threads/scott/t2.json', [{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}], 3)
        for i in range(30):
            s.put(f"system/outbound_mail/{_day(i % 10)}/{i:06d}_x.json",
                  {'to': 'scott@example.com' if i % 5 == 0 else 'liz@example.com', 'sent': i != 5, 'subject': f'Starz: your read {i}',
                   'pdf_name': f'Starz_{i}.pdf', 'logged_at': _iso(i % 10, f"06:{i:02d}:00")}, i % 10)
        s.put('system/decks/k1.json', {'owner': 'scott', 'name': 'Starz upfront', 'slides': [1, 2, 3], 'updated_at': _iso(4)}, 4)
        s.put('system/decks/k2.json', {'owner': 'liz', 'name': 'Starz other', 'slides': [1]}, 4)

    def _sequential(self):
        """The module with every parallel read turned back into one-at-a-time reads, and the trail walked the old way."""
        orig_many, orig_trail = DL._many, DL._ask_trail
        DL._many = lambda fn, items, workers=1: [(_safe(fn, x)) for x in list(items)]
        DL._ask_trail = _walk_as_it_was
        return orig_many, orig_trail

    def test_the_whole_reply_is_the_same_for_a_reader_with_files_and_one_with_none(self):
        self._fill_everything()
        host = Host(self.s3)
        user = {'username': 'scott', 'email': 'scott@example.com'}
        asks = ('where is the starz deck', 'where is the pdf', 'where is my deck', 'where is the howdy deck', 'where are the Will And Grace files',
                'can you resend the starz chart', 'where is the nothing-at-all deck')
        new = [DL.answer(q, 'scott', user=user, history=[], host=host) for q in asks]
        orig = self._sequential()
        try:
            old = [DL.answer(q, 'scott', user=user, history=[], host=host) for q in asks]
        finally:
            DL._many, DL._ask_trail = orig
        for q, a, b in zip(asks, old, new):
            self.assertEqual(a, b, q)
        self.assertIn('Starz_Insights.pptx', new[0]['reply'])
        self.assertIn('What you asked me about', new[3]['reply'])
        self.assertIn('"build a howdy profile"', new[3]['reply'])
        self.assertIn('the profile was built', new[3]['reply'])

    def test_records_read_together_keep_their_order_and_a_bad_one_is_skipped(self):
        self.assertEqual(DL._many(lambda x: x * 2, range(40)), [x * 2 for x in range(40)])
        self.assertEqual(DL._many(lambda x: 1 / x, [1, 0, 2]), [1.0, None, 0.5])
        self.assertEqual(DL._many(lambda x: x, []), [])
        self._fill_everything()
        self.s3.fail = {'system/prometheus_reads/r03.json'}
        got = DL._read_items('scott', 'starz', self.s3, B, 'system/prometheus_reads/', self.since)
        self.assertEqual([it['label'] for it in got], ['Starz 0.csv', 'Starz 6.csv', 'Starz 9.csv'])   # r03 could not be read; the rest stand, in order

    def test_the_lookup_reads_a_bounded_number_of_records(self):
        self._fill_everything()
        self.s3.reset_counts()
        DL.answer('where is the howdy deck', 'scott', user={'username': 'scott', 'email': 'scott@example.com'}, history=[], host=Host(self.s3))
        others = [k for k in self.s3.gets if re.match(r"system/usage/ask_log/\d{4}-", k)]
        self.assertEqual(others, [])                                           # never the log itself
        self.assertLess(len(self.s3.gets), 80)


def _safe(fn, x):
    try:
        return fn(x)
    except Exception:
        return None


# ============================================================================ 6
class TheBackfill(_Base):
    def test_it_plans_a_row_for_every_readers_ask_and_none_for_a_probe(self):
        spec = importlib.util.spec_from_file_location('backfill_ask_by_user_t', os.path.join(HERE, 'backfill_ask_by_user.py'))
        bf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bf)
        _fill_log(self.s3, by_user=False, marker=False)
        keys = bf.log_rows(self.s3)
        self.assertEqual(len(keys), len(ASKS))
        plans = [bf.plan_one(self.s3, k) for k in keys]
        states = {}
        for p in plans:
            states[p[3]] = states.get(p[3], 0) + 1
        self.assertEqual(states, {'new': len(ASKS) - 2, 'skipped': 2})          # the probe's and the unknown asker's
        for _k, target, row, st in plans:
            if st == 'new':
                self.s3.put(target, row)
        again = [bf.plan_one(self.s3, k)[3] for k in keys]
        self.assertEqual(again.count('same'), len(ASKS) - 2)                    # a second run writes nothing
        self.assertEqual(self.s3.puts, [])                                      # planning never writes


if __name__ == '__main__':
    unittest.main(verbosity=1)
