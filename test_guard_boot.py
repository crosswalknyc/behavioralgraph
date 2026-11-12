"""Put the test-mode storage rule on the client class from a module
that is not inside the `migration` package (2026-11-12).

`migration/storage_test_guard.py` holds the rule: under
REGRESSION_TEST_MODE=1 no write from this process reaches a live store.
The `migration` package installs it when it is imported. The modules
that make the clients a suite reaches without that package (the ledger,
the ask log, the Prometheus package) call `install()` here instead. The
guard is loaded by file path, never as `migration.<x>`: both this repo
and the parent repo carry a `migration` package, and which one a process
binds to that name is the suite's business, not this module's.

Outside a test process this does nothing and imports nothing.
"""
from __future__ import annotations

import os

_GUARD = None


def install():
    """True when the rule is in place for this process."""
    global _GUARD
    if os.environ.get('REGRESSION_TEST_MODE') != '1':
        return False
    try:
        if _GUARD is None:
            import importlib.util
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migration', 'storage_test_guard.py')
            spec = importlib.util.spec_from_file_location('_pm_storage_test_guard', path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _GUARD = mod
        return bool(_GUARD.install())
    except Exception:
        return False


def guard():
    """The loaded guard module (after `install()`), else None."""
    return _GUARD
