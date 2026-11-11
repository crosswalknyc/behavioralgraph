"""Thread presence: no empty threads in the rail, and a reopen resumes
unless the thread has been idle 15 minutes (Jenna 2026-11-06).

Jenna, verbatim, with a rail showing five 'New chat' entries with no
turns stacked between real threads: "if a chat window is empty there is
no need to save it to the left rail. and if you reopen prometheus it
should only go to a new window if it has been inactive for 15m or more".

How the empties got there: every reopen after two minutes collapsed, and
every automatic switch back to the general chat, created a server thread
on the spot. A thread nobody typed into stayed in the rail as 'New chat'.

The rules this module carries:

  * A new chat is a pane, not a thread. The widget clears itself and
    posts ``threads/fresh`` (a stamp on the index, ``fresh_at``); the
    thread id is minted and its document created on the first message
    (``threads/new`` from the send path). The rail lists only threads
    with at least one turn (``visible_threads``).
  * The greeting is a bubble the widget draws, never a turn; a save that
    carries no user turn into an empty thread writes nothing.
  * On open (page load, the Prometheus button, a product switch) the
    server decides from the index (``fresh_decision``): the thread the
    reader was last in resumes when its last activity, the later of its
    last turn (``updated``) and the last time a widget was open on it
    (``last_seen``), is under IDLE_FRESH_S old and no ``fresh_at`` is
    newer; otherwise the pane opens fresh and the thread stays one click
    away in the rail. ``opened`` is not activity: the project follow
    stamps it on every automatic bind. A new tab or device reads the same
    index and behaves the same.
  * ``last_seen`` is stamped on a resume, on a rail activate, on every
    save, and by the open widget's pull (``seen=1``, throttled).
  * A sweep on load (``sweep_empty_threads``) removes zero-turn documents
    older than SWEEP_MIN_AGE_S; a thread whose index count lags a body
    that holds turns is repaired, never deleted (nothing a reader typed
    is ever removed). The active thread and the last remaining thread
    are kept (hidden while empty).

Pure functions over the index plus a small store protocol (load / put /
delete by key), so the one-off sweep over every user and the hermetic
tests run without the Flask host. Nothing here raises into a request.
"""
from __future__ import annotations

from datetime import datetime, timezone

IDLE_FRESH_S = 15 * 60          # a reopen after this long idle opens a fresh pane
SWEEP_MIN_AGE_S = 15 * 60       # an empty thread younger than this may still be typed into
SWEEP_EVERY_S = 10 * 60         # one sweep per user per this long, on load
SEEN_MIN_GAP_S = 60             # the open widget stamps last_seen at most this often


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now_utc()).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    try:
        s = str(s or '').strip()
        if not s:
            return None
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _latest(*stamps):
    ds = [d for d in (parse_iso(s) for s in stamps) if d is not None]
    return max(ds) if ds else None


def thread_of(idx, tid):
    for t in (idx or {}).get('threads', []) or []:
        if isinstance(t, dict) and t.get('id') == tid:
            return t
    return None


def active_thread(idx):
    idx = idx or {}
    th = thread_of(idx, idx.get('active'))
    if th is None and idx.get('threads'):
        th = idx['threads'][0] if isinstance(idx['threads'][0], dict) else None
    return th


def turn_count(th):
    try:
        return int((th or {}).get('turns') or 0)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------- activity
def last_activity(th):
    """The later of the thread's last turn and the last time a widget was
    open on it. ``opened`` is left out on purpose (the automatic project
    follow stamps it)."""
    return _latest((th or {}).get('updated'), (th or {}).get('last_seen'))


def idle_seconds(th, now=None):
    la = last_activity(th)
    if la is None:
        return None
    return max(0.0, ((now or now_utc()) - la).total_seconds())


def fresh_decision(idx, now=None, tid=None):
    """(fresh, reason) for a widget opening on this reader's thread.

    ``tid`` names the thread the tab is on; None means the index's active
    thread. fresh is True when the pane should open empty with no thread:
    the reader asked for a new chat and sent nothing since (``fresh_at``
    newer than the thread's last activity), or the thread's last activity
    is IDLE_FRESH_S or more ago. An empty thread is never fresh: it is
    the empty pane already. A thread with no stamps at all resumes."""
    now = now or now_utc()
    th = thread_of(idx, tid) if tid else active_thread(idx)
    if th is None:
        return False, 'no_thread'
    la = last_activity(th)
    fresh_at = parse_iso((idx or {}).get('fresh_at'))
    if turn_count(th) <= 0:
        if fresh_at is not None and (la is None or fresh_at >= la):
            return True, 'fresh_requested'
        return False, 'empty_thread'
    if fresh_at is not None and (la is None or fresh_at >= la):
        return True, 'fresh_requested'
    if la is None:
        return False, 'no_stamp'
    if (now - la).total_seconds() >= IDLE_FRESH_S:
        return True, 'idle'
    return False, 'recent'


def mark_seen(idx, tid, now=None, min_gap_s=SEEN_MIN_GAP_S):
    """Stamp ``last_seen`` on a thread entry. Returns True when the index
    changed (a stamp younger than ``min_gap_s`` is left alone so the open
    widget's pull does not write the index every twenty seconds)."""
    th = thread_of(idx, tid)
    if th is None:
        return False
    now = now or now_utc()
    prev = parse_iso(th.get('last_seen'))
    if prev is not None and min_gap_s and (now - prev).total_seconds() < min_gap_s:
        return False
    th['last_seen'] = iso(now)
    return True


def mark_fresh(idx, now=None):
    """The reader asked for a new chat: nothing is created, the index
    remembers so a reload or another tab opens the same empty pane."""
    idx['fresh_at'] = iso(now or now_utc())
    return True


def clear_fresh(idx):
    return idx.pop('fresh_at', None) is not None


# ---------------------------------------------------------------- rail
def visible_threads(threads):
    """The rail's list: threads with at least one turn."""
    return [t for t in (threads or []) if isinstance(t, dict) and turn_count(t) > 0]


def _is_greeting_turn(t):
    if not isinstance(t, dict):
        return True
    if str(t.get('role') or '') == 'user':
        return False
    meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
    return bool(meta.get('greeting') or meta.get('welcome'))


def body_has_turns(body):
    """True when a stored thread holds anything a reader or the server
    wrote; a greeting-marked agent turn alone does not count."""
    return any(not _is_greeting_turn(t) for t in (body or []) if isinstance(t, dict))


def sweep_empty_threads(idx, *, load, put, delete, key_for, now=None,
                        min_age_s=SWEEP_MIN_AGE_S, title_from=None):
    """Remove this reader's empty threads from the index and the store.

    ``idx`` is mutated. ``load(key) -> body | None``, ``put(key, obj)``,
    ``delete(key)``, ``key_for(tid) -> key``. A thread the index counts
    at zero turns whose body holds turns is repaired (count, title) and
    kept. The active thread and the last remaining thread are kept.
    Returns {'removed': [ids], 'repaired': [ids], 'kept_recent': [ids]}.
    Never raises; a store error leaves that thread in place."""
    now = now or now_utc()
    out = {'removed': [], 'repaired': [], 'kept_recent': []}
    threads = [t for t in (idx or {}).get('threads', []) or [] if isinstance(t, dict)]
    active = (idx or {}).get('active')
    keep = []
    for th in threads:
        tid = th.get('id')
        if turn_count(th) > 0 or not tid:
            keep.append(th)
            continue
        try:
            body = load(key_for(tid))
        except Exception:
            body = None
        body = body if isinstance(body, list) else []
        if body_has_turns(body):
            th['turns'] = len(body)
            if th.get('title') in (None, '', 'New chat') and callable(title_from):
                try:
                    th['title'] = title_from(body) or th.get('title') or 'New chat'
                except Exception:
                    pass
            out['repaired'].append(tid)
            keep.append(th)
            continue
        age_from = _latest(th.get('created'), th.get('updated'), th.get('opened'), th.get('last_seen'))
        age = (now - age_from).total_seconds() if age_from is not None else None
        if th.get('pinned') and (age is None or age < 48 * 3600):
            keep.append(th)          # a key-surface thread waiting on its job is not an abandoned pane (2026-11-10)
            continue
        if tid == active or (age is not None and age < min_age_s):
            if tid != active:
                out['kept_recent'].append(tid)
            keep.append(th)
            continue
        try:
            delete(key_for(tid))
        except Exception:
            keep.append(th)
            continue
        out['removed'].append(tid)
    if not keep and threads:
        keep = [threads[-1]]
        if threads[-1].get('id') in out['removed']:
            out['removed'].remove(threads[-1].get('id'))
    idx['threads'] = keep
    if active and not thread_of(idx, active) and keep:
        idx['active'] = sorted(keep, key=lambda t: str(t.get('updated') or ''), reverse=True)[0].get('id')
    idx['swept_at'] = iso(now)
    return out


def sweep_due(idx, now=None, every_s=SWEEP_EVERY_S):
    last = parse_iso((idx or {}).get('swept_at'))
    if last is None:
        return True
    return ((now or now_utc()) - last).total_seconds() >= every_s


# ---------------------------------------------------------------- host
def _host_store():
    """(load, put, delete, key_for_fn(uname), index_key_fn) over the bound
    host, or None when the host has no thread store."""
    try:
        from .host import host as h
        if not (h.has('s3_json') and h.has('s3_put_json') and h.has('thread_key') and h.has('threads_index_key')):
            return None
        s3 = h.s3_client if h.has('s3_client') else None
        bucket = (h.bucket if h.has('bucket') else None) or 'dashboard-inputs'

        def delete(key):
            if s3 is None:
                return
            s3.delete_object(Bucket=bucket, Key=key)
        return {'load': lambda k: h.s3_json(k, None), 'put': h.s3_put_json, 'delete': delete,
                'key_for': lambda uname: (lambda tid: h.thread_key(uname, tid)),
                'index_key': h.threads_index_key,
                'title_from': (h.thread_title_from if h.has('thread_title_from') else None)}
    except Exception:
        return None


def sweep_for_user(uname, idx=None, *, force=False, now=None):
    """Sweep one reader through the host. Loads the index when none is
    given; writes it back when anything changed. Returns the sweep result
    (or None when nothing was due or the host has no store)."""
    st = _host_store()
    if st is None or not uname:
        return None
    try:
        if idx is None:
            from .host import host as h
            idx = h.load_threads_index(uname) if h.has('load_threads_index') else None
        if not isinstance(idx, dict):
            return None
        if not force and not sweep_due(idx, now):
            return None
        res = sweep_empty_threads(idx, load=st['load'], put=st['put'], delete=st['delete'],
                                  key_for=st['key_for'](uname), now=now, title_from=st['title_from'])
        st['put'](st['index_key'](uname), idx)
        return res
    except Exception:
        return None


__all__ = ['IDLE_FRESH_S', 'SWEEP_MIN_AGE_S', 'SWEEP_EVERY_S', 'SEEN_MIN_GAP_S', 'iso', 'parse_iso',
           'thread_of', 'active_thread', 'turn_count', 'last_activity', 'idle_seconds', 'fresh_decision',
           'mark_seen', 'mark_fresh', 'clear_fresh', 'visible_threads', 'body_has_turns',
           'sweep_empty_threads', 'sweep_due', 'sweep_for_user']
