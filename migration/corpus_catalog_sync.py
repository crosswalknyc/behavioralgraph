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

Every size the audit compares is projected US people (a profile's US
Gen Pop Projection, a journey's end point, a partnership's reach); a
profile's count inside the 10 million sample is kept as its own fact
and never compared across products. A Profile IQ Total Universe that
sits below another product's figure on a shorter overlapping window is
corrected IN PLACE by the reconcile step (migration/corpus_coherence:
the universe lifts above the figure under its own dated filename, its
cuts follow, the catalog re-indexes) and the mail carries only what the
sweep could not settle itself (2026-10-20, Jenna: "have the system
realize that so no need for the email").
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
def _attach_samples(facts):
    """A profile's audience fact carries its sibling sample (same source
    file) as ``_sample`` so a line can show both figures: '260,390 US
    people (7,893 inside the 10 million sample)'. The 2026-10-20 sweep
    note read 'US audience = 260,390' as a sample next to a journey's
    projection; it was a projection, and the line now says so."""
    samples = {}
    for f in facts:
        if f.get('product') == 'profile' and f.get('kind') == 'sample_size':
            samples[(f.get('source') or {}).get('key') or ''] = f.get('value')
    for f in facts:
        if f.get('product') == 'profile' and f.get('kind') == 'audience_size':
            s = samples.get((f.get('source') or {}).get('key') or '')
            if s:
                f['_sample'] = s
    return facts


def _val(f):
    try:
        return float(f.get('value'))
    except (TypeError, ValueError):
        return 0.0


def audit_facts(facts, subject=''):
    """The disagreements among one subject's facts. Every rule asks the
    same three things (2026-10-21, Jenna, after eight '<Title> on
    <Platform>' journeys were paired with the platform's own profile):
    the two sources share an identity (they sit on one subject page),
    one window is CONTAINED in the other (identical counts; a mere
    overlap never does), and the figure that must sit below sits
    above. Every figure is projected US people. A subset by
    construction is never a finding: a cut inside its universe, a
    journey's steps inside its own base, a title's watchers inside a
    universe that is larger.

      universe_below_subset   Profile IQ universe (a parent file) below
                              another product's total on a window
                              inside the profile's. Fixable: the
                              universe lifts in place.
      sub_window_exceeds      two other products, like steps, the one
                              on the contained window above the one on
                              the containing window.
      same_window_disagree    two other products, like steps, identical
                              windows, apart by more than 35%.

    Returns findings; each carries the two facts (``profile`` / ``other``
    for the universe rule, ``inner`` / ``outer`` for the rest)."""
    facts = _attach_samples([f for f in (facts or []) if isinstance(f, dict)])
    sizes = [f for f in facts if f.get('kind') in ('audience_size', 'stage_count')
             and f.get('unit') == 'people' and f.get('product') != 'chat'
             and not (f.get('product') == 'profile' and f.get('cut')) and _val(f) > 0]
    universes = [f for f in sizes if f.get('product') == 'profile' and f.get('kind') == 'audience_size']
    others = [f for f in sizes if f.get('product') != 'profile']
    findings = []
    # Rule 1: a Profile IQ universe holds everything another product
    # measured on a window inside the profile's own. Above it: fine,
    # whatever the windows (a universe is the superset). Below it: a
    # finding, fixed in place by the reconcile step.
    for p in universes:
        for o in others:
            if o.get('kind') != 'audience_size':
                continue        # a path step is not a total
            if not cc.window_within(o.get('window') or {}, p.get('window') or {}):
                continue
            if _val(o) > _val(p) * 1.05:
                findings.append({'subject': subject, 'kind': 'universe_below_subset',
                                 'a': _fact_line(o), 'b': _fact_line(p), 'profile': p, 'other': o})
    # Rules 2 and 3: between two other products, like with like only.
    for i, a in enumerate(others):
        for b in others[i + 1:]:
            if a.get('product') == b.get('product') or not _comparable(a, b):
                continue
            wa, wb = a.get('window') or {}, b.get('window') or {}
            va, vb = _val(a), _val(b)
            if cc.same_window(wa, wb):
                if max(va, vb) / min(va, vb) > 1.35:
                    small, big = (a, b) if va < vb else (b, a)
                    findings.append({'subject': subject, 'kind': 'same_window_disagree',
                                     'a': _fact_line(small), 'b': _fact_line(big), 'inner': small, 'outer': big})
                continue
            if cc.window_within(wa, wb):
                inner, outer = a, b
            elif cc.window_within(wb, wa):
                inner, outer = b, a
            else:
                continue        # overlap without containment is never a sub-window case
            if _val(inner) > _val(outer) * 1.05:
                findings.append({'subject': subject, 'kind': 'sub_window_exceeds',
                                 'a': _fact_line(inner), 'b': _fact_line(outer), 'inner': inner, 'outer': outer})
    return findings


def audit(limit_subjects=5000):
    """Same-subject disagreements across products over the whole
    catalog (see audit_facts for the rules). Returns (findings,
    subjects described by two or more products)."""
    idx = cc.load_index(force=True)
    findings = []
    subs = idx.get('subjects') or {}
    multi = [(k, v) for k, v in subs.items() if len(v.get('products') or []) >= 2]
    for skey, ent in multi[:limit_subjects]:
        page = cc.load_subject_page(skey, force=True) or {}
        findings.extend(audit_facts(page.get('facts') or [], subject=ent.get('subject')))
    print(f"[audit] subjects with 2+ products: {len(multi)}, findings: {len(findings)}")
    return findings, len(multi)


def reconcile(findings, dry_run=False, s3=None):
    """Correct every finding of the fixable shape in place and return
    (fixed, unresolved). Fixable: a Profile IQ Total Universe (the long
    side, a parent file) sits below another product's US-people figure
    on a shorter or equal overlapping window. The universe lifts above
    that figure under its own dated filename, its cuts follow, the
    catalog re-indexes, and the finding is closed without mail
    (no-rebuild-level-correction.mdc). Anything else (two journeys, a
    journey against a campaign) stays for a human."""
    from migration import corpus_coherence as coh
    fixed, unresolved = [], []
    s3 = s3 or _s3()
    stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
    done_keys = set()
    for f in findings:
        profile, other = f.get('profile'), f.get('other')
        if not (f.get('kind') == 'universe_below_subset' and isinstance(profile, dict)
                and isinstance(other, dict) and profile.get('product') == 'profile'
                and other.get('product') not in ('profile', 'chat')
                and profile.get('kind') == 'audience_size' and not profile.get('cut')):
            unresolved.append(f)
            continue
        key = (profile.get('source') or {}).get('key') or ''
        if key in done_keys:
            fixed.append(dict(f, trail={'key': key, 'note': 'lifted earlier in this pass'}))
            continue
        trail = coh.reconcile_universe(s3, profile, other, dry_run=dry_run, stamp=stamp)
        if not trail or trail.get('error'):
            unresolved.append(dict(f, trail=trail))
            continue
        done_keys.add(key)
        fixed.append(dict(f, trail=trail))
        print(f"[audit] fixed in place: {f['subject']} | {f['kind']} | {key}: "
              f"{trail.get('old_people', 0):,} -> {trail.get('new_people', 0):,} US people"
              + (f" ({len(trail.get('cuts') or [])} cut(s) followed)" if trail.get('cuts') else '')
              + (' [dry run]' if dry_run else ''))
    return fixed, unresolved


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
    """One figure, as the reader should read it: a people count says
    'US people'; a profile's audience shows its sample alongside so the
    two can never be mistaken for each other."""
    unit = ' US people' if f.get('unit') == 'people' else ''
    tail = ''
    if f.get('product') == 'profile' and f.get('kind') == 'audience_size' and f.get('_sample'):
        try:
            tail = f" ({int(round(float(f['_sample']))):,} inside the 10 million sample)"
        except (TypeError, ValueError):
            tail = ''
    return (f"{f.get('note') or f.get('product')}: {f.get('label')} = {cc._fmt_value(f)}{unit}{tail}"
            f"{cc._fmt_window(f.get('window'))}")


def send_audit_email(findings, n_multi, stats, fixed=()):
    """The sweep mails ONLY what it could not settle itself. Both sides
    of every line are projected US people. Nothing left to look at means
    no mail at all, never an empty one (2026-10-21, Jenna: 'getting so
    many of these')."""
    if not findings:
        print('[audit] nothing to mail')
        return ''
    import boto3
    lines = [f"Corpus catalog sweep {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
             '',
             f"Profiles indexed this run: {stats.get('profiles', 0)}; journeys: {stats.get('journeys', 0)}; "
             f"attribution campaigns: {stats.get('attribution', 0)}; brand partnership reads: {stats.get('bpiq', 0)}; "
             f"sources removed: {stats.get('gone', 0)}.",
             f"Subjects described by two or more products: {n_multi}.",
             '']
    if fixed:
        lines.append(f"{len(fixed)} universe(s) brought into line in place this run (no action needed).")
        lines.append('')
    lines.append(f"{len(findings)} cross-product disagreement(s) to look at (every figure is projected US people; "
                 f"the first line is the figure that sits above, the second the one it had to sit under):")
    lines.append('')
    for f in findings[:60]:
        lines.append(f"- {f['subject']} ({f['kind'].replace('_', ' ')})")
        lines.append(f"    {f['a']}")
        lines.append(f"    {f['b']}")
    body = '\n'.join(lines)
    subj = f"Corpus catalog: {len(findings)} cross-product disagreement(s)"
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
    ap.add_argument('--force-profiles', action='store_true',
                    help='re-read every profile CSV regardless of its stored ETag (after a change '
                         'to the profile reader); journeys, campaigns and reads keep their ETag skip')
    ap.add_argument('--force-journeys', action='store_true',
                    help='re-read every journey payload regardless of its stored ETag (after a change '
                         'to the journey identity reader)')
    ap.add_argument('--no-reconcile', action='store_true',
                    help='report disagreements without correcting the fixable ones in place')
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
        if args.force_profiles:
            sources = {k: v for k, v in sources.items() if (v or {}).get('product') != 'profile'}
            print('[sync] --force-profiles: every profile CSV will be re-read')
        if args.force_journeys:
            # a journey whose identity moved (2026-10-21, 'Nip/Tuck on Hulu'
            # from Hulu to Nip/Tuck) leaves its old page inside bulk_upsert
            sources = {k: v for k, v in sources.items() if (v or {}).get('product') != 'journey'}
            print('[sync] --force-journeys: every journey payload will be re-read')
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
    fixed = []
    if not args.no_audit:
        findings, n_multi = audit()
        for f in findings[:40]:
            print(f"[audit] {f['subject']} | {f['kind']} | {f['a']} || {f['b']}")
        if findings and not args.no_reconcile:
            # the fixable shape is corrected in place and silently; the
            # catalog re-indexes as each universe lifts, so a second audit
            # shows only what is still live, and only that can mail
            fixed, findings = reconcile(findings, dry_run=args.dry_run)
            if fixed and not args.dry_run:
                findings, n_multi = audit()
            stats['universes_lifted'] = len(fixed)
        if not args.no_email and not args.dry_run and findings:
            send_audit_email(findings, n_multi, stats, fixed=fixed)
    print(f"[sync] done in {time.time() - t0:.1f}s: {stats}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
