"""Faults with a budget (audit item 8, Jenna 2026-10-14).

Hundreds of `except Exception: pass` blocks meant a failure usually
became a slightly worse answer instead of a visible fault. Every
swallow now goes through `note(label, exc)`: one log line with the
trace id, a per-label counter in this process, and a per-process daily
document under system/ops/pm_faults/<day>/<instance>.json flushed every
minute. The nightly sums the day's documents, lists the top labels, and
alerts when a label runs past its budget.

  from prometheus import faults as _faults
  try:
      ...
  except Exception as _fx:
      _faults.note('service.catalog_lane', _fx)

Never raises; a fault in the fault recorder is dropped.
"""
from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time
import traceback

PREFIX = 'system/ops/pm_faults/'
FLUSH_EVERY_S = 60
BUDGETS = {'default': 40}          # per label per day; exceed -> the nightly flags it
_counts = collections.Counter()
_samples = {}
_lock = threading.Lock()
_last_flush = 0.0
_INSTANCE = f"{socket.gethostname()[:24]}-{os.getpid()}"
_flusher_started = False


def _trace():
    try:
        from flask import g as _g, has_request_context
        if has_request_context():
            return str(getattr(_g, '_pm_trace_id', '') or '')
    except Exception:
        pass
    return ''


def note(label, exc=None, detail=''):
    """Record one swallowed fault. Returns the running count for the label."""
    global _flusher_started
    try:
        label = str(label or 'unlabeled')[:80]
        tr = _trace()
        msg = f"{type(exc).__name__}: {exc}" if exc is not None else str(detail or '')
        print(f"[fault] {label}{(' trace=' + tr) if tr else ''} {msg[:300]}")
        with _lock:
            _counts[label] += 1
            n = _counts[label]
            if label not in _samples:
                _samples[label] = {'first_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'trace': tr, 'message': msg[:300]}
        if not _flusher_started:
            _flusher_started = True
            threading.Thread(target=_flush_loop, daemon=True, name='pm-faults-flush').start()
        return n
    except Exception:
        return 0


def snapshot():
    with _lock:
        return {'instance': _INSTANCE, 'day': time.strftime('%Y-%m-%d', time.gmtime()), 'counts': dict(_counts),
                'samples': dict(_samples), 'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def flush(force=False):
    global _last_flush
    try:
        if not force and time.time() - _last_flush < FLUSH_EVERY_S:
            return False
        snap = snapshot()
        if not snap['counts']:
            return False
        from .host import host as _h
        _h.s3_client.put_object(Bucket=_h.bucket, Key=f"{PREFIX}{snap['day']}/{_INSTANCE}.json",
                                Body=json.dumps(snap).encode('utf-8'), ContentType='application/json')
        _last_flush = time.time()
        return True
    except Exception:
        return False


def _flush_loop():
    while True:
        try:
            time.sleep(FLUSH_EVERY_S)
            flush(force=True)
        except Exception:
            pass


def day_report(s3, bucket, day):
    """Summed counts for a day across processes: {'counts', 'samples',
    'over_budget': [(label, n, budget)]}."""
    counts = collections.Counter()
    samples = {}
    try:
        token = None
        while True:
            kw = {'Bucket': bucket, 'Prefix': f"{PREFIX}{day}/"}
            if token:
                kw['ContinuationToken'] = token
            r = s3.list_objects_v2(**kw)
            for o in r.get('Contents') or []:
                try:
                    doc = json.loads(s3.get_object(Bucket=bucket, Key=o['Key'])['Body'].read())
                except Exception:
                    continue
                for k, v in (doc.get('counts') or {}).items():
                    counts[k] += int(v or 0)
                for k, v in (doc.get('samples') or {}).items():
                    samples.setdefault(k, v)
            if not r.get('IsTruncated'):
                break
            token = r.get('NextContinuationToken')
    except Exception:
        traceback.print_exc()
    over = [(k, n, BUDGETS.get(k, BUDGETS['default'])) for k, n in counts.items() if n > BUDGETS.get(k, BUDGETS['default'])]
    return {'day': day, 'counts': dict(counts.most_common()), 'samples': samples, 'over_budget': sorted(over, key=lambda x: -x[1])}


def render_lines(report):
    rep = report or {}
    counts = rep.get('counts') or {}
    total = sum(counts.values())
    lines = [f"Faults ({rep.get('day')}): {total} swallowed across {len(counts)} labels"]
    for k, n in list(counts.items())[:8]:
        smp = (rep.get('samples') or {}).get(k) or {}
        lines.append(f"- {k}: {n}" + (f" ({smp.get('message', '')[:90]})" if smp.get('message') else ''))
    for k, n, b in rep.get('over_budget') or []:
        lines.append(f"  OVER BUDGET: {k} {n} > {b}")
    return lines


# ---------------------------------------------------------------------------
# One retry before the calm line (I4b, 2026-10-20)
# ---------------------------------------------------------------------------
def retry_once(fn, args, kwargs, question, history, first_exc):
    """A chat route raised. Most of a day's faults were binding
    failures, so the turn runs once more with the binder's subject
    on flask.g (``_pm_retry_bound``) for the lanes that take a bound
    subject. A second failure re-raises the SECOND exception carrying
    the bound subject and the suggested path, so the ops mail names
    them and the ask log reads 'faulted'. Never retries a retry."""
    try:
        from flask import g as _g
        if getattr(_g, '_pm_retried', False):
            raise first_exc
        _g._pm_retried = True
        bound = ''
        try:
            from . import subject_rules as _sr
            page = ''
            try:
                from flask import request as _rq
                body = _rq.get_json(force=True, silent=True) or {}
                page = _sr._page_name(body.get('page_context') or body.get('context') or {})
            except Exception:
                page = ''
            s = _sr.bind(question, history, page)
            bound = s.display if (s and s.source == 'words') else ''
        except Exception:
            bound = ''
        _g._pm_retry_bound = bound
        note('chat.retry_once', first_exc, detail=f"bound={bound!r}")
    except Exception as e:
        if e is first_exc:
            raise
    try:
        return fn(*args, **kwargs)
    except Exception as second:
        try:
            from flask import g as _g2
            setattr(second, 'pm_bound_subject', getattr(_g2, '_pm_retry_bound', ''))
            setattr(second, 'pm_first_error', f"{type(first_exc).__name__}: {str(first_exc)[:200]}")
            _g2._pm_ask_outcome = 'faulted'
        except Exception:
            pass
        raise
