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


def sending_allowed():
    """False inside a test or replay process (no SES call may follow)."""
    return not test_mode_reason()


def suppressed_note(where, subject=''):
    """The one-line record a suppressed send leaves in the log."""
    return f"[mail] {where}: not sent, {test_mode_reason()} is set" + (f" ({str(subject)[:80]})" if subject else '')


__all__ = ['sending_allowed', 'test_mode_reason', 'suppressed_note']
