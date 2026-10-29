#!/usr/bin/env python3
"""The Subscriber IQ tracker reports the watcher count its FILE displays
(2026-10-29, S.W.A.T. Exiles Season 1 to date).

run_synthetic_attribution returned the pre-divisor panel as total_panel; the
worker snapped the title anchor to that value x 32.99 and recorded a universe
ten times the file's own projection (18,720,835 against 1,872,084). The
return now reads the Total Show Watchers row back from the written CSV.

Offline: exercises the read-back helper and the return wiring by text.
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


src = (ROOT / 'SVOD_Churn_Attribution.py').read_text(encoding='utf-8')
start = src.index('def _displayed_total_show_watchers')
end = src.index('\n# =============', start)
ns = {}
exec(src[start:end], ns)
readback = ns['_displayed_total_show_watchers']

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / 'tracker.csv'
    p.write_text('Category,Episode Date,Count,Count Label,Secondary Count,Secondary Label,'
                 'Tertiary Count,Tertiary Label,Percentage,Gen Pop Projection\n'
                 ',,KEY METRICS,,,,,,,Gen Pop Projection\n'
                 'Total Show Watchers,,56747,,,,,,,"1,872,084"\n'
                 'New Platform Signups,,1509,,,,,,,"49,782"\n', encoding='utf-8')
    n = readback(p)
    check('reads the displayed Total Show Watchers (post-divisor)', n == 56747, n)
    check('the lattice off that count is the file projection', round(n / 1e7 * 329.9e6) == 1_872_084)
    check('a pre-divisor panel would have projected ten times the file', round(567_470 / 1e7 * 329.9e6) == 18_720_835)
    check('missing file reads 0 so the caller keeps its own value', readback(Path(td) / 'nope.csv') == 0)

ret = src[src.index('    result = {\n        "output_path":'):src.index('def _displayed_total_show_watchers')]
check('return still carries the total_panel key the worker reads', '"total_panel":       panel.get(\'total_panel\')' in ret)
check('return overrides total_panel with the read-back count', 'result["total_panel"] = _displayed' in ret)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
