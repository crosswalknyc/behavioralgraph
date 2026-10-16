"""Status-aware answers (Jenna 2026-10-13, after Carolyn: "I don't see
No Reservations - only Babylon 5" drew "Nothing came back for that one.
Try asking it another way." while her journey was ten minutes from
landing, and "have not received an email yet" drew a page analysis).

Any ask that says the reader cannot see, find, or has not received what
they asked for is answered from what Prometheus knows about THEIR work:
running and finished jobs across every product (journeys, Brand
Partnership, Attribution, Flywheel, decks, reads), profile builds on the
engine, and the delivery records (what was emailed, when, where it
sits). No model call, never a read, never a clarify.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from datetime import datetime, timezone

_STATUS_RX = re.compile(
    r"(\b(?:i\s+)?(?:don'?t|do\s+not|can'?t|cannot|still\s+(?:don'?t|can'?t))\s+(?:see|find|locate|get)\b"
    r"|\bnot\s+(?:showing|there|visible|appearing|on\s+the\s+(?:site|tab|dashboard|page))\b"
    r"|\bonly\s+(?:see|shows?|showing|have)\b"
    r"|\b(?:have\s+not|haven'?t|hasn'?t|has\s+not|did\s+not|didn'?t|never)\s+(?:received|gotten|got|arrived|landed|shown\s+up|come\s+through|seen)\b"
    r"|\bno\s+email\b|\bnot\s+received\b|\bnothing\s+(?:arrived|landed|came|showed|in\s+my\s+(?:inbox|email))\b"
    r"|\bwhere(?:'s|\s+is|\s+are|\s+did)\s+(?:it|that|my|the|they)\b"
    r"|\b(?:is|are)\s+(?:it|that|this|they|my\s+[\w ]{1,30})\s+(?:done|ready|finished|complete[d]?|live|up)\b"
    r"|\bstill\s+(?:waiting|running|building|processing|not\s+(?:there|here|done|ready))\b"
    r"|\bwhat\s+happened\s+to\b|\bdid\s+(?:it|that|this|the\s+\w+)\s+(?:finish|run|land|go\s+through|work)\b"
    r"|\bhow\s+do\s+i\s+(?:find|see|get\s+to|open)\b|\bwhere\s+(?:do|can|would)\s+i\s+(?:find|see|get)\b"
    r"|\bis\s+it\s+(?:there|in\s+the\s+tab|on\s+the\s+site)\b)", re.I)
_NOT_STATUS_RX = re.compile(r"\b(fall[- ]?off|drop[- ]?off|index(?:es)?|penetration|overlap|share\s+of|what\s+share|how\s+many|percent|%)\b", re.I)
KIND_LABEL = {'jiq': 'Digital Journey', 'bpiq': 'Brand Partnership IQ read', 'aiq': 'Attribution IQ campaign',
              'fw': 'Flywheel read', 'deck': 'deck', 'read': 'read', 'profile': 'profile', 'subiq': 'Subscriber IQ tracker',
              'journey': 'Digital Journey', 'attribution': 'Attribution IQ campaign', 'flywheel': 'Flywheel read',
              'package': 'data package'}
TYPICAL_MIN = {'jiq': 25, 'bpiq': 12, 'aiq': 20, 'fw': 15, 'deck': 6, 'read': 3, 'profile': 45}
_STALE_MIN = 60
_SWEEP_KINDS = ('jiq', 'bpiq', 'aiq', 'fw')


def _kick_sweep(host):
    """Run the stale-job sweeper once on a daemon thread; never blocks the answer."""
    try:
        import threading
        from . import jobs_sweeper as _js
        threading.Thread(target=_js.sweep, args=(host,), daemon=True).start()
    except Exception:
        traceback.print_exc()


def wants_status(text):
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 320:
        return False
    if not _STATUS_RX.search(t):
        return False
    if _NOT_STATUS_RX.search(t) and not re.search(r"\b(email|received|see|find|tab|site|dashboard)\b", t, re.I):
        return False
    return True


def _ago(ts):
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        if mins < 1:
            return 'just now'
        if mins < 60:
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        hrs = mins // 60
        if hrs < 36:
            return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
        return f"{hrs // 24} days ago"
    except Exception:
        return ''


def _minutes_since(ts):
    try:
        if isinstance(ts, (int, float)):
            return (time.time() - float(ts)) / 60.0
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


_DOC_CACHE = {}       # key -> (etag, doc); a status ask re-reads only what changed
_DOC_CACHE_MAX = 3000


def _doc(s3, bucket, obj):
    key = obj.get('Key')
    etag = str(obj.get('ETag') or '') or (str(obj.get('LastModified')) if obj.get('LastModified') else '')
    hit = _DOC_CACHE.get(key) if etag else None
    if hit and hit[0] == etag:
        return hit[1]
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    if etag:                      # no version marker (a fake store): never cache
        if len(_DOC_CACHE) >= _DOC_CACHE_MAX:
            _DOC_CACHE.clear()
        _DOC_CACHE[key] = (etag, doc)
    return doc


def _job_items(host, uname, days=7):
    """The caller's jobs across every job store, newest first."""
    items = []
    prefixes = []
    for cap, jtype in (('read_prefix', 'read'), ('deck_prefix', 'deck'), ('bpiq_prefix', 'bpiq'), ('jiq_prefix', 'jiq'),
                       ('fw_prefix', 'fw'), ('aiq_prefix', 'aiq')):
        try:
            if host.has(cap):
                prefixes.append((getattr(host, cap), jtype))
        except Exception:
            continue
    s3 = getattr(host, 's3_client', None)
    bucket = getattr(host, 'bucket', 'dashboard-inputs')
    if s3 is None:
        return items
    cutoff = time.time() - days * 86400
    for prefix, jtype in prefixes:
        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            objs = [o for o in (resp.get('Contents') or []) if o.get('Key', '').endswith('.json')]
            objs.sort(key=lambda o: o.get('LastModified') or 0, reverse=True)
            for o in objs[:120]:
                lm = o.get('LastModified')
                try:
                    if lm is not None and lm.timestamp() < cutoff:
                        continue
                except Exception:
                    pass
                doc = _doc(s3, bucket, o)
                if doc is None:
                    continue
                if str(doc.get('user') or '').lower() != str(uname or '').lower():
                    continue
                if str(doc.get('final_reason') or '') == 'requester not found':
                    continue        # nobody to report it to (a probe identity with no seat)
                items.append({'kind': jtype, 'status': str(doc.get('status') or 'running').lower(),
                              'subject': doc.get('subject') or doc.get('project_name') or doc.get('title')
                              or (('"' + str(doc.get('question'))[:80].rstrip() + '"') if doc.get('question') else ''),
                              'started': doc.get('started_at') or doc.get('created_at'),
                              'finished': doc.get('finished_at'), 'key': doc.get('s3_key') or doc.get('slug') or '',
                              'ready_message': doc.get('ready_message') or '', 'job_id': doc.get('job_id')})
        except Exception:
            traceback.print_exc()
    items.sort(key=lambda i: str(i.get('finished') or i.get('started') or ''), reverse=True)
    return items


# Terminal-failure lines the engine writes FOR the client (the partner
# API whitelist mirrors this): the refund copy, and StreamScout's
# "We can't see <title> on <platform> at this time" when the client named
# a platform the title is not on. Anything else stays generic.
_CLIENT_FAILURE_PREFIXES = ("this build could not be completed", "we can't see ", "we cant see ")


def client_failure_line(error):
    """The engine's deliberate client-facing failure line, or '' when the
    recorded error is internal and must not be relayed."""
    e = ' '.join(str(error or '').split())
    if e and e.lower().startswith(_CLIENT_FAILURE_PREFIXES) and len(e) <= 400:
        return e
    return ''


def _engine_runs(host, user):
    """Profile builds on the engine (the listener), via the host when bound."""
    try:
        if host.has('user_runs'):
            docs = host.user_runs(user, limit=12) or []
            out = []
            for d in docs:
                if not isinstance(d, dict):
                    continue
                st = str(d.get('status') or '').lower()
                out.append({'kind': 'profile', 'status': 'done' if st in ('complete', 'completed', 'done') else ('error' if st in ('error', 'failed') else 'running'),
                            'subject': d.get('subject') or d.get('profile_name') or '', 'started': d.get('queued_at') or d.get('started_at'),
                            'finished': d.get('completed_at'), 'key': d.get('tu_s3_key') or d.get('s3_key') or '', 'job_id': d.get('run_id'),
                            'client_line': client_failure_line(d.get('error'))})
            return out
    except Exception:
        traceback.print_exc()
    return []


def _deliveries(host, uname):
    try:
        from . import delivered as _dl
        s3 = getattr(host, 's3_client', None)
        if s3 is None:
            return []
        return _dl.list_records(s3, getattr(host, 'bucket', 'dashboard-inputs'), uname, limit=20)
    except Exception:
        traceback.print_exc()
        return []


def _mentioned(text, items):
    """Items whose subject the ask names, else all items."""
    low = ' '.join(str(text or '').lower().split())
    hits = []
    for it in items:
        subj = str(it.get('subject') or '')
        toks = [t for t in re.findall(r"[a-z0-9]+", subj.lower()) if len(t) >= 5 and t not in ('prime', 'video', 'netflix', 'peacock', 'viewers', 'series', 'season')]
        if toks and any(t in low for t in toks):
            hits.append(it)
    return hits or items


def answer(text, uname, *, host, user=None, history=None):
    """A status answer dict for the chat, or None when the reader has no
    work on record (the ask then routes normally)."""
    try:
        items = _job_items(host, uname) + _engine_runs(host, user or {'username': uname})
        deliveries = _deliveries(host, uname)
    except Exception:
        traceback.print_exc()
        items, deliveries = [], []
    if not items and not deliveries:
        # Nothing on record is still a status answer, never a build draft
        # (the replay of Carolyn's "I don't see No Reservations" drafted a
        # profile of "No Reservations" when the record was empty).
        return {'success': True, 'action': 'answer',
                'reply': ('I do not have anything running or delivered for you in the last week. If you asked for '
                          'something and it has not landed, tell me what it was and I will run it now; if you are '
                          'looking for something built earlier, name it and I will find it.'),
                'followups': [], 'offer_deck': False, 'deck_angle': None, 'status_items': []}
    by_key = {}
    for d in deliveries:
        k = str(d.get('key') or '')
        if k:
            by_key.setdefault(k, d)
    picked = _mentioned(text, items)[:3] if items else []
    lines = []
    for it in picked:
        label = KIND_LABEL.get(it['kind'], it['kind'])
        subj = str(it.get('subject') or 'your request')
        st = it.get('status')
        if st in ('done', 'complete', 'completed', 'ready'):
            d = by_key.get(str(it.get('key') or ''))
            when = _ago(it.get('finished')) if it.get('finished') else ''
            line = f"Your {label} for {subj} finished{(' ' + when) if when else ''}."
            if d:
                line += f" {d.get('where') or ''}".rstrip()
                em = d.get('email') or {}
                if em.get('sent'):
                    line += f" The email with the files went to {em.get('to') or 'you'}{(' ' + _ago(d.get('recorded_at'))) if d.get('recorded_at') else ''}."
                elif em.get('reason') == 'no email on seat':
                    line += " I could not email it because your seat has no email address on file; ask your admin to add one."
            else:
                where = {'jiq': 'the Digital Journey IQ tab', 'bpiq': 'the Brand Partnership IQ tab', 'aiq': 'the Attribution IQ tab',
                         'fw': 'the Flywheel tab', 'profile': 'Profile IQ, Select Profile', 'deck': 'your email', 'read': 'this chat'}.get(it['kind'], 'the dashboard')
                line += f" It is in {where}; if the tab was open before it landed, refresh once and it will be there."
            lines.append(line)
        elif st in ('error', 'failed'):
            if it.get('client_line'):
                # the engine wrote this line for the client (a platform the
                # title is not on, 2026-10-16); relay it as written
                lines.append(f"Your {label} for {subj}: {it['client_line']}")
            else:
                lines.append(f"Your {label} for {subj} did not complete. The charge comes back to your balance automatically; tell me to run it again and I will.")
        elif st == 'requeued':
            lines.append(f"Your {label} for {subj} did not finish in its first hour, so I restarted it. It lands in the dashboard and in your email when it is done.")
        else:
            mins = _minutes_since(it.get('started'))
            typical = TYPICAL_MIN.get(it['kind'], 20)
            if mins is not None and mins >= _STALE_MIN and it['kind'] in _SWEEP_KINDS:
                # A job past the hour is the sweeper's (2026-10-13 audit):
                # restart it now instead of saying "still going" all day.
                _kick_sweep(host)
                lines.append(f"Your {label} for {subj} has run past the hour it usually takes. I am restarting it now; it lands in the dashboard and in your email when it is done.")
            elif mins is not None and mins >= 0:
                left = max(2, int(typical - mins)) if mins < typical else None
                eta = (f"usually about {left} more minutes" if left else "it is running longer than usual; it is still going")
                lines.append(f"Your {label} for {subj} is still running (started {int(mins)} minutes ago, {eta}). I will post here and email you the files the moment it lands.")
            else:
                lines.append(f"Your {label} for {subj} is still running. I will post here and email you the files the moment it lands.")
    if not picked and deliveries:
        d = deliveries[0]
        em = d.get('email') or {}
        lines.append(f"The last thing I delivered to you was the {KIND_LABEL.get(d.get('kind'), d.get('kind'))} for {d.get('subject') or 'your request'} "
                     f"{_ago(d.get('recorded_at'))}. {d.get('where') or ''}".strip()
                     + (f" The email went to {em.get('to')}." if em.get('sent') else ''))
    if not lines:
        return None
    reply = '\n\n'.join(dict.fromkeys(lines))      # three failed copies of one read say it once
    followups = []
    if any(it.get('status') not in ('done', 'complete', 'completed', 'ready', 'error', 'failed') for it in picked):
        followups.append('Email me when it lands')
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups, 'offer_deck': False, 'deck_angle': None,
            'status_items': [{'kind': i['kind'], 'subject': i['subject'], 'status': i['status']} for i in picked]}
