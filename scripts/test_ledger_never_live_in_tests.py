#!/usr/bin/env python3
"""A test process never reads or writes the live insights ledger
(2026-11-12).

The nightly on the build server runs every suite with the machine's own
credentials in reach (its runner passes the environment through), and a
suite run by hand from a shell with the dashboard's environment loaded
has them too. The ledger client made a real S3 client whenever one could
be made, so any test that reached the ledger through the door without
handing it a store read the live index: its result moved with whatever
readers had banked that day, and a write would have gone into the
document every reader's answers are checked against.

Holds, with credentials present in the environment and a real
`boto3.client` that fails the test if the ledger asks it for a client:
  1. the client is never a real S3 client; it starts as an empty ledger;
  2. a write lands in the process's own ledger and is read back from it;
  3. a store a test hands over is the one used (`_s3 = fake`,
     `use_test_ledger(doc)`, a replaced `boto3.client`, the fixture file);
  4. a real client made before the mode was set is dropped, not used;
  5. outside a test process nothing changed.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')
# credentials are present: the ledger must not care (these are not real ones)
os.environ['AWS_ACCESS_KEY_ID'] = 'AKIAEXAMPLEEXAMPLE00'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'example/not/a/real/secret/key/0000000000'
os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-2')

import boto3  # noqa: E402
import insights_ledger as IL  # noqa: E402

ENTRY = {'subject': 'Test Subject Alpha', 'family': 'audience', 'q': 'how big is it', 'route': 'reasoned_metrics',
         'metrics': [{'name': 'us_people', 'label': 'US people', 'value': 1234567, 'unit': 'people'}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._real_client = boto3.client
        self.asked = []

        def no_client(*a, **k):
            self.asked.append(a)
            raise AssertionError('the ledger asked for a real S3 client inside a test process')
        no_client.__module__ = 'boto3'            # stands where the real function stands
        boto3.client = no_client
        IL._s3 = None
        IL._index_cache.update(ts=0.0, doc=None)
        os.environ['REGRESSION_TEST_MODE'] = '1'
        os.environ.pop(IL.TEST_LEDGER_FIXTURE_ENV, None)

    def tearDown(self):
        boto3.client = self._real_client
        IL._s3 = None
        IL._index_cache.update(ts=0.0, doc=None)
        os.environ['REGRESSION_TEST_MODE'] = '1'
        os.environ.pop(IL.TEST_LEDGER_FIXTURE_ENV, None)


class TheLedgerIsNeverLiveInATest(_Base):
    def test_it_starts_empty_and_never_asks_for_a_real_client(self):
        c = IL._client()
        self.assertIsInstance(c, IL._TestLedger)
        self.assertFalse(IL._is_live_client(c))
        self.assertIsNone(IL._read_index_with_etag()[0])
        doc = IL._load_index(force=True)
        self.assertEqual((doc or {}).get('subjects') or {}, {})
        self.assertEqual(IL.consult('how big is the Reba McEntire audience', 'Reba McEntire'), IL.consult('x', 'Reba McEntire'))
        self.assertEqual(self.asked, [])

    def test_a_write_stays_in_the_process_and_reads_back(self):
        calls = []

        def mutate(doc):
            calls.append(1)
            doc.setdefault('subjects', {})['test_subject_alpha'] = {'subject': 'Test Subject Alpha', 'entries': [dict(ENTRY)]}
            return doc
        IL._update_index(mutate)
        self.assertEqual(calls, [1])
        doc = IL._load_index(force=True)
        self.assertIn('test_subject_alpha', doc.get('subjects') or {})
        store = IL._client()
        self.assertIsInstance(store, IL._TestLedger)
        self.assertIn(IL.INDEX_KEY, store.objects)
        # the conditional put the index update relies on holds: a stale tag is refused
        self.assertFalse(IL._put_index_cas({'subjects': {}}, '"stale"'))
        self.assertTrue(IL._put_index_cas({'subjects': {}}, store.objects[IL.INDEX_KEY][1]))
        self.assertEqual(self.asked, [])

    def test_a_store_a_test_hands_over_is_the_one_used(self):
        class Fake:
            def __init__(self):
                self.gets = []

            def get_object(self, Bucket=None, Key=None, **kw):
                self.gets.append(Key)
                return {'Body': io.BytesIO(json.dumps({'subjects': {'handed': {'subject': 'Handed', 'entries': []}}}).encode()), 'ETag': '"1"'}
        fake = Fake()
        IL._s3 = fake
        self.assertIs(IL._client(), fake)
        self.assertIn('handed', IL._load_index(force=True)['subjects'])
        self.assertEqual(fake.gets, [IL.INDEX_KEY])
        # the helper
        store = IL.use_test_ledger({'subjects': {'given': {'subject': 'Given', 'entries': []}}})
        self.assertIs(IL._client(), store)
        self.assertIn('given', IL._load_index(force=True)['subjects'])
        self.assertEqual(IL.use_test_ledger().objects[IL.INDEX_KEY][0], b'{}')
        # a test that replaced boto3.client with its own store gets that store
        IL._s3 = None
        boto3.client = lambda *a, **k: fake
        self.assertIs(IL._client(), fake)

    def test_the_fixture_file(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            json.dump({'subjects': {'from_fixture': {'subject': 'From Fixture', 'entries': []}}}, fh)
        try:
            os.environ[IL.TEST_LEDGER_FIXTURE_ENV] = fh.name
            self.assertIn('from_fixture', IL._load_index(force=True)['subjects'])
            os.environ[IL.TEST_LEDGER_FIXTURE_ENV] = fh.name + '.missing'
            IL._s3 = None
            self.assertEqual((IL._load_index(force=True) or {}).get('subjects') or {}, {})
        finally:
            os.unlink(fh.name)

    def test_a_real_client_made_earlier_is_dropped(self):
        real = self._real_client('s3', region_name='us-east-2')          # made, never called
        self.assertTrue(IL._is_live_client(real))
        IL._s3 = real
        c = IL._client()
        self.assertIsNot(c, real)
        self.assertIsInstance(c, IL._TestLedger)
        # and a replaced boto3.client that still hands back a real client is not trusted either
        IL._s3 = None
        boto3.client = lambda *a, **k: real
        self.assertIsInstance(IL._client(), IL._TestLedger)

    def test_outside_a_test_process_nothing_changed(self):
        made = []

        class Live:
            pass

        def client(*a, **k):
            made.append(a)
            return Live()
        boto3.client = client
        os.environ['REGRESSION_TEST_MODE'] = '0'
        try:
            IL._s3 = None
            c = IL._client()
            self.assertIsInstance(c, Live)
            self.assertEqual(made, [('s3',)])
            self.assertIs(IL._client(), c)                               # made once
            with self.assertRaises(RuntimeError):
                IL.use_test_ledger({})
        finally:
            os.environ['REGRESSION_TEST_MODE'] = '1'


if __name__ == '__main__':
    unittest.main(verbosity=1)
