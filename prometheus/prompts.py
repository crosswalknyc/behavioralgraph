"""Prompt registry (audit item 6, Jenna 2026-10-14).

Nine system prompts had grown by accretion with no owner, no version,
no size budget and no test of their own. This registry names each one,
gives it a version and a token budget, lists the invariants its text
must carry and the words it must never carry, and binds the evals that
exercise it. `scripts/test_prompt_registry.py` runs it nightly: a prompt
over budget, missing an invariant, or carrying a banned word fails the
build before it ships.

Budgets are set at today's measured size rounded up a little, so a
prompt can only shrink from here; the target in `target_tokens` is where
the diet is headed (the build rulebook: 5,000 tokens for a plain ask,
from about 12,000 after Reader-aware pruning and 19,800 before it).
"""
from __future__ import annotations

import re

BANNED = (r"—", r"–")                      # never in any text, prompts included (no-em-dashes)
INTERNAL = (r"\bsynth\b", r"\bhostmap\b", r"\bHetzner\b", r"\bClaude\b")   # noted, not failed: prompts may name internal terms to forbid them
_TOK = 4.0   # chars per token, the house estimate


def tokens(text):
    return int(len(str(text or '')) / _TOK)


def _rulebook(text='audience of Taylor Swift fans', candidates=None, reading=None):
    """The build rulebook as assembled for an ask (pruned the way the
    service prunes it)."""
    from .host import host
    import importlib
    chat = importlib.import_module('prometheus.legacy.chat')
    sp, _up = chat._synth_chat_interpret_prompts(text, chat_history=[], master_categories={}, candidate_matches=candidates or [])
    from . import smarter
    pruned, dropped = smarter.prune_rulebook(sp, text, candidates, reading=reading)
    return pruned


def _analysis():
    import prometheus_analysis as pma
    return pma.ANALYSIS_SYSTEM_PROMPT


def _metrics():
    import prometheus_analysis as pma
    t = pma._REASONED_METRICS_SYSTEM_PROMPT_T
    return t if isinstance(t, str) else str(t)


def _reader():
    from . import reader
    return reader.CHARTER


def _journey():
    # journey_synthesis lives only in bg-webapp/migration; the root
    # `migration` package can shadow it, so load by path when it does
    try:
        from migration import journey_synthesis as js
        return js.RESEARCH_SYSTEM_PROMPT
    except ImportError:
        import importlib.util
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'migration', 'journey_synthesis.py')
        spec = importlib.util.spec_from_file_location('journey_synthesis_prompt_probe', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.RESEARCH_SYSTEM_PROMPT


def _deck():
    import prometheus_analysis as pma
    return pma.DECK_PLAN_SYSTEM_PROMPT


REGISTRY = {
    'interpret.rulebook.full': {
        'version': '2026.10.14', 'owner': 'build drafting', 'loader': lambda: _rulebook(candidates=[{'s3_key': 'x', 'display_name': 'X'}], reading=None),
        'budget_tokens': 13000, 'target_tokens': 9000,
        'invariants': ('OUTPUT SHAPE', 'DECISION LOGIC', 'STRICT VOCABULARY RULE', 'SAMPLE-SIZE', 'AUDIENCE TYPE'),
        'note': 'the whole rulebook with candidates (worst case); 19,838 before the 2026-10-14 diet'},
    'interpret.rulebook.plain': {
        'version': '2026.10.14', 'owner': 'build drafting',
        'loader': lambda: _rulebook(reading={'intent': 'build_profile', 'confidence': 0.95, 'subjects': [{'as_written': 'Taylor Swift', 'catalog': 'Taylor Swift'}], 'flags': []}),
        'budget_tokens': 4800, 'target_tokens': 4000,
        'invariants': ('OUTPUT SHAPE', 'DECISION LOGIC', 'STRICT VOCABULARY RULE', 'SAMPLE-SIZE', 'AUDIENCE TYPE'),
        'note': 'a plain profile ask with a confident reading: the Reader-aware prune; 8,901 before the 2026-10-14 diet, 19,838 before the audit'},
    'analysis.system': {'version': '2026.10.14', 'owner': 'page answers', 'loader': _analysis, 'budget_tokens': 6400, 'target_tokens': 3500,
                        'invariants': ('MULTIPLE QUESTIONS',), 'note': 'the page analyst'},
    'metrics.system': {'version': '2026.10.14', 'owner': 'reasoned reads', 'loader': _metrics, 'budget_tokens': 3900, 'target_tokens': 2500,
                       'invariants': ('SUBSCRIPTION CHURN, RETENTION, REACTIVATION AND COHORT DURABILITY ARE DIGITAL BEHAVIORS',), 'note': 'the reasoned-metrics read'},
    'reader.charter': {'version': '2026.10.14', 'owner': 'the Reader', 'loader': _reader, 'budget_tokens': 1200, 'target_tokens': 1000,
                       'invariants': ('A question is a question', 'Return JSON only'), 'note': 'one understanding step'},
    'journey.research': {'version': '2026.10.14', 'owner': 'Digital Journey', 'loader': _journey, 'budget_tokens': 3200, 'target_tokens': 2500,
                         'invariants': ('CONVERSION NORMS',), 'note': 'journey research'},
    'deck.plan': {'version': '2026.10.14', 'owner': 'decks', 'loader': _deck, 'budget_tokens': 4000, 'target_tokens': 2500,
                  'invariants': (), 'note': 'the slide plan'},
}


def check(name, entry=None):
    """{'name','tokens','budget','ok','problems'} for one registered prompt."""
    entry = entry or REGISTRY[name]
    problems = []
    try:
        text = str(entry['loader']() or '')
    except Exception as e:
        return {'name': name, 'tokens': None, 'budget': entry.get('budget_tokens'), 'ok': False, 'problems': [f'loader failed: {type(e).__name__}: {e}'[:200]]}
    n = tokens(text)
    if n > int(entry.get('budget_tokens') or 0):
        problems.append(f"over budget: {n} > {entry['budget_tokens']} tokens")
    for inv in entry.get('invariants') or ():
        if inv not in text:
            problems.append(f"missing invariant: {inv!r}")
    for rx in BANNED:
        if re.search(rx, text):
            problems.append(f"banned: /{rx}/")
    notes = [f"internal term /{rx}/ x{len(re.findall(rx, text))}" for rx in INTERNAL if re.search(rx, text)]
    return {'name': name, 'tokens': n, 'budget': entry.get('budget_tokens'), 'target': entry.get('target_tokens'),
            'version': entry.get('version'), 'ok': not problems, 'problems': problems, 'notes': notes}


def check_all():
    return [check(n, e) for n, e in REGISTRY.items()]


def render_lines(results):
    lines = ['Prompt registry:']
    for r in results:
        lines.append(f"- {r['name']} v{r.get('version')}: {r.get('tokens')} tokens (budget {r.get('budget')}, target {r.get('target')}) {'ok' if r['ok'] else 'FAIL ' + '; '.join(r['problems'])}")
    return lines
