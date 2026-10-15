"""Webhooks for key callers: one signed POST when a job reaches a
terminal state (done, held, error), retried three times with backoff.

A marker per job sits under ``system/pm_webhooks/_pending/<job_id>.json``
from the moment a key caller's ask becomes a job. The sweep (run by the
read worker loop on the build server, and callable anywhere) reads each
marker, looks the job up, and when it is terminal delivers the v1
envelope the poll would return, signed with the key's secret::

    X-Crosswalk-Signature: sha256=<hmac_sha256(secret, body)>
    X-Crosswalk-Event: job.done | job.held | job.error
    X-Crosswalk-Delivery: <uuid>

Delivery log on the marker (moved to ``_done/`` or ``_failed/`` after
the last attempt). Never raises into the caller.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from .host import host

__all__ = ['register_for_key', 'register', 'sweep', 'sign', 'PENDING_PREFIX', 'MAX_ATTEMPTS']

PENDING_PREFIX = 'system/pm_webhooks/_pending/'
DONE_PREFIX = 'system/pm_webhooks/_done/'
FAILED_PREFIX = 'system/pm_webhooks/_failed/'
MAX_ATTEMPTS = 3
BACKOFF_S = (0, 60, 300)
TERMINAL = {'done': 'job.done', 'complete': 'job.done', 'completed': 'job.done', 'ready': 'job.done', 'held': 'job.held',
            'error': 'job.error', 'failed': 'job.error', 'timed_out': 'job.error'}


def sign(secret: str, body: bytes) -> str:
    return 'sha256=' + hmac.new(str(secret or '').encode('utf-8'), body, hashlib.sha256).hexdigest()


def _s3():
    return host.s3_client if host.has('s3_client') else None


def register(job_id: str, *, owner: str, url: str, secret: str = '', conversation_id: str = '', key_id: str = '') -> str:
    s3 = _s3()
    if s3 is None or not job_id or not str(url).lower().startswith('https://'):
        return ''
    key = f"{PENDING_PREFIX}{job_id}.json"
    doc = {'job_id': str(job_id), 'owner': owner, 'url': url, 'secret': secret or '', 'conversation_id': conversation_id or '',
           'key_id': key_id or '', 'created_at': time.time(), 'attempts': [], 'next_at': 0}
    s3.put_object(Bucket=host.bucket, Key=key, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
    return key


def register_for_key(user: dict, job_id: str, *, conversation_id: str = '', request_url: str = '') -> str:
    """The key's webhook (or a per-request https URL) for this job."""
    from . import partner_keys as K
    wh = K.webhook(user)
    url = wh.get('url') or (request_url if str(request_url or '').lower().startswith('https://') else '')
    if not url:
        return ''
    from .service import username_of
    return register(job_id, owner=username_of(user), url=url, secret=wh.get('secret') or '', conversation_id=conversation_id, key_id=K.key_id(user))


def _list(prefix: str) -> list:
    s3 = _s3()
    if s3 is None:
        return []
    out, token = [], None
    while True:
        kw = {'Bucket': host.bucket, 'Prefix': prefix, 'MaxKeys': 500}
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw)
        out.extend(o['Key'] for o in resp.get('Contents') or [])
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')
    return out


def _load(key: str):
    s3 = _s3()
    try:
        return json.loads(s3.get_object(Bucket=host.bucket, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _move(key: str, doc: dict, prefix: str) -> None:
    s3 = _s3()
    try:
        s3.put_object(Bucket=host.bucket, Key=prefix + key.rsplit('/', 1)[1], Body=json.dumps(doc, default=str).encode('utf-8'),
                      ContentType='application/json')
        s3.delete_object(Bucket=host.bucket, Key=key)
    except Exception:
        pass


def _payload_for(doc: dict, job_doc: dict, kind: str, event: str) -> dict:
    """The v1 envelope the poll would return for this terminal job."""
    from . import contract_v1 as C, envelope, seams
    from . import guards as _g
    status = str(job_doc.get('status') or '').lower()
    if event == 'job.done':
        try:
            content = seams.unwrap(seams.tag_job(dict(job_doc), kind))
            env = envelope.wrap(content, surface=('deck' if kind == 'deck' else 'analyze'), decision={'reason': 'job_result'}, via='api_key')
            v1 = C.to_v1(env, env.get('_raw'), conversation_id=doc.get('conversation_id') or None)
            if v1.get('kind') == 'job':
                v1 = C.to_v1(dict(env, kind='answer', job=None), env.get('_raw'), conversation_id=doc.get('conversation_id') or None)
        except Exception:
            v1 = C.declined('error', 'The job finished but its result could not be read. Poll the job for the answer.')
    elif event == 'job.held':
        v1 = C.pending('Held for a moment on our side. Poll again shortly.', retry_after=300, job_id=doc.get('job_id'),
                       conversation_id=doc.get('conversation_id') or None)
    else:
        v1 = C.declined('error', 'That one did not finish. Ask again and it will rerun.', conversation_id=doc.get('conversation_id') or None)
    v1['event'] = event
    v1['job_id'] = doc.get('job_id')
    _g.scrub_partner_payload(v1)
    return v1


def _post(url: str, body: bytes, headers: dict, timeout: float = 10.0) -> int:
    import urllib.request
    req = urllib.request.Request(url, data=body, method='POST', headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)


def deliver(doc: dict, payload: dict, *, post=_post) -> tuple:
    """(ok, http_status_or_error_text)."""
    body = json.dumps(payload, default=str).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Crosswalk-Prometheus/1.0',
               'X-Crosswalk-Event': str(payload.get('event') or 'job.done'), 'X-Crosswalk-Delivery': uuid.uuid4().hex,
               'X-Crosswalk-Signature': sign(doc.get('secret') or '', body)}
    try:
        status = post(doc['url'], body, headers)
        return (200 <= status < 300), status
    except Exception as e:
        return False, str(e)[:160]


def sweep(*, now: float = None, post=_post, find_job=None) -> dict:
    """Deliver every pending webhook whose job reached a terminal state.
    Returns counts. Safe to run every few seconds."""
    from .service import find_job_doc
    find_job = find_job or find_job_doc
    now = now or time.time()
    counts = {'pending': 0, 'delivered': 0, 'retry': 0, 'failed': 0, 'waiting': 0}
    for key in _list(PENDING_PREFIX):
        doc = _load(key)
        if not isinstance(doc, dict):
            continue
        counts['pending'] += 1
        if float(doc.get('next_at') or 0) > now:
            counts['waiting'] += 1
            continue
        job_doc, kind = find_job(doc.get('job_id'))
        if not isinstance(job_doc, dict):
            if now - float(doc.get('created_at') or now) > 7 * 86400:
                _move(key, dict(doc, outcome='job_missing'), FAILED_PREFIX)
            continue
        status = str(job_doc.get('status') or '').lower()
        event = TERMINAL.get(status)
        if not event:
            counts['waiting'] += 1
            continue
        ok, info = deliver(doc, _payload_for(doc, job_doc, kind, event), post=post)
        doc.setdefault('attempts', []).append({'at': now, 'ok': ok, 'info': info})
        if ok:
            counts['delivered'] += 1
            _move(key, dict(doc, outcome='delivered', event=event), DONE_PREFIX)
            continue
        if len(doc['attempts']) >= MAX_ATTEMPTS:
            counts['failed'] += 1
            _move(key, dict(doc, outcome='failed', event=event), FAILED_PREFIX)
            continue
        counts['retry'] += 1
        doc['next_at'] = now + BACKOFF_S[min(len(doc['attempts']), len(BACKOFF_S) - 1)]
        try:
            _s3().put_object(Bucket=host.bucket, Key=key, Body=json.dumps(doc, default=str).encode('utf-8'), ContentType='application/json')
        except Exception:
            pass
    return counts
