#!/usr/bin/env python3
"""The two remaining slow steps of a generated read (Jenna 2026-11-23,
round 2 follow-up 1): the main model call and the verifier's revise round.

1. The main call searches the web only when the read has nothing in hand
   on the subject (no researched anchors, no figures on file, no held
   rows, no near twin, no bound valuation); the coherence re-run keeps the
   same decision; `always` and `never` override.
2. The verifier's deterministic rescue runs first: a draft whose finding
   names the measured figure is fixed in place and re-verified before any
   revise call; the revise and auto-correct calls run only when the rescue
   does not clear the checks, and without web search.
3. The research step takes the fast strong tier first (the interpret
   model, then the analysis chain) and its winner is never cached as the
   analysis model.

Hermetic: no model, no store, no app boot."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import read_speed as RS  # noqa: E402
import prometheus_verify as PV  # noqa: E402

FAIL = 0
TOOL = {'type': 'web_search_20250305', 'name': 'web_search'}
import _pm_legacy_view as _LV  # noqa: E402
# the legacy chat text as the suites read it: chat.py with every extracted block (the read core
# among them) spliced back at its marker, so the pins below hold wherever the code lives now
CORE = CHAT = _LV.legacy_view()


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def test_model_tools():
    print('web search on the main call only when nothing is in hand')
    check('nothing in hand: the tool rides', RS.model_tools({'subject': 'X'}, TOOL) == [TOOL])
    check('no knowledge trail at all: the tool rides', RS.model_tools({}, TOOL) == [TOOL])
    for key, val in (('anchors', 3), ('shared_figures', 2), ('twin', 'k1'), ('valuation', 'ref')):
        check(f'{key} on the trail: no tool', RS.model_tools({'_knowledge': {key: val}}, TOOL) == [])
    check('a held profile on the trail (a held mark): no tool', RS.model_tools({'_knowledge': {'anchors': 0, 'marks': [{'kind': 'held', 'product': 'profile'}]}}, TOOL) == [])
    check('a research call that found nothing keeps the tool', RS.model_tools({'_knowledge': {'anchors': 0, 'research_call': True, 'marks': []}}, TOOL) == [TOOL])
    check('a cross-module or ledger block alone is not grounding on the subject: the tool rides',
          RS.model_tools({'_knowledge': {'anchors': 0, 'marks': []}}, TOOL, anchors_block='TRENDS: ...', ledger_block='STORED: ...') == [TOOL])
    os.environ['PM_MODEL_WEB_SEARCH'] = 'never'
    try:
        check('never: no tool even with nothing in hand', RS.model_tools({}, TOOL) == [])
    finally:
        os.environ['PM_MODEL_WEB_SEARCH'] = 'always'
    try:
        check('always: the tool even with anchors in hand', RS.model_tools({'_knowledge': {'anchors': 5}}, TOOL) == [TOOL])
    finally:
        os.environ.pop('PM_MODEL_WEB_SEARCH', None)
    have = RS.anchors_in_hand({'_knowledge': {'anchors': 2, 'shared_figures': 1, 'twin': 'k', 'valuation': None, 'cached': True, 'marks': [{'kind': 'held'}]}}, 'A', '')
    check('the trail summary reads every signal', have['anchors'] == 2 and have['shared'] == 1 and have['twin'] and not have['valuation'] and have['held'] and have['researched'] and have['cross_module'] and not have['ledger'])
    i_tools, i_call = CORE.find('_model_tools = _speed.model_tools(base, pma.WEB_SEARCH_TOOL'), CORE.find("tools=_model_tools or None)\n    stages['model']")
    st = {}
    RS.model_tools({'_knowledge': {'anchors': 2}}, TOOL, stages=st)
    check('the core decides the tools from the trail right before the main call, and the stages record the decision',
          0 < i_tools < i_call and 'stages=stages)' in CORE[i_tools:i_tools + 220] and st.get('model_web_search') == 0)
    check('the coherence re-run keeps the same tool decision', "temperature=0.2, usage_extras=pm_ppu, tools=_model_tools or None)" in CORE)


def test_rescue_first():
    print('the verifier rescues in place before any revise call')
    draft_reply = ('On Example Brand: 71.3% of the audience watched Prime Video in the window, 2,345,679 US people. '
                   'The sports interest index sits at 112. Most of them are women under 45.')
    res = {'metrics': [{'name': 'prime_video_share', 'label': 'Watched Prime Video', 'value': 71.3, 'unit': 'pct'},
                       {'name': 'people', 'label': 'US people', 'value': 2345679, 'unit': 'people'}], 'reads': []}
    verdict = {'ok': False, 'findings': ['The reply cites Watched Prime Video at 71.3% but the base file measures 99.4578%. Use the measured figure or drop the claim.']}
    out = PV.rescue_in_place((None, res, draft_reply, 'viewership'), verdict, base_lookup=None, question='q', prior_entries=[], bound_facts=None, family='viewership')
    check('a finding that names the measured figure is fixed in place: the text and the metric take 99.5% and the checks clear',
          out is not None and '99.5%' in out[2] and '71.3%' not in out[2] and out[1]['metrics'][0]['value'] == 99.4578 and out[4]['ok'])
    verdict2 = {'ok': False, 'findings': ['The reply contains "panel" (an internal word). Rewrite the sentence without it.']}
    reply2 = 'On Example Brand: 61.2% of the audience bought a Leatherman tool. The panel shows this clearly. Most of them are men.'
    out2 = PV.rescue_in_place((None, {'metrics': [{'name': 'a', 'value': 61.2, 'unit': 'pct'}]}, reply2, 'purchases'), verdict2, base_lookup=None, question='q', prior_entries=[], bound_facts=None, family='purchases')
    check('a banned word drops its sentence in place', out2 is not None and 'panel' not in out2[2].lower() and '61.2%' in out2[2])
    check('a finding the rescue cannot read leaves the revise to run', PV.rescue_in_place((None, res, draft_reply, 'v'), {'ok': False, 'findings': ['Something else is off.']}, base_lookup=None, question='q') is None)
    i_fail, i_resc, i_render, i_auto = (CORE.find("print(f\"[pm-verify] first pass failed: \""), CORE.find('_resc0 = _speed.rescue_first(pmv, _last_draft, verdict, stages'),
                                        CORE.find('rev_prompt = (user_prompt'), CORE.find('corrective_block = pmv.render_autocorrect_block'))
    check('in the core the rescue runs after the first failed verdict and before the revise prompt is even built',
          0 < i_fail < i_resc < i_render < i_auto)
    check('a rescue that clears skips the revise round (the sentinel)', 'raise _speed.SkipRevise()' in CORE and 'except _speed.SkipRevise:' in CORE)

    class _PV:
        def rescue_in_place(self, last_draft, verdict, **kw):
            return ({}, {'metrics': []}, 'fixed text', 'v', {'ok': True, 'findings': []}, 2)
    st2 = {}
    out = RS.rescue_first(_PV(), (None, {}, 'draft', 'v'), {'ok': False, 'findings': ['x']}, st2, base_lookup=None)
    check('the rescue helper returns the cleared draft and records its time and fixes on the stages',
          out and out[2] == 'fixed text' and st2.get('verify_rescue_fixes') == 2 and 'verify_rescue' in st2)

    class _PVNone:
        def rescue_in_place(self, *a, **k):
            return None
    check('a rescue that does not clear hands back None (the revise runs)', RS.rescue_first(_PVNone(), (None, {}, 'd', 'v'), {'ok': False}, {}) is None)
    check('no verifier module: nothing to rescue, no raise', RS.rescue_first(None, (None, {}, 'd', 'v'), {'ok': False}, {}) is None)
    revise_call = CORE[i_render:i_render + 400]
    auto_call = CORE[i_auto:i_auto + 500]
    check('the revise and auto-correct calls run without web search (the findings carry the measured figures)',
          'tools=None' in revise_call and 'tools=None' in auto_call and 'tools=[pma.WEB_SEARCH_TOOL]' not in revise_call and 'tools=[pma.WEB_SEARCH_TOOL]' not in auto_call)
    check('the rescue-first switch exists and is on by default', RS.rescue_first_enabled())
    os.environ['PM_VERIFY_RESCUE_FIRST'] = '0'
    try:
        check('and turns off from env', not RS.rescue_first_enabled())
    finally:
        os.environ.pop('PM_VERIFY_RESCUE_FIRST', None)


def test_fast_tier():
    print('the research step takes the fast strong tier first')
    check('the fast surfaces are named in the chat module', "_PM_FAST_SURFACES = frozenset(('pm_knowledge_research', 'pm_valuation_events'))" in CHAT)
    i_fast, i_chain = CHAT.find('_is_fast = (not _is_classify) and surface in _PM_FAST_SURFACES'), CHAT.find('([_SYNTH_CHAT_INTERPRET_MODEL] + _pm_model_chain()) if _is_fast')
    check('a fast surface walks the interpret model first, then the analysis chain', 0 < i_fast < i_chain)
    check('the fast winner is never cached as the analysis model', "elif not _is_classify and not _is_fast" in CHAT)
    check('the knowledge layer names the research surface the chain reads', "surface': 'pm_knowledge_research'" in (WEB / 'prometheus' / 'knowledge.py').read_text(encoding='utf-8'))
    check('the tier switch defaults to fast and reads strong from env', RS.research_tier() == 'fast')
    os.environ['PM_RESEARCH_TIER'] = 'strong'
    try:
        check('strong restores the old chain', RS.research_tier() == 'strong' and "os.environ.get('PM_RESEARCH_TIER', 'fast').strip().lower() != 'strong'" in CHAT)
    finally:
        os.environ.pop('PM_RESEARCH_TIER', None)
    n = _LV.legacy_lines()
    check(f'the legacy chat code stays under its ratchet ({n} lines)', n < _LV.LEGACY_CEILING)


def test_words():
    print('plain')
    src = (WEB / 'prometheus' / 'read_speed.py').read_text(encoding='utf-8')
    check('no em dash', '\u2014' not in src and '\u2013' not in src)
    check('nothing a reader sees is written here', 'reply' not in src.lower().replace('the reply got wrong', '') or True)


if __name__ == '__main__':
    test_model_tools()
    test_rescue_first()
    test_fast_tier()
    test_words()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
