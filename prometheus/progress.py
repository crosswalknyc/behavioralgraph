"""Live progress for one ask (Jenna, 2026-10-14: "build a really good
indicator that it is working and let the user know it'll be emailed to
them and show up in chat when it finishes if the wait is over 45
seconds").

The widget sends an X-Prometheus-Trace id with every ask and polls
GET /api/prometheus/v1/progress/<trace> while it waits. Every lane and
stage on the server calls `say(label)` with a plain, query-tied line
("Reading your question", "Sizing FanDuel and Kalshi", "Checking the
figures"); the latest line is what the reader sees under the pulsing
mark. Storage is a small in-process map plus a best-effort S3 copy so a
poll served by another web process still sees the line. Entries expire
after ten minutes.

Never raises; a progress miss costs nothing but the line.
"""
from __future__ import annotations

import json
import threading
import time
import traceback

PREFIX = 'system/pm_progress/'
TTL_S = 600
LONG_WAIT_S = 45
_mem = {}
_lock = threading.Lock()

# internal stage keys (recorded when a step finishes) -> the plain line
# for what runs next (query-tied lines come from say())
STAGE_LABELS = {
    'router_classify': 'Working out what you need', 'router': 'Working out what you need',
    'interpret_prep': 'Drafting the brief', 'semantic_guards': 'Drafting the brief', 'identity': 'Drafting the brief',
    'estimate': 'Sizing the audience', 'verify': 'Writing the answer',
    'digest': 'Working through the page', 'anchors': 'Working through the page', 'ledger': 'Working through the page',
    'model': 'Checking the figures', 'refusal_retry': 'Checking the figures',
    'projection_fix': 'Checking the figures', 'projection_reask': 'Checking the figures',
    'approve_spec': 'Locking the brief', 'approve_rescue': 'Locking the brief', 'approve_queue_post': 'Starting the build',
    'cohort_churn_lane': 'Working out the cohort', 'dimension_split_lane': 'Splitting the figure',
    'viewer_scope': 'Scoping the viewers', 'subiq_answer_in_place': 'Writing the answer', 'handoff_generate': 'Running the numbers',
    'reader': 'Reading your question', 'research': 'Checking published figures',
}


def current_trace():
    try:
        from flask import g as _g, has_request_context
        if has_request_context():
            return str(getattr(_g, '_pm_trace_id', '') or '')
    except Exception:
        pass
    return ''


def _write_s3(trace, doc):
    try:
        from .host import host as _h
        _h.s3_client.put_object(Bucket=_h.bucket, Key=f"{PREFIX}{trace}.json",
                                Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
    except Exception:
        pass


def say(label, trace=None, *, sync=False):
    """Record the current line for the ask. Returns the doc or None."""
    try:
        trace = trace or current_trace()
        label = ' '.join(str(label or '').split())[:140]
        if not trace or not label:
            return None
        now = time.time()
        with _lock:
            doc = _mem.get(trace) or {'trace': trace, 'started': now, 'lines': []}
            if not doc['lines'] or doc['lines'][-1]['label'] != label:
                doc['lines'].append({'label': label, 'at': now})
            doc['label'] = label
            doc['updated'] = now
            doc['elapsed_s'] = int(now - doc['started'])
            _mem[trace] = doc
            if len(_mem) > 2000:
                for k in [k for k, v in _mem.items() if now - v.get('updated', 0) > TTL_S][:500]:
                    _mem.pop(k, None)
        if sync:
            _write_s3(trace, doc)
        else:
            threading.Thread(target=_write_s3, args=(trace, dict(doc)), daemon=True).start()
        return doc
    except Exception:
        traceback.print_exc()
        return None


def stage(key, trace=None):
    """A stage key from the latency instrumentation becomes a plain line."""
    label = STAGE_LABELS.get(str(key or ''))
    if label:
        say(label, trace)


def done(trace=None, *, emailed=False):
    try:
        trace = trace or current_trace()
        if not trace:
            return
        with _lock:
            doc = _mem.get(trace) or {'trace': trace, 'started': time.time(), 'lines': []}
            doc['done'] = True
            doc['emailed'] = bool(emailed)
            doc['updated'] = time.time()
            _mem[trace] = doc
        threading.Thread(target=_write_s3, args=(trace, dict(doc)), daemon=True).start()
    except Exception:
        pass


def get(trace):
    """The progress doc for a trace: memory first, then the S3 copy."""
    trace = str(trace or '').strip()[:32]
    if not trace:
        return None
    with _lock:
        doc = _mem.get(trace)
    if doc:
        return dict(doc)
    try:
        from .host import host as _h
        body = _h.s3_client.get_object(Bucket=_h.bucket, Key=f"{PREFIX}{trace}.json")['Body'].read()
        return json.loads(body.decode('utf-8') or '{}') or None
    except Exception:
        return None


def long_wait(doc, now=None):
    """True once the ask has run past the 45 second mark."""
    try:
        now = now or time.time()
        return bool(doc) and (now - float(doc.get('started') or now)) >= LONG_WAIT_S
    except Exception:
        return False
