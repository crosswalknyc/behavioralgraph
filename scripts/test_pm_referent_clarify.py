#!/usr/bin/env python3
"""Unresolved-referent gate + correction distillation (2026-10-02).

Jenna: "it should have asked him which 3 influencers he was talking
about then actually given him the answer" and "I don't mean for what I
typed in to be the served answer ... but what to do to correct it."

Hermetic: no Flask, no S3, no model. Run from bg-webapp/:
    python3 scripts/test_pm_referent_clarify.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prometheus import referents as R  # noqa: E402
import prometheus_corrections as pmc  # noqa: E402
import prometheus_knowledge as pk  # noqa: E402

FAILS = []


def check(cond, label):
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


SCOTT = ("review these three creators and preapre a report on which of "
         "the three actually influence product purchases and list some "
         "of the categories")

print("[1] detect_unresolved")
ref = R.detect_unresolved(SCOTT)
check(ref is not None and ref['noun'] == 'creators' and ref['count'] == 3,
      "Scott's ask: three unnamed creators")
check(R.detect_unresolved("compare both shows") is not None,
      "'both shows' is a referent")
check(R.detect_unresolved("rank all 4 titles by reach") is not None,
      "'all 4 titles' is a referent")
check(R.detect_unresolved("which of these two podcasts skews younger")
      is not None, "'these two podcasts' is a referent")
check(R.detect_unresolved("what are the brands they buy") is None,
      "plain 'the brands' is not a referent")
check(R.detect_unresolved("what are the top brands for Paw Patrol viewers")
      is None, "named subject, no referent")
check(R.detect_unresolved(
    "compare these three creators: MrBeast, Emma Chamberlain, Logan Paul")
    is None, "names listed after a colon resolve it")
check(R.detect_unresolved("compare these three creators @mrbeast @emma")
      is None, "handles resolve it")
check(R.detect_unresolved(
    "compare these two profiles",
    ctx={'primary': {'name': 'A'}, 'extras': [{'name': 'B'}]}) is None,
    "two profiles open on the compare view resolve 'these two profiles'")
check(R.detect_unresolved("compare these two profiles",
                          ctx={'primary': {'name': 'A'}}) is not None,
      "one profile open does not resolve 'these two profiles'")
check(R.detect_unresolved(
    "which of those three creators sells the most",
    history=[{'role': 'user', 'text': 'look at MrBeast, Emma Chamberlain '
              'and Logan Paul'}]) is None,
    "names in a recent user turn resolve it")

print("[2] clarify_payload")
pay = R.clarify_payload(ref, SCOTT)
check(pay['action'] == 'clarify' and pay['success'] is True,
      "payload is a clarify")
check(pay['reply'].startswith("Which three creators?"),
      "asks which three creators")
check('product purchases' in pay['reply'] and 'categories' in pay['reply'],
      "says what runs once the names land")
check('\u2014' not in pay['reply'], "no em dash")
check(pay['referent_clarify']['question'] == SCOTT,
      "carries the original question for the merge")
check(not pay['followups'], "no chips, no offer, no price")

print("[3] answer_merge")
hist = [{'role': 'user', 'text': SCOTT},
        {'role': 'agent', 'text': pay['reply'],
         'meta': {'referent_clarify': pay['referent_clarify']}}]
merged, label = R.answer_merge(hist, "MrBeast, Emma Chamberlain and Logan Paul")
check(label == "MrBeast, Emma Chamberlain and Logan Paul", "label joins the names")
check(merged.startswith("review MrBeast, Emma Chamberlain and Logan Paul and "
                        "preapre a report"),
      "names replace the referent phrase in the original question")
check(R.detect_unresolved(merged) is None, "merged question is resolved")
m2, _ = R.answer_merge(hist, "what does a credit cost?")
check(m2 == '', "a question is not a names answer")
m3, _ = R.answer_merge([{'role': 'user', 'text': 'hi'},
                        {'role': 'agent', 'text': 'Hello.'}],
                       "MrBeast, Emma Chamberlain")
check(m3 == '', "names after a non-clarify turn do not merge")
# Dashboard history carries text only (no meta): text fallback works.
hist_text_only = [{'role': 'user', 'text': SCOTT},
                  {'role': 'agent', 'text': pay['reply']}]
m4, l4 = R.answer_merge(hist_text_only, "mrbeast, emma chamberlain, logan paul")
check(m4 and 'mrbeast' in m4 and l4.count(',') == 1,
      "text-only history still merges (lowercase names ok)")

print("[4] plausible_subject")
check(not R.plausible_subject("Three Actually Influence Product Purchases and"),
      "the defect subject is rejected")
check(not R.plausible_subject("the three"), "'the three' rejected")
check(R.plausible_subject("MrBeast"), "MrBeast accepted")
check(R.plausible_subject("Paw Patrol"), "Paw Patrol accepted")
check(R.plausible_subject("Outlander Blood of My Blood"),
      "long real title accepted")
check(R.plausible_subject("Will And Grace"), "Will And Grace accepted")

print("[5] distill_correction")
CORR = ("Wrong answer. It should have asked him which influencers he "
        "wants to compare and then answer the actual question.")
fb = pmc.distill_correction(SCOTT, "Three Actually ... 6 credits", CORR,
                            "Three Actually Influence Product Purchases and",
                            call_fn=lambda s, u: '')
check(fb['instruction'].startswith("On an ask like this, ask the user"),
      "fallback turns the note into an imperative rule")
check('him' not in fb['instruction'].split() and 'he' not in
      fb['instruction'].split(), "fallback drops the user pronouns")
check(all(t not in pmc._GENERIC_TERMS for t in fb['trigger_terms']),
      "fallback trigger terms carry no generic words")
check('three actually influence product purchases and' not in
      fb['trigger_terms'], "the garbage subject never becomes a trigger")
check(CORR not in fb['instruction'], "raw correction is not the statement")

mock = lambda s, u: json.dumps({  # noqa: E731
    "instruction": "When an ask points at creators, brands, or titles "
                   "without naming them, ask which ones by name first, "
                   "then answer the question that was actually asked.",
    "trigger_terms": ["these three", "which of the", "report",
                      "those brands"],
    "trigger_regex": r"\b(these|those|both)\s+(\w+\s+)?"
                     r"(creators|influencers|brands|shows|titles)\b",
    "scope": "general"})
md = pmc.distill_correction(SCOTT, "x", CORR, "x", call_fn=mock)
check(md['instruction'].startswith("When an ask points at"),
      "model instruction is used")
check('report' not in md['trigger_terms'],
      "generic 'report' is filtered out of model trigger terms")
check(md['trigger_regex'] and md['scope'] == 'general',
      "regex and scope carried")
bad_rx = lambda s, u: json.dumps({  # noqa: E731
    "instruction": "Ask which ones first, then answer the real question.",
    "trigger_terms": ["these three"], "trigger_regex": "(unclosed",
    "scope": "general"})
br = pmc.distill_correction(SCOTT, "x", CORR, "x", call_fn=bad_rx)
check(br['trigger_regex'] is None, "a regex that does not compile is dropped")

print("[6] knowledge match on distilled decisions")
dec = [{'id': 'correction_test', 'statement': 'rule',
        'match_terms': md['trigger_terms'], 'trigger': md['trigger_regex']}]
check(pk.match_decisions("compare those brands for me", decisions=dec),
      "trigger regex matches the ask shape")
check(pk.match_decisions("which of the two shows wins", decisions=dec),
      "trigger term matches")
check(not pk.match_decisions("how many people streamed Landman",
                             decisions=dec),
      "unrelated ask does not attract the rule")
check(not pk.match_decisions("prepare a report on Netflix churn",
                             decisions=dec),
      "'report' alone no longer attracts the rule")

print("[7] service.ask end to end (stub host)")
from prometheus.host import host  # noqa: E402
from prometheus import service  # noqa: E402

calls = []


def _analyze_core(user, body, text, history):
    calls.append(('analyze', body))
    return {'success': True, 'reply': 'READ on ' + str(body.get('bind_subject')),
            'panel_offer': {'credits': 6}, 'followups': ['Run it', 'Never mind']}


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
U = {'username': 'scott'}
env, st = service.ask(U, {'text': SCOTT, 'surface': 'analyze',
                          'client': 'dashboard', 'history': [],
                          'page_context': {'primary': {'name': 'Paw Patrol'}}})
check(st == 200 and env['kind'] == 'clarify'
      and env['decision']['reason'] == 'referent_clarify',
      "analyze surface: which-ones question, no core ran")
check(not calls, "no analyze or interpret core ran on the clarify")
hist = [{'role': 'user', 'text': SCOTT},
        {'role': 'agent', 'text': env['raw']['reply']}]
env2, st = service.ask(U, {'text': 'MrBeast, Emma Chamberlain and Logan Paul',
                           'surface': 'interpret', 'client': 'dashboard',
                           'history': hist})
check(calls and calls[0][0] == 'analyze'
      and calls[0][1]['bind_subject'] == 'MrBeast, Emma Chamberlain and Logan Paul'
      and 'MrBeast' in calls[0][1]['text'],
      "names from the build flow run the merged question on analyze, bound to the names")
check(env2['raw'].get('guidance') and env2['raw'].get('panel_offer')
      and env2['raw'].get('followups') and env2['raw']['error'].startswith('READ'),
      "build-flow client gets the guidance shape with the offer armed")
calls.clear()
env3, st = service.ask(U, {'text': 'compare both shows for me',
                           'surface': 'interpret', 'client': 'dashboard',
                           'history': []})
check(env3['raw'].get('guidance') and env3['raw']['error'].startswith('Which two shows?')
      and not calls, "build-flow client: which-ones question in the guidance shape")
env4, st = service.ask(U, {'text': 'build a profile for Paw Patrol',
                           'surface': 'interpret', 'client': 'dashboard',
                           'history': []})
check(calls and calls[-1][0] == 'interpret' and env4['kind'] == 'draft',
      "a normal build ask is untouched")
calls.clear()
env5, st = service.ask(U, {'text': 'MrBeast, Emma Chamberlain', 'surface': 'analyze',
                           'client': 'dashboard', 'history': hist})
check(env5['kind'] == 'answer' and env5['raw'].get('success') is True,
      "names from the analyze flow keep the analyze shape")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
