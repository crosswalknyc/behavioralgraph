"""One delivered contract for every product (Jenna 2026-10-13, after
Carolyn's day: a journey that was "done" sat on the wrong shelf, needed
a reload to appear, sent no email, wrote no figures anywhere Prometheus
would read, and the chat kept answering her from a cold read).

"Done" means the user has it:

  1. the figures join the shared ledger (so the chat is bound to them),
  2. the requester is emailed with the raw files attached (From
     Prometheus, Jenna and Liz on BCC),
  3. a delivery record is written where the status lane, the where-is-my
     lane and the precedent lookup read it,
  4. the shelf / refresh facts are recorded for the widget.

`on_done(kind, ...)` runs the steps best-effort; a failure in one never
blocks the others or the job. Kinds: journey, bpiq, attribution,
flywheel, profile, subiq, package (hand-built deliverables written back
through `migration/deliverable_writeback`).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone

RECORD_PREFIX = 'system/pm_deliveries/'
DASHBOARD = 'https://dashboard.crosswalknyc.com'
KIND_LABEL = {
    'journey': 'Digital Journey', 'bpiq': 'Brand Partnership IQ read', 'attribution': 'Attribution IQ campaign',
    'flywheel': 'Flywheel read', 'profile': 'Profile IQ profile', 'subiq': 'Subscriber IQ tracker',
    'package': 'data package', 'deck': 'deck',
}
KIND_VIEW = {'journey': 'journeyIQ', 'bpiq': 'brandPartnershipIQ', 'attribution': 'attributionIQ',
             'flywheel': 'flywheelIQ', 'profile': 'profileIQ', 'subiq': 'subscriberIQ'}
WHERE = {
    'journey': 'Digital Journey IQ tab{shelf}: {name}',
    'bpiq': 'Brand Partnership IQ tab: {name}',
    'attribution': 'Attribution IQ tab: {name}',
    'flywheel': 'Flywheel tab: {name}',
    'profile': 'Profile IQ, Select Profile: {name}',
    'subiq': 'Subscriber IQ tab: {name}',
    'package': 'your email (files attached)',
    'deck': 'your email (file attached)',
}


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', str(s or '')).strip('_')[:80] or 'deliverable'


def dashboard_url(kind):
    base = (os.environ.get('APP_URL') or '').rstrip('/')
    base = base if base and 'onrender.com' not in base else DASHBOARD
    view = KIND_VIEW.get(kind)
    return f"{base}/?view={view}" if view else base


def where_line(kind, name, shelf=''):
    tpl = WHERE.get(kind, '{name}')
    shelf_txt = f", {shelf}" if shelf else ''
    return 'Find it in the dashboard under ' + tpl.format(name=name, shelf=shelf_txt) + '.' if kind not in ('package', 'deck') \
        else f"It is in {tpl.format(name=name, shelf='')}."


# ------------------------------------------------------------- raw files
def numeric_leaves_csv(payload, title=''):
    """Every numeric fact in a payload as (section, item, value) rows:
    the raw-data file for a product that has no tabular export of its
    own. Skips transport keys and ids."""
    rows = []
    skip = re.compile(r'(^|\.)(id|ts|created_at|finished_at|started_at|job_id|schema|version|color|accent|x|y|w|h)$', re.I)

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                label = v.get('label') or v.get('name') or v.get('title') if isinstance(v, dict) else None
                walk(v, f"{path}[{_slug(label)[:40] if label else i}]")
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            if skip.search(path):
                return
            sec, _, item = path.rpartition('.')
            rows.append((sec or title, item or path, o))
    walk(payload, '')
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['section', 'item', 'value'])
    for r in rows[:5000]:
        w.writerow(r)
    return buf.getvalue()


# -------------------------------------------------------------- records
def record_key(username, kind, ident):
    safe = ''.join(c for c in str(username or 'anon') if c.isalnum() or c in '-_.@').lower() or 'anon'
    return f"{RECORD_PREFIX}{safe}/{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}_{kind}_{_slug(ident)[:40]}.json"


def write_record(s3, bucket, rec):
    try:
        key = record_key(rec.get('user'), rec.get('kind'), rec.get('subject') or rec.get('key') or 'x')
        rec = dict(rec, record_key=key, recorded_at=_now_iso())
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(rec, default=str).encode('utf-8'),
                      ContentType='application/json')
        return key
    except Exception:
        traceback.print_exc()
        return ''


def list_records(s3, bucket, username, limit=40):
    """The user's delivery records, newest first. Never raises."""
    out = []
    try:
        safe = ''.join(c for c in str(username or 'anon') if c.isalnum() or c in '-_.@').lower() or 'anon'
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{RECORD_PREFIX}{safe}/")
        keys = sorted((o['Key'] for o in resp.get('Contents', []) or []), reverse=True)[:limit]
        for k in keys:
            try:
                out.append(json.loads(s3.get_object(Bucket=bucket, Key=k)['Body'].read()))
            except Exception:
                continue
    except Exception:
        traceback.print_exc()
    return out


# ------------------------------------------------------------ the contract
def _seat(username, host=None, s3_client=None, bucket='dashboard-inputs'):
    try:
        from prometheus.journey_delivery import user_record, first_name
        rec = user_record(username, host=host, s3_client=s3_client, bucket=bucket)
        return rec, first_name(rec, username)
    except Exception:
        return {}, ''


def _persist(kind, payload, subject, s3_key, figures=None, window=None):
    """The figures join the shared ring. Returns True when something landed."""
    try:
        if kind == 'journey':
            from prometheus.journey_delivery import persist_journey_figures
            return persist_journey_figures(payload or {}, s3_key=s3_key)
        import insights_ledger as il
        metrics = []
        for f in (figures or []):
            try:
                metrics.append({'name': str(f['name'])[:120], 'value': float(f['value']), 'unit': str(f.get('unit') or 'people'),
                                'definition': str(f.get('definition') or '')[:160]})
            except Exception:
                continue
        if not metrics:
            return False
        fam = {'bpiq': 'brand_partnership', 'attribution': 'attribution', 'flywheel': 'journey', 'profile': 'audience',
               'subiq': 'viewership', 'package': 'viewership', 'deck': 'audience'}.get(kind, 'audience')
        win = window or {}
        il.persist(subject=str(subject or ''), metric_family=fam, question=f"{KIND_LABEL.get(kind, kind)}: {subject}",
                   route=f'delivered_{kind}', metrics=metrics, reply=f"{KIND_LABEL.get(kind, kind)} {s3_key}".strip(),
                   window_start=str(win.get('start') or ''), window_end=str(win.get('end') or ''),
                   provenance=getattr(il, 'PROV_DELIVERED', 'delivered_artifact'))
        return True
    except Exception:
        traceback.print_exc()
        return False


def _email(kind, *, username, name, who, email, shelf, files, facts, intro, ready_message, extra_bcc, send=None, dry_run=False):
    try:
        import welcome_email as we
        if send is None:
            from prometheus.outbound_mail import send_user_email as send
        label = KIND_LABEL.get(kind, kind)
        lead = (intro or '').strip() or (ready_message or '').strip() or f"Your {label} for {name} is ready."
        where = where_line(kind, name, shelf)
        attach = ('The raw data is attached' + (f" ({len(files)} file{'s' if len(files) != 1 else ''})." if files else '.')) if files else ''
        body = "\n\n".join(p for p in [f"Hi {who}," if who else 'Hi,', lead, where, attach,
                                       f"Open it: {dashboard_url(kind)}" if kind not in ('package', 'deck') else '',
                                       'Prometheus\nCrosswalk'] if p)
        html = we.render_card_html('Your detailed breakdown.' if intro else f"Your {label.split(' ')[0] if kind != 'bpiq' else 'read'} is ready.",
                                   lead, facts=[(k, v) for k, v in (facts or [])][:4],
                                   button=(f"Open {label.split(' tab')[0]}", dashboard_url(kind)) if kind not in ('package', 'deck') else None,
                                   lines=[where, attach], title=f"{name}: {label}")
        subject = f"{name}: your {label} is ready" if not intro else f"{name}: {label}"
        return send(to=email, subject=subject, body=body, html=html, instructed=True, caller=f'delivered_{kind}',
                    files=[(data, fname, mime) for data, fname, mime in files], extra_bcc=tuple(extra_bcc or ()), dry_run=dry_run)
    except Exception as e:
        traceback.print_exc()
        return {'sent': False, 'reason': f'email failed: {e}'[:200]}


HOLD_PREFIX = 'system/pm_delivery_holds/'


def hold_key(job_id: str) -> str:
    return f"{HOLD_PREFIX}{str(job_id).strip()}.json"


def delivery_held(job_id: str, *, s3_client=None, host=None, bucket='dashboard-inputs') -> bool:
    """True when ops put a hold marker on this job's completion email.
    Write one with hold_delivery(); the ledger and the dashboard entry
    still land, only the automatic email steps aside."""
    s3 = s3_client or (getattr(host, 's3_client', None) if host is not None else None)
    if s3 is None or not job_id:
        return False
    try:
        s3.head_object(Bucket=bucket, Key=hold_key(job_id))
        return True
    except Exception:
        return False


def hold_delivery(job_id: str, *, reason: str, s3_client, bucket='dashboard-inputs') -> str:
    key = hold_key(job_id)
    s3_client.put_object(Bucket=bucket, Key=key, ContentType='application/json',
                         Body=json.dumps({'job_id': job_id, 'reason': reason, 'held_at': _now_iso()}).encode('utf-8'))
    return key


def on_done(kind, *, username, subject='', s3_key='', payload=None, host=None, s3_client=None, bucket='dashboard-inputs',
            files=(), figures=(), facts=(), shelf='', window=None, intro='', ready_message='', extra_bcc=(), to='',
            job_id='', send=None, dry_run=False, email=True):
    """Run the delivered contract. Returns the delivery record. Never raises."""
    rec = {'kind': kind, 'user': username, 'subject': subject, 'key': s3_key, 'job_id': job_id, 'shelf': shelf,
           'window': window or {}, 'files': [f[1] for f in files], 'ledger': False, 'email': {}, 'where': where_line(kind, subject, shelf)}
    try:
        rec['ledger'] = bool(_persist(kind, payload, subject, s3_key, figures=figures, window=window))
    except Exception:
        traceback.print_exc()
    try:
        seat, who = _seat(username, host=host, s3_client=s3_client, bucket=bucket)
        addr = (to or str(seat.get('email') or '')).strip()
        if email and job_id and delivery_held(job_id, s3_client=s3_client, host=host, bucket=bucket):
            # an operator is shipping this one by hand (an apology, extra files, a named BCC)
            email = False
            rec['email'] = {'sent': False, 'reason': 'held for an operator send'}
        if email and addr and '@' in addr:
            if kind == 'journey':
                from prometheus.journey_delivery import deliver_journey
                rec['email'] = deliver_journey(username=username, payload=payload or {}, s3_key=s3_key, s3_client=s3_client, host=host,
                                               intro=intro, ready_message=ready_message, extra_bcc=extra_bcc, to=addr, dry_run=dry_run, send=send)
            else:
                use_files = list(files)
                if not use_files and payload:
                    use_files = [(numeric_leaves_csv(payload, subject).encode('utf-8'), f"{_slug(subject)}_{kind}_data.csv", 'text/csv')]
                    rec['files'] = [f[1] for f in use_files]
                rec['email'] = _email(kind, username=username, name=subject or KIND_LABEL.get(kind, kind), who=who, email=addr, shelf=shelf,
                                      files=use_files, facts=facts, intro=intro, ready_message=ready_message, extra_bcc=extra_bcc,
                                      send=send, dry_run=dry_run)
        elif email:
            rec['email'] = {'sent': False, 'reason': 'no email on seat'}
    except Exception as e:
        traceback.print_exc()
        rec['email'] = {'sent': False, 'reason': f'email step failed: {e}'[:200]}
    try:
        s3 = s3_client or (getattr(host, 's3_client', None) if host is not None else None)
        if s3 is not None and not dry_run:
            rec['record_key'] = write_record(s3, bucket, rec)
    except Exception:
        traceback.print_exc()
    print(f"[delivered] {kind} {subject!r} for {username}: ledger={rec['ledger']} email={'sent' if (rec['email'] or {}).get('sent') else (rec['email'] or {}).get('reason')}")
    return rec
