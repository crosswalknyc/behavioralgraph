#!/usr/bin/env python3
"""The store's document mirror: write-through, parity, the gated read
(Prometheus rebuild stage 6, Jenna 2026-11-20).

  * The three mirrored families are exactly threads and their index
    (system/synth_chat_threads/), generated-read job documents
    (system/prometheus_reads/<job>.json, not the queue / chain / in-flight
    markers under it) and the ledger index.
  * One write seam: the S3 client's put_object is hooked at bind
    (store.install_mirror: botocore's provide-client-params and after-call
    events on a real client, a wrapper on the in-memory one), so every
    writer (the shared helper, the ETag-guarded thread merge on both its
    paths, the read status write, the read worker's claim, the sweeper,
    the ledger index compare-and-set) mirrors the same bytes S3 took,
    after S3 took them; a refused put is not mirrored; a store fault never
    reaches the caller.
  * With the store off nothing changes: no table is touched, the helpers
    behave as before.
  * The read switch (PM_STORE_READ=1): _pm_s3_json, the read status route's
    document read and service.find_job_doc read the mirror first and fall
    through to S3 on a miss; the ledger index read stays on S3 (its ETag
    guards the compare-and-set).
  * Parity on the committed fixtures through the replay harness: every
    mirrored document written is byte-identical in the store, and the read
    pass lands every ask with S3 emptied of the mirrored keys.
  * No S3 document is ever deleted or moved by any of this.

Hermetic: SQLite in a temp file, fake S3, no network, no model.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEB = os.path.dirname(_HERE)
if not os.path.exists(os.path.join(_WEB, 'prometheus', 'store.py')):
    _WEB = os.path.join(_WEB, 'bg-webapp')
if _WEB not in sys.path:
    sys.path.insert(0, _WEB)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.pop('DATABASE_URL', None)
os.environ.pop('PM_STORE_READ', None)

from prometheus import store as ST  # noqa: E402


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.deletes = []

    def put_object(self, Bucket=None, Key=None, Body=b'', **kw):
        if kw.get('IfNoneMatch') == '*' and Key in self.objects:
            raise Exception('PreconditionFailed')
        if kw.get('IfMatch') and f'"{len(self.objects.get(Key, b""))}"'.strip('"') != str(kw['IfMatch']).strip('"'):
            raise Exception('PreconditionFailed')
        self.objects[Key] = bytes(Body)
        self.puts.append(Key)
        return {'ETag': f'"{len(self.objects[Key])}"'}

    def get_object(self, Bucket=None, Key=None, **kw):
        if Key not in self.objects:
            raise Exception('NoSuchKey')
        return {'Body': io.BytesIO(self.objects[Key]), 'ETag': f'"{len(self.objects[Key])}"'}

    def head_object(self, Bucket=None, Key=None, **kw):
        if getattr(self, 'head_fails', False):
            raise Exception('network down')
        if Key not in self.objects:
            raise Exception('NoSuchKey')
        return {'ETag': f'"{len(self.objects[Key])}"'}

    def delete_object(self, **kw):
        self.deletes.append(kw.get('Key'))
        self.objects.pop(kw.get('Key'), None)
        return {}

    def delete_objects(self, Bucket=None, Delete=None, **kw):
        out = {'Deleted': [], 'Errors': []}
        for o in (Delete or {}).get('Objects') or []:
            k = o.get('Key')
            if k == getattr(self, 'refuse', None):
                out['Errors'].append({'Key': k, 'Code': 'AccessDenied'})
                continue
            self.deletes.append(k)
            self.objects.pop(k, None)
            out['Deleted'].append({'Key': k})
        return out

    def list_objects_v2(self, Bucket=None, Prefix='', **kw):
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        items = [{'Key': k, 'LastModified': getattr(self, 'stamps', {}).get(k, now), 'ETag': f'"{len(self.objects[k])}"'}
                 for k in sorted(self.objects) if k.startswith(Prefix)]
        return {'Contents': items, 'IsTruncated': False, 'KeyCount': len(items)}


def _sqlite_on():
    db = tempfile.mktemp(prefix='pm_store_docs_', suffix='.db')
    os.environ['PM_STORE_SQLITE'] = db
    ST.reset_for_tests()
    return db


def _store_off():
    os.environ.pop('PM_STORE_SQLITE', None)
    os.environ.pop('PM_STORE_READ', None)
    ST.reset_for_tests()


class Families(unittest.TestCase):
    def test_exactly_the_three_families(self):
        self.assertEqual(ST.family_of('system/synth_chat_threads/jane/abc123.json'), 'thread')
        self.assertEqual(ST.family_of('system/synth_chat_threads/jane/index.json'), 'thread')
        self.assertEqual(ST.family_of('system/prometheus_reads/0123456789ab.json'), 'read')
        self.assertEqual(ST.family_of('system/insights_ledger/index.json'), 'ledger_index')
        for k in ('system/prometheus_reads/_queue/x.json', 'system/prometheus_reads/_inflight/jane.json',
                  'system/prometheus_reads/_chain/x.json', 'system/insights_ledger/entries/2026-11-20/x.json',
                  'system/prometheus_decks/abc.json', 'system/usage/ask_log/2026-11-20/x.json', 'Reba_McEntire.csv'):
            self.assertIsNone(ST.family_of(k), k)


class StoreOff(unittest.TestCase):
    def setUp(self):
        _store_off()

    def test_nothing_happens(self):
        self.assertFalse(ST.enabled())
        self.assertFalse(ST.read_enabled())
        self.assertFalse(ST.doc_put('system/synth_chat_threads/jane/abc.json', b'[]'))
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/jane/abc.json'))
        self.assertEqual(ST.doc_get('system/synth_chat_threads/jane/abc.json', 'dflt'), 'dflt')
        self.assertEqual(ST.docs_count(), 0)


class WriteThrough(unittest.TestCase):
    def setUp(self):
        self.db = _sqlite_on()
        self.s3 = FakeS3()
        ST.install_mirror(self.s3)          # what legacy.bind does for the host's client

    def tearDown(self):
        _store_off()
        try:
            os.remove(self.db)
        except Exception:
            pass

    def test_the_etag_s3_answered_with_is_kept(self):
        s3 = FakeS3()
        ST.install_mirror(s3)
        s3.put_object(Bucket='b', Key='system/synth_chat_threads/jane/t1.json', Body=b'[1, 2]')
        row = ST._rows(ST._exec('SELECT etag, nbytes FROM pm_docs WHERE key = %s', ('system/synth_chat_threads/jane/t1.json',)))[0]
        self.assertEqual(row['etag'], '6')     # the fake's ETag is the byte count, quotes stripped
        self.assertEqual(row['nbytes'], 6)

    def test_a_dropped_connection_is_reopened_once(self):
        s3 = FakeS3()
        ST.install_mirror(s3)
        s3.put_object(Bucket='b', Key='system/synth_chat_threads/jane/t1.json', Body=b'[1]')
        ST._connect().close()          # the server dropped it under us
        s3.put_object(Bucket='b', Key='system/synth_chat_threads/jane/t2.json', Body=b'[2]')
        self.assertEqual(ST.doc_text('system/synth_chat_threads/jane/t2.json'), '[2]')
        self.assertEqual(ST.docs_count('thread'), 2)

    def test_doc_put_keeps_the_bytes(self):
        body = json.dumps([{'role': 'user', 'text': 'hi'}], indent=2).encode('utf-8')
        self.assertTrue(ST.doc_put('system/synth_chat_threads/jane/abc.json', body))
        self.assertEqual(ST.doc_text('system/synth_chat_threads/jane/abc.json'), body.decode('utf-8'))
        self.assertEqual(ST.doc_get('system/synth_chat_threads/jane/abc.json'), [{'role': 'user', 'text': 'hi'}])
        self.assertFalse(ST.doc_put('system/prometheus_reads/_queue/x.json', b'{}'))     # a marker is not mirrored
        self.assertEqual(ST.docs_count('thread'), 1)

    def test_the_shared_helper_writes_through(self):
        from prometheus.legacy import watch as W, bind
        host = type('H', (), {})()
        host.s3_client = self.s3
        host.S3_BUCKET = 'dashboard-inputs'
        bind(host)
        key = 'system/synth_chat_threads/jane/index.json'
        W._pm_s3_put_json(key, {'threads': [], 'active': None})
        self.assertEqual(self.s3.objects[key].decode('utf-8'), ST.doc_text(key))
        W._pm_s3_put_json('system/insights_ledger/entries/2026-11-20/x.json', {'a': 1})   # not a family: S3 only
        self.assertIsNone(ST.doc_text('system/insights_ledger/entries/2026-11-20/x.json'))
        self.assertEqual(ST.parity(self.s3.objects)['different'] + ST.parity(self.s3.objects)['only_s3'], 0)

    def test_the_thread_merge_writes_through_on_both_paths(self):
        from prometheus import thread_merge as TM
        key = 'system/synth_chat_threads/jane/t1.json'
        turns = [{'role': 'user', 'text': 'who watches Reba McEntire', 'ts': '2026-11-20T10:00:00Z'}]
        TM.merge_save(self.s3, 'dashboard-inputs', key, turns)
        self.assertEqual(self.s3.objects[key].decode('utf-8'), ST.doc_text(key))
        more = turns + [{'role': 'agent', 'text': 'On Reba McEntire: 4.2M.', 'ts': '2026-11-20T10:00:05Z'}]
        TM.merge_save(self.s3, 'dashboard-inputs', key, more)
        self.assertEqual(self.s3.objects[key].decode('utf-8'), ST.doc_text(key))
        self.assertEqual(len(json.loads(ST.doc_text(key))), 2)
        # the final unguarded put (every guarded attempt raced) goes through the same client hook
        TM._SUPPORTS_CONDITIONAL_PUT = False
        try:
            TM.merge_save(self.s3, 'dashboard-inputs', key, more + [{'role': 'user', 'text': 'and again', 'ts': '2026-11-20T10:01:00Z'}])
        finally:
            TM._SUPPORTS_CONDITIONAL_PUT = True
        self.assertEqual(self.s3.objects[key].decode('utf-8'), ST.doc_text(key))

    def test_the_read_status_write_and_the_ledger_index_write_through(self):
        root = os.path.dirname(_WEB)
        if not os.path.exists(os.path.join(root, 'scripts', '_pm_behavior.py')):
            self.skipTest('the behavior harness (parent scripts/_pm_behavior.py) is not beside this checkout')
        sys.path.insert(0, os.path.join(root, 'scripts'))
        from _pm_behavior import load_chat
        h = load_chat()
        s3 = h.s3
        h.chat._pm_read_status_write('0123456789ab', {'job_id': '0123456789ab', 'user': 'jane', 'status': 'working'})
        key = 'system/prometheus_reads/0123456789ab.json'
        self.assertIn(key, s3.objects)
        self.assertEqual(s3.objects[key].decode('utf-8'), ST.doc_text(key))
        import insights_ledger as IL
        fake = self.s3
        IL._client = lambda: fake
        IL.S3_BUCKET = 'dashboard-inputs'
        ok = IL._put_index_cas({'subjects': {'reba mcentire': {'entries': []}}}, None)
        self.assertTrue(ok)
        self.assertEqual(fake.objects[IL.INDEX_KEY].decode('utf-8'), ST.doc_text(IL.INDEX_KEY))
        self.assertEqual(ST.family_of(IL.INDEX_KEY), 'ledger_index')
        self.assertEqual(fake.deletes, [])

    def test_the_botocore_hooks_mirror_only_a_successful_put(self):
        ctx = {}
        key = 'system/prometheus_reads/0123456789ab.json'
        ST._provide_params({'Bucket': 'b', 'Key': key, 'Body': b'{"status": "done"}'}, context=ctx)
        self.assertIn(ST._CTX, ctx)
        ST._after_call(http_response=type('R', (), {'status_code': 412})(), parsed={}, context=ctx)
        self.assertIsNone(ST.doc_text(key))                     # a refused put is not mirrored
        ST._provide_params({'Bucket': 'b', 'Key': key, 'Body': b'{"status": "done"}'}, context=ctx)
        ST._after_call(http_response=type('R', (), {'status_code': 200})(), parsed={}, context=ctx)
        self.assertEqual(ST.doc_get(key), {'status': 'done'})
        ST._provide_params({'Bucket': 'b', 'Key': 'system/usage/x.json', 'Body': b'{}'}, context=ctx)
        self.assertNotIn(ST._CTX, ctx)                          # not a family: nothing carried
        ST._provide_params({'Bucket': 'b', 'Key': key, 'Body': io.BytesIO(b'{}')}, context=ctx)
        self.assertNotIn(ST._CTX, ctx)                          # a file-like body is not mirrored

    def test_a_store_fault_never_reaches_the_caller(self):
        from prometheus.legacy import watch as W, bind
        host = type('H', (), {})()
        host.s3_client = self.s3
        host.S3_BUCKET = 'dashboard-inputs'
        bind(host)
        os.environ['PM_STORE_SQLITE'] = '/nonexistent/dir/x.db'
        ST.reset_for_tests()
        key = 'system/synth_chat_threads/jane/t9.json'
        W._pm_s3_put_json(key, [])                     # S3 written, the store fault counted
        self.assertIn(key, self.s3.objects)
        self.assertEqual(self.s3.deletes, [])


class TheReadSwitch(unittest.TestCase):
    def setUp(self):
        self.db = _sqlite_on()
        self.s3 = FakeS3()
        from prometheus.legacy import bind
        host = type('H', (), {})()
        host.s3_client = self.s3
        host.S3_BUCKET = 'dashboard-inputs'
        bind(host)

    def tearDown(self):
        _store_off()
        try:
            os.remove(self.db)
        except Exception:
            pass

    def test_off_by_default_and_s3_is_read(self):
        from prometheus.legacy import watch as W
        key = 'system/synth_chat_threads/jane/t1.json'
        W._pm_s3_put_json(key, [{'role': 'user', 'text': 'a'}])
        self.s3.objects[key] = json.dumps([{'role': 'user', 'text': 'changed in s3'}]).encode('utf-8')
        self.assertFalse(ST.read_enabled())
        self.assertEqual(W._pm_s3_json(key, [])[0]['text'], 'changed in s3')

    def test_on_the_mirror_is_read_first_with_s3_as_the_fallback(self):
        from prometheus.legacy import watch as W
        key = 'system/synth_chat_threads/jane/t1.json'
        W._pm_s3_put_json(key, [{'role': 'user', 'text': 'from the mirror'}])
        os.environ['PM_STORE_READ'] = '1'
        self.assertTrue(ST.read_enabled())
        # checked read (the default): the mirror serves while its ETag is S3's current one
        self.assertEqual(W._pm_s3_json(key, [])[0]['text'], 'from the mirror')
        self.assertGreaterEqual(ST.STATS['doc_hit'], 1)
        # the mirror as it stands (PM_STORE_READ_VALIDATE=0): S3 has nothing; the mirror still serves it
        os.environ['PM_STORE_READ_VALIDATE'] = '0'
        del self.s3.objects[key]
        self.assertEqual(W._pm_s3_json(key, [])[0]['text'], 'from the mirror')
        os.environ.pop('PM_STORE_READ_VALIDATE')
        # checked again: S3 no longer has it, so neither does the reader
        self.assertEqual(W._pm_s3_json(key, []), [])
        other = 'system/synth_chat_threads/jane/only_in_s3.json'
        self.s3.objects[other] = b'[{"role": "user", "text": "only s3"}]'
        self.assertEqual(W._pm_s3_json(other, [])[0]['text'], 'only s3')   # a miss falls through
        self.assertEqual(W._pm_s3_json('system/usage/x.json', 'dflt'), 'dflt')   # not a family: S3 only, default on a miss
        self.assertGreaterEqual(ST.STATS['doc_hit'], 1)

    def test_find_job_doc_reads_the_mirror_first(self):
        from prometheus import service as SV
        from prometheus.host import host
        key = 'system/prometheus_reads/0123456789ab.json'
        body = json.dumps({'job_id': '0123456789ab', 'status': 'done'}).encode('utf-8')
        self.s3.put_object(Bucket='dashboard-inputs', Key=key, Body=body)      # through the seam: S3 and the mirror agree
        os.environ['PM_STORE_READ'] = '1'
        host.bind(s3_client=self.s3, bucket='dashboard-inputs', read_prefix='system/prometheus_reads/')
        hits = ST.STATS['doc_hit']
        doc, kind = SV.find_job_doc('0123456789ab')
        self.assertEqual((doc or {}).get('status'), 'done')
        self.assertEqual(kind, 'read')
        self.assertEqual(ST.STATS['doc_hit'], hits + 1)             # served from the mirror, checked against S3's ETag
        os.environ['PM_STORE_READ_VALIDATE'] = '0'
        del self.s3.objects[key]
        doc_m, _k = SV.find_job_doc('0123456789ab')
        self.assertEqual((doc_m or {}).get('status'), 'done')      # the mirror as it stands serves it with S3 empty
        os.environ.pop('PM_STORE_READ_VALIDATE')
        os.environ.pop('PM_STORE_READ', None)
        doc2, _k = SV.find_job_doc('0123456789ab')
        self.assertIsNone(doc2)                                    # switch off: S3 only, and S3 has nothing


class ParityOnTheFixtures(unittest.TestCase):
    def test_every_written_document_is_byte_identical_and_the_read_pass_lands(self):
        root = os.path.dirname(_WEB)
        if not os.path.exists(os.path.join(root, 'scripts', '_pm_behavior.py')):
            self.skipTest('the behavior harness (parent scripts/_pm_behavior.py) is not beside this checkout')
        env = dict(os.environ, REGRESSION_TEST_MODE='1')
        env.pop('PM_STORE_SQLITE', None)
        p = subprocess.run([sys.executable, os.path.join(_HERE, 'pm_store_parity.py'), '--fixtures'],
                           capture_output=True, text=True, env=env, cwd=_WEB, timeout=600)
        out = p.stdout + p.stderr
        self.assertIn('PARITY OK', out, out[-2000:])
        self.assertIn('0 different, 0 missing from the store', out)
        import re
        m = re.search(r'read pass .*?: (\d+) of (\d+) asks landed a reply, (\d+) reads served from the mirror', out)
        self.assertIsNotNone(m, out[-800:])
        self.assertEqual(m.group(1), m.group(2))
        self.assertGreater(int(m.group(3)), 0)


class ParityInProduction(unittest.TestCase):
    """store.production_parity: the mirror against the live bucket from
    inside a running process, and the ops routes in front of it."""

    def setUp(self):
        _sqlite_on()
        self.s3 = FakeS3()
        ST.install_mirror(self.s3)

    def tearDown(self):
        _store_off()

    def _write(self, key, body):
        self.s3.put_object(Bucket='b', Key=key, Body=body)

    def test_same_different_only_s3_only_store_and_older(self):
        import datetime as _dt
        self._write('system/synth_chat_threads/jane/t1.json', b'[1]')
        self._write('system/synth_chat_threads/jane/index.json', b'{"a": 1}')
        self._write('system/prometheus_reads/0123456789ab.json', b'{"status": "queued"}')
        self._write('system/insights_ledger/index.json', b'{"rows": []}')
        # a writer outside this process changed one and added one
        self.s3.objects['system/prometheus_reads/0123456789ab.json'] = b'{"status": "done"}'
        self.s3.objects['system/synth_chat_threads/bob/t9.json'] = b'[9]'
        # a markers key under the reads prefix is not a family and is ignored
        self.s3.objects['system/prometheus_reads/_queue/0123456789ab.json'] = b'{}'
        # a document S3 held before the mirror started is older, not checked
        self.s3.objects['system/synth_chat_threads/old/t0.json'] = b'[0]'
        self.s3.stamps = {'system/synth_chat_threads/old/t0.json': _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)}
        # a document the mirror holds that S3 no longer lists
        ST.doc_put('system/synth_chat_threads/gone/t2.json', b'[2]')
        out = ST.production_parity(self.s3, 'b', settle_s=0)
        self.assertTrue(out['enabled'])
        self.assertEqual(out['rows'], 5)
        self.assertEqual(out['listed'], 6)
        self.assertEqual(out['older'], 1)
        self.assertEqual(out['checked'], 4)
        self.assertEqual(out['same'], 3)
        self.assertEqual(out['different'], 1)
        self.assertEqual(out['only_s3'], 1)
        self.assertEqual(out['only_store'], 1)
        self.assertEqual([e['key'] for e in out['examples']['different']], ['system/prometheus_reads/0123456789ab.json'])
        self.assertEqual(out['examples']['different'][0]['store_bytes'], len(b'{"status": "queued"}'))
        self.assertEqual(out['examples']['different'][0]['s3_bytes'], len(b'{"status": "done"}'))
        self.assertTrue(out['examples']['different'][0]['store_updated'])
        self.assertEqual([e['key'] for e in out['examples']['only_s3']], ['system/synth_chat_threads/bob/t9.json'])
        self.assertEqual([e['key'] for e in out['examples']['only_store']], ['system/synth_chat_threads/gone/t2.json'])
        self.assertEqual(out['by_family']['read']['different'], 1)
        self.assertEqual(out['by_family']['ledger_index']['same'], 1)
        self.assertFalse(out['truncated'])
        self.assertNotIn('body', json.dumps(out).lower().replace('"by_family"', ''))

    def test_the_limit_truncates_the_gets_not_the_listing(self):
        for i in range(5):
            self._write(f'system/synth_chat_threads/u/t{i}.json', b'[1]')
        ST._exec('UPDATE pm_docs SET etag = NULL')          # no ETag to compare: every row needs its body read
        out = ST.production_parity(self.s3, 'b', limit=2, settle_s=0)
        self.assertTrue(out['truncated'])
        self.assertEqual(out['checked'], 2)
        self.assertEqual(out['listed'], 5)

    def test_store_off_says_so(self):
        _store_off()
        out = ST.production_parity(self.s3, 'b')
        self.assertFalse(out['enabled'])
        self.assertEqual(out['rows'], 0)

    def test_the_ops_routes_answer_only_to_the_secret(self):
        from flask import Flask
        from prometheus import store_ops as SO
        self._write('system/synth_chat_threads/jane/t1.json', b'[1]')
        app = Flask('t')
        app.register_blueprint(SO.bp)
        SO._s3_and_bucket = lambda: (self.s3, 'b')
        os.environ['CRON_SECRET'] = 'shh-test'
        try:
            c = app.test_client()
            self.assertEqual(c.get('/api/prometheus/ops/store-status').status_code, 404)
            self.assertEqual(c.get('/api/prometheus/ops/store-parity', headers={'X-Ops-Secret': 'wrong'}).status_code, 404)
            st = c.get('/api/prometheus/ops/store-status', headers={'X-Ops-Secret': 'shh-test'}).get_json()
            self.assertTrue(st['enabled'])
            self.assertFalse(st['read_enabled'])
            self.assertEqual(st['rows'], 1)
            self.assertEqual(st['by_family']['thread'], 1)
            par = c.get('/api/prometheus/ops/store-parity?settle=0', headers={'X-Ops-Secret': 'shh-test'}).get_json()
            self.assertEqual((par['checked'], par['same'], par['different']), (1, 1, 0))
            os.environ.pop('CRON_SECRET')
            self.assertEqual(c.get('/api/prometheus/ops/store-status', headers={'X-Ops-Secret': 'shh-test'}).status_code, 404)
        finally:
            os.environ.pop('CRON_SECRET', None)


class DeletesAreMirrored(unittest.TestCase):
    """Jenna 2026-11-20: every S3 delete of a mirrored family removes the
    row in the same seam, so a deleted thread never lingers."""

    def setUp(self):
        _sqlite_on()
        self.s3 = FakeS3()
        ST.install_mirror(self.s3)

    def tearDown(self):
        _store_off()

    def test_delete_object_and_delete_objects_through_the_plain_seam(self):
        for k in ('system/synth_chat_threads/u/a.json', 'system/synth_chat_threads/u/b.json', 'system/synth_chat_threads/u/c.json', 'system/synth_chat_threads/u/index.json'):
            self.s3.put_object(Bucket='b', Key=k, Body=b'[1]')
        self.assertEqual(ST.docs_count('thread'), 4)
        self.s3.delete_object(Bucket='b', Key='system/synth_chat_threads/u/a.json')
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/u/a.json'))
        self.assertEqual(ST.docs_count('thread'), 3)
        self.s3.refuse = 'system/synth_chat_threads/u/c.json'
        self.s3.delete_objects(Bucket='b', Delete={'Objects': [{'Key': 'system/synth_chat_threads/u/b.json'}, {'Key': 'system/synth_chat_threads/u/c.json'}, {'Key': 'system/prometheus_reads/_queue/x.json'}]})
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/u/b.json'))
        self.assertEqual(ST.doc_text('system/synth_chat_threads/u/c.json'), '[1]', 'a delete S3 refused keeps its row')
        self.assertEqual(ST.STATS['doc_delete'] >= 2, True)
        # a delete outside the families touches nothing and never raises
        self.s3.delete_object(Bucket='b', Key='system/prometheus_reads/_queue/y.json')
        self.assertEqual(ST.docs_count('thread'), 2)

    def test_the_botocore_delete_hooks(self):
        import botocore.session
        from botocore.stub import Stubber
        client = botocore.session.get_session().create_client('s3', region_name='us-east-2', aws_access_key_id='x', aws_secret_access_key='y')
        ST.install_mirror(client)
        ST.doc_put('system/synth_chat_threads/u/t.json', b'[1]')
        ST.doc_put('system/synth_chat_threads/u/t2.json', b'[2]')
        ST.doc_put('system/synth_chat_threads/u/t3.json', b'[3]')
        with Stubber(client) as stub:
            stub.add_response('delete_object', {}, {'Bucket': 'b', 'Key': 'system/synth_chat_threads/u/t.json'})
            stub.add_response('delete_objects', {'Deleted': [{'Key': 'system/synth_chat_threads/u/t2.json'}], 'Errors': [{'Key': 'system/synth_chat_threads/u/t3.json', 'Code': 'AccessDenied', 'Message': 'no'}]},
                              {'Bucket': 'b', 'Delete': {'Objects': [{'Key': 'system/synth_chat_threads/u/t2.json'}, {'Key': 'system/synth_chat_threads/u/t3.json'}]}})
            client.delete_object(Bucket='b', Key='system/synth_chat_threads/u/t.json')
            client.delete_objects(Bucket='b', Delete={'Objects': [{'Key': 'system/synth_chat_threads/u/t2.json'}, {'Key': 'system/synth_chat_threads/u/t3.json'}]})
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/u/t.json'))
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/u/t2.json'))
        self.assertEqual(ST.doc_text('system/synth_chat_threads/u/t3.json'), '[3]')

    def test_the_store_never_deletes_from_s3(self):
        src = open(os.path.join(_WEB, 'prometheus', 'store.py'), encoding='utf-8').read()
        self.assertNotIn('s3.delete_object(', src)
        self.assertNotIn('delete_objects(Bucket', src)


class TheReconciler(unittest.TestCase):
    """store.reconcile: S3 always wins, silently; a document either side
    touched in the last two minutes waits for the next pass."""

    def setUp(self):
        _sqlite_on()
        self.s3 = FakeS3()
        ST.install_mirror(self.s3)

    def tearDown(self):
        _store_off()

    def _old(self, key):
        import datetime as _dt
        self.s3.stamps = getattr(self.s3, 'stamps', {})
        self.s3.stamps[key] = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)

    def test_same_added_replaced_etag_filled_removed(self):
        import datetime as _dt
        # same: written through the seam, etag recorded
        self.s3.put_object(Bucket='b', Key='system/synth_chat_threads/u/same.json', Body=b'[1]')
        # added: S3 has it, the mirror never saw it (an older writer)
        self.s3.objects['system/synth_chat_threads/u/added.json'] = b'[2]'
        # replaced: the mirror is stale
        self.s3.put_object(Bucket='b', Key='system/prometheus_reads/0123456789ab.json', Body=b'{"status": "queued"}')
        self.s3.objects['system/prometheus_reads/0123456789ab.json'] = b'{"status": "done"}'
        # etag_filled: the bytes match but the row carries no etag (a row from before the column)
        self.s3.objects['system/synth_chat_threads/u/noetag.json'] = b'[4]'
        ST.doc_put('system/synth_chat_threads/u/noetag.json', b'[4]')
        # removed: the mirror holds what S3 no longer lists
        ST.doc_put('system/synth_chat_threads/gone/t.json', b'[5]')
        for k in list(self.s3.objects):
            self._old(k)
        # everything the mirror wrote is "fresh" by updated_at; age the rows
        ST._exec("UPDATE pm_docs SET updated_at = %s", ('2026-01-01T00:00:00Z',))
        out = ST.reconcile(self.s3, 'b')
        self.assertEqual((out['same'], out['added'], out['replaced'], out['etag_filled'], out['removed'], out['settling']), (1, 1, 1, 1, 1, 0), out)
        self.assertEqual(ST.doc_text('system/prometheus_reads/0123456789ab.json'), '{"status": "done"}')
        self.assertEqual(ST.doc_text('system/synth_chat_threads/u/added.json'), '[2]')
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/gone/t.json'))
        row = ST._rows(ST._exec('SELECT etag FROM pm_docs WHERE key = %s', ('system/synth_chat_threads/u/noetag.json',)))[0]
        self.assertEqual(row['etag'], '3')
        # a second pass finds nothing to do
        out2 = ST.reconcile(self.s3, 'b')
        self.assertEqual((out2['added'], out2['replaced'], out2['etag_filled'], out2['removed']), (0, 0, 0, 0), out2)
        self.assertEqual(out2['same'], 4)

    def test_a_fresh_document_waits(self):
        self.s3.objects['system/synth_chat_threads/u/new.json'] = b'[1]'     # just written by someone else
        ST.doc_put('system/synth_chat_threads/u/going.json', b'[2]')          # just mirrored; S3 has no object (yet)
        out = ST.reconcile(self.s3, 'b')
        self.assertEqual(out['settling'], 2)
        self.assertEqual((out['added'], out['removed']), (0, 0))
        self.assertIsNone(ST.doc_text('system/synth_chat_threads/u/new.json'))

    def test_the_sweeper_lock_and_marker(self):
        from prometheus import store_sweep as SW
        import datetime as _dt
        self.assertTrue(SW._claim(self.s3, 'b', 'a'))
        self.assertFalse(SW._claim(self.s3, 'b', 'b'), 'a fresh lock is held')
        # a stale lock is taken over
        self.s3.objects[SW.LOCK_KEY] = json.dumps({'owner': 'a', 'at': 1.0}).encode('utf-8')
        self.assertTrue(SW._claim(self.s3, 'b', 'b'))
        SW._release(self.s3, 'b')
        self.assertNotIn(SW.LOCK_KEY, self.s3.objects)
        # due only in the hour and once a day
        hour = _dt.datetime(2026, 11, 21, SW.HOUR_UTC, 10, tzinfo=_dt.timezone.utc)
        self.assertTrue(SW.due(self.s3, 'b', now=hour))
        self.assertFalse(SW.due(self.s3, 'b', now=hour.replace(hour=(SW.HOUR_UTC + 3) % 24)))
        out = SW.run_once(self.s3, 'b', owner='t', force=True)
        self.assertIsInstance(out, dict)
        self.assertIn(SW.MARKER_KEY, self.s3.objects)
        self.assertNotIn(SW.LOCK_KEY, self.s3.objects)
        marker = json.loads(self.s3.objects[SW.MARKER_KEY])
        self.assertEqual(marker['day'], SW._today())
        self.assertIn('counts', marker)
        # the marker and lock are system keys, never a mirrored family
        self.assertIsNone(ST.family_of(SW.MARKER_KEY))
        self.assertIsNone(ST.family_of(SW.LOCK_KEY))
        self.assertIsNone(SW.run_once(self.s3, 'b', owner='t'), 'not due now')
        self.assertIsNone(SW.start(object()), 'never a thread under REGRESSION_TEST_MODE')

    def test_the_reconcile_route(self):
        from flask import Flask
        from prometheus import store_ops as SO
        self.s3.objects['system/synth_chat_threads/u/added.json'] = b'[2]'
        self._old('system/synth_chat_threads/u/added.json')
        app = Flask('t')
        app.register_blueprint(SO.bp)
        SO._s3_and_bucket = lambda: (self.s3, 'b')
        os.environ['CRON_SECRET'] = 'shh-test'
        try:
            c = app.test_client()
            self.assertEqual(c.post('/api/prometheus/ops/store-reconcile').status_code, 404)
            out = c.post('/api/prometheus/ops/store-reconcile', headers={'X-Ops-Secret': 'shh-test'}).get_json()
            self.assertTrue(out['ran'])
            self.assertEqual(out['added'], 1)
            st = c.get('/api/prometheus/ops/store-status', headers={'X-Ops-Secret': 'shh-test'}).get_json()
            self.assertIn('faults', st)
            self.assertIn('pid', st)
        finally:
            os.environ.pop('CRON_SECRET', None)


class ForkSafety(unittest.TestCase):
    def test_a_connection_from_another_process_is_not_reused(self):
        _sqlite_on()
        try:
            c1 = ST._connect()
            ST._conn_pid = -1          # as if the parent opened it before the fork
            c2 = ST._connect()
            self.assertIsNot(c1, c2)
            self.assertEqual(ST._conn_pid, os.getpid())
            self.assertTrue(ST.doc_put('system/synth_chat_threads/u/t.json', b'[1]'))
        finally:
            _store_off()


class TheEngineWorkerCarriesTheHook(unittest.TestCase):
    def test_source(self):
        p = os.path.join(os.path.dirname(_WEB), 'migration', 'synth_queue_worker.py')
        if not os.path.exists(p):
            self.skipTest('parent checkout not beside bg-webapp')
        src = open(p, encoding='utf-8').read()
        self.assertIn('_pm_store.install_mirror()', src)
        self.assertIn('sys.path.append(_bgw_for_store)', src, 'bg-webapp goes last on the path')
        head = src[:src.index('_pm_store.install_mirror()')]
        self.assertIn('try:', head[-700:])


class TheCheckedRead(unittest.TestCase):
    """doc_read under the switch: the mirror is served when its ETag is S3's
    current ETag; a step behind reads S3 and heals the row; a document S3
    no longer has drops the row; a HEAD that fails hands the read back."""

    def setUp(self):
        _sqlite_on()
        os.environ['PM_STORE_READ'] = '1'
        self.s3 = FakeS3()
        ST.install_mirror(self.s3)
        self.k = 'system/synth_chat_threads/u/t.json'
        for k in ST.STATS:
            ST.STATS[k] = 0

    def tearDown(self):
        os.environ.pop('PM_STORE_READ_VALIDATE', None)
        _store_off()

    def test_hit_heal_gone_and_head_failure(self):
        self.s3.put_object(Bucket='b', Key=self.k, Body=b'[1]')
        self.assertEqual(ST.doc_read(self.k, self.s3, 'b'), [1])
        self.assertEqual(ST.STATS['doc_hit'], 1)
        # another writer moved S3 on without the mirror (different length -> different fake ETag)
        self.s3.objects[self.k] = b'[1, 2, 3]'
        self.assertEqual(ST.doc_read(self.k, self.s3, 'b'), [1, 2, 3])
        self.assertEqual(ST.STATS['doc_heal'], 1)
        self.assertEqual(ST.doc_text(self.k), '[1, 2, 3]', 'the row was corrected on the spot')
        before = ST.STATS['doc_hit']
        self.assertEqual(ST.doc_read(self.k, self.s3, 'b'), [1, 2, 3])
        self.assertEqual(ST.STATS['doc_hit'], before + 1)
        # S3 no longer has it: the row goes and the caller sees the default
        del self.s3.objects[self.k]
        self.assertIsNone(ST.doc_read(self.k, self.s3, 'b'))
        self.assertEqual(ST.STATS['doc_gone'], 1)
        self.assertIsNone(ST.doc_text(self.k))
        # a HEAD that fails for another reason: the default, the caller reads S3
        self.s3.put_object(Bucket='b', Key=self.k, Body=b'[9]')
        self.s3.head_fails = True
        self.assertEqual(ST.doc_read(self.k, self.s3, 'b', default='fallback'), 'fallback')
        self.s3.head_fails = False
        # validation off: the mirror as it stands, no HEAD
        os.environ['PM_STORE_READ_VALIDATE'] = '0'
        self.s3.objects[self.k] = b'[9, 9]'
        self.assertEqual(ST.doc_read(self.k, self.s3, 'b'), [9])
        # a key outside the families or the switch off: the default
        self.assertIsNone(ST.doc_read('system/prometheus_reads/_queue/x.json', self.s3, 'b'))
        os.environ.pop('PM_STORE_READ')
        self.assertIsNone(ST.doc_read(self.k, self.s3, 'b'))

    def test_the_three_seams_use_the_checked_read(self):
        for rel in ('prometheus/legacy/watch.py', 'prometheus/legacy/jobs.py', 'prometheus/service.py'):
            src = open(os.path.join(_WEB, rel), encoding='utf-8').read()
            self.assertIn('doc_read(', src, rel)
            self.assertNotRegex(src, r"read_enabled\(\)[\s\S]{0,200}doc_get\(", rel)

    def test_parity_settles_and_compares_by_etag(self):
        self.s3.put_object(Bucket='b', Key=self.k, Body=b'[1]')          # mirrored, fresh
        out = ST.production_parity(self.s3, 'b')
        self.assertEqual(out['settling'], 1)
        self.assertEqual((out['checked'], out['different']), (0, 0))
        import datetime as _dt
        self.s3.stamps = {self.k: _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)}
        ST._exec("UPDATE pm_docs SET updated_at = %s", ('2026-01-01T00:00:00Z',))
        out = ST.production_parity(self.s3, 'b', since='2000-01-01T00:00:00Z')
        self.assertEqual((out['checked'], out['same'], out['settling']), (1, 1, 0), out)


class TheHookIsRegisteredBeforeTheFirstClient(unittest.TestCase):
    def test_app_registers_the_default_session_hook_before_any_client(self):
        src = open(os.path.join(_WEB, 'app.py'), encoding='utf-8').read()
        reg = src.index('_pm_store_early.install_mirror()')
        first_client = src.index('boto3.client(')
        self.assertLess(reg, first_client, 'the default-session hook must precede the first S3 client')
        self.assertIn('sys.path.insert(0, _APP_DIR)', src[:reg])

    def test_a_client_made_after_the_bare_install_carries_the_hook(self):
        _sqlite_on()
        try:
            import boto3
            ST.install_mirror()                                   # no client: the default session only
            c = boto3.client('s3', region_name='us-east-2', aws_access_key_id='x', aws_secret_access_key='y')
            ids = set(getattr(c.meta.events, '_emitter', c.meta.events)._unique_id_handlers.keys())
            self.assertTrue(any('pm-store-mirror' in str(u) for u in ids))
            self.assertTrue(any('del-after-DeleteObjects' in str(u) for u in ids))
        finally:
            _store_off()


if __name__ == '__main__':
    unittest.main(verbosity=1)
