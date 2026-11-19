#!/usr/bin/env python3
"""A tracker never ships cut far below the figure its run was sized at,
and a size that does move takes the whole file with it (2026-11-18).

The incident: a Starz title sized at 29,828 viewers and 858 signups went
through the late check, which cut it to 10,000 and 400 on a title with no
published count. The key metrics moved; the summary under them kept its
old shares (3.99% above, 2.88% below), the platform's own monthly signups
were scaled with the title's, and each month's churn rate was left
reading against a base that was no longer on the page.

  [1] the floor itself (pure): a raise and a trim pass, a cut to under
      half does not, an unreadable pair passes
  [2] the incident, replayed: the cut is refused, viewers and signups
      stand, nothing is rescaled, every rate still agrees with its count
  [3] a trim inside the floor is taken, and the file moves together:
      both conversion rates, the three summary shares, each month's
      watched share; the platform's monthly signups and its churn rates
      do not move
  [4] the old half-rescale cannot come back: with the floor switched off
      in the test, the same 10,000 cut leaves no rate on the old base
  [5] a raise inside the existing evidence rules still applies
  [6] an analyst-locked figure and a held title figure are untouched

Hermetic: no network, no model calls.
Run: python3 scripts/test_subiq_late_cut_floor.py  (exit 0 = pass)
"""
import os
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
        print(f'  FAIL  {name}  {str(detail)[:300]}')


def gp(n):
    return svod.format_gen_pop(svod.gen_pop_projection(n))


W, S = 29828, 858            # the run's own figures
NEW, REACT = 659, 199
MONTHS = [("2025-12", 11528, 474), ("2026-01", 12106, 253), ("2026-02", 12303, 131)]
CHURN = [("2025-12", 1156), ("2026-01", 946), ("2026-02", 958)]
TOUCH = [("1st Touchpoint", 617), ("2nd Touchpoint", 52), ("3rd Touchpoint", 31),
         ("4th Touchpoint", 27), ("5th Touchpoint", 131)]
AGE = [("18-24", 108), ("25-34", 314), ("35-44", 254), ("45-54", 91), ("55-64", 45), ("65 or Older", 37), ("Other", 9)]
GENDER = [("Male", 574), ("Female", 241), ("Trans Male", 9), ("Trans Female", 9), ("Non-Binary", 17),
          ("Prefer Not to Say", 8)]


def tracker():
    r = []
    blank = ("", "", "", "", "", "", "", "", "", "")
    r.append(("", "", "KEY METRICS", "", "", "", "", "", "", "Gen Pop Projection"))
    r.append(("Total Show Watchers", "", W, "", "", "", "", "", "", gp(W)))
    r.append(("Pre-Existing Series Viewers", "", 0, "", "", "", "", "", "", "0"))
    r.append(("Clean Sample (New First Time Viewers)", "", W, "", "", "", "", "", "", gp(W)))
    r.append(("New Platform Signups", "", S, "", "", "", "", "", "", gp(S)))
    r.append(("Clean Conversion Rate", "", "", "", "", "", "", "", f"{S * 100.0 / W:.2f}%", ""))
    r.append(("Total Show Conversion Rate", "", "", "", "", "", "", "", f"{S * 100.0 / W:.2f}%", ""))
    r.append(blank)
    r.append(("", "", "ATTRIBUTION SUMMARY", "", "", "", "", "", "", ""))
    r.append(("", "", "(% of Total Show Watchers)", "", "", "", "", "", "", ""))
    r.append(("Attributed Signups", "", NEW, "signups", "", "(signed up then watched)", "", "",
              f"{round(NEW * 100.0 / W, 2)}%", gp(NEW)))
    r.append(("Dormant to Reactive", "", REACT, "signups", "", "(signed up before the exclusion period)", "", "",
              f"{round(REACT * 100.0 / W, 2)}%", gp(REACT)))
    r.append(("TOTAL SIGNUPS", "", S, "signups", "", "", "", "", f"{round(S * 100.0 / W, 2)}%", gp(S)))
    r.append(blank)
    r.append(("", "", "POST-SIGNUP TOUCHPOINT ANALYSIS", "", "", "", "", "", "", ""))
    for lb, c in TOUCH:
        r.append((lb, "", c, "accounts activated", "", "", "", "", f"{c * 100.0 / S:.2f}%", gp(c)))
    r.append(("Total Platform Signups", "", S, "accounts activated", "", "", "", "", "100.00%", gp(S)))
    r.append(blank)
    r.append(("", "", "MONTHLY PLATFORM SIGNUPS - Starz", "", "", "", "", "", "", ""))
    for m, plat, watched in MONTHS:
        r.append((m, "", plat, "signups", watched, "watched show", "", "", f"{watched * 100.0 / plat:.1f}%", gp(plat)))
    r.append(blank)
    r.append(("", "", "MONTHLY PLATFORM CHURN - Starz", "", "", "", "", "", "", ""))
    for (m, c), (_m, plat, _w) in zip(CHURN, MONTHS):
        r.append((m, "", c, "churned", "", "", "", "", f"{c * 100.0 / plat:.1f}%", gp(c)))
    r.append(blank)
    r.append(("", "", "DEMOGRAPHICS - New Signups", "", "", "", "", "", "", ""))
    r.append(("AGE", "", "", "", "", "", "", "", "", ""))
    for lb, c in AGE:
        r.append((lb, "", c, "people", "", "", "", "", f"{c * 100.0 / S:.2f}%", gp(c)))
    r.append(("GENDER", "", "", "", "", "", "", "", "", ""))
    for lb, c in GENDER:
        r.append((lb, "", c, "people", "", "", "", "", f"{c * 100.0 / S:.2f}%", gp(c)))
    return pd.DataFrame(r, columns=COLS).astype(object)


def num(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def cell(df, cat, col="Count", nth=0):
    rows = df[df["Category"] == cat]
    return num(rows.iloc[nth][col])


def months(df):
    """[(month, platform signups, watched, share)] then [(month, churned, rate)]."""
    m = df[df["Category"].astype(str).str.match(r"^\d{4}-\d{2}$")]
    sig = m[m["Count Label"] == "signups"]
    ch = m[m["Count Label"] == "churned"]
    return ([(r["Category"], num(r["Count"]), num(r["Secondary Count"]), num(r["Percentage"])) for _, r in sig.iterrows()],
            [(r["Category"], num(r["Count"]), num(r["Percentage"])) for _, r in ch.iterrows()])


def whole(df, label):
    """Every rate in the file agrees with the count it sits on."""
    w, s = cell(df, "Total Show Watchers"), cell(df, "New Platform Signups")
    clean = cell(df, "Clean Sample (New First Time Viewers)")
    ok = True
    ok &= abs(cell(df, "Total Show Conversion Rate", "Percentage") - s * 100.0 / w) < 0.006
    ok &= abs(cell(df, "Clean Conversion Rate", "Percentage") - s * 100.0 / clean) < 0.006
    ok &= abs(cell(df, "TOTAL SIGNUPS", "Percentage") - cell(df, "TOTAL SIGNUPS") * 100.0 / w) < 0.006
    ok &= abs(cell(df, "TOTAL SIGNUPS", "Percentage") - cell(df, "Total Show Conversion Rate", "Percentage")) < 0.011
    for lb in ("Attributed Signups", "Dormant to Reactive"):
        ok &= abs(cell(df, lb, "Percentage") - cell(df, lb) * 100.0 / w) < 0.006
    sig, ch = months(df)
    for _m, plat, watched, share in sig:
        ok &= abs(share - watched * 100.0 / plat) < 0.06
    for (_m, churned, rate), (_m2, plat, _w, _s) in zip(ch, sig):
        ok &= abs(rate - churned * 100.0 / plat) < 0.06
    check(label, ok, df[["Category", "Count", "Secondary Count", "Percentage"]].to_string())


NOTE = ("No public viewership figure exists for this title; comparable premium dramas on niche services "
        "draw roughly 300,000 US viewers in a season window.")


def verdict(w_range=None, s_range=None, c_range=None):
    v = {'passed': False, 'watchers_note': NOTE,
         'flags': ['Projected watchers number seems inflated', 'New signups number seems inflated'],
         'overall_assessment': 'Inflated for a niche platform.'}
    if w_range:
        v.update(watchers_plausible=False, suggested_watchers_range_panel=list(w_range))
    if s_range:
        v.update(signups_plausible=False, suggested_signups_range_panel=list(s_range))
    if c_range:
        v.update(conversion_plausible=False, suggested_conversion_range=list(c_range))
    return v


print('== [1] the floor, pure')
check('a cut to a third is refused', svod.late_cut_refused(29828, 10000))
check('a cut to just under half is refused', svod.late_cut_refused(858, 400))
check('a trim to 80% passes', not svod.late_cut_refused(29828, 24000))
check('a trim to exactly half passes (the floor is the edge, not inside it)', not svod.late_cut_refused(1000, 500))
check('a raise passes', not svod.late_cut_refused(2387, 4000))
check('an unreadable pair passes', not svod.late_cut_refused(None, 'x') and not svod.late_cut_refused(0, 10))
check('the floor is half', svod.LATE_CUT_FLOOR == 0.5)

print('== [2] the incident: 29,828 to 10,000 and 858 to 400')
df = tracker()
before = df.copy()
p = {'platform_name': 'Starz'}
out, changes = svod.apply_ai_adjustments(df, verdict((8000, 12000), (350, 450)), W, S, 'Starz', p)
check('viewers stand', cell(out, "Total Show Watchers") == W, cell(out, "Total Show Watchers"))
check('signups stand', cell(out, "New Platform Signups") == S and cell(out, "TOTAL SIGNUPS") == S)
check('nothing was rescaled', not any('Rescaled' in c for c in changes), changes)
check('every cell is as it was', out.astype(str).equals(before.astype(str)))
check('the refusals are on the audit trail', len(p.get('_ai_flags') or []) >= 2, p.get('_ai_flags'))
whole(out, 'every rate agrees with its count')

print('== [3] a trim inside the floor is taken, whole')
df = tracker()
out, changes = svod.apply_ai_adjustments(df, verdict((23000, 25000), (580, 620)), W, S, 'Starz', {'platform_name': 'Starz'})
check('viewers moved to 24,000', cell(out, "Total Show Watchers") == 24000, cell(out, "Total Show Watchers"))
check('signups moved to 600', cell(out, "New Platform Signups") == 600 and cell(out, "TOTAL SIGNUPS") == 600)
check('clean sample follows the viewers', cell(out, "Clean Sample (New First Time Viewers)") == 24000)
sig, ch = months(out)
check("the platform's own monthly signups did not move", [x[1] for x in sig] == [float(m[1]) for m in MONTHS], sig)
check('the title\'s monthly counts moved with its signups',
      all(a[2] < b[2] for a, b in zip(sig, MONTHS)) and abs(sum(x[2] for x in sig) - 600) <= 2, sig)
check("the platform's churn did not move", [(x[1], x[2]) for x in ch] ==
      [(float(c), round(c * 100.0 / m[1], 1)) for (_a, c), m in zip(CHURN, MONTHS)], ch)
check('age and gender moved with the signups',
      abs(sum(cell(out, lb) for lb, _ in AGE) - 600) <= 4 and abs(sum(cell(out, lb) for lb, _ in GENDER) - 600) <= 4)
whole(out, 'every rate agrees with its count')

print('== [4] the half-rescale cannot come back')
keep = svod.LATE_CUT_FLOOR
svod.LATE_CUT_FLOOR = 0.0        # the floor off, in this test only: the old cut goes through
try:
    orig = svod.late_cut_refused
    svod.late_cut_refused = lambda cur, new, floor=0.0: False
    df = tracker()
    out, changes = svod.apply_ai_adjustments(df, verdict((8000, 12000), (350, 450)), W, S, 'Starz',
                                             {'platform_name': 'Starz'})
finally:
    svod.LATE_CUT_FLOOR = keep
    svod.late_cut_refused = orig
check('the cut went through in this test', cell(out, "Total Show Watchers") == 10000 and cell(out, "New Platform Signups") == 400)
check('key metrics and the summary read one conversion',
      abs(cell(out, "Total Show Conversion Rate", "Percentage") - 4.0) < 0.006
      and abs(cell(out, "TOTAL SIGNUPS", "Percentage") - 4.0) < 0.006,
      (cell(out, "Total Show Conversion Rate", "Percentage"), cell(out, "TOTAL SIGNUPS", "Percentage")))
check('the two summary shares add to the total',
      abs(cell(out, "Attributed Signups", "Percentage") + cell(out, "Dormant to Reactive", "Percentage")
          - cell(out, "TOTAL SIGNUPS", "Percentage")) < 0.03)
sig, ch = months(out)
check("the platform's months are not scaled with the title", [x[1] for x in sig] == [float(m[1]) for m in MONTHS], sig)
whole(out, 'every rate agrees with its count')

print('== [5] a raise still applies')
df = tracker()
out, changes = svod.apply_ai_adjustments(df, verdict((33000, 35000)), W, S, 'Starz', {'platform_name': 'Starz'})
check('viewers rose to 34,000', cell(out, "Total Show Watchers") == 34000, cell(out, "Total Show Watchers"))
whole(out, 'every rate agrees with its count')

print('== [6] locked figures are untouched')
for lock in ({'reach_us_override': 984040}, {'_held_locked_reach': 984040}):
    df = tracker()
    before = df.copy()
    out, changes = svod.apply_ai_adjustments(df, verdict((8000, 12000), (350, 450)), W, S, 'Starz',
                                             dict({'platform_name': 'Starz'}, **lock))
    check(f"{list(lock)[0]}: nothing moves", out.astype(str).equals(before.astype(str)) and not changes)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
