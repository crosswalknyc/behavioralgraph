"""The caller's recent reads by question (2026-10-26).

One question, one read, one answer. The per-user doc at
``system/prometheus_reads/_inflight/<user>.json`` already recorded the
running job under the question's key so a re-send attached to it; it
pruned itself at fifteen minutes, so once the read had landed nothing
remembered it, and the next copy of the question (a minute later in the
same thread, or in the next thread the reader opened) started a new
read with new numbers. The doc now keeps a day of entries, keyed by the
shared fold in ``question_key``:

- ``inflight(...)``: the caller's RUNNING job for this question, under
  fifteen minutes old (the attach-instead-of-second-copy rule).
- ``recent(...)``: the caller's LANDED read for this question inside a
  day, with the reply that landed (resumed chains followed), so every
  later surface (the repeat guard, the thread lane, the status lane)
  hands back the same words and never a fresh draw.
- ``mark(...)``: record a job under the question.

Everything is fail-safe: a store error reads as "no record".
"""
from __future__ import annotations

import json
import time
import traceback

from . import question_key as _qk

PREFIX = 'system/prometheus_reads/_inflight/'
KEEP_S = 24 * 3600          # entries live a day (the same-answer window)
RUNNING_S = 900             # a running job attaches for fifteen minutes


def doc_key(user):
    import re
    safe = re.sub(r'[^a-z0-9_.-]+', '_', str(user or 'anon').strip().lower()) or 'anon'
    return f"{PREFIX}{safe}.json"


def _load(s3, bucket, user):
    try:
        resp = s3.get_object(Bucket=bucket, Key=doc_key(user))
        doc = json.loads(resp['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _status(s3, bucket, read_prefix, job_id):
    try:
        st = s3.get_object(Bucket=bucket, Key=f"{read_prefix}{job_id}.json")
        doc = json.loads(st['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _followed(s3, bucket, read_prefix, status):
    """The terminal record of a resumed chain (up to three hops), the
    first job id kept on it."""
    try:
        cur, hops, first = status, 0, str(status.get('job_id') or '')
        while str(cur.get('status') or '') == 'working' and cur.get('resumed_as') \
                and cur.get('resumed_as') != cur.get('job_id') and hops < 3:
            nxt = _status(s3, bucket, read_prefix, cur['resumed_as'])
            if not nxt:
                break
            cur = {**nxt, 'job_id': first or nxt.get('job_id'), 'resumed_from': cur.get('job_id')}
            hops += 1
        return cur
    except Exception:
        return status


def mark(s3, bucket, user, text, job_id, subject='', now=None):
    """Record the running job under the caller's question key. Never raises."""
    try:
        now = now or time.time()
        doc = _load(s3, bucket, user)
        doc = {k: v for k, v in doc.items()
               if isinstance(v, dict) and now - float(v.get('started_at') or 0) <= KEEP_S}
        doc[_qk.key(text)] = {'job_id': str(job_id), 'started_at': now, 'question': str(text or '')[:200],
                              'subject': str(subject or '')[:120]}
        s3.put_object(Bucket=bucket, Key=doc_key(user), Body=json.dumps(doc).encode('utf-8'),
                      ContentType='application/json')
    except Exception:
        traceback.print_exc()


def _entry(s3, bucket, user, text):
    doc = _load(s3, bucket, user)
    ent = doc.get(_qk.key(text))
    return ent if isinstance(ent, dict) and ent.get('job_id') else None


def inflight(s3, bucket, read_prefix, user, text, now=None):
    """{'job_id', 'stage'} for the caller's own RUNNING job on this question, or None."""
    try:
        ent = _entry(s3, bucket, user, text)
        if not ent:
            return None
        now = now or time.time()
        if now - float(ent.get('started_at') or 0) > RUNNING_S:
            return None
        status = _status(s3, bucket, read_prefix, ent['job_id'])
        if not status or str(status.get('status') or '') != 'working':
            return None
        if status.get('claimed_by') and not status.get('resumed_as'):
            return None   # being resumed right now: its own launch must run
        return {'job_id': str(ent['job_id']), 'stage': str(status.get('stage') or '').strip()}
    except Exception:
        return None


def recent(s3, bucket, read_prefix, user, text, now=None):
    """The caller's read on this question inside the day: {'status': 'done',
    'job_id', 'reply', 'followups', 'finished_at', 'question'} when it
    landed; {'status': 'working', 'job_id', 'stage'} while it runs; None
    when there is no record or it did not land."""
    try:
        ent = _entry(s3, bucket, user, text)
        if not ent:
            return None
        now = now or time.time()
        if now - float(ent.get('started_at') or 0) > KEEP_S:
            return None
        status = _status(s3, bucket, read_prefix, ent['job_id'])
        if not status:
            return None
        status = _followed(s3, bucket, read_prefix, status)
        st = str(status.get('status') or '')
        payload = status.get('payload') if isinstance(status.get('payload'), dict) else {}
        reply = str(payload.get('reply') or '').strip()
        subject = str(ent.get('subject') or status.get('subject') or '')
        if st == 'done' and reply:
            return {'status': 'done', 'job_id': str(ent['job_id']), 'reply': reply, 'subject': subject,
                    'followups': [f for f in (payload.get('followups') or []) if isinstance(f, str)][:6],
                    'finished_at': status.get('finished_at'), 'question': str(ent.get('question') or '')}
        if st == 'working' and now - float(ent.get('started_at') or 0) <= RUNNING_S:
            return {'status': 'working', 'job_id': str(ent['job_id']), 'subject': subject,
                    'stage': str(status.get('stage') or '').strip()}
        return None
    except Exception:
        return None


def for_subject(hit, bind_subject):
    """The hit, unless the caller bound a different subject this time
    (the which-audience chip picked another audience): then None."""
    if not hit or not bind_subject or not hit.get('subject'):
        return hit
    return hit if _qk.fold(bind_subject) == _qk.fold(hit['subject']) else None


def when_text(finished_at):
    """'Oct 21 at 09:21 UTC' from an epoch or ISO stamp, or ''."""
    try:
        import datetime as _dt
        if isinstance(finished_at, (int, float)) or str(finished_at or '').replace('.', '', 1).isdigit():
            d = _dt.datetime.fromtimestamp(float(finished_at), tz=_dt.timezone.utc)
        else:
            d = _dt.datetime.fromisoformat(str(finished_at).replace('Z', '+00:00'))
        return d.strftime('%b %-d at %H:%M UTC')
    except Exception:
        return ''


def replay_payload(hit):
    """The analyze payload that hands a landed read back verbatim."""
    when = when_text(hit.get('finished_at'))
    lead = f"Same read as earlier{(' (' + when + ')') if when else ''}:"
    return {'success': True, 'action': 'answer', 'reply': lead + '\n\n' + str(hit.get('reply') or ''),
            'followups': list(hit.get('followups') or [])[:3], 'offer_deck': False, 'deck_angle': None,
            'read_job_id': hit.get('job_id'), 'replayed_read': True}


def inflight_payload(hit):
    stage = str(hit.get('stage') or 'working through the data')
    return {'success': True, 'action': 'answer', 'read_job_id': hit.get('job_id'),
            'reply': ('Still on it: that exact read is running now (' + stage + '). It lands right here '
                      'the moment it is ready, and I did not start a second copy.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None}


__all__ = ['PREFIX', 'KEEP_S', 'RUNNING_S', 'doc_key', 'mark', 'inflight', 'recent', 'for_subject',
           'when_text', 'replay_payload', 'inflight_payload']
