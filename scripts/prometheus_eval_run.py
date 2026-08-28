#!/usr/bin/env python3
"""Nightly route-level eval for the dashboard chat assistant (Phase 0,
2026-08-28).

Grades the DETERMINISTIC routing layer of the two chat surfaces against
a fixed set of real user questions (scripts/prometheus_eval_set.json,
sampled from the S3 ask log plus constructed representatives for
families the young log does not carry yet). No model calls, no S3, no
Flask app import: the fixture's page_context / has_base / memory
fields stand in for the data-dependent conditions, so the run is
hermetic and takes under a second on the box.

WHAT IS MIRRORED (and must stay in sync with app.py):

analyze surface (api_synth_chat_analyze, in production order):
  1. prometheus_analysis.detect_csv_download_intent  -> csv_download
  2. prometheus_analysis.classify_quantifiability    -> not_quantifiable
  3. mode == 'search_demand' or detect_search_demand_intent
                                                     -> search_demand
  4. detect_metric_kpi_intent                        -> generate
  5. detect_strategy_intent                          -> generate
  6. no page context:
       detect_generate_intent / detect_subcut_intent /
       detect_analysis_ask                           -> generate
       stored memory referent (fixture flag)         -> memory_confirm
       otherwise (open-something nudge)              -> clarify
  7. page context present                            -> analysis

interpret surface (api_synth_chat_interpret, deterministic pre-model
steps; the incidence pre-check and the model-backed semantic
classifier are out of hermetic scope by design):
  1. detect_csv_download_intent                      -> csv_download
  2. fast-path analysis deflection:
       (detect_analysis_ask or detect_strategy_intent)
       and a base exists (fixture has_base)          -> generate
  3. subiq_intent.subiq_intent_family truthy         -> subiq
  4. subiq_intent.ambiguous_churn_subject fork       -> clarify
  5. otherwise                                       -> build_interpret

Route vocabulary: analysis, generate, search_demand, csv_download,
not_quantifiable, memory_confirm, build_interpret, subiq, clarify.

Known-fails: a case with a "known_fail" reason string documents a real
routing defect owned by a separate fix. It reports as KNOWN-FAIL and
does not fail the run; if it starts passing it reports as XPASS (a
warning to promote it to a normal case), also without failing the run.

Exit codes: 0 = no regressions, 1 = at least one regression, 2 = the
harness itself could not run (fixture missing, import failure).

Usage:
    python3 scripts/prometheus_eval_run.py             # hermetic grading
    python3 scripts/prometheus_eval_run.py -v          # per-case detail
    python3 scripts/prometheus_eval_run.py --live 3    # + 3 live smokes

--live N additionally runs N end-to-end generation smokes (real model
calls through claude_client on generate / search_demand cases: build
the real prompt, call the model, run the real coherence + format
pass, require a non-empty reply). Skipped cleanly when no model
credentials are configured. A live failure counts as a regression.
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_WEBAPP = os.path.dirname(_HERE)
if _WEBAPP not in sys.path:
    sys.path.insert(0, _WEBAPP)

FIXTURE_PATH = os.path.join(_HERE, 'prometheus_eval_set.json')

ROUTES = ('analysis', 'generate', 'search_demand', 'csv_download',
          'not_quantifiable', 'memory_confirm', 'build_interpret',
          'subiq', 'clarify')


def classify_analyze(pma, text, has_ctx, mode=None, memory_referent=False):
    """Mirror of the deterministic branch order in
    api_synth_chat_analyze. Keep in sync with app.py."""
    if pma.detect_csv_download_intent(text):
        return 'csv_download'
    if pma.classify_quantifiability(text):
        return 'not_quantifiable'
    if str(mode or '').strip().lower() == 'search_demand' \
            or pma.detect_search_demand_intent(text):
        return 'search_demand'
    if pma.detect_metric_kpi_intent(text):
        return 'generate'
    if pma.detect_strategy_intent(text):
        return 'generate'
    if not has_ctx:
        if pma.detect_generate_intent(text) \
                or pma.detect_subcut_intent(text) \
                or pma.detect_analysis_ask(text):
            return 'generate'
        if memory_referent:
            return 'memory_confirm'
        return 'clarify'
    return 'analysis'


def classify_interpret(pma, si, text, has_base=True):
    """Mirror of the deterministic pre-model steps in
    api_synth_chat_interpret. Keep in sync with app.py. The incidence
    pre-check and the model-backed semantic classifier are not
    mirrored (out of hermetic scope)."""
    if pma.detect_csv_download_intent(text):
        return 'csv_download'
    if (pma.detect_analysis_ask(text)
            or pma.detect_strategy_intent(text)) and has_base:
        return 'generate'
    if si.subiq_intent_family(text):
        return 'subiq'
    ambig, _subj = si.ambiguous_churn_subject(text)
    if ambig:
        return 'clarify'
    return 'build_interpret'


def classify_case(pma, si, case):
    surface = str(case.get('surface') or 'analyze').strip().lower()
    text = case.get('input') or ''
    if surface == 'interpret':
        return classify_interpret(pma, si, text,
                                  has_base=bool(case.get('has_base', True)))
    return classify_analyze(
        pma, text,
        has_ctx=bool(case.get('page_context')),
        mode=case.get('mode'),
        memory_referent=bool(case.get('memory_referent', False)))


def run_live_smoke(pma, case, verbose=False):
    """One end-to-end generation smoke: real prompt build, real model
    call, real coherence + format pass. Returns (ok, detail)."""
    text = case.get('input') or ''
    route = case.get('expected_route')
    try:
        from claude_client import (claude_reason_json,
                                   is_claude_reasoning_enabled)
    except Exception as e:
        return None, f'model client unavailable ({e})'
    if not is_claude_reasoning_enabled():
        return None, 'model credentials not configured; skipped'
    try:
        if route == 'search_demand':
            system = pma.SEARCH_DEMAND_SYSTEM_PROMPT
            user_prompt = pma.build_search_demand_user_prompt(text, [])
        else:
            system = pma.REASONED_METRICS_SYSTEM_PROMPT
            user_prompt = pma.build_reasoned_metrics_user_prompt(text, [])
        t0 = time.monotonic()
        raw = claude_reason_json(system=system, user=user_prompt,
                                 max_tokens=6000, temperature=0.4,
                                 raise_on_error=True,
                                 usage_tag=('eval_smoke', 'chatbot'))
        ms = int((time.monotonic() - t0) * 1000)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        if not isinstance(data, dict):
            return False, f'model returned no JSON object ({ms}ms)'
        action = str(data.get('action') or '').strip().lower()
        if action == 'clarify':
            q = str(data.get('clarify_question') or '').strip()
            return (bool(q), f'clarify ({ms}ms): {q[:80]}')
        if route == 'search_demand':
            study = pma.enforce_demand_coherence(data)
            reply = pma.format_search_demand_reply(study)
        else:
            res = pma.enforce_metrics_coherence(data)
            reply = pma.format_generated_metrics_reply(res)
        reply = str(reply or '').strip()
        flags = case.get('flags') or {}
        if flags.get('must_not_fallback') and not reply:
            return False, f'empty reply after coherence pass ({ms}ms)'
        if verbose and reply:
            print(f'      reply head: {reply[:100]!r}')
        return bool(reply), f'{len(reply)} chars in {ms}ms'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--live', type=int, default=0, metavar='N',
                    help='also run N end-to-end generation smokes '
                         '(real model calls)')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='print every case, not just failures')
    ap.add_argument('--fixture', default=FIXTURE_PATH,
                    help='path to the eval set JSON')
    args = ap.parse_args(argv)

    try:
        with open(args.fixture, encoding='utf-8') as f:
            fixture = json.load(f)
        cases = fixture['cases']
    except Exception as e:
        print(f'[eval] cannot load fixture {args.fixture}: {e}')
        return 2
    try:
        import prometheus_analysis as pma
        import subiq_intent as si
    except Exception as e:
        print(f'[eval] cannot import routing modules: {e}')
        return 2

    t0 = time.monotonic()
    passed, failed, known, xpass = [], [], [], []
    by_family = {}
    for case in cases:
        cid = case.get('id') or '?'
        family = case.get('family') or 'other'
        expected = case.get('expected_route')
        if expected not in ROUTES:
            failed.append((case, f'bad expected_route {expected!r}'))
            continue
        try:
            got = classify_case(pma, si, case)
        except Exception as e:
            got = f'ERROR {type(e).__name__}: {e}'
        ok = (got == expected)
        fam = by_family.setdefault(family, [0, 0])
        kf = case.get('known_fail')
        if ok and not kf:
            passed.append(case)
            fam[0] += 1
            if args.verbose:
                print(f'  PASS       {cid:<22} -> {got}')
        elif ok and kf:
            xpass.append(case)
            fam[0] += 1
            print(f'  XPASS      {cid:<22} -> {got} '
                  f'(known-fail now passes; promote it: {kf})')
        elif kf:
            known.append(case)
            fam[0] += 1
            if args.verbose:
                print(f'  KNOWN-FAIL {cid:<22} expected {expected}, '
                      f'got {got} ({kf})')
        else:
            failed.append((case, got))
            fam[1] += 1
            print(f'  FAIL       {cid:<22} expected {expected}, got {got}')
            print(f'             input: {str(case.get("input"))[:90]!r}')

    live_failed = []
    live_ran = live_skipped = 0
    if args.live > 0:
        live_pool = [c for c in cases
                     if c.get('expected_route') in ('generate',
                                                    'search_demand')
                     and not c.get('known_fail')]
        print(f'\n[eval] live smokes: {min(args.live, len(live_pool))} '
              f'of {len(live_pool)} eligible cases')
        for case in live_pool[:args.live]:
            cid = case.get('id') or '?'
            ok, detail = run_live_smoke(pma, case, verbose=args.verbose)
            if ok is None:
                live_skipped += 1
                print(f'  LIVE SKIP  {cid:<22} {detail}')
            elif ok:
                live_ran += 1
                print(f'  LIVE PASS  {cid:<22} {detail}')
            else:
                live_ran += 1
                live_failed.append((case, detail))
                print(f'  LIVE FAIL  {cid:<22} {detail}')

    ms = int((time.monotonic() - t0) * 1000)
    total = len(cases)
    print(f'\n[eval] {total} cases in {ms}ms: '
          f'{len(passed)} pass, {len(failed)} FAIL, '
          f'{len(known)} known-fail, {len(xpass)} xpass')
    if args.live > 0:
        print(f'[eval] live: {live_ran} ran '
              f'({len(live_failed)} failed), {live_skipped} skipped')
    print('[eval] by family:')
    for family in sorted(by_family):
        p, f = by_family[family]
        print(f'    {family:<24} {p:>3} ok  {f:>3} fail')
    if failed or live_failed:
        print('[eval] REGRESSION: routing changed for the cases above.')
        return 1
    print('[eval] OK: no regressions.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
