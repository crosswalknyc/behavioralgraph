"""Page analysis over the open profile: the ask log decorator, the analyze route and its core.

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
import uuid
import traceback
import re
from prometheus import smarter as _smart
from prometheus import figures as _fig
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from prometheus import subiq_library as _subiq_library
from prometheus import digital_twin_lane as _twin_lane
from prometheus import bpiq_wizard as _bpiq_wiz
import time
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared
from prometheus.ask_outcome import (  # noqa: E402,F401
    _ask_infer_route_outcome, _ASK_SUBJECT_VIEWS, _ASK_SUBJ_STOP, _ask_mentions_subject)
from prometheus import kpi_definitions as _kpi
from prometheus import named_subject as _ns_mod
from prometheus import read_recovery as _rr

# <<moved>>
# <<block: 1>>
def _ask_logged(surface):
    """Wrap a chatbot route so every question is recorded to the ask
    log with route, outcome, and response time. Fire-and-forget."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from flask import g as _g
            t0 = time.time()
            question, view, mode = '', '', None
            log_surface = surface
            ask_history = []
            # One trace id per ask (2026-10-06, observability): on the
            # ask-log record, in the reply, and on the queue job, so a
            # user's question can be followed from widget to worker.
            try:
                _g._pm_trace_id = (str(request.headers.get('X-Prometheus-Trace') or '').strip()[:32]
                                   or uuid.uuid4().hex[:12])
            except Exception as _fx:
                _faults.note('chat', _fx)
            try:
                body = request.get_json(force=True, silent=True) or {}
                question = str(body.get('text') or '').strip()
                _g._pm_ask_text = question   # the exit scrub reads the ask (I5)
                ask_history = body.get('history') or []
                mode = (str(body.get('mode') or '').strip().lower()
                        or None)
                pc = body.get('page_context') or {}
                _g._pm_page_ctx = pc if isinstance(pc, dict) else {}   # the answer gate's calm hold names the page (2026-10-21)
                vc = (pc.get('view_context') or {}) \
                    if isinstance(pc, dict) else {}
                view = str(vc.get('view_id') or '').strip()
                if not view and isinstance(pc, dict) and pc.get('primary'):
                    view = 'profileIQ'
                __import__('prometheus.service', fromlist=['stash_binding']).stash_binding(_C._pm_ask_log_user(''), body.get('thread_id'))   # the thread's project rides the request (2026-10-21)
            except Exception as _fx:
                _faults.note('chat', _fx)
            # What the user did next (2026-10-02): a repeat of the
            # prior ask or a push-back phrase grades the PRIOR reply
            # failed. Computed before the handler runs so a route can
            # read g._pm_user_signal and stop replaying the same
            # answer (S5); recorded on this ask under extra.
            user_sig = None
            try:
                from prometheus import user_signal as _us
                user_sig = _us.detect(question, ask_history, body)   # an approval of a quote is a yes, never a repeat
                _g._pm_user_signal = user_sig
            except Exception:
                user_sig = None
            try:
                try:
                    resp = fn(*args, **kwargs)
                except Exception as _first:
                    # I4b (2026-10-20): most of a day's faults were binding
                    # failures; one retry with the binder's subject before
                    # the calm line, then the ops mail carries the subject.
                    resp = __import__('prometheus.faults', fromlist=['retry_once']).retry_once(fn, args, kwargs, question, ask_history, _first)
            except Exception:
                # 2026-09-30 (Jenna: "make sure they are all being
                # emailed to me with the replies so I can troubleshoot
                # in real time"): a route that died mid-request used to
                # skip the ask log AND the question email, leaving only
                # the ops failure note. Record and email the ask first,
                # then re-raise so the calm guard still answers the
                # user with the working-on-it promise.
                if question:
                    try:
                        import render_usage_log as _rul
                        _rul.record_ask(
                            user=_C._pm_ask_log_user(),
                            view=view, question=question,
                            surface=log_surface, route='unknown',
                            outcome='faulted',   # I4b: the calm line is a fault, never 'answered'
                            ms=int((time.time() - t0) * 1000),
                            mode=mode, subject=getattr(_g, '_pm_retry_bound', None) or None,
                            extra={'retried': bool(getattr(_g, '_pm_retried', False))},
                            stages=getattr(_g, '_pm_ask_stages', None))
                    except Exception as _fx:
                        _faults.note('chat', _fx)
                    try:
                        _C._pm_watch_notify(
                            _C._pm_ask_log_user(''), question,
                            {'reply': _H._CHATBOT_CALM_MESSAGE}, None)
                    except Exception as _fx:
                        _faults.note('chat', _fx)
                raise
            try:
                if not question:
                    return resp
                status_code, payload = 200, None
                actual = resp
                if isinstance(resp, tuple) and resp:
                    actual = resp[0]
                    if len(resp) > 1 and isinstance(resp[1], int):
                        status_code = resp[1]
                try:
                    payload = actual.get_json(silent=True)
                except Exception:
                    payload = None
                # Prometheus v1 envelope (2026-10-01): the one-door
                # route answers {kind, surface, raw}. The log keeps
                # the legacy surface names and reads the legacy body,
                # so the weekly reviews and the per-ask notify see
                # exactly what they saw before.
                _env_text = ''
                if isinstance(payload, dict) \
                        and isinstance(payload.get('raw'), dict) \
                        and payload.get('surface') in ('interpret',
                                                       'analyze', 'deck'):
                    log_surface = payload['surface']
                    _env_text = str(payload.get('text') or '')   # the reader's line for a job / draft (2026-10-13)
                    payload = payload['raw']
                # partner contract (2026-10-14): a v1 response is logged through its legacy view, never rewritten by the gate
                _v1_api, log_surface, _env_text, payload = __import__('prometheus.contract_v1', fromlist=['log_view']).log_view(payload, log_surface, _env_text)
                route, outcome, subject = _ask_infer_route_outcome(
                    log_surface, payload, status_code)
                route = __import__('prometheus.ask_outcome', fromlist=['route_pick']).route_pick(getattr(_g, '_pm_ask_route', None), route)  # door placeholder yields to the body's route (2026-10-13)
                outcome = getattr(_g, '_pm_ask_outcome', None) or outcome
                subject = getattr(_g, '_pm_ask_subject', None) or subject
                mode = getattr(_g, '_pm_ask_mode', None) or mode
                # Do not record a subject the view cannot own
                # (2026-09-14). Week 2026-W37 filed three Trends IQ
                # asks about DNC news coverage under subject 'shark
                # tank' and two about MMA under 'the twilight saga',
                # because the field held the last profile the reader
                # opened. The weekly review then grouped them and
                # proposed teaching replay on those pairs, which would
                # have served a Shark Tank read to a news question.
                # An ask that names the subject keeps it, and an
                # unknown view keeps it rather than lose real data.
                if subject and view and view not in _ASK_SUBJECT_VIEWS \
                        and not _ask_mentions_subject(question, subject):
                    subject = None
                # Failure taxonomy (2026-10-02 RCA): classify what the
                # user actually received. Legacy outcome names are kept
                # for answers and build decisions; the states that used
                # to hide under 'answered' (empty, faulted, clarified,
                # clarified_repeat, proposed, confirmed) replace it.
                outcome, extra = _C._pm_ask_apply_taxonomy(
                    log_surface, payload, status_code, ask_history,
                    outcome, question,
                    _C._pm_ask_log_user(''))
                # Answer gate (2026-10-02 RCA): the only exit. A held
                # reply is retried once, then replaced with the honest
                # email promise; a build card for a task becomes the
                # which-audience question.
                if not _v1_api:
                    _t_exit = time.monotonic()
                    resp, payload, status_code, outcome, extra = \
                        _C._pm_answer_gate(
                            fn, args, kwargs, resp, payload, status_code,
                            log_surface, outcome, extra, question,
                            ask_history,
                            _C._pm_ask_log_user(''),
                            t0)
                    _C._pm_ask_stage('exit_gate', t0=_t_exit)   # the exit's own time, so the ask log accounts for it (2026-11-24)
                if user_sig:
                    merged = {'user_signal': user_sig.get('signal'),
                              'rejects': str(user_sig.get('prior_question')
                                             or '')[:120]}
                    for k, v in (extra or {}).items():
                        merged.setdefault(k, v)
                    extra = merged
                try:
                    _tid_trace = getattr(_g, '_pm_trace_id', '')
                    if _tid_trace:
                        extra = dict(extra or {})
                        extra['trace_id'] = _tid_trace
                    extra = __import__('prometheus.understanding', fromlist=['stamp_log']).stamp_log(extra, _g)   # the Reader's reading and the Understanding beside the route (rebuild stage 2 shadow, 2026-11-20)
                    _tb = getattr(_g, '_pm_thread_binding', None); (_tb or getattr(_g, '_pm_thread_id', None)) and extra.update({'thread': dict((_tb or {}), id=getattr(_g, '_pm_thread_id', None))})   # the project the thread follows (2026-10-21)
                    _ao = getattr(_g, '_pm_approved_offer', None); _ao and extra.update({'approved_offer': _ao})   # a typed approval bound to a pending priced offer (2026-10-22)
                except Exception as _fx:
                    _faults.note('chat', _fx)
                extra = __import__('prometheus.order_stall', fromlist=['stamp']).stamp(extra, question, payload, ask_history)   # where a priced order stands after this reply (Jenna 2026-11-11)
                import render_usage_log as _rul
                _rul.record_ask(
                    user=_C._pm_ask_log_user(),
                    view=view, question=question, surface=log_surface,
                    route=route, outcome=outcome,
                    ms=int((time.time() - t0) * 1000),
                    mode=mode, subject=subject, extra=extra,
                    stages=getattr(_g, '_pm_ask_stages', None),
                    reply=__import__('prometheus.ask_outcome', fromlist=['reply_text']).reply_text(payload) or _env_text,
                    job_id=__import__('prometheus.ask_outcome', fromlist=['promised_job']).promised_job(payload))
                _C._pm_watch_flag(_C._pm_ask_log_user(), question, route, outcome, extra)
                # A key ask is notified once, at the API door, with the
                # text the caller actually received (the v1 doc can turn an
                # empty reply into a decline after this point; 2026-10-21
                # the copy read 'No written answer' while the partner read
                # 'That one did not land'). Dashboard asks notify here.
                if not getattr(_g, '_pm_api_key_owner', None):
                    _C._pm_watch_notify(
                        _C._pm_ask_log_user(''), question, payload,
                        subject, surface=log_surface)
                # Cross-session memory (2026-08-27, Jenna): every ask
                # that resolved a subject feeds the per-user memory,
                # unless the handler already recorded it with richer
                # detail (cohort + ledger key on the analyze path).
                try:
                    if subject and not getattr(_g, '_pm_mem_recorded',
                                               False) \
                            and outcome not in ('error', 'clarify',
                                                'memory_confirm'):
                        import prometheus_memory as _pmm
                        _pmm.remember(
                            (_C._pm_ask_log_user('')).strip(),
                            question, subject=subject, view=view,
                            route=route)
                except Exception as _fx:
                    _faults.note('chat', _fx)
            except Exception:
                traceback.print_exc()
            return resp
        return wrapper
    return deco


# <<block: 2>>
@_H.app.route('/api/brief-chat/analyze', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/analyze')
@_ask_logged('analyze')
def api_synth_chat_analyze():
    """Analyze the data open on the dashboard (primary profile + any
    checked Data Cuts). First-party digest is the primary evidence.

    Session-authenticated dashboard users only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): analyze is the primary
    # read surface; 'pull'-only users are blocked here. Runs before
    # the JSON body parse so an empty pull-only request also gets the
    # friendly refusal instead of the calm-fallback line.
    if not _C._pm_gate_analyze(user):
        return _C._pm_gate_refusal('analyze')
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/analyze', e)
        return jsonify(_H._chatbot_calm_payload())
    text = (body.get('text') or '').strip()
    if not text:
        _H._chatbot_error_email('brief-chat/analyze',
                             'empty request text from chat UI',
                             tb='(request validation)')
        return jsonify(_H._chatbot_calm_payload())
    history = body.get('history') or []
    return _C._pm_analyze_core(user, body, text, history)


# <<block: 3>>
def _pm_analyze_core(user, body, text, history):
    """The analyze surface for an already-gated user.

    Split out of ``api_synth_chat_analyze`` (2026-10-01, Prometheus
    Phase 1) so the same body runs for a session caller on the legacy
    route and for any caller of ``prometheus.service.ask``. The mode
    gate and body parse stay with the callers; the funds, tier, and
    usage gates below are part of the surface and run for everyone.
    Returns a Flask response exactly as before.
    """
    _C._pm_req_thread_set(body)
    # Pricing questions (2026-09-23 Jenna): the flat rate card, served
    # before the tier and funds gates - a drained account asking what
    # things cost gets the answer, free, no model call.
    if _C._pm_pricing_question(text):
        _C._pm_ask_hint(route='pricing_fact', outcome='answered')
        return jsonify({'success': True, 'reply': _C._pm_pricing_copy(_C._pm_ask_log_user(''), user)})
    # Challenged-number heads-up (2026-09-30): fire-and-continue;
    # the answer path is untouched.
    _C._pm_challenge_headsup(user, text)
    # Work-order verbs (2026-09-30 Jenna): status / ETA / cancel
    # answer from the caller's own runs, free, before any gate.
    _wo_intent = _C._pm_workorder_intent(text)
    if _wo_intent:
        _wo_reply = _C._pm_workorder_reply(user, text, _wo_intent)
        if _wo_reply:
            try:
                _C._pm_ask_hint(outcome='workorder_' + _wo_intent)
            except Exception as _fx:
                _faults.note('chat', _fx)
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': _wo_reply, 'followups': [],
                'offer_deck': False, 'deck_angle': None})
    # Typed approval with no brief on screen (2026-10-02 S3 lanes):
    # bare "approved" / "run it" on a dashboard view is not an
    # analysis ask. Skipped when this chat just asked a question, so
    # a reply to that question still lands where it belongs.
    if _C._pm_approval_word(text) and not _C._pm_last_agent_asked(history) \
            and not _smart.rearm_brief_from_thread(body, history, user, _C._pm_usage_extras(user), _C._PM_INTAKE_UI, _C._pm_intake_turn):
        _C._pm_ask_hint(route='command', outcome='approve_without_draft')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _C._PM_APPROVE_NO_DRAFT_COPY, 'followups': [],
            'offer_deck': False, 'deck_angle': None})
    # KPI definition lookup (2026-10-02 S4): "how is penetration
    # calculated?" / "what counts as an avid fan?" answer from the
    # house glossary, free, no model call. Only when the ask names a
    # KPI the glossary carries; anything else falls through with the
    # view's definitions riding the prompt (see the prompt assembly).
    _kpi_defn = _C._pm_kpi_definition_for(text, body)
    if _kpi_defn:
        _C._pm_ask_hint(route='kpi_definition', outcome='answered',
                     subject=_kpi_defn['label'])
        # every term the ask names, in order ("share vs penetration" is two entries, 2026-11-23)
        try:
            _kpi_vid = _C._pm_kpi_view(body)[0]
            _kpi_entries = _kpi.find_definitions(text, _kpi_vid) or [_kpi_defn]
            _kpi_reply = _kpi.definitions_reply(_kpi_entries, _kpi_vid) or _kpi.definition_reply(_kpi_defn)
        except Exception:
            _kpi_reply = _kpi.definition_reply(_kpi_defn)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _kpi_reply, 'followups': [],
            'offer_deck': False, 'deck_angle': None})
    _wf = _kpi.which_figure_payload(text, _C._pm_kpi_view(body)[1])  # deictic: ask which figure
    if _wf:
        _C._pm_ask_hint(route='kpi_which_figure', outcome='clarified')
        return jsonify(_wf)
    # Sample size lane (2026-10-05, Jenna: "the answer will always be
    # 10 million us gen pop panel ... the panel size is always that
    # 10m"). Scott's ask was parsed as a subject and offered a build.
    try:
        import prometheus_analysis as _pma_ss
        if _pma_ss.is_sample_size_ask(text):
            _C._pm_ask_hint(route='sample_size', outcome='answered')
            return jsonify({
                'success': True, 'action': 'answer', 'product_fact': True,   # the one reply that keeps 'ten million' (I5)
                'reply': _pma_ss.sample_size_reply(text), 'followups': [],
                'offer_deck': False, 'deck_angle': None})
    except Exception:
        traceback.print_exc()

    # Box office lane (2026-10-05, Jenna: "I dont want to get in the
    # habit of predicting box office ever ... no matter how hard the
    # user pushes we just keep saying we do not predict box office
    # performance all we can do is tell you how many people went to
    # the ticketing site"). Deterministic, no model call, same words
    # on every push; the count comes off the open ticketing read.
    try:
        import prometheus_analysis as _pma_bo
        _bo_ctx = body.get('page_context') or {}
        if _pma_bo.is_box_office_ask(text, _bo_ctx.get('view_context')):
            _bo_reply = _pma_bo.box_office_reply(
                text, view_context=_bo_ctx.get('view_context'),
                subject_hint=str(((_bo_ctx.get('primary') or {})
                                  .get('name')) or ''))
            _C._pm_ask_hint(route='box_office', outcome='answered')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': _bo_reply, 'followups': [],
                'offer_deck': False, 'deck_angle': None})
    except Exception:
        traceback.print_exc()
    # Prometheus tier gate (2026-08-26): pulls_only users without the
    # pay-as-you-go opt-in get Jenna's offer instead of any analysis
    # flow. Runs before every branch so no analysis path leaks.
    _gate_resp = _C._pm_access_gate(user)
    if _gate_resp is not None:
        return _gate_resp
    # Funds gate (2026-09-16, Jenna): no credits, no balance, not
    # unlimited = no metered usage. Runs before any replay or model
    # call so a drained account never accrues usage it cannot cover.
    _funds_resp = _C._pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    # Pay-as-you-go attribution (None for subscribed users): rides
    # every model call this request makes, and switches billing from
    # credits to per-session dollar usage.
    _pm_ppu = _C._pm_usage_extras(user)
    # Email the last delivered file (2026-09-29 Jenna: every data
    # answer creates a CSV the user can download or have emailed).
    # Runs before routing so an open profile never hijacks it.
    _ins_resp = __import__('prometheus.insight_ask', fromlist=['core_hook']).core_hook(text, body, history, user, _pm_ppu, _C.globals())   # an open-ended insight request on a subject we hold is read from the whole file (Jenna 2026-11-19)
    if _ins_resp is not None:
        return _ins_resp
    if _C._pm_email_file_intent(text):
        return _C._pm_email_file_response(user, text, history)
    # Brand Partnership Valuation wizard (Jenna 2026-10-28): the chip
    # opens step 1 before any other analyze lane, including twins.
    _bpiq_user = (session.get('username') or user.get('username')
                  or '').strip()
    _bpiq_store = _bpiq_wiz.store_for(body, _C.globals())
    _bpiq_tid = str((body or {}).get('thread_id') or '')
    if _bpiq_wiz.wants(text, body, history, store=_bpiq_store,
                       user=_bpiq_user, thread_id=_bpiq_tid):
        _bpiq_resp = _bpiq_wiz.analyze_hook(
            text, body, history, user, _pm_ppu, _bpiq_user, _C.globals(),
            first=_bpiq_wiz.is_start(text), store=_bpiq_store)
        if _bpiq_resp is None:
            _bpiq_resp = _bpiq_wiz.hold_payload(
                history, _bpiq_store, _bpiq_user, _bpiq_tid)
        if _bpiq_resp is not None:
            _bpiq_resp = dict(_bpiq_resp)
            _bpiq_resp.pop('read_job_id', None)
            _bpiq_resp.pop('job_id', None)
            return jsonify(_bpiq_resp)
    # ---- Multi-year trend asks (2026-09-22, Jenna: "it should have
    # forced him to pay for profiles to be run on Will and Grace for
    # the past 4-5 years so it could then compare") ----
    # A '4-5 year analysis' has no honest answer from one single-window
    # file: the reply routes to a paid year-build package instead of a
    # generated read that fabricates the missing years. Fires only on
    # plain text asks - guided-flow confirms pass through untouched.
    if not any(body.get(k) for k in (
            'bpiq_confirm', 'bpiq_inputs', 'jiq_confirm', 'jiq_inputs',
            'fw_confirm', 'fw_inputs', 'aiq_confirm', 'aiq_inputs',
            'twin_confirm', 'twin_inputs', 'panel_confirm')):
        try:
            import prometheus_analysis as pma
            _my_years = _C._pm_detect_multi_year_ask(text)
            if _my_years:
                _my_ctx = body.get('page_context') or {}
                _my_subj = str(((_my_ctx.get('primary') or {})
                                .get('name')) or '').strip() \
                    or (pma.guess_subject_from_text(text) or '').strip()
                if _my_subj and not __import__('prometheus.named_title', fromlist=['other_than']).other_than(text, _my_subj):   # never the open profile's year package for a title the words name (2026-11-19)
                    _have_y, _miss_y = _C._pm_year_files_for_subject(
                        _my_subj, _my_years)
                    if _miss_y:
                        _my_reply, _my_chips = _C._pm_year_package_reply(
                            _my_subj, _my_years, _have_y, _miss_y)
                        _C._pm_ask_hint(outcome='year_package_offer',
                                     subject=_my_subj)
                        return jsonify({
                            'success': True, 'action': 'answer',
                            'reply': _my_reply,
                            'followups': _my_chips,
                            'offer_deck': False, 'deck_angle': None,
                            'build_required': True})
                    elif len(_have_y) >= 2:
                        # Every year is on the shelf (2026-09-30):
                        # answer the series deterministically from the
                        # year files - same ask, same route, same
                        # numbers, every time.
                        _tj = _C._pm_trajectory_reply(
                            (session.get('username')
                             or user.get('username') or ''),
                            _my_subj, _have_y, text)
                        if _tj:
                            _C._pm_ask_hint(outcome='answered',
                                         subject=_my_subj)
                            return jsonify(_tj)
        except Exception:
            traceback.print_exc()
    # The way back to an open intake (prometheus.intake_guard, Jenna 2026-11-05). A question typed while a
    # guided flow waited was answered on its own, with 'Back to the flywheel' (the journey, the campaign
    # setup, the twin, the valuation) on the reply. That chip, or 'back to it', re-asks the open step.
    try:
        from prometheus import intake_guard as _ig_resume
        if _ig_resume.is_resume(text):
            _rflow = _ig_resume.resume_flow(text, history)
            if _rflow == 'flywheel':
                _r = _C._pm_intake_turn('flywheel', text, dict(body, fw_inputs=True), history, user, _pm_ppu, first=False)
                if _r is not None:
                    return jsonify(_r)
            elif _rflow == 'brand_partnership':
                return jsonify(_bpiq_wiz.hold_payload(history, _bpiq_wiz.store_for(body, _C.globals()), _bpiq_user,
                                                      str(body.get('thread_id') or '')))
            elif _rflow in ('digital_journey', 'attribution', 'digital_twin'):
                return jsonify(_C._pm_intake_resume_reply(_rflow, history))
    except Exception as _rx:
        _faults.note('chat', _rx)
    _twin_resp = _twin_lane.analyze_hook(text, body, history, user, _pm_ppu, _bpiq_user, _C.globals())   # Digital Twins IQ (2026-10-26): intake, labs, Focus Group IQ, catalog, status, directions
    if _twin_resp is not None:
        return jsonify(_twin_resp)
    # Bulk Profile IQ pulls from an uploaded list (Jenna 2026-11-05): a
    # names sheet on the thread plus pull words reads the list back with
    # the price; 'Queue the list' writes the ledger the build server's
    # feeder walks. 'How is my list going' answers from the ledger.
    try:
        from prometheus import bulk_pull as _bulk
        from prometheus import pending_intake as _bulk_pi

        def _bulk_pending(op, usr, flow, *args):
            if op in ('peek', 'clear'):
                _bu = str((usr or {}).get('username') or (usr or {}).get('email') or '').strip() if isinstance(usr, dict) else str(usr or '')
                return _bulk_pi.peek(_H.s3_client, _H.S3_BUCKET, _bu, flow) if op == 'peek' else _bulk_pi.clear(_H.s3_client, _H.S3_BUCKET, _bu, flow)
            return _C._pm_pending_intake(op, usr, flow, *args)
        _bulk_resp = _bulk.analyze_hook(text, body, history, user, _pm_ppu, _bpiq_user, host=_H, pending=_bulk_pending)
        if _bulk_resp is not None:
            return jsonify(_bulk_resp)
    except Exception as _bulk_err:
        _faults.note('chat', _bulk_err)
    # Mid-wizard answers whose start phrase already passed still land
    # here when a leftover S3 draft is the only signal.
    _bpiq_late = _bpiq_wiz.analyze_hook(
        text, body, history, user, _pm_ppu, _bpiq_user, _C.globals())
    if _bpiq_late is not None:
        return jsonify(_bpiq_late)
    # ---- Attribution IQ tracking flow (Jenna 2026-09-22) ----
    _aiq_confirm = body.get('aiq_confirm') or _C._pm_pending_intake('pick', user, 'attribution', text)
    if isinstance(_aiq_confirm, dict) \
            and _aiq_confirm.get('campaign_name'):
        _aname = str(_aiq_confirm['campaign_name'])
        _adays = _C._pm_aiq_days(_aiq_confirm)
        _is_add = bool(_aiq_confirm.get('add_to_slug'))   # assets joining a live campaign: the day's read, not a setup
        _atotal = _C._PM_AIQ_DAILY_CREDITS if _is_add else _C._PM_AIQ_SETUP_CREDITS + _C._PM_AIQ_DAILY_CREDITS * _adays
        _adesc = (f"Attribution IQ: {_aname} (added {len(_aiq_confirm.get('urls') or [])} assets, the day's read)" if _is_add else
                  f'Attribution IQ: {_aname} (setup'
                  + (f' + {_adays} prepaid daily refreshes through '
                     f'{_aiq_confirm.get("end_tracking_date")}'
                     if _adays else '') + ')')
        _aiq_job = uuid.uuid4().hex[:12]
        if not _H.consume_credit(
                _bpiq_user, description=_adesc,
                pull_type='Attribution IQ Refresh' if _is_add else 'Attribution IQ Setup',
                credits_used=_atotal,
                job_id=_aiq_job):
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('That tracking package comes to '
                          + _C._pm_usd_label(
                              _C._pm_usd('attribution_iq_setup', 500.0)
                              + _C._pm_usd('attribution_iq_daily', 100.0) * _adays)
                          + ' and your account cannot cover it '
                          'right now. Add funds or ask your admin, '
                          'and I will set it up the moment you are '
                          'set.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        _aiq_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), _pm_ppu)
        _rr.start_engine_job('aiq', _aiq_job, _bpiq_user, _aiq_confirm, _aiq_extras, _C._pm_aiq_status_write, _C._pm_run_aiq_job)   # the queue, never a web thread (audit item 5)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': (f"On it. Adding the assets to {_aname} now; today's read is recomputed with them in it, and I will confirm here when it is in." if _is_add else
                      f'On it. Setting up {_aname} tracking now - '
                      'the campaign lands in the Attribution IQ tab '
                      'with the Multi-Touch read on every URL, and I '
                      'will confirm here when it is live.'
                      + (f' Daily refreshes run each morning through '
                         f'{_aiq_confirm.get("end_tracking_date")}, '
                         f'then tracking stops on its own.'
                         if _adays else '')),
            'aiq_job_id': _aiq_job,
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    if _C._pm_collect_flag_applies(body, 'aiq_inputs', history) and not _C._pm_intake_breakout(text, 'attribution'):
        return jsonify(_C._pm_intake_turn('attribution', text, body, history, user, _pm_ppu, first=False))
    if _C._pm_aiq_stop_intent(text):
        try:
            from migration.attribution_synthesis import (load_trackers,
                                                         stop_tracker)
            _low = ' '.join(str(text or '').lower().split())
            _hit = None
            for t in load_trackers(_H.s3_client):
                if not t.get('active'):
                    continue
                nm = str(t.get('campaign') or '').lower()
                if nm and (nm in _low or all(
                        w in _low for w in nm.split()[:3])):
                    _hit = t
                    break
            if _hit:
                stop_tracker(_hit['slug'], _H.s3_client)
                return jsonify({
                    'success': True, 'action': 'answer',
                    'reply': (f"Done - daily tracking for "
                              f"{_hit['campaign']} is stopped. The "
                              f"campaign and every day already "
                              f"tracked stay in the Attribution IQ "
                              f"tab; the prepaid window is not "
                              f"refunded."),
                    'followups': [], 'offer_deck': False,
                    'deck_angle': None})
            _act = [t['campaign'] for t in load_trackers(_H.s3_client)
                    if t.get('active')]
            if _act:
                return jsonify({
                    'success': True, 'action': 'answer',
                    'reply': ('Which campaign should stop tracking? '
                              'Currently tracking: '
                              + ', '.join(_act[:8])),
                    'followups': [f'Stop attribution tracking for '
                                  f'{c}' for c in _act[:3]],
                    'offer_deck': False, 'deck_angle': None})
        except Exception:
            traceback.print_exc()
    if _C._pm_aiq_intent(text) or _C._pm_read_intent(body, 'campaign'):
        return jsonify(_C._pm_intake_turn('attribution', text, body, history, user, _pm_ppu, first=True))
    # ---- Digital Journey flow (Jenna 2026-09-16) ----
    _fw_confirm = body.get('fw_confirm') or _C._pm_pending_intake('pick', user, 'flywheel', text)
    if isinstance(_fw_confirm, dict) and _fw_confirm.get('subject'):
        _fsubj = (f"{_fw_confirm.get('subject')} "
                  f"{_fw_confirm.get('ecosystem')} flywheel")
        _fw_job = uuid.uuid4().hex[:12]
        if not _H.consume_credit(
                _bpiq_user,
                description=f'Flywheel: {_fsubj}',
                pull_type='Flywheel IQ',
                credits_used=_C._PM_FW_CREDITS,
                job_id=_fw_job):
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('That flywheel prices at '
                          + _C._pm_tool_price_label('flywheel_iq', '$500')
                          + ' and your '
                          'account cannot cover it right now. Add '
                          'funds or ask your admin, and I will run '
                          'it the moment you are set.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        _fw_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), _pm_ppu)
        _rr.start_engine_job('fw', _fw_job, _bpiq_user, _fw_confirm, _fw_extras, _C._pm_fw_status_write, _C._pm_run_fw_job)   # the queue, never a web thread (audit item 5)
        try:
            from prometheus import flywheel_wizard as _fw_wiz2
            _fw_line = _fw_wiz2.running_reply(_fw_confirm)
        except Exception:
            _fw_line = (f'On it. Building the {_fsubj} now. It lands on the Flywheel IQ page, and I will confirm here when it is ready.')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _fw_line,
            'fw_job_id': _fw_job,
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    if _C._pm_collect_flag_applies(body, 'fw_inputs', history) and not _C._pm_intake_breakout(text, 'flywheel'):
        _fw_turn = _C._pm_intake_turn('flywheel', text, body, history, user, _pm_ppu, first=False)
        if _fw_turn is not None:   # None: the wizard let a question through; it routes below (2026-11-05)
            return jsonify(_fw_turn)
    if _C._pm_fw_intent(text):
        return jsonify(_C._pm_intake_turn('flywheel', text, body, history, user, _pm_ppu, first=True))
    _jiq_confirm = body.get('jiq_confirm') or _C._pm_pending_intake('pick', user, 'digital_journey', text)
    if isinstance(_jiq_confirm, dict) and _jiq_confirm.get('subject'):
        _jsubj = (f"{_jiq_confirm.get('subject')} on "
                  f"{_jiq_confirm.get('platform')}")
        _jiq_job = uuid.uuid4().hex[:12]
        if not _H.consume_credit(
                _bpiq_user,
                description=f'Digital Journey: {_jsubj}',
                pull_type='Digital Journey IQ',
                credits_used=_C._PM_JIQ_CREDITS,
                job_id=_jiq_job):
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('That journey prices at '
                          + _C._pm_tool_price_label('journey_iq', '$500')
                          + ' and your '
                          'account cannot cover it right now. Add '
                          'funds or ask your admin, and I will run '
                          'it the moment you are set.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        _jiq_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), _pm_ppu)
        _rr.start_engine_job('jiq', _jiq_job, _bpiq_user, _jiq_confirm, _jiq_extras, _C._pm_jiq_status_write, _C._pm_run_jiq_job)   # the queue, never a web thread (audit item 5)
        _jiq_line = (f'On it. Building the {_jsubj} journey now - '
                     'it takes a few minutes. '
                     'It lands in the Digital Journey tab, and I '
                     'will confirm here when it is ready.')
        if _C._pm_included_seat(_bpiq_user):   # an unlimited or staff seat reads the included line, never a price (2026-11-08 rule)
            _jiq_line = __import__('prometheus.priced_read', fromlist=['with_included_line']).with_included_line(_jiq_line)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _jiq_line,
            'jiq_job_id': _jiq_job,
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    if _C._pm_collect_flag_applies(body, 'jiq_inputs', history) and not _C._pm_intake_breakout(text, 'digital_journey'):
        return jsonify(_C._pm_intake_turn('digital_journey', text, body, history, user, _pm_ppu, first=False))
    # An explicit Profile IQ order never stays on the analyze surface
    # (2026-10-20 Jenna: "run a profile on mountain dew", typed while a
    # journey intake sat armed, was read as journey inputs, broke out,
    # ran as a read on the open profile and drew a $500 custom-read
    # offer). The words order a profile on a named subject: the
    # interpret flow drafts the brief, whatever is open or armed.
    _eb_subject = _subject_rules.explicit_build_subject(text)
    if _eb_subject:
        _C._pm_ask_hint(route='build_reroute', outcome='explicit_build_order', subject=_eb_subject)
        return jsonify(_subject_rules.build_reroute_payload())
    # An order that points at the open profile ("can you make this profile?",
    # "do one june 2024 - june 2025") builds that profile on that window; it
    # is never a read (Jenna 2026-10-30, Jay Patel on Godzilla Minus One).
    try:
        _pb = _subject_rules.open_profile_build_order(text, str(((ctx or {}).get('primary') or {}).get('name') or ''), history)
    except Exception:
        _pb = None
    if _pb:
        _C._pm_ask_hint(route='build_reroute', outcome='page_build_order', subject=_pb['subject'])
        return jsonify(dict(_subject_rules.build_reroute_payload(), prompt=_pb['prompt'], window=({'start': _pb['start'], 'end': _pb['end']} if _pb.get('start') else None)))
    if _C._pm_jiq_intent(text) or _C._pm_read_intent(body, 'journey'):
        return jsonify(_C._pm_intake_turn('digital_journey', text, body, history, user, _pm_ppu, first=True))
    # A message about how the dashboard itself is behaving is never a read of the open profile (Jenna 2026-11-18,
    # prometheus.dashboard_behavior): the door's lane answers it; this is the same lane for an ask that reached the core.
    _dbq_raw = __import__('prometheus.dashboard_behavior', fromlist=['core_hook']).core_hook(text, body, history, user, _C._pm_ask_hint)
    if _dbq_raw is not None:
        return jsonify(_dbq_raw)
    # ------------------ SINGLE ROUTER (2026-08-28) ------------------
    # One server-side decision for every ask (prometheus_router):
    # deterministic prefilters in one fixed precedence, the fast
    # classify model as the ambiguity backstop, open data decisive.
    # This removed the double reasoning call: an analysis-shaped data
    # ask with a profile open goes STRAIGHT to the measured-read pass
    # instead of paying the page-analysis pass first. The page
    # analysis keeps genuine open-ended asks; its generate_metrics
    # handoff survives only as a safety net (counted in stages as
    # handoff_generate so the shrink is visible in the ask log).
    ctx, ctx_err = _C._pm_validate_page_context(body.get('page_context'))
    # Derived read (2026-10-12, Jordan; Jenna: never page dependent by default): a held figure split by a dimension
    # runs the same with or without a page; subject + figures from the ask, thread, then screen (prometheus/derived_read).
    try:
        from prometheus import derived_read as _derived
        import prometheus_analysis as _pma_dr
        _dr = _derived.plan(text, history, ctx if (isinstance(ctx, dict) and not ctx_err) else None, body,
                            pma=_pma_dr, ns=_ns_mod, normalize=_H._normalize_for_match, stop=_C._PM_CLARIFY_STOP_TOKENS,
                            plausible=_C._pm_plausible_subject, consumption=_C._pm_consumption_subject)
    except Exception:
        traceback.print_exc()
        _dr = None
    if _dr:
        _C._pm_ask_stage('dimension_split_lane', count=1)
        if _dr.get('ask'):
            _C._pm_ask_hint(route='derived_read', outcome='clarify_subject')
            return jsonify(_dr['ask'])
        return _C._pm_generate_metrics_response(
            user, text, history, ctx=ctx if (isinstance(ctx, dict) and not ctx_err) else None,
            prefer_catalog=True, bind_subject=_dr['subject'], metric_request=_dr['metric_request'],
            panel_confirm=_dr.get('panel_confirm'))
    # Overall ranks (2026-09-25, Jenna): "No" on the open-profile
    # question means the boards, not the profile that happens to
    # be open. Answer from Rankers and do not bind the page.
    if body.get('overall_ranks'):
        _C._pm_ask_hint(route='rankers_overall', outcome='answered')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _C._pm_overall_rankers_reply(),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    # Confirmed memory referent (2026-08-27, Jenna: cross-session
    # memory). The confirm chip re-sends the original ask with the
    # remembered subject; route it straight to the measured-read pass
    # bound to that subject (instant replay when the read is banked).
    _bind_subject = str(body.get('bind_subject') or '').strip()
    if _bind_subject:
        if ctx_err:
            return ctx_err
        __import__('flask').g._pm_referent_confirmed = _bind_subject   # the chip confirmed the audience a bare "this audience" pointed at (2026-10-26)
        try:   # "Yes, {page}": the page is kept for this user's follow-ups
            _pg = str(((ctx or {}).get('primary') or {}).get('name') or '')
            if _pg and _H._normalize_for_match(_pg) == _H._normalize_for_match(_bind_subject):
                _C._pm_page_confirm_mark(_C._pm_ask_log_user(''), _pg)
        except Exception as _fx:
            _faults.note('chat', _fx)
        _pc_with_bind = body.get('panel_confirm') if isinstance(body.get('panel_confirm'), dict) else None
        return _C._pm_generate_metrics_response(
            user, text, history, ctx=ctx, prefer_catalog=True,
            bind_subject=_bind_subject,
            bind_cohort=str(body.get('bind_cohort') or '').strip(),
            panel_confirm=_pc_with_bind)
    # Open profile is not the question (2026-09-25, Jenna): a titles
    # ask that does not name the profile on screen asks before it
    # answers. Yes binds that profile. No is overall_ranks above.
    if isinstance(ctx, dict) and not ctx_err:
        _scope_page = str((ctx.get('primary') or {}).get('name')
                          or '').strip()
        if _scope_page and _C._pm_titles_ask_needs_scope(text, _scope_page):
            _C._pm_ask_hint(route='scope_clarify', outcome='asked_scope',
                         subject=_scope_page)
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': f'Do you mean on {_scope_page}?',
                'followups': ['Yes', 'No'],
                'offer_deck': False, 'deck_angle': None,
                'memory_confirm': {
                    'question': text,
                    'options': [
                        {'label': 'Yes', 'subject': _scope_page},
                        {'label': 'No', 'overall': True},
                    ]}})
    # Research-report confirm (2026-09-14): the widget's priced chip
    # re-sends the original ask with panel_confirm. The price is
    # recomputed and charged server-side; the client payload only
    # names the subject the quote was for.
    _panel_confirm = body.get('panel_confirm')
    if isinstance(_panel_confirm, dict) and _panel_confirm:
        if ctx_err:
            return ctx_err
        return _C._pm_generate_metrics_response(
            user, text, history, ctx=ctx, prefer_catalog=True,
            panel_confirm=_panel_confirm)
    # Garbled-input repair (2026-09-28, Phase 4): split digits and
    # space runs join before anything routes on the text.
    text = _C._pm_normalize_ask(text)
    # NUMERIC FOLLOW-UP (2026-09-28, Phase 3): share-of-US arithmetic
    # on the last delivered count answers instantly.
    _nf_reply = _C._pm_numeric_followup_reply(text, history)
    if _nf_reply:
        _C._pm_ask_hint(route='numeric_followup', outcome='answered')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': _nf_reply,
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    # FEEDBACK / METHODOLOGY INTERCEPT (2026-09-28, W39 review):
    # product feedback and number challenges acknowledge and forward
    # to ops; they never generate a read.
    _fb_user = (session.get('username') or user.get('username')
                or '').strip()
    # WRONG-ANSWER COMPLAINT (2026-09-28, Casey's second-screen ask):
    # "thats not what i asked for" reruns the PREVIOUS question fresh.
    # It never replays the entry the reader just rejected, and it
    # never gets brushed off as artwork feedback.
    _pm_skip_replay = False
    if any(rx.search(text or '') for rx in _C._PM_WRONG_ANSWER_RES):
        _C._pm_forward_user_feedback(_fb_user, text, 'wrong_answer')
        _prev_q = _C._pm_prev_user_question(history, text)
        # Learn from the correction (Jenna 2026-11-23): the wrong answer's
        # ledger row is retired, its words set aside, a figure the reader
        # gave is recorded for the subject and window, and a sanitized
        # fixture candidate goes to the staff-only queue (prometheus.corrections).
        try:
            from prometheus import corrections as _corr, thread_scope as _ts_corr
            _corr.learn_from_reader(_fb_user, _ts_corr.current_thread_id(), history, text,
                                    view=str(((body.get('page_context') or {}).get('view_context') or {}).get('view_id') or ''),
                                    job_doc_loader=_C._pm_read_job_doc)
        except Exception as _fx:
            _faults.note('chat', _fx)
        if _prev_q:
            # The rejected question joins the nightly regression set
            # (Jenna 2026-09-30) with the complaint and the on-screen
            # context it failed under.
            _C._pm_bank_regression_case(
                _fb_user, _prev_q, text, history,
                (body.get('page_context') or {}).get('view_context'))
            text = _prev_q
            _pm_skip_replay = True
            _C._pm_ask_hint(route='complaint_regenerate')
        else:
            _C._pm_ask_hint(route='complaint_regenerate',
                         outcome='asked_what')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('My fault - tell me what you were after '
                          '(the subject and the read you want) and I '
                          'will run it fresh right now.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
    if any(rx.search(text or '') for rx in _C._PM_FEEDBACK_RES):
        _C._pm_forward_user_feedback(_fb_user, text, 'content_feedback')
        _C._pm_ask_hint(route='user_feedback', outcome='forwarded')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Thank you - passed straight to the team, and '
                      'I will make sure it gets fixed. The read '
                      'itself stands in the meantime.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    if any(rx.search(text or '') for rx in _C._PM_METHOD_CHALLENGE_RES):
        _C._pm_forward_user_feedback(_fb_user, text, 'number_challenge')
        _C._pm_ask_hint(route='user_feedback', outcome='forwarded')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Every figure is Crosswalk first-party '
                      'measurement: unique US people in the window, '
                      'projected to the US population. I have flagged '
                      'your note so the team gives this one a second '
                      'look, and I will follow up here if it moves.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    # DELIVERY REQUEST INTERCEPT (2026-09-28, W39 review): "email
    # <address> when it is ready" registers the ready-notification on
    # the caller's in-flight builds instead of generating a read.
    if _C._PM_EMAIL_WHEN_READY_RE.search(text or ''):
        _addr_m = _C._PM_EMAIL_ADDR_RE.search(text or '')
        _notify_addr = (_addr_m.group(0) if _addr_m
                        else str(user.get('email') or '').strip())
        if _notify_addr and '@' in _notify_addr:
            # A delivery ask names no subject: take every non-terminal
            # run the caller owns (usually exactly one).
            _nf_runs = []
            try:
                import requests as _rqn
                _nf_resp = _rqn.get(
                    f"{_H.SYNTH_QUEUE_URL}/synth/list",
                    params={'limit': 25, 'active': '1',
                            'user': (user.get('email')
                                     or user.get('username')
                                     or '').strip()},
                    headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET},
                    timeout=12)
                if _nf_resp.status_code == 200:
                    _nf_runs = [d for d in (_nf_resp.json() or [])
                                if isinstance(d, dict)]
            except Exception:
                _nf_runs = []
            _nf_active = [d for d in _nf_runs
                          if str(d.get('status') or '').lower()
                          not in ('complete', 'error', 'failed',
                                  'canceled', 'cancelled')]
            if _nf_active:
                _done_subjects = []
                for _nfd in _nf_active[:3]:
                    if _C._pm_register_build_notify(
                            str(_nfd.get('run_id') or ''),
                            [_notify_addr], _fb_user):
                        _done_subjects.append(
                            str(_nfd.get('subject') or 'your profile'))
                if _done_subjects:
                    _C._pm_ask_hint(route='delivery_request',
                                 outcome='notify_registered')
                    _subj_txt = ' and '.join(_done_subjects)
                    return jsonify({
                        'success': True, 'action': 'answer',
                        'reply': (f'Done. {_notify_addr} gets an '
                                  f'email the moment {_subj_txt} is '
                                  f'ready, and it will be here in '
                                  f'the chat too.'),
                        'followups': [], 'offer_deck': False,
                        'deck_angle': None})
            _C._pm_ask_hint(route='delivery_request',
                         outcome='nothing_active')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('Nothing is building for your account right '
                          'now. Kick off a pull and ask me again, and '
                          'I will set up the email.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
    # CAN'T-DO LANE (2026-10-02 S8): an action this chat cannot take
    # (add a user, refund credits, rename or re-image a profile, edit
    # a number, contact a third party, schedule a run, push a file
    # into another tool) gets an honest answer naming what Prometheus
    # can do instead and who handles the rest. Runs after the
    # feedback and delivery lanes, before any model path.
    _cd_lane = _C._pm_cant_do_lane(_fb_user, text)
    if _cd_lane:
        _cd_reply, _cd_chips, _cd_kind = _cd_lane
        _C._pm_ask_hint(route='cant_do', outcome=_cd_kind)
        return jsonify({
            'success': True, 'action': 'answer', 'reply': _cd_reply,
            'followups': _cd_chips, 'offer_deck': False,
            'deck_angle': None})
    # SUBSCRIBER IQ LOOKUP (2026-10-02 Bria): "Do you see the SWAT
    # Exiles Season 1 Subscriber IQ?" is answered from the library and
    # the caller's runs. It never reaches the Subscriber IQ build
    # re-route below, so a read that already exists is never offered
    # again for credits.
    try:
        from prometheus import guards as _pg_lk
        _lk_title = _pg_lk.subiq_lookup_title(text)
    except Exception:
        _lk_title = ''
    if _lk_title:
        _lk_reply, _lk_chips = _C._pm_subiq_lookup_answer(user, _lk_title)
        _C._pm_ask_hint(route='subiq_lookup', outcome='answered',
                     subject=_lk_title if _lk_title != '*' else '')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': _lk_reply,
            'followups': _lk_chips, 'offer_deck': False,
            'deck_angle': None})
    # STATUS-CHECK INTERCEPT (2026-09-28 Jenna): "is eastside golf
    # running?" answers from the caller's own runs. Fires only when a
    # run actually matches the named subject; everything else falls
    # through untouched.
    _st_lane = _C._pm_status_lane(user, text)
    # "answer this now: <question>" is a command, not part of the
    # question (2026-10-02 S5). Strip it and remember it for the
    # open-screen confirm, which then binds instead of asking again.
    try:
        _an_text, _an_flag = _C._pm_answer_now_strip(text)
        if _an_flag:
            from flask import g as _g_an
            text = _an_text
            _g_an._pm_answer_now = True
    except Exception:
        traceback.print_exc()
    if _st_lane:
        _st_reply, _st_chips, _st_outcome, _st_subject = _st_lane
        _C._pm_ask_hint(route='status_check', outcome=_st_outcome,
                     subject=_st_subject)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _st_reply, 'followups': _st_chips,
            'offer_deck': False, 'deck_angle': None})
    # CUT-REQUEST INTERCEPT (2026-09-28 Jenna): a cut request is a
    # build action - it never rides the analysis pass or replays from
    # the answer library. Parent named in the ask: hand straight to
    # the build flow. No parent named: confirm the profile first,
    # leading with the one open on screen; the chip carries the full
    # composed command so the cohorts survive the tap.
    if _C._PM_CUT_INTENT_RE.search(text or '') \
            and not _C._PM_CUT_IDIOM_RE.search(text or ''):
        if ctx_err:
            return ctx_err
        if re.fullmatch(r'\s*cut a different profile\.?\s*',
                        text or '', re.IGNORECASE):
            _C._pm_ask_hint(route='cut_clarify', outcome='asked_parent')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('Name the profile and the cuts in one line, '
                          'like: Cut Yellowstone by males only and '
                          'Black consumers.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        if _C._pm_text_names_catalog_subject(text):
            _C._pm_ask_hint(route='cut_reroute', outcome='rerouted')
            return jsonify({
                'success': True, 'action': 'build_profile',
                'route_hint': 'interpret', 'reply': '',
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        _cut_cohorts = [c.strip() for c in
                        _C._PM_CUT_COHORT_RE.findall(text or '')
                        if c.strip()]
        _cut_page = str(((ctx or {}).get('primary') or {})
                        .get('name') or '').strip()
        if _cut_page:
            _cut_composed = (
                f"Cut {_cut_page} by {' and '.join(_cut_cohorts)}"
                if _cut_cohorts else f"Cut {_cut_page}")
            _C._pm_ask_hint(route='cut_clarify', outcome='asked_parent',
                         subject=_cut_page)
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': (f'Happy to run those. Which profile am I '
                          f'cutting - {_cut_page} (open on your '
                          f'screen) or a different one?'),
                'followups': [_cut_composed,
                              'Cut a different profile'],
                'offer_deck': False, 'deck_angle': None})
        _C._pm_ask_hint(route='cut_clarify', outcome='asked_parent')
        _cut_example = (' and '.join(_cut_cohorts) if _cut_cohorts
                        else 'males only and Black consumers')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Which profile should I cut? Name it and the '
                      'cuts in one line, like: Cut Yellowstone by '
                      + _cut_example + '.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    _pm_user = (session.get('username') or user.get('username') or '').strip()
    _nc_refs = []

    def _router_memory_referent():
        # Lazy: costs an S3 read; the router calls it at most once,
        # only when the decision needs it. The resolved referents are
        # kept for the memory-confirm payload below.
        try:
            import prometheus_memory as _pmm_rt
            if _pm_user and not _nc_refs:
                _nc_refs.extend(_pmm_rt.recent_referents(_pm_user, k=1))
        except Exception:
            traceback.print_exc()
        return bool(_nc_refs)

    def _router_has_base():
        # Ground for a question on a subject we hold: a catalog base for
        # the named subject, or a stored cross-session referent (the
        # measured-read pass then binds or asks the grounded confirm).
        try:
            if _C._pm_generation_base('', text, prefer_catalog=True):
                return True
        except Exception:
            traceback.print_exc()
        return _router_memory_referent()

    _t_router = time.monotonic()
    try:
        from prometheus import routing as _routing_c, understanding as _und_c
        # the Understanding decides the route (prometheus.routing, step 3); the rules that are not intents run first
        # inside it; no model is called to route (the fast-model classifier retired 2026-11-23)
        _route_d = _routing_c.analyze_route(
            _und_c.on_body(body) or _und_c.current(), text, surface='analyze', has_ctx=bool(ctx),
            mode=str(body.get('mode') or ''),
            has_base=_router_has_base,
            memory_referent=_router_memory_referent,
            other_subject=lambda: _subject_rules.names_other_subject(text, ctx))   # a named subject that is not the page is never a page analysis (Debi 2026-10-26)
    except Exception:
        traceback.print_exc()
        _route_d = {'route': 'analysis' if ctx else 'clarify',
                    'why': 'router_error'}
    _C._pm_ask_stage('router', t0=_t_router)
    _route = str(_route_d.get('route') or '')
    # Subscriber IQ asks (and the ambiguous-churn fork) belong to the
    # build surface's promotion machinery: hand the widget a re-route.
    # The widget already respects action='build_profile' as "fall
    # through to the interpret flow"; route_hint makes it explicit.
    # A QUESTION about reads the library already holds answers from
    # them (2026-10-02 Bria: "compare the first three days of Outlander
    # Blood of My Blood Season 2 to the first three days of SWAT Exiles
    # Season 1" was handed to the build flow and came back as a new
    # 10-credit order). Only a question with no pull verb and at
    # least one named library title stays here; everything else still
    # re-routes to the Subscriber IQ build flow.
    _subiq_answerable = False
    _subiq_fork = (_route == 'clarify'
                   and _route_d.get('why') == 'subiq_fork')
    if (_route == 'subiq' or _subiq_fork) and _subiq_library.is_library_wide_ask(text):   # "of the existing Subscriber IQs, which series ..." is read off the library (Bria 2026-10-21)
        _C._pm_ask_hint(route='subiq_library', outcome='answered')
        return jsonify(_subiq_library.answer(text, _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, user, can_open=(lambda k, n: _H._user_can_open_subscriber(user, k, n))))
    if _route == 'subiq' or _subiq_fork:
        try:
            from prometheus import guards as _pg_sq
            import prometheus_analysis as _pma_sq
            if (not _pg_sq.subiq_is_explicit_pull(text)
                    and _pg_sq.is_question_shaped(text)):
                _sq_view_open = str(((ctx or {}).get('view_context')
                                     or {}).get('view_id') or '') \
                    == 'subscriberIQ'
                # 2026-10-05 (Emma, Peacock July-cohort churn): a
                # QUESTION about churn / signups is answered, never
                # handed to the build flow. The ambiguous-churn fork
                # and the Subscriber IQ view being open both count:
                # the open read (or the library) is the base and the
                # deeper pass derives what the read does not carry.
                # A count of a sign-up cohort is a question too (Grace
                # Yin 2026-11-08: "how many US consumers signed up for
                # Amazon Prime and Hazbin Hotel Season 1 was the first
                # thing they watched" was handed to the build flow twice).
                if (_subiq_fork or _sq_view_open
                        or _pma_sq.is_cohort_churn_ask(text)
                        or _pma_sq.is_signup_count_ask(text)
                        or _pma_sq.find_subiq_titles_in_text(
                            _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, text)):
                    _subiq_answerable = True
        except Exception:
            traceback.print_exc()
    if _subiq_answerable:
        _C._pm_ask_hint(route='subiq_library_answer')
        if not ctx:
            # No screen open: the reasoned-read path carries the
            # named titles' Subscriber IQ evidence blocks itself.
            return _C._pm_generate_metrics_response(user, text, history)
        _route = 'analysis'
        _route_d['route'] = 'analysis'
        _route_d['why'] = 'subiq_library_answer'
    elif _route == 'subiq' or (_route == 'clarify'
                               and _route_d.get('why') == 'subiq_fork'):
        _C._pm_ask_hint(route='subiq_reroute', outcome='rerouted')
        return jsonify({
            'success': True, 'action': 'build_profile',
            'route_hint': 'interpret', 'reply': '',
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    # CSV download of an already-delivered read (2026-08-27, Jenna):
    # the offer chip on every generated-data reply lands here. Before
    # any charge; exporting an already-delivered read costs nothing.
    if _route == 'csv_download':
        return _C._pm_csv_download_response(user, text, history)
    # Quantifiability gate (2026-08-26, Jenna): asks about behavior
    # with no digital trace (linear / over-the-air tune-in, in-store
    # physical purchases, foot traffic, terrestrial radio) decline
    # gracefully with the nearest measurable read.
    if _route == 'not_quantifiable':
        _gate = _route_d.get('nq_gate') or {}
        try:
            import prometheus_analysis as _pma_gate
            _g_reply, _g_chips = _pma_gate.build_not_quantifiable_reply(
                text, _gate)
            _C._pm_ask_hint(route='quantifiability_gate',
                         outcome='declined_not_quantifiable')
            return jsonify({
                'success': True, 'action': 'answer', 'reply': _g_reply,
                'followups': _g_chips, 'offer_deck': False,
                'deck_angle': None,
                'not_quantifiable': _gate.get('domain')})
        except Exception:
            traceback.print_exc()
    # Search-journey demand asks (2026-08-26): these carry their own
    # subject and need no open profile.
    if _route == 'search_demand':
        try:
            return _C._pm_search_demand_response(user, text, history)
        except Exception as e:
            traceback.print_exc()
            _H._chatbot_error_email('brief-chat/analyze', e)
            return jsonify(_H._chatbot_calm_payload())
    if ctx_err:
        return ctx_err
    # The classifier read an explicit build/cut ask that slipped past
    # the widget's fast checks: hand it back to the interpret flow.
    if _route == 'build_interpret':
        _C._pm_ask_hint(route='build_reroute', outcome='rerouted')
        return jsonify({
            'success': True, 'action': 'build_profile',
            'route_hint': 'interpret', 'reply': '',
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    # Measured read: KPI vocabulary (2026-08-27, Paige Bueckers ad
    # CTR), strategy / white-space asks, direct data asks with nothing
    # open, and (2026-08-28) analysis-shaped data asks WITH the
    # profile open - straight to the measured-read pass, no page-
    # analysis pass first. The base gate inside resolves the subject
    # (catalog first, then the open page, then Subscriber IQ) and
    # steers to the 5-credit build when no base exists anywhere.
    # Open profile is not the subject until the user says so
    # (2026-09-28). Mode chips stay commands on the view already open.
    # Yes re-sends with bind_subject, which returns above this point.
    # View-grounded asks stay on the view (2026-09-30 Jenna: the
    # Attribution view's own chip "What drove conversion in this
    # campaign window?" asked "Do you mean for Paw Patrol Series
    # Viewers, or Obsession?"). When the open view carries the data
    # the ask points at, the answer grounds in that view; the
    # open-profile verdict and the profile-subject generate ladder
    # never run on it.
    _vc_owns = False
    try:
        _vc_owns = _C._pm_view_owns_ask(text, ctx) and not _subject_rules.names_other_subject(text, ctx)   # I6: a named subject is never answered from the page
    except Exception:
        traceback.print_exc()
    if _vc_owns:
        if _route in ('generate', 'memory_confirm'):
            _route = ''
        _C._pm_ask_hint(route='view_grounded',
                     subject=str(((ctx or {}).get('view_context')
                                  or {}).get('view_title') or ''))
    if isinstance(ctx, dict) and not _vc_owns \
            and not str(body.get('mode') or '').strip():
        # An answer to the which-audience clarify is consumed here:
        # merge it into the question that triggered the clarify and
        # never re-ask (Casey Pearson, 2026-09-29).
        _ca_declined = _C._pm_clarify_declined(history, text)
        _ca_merged = _C._pm_clarify_answer_merge(history, text)
        if _ca_merged and _ca_declined:
            # "No" / "neither" / "something else" to the open-screen
            # question: the original ask runs away from the page, the
            # page rides as a switch chip, and the question is never
            # asked again (2026-10-02 S5).
            _C._pm_ask_hint(route='clarify_declined', outcome='answered_away')
            return _C._pm_generate_metrics_response(
                user, _ca_merged, history,
                switch_page=str((ctx.get('primary') or {}).get('name')
                                or '').strip())
        if _ca_merged:
            text = _ca_merged
            try:
                _C._pm_ask_hint(route='clarify_answer_merge')
                _C._pm_page_confirm_mark(_C._pm_ask_log_user(''), str(
                    (ctx.get('primary') or {}).get('name') or ''))
            except Exception as _fx:
                _faults.note('chat', _fx)
        else:
            _osc = _C._pm_open_screen_confirm(text, ctx, history=history)
            if isinstance(_osc, dict):
                _sw_page = str((ctx.get('primary') or {}).get('name')
                               or '').strip()
                if _osc.get('route') == 'bind':
                    # ctx stays out so a named subject with no base
                    # anywhere steers to its build instead of falling
                    # back onto the open page.
                    return _C._pm_generate_metrics_response(
                        user, text, history, prefer_catalog=True,
                        bind_subject=_osc.get('subject'),
                        switch_page=_sw_page)
                return _C._pm_generate_metrics_response(
                    user, text, history, switch_page=_sw_page)
            if _osc is not None:
                return _osc
    if _route == 'generate':
        if _route_d.get('why') == 'no_ctx_data_ask':
            return _C._pm_generate_metrics_response(user, text, history)
        return _C._pm_generate_metrics_response(
            user, text, history, ctx=ctx, prefer_catalog=True)
    # Grounded clarify (2026-08-27, Jenna): when the user's own memory
    # holds a plausible referent, the open-something nudge leads with
    # it instead of asking cold. The chip re-runs this ask bound to
    # the remembered subject.
    if _route == 'memory_confirm':
        try:
            import prometheus_memory as _pmm_nc
            if _nc_refs:
                _nc_lab = _pmm_nc.referent_label(_nc_refs[0])
                # The ask already names the remembered subject
                # (2026-10-08, East Tree Media: "Show me monthly
                # consumption for The Office US" drew "Nothing is open
                # yet. Do you mean for The Office?"). A named subject
                # binds; the question is never asked back.
                if _nc_lab and _ns_mod.page_named(
                        text, _nc_lab, _H._normalize_for_match,
                        _C._PM_CLARIFY_STOP_TOKENS):
                    _C._pm_ask_hint(route='memory_bind', subject=_nc_lab)
                    return _C._pm_generate_metrics_response(
                        user, text, history, prefer_catalog=True,
                        bind_subject=str(_nc_refs[0].get('subject')
                                         or _nc_lab))
                # The ask names a different subject of its own ("monthly
                # viewership for Suits" after an Office thread): read on
                # it; never ask about the remembered one.
                try:
                    import prometheus_analysis as _pma_nc
                    _own = str(_pma_nc.guess_subject_from_text(text)
                               or '').strip()
                    if not (_own and _C._pm_plausible_subject(_own)):
                        _own = (_C._pm_consumption_subject(text)
                                or _ns_mod.mentioned_subject(
                                    text, _nc_lab, _H._normalize_for_match,
                                    _C._PM_CLARIFY_STOP_TOKENS,
                                    _C._pm_plausible_subject))
                except Exception:
                    _own = ''
                if _own and _C._pm_plausible_subject(_own):
                    _C._pm_ask_hint(route='named_subject', subject=_own)
                    return _C._pm_generate_metrics_response(
                        user, text, history, prefer_catalog=True,
                        bind_subject=_own)
                if _nc_lab:
                    _C._pm_ask_hint(outcome='memory_confirm')
                    return jsonify({
                        'success': True, 'action': 'answer',
                        'reply': (f"Nothing is open yet. Do you mean "
                                  f"for {_nc_lab}?"),
                        'followups': [_nc_lab, 'Something else'],
                        'offer_deck': False, 'deck_angle': None,
                        'memory_confirm': {
                            'question': text,
                            'options': [{
                                'label': _nc_lab,
                                'subject': _nc_refs[0]['subject'],
                                'cohort': _nc_refs[0].get('cohort'),
                            }]}})
        except Exception:
            traceback.print_exc()
    if not ctx:
        # Nothing open is not a dead end (Jenna 2026-10-12): the read binds the subject from the ask or thread, or asks
        _C._pm_ask_hint(route='no_page_read')
        return _C._pm_generate_metrics_response(user, text, history, prefer_catalog=True)
    # 2026-09-09 Jenna: "Analyze this data" is session-metered, not
    # per-pull charged. Pay-per-use accounts bill via the session
    # close; subscribed accounts are covered by their tier. Real
    # pipeline pulls (Profile IQ, Subscriber IQ) remain credit-gated
    # in their own routes.
    _pm_user = (session.get('username') or user.get('username') or '').strip()
    try:
        import prometheus_analysis as pma
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        return jsonify(_H._chatbot_calm_payload())
    mode = str(body.get('mode') or '').strip().lower()
    if mode not in pma.MODE_INSTRUCTIONS:
        mode = ''
    # Ledger replay first (2026-08-28, the routing wave): a repeat of
    # an ask Crosswalk already answered for the OPEN subject replays
    # the stored reply instantly - no digest build, no anchors, no
    # reasoning pass. Subject-scoped matches only: a question-text hit
    # on some other subject's bucket must never replay under a
    # different open profile (that fallback consult still feeds the
    # prompt constraints below). Mode chips (personas, exec summary,
    # ...) always run fresh - they are render-shape commands.
    _led = {'block': '', 'exact': None, 'entries': []}
    try:
        # the study the open view names, never the tab label with its icon
        # (2026-11-05, Liz: a replay keyed under "🗺️ Digital Journey IQ")
        from prometheus import view_base as _vb_led
        _led_view_name = str(_vb_led.view_label(ctx.get('view_context') or {}) or '').strip()
        _led_self_contained = bool(_vb_led.is_self_contained(((ctx.get('view_context') or {}).get('view_id'))))
    except Exception:
        _led_view_name = str((ctx.get('view_context') or {}).get('view_title') or '').strip()
        _led_self_contained = False
    # under a self-contained product the study on screen is the subject;
    # a profile left in the picker is not open (2026-10-22, Alexia)
    _led_subj = str(((_led_view_name if _led_self_contained else '')
                     or (ctx.get('primary') or {}).get('name')
                     or _led_view_name
                     or '')).strip()
    _led_same_subject = True
    _t_ledger = time.monotonic()
    try:
        import insights_ledger as _il_an
        # the thread rides along so a viewing-chain follow-up binds to the
        # question it follows (2026-11-05)
        _led = _il_an.consult(subject=_led_subj or None, question=text, history=history)
        if not _led.get('entries'):
            _led = _il_an.consult(question=text, history=history)
            _led_same_subject = False
    except Exception:
        traceback.print_exc()
    _C._pm_ask_stage('ledger', t0=_t_ledger)
    _led_exact = _led.get('exact')
    # A self-contained product on screen never replays a stored read whose
    # base was a profile file: that read was a different dataset
    # (2026-10-22, Alexia: the Potential Ticket Buyer read replayed under
    # the Attribution IQ campaign of the same title).
    try:
        from prometheus import view_base as _vb_rp
        if _led_exact and _vb_rp.is_self_contained(((ctx.get('view_context') or {}).get('view_id'))) \
                and str(_led_exact.get('base') or '').lower().endswith('.csv'):
            _led_exact = None
    except Exception:
        traceback.print_exc()
    _led_rejected = bool(_led_exact and _led_exact.get('reply') and _C._pm_replay_rejected_in_thread(_led_exact, history))
    if _led_rejected:
        try:   # the pushed-back words are set aside for good (2026-11-23, prometheus.corrections)
            from prometheus import corrections as _corr, thread_scope as _ts_corr
            _corr.learn_from_rejected_entry(_pm_user, _ts_corr.current_thread_id(), history, _led_exact)
        except Exception as _fx:
            _faults.note('chat', _fx)
    if _led_exact and _led_exact.get('reply') and _led_same_subject \
            and _led_subj and not mode and not _pm_skip_replay \
            and not _C._pm_replay_repeat_block(_pm_user, text) \
            and not _led_rejected:
        _replay_subj = _led.get('subject') or _led_subj
        _C._pm_ask_hint(route='ledger_replay', outcome='answered',
                     subject=_replay_subj)
        _C._pm_remember_ask(_pm_user, text, subject=_replay_subj,
                         cohort=_led_exact.get('cohort'),
                         ledger_key=_led_exact.get('k'), route='replay')
        # Served from the library: metered, never free (2026-09-14).
        _C._pm_meter_answer('replay_analysis', _pm_ppu)
        _replay_chips = [c for c in
                         list(_led_exact.get('followups') or [])[:3]
                         if c != pma.CSV_OFFER_CHIP]
        _C._pm_csv_point(_replay_subj, text, _led_exact.get('family'))
        # Replays carry their CSV too (2026-09-29 Jenna: every answer
        # with data creates the file).
        _rp_file = {}
        try:
            if _led_exact.get('breakdown') or _led_exact.get('metrics'):
                _rp_file = _C._pm_answer_file_payload(
                    _led_exact,
                    auto_save=bool(
                        _C._PM_FILE_ASK_RE.search(str(text or ''))),
                    username=_pm_user, question=text)
                if _rp_file and 'Email me this file' \
                        not in _replay_chips:
                    _replay_chips.append('Email me this file')
        except Exception:
            traceback.print_exc()
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': _led_exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': _replay_subj,
            **_rp_file})
    # Board-view scoping (2026-09-21 Jenna: the Starz Rankers ask "had
    # nothing to do with that profile"): an ask made from the Rankers /
    # Trends board is about the BOARD unless the text itself names the
    # selected profile. Drop the profile from the context so the answer
    # derives from the board data instead of anchoring to whatever
    # profile happened to be selected in another tab.
    try:
        _bv_view = str(((ctx.get('view_context') or {}).get('view_id'))
                       or '')
        if _bv_view in ('cultureRankerIQ', 'trendsIQ') \
                and ctx.get('primary'):
            _bv_name = str((ctx.get('primary') or {}).get('name') or '')
            _bv_toks = [t for t in re.findall(
                r'[a-zA-Z0-9]+', _bv_name.lower()) if len(t) >= 3]
            _bv_tl = str(text or '').lower()
            if not (_bv_toks and any(t in _bv_tl for t in _bv_toks)):
                ctx = dict(ctx)
                ctx['primary'] = None
                ctx['cuts'] = []
                print(f"[analyze] board-view ask ({_bv_view}): selected "
                      f"profile {_bv_name!r} not referenced - answering "
                      f"about the board")
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        digest, p_meta = None, {}
        if ctx.get('primary'):
            _t_digest = time.monotonic()
            try:
                digest, p_meta = pma.get_digest_bundle(
                    _H.s3_client, _H.S3_BUCKET, ctx)
            except Exception as _dg_err:
                if not pma.is_missing_key_error(_dg_err):
                    raise
                # Stale page context (2026-09-21 Starz-ask defect): the
                # browser's selected profile was deleted or retitled on
                # S3 after the page loaded. The profile is auxiliary
                # context, not the question - drop it and answer from
                # the view context instead of failing the whole ask.
                print(f"[analyze] stale page-context profile "
                      f"{(ctx.get('primary') or {}).get('s3_key')!r} "
                      f"no longer exists; proceeding without profile "
                      f"digest")
                ctx = dict(ctx)
                ctx['primary'] = None
                ctx['cuts'] = []
                digest, p_meta = None, {}
            _C._pm_ask_stage('digest', t0=_t_digest)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        return jsonify(_H._chatbot_calm_payload())
    # Cross-module signals (2026-08-26 Jenna: "prometheus thinks
    # between modules"): what Subscriber IQ, Trends, and the profile
    # library know about the same subject. Existence checks against
    # TTL-cached indexes, parallel fetches, hard time budget; on any
    # failure the analysis proceeds without enrichment.
    xmod_block, xmod_modules = '', []
    _t_anchors = time.monotonic()
    try:
        _xm_view = ((ctx.get('view_context') or {}).get('view_id')
                    or ('profileIQ' if ctx.get('primary') else ''))
        _xm_trends_reader = None
        if _H._trends_iq is not None:
            def _xm_trends_reader():
                return _H._trends_iq._cache_get({
                    'geo_type': 'National', 'geo_value': '',
                    'lookback_days': _H._trends_iq.DEFAULT_LOOKBACK_DAYS})
        xmod_block, xmod_modules = pma.build_cross_module_block(
            _H.s3_client, _H.S3_BUCKET, ctx, text,
            active_view=_xm_view,
            subiq_bucket=_H.SUBSCRIBER_S3_BUCKET,
            subiq_parser=_H.parse_subscriber_iq_csv,
            trends_reader=_xm_trends_reader)
    except Exception:
        traceback.print_exc()
        xmod_block, xmod_modules = '', []
    # Rankers board data (2026-09-21 Jenna): a board-view ask answers
    # from the actual board rows, not from headings + free knowledge.
    try:
        _board_view = str(((ctx.get('view_context') or {})
                           .get('view_id')) or '')
        _board_block = _C._pm_rankers_board_block(text, _board_view)
        if _board_block:
            xmod_block = (f"{xmod_block}\n\n{_board_block}"
                          if xmod_block else _board_block)
    except Exception:
        traceback.print_exc()
    # Exact rows for question-named entities (2026-10-01, Jenna:
    # deep corpus reach): verbatim cells from the open profile's full
    # file for any brand / title the question names, with Gen Pop
    # baselines. Closes the digest's mid-tail gap on screen asks.
    try:
        _er_key = str((ctx.get('primary') or {}).get('s3_key') or '')
        if _er_key.lower().endswith('.csv'):
            _er_df, _ = pma.load_profile_df(_H.s3_client, _H.S3_BUCKET,
                                            _er_key)
            _er_block = pma.build_named_entity_rows(
                _er_df, pma.load_genpop_map(_H.s3_client, _H.S3_BUCKET),
                text)
            if _er_block:
                xmod_block = (f"{xmod_block}\n\n{_er_block}"
                              if xmod_block else _er_block)
    except Exception:
        traceback.print_exc()
    # Measured daily signals (2026-10-01, Jenna: flavor 1) for
    # subjects the on-screen ask names. Fail-safe to ''.
    try:
        import panel_fact_store as _pfs
        _ms_block = _pfs.measured_signals_block(
            text,
            extra_names=[str((p_meta.get('name')
                              if ctx.get('primary') else '') or '')])
        if _ms_block:
            xmod_block = (f"{xmod_block}\n\n{_ms_block}"
                          if xmod_block else _ms_block)
    except Exception:
        traceback.print_exc()
    # Subscriber IQ parity on the screen path (2026-10-02). The cross-
    # module block carries one line for the open subject's acquisition
    # read; the full block (signups, windows, cohorts, drivers) now
    # rides too, for any indexed title the question names first and
    # the open profile second. Skipped when that title's Subscriber IQ
    # view is already on screen, since its payload is the page context.
    _subiq_grounded = False
    try:
        _vc = ctx.get('view_context') or {}
        _subiq_view_open = str(_vc.get('view_id') or '') == 'subscriberIQ'
        # 2026-10-02 (Bria): the open title's full file evidence rides
        # even when its Subscriber IQ view is on screen. The page
        # context is a compact summary; the evidence block is the
        # authoritative file read with the churn definition, and it is
        # cached, so there is no cost to carrying both.
        _sq_block, _sq_show = pma.build_subiq_evidence_block(
            _H.s3_client, _H.SUBSCRIBER_S3_BUCKET,
            _H.parse_subscriber_iq_csv, text,
            subject_hint=str((p_meta.get('name')
                              if ctx.get('primary') else '') or ''),
            prefer_text=True)
        if _sq_block:
            xmod_block = (f"{xmod_block}\n\n{_sq_block}"
                          if xmod_block else _sq_block)
        _subiq_grounded = bool(_sq_block) or _subiq_view_open
    except Exception:
        traceback.print_exc()
    _C._pm_ask_stage('anchors', t0=_t_anchors)
    # Cohort-churn lane (2026-10-05, Emma / Jenna: "the agent should
    # look at what monthly average churn is for peacock from external
    # sources (sec reports, etc) then figure it out"). With the title's
    # Subscriber IQ read grounded, a churn question about a signup
    # cohort skips the screen model (which can only quote platform
    # churn) and goes straight to the derived read, which researches
    # the platform's published churn and shapes the cohort curve.
    if _subiq_grounded and pma.is_cohort_churn_ask(text):
        _C._pm_ask_stage('cohort_churn_lane', count=1)
        _cc_vc = (ctx.get('view_context') or {}).get('data') or {}
        _cc_subject = (str(_cc_vc.get('show') or '').strip()
                       or (p_meta.get('name') if ctx.get('primary') else '')
                       or '')
        return _C._pm_generate_metrics_response(
            user, text, history,
            metric_request={
                'subject': _cc_subject,
                'metric_family': 'cohort churn',
                'cohort': text[:200],
                'needed': 'month-by-month churn of the signup cohort '
                          'named in the question, US-projected counts '
                          'and rates'},
            anchors_block=xmod_block, charge_done=True,
            ctx=ctx, digest_block=digest or '')
    # Insights-ledger history (2026-08-26, Jenna): numbers Crosswalk
    # already delivered for this subject ride the prompt as binding
    # constraints, so the NORMAL answer path sits on the same
    # consistency surface as generated reads. The consult itself ran
    # before the digest (replay-first, 2026-08-28); when the widget's
    # context name found nothing, the digest's canonical name gets one
    # recovery lookup so the constraints block still binds.
    if not _led.get('entries'):
        try:
            import insights_ledger as _il_an
            _pm_canon_name = (p_meta.get('name')
                              if ctx.get('primary') else None)
            if _pm_canon_name and _pm_canon_name != _led_subj:
                _led = _il_an.consult(subject=_pm_canon_name,
                                      question=text)
        except Exception:
            traceback.print_exc()
    _led_block = _led.get('block') or ''
    # Corpus catalog (2026-10-05): dashboard figures on the subject
    # ride the binding block ahead of chat-stated ones.
    try:
        _cat_block = '\n\n'.join(x for x in (_bpiq_wiz.saved_valuation_block(text), _C._pm_catalog_block(
            _led_subj, extra_subjects=[p_meta.get('name') if ctx.get('primary') else ''])) if x)
        if _cat_block:
            _led_block = (_cat_block + '\n' + _led_block) if _led_block else _cat_block
    except Exception:
        traceback.print_exc()
    # Who is asking (2026-10-06): company, role, the views they live in,
    # recent subjects, last window. Tone and defaults, never echoed.
    try:
        _ub = _C._pm_user_block(_pm_user)
        if _ub:
            _led_block = (_led_block + '\n\n' + _ub) if _led_block else _ub
    except Exception as _fx:
        _faults.note('chat', _fx)
    # Thread number bank + view glossary (2026-10-02 S4): on a
    # "why is this different" / "how is this calculated" ask, every
    # figure already stated in this thread and the open view's KPI
    # definitions ride the binding block, so the answer names the
    # earlier figure and the definition instead of re-deriving.
    _led_block = _C._pm_kpi_prompt_extras(text, history, ctx, _led_block)
    _figs = _fig.registry_for(xmod_block, digest, _led_block)   # figures by token, with provenance (audit items 3 + 4)
    user_prompt = _smart.with_reading(pma.build_analysis_user_prompt(
        digest, history, text, mode=mode or None,
        view_context=ctx.get('view_context'),
        cross_module_block=xmod_block,
        ledger_block=_led_block), body.get('reading')) + _figs.prompt_suffix()   # the Reader's understanding rides along (2026-10-14)
    try:
        import prometheus_knowledge as _pmk
        _kb = _pmk.knowledge_block(
            'analysis', text=text, ctx=ctx, mode=mode or None,
            subject=(p_meta.get('name') if ctx.get('primary') else '') or '',
            s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
        if _kb:
            user_prompt = f"{user_prompt}\n\n{_kb}"
    except Exception as _fx:
        _faults.note('chat', _fx)
    _max_tok = 7500 if mode in ('cross_profile', 'personas',
                                'whitespace') else 6000
    _t_model = time.monotonic()
    result = _C._pm_claude_json(pma.ANALYSIS_SYSTEM_PROMPT, user_prompt,
                             max_tokens=_max_tok, temperature=0.5,
                             usage_extras=_pm_ppu)
    _C._pm_ask_stage('model', t0=_t_model)
    if not result.get('success'):
        _H._chatbot_error_email(
            'brief-chat/analyze',
            'analysis model call failed: '
            + str(result.get('error') or 'unknown')[:400],
            tb='(model chain exhausted without a usable reply)')
        return jsonify(_H._chatbot_calm_payload())
    data = result.get('data') or {}
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), {})
    action = str(data.get('action') or 'answer')
    # Measured-read handoff (2026-08-26): the analysis pass decided the
    # ask needs a concrete number nothing on screen carries. Credits
    # were prechecked above; the measured-read pass consults the
    # ledger, generates, persists, and charges. Since the router
    # (2026-08-28) sends analysis-shaped data asks straight to the
    # measured read, this handoff is the safety net only - the stage
    # counter tracks how often it still fires.
    # Subscriber IQ answer-in-place (2026-10-02, Bria): "Analyze SWAT
    # Exiles, top 3 insights" with the read on screen came back as
    # generate_metrics and the reasoned pass answered about Starz with
    # numbers that were not the file's. When the title's read is on
    # screen or in evidence, the answer comes from the read. One re-ask
    # with the force note; if that still yields no reply, fall through
    # to the handoff so the user is never left without an answer.
    if action == 'generate_metrics' and _subiq_grounded \
            and pma.subiq_answer_in_place(text):
        _C._pm_ask_stage('subiq_answer_in_place', count=1)
        try:
            _r2 = _C._pm_claude_json(
                pma.ANALYSIS_SYSTEM_PROMPT,
                f"{user_prompt}\n\n{pma.SUBIQ_FORCE_ANSWER_NOTE}",
                max_tokens=_max_tok, temperature=0.4,
                usage_extras=_pm_ppu)
            _d2 = (_r2.get('data') or {}) if _r2.get('success') else {}
            if isinstance(_d2, list):
                _d2 = next((d for d in _d2 if isinstance(d, dict)), {})
            if str(_d2.get('reply') or '').strip():
                data = dict(_d2)
                data['action'] = 'answer'
                action = 'answer'
        except Exception:
            traceback.print_exc()
    if action == 'generate_metrics':
        _C._pm_ask_stage('handoff_generate', count=1)
        return _C._pm_generate_metrics_response(
            user, text, history,
            metric_request=data.get('metric_request'),
            anchors_block=xmod_block, charge_done=True,
            ctx=ctx, digest_block=digest or '')
    # A build verdict on a turn that orders no build is the model's
    # guess, never a handoff (2026-10-20 Jenna, Mark at Bent Pixels:
    # "No I am interested in showing a lot of aligned creators...I am
    # unclear why you keep bringing up Salish matter" drafted a $300
    # DraftKings Users profile). The turn is read again as the
    # correction it is; if that still yields nothing, the one-tap
    # which-subject question (the thread's subjects first, the page
    # last as the open profile). Nothing with a price is drafted.
    if action == 'build_profile' and not _subject_rules.priced_handoff_allowed(text):
        _C._pm_ask_stage('refusal_retry', count=1)

        def _reask_conv(note, _up=user_prompt, _mt=_max_tok):
            _r = _C._pm_claude_json(pma.ANALYSIS_SYSTEM_PROMPT, f"{_up}\n\n{note}",
                                 max_tokens=_mt, temperature=0.3, usage_extras=_pm_ppu)
            return (_r.get('data') or {}) if _r.get('success') else {}
        _how, _out = _subject_rules.conversation_hold(
            text, history, p_meta.get('name') or '', _reask_conv, pma.scrub_user_text)
        if _how == 'answer':
            data, action = _out, 'answer'
            _C._pm_ask_hint(outcome='conversation_kept', subject=p_meta.get('name') or None)
        else:
            _C._pm_ask_hint(route='open_screen_confirm', outcome='asked_which_subject',
                         subject=p_meta.get('name') or None)
            return jsonify(_out)
    reply = str(data.get('reply') or '').strip()
    if action != 'build_profile' and not reply:
        _H._chatbot_error_email('brief-chat/analyze',
                             'analysis returned an empty reply',
                             tb='(model returned no reply text)')
        return jsonify(_H._chatbot_calm_payload())
    # Sub-cut safety net (2026-08-27, Jenna / Paw Patrol kids-4-6):
    # if the analysis pass answered a sub-cut ask by disclosing a
    # coverage gap ("there is no 4 to 6 row", "not cut to child age")
    # instead of returning generate_metrics, reroute to the
    # measured-read pass. The gap disclosure never ships.
    if action == 'answer' and ctx.get('primary'):
        try:
            if pma.detect_subcut_intent(text) \
                    and pma.contains_gap_disclosure(reply):
                return _C._pm_generate_metrics_response(
                    user, text, history,
                    metric_request={
                        'subject': p_meta.get('name') or '',
                        'cohort': text[:200],
                        'needed': text[:200]},
                    anchors_block=xmod_block, charge_done=True,
                    ctx=ctx, digest_block=digest or '')
        except Exception:
            traceback.print_exc()
    # Defense-in-depth vocabulary pass (2026-08-26): banned internal
    # terms replaced and em dashes stripped before the text reaches
    # the user. Mirrors the partner API's _V1_BANNED_TOKENS posture.
    reply = pma.scrub_user_text(reply)
    reply, _fig_ledger = _fig.compose(reply, _figs, xmod_block) if action == 'answer' else (reply, [])   # tokens rendered, leftovers bound, every figure sourced
    # Refusal guard (2026-09-28): a draft that declines to answer or
    # carries no numbers retries once with a produce-the-read
    # instruction; a second refusal reroutes to the measured-read
    # pass, which owns the follow-up delivery machinery.
    if action == 'answer' and _C._pm_reads_as_refusal(reply, mode):
        _t_refuse = time.monotonic()
        _r2 = _C._pm_claude_json(
            pma.ANALYSIS_SYSTEM_PROMPT,
            user_prompt + (
                "\n\nYour previous draft declined to answer. That is "
                "not acceptable. Produce the read now: state the "
                "numbers for exactly what was asked, derived from the "
                "measures above. Do not ask the reader to rephrase, "
                "narrow, or pick a different question."),
            max_tokens=_max_tok, temperature=0.4,
            usage_extras=_pm_ppu)
        _C._pm_ask_stage('refusal_retry', t0=_t_refuse)
        _d2 = (_r2.get('data') or {}) if _r2.get('success') else {}
        if isinstance(_d2, list):
            _d2 = next((d for d in _d2 if isinstance(d, dict)), {})
        _reply2 = pma.scrub_user_text(
            str(_d2.get('reply') or '').strip())
        if _reply2 and not _C._pm_reads_as_refusal(_reply2, mode):
            reply = _reply2
            data = _d2
        else:
            return _C._pm_generate_metrics_response(
                user, text, history,
                metric_request={
                    'subject': (p_meta.get('name')
                                if ctx.get('primary') else '') or '',
                    'needed': text[:200]},
                anchors_block=xmod_block, charge_done=True,
                ctx=ctx, digest_block=digest or '')
    # US projection audit (2026-10-05, Jenna: "make sure all numbers
    # prometheus sends out are always projected to the US gen pop ...
    # never dont project"). Every panel -> US pair the prompt carried
    # (screen data, Subscriber IQ evidence, profile digest) is checked
    # against the finished reply. A panel count standing without its
    # US figure re-asks the model once with the explicit map; a redo
    # that still leaks has the panel numbers replaced in place.
    if action == 'answer' and reply:
        try:
            from prometheus import projection as _proj
            _, _pj_pairs = _proj.project_view_context(
                ctx.get('view_context'))
            _pj_pairs = list(_pj_pairs or []) \
                + _proj.pairs_from_text(xmod_block) \
                + _proj.pairs_from_text(digest or '')
            if _pj_pairs:
                def _pj_reask(note, _up=user_prompt, _mt=_max_tok):
                    _t = time.monotonic()
                    _r = _C._pm_claude_json(
                        pma.ANALYSIS_SYSTEM_PROMPT, f"{_up}\n\n{note}",
                        max_tokens=_mt, temperature=0.3,
                        usage_extras=_pm_ppu)
                    _C._pm_ask_stage('projection_reask', t0=_t)
                    _d = (_r.get('data') or {}) if _r.get('success') else {}
                    if isinstance(_d, list):
                        _d = next((x for x in _d if isinstance(x, dict)), {})
                    return pma.scrub_user_text(
                        str(_d.get('reply') or '').strip())
                reply, _pj_detail = _proj.enforce(
                    reply, _pj_pairs, reask=_pj_reask, log=print)
                if _pj_detail:
                    _C._pm_ask_stage('projection_fix', count=1)
                    print(f"[projection] analyze reply fixed: {_pj_detail}")
        except Exception:
            _C._pm_swallow('projection audit')
    # Arithmetic coherence on the page read (2026-11-08, Grace Yin's exec
    # summary: "31.4% of the file, 42,957,147 people" on a 1,368,062-person
    # file). Every share/count pair is checked against the file's own
    # audience; a count on the wrong base is recomputed in place.
    if action == 'answer' and reply and p_meta.get('proj'):
        try:
            from prometheus import arith_coherence as _arith
            _ai = _arith.check(reply, counts=[('the file', int(p_meta['proj']))],
                               subject=str(p_meta.get('name') or ''))
            if _ai.get('changed'):
                reply = _ai['text']
                _C._pm_ask_stage('arith_fix', count=len(_ai.get('fixes') or []))
                print(f"[analyze] arithmetic coherence: {_arith.trail(_ai)[:400]}")
        except Exception:
            _C._pm_swallow('arithmetic coherence')
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:4]
    # Cross-module chip: when Subscriber IQ has a read for this
    # subject, surface it as a followup (deduped, room permitting).
    if 'subscriber_iq' in xmod_modules:
        _sq_chip = 'Compare with its Subscriber IQ read'
        if not any('subscriber iq' in f.lower() for f in followups):
            followups = (followups[:3] + [_sq_chip])
    deck_angle = data.get('deck_angle')
    # Deck builds render from an open Profile IQ profile; a view-only
    # analysis (Subscriber IQ / Trends / Microdramas screen data with
    # no profile open) never offers one.
    offer_deck = bool(data.get('offer_deck')) and bool(ctx.get('primary'))
    _pm_subject = (p_meta.get('name')
                   or (ctx.get('view_context') or {}).get('view_title')
                   or 'open view')
    # A build_profile action is a handoff to the interpret flow, not
    # an answer: log it as the reroute it is (the explicit reroute
    # exits above use the same route / outcome pair).
    _C._pm_ask_hint(route=('build_reroute' if action == 'build_profile'
                        else 'page_analysis'),
                 outcome=('rerouted' if action == 'build_profile'
                          else 'answered'),
                 subject=_pm_subject, mode=mode or None)
    if action != 'build_profile':   # one brain (2026-10-13): the figures join the shared ring, a few notes the reader's own
        _shared.after_answer(__import__('insights_ledger'), _C._pm_claude_data, user=_pm_user, question=text, reply=reply, subject=_pm_subject,
                             followups=followups, base_profile_key=str((ctx.get('primary') or {}).get('s3_key') or ''), figures=_fig_ledger)
    # Fresh generations meter through their own model-call usage rows
    # (usage_extras on _pm_claude_json); no separate debit here.
    return jsonify({
        'success': True, 'action': action, 'reply': reply, 'figures': _fig_ledger,
        'followups': followups,
        'offer_deck': offer_deck,
        'deck_angle': (str(deck_angle)[:400]
                       if (deck_angle and offer_deck) else None),
        'model': result.get('model'),
        'profile': p_meta.get('name')})


__all__ = ['_ask_logged', 'api_synth_chat_analyze', '_pm_analyze_core']
