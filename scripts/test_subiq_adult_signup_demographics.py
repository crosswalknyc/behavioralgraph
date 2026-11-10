#!/usr/bin/env python3
"""New sign-ups are account holders, and account holders are adults
(Grace Yin, Amazon, 2026-11-10). The Subscriber IQ tracker's
"DEMOGRAPHICS - New Signups" AGE block carries the under-18 band at 0 on
every build, with the adult bands renormalized through the shared
adult-universe helper. Hermetic: no network, no model calls.

a. the under-18 row goes to 0 (count, percentage, projection); adults sum
   to 100; counts re-sum to the block's total; projections follow counts
b. idempotent: a second pass changes nothing
c. subject-salted through the shared helper (the salt lives at 4dp)
d. a children's cut (the under-18 band is the universe) is left alone
e. the GENDER block and every row outside the AGE block are untouched
f. write_output calls the step after the alignment agent and before the
   final percentages are captured for the title anchor
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = Path(os.path.dirname(os.path.abspath(__file__)))
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import SVOD_Churn_Attribution as svod  # noqa: E402

PASS = FAIL = 0
COLS = ["Category", "Episode Date", "Count", "Count Label", "Secondary Count", "Secondary Label",
        "Tertiary Count", "Tertiary Label", "Percentage", "Gen Pop Projection"]


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {detail}')


def _row(cat='', count='', label='', pct='', gp=''):
    return (cat, '', count, label, '', '', '', '', pct, gp)


def fixture(age, gender, total):
    rows = [_row('New Platform Signups', total, '', '', f'{total * 32.99:,.0f}'), _row(),
            _row('', 'DEMOGRAPHICS - New Signups'), _row(), _row('AGE')]
    for label, (cnt, pct) in age.items():
        rows.append(_row(label, cnt, 'people', f'{pct:.2f}%', svod.format_gen_pop(svod.gen_pop_projection(cnt))))
    rows.append(_row())
    rows.append(_row('GENDER'))
    for label, (cnt, pct) in gender.items():
        rows.append(_row(label, cnt, 'people', f'{pct:.2f}%', svod.format_gen_pop(svod.gen_pop_projection(cnt))))
    return pd.DataFrame(rows, columns=COLS)


def age_block(df):
    out, on = {}, False
    for idx in df.index:
        cat = str(df.loc[idx, 'Category'] or '').strip()
        if cat == 'AGE' and not str(df.loc[idx, 'Percentage'] or '').strip():
            on = True
            continue
        if on:
            if not cat:
                break
            out[cat] = (int(df.loc[idx, 'Count']), float(str(df.loc[idx, 'Percentage']).rstrip('%')),
                        str(df.loc[idx, 'Gen Pop Projection']))
    return out


AGE = {'17 and Under': (86, 4.99), '18-24': (345, 20.02), '25-34': (431, 25.01), '35-44': (345, 20.02),
       '45-54': (258, 14.97), '55-64': (172, 9.98), '65 or Older': (69, 4.00), 'Other': (17, 0.99)}
GENDER = {'Male': (923, 53.57), 'Female': (765, 44.40), 'Non-Binary': (35, 2.03)}
TOTAL = 1723

print('== a. the under-18 band goes to 0 and the adults carry the block')
df = fixture(AGE, GENDER, TOTAL)
before_gender = df[df['Category'].isin(GENDER)].copy()
out, changes = svod.enforce_adult_signup_demographics(df.copy(), subject='The Paper')
blk = age_block(out)
check('one change recorded', len(changes) == 1, changes)
check('under-18 count is 0', blk['17 and Under'][0] == 0, blk['17 and Under'])
check('under-18 percentage is 0.00', blk['17 and Under'][1] == 0.0, blk['17 and Under'])
check('under-18 projection is 0', blk['17 and Under'][2] == '0', blk['17 and Under'])
adults = {k: v for k, v in blk.items() if k != '17 and Under'}
check('adult percentages sum to 100 (2dp)', abs(sum(v[1] for v in adults.values()) - 100.0) <= 0.05,
      sum(v[1] for v in adults.values()))
check('counts re-sum to the block total', sum(v[0] for v in blk.values()) == TOTAL, sum(v[0] for v in blk.values()))
check('no adult band on a .X0 boundary', all(int(round(v[1] * 100)) % 10 for v in adults.values()), adults)
check('order of the bands held (25-34 still leads)', max(adults, key=lambda k: adults[k][1]) == '25-34', adults)
check('projection follows the count', all(v[2] == svod.format_gen_pop(svod.gen_pop_projection(v[0])) for v in blk.values()), blk)

print('== b. idempotent')
out2, changes2 = svod.enforce_adult_signup_demographics(out.copy(), subject='The Paper')
check('second pass records no change', changes2 == [], changes2)
check('second pass leaves the block as it was', age_block(out2) == blk)

print('== c. subject-salted through the shared helper')
# the salt lives at 4dp in adult_universe (the file prints 2dp): two
# titles get distinct renormalized bands from the same input
from adult_universe import zero_minor_age_distribution as _zmad  # noqa: E402
_a, _ = _zmad({k: v[1] for k, v in AGE.items()}, 'The Paper')
_b, _ = _zmad({k: v[1] for k, v in AGE.items()}, 'Another Title')
check('two titles do not share the 4dp renormalized shape', _a != _b, (_a, _b))
check('both are adults only', _a['17 and Under'] == 0.0 and _b['17 and Under'] == 0.0)

print('== d. a children\'s cut keeps its band')
kids_age = {'17 and Under': (900, 60.0), '18-24': (300, 20.0), '25-34': (150, 10.0), '35-44': (150, 10.0)}
outk, changesk = svod.enforce_adult_signup_demographics(fixture(kids_age, GENDER, 1500), subject='Kids Title')
check('no change on a universe defined by minors', changesk == [] and age_block(outk)['17 and Under'][0] == 900)

print('== e. everything outside the AGE block is untouched')
after_gender = out[out['Category'].isin(GENDER)]
check('GENDER rows unchanged', after_gender.reset_index(drop=True).equals(before_gender.reset_index(drop=True)))
check('New Platform Signups row unchanged', str(out.loc[0, 'Count']) == str(TOTAL))
no_demo = pd.DataFrame([_row('Total Show Watchers', 100, '', '', '3,299')], columns=COLS)
outn, changesn = svod.enforce_adult_signup_demographics(no_demo, subject='X')
check('a frame without a demographics block passes through', changesn == [] and outn.equals(no_demo))

print('== f. wired into write_output in the right place')
src = open(ROOT / 'SVOD_Churn_Attribution.py', encoding='utf-8').read()
wo = src[src.index('def write_output('):]
i_align = wo.index('ai_align_final_demographics_with_research(df_out')
i_call = wo.index('enforce_adult_signup_demographics(')
i_final = wo.index("p['_final_demo_pcts']")
check('the step runs after the alignment agent and before the anchor percentages are captured',
      i_align < i_call < i_final, (i_align, i_call, i_final))
check('the step runs whether or not demographics are locked',
      re.search(r"\n    df_out, _adult_changes = enforce_adult_signup_demographics\(", wo) is not None)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
