"""Measured daily signals for Prometheus reads (2026-10-01, Jenna:
"do flavor 1 and 2").

Flavor 1 scope, exactly as approved: read-only, TEMPLATED, aggregate-
only lookups against the pre-aggregated daily tracker tables
(reference.v_iq_daily_metrics). Never the raw event store: no UIDs, no
URLs, no row-level events leave the database - every template returns
(date, count) or (label, count) shapes that were already tallied by
the nightly tracker. The model never writes SQL; it gets a finished
text block.

Fail-safe everywhere: any miss, timeout, or connection problem returns
'' / [] and the read proceeds without the block. Kill switch:
PM_PANEL_FACT_STORE=0 (host env only, never a request field).
"""

import os
import re
import threading
import time
import traceback
from datetime import date

CH_HOST = os.environ.get('CH_HOST', '168.119.215.48')
CH_PORT = int(os.environ.get('CH_PORT', '8123'))
CH_USER = os.environ.get('CH_USER', 'bgapp')
CH_PASS = os.environ.get('CH_PASSWORD',
                         '4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ')

_SUBJECT_TTL_S = 3600
_QUERY_TIMEOUT_S = 6

_lock = threading.Lock()
_state = {'client': None, 'subjects': None, 'subjects_ts': 0.0}


def _enabled():
    return os.environ.get('PM_PANEL_FACT_STORE', '1').strip() != '0'


def _client():
    with _lock:
        if _state['client'] is not None:
            return _state['client']
    import clickhouse_connect
    cl = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASS, connect_timeout=4,
        settings={'max_execution_time': _QUERY_TIMEOUT_S,
                  'readonly': 1})
    with _lock:
        _state['client'] = cl
    return cl


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def tracked_subjects():
    """{normalized name -> canonical profile_subject} for every
    subject the daily tracker covers. Cached an hour; [] on failure."""
    now = time.time()
    with _lock:
        if (_state['subjects'] is not None
                and now - _state['subjects_ts'] < _SUBJECT_TTL_S):
            return _state['subjects']
    out = {}
    try:
        rows = _client().query(
            'SELECT DISTINCT profile_subject '
            'FROM reference.v_iq_daily_metrics '
            'LIMIT 5000').result_rows
        for (name,) in rows:
            n = _norm(name)
            if len(n) >= 3:
                out[n] = str(name)
    except Exception as e:
        print(f"[panel-fact-store] subject index failed: {e}")
        with _lock:
            # Short negative cache so an outage never hammers the DB.
            _state['subjects'] = _state['subjects'] or {}
            _state['subjects_ts'] = now - _SUBJECT_TTL_S + 120
        return _state['subjects']
    with _lock:
        _state['subjects'] = out
        _state['subjects_ts'] = now
    return out


def match_tracked(text, extra_names=(), limit=3):
    """Canonical tracked subjects the question (or the base subject)
    names. Word-boundary containment on normalized text."""
    subs = tracked_subjects()
    if not subs:
        return []
    hay = ' ' + _norm(text) + ' '
    for nm in extra_names:
        hay += ' ' + _norm(nm) + ' '
    hits = []
    for n, canon in subs.items():
        if f' {n} ' in hay and canon not in hits:
            hits.append(canon)
    # Longest (most specific) names first; a question naming
    # "Taylor Swift" should not also drag in a tracked "Taylor".
    hits.sort(key=lambda c: -len(c))
    kept = []
    for c in hits:
        if any(_norm(c) in _norm(k) for k in kept):
            continue
        kept.append(c)
        if len(kept) >= limit:
            break
    return kept


def subject_series(subject, days=90):
    """Daily measured series for one tracked subject. List of dicts,
    newest last; [] on miss."""
    try:
        rows = _client().query(
            'SELECT snapshot_date, mentions, unique_uids, '
            '       cw_iq_score, net_sentiment '
            'FROM reference.v_iq_daily_metrics '
            'WHERE lower(profile_subject) = lower({subject:String}) '
            '  AND snapshot_date >= today() - {days:UInt16} '
            'ORDER BY snapshot_date LIMIT 400',
            parameters={'subject': str(subject),
                        'days': int(min(max(days, 7), 366))}
        ).result_rows
    except Exception as e:
        print(f"[panel-fact-store] series failed for {subject}: {e}")
        return []
    return [{'date': str(r[0]), 'mentions': int(r[1] or 0),
             'unique_uids': int(r[2] or 0),
             'score': float(r[3] or 0.0),
             'sentiment': float(r[4] or 0.0)} for r in rows]


def _fmt_int(v):
    return f"{int(v):,}"


def _series_line(subject, series):
    if not series:
        return ''
    # Tracker names carry underscores (Taylor_Jenkins_Reid); the
    # prompt shows the human spelling so a reply never echoes one.
    subject = str(subject).replace('_', ' ').strip()
    first, last = series[0], series[-1]
    n_days = len(series)
    mentions_total = sum(r['mentions'] for r in series)
    peak = max(series, key=lambda r: r['unique_uids'])
    direction = 'flat'
    if last['score'] - first['score'] > 1.0:
        direction = 'rising'
    elif first['score'] - last['score'] > 1.0:
        direction = 'cooling'
    return (
        f"- {subject} | {n_days} tracked days through {last['date']}: "
        f"{_fmt_int(mentions_total)} mentions total; unique people "
        f"peak {_fmt_int(peak['unique_uids'])}/day ({peak['date']}), "
        f"latest {_fmt_int(last['unique_uids'])}/day; engagement "
        f"score {first['score']:.1f} -> {last['score']:.1f} "
        f"({direction}); latest sentiment {last['sentiment']:+.2f}")


def measured_signals_block(text, extra_names=(), days=90):
    """Prompt block of measured daily-tracker signals for subjects the
    ask names, or ''. One cached index lookup plus at most 3 tiny
    aggregate queries; hard fail-safe."""
    if not _enabled():
        return ''
    try:
        names = match_tracked(text, extra_names=extra_names)
        if not names:
            return ''
        lines = []
        for nm in names:
            ln = _series_line(nm, subject_series(nm, days=days))
            if ln:
                lines.append(ln)
        if not lines:
            return ''
        return ('MEASURED DAILY SIGNALS (aggregate daily counts from '
                'the tracker for subjects this question names; '
                'measured values - quote them as stated and date any '
                'claim to the series end, never "today"):\n'
                + '\n'.join(lines))
    except Exception:
        traceback.print_exc()
        return ''


def top_movers(days=7, k=10):
    """Biggest engagement-score moves at the latest snapshot:
    [(subject, latest_score, prev_score, delta)]. Aggregate-only;
    [] on any failure. Feeds the proactive scanner."""
    if not _enabled():
        return []
    try:
        rows = _client().query(
            'WITH latest AS (SELECT max(snapshot_date) AS d '
            '                FROM reference.v_iq_daily_metrics) '
            'SELECT profile_subject, cw_iq_score, prev_cw_iq_score, '
            '       cw_iq_score - prev_cw_iq_score AS delta '
            'FROM reference.v_iq_daily_metrics '
            'WHERE snapshot_date = (SELECT d FROM latest) '
            '  AND prev_cw_iq_score > 0 '
            'ORDER BY abs(delta) DESC '
            'LIMIT {k:UInt8}',
            parameters={'k': int(min(max(k, 1), 25))}).result_rows
        return [(str(r[0]), float(r[1] or 0), float(r[2] or 0),
                 float(r[3] or 0)) for r in rows]
    except Exception as e:
        print(f"[panel-fact-store] top movers failed: {e}")
        return []
