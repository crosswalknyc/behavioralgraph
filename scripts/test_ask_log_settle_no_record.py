#!/usr/bin/env python3
"""Settling a job that no ask-log record promised is not a failure (2026-11-11).

Three reads rerun by ops from a script each logged
'[ask-log] settle failed for <job>: ... NoSuchKey ...'. The ask door writes
the ask-log record and its by-job pointer; a job started outside the door
has neither, so there is nothing to settle. settle_ask returns False for
that, quietly. A real failure (the store refused the read) is still logged,
and a record that exists is still settled.

Hermetic: the S3 client is faked. Run:
    python3 scripts/test_ask_log_settle_no_record.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import render_usage_log as RUL  # noqa: E402


class ClientError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}
        super().__init__(f"An error occurred ({code}) when calling the GetObject operation")


class FakeS3:
    def __init__(self, objects=None, fail=None):
        self.objects = dict(objects or {})
        self.fail = fail
        self.puts = []

    def get_object(self, Bucket, Key):
        if self.fail:
            raise ClientError(self.fail)
        if Key not in self.objects:
            raise ClientError('NoSuchKey')
        return {'Body': io.BytesIO(json.dumps(self.objects[Key]).encode('utf-8'))}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = json.loads(Body.decode('utf-8'))
        self.puts.append(Key)


def settle(fake, job_id, **kw):
    orig = RUL._client
    RUL._client = lambda: fake
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ok = RUL.settle_ask(job_id, **kw)
    finally:
        RUL._client = orig
    return ok, buf.getvalue()


class SettleAsk(unittest.TestCase):
    def test_a_job_no_record_promised_is_quietly_false(self):
        ok, out = settle(FakeS3(), '9e5e8d62fd5a', outcome='landed', reply='On people who watched Mad Men')
        self.assertFalse(ok)
        self.assertNotIn('settle failed', out)
        self.assertEqual(out, '')

    def test_a_real_failure_is_still_logged(self):
        ok, out = settle(FakeS3(fail='AccessDenied'), '9e5e8d62fd5a', outcome='landed')
        self.assertFalse(ok)
        self.assertIn('[ask-log] settle failed for 9e5e8d62fd5a', out)

    def test_a_promised_job_is_settled(self):
        key = 'system/usage/ask_log/2026-11-11/174622_abc.json'
        fake = FakeS3({RUL.ASK_BY_JOB_PREFIX + '3ed905367f62.json': {'key': key, 'ts': '2026-11-11T17:46:22Z', 'user': 'u'},
                       key: {'ts': '2026-11-11T17:46:22Z', 'user': 'u', 'question': 'q', 'reply': 'On it', 'outcome': 'queued'}})
        ok, out = settle(fake, '3ed905367f62', outcome='landed', reply='Comedy carries 34.6%', ms=542000)
        self.assertTrue(ok)
        rec = fake.objects[key]
        self.assertEqual(rec['outcome'], 'landed')
        self.assertEqual(rec['promise_line'], 'On it')
        self.assertEqual(rec['reply'], 'Comedy carries 34.6%')
        self.assertEqual(rec['ms_total'], 542000)
        self.assertNotIn('settle failed', out)

    def test_missing_key_detection(self):
        self.assertTrue(RUL._is_missing_key(ClientError('NoSuchKey')))
        self.assertTrue(RUL._is_missing_key(ClientError('404')))
        self.assertFalse(RUL._is_missing_key(ClientError('AccessDenied')))
        self.assertFalse(RUL._is_missing_key(ValueError('bad json')))


if __name__ == '__main__':
    unittest.main(verbosity=1)
