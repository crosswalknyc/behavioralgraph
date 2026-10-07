"""Email Jenna when anyone asks Prometheus something.

Jenna, 2026-09-24: email her any time someone from Paramount+ or Sony
asks a question, with the question and the answer Prometheus gave.

Jenna, 2026-09-25: do it for every account, not only those two.

Jenna, 2026-09-30: "bcc liz on all prometheus questions."

Jenna, 2026-10-07: "email the questions to jessie too not just me and
liz for all prometheus queries."

The send never raises into the chat path.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# (label shown in the subject, company substrings, email domain suffixes)
_WATCH = (
    ('Paramount+', ('paramount',), ('paramount.com',)),
    ('Sony', ('sony',), ('sony.com', 'spe.sony.com')),
)

_TO = 'jenna@crosswalknyc.com'
_BCC = ('liz@crosswalknyc.com', 'jessie@crosswalknyc.com')
_FROM = 'Prometheus <prometheus@crosswalknyc.com>'
_REPLY_TO = 'jenna@crosswalknyc.com'

# Immediate replies that mean the real answer is still being written.
# The finished job sends the actual answer, so these are not emailed.
_PLACEHOLDER_PREFIXES = ('On it.', 'Already on it')


def is_placeholder(payload):
    """True when the finished job will email the real answer, so this
    response never sends. Covers the plain ack (text in 'reply'), the
    guidance shape (text in 'error'), and any payload that carries a
    read_job_id - the background read emails on completion either way
    (2026-10-01; cbisson's 14-title ask emailed 'ANSWER True' because
    the guidance-wrapped ack rode 'error' and slipped the skip)."""
    if not isinstance(payload, dict):
        return False
    if payload.get('read_job_id'):
        return True
    for field in ('reply', 'error'):
        v = payload.get(field)
        if isinstance(v, str) and v.startswith(_PLACEHOLDER_PREFIXES):
            return True
    return False


def user_record(doc, username):
    """The account dict for a username.

    load_users() returns the full document, with accounts under
    'users'. A username-keyed map still works.
    """
    if not isinstance(doc, dict):
        return {}
    name = str(username or '').strip()
    if not name:
        return {}
    inner = doc.get('users')
    if isinstance(inner, dict) and isinstance(inner.get(name), dict):
        return inner[name]
    rec = doc.get(name)
    return rec if isinstance(rec, dict) else {}


def watched_label(email, company):
    """Label for the note. Paramount+ and Sony keep those names.
    Every other account still sends, under its company or domain."""
    em = str(email or '').strip().lower()
    co = str(company or '').strip()
    domain = em.split('@', 1)[1] if '@' in em else ''
    col = co.lower()
    for label, companies, domains in _WATCH:
        if any(c in col for c in companies):
            return label
        if any(domain == d or domain.endswith('.' + d) for d in domains):
            return label
    if co:
        return co
    if domain:
        return domain
    return 'Dashboard'


def answer_text(payload):
    """The words the user was shown, or a plain summary of a build
    draft. '' for a placeholder that the finished job will replace."""
    if not isinstance(payload, dict):
        return ''
    reply = payload.get('reply')
    reply = reply.strip() if isinstance(reply, str) else ''
    if reply:
        if reply.startswith(_PLACEHOLDER_PREFIXES):
            return ''
        return reply[:6000]
    draft = payload.get('spec_draft')
    if isinstance(draft, dict):
        lines = []
        decision = str(draft.get('decision') or '').replace('_', ' ')
        subject = str(draft.get('subject')
                      or payload.get('subject_label') or '').strip()
        head = ' '.join(x for x in (
            f"Came back with a {decision}" if decision else '',
            f"for {subject}" if subject else '') if x)
        if head:
            lines.append(head.strip() + '.')
        window = str(payload.get('date_window') or '').strip()
        if window:
            lines.append(f"Window: {window}")
        if payload.get('needs_date_clarification'):
            lines.append("Asked which time window to use before running.")
        sentence = str(draft.get('estimated_audience_sentence') or '').strip()
        if sentence:
            lines.append(sentence)
        credits = payload.get('estimated_credits')
        if credits is not None:
            lines.append(f"Credits on approval: {credits}")
        return '\n'.join(lines)[:4000]
    if payload.get('success') is False:
        # Guidance shape (2026-10-01): 'guidance' is a boolean FLAG in
        # the house shape and the words ride in 'error'. Only text
        # fields ever email - str(True) under ANSWER was the defect
        # Jenna flagged on cbisson's 14-title ask. 'error' first (house
        # shape), then 'guidance' for any legacy payload that carried
        # the words there, then 'message'.
        for field in ('error', 'guidance', 'message'):
            v = payload.get(field)
            if isinstance(v, str) and v.strip():
                if v.strip().startswith(_PLACEHOLDER_PREFIXES):
                    return ''
                return v.strip()[:6000]
        return 'Could not answer.'
    return ''


def _correct_link(username, question, answer, subject):
    """Signed correction link (2026-10-01 learning loop). '' on any
    failure; the email still sends without it."""
    try:
        import prometheus_corrections as pmc
        _cid, url = pmc.stash_context(username, question, answer,
                                      subject=subject or '')
        return url or ''
    except Exception:
        traceback.print_exc()
        return ''


def _html(who, label, email, when, subject, question, answer,
          correct_url=''):
    ink, body, muted, olive, off = (
        '#0C1618', '#5C6560', '#888C89', '#5E7E12', '#E9E8E1')
    def block(title, text):
        safe = (str(text or '')
                .replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('\n', '<br>'))
        return (f"<div style='font-size:11px;letter-spacing:.08em;"
                f"text-transform:uppercase;color:{olive};font-weight:600;"
                f"margin:22px 0 6px'>{title}</div>"
                f"<div style='background:#fff;border-radius:12px;"
                f"padding:14px 16px;color:{ink};font-size:15px;"
                f"line-height:1.5'>{safe}</div>")
    subj = (f"<div style='color:{muted};font-size:13px;margin-top:4px'>"
            f"Open profile: {subject}</div>" if subject else '')
    return (
        f"<html><body style='margin:0;padding:0;background:{off};"
        f"font-family:Helvetica,Arial,sans-serif;color:{body}'>"
        f"<div style='max-width:680px;margin:0 auto;padding:32px 24px'>"
        f"<div style='font-size:11px;letter-spacing:.12em;text-transform:"
        f"uppercase;color:{olive};font-weight:600'>Prometheus</div>"
        f"<h1 style='font-size:22px;color:{ink};font-weight:700;"
        f"margin:10px 0 4px'>{who} at {label} asked a question.</h1>"
        f"<div style='color:{muted};font-size:13px'>{email} · {when}</div>"
        f"{subj}"
        f"{block('Question', question)}"
        f"{block('Answer', answer or 'No written answer on this step.')}"
        + ((f"<div style='margin:20px 0 0'><a href='{correct_url}' "
            f"style='display:inline-block;background:{ink};"
            f"color:{off};text-decoration:none;border-radius:10px;"
            f"padding:10px 18px;font-size:14px;font-weight:600'>"
            f"Correct this answer</a>"
            f"<div style='color:{muted};font-size:12px;margin-top:6px'>"
            f"A correction retires this answer and holds for every "
            f"future ask like it.</div></div>") if correct_url else '')
        + f"<div style='margin-top:28px;color:{ink}'>Prometheus<br>"
        f"<span style='color:{muted}'>Crosswalk</span></div>"
        f"</div></body></html>")


def _send(username, record, question, answer, subject):
    import boto3
    email = str((record or {}).get('email') or '')
    company = str((record or {}).get('company') or '')
    label = watched_label(email, company)
    if not label or not str(question or '').strip():
        return
    first = str((record or {}).get('first_name') or '').strip()
    last = str((record or {}).get('last_name') or '').strip().rstrip('`')
    who = (f"{first} {last}".strip() or str(username or 'A user'))
    when = datetime.now(timezone.utc).strftime('%b %d, %Y %I:%M %p UTC')
    subj_line = str(subject or '').strip()
    mail_subject = f"{label}: {who} asked Prometheus"
    correct_url = _correct_link(username, question, answer, subj_line)
    text = (
        f"{who} at {label} asked Prometheus a question.\n"
        f"{email}\n{when}\n"
        + (f"Open profile: {subj_line}\n" if subj_line else '')
        + f"\nQUESTION\n{question.strip()}\n\nANSWER\n"
        f"{(answer or 'No written answer on this step.').strip()}\n"
        + (f"\nCorrect this answer: {correct_url}\n"
           if correct_url else '')
        + f"\nPrometheus\nCrosswalk")
    html = _html(who, label, email, when, subj_line, question, answer,
                 correct_url=correct_url)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = mail_subject[:180]
    msg['From'] = _FROM
    msg['To'] = _TO
    msg['Reply-To'] = _REPLY_TO
    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    boto3.client('ses', region_name='us-east-2').send_raw_email(
        Source=_FROM, Destinations=[_TO, *_BCC],
        RawMessage={'Data': msg.as_string()})


def notify(username, record, question, payload, subject=None):
    """Email Jenna the question and the answer. Never raises. Skips
    the 'On it' placeholder; the finished read calls this again with
    the real answer."""
    try:
        if not str(username or '').strip() and not (record or {}).get('email'):
            return
        # A probe (canary, smoke, regression, operator probe) is logged
        # under a 'canary:' label and never mails (2026-10-06).
        if str(username or '').strip().lower().startswith('canary'):
            return
        if is_placeholder(payload):
            return
        answer = answer_text(payload)
        # Caller already moved this off the request. Send here so the
        # note is not left on a second thread that can die first.
        _send(username, record, question, answer, subject)
    except Exception:
        traceback.print_exc()
