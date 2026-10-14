"""One door for managed keys and secrets (audit item 8, Jenna 2026-10-14).

(The file is named managed_keys because the repo ignores any path
containing "secret".)

Secrets used to live only in .env files on laptops and the build server.
`get(name)` resolves a secret in this order and caches it for the
process: the environment, AWS Secrets Manager (crosswalk/prod/<NAME>),
SSM Parameter Store (/crosswalk/prod/<NAME>), then None. The
environment stays first so nothing changes for a deployment that
already sets its variables; the managed stores let the files go away.

`scripts/push_secrets.py` uploads the keys from a .env file to Secrets
Manager once; after that a host needs only AWS credentials.
"""
from __future__ import annotations

import os
import threading
import time

PREFIX = os.environ.get('CROSSWALK_SECRETS_PREFIX', 'crosswalk/prod/')
REGION = os.environ.get('CROSSWALK_SECRETS_REGION', os.environ.get('AWS_DEFAULT_REGION', 'us-east-2'))
_cache = {}
_lock = threading.Lock()
_TTL_S = 15 * 60
MANAGED = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'SYNTH_QUEUE_SECRET', 'FLASK_SECRET_KEY', 'STRIPE_SECRET_KEY',
           'CLICKHOUSE_PASSWORD', 'SES_SMTP_PASSWORD', 'GOOGLE_SHEETS_CREDENTIALS')


def _from_secrets_manager(name):
    try:
        import boto3
        cl = boto3.client('secretsmanager', region_name=REGION)
        v = cl.get_secret_value(SecretId=f"{PREFIX}{name}")
        return v.get('SecretString') or None
    except Exception:
        return None


def _from_ssm(name):
    try:
        import boto3
        cl = boto3.client('ssm', region_name=REGION)
        v = cl.get_parameter(Name=f"/{PREFIX}{name}", WithDecryption=True)
        return (v.get('Parameter') or {}).get('Value') or None
    except Exception:
        return None


def get(name, default=None, *, env_first=True):
    """The secret's value, or default. Never raises."""
    try:
        n = str(name or '').strip()
        if not n:
            return default
        if env_first:
            v = (os.environ.get(n) or '').strip()
            if v:
                return v
        now = time.time()
        with _lock:
            hit = _cache.get(n)
            if hit and now - hit[0] < _TTL_S:
                return hit[1] if hit[1] is not None else default
        v = _from_secrets_manager(n) or _from_ssm(n)
        with _lock:
            _cache[n] = (now, v)
        if v:
            os.environ.setdefault(n, v)   # downstream libraries read the environment
        return v if v else default
    except Exception:
        return default


def warm(names=MANAGED):
    """Resolve the managed secrets into the environment at boot (a host
    with only AWS credentials comes up with its keys)."""
    out = {}
    for n in names:
        out[n] = bool(get(n))
    return out
