"""Aggregate per-call Prometheus usage records for the admin surfaces.

Data source is the raw per-call records that render_usage_log.py writes to
`s3://dashboard-inputs/system/usage/render_calls/YYYY_MM_DD/`. Every call
carries `ts, surface, origin, model, cost_usd, user, user_email, duration_s`
already; `_pm_attrib_extras()` in app.py auto-attributes the logged-in user
on every Prometheus surface (interpret, analysis, deck, corpus_select,
ask_classify), so we can group by user without any new instrumentation.

This module is READ-ONLY. It never writes to S3, never mutates state, never
raises into a caller (every function catches at the boundary). It exposes:

    fetch_usage(user_emails=None, company_emails=None, days=30, ...)
        Scan the render_calls prefix for the trailing `days` days and
        return a summary dict for the requested user set. When called
        with `company_emails`, the summary rolls up the whole company
        AND breaks per-user rows out for the employee table.

    admin_prometheus_surfaces
        Ordered tuple of the Prometheus surfaces we roll up in the UI.

The scan is cached per (window_days, today_key) for 5 minutes so admin
polling doesn't hammer S3. Cache is per-process (fine for the admin
surface: even worst case each gunicorn worker only pays the scan once
per window per 5 min).

The API surface exposes a single cost field `cost_display` on every
returned dict. Per the 2026-09-04 pricing directive (Jenna), the number
that ships to the browser is our internal per-call cost multiplied by
`PROMETHEUS_MARKUP_MULTIPLIER` and rounded to cents. The raw internal
cost is never included in the returned payload - the browser only ever
sees the marked-up figure.

Copy in the returned dicts stays in the standing vocabulary: "Prometheus",
"actions", "reads", "analyses", "deck builds". No mention of models,
providers, queues, or workers ever crosses the wire to admin.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

S3_BUCKET = os.environ.get('RENDER_USAGE_BUCKET') or 'dashboard-inputs'
CALLS_PREFIX = 'system/usage/render_calls/'

# Surfaces every dashboard Prometheus call routes through. `partner_api`
# origin records are excluded from admin user rollups by construction
# (partners do not have admin users behind them).
PROMETHEUS_SURFACES = (
    'interpret',       # natural-language ask -> spec draft
    'analysis',        # on-screen profile analysis / read
    'deck',            # PPTX slide plan
    'corpus_select',   # pick the right base profile for an ask
    'ask_classify',    # route classification
)

# Public-facing labels + descriptions. Admin surface only, but we still
# keep the vocabulary clean (no "Claude", "queue", "worker", "Anthropic").
SURFACE_LABELS: Dict[str, str] = {
    'interpret': 'Interpret',
    'analysis': 'Analysis',
    'deck': 'Deck build',
    'corpus_select': 'Base match',
    'ask_classify': 'Route',
    'other': 'Other',
}
SURFACE_DESCRIPTIONS: Dict[str, str] = {
    'interpret': 'Natural-language ask turned into a build brief.',
    'analysis': 'On-screen profile analysis and read Q&A.',
    'deck': 'Slide plan built from an on-screen profile.',
    'corpus_select': 'Match an ask to the right base profile.',
    'ask_classify': 'Route an ask to the right flow.',
    'other': 'Other Prometheus action.',
}

# Default and cap for the rolling window the admin surface asks for.
DEFAULT_DAYS = 30
MAX_DAYS = 90

# Per-user recent-actions cap (avoid unbounded payloads for heavy users).
MAX_RECENT_ACTIONS_PER_USER = 50

# Displayed Prometheus cost = raw per-call cost x this multiplier, rounded
# to cents. Per Jenna 2026-09-04 pricing directive. The raw cost is
# retained inside this module for internal logging + reconciliation but
# is NEVER included in the returned payload; only the marked-up
# `cost_display` value crosses the wire.
PROMETHEUS_MARKUP_MULTIPLIER = 2.1

# In-process cache: (days, today_key) -> (fetched_at_epoch, raw_records).
# 5 minute TTL is plenty for an admin surface.
_CACHE_TTL_S = 300
_cache_lock = threading.Lock()
_cache: Dict[Tuple[int, str], Tuple[float, List[dict]]] = {}


admin_prometheus_surfaces = PROMETHEUS_SURFACES


# ---------------------------------------------------------------------------
# S3 fetch (cached)
# ---------------------------------------------------------------------------

def _s3_client():
    import boto3
    return boto3.client('s3')


def _day_keys(days: int) -> List[str]:
    """Return the YYYY_MM_DD prefixes we need to scan for the trailing window."""
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).strftime('%Y_%m_%d')
            for i in range(days)]


def _load_day(s3, day_key: str) -> List[dict]:
    """Load every per-call record for one day. Never raises."""
    prefix = f"{CALLS_PREFIX}{day_key}/"
    out: List[dict] = []
    try:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents') or []:
                try:
                    body = s3.get_object(
                        Bucket=S3_BUCKET, Key=obj['Key'])['Body'].read()
                    rec = json.loads(body)
                except Exception:
                    continue
                # Only Prometheus records are of interest: origin=chatbot
                # (partner_api origin is excluded from admin surfaces).
                if str(rec.get('origin') or '') != 'chatbot':
                    continue
                out.append(rec)
    except Exception as exc:
        # Bucket unreachable / permission error: return an empty day
        # rather than propagate; the admin surface stays usable.
        try:
            print(f"[prometheus-usage-admin] day {day_key} unreadable: {exc}")
        except Exception:
            pass
    return out


def _load_window(days: int) -> List[dict]:
    """Cached scan of the trailing `days` days of render_calls records."""
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    today_key = datetime.now(timezone.utc).date().isoformat()
    cache_key = (days, today_key)
    now = time.time()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (now - entry[0]) < _CACHE_TTL_S:
            return entry[1]
    s3 = _s3_client()
    records: List[dict] = []
    for day_key in _day_keys(days):
        records.extend(_load_day(s3, day_key))
    with _cache_lock:
        _cache[cache_key] = (now, records)
        # Evict any entries whose today_key rolled over.
        for k in list(_cache.keys()):
            if k[1] != today_key:
                _cache.pop(k, None)
    return records


def _norm_label(rec: dict) -> str:
    """Case-normalized user identity for grouping. user_email preferred,
    then user, else empty string."""
    lbl = (rec.get('user_email') or rec.get('user') or '')
    return str(lbl).strip().lower()


def _norm_email_set(emails: Optional[Iterable[str]]) -> Set[str]:
    if not emails:
        return set()
    return {str(e).strip().lower() for e in emails if e}


def _cost_display(raw_cost: float) -> float:
    """Return the browser-visible Prometheus cost: raw x markup, cents.

    Internal-only helper. Never expose the raw value or the multiplier
    itself to any caller of this module; every returned dict carries
    only the display figure.
    """
    try:
        return round(float(raw_cost or 0.0) * PROMETHEUS_MARKUP_MULTIPLIER, 2)
    except (TypeError, ValueError):
        return 0.0


def _empty_surface_map() -> Dict[str, dict]:
    """Return a fresh per-surface accumulator dict.

    `cost_usd_raw` is internal-only and stripped before the map is
    returned to callers. `duration_s` and `actions` are the only other
    accumulators."""
    return {s: {'actions': 0, 'cost_usd_raw': 0.0, 'duration_s': 0.0,
                'last_at': None}
            for s in PROMETHEUS_SURFACES + ('other',)}


def _classify_surface(raw: str) -> str:
    """Map a raw surface tag to a known Prometheus label, or 'other'."""
    s = str(raw or '').strip().lower()
    if s in PROMETHEUS_SURFACES:
        return s
    return 'other'


def _bump_surface(acc: Dict[str, dict], rec: dict) -> None:
    key = _classify_surface(rec.get('surface'))
    slot = acc[key]
    slot['actions'] += 1
    try:
        slot['cost_usd_raw'] += float(rec.get('cost_usd') or 0.0)
    except (TypeError, ValueError):
        pass
    try:
        d = rec.get('duration_s')
        if d is not None:
            slot['duration_s'] += float(d)
    except (TypeError, ValueError):
        pass
    ts = str(rec.get('ts') or '')
    if ts and (slot['last_at'] is None or ts > slot['last_at']):
        slot['last_at'] = ts


def _round_surface_map(m: Dict[str, dict]) -> Dict[str, dict]:
    """Convert the internal accumulator map into the payload shape.

    Strips `cost_usd_raw`; emits only `cost_display` (marked-up)."""
    out: Dict[str, dict] = {}
    for k, v in m.items():
        out[k] = {
            'actions': int(v['actions']),
            'cost_display': _cost_display(v['cost_usd_raw']),
            'duration_s': round(float(v['duration_s']), 2),
            'last_at': v['last_at'],
            'label': SURFACE_LABELS.get(k, k.title()),
        }
    return out


def _sum_totals(m: Dict[str, dict]) -> Dict[str, Any]:
    """Roll up the accumulator map into a single totals dict.

    Emits only `cost_display` (marked-up). Raw cost stays internal."""
    actions = 0
    raw_cost = 0.0
    duration = 0.0
    last = None
    for v in m.values():
        actions += int(v['actions'])
        raw_cost += float(v['cost_usd_raw'])
        duration += float(v['duration_s'])
        if v['last_at'] and (last is None or v['last_at'] > last):
            last = v['last_at']
    return {'actions': actions,
            'cost_display': _cost_display(raw_cost),
            'duration_s': round(duration, 2),
            'last_at': last}


def _record_row(rec: dict) -> dict:
    """One trimmed row for the recent-actions list. Vocabulary-safe.

    Per-call `cost_display` = per-call raw x markup, so a caller
    summing every recent_action's cost_display reconciles with the
    totals dict (rounding is per-call rather than per-sum, so the
    sum may drift by a few cents on very large windows - that is
    expected and preferred over exposing raw cost)."""
    surface = _classify_surface(rec.get('surface'))
    try:
        cost_display = _cost_display(rec.get('cost_usd') or 0.0)
    except (TypeError, ValueError):
        cost_display = 0.0
    try:
        duration = rec.get('duration_s')
        duration = round(float(duration), 2) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    return {
        'ts': str(rec.get('ts') or ''),
        'surface': surface,
        'surface_label': SURFACE_LABELS.get(surface, surface.title()),
        'cost_display': cost_display,
        'duration_s': duration,
        'user_email': (rec.get('user_email') or rec.get('user') or ''),
    }


def _extract_day_key(rec: dict) -> str:
    """Return the YYYY-MM-DD day this record belongs to (UTC).

    Falls back to '' if the ts is missing / unparseable."""
    ts = str(rec.get('ts') or '')
    if not ts:
        return ''
    # ts is typically ISO-8601 with a Z or offset suffix. Take the first
    # 10 chars if they parse as a date; otherwise try full parse.
    head = ts[:10]
    if len(head) == 10 and head[4] == '-' and head[7] == '-':
        return head
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc).date().isoformat()
    except Exception:
        return ''


def _build_by_day(records: Iterable[dict],
                  labels: Set[str]) -> List[dict]:
    """Return the per-day cost + call breakdown for the given user labels.

    Only days with at least one call are included; empty days are
    dropped so the daily log stays compact. Sorted most-recent-first.
    Each entry is `{date, cost_display, calls}`.
    """
    per_day_raw: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        if labels and _norm_label(rec) not in labels:
            continue
        day = _extract_day_key(rec)
        if not day:
            continue
        slot = per_day_raw.setdefault(day, {'raw': 0.0, 'calls': 0})
        try:
            slot['raw'] += float(rec.get('cost_usd') or 0.0)
        except (TypeError, ValueError):
            pass
        slot['calls'] += 1
    out: List[dict] = []
    for day, slot in per_day_raw.items():
        out.append({
            'date': day,
            'cost_display': _cost_display(slot['raw']),
            'calls': int(slot['calls']),
        })
    out.sort(key=lambda r: r['date'], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_user_usage(user_emails: Iterable[str],
                     days: int = DEFAULT_DAYS,
                     include_actions: bool = True) -> Dict[str, Any]:
    """Roll up Prometheus usage for one user across the trailing window.

    `user_emails` is the set of case-normalized labels that identify this
    user (typically {email, username}). Returns:

        {
          'days': int,
          'from': ISO date,
          'to':   ISO date,
          'totals':      {actions, cost_display, duration_s, last_at},
          'by_surface':  {surface: {actions, cost_display, duration_s,
                                     last_at, label}},
          'by_day':      [{date, cost_display, calls}, ...],
                          # most-recent-first, empty days dropped
          'active_days': int,   # len(by_day)
          'recent_actions': [{ts, surface, surface_label, cost_display,
                               duration_s, user_email}, ...],
          'surface_descriptions': {surface: str},
        }

    Every cost field is `cost_display` (marked-up per
    PROMETHEUS_MARKUP_MULTIPLIER, 2dp). The raw per-call cost is not
    returned.

    Never raises."""
    labels = _norm_email_set(user_emails)
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    try:
        records = _load_window(days)
    except Exception:
        records = []

    surface_acc = _empty_surface_map()
    recent: List[dict] = []
    for rec in records:
        if _norm_label(rec) not in labels:
            continue
        _bump_surface(surface_acc, rec)
        if include_actions:
            recent.append(_record_row(rec))

    recent.sort(key=lambda r: r['ts'], reverse=True)
    if len(recent) > MAX_RECENT_ACTIONS_PER_USER:
        recent = recent[:MAX_RECENT_ACTIONS_PER_USER]

    by_day = _build_by_day(records, labels)

    today = datetime.now(timezone.utc).date()
    return {
        'days': days,
        'from': (today - timedelta(days=days - 1)).isoformat(),
        'to': today.isoformat(),
        'totals': _sum_totals(surface_acc),
        'by_surface': _round_surface_map(surface_acc),
        'by_day': by_day,
        'active_days': len(by_day),
        'recent_actions': recent,
        'surface_descriptions': dict(SURFACE_DESCRIPTIONS),
    }


def fetch_company_usage(user_map: Dict[str, List[str]],
                        days: int = DEFAULT_DAYS) -> Dict[str, Any]:
    """Roll up Prometheus usage for a whole company.

    `user_map` is `{username: [labels]}` where each label list is the
    email + username the caller is willing to accept as a match for that
    user (typically the same shape the caller uses for fetch_user_usage).

    Returns:

        {
          'days': int, 'from': str, 'to': str,
          'totals': {actions, cost_display, duration_s, last_at},
          'by_surface': {...},
          'by_user': [
              {'username': str, 'totals': {...}, 'by_surface': {...}},
              ...   # one row per known user, sorted by cost desc
          ],
          'unattributed': {actions, cost_display, duration_s, last_at},
              # Company-matching records with no attributed user label.
        }

    Every cost field is `cost_display` (marked-up, 2dp). Raw internal
    cost is never included.

    "unattributed" is always {actions:0, ...} in normal operation
    (every Prometheus call carries user attribution since 2026-09-02).
    Kept for defense so the numbers always reconcile.

    Never raises.
    """
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    try:
        records = _load_window(days)
    except Exception:
        records = []

    # label -> username lookup. Case + whitespace normalized to match
    # _norm_label. A label collision across two usernames (rare, only
    # if two accounts share the same email) picks the first one; the
    # collision itself is not treated as an error.
    label_to_user: Dict[str, str] = {}
    per_user: Dict[str, Dict[str, dict]] = {}
    for username, labels in (user_map or {}).items():
        norm = _norm_email_set(labels)
        for lbl in norm:
            if lbl and lbl not in label_to_user:
                label_to_user[lbl] = username
        per_user[username] = _empty_surface_map()

    company_acc = _empty_surface_map()
    unattributed = _empty_surface_map()
    company_labels = set(label_to_user.keys())

    for rec in records:
        lbl = _norm_label(rec)
        if lbl not in company_labels:
            continue
        _bump_surface(company_acc, rec)
        username = label_to_user.get(lbl)
        if username:
            _bump_surface(per_user[username], rec)
        else:
            _bump_surface(unattributed, rec)

    by_user = []
    for username, acc in per_user.items():
        totals = _sum_totals(acc)
        by_user.append({
            'username': username,
            'totals': totals,
            'by_surface': _round_surface_map(acc),
        })
    # Sort by cost desc (biggest spenders first), tiebreak by username.
    by_user.sort(key=lambda r: (-float(r['totals']['cost_display']),
                                r['username'].lower()))

    today = datetime.now(timezone.utc).date()
    return {
        'days': days,
        'from': (today - timedelta(days=days - 1)).isoformat(),
        'to': today.isoformat(),
        'totals': _sum_totals(company_acc),
        'by_surface': _round_surface_map(company_acc),
        'by_user': by_user,
        'unattributed': _sum_totals(unattributed),
        'surface_descriptions': dict(SURFACE_DESCRIPTIONS),
    }


def fetch_all_users_counts(
        label_index: Dict[str, str],
        days: int = DEFAULT_DAYS) -> Dict[str, int]:
    """Return {username: action_count} for every user with any Prometheus
    activity in the window.

    `label_index` is `{lower_label: username}` so a single scan can
    attribute every record without doing a per-user pass. Callers should
    build this from the users file once and pass it in. Never raises."""
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    try:
        records = _load_window(days)
    except Exception:
        records = []
    counts: Dict[str, int] = defaultdict(int)
    for rec in records:
        lbl = _norm_label(rec)
        if not lbl:
            continue
        username = label_index.get(lbl)
        if username:
            counts[username] += 1
    return dict(counts)


def fetch_all_users_summary(
        label_index: Dict[str, str],
        days: int = DEFAULT_DAYS) -> Dict[str, Dict[str, Any]]:
    """Return `{username: {actions, cost_display}}` for every user with any
    Prometheus activity in the window.

    Same one-pass shape as `fetch_all_users_counts` but also carries
    the marked-up cost so a company rollup can display a dollar total
    without a second S3 scan. Raw cost is never exposed - only
    `cost_display`. Never raises."""
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    try:
        records = _load_window(days)
    except Exception:
        records = []
    per_user_raw: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {'actions': 0, 'raw': 0.0})
    for rec in records:
        lbl = _norm_label(rec)
        if not lbl:
            continue
        username = label_index.get(lbl)
        if not username:
            continue
        slot = per_user_raw[username]
        slot['actions'] += 1
        try:
            slot['raw'] += float(rec.get('cost_usd') or 0.0)
        except (TypeError, ValueError):
            pass
    return {u: {'actions': int(v['actions']),
                'cost_display': _cost_display(v['raw'])}
            for u, v in per_user_raw.items()}


def clear_cache() -> None:
    """Test hook: drop the in-process cache so unit tests see fresh data."""
    with _cache_lock:
        _cache.clear()


__all__ = [
    'PROMETHEUS_SURFACES',
    'PROMETHEUS_MARKUP_MULTIPLIER',
    'SURFACE_LABELS',
    'SURFACE_DESCRIPTIONS',
    'DEFAULT_DAYS',
    'MAX_DAYS',
    'admin_prometheus_surfaces',
    'fetch_user_usage',
    'fetch_company_usage',
    'fetch_all_users_counts',
    'fetch_all_users_summary',
    'clear_cache',
]
