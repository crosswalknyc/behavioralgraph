#!/usr/bin/env python3
"""POST /api/v1/profiles/bulk and GET /api/v1/profiles/<bulk_id> on the
partner API (Jenna 2026-11-05: "update the API to talk about bulk
uploads"). Hermetic: a fake S3, no Flask, no model, no network.

Covers: the body contract (plain names and {subject, category, notes}
objects, 1 to 1,000, unknown fields named in the 400, the date_range
shape, run_avid); the read (duplicates collapsed, links and numbers
dropped, category and notes kept); library reuse (a name already on file
is complete at once, reused: true, never charged); the charge, once, at
this key's rate for the new builds only; the free seat; Idempotency-Key
(the same key returns the first body, one ledger, one charge); one list
in progress per key (409 naming the open bulk id); the 402 with the price
and balance; the status poll (the ledger, per-row statuses, 24h download
links on complete rows, another key's list is a 404); the rate limit;
the partner belt over every string; and the schema entry.

The route functions are lifted out of app.py by source (no Flask
import) and run against fakes, so the test exercises the real route
logic, not a copy of it.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.pop('SYNTH_QUEUE_URL', None)
os.environ.pop('SYNTH_QUEUE_SECRET', None)
os.environ.pop('BULK_IN_FLIGHT', None)
os.environ.pop('SYNTH_WORKER_COUNT', None)

from test_bulk_pull import CATALOG, FakeS3  # noqa: E402

from prometheus import bulk_pull as BP  # noqa: E402
from prometheus import partner_bulk as PBK  # noqa: E402

APP_PY = os.path.join(ROOT, 'app.py')
USERS = {
    'acme': {'username': 'acme', 'email': 'ops@acme.example', 'role': 'user', 'company': 'Acme', 'credits': 500},
    'globex': {'username': 'globex', 'email': 'data@globex.example', 'role': 'user', 'company': 'Globex', 'credits': 500},
    'jenna': {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'role': 'super_admin', 'credits': -1},
}
BANNED = ('synth', 'hostmap', 'pipeline', 'claude', 'anthropic', 'modeled', 'panel', 'worker', 'hetzner', 'render',
          'queue worker', 'ledger', 'feeder', 'interpret', 'cohort', '\u2014', '\u2013')


# ------------------------------------------------------------ the routes
def _extract(names):
    with open(APP_PY, encoding='utf-8') as fh:
        src = fh.read()
    tree = ast.parse(src)
    pieces = []
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            pieces.append(ast.get_source_segment(src, node))
            found.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tg:
                if isinstance(t, ast.Name) and t.id in names:
                    pieces.append(ast.get_source_segment(src, node))
                    found.add(t.id)
    missing = set(names) - found
    if missing:
        raise SystemExit(f"could not find {sorted(missing)} in app.py")
    return '\n\n'.join(pieces)


class _Resp:
    """What jsonify returns here: the body dict plus a status code slot."""

    def __init__(self, body):
        self.body = body
        self.status_code = 200
        self.headers = {}


def _jsonify(body):
    return _Resp(json.loads(json.dumps(body, default=str)))


class _App:
    def route(self, *a, **k):
        return lambda fn: fn


class _Request:
    def __init__(self, body=None, headers=None, host_url='https://dashboard.crosswalknyc.com/'):
        self._body = body
        self.headers = headers or {}
        self.host_url = host_url

    def get_json(self, force=False):
        return self._body


class _SigningS3(FakeS3):
    def generate_presigned_url(self, op, Params=None, ExpiresIn=0):
        return f"https://b.s3.amazonaws.com/{(Params or {}).get('Key')}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=test"


class Harness:
    """The two routes with the app's own idempotency and rate limiter,
    one fake key owner at a time."""

    def __init__(self):
        self.s3 = _SigningS3()
        self.calls = []
        self.charge_ok = True
        self.catalog = list(CATALOG)
        ns = {'__name__': 'app_under_test', 'app': _App(), 'jsonify': _jsonify, 'traceback': __import__('traceback'),
              're': re, '_re_dlkey': re, 'json': json, 's3_client': self.s3, 'S3_BUCKET': 'b', 'request': None}
        ns['_synth_chat_gate'] = lambda allow_api_key=True: (self.user, None)
        ns['_partner_key_id'] = lambda u: 'key:' + str(u.get('username'))
        ns['_profile_catalog_for_chat'] = lambda: list(self.catalog)
        ns['_v1_balance_usd'] = lambda uname: 1234.5

        def _consume(username, description=None, job_id=None, pull_type=None, credits_used=1, addon_cuts=0, quantity=1):
            self.calls.append({'username': username, 'description': description, 'job_id': job_id, 'pull_type': pull_type,
                               'credits_used': credits_used, 'quantity': quantity})
            return self.charge_ok
        ns['consume_credit'] = _consume
        # the status route's other branches are never reached for a bulk id
        for name in ('_fetch_run_status_from_queue', '_lookup_run_owner', '_scrub_v1_status', '_generate_presigned_profile_url',
                     '_scrub_v1_freetext'):
            ns[name] = lambda *a, **k: None
        code = _extract(['api_v1_profiles_bulk', 'api_v1_profiles_status', '_idempotency_lookup', '_idempotency_store',
                         '_v1_idempotency_cache', '_v1_idempotency_lock', '_V1_IDEMPOTENCY_TTL_SECONDS', '_rate_limit_check',
                         '_rate_limit_response', '_V1_RATE_LIMITS', '_v1_rate_windows', '_v1_rate_windows_lock',
                         '_PROFILE_FILENAME_RE'])
        exec(compile(code, APP_PY, 'exec'), ns)
        self.ns = ns
        self.user = dict(USERS['acme'], _auth_via='api_key')

    def as_user(self, uname):
        self.user = dict(USERS[uname], _auth_via='api_key')
        return self

    def post(self, body, headers=None):
        self.ns['request'] = _Request(body, headers)
        out = self.ns['api_v1_profiles_bulk']()
        return self._norm(out)

    def get(self, run_id):
        self.ns['request'] = _Request(None, {})
        out = self.ns['api_v1_profiles_status'](run_id)
        return self._norm(out)

    @staticmethod
    def _norm(out):
        if isinstance(out, tuple):
            resp, code = out
            return resp.body, int(code)
        return out.body, int(getattr(out, 'status_code', 200) or 200)


def _setup_app_stub():
    """``prometheus.priced_read.live_user`` reads the live users row through
    ``app.load_users``; the stub keeps the seat rule hermetic."""
    mod = types.ModuleType('app')
    mod.load_users = lambda: {'users': {k: dict(v) for k, v in USERS.items()}}
    sys.modules['app'] = mod


_setup_app_stub()


def _strings(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _assert_clean(tc, doc):
    tc.assertEqual(PBK.violations(doc), [])
    for path, s in _strings(doc):
        if path.endswith(('.subject', 's3_key', 'download_url', 'status_url', 'bulk_id', 'run_id', 'open_bulk_id')):
            continue
        low = s.lower()
        for bad in BANNED:
            tc.assertNotIn(bad, low, (path, s))


# ------------------------------------------------------------------ tests
class Body(unittest.TestCase):
    def test_shapes(self):
        p, err = PBK.parse_body({'subjects': ['Taylor Swift', {'subject': 'Dolly Parton', 'category': 'MUSICIAN/BAND', 'notes': 'country icon'},
                                              {'name': 'Pedro Pascal'}]})
        self.assertIsNone(err)
        self.assertEqual(p['rows'], [['Taylor Swift', '', ''], ['Dolly Parton', 'MUSICIAN/BAND', 'country icon'], ['Pedro Pascal', '', '']])
        self.assertTrue(p['run_avid'])
        self.assertIsNone(p['window'])
        p, err = PBK.parse_body({'subjects': ['A B'], 'run_avid': False, 'date_range': {'start': '2025-01-01', 'end': '2025-12-31'}})
        self.assertIsNone(err)
        self.assertFalse(p['run_avid'])
        self.assertEqual((p['window']['start'], p['window']['end']), ('2025-01-01', '2025-12-31'))
        p, err = PBK.parse_body({'subjects': ['A B'], 'window': {'start': '2025-01-01', 'end': '2025-12-31'}})
        self.assertIsNone(err)
        self.assertEqual(p['window']['end'], '2025-12-31')

    def test_errors_name_the_field(self):
        cases = [
            ({}, 'required field: "subjects"'),
            ({'subjects': 'Taylor Swift'}, 'must be a list'),
            ({'subjects': []}, 'is empty'),
            ({'subjects': ['x'] * 1001, }, 'at most 1,000'),
            ({'subjects': ['A B'], 'force_new': True}, 'unknown field: "force_new"'),
            ({'subjects': ['A B'], 'run_avid': 'yes'}, '"run_avid" must be a boolean'),
            ({'subjects': ['A B'], 'date_range': 'last year'}, '"date_range" must be an object'),
            ({'subjects': ['A B'], 'date_range': {'start': '2025-13-01', 'end': '2025-12-31'}}, 'real dates, YYYY-MM-DD'),
            ({'subjects': ['A B'], 'date_range': {'start': '2025/01/01', 'end': '2025-12-31'}}, 'YYYY-MM-DD'),
            ({'subjects': ['A B'], 'date_range': {'start': '2026-01-01', 'end': '2025-12-31'}}, 'on or before'),
            ({'subjects': ['A B'], 'date_range': {'start': '2025-01-01', 'end': '2025-12-31', 'tz': 'UTC'}}, 'unknown field in "date_range": "tz"'),
            ({'subjects': ['A B'], 'date_range': {'start': '2025-01-01', 'end': '2025-12-31'}, 'window': {'start': '2025-01-01', 'end': '2025-12-31'}}, 'not both'),
            ({'subjects': [{'subject': 'A B', 'priority': 1}]}, 'unknown field in "subjects[0]": "priority"'),
            ({'subjects': [{'category': 'ACTOR'}]}, '"subjects[0].subject" must be a string'),
            ({'subjects': [42]}, '"subjects[0]" must be a plain name'),
            ({'subjects': ['x' * 130]}, 'exceeds 120 characters'),
            ({'subjects': [{'subject': 'A B', 'notes': 'n' * 301}]}, 'notes" exceeds 300'),
            ('not a dict', 'JSON object'),
        ]
        for body, needle in cases:
            p, err = PBK.parse_body(body)
            self.assertIsNone(p, body)
            self.assertIn(needle, err or '', (body, err))

    def test_read_collapses_and_drops(self):
        p, _e = PBK.parse_body({'subjects': ['Taylor Swift', 'taylor-swift', '', 'https://instagram.com/x', '12345',
                                              {'subject': 'Dolly Parton', 'category': 'MUSICIAN/BAND', 'notes': 'country icon'}]})
        read = PBK.read_from_items(p)
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift', 'Dolly Parton'])
        self.assertEqual(read['duplicates'], 1)
        self.assertEqual(read['dropped'], 3)
        self.assertEqual(read['items'][1]['category_hint'], 'MUSICIAN/BAND')
        self.assertEqual(read['items'][1]['notes'], 'country icon')
        self.assertTrue(all(i['run_avid'] for i in read['items']))


class TheRoute(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def test_reuse_charge_once_and_the_shape(self):
        body, code = self.h.post({'subjects': ['Taylor Swift', 'MrBeast', 'mrbeast', 'Pedro Pascal', 'https://x.y/z']})
        self.assertEqual(code, 200, body)
        self.assertTrue(body['success'])
        self.assertTrue(body['bulk_id'].startswith('bulk_'))
        self.assertEqual(body['status'], 'running')
        self.assertEqual((body['total'], body['reused'], body['queued'], body['duplicates'], body['dropped']), (3, 1, 2, 1, 1))
        self.assertEqual(body['price_each_usd'], 300.0)
        self.assertEqual(body['price_usd'], 600.0)
        self.assertEqual(body['charge_usd'], 600.0)
        self.assertEqual(body['balance_usd'], 1234.5)
        self.assertEqual(body['estimated_minutes'], 65)
        self.assertEqual(body['estimated_completion'], 'about 1 hour')
        self.assertTrue(body['status_url'].endswith('/api/v1/profiles/' + body['bulk_id']))
        self.assertIn('Trailing 12 months', body['date_window'])
        self.assertTrue(body['run_avid'])
        self.assertIn('summary email', body['digest'])
        rows = body['rows']
        self.assertEqual([r['subject'] for r in rows], ['Taylor Swift', 'MrBeast', 'Pedro Pascal'])
        self.assertEqual(rows[0]['status'], 'complete')
        self.assertTrue(rows[0]['reused'])
        self.assertEqual(rows[0]['s3_key'], 'Taylor_Swift_09_01_2026_10_00.csv')
        self.assertEqual(rows[1]['status'], 'queued')
        self.assertNotIn('s3_key', rows[1])
        # the charge: once, for the two new builds, at the key's rate, on the list's id
        self.assertEqual(len(self.h.calls), 1)
        c = self.h.calls[0]
        self.assertEqual((c['username'], c['credits_used'], c['quantity'], c['job_id']), ('acme', 10, 2, body['bulk_id']))
        self.assertEqual(c['pull_type'], 'Chatbot Profile IQ (new_build)')
        led = BP.load_ledger(body['bulk_id'], s3=self.h.s3, bucket='b')
        self.assertEqual(led['source'], 'partner_api_v1')
        self.assertEqual(led['requester'], {'username': 'acme', 'email': 'ops@acme.example'})
        self.assertEqual([r['state'] for r in led['rows']], ['reused', 'queued', 'queued'])
        self.assertEqual(led['charged']['usd_total'], 600.0)
        _assert_clean(self, body)

    def test_every_name_on_file_is_free(self):
        body, code = self.h.post({'subjects': ['Taylor Swift', 'Reba McEntire']})
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'complete')
        self.assertEqual((body['reused'], body['queued'], body['price_usd'], body['charge_usd']), (2, 0, 0.0, 0.0))
        self.assertEqual(body['estimated_minutes'], 0)
        self.assertIn('Nothing was charged', body['note'])
        self.assertIsNotNone(body['completed_at'])
        self.assertEqual(self.h.calls, [])
        # a complete list never blocks the next one
        body2, code2 = self.h.post({'subjects': ['Someone New']})
        self.assertEqual(code2, 200)
        self.assertEqual(body2['queued'], 1)

    def test_idempotency_key(self):
        hdr = {'Idempotency-Key': 'req-123'}
        b1, c1 = self.h.post({'subjects': ['MrBeast', 'Pedro Pascal']}, hdr)
        self.assertEqual(c1, 200)
        b2, c2 = self.h.post({'subjects': ['MrBeast', 'Pedro Pascal']}, hdr)
        self.assertEqual(c2, 200)
        self.assertEqual(b1, b2)
        self.assertEqual(len(self.h.calls), 1)
        self.assertEqual(len(BP.list_ledgers(s3=self.h.s3, bucket='b', active_only=False)), 1)

    def test_one_list_in_progress_per_key(self):
        b1, c1 = self.h.post({'subjects': ['MrBeast']})
        self.assertEqual(c1, 200)
        b2, c2 = self.h.post({'subjects': ['Pedro Pascal']})
        self.assertEqual(c2, 409, b2)
        self.assertFalse(b2['success'])
        self.assertEqual(b2['open_bulk_id'], b1['bulk_id'])
        self.assertIn(b1['bulk_id'], b2['error'])
        self.assertIn('One list runs at a time', b2['error'])
        self.assertTrue(b2['status_url'].endswith(b1['bulk_id']))
        self.assertEqual(len(self.h.calls), 1)
        _assert_clean(self, b2)
        # another key is not blocked by this one
        b3, c3 = self.h.as_user('globex').post({'subjects': ['Pedro Pascal']})
        self.assertEqual(c3, 200, b3)
        # the first list completes: the key may send the next one
        BP.update_ledger(b1['bulk_id'], lambda d: (d['rows'][0].update({'state': 'done', 'key': 'MrBeast_11_05_2026_10_00.csv'}) or d),
                         s3=self.h.s3, bucket='b')
        b4, c4 = self.h.as_user('acme').post({'subjects': ['Pedro Pascal']})
        self.assertEqual(c4, 200, b4)

    def test_insufficient_balance(self):
        self.h.charge_ok = False
        body, code = self.h.post({'subjects': ['MrBeast', 'Pedro Pascal', 'Taylor Swift']})
        self.assertEqual(code, 402, body)
        self.assertFalse(body['success'])
        self.assertEqual((body['price_usd'], body['price_each_usd'], body['new_builds'], body['balance_usd']), (600.0, 300.0, 2, 1234.5))
        self.assertIn('cannot cover it right now', body['error'])
        self.assertEqual(BP.list_ledgers(s3=self.h.s3, bucket='b', active_only=False), [])
        _assert_clean(self, body)

    def test_free_seat_is_never_charged(self):
        body, code = self.h.as_user('jenna').post({'subjects': ['MrBeast']})
        self.assertEqual(code, 200, body)
        self.assertEqual(body['price_usd'], 300.0)
        self.assertEqual(body['charge_usd'], 0.0)
        self.assertEqual(self.h.calls, [])

    def test_bad_bodies(self):
        for body, needle in ((None, 'required field'), ({'subjects': ['https://a.b/c', '12345']}, 'no usable names'),
                             ({'subjects': ['A B'], 'mode': 'fresh'}, 'unknown field: "mode"')):
            out, code = self.h.post(body)
            self.assertEqual(code, 400, out)
            self.assertIn(needle, out['error'])
            _assert_clean(self, out)
        self.assertEqual(self.h.calls, [])

    def test_status_poll(self):
        body, _c = self.h.post({'subjects': ['Taylor Swift', 'MrBeast', 'Pedro Pascal']})
        bid = body['bulk_id']
        st, code = self.h.get(bid)
        self.assertEqual(code, 200, st)
        self.assertEqual(st['run_id'], bid)
        self.assertEqual(st['bulk_id'], bid)
        self.assertEqual(st['status'], 'running')
        self.assertEqual((st['total'], st['complete'], st['built'], st['reused'], st['failed'], st['queued'], st['building'], st['in_progress']),
                         (3, 1, 0, 1, 0, 2, 0, 2))
        self.assertEqual([r['status'] for r in st['rows']], ['complete', 'queued', 'queued'])
        self.assertTrue(st['rows'][0]['download_url'].startswith('https://'))
        self.assertEqual(st['download_expires_seconds'], 86400)
        # the engine moves the rows: building, done with its avid, failed and refunded
        def _move(d):
            d['rows'][1].update({'state': 'running'})
            d['rows'][2].update({'state': 'done', 'key': 'Pedro_Pascal_11_05_2026_10_00.csv', 'avid_key': 'Pedro Pascal - Avid Fan.csv'})
            return d
        BP.update_ledger(bid, _move, s3=self.h.s3, bucket='b')
        st, _c = self.h.get(bid)
        self.assertEqual([r['status'] for r in st['rows']], ['complete', 'running', 'complete'])
        self.assertFalse(st['rows'][2]['reused'])
        self.assertTrue(st['rows'][2]['download_url'].startswith('https://'))
        self.assertIn('Pedro_Pascal_11_05_2026_10_00.csv', st['rows'][2]['download_url'])
        self.assertNotIn('avid_download_url', st['rows'][2])      # the avid key is not a catalog entry or a dated file
        self.assertEqual((st['building'], st['queued']), (1, 0))
        BP.update_ledger(bid, lambda d: (d['rows'][1].update({'state': 'failed', 'error': BP.FAIL_LINE, 'refunded': True}) or d), s3=self.h.s3, bucket='b')
        st, _c = self.h.get(bid)
        self.assertEqual(st['rows'][1]['status'], 'failed')
        self.assertEqual(st['rows'][1]['reason'], BP.FAIL_LINE)
        self.assertTrue(st['rows'][1]['refunded'])
        self.assertEqual(st['status'], 'complete')
        self.assertEqual(st['failed'], 1)
        _assert_clean(self, st)
        # another key never sees this list
        other, code = self.h.as_user('globex').get(bid)
        self.assertEqual(code, 404)
        self.assertEqual(other['error'], 'run not found')
        # an unknown bulk id
        other, code = self.h.get('bulk_zzzzzzzzzzzz')
        self.assertEqual(code, 404)

    def test_rate_limit(self):
        per_min, _per_day = self.h.ns['_V1_RATE_LIMITS']['/v1/bulk']
        for _i in range(per_min):
            out, code = self.h.post({'subjects': 'bad'})     # the limit counts the call, not the outcome
            self.assertEqual(code, 400)
        out, code = self.h.post({'subjects': 'bad'})
        self.assertEqual(code, 429, out)
        self.assertIn('retry_after_seconds', out)


class SchemaAndSources(unittest.TestCase):
    def test_schema_and_route_strings_carry_no_internal_words(self):
        with open(APP_PY, encoding='utf-8') as fh:
            src = fh.read()
        tree = ast.parse(src)
        banned = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_V1_BANNED_TOKENS' for t in node.targets):
                banned = ast.literal_eval(node.value)
                break
        self.assertTrue(banned)
        for fn in ('api_v1_schema', 'api_v1_profiles_bulk'):
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn)
            lits = ' '.join(n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and not (fn == 'api_v1_profiles_bulk' and n is node.body[0].value)).lower()
            hits = [t for t in banned if t in lits]
            self.assertEqual(hits, [], fn)
        self.assertIn("'POST /api/v1/profiles/bulk'", src)
        self.assertIn("'/v1/bulk'", src)
        # the module's own copy has no internal words either
        with open(os.path.join(ROOT, 'prometheus', 'partner_bulk.py'), encoding='utf-8') as fh:
            mod = fh.read()
        for s in re.findall(r"'([^'\n]{12,})'", mod):
            low = s.lower()
            if 'prometheus.' in low or low.startswith(('system/', 'partner_api', 'the api')):
                continue
            for bad in ('synth', 'hostmap', 'claude', 'anthropic', 'hetzner', '\u2014'):
                self.assertNotIn(bad, low, s)

    def test_partner_status_is_the_documented_shape(self):
        s3 = FakeS3()
        read = BP.subjects_from_text('Taylor Swift\nMrBeast')
        led, _l, _e = BP.start({'username': 'acme', 'email': 'ops@acme.example'}, 'acme', read, None, index=BP.library_index(CATALOG),
                               s3=s3, bucket='b', consume_credit=lambda *a, **k: True, is_free=lambda u, n: False, usd_each=300.0,
                               file_name=PBK.FILE_NAME, source=PBK.SOURCE)
        ps = BP.partner_status(led)
        self.assertEqual(sorted(ps), sorted(['bulk_id', 'status', 'total', 'complete', 'built', 'reused', 'failed', 'queued', 'building',
                                             'in_progress', 'run_avid', 'date_window', 'created_at', 'completed_at', 'rows']))
        self.assertEqual([r['status'] for r in ps['rows']], ['complete', 'queued'])
        self.assertIsNone(ps['completed_at'])
        self.assertTrue(ps['created_at'].endswith('Z'))
        words = BP.status_words(led)
        self.assertTrue(words.startswith('Your list from the API ('), words)
        self.assertEqual(PBK.violations(ps), [])


if __name__ == '__main__':
    unittest.main(verbosity=1)
