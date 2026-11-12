"""Who may bank a figure in the shared insights ledger (2026-11-11).

A `canary:` page-analysis probe asked "What share of Chime Banking
Customers engage with insurance brands overall" on production. The
answer it got ("392,312,555 insurance-active individuals") joined the
ledger under the real subject and was replayed to whoever asked next.
The row was pulled by hand at 21:43 UTC; a second probe banked it again
at 21:45. The ledger had no idea who was asking: `persist` took a
subject and a reply, never an asker, and half its callers run on a
thread that has left the request behind.

This module is the one answer to "may the work in flight bank?", read
by the ledger itself at every write (`insights_ledger.persist`,
`bank_record`, `ingest_deck_anchors` and the last step before S3,
`_record_entry_now`), so a route that forgets to ask still cannot write.

The asker of the work in flight, in order:

  1. an asker carried explicitly: `as_asker(name, probe=...)` around a
     job body, or `carry(fn)` around a thread target (the asker seen on
     the request thread rides onto the thread);
  2. the request in flight: a probe caller (the `X-Prometheus-Caller`
     header, a user agent that names itself a probe, `g._pm_probe`)
     reads as `canary:<label>` whatever seat's session it runs under;
     otherwise the gated identity (`g._pm_ask_user`), the session user,
     the API key's owner;
  3. nobody: an operator's script, a batch job, a deliberate correction.

Only a positive probe identity is refused (`probe_identity.asks_on_purpose`:
canary:*, smoke, golden, regression, a sandbox key's owner). An asker
nobody can name is not a probe; operators bank on purpose. The `replay`
user is not one either: it stands in for the real reader whose
conversation it replays, and it only ever runs inside the replay child,
whose ledger is the harness's own in-memory store.

Nothing here raises. `refusals()` is the short in-process trail the
regression test and the logs read.
"""
from __future__ import annotations

import contextvars
import functools
import re

_ASKER = contextvars.ContextVar('pm_ledger_asker', default=None)   # (label, probe) or None
_REFUSED = []
_REFUSED_MAX = 50
_PROBE_UA_RX = re.compile(r'canary|smoke|regression|probe', re.I)
PROBE_LABEL = 'canary:probe'
_NOBODY = frozenset(('unknown', 'anon', 'replay'))
_JOBS = {}            # job id -> (label, probe) noted on the request thread, read once by the job body
_JOBS_MAX = 400


def _label(caller):
    return 'canary:' + (re.sub(r'[^a-z0-9_-]+', '', str(caller or '').lower())[:40] or 'probe')


def _request_asker():
    """(label, probe) for the request in flight, or None outside one."""
    try:
        from flask import g, has_request_context, request, session
        if not has_request_context():
            return None
        caller = str(request.headers.get('X-Prometheus-Caller') or '').strip()
        if caller:
            return _label(caller), True
        if _PROBE_UA_RX.search(str(request.headers.get('User-Agent') or '')):
            return PROBE_LABEL, True
        if getattr(g, '_pm_probe', False):
            return PROBE_LABEL, True
        user = ''
        for name in ('_pm_ask_user', '_pm_api_key_owner'):
            user = str(getattr(g, name, '') or '').strip()
            if user:
                break
        if not user:
            try:
                user = str(session.get('username') or '').strip()
            except Exception:
                user = ''
        return (user, False) if user else None
    except Exception:
        return None


def current():
    """(label, probe) for the work in flight, or None when nobody can
    be named. A request's own probe signal wins over a carried name."""
    req = _request_asker()
    if req and req[1]:
        return req
    carried = _ASKER.get()
    if carried:
        return carried
    return req


def current_label():
    cur = current()
    return str(cur[0] or '') if cur else ''


def is_probe(asker=None, record=None, probe=False):
    """True when the named asker (or, with none named, the work in
    flight) is a probe identity."""
    try:
        if probe:
            return True
        cur = current()
        if cur and cur[1]:
            return True     # the request or the job says probe, whatever name a caller passed
        label = str(asker or '').strip() if asker is not None else (str(cur[0] or '').strip() if cur else '')
        if not label or label.lower() in _NOBODY:
            return False    # nobody named, or the replay stand-in: not a probe (an operator's script banks on purpose)
        from . import probe_identity as _pi
        return bool(_pi.asks_on_purpose(label, record))
    except Exception:
        return False


def may_bank(asker=None, record=None, probe=False):
    """False for a probe's ask on any route; True otherwise."""
    return not is_probe(asker, record, probe)


def refuse(where, subject='', question='', asker=None):
    """Record one refused write (in-process trail and one log line)."""
    try:
        who = str(asker or current_label() or 'probe')
        _REFUSED.append({'where': str(where)[:40], 'asker': who[:80], 'subject': str(subject or '')[:120],
                         'question': str(question or '')[:160]})
        del _REFUSED[:-_REFUSED_MAX]
        print(f"[insights-ledger] not banked ({where}): the asker is a probe ({who}) | {str(subject or '')[:80]}")
    except Exception:
        pass


def refusals():
    return list(_REFUSED)


def clear_refusals():
    del _REFUSED[:]


class as_asker:
    """`with as_asker('jessie'):` around a job body: every ledger write
    inside belongs to that asker. `probe=True` marks the job a probe's
    whatever seat it runs as (a job launched by a probe under a session)."""

    def __init__(self, name, probe=False):
        self._val = (str(name or '').strip(), bool(probe))
        self._tok = None

    def __enter__(self):
        try:
            self._tok = _ASKER.set(self._val)
        except Exception:
            self._tok = None
        return self

    def __exit__(self, *exc):
        try:
            if self._tok is not None:
                _ASKER.reset(self._tok)
        except Exception:
            pass
        return False


def carry(fn, name=None, probe=None, from_request=True):
    """`fn` wrapped so it runs as the asker seen right now (a thread has
    no request and no context of its own). `name` is used when nobody
    can be read off the request; `probe` forces the flag either way.
    `from_request=False` for a job restarted on someone else's request
    (a sweep): the job's own owner and flag, nothing from the request."""
    cur = current() if from_request else None
    label = str((cur[0] if cur else '') or name or '').strip()
    flag = bool(cur[1]) if cur else False
    if probe is not None:
        flag = flag or bool(probe)
    if not label and not flag:
        return fn

    @functools.wraps(fn)
    def _carried(*a, **k):
        with as_asker(label, probe=flag):
            return fn(*a, **k)
    return _carried


def note_job(job_id, name='', probe=False):
    """On the request thread, before a job is handed to a thread: who
    asked, and whether the request is a probe's. The job body picks it
    up through `job_scope` (the thread itself has no request)."""
    try:
        cur = current()
        label = str((cur[0] if cur else '') or name or '').strip()
        flag = bool(cur and cur[1]) or bool(probe)
        _JOBS[str(job_id)] = (label, flag)
        if len(_JOBS) > _JOBS_MAX:
            for k in list(_JOBS)[:len(_JOBS) - _JOBS_MAX]:
                _JOBS.pop(k, None)
    except Exception:
        pass


def job_scope(user_arg=1, probe_kw='probe'):
    """Decorator for a job body `fn(job_id, username, ...)`: every ledger
    write inside it belongs to the asker noted at launch (`note_job`),
    else to the job's own username and probe flag. A scope already set
    by the caller (the worker's inline run) is left alone."""
    def deco(fn):
        @functools.wraps(fn)
        def inner(*a, **k):
            try:
                if _ASKER.get() is not None:
                    return fn(*a, **k)
                val = _JOBS.pop(str(a[0]) if a else '', None)
                if val is None:
                    name = a[user_arg] if len(a) > user_arg else ''
                    val = (str(name or '').strip(), bool(k.get(probe_kw)) if probe_kw else False)
            except Exception:
                return fn(*a, **k)
            with as_asker(val[0], probe=val[1]):
                return fn(*a, **k)
        return inner
    return deco


__all__ = ['may_bank', 'is_probe', 'current', 'current_label', 'as_asker', 'carry', 'note_job', 'job_scope', 'refuse',
           'refusals', 'clear_refusals', 'PROBE_LABEL']
