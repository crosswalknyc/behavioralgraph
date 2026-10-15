"""Policy on a partner key: scopes, limits, caps, webhook, sandbox.

Everything lives on the key owner's users.json record so the admin user
modal can show and edit it. Missing fields mean the defaults below, so
every key that exists today keeps working unchanged.

    api_key_scopes        ['ask', 'build']           what the key may do
    api_key_limits        {'ask_per_minute': 30, 'ask_per_day': 400,
                           'jobs_per_minute': 300, 'jobs_per_day': 5000}
    api_key_daily_cap_usd 50.0                      metered spend per UTC day
    api_key_webhook_url   https://...                fired once per terminal job
    api_key_webhook_secret shared secret for X-Crosswalk-Signature (HMAC-SHA256)
    api_key_sandbox       false                      sandbox keys never bill
    api_key_label         a name for the key (admin)

Decisions (Jenna 2026-10-14, plan defaults taken): metered billing on
the owner's wallet, 30 / 400 asks, $50 a day, webhooks optional,
conversation memory per key.
"""
from __future__ import annotations

import hashlib

__all__ = ['SCOPES', 'DEFAULT_LIMITS', 'DEFAULT_DAILY_CAP_USD', 'key_id', 'scopes', 'has_scope', 'limits',
           'daily_cap_usd', 'webhook', 'is_sandbox', 'policy_view', 'apply_policy']

SCOPES = ('ask', 'build', 'read_only')
DEFAULT_SCOPES = ('ask', 'build')
DEFAULT_LIMITS = {'ask_per_minute': 30, 'ask_per_day': 400, 'jobs_per_minute': 300, 'jobs_per_day': 5000}
DEFAULT_DAILY_CAP_USD = 50.0
REQUEST_TEXT_MAX = 4000
REQUEST_BODY_MAX = 64 * 1024
IDEMPOTENCY_TTL_S = 86400


def key_id(user: dict) -> str:
    """A stable, non-secret id for the key: the first 24 hex of the
    sha256 the record already stores; a session user gets a session id."""
    u = user or {}
    sha = str(u.get('api_key_sha256') or '').lower()
    if sha:
        return sha[:24]
    name = str(u.get('username') or u.get('email') or 'anon')
    return 'session:' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]


def scopes(user: dict) -> tuple:
    raw = (user or {}).get('api_key_scopes')
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(',') if x.strip()]
    if not isinstance(raw, (list, tuple)) or not raw:
        return DEFAULT_SCOPES
    out = tuple(str(x).strip().lower() for x in raw if str(x).strip().lower() in SCOPES)
    return out or DEFAULT_SCOPES


def has_scope(user: dict, scope: str) -> bool:
    sc = scopes(user)
    if scope in sc:
        return True
    # read_only is implied by any scope: status, schemas and usage are always readable
    return scope == 'read_only'


def limits(user: dict) -> dict:
    out = dict(DEFAULT_LIMITS)
    raw = (user or {}).get('api_key_limits')
    if isinstance(raw, dict):
        for k in DEFAULT_LIMITS:
            try:
                v = int(raw.get(k))
                if v > 0:
                    out[k] = v
            except Exception:
                pass
    return out


def daily_cap_usd(user: dict) -> float:
    try:
        v = float((user or {}).get('api_key_daily_cap_usd'))
        return v if v > 0 else DEFAULT_DAILY_CAP_USD
    except Exception:
        return DEFAULT_DAILY_CAP_USD


def webhook(user: dict) -> dict:
    """{'url', 'secret'} when the key has a webhook; {} otherwise. Only
    https URLs count."""
    url = str((user or {}).get('api_key_webhook_url') or '').strip()
    if not url.lower().startswith('https://'):
        return {}
    return {'url': url, 'secret': str((user or {}).get('api_key_webhook_secret') or '')}


def is_sandbox(user: dict) -> bool:
    return bool((user or {}).get('api_key_sandbox'))


def policy_view(user: dict) -> dict:
    """What the admin key view and the usage endpoint show about policy."""
    wh = webhook(user)
    return {'key_id': key_id(user), 'scopes': list(scopes(user)), 'limits': limits(user),
            'daily_cap_usd': daily_cap_usd(user), 'webhook_url': wh.get('url') or None,
            'sandbox': is_sandbox(user), 'label': str((user or {}).get('api_key_label') or ''),
            'enabled': bool((user or {}).get('api_key_enabled')), 'last_used': (user or {}).get('api_key_last_used')}


def apply_policy(user: dict, patch: dict) -> dict:
    """Validate and apply an admin patch to the key fields in place.
    Returns the changed field names. Unknown fields are ignored."""
    changed = []
    p = patch or {}
    if 'scopes' in p:
        sc = p.get('scopes')
        if isinstance(sc, str):
            sc = [x.strip() for x in sc.split(',')]
        sc = [str(x).strip().lower() for x in (sc or []) if str(x).strip().lower() in SCOPES]
        user['api_key_scopes'] = sc or list(DEFAULT_SCOPES)
        changed.append('scopes')
    if 'limits' in p and isinstance(p.get('limits'), dict):
        lim = {}
        for k in DEFAULT_LIMITS:
            try:
                v = int(p['limits'].get(k))
                if v > 0:
                    lim[k] = v
            except Exception:
                pass
        user['api_key_limits'] = lim
        changed.append('limits')
    if 'daily_cap_usd' in p:
        try:
            user['api_key_daily_cap_usd'] = max(0.0, float(p['daily_cap_usd']))
            changed.append('daily_cap_usd')
        except Exception:
            pass
    if 'webhook_url' in p:
        url = str(p.get('webhook_url') or '').strip()
        user['api_key_webhook_url'] = url if url.lower().startswith('https://') else ''
        changed.append('webhook_url')
    if 'webhook_secret' in p:
        user['api_key_webhook_secret'] = str(p.get('webhook_secret') or '')
        changed.append('webhook_secret')
    if 'sandbox' in p:
        user['api_key_sandbox'] = bool(p.get('sandbox'))
        changed.append('sandbox')
    if 'label' in p:
        user['api_key_label'] = str(p.get('label') or '')[:60]
        changed.append('label')
    return changed
