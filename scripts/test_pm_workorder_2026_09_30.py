#!/usr/bin/env python3
"""Regression: the 2026-09-30 Prometheus improvements (Jenna: "do all
of them" + the exact working-on-it promise).

Hermetic - no Flask import, no network. Source-pattern checks on
app.py / prometheus_analysis.py plus exec-extracted detector blocks
exercised against real ask-log phrasings.

Covers:
  1. catch-all calm promise (exact copy, HELD path, ops email banner)
  2. work-order verbs (status / eta / cancel intent matrix, notify
     verb widening, listener cancel endpoint, worker checkpoints)
  3. any-of title universes (compound guard S1b + interpret rule)
  4. cross-tab prompt section
  5. challenged-number prompt section + heads-up detector
  6. trajectory else-branch + series helpers
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
PMA = open(os.path.join(ROOT, 'prometheus_analysis.py'),
           encoding='utf-8').read()

FAILS = []


def check(name, ok):
    print(('PASS' if ok else 'FAIL'), name)
    if not ok:
        FAILS.append(name)


# ------------------------------------------------ 1. catch-all promise
check("calm message is Jenna's exact sentence",
      '"Working on it. Confirmed your task is in "' in APP
      and '"progress and I will email you when it "' in APP
      and '"completes."' in APP)
check("old calm copy retired",
      'This one needs a closer look' not in APP)
check("HELD path returns the calm promise",
      "'reply': _CHATBOT_CALM_MESSAGE," in APP
      and "'_held': True" in APP)
check("HELD path no longer returns clarify chips",
      "_pm_held_read_clarify(\n                text," not in APP)
check("ops email carries the promise banner",
      'PROMISE MADE: the user was shown' in APP
      and 'DELIVER THE ANSWER BY EMAIL TO:' in APP)
check("promise banner respects _no_promise",
      "_pl_np.get('_no_promise')" in APP)
check("banner names the Prometheus sender",
      'From Prometheus ' in APP)

# --------------------------------------------- 2. work-order verbs
start = APP.index('_PM_WO_WORK_NOUNS = (')
end = APP.index('def _pm_user_runs(')
ns = {'re': re}
exec(compile(APP[start:end], 'wo', 'exec'), ns)
intent = ns['_pm_workorder_intent']

for text, want in [
    ('status', 'status'), ('any update?', 'status'),
    ('Are you still working on my report?', 'status'),
    ('is it done yet?', 'status'), ('where is my report', 'status'),
    ('stop', 'cancel'), ('cancel it', 'cancel'),
    ('please stop it', 'cancel'),
    ('cancel the Will & Grace build', 'cancel'),
    ('how long will this take?', 'eta'), ('eta', 'eta'),
    ('how much longer?', 'eta'),
    ('when will my profile be done', 'eta'),
]:
    check(f"intent {text!r} -> {want}", intent(text) == want)

for text in [
    'people who cancel Netflix in the first month',
    'how do subscribers who stop watching behave',
    'where is my audience most dense',
    'stop showing me QSR rows',
    'how long does a viewer take to churn',
    'how much longer do West Coast viewers watch than East Coast',
    'what share of viewers cancel within 90 days',
    'run a profile on Stop Making Sense viewers',
    'is the audience more male or female?',
]:
    check(f"intent {text!r} -> None", intent(text) is None)

check("work-order wired on analyze",
      APP.count("_wo_intent = _pm_workorder_intent(text)") == 2)
check("cancel POSTs the engine",
      '/synth/cancel/' in APP)
check("cancel failure falls back to the calm promise + ops email",
      "'pm/cancel'," in APP)
check("eta derives from measured build durations",
      'def _pm_typical_build_minutes' in APP
      and "status='complete'" in APP)

# notify verb widening
m = re.search(r'_PM_EMAIL_WHEN_READY_RE = re\.compile\((.*?)\n    re\.IGNORECASE\)',
              APP, re.S)
check("notify-me verbs widened",
      m is not None and 'notify' in m.group(1)
      and 'let\\s+me\\s+know' in m.group(1))
if m:
    pat = re.compile(
        r"\b(?:e-?mail|send|notify|tell|ping|alert|update|"
        r"let\s+me\s+know)\b[^?!\n]{0,80}?\bwhen\b[^?!\n]{0,30}?"
        r"\b(?:ready|done|finish(?:e[sd])?|complete[sd]?|lands?|"
        r"arrives?)\b", re.I)
    for t in ("notify me when it's done",
              "let me know when the profile is ready",
              "tell me when it finishes",
              "ping me when complete",
              "email david.carter@spe.sony.com when it is ready"):
        check(f"notify phrasing matches: {t!r}", bool(pat.search(t)))

# ------------------------------------------ 3. any-of title universes
check("compound guard S1b (platform + title list)",
      "jessie's Audible thread" in APP
      and "(?:for|covering|across)\\s*:" in APP)
sg_start = APP.index('def _is_single_compound_audience')
sg_end = APP.index('def ', APP.index('return False', sg_start))
check("interpret any-of titles rule present",
      'ANY-OF / ALL-OF TITLE LISTS = ONE build' in APP
      and 'Never a batch, ' in APP)

# exercise S1b regex against the real ask
s1b = re.compile(
    r'\b(?:listeners?|viewers?|watchers?|streamers?|readers?'
    r'|players?|buyers?|renters?|subscribers?|fans?|users?)\s+'
    r'(?:on|of|to|from)\s+[A-Za-z][\w .&+-]{1,40}?\s+'
    r'(?:for|covering|across)\s*:', re.I)
check("S1b matches jessie's Audible ask",
      bool(s1b.search(
          'I need a new profile for the listeners on Audible for:\n'
          'The Weddings of Lady Miss Jacqueline Audiobook')))
check("S1b leaves a bare brand batch alone",
      not s1b.search('run profiles on VIZIO, Samsung and LG owners'))

# --------------------------------------------------- 4. cross-tab
check("cross-tab section in analysis prompt",
      'CROSS-TAB ASKS' in PMA
      and 'derived cut of this audience' in PMA
      and 'leans / skews' in PMA)

# ------------------------------------------- 5. challenged numbers
check("challenged-numbers section in analysis prompt",
      'CHALLENGED NUMBERS' in PMA
      and 'Never revise, walk back' in PMA)
ch_start = APP.index('_PM_CHALLENGE_RES = (')
ch_end = APP.index('def _pm_challenge_headsup')
ns2 = {'re': re}
exec(compile(APP[ch_start:ch_end], 'ch', 'exec'), ns2)
ch = ns2['_PM_CHALLENGE_RES']
for t in ("The Apple TV+ number seems very high",
          "why is the # of subscribers smaller than a previous analysis",
          "how is this calculated?",
          "that can't be right",
          "this doesn't match the prior read"):
    check(f"challenge fires: {t!r}", any(rx.search(t) for rx in ch))
for t in ("what are the highest indexing brands",
          "how is the audience split by gender",
          "give me a high level summary"):
    check(f"challenge silent: {t!r}",
          not any(rx.search(t) for rx in ch))
check("challenge heads-up wired on analyze",
      '_pm_challenge_headsup(user, text)' in APP)
check("challenge email suppresses the promise banner",
      "'_no_promise': True" in APP)

# --------------------------------------------------- 6. trajectory
check("trajectory helpers present",
      'def _pm_trajectory_files' in APP
      and 'def _pm_trajectory_metrics' in APP
      and 'def _pm_trajectory_reply' in APP)
check("trajectory else-branch wired",
      'elif len(_have_y) >= 2:' in APP
      and '_pm_trajectory_reply(' in APP)
check("package offer stays for missing years",
      "_pm_ask_hint(outcome='year_package_offer'" in APP)
check("trajectory answer stashes a CSV",
      'Year_Over_Year_' in APP
      and "'Email me this file'" in APP)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURES:")
    for f in FAILS:
        print(' -', f)
    sys.exit(1)
print("ALL CHECKS PASSED")
