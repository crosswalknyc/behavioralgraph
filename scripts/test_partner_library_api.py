#!/usr/bin/env python3
"""GET /api/v1/profiles/library and GET /api/v1/profiles/library/<id> on
the partner API (Jenna 2026-11-23, after a partner building an app asked
for "a list of existing profiles and their associated meta data").

Hermetic: a fake S3, no Flask, no model, no network.

Covers: the index records (window, market, audience, the US share of a
worldwide file, categories; from the nightly summary when it matches the
file, else the file); the store (compact vocabulary, drops, the cached
read, registration, the nightly refresh and its cap, the off switch, the
background fill); the list (every Total Universe and cut, the general
population baselines and anything private to one company left out, the
item fields and nothing else, the family links, the worldwide US part,
canonical categories, build dates); the filters (q case and punctuation
insensitive, category, type, updated_since, and the 400s); the pages
(limit, cursor, no repeats, filters pinned); the ETag (304 on a match,
weak and listed forms, a new tag when anything changes); the detail call
(cuts, every category, 404s); the partner belt over every string; the
routes lifted out of app.py (gate, the /v1/library rate limit and its
429, no balance preflight, 304 and 503 replies); a library id as the
prompt on check and run, on a bulk list, and on the results that point
back; the schema and the guide.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import time
import types
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PARENT = os.path.dirname(ROOT)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.pop('PARTNER_LIBRARY_INDEX', None)

from botocore.exceptions import ClientError  # noqa: E402

from migration import partner_library_index as PLI  # noqa: E402
from prometheus import partner_library as PL  # noqa: E402
from prometheus import partner_bulk as PBK  # noqa: E402
from prometheus import bulk_pull as BP  # noqa: E402
from prometheus import guards as G  # noqa: E402

APP_PY = os.path.join(ROOT, 'app.py')
_APP_CACHE = {}


def _APP_SRC():
    if 'src' not in _APP_CACHE:
        with open(APP_PY, encoding='utf-8') as fh:
            _APP_CACHE['src'] = fh.read()
    return _APP_CACHE['src']
DOC = os.path.join(PARENT, 'docs', 'PARTNER_API.md')
BANNED_FULL = tuple(G.PARTNER_BANNED_TOKENS)


# ------------------------------------------------------------------ fakes
class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


def _ce(code, op, status=None):
    resp = {'Error': {'Code': code, 'Message': 'x'}}
    if status:
        resp['ResponseMetadata'] = {'HTTPStatusCode': status}
    return ClientError(resp, op)


class FakeS3:
    """Enough of S3 for the index: ETags, NoSuchKey, conditional puts and
    reads, a root listing."""

    def __init__(self):
        self.objs = {}
        self.gets = []
        self.puts = 0

    def _etag(self, key):
        return '"' + hashlib.md5(self.objs[key]).hexdigest() + '"'

    def put_text(self, key, text):
        self.objs[key] = text.encode('utf-8')

    def get_object(self, Bucket, Key, IfNoneMatch=None, **kw):
        self.gets.append(Key)
        if Key not in self.objs:
            raise _ce('NoSuchKey', 'GetObject')
        if IfNoneMatch and IfNoneMatch == self._etag(Key):
            raise _ce('304', 'GetObject', 304)
        return {'Body': _Body(self.objs[Key]), 'ETag': self._etag(Key)}

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _ce('404', 'HeadObject')
        return {'ETag': self._etag(Key)}

    def put_object(self, Bucket, Key, Body, **kw):
        if 'IfMatch' in kw and (Key not in self.objs or kw['IfMatch'] != self._etag(Key).strip('"')):
            raise _ce('PreconditionFailed', 'PutObject', 412)
        if 'IfNoneMatch' in kw and Key in self.objs:
            raise _ce('PreconditionFailed', 'PutObject', 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.puts += 1

    def list_objects_v2(self, Bucket, Prefix='', Delimiter=None, **kw):
        keys = sorted(k for k in self.objs if k.startswith(Prefix))
        if Delimiter:
            keys = [k for k in keys if Delimiter not in k[len(Prefix):]]
        return {'Contents': [{'Key': k, 'ETag': self._etag(k)} for k in keys], 'IsTruncated': False}


HEADER = 'Column,Value,Brand Penetration (Row),Category Share,Original Raw Numbers,US Gen Pop Projection\n'


def profile_csv(subject, sample, proj, start, end, columns, market='', us_share=None, bi_proj=True):
    tag = f' | MARKET: {market}' if market else ''
    lines = [HEADER,
             f'BRAND INPUT,{subject},100.0000,,{sample},{proj if bi_proj else ""}\n',
             f'SAMPLE SIZE,SAMPLE SIZE ({start} TO {end}) | BEHAVIOR STUDY ({start} TO {end}){tag},100.0,,{sample},'
             f'{proj if bi_proj else ""}\n',
             'BRAND CATEGORY,ACTOR,,,,\n',
             f'SUBJECT,{subject},100.0000,,{sample},{proj}\n',
             'AVID FAN,Avid Fan,24.1873,,,\n']
    for c in columns:
        col = f'"{c}"' if ',' in c else c
        lines.append(f'{col},Item One,41.2287,,123,4057\n{col},Item Two,12.6543,,45,1484\n')
    if us_share is not None:
        lines.append(f'COUNTRY,United States,{us_share},,,\nCOUNTRY,United Kingdom,9.4421,,,\n')
    return ''.join(lines)


COLS = ['GENDER', 'AGE', 'ETHNICITY', 'LOCATION', 'STREAMING/PLATFORM', 'STREAMING/MUSIC', 'SOCIAL MEDIA', 'QSR',
        'MOST PURCHASED BRANDS', 'CPG', 'TALENT', 'MUSICIAN/BAND', 'NFL', 'AFC EAST', 'TRAVEL CONTENT']

TS_NEW = 'Taylor_Swift_11_01_2026_14_22.csv'
TS_OLD = 'Taylor_Swift_06_01_2026_10_00.csv'
TS_AVID = 'Taylor Swift - Avid Fan.csv'
TS_FEM = 'Taylor Swift - Female.csv'
TS_2025 = 'Taylor Swift - 2025 Total Universe.csv'
GU = 'Guinness_11_23_2026_17_38.csv'
GU_GLOBAL = 'Guinness_Global_11_23_2026_20_44.csv'
GU_GLOBAL_AVID = 'Guinness - Global - Avid Fan.csv'
GU_AVID = 'Guinness - Avid Fan.csv'
SPIDER = 'Spider_Man_Universe_08_22_2026_10_00.csv'
FRESH = 'Fresh_Show_11_23_2026_21_00.csv'
ORPHAN = 'Orphan Show - Avid Fan.csv'
PRIVATE_KEYS = ['brand-partnership-iq/acme/valuation.json', 'digital-twins/acme/room_1.json.gz',
                'generated_decks/acme/2026/deck.pptx', 'generated_data/acme/2026/read.csv', 'journey-iq/acme/run1.json',
                'system/s3_cache.json', 'Taylor_Swift_11_01_2026_14_22.pre_sort_20261101_120000.csv']

CATALOG = [
    {'s3_key': TS_NEW, 'display_name': 'Taylor Swift', 'category': 'MUSICIAN/BAND'},
    {'s3_key': TS_OLD, 'display_name': 'Taylor Swift', 'category': 'MUSICIAN/BAND'},
    {'s3_key': TS_AVID, 'display_name': 'Taylor Swift - Avid Fan', 'category': 'MUSICIAN/BAND'},
    {'s3_key': TS_FEM, 'display_name': 'TAYLOR SWIFT - Female', 'category': 'musician/band'},
    {'s3_key': TS_2025, 'display_name': 'Taylor Swift - 2025 Total Universe', 'category': 'MUSICIAN/BAND'},
    {'s3_key': GU, 'display_name': 'Guinness', 'category': 'BEVERAGE'},
    {'s3_key': GU_GLOBAL, 'display_name': 'Guinness - Global', 'category': 'BEVERAGE'},
    {'s3_key': GU_GLOBAL_AVID, 'display_name': 'Guinness - Global - Avid Fan', 'category': 'BEVERAGE'},
    {'s3_key': GU_AVID, 'display_name': 'Guinness - Avid Fan', 'category': 'BEVERAGE'},
    {'s3_key': SPIDER, 'display_name': 'Spider-Man Universe', 'category': 'MOVIES'},
    {'s3_key': FRESH, 'display_name': 'Fresh Show', 'category': 'SERIES - DRAMA'},
    {'s3_key': ORPHAN, 'display_name': 'Orphan Show - Avid Fan', 'category': 'SERIES'},
    {'s3_key': 'Gen_Pop_2026.csv', 'display_name': 'Gen Pop 2026', 'category': 'GEN POP'},
    {'s3_key': 'Gen_Pop_2026_Female.csv', 'display_name': 'Gen Pop 2026 - Female', 'category': 'TRENDS'},
] + [{'s3_key': k, 'display_name': f'Acme private {i}', 'category': 'BRAND'} for i, k in enumerate(PRIVATE_KEYS)]

PUBLIC_KEYS = [TS_NEW, TS_OLD, TS_AVID, TS_FEM, TS_2025, GU, GU_GLOBAL, GU_GLOBAL_AVID, GU_AVID, SPIDER, FRESH, ORPHAN]


def _rec(start, end, proj, cols=COLS, market='', us_share=None):
    r = {'start': start, 'end': end, 'proj': proj, 'market': market, 'columns': list(cols)}
    if us_share is not None:
        r['us_share'] = us_share
    return r


INDEX = {
    TS_NEW: _rec('2025-11-01', '2026-11-01', 48213767),
    TS_OLD: _rec('2025-06-01', '2026-06-01', 45128843),
    TS_AVID: _rec('2025-11-01', '2026-11-01', 9318457),
    TS_FEM: _rec('2025-11-01', '2026-11-01', 29871563),
    TS_2025: _rec('2025-01-01', '2025-12-31', 43987121),
    GU: _rec('2025-11-23', '2026-11-23', 5642247),
    GU_GLOBAL: _rec('2025-11-23', '2026-11-23', 17483525, market='GLOBAL', us_share=32.2717),
    GU_GLOBAL_AVID: _rec('2025-11-23', '2026-11-23', 3212389, market='GLOBAL'),
    GU_AVID: _rec('2025-11-23', '2026-11-23', 1023457),
    SPIDER: _rec('2025-08-22', '2026-08-22', 30155189,
                 cols=COLS + ['AGE | UNITED KINGDOM', 'UK - STREAMING/PLATFORM', 'SERIES - DRAMA', 'SPORTS TEAM, WNBA',
                              'NFL ATHLETE']),
    ORPHAN: _rec('2025-10-01', '2026-10-01', 211983),
}

ITEM_FIELDS = {'id', 'name', 's3_key', 'subject', 'type', 'category', 'window', 'updated_at', 'us_audience',
               'worldwide_audience', 'sections', 'cuts', 'parent_id'}
# The file name is the one file field (s3_key, 2026-11-23); every other
# internal word stays out of a response.
INTERNAL_WORDS = ('"_', 'sample', '"etag"', '"proj"', '"columns"', '"market"', 'us_share', 'dashboard-inputs',
                  'system/', 's3://')


def _lst(params=None, inm=None, index=None, catalog=None):
    return PL.list_route(params or {}, inm, CATALOG if catalog is None else catalog,
                         INDEX if index is None else index)


def _by_name(body):
    return {p['name']: p for p in body['profiles']}


def _all(params=None, index=None):
    p = dict(params or {})
    p['limit'] = '200'
    body, status, _h = _lst(p, index=index)
    assert status == 200, body
    return body


class _Base(unittest.TestCase):
    def setUp(self):
        PL.reset_cache()
        PLI.set_client(None)
        self._fills = []
        self._orig_fill = PLI.fill_async
        PLI.fill_async = lambda keys, **kw: self._fills.append(list(keys)) or True

    def tearDown(self):
        PLI.fill_async = self._orig_fill
        PLI.set_client(None)
        PL.reset_cache()


# ------------------------------------------------------------------ the index records
class Records(unittest.TestCase):
    def test_record_from_rows(self):
        import csv
        import io
        text = profile_csv('Guinness', 291279, 17483525, '2025-11-23', '2026-11-23', ['GENDER', 'QSR', 'SPORTS TEAM, WNBA'],
                           market='GLOBAL', us_share=32.2717)
        rec = PLI.record_from_rows(csv.DictReader(io.StringIO(text)))
        self.assertEqual((rec['start'], rec['end'], rec['proj'], rec['market']),
                         ('2025-11-23', '2026-11-23', 17483525, 'GLOBAL'))
        self.assertEqual(rec['us_share'], 32.2717)
        self.assertEqual(rec['columns'], ['GENDER', 'QSR', 'SPORTS TEAM, WNBA', 'COUNTRY'])
        for meta in ('BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'AVID FAN'):
            self.assertNotIn(meta, rec['columns'])
        self.assertNotIn('sample', rec)

    def test_no_projection_cell_uses_the_chain(self):
        import csv
        import io
        text = profile_csv('Old File', 10001, 0, '2024-07-01', '2025-06-30', ['AGE'], bi_proj=False)
        rec = PLI.record_from_rows(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rec['proj'], int(round(10001 * 32.99)))
        self.assertEqual(rec['market'], '')

    def test_windows_and_markets(self):
        self.assertEqual(PLI.parse_window('SAMPLE SIZE (2025-11-23 TO 2026-11-23) | x'), ('2025-11-23', '2026-11-23'))
        self.assertEqual(PLI.parse_window('07/01/2025 - 06/30/2026'), ('2025-07-01', '2026-06-30'))
        self.assertEqual(PLI.parse_window('2026-11-23 TO 2025-11-23'), (None, None))
        self.assertEqual(PLI.parse_window('no dates here'), (None, None))
        self.assertEqual(PLI.parse_market('SAMPLE SIZE (x) | MARKET: GLOBAL'), 'GLOBAL')
        self.assertEqual(PLI.parse_market('SAMPLE SIZE (x) | MARKET: US'), '')
        self.assertEqual(PLI.parse_market('SAMPLE SIZE (x)'), '')

    def test_record_from_summary(self):
        doc = {'etag': 'abc', 'meta': {'window': '2025-11-20 to 2026-11-20', 'proj': 1589194, 'sample': 48172},
               'demos': {'GENDER': [], 'PARENTAL STATUS': []},
               'categories': {'AVID FAN': {}, 'QSR': {}, 'COUNTRY': {'rows': [['United States', 31.8811, None, 1, 2]]}}}
        rec = PLI.record_from_summary(doc)
        self.assertEqual((rec['start'], rec['end'], rec['proj']), ('2025-11-20', '2026-11-20', 1589194))
        self.assertEqual(rec['columns'], ['GENDER', 'PARENTAL STATUS', 'QSR', 'COUNTRY'])
        self.assertEqual(rec['us_share'], 31.8811)

    def test_summary_key_matches_the_nightly_job(self):
        key = 'Reba_McEntire_05_13_2026_10_00.csv'
        h = hashlib.sha1(key.encode('utf-8')).hexdigest()[:24]
        self.assertEqual(PLI.summary_key(key), f'system/prometheus_profile_indexes/{h}.json')
        src = _read(os.path.join(ROOT, 'prometheus_analysis.py'))
        self.assertIn("hashlib.sha1(str(s3_key).encode('utf-8')).hexdigest()[:24]", src)

    def test_profile_id(self):
        a, b = PLI.profile_id(TS_NEW), PLI.profile_id(TS_OLD)
        self.assertRegex(a, r'^pf_[0-9a-f]{20}$')
        self.assertEqual(a, PLI.profile_id(TS_NEW))
        self.assertNotEqual(a, b)
        self.assertNotIn('taylor', a.lower())
        self.assertEqual(PLI.profile_id(''), '')


# ------------------------------------------------------------------ the store
class Store(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        PLI.set_client(self.s3)
        for key in (TS_NEW, TS_AVID, 'Hidden_Show_10_01_2026_10_00.csv', FRESH):
            self.s3.put_text(key, profile_csv(key[:8], 291279, 9609294, '2025-11-01', '2026-11-01', ['GENDER', 'QSR']))
        self.s3.put_text('system/s3_cache.json', json.dumps({'jobs': [
            {'s3_key': TS_NEW}, {'s3_key': TS_AVID}, {'s3_key': 'Hidden_Show_10_01_2026_10_00.csv'},
            {'s3_key': FRESH}, {'s3_key': 'brand-partnership-iq/acme/valuation.json'}]}))
        self.s3.put_text('metadata/admin_quick_selects.json', json.dumps({'profiles': {
            'Hidden_Show_10_01_2026_10_00.csv': False, TS_NEW: True}}))

    def tearDown(self):
        PLI.set_client(None)

    def _summary(self, key, etag, proj=1111111):
        self.s3.put_text(PLI.summary_key(key), json.dumps({
            's3_key': key, 'etag': etag, 'meta': {'window': '2025-11-01 to 2026-11-01', 'proj': proj},
            'demos': {'GENDER': []}, 'categories': {'TRAVEL': {}}}))

    def test_build_record_uses_a_matching_summary_else_the_file(self):
        etag = self.s3._etag(TS_NEW).strip('"')
        self._summary(TS_NEW, etag)
        self.s3.gets.clear()
        rec, src = PLI.build_record(TS_NEW, etag, s3=self.s3)
        self.assertEqual(src, 'summary')
        self.assertNotIn(TS_NEW, self.s3.gets)
        self.assertEqual(rec['proj'], 1111111)
        self._summary(TS_NEW, 'stale-etag')
        rec, src = PLI.build_record(TS_NEW, etag, s3=self.s3)
        self.assertEqual(src, 'file')
        self.assertEqual(rec['proj'], 9609294)
        self.assertEqual(rec['columns'], ['GENDER', 'QSR'])

    def test_upsert_compacts_and_drops(self):
        PLI.upsert({TS_NEW: {'start': '2025-11-01', 'end': '2026-11-01', 'proj': 7, 'columns': ['GENDER', 'QSR'], 'etag': 'e1'},
                    TS_AVID: {'start': '2025-11-01', 'end': '2026-11-01', 'proj': 3, 'columns': ['QSR', 'TRAVEL'], 'etag': 'e2'}})
        doc = json.loads(self.s3.objs[PLI.INDEX_KEY])
        self.assertEqual(doc['columns'], ['GENDER', 'QSR', 'TRAVEL'])
        self.assertEqual(doc['profiles'][TS_AVID]['cols'], [1, 2])
        self.assertNotIn('columns', doc['profiles'][TS_NEW])
        PLI.upsert({}, drop=[TS_AVID])
        doc = json.loads(self.s3.objs[PLI.INDEX_KEY])
        self.assertEqual(sorted(doc['profiles']), [TS_NEW])
        recs, ver = PLI.load_index(force=True)
        self.assertEqual(recs[TS_NEW]['columns'], ['GENDER', 'QSR'])
        self.assertTrue(ver)

    def test_load_index_is_cached_and_revalidates(self):
        PLI.upsert({TS_NEW: {'proj': 7, 'columns': ['GENDER'], 'etag': 'e1'}})
        recs, ver = PLI.load_index(force=True)
        recs2, ver2 = PLI.load_index(force=True)        # conditional read answers 304: the cache stands
        self.assertEqual((recs2, ver2), (recs, ver))
        PLI.upsert({TS_AVID: {'proj': 3, 'columns': ['QSR'], 'etag': 'e2'}})
        recs3, ver3 = PLI.load_index(force=True)
        self.assertIn(TS_AVID, recs3)
        self.assertNotEqual(ver3, ver)

    def test_index_profiles_and_the_registration_hook(self):
        self.assertEqual(PLI.index_profiles([TS_AVID]), 1)
        recs, _v = PLI.load_index(force=True)
        self.assertEqual(recs[TS_AVID]['proj'], 9609294)
        src = _read(os.path.join(ROOT, 'migration', 'dashboard_register.py'))
        self.assertIn('_pli.index_profiles', src)
        self.assertIn('kwargs={"s3": s3_client}', src)

    def test_refresh_reads_only_changed_files_and_drops_the_rest(self):
        PLI.upsert({'Gone_File_01_01_2026_10_00.csv': {'proj': 5, 'columns': ['AGE'], 'etag': 'x'}})
        etag_new = self.s3._etag(TS_NEW).strip('"')
        self._summary(TS_NEW, etag_new, proj=2222222)
        stats = PLI.refresh(s3=self.s3, workers=2, verbose=False)
        self.assertEqual(stats['listed'], 3)                # hidden and non-root files are not listed
        self.assertEqual((stats['from_summary'], stats['from_file'], stats['dropped']), (1, 2, 1))
        recs, _v = PLI.load_index(force=True)
        self.assertEqual(sorted(recs), sorted([TS_NEW, TS_AVID, FRESH]))
        self.assertEqual(recs[TS_NEW]['proj'], 2222222)
        stats = PLI.refresh(s3=self.s3, workers=2, verbose=False)
        self.assertEqual(stats['changed'], 0)               # nothing re-read when the bytes did not change
        self.s3.put_text(TS_AVID, profile_csv('Avid', 30001, 989731, '2025-11-01', '2026-11-01', ['AGE']))
        stats = PLI.refresh(s3=self.s3, workers=2, verbose=False, max_file_reads=0)
        self.assertEqual((stats['changed'], stats['waiting']), (1, 1))   # the cap holds it for the next run
        stats = PLI.refresh(s3=self.s3, workers=2, verbose=False)
        recs, _v = PLI.load_index(force=True)
        self.assertEqual(recs[TS_AVID]['proj'], 989731)

    def test_off_in_the_harness_without_a_client(self):
        PLI.set_client(None)
        self.assertFalse(PLI.enabled())
        self.assertEqual(PLI.index_profiles([TS_NEW]), 0)
        self.assertEqual(PLI.load_index(force=True), ({}, ''))
        self.assertEqual(PLI.refresh(verbose=False)['error'], 'off')
        self.assertFalse(PLI.fill_async([TS_NEW]))

    def test_fill_runs_once_in_the_background_and_cools_down(self):
        os.environ.pop('PARTNER_LIBRARY_FILL', None)
        self.assertTrue(PLI.fill_async([FRESH]))
        t0 = time.time()
        while PLI._FILL['running'] and time.time() - t0 < 10:
            time.sleep(0.02)
        recs, _v = PLI.load_index(force=True)
        self.assertIn(FRESH, recs)
        self.assertFalse(PLI.fill_async([FRESH]))           # tried in the last ten minutes
        os.environ['PARTNER_LIBRARY_FILL'] = '0'
        try:
            self.assertFalse(PLI.fill_async([TS_AVID]))
        finally:
            os.environ.pop('PARTNER_LIBRARY_FILL', None)


# ------------------------------------------------------------------ the list
class TheList(_Base):
    def test_every_profile_and_nothing_else(self):
        body = _all()
        self.assertEqual(body['total'], len(PUBLIC_KEYS))
        self.assertEqual(body['count'], len(PUBLIC_KEYS))
        self.assertIsNone(body['next_cursor'])
        ids = {p['id'] for p in body['profiles']}
        self.assertEqual(ids, {PLI.profile_id(k) for k in PUBLIC_KEYS})
        blob = json.dumps(body)
        self.assertNotIn('Gen Pop', blob)
        self.assertNotIn('Acme', blob)

    def test_order_subject_then_total_universe_first(self):
        names = [p['name'] for p in _all()['profiles']]
        self.assertEqual(names[:2], ['Fresh Show', 'Guinness'])
        ts = [n for n in names if n.lower().startswith('taylor')]
        self.assertEqual(ts[:2], ['Taylor Swift', 'Taylor Swift'])

    def test_item_fields_only(self):
        for p in _all()['profiles']:
            self.assertEqual(set(p), ITEM_FIELDS)
            self.assertRegex(p['id'], r'^pf_[0-9a-f]{20}$')
            self.assertEqual(set(p['window']), {'start', 'end', 'label'})
            for c in p['cuts']:
                self.assertEqual(set(c), {'id', 'label'})
        body = _all()
        blob = json.dumps(body)
        for word in INTERNAL_WORDS:
            self.assertNotIn(word, blob, word)
        for p in body['profiles']:                      # a file name shows only as the file name
            blob = blob.replace(json.dumps(p['s3_key']), '""')
        self.assertNotIn('.csv', blob)

    def test_family_links_types_and_dates(self):
        body = _all()
        ts = [p for p in body['profiles'] if p['name'] == 'Taylor Swift']
        new = next(p for p in ts if p['id'] == PLI.profile_id(TS_NEW))
        old = next(p for p in ts if p['id'] == PLI.profile_id(TS_OLD))
        self.assertEqual(new['type'], 'Total Universe')
        self.assertEqual([c['label'] for c in new['cuts']], ['2025 Total Universe', 'Avid Fan', 'Female'])
        self.assertEqual(old['cuts'], [])
        self.assertIsNone(new['parent_id'])
        self.assertEqual(new['updated_at'], '2026-11-01T14:22:00Z')
        self.assertEqual(new['window'], {'start': '2025-11-01', 'end': '2026-11-01', 'label': 'Nov 1, 2025 to Nov 1, 2026'})
        by = _by_name(body)
        avid, fem, y25 = by['Taylor Swift - Avid Fan'], by['TAYLOR SWIFT - Female'], by['Taylor Swift - 2025 Total Universe']
        self.assertEqual((avid['type'], fem['type'], y25['type']), ('Avid Fan', 'Female', '2025 Total Universe'))
        for cut in (avid, fem, y25):
            self.assertEqual(cut['parent_id'], new['id'])
            self.assertEqual(cut['subject'], 'Taylor Swift')
            self.assertEqual(cut['cuts'], [])
        self.assertEqual(avid['updated_at'], '2026-11-01T14:22:00Z')     # built with its parent
        self.assertEqual(y25['updated_at'], '2025-12-31T00:00:00Z')      # the end of the window it covers
        self.assertEqual(y25['window']['label'], 'Jan 1, 2025 to Dec 31, 2025')
        orphan = by['Orphan Show - Avid Fan']
        self.assertIsNone(orphan['parent_id'])
        self.assertEqual(orphan['subject'], 'Orphan Show')

    def test_audiences_and_the_worldwide_us_part(self):
        by = _by_name(_all())
        self.assertEqual((by['Guinness']['us_audience'], by['Guinness']['worldwide_audience']), (5642247, None))
        g = by['Guinness - Global']
        self.assertEqual(g['type'], 'Global')
        self.assertEqual(g['worldwide_audience'], 17483525)
        self.assertEqual(g['us_audience'], int(round(17483525 * 32.2717 / 100)))
        ga = by['Guinness - Global - Avid Fan']
        self.assertEqual((ga['us_audience'], ga['worldwide_audience']), (1023457, 3212389))
        self.assertEqual(by['Taylor Swift - Avid Fan']['us_audience'], 9318457)

    def test_categories_are_canonical(self):
        by = _by_name(_all())
        self.assertEqual(by['TAYLOR SWIFT - Female']['category'], 'MUSICIAN/BAND')
        self.assertEqual(by['Spider-Man Universe']['category'], 'MOVIE')
        self.assertEqual(by['Fresh Show']['category'], 'SERIES - DRAMA')

    def test_sections_in_plain_words(self):
        by = _by_name(_all())
        self.assertEqual(by['Taylor Swift - Avid Fan']['sections'],
                         ['demographics', 'locations', 'streaming', 'music streaming', 'social media', 'talent',
                          'creators by topic', 'sports', 'brands', 'quick service restaurants'])

    def test_a_file_the_index_has_not_seen_lists_and_is_filled(self):
        p = _by_name(_all())['Fresh Show']
        self.assertEqual(p['window'], {'start': None, 'end': None, 'label': None})
        self.assertIsNone(p['us_audience'])
        self.assertEqual(p['sections'], [])
        self.assertEqual(p['updated_at'], '2026-11-23T21:00:00Z')
        self.assertTrue(any(FRESH in keys for keys in self._fills))


class Filters(_Base):
    def _names(self, **params):
        body, status, _h = _lst(dict(params, limit='200'))
        self.assertEqual(status, 200, body)
        return [p['name'] for p in body['profiles']]

    def test_q_ignores_case_and_punctuation(self):
        for q in ('spider man', 'SPIDER-MAN', 'spiderman', 'Spider-Man Universe'):
            self.assertEqual(self._names(q=q), ['Spider-Man Universe'], q)
        self.assertEqual(self._names(q='taylor avid'), ['Taylor Swift - Avid Fan'])
        self.assertEqual(self._names(q='swift'), ['Taylor Swift', 'Taylor Swift', 'Taylor Swift - 2025 Total Universe',
                                                  'Taylor Swift - Avid Fan', 'TAYLOR SWIFT - Female'])
        self.assertEqual(self._names(q='nothing like this'), [])

    def test_category(self):
        for c in ('BEVERAGE', 'beverage', 'Beverage '):
            self.assertEqual(len(self._names(category=c)), 4, c)
        self.assertEqual(len(self._names(category='musician band')), 5)
        self.assertEqual(self._names(category='movies'), ['Spider-Man Universe'])

    def test_type(self):
        self.assertEqual(len(self._names(type='total universe')), 5)
        self.assertEqual(sorted(self._names(type='Avid-Fan')), ['Guinness - Avid Fan', 'Orphan Show - Avid Fan',
                                                               'Taylor Swift - Avid Fan'])
        self.assertEqual(self._names(type='Global'), ['Guinness - Global'])
        self.assertEqual(self._names(type='2025 Total Universe'), ['Taylor Swift - 2025 Total Universe'])
        self.assertEqual(len(self._names(type='cut')), 7)

    def test_updated_since(self):
        got = self._names(updated_since='2026-11-02')
        self.assertEqual(sorted(got), ['Fresh Show', 'Guinness', 'Guinness - Avid Fan', 'Guinness - Global',
                                       'Guinness - Global - Avid Fan'])
        got = self._names(updated_since='2026-11-01T10:22:00-04:00')
        self.assertIn('Taylor Swift - Avid Fan', got)
        got = self._names(updated_since='2026-11-01T14:23:00Z')
        self.assertNotIn('Taylor Swift - Avid Fan', got)

    def test_bad_requests_say_why(self):
        cases = [({'updated_since': 'last week'}, '"updated_since"'), ({'limit': '0'}, '"limit"'),
                 ({'limit': '201'}, '"limit"'), ({'limit': 'ten'}, '"limit"'), ({'catgory': 'ACTOR'}, '"catgory"'),
                 ({'q': 'x' * 201}, '"q"'), ({'cursor': 'not-a-cursor'}, '"cursor"')]
        for params, needle in cases:
            body, status, headers = _lst(params)
            self.assertEqual(status, 400, params)
            self.assertFalse(body['success'])
            self.assertIn(needle, body['error'], params)
            self.assertEqual(headers, {})


class Pages(_Base):
    def test_walk_every_page(self):
        seen, cursor, totals = [], None, set()
        for _ in range(10):
            params = {'limit': '5'}
            if cursor:
                params['cursor'] = cursor
            body, status, _h = _lst(params)
            self.assertEqual(status, 200, body)
            totals.add(body['total'])
            seen += [p['id'] for p in body['profiles']]
            cursor = body['next_cursor']
            if not cursor:
                break
        self.assertEqual(seen, [p['id'] for p in _all()['profiles']])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(totals, {len(PUBLIC_KEYS)})

    def test_a_cursor_keeps_its_filters(self):
        body, _s, _h = _lst({'limit': '2', 'q': 'swift'})
        self.assertTrue(body['next_cursor'])
        nxt, status, _h = _lst({'limit': '2', 'q': 'swift', 'cursor': body['next_cursor']})
        self.assertEqual(status, 200)
        self.assertEqual(nxt['profiles'][0]['type'], '2025 Total Universe')
        bad, status, _h = _lst({'limit': '2', 'q': 'guinness', 'cursor': body['next_cursor']})
        self.assertEqual(status, 400)
        self.assertIn('"cursor"', bad['error'])
        ok, status, _h = _lst({'limit': '3', 'q': 'swift', 'cursor': body['next_cursor']})   # a new page size is fine
        self.assertEqual(status, 200)


class ETags(_Base):
    def test_304_on_a_match(self):
        body, status, headers = _lst({'q': 'swift'})
        tag = headers['ETag']
        self.assertRegex(tag, r'^"[0-9a-f]{32}"$')
        self.assertEqual(headers['Cache-Control'], 'private, no-cache')
        _b, _s, h2 = _lst({'q': 'swift'})
        self.assertEqual(h2['ETag'], tag)
        for inm in (tag, 'W/' + tag, '"nope", ' + tag, '*'):
            b, s, h = _lst({'q': 'swift'}, inm=inm)
            self.assertEqual((b, s), (None, 304), inm)
            self.assertEqual(h['ETag'], tag)
        b, s, _h = _lst({'q': 'swift'}, inm='"nope"')
        self.assertEqual(s, 200)

    def test_any_change_is_a_new_tag(self):
        _b, _s, h = _lst({'q': 'swift'})
        changed = dict(INDEX)
        changed[TS_AVID] = dict(INDEX[TS_AVID], proj=9318459)
        body, status, h2 = _lst({'q': 'swift'}, inm=h['ETag'], index=changed)
        self.assertEqual(status, 200)
        self.assertNotEqual(h2['ETag'], h['ETag'])
        self.assertEqual(_by_name(body)['Taylor Swift - Avid Fan']['us_audience'], 9318459)


class Detail(_Base):
    def _get(self, pid, params=None, inm=None):
        return PL.detail_route(pid, params or {}, inm, CATALOG, INDEX)

    def test_a_total_universe_and_its_cuts(self):
        body, status, headers = self._get(PLI.profile_id(TS_NEW))
        self.assertEqual(status, 200, body)
        prof = body['profile']
        self.assertEqual(set(prof), ITEM_FIELDS | {'section_details'})
        self.assertEqual([c['type'] for c in body['cuts']], ['2025 Total Universe', 'Avid Fan', 'Female'])
        for c in body['cuts']:
            self.assertEqual(set(c), ITEM_FIELDS | {'section_details'})
        details = {d['section']: d['categories'] for d in prof['section_details']}
        self.assertEqual(details['demographics'], ['gender', 'age', 'ethnicity'])
        self.assertEqual(details['sports'], ['NFL teams', 'AFC East teams'])
        self.assertEqual(details['brands'], ['brands they buy', 'packaged goods'])
        self.assertEqual(details['creators by topic'], ['travel creators'])
        self.assertEqual([d['section'] for d in prof['section_details']], prof['sections'])
        b, s, _h = self._get(PLI.profile_id(TS_NEW), inm=headers['ETag'])
        self.assertEqual((b, s), (None, 304))

    def test_a_cut_has_no_cuts(self):
        body, status, _h = self._get(PLI.profile_id(TS_AVID))
        self.assertEqual(status, 200)
        self.assertEqual(body['cuts'], [])
        self.assertEqual(body['profile']['parent_id'], PLI.profile_id(TS_NEW))

    def test_not_found(self):
        for pid in (PLI.profile_id('Never_Built_01_01_2026_10_00.csv'), 'pf_xyz', TS_NEW, '../system/s3_cache.json',
                    PLI.profile_id('Gen_Pop_2026.csv')):
            body, status, _h = self._get(pid)
            self.assertEqual((status, body), (404, {'success': False, 'error': 'profile not found'}), pid)
        body, status, _h = self._get(PLI.profile_id(TS_NEW), {'expand': '1'})
        self.assertEqual(status, 400)
        self.assertIn('"expand"', body['error'])


class CompanyPrivate(_Base):
    def test_private_work_never_lists(self):
        blob = json.dumps(_all()) + json.dumps(PL.detail_route(PLI.profile_id(TS_NEW), {}, None, CATALOG, INDEX)[0])
        for i, key in enumerate(PRIVATE_KEYS):
            self.assertNotIn(f'Acme private {i}', blob)
            body, status, _h = PL.detail_route(PLI.profile_id(key), {}, None, CATALOG, INDEX)
            self.assertEqual(status, 404, key)
        self.assertNotIn('Gen Pop', blob)

    def test_the_routes_read_the_dashboards_own_list(self):
        src = _APP_SRC()
        for fn in ('api_v1_profiles_library', 'api_v1_profiles_library_item'):
            seg = _fn_src(src, fn)
            self.assertIn('_profile_catalog_for_chat()', seg)     # admin-hidden files are already out of it
        lm = _read(os.path.join(ROOT, 'prometheus', 'library_match.py'))
        self.assertIn('hidden = _H._admin_hidden_profile_keys()', lm)


class FileName(_Base):
    """Each profile carries its file name, the value /check returns as
    existing_match_s3_key (the client's ask, approved 2026-11-23)."""

    def test_every_item_and_cut_carries_its_own_file_name(self):
        by_id = {PLI.profile_id(k): k for k in PUBLIC_KEYS}
        for p in _all()['profiles']:
            self.assertEqual(p['s3_key'], by_id[p['id']])
        body, status, _h = PL.detail_route(PLI.profile_id(TS_NEW), {}, None, CATALOG, INDEX)
        self.assertEqual(status, 200)
        self.assertEqual(body['profile']['s3_key'], TS_NEW)
        self.assertEqual([c['s3_key'] for c in body['cuts']], [TS_2025, TS_AVID, TS_FEM])

    def test_never_a_folder_a_bucket_or_another_file_field(self):
        for p in _all()['profiles']:
            k = p['s3_key']
            self.assertNotIn('/', k)
            self.assertFalse(k.lower().startswith(('dashboard-inputs', 's3:')), k)
            self.assertTrue(k.lower().endswith('.csv'), k)
        det = PL.detail_route(PLI.profile_id(GU), {}, None, CATALOG, INDEX)[0]
        for c in [det['profile']] + det['cuts']:
            self.assertEqual(set(c), ITEM_FIELDS | {'section_details'})

    def test_q_finds_a_file_name(self):
        for q, want in (('Taylor_Swift_11_01_2026_14_22.csv', [TS_NEW]),
                        ('Guinness - Global - Avid Fan.csv', [GU_GLOBAL_AVID]),
                        ('06_01_2026', [TS_OLD])):
            body, status, _h = _lst({'q': q, 'limit': '200'})
            self.assertEqual(status, 200, q)
            self.assertEqual([p['s3_key'] for p in body['profiles']], want, q)

    def test_the_belt_never_strips_it(self):
        tu, cut = 'Synth_Pop_Fans_11_01_2026_10_00.csv', 'Synth Pop Fans - Avid Fan.csv'
        self.assertEqual(G.scrub_partner_text(cut, fallback=''), '')        # the belt alone would blank this one
        catalog = CATALOG + [{'s3_key': tu, 'display_name': 'Synth Pop Fans', 'category': 'MUSICIAN/BAND'},
                             {'s3_key': cut, 'display_name': 'Synth Pop Fans - Avid Fan', 'category': 'MUSICIAN/BAND'}]
        index = dict(INDEX, **{k: _rec('2025-11-01', '2026-11-01', 2318467) for k in (tu, cut)})
        orig_house, orig_lib = G._PARTNER_SKIP_KEYS, PL.SKIP_KEYS
        no_house = frozenset(k for k in orig_house if k != 's3_key')

        def _keys(house, lib):
            G._PARTNER_SKIP_KEYS, PL.SKIP_KEYS = house, lib
            try:
                PL.reset_cache()
                body, status, _h = _lst({'q': 'synth pop', 'limit': '200'}, catalog=catalog, index=index)
            finally:
                G._PARTNER_SKIP_KEYS, PL.SKIP_KEYS = orig_house, orig_lib
                PL.reset_cache()
            self.assertEqual(status, 200)
            return body, sorted(p['s3_key'] for p in body['profiles'])

        for house in (orig_house, no_house):                                  # with or without the house list's entry
            body, keys = _keys(house, orig_lib)
            self.assertEqual(keys, [cut, tu])
            self.assertEqual(PL.violations(body), [])
        _body, keys = _keys(no_house, frozenset(orig_lib - {'s3_key'}))      # the library's own entry is what holds it
        self.assertEqual(keys, ['', tu])

    def test_the_allowlist_is_narrow(self):
        self.assertEqual(PL.SKIP_KEYS, frozenset({'id', 'parent_id', 'name', 's3_key', 'subject', 'type', 'label',
                                                  'updated_at', 'next_cursor', 'window', 'start', 'end'}))


class Belt(_Base):
    def test_responses_carry_no_banned_word(self):
        body = _all()
        self.assertEqual(PL.violations(body), [])
        det = PL.detail_route(PLI.profile_id(SPIDER), {}, None, CATALOG, INDEX)[0]
        self.assertEqual(PL.violations(det), [])
        for path, s in _strings(body):
            if path.split('.')[-1].split('[')[0] in ('name', 'subject', 'type', 'label'):
                continue
            low = s.lower()
            for tok in BANNED_FULL:
                self.assertNotIn(tok, low, (path, s))

    def test_internal_words_in_a_file_never_reach_a_key(self):
        index = dict(INDEX)
        index[SPIDER] = dict(INDEX[SPIDER], columns=['GENDER', 'HOSTMAP GAP', 'SYNTH SCORES', 'PANEL WEIGHTS'])
        catalog = [dict(e, category='SYNTH TEST') if e['s3_key'] == SPIDER else e for e in CATALOG]
        det, status, _h = PL.detail_route(PLI.profile_id(SPIDER), {}, None, catalog, index)
        self.assertEqual(status, 200)
        prof = det['profile']
        self.assertEqual(prof['sections'], ['demographics'])
        self.assertEqual(prof['section_details'], [{'section': 'demographics', 'categories': ['gender']}])
        self.assertNotIn('synth', prof['category'].lower())
        self.assertEqual(PL.violations(det), [])

    def test_every_plain_word_passes_the_belt_unchanged(self):
        words = set(PL.SECTION_ORDER) | {plain for _sec, plain in PL._T.values()}
        for w in sorted(words):
            self.assertEqual(G.scrub_partner_text(w, fallback=''), w, w)
            self.assertEqual(G.partner_violations(w), [], w)


GENPOP_COLUMNS = [
    'ACCESSORIES', 'ACTOR', 'AFC', 'AFC EAST', 'AFC NORTH', 'AFC SOUTH', 'AFC WEST', 'AGE', 'AGE_OF_CHILDREN', 'AL',
    'AL CENTRAL', 'AL EAST', 'AL WEST', 'AMUSEMENT PARKS', 'APP/PLATFORM USAGE', 'APPAREL/FOOTWEAR', 'ART CONTENT',
    'ATHLETE', 'ATLANTIC DIVISION', 'AUSL', 'AUTOMOBILE', 'BANKING', 'BEAUTY CONTENT', 'BEAUTY/WELLNESS', 'BETTING',
    'BEVERAGE', 'BROADCAST/CABLE', 'BUSINESS/FINANCE CONTENT', 'BUSINESS/TECH LEADERS', 'CENTRAL DIVISION',
    'COLLEGE/UNIVERSITY', 'CPG', 'CREATOR/INFLUENCER', 'CREDIT PROVIDER', 'DIGITAL BANKING', 'EASTERN CONFERENCE',
    'EDUCATION', 'EDUCATION & LEARNING', 'EDUCATION/HOW-TO CONTENT', 'ENTERTAINMENT CONTENT', 'ETHNICITY', 'EVENTS',
    'FAITH/SPIRITUALITY CONTENT', 'FAMILY CONTENT', 'FASHION CONTENT', 'FITNESS/WELLNESS CONTENT', 'FOOD CONTENT',
    'FRANCHISE', 'GAMES', 'GAMING CONTENT', 'GENDER', 'GOLF', 'HEALTH & WELLNESS', 'HEAVY MACHINERY', 'HOME/DIY CONTENT',
    'HOME/OUTDOOR', 'HORSE RACING', 'HOST/PERSONALITY', 'INCOME', 'INSURANCE', 'INTEREST', 'INVESTMENTS', 'LA LIGA',
    'LIFESTYLE CONTENT', 'LOCATION', 'MEDIA', 'METROPOLITAN DIVISION', 'MLB', 'MLB ATHLETE', 'MLS',
    'MOST PURCHASED BRANDS', 'MOST PURCHASED CATEGORIES', 'MOVIE THEATER', 'MUSIC/DANCE CONTENT', 'MUSICIAN/BAND',
    'NBA', 'NBA ATHLETE', 'NEWS/POLITICS CONTENT', 'NFC', 'NFC EAST', 'NFC NORTH', 'NFC SOUTH', 'NFC WEST', 'NFL',
    'NFL ATHLETE', 'NHL', 'NHL ATHLETE', 'NON PROFIT/CHARITY', 'NUMBER_OF_CHILDREN', 'NWSL', 'OCCUPATION',
    'ORGANIZATIONAL MEMBERSHIPS', 'PACIFIC DIVISION', 'PARENTAL_STATUS', 'PETS', 'PHARMACY', 'PODCAST',
    'PODCAST RANKER', 'POLITICS/ACTIVIST', 'PORN MEDIA', 'PREMIER LEAGUE', 'PRIMARY_LANGUAGE', 'QSR', 'RELATIONSHIP',
    'RUGBY', 'SEARCH ENGINE/AI', 'SERIE A', 'SEXUAL_ORIENTATION', 'SOCCER', 'SOCCER ATHLETE', 'SOCIAL MEDIA',
    'SPORTS CONTENT', 'SPORTS ORGANIZATIONS', 'SPORTS TEAM', 'STREAMING/MUSIC', 'STREAMING/PLATFORM', 'TALENT',
    'TECH CONTENT', 'TECHNOLOGY BRAND', 'TECHNOLOGY/DEVICE', 'TELECOM', 'TENNIS', 'TICKETING', 'TOYS', 'TRADING',
    'TRAVEL', 'TRAVEL CONTENT', 'VENUE', 'VIRTUAL MVPD FAST', 'VOLLEYBALL', 'WHERE THEY DINE', 'WHERE THEY SHOP', 'WNBA',
    'WNBA ATHLETE', 'WORKOUT FACILITY', 'WRITER/DIRECTOR/AUTHOR/ARTIST',
]


class Sections(unittest.TestCase):
    def test_every_base_category_has_a_plain_home(self):
        for col in GENPOP_COLUMNS:
            hit = PL.section_of(col)
            self.assertIsNotNone(hit, col)
            self.assertNotEqual(hit[0], 'other', col)
            self.assertIn(hit[0], PL.SECTION_ORDER, col)
        for meta in ('BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'AVID FAN', 'INPUT_METADATA'):
            self.assertIsNone(PL.section_of(meta), meta)

    def test_the_odd_shapes(self):
        cases = {
            'AGE | UNITED KINGDOM': ('demographics', 'age (United Kingdom)'),
            'UK - STREAMING/PLATFORM': ('streaming', 'streaming services (United Kingdom)'),
            'SERIES - DRAMA': ('series and movies', 'drama series'),
            'SPORTS TEAM, WNBA': ('sports', 'WNBA teams'),
            'NL WEST': ('sports', 'NL West teams'),
            'WESTERN CONFERENCE': ('sports', 'Western Conference teams'),
            'NFL ATHLETE': ('talent', 'NFL athletes'),
            'HOME/DIY CONTENT': ('creators by topic', 'home and DIY creators'),
            'CASUAL DINING': ('restaurants', 'casual dining'),
            'QSR': ('quick service restaurants', 'quick service restaurants'),
            'GENERAL': ('other', 'general'),
        }
        for col, want in cases.items():
            self.assertEqual(PL.section_of(col), want, col)

    def test_split_name_matches_the_dashboard(self):
        src = _APP_SRC()
        ns = {'re': re}
        exec(compile('\n\n'.join(_extract_src(src, ['_FAM_DASH_SUFFIX_RE', '_FAM_TOKEN_SUFFIX_RE', '_FAM_BASE_UNIVERSE_RE',
                                                     '_fam_norm_ws', '_fam_suffix_info'])), APP_PY, 'exec'), ns)
        for name in ('Taylor Swift', 'Taylor Swift - Avid Fan', 'Reba McEntire - 2025 Total Universe',
                     'Spectrum Switchers - Total Universe', 'Show - Total Universe 2025', 'Guinness - Global - Avid Fan',
                     'Taylor Swift Avid', 'Gen Pop 2026', 'Spider-Man Universe', 'A - B - C', '  Spaced   Name  '):
            self.assertEqual(PL.split_name(name), ns['_fam_suffix_info'](name), name)


# ------------------------------------------------------------------ the routes in app.py
_PARSED = {}


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def _app_tree(src):
    if _PARSED.get('src') is not src:
        _PARSED.update(src=src, tree=ast.parse(src))
    return _PARSED['tree']


def _extract_src(src, names):
    tree = _app_tree(src)
    pieces, found = [], set()
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
        raise AssertionError(f'not found in app.py: {sorted(missing)}')
    return pieces


def _fn_src(src, name):
    for node in _app_tree(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f'{name} not found')


def _strings(obj, path=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f'{path}.{k}' if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f'{path}[{i}]')
    elif isinstance(obj, str):
        yield path, obj


class _Resp:
    def __init__(self, body=None, status=200):
        self.body = body
        self.status_code = status
        self.headers = {}


class _App:
    def route(self, *a, **k):
        return lambda fn: fn

    def response_class(self, status=200):
        return _Resp(None, status)


class _Args(dict):
    pass


class _Request:
    def __init__(self, args=None, headers=None):
        self.args = _Args(args or {})
        self.headers = headers or {}


class Routes(unittest.TestCase):
    def setUp(self):
        PL.reset_cache()
        self.catalog_calls = 0
        src = _APP_SRC()
        self.src = src
        ns = {'__name__': 'app_under_test', 'app': _App(), 'traceback': __import__('traceback'), 're': re, 'json': json,
              'request': None}
        ns['jsonify'] = lambda body: _Resp(json.loads(json.dumps(body, default=str)))
        ns['_synth_chat_gate'] = lambda allow_api_key=True: ({'username': 'acme', '_auth_via': 'api_key'}, None)
        ns['_partner_key_id'] = lambda u: 'key:' + str(u.get('username'))

        def _catalog():
            self.catalog_calls += 1
            return list(CATALOG)
        ns['_profile_catalog_for_chat'] = _catalog
        code = _extract_src(src, ['api_v1_profiles_library', 'api_v1_profiles_library_item', '_v1_library_reply',
                                  '_v1_prompt_for_library_id', '_v1_library_ref_resolver', '_v1_profile_id',
                                  '_rate_limit_check', '_rate_limit_response', '_V1_RATE_LIMITS', '_v1_rate_windows',
                                  '_v1_rate_windows_lock'])
        exec(compile('\n\n'.join(code), APP_PY, 'exec'), ns)
        self.ns = ns
        self._orig_lib = PL.library
        PL.library = lambda catalog, index=None: self._orig_lib(catalog, INDEX)
        self._orig_fill = PLI.fill_async
        PLI.fill_async = lambda keys, **kw: True

    def tearDown(self):
        PL.library = self._orig_lib
        PLI.fill_async = self._orig_fill
        PL.reset_cache()

    def _call(self, fn, *args, query=None, headers=None):
        self.ns['request'] = _Request(query, headers)
        out = self.ns[fn](*args)
        if isinstance(out, tuple):
            resp, code = out
            return resp.body, int(code), resp.headers
        return out.body, int(out.status_code), out.headers

    def test_list_detail_304_and_404(self):
        body, status, headers = self._call('api_v1_profiles_library', query={'q': 'guinness'})
        self.assertEqual(status, 200)
        self.assertEqual(body['total'], 4)
        self.assertIn('ETag', headers)
        b, s, h = self._call('api_v1_profiles_library', query={'q': 'guinness'}, headers={'If-None-Match': headers['ETag']})
        self.assertEqual((b, s, h['ETag']), (None, 304, headers['ETag']))
        body, status, _h = self._call('api_v1_profiles_library_item', PLI.profile_id(GU))
        self.assertEqual((status, [c['type'] for c in body['cuts']]), (200, ['Avid Fan', 'Global', 'Global - Avid Fan']))
        body, status, _h = self._call('api_v1_profiles_library_item', 'pf_' + '0' * 20)
        self.assertEqual((status, body['error']), (404, 'profile not found'))

    def test_a_failure_is_a_plain_503(self):
        orig = PL.list_route
        PL.list_route = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('S3 bucket dashboard-inputs down'))
        import contextlib
        import io
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                body, status, _h = self._call('api_v1_profiles_library')
        finally:
            PL.list_route = orig
        self.assertEqual((status, body), (503, {'success': False, 'error': 'library temporarily unavailable'}))

    def test_rate_limit_120_a_minute_shared_by_both_calls(self):
        self.assertEqual(self.ns['_V1_RATE_LIMITS']['/v1/library'], (120, 2000))
        self.assertEqual(tuple(PL.RATE_LIMIT), (120, 2000))
        for i in range(120):
            fn, args = (('api_v1_profiles_library', ()) if i % 2 else ('api_v1_profiles_library_item', (PLI.profile_id(GU),)))
            _b, status, _h = self._call(fn, *args)
            self.assertIn(status, (200, 304), i)
        body, status, headers = self._call('api_v1_profiles_library')
        self.assertEqual(status, 429)
        self.assertIn('Retry-After', headers)
        self.assertIn('/v1/library', body['error'])
        self.assertTrue(int(body['retry_after_seconds']) >= 1)

    def test_free_and_gated_like_the_other_free_calls(self):
        for fn in ('api_v1_profiles_library', 'api_v1_profiles_library_item'):
            seg = _fn_src(self.src, fn)
            self.assertIn('user, err = _synth_chat_gate()', seg)
            self.assertIn("_rate_limit_check(\n        key_id, '/v1/library')", seg)
            self.assertNotIn('_partner_credit_preflight', seg)
            self.assertNotIn('consume_credit', seg)
        self.assertIn("@app.route('/api/v1/profiles/library', methods=['GET'])", self.src)
        self.assertIn("@app.route('/api/v1/profiles/library/<profile_id>', methods=['GET'])", self.src)

    def test_a_library_id_as_the_prompt(self):
        fn = self.ns['_v1_prompt_for_library_id']
        self.assertEqual(fn(PLI.profile_id(TS_AVID)), ('Taylor Swift - Avid Fan', None))
        prompt, err = fn(PLI.profile_id('Never_Built_01_01_2026_10_00.csv'))
        resp, code = err
        self.assertEqual((code, resp.body), (404, {'success': False, 'error': 'profile not found'}))
        self.assertEqual(fn('pf_changs fans in Texas'), ('pf_changs fans in Texas', None))
        self.assertEqual(fn('Taylor Swift fans'), ('Taylor Swift fans', None))
        self.assertEqual(fn(PLI.profile_id('Gen_Pop_2026.csv'))[1][1], 404)
        conclude = _fn_src(self.src, '_v1_conclude')
        self.assertLess(conclude.index('_v1_exact_library_file(prompt)'), conclude.index('_v1_prompt_for_library_id(prompt)'))
        self.assertLess(conclude.index('_v1_prompt_for_library_id(prompt)'), conclude.index('_v1_interpret('))

    def test_results_point_back_to_the_library(self):
        self.assertEqual(self.ns['_v1_profile_id'](TS_NEW), PLI.profile_id(TS_NEW))
        check = _fn_src(self.src, 'api_v1_profiles_check')
        run = _fn_src(self.src, 'api_v1_profiles_run')
        status = _fn_src(self.src, 'api_v1_profiles_status')
        self.assertIn("'existing_match_profile_id': _v1_profile_id(ex_key) if ex_key else None", check)
        self.assertIn("'profile_id': _v1_profile_id(ex_key),", run)
        self.assertIn("resp['profile_id'] = _v1_profile_id(s3_key)", status)
        self.assertIn("resp['avid_profile_id'] = _v1_profile_id(avid_key)", status)
        bulk = _fn_src(self.src, 'api_v1_profiles_bulk')
        self.assertIn('_pb.read_from_items(parsed, resolve_ref=_v1_library_ref_resolver())', bulk)
        self.assertIn('index = _pb.with_library_refs(_bulk.library_index(catalog), read)', bulk)

    def test_a_file_name_as_the_prompt(self):
        fn = self.ns['_v1_prompt_for_library_id']
        self.assertEqual(fn(TS_NEW), ('Taylor Swift', None))
        self.assertEqual(fn('taylor swift - avid fan'), ('Taylor Swift - Avid Fan', None))
        self.assertEqual(fn('GUINNESS_GLOBAL_11_23_2026_20_44'), ('Guinness - Global', None))
        for unknown in ('Taylor_Swift_01_01_2020_10_00.csv', 'Taylor_Swift_01_01_2020_10_00', 'Reba McEntire - Avid Fan.csv',
                        'Gen_Pop_2026.csv'):
            prompt, err = fn(unknown)
            self.assertEqual((prompt, err[1], err[0].body['error']), (unknown, 404, 'profile not found'), unknown)
        for sentence in ('Can you pull Taylor_Swift_11_01_2026_14_22.csv for me?', 'please send Taylor Swift - Avid Fan.csv',
                         'Build a profile from my list.csv', 'Taylor Swift - Avid Fan fans in Ohio'):
            self.assertEqual(fn(sentence), (sentence, None), sentence)

    def test_a_bulk_list_reads_the_names_once(self):
        resolve = self.ns['_v1_library_ref_resolver']()
        self.assertEqual(self.catalog_calls, 0)
        self.assertEqual(resolve(PLI.profile_id(TS_NEW)), ('id', 'Taylor Swift', TS_NEW))
        self.assertEqual(resolve(GU), ('file', 'Guinness', GU))
        self.assertEqual(resolve('guinness - avid fan'), ('file', 'Guinness - Avid Fan', GU_AVID))
        self.assertEqual(resolve(PLI.profile_id('Never_Built_01_01_2026_10_00.csv')), ('unknown', None, None))
        self.assertEqual(resolve('Never_Built_01_01_2026_10_00.csv'), ('unknown', None, None))
        self.assertEqual(resolve('Dolly Parton'), (None, None, None))
        self.assertEqual(self.catalog_calls, 1)


class _CheckRequest:
    def __init__(self, body):
        self._body = body
        self.headers = {}
        self.args = {}

    def get_json(self, force=False, silent=False):
        return self._body


class _EngineRead(BaseException):
    """The real _v1_conclude reached the engine with this prompt. A
    BaseException, so it passes the decision's own `except Exception` and
    the stand-in engine can answer instead."""

    def __init__(self, prompt):
        super().__init__(prompt)
        self.prompt = prompt


def _engine_read(prompt, identity_context=None, actor=None):
    raise _EngineRead(prompt)


# The real decision up to the engine: the exact-file step, the name step,
# and what an exact file's verdict needs (in app.py's own order).
_DECISION_NAMES = ['CREDITS_PROFILE_ANALYSIS', 'CREDITS_SVOD', '_S3_KEY_TS_RE', '_profile_key_timestamp',
                   '_profile_age_days', 'ADDON_CUT_CREDITS', '_V1_CREDITS', '_v1_build_brief_summary', '_v1_conclude',
                   '_v1_exact_library_file', '_v1_exact_file_conclusion']


class MatchesCheck(unittest.TestCase):
    """The file name is exactly what POST /api/v1/profiles/check returns as
    existing_match_s3_key for the same profile: the real check route body
    and the real first steps of the decision (an exact file name or a
    library id is that file; a file name written as a profile's name is
    read as that name); only the engine's match, which cannot run
    offline, is stood in."""

    SAMPLE = (GU, TS_NEW, TS_AVID, TS_FEM, GU_GLOBAL, GU_GLOBAL_AVID)

    def setUp(self):
        PL.reset_cache()
        self.seen = []
        ns = {'__name__': 'app_under_test', 'app': _App(), 'traceback': __import__('traceback'), 're': re, 'json': json,
              'request': None, 'ADDON_CUT_CREDITS': 3, 'datetime': datetime, '_v1_interpret': _engine_read}
        ns['jsonify'] = lambda body: _Resp(json.loads(json.dumps(body, default=str)))
        ns['_synth_chat_gate'] = lambda allow_api_key=True: ({'username': 'acme', '_auth_via': 'api_key'}, None)
        ns['_partner_key_id'] = lambda u: 'key:check'
        ns['_profile_catalog_for_chat'] = lambda: list(CATALOG)
        ns['_v1_parse_check_run_body'] = lambda body: (str((body or {}).get('prompt') or '').strip(), True, None, None, None)
        ns['_partner_credit_preflight'] = lambda username, min_credits=0: (None, None)
        ns['_existing_match_charge_credits'] = lambda *a, **k: 0
        ns['_scrub_identity_dashes'] = lambda s: s
        ns['_v1_price_usd_for'] = lambda *a, **k: 0.0
        ns['_v1_price_usd_for_cut'] = lambda: 100.0
        ns['_draft_window_field'] = lambda d, dec: None
        ns['_v1_check_caveats'] = lambda: []
        ns['_generate_presigned_profile_url'] = lambda k, expires_seconds=0: 'https://signed.example/file'
        code = _extract_src(_APP_SRC(), ['api_v1_profiles_check', '_v1_prompt_for_library_id', '_v1_profile_id',
                                         '_scrub_v1_freetext', '_V1_BANNED_TOKENS', '_rate_limit_check',
                                         '_rate_limit_response', '_V1_RATE_LIMITS', '_v1_rate_windows',
                                         '_v1_rate_windows_lock'] + _DECISION_NAMES)
        exec(compile('\n\n'.join(code), APP_PY, 'exec'), ns)
        self.real_conclude = ns['_v1_conclude']
        ns['_v1_conclude'] = self._engine
        self.ns = ns

    def _engine(self, prompt, run_avid=True, identity_context=None, competitor_brands=None, actor=None):
        try:
            return self.real_conclude(prompt, run_avid=run_avid)          # _v1_conclude's own first steps
        except _EngineRead as read:
            prompt = read.prompt
        self.seen.append(prompt)                                          # what the engine reads
        hits = [e for e in CATALOG
                if e['s3_key'] in PUBLIC_KEYS and PL._ws(e['display_name']).lower() == PL._ws(prompt).lower()]
        hits.sort(key=lambda e: PL._stamp_iso(e['s3_key']) or '', reverse=True)     # the newest file of that name
        if not hits:
            return {'draft': {'subject': prompt, 'decision_reason': 'A new build.'}, 'decision': 'new_build',
                    'ex_key': '', 'd_type': None, 'cuts': [], 'price': 5, 'buildable': True, 'brief_summary': '',
                    'match_score': None}, None
        e = hits[0]
        draft = {'subject': prompt, 'existing_match_display_name': e['display_name'], 'existing_match_days_old': 4,
                 'decision_reason': 'Already in the library.'}
        return {'draft': draft, 'decision': 'existing_match', 'ex_key': e['s3_key'], 'd_type': None, 'cuts': [],
                'price': 0, 'buildable': True, 'brief_summary': '', 'match_score': 0.97}, None

    def _check(self, prompt):
        self.ns['request'] = _CheckRequest({'prompt': prompt})
        out = self.ns['api_v1_profiles_check']()
        if isinstance(out, tuple):
            return out[0].body, int(out[1])
        return out.body, int(out.status_code)

    def test_the_field_equals_checks_existing_match_s3_key(self):
        listed = {p['id']: p for p in _all()['profiles']}
        for key in self.SAMPLE:
            pid = PLI.profile_id(key)
            item = listed[pid]
            det = PL.detail_route(item['parent_id'] or pid, {}, None, CATALOG, INDEX)[0]
            in_detail = [c['s3_key'] for c in [det['profile']] + det['cuts'] if c['id'] == pid]
            self.assertEqual(in_detail, [item['s3_key']], key)
            for prompt in (pid, item['name']):
                body, status = self._check(prompt)
                self.assertEqual(status, 200, (key, prompt, body))
                self.assertEqual(body['decision'], 'existing_match')
                self.assertEqual(body['existing_match_s3_key'], item['s3_key'], (key, prompt))
                self.assertEqual(body['existing_match_profile_id'], pid, (key, prompt))
        self.assertTrue(any(listed[PLI.profile_id(k)]['parent_id'] for k in self.SAMPLE))           # cuts in the sample
        self.assertTrue(listed[PLI.profile_id(GU_GLOBAL)]['worldwide_audience'])                   # and the worldwide one

    def test_a_file_name_is_read_as_that_profile(self):
        for key in self.SAMPLE:                      # a Total Universe, cuts, the worldwide profile and its cut
            for prompt in (key, key[:-4].upper(), '  ' + key.lower() + '  '):
                body, status = self._check(prompt)
                self.assertEqual(status, 200, (key, prompt, body))
                self.assertEqual((body['decision'], body['existing_match_s3_key']), ('existing_match', key), prompt)
                self.assertEqual(body['credits_would_charge'], 0, prompt)
        # an older file that shares its name with a newer one: that exact file, as it is (Jenna 2026-11-24)
        self.seen.clear()
        body, _s = self._check(TS_OLD)
        self.assertEqual((body['existing_match_s3_key'], self.seen), (TS_OLD, []))
        # the shared name itself is still read by the engine, which answers with the newest
        body, _s = self._check('Taylor Swift')
        self.assertEqual((body['existing_match_s3_key'], self.seen), (TS_NEW, ['Taylor Swift']))

    def test_a_file_name_we_do_not_hold_is_a_404(self):
        for prompt in ('Taylor_Swift_01_01_2020_10_00.csv', 'TAYLOR_SWIFT_01_01_2020_10_00', 'Guinness - Global - Female.csv',
                       'Gen_Pop_2026.csv', 'folder/Taylor_Swift_11_01_2026_14_22.csv'):
            self.seen.clear()
            body, status = self._check(prompt)
            self.assertEqual((status, body), (404, {'success': False, 'error': 'profile not found'}), prompt)
            self.assertEqual(self.seen, [], prompt)                       # the engine never read it

    def test_a_sentence_with_a_file_name_is_read_as_written(self):
        for text in ('Can you pull Taylor_Swift_11_01_2026_14_22.csv for me?', 'please send Guinness - Global.csv'):
            self.seen.clear()
            body, status = self._check(text)
            self.assertEqual(status, 200, text)
            self.assertEqual(self.seen, [text])
            self.assertIsNone(body['existing_match_s3_key'])


NIKE_OLD = 'Nike_05_29_2026_02_45.csv'           # the file a partner pulled by its dates: over 120 days old
NIKE_OLDER = 'Nike_10_14_2025_11_07.csv'
NIKE_AVID = 'Nike - Avid Fan.csv'
NIKE_2025 = 'Nike - 2025 Total Universe.csv'
EXACT_CATALOG = CATALOG + [
    {'s3_key': NIKE_OLD, 'display_name': 'Nike', 'category': 'APPAREL/FOOTWEAR', 'last_modified': '2026-06-03T08:00:00Z'},
    {'s3_key': NIKE_OLDER, 'display_name': 'Nike', 'category': 'APPAREL/FOOTWEAR'},
    {'s3_key': NIKE_AVID, 'display_name': 'Nike - Avid Fan', 'category': 'APPAREL/FOOTWEAR',
     'last_modified': '2026-05-29T03:12:00Z'},
    {'s3_key': NIKE_2025, 'display_name': 'Nike - 2025 Total Universe', 'category': 'APPAREL/FOOTWEAR',
     'last_modified': '2026-01-04T12:00:00Z'},
]
EXACT_PUBLIC = PUBLIC_KEYS + [NIKE_OLD, NIKE_OLDER, NIKE_AVID, NIKE_2025]
FRESH_DAYS = 120                                   # prometheus.library_match.SYNTH_CHAT_FRESH_DAYS
NOT_FOUND = {'success': False, 'error': 'profile not found'}


def _days_since(stamp):
    from datetime import timezone
    return int((datetime.now(timezone.utc) - stamp.replace(tzinfo=timezone.utc)).total_seconds() // 86400)


class ExactFileWhateverItsAge(unittest.TestCase):
    """Jenna 2026-11-24 (a partner asked to pull profiles older than 120
    days by file name): an exact file name or a library id returns that
    file, whatever its age, at the key's existing-match price. A plain name
    still gets the newest file, with the fresh-build offer once that file
    is over 120 days old; dates still get the file for those dates.

    The real /check and /run route bodies and the real first steps of the
    decision. The engine, which cannot run offline, is stood in with
    today's rule; it answers only the prompts the decision hands it."""

    def setUp(self):
        PL.reset_cache()
        self.seen, self.charges, self.grants = [], [], []
        self.seat_charge = 0                   # a key whose plan covers the library: an existing match costs $0
        ns = {'__name__': 'app_under_test', 'app': _App(), 'traceback': __import__('traceback'), 're': re, 'json': json,
              'request': None, 'datetime': datetime, '_v1_interpret': _engine_read,
              'SYNTH_QUEUE_SECRET': 'test', 'SYNTH_QUEUE_URL': 'http://queue.invalid'}
        ns['jsonify'] = lambda body: _Resp(json.loads(json.dumps(body, default=str)))
        ns['_synth_chat_gate'] = lambda allow_api_key=True: ({'username': 'acme', '_auth_via': 'api_key'}, None)
        ns['_partner_key_id'] = lambda u: 'key:acme'
        ns['_profile_catalog_for_chat'] = lambda: list(EXACT_CATALOG)
        ns['_v1_parse_check_run_body'] = lambda body: (str((body or {}).get('prompt') or '').strip(),
                                                       bool((body or {}).get('run_avid', True)), None, None, None)
        ns['_partner_credit_preflight'] = lambda username, min_credits=0: (None, None)
        ns['_existing_match_charge_credits'] = lambda *a, **k: self.seat_charge
        ns['_scrub_identity_dashes'] = lambda s: s
        ns['_v1_price_usd_for'] = lambda decision, *a, **k: 0.0 if decision == 'existing_match' else 300.0
        ns['_v1_price_usd_for_cut'] = lambda: 100.0
        ns['_draft_window_field'] = lambda d, dec: '' if dec == 'existing_match' else 'Trailing 12 months'
        ns['_v1_check_caveats'] = lambda: []
        ns['_generate_presigned_profile_url'] = lambda k, expires_seconds=0: 'https://signed.example/' + k
        ns['_idempotency_lookup'] = lambda username, key: None
        ns['_idempotency_store'] = lambda username, key, body: None
        ns['has_credits_for'] = lambda *a, **k: True
        ns['check_user_credits'] = lambda username: (True, 7)
        ns['_v1_balance_usd'] = lambda username: 1234.56
        ns['consume_credit'] = lambda username, **kw: self.charges.append(kw) or True
        code = _extract_src(_APP_SRC(), ['api_v1_profiles_check', 'api_v1_profiles_run', '_v1_prompt_for_library_id',
                                         '_v1_profile_id', '_scrub_v1_freetext', '_V1_BANNED_TOKENS', '_rate_limit_check',
                                         '_rate_limit_response', '_V1_RATE_LIMITS', '_v1_rate_windows',
                                         '_v1_rate_windows_lock'] + _DECISION_NAMES)
        exec(compile('\n\n'.join(code), APP_PY, 'exec'), ns)
        self.real_conclude = ns['_v1_conclude']
        ns['_v1_conclude'] = self._decide
        self.ns = ns
        self._signup = sys.modules.get('site_signup')
        fake = types.ModuleType('site_signup')
        fake.grant_runs_to_user = lambda username, keys: self.grants.append(list(keys))
        sys.modules['site_signup'] = fake

    def tearDown(self):
        if self._signup is None:
            sys.modules.pop('site_signup', None)
        else:
            sys.modules['site_signup'] = self._signup
        PL.reset_cache()

    # the decision: its real first steps, then today's engine on whatever it reads
    def _decide(self, prompt, run_avid=True, identity_context=None, competitor_brands=None, actor=None):
        try:
            return self.real_conclude(prompt, run_avid=run_avid)
        except _EngineRead as read:
            self.seen.append(read.prompt)
            return self._today(read.prompt), None

    def _today(self, prompt):
        """Today's engine: dates name the file built on that day; a name gets
        its newest file, or the fresh-build offer once that file is over 120
        days old; anything else is a new build."""
        age = self.ns['_profile_age_days']

        def verdict(decision, key='', price=0):
            entry = next((e for e in EXACT_CATALOG if e['s3_key'] == key), {})
            draft = {'subject': entry.get('display_name') or prompt, 'decision_reason': 'Read by the engine.'}
            if key:
                draft.update(existing_match_s3_key=key, existing_match_display_name=entry['display_name'],
                             existing_match_days_old=age(key, entry.get('last_modified')))
            return {'draft': draft, 'candidates': [], 'decision': decision, 'ex_key': key, 'd_type': None, 'cuts': [],
                    'quarter_reads': [], 'price': price, 'buildable': True, 'refusal_code': None,
                    'refusal_message': None, 'spec': None, 'brief_summary': '', 'estimated_audience': None,
                    'subject_verified': True, 'match_score': 0.96 if key else None, 'related_profile': None}

        m = re.search(r'for the 12 months ending ([A-Z][a-z]+ \d{1,2}, \d{4})', prompt)
        if m:
            day = datetime.strptime(m.group(1), '%B %d, %Y').strftime('%m_%d_%Y')
            hit = next((e['s3_key'] for e in EXACT_CATALOG if e['s3_key'] in EXACT_PUBLIC and day in e['s3_key']), '')
            if hit:
                return verdict('existing_match', hit)
        same = sorted((e['s3_key'] for e in EXACT_CATALOG if e['s3_key'] in EXACT_PUBLIC
                       and PL._ws(e['display_name']).lower() == PL._ws(prompt).lower()),
                      key=lambda k: PL._stamp_iso(k) or '', reverse=True)
        if not same:
            return verdict('new_build', price=5)
        days = age(same[0])
        if isinstance(days, int) and days > FRESH_DAYS:
            return verdict('time_shifted_refresh', price=5)
        return verdict('existing_match', same[0])

    def _call(self, route, prompt):
        self.ns['request'] = _CheckRequest({'prompt': prompt})
        out = self.ns[route]()
        if isinstance(out, tuple):
            return out[0].body, int(out[1])
        return out.body, int(out.status_code)

    def _pick(self, body, want):
        return {k: body.get(k) for k in want}

    def test_an_old_file_by_its_exact_name_is_that_file(self):
        days = _days_since(datetime(2026, 5, 29, 2, 45))         # from the name, not the registry date
        self.assertGreater(days, FRESH_DAYS)
        for prompt in (NIKE_OLD, NIKE_OLD[:-4], NIKE_OLD.upper(), '  ' + NIKE_OLD[:-4].lower() + '  '):
            self.seen.clear()
            body, status = self._call('api_v1_profiles_check', prompt)
            want = {'decision': 'existing_match', 'existing_match_s3_key': NIKE_OLD,
                    'existing_match_profile_id': PLI.profile_id(NIKE_OLD), 'existing_match_display_name': 'Nike',
                    'existing_match_days_old': days, 'existing_match_last_modified': None, 'price_usd': 0.0,
                    'credits_would_charge': 0, 'match_score': 1.0, 'date_window': None,
                    'download_url': 'https://signed.example/' + NIKE_OLD}
            self.assertEqual((status, self._pick(body, want)), (200, want), prompt)
            body, status = self._call('api_v1_profiles_run', prompt)
            want = {'success': True, 'decision': 'existing_match', 'reused_existing': True, 'subject': 'Nike',
                    's3_key': NIKE_OLD, 'profile_id': PLI.profile_id(NIKE_OLD), 'profile_name': NIKE_OLD,
                    'download_url': 'https://signed.example/' + NIKE_OLD, 'charge_usd': 0.0, 'credits_charged': 0,
                    'existing_match_days_old': days, 'existing_match_last_modified': None, 'match_score': 1.0,
                    'run_id': None}
            self.assertEqual((status, self._pick(body, want)), (200, want), prompt)
            self.assertEqual(self.seen, [], prompt)                 # never read by the engine: no fresh-build offer
        self.assertEqual(self.charges, [])
        self.assertEqual({k for keys in self.grants for k in keys}, {NIKE_OLD})
        # an older file that shares its name with a newer one
        body, _s = self._call('api_v1_profiles_check', TS_OLD)
        self.assertEqual((body['existing_match_s3_key'], body['price_usd'], self.seen), (TS_OLD, 0.0, []))

    def test_a_library_id_is_the_same(self):
        for key in (NIKE_OLD, NIKE_OLDER, NIKE_AVID):
            pid = PLI.profile_id(key)
            for route, field in (('api_v1_profiles_check', 'existing_match_s3_key'), ('api_v1_profiles_run', 's3_key')):
                self.seen.clear()
                body, status = self._call(route, pid)
                self.assertEqual((status, body['decision'], body[field], self.seen), (200, 'existing_match', key, []),
                                 (key, route))
        self.assertEqual(self.charges, [])

    def test_a_plain_name_still_gets_the_fresh_build_offer(self):
        body, status = self._call('api_v1_profiles_check', 'Nike')
        self.assertEqual((status, body['decision'], body['price_usd'], body['existing_match_s3_key']),
                         (200, 'time_shifted_refresh', 300.0, None))
        self.assertEqual(self.seen, ['Nike'])
        # written without '.csv' and without a build date, a file name reads as the profile's name, as before
        for name in ('Nike - 2025 Total Universe', 'nike - avid fan'):
            self.seen.clear()
            self._call('api_v1_profiles_check', name)
            self.assertEqual(len(self.seen), 1, name)
        # a name whose newest file is inside the 120 days: that newest file
        self.seen.clear()
        body, _s = self._call('api_v1_profiles_check', 'Taylor Swift')
        self.assertEqual((body['decision'], body['existing_match_s3_key'], self.seen),
                         ('existing_match', TS_NEW, ['Taylor Swift']))

    def test_dates_still_get_the_file_for_those_dates(self):
        prompt = 'audience of Nike customers for the 12 months ending May 29, 2026'
        body, status = self._call('api_v1_profiles_check', prompt)
        self.assertEqual((status, body['decision'], body['existing_match_s3_key'], body['price_usd']),
                         (200, 'existing_match', NIKE_OLD, 0.0))
        self.assertEqual(self.seen, [prompt])                        # read by the engine word for word

    def test_a_file_we_do_not_hold_is_not_found(self):
        for prompt in ('Nike_05_29_2020_02_45.csv', 'NIKE_01_01_2026_10_00', PLI.profile_id('Nike_01_01_2026_10_00.csv'),
                       'Nike - 2019 Total Universe.csv', 'Gen_Pop_2026.csv'):
            for route in ('api_v1_profiles_check', 'api_v1_profiles_run'):
                self.seen.clear()
                body, status = self._call(route, prompt)
                self.assertEqual((status, body, self.seen), (404, NOT_FOUND, []), (prompt, route))
        self.assertEqual((self.charges, self.grants), ([], []))

    def test_a_year_cut_by_its_file_name_is_that_file(self):
        for key in (NIKE_2025, TS_2025):
            for prompt in (key, key.lower(), key.upper()):
                self.seen.clear()
                body, status = self._call('api_v1_profiles_check', prompt)
                self.assertEqual((status, body['decision'], body['existing_match_s3_key'], self.seen),
                                 (200, 'existing_match', key, []), prompt)
        body, _s = self._call('api_v1_profiles_check', NIKE_2025)
        self.assertEqual(body['existing_match_days_old'], _days_since(datetime(2026, 1, 4, 12, 0)))   # the file's own date
        body, status = self._call('api_v1_profiles_run', NIKE_2025)
        self.assertEqual((status, body['s3_key'], body['profile_name'], body['charge_usd']), (200, NIKE_2025, NIKE_2025, 0.0))

    def test_the_price_is_the_keys_existing_match_price(self):
        dated = 'audience of Nike customers for the 12 months ending May 29, 2026'
        for seat_charge, want in ((0, (0.0, 0, True)), (5, (300.0, 5, False))):   # a library plan, then a retail one
            self.seat_charge = seat_charge
            exact, _s = self._call('api_v1_profiles_check', NIKE_OLD)
            match, _s = self._call('api_v1_profiles_check', dated)                   # the engine's own existing match
            for body in (exact, match):
                self.assertEqual((body['decision'], body['existing_match_s3_key']), ('existing_match', NIKE_OLD))
            price = lambda b: (b['price_usd'], b['credits_would_charge'], 'download_url' in b)
            self.assertEqual((price(exact), price(match)), (want, want), seat_charge)
        body, status = self._call('api_v1_profiles_run', NIKE_OLD)
        self.assertEqual((status, body['charge_usd'], body['credits_charged'], body['s3_key']), (200, 300.0, 5, NIKE_OLD))
        self.assertEqual([c['credits_used'] for c in self.charges], [5])

    def test_plain_words_only(self):
        for prompt in (NIKE_OLD, PLI.profile_id(NIKE_OLD)):
            for route in ('api_v1_profiles_check', 'api_v1_profiles_run'):
                body, _s = self._call(route, prompt)
                for field in ('decision_reason', 'brief_summary'):
                    text = str(body.get(field) or '')
                    self.assertEqual([t for t in BANNED_FULL if t in text.lower()], [], (route, field, text))
                    self.assertFalse(set(text) & {'\u2014', '\u2013'}, text)
                self.assertEqual(body['decision_reason'], 'This is the exact file you asked for, returned as it is.')

    def test_the_decision_reads_the_exact_file_first(self):
        src = _APP_SRC()
        conclude = _fn_src(src, '_v1_conclude')
        steps = [conclude.index(s) for s in ('_v1_exact_library_file(prompt)', '_v1_prompt_for_library_id(prompt)',
                                             '_v1_interpret(')]
        self.assertEqual(steps, sorted(steps))
        for route in ('api_v1_profiles_check', 'api_v1_profiles_run'):
            seg = _fn_src(src, route)
            self.assertIn('_existing_match_charge_credits(', seg)            # the key's own existing-match price
        parse = _fn_src(src, '_v1_parse_check_run_body')
        for field in ('exact_file', 'file_name', 'library_id', 'profile_id', 's3_key', 'force_new', 'mode'):
            self.assertNotIn(f"'{field}'", parse)                            # no new request field: the prompt decides


class Resolver(unittest.TestCase):
    def setUp(self):
        PL.reset_cache()

    def test_written_as_a_file_name(self):
        for text in (TS_NEW, TS_NEW.upper(), TS_NEW[:-4], ' ' + TS_NEW[:-4].lower() + ' ', TS_AVID, TS_2025,
                     'Taylor Swift - Avid Fan.CSV'):
            self.assertTrue(PL.written_as_file_name(text), text)
        for text in ('Taylor Swift', 'Taylor Swift - Avid Fan', 'American Eagle', 'Taylor Swift - 2025 Total Universe',
                     'audience of Nike customers for the 12 months ending May 29, 2026', '', 'x' * 300):
            self.assertFalse(PL.written_as_file_name(text), text)

    def test_the_whole_text_is_an_id_or_a_file_name(self):
        cases = {
            TS_NEW: ('file', 'Taylor Swift', TS_NEW),
            TS_NEW[:-4].lower(): ('file', 'Taylor Swift', TS_NEW),
            TS_AVID.upper(): ('file', 'Taylor Swift - Avid Fan', TS_AVID),
            'taylor swift - avid fan': ('file', 'Taylor Swift - Avid Fan', TS_AVID),
            GU_GLOBAL[:-4]: ('file', 'Guinness - Global', GU_GLOBAL),
            '  ' + GU + '  ': ('file', 'Guinness', GU),
            PLI.profile_id(ORPHAN): ('id', 'Orphan Show - Avid Fan', ORPHAN),
        }
        for text, want in cases.items():
            self.assertEqual(PL.resolve_ref(text, CATALOG), want, text)

    def test_shaped_like_ours_but_not_held(self):
        for text in ('Taylor_Swift_01_01_2020_10_00.csv', 'Taylor_Swift_01_01_2020_10_00', 'Reba McEntire - Avid Fan.csv',
                     'Some Show.csv', 'Gen_Pop_2026.csv', 'pf_' + '0' * 20, 'folder/Taylor_Swift_11_01_2026_14_22.csv'):
            self.assertEqual(PL.resolve_ref(text, CATALOG), ('unknown', None, None), text)
            self.assertTrue(PL.looks_like_ref(text), text)

    def test_anything_else_is_a_prompt(self):
        for text in ('Can you pull Taylor_Swift_11_01_2026_14_22.csv for me?', 'please send Taylor Swift - Avid Fan.csv',
                     'Build a profile from my list.csv', 'Taylor Swift - Avid Fan.csv and Guinness.csv', 'Taylor Swift fans',
                     'Taylor Swift', 'pf_changs fans in Texas', '', 'x' * 300):
            self.assertEqual(PL.resolve_ref(text, CATALOG), (None, None, None), text)


class Bulk(unittest.TestCase):
    def test_file_names_and_ids_reuse_exactly_their_files(self):
        refs = PL.refs_for(CATALOG)
        parsed, err = PBK.parse_body({'subjects': ['taylor swift - avid fan', GU_GLOBAL, PLI.profile_id(TS_FEM), TS_OLD,
                                                   'Taylor Swift', 'Guinness_01_01_2020_10_00.csv',
                                                   'please send Guinness_11_23_2026_17_38.csv']})
        self.assertIsNone(err)
        read = PBK.read_from_items(parsed, resolve_ref=lambda s: PL.resolve_ref(s, refs=refs))
        self.assertEqual([i['subject'] for i in read['items']],
                         ['Taylor Swift - Avid Fan', 'Guinness - Global', 'TAYLOR SWIFT - Female', TS_OLD, 'Taylor Swift',
                          'please send Guinness_11_23_2026_17_38.csv'])
        self.assertEqual(read['dropped'], 1)
        index = PBK.with_library_refs(BP.library_index(CATALOG), read)
        plan = {r['subject']: (r['state'], r['key']) for r in BP.plan_rows(read['items'], index)}
        self.assertEqual(plan['Taylor Swift - Avid Fan'], ('reused', TS_AVID))
        self.assertEqual(plan['Guinness - Global'], ('reused', GU_GLOBAL))
        self.assertEqual(plan['TAYLOR SWIFT - Female'], ('reused', TS_FEM))
        self.assertEqual(plan[TS_OLD], ('reused', TS_OLD))                      # exactly the older file named
        self.assertEqual(plan['Taylor Swift'], ('reused', TS_NEW))              # a plain name: the newest, as always
        self.assertEqual(plan['please send Guinness_11_23_2026_17_38.csv'][0], 'queued')
        bare = {r['subject']: r['state'] for r in BP.plan_rows(read['items'], BP.library_index(CATALOG))}
        self.assertEqual((bare['Taylor Swift - Avid Fan'], bare['Guinness - Global']), ('queued', 'queued'))

    def test_an_old_file_on_a_list_is_reused_whatever_its_age(self):
        refs = PL.refs_for(EXACT_CATALOG)
        parsed, err = PBK.parse_body({'subjects': [NIKE_OLD, PLI.profile_id(NIKE_OLDER), NIKE_2025]})
        self.assertIsNone(err)
        read = PBK.read_from_items(parsed, resolve_ref=lambda s: PL.resolve_ref(s, refs=refs))
        index = PBK.with_library_refs(BP.library_index(EXACT_CATALOG), read)
        plan = [(r['state'], r['key'], r['charged']) for r in BP.plan_rows(read['items'], index)]
        self.assertEqual(sorted(plan), sorted([('reused', NIKE_OLD, False), ('reused', NIKE_OLDER, False),
                                               ('reused', NIKE_2025, False)]))

    def test_ids_on_a_list(self):
        names = {PLI.profile_id(TS_AVID): 'Taylor Swift - Avid Fan'}
        parsed, err = PBK.parse_body({'subjects': [PLI.profile_id(TS_AVID), 'Dolly Parton',
                                                   PLI.profile_id('Never_Built_01_01_2026_10_00.csv')]})
        self.assertIsNone(err)
        read = PBK.read_from_items(parsed, resolve_id=names.get)
        self.assertEqual([i['subject'] for i in read['items']], ['Taylor Swift - Avid Fan', 'Dolly Parton'])
        self.assertEqual(read['dropped'], 1)
        read = PBK.read_from_items(parsed)                  # no way to resolve: an id never becomes a name to build
        self.assertEqual([i['subject'] for i in read['items']], ['Dolly Parton'])
        self.assertEqual(read['dropped'], 2)

    def test_rows_carry_the_library_id(self):
        ledger = {'bulk_id': 'bulk_20261123_x', 'status': 'active', 'run_avid': True,
                  'rows': [{'subject': 'Taylor Swift', 'state': 'reused', 'key': TS_NEW, 'avid_key': TS_AVID},
                           {'subject': 'Dolly Parton', 'state': 'queued'}]}
        doc = PBK.status_doc(ledger, host_url='https://dashboard.crosswalknyc.com')
        row = doc['rows'][0]
        self.assertEqual((row['profile_id'], row['avid_profile_id']), (PLI.profile_id(TS_NEW), PLI.profile_id(TS_AVID)))
        self.assertNotIn('profile_id', doc['rows'][1])


class SchemaAndGuide(unittest.TestCase):
    def test_schema_names_both_calls_in_plain_words(self):
        src = _APP_SRC()
        seg = _fn_src(src, 'api_v1_schema')
        self.assertIn("'GET /api/v1/profiles/library': {", seg)
        self.assertIn("'GET /api/v1/profiles/library/{id}': {", seg)
        part = seg[seg.index("'GET /api/v1/profiles/library': {"):seg.index("'GET /api/v1/genpop': {")]
        lits = ' '.join(n.value for n in ast.walk(ast.parse('{' + part + '}'))
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)).lower()
        self.assertEqual([t for t in BANNED_FULL if t in lits], [])
        for f in ('existing_match_profile_id', "'profile_id'", "'avid_profile_id'"):
            self.assertIn(f, seg)
        check = seg[seg.index("'POST /api/v1/profiles/check': {"):seg.index("'POST /api/v1/profiles/run': {")]
        lits = ' '.join(n.value for n in ast.walk(ast.parse('{' + check.rstrip().rstrip(',') + '}'))
                        if isinstance(n, ast.Constant) and isinstance(n.value, str))
        self.assertIn('an id or a file name returns that exact file, whatever its age', lits)

    def test_guide_older_files(self):
        doc = _read(DOC)
        sec = doc[doc.index('### Getting an older file'):doc.index('## Bulk builds')]
        low = sec.lower()
        for want in ('exact file', 'whatever its age', 'over 120 days old', '$300', 'dates', '`.csv`', '`id`'):
            self.assertIn(want, low if want.islower() else sec, want)
        self.assertEqual([t for t in BANNED_FULL if t in low], [])
        self.assertFalse(set(sec) & {'\u2014', '\u2013'})
        self.assertIn('(#getting-an-older-file)', doc)
        self.assertNotIn('answer with the newer one', doc)

    def test_guide(self):
        doc = _read(DOC)
        self.assertIn('### `GET /api/v1/profiles/library`', doc)
        self.assertIn('### `GET /api/v1/profiles/library/<id>`', doc)
        sec = doc[doc.index('## Browse the library'):doc.index('## Bulk builds')]
        low = sec.lower()
        self.assertEqual([t for t in BANNED_FULL if t in low], [])
        self.assertNotIn('\u2014', sec)
        self.assertNotIn('\u2013', sec)
        self.assertIn('120 library calls', doc)


if __name__ == '__main__':
    unittest.main(verbosity=1)
