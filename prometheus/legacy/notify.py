"""Prometheus notify family (moved out of chat.py 2026-10-09).

The email opt-in store for long jobs, the finished-output email (a read
with its PDF, a deck with the file attached), the completion flush, and
the view-deck digest. Reaches the app through ``_H`` and the legacy
chat module through ``_C``; see threads.py for the pattern.
"""
import json
import re
import threading
import time
import traceback

from prometheus.legacy import H as _H, C as _C  # noqa: E402

__all__ = ['_PM_NOTIFY_PREFIX', '_pm_clean_notify_email', '_pm_notify_write', '_pm_notify_read',
           '_pm_notify_delete', '_pm_send_output_email', '_pm_flush_notify', '_pm_user_email',
           '_pm_view_deck_digest']

_PM_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


_PM_NOTIFY_PREFIX = 'system/prometheus_notify/'


def _pm_clean_notify_email(raw):
    """First syntactically valid address from a raw string, or ''."""
    for part in re.split(r'[,;\n]+', str(raw or '')):
        addr = part.strip()
        if addr and _PM_EMAIL_RE.match(addr) and len(addr) <= 254:
            return addr
    return ''


def _pm_notify_write(job_id, payload):
    """Persist a requester's email opt-in for one background job."""
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_PM_NOTIFY_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


def _pm_notify_read(job_id):
    """Read the email opt-in for a job, or None when none was set."""
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_PM_NOTIFY_PREFIX}{job_id}.json")
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _pm_notify_delete(job_id):
    try:
        _H.s3_client.delete_object(
            Bucket=_H.S3_BUCKET, Key=f"{_PM_NOTIFY_PREFIX}{job_id}.json")
    except Exception:
        pass


def _pm_send_output_email(kind, to_email, data, sync=False):
    """Email the finished OUTPUT of a long task to the requester.

    Owned first-party voice, no internal vocabulary. `kind` is 'read'
    or 'deck': a read carries the read itself in the body plus a
    branded PDF of the same words the recipient can take and share
    (Jenna 2026-09-30); a deck carries its title and a download link.
    Sent From Prometheus with Jenna BCC'd, Reply-To Jenna, per the
    standing send rules. The send runs on a daemon thread; never
    raises, and a PDF render failure ships the email without the
    attachment."""
    import html as _html
    to_email = _pm_clean_notify_email(to_email)
    if not to_email:
        return False
    kind = 'deck' if str(kind) == 'deck' else 'read'
    pdf_bytes, pdf_name = b'', ''
    csv_bytes, csv_name = b'', ''
    if kind == 'deck':
        title = str((data or {}).get('title')
                    or (data or {}).get('filename') or 'Your deck')[:200]
        slides = (data or {}).get('slides')
        url = str((data or {}).get('url') or '')
        slide_note = (f" ({slides} slides)"
                      if isinstance(slides, int) and slides else '')
        subject_line = f"{title} is ready"
        body_text = (
            f"{title}{slide_note} is ready.\n\n"
            + "The deck is attached as a PowerPoint file.\n\n"
            + (f"Download the deck: {url}\n\n" if url else "")
            + "The link is good for 7 days. It is also waiting in the "
              "chat on your dashboard.\n\nPrometheus\nCrosswalk\n")
        # Light design (Jenna 2026-09-30: "I prefer this design for
        # emails moving forward"). Legacy shell only on render failure.
        body_html = ''
        try:
            import prometheus_email_html as _peh
            body_html = _peh.render_answer_email_html(
                title,
                f"{title}{slide_note} is ready.\n\n"
                "The link is good for 7 days. It is also waiting in "
                "the chat on your dashboard.\n\nPrometheus\nCrosswalk",
                cta_url=(url if url.lower().startswith('https://')
                         else None),
                cta_text='Download the deck')
        except Exception:
            body_html = ''
        if not body_html:
            link_html = ''
            if url.lower().startswith('https://'):
                link_html = (
                    f'<p><a href="{_html.escape(url)}" '
                    'style="display:inline-block;background:#66d9ef;'
                    'color:#0a1929;padding:12px 24px;border-radius:6px;'
                    'text-decoration:none;font-weight:bold;'
                    'margin-top:8px;">Download the deck</a></p>')
            body_html = _H._wrap_email_html(
                f"<p>{_html.escape(title)}{slide_note} is ready.</p>"
                f"{link_html}"
                "<p>The link is good for 7 days. It is also waiting in "
                "the chat on your dashboard.</p>"
                "<p>Prometheus<br>Crosswalk</p>",
                title="Your deck is ready")
    else:
        reply = str((data or {}).get('reply') or '').strip()
        if not reply:
            return False
        subject_line = "Your read is ready"
        _subj = str((data or {}).get('profile') or '').strip()
        email_title = _subj or 'Your Crosswalk read'
        # The same words as a branded, shareable PDF (Jenna
        # 2026-09-30: "attach pdfs of the prometheus emails of what
        # the email body says"). Fail-safe: b'' means no attachment.
        try:
            import prometheus_email_pdf as _pep
            pdf_bytes = _pep.render_answer_pdf(
                email_title,
                reply + '\n\nPrometheus\nCrosswalk')
            if pdf_bytes:
                _safe = re.sub(r'[^A-Za-z0-9]+', '_', _subj).strip('_')
                pdf_name = ((_safe + '_Read.pdf') if _safe
                            else 'Crosswalk_Read.pdf')
        except Exception:
            pdf_bytes, pdf_name = b'', ''
        # The raw data rides along as a CSV (Jenna 2026-10-01: the
        # read email "should also always have a .csv file"). Built
        # from the SAME ledger entry the reply shipped from - the
        # exact file the download chip would produce - so the attached
        # numbers match the chat numbers exactly. Fail-safe: no banked
        # entry or a build failure ships the email without the file.
        try:
            _q = str((data or {}).get('question') or '').strip()
            _entry = None
            if _q:
                import insights_ledger as _il
                import prometheus_analysis as _pma
                _led = _il.consult(subject=_subj or None, question=_q)
                _entry = (_led or {}).get('exact')
                if _entry is None:
                    _entry = (_il.consult(question=_q)
                              or {}).get('exact')
            if _entry and (_entry.get('breakdown')
                           or _entry.get('metrics')):
                _cf, _ct = _pma.build_generated_csv(_entry)
                _rng = ''
                try:
                    if _entry.get('ws') and _entry.get('we'):
                        _rng = (f"{_H._fmt_study_date(_entry['ws'])} - "
                                f"{_H._fmt_study_date(_entry['we'])}")
                    elif _entry.get('wl'):
                        _rng = str(_entry['wl'])
                except Exception:
                    _rng = ''
                _ct = _H._stamp_csv_text(_ct, _rng)
                csv_name = _C._pm_csv_task_filename(_entry) or _cf
                csv_bytes = _ct.encode('utf-8')
        except Exception:
            csv_bytes, csv_name = b'', ''
        if pdf_bytes and csv_bytes:
            _attach_note = ("The read is attached as a PDF you can "
                            "share, and the data behind it is attached "
                            "as a CSV. You can also pick this up in "
                            "the chat on your dashboard.")
        elif csv_bytes:
            _attach_note = ("The data behind this read is attached as "
                            "a CSV. You can also pick this up in the "
                            "chat on your dashboard.")
        else:
            _attach_note = ("The same read is attached as a PDF you "
                            "can share. You can also pick this up in "
                            "the chat on your dashboard.")
        mail_body = (
            f"{reply}\n\n"
            f"{_attach_note}\n\nPrometheus\nCrosswalk")
        body_text = mail_body + "\n"
        # Light design (Jenna 2026-09-30: "I prefer this design for
        # emails moving forward"). Legacy shell only on render failure.
        body_html = ''
        try:
            import prometheus_email_html as _peh
            body_html = _peh.render_answer_email_html(email_title,
                                                      mail_body)
        except Exception:
            body_html = ''
        if not body_html:
            reply_html = _html.escape(reply).replace('\n', '<br>')
            body_html = _H._wrap_email_html(
                f"<p>{reply_html}</p>"
                f"<p>{_html.escape(_attach_note)}</p>"
                "<p>Prometheus<br>Crosswalk</p>",
                title="Your read is ready")

    def _send():
        try:
            from email.mime.application import MIMEApplication as _MApp
            from email.mime.multipart import MIMEMultipart as _MMul
            from email.mime.text import MIMEText as _MTxt
            msg = _MMul('mixed')
            msg['Subject'] = subject_line[:200]
            msg['From'] = 'Prometheus <prometheus@crosswalknyc.com>'
            msg['To'] = to_email
            msg['Reply-To'] = 'jenna@crosswalknyc.com'
            alt = _MMul('alternative')
            alt.attach(_MTxt(body_text, 'plain', 'utf-8'))
            alt.attach(_MTxt(body_html, 'html', 'utf-8'))
            msg.attach(alt)
            if pdf_bytes and pdf_name:
                att = _MApp(pdf_bytes, _subtype='pdf')
                att.add_header('Content-Disposition', 'attachment',
                               filename=pdf_name)
                msg.attach(att)
            if csv_bytes and csv_name:
                attc = _MApp(csv_bytes, _subtype='csv')
                attc.add_header('Content-Disposition', 'attachment',
                                filename=csv_name)
                msg.attach(attc)
            # One door for user-facing mail (2026-10-06): the user asked
            # for this notification, so it is instructed by them.
            from prometheus import outbound_mail as _om
            from prometheus import charts as _charts
            _imgs = _charts.attachments_for(data, _H.s3_client, _H.S3_BUCKET)
            _files = []
            if kind == 'deck' and (data or {}).get('s3_key'):
                try:   # the finished deck itself rides along (2026-10-09, Liz)
                    _blob = _H.s3_client.get_object(Bucket=_H.S3_BUCKET, Key=data['s3_key'])['Body'].read()
                    _files.append((_blob, str(data.get('filename') or 'deck.pptx'),
                                   'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
                except Exception:
                    traceback.print_exc()
            _om.send_user_email(
                to=to_email, subject=subject_line[:200], body=body_text,
                instructed=True, caller=f'pm-notify:{kind}', html=body_html,
                pdf=pdf_bytes or None, pdf_name=pdf_name,
                csv=csv_bytes or None, csv_name=csv_name, bcc_liz=False, images=_imgs, files=_files)
        except Exception as e:
            print(f"[pm-notify] send failed: {e}")

    if sync:   # a background job sends inline so the mail never dies with the thread (2026-10-09)
        _send()
        return True
    threading.Thread(target=_send, daemon=True).start()
    return True


def _pm_flush_notify(job_id, kind, data):
    """On successful completion: if the requester opted in, email them
    the output, then clear the opt-in. Returns True when a mail went
    out; False when none was set."""
    sent = False
    try:
        opt = _pm_notify_read(job_id)
        if opt and opt.get('email'):
            sent = bool(_pm_send_output_email(kind, opt.get('email'), data))
    except Exception:
        traceback.print_exc()
    finally:
        _pm_notify_delete(job_id)
    return sent


def _pm_user_email(username):
    """The seat's email from the user record, '' when none."""
    try:
        u = (_H.load_users().get('users') or {}).get(str(username or '')) or {}
        return str(u.get('email') or '').strip()
    except Exception:
        return ''


def _pm_view_deck_digest(view_context, history, subject=''):
    """Deck digest for a dashboard view (2026-10-09): the on-screen
    summary rendered the way the analysis prompt sees it, plus every
    read Prometheus already delivered in this thread, so the deck
    restates what the reader was shown and nothing else. Returns
    (digest_text, meta)."""
    import prometheus_analysis as pma
    parts = [pma.render_view_context_block(view_context)]
    turns = [str(h.get('text') or '') for h in (history or []) if isinstance(h, dict)
             and str(h.get('role') or '').lower() in ('agent', 'assistant') and str(h.get('text') or '').strip()]
    if turns:
        parts.append('READS ALREADY DELIVERED IN THIS CONVERSATION (the deck restates these; every number stays as given)\n'
                     '===================================================\n' + '\n\n'.join(t[:4000] for t in turns[-6:]))
    title = str((view_context or {}).get('view_title') or '').strip()
    name = str(subject or '').strip() or pma.resolve_subject({}, view_context) or title or 'this view'
    return '\n\n'.join(p for p in parts if p), {'name': name, 'brand_category': '', 'view_title': title}
