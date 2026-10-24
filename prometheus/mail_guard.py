"""One question every mailer asks before it touches SES: may this
process send mail at all? (Jenna 2026-10-16: "why do I keep getting the
acme partner emails ... you dont have to email me/liz/jessie those".)

A regression suite (`scripts/test_pm_api_contract_v1.py`, nightly on the
build server and whenever an engineer ran it) exercised the ask door
with a fixture account named acme_partner; every ask fired the real
"asked Prometheus" note to Jenna, Liz and Jessie, 34 per run. A second
suite shipped a partner's "profile is ready" note through the outbound
door each night. Neither test meant to mail anyone; the mailers had no
way to know they were inside a test.

Sending is off when the process says it is a test or a replay:
REGRESSION_TEST_MODE, PM_REPLAY_CHILD, PYTEST_CURRENT_TEST, or the
explicit PM_MAIL_OFF=1. Every SES caller in the Prometheus surface
checks `sending_allowed()` first and records the suppressed send
instead. Production (Render, the build server's workers) sets none of
these, so real users and ops mail exactly as before.

Sending is also off for the ask in flight when the asker is a probe
identity (Jenna 2026-10-24: the nightly golden grader asked "Section 4
of the Digital Journey page, please remove the clickstream tab" as
canary:golden on production, and the design-request lane mailed the
user experience team every night). The doors stamp the asker on
flask.g (`_pm_ask_user`); a canary, smoke, sandbox or golden asker
means no mailer in that request reaches SES, whatever lane it runs.
"""
from __future__ import annotations

import os

_FLAGS = ('REGRESSION_TEST_MODE', 'PM_REPLAY_CHILD', 'PYTEST_CURRENT_TEST', 'PM_MAIL_OFF')


def test_mode_reason():
    """The flag that marks this process as a test, or ''."""
    for k in _FLAGS:
        v = str(os.environ.get(k) or '').strip().lower()
        if v and v not in ('0', 'false', 'no', 'off'):
            return k
    return ''


def probe_ask_reason():
    """'probe ask (<user>)' when the request in flight belongs to a probe
    identity (canary, smoke, sandbox key, golden grader), else ''.
    Outside a request there is no asker to judge, so ''."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return ''
        user = str(getattr(g, '_pm_ask_user', '') or '').strip()
        if not user:
            return ''
        from prometheus.probe_identity import is_probe_user
        return f'probe ask ({user})' if is_probe_user(user) else ''
    except Exception:
        return ''


def suppression_reason():
    """Why this send must not happen: the test flag, or the probe asker, or ''."""
    return test_mode_reason() or probe_ask_reason()


def sending_allowed():
    """False inside a test or replay process, and false for a probe's ask
    (no SES call may follow in either case)."""
    return not suppression_reason()


def suppressed_note(where, subject=''):
    """The one-line record a suppressed send leaves in the log."""
    reason = suppression_reason()
    why = reason if reason.startswith('probe ask') else f"{reason} is set"
    return f"[mail] {where}: not sent, {why}" + (f" ({str(subject)[:80]})" if subject else '')


__all__ = ['sending_allowed', 'test_mode_reason', 'probe_ask_reason', 'suppression_reason', 'suppressed_note']
