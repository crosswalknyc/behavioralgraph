#!/usr/bin/env python3
"""A tagged model call never boots the dashboard (2026-11-17).

The usage log prices every tagged call (``render_usage_log.record_call``
-> the markup -> ``wallet.load_pricing``). Until today ``load_pricing``
did ``from app import s3_client``, so any process on the build server
that made one model call imported the whole dashboard: its background
threads (the stale sweep, the category norm refresh, the cache checker)
ran from inside a one-off script and held the process open after its
work was done. ``wallet._pricing_store`` now reads the pricing through
a client of its own and takes the dashboard's only when the dashboard
is already the running process.

Held here (each in a fresh interpreter, hermetic):
  * load_pricing returns the pricing and ``app`` is not imported;
  * record_call on a tagged call prices it and ``app`` is not imported;
  * when ``app`` is already imported and initialized, its client is the
    one used (a stand-in module proves the lookup order).
    python3 scripts/test_pricing_lookup_never_boots_the_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ENV = dict(os.environ, REGRESSION_TEST_MODE='1', CORPUS_CATALOG='0', PYTHONPATH=ROOT)


def _run(code: str) -> str:
    r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AssertionError(r.stdout[-1500:] + r.stderr[-1500:])
    return r.stdout.strip().splitlines()[-1]


class NoApp(unittest.TestCase):
    def test_load_pricing_does_not_import_the_dashboard(self):
        out = _run("import sys, wallet; p = wallet.load_pricing(force_reload=True); print(isinstance(p, dict) and 'per_tool_usd' in p, 'app' in sys.modules)")
        self.assertEqual(out, 'True False')

    def test_a_tagged_call_is_priced_without_the_dashboard(self):
        out = _run("import sys, render_usage_log as RU; RU.record_call('twin_room_archetype', 'chatbot', 'claude-opus-5', {'input_tokens': 9000, 'output_tokens': 7500}); "
                   "print(RU.cost_usd('claude-opus-5', {'input_tokens': 9000, 'output_tokens': 7500}) > 0, 'app' in sys.modules)")
        self.assertEqual(out, 'True False')

    def test_the_dashboard_client_is_used_when_the_dashboard_is_the_process(self):
        out = _run("import sys, types, os; os.environ.pop('REGRESSION_TEST_MODE', None); m = types.ModuleType('app'); m.s3_client = object(); m.METADATA_BUCKET = 'their-bucket'; sys.modules['app'] = m; "
                   "import wallet; c, b = wallet._pricing_store(); print(c is m.s3_client, b)")
        self.assertEqual(out, 'True their-bucket')


if __name__ == '__main__':
    unittest.main(verbosity=2)
