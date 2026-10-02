#!/usr/bin/env python3
"""Self-growing regression set + in-flight read dedupe (2026-09-30).

1. A question the reader called wrong banks to the nightly registry
   at the complaint-regenerate point, before the rerun starts.
2. The same user re-sending the same question while its read job is
   still working attaches to the running job instead of starting a
   second copy (and a paid re-send refunds first).
"""
import hashlib
import os
import re
import sys
# Legacy-move shim (2026-10-01): the Prometheus code lives in
# bg-webapp/prometheus/legacy/chat.py; read app.py + legacy as one source.
import os as _pm_os, sys as _pm_sys
_pm_r = _pm_os.path.dirname(_pm_os.path.abspath(__file__))
while not _pm_os.path.exists(_pm_os.path.join(_pm_r, 'bg-webapp', 'app.py')):
    _pm_r = _pm_os.path.dirname(_pm_r)
_pm_sys.path.insert(0, _pm_os.path.join(_pm_r, 'scripts'))
from _pm_test_source import app_path as _pm_app_path, host_for as _pm_host_for  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = open(str(_pm_app_path()), encoding='utf-8').read()

FAIL = 0


def check(name, ok):
    global FAIL
    print(('PASS' if ok else 'FAIL'), name)
    if not ok:
        FAIL += 1


# ------------------------------------------------------------------
# 1. Bank helpers exist and are wired at the regenerate point.
# ------------------------------------------------------------------
check('bank helper defined',
      'def _pm_bank_regression_case(' in SRC)
check('registry key constant',
      "_PM_REGRESSION_CASES_KEY = 'system/pm_regression_cases.json'"
      in SRC)

wire = SRC.find('_pm_bank_regression_case(\n                _fb_user')
reassign = SRC.find('text = _prev_q')
check('bank fires before the rerun reassigns text',
      0 < wire < reassign)
check('bank receives the on-screen context',
      "(body.get('page_context') or {}).get('view_context'))" in SRC)

# Functional: extract the pure helpers (q key + compactor).
m = re.search(r"(_PM_REGRESSION_CASES_KEY = 'system.*?)\n\n\ndef "
              r"_pm_bank_regression_case", SRC, re.S)
check('pure helpers extractable', m is not None)
ns = {'re': re, 'hashlib': hashlib}
_pm_host_for(ns)
exec(m.group(1), ns)
qkey = ns['_pm_regression_q_key']
compact = ns['_pm_compact_for_bank']

check('q key stable across spacing and case',
      qkey('Where is the fall-off from the ticket checkout page?')
      == qkey('where IS the   fall off from the ticket CHECKOUT'
              ' page'))
check('q key distinct for different questions',
      qkey('where is the fall-off') != qkey('who watches landman'))
check('q key is 16 hex chars',
      re.fullmatch(r'[0-9a-f]{16}', qkey('any question')))

big = {'title': 'x' * 900,
       'rows': [{'name': 'r%d' % i} for i in range(60)],
       'deep': {'a': {'b': {'c': {'d': {'e': 'gone'}}}}}}
out = compact(big)
check('long strings trimmed to 400', len(out['title']) == 400)
check('lists capped at 25', len(out['rows']) == 25)
check('depth capped', out['deep']['a']['b'] == {'c': None})

# ------------------------------------------------------------------
# 2. In-flight dedupe on analysis asks.
# ------------------------------------------------------------------
check('inflight check defined',
      'def _pm_read_inflight_check(' in SRC)
check('inflight mark defined',
      'def _pm_read_inflight_mark(' in SRC)
check('inflight index under the reads prefix',
      "_PM_READ_INFLIGHT_PREFIX = 'system/prometheus_reads/"
      "_inflight/'" in SRC)

gate = SRC.find('_dup_read = _pm_read_inflight_check(_pm_user, text)')
spawn = SRC.find("job_id = uuid.uuid4().hex[:12]")
check('dedupe gate runs before the job spawns', 0 < gate < spawn)
check('new jobs are marked in the index',
      '_pm_read_inflight_mark(_pm_user, text, job_id)' in SRC)
check('duplicate reply hands back the running job id',
      "'read_job_id': _dup_read['job_id']" in SRC)
check('paid re-send refunds before attaching',
      SRC.count('_pm_panel_refund(panel_charge)') >= 2)
check('entries self-expire at 15 minutes',
      SRC.count("> 900") >= 1 and SRC.count("<= 900") >= 1)
check('only working jobs dedupe',
      "!= 'working':\n            return None" in SRC.replace(
          'str(status.get(\'status\') or \'\') ', ''))

reply_block = SRC[SRC.find("'Already on it - that exact read is'"
                           .replace("'", '')) - 50:]
dup_reply = ('Already on it - that exact read is running now')
check('duplicate reply text present',
      'Already on it - that exact read is' in SRC)
for banned in ('synth', 'pipeline', 'hostmap', 'queue', 'Claude',
               'dedupe', 'job index'):
    seg = SRC[SRC.find('Already on it - that exact read is'):
              SRC.find('Already on it - that exact read is') + 400]
    check(f'duplicate reply carries no "{banned}"',
          banned not in seg)

# ------------------------------------------------------------------
# 3. Nightly runner (parent repo; skip when absent in a bare
#    bg-webapp checkout).
# ------------------------------------------------------------------
runner = os.path.join(os.path.dirname(ROOT), 'migration',
                      'pm_regression_nightly.py')
if os.path.exists(runner):
    rsrc = open(runner, encoding='utf-8').read()
    check('nightly failures go to jenna and jessie only',
          "'jenna@crosswalknyc.com'" in rsrc
          and "'jessie@crosswalknyc.com'" in rsrc
          and 'liz@' not in rsrc)
    check('nightly runs the static suites',
          "test_pm_*.py" in rsrc)
    check('nightly replays the banked registry',
          'pm_regression_cases.json' in rsrc
          and '_pm_view_owns_ask' in rsrc)
else:
    print('SKIP nightly runner checks (bare checkout)')

print()
if FAIL:
    print(f'{FAIL} CHECK(S) FAILED')
    sys.exit(1)
print('ALL CHECKS PASSED')
