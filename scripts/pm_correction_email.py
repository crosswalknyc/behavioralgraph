#!/usr/bin/env python3
"""Send a corrected read to a user and log it in their Prometheus chat
history (Jenna 2026-09-30, verbatim: "if something is wrong and we fix
it and it's emailed to a user it needs to log that history in their
prometheus chat history so it can pick up from there with that
context").

One command does both halves so neither can be skipped:
1. Email: light-design HTML + matching shareable PDF (and an optional
   CSV), From Prometheus <prometheus@crosswalknyc.com>, Reply-To
   jenna@, Jenna BCC'd.
2. History: the answer lands as an agent turn in the user's Prometheus
   thread (active thread by default) so a follow-up picks up from the
   corrected read, not from the miss.

Usage:
    python3 scripts/pm_correction_email.py \
        --user alexia --to alexia@crosswalknyc.com \
        --title "The read title" --body-file /tmp/body.txt \
        [--thread THREAD_ID] [--csv /tmp/detail.csv] \
        [--chat-text-file /tmp/chat.txt] [--date-label "September 30, 2026"] \
        [--dry-run]

The body file is plain text in the shared email grammar (blank-line
paragraphs, ALL-CAPS headers, " / " table rows, "- " bullets, ending
"Prometheus\\nCrosswalk"). The chat turn defaults to the body without
the signature; pass --chat-text-file to use different chat copy.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

S3_BUCKET = 'dashboard-inputs'
THREADS_PREFIX = 'system/synth_chat_threads'
FROM = 'Prometheus <prometheus@crosswalknyc.com>'
REPLY_TO = 'jenna@crosswalknyc.com'
BCC = 'jenna@crosswalknyc.com'

BANNED = ('synth', 'modeled', 'estimated', 'pipeline', 'hostmap',
          'panel', 'coefficient', 'odds ratio', 'regression', 'logit',
          'claude', 'hetzner', 'clickstream', 'gen pop', 'genpop',
          '\u2014', '\u2013')


def _scrub_assert(text, where):
    low = str(text or '').lower()
    bad = [b for b in BANNED if b in low]
    if bad:
        raise SystemExit(f"banned vocabulary in {where}: {bad}")


def _safe_user(username):
    return ''.join(c for c in (username or 'anon')
                   if c.isalnum() or c in '-_.@').lower()


def _strip_signature(body):
    lines = body.rstrip().split('\n')
    if len(lines) >= 2 and lines[-1].strip().lower() == 'crosswalk' \
            and lines[-2].strip().lower() == 'prometheus':
        return '\n'.join(lines[:-2]).rstrip()
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', required=True,
                    help='dashboard username for the thread')
    ap.add_argument('--to', required=True, help='recipient email')
    ap.add_argument('--title', required=True)
    ap.add_argument('--body-file', required=True)
    ap.add_argument('--thread', default='',
                    help='thread id (default: active thread)')
    ap.add_argument('--csv', default='', help='optional CSV attachment')
    ap.add_argument('--chat-text-file', default='')
    ap.add_argument('--date-label', default='')
    ap.add_argument('--subject', default='',
                    help='email subject (default: the title)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    body_text = open(args.body_file, encoding='utf-8').read().strip()
    if not body_text:
        raise SystemExit('empty body')
    chat_text = (open(args.chat_text_file, encoding='utf-8').read().strip()
                 if args.chat_text_file else _strip_signature(body_text))
    _scrub_assert(body_text, 'email body')
    _scrub_assert(chat_text, 'chat turn')
    _scrub_assert(args.title, 'title')

    import prometheus_email_html as peh
    import prometheus_email_pdf as pep
    date_label = args.date_label or datetime.now(
        timezone.utc).strftime('%B %d, %Y')
    html = peh.render_answer_email_html(args.title, body_text,
                                        date_label=date_label)
    if not html:
        raise SystemExit('html render failed')
    pdf = pep.render_answer_pdf(args.title, body_text,
                                date_label=date_label)
    safe = re.sub(r'[^A-Za-z0-9]+', '_', args.title).strip('_')[:60]

    msg = MIMEMultipart('mixed')
    msg['Subject'] = args.subject or args.title
    msg['From'] = FROM
    msg['To'] = args.to
    msg['Reply-To'] = REPLY_TO
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(body_text, 'plain', 'utf-8'))
    alt.attach(MIMEText(html, 'html', 'utf-8'))
    msg.attach(alt)
    if pdf:
        p = MIMEApplication(pdf, _subtype='pdf')
        p.add_header('Content-Disposition', 'attachment',
                     filename=f'{safe or "Crosswalk_Read"}.pdf')
        msg.attach(p)
    if args.csv:
        c = MIMEApplication(open(args.csv, 'rb').read(), _subtype='csv')
        c.add_header('Content-Disposition', 'attachment',
                     filename=os.path.basename(args.csv))
        msg.attach(c)

    dests = [args.to]
    if BCC.lower() != args.to.lower():
        dests.append(BCC)

    if args.dry_run:
        print(f'DRY RUN: would send "{msg["Subject"]}" to {dests}, '
              f'pdf={len(pdf or b"")}B, then bank to thread')
        return

    import boto3
    ses = boto3.client('ses', region_name='us-east-2')
    resp = ses.send_raw_email(Source=FROM, Destinations=dests,
                              RawMessage={'Data': msg.as_string()})
    print('sent:', resp['MessageId'])

    # ---- Bank the corrected read into the Prometheus thread ----
    s3 = boto3.client('s3', region_name='us-east-2')
    user = _safe_user(args.user)
    ikey = f'{THREADS_PREFIX}/{user}/index.json'
    idx = json.loads(s3.get_object(Bucket=S3_BUCKET,
                                   Key=ikey)['Body'].read())
    tid = args.thread or idx.get('active') or (
        idx['threads'][0]['id'] if idx.get('threads') else '')
    if not tid:
        raise SystemExit(f'no thread found for {user}')
    tkey = f'{THREADS_PREFIX}/{user}/{tid}.json'
    try:
        th = json.loads(s3.get_object(Bucket=S3_BUCKET,
                                      Key=tkey)['Body'].read())
    except Exception:
        th = []
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    th.append({'role': 'agent', 'text': chat_text, 'ts': now,
               'meta': {'source': 'correction_email',
                        'kind': 'read', 'title': args.title}})
    th = th[-200:]
    s3.put_object(Bucket=S3_BUCKET, Key=tkey,
                  Body=json.dumps(th, indent=2).encode('utf-8'),
                  ContentType='application/json')
    for t in idx.get('threads', []):
        if t.get('id') == tid:
            t['updated'] = now
            t['turns'] = len(th)
    s3.put_object(Bucket=S3_BUCKET, Key=ikey,
                  Body=json.dumps(idx, indent=2).encode('utf-8'),
                  ContentType='application/json')
    print(f'banked to thread {tid}: {len(th)} turns')


if __name__ == '__main__':
    main()
