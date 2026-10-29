#!/usr/bin/env python3
"""A Subscriber IQ ready note carries its tracker (2026-10-29).

Every tracker ready note shipped without its file: the caller fetched the
key from the dashboard bucket while trackers live in svod-acquisition
("subiq delivery file fetch failed: NoSuchKey"). The delivered contract now
resolves the tracker from the Subscriber IQ bucket itself when the caller
hands it no files. Hermetic: stub S3, stub sender, no network.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from prometheus import delivered  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


TRACKER = ('Category,Episode Date,Count,Count Label,Secondary Count,Secondary Label,Tertiary Count,'
           'Tertiary Label,Percentage,Gen Pop Projection\n,,KEY METRICS,,,,,,,Gen Pop Projection\n'
           'Total Show Watchers,,56747,,,,,,,"1,872,084"\nNew Platform Signups,,1509,,,,,,,"49,782"\n').encode('utf-8')
KEY = 'SWAT_Exiles_Season_1_to_date_10_29_2026_23_02.csv'


class _Body:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d


class StubS3:
    """Only the Subscriber IQ bucket holds the tracker; the dashboard bucket
    raises exactly as the real one did."""

    def __init__(self):
        self.put = []

    def get_object(self, Bucket, Key):
        if Bucket == 'svod-acquisition' and Key == KEY:
            return {'Body': _Body(TRACKER)}
        raise RuntimeError(f'NoSuchKey {Bucket}/{Key}')

    def put_object(self, **kw):
        self.put.append(kw)
        return {}

    def head_object(self, **kw):
        raise RuntimeError('404')


sent = {}


def stub_send(**kw):
    sent.update(kw)
    return {'sent': True, 'message_id': 'stub', 'destinations': [kw.get('to')]}


print('== tracker bytes resolve from the Subscriber IQ bucket')
files = delivered.subiq_tracker_files(KEY, s3_client=StubS3())
check('one file comes back', len(files) == 1, files)
check('attachment bytes equal the tracker written', files and files[0][0] == TRACKER)
check('named after the key, as csv', files and files[0][1] == KEY and files[0][2] == 'text/csv', files and files[0][1:])
check('a missing tracker yields no file, never an error', delivered.subiq_tracker_files('nope.csv', s3_client=StubS3()) == [])
check('no client yields no file', delivered.subiq_tracker_files(KEY) == [])

print('== the delivered contract attaches it when the caller passes no files')
s3 = StubS3()
rec = delivered.on_done('subiq', username='admin', subject='SWAT Exiles - Season 1 to date', s3_key=KEY,
                        s3_client=s3, files=[], to='jenna@crosswalknyc.com', figures=[], facts=[],
                        window={'start': '2026-09-25', 'end': '2026-10-29'}, job_id='',
                        ready_message='Your SWAT Exiles - Season 1 to date Subscriber IQ is ready.',
                        send=stub_send)
got = sent.get('files') or []
check('the note went through the sender', bool(sent) and sent.get('to') == 'jenna@crosswalknyc.com', sent.get('to'))
check('exactly one attachment', len(got) == 1, [f[1] for f in got])
check('attachment bytes equal the tracker written', got and bytes(got[0][0]) == TRACKER)
check('delivery record lists the file', rec.get('files') == [KEY], rec.get('files'))
check('record written to the dashboard bucket', any(p.get('Key', '').startswith('system/pm_deliveries/admin/') for p in s3.put), [p.get('Key') for p in s3.put])

print('== caller-provided files still win')
sent.clear()
delivered.on_done('subiq', username='admin', subject='X', s3_key=KEY, s3_client=StubS3(),
                  files=[(b'own-bytes', 'own.csv', 'text/csv')], to='jenna@crosswalknyc.com', job_id='', send=stub_send)
check('caller files are attached unchanged', (sent.get('files') or [])[0][0] == b'own-bytes')

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
