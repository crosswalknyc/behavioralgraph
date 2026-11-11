#!/usr/bin/env python3
"""A peer norm whose 90th percentile is 0.0 never breaks a profile digest.

2026-11-11: 28 profiles (M3GAN, King Richard, Girls Trip, Protein
Enthusiasts and their cuts, ...) failed the nightly index build with
"ZeroDivisionError: float division by zero", and the live digest path
runs the same function. The row is the rarest kind of signal (nine in
ten peers do not carry it), so it leads the list.

Hermetic: no network, no S3, no model call.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import prometheus_analysis as pma  # noqa: E402

FAILS = []


def check(cond, label, detail=''):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  ({str(detail)[:220]})" if detail and not cond else ''))
    if not cond:
        FAILS.append(label)


# norms[group][category][brand] = [n, med_pen, p90_pen, max_pen, med_idx, p90_idx, max_idx, max_profile]
norms = {'n_profiles': 40, 'groups': {'MOVIE': 12},
         'norms': {'MOVIE': {'QSR': {pma._norm_brand('Rare Burger'): [12, 0.0, 0.0, 4.2, None, None, None, 'Some Film'],
                                     pma._norm_brand('Known Burger'): [12, 6.0, 9.0, 14.0, None, None, None, 'Other Film']}}}}
meta = {'brand_category': 'MOVIE', 'name': 'Test Film'}
beh = [('QSR', 'Rare Burger', 5.5), ('QSR', 'Known Burger', 12.0)]
try:
    out = pma._peer_norms_section(meta, {}, norms, [], beh)
    raised = None
except Exception as e:      # the defect
    out, raised = [], e
check(raised is None, 'a zero peer p90 does not raise', raised)
text = '\n'.join(out)
check('QSR / Rare Burger: pen 5.5 vs peers med 0.0, p90 0.0, max 4.2' in text, 'the row is reported with its peers', text)
check(text.index('Rare Burger') < text.index('Known Burger'), 'and it leads the rarest signals', text)
check('inf' not in text.lower() and 'nan' not in text.lower(), 'no inf or nan reaches the digest', text)

print()
if FAILS:
    print(f"FAILED ({len(FAILS)}):")
    for f in FAILS:
        print('  -', f)
    sys.exit(1)
print('ALL PASS')
