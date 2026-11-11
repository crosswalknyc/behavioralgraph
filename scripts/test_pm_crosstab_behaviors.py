#!/usr/bin/env python3
"""A cross of behaviors inside one audience (Jenna 2026-11-11, feedback
she forwarded: "The cross-tab functionality, open up to across
behaviors. between behavior A, B and C... For example the Chime users
who shop at Target and have an interest in X."), then, the same day:
"Build it on the page, free for anyone with the profile."

  [1] the one law (migration/behavior_ties.py): inside the bounds for
      any shares, the same for any order, the same on both sides
  [2] nested: A and B and C <= A and B <= A, shares and counts; a figure
      on the record stands and bounds every new one
  [3] binding on a real profile: the brand in the category the verb
      points at, an interest row, a compound row, a demographic bucket,
      a pick by its exact column; a behavior the profile does not carry
      is missing with its nearest rows, never substituted
  [4] the words: a cross is a question; an order (build / run / create a
      profile or a cut) is never this lane; other reads keep their lanes
  [5] chat is free when every behavior is a row on a profile the reader
      can open: no quote, no Approve, no charge, the answer now; a
      reader who cannot open the profile keeps the standing quote; a
      missing row is said plainly
  [6] the record: a repeat is the same figure in any order; the page and
      the chat state the same figure to the person, whichever asked
      first; a page read goes on the record like a chat read
  [7] with the category reach: for any two rows either + both = A + B,
      to the person; a whole category crossed with one of its own rows
      is that row; a whole category's reach is what its rows' crosses
      imply; the reach of every ordinary category is unchanged
  [8] Telecom and Insurance read as they always did (Jenna 2026-11-11:
      the reach is anchored low; the either/or list is empty): the two
      categories' reach is the fixed-association figure, two carriers
      add up with their two-brand reach and with the category, a group
      inside a category never passes it, adding a brand never lowers it;
      the either/or part of the law still holds its own arithmetic
  [9] the split of a base across two services ("on A, on B, both,
      neither"): a figure on the record keeps winning
  [10] the page: options, picks by column, duplicate / missing / one
       pick / a brand beside its own category / a profile with no
       interest rows; the export in the Profile CSV's columns
  [11] the text a reader sees carries no banned word

Hermetic: a trimmed copy of the real Chime Banking Customers profile and
the matching US rows stand in for S3; the record is an in-memory store;
no model; no network.
Run: /opt/homebrew/bin/python3.11 scripts/test_pm_crosstab_behaviors.py
"""
from __future__ import annotations

import itertools
import json
import os
import random
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BGW = os.path.dirname(HERE)
sys.path.insert(0, BGW)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')

import pandas as pd  # noqa: E402

from migration import behavior_crosstab as bx  # noqa: E402
from migration import behavior_ties as ties  # noqa: E402
from migration import category_rollup as cr  # noqa: E402
from prometheus import crosstab as xt  # noqa: E402
from prometheus import cross_sizing as cx  # noqa: E402
from prometheus import priced_read as pr  # noqa: E402
from prometheus import sizing_lane as sl  # noqa: E402
from prometheus import brand_coverage as bc  # noqa: E402
from prometheus import orders as orders  # noqa: E402

FAILS = []


def check(cond, msg, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + msg + (f'  ({str(detail)[:260]})' if (detail and not cond) else ''))
    if not cond:
        FAILS.append(msg)


# ------------------------------------------------------------ fixtures
FIX = os.path.join(HERE, 'fixtures')
with open(os.path.join(FIX, 'crosstab_chime_profile.json'), encoding='utf-8') as _fh:
    _FX = json.load(_fh)


def _frame_of(part):
    df = pd.DataFrame(part['rows'], columns=part['columns'])
    for col in df.columns:
        if col not in ('Column', 'Value'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


CHIME_KEY = _FX['profile_key']
CHIME = _frame_of(_FX['profile'])
GENPOP = _frame_of(_FX['genpop'])
NO_INTERESTS_KEY = 'No_Interest_Rows_01_01_2026_00_00.csv'
NO_INTERESTS = CHIME[CHIME['Column'] != 'INTEREST'].reset_index(drop=True)
FRAMES = {CHIME_KEY: CHIME, 'Gen_Pop_2026.csv': GENPOP, NO_INTERESTS_KEY: NO_INTERESTS}
bc._frame = lambda host, key: FRAMES[key]
SAMPLE = 272_125


def fake_resolve(phrase, ctx=None):
    k = sl.strip_audience_nouns(str(phrase or '').strip()).lower() or str(phrase or '').strip().lower()
    if k in ('chime', 'chime banking', 'chime banking customers'):
        return {'name': 'Chime Banking Customers', 'sample': SAMPLE, 'us': round(SAMPLE * 32.99),
                'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'key': CHIME_KEY, 'matched': phrase}
    return None


sl.resolve = fake_resolve
xt._catalog_bases = lambda bare: []      # the library is stubbed per case below
CHARGES = []


def make_host(can_open=True):
    return types.SimpleNamespace(
        s3_client=None, bucket='dashboard-inputs',
        has=lambda n: n in ('consume_credit', 'can_open_profile'),
        consume_credit=lambda u, **kw: CHARGES.append((u, kw)) or True,
        can_open_profile=lambda user, key: bool(can_open))


HOST = make_host(True)             # a reader who can open the profile
HOST_SHUT = make_host(False)       # a reader who cannot
BANK = {}
xt._BANK_HOOK['get'] = lambda key: BANK.get(key)
xt._BANK_HOOK['put'] = lambda key, doc: BANK.__setitem__(key, doc)
PAYING = {'username': 'pat', 'email': 'pat@client.com', 'company': 'Client Co', 'role': 'user', 'credits': 5000}
PAGE = {'primary': {'name': 'Chime Banking Customers', 's3_key': CHIME_KEY}, 'cuts': []}
GP = bx.genpop_map(GENPOP)
CTX = bx.profile_context(CHIME, GP)

T_MISSING = "Chime users who shop at Target and are interested in home improvement"
T_PAIR = "Chime users who shop at Target and are interested in technology"
T_TRIPLE = "of Chime users, how many shop at Target, use Hulu and have an interest in technology"
T_TRIPLE_REORDERED = "how many Chime users have an interest in technology, use Hulu and shop at Target?"
T_PAGE = "cross Target shoppers with Netflix and Costco in this profile"

# ------------------------------------------------------------ [1] the one law
print("[1] the one law holds its bounds and does not care about order")
rng = random.Random(20261111)
worst = 0.0
for _ in range(300):
    k = rng.choice((2, 2, 3, 3, 4, 5))
    rows = []
    for i in range(k):
        p = rng.choice((rng.uniform(0.001, 0.2), rng.uniform(0.2, 0.9), rng.uniform(0.9, 0.9999)))
        kind = rng.choice(('brand', 'brand', 'soft', 'pick'))
        rows.append(ties.Row(p, kind, ties.soft_loading(rng.choice((40, 100, 180, 260, 400, 900))), 'TELECOM' if kind == 'pick' else 'X', 1.5))
    groups = [{i: r} for i, r in enumerate(rows)]
    j = bx.all_share(groups)
    ps = [r.share for r in rows]
    lo, hi = max(0.0, sum(ps) - (k - 1)), min(ps)
    if not (lo - 1e-12 <= j <= hi + 1e-12):
        check(False, "a group left its bounds", (rows, j))
        break
    shuffled = list(groups)
    rng.shuffle(shuffled)
    worst = max(worst, abs(j - bx.all_share(shuffled)))
else:
    check(True, "300 random groups of brand, soft and either/or rows: never above the smallest share, never below max(0, sum - (k - 1))")
check(worst < 1e-12, "the same figure for any order the behaviors are given in", worst)
check(ties.soft_loading(100) == 0.0 and ties.soft_loading(60) == 0.0 and ties.soft_loading(None) == 0.0, "an interest at or under the US average is not tied")
check(0 < ties.soft_loading(150) < ties.soft_loading(250) < ties.soft_loading(300) == ties.A_MAX == ties.soft_loading(2000), "the tie rises with the index and stops at the cap")
check(cr.RHO == ties.RHO and abs(cr.CAP - ties.CAP * 100) < 1e-9, "one set of tie parameters: the category reach reads RHO and the cap from the one law")
for ps in ([30.0, 12.0], [84.4, 30.3], [5.0, 2.0, 1.0], [71.7, 53.1, 49.7, 46.4], [99.1, 94.8, 84.4]):
    mine = 100.0 * ties.reach_share([ties.Row(p / 100.0, 'brand') for p in ps])
    check(abs(mine - cr.union_reach(ps)) < 1e-6, f"brand rows {ps}: the law's reach is the category reach's own figure", (mine, cr.union_reach(ps)))
check(abs(bx.joint([0.3, 0.4], [0.0, 0.0]) - 0.12) < 1e-9, "rows with no tie sit on the multiplied figure")
check(bx.times_of(213) == '2.1x the US average' and bx.times_of(101) == 'in line with the US average', "the plain comparison beside an index")

# ------------------------------------------------------------ [3] binding
print("[3] each behavior binds to its row on the profile")


def bind(name, hint='', whole=False, column=None):
    return bx.bind(CHIME, name, hint=hint, whole=whole, context=CTX, column=column)


b_target = bind('Target', 'shop')
check(b_target['status'] == 'bound' and b_target['condition']['column'] == 'WHERE THEY SHOP' and abs(b_target['condition']['pct'] - 84.4259) < 1e-6
      and b_target['condition']['law'] == 'brand', "shop at Target is the Where They Shop row, 84.4259, a brand row", b_target)
check(abs(b_target['condition']['gp'] - 29.6143) < 1e-6, "its US share comes from the US file's own row", b_target)
b_tech = bind('technology', 'interest')
check(b_tech['status'] == 'bound' and b_tech['condition']['kind'] == 'interest' and b_tech['condition']['law'] == 'soft', "an interest row binds and ties by its index", b_tech)
b_hulu = bind('Hulu', 'use')
check(b_hulu['status'] == 'bound' and b_hulu['condition']['value'] == 'Disney+/Hulu', "Hulu answers to the compound row", b_hulu)
b_netflix, b_costco = bind('Netflix', 'use'), bind('Costco', 'shop')
check(bind('Target shoppers')['status'] == 'bound' and bind('Target shoppers')['condition']['key'] == b_target['condition']['key'], "the audience noun rides along")
b_women = bind('women', 'demo')
check(b_women['status'] == 'bound' and b_women['condition']['kind'] == 'demo' and b_women['condition']['column'] == 'GENDER', "a demographic bucket by its plain name", b_women)
check(bind('aged 18 to 24', 'demo')['condition']['value'] == '18-24', "an age bucket")
b_miss = bind('home improvement', 'interest')
check(b_miss['status'] == 'missing' and [n['value'] for n in b_miss['nearest']][:2] == ['BUILDING/DIY', 'HOME & LIVING'],
      "home improvement is not a row: missing, with Building/DIY and Home & Living as the nearest", b_miss)
check(bind('Zzyzx Qwvrt')['status'] == 'missing' and bind('Zzyzx Qwvrt')['nearest'] == [], "a name nothing is close to is plainly missing")
check(bind('TARGET', column='WHERE THEY SHOP')['condition']['key'] == b_target['condition']['key'], "a pick by its exact column is the same row")
check(bind('TARGET', column='QSR')['status'] == 'missing', "a pick in a column that does not carry it is missing, never another column's row")
check(bx.sample_of(CHIME) == SAMPLE and bx.window_of(CHIME).get('label') == 'Jan 1, 2025 to Dec 31, 2025', "the sample and the window come off the file")

# ------------------------------------------------------------ [2] nesting
print("[2] nested groups stay inside one another")
conds = [b_target['condition'], b_tech['condition'], b_hulu['condition']]
r3 = bx.cross(conds, SAMPLE, salt=CHIME_KEY)
keys = [c['key'] for c in r3['conditions']]
singles = {c['key']: c for c in r3['conditions']}
ok = True
for size in (2, 3):
    for combo in itertools.combinations(keys, size):
        cell = r3['cells'][bx.set_key(combo)]
        for k in combo:
            parent = singles[k] if size == 2 else r3['cells'][bx.set_key([x for x in combo if x != k])]
            ok = ok and cell['share'] <= parent['share'] + 1e-12 and cell['raw'] <= parent['raw'] and cell['us'] <= parent['us']
check(ok, "A and B and C <= every pair <= every single, shares and counts")
check(all(c['raw'] % 10 != 0 and c['us'] % 10 != 0 for c in r3['cells'].values()), "no count the read works out ends in a zero")
t3 = r3['cells'][bx.set_key(keys)]
pairs = [r3['cells'][bx.set_key(c)]['share'] for c in itertools.combinations(keys, 2)]
check(t3['share'] <= 1 - sum(singles[k]['share'] for k in keys) + sum(pairs) + 1e-12, "the three-way figure leaves room for everyone else")
for order in itertools.permutations(conds):
    rr = bx.cross(list(order), SAMPLE, salt=CHIME_KEY)
    if rr['key'] != r3['key'] or rr['share'] != r3['share'] or rr['us'] != r3['us'] or rr['index'] != r3['index']:
        check(False, "order changed the read", [c['label'] for c in order])
        break
else:
    check(True, "six orders of the same three behaviors: one key, one share, one US figure, one index")
r2 = bx.cross([b_target['condition'], b_tech['condition']], SAMPLE, salt=CHIME_KEY)
check(r2['share'] == r3['cells'][r2['key']]['share'] and r2['us'] == r3['cells'][r2['key']]['us'], "the pair on its own is the pair inside the triple")
check(r2['unrelated'] < r2['share'] < 0.844259, "Target and Technology (both far above the US average) sit above the multiplied figure, under the smaller share")
gp_unrelated = 0.296143 * (b_tech['condition']['gp'] / 100.0)
check(r2['gp'] > gp_unrelated and r2['index'] < round(0.844259 * 0.889308 / gp_unrelated * 100),
      "the same law runs on the US side, so the index is lower than the two indexes multiplied", (r2['gp'], r2['index']))
check(abs(r2['raw'] - r2['share'] * SAMPLE) < 100, "people in the data = share x the profile's sample")
check(abs(r2['us'] - r2['raw'] / 10_000_000 * 329_900_000) < 200, "US people = that / 10,000,000 x 329,900,000")
held = {r2['key']: {'share': 0.74, 'gp': 0.12}}
r3h = bx.cross(conds, SAMPLE, held=held, salt=CHIME_KEY)
check(abs(r3h['cells'][r2['key']]['share'] - 0.74) < 1e-12 and r3h['cells'][r2['key']]['on_record'], "a pair on the record stands")
check(r3h['share'] <= 0.74 + 1e-12, "the triple never exceeds the banked pair", r3h['share'])
held_up = {r3['key']: {'share': min(c['share'] for c in r3['conditions']) * 0.97, 'gp': None}}
r2h = bx.cross([b_target['condition'], b_tech['condition']], SAMPLE, held=held_up, salt=CHIME_KEY)
check(r2h['share'] >= held_up[r3['key']]['share'] - 1e-12, "a pair asked later never falls below a triple on the record", (r2h['share'], held_up))
check(bx.cross([b_target['condition']], SAMPLE) is None and bx.cross([b_target['condition'], dict(b_target['condition'])], SAMPLE) is None,
      "one behavior, or the same one twice, is not a cross")

# ------------------------------------------------------------ [7] with the category reach
print("[7] the cross and the category reach add up")
for a, b in ((b_target, b_costco), (b_netflix, b_hulu), (b_target, b_netflix), (bind('McDonalds', 'eat'), b_costco)):
    ca, cb = a['condition'], b['condition']
    rr = bx.cross([ca, cb], SAMPLE, salt=CHIME_KEY)
    either_pct = cr.union_reach([ca['pct'], cb['pct']])
    check(abs(rr['share'] * 100 + either_pct - ca['pct'] - cb['pct']) < 1e-6,
          f"{ca['label']} and {cb['label']}: either (category reach) + both (cross) = A + B", (rr['share'] * 100, either_pct))
    e = rr['cells'][rr['key']]['either']
    sa, sb = (next(c for c in rr['conditions'] if c['key'] == x['key']) for x in (ca, cb))
    check(e['us'] + rr['us'] == sa['us'] + sb['us'] and e['raw'] + rr['raw'] == sa['raw'] + sb['raw'] and e['us'] % 10 and e['raw'] % 10
          and rr['us'] % 10 and rr['raw'] % 10,
          f"{ca['label']} and {cb['label']}: either + both = A + B to the person, and no count ends in a zero", (e, rr['us']))
    cl = cr.cluster_rollup(CHIME, GP, [ca['value'], cb['value']])
    check(cl and abs(cl['reach'] - round(e['share'] * 100, 1)) < 0.051, f"{ca['label']} or {cb['label']}: the reach of the two-brand group is the cross's either", (cl and cl['reach'], e['share']))
# three rows of one category: the reach is what the crosses imply (inclusion and exclusion)
tri = [b_target['condition'], b_costco['condition'], bind('Walmart', 'shop')['condition']]
rt = bx.cross(tri, SAMPLE, salt=CHIME_KEY)
implied = sum(c['share'] for c in rt['conditions']) - sum(c['share'] for c in rt['cells'].values() if len(c['members']) == 2) + rt['share']
check(abs(implied * 100 - cr.union_reach([c['pct'] for c in tri])) < 1e-6, "three retailers: singles - pairs + all three is the reach of the three",
      (implied * 100, cr.union_reach([c['pct'] for c in tri])))
any_qsr = bind('QSR', 'eat', whole=True)
roll_qsr = cr.category_rollups(CHIME, GP)['QSR']
check(any_qsr['status'] == 'bound' and any_qsr['condition']['kind'] == 'category' and any_qsr['condition']['pct'] == roll_qsr['reach'],
      "any QSR is the category reach's own figure", (any_qsr.get('condition', {}).get('pct'), roll_qsr['reach']))
mcd = bind('McDonalds', 'eat')['condition']
rq = bx.cross([any_qsr['condition'], mcd], SAMPLE, salt=CHIME_KEY)
smcd = next(c for c in rq['conditions'] if c['key'] == mcd['key'])
check(abs(rq['share'] - mcd['pct'] / 100) < 1e-9 and rq['us'] == smcd['us'], "any QSR crossed with McDonalds is McDonalds, to the person", (rq['share'], rq['us'], smcd['us']))
sq = next(c for c in rq['conditions'] if c['kind'] == 'category')
check(sq['index'] == roll_qsr['index'] and round(sq['share'] * 100, 1) == roll_qsr['reach'], "and its single line prints the category reach's percent and index", (sq['index'], roll_qsr['index']))
rn = bx.cross([any_qsr['condition'], b_netflix['condition']], SAMPLE, salt=CHIME_KEY)
qsr_rows = cr.profile_rows(CHIME)['QSR']
with_netflix = cr.union_reach([p for _, p in qsr_rows] + [b_netflix['condition']['pct']])
check(abs(rn['share'] * 100 - (cr.union_reach([p for _, p in qsr_rows]) + b_netflix['condition']['pct'] - with_netflix)) < 1e-6,
      "any QSR and Netflix = reach(QSR) + Netflix - reach(QSR with Netflix)", rn['share'])
# the reach of every ordinary category is what it was before the one law
before = {'QSR': None}
moved = []
for col, r in cr.category_rollups(CHIME, GP).items():
    plain = round(cr.union_reach([p for _, p in cr.profile_rows(CHIME)[col]]), 1)
    if r['reach'] != plain:
        moved.append(col)
check(not moved, "every category's reach is the fixed-association figure, Telecom and Insurance included", moved)

# ------------------------------------------------------------ [8] Telecom and Insurance read low
print("[8] Telecom and Insurance read as they always did")
check(ties.EITHER_OR_COLS == frozenset(), "the either/or list is empty: no column raises its category's reach", ties.EITHER_OR_COLS)
check(not ties.is_either_or('TELECOM') and not ties.is_either_or('INSURANCE'), "Telecom and Insurance are ordinary brand columns")
tm, vz = bind('T-Mobile')['condition'], bind('Verizon')['condition']
check(tm['law'] == 'brand' and vz['law'] == 'brand', "two carriers bind as ordinary brand rows", (tm['law'], vz['law']))
rolls = cr.category_rollups(CHIME, GP)
for col in ('TELECOM', 'INSURANCE'):
    rows_c = cr.profile_rows(CHIME)[col]
    plain = round(cr.union_reach([p for _, p in rows_c]), 1)
    check(rolls[col]['reach'] == plain, f"{col}: the category's reach is the fixed-association figure", (rolls[col]['reach'], plain))
    # a group inside the category never passes it, and adding a brand never lowers the reach
    names = [b for b, _ in sorted(rows_c, key=lambda r: -r[1])]
    last, ok_up, ok_in = 0.0, True, True
    for n in range(1, len(names) + 1):
        g = cr.cluster_rollup(CHIME, GP, names[:n], prefer=(col,))
        ok_up = ok_up and g['reach'] >= last
        ok_in = ok_in and g['reach'] <= rolls[col]['reach']
        last = g['reach']
    check(ok_up, f"{col}: adding a brand never lowers the reach")
    check(ok_in and last == rolls[col]['reach'], f"{col}: a group of its brands never reads above the category, and all of them is the category", (last, rolls[col]['reach']))
rc = bx.cross([tm, vz], SAMPLE, salt=CHIME_KEY)
either_tv = cr.union_reach([tm['pct'], vz['pct']])
check(abs(rc['share'] * 100 + either_tv - tm['pct'] - vz['pct']) < 1e-6, "T-Mobile and Verizon: either (category reach) + both (cross) = A + B", (rc['share'], either_tv))
e_tv = rc['cells'][rc['key']]['either']
s_tm, s_vz = (next(c for c in rc['conditions'] if c['key'] == x['key']) for x in (tm, vz))
check(e_tv['us'] + rc['us'] == s_tm['us'] + s_vz['us'] and e_tv['raw'] + rc['raw'] == s_tm['raw'] + s_vz['raw'],
      "and to the person", (e_tv, rc['us']))
cl = cr.cluster_rollup(CHIME, GP, ['T-Mobile', 'Verizon'])
check(abs(cl['reach'] - round(e_tv['share'] * 100, 1)) < 0.051 and cl['reach'] <= rolls['TELECOM']['reach'],
      "either carrier is the two-brand reach, and it sits inside Telecom", (cl['reach'], rolls['TELECOM']['reach']))
check(rc['share'] * 100 >= tm['pct'] + vz['pct'] - rolls['TELECOM']['reach'] - 0.051,
      "with Telecom's reach held low, both carriers is at least A + B - Telecom", (rc['share'] * 100, tm['pct'] + vz['pct'] - rolls['TELECOM']['reach']))
check(rc['share'] > rc['unrelated'], "so two carriers read like any two brands of one category, above the multiplied figure", (rc['share'], rc['unrelated']))
geico = bind('Geico')['condition']
rg = bx.cross([tm, geico], SAMPLE, salt=CHIME_KEY)
either_tg = cr.union_reach([tm['pct'], geico['pct']])
check(abs(rg['share'] * 100 + either_tg - tm['pct'] - geico['pct']) < 1e-6, "a carrier and an insurer add up the same way", (rg['share'], either_tg))
# the either/or part of the law, kept for a column Jenna may add later, holds its own arithmetic
h, look = ties.pick_parts(0.31, 1.86)
check(abs(h - ties.PICK * 0.31) < 1e-12 and abs(h + (1 - h) * look - 0.31) < 1e-12, "a row's pick and its looking around add back to its own share")
h2, _ = ties.pick_parts(0.31, 5.0)
check(h2 < ties.PICK * 0.31 and ties.PICK * 5.0 * (h2 / (ties.PICK * 0.31)) <= ties.PICK_MAX + 1e-9, "the picks in a column never add past the cap")

# ------------------------------------------------------------ [4] the words
print("[4] a cross is a question; an order is an order")
for t, n in ((T_MISSING, 2), (T_PAIR, 2), (T_TRIPLE, 3), (T_TRIPLE_REORDERED, 3), (T_PAGE, 3),
             ("of Wicked Tuna viewers, how many use Hulu and drive a Ford", 2),
             ("what share of this audience shops at Target and has an interest in technology", 2),
             ("how many of these viewers drive a Ford, use Hulu and are aged 25 to 34", 3),
             ("female Chime users who shop at Target and use Hulu", 3)):
    q = xt.parse(t, PAGE)
    check(bool(q) and len(q['behaviors']) == n and not orders.is_order(t), f"a cross of {n}: {t[:64]!r}", q)
q = xt.parse("of Wicked Tuna viewers, how many use Hulu and drive a Ford")
check(q and q['audience'] == 'Wicked Tuna viewers' and [(b['name'], b['hint']) for b in q['behaviors']] == [('Hulu', 'use'), ('Ford', 'drive')],
      "the audience and each behavior with its verb", q)
check(xt.parse(T_PAGE)['page'] is True and xt.parse(T_PAGE)['audience'] == '', "'in this profile' points at the page")
for t in ("Build a cut of Chime Banking Customers who shop at Target and have an interest in Technology",
          "build a profile of Chime users who shop at Target and use Hulu",
          "create a cut: Chime users who shop at Target and are interested in technology",
          "run a cut of this audience for Target shoppers who use Hulu"):
    check(xt.parse(t, PAGE) is None and orders.is_order(t) and pr.classify(t, [], PAGE) is None, f"an order, never this lane: {t[:60]!r}")
for t in ("Claire's shoppers that also shop at Target, Walmart and Kohl's",
          "how many people watched Landman and Yellowstone", "how many people use Netflix and Hulu",
          "people who shop at Target and use Hulu", "how big is the Chime audience", "Chime users who shop at Target",
          "What else stands out about Chime Banking Customers who shop at Target and have an interest in Technology?",
          "tell me about Chime users who shop at Target and use Hulu",
          "how many UFC fans are there out there, of those, how many are on Paramount+, on HBO Max and how many have neither service",
          "what percent of the audience is female and what percent is male", "which brands do Chime users who shop at Target buy",
          "Of those viewers who watched Stranger Things in November and December 2025, how many also watched Peacock?",
          "What are other titles watched by people who watched Rick and Morty on Max?"):
    check(xt.parse(t, PAGE) is None, f"not a cross of behaviors: {t[:64]!r}")
check((pr.classify("how many shop at Target and use Hulu", [], {}) or {}).get('frame') != 'crosstab',
      "with no audience named and no profile open there is nothing to cross")
check((pr.classify("how many shop at Target and use Hulu", [], PAGE) or {}).get('frame') == 'crosstab', "with a profile open the bare question points at it")
sub = "Of those viewers who watch Law & Order: SVU on Peacock, how many subscribe to Netflix and pay for Hulu?"
check((pr.classify(sub, [], PAGE) or {}).get('frame') != 'crosstab', "a subscribe overlap keeps its own lane and its own price")
no_prof = "of Wicked Tuna viewers, how many use Hulu and drive a Ford"
check((pr.classify(no_prof, [], {}) or {}).get('frame') == 'crosstab', "named audience: a cross until the profile is looked for")
check(xt.plan(no_prof, {}, host=HOST) is None, "no profile for the audience: no plan")
xt.decline(no_prof)
after = pr.classify(no_prof, [], {})
check((after or {}).get('frame') != 'crosstab' and (after or {}).get('usd') == 100, "an audience with no profile keeps its standing lane and price", after)
xt.decline('')

# ------------------------------------------------------------ [5] chat is free on a profile the reader can open
print("[5] Chime users x Target x an interest in chat: free, no quote, no Approve")
out = pr.service_turn(T_MISSING, user=PAYING, ctx={}, history=[], host=HOST)
r_missing = (out or {}).get('reply') or ''
print('    ' + r_missing.replace('\n', '\n    '))
check(out and not out.get('panel_offer') and out.get('crosstab_unbound'), "a row the profile does not carry: said plainly")
check('Home improvement is not a row on the Chime Banking Customers profile' in r_missing and 'Building/DIY' in r_missing and 'Home & Living' in r_missing,
      "names what is missing and offers the nearest rows", r_missing)
check('$' not in r_missing and any('Building/DIY' in c for c in out.get('followups') or []), "no price; a chip asks again with the nearest row", out.get('followups'))
check(not BANK, "nothing goes on the record for a row that did not bind")

CHARGES.clear()
ans = pr.service_turn(T_PAIR, user=PAYING, ctx={}, history=[], host=HOST)
ra = (ans or {}).get('reply') or ''
print('    ' + ra.replace('\n', '\n    '))
ct = (ans or {}).get('crosstab') or {}
check(ans and ans.get('free_read') and not ans.get('panel_offer') and not ans.get('priced_paid') and not CHARGES,
      "a paying seat who can open the profile: answered now, no offer, no charge", (ans and ans.keys(), CHARGES))
check('$' not in ra and 'Approve' not in ra and 'Pulling this data' not in ra and not any(c in ('Approve', 'Cancel') for c in ans.get('followups') or []),
      "no price, no Approve, no Cancel anywhere in the answer", ans.get('followups'))
check(ra.startswith('On Chime Banking Customers, Jan 1, 2025 to Dec 31, 2025:'), "opens on the audience and its window", ra[:80])
check(f"{ct.get('us'):,} US people in this audience shop at Target and have an interest in Technology" in ra, "the size in US people", ra)
check(f"That is {ct.get('share_pct'):.1f}% of the audience" in ra, "the share of the audience")
check(f"index {ct.get('index')} ({ct.get('times')})" in ra and ct.get('times', '').endswith('x the US average'), "the index with its plain comparison", ct)
check('- Shop at Target: 84.4% of the audience, 7,579,255 US people, index 285 (2.9x the US average)' in ra, "the single shares beside it, each with its index", ra)
check('- Have an interest in Technology: 88.9% of the audience' in ra, "the interest row beside it")
check('cut of the audience' in ra and 'deck page' in ra and ans['followups'] and ans['followups'][0].startswith('Build a cut of Chime Banking Customers who'),
      "offers the cut and the deck page", ans.get('followups'))
check(orders.is_order(ans['followups'][0]) and len(ans['followups']) == 1, "the one chip is the cut, an order the paid build flow takes")
check(75.0 < ct['share_pct'] < 84.4 and ct['us'] < 7_579_255 and ct['in_data'] < 229_744, "the group sits inside both single rows", ct)
check(abs(ct['us'] - ct['in_data'] / 10_000_000 * 329_900_000) < 200 and ct['us'] % 10 and ct['in_data'] % 10, "house count math, no trailing zero", ct)
check(ct['share_pct'] == round(r2['share'] * 100, 2) and ct['index'] == r2['index'] and ct['us'] == r2['us'], "the lane's figure is the engine's figure")
check(len(((BANK.get(CHIME_KEY) or {}).get('cells') or {})) == 1, "and it is on the record")

# a reader who cannot open the profile keeps the standing quote, then the same figure on Approve
BANK.clear()
CHARGES.clear()
off = pr.service_turn(T_PAIR, user=PAYING, ctx={}, history=[], host=HOST_SHUT)
ro = (off or {}).get('reply') or ''
check(off and isinstance(off.get('panel_offer'), dict) and off['panel_offer'].get('kind') == 'cross' and pr.COST_LINE.format(price='$100') in ro
      and len(off.get('followups') or []) == 2 and not BANK, "a reader who cannot open the profile: the standing $100 quote, unchanged", ro)
paid = pr.service_turn(T_PAIR, user=PAYING, ctx={}, history=[], host=HOST_SHUT,
                       panel_confirm={'kind': 'cross', 'subject': off['panel_offer'].get('subject'), 'question': T_PAIR})
check(paid and paid.get('priced_paid', {}).get('kind') == 'cross' and len(CHARGES) == 1 and paid['crosstab']['us'] == ct['us'],
      "Approve charges once and delivers the same figure", (CHARGES, paid and paid.get('crosstab', {}).get('us')))
# a no from a host that cannot say
bare_host = types.SimpleNamespace(s3_client=None, bucket='x', has=lambda n: False)
check(xt.can_open(PAYING, CHIME_KEY, bare_host) is False and xt.can_open(PAYING, CHIME_KEY, HOST) is True, "a host that cannot say whether the reader may open the file is a no")
# other priced reads keep their price
for t in ("how big is the fanduel audience", "how incremental is Lore Olympus to Prime Video?", "top 10 QSRs for Chime Banking Customers",
          "how many UFC fans are there out there, of those, how many are on Paramount+, on HBO Max and how many have neither service"):
    hit = pr.classify(t, [], PAGE)
    check(hit and hit.get('usd') == 100 and hit.get('frame') != 'crosstab', f"still priced: {t[:50]!r}", hit)

# ------------------------------------------------------------ [6] the record; page and chat
print("[6] the record: a repeat, the page and the chat state one figure")
BANK.clear()
first = xt.answer(T_PAIR, {}, host=HOST, user=PAYING)
cells = (BANK.get(CHIME_KEY) or {}).get('cells') or {}
check(len(cells) == 1 and r2['key'] in cells and cells[r2['key']]['us'] == first['crosstab']['us'], "the pair is on the record under the profile and the group", list(cells))
again = xt.answer("how many Chime users have an interest in technology and shop at Target?", {}, host=HOST, user=PAYING)
ct2 = (again or {}).get('crosstab') or {}
check(ct2.get('us') == first['crosstab']['us'] and ct2.get('share_pct') == first['crosstab']['share_pct'] and ct2.get('key') == first['crosstab']['key'],
      "the same group named the other way round: the same figure", (ct2.get('us'), first['crosstab']['us']))
check(len(BANK[CHIME_KEY]['cells']) == 1, "and nothing new is written")
PICK_T = {'column': 'WHERE THEY SHOP', 'value': 'TARGET'}
PICK_TECH = {'column': 'INTEREST', 'value': 'TECHNOLOGY'}
PICK_HULU = {'column': 'STREAMING/PLATFORM', 'value': 'Disney+/Hulu'}
pg = xt.page_read(CHIME_KEY, [PICK_TECH, PICK_T], host=HOST, username='pat', subject='Chime Banking Customers')
check(pg.get('success') and pg['us'] == first['crosstab']['us'] and pg['in_data'] == first['crosstab']['in_data'] and pg['share_pct'] == first['crosstab']['share_pct']
      and pg['index'] == first['crosstab']['index'] and pg['key'] == first['crosstab']['key'],
      "the page states the chat's figure to the person (share, US people, people in the data, index)", (pg.get('us'), first['crosstab']['us']))
check([r['us'] for r in pg['table'] if r['kind'] == 'single'] == [b['us'] for b in again['crosstab']['behaviors']][::-1] or
      sorted(r['us'] for r in pg['table'] if r['kind'] == 'single') == sorted(b['us'] for b in first['crosstab']['behaviors']), "and the same single rows beside it")
# the page first, then the chat
BANK.clear()
pg3 = xt.page_read(CHIME_KEY, [PICK_T, PICK_HULU, PICK_TECH], host=HOST, username='pat', subject='Chime Banking Customers')
check(pg3.get('success') and len(BANK[CHIME_KEY]['cells']) == 4, "a page read goes on the record exactly like a chat read: the triple and its three pairs", list(BANK.get(CHIME_KEY, {}).get('cells', {})))
chat3 = xt.answer(T_TRIPLE_REORDERED, {}, host=HOST, user=PAYING)
check(chat3 and chat3['crosstab']['us'] == pg3['us'] and chat3['crosstab']['in_data'] == pg3['in_data'] and chat3['crosstab']['index'] == pg3['index']
      and len(BANK[CHIME_KEY]['cells']) == 4, "the chat after the page: the same figure to the person, nothing new written", (chat3 and chat3['crosstab']['us'], pg3.get('us')))
pair_rows = {r['group']: r for r in pg3['table'] if r['kind'] == 'pair'}
chat_pairs = {' and '.join(p['labels']): p for p in chat3['crosstab']['pairs']}
check(sorted(r['us'] for r in pair_rows.values()) == sorted(p['us'] for p in chat_pairs.values()), "and the same pairs", (pair_rows.keys(), chat_pairs.keys()))
tr = chat3['reply']
print('    ' + tr.replace('\n', '\n    '))
check('Two at a time:' in tr and tr.count('\n- ') == 6, "the chat shows each single and each pair")
# the record wins over a fresh draw
BANK[CHIME_KEY]['cells'][r2['key']].update({'share': 0.7412, 'gp': 0.1401, 'us': 6_654_321, 'raw': 201_707})
moved_pg = xt.page_read(CHIME_KEY, [PICK_T, PICK_TECH], host=HOST, username='pat', subject='Chime Banking Customers')
moved_chat = xt.answer(T_PAIR, {}, host=HOST, user=PAYING)
check(moved_pg['share_pct'] == 74.12 and moved_pg['us'] == 6_654_321 and moved_chat['crosstab']['us'] == 6_654_321,
      "a figure on the record is the figure returned, on the page and in chat, to the person", (moved_pg.get('us'), moved_chat['crosstab']['us']))
# a probe writes nothing
BANK.clear()
xt._BANK_HOOK['put'] = None
check(xt.bank_put(CHIME_KEY, 'x', {'a & b': {'share': 0.1}}, by='canary:golden', host=HOST) == 0 and not BANK, "a probe and a test process put nothing on the record")
xt._BANK_HOOK['put'] = lambda key, doc: BANK.__setitem__(key, doc)

# ------------------------------------------------------------ [9] the split of a base
print("[9] the split of a base across two services")
BANK.clear()
split = cx.answer("how many Chime Banking Customers are there, of those how many are on Netflix, on Hulu", {}, host=HOST)
pairx = xt.answer("Chime Banking Customers who use Netflix and Hulu", {}, host=HOST, user=PAYING)
check(split and pairx and split['sizing'][0]['both'] == pairx['crosstab']['us'] and f"- On both: {pairx['crosstab']['us']:,} ({pairx['crosstab']['share_pct']:.1f}%)" in split['reply'],
      "one figure for the people on both, in the split and in the cross", (split and split['sizing'][0].get('both'), pairx and pairx['crosstab']['us']))
check(split and 'On both' in split['reply'] and 'On neither' in split['reply'], "the split keeps its own shape", split and split['reply'][:200])
# the split delivered to a reader goes on the record itself (it did not before 2026-11-11)
BANK.clear()
split_u = cx.answer("how many Chime Banking Customers are there, of those how many are on Netflix, on Hulu", {}, host=HOST, username='pat')
pk0 = bx.set_key([b_netflix['condition']['key'], b_hulu['condition']['key']])
check(pk0 in (BANK.get(CHIME_KEY) or {}).get('cells', {}) and BANK[CHIME_KEY]['cells'][pk0]['us'] == split_u['sizing'][0]['both'],
      "a split delivered to a reader puts its 'both' on the record", list((BANK.get(CHIME_KEY) or {}).get('cells', {})))
# a figure on the record keeps winning for the same profile and group
pk = bx.set_key([b_netflix['condition']['key'], b_hulu['condition']['key']])
BANK[CHIME_KEY]['cells'][pk].update({'share': 0.5512, 'us': 4_948_333, 'raw': 149_995, 'by': 'delivered'})
split2 = cx.answer("how many Chime Banking Customers are there, of those how many are on Netflix, on Hulu", {}, host=HOST)
check(split2['sizing'][0]['both'] == 4_948_333 and '- On both: 4,948,333 (55.1%)' in split2['reply'],
      "a 'both' on the record keeps winning for the same profile and group", split2['reply'])
pair2 = xt.answer("Chime Banking Customers who use Netflix and Hulu", {}, host=HOST, user=PAYING)
check(pair2['crosstab']['us'] == 4_948_333, "and the cross of the same two rows states it too")

# ------------------------------------------------------------ [10] the page
print("[10] the page: options, picks, the export")
BANK.clear()
opts = xt.page_options(CHIME_KEY, '', host=HOST)
check(opts.get('success') and len(opts['behaviors']) == 30 and opts['behaviors'][0]['share_pct'] >= opts['behaviors'][-1]['share_pct'] and opts['has_interests'],
      "with nothing typed: the largest rows, each with its category and its own share", opts.get('behaviors', [])[:2])
check(all(set(o) >= {'column', 'value', 'label', 'category', 'share_pct', 'kind'} for o in opts['behaviors'] + opts['demographics']), "every option carries its category and its share")
check(any(d['column'] == 'GENDER' and d['label'] == 'Female' for d in opts['demographics']) and len(opts['demographics']) > 10, "every demographic bucket is offered")
ot = xt.page_options(CHIME_KEY, 'targ', host=HOST)
check(ot['behaviors'] and ot['behaviors'][0]['value'] == 'TARGET' and ot['behaviors'][0]['category'] == 'Where They Shop' and ot['behaviors'][0]['share_pct'] == 84.4,
      "typing finds a row by the start of its name", ot.get('behaviors'))
oi = xt.page_options(CHIME_KEY, 'interest tech', host=HOST)
check(oi['behaviors'] and oi['behaviors'][0]['value'] == 'TECHNOLOGY' and oi['behaviors'][0]['kind'] == 'interest', "a category word narrows the search (an interest row)", oi.get('behaviors'))
oq = xt.page_options(CHIME_KEY, 'qsr', host=HOST)
check(oq['categories'] and oq['categories'][0]['label'] == 'Any QSR' and oq['categories'][0]['whole'] and oq['categories'][0]['share_pct'] == roll_qsr['reach'],
      "a category's name offers the whole category with its reach", oq.get('categories'))
check(xt.page_options(CHIME_KEY, 'zzzzzz', host=HOST)['behaviors'] == [], "nothing matches: an empty list, not an error")
one = xt.page_read(CHIME_KEY, [PICK_T], host=HOST, username='pat')
check(one.get('success') is False and one['message'] == 'Pick at least two different behaviors to size a group.' and not BANK, "one pick: a plain line, nothing sized", one)
check(xt.page_read(CHIME_KEY, [], host=HOST, username='pat').get('success') is False, "no picks: the same plain line")
dup = xt.page_read(CHIME_KEY, [PICK_T, dict(PICK_T), PICK_TECH], host=HOST, username='pat', subject='Chime Banking Customers')
check(dup.get('success') and dup['us'] == first['crosstab']['us'] and any('picked twice' in n for n in dup['notes']), "the same row twice is counted once, and said", dup.get('notes'))
twin = xt.page_read(CHIME_KEY, [PICK_T, {'column': 'MOST PURCHASED BRANDS', 'value': 'TARGET'}], host=HOST, username='pat')
check(twin.get('success') is False, "a row that is not on the profile (or the same brand again) leaves nothing to size", twin)
gone = xt.page_read(CHIME_KEY, [PICK_T, {'column': 'QSR', 'value': 'NOT A BRAND'}], host=HOST, username='pat')
check(gone.get('success') is False and 'is not a row on this profile' in gone['message'], "a pick the profile does not carry is said plainly", gone)
inside = xt.page_read(CHIME_KEY, [{'column': 'QSR', 'value': 'QSR', 'whole': True}, {'column': 'QSR', 'value': 'MCDONALDS'}, PICK_T], host=HOST, username='pat')
check(inside.get('success') and any('already inside' in n for n in inside['notes']) and len([r for r in inside['table'] if r['kind'] == 'single']) == 2,
      "a brand beside its own whole category: the category is left out, and said", inside.get('notes'))
wq = xt.page_read(CHIME_KEY, [{'column': 'QSR', 'value': 'QSR', 'whole': True}, {'column': 'STREAMING/PLATFORM', 'value': 'NETFLIX'}], host=HOST, username='pat')
check(wq.get('success') and wq['table'][0]['group'] == 'Eat at any QSR' and wq['table'][0]['share_pct'] == roll_qsr['reach'] and wq['table'][0]['index'] == roll_qsr['index'],
      "a whole category as a pick prints the category reach's percent and index", wq.get('table', [])[:1])
demo = xt.page_read(CHIME_KEY, [PICK_T, {'column': 'STREAMING/PLATFORM', 'value': 'NETFLIX'}, {'column': 'GENDER', 'value': 'FEMALE'}], host=HOST, username='pat',
                    subject='Chime Banking Customers')
check(demo.get('success') and demo['table'][2]['group'] == 'Are female' and demo['table'][-1]['group'] == 'All three' and 'are female' in demo['doing'],
      "an optional demographic bucket narrows the group", [r['group'] for r in demo.get('table', [])])
ni_opts = xt.page_options(NO_INTERESTS_KEY, '', host=HOST)
ni = xt.page_read(NO_INTERESTS_KEY, [PICK_T, {'column': 'STREAMING/PLATFORM', 'value': 'NETFLIX'}], host=HOST, username='pat')
check(ni_opts.get('success') and ni_opts['has_interests'] is False and all(o['kind'] != 'interest' for o in ni_opts['behaviors']) and ni.get('success'),
      "a profile with no interest rows still offers its behaviors and sizes a group", ni_opts.get('has_interests'))
csv = pg3['csv']
check(csv[0] == ['Column', 'Value', 'Brand Penetration (Row)', 'Category Share', 'Original Raw Numbers', 'US Gen Pop Projection', 'Gen Pop Penetration', 'Index vs Gen Pop'],
      "the export carries the Profile CSV's own columns", csv[0])
check(csv[1][0] == 'SUBJECT' and csv[2][0] == 'SAMPLE SIZE' and csv[2][4] == SAMPLE and '2025-01-01 TO 2025-12-31' in csv[2][1], "then the subject and the sample line with the window")
check(csv[3][:3] == ['WHERE THEY SHOP', 'TARGET', '84.4259'] and csv[3][4] == 229_744 and csv[3][5] == 7_579_255 and csv[3][6] == '29.6143' and csv[3][7] == '285.0',
      "each behavior's own row as the profile file prints it", csv[3])
grp = [r for r in csv if r[0] == 'CROSS BEHAVIORS']
check(len(grp) == 4 and grp[-1][1] == 'Target, Disney+/Hulu and Technology' and grp[-1][5] == pg3['us'] and grp[-1][4] == pg3['in_data']
      and float(grp[-1][2]) == round(pg3['share_pct'], 2) or abs(float(grp[-1][2]) - pg3['share_pct']) < 0.006,
      "then each pair and the whole group under CROSS BEHAVIORS, the page's own figures", grp[-1])
check(pg3['cut_order'].startswith('Build a cut of Chime Banking Customers who shop at Target') and orders.is_order(pg3['cut_order']),
      "Build as a cut hands the paid build flow an order in its own words", pg3['cut_order'])

# ------------------------------------------------------------ the audience under a shorter name
print("[3b] the audience")
BANK.clear()
xt._catalog_bases = lambda bare: ([{'name': 'Wicked Tuna Viewers', 'key': CHIME_KEY}] if 'wicked' in bare.lower() else
                                  [{'name': 'Acme Shoppers', 'key': CHIME_KEY}, {'name': 'Acme Rewards Members', 'key': 'other.csv'}]
                                  if 'acme' in bare.lower() else [])
one_aud = xt.plan("of Wicked Tuna viewers, how many shop at Target and use Netflix", {}, host=HOST)
check(one_aud and one_aud.get('subject') == 'Wicked Tuna Viewers' and len(one_aud.get('conditions') or []) == 2, "a name that begins exactly one audience in the library is that audience", one_aud and one_aud.get('subject'))
two = xt.plan("Acme customers who shop at Target and use Netflix", {}, host=HOST)
check(two and two.get('reply') and 'Which audience do you mean: Acme Shoppers or Acme Rewards Members?' in two['reply']['reply']
      and not two['reply'].get('panel_offer'), "a name that fits two audiences asks which", two and two.get('reply'))
short = xt.plan("Chime Banking users who shop at Target and use Netflix", {'primary': {'name': 'Chime Banking Customers - Planners', 's3_key': 'planners.csv'}}, host=HOST)
check(short and short.get('key') == CHIME_KEY, "a named audience wins over the page when the library holds it", short and short.get('key'))
amp = xt.plan("Chime users who shop at Target and are interested in Home & Living", {}, host=HOST)
check(amp and [c['value'] for c in amp.get('conditions') or []] == ['TARGET', 'HOME & LIVING'], "an ampersand inside a row's name is part of the name", amp and amp.get('conditions'))
androw = xt.plan("Chime users who shop at Target and are interested in health and wellness", {}, host=HOST)
check(androw and [c['value'] for c in androw.get('conditions') or []] == ['TARGET', 'HEALTH & WELLNESS'], "a row whose own name carries an 'and' binds whole", androw and (androw.get('conditions') or androw.get('reply')))
xt._catalog_bases = lambda bare: []
check(xt.plan("Zebra users who shop at Target and use Netflix", PAGE, host=HOST) is None, "a named audience the library does not hold is never read off the open page")
pgx = xt.answer(T_PAGE, PAGE, host=HOST, user=PAYING)
pgr = (pgx or {}).get('reply') or ''
check(pgx and len(pgx['crosstab']['behaviors']) == 3 and pgx['offer_deck'] is True and pgx['deck_angle'], "the open profile is the audience; a deck page is offered", pgx and pgx['crosstab'])
chase = xt.answer("among Chime users what percent bank with Chase and have kids", {}, host=HOST, user=PAYING)
check(chase and [b['kind'] for b in chase['crosstab']['behaviors']] == ['brand', 'demo'] and 'have children' in chase['reply'],
      "a behavior narrowed by a demographic bucket", chase and chase['reply'][:300])
carriers = xt.answer("Chime users who use T-Mobile and Verizon", {}, host=HOST, user=PAYING)

low_line = xt._read_line({'index': 94, 'conditions': [
    {'key': 'a', 'share': 0.306, 'index': 90, 'times': '0.9x the US average', 'label': 'Verizon'},
    {'key': 'b', 'share': 0.311, 'index': 104, 'times': 'in line with the US average', 'label': 'T-Mobile'}]},
    {'phrases': {'a': 'use Verizon', 'b': 'use T-Mobile'}}, {})
check('Verizon is what holds it down, at index 90 (0.9x the US average). ' in low_line, "a group under the US average names the row that holds it down, one set of brackets", low_line)
# ------------------------------------------------------------ [11] wording
print("[11] the text a reader sees")
BANNED = re.compile(r"\b(?:modell?ed|modeling|model|synth\w*|estimat\w*|joint probabilit\w*|independen\w*|projected[- ]from|household\w*|hhs?|"
                    r"panel|panelists?|sample|confidence|margin of error|approximately|roughly|about \d|source|copula|loading|tied|unrelated|"
                    r"pick law|either/or)\b", re.I)
page_text = ' \n'.join([pg3['read'], pg3['rows_read'], pg3['doing'], pg3['label'], pg3['cut_order'], one['message'], gone['message']]
                       + [r['group'] for r in pg3['table']] + dup['notes'] + inside['notes']
                       + [o['label'] + ' ' + o['category'] for o in opts['behaviors'] + oq['categories'] + opts['demographics']])
for name, text in (('missing row', r_missing), ('quote', ro), ('pair', ra), ('triple', tr), ('open profile', pgr), ('demographic', chase['reply']),
                   ('carriers', carriers['reply']), ('below average', low_line), ('split', split['reply']), ('page', page_text)):
    bad = BANNED.findall(text)
    check(not re.search(r"\([^()]*\(", text), f"{name}: no brackets inside brackets", re.findall(r"\([^()]*\([^()]*\)[^()]*\)", text)[:2])
    check(not bad and '\u2014' not in text and '\u2013' not in text and xt.is_clean(text), f"{name}: no banned word, no dash", bad)
    for m in re.finditer(r"index (\d+)", text):
        check(re.match(r" \((?:\d+\.\dx the US average|in line with the US average)\)", text[m.end():m.end() + 40]) is not None,
              f"{name}: index {m.group(1)} carries its plain comparison", text[m.start():m.end() + 40])
# a single row prints the profile file's own counts (what the page shows for that row); every count this read
# works out (a pair, the whole group) never ends in a zero
FILE_US = {int(v) for v in CHIME['US Gen Pop Projection'].dropna()}
for text in (ra, tr, pgr):
    for m in re.finditer(r"(?<![\d.$])(\d{1,3}(?:,\d{3})+)(?!\.\d)", text):
        n = int(m.group(1).replace(',', ''))
        check(n % 10 != 0 or n in FILE_US, f"no count this read works out ends in a zero ({m.group(1)})")
nf_row = CHIME[(CHIME['Column'] == 'STREAMING/PLATFORM') & (CHIME['Value'] == 'NETFLIX')].iloc[0]
nf_single = next(b for b in pgx['crosstab']['behaviors'] if b['label'] == 'Netflix')
check(nf_single['us'] == int(nf_row['US Gen Pop Projection']) and nf_single['in_data'] == int(nf_row['Original Raw Numbers']),
      "a single row's counts are the profile file's own, to the person", (nf_single, int(nf_row['US Gen Pop Projection'])))
check(all(r['index'] is None or r['times'] for r in pg3['table']), "every index on the page carries its plain comparison")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all crosstab checks passed")
