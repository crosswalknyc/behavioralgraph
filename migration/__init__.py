"""The migration package.

Importing it in a test process (REGRESSION_TEST_MODE=1) puts the
storage rule on the client class: no write from a test reaches a live
store (migration/storage_test_guard.py). Outside a test process this
does nothing.
"""
try:
    from . import storage_test_guard as _storage_test_guard
    _storage_test_guard.install()
except Exception:      # never in the way of an import
    pass
