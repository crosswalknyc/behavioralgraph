"""The product intake in the chat: the stalled and resumed intake, the breakout, the collect flags, the resolve and finish, the intake turn, the pending intake, the page question, the screen draft, the flow table, and the internal canary route.

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
from prometheus import faults as _faults
from prometheus import intents as _intents
from prometheus import digital_twin_lane as _twin_lane
from prometheus import bpiq_wizard as _bpiq_wiz
import time
from datetime import datetime, timedelta, date, timezone
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import intake_draft as _intake_draft
from prometheus.intake_reader import missing_labels as _ir_labels
from prometheus.aiq_assets import ASK_COPY as _PM_AIQ_ASK_COPY

# <<moved>>
# <<block: 1>>
_PM_INTAKE_STALL_PREFIX = 'Almost there - I still need'


# <<block: 2>>
# Agent turns that are part of an open intake: the ask copy, a stall,
# or a confirm card. A user correction sent after any of these still
# belongs to the same brief.
_PM_INTAKE_AGENT_PREFIXES = (
    _PM_INTAKE_STALL_PREFIX.lower(), 'happy to build', 'happy to run',
    'happy to set up', "here's the", 'here is the', 'one question before i line it up')


# <<block: 3>>
_PM_INTAKE_FAULT_REPLY = ('Got it. I have what you sent and I am lining '
                          'it up now. I will email you when it is '
                          'complete.')


# <<block: 4>>
def _pm_intake_prior_user_turns(history, ask_copy, limit=4):
    """User turns that belong to the open guided intake.

    Walks the widget history backwards. Stops at the first agent turn
    that is neither the intake ask copy nor a stall reply, so only the
    messages the user sent while answering THIS intake come back,
    oldest first. The current message is not in ``history`` yet.
    """
    out = []
    ask = ' '.join(str(ask_copy or '').split()).lower()
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict):
            continue
        role = str(turn.get('role') or '').lower()
        txt = str(turn.get('text') or '').strip()
        if role == 'user':
            if txt:
                out.append(txt)
            if len(out) >= limit:
                break
            continue
        norm = ' '.join(txt.split()).lower()
        if ask and norm[:80] == ask[:80]:
            break
        if norm.startswith(_C._PM_INTAKE_AGENT_PREFIXES):
            continue
        break
    return list(reversed(out))


# <<block: 5>>
def _pm_intake_last_agent_stalled(history):
    for turn in reversed(list(history or [])):
        if isinstance(turn, dict) and str(turn.get('role') or '').lower() != 'user':
            txt = ' '.join(str(turn.get('text') or '').split()).lower()
            return txt.startswith(_C._PM_INTAKE_STALL_PREFIX.lower())
    return False


# <<block: 6>>
def _pm_intake_breakout(text, flow=''):
    """A new ask typed while a guided intake is collecting leaves the
    intake (2026-10-07 Eliot: "is there a way to build an audience of
    people who have attended a Gunna concert?" was read as journey
    fields and answered "I still need platform"). The intake guard
    (prometheus.intake_guard, Jenna 2026-11-05) widens this to every
    question-shaped message: an interrogative opener, a question mark,
    a figure asked, an order on something else. When `flow` is named
    the reply that answers the question carries the way back to the
    intake ('Back to the journey'); the brief it had read so far stays
    stashed for the reader."""
    from prometheus.intake_reader import is_new_ask
    out = is_new_ask(text)
    if not out:
        try:
            from prometheus import intake_guard as _ig
            out = bool(_ig.is_new_ask_for_brief(text))
        except Exception:
            out = False
    if out and flow:
        try:
            from prometheus import intake_guard as _ig
            _ig.mark_resume(flow)
        except Exception:
            pass
    return out


# <<block: 7>>
def _pm_intake_resume_reply(flow, history):
    """Re-ask the open step of a brief-reading intake (a journey, a
    campaign, a twin) after a question typed mid-intake was answered on
    its own (2026-11-05): the last prompt this flow gave in the thread,
    verbatim, with its chips and the collect flag; the opener when the
    thread holds none. The brief read so far stays stashed for the
    reader and the earlier answers stay in the thread, so the next
    answer completes it."""
    kind, confirm_name, run_label, payload_key, collect_key, labels = _C._PM_INTAKE_UI[flow]
    ask_copy = _C._pm_intake_flow_table()[flow][3]
    base = {'success': True, 'action': 'answer', 'offer_deck': False, 'deck_angle': None}
    heads = (str(ask_copy or '')[:40], 'Almost there - I still need', 'One question before I line it up', 'Do you want this ',
             "Here's what I'll run", 'Here is what I will run')
    for t in reversed([x for x in (history or []) if isinstance(x, dict)]):
        if str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        if not txt or not any(h and txt.startswith(h) for h in heads):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        chips = []
        for o in (meta.get('options') or t.get('options') or []):
            lbl = (o.get('send') or o.get('label')) if isinstance(o, dict) else o
            lbl = str(lbl or '').strip()
            if lbl and not lbl.lower().startswith('back to'):
                chips.append(lbl)
        return {**base, 'reply': txt, collect_key: True, 'followups': chips or ['Cancel']}
    return {**base, 'reply': ask_copy, collect_key: True, 'followups': ['Cancel']}


# <<block: 8>>
def _pm_collect_flag_applies(body, key, history):
    """The widget's collect flag (`jiq_inputs`, `fw_inputs`, ...) is page
    state and follows the reader across threads (Jenna 2026-11-05). It
    arms this turn only when the thread itself could be waiting: no
    agent turn yet, or a last agent turn that is not a job notice."""
    if not (body or {}).get(key):
        return False
    try:
        from prometheus import intake_guard as _ig
        return bool(_ig.armed_flag_applies(history))
    except Exception:
        return True


# <<block: 9>>
def _pm_intake_resolve(flow, text, history, parse_fn, complete_fn,
                       required, ask_copy, user=None, usage_extras=None,
                       fallback_fn=None, propose_fn=None, alert=True):
    """Parse a guided-intake message the way a person would read it.

    Returns ``(parsed, fault)``. Three layers, cheapest first:

    1. Parse the message on its own.
    2. If that is incomplete and the user already sent earlier turns
       in this intake, parse all of them together. Users split the
       brief across messages; a parser that only sees the latest one
       asks for things it was already told.
    3. If a substantive message (10+ words) still yields none of the
       required fields, or the previous reply was already a stall and
       the user answered with substance again, that is a fault in our
       reading, not a gap in their message. ``fault`` is True: the
       caller must NOT send the stall copy a second time. It sends the
       'lining it up, will email you' reply and this helper emails ops
       (jenna@ + jessie@) the raw messages so a person runs it.

    Born from the 2026-10-01 Babylon 5 intake: a complete brief got
    'Almost there - I still need the category, the platform, the
    conversion event' twice in a row because the parser read an empty
    object and nothing noticed the reply was repeating.
    """
    text = str(text or '').strip()
    try:
        parsed = parse_fn(text, usage_extras=usage_extras) or {}
    except Exception:
        traceback.print_exc()
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if complete_fn(parsed):
        return parsed, False
    prior = _C._pm_intake_prior_user_turns(history, ask_copy)
    if not prior:
        from prometheus import intake_predicates as _ip   # local: the resolver is also exec'd standalone in tests
        if _ip.points_back_at_thread(text):   # "look at the colin and samir campaign": the brief sits one message up (Debi 2026-10-21)
            prior = _ip.recent_substantive_user_turns(history)
    _pd = _intake_draft.prior_draft(history)   # an edit merges with the drafted brief (2026-10-09)
    if _pd:
        prior = [_pd] + prior
    if prior:
        combined = '\n\n'.join(prior + [text])
        try:
            again = parse_fn(combined, usage_extras=usage_extras) or {}
        except Exception:
            traceback.print_exc()
            again = {}
        if isinstance(again, dict):
            if complete_fn(again):
                return again, False
            if (sum(1 for k in required if again.get(k))
                    > sum(1 for k in required if parsed.get(k))):
                parsed = again
    combined_text = '\n\n'.join(prior + [text]) if prior else text
    # Deterministic second reader (2026-10-02): plain rules over the
    # same words (quoted or named title, platform names, end-step
    # verbs) fill ONLY what the model left empty. It would have read
    # Carolyn's brief on the first message even with the parser bug.
    # Runs on a substantive message, or whenever the model read nothing
    # ("Build a journey for Suits on Peacock" is seven words, 2026-10-13).
    substantive = len(combined_text.split()) >= 10 or not any(parsed.get(k) for k in required)
    if fallback_fn and substantive:
        try:
            from prometheus.intake_reader import merge_missing
            kw = fallback_fn(combined_text) or {}
            if isinstance(kw, dict):
                before = sum(1 for k in required if parsed.get(k))
                merged = merge_missing(parsed, kw, required)
                after = sum(1 for k in required if merged.get(k))
                if after > before:
                    merged['_read_by'] = 'model+rules'
                    parsed = merged
                if complete_fn(parsed):
                    try:
                        _C._pm_ask_hint(outcome='intake_rules_completed',
                                     subject=flow)
                    except Exception as _fx:
                        _faults.note('chat', _fx)
                    return parsed, False
        except Exception:
            traceback.print_exc()
    # Confirm instead of interrogate (2026-10-02): two of three in
    # hand and the third obvious -> propose it on the confirm card
    # instead of asking the user to type it again.
    if propose_fn:
        try:
            parsed['_ask'] = combined_text   # the proposer reads the ask itself (a prospective partnership gets its window); popped there
            prop = propose_fn(parsed)
            parsed.pop('_ask', None)
            # (field, value) or (field, value, display); the display
            # form lets a falsy value ("daily tracking off") ride.
            if prop and len(prop) in (2, 3) and (
                    prop[1] or (len(prop) == 3 and prop[2])):
                from prometheus.intake_reader import apply_proposal
                _keys = apply_proposal(parsed, prop)   # one field, or a window's two (2026-10-16)
                if complete_fn(parsed):
                    try:
                        _C._pm_ask_hint(outcome='intake_proposed_field',
                                     subject=flow)
                    except Exception as _fx:
                        _faults.note('chat', _fx)
                    return parsed, False
                for _k in _keys + ['_proposed']:
                    parsed.pop(_k, None)
        except Exception:
            traceback.print_exc()
    found = sum(1 for k in required if parsed.get(k))
    fault = bool(substantive
                 and (found == 0 or _C._pm_intake_last_agent_stalled(history)))
    if fault and alert:
        try:
            _C._pm_ask_hint(outcome='intake_fault', subject=flow)
            from flask import g as _gf
            _gf._pm_fault_alerted = True
        except Exception as _fx:
            _faults.note('chat', _fx)
        try:
            _who = ''
            if isinstance(user, dict):
                _who = (user.get('email') or user.get('username') or '')
            _H._chatbot_error_email(
                route=f'prometheus/intake/{flow}',
                err=RuntimeError(
                    f'{flow} intake could not read a complete brief; '
                    f'user told it will be emailed'),
                user_email=_who or None,
                payload={'flow': flow, 'message': text,
                         'earlier_messages': prior,
                         'parsed': parsed,
                         'required': list(required)},
                tb='(guided intake fault - run this by hand and email '
                   'the user from Prometheus)')
        except Exception:
            traceback.print_exc()
    return parsed, fault


# <<block: 10>>
def _pm_intake_finish_confirm(parsed, reply, run_label):
    """Append the proposed-field note (if any) to the confirm reply
    and strip private resolver keys from the payload the widget echoes
    back on approve."""
    prop = parsed.pop('_proposed', None) if isinstance(parsed, dict) else None
    if isinstance(parsed, dict):
        parsed.pop('_read_by', None)
        parsed.pop('missing', None)
        parsed.pop('_precedent_note', None)
        parsed.pop('_precedent', None)
    if prop and prop.get('field'):
        try:
            from prometheus.intake_reader import proposed_note
            reply = reply + proposed_note(prop['field'], prop['value'],
                                          run_label=run_label, source=prop.get('from') or '')
        except Exception:
            traceback.print_exc()
    return reply


# <<block: 11>>
_PM_INTAKE_UI = {   # flow -> (screen-draft kind, confirm reply fn, run chip, payload key, collect key, plain labels)
    'digital_journey': ('journey', '_pm_jiq_confirm_reply', 'Run the journey', 'jiq_confirm_payload', 'jiq_collect',
                        ['the category', 'the platform', 'the conversion event']),
    'brand_partnership': ('bpiq', '_pm_bpiq_confirm_reply', 'Run the valuation', 'bpiq_confirm_payload', 'bpiq_collect',
                          ['the brand', 'the partner', 'the campaign window']),
    'flywheel': ('', '_pm_fw_confirm_reply', 'Run the flywheel', 'fw_confirm_payload', 'fw_collect',
                 ['the title', 'the captured action', 'the ecosystem', 'the conversion']),
    'attribution': ('', '_pm_aiq_confirm_reply', 'Start tracking', 'aiq_confirm_payload', 'aiq_collect',
                    ['the campaign name', 'tagged URLs', 'the conversion']),
    'digital_twin': _twin_lane.ui_entry(),   # Digital Twins IQ (2026-10-26): the row lives in prometheus/digital_twin_lane.py
}


# <<block: 12>>
_pm_twin_confirm_reply = lambda parsed: _twin_lane.confirm_reply(parsed)   # noqa: E731  (the intake looks confirm functions up by name)


# <<block: 13>>
_pm_twin_intent = _intents.twin_intent   # host binding for understand.tool_intent (2026-10-26)


# <<block: 14>>
def _pm_intake_turn(flow, text, body, history, user, ppu, first):
    """One guided-intake turn for any of the four flows (2026-10-13). The first message is READ before anything
    is asked: "Build a digital journey for Anthony Bourdain No Reservations on Amazon Prime just like the one you
    did for Babylon 5" (Carolyn, WBD) names the title, the platform and the shape, and drew the three-field opener.
    Order: the open screen can draft the whole brief; the ask itself is parsed (model, then rules); a prior
    deliverable it points at lends its shape; a complete brief confirms; a partial one asks only for what is
    missing; only an ask that names nothing gets the opener."""
    _C._pm_ask_hint(route=flow)   # an intake turn is the flow's in the ask log, never the page analysis it runs beside (2026-11-23)
    if flow == 'brand_partnership':
        uname = (str((user or {}).get('username') or (user or {}).get('email') or '').strip()
                 if isinstance(user, dict) else str(user or '').strip())
        hit = _bpiq_wiz.analyze_hook(
            text, body, history, user, ppu, uname, _C.globals(), first=first,
            store=_bpiq_wiz.store_for(body, _C.globals()))
        if hit is not None:
            return hit
        reply, chips = _bpiq_wiz.step_prompt(_bpiq_wiz.empty_draft())
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': chips, 'offer_deck': False, 'deck_angle': None,
                'bpiq_collect': True, 'bpiq_wizard': _bpiq_wiz.empty_draft()}
    if flow == 'flywheel':
        # The Flywheel wizard (Jenna 2026-11-03, after Grace Yin's Gilmore Girls on Prime Video attempt): the
        # entry point, the ecosystem, the date range, one at a time, then the card with the price.
        from prometheus import flywheel_wizard as _fw_wiz
        uname = (str((user or {}).get('username') or (user or {}).get('email') or '').strip()
                 if isinstance(user, dict) else str(user or '').strip())
        def _fw_existing(inputs):
            # the studies already on the Flywheel IQ page for this entry and ecosystem (Jenna 2026-11-03: tell the
            # user one exists for that date range and offer it before building again)
            try:
                from migration.flywheel_synthesis import existing_studies as _fx
                return _fx(str((inputs or {}).get('subject') or ''), str((inputs or {}).get('ecosystem') or ''), _H.s3_client)
            except Exception as _ex:
                _faults.note('chat', _ex)
                return []
        hit = _fw_wiz.analyze_hook(text, body, history, user, ppu, uname, _C.globals(), first=first,
                                   store=_fw_wiz.store_for(body, _C.globals()),
                                   price_label=_C._pm_tool_price_label('flywheel_iq', '$500'),
                                   existing_lookup=_fw_existing)
        if hit is not None:
            if hit.get('fw_confirm_payload'):
                _C._pm_pending_intake('stash', user, 'flywheel', hit['fw_confirm_payload'], _fw_wiz.RUN_CHIP)   # "run the flywheel" typed later still finds it
            return hit
        try:
            from prometheus import intake_guard as _ig
            if _ig.pending_resume():
                return None   # the wizard let a question through (2026-11-05): the router answers it, the reply carries the way back
        except Exception:
            pass
        return {'success': True, 'action': 'answer', 'reply': _fw_wiz.ASK_ENTRY,
                'followups': ['Cancel'], 'offer_deck': False, 'deck_angle': None, 'fw_collect': True}
    kind, confirm_name, run_label, payload_key, collect_key, labels = _C._PM_INTAKE_UI[flow]
    parse_fn, complete_fn, required, ask_copy, fallback_fn, propose_fn = _C._pm_intake_flow_table()[flow]
    base = {'success': True, 'action': 'answer', 'offer_deck': False, 'deck_angle': None}
    from prometheus import intake_reader as _ir
    if flow == 'attribution':   # the sheet on the thread carries the URLs; a named live campaign is an add (Alexia 2026-10-16)
        from prometheus import aiq_assets as _aa
        _add = _aa.intake_turn(text, body, user, history, host=_H, price_label=_C._pm_tool_price_label('attribution_iq_daily', '$100'), stash=_C._pm_pending_intake)
        if _add:
            return {**base, **_add}
        parse_fn = _aa.with_sheet_urls(parse_fn, body, user, text, host=_H)
    _pre = None
    _low = ' '.join(str(text or '').lower().split()).strip(' .!')
    if not first and _low == 'name another subject':
        return {**base, 'reply': ask_copy, collect_key: True, 'followups': ['Cancel']}
    _page = str((((_C._pm_validate_page_context((body or {}).get('page_context')) or (None,))[0] or {}).get('primary') or {}).get('name') or '').strip()
    if kind and _page:
        # Never bound to the open profile unless told (Jenna 2026-10-16: "never ever just start there"). "Use <page>" after the
        # question, or an ask that points at the screen ("this profile"), drafts from it; an ask that names no subject gets the question.
        if _low in (f"use {_page.lower()}", 'use the open profile', 'use this profile', 'yes, use it') or (first and _C._PM_PAGE_DEIXIS_RE.search(_low)):
            drafted = _C._pm_intake_screen_draft(kind, body)
            if drafted:
                return drafted
        if first:
            _pre = _ir.first_read(text, parse_fn, fallback_fn, usage_extras=ppu)   # the model, then the rules when it read nothing
            if not _ir.names_subject(_pre):
                _C._pm_ask_hint(outcome='intake_page_question', subject=_page)
                return {**base, 'reply': _C._pm_intake_page_question(kind, _page), collect_key: True,
                        'followups': [f'Use {_page}', 'Name another subject', 'Cancel']}
    from prometheus import precedent as _prec
    template = _prec.reference(text) or _ir.reading_precedent(body)   # "like the one you did for X" (Jenna 2026-10-13); the Reader's precedent flag too (2026-10-14)
    ask_text = _ir.strip_template_reference(text) if template else text
    _reuse = (lambda t, usage_extras=None, _p=_pre: _p if (t == text and isinstance(_p, dict) and _p) else parse_fn(t, usage_extras=usage_extras))
    parsed, fault = _C._pm_intake_resolve(flow, ask_text, history, _reuse, complete_fn, required, ask_copy,
                                       user=user, usage_extras=ppu, fallback_fn=fallback_fn,
                                       propose_fn=None if template else propose_fn, alert=not first)
    found = None
    if template:   # the prior deliverable lends its shape before any default is proposed
        try:
            found = _prec.resolve(template, (user or {}).get('username') or '', host=_H)
        except Exception:
            found = None
        if flow == 'digital_journey' and not found:
            parsed = _ir.apply_journey_template(parsed, _C._pm_prior_journey_shape(template, user), template)
        else:
            parsed = _prec.apply(flow, parsed, found, template, host=_H)
        if isinstance(parsed, dict) and found:
            parsed['_precedent_note'] = _prec.describe(found)
        if not complete_fn(parsed) and propose_fn:
            prop = propose_fn(parsed)
            if prop:
                _ir.apply_proposal(parsed, prop, keep_existing=True)
    have = any(parsed.get(k) for k in required) if isinstance(parsed, dict) else False
    if first and not have:   # an ask that names nothing gets the opener (a reader fault on a first turn is not the reader's)
        return {**base, 'reply': ask_copy, collect_key: True, 'followups': ['Cancel']}
    if fault and not complete_fn(parsed):
        return _C._pm_intake_fault_payload()
    if flow == 'digital_journey' and isinstance(parsed, dict):
        parsed = _ir.apply_any_of_platforms(parsed, ask_text)   # several services as alternatives: one cross-service end step (Carolyn 2026-11-02)
        parsed = _ir.apply_direction(parsed)
        if _ir.direction_unclear(parsed) and parsed.get('subject') and _ir.direction_asked_already(history):
            parsed = _ir.default_direction(parsed)   # asked once already: the default, never the same question twice (2026-10-26)
        if _ir.direction_unclear(parsed) and parsed.get('subject'):
            # which way does it run (Jenna 2026-10-19): how they came TO WATCH, or what watchers do after and in addition
            _C._pm_ask_hint(outcome='clarified', subject=parsed.get('subject'))
            _C._pm_pending_intake('stash', user, flow, parsed, run_label)
            return {**base, 'reply': _ir.direction_question(parsed.get('subject')), collect_key: True,
                    'followups': list(_ir.DIRECTION_CHIPS) + ['Cancel']}
        parsed = _ir.watch_defaults(parsed)   # a title on a video platform starts on social (Jenna 2026-10-13)
        parsed = _ir.watch_definition_default(parsed, (user or {}).get('username') if isinstance(user, dict) else user)   # click chain or saw-then-watched (2026-10-22)
    if flow == 'attribution' and isinstance(parsed, dict) and parsed.get('daily_refresh') and _C._pm_aiq_days(parsed) < 1 \
            and parsed.get('campaign_name') and parsed.get('urls') and parsed.get('conversion_event'):
        # daily tracking with no stop date: propose 30 days and confirm rather than ask (the intake contract; golden g058, 2026-10-14)
        parsed['end_tracking_date'] = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        parsed.setdefault('_proposed', {'field': 'end_tracking_date', 'value': parsed['end_tracking_date']})
    _df = __import__('prometheus.data_floor', fromlist=['gate']).intake(parsed) or {}   # no data before the data floor (Jenna 2026-11-18): a window before it is not confirmed; one across it starts on it and the card says so first
    if _df.get('refuse'):
        return {**base, 'reply': _df['refuse'], collect_key: True, 'followups': ['Cancel'], 'data_floor': 'before'}
    if complete_fn(parsed):
        _pnote = parsed.pop('_precedent_note', '') if isinstance(parsed, dict) else ''
        reply = (_df['note'] + '\n\n' if _df.get('note') else '') + _C._pm_intake_finish_confirm(parsed, _C.globals()[confirm_name](parsed), run_label)
        if _pnote:
            reply = reply.rstrip() + '\n\n' + _pnote
        _C._pm_pending_intake('stash', user, flow, parsed, run_label)   # a later "Run the journey" still finds it
        return {**base, 'reply': reply, payload_key: parsed, 'followups': [run_label, 'Cancel']}
    missing = _ir_labels(parsed, labels) or list(labels)
    if flow == 'attribution' and parsed.get('daily_refresh') and _C._pm_aiq_days(parsed) < 1:
        missing = ['a stop date after today for the daily tracking']
    return {**base, 'reply': ('Almost there - I still need ' + ', '.join(str(m) for m in missing)
                              + '. Send the missing piece(s) and I will line it up.'),
            collect_key: True, 'followups': ['Cancel']}


# <<block: 15>>
def _pm_pending_intake(op, user, flow, *args):
    """stash / pick the brief a guided intake proposed, per reader, for a day (prometheus.pending_intake)."""
    try:
        from prometheus import pending_intake as _pi
        if _faults.is_probe():
            return None   # a probe leaves no brief behind for the next probe to pick up (2026-11-02)
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip() if isinstance(user, dict) else str(user or '')
        if op == 'stash':
            return _pi.stash(_H.s3_client, _H.S3_BUCKET, uname, flow, args[0], args[1] if len(args) > 1 else '')
        return _pi.pick(_H.s3_client, _H.S3_BUCKET, uname, flow, args[0] if args else '')
    except Exception:
        return None


# <<block: 16>>
def _pm_prior_journey_shape(title, user=None):
    """The shape of a prior Digital Journey on `title` (start step, end step, kind) from the corpus, or None."""
    try:
        from migration import corpus_catalog as _cc
        from prometheus import intake_reader as _ir
        prior = _cc.prior_journey(_cc.anchors_for(title, with_ledger=False))
        return _ir.journey_shape(prior, title) if prior else None
    except Exception:
        traceback.print_exc()
        return None


# <<block: 17>>
_PM_PAGE_DEIXIS_RE = re.compile(r"\b(?:this|the open|the current|my open) (?:profile|audience|page|screen)\b|\bprofile on (?:my|the) screen\b|\bwhat(?:'s| is) (?:open|on (?:my|the) screen)\b")


# <<block: 18>>
def _pm_intake_page_question(kind, page):
    """The question, never the draft, when an ask names no subject and a profile is open (Jenna 2026-10-16)."""
    what = 'journey' if kind == 'journey' else 'valuation'
    return (f"Do you want this {what} on {page}, the profile open on your screen, or on another subject? "
            f"Say 'Use {page}' and I will draft it from that profile, or name the subject and I will start from there.")


# <<block: 19>>
def _pm_intake_fault_payload():
    return {'success': True, 'action': 'answer',
            'reply': _C._PM_INTAKE_FAULT_REPLY,
            'followups': [], 'offer_deck': False, 'deck_angle': None}


# <<block: 20>>
def _pm_intake_screen_draft(kind, body):
    """The full brief drafted from the profile open on screen (item 4,
    2026-10-09): one confirm card instead of a field-by-field ask.
    None when nothing is open or the profile cannot carry the brief."""
    try:
        ctx, _err = _C._pm_validate_page_context((body or {}).get('page_context'))
        if not ctx:
            return None
        page = str((ctx.get('primary') or {}).get('name') or '').strip()
        journey = kind == 'journey'
        parsed = (_intake_draft.journey_draft if journey else _intake_draft.bpiq_draft)(ctx, _H.s3_client, _H.S3_BUCKET)
        if not parsed or not (_C._pm_jiq_inputs_complete if journey else _C._pm_bpiq_inputs_complete)(parsed):
            return None
        parsed.pop('_drafted_from', None)
        reply = _intake_draft.draft_lead(kind, page) + '\n\n' + (_C._pm_jiq_confirm_reply if journey else _C._pm_bpiq_confirm_reply)(parsed)
        payload = ({'jiq_confirm_payload': parsed, 'followups': ['Run the journey', 'Cancel']} if journey
                   else {'bpiq_confirm_payload': parsed, 'followups': ['Run it', 'Cancel']})
        _C._pm_ask_hint(outcome='intake_drafted_from_screen', subject=page)
        return {'success': True, 'action': 'answer', 'reply': reply, 'offer_deck': False, 'deck_angle': None, **payload}
    except Exception:
        traceback.print_exc()
        return None


# <<block: 21>>
def _pm_intake_flow_table():
    """flow -> (parse_fn, complete_fn, required, ask_copy, fallback_fn,
    propose_fn). One place the analyze branches and the canary share."""
    from prometheus.intake_reader import (keyword_parse_journey,
                                          propose_journey_field,
                                          keyword_parse_flywheel,
                                          propose_flywheel_field,
                                          keyword_parse_brand_partnership,
                                          propose_brand_partnership_field,
                                          keyword_parse_attribution,
                                          propose_attribution_field)
    return {
        'digital_journey': (
            _C._pm_jiq_parse, _C._pm_jiq_inputs_complete,
            ('subject', 'platform', 'conversion_event'),
            _C._PM_JIQ_ASK_COPY, keyword_parse_journey,
            propose_journey_field),
        'flywheel': (
            _C._pm_fw_parse, _C._pm_fw_inputs_complete,
            ('subject', 'captured_action', 'ecosystem',
             'conversion_event'),
            _C._PM_FW_ASK_COPY, keyword_parse_flywheel,
            propose_flywheel_field),
        'brand_partnership': (
            _C._pm_bpiq_parse, _C._pm_bpiq_inputs_complete,
            ('brand_partner', 'qualifier', 'event_start', 'event_end'),
            _C._PM_BPIQ_ASK_COPY, keyword_parse_brand_partnership,
            lambda parsed: propose_brand_partnership_field(parsed, researcher=_C._pm_bpiq_flight_research)),
        'attribution': (
            _C._pm_aiq_parse, _C._pm_aiq_inputs_complete,
            ('campaign_name', 'urls', 'conversion_event'),
            _PM_AIQ_ASK_COPY, keyword_parse_attribution,
            propose_attribution_field),
        'digital_twin': _twin_lane.flow_table_entry(),
    }


# <<block: 22>>
@_H.app.route('/api/internal/intake-canary', methods=['POST'])
def api_internal_intake_canary():
    """Run one known-complete brief through a guided intake on THIS
    deployment, exactly as the analyze route would, with no user, no
    thread write, no charge, no ops alert (2026-10-02 Jenna: the test
    suite catches code regressions; this catches model or transport
    changes). Shared-secret auth: ``X-Synth-Auth`` must equal the
    build server's SYNTH_QUEUE_SECRET, so only the nightly canary on
    the build server can call it. Never a dashboard surface.

    Reports two verdicts per brief: ``model_complete`` (the model read
    alone, the signal the canary alerts on) and ``complete`` (after
    the rules reader and any proposal, what the user would see).
    """
    import hmac as _hmac
    secret = str(getattr(_H, 'SYNTH_QUEUE_SECRET', '') or '')
    given = str(request.headers.get('X-Synth-Auth') or '')
    if not secret or not _hmac.compare_digest(secret, given):
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    flow = str(body.get('flow') or '').strip()
    text = str(body.get('text') or '').strip()
    history = body.get('history') or []
    table = _C._pm_intake_flow_table()
    if flow not in table or not text:
        return jsonify({'error': 'flow and text required',
                        'flows': sorted(table)}), 400
    parse_fn, complete_fn, required, ask_copy, fb, pr = table[flow]
    t0 = time.time()
    try:
        model_parsed = parse_fn(text, usage_extras=None) or {}
    except Exception as e:
        traceback.print_exc()
        model_parsed = {'_error': f'{type(e).__name__}: {e}'[:300]}
    model_complete = bool(isinstance(model_parsed, dict)
                          and complete_fn(model_parsed))
    parsed, fault = _C._pm_intake_resolve(
        flow, text, history, parse_fn, complete_fn, required, ask_copy,
        user=None, usage_extras=None, fallback_fn=fb, propose_fn=pr,
        alert=False)
    return jsonify({
        'success': True, 'flow': flow,
        'model_complete': model_complete,
        'model_found': [k for k in required if model_parsed.get(k)]
        if isinstance(model_parsed, dict) else [],
        'model_error': (model_parsed.get('_error')
                        if isinstance(model_parsed, dict) else None),
        'complete': bool(complete_fn(parsed)),
        'fault': bool(fault),
        'read_by': parsed.get('_read_by') or 'model',
        'proposed': parsed.get('_proposed'),
        'found': [k for k in required if parsed.get(k)],
        'missing': [k for k in required if not parsed.get(k)],
        'elapsed_s': round(time.time() - t0, 2),
    })


__all__ = ['_PM_INTAKE_STALL_PREFIX', '_PM_INTAKE_AGENT_PREFIXES', '_PM_INTAKE_FAULT_REPLY', '_pm_intake_prior_user_turns', '_pm_intake_last_agent_stalled', '_pm_intake_breakout', '_pm_intake_resume_reply', '_pm_collect_flag_applies', '_pm_intake_resolve', '_pm_intake_finish_confirm', '_PM_INTAKE_UI', '_pm_twin_confirm_reply', '_pm_twin_intent', '_pm_intake_turn', '_pm_pending_intake', '_pm_prior_journey_shape', '_PM_PAGE_DEIXIS_RE', '_pm_intake_page_question', '_pm_intake_fault_payload', '_pm_intake_screen_draft', '_pm_intake_flow_table', 'api_internal_intake_canary']
