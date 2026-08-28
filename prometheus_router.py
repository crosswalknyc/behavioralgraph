#!/usr/bin/env python3
"""Single server-side router for the dashboard chat assistant
(2026-08-28, the routing wave of the Prometheus plan).

One function, `route_ask`, decides the route for every ask on both
chat surfaces (the analyze endpoint and the interpret endpoint), so
the two no longer carry divergent hard-coded branch orders. The
composition is cheap deterministic prefilters first (the regex
detectors that already exist in prometheus_analysis / subiq_intent,
consolidated in one precedence), then the fast classify model as the
backstop for genuinely ambiguous asks. Open-profile context is
DECISIVE: a data question with a profile open can never route to the
build interpreter or any unmappable fallback.

Route vocabulary (mirrors scripts/prometheus_eval_run.py):

    analysis          page-analysis pass over the open data
    generate          measured-read pass (_pm_generate_metrics_response)
    search_demand     search-journey demand study
    csv_download      export of the last delivered read
    not_quantifiable  graceful decline (no digital trace)
    memory_confirm    grounded "do you mean for X?" confirm
    build_interpret   normal build-interpret flow
    subiq             Subscriber IQ promotion
    clarify           open-something nudge, or the churn fork
                      (why='subiq_fork') that asks impact-vs-audience

PRECEDENCE (top wins; the router unit tests pin this order):

    1. subiq            subiq_intent_family (product, acquisition,
                        first_watch, churn, impact)
    2. clarify (fork)   ambiguous_churn_subject -> why='subiq_fork'
    3. csv_download     detect_csv_download_intent
    4. not_quantifiable classify_quantifiability (never fires on
                        build phrasing on the interpret surface)
    5. search_demand    mode == 'search_demand' (analyze) or
                        detect_search_demand_intent (never fires on
                        build phrasing on the interpret surface)
    6. generate         KPI vocabulary, strategy / white-space asks,
                        and analysis-shaped data asks. With a profile
                        open, detect_analysis_ask routes STRAIGHT to
                        the measured read (this is what removes the
                        double reasoning call: previously only exact
                        KPI hits skipped the page-analysis pass).
                        On the interpret surface, generate requires an
                        existing base (has_base); a subject with no
                        base keeps flowing to the build interpreter.
    7. analysis         analyze surface with open data (the default:
                        open-ended "what stands out" asks stay here)
    8. memory_confirm   analyze surface, nothing open, stored referent
    9. classifier       fast-model backstop for question-shaped asks
                        the regexes missed, gated on a resolvable base
                        so a data ask with a base never dead-ends
   10. clarify          analyze surface, nothing open, nothing stored
       build_interpret  interpret surface default

`has_base` and `memory_referent` accept a bool OR a zero-argument
callable (resolved lazily, at most once, only when the decision needs
them - they can cost an S3 read or a catalog scan). `classify_fn`
takes the ask text and returns 'analysis' | 'build' | 'cut' | 'other'
(the classify_ask_semantic contract); pass None to keep the router
fully deterministic (the hermetic eval does).

The decision dict:

    {'route': <route>, 'why': <short reason tag>,
     'subiq_family': str|None, 'fork_subject': str|None,
     'nq_gate': dict|None,          # classify_quantifiability result
     'used_classifier': bool, 'classify_kind': str|None,
     'classify_ms': int}

This module imports only prometheus_analysis and subiq_intent (both
pure text modules): no Flask, no S3, no model client, so it stays
unit-testable and safe to import anywhere.
"""
import time

__all__ = ['route_ask', 'ROUTES']

ROUTES = ('analysis', 'generate', 'search_demand', 'csv_download',
          'not_quantifiable', 'memory_confirm', 'build_interpret',
          'subiq', 'clarify')


def _resolve(flag):
    """Bool-or-callable resolver. Lazy flags never break routing."""
    if callable(flag):
        try:
            return bool(flag())
        except Exception:
            return False
    return bool(flag)


def route_ask(text, *, surface, has_ctx=False, mode='', has_base=False,
              memory_referent=False, classify_fn=None):
    """Decide the route for one ask. See the module docstring for the
    precedence and the decision-dict shape. Never raises."""
    import prometheus_analysis as pma
    import subiq_intent as si

    d = {'route': '', 'why': '', 'subiq_family': None,
         'fork_subject': None, 'nq_gate': None,
         'used_classifier': False, 'classify_kind': None,
         'classify_ms': 0}
    surface = ('interpret'
               if str(surface or '').strip().lower().startswith('i')
               else 'analyze')
    t = str(text or '').strip()
    if not t:
        d.update(route=('build_interpret' if surface == 'interpret'
                        else 'clarify'), why='empty')
        return d

    try:
        build_shaped = pma.is_build_request(t)
    except Exception:
        build_shaped = False

    # 1. Subscriber IQ family. Ranks ahead of everything so signup /
    # acquisition / churn-impact asks are never hijacked by the
    # analysis deflection (the subiq-004 defect) or the KPI detector.
    try:
        fam = si.subiq_intent_family(t)
    except Exception:
        fam = None
    if fam:
        d.update(route='subiq', why=f'subiq_{fam}', subiq_family=fam)
        return d

    # 2. Ambiguous churn fork: churn vocabulary with no title event
    # and no cohort framing forks to a user prompt.
    try:
        fork, fork_subject = si.ambiguous_churn_subject(t)
    except Exception:
        fork, fork_subject = False, ''
    if fork:
        d.update(route='clarify', why='subiq_fork',
                 fork_subject=fork_subject or '')
        return d

    # 3. CSV export of an already-delivered read.
    try:
        if pma.detect_csv_download_intent(t):
            d.update(route='csv_download', why='csv_export')
            return d
    except Exception:
        pass

    # 4. Quantifiability gate. On the interpret surface a build ask
    # that happens to mention in-store behavior is still a build.
    if surface == 'analyze' or not build_shaped:
        try:
            gate = pma.classify_quantifiability(t)
        except Exception:
            gate = None
        if gate:
            d.update(route='not_quantifiable', why='quantifiability_gate',
                     nq_gate=gate)
            return d

    # 5. Search-journey demand. The widget's explicit mode wins on the
    # analyze surface; the intent detector covers typed asks. Build
    # phrasing on the interpret surface stays a build.
    try:
        sd = ((surface == 'analyze'
               and str(mode or '').strip().lower() == 'search_demand')
              or ((surface == 'analyze' or not build_shaped)
                  and pma.detect_search_demand_intent(t)))
    except Exception:
        sd = False
    if sd:
        d.update(route='search_demand', why='search_demand')
        return d

    # 6. Deterministic generate (the measured-read pass).
    if surface == 'analyze':
        try:
            if pma.detect_metric_kpi_intent(t):
                d.update(route='generate', why='kpi')
                return d
            if pma.detect_strategy_intent(t):
                d.update(route='generate', why='strategy')
                return d
        except Exception:
            pass
        if has_ctx:
            # OPEN DATA IS DECISIVE. An analysis-shaped data ask goes
            # straight to the measured read (no page-analysis pass
            # first); everything else is a page analysis. Never a
            # build route, never an unmappable fallback.
            try:
                if pma.detect_analysis_ask(t):
                    d.update(route='generate', why='ctx_analysis_ask')
                    return d
            except Exception:
                pass
            d.update(route='analysis', why='open_data_default')
            return d
        # Nothing open: direct data asks generate; the base gate
        # inside the measured-read pass resolves or steers to build.
        try:
            if pma.detect_generate_intent(t) \
                    or pma.detect_subcut_intent(t) \
                    or pma.detect_analysis_ask(t):
                d.update(route='generate', why='no_ctx_data_ask')
                return d
        except Exception:
            pass
        # 8. Grounded confirm from cross-session memory.
        if _resolve(memory_referent):
            d.update(route='memory_confirm', why='memory_referent')
            return d
        # 9. Classifier backstop: a question-shaped ask the regexes
        # missed, with a resolvable base, never dead-ends at the
        # nudge. 'build'/'cut' verdicts hand the ask to the build
        # flow via the re-route contract.
        kind = _classify(d, t, pma, has_base, classify_fn)
        if kind == 'analysis':
            d.update(route='generate', why='classifier')
            return d
        if kind in ('build', 'cut'):
            d.update(route='build_interpret', why='classifier')
            return d
        d.update(route='clarify', why='no_context')
        return d

    # ---- interpret surface ----
    # 6. Analysis deflection: an analysis-shaped or strategy ask that
    # reached the build surface routes to the measured read when the
    # named subject has a base on file (catalog or memory referent).
    try:
        det = (pma.detect_analysis_ask(t)
               or pma.detect_strategy_intent(t))
    except Exception:
        det = False
    if det and _resolve(has_base):
        d.update(route='generate', why='analysis_ask')
        return d
    # 9. Classifier backstop, same base gate as production's semantic
    # classifier: question-shaped, not an explicit build/cut/deck/
    # export, base exists.
    if not det:
        kind = _classify(d, t, pma, has_base, classify_fn)
        if kind == 'analysis':
            d.update(route='generate', why='classifier')
            return d
    # 10. Default: the normal build-interpret flow.
    d.update(route='build_interpret', why='build_default')
    return d


def _classify(d, t, pma, has_base, classify_fn):
    """Run the fast-model backstop when it is worth a call: the ask is
    a candidate (question-shaped, not an explicit build/cut/deck/
    export) and a base resolves. Records timing + verdict on the
    decision dict. Returns the kind ('other' when skipped/failed)."""
    if classify_fn is None:
        return 'other'
    try:
        if not pma.analysis_ask_candidate(t):
            return 'other'
    except Exception:
        return 'other'
    if not _resolve(has_base):
        return 'other'
    t0 = time.monotonic()
    try:
        kind = str(classify_fn(t) or 'other').strip().lower()
    except Exception:
        kind = 'other'
    d['classify_ms'] = int((time.monotonic() - t0) * 1000)
    d['used_classifier'] = True
    d['classify_kind'] = kind if kind in ('analysis', 'build', 'cut',
                                          'other') else 'other'
    return d['classify_kind']
