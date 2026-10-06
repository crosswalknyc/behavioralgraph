#!/usr/bin/env python3
"""One ops status payload for the admin System Status tile (2026-10-02 RCA W3).

Reads the documents the build server writes and folds them into one
shape the admin page renders. Pure read: nothing here mutates S3.

    system/ops/status.json                 hourly heartbeat (timers, failed units,
                                           regression suites, canary, prod version)
    system/ops/pm_corrections_status.json  nightly corrections triage
    system/ops/smoke_latest.json           post-deploy smoke + main vs prod
    system/ops/publish_lock.json           who is publishing right now

``build_payload(s3, bucket, this_version, now)`` is the only entry
point. It never raises: a missing or unreadable document shows up as
``present: False`` on its section, and the overall light turns amber
when the heartbeat is stale (> 2 hours) rather than red, because a
late heartbeat is itself one of the conditions the tile exists to
show.

Twin: bg-webapp/migration/ops_status.py is generated from this file by
scripts/sync_module_twins.py.
"""
from __future__ import annotations

import datetime as dt
import json

STATUS_KEY = 'system/ops/status.json'
CORRECTIONS_KEY = 'system/ops/pm_corrections_status.json'
SMOKE_KEY = 'system/ops/smoke_latest.json'
WATCH_KEY = 'system/ops/pm_watch_recent.json'
HELD_PREFIX = 'system/ops/held_replies/'
LOCK_KEY = 'system/ops/publish_lock.json'
STALE_HEARTBEAT_S = 2 * 3600


def _parse(s):
    try:
        return dt.datetime.strptime(str(s), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _age_s(stamp, now):
    t = _parse(stamp)
    return int((now - t).total_seconds()) if t else None


def read_json(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except Exception:  # noqa: BLE001
        return None


def overall_light(heartbeat, smoke, lock, now):
    """'green' | 'amber' | 'red' plus the plain reasons."""
    reasons = []
    if not isinstance(heartbeat, dict) or not heartbeat.get('generated_at'):
        return 'amber', ['no heartbeat document yet']
    age = _age_s(heartbeat.get('generated_at'), now)
    if age is not None and age > STALE_HEARTBEAT_S:
        reasons.append(f'heartbeat is {age // 3600}h old')
    for c in heartbeat.get('conditions') or []:
        text = c.get('text') if isinstance(c, dict) else str(c)
        if text:
            reasons.append(text)
    if isinstance(smoke, dict):
        sm = smoke.get('smoke') if isinstance(smoke.get('smoke'), dict) else {}
        if sm.get('result') == 'red':
            reasons.append(f"prod smoke failed on {sm.get('sha')}")
        if smoke.get('state') == 'pending' and (smoke.get('lag_min') or 0) >= 25:
            reasons.append(f"prod is {smoke.get('lag_min')} min behind main")
    red = any(not r.startswith('heartbeat is') for r in reasons)
    if red:
        return 'red', reasons
    if reasons:
        return 'amber', reasons
    return 'green', []


def timers_view(heartbeat, now):
    out = []
    for t in (heartbeat or {}).get('timers') or []:
        if not t.get('ours', True):
            continue
        last = _parse(t.get('last'))
        nxt = _parse(t.get('next'))
        out.append({'unit': t.get('unit'), 'last': t.get('last'), 'next': t.get('next'),
                    'last_age_min': int((now - last).total_seconds() // 60) if last else None,
                    'overdue': bool(nxt and nxt < now - dt.timedelta(minutes=15))})
    out.sort(key=lambda x: (not x['overdue'], x['unit'] or ''))
    return out


def watch_view(s3, bucket, now):
    """Prometheus watch (2026-10-06): the asks flagged in the last 24
    hours (a repeated clarify, an empty or faulted reply, a user saying
    no / wrong, a held read) and the open promises with a due time.
    Red when a promise is overdue or three or more flags landed in the
    last hour; amber when anything is open; green when quiet."""
    out = {'present': False, 'flags_24h': 0, 'flags_1h': 0, 'recent': [],
           'open_promises': 0, 'overdue_promises': 0, 'promises': [], 'light': None}
    try:
        doc = read_json(s3, bucket, WATCH_KEY)
        items = [x for x in ((doc or {}).get('items') or []) if isinstance(x, dict)]
        day = now - dt.timedelta(hours=24)
        hour = now - dt.timedelta(hours=1)
        recent = []
        for x in items:
            ts = _parse(x.get('ts'))
            if not ts or ts < day:
                continue
            recent.append(x)
        out['present'] = bool(doc)
        out['flags_24h'] = len(recent)
        out['flags_1h'] = sum(1 for x in recent if (_parse(x.get('ts')) or day) >= hour)
        out['recent'] = [{'ts': x.get('ts'), 'user': x.get('user'), 'outcome': x.get('outcome'),
                          'route': x.get('route'), 'question': str(x.get('question') or '')[:120]}
                         for x in sorted(recent, key=lambda x: str(x.get('ts') or ''), reverse=True)[:8]]
    except Exception:
        pass
    try:
        promises = []
        for d in (now, now - dt.timedelta(days=1)):
            prefix = f"{HELD_PREFIX}{d.strftime('%Y-%m-%d')}/"
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for o in resp.get('Contents') or []:
                doc = read_json(s3, bucket, o['Key'])
                if isinstance(doc, dict) and doc.get('status', 'open') == 'open':
                    due = _parse(doc.get('due_by'))
                    promises.append({'user': doc.get('user'), 'question': str(doc.get('question') or '')[:120],
                                     'kind': doc.get('kind'), 'opened_at': doc.get('opened_at'),
                                     'due_by': doc.get('due_by'), 'overdue': bool(due and due < now)})
        out['present'] = out['present'] or bool(promises)
        out['open_promises'] = len(promises)
        out['overdue_promises'] = sum(1 for p in promises if p['overdue'])
        out['promises'] = sorted(promises, key=lambda p: str(p.get('opened_at') or ''), reverse=True)[:8]
    except Exception:
        pass
    if out['overdue_promises'] or out['flags_1h'] >= 3:
        out['light'] = 'red'
    elif out['open_promises'] or out['flags_24h']:
        out['light'] = 'amber'
    else:
        out['light'] = 'green'
    return out


def build_payload(s3, bucket, this_version=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    hb = read_json(s3, bucket, STATUS_KEY)
    corr = read_json(s3, bucket, CORRECTIONS_KEY)
    smoke = read_json(s3, bucket, SMOKE_KEY)
    lock = read_json(s3, bucket, LOCK_KEY)
    light, reasons = overall_light(hb, smoke, lock, now)

    hb = hb if isinstance(hb, dict) else {}
    smoke = smoke if isinstance(smoke, dict) else {}
    lock = lock if isinstance(lock, dict) else {}
    corr = corr if isinstance(corr, dict) else {}

    lock_exp = _parse(lock.get('expires_at'))
    lock_held = bool(lock.get('owner') and lock_exp and lock_exp > now)

    prod = hb.get('prod') if isinstance(hb.get('prod'), dict) else {}
    sm = smoke.get('smoke') if isinstance(smoke.get('smoke'), dict) else {}
    regression = hb.get('regression') if isinstance(hb.get('regression'), dict) else {}
    canary = hb.get('intake_canary') if isinstance(hb.get('intake_canary'), dict) else {}

    return {
        'success': True,
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'light': light,
        'reasons': reasons,
        'heartbeat': {
            'present': bool(hb.get('generated_at')),
            'generated_at': hb.get('generated_at'),
            'age_min': (_age_s(hb.get('generated_at'), now) or 0) // 60 if hb.get('generated_at') else None,
            'host': hb.get('host'),
            'green': hb.get('green'),
            'failed_units': list(hb.get('failed_units') or []),
        },
        'prod': {
            'version': prod.get('version'),
            'boot_time': prod.get('boot_time'),
            'reachable': prod.get('ok'),
            'this_process_version': (this_version or '')[:12] or None,
            'main_sha': smoke.get('main_sha'),
            'deploy_state': smoke.get('state'),
            'lag_min': smoke.get('lag_min'),
            'deployed_at': smoke.get('deployed_at'),
        },
        'smoke': {
            'present': bool(sm),
            'result': sm.get('result'),
            'sha': sm.get('sha'),
            'ran_at': sm.get('ran_at'),
            'total': sm.get('total'),
            'failed': sm.get('failed'),
            'failing': list(sm.get('failing') or []),
            'checks': [{'label': c.get('label'), 'status': c.get('status'), 'ms': c.get('ms'),
                        'ok': c.get('ok')} for c in (sm.get('checks') or [])],
        },
        'canary': {
            'present': bool(canary),
            'date': canary.get('date'),
            'result': canary.get('result'),
            'stalled': canary.get('stalled'),
            'total': canary.get('total'),
        },
        'regression': [
            {'name': name, 'result': st.get('result'), 'passed': st.get('passed'),
             'total': st.get('total'), 'failing': list(st.get('failing') or []),
             'crash': st.get('crash')}
            for name, st in regression.items() if isinstance(st, dict)
        ],
        'timers': timers_view(hb, now),
        'watch': watch_view(s3, bucket, now),
        'scorecard': read_json(s3, bucket, 'system/ops/pm_scorecard.json') or {'present': False},
        'corrections': {
            'present': bool(corr.get('generated_at')),
            'generated_at': corr.get('generated_at'),
            'offered': corr.get('offered'),
            'open': corr.get('open'),
            'applied': corr.get('applied'),
            'applied_last_7d': corr.get('applied_last_7d'),
            'banked': corr.get('banked'),
            'decisions_from_corrections': corr.get('decisions_from_corrections'),
            'replay': (corr.get('replay') or {}).get('result') if isinstance(corr.get('replay'), dict) else corr.get('replay'),
            'replay_failed': (corr.get('replay') or {}).get('failed') if isinstance(corr.get('replay'), dict) else None,
        },
        'publish_lock': {
            'held': lock_held,
            'owner': lock.get('owner') if lock_held else None,
            'acquired_at': lock.get('acquired_at') if lock_held else None,
            'expires_at': lock.get('expires_at') if lock_held else None,
            'note': (lock.get('note') or '') if lock_held else '',
        },
    }
