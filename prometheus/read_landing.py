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

from datetime import datetime, timezone

__all__ = ['JOB_MARKS', 'LANDED_MARK', 'text_key', 'landing_state', 'land', 'read_meta']

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
