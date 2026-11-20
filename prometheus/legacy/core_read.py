"""The generated read: context bind, research, synthesis, verify and bank.

Extracted from prometheus/legacy/chat.py (rebuild stage 5, 2026-11-20) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the rest of the legacy chat module through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names. ``_C.globals()`` is chat.py's own
namespace, the one the lanes this code hands it to expect. The suites read
the moved text through scripts/_pm_legacy_view.legacy_view(), which splices
each block back at its marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
from prometheus import file_names as _file_names, no_build as _no_build, study_record as _study_record
import uuid
import threading
import traceback
from prometheus import figures as _fig
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from prometheus import page_default as _page_default
from prometheus import creator_fit as _creator_fit
from prometheus import audience_referent as _audience_referent
from prometheus import bpiq_wizard as _bpiq_wiz
from prometheus import matrix_read as _matrix
from prometheus import question_universe as _question_universe
from prometheus import ledger_gate as _ledger_gate, probe_files as _probe_files, cut_list as _cut_list, primary_profile as _primary_profile
from prometheus import promise_truth as _PT, read_repair as _read_repair, grid_facts as _grid_facts
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import named_subject as _ns_mod
from prometheus.legacy.jobs import (  # noqa: E402,F401
    api_synth_chat_status,
    _pm_job_owner_ok,
    _pm_read_status_write,
    api_synth_chat_read_status,
    _pm_deck_status_write,
    _pm_bpiq_status_write,
    _pm_jiq_status_write,
    _pm_fw_status_write,
    _pm_aiq_status_write,
    api_synth_chat_deck_status,
    api_synth_chat_bpiq_status,
    api_synth_chat_jiq_status,
    api_synth_chat_fw_status,
    api_synth_chat_aiq_status,
    api_synth_chat_active_runs,
    api_synth_chat_health,
)
from prometheus.legacy.land import (  # noqa: E402,F401  (I4: landing settles the promise; the calm hold on a repeat)
    _pm_append_read_to_history, _pm_append_deck_to_history, _pm_settle_ask, _pm_gate_host, _pm_calm_hold_response, _pm_on_it_line)
from prometheus.legacy.jobs import (  # noqa: E402,F401  (the last-file stash, 2026-10-21)
    _PM_LAST_FILE_PREFIX, _pm_file_stash_write, _pm_file_stash_read)
from prometheus.legacy.watch import (  # noqa: E402,F401
    _PM_WATCH_FLAGGED,
    _PM_USER_BLOCK_CACHE,
    _PM_USER_BLOCK_LOCK,
    _pm_user_block,
    _pm_catalog_block,
    _pm_ask_log_user,
    _pm_probe_caller,
    _pm_is_probe_user,
    _PM_COMMON_IDENTITY_WORDS,
    _pm_thread_confirmed_page,
    _pm_page_confirm_mark,
    _pm_page_confirmed_recently,
    _pm_watch_flag,
    _pm_record_held_reply,
    _pm_gate_options,
    _pm_open_status_line,
    _pm_price_table,
    _pm_usd,
    _pm_usd_label,
    _pm_money_symbol,
    _pm_viewership_read_price,
    _pm_derived_read_price,
    _pm_viewership_verb,
    _pm_is_viewership_series_ask,
    _pm_is_viewership_ask,
    _pm_consumption_subject,
    _pm_page_clarify_subject,
    _pm_panel_price_label,
    _pm_pending_q_tokens,
    _pm_stash_pending_question, _pm_pop_pending_question,
    _pm_safe_user,
    _pm_s3_json,
    _pm_s3_put_json,
    _PM_REPORT_ASK_RE,
    _pm_looks_report_ask,
)
from prometheus import read_recovery as _rr

# <<moved>>
# <<block: 1>>
def _pm_generate_metrics_response(user, text, history, metric_request=None,
                                  anchors_block='', charge_done=False,
                                  ctx=None, digest_block='',
                                  prefer_catalog=False, async_fresh=None,
                                  bind_subject=None, bind_cohort=None,
                                  panel_confirm=None,
                                  switch_page=None, inline_job_id=None,
                                  force_local=False, resume_base=None):
    """Reasoned measurement read (2026-08-26, Jenna): a concrete
    number for a digitally observable ask the open data does not
    cover, or the read for a sub-cohort the open data does not
    directly carry (2026-08-27, Paw Patrol kids-4-6). The insights
    ledger is the consistency surface: identical asks replay the
    stored reply verbatim, prior published numbers ride the prompt as
    binding constraints, and every delivered read persists back to
    the ledger before it ships.

    Base-profile boundary (2026-08-27, Jenna): a generated read only
    exists as a derivation of a subject already pulled (the open
    profile, a catalog profile, or a Subscriber IQ run). A subject
    with no base anywhere gets the steer-to-build reply, never
    numbers.

    `metric_request` comes from the analysis pass (action=
    generate_metrics); the direct no-context path passes None and the
    subject resolves from the question. `charge_done` skips the credit
    preflight when the caller already ran it. `digest_block` carries
    the open profile's digest so sub-cohort reads stay bound to the
    covering first-party rows."""
    import prometheus_analysis as pma
    import insights_ledger as il
    _C._pm_ask_hint(route='reasoned_metrics')
    _pm_user = (session.get('username') or user.get('username') or '').strip()
    _pm_ppu = _C._pm_usage_extras(user)
    # A sizing / cross confirm landed on generate (2026-10-21): charge
    # the quoted price and run the deterministic lane, never a compiled
    # read. Where-watched is never priced (service.py, the free lane).
    if isinstance(panel_confirm, dict) and str(panel_confirm.get('kind') or '') in ('sizing', 'cross'):
        try:
            _lane = __import__('prometheus.priced_read', fromlist=['service_turn']).service_turn(
                text, user=user, ctx=ctx, history=history, panel_confirm=panel_confirm)
        except Exception:
            traceback.print_exc(); _lane = None
        if _lane:
            return jsonify(_lane)
    # Talk on an open file is session-metered (2026-09-09 Jenna); a put-together
    # read is a priced custom ask, gated below (2026-10-21). `charge_done` is kept for callsite compatibility.
    mr = metric_request if isinstance(metric_request, dict) else {}
    subj_hint = _cut_list.hint(text, mr.get('subject'))   # one of the categories the words list is a cut, never the guess the read starts from (2026-11-11)
    # Confirmed memory referent (2026-08-27, Jenna: "know context between sessions and threads"):
    # the widget's confirm chip sends the remembered subject explicitly; it resolves the base directly and rides the consult below so a banked read replays instantly.
    bind_subject = str(bind_subject or '').strip() or str(getattr(__import__('flask').g, '_pm_retry_bound', '') or '').strip()   # I4b: the retry's bound subject
    bind_subject = _page_default.quoted_subject(panel_confirm, bind_subject) or bind_subject   # the subject a quote named is the one the Approve runs on (2026-10-24)
    bind_subject = bind_subject or _subject_rules.pinned_bind_for(text, history, str(((ctx or {}).get('primary') or {}).get('name') or ''))   # the thread's own subject, never a which-one question from cross-session memory (Emmet Riley 2026-10-26)
    bind_subject = __import__('prometheus.named_title', fromlist=['rebound']).rebound(bind_subject, text, panel_confirm)   # a subject a lane bound is the title the words name, never a franchise mate of it (2026-11-19)
    bind_cohort = str(bind_cohort or '').strip()
    # A bare "this audience" that starts work binds to the audience on screen (or the thread's) and is confirmed in one line first; the confirmed name rides into the job (2026-10-26, Mark Valdez)
    # A resumed read (the read worker) already passed this gate with the screen it had; it runs on its pinned base and never asks again (Jessie 2026-11-04)
    _resume_pin = str((resume_base or {}).get('subject') or (resume_base or {}).get('s3_key') or '').strip() if isinstance(resume_base, dict) else ''
    try:
        _arg = _audience_referent.gate(text, bind_subject, ctx=ctx, thread=getattr(__import__('flask').g, '_pm_thread_binding', None), history=history,
                                       confirmed=str(getattr(__import__('flask').g, '_pm_referent_confirmed', '') or ''), pinned=_resume_pin)
    except Exception as _fx:
        _faults.note('chat', _fx); _arg = None
    if _arg:
        _C._pm_ask_hint(route='audience_referent', outcome=_arg.pop('outcome', ''), subject=_arg.get('subject'))
        return jsonify(_arg)
    if bind_subject and not subj_hint:
        subj_hint = bind_subject
    # I2 (2026-10-20): a re-run accepted on its card is charged at the quoted price before anything is generated
    _rr_err = __import__('prometheus.priced_read', fromlist=['charge_rerun']).charge_rerun(_pm_user, subj_hint, _H.consume_credit)
    if _rr_err is not None:
        if _rr_err:
            _C._pm_ask_hint(outcome='panel_out_of_credits', subject=_rr_err.get('subject'))
            return jsonify(_rr_err)
        charge_done = True
    # A consumption count typed in lowercase ("how many people read the walsh family book series
    # in the us last year?") names its own subject; the capitalized-run guess misses it and the ladder
    # used to ask "Do you mean for <memory>?" (Jenna 2026-10-08). Read the object of the consumption verb instead.
    if not subj_hint and _pm_is_viewership_ask(text):
        subj_hint = _pm_consumption_subject(text)
    # WHICH ONES? (2026-10-02 Jenna: "it should have asked him which 3 influencers he was talking
    # about then actually given him the answer"). An ask that points at "these three creators" and
    # names none of them stops here with the question, before any base lookup, model call or offer.
    # A subject from a model or extractor that is only ordinary words ("Three Actually Influence Product Purchases and") is not a subject.
    try:
        from prometheus import referents as _refs
        if subj_hint and not _refs.plausible_subject(subj_hint):
            print(f"[pm-referent] dropped implausible subject hint "
                  f"{subj_hint!r}")
            subj_hint = ''
        if not bind_subject and not isinstance(panel_confirm, dict):
            _unres = _refs.detect_unresolved(text, history, ctx)
            if _unres:
                _C._pm_ask_hint(route='referent_clarify',
                             outcome='asked_which')
                return jsonify(_refs.clarify_payload(_unres, text))
    except Exception:
        traceback.print_exc()
    # HARD GATE (2026-08-27, Jenna): resolve the base that authorizes
    # this generated read BEFORE anything is generated. No base
    # anywhere = no numbers; the subject needs its 5-credit Total
    # Universe build first.
    base = None
    # A resumed read (the read worker, a recovery) runs on the base it
    # was started on, pinned on the job record with its screen data, and
    # never re-resolves without that screen (2026-11-04: nine of Liz's
    # Digital Journey reads re-resolved to no base on the worker and
    # landed the $100 offer line as their result).
    _pinned_base = bool(isinstance(resume_base, dict) and (resume_base.get('subject') or resume_base.get('s3_key')))
    if _pinned_base:
        base = dict(resume_base)
        if str(base.get('source') or '') == 'view' and not isinstance(base.get('view_context'), dict) \
                and isinstance((ctx or {}).get('view_context'), dict):
            base['view_context'] = ctx['view_context']
        if not subj_hint:
            subj_hint = str(base.get('subject') or '')
    if bind_subject and not base:
        try:
            base = _C._pm_generation_base(
                bind_subject, f"{bind_subject} {bind_cohort}".strip(),
                ctx=ctx, prefer_catalog=True, bound=True)
        except Exception:
            traceback.print_exc()
    if not base:
        try:
            base = _C._pm_generation_base(subj_hint, text, ctx=ctx,
                                       prefer_catalog=prefer_catalog)
        except Exception:
            traceback.print_exc()
    if not base and history:
        # Anaphoric ask (2026-08-27, Jenna's white-space ask): "this
        # audience" names no subject; the thread does. Resolve the
        # referent from recent turns and bind that base.
        try:
            if pma.ask_is_anaphoric(text):
                _hist_text = _C._pm_history_bind_text(history)
                if _hist_text:
                    base = _C._pm_generation_base(subj_hint, _hist_text,
                                               ctx=ctx,
                                               prefer_catalog=True)
        except Exception:
            traceback.print_exc()
    if base and not _pinned_base and _page_default.brief_on_own_base(text):
        base, digest_block = None, ''   # nor a file reached through the brief's own label (2026-11-17)
    if base and not _pinned_base and str(base.get('source') or '') in ('page', 'catalog') and not str(base.get('s3_key') or '').startswith('subiq:'):
        _stands, _why = _page_default.base_stands(text, history, base, bind_subject, normalize=_H._normalize_for_match,
                                                  stop_tokens=_C._PM_CLARIFY_STOP_TOKENS, brand_named=_C._pm_page_brand_named, ask_user=_pm_ask_log_user(''))
        if not _stands:   # the open profile is context, never a default subject; a name the reader said no to is no base (2026-10-06, 2026-10-24)
            print(f"[pm-base] {base.get('source')} base {base.get('subject')!r} dropped ({_why})")
            switch_page, base = (switch_page or str(base.get('subject') or '')), None
    if not _pinned_base:   # a platform the question adds scopes the subject on record; it never replaces it (2026-11-11, Armine: 'YouTube original content' was quoted and run on everyone who uses YouTube)
        base, digest_block, subj_hint, bind_subject, bind_cohort, switch_page = __import__('prometheus.platform_scope', fromlist=['apply_to_read']).apply_to_read(text, history, il, base, digest_block, subj_hint, bind_subject, bind_cohort, switch_page)
    # The anchor is reasoned, not inherited (Jenna 2026-11-04): a profile
    # of a PERSON (ACTOR, MUSICIAN/BAND, creator ...) counts the people with
    # digital touchpoints to that person. A question about viewers of the
    # person's films or titles, their Prime Video share or total viewers is
    # a title-viewing read: it never takes the person's profile as its
    # base, from the page or from the catalog. The read opens by naming
    # the universe it used (prometheus.page_default.title_universe_label).
    if base and str(base.get('source') or '') in ('page', 'catalog') \
            and str(base.get('s3_key') or '').lower().endswith('.csv'):
        try:
            _pcat_b = _page_default.profile_category(base.get('s3_key'), base.get('subject'), _C._profile_catalog_for_chat())
            if _page_default.person_page_for_title_ask(text, _pcat_b, base.get('subject')):
                print(f"[pm-base] {base.get('source')} base {base.get('subject')!r} is a person ({_pcat_b}); "
                      f"the ask is about viewers of titles; the profile is not the anchor")
                _title_person = str(base.get('subject') or '').split(' - ')[0].strip()
                if _title_person and not bind_subject:
                    bind_subject = _title_person   # the read is still about this person's titles; never the page by default
                base = None
                digest_block = ''   # the profile's rows never ride the prompt as the anchor of a title read
        except Exception:
            traceback.print_exc()
    # The base is the universe the question names (Jenna 2026-11-06): a
    # page or library base the question did not name never carries a
    # question that named its own universe ("potential Paw Patrol
    # consumers", "when people start holiday toy shopping"). The read runs
    # on that universe (US people) below; the page's rows never ride the
    # prompt as its anchor, and no "On <page> instead" chip is offered,
    # since the page was never a candidate.
    _qu_page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    try:
        _qu = _question_universe.question_universe(text, _qu_page)
    except Exception as _fx:
        _faults.note('chat', _fx); _qu = _question_universe.NONE
    _qu_bound = bool(bind_subject) and _H._normalize_for_match(str(bind_subject).split(' - ')[0]) == \
        _H._normalize_for_match(str((base or {}).get('subject') or '').split(' - ')[0])   # a chip or an approved quote chose this base: it stands
    if base and not _pinned_base and not _qu_bound and str(base.get('source') or '') in ('page', 'catalog') \
            and not str(base.get('s3_key') or '').startswith('subiq:') \
            and _question_universe.names_other_universe(text, base.get('subject'), page=_qu_page):
        print(f"[pm-base] {base.get('source')} base {base.get('subject')!r} dropped: the question names its own "
              f"universe ({_qu.label!r}, base {_qu.base!r})")
        base = None
        digest_block = ''
    elif base and _qu_bound and not base.get('bound'):
        base = dict(base, bound=True)   # the reader's own choice rides to the read core and the resume record
    if not base:
        # RESOLUTION LADDER, last rung (2026-08-27, Jenna: "if I go
        # back and ask for toy white space it can say do you mean for
        # paw patrol viewers parents"). Thread and session referents
        # bind silently above; a CROSS-SESSION memory hit never binds
        # silently - it asks a grounded confirm with the remembered
        # referent(s) as chips. Only for underspecified asks: an ask
        # that names its own subject keeps the steer-to-build reply.
        # A campaign ask never disambiguates between profiles
        # (2026-09-30 Jenna). Reaching this rung means no campaign is
        # on screen: say what to open instead of offering audience
        # names from memory.
        _lr_funnel = False
        try:
            _lr_funnel = bool(
                _C._PM_FUNNEL_ASK_RE.search(str(text or ''))
                and not str(subj_hint or '').strip()
                and not str(pma.guess_subject_from_text(text)
                            or '').strip())
        except Exception:
            _lr_funnel = False
        if _C._PM_CAMPAIGN_ASK_RE.search(str(text or '')) or _lr_funnel:
            _C._pm_ask_hint(outcome='campaign_clarify')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('Which campaign is this about? Open it in '
                          'the Attribution IQ tab and ask from there, '
                          'or give me the campaign name and its '
                          'window.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        try:
            import prometheus_memory as pmm
            _named = (subj_hint
                      or str(pma.guess_subject_from_text(text)
                             or '').strip())
            # A question that names its own universe outright has its
            # subject (Jenna 2026-11-06): cross-session memory never names
            # it, never binds it and never asks which remembered audience
            # it meant. The no-base path below quotes the named universe.
            if _qu.kind == 'named' and _qu.explicit:
                _named = _named or _qu.label
            if _pm_user and not _named:
                _refs = pmm.recent_referents(_pm_user, k=2)
                _opts = []
                for _r in _refs:
                    _lab = pmm.referent_label(_r)
                    if _lab:
                        _opts.append({'label': _lab,
                                      'subject': _r['subject'],
                                      'cohort': _r.get('cohort')})
                # The ask names one of the remembered subjects: bind it
                # (never offer it back as a chip). 2026-10-08. The SUBJECT
                # has to be named: a remembered cohort is often the earlier
                # question's own words ('Paw Patrol toy buyers in the Starz
                # audience' bound Starz to a question about Paw Patrol toy
                # buyers, 2026-11-06), so the cohort alone never binds.
                _hit = [o for o in _opts if _ns_mod.page_named(
                    text, o['subject'], _H._normalize_for_match,
                    _C._PM_CLARIFY_STOP_TOKENS)]
                if len(_hit) == 1 and not bind_subject:
                    _C._pm_ask_hint(route='memory_bind',
                                 subject=_hit[0]['subject'])
                    return _pm_generate_metrics_response(
                        user, text, history, metric_request=metric_request,
                        prefer_catalog=True,
                        bind_subject=str(_hit[0]['subject']),
                        bind_cohort=str(_hit[0].get('cohort') or ''),
                        switch_page=switch_page)
                if _opts:
                    from prometheus import referents as _rf2
                    _mc = _rf2.memory_confirm_payload(text, _opts, history)   # never the same confirm twice in a row (2026-10-15)
                    _C._pm_ask_hint(outcome='memory_confirm' if _mc.get('memory_confirm') else 'memory_confirm_released')
                    return jsonify(_mc)
        except Exception:
            traceback.print_exc()
    # TWO-BASE READS (2026-09-28, Phase 3): a comparison / overlap ask
    # naming two resolvable audiences gets both digests. The second
    # side rides the digest bundle's COMPARISON PROFILE rendering, and
    # its own published measurements ride the anchors block so both
    # sides stay consistent with what each already shipped.
    try:
        _two = None if _page_default.brief_on_own_base(text) else _C._pm_two_base_detect(text, ctx)
    except Exception:
        _two = None
    if _two:
        _b1, _b2 = _two['pair']
        if base is None and _b1.get('s3_key') \
                and not str(_b1['s3_key']).startswith('subiq:'):
            base = {'subject': _b1['subject'],
                    's3_key': _b1['s3_key'], 'source': 'catalog'}
        _second = None
        if base is not None:
            for _cand in (_b1, _b2):
                if _H._normalize_for_match(_cand['subject']) != \
                        _H._normalize_for_match(base.get('subject') or ''):
                    _second = _cand
                    break
        if base is not None and _second and _second.get('s3_key') \
                and not str(_second['s3_key']).startswith('subiq:') \
                and not str(base.get('s3_key') or '').startswith('subiq:') \
                and not digest_block:
            try:
                _pc2 = {'primary': {'s3_key': base['s3_key'],
                                    'name': base.get('subject') or ''},
                        'extras': [{'s3_key': _second['s3_key'],
                                    'name': _second['subject']}]}
                digest_block = pma.get_digest_bundle(
                    _H.s3_client, _H.S3_BUCKET, _pc2)[0]
                _led2 = il.consult(subject=_second['subject'])
                if _led2.get('block'):
                    anchors_block = (
                        f"{anchors_block or ''}\n\n"
                        f"PUBLISHED MEASUREMENTS - "
                        f"{_second['subject']}\n{_led2['block']}").strip()
                print(f"[pm-twobase] paired read: "
                      f"{base.get('subject')!r} x "
                      f"{_second['subject']!r}")
            except Exception:
                traceback.print_exc()
    panel_charge = None
    _view_base = __import__('prometheus.view_base', fromlist=['is_view_base']).is_view_base(base)   # the open product's own numbers: a lookup, never priced (2026-10-22)
    if _view_base and not isinstance(base.get('view_context'), dict):
        base['view_context'] = (ctx or {}).get('view_context')   # the read core reads the screen and its window from the base (2026-10-26); a resumed read already carries it
    # A figure an Attribution IQ campaign holds is broken down from the campaign's own assets, never generated
    # (2026-10-26, Alexia's 67,729 read the same-title Profile IQ file and printed invented asset groups under its
    # Jan to Dec 2025 window). Runs here too so a read reached without the service door, or resumed on the read
    # worker with no screen, still answers from the campaign.
    try:
        _cd_raw = __import__('prometheus.campaign_drilldown', fromlist=['answer']).answer(
            text, _pm_user, ctx, hint=f"{bind_subject} {subj_hint} {(base or {}).get('subject') or ''}")
    except Exception:
        traceback.print_exc(); _cd_raw = None
    if _cd_raw:
        _C._pm_ask_hint(route='campaign_drilldown', outcome='answered', subject=_cd_raw.get('subject'))
        return jsonify(_cd_raw)
    if not base:
        # A QUESTION about a never-pulled subject is a custom read
        # (Jenna 2026-10-08: "all custom asks are just 500 ... take out
        # force running a profile"); the 2026-09-24 build-first flow is
        # retired, the stash + follow-through stay for approved runs.
        _bf_subj = (subj_hint or pma.guess_subject_from_text(text)
                    or '').strip()
        # A subject bound upstream (memory, catalog, the open page)
        # was already resolved; only a free guess is screened.
        if _bf_subj and _bf_subj != str(bind_subject or '').strip() \
                and _page_default.not_a_subject(_bf_subj, text, _C._pm_plausible_subject, history):
            _bf_subj = ''   # ordinary words, a prose fragment ("Target. Among"), or a place in a list of places (2026-10-24)
        # Entity core (2026-10-02 S6): 'Appeal of the Spiderwick
        # Franchise' is a question about Spiderwick, not a subject.
        # Collapse to the entity; when the library already carries
        # it, answer on that base instead of offering a build.
        _bf_core, _bf_lib = _C._pm_entity_core_bind(_bf_subj)
        if _bf_lib and not bind_subject:
            _C._pm_ask_hint(route='entity_core_bind', subject=_bf_lib)
            # The metric request's own subject is the wrapper; hand
            # the library subject down so the base resolves on it.
            _bf_mr = (dict(mr, subject=_bf_lib)
                      if isinstance(metric_request, dict) else None)
            return _pm_generate_metrics_response(
                user, text, history, metric_request=_bf_mr,
                prefer_catalog=True, bind_subject=_bf_lib,
                bind_cohort=bind_cohort, switch_page=switch_page)
        _bf_subj = _bf_core
        # The build-first offer that used to sit here ("Prometheus needs
        # an initial data cut of X ... Approve the run") is retired
        # (Jenna 2026-10-08: "take out force running a profile and just
        # charge 500 for the custom ask"). A question about a subject
        # with no base is a custom read, priced below.
        # PANEL RESEARCH REPORT (2026-09-14, Jenna, verbatim: "before
        # it puts together any report outside of a simple analysis of
        # what already exists it should charge them. if they request
        # something that doesnt have a set price it should charge
        # $550."). A question about a subject with no base anywhere is
        # a full put-together read, not a lookup: quote the price
        # first, charge on confirm, THEN generate. Reading back a
        # report that already delivered does not re-quote the $550 -
        # the identical-ask replay below fires before any quote and
        # bills as metered usage (2026-09-14: nothing is ever free).
        _qs = _page_default.quoted_subject(panel_confirm, bind_subject)
        subj_name = _qs or (subj_hint or pma.guess_subject_from_text(text) or '').strip()
        if subj_name and not _qs and _page_default.not_a_subject(subj_name, text, _C._pm_plausible_subject, history):
            subj_name = ''   # ordinary words, a prose fragment, or a place in a list of places ("at Amazon, Walmart and Target"), 2026-10-24
        # The universe the question names is the subject the offer quotes
        # and the read runs on (Jenna 2026-11-06): "potential Paw Patrol
        # consumers" over the router's paraphrase of the question ("When
        # People Start Christmas Holiday Toy Shopping"); a population
        # ("US households", "when people start ...") is the whole US unless
        # the words also name a title or brand as the thing consumed.
        if not _qs and _qu.kind == 'named':
            subj_name = _question_universe.subject_for(_qu, subj_name, text)
        subj_name = subj_name if _page_default.is_everyone(subj_name) else _C._pm_entity_core_bind(subj_name)[0]
        if subj_name:
            try:
                _led_nb = il.consult(subject=subj_name, question=text)
                _exact_nb = (_led_nb or {}).get('exact')
                if _exact_nb and _C._pm_replay_rejected_in_thread(_exact_nb, history):
                    _exact_nb = None   # the reader already said no to these words (2026-11-04)
                if _exact_nb and _exact_nb.get('reply'):
                    _C._pm_ask_hint(outcome='answered', subject=subj_name)
                    # Served from the library: metered, never free
                    # (2026-09-14).
                    _C._pm_meter_answer('replay_read', _pm_ppu)
                    _C._pm_remember_ask(_pm_user, text, subject=subj_name,
                                     cohort=_exact_nb.get('cohort'),
                                     ledger_key=_exact_nb.get('k'),
                                     route='replay')
                    _nb_chips = [c for c in list(_exact_nb.get('followups')
                                                 or [])[:3] if c != pma.CSV_OFFER_CHIP]
                    _C._pm_csv_point(subj_name, text,
                                  _exact_nb.get('family'))
                    # the file rides the answer (Bria 2026-11-04: the
                    # chip this path offered had nothing behind it)
                    _nb_file = _C._pm_replay_file(_exact_nb, text, _pm_user, _nb_chips)
                    return jsonify({
                        'success': True, 'action': 'answer',
                        'reply': _exact_nb['reply'],
                        'followups': _nb_chips,
                        'offer_deck': False, 'deck_angle': None,
                        'profile': subj_name,
                        **_nb_file})
            except Exception:
                traceback.print_exc()
        if not subj_name and _bf_subj:
            subj_name = _bf_subj
        # The universe the question names is already how a reader writes
        # it; the binder's reading of the words (the Reader's paraphrase,
        # 'When People Start Christmas Holiday Toy Shopping') never
        # replaces it (2026-11-06: that paraphrase reads as a question
        # fragment at the price gate and drew 'Which subject should this
        # be on?').
        _qu_keep = bool(_qu.kind == 'named' and _qu.explicit and subj_name
                        and _question_universe.same_name(subj_name, _qu.label))
        if not _qu_keep:
            subj_name = _subject_rules.display_for(subj_name, text, page=str(((ctx or {}).get('primary') or {}).get('name') or ''))   # I1: never 'Unique', never a wrapper
        _is_derived = str((metric_request or {}).get('metric_family') or '') == 'dimension split' or (isinstance(panel_confirm, dict) and str(panel_confirm.get('kind') or '') == 'derived')
        # I2 (2026-10-20, Bria on The Office US): a different metric or
        # breakdown asked back at a landed read is the derived read on
        # that subject at the derived-read row, never a fresh custom read
        _cont_pc = getattr(__import__('flask').g, '_pm_continuation', None) or {}
        _param_change = (str(_cont_pc.get('kind') or '') == 'parameter_change'
                         and str((_cont_pc.get('deliverable') or {}).get('kind') or '') == 'read')
        if _param_change:
            _is_derived = True
            _pc_params = dict(_cont_pc.get('params') or {})
            _pc_params['breakdown'] = {'surface': 'device', 'screen': 'device', 'devices': 'device'}.get(str(_pc_params.get('breakdown') or '').lower(), _pc_params.get('breakdown'))
            if isinstance(metric_request, dict):
                metric_request = dict(metric_request, dimension=metric_request.get('dimension') or _pc_params.get('breakdown') or '', bound_series=metric_request.get('bound_series') or [{'from': 'thread'}])
            else:
                metric_request = {'dimension': _pc_params.get('breakdown') or '', 'bound_series': [{'from': 'thread'}], 'metric_family': 'dimension split'}
        if subj_name and not _view_base and (_is_derived or pma.panel_report_eligible(text, subj_name)):
            # One gate for every priced read (prometheus/priced_read.py,
            # Jenna 2026-10-21): viewership is $500 a year, a breakdown of
            # a delivered figure $100, everything else the custom ask
            # ($100 first on a subject in the thread, $50 after). The offer
            # is one shape; the charge lands on Accept, server side.
            from prometheus import priced_read as _prg
            _vw = _pm_viewership_read_price(text, _pm_user)
            _has_delivered = bool((metric_request or {}).get('bound_series')) \
                or (isinstance(panel_confirm, dict) and str(panel_confirm.get('kind') or '') == 'derived')
            _pr_kind = 'derived' if (_is_derived and (_has_delivered or not _vw)) else ('viewership' if _vw else 'report')
            _state, _pl = _prg.offer_or_charge(
                text=text, subject=subj_name, kind=_pr_kind, panel_confirm=panel_confirm,
                username=_pm_user, user=user, history=history, consume=_H.consume_credit,
                years=(_vw[2] if _vw else 1), dimension=str((metric_request or {}).get('dimension') or ''),
                verb=_pm_viewership_verb(text), series=_pm_is_viewership_series_ask(text))
            if _state != 'charged':
                _C._pm_ask_hint(outcome=(('panel_offer' if _pl.get('panel_offer') else 'answered') if _state == 'offer' else 'panel_out_of_credits'), subject=subj_name)
                return jsonify(_pl)
            subj_name = str(_pl.get('subject') or subj_name)   # the read runs on the subject the gate settled on (prometheus.quote_subject)
            base = {'subject': subj_name, 's3_key': '', 'source': 'panel'}
            if _qu.kind == 'named':
                base['universe'] = _qu.base   # the figures describe US people (or the named audience), never a page
                digest_block = ''             # the open profile's rows are not the anchor of a read on another universe
            panel_charge = _pl
            print(f"[pm-panel] charged {_pl.get('credits')} credits to {_pm_user} for {subj_name}")
    if not base:
        # No base and no priced read fired above: a named subject still
        # gets the custom read offer (never a forced profile, Jenna
        # 2026-10-08); with no subject at all, ask which audience.
        # An explicit profile ORDER is the one exception (2026-10-20
        # Jenna, "run a profile on mountain dew" drew this offer): the
        # reader asked for the profile, so the build flow drafts it.
        _eb = _subject_rules.explicit_build_subject(text)
        if _eb and not isinstance(panel_confirm, dict):
            _C._pm_ask_hint(route='build_reroute', outcome='explicit_build_order', subject=_eb)
            return jsonify(_subject_rules.build_reroute_payload())
        from prometheus import priced_read as _prg
        _pg_name = str(((ctx or {}).get('primary') or {}).get('name') or switch_page or '').strip()
        subj_name = _page_default.no_base_subject(text, _pg_name, bind_subject, subj_hint or pma.guess_subject_from_text(text), panel_confirm,
                                                  display_for=_subject_rules.display_for, reading_cohort=_prg.reading_cohort, plausible=_C._pm_plausible_subject)
        if subj_name and not isinstance(panel_confirm, dict):
            _state, _pl = _prg.offer_or_charge(text=text, subject=subj_name, kind='report', panel_confirm=None,
                                               username=_pm_user, user=user, history=history)
            if _state != 'charged':
                _C._pm_ask_hint(outcome='panel_offer', subject=subj_name)
                return jsonify(_pl)
            # no offer step (included with the plan, Jenna 2026-11-08, or a
            # metered follow-up): the read runs now on the named subject
            subj_name = str(_pl.get('subject') or subj_name)   # as the gate settled it (prometheus.quote_subject)
            base = {'subject': subj_name, 's3_key': '', 'source': 'panel'}
            panel_charge = _pl
        if not base and not isinstance(panel_confirm, dict):   # nothing named: which audience? the page is a chip, never the answer (2026-10-24)
            _C._pm_ask_hint(route='audience_confirm', outcome='asked_audience', subject=_pg_name)
            return jsonify(_prg.which_audience(text, ctx if (_pg_name and not _page_default.page_rejected(text, history, _pg_name)) else None))
        if not base:
            reply, followups = pma.build_profile_required_reply(
                '', price_label=_pm_panel_price_label(_pm_user)[0])
            _C._pm_ask_hint(outcome='clarify_subject')
            return jsonify({
                'success': True, 'action': 'answer', 'reply': reply,
                'followups': followups, 'offer_deck': False,
                'deck_angle': None})
    # NEW-NUMBERS ON AN OPEN FILE (2026-10-21): overlap, incrementality,
    # path, ranked list. Opening the profile does not make a new count
    # chat. Quote first; charge on confirm; extra titles in the same
    # thread frame are $100. Sizing and where-watched quote in
    # priced_read.service_turn. Keep the catalog base so the read
    # still uses the file's rows.
    if base and panel_charge is None:
        # The line is drawn on WORK, not on whether a profile exists
        # (Jenna 2026-10-21, Liz's QSR ask on the open Paramount+ profile:
        # "this should have charged her $100"). A put-together read on
        # the open file (a ranked list, a table, a cross, a top N, what
        # they watched next, a cohort within the audience) is a custom
        # ask: the offer first, the charge on Accept, the read after.
        # A lookup of a figure on the screen stays metered.
        from prometheus import priced_read as _prg
        _pr_hit = None
        try:
            _pr_hit = None if _view_base else _prg.classify(text, history, ctx)
        except Exception:
            _pr_hit = None
        _ov_hit = bool(_pr_hit and _pr_hit.get('via') == 'generate')
        _same_frame = bool(_pr_hit and _pr_hit.get('same_frame'))
        if _ov_hit:
            _ov_subj = _prg.bound_subject(text, history, ctx, fallback=str(base.get('subject') or subj_hint or '').strip())
            _ov_subj = _ov_subj or str((panel_confirm or {}).get('subject') or '').strip() if isinstance(panel_confirm, dict) else _ov_subj
            if _ov_subj:
                _is_derived = (
                    str((metric_request or {}).get('metric_family') or '') == 'dimension split'
                    or (isinstance(panel_confirm, dict)
                        and str(panel_confirm.get('kind') or '') == 'derived')
                    or _same_frame)
                _has_delivered = bool((metric_request or {}).get('bound_series')) \
                    or (isinstance(panel_confirm, dict)
                        and str(panel_confirm.get('kind') or '') == 'derived') \
                    or _same_frame
                _pr_kind = 'derived' if (_is_derived and _has_delivered) else str((_pr_hit or {}).get('kind') or 'report')
                _state, _pl = _prg.offer_or_charge(
                    text=text, subject=_ov_subj, kind=_pr_kind, panel_confirm=panel_confirm,
                    username=_pm_user, user=user, history=history, consume=_H.consume_credit,
                    dimension=str((metric_request or {}).get('dimension') or ''),
                    frame=str((_pr_hit or {}).get('frame') or ''))
                if _state != 'charged':
                    _C._pm_ask_hint(outcome=(('panel_offer' if _pl.get('panel_offer') else 'answered') if _state == 'offer' else 'panel_out_of_credits'), subject=_ov_subj)
                    return jsonify(_pl)
                panel_charge = _pl
                print(f"[pm-panel] charged {_pl.get('credits')} credits to {_pm_user} for {_pr_kind} {_ov_subj}")
    # Base rows ride the prompt (2026-08-27, toy-categories routing):
    # when the caller had no open-page digest but the base resolved to
    # a catalog profile, load that profile's digest so the read
    # derives from the base's actual rows (a category mix weighs the
    # base's own brand rows, not free-floating knowledge).
    if _view_base:
        # The open product is the base (2026-10-22, Alexia): the read
        # derives from the payload on screen and nothing else. Any digest
        # a caller attached (a profile left in the picker) is dropped.
        digest_block = __import__('prometheus.view_base', fromlist=['data_block']).data_block((ctx or {}).get('view_context'))
    if not str(digest_block or '').strip() \
            and str(base.get('s3_key') or '').lower().endswith('.csv'):
        _t_digest = time.monotonic()
        try:
            digest_block, _base_meta = pma.get_digest_bundle(
                _H.s3_client, _H.S3_BUCKET,
                {'primary': {'s3_key': base['s3_key'],
                             'name': base.get('subject') or ''},
                 'cuts': []})
        except Exception:
            traceback.print_exc()
        _C._pm_ask_stage('digest', t0=_t_digest)
    led = {'block': '', 'exact': None, 'entries': []}
    _t_ledger = time.monotonic()
    try:
        # A product view is one subject across every pass on it: the
        # figures already stored under the view's title bind this pass
        # before any model-written label is consulted (2026-11-04,
        # Liz's gender by door: each pass had persisted under its own
        # label, so no pass ever saw the one before it).
        # The thread rides along (2026-11-05, Jenna's Kanan S5 -> Fightland
        # ask): a viewing-chain follow-up ("what percent of those watched
        # the full season") binds to the question it follows, and a banked
        # journey read is reused only for the same whole journey.
        if _view_base and str(base.get('subject') or '').strip():
            led = il.consult(subject=str(base.get('subject')), question=text,
                             metric_family=mr.get('metric_family'), history=history)
        if not led.get('entries'):
            led = il.consult(subject=subj_hint or None, question=text,
                             metric_family=mr.get('metric_family'), history=history)
        if not led.get('entries') and subj_hint:
            led = il.consult(question=text, history=history)
        if not led.get('entries') and base.get('subject'):
            # The ask itself may name no subject ("what are parents
            # of kids 4-7 buying in terms of toy categories"); the
            # resolved base still knows whose history to consult
            # (2026-08-27, rephrased toy ask).
            led = il.consult(subject=base.get('subject'),
                             question=text,
                             metric_family=mr.get('metric_family'), history=history)
    except Exception:
        traceback.print_exc()
    _C._pm_ask_stage('ledger', t0=_t_ledger)
    exact = led.get('exact')
    # A charged research report never takes the replay shortcut (the
    # pre-charge replay check already ran; a race landing here would
    # hand back a stored reply against a fresh charge). Entries still
    # ride the prompt as binding constraints.
    if panel_charge is not None:
        exact = None
    # A stored read this thread already showed and the reader pushed back
    # on is never the answer to the repeat (Jessie 2026-11-04: the 62.4%
    # FAST read came back verbatim after "Samsung TV Plus isn't even in
    # the data"). It still rides the constraint block; the ask runs fresh.
    if exact and _C._pm_replay_rejected_in_thread(exact, history):
        exact = None
    # The same question re-asked minutes after a replay means the stored
    # read did not satisfy; it runs fresh, as on the analyze path
    # (2026-11-05: the same stale entry was served three times in four
    # minutes, the third on an Approve of the identical question).
    if exact and _C._pm_replay_repeat_block(_pm_user, text):
        print(f"[pm-replay] {str(exact.get('k') or '')!r} was already served for this question minutes ago; running fresh")
        exact = None
    # a decision of record answers part by part from the record; its stored summary replays only for a general ask (prometheus.study_record)
    _sr_hit = _study_record.respond(text, history) if (exact and exact.get('reply') and il.is_record_entry(exact)) else None
    if _sr_hit:
        _C._pm_ask_hint(route='study_record', outcome='answered', subject=led.get('subject') or subj_hint)
        return jsonify(_sr_hit)
    if exact and exact.get('reply'):
        _C._pm_ask_hint(outcome='answered',
                     subject=led.get('subject') or subj_hint)
        _C._pm_remember_ask(_pm_user, text,
                         subject=led.get('subject') or subj_hint,
                         cohort=exact.get('cohort'),
                         ledger_key=exact.get('k'), route='replay')
        # Served from the library: metered, never free (2026-09-14).
        _C._pm_meter_answer('replay_read', _pm_ppu)
        _replay_chips = [c for c in list(exact.get('followups') or [])[:3]
                         if c != pma.CSV_OFFER_CHIP]
        _C._pm_csv_point(led.get('subject') or subj_hint, text,
                      exact.get('family'))
        _rp_file = _C._pm_replay_file(exact, text, _pm_user, _replay_chips)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': led.get('subject') or subj_hint or None,
            **_rp_file})
    # PANEL FACTS (2026-09-30, Jenna: "lets now do the panel-fact
    # queries upgrades"): a factual ask the base file answers exactly
    # returns the file's own numbers now instead of riding the full
    # read. Comparison pairs and charged research reports never take
    # this path; a miss falls through unchanged.
    if not _two and panel_charge is None:
        _pf_resp = None
        try:
            _pf_resp = _C._pm_panel_fact_response(_pm_user, _pm_ppu,
                                               text, base)
        except Exception:
            traceback.print_exc()
        if _pf_resp is not None:
            return _pf_resp
    # "Do not build anything" (prometheus.no_build, 2026-11-16): answered from what is held above, or one plain line; never a fresh read
    if panel_charge is None and inline_job_id is None and _no_build.active():
        _C._pm_ask_hint(route='no_build', outcome='held', subject=base.get('subject'))
        return jsonify(_no_build.hold())
    # No stored read to replay: this is a FRESH generation, which
    # runs the full operating loop (corpus retrieval, live research,
    # examples-as-foundation) and can take tens of seconds. By
    # default it rides a background job the widget polls - the reply
    # survives phone backgrounding and reloads (2026-08-27, the
    # fallback Jenna kept hitting was the client fetch dying mid-
    # generation, not the server failing).
    if async_fresh is None:
        async_fresh = True
    # Capture per-user attribution NOW while we are on the Flask
    # request thread. The async path spawns a background thread with
    # no request context; the sync path stays on the request thread
    # but re-merging here keeps behavior uniform between the two.
    # Merged with _pm_ppu so pay-as-you-go billing fields (session
    # id, request id, pay_per_use flag) are preserved. 2026-09-04 fix:
    # before this, background read jobs for full-tier users had no
    # attribution because _pm_ppu was None and the bg thread's
    # _pm_attrib_extras() returned {} from missing request context.
    _pm_read_extras = _C._pm_merge_extras(_C._pm_attrib_extras(), _pm_ppu)
    if async_fresh:
        # Same user, same question, while the first copy still runs:
        # hand back the running job's progress instead of a second
        # run (Jenna 2026-09-30). A paid report re-send refunds the
        # fresh charge before attaching to the running copy.
        _dup_read = _C._pm_read_inflight_check(_pm_user, text)
        if _dup_read:
            if panel_charge is not None:
                try:
                    _C._pm_panel_refund(panel_charge)
                except Exception:
                    traceback.print_exc()
            _C._pm_ask_hint(route='read_inflight_dedupe',
                         outcome='answered',
                         subject=base.get('subject'))
            _stage = (_dup_read.get('stage') or 'working through the data')
            return jsonify({
                'success': True, 'action': 'answer', 'read_job_id': _dup_read['job_id'],
                'reply': ('Already on it - that exact read is running now (' + _stage
                          + '). It lands right here the moment it is ready, and I '
                          'did not start a second copy.'),
                'followups': [], 'offer_deck': False, 'deck_angle': None})
        # I1/I4: the subject the person named leads the promise and the ask
        # log; the base profile is context (Marie Flores: Everybody Loves
        # Raymond on the Prime Video base; Casey Pearson: Among Us on the
        # Paramount+ base)
        _promised = _subject_rules.clean_display(str(bind_subject or ''), text) or _subject_rules.clean_display(str(base.get('subject') or ''), text)
        if not _promised:   # I4: a promise names its subject or is not made
            _C._pm_ask_hint(outcome='clarify_subject')
            return jsonify({'success': True, 'action': 'answer', 'reply': _subject_rules.clarify_for(text), 'followups': [], 'offer_deck': False, 'deck_angle': None})
        job_id = inline_job_id or uuid.uuid4().hex[:12]
        # A matrix ask (many groups by several dimensions, more than one
        # pass carries whole) runs piece by piece inside the one job and
        # lands as one table plus one file (2026-11-04, Liz's genre
        # demographics). The plan is read off the words, so a resumed
        # copy of the job makes the same plan.
        _mplan = None
        try:
            _mplan = _matrix.plan(text, mr)
        except Exception as _fx:
            _faults.note('chat', _fx); _mplan = None
        _on_it = _pm_on_it_line(base, bound=bind_subject)
        if _mplan:
            _on_it = (_on_it.rstrip() + f" This one runs in pieces ({_matrix.describe(_mplan)}); "
                      "the whole table lands here as one answer with the file under it.")
        if isinstance(panel_charge, dict) and panel_charge.get('included'):
            # Included with your plan (Jenna 2026-11-08): the opener an unlimited seat reads instead of a price
            _on_it = __import__('prometheus.priced_read', fromlist=['with_included_line']).with_included_line(_on_it)
        if _pm_probe_caller() == 'golden-set' or _faults.is_probe():   # a probe grades the reply shape only: no job, no queue, no inflight mark (2026-11-02: a local replay's orphaned read drew the deliver-by-email alert for 'canary:golden')
            _C._pm_ask_hint(outcome='answered', subject=_promised)
            return jsonify({'success': True, 'action': 'answer', 'read_job_id': 'golden-' + job_id, 'followups': [], 'offer_deck': False, 'deck_angle': None,
                            'reply': _on_it})
        # A probe's finished read never mails anyone (captured here,
        # on the request thread, and carried on the job).
        _probe = bool(_pm_probe_caller()) or _pm_is_probe_user(_pm_ask_log_user(''))
        _head = {'job_id': job_id, 'user': _pm_user, 'status': 'working',
                 'stage': 'reading the data', 'probe': _probe, 'subject': _promised,
                 'question': text[:300], 'started_at': time.time(),
                 'resume': _rr.resume_record(          # survives a deploy
                     text=text, history=history, base=base,
                     panel_charge=panel_charge, probe=_probe,
                     switch_page=switch_page, bind_cohort=bind_cohort)}
        if _mplan:
            _head['matrix'] = {'groups': len(_mplan['groups']), 'dimensions': len(_mplan['dimensions']),
                               'pieces': len(_mplan['pieces'])}
        _args = (job_id, _pm_user, _pm_read_extras, text, list(history or [])[-10:],
                 mr, base, digest_block, anchors_block, led)
        _kw = {'panel_charge': panel_charge, 'probe': _probe, 'matrix_plan': _mplan}
        _C._pm_read_inflight_mark(_pm_user, text, job_id, _promised)
        _head['thread_id'] = _C._pm_job_bind_thread(job_id, _pm_user) if not inline_job_id else _C._pm_job_bind_thread(job_id, _pm_user, resumed=True)   # the worker is another process (2026-10-14); it keeps the launch's thread (2026-11-11)
        _head['via'] = 'api' if getattr(__import__('flask').g, '_pm_api_key_owner', None) else 'dashboard'
        if inline_job_id:            # the read worker runs the job right here
            _pm_read_status_write(job_id, _head)
            _C._pm_run_read_job(*_args, **_kw)
        elif _rr.queue_enabled() and not force_local:
            _rr.enqueue(job_id, _head)   # the Hetzner read worker picks it up
        else:                        # local thread (fallback / kill switch)
            _pm_read_status_write(job_id, _head)
            _ledger_gate.note_job(job_id, _pm_user, _probe)   # the thread has no request: who asked rides with the job (a probe never banks, 2026-11-11)
            threading.Thread(target=_C._pm_run_read_job, args=_args, kwargs=_kw, daemon=True).start()
        _C._pm_ask_hint(outcome='answered', subject=_promised)
        return jsonify({
            'success': True, 'action': 'answer', 'read_job_id': job_id, 'subject': _promised,
            'reply': _on_it,
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    payload = _pm_generate_read_core(
        text=text, history=history, mr=mr, base=base,
        digest_block=digest_block, anchors_block=anchors_block,
        led=led, pm_user=_pm_user, pm_ppu=_pm_read_extras,
        switch_page=switch_page)
    _sync_held = bool(payload.get('_held'))
    if panel_charge and (_sync_held or not payload.get('success') or payload.get('data_floor') == 'before'):
        # The paid report never delivered: reverse the charge.
        _C._pm_panel_refund(panel_charge)
    if isinstance(panel_charge, dict) and panel_charge.get('included') and payload.get('success') and payload.get('reply'):
        payload['reply'] = __import__('prometheus.priced_read', fromlist=['with_included_line']).with_included_line(payload['reply'])
    try:
        if not payload.pop('_held', False):
            _C._pm_csv_point(payload.get('profile'), text,
                          (payload.get('_family') or ''))
    except Exception as _fx:
        _faults.note('chat', _fx)
    payload.pop('_family', None)
    payload.pop('_verify', None)
    _stages = payload.pop('_stages_ms', None)
    if isinstance(_stages, dict):
        for _sk, _sv in _stages.items():
            _C._pm_ask_stage(_sk, ms=_sv)
    return jsonify(payload)


# <<block: 2>>
def _pm_generate_read_core(*, text, history, mr, base, digest_block,
                           anchors_block, led, pm_user, pm_ppu,
                           stage_cb=None, switch_page=None, bank=True,
                           universe_guard=True):
    """Fresh generated read - the operating loop (2026-08-27, Jenna:
    "it truly needs to be really smart"). Request-context free so it
    runs identically inline and inside a background read job.

    1. CONTEXT BIND: the base profile's rows, cross-module anchors,
       the subject's ledger history, worked examples from the whole
       ledger, neighbor evidence from across the ~4,200-profile
       library (model-picked comparables, digests cached by ETag),
       and the full Subscriber IQ payload when the ask or its base
       names a title with an acquisition read (2026-08-28).
    2. GAP RESEARCH: the reasoning call carries the web_search tool
       and researches what the grounding cannot answer, approved
       sources only, never named in output.
    3. SYNTHESIZE: playbooks + coherence enforcement.
    4. VERIFY: anchor recompute against the base rows, ledger
       coherence, scrub residue - one self-revision on failure; a
       second failure holds the read (2026-08-28, p3-verify).
    5. BANK: the verified read persists with its derivation trail and
       verification stamp and becomes a worked example for the next
       ask.

    `stage_cb`, when provided (the background read job), receives a
    user-safe stage label at each phase transition so the widget can
    narrate progress. Returns the response payload dict (plus internal
    '_family', '_verify', '_held' when applicable, and '_stages_ms', a
    per-stage wall-clock breakdown the callers route to the ask log /
    the read-job JSON).

    `pm_ppu` (2026-09-04): historically this only carried pay-as-you-go
    billing extras (session id, request id, pay_per_use flag). It now
    ALSO carries the requesting user's attribution (user, user_email)
    pre-captured on the request thread by the caller and merged with
    the PPU dict. This function runs request-context free, so any
    _pm_attrib_extras() call inside its model calls would return {};
    passing the pre-merged dict through the existing pm_ppu slot keeps
    every per-call render_calls record attributed to the right user
    without a signature change.

    `bank=False` (2026-11-04): one piece of a matrix read. The pass
    runs whole (grounding, generation, binding, verification) but
    nothing persists, no file and no chart are made and no ask outcome
    is recorded; the caller assembles the pieces and banks once. The
    payload then carries the structured result on '_res'."""
    import prometheus_analysis as pma
    import insights_ledger as il
    from prometheus import figure_binding as _fb

    def _stage_note(label):
        if stage_cb is None:
            return
        try:
            stage_cb(label)
        except Exception as _fx:
            _faults.note('chat', _fx)

    _stage_note('reading the data')
    stages = {}
    # The base is the universe the question names (Jenna 2026-11-06). A
    # read that reaches the core on a page or library base the question
    # never named, while the question named its own universe (a job
    # resumed from before the rule, a direct caller), runs on that
    # universe instead: the panel, US people, the named audience as the
    # subject. The page's rows do not ride the prompt. The same reading
    # frames the prompt and guards the reply below.
    _qu = _question_universe.NONE
    _qu_dash = [str(switch_page or '')]   # dashboard audiences a named-universe reply may never frame itself on
    try:
        if str(base.get('source') or '') in ('page', 'catalog'):
            _qu_dash.append(str(base.get('subject') or ''))
        _qu = _question_universe.question_universe(text, '')
        if _qu.kind == 'named' and str(base.get('source') or '') in ('page', 'catalog') \
                and not base.get('bound') \
                and not str(base.get('s3_key') or '').startswith('subiq:') \
                and _question_universe.names_other_universe(text, base.get('subject')):
            print(f"[pm-loop] base {base.get('subject')!r} ({base.get('source')}) is not the universe the question "
                  f"names ({_qu.label!r}); reading on {_qu.base!r}")
            base = _question_universe.panel_base(_qu)
            digest_block = ''
        elif _qu.kind == 'named' and str(base.get('source') or '') == 'panel':
            base = dict(base, universe=str(base.get('universe') or _qu.base))
            if _qu.explicit:
                digest_block = ''
    except Exception as _fx:
        _faults.note('chat', _fx)
    _t_stage = time.monotonic()
    if not anchors_block:
        try:
            _xm_trends_reader = None
            if _H._trends_iq is not None:
                def _xm_trends_reader():
                    return _H._trends_iq._cache_get({
                        'geo_type': 'National', 'geo_value': '',
                        'lookback_days': _H._trends_iq.DEFAULT_LOOKBACK_DAYS})
            anchors_block, _xm_mods = pma.build_cross_module_block(
                _H.s3_client, _H.S3_BUCKET, {'primary': None}, text,
                active_view='', subiq_bucket=_H.SUBSCRIBER_S3_BUCKET,
                subiq_parser=_H.parse_subscriber_iq_csv,
                trends_reader=_xm_trends_reader)
        except Exception:
            traceback.print_exc()
            anchors_block = ''
    stages['anchors'] = int((time.monotonic() - _t_stage) * 1000)
    # Corpus-wide neighbor evidence (2026-08-27, Jenna: "it can go
    # through all the profiles"): comparable audiences from the whole
    # library, model-picked for this ask, digests cached by ETag.
    neighbor_block, neighbor_names = '', []
    _corpus_t = {}
    _t_stage = time.monotonic()
    try:
        import prometheus_corpus as pmc
        neighbor_block, neighbor_names = pmc.gather_neighbor_evidence(
            _H.s3_client, _H.S3_BUCKET, text, base.get('subject'),
            _C._profile_catalog_for_chat(),
            lambda s, u: _C._pm_claude_json(s, u, max_tokens=400,
                                         temperature=0.0,
                                         surface='corpus_select',
                                         usage_extras=pm_ppu),
            k=6, timings=_corpus_t)
    except Exception:
        traceback.print_exc()
    stages['corpus'] = int((time.monotonic() - _t_stage) * 1000)
    for _tk, _sk in (('select_ms', 'corpus_select'),
                     ('digest_ms', 'corpus_digest')):
        if _corpus_t.get(_tk) is not None:
            stages[_sk] = int(_corpus_t[_tk])
    # Bank-as-foundation: nearest prior delivered reads ride the
    # prompt as worked examples (method + voice, never numbers).
    examples_block = ''
    _t_stage = time.monotonic()
    try:
        _ex = il.examples(question=text, subject=base.get('subject'))
        examples_block = il.render_examples_block(_ex)
    except Exception:
        traceback.print_exc()
    stages['examples'] = int((time.monotonic() - _t_stage) * 1000)
    # Subscriber IQ parity (2026-08-28): when the ask or its base names
    # a title with an acquisition read, the full parsed payload rides
    # the evidence as a compact structured block (signups, windows,
    # cohorts, key drivers), not just the one-line cross-module signal.
    subiq_block, subiq_show = '', None
    _t_stage = time.monotonic()
    try:
        subiq_block, subiq_show = pma.build_subiq_evidence_block(
            _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, _H.parse_subscriber_iq_csv,
            text, subject_hint=base.get('subject') or '')
    except Exception:
        traceback.print_exc()
    stages['subiq'] = int((time.monotonic() - _t_stage) * 1000)
    # Exact rows for question-named entities plus, for a brand
    # purchase question, the Avid tier and the retail channel
    # (2026-10-06, Jenna); one call, fail-safe to ('', '').
    _t_stage = time.monotonic()
    entity_rows_block, purchase_block, _purchase_facts = \
        pma.build_entity_and_purchase_blocks(_H.s3_client, _H.S3_BUCKET, base, text)
    stages['entity_rows'] = int((time.monotonic() - _t_stage) * 1000)
    # Measured daily signals (2026-10-01, Jenna: flavor 1). Aggregate
    # tracker counts for subjects the ask names - templated, read-
    # only, never row-level. Fail-safe to ''.
    measured_block = ''
    _t_stage = time.monotonic()
    try:
        import panel_fact_store as _pfs
        measured_block = _pfs.measured_signals_block(
            text, extra_names=[str(base.get('subject') or '')])
    except Exception:
        traceback.print_exc()
    stages['measured'] = int((time.monotonic() - _t_stage) * 1000)
    _cat_block = ''
    try:   # a pairing Crosswalk has already valued hands the read the valuation's own figures (2026-11-11)
        from prometheus import peacock_scale as _pk_scale   # a read on Peacock answers from the figures on file (2026-11-18)
        _cat_block = __import__('prometheus.knowledge', fromlist=['read_blocks']).read_blocks(text, mr, base, pk=_pk_scale.block, saved=_bpiq_wiz.saved_valuation_block, catalog=_pm_catalog_block, stage=_stage_note, stages=stages, claude_json=lambda _s, _u, **_kw: _C._pm_claude_json(_s, _u, usage_extras=pm_ppu, **_kw), tools=[pma.WEB_SEARCH_TOOL], history=history, led=led)   # the knowledge layer: held, stored, the near twin, the researched anchors (2026-11-20)
    except Exception:
        traceback.print_exc()
    # The words named a subject the base does not describe (Emma Crouchen's
    # VICE Sports read on the open Pinterest profile, 2026-11-20): the layer
    # re-consulted the ledger for the named subject in place, and the read
    # runs researched end to end on it; the base's rows never ride the prompt
    # and never stand as measured.
    _borrowed_read = bool(((base.get('_knowledge') or {}) if isinstance(base.get('_knowledge'), dict) else {}).get('borrowed_base'))
    if _borrowed_read:
        digest_block = ''
    _rm_led_block = led.get('block') or ''
    if _cat_block:
        _rm_led_block = (_cat_block + '\n' + _rm_led_block) if _rm_led_block else _cat_block
    user_prompt = pma.build_reasoned_metrics_user_prompt(
        text, history, metric_request=mr or None,
        anchors_block=anchors_block, ledger_block=_rm_led_block,
        profile_rows_block=digest_block)
    _v_lookup, verifier_block = None, ''
    try:   # the verifier's rows go in BEFORE generation (2026-10-09, item 2: editor, not gate)
        import prometheus_verify as _pmv0
        if not _borrowed_read:
            _v_lookup = _pmv0.load_base_lookup(_H.s3_client, _H.S3_BUCKET, base.get('s3_key'), base.get('subject') or '')
            verifier_block = _pmv0.measured_prompt_block(_v_lookup, text, metric_request=mr or None)
    except Exception:
        traceback.print_exc()
    from prometheus import links as _links   # links are signals (2026-10-09, item 5)
    links_block = _links.block(_links.merge(_links.extract(text), _links.thread_links(history)))
    extra_blocks = [b for b in (entity_rows_block, purchase_block, verifier_block, links_block,
                                measured_block, subiq_block,
                                neighbor_block, examples_block) if b]
    extra_blocks.append(pma.GENERATION_LOOP_GUIDANCE)
    extra_blocks.extend(_read_repair.ask_blocks(text))   # the credit the reader set, what 'cannot be measured' means, a demographic grid's contract (2026-11-18)
    try:
        from prometheus import methodology as _meth_rules
        if _meth_rules.is_viewership_read(text, None, text):
            extra_blocks.append(_meth_rules.MODEL_RULES)
    except Exception as _fx:
        _faults.note('chat', _fx)
    if pma.is_cohort_churn_ask(text):
        extra_blocks.append(pma.COHORT_CHURN_GUIDANCE)
    _ds_series = []
    if pma.is_dimension_split_ask(text):   # a held figure split by a panel-observed dimension (2026-10-12)
        _ds_series = list((mr or {}).get('bound_series') or []) or pma.extract_pasted_series(text)
        extra_blocks.append(pma.dimension_split_guidance(text, _ds_series, (mr or {}).get('dimension') or ''))
    # Paid research report on a no-base subject (2026-09-14): the
    # subject has no profile rows anywhere, so the read is researched
    # end to end instead of derived from a base file. The guidance
    # block swaps the grounding order accordingly.
    _is_panel = str(base.get('source') or '') == 'panel' or _borrowed_read
    if _is_panel:
        extra_blocks.append(pma.PANEL_REPORT_GUIDANCE)
    if _qu.kind == 'named' and _qu.explicit and _is_panel:
        # the universe the question names frames the read: its base is US
        # people (or the named audience), never a file on the dashboard
        try:
            _qu_block = _question_universe.framing_block(_qu, text)
            if _qu_block:
                extra_blocks.append(_qu_block)
        except Exception as _fx:
            _faults.note('chat', _fx)
    # A title-viewing read on a person (Jenna 2026-11-04): the universe
    # is the people who watched the titles, anchored on title viewing
    # (the tracker figures and public title-viewing anchors), never the
    # person's own profile; one definition and one window carry every
    # figure in the reply, and the reply says which universe it counts.
    try:
        _title_subj = str(base.get('subject') or (mr or {}).get('subject') or '')
        _title_uni = _page_default.title_universe_label(
            text, _title_subj, category=_page_default.subject_category(_title_subj, base))
        if _title_uni:
            extra_blocks.append(_page_default.title_read_guidance(_title_uni))
    except Exception as _fx:
        _faults.note('chat', _fx)
    is_strategy = False
    try:
        is_strategy = pma.detect_strategy_intent(text)
    except Exception as _fx:
        _faults.note('chat', _fx)
    if is_strategy:
        extra_blocks.append(pma.STRATEGY_GUIDANCE)
    _is_so_what = pma.is_so_what_ask(text)  # so-what asks answer as a plan
    if _is_so_what:
        extra_blocks.append(pma.SO_WHAT_GUIDANCE)
    _is_plan = False
    try:   # a social / search / media plan asked for beside the figures (2026-11-06)
        _is_plan = bool(pma.is_media_plan_ask(text)) and not _is_so_what
        if _is_plan:
            extra_blocks.append(pma.MEDIA_PLAN_GUIDANCE)
    except Exception as _fx:
        _faults.note('chat', _fx)
    _multi_qs = pma.split_questions(text)  # several questions: answer all
    if _multi_qs:
        extra_blocks.append(pma.multi_question_guidance(_multi_qs))
    _figs2 = _fig.registry_for(anchors_block, digest_block, verifier_block, measured_block, subiq_block)   # audit items 3 + 4
    user_prompt = user_prompt + '\n\n' + '\n\n'.join(extra_blocks) + _figs2.prompt_suffix()
    try:
        import prometheus_knowledge as _pmk
        _kb = _pmk.knowledge_block('measured_read', text=text,
                                   s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
        if _kb:
            user_prompt = f"{user_prompt}\n\n{_kb}"
    except Exception as _fx:
        _faults.note('chat', _fx)
    print(f"[pm-loop] grounding: base={base.get('s3_key')!r} "
          f"neighbors={neighbor_names} "
          f"examples={'yes' if examples_block else 'no'} "
          f"subiq={subiq_show or 'no'} "
          f"strategy={is_strategy}")
    # 11000, not 4000: breakdown asks (one ranked row per category plus
    # shares, penetrations, notes) legitimately run long. The 4000
    # ceiling truncated the Shark Tank category read twice on
    # 2026-08-27 and the fragment crashed coherence enforcement.
    _stage_note('researching')
    _t_stage = time.monotonic()
    result = _C._pm_claude_json(pma.REASONED_METRICS_SYSTEM_PROMPT,
                             user_prompt, max_tokens=11000,
                             temperature=0.4, usage_extras=pm_ppu,
                             tools=[pma.WEB_SEARCH_TOOL])
    stages['model'] = int((time.monotonic() - _t_stage) * 1000)
    try:
        from claude_client import last_call_stats as _pm_lcs
        stages['model_ws_rounds'] = int(
            (_pm_lcs() or {}).get('web_search_requests') or 0)
    except Exception as _fx:
        _faults.note('chat', _fx)
    if not result.get('success'):
        _H._chatbot_error_email(
            'brief-chat/analyze',
            'measured-read model call failed: '
            + str(result.get('error') or 'unknown')[:400],
            tb='(model chain exhausted without a usable reply)')
        _C._pm_ask_hint(outcome='error')
        return _H._chatbot_calm_payload()
    data = result.get('data') or {}
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), {})
    _df_read = __import__('prometheus.data_floor', fromlist=['gate']).hold_read(data, text)   # no data before the data floor (Jenna 2026-11-18): a read that states a window or a row before it never lands; one moved onto it says so first
    if _df_read:
        _C._pm_ask_hint(outcome='data_floor')
        return _df_read
    if str(data.get('action') or '').strip().lower() == 'decline':
        _dbq_dec = __import__('prometheus.dashboard_behavior', fromlist=['from_decline']).from_decline(data, text, '', _C._pm_ask_hint)   # the dashboard's own controls are never a read (2026-11-18)
        if _dbq_dec is not None:
            return _dbq_dec
        gate = {'domain': 'model_decline',
                'what': 'That behavior',
                'alternative': 'the digital read on the same subject'}
        reply, followups = pma.build_not_quantifiable_reply(text, gate)
        _C._pm_ask_hint(outcome='declined_not_quantifiable')
        return {
            'success': True, 'action': 'answer', 'reply': reply,
            'followups': followups, 'offer_deck': False,
            'deck_angle': None, 'not_quantifiable': 'model_decline'}
    _stage_note('composing the answer')
    _t_stage = time.monotonic()
    # an age breakdown on an account-holder universe, or of an account
    # action the ask names (sign-ups, paid, subscribers), is adults only
    # (Grace Yin, Amazon, 2026-11-10); the ask and the base ride into
    # every coherence pass of this read
    _ah_ctx = {'question': str(text or ''),
               'base_label': ' '.join(str((base or {}).get(k) or '') for k in ('subject', 's3_key', 'name'))}
    # Figures already on record never have their digits drawn again
    # (2026-11-11, the Mad Men compare: the profile's own 7,582,850 was
    # re-drawn three ways in one reply): the profile's figures and the
    # evidence the read was given, the subject's banked reads, and every
    # count an earlier turn of this thread stated.
    _on_record = set()
    try:
        from prometheus import count_digits as _cdh
        _on_record = _cdh.on_record(
            _figs2,
            [e for e in ((led or {}).get('entries') or []) if isinstance(e, dict) and not e.get('retired')],
            [t.get('text') for t in (history or []) if isinstance(t, dict)])
    except Exception as _fx:
        _faults.note('chat', _fx)
    _ah_ctx['held'] = _on_record
    try:
        res, data = _read_repair.coherent(   # a grid is shaped and completed; a reply with no figures is salvaged, then run once more (2026-11-18, Liz)
            data, question=text, subject=str(base.get('subject') or ''), log=lambda _m: print(f"[pm-loop] read repair: {_m}"),
            enforce=lambda _d: pma.reconcile_breakdown_to_series(pma.enforce_metrics_coherence(_d, so_what=_is_so_what, multi=_multi_qs, plan=_is_plan, **_ah_ctx), _ds_series),
            again=lambda _blk: _C._pm_claude_json(pma.REASONED_METRICS_SYSTEM_PROMPT, user_prompt + '\n\n' + _blk, max_tokens=11000, temperature=0.2, usage_extras=pm_ppu, tools=[pma.WEB_SEARCH_TOOL]),
            note=lambda _why: _H._chatbot_error_email('brief-chat/complete', _why, user_email=pm_user, payload={'question': text[:300], 'subject': base.get('subject')}),
            facts=lambda _gp: _grid_facts.platform_facts(_gp, _H.s3_client, _H.S3_BUCKET, _C._profile_catalog_for_chat(), pma))   # the comparison row reads the service's own profile and the US baseline
        # a creator-fit read is held to the subject's universe (the catalog's
        # projected US people, the thread's banked figure, Gen Pop) so it can
        # never disagree with the subject's profile (2026-10-20, Mark's
        # DraftKings read: twelve creators 'together reach 8,947,163' against
        # a build the thread quoted at under a million)
        res = _creator_fit.apply(
            res, text,
            anchors_fn=lambda s: __import__('migration.corpus_catalog', fromlist=['anchors_for']).anchors_for(s, with_ledger=True),
            genpop_fn=lambda: pma.load_genpop_map(_H.s3_client, _H.S3_BUCKET),
            profile_loader=lambda k: pma.load_profile_df(_H.s3_client, _H.S3_BUCKET, k)[0])
        if str(base.get('source') or '') == 'view' and str(base.get('domain') or '') == 'attribution':
            _cw = __import__('prometheus.campaign_drilldown', fromlist=['window_for_base']).window_for_base(base)
            if _cw:   # the campaign's own window, never a profile's and never a default (2026-10-26)
                res.update(window_label=_cw['label'], window_start=_cw['start'], window_end=_cw['end'])
        if _qu.kind == 'named' and _qu.explicit and _is_panel and not str(res.get('universe') or '').strip():
            res['universe'] = _question_universe.universe_label(_qu)   # the opener names the universe the question named (2026-11-06)
        # Stored figures bind this pass (2026-11-04, Liz's gender by door):
        # a figure already reported for the same subject, definition and
        # window is kept, and the reply says so in one line. The same on
        # the dashboard and on the API door; the read core is the one path.
        _bound = []
        try:
            res, _bound = _fb.bind_to_stored(
                res, _C._pm_verify_prior_entries(res, res.get('metric_family'), led), ask_text=text)
        except Exception as _fx:
            _faults.note('chat', _fx); _bound = []
        # A named base holds across the thread's reads (2026-11-10, Liz's
        # Amazon Channels vs direct survival thread: 52,605,814 direct
        # starts became 23,272,291 two reads later on a basis change). The
        # cohort counts the thread's earlier reads stated, resolved through
        # their job documents when the landing sits in another thread, bind
        # this pass unless the ask named a window of its own.
        _tb_held = []
        try:
            from prometheus import thread_bases as _tbases
            _tb_bases = _tbases.bases_from_reads(_tbases.thread_reads(history, _C._pm_read_job_doc))
            if _tb_bases:
                res, _unused_reply, _tb_held = _tbases.hold(res, '', _tb_bases, ask_text=text)
                if _tb_held:
                    print(f"[pm-loop] thread bases held: " + '; '.join(f"{h['label']} {h['from']:,} -> {h['to']:,}" for h in _tb_held)[:600])
        except Exception as _fx:
            _faults.note('chat', _fx); _tb_held = []
        reply, _fig_ledger2 = _fig.compose(pma.format_generated_metrics_reply(res), _figs2, anchors_block)
        if _bound:
            _ar = _fb.already_reported_line(_bound)
            if _ar:
                reply = reply.rstrip() + '\n\n' + _ar
        if _tb_held:
            try:
                _hl = _tbases.held_line(_tb_held)
                if _hl:
                    reply = reply.rstrip() + '\n\n' + _hl
            except Exception as _fx:
                _faults.note('chat', _fx)
    except _read_repair.EmptyRead as e:   # held: the calm line with the job behind it, a fresh pass from the sweep, the answer emailed when it lands (2026-11-18)
        _H._chatbot_error_email('brief-chat/analyze', f"generated read carried no usable figures after the repair and a second pass ({e}); held for a fresh pass, the user needs the answer by email",
                                user_email=pm_user, payload={'question': text[:300], 'subject': base.get('subject')})
        _C._pm_ask_hint(outcome='held')
        return {'success': True, 'action': 'answer', 'reply': _H._CHATBOT_CALM_MESSAGE, 'followups': [], 'offer_deck': False, 'deck_angle': None, '_held': True, '_family': 'audience', '_stages_ms': stages}
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _C._pm_ask_hint(outcome='error')
        return _H._chatbot_calm_payload()
    stages['coherence'] = int((time.monotonic() - _t_stage) * 1000)
    # Output guard (Jenna 2026-11-06): a reply framed on a dashboard
    # audience the question never named ("Paw Patrol toy buyers in the
    # Starz audience"), while the question named its own universe, never
    # ships. It is read again, once, on the named universe.
    if universe_guard and _qu.kind == 'named' and _qu.explicit:
        try:
            _leak = _question_universe.base_leak(reply, res, _qu_dash, text=text)
        except Exception as _fx:
            _faults.note('chat', _fx); _leak = ''
        if _leak:
            print(f"[pm-loop] reply framed on {_leak!r}, an audience the question never named; "
                  f"reading again on {_qu.base!r} ({_qu.label!r})")
            _stage_note('reading the data')
            return _pm_generate_read_core(
                text=text, history=history, mr=mr, base=_question_universe.panel_base(_qu),
                digest_block='', anchors_block=anchors_block, led=led, pm_user=pm_user, pm_ppu=pm_ppu,
                stage_cb=stage_cb, switch_page=None, bank=bank, universe_guard=False)
    # ---- Verification pass (2026-08-28, p3-verify): anchor recompute
    # against the base rows, ledger coherence, scrub residue. ONE
    # self-revision on failure; a second failure holds the read (never
    # banked, never delivered). Trouble inside the pass itself never
    # blocks a read - verification then records as skipped.
    _stage_note('checking the numbers')
    _t_verify = time.monotonic()
    fam0 = 'strategy' if is_strategy else res.get('metric_family')
    pmv = None
    try:
        import prometheus_verify as pmv
    except Exception:
        traceback.print_exc()
    verdict, verify_revised = None, False
    _last_draft, _last_verdict = (None, None, None, None), None
    _pm_auto_corrected = False   # silent auto-correct -> verify_outcome 4
    if pmv is not None:
        try:
            if _v_lookup is None and not _borrowed_read:   # another subject's rows never verify a read on the named one (2026-11-20)
                _v_lookup = pmv.load_base_lookup(
                    _H.s3_client, _H.S3_BUCKET, base.get('s3_key'),
                    base.get('subject') or '')
            verdict = pmv.verify_read(bound_facts=_purchase_facts, 
                reply=reply, res=res, family=fam0,
                base_lookup=_v_lookup, question=text,
                prior_entries=_C._pm_verify_prior_entries(res, fam0, led))
        except Exception:
            traceback.print_exc()
    if verdict is not None and not verdict.get('ok'):
        print(f"[pm-verify] first pass failed: "
              f"{verdict.get('findings')}")
        revised_ok = False
        _last_draft, _last_verdict = (data, res, reply, fam0), verdict
        try:
            rev_prompt = (user_prompt + '\n\n'
                          + pmv.render_findings_block(
                              verdict.get('findings') or []))
            result2 = _C._pm_claude_json(
                pma.REASONED_METRICS_SYSTEM_PROMPT, rev_prompt,
                max_tokens=11000, temperature=0.2,
                usage_extras=pm_ppu, tools=[pma.WEB_SEARCH_TOOL])
            if result2.get('success'):
                data2 = result2.get('data') or {}
                if isinstance(data2, list):
                    data2 = next((d for d in data2
                                  if isinstance(d, dict)), {})
                if str(data2.get('action') or '').strip().lower() \
                        != 'decline':
                    res2 = pma.reconcile_breakdown_to_series(pma.enforce_metrics_coherence(data2, so_what=_is_so_what, multi=_multi_qs, plan=_is_plan, **_ah_ctx), _ds_series)
                    reply2 = pma.format_generated_metrics_reply(res2)
                    fam2 = ('strategy' if is_strategy
                            else res2.get('metric_family'))
                    verdict2 = pmv.verify_read(bound_facts=_purchase_facts, 
                        reply=reply2, res=res2, family=fam2,
                        base_lookup=_v_lookup, question=text,
                        prior_entries=_C._pm_verify_prior_entries(
                            res2, fam2, led))
                    if verdict2.get('ok'):
                        data, res, reply = data2, res2, reply2
                        fam0, verdict = fam2, verdict2
                        verify_revised = revised_ok = True
                    else:
                        verdict = verdict2
                        _last_draft = (data2, res2, reply2, fam2)
        except Exception:
            traceback.print_exc()
        if not revised_ok:
            # Auto-correct pass (2026-09-03, no-rebuild-level-correction):
            # ONE more model attempt fed the verify findings (they name
            # the measured figure); never a loop; a raise falls through
            # to HELD with the original findings. Routes via _pm_claude_json.
            _retry_findings = []
            try:
                corrective_block = pmv.render_autocorrect_block((verdict or {}).get('findings') or [])
                rev_prompt2 = user_prompt + '\n\n' + corrective_block
                result3 = _C._pm_claude_json(
                    pma.REASONED_METRICS_SYSTEM_PROMPT, rev_prompt2,
                    max_tokens=11000, temperature=0.1,
                    usage_extras=pm_ppu, tools=[pma.WEB_SEARCH_TOOL])
                if result3.get('success'):
                    data3 = result3.get('data') or {}
                    if isinstance(data3, list):
                        data3 = next((d for d in data3
                                      if isinstance(d, dict)), {})
                    if str(data3.get('action') or '').strip().lower() \
                            != 'decline':
                        res3 = pma.reconcile_breakdown_to_series(pma.enforce_metrics_coherence(data3, so_what=_is_so_what, multi=_multi_qs, plan=_is_plan, **_ah_ctx), _ds_series)
                        reply3 = pma.format_generated_metrics_reply(
                            res3)
                        fam3 = ('strategy' if is_strategy
                                else res3.get('metric_family'))
                        verdict3 = pmv.verify_read(bound_facts=_purchase_facts, 
                            reply=reply3, res=res3, family=fam3,
                            base_lookup=_v_lookup, question=text,
                            prior_entries=_C._pm_verify_prior_entries(
                                res3, fam3, led))
                        if verdict3.get('ok'):
                            data, res, reply = data3, res3, reply3
                            fam0, verdict = fam3, verdict3
                            verify_revised = True
                            revised_ok = _pm_auto_corrected = True
                        else:
                            _retry_findings = (
                                verdict3.get('findings') or [])
                            _last_draft, _last_verdict = (data3, res3, reply3, fam3), verdict3
            except Exception:
                traceback.print_exc()
        if not revised_ok and _purchase_facts and pmv is not None:
            # In-place rescue: only bound purchase facts still wrong.
            _resc = pmv.rescue_with_facts(_last_draft, _last_verdict or verdict, _purchase_facts)
            if _resc:
                data, res, reply, fam0, verdict, _nfix = _resc
                verify_revised = revised_ok = _pm_auto_corrected = True
                stages['facts_fixed'] = int(_nfix)
        if not revised_ok and pmv is not None:
            # general in-place rescue (2026-10-08): fixed from its own findings
            _ld = _last_draft
            _resc = pmv.rescue_in_place(
                _ld, _last_verdict or verdict, base_lookup=_v_lookup, question=text,
                prior_entries=_C._pm_verify_prior_entries(_ld[1], _ld[3], led) if _ld and _ld[1] else [],
                bound_facts=_purchase_facts, family=_ld[3] if _ld else fam0)
            if _resc:
                data, res, reply, fam0, verdict, _nfix = _resc
                verify_revised = revised_ok = _pm_auto_corrected = True
                stages['facts_fixed'] = int(_nfix)
        if not revised_ok and pmv is not None and _last_draft and _last_draft[0] is not None:
            # Trim, never hold (2026-10-09, item 2): what the findings name comes out, the rest re-verifies and ships
            _ld = _last_draft
            _trm = pmv.trim_unverified(
                _ld, _last_verdict or verdict, base_lookup=_v_lookup, question=text,
                recompute=lambda d: (lambda r: (r, pma.format_generated_metrics_reply(r)))(
                    pma.reconcile_breakdown_to_series(pma.enforce_metrics_coherence(d, so_what=_is_so_what, multi=_multi_qs, plan=_is_plan, **_ah_ctx), _ds_series)),
                prior_entries=_C._pm_verify_prior_entries(_ld[1], _ld[3], led) if _ld[1] else [],
                bound_facts=_purchase_facts, family=_ld[3] or fam0)
            if _trm:
                data, res, reply, fam0, verdict, _ntrim, _dropped = _trm
                verify_revised = revised_ok = _pm_auto_corrected = True
                stages['trimmed'] = int(_ntrim)
                # Every cell the question asked for is delivered (Jenna
                # 2026-11-04, Jay Patel's Love Hypothesis read: "it should
                # never say anything like that ever. it should only send it
                # to us."). A trimmed cell the question named comes back
                # filled: one completion call with research under the
                # retained figures' arithmetic, else the deterministic
                # remainder split; the read ships whole either way and the
                # reader never hears what was filled. The arithmetic fill
                # goes to ops (jenna@ + jessie@) with the findings.
                _trim_findings = list(((_last_verdict or verdict) or {}).get('findings') or [])
                try:
                    _cmp = pmv.complete_cells(
                        _ld, (data, res, reply), question=text, dropped_labels=_dropped,
                        recompute=lambda d: (lambda r: (r, pma.format_generated_metrics_reply(r)))(
                            pma.reconcile_breakdown_to_series(pma.enforce_metrics_coherence(d, so_what=_is_so_what, multi=_multi_qs, plan=_is_plan, **_ah_ctx), _ds_series)),
                        model_json=lambda p: (lambda r: (r.get('data') if r.get('success') else None))(
                            _C._pm_claude_json(pmv.COMPLETE_CELLS_SYSTEM_PROMPT, p, max_tokens=2500,
                                            temperature=0.1, usage_extras=pm_ppu, tools=[pma.WEB_SEARCH_TOOL])),
                        subject=str(res.get('subject') or ''), base_lookup=_v_lookup,
                        prior_entries=_C._pm_verify_prior_entries(res, fam0, led) if res else [],
                        bound_facts=_purchase_facts, family=fam0, trim_findings=_trim_findings,
                        ops_email=lambda note: _H._chatbot_error_email(
                            'brief-chat/complete', note, user_email=pm_user,
                            payload={'question': text[:300], 'subject': res.get('subject'),
                                     'base': base.get('s3_key')}))
                except Exception:
                    traceback.print_exc()
                    _cmp = None
                if _cmp:
                    data, res, reply, fam0, verdict, _cinfo = _cmp
                    stages['completed'] = len(_cinfo.get('cells') or [])
        if not revised_ok:
            stages['verify'] = int(
                (time.monotonic() - _t_verify) * 1000)
            stages['verify_outcome'] = 2   # held
            _findings = (verdict or {}).get('findings') or []
            _findings_text = (
                ' | '.join(str(f) for f in _findings)
                or 'no findings recorded')
            if _retry_findings:
                _findings_text += (
                    ' || auto-correct retry findings: '
                    + ' | '.join(str(f) for f in _retry_findings))
            print(f"[pm-verify] HELD after all passes: {_findings_text[:1200]}")
            _H._chatbot_error_email(
                'brief-chat/verify',
                'generated read HELD after failed verification '
                '(not banked, not delivered): '
                + _findings_text[:1600],
                user_email=pm_user,
                payload={'prompt': text[:300],
                         'question': text[:300],
                         'subject': res.get('subject'),
                         'base': base.get('s3_key')})
            _C._pm_ask_hint(outcome='held')
            # Calm promise instead of a dead end (Jenna 2026-09-30); the
            # held job is retried by read_recovery before anyone is paged.
            return {
                'success': True, 'action': 'answer',
                'reply': _H._CHATBOT_CALM_MESSAGE,
                'followups': [], 'offer_deck': False,
                'deck_angle': None, '_held': True, '_family': fam0,
                '_stages_ms': stages}
    stages['verify'] = int((time.monotonic() - _t_verify) * 1000)
    # Arithmetic coherence (2026-11-05, the Jeopardy Volume 2 read: "38.4%
    # (132,414 people)" of a 95,274 group). Every 'X% (N people)' pair is
    # checked against the base its own sentence names and the count is
    # recomputed when it sits on another base; a partition of shares that
    # misses 100 is noted. The trail carries it; the reader sees the
    # corrected figure and nothing else.
    _arith_trail = ''
    try:
        from prometheus import arith_coherence as _arith
        try:   # a figure this pass bound to the record or held to the thread is on record too
            _on_record = set(_on_record) | {int(h['to']) for h in (_tb_held or []) if isinstance(h, dict) and h.get('to')}
        except Exception as _fx:
            _faults.note('chat', _fx)
        reply, _arith_info = _arith.apply(reply, res, subject=str(res.get('subject') or ''), held=_on_record)
        _arith_trail = _arith.trail(_arith_info)
        if _arith_trail:
            print(f"[pm-verify] arithmetic coherence: {_arith_trail[:700]}")
        if _arith_info.get('fixes'):
            stages['arith_fixed'] = len(_arith_info['fixes'])
    except Exception:
        traceback.print_exc()
    # Bound purchase facts (2026-10-06): measured figures in place.
    if _purchase_facts and pmv is not None:
        try:
            reply, res, _nfix = pmv.facts_enforce(reply, res, _purchase_facts)
            if _nfix:
                stages['facts_fixed'] = int(_nfix)
                print(f"[pm-verify] bound facts enforced in place: {_nfix} figure(s)")
        except Exception:
            traceback.print_exc()
    # verify_outcome: 0 clean, 1 passed after revision, 2 held, 3 pass
    # unavailable, 4 auto-corrected then shipped (silent, in place).
    stages['verify_outcome'] = (3 if verdict is None
                                else (1 if verify_revised else 0))
    if _pm_auto_corrected:
        stages['verify_outcome'] = 4
    _verify_stamp = None
    if pmv is not None and verdict is not None:
        try:
            _verify_stamp = pmv.stamp(verdict, revised=verify_revised)
        except Exception as _fx:
            _faults.note('chat', _fx)
    if _verify_stamp is not None and _arith_trail:
        _verify_stamp['arith'] = {'fixed': int(stages.get('arith_fixed') or 0), 'trail': _arith_trail[:800]}
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:3]
    # The CSV rides the answer itself (2026-09-29 Jenna), so the
    # download chip is retired here; the email chip lands after
    # persist so it is never stored on the ledger entry.
    followups = [f for f in followups if f != pma.CSV_OFFER_CHIP]
    if not bank:
        # one piece of a matrix read: the structured result goes back to
        # the assembler; nothing persists, files or lands here
        stages['verify_outcome'] = (3 if verdict is None else (1 if verify_revised else 0))
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups,
                'offer_deck': False, 'deck_angle': None, 'profile': res.get('subject'),
                '_res': res, '_family': ('strategy' if is_strategy else res.get('metric_family')),
                '_verify': None, '_stages_ms': stages, '_bound': _bound}
    # The answer opens by naming the audience it used (2026-09-29
    # Jenna: the screen no longer binds by default, so the binding is
    # stated up front and a wrong one is visible in the first line).
    # The opener and the counting block name the same audience, as a
    # reader would say it: subject plus cohort once ('On Mountain Dew
    # drinkers who watch scary movies - Mountain Dew drinkers who watch
    # scary movies:' shipped on 2026-10-21), never a key.
    from prometheus import methodology as _meth
    # A title-viewing read on a person names the universe it counted, not
    # the person (Jenna 2026-11-04: the Jason Statham profile is people
    # with digital touchpoints to him; viewers of his films are another
    # universe). 'On people who watched a Jason Statham film on Prime
    # Video in the window:' opens the read and the Base line says the
    # same; the subject stays the person for the ledger and the thread.
    try:
        if str(base.get('source') or '') != 'view':
            if not _C._pm_name_two_audiences(res, text, base) and not str(res.get('universe') or '').strip():
                _uni_subj = _meth.audience_label(res, '')
                _uni = _page_default.title_universe_label(
                    text, _uni_subj, category=_page_default.subject_category(_uni_subj, base))
                if _uni:
                    res['universe'] = _uni
    except Exception:
        traceback.print_exc()
    _aud = _meth.audience_label(res, '')
    # one opener, never two (2026-11-11: 'On people who watched Mad Men in
    # the window:' over the headline's own 'On people who watched Mad Men:')
    reply = __import__('prometheus.read_opener', fromlist=['with_opener']).with_opener(reply, _aud)
    # Counting block under every viewership figure (Liz 2026-10-08).
    try:
        reply = _meth.ensure(reply, res, question=text,
                             base_label=str(base.get('subject') or ''),
                             domain=str(base.get('domain') or ''),
                             view_context=(base.get('view_context') if str(base.get('source') or '') == 'view' else None))
    except Exception:
        traceback.print_exc()
    # The finished answer agrees with itself (2026-11-11): rows sum to
    # their pools, one figure is one value in the headline, the stat line,
    # the table, the file and the record, a stated change is the
    # difference of the two counts beside it. Checked last, on the text
    # the reader gets and the result everything else is written from;
    # fixed in place, never held.
    try:
        from prometheus import read_identities as _rid
        reply, _id_info = _rid.enforce(reply, res, held=_on_record)
        _id_trail = _rid.trail(_id_info)
        if _id_trail:
            print(f"[pm-verify] {_id_trail[:700]}")
            stages['identities_fixed'] = len(_id_info.get('fixes') or [])
            _arith_trail = (_arith_trail + '; ' if _arith_trail else '') + _id_trail[:400]
        if _verify_stamp is not None:
            _verify_stamp.setdefault('arith', {'fixed': int(stages.get('arith_fixed') or 0), 'trail': ''})
            _verify_stamp['arith']['identities'] = str(_id_info.get('outcome') or 'pass')
            if _id_trail:
                _verify_stamp['arith']['trail'] = (str(_verify_stamp['arith'].get('trail') or '') + '; ' + _id_trail)[:800].lstrip('; ')
    except Exception:
        traceback.print_exc()
    _t_stage = time.monotonic()
    try:
        anchor_names = []
        for ln in (anchors_block or '').splitlines():
            ln = ln.strip().lstrip('-').strip()
            if ln and len(anchor_names) < 6:
                anchor_names.append(ln[:120])
        _verify_note = ''
        if _verify_stamp:
            _verify_note = (
                f"; verify={_verify_stamp['outcome']}"
                f"(anchor={_verify_stamp['anchor']},"
                f"ledger={_verify_stamp['ledger']},"
                f"scrub={_verify_stamp['scrub']})")
        _derivation = (
            f"base={base.get('s3_key') or ''}; "
            f"neighbors={', '.join(neighbor_names) or 'none'}; "
            f"examples={'yes' if examples_block else 'no'}; "
            f"subiq={subiq_show or 'no'}; "
            f"research=web_search; "
            f"playbook="
            f"{'panel_report' if _is_panel else ('strategy' if is_strategy else 'standard')}"
            + _verify_note
            + (f"; {_arith_trail}" if _arith_trail else ''))
        # Every figure is stored with what it counted (the universe the
        # opener named, each metric's definition) and the unprojected
        # count behind it, so a later reconcile ask reads the record
        # instead of sizing anything again (Jenna 2026-11-04).
        # Every row carries its definition and window before it banks, so
        # the next pass reads the same base; a read on a product view
        # banks under the view's own title (2026-11-04, Liz).
        _fb.stamp_definitions(res)
        _persist_subject, _persist_cohort = _C._pm_persist_identity(res, base)
        il.persist(
            subject=_persist_subject,
            metric_family=fam0,
            question=text, route='reasoned_metrics',
            metrics=il.attach_sample_people(res.get('metrics'), universe=_aud),
            anchors=anchor_names,
            window_start=res.get('window_start'),
            window_end=res.get('window_end'),
            window_label=res.get('window_label'),
            reply=reply, followups=followups,
            base_profile_key=base.get('s3_key') or (base.get('subject') if str(base.get('source') or '') == 'view' else ''),
            cohort=_persist_cohort,
            breakdown=res.get('breakdown'),
            derivation=_derivation,
            verify=_verify_stamp,
            universe=_aud,
            # a viewing-chain read banks under both titles and the metric; a
            # follow-up's chain is completed from the question it followed
            # (2026-11-05)
            journey=il.ask_journey(text, history=history))
    except Exception:
        traceback.print_exc()
    stages['persist'] = int((time.monotonic() - _t_stage) * 1000)
    _C._pm_remember_ask(pm_user, text, subject=res.get('subject'),
                     cohort=res.get('cohort'), route='generated')
    # Every answer with data creates its CSV (2026-09-29 Jenna); an explicit
    # file ask also auto-saves; the stash serves "Email me this file".
    _file_payload = {}
    try:
        if res.get('breakdown') or res.get('metrics'):
            _explicit = bool(_C._PM_FILE_ASK_RE.search(str(text or '')))
            _fe = {'subject': res.get('subject'), 'cohort': res.get('cohort'),
                   'question': text, 'metrics': res.get('metrics'),
                   'breakdown': res.get('breakdown'), 'ws': res.get('window_start'),
                   'we': res.get('window_end'), 'wl': res.get('window_label'),
                   'family': res.get('metric_family')}   # the file's pool noun and note follow the family (2026-11-06)
            _fn, _fcsv = pma.build_generated_csv(_fe)
            _frng = ''
            if _fe.get('ws') and _fe.get('we'):
                _frng = (f"{_H._fmt_study_date(_fe['ws'])} - "
                         f"{_H._fmt_study_date(_fe['we'])}")
            elif _fe.get('wl'):
                _frng = str(_fe['wl'])
            _fcsv = _H._stamp_csv_text(_fcsv, _frng)
            _fn = _C._pm_csv_task_filename(_fe) or _fn
            _fkey = f"{_C._PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{_fn}"
            _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=_fkey,
                                 Body=_fcsv.encode('utf-8'), ContentType='text/csv')
            _furl = _H.s3_client.generate_presigned_url(
                'get_object', Params={'Bucket': _H.S3_BUCKET, 'Key': _fkey,
                                      'ResponseContentDisposition': _file_names.disposition(_fn)},
                ExpiresIn=7 * 24 * 3600)
            _file_payload = {'file_link': {
                'url': _furl, 'label': f"Download {_fn}", 'name': _fn, 'key': _fkey}}
            if _explicit:
                _file_payload.update(
                    {'download_url': _furl, 'filename': _fn})
                reply += (f"\n\n{_fn} is saving to your browser "
                          "downloads now.")
            _pm_file_stash_write(pm_user, _furl, _fn, _fkey,
                                 subject=res.get('subject'),
                                 question=text)
            if 'Email me this file' not in followups:
                followups.append('Email me this file')
    except Exception:
        traceback.print_exc()
    try:   # the visual rides the read (asked for, or a distribution)
        from prometheus import charts as _charts
        _chart_payload = _charts.publish(res, text, pm_user, _H.s3_client, _H.S3_BUCKET)
    except Exception:
        traceback.print_exc()
        _chart_payload = {}
    # the whole table rides the payload too (the API door's facts, the
    # job record); the chat shows its top rows and the file the rest
    _table_payload = {}
    try:
        if res.get('breakdown') or res.get('metrics'):
            _table_payload = {'metrics': list(res.get('metrics') or []),
                              'breakdown': (dict(res['breakdown']) if isinstance(res.get('breakdown'), dict) else None)}
            if res.get('_matrix'):
                _table_payload['matrix'] = dict(res['_matrix'])
    except Exception:
        _table_payload = {}
    _C._pm_ask_hint(
        outcome=('corrected' if _pm_auto_corrected else 'answered'),
        subject=res.get('subject'))
    # The ask answered away from the profile that was open: carry a
    # one-tap switch chip that re-runs it bound to that profile
    # (2026-09-29). Not persisted - the chip is contextual.
    _sw_payload = {}
    _sw = str(switch_page or '').strip()
    if _sw and _H._normalize_for_match(_sw) != _H._normalize_for_match(
            str(res.get('subject') or '')):
        _sw_chip = f'On {_sw} instead'
        if _sw_chip not in followups:
            followups.append(_sw_chip)
        _sw_payload = {'memory_confirm': {
            'question': text,
            'options': [{'label': _sw_chip, 'subject': _sw}]}}
    return {
        'success': True, 'action': 'answer', 'reply': reply, **_chart_payload, 'figures': _fig_ledger2,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': res.get('subject'),
        '_family': fam0,
        '_verify': _verify_stamp,
        '_stages_ms': stages,
        **_table_payload,
        **_sw_payload,
        **_file_payload}


__all__ = ['_pm_generate_metrics_response', '_pm_generate_read_core']
