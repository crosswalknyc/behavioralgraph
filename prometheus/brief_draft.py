"""Drafting a brief from a request: the run-time estimate, the per-subject draft and the batch fan-out over a list of subjects.

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
import traceback
from prometheus import faults as _faults
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import order_quote as _oq

# <<moved>>
# <<block: 1>>
# ---------------------------------------------------------------------------
# Batch-mode multi-subject detection (2026-08-18).
# Recognizes prompts like:
#   - "run individual profiles for VIZIO, Samsung, LG"
#   - "profiles for the following: Coke, Pepsi, Dr Pepper"
#   - "separate profiles for each of X, Y, and Z"
#   - "one profile each for A, B, C"
# ...and returns a list of subject strings so the interpret endpoint can
# fan out into N parallel Claude calls, one per subject.
#
# Anti-false-positive guardrails:
#   * Must contain an explicit multi-request keyword ("individual profiles",
#     "separate profiles", "profiles for the following", "profile each",
#     "each of").
#   * Must have >= 2 comma-separated or "and"-separated items after
#     stripping the trigger phrase.
#   * Rejects if the whole prompt is under ~10 chars (typo territory).
#   * Cap batch size at 100 (Jenna 2026-09-02, raised from 15 so a big
#     list of subjects can be queued in one message). The interpret
#     step fans out one Claude call per subject and the builds drain
#     through the worker pool (~10 at a time), so a large batch queues
#     in parallel and completes as workers free up rather than
#     swamping the interpret step or the pool.
# ---------------------------------------------------------------------------
SYNTH_CHAT_BATCH_MAX = 100


# <<block: 2>>
# Chat interpret reasoning model (2026-08-20 Jenna: 'does the agent
# need to be smarter?'). The chat brief-drafting calls run on the
# stronger Sonnet line than the deck agents' default; override via
# env without a deploy.
_SYNTH_CHAT_INTERPRET_MODEL = (
    os.environ.get('SYNTH_CHAT_INTERPRET_MODEL')
    or 'claude-sonnet-4-6')


# <<block: 3>>
# Deterministic wall-time estimator for the chatbot approval card.
# We override whatever `estimated_run_minutes` value Claude puts in the
# interpret JSON because the model has no grounding in the current
# pipeline speed - it tends to guess 30-60 min based on "typical AI
# synthesis workflow" priors. These numbers are anchored to actual
# observed wall times after the 2026-08-18 optimizations (Anthropic
# prompt caching + chunk_size=80 + max_workers=8): TU new_build
# smoke-tested at ~9-12 min, TU + Avid pair at ~18-22 min.
#
# If we later observe drift we can retune here without touching the
# prompt. Anti-drift note: this is a display estimate for the approval
# card, not a hard SLA - it excludes any queue wait time on Hetzner.
_RUN_MINUTES_TABLE = {
    'existing_match':        0,   # reuse - no pipeline work
    'derive_cut':            10,  # cut derived from parent, no fresh TU
    'new_build':             12,  # TU only
    'new_build_avid':        20,  # TU + Avid pair (largely parallel)
    'time_shifted_refresh':  12,  # same shape as new_build
    'time_shifted_refresh_avid': 20,
    'cut_needs_parent':      25,  # TU + Avid + cut derive
    'cut_needs_parent_avid': 25,
}


# <<block: 4>>
def _estimate_run_minutes(decision: str, run_avid: bool) -> int:
    """Deterministic wall-time estimate (minutes) for the chatbot
    approval card. Anchored to observed pipeline speed post the
    2026-08-18 optimizations. Overrides Claude's hallucinated value.
    """
    d = (decision or '').strip().lower() or 'new_build'
    key = d + ('_avid' if run_avid else '')
    if key in _C._RUN_MINUTES_TABLE:
        return _C._RUN_MINUTES_TABLE[key]
    if d in _C._RUN_MINUTES_TABLE:
        return _C._RUN_MINUTES_TABLE[d]
    return 15


# <<block: 5>>
def _jitter_draft_est_sample(spec_draft):
    """Normalize subject_raw_tu / subject_raw_avid IN the draft so the
    approval card can show the estimated sample size BEFORE approve
    (2026-08-19 Jenna directive: "have it say the estimated sample
    size on here so you can choose before you proceed").

    Uses the same idempotent messy-sample helper as _spec_from_draft
    and the build worker: a messy value passes through every later
    stage unchanged, so the number shown on the card IS the sample the
    build runs with. Seeds mirror _spec_from_draft exactly. The
    follower-ceiling cap (applied at approve + re-applied by the
    worker) can still lower a capped audience below this estimate,
    which is why the card labels it "Est."
    """
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
    except Exception:
        return
    try:
        subject = spec_draft.get('subject') or spec_draft.get('name') \
            or 'Unknown Subject'
        try:
            tu = int(round(float(spec_draft.get('subject_raw_tu'))))
        except (TypeError, ValueError):
            tu = 0
        if tu <= 0:
            return
        # Clamp just under the 9.5M hard ceiling so the +/-98 jitter
        # can't push a ceiling-pinned value over it.
        tu = max(800, min(tu, 9_499_900))
        tu = ensure_messy_sample_size(subject, tu)
        try:
            av = int(round(float(spec_draft.get('subject_raw_avid'))))
        except (TypeError, ValueError):
            av = 0
        if av <= 0 or av >= tu:
            av = max(801, int(tu * 0.22))
        av = ensure_messy_sample_size(f"{subject}|avid", av,
                                      default_if_missing=2937)
        if av >= tu:
            av = tu - 13
        spec_draft['subject_raw_tu'] = tu
        spec_draft['subject_raw_avid'] = av
    except Exception as e:
        print(f"[synth-chat] est-sample jitter skipped: {e}")


# <<block: 6>>
def _synth_chat_interpret_one_subject(subject: str, shared_context: str,
                                       history: list,
                                       attrib_extras: dict = None) -> dict:
    """Run one Claude interpret call for a single subject inside a batch.
    Returns a dict with `success` + either `spec_draft`+`candidates` or
    `error`. Never raises - errors bubble up as `success: false`.

    `attrib_extras` (2026-09-04 fix): the calling batch is dispatched
    via a ThreadPoolExecutor whose worker threads have no Flask
    request context, so _pm_attrib_extras() inside a worker returns
    {} and every interpret record ships unattributed. The batch
    captures attribution ONCE on the request thread and passes it
    here; if None, falls back to the (usually-empty) request-context
    lookup so behavior is unchanged when a caller invokes this
    directly without pre-capturing.
    """
    per_prompt = subject.strip()
    if shared_context:
        per_prompt = f"{per_prompt} {shared_context}".strip()
    _attrib = attrib_extras if attrib_extras is not None \
        else (_C._pm_attrib_extras() or None)
    try:
        try:
            from iq_rankers import MASTER_CATEGORIES
        except Exception:
            MASTER_CATEGORIES = {}
        catalog = _C._profile_catalog_for_chat()
        candidates = _C._shortlist_profile_matches(per_prompt, catalog)
        system_prompt, user_prompt = _C._synth_chat_interpret_prompts(
            per_prompt, chat_history=history,
            master_categories=MASTER_CATEGORIES,
            candidate_matches=candidates,
        )
        # 32000 matches the single-path interpret ceiling (2026-09-22,
        # Will & Grace year batch: a slice truncated at 16000 and the
        # five-year package shipped short).
        result = _H._run_nflx_claude_agent(
            system_prompt=system_prompt, user_prompt=user_prompt,
            max_tokens=32000, temperature=0.4,
            model=_C._SYNTH_CHAT_INTERPRET_MODEL,
            usage_tag=('interpret', 'chatbot', _attrib),
        )
        if not result.get('success'):
            return {
                'success': False, 'subject_input': subject,
                'error': result.get('error', 'interpret failed'),
            }
        spec_draft = result.get('data') or {}

        # 2026-08-20: chat history from earlier batch turns can prime
        # the model to return a JSON ARRAY of drafts even for this
        # single-slice ask. Take the first dict - the caller-subject
        # override below repairs subject/file_stem deterministically -
        # instead of crashing on list.get and failing the whole slice.
        if isinstance(spec_draft, list):
            spec_draft = next(
                (d for d in spec_draft if isinstance(d, dict)), {})
        if not isinstance(spec_draft, dict) or not spec_draft:
            return {
                'success': False, 'subject_input': subject,
                'error': 'interpret returned no usable draft',
            }

        # Force the caller-provided subject name onto the draft so the
        # approval card and file_stem match the specific slice we
        # asked Claude to interpret. When we thread the full original
        # request as shared_context (Cartesian batch case) Claude
        # tends to echo the umbrella name back in `subject` (e.g.
        # "EST Buyers vs. TVOD Renters by Retailer") instead of the
        # per-slice name we handed it (e.g. "Amazon EST buyers").
        # Overriding here is deterministic, no round-trip needed.
        import re as _re_local
        _canon_subject = _re_local.sub(r'\s+', ' ',
                                          (subject or '').strip())
        # Title-case any word that is entirely lowercase (so
        # "amazon est buyers" -> "Amazon EST Buyers"), preserve
        # acronyms and mixed-case tokens as-is (EST, TVOD, YouTube,
        # AT&T), and keep common stopwords lowercase unless they're
        # the first word (so "Fandango at Home" stays intact).
        _stopwords = {'at', 'of', 'the', 'and', 'or', 'in', 'on',
                      'to', 'from', 'for', 'by', 'vs', 'a', 'an'}
        _tokens = _canon_subject.split()
        _display_parts = []
        for i, w in enumerate(_tokens):
            if not w.islower():
                _display_parts.append(w)  # preserve acronyms/mixed-case
            elif i > 0 and w in _stopwords:
                _display_parts.append(w)  # keep stopword lowercase
            else:
                _display_parts.append(w.capitalize())
        _display_subject = ' '.join(_display_parts) if _display_parts else _canon_subject
        if _display_subject:
            spec_draft['subject'] = _display_subject
            # Rebuild file_stem to match. Keep only alnum + _/- and
            # collapse whitespace-to-underscore for a clean S3 key.
            _stem = _re_local.sub(r'\s+', '_', _display_subject)
            _stem = ''.join(c for c in _stem if c.isalnum() or c in '_-')
            spec_draft['file_stem'] = _stem or 'Profile'

        # Apply the same guardrails the single-subject path does.
        try:
            swapped, swap_note = _H._enforce_base_parent_pick(
                spec_draft, candidates, per_prompt)
            if swapped:
                spec_draft['_parent_guardrail_note'] = swap_note
        except Exception as _fx:
            _faults.note('chat', _fx)

        # Normalized existing-profile match (2026-08-24 SHARKNINJA
        # directive). Batch slices are one-shot (no clarify loop), so
        # force existing_match / time_shifted_refresh instead of
        # asking - a batch must never mint a near-duplicate filename
        # differing only in case or spacing.
        try:
            _nm_acted, _nm_note = _H._enforce_normalized_existing_match(
                spec_draft, candidates, per_prompt,
                catalog=catalog, allow_ask=False)
            if _nm_acted:
                print(f"[synth-chat batch] {_nm_note}")
        except Exception as _fx:
            _faults.note('chat', _fx)

        # Universe-qualifier gate (2026-08-25): batch slices are
        # one-shot, so a brand-matches-but-qualifier-differs pick
        # demotes to new_build with the related profile noted by name.
        try:
            _H._apply_universe_qualifier_gate(spec_draft, per_prompt,
                                           allow_ask=False)
        except Exception as _fx:
            _faults.note('chat', _fx)

        # Intersect-cut promoter: if the per-subject prompt looks like
        # "<parent> for <cohort>" / "<parent> -> <cohort>" and a strong
        # candidate matches the left operand, promote from new_build
        # to derive_cut so the cut runs cheap (~$2-4 vs. ~$40).
        try:
            _H._maybe_promote_intersect_to_derive_cut(
                spec_draft, per_prompt, candidates, catalog=catalog)
        except Exception as _fx:
            _faults.note('chat', _fx)
        __import__('prometheus.held_window', fromlist=['settle_on_host']).settle_on_host(spec_draft, per_prompt, _H, catalog=catalog, est_minutes=_C._estimate_run_minutes)   # a window the file already holds is a reuse, never a rebuild (Grace Yin 2026-11-16)

        decision_str = str(spec_draft.get('decision') or '').strip().lower()
        user_optout = _H._user_optout_of_avid(per_prompt, chat_history=history)
        claude_says_false = spec_draft.get('run_avid') is False
        if decision_str in _H._AVID_INAPPLICABLE_DECISIONS:
            spec_draft['run_avid'] = False if decision_str == 'derive_cut' else True
        elif claude_says_false and user_optout:
            spec_draft['run_avid'] = False
        else:
            spec_draft['run_avid'] = True

        try:
            _dec_norm, _, _ = _H._normalize_v1_decision(spec_draft)
        except Exception:
            _dec_norm = str(spec_draft.get('decision') or 'new_build').strip() or 'new_build'
        est_credits = int(_H._V1_CREDITS.get(_dec_norm, _H.CREDITS_PROFILE_ANALYSIS))
        spec_draft['estimated_credits'] = est_credits
        if _dec_norm == 'existing_match':
            try:
                _em_uname = ''
                _em_user = None
                try:
                    _em_uname = (session.get('username') or '').strip()
                    _em_user = _H.get_current_user()
                except Exception:
                    _em_user = None
                est_credits = _H._apply_existing_match_retail_price(
                    spec_draft, _em_user, _em_uname)
            except Exception:
                traceback.print_exc()
        # Override Claude's hallucinated run-time guess with a value
        # anchored to actual observed pipeline speed (see
        # _estimate_run_minutes docstring).
        spec_draft['estimated_run_minutes'] = _C._estimate_run_minutes(
            _dec_norm, bool(spec_draft.get('run_avid')))
        # Pin the est. sample now so the batch card shows the exact
        # number the build will use (idempotent through the pipeline).
        _C._jitter_draft_est_sample(spec_draft)

        # Subject naming + embedded cuts (2026-08-20): decompose any
        # demographic qualifier out of the subject; TU/avid build on
        # the full universe, qualifier rides as a 3-credit cut. Runs
        # AFTER the caller-subject override above so the forced batch
        # slice name is what gets decomposed. Reprices the draft.
        _H._decompose_embedded_subject_cuts(spec_draft, per_prompt)
        # Persona-universe normalization (2026-09-15): an audience
        # described by demographics + interests gets a clean cohort
        # label with age / income qualifiers riding as cuts. Same
        # treatment on every interpret surface.
        _H._v1_persona_universe_normalize(spec_draft, per_prompt)
        # Multi-cohort recovery: age cohorts named in the raw ask that
        # the interpreter dropped ride as additional cuts.
        _H._augment_multi_cohort_cuts(spec_draft, per_prompt)
        # Drop degenerate cuts (whole-universe age band / skew-redundant
        # gender) so a cut is always a strict, meaningful subset.
        _H._drop_degenerate_addon_cuts(spec_draft)
        # Cuts-only promoter: existing TU parent -> derive the cuts
        # off it instead of rebuilding (3 x cuts, no base).
        try:
            _H._maybe_promote_embedded_cuts_to_parent(spec_draft, catalog)
            _C._pm_promote_date_snapshot_ask(spec_draft, per_prompt)
            __import__('migration.saved_subject_size', fromlist=['apply_to_draft']).apply_to_draft(spec_draft, per_prompt, history=history)   # a saved audience size: the card quotes what the build starts from
        except Exception as _fx:
            _faults.note('chat', _fx)
        est_credits = int(spec_draft.get('estimated_credits')
                          or est_credits)
        try:
            _H._stamp_existing_match_age(spec_draft)
            _H._scrub_draft_prose_dashes(spec_draft)
        except Exception as _fx:
            _faults.note('chat', _fx)

        spec_draft['price_table'] = _oq.table_for_draft(_H, None, spec_draft, _C._pm_price_table())   # its quarter reads inside it
        return {
            'success': True, 'subject_input': subject,
            'spec_draft': spec_draft,
            'estimated_credits': est_credits,
            'candidates': [
                {k: v for k, v in c.items()
                 if not k.startswith('_') or k == '_score'}
                for c in candidates
            ],
            'model': result.get('model'),
        }
    except Exception as e:
        return {
            'success': False, 'subject_input': subject,
            'error': f'{type(e).__name__}: {e}',
        }


# <<block: 7>>
def _synth_chat_interpret_batch(user_text: str, subjects: list,
                                  history: list,
                                  shared_context_override: str = None):
    """Fan-out interpret across a list of subjects. Runs up to 10 Claude
    calls concurrently (well under our Anthropic key pool ceiling) and
    stitches the results into a single response with a `batch: true`
    flag so the frontend can render N approval cards. Concurrency was
    raised from 5 to 10 on 2026-09-02 alongside the SYNTH_CHAT_BATCH_MAX
    100 cap so a large batch drains fast enough to return before the
    HTTP request times out.

    `shared_context_override` (optional): when the caller has already
    computed the right per-subject context (e.g. the Cartesian
    detector pre-baked the definitional context into every subject),
    use that instead of trying to re-derive it from user_text. Falls
    back to `_shared_context_from_batch` when not provided.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if shared_context_override is not None:
        shared_context = shared_context_override
    else:
        shared_context = _H._shared_context_from_batch(user_text, subjects)
    print(f"[synth-chat interpret batch] subjects={subjects} "
          f"shared_context={shared_context!r}")

    # Capture per-user attribution NOW while we are on the Flask
    # request thread. The ThreadPoolExecutor workers below run in
    # separate threads with no request context, so a per-worker
    # _pm_attrib_extras() call returns {} and every interpret record
    # ships unattributed. Pre-capturing here and passing through to
    # each worker keeps attribution intact (2026-09-04 fix; matches
    # the deck-job pattern that stashes attribution at kickoff).
    _batch_attrib = _C._pm_attrib_extras() or None

    results_by_index: dict = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {
            pool.submit(_C._synth_chat_interpret_one_subject,
                         subj, shared_context, history,
                         _batch_attrib): idx
            for idx, subj in enumerate(subjects)
        }
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                results_by_index[idx] = fut.result()
            except Exception as e:
                results_by_index[idx] = {
                    'success': False,
                    'subject_input': subjects[idx],
                    'error': f'{type(e).__name__}: {e}',
                }

    # One retry per failed slice (2026-09-22, Will & Grace year batch:
    # one of five slices truncated and the package shipped short even
    # though four drafts were healthy). Sequential and single-shot -
    # temperature variance clears transient truncations, and a slice
    # that fails twice stays a failure for the partial-batch path.
    for i, r in list(results_by_index.items()):
        if r.get('success'):
            continue
        try:
            print(f"[synth-chat interpret batch] retrying failed "
                  f"slice {subjects[i]!r}: "
                  f"{str(r.get('error'))[:120]}")
            results_by_index[i] = _C._synth_chat_interpret_one_subject(
                subjects[i], shared_context, history, _batch_attrib)
        except Exception as e:
            results_by_index[i] = {
                'success': False,
                'subject_input': subjects[i],
                'error': f'{type(e).__name__}: {e}',
            }

    ordered = [results_by_index[i] for i in range(len(subjects))]
    ok_drafts = [r for r in ordered if r.get('success')]
    failures = [r for r in ordered if not r.get('success')]
    total_credits = sum(int(r.get('estimated_credits') or 0)
                        for r in ok_drafts)

    # Zero-drafts guard (2026-08-20): when EVERY per-subject interpret
    # fails, returning batch:true with an empty spec_drafts array made
    # the frontend fall through to an empty single-draft card that
    # read 'Building a new **this profile** profile'. Fail loudly with
    # a friendly rephrase ask instead; log the real errors server-side.
    if not ok_drafts:
        print("[synth-chat interpret batch] ALL subject interprets "
              "failed: " + "; ".join(
                  f"{f.get('subject_input')!r}: {f.get('error')}"
                  for f in failures[:8]))
        # Direct retry (2026-08-20, "make the chatbot smarter"): the
        # regex splitter demonstrably produced garbage slices, so ask
        # Claude ONCE on the ORIGINAL text in array mode - it is a far
        # better splitter than the regexes when phrasing gets creative.
        try:
            try:
                from iq_rankers import MASTER_CATEGORIES as _MC
            except Exception:
                _MC = {}
            _catalog = _C._profile_catalog_for_chat()
            # Union shortlist (2026-08-20): per-item parent candidates,
            # not just whole-text matches, so rule 7a cut-if-exists can
            # link every element to its base profile.
            _cands = _H._union_shortlist_for_multi(user_text, _catalog)
            _sp, _up = _C._synth_chat_interpret_prompts(
                user_text, chat_history=history,
                master_categories=_MC, candidate_matches=_cands)
            _up += ("\n\nIMPORTANT: This request names MULTIPLE "
                    "profiles. Return a JSON ARRAY with one complete "
                    "spec object per profile, each with its own "
                    "precise subject. Follow rules 7a (per-element "
                    "decision logic, cut-if-exists) and 7b (lean "
                    "elements, never drop a profile).")
            # 32k output ceiling (2026-08-20): 5 full specs need
            # ~20-25k tokens; the old 8192 cap truncated the array
            # mid-object EVERY time and failed the whole batch.
            _res = _H._run_nflx_claude_agent(
                system_prompt=_sp, user_prompt=_up,
                max_tokens=32000, temperature=0.4,
                model=_C._SYNTH_CHAT_INTERPRET_MODEL,
                salvage_arrays=True,
                usage_tag=('interpret', 'chatbot', _batch_attrib))
            _data = _res.get('data') if _res.get('success') else None
            if isinstance(_data, dict):
                _data = [_data]
            _drafts = [d for d in (_data or []) if isinstance(d, dict)
                       and not _H._is_placeholder_subject(d.get('subject'))]
            if _drafts:
                print(f"[synth-chat interpret batch] direct retry "
                      f"recovered {len(_drafts)} drafts")
                for _d in _drafts:
                    _H._finalize_chat_draft(_d, prompt_text=user_text,
                                         catalog=_catalog)
                return _H._batch_payload_from_drafts(
                    _drafts, user_text, history,
                    model=_res.get('model'))
        except Exception as _retry_err:
            print(f"[synth-chat interpret batch] direct retry failed: "
                  f"{_retry_err}")
        _H._chatbot_error_email(
            'brief-chat/interpret',
            'batch interpret failed for every subject: '
            + '; '.join(
                f"{str(f.get('subject_input'))[:80]}: "
                f"{str(f.get('error'))[:160]}"
                for f in failures[:6]),
            tb='(all per-subject interprets and the direct retry '
               'failed)')
        return jsonify(_H._chatbot_calm_payload())

    # Batch-level date-clarification gate (2026-08-20, Jenna: "the user
    # did not specify a time frame so the bot should have prompted in
    # the chat as well"). Same contract as the single-subject path: if
    # neither the user's text/history nor any per-subject interpret
    # flagged the window as explicit, the frontend asks BEFORE showing
    # the batch approval summary. The proposed range comes from the
    # first draft (all drafts share the standard default when the ask
    # had no dates).
    user_explicit = _H._user_specified_dates(user_text, chat_history=history)
    claude_explicit = any(
        bool((r.get('spec_draft') or {}).get('date_range_explicit'))
        for r in ok_drafts
    )
    needs_date_clarification = False   # the batch card lists the window too (2026-10-13 audit)
    _dr0 = {}
    if ok_drafts:
        _dr0 = (ok_drafts[0].get('spec_draft') or {}).get('date_range') or {}
    proposed_range = _H._proposed_range_from_drafts(
        [r.get('spec_draft') for r in ok_drafts])

    if failures:
        _H._chatbot_error_email(
            'brief-chat/interpret',
            f'batch interpret failed for {len(failures)} of '
            f'{len(subjects)} subject(s): '
            + '; '.join(
                f"{str(f.get('subject_input'))[:80]}: "
                f"{str(f.get('error'))[:160]}"
                for f in failures[:6]),
            tb='(per-subject batch interpret failures)')
    # Per-item plain-language window (ECHO RULE 2026-08-24): the batch
    # card renders a window suffix on each line when present.
    _H._annotate_drafts_date_window([r.get('spec_draft') for r in ok_drafts])
    return jsonify({
        'success': True,
        'batch': True,
        'batch_size': len(subjects),
        'subjects': subjects,
        'needs_date_clarification': needs_date_clarification,
        'proposed_date_range': proposed_range,
        # Grounded clarify (2026-08-27): last-used custom window chip.
        'memory_window': (_C._pm_memory_last_window()
                          if needs_date_clarification else None),
        'spec_drafts': [r.get('spec_draft') for r in ok_drafts],
        'per_subject_meta': [
            {
                'subject_input': r.get('subject_input'),
                'estimated_credits': r.get('estimated_credits'),
                'model': r.get('model'),
                'candidates': r.get('candidates', []),
            } for r in ok_drafts
        ],
        'failures': [
            {'subject_input': r.get('subject_input'),
             'error': r.get('error')}
            for r in failures
        ],
        'estimated_credits_total': total_credits,
        'shared_context': shared_context,
    })


__all__ = ['SYNTH_CHAT_BATCH_MAX', '_SYNTH_CHAT_INTERPRET_MODEL', '_RUN_MINUTES_TABLE', '_estimate_run_minutes', '_jitter_draft_est_sample', '_synth_chat_interpret_one_subject', '_synth_chat_interpret_batch']
