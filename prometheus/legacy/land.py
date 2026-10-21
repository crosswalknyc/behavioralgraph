"""Landing a finished job in the requester's thread (moved out of
chat.py 2026-10-20 for the promise ledger, I4).

A read or a deck that finishes on a daemon thread or the Hetzner read
worker lands in the thread so it is waiting when the reader returns.
Landing is also the moment the ask-log entry that promised it is
SETTLED: the record written at "On it: reading X now" carried outcome
'answered' with only the transport line as its reply, so a read that
never landed was invisible to the nightly grading and to the watch
card. Now the record is updated to 'landed' (reply = the read's
opening, ms = total), 'held' when the read came back held, 'faulted'
when the job errored.

Chat helpers are reached through ``_C`` at call time; see threads.py.
"""
from __future__ import annotations

import time
import traceback

from prometheus.legacy import C as _C  # noqa: E402

__all__ = ['_pm_append_read_to_history', '_pm_append_deck_to_history', '_pm_settle_ask']


def _pm_settle_ask(job_id, outcome, reply='', started_at=None):
    """Update the ask-log entry that promised this job. Never raises."""
    try:
        import render_usage_log as _rul
        ms = None
        if started_at:
            try:
                ms = int((time.time() - float(started_at)) * 1000)
            except Exception:
                ms = None
        _rul.settle_ask(job_id, outcome=outcome, reply=reply, ms=ms)
    except Exception:
        pass


def _pm_append_read_to_history(username, job_id, payload, held=False, started_at=None):
    """Append a finished read to the requester's chat thread so it is
    waiting when they return, even if the tab that started it is gone.
    Uses the existing per-user history store; idempotent by
    read_job_id so it never doubles a turn the widget also delivered.
    Settles the promise in the ask log either way."""
    if not username:
        return
    try:
        reply = str((payload or {}).get('reply') or '').strip()
        _pm_settle_ask(job_id, 'held' if held else 'landed', reply, started_at)
        if not reply:
            return
        _tid = _C._pm_thread_for_job(username, job_id)
        history = _C._pm_load_thread_or_active(username, _tid)
        if _C._pm_history_has_job_turn(history, 'read_job_id', job_id):
            return
        followups = [f for f in ((payload or {}).get('followups') or [])
                     if isinstance(f, str)][:6]
        _meta = {'read_job_id': job_id, 'kind': 'read',
                 'options': [{'label': f, 'send': f} for f in followups]}
        for _k in ('file_link', 'chart'):   # the CSV link and the chart survive a reload
            if isinstance((payload or {}).get(_k), dict) and (payload or {})[_k].get('url'):
                _meta['link' if _k == 'file_link' else 'chart'] = dict((payload or {})[_k])
        if (payload or {}).get('profile'):
            _meta['subject'] = str((payload or {}).get('profile'))[:120]
        history.append({'role': 'agent', 'text': reply, 'ts': _C._pm_iso_now(), 'meta': _meta})
        _C._pm_save_thread_or_active(username, _tid, history)
    except Exception:
        traceback.print_exc()


def _pm_append_deck_to_history(username, job_id, status):
    """Append a finished deck (title + download link) to the
    requester's chat thread. Idempotent by deck_job_id."""
    if not username:
        return
    try:
        url = str((status or {}).get('url') or '')
        if not url.lower().startswith('https://'):
            return
        _tid = _C._pm_thread_for_job(username, job_id)
        history = _C._pm_load_thread_or_active(username, _tid)
        if _C._pm_history_has_job_turn(history, 'deck_job_id', job_id):
            return
        title = str((status or {}).get('title')
                    or (status or {}).get('filename')
                    or 'Profile IQ deck')
        slides = (status or {}).get('slides')
        slide_note = (f" ({slides} slides)"
                      if isinstance(slides, int) and slides else '')
        history.append({
            'role': 'agent',
            'text': (f"Deck ready: {title}{slide_note}. "
                     "The link is good for 7 days."),
            'ts': _C._pm_iso_now(),
            'meta': {'deck_job_id': job_id, 'kind': 'deck',
                     'link': {'url': url,
                              'label': 'Download the deck'}}})
        _C._pm_save_thread_or_active(username, _tid, history)
        _pm_settle_ask(job_id, 'landed', f"Deck ready: {title}", (status or {}).get('started_at'))
    except Exception:
        traceback.print_exc()
