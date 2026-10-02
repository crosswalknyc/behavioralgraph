#!/usr/bin/env python3
"""Prometheus audit regressions (2026-10-02).

Jenna: "audit prometheus now that its its own thng and find all errors
and things that coulde be errors and make it smarter and better and
ensure no bad query answers".

Every case here is a real session failure from the last two weeks.
Hermetic: no Flask, no S3, no model. Run from bg-webapp/:
    python3 scripts/test_pm_audit_2026_10_02.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prometheus import guards as G  # noqa: E402
from prometheus import understand as U  # noqa: E402

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


print("[1] question shape and capability")
Q_APPLE = ("Can I cut the existing Apple TV+ profile by Quarter "
           "(i.e., 2Q 2026)?")
check(G.is_question_shaped(Q_APPLE), "quarter ask is a question")
check(G.is_capability_question(Q_APPLE), "quarter ask is a capability question")
check(G.is_capability_question("Is it possible to compare two open profiles?"),
      "'is it possible' is capability")
check(not G.is_capability_question("Build a profile for Paw Patrol"),
      "a build order is not a capability question")
check(not G.is_question_shaped("Nike runners"), "a bare subject is not a question")
check(G.is_question_shaped("what % are female"),
      "interrogative opener without '?' still reads as a question")

print("[2] subscriber iq: question vs pull")
check(G.subiq_is_explicit_pull("Pull Subscriber IQ for Outlander on Starz"),
      "pull verb + product = pull")
check(G.subiq_question_not_build(
    "What does the churn number on this Subscriber IQ page mean?", has_ctx=True),
    "definition question on an open page is not a build")
check(G.subiq_question_not_build(
    "Can Subscriber IQ show me retention by season?", has_ctx=False),
    "capability question about the product is not a build")
check(not G.subiq_question_not_build(
    "Run Subscriber IQ on Outlander season 7", has_ctx=False),
    "a pull stays a pull")

print("[3] bare replies")
for t, kind in (("ok", 'affirm'), ("thanks!", 'affirm'), ("yes", 'affirm'),
                ("no", 'negative'), ("Something else", 'negative'),
                ("cancel", 'negative'), ("none", 'none'),
                ("2", 'number'), ("1 and 2", 'number'),
                ("the second one", 'number')):
    check(G.bare_reply_kind(t) == kind, f"{t!r} -> {kind}")
check(G.bare_reply_kind("what % are female?") is None,
      "a real question is not a bare reply")
check(G.bare_reply_kind("Paw Patrol viewers") is None,
      "a named audience is not a bare reply")

print("[4] streaming platforms in the title slot")
for p in ("Starz", "Starz+", "Netflix", "Paramount+", "Roku", "Starz subscribers",
          "The Roku Channel", "Apple TV+", "HBO Max"):
    check(G.is_streaming_platform(p), f"{p} is a platform, not a title")
for p in ("Outlander", "The Pitt", "Heated Rivalry", "Outlander: Blood of My Blood"):
    check(not G.is_streaming_platform(p), f"{p} is a title")

print("[5] subject sanitizer")
check(G.sanitize_subject_label(
    "Starz+ ; The prior turn asked for Starz; this turn asks for Starz+")
    == "Starz+", "model reasoning stripped from the subject")
check(G.sanitize_subject_label("Appeal of the Spiderwick Franchise")
      == "Spiderwick Franchise", "abstract frame stripped")
check(G.sanitize_subject_label("Potential The Influencer Project")
      == "The Influencer Project", "'Potential' before a title stripped")
check(G.sanitize_subject_label("potential chipotle eaters")
      == "potential chipotle eaters", "persona tail kept")
check(G.sanitize_subject_label("Outlander: Blood of My Blood")
      == "Outlander: Blood of My Blood", "subtitle kept")
check(G.sanitize_subject_label("I.e Can I Cut") == '',
      "interrogative fragment yields nothing usable")
check(G.sanitize_subject_label("2Q 2026 Can I Cut") == '',
      "quarter fragment yields nothing usable")

print("[6] quarters and time-window cuts")
qs = G.parse_quarters("2Q 2026 and 3Q 2026")
check([q['label'] for q in qs] == ['2Q 2026', '3Q 2026'], "two quarters parsed")
check(qs[0]['start'] == '2026-04-01' and qs[0]['end'] == '2026-06-30',
      "2Q 2026 dates")
qs2 = G.parse_quarters("Q1 and Q2 2025")
check(len(qs2) == 2 and all(q['start'].startswith('2025') for q in qs2),
      "year carries to the unlabeled quarter")
w = G.time_window_cut_ask("I want cuts by Quarter based on dates")
check(w and w['kind'] == 'quarter' and not w.get('quarters'),
      "unnamed quarter ask asks which quarters")
w2 = G.time_window_cut_ask("female and 18-34")
check(w2 is None, "demo cut reply is not a time-window ask")
check(G.window_phrase('2026-04-01', '2026-06-30') == 'Apr 1 to Jun 30, 2026',
      "window phrase")

print("[7] capability answers")
ans = G.capability_answer(Q_APPLE)
check(ans and ans['family'] == 'time_window_cut', "quarter ask answered as a time window")
check('Apple TV+' in ans['reply'] and '2Q 2026' in ans['reply'],
      "names the profile and the quarter")
check(ans['followups'] and any('2Q 2026' in f for f in ans['followups']),
      "offers a chip that runs the quarter")
check('credit' not in ans['reply'].lower() or 'brief' in ans['reply'].lower(),
      "no price quoted that could contradict the brief")
check('\u2014' not in ans['reply'], "no em dash")
check(G.capability_answer("Build a profile for Paw Patrol") is None,
      "a build order gets no capability answer")

print("[8] humanize names")
check(G.humanize_name("THE_ROKU_CHANNEL") == "The Roku Channel", "file key -> name")
check(G.humanize_name("NFL_ON_NETFLIX").startswith("NFL"), "keeps NFL caps")
check(G.humanize_name("Outlander") == "Outlander", "clean name untouched")

print("[9] understand.decide routing")


def _d(text, ctx=None, history=None):
    return U.decide(text, has_ctx=bool(ctx))


d = _d(Q_APPLE)
check(d['surface'] == 'analyze' and d['reason'] in ('capability_question',
                                                      'question_shape',
                                                      'analysis_question'),
      "quarter capability ask never becomes a build")
d = _d("What does the churn number on this Subscriber IQ page mean?",
       ctx={'primary': {'name': 'Outlander', 'product': 'subscriber_iq'}})
check(d['surface'] == 'analyze', "SubIQ definitional question answers, no draft")
d = _d("ok")
check(d['reason'] == 'short_reply:affirm', "bare ok is a short reply")
d = _d("Something else", ctx={'primary': {'name': 'Outlander'}})
check(d['surface'] == 'analyze' and d['reason'].startswith('short_reply'),
      "'Something else' with a page open stays on the answer side")
d = _d("Pull Subscriber IQ for Outlander on Starz")
check(d['surface'] in ('interpret', 'subiq', 'build') or d['reason'].startswith('subiq'),
      "a real SubIQ pull still goes to the build side")
d = _d("build a profile for Paw Patrol")
check(d['surface'] == 'interpret', "a build order still builds")

print("[10] service.ask gates (stub host)")
from prometheus.host import host  # noqa: E402
from prometheus import service  # noqa: E402

calls = []


def _analyze_core(user, body, text, history):
    calls.append(('analyze', body))
    return {'success': True, 'reply': 'READ ' + text, 'followups': []}


def _interpret_core(user, body, text, history):
    calls.append(('interpret', body))
    return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}


host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
          deck_core=lambda *a: {'success': True, 'reply': 'deck'},
          ask_hint=lambda **k: None, gate_analyze=lambda u: True,
          gate_pull=lambda u: True, funds_gate=lambda u: None,
          gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
          calm_payload=lambda: {'success': False, 'error': 'calm'},
          error_email=lambda *a: None, has=lambda c: False,
          load_threads_index=lambda u: {'threads': []}, max_threads=50,
          s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
          thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
          thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
          job_owner_ok=lambda a, b: True)
USER = {'username': 'audit'}

env, st = service.ask(USER, {'text': Q_APPLE, 'surface': 'interpret',
                             'client': 'dashboard', 'history': []})
check(st == 200 and not calls and env['decision']['reason'] == 'capability_question',
      "quarter capability ask: answered at once, no build drafted")
check(env['raw'].get('guidance') and 'Apple TV+' in env['raw']['error']
      and env['raw'].get('followups'),
      "build-flow client gets guidance text plus chips")

calls.clear()
env, st = service.ask(USER, {'text': 'ok', 'surface': 'interpret',
                             'client': 'dashboard', 'history': []})
check(not calls and env['decision']['reason'] == 'short_reply',
      "bare 'ok' with nothing pending: no core ran")
check('Nothing is waiting' in env['raw']['error'],
      "says nothing is waiting on a yes")

calls.clear()
hist = [{'role': 'user', 'text': 'who watches more, Outlander or The Pitt'},
        {'role': 'agent', 'text': 'Which one do you mean?\n1. Outlander\n2. The Pitt'}]
env, st = service.ask(USER, {'text': '2', 'surface': 'analyze',
                             'client': 'dashboard', 'history': hist})
check(calls and calls[0][0] == 'analyze' and 'The Pitt' in calls[0][1]['text'],
      "'2' after a numbered question runs the second option")

calls.clear()
env, st = service.ask(USER, {'text': 'thanks', 'surface': 'analyze',
                             'client': 'dashboard', 'history': []})
check(not calls and 'Anytime' in env['raw']['reply'],
      "'thanks' is acknowledged, not analyzed")

calls.clear()
env, st = service.ask(USER, {'text': 'Something else', 'surface': 'analyze',
                             'client': 'dashboard', 'history': [],
                             'page_context': {'primary': {'name': 'Outlander'}}})
check(not calls and 'audience' in env['raw']['reply'],
      "'Something else' asks for the audience instead of guessing")

calls.clear()
env, st = service.ask(USER, {'text': 'build a profile for Paw Patrol',
                             'surface': 'interpret', 'client': 'dashboard',
                             'history': []})
check(calls and calls[0][0] == 'interpret' and env['kind'] == 'draft',
      "a normal build still drafts")

calls.clear()
env, st = service.ask(USER, {'text': 'no', 'surface': 'analyze',
                             'client': 'dashboard', 'history': [],
                             'confirm_open_screen': True,
                             'page_context': {'primary': {'name': 'Outlander'}}})
check(calls and calls[0][0] == 'analyze',
      "an armed body (open-screen confirm) passes the short reply through")

print("[11] panel facts never judge or read the subject back")
try:
    import prometheus_analysis as pma
    for q in ("is this a strong number?",
              "What % of Roku subscribers stayed through the season?",
              "How old is this audience? And is that typical?"):
        check(pma.detect_panel_fact(q) is None, f"not a panel fact: {q!r}")
    check(pma.detect_panel_fact("what % are female?") is not None,
          "a plain share ask is still a panel fact")
    check(pma._humanize_subject("THE_ROKU_CHANNEL") == "The Roku Channel",
          "subject name humanized")
except ImportError as e:
    print(f"  skip  prometheus_analysis not importable here ({e})")

print("[12] no undefined names in the Prometheus package")
try:
    out = subprocess.run(
        [sys.executable, '-m', 'pyflakes',
         os.path.join(ROOT, 'prometheus'),
         os.path.join(ROOT, 'prometheus_analysis.py'),
         os.path.join(ROOT, 'insights_ledger.py')],
        capture_output=True, text=True)
    undefined = [ln for ln in out.stdout.splitlines()
                 if 'undefined name' in ln]
    check(not undefined, "pyflakes: no undefined names")
    for ln in undefined[:10]:
        print("     ", ln)
except Exception as e:
    print(f"  skip  pyflakes unavailable ({e})")

print("[13] user-facing copy")
chat_src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'),
                encoding='utf-8').read()
check('approve to queue' not in chat_src, "no 'approve to queue' copy")
idx = os.path.join(ROOT, 'templates', 'index.html')
if os.path.exists(idx):
    html = open(idx, encoding='utf-8').read()
    check('in queue)' not in html and "in queue.'" not in html,
          "widget: no 'in queue' copy")
    check("case 'addon_cuts'" in html, "widget: add-on cut stage has a label")
    check('_synthChatDateConfirmSplit' in html,
          "widget: default-window reply plus detail folds instead of re-asking")
    check(re.search(r"something else\|no\|nope", html),
          "widget: 'no' to a guess asks for the audience")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
