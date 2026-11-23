"""The interpret turn: the route that drafts a brief from a chat message, its core, and the auto-start of an order the words already approve.

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
import traceback
import re
from prometheus import smarter as _smart
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from prometheus import bpiq_wizard as _bpiq_wiz
from prometheus import named_platforms as _named_platforms
from prometheus import build_ready as _build_ready, list_shape as _list_shape
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import order_quote as _oq

# <<moved>>
# <<block: 1>>
@_H.app.route('/api/brief-chat/interpret', methods=['POST'])
@_H.app.route('/api/synth-chat/interpret', methods=['POST'])  # legacy alias
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/interpret')
@_C._ask_logged('interpret')
def api_synth_chat_interpret():
    """Free-form text -> draft spec JSON. Non-destructive: does NOT queue
    the run. Front-end shows the returned draft as an approvable brief.

    Session-authenticated dashboard users only. Partner API keys must
    use POST /api/v1/profiles/check instead so they never see internal
    chatbot JSON shapes.
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): the interpret step is
    # the entry point for a new profile pull, so an 'analysis'-only
    # user is blocked here. The router deflection to a read pass
    # never fires for such a user because the frontend hides the
    # build-oriented chips and the backend refuses the pull entry.
    if not _C._pm_gate_pull(user):
        return _C._pm_gate_refusal('pull')
    # Funds gate (2026-09-16, Jenna): interpreting a brief costs model
    # calls; a drained account gets the paused reply, not free work.
    _funds_resp = _C._pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/interpret', e)
        return jsonify(_H._chatbot_calm_payload())

    text = (body.get('text') or '').strip()
    if not text:
        _H._chatbot_error_email('brief-chat/interpret',
                             'empty request text from chat UI',
                             tb='(request validation)')
        return jsonify(_H._chatbot_calm_payload())

    history = body.get('history') or []
    return _C._pm_interpret_core(user, body, text, history)


# <<block: 2>>
def _pm_auto_start_order(user, spec_draft, order_text, dec, clarify_steps, chip_options, body):
    """An unlimited seat's explicit order starts building in the same turn
    (Jenna 2026-11-08: included with your plan, no price, no chip;
    2026-11-10, Grace Yin: an order is queued, never deflected). Returns
    the Flask response the widget renders as the build turn, or None
    when the brief still needs the reader (a clarify step, an identity
    picker, a paying seat, a reuse or a cut, a probe)."""
    if not isinstance(spec_draft, dict) or clarify_steps or chip_options:
        return None
    if str(dec or '').strip().lower() not in ('new_build', 'time_shifted_refresh', 'cut_needs_parent'):
        return None
    if isinstance(body, dict) and body.get('_pm_no_auto_start'):
        return None
    from prometheus import orders as _orders
    from prometheus.priced_read import included_with_plan as _incl
    _otext = str((body or {}).get('_pm_order_text') or order_text or '')
    if not _orders.is_order(_otext):
        return None
    _uname = (session.get('username') or (user or {}).get('username') or '').strip()
    if not _incl(user, _uname, None):
        return None
    if _C._pm_probe_caller() or _faults.is_probe() or _C._pm_is_probe_user(_uname):
        return None      # a probe grades the brief; nothing queues
    _email = str((user or {}).get('email') or '').strip()
    resp = _C._pm_approve_core(user, {'spec_draft': spec_draft, 'run_avid': bool(spec_draft.get('run_avid', True)),
                                   'email_to': _email})
    _status = 200
    if isinstance(resp, tuple):
        resp, _status = resp[0], (resp[1] if len(resp) > 1 else 200)
    try:
        data = resp.get_json(silent=True) or {}
    except Exception:
        data = {}
    if _status != 200 or not data.get('success') or not data.get('run_id'):
        return resp if _status == 200 else (resp, _status)   # the approve path's own guidance stands (a duplicate, a hold)
    _name = str(data.get('subject') or spec_draft.get('subject') or spec_draft.get('name') or '')
    _win = str(spec_draft.get('date_window_label') or '').split(' (')[0].strip()
    reply = _orders.build_line(_name, _win, avid=bool(data.get('run_avid', True)), included=True,
                               pending_position=data.get('pending_position'), notes=spec_draft.get('deliverable_notes') or '')
    out = dict(data)
    out.update({'auto_started': True, 'included_with_plan': True, 'message': reply, 'reply': reply,
                'subject': _name, 'followups': []})
    try:
        _C._pm_ask_hint(route='build_order', outcome='queued', subject=_name)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return jsonify(out)


# <<block: 3>>
def _pm_interpret_core(user, body, text, history):
    """The interpret surface for an already-gated user.

    Split out of ``api_synth_chat_interpret`` (2026-10-01, Prometheus
    Phase 1) so the same body runs for a session caller on the legacy
    route and for any caller of ``prometheus.service.ask`` (dashboard
    widget, standalone app, API key). Gates (mode, funds) and the body
    parse stay with the callers; everything from here down is the
    surface itself and returns a Flask response exactly as before.
    """

    # Brand Partnership Valuation wizard (Jenna 2026-10-28): the chip
    # must never become a library-comp read on this surface.
    try:
        _bw_user = (session.get('username') or (user or {}).get('username')
                    or '').strip()
        _bw_store = _bpiq_wiz.store_for(body, _C.globals())
        _bw_tid = str((body or {}).get('thread_id') or '')
        if _bpiq_wiz.wants(text, body, history, store=_bw_store,
                           user=_bw_user, thread_id=_bw_tid):
            _bw_hit = _bpiq_wiz.analyze_hook(
                text, body, history, user, _C._pm_usage_extras(user),
                _bw_user, _C.globals(), first=_bpiq_wiz.is_start(text),
                store=_bw_store)
            if _bw_hit is None:
                _bw_hit = _bpiq_wiz.hold_payload(
                    history, _bw_store, _bw_user, _bw_tid)
            if _bw_hit is not None:
                _bw_hit = dict(_bw_hit)
                _bw_hit.pop('read_job_id', None)
                _bw_hit.pop('job_id', None)
                return jsonify(_bw_hit)
    except Exception:
        traceback.print_exc()

    # PRICING QUESTIONS (2026-09-23 Jenna): the flat rate card, never the build intake.
    # Runs before every detector ('how much is a credit' once reached interpret and drafted a build brief).
    if _C._pm_pricing_question(text):
        _C._pm_ask_hint(route='pricing_fact', outcome='answered')
        return jsonify({'success': False, 'guidance': True,
                        'error': _C._pm_pricing_copy(_C._pm_ask_log_user(''), user)})
    _ch = __import__('prometheus.build_guards', fromlist=['correction_hold']).correction_hold(text, history)   # a complaint is never a go-ahead (Emmet Riley 2026-10-26)
    if _ch:
        _C._pm_ask_hint(route='correction', outcome='no_draft')
        return jsonify(_ch)
    # Subscriber IQ lookup (2026-10-02 Bria): "do you see the X Subscriber IQ?" reaching the
    # build surface answers from the library instead of drafting a duplicate order.
    try:
        from prometheus import guards as _pg_lk
        _lk_title = _pg_lk.subiq_lookup_title(text)
        _sp = None if _lk_title else _C._pm_subiq_platform_only_question({'title': _pg_lk.subiq_platform_only(text)}, text)
    except Exception:
        _lk_title, _sp = '', None
    if _sp:   # "Pull Subscriber IQ for Starz" waited 81s for this question; the reader had already stopped (Bria 2026-11-17)
        _C._pm_ask_hint(route='subiq_platform_only', outcome='asked_title')
        return jsonify(_sp)
    if _lk_title:
        _lk_reply, _lk_chips = _C._pm_subiq_lookup_answer(user, _lk_title)
        try:
            _C._pm_ask_hint(route='subiq_lookup', outcome='answered',
                         subject=_lk_title if _lk_title != '*' else '')
        except Exception as _fx:
            _faults.note('chat', _fx)
        return jsonify({'success': False, 'guidance': True,
                        'error': _lk_reply, 'followups': _lk_chips})
    # Deterministic lanes run before any library or model path (2026-10-02 S7 + S3 lanes):
    # "is eastside golf running?" is a status question even when it is question-shaped and
    # names a library title; it answers from the caller's runs and the library on this surface
    # too (it used to spend a model call on a lookup, or draft a fresh 5-credit build here).
    _st_lane = _C._pm_status_lane(user, text)
    if _st_lane:
        _st_reply, _st_chips, _st_outcome, _st_subject = _st_lane
        _C._pm_ask_hint(route='status_check', outcome=_st_outcome,
                     subject=_st_subject)
        return jsonify({'success': False, 'guidance': True,
                        'error': _st_reply, 'followups': _st_chips})
    # A question about reads the library already holds (compare two titles' first three days,
    # which had more new accounts) answers from those reads instead of drafting a new order (2026-10-02 Bria). Pull verbs still draft.
    try:
        from prometheus import guards as _pg_q
        import prometheus_analysis as _pma_q
        _lib_q = (not _pg_q.subiq_is_explicit_pull(text)
                  and _pg_q.is_question_shaped(text)
                  and bool(_pma_q.find_subiq_titles_in_text(
                      _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, text)))
    except Exception:
        traceback.print_exc()
        _lib_q = False
    if _lib_q:
        try:
            _C._pm_ask_hint(route='subiq_library_answer')
        except Exception as _fx:
            _faults.note('chat', _fx)
        _lib_resp = _C._pm_generate_metrics_response(user, text, history)
        try:
            _lib_raw = _lib_resp.get_json(silent=True) or {}
        except Exception:
            _lib_raw = {}
        if _lib_raw.get('success') and _lib_raw.get('reply'):
            _out = {'success': False, 'guidance': True,
                    'analysis_read': True,
                    'error': str(_lib_raw['reply'])}
            for _k in ('read_job_id', 'memory_confirm', 'panel_offer',
                       'referent_clarify', 'file_link'):
                if _lib_raw.get(_k):
                    _out[_k] = _lib_raw[_k]
            if _lib_raw.get('followups'):
                _out['followups'] = [str(f) for f in _lib_raw['followups']
                                     if f][:4]
            return jsonify(_out)
        return _lib_resp
    # Work-order verbs (2026-09-30 Jenna): 'stop' / 'status' /
    # 'how long' on the build surface must never draft a build.
    _wo_intent = _C._pm_workorder_intent(text)
    if _wo_intent:
        _wo_reply = _C._pm_workorder_reply(user, text, _wo_intent)
        if _wo_reply:
            try:
                _C._pm_ask_hint(outcome='workorder_' + _wo_intent)
            except Exception as _fx:
                _faults.note('chat', _fx)
            return jsonify({'success': False, 'guidance': True,
                            'error': _wo_reply})
    # Typed approval with no brief on screen (2026-10-02 S3 lanes):
    # "approved" reaching this surface bare means the card is gone
    # (reload, new tab). It used to become a build for a subject
    # named "approved".
    if _C._pm_approval_word(text):
        if _smart.rearm_brief_from_thread(body, history, user, _C._pm_usage_extras(user), _C._PM_INTAKE_UI, _C._pm_intake_turn):
            return _C._pm_analyze_core(user, body, text, history)   # the re-armed flow's confirm branch lives there (2026-10-14, golden g085)
        _C._pm_ask_hint(route='command', outcome='approve_without_draft')
        return jsonify({'success': False, 'guidance': True,
                        'error': _C._PM_APPROVE_NO_DRAFT_COPY})

    # CAN'T-DO LANE (2026-10-02 S8): the same honest boundary on the
    # build surface, so "refund those credits" or "add my colleague"
    # never becomes a draft named after the request.
    _cd_lane = _C._pm_cant_do_lane(
        (session.get('username') or user.get('username') or '').strip(),
        text)
    if _cd_lane:
        _cd_reply, _cd_chips, _cd_kind = _cd_lane
        _C._pm_ask_hint(route='cant_do', outcome=_cd_kind)
        return jsonify({'success': False, 'guidance': True,
                        'error': _cd_reply, 'followups': _cd_chips})

    # ------------------------------------------------------------------
    # INCIDENCE / SAMPLE-SIZE PRE-CHECK (2026-08-19; priced 2026-10-21):
    # an audience size is a $100 read, quoted first. The ten million
    # panel line stays free. Runs BEFORE batch and Cartesian detection
    # because those detectors can misread a question containing commas
    # as a multi-subject build request.
    # ------------------------------------------------------------------
    if _C._synth_chat_is_incidence_request(text) and not _subject_rules.wants_breakdown(text):   # I3: a split or a table is never the sample card
        try:
            from prometheus import priced_read as _pr_inc
            _inc_pl = _pr_inc.sizing_offer_payload(text, history, _C._pm_ask_log_user(''), user=user)   # no quote for an included seat (2026-11-08)
            if _inc_pl:
                _C._pm_ask_hint(route='incidence', outcome='panel_offer', subject=_inc_pl.get('subject'))
                return jsonify(_inc_pl)
            return _C._synth_chat_incidence_check(text, history)
        except Exception as _inc_err:
            traceback.print_exc()
            _H._chatbot_error_email('brief-chat/interpret', _inc_err)
            return jsonify(_H._chatbot_calm_payload())

    # ------------------------------------------------------------------
    # ROUTER-DRIVEN DEFLECTION (2026-08-28, the routing wave; grew out
    # of the 2026-08-27 Paw Patrol analysis-ask deflection): the same
    # server-side router the analyze endpoint consults decides what an
    # ask that reached this build surface actually wants, in one
    # precedence. Subscriber IQ family detection ranks AHEAD of the
    # analysis deflection here - "how many people signed up because of
    # Landman" promotes to the Subscriber IQ flow downstream instead
    # of deflecting to a generated read. An analysis-phrased ask whose
    # subject has a base on file routes to the measured-read pass; a
    # typed CSV-export ask is served; a search-demand ask gets its
    # study; a non-digital ask gets the graceful decline. Only a clean
    # delivered answer deflects; any other outcome (no base, credit
    # gate, model trouble) falls through to the normal build interpret
    # below, and subiq / churn-fork / build verdicts always fall
    # through to the promotion machinery downstream.
    # ------------------------------------------------------------------
    try:
        import prometheus_analysis as _pma_route
        _deflect_payload = None

        def _interp_has_base():
            # Catalog base for the named subject, or a stored cross-
            # session referent (2026-08-27: "toy white space" must ask
            # "do you mean for paw patrol viewers parents" instead of
            # composing a paid rebuild). Memory is strictly per-user.
            try:
                if _C._pm_generation_base('', text, prefer_catalog=True):
                    return True
            except Exception:
                traceback.print_exc()
            try:
                import prometheus_memory as _pmm_route
                return bool(_pmm_route.recent_referents(
                    (session.get('username') or user.get('username')
                     or '').strip(), k=1))
            except Exception:
                return False

        _t_router = time.monotonic()
        from prometheus import routing as _routing_i, understanding as _und_i
        # the Understanding decides this surface's route as well (prometheus.routing, 2026-11-23): an analysis ask
        # on a subject with a base on file is the measured read, the rest the build flow; no model is called to route
        _route_d = _routing_i.analyze_route(
            _und_i.on_body(body) or _und_i.current(), text, surface='interpret',
            has_base=_interp_has_base)
        _C._pm_ask_stage('router', t0=_t_router)
        _route = __import__('prometheus.build_lane', fromlist=['keep_order_route']).keep_order_route(str(_route_d.get('route') or ''), body, text, hint=_C._pm_ask_hint)   # an order for a profile is never deflected to a read here (2026-11-17)
        if _route == 'not_quantifiable':
            # Graceful decline (2026-08-26): behavior with no digital
            # trace never produces a number, on this surface either.
            _gate = _route_d.get('nq_gate') or {}
            _nq_reply, _nq_chips = \
                _pma_route.build_not_quantifiable_reply(text, _gate)
            _C._pm_ask_hint(route='quantifiability_gate',
                         outcome='declined_not_quantifiable')
            return jsonify({
                'success': False,
                'guidance': True,
                'analysis_read': True,
                'error': _nq_reply,
                'followups': _nq_chips,
            })
        if _route == 'csv_download':
            _csv_resp = _C._pm_csv_download_response(user, text,
                                                  history=history)
            _csv_obj = _csv_resp[0] if isinstance(_csv_resp, tuple) \
                else _csv_resp
            _csv_data = _csv_obj.get_json(silent=True) or {}
            if _csv_data.get('success') and _csv_data.get('reply'):
                _deflect_payload = _csv_data
        elif _route == 'search_demand':
            _sd_resp = _C._pm_search_demand_response(user, text, history)
            _sd_obj = _sd_resp[0] if isinstance(_sd_resp, tuple) \
                else _sd_resp
            _sd_data = _sd_obj.get_json(silent=True) or {}
            if _sd_data.get('success') and _sd_data.get('reply') \
                    and not _sd_data.get('build_required'):
                _deflect_payload = _sd_data
        elif _route == 'generate':
            _an_resp = _C._pm_generate_metrics_response(
                user, text, history, prefer_catalog=True)
            _an_obj = _an_resp[0] if isinstance(_an_resp, tuple) \
                else _an_resp
            _an_data = _an_obj.get_json(silent=True) or {}
            if _an_data.get('success') and _an_data.get('reply') \
                    and not _an_data.get('build_required') \
                    and not _an_data.get('pay_per_use_offer'):
                _deflect_payload = _an_data
        if _deflect_payload:
            _reply_text = str(_deflect_payload['reply'])
            _fups = [str(f) for f in
                     (_deflect_payload.get('followups') or []) if f]
            if any(f.lower() == _pma_route.CSV_OFFER_CHIP.lower()
                   for f in _fups):
                _reply_text += ("\n\nReply \"download this data as a "
                                "CSV\" and I'll drop the file.")
            # The guidance shape is the one payload this surface
            # renders as a plain chat turn without an approval card.
            _guid = {
                'success': False,
                'guidance': True,
                'analysis_read': True,
                'error': _reply_text,
            }
            # A file handed over on this surface rides the guidance turn
            # (2026-11-04, Bria: the download lane answered "Saved" here
            # with nothing for the browser to save).
            for _fk in ('download_url', 'filename', 'file_link'):
                if _deflect_payload.get(_fk):
                    _guid[_fk] = _deflect_payload[_fk]
            if _deflect_payload.get('download_url') or _deflect_payload.get('file_link'):
                _guid['followups'] = list(_deflect_payload.get('followups') or [])
            # Fresh generations ride a background job (2026-08-27):
            # the widget polls read-status and swaps the holding line
            # for the finished read when it lands.
            if _deflect_payload.get('read_job_id'):
                _guid['read_job_id'] = _deflect_payload['read_job_id']
            # Cross-session memory confirm (2026-08-27): the widget
            # arms the confirm and renders the referent chips.
            if _deflect_payload.get('memory_confirm'):
                _guid['memory_confirm'] = \
                    _deflect_payload['memory_confirm']
                _guid['followups'] = list(
                    _deflect_payload.get('followups') or [])
            # Research-report quote (2026-09-14): the widget arms the
            # priced confirm and renders the run / build / never-mind
            # chips.
            if _deflect_payload.get('panel_offer'):
                _guid['panel_offer'] = _deflect_payload['panel_offer']
                _guid['followups'] = list(
                    _deflect_payload.get('followups') or [])
            return jsonify(_guid)
    except Exception:
        traceback.print_exc()

    # ------------------------------------------------------------------
    # DISCOVERY (2026-08-19): pitch-shaped asks without an audience
    # ("I'm pitching GoGo squeeZ") get 2-4 proposed audience framings
    # to pick from instead of a guessed build. Direct build asks skip
    # this branch entirely.
    # ------------------------------------------------------------------
    if _C._synth_chat_is_discovery_request(text):
        try:
            return _C._synth_chat_discovery_options(text)
        except Exception:
            traceback.print_exc()
            # Non-fatal: fall through to the normal interpret so a
            # detector misfire never blocks a legitimate build ask.
            pass

    # ------------------------------------------------------------------
    # BATCH MODE (2026-08-18): if the prompt matches
    # "run individual profiles for X, Y, Z" style, fan out into one
    # Claude call per subject and return an array of spec_drafts. The
    # frontend renders one approval card per draft + an "Approve all"
    # button. Cap enforced at SYNTH_CHAT_BATCH_MAX so we don't swamp
    # the interpret step or the 10-worker pool.
    #
    # PRECEDENCE (2026-08-20 Jenna: "create seperate profiles on EST
    # buyers and TVOD renters for each platform Amazon, Apple, ..."
    # was hijacked by the plain splitter into 6 mangled slices like
    # 'TVOD renters for each platform Amazon'): shape-B Cartesian
    # (segmentation marker: 'for each platform X, Y, Z') ALWAYS wins,
    # even with one cohort - the plain splitter demonstrably mangles
    # that phrasing ('EST Buyers for Each Platform Amazon' + bare
    # 'Apple'). Shape-A (trailing parenthetical) wins with 2+ real
    # cohorts; a degenerate 1-cohort read ("TV brands (VIZIO,
    # Samsung, LG)") defers to the plain splitter's cleaner
    # one-entity slices.
    # ------------------------------------------------------------------
    # One title asked across services (Jay Patel 2026-11-02: "three
    # distinct profiles: watched Wuthering Heights on any streaming
    # service / on Amazon Prime Video / on HBO Max") is ONE build: the
    # title's viewers on any service, with one behavioral cut per
    # service named, pinned to 100 on it (Jenna: "a wuthering heights
    # profile with two cuts based on streaming service"). The model
    # reads the one-sentence universe; the cuts and the how-it-is-built
    # note ride the draft below. Before this the model wrote three
    # drafts, ran past its ceiling twice and the turn died at 181 s.
    _platform_hit = None
    try:
        from prometheus import platform_cuts as _pcuts
        _platform_hit = _pcuts.detect(text)
        if _platform_hit:
            print(f"[synth-chat interpret] platform cuts: {_platform_hit['title']!r} on "
                  f"{_platform_hit['platforms']} (any={_platform_hit['any']}, method={_platform_hit['method_question']})")
            text = _pcuts.rewrite_for_interpret(text, _platform_hit)
            # the Reader read three subjects; the model must see one, or it
            # writes three drafts again
            body = dict(body or {}, reading=None)
    except Exception as _fx:
        _faults.note('chat', _fx)
        _platform_hit = None
    # An order's deliverable notes ride the brief, never the subject read
    # (Grace Yin 2026-11-10, prometheus.orders): "Include reach, age and
    # gender split, top other titles ... Deliver as a CSV with the
    # unprojected panelist count for each figure" are requirements on the
    # file, not cuts and not a second subject. The model reads the order
    # alone; the notes land on the draft and the spec, and the confirm
    # says what the file carries.
    _order_text, _order_notes = text, ''
    try:
        from prometheus import orders as _orders
        _oc, _order_notes = _orders.split(text)
        if _order_notes and _oc:
            print(f"[synth-chat interpret] deliverable notes set aside: {_order_notes[:160]!r}")
            text = _oc
    except Exception as _fx:
        _faults.note('chat', _fx)
        _order_notes = ''
    # A single compound behavioral audience ("anyone who watched X and
    # any one of the following A, B, C on streaming") is ONE build, not
    # a batch - never let the list OR Cartesian splitters shatter it
    # into pseudo-subjects. Fall through to the single interpret, which
    # already reads stacked criteria as one persona. See
    # _is_single_compound_audience + profile-iq-pipeline-rules.mdc s2.
    _single_compound = bool(_platform_hit) or _H._is_single_compound_audience(text)
    # 2026-10-02 audit: a question is never a batch. "Can I cut the
    # existing Apple TV+ profile by Quarter (i.e., 2Q 2026)?" was split
    # into two builds named "I.e Can I Cut ..." and "2Q 2026 Can I Cut
    # ...". Capability questions and question-shaped asks with no build
    # verb skip both splitters and read as one request.
    try:
        from prometheus import guards as _pg
        _is_q = _pg.is_capability_question(text) or (
            _pg.is_question_shaped(text)
            and not re.search(r'\b(?:run|build|pull|create|queue|'
                              r'generate|make|give me|i need|i want)\b',
                              text, re.I))
    except Exception:
        _is_q = False
    _single_compound = _single_compound or _is_q
    _batch_subjects, _ls_ask = _list_shape.names(text, [] if _single_compound else _H._detect_batch_subjects(text))   # names only; the lines of a pasted read are details of one order (Jessie 2026-11-11)
    if _ls_ask:
        return jsonify(_ls_ask)
    _cart = None if _single_compound else _H._detect_cartesian_batch(text)
    _cart_wins = bool(_cart) and (
        (len(_cart) >= 4 and _cart[3] == 'B')
        or (len(_cart) >= 3 and _cart[2] >= 2))
    if _cart_wins:
        _batch_subjects = []
    elif _batch_subjects:
        _cart = None
    if _batch_subjects:
        if len(_batch_subjects) > _C.SYNTH_CHAT_BATCH_MAX:
            return jsonify({
                'success': False,
                'guidance': True,
                'error': (
                    f"That looks like {len(_batch_subjects)} subjects. "
                    f"Batch mode is capped at {_C.SYNTH_CHAT_BATCH_MAX} at "
                    f"a time - split it into two messages and I'll queue "
                    f"them all in parallel."
                ),
            }), 400
        try:
            return _C._synth_chat_interpret_batch(
                text, _batch_subjects, history=history,
            )
        except Exception as _batch_err:
            traceback.print_exc()
            _H._chatbot_error_email('brief-chat/interpret', _batch_err)
            return jsonify(_H._chatbot_calm_payload())

    # Cartesian batch (2026-08-18): patterns like
    #   "make a profile of EST buyers vs. TVOD renters by retailer
    #    (Amazon, Apple, Fandango at Home, Google Play/YouTube)"
    # fan out to |cohorts| * |items| profiles. Precedence against the
    # plain list detector is decided above: 2+ cohorts wins, 1-cohort
    # reads defer to the plain splitter's cleaner one-entity slices.
    if _cart:
        _cart_subjects, _cart_shared_ctx = _cart[0], _cart[1]
        if len(_cart_subjects) > _C.SYNTH_CHAT_BATCH_MAX:
            return jsonify({
                'success': False,
                'guidance': True,
                'error': (
                    f"That expands to {len(_cart_subjects)} profiles. "
                    f"Batch mode is capped at {_C.SYNTH_CHAT_BATCH_MAX} at "
                    f"a time - split it up and I'll queue them in "
                    f"parallel."
                ),
            }), 400
        try:
            return _C._synth_chat_interpret_batch(
                text, _cart_subjects, history=history,
                shared_context_override=_cart_shared_ctx,
            )
        except Exception as _cart_err:
            traceback.print_exc()
            _H._chatbot_error_email('brief-chat/interpret', _cart_err)
            return jsonify(_H._chatbot_calm_payload())

    # Claude-as-splitter fallback (2026-08-20, Jenna: "does the agent
    # need to be smarter?"). When BOTH regex detectors miss but the
    # text plainly asks for multiple profiles ("for each platform
    # (A, B, C so that ...", unclosed parens, trailing clauses), don't
    # run a mangled single interpret - hand the ORIGINAL text to the
    # array-mode Claude interpret inside _synth_chat_interpret_batch
    # (empty subject list routes straight to its direct-retry path,
    # which finalizes each draft and returns the standard batch
    # payload).
    if not _single_compound and re.search(
                 r'\bfor each\b|\bone (?:profile )?per\b'
                 r'|\bprofiles? for each\b|\bper (?:platform|retailer'
                 r'|brand|market|title)\b', text, re.IGNORECASE):
        try:
            return _C._synth_chat_interpret_batch(text, [], history=history)
        except Exception as _marker_err:
            traceback.print_exc()
            print(f"[synth-chat interpret] marker-batch fallback "
                  f"failed, continuing to single interpret: {_marker_err}")

    _fur = __import__('prometheus.followup_refine', fromlist=['at_interpret']); _fu, _fu_early = _fur.at_interpret(text, history, body, _C._pm_ask_hint)   # a follow-up is the ask before it with one thing changed, read from the thread's own turns (Grace Yin 2026-11-18)
    if _fu_early:
        return jsonify(_fu_early)   # it points back and nothing is there: which title, in plain words, before any draft
    try:
        _t_prep = time.monotonic()
        try:
            from iq_rankers import MASTER_CATEGORIES
        except Exception:
            MASTER_CATEGORIES = {}
        catalog = _C._profile_catalog_for_chat()
        candidates = _C._shortlist_profile_matches(text, catalog)
        # Observability: log the candidate list so we can debug
        # misclassifications like "chatbot picked new_build when a
        # parent exists in the catalog."
        try:
            print(f"[synth-chat interpret] prompt={text[:120]!r} "
                  f"catalog_size={len(catalog)} candidates={len(candidates)}"
                  + ("".join([
                      f"\n  candidate: score={c.get('_score'):.3f} "
                      f"display={c.get('display_name')!r} "
                      f"key={c.get('s3_key')!r} age={c.get('days_old')}d"
                      for c in candidates[:5]
                  ]) if candidates else "  (no candidates)"))
        except Exception as _fx:
            _faults.note('chat', _fx)
        system_prompt, user_prompt = _C._synth_chat_interpret_prompts(
            text, chat_history=history, master_categories=MASTER_CATEGORIES,
            candidate_matches=candidates,
        )
        system_prompt, _dropped = _smart.prune_rulebook(system_prompt, text, candidates, reading=body.get('reading'))   # rulebook pruned per ask, Reader-aware (2026-10-13/14)
        user_prompt = _smart.with_reading(user_prompt, body.get('reading'))   # the Reader's understanding rides along (2026-10-14)
        _C._pm_ask_stage('interpret_prep', t0=_t_prep)
        _t_model = time.monotonic()
        # Subject verification runs alongside the draft model for the
        # name the ask plainly carries (2026-10-06, speed): the two
        # longest steps overlap instead of queuing.
        _preverify = _C._pm_start_preverify(text, candidates)
        result = _H._run_nflx_claude_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=_smart.draft_output_ceiling(text), temperature=0.4,
            model=_C._SYNTH_CHAT_INTERPRET_MODEL,
            salvage_arrays=True,
        )
        _C._pm_ask_stage('model', t0=_t_model)
        if not result.get('success'):
            _H._chatbot_error_email(
                'brief-chat/interpret',
                'interpret model call failed: '
                + str(result.get('error') or 'unknown')[:400],
                tb=('raw excerpt:\n'
                    + str(result.get('raw_excerpt') or '')[:2000])
                if result.get('raw_excerpt') else '(no raw excerpt)')
            return jsonify(_H._chatbot_calm_payload())

        spec_draft = result.get('data') or {}

        # Claude-native multi-profile output (prompt rule 7): a JSON
        # ARRAY means one spec per requested profile. Filter out
        # placeholder subjects, run every draft through the same
        # finisher the batch path uses (credits, run-minutes, est
        # sample, embedded-cut decomposition, cuts-only promoter),
        # then return the standard batch payload with the date gate.
        # A 1-element array degrades gracefully to the single-draft
        # flow below.
        if isinstance(spec_draft, list):
            drafts = [d for d in spec_draft if isinstance(d, dict)
                      and not _H._is_placeholder_subject(d.get('subject'))]
            if len(drafts) >= 2:
                for _d in drafts:
                    _H._finalize_chat_draft(_d, prompt_text=text,
                                         catalog=catalog)
                return _H._batch_payload_from_drafts(
                    drafts, text, history, model=result.get('model'))
            if len(drafts) == 1:
                spec_draft = drafts[0]
            else:
                _H._chatbot_error_email(
                    'brief-chat/interpret',
                    'interpret returned zero usable drafts for '
                    f'prompt: {text[:300]!r}',
                    tb='(array salvage produced no valid spec drafts)')
                return jsonify(_H._chatbot_calm_payload())

        # Subject guards on the draft (prometheus/build_guards, 2026-10-21): placeholder, the entity
        # behind an explicit build order (typo-tolerant, scope-aware), fragment, entity core.
        from prometheus import build_guards as _bgd, guards as _pg_mod
        spec_draft, _bgd_resp = _bgd.settle_subject(spec_draft, text, catalog, body, {
            'is_placeholder_subject': _H._is_placeholder_subject, 'rescue': _C._pm_rescue_unverified_draft,
            'manual_look_email': _H._prometheus_manual_look_email, 'ask_hint': _C._pm_ask_hint,
            'swallow': _C._pm_swallow, 'qualified_audience': _subject_rules.qualified_audience,
            'explicit_build_subject': _subject_rules.explicit_build_subject, 'guards': _pg_mod})
        if _bgd_resp is not None:
            return jsonify(_bgd_resp[0]), _bgd_resp[1]

        # Subscriber IQ intent net (2026-08-26): typo-tolerant
        # deterministic routing for Subscriber IQ asks the interpret
        # step misread ('subscriber aqcuisiont', 'first watch', 'what
        # drove signups'). Runs before the parent-match gates so a
        # catalog Profile IQ never hijacks a Subscriber IQ pull.
        _H._promote_subiq_intent(spec_draft, text)

        # Parent-selection guardrail (2026-08-18 Reba-2024 regression).
        # Deterministically swap Claude's existing_match away from any
        # historical-year or cut skin the user did not name. See
        # _enforce_base_parent_pick docstring for full logic.
        try:
            swapped, swap_note = _H._enforce_base_parent_pick(
                spec_draft, candidates, text)
            if swapped:
                print(f"[synth-chat interpret] {swap_note}")
                # Surface the correction so the frontend renders the
                # right file name in the approval card.
                spec_draft['_parent_guardrail_note'] = swap_note
        except Exception as _guard_err:
            print(f"[synth-chat interpret] guardrail error: {_guard_err}")

        # Subject identity resolution (2026-08-24 Furious defect):
        # binding medium/platform words in the ask win over a famous
        # lookalike name. Verifies suspect content titles via web
        # search; still-ambiguous drafts stash a confirm question.
        _t_identity = time.monotonic()
        try:
            _H._resolve_subject_identity(spec_draft, text, allow_ask=True) if not _fu else _H._resolve_subject_identity(spec_draft, text, allow_ask=True, **_fur.identity_kwargs(_fu))   # on a follow-up, a name the reader gave earlier in the thread is their own word (2026-11-18)
        except Exception as _id_err:
            print(f"[synth-chat interpret] identity error: {_id_err}")
        _C._pm_ask_stage('identity', t0=_t_identity)

        # Normalized existing-profile match (2026-08-24 SHARKNINJA
        # directive): same entity under case/spacing/punctuation
        # normalization (one-typo tolerant) never silently new_builds.
        # Stashes the use-existing vs pull-fresh question.
        try:
            _nm_acted, _nm_note = _H._enforce_normalized_existing_match(
                spec_draft, candidates, text,
                catalog=catalog, allow_ask=True)
            if _nm_acted:
                print(f"[synth-chat interpret] {_nm_note}")
        except Exception as _nm_err:
            print(f"[synth-chat interpret] normalized-match error: "
                  f"{_nm_err}")

        # Universe-qualifier gate (2026-08-25, Jenna's 'is that what
        # you meant?' flow): brand matches an existing profile but the
        # universe qualifier differs ('Apple buyers' vs 'Apple TV EST
        # Buyers') - never silently pick either path. Stashes the
        # qualifier_match clarify question; yes routes to the existing
        # profile, no proceeds with the fresh build as stated.
        try:
            from prometheus import match_question as _mq_g   # one profile is asked about once in a thread (2026-11-17)
            _uq_acted, _uq_note = _H._apply_universe_qualifier_gate(
                spec_draft, text, allow_ask=not _mq_g.asked_before(history, spec_draft.get('existing_match_display_name')))
            if _uq_acted:
                print(f"[synth-chat interpret] {_uq_note}")
        except Exception as _uq_err:
            print(f"[synth-chat interpret] qualifier-gate error: "
                  f"{_uq_err}")

        # Resolvable entity (2026-10-02 S6): a fresh chat build of a
        # subject the catalog does not carry runs the same ladder the
        # partner API runs (catalog collapse, near-miss suggestion,
        # model attestation, live evidence for the exact name). A
        # subject that does not resolve gets an honest question with
        # the likely intended name as a chip; no brief is drafted.
        # Fail-open on errors and timeouts; off under
        # PM_CHAT_SUBJECT_VERIFY=0 for hermetic runs.
        _t_verify = time.monotonic()
        _sv_block = _C._pm_chat_subject_verify(spec_draft, candidates, text,
                                            history=history, followup=_fu, preverify=_preverify)
        _C._pm_ask_stage('verify', t0=_t_verify)
        if _sv_block is not None:
            return _sv_block

        # Intersect-cut promoter (2026-08-19). Same logic that runs in
        # the batch check path: if the prompt is a cut of an existing
        # profile (e.g. "Vizio for Spider-Man Moviegoers"), promote
        # new_build -> derive_cut so we don't burn ~$40 of Anthropic
        # on a build that could have run for ~$2-4. 2026-08-20: passes
        # the full catalog so the parent is scored against the LEFT
        # operand (whole-prompt scores buried "Vizio TV Owners" under
        # the Spider-Man cohort tokens), and renames the deliverable
        # to '{Parent} - {Cut Label}'.
        _pre_promo_subject = str(spec_draft.get('subject') or '').split(' - ', 1)[0].strip()
        try:
            _H._maybe_promote_intersect_to_derive_cut(
                spec_draft, text, candidates, catalog=catalog)
        except Exception as _int_err:
            print(f"[synth-chat interpret] intersect-cut error: "
                  f"{_int_err}")
        # Whole-subject identity backstop (2026-11-06, Sydney McLain /
        # WME): 'Tomorrow x Together' was promoted to a cut of 'Hurry Up
        # Tomorrow Movie' on one shared word. No promoter may hand the
        # build to a catalog subject the user did not name.
        try:
            _H._guard_catalog_identity(spec_draft, text, _pre_promo_subject)
        except Exception as _gi_err:
            print(f"[synth-chat interpret] identity backstop error: {_gi_err}")

        # Parent-link question (2026-08-20 Jenna directive): if the
        # ask still looks like a cut of something but no parent was
        # confidently matched (or several distinct parents plausibly
        # match), ASK the user "is this a cut of X data we already
        # have?" instead of silently running a fresh build. Stashes
        # candidates on the draft; the parent_link clarify step reads
        # them.
        try:
            _H._maybe_ask_parent_link(spec_draft, text, catalog)
        except Exception as _pl_err:
            print(f"[synth-chat interpret] parent-link error: {_pl_err}")
        try: from prometheus.title_variants import apply_variant_guard as _tv_guard; _tv_guard(spec_draft)  # same-title variants (HowTo 5g, 2026-10-26): fresh builds only, after the existing-profile steps above; the guard gates itself
        except Exception as _tv_err: print(f"[synth-chat interpret] variant guard error: {_tv_err}")

        # Age-break alignment (2026-08-20 Jenna directive): a request
        # like "15-26" can't be honored by the panel's age breaks
        # (18-24 / 25-34 / ... / 65+), so ask the user to pick the
        # closest canonical coverage BEFORE anything builds. Canonical
        # ranges (18-24, 18-34, 55+, ...) pass through silently.
        try:
            _H._maybe_ask_age_breaks(spec_draft, text)
        except Exception as _ab_err:
            print(f"[synth-chat interpret] age-breaks error: {_ab_err}")

        # IP audience scope (2026-08-21 Jenna directive): a series /
        # movie / book / podcast / game ask must pick its universe -
        # broad engagers (standard profile, no home-platform pin) or
        # consumers-only (viewers / readers / listeners / players,
        # home platform pinned at 100). If the words already said,
        # normalize silently; otherwise stash the question.
        _named_platforms.bind(spec_draft, text, history, cuts_hit=_platform_hit, catalog=catalog)   # a platform the client names is the universe's platform (2026-11-10); the same title, services and window as a live profile is that profile (2026-11-12)
        try:
            _H._maybe_ask_ip_scope(spec_draft, text)
        except Exception as _ip_err:
            print(f"[synth-chat interpret] ip-scope error: {_ip_err}")

        # Viewer content scope (2026-08-27 Jenna): a viewers universe
        # researches what the title IS (series / franchise film /
        # standalone) and scopes to seasons or films - chips when the
        # ask didn't say, silent binding when it did. Runs after the
        # ip-scope normalizer so an already-viewers subject scopes
        # now; while the broad-vs-viewers question is still pending,
        # the scope question chains after that answer instead (see the
        # ip_scope clarify handler).
        _t_scope = time.monotonic()
        try:
            if not spec_draft.get('ask_ip_scope'):
                _H._apply_viewer_scope_guard(spec_draft, text, allow_ask=True)
        except Exception as _vsc_err:
            print(f"[synth-chat interpret] viewer-scope error: {_vsc_err}")
        _C._pm_ask_stage('viewer_scope', t0=_t_scope)

        # Kids-product definition (2026-08-27 Jenna, Toca Boca
        # directive): a product whose end users are predominantly
        # children asks WHO the universe is - the players themselves
        # or the parents who buy - before composing anything. Waits
        # behind a pending ip-scope ask (one question per turn; the
        # ip_scope handler chains this guard after its answer).
        try:
            if not spec_draft.get('ask_ip_scope'):
                _H._apply_product_audience_guard(spec_draft, text,
                                              allow_ask=True)
        except Exception as _pag_err:
            print(f"[synth-chat interpret] product-audience error: "
                  f"{_pag_err}")

        # Sample-lock passthrough (2026-08-19 incidence check): when
        # the user ran a sample check and replied "run it", the
        # frontend resends the ask with the checked sample pinned.
        # Override the interpret step's own guess so the quoted number
        # IS the built number. ensure_messy_sample_size downstream
        # (approve path + Hetzner worker) is idempotent for already-
        # messy values, so these survive the pipeline untouched. The
        # follower-ceiling cap still applies after persona research -
        # a public-metric ceiling always wins over a locked sample.
        try:
            _lk_tu = body.get('locked_sample_tu')
            _lk_av = body.get('locked_sample_avid')
            if _lk_tu:
                _lk_tu = int(_lk_tu)
                if 800 <= _lk_tu <= 9_500_000:
                    spec_draft['subject_raw_tu'] = _lk_tu
                    spec_draft['sample_size_locked'] = True
            if _lk_av:
                _lk_av = int(_lk_av)
                _cur_tu = int(spec_draft.get('subject_raw_tu') or 0)
                if 0 < _lk_av < max(_cur_tu, 9_500_000):
                    spec_draft['subject_raw_avid'] = _lk_av
                    spec_draft['sample_size_locked'] = True
            if spec_draft.get('sample_size_locked'):
                print(f"[synth-chat interpret] sample locked from "
                      f"incidence check: tu="
                      f"{spec_draft.get('subject_raw_tu')} avid="
                      f"{spec_draft.get('subject_raw_avid')}")
        except Exception as _lk_err:
            print(f"[synth-chat interpret] sample-lock error: {_lk_err}")

        # Run-Avid default (2026-08-17 Jenna): TRUE for every fresh-build decision; false only when the
        # model AND the opt-out detector agree, or where run_avid is a no-op (existing_match / derive_cut).
        try:   # links are signals (2026-10-09, item 5): brand input + platform scope from the ask's and the thread's URLs
            from prometheus import links as _links
            _links.apply_to_draft(spec_draft, _links.merge(_links.extract(text), _links.thread_links(history)))
        except Exception:
            traceback.print_exc()
        decision_str = str(spec_draft.get('decision') or '').strip().lower()
        user_optout = _H._user_optout_of_avid(text, chat_history=history)
        claude_says_false = spec_draft.get('run_avid') is False
        if decision_str in _H._AVID_INAPPLICABLE_DECISIONS:
            # existing_match / derive_cut: run_avid is irrelevant.
            # Preserve whatever Claude said (spec ends up ignored
            # downstream anyway) but stop the frontend checkbox from
            # showing false when it doesn't matter.
            spec_draft['run_avid'] = False if decision_str == 'derive_cut' else True
        elif claude_says_false and user_optout:
            # Both agree - honor the opt-out.
            spec_draft['run_avid'] = False
        else:
            # Fresh build without a clear user opt-out - force true.
            # This covers "Claude wrongly set false", "field missing",
            # and "field was true" uniformly.
            spec_draft['run_avid'] = True

        # Semantic bind-or-ask guards (2026-08-25 Jenna: "handle for
        # all of them"): non-default phrasing - exclusions, churn,
        # sequences, fiscal periods, countries, regions, stacked
        # qualifiers, intensity words, client-supplied counts, tier
        # scopes - binds onto the draft with an echo, or asks before
        # anything builds. Runs BEFORE the date gate so a bound
        # quarter/fiscal window marks the range explicit.
        _t_guards = time.monotonic()
        _sg_ask = _H._apply_semantic_guards(
            spec_draft, text, history=history, allow_ask=True,
            decision=decision_str)
        _C._pm_ask_stage('semantic_guards', t0=_t_guards)
        if _sg_ask:
            return jsonify({
                'success': False,
                'guidance': True,
                'error': _sg_ask['question'],
            })
        # A guard may re-route the decision (a window-only 'quarter
        # cut' of an existing profile becomes a window re-read,
        # 2026-10-01); downstream gates read the live value.
        decision_str = str(spec_draft.get('decision')
                           or decision_str or '').strip()

        # Event-scoped window resolution (2026-08-24 Rosie O'Donnell /
        # Jimmy Kimmel Live defect): when the ask ties the audience to
        # a real-world event/stint, the resolved event dates override
        # the default window - on cuts too - and unresolved event dates
        # trigger a clarify ask instead of a silent default.
        _ev_state, _fu_ask = _fur.settle_window_on_host(spec_draft, _fu, _H._apply_event_window_to_draft(
            spec_draft, text, decision=decision_str, history=history), _H, decision_str, _C._pm_ask_hint)   # "after it launched": the day the reader gave through the last full day (2026-11-18)
        if _fu_ask:
            return jsonify(_fu_ask)   # no day given and none resolved: one short question for the date

        # Date-clarification gate. If the user didn't specify a window
        # AND Claude also flagged the range as non-explicit, we return
        # `needs_date_clarification: true` so the frontend can ask
        # rather than silently defaulting. The Partner API `/run`
        # endpoint ignores this flag - it just uses the default range
        # (that's the documented contract). This gate applies to the
        # dashboard chatbot only.
        claude_explicit = bool(spec_draft.get('date_range_explicit'))
        user_explicit = _H._user_specified_dates(text, chat_history=history)
        needs_date_clarification = False   # default window is the rule; the card shows it (2026-10-13 audit)
        # Reuse/derive decisions ship the parent file's window - asking
        # which window to use is noise (2026-08-20 Spider-Man TVOD ask
        # got a window question on a 3-credit derive).
        if decision_str in ('existing_match', 'derive_cut'):
            needs_date_clarification = False
        if _ev_state == 'ask' and decision_str != 'existing_match':
            # Event-scoped but the exact dates aren't confidently
            # known: confirm with the user (with the best-researched
            # candidates) rather than silently defaulting. The user's
            # reply re-enters interpret with the dates in history.
            return jsonify({
                'success': False,
                'guidance': True,
                'error': _H._event_window_question(spec_draft),
                'event_window': spec_draft.get('event_window'),
            })

        proposed_range = _H._proposed_range_from_drafts([spec_draft])
        subject_label = (
            spec_draft.get('subject')
            or spec_draft.get('name')
            or 'this profile'
        )
        # Surface the credit cost so the approval card can show it
        # BEFORE the user clicks Approve. Uses the same _V1_CREDITS
        # table the Partner API uses to charge, so what you see is
        # what you pay. 2026-08-18: Jenna directive.
        try:
            _dec_norm, _, _ = _H._normalize_v1_decision(spec_draft)
        except Exception:
            _dec_norm = str(spec_draft.get('decision') or 'new_build').strip() or 'new_build'
        estimated_credits = int(
            _H._V1_CREDITS.get(_dec_norm, _H.CREDITS_PROFILE_ANALYSIS))
        spec_draft['estimated_credits'] = estimated_credits
        # base_credits anchors the add-on cuts math in the clarify
        # flow: total = base + 3 x cuts. Kept separate so re-answering
        # the cuts question can't compound.
        spec_draft['base_credits'] = estimated_credits
        if _dec_norm == 'existing_match':
            estimated_credits = _H._apply_existing_match_retail_price(
                spec_draft, user,
                session.get('username') or user.get('username') or '')
        spec_draft['estimated_run_minutes'] = _C._estimate_run_minutes(
            _dec_norm, bool(spec_draft.get('run_avid')))
        # Pin the est. sample now so the approval card shows the exact
        # number the build will use. Runs AFTER the locked-sample
        # passthrough above: locked values are already messy, so this
        # is a no-op for them (idempotent helper).
        _C._jitter_draft_est_sample(spec_draft)
        # Subject naming + embedded cuts (2026-08-20 Jenna): strip any
        # demographic qualifier out of the subject - the TU + Avid
        # always build on the FULL universe under the clean entity
        # name, and the qualifier rides as a 3-credit derived cut
        # ('Go-GURT - 18-24'). Runs after base_credits is anchored so
        # the repricing (base + 3 x cuts) sticks.
        _H._decompose_embedded_subject_cuts(spec_draft, text)
        # Persona-universe normalization (2026-09-15): an audience
        # described by demographics + interests gets a clean cohort
        # label with age / income qualifiers riding as cuts. Same
        # treatment on every interpret surface (Prometheus included).
        _H._v1_persona_universe_normalize(spec_draft, text)
        # Multi-cohort recovery (2026-08-20 Jenna, Protein
        # Enthusiasts): a request naming SEVERAL age cohorts of the
        # same base audience is ONE TU + one cut per cohort. The
        # interpreter sometimes collapses them into a single mangled
        # subject - rescan the raw ask and merge any missing cohorts.
        _H._augment_multi_cohort_cuts(spec_draft, text)
        if _platform_hit:
            try:
                _pcuts.apply_to_draft(spec_draft, _platform_hit, text)   # the service cuts + the how-it-is-built note
            except Exception as _fx:
                _faults.note('chat', _fx)
        else:
            try:   # "talk me through how you build it" on a single title audience: the note rides the brief
                from prometheus import platform_cuts as _pcuts2
                _mn = _pcuts2.note_for_title_build(spec_draft, text)
                if _mn:
                    spec_draft['method_note'] = _mn
            except Exception as _fx:
                _faults.note('chat', _fx)
        _H._drop_degenerate_addon_cuts(spec_draft)
        # Cuts-only promoter (2026-08-20): if the cleaned subject
        # already has a full-universe TU in the catalog, skip the
        # rebuild - flip to derive_cut/addon_cuts and charge 3 x cuts.
        try:
            _H._maybe_promote_embedded_cuts_to_parent(spec_draft, catalog)
            _C._pm_promote_date_snapshot_ask(spec_draft, text)
            _dec_norm, _, _ = _H._normalize_v1_decision(spec_draft)
        except Exception as _fx:
            _faults.note('chat', _fx)
        # ECHO RULE (2026-08-24): every cut/build confirmation states
        # its window. Runs AFTER the promoters so a decision flipped to
        # derive_cut/cut_needs_parent still gets the echo, and re-routes
        # the resolved event window onto the final decision's fields.
        if _ev_state == 'confident':
            _H._route_window_fields(
                spec_draft, _dec_norm,
                (spec_draft.get('date_range') or {}).get('start'),
                (spec_draft.get('date_range') or {}).get('end'),
                spec_draft.get('date_window_label') or '',
                (spec_draft.get('date_window_label') or '')
                .split(' (')[0].strip())
        else:
            # Relative-window binding (2026-08-24): 'trailing 60 days'
            # style requests compute concrete dates at interpret time;
            # a confident event window wins when both are present. Then
            # the shared 'Date range:' clause, then the dates written
            # into the ask (2026-11-08, Grace Yin: 'from January 19 and
            # February 15, 2024' is the window, never the default), then
            # the standing default on a draft still without one.
            _H._bind_explicit_windows(spec_draft, text, chat_history=history,
                                      decision=_dec_norm)
        _H._ensure_cut_window_echo(spec_draft, decision=_dec_norm)
        # Subscriber IQ dates/season guard (2026-08-25): researches the
        # real air window, flags a season still in progress as 'Season
        # N to date', surfaces user-date conflicts, and stashes the
        # window confirmation + Profile IQ add-on asks. Runs before the
        # future-window guard so an in-progress season's through-today
        # binding is what that guard sees.
        if _dec_norm == 'subscriber_iq':
            # 2026-10-02 audit: a platform in the title slot ("Pull
            # Subscriber IQ for Starz") researched an air window for a
            # network for 190 to 590 seconds and then failed. Ask for
            # the title at once instead.
            _plat_q = _C._pm_subiq_platform_only_question(spec_draft, text)
            if _plat_q:
                return jsonify(_plat_q)
            _H._apply_subiq_guards(spec_draft, text)
            needs_date_clarification = False
            # Already in the library (2026-10-02): the brief says so
            # before anyone approves a second copy. An explicit pull
            # still builds (a fresh window is a legitimate re-pull).
            try:
                _C._pm_note_existing_subiq_read(spec_draft)
            except Exception:
                traceback.print_exc()
        # Future-window guard (2026-08-25): runs AFTER every window
        # binder so it sees the final dates. Windows that spill past
        # today clamp to today with an echo; entirely-future windows
        # ask instead of building on data that does not exist yet.
        _fw_ask = _H._guard_future_window(spec_draft, decision=_dec_norm,
                                       allow_ask=True)
        if _fw_ask:
            return jsonify({
                'success': False,
                'guidance': True,
                'error': _fw_ask['question'],
            })
        # No data before the data floor (Jenna 2026-11-18, prometheus.data_floor): a window before it never becomes a brief; one across it starts on it and the card says so.
        _df_ask = __import__('prometheus.data_floor', fromlist=['gate']).guard_draft(spec_draft, text, decision=_dec_norm, allow_ask=True, route=_H._route_window_fields)
        if _df_ask:
            return jsonify({'success': False, 'guidance': True, 'error': _df_ask['question'], 'data_floor': 'before', 'followups': [_df_ask['chip']] if _df_ask.get('chip') else []})
        _dec_norm = __import__('prometheus.held_window', fromlist=['settle_on_host']).settle_on_host(spec_draft, text, _H, catalog=catalog, user=user, username=(session.get('username') or ''), est_minutes=_C._estimate_run_minutes) or _dec_norm   # a held window is a reuse (2026-11-16)
        estimated_credits = int(spec_draft.get('estimated_credits')
                                if spec_draft.get('estimated_credits') is not None else estimated_credits)
        # 2026-10-02 audit: model reasoning leaked into the subject
        # ("Starz+ ... The prior turn asked for Starz; this turn asks
        # for Starz+") and a question fragment became a build ("Appeal
        # of the Spiderwick Franchise"). Clean every name field; a
        # subject with nothing usable left asks instead of building.
        try:
            _bad_subj = _C._pm_sanitize_draft_subjects(spec_draft)
        except Exception:
            traceback.print_exc()
            _bad_subj = None
        if _bad_subj:
            return jsonify({'success': False, 'guidance': True,
                            'error': _bad_subj})
        subject_label = (spec_draft.get('subject')
                         or spec_draft.get('name') or subject_label)
        # Guided clarify steps (2026-08-19 Cut Strategist): every fresh
        # single-subject build asks the business goal, then the
        # strategist recommends a cut package (3 credits per cut,
        # every cut derived from the always-national TU + avid base).
        # Derive-cut / existing-match asks skip - no fresh build to
        # scope. 2026-08-20: when the ask looks like a cut of existing
        # data but no parent auto-linked, the parent_link question
        # runs FIRST - linking to a parent reroutes the whole thing to
        # a 3-credit derive and skips the fresh-build scoping.
        clarify_steps = []
        # Suggested cuts retired (Jenna 2026-10-06: "let's take suggested
        # cuts out. they seem to be confusing"). A fresh build goes
        # straight to the brief: the national total universe + avid is
        # the whole audience. Cuts stay available when a user asks for
        # one by name (derive_cut / cut_needs_parent / addon phrasing);
        # the goal question only existed to feed the strategist, so it
        # goes too. The 'goal' / 'strategy' clarify handlers stay for
        # stale clients and finish without recommending anything.
        if spec_draft.get('ask_parent_link') and \
                spec_draft.get('parent_link_candidates'):
            clarify_steps = ['parent_link'] + clarify_steps
        # Age-break question runs before everything else - the subject
        # definition (which ages the cohort covers) shapes every
        # downstream step.
        if spec_draft.get('ask_age_breaks') and \
                spec_draft.get('age_break_data'):
            clarify_steps = ['age_breaks'] + clarify_steps
        # Viewer season/film scope (2026-08-27): which seasons or
        # films the viewers universe covers. Runs after ip_scope
        # (broad vs viewers decides whether it applies at all) and
        # before every other refinement.
        if spec_draft.get('ask_viewer_scope') and \
                spec_draft.get('viewer_scope_data'):
            clarify_steps = ['viewer_scope'] + clarify_steps
        # Kids-title definition (2026-08-27 Paw Patrol directive):
        # actual under-18 viewers vs parents of the viewers. WHO the
        # universe is comes before WHICH seasons it covers, so this
        # prepends ahead of viewer_scope (one question per turn).
        if spec_draft.get('ask_viewer_audience') and \
                spec_draft.get('viewer_audience_data'):
            clarify_steps = ['viewer_audience'] + clarify_steps
        # IP-scope question runs FIRST - broad engagers vs consumers
        # defines the universe itself (pins, naming, sample) before
        # any other refinement makes sense.
        if spec_draft.get('ask_ip_scope'):
            clarify_steps = ['ip_scope'] + clarify_steps
        # Existing-profile question (2026-08-24 SHARKNINJA directive):
        # the ask names an entity we already have - confirm use vs
        # fresh pull before anything else is scoped.
        if spec_draft.get('ask_existing_profile') and \
                spec_draft.get('existing_profile_data'):
            clarify_steps = ['existing_profile'] + clarify_steps
        # Qualifier-match question (2026-08-25): the closest existing
        # profile is the same brand but a different universe scope -
        # 'We have X, is that what you meant?' runs before any
        # fresh-build scoping.
        if spec_draft.get('ask_qualifier_match') and \
                spec_draft.get('qualifier_match_data'):
            clarify_steps = ['qualifier_match'] + clarify_steps
        # Identity confirmation runs before EVERYTHING - what the
        # subject even IS defines every downstream step (2026-08-24
        # Furious defect).
        if spec_draft.get('ask_identity') and \
                spec_draft.get('identity_data'):
            clarify_steps = ['identity'] + clarify_steps
        # Subscriber IQ flow (2026-08-25): its own two-step clarify
        # (window confirmation, then the Profile IQ add-on offer)
        # replaces the fresh-build scoping steps entirely. Identity
        # still leads when stashed.
        if _dec_norm == 'subscriber_iq':
            clarify_steps = []
            if spec_draft.get('ask_identity') and \
                    spec_draft.get('identity_data'):
                clarify_steps.append('identity')
            if spec_draft.get('ask_subiq_window'):
                clarify_steps.append('subiq_window')
            if spec_draft.get('ask_subiq_upsell'):
                clarify_steps.append('subiq_upsell')
        # Ambiguous churn fork (2026-08-26 Jenna): a churn / retention
        # ask with no title event leads with the two-chip question -
        # the churn and cancellation read, or an audience profile of
        # the churned subscribers - before every other step. The pick
        # re-enters the flow as its own request, so downstream steps
        # queued here never run.
        if spec_draft.get('ask_subiq_or_profile') and \
                spec_draft.get('subiq_or_profile_data') is not None:
            clarify_steps = ['subiq_or_profile'] + [
                s for s in clarify_steps if s != 'subiq_or_profile']
        # Estimated audience band on the approval brief (2026-08-24
        # Jenna): same helper the Partner API quotes from, derived from
        # the exact sample pinned on this draft, so the brief, the
        # partner surfaces, and the delivered file all agree.
        _t_estimate = time.monotonic()
        try:
            __import__('migration.saved_subject_size', fromlist=['apply_to_draft']).apply_to_draft(spec_draft, text, history=history, decision=_dec_norm)   # a saved audience size starts a fresh build (2026-11-20): the brief quotes it; a size the reader wrote stays theirs
            _est_rng = __import__('prometheus.brief_size_hold', fromlist=['ranged']).ranged(_H._estimated_audience_range, spec_draft,   # a one-service order is sized by the rule before the brief quotes it (2026-11-11); the first size held for a subject and window stands (2026-11-18)
                __import__('migration.service_scope_sizing', fromlist=['apply_to_draft']).apply_to_draft(spec_draft).get('subject_raw_tu'))
            if _est_rng and _dec_norm in ('new_build',
                                          'time_shifted_refresh',
                                          'cut_needs_parent'):
                spec_draft['estimated_audience_low'] = _est_rng['low']
                spec_draft['estimated_audience_high'] = _est_rng['high']
                spec_draft['estimated_audience_sentence'] = \
                    _H._estimated_audience_sentence(_est_rng)
            elif _dec_norm == 'derive_cut' \
                    and str(spec_draft.get('derive_type')
                            or '').strip().lower() == 'avid' \
                    and spec_draft.get('existing_match_s3_key'):
                # Avid cut off an existing parent (2026-08-24): the
                # parent carries its own avid share row, which is the
                # exact fraction the build sizes the cut from - quote
                # the band from it so the estimate matches delivery.
                # Parents without the row skip the chip, same as
                # before.
                _av_est = _H._parent_avid_estimate(
                    spec_draft.get('existing_match_s3_key'))
                _av_rng = _H._estimated_audience_range(_av_est) \
                    if _av_est else None
                if _av_rng:
                    spec_draft['estimated_audience_low'] = _av_rng['low']
                    spec_draft['estimated_audience_high'] = _av_rng['high']
                    spec_draft['estimated_audience_sentence'] = \
                        _H._estimated_audience_sentence(_av_rng)
        except Exception as _est_err:
            print(f"[synth-chat interpret] estimate range skipped: "
                  f"{_est_err}")
        _C._pm_ask_stage('estimate', t0=_t_estimate)
        # Age from the immutable filename stamp + em/en dash scrub on
        # every model-authored prose field the widget renders
        # (2026-08-25: interpret assumptions shipped em dashes onto
        # the brief card).
        try:
            _H._stamp_existing_match_age(spec_draft)
        except Exception as _fx:
            _faults.note('chat', _fx)
        try:
            _H._scrub_draft_prose_dashes(spec_draft)
        except Exception as _fx:
            _faults.note('chat', _fx)
        # Thread the user's exact natural-language ask onto the draft so
        # it round-trips back on approve and lands on the queued job. If
        # the build later fails, the ops failure email can quote what the
        # user actually asked. Best-effort; never blocks the response.
        try:
            if isinstance(spec_draft, dict) and text:
                spec_draft['user_prompt'] = str(text)[:4000]
        except Exception as _fx:
            _faults.note('chat', _fx)
        # Chip pair for existing_match (2026-09-04, Jenna verbatim: "it
        # shouldnt just say is this the one you want yes or no and then
        # you click the chip and if yes it would have retunred that one
        # and if I said no it would run the one I asked"). Two chips
        # replace the free-text "approve or send another message"
        # nudge, one click each:
        #   Yes, use this        -> hand back the matched existing file
        #                           at 0 credits (the current approve
        #                           path for a decision=existing_match
        #                           draft)
        #   No, run what I asked -> flip the draft to a fresh new_build
        #                           and take the normal build path
        #                           (session-only force_new_build flag
        #                           on /api/brief-chat/approve; the
        #                           partner API `/api/v1/*` never sees
        #                           these fields, per
        #                           no-external-overrides.mdc: chatbot
        #                           chips are a user-driven session
        #                           flow, not a wire-protocol override).
        # Existing_match-only. Other verdicts already have their own
        # approve/adjust chip flows and stay unchanged.
        _chip_options = None
        _chip_targets = None
        if _dec_norm == 'existing_match':
            estimated_credits = _H._apply_existing_match_retail_price(
                spec_draft, user,
                session.get('username') or user.get('username') or '')
            _fresh_new_credits = int(_H._V1_CREDITS.get(
                'new_build', _H.CREDITS_PROFILE_ANALYSIS))
            _chip_options = [
                {'id': 'use_existing', 'label': 'Yes, use this'},
                {'id': 'run_new', 'label': 'No, run what I asked'},
            ]
            _reuse_cr = int(spec_draft.get('estimated_credits') or 0)
            _chip_targets = {
                'use_existing': {
                    'action': 'approve',
                    'endpoint': '/api/brief-chat/approve',
                    'credits': _reuse_cr,
                },
                'run_new': {
                    'action': 'approve',
                    'endpoint': '/api/brief-chat/approve',
                    'credits': _fresh_new_credits,
                    'force_new_build': True,
                },
            }
            # Surface the fresh-build cost so the cost line can read
            # "0 credits to reuse, or N credits for a fresh build"
            # instead of just "0 credits (reusing the existing file)".
            try:
                spec_draft['estimated_credits_new_build'] = _fresh_new_credits
            except Exception as _fx:
                _faults.note('chat', _fx)
        # Short-name disambiguation (2026-11-04, EJAE): when more than
        # one public figure carries the name, the card names the one
        # picked and this chip opens the picker for the others.
        if _chip_options is None:
            try:
                from prometheus import short_name_identity as _sni_chip
                _chip_options = _sni_chip.chip_options(spec_draft)
                if _chip_options:
                    _chip_targets = {'identity_other': {
                        'action': 'identity_picker'}}
            except Exception as _fx:
                _faults.note('chat', _fx)
        if _order_notes and isinstance(spec_draft, dict):
            spec_draft['deliverable_notes'] = str(_order_notes)[:600]
        # Orders beat chips (Grace Yin 2026-11-10, prometheus.orders): an
        # explicit order on an unlimited seat queues the brief in this same
        # turn, no approve chip, when nothing is left to ask (no clarify
        # step, no identity picker, a fresh build). The reply is the build
        # line plus what the file carries. A paying seat still reads the
        # brief and approves; a probe never queues.
        try:
            _auto = _C._pm_auto_start_order(user, spec_draft, _order_text, _dec_norm, clarify_steps, _chip_options, body)
        except Exception as _fx:
            _faults.note('chat', _fx)
            _auto = None
        if _auto is not None:
            return _auto
        spec_draft['price_table'] = _ptab = _oq.table_for_draft(_H, None, spec_draft, _C._pm_price_table())  # money, not credits; its quarter reads inside it
        return jsonify({
            'success': True,
            'spec_draft': spec_draft,
            'estimated_credits': estimated_credits,
            'price_table': _ptab,
            'clarify_steps': clarify_steps,
            'candidates': [
                {k: v for k, v in c.items() if not k.startswith('_') or k == '_score'}
                for c in candidates
            ],
            'model': result.get('model'),
            'needs_date_clarification': needs_date_clarification,
            'proposed_date_range': proposed_range,
            # Grounded clarify (2026-08-27, Jenna): the window question
            # leads with the user's last-used custom window as a chip
            # when their memory holds one; None keeps current wording.
            'memory_window': (_C._pm_memory_last_window()
                              if needs_date_clarification else None),
            'subject_label': subject_label,
            # Plain-language window this build/cut will run with
            # (ECHO RULE 2026-08-24). '' only for existing_match.
            'date_window': _H._draft_window_field(spec_draft, _dec_norm),
            # Chip pair for existing_match. None on every other verdict
            # so the frontend can no-op cleanly.
            'chip_options': _chip_options,
            'chip_targets': _chip_targets,
        })
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/interpret', e)
        return jsonify(_H._chatbot_calm_payload())


__all__ = ['api_synth_chat_interpret', '_pm_auto_start_order', '_pm_interpret_core']
