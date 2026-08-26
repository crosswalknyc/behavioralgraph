"""Prometheus access tiers + pay-as-you-go usage billing (2026-08-26).

Jenna's mandate, verbatim: "add two options on promethus access in the
user adim. one is that users can use it to only pull profile IQs and
subscriber IQs. One that is analysis and everything else. if analysis
is turned off you can still show the analyze the data but give a
message to the user that says 'Not subscribed to this feature. Turn it
on for pay as you go?' and they can select yes at which point you start
tracking usage cost to us and allocate that usage to that user and send
an email to me, jessie, liz that says pay per use started for x user.
and then when they finish taht session email the cost of that session."

Pricing mandate: "we need to have a 110% markup on what we are charged
from anthropic" - billed = our cost x 2.10 (PPU_MARKUP lives in
render_usage_log next to the cost math).

The two tiers, stored per user in system/users.json:

    prometheus_access: 'pulls_only' | 'full'
    pay_per_use_enabled: bool          (set by the user's own Yes)
    pay_per_use_started_at: ISO ts     (read-only indicator in admin)

DEFAULT IS 'full': a missing / empty / unrecognized value resolves to
'full', so every existing user keeps exactly the behavior they have
today. Only an admin explicitly selecting "Pulls only" changes
anything for a user.

'pulls_only' allows the build/pull routes (interpret, clarify,
approve, status, history, active-runs, health - Profile IQ and
Subscriber IQ builds) and blocks the analysis surfaces (page-aware
analysis, search-demand reads, reasoned metrics, cross-module
enrichment, deck builds) until the user opts into pay as you go.
The opt-in persists until an admin changes their tier; changing
prometheus_access in the admin resets the opt-in.

Session semantics: a session is contiguous Prometheus analysis
activity for one pay-per-use user; 30 minutes of inactivity (or
logout) ends it. Attribution rows land in S3 (one object per model
call, mirrored to a pay-per-use prefix), so the sweep below - which
runs in every gunicorn worker - reads the shared S3 truth, claims each
closed session with a conditional-put idempotency stamp, and emails
the summary exactly once no matter how many workers sweep in parallel.
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import render_usage_log

ACCESS_PULLS_ONLY = 'pulls_only'
ACCESS_FULL = 'full'

# Jenna's exact user-facing copy. Do not edit without a new mandate.
OFFER_MESSAGE = 'Not subscribed to this feature. Turn it on for pay as you go?'

# Session = contiguous analysis activity; this much quiet ends it.
SESSION_IDLE_S = 30 * 60

# Where the sweep's idempotency stamps (one per closed session) live.
SESSIONS_PREFIX = 'system/usage/ppu_sessions/'

EMAIL_TO = ('jenna@crosswalknyc.com', 'jessie@crosswalknyc.com',
            'liz@crosswalknyc.com')
EMAIL_SOURCE = 'BehavioralGraph <jenna@crosswalknyc.com>'
AWS_REGION = 'us-east-2'


# ---------------------------------------------------------------------------
# Tier resolution (pure functions; app.py routes call these)
# ---------------------------------------------------------------------------

def resolve_access(user) -> tuple:
    """(tier, pay_per_use_enabled) for a users.json record.

    Anything that is not exactly 'pulls_only' resolves to 'full' -
    existing users have no prometheus_access field and stay 'full'
    with zero behavior change."""
    u = user or {}
    tier = str(u.get('prometheus_access') or '').strip().lower()
    if tier != ACCESS_PULLS_ONLY:
        tier = ACCESS_FULL
    return tier, bool(u.get('pay_per_use_enabled'))


def analysis_allowed(user) -> bool:
    """Whether this user may run the analysis surfaces right now.

    super_admin and 'full' tier: always. 'pulls_only': only after the
    user's own pay-as-you-go Yes (their usage is then billed)."""
    if (user or {}).get('role') == 'super_admin':
        return True
    tier, ppu = resolve_access(user)
    return tier == ACCESS_FULL or ppu


def billing_active(user) -> bool:
    """True when this user's analysis calls are billed pay-as-you-go:
    pulls_only tier with the opt-in flag set. Full-tier users are
    subscribed; their calls are never billed per use."""
    if (user or {}).get('role') == 'super_admin':
        return False
    tier, ppu = resolve_access(user)
    return tier == ACCESS_PULLS_ONLY and ppu


# ---------------------------------------------------------------------------
# In-process session ids (attribution metadata on usage rows)
# ---------------------------------------------------------------------------
# The sweep derives canonical sessions from row timestamps alone, so
# these ids only need to be stable within one worker's view of a
# session - they are attribution metadata, not the grouping key.

_sessions_lock = threading.Lock()
_session_map = {}   # email -> {'id': str, 'started': epoch, 'last': epoch}


def touch_session(email: str, now: float = None) -> str:
    """Return the active session id for this user, starting a new one
    after SESSION_IDLE_S of quiet (or after note_logout)."""
    email = (email or '').strip().lower()
    if not email:
        return ''
    now = time.time() if now is None else float(now)
    with _sessions_lock:
        s = _session_map.get(email)
        if not s or (now - s['last']) > SESSION_IDLE_S:
            stamp = datetime.fromtimestamp(now, tz=timezone.utc)
            s = {'id': (stamp.strftime('%Y%m%d%H%M%S') + '-'
                        + uuid.uuid4().hex[:8]),
                 'started': now}
        s['last'] = now
        _session_map[email] = s
        return s['id']


def peek_session(email: str) -> str:
    """Current session id for a user WITHOUT starting or extending
    one. '' when this process has no live session for them."""
    email = (email or '').strip().lower()
    with _sessions_lock:
        s = _session_map.get(email)
        return s['id'] if s else ''


def note_logout(email: str) -> bool:
    """Drop the in-process session for a user on logout. Returns True
    when a session existed (the caller then writes the logout marker
    row so the sweep can close the session immediately)."""
    email = (email or '').strip().lower()
    with _sessions_lock:
        return _session_map.pop(email, None) is not None


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def _fmt_ts(ts: str) -> str:
    """'2026-08-26T18:02:11Z' -> '2026-08-26 18:02 UTC'."""
    t = str(ts or '')
    if 'T' in t:
        return t[:16].replace('T', ' ') + ' UTC'
    return t


def build_start_email(display_name: str, email: str, ts: str) -> tuple:
    """(subject, body) for the opt-in notification to Jenna/Jessie/Liz."""
    who = (display_name or '').strip() or (email or '').strip() or 'a user'
    subject = f"Pay per use started for {who}"
    body = (
        f"Pay per use started for {who} ({email}).\n\n"
        f"Started: {_fmt_ts(ts)}\n\n"
        "This user turned on pay as you go for the analysis features. "
        "Their analysis usage is now tracked per session and allocated "
        "to them; a cost summary for each session arrives when the "
        "session ends.\n"
    )
    return subject, body


def build_session_email(summary: dict) -> tuple:
    """(subject, body) for one closed session's cost summary."""
    who = (summary.get('user') or '').strip() \
        or (summary.get('user_email') or '').strip() or 'user'
    email = summary.get('user_email') or ''
    cost = float(summary.get('cost_usd') or 0.0)
    billed = float(summary.get('billed_usd') or 0.0)
    subject = f"Pay per use session cost: {who} ${billed:,.2f}"
    body = (
        f"Pay per use session summary for {who} ({email}).\n\n"
        f"Session start: {_fmt_ts(summary.get('session_start'))}\n"
        f"Session end:   {_fmt_ts(summary.get('session_end'))}\n"
        f"Asks: {int(summary.get('asks') or 0)}\n\n"
        f"Our cost: ${cost:,.2f}\n"
        f"Billed (2.10x): ${billed:,.2f}\n"
    )
    return subject, body


def send_email(subject: str, body: str, to=None) -> bool:
    """SES send on the established sender. Never raises."""
    try:
        import boto3
        ses = boto3.client('ses', region_name=AWS_REGION)
        ses.send_email(
            Source=EMAIL_SOURCE,
            Destination={'ToAddresses': list(to or EMAIL_TO)},
            Message={'Subject': {'Data': subject},
                     'Body': {'Text': {'Data': body}}})
        print(f"[pay-per-use] sent {subject!r}")
        return True
    except Exception as e:
        print(f"[pay-per-use] SES send failed: {e}")
        return False


def send_start_email_async(display_name: str, email: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    subject, body = build_start_email(display_name, email, ts)
    threading.Thread(target=send_email, args=(subject, body),
                     daemon=True).start()


# ---------------------------------------------------------------------------
# Session sweep (closed-session detection + exactly-once summary email)
# ---------------------------------------------------------------------------

_s3 = None
_s3_lock = threading.Lock()
_row_cache = {}          # s3 key -> parsed row (ppu rows are immutable)
_row_cache_lock = threading.Lock()


def _client():
    global _s3
    with _s3_lock:
        if _s3 is None:
            import boto3
            _s3 = boto3.client('s3')
        return _s3


def _list_ppu_rows(s3, now_dt) -> list:
    """All pay-per-use usage rows from the trailing 3 UTC days.
    Bodies are cached per key (rows never change once written)."""
    rows = []
    for back in range(3):
        day = (now_dt - timedelta(days=back)).strftime('%Y_%m_%d')
        prefix = f"{render_usage_log.PPU_CALLS_PREFIX}{day}/"
        try:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(
                    Bucket=render_usage_log.S3_BUCKET, Prefix=prefix):
                for obj in page.get('Contents') or []:
                    key = obj['Key']
                    with _row_cache_lock:
                        cached = _row_cache.get(key)
                    if cached is not None:
                        rows.append(cached)
                        continue
                    try:
                        body = s3.get_object(
                            Bucket=render_usage_log.S3_BUCKET,
                            Key=key)['Body'].read()
                        row = json.loads(body)
                    except Exception:
                        continue
                    with _row_cache_lock:
                        _row_cache[key] = row
                        if len(_row_cache) > 5000:
                            _row_cache.clear()
                    rows.append(row)
        except Exception as e:
            print(f"[pay-per-use] list {prefix} failed: {e}")
    return rows


def _parse_ts(ts: str) -> float:
    try:
        return datetime.strptime(
            str(ts), '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


def _sessions_from_rows(rows: list) -> list:
    """Group usage rows into per-user sessions. A gap longer than
    SESSION_IDLE_S (or a logout marker) splits sessions."""
    by_user = {}
    for r in rows:
        email = str(r.get('user_email') or '').strip().lower()
        if not email:
            continue
        by_user.setdefault(email, []).append(r)
    sessions = []
    for email, urows in by_user.items():
        urows.sort(key=lambda r: str(r.get('ts') or ''))
        cur = None
        for r in urows:
            t = _parse_ts(r.get('ts'))
            if cur is None or (t - cur['last_epoch']) > SESSION_IDLE_S \
                    or cur.get('ended_by_logout'):
                cur = {'user_email': email,
                       'user': str(r.get('user') or ''),
                       'start_ts': str(r.get('ts') or ''),
                       'start_epoch': t,
                       'rows': [],
                       'ended_by_logout': False}
                sessions.append(cur)
            cur['last_epoch'] = t
            cur['end_ts'] = str(r.get('ts') or '')
            if r.get('logout'):
                cur['ended_by_logout'] = True
            else:
                cur['rows'].append(r)
                if not cur.get('user') and r.get('user'):
                    cur['user'] = str(r.get('user'))
    return [s for s in sessions if s['rows']]


def _summarize(sess: dict) -> dict:
    rows = sess['rows']
    cost = sum(float(r.get('cost_usd') or 0.0) for r in rows)
    billed = sum(float(r.get('billed_usd') or 0.0) for r in rows)
    req_ids = {str(r.get('request_id') or '') for r in rows
               if r.get('request_id')}
    return {
        'user': sess.get('user') or '',
        'user_email': sess['user_email'],
        'session_start': sess['start_ts'],
        'session_end': sess['end_ts'],
        'asks': len(req_ids) or len(rows),
        'calls': len(rows),
        'cost_usd': round(cost, 6),
        'billed_usd': round(billed, 6),
    }


def _stamp_key(sess: dict) -> str:
    """Deterministic idempotency-stamp key for one session. Keyed on
    user + session START only: the start of a closed session is
    stable, so parallel sweepers (and a late-landing tail row that
    would move the end) all resolve to the same stamp."""
    day = sess['start_ts'][:10].replace('-', '_') or 'unknown'
    slug = hashlib.sha1(
        sess['user_email'].encode('utf-8')).hexdigest()[:10]
    start_compact = (sess['start_ts'][11:19] or '000000').replace(':', '')
    return f"{SESSIONS_PREFIX}{day}/{slug}_{start_compact}.json"


def _claim_stamp(s3, key: str, summary: dict) -> bool:
    """Conditional-create the stamp; True only for the single winner.
    On botocore too old for IfNoneMatch, degrade to a head-then-put
    (the same graceful posture as s3_json_state)."""
    body = json.dumps({**summary,
                       'emailed_at': datetime.now(timezone.utc).strftime(
                           '%Y-%m-%dT%H:%M:%SZ')}).encode('utf-8')
    try:
        s3.put_object(Bucket=render_usage_log.S3_BUCKET, Key=key,
                      Body=body, ContentType='application/json',
                      IfNoneMatch='*')
        return True
    except Exception as e:
        name = type(e).__name__
        code = ''
        try:
            code = str(e.response['Error']['Code'])
        except Exception:
            pass
        if code in ('PreconditionFailed', '412', 'ConditionalRequestConflict'):
            return False
        if name in ('ParamValidationError', 'TypeError') \
                or 'IfNoneMatch' in str(e):
            try:
                s3.head_object(Bucket=render_usage_log.S3_BUCKET, Key=key)
                return False
            except Exception:
                try:
                    s3.put_object(Bucket=render_usage_log.S3_BUCKET,
                                  Key=key, Body=body,
                                  ContentType='application/json')
                    return True
                except Exception as e2:
                    print(f"[pay-per-use] stamp put failed: {e2}")
                    return False
        print(f"[pay-per-use] stamp claim failed: {e}")
        return False


def sweep_closed_sessions(now: float = None, s3=None, send=None) -> list:
    """Close idle pay-per-use sessions and email each summary exactly
    once. Reads the shared S3 usage rows (the source of truth), so
    any worker can run this; the conditional-put stamp guarantees a
    session is claimed - and emailed - by exactly one sweeper.

    Returns the summaries THIS call emailed (empty on repeat runs)."""
    now = time.time() if now is None else float(now)
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    s3 = s3 or _client()
    send = send or (lambda subj, body: send_email(subj, body))
    emailed = []
    try:
        rows = _list_ppu_rows(s3, now_dt)
        for sess in _sessions_from_rows(rows):
            closed = sess.get('ended_by_logout') \
                or (now - sess['last_epoch']) > SESSION_IDLE_S
            if not closed:
                continue
            summary = _summarize(sess)
            if not _claim_stamp(s3, _stamp_key(sess), summary):
                continue
            subject, body = build_session_email(summary)
            try:
                send(subject, body)
            except Exception as e:
                print(f"[pay-per-use] summary email failed: {e}")
            emailed.append(summary)
    except Exception as e:
        print(f"[pay-per-use] sweep failed: {e}")
    return emailed


_sweeper_started = threading.Event()


def start_sweeper(interval_s: int = 300) -> bool:
    """Start the background sweep thread once per process. Every
    worker runs one; the stamps keep emails exactly-once anyway."""
    if _sweeper_started.is_set():
        return False
    _sweeper_started.set()

    def _loop():
        # Spread workers out so their sweeps do not align.
        time.sleep(random.uniform(20, 90))
        while True:
            try:
                sweep_closed_sessions()
            except Exception:
                pass
            time.sleep(interval_s + random.uniform(0, 30))

    threading.Thread(target=_loop, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Admin rollup (super_admin Daily Spend view)
# ---------------------------------------------------------------------------

def load_session_rollup(s3=None, days: int = 45) -> list:
    """Closed pay-per-use sessions grouped per (day, user):
    [{'date', 'user', 'user_email', 'sessions', 'asks',
      'cost_usd', 'billed_usd'}], newest day first."""
    s3 = s3 or _client()
    now_dt = datetime.now(timezone.utc)
    agg = {}
    for back in range(max(1, int(days))):
        day_dt = now_dt - timedelta(days=back)
        prefix = f"{SESSIONS_PREFIX}{day_dt.strftime('%Y_%m_%d')}/"
        try:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(
                    Bucket=render_usage_log.S3_BUCKET, Prefix=prefix):
                for obj in page.get('Contents') or []:
                    try:
                        body = s3.get_object(
                            Bucket=render_usage_log.S3_BUCKET,
                            Key=obj['Key'])['Body'].read()
                        st = json.loads(body)
                    except Exception:
                        continue
                    date = str(st.get('session_start') or '')[:10]
                    email = str(st.get('user_email') or '')
                    who = str(st.get('user') or '') or email
                    k = (date, email)
                    row = agg.setdefault(k, {
                        'date': date, 'user': who, 'user_email': email,
                        'sessions': 0, 'asks': 0,
                        'cost_usd': 0.0, 'billed_usd': 0.0})
                    row['sessions'] += 1
                    row['asks'] += int(st.get('asks') or 0)
                    row['cost_usd'] += float(st.get('cost_usd') or 0.0)
                    row['billed_usd'] += float(st.get('billed_usd') or 0.0)
        except Exception as e:
            print(f"[pay-per-use] rollup list {prefix} failed: {e}")
    rows = sorted(agg.values(),
                  key=lambda r: (r['date'], r['user']), reverse=True)
    for r in rows:
        r['cost_usd'] = round(r['cost_usd'], 2)
        r['billed_usd'] = round(r['billed_usd'], 2)
    return rows


__all__ = [
    'ACCESS_PULLS_ONLY', 'ACCESS_FULL', 'OFFER_MESSAGE', 'SESSION_IDLE_S',
    'resolve_access', 'analysis_allowed', 'billing_active',
    'touch_session', 'peek_session', 'note_logout',
    'build_start_email', 'build_session_email', 'send_email',
    'send_start_email_async', 'sweep_closed_sessions', 'start_sweeper',
    'load_session_rollup',
]
