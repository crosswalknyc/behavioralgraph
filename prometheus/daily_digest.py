"""The same-day Prometheus digest (I10, 2026-10-20).

One plain email a day, 18:00 UTC, to jenna@ and jessie@ (ops mail; Liz
is not on failure or system mail): the day's corrections and repeats
(replies the reader graded failed_by_user in real time), the promises
that were held or never landed, and the asks that faulted. No
per-failure mail beyond the standing chatbot error email; the System
Status watch card carries the same items the moment they happen.

    collect(day, s3)  -> the day's document
    render(doc)       -> the plain-text body (no internal terms)
    send(doc, ...)    -> SES to RECIPIENTS + system/ops/pm_daily_digest/<day>.json
    main()            -> the timer entry point (migration/pm_daily_digest.py)

Fail-safe: a missing prefix is an empty list, a bad record is skipped,
nothing here raises into the timer. Offline-safe: every S3 touch goes
through the injected client so the suite can hand it a fake.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import traceback

BUCKET = 'dashboard-inputs'
ASK_PREFIX = 'system/usage/ask_log/'
SIGNAL_PREFIX = 'system/usage/ask_log/_signals/'
HELD_PREFIX = 'system/ops/held_replies/'
OUT_PREFIX = 'system/ops/pm_daily_digest/'
RECIPIENTS = ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com']
SENDER = 'BehavioralGraph <jenna@crosswalknyc.com>'
_SKIP_USERS = ('', 'unknown', 'canary', 'replay')
_HELD_OUTCOMES = ('held', 'faulted', 'promised')
_INTERNAL_RX = re.compile(r"\b(?:synth\w*|modeled|modelled|hostmap|clickstream|panelists?|tier\s*[12]|gen\s*pop|claude|anthropic)\b", re.I)


def _today():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')


def _list(s3, prefix):
    keys, tok = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=prefix)
        if tok:
            kw['ContinuationToken'] = tok
        try:
            r = s3.list_objects_v2(**kw)
        except Exception:
            return keys
        for o in r.get('Contents', []) or []:
            k = str(o.get('Key') or '')
            if k.endswith('.json'):
                keys.append(k)
        if not r.get('IsTruncated'):
            return keys
        tok = r.get('NextContinuationToken')


def _get(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8', 'replace'))
    except Exception:
        return None


def _live_user(u):
    u = str(u or '')
    return u not in _SKIP_USERS and not u.startswith('canary')


def _ask_records(s3, day):
    out = []
    for k in _list(s3, f'{ASK_PREFIX}{day}/'):
        rec = _get(s3, k)
        if isinstance(rec, dict) and rec.get('question') and _live_user(rec.get('user')):
            out.append(rec)
    out.sort(key=lambda r: str(r.get('ts') or ''))
    return out


def collect(day=None, s3=None):
    """The day's document: corrections, repeats, held promises, faults,
    plus the counts the watch card shows."""
    day = str(day or _today())[:10]
    if s3 is None:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
    doc = {'date': day, 'corrections': [], 'repeats': [], 'held': [], 'faults': [], 'asks': 0, 'users': 0}
    try:
        recs = _ask_records(s3, day)
        doc['asks'] = len(recs)
        doc['users'] = len({str(r.get('user') or '') for r in recs})
        for r in recs:
            oc = str(r.get('outcome') or '')
            if oc in ('faulted', 'error'):
                doc['faults'].append({'ts': r.get('ts'), 'user': r.get('user'), 'question': r.get('question'),
                                      'route': r.get('route'), 'subject': r.get('subject')})
            elif oc in _HELD_OUTCOMES or (r.get('job_id') and oc not in ('landed', 'answered')):
                doc['held'].append({'ts': r.get('ts'), 'user': r.get('user'), 'question': r.get('question'),
                                    'job_id': r.get('job_id'), 'outcome': oc, 'subject': r.get('subject')})
        for k in _list(s3, f'{SIGNAL_PREFIX}{day}/'):
            sig = _get(s3, k)
            if not isinstance(sig, dict) or not _live_user(sig.get('user')):
                continue
            item = {'ts': sig.get('ts'), 'user': sig.get('user'), 'question': sig.get('question'),
                    'reply': sig.get('prior_reply'), 'reason': sig.get('reason')}
            (doc['repeats'] if str(sig.get('reason') or '') == 'repeat' else doc['corrections']).append(item)
        for k in _list(s3, f'{HELD_PREFIX}{day}/'):
            h = _get(s3, k)
            if isinstance(h, dict) and _live_user(h.get('user')) and str(h.get('status') or 'open') == 'open':
                doc['held'].append({'ts': h.get('opened_at'), 'user': h.get('user'), 'question': h.get('question'),
                                    'job_id': h.get('id'), 'outcome': 'held', 'told': h.get('told')})
    except Exception:
        traceback.print_exc()
    return doc


def _clean(s, n=220):
    s = ' '.join(str(s or '').split())
    s = _INTERNAL_RX.sub('', s)
    s = s.replace('\u2014', '-').replace('\u2013', '-')
    return (s[:n] + ('...' if len(s) > n else '')) if s else ''


def _line(item, with_reply=False):
    ts = str(item.get('ts') or '')[11:16]
    who = _clean(item.get('user'), 40) or 'someone'
    q = _clean(item.get('question'))
    out = f"- {ts} UTC  {who}: {q}" if ts else f"- {who}: {q}"
    if with_reply and item.get('reply'):
        out += f"\n    the reply they pushed back on: {_clean(item.get('reply'), 160)}"
    if item.get('subject'):
        out += f"\n    subject: {_clean(item.get('subject'), 80)}"
    if item.get('outcome') and item.get('outcome') != 'held' and item.get('job_id'):
        out += f"\n    status: {_clean(item.get('outcome'), 30)}"
    return out


def render(doc):
    """Plain text; every section present even when empty so the day
    reads the same each day. No internal terms, no dashes."""
    day = str(doc.get('date') or _today())
    parts = [f"Prometheus, {day}: {int(doc.get('asks') or 0)} questions from {int(doc.get('users') or 0)} people.", '']
    sections = (('Corrections (the person said the answer was wrong)', doc.get('corrections') or [], True),
                ('Repeats (the same question asked again)', doc.get('repeats') or [], True),
                ('Promises held or not landed', doc.get('held') or [], False),
                ('Faults (the reply did not come back)', doc.get('faults') or [], False))
    for title, items, with_reply in sections:
        parts.append(f"{title}: {len(items)}")
        if not items:
            parts.append('- none')
        for it in items[:40]:
            parts.append(_line(it, with_reply))
        if len(items) > 40:
            parts.append(f"- and {len(items) - 40} more")
        parts.append('')
    parts.append('The same items sit on the System Status watch card as they happen. Nothing in this note went to a user.')
    return '\n'.join(parts)


def send(doc, s3=None, dry=False, recipients=None):
    """Write the day's digest to S3 and send it to the ops recipients.
    Returns {'sent': bool, 'key': str, 'body': str}."""
    body = render(doc)
    subject = (f"Prometheus day {doc.get('date')}: {len(doc.get('corrections') or [])} corrections, "
               f"{len(doc.get('repeats') or [])} repeats, {len(doc.get('held') or [])} held, {len(doc.get('faults') or [])} faults")
    key = f"{OUT_PREFIX}{doc.get('date')}.json"
    out = {'sent': False, 'key': key, 'body': body, 'subject': subject}
    if s3 is None and not dry:
        try:
            import boto3
            s3 = boto3.client('s3', region_name='us-east-2')
        except Exception:
            s3 = None
    if s3 is not None:
        try:
            s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps({**doc, 'body': body, 'subject': subject}).encode('utf-8'),
                          ContentType='application/json')
        except Exception:
            traceback.print_exc()
    if dry or os.environ.get('PM_MAIL_OFF') == '1' or os.environ.get('REGRESSION_TEST_MODE') == '1':
        return out
    try:
        import boto3
        boto3.client('ses', region_name='us-east-2').send_email(
            Source=SENDER, Destination={'ToAddresses': list(recipients or RECIPIENTS)},
            Message={'Subject': {'Data': subject}, 'Body': {'Text': {'Data': body}}})
        out['sent'] = True
    except Exception:
        traceback.print_exc()
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='The same-day Prometheus digest (18:00 UTC).')
    ap.add_argument('--day', default=None, help='YYYY-MM-DD (default: today UTC)')
    ap.add_argument('--dry-run', action='store_true', help='print the body, send nothing, write nothing')
    args = ap.parse_args(argv)
    doc = collect(args.day)
    res = send(doc, dry=args.dry_run)
    print(res['subject'])
    print(res['body'])
    print(f"[digest] sent={res['sent']} key={res['key']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
