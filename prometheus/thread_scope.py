"""Nothing crosses threads (Jenna 2026-11-11, Armine Meseldzhyan at
Lionsgate).

Armine, verbatim: "sometimes when I start a new chat with Prometheus and
ask a question about a new profile, such as Cruel Summer, it will pull in
information from past chats, like Mad Men, and switch the subject."

Her record on 2026-11-10 (the thread documents, the ask log and the two
read job documents) shows how one chat reached another:

  * her Mad Men question was posted on a new thread; while it ran she
    opened her first Cruel Summer thread. The read worker, a second
    process, re-derived the job's thread as "the reader's active thread"
    and wrote that over the thread the launch had recorded, so the Mad
    Men read landed in the Cruel Summer thread;
  * the widget wrote every reply and every finished read into whichever
    thread was on screen when it arrived, so the Mad Men "On it" line
    went to the Cruel Summer thread and the Cruel Summer read went under
    the Mad Men question;
  * an ask that named no thread, or a thread the index had lost, ran on
    the reader's active thread: its pinned subject, its project binding,
    its stored turns;
  * the thread a page switch left rode into the new one for four asks
    (``carried_from``); a typed approval bound to the newest open offer
    on any recent thread; a stashed intake brief answered a run order on
    any thread; the reader's remembered subjects and last window from
    every earlier chat were offered back as chips and written into the
    model's "about this user" block.

The rule this module carries, in one place:

  A turn belongs to the thread it was asked in. A follow-up inherits
  inside its thread. Nothing inherits across threads: no subject, base,
  window, pending action, wizard or cut plan.

What lives here:

  resolve_ask_thread   the thread an ask runs on. A named thread the
                       index lists is that thread. A named thread the
                       index lost is re-listed under its own id for the
                       dashboard (the tab is on it), never swapped for
                       the active one. No thread named: the dashboard
                       gets the empty pane's thread or a new one, never
                       one that holds a conversation; any other caller
                       runs with no thread at all (no pin, no binding,
                       no stored turns), never on the dashboard's
                       active thread.
  job_thread           the thread a job lands in: the launch's record
                       first (a resumed copy on the read worker never
                       re-derives it), then the request's own thread;
                       the active thread only for a web request that
                       named none.
  current_thread_id    the thread of the request in flight.
  scope                (scoped, thread id) for the request in flight: an
                       ask through the service is scoped, with a thread
                       or with none; code that runs outside any ask (a
                       sweeper, a test of a store on its own) is not.
  stamp / mine         per-user stores (remembered asks, the intake
                       stash, page confirms, private notes) carry the
                       thread they were written on and answer only on
                       that thread. An ask scoped to no thread reads
                       none of them.

Pure functions plus two small reads of the request context; nothing here
raises into a request. Regression: scripts/test_pm_new_chat_no_bleed.py.
"""
from __future__ import annotations

import re

__all__ = ['valid_id', 'current_thread_id', 'scope', 'set_current_thread', 'set_request_thread', 'probe_request',
           'resolve_ask_thread', 'job_thread', 'stamp', 'mine', 'only_mine', 'same']

_ID_RX = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{1,95}$')
THREAD_KEY = 'thread'            # the field a per-user record carries its thread under


def valid_id(tid):
    """A thread id a client may name: letters, digits, dash, underscore
    and dot, 2 to 96 characters."""
    return bool(_ID_RX.match(str(tid or '')))


def same(a, b):
    """Two thread ids name one thread. An empty id is no thread and
    matches nothing, not even another empty id."""
    a, b = str(a or '').strip(), str(b or '').strip()
    return bool(a) and a == b


# ------------------------------------------------------------ the request
def current_thread_id():
    """The thread the request in flight runs on ('' outside a request or
    for an ask that runs on no thread)."""
    try:
        from flask import g as _g, has_app_context
        if not has_app_context():
            return ''
        return str(getattr(_g, '_pm_thread_id', None) or '')
    except Exception:
        return ''


def scope():
    """(scoped, thread id) for the request in flight.

    scoped is True once the ask service (or the read worker) has put
    the ask's thread on the request, including an ask that runs on no
    thread at all (the key surface with no conversation id, a probe):
    that ask reads nothing any thread wrote. Outside an ask (no request,
    a sweeper, a store exercised on its own) nothing is scoped and the
    per-user stores read the way they always did."""
    try:
        from flask import g as _g, has_app_context
        if not has_app_context():
            return False, ''
        tid = str(getattr(_g, '_pm_thread_id', None) or '')
        return (bool(tid) or bool(getattr(_g, '_pm_thread_scoped', False))), tid
    except Exception:
        return False, ''


def set_current_thread(tid):
    """Put the ask's thread on the request context and mark the request
    scoped (the ask service does this for every ask; the read worker for
    the job it runs, so what the job remembers is stamped with the
    thread the question was asked on). ``tid`` empty: scoped to no
    thread."""
    try:
        from flask import g as _g, has_app_context
        if has_app_context():
            _g._pm_thread_id = str(tid or '') or None
            _g._pm_thread_scoped = True
            return True
    except Exception:
        pass
    return False


def set_request_thread(tid):
    """The legacy per-OS-thread marker follows the request. It used to
    be written only by the analyze and deck cores and never cleared, so
    a build ask served next on the same worker thread read the previous
    request's thread (another reader's, as often as not)."""
    try:
        import sys
        c = sys.modules.get('prometheus.legacy.chat')
        loc = getattr(c, '_PM_REQ_THREAD', None) if c is not None else None
        if loc is not None:
            loc.tid = str(tid or '')
            return True
    except Exception:
        pass
    return False


def probe_request():
    """True for a probe (the golden set, the canary, a replay, an
    operator's X-Prometheus-Caller ask): it names a thread or runs on
    none; no thread is ever minted or re-listed for it, so a probe
    leaves nothing in the reader's list."""
    try:
        from flask import g as _g, has_request_context, request as _rq
        if not has_request_context():
            return False
        if getattr(_g, '_pm_probe', False):
            return True
        return bool(str(_rq.headers.get('X-Prometheus-Caller') or '').strip())
    except Exception:
        return False


# ---------------------------------------------------------------- the ask
def resolve_ask_thread(named, client, *, exists, relist=None, mint=None):
    """(thread id or None, how) for one ask.

    ``named`` is the id the caller sent ('' for none); ``client`` is the
    caller's own label ('dashboard', 'api', 'app', ...). ``exists(tid)``
    says whether the reader's index lists it; ``relist(tid)`` puts a lost
    id back in the index; ``mint()`` creates a new thread and returns its
    id. how is one of:

      'named'     the caller's thread, listed
      'relisted'  the dashboard named a thread the index had lost; it is
                  the tab's thread again under the same id
      'minted'    the dashboard named no thread; the empty pane's thread
                  or a new one, never one that holds a conversation
      'none'      any other caller that named no thread: the ask runs on
                  no thread at all
      'unknown'   a non-dashboard caller named a thread that is not
                  theirs (the caller answers 404)
    """
    tid = str(named or '').strip()
    is_dash = str(client or '').strip().lower() == 'dashboard'
    if tid:
        try:
            if exists(tid):
                return tid, 'named'
        except Exception:
            pass
        if not is_dash:
            return None, 'unknown'
        if valid_id(tid) and callable(relist):
            try:
                relist(tid)
                return tid, 'relisted'
            except Exception:
                pass
        tid = ''
    if is_dash and callable(mint):
        try:
            new = mint()
            if new:
                return str(new), 'minted'
        except Exception:
            pass
    return None, 'none'


# ---------------------------------------------------------------- the job
def job_thread(*, recorded='', request='', stashed='', active=None, web=True):
    """The thread a job lands in.

    ``recorded`` is the thread already written on the job's document (a
    resumed copy, the read worker picking a queued job up): it always
    wins, because the launch knew which thread asked. ``request`` is the
    thread the request body named, ``stashed`` the one the ask service
    put on the request. ``active()`` (the reader's active thread) is the
    last resort and only for a web request that named none; a worker
    never falls back to it, since "active" there is whatever the reader
    opened while the job waited."""
    for cand in (recorded, request, stashed):
        cand = str(cand or '').strip()
        if cand:
            return cand
    if web and callable(active):
        try:
            return str(active() or '').strip()
        except Exception:
            return ''
    return ''


# ----------------------------------------------------- per-user stores
def stamp(rec, tid=None):
    """Write the thread a per-user record belongs to onto it. ``tid``
    defaults to the request's thread. A record written outside any
    thread carries none. Returns the record."""
    try:
        t = str(tid or '').strip() or current_thread_id()
        if isinstance(rec, dict) and t and not rec.get(THREAD_KEY):
            rec[THREAD_KEY] = t[:96]
    except Exception:
        pass
    return rec


def mine(rec, *, key=THREAD_KEY):
    """True when a per-user record may answer the request in flight: it
    was written on this ask's thread. An ask scoped to no thread reads
    no record; a record with no stamp (written before 2026-11-11, or
    outside a thread) belongs to no thread and answers no scoped ask.
    Outside an ask nothing is scoped and every record reads as before."""
    scoped, t = scope()
    if not scoped:
        return True
    if not t or not isinstance(rec, dict):
        return False
    return same(rec.get(key), t)


def only_mine(recs, *, key=THREAD_KEY):
    """The records that may answer the request in flight, in order."""
    return [r for r in (recs or []) if isinstance(r, dict) and mine(r, key=key)]
