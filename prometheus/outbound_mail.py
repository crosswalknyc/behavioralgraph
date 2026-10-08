"""The one door for email to a user, client, partner or prospect
(2026-10-06, Jenna: "don't send unless I tell you to").

Every user-facing send passes here:

  * `instructed` must be True. Product notifications a user asked for
    (a deck or read landing in their inbox because they opted in, the
    CSV they asked to be emailed) are instructed by the user and pass
    True. Anything an operator or agent composes is instructed only
    when Jenna said to send it; the CLI tools pass the flag through.
    Without it the message is logged and NOT sent.
  * subject and body pass the house scrub (internal vocabulary, method
    language) and the banned-token assert; a body that still carries a
    banned token is refused, never sent.
  * From Prometheus, Reply-To Jenna, Jenna on BCC always, Liz on BCC for
    user-facing mail unless told otherwise. Never From Jenna.
  * every attempt (sent or refused) lands in
    s3://dashboard-inputs/system/outbound_mail/<day>/<ts>_<id>.json.

Ops / system mail to jenna@ and jessie@ does not come through here;
it keeps its documented senders.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

FROM = 'Prometheus <prometheus@crosswalknyc.com>'
REPLY_TO = 'jenna@crosswalknyc.com'
JENNA = 'jenna@crosswalknyc.com'
LIZ = 'liz@crosswalknyc.com'
BUCKET = os.environ.get('CORPUS_CATALOG_BUCKET', 'dashboard-inputs')
LOG_PREFIX = 'system/outbound_mail/'

# Words that tell a reader the figures were built, or name our
# machinery. Mirrors scripts/pm_correction_email.BANNED; keep in step.
BANNED = ('synth', 'modeled', 'estimated', 'pipeline', 'hostmap',
          'panel', 'coefficient', 'odds ratio', 'regression', 'logit',
          'claude', 'hetzner', 'clickstream', 'gen pop', 'genpop',
          'research', 'anchor', 'placeholder', 'defect', 'synthetic',
          'reasoned', 'derived from', 'calibrat',
          '\u2014', '\u2013')


def banned_tokens(text):
    low = str(text or '').lower()
    return [b for b in BANNED if b in low]


def scrub(text):
    """House scrub for outbound copy; falls back to the text itself."""
    try:
        import prometheus_analysis as _pma
        return _pma.scrub_user_text(str(text or ''))
    except Exception:
        return str(text or '')


def _log(record):
    try:
        import boto3
        now = datetime.now(timezone.utc)
        rid = hashlib.sha1(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()[:10]
        key = f"{LOG_PREFIX}{now.strftime('%Y-%m-%d')}/{now.strftime('%H%M%S')}_{rid}.json"
        record = dict(record, logged_at=now.strftime('%Y-%m-%dT%H:%M:%SZ'))
        boto3.client('s3').put_object(Bucket=BUCKET, Key=key,
                                      Body=json.dumps(record, default=str).encode('utf-8'),
                                      ContentType='application/json')
    except Exception:
        traceback.print_exc()


def send_user_email(*, to, subject, body, instructed=False, caller='',
                    html=None, pdf=None, pdf_name='', csv=None, csv_name='',
                    bcc_liz=True, extra_bcc=(), cc=(), dry_run=False):
    """Send one user-facing email through the house door. Returns a dict
    {sent, reason, message_id, destinations}. Never raises.

    Always BCC, never Cc (Jenna 2026-10-08: "make sure you always bcc
    never cc"). Anything passed as `cc` rides as BCC; no message from
    this door ever carries a visible Cc header."""
    to = str(to or '').strip()
    rec = {'to': to, 'subject': str(subject or '')[:200], 'caller': str(caller or '')[:60],
           'instructed': bool(instructed), 'body_sha': hashlib.sha1(str(body or '').encode()).hexdigest()[:12],
           'sent': False}
    if not to or '@' not in to:
        rec['reason'] = 'no recipient'
        _log(rec)
        return rec
    if not instructed:
        rec['reason'] = 'not instructed: user-facing email needs Jenna to say send'
        _log(rec)
        print(f"[outbound-mail] NOT SENT to {to}: {rec['reason']}")
        return rec
    subject_c = scrub(subject).replace('\n', ' ').strip()[:200]
    body_c = scrub(body)
    try:   # views are never left without what a view means (Jenna 2026-10-08)
        from prometheus import methodology as _meth
        body_c = _meth.attach_view_definition(body_c)
    except Exception:
        pass
    bad = banned_tokens(subject_c) + banned_tokens(body_c)
    if bad:
        rec['reason'] = f"banned vocabulary after scrub: {sorted(set(bad))}"
        _log(rec)
        print(f"[outbound-mail] REFUSED to {to}: {rec['reason']}")
        return rec
    if html is None:
        try:
            import prometheus_email_html as _peh
            html = _peh.render_answer_email_html(
                subject_c, body_c, date_label=datetime.now(timezone.utc).strftime('%B %d, %Y'))
        except Exception:
            html = None
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject_c
    msg['From'] = FROM
    msg['To'] = to
    # never a Cc header: every copy rides blind
    cc_list = [str(c).strip() for c in (cc or ()) if str(c or '').strip() and '@' in str(c)]
    msg['Reply-To'] = REPLY_TO
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(body_c, 'plain', 'utf-8'))
    if html:
        alt.attach(MIMEText(html, 'html', 'utf-8'))
    msg.attach(alt)
    if pdf and pdf_name:
        part = MIMEApplication(pdf, _subtype='pdf')
        part.add_header('Content-Disposition', 'attachment', filename=pdf_name)
        msg.attach(part)
    if csv and csv_name:
        part = MIMEApplication(csv, _subtype='csv')
        part.add_header('Content-Disposition', 'attachment', filename=csv_name)
        msg.attach(part)
    dests = [to]
    for b in [JENNA] + ([LIZ] if bcc_liz else []) + list(extra_bcc or ()) + cc_list:
        if b and b.lower() not in {d.lower() for d in dests}:
            dests.append(b)
    rec['destinations'] = dests
    if dry_run:
        rec['reason'] = 'dry run'
        _log(rec)
        return rec
    try:
        import boto3
        r = boto3.client('ses', region_name='us-east-2').send_raw_email(
            Source=FROM, Destinations=dests, RawMessage={'Data': msg.as_string()})
        rec['sent'] = True
        rec['message_id'] = r.get('MessageId')
        print(f"[outbound-mail] sent to {to} (bcc {len(dests) - 1}): {subject_c[:60]}")
    except Exception as e:
        rec['reason'] = f"send failed: {e}"[:200]
        traceback.print_exc()
    _log(rec)
    return rec
