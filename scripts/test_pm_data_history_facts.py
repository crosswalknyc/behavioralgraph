#!/usr/bin/env python3
"""How far back the data goes is a product fact, answered in one line
(Jenna 2026-11-23: "this should have answered her you can go back to
jan 1 2021").

A studio user asked "how back can we pull a window" and got the
working-on-it line from a read that had started. The data floor gate
answers "how far back does the data go" ahead of every lane, but her
words (the typo, "can we pull a window") were not a shape it reads, the
Understanding read the ask as a capability question, the product facts
lane had no family for it, and the ask fell through to the read lane,
which promised a job.

Covers, hermetically (no model, no S3 beyond a fake, no mail):
  * the data_history family fires on the exact words, the variants and
    an earlier-year ask, and never on an order, a subject read, a title
    year or "how do I go back to the dashboard";
  * the answer is one plain line that carries the house sentence, the
    date read from migration.event_window (never spelled here), what an
    earlier year means (the window would start on the floor) and that a
    covered year is covered; no internal word, no dash;
  * through the service door (the Understanding stamped as the reader
    read it, and with none on the body) the reply is the fact, the
    route is product_facts, nothing is promised, no job starts;
  * the guard: the read lane's entry returns the fact and never the
    working-on-it line, the priced classifier never quotes one of these
    questions, and the sanitized replay fixtures carry the case;
  * the lane tables list the family as deterministic and never priced.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import event_window as EW  # noqa: E402
from prometheus import data_floor as DF  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import product_facts as PF  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import understanding as U  # noqa: E402
from prometheus.host import host  # noqa: E402

EXACT = 'how back can we pull a window'          # the words as typed, typo included
FLOOR_LABEL = EW.data_floor_label()
SENTENCE = EW.data_floor_sentence()
BANNED = ('synth', 'model', 'panel', 'sample', 'pipeline', 'queue', 'claude', 'hostmap', 'ledger', 'anthropic', 'estimated',
          'working on it', 'in progress', 'email you', '\u2014', '\u2013')

HITS = (
    EXACT, 'How back can we pull a window?', 'how far back can we pull a window', 'How far back can you go?',
    'how far back does the data go', 'how far back can i go', 'how far does the data go', 'how long back does it go',
    'how much history do you have', 'how many years back can we look', 'how many years of data do you have',
    'what is the earliest date I can pull', "what's the earliest window", 'oldest window available?',
    'what years do you have', 'what years are available', 'when does the data start', 'does the data go back to 2020',
    'can we go back to 2018', 'can I pull 2019?', 'can we pull 2019 data', 'could we run a window in 2020',
    'do you have 2020 data', 'is 2022 available?', 'do you have historical data', 'what is your data range',
    'Hi, how far back can we look at a window?', 'can I pull a window from 2019 to 2022', 'how far back does your data go for Netflix',
)
MISSES = (
    'Run a profile for Example Series with a window in 2020-2021', 'pull a profile of Example Talent', 'how is my list going',
    'what can you do?', 'how many years has Example Series been on', 'what year did Example Series come out',
    'can I pull the 2024 Super Bowl audience', 'can I pull 2024 Super Bowl viewers', 'can we pull 2024 data for Netflix',
    'can I pull 1923?', 'can I pull Blade Runner 2049', 'how big is the Example Talent audience',
    'value the Example Talent x Northwind partnership', 'how far is Netflix ahead of Hulu',
    'pull the Netflix profile going as far back as you can', 'how did Netflix do in 2019',
    'what was the audience in 2022 for Example Series', 'build a Nike profile for 2023', 'run it back',
    'how do I go back to the dashboard', 'go back to the previous question',
)


def _clean(tc, text):
    low = text.lower()
    for bad in BANNED:
        tc.assertNotIn(bad, low, (bad, text))


# ------------------------------------------------------------------ the family

class Family(unittest.TestCase):
    def test_fires_on_the_exact_words_and_the_variants(self):
        for t in HITS:
            self.assertEqual(PF.family_of(t), 'data_history', t)
            self.assertTrue(PF.looks_like(t), t)

    def test_never_fires_on_work_or_other_questions(self):
        for t in MISSES:
            self.assertNotEqual(PF.family_of(t), 'data_history', t)

    def test_in_the_family_list_and_the_kill_switch(self):
        self.assertIn('data_history', PF.FAMILIES)
        os.environ['PM_PRODUCT_FACTS'] = '0'
        try:
            self.assertIsNone(PF.family_of(EXACT))
        finally:
            os.environ.pop('PM_PRODUCT_FACTS', None)
        self.assertEqual(PF.family_of(EXACT), 'data_history')


# ------------------------------------------------------------------ the line

class Answer(unittest.TestCase):
    def test_one_plain_line_from_the_one_date(self):
        out = PF.answer(EXACT)
        self.assertEqual(out['family'], 'data_history')
        reply = out['reply']
        _clean(self, reply)
        self.assertEqual(reply, f"You can pull any window from {FLOOR_LABEL} to today. {SENTENCE}")
        self.assertIn('January 1, 2021', reply)          # the product fact as Jenna stated it
        self.assertEqual(out['followups'], [])
        self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", reply)), 3)

    def test_the_date_is_read_not_spelled(self):
        src = open(os.path.join(ROOT, 'prometheus', 'product_facts.py'), encoding='utf-8').read()
        self.assertNotIn('2021', re.sub(r'""".*?"""|#[^\n]*', '', src, flags=re.S), 'the floor date must come from migration.event_window')
        self.assertEqual(DF.FLOOR, EW.DATA_FLOOR)
        self.assertEqual(DF.FLOOR_LABEL, FLOOR_LABEL)

    def test_an_earlier_year_is_told_where_the_window_would_start(self):
        for t, y in (('can I pull 2019?', 2019), ('can we pull 2019 data', 2019), ('do you have 2020 data', 2020),
                     ('can we go back to 2018', 2018), ('can I pull a window from 2019 to 2022', 2019)):
            reply = PF.answer(t)['reply']
            _clean(self, reply)
            self.assertTrue(reply.startswith(f"You can pull any window from {FLOOR_LABEL} to today. {SENTENCE}"), reply)
            self.assertTrue(reply.endswith(f"A window from {y} would start {FLOOR_LABEL}."), reply)

    def test_a_covered_year_is_covered(self):
        reply = PF.answer('is 2022 available?')['reply']
        self.assertTrue(reply.endswith('2022 is covered.'), reply)
        self.assertNotIn('would start', reply)
        plain = PF.answer('what years do you have')['reply']
        self.assertNotIn('covered', plain)
        self.assertNotIn('would start', plain)

    def test_a_year_that_describes_the_subject_is_not_a_window(self):
        reply = PF.answer('how far back does the data go? the show came out in 2019')['reply']
        self.assertNotIn('2019', reply)


# ------------------------------------------------------------------ the door

class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _S3Error('NoSuchKey', 404)
        return {'Body': io.BytesIO(self.objs[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix='', MaxKeys=1000, ContinuationToken=None):
        keys = sorted(k for k in self.objs if k.startswith(Prefix))
        return {'Contents': [{'Key': k, 'ETag': '"x"'} for k in keys], 'IsTruncated': False}


USER = 'studio_user'
THREADS = 'system/synth_chat_threads'
IDX = {'active': 'thr1', 'threads': [{'id': 'thr1', 'title': 'New chat', 'created': 'x', 'updated': 'x', 'turns': 0}]}


def _bind(s3):
    def s3_json(key, default=None):
        try:
            return json.loads(s3.get_object(Bucket='b', Key=key)['Body'].read().decode('utf-8'))
        except Exception:
            return default

    def s3_put_json(key, obj):
        s3.put_object(Bucket='b', Key=key, Body=json.dumps(obj).encode('utf-8'))

    hints = []
    host.bind(s3_client=s3, bucket='b', s3_json=s3_json, s3_put_json=s3_put_json,
              thread_key=lambda u, tid: f"{THREADS}/{u}/{tid}.json",
              threads_index_key=lambda u: f"{THREADS}/{u}/index.json",
              load_threads_index=lambda u: IDX, thread_title_from=lambda h: 'New chat',
              ask_hint=lambda **kw: hints.append(kw), max_threads=40, read_prefix='system/prometheus_reads/')
    return hints


def _user():
    return {'username': USER, 'role': 'user', 'company': 'Example Studio'}


class Door(unittest.TestCase):
    def setUp(self):
        self.hints = _bind(FakeS3())
        self._persist = S._persist_turn
        S._persist_turn = lambda *a, **k: None

    def tearDown(self):
        S._persist_turn = self._persist

    def _ask(self, text, reading=None, **extra):
        body = dict({'text': text, 'client': 'dashboard', 'surface': 'analyze', 'thread_id': 'thr1', 'history': []}, **extra)
        if reading is not None:
            body = U.attach(body, U.Understanding(intent=reading['intent'], confidence=reading['confidence'],
                                                  source=reading.get('source', 'reader'), action='answer',
                                                  evidence=list(reading.get('evidence') or []), text_key=U.text_key(text)))
        return S.ask(_user(), body)

    def _is_the_fact(self, env, status, gate_ok=False):
        """The reply is the fact and nothing was started. ``gate_ok``: the
        data floor gate, which runs ahead of every lane, may have answered
        (it reads "how far back" and "earliest date" itself)."""
        self.assertEqual(status, 200)
        reason = (env.get('decision') or {}).get('reason')
        raw = env['raw']
        self.assertEqual(raw.get('action'), 'answer')
        _clean(self, raw['reply'])
        self.assertIn(SENTENCE, raw['reply'])
        self.assertNotIn('read_job_id', raw)
        self.assertFalse(raw.get('held'))
        routes = [(h.get('route'), h.get('outcome')) for h in self.hints]
        if gate_ok and reason == 'data_floor':
            self.assertIn(('data_floor', 'capability'), routes)
        else:
            self.assertEqual(reason, 'product_facts', env.get('decision'))
            self.assertTrue(raw['reply'].startswith(f"You can pull any window from {FLOOR_LABEL} to today. {SENTENCE}"), raw['reply'])
            self.assertIn(('product_facts', 'answered'), routes)
        self.assertNotIn('reasoned_metrics', [r for r, _ in routes])

    def test_the_exact_words_as_the_reader_read_them(self):
        # the Understanding as it stood on the live ask: capability, 0.78, the reader, no subject
        env, status = self._ask(EXACT, reading={'intent': 'capability', 'confidence': 0.78, 'evidence': ['build_verb', 'reader:capability@0.78']})
        self._is_the_fact(env, status)

    def test_the_exact_words_with_no_understanding(self):
        env, status = self._ask(EXACT)
        self._is_the_fact(env, status)

    def test_the_exact_words_with_a_page_open(self):
        env, status = self._ask(EXACT, context={'primary': {'name': 'Example Brand', 'category': 'BRAND'}})
        self._is_the_fact(env, status)

    def test_variants_and_an_earlier_year_through_the_door(self):
        for t in ('what years do you have?', 'how much history do you have', 'how many years back can we look', 'is 2022 available?'):
            env, status = self._ask(t)
            self._is_the_fact(env, status)
        for t in ('what is the earliest date I can pull', 'how far back can you go?'):
            env, status = self._ask(t)
            self._is_the_fact(env, status, gate_ok=True)
        env, status = self._ask('can I pull 2019?')
        self._is_the_fact(env, status)
        self.assertTrue(env['raw']['reply'].endswith(f"A window from 2019 would start {FLOOR_LABEL}."), env['raw']['reply'])

    def test_the_gate_still_answers_how_far_back_ahead_of_the_lanes(self):
        env, status = self._ask('how far back does the data go?')
        self.assertEqual(status, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'data_floor')
        self.assertIn(SENTENCE, env['raw']['reply'])


# ------------------------------------------------------------------ the guard

class Guard(unittest.TestCase):
    def test_read_guard_returns_the_fact_and_nothing_else(self):
        out = PF.read_guard(EXACT)
        self.assertEqual(out['action'], 'answer')
        self.assertEqual(out['product_facts'], 'data_history')
        self.assertTrue(out['reply'].startswith(f"You can pull any window from {FLOOR_LABEL} to today."))
        self.assertNotIn('read_job_id', out)
        for t in ('how big is the Example Talent audience', 'pull a profile of Example Talent', 'Run a profile for Example Series with a window in 2020-2021'):
            self.assertIsNone(PF.read_guard(t), t)

    def test_the_read_lane_runs_the_guard_before_any_work(self):
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        src = __import__('_pm_legacy_view').legacy_view()     # the legacy chat text, the read core included, wherever it lives now
        fn = src[src.index('def _pm_generate_metrics_response('):]
        guard = fn.index('read_guard(')
        self.assertLess(guard, fn.index("_pm_ask_hint(route='reasoned_metrics')"), 'the guard sits before the read is even hinted')
        self.assertLess(guard, fn.index('_on_it'), 'the guard sits before the working-on-it promise')
        self.assertLess(guard, fn.index('_pm_generate_read_core('), 'the guard sits before a read is generated')
        self.assertIn("route='product_facts'", fn[:fn.index("_pm_ask_hint(route='reasoned_metrics')")])

    def test_a_product_question_is_never_priced(self):
        for t in (EXACT, 'what is the earliest date I can pull', 'can I pull 2019?', 'what can you do?'):
            self.assertIsNone(PR.classify(t), t)
        self.assertIsNone(PR.work_gate(EXACT, [], None, _user(), {'text': EXACT}, username=USER))

    def test_lane_tables(self):
        self.assertIn('product_facts', S.DETERMINISTIC_LANES)
        self.assertIn('product_facts', S.NEVER_PRICED_LANES)

    def test_the_sanitized_fixtures_carry_the_case(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        cases = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip() and not l.startswith('#')]
        qs = {c['question'].lower(): c for c in cases}
        self.assertIn(EXACT, qs)
        self.assertEqual(qs[EXACT]['recorded'], {'route': 'product_facts', 'outcome': 'answered'})
        self.assertTrue(any('2019' in q for q in qs), 'an earlier-year ask rides the fixtures')
        for c in cases:
            self.assertNotIn('ameseldzhyan', json.dumps(c).lower())
            self.assertNotIn('lionsgate', json.dumps(c).lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
