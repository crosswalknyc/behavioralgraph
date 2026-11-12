#!/usr/bin/env python3
"""A whole-category answer served from the digest states the cross's
own people count (2026-11-11).

The category reach block a category ask is handed carries the people
behind the share (the Chime Banking Customers insurance read printed
392,312,555 before it did). The same category read off the profile
digest alone had a share and an index and no people: the answer worked
the count out from the rounded share, and it differed from the count a
cross of behaviors prints for the same category.

Holds:
  1. every category line in the digest carries the projected US people
     behind its share;
  2. that count is the one the category ask's block prints and the one
     a cross prints for the category on its own line, to the person;
  3. it is not the rounded share times the audience;
  4. it is sized in one pass (one rollup of the profile, however many
     categories);
  5. with no profile key the digest reads as it did, and the head says
     nothing about people.

Hermetic: an in-memory profile.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import pandas as pd  # noqa: E402

from prometheus import category_reach as CRE  # noqa: E402
from migration import behavior_crosstab as BX  # noqa: E402

FAILS = []


def check(msg, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f"  [{detail}]" if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


SAMPLE = 272_125
AUDIENCE = 8_977_404
KEY = 'Chime_Banking_Customers_05_12_2026_15_12.csv'


def profile():
    rows = [('BRAND INPUT', 'Chime', 100.0, SAMPLE, AUDIENCE),
            ('SAMPLE SIZE', 'SAMPLE SIZE (2025-01-01 TO 2025-12-31)', 100.0, SAMPLE, AUDIENCE),
            ('SUBJECT', 'Chime Banking Customers', 100.0, SAMPLE, AUDIENCE)]
    ins = (('GEICO', 26.7012), ('Progressive', 14.2093), ('Allstate', 9.8127), ('State Farm', 8.9034), ('Liberty Mutual', 4.1178),
           ('Farmers', 3.2291), ('Blue Cross Blue Shield', 2.0419), ('United Healthcare', 1.7733))
    shop = (('Walmart', 62.3381), ('Target', 41.0926), ('Costco', 22.6614))
    qsr = (('McDonalds', 48.2217), ('Taco Bell', 31.9043), ('Wendys', 27.5518), ('Chick-fil-A', 24.1172))
    for col, items in (('INSURANCE', ins), ('WHERE THEY SHOP', shop), ('QSR', qsr)):
        for b, p in items:
            raw = int(round(p / 100.0 * SAMPLE))
            rows.append((col, b, p, raw, int(round(raw / 10_000_000 * 329_900_000))))
    df = pd.DataFrame(rows, columns=['Column', 'Value', 'Brand Penetration (Row)', 'Raw', 'Projection'])
    df.attrs['s3_key'] = KEY
    gp = {('INSURANCE', CRE.cr.norm_brand(b)): v for b, v in (('GEICO', 22.1), ('Progressive', 13.9), ('Allstate', 9.1), ('State Farm', 12.2),
                                                    ('Liberty Mutual', 4.4), ('Farmers', 3.6), ('Blue Cross Blue Shield', 6.2),
                                                    ('United Healthcare', 5.1))}
    gp.update({('WHERE THEY SHOP', CRE.cr.norm_brand(b)): v for b, v in (('Walmart', 62.3), ('Target', 44.0), ('Costco', 27.5))})
    gp.update({('QSR', CRE.cr.norm_brand(b)): v for b, v in (('McDonalds', 44.1), ('Taco Bell', 24.6), ('Wendys', 22.9), ('Chick-fil-A', 26.3))})
    return df, gp


LINE = re.compile(r"(?:^|; )\s*([A-Z][A-Z /&+'-]*?) (\d+\.\d) \| (\d+\.\d) \| (\d+)(?: \| ([\d,]+))?")

print("[1] every category line carries the people behind its share")
df, gp = profile()
sec = CRE.digest_section(df, gp, 'Chime Banking Customers')
head, body = sec.split('\n', 1)
got = {m.group(1): m for m in LINE.finditer(body)}
check("the digest has its categories", set(got) == {'INSURANCE', 'QSR', 'WHERE THEY SHOP'}, str(sorted(got)))
check("each with a people count", all(m.group(5) for m in got.values()), body)
check("the head names the fourth figure and how to use it",
      'projected US people behind the share' in head and 'never a share multiplied by the audience' in head, head)

print("[2] it is the block's count and the cross's count, to the person")
ctx = BX.profile_context(df, gp)
for col, ask in (('INSURANCE', 'What share of Chime Banking Customers engage with insurance brands overall, and what is the index?'),
                 ('QSR', 'What share of this audience engage with QSR brands overall?')):
    people = int(got[col].group(5).replace(',', ''))
    one = BX.single(BX.category_condition(df, ctx, col), BX.sample_of(df), salt=KEY)
    check(f"{col}: the digest's {people:,} is the cross's own line", people == one['us'], f"{people} vs {one['us']}")
    blk = CRE.block(df, gp, ask)
    m = re.search(r"projected US people ([\d,]+)", blk)
    check(f"{col}: and the category ask's block", bool(m) and int(m.group(1).replace(',', '')) == people, blk[-200:])
    check(f"{col}: and people_for", CRE.people_for(df, gp, KEY, column=col) == people)
ins = BX.category_condition(df, ctx, 'INSURANCE')
tgt = BX.bind(df, 'Target', context=ctx)['condition']
res = BX.cross([ins, tgt], BX.sample_of(df), salt=KEY)
in_cross = next(c for c in res['conditions'] if c['key'] == ins['key'])
check("the same category inside a cross of two behaviors prints the same people",
      in_cross['us'] == int(got['INSURANCE'].group(5).replace(',', '')))

print("[3] it is not the rounded share times the audience")
people = int(got['INSURANCE'].group(5).replace(',', ''))
worked_out = int(round(float(got['INSURANCE'].group(2)) / 100.0 * AUDIENCE))
check("the count an answer would work out from the printed share differs", worked_out != people, f"{worked_out} vs {people}")
check("the printed count sits inside the audience and never ends in a zero", 0 < people < AUDIENCE and people % 10 != 0)

print("[4] one pass")
calls = {'n': 0}
_real = CRE.cr.category_rollups


def _counted(*a, **k):
    calls['n'] += 1
    return _real(*a, **k)


CRE.cr.category_rollups = _counted
BX._cr.category_rollups = _counted
try:
    CRE.digest_section(df, gp, 'Chime Banking Customers')
finally:
    CRE.cr.category_rollups = _real
    BX._cr.category_rollups = _real
check("the profile is rolled up at most twice for the whole section, not once per category", calls['n'] <= 2, str(calls['n']))

print("[5] no profile key: the digest reads as it did")
df2, gp2 = profile()
df2.attrs.pop('s3_key', None)
sec2 = CRE.digest_section(df2, gp2, 'Chime Banking Customers')
head2, body2 = sec2.split('\n', 1)
check("no people figures", not any(m.group(5) for m in LINE.finditer(body2)), body2)
check("and the head does not promise any", 'projected US people' not in head2)
check("a key passed in sizes them", '|' in CRE.digest_section(df2, gp2, 'Chime Banking Customers', s3_key=KEY).split('\n', 1)[1]
      and bool(CRE.category_people(df2, gp2, KEY, ['INSURANCE']).get('INSURANCE')))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all checks passed")
