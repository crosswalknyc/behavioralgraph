#!/usr/bin/env python3
"""A test process cannot write to a live store (2026-11-12).

Jenna: "Make it impossible, not just absent tonight."

Suites that reached a real client through the code they drive wrote to
production: fixture figures banked in the saved-answers ledger every
night from 2026-10-14, test rows in the mail log, two real builds
ordered on 2026-11-05, four emails to an outside address in October.
The rule now sits on the storage client class
(migration/storage_test_guard.py, loaded here through test_guard_boot):
under REGRESSION_TEST_MODE=1 a write through a real client is refused
unless the bucket is a suite's own, the client talks to a local
stand-in, or the test named a scratch prefix.

The clients here are real boto3 clients with made-up keys whose sender
is replaced, so every request that would have left the machine is
counted instead of sent. No network.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')

import boto3  # noqa: E402
from botocore.awsrequest import AWSResponse  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

import test_guard_boot  # noqa: E402

LIVE = 'dashboard-inputs'


def _client(service='s3', sent=None, **kw):
    """A real client whose requests are recorded, never sent."""
    c = boto3.client(service, region_name='us-east-2', aws_access_key_id='AKIAEXAMPLEEXAMPLE00',
                     aws_secret_access_key='example/not/a/real/secret', **kw)

    def before_send(request, **_kw):
        if sent is not None:
            sent.append((request.method, request.url.split('?')[0]))

        class _Raw:
            def stream(self, *a, **k):
                return iter([b''])

            def read(self, *a, **k):
                return b''
        return AWSResponse(request.url, 200, {'ETag': '"x"'}, _Raw())
    c.meta.events.register(f'before-send.{service}', before_send)
    return c


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ['REGRESSION_TEST_MODE'] = '1'
        self.assertTrue(test_guard_boot.install())
        self.G = test_guard_boot.guard()
        self.G.clear_refused()
        self.sent = []
        self.s3 = _client('s3', self.sent)

    def tearDown(self):
        os.environ['REGRESSION_TEST_MODE'] = '1'

    def puts(self):
        return [u for m, u in self.sent if m in ('PUT', 'POST', 'DELETE')]


class NoWriteReachesALiveStore(_Base):
    def test_every_kind_of_write_is_refused_and_nothing_is_sent(self):
        calls = (
            lambda: self.s3.put_object(Bucket=LIVE, Key='system/insights_ledger/index.json', Body=b'{}'),
            lambda: self.s3.put_object(Bucket=LIVE, Key='system/outbound_mail/2026-11-12/x.json', Body=b'{}'),
            lambda: self.s3.put_object(Bucket=LIVE, Key='Mark_Rober_11_05_2026_09_14.csv', Body=b'a,b'),
            lambda: self.s3.delete_object(Bucket=LIVE, Key='system/users.json'),
            lambda: self.s3.delete_objects(Bucket=LIVE, Delete={'Objects': [{'Key': 'a'}, {'Key': 'b'}]}),
            lambda: self.s3.copy_object(Bucket=LIVE, Key='system/x.json', CopySource={'Bucket': LIVE, 'Key': 'system/y.json'}),
            lambda: self.s3.upload_fileobj(io.BytesIO(b'deck'), LIVE, 'generated_decks/d.pptx'),
            lambda: self.s3.put_object(Bucket='svod-acquisition', Key='runs/x.json', Body=b'{}'),
            lambda: self.s3.put_object(Bucket='some-bucket-nobody-declared', Key='k', Body=b'1'),
        )
        for i, call in enumerate(calls):
            with self.assertRaises(Exception, msg=str(i)) as cm:
                call()
            self.assertIn('may not write to a live store', str(cm.exception), i)
        self.assertEqual(self.puts(), [])
        self.assertEqual(len(self.G.refused()), len(calls))
        # it is an S3-shaped error: code that survives a failed write survives this
        with self.assertRaises(ClientError) as cm:
            self.s3.put_object(Bucket=LIVE, Key='k', Body=b'1')
        self.assertEqual(cm.exception.response['Error']['Code'], 'TestModeWriteRefused')

    def test_reads_are_untouched(self):
        self.s3.get_object(Bucket=LIVE, Key='system/users.json')
        self.s3.list_objects_v2(Bucket=LIVE, Prefix='system/')
        self.s3.head_object(Bucket=LIVE, Key='system/users.json')
        url = self.s3.generate_presigned_url('get_object', Params={'Bucket': LIVE, 'Key': 'generated_data/a.csv'}, ExpiresIn=60)
        self.assertIn('generated_data/a.csv', url)
        self.assertEqual([m for m, _u in self.sent], ['GET', 'GET', 'HEAD'])
        self.assertEqual(self.G.refused(), [])

    def test_mail_is_refused(self):
        sent = []
        ses = _client('ses', sent)
        with self.assertRaises(ClientError):
            ses.send_email(Source='a@crosswalknyc.com', Destination={'ToAddresses': ['partner-ops@partnerco.com']},
                           Message={'Subject': {'Data': 's'}, 'Body': {'Text': {'Data': 'b'}}})
        with self.assertRaises(ClientError):
            ses.send_raw_email(RawMessage={'Data': b'x'})
        self.assertEqual(sent, [])
        self.assertEqual(len(self.G.refused()), 2)

    def test_a_client_made_before_and_one_made_after_are_both_covered(self):
        later = _client('s3', self.sent)
        for c in (self.s3, later):
            with self.assertRaises(ClientError):
                c.put_object(Bucket=LIVE, Key='system/x.json', Body=b'1')
        self.assertEqual(self.puts(), [])


class WhatATestMayStillDo(_Base):
    def test_a_suites_own_bucket(self):
        for bucket in ('b', 'test-bucket', 'stub-bucket', 'unit-test', 'x'):
            self.s3.put_object(Bucket=bucket, Key='generated_data/aaa/x.csv', Body=b'1')
        self.assertEqual(len(self.puts()), 5)
        os.environ['PM_TEST_BUCKETS'] = 'my-sandbox, other'
        try:
            self.s3.put_object(Bucket='my-sandbox', Key='k', Body=b'1')
        finally:
            os.environ.pop('PM_TEST_BUCKETS', None)
        self.assertEqual(len(self.puts()), 6)

    def test_a_local_stand_in(self):
        local = _client('s3', self.sent, endpoint_url='http://127.0.0.1:9000')
        local.put_object(Bucket=LIVE, Key='system/x.json', Body=b'1')
        self.assertEqual(len(self.puts()), 1)

    def test_a_scratch_prefix_the_test_names(self):
        with self.assertRaises(ClientError):
            self.s3.put_object(Bucket=LIVE, Key='_test_scratch/run1/a.json', Body=b'1')        # not named yet
        with self.G.allow_scratch('_test_scratch/run1/'):
            self.s3.put_object(Bucket=LIVE, Key='_test_scratch/run1/a.json', Body=b'1')
            self.s3.delete_objects(Bucket=LIVE, Delete={'Objects': [{'Key': '_test_scratch/run1/a.json'}]})
            with self.assertRaises(ClientError):
                self.s3.put_object(Bucket=LIVE, Key='_test_scratch/run2/a.json', Body=b'1')    # another run's
            with self.assertRaises(ClientError):
                self.s3.put_object(Bucket=LIVE, Key='system/users.json', Body=b'1')
            with self.assertRaises(ClientError):
                self.s3.delete_objects(Bucket=LIVE, Delete={'Objects': [{'Key': '_test_scratch/run1/a.json'}, {'Key': 'system/users.json'}]})
        with self.assertRaises(ClientError):
            self.s3.put_object(Bucket=LIVE, Key='_test_scratch/run1/a.json', Body=b'1')        # the allowance ended with the block
        self.assertEqual(len(self.puts()), 2)
        # a scratch prefix is under the scratch root, nowhere else
        for bad in ('system/', '', '_test_scratch/', 'generated_data/x/'):
            with self.assertRaises(ValueError):
                with self.G.allow_scratch(bad):
                    pass


class HowItIsInstalled(_Base):
    def test_once_for_the_process_and_shared_by_every_copy(self):
        import botocore.client as bc
        fn = bc.BaseClient._make_api_call
        self.assertTrue(self.G.installed())
        self.assertTrue(self.G.install())
        self.assertIs(bc.BaseClient._make_api_call, fn)                       # twice is once
        # the mirror loaded as part of the migration package is another copy of the module: same rule, same state
        spec = importlib.util.spec_from_file_location('guard_second_copy', os.path.join(ROOT, 'migration', 'storage_test_guard.py'))
        other = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(other)
        self.assertTrue(other.install())
        self.assertIs(bc.BaseClient._make_api_call, fn)
        with other.allow_scratch('_test_scratch/shared/'):
            self.s3.put_object(Bucket=LIVE, Key='_test_scratch/shared/a', Body=b'1')
        with self.assertRaises(ClientError):
            self.s3.put_object(Bucket=LIVE, Key='k', Body=b'1')
        self.assertEqual(other.refused(), self.G.refused())

    def test_the_modules_that_make_clients_install_it(self):
        src = {name: open(os.path.join(ROOT, name), encoding='utf-8').read()
               for name in ('insights_ledger.py', 'render_usage_log.py', os.path.join('prometheus', '__init__.py'),
                            os.path.join('migration', '__init__.py'))}
        for name in ('insights_ledger.py', 'render_usage_log.py', os.path.join('prometheus', '__init__.py')):
            self.assertIn('test_guard_boot', src[name], name)
        self.assertIn('storage_test_guard', src[os.path.join('migration', '__init__.py')])

    def test_a_suite_that_switches_the_flag_off_is_still_a_suite(self):
        """One suite turns the flag off to exercise production behaviour; its next write went to the live
        corrections store. The rule stays in force for the life of a process that started as a test."""
        os.environ['REGRESSION_TEST_MODE'] = '0'
        try:
            with self.assertRaises(ClientError):
                self.s3.put_object(Bucket=LIVE, Key='system/prometheus_corrections/ctx/x.json', Body=b'1')
            os.environ.pop('REGRESSION_TEST_MODE', None)
            with self.assertRaises(ClientError):
                self.s3.put_object(Bucket=LIVE, Key='system/x.json', Body=b'1')
            self.assertEqual(self.puts(), [])
        finally:
            os.environ['REGRESSION_TEST_MODE'] = '1'

    def test_a_tool_that_loads_the_harness_and_switches_the_flag_off_does_real_work(self):
        """The portrait backfill loads the suites' harness (which sets the flag) and then switches it off to write for
        real. It was not started as a test, so for it the flag alone decides: refused while on, sent once off."""
        import subprocess
        import tempfile
        tool = os.path.join(tempfile.mkdtemp(prefix='guard_tool_'), 'backfill_tool.py')
        with open(tool, 'w') as fh:
            fh.write(
                "import os, sys\n"
                "sys.path.insert(0, %r)\n"
                "os.environ['REGRESSION_TEST_MODE'] = '1'\n"
                "import boto3, test_guard_boot\n"
                "from botocore.awsrequest import AWSResponse\n"
                "from botocore.exceptions import ClientError\n"
                "test_guard_boot.install()\n"
                "sent = []\n"
                "c = boto3.client('s3', region_name='us-east-2', aws_access_key_id='AKIAEXAMPLEEXAMPLE00', aws_secret_access_key='x')\n"
                "class R:\n"
                "    def stream(self, *a, **k): return iter([b''])\n"
                "    def read(self, *a, **k): return b''\n"
                "def send(request, **kw):\n"
                "    sent.append(request.method)\n"
                "    return AWSResponse(request.url, 200, {'ETag': 'x'}, R())\n"
                "c.meta.events.register('before-send.s3', send)\n"
                "try:\n"
                "    c.put_object(Bucket='dashboard-inputs', Key='system/x.json', Body=b'1'); first = 'sent'\n"
                "except ClientError:\n"
                "    first = 'refused'\n"
                "os.environ.pop('REGRESSION_TEST_MODE', None)\n"
                "c.put_object(Bucket='dashboard-inputs', Key='system/x.json', Body=b'1')\n"
                "print(first, sent)\n" % ROOT)
        env = {k: v for k, v in os.environ.items() if k not in ('REGRESSION_TEST_MODE', 'PYTEST_CURRENT_TEST', 'PM_REPLAY_CHILD')}
        out = subprocess.run([sys.executable, tool], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "refused ['PUT']", out.stderr[-600:])

    def test_outside_a_test_process_nothing_is_installed(self):
        """A fresh interpreter that never saw the mode: the client class is untouched and a write goes out."""
        import subprocess
        env = {k: v for k, v in os.environ.items() if k not in ('REGRESSION_TEST_MODE', 'PM_MAIL_OFF', 'PYTEST_CURRENT_TEST', 'PM_REPLAY_CHILD')}
        code = ("import sys; sys.path.insert(0, %r)\n"
                "import prometheus, insights_ledger, render_usage_log, migration, trends_iq, test_guard_boot\n"
                "import botocore.client as bc\n"
                "print(hasattr(bc.BaseClient._make_api_call, '_pm_test_guard'), test_guard_boot.install())\n" % ROOT)
        out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(out.stdout.strip().splitlines()[-1], 'False False', out.stderr[-400:])


if __name__ == '__main__':
    unittest.main(verbosity=1)
