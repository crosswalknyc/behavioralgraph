#!/usr/bin/env python3
"""Emma Friedenberg's launch-day brief (Microdramas IQ, 2026-11-04).

A 300-word brief on the Micro-Series, Measured launch cohort ("do titles
that launch on a weekend do better than weekday launches") drew "Which
title or audience are the figures for? Name it, or paste the figures, and
I will split them by device." Her follow-up, "please don't split by
device; count all devices together", drew the same clarify and then the
calm hold. Two misses:

1. prometheus_analysis.is_dimension_split_ask read "share of titles" plus
   "on any screen" as a device split of a held figure. A denied or
   universal dimension is not a split request, and in a brief the verb
   and the dimension must share a clause.
2. prometheus.derived_read.plan asked for a subject on a brief that held
   no figure and named no subject; that ask belongs to the other lanes.

And the binding: the series' figures were never banked under a subject
her words resolve. insights_ledger folds micro-series, microdramas,
vertical shorts and short dramas onto the "Vertical Drama" bucket at
resolution time; scripts/bank_micro_series_measured.py carries the figures.

Hermetic: no Flask, no S3, no model. Run from bg-webapp/:
    REGRESSION_TEST_MODE=1 python3 scripts/test_pm_microseries_launch_ask.py
"""
import os
import re
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prometheus_analysis as pma  # noqa: E402
from prometheus import derived_read as dr  # noqa: E402
import insights_ledger as il  # noqa: E402

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


EMMA_Q1 = (
    "I want to know whether micro-series titles that launch on a weekend do better than titles that launch on a weekday.\n\n"
    "Titles: every micro-series title first seen on a tracked platform from January 1 through September 15, 2026. "
    "Launch day is the first day a title appears. Read each title over its own first 28 days.\n\n"
    "Rules: US viewers (persons) on any screen, from the Crosswalk clickstream panel, projected to the US. "
    "A title start is one second of playback; autoplayed previews don't count. Include direct app and site sessions, "
    "including the Peacock Microdramas hub inside the Peacock app.\n\n"
    "Definitions:\n- Weekend launch = Friday, Saturday or Sunday. Weekday launch = Monday to Thursday. Use US Eastern time.\n"
    "- First-72-hour activity = searches for the title's name, searches for its actors' names, and shares of its clips out of TikTok and YouTube, in the 72 hours after launch.\n"
    "- Spike = first-72-hour activity at 3x or more of the title's own baseline.\n"
    "- 28-day audience = unique viewers in the title's first 28 days.\n\n"
    "For each launch day of the week, and for weekend vs. weekday, show:\n1. Number of titles that launched\n2. Share that spiked\n"
    "3. Median 28-day audience, and that number as a multiple of the median for all titles\n"
    "4. Share of titles in the top quarter of first-72-hour activity, ranked across all titles\n\n"
    "Also show launches by day of the week for each platform (ReelShort, DramaBox, GoodShort, Peacock hub, all others grouped), "
    "so I can see whether any platform launches on a fixed day.\n\n"
    "Use medians, show the number of titles in every cell, and flag any cell under 30.")
EMMA_Q2 = (
    "There's no specific title. I want all micro-series titles, grouped by the day of the week they launched. "
    "Please don't split by device; count all devices together.\n\n"
    "Start with this: list every micro-series title on ReelShort, DramaBox, GoodShort and the Peacock Microdramas hub that "
    "first appeared between January 1 and September 15, 2026. For each, show its launch date, the day of the week, and its "
    "unique US viewers in its first 28 days.\n\n"
    "Then group them: Friday to Sunday launches vs. Monday to Thursday launches. Show the number of titles and the median "
    "28-day viewers for each group.")

print("[1] the brief is not a dimension split")
check(not pma.is_dimension_split_ask(EMMA_Q1), "Emma's brief: 'share of titles' + 'on any screen' is not a device split")
check(not pma.is_dimension_split_ask(EMMA_Q2), "Emma's follow-up: 'don't split by device; count all devices together' is not a split")
check(pma.split_dimension(EMMA_Q2) == '', "the denied device dimension is not the ask's dimension")
check(dr.is_brief(EMMA_Q1) and dr.is_brief(EMMA_Q2), "both read as briefs")
check(not dr.is_brief("Split the Landman viewers by device"), "a one-liner is not a brief")

print("[2] denied and universal dimensions never split")
for t in (
    "Please don't split by device; count all devices together.",
    "Do not break it out by screen, I want one number across all devices.",
    "Rather than splitting by device, give me the total on any screen.",
    "Counts are US viewers on any screen.",
    "Share of titles in the top quarter of first-72-hour activity, ranked across all titles",
    "Which micro-series titles spiked on their first weekend?",
    "How many people watched Landman in October?",
):
    check(not pma.is_dimension_split_ask(t), f"not a split: {t[:60]}")

print("[3] Jordan's device splits still run (2026-10-12 contract)")
for t, dim in (
    ("Can you break out The Office US hours by the method of consumption (tv's, mobile, tablet etc.)?", 'device'),
    ("Split the Landman viewers by device", 'device'),
    ("break this down by daypart", 'daypart'),
    ("How does the Yellowstone viewership break out by region?", 'geography'),
    ("Where does the consumption happen, mobile or connected TV?", 'device'),
    ("Break those out. By device please.", 'device'),
    ("what is the device mix for these hours", 'device'),
):
    check(pma.is_dimension_split_ask(t) and pma.split_dimension(t) == dim, f"split by {dim}: {t[:60]}")

print("[3b] every named dimension, minus the denied and universal ones")
if hasattr(pma, 'split_dimensions'):
    check(pma.split_dimensions("break this down by age, gender, ethnicity, household income and region")
          == ['age', 'gender', 'ethnicity', 'income', 'geography'], "all five named dimensions, in the reader's order")
    check(pma.split_dimensions("break this out by age and gender, counting US viewers on any screen") == ['age', 'gender'],
          "'on any screen' does not add device to the list")
    check(pma.split_dimensions(EMMA_Q2) == [], "Emma's denied device split names no dimension")

print("[4] derived_read.plan leaves a subjectless, figureless brief to the other lanes")


class _NS:
    @staticmethod
    def mentioned_subject(t, *a, **k):
        return ''

    @staticmethod
    def thread_subject(h, *a, **k):
        return ''


_kw = dict(pma=pma, ns=_NS, normalize=lambda s: str(s).lower(), stop=set(), plausible=lambda s: True,
           consumption=lambda t: '')
check(dr.plan(EMMA_Q1, [], None, {}, **_kw) is None, "Emma's brief: plan is None (no clarify, no derived read)")
check(dr.plan(EMMA_Q2, [], None, {}, **_kw) is None, "Emma's follow-up: plan is None")
p = dr.plan("Split these by device", [], None, {}, **_kw)
check(p is not None and p.get('ask') and 'by device' in p['ask']['reply'], "a short split with nothing held still asks which title")
p2 = dr.plan("Split these by device", [], None, {'bind_subject': 'The Office US'}, **_kw)
check(p2 is not None and p2.get('ask') is None and p2['subject'] == 'The Office US', "a bound subject runs the derived read")
pc = dr.plan(EMMA_Q1, [], None, {'panel_confirm': {'kind': 'derived', 'subject': 'Landman', 'dimension': 'device'}}, **_kw)
check(pc is not None and pc['subject'] == 'Landman', "a confirmed derived read still runs on a brief")

print("[5] the ledger resolves every spelling of the family to the banked subject")
doc = {'subjects': {
    'vertical_drama': {'subject': 'Vertical Drama', 'entries': [{'metrics': [{'name': 'x', 'value': 1}]}]},
    'landman': {'subject': 'Landman', 'entries': [{'metrics': [{'name': 'x', 'value': 1}]}]},
}}
for q in (EMMA_Q1, EMMA_Q2, 'what are the top micro dramas today?', 'how many people use vertical shorts apps',
          'microdramas launch cadence by platform', 'Micro-Series, Measured episode 6 figures', 'short drama viewers by age'):
    check(il._resolve_subject(doc, question=q) == 'vertical_drama', f"resolves Vertical Drama: {q[:50]!r}")
check(il._resolve_subject(doc, subject='Microdramas IQ', question=EMMA_Q2) == 'vertical_drama', "the Microdramas IQ view title resolves the family")
check(il._resolve_subject(doc, subject='Micro-Series') == 'vertical_drama', "an explicit Micro-Series subject resolves the family")
check(il._resolve_subject(doc, question='how many people watched Landman') == 'landman', "other subjects unaffected")
check(il._resolve_subject(doc, question='what did people buy at Target') is None, "no bucket, no match")
check(il.normalize_question('what are the top micro dramas today?') == 'what are the top micro dramas today',
      "stored question keys are untouched by the alias (exact replay unchanged)")
check(il._fold_family_aliases(il._fold_family_aliases('micro series titles')) == 'vertical drama titles', "folding is idempotent")

print("[6] the banked figures are clean")
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import bank_micro_series_measured as bank  # noqa: E402

banned = re.compile(r"\b(?:synthetic|modeled|modelled|estimated|pipeline|research\w*|ledger|lane|pew|nielsen|sensor tower|statista|omdia)\b", re.I)
allm = bank.LAUNCH_ANCHORS + bank.LEADER_ANCHORS + bank.T12_ANCHORS + bank.PACKAGE_FIGURES
check(len(bank.PACKAGE_FIGURES) <= 10, "the package entry carries at most ten figures")
check(all(not banned.search(str(x.get('label', '')) + ' ' + str(x.get('name', '')) + ' ' + str(x.get('definition', ''))) for x in allm),
      "no banned vocabulary in any label or definition")
check(all('\u2014' not in str(x.get('definition', '')) and '\u2014' not in str(x.get('label', '')) for x in allm), "no em dashes")
sig = [x['value'] for x in bank.LAUNCH_ANCHORS if x['name'].startswith('signal_')]
check(abs(sum(sig) - 100.0) < 0.05, "the first-72-hour signal mix sums to 100")
check([x['value'] for x in bank.LAUNCH_ANCHORS if x['name'].endswith('_28d_audience_x')] == [3.1, 1.4, 0.8, 0.4], "quarter multiples descend 3.1, 1.4, 0.8, 0.4")
check(bank.SUBJECT == 'Vertical Drama', "everything banks under the one family subject")
check(bank.LAUNCH_WINDOW == {'start': '2026-01-01', 'end': '2026-09-15'}, "the launch cohort window is the series' own")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
