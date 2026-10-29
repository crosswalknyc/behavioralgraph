#!/usr/bin/env python3
"""Brand Partnership IQ: a corrected rerun sends its note once, with the
final figure, after the last write (Jenna 2026-10-28: the updated note
went out at an interim total while the tab held the final one).
Hermetic: an in-memory S3, a recorded send, no research."""
from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
from prometheus import bpiq_job as BJ  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402
import test_bpiq_campaign_reach as R  # noqa: E402

FAIL = 0
BUCKET = 'dashboard-inputs'
BARE = 'Jennifer_Beals_x_Lululemon_10_29_2026_04_17.json'


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    """Enough of the client for the rerun: a dict, a write sequence."""

    def __init__(self):
        self.objects = {}
        self.seq = 0
        self.writes = []          # (seq, key)

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise KeyError(Key)
        return {'Body': _Body(self.objects[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get('IfNoneMatch') and Key in self.objects:
            raise RuntimeError('PreconditionFailed 412')
        self.seq += 1
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.writes.append((self.seq, Key))
        return {}

    def delete_object(self, Bucket, Key, **kw):
        self.objects.pop(Key, None)
        return {}

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in self.objects if k.startswith(Prefix)]}


class FakeHost:
    def __init__(self, s3):
        self.s3_client = s3
        self.S3_BUCKET = BUCKET
        self.BPIQ_METADATA_KEY = 'system/brand_partnership_iq_metadata.json'

    def load_bpiq_metadata(self):
        return {}


def seed(s3, inputs, prim, campaign, total_hint=None):
    """A finished run on the registry, its file and its underpinnings."""
    key = S.run_key(inputs)
    salt = S.run_salt(inputs)
    payload = S.build_payload(inputs, dict(prim, _talent_audience_in_window=79_738), campaign=campaign)
    payload['created_at'] = '2026-10-29T02:17:43Z'
    s3.put_object(Bucket=BUCKET, Key='brand-partnership-iq/' + BARE, Body=json.dumps(payload).encode('utf-8'))
    reg = S.S3Registry(s3, BUCKET)
    reg.put(key, {'run_key': key, 'job_id': 'job123456789', 'user': 'tester', 'status': 'done', 's3_key': BARE,
                  'inputs': inputs, 'project_name': payload['project_name'],
                  'total_brand_value': payload['valuation']['total_brand_value'], 'updated_at': 1.0})
    S.S3Underpinnings(s3, BUCKET).save(key, {'run_key': key, 'inputs': inputs, 'campaign': campaign,
                                              'primitives': prim, 'readme': 'x'})
    s3.put_object(Bucket=BUCKET, Key='system/users.json',
                  Body=json.dumps({'users': {'tester': {'username': 'tester', 'email': 'tester@example.com',
                                                        'first_name': 'Tess'}}}).encode('utf-8'))
    s3.put_object(Bucket=BUCKET, Key='system/pm_bpiq_jobs/job123456789.json',
                  Body=json.dumps({'job_id': 'job123456789', 'status': 'done', 'thread_id': 'thr1'}).encode('utf-8'))
    return key, payload


def main():
    # the catalog, the ledger and the thread are other systems; record the calls
    calls = {'ledger': [], 'thread': []}
    BJ._update_catalog_and_ledger = lambda key, bare, payload, username: calls['ledger'].append(
        float(payload['valuation']['total_brand_value']))
    BJ._update_thread_note = lambda username, job_id, bare, payload: calls['thread'].append(
        float(payload['valuation']['total_brand_value']))

    s3 = FakeS3()
    host = FakeHost(s3)
    inputs = T.inputs()
    camp_a = R.camp_with()
    key, first = seed(s3, inputs, T.prim(), camp_a)
    first_total = float(first['valuation']['total_brand_value'])

    sends = []

    def fake_send(**kw):
        sends.append((s3.seq, kw))
        return {'sent': True, 'to': kw.get('to'), 'subject': kw.get('subject')}

    def no_research(*a, **k):
        raise AssertionError('a re-derive pass must not research')

    # pass 1: the saved anchors move (a bigger run) and the pass re-derives; no note
    under = S.S3Underpinnings(s3, BUCKET)
    doc = under.load(key)
    doc['campaign']['footprint']['inventory'] = {'skus': None, 'colorways_avg': 1.9, 'sizes_avg': 5.8}
    under.save(key, doc)
    r1 = BJ.rerun_in_place(key, host, no_research, research=False, email='defer', send=fake_send, reason='pass 1')
    check("pass 1 rewrote the same file", r1['s3_key'] == BARE and r1['new_total'] != first_total)
    check("pass 1 sent nothing", sends == [] and r1['note_pending'] is True)
    check("pass 1 left a pending marker holding the figure the reader has",
          (BJ.pending_note(s3, BUCKET, key) or {}).get('reader_total') == first_total)
    check("a backup of the prior file landed",
          any(k.startswith('brand-partnership-iq/_backups/') for k in s3.objects))

    # pass 2: another correction on top; still no note, the marker keeps the reader's figure
    doc = under.load(key)
    doc['campaign']['footprint']['inventory'] = {'skus': None, 'colorways_avg': 2.1, 'sizes_avg': 6.0}
    under.save(key, doc)
    r2 = BJ.rerun_in_place(key, host, no_research, research=False, email='defer', send=fake_send, reason='pass 2')
    check("pass 2 changed the total again", r2['new_total'] != r1['new_total'])
    check("pass 2 sent nothing", sends == [])
    pend = BJ.pending_note(s3, BUCKET, key) or {}
    check("the marker still compares against the reader's figure, not the previous pass",
          pend.get('reader_total') == first_total and pend.get('passes') == 2)
    last_write = max(seq for seq, k in s3.writes if k == 'brand-partnership-iq/' + BARE)

    # the note: once, from the live document, after the last write
    out = BJ.send_corrected_note(key, host, send=fake_send)
    live = json.loads(s3.objects['brand-partnership-iq/' + BARE])
    live_total = float(live['valuation']['total_brand_value'])
    check("exactly one note went out", out.get('sent') is True and len(sends) == 1)
    seq_at_send, kw = sends[0]
    check("the note went out after the last write of the run file", seq_at_send >= last_write)
    check("the note carries the live final figure", f"${live_total:,.2f}" in kw['body'] and live_total == r2['new_total'])
    v = live['valuation']
    check("the note carries the four lenses",
          all(f"${float(v[k]):,.2f}" in kw['body'] for k in ('earned_media_value', 'brand_engagement_value', 'brand_lift_value', 'conversion_value')))
    check("the note says it replaces the earlier figure", 'replaces the one in the earlier note' in kw['body'])
    check("the subject names the valuation", kw['subject'].startswith('Jennifer Beals x Lululemon'))
    check("the note goes to the seat, through the Prometheus path",
          kw['to'] == 'tester@example.com' and kw.get('caller') == 'delivered_bpiq_update' and kw.get('instructed') is True)
    low = kw['body'].lower()
    check("the note is clean", '\u2014' not in kw['body'] and '\u2013' not in kw['body']
          and not any(w in low for w in ('synth', 'modeled', 'pipeline', 'estimated', 'because', 'reason'))
          and not re.search(r'\bdoors?\b', low))
    check("the note has no interim figure", f"${first_total:,.2f}" not in kw['body'] and f"${r1['new_total']:,.2f}" not in kw['body'])
    check("the marker is cleared and the figure sent is on the registry",
          BJ.pending_note(s3, BUCKET, key) is None
          and S.S3Registry(s3, BUCKET).load(key).get('noted_total') == live_total)
    check("ledger and thread were updated on every pass with that pass's figure",
          calls['ledger'] == [r1['new_total'], r2['new_total']] and calls['thread'] == [r1['new_total'], r2['new_total']])

    # nothing new: no second note
    out2 = BJ.send_corrected_note(key, host, send=fake_send)
    check("with nothing new no second note goes out", out2.get('sent') is False and len(sends) == 1)

    # a pass that asks for the note right away still sends from the live document, once
    doc = under.load(key)
    doc['campaign']['footprint']['inventory'] = {'skus': None, 'colorways_avg': 1.5, 'sizes_avg': 5.0}
    under.save(key, doc)
    r3 = BJ.rerun_in_place(key, host, no_research, research=False, email='auto', send=fake_send, reason='pass 3')
    live3 = float(json.loads(s3.objects['brand-partnership-iq/' + BARE])['valuation']['total_brand_value'])
    check("a single-pass correction with email on sends once at the live figure",
          len(sends) == 2 and f"${live3:,.2f}" in sends[1][1]['body'] and r3['email'] is not None)
    check("a small move sends nothing",
          BJ.send_corrected_note(key, host, send=fake_send, material_rel=0.05).get('sent') is False and len(sends) == 2)

    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAILED'}")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
