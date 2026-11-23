"""The surface and the analyze route, decided from the Understanding
(Prometheus rebuild step 3, Jenna 2026-11-20: finish the upgrade; the
older readers retired 2026-11-23, round two).

Until step 3 two older readers decided the big classes: ``understand.decide``
picked the surface (analyze / interpret / deck) from its own regex
ladder, and ``prometheus_router.route_ask`` picked the analyze core's
route (a written page analysis, the measured read, a Subscriber IQ
reroute, a CSV export, the quantifiability gate, search demand) with a
fast-model classifier as its backstop. Both ran after the Understanding
had already read the ask, and neither looked at it.

This module is the one decider now. The deterministic rules those readers
carried that are not intents stay as rules and run here in their order
(an explicit mode or step from the client, the product intakes, the deck
rule, a profile order, a dataset on an audience, a dimension split, a
cross of behaviors, a bare reply, a capability question, a product fact,
a library lookup, the Subscriber IQ family and the churn fork, the
dashboard pickers, where a title is watched, search demand, a named
analysis mode with data open, a question-shaped ask, an imperative task,
a CSV export, the quantifiability gate, open data is decisive). Between
intents the Understanding decides: a build reading on a named subject is
the build flow, an intake reading is the wizard lane, a deck reading the
deck, a confident reading that is not a build answers on the analyze
surface where the older ladder fell to its interpret default. No model is
called to route. ``understand.decide`` is a thin wrapper over ``surface``
for the callers that only have the words (the dashboard's pre-decision
call, the nightly replay); the ask service hands in the ask's own
Understanding, with the Reader's reading absorbed.

Every surface reason is a row in ``prometheus.routing_table.ROWS`` so the
widget and the ask log keep one vocabulary; the Understanding's own line
rides the ask log beside the route. The analyze route keeps the older
router's reasons where the row is the same.

The ``PM_LEGACY_ROUTING`` switch and the legacy branches that stood behind
it for one release were removed on 2026-11-23 after the production
environment was read (never set) and the ask log since the step 3 deploy
showed no ask on the older readers.

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
# the armed intake steps a client can be in the middle of
_INTAKE_STEPS = ('bpiq_wizard', 'bpiq_inputs', 'bpiq_confirm', 'jiq_inputs', 'jiq_confirm', 'fw_inputs', 'fw_confirm',
                 'aiq_inputs', 'aiq_confirm', 'twin_inputs', 'twin_confirm')


def _row(reason):
    """The routing-table row id for a reason. Every reason ``surface`` emits
    is a row (2026-10-02 RCA): a reason with no row is a contract break that
    raises under REGRESSION_TEST_MODE and is logged otherwise."""
    try:
        from . import routing_table as _rt
        r = _rt.row_for(reason)
    except Exception:
        return None
    if r is not None:
        return r['id']
    msg = 'routing.surface emitted a reason with no routing table row: %r' % (reason,)
    if os.environ.get('REGRESSION_TEST_MODE'):
        raise RuntimeError(msg)
    print('[routing] ' + msg)
    return None


def _u(u, text='', has_ctx=False, extra=None):
    """The Understanding as an object; from the words alone when none rides
    the ask (a caller that only has the text, or a fault upstream)."""
    try:
        from . import understanding as _und
        if isinstance(u, _und.Understanding):
            return u
        got = _und.Understanding.from_dict(u) if u else None
        if got is not None:
            return got
        return _und.features(str(text or ''), {'has_ctx': True} if has_ctx else None, [],
                             {'extra': extra} if isinstance(extra, dict) else {})
    except Exception:
        return None


def _reader_reading(u):
    """(intent, confidence) of the Reader's reading the Understanding absorbed or
    reconciled against, from its evidence; ('', 0.0) when there was none."""
    try:
        for e in reversed(list(getattr(u, 'evidence', None) or [])):
            e = str(e)
            if e.startswith('reader:') and '@' in e:
                name, conf = e[7:].rsplit('@', 1)
                return name, float(conf)
    except Exception:
        pass
    return '', 0.0


# the Reader's readings that keep an ask on the analyze surface over the order gate's own product read: "give me the
# age profile for each series" on an open journey view is a page question, not a Profile IQ order, and the Reader
# says so at 0.85 (the steer on the reading used to carry this before the lanes; 2026-11-23)
_READER_ANALYZE = frozenset(('analyze_page', 'analyze', 'compare', 'definition', 'pricing'))


def reader_says_answer(u):
    """True when the Reader read the words, with confidence, as a question,
    a read, a compare, a definition or a price (the readings that keep an
    ask on the analyze surface over the word rules)."""
    r_int, r_conf = _reader_reading(u)
    return r_int in _READER_ANALYZE and r_conf >= 0.7


def _confident_answer(u):
    """The Reader's reading, taken into the Understanding, of something that
    is not a build and not an intake: the words are a question in substance
    even when they are not shaped like one ("wjat does Avid tier mean"). A
    features-only reading never carries this: the word rules decide those."""
    if u is None:
        return False
    try:
        if str(u.action or '') in ('build', '') or str(u.intent or '') in INTAKE_INTENTS or str(u.intent or '') in BUILD_INTENTS:
            return False
        return str(u.source or '') == 'reader'
    except Exception:
        return False


# ------------------------------------------------------------- the surface
def surface(u, text, *, has_ctx=False, mode=None, extra=None, open_tabs=0, deck_in_flight=False, prior_outline=False):
    """The surface decision for one fresh ask: ``{'surface', 'mode', 'reason',
    'client_hint', 'row'}``. The rules that are not intents run first in
    their standing order; the Understanding decides between intents."""
    t = str(text or '').strip()
    extra = extra if isinstance(extra, dict) else {}
    u = _u(u, t, has_ctx, extra)
    d = {'surface': 'interpret', 'mode': None, 'reason': 'default', 'client_hint': None, 'row': None}

    def done(surface_, reason, mode_=None, hint=None):
        d.update(surface=surface_, mode=mode_, reason=reason, client_hint=hint, row=_row(reason))
        return d

    if not t:
        return done('interpret', 'empty')
    intent = str(getattr(u, 'intent', '') or '')
    action = str(getattr(u, 'action', '') or '')
    scope = getattr(u, 'scope', None)
    if scope is not None and getattr(scope, 'no_build', False) and action == 'build':
        action = 'answer'
    named = any(getattr(s, 'as_written', '') or getattr(s, 'catalog', '') for s in (getattr(u, 'subjects', None) or []))
    # the Reader's confident reading of a build on a named subject is the build flow, and its confident reading of a
    # question, a read, a compare, a definition or a price keeps the ask on the analyze surface, over the word rules
    # below (a mode the widget matched in the words, the order gate's product read, the deck rule, the ladder's
    # interpret default); the steer on the reading did this before the lanes until 2026-11-23
    r_int, r_conf = _reader_reading(u)
    reader_analyze = r_int in _READER_ANALYZE and r_conf >= 0.7
    reader_build = r_int in ('build_profile', 'cut', 'tracker') and r_conf >= 0.7 and named and action != 'answer'
    # 0. the client's own step is a decision already (a confirm payload; a mode chip, unless the Reader read an order
    # for a profile in the words: "generate a profile iq for X" with a mode matched in the same words is the order)
    if any(extra.get(k) for k in _understand._ANALYZE_EXTRA_KEYS):
        return done('analyze', 'explicit_step')
    if mode and str(mode).strip() and not reader_build:
        return done('analyze', 'explicit_mode', str(mode).strip().lower())
    # 1. the product intakes live on the analyze surface (the wizard lane), by the host's own intent checks or the reading
    try:
        ti = _understand.tool_intent(t)
    except Exception:
        ti = ''
    armed = str(getattr(scope, 'armed_step', '') or '') if scope is not None else ''
    if ti or intent in INTAKE_INTENTS or (intent == 'armed' and armed in _INTAKE_STEPS):
        return done('analyze', 'tool_intake:' + (ti or (intent if intent in INTAKE_INTENTS else 'armed')))
    # 2. a deck: the reading, or the deck rule (a deck in flight, a yes on a deck outline)
    try:
        deck_rule = _understand.looks_like_deck_ask(t, deck_in_flight, prior_deck_outline=bool(prior_outline))
    except Exception:
        deck_rule = False
    if intent == 'deck' or action == 'deck' or (deck_rule and not reader_analyze):
        return done('deck', 'deck_ask')
    # 2a. a Profile IQ build: an order for a profile (the order gate's own product read), a dataset on an audience, or a
    # build reading that names its subject; "give me the age profile for each series" or "please run one read" are
    # orders for something else and stay on the analyze surface
    try:
        from . import orders as _ord
        if _ord.is_profile_order(t) and not reader_analyze:
            return done('interpret', 'build_order')
    except Exception:
        pass
    try:
        from . import dataset_ask as _dsa
        if _dsa.is_build_ask(t) and not reader_analyze:
            return done('interpret', 'dataset_build')
    except Exception:
        pass
    if (action == 'build' and intent in ('build_profile', 'cut', 'tracker') and named) or reader_build:
        return done('interpret', 'build_order')
    # an order for a read ("please run one read for a single window ...") is work for the analysis pass, never a
    # Profile IQ brief
    try:
        if _ord.is_order(t) and _ord.product_of(t) == 'read':
            return done('analyze', 'task_not_build')
    except Exception:
        pass
    # 2b. a delivered figure split by a dimension the panel observes is a derived read with or without a page
    # (2026-10-12, Jenna: never page dependent by default; Jordan's device split)
    try:
        import prometheus_analysis as _pma
        if _pma.is_dimension_split_ask(t):
            return done('analyze', 'derived_read')
    except Exception:
        pass
    # 2c. a cross of behaviors inside one audience is a read with or without a page (prometheus.crosstab, Jenna
    # 2026-11-11); a bare "how many shop at Target and use Hulu" needs the open profile to point at
    try:
        from . import crosstab as _xt
        _q = _xt.parse(t)
        if _q and (_q.get('audience') or has_ctx):
            return done('analyze', 'crosstab')
    except Exception:
        pass
    # 2d. a bare acknowledgement, pick or refusal is not an ask; the surface is whatever the data state says and the
    # ask service answers it without a reasoning pass
    kind = _understand.guards.bare_reply_kind(t)
    if kind:
        return done('analyze' if has_ctx else 'interpret', 'short_reply:' + kind)
    # 2e. a capability question with a deterministic answer is answered, never drafted; a product fact with one fixed
    # answer (how big the sample is) the same; "do you see the X Subscriber IQ" is a library lookup
    try:
        if _understand.guards.capability_answer(t):
            return done('analyze', 'capability_question')
    except Exception:
        pass
    try:
        if _understand.guards.is_sample_size_ask(t):
            return done('analyze', 'product_fact')
    except Exception:
        pass
    try:
        if _understand.guards.subiq_lookup_title(t):
            return done('analyze', 'subiq_lookup')
    except Exception:
        pass
    # 3. a Subscriber IQ pull is the build flow's, even with a profile open; a question about the open Subscriber IQ
    # page or about what the product can do is a question; a churn / retention / signup question with no page open is
    # a question; a churn task without a build order is analysis, not a pull
    g = _understand.guards
    subiq = bool(_understand.looks_subscriber_iq(t) or _understand.looks_ambiguous_churn(t))
    if subiq and g.subiq_question_not_build(t, has_ctx=has_ctx):
        subiq = False
        if has_ctx:
            return done('analyze', 'question_about_subiq')
        if g.is_capability_question(t):
            return done('analyze', 'capability_question')
    if subiq and not has_ctx and not g.subiq_is_explicit_pull(t) and g.is_question_shaped(t):
        return done('analyze', 'question')
    try:
        if subiq and not g.subiq_is_explicit_pull(t) and g.reads_as_task(t):
            return done('analyze', 'task_not_build')
    except Exception:
        pass
    # 4. the dashboard pickers (menus the dashboard can open); the surface is still analyze for a client without that UI
    picker = _understand.dashboard_picker(t, open_tabs)
    if picker:
        return done('analyze', picker[0], None, picker[1])
    # 4b. where a named title is watched: the title in the words is the subject, the page is context at most
    try:
        from . import where_watched as _ww
        if not subiq and _ww.parse(t) is not None:
            return done('analyze', 'where_watched')
    except Exception:
        pass
    # 5. search-journey demand carries its own subject
    try:
        if not subiq and _understand.looks_search_demand(t):
            return done('analyze', 'search_demand', 'search_demand')
    except Exception:
        pass
    # 6. a named analysis mode with data open
    try:
        m = None if subiq else _understand.mode_for_text(t)
    except Exception:
        m = None
    if m and has_ctx:
        return done('analyze', 'mode:' + m, m)
    # 7. question-shaped or data-open asks analyze; an imperative task that does not order a build is work for the
    # analysis pass; the Reader's confident reading of something that is not a build answers (the Understanding,
    # where the older ladder fell to its interpret default: "wjat does Avid tier mean" at 0.93 is a question)
    if not subiq and _understand.should_analyze(t, has_ctx):
        return done('analyze', 'question' if not has_ctx else 'data_open')
    try:
        if not subiq and g.reads_as_task(t):
            return done('analyze', 'task_not_build')
    except Exception:
        pass
    if _confident_answer(u) or reader_analyze:
        return done('analyze', 'data_open' if has_ctx else 'question')
    # a Subscriber IQ pull, or a short bare subject, is the build flow's
    return done('interpret', 'subiq' if subiq else 'build_or_other')


# ------------------------------------------------------- the analyze route
def analyze_route(u, text, *, surface='analyze', has_ctx=False, mode='', has_base=False, memory_referent=False,
                  other_subject=False):
    """The analyze core's route for one ask, the dict ``prometheus_router.route_ask``
    returns (``route``, ``why``, ``subiq_family``, ``fork_subject``, ``nq_gate``).
    The deterministic prefilters stay; the Understanding's intent decides the
    rest (no model call)."""
    import prometheus_router as _pmr
    t = str(text or '')
    u = _u(u, t, has_ctx)
    # the rules that are not intents, in the router's own order
    d = _pmr.route_ask(t, surface=surface, has_ctx=has_ctx, mode=mode, has_base=False,
                       memory_referent=False, other_subject=False)
    pre, why = str(d.get('route') or ''), str(d.get('why') or '')
    if pre in ('subiq', 'csv_download', 'not_quantifiable', 'search_demand') or (pre == 'clarify' and why == 'subiq_fork'):
        return d   # a rule that is not an intent decided (the Subscriber IQ family, the churn fork, a CSV export, the gate, search demand)
    intent = str(getattr(u, 'intent', '') or '')
    action = str(getattr(u, 'action', '') or '')
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
            if _analysis_ask(t):
                d.update(route='generate', why='ctx_analysis_ask')
                return d
            if other:
                d.update(route='generate', why='named_other_subject')
                return d
            d.update(route='analysis', why='open_data_default')
            return d
        # nothing open: a build goes to the build flow; a data ask, a confident read intent or a question on a subject
        # we hold is the measured read; a remembered referent is the grounded confirm; the rest is the clarify
        named = any(getattr(s, 'as_written', '') or getattr(s, 'catalog', '') for s in (getattr(u, 'subjects', None) or []))
        try:
            from . import orders as _ord
            profile_order = bool(_ord.is_profile_order(t))
        except Exception:
            profile_order = False
        if profile_order or (action == 'build' and intent in ('build_profile', 'cut', 'tracker') and named):
            d.update(route='build_interpret', why='understanding:build')
            return d
        if _analysis_ask(t) or _generate_ask(t):
            d.update(route='generate', why='no_ctx_data_ask')
            return d
        confident_read = intent in READ_INTENTS and (str(getattr(u, 'source', '') or '') == 'reader' or intent != 'analyze')
        if confident_read:
            d.update(route='generate', why='understanding:read')
            return d
        if _pmr._resolve(has_base):
            if intent in READ_INTENTS or _pmr._question_shaped(t):
                d.update(route='generate', why='understanding:named_base')
                return d
        if _pmr._resolve(memory_referent):
            d.update(route='memory_confirm', why='memory_referent')
            return d
        d.update(route='clarify', why='no_context')
        return d
    # the interpret surface: an analysis-shaped ask on a subject with a base on file is the measured read, as the
    # router had it (the order gate's coarse build reading never keeps a consumption report in the build flow); a read
    # reading on such a subject the same; an explicit profile order is never deflected (prometheus.build_lane holds
    # it too); the rest is the build flow
    try:
        from . import orders as _ord
        profile_order = bool(_ord.is_profile_order(t))
    except Exception:
        profile_order = False
    try:
        from . import dataset_ask as _dsa
        order_like = profile_order or bool(_dsa.is_build_ask(t)) or bool(_ord.is_order(t))
    except Exception:
        order_like = profile_order
    if not profile_order and _pmr._resolve(has_base):
        if _analysis_ask(t):
            d.update(route='generate', why='analysis_ask')
            return d
        if intent in READ_INTENTS and _confident_answer(u):   # the Reader's read; never the features' default reading
            d.update(route='generate', why='understanding:analysis_ask')
            return d
        try:
            import prometheus_analysis as _pma
            build_shaped = bool(_pma.is_build_request(t))
        except Exception:
            build_shaped = False
        if _pmr._question_shaped(t) and not build_shaped and not order_like:
            # a question on a subject we hold reads on it (the classifier's old ground); "can you help me query a
            # dataset on X" is an order asked politely, never a read
            d.update(route='generate', why='named_base')
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


__all__ = ['surface', 'analyze_route', 'reader_says_answer', 'READ_INTENTS', 'INTAKE_INTENTS', 'BUILD_INTENTS']
