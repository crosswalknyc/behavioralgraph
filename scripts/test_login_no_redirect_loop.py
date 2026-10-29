#!/usr/bin/env python3
"""The sign-in page never loops, and users.json can never be replaced
by a non-document (2026-10-28 outage).

What happened: a Brand Partnership IQ job finished, its access-grant
mutator handed `True` back to the users.json compare-and-swap writer,
and the literal `true` landed on s3://dashboard-inputs/system/users.json.
Every authenticated request then raised TypeError in get_current_user,
the 500 handler sent the page to /login, /login saw a username in the
session and sent it back to /, and the browser gave up with
ERR_TOO_MANY_REDIRECTS.

This test pins both halves:
  1. The CAS writer, save_users and load_users refuse anything that is
     not a users document, and load_users serves the last known good
     copy when S3 holds a corrupt one.
  2. /login never answers with a redirect to itself: no cookie, an
     oversized junk cookie, a session whose user cannot be resolved
     (corrupt users doc, deleted seat) all render the sign-in page;
     / with no session is a single hop to /login.
  3. No users.json mutator in the repo returns a bool, and none is a
     lambda; the brand partnership wizard draft lives server-side, not
     in the cookie session.

Hermetic: bogus AWS credentials so no S3 call can succeed, the local
users mirror is pointed at a temp file, and load_users is monkeypatched
per scenario.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# No real S3 from this process, ever.
os.environ['AWS_ACCESS_KEY_ID'] = 'AKIATESTNOTREAL000000'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'not-a-real-secret-for-the-login-loop-test'
os.environ['AWS_SHARED_CREDENTIALS_FILE'] = '/nonexistent/aws/credentials'
os.environ['AWS_CONFIG_FILE'] = '/nonexistent/aws/config'
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

import app as A  # noqa: E402

A.s3_client = None
A.app.testing = False
A.app.config['PROPAGATE_EXCEPTIONS'] = False
_TMP = tempfile.mkdtemp(prefix='login_loop_users_')
A.USERS_FILE = os.path.join(_TMP, 'users.json')

VALID_DOC = {
    'users': {
        'jenna': {
            'password_hash': 'pbkdf2:sha256:1$aa$' + 'b' * 64,
            'role': 'super_admin',
            'credits': -1,
            'allowed_categories': ['*'],
            'allowed_runs': ['*'],
        },
    },
    'categories': {},
    'runs': {},
}


class _FakeExceptions:
    NoSuchKey = type('NoSuchKey', (Exception,), {})


class FakeS3:
    """Just enough of boto3's S3 client for load_users and the CAS
    writer: get_object returns a fixed body, put_object records."""

    exceptions = _FakeExceptions()

    def __init__(self, body=b'true', etag='e-corrupt'):
        self.body = body
        self.etag = etag
        self.puts = []

    def get_object(self, **kw):
        return {'Body': io.BytesIO(self.body), 'ETag': f'"{self.etag}"'}

    def put_object(self, **kw):
        self.puts.append(kw)
        return {'ETag': '"e-new"'}


class LoginNeverLoops(unittest.TestCase):

    def setUp(self):
        self._load_users = A.load_users
        self._s3 = A.s3_client
        self.c = A.app.test_client()

    def tearDown(self):
        A.load_users = self._load_users
        A.s3_client = self._s3

    def _login_session(self, username='jenna'):
        with self.c.session_transaction() as sess:
            sess['username'] = username
            sess['role'] = 'super_admin'

    def test_login_no_cookie_is_200(self):
        r = self.c.get('/login')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('Location', r.headers)
        set_cookie = r.headers.get('Set-Cookie') or ''
        self.assertLess(len(set_cookie), 3072)

    def test_root_no_cookie_is_one_hop_to_login(self):
        r = self.c.get('/')
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get('Location') or ''
        self.assertTrue(loc.endswith('/login'), loc)
        r2 = self.c.get(loc)
        self.assertEqual(r2.status_code, 200)

    def test_login_with_oversized_junk_cookie_is_200(self):
        junk = 'session=' + ('x' * 5000)
        r = self.c.get('/login', headers={'Cookie': junk})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('Location', r.headers)

    def test_login_with_session_but_corrupt_users_doc_is_200(self):
        # The exact 2026-10-28 state: load_users hands back the bool
        # that json.loads('true') produced.
        A.load_users = lambda: True
        self._login_session()
        r = self.c.get('/login')
        self.assertEqual(r.status_code, 200, r.headers.get('Location'))
        self.assertNotIn('Location', r.headers)
        with self.c.session_transaction() as sess:
            self.assertNotIn('username', sess)

    def test_root_with_session_but_corrupt_users_doc_terminates(self):
        A.load_users = lambda: True
        self._login_session()
        hops = 0
        path = '/'
        seen = []
        while hops < 5:
            r = self.c.get(path)
            seen.append((path, r.status_code))
            if r.status_code != 302:
                break
            path = r.headers['Location']
            hops += 1
        self.assertEqual(r.status_code, 200, seen)
        self.assertLessEqual(hops, 2, seen)

    def test_login_with_session_for_deleted_seat_is_200(self):
        A.load_users = lambda: {'users': {}, 'categories': {}, 'runs': {}}
        self._login_session('ghost')
        r = self.c.get('/login')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('Location', r.headers)

    def test_login_with_resolving_session_still_goes_home(self):
        A.load_users = lambda: json.loads(json.dumps(VALID_DOC))
        self._login_session()
        r = self.c.get('/login')
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get('Location') or ''
        self.assertTrue(loc.endswith('/'), loc)
        self.assertFalse(loc.endswith('/login'), loc)

    def test_error_handlers_never_redirect_login_to_itself(self):
        with A.app.test_request_context('/login'):
            r = A.not_found(Exception('x'))
            status = r[1] if isinstance(r, tuple) else r.status_code
            self.assertEqual(status, 404)
            r = A.server_error(Exception('x'))
            status = r[1] if isinstance(r, tuple) else r.status_code
            self.assertEqual(status, 500)
        with A.app.test_request_context('/anything-else'):
            r = A.server_error(Exception('x'))
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers['Location'].endswith('/login'))


class UsersDocNeverCorrupted(unittest.TestCase):

    def setUp(self):
        self._s3 = A.s3_client
        self._read = A._s3_json_read_with_etag
        self._cache = dict(A._users_s3_cache)

    def tearDown(self):
        A.s3_client = self._s3
        A._s3_json_read_with_etag = self._read
        A._users_s3_cache.clear()
        A._users_s3_cache.update(self._cache)

    def test_validity_predicate(self):
        self.assertTrue(A._users_doc_is_valid(VALID_DOC))
        for bad in (True, False, None, [], 'true', 1, {}, {'users': []},
                    {'users': None}):
            self.assertFalse(A._users_doc_is_valid(bad), repr(bad))

    def test_cas_writer_skips_a_bool_mutator_result(self):
        fake = FakeS3()
        A.s3_client = fake
        A._s3_json_read_with_etag = lambda b, k: (
            json.loads(json.dumps(VALID_DOC)), 'e0')
        out = A._s3_json_cas_update('b', 'system/users.json',
                                    lambda d: True, log_name='users.json')
        self.assertEqual(out, (None, None))
        self.assertEqual(fake.puts, [])
        out = A._s3_json_cas_update('b', 'system/users.json',
                                    lambda d: 'oops', log_name='users.json')
        self.assertEqual(out, (None, None))
        self.assertEqual(fake.puts, [])
        # A real document still lands.
        final, etag = A._s3_json_cas_update(
            'b', 'system/users.json', lambda d: d, log_name='users.json')
        self.assertTrue(A._users_doc_is_valid(final))
        self.assertEqual(len(fake.puts), 1)
        self.assertEqual(json.loads(fake.puts[0]['Body']), VALID_DOC)

    def test_users_cas_mutate_with_bool_mutator_writes_nothing(self):
        fake = FakeS3()
        A.s3_client = fake
        A._s3_json_read_with_etag = lambda b, k: (
            json.loads(json.dumps(VALID_DOC)), 'e0')
        before = os.path.exists(A.USERS_FILE) and _read(A.USERS_FILE)
        out = A._users_cas_mutate(lambda d: True)
        self.assertIsNone(out)
        self.assertEqual(fake.puts, [])
        after = os.path.exists(A.USERS_FILE) and _read(A.USERS_FILE)
        self.assertEqual(before, after)

    def test_save_users_refuses_a_non_document(self):
        A.s3_client = None
        with open(A.USERS_FILE, 'w') as f:
            json.dump(VALID_DOC, f)
        for bad in (True, None, [], 'true', {'users': []}):
            self.assertFalse(A.save_users(bad), repr(bad))
        with open(A.USERS_FILE) as f:
            self.assertEqual(json.load(f), VALID_DOC)

    def test_load_users_serves_last_known_good_over_corrupt_s3(self):
        good_raw = json.dumps(VALID_DOC, indent=2)
        A._users_s3_cache.update({'etag': 'e-good', 'raw': good_raw,
                                  'mirror_etag': 'e-good'})
        A.s3_client = FakeS3(body=b'true', etag='e-corrupt')
        doc = A.load_users()
        self.assertEqual(doc, VALID_DOC)
        # The corrupt bytes never enter the read cache.
        self.assertEqual(A._users_s3_cache['raw'], good_raw)
        self.assertEqual(A._users_s3_cache['etag'], 'e-good')
        # And get_current_user is whole again.
        with A.app.test_request_context('/'):
            from flask import session
            session['username'] = 'jenna'
            self.assertEqual(A.get_current_user()['role'], 'super_admin')

    def test_load_users_ignores_a_corrupt_local_mirror(self):
        A.s3_client = None
        with open(A.USERS_FILE, 'w') as f:
            f.write('true')
        doc = A.load_users()
        self.assertTrue(A._users_doc_is_valid(doc))


_MUTATOR_FILES = ['app.py', 'wallet.py', 'billing_routes.py',
                  'pay_per_use.py', 'site_signup.py']


def _py_files():
    for f in _MUTATOR_FILES:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            yield p
    for sub in ('prometheus', 'migration'):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, sub)):
            for fn in files:
                if fn.endswith('.py'):
                    yield os.path.join(dirpath, fn)


def _is_cas_mutate_call(node):
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else (
        fn.id if isinstance(fn, ast.Name) else '')
    return name == '_users_cas_mutate' and node.args


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _scope_of(node, parents):
    """The innermost function (or the module) a node sits in."""
    cur = parents.get(node)
    while cur is not None and not isinstance(
            cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        cur = parents.get(cur)
    return cur


def _own_returns(fn_def):
    """Return statements that belong to fn_def itself, not to a
    function nested inside it."""
    out = []
    stack = list(fn_def.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return) and n.value is not None:
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


class MutatorsReturnDocuments(unittest.TestCase):
    """Every users.json mutator hands back the doc or None. A bool
    return, or a `call(...) or None` lambda, is the 2026-10-28 bug."""

    def test_no_mutator_returns_a_bool_and_none_is_a_lambda(self):
        problems = []
        checked = 0
        for path in _py_files():
            try:
                tree = ast.parse(_read(path))
            except Exception:
                continue
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            rel = os.path.relpath(path, ROOT)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and _is_cas_mutate_call(node)):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Lambda):
                    problems.append(f'{rel}:{node.lineno} lambda mutator')
                    continue
                if not isinstance(arg, ast.Name):
                    continue
                scope = _scope_of(node, parents)
                # The mutator is the def with that name declared in the
                # same scope as the call (sibling defs that happen to
                # share a name elsewhere in the file do not count).
                cands = [d for d in ast.walk(tree)
                         if isinstance(d, ast.FunctionDef) and d.name == arg.id
                         and _scope_of(d, parents) is scope]
                for d in cands:
                    checked += 1
                    for r in _own_returns(d):
                        v = r.value
                        if isinstance(v, ast.Constant) and isinstance(v.value, bool):
                            problems.append(f'{rel}:{r.lineno} returns {v.value}')
                        if isinstance(v, ast.BoolOp) and v.values and \
                                isinstance(v.values[0], ast.Call):
                            problems.append(f'{rel}:{r.lineno} returns call(...) or ...')
        self.assertGreater(checked, 10, 'the scan found no mutators to check')
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_bpiq_grant_mutator_returns_the_doc(self):
        src = _read(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'))
        self.assertNotIn(".get(username)) or None)", src)
        self.assertIn('def _grant_doc(data):', src)


class WizardDraftIsServerSide(unittest.TestCase):

    def test_wizard_never_touches_the_cookie_session(self):
        from prometheus import bpiq_wizard as W
        src = _read(W.__file__)
        self.assertIsNone(re.search(r'\bsession\[', src))
        self.assertNotIn('from flask import session', src)
        store = W.shared_store()
        for m in ('load', 'save', 'clear'):
            self.assertTrue(callable(getattr(store, m, None)), m)
        self.assertFalse(type(store).__name__.lower().startswith('session'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
