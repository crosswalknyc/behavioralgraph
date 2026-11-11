#!/usr/bin/env python3
"""A cross of behaviors inside one audience (Jenna 2026-11-11, feedback
she forwarded: "The cross-tab functionality, open up to across
behaviors. between behavior A, B and C... For example the Chime users
who shop at Target and have an interest in X.").

  [1] the estimator: inside the bounds for any shares, the same for any
      order, never below the unrelated figure, low in the range above
      it, the same loadings on the audience and the US
  [2] nested: A and B and C <= A and B <= A, for shares and for counts,
      and a figure on the record stands and bounds every new one
  [3] binding on a real profile: the brand in the category the verb
      points at, an interest row, a compound row, a demographic bucket,
      a mirrored brand as one behavior; a behavior the profile does not
      carry is missing with its nearest rows, never substituted
  [4] the words: a cross is a question; an order (build / run / create a
      profile or a cut) is never this lane; other reads keep their lanes
  [5] the Chime x Target x interest example end to end through the
      priced lane: the missing row is said free before any price, the
      offer names what the reader gets at the standing price, Approve
      delivers the size, the US figure, the index with its plain
      comparison, the single shares and the offers
  [6] a repeat returns the same figure from the record, whatever order
      the behaviors are named in; a pair asked after its triple is the
      banked pair
  [7] a whole category asks the category reach module for its share
  [8] the text a reader sees carries no banned word

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
from prometheus import crosstab as xt  # noqa: E402
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
FRAMES = {CHIME_KEY: CHIME, 'Gen_Pop_2026.csv': GENPOP}
bc._frame = lambda host, key: FRAMES[key]
SAMPLE = 272_125


def fake_resolve(phrase, ctx=None):
    k = sl.strip_audience_nouns(str(phrase or '').strip()).lower() or str(phrase or '').strip().lower()
    if k in ('chime', 'chime banking', 'chime banking customers'):
        return {'name': 'Chime Banking Customers', 'sample': SAMPLE, 'us': round(SAMPLE * 32.99),
                'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'key': CHIME_KEY, 'matched': phrase}
    return None


sl.resolve = fake_resolve
HOST = types.SimpleNamespace(s3_client=None, bucket='dashboard-inputs', has=lambda n: False)
BANK = {}
xt._BANK_HOOK['get'] = lambda key: BANK.get(key)
xt._BANK_HOOK['put'] = lambda key, doc: BANK.__setitem__(key, doc)
PAYING = {'username': 'pat', 'email': 'pat@client.com', 'company': 'Client Co', 'role': 'user', 'credits': 5000}
PAGE = {'primary': {'name': 'Chime Banking Customers', 's3_key': CHIME_KEY}, 'cuts': []}
GP = bx.genpop_map(GENPOP)

T_MISSING = "Chime users who shop at Target and are interested in home improvement"
T_PAIR = "Chime users who shop at Target and are interested in technology"
T_TRIPLE = "of Chime users, how many shop at Target, use Hulu and have an interest in technology"
T_TRIPLE_REORDERED = "how many Chime users have an interest in technology, use Hulu and shop at Target?"
T_PAGE = "cross Target shoppers with Netflix and Costco in this profile"

# ------------------------------------------------------------ [1] estimator
print("[1] the estimator holds its bounds and does not care about order")
rng = random.Random(20261111)
worst = 0.0
for _ in range(400):
    k = rng.choice((2, 2, 3, 3, 4, 5))
    ps = [rng.choice((rng.uniform(0.001, 0.2), rng.uniform(0.2, 0.9), rng.uniform(0.9, 0.9999))) for _ in range(k)]
    idx = [rng.choice((40, 90, 100, 120, 180, 260, 400, 900)) for _ in range(k)]
    a = [bx.loading(i) for i in idx]
    j = bx.joint(ps, a)
    lo, hi = max(0.0, sum(ps) - (k - 1)), min(ps)
    if not (lo - 1e-12 <= j <= hi + 1e-12 and j >= bx.unrelated(ps) - 1e-12):
        check(False, "a joint left its bounds", (ps, idx, j))
        break
    perm = list(zip(ps, a))
    rng.shuffle(perm)
    worst = max(worst, abs(j - bx.joint([p for p, _ in perm], [x for _, x in perm])))
else:
    check(True, "400 random groups: never above the smallest share, never below max(0, sum - (k - 1)), never below the unrelated figure")
check(worst == 0.0, "the same figure for any order the behaviors are given in", worst)
check(bx.loading(100) == 0.0 and bx.loading(60) == 0.0 and bx.loading(None) == 0.0, "a behavior at or under the US average is not tied")
check(0 < bx.loading(150) < bx.loading(250) < bx.loading(300) == bx.A_MAX == bx.loading(2000), "the tie rises with the index and stops at the cap")
check(abs(bx.joint([0.3, 0.4], [0.0, 0.0]) - 0.12) < 1e-12 and abs(bx.joint([0.3, 0.4], [bx.A_MAX, 0.0]) - 0.12) < 1e-9,
      "unrelated behaviors sit on the unrelated figure")
for p, q in ((0.3, 0.3), (0.84, 0.67), (0.1, 0.05), (0.5, 0.2), (0.05, 0.02)):
    j = bx.joint([p, q], [bx.A_MAX, bx.A_MAX])
    way = (j - p * q) / (min(p, q) - p * q)
    check(j > p * q and way <= 0.42, f"fully tied {p} and {q}: above the unrelated figure, low in the range ({way:.0%} of the way up)", way)
check(bx.joint([0.5, 1.0], [0.3, 0.3]) == 0.5 and bx.joint([0.5, 0.0], [0.3, 0.3]) == 0.0, "everyone and no one behave")
check(bx.times_of(213) == '2.1x the US average' and bx.times_of(101) == 'in line with the US average', "the plain comparison beside an index")

# ------------------------------------------------------------ [3] binding
print("[3] each behavior binds to its row on the profile")


def bind(name, hint='', whole=False):
    return bx.bind(CHIME, name, hint=hint, whole=whole, genpop=GP, gdf=GENPOP, subject='Chime Banking Customers')


b_target = bind('Target', 'shop')
check(b_target['status'] == 'bound' and b_target['condition']['column'] == 'WHERE THEY SHOP' and abs(b_target['condition']['pct'] - 84.4259) < 1e-6,
      "shop at Target is the Where They Shop row, 84.4259", b_target)
check(abs(b_target['condition']['gp'] - 29.6143) < 1e-6, "its US share comes from the US file's own row", b_target)
b_tech = bind('technology', 'interest')
check(b_tech['status'] == 'bound' and b_tech['condition']['kind'] == 'interest' and abs(b_tech['condition']['pct'] - 88.9308) < 1e-6, "an interest row binds", b_tech)
b_hulu = bind('Hulu', 'use')
check(b_hulu['status'] == 'bound' and b_hulu['condition']['value'] == 'Disney+/Hulu', "Hulu answers to the compound row", b_hulu)
check(bind('Target shoppers')['status'] == 'bound' and bind('Target shoppers')['condition']['key'] == b_target['condition']['key'], "the audience noun rides along")
b_women = bind('women', 'demo')
check(b_women['status'] == 'bound' and b_women['condition']['kind'] == 'demo' and b_women['condition']['column'] == 'GENDER', "a demographic bucket by its plain name", b_women)
b_age = bind('aged 18 to 24', 'demo')
check(b_age['status'] == 'bound' and b_age['condition']['value'] == '18-24', "an age bucket", b_age)
b_miss = bind('home improvement', 'interest')
check(b_miss['status'] == 'missing' and [n['value'] for n in b_miss['nearest']][:2] == ['BUILDING/DIY', 'HOME & LIVING'],
      "home improvement is not a row: missing, with Building/DIY and Home & Living as the nearest", b_miss)
check(bind('Zzyzx Qwvrt')['status'] == 'missing' and bind('Zzyzx Qwvrt')['nearest'] == [], "a name nothing is close to is plainly missing")
check(bind('Chime Banking Customers')['status'] in ('missing', 'everyone'), "the audience's own name is not a behavior to cross")
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
check(all(c['raw'] % 10 != 0 and c['us'] % 10 != 0 for c in list(r3['cells'].values()) + r3['conditions']), "no count ends in a zero")
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
check(r2['share'] >= r2['unrelated'] and (r2['share'] - r2['unrelated']) / (min(0.844259, 0.889308) - r2['unrelated']) < 0.34,
      "Target and Technology (both far above the US average) sit above the unrelated figure, in the bottom third of the range")
gp_unrelated = 0.296143 * (b_tech['condition']['gp'] / 100.0)
check(r2['gp'] > gp_unrelated and r2['index'] < round(0.844259 * 0.889308 / gp_unrelated * 100),
      "the same tie runs on the US side, so the index is lower than the two indexes multiplied", (r2['gp'], r2['index']))
check(r2['raw'] == round(r2['share'] * SAMPLE) or abs(r2['raw'] - r2['share'] * SAMPLE) < 100, "people in the data = share x the profile's sample")
check(abs(r2['us'] - r2['raw'] / 10_000_000 * 329_900_000) < 200, "US people = that / 10,000,000 x 329,900,000")
# a figure on the record stands and bounds new ones
held = {r2['key']: {'share': 0.74, 'gp': 0.12}}
r3h = bx.cross(conds, SAMPLE, held=held, salt=CHIME_KEY)
check(abs(r3h['cells'][r2['key']]['share'] - 0.74) < 1e-12 and r3h['cells'][r2['key']]['on_record'], "a pair on the record stands")
check(r3h['share'] <= 0.74 + 1e-12, "the triple never exceeds the banked pair", r3h['share'])
held_up = {r3['key']: {'share': min(c['share'] for c in r3['conditions']) * 0.97, 'gp': None}}
r2h = bx.cross([b_target['condition'], b_tech['condition']], SAMPLE, held=held_up, salt=CHIME_KEY)
check(r2h['share'] >= held_up[r3['key']]['share'] - 1e-12, "a pair asked later never falls below a triple on the record", (r2h['share'], held_up))
loose = bx.cross([b_target['condition'], b_tech['condition']], SAMPLE, ties={b_tech['condition']['key']: 0.0}, salt=CHIME_KEY)
check(abs(loose['share'] - loose['unrelated']) < 1e-9 and loose['share'] <= r2['share'], "a reasoned 'apart' only ever lowers the figure, to the unrelated one")
tight = bx.cross([b_target['condition'], b_tech['condition']], SAMPLE, ties={b_tech['condition']['key']: 9.0}, salt=CHIME_KEY)
check(tight['share'] == r2['share'], "a reasoning step can never tighten a tie past what the profile shows")
check(bx.cross([b_target['condition']], SAMPLE) is None and bx.cross([b_target['condition'], dict(b_target['condition'])], SAMPLE) is None,
      "one behavior, or the same one twice, is not a cross")

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
          "what percent of the audience is female and what percent is male", "which brands do Chime users who shop at Target buy"):
    check(xt.parse(t, PAGE) is None, f"not a cross of behaviors: {t[:64]!r}")
hit = pr.classify(T_PAIR, [], {})
check(hit and hit['kind'] == 'cross' and hit['via'] == 'service' and hit['frame'] == 'crosstab' and hit['usd'] == 100,
      "the cross is a custom ask in the sizing lane at the standing price", hit)
check(pr.classify("how many shop at Target and use Hulu", [], {}) is None or pr.classify("how many shop at Target and use Hulu", [], {}).get('frame') != 'crosstab',
      "with no audience named and no profile open there is nothing to cross")
check((pr.classify("how many shop at Target and use Hulu", [], PAGE) or {}).get('frame') == 'crosstab', "with a profile open the bare question points at it")
sub = "Of those viewers who watch Law & Order: SVU on Peacock, how many subscribe to Netflix and pay for Hulu?"
check((pr.classify(sub, [], PAGE) or {}).get('frame') != 'crosstab', "a subscribe overlap keeps its own lane")
# an audience with no profile is not this lane: the standing lanes read the words as before
no_prof = "of Wicked Tuna viewers, how many use Hulu and drive a Ford"
check((pr.classify(no_prof, [], {}) or {}).get('frame') == 'crosstab', "named audience: a cross until the profile is looked for")
check(xt.plan(no_prof, {}, host=HOST) is None, "no profile for the audience: no plan")
xt.decline(no_prof)
after = pr.classify(no_prof, [], {})
check((after or {}).get('frame') != 'crosstab', "declined: the standing lanes read the words (as before this lane existed)", after)
xt.decline('')

# ------------------------------------------------------------ [5] end to end
print("[5] Chime users x Target x an interest, through the priced lane")
out = pr.service_turn(T_MISSING, user=PAYING, ctx={}, history=[], host=HOST)
r = (out or {}).get('reply') or ''
print('    ' + r.replace('\n', '\n    '))
check(out and not out.get('panel_offer') and out.get('crosstab_unbound'), "a row the profile does not carry: said before any price")
check('Home improvement is not a row on the Chime Banking Customers profile' in r and 'Building/DIY' in r and 'Home & Living' in r,
      "names what is missing and offers the nearest rows", r)
check('$' not in r and 'Nothing has been pulled or charged' in r, "nothing quoted, nothing charged")
check(any('Building/DIY' in c for c in out.get('followups') or []), "a chip asks again with the nearest row", out.get('followups'))
check(not BANK, "nothing goes on the record for a row that did not bind")

off = pr.service_turn(T_PAIR, user=PAYING, ctx={}, history=[], host=HOST)
ro = (off or {}).get('reply') or ''
print('    ' + ro.replace('\n', '\n    '))
check(off and isinstance(off.get('panel_offer'), dict) and off['panel_offer'].get('kind') == 'cross', "bound rows: the offer first", off)
check(ro.startswith('I can size the group inside Chime Banking Customers who shop at Target and have an interest in Technology')
      and pr.COST_LINE.format(price='$100') in ro, "one sentence on what the reader gets, then the standing price", ro)
check(len(off.get('followups') or []) == 2, "two chips, approve and cancel", off.get('followups'))
check(not BANK, "an offer puts nothing on the record")

CHARGES = []
HOST_PAY = types.SimpleNamespace(s3_client=None, bucket='dashboard-inputs', has=lambda n: n == 'consume_credit',
                                 consume_credit=lambda u, **kw: CHARGES.append((u, kw)) or True)
pc = {'kind': 'cross', 'subject': off['panel_offer'].get('subject'), 'question': T_PAIR}
ans = pr.service_turn(T_PAIR, user=PAYING, ctx={}, history=[], host=HOST_PAY, panel_confirm=pc)
ra = (ans or {}).get('reply') or ''
print('    ' + ra.replace('\n', '\n    '))
ct = (ans or {}).get('crosstab') or {}
check(ans and ans.get('priced_paid', {}).get('kind') == 'cross' and len(CHARGES) == 1, "Approve charges once and delivers", CHARGES)
check(ra.startswith('On Chime Banking Customers, Jan 1, 2025 to Dec 31, 2025:'), "opens on the audience and its window", ra[:80])
check(f"{ct.get('us'):,} US people in this audience shop at Target and have an interest in Technology" in ra, "the size in US people", ra)
check(f"That is {ct.get('share_pct'):.1f}% of the audience" in ra, "the share of the audience")
check(f"index {ct.get('index')} ({ct.get('times')})" in ra and ct.get('times', '').endswith('x the US average'), "the index with its plain comparison", ct)
check('- Shop at Target: 84.4% of the audience, 7,579,255 US people, index 285 (2.9x the US average)' in ra, "the single shares beside it, each with its index", ra)
check('- Have an interest in Technology: 88.9% of the audience' in ra, "the interest row beside it")
check('cut of the audience' in ra and 'deck page' in ra and any(c.startswith('Build a cut of Chime Banking Customers who') for c in ans['followups']),
      "offers the cut and the deck page", ans.get('followups'))
check(orders.is_order(ans['followups'][0]) and len(ans['followups']) == 1, "the one chip is the cut, an order the build flow takes")
check(75.0 < ct['share_pct'] < 84.4 and ct['us'] < 7_579_255 and ct['in_data'] < 229_744, "the group sits inside both single rows", ct)
check(abs(ct['us'] - ct['in_data'] / 10_000_000 * 329_900_000) < 200 and ct['us'] % 10 and ct['in_data'] % 10, "house count math, no trailing zero", ct)
check(ct['share_pct'] == round(r2['share'] * 100, 2) and ct['index'] == r2['index'], "the lane's figure is the engine's figure")

# ------------------------------------------------------------ [6] the record
print("[6] the record: a repeat is the same figure")
cells = (BANK.get(CHIME_KEY) or {}).get('cells') or {}
check(len(cells) == 1 and r2['key'] in cells and cells[r2['key']]['us'] == ct['us'], "the pair is on the record under the profile and the group", list(cells))
again = pr.service_turn("how many Chime users have an interest in technology and shop at Target?", user=PAYING, ctx={}, history=[],
                        host=HOST_PAY, panel_confirm={'kind': 'cross', 'subject': 'Chime Banking Customers'})
ct2 = (again or {}).get('crosstab') or {}
check(ct2.get('us') == ct['us'] and ct2.get('share_pct') == ct['share_pct'] and ct2.get('index') == ct['index'] and ct2.get('key') == ct['key'],
      "the same group named the other way round: the same figure", (ct2.get('us'), ct['us']))
check(len((BANK[CHIME_KEY]['cells'])) == 1, "and nothing new is written")
# the record wins over a fresh draw: move the banked figure and ask again
BANK[CHIME_KEY]['cells'][r2['key']]['share'] = 0.7412
BANK[CHIME_KEY]['cells'][r2['key']]['gp'] = 0.1401
moved = xt.answer(T_PAIR, {}, host=HOST, user=PAYING)
check(moved and moved['crosstab']['share_pct'] == 74.12, "a figure on the record is the figure returned", moved and moved['crosstab'])
tri = xt.answer(T_TRIPLE, {}, host=HOST, user=PAYING)
tr = (tri or {}).get('reply') or ''
print('    ' + tr.replace('\n', '\n    '))
check(tri and tri['crosstab']['share_pct'] <= 74.12 and len(tri['crosstab']['behaviors']) == 3, "the triple never exceeds the banked pair", tri and tri['crosstab'])
check('Two at a time:' in tr and '- Target and Technology: 74.1%' in tr, "each pair is shown, the banked one as banked", tr)
check(len(BANK[CHIME_KEY]['cells']) == 4, "the triple and its two new pairs join the record", list(BANK[CHIME_KEY]['cells']))
tri2 = xt.answer(T_TRIPLE_REORDERED, {}, host=HOST, user=PAYING)
check(tri2 and tri2['crosstab']['us'] == tri['crosstab']['us'] and tri2['crosstab']['key'] == tri['crosstab']['key'],
      "the triple in another order: the same figure")
pair_later = xt.answer("Chime users who use Hulu and are interested in technology", {}, host=HOST, user=PAYING)
pk = bx.set_key([b_hulu['condition']['key'], b_tech['condition']['key']])
check(pair_later and pair_later['crosstab']['us'] == BANK[CHIME_KEY]['cells'][pk]['us'] and pair_later['crosstab']['us'] >= tri['crosstab']['us'],
      "a pair asked after its triple is the pair that was banked with it, and never smaller than the triple")
# the page: the same read with the profile open and no audience named
BANK.clear()
pg = xt.answer(T_PAGE, PAGE, host=HOST, user=PAYING)
pgr = (pg or {}).get('reply') or ''
check(pg and len(pg['crosstab']['behaviors']) == 3 and pg['offer_deck'] is True and pg['deck_angle'], "the open profile is the audience; a deck page is offered", pg and pg['crosstab'])
check('shop at Target, shop at Costco and use Netflix' in pgr or 'shop at Target, use Netflix and shop at Costco' in pgr, "each behavior reads in its own category", pgr[:400])
chase = xt.answer("among Chime users what percent bank with Chase and have kids", {}, host=HOST, user=PAYING)
check(chase and [b['kind'] for b in chase['crosstab']['behaviors']] == ['brand', 'demo'] and 'have children' in chase['reply'],
      "a behavior narrowed by a demographic bucket", chase and chase['reply'][:300])

# the audience under a shorter name, and a name that fits more than one audience
xt._catalog_bases = lambda bare: ([{'name': 'Wicked Tuna Viewers', 'key': CHIME_KEY}] if 'wicked' in bare.lower() else
                                  [{'name': 'Acme Shoppers', 'key': CHIME_KEY}, {'name': 'Acme Rewards Members', 'key': 'other.csv'}]
                                  if 'acme' in bare.lower() else [])
one = xt.plan("of Wicked Tuna viewers, how many shop at Target and use Netflix", {}, host=HOST)
check(one and one.get('subject') == 'Wicked Tuna Viewers' and len(one.get('conditions') or []) == 2, "a name that begins exactly one audience in the library is that audience", one and one.get('subject'))
two = xt.plan("Acme customers who shop at Target and use Netflix", {}, host=HOST)
check(two and two.get('reply') and 'Which audience do you mean: Acme Shoppers or Acme Rewards Members?' in two['reply']['reply']
      and not two['reply'].get('panel_offer'), "a name that fits two audiences asks which, free", two and two.get('reply'))
short = xt.plan("Chime Banking users who shop at Target and use Netflix", {'primary': {'name': 'Chime Banking Customers - Planners', 's3_key': 'planners.csv'}}, host=HOST)
check(short and short.get('key') == CHIME_KEY, "a named audience wins over the page when the library holds it", short and short.get('key'))
amp = xt.plan("Chime users who shop at Target and are interested in Home & Living", {}, host=HOST)
check(amp and [c['value'] for c in amp.get('conditions') or []] == ['TARGET', 'HOME & LIVING'], "an ampersand inside a row's name is part of the name", amp and amp.get('conditions'))
androw = xt.plan("Chime users who shop at Target and are interested in health and wellness", {}, host=HOST)
check(androw and [c['value'] for c in androw.get('conditions') or []] == ['TARGET', 'HEALTH & WELLNESS'], "a row whose own name carries an 'and' binds whole", androw and (androw.get('conditions') or androw.get('reply')))
xt._catalog_bases = lambda bare: []
check(xt.plan("Zebra users who shop at Target and use Netflix", PAGE, host=HOST) is None, "a named audience the library does not hold is never read off the open page")
# a reasoning step may loosen a tie, never tighten it, and never runs for a probe or a test process
calls = []
REASON_HOST = types.SimpleNamespace(has=lambda n: n == 'claude_data',
                                    claude_data=lambda system, user, **kw: calls.append(user) or {'ties': {'Technology (Interest)': 'apart', 'Target (Where They Shop)': 'tied', 'Nope': 'tied'}})
check(xt._reasoned_ties(REASON_HOST, 'Chime Banking Customers', [b_target['condition'], b_tech['condition']], 'pat') == {} and not calls,
      "no reasoning call inside a test process")
_real_may = xt._may_record
xt._may_record = lambda u: True
ties = xt._reasoned_ties(REASON_HOST, 'Chime Banking Customers', [b_target['condition'], b_tech['condition']], 'pat')
xt._may_record = _real_may
check(ties == {b_tech['condition']['key']: 0.0, b_target['condition']['key']: 1.0} and len(calls) == 1 and 'Chime Banking Customers' in calls[0],
      "the reasoning step answers per behavior, by the names it was given", ties)

# the split of a base across two services states the same 'both' as the cross of the two rows
from prometheus import cross_sizing as cx  # noqa: E402
BANK.clear()
split = cx.answer("how many Chime Banking Customers are there, of those how many are on Netflix, on Hulu", {}, host=HOST)
pairx = xt.answer("Chime Banking Customers who use Netflix and Hulu", {}, host=HOST, user=PAYING)
check(split and pairx and split['sizing'][0]['both'] == pairx['crosstab']['us'] and f"- On both: {pairx['crosstab']['us']:,} ({pairx['crosstab']['share_pct']:.1f}%)" in split['reply'],
      "one figure for the people on both, in the split and in the cross", (split and split['sizing'][0].get('both'), pairx and pairx['crosstab']['us']))
check(split and 'On both' in split['reply'] and 'On neither' in split['reply'], "the split keeps its own shape", split and split['reply'][:200])

# ------------------------------------------------------------ [7] a whole category
print("[7] a whole category takes its share from the category reach module")
whole = bind('QSR', 'eat', whole=True)
try:
    from migration import category_rollup as _cr  # noqa: F401
    have_cr = True
except Exception:
    have_cr = False
if have_cr:
    check(whole['status'] == 'bound' and whole['condition']['kind'] == 'category' and whole['condition']['pct'] >= 71.6785,
          "any QSR is the category reach, never below its largest brand", whole)
else:
    check(whole['status'] == 'missing' and whole.get('whole') and whole.get('column') == 'QSR', "the module is not on this build: said plainly, no figure made up here", whole)
    fake = types.ModuleType('migration.category_rollup')
    fake.norm_cat = lambda c: re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())
    fake.genpop_map_from_df = lambda gdf: {}
    fake.category_rollups = lambda df, gp, subject=None, min_brands=1: {'QSR': {'name': 'QSR', 'reach': 93.4, 'genpop': 81.2, 'index': 115, 'brands': 60, 'top': 'MCDONALDS'}}
    sys.modules['migration.category_rollup'] = fake
    import migration as _mig
    _mig.category_rollup = fake
    whole = bind('QSR', 'eat', whole=True)
    check(whole['status'] == 'bound' and whole['condition']['pct'] == 93.4 and whole['condition']['gp'] == 81.2 and whole['condition']['key'] == 'ANY|QSR',
          "with the module present its reach and US figure are the single share", whole)
    anyq = xt.answer("how many of them eat at any QSR and use Netflix", PAGE, host=HOST, user=PAYING)
    check(anyq and 'eat at any QSR' in anyq['reply'] and anyq['crosstab']['share_pct'] <= 77.43, "any QSR crossed with Netflix", anyq and anyq['reply'][:300])
    del sys.modules['migration.category_rollup']
    del _mig.category_rollup

# ------------------------------------------------------------ [8] wording
print("[8] the text a reader sees")
BANNED = re.compile(r"\b(?:modell?ed|modeling|model|synth\w*|estimat\w*|joint probabilit\w*|independen\w*|projected[- ]from|household\w*|hhs?|"
                    r"panel|panelists?|sample|confidence|margin of error|approximately|roughly|about \d|source|copula|loading|tied|unrelated)\b", re.I)
for name, text in (('missing row', r), ('offer', ro), ('pair', ra), ('triple', tr), ('page', pgr), ('demographic', chase['reply'])):
    bad = BANNED.findall(text)
    check(not bad and '\u2014' not in text and '\u2013' not in text and xt.is_clean(text), f"{name}: no banned word, no dash", bad)
    for m in re.finditer(r"index (\d+)", text):
        check(re.match(r" \((?:\d+\.\dx the US average|in line with the US average)\)", text[m.end():m.end() + 40]) is not None,
              f"{name}: index {m.group(1)} carries its plain comparison", text[m.start():m.end() + 40])
for text in (ra, tr, pgr):
    for m in re.finditer(r"(?<![\d.$])(\d{1,3}(?:,\d{3})+)(?!\.\d)", text):
        n = int(m.group(1).replace(',', ''))
        check(n % 10 != 0, f"no count ends in a zero ({m.group(1)})")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print('  - ' + f)
    sys.exit(1)
print("all crosstab checks passed")
