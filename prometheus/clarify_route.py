"""The clarify route: the pricing copy and question, the Subscriber IQ notes, the manual-look heads-up, the universe phrase, and the turn that answers a chat message against the open brief.

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
import threading
import traceback
import re
from prometheus import faults as _faults
from prometheus import window_scope as _wscope
from prometheus import named_platforms as _named_platforms
from prometheus import question_first as _question_first
from datetime import datetime, timedelta, date, timezone
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import plan_allowance as _plan
from prometheus import order_quote as _oq
from prometheus import rate_card as _rate_card

# <<moved>>
# <<block: 1>>
_PM_PRICING_COPY = _rate_card.render('$')


# <<block: 2>>
def _pm_pricing_copy(username=None, user=None):
    """The rate card in the seat's currency (East Tree Media and Omaze
    read it in pounds, Jenna 2026-10-08) with live prices. An unlimited
    seat (or Crosswalk staff) reads "Included with your plan." and no
    price (Jenna 2026-11-08)."""
    try:
        from prometheus.priced_read import included_with_plan as _incl
        if (username or user) and _incl(user, username, None):
            return _rate_card.render(included=True)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return _rate_card.render(_C._pm_money_symbol(username),
                             lambda key, dflt: _C._pm_usd(key, dflt, username))


# <<block: 3>>
_PM_SUBJECT_FIELDS = ('subject', 'name', 'display_name', 'subject_label',
                      'title')


# <<block: 4>>
def _pm_sanitize_draft_subjects(spec_draft):
    """2026-10-02 audit. Cleans every name field on the draft through
    prometheus.guards.sanitize_subject_label. Returns a plain question
    for the user when nothing usable is left (the ask was a question
    fragment, not an audience), else None."""
    if not isinstance(spec_draft, dict):
        return None
    from prometheus import guards as _pg
    for k in _C._PM_SUBJECT_FIELDS:
        v = spec_draft.get(k)
        if not isinstance(v, str) or not v.strip():
            continue
        clean = _pg.sanitize_subject_label(v)
        if clean and clean != v:
            spec_draft[k] = clean
        elif not clean:
            spec_draft[k] = ''
    subj = spec_draft.get('subject') or spec_draft.get('name')
    if subj:
        return None
    return ("I did not catch which audience to build. Name the person, "
            "brand, title, or group (for example \"Spiderwick Chronicles "
            "viewers\" or \"Starz subscribers\") and I will set up the "
            "brief.")


# <<block: 5>>
def _pm_subiq_platform_only_question(spec_draft, text):
    """2026-10-02 audit. A Subscriber IQ draft whose title slot holds a
    streaming service, not a show, asks for the title instead of
    researching an air window for a network. Returns a guidance
    payload or None."""
    if not isinstance(spec_draft, dict):
        return None
    from prometheus import guards as _pg
    title = ''
    for k in ('title', 'show', 'subject', 'name'):
        v = spec_draft.get(k)
        if isinstance(v, str) and v.strip():
            title = v.strip()
            break
    if not title or not _pg.is_streaming_platform(title):
        return None
    plat = _pg.humanize_name(title)
    return {
        'success': False, 'guidance': True,
        'error': (f"Subscriber IQ reads one title on a service, so I need "
                  f"the show. Which {plat} title and season? For example "
                  f"\"Outlander season 7 on {plat}\". If you want the whole "
                  f"{plat} subscriber base as an audience, say \"{plat} "
                  "subscribers\" and I will set up that profile instead."),
        'followups': [f"{plat} subscribers as a Profile IQ"],
    }


# <<block: 6>>
def _pm_note_existing_subiq_read(spec_draft):
    """Adds a plain note to a Subscriber IQ draft when the library
    already holds a read for its title: 'X already has a Subscriber IQ
    read (finished <date>); approving builds a fresh copy.'"""
    if not isinstance(spec_draft, dict):
        return
    import prometheus_analysis as _pma
    title = ''
    for k in ('title', 'show', 'subject', 'name'):
        v = spec_draft.get(k)
        if isinstance(v, str) and v.strip():
            title = v.strip()
            break
    if not title:
        return
    rows = _pma.match_subiq_shows(_H.s3_client, _H.SUBSCRIBER_S3_BUCKET,
                                  title, limit=1)
    if not rows:
        return
    show, _key, lm = rows[0]
    when = ''
    try:
        from datetime import datetime as _dt
        when = _dt.strptime(str(lm or '')[:16],
                            '%Y-%m-%dT%H:%M').strftime('%b %-d, %Y')
    except Exception:
        when = ''
    note = (f"{show} already has a Subscriber IQ read in the library"
            + (f" (finished {when})" if when else '')
            + ". Approving builds a fresh copy; to view the existing "
            "one for free, open it from the Subscriber IQ tab.")
    spec_draft['existing_subiq_show'] = show
    spec_draft['existing_subiq_note'] = note
    assumptions = spec_draft.get('assumptions')
    if isinstance(assumptions, list):
        if note not in assumptions:
            assumptions.insert(0, note)
    else:
        spec_draft['assumptions'] = [note]


# <<block: 7>>
def _pm_pricing_question(text):
    """True when the ask is about what things COST. Balance and usage
    asks ('how many credits do I have left'), category mentions
    ('credit provider'), and analytic how-much asks ('how much does
    the female cut index on Nike') never match."""
    t = str(text or '').strip().lower()
    if not t or len(t) > 300:
        return False
    if re.search(r'credit (provider|card|karma|union|score)', t):
        return False
    if re.search(r'\b(do i have|left|remaining|balance|my credits'
                 r'|have i used|usage so far)\b', t):
        return False
    # explicit cost vocabulary with product / credit context
    if re.search(r'\b(pricing|price list|rate card|price sheet)\b', t):
        return True
    if re.search(r'\bhow (is|does)\b.{0,40}\b(billed|billing|charged)\b',
                 t) or re.search(r'\b(billing|metered) rate\b', t):
        return True
    if (re.search(r'\b(cost|costs|price|prices|charge|charges|charged'
                  r'|billed)\b', t)
            and re.search(r'\b(credit|credits|profile|pull|cut|report'
                          r'|deck|build|journey|subscriber|flywheel'
                          r'|attribution|brand partnership|prometheus'
                          r'|search|it)\b', t)):
        return True
    # object-noun form: 'how much is a credit / a profile / one pull' -
    # rejected when the sentence is an analytic read, not a price ask
    if re.search(r'\bhow much (is|are|does|do|would|will) (a|an|the|one'
                 r'|it|each|per)\b.{0,40}\b(credit|profile|pull|cut'
                 r'|report|deck|build|journey|subscriber|flywheel'
                 r'|attribution|brand partnership)\b', t):
        if not re.search(r'\b(index|indexes|overlap|watch|viewers'
                         r'|audience|penetration|reach|skew|engage)\b',
                         t):
            return True
    return False


# <<block: 8>>
# Manual-look heads-up (2026-08-27, Jenna, verbatim: "it should never
# say it can't do it or show code it should say working on it and
# email me and jessie"). Fired whenever Prometheus resolves an ask
# only partially or not at all: the user sees calm, jargon-free copy
# and Jenna + Jessie get one clean email per ask with the ask
# verbatim, what the user was told, and the reason in plain language.
# No tracebacks, no internal vocabulary - this is a workflow email,
# not the ops debug email above.
_PM_MANUAL_LOOK_COOLDOWN_S = 3600   # one email per ask, not per retry


# <<block: 9>>
_PM_MANUAL_LOOK_STAMP_FILE = '/tmp/prometheus_manual_look_stamps.json'


# <<block: 10>>
_pm_manual_look_stamps = {}


# <<block: 11>>
_pm_manual_look_lock = threading.Lock()


# <<block: 12>>
def _pm_universe_phrase(draft):
    """'total universe' or 'total <viewers|listeners|readers|players>
    universe', from the draft's consumption scope (2026-09-21, Jessie:
    a viewers build confirmed as 'the national total universe' reads
    like the wrong pull). The noun comes from the subject's own
    audience word, falling back to consumer_verb on seeded builds. A
    draft scoped to the platform the client named says so ('total
    viewers universe on Peacock', 2026-11-10)."""
    phrase = 'total universe'
    try:
        d = draft or {}
        subj = str(d.get('subject') or d.get('name') or '')
        import re as _re_up
        m = _re_up.search(r'\b(viewers|listeners|readers|players)\b',
                          subj, _re_up.I)
        verb = str(d.get('consumer_verb') or '').strip().lower()
        if m:
            phrase = f'total {m.group(1).lower()} universe'
        elif verb in ('viewers', 'listeners', 'readers', 'players'):
            phrase = f'total {verb} universe'
        elif str(d.get('seed_source') or '').startswith('content_map'):
            phrase = 'total viewers universe'
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        from prometheus import named_platforms as _np_up
        return _np_up.universe_phrase(draft, phrase)
    except Exception:
        return phrase


# <<block: 13>>
@_H.app.route('/api/brief-chat/clarify', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/clarify')
def api_synth_chat_clarify():
    """One clarify step of the guided flow. Stateless: the draft rides
    the request and the updated draft rides the response, exactly like
    the date-confirmation stash pattern.

    Steps (2026-08-19 Cut Strategist rework):
      goal     -> capture business_goal, run the strategist, ask for
                  cut picks (next_step=strategy)
      strategy -> parse picks/names/markets into addon_cuts, price,
                  release the approval card (next_step=approve)
      region / cuts -> legacy steps kept for stale clients. Region
                  answers become DMA CUTS (3 credits each); the base
                  build is ALWAYS the national total universe + avid.

    body: { step: str, answer: str, draft: {...} }
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Funds gate (2026-09-16, Jenna): clarify steps cost model calls.
    _funds_resp = _C._pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/clarify', e)
        return jsonify(_H._chatbot_calm_payload())
    step = str(body.get('step') or '').strip().lower()
    answer = str(body.get('answer') or '').strip()
    draft = body.get('draft') or {}
    if step not in ('goal', 'strategy', 'region', 'cuts', 'parent_link',
                    'age_breaks', 'ip_scope', 'viewer_scope',
                    'viewer_audience', 'identity',
                    'existing_profile', 'qualifier_match',
                    'subiq_window', 'subiq_upsell',
                    'subiq_or_profile') \
            or not isinstance(draft, dict):
        _H._chatbot_error_email('brief-chat/clarify',
                             f'bad clarify step: {step!r}',
                             tb='(request validation)')
        return jsonify(_H._chatbot_calm_payload())

    import re as _re
    subject = str(draft.get('subject') or draft.get('name')
                  or 'this profile')
    # a question or a "not building" typed at a brief's open question is not an answer to it (prometheus.question_first)
    _side = _question_first.at_open_step(answer, step, subject)
    if _side:
        return jsonify({'success': True, 'draft': draft, 'discard': bool(_side.get('discard')), 'message': _side['message'],
                        'next_step': '' if _side.get('discard') else step})
    base_credits = int(draft.get('base_credits')
                       or draft.get('estimated_credits') or 5)
    draft.setdefault('base_credits', base_credits)

    def _finalize_cuts_response(notes=None):
        """Price whatever is on draft['addon_cuts'] and release the
        approval card. Base ALWAYS covers the national TU + avid;
        every cut is +ADDON_CUT_CREDITS derived from that parent."""
        cuts = draft.get('addon_cuts') or []
        qcuts = [q for q in (draft.get('quarter_cuts') or [])
                 if isinstance(q, dict) and q.get('label')]
        n_all = len(cuts) + len(qcuts)
        total = base_credits + _H.ADDON_CUT_CREDITS * n_all
        draft['estimated_credits'] = total
        draft.pop('strategist_recs', None)
        if qcuts or (draft.get('price_table') or {}).get('quarter_reads'):   # the card's table holds the quarter reads as they now stand
            draft['price_table'] = _oq.table_for_draft(_H, None, draft, draft.get('price_table'))
        if cuts or qcuts:
            _lk = _C._pm_seat_reads_included()   # an included seat reads no price on the cuts or the total (2026-11-17)
            _lq = _oq.lock(_H, draft)   # what Approve charges: the build, its add-on cuts and its quarter reads (2026-11-18)
            cut_lines = "\n".join(
                [f"  - {c.get('label') or c.get('cut_id')}"
                 f"{_plan.cut_tag(_lk, _C._pm_usd_label(_C._pm_cut_usd(draft)))}" for c in cuts]
                + [f"  - {q['label']} quarter read "
                   f"({_H._ew_format_label(q.get('start'), q.get('end'))})"
                   f"{_plan.cut_tag(_lk or not _lq['quarters'], _C._pm_usd_label(_lq['quarter_each']))}" for q in qcuts])
            msg = (f"Locked in {n_all} cut"
                   f"{'s' if n_all != 1 else ''}:\n{cut_lines}\n\n"
                   f"{_plan.lock_total(_lk, _C._pm_usd_label(_lq['total']), _C._pm_usd_label(_lq['base']))}"
                   f"covers the national {_C._pm_universe_phrase(draft)} "
                   f"+ avid; "
                   f"{_plan.lock_cuts(_lk, _lq['n'], _C._pm_usd_label(_lq['each']))}"
                   "each derived from that national parent so the "
                   "numbers ladder up"
                   + (" - every quarter ships as its own dated file"
                      if qcuts else "")
                   + "). Review the brief below and approve to start the build.")
        else:
            msg = (f"The build covers the whole audience: the national "
                   f"{_C._pm_universe_phrase(draft)} + avid. Review the "
                   "brief below and approve to start the build.")
        if notes:
            real = [n for n in notes if n not in
                    ('parse_partial', 'parse_failed')][:4]
            if real:
                # Never a can't-do line (Jenna 2026-08-27): the user
                # sees a calm working line and Jenna + Jessie get the
                # manual-look email with the unmapped piece verbatim.
                held = ", ".join(real)
                msg = (f"Working on: {held}. That piece needs a "
                       "closer look and I'll come back to you on it. "
                       + msg)
                try:
                    _H._prometheus_manual_look_email(
                        answer or held, msg,
                        'The reply to the cut picker included '
                        'something that does not match any known '
                        'cut: ' + held + '. It may be a measurement '
                        'question that arrived mid-draft.')
                except Exception:
                    traceback.print_exc()
        return jsonify({'success': True, 'draft': draft,
                        'message': msg, 'next_step': 'approve'})

    if step == 'identity':
        # Subject identity confirmation (2026-08-24 Furious defect):
        # the interpret step could not confidently place the title, so
        # the user confirms WHAT the subject is before anything else.
        data = draft.get('identity_data') or {}
        title = str(data.get('title') or subject).strip()
        medium = str(data.get('medium') or 'title').strip()
        platform = str(data.get('platform') or '').strip()
        where = f' on {platform}' if platform else ''
        low = answer.lower().strip()
        said_no = bool(_re.search(
            r'\b(no|nope|nah|not that|none|neither|something else|'
            r'different|wrong|other)\b', low))
        # Same-name versions / alias candidates (2026-08-25): more
        # than one entity carries this name and the user picks which
        # one - a number (the chips send '1'..'N'), the title, or the
        # version label. Never a silent default.
        versions = [v for v in (data.get('versions') or [])
                    if isinstance(v, dict)
                    and str(v.get('title') or '').strip()]
        if versions:
            pick = None
            m = _re.match(r'^\s*(?:option\s*)?(\d{1,2})\b', answer or '')
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(versions):
                    pick = versions[idx]
            if pick is None:
                coll_ans = _H._collapse_for_match(answer)
                for v in versions:
                    t_coll = _H._collapse_for_match(
                        str(v.get('title') or ''))
                    l_coll = _H._collapse_for_match(
                        str(v.get('version_label') or ''))
                    full_coll = _H._collapse_for_match(
                        f"{v.get('title', '')} "
                        f"{v.get('version_label', '')}")
                    keys = [k for k in (t_coll, full_coll, l_coll) if k]
                    if coll_ans and any(
                            coll_ans == k
                            or (len(k) >= 4 and k in coll_ans)
                            for k in keys):
                        pick = v
                        break
            if pick is None and said_no:
                draft.pop('ask_identity', None)
                draft.pop('identity_data', None)
                return jsonify({
                    'success': True, 'draft': draft, 'discard': True,
                    'message': (
                        "No problem - that brief is set aside, "
                        "nothing queued. Tell me the subject with "
                        "one more detail (what it is and where it "
                        "lives) and I'll draw up a fresh one."),
                    'next_step': '',
                })
            if pick is None:
                lines = [f"More than one {title} exists. Which one "
                         f"do you mean?"]
                for i, v in enumerate(versions, 1):
                    lbl = str(v.get('version_label') or '').strip()
                    lines.append(
                        f"  {i}. {v['title']}"
                        + (f" ({lbl})" if lbl else ''))
                lines.append("Reply with a number, or say no if it's "
                             "something else.")
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': "\n".join(lines),
                    'next_step': 'identity',
                })
            _H._apply_identity_version(draft, pick)
            # A researched person pick (short-name picker, 2026-11-04)
            # re-anchors the identity line, prose and category to that
            # figure; a plain version pick is unchanged.
            _person_head = ''
            try:
                from prometheus import short_name_identity as _sni_pick
                if _sni_pick.apply_pick(draft, pick):
                    _person_head = _sni_pick.confirm_head(
                        pick, str(draft.get('subject') or title))
            except Exception as _fx:
                _faults.note('chat', _fx)
            draft.pop('ask_identity', None)
            draft.pop('identity_data', None)
            title = str(draft.get('subject') or title).strip()
            medium = str(pick.get('medium') or medium).strip() or 'title'
            platform = str(pick.get('platform') or '').strip()
            where = f' on {platform}' if platform else ''
        else:
            _person_head = ''
            said_yes = (bool(_re.search(
                r'\b(yes|yep|yeah|yup|correct|right|confirm(ed)?|'
                r'exactly|that one|it is)\b', low))
                or _H._collapse_for_match(low) == _H._collapse_for_match(title))
            if said_no and not said_yes:
                draft.pop('ask_identity', None)
                draft.pop('identity_data', None)
                return jsonify({
                    'success': True, 'draft': draft, 'discard': True,
                    'message': (
                        "No problem - that brief is set aside, nothing "
                        "queued. Tell me the subject with one more "
                        "detail (what it is and where it lives - like "
                        "'Furious, the series on Hulu') and I'll draw "
                        "up a fresh one."),
                    'next_step': '',
                })
            if not said_yes:
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': (f"Just to confirm the subject: {title}, "
                                f"the {medium}{where}? Reply yes if "
                                f"that's it, or no and tell me what you "
                                f"meant."),
                    'next_step': 'identity',
                })
            draft.pop('ask_identity', None)
            draft.pop('identity_data', None)
        draft['identity_confident'] = True
        try:
            _H._set_resolved_identity_line(draft)
        except Exception as _fx:
            _faults.note('chat', _fx)
        head = _person_head or f"Confirmed - {title}, the {medium}{where}."
        # Continue the flow in the same order interpret queued it.
        if draft.get('ask_qualifier_match') and \
                draft.get('qualifier_match_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head,
                            'next_step': 'qualifier_match'})
        if draft.get('ask_existing_profile') and \
                draft.get('existing_profile_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head,
                            'next_step': 'existing_profile'})
        if draft.get('ask_ip_scope'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'ip_scope'})
        if draft.get('ask_age_breaks') and draft.get('age_break_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'age_breaks'})
        if draft.get('ask_parent_link') and \
                draft.get('parent_link_candidates'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'parent_link'})
        if str(draft.get('decision') or '').strip().lower() in (
                'new_build', 'time_shifted_refresh', 'cut_needs_parent'):
            return jsonify({
                'success': True, 'draft': draft,
                'message': (head + " Quick scoping question: what's "
                            "the business goal for this one? (One "
                            "line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to the "
                            "build.)"),
                'next_step': 'goal',
            })
        return jsonify({'success': True, 'draft': draft,
                        'message': head + " Review the brief below "
                        "and approve to start the build.",
                        'next_step': 'approve'})

    if step == 'qualifier_match':
        # Qualifier-match confirmation (2026-08-25, Jenna): the closest
        # existing profile is the same brand but a different universe
        # scope ('Apple buyers' vs 'Apple TV EST Buyers'). 'We have X -
        # is that what you meant?' Yes routes to the existing profile
        # (existing_match semantics, 0 credits); no proceeds with the
        # fresh build for the subject exactly as the user stated it.
        data = draft.get('qualifier_match_data') or {}
        disp = str(data.get('display_name') or '').strip()
        from prometheus import match_question as _mq   # "Build Euphoria series viewers" at the question is a no (2026-11-17)
        said_yes, said_no = _mq.reply_reading(answer, draft.get('subject') or subject, disp)
        if said_yes and not said_no and disp:
            entity = disp.split(' - ', 1)[0].strip()
            draft['subject'] = entity
            draft['name'] = entity
            draft.pop('file_stem', None)
            draft['decision'] = 'existing_match'
            draft['run_avid'] = False
            draft['addon_cuts'] = []
            draft['estimated_credits'] = int(
                _H._V1_CREDITS.get('existing_match', 0))
            draft['base_credits'] = draft['estimated_credits']
            draft['existing_match_s3_key'] = data.get('s3_key')
            draft['existing_match_display_name'] = disp
            try:
                _H._apply_existing_match_retail_price(
                    draft, user,
                    session.get('username') or user.get('username') or '')
            except Exception:
                traceback.print_exc()
            draft['existing_match_days_old'] = data.get('days_old')
            draft['existing_match_last_modified'] = data.get(
                'last_modified')
            draft['decision_reason'] = (
                f"You confirmed the existing {disp} profile is the "
                f"audience you meant.")
            draft.pop('ask_qualifier_match', None)
            draft.pop('qualifier_match_data', None)
            draft.pop('related_profile_display_name', None)
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"Done - {disp} is ready now in the "
                            f"Select Profile dropdown, no new run "
                            f"needed. Approve below to confirm."),
                'next_step': 'approve',
            })
        if said_no and not said_yes:
            draft.pop('ask_qualifier_match', None)
            draft.pop('qualifier_match_data', None)
            draft['decision'] = 'new_build'
            draft.pop('existing_match_s3_key', None)
            draft.pop('existing_match_display_name', None)
            draft.pop('existing_match_days_old', None)
            draft.pop('existing_match_last_modified', None)
            subj_now = str(draft.get('subject') or subject).strip()
            head = (f"Got it - fresh build for {subj_now}, exactly as "
                    f"you described it.")
            # Continue the flow in the same order interpret queued it.
            if draft.get('ask_existing_profile') and \
                    draft.get('existing_profile_data'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'existing_profile'})
            if draft.get('ask_ip_scope'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'ip_scope'})
            if draft.get('ask_age_breaks') and \
                    draft.get('age_break_data'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'age_breaks'})
            if draft.get('ask_parent_link') and \
                    draft.get('parent_link_candidates'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'parent_link'})
            return jsonify({
                'success': True, 'draft': draft,
                'message': (head + " Quick scoping question: what's "
                            "the business goal for this one? (One "
                            "line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to the "
                            "build.)"),
                'next_step': 'goal',
            })
        built = str(data.get('built_label') or '').strip()
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"We have {disp}"
                        f"{' (' + built + ')' if built else ''} - is "
                        f"that what you meant? Reply yes to use it, "
                        f"or no to build "
                        f"{draft.get('subject') or subject} fresh."),
            'next_step': 'qualifier_match',
        })

    if step == 'existing_profile':
        # Existing-profile confirmation (2026-08-24 SHARKNINJA
        # directive): the ask names an entity we already have. Use the
        # existing file (0 credits, ready now) or pull fresh.
        data = draft.get('existing_profile_data') or {}
        disp = str(data.get('display_name') or subject).strip()
        low = answer.lower().strip()
        chose_fresh = bool(_re.search(
            r'\b(fresh|new|re\s?pull|pull|rebuild|refresh(ed)?|'
            r'update(d)?|re\s?run|different)\b', low))
        chose_use = bool(_re.search(
            r'\b(use|existing|reuse|keep|grab|open|that works|works|'
            r'yes|yep|yeah|sure|ok|okay)\b', low))
        if chose_use and not chose_fresh:
            draft['decision'] = 'existing_match'
            draft['run_avid'] = False
            draft['addon_cuts'] = []
            if data.get('s3_key'):
                draft['existing_match_s3_key'] = data.get('s3_key')
            if disp:
                draft['existing_match_display_name'] = disp
            draft['estimated_credits'] = int(
                _H._V1_CREDITS.get('existing_match', 0))
            draft['base_credits'] = draft['estimated_credits']
            try:
                _H._apply_existing_match_retail_price(
                    draft, user,
                    session.get('username') or user.get('username') or '')
            except Exception:
                traceback.print_exc()
            draft.pop('ask_existing_profile', None)
            draft.pop('existing_profile_data', None)
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"Done - {disp} is ready now in the "
                            f"Select Profile dropdown, no new run "
                            f"needed. Approve below to confirm."),
                'next_step': 'approve',
            })
        if chose_fresh and not chose_use:
            draft['decision'] = 'time_shifted_refresh'
            draft['run_avid'] = True
            draft['estimated_credits'] = int(_H._V1_CREDITS.get(
                'time_shifted_refresh', _H.CREDITS_PROFILE_ANALYSIS))
            draft['base_credits'] = draft['estimated_credits']
            try:
                draft['estimated_run_minutes'] = _C._estimate_run_minutes(
                    'time_shifted_refresh', True)
            except Exception as _fx:
                _faults.note('chat', _fx)
            if not str(draft.get('refresh_row_hypothesis') or '').strip():
                draft['refresh_row_hypothesis'] = (
                    f"Fresh read of the {disp} audience over the "
                    f"current window: brand, talent, platform, and "
                    f"retail engagement re-checked so recent "
                    f"launches, partnerships, and seasonal shifts "
                    f"are reflected.")
            draft.pop('ask_existing_profile', None)
            draft.pop('existing_profile_data', None)
            head = (f"Got it - fresh pull for "
                    f"{draft.get('subject') or subject}, anchored to "
                    f"the existing file so the numbers stay "
                    f"comparable. It runs on {_H._draft_window_field(draft, 'time_shifted_refresh') or _wscope.default_label()} "
                    f"unless you named a window - to use a "
                    f"different one, send it as a new message (like "
                    f"'{draft.get('subject') or subject} past 90 "
                    f"days') and I'll re-draft.")
            if str(draft.get('decision') or '').strip().lower() in (
                    'new_build', 'time_shifted_refresh',
                    'cut_needs_parent'):
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': (head + " Quick scoping question: "
                                "what's the business goal for this "
                                "one? (One line - a pitch, a "
                                "renewal, a media plan. Say skip to "
                                "jump straight to the build.)"),
                    'next_step': 'goal',
                })
            return jsonify({'success': True, 'draft': draft,
                            'message': head + " Review the brief "
                            "below and approve to start the build.",
                            'next_step': 'approve'})
        built = str(data.get('built_label') or '').strip()
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"There is an existing {disp} profile"
                        f"{' (' + built + ')' if built else ''}. "
                        f"Reply use it to open that one (no new "
                        f"run), or pull fresh for an updated read."),
            'next_step': 'existing_profile',
        })

    if step == 'subiq_or_profile':
        # Ambiguous churn fork (2026-08-26 Jenna: "it would prompt the
        # user, do you want a profile on this or subscriber iq kind of
        # thing"). The pick re-enters the flow as its own request via
        # reinterpret_text so each path keeps its full clarify chain
        # (dates/season confirmation for the read, the build brief for
        # the profile) and the platform or title rides along.
        data = draft.get('subiq_or_profile_data') or {}
        subj = str(data.get('subject') or '').strip()
        low = answer.lower()
        wants_profile = bool(_re.search(
            r'\b(?:2|profile|audience|cohort|who they are)\b', low))
        wants_read = bool(_re.search(
            r'\b(?:1|read|tracker|subscriber iq|sub ?iq|churn|'
            r'cancel\w*|win ?back)\b', low))
        draft.pop('ask_subiq_or_profile', None)
        draft.pop('subiq_or_profile_data', None)
        if wants_read and not wants_profile:
            target = ('Subscriber IQ for ' + subj) if subj \
                else 'Subscriber IQ'
            return jsonify({
                'success': True, 'draft': draft,
                'message': ('On it - the churn and cancellation read'
                            + ((' for ' + subj) if subj else '')
                            + '.'),
                'reinterpret_text': target,
                'next_step': 'reinterpret'})
        if wants_profile:
            aud = ('churned ' + subj + ' subscribers') if subj \
                else 'churned subscribers'
            return jsonify({
                'success': True, 'draft': draft,
                'message': f"On it - an audience profile of {aud}.",
                'reinterpret_text': f"Build an audience profile of {aud}",
                'next_step': 'reinterpret'})
        draft['ask_subiq_or_profile'] = True
        draft['subiq_or_profile_data'] = {'subject': subj}
        return jsonify({
            'success': True, 'draft': draft,
            'message': ('Which one - the churn and cancellation read, '
                        'or an audience profile of the churned '
                        'subscribers? Tap one below or say read or '
                        'profile.'),
            'next_step': 'subiq_or_profile'})

    if step == 'subiq_window':
        # Subscriber IQ dates/season confirmation (2026-08-25, bind-or-
        # ask). The interpret step researched the real air window; the
        # user confirms it, resolves a conflict with their own dates,
        # picks a movie measurement window, or supplies dates directly.
        data = draft.get('subiq_window_data') or {}
        subiq = draft.get('subiq') if isinstance(draft.get('subiq'),
                                                 dict) else {}
        draft['subiq'] = subiq
        kind = str(data.get('kind') or 'confirm').strip().lower()
        title = str(data.get('title') or subiq.get('title')
                    or subject).strip()
        season = data.get('season')
        air = data.get('air_window') or {}
        low = answer.lower().strip()

        def _fmt_win(w):
            return (f"{(w or {}).get('start', '?')} to "
                    f"{(w or {}).get('end', '?')}")

        def _label_now():
            lbl = title
            if season:
                lbl = f"{title} - Season {season}"
                if subiq.get('season_to_date'):
                    lbl = f"{lbl} to date"
            subiq['deliverable_label'] = lbl
            return lbl

        def _bind_window(start, end, note):
            _df = __import__('prometheus.data_floor', fromlist=['gate']).tracker_window(start, subiq.get('through_date') or end)   # no data before the data floor (Jenna 2026-11-18): refused whole, or started on it and said so, look-back included
            if _df.get('refuse'):
                return jsonify({'success': True, 'draft': draft, 'message': _df['refuse'], 'next_step': 'subiq_window'})
            start, note = _df['start'], _df['lead'] + note
            subiq['air_window'] = {'start': start, 'end': end}
            draft['date_range'] = {
                'start': start,
                'end': subiq.get('through_date') or end,
            }
            draft['date_range_explicit'] = True
            draft.pop('ask_subiq_window', None)
            draft.pop('subiq_window_data', None)
            lbl = _label_now()
            nxt = ('subiq_upsell' if draft.get('ask_subiq_upsell')
                   else 'approve')
            tail = (" One more thing below." if nxt == 'subiq_upsell'
                    else " Review the brief below and approve to "
                         "queue.")
            # Every date variable that governs the read is confirmed
            # here, before approval (2026-10-01 Jenna, after a tracker
            # shipped with an unexplained "Exclusion Window: 0" and
            # the requester read the correct file as a build error):
            # the measurement window, the 30-day signup credit tail
            # with its concrete end date, and why the exclusion window
            # reads 0 days.
            shown_end = subiq.get('through_date') or end
            try:
                _credit_end = (datetime.strptime(
                    str(shown_end)[:10], '%Y-%m-%d')
                    + timedelta(days=30)).strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                _credit_end = None
            _plat_name = str(subiq.get('platform')
                             or data.get('platform')
                             or '').strip() or 'the platform'
            terms = (" New signups are credited through "
                     + (f"{_credit_end} (30 days past the last date)"
                        if _credit_end else
                        "30 days past the last date")
                     + f". Standard exclusion window: viewers "
                       f"already on {_plat_name} in the 6 months "
                       f"(180 days) before your start date are split "
                       f"out and never counted as new signups.{_df['lookback']} "
                       f"Approving locks these dates.")
            return jsonify({'success': True, 'draft': draft,
                            'message': f"{note} {lbl} measures "
                                       f"{start} to {shown_end}."
                                       f"{terms}{tail}",
                            'next_step': nxt})

        # Direct dates in the answer always win (any kind).
        _dates = _re.findall(r'(\d{4}-\d{2}-\d{2})', answer)
        if len(_dates) >= 2:
            subiq.pop('season_to_date', None)
            subiq.pop('through_date', None)
            return _bind_window(_dates[0], _dates[1],
                                "Locked to your dates.")

        if kind == 'movie_scope':
            scope = None
            if _re.search(r'\b(theatrical|theater|theatre|box office)\b',
                          low) or low.startswith('1'):
                scope = 'theatrical'
            elif _re.search(r'\b(stream|streaming|svod|on '
                            r'(?:netflix|hulu|max|peacock|paramount|'
                            r'prime|apple|disney))\b', low) \
                    or low.startswith('2'):
                scope = 'streaming'
            elif _re.search(r'\b(since|release|all|everything|both|'
                            r'cumulative|to date)\b', low) \
                    or low.startswith('3'):
                scope = 'since_release'
            if not scope:
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': (f"Which window should {title} measure? "
                                "Reply theatrical (the theater run), "
                                "streaming (its streaming window), or "
                                "since release (everything to date). "
                                "Or send exact dates like 2025-11-16 "
                                "to 2026-01-11."),
                    'next_step': 'subiq_window'})
            subiq['movie_scope'] = scope
            start = str(air.get('start') or '').strip()
            today_iso = datetime.now().strftime('%Y-%m-%d')
            if not start:
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': ("Send the window as two dates like "
                                "2025-11-16 to 2026-01-11 and I'll "
                                "lock it in."),
                    'next_step': 'subiq_window'})
            if scope == 'theatrical':
                try:
                    _s = datetime.strptime(start, '%Y-%m-%d')
                    _e = min(_s + timedelta(days=89), datetime.now())
                    end = _e.strftime('%Y-%m-%d')
                except ValueError:
                    end = str(air.get('end') or today_iso)[:10]
                return _bind_window(start, end,
                                    "Measuring the theatrical window.")
            if scope == 'streaming':
                _sw = None
                try:
                    try:
                        from migration.event_window import (
                            resolve_event_window_via_search as _rev)
                    except ImportError:
                        from event_window import (
                            resolve_event_window_via_search as _rev)
                    _plat = str(data.get('platform')
                                or subiq.get('platform') or '').strip()
                    _sw = _rev(f"{title} streaming premiere date"
                               + (f" on {_plat}" if _plat else ""),
                               run_id='subiq-clarify')
                except Exception as _sw_err:
                    print(f"[subiq-clarify] streaming window research "
                          f"failed: {_sw_err}")
                if _sw and _sw.get('start'):
                    return _bind_window(_sw['start'], today_iso,
                                        "Measuring the streaming "
                                        "window.")
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': ("I could not pin the streaming "
                                "premiere date. Send the window as "
                                "two dates like 2025-11-16 to "
                                "2026-01-11."),
                    'next_step': 'subiq_window'})
            return _bind_window(start, today_iso,
                                "Measuring since release.")

        if kind == 'conflict':
            user_win = data.get('user_window') or {}
            if _re.search(r'\b(mine|my dates|keep|user|stick)\b', low) \
                    or low.startswith('2'):
                subiq.pop('season_to_date', None)
                subiq.pop('through_date', None)
                return _bind_window(
                    str(user_win.get('start') or '')[:10],
                    str(user_win.get('end') or '')[:10],
                    "Keeping your dates.")
            if _re.search(r'\b(research|air|actual|real|correct|'
                          r'yours|use th)\b', low) or low.startswith('1') \
                    or _re.match(r'^(yes|yep|yeah|ok|okay|sure)\b', low):
                return _bind_window(
                    str(air.get('start') or '')[:10],
                    str(air.get('end') or '')[:10],
                    "Using the real air window.")
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"The dates you gave "
                            f"({_fmt_win(user_win)}) differ from the "
                            f"real air window ({_fmt_win(air)}). Reply "
                            f"air window to use the researched dates, "
                            f"my dates to keep yours, or send exact "
                            f"dates."),
                'next_step': 'subiq_window'})

        if kind == 'need_dates':
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"I could not pin the exact window for "
                            f"{title}"
                            + (f" season {season}" if season else "")
                            + ". Send it as two dates like 2025-11-16 "
                              "to 2026-01-11 and I'll lock it in."),
                'next_step': 'subiq_window'})

        # kind in ('confirm', 'season_to_date'): a yes keeps the bound
        # researched window; a no invites dates.
        if _re.match(r'^(yes|yep|yeah|ok|okay|sure|confirm|correct|'
                     r'right|approve|sounds good|that works|good|'
                     r'season to date|to date|1)\b', low):
            return _bind_window(
                str(air.get('start') or '')[:10],
                str(air.get('end') or '')[:10],
                "Confirmed.")
        if _re.search(r'\b(no|nope|different|wrong|change|not)\b', low):
            return jsonify({
                'success': True, 'draft': draft,
                'message': ("No problem. Send the window as two dates "
                            "like 2025-11-16 to 2026-01-11, or name "
                            "the season you meant."),
                'next_step': 'subiq_window'})
        _season_pick = _re.search(r'\bseason\s*(\d{1,2})\b', low)
        if _season_pick and int(_season_pick.group(1)) != (season or 0):
            return jsonify({
                'success': True, 'draft': draft, 'discard': True,
                'message': (f"Got it - season "
                            f"{int(_season_pick.group(1))}. That brief "
                            "is set aside; ask again naming that "
                            "season and I'll research its air window "
                            "fresh."),
                'next_step': ''})
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"Reply yes to measure {_fmt_win(air)}"
                        + (f" (through "
                           f"{data.get('through_date')} so far this "
                           f"season)" if kind == 'season_to_date'
                           else "")
                        + ", or send exact dates like 2025-11-16 to "
                          "2026-01-11."),
            'next_step': 'subiq_window'})

    if step == 'subiq_upsell':
        # Profile IQ add-on offer (2026-08-25): one click adds a
        # Profile IQ for the same title audience, built off the same
        # universe so the two deliverables always agree.
        subiq = draft.get('subiq') if isinstance(draft.get('subiq'),
                                                 dict) else {}
        draft['subiq'] = subiq
        data = draft.get('subiq_upsell_data') or {}
        label = str(data.get('label') or subiq.get('deliverable_label')
                    or subject).strip()
        subiq_credits = int(data.get('subiq_credits') or _H.CREDITS_SVOD)
        _sq = _oq.subiq(_H, draft, label, _C._pm_usd_label)   # the tracker with and without the Profile IQ, as Approve charges it
        low = answer.lower().strip()
        said_yes = bool(_re.match(
            r'^(yes|yep|yeah|ok|okay|sure|add|both|include|do it|'
            r'add it|1)\b', low)) or 'profile' in low and not \
            _re.search(r'\b(no|skip|just|only|without)\b', low)
        said_no = bool(_re.search(
            r'\b(no|nope|skip|just the|only the|not now|pass|'
            r'without)\b', low)) or low.startswith('2')
        if said_yes and not said_no:
            subiq['addon_profile'] = True
            draft['estimated_credits'] = subiq_credits   # the add-on rides the tracker, in credits as in dollars (2026-11-18)
            draft.pop('ask_subiq_upsell', None)
            draft.pop('subiq_upsell_data', None)
            return jsonify({
                'success': True, 'draft': draft,
                'message': _plan.SUBIQ_ADDED if _C._pm_seat_reads_included(_sq['total']) else _sq['added'],
                'next_step': 'approve'})
        if said_no:
            subiq['addon_profile'] = False
            draft['estimated_credits'] = subiq_credits
            draft.pop('ask_subiq_upsell', None)
            draft.pop('subiq_upsell_data', None)
            return jsonify({
                'success': True, 'draft': draft,
                'message': _plan.SUBIQ_JUST if _C._pm_seat_reads_included(_sq['alone']) else _sq['just'],
                'next_step': 'approve'})
        return jsonify({
            'success': True, 'draft': draft,
            'message': _plan.subiq_ask(label) if _C._pm_seat_reads_included(_sq['total']) else _sq['ask'],
            'next_step': 'subiq_upsell'})

    if step == 'ip_scope':
        # IP audience scope (2026-08-21 Jenna directive): broad
        # engagers vs consumers-only. Question data was stashed by the
        # interpret step (_maybe_ask_ip_scope). Every string here is
        # user-facing: plain audience language only, zero build
        # mechanics (2026-08-21 Jenna: never anything that sounds
        # constructed - no percentages, no locking/pinning talk).
        data = draft.get('ip_scope_data') or {}
        verb = str(data.get('verb') or 'viewers').strip().lower()
        if verb not in _H._IP_CONSUMER_VERBS:
            verb = 'viewers'
        low = answer.lower().strip()
        verb_stem = verb.rstrip('s')  # viewer / reader / listener / player
        chose_consumers = bool(_re.search(
            r'\b(consum|viewer|watch|stream|binge|reader|read|listen|'
            r'player|play|narrow|limit)\w*\b|'
            r'^(?:just|only)\b|\b(?:just|only)\s+(?:the\s+)?'
            + verb_stem, low)) or verb_stem in low
        chose_broad = bool(_re.search(
            r'\b(broad|engag|anyone|everyone|every one|all|standard|'
            r'wide|full|both|general)\w*\b', low))
        # A season / film scope answer (2026-08-31 Love Island death-
        # loop) only makes sense for the viewers universe - you never
        # scope seasons for a broad engager audience. When the reply
        # names a scope, treat it as the viewers pick and let the SAME
        # answer bind in the viewer-scope chain below. This also rescues
        # a stale client that keeps the step pinned at ip_scope: the
        # reply resolves the build instead of re-asking broad-vs-viewers
        # in a loop. Forced past the broad detector, which would else
        # fire on the 'all' in 'all seasons'.
        _scope_like = bool(_re.search(
            r'\ball\s+seasons?\b|\bevery\s+season\b|\bmost\s+recent\b|'
            r'\blatest\b|\bnewest\b|\bcurrent\s+season\b|'
            r'\bspecific\s+season\b|\bthis\s+season\b|\bseason\s*\d|'
            r'\bs\d{1,2}\b|\bwhole\s+franchise\b|\ball\s+films?\b|'
            r'\bmost\s+recent\s+film\b|\bspecific\s+film\b|'
            r'\blatest\s+film\b', low))
        if _scope_like:
            chose_consumers = True
            chose_broad = False
        if chose_broad and not chose_consumers:
            _H._apply_ip_scope_to_draft(draft, 'broad')
            head = (f"Broad it is - the profile covers everyone who "
                    f"engaged with {draft.get('subject') or subject} "
                    "(search, social, fan content, commerce), the "
                    "standard build.")
        elif chose_consumers and not chose_broad:
            _H._apply_ip_scope_to_draft(draft, 'consumers')
            _named_platforms.promote(draft)   # the platform the ask described is the viewers universe's platform
            frac = data.get('fraction')
            try:
                frac = float(frac)
            except (TypeError, ValueError):
                frac = None
            if not frac or not (0.10 <= frac <= 0.95):
                frac = 0.55
            try:
                tu_now = int(draft.get('subject_raw_tu') or 0)
                if tu_now > 0:
                    draft['subject_raw_tu'] = max(800, int(tu_now * frac))
                av_now = int(draft.get('subject_raw_avid') or 0)
                if av_now > 0:
                    draft['subject_raw_avid'] = max(
                        400, int(av_now * frac))
                _C._jitter_draft_est_sample(draft)
            except Exception as _sc_err:
                print(f"[ip-scope] consumer sample scale skipped: "
                      f"{_sc_err}")
            # Viewer season/film scope (2026-08-27): the subject just
            # became a viewers universe - research the title's
            # structure and, when it is a multi-season series or a
            # film franchise, chain the scope question next. Runs
            # before the head line so a scope the words already named
            # (renamed subject) is what the confirmation echoes.
            try:
                _H._apply_viewer_scope_guard(draft, answer, allow_ask=True)
            except Exception as _vsg_err:
                print(f"[ip-scope] viewer-scope chain skipped: "
                      f"{_vsg_err}")
            # Kids-product definition chain (2026-08-27 Toca Boca):
            # a players-only pick on a kids' game/app resolves WHO
            # those players are - 'X Players' reads as the under-18
            # end users and silently renames to 'X - Players'; a
            # non-kids product is untouched.
            try:
                _H._apply_product_audience_guard(draft, answer,
                                              allow_ask=True)
            except Exception as _pag_err:
                print(f"[ip-scope] product-audience chain skipped: "
                      f"{_pag_err}")
            head = (f"Got it - building on {verb} only. The profile "
                    f"is now {draft.get('subject') or subject}.")
            for _note_key in ('viewer_audience_note', 'viewer_scope_note'):
                _vs_note = str(draft.get(_note_key) or '').strip()
                if _vs_note:
                    head += ' ' + _vs_note
        else:
            past = {'viewers': 'watched', 'watchers': 'watched',
                    'readers': 'read', 'listeners': 'listened to',
                    'players': 'played'}.get(verb, 'consumed')
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"Two ways to build this: broad - anyone "
                            f"who engaged with it anywhere (social, "
                            f"search, fan content, shopping) - or "
                            f"{verb} only, just the people who "
                            f"actually {past} it. "
                            f"Reply broad or {verb}."),
                'next_step': 'ip_scope',
            })
        draft.pop('ip_scope_data', None)
        # Continue the normal flow, same chaining as age_breaks. The
        # kids-title definition (WHO the universe is) asks before the
        # season/film scope (WHICH content it covers).
        if draft.get('ask_viewer_audience') and \
                draft.get('viewer_audience_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head,
                            'next_step': 'viewer_audience'})
        if draft.get('ask_viewer_scope') and \
                draft.get('viewer_scope_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head,
                            'next_step': 'viewer_scope'})
        if draft.get('ask_age_breaks') and draft.get('age_break_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'age_breaks'})
        if draft.get('ask_parent_link') and \
                draft.get('parent_link_candidates'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'parent_link'})
        if str(draft.get('decision') or '').strip().lower() in (
                'new_build', 'time_shifted_refresh', 'cut_needs_parent'):
            return jsonify({
                'success': True, 'draft': draft,
                'message': (head + " Quick scoping question: what's "
                            "the business goal for this one? "
                            "(One line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to "
                            "the build.)"),
                'next_step': 'goal',
            })
        return jsonify({'success': True, 'draft': draft,
                        'message': head + " Review the brief below "
                        "and approve to start the build.",
                        'next_step': 'approve'})

    if step == 'viewer_audience':
        # Kids-title definition (2026-08-27 Jenna, Paw Patrol
        # directive): a preschool/kids title asks whether the universe
        # is the actual under-18 viewers or the parents of the
        # viewers. Data was stashed by _apply_viewer_scope_guard.
        # kind='product' is the kids-product twin (2026-08-27 Toca
        # Boca directive, stashed by _apply_product_audience_guard):
        # the players themselves vs the parents who buy.
        from migration.viewer_content_scope import (
            audience_from_text as _va_from_text,
            product_audience_from_text as _pa_from_text,
        )
        data = draft.get('viewer_audience_data') or {}
        is_product = str(data.get('kind') or '') == 'product'
        title = str(data.get('title') or subject)
        low = answer.lower().strip()
        choice = (_pa_from_text(answer) if is_product
                  else _va_from_text(answer))
        if not choice:
            if _re.search(r'\bparents?\b|\bmoms?\b|\bdads?\b'
                          r'|\badults?\b|\bbuyers?\b', low):
                choice = 'parents'
            elif _re.search(r'\bkids?\b|\bchild(?:ren)?\b|\bactual\b'
                            r'|\bunder\b|\b17\b|\b18\b|\byoung\b'
                            r'|\bplayers?\b', low):
                choice = 'under18'
        if not choice:
            if is_product:
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': (f"{title} is made for kids - should "
                                f"the profile cover the players "
                                f"themselves (the under-18 end "
                                f"users), or the parents of the "
                                f"players (the adults who download "
                                f"and buy)? Pick below, or reply "
                                f"players or parents."),
                    'next_step': 'viewer_audience',
                })
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"{title} is made for a young audience - "
                            f"should the profile cover the actual "
                            f"under-18 viewers, or the parents of the "
                            f"viewers? Pick below, or reply parents "
                            f"or kids."),
                'next_step': 'viewer_audience',
            })
        if is_product:
            _H._bind_product_audience(draft, data, choice)
            who = ('the parents of the players'
                   if choice == 'parents'
                   else 'the under-18 players themselves')
        else:
            _H._bind_viewer_audience(draft, data, choice)
            who = ('the parents and co-viewing adults'
                   if choice == 'parents'
                   else 'the actual under-18 viewers')
        draft.pop('viewer_audience_data', None)
        head = (f"Got it - building on {who}. The profile is now "
                f"{draft.get('subject') or subject}.")
        if draft.get('ask_viewer_scope') and \
                draft.get('viewer_scope_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head,
                            'next_step': 'viewer_scope'})
        if draft.get('ask_age_breaks') and draft.get('age_break_data'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'age_breaks'})
        if draft.get('ask_parent_link') and \
                draft.get('parent_link_candidates'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'parent_link'})
        if str(draft.get('decision') or '').strip().lower() in (
                'new_build', 'time_shifted_refresh', 'cut_needs_parent'):
            return jsonify({
                'success': True, 'draft': draft,
                'message': (head + " Quick scoping question: what's "
                            "the business goal for this one? "
                            "(One line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to "
                            "the build.)"),
                'next_step': 'goal',
            })
        return jsonify({'success': True, 'draft': draft,
                        'message': head + " Review the brief below "
                        "and approve to start the build.",
                        'next_step': 'approve'})

    if step == 'viewer_scope':
        # Viewer season/film scope (2026-08-27 Jenna directive): a
        # series asks all seasons / most recent / a specific season
        # (then lists the researched seasons); a film franchise asks
        # whole franchise / most recent film / a specific film (then
        # lists the films). Data was stashed by
        # _apply_viewer_scope_guard; viewer_scope_data.mode flips
        # 'scope' -> 'pick' for the second ask.
        from migration.viewer_content_scope import (
            scope_from_text as _vs_from_text,
            extend_series_seasons as _vs_extend_seasons,
        )
        data = draft.get('viewer_scope_data') or {}
        kind = str(data.get('kind') or 'series')
        is_series = kind == 'series'
        unit = 'season' if is_series else 'film'
        seasons = data.get('seasons') or []
        films = data.get('films') or []
        low = answer.lower().strip()
        mode = str(data.get('mode') or 'scope')

        def _vs_bind_and_continue(req):
            _H._bind_viewer_scope(draft, data, req)
            draft.pop('viewer_scope_data', None)
            vs = draft.get('viewer_scope') or {}
            note = str(draft.get('viewer_scope_note') or '').strip()
            if vs.get('mode') in ('latest', 'specific'):
                head = (f"Locked to {vs.get('label') or 'that scope'} - "
                        f"the profile is now "
                        f"{draft.get('subject') or subject}.")
            else:
                head = note or "Covering the full title."
            if draft.get('ask_age_breaks') and \
                    draft.get('age_break_data'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'age_breaks'})
            if draft.get('ask_parent_link') and \
                    draft.get('parent_link_candidates'):
                return jsonify({'success': True, 'draft': draft,
                                'message': head,
                                'next_step': 'parent_link'})
            if str(draft.get('decision') or '').strip().lower() in (
                    'new_build', 'time_shifted_refresh',
                    'cut_needs_parent'):
                return jsonify({
                    'success': True, 'draft': draft,
                    'message': (head + " Quick scoping question: "
                                "what's the business goal for this "
                                "one? (One line - a pitch, a renewal, "
                                "a media plan. Say skip to jump "
                                "straight to the build.)"),
                    'next_step': 'goal',
                })
            return jsonify({'success': True, 'draft': draft,
                            'message': head + " Review the brief "
                            "below and approve to start the build.",
                            'next_step': 'approve'})

        def _vs_pick_question():
            opts = seasons if is_series else films
            lines = [f"Which {unit}?"]
            for _i, _o in enumerate(opts[:60], start=1):
                lines.append(f"  {_i}. {_o.get('label')}")
            lines.append(f"Pick below, or reply with the {unit} "
                         f"(or all).")
            return "\n".join(lines)

        # Direct picks work at either step ('season 5', a film name).
        direct = _vs_from_text(answer, data)
        if direct and direct.get('mode') == 'specific':
            if is_series and direct.get('season') is not None:
                want_n = int(direct['season'])
                have = {int(s.get('number') or 0) for s in seasons}
                researched_max = max(have) if have else 0
                if want_n not in have:
                    # A season the researched list is behind on (a
                    # just-aired / current season the cached structure
                    # missed) must not be hard-rejected - that made the
                    # current season impossible to pick when the list
                    # was stale. Accept a number just beyond the
                    # researched max: extend the structure so the label,
                    # year, and sample sizing land right, then bind it.
                    # Keep the helpful rejection only for a genuine
                    # in-run gap or an implausibly high number.
                    plausible_current = (
                        researched_max > 0
                        and want_n > researched_max
                        and want_n <= researched_max + 6
                        and want_n <= 60)
                    if plausible_current:
                        data = _vs_extend_seasons(data, want_n)
                        draft['viewer_scope_data'] = data
                        seasons = data.get('seasons') or seasons
                    else:
                        return jsonify({
                            'success': True, 'draft': draft,
                            'message': (f"Season {direct['season']} isn't "
                                        f"in the researched run.\n"
                                        + _vs_pick_question()),
                            'next_step': 'viewer_scope',
                        })
            return _vs_bind_and_continue(direct)
        if _re.search(r'\b(most\s+recent|latest|newest|current)\b', low):
            return _vs_bind_and_continue({'mode': 'latest'})
        if mode == 'pick':
            m_idx = _re.match(r'^(?:option\s*|#\s*)?(\d{1,2})\b', low)
            opts = seasons if is_series else films
            if m_idx and 1 <= int(m_idx.group(1)) <= len(opts):
                pick = opts[int(m_idx.group(1)) - 1]
                req = ({'mode': 'specific', 'season': pick.get('number')}
                       if is_series
                       else {'mode': 'specific',
                             'film': pick.get('title')})
                return _vs_bind_and_continue(req)
            if _re.search(r'\b(all|every|whole|entire|full)\b', low):
                return _vs_bind_and_continue({'mode': 'all'})
            return jsonify({
                'success': True, 'draft': draft,
                'message': _vs_pick_question(),
                'next_step': 'viewer_scope',
            })
        if _re.search(r'\b(specific|pick|choose|list|which)\b', low):
            data['mode'] = 'pick'
            draft['viewer_scope_data'] = data
            return jsonify({
                'success': True, 'draft': draft,
                'message': _vs_pick_question(),
                'next_step': 'viewer_scope',
            })
        if _re.search(r'\b(all|every|whole|entire|full|everything|'
                      r'franchise)\b', low):
            return _vs_bind_and_continue({'mode': 'all'})
        n_units = len(seasons) if is_series else len(films)
        scope_words = ('all seasons, most recent season, or a '
                       'specific season' if is_series else
                       'the whole franchise, the most recent film, '
                       'or a specific film')
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"{data.get('title') or subject} has "
                        f"{n_units} {unit}s. Cover {scope_words}?"),
            'next_step': 'viewer_scope',
        })

    if step == 'age_breaks':
        # Requested age range doesn't line up with the panel's breaks
        # (2026-08-20). Options were stashed by the interpret step.
        data = draft.get('age_break_data') or {}
        opts = [str(o) for o in (data.get('options') or []) if o]
        low = answer.lower().strip()
        chosen = None
        if _re.match(r'^(none|all|all ages|any|anyone|skip|drop|'
                     r'no age|18\s*\+|everyone|adults)\b', low):
            chosen = ''  # drop the age qualifier entirely (18+)
        elif _re.search(r'\b1[0-7]\s*(?:and|or|&)\s*(?:under|younger)\b'
                        r'|\b(?:under|below)\s+1[0-8]\b|\bkids?\b'
                        r'|\bminors?\b|\bteens?\b', low):
            chosen = '17 and under'
        else:
            m_num = _re.match(r'^(?:option\s*|#\s*)?([1-9])\b', low)
            m_rng = _re.search(
                r'(\d{1,2})\s*(?:-|–| - |to)\s*(\d{1,2})|(\d{2})\s*\+', low)
            if m_num and int(m_num.group(1)) <= len(opts):
                chosen = opts[int(m_num.group(1)) - 1]
            elif m_rng:
                if m_rng.group(3):
                    c_lo, c_hi = int(m_rng.group(3)), 999
                else:
                    c_lo, c_hi = int(m_rng.group(1)), int(m_rng.group(2))
                if c_lo == 0 and c_hi <= 17:
                    c_hi = 17  # 0-17 style answers snap to the break
                if _H._age_range_is_canonical(c_lo, c_hi):
                    chosen = _H._fmt_age_range(c_lo, c_hi)
            elif _re.search(r'\b(tight|narrow|first|smaller)\b', low) \
                    and opts:
                chosen = opts[0]
            elif _re.search(r'\b(wide|broad|second|bigger|larger)\b',
                            low) and opts:
                chosen = opts[-1]
            elif _re.match(r'^(yes|yep|yeah|ok|okay|sure|fine)\b', low) \
                    and len(opts) == 1:
                chosen = opts[0]
        if chosen is None:
            breaks = ", ".join(data.get('canonical_breaks') or
                               list(_H._AGE_BREAK_LABELS))
            opt_lines = "\n".join(f"  {i}. {o}"
                                  for i, o in enumerate(opts, start=1))
            return jsonify({
                'success': True, 'draft': draft,
                'message': (f"Our age data comes in these breaks: "
                            f"{breaks}. Closest coverage for "
                            f"{data.get('requested')}:\n{opt_lines}"
                            f"\n\nReply with a number, a break "
                            "(like 18-34), or all ages to drop "
                            "the age filter."),
                'next_step': 'age_breaks',
            })
        # Rewrite the subject with the chosen canonical range (or strip
        # the age qualifier entirely on 'all ages').
        subj_now = str(draft.get('subject') or draft.get('name') or '')
        matched = str(data.get('matched_text') or '')

        def _swap_age(s):
            if not s:
                return s
            rep = f'({chosen})' if chosen else ''
            if matched and matched in s:
                return s.replace(matched, rep)
            s2 = _re.sub(
                r'\(\s*\d{1,2}\s*(?:-|–| - )\s*\d{1,2}\s*\)|'
                r'(?:ages?|aged)\s*[:\s]?\s*\d{1,2}\s*'
                r'(?:-|–| - |to|through|thru)\s*\d{1,2}|'
                r'\(?\s*(?:under|below)\s+\d{1,2}\s*\)?|'
                r'\(?\s*1[0-7]\s*(?:and|or|&)\s*(?:under|younger)\s*\)?|'
                r'\b\d{2}\s*\+', rep, s, count=1)
            if s2 != s:
                return s2
            return f'{s} ({chosen})' if chosen else s
        new_subj = ' '.join(_swap_age(subj_now).split())
        new_subj = new_subj.strip(' -,') or subj_now
        draft['subject'] = new_subj
        draft['name'] = new_subj
        draft.pop('file_stem', None)  # re-derive from the new subject
        draft.pop('ask_age_breaks', None)
        # Under-18 audience sits in the panel's canonical 17 AND UNDER
        # break; only note anything when the pick EXCLUDES it after an
        # under-18 ask.
        under18_note = (
            " (note: this range excludes the 17 AND UNDER break)"
            if data.get('under18') and chosen
            and 'under' not in chosen.lower() else "")
        if chosen:
            head = (f"Locked ages to {chosen}{under18_note} - "
                    f"the profile is now {new_subj}.")
        else:
            head = (f"Dropped the age filter{under18_note} - "
                    f"the profile is now {new_subj}.")
        # Continue the normal flow: parent_link if queued, else the
        # fresh-build scoping question, else straight to approve.
        if draft.get('ask_parent_link') and \
                draft.get('parent_link_candidates'):
            return jsonify({'success': True, 'draft': draft,
                            'message': head, 'next_step': 'parent_link'})
        if str(draft.get('decision') or '').strip().lower() in (
                'new_build', 'time_shifted_refresh', 'cut_needs_parent'):
            return jsonify({
                'success': True, 'draft': draft,
                'message': (head + " Quick scoping question: what's "
                            "the business goal for this one? "
                            "(One line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to "
                            "the build.)"),
                'next_step': 'goal',
            })
        return jsonify({'success': True, 'draft': draft,
                        'message': head + " Review the brief below "
                        "and approve to start the build.",
                        'next_step': 'approve'})

    if step == 'parent_link':
        # "Is this a cut of X data we already have?" (2026-08-20).
        # Candidates were stashed on the draft by the interpret step.
        cands = draft.get('parent_link_candidates') or []
        # Defense-in-depth: filter any stashed candidate whose entity
        # shares no real identity token with the ask, so a stale draft
        # from a prior turn can't re-offer a mismatched parent
        # (2026-09-02, companion to the _maybe_ask_parent_link gate).
        _ask_subject_rp = draft.get('subject') or ''
        # 2026-09-04 hotfix (Jenna live 500): _subject_identity_mismatch
        # takes (prompt, subject, candidate_display) and the 2026-09-02
        # author left a bare `prompt` reference that was never bound in
        # this branch (NameError at runtime). The user's natural-language
        # ask is stashed on the draft during interpret via
        # spec_draft['user_prompt'] (see line ~54237). Fall back through
        # a couple of legacy field names, then to the subject itself as
        # a last resort so the identity-mismatch helper never sees None
        # (empty first arg still works: helper does `prompt or ''`,
        # ORs both args together, and its whole body is wrapped in
        # try/except Exception -> False, so a stale draft cannot re-500
        # here regardless).
        _clarify_prompt = str(
            body.get('prompt')
            or draft.get('user_prompt')
            or draft.get('prompt')
            or draft.get('original_prompt')
            or draft.get('question')
            or _ask_subject_rp
            or ''
        )
        cands = [c for c in cands
                 if not _H._subject_identity_mismatch(
                     _clarify_prompt, _ask_subject_rp, c.get('display_name'))]
        low = answer.lower().strip()
        declined = bool(_re.match(
            r'^(no|none|neither|nope|fresh|new|not a cut|'
            r'no[,\s]+(build|start)\s+fresh|build fresh|'
            r'start fresh|new build|standalone|its own|skip)\b', low))
        picked = None
        if not declined and cands:
            m_num = _re.match(r'^(?:option\s*|#\s*)?([1-9])\b', low)
            if m_num:
                i = int(m_num.group(1)) - 1
                if 0 <= i < len(cands):
                    picked = cands[i]
            elif _re.match(r'^(yes|yep|yeah|correct|right|it is|'
                           r'that one|exactly|link it)\b', low) \
                    and len(cands) == 1:
                picked = cands[0]
            else:
                # Name match: candidate whose display tokens best
                # overlap the answer.
                ans_tokens = set(
                    t for t in _H._normalize_for_match(answer).split()
                    if len(t) >= 3)
                best, best_ov = None, 0
                for c in cands:
                    dn_tokens = set(
                        t for t in _H._normalize_for_match(
                            c.get('display_name') or '').split()
                        if len(t) >= 3)
                    ov = len(ans_tokens & dn_tokens)
                    if ov > best_ov:
                        best, best_ov = c, ov
                if best is not None and best_ov > 0:
                    picked = best
        # Identity gate on the picked candidate (2026-09-02,
        # defense-in-depth): if the picked candidate shares no real
        # identity token with the ask, drop the link and route to the
        # build-fresh branch below instead of committing derive_cut.
        # Catches stale drafts whose candidates were stashed before
        # the identity gate landed. 2026-09-04 hotfix: reuse the
        # _clarify_prompt bound at the top of this branch (bare
        # `prompt` was a NameError; see the note above).
        if picked is not None and _H._subject_identity_mismatch(
                _clarify_prompt, draft.get('subject') or '',
                picked.get('display_name')):
            picked = None
            declined = True
        if picked is None and not declined:
            opts = "\n".join(
                f"  {i}. {c.get('display_name')}"
                for i, c in enumerate(cands, start=1))
            return jsonify({
                'success': True, 'draft': draft,
                'message': ("Which existing profile is this a cut "
                            f"of?\n{opts}\n\nReply with the number or "
                            "name - or say none to build it fresh "
                            "as its own profile."),
                'next_step': 'parent_link',
            })
        if declined:
            draft.pop('ask_parent_link', None)
            return jsonify({
                'success': True, 'draft': draft,
                'message': ("Got it - building this fresh as its own "
                            "profile. Quick scoping question: what's "
                            "the business goal for this one? "
                            "(One line - a pitch, a renewal, a media "
                            "plan. Say skip to jump straight to "
                            "the build.)"),
                'next_step': 'goal',
            })
        # Linked: reroute the whole ask as a 3-credit derived cut of
        # the chosen parent. Fresh-build scoping (goal/strategy) no
        # longer applies.
        parent_display = str(picked.get('display_name') or '').strip()
        label = str(draft.get('cut_label')
                    or draft.get('cut_label_guess') or subject).strip()
        # Recover the FULL cohort label from the subject once the
        # parent is known (2026-08-20: 'Marvel TVOD Renters -> EST
        # Buyers' must not collapse to 'EST buyers').
        label = _H._upgrade_cut_label_with_residual(
            label, draft.get('subject') or subject, parent_display)
        new_name = _H._compose_cut_name(parent_display, label)
        draft['decision'] = 'derive_cut'
        draft['existing_match_s3_key'] = picked.get('s3_key')
        draft['existing_match_display_name'] = parent_display
        draft['parent_display_name'] = parent_display
        draft['cut_label'] = label
        draft['cut_label_guess'] = label
        draft['subject'] = new_name
        draft['name'] = new_name
        if not (draft.get('derive_type') or '').strip() \
                or draft.get('derive_type') == 'other':
            draft['derive_type'] = 'intersect_cut'
        draft['run_avid'] = False
        cut_credits = int(_H._V1_CREDITS.get('derive_cut', 3))
        draft['estimated_credits'] = cut_credits
        draft['base_credits'] = cut_credits
        try:
            draft['estimated_run_minutes'] = _C._estimate_run_minutes(
                'derive_cut', False)
        except Exception as _fx:
            _faults.note('chat', _fx)
        draft.pop('addon_cuts', None)
        draft.pop('strategist_recs', None)
        draft.pop('ask_parent_link', None)
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"Linked. {new_name} will be derived as a "
                        f"cut of {parent_display} "
                        f"({cut_credits} credits) - the numbers ladder "
                        "up to that parent. Review the brief below "
                        "and approve to start the build."),
            'next_step': 'approve',
        })

    if step == 'goal':
        # Stale-client step. Suggested cuts are retired (Jenna
        # 2026-10-06), so the goal answer is kept on the draft and the
        # approval card releases with no cut picker.
        low = answer.lower()
        if answer and not _re.match(
                r'^(skip|none|no|nothing|nope|na|n/a|pass|'
                r'just build( it)?|next)\s*[.!]?$', low):
            draft['business_goal'] = answer[:500]
        else:
            draft['business_goal'] = ''
        draft.pop('strategist_recs', None)
        return _finalize_cuts_response()

    if step == 'strategy':
        cuts, notes = _H._parse_strategy_answer(answer, subject, draft)
        if cuts is None:
            return jsonify({
                'success': True, 'draft': draft,
                'message': ("Which ones would you like? Reply with "
                            "the numbers (\"1 and 3\"), \"all\", cut "
                            "names (\"female\", \"Gen Z\", \"Dallas "
                            "only\") - or say none."),
                'next_step': 'strategy',
            })
        draft['addon_cuts'] = cuts
        return _finalize_cuts_response(notes)

    if step == 'region':
        # Legacy step, reworked per Jenna 2026-08-19: a region answer
        # NEVER narrows the base build. The TU + avid always build
        # national; each named market becomes its own 3-credit cut.
        low = answer.lower()
        if not answer or _re.match(
                r'^(national|nationwide|nation wide|all|whole country|'
                r'us|usa|everywhere|no|none|skip|not regional)\s*[.!]?$',
                low):
            # Nationwide: release the card (no cut menu, Jenna 2026-10-06).
            return _finalize_cuts_response()
        resolved, unresolved = _H._resolve_markets_to_dmas(answer)
        if not resolved:
            return jsonify({
                'success': True, 'draft': draft,
                'message': ("Help me pin down "
                            f"{', '.join(unresolved) or 'that'} - "
                            "name the metro areas or states (e.g. "
                            "\"LA and San Diego\", \"Texas\"), or "
                            "say national."),
                'next_step': 'region',
            })
        dma_cuts = _H._normalize_cut_items(
            [{'type': 'dma', 'dma': d} for d in resolved])
        draft['addon_cuts'] = _H._merge_cuts(draft.get('addon_cuts'),
                                          dma_cuts)
        n_cuts = len(draft['addon_cuts'])
        draft['estimated_credits'] = base_credits \
            + _H.ADDON_CUT_CREDITS * n_cuts
        _cut_usd = _C._pm_usd_label(_C._pm_cut_usd(draft))
        parts = [
            "The total universe and avid always build national - "
            "each market you named becomes its own "
            f"{_plan.cut_word(_C._pm_seat_reads_included(), _cut_usd)} from that parent:\n"
            + "\n".join(f"  - {c['label']}{_plan.cut_tag(_C._pm_seat_reads_included(), _cut_usd)}" for c in dma_cuts)]
        if unresolved:
            parts.append(f"(For {', '.join(unresolved)}, tell me "
                         "the metro if you want them added.)")
        parts.append("Review the brief below and approve to start "
                     "the build.")
        return jsonify({'success': True, 'draft': draft,
                        'message': "\n\n".join(parts),
                        'next_step': 'approve'})

    # step == 'cuts' (legacy). Merges with any market cuts the region
    # step already added instead of overwriting them.
    # 2026-10-02 audit: "I want cuts by Quarter based on dates" used to
    # get "that piece needs a closer look and I'll come back to you"
    # and then queued with no cuts. Time-window replies either add the
    # named quarters as dated reads or ask which quarters, plainly.
    try:
        from prometheus import guards as _pg
        _win = _pg.time_window_cut_ask(answer)
    except Exception:
        _win = None
    if _win:
        _named = _win.get('quarters') or []
        if _win['kind'] == 'quarter' and _named:
            _have = {str(q.get('label')) for q in
                     (draft.get('quarter_cuts') or []) if isinstance(q, dict)}
            draft['quarter_cuts'] = list(draft.get('quarter_cuts') or []) + [
                q for q in _named if q['label'] not in _have]
            # Demo / market cuts named in the same reply still count.
            _more, _ = _H._parse_addon_cuts_answer(
                _pg._QUARTER_RX.sub(' ', _pg._QUARTER_WORD_RX.sub(' ', answer)),
                subject)
            if _more:
                draft['addon_cuts'] = _H._merge_cuts(draft.get('addon_cuts'),
                                                     _more)
            return _finalize_cuts_response(None)
        _unit = {'quarter': 'quarters', 'month': 'months', 'year': 'years',
                 'week': 'weeks'}.get(_win['kind'], 'date ranges')
        _eg = ('"2Q 2026 and 3Q 2026"' if _win['kind'] == 'quarter'
               else '"Jan 2026 and Feb 2026"' if _win['kind'] == 'month'
               else '"2025 and 2026"')
        return jsonify({
            'success': True, 'draft': draft,
            'message': (f"I can do that. Each {_unit[:-1]} ships as its "
                        f"own dated read on the same audience"
                        f"{_plan.quarter_price(_win['kind'], _C._pm_seat_reads_included(), _C._pm_usd_label(_oq.quarter_each(_H, draft)))}. Which "
                        f"{_unit} do you want? For example {_eg}. Or say "
                        "none to skip the cuts."),
            'next_step': 'cuts',
        })
    cuts, notes = _H._parse_addon_cuts_answer(answer, subject)
    if cuts is None:
        return jsonify({
            'success': True, 'draft': draft,
            'message': ("Which cuts would you like? Try e.g. "
                        "\"female and male\", \"by generation\", "
                        "\"18-34\", \"Los Angeles only\" - or say "
                        "none."),
            'next_step': 'cuts',
        })
    draft['addon_cuts'] = _H._merge_cuts(draft.get('addon_cuts'), cuts)
    return _finalize_cuts_response(notes)


__all__ = ['_PM_PRICING_COPY', '_pm_pricing_copy', '_PM_SUBJECT_FIELDS', '_pm_sanitize_draft_subjects', '_pm_subiq_platform_only_question', '_pm_note_existing_subiq_read', '_pm_pricing_question', '_PM_MANUAL_LOOK_COOLDOWN_S', '_PM_MANUAL_LOOK_STAMP_FILE', '_pm_manual_look_stamps', '_pm_manual_look_lock', '_pm_universe_phrase', 'api_synth_chat_clarify']
