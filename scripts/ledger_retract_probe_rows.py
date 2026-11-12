#!/usr/bin/env python3
"""Pull rows a probe wrote out of the insights ledger, and put back rows
that were retired on a comparison that never applied (2026-11-11).

A `canary:` page-analysis probe banked "392,312,555 insurance-active
individuals" under Chime Banking Customers and it was replayed to
readers. The write paths now refuse a probe (prometheus.ledger_gate);
this is the sweep for what was already there.

    python3 scripts/ledger_retract_probe_rows.py --manifest rows.json            # dry run
    python3 scripts/ledger_retract_probe_rows.py --manifest rows.json --apply

manifest:
  {"batch": "2026-11-12_probe_rows",
   "retract": [{"bucket": "chime_banking_customers", "ts": "2026-11-11T21:45:31Z", "why": "..."}, ...],
   "retract_buckets": [{"bucket": "twin_list_smoke_canary", "why": "..."}],
   "restore": [{"bucket": "perception_box_youtube_followers", "ts": "2026-10-29T22:29:40Z", "why": "..."}],
   "reinstate": [{"bucket": "...", "entry_object": "system/insights_ledger/entries/<date>/<name>.json", "why": "..."}]}

What it does, in order:
  1. copies the index to system/insights_ledger/_backups/;
  2. writes every row it will remove (whole, with who and why) to
     system/insights_ledger/_retracted/<batch>.json;
  3. copies each removed row's audit object under entries/<date>/ to
     system/insights_ledger/_retracted/<batch>/entries/ and deletes the
     original, so a rebuild from the audit objects can never bring a
     probe's figure back;
  4. removes the rows (and any list rows derived from them) from the
     index and lifts the retired stamp on the restore list, in one
     compare-and-swap update.

A row is matched by bucket and timestamp; nothing else in the bucket is
touched. A manifest row that is not found is reported and skipped.
`reinstate` puts a row back from its own audit object (a row a removed
row had replaced), in timestamp order, unless the bucket already holds it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as il  # noqa: E402

RETRACTED_PREFIX = il.LEDGER_PREFIX + '_retracted/'
BACKUP_PREFIX = il.LEDGER_PREFIX + '_backups/'


def _now():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _audit_objects(s3, entry):
    """Keys of the per-entry audit objects for one index row: same day,
    same second, same question and route."""
    day = str(entry.get('ts') or '')[:10]
    hhmmss = str(entry.get('ts') or '')[11:19].replace(':', '')
    out = []
    if not day or not hhmmss:
        return out
    prefix = f"{il.ENTRY_PREFIX}{day}/{hhmmss}_"
    token = None
    while True:
        kw = dict(Bucket=il.S3_BUCKET, Prefix=prefix)
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get('Contents') or []:
            try:
                doc = json.loads(s3.get_object(Bucket=il.S3_BUCKET, Key=o['Key'])['Body'].read())
            except Exception:
                continue
            if doc.get('qn') == entry.get('qn') and doc.get('route') == entry.get('route'):
                out.append(o['Key'])
        if not r.get('IsTruncated'):
            break
        token = r.get('NextContinuationToken')
    return out


def plan(doc, manifest):
    subjects = doc.get('subjects') or {}
    remove, restore, missing = [], [], []
    for item in manifest.get('retract') or []:
        b = subjects.get(item['bucket']) or {}
        hit = [e for e in (b.get('entries') or []) if e.get('ts') == item['ts']
               and (not item.get('route') or e.get('route') == item['route'])]
        if not hit:
            missing.append(item)
            continue
        for e in hit:
            remove.append({'bucket': item['bucket'], 'subject': b.get('subject'), 'why': item.get('why', ''),
                           'asker': item.get('asker', ''), 'entry': e})
    for item in manifest.get('retract_buckets') or []:
        b = subjects.get(item['bucket']) or {}
        if not b:
            missing.append(item)
            continue
        for e in (b.get('entries') or []):
            remove.append({'bucket': item['bucket'], 'subject': b.get('subject'), 'why': item.get('why', ''),
                           'asker': item.get('asker', ''), 'entry': e, 'whole_bucket': True})
    # list rows derived from a removed list read go with it
    gone_keys = {str(r['entry'].get('k') or '') for r in remove}
    have = {(r['bucket'], r['entry'].get('ts'), r['entry'].get('qn')) for r in remove}
    for bk, b in subjects.items():
        for e in (b.get('entries') or []):
            if (bk, e.get('ts'), e.get('qn')) in have:
                continue
            if any(k and f"list read {k}" in str(e.get('derivation') or '') for k in gone_keys) \
                    and any(r['entry'].get('ts', '')[:16] == str(e.get('ts') or '')[:16] for r in remove):
                remove.append({'bucket': bk, 'subject': b.get('subject'), 'why': 'derived from a removed list read',
                               'asker': '', 'entry': e})
    for item in manifest.get('restore') or []:
        b = subjects.get(item['bucket']) or {}
        hit = [e for e in (b.get('entries') or []) if e.get('ts') == item['ts'] and e.get('retired')]
        if not hit:
            missing.append(item)
            continue
        for e in hit:
            restore.append({'bucket': item['bucket'], 'subject': b.get('subject'), 'why': item.get('why', ''), 'entry': e})
    return remove, restore, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    manifest = json.load(open(a.manifest))
    batch = str(manifest.get('batch') or f"{_now()}_probe_rows")
    s3 = il._client()
    doc, _etag = il._read_index_with_etag()
    remove, restore, missing = plan(doc, manifest)
    print(f"index: {len(doc.get('subjects') or {})} subjects; to remove {len(remove)}, to restore {len(restore)}, "
          f"not found {len(missing)}")
    for m in missing:
        print("  not found:", json.dumps(m)[:200])
    for r in remove:
        e = r['entry']
        print(f"  remove  {e.get('ts')} {r['bucket'][:44]:44} {str(e.get('route'))[:18]:18} | {str(e.get('q'))[:70]}")
    for r in restore:
        e = r['entry']
        print(f"  restore {e.get('ts')} {r['bucket'][:44]:44} {str(e.get('route'))[:18]:18} | {str(e.get('q'))[:70]}")
    if not a.apply:
        print("dry run: nothing written")
        return 0
    stamp = _now()
    raw = s3.get_object(Bucket=il.S3_BUCKET, Key=il.INDEX_KEY)['Body'].read()
    bkey = f"{BACKUP_PREFIX}index.pre_{batch}_{stamp}.json"
    s3.put_object(Bucket=il.S3_BUCKET, Key=bkey, Body=raw, ContentType='application/json')
    print("index backup ->", bkey)
    moved = []
    for r in remove:
        r['audit_objects'] = _audit_objects(s3, r['entry'])
    record = {'batch': batch, 'at': stamp, 'removed': remove, 'reinstate': manifest.get('reinstate') or [],
              'restored': [{'bucket': r['bucket'], 'ts': r['entry'].get('ts'), 'q': r['entry'].get('q'), 'why': r['why'],
                            'was': r['entry'].get('retired')} for r in restore]}
    rkey = f"{RETRACTED_PREFIX}{batch}.json"
    s3.put_object(Bucket=il.S3_BUCKET, Key=rkey, Body=json.dumps(record, default=str).encode('utf-8'),
                  ContentType='application/json')
    print("retracted record ->", rkey)
    for r in remove:
        for k in r.get('audit_objects') or []:
            dst = f"{RETRACTED_PREFIX}{batch}/entries/{k[len(il.ENTRY_PREFIX):]}"
            s3.copy_object(Bucket=il.S3_BUCKET, Key=dst, CopySource={'Bucket': il.S3_BUCKET, 'Key': k})
            s3.delete_object(Bucket=il.S3_BUCKET, Key=k)
            moved.append(k)
    print(f"audit objects moved: {len(moved)}")
    back = []
    for item in manifest.get('reinstate') or []:
        try:
            e = json.loads(s3.get_object(Bucket=il.S3_BUCKET, Key=item['entry_object'])['Body'].read())
            back.append((item['bucket'], e, item.get('why', '')))
        except Exception as ex:
            print("  reinstate skipped:", item.get('entry_object'), ex)
    rm = {(r['bucket'], r['entry'].get('ts'), r['entry'].get('qn'), r['entry'].get('route')) for r in remove}
    rs = {(r['bucket'], r['entry'].get('ts'), r['entry'].get('qn')) for r in restore}
    whole = {r['bucket'] for r in remove if r.get('whole_bucket')}
    done = {'removed': 0, 'restored': 0}

    def _mut(d):
        done['removed'] = done['restored'] = 0
        subjects = d.get('subjects') or {}
        for bk in list(subjects.keys()):
            b = subjects[bk]
            keep = []
            for e in (b.get('entries') or []):
                if (bk, e.get('ts'), e.get('qn'), e.get('route')) in rm:
                    done['removed'] += 1
                    continue
                if (bk, e.get('ts'), e.get('qn')) in rs and e.get('retired'):
                    e['restored'] = {'by': 'ops', 'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                     'was': e.pop('retired'), 'batch': batch}
                    done['restored'] += 1
                keep.append(e)
            b['entries'] = keep
            if bk in whole and not keep:
                subjects.pop(bk, None)
        done['reinstated'] = 0
        for bk, e, _why in back:
            b = subjects.get(bk)
            if b is None:
                continue
            ents = b.get('entries') or []
            if any(x.get('ts') == e.get('ts') and x.get('qn') == e.get('qn') for x in ents):
                continue
            ents.append(e)
            ents.sort(key=lambda x: str(x.get('ts') or ''))
            b['entries'] = ents
            done['reinstated'] += 1
        return d

    il._update_index(_mut)
    print(f"index updated: removed {done['removed']}, restored {done['restored']}, reinstated {done.get('reinstated', 0)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
