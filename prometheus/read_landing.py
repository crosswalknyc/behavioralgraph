"""A finished job lands in the thread once, whoever is looking (2026-11-05).

Found that day: a read launched from a session the server persists (an
API conversation, an operator session, any client that is not the
dashboard widget) carried its ack turn in the thread with the job id on
it ("On it: reading X now", meta.read_job_id). The landing step read
"a turn carries this job id" as "already landed" and returned, so the
finished read sat in the job payload and never reached the thread. The
job document also carried no thread_id, so the landing went to whatever
thread was active.

The rule now:

* A turn that carries the job id is LANDED only when it also carries
  the payload (its text is the reply) or the landed mark (meta.landed_at).
  A job id alone is the ack: the promise, waiting to be filled.
* The server lands the payload INTO the ack turn (same turn_id and
  moment, the reply as its text, the read's meta on it, landed_at set)
  or appends a turn when no ack is in the thread.
* Two landings of one job collapse to one turn: the merge
  (prometheus.thread_merge) treats two agent turns with one job id as
  one turn and keeps the landed reading.
* The widget's own copy (its poll filled the turn before the pull did)
  is the same payload text under the same job id: landed, no duplicate.

Pure functions; no Flask, no host. prometheus.legacy.land,
prometheus.read_recovery and prometheus.drilldown call in. Regression:
scripts/test_pm_read_landing.py.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone

__all__ = ['JOB_MARKS', 'LANDED_MARK', 'text_key', 'landing_state', 'land', 'read_meta',
           'landed_since_last_user', 'note_landed_since', 'reset_landed_since', 'pending_landed', 'attach_landed_note', 'landed_line']

# the job marks a server turn can carry (one per job family)
JOB_MARKS = ('read_job_id', 'deck_job_id', 'jiq_job_id', 'bpiq_job_id', 'fw_job_id', 'aiq_job_id',
             'tlist_job_id', 'twin_job_id')
LANDED_MARK = 'landed_at'
# ack-only keys that never ride into the landed turn
_ACK_ONLY = ('drilldown', 'blank_filled', 'surface')


def text_key(text):
    return ' '.join(str(text or '').split())


def _meta(t):
    m = t.get('meta') if isinstance(t, dict) else None
    return m if isinstance(m, dict) else {}


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def landing_state(history, meta_key, job_id, text=''):
    """('landed', i) when turn i carries the job id with the payload
    (its text is `text`) or the landed mark; ('ack', i) when turn i
    carries the job id alone (the promise); ('none', None) when no turn
    carries the job id. A landed turn wins over an ack when both are
    present (two readings of one job)."""
    if not job_id:
        return 'none', None
    want = text_key(text)
    ack = None
    for i, t in enumerate(history or []):
        try:
            if _meta(t).get(meta_key) != job_id:
                continue
            if _meta(t).get(LANDED_MARK):
                return 'landed', i
            if want and text_key(t.get('text')) == want:
                return 'landed', i
            if ack is None:
                ack = i
        except Exception:
            continue
    return ('ack', ack) if ack is not None else ('none', None)


def read_meta(job_id, payload, meta_key='read_job_id'):
    """The thread meta for a finished read: the job id, kind, follow-up
    chips, the CSV link and the chart (they survive a reload), the
    subject and the figure ledger, and the landed mark."""
    p = payload if isinstance(payload, dict) else {}
    followups = [f for f in (p.get('followups') or []) if isinstance(f, str)][:6]
    meta = {meta_key: job_id, 'kind': 'read',
            'options': [{'label': f, 'send': f} for f in followups]}
    for k in ('file_link', 'chart'):
        if isinstance(p.get(k), dict) and p[k].get('url'):
            meta['link' if k == 'file_link' else 'chart'] = dict(p[k])
    if p.get('profile'):
        meta['subject'] = str(p.get('profile'))[:120]
    try:
        figs = [f for f in (p.get('figures') or []) if isinstance(f, dict) and f.get('value') is not None]
        if figs:
            meta['figures'] = [{'value': f.get('value'), 'kind': f.get('kind'), 'label': str(f.get('label') or '')[:120],
                                'source': str(f.get('source') or '')[:40]} for f in figs[:40]]
    except Exception:
        pass
    if p.get('faulted'):
        meta['faulted'] = str(p.get('faulted'))[:40]
    meta[LANDED_MARK] = _now_iso()
    return meta


def land(history, meta_key, job_id, text, meta, *, ts=None):
    """The thread with the payload landed once. Returns (history, how):
    how is 'already' (nothing written: the turn carries the payload or
    the landed mark), 'filled' (the ack turn now carries the payload),
    or 'appended' (a new turn). The ack keeps its turn_id and moment so
    every copy of the thread sees one turn change text rather than a
    second one appear; ack-only meta comes off, the ack's other meta
    (a subject note, options the offer carried) is kept under the
    read's."""
    hist = list(history or [])
    text = str(text or '')
    meta = dict(meta or {})
    meta.setdefault(meta_key, job_id)
    meta.setdefault(LANDED_MARK, _now_iso())
    state, i = landing_state(hist, meta_key, job_id, text)
    if state == 'landed':
        return hist, 'already'
    if state == 'ack':
        ack = dict(hist[i])
        kept = {k: v for k, v in _meta(ack).items() if k not in _ACK_ONLY and k != 'options'}
        kept.update(meta)
        ack['text'] = text
        ack['meta'] = kept
        ack['role'] = 'agent'
        hist[i] = ack
        return hist, 'filled'
    hist.append({'role': 'agent', 'text': text, 'ts': ts or _now_iso(), 'meta': meta})
    return hist, 'appended'


# ------------------------------------------------------------------------
# A finished read never rides inside another answer (Jenna 2026-11-10).
#
# Found that day: a Sony Television seat asked "where is the digital
# journey in the dashboard?" and the status lane answered "Your read for
# Select a Journey finished 5 minutes ago. Here it is:" with the whole
# Walter Boys read under it, then two more "finished 41 / 47 minutes ago
# ... open the tab again" lines. The read had already landed in the
# thread as its own turn (above); gluing it onto the reply to a
# different question buried the question and repeated the read.
#
# The rule: the reply to a question is the answer to that question. When
# a read landed since the reader's last message and the copy of the
# thread the reader is looking at does not show it yet, the reply gets
# ONE trailing line, "Your <name> read landed above.", and the widget
# pulls the thread so the landed turn shows. When the landed turn is
# already visible, nothing is added.

_LANDED_SINCE = ContextVar('pm_landed_since', default=None)


def _epoch(t):
    try:
        from .thread_merge import turn_epoch
        return turn_epoch(t)
    except Exception:
        return 0.0


def _is_promise_text(text):
    """The ack's own words, with or without the included-with-your-plan
    lead (prometheus.thread_merge.is_promise knows the same shapes)."""
    import re as _re
    s = str(text or '')[:400]
    if _re.match(r"^\W*(?:included with your plan[.!]?\s*)?(?:on it\b|already on it\b|working on it\b)", s, _re.I):
        return True
    try:
        from .thread_merge import is_promise
        return bool(is_promise({'text': s}))
    except Exception:
        return False


def _is_landed_turn(t):
    """An agent turn that carries a read's payload: the landed mark, the
    read kind, or a read job id with a text that is not the ack's
    promise."""
    m = _meta(t)
    if str((t or {}).get('role') or '') not in ('agent', 'assistant'):
        return False
    if not m.get('read_job_id'):
        return False
    if m.get(LANDED_MARK) or str(m.get('kind') or '') == 'read':
        return True
    txt = str((t or {}).get('text') or '').strip()
    return bool(txt) and not _is_promise_text(txt)


def landed_since_last_user(stored, client_history):
    """Reads in the stored thread that landed after the client's last
    user turn and that the client's copy does not show as a landed turn:
    [{'job_id', 'name', 'ts'}], oldest first. [] when the client sent no
    history (a caller that reads the stored thread sees the turns)."""
    if not isinstance(client_history, list) or not isinstance(stored, list):
        return []
    client = [t for t in client_history if isinstance(t, dict)]
    last_user = 0.0
    for t in client:
        if str(t.get('role') or '') == 'user':
            last_user = max(last_user, _epoch(t))
    if not last_user:
        # no user turn in the client's copy: measure from its last turn of any kind
        for t in client:
            last_user = max(last_user, _epoch(t))
    seen_jobs = set()
    seen_text = set()
    for t in client:
        m = _meta(t)
        if _is_landed_turn(t):
            seen_jobs.add(str(m['read_job_id']))
        tk = text_key(t.get('text'))
        if tk:
            seen_text.add(tk)
    out = []
    for t in stored:
        if not isinstance(t, dict) or not _is_landed_turn(t):
            continue
        m = _meta(t)
        job = str(m.get('read_job_id') or '')
        when = _epoch(t)
        landed_at = str(m.get(LANDED_MARK) or '')
        if landed_at:
            try:
                when = max(when, datetime.fromisoformat(landed_at.replace('Z', '+00:00')).timestamp())
            except Exception:
                pass
        if last_user and when <= last_user:
            continue
        if job in seen_jobs or text_key(t.get('text')) in seen_text:
            continue
        out.append({'job_id': job, 'name': str(m.get('subject') or '').strip(), 'ts': str(t.get('ts') or '')})
    out.sort(key=lambda r: r['ts'])
    return out


def reset_landed_since():
    _LANDED_SINCE.set(None)


def note_landed_since(uname, tid, client_history, loader):
    """Compute the landed-since list for this ask from the stored thread
    (``loader(uname, tid)``) and keep it for the reply. Never raises."""
    reset_landed_since()
    try:
        if not uname or not tid or not isinstance(client_history, list):
            return []
        stored = loader(uname, tid) or []
        hits = landed_since_last_user(stored, client_history)
        _LANDED_SINCE.set(hits or None)
        return hits
    except Exception:
        return []


def pending_landed():
    return _LANDED_SINCE.get() or []


def landed_line(hits):
    """'Your X read landed above.' / 'Your X and Y reads landed above.'"""
    names = []
    for h in hits or []:
        n = str((h or {}).get('name') or '').strip()
        if n and n not in names:
            names.append(n)
    if not names:
        return "Your read landed above." if len(hits or []) == 1 else "Your reads landed above."
    if len(names) == 1:
        return f"Your {names[0]} read landed above."
    return f"Your {' and '.join(names[:2])} reads landed above."


def attach_landed_note(env, raw, *, via='session'):
    """Add the one trailing landed line to an envelope and its raw payload
    when a read landed since the reader's last message and the reader's
    copy does not show it; mark the payload so the widget pulls the
    thread. Idempotent per ask (the list is cleared once attached). A
    status answer that already names the landing (raw.landed_noted) gets
    the widget mark only."""
    hits = pending_landed()
    if not hits or not isinstance(env, dict):
        return env
    raw = raw if isinstance(raw, dict) else {}
    try:
        _prev = [str(x) for x in (raw.get('landed_since') or []) if x] if isinstance(raw.get('landed_since'), list) else []
        raw['landed_since'] = list(dict.fromkeys(_prev + [str(h.get('job_id')) for h in hits if h.get('job_id')]))
        if via == 'api_key' or raw.get('landed_noted') or env.get('kind') in ('error',):
            return env
        line = landed_line(hits)
        text = str(env.get('text') or '')
        if line in text:
            return env
        env['text'] = (text.rstrip() + '\n\n' + line) if text.strip() else line
        for k in ('reply', 'error', 'message', 'question', 'summary', 'text'):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                if line not in v:
                    raw[k] = v.rstrip() + '\n\n' + line
                break
    finally:
        reset_landed_since()
    return env
