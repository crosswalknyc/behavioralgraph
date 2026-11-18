#!/usr/bin/env python3
"""Internal copies never reach a reader (2026-11-17).

The Subscriber IQ list showed every ``.csv`` in its bucket: 311 live
trackers and 272 copies under ``_backups/`` (the state of a file before
a correction), to every seat. A reader could open the old figure beside
the current one. The standing rule: those copies are for internal audit
only and are never visible.

What this holds:

  1. the predicate (``internal_copies``): what is a copy, what is not
  2. the Subscriber IQ list: live trackers only, for a client seat and
     for staff; a paid-only seat still sees only what it was granted
  3. a direct open of an internal key is refused as not found, for a
     client and for staff, before storage is touched; a live tracker
     still opens; a file a seat was not granted is still refused
  4. the admin routes that take a Subscriber IQ key refuse a copy
  5. the siblings: Profile IQ gate and selector, Brand Partnership IQ,
     Digital Journey IQ, Digital Twins
  6. Prometheus: the tracker index, the library, the file hand-over and
     the ready note never carry a copy
  7. nothing a reader sees in a refusal says what the key was

Hermetic: the routes are lifted out of app.py by name and run against a
stub bucket inside a bare Flask request. The dashboard app is never
imported, nothing is written, no mail.
"""
import ast
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import flask  # noqa: E402
from flask import jsonify  # noqa: E402

import internal_copies as IC  # noqa: E402
import file_access as FA  # noqa: E402   (the shared gate the lifted routes call, 2026-11-18)
import wallet  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


# ----------------------------------------------------------------- the bucket
LIVE = [
    'Outlander_Blood_of_My_Blood_Season_2_to_date_11_18_2026_02_30.csv',
    'Outlander_Blood_of_My_Blood_Season_1_10_02_2026_01_19.csv',
    'Gilmore_Girls_Season_1_09_28_2026_14_11.csv',
    'SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv',
]
INTERNAL = [
    '_backups/Outlander_Blood_of_My_Blood_Season_2_to_date_11_18_2026_02_30.pre_universe_fix_20261118_014045.csv',
    '_backups/Outlander_Blood_of_My_Blood_Season_1_10_02_2026_01_19.csv.pre_adult_signups_20261110_225014.csv',
    '_backups/Gilmore_Girls_Season_1_09_28_2026_14_11.csv.pre_adult_signups_20261110_225014.csv',
    '_backups/plain_copy_with_no_suffix.csv',
    'backups/SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv',
    '_retired/2026_11/Gilmore_Girls_Season_1_09_28_2026_14_11.csv',
    '_quarantine/SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv',
    '_tmp/work.csv',
    '_archive/old.csv',
    '_scratch_root_file.csv',
    'Gilmore_Girls_Season_1_09_28_2026_14_11.pre_rebase_20261101_010101.csv',
    'nested/_backups/Gilmore_Girls_Season_1_09_28_2026_14_11.csv',
]
NEVER_LISTED = [
    'historic/Outlander_Blood_of_My_Blood_Season_2_10_01_2026_16_58.csv',
    'historic/Gilmore_Girls_Season_1_09_28_2026_14_11.csv.pre_round_reconcile_20261014',
    'purgatory/Unreleased_Show_11_01_2026_10_10.csv',
    'Gilmore_Girls_Season_1_09_28_2026_14_11.json',
]
ALL_KEYS = LIVE + INTERNAL + NEVER_LISTED

TRACKER = ('Category,Count\nShow/Content Tracked,Outlander\nPlatform Tracked,Starz\n'
           'Total Show Watchers,56747\nNew Platform Signups,1509\n').encode('utf-8')


class _Body:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d


class _Paginator:
    def __init__(self, s3):
        self.s3 = s3

    def paginate(self, **kw):
        self.s3.lists.append(kw)
        bucket = kw.get('Bucket')
        keys = self.s3.buckets.get(bucket, [])
        prefix = kw.get('Prefix') or ''
        now = datetime(2026, 11, 18, 1, 45, tzinfo=timezone.utc)
        half = max(1, len(keys) // 2)
        for chunk in (keys[:half], keys[half:]):
            yield {'Contents': [{'Key': k, 'Size': 1234, 'LastModified': now}
                                for k in chunk if k.startswith(prefix)]}


class StubS3:
    """Reads only. Any write fails the test."""

    class exceptions:                                       # noqa: N801
        class NoSuchKey(Exception):
            pass

    def __init__(self, buckets):
        self.buckets = buckets
        self.gets = []
        self.lists = []
        self.writes = []

    def get_paginator(self, name):
        assert name == 'list_objects_v2'
        return _Paginator(self)

    def get_object(self, Bucket, Key, **kw):
        self.gets.append((Bucket, Key))
        if Key in self.buckets.get(Bucket, []):
            return {'Body': _Body(TRACKER)}
        raise self.exceptions.NoSuchKey(f'{Bucket}/{Key}')

    def head_object(self, Bucket, Key, **kw):
        if Key in self.buckets.get(Bucket, []):
            return {'ContentLength': len(TRACKER), 'LastModified': datetime(2026, 11, 18, tzinfo=timezone.utc)}
        raise self.exceptions.NoSuchKey(f'{Bucket}/{Key}')

    def generate_presigned_url(self, *a, **kw):
        return 'https://example.invalid/signed'

    def _write(self, *a, **kw):
        self.writes.append((a, kw))
        raise AssertionError('this test never writes')

    put_object = copy_object = delete_object = _write


# ------------------------------------------------- lift functions out of app.py
APP_SRC = (ROOT / 'app.py').read_text(encoding='utf-8')
APP_TREE = ast.parse(APP_SRC)
APP_LINES = APP_SRC.splitlines(keepends=True)


def seg_of(node, lines=None):
    """The source of a top-level node (ast.get_source_segment re-splits the
    whole file on every call, far too slow on a file this size)."""
    lines = APP_LINES if lines is None else lines
    return ''.join(lines[node.lineno - 1:node.end_lineno])


def lift(names, ns):
    """Compile the named top-level functions of app.py into ``ns`` with
    their route decorators removed. The app itself is never imported."""
    found = set()
    for node in APP_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            mod = ast.Module(body=[node], type_ignores=[])
            exec(compile(mod, str(ROOT / 'app.py'), 'exec'), ns)   # noqa: S102
            found.add(node.name)
    missing = set(names) - found
    assert not missing, f'not found in app.py: {sorted(missing)}'
    return ns


WEB = flask.Flask('subiq_internal_copies_test')

STAFF = {'username': 'ops', 'role': 'super_admin', 'email': 'ops@crosswalknyc.com',
         'has_subscriber_iq_access': True}
ADMIN = {'username': 'adm', 'role': 'admin', 'has_subscriber_iq_access': True}
SONY = {'username': 'sony_seat', 'role': 'user', 'company': 'Sony Pictures Television',
        'has_subscriber_iq_access': True}
PAID_ONLY = {'username': 'paid_seat', 'role': 'user', 'company': 'Small Studio',
             'plan': wallet.PROMETHEUS_SELF_SERVE_PLAN, 'has_subscriber_iq_access': True,
             'allowed_subscriber_iq_runs': ['SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv']}
GIFTED = {'username': 'gift_seat', 'role': 'user', 'company': 'Warner Bros. Discovery',
          'plan': wallet.PROMETHEUS_SELF_SERVE_PLAN, 'has_subscriber_iq_access': True,
          'allowed_subscriber_iq_runs': [],
          'complimentary_titles': {'subscriber_iq': ['Gilmore Girls']}}

STATE = {'user': None}


def _ns(s3):
    def _parse(csv_text):
        return {'metadata': {'platform': 'Starz', 'date_range': '2026-09-01 TO 2026-11-17'},
                'key_metrics': {'total_watchers': {'count': 56747}},
                'episode_attribution': [], 'signup_timing': [], 'attribution_summary': {},
                'post_signup_touchpoints': []}

    ns = {
        '__name__': 'app_lifted', 're': __import__('re'), 'json': __import__('json'),
        'jsonify': jsonify, 'request': flask.request, 'session': {},
        's3_client': s3, 'SUBSCRIBER_S3_BUCKET': 'svod-acquisition', 'S3_BUCKET': 'dashboard-inputs',
        'S3_PURGATORY_PREFIX': 'purgatory/', '_internal_copies': IC, '_file_access': FA,
        '_require_module_access': lambda *a, **k: (True, None),
        'load_svod_metadata': lambda: {LIVE[0]: {'category': 'SERIES - SONY'}},
        'normalize_subscriber_iq_category': lambda c: c,
        'get_current_user': lambda: STATE['user'],
        'parse_subscriber_iq_csv': _parse,
        'apply_subscriber_episode_overrides': lambda k, p: None,
        'normalize_demographics_gen_pop_to_nps': lambda p: None,
        'svod_pricing_for': lambda p: {},
        'parse_number': lambda v: 0,
        'traceback': __import__('traceback'),
    }
    lift({'_normalize_role', '_user_can_open_subscriber', 'list_subscriber_iq_files',
          'get_subscriber_iq_data', 'resolve_subscriber_iq_s3_key', '_bpiq_is_backup_key',
          '_is_root_csv_key', '_require_profile_run_access', 'get_journey_iq_result',
          '_key_refusal_username', '_key_not_found', '_door_key', '_is_missing_key_error'}, ns)
    return ns


def _call(fn, *a, user=None, path='/x', **kw):
    STATE['user'] = user
    import contextlib
    import io
    with WEB.test_request_context(path):
        with contextlib.redirect_stdout(io.StringIO()):
            out = fn(*a, **kw)
        if isinstance(out, tuple):
            resp, code = out[0], out[1]
        else:
            resp, code = out, out.status_code
        return code, resp.get_json()


BANNED_WORDS = ('backup', 'quarantine', 'retired', 'internal', 'pipeline', 'archive', 'copy',
                '_backups', '.pre_', 'pre_universe', 'synth')


def plain(text):
    low = str(text or '').lower()
    return not any(w in low for w in BANNED_WORDS) and '\u2014' not in low and '\u2013' not in low


# ============================================================ 1. the predicate
print('1. the predicate')
for k in INTERNAL:
    check(f'internal: {k[:60]}', IC.is_internal_copy_key(k))
for k in LIVE:
    check(f'live: {k[:60]}', not IC.is_internal_copy_key(k) and IC.subscriber_iq_key_servable(k))
for k in NEVER_LISTED:
    check(f'not servable in the tab: {k[:60]}', not IC.subscriber_iq_key_servable(k))
check('bucket-prefixed copy is still a copy',
      IC.is_internal_copy_key('svod-acquisition/_backups/x.csv')
      and not IC.subscriber_iq_key_servable('svod-acquisition/_backups/x.csv'))
check('bucket-prefixed live tracker is servable', IC.subscriber_iq_key_servable('svod-acquisition/' + LIVE[0]))
check('a deep underscore folder is a copy', IC.is_internal_copy_key('brand-partnership-iq/_backups/a.json')
      and IC.is_internal_copy_key('journey-iq/_cache/x.json.gz')
      and IC.is_internal_copy_key('digital-twins/_backups/t.json.gz'))
check('a story journey id is not a copy', not IC.is_internal_copy_key('__demo_fragrance_tiktok__')
      and not IC.is_internal_copy_key('journey-iq/demos/__demo_fragrance_tiktok__.json.gz'))
check('ordinary keys pass', not any(IC.is_internal_copy_key(k) for k in (
    'Reba_McEntire_08_10_2026_12_00.csv', 'Gen_Pop_2026.csv', 'generated_data/ab12cd34ef56/Nike_reach.csv',
    'journey-iq/admin/Chime_20261001.json.gz', 'brand-partnership-iq/Goodys_x_NASCAR_09_23_2026_10_10.json',
    'digital-twins/admin/Paramount_20261030_202216_418f0c1d10.json.gz', 'Reba McEntire - Avid Fan.csv',
    'The_Pre_Show_10_10_2026_10_10.csv', 'Pre_Game_Live_10_10_2026_10_10.csv')))
check('a name carrying backups as a word is not a folder', not IC.is_internal_copy_key('Backups_The_Movie_10_10_2026_10_10.csv'))
check('empty and None are not copies, and are not servable',
      not IC.is_internal_copy_key('') and not IC.is_internal_copy_key(None)
      and not IC.subscriber_iq_key_servable('') and not IC.subscriber_iq_key_servable(None))
check('windows separators and stray spaces do not slip past',
      IC.is_internal_copy_key('_backups\\x.csv') and IC.is_internal_copy_key(' _backups/x.csv')
      and IC.is_internal_copy_key('a/ _retired /x.csv'))
check('helpers', IC.subscriber_iq_live_keys(ALL_KEYS) == LIVE and IC.drop_internal(LIVE + INTERNAL) == LIVE)
check('the refusal words are plain', plain(IC.NOT_FOUND) and plain(IC.SUBIQ_NOT_FOUND))

# ================================================================ 2. the list
print('2. the Subscriber IQ list')
s3 = StubS3({'svod-acquisition': list(ALL_KEYS), 'dashboard-inputs': []})
ns = _ns(s3)


def listed(user):
    code, body = _call(ns['list_subscriber_iq_files'], user=user)
    assert code == 200 and body.get('success'), body
    return [f['s3_key'] for f in body['files']], body


for label, user in (('a Sony seat', SONY), ('staff (super admin)', STAFF), ('an admin', ADMIN)):
    keys, body = listed(user)
    check(f'{label} sees exactly the live trackers', sorted(keys) == sorted(LIVE), str(keys))
    check(f'{label} sees no copy', not any(IC.is_internal_copy_key(k) for k in keys))
    names = ' | '.join(f['show_name'] for f in body['files'])
    check(f'{label}: no name on the list reads as a copy', 'backups' not in names.lower() and ' pre ' not in names.lower(), names)

keys, _ = listed(PAID_ONLY)
check('a paid-only seat still sees only the file it was granted', keys == [PAID_ONLY['allowed_subscriber_iq_runs'][0]], str(keys))
keys, _ = listed(GIFTED)
check('a seat with a title grant sees the live file for that title and nothing else',
      keys == ['Gilmore_Girls_Season_1_09_28_2026_14_11.csv'], str(keys))
check('no storage read or write happened for a list', not s3.gets and not s3.writes)

can = ns['_user_can_open_subscriber']
check('the access helper refuses a copy for staff', not can(STAFF, INTERNAL[0]) and not can(ADMIN, INTERNAL[1]))
check('the access helper refuses a copy for a client', not can(SONY, INTERNAL[0], 'Outlander Blood of My Blood'))
check('the access helper refuses a copy even when the title grant matches', not can(GIFTED, INTERNAL[2], 'Gilmore Girls'))
check('the access helper still opens a live tracker', can(SONY, LIVE[0]) and can(STAFF, LIVE[0]) and can(GIFTED, LIVE[2], 'Gilmore Girls'))
check('the access helper still refuses a file a paid-only seat was not granted', not can(PAID_ONLY, LIVE[0]))
check('no user at all opens nothing', not can(None, LIVE[0]))

# ================================================================ 3. the open
print('3. a direct open or download by key')
for label, user in (('a Sony seat', SONY), ('staff', STAFF), ('a paid-only seat', PAID_ONLY)):
    for k in INTERNAL + NEVER_LISTED[:3]:
        before = len(s3.gets)
        code, body = _call(ns['get_subscriber_iq_data'], k, user=user)
        ok = (code == 404 and body.get('success') is False and body.get('error') == IC.SUBIQ_NOT_FOUND
              and len(s3.gets) == before and 'data' not in body and plain(body.get('error')) and k not in str(body))
        if not ok:
            check(f'{label}: refused as not found: {k[:50]}', False, f'{code} {body}')
            break
    else:
        check(f'{label}: every internal, archived and unreleased key is refused as not found, storage untouched', True)

code, body = _call(ns['get_subscriber_iq_data'], LIVE[0], user=SONY)
check('a live tracker still opens for a client', code == 200 and body.get('success') and body.get('s3_key') == LIVE[0]
      and body.get('show') == 'OUTLANDER BLOOD OF MY BLOOD SEASON 2 TO DATE', f'{code} {str(body)[:200]}')
code, body = _call(ns['get_subscriber_iq_data'], LIVE[0], user=STAFF)
check('a live tracker still opens for staff', code == 200 and body.get('success'))
code, body = _call(ns['get_subscriber_iq_data'], LIVE[0], user=PAID_ONLY)
check('a file a paid-only seat was not granted is still refused', code == 403 and body.get('success') is False, f'{code} {body}')
code, body = _call(ns['get_subscriber_iq_data'], PAID_ONLY['allowed_subscriber_iq_runs'][0], user=PAID_ONLY)
check('the file a paid-only seat was granted opens', code == 200 and body.get('success'))
check('nothing was written', not s3.writes)

# ====================================================== 4. admin key routes
print('4. admin routes that take a Subscriber IQ key')
src_ep = seg_of(next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                     and n.name == 'admin_subscriber_episode_dates'))
check('episode dates: both the read and the save refuse a copy before storage',
      src_ep.count('_internal_copies.subscriber_iq_key_servable(s3_key)') == 2
      and src_ep.index('subscriber_iq_key_servable') < src_ep.index('get_object'))
for fn_name, needle in (('rename_file', 'subscriber_iq_key_servable(bare_old)'),
                        ('change_file_category', 'subscriber_iq_key_servable(actual_key)')):
    node = next((n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                 and needle in seg_of(n)), None)
    check(f'{needle.split("(")[1][:-1]}: the admin action refuses a copy', node is not None)
node = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
            and 'svod_paginator.paginate' in seg_of(n))
seg = seg_of(node)
i = seg.index('svod_paginator.paginate')
check('the admin file manager list skips copies', 'subscriber_iq_key_servable(key)' in seg[i:i + 900])
check('the admin key resolver strips the bucket prefix', ns['resolve_subscriber_iq_s3_key']('svod-acquisition/' + INTERNAL[0]) == INTERNAL[0])

# ============================================================ 5. the siblings
print('5. the sibling products')
# Profile IQ selector
root_ok = ns['_is_root_csv_key']
check('Profile IQ selector: a root profile passes', root_ok('Reba_McEntire_08_10_2026_12_00.csv') and root_ok('Gen_Pop_2026.csv'))
check('Profile IQ selector: a root copy is dropped',
      not root_ok('Amber_Midthunder_06_05_2026_07_35.pre_patch.bak.csv') and not root_ok('_scratch.csv'))
check('Profile IQ selector: a subfolder key is dropped, as before',
      not root_ok('_backups/Reba.csv') and not root_ok('historic/Reba.csv') and not root_ok('system/users.csv'))

# Profile IQ read gate (every profile read and download route)
ns['_priced_profile_unlock_response'] = lambda user, key: None
ns['_user_can_access_profile_run'] = lambda user, key: True
gate = ns['_require_profile_run_access']


def gate_call(key, user):
    STATE['user'] = user
    with WEB.test_request_context('/x'):
        ok, err = gate(key)
        if ok:
            return True, None, None
        return False, err[1], err[0].get_json()


for label, user in (('a client', SONY), ('staff', STAFF), ('an admin', ADMIN)):
    ok, code, body = gate_call('_backups/Reba_McEntire_08_10_2026_12_00.csv.pre_mpb_release_20261019.csv', user)
    check(f'Profile IQ gate: a copy is not found for {label}', ok is False and code == 404
          and body.get('error') == IC.NOT_FOUND and 's3_key' not in body and plain(body.get('error')), f'{ok} {code} {body}')
    ok, code, body = gate_call('Amber_Midthunder_06_05_2026_07_35.pre_patch.bak.csv', user)
    check(f'Profile IQ gate: a root copy is not found for {label}', ok is False and code == 404)
    ok, code, body = gate_call('Reba_McEntire_08_10_2026_12_00.csv', user)
    check(f'Profile IQ gate: a profile still opens for {label}', ok is True)
# A data file Prometheus wrote goes out on its own signed link, never through a
# profile route: the profile gate serves profile files only (file_access, 2026-11-18).
ok, code, body = gate_call('generated_data/ab12cd34ef56/Nike_reach.csv', SONY)
check('Profile IQ gate: a delivered data file is not a profile file (it rides its own link)',
      ok is False and code == 404 and body.get('error') == IC.NOT_FOUND)

# Brand Partnership IQ
bp = ns['_bpiq_is_backup_key']
check('Brand Partnership IQ: copies are refused', bp('brand-partnership-iq/_backups/x.json') and bp('_backups/x.json')
      and bp('brand-partnership-iq/_retired/x.json') and bp('brand-partnership-iq/x.json.pre_fix_20261101.json')
      and bp('_quarantine/x.json'))
check('Brand Partnership IQ: a live read passes', not bp('brand-partnership-iq/Goodys_x_NASCAR_09_23_2026_10_10.json')
      and not bp('Goodys_x_NASCAR_09_23_2026_10_10.json') and not bp(''))

# Digital Journey IQ
loads = []
fake_jiq = types.ModuleType('migration.journey_iq')
fake_jiq.S3_PREFIX = 'journey-iq/'
fake_jiq.is_archive_key = lambda k: k.startswith('journey-iq/archive/')
fake_jiq.load_run_from_s3 = lambda s3c, key: (loads.append(key) or {'meta': {'project_name': 'x'}})
_saved = {k: sys.modules.get(k) for k in ('migration', 'migration.journey_iq')}
try:
    import migration as _mig_pkg
except Exception:                                    # pragma: no cover
    _mig_pkg = types.ModuleType('migration')
    sys.modules['migration'] = _mig_pkg
_had_attr = getattr(_mig_pkg, 'journey_iq', None)
sys.modules['migration.journey_iq'] = fake_jiq
_mig_pkg.journey_iq = fake_jiq
ns['user_can_run_analysis_module'] = lambda user, mod: True
ns['_user_jiq_run_access'] = lambda user: (user.get('role') in ('admin', 'super_admin'), True, [])
ns['_jiq_item_allowed'] = lambda *a, **k: True
try:
    for label, user in (('a client', SONY), ('staff', STAFF)):
        for k in ('_backups/Chime_20261001.json.gz', 'journey-iq/_backups/Chime_20261001.json.gz',
                  '_index.json', 'journey-iq/_index.json', 'journey-iq/_cache/x.json.gz',
                  'admin/Chime_20261001.json.gz.pre_fix_20261101'):
            n0 = len(loads)
            code, body = _call(ns['get_journey_iq_result'], k, user=user)
            if not (code == 404 and body.get('error') == 'Run not found' and len(loads) == n0):
                check(f'Digital Journey IQ: {label} is refused {k}', False, f'{code} {body}')
                break
        else:
            check(f'Digital Journey IQ: copies and the index files are not found for {label}, storage untouched', True)
    code, body = _call(ns['get_journey_iq_result'], 'admin/Chime_20261001.json.gz', user=SONY)
    check('Digital Journey IQ: a live run still opens', code == 200 and body.get('s3_key') == 'journey-iq/admin/Chime_20261001.json.gz')
    code, body = _call(ns['get_journey_iq_result'], '__demo_fragrance_tiktok__', user=SONY)
    check('Digital Journey IQ: a story journey still opens',
          code == 200 and body.get('s3_key') == 'journey-iq/demos/__demo_fragrance_tiktok__.json.gz')
finally:
    for k, v in _saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if _had_attr is None:
        try:
            delattr(_mig_pkg, 'journey_iq')
        except Exception:
            pass
    else:
        _mig_pkg.journey_iq = _had_attr

# Digital Twins: the one loader every twin route goes through
DT_SRC = (ROOT / 'digital_twins_api.py').read_text(encoding='utf-8')
DT_TREE = ast.parse(DT_SRC)
node = next(n for n in DT_TREE.body if isinstance(n, ast.FunctionDef) and n.name == '_twin_or_404')
twin_loads = []
dt_ns = {'jsonify': jsonify, '_internal_copies': IC, '_s3': lambda: None,
         'DT': types.SimpleNamespace(S3_PREFIX='digital-twins/', list_runs=lambda s3c: [],
                                     in_company=lambda *a, **k: True, can_open=lambda *a, **k: True,
                                     load_twin=lambda s3c, key: (twin_loads.append(key) or {'meta': {}})),
         'CO': types.SimpleNamespace(NOT_IN_LIBRARY='That twin is not in your library.')}
exec(compile(ast.Module(body=[node], type_ignores=[]), 'digital_twins_api.py', 'exec'), dt_ns)   # noqa: S102
with WEB.test_request_context('/x'):
    for k in ('digital-twins/_backups/Paramount_x.json.gz', 'digital-twins/admin/_backups/P.json.gz',
              'digital-twins/admin/Paramount.pre_liz_fixes_20261105.json.gz'):
        payload, err = dt_ns['_twin_or_404'](STAFF, k)
        check(f'Digital Twins: a copy is not found, even for staff: {k[14:44]}',
              payload is None and err[1] == 404 and err[0].get_json().get('error') == 'twin not found' and not twin_loads)
    payload, err = dt_ns['_twin_or_404'](SONY, 'digital-twins/admin/Paramount_20261030_202216_418f0c1d10.json.gz')
    check('Digital Twins: a live twin still opens', err is None and isinstance(payload, dict))

# ============================================================== 6. Prometheus
print('6. Prometheus')
import prometheus_analysis as pma  # noqa: E402
from prometheus import delivered, last_thing, subiq_library  # noqa: E402

s3p = StubS3({'svod-acquisition': list(ALL_KEYS),
              'dashboard-inputs': ['generated_data/ab12cd34ef56/Nike_reach.csv', '_backups/Reba.csv.pre_x.csv']})
with pma._xmod_lock:
    pma._xmod_subiq_index_cache.update(ts=0, index=None, shows=None)
idx = pma._load_subiq_index(s3p, 'svod-acquisition')
shows = pma.list_subiq_shows(s3p, 'svod-acquisition')
check('the tracker index holds only live trackers',
      sorted(k for _s, k in idx.values()) == sorted(LIVE) or all(k in LIVE for _s, k in idx.values()), str(idx))
check('the per-file list holds only live trackers', sorted(k for _s, k, _lm in shows) == sorted(LIVE), str(shows))
hits = pma.match_subiq_shows(s3p, 'svod-acquisition', 'Outlander Blood of My Blood Season 2', limit=5)
check('a title lookup never lands on a copy', hits and all(k in LIVE for _s, k, _lm in hits), str(hits))
with pma._xmod_lock:
    pma._xmod_subiq_index_cache.update(ts=0, index=None, shows=None)

subiq_library._CACHE.update({'at': 0.0, 'bucket': '', 'rows': None})
rows = subiq_library.library(s3p, 'svod-acquisition', can_open=lambda k, n: can(SONY, k, n))
check('the library answer holds only live trackers (one line per show)',
      rows and all(r['key'] in LIVE for r in rows), str([r['key'] for r in rows]))
check('the library read every live tracker and never a copy',
      sorted({k for _b, k in s3p.gets}) == sorted(LIVE), str(s3p.gets))
subiq_library._CACHE.update({'at': 0.0, 'bucket': '', 'rows': None})

n0 = len(s3p.gets)
for k in (INTERNAL[0], INTERNAL[4], '_backups/Reba.csv.pre_x.csv'):
    f = {'s3_key': k, 'filename': k.rsplit('/', 1)[-1], 'bucket': 'dashboard-inputs', 'mime': 'text/csv'}
    blob = last_thing.file_bytes(f, s3p, bucket='dashboard-inputs', extra_buckets=('svod-acquisition',))
    url = last_thing.fresh_url(f, s3p, bucket='dashboard-inputs')
    check(f'email / download this file never hands over a copy: {k[:40]}', blob is None and url == '')
check('and storage was not read for them', len(s3p.gets) == n0)
f = {'s3_key': LIVE[0], 'filename': LIVE[0], 'bucket': 'dashboard-inputs', 'mime': 'text/csv'}
check('a live tracker is still handed over to a reader who can open it',
      last_thing.file_bytes(f, s3p, bucket='dashboard-inputs', extra_buckets=('svod-acquisition',),
                            allowed=lambda kind, key: kind == 'subscriber') == TRACKER)
check('and never without the reader\'s own access check (file_access, 2026-11-18)',
      last_thing.file_bytes(f, s3p, bucket='dashboard-inputs', extra_buckets=('svod-acquisition',)) is None)
f = {'s3_key': 'generated_data/ab12cd34ef56/Nike_reach.csv', 'filename': 'Nike_reach.csv', 'bucket': 'dashboard-inputs'}
check('a delivered data file is still handed over and linked',
      last_thing.file_bytes(f, s3p, bucket='dashboard-inputs') == TRACKER
      and last_thing.fresh_url(f, s3p, bucket='dashboard-inputs').startswith('https://'))

import contextlib  # noqa: E402
import io  # noqa: E402
with contextlib.redirect_stdout(io.StringIO()):
    got_copy = delivered.subiq_tracker_files(INTERNAL[0], s3_client=s3p)
    got_live = delivered.subiq_tracker_files(LIVE[0], s3_client=s3p)
check('a ready note never attaches a copy', got_copy == [])
check('a ready note still attaches its live tracker', len(got_live) == 1 and got_live[0][0] == TRACKER and got_live[0][1] == LIVE[0])
check('Prometheus wrote nothing', not s3p.writes)

# catalog names for the cross-product lookup skip a copy that sits in the catalog
import json as _json  # noqa: E402


class CatS3(StubS3):
    def get_object(self, Bucket, Key, **kw):
        if Key == 'system/s3_cache.json':
            return {'Body': _Body(_json.dumps({'jobs': [
                {'display_name': 'Amber Midthunder', 's3_key': 'Amber_Midthunder_06_05_2026_07_35.csv'},
                {'display_name': 'Amber Midthunder 06 05 2026 07 35.Pre Patch.Bak',
                 's3_key': 'Amber_Midthunder_06_05_2026_07_35.pre_patch.bak.csv'}]}).encode('utf-8'))}
        return super().get_object(Bucket, Key, **kw)


with pma._xmod_lock:
    pma._xmod_catalog_cache.update(ts=0, names=None)
names = pma._load_catalog_names(CatS3({}), 'dashboard-inputs')
with pma._xmod_lock:
    pma._xmod_catalog_cache.update(ts=0, names=None)
check('the profile catalog Prometheus reads skips a copy', [k for _n, k in names] == ['Amber_Midthunder_06_05_2026_07_35.csv'], str(names))

# ===================================================== 7. the words a reader sees
print('7. wording')
check('no refusal names what the key was', all(plain(t) for t in (
    IC.NOT_FOUND, IC.SUBIQ_NOT_FOUND, 'Run not found', 'twin not found')))
check('this file and the module carry no long dashes',
      '\u2014' not in Path(__file__).read_text(encoding='utf-8')
      and '\u2013' not in Path(__file__).read_text(encoding='utf-8')
      and '\u2014' not in (ROOT / 'internal_copies.py').read_text(encoding='utf-8')
      and '\u2013' not in (ROOT / 'internal_copies.py').read_text(encoding='utf-8'))

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
