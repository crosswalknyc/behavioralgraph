#!/usr/bin/env python3
"""Bulk Profile IQ pulls from an uploaded list (Jenna 2026-11-05).
Hermetic: a fake S3, a fake queue, no model, no network.

Covers: reading the list (header variants, no header, TXT, duplicates,
blanks, links and numbers dropped, the optional category / notes /
avid columns), the deterministic detection (and what never fires), the
library check (a base profile of the same name is reused, a cut or a
near miss is not), the spec body one row becomes (through the chat's
converter shape, passing the listener's own validator), the feeder (the
in-flight cap across lists, the engine backlog, backoff on 429 / 5xx,
the per-subject lock, reconcile to done / failed with the refund, a
restart that never re-posts), the digest (every N, the hour, the final
one once), the status reply, the partner status, the confirm copy, and
the Prometheus door end to end.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.pop('SYNTH_QUEUE_URL', None)
os.environ.pop('SYNTH_QUEUE_SECRET', None)
os.environ.pop('BULK_IN_FLIGHT', None)
os.environ.pop('SYNTH_WORKER_COUNT', None)

from botocore.exceptions import ClientError  # noqa: E402

from prometheus import bulk_pull as BP  # noqa: E402
from prometheus import bulk_feeder as BF  # noqa: E402

BANNED = ('synth', 'hostmap', 'pipeline', 'claude', 'anthropic', 'modeled', 'panel', 'queue worker', 'hetzner', 'render', '\u2014', '\u2013')
WINDOW = {'start': '2025-11-05', 'end': '2026-11-05', 'label': 'Trailing 12 months'}


# --------------------------------------------------- the listener's check
def _live_validate_spec():
    for cand in (os.environ.get('FINISHED_CODES_ROOT') or '', os.path.join(ROOT, '..'), '/tmp/fc-main', '/root/finished_codes'):
        path = os.path.join(cand, 'migration', 'synth_queue_listener.py') if cand else ''
        if path and os.path.exists(path):
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
                fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_validate_spec')
                ns = {}
                exec(compile(ast.Module(body=[fn], type_ignores=[]), path, 'exec'), ns)
                return ns['_validate_spec'], path
            except Exception:
                continue
    return None, ''


validate_spec, VALIDATOR_SOURCE = _live_validate_spec()


# ------------------------------------------------------------- fixtures
class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    """Enough of S3 for the ledger: ETags, NoSuchKey, conditional puts."""

    def __init__(self):
        self.objs = {}
        self.puts = 0

    def _etag(self, key):
        import hashlib
        return '"' + hashlib.md5(self.objs[key]).hexdigest() + '"'

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'x'}}, 'GetObject')
        return {'Body': _Body(self.objs[Key]), 'ETag': self._etag(Key)}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise ClientError({'Error': {'Code': '404', 'Message': 'x'}}, 'HeadObject')
        return {'ETag': self._etag(Key)}

    def put_object(self, Bucket, Key, Body, **kw):
        if 'IfMatch' in kw and Key in self.objs and kw['IfMatch'] != self._etag(Key).strip('"'):
            raise ClientError({'Error': {'Code': 'PreconditionFailed', 'Message': 'x'}, 'ResponseMetadata': {'HTTPStatusCode': 412}}, 'PutObject')
        if 'IfNoneMatch' in kw and Key in self.objs:
            raise ClientError({'Error': {'Code': 'PreconditionFailed', 'Message': 'x'}, 'ResponseMetadata': {'HTTPStatusCode': 412}}, 'PutObject')
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.puts += 1

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in sorted(self.objs) if k.startswith(Prefix)], 'IsTruncated': False}


CATALOG = [
    {'s3_key': 'Taylor_Swift_09_01_2026_10_00.csv', 'display_name': 'Taylor Swift', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Taylor_Swift_06_01_2026_10_00.csv', 'display_name': 'Taylor Swift', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Taylor Swift - Avid Fan.csv', 'display_name': 'Taylor Swift - Avid Fan', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Reba_McEntire_05_13_2026_10_00.csv', 'display_name': 'Reba McEntire', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Reba McEntire - 2025 Total Universe.csv', 'display_name': 'Reba McEntire - 2025 Total Universe', 'category': 'MUSICIAN/BAND'},
    {'s3_key': 'Gen_Pop_2026.csv', 'display_name': 'Gen Pop 2026', 'category': 'TRENDS'},
    {'s3_key': 'system/s3_cache.json', 'display_name': 'x', 'category': ''},
]

CSV_HEADER = ("Name,Category,Notes,Avid\n"
              "Taylor Swift,MUSICIAN/BAND,,yes\n"
              "Dolly Parton,MUSICIAN/BAND,country icon,no\n"
              "dolly parton,,,\n"
              ",,,\n"
              "https://instagram.com/someone,,,\n"
              "12345,,,\n"
              "Pedro Pascal,ACTOR,,\n")
CSV_NO_HEADER = "Taylor Swift\nMrBeast\nMrBeast\nPedro Pascal\n"
TXT = "Taylor Swift\nMrBeast\n\nPedro Pascal\n"

DEMOS = {'GENDER': {'FEMALE': 61.2341, 'MALE': 37.8812, 'NON-BINARY': 0.8847},
         'AGE': {'18-24': 19.3312, '25-34': 24.1187, '35-44': 19.8812, '45-54': 14.2201, '55-64': 11.9027, '65 OR OLDER': 10.5461}}


def _draft(subject, decision='new_build', key=''):
    d = {'subject': subject, 'brand_category': 'ACTOR', 'audience_type': 'general', 'decision': decision, 'run_avid': True,
         'subject_rows': [['ACTOR', subject], ['TALENT', subject]], 'extra_rows': [], 'tu_demos': DEMOS, 'avid_demos': DEMOS,
         'subject_raw_tu': 48_317, 'subject_raw_avid': 10_631, 'persona_notes': f"{subject}: a person.",
         'date_range': {'start': WINDOW['start'], 'end': WINDOW['end']}}
    if key:
        d['existing_match_s3_key'] = key
    return d


def _fake_spec_from_draft(d):
    return {'name': d.get('subject'), 'file_stem': str(d.get('subject') or '').replace(' ', '_'), 'brand_category': d.get('brand_category'),
            'subject_raw_tu': 48_317, 'subject_raw_avid': 10_631, 'subject_rows': [tuple(r) for r in d.get('subject_rows') or []],
            'extra_rows': list(d.get('extra_rows') or []), 'tu_demos': d.get('tu_demos') or {}, 'avid_demos': d.get('avid_demos') or d.get('tu_demos') or {},
            'persona_notes': d.get('persona_notes') or '', 'date_range': d.get('date_range'), 'addon_cuts': d.get('addon_cuts') or []}


def _ledger(s3, names, *, uname='alex', email='alex@example.com', catalog=None, charged=True):
    read = BP.subjects_from_text('\n'.join(names))
    idx = BP.library_index(catalog if catalog is not None else CATALOG)
    user = {'username': uname, 'email': email, 'role': 'user'}
    calls = []

    def _consume(u, **kw):
        calls.append((u, kw))
        return charged
    ledger, line, err = BP.start(user, uname, read, WINDOW, index=idx, thread_id='t1', file_name='talent.csv', ask_text='pull profiles for everyone on this list',
                                 s3=s3, bucket='b', consume_credit=_consume, is_free=lambda u, n: False, usd_each=300.0)
    return ledger, line, err, calls


class FakeEngine:
    """The queue and the status documents."""

    def __init__(self, *, fail_codes=None, lock_subjects=None, pending=0):
        self.posts = []
        self.status = {}
        self.fail_codes = list(fail_codes or [])
        self.lock_subjects = dict(lock_subjects or {})
        self.pending = pending

    def post(self, body):
        if self.fail_codes:
            code = self.fail_codes.pop(0)
            return code, {'error': f'queue returned {code}'}
        subj = body['spec']['name']
        if subj in self.lock_subjects:
            return 409, {'error': 'a build for this subject is already in progress', 'existing_run_id': self.lock_subjects[subj]}
        self.posts.append(body)
        self.status[body['run_id']] = {'run_id': body['run_id'], 'status': 'queued', 'subject': subj}
        return 200, {'run_id': body['run_id'], 'status': 'queued'}

    def status_fn(self, rid):
        return dict(self.status.get(rid) or {})

    def health(self):
        return {'pending': self.pending, 'running': len([s for s in self.status.values() if s.get('status') in ('queued', 'running')])}

    def complete(self, rid, tu='', avid=''):
        self.status[rid] = {'run_id': rid, 'status': 'complete', 'tu_key': tu or f"{rid}_TU.csv", 'avid_key': avid or f"{rid} - Avid Fan.csv",
                            'tu_registered_in_dashboard': True}

    def fail(self, rid):
        self.status[rid] = {'run_id': rid, 'status': 'failed', 'error': 'x'}


def _tick(s3, eng, **kw):
    kw.setdefault('post', eng.post)
    kw.setdefault('status_fn', eng.status_fn)
    kw.setdefault('health_fn', eng.health)
    kw.setdefault('draft_fn', lambda prompt, cands: _draft(re.search(r"run a profile on (.+)", prompt).group(1).strip()))
    kw.setdefault('spec_from_draft', _fake_spec_from_draft)
    kw.setdefault('catalog', CATALOG)
    kw.setdefault('send_fn', lambda to, subj, body: {'sent': True})
    kw.setdefault('refund_fn', lambda u, c, rid, reason, usd: True)
    kw.setdefault('log', lambda m: None)
    return BF.tick(s3, 'b', **kw)


# ---------------------------------------------------------------- tests
class ReadTheList(unittest.TestCase):
    def test_csv_with_header_and_optional_columns(self):
        read = BP.subjects_from_bytes('talent.csv', CSV_HEADER.encode())
        names = [i['subject'] for i in read['items']]
        self.assertEqual(names, ['Taylor Swift', 'Dolly Parton', 'Pedro Pascal'])
        self.assertEqual(read['duplicates'], 1)          # dolly parton
        self.assertEqual(read['dropped'], 3)             # blank, a link, a number
        dolly = read['items'][1]
        self.assertEqual(dolly['category_hint'], 'MUSICIAN/BAND')
        self.assertEqual(dolly['notes'], 'country icon')
        self.assertFalse(dolly['run_avid'])
        self.assertTrue(read['items'][0]['run_avid'])
        self.assertTrue(read['items'][2]['run_avid'])    # blank avid cell = yes
        self.assertEqual(read['columns']['subject'], 'Name')

    def test_csv_without_header_and_txt(self):
        read = BP.subjects_from_bytes('list.csv', CSV_NO_HEADER.encode())
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'MrBeast', 'Pedro Pascal'])
        self.assertEqual(read['duplicates'], 1)
        read = BP.subjects_from_bytes('list.txt', TXT.encode())
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'MrBeast', 'Pedro Pascal'])
        self.assertEqual(read['dropped'], 0)             # the blank line is not a row

    def test_names_column_by_header_among_others(self):
        csv_text = "id,subject,followers\n1,Taylor Swift,280000000\n2,MrBeast,330000000\n"
        read = BP.subjects_from_bytes('x.csv', csv_text.encode())
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'MrBeast'])

    def test_first_text_column_when_no_known_header(self):
        csv_text = "who we want,why\nTaylor Swift,tour\nMrBeast,youtube\nPedro Pascal,hbo\n"
        read = BP.subjects_from_bytes('x.csv', csv_text.encode())
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'MrBeast', 'Pedro Pascal'])

    def test_xlsx(self):
        try:
            import openpyxl
        except Exception:
            self.skipTest('openpyxl not installed')
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Talent', 'Notes'])
        ws.append(['Taylor Swift', ''])
        ws.append(['MrBeast', 'creator'])
        ws.append([None, None])
        buf = io.BytesIO()
        wb.save(buf)
        read = BP.subjects_from_bytes('list.xlsx', buf.getvalue())
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'MrBeast'])
        self.assertEqual(read['items'][1]['notes'], 'creator')

    def test_cap(self):
        read = BP.subjects_from_text('\n'.join(f"Person {i}" for i in range(BP.MAX_SUBJECTS + 50)))
        self.assertEqual(len(read['items']), BP.MAX_SUBJECTS)


class Detection(unittest.TestCase):
    def test_fires(self):
        for t in ('pull profiles for everyone on this list', 'bulk pull these', 'run profile iq on this list', 'Can you build profiles for all of them?',
                  'queue a profile for each name in the attached csv', 'please pull a profile on everyone in the file', 'bulk profiles please'):
            self.assertTrue(BP.is_bulk_ask(t, True), t)

    def test_never_fires(self):
        for t in ('pull a profile of Taylor Swift', 'run individual profiles for Taylor Swift, MrBeast, Pedro Pascal', 'build a twin from these URLs',
                  'what is on this list?', 'how big is the Taylor Swift audience', 'make a digital twin of the people who watched these'):
            self.assertFalse(BP.is_bulk_ask(t, True), t)
        self.assertFalse(BP.is_bulk_ask('pull profiles for everyone on this list', False))

    def test_status_ask(self):
        for t in ('how is my bulk pull going', 'status of the list', "how's the list going?", 'bulk status', 'where is my list', 'how far along is my list'):
            self.assertTrue(BP.is_status_ask(t), t)
        for t in ('status', 'how is Taylor Swift doing', 'pull profiles for everyone on this list'):
            self.assertFalse(BP.is_status_ask(t), t)


class Library(unittest.TestCase):
    def test_index_and_match(self):
        idx = BP.library_index(CATALOG)
        self.assertIn('taylorswift', idx)
        self.assertEqual(idx['taylorswift'][0], 'Taylor_Swift_09_01_2026_10_00.csv')   # the newest primary wins
        self.assertNotIn('taylorswiftavidfan', idx)
        self.assertNotIn('rebamcentire2025totaluniverse', idx)
        self.assertEqual(BP.library_match('taylor swift', idx)[0], 'Taylor_Swift_09_01_2026_10_00.csv')
        self.assertEqual(BP.library_match('TAYLOR-SWIFT', idx)[0], 'Taylor_Swift_09_01_2026_10_00.csv')
        self.assertIsNone(BP.library_match('Taylor Swift Fans', idx))
        self.assertIsNone(BP.library_match('Swift', idx))

    def test_plan_rows(self):
        rows = BP.plan_rows([{'subject': 'Taylor Swift'}, {'subject': 'MrBeast'}], BP.library_index(CATALOG))
        self.assertEqual(rows[0]['state'], 'reused')
        self.assertFalse(rows[0]['charged'])
        self.assertEqual(rows[1]['state'], 'queued')
        self.assertTrue(rows[1]['charged'])
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{4,64}", rows[1]['run_id']))


class SpecBody(unittest.TestCase):
    def test_body_passes_the_listener(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Pedro Pascal'])
        row = ledger['rows'][0]
        body = BF.row_body(ledger, row, _draft('Pedro Pascal'), spec_from_draft=_fake_spec_from_draft)
        self.assertEqual(body['run_id'], row['run_id'])
        self.assertEqual(body['decision'], 'new_build')
        self.assertEqual(body['source'], 'bulk_pull')
        self.assertEqual(body['bulk']['bulk_id'], ledger['bulk_id'])
        self.assertEqual(body['bulk']['row'], 0)
        self.assertEqual(body['bulk']['credits'], 5)
        self.assertEqual(body['bulk']['usd'], 300.0)
        spec = body['spec']
        self.assertEqual(spec['origin'], 'bulk')
        self.assertEqual(spec['name'], 'Pedro Pascal')
        self.assertEqual(spec['brand_category'], 'ACTOR')
        self.assertEqual(spec['date_range'], f"{WINDOW['start']} TO {WINDOW['end']}")
        self.assertEqual(spec['addon_cuts'], [])
        self.assertNotEqual(spec['subject_raw_tu'] % 10, 0)
        self.assertLessEqual(spec['subject_raw_avid'], spec['subject_raw_tu'])
        if validate_spec is not None:
            self.assertIsNone(validate_spec(spec, decision='new_build', body=body), VALIDATOR_SOURCE)

    def test_hygiene(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Pedro Pascal'])
        row = ledger['rows'][0]
        d = _draft('pedro pascal')
        d['decision'] = 'derive_cut'
        d['addon_cuts'] = [{'cut_id': 'x'}]
        d['brand_category'] = 'NOT A CATEGORY'
        body = BF.row_body(ledger, row, d, spec_from_draft=_fake_spec_from_draft)
        self.assertEqual(body['spec']['name'], 'pedro pascal')            # a casing variant of the row's name stands
        self.assertEqual(body['spec']['addon_cuts'], [])
        self.assertEqual(body['spec']['brand_category'], 'MEDIA')
        row['category_hint'] = 'ACTOR'
        body = BF.row_body(ledger, row, dict(d), spec_from_draft=_fake_spec_from_draft)
        self.assertEqual(body['spec']['brand_category'], 'ACTOR')
        d2 = _draft('Someone Else Entirely')
        body = BF.row_body(ledger, row, d2, spec_from_draft=_fake_spec_from_draft)
        self.assertEqual(body['spec']['name'], 'Pedro Pascal')
        d3 = _draft('Pedro Pascal')
        d3['tu_demos'] = {}
        self.assertIsNone(BF.row_body(ledger, row, d3, spec_from_draft=_fake_spec_from_draft))

    def test_reuse_from_draft(self):
        self.assertEqual(BF.reuse_key_from_draft(_draft('Reba McEntire', 'existing_match', 'Reba_McEntire_05_13_2026_10_00.csv'), 'Reba McEntire'),
                         'Reba_McEntire_05_13_2026_10_00.csv')
        self.assertEqual(BF.reuse_key_from_draft(_draft('Reba McEntire', 'time_shifted_refresh', 'Reba_McEntire_05_13_2026_10_00.csv'), 'Reba McEntire'),
                         'Reba_McEntire_05_13_2026_10_00.csv')
        self.assertEqual(BF.reuse_key_from_draft(_draft('Reba McEntire', 'new_build'), 'Reba McEntire'), '')
        self.assertEqual(BF.reuse_key_from_draft(_draft('Reba Fans', 'derive_cut', 'Reba McEntire - Avid Fan.csv'), 'Reba Fans'), '')


class Feeder(unittest.TestCase):
    def test_cap_backlog_and_restart(self):
        s3 = FakeS3()
        names = [f"Person {i}" for i in range(7)]
        ledger, _l, _e, _c = _ledger(s3, names)
        eng = FakeEngine()
        out = _tick(s3, eng, cap=3, max_posts=10)
        self.assertEqual(out['posted'], 3)
        self.assertEqual(len(eng.posts), 3)
        self.assertTrue(all(b['spec']['origin'] == 'bulk' for b in eng.posts))
        # a second tick with nothing finished posts nothing more (the cap)
        out = _tick(s3, eng, cap=3, max_posts=10)
        self.assertEqual(out['posted'], 0)
        # the engine has a backlog: nothing is posted even with room
        eng.complete(eng.posts[0]['run_id'])
        eng.pending = 2
        out = _tick(s3, eng, cap=3, max_posts=10)
        self.assertEqual(out['posted'], 0)
        self.assertEqual(out['skipped'], 'engine backlog')
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(BP.counts(led)['done'], 1)
        # the backlog clears: one slot opens, one row posts; a restart (fresh tick, fresh read) never re-posts
        eng.pending = 0
        out = _tick(s3, eng, cap=3, max_posts=10)
        self.assertEqual(out['posted'], 1)
        self.assertEqual(len(eng.posts), 4)
        ids = [b['run_id'] for b in eng.posts]
        self.assertEqual(len(ids), len(set(ids)))
        _tick(s3, eng, cap=3, max_posts=10)
        self.assertEqual(len(eng.posts), 4)
        # per tick posting is also capped
        for b in eng.posts:
            eng.complete(b['run_id'])
        out = _tick(s3, eng, cap=10, max_posts=2)
        self.assertEqual(out['posted'], 2)

    def test_backoff_on_queue_trouble(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A', 'Person B', 'Person C'])
        eng = FakeEngine(fail_codes=[429])
        now = time.time()
        out = _tick(s3, eng, cap=5, now=now)
        self.assertEqual(out['posted'], 0)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertGreater(float(led['feeder']['backoff_until']), now)
        self.assertEqual(BP.counts(led)['queued'], 3)
        # still backing off: nothing posts
        out = _tick(s3, eng, cap=5, now=now + 10)
        self.assertEqual(out['posted'], 0)
        # past the backoff: posting resumes and the backoff clears
        out = _tick(s3, eng, cap=5, now=now + 120)
        self.assertEqual(out['posted'], 3)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(float(led['feeder']['backoff_until']), 0)
        # a 5xx backs off longer the second time
        ledger2, _l, _e, _c = _ledger(s3, ['Person D', 'Person E'])
        eng2 = FakeEngine(fail_codes=[503, 503])
        _tick(s3, eng2, cap=5, now=now)
        led2 = BP.load_ledger(ledger2['bulk_id'], s3=s3, bucket='b')
        first = float(led2['feeder']['backoff_until']) - now
        _tick(s3, eng2, cap=5, now=now + first + 1)
        led2 = BP.load_ledger(ledger2['bulk_id'], s3=s3, bucket='b')
        second = float(led2['feeder']['backoff_until']) - (now + first + 1)
        self.assertGreater(second, first)

    def test_subject_lock_then_reuse(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A'])
        eng = FakeEngine(lock_subjects={'Person A': 'otherRun123'})
        refunds = []
        out = _tick(s3, eng, cap=5, refund_fn=lambda u, c, rid, reason, usd: refunds.append((u, c, rid)) or True)
        self.assertEqual(out['posted'], 0)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(led['rows'][0]['state'], 'waiting')
        self.assertEqual(led['rows'][0]['existing_run_id'], 'otherRun123')
        eng.complete('otherRun123', tu='Person_A_11_05_2026_10_00.csv')
        _tick(s3, eng, cap=5, refund_fn=lambda u, c, rid, reason, usd: refunds.append((u, c, rid)) or True)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(led['rows'][0]['state'], 'reused')
        self.assertEqual(led['rows'][0]['key'], 'Person_A_11_05_2026_10_00.csv')
        self.assertTrue(led['rows'][0]['refunded'])
        self.assertEqual(refunds, [('alex', 5, led['rows'][0]['run_id'])])
        self.assertEqual(led['status'], 'done')

    def test_failed_row_refunds_once(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A', 'Person B'])
        eng = FakeEngine()
        refunds = []
        rf = lambda u, c, rid, reason, usd: refunds.append(rid) or True  # noqa: E731
        _tick(s3, eng, cap=5, refund_fn=rf)
        eng.fail(eng.posts[0]['run_id'])
        eng.complete(eng.posts[1]['run_id'])
        _tick(s3, eng, cap=5, refund_fn=rf)
        _tick(s3, eng, cap=5, refund_fn=rf)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        states = sorted(r['state'] for r in led['rows'])
        self.assertEqual(states, ['done', 'failed'])
        self.assertEqual(refunds, [eng.posts[0]['run_id']])
        self.assertEqual(led['status'], 'done')
        self.assertEqual([r for r in led['rows'] if r['state'] == 'failed'][0]['error'], BP.FAIL_LINE)

    def test_interpret_failure_retries_then_fails(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A'])
        eng = FakeEngine()
        refunds = []
        now = time.time()
        for k in range(BF.MAX_ATTEMPTS):
            led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
            t = max(now, float(led['feeder'].get('backoff_until') or 0)) + 1
            _tick(s3, eng, cap=5, now=t, draft_fn=lambda p, c: None, refund_fn=lambda u, c, rid, reason, usd: refunds.append(rid) or True)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(led['rows'][0]['state'], 'failed')
        self.assertEqual(led['rows'][0]['attempts'], BF.MAX_ATTEMPTS)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(eng.posts, [])

    def test_existing_match_at_interpret_is_reused_and_refunded(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Reba Mcentire Jr'])
        eng = FakeEngine()
        refunds = []
        _tick(s3, eng, cap=5, draft_fn=lambda p, c: _draft('Reba Mcentire Jr', 'existing_match', 'Reba_Mcentire_Jr_05_13_2026_10_00.csv'),
              refund_fn=lambda u, c, rid, reason, usd: refunds.append(rid) or True)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(led['rows'][0]['state'], 'reused')
        self.assertEqual(len(refunds), 1)
        self.assertEqual(eng.posts, [])

    def test_worker_terminal_state_wins_over_feeder_view(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A'])
        eng = FakeEngine()
        _tick(s3, eng, cap=5)
        # the engine's worker marks the row done on the ledger between two feeder reads
        BP.update_ledger(ledger['bulk_id'], lambda d: (d['rows'][0].update({'state': 'done', 'key': 'Person_A_TU.csv', 'avid_key': ''}) or d), s3=s3, bucket='b')
        stale = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        stale['rows'][0]['state'] = 'posted'
        merged = BF._merge_rows(BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b'), stale)
        self.assertEqual(merged['rows'][0]['state'], 'done')


class Digest(unittest.TestCase):
    def test_every_n_and_the_hour_and_final_once(self):
        s3 = FakeS3()
        names = [f"Person {i}" for i in range(BP.DIGEST_EVERY + 3)]
        ledger, _l, _e, _c = _ledger(s3, names)
        eng = FakeEngine()
        sent = []
        sf = lambda to, subj, body: sent.append((to, subj, body)) or {'sent': True}  # noqa: E731
        now = time.time()
        _tick(s3, eng, cap=100, max_posts=100, now=now, send_fn=sf)
        self.assertEqual(len(eng.posts), len(names))
        # two done: no digest yet (under N, under the hour)
        for b in eng.posts[:2]:
            eng.complete(b['run_id'])
        out = _tick(s3, eng, cap=100, now=now + 60, send_fn=sf)
        self.assertEqual(out['digests'], 0)
        # the hour passes with completions: one digest
        out = _tick(s3, eng, cap=100, now=now + 3700, send_fn=sf)
        self.assertEqual(out['digests'], 1)
        self.assertEqual(len(sent), 1)
        self.assertIn('2 of', sent[0][1])
        self.assertIn('Person 0', sent[0][2])
        # N more complete: a digest on the count
        for b in eng.posts[2:2 + BP.DIGEST_EVERY]:
            eng.complete(b['run_id'])
        out = _tick(s3, eng, cap=100, now=now + 3800, send_fn=sf)
        self.assertEqual(out['digests'], 1)
        self.assertNotIn('Person 0\n', sent[1][2])        # reported once
        # the last one lands: the final digest, once, and the ledger closes
        eng.complete(eng.posts[-1]['run_id'])
        out = _tick(s3, eng, cap=100, now=now + 3900, send_fn=sf)
        self.assertEqual(out['digests'], 1)
        self.assertEqual(out['completed'], 1)
        self.assertIn('is done', sent[2][1])
        out = _tick(s3, eng, cap=100, now=now + 4000, send_fn=sf)
        self.assertEqual(out['digests'], 0)
        self.assertEqual(out['ledgers'], 0)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(led['status'], 'done')
        self.assertEqual(len(led['digest']['history']), 3)
        for _to, subj, body in sent:
            low = (subj + ' ' + body).lower()
            for bad in BANNED:
                self.assertNotIn(bad, low, (bad, subj))

    def test_probe_requester_gets_the_record_not_the_mail(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A'], uname='canary:bulk-smoke', email='canary@example.com')
        eng = FakeEngine()
        _tick(s3, eng, cap=5, send_fn=None)
        eng.complete(eng.posts[0]['run_id'])
        _tick(s3, eng, cap=5, send_fn=None)
        led = BP.load_ledger(ledger['bulk_id'], s3=s3, bucket='b')
        self.assertEqual(len(led['digest']['history']), 1)
        self.assertFalse(led['digest']['history'][0]['sent'])
        self.assertEqual(led['digest']['history'][0]['reason'], 'probe identity')

    def test_failed_rows_carry_the_partner_safe_line(self):
        s3 = FakeS3()
        ledger, _l, _e, _c = _ledger(s3, ['Person A', 'Person B'])
        ledger['rows'][0].update({'state': 'failed', 'error': BP.FAIL_LINE})
        ledger['rows'][1].update({'state': 'done', 'key': 'Person_B_TU.csv', 'avid_key': 'Person B - Avid Fan.csv'})
        subject, body = BP.digest_text(ledger, [0, 1], final=True)
        self.assertIn(BP.FAIL_LINE, body)
        self.assertIn('Person B (with its Avid Fan cut)', body)
        self.assertIn('1 refunded', subject)


class Copy(unittest.TestCase):
    def test_confirm_and_status_and_partner(self):
        s3 = FakeS3()
        read = BP.subjects_from_bytes('talent.csv', CSV_HEADER.encode())
        rows = BP.plan_rows(read['items'], BP.library_index(CATALOG))
        text = BP.confirm_reply(read, rows, WINDOW, 'alex', file_name='talent.csv', cap=10)
        self.assertTrue(text.startswith('Here is the list:'))
        self.assertIn('3 names read from talent.csv (1 duplicate collapsed, 3 blank or unusable rows dropped).', text)
        self.assertIn('1 name already in the library: reused as is, no charge. 2 new builds.', text)
        self.assertIn('Trailing 12 months', text)
        self.assertIn('$600 charged now (2 new builds at $300 each; reused profiles are free)', text)
        self.assertIn('about 45 minutes', text)
        self.assertIn(f"Say '{BP.RUN_LABEL}'", text)
        ledger, line, err, calls = _ledger(s3, ['Taylor Swift', 'MrBeast', 'Pedro Pascal'])
        self.assertIsNone(err)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['credits_used'], 10)
        self.assertEqual(calls[0][1]['quantity'], 2)
        self.assertEqual(calls[0][1]['job_id'], ledger['bulk_id'])
        self.assertIn('2 builds queued, 1 name reused from the library', line)
        st = BP.status_reply('alex', s3=s3, bucket='b', cap=10)
        self.assertIn('0 of 2 builds done, 1 reused from the library', st['reply'])
        self.assertIn('Next up: MrBeast, Pedro Pascal.', st['reply'])
        ps = BP.partner_status(ledger)
        self.assertEqual(ps['status'], 'running')
        self.assertEqual(ps['total'], 3)
        self.assertEqual(ps['reused'], 1)
        self.assertEqual(ps['in_progress'], 2)
        self.assertTrue(ps['rows'][0]['reused'])
        for t in (text, line, st['reply'], json.dumps(ps)):
            low = t.lower()
            for bad in BANNED:
                self.assertNotIn(bad, low, (bad, t[:80]))
        self.assertIsNone(BP.status_reply('nobody', s3=s3, bucket='b'))

    def test_eta_words(self):
        self.assertEqual(BP.eta_words(0), 'no build time: every name is already in the library')
        self.assertEqual(BP.eta_words(1, cap=10), 'about 45 minutes')
        self.assertEqual(BP.eta_words(20, cap=10), 'about 2 hours')
        self.assertEqual(BP.eta_words(1000, cap=10), 'about 3 days')

    def test_funds_error_and_free_seat(self):
        s3 = FakeS3()
        ledger, line, err, calls = _ledger(s3, ['MrBeast'], charged=False)
        self.assertIsNone(ledger)
        self.assertIn('cannot cover it right now', err)
        read = BP.subjects_from_text('MrBeast')
        ledger, line, err = BP.start({'username': 'jenna', 'role': 'super_admin'}, 'jenna', read, WINDOW, index={}, s3=s3, bucket='b',
                                     consume_credit=lambda *a, **k: (_ for _ in ()).throw(AssertionError('charged a free seat')), is_free=lambda u, n: True, usd_each=300.0)
        self.assertIsNone(err)
        self.assertTrue(ledger['charged']['free_seat'])
        self.assertFalse(ledger['rows'][0]['charged'])


class FakeHost:
    def __init__(self, s3):
        self.s3_client = s3
        self.S3_BUCKET = 'b'


class TheDoor(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.host = FakeHost(self.s3)
        self.user = {'username': 'alex', 'email': 'alex@example.com', 'role': 'user'}
        self.stash = {}
        from prometheus import documents as _docs
        _docs.store_upload('alex', 't1', 'talent.csv', CSV_HEADER.encode(), s3=self.s3, bucket='b')

    def pending(self, op, user, flow, *args):
        if op == 'stash':
            self.stash[flow] = args[0]
            return True
        if op == 'pick':
            if str(args[0] or '').strip().lower().rstrip('.!') in (BP.RUN_LABEL.lower(), 'yes', 'go ahead'):
                return self.stash.pop(flow, None)
            return None
        if op == 'peek':
            return self.stash.get(flow)
        if op == 'clear':
            self.stash.pop(flow, None)
            return True
        return None

    def ask(self, text, history=None, consume=None):
        return BP.analyze_hook(text, {'thread_id': 't1'}, history or [], self.user, {}, 'alex', host=self.host, pending=self.pending, catalog=CATALOG,
                               consume_credit=consume or (lambda *a, **k: True), is_free=lambda u, n: False, cap=10)

    def test_end_to_end(self):
        self.assertIsNone(self.ask('how big is the Taylor Swift audience'))
        out = self.ask('pull profiles for everyone on this list')
        self.assertTrue(out['reply'].startswith('Here is the list:'), out['reply'])
        self.assertIn('$600 charged now', out['reply'])
        self.assertEqual(out['followups'], [BP.RUN_LABEL, 'Cancel'])
        self.assertIn(BP.FLOW, self.stash)
        hist = [{'role': 'user', 'text': 'pull profiles for everyone on this list'}, {'role': 'agent', 'text': out['reply']}]
        calls = []
        out2 = self.ask(BP.RUN_LABEL, hist, consume=lambda u, **kw: calls.append(kw) or True)
        self.assertIn('2 builds queued, 1 name reused from the library', out2['reply'])
        self.assertTrue(out2.get('bulk_id', '').startswith('bulk_'))
        self.assertEqual(calls[0]['quantity'], 2)
        led = BP.load_ledger(out2['bulk_id'], s3=self.s3, bucket='b')
        self.assertEqual(led['requester'], {'username': 'alex', 'email': 'alex@example.com'})
        self.assertEqual([r['state'] for r in led['rows']], ['reused', 'queued', 'queued'])
        self.assertFalse(led['rows'][1]['run_avid'])      # Dolly's avid column said no
        st = self.ask('how is my list going?')
        self.assertIn('0 of 2 builds done', st['reply'])

    def test_cancel_and_no_list(self):
        out = self.ask('pull profiles for everyone on this list')
        hist = [{'role': 'agent', 'text': out['reply']}]
        out = self.ask('never mind', hist)
        self.assertIn('nothing was charged', out['reply'])
        self.assertNotIn(BP.FLOW, self.stash)
        s3 = FakeS3()
        host = FakeHost(s3)
        out = BP.analyze_hook('bulk pull these', {'thread_id': 't9'}, [], self.user, {}, 'alex', host=host, pending=self.pending, catalog=CATALOG, cap=10)
        self.assertIsNone(out)     # no list on the thread: not this door's ask


if __name__ == '__main__':
    print(f"[test_bulk_pull] listener validator: {VALIDATOR_SOURCE or 'not found (spec check skipped)'}")
    unittest.main(verbosity=1)
