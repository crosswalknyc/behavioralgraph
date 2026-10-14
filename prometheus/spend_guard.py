"""Per-user daily spend cap on Prometheus model work (audit item 8,
Jenna 2026-10-14).

Every model call the chat surfaces make is recorded with the user it
served (render_usage_log.record_call). This module keeps a running
total per user per UTC day: an in-process accumulator flushed every
thirty seconds to system/ops/pm_spend/<day>/<user>/<instance>.json, and a
reader that sums a user's documents (cached a minute) before any model
surface runs. Over the cap, the ask gets one honest line and the
deterministic lanes still answer; admins and unlimited seats are exempt.

The cap: PM_DAILY_SPEND_CAP_USD (env) or pricing.json's
prometheus_daily_cap_usd, else $400. Never raises.
"""
from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time

PREFIX = 'system/ops/pm_spend/'
DEFAULT_CAP_USD = 400.0
FLUSH_EVERY_S = 30
_acc = collections.Counter()      # (day, user) -> usd this process
_lock = threading.Lock()
_cache = {}                        # user -> (ts, usd_today)
_INSTANCE = f"{socket.gethostname()[:24]}-{os.getpid()}"
_flusher = False


def _day():
    return time.strftime('%Y-%m-%d', time.gmtime())


def cap_usd():
    try:
        v = float(os.environ.get('PM_DAILY_SPEND_CAP_USD') or 0)
        if v > 0:
            return v
    except ValueError:
        pass
    try:   # pricing.json through the host's S3, cached (never import the wallet module here: it boots the app)
        now = time.time()
        hit = _cache.get('__cap__')
        if hit and now - hit[0] < 600:
            return hit[1]
        from .host import host as _h
        pr = json.loads(_h.s3_client.get_object(Bucket=_h.bucket, Key='system/pricing.json')['Body'].read()) or {}
        v = float(pr.get('prometheus_daily_cap_usd') or 0) or DEFAULT_CAP_USD
        _cache['__cap__'] = (now, v)
        return v
    except Exception:
        return DEFAULT_CAP_USD


def add(user, usd):
    """Count one call's cost against the user's day."""
    global _flusher
    try:
        u = str(user or '').strip().lower()
        v = float(usd or 0)
        if not u or v <= 0:
            return
        with _lock:
            _acc[(_day(), u)] += v
        if not _flusher:
            _flusher = True
            threading.Thread(target=_flush_loop, daemon=True, name='pm-spend-flush').start()
    except Exception:
        pass


def flush():
    try:
        with _lock:
            items = dict(_acc)
        if not items:
            return 0
        from .host import host as _h
        n = 0
        for (day, u), v in items.items():
            _h.s3_client.put_object(Bucket=_h.bucket, Key=f"{PREFIX}{day}/{u}/{_INSTANCE}.json",
                                    Body=json.dumps({'day': day, 'user': u, 'instance': _INSTANCE, 'usd': round(v, 4),
                                                     'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}).encode('utf-8'),
                                    ContentType='application/json')
            n += 1
        return n
    except Exception:
        return 0


def _flush_loop():
    while True:
        try:
            time.sleep(FLUSH_EVERY_S)
            flush()
        except Exception:
            pass


def today(user, s3=None, bucket='dashboard-inputs', ttl_s=60):
    """The user's spend so far today across processes (USD)."""
    u = str(user or '').strip().lower()
    if not u:
        return 0.0
    now = time.time()
    hit = _cache.get(u)
    if hit and now - hit[0] < ttl_s:
        base = hit[1]
    else:
        base = 0.0
        try:
            if s3 is None:
                from .host import host as _h
                s3, bucket = _h.s3_client, _h.bucket
            r = s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIX}{_day()}/{u}/")
            for o in r.get('Contents') or []:
                if o['Key'].endswith(f"{_INSTANCE}.json"):
                    continue   # this process's share is added live below
                try:
                    base += float(json.loads(s3.get_object(Bucket=bucket, Key=o['Key'])['Body'].read()).get('usd') or 0)
                except Exception:
                    pass
        except Exception:
            base = 0.0
        _cache[u] = (now, base)
    with _lock:
        live = _acc.get((_day(), u), 0.0)
    return round(base + live, 4)


def exempt(user):
    u = user if isinstance(user, dict) else {}
    return str(u.get('role') or '') in ('super_admin', 'admin') or bool(u.get('unlimited'))


def check(user):
    """(allowed, spent_usd, cap_usd). Allowed is True under the cap or
    for exempt seats."""
    try:
        if exempt(user):
            return True, 0.0, cap_usd()
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip().lower()
        spent = today(uname)
        cap = cap_usd()
        return spent < cap, spent, cap
    except Exception:
        return True, 0.0, DEFAULT_CAP_USD


def over_cap_reply(spent, cap):
    return (f"You have reached today's processing limit (${cap:,.0f} of model work; ${spent:,.0f} used). "
            f"It resets at midnight UTC. Library lookups, sizes and status still answer right away; ask your admin to raise the limit if you need more today.")
