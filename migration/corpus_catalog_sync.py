#!/usr/bin/env python3
"""Sweep every dashboard store in S3 into the corpus catalog and audit
cross-product coherence (2026-10-05, Jenna: "it should always scour
everything in the dashboard which is all stored in s3").

The write hooks catalog new objects the moment they land. This sweep is
the safety net: it walks every store, re-reads only objects whose ETag
changed since they were last indexed, forgets objects that are gone,
and then audits every subject that two or more products describe.

    python3 migration/corpus_catalog_sync.py            # sweep + audit
    python3 migration/corpus_catalog_sync.py --dry-run  # report only
    python3 migration/corpus_catalog_sync.py --audit-only
    python3 migration/corpus_catalog_sync.py --no-email

Stores:
    profiles      s3://dashboard-inputs/<Name>_<MM_DD_YYYY_HH_MM>.csv (root)
    journeys      s3://dashboard-inputs/journey-iq/<user>/<slug>.json.gz
    attribution   s3://dashboard-inputs/intent/<slug>/{source/normalized_assets.json,
                  mta/coefficients_<date>.json}
    bpiq          s3://dashboard-inputs/brand-partnership-iq/<name>.json

Deployed on Hetzner by migration/systemd/corpus-catalog-sync.{service,timer}
(every 30 minutes). Log: /var/log/corpus-catalog-sync.log. The audit
email goes to jenna@ and jessie@ only (system alert recipients).
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# PM_TEST_WEBAPP points the test harness at the webapp checkout under
# test; on the engine host it is unset and the sibling checkout is used.
for p in (ROOT, os.environ.get('PM_TEST_WEBAPP') or os.path.join(ROOT, 'bg-webapp')):
    if p not in sys.path:
        sys.path.insert(0, p)

from migration import corpus_catalog as cc  # noqa: E402

BUCKET = cc.S3_BUCKET
ALERT_TO = ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com']
SENDER = 'BehavioralGraph <jenna@crosswalknyc.com>'


def _s3():
    return cc._client()


def _list(prefix, delimiter=None):
    s3 = _s3()
    kw = dict(Bucket=BUCKET, Prefix=prefix)
    if delimiter:
        kw['Delimiter'] = delimiter
    token = None
    while True:
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw)
        for o in resp.get('Contents') or []:
            yield o
        for cp in resp.get('CommonPrefixes') or []:
            yield {'Prefix': cp.get('Prefix')}
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')


def _display_names():
    """s3_key -> display name from the dashboard's persisted selector cache."""
    out = {}
    try:
        doc, _ = cc._read_json_with_etag('system/s3_cache.json')
        for j in (doc or {}).get('jobs') or []:
            k = str(j.get('s3_key') or '')
            if k:
                out[k] = str(j.get('display_name') or '')
    except Exception as e:
        print(f"[sync] s3_cache read failed: {e}")
    return out


def sweep_profiles(sources, dry_run=False, workers=12):
    names = _display_names()
    todo = []
    seen = set()
    for o in _list('', delimiter='/'):
        key = o.get('Key')
        if not key or not key.lower().endswith('.csv') or '/' in key:
            continue
        if key.lower().startswith(('gen_pop', 'gen pop')) or key.startswith('_'):
            continue
        seen.add(key)
        et = (o.get('ETag') or '').strip('"')
        if (sources.get(key) or {}).get('etag') == et:
            continue
        todo.append((key, et))
    print(f"[sync] profiles: {len(seen)} on S3, {len(todo)} new or changed")
    if dry_run or not todo:
        return len(todo), seen
    records = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(cc.extract_profile_from_s3, key, names.get(key, ''), '', et): key
                for key, et in todo}
        for f in as_completed(futs):
            r = f.result()
            if r:
                records.append(r)
    n_subjects = cc.bulk_upsert(records)
    print(f"[sync] profiles indexed: {len(records)}/{len(todo)} into {n_subjects} subjects")
    return len(records), seen


def sweep_journeys(sources, dry_run=False):
    seen = set()
    n = 0
    records = []
    for o in _list('journey-iq/'):
        key = o.get('Key') or ''
        if not key.endswith('.json.gz') or '/_backups/' in key:
            continue
        seen.add(key)
        et = (o.get('ETag') or '').strip('"')
        if (sources.get(key) or {}).get('etag') == et:
            continue
        n += 1
        if dry_run:
            continue
        try:
            body = _s3().get_object(Bucket=BUCKET, Key=key)['Body'].read()
            payload = json.loads(gzip.decompress(body).decode('utf-8'))
            user = key.split('/')[1] if key.count('/') >= 2 else ''
            subject, facts = cc.facts_from_journey(key, payload, user=user)
            if subject and facts:
                records.append({'product': 'journey', 's3_key': key, 'subject': subject,
                                'facts': facts, 'etag': et, 'aliases': []})
        except Exception as e:
            print(f"[sync] journey failed {key}: {e}")
    if records:
        cc.bulk_upsert(records)
    print(f"[sync] journeys: {len(seen)} on S3, {n} new or changed, {len(records)} indexed")
    return n, seen


def sweep_attribution(sources, dry_run=False):
    seen = set()
    n = 0
    records = []
    for o in _list('intent/', delimiter='/'):
        pre = o.get('Prefix')
        if not pre:
            continue
        slug = pre.rstrip('/').split('/')[-1]
        fits = sorted((x.get('Key') for x in _list(f'{pre}mta/coefficients_')
                       if x.get('Key')), reverse=True)
        if not fits:
            continue
        fit_key = fits[0]
        src_key = f'intent/{slug}/'
        seen.add(src_key)
        head = _s3().head_object(Bucket=BUCKET, Key=fit_key)
        et = (head.get('ETag') or '').strip('"')
        if (sources.get(src_key) or {}).get('etag') == et:
            continue
        n += 1
        if dry_run:
            continue
        try:
            fit = json.loads(_s3().get_object(Bucket=BUCKET, Key=fit_key)['Body'].read())
            assets = {}
            try:
                assets = json.loads(_s3().get_object(
                    Bucket=BUCKET, Key=f'{pre}source/normalized_assets.json')['Body'].read())
            except Exception:
                pass
            subject, facts = cc.facts_from_attribution(slug, assets, fit)
            if subject and facts:
                records.append({'product': 'attribution', 's3_key': src_key, 'subject': subject,
                                'facts': facts, 'etag': et, 'aliases': []})
        except Exception as e:
            print(f"[sync] attribution failed {slug}: {e}")
    if records:
        cc.bulk_upsert(records)
    print(f"[sync] attribution campaigns: {len(seen)} on S3, {n} new or changed, {len(records)} indexed")
    return n, seen


def sweep_bpiq(sources, dry_run=False):
    seen = set()
    n = 0
    records = []
    for o in _list('brand-partnership-iq/'):
        key = o.get('Key') or ''
        if not key.endswith('.json') or '/_backups/' in key:
            continue
        seen.add(key)
        et = (o.get('ETag') or '').strip('"')
        if (sources.get(key) or {}).get('etag') == et:
            continue
        n += 1
        if dry_run:
            continue
        try:
            payload = json.loads(_s3().get_object(Bucket=BUCKET, Key=key)['Body'].read())
            subject, facts = cc.facts_from_bpiq(key, payload, user=str(payload.get('created_by') or ''))
            if subject and facts:
                records.append({'product': 'bpiq', 's3_key': key, 'subject': subject,
                                'facts': facts, 'etag': et, 'aliases': []})
        except Exception as e:
            print(f"[sync] bpiq failed {key}: {e}")
    if records:
        cc.bulk_upsert(records)
    print(f"[sync] bpiq reads: {len(seen)} on S3, {n} new or changed, {len(records)} indexed")
    return n, seen


def sweep_trends(dry_run=False, top_n=25):
    """Latest Trends IQ snapshot day -> one small document of ranks per
    subject (top_n per source), so a Prometheus answer about a title's
    trend position agrees with the Trends tab."""
    days = sorted(p['Prefix'].rstrip('/').split('/')[-1] for p in _list('trends_iq_snapshots/', delimiter='/')
                  if p.get('Prefix') and re.fullmatch(r'trends_iq_snapshots/\d{4}-\d{2}-\d{2}/', p['Prefix']))
    if not days:
        print('[sync] trends: no snapshot days')
        return 0
    day = days[-1]
    entries = {}
    n_sources = 0
    for o in _list(f'trends_iq_snapshots/{day}/'):
        key = o.get('Key') or ''
        if not key.endswith('.json'):
            continue
        try:
            doc = json.loads(_s3().get_object(Bucket=BUCKET, Key=key)['Body'].read())
        except Exception:
            continue
        label = str(doc.get('label') or doc.get('source') or key.rsplit('/', 1)[-1][:-5])
        rows = doc.get('national') or doc.get('items') or doc.get('rows') or []
        if not isinstance(rows, list):
            continue
        n_sources += 1
        for r in rows[:top_n]:
            if not isinstance(r, dict):
                continue
            title = str(r.get('title') or r.get('name') or '').strip()
            rank = r.get('rank') or r.get('bucket_rank')
            if not title or rank is None:
                continue
            skey = cc.subject_key(title)
            if not skey:
                continue
            entries.setdefault(skey, []).append({'source': str(doc.get('source') or ''), 'label': label,
                                                 'rank': int(rank) if str(rank).isdigit() else rank,
                                                 'title': title, 'as_of': day})
    print(f"[sync] trends: day {day}, {n_sources} sources, {len(entries)} subjects")
    if dry_run or not entries:
        return len(entries)
    cc.write_trends_latest(day, entries)
    return len(entries)


def backfill_threads(sources, dry_run=False, max_threads=5000):
    """Every figure Prometheus stated in chat before the write hook
    existed, banked under the subject its aliases name (2026-10-06).
    A thread is re-read only when its ETag changed."""
    idx = cc.load_index(force=True)
    aliases = []
    for skey, ent in (idx.get('subjects') or {}).items():
        for a in (ent.get('aliases') or []) + [ent.get('subject') or '']:
            a = str(a or '').strip()
            if len(a) >= 4:
                aliases.append((a.lower(), ent.get('subject') or a))
    aliases.sort(key=lambda x: -len(x[0]))
    n_threads = n_turns = 0
    present = set()
    for o in _list('system/synth_chat_threads/'):
        key = o.get('Key') or ''
        if not key.endswith('.json') or key.endswith('/index.json') or '/_backups/' in key:
            continue
        present.add(key)
        et = (o.get('ETag') or '').strip('"')
        if (sources.get(key) or {}).get('etag') == et:
            continue
        if n_threads >= max_threads:
            break
        n_threads += 1
        if dry_run:
            continue
        try:
            turns = json.loads(_s3().get_object(Bucket=BUCKET, Key=key)['Body'].read())
        except Exception:
            continue
        parts = key.split('/')
        user = parts[2] if len(parts) >= 4 else ''
        tid = parts[-1][:-5]
        prev_user = ''
        banked = 0
        for turn in (turns if isinstance(turns, list) else []):
            if not isinstance(turn, dict):
                continue
            txt = str(turn.get('text') or '')
            if turn.get('role') == 'user':
                prev_user = txt
                continue
            if not re.search(r'\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?%', txt):
                continue
            hay = (prev_user + ' ' + txt).lower()
            subject = next((disp for low, disp in aliases if low in hay), '')
            if not subject:
                continue
            if cc.record_answer(subject, txt, user=user, thread_id=tid):
                banked += 1
        n_turns += banked
        # mark the thread as indexed so the next sweep skips it
        try:
            cc._update_json(cc.SOURCES_KEY, lambda d, _k=key, _e=et: (d.__setitem__(_k, {
                'etag': _e, 'product': 'chat', 'subject_key': '', 'indexed_at': cc._now_iso()}) or d))
        except Exception:
            pass
    print(f"[sync] threads: {len(present)} on S3, {n_threads} new or changed, {n_turns} replies banked")
    return n_turns


def forget_gone(sources, present, dry_run=False):
    gone = [k for k, v in sources.items()
            if v.get('product') in ('profile', 'journey', 'attribution', 'bpiq')
            and k not in present]
    print(f"[sync] sources gone from S3: {len(gone)}")
    if dry_run:
        return len(gone)
    for k in gone:
        cc.forget_source(k)
    return len(gone)


# ---------------------------------------------------------------- audit
def audit(limit_subjects=5000):
    """Same-subject disagreements across products. A sub-window may sit
    below an annual figure; it may never sit above it. Two reads of the
    same thing on overlapping windows should agree within 35%."""
    idx = cc.load_index(force=True)
    findings = []
    subs = idx.get('subjects') or {}
    multi = [(k, v) for k, v in subs.items() if len(v.get('products') or []) >= 2]
    for skey, ent in multi[:limit_subjects]:
        page = cc.load_subject_page(skey, force=True) or {}
        facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)]
        sizes = [f for f in facts if f.get('kind') in ('audience_size', 'stage_count')
                 and f.get('unit') == 'people' and f.get('product') != 'chat']
        # pairwise across products
        for i, a in enumerate(sizes):
            for b in sizes[i + 1:]:
                if a.get('product') == b.get('product'):
                    continue
                wa, wb = a.get('window') or {}, b.get('window') or {}
                ov = cc._window_overlap(wa, wb)
                if ov is None or ov <= 0:
                    continue
                va, vb = float(a['value']), float(b['value'])
                if va <= 0 or vb <= 0:
                    continue
                # the one with the shorter window must not exceed the longer
                la = (cc._d(wa.get('end')) - cc._d(wa.get('start'))).days + 1
                lb = (cc._d(wb.get('end')) - cc._d(wb.get('start'))).days + 1
                short, long_ = (a, b) if la < lb else (b, a)
                vs, vl = float(short['value']), float(long_['value'])
                if la != lb and vs > vl * 1.05 and _comparable(short, long_):
                    findings.append({'subject': ent.get('subject'), 'kind': 'sub_window_exceeds',
                                     'a': _fact_line(short), 'b': _fact_line(long_)})
                elif la == lb and max(va, vb) / min(va, vb) > 1.35 and _comparable(a, b):
                    findings.append({'subject': ent.get('subject'), 'kind': 'same_window_disagree',
                                     'a': _fact_line(a), 'b': _fact_line(b)})
    print(f"[audit] subjects with 2+ products: {len(multi)}, findings: {len(findings)}")
    return findings, len(multi)


# Order matters: a label is classed by the first family that names it.
# A showtimes page and a ticketing-site visit are two different steps
# (film ladder per no-box-office-prediction.mdc, settled 2026-10-06):
# 'Looked up showtimes' compares with 'reached a showtimes page', never
# with 'went to a ticketing site or app for a ticket'.
_STAGE_CLASSES = (
    ('checkout', ('checkout', 'cart', 'paid', 'bought', 'purchase', 'end point',
                  'final ticket step')),
    ('showtimes', ('showtime',)),
    ('ticketing', ('ticketing site', 'ticketing-site', 'ticketing page', 'for a ticket')),
    ('infoseek', ('looked', 'search', 'info', 'research', 'trailer', 'review')),
    ('exposed', ('saw', 'exposed', 'reached by', 'tracked campaign')),
    ('acted', ('acted', 'engaged', 'clicked')),
)


def _stage_class(label):
    low = str(label or '').lower()
    for name, toks in _STAGE_CLASSES:
        if any(t in low for t in toks):
            return name
    return 'other'


def _comparable(a, b):
    """Only compare like with like. Two total audiences compare. Two
    path stages compare only when they are the same step (exposed with
    exposed, looked-up with looked-up). A total never compares with a
    stage, and bottom-of-path steps never enter the audit at all."""
    ka, kb = a.get('kind'), b.get('kind')
    if ka != kb:
        return False
    if ka == 'audience_size':
        return True
    ca, cb = _stage_class(a.get('label')), _stage_class(b.get('label'))
    if ca in ('checkout', 'other') or cb in ('checkout', 'other'):
        return False
    return ca == cb


def _fact_line(f):
    return (f"{f.get('note') or f.get('product')}: {f.get('label')} = {cc._fmt_value(f)}"
            f"{cc._fmt_window(f.get('window'))}")


def send_audit_email(findings, n_multi, stats):
    import boto3
    lines = [f"Corpus catalog sweep {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             '',
             f"Profiles indexed this run: {stats.get('profiles', 0)}; journeys: {stats.get('journeys', 0)}; "
             f"attribution campaigns: {stats.get('attribution', 0)}; brand partnership reads: {stats.get('bpiq', 0)}; "
             f"sources removed: {stats.get('gone', 0)}.",
             f"Subjects described by two or more products: {n_multi}.",
             '']
    if not findings:
        lines.append('No cross-product disagreements.')
    else:
        lines.append(f"{len(findings)} cross-product disagreement(s) to look at:")
        lines.append('')
        for f in findings[:60]:
            lines.append(f"- {f['subject']} ({f['kind'].replace('_', ' ')})")
            lines.append(f"    {f['a']}")
            lines.append(f"    {f['b']}")
    body = '\n'.join(lines)
    subj = ('Corpus catalog: ' + (f"{len(findings)} cross-product disagreement(s)" if findings
                                  else 'clean sweep'))
    try:
        ses = boto3.client('ses', region_name='us-east-2')
        ses.send_email(Source=SENDER, Destination={'ToAddresses': ALERT_TO},
                       Message={'Subject': {'Data': subj},
                                'Body': {'Text': {'Data': body}}})
        print('[audit] email sent')
    except Exception as e:
        print(f"[audit] email failed: {e}")
    return body


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--audit-only', action='store_true')
    ap.add_argument('--no-email', action='store_true')
    ap.add_argument('--no-audit', action='store_true')
    ap.add_argument('--workers', type=int, default=12)
    ap.add_argument('--rebuild', action='store_true',
                    help='forget every indexed source first so the whole corpus is re-read '
                         '(after a change to the subject fold)')
    args = ap.parse_args(argv)
    if args.rebuild and not args.dry_run:
        s3 = _s3()
        tok = None
        n = 0
        while True:
            kw = dict(Bucket=BUCKET, Prefix='system/corpus_catalog/')
            if tok:
                kw['ContinuationToken'] = tok
            r = s3.list_objects_v2(**kw)
            keys = [o['Key'] for o in r.get('Contents') or []]
            for i in range(0, len(keys), 1000):
                s3.delete_objects(Bucket=BUCKET, Delete={'Objects': [{'Key': k} for k in keys[i:i + 1000]]})
            n += len(keys)
            if not r.get('IsTruncated'):
                break
            tok = r.get('NextContinuationToken')
        cc.set_client(None)
        print(f"[sync] rebuild: cleared {n} catalog objects")
    t0 = time.time()
    stats = {}
    if not args.audit_only:
        sources = dict(cc.load_sources())
        present = set()
        n, seen = sweep_profiles(sources, dry_run=args.dry_run, workers=args.workers)
        stats['profiles'] = n
        present |= seen
        n, seen = sweep_journeys(sources, dry_run=args.dry_run)
        stats['journeys'] = n
        present |= seen
        n, seen = sweep_attribution(sources, dry_run=args.dry_run)
        stats['attribution'] = n
        present |= seen
        n, seen = sweep_bpiq(sources, dry_run=args.dry_run)
        stats['bpiq'] = n
        present |= seen
        stats['gone'] = forget_gone(sources, present, dry_run=args.dry_run)
        try:
            stats['trends_subjects'] = sweep_trends(dry_run=args.dry_run)
        except Exception as e:
            print(f"[sync] trends sweep skipped: {e}")
        try:
            stats['chat_replies'] = backfill_threads(sources, dry_run=args.dry_run)
        except Exception as e:
            print(f"[sync] thread backfill skipped: {e}")
    findings = []
    n_multi = 0
    if not args.no_audit:
        findings, n_multi = audit()
        for f in findings[:40]:
            print(f"[audit] {f['subject']} | {f['kind']} | {f['a']} || {f['b']}")
        if not args.no_email and not args.dry_run and findings:
            send_audit_email(findings, n_multi, stats)
    print(f"[sync] done in {time.time() - t0:.1f}s: {stats}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
