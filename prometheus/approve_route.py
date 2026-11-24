"""Approving a brief: the window rebind, the rescue of an unverified draft, the recent-build guard, the seat and cut pricing, and the approve route with its core.

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
from prometheus import api_threads as _api_threads
from prometheus import order_truth as _order_truth
from prometheus import question_first as _question_first
from prometheus import build_ready as _build_ready, list_shape as _list_shape
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared
from prometheus import plan_allowance as _plan
from prometheus import order_quote as _oq

# <<moved>>
# <<block: 1>>
@_H.app.route('/api/brief-chat/rebind-window', methods=['POST'])
@_H.app.route('/api/synth-chat/rebind-window', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/rebind-window')
def api_synth_chat_rebind_window():
    """Apply an answered time window to drafts the reader already has.

    Week 2026-W37 showed 26% of all chat latency (539s of 2100s) going
    into re-reading asks that had already been read. Cause: when the
    reader answers the window question with their own range instead of
    taking the default, the chat used to fold the answer onto the end
    of the original ask and start over from scratch. That second pass
    cost 57s to 147s and produced exactly the same subjects, the same
    decisions, and the same sizes as the first. The only thing that
    actually changed was the window.

    So change only the window. The drafts are already in the reader's
    hands (the approve route has always taken spec_draft straight from
    the client), so this adds no new trust surface, and it is strictly
    narrower than approve: it edits one field group and queues nothing.
    No model call, so the answer comes back in milliseconds.

    This is not a behaviour override per no-external-overrides.mdc. The
    caller states no decision and passes no flags. It answers a
    question the product asked, and the window is parsed server side by
    the same _split_trailing_date_range the interpret step uses, then
    applied by the same _bind_shared_explicit_window. An answer this
    cannot parse returns unparsed=True so the caller falls back to a
    full re-read rather than guessing.
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not _C._pm_gate_pull(user):
        return _C._pm_gate_refusal('pull')
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'success': False, 'unparsed': True}), 200

    answer = str(body.get('text') or '').strip()
    drafts = body.get('spec_drafts')
    if not isinstance(drafts, list):
        one = body.get('spec_draft')
        drafts = [one] if isinstance(one, dict) else []
    drafts = [d for d in drafts if isinstance(d, dict)]
    if not answer or not drafts:
        return jsonify({'success': False, 'unparsed': True}), 200

    # Parse through the interpret step's own reader so a window that
    # binds here is exactly the window a full re-read would have bound.
    #
    # That reader splits a TRAILING clause off a carrier string and
    # deliberately declines when the whole string is the clause (it
    # refuses to leave an empty subject behind). A bare reply is all
    # clause, so give it an inert carrier. 'Run it' is chosen because
    # it contains none of the words the reader triggers on (date,
    # dates, date range, date window, window, time frame, timeframe,
    # period) - a carrier carrying one of those would match at index 0
    # and leave nothing in front of the clause again. The carrier is
    # never shown or stored; only the parsed window survives.
    bare = re.sub(
        r'^\s*(?:date\s*range|dates?|date\s*window|window|'
        r'time\s*frame|timeframe|period)\s*[:\-]?\s*',
        '', answer, flags=re.IGNORECASE).strip()
    probe = f'Run it. Date range: {bare or answer}'
    try:
        _, rng = _H._split_trailing_date_range(probe)
    except Exception:
        rng = None
    if not rng or ' TO ' not in rng:
        # Relative phrasing ("trailing 6 months"), an event window, or
        # anything else we cannot resolve deterministically. Say so and
        # let the caller re-read the ask properly.
        return jsonify({'success': False, 'unparsed': True}), 200

    bound = 0
    for d in drafts:
        # Clear the flag first: these drafts already carry a window
        # (the default we proposed), and the binder deliberately yields
        # to anything already marked explicit.
        d.pop('date_range_explicit', None)
        try:
            if _H._bind_shared_explicit_window(
                    d, probe, decision=d.get('decision')):
                bound += 1
        except Exception as _fx:
            _faults.note('chat', _fx)
    if not bound:
        return jsonify({'success': False, 'unparsed': True}), 200

    # Same per-line window suffix the interpret step renders, so a
    # rebound card and a re-read card are indistinguishable.
    try:
        _H._annotate_drafts_date_window(drafts)
    except Exception as _fx:
        _faults.note('chat', _fx)

    start, end = [p.strip() for p in rng.split(' TO ', 1)]
    try:
        label = _H._ew_format_label(start, end) or rng
    except Exception:
        label = rng
    try:
        print(f"[rebind-window] {user}: {bound} draft(s) -> {rng} "
              f"(no re-read)")
    except Exception as _fx:
        _faults.note('chat', _fx)
    return jsonify({
        'success': True,
        'bound': bound,
        'date_range': {'start': start, 'end': end},
        'date_window_label': label,
        'spec_drafts': drafts,
    })


# <<block: 2>>
def _pm_rescue_unverified_draft(draft, usage_extras=None):
    """Approve-time rescue for a draft whose subject the interpret step
    could not verify (2026-09-22, Jessie's 'run a profile on
    Amandaland': the model shipped a new_build draft with EMPTY
    tu_demos and its clarify question buried in assumptions; the queue
    then 400'd 'tu_demos must be a non-empty dict' and the user got an
    error email instead of either an answer or a question).

    Order of rescue:
      1. One search-enabled identification call - a real entity the
         model simply did not know (Amandaland is a 2025 BBC sitcom)
         resolves here without bothering the user.
      2. Identified -> the interpret re-runs with the verified context
         appended, producing a complete draft (demos included) that
         continues into the normal approve flow.
      3. Not identified / still incomplete -> a friendly in-chat
         question (the guidance shape the widget already renders).

    Returns {'draft': fresh_draft} on success, {'ask': question} when
    the user has to answer, None on any internal failure (caller then
    proceeds with the original draft and the standard validation)."""
    try:
        subject = str(draft.get('resolved_title') or draft.get('subject')
                      or draft.get('name') or '').strip()
        subject = re.sub(r'\s+(viewers|listeners|readers|players|fans)$',
                         '', subject, flags=re.I).strip()
        if not subject:
            return None
        ident = _C._pm_claude_json(
            ('You identify real-world entities. Search the web when '
             'unsure. Return STRICT JSON only: {"identified": bool, '
             '"kind": "tv series|film|podcast|book|game|brand|person|'
             'other", "summary": "2-3 sentences: what it is, year, '
             'country, and where it streams/airs/sells in the US", '
             '"platform": "primary US platform or null"}. identified '
             'is true ONLY when you are confident this is a real '
             'entity.'),
            f'Identify: {subject}',
            max_tokens=900, temperature=0.0,
            surface='approve-rescue', usage_extras=usage_extras,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search',
                    'max_uses': 4}])
        data = (ident or {}).get('data') or {}
        if not (ident.get('success') and data.get('identified')
                and str(data.get('summary') or '').strip()):
            print(f"[approve-rescue] {subject!r} not identified by "
                  f"research; asking the user")
            return {'ask': (
                f"Before I build this: I could not verify what "
                f"{subject} is. Is it a TV series, a podcast, a book, "
                f"a brand, or something else - and if it is a show, "
                f"where does it air or stream? One line is enough, "
                f"and I will take it from there.")}
        summary = ' '.join(str(data['summary']).split())
        print(f"[approve-rescue] {subject!r} identified: "
              f"{summary[:140]}")
        # 2026-10-02 audit: this name was never imported here, so the
        # rescue raised NameError into its own except and silently
        # returned None on every approve it should have saved.
        try:
            from iq_rankers import MASTER_CATEGORIES
        except Exception:
            MASTER_CATEGORIES = {}
        # the composed ask is the text the rulebook prune and the draft
        # ceiling read (no caller text reaches this rescue; the name was
        # unbound here and the rescue raised NameError on every approve
        # it should have saved)
        rescue_text = (f"run a profile on {subject}\n\n"
                       f"VERIFIED CONTEXT (already researched, treat as fact): "
                       f"{subject} is {summary}")
        sys_p, usr_p = _C._synth_chat_interpret_prompts(
            rescue_text,
            chat_history=None,
            master_categories=MASTER_CATEGORIES,
            candidate_matches=[])
        sys_p, _dropped2 = _smart.prune_rulebook(sys_p, rescue_text, None)
        fresh = _H._run_nflx_claude_agent(
            system_prompt=sys_p, user_prompt=usr_p,
            max_tokens=_smart.draft_output_ceiling(rescue_text), temperature=0.4,
            model=_C._SYNTH_CHAT_INTERPRET_MODEL,
            usage_tag=('interpret', 'approve-rescue', None))
        fd = (fresh or {}).get('data') or {}
        if isinstance(fd, list):
            fd = next((d for d in fd if isinstance(d, dict)), {})
        if fresh.get('success') and isinstance(fd, dict) \
                and fd.get('tu_demos'):
            # carry the original ask's avid choice + any explicit
            # window; everything else comes from the re-interpret
            for k in ('run_avid',):
                if k in draft:
                    fd.setdefault(k, draft[k])
            return {'draft': fd}
        print(f"[approve-rescue] re-interpret still incomplete for "
              f"{subject!r}; asking the user")
        return {'ask': (
            f"Before I build this: I could not verify what {subject} "
            f"is. Is it a TV series, a podcast, a book, a brand, or "
            f"something else - and if it is a show, where does it air "
            f"or stream? One line is enough, and I will take it from "
            f"there.")}
    except Exception:
        traceback.print_exc()
        return None


# <<block: 3>>
_PM_RECENT_BUILDS_KEY = 'system/usage/recent_builds.json'


# <<block: 4>>
def _pm_recent_build_guard(username, subject, ws='', we=''):
    """The half-hour duplicate rule (smclain, Trinity Tatum x2, 2026-09-29: a 6-day window drift ran two full
    builds). Since 2026-11-17 it is one guard for every product an approval can start, kept in
    prometheus.repeat_approval beside the day-long check the approve path runs first. Returns the earlier entry
    when this profile build was ordered in the last half hour, else records it and returns None. A window that
    moved more than 21 days on either end is a correction and is allowed through. Fail-open."""
    from prometheus import repeat_approval as _repeat
    return _repeat.legacy_hold(_H, username, subject, ws, we)


# <<block: 5>>
def _pm_seat_reads_included(amount_usd=None, username=None):   # 'Included with your plan.' in place of a price
    return _plan.seat_reads_included(_H, session, amount_usd, username)


# <<block: 6>>
def _pm_cut_usd(draft=None):   # one add-on cut, as the approval charges it (prometheus.order_quote)
    return _oq.cut_each(_H, draft)


# <<block: 7>>
_PM_NO_DRAFT_REPLY = 'Nothing is queued yet. Tell me who or what you want pulled, a person, a brand or a title, and I will draft the brief for you to approve.'


# <<block: 8>>
@_H.app.route('/api/brief-chat/approve', methods=['POST'])
@_H.app.route('/api/synth-chat/approve', methods=['POST'])  # legacy alias
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/approve')
def api_synth_chat_approve():
    """User-approved spec + run params -> POSTs to Hetzner queue. Returns run_id.

    Session-authenticated dashboard users only. Partner API keys must
    use POST /api/v1/profiles/run instead.
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    _funds_resp = _C._pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    # Prometheus mode gate (2026-09-03, Jenna): the approve step
    # confirms and queues a new profile build. 'analysis'-only users
    # cannot queue a build.
    if not _C._pm_gate_pull(user):
        return _C._pm_gate_refusal('pull')
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        _H._chatbot_error_email('brief-chat/approve',
                             'profile engine not configured '
                             '(queue URL/secret missing)',
                             tb='(configuration check)')
        return jsonify(_H._chatbot_calm_payload())

    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/approve', e)
        return jsonify(_H._chatbot_calm_payload())
    return _C._pm_approve_core(user, body)


# <<block: 9>>
def _pm_approve_core(user, body):
    """The approve surface for an already-gated user and a parsed body.
    Split out of api_synth_chat_approve (2026-11-10, prometheus.orders):
    the route above runs it on the widget's Approve, and the interpret
    core runs it in the same turn for an unlimited seat's explicit order
    (no approve chip, Jenna 2026-11-08 and 2026-11-10). Returns a Flask
    response exactly as the route did."""
    draft = body.get('spec_draft') or {}
    if not isinstance(draft, dict) or not any(draft.get(k) for k in ('subject', 'display_name', 'name', 'subject_name', 'decision')):
        # No draft, nothing to run (2026-10-15, Emmet at Kartel): guidance the reader can act on, never the working-on-it promise, no ops email.
        _faults.note('chat', RuntimeError('approve without a draft'))
        return jsonify({'success': False, 'guidance': True, 'error': _C._PM_NO_DRAFT_REPLY, 'followups': []})

    # Chip pair override (2026-09-04, Jenna existing_match UX). The
    # session-only 'No, run what I asked' chip flips an existing_match
    # draft into a fresh new_build in one click. Semantically the
    # same as the user typing 'no, build me a new one' as free text
    # and letting the interpret step re-decide - but without the
    # extra Claude round-trip.
    #
    # Session-only per no-external-overrides.mdc: chatbot chips are
    # a user-driven session flow, not a wire-protocol override. The
    # partner API `/api/v1/*` (via `/api/v1/profiles/run`) never
    # touches `force_new_build`; only this session route
    # `/api/synth-chat/approve` (alias `/api/brief-chat/approve`) does.
    # Ops-side forcing still lives in migration/local_override_profile.py.
    #
    # Guardrails:
    #   * Only mutates when the draft's own decision is 'existing_match'.
    #     A new_build / derive_cut / refresh draft with a stray
    #     force_new_build=true flag is a no-op (chips only render on
    #     existing_match, so a stray flag from any other origin is
    #     rejected here as well).
    #   * Strips ALL existing_match_* pointers so the normalized-match
    #     backstop in _normalize_v1_decision cannot flip it BACK to
    #     existing_match on entity-match fuzz.
    #   * Re-prices estimated_credits to the new_build tier so the
    #     credit preflight below charges the right amount.
    #   * Idempotency store still keys on the mutated spec; a rapid
    #     double-click on the chip does not double-queue (the standard
    #     hostname-scoped idempotency + queue-side dedupe handles it).
    if bool(body.get('force_new_build')) and \
            str(draft.get('decision') or '').strip().lower() == 'existing_match':
        for _emk in ('existing_match_s3_key', 'existing_match_display_name',
                     'existing_match_days_old', 'existing_match_last_modified'):
            draft.pop(_emk, None)
        draft['decision'] = 'new_build'
        try:
            _fresh_credits = int(
                _H._V1_CREDITS.get('new_build', _H.CREDITS_PROFILE_ANALYSIS))
            draft['estimated_credits'] = _fresh_credits
            draft['base_credits'] = _fresh_credits
            draft.pop('estimated_credits_new_build', None)
        except Exception as _fx:
            _faults.note('chat', _fx)

    # Unverified-subject rescue (2026-09-22, Amandaland): a build draft
    # with empty tu_demos means the interpret step could not verify the
    # subject and held every sizing field. Posting it to the queue can
    # only 400 ('tu_demos must be a non-empty dict') and email an error
    # while the user gets nothing. Research the entity first (one
    # search-enabled call - a real show the model did not know resolves
    # without bothering anyone), re-interpret with the verified
    # context, and only ask the user when research genuinely fails.
    _t_rescue = time.monotonic()
    if str(draft.get('decision') or '').strip().lower() in \
            ('new_build', 'cut_needs_parent') \
            and not draft.get('tu_demos'):
        _uv = _C._pm_rescue_unverified_draft(
            draft, usage_extras=_C._pm_usage_extras(user))
        _C._pm_ask_stage('approve_rescue', t0=_t_rescue)
        if isinstance(_uv, dict) and _uv.get('draft'):
            draft = _uv['draft']
            print(f"[approve-rescue] proceeding with the researched "
                  f"draft for {draft.get('subject') or draft.get('name')!r}")
        elif isinstance(_uv, dict) and _uv.get('ask'):
            return jsonify({
                'success': False, 'guidance': True,
                'error': _uv['ask'], 'followups': []})

    _t_spec = time.monotonic()
    # the question this build was started from rides the brief; read here and taken off, never to the engine
    _origin_keep = _question_first.take_origin(draft)
    _df_stop = __import__('prometheus.data_floor', fromlist=['gate']).approve_stop(draft)   # a brief whose window still starts before the data floor is not run or charged (Jenna 2026-11-18)
    if _df_stop:
        return jsonify({'success': False, 'guidance': True, 'error': _df_stop, 'followups': [], 'data_floor': 'before'})
    spec = _C._spec_from_draft(draft)
    _C._pm_ask_stage('approve_spec', t0=_t_spec); _t_gates = time.monotonic()
    run_avid = bool(body.get('run_avid', True))
    # Accept a single address or a comma / semicolon / whitespace-
    # separated list. Clean each entry, drop obvious garbage, dedupe
    # case-insensitively, and rejoin with ', ' so the wire contract
    # stays a plain string (Hetzner side splits it back into SES
    # Destinations).
    import re as _re_email
    _raw_email = (body.get('email_to') or '')
    _seen_emails = set()
    _clean_emails = []
    for _part in _re_email.split(r'[,;\s]+', _raw_email):
        _addr = (_part or '').strip()
        if not _addr:
            continue
        if not _re_email.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', _addr):
            continue
        _key = _addr.lower()
        if _key in _seen_emails:
            continue
        _seen_emails.add(_key)
        _clean_emails.append(_addr)
    email_to = ', '.join(_clean_emails)
    # Directive 2026-08-17: no override flags are accepted from the
    # dashboard or the partner API. The interpret step's decision is
    # authoritative. If ops needs to force a specific decision (e.g.
    # bypass a stale existing_match), that's done LOCALLY on Hetzner
    # via migration/local_override_profile.py - not through this route.
    decision, ex_key, d_type = _H._normalize_v1_decision(draft)

    # existing_match: no queue, no rebuild. Full-access seats reuse
    # the file at $0. Prometheus-only seats pay the retail Profile
    # price, then the file is granted to them (and their company).
    if decision == 'existing_match':
        _charge_user = (session.get('username') or user.get('username')
                        or '').strip()
        _em_subject = (draft.get('existing_match_display_name')
                       or spec.get('name') or 'profile')
        _em_pt = __import__('wallet').chat_pull_type(decision)
        _em_charged = _H._charge_existing_match_or_402(
            user, _charge_user, ex_key, _em_subject, _em_pt)
        if isinstance(_em_charged, tuple):
            return _em_charged
        url = _H._generate_presigned_profile_url(ex_key, expires_seconds=86400)
        try:
            from site_signup import grant_runs_to_user as _grant_runs
            _grant_runs(_charge_user, [ex_key])
        except Exception:
            traceback.print_exc()
        return jsonify({
            'success': True,
            'decision': 'existing_match',
            'reused_existing': True,
            'subject': _em_subject,
            's3_key': ex_key,
            'profile_name': ex_key.rsplit('/', 1)[-1] if ex_key else None,
            'download_url': url,
            'download_expires_seconds': 86400 if url else None,
            'run_id': None,
        })

    # ---- Subscriber IQ payload shaping (2026-08-25): the tracker's
    # own fields ride the spec under `subiq` (scrubbed + bounded), and
    # anchor_title pins the cross-product universe anchor. The window
    # was confirmed in the clarify flow; a draft that somehow arrives
    # without one gets a plain ask instead of a queue rejection.
    if decision == 'subscriber_iq':
        _subiq_d = (draft.get('subiq')
                    if isinstance(draft.get('subiq'), dict) else {})
        _siq_title = str(_subiq_d.get('title') or spec.get('name')
                         or '').split(' - ', 1)[0].strip()
        _siq_win = (_subiq_d.get('air_window')
                    if isinstance(_subiq_d.get('air_window'), dict)
                    else {})
        if not (_siq_win.get('start') and _siq_win.get('end')):
            return jsonify({
                'success': False,
                'guidance': True,
                'error': ('Need the measurement window first - send '
                          'the dates as 2025-11-16 to 2026-01-11 and '
                          'then approve.'),
            })
        try:
            _siq_season = int(_subiq_d.get('season'))
        except (TypeError, ValueError):
            _siq_season = None
        spec['subiq'] = {
            'title': _siq_title[:120],
            'platform': str(_subiq_d.get('platform') or '')[:60],
            'medium': ('movie'
                       if str(_subiq_d.get('medium') or ''
                              ).strip().lower() == 'movie'
                       else 'series'),
            'season': _siq_season,
            'genre': str(_subiq_d.get('genre') or '')[:80],
            'content_cadence': str(_subiq_d.get('content_cadence')
                                   or 'Weekly')[:40],
            'is_new_show': bool(_subiq_d.get('is_new_show')),
            'air_window': {'start': str(_siq_win['start'])[:10],
                           'end': str(_siq_win['end'])[:10]},
            'episode_dates': [str(d)[:10]
                              for d in (_subiq_d.get('episode_dates')
                                        or [])
                              if isinstance(d, str)][:60],
            'movie_scope': (str(_subiq_d.get('movie_scope'))[:20]
                            if _subiq_d.get('movie_scope') else None),
            'season_to_date': bool(_subiq_d.get('season_to_date')),
            'through_date': (str(_subiq_d.get('through_date'))[:10]
                             if _subiq_d.get('through_date') else None),
            'deliverable_label': str(_subiq_d.get('deliverable_label')
                                     or _siq_title)[:120],
            'addon_profile': bool(_subiq_d.get('addon_profile')),
        }
        spec['anchor_title'] = _siq_title[:120]
        spec['anchor_season'] = _siq_season

    # ---- A repeat approval is answered, never run twice (2026-11-17, Naomi at Sony: the team had started her
    # Subscriber IQ for her and it was delivered; the Approve & run card for the same brief was still open in
    # her tab, and one more click would have run and charged it a second time). Before anything is priced,
    # charged or queued, the order is compared with what is already running or was delivered in the last day
    # for this account or its company wallet, however that run was started (prometheus.repeat_approval). Nothing
    # in the request switches this off: a deliberate second run is an operator's, on the build server.
    _rpa_who = (session.get('username') or user.get('username') or '').strip()
    _rpa = __import__('prometheus.repeat_approval', fromlist=['Gate']).Gate(
        _H, user, _rpa_who, probe=bool(_C._pm_probe_caller() or _faults.is_probe() or _C._pm_is_probe_user(_rpa_who)))
    _rpa_reply = _rpa.check(decision, spec, draft, d_type, ex_key)
    if _rpa_reply is not None:
        return jsonify(_rpa_reply)

    # ---- Credit pricing + preflight (2026-08-21 Jenna directive: the
    # chatbot must charge and record usage like the dashboard and the
    # partner API do - including for unlimited users, whose history and
    # credits_used still increment). Price uses the SAME tier table as
    # the v1 API (_V1_CREDITS + ADDON_CUT_CREDITS per embedded cut), so
    # the charge always matches the estimate shown on the approve card.
    _charge_user = (session.get('username') or user.get('username')
                    or '').strip()
    _billable_cuts = __import__('wallet').billable_cuts(draft)
    # Dated quarter reads charge with the build, each at the cut price (Jenna 2026-11-18: "Charge $100 each, as
    # the chat used to say"); the list is the one the spec ships and every quote counts.
    _quarter_reads = len(__import__('wallet').billable_quarter_reads(draft, decision))
    # Credits follow the dollars' rule (wallet.order_credits): the tier, plus one cut's credits for each add-on
    # cut and each quarter read; a cuts-only derive has no tier; the Subscriber IQ's Profile IQ add-on rides the
    # tracker and adds nothing (Jenna 2026-11-18: "Make it 10 credits either way, to match the dollar price").
    price = __import__('wallet').order_credits(
        _H._V1_CREDITS, _H.ADDON_CUT_CREDITS, decision, len(_billable_cuts), _quarter_reads,
        cuts_only=(d_type == 'addon_cuts'), default=_H.CREDITS_PROFILE_ANALYSIS)
    _approve_pull_type = __import__('wallet').chat_pull_type(decision)
    # A card that shows less than this approval now takes (left open across a price change) is not run: the
    # reader drafts it again at the price as it stands, and is never charged more than they were quoted.
    _stale = _oq.stale_quote(_H, _charge_user, draft, decision, lambda v: _C._pm_usd_label(v, _charge_user))
    if isinstance(_stale, dict):
        return jsonify(_stale)
    if price > 0 and _charge_user and not _H.has_credits_for(
            _charge_user, price, pull_type=_approve_pull_type,
            addon_cuts=len(_billable_cuts), what=_plan.approve_what(decision, spec),
            quarter_reads=_quarter_reads):
        # The refusal is the wallet's one shared line (2026-11-17, Naomi at Sony read "This run costs $0. Wallet
        # balance is $11,000. Top up to keep going." on a funded plan): a plan allowance reads no price and no
        # balance, a pay-per-use wallet its real price, and nothing ever reads "costs $0".
        return jsonify(_plan.refusal_payload(
            _H, _charge_user, _approve_pull_type, len(_billable_cuts), price,
            lambda v: _C._pm_usd_label(v, _charge_user), quarter_reads=_quarter_reads)), 402

    # Thread the commissioning user + their exact ask onto the job so a
    # failed build's ops email can name who asked and quote what they
    # asked. The prompt round-trips on the draft (stamped at interpret);
    # fall back to the subject so the field is never empty.
    _approve_username = (session.get('username') or user.get('username')
                         or '').strip()
    _approve_prompt = (str(body.get('prompt')
                           or draft.get('user_prompt')
                           or draft.get('subject')
                           or spec.get('name') or '').strip())[:4000]
    payload = _seams.tag_queue({
        'user_email': user.get('email') or user.get('username') or 'unknown',
        'username': _approve_username or (user.get('username') or ''),
        'prompt': _approve_prompt,
        'run_avid': run_avid,
        'email_to': email_to,
        'spec': spec,
        'decision': decision,
    })
    if decision in ('derive_cut', 'time_shifted_refresh', 'cut_needs_parent'):
        payload['parent_s3_key'] = ex_key or ''
    if decision in ('derive_cut', 'cut_needs_parent'):
        payload['derive_type'] = d_type or ''
        # Behavioral/intersect cuts: the worker names the output
        # '{Parent} - {cut_label}.csv' and briefs the reasoning engine
        # with the cohort description (2026-08-20 lineage directive).
        # 2026-08-24: the display cut_label may carry a window echo
        # ('..., Aug 17 to Aug 20, 2026'); deliverable naming uses the
        # CLEAN label per the '{Subject} - {Cut}' rule, and the window
        # rides the cohort framing instead.
        _clean_cut_label = str(draft.get('cut_label_clean')
                               or draft.get('cut_label') or '').strip()
        if _clean_cut_label:
            payload['cut_label'] = _H._scrub_spec_text(
                _clean_cut_label, field='payload_cut_label',
                subject=spec.get('name') or '', max_len=160,
                single_line=True)
        if _clean_cut_label or draft.get('cut_label_guess'):
            # cut_label wins: the parent-link/promoter paths upgrade it
            # with the full residual cohort; the guess can be the
            # truncated intersect right-operand (2026-08-20 Marvel
            # TVOD fix).
            _cohort_desc = str(
                _clean_cut_label
                or draft.get('cut_label_guess'))[:300]
            _cut_win = str(draft.get('cut_window_label')
                           or draft.get('date_window_label') or '').strip()
            if _cut_win and _cut_win.lower() not in _cohort_desc.lower():
                _cohort_desc = (f"{_cohort_desc} - "
                                f"audience active during {_cut_win}")
            payload['cohort_description'] = _H._scrub_spec_text(
                _cohort_desc, field='payload_cohort_description',
                subject=spec.get('name') or '', max_len=400,
                single_line=True)
    if decision == 'time_shifted_refresh':
        payload['refresh_row_hypothesis'] = _H._scrub_spec_text(
            draft.get('refresh_row_hypothesis') or '',
            field='payload_refresh_row_hypothesis',
            subject=spec.get('name') or '', max_len=2000)
    # Quoted-estimate passthrough (2026-08-24 Jenna): the estimated
    # band shown on the approval brief rides to the engine host so the
    # delivered audience is verified against the quoted numbers.
    try:
        if decision in ('new_build', 'time_shifted_refresh',
                        'cut_needs_parent'):
            _q_est = _H._estimated_audience_range(spec.get('subject_raw_tu'))
            if _q_est:
                payload['quoted_estimate'] = _q_est
    except Exception as _fx:
        _faults.note('chat', _fx)

    # The half-hour duplicate rule (smclain, Trinity Tatum x2, 2026-09-29), now for every product an approval
    # can start: the order goes on record BEFORE the queue post, so a second click while the first is on its
    # way to the engine is caught and nothing is charged. A profile window that moved materially still builds.
    _rpa_reply = _rpa.hold()
    if _rpa_reply is not None:
        return jsonify(_rpa_reply)

    try:
        import requests as _requests
        try:
            from flask import g as _g_tr
            if getattr(_g_tr, '_pm_trace_id', ''):
                payload['trace_id'] = _g_tr._pm_trace_id
        except Exception as _fx:
            _faults.note('chat', _fx)
        _t_queue = time.monotonic(); _C._pm_ask_stage('approve_gates', ms=int((_t_queue - _t_gates) * 1000))   # the decision, the repeat gate, the price and the quote (2026-11-24)
        # What the reply says follows the artifact (2026-11-10, prometheus.order_truth): a run id means it
        # started; a refusal is tried once more and then answered plainly; a timeout or a reply with no run id
        # is never called running. The calm promise is not made for an order that did not start.
        queue_resp, _q_state, _q_detail = _order_truth.queue_post(lambda: _requests.post(
            f"{_H.SYNTH_QUEUE_URL}/synth/queue",
            json=payload, timeout=30,
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET,
                     'Content-Type': 'application/json'},
        ), on_retry=lambda d: print(f"[synth_chat_run] {_order_truth.RETRY_LINE} ({d[:200]})"))
        if queue_resp is None:
            _H._chatbot_error_email(
                'brief-chat/approve',
                f'order {_q_state}: {_H._clean_queue_error_text(_q_detail)[:400]}',
                tb=str(_q_detail or '')[:2000] or '(empty queue reply)')
            if _q_state == 'not_started':
                _rpa.not_started()   # it did not start: sending it again must work
            return jsonify(_order_truth.not_started_payload(spec.get('name')) if _q_state == 'not_started'
                           else _order_truth.unconfirmed_payload(spec.get('name')))
        _C._pm_ask_stage('approve_queue_post', t0=_t_queue)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/approve', e)
        _rpa.not_started()
        return jsonify(_order_truth.not_started_payload(spec.get('name')))

    # Queue-returned-200-without-run_id parity fix (2026-08-19, Jessie
    # fix #4 applied to session route). If the queue accepted the POST
    # but didn't hand back a run_id, the caller has no way to poll and
    # no way to receive the deliverable. Session users don't get a
    # credit refund (session credit model differs from v1), but they
    # DO get the same 502 error instead of `success: True, run_id:
    # null` which is worse than useless.
    _session_run_id = queue_resp.get('run_id')
    if not _session_run_id:
        try:
            print(f"[synth_chat_run] queue returned 200 with no run_id "
                  f"payload={str(queue_resp)[:400]!r} - returning 502")
        except Exception as _fx:
            _faults.note('chat', _fx)
        _H._chatbot_error_email(
            'brief-chat/approve',
            'queue accepted the run but returned no run_id: '
            + str(queue_resp)[:400],
            tb='(queue reply inspection)')
        return jsonify(_order_truth.unconfirmed_payload(spec.get('name')))
    _rpa.started(_session_run_id)   # the hold now points at the run (prometheus.repeat_approval)
    # The launch is on record with the thread it was asked in, so the server posts the finished line there
    # itself, browser or no browser (2026-11-10, prometheus.build_ready).
    try:
        from prometheus import thread_scope as _tsc_bl
        _build_ready.note_launch(
            _C._pm_s3_put_json, run_id=_session_run_id, user=_approve_username,
            thread_id=(str(body.get('thread_id') or '') or _tsc_bl.current_thread_id() or _build_ready.active_thread(lambda: _C._load_threads_index(_approve_username), api=_api_threads.is_api_request())),
            subject=spec.get('name'), decision=decision, engine_user=payload.get('user_email') or '',
            via=('api' if _api_threads.is_api_request() else 'dashboard'))
    except Exception as _fx:
        _faults.note('chat', _fx)
    # A build started from a question answers it when it lands: the launch keeps the question (prometheus.question_first)
    if _origin_keep and not (_C._pm_probe_caller() or _faults.is_probe() or _C._pm_is_probe_user(_approve_username)):
        _question_first.keep(_C._pm_stash_pending_question, _approve_username, spec.get('name'), _origin_keep, str(body.get('thread_id') or ''))

    # Charge AFTER the queue accepted the run so the usage-history entry
    # carries the real run_id (same check -> queue -> charge order the
    # dashboard IQ routes use). consume_credit records credits_used +
    # credit_usage_history even for unlimited users.
    _t_after = time.monotonic(); _, _credits_left = _H.check_user_credits(_charge_user)
    if price > 0 and _charge_user:
        # Post-queue and failure-tolerant as before, now on its own thread so the 13.7 MB users.json write is off the person's wait; the response reports the pre-charge balance less the price (brief_speed, 2026-11-24)
        def _charge():
            if not _H.consume_credit(
                    _charge_user,
                    description=(f"Chatbot Profile IQ [{decision}] - "
                                 f"{spec.get('name', 'profile')}"),
                    job_id=str(_session_run_id),
                    pull_type=_approve_pull_type,
                    credits_used=price,
                    quarter_reads=_quarter_reads,
                    addon_cuts=len(_billable_cuts)):
                print(f"[synth_chat_run] WARNING: post-queue credit charge failed user={_charge_user} run={_session_run_id} price={price} - run continues, usage NOT recorded")
        _bsp = __import__('prometheus.brief_speed', fromlist=['charge_later']); _bsp.charge_later(_charge, label=f"run={_session_run_id}")
        _credits_left = _bsp.remaining_after(_credits_left, price)

    # Cross-session memory (2026-08-27, Jenna): the approved build's
    # confirmed window and any named markets become the user's
    # last-used values, so the next window clarify can lead with them.
    try:
        _mem_dr = draft.get('date_range') \
            if isinstance(draft.get('date_range'), dict) else {}
        _mem_win = ({'start': _mem_dr.get('start'),
                     'end': _mem_dr.get('end')}
                    if _mem_dr.get('start') and _mem_dr.get('end')
                    else None)
        _mem_region = [str(c.get('dma') or c.get('label') or '').strip()
                       for c in (draft.get('addon_cuts') or [])
                       if isinstance(c, dict)
                       and str(c.get('type') or '') == 'dma']
        _C._pm_remember_ask_build(
            (session.get('username') or '').strip(),
            f"build: {spec.get('name', 'profile')}",
            subject=spec.get('name'), window=_mem_win,
            region=[r for r in _mem_region if r])
    except Exception as _fx:
        _faults.note('chat', _fx)

    _C._pm_ask_stage('approve_after', t0=_t_after); return jsonify({   # the balance, the charge hand-off, the memory note (2026-11-24)
        'success': True,
        'decision': decision,
        'run_id': _session_run_id,
        'pending_position': queue_resp.get('pending_position'),
        'subject': spec['name'],
        'brand_category': spec['brand_category'],
        'run_avid': run_avid,
        'parent_s3_key': ex_key or None,
        'derive_type': d_type or None,
        'email_to': email_to,
        'credits_charged': price,
        'credits_remaining': _credits_left,
    })


__all__ = ['api_synth_chat_rebind_window', '_pm_rescue_unverified_draft', '_PM_RECENT_BUILDS_KEY', '_pm_recent_build_guard', '_pm_seat_reads_included', '_pm_cut_usd', '_PM_NO_DRAFT_REPLY', 'api_synth_chat_approve', '_pm_approve_core']
