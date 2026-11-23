"""The model chain behind the chat: which model answers an analysis or a classification call, the resolved-model caches, and the JSON call every surface makes.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import os
import threading
from prometheus import faults as _faults
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared

# <<moved>>
# <<block: 1>>
_PM_ANALYZE_MODEL_ENV = (os.environ.get('PROMETHEUS_ANALYZE_MODEL')
                         or '').strip()


# <<block: 2>>
# Candidate IDs verified against the live Anthropic models list
# 2026-08-21 (client.models.list): opus-5 / opus-4-8 / opus-4-7 all
# exist. The chain tail is always the interpret sonnet, so analysis
# degrades gracefully when the deploy key has no Opus access.
_PM_ANALYZE_CANDIDATES = (
    ([_PM_ANALYZE_MODEL_ENV] if _PM_ANALYZE_MODEL_ENV else [])
    + ['claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7',
       'claude-opus-4-6'])


# <<block: 3>>
_pm_model_lock = threading.Lock()


# <<block: 4>>
_pm_resolved_model = {'name': None}


# <<block: 5>>
# Pure-classification surfaces ride a fast Haiku-class model
# (2026-08-28, the routing wave): corpus selection and the semantic
# ask classifier are label decisions, not reasoning, and the fast
# model answers them ~2x sooner (live probe on this key: opus-5
# ~1.4s vs haiku-4-5 ~0.6s on the identical classify prompt). The
# alias + the dated ID both verified against client.models.list on
# 2026-08-28. The full analyze chain is always the tail, so
# classification can never break on model naming.
_PM_CLASSIFY_MODEL_ENV = (os.environ.get('PROMETHEUS_CLASSIFY_MODEL')
                          or '').strip()


# <<block: 6>>
# 2026-09-04: dated ID first per Anthropic's Feb-2026 retirement of
# claude-3-5-haiku-20241022. Migration notice explicitly recommends
# the dated haiku-4-5-20251001 form.
# 2026-10-30 (Sonnet 4.5 retirement wave): the ask classifier keys on
# a literal label, so it was compared live on the five most frequent
# real asks before moving. Haiku 5.5 agreed on four; the 'Value a brand
# partnership' chip read `other` on 4.5 and `analysis` on 5.5. Per the
# rule (move only what passed) this chain stays on Haiku 4.5 first,
# with Haiku 5.5 as the live fallback in place of the retiring alias
# (Haiku 4.5's earliest retirement date, 2026-10-15, has passed).
_PM_CLASSIFY_CANDIDATES = (
    ([_PM_CLASSIFY_MODEL_ENV] if _PM_CLASSIFY_MODEL_ENV else [])
    + ['claude-haiku-4-5-20251001', 'claude-haiku-5-5'])


# <<block: 7>>
_PM_CLASSIFY_SURFACES = frozenset(('corpus_select', 'ask_classify', 'reader_notes', 'crosstab_ties'))


# <<block: 8>>
_PM_FAST_SURFACES = frozenset(('pm_knowledge_research', 'pm_valuation_events'))   # the interpret model first, the analysis chain behind it; the winner never cached as the analysis model (prometheus.read_speed, 2026-11-23)


# <<block: 9>>
_pm_resolved_classify = {'name': None}


# <<block: 10>>
def _pm_model_chain():
    with _C._pm_model_lock:
        if _C._pm_resolved_model['name']:
            return [_C._pm_resolved_model['name'], _C._SYNTH_CHAT_INTERPRET_MODEL]
    return _C._PM_ANALYZE_CANDIDATES + [_C._SYNTH_CHAT_INTERPRET_MODEL]


# <<block: 11>>
def _pm_classify_chain():
    """Fast-classifier candidates, then the full analyze chain as the
    fallback so a classification call can never hard-fail on model
    naming. The winning fast model is cached for the process lifetime
    in its own slot - it must never leak into the analysis chain (a
    label model cannot own the reasoning calls)."""
    # Read the resolved slot under the lock, then build the chain
    # OUTSIDE it: _pm_model_chain() takes the same non-reentrant lock,
    # so calling it while held self-deadlocks the second classify call
    # of the process and every model call queues behind it (found
    # 2026-08-28 via a hung read-job smoke).
    with _C._pm_model_lock:
        resolved = _C._pm_resolved_classify['name']
    if resolved:
        return [resolved] + _C._pm_model_chain()
    return _C._PM_CLASSIFY_CANDIDATES + _C._pm_model_chain()


# <<block: 12>>
def _pm_claude_json(system_prompt, user_prompt, max_tokens=6000,
                    temperature=0.5, surface='analysis',
                    usage_extras=None, tools=None):
    """JSON reasoning pass on the strongest model that answers. Walks
    the Opus candidate chain once, caches the winner for the process
    lifetime, and always keeps the interpret sonnet as the final
    fallback so analysis never hard-fails on model naming.

    2026-08-21: advance on ANY failure, not just 404s. The original
    404-only advance never fired because claude_reason_json swallowed
    permanent API errors into "" (surfaced as 'non-JSON output', which
    matched neither 'not_found' nor '404'), so the first unusable Opus
    candidate hard-failed every Prometheus analyze call. The tail
    sonnet is proven on this deploy's key, so walking the whole chain
    is always safe and at worst costs a few failed cheap requests.

    2026-08-26: `usage_extras` (from _pm_usage_extras) rides the usage
    tag so a pay-as-you-go user's calls land with user + session
    attribution and the billed amount.

    2026-08-27 (Jenna): the compiled house canon (voice, vocabulary,
    confidence calibration, number rules, boundaries, window default)
    appends to every Prometheus system prompt here, at the single choke
    point all four surfaces share. The block is byte-stable per pack
    content hash (prometheus_knowledge caches it), so the assembled
    system prompt stays byte-identical across calls and the provider
    prompt cache keeps holding. On any failure the base prompt ships
    unchanged."""
    try:
        import prometheus_knowledge as _pmk
        system_prompt = _pmk.with_canon(system_prompt,
                                        s3_client=_H.s3_client,
                                        bucket=_H.S3_BUCKET)
    except Exception as _fx:
        _faults.note('chat', _fx)
    # Per-user attribution (2026-09-02): tag every dashboard chat call
    # with the logged-in user so the daily spend email can break
    # Prometheus cost out per user. _pm_attrib_extras() never sets
    # pay_per_use, so billing and the credit gate (which key off
    # _pm_usage_extras) are untouched; any real pay-as-you-go fields
    # ride in via the caller's usage_extras and win the merge. The deck
    # job runs on a background thread with no request context, so
    # _pm_attrib_extras() is a no-op there and the caller's explicit
    # usage_extras carries the enqueue-time attribution instead.
    try:
        usage_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), usage_extras)
    except Exception as _fx:
        _faults.note('chat', _fx)
    last = None
    # Tool plans (2026-08-27, the generation loop): when the caller
    # asks for web research, try the current web_search tool type,
    # then the legacy type, then no tools at all - research is
    # additive and must never make a read fail.
    tool_plans = [tools] if tools else [None]
    if tools:
        try:
            import prometheus_analysis as _pma_tools
            if tools == [_pma_tools.WEB_SEARCH_TOOL]:
                tool_plans = [[_pma_tools.WEB_SEARCH_TOOL],
                              [_pma_tools.WEB_SEARCH_TOOL_LEGACY], None]
            else:
                tool_plans = [tools, None]
        except Exception:
            tool_plans = [tools, None]
    # Pure classification surfaces walk the fast chain first
    # (2026-08-28): label decisions do not need the Opus chain, and
    # the fast model answers them in under a second. Any failure
    # falls through to the full analyze chain, so classification can
    # never break.
    _is_classify = surface in _C._PM_CLASSIFY_SURFACES
    _is_fast = (not _is_classify) and surface in _C._PM_FAST_SURFACES and os.environ.get('PM_RESEARCH_TIER', 'fast').strip().lower() != 'strong'
    for _tp in tool_plans:
        for m in (_C._pm_classify_chain() if _is_classify
                  else ([_C._SYNTH_CHAT_INTERPRET_MODEL] + _C._pm_model_chain()) if _is_fast
                  else _C._pm_model_chain()):
            result = _H._run_nflx_claude_agent(
                system_prompt=system_prompt, user_prompt=user_prompt,
                max_tokens=max_tokens, temperature=temperature, model=m,
                usage_tag=((surface, 'chatbot', usage_extras)
                           if usage_extras else (surface, 'chatbot')),
                tools=_tp)
            if result.get('success'):
                with _C._pm_model_lock:
                    if _is_classify and m in _C._PM_CLASSIFY_CANDIDATES:
                        # The fast winner caches in its own slot only.
                        # It must never become the analysis model.
                        if _C._pm_resolved_classify['name'] != m:
                            print(f"[prometheus] classify model "
                                  f"resolved: {m}")
                            _C._pm_resolved_classify['name'] = m
                    elif not _is_classify and not _is_fast \
                            and _C._pm_resolved_model['name'] != m:
                        print(f"[prometheus] analysis model resolved: {m}")
                        _C._pm_resolved_model['name'] = m
                return _seams.tag_model(result)
            print(f"[prometheus] model {m} failed "
                  f"({str(result.get('error') or '')[:160]}); trying next")
            last = result
    return _seams.tag_model(
        last or {'success': False, 'status': 503,
                 'error': 'no reasoning model available'})


# <<block: 13>>
def _pm_claude_data(system_prompt, user_prompt, **kw):
    """The parsed JSON object from _pm_claude_json, or {} on any
    failure. _pm_claude_json returns the transport envelope
    ({'success', 'data', 'model'}); the chip intake parsers and the
    synthesis modules (journey / flywheel / brand partnership /
    attribution) expect the object itself. Until 2026-10-02 they read
    the envelope, so every guided pull answered 'Almost there - I
    still need ...' no matter what the user typed (Bria, Babylon 5 on
    Prime Video) and the research step raised 'returned no
    primitives'."""
    result = _C._pm_claude_json(system_prompt, user_prompt, **kw)
    if not isinstance(result, dict) or not result.get('success'):
        return {}
    data = _seams.unwrap(result)
    return data if isinstance(data, dict) else {}


__all__ = ['_PM_ANALYZE_MODEL_ENV', '_PM_ANALYZE_CANDIDATES', '_pm_model_lock', '_pm_resolved_model', '_PM_CLASSIFY_MODEL_ENV', '_PM_CLASSIFY_CANDIDATES', '_PM_CLASSIFY_SURFACES', '_PM_FAST_SURFACES', '_pm_resolved_classify', '_pm_model_chain', '_pm_classify_chain', '_pm_claude_json', '_pm_claude_data']
