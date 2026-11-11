"""What Prometheus says about an order follows the artifact (Jenna
2026-11-10, the day audit).

Admin, 2026-11-10 21:58 UTC: "Pull Subscriber IQ for The Paper (series,
season 1) on Peacock". The draft step failed, nothing was queued, and
the reply was the calm line: "Working on it. Confirmed your task is in
progress and I will email you when it completes." Nothing was in
progress.

The calm line is a promise (Jenna 2026-09-30), and it stays the reply
for a question that could not be answered at once: the team is told and
the answer follows by email. An ORDER is different. The reader will not
ask twice for something they were told is running, so the claim must
follow the artifact:

  * the run id or the job record exists: the order started, and the
    reply may say so;
  * the engine refused it or could not be reached: it is tried once
    more; when that fails too the reply says plainly that it did not
    start and nothing is running;
  * the engine may have taken it but gave nothing back to point at (a
    timeout, an accepted post with no run id): the reply says it could
    not be confirmed and how to check, and never claims it is running.

``queue_post(post)``       posts an approved order to the build engine
                           with one retry on a refusal; returns
                           (reply dict or None, state, detail).
``not_started_payload``    the plain reply for an order that did not
                           start; ``unconfirmed_payload`` for one that
                           could not be confirmed.
``settle(raw, text, ...)`` for the ask service: an order whose draft
                           step came back with the calm line and no
                           artifact is tried once more, then answered
                           plainly.
``has_artifact(raw)``      a run id, a job id or a draft to approve.

Pure apart from the callables it is handed. Never raises. Regression:
scripts/test_pm_conversation_plumbing.py.
"""
from __future__ import annotations

import time

__all__ = ['NOT_STARTED', 'UNCONFIRMED', 'RETRY_LINE', 'has_artifact', 'is_calm', 'not_started_payload',
           'unconfirmed_payload', 'queue_post', 'settle']

RETRY_LINE = 'That did not start. Trying again.'
NOT_STARTED = ("That did not start, and a second try did not either. Nothing is running and nothing was charged. "
               "Send it again in a minute and I will start it.")
UNCONFIRMED = ("I could not confirm that it started, so I am not going to tell you it is running. Nothing was charged. "
               "Ask me for a status update in a minute: if it is not listed, send it again and I will start it.")
_ARTIFACT_KEYS = ('run_id', 'job_id', 'read_job_id', 'deck_job_id', 'jiq_job_id', 'bpiq_job_id', 'fw_job_id',
                  'aiq_job_id', 'twin_job_id', 'tlist_job_id', 'bulk_id', 'run_ids')
_DRAFT_KEYS = ('spec_draft', 'spec_drafts', 'draft', 'drafts', 'panel_offer', 'offer', 'next_step')


def has_artifact(raw):
    """True when the payload points at something that exists: a run, a
    job, or a draft the reader can approve."""
    if not isinstance(raw, dict):
        return False
    if any(raw.get(k) for k in _ARTIFACT_KEYS):
        return True
    if isinstance(raw.get('job'), dict) and raw['job'].get('id'):
        return True
    return any(raw.get(k) for k in _DRAFT_KEYS)


def is_calm(raw, calm_message=''):
    """True for the calm line with nothing behind it."""
    if not isinstance(raw, dict) or has_artifact(raw) or raw.get('guidance'):
        return False
    msg = ' '.join(str(raw.get('error') or raw.get('reply') or '').split())
    if calm_message and msg == ' '.join(str(calm_message).split()):
        return True
    return raw.get('success') is False and msg.startswith('Working on it. Confirmed your task is in progress')


def _payload(text, state, name=''):
    out = {'success': False, 'guidance': True, 'error': text, 'order_state': state, 'not_started': True}
    if name:
        out['subject'] = str(name)[:160]
    return out


def not_started_payload(name=''):
    return _payload(NOT_STARTED, 'not_started', name)


def unconfirmed_payload(name=''):
    return _payload(UNCONFIRMED, 'unconfirmed', name)


def _is_refusal(exc):
    """A failure before the engine could have taken the order: refused,
    reset, unreachable. A timeout is not one (the post may have landed)."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if 'timeout' in name or 'timed out' in text or 'timeout' in text:
        return False
    return any(k in name for k in ('connection', 'connect', 'dns', 'refused')) or \
        any(k in text for k in ('connection refused', 'connection reset', 'max retries', 'name or service not known',
                                'failed to establish', 'connection aborted'))


def queue_post(post, *, tries=2, pause=0.8, sleep=time.sleep, on_retry=None):
    """Post an approved order to the engine.

    ``post()`` returns a response with ``status_code``, ``json()`` and
    ``text``. Returns (reply, state, detail):

      (dict, 'started', '')          the engine returned a run id
      (None, 'not_started', detail)  refused or unreachable, after one
                                     more try (a 4xx is not retried)
      (None, 'unconfirmed', detail)  a timeout, or accepted with no run
                                     id: never retried (it may be queued)
    """
    detail = ''
    for attempt in range(max(1, int(tries))):
        try:
            resp = post()
        except Exception as e:                        # noqa: BLE001 - the caller reports it
            detail = f"{type(e).__name__}: {e}"[:400]
            if not _is_refusal(e):
                return None, 'unconfirmed', detail
            if attempt + 1 < tries:
                if callable(on_retry):
                    on_retry(detail)
                sleep(pause)
                continue
            return None, 'not_started', detail
        code = int(getattr(resp, 'status_code', 0) or 0)
        if code == 200:
            try:
                doc = resp.json()
            except Exception as e:                    # noqa: BLE001
                return None, 'unconfirmed', f"unreadable engine reply: {e}"[:400]
            if isinstance(doc, dict) and doc.get('run_id'):
                return doc, 'started', ''
            return None, 'unconfirmed', f"accepted with no run id: {str(doc)[:300]}"
        detail = f"engine returned {code}: {str(getattr(resp, 'text', '') or '')[:300]}"
        if code in (502, 503, 504, 429) and attempt + 1 < tries:
            if callable(on_retry):
                on_retry(detail)
            sleep(pause)
            continue
        return None, 'not_started', detail
    return None, 'not_started', detail


def settle(raw, text, *, calm_message='', retry=None, is_order=None):
    """For the ask service: an order answered with the calm line and
    nothing behind it is tried once more (``retry()`` returns a fresh
    payload), then answered plainly. Returns (payload, changed). A
    question keeps the calm line: that promise is kept by email."""
    try:
        if not is_calm(raw, calm_message):
            return raw, False
        if is_order is None:
            from . import orders as _ord
            is_order = _ord.is_order
        if not is_order(text):
            return raw, False
        if callable(retry):
            try:
                again = retry()
            except Exception:
                again = None
            if isinstance(again, dict) and not is_calm(again, calm_message):
                return again, True
        return not_started_payload(), True
    except Exception:
        return raw, False
