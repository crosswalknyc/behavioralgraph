"""Probe identities and test processes: one predicate for every surface
that records what a person did (2026-10-22).

The day's digest listed seven "corrections" from Scott between 01:57
and 09:06 UTC. Scott had typed nothing in two weeks. Each one was a
regression suite replaying his 2026-10-09 fixture ("no a pdf was
created. where is it") through the real door: the turn gate graded the
prior reply failed_by_user and wrote the signal to production S3 with
the clock's time, once per suite run (the full nightly runner twice,
single suites in between, the build server's own nightly at 09:06).
The same digest listed six "repeats" and a held promise from
sandbox_partner, the internal API sandbox key that the post-deploy
smoke asks "How big is the Babylon 5 audience on Prime Video?" on
every deploy; three deploys inside thirty minutes walked the smoke
into the repeat guard's calm line.

The question-copy mailer already knew to skip these (canary:* names,
api_key_sandbox on the record). The signal writer, the watch card, the
held-promise record, the grading and the digest each kept their own
partial list, or none. This module is the one list and the one test:

    is_probe_user(username, record=None)  a probe identity: canary:*,
                                          replay / unknown / blank, a
                                          sandbox key's owner, a name
                                          the smoke or a grader uses
    is_test_process()                     this process is a test or a
                                          replay (mail_guard's flags)
    may_record(username, record=None)     a real person in a real
                                          process: the only case any
                                          surface writes a signal, a
                                          watch flag, a held promise or
                                          a grade, or lists one
    live(username, record=None)           read-side twin: a stored
                                          item belongs to a real person

Every surface imports from here; none keeps its own list. Fail-safe:
an unreadable users.json only loses the dynamic sandbox lookup, the
static names still hold.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import mail_guard as _mg

PROBE_PREFIXES = ('canary',)
PROBE_USERS = frozenset({'', 'unknown', 'replay', 'sandbox_partner', 'canary', 'smoke', 'probe',
                         'golden', 'regression', 'anon'})
_PROBE_NAME_RX = re.compile(r"(?:^|[^a-z])(?:canary|smoke|sandbox|regression|golden|probe)(?:[^a-z]|$)", re.I)
_SANDBOX_TTL_S = 15 * 60
_sandbox_cache = {'at': 0.0, 'names': frozenset()}


def is_test_process():
    """True inside a test or replay process (REGRESSION_TEST_MODE,
    PM_REPLAY_CHILD, PYTEST_CURRENT_TEST, PM_MAIL_OFF)."""
    return bool(_mg.test_mode_reason())


def _sandbox_owners(s3=None, bucket='dashboard-inputs'):
    """Usernames whose API key is a sandbox key, from users.json, cached
    a quarter hour. Empty (never raising) when the file cannot be read."""
    now = time.time()
    if now - float(_sandbox_cache.get('at') or 0) < _SANDBOX_TTL_S:
        return _sandbox_cache['names']
    names = set()
    try:
        if s3 is None:
            import boto3
            s3 = boto3.client('s3', region_name='us-east-2')
        doc = json.loads(s3.get_object(Bucket=bucket, Key='system/users.json')['Body'].read())
        for name, rec in (doc.get('users') or {}).items():
            if isinstance(rec, dict) and rec.get('api_key_sandbox'):
                names.add(str(name or '').strip().lower())
    except Exception:
        names = set(_sandbox_cache['names'])
    _sandbox_cache['names'] = frozenset(names)
    _sandbox_cache['at'] = now
    return _sandbox_cache['names']


def is_probe_user(username, record=None, s3=None):
    """A probe identity: never a real person's ask."""
    u = str(username or '').strip().lower()
    if u in PROBE_USERS:
        return True
    if any(u.startswith(p) for p in PROBE_PREFIXES):
        return True
    if isinstance(record, dict):
        if record.get('api_key_sandbox') or record.get('probe') or record.get('is_probe'):
            return True
        ru = str(record.get('username') or '').strip().lower()
        if ru and ru != u and is_probe_user(ru):
            return True
    if _PROBE_NAME_RX.search(u):
        return True
    if os.environ.get('PM_PROBE_LOOKUP', '1') not in ('0', 'false', 'no', 'off') and u in _sandbox_owners(s3):
        return True
    return False


def asks_on_purpose(username, record=None, s3=None):
    """A scripted asker that repeats the same question by design: the
    post-deploy smoke on its sandbox key, a canary, a golden-set grader.
    The repeat guard, the correction escalation and the calm line never
    apply to it. The nightly `replay` user is NOT one: it replays real
    conversations to diff outcomes, so the guard must behave for it
    exactly as it did for the person (recording is still off for it
    through may_record)."""
    u = str(username or '').strip().lower()
    if u in ('replay', 'unknown', '', 'anon'):
        return False
    return is_probe_user(username, record, s3)


def may_record(username, record=None, s3=None):
    """A real person in a real process: the only case a surface writes
    a signal, a watch flag, a held promise or a grade."""
    if is_test_process():
        return False
    return not is_probe_user(username, record, s3)


def live(username, record=None, s3=None):
    """Read-side twin of may_record: a stored item belongs to a real
    person (the digest, the watch card and the grading list only these)."""
    return not is_probe_user(username, record, s3)


def set_sandbox_owners(names):
    """Test hook: pin the sandbox owner set without reading users.json."""
    _sandbox_cache['names'] = frozenset(str(n or '').strip().lower() for n in (names or ()))
    _sandbox_cache['at'] = time.time()


__all__ = ['is_probe_user', 'asks_on_purpose', 'is_test_process', 'may_record', 'live', 'set_sandbox_owners',
           'PROBE_PREFIXES', 'PROBE_USERS']
