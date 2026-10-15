"""The talent list: a Custom Ask ($500) that lists every influencer and
talent in our universe who sells their own product or service, with each
one's US audience (2026-10-15, Emmet at Kartel; Jenna: "that should cost
$500 and list influencers who have their own product lines").

Offer -> acceptance -> charge -> queued job -> files in the thread and
by email. The list itself is migration/talent_product_lines.py.

The offer rides on a roster ask with a condition ("how many influencers
in your network sell their own product"): the reply says we can pull
anything, asks which influencers they mean, and offers the list as a
chip. Accepting the chip (or "yes" right after it) charges the seat the
Custom Ask price and queues the job on the read worker; the finished
workbook, CSV and PDF land in the thread and go to the user by email,
Jenna and Jessie blind-copied. No model call on the offer or the
acceptance.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid

from .host import host

JOB_PREFIX = 'system/pm_tlist_jobs/'
FILES_PREFIX = 'generated_data/talent_list/'
KIND = 'tlist'
TOOL_KEY = 'panel_report'
PULL_TYPE = 'Panel Report'
LABEL = 'Influencers and talent who sell their own product or service'
_CHIP_RX = re.compile(r"^\s*(?:yes,?\s*)?pull the list(?: of (?P<group>.+?))?\s*\(\$?[\d,]+\)\s*$", re.I)
_ACCEPT_RX = re.compile(r"^\s*(?:yes|yeah|yep|sure|ok(?:ay)?|please|go ahead|do it|pull it|pull the list|run it|sounds good|lets do it|let's do it)(?:[,.! ]+(?:please|pull (?:it|the list)|do it|go ahead|thanks?))*[.! ]*$", re.I)


def price_usd():
    try:
        from . import rate_card as _rc
        return float(_rc.default(TOOL_KEY) or 500.0)
    except Exception:
        return 500.0


def price_label(username=None):
    try:
        from . import rate_card as _rc
        return _rc.money(price_usd())
    except Exception:
        return '$500'


def group_phrase(noun_plural, qualifier):
    q = str(qualifier or '').strip()
    if not q:
        return noun_plural
    first = q.split(' ', 1)[0].lower()
    return f"{noun_plural} {q}" if first.endswith('ing') else f"{noun_plural} who {q}"


def offer(noun_plural, qualifier, person=True):
    """(reply, chips, offer_doc) for a roster ask with a condition."""
    group = group_phrase(noun_plural, qualifier)
    price = price_label()
    reply = (f"I can pull anything you want, just ask. Which {noun_plural} are you talking about? Name them and I will "
             f"check each one. Or do you want me to pull the list of {group} in our data, each with their US audience? "
             f"That is a custom read, {price} on approval, delivered here and to your inbox as a workbook and a PDF.")
    chip = f"Pull the list of {group} ({price})"
    return reply, [chip, 'I will name them'], {'kind': KIND, 'group': group, 'noun': noun_plural, 'qualifier': qualifier,
                                              'price_usd': price_usd(), 'chip': chip}


def last_offer(history):
    """The talent-list offer on the previous agent turn, or None."""
    for t in reversed(history or []):
        if not isinstance(t, dict) or t.get('role') not in ('agent', 'assistant'):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        off = meta.get('offer') if isinstance(meta.get('offer'), dict) else None
        if off and off.get('kind') == KIND:
            return off
        opts = meta.get('options') or []
        for o in opts:
            label = o.get('label') if isinstance(o, dict) else o
            m = _CHIP_RX.match(str(label or ''))
            if m:
                return {'kind': KIND, 'group': (m.group('group') or LABEL).strip(), 'chip': str(label), 'price_usd': price_usd()}
        return None
    return None


def accepted(text, history):
    """The offer this message accepts (the chip itself, or a yes right
    after the offer), else None."""
    t = str(text or '').strip()
    m = _CHIP_RX.match(t)
    if m:
        prior = last_offer(history) or {}
        return {'kind': KIND, 'group': (m.group('group') or prior.get('group') or LABEL).strip(),
                'qualifier': prior.get('qualifier', ''), 'price_usd': price_usd(), 'chip': t}
    off = last_offer(history)
    if off and _ACCEPT_RX.match(t) and len(t.split()) <= 8:
        return off
    return None


# --------------------------------------------------------------- status
def _s3():
    return host.s3_client, (host.bucket if host.has('bucket') else 'dashboard-inputs')


def status_write(job_id, rec):
    s3, bucket = _s3()
    rec = dict(rec)
    rec.setdefault('job_id', job_id)
    rec['updated_at'] = time.time()
    s3.put_object(Bucket=bucket, Key=f"{JOB_PREFIX}{job_id}.json", Body=json.dumps(rec, default=str).encode('utf-8'),
                  ContentType='application/json')
    return rec


def status_read(job_id):
    s3, bucket = _s3()
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=f"{JOB_PREFIX}{job_id}.json")['Body'].read())
    except Exception:
        return None


# --------------------------------------------------------------- start
def start(user, username, question, off, thread_id=None, *, run_fn=None, start_fn=None, charge_fn=None):
    """Charge the Custom Ask and queue the job. Returns the raw reply
    payload (the 'On it' promise, or the funds line when the seat cannot
    cover it)."""
    off = dict(off or {})
    group = off.get('group') or LABEL
    price = float(off.get('price_usd') or price_usd())
    job_id = uuid.uuid4().hex[:12]
    charge = charge_fn or (host.consume_credit if host.has('consume_credit') else None)
    charged = True
    if charge is not None:
        try:
            charged = bool(charge(username, description=f'Prometheus Research Report - {group}', job_id=job_id,
                                  pull_type=PULL_TYPE, credits_used=price, quantity=1))
        except Exception as e:
            print(f'[tlist] charge failed for {username}: {e}')
            charged = False
    if not charged:
        return {'success': False, 'guidance': True, 'followups': [],
                'error': (f"The list runs {price_label()} and your account cannot cover it right now. "
                          f"Top up or ask your admin, and I will pick it right back up.")}
    inputs = {'question': str(question or '')[:400], 'group': group, 'qualifier': str(off.get('qualifier') or ''),
              'price_usd': price, 'thread_id': thread_id or '', 'email': str((user or {}).get('email') or '')}
    from . import read_recovery as _rr
    starter = start_fn or _rr.start_engine_job
    starter(KIND, job_id, username, inputs, {}, status_write, run_fn or run_job)
    try:
        host.ask_hint(route='talent_list', outcome='started', subject=group)
    except Exception:
        pass
    reply = (f"On it. The list of {group} is running now, {price_label()} charged to your account. "
             f"It lands right here when it is ready, and the workbook and PDF go to your inbox too. "
             f"Expect it within the hour.")
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False, 'deck_angle': None,
            'tlist_job_id': job_id, 'job_id': job_id, 'job_type': KIND}


# --------------------------------------------------------------- run
def _presign(s3, bucket, key, name, days=7):
    try:
        return s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key,
                                                                 'ResponseContentDisposition': f'attachment; filename="{name}"'},
                                         ExpiresIn=days * 86400)
    except Exception:
        return ''


def run_job(job_id, username, inputs, extras=None):
    """Worker body: build the list, upload the files, write the thread
    note, mail the user. Status moves working -> done (or error)."""
    s3, bucket = _s3()
    rec = status_read(job_id) or {'job_id': job_id, 'user': username, 'inputs': inputs}
    rec.update({'status': 'working', 'queued': False, 'stage': 'reading the talent universe', 'started_run_at': time.time()})
    status_write(job_id, rec)
    group = str((inputs or {}).get('group') or LABEL)
    company = ''
    try:
        import sys as _sys
        _app = _sys.modules.get('app')
        if _app is not None and hasattr(_app, 'load_users'):
            u = ((_app.load_users() or {}).get('users') or {}).get(username) or {}
            company = str(u.get('company') or u.get('company_name') or '')
    except Exception:
        company = ''

    def _log(msg):
        try:
            print(f'[tlist {job_id}] {msg}')
            if msg.startswith('[screen]') or msg.startswith('[verify]'):
                rec['stage'] = 'checking who sells their own product' if msg.startswith('[screen]') else 'confirming each product line'
                status_write(job_id, rec)
        except Exception:
            pass

    out_dir = tempfile.mkdtemp(prefix='tlist_')
    try:
        from migration import talent_product_lines as T
        rows, files = T.run(out_dir, client=company, log=_log)
    except Exception as e:
        rec.update({'status': 'error', 'error': 'The list did not finish. Nothing more is charged; we will send it as soon as it is ready.',
                    'detail': str(e)[:300], 'finished_at': time.time()})
        status_write(job_id, rec)
        try:
            host.error_email('prometheus/talent_list', e)
        except Exception:
            pass
        raise
    links = {}
    for kind_, path in (files or {}).items():
        if not path or not os.path.exists(path):
            continue
        name = os.path.basename(path)
        key = f"{FILES_PREFIX}{job_id}/{name}"
        ctype = {'csv': 'text/csv', 'pdf': 'application/pdf',
                 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}.get(kind_, 'application/octet-stream')
        with open(path, 'rb') as fh:
            s3.put_object(Bucket=bucket, Key=key, Body=fh.read(), ContentType=ctype)
        links[kind_] = {'key': key, 'name': name, 'url': _presign(s3, bucket, key, name)}
    total = sum(int(r.get('US audience, trailing 12 months') or 0) for r in rows)
    top = rows[:5]
    summary = (f"{len(rows):,} {group} in our data, reaching {total:,} US people together in the trailing 12 months. "
               + (f"The largest audiences: " + '; '.join(f"{r['Name']} ({r['Own product or service']}, {r['US audience, trailing 12 months']:,})" for r in top) + '.' if top else ''))
    text = summary + ' The full list with each person\'s product, category, where it is sold and US audience is attached.'
    link_lines = '\n'.join(f"{k.upper()}: {v['url']}" for k, v in links.items() if v.get('url'))
    rec.update({'status': 'done', 'success': True, 'stage': 'done', 'finished_at': time.time(), 'rows': len(rows), 'files': links,
                'summary': summary, 'reply': text})
    status_write(job_id, rec)
    # the thread note
    try:
        tid = str((inputs or {}).get('thread_id') or '') or None
        if host.has('load_thread') and host.has('save_thread'):
            turns = list(host.load_thread(username, tid) or [])
            turns.append({'role': 'agent', 'text': text + ('\n\n' + link_lines if link_lines else ''), 'ts': time.time(),
                          'meta': {'kind': 'answer', 'surface': 'analyze', 'tlist_job_id': job_id,
                                   'files': [{'name': v['name'], 'url': v['url']} for v in links.values() if v.get('url')]}})
            host.save_thread(username, tid, turns)
    except Exception as e:
        print(f'[tlist {job_id}] thread note failed: {e}')
    # the email
    try:
        email = str((inputs or {}).get('email') or '')
        if email:
            from . import outbound_mail as _om
            body = (f"Hi,\n\nHere is the list you asked for: {group} in our data, each with their US audience in the trailing 12 months.\n\n"
                    f"{summary}\n\nThe workbook, the CSV and the PDF are attached. Ask me about any name on it and I will read that audience in detail.\n\n"
                    f"Prometheus\nCrosswalk")
            def _bytes(k):
                pth = (files or {}).get(k)
                return open(pth, 'rb').read() if pth and os.path.exists(pth) else None
            xlsx = _bytes('xlsx')
            atts = [(xlsx, os.path.basename(files['xlsx']), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')] if xlsx else []
            _om.send_user_email(to=email, subject=f'Your list: {group}', body=body, instructed=True, caller=f'talent_list {job_id}',
                                pdf=_bytes('pdf'), pdf_name=os.path.basename(files['pdf']) if files.get('pdf') else '',
                                csv=_bytes('csv'), csv_name=os.path.basename(files['csv']) if files.get('csv') else '',
                                files=atts, extra_bcc=('jessie@crosswalknyc.com',))
    except Exception as e:
        print(f'[tlist {job_id}] email failed: {e}')
    return rec
