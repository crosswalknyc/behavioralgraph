"""The surface and the analyze route, decided from the Understanding
(Prometheus rebuild step 3, Jenna 2026-11-20: finish the upgrade).

Until now two older readers decided the big classes: ``understand.decide``
picked the surface (analyze / interpret / deck) from its own regex
ladder, and ``prometheus_router.route_ask`` picked the analyze core's
route (a written page analysis, the measured read, a Subscriber IQ
reroute, a CSV export, the quantifiability gate, search demand) with a
fast-model classifier as its backstop. Both ran after the Understanding
had already read the ask, and neither looked at it.

This module is what the Understanding calls instead. The deterministic
rules those readers carried that are not intents (an explicit mode or
step from the client, the Subscriber IQ family, the churn fork, a CSV
export, the quantifiability gate, search demand, the analysis-shaped
data ask) stay as rules and are called from here; the model backstop is
gone, the Understanding's intent takes its place. The decision reasons
and rows are the older vocabulary where the decision is the same row
(build_order, subiq, deck_ask, tool_intake, question, data_open), so the
widget and the ask log keep one vocabulary; the Understanding's own line
rides the ask log beside the route.

The legacy path stays reachable for one release behind one environment
switch, ``PM_LEGACY_ROUTING=1`` (never a request field, per
no-external-overrides): with it set, or when no Understanding is on the
ask, the older readers decide exactly as before. The removal plan is in
the rule file and CONTRACTS.md.

Pure Python; every outside call is fail-safe. No model call, no S3.
"""
from __future__ import annotations

import os

from . import understand as _understand

# the Understanding intents that are a read (the measured read loop, a priced read) on the analyze surface
READ_INTENTS = frozenset(('analyze', 'compare', 'size', 'brand_fit', 'insight_ask', 'multi_ask', 'exists',
                          'continuation_rerun', 'continuation_correction'))
# the intents that are the product intakes, which live on the analyze surface (the wizard lane)
INTAKE_INTENTS = frozenset(('valuation', 'journey', 'campaign', 'twin'))
# the intents that are a Profile IQ build (the interpret surface)
BUILD_INTENTS = frozenset(('build_profile', 'cut', 'tracker', 'order', 'build_fix', 'build_facts'))


def legacy_routing():
    """True when the older readers decide (PM_LEGACY_ROUTING=1 in the environment)."""
    return os.environ.get('PM_LEGACY_ROUTING', '0') in ('1', 'true', 'yes', 'on')


def _row(reason):
    try:
        from . import routing_table as _rt
        r = _rt.row_for(reason)
        return r['id'] if r else None
    except Exception:
        return None


def _u(u):
    try:
        from . import understanding as _und
        return _und.Understanding.from_dict(u) if not isinstance(u, _und.Understanding) else u
    except Exception:
        return None


# ------------------------------------------------------------- the surface
def surface(u, text, *, has_ctx=False, mode=None, extra=None, open_tabs=0, deck_in_flight=False, prior_outline=False):
    """The surface decision for one fresh ask: ``{'surface', 'mode', 'reason',
    'client_hint', 'row'}``, the shape ``understand.decide`` returns. The
    Understanding decides; the older ladder only when the switch is set
    or no Understanding is on the ask."""
    u = _u(u)
    if legacy_routing() or u is None:
        return _understand.decide(text, has_ctx=has_ctx, mode=mode, extra=extra, open_tabs=open_tabs,
                                  deck_in_flight=deck_in_flight, prior_outline=prior_outline)
    t = str(text or '').strip()
    extra = extra if isinstance(extra, dict) else {}
    d = {'surface': 'analyze', 'mode': None, 'reason': '', 'client_hint': None, 'row': None}
    if not t:
        d.update(surface='interpret', reason='empty', row=_row('empty'))
        return d
    # the client's own step is a decision already (a mode chip, a confirm payload)
    if mode and str(mode).strip():
        d.update(surface='analyze', mode=str(mode).strip().lower(), reason='explicit_mode', row=_row('explicit_mode'))
        return d
    if any(extra.get(k) for k in _understand._ANALYZE_EXTRA_KEYS):
        d.update(surface='analyze', reason='explicit_step', row=_row('explicit_step'))
        return d
    intent = str(u.intent or '')
    action = str(u.action or '')
    if u.scope.no_build and action == 'build':
        action = 'answer'
    # the product intakes live on the analyze surface (the wizard lane), by the host's own intent checks or the reading
    try:
        ti = _understand.tool_intent(t)
    except Exception:
        ti = ''
    if ti or intent in INTAKE_INTENTS or (intent == 'armed' and u.scope.armed_step in (
            'bpiq_wizard', 'bpiq_inputs', 'bpiq_confirm', 'jiq_inputs', 'jiq_confirm', 'fw_inputs', 'fw_confirm', 'aiq_inputs',
            'aiq_confirm', 'twin_inputs', 'twin_confirm')):
        d.update(surface='analyze', reason='tool_intake:' + (ti or (intent if intent in INTAKE_INTENTS else 'armed')), row=_row('tool_intake:x'))
        return d
    # a deck: the reading, or the deck rule (a deck in flight, a yes on a deck outline)
    try:
        deck_rule = _understand.looks_like_deck_ask(t, deck_in_flight, prior_deck_outline=bool(prior_outline))
    except Exception:
        deck_rule = False
    if intent == 'deck' or action == 'deck' or deck_rule:
        d.update(surface='deck', reason='deck_ask', row=_row('deck_ask'))
        return d
    # a Profile IQ build: an order for a profile (the order gate's own product read), a dataset on an audience, or a
    # build reading that names its subject; "give me the age profile for each series" or "please run one read" are
    # orders for something else and stay on the analyze surface
    try:
        from . import orders as _ord
        profile_order = bool(_ord.is_profile_order(t))
    except Exception:
        profile_order = False
    try:
        from . import dataset_ask as _dsa
        dataset_build = bool(_dsa.is_build_ask(t))
    except Exception:
        dataset_build = False
    named = any(getattr(s, 'as_written', '') or getattr(s, 'catalog', '') for s in (u.subjects or []))
    if profile_order or dataset_build or (action == 'build' and intent in ('build_profile', 'cut', 'tracker') and named):
        d.update(surface='interpret', reason='dataset_build' if (dataset_build and not profile_order) else 'build_order', row=_row('build_order'))
        return d
    # a Subscriber IQ pull is the build flow's, even with a profile open; a question about churn or signups is a
    # question (the older ladder's own rule, kept as a rule)
    try:
        from . import guards as _g
        subiq = bool(_understand.looks_subscriber_iq(t) or _understand.looks_ambiguous_churn(t))
        if subiq and not _g.subiq_question_not_build(t, has_ctx=has_ctx) and not (
                not has_ctx and not _g.subiq_is_explicit_pull(t) and _g.is_question_shaped(t)) and not (
                not _g.subiq_is_explicit_pull(t) and _g.reads_as_task(t)):
            d.update(surface='interpret', reason='subiq', row=_row('subiq'))
            return d
    except Exception:
        pass
    # everything else is answered on the analyze surface; a named analysis mode or search demand rides as the mode
    m = None
    try:
        if has_ctx:
            m = _understand.mode_for_text(t)
        if not m and _understand.looks_search_demand(t):
            m = 'search_demand'
    except Exception:
        m = None
    if m == 'search_demand':
        d.update(surface='analyze', mode=m, reason='search_demand', row=_row('search_demand'))
    elif m:
        d.update(surface='analyze', mode=m, reason='mode:' + m, row=_row('mode:x'))
    else:
        d.update(surface='analyze', mode=None, reason='data_open' if has_ctx else 'question', row=_row('data_open' if has_ctx else 'question'))
    return d


# ------------------------------------------------------- the analyze route
def analyze_route(u, text, *, surface='analyze', has_ctx=False, mode='', has_base=False, memory_referent=False,
                  other_subject=False, classify_fn=None):
    """The analyze core's route for one ask, the dict ``prometheus_router.route_ask``
    returns (``route``, ``why``, ``subiq_family``, ``fork_subject``, ``nq_gate``,
    ``used_classifier``, ``classify_kind``, ``classify_ms``). The deterministic
    prefilters stay; the Understanding's intent takes the classifier's place
    (no model call). The older router only when the switch is set or no
    Understanding is on the ask."""
    import prometheus_router as _pmr
    u = _u(u)
    if legacy_routing() or u is None:
        return _pmr.route_ask(text, surface=surface, has_ctx=has_ctx, mode=mode, has_base=has_base,
                              memory_referent=memory_referent, classify_fn=classify_fn, other_subject=other_subject)
    # the rules that are not intents, in the router's own order
    d = _pmr.route_ask(text, surface=surface, has_ctx=has_ctx, mode=mode, has_base=False,
                       memory_referent=False, classify_fn=None, other_subject=False)
    pre, why = str(d.get('route') or ''), str(d.get('why') or '')
    if pre in ('subiq', 'csv_download', 'not_quantifiable', 'search_demand') or (pre == 'clarify' and why == 'subiq_fork'):
        return d   # a rule that is not an intent decided (the Subscriber IQ family, the churn fork, a CSV export, the gate, search demand)
    d.update(used_classifier=False, classify_kind=None, classify_ms=0)
    intent = str(u.intent or '')
    action = str(u.action or '')
    surf = 'interpret' if str(surface or '').strip().lower().startswith('i') else 'analyze'
    try:
        other = _pmr._resolve(other_subject)
    except Exception:
        other = False
    if surf == 'analyze':
        if has_ctx:
            # open data is decisive, as the router had it: an analysis-shaped data ask goes straight to the measured
            # read; a subject the words name that is not the page is never a page analysis (Debi 2026-10-26); a build
            # order is the build flow's; everything else is the page analysis, whose own open-screen confirm asks
            # before it answers (the open view never decides on its own, prometheus.screen_scope)
            if _analysis_ask(text):
                d.update(route='generate', why='ctx_analysis_ask')
                return d
            if other:
                d.update(route='generate', why='named_other_subject')
                return d
            d.update(route='analysis', why='open_data_default')
            return d
        # nothing open: a build goes to the build flow; a data ask, a confident read intent or a question on a subject
        # we hold is the measured read; a remembered referent is the grounded confirm; the rest is the clarify (the
        # fast-model classifier that used to arbitrate here is gone: the Understanding's reading stands in its place)
        named = any(getattr(s, 'as_written', '') or getattr(s, 'catalog', '') for s in (u.subjects or []))
        try:
            from . import orders as _ord
            profile_order = bool(_ord.is_profile_order(text))
        except Exception:
            profile_order = False
        if profile_order or (action == 'build' and intent in ('build_profile', 'cut', 'tracker') and named):
            d.update(route='build_interpret', why='understanding:build')
            return d
        if _analysis_ask(text) or _generate_ask(text):
            d.update(route='generate', why='no_ctx_data_ask')
            return d
        confident_read = intent in READ_INTENTS and (str(u.source or '') == 'reader' or intent != 'analyze')
        if confident_read:
            d.update(route='generate', why='understanding:read')
            return d
        if _pmr._resolve(has_base):
            if intent in READ_INTENTS or _pmr._question_shaped(text):
                d.update(route='generate', why='understanding:named_base')
                return d
        if _pmr._resolve(memory_referent):
            d.update(route='memory_confirm', why='memory_referent')
            return d
        d.update(route='clarify', why='no_context')
        return d
    # the interpret surface: an analysis ask with a base on file is the measured read; the rest is the build flow
    if (intent in READ_INTENTS or _analysis_ask(text)) and _pmr._resolve(has_base) and action != 'build':
        d.update(route='generate', why='understanding:analysis_ask')
        return d
    d.update(route='build_interpret', why='build_default')
    return d


def _analysis_ask(text):
    try:
        import prometheus_analysis as pma
        return bool(pma.detect_analysis_ask(text) or pma.detect_metric_kpi_intent(text) or pma.detect_strategy_intent(text))
    except Exception:
        return False


def _generate_ask(text):
    try:
        import prometheus_analysis as pma
        return bool(pma.detect_generate_intent(text) or pma.detect_subcut_intent(text))
    except Exception:
        return False


__all__ = ['surface', 'analyze_route', 'legacy_routing', 'READ_INTENTS', 'INTAKE_INTENTS', 'BUILD_INTENTS']
