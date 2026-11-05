#!/usr/bin/env python3
"""Digital Twins IQ is company-scoped (Jenna 2026-11-05): a seat sees the
twins, pilot tests, rooms, saved setups, reports and norms its own
shared-wallet company built; nothing from another company; Crosswalk
staff see every company with a company filter and can assign a twin to
a company. Hermetic: FakeS3, a fake app module, no network."""
from __future__ import annotations

import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import digital_twin_company as CO  # noqa: E402
from migration import digital_twin_norms as N  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, key):
        import hashlib
        return hashlib.md5(self.objs[key]).hexdigest()

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key]), 'ETag': f'"{self._etag(Key)}"'}

    def put_object(self, Bucket, Key, Body, **kw):
        if 'IfMatch' in kw and (Key not in self.objs or self._etag(Key) != kw['IfMatch']):
            raise RuntimeError('PreconditionFailed 412')
        if 'IfNoneMatch' in kw and Key in self.objs:
            raise RuntimeError('PreconditionFailed 412')
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)

    def list_objects_v2(self, **kw):
        return {'Contents': [{'Key': k, 'LastModified': '2026'} for k in list(self.objs) if k.startswith(kw.get('Prefix', ''))]}

    def copy_object(self, **kw):
        src = kw.get('CopySource') or {}
        self.objs[kw['Key']] = self.objs[src['Key']]


USERS = {
    'users': {
        'a1': {'email': 'a1@acme.example', 'role': 'user', 'company': 'Acme', 'billing_source': 'company', 'has_digital_twins_iq_access': True},
        'a2': {'email': 'a2@acme.example', 'role': 'admin', 'company': 'Acme', 'billing_source': 'company', 'has_digital_twins_iq_access': True},
        'b1': {'email': 'b1@globex.example', 'role': 'user', 'company': 'Globex', 'billing_source': 'company', 'has_digital_twins_iq_access': True},
        'b2': {'email': 'b2@globex.example', 'role': 'user', 'company': 'Globex', 'billing_source': 'user', 'has_digital_twins_iq_access': True},
        'solo': {'email': 'solo@example.com', 'role': 'user', 'has_digital_twins_iq_access': True},
        'jenna': {'email': 'jenna@crosswalknyc.com', 'role': 'super_admin'},
        'admin': {'email': 'ops@example.com', 'role': 'super_admin'},
        'canary:probe': {'role': 'user', 'has_digital_twins_iq_access': True},
    },
    'companies': {
        'Acme': {'paying_customer': True, 'wallet_balance_usd': 500.0},
        'Globex': {'paying_customer': True, 'wallet_balance_usd': 500.0},
    },
}

KEY_A = 'digital-twins/a1/Acme_Show_20261105_120000_aaaaaaaaaa.json.gz'
KEY_B = 'digital-twins/b1/Globex_Fans_20261105_120000_bbbbbbbbbb.json.gz'
KEY_ADMIN = 'digital-twins/admin/Paramount_20261030_202216_418f0c1d10.json.gz'


def _user(name):
    rec = dict(USERS['users'][name])
    rec['username'] = name
    return rec


def _payload(name, created_by, key):
    return {
        'meta': {'name': name, 'subject': name, 'created_by': created_by, 'size_us': 9339205, 'key': key, 'format': 'series'},
        'panel_sessions': [{'id': 'room1', 'title': f'{name} room', 'created_by': created_by, 'twin_keys': [key],
                            'group': {'roster': [], 'segments': []}, 'questions': []}],
        'pilot_sessions': [{'id': 'pilot1', 'title': f'{name} pilot', 'created_by': created_by, 'twin_keys': [key], 'mode': 'pilot',
                            'status': 'stopped', 'group': {'roster': []}, 'segments': []}],
        'pilot_setups': [{'id': 'setup1', 'name': f'{name} setup', 'created_by': created_by, 'tab': 'pilot', 'mode': '', 'fields': {}}],
        'runs': [],
    }


def _seed(s3):
    SYN.save_twin(s3, KEY_A, _payload('Acme Show', 'a1', KEY_A))
    SYN.save_twin(s3, KEY_B, _payload('Globex Fans', 'b1', KEY_B))
    SYN.save_twin(s3, KEY_ADMIN, _payload('Paramount', 'admin', KEY_ADMIN))


class Grouping(unittest.TestCase):
    def setUp(self):
        CO.set_users_loader(lambda: USERS)

    def tearDown(self):
        CO.set_users_loader(None)

    def test_company_is_the_users_json_company_field(self):
        self.assertEqual(CO.company_of_user(_user('a1'), 'a1'), 'Acme')
        self.assertEqual(CO.company_of_user(_user('a2'), 'a2'), 'Acme')
        self.assertEqual(CO.company_of_user(_user('b2'), 'b2'), 'Globex')      # the company field, whichever wallet the seat bills
        self.assertEqual(CO.company_of_user(_user('solo'), 'solo'), 'user:solo')   # no company: their own company
        self.assertEqual(CO.company_of_user(_user('jenna'), 'jenna'), CO.STAFF_COMPANY)
        self.assertEqual(CO.company_of_user(_user('admin'), 'admin'), CO.STAFF_COMPANY)
        self.assertEqual(CO.company_of_username('a1'), 'Acme')
        self.assertEqual(CO.company_of_username('nobody'), 'user:nobody')

    def test_twin_company_derives_from_the_builder_and_an_explicit_field_wins(self):
        self.assertEqual(CO.twin_company(None, {'key': KEY_A, 'created_by': 'a1'}, KEY_A), 'Acme')
        self.assertEqual(CO.twin_company(None, None, KEY_B), 'Globex')                   # the owner segment of the key
        self.assertEqual(CO.twin_company(None, {'key': KEY_ADMIN, 'created_by': 'admin'}, KEY_ADMIN), CO.STAFF_COMPANY)
        self.assertEqual(CO.twin_company({'meta': {'created_by': 'a1', 'company': 'Globex'}}, None, KEY_A), 'Globex')
        self.assertEqual(CO.twin_company(None, {'key': KEY_A, 'created_by': 'a1', 'company': 'Globex'}, KEY_A), 'Globex')

    def test_can_see(self):
        run_a = {'key': KEY_A, 'created_by': 'a1'}
        self.assertTrue(CO.can_see(_user('a1'), run=run_a, key=KEY_A))
        self.assertTrue(CO.can_see(_user('a2'), run=run_a, key=KEY_A))
        self.assertFalse(CO.can_see(_user('b1'), run=run_a, key=KEY_A))
        self.assertFalse(CO.can_see(_user('solo'), run=run_a, key=KEY_A))
        self.assertTrue(CO.can_see(_user('jenna'), run=run_a, key=KEY_A))
        self.assertFalse(CO.can_see(_user('canary:probe'), run=run_a, key=KEY_A))
        self.assertFalse(CO.can_see(None, run=run_a, key=KEY_A))

    def test_filter_and_companies(self):
        runs = [{'key': KEY_A, 'created_by': 'a1'}, {'key': KEY_B, 'created_by': 'b1'}, {'key': KEY_ADMIN, 'created_by': 'admin'}]
        self.assertEqual([r['key'] for r in CO.filter_runs(list(runs), _user('a1'))], [KEY_A])
        self.assertEqual([r['key'] for r in CO.filter_runs(list(runs), _user('b2'))], [KEY_B])
        self.assertEqual([r['key'] for r in CO.filter_runs(list(runs), _user('canary:probe'))], [])
        self.assertEqual(len(CO.filter_runs(list(runs), _user('jenna'))), 3)
        self.assertEqual([r['key'] for r in CO.filter_runs(list(runs), _user('jenna'), company='Globex')], [KEY_B])
        self.assertEqual([r['key'] for r in CO.filter_runs(list(runs), _user('a1'), company='Globex')], [KEY_A])   # the filter is staff-only
        names = {c['company']: c['twins'] for c in CO.companies(CO.annotate(list(runs)))}
        self.assertEqual(names['Acme'], 1)
        self.assertEqual(names['Globex'], 1)
        self.assertEqual(names[CO.STAFF_COMPANY], 1)
        self.assertIn('user:solo', names)

    def test_dt_visible_runs_and_can_open_are_scoped(self):
        runs = [{'key': KEY_A, 'created_by': 'a1'}, {'key': KEY_B, 'created_by': 'b1'}]
        self.assertEqual([r['key'] for r in DT.visible_runs(runs, _user('a2'))], [KEY_A])
        self.assertTrue(DT.can_open(_user('a2'), KEY_A, runs[0]))
        self.assertFalse(DT.can_open(_user('a2'), KEY_B, runs[1]))     # a client admin is scoped to its company
        self.assertTrue(DT.can_open(_user('jenna'), KEY_B, runs[1]))
        self.assertEqual([r['key'] for r in DT.visible_runs(runs, _user('jenna'), company='Acme')], [KEY_A])


class Routes(unittest.TestCase):
    """Two companies with two seats each, a staff seat, a probe."""

    @classmethod
    def setUpClass(cls):
        try:
            import flask  # noqa: F401
        except Exception:
            raise unittest.SkipTest('flask not installed')

    def setUp(self):
        import flask
        import digital_twins_api as A
        from prometheus.host import host
        self.s3 = FakeS3()
        _seed(self.s3)
        self.current = {'user': _user('a1')}
        fake_app = types.SimpleNamespace(s3_client=self.s3, get_current_user=lambda: self.current['user'], _normalize_role=lambda r: r,
                                         consume_credit=lambda *a, **k: True, load_users=lambda: json.loads(json.dumps(USERS)))
        self._old_app = sys.modules.get('app')
        sys.modules['app'] = fake_app
        CO.set_users_loader(None)
        host.bind(gate=lambda allow_api_key=False: (self.current['user'], None), s3_client=self.s3)
        app = flask.Flask('t')
        app.secret_key = 'x'
        A.register(app)
        self.c = app.test_client()

    def tearDown(self):
        if self._old_app is not None:
            sys.modules['app'] = self._old_app
        else:
            sys.modules.pop('app', None)
        CO.set_users_loader(None)

    def _as(self, name):
        self.current['user'] = _user(name)
        with self.c.session_transaction() as sess:
            sess['username'] = name

    def _list(self, **params):
        r = self.c.get('/api/digital-twins/list', query_string=params)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return r.get_json()

    def test_listing_is_the_companys_own(self):
        for who in ('a1', 'a2'):
            self._as(who)
            out = self._list()
            self.assertEqual([r['key'] for r in out['runs']], [KEY_A], who)
            self.assertEqual(out['company'], 'Acme')
            self.assertFalse(out['is_staff'])
            self.assertNotIn('companies', out)
        for who in ('b1', 'b2'):
            self._as(who)
            self.assertEqual([r['key'] for r in self._list()['runs']], [KEY_B], who)
        self._as('solo')
        self.assertEqual(self._list()['runs'], [])
        self._as('canary:probe')
        self.assertEqual(self._list()['runs'], [])

    def test_another_companys_objects_read_as_not_there(self):
        self._as('b1')
        for path in (f'/api/digital-twins/{KEY_A}', f'/api/digital-twins/{KEY_A}/export.csv', f'/api/digital-twins/{KEY_A}/pilot/pilot1',
                     f'/api/digital-twins/{KEY_A}/panel/room1', f'/api/digital-twins/{KEY_A}/pilot/pilot1/export.csv',
                     f'/api/digital-twins/{KEY_A}/pilot/pilot1/report.pdf'):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.get_json()['error'], CO.NOT_IN_LIBRARY, path)
        for path in (f'/api/digital-twins/{KEY_A}/panel/room1/duplicate', f'/api/digital-twins/{KEY_A}/pilot/pilot1/duplicate',
                     f'/api/digital-twins/{KEY_A}/panel/open', f'/api/digital-twins/{KEY_A}/pilot/open', f'/api/digital-twins/{KEY_A}/pilot/pilot1/stop',
                     f'/api/digital-twins/{KEY_A}/pilot/setups/setup1/duplicate', f'/api/digital-twins/{KEY_A}/panel/room1/ask',
                     f'/api/digital-twins/{KEY_A}/run', f'/api/digital-twins/{KEY_A}/delete'):
            r = self.c.post(path, json={'question': 'why', 'kind': 'content'})
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.get_json()['error'], CO.NOT_IN_LIBRARY, path)
        # a room cannot borrow another company's twin as a second audience
        r = self.c.post(f'/api/digital-twins/{KEY_B}/panel/open', json={'twins': [KEY_A]})
        self.assertNotEqual(r.status_code, 500)
        twin = SYN.load_twin(self.s3, KEY_B)
        for sess in twin.get('panel_sessions') or []:
            self.assertNotIn(KEY_A, sess.get('twin_keys') or [])
        # the probe sees nothing
        self._as('canary:probe')
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}').status_code, 404)
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_ADMIN}').status_code, 404)
        # the company's own seat opens its objects
        self._as('a2')
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}').status_code, 200)
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}/pilot/pilot1').status_code, 200)
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}/export.csv').status_code, 200)

    def test_staff_see_everything_and_can_filter(self):
        self._as('jenna')
        out = self._list()
        self.assertEqual({r['key'] for r in out['runs']}, {KEY_A, KEY_B, KEY_ADMIN})
        self.assertTrue(out['is_staff'])
        self.assertTrue(out['can_assign'])
        self.assertEqual(out['company_filter'], '')
        by = {r['key']: r['company'] for r in out['runs']}
        self.assertEqual(by[KEY_A], 'Acme')
        self.assertEqual(by[KEY_B], 'Globex')
        self.assertEqual(by[KEY_ADMIN], CO.STAFF_COMPANY)          # built by admin: Crosswalk until assigned
        names = {c['company'] for c in out['companies']}
        self.assertTrue({'Acme', 'Globex', CO.STAFF_COMPANY} <= names)
        self.assertEqual([r['key'] for r in self._list(company='Globex')['runs']], [KEY_B])
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_B}').status_code, 200)

    def test_assign_moves_the_twin_with_its_tests_rooms_and_setups(self):
        self._as('a2')
        r = self.c.post(f'/api/digital-twins/{KEY_A}/company', json={'company': 'Globex'})
        self.assertEqual(r.status_code, 403)                                       # a client admin cannot assign
        self._as('jenna')
        r = self.c.post(f'/api/digital-twins/{KEY_A}/company', json={'company': 'Globex'})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.assertEqual(r.get_json()['company'], 'Globex')
        self.assertEqual(r.get_json()['was'], 'Acme')
        twin = SYN.load_twin(self.s3, KEY_A)
        self.assertEqual(twin['meta']['company'], 'Globex')
        idx = json.loads(self.s3.objs[SYN.S3_INDEX_KEY].decode('utf-8'))
        self.assertEqual(next(e for e in idx['runs'] if e['key'] == KEY_A)['company'], 'Globex')
        CO.reset_cache()
        self._as('a1')
        self.assertEqual(self._list()['runs'], [])
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}').status_code, 404)
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}/pilot/pilot1').status_code, 404)
        self._as('b1')
        self.assertEqual({r['key'] for r in self._list()['runs']}, {KEY_A, KEY_B})
        g = self.c.get(f'/api/digital-twins/{KEY_A}').get_json()
        self.assertEqual(g['twin']['pilot_sessions'][0]['id'], 'pilot1')
        self.assertEqual(g['twin']['panel_sessions'][0]['id'], 'room1')
        self.assertEqual(g['twin']['pilot_setups'][0]['id'], 'setup1')
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}/pilot/pilot1').status_code, 200)
        self.assertEqual(self.c.get(f'/api/digital-twins/{KEY_A}/panel/room1').status_code, 200)
        self._as('jenna')
        self.assertEqual([r['key'] for r in self._list(company='Acme')['runs']], [])
        self.assertEqual(self.c.post(f'/api/digital-twins/{KEY_A}/company', json={}).status_code, 400)
        self.assertEqual(self.c.post('/api/digital-twins/digital-twins/x/nope.json.gz/company', json={'company': 'Acme'}).status_code, 404)

    def test_norms_routes_read_the_companys_own_tests(self):
        metrics = {'definitely_recommend': 55.0, 'total_positive': 70.0, 'excellent': 30.0}
        for i in range(N.MIN_OWN_TESTS):
            N.record(self.s3, 'pilot', 'procedural', metrics, f'acme-{i}', company='Acme')
        self._as('a1')
        out = self.c.get('/api/digital-twins/pilot-norms', query_string={'format': 'pilot', 'genre': 'procedural'}).get_json()['norms']
        self.assertEqual(out['choice']['own_tests'], N.MIN_OWN_TESTS)
        self.assertTrue(out['choice']['has_genre_norm'])
        self.assertEqual(out['company'], 'Acme')
        self._as('b1')
        out = self.c.get('/api/digital-twins/pilot-norms', query_string={'format': 'pilot', 'genre': 'procedural'}).get_json()['norms']
        self.assertEqual(out['choice']['own_tests'], 0)
        self.assertFalse(out['choice']['has_genre_norm'])
        self.assertEqual(out['company'], 'Globex')
        self._as('jenna')
        out = self.c.get('/api/digital-twins/pilot-norms', query_string={'format': 'pilot', 'genre': 'procedural'}).get_json()['norms']
        self.assertEqual(out['choice']['own_tests'], N.MIN_OWN_TESTS)            # staff, every company
        out = self.c.get('/api/digital-twins/pilot-norms', query_string={'format': 'pilot', 'genre': 'procedural', 'company': 'Globex'}).get_json()['norms']
        self.assertEqual(out['choice']['own_tests'], 0)                          # staff looking at one company
        allf = self.c.get('/api/digital-twins/pilot-norms/all', query_string={'genre': 'procedural'}).get_json()
        self.assertEqual(allf['norms']['pilot']['choice']['own_tests'], N.MIN_OWN_TESTS)


class Norms(unittest.TestCase):
    def test_company_buckets_count_and_blend_only_the_companys_tests(self):
        s3 = FakeS3()
        N.seed(s3)
        for i in range(N.MIN_OWN_TESTS):
            N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 60.0, 'total_positive': 80.0}, f'a-{i}', company='Acme')
        N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 20.0, 'total_positive': 30.0}, 'b-0', company='Globex')
        doc = N.load(s3)
        cells = doc['cells']
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural', 'Acme'), N.MIN_OWN_TESTS)
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural', 'Globex'), 1)
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural'), N.MIN_OWN_TESTS + 1)     # the all-companies aggregate (staff)
        acme = N.norms_for('pilot', 'procedural', doc=doc, company='Acme')
        globex = N.norms_for('pilot', 'procedural', doc=doc, company='Globex')
        self.assertTrue(acme['choice']['has_genre_norm'])
        self.assertFalse(globex['choice']['has_genre_norm'])
        self.assertEqual(globex['choice']['own_tests'], 1)
        # the genre value Acme reads blends the anchor with Acme's own tests only: Globex's 20 never pulls it down
        a_val = acme['genre']['definitely_recommend']['value']
        all_val = N.norms_for('pilot', 'procedural', doc=doc)['genre']['definitely_recommend']['value']
        self.assertGreater(a_val, all_val)
        self.assertEqual(acme['genre']['definitely_recommend']['n'], N.MIN_OWN_TESTS)
        # a test recorded twice for the same company counts once; a forgotten test leaves its company bucket too
        N.record(s3, 'pilot', 'procedural', {'definitely_recommend': 60.0}, 'a-0', company='Acme')
        self.assertEqual(N.own_tests(N.load(s3)['cells'], 'pilot', 'procedural', 'Acme'), N.MIN_OWN_TESTS)
        N.forget_test(s3, 'a-0')
        cells = N.load(s3)['cells']
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural', 'Acme'), N.MIN_OWN_TESTS - 1)
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural'), N.MIN_OWN_TESTS)
        N.forget_test_genre(s3, 'b-0', 'pilot', 'procedural')
        cells = N.load(s3)['cells']
        self.assertEqual(N.own_tests(cells, 'pilot', 'procedural', 'Globex'), 0)
        self.assertEqual(N.own_tests(cells, 'pilot', 'all', 'Globex'), 1)        # the all-genres cells keep it

    def test_record_without_a_company_keeps_the_old_shape(self):
        s3 = FakeS3()
        N.record(s3, 'pilot', 'all', {'definitely_recommend': 50.0}, 't-1')
        cell = N.load(s3)['cells'][N.cell_key('pilot', 'all', 'definitely_recommend')]
        self.assertEqual(cell['our_n'], 1)
        self.assertNotIn('companies', cell)


class Chat(unittest.TestCase):
    def setUp(self):
        from prometheus.host import host
        self.s3 = FakeS3()
        _seed(self.s3)
        CO.set_users_loader(lambda: USERS)
        host.bind(s3_client=self.s3)

    def tearDown(self):
        CO.set_users_loader(None)

    def test_resolver_finds_a_twin_for_its_company_only_and_offers_the_other_a_build(self):
        from prometheus import digital_twin_lane as L
        found, runs = L.resolve_twins('Open a focus group with the Acme Show twin', {}, [], _user('a2'), limit=2)
        self.assertEqual([r['key'] for r in found], [KEY_A])
        self.assertEqual([r['key'] for r in runs], [KEY_A])
        found, runs = L.resolve_twins('Open a focus group with the Acme Show twin', {}, [], _user('b1'), limit=2)
        self.assertEqual(found, [])
        self.assertEqual([r['key'] for r in runs], [KEY_B])
        reply = L._which_twin(runs, 'open the room', 'Open a focus group with the Acme Show twin')
        self.assertIn('no Acme Show twin in your company', reply['reply'])
        self.assertIn('Build a twin of Acme Show', reply['reply'])
        self.assertIn('Build a twin of Acme Show', reply['followups'])
        self.assertNotIn(KEY_A, json.dumps(reply))
        cat = L.catalog_reply('Is there a twin of Acme Show?', _user('b1'))
        self.assertIn('No twin for Acme Show yet', cat['reply'])
        self.assertNotIn('twin_key', cat)
        cat = L.catalog_reply('Is there a twin of Acme Show?', _user('a1'))
        self.assertEqual(cat.get('twin_key'), KEY_A)
        self.assertTrue(any('Acme Show' in f for f in cat['followups']))      # the greeting-style chips name the company's own twin
        # a key off the page context from another company does not resolve
        body = {'page_context': {'view_context': {'view_id': 'digitalTwinsIQ', 'data': {'key': KEY_A}}}}
        found, _ = L.resolve_twins('what would they think of a reboot', body, [], _user('b1'), limit=2)
        self.assertNotIn(KEY_A, [r['key'] for r in found])                     # the seat's own twin may answer a vague ask, never another company's
        found, _ = L.resolve_twins('what would they think of a reboot', body, [], _user('a1'), limit=2)
        self.assertEqual([r['key'] for r in found], [KEY_A])
        # staff resolve any company's twin
        found, _ = L.resolve_twins('Open a focus group with the Globex Fans twin', {}, [], _user('jenna'), limit=2)
        self.assertEqual([r['key'] for r in found], [KEY_B])

    def test_deck_from_a_page_key_in_another_company_is_refused(self):
        from prometheus import digital_twin_lane as L
        ctx = {'view_context': {'view_id': 'digitalTwinsIQ', 'data': {'key': KEY_A}}}
        self.assertIsNone(L.deck_from_twin('build a deck of this twin', ctx, _user('b1'), 'b1', self.s3, 'dashboard-inputs'))
        got = L.deck_from_twin('build a deck of this twin', ctx, _user('a1'), 'a1', self.s3, 'dashboard-inputs')
        self.assertIsNotNone(got)


class Frontend(unittest.TestCase):
    def test_index_has_the_staff_filter_and_assign_control(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        self.assertIn('dtwCompanyFilter', html)
        self.assertIn('dtwAssignCompany', html)
        self.assertIn("api('/list' + (S.companyFilter ? '?company=' + encodeURIComponent(S.companyFilter) : ''))", html)
        self.assertIn('dtwCompanyFilterChange', html)
        self.assertIn("api('/' + key + '/company'", html)
        # nothing client-side lists the bucket or caches other companies' keys
        self.assertNotIn("'digital-twins/_index.json'", html)
        self.assertNotIn('digital-twins/_index', html)


if __name__ == '__main__':
    unittest.main(verbosity=1)
