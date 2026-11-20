#!/usr/bin/env python3
"""A finished read's status line names its link only when the read has one.

2026-11-20: a "what's running?" ask told Liz that a read which answered in
words "is in this chat, above, as its own message with its CSV link under
it". The line now names a CSV link for a read that carries a CSV, a file
link for any other file, and nothing for a read with no file. The general
directions ("where do my reads land") say a CSV link comes with a read that
has a file, never with every read.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import dashboard_map as DM  # noqa: E402
from prometheus import status_lane as SL  # noqa: E402

PREFIX = 'system/prometheus_reads/'
CSV_LINK = {'url': 'https://files.example.com/reads/Nurses_read.csv?sig=abc', 'name': 'Nurses_read.csv',
            'key': 'reads/Nurses_read.csv'}
XLSX_LINK = {'url': 'https://files.example.com/reads/Nurses_read.xlsx?sig=abc', 'name': 'Nurses_read.xlsx',
             'key': 'reads/Nurses_read.xlsx'}
WITH_CSV = 'It is in this chat, above, as its own message with its CSV link under it.'
WITH_FILE = 'It is in this chat, above, as its own message with its file link under it.'
NO_LINK = 'It is in this chat, above, as its own message.'


class _S3:
    """Lists and reads job records the way the status lane does."""

    def __init__(self, docs):
        self.docs = docs

    def list_objects_v2(self, Bucket, Prefix, **_kw):
        now = datetime.now(timezone.utc)
        return {'Contents': [{'Key': k, 'LastModified': now,
                              'ETag': '"%s"' % hashlib.sha1(json.dumps(v, sort_keys=True).encode()).hexdigest()}
                             for k, v in self.docs.items() if k.startswith(Prefix)]}

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(json.dumps(self.docs[Key]).encode('utf-8'))}


class _Host:
    read_prefix = PREFIX
    bucket = 'dashboard-inputs'

    def __init__(self, s3):
        self.s3_client = s3

    def has(self, cap):
        return cap in ('read_prefix', 'bucket')


def _job(jid, reply, *, file_link=None, files=None, thread='t-casey'):
    payload = {'success': True, 'action': 'answer', 'reply': reply, 'followups': []}
    if file_link:
        payload['file_link'] = dict(file_link)
    doc = {'job_id': jid, 'user': 'casey', 'status': 'done', 'question': 'how many people watched Nurses',
           'subject': 'Nurses', 'thread_id': thread, 'started_at': time.time() - 300,
           'finished_at': time.time() - 90, 'payload': payload, 'schema': 'pm.job.v1', 'job_type': 'read'}
    if files:
        doc['files'] = files
    return doc


def _thread(jid, reply, link=None):
    meta = {'read_job_id': jid, 'kind': 'read', 'options': [], 'landed_at': '2026-11-20T21:00:00.000Z'}
    if link is not None:
        meta['link'] = dict(link)
    return [{'role': 'user', 'text': 'how many people watched Nurses', 'ts': '2026-11-20T20:59:00Z'},
            {'role': 'agent', 'text': reply, 'ts': '2026-11-20T20:59:01Z', 'meta': meta}]


class FinishedReadLine(unittest.TestCase):
    def _reply(self, doc, history, thread_id='t-casey'):
        s3 = _S3({f"{PREFIX}{doc['job_id']}.json": doc})
        with mock.patch.object(SL, '_engine_runs', return_value=[]), \
                mock.patch.object(SL, '_deliveries', return_value=[]):
            out = SL.answer("what's running?", 'casey', host=_Host(s3), user={'username': 'casey'},
                            history=history, thread_id=thread_id)
        self.assertTrue(out and out.get('reply'))
        self.assertNotIn('\u2014', out['reply'])
        return out['reply']

    def test_a_read_with_a_csv_says_so(self):
        reply = 'Nurses drew 1,204,317 people in the window. ' * 6
        out = self._reply(_job('a1b2c3d4e5f6', reply, file_link=CSV_LINK), _thread('a1b2c3d4e5f6', reply, CSV_LINK))
        self.assertIn('Your read for Nurses finished', out)
        self.assertIn(WITH_CSV, out)

    def test_a_read_with_no_file_names_no_link(self):
        reply = 'Here are three, each five words or fewer.'
        out = self._reply(_job('b1b2c3d4e5f6', reply), _thread('b1b2c3d4e5f6', reply, {}))
        self.assertIn(NO_LINK, out)
        self.assertNotIn('link under it', out)
        self.assertNotIn('CSV', out)

    def test_the_job_record_alone_carries_the_csv(self):
        reply = 'Nurses drew 1,204,317 people in the window.'
        out = self._reply(_job('c1b2c3d4e5f6', reply, file_link=CSV_LINK), _thread('c1b2c3d4e5f6', reply))
        self.assertIn(WITH_CSV, out)

    def test_a_job_file_record_counts_as_a_csv(self):
        reply = 'Nurses drew 1,204,317 people in the window.'
        doc = _job('d1b2c3d4e5f6', reply, files={'csv': {'key': 'reads/Nurses_read.csv', 'name': 'Nurses_read.csv'}})
        out = self._reply(doc, _thread('d1b2c3d4e5f6', reply))
        self.assertIn(WITH_CSV, out)

    def test_any_other_file_is_a_file_link(self):
        reply = 'Nurses drew 1,204,317 people in the window.'
        out = self._reply(_job('e1b2c3d4e5f6', reply, file_link=XLSX_LINK), _thread('e1b2c3d4e5f6', reply, XLSX_LINK))
        self.assertIn(WITH_FILE, out)
        self.assertNotIn('CSV', out)

    def test_a_read_in_another_chat_names_no_link(self):
        reply = 'Nurses drew 1,204,317 people in the window.'
        out = self._reply(_job('f1b2c3d4e5f6', reply, file_link=CSV_LINK, thread='t-other'), [], thread_id='t-casey')
        self.assertIn('It is in another of your chats', out)
        self.assertNotIn('link under it', out)


class LinkKind(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(SL._link_kind(None), '')
        self.assertEqual(SL._link_kind({}), '')
        self.assertEqual(SL._link_kind({'url': '  '}), '')
        self.assertEqual(SL._link_kind(CSV_LINK), 'CSV')
        self.assertEqual(SL._link_kind({'url': 'https://files.example.com/x?id=1', 'label': 'Download the CSV'}), 'CSV')
        self.assertEqual(SL._link_kind(XLSX_LINK), 'file')
        self.assertEqual(SL._read_file_kind({}, {}), '')
        self.assertEqual(SL._read_file_kind({'file_link': CSV_LINK}, {}), 'CSV')


class Directions(unittest.TestCase):
    def test_no_promise_of_a_csv_with_every_read(self):
        read = DM.where_lands('read')
        self.assertIn('in this chat', read)
        self.assertIn('when the read comes with a file', read)
        self.assertNotIn('with its CSV link under it', read)
        export = DM.where_lands('export')
        self.assertNotIn('every data answer', export)
        self.assertIn('comes with a file', export)
        for line in (read, export):
            self.assertNotIn('\u2014', line)


if __name__ == '__main__':
    unittest.main(verbosity=1)
