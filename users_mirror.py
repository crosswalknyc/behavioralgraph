"""Where the app keeps its local copy of the users document.

`app.load_users` mirrors the S3 users document to a local file for
faster reads, and `save_users` keeps that file in step. The file is
`users.json` beside the app, which is tracked in git. Every test suite
that boots the app in a child process (the ask replays, about three
dozen suites) loaded the live document and rewrote the tracked file, so
a test run left a 5 MB change in the working tree that a bulk add would
have swept into a commit (found 2026-11-12).

A test process keeps its mirror at a path of its own, seeded from the
tracked file so the local fallback reads what it always read, and
removed when the process ends. Outside a test the path is the tracked
file, as before.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TEST_ENV = ('REGRESSION_TEST_MODE', 'PM_REPLAY_CHILD', 'PYTEST_CURRENT_TEST')


def is_test_process(environ=None):
    env = os.environ if environ is None else environ
    return any(str(env.get(k) or '').strip() not in ('', '0') for k in _TEST_ENV)


def private_path(pid=None):
    return os.path.join(tempfile.gettempdir(), f"bg_webapp_users_mirror_{int(pid or os.getpid())}.json")


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def path_for_process(tracked, environ=None):
    """The file this process mirrors the users document to. Never raises:
    on any failure the answer is the path a test may write to freely, or
    the tracked file outside a test."""
    if not is_test_process(environ):
        return tracked
    private = private_path()
    try:
        if os.path.exists(tracked) and not os.path.exists(private):
            shutil.copyfile(tracked, private)
        atexit.register(_remove, private)
    except Exception:
        pass
    return private
