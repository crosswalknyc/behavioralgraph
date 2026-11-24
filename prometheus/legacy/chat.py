"""Legacy Prometheus chat code, moved out of app.py (2026-10-01).

Every function and constant here used to live inline in app.py. The
move is mechanical: the code is byte-for-byte the same except that a
reference to an app.py global that did not move is written ``_H.<name>``
and resolves at call time through the host proxy in
``prometheus.legacy``. app.py binds the host, imports this module and
re-exports every name in ``_EXPORTS`` into its own namespace, so the
routes register exactly as before and every existing reference keeps
working. A second host (the standalone Prometheus service) binds the
same proxy to its own namespace.

Do not add new code here. New Prometheus work goes in the typed package
modules (service, understand, envelope, blueprint). Code leaves this
file when it is rewritten against the host registry.
"""
from prometheus import file_names as _file_names, no_build as _no_build, study_record as _study_record   # a download's name is one a link can carry; do-not-build is honored; a published study answers from its record (2026-11-16)
import os
import sys
import uuid
import json
import csv
import threading
import traceback
import re
from prometheus import smarter as _smart   # 2026-10-13 audit helpers
from prometheus import figures as _fig   # figures by composition (audit items 3 + 4, 2026-10-14)
from prometheus import faults as _faults   # swallowed faults are counted (audit item 8)
from prometheus import store as _store   # relational shadow store (audit item 7)
from prometheus import intents as _intents   # product-intent gates (extracted 2026-10-14)
from prometheus import subject_rules as _subject_rules   # the open profile is context, never a default subject (2026-10-20)
from prometheus import page_default as _page_default   # the page is the base only on a positive signal; the quoted subject is the one that runs (2026-10-24)
from prometheus import creator_fit as _creator_fit   # a creator-fit read is held to the subject's universe (2026-10-20)
from prometheus import intake_predicates as _intake_predicates   # pure text predicates (extracted 2026-10-20)
from prometheus import subject_display as _subject_display   # a reader sees a name, never a key (2026-10-22)
from prometheus import question_key as _question_key   # one fold for one question (2026-10-26)
from prometheus import read_registry as _read_registry   # the caller's recent reads by question (2026-10-26)
from prometheus import audience_referent as _audience_referent   # a bare "this audience" binds and is confirmed before work starts (2026-10-26)
from prometheus import subiq_library as _subiq_library   # a question across the Subscriber IQ library (2026-10-26)
from prometheus import deck_sources as _deck_sources   # a deck of something already delivered or attached (2026-10-26)
from prometheus import digital_twin_lane as _twin_lane   # Digital Twins IQ: intake, labs, Focus Group IQ, catalog, status, directions (2026-10-26)
from prometheus import bpiq_wizard as _bpiq_wiz   # Brand Partnership Valuation wizard (2026-10-28)
from prometheus import deck_pick as _deck_pick, deck_subject as _deck_subject   # the deck is on the subject the reader picked or named; a deck from a read keeps its figures (2026-11-11)
from prometheus import matrix_read as _matrix   # big matrix asks run in pieces and land whole (2026-11-04)
from prometheus import question_universe as _question_universe   # the base is the universe the question names; the page only when pointed at (2026-11-06)
from prometheus import ledger_gate as _ledger_gate, probe_files as _probe_files, cut_list as _cut_list, primary_profile as _primary_profile   # a probe's ask never banks in the shared ledger, on any route, and leaves no file where a reader's files live (2026-11-11)
from prometheus import window_scope as _wscope   # the window is the one the reader asked for; the default is never a frozen pair (2026-11-10)
from prometheus import named_platforms as _named_platforms   # a platform the client names is the universe's platform, strict (2026-11-10)
from prometheus import door_words as _door_words   # the word door never reaches a chat read or a file name (2026-11-10)
from prometheus import promise_truth as _PT, read_repair as _read_repair, grid_facts as _grid_facts   # a progress line never outlives its job; a promise has a record (2026-11-11) and ends delivered; a read with no figures is repaired, never thrown (2026-11-18)
from prometheus import api_threads as _api_threads   # a key-surface job lands in a key thread, never a dashboard chat (2026-11-10)
from prometheus import order_truth as _order_truth   # what is said about an order follows the artifact (2026-11-10)
from prometheus import question_first as _question_first   # a question where profiles are built is offered first and answered when a build lands (2026-11-16)
from prometheus import build_ready as _build_ready, list_shape as _list_shape   # the server posts the finished line into the launching thread (2026-11-10); a list of subjects is a list of names (2026-11-11)
import io
import hashlib
import time
from datetime import datetime, timedelta, date, timezone
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
import pandas as pd
import boto3

from prometheus.legacy import H as _H  # noqa: E402
from prometheus import intake_draft as _intake_draft  # noqa: E402  (drafts briefs from the open profile)
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared  # noqa: E402
from prometheus import plan_allowance as _plan  # noqa: E402  (a seat on a plan reads no price, 2026-11-17)
from prometheus import order_quote as _oq  # noqa: E402  (a quote is the approval's charge, 2026-11-17)


def _pm_correct_page(title, body_html):
    return (
        "<html><head><title>" + title + "</title>"
        "<meta name='viewport' content='width=device-width,"
        "initial-scale=1'></head>"
        "<body style='margin:0;background:#E9E8E1;font-family:"
        "Helvetica,Arial,sans-serif;color:#5C6560'>"
        "<div style='max-width:640px;margin:0 auto;padding:40px 24px'>"
        "<div style='font-size:11px;letter-spacing:.12em;"
        "text-transform:uppercase;color:#5E7E12;font-weight:600'>"
        "Prometheus</div>"
        f"<h1 style='font-size:22px;color:#0C1618;font-weight:700;"
        f"margin:10px 0 18px'>{title}</h1>"
        f"{body_html}"
        "</div></body></html>")


def _pm_correct_esc(text):
    return (str(text or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


# <<extracted: prometheus.thread_history block 1>>


# <<extracted: prometheus.library_match block 1>>


# <<extracted: prometheus.library_match block 2>>
try:   # the marker claude_client splits a system prompt on: cached rulebook before it, per-ask tail after (2026-10-13)
    from claude_client import PROMPT_CACHE_SPLIT as _PM_PROMPT_CACHE_SPLIT
except Exception:   # no client on this host: one block, no split
    _PM_PROMPT_CACHE_SPLIT = "\n"


# <<extracted: prometheus.library_match block 3>>


from prometheus.subject_rules import identity_swapped as _identity_swapped   # 2026-10-30: Ohio is not Florida


# <<extracted: prometheus.library_match block 4>>


# <<extracted: prometheus.library_match block 5>>


# <<extracted: prometheus.brief_draft block 1>>


# <<extracted: prometheus.brief_prompts block 1>>


# <<extracted: prometheus.brief_draft block 2>>


# <<extracted: prometheus.thread_history block 2>>


# <<extracted: prometheus.thread_history block 3>>


# <<extracted: prometheus.thread_history block 4>>


# <<extracted: prometheus.thread_history block 5>>


# <<extracted: prometheus.thread_history block 6>>


# <<extracted: prometheus.thread_history block 7>>


# <<extracted: prometheus.thread_history block 8>>


# <<extracted: prometheus.thread_history block 9>>


# <<extracted: prometheus.thread_history block 10>>


# <<extracted: prometheus.thread_history block 11>>


# <<extracted: prometheus.thread_history block 12>>


# <<extracted: prometheus.thread_history block 13>>


# <<extracted: prometheus.thread_history block 14>>


# <<extracted: prometheus.brief_draft block 3>>


# <<extracted: prometheus.brief_draft block 4>>


# <<extracted: prometheus.brief_draft block 5>>


# <<extracted: prometheus.brief_draft block 6>>


# <<extracted: prometheus.brief_draft block 7>>


# <<extracted: prometheus.incidence_check block 1>>


# <<extracted: prometheus.incidence_check block 2>>


# <<extracted: prometheus.incidence_check block 3>>


# <<extracted: prometheus.incidence_check block 4>>


# <<extracted: prometheus.incidence_check block 5>>


# <<extracted: prometheus.incidence_check block 6>>


# <<extracted: prometheus.incidence_check block 7>>


# <<extracted: prometheus.incidence_check block 8>>


# Flat rate card for pricing questions (2026-09-23 Jenna, verbatim
# copy): 'if someone asks a credit question they should get this'.
# Served deterministically on BOTH chat surfaces before any routing.
# One source (2026-10-09, item 7): prometheus/rate_card.py renders
# Jenna's words with the LIVE per-pull prices admins set in the billing
# panel (the same prices the charge uses), in the seat's currency.
from prometheus import rate_card as _rate_card  # noqa: E402
# <<extracted: prometheus.clarify_route block 1>>


# <<extracted: prometheus.clarify_route block 2>>


# <<extracted: prometheus.clarify_route block 3>>


# <<extracted: prometheus.clarify_route block 4>>


# <<extracted: prometheus.clarify_route block 5>>


# <<extracted: prometheus.clarify_route block 6>>


# <<extracted: prometheus.clarify_route block 7>>


# <<extracted: prometheus.clarify_route block 8>>


# <<extracted: prometheus.clarify_route block 9>>


# <<extracted: prometheus.clarify_route block 10>>


# <<extracted: prometheus.clarify_route block 11>>


# <<extracted: prometheus.clarify_route block 12>>


# <<extracted: prometheus.clarify_route block 13>>


# The ask-log route / outcome / subject inference lives in
# prometheus.ask_outcome (moved 2026-10-20 to keep this module under
# its line ratchet); the names stay importable from here.
from prometheus.ask_outcome import (  # noqa: E402,F401
    _ask_infer_route_outcome, _ASK_SUBJECT_VIEWS, _ASK_SUBJ_STOP, _ask_mentions_subject)


# <<extracted: prometheus.answer_gate block 1>>


# <<extracted: prometheus.answer_gate block 2>>


# <<extracted: prometheus.answer_gate block 3>>


# <<extracted: prometheus.answer_gate block 4>>


# <<extracted: prometheus.answer_gate block 5>>
# <<extracted: prometheus.answer_gate block 6>>
# <<extracted: prometheus.answer_gate block 7>>
# <<extracted: prometheus.answer_gate block 8>>
# <<extracted: prometheus.answer_gate block 9>>


# <<extracted: prometheus.answer_gate block 10>>
# <<extracted: prometheus.answer_gate block 11>>


# <<extracted: prometheus.answer_gate block 12>>


# <<extracted: prometheus.answer_gate block 13>>


# <<extracted: prometheus.answer_gate block 14>>


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
                __import__('prometheus.service', fromlist=['stash_binding']).stash_binding(_pm_ask_log_user(''), body.get('thread_id'))   # the thread's project rides the request (2026-10-21)
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
                            user=_pm_ask_log_user(),
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
                        _pm_watch_notify(
                            _pm_ask_log_user(''), question,
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
                outcome, extra = _pm_ask_apply_taxonomy(
                    log_surface, payload, status_code, ask_history,
                    outcome, question,
                    _pm_ask_log_user(''))
                # Answer gate (2026-10-02 RCA): the only exit. A held
                # reply is retried once, then replaced with the honest
                # email promise; a build card for a task becomes the
                # which-audience question.
                if not _v1_api:
                    resp, payload, status_code, outcome, extra = \
                        _pm_answer_gate(
                            fn, args, kwargs, resp, payload, status_code,
                            log_surface, outcome, extra, question,
                            ask_history,
                            _pm_ask_log_user(''),
                            t0)
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
                    user=_pm_ask_log_user(),
                    view=view, question=question, surface=log_surface,
                    route=route, outcome=outcome,
                    ms=int((time.time() - t0) * 1000),
                    mode=mode, subject=subject, extra=extra,
                    stages=getattr(_g, '_pm_ask_stages', None),
                    reply=__import__('prometheus.ask_outcome', fromlist=['reply_text']).reply_text(payload) or _env_text,
                    job_id=__import__('prometheus.ask_outcome', fromlist=['promised_job']).promised_job(payload))
                _pm_watch_flag(_pm_ask_log_user(), question, route, outcome, extra)
                # A key ask is notified once, at the API door, with the
                # text the caller actually received (the v1 doc can turn an
                # empty reply into a decline after this point; 2026-10-21
                # the copy read 'No written answer' while the partner read
                # 'That one did not land'). Dashboard asks notify here.
                if not getattr(_g, '_pm_api_key_owner', None):
                    _pm_watch_notify(
                        _pm_ask_log_user(''), question, payload,
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
                            (_pm_ask_log_user('')).strip(),
                            question, subject=subject, view=view,
                            route=route)
                except Exception as _fx:
                    _faults.note('chat', _fx)
            except Exception:
                traceback.print_exc()
            return resp
        return wrapper
    return deco


# <<extracted: prometheus.interpret_turn block 1>>


# <<extracted: prometheus.interpret_turn block 2>>


# <<extracted: prometheus.interpret_turn block 3>>


# <<extracted: prometheus.spec_from_draft block 1>>


# <<extracted: prometheus.approve_route block 1>>


# <<extracted: prometheus.approve_route block 2>>


# <<extracted: prometheus.approve_route block 3>>


# <<extracted: prometheus.approve_route block 4>>


# <<extracted: prometheus.approve_route block 5>>


# <<extracted: prometheus.approve_route block 6>>


# <<extracted: prometheus.approve_route block 7>>


# <<extracted: prometheus.approve_route block 8>>


# <<extracted: prometheus.approve_route block 9>>


# <<extracted: prometheus.thread_history block 15>>


# <<extracted: prometheus.thread_history block 16>>


# <<extracted: prometheus.model_chain block 1>>


# <<extracted: prometheus.model_chain block 2>>


# <<extracted: prometheus.model_chain block 3>>


# <<extracted: prometheus.model_chain block 4>>


# <<extracted: prometheus.model_chain block 5>>


# <<extracted: prometheus.model_chain block 6>>


# <<extracted: prometheus.model_chain block 7>>
# <<extracted: prometheus.model_chain block 8>>


# <<extracted: prometheus.model_chain block 9>>


_PM_DECK_PREFIX = 'system/prometheus_decks/'


# <<extracted: prometheus.model_chain block 10>>


# <<extracted: prometheus.model_chain block 11>>


# <<extracted: prometheus.model_chain block 12>>


# <<extracted: prometheus.model_chain block 13>>


_PM_INTAKE_STALL_PREFIX = 'Almost there - I still need'

# Agent turns that are part of an open intake: the ask copy, a stall,
# or a confirm card. A user correction sent after any of these still
# belongs to the same brief.
_PM_INTAKE_AGENT_PREFIXES = (
    _PM_INTAKE_STALL_PREFIX.lower(), 'happy to build', 'happy to run',
    'happy to set up', "here's the", 'here is the', 'one question before i line it up')

_PM_INTAKE_FAULT_REPLY = ('Got it. I have what you sent and I am lining '
                          'it up now. I will email you when it is '
                          'complete.')


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
        if norm.startswith(_PM_INTAKE_AGENT_PREFIXES):
            continue
        break
    return list(reversed(out))


def _pm_intake_last_agent_stalled(history):
    for turn in reversed(list(history or [])):
        if isinstance(turn, dict) and str(turn.get('role') or '').lower() != 'user':
            txt = ' '.join(str(turn.get('text') or '').split()).lower()
            return txt.startswith(_PM_INTAKE_STALL_PREFIX.lower())
    return False


from prometheus.intake_reader import missing_labels as _ir_labels  # noqa: E402  (plain words for missing intake fields)


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


def _pm_intake_resume_reply(flow, history):
    """Re-ask the open step of a brief-reading intake (a journey, a
    campaign, a twin) after a question typed mid-intake was answered on
    its own (2026-11-05): the last prompt this flow gave in the thread,
    verbatim, with its chips and the collect flag; the opener when the
    thread holds none. The brief read so far stays stashed for the
    reader and the earlier answers stay in the thread, so the next
    answer completes it."""
    kind, confirm_name, run_label, payload_key, collect_key, labels = _PM_INTAKE_UI[flow]
    ask_copy = _pm_intake_flow_table()[flow][3]
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
    prior = _pm_intake_prior_user_turns(history, ask_copy)
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
                        _pm_ask_hint(outcome='intake_rules_completed',
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
                        _pm_ask_hint(outcome='intake_proposed_field',
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
                 and (found == 0 or _pm_intake_last_agent_stalled(history)))
    if fault and alert:
        try:
            _pm_ask_hint(outcome='intake_fault', subject=flow)
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
_pm_twin_confirm_reply = lambda parsed: _twin_lane.confirm_reply(parsed)   # noqa: E731  (the intake looks confirm functions up by name)
_pm_twin_intent = _intents.twin_intent   # host binding for understand.tool_intent (2026-10-26)


def _pm_intake_turn(flow, text, body, history, user, ppu, first):
    """One guided-intake turn for any of the four flows (2026-10-13). The first message is READ before anything
    is asked: "Build a digital journey for Anthony Bourdain No Reservations on Amazon Prime just like the one you
    did for Babylon 5" (Carolyn, WBD) names the title, the platform and the shape, and drew the three-field opener.
    Order: the open screen can draft the whole brief; the ask itself is parsed (model, then rules); a prior
    deliverable it points at lends its shape; a complete brief confirms; a partial one asks only for what is
    missing; only an ask that names nothing gets the opener."""
    _pm_ask_hint(route=flow)   # an intake turn is the flow's in the ask log, never the page analysis it runs beside (2026-11-23)
    if flow == 'brand_partnership':
        uname = (str((user or {}).get('username') or (user or {}).get('email') or '').strip()
                 if isinstance(user, dict) else str(user or '').strip())
        hit = _bpiq_wiz.analyze_hook(
            text, body, history, user, ppu, uname, globals(), first=first,
            store=_bpiq_wiz.store_for(body, globals()))
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
        hit = _fw_wiz.analyze_hook(text, body, history, user, ppu, uname, globals(), first=first,
                                   store=_fw_wiz.store_for(body, globals()),
                                   price_label=_pm_tool_price_label('flywheel_iq', '$500'),
                                   existing_lookup=_fw_existing)
        if hit is not None:
            if hit.get('fw_confirm_payload'):
                _pm_pending_intake('stash', user, 'flywheel', hit['fw_confirm_payload'], _fw_wiz.RUN_CHIP)   # "run the flywheel" typed later still finds it
            return hit
        try:
            from prometheus import intake_guard as _ig
            if _ig.pending_resume():
                return None   # the wizard let a question through (2026-11-05): the router answers it, the reply carries the way back
        except Exception:
            pass
        return {'success': True, 'action': 'answer', 'reply': _fw_wiz.ASK_ENTRY,
                'followups': ['Cancel'], 'offer_deck': False, 'deck_angle': None, 'fw_collect': True}
    kind, confirm_name, run_label, payload_key, collect_key, labels = _PM_INTAKE_UI[flow]
    parse_fn, complete_fn, required, ask_copy, fallback_fn, propose_fn = _pm_intake_flow_table()[flow]
    base = {'success': True, 'action': 'answer', 'offer_deck': False, 'deck_angle': None}
    from prometheus import intake_reader as _ir
    if flow == 'attribution':   # the sheet on the thread carries the URLs; a named live campaign is an add (Alexia 2026-10-16)
        from prometheus import aiq_assets as _aa
        _add = _aa.intake_turn(text, body, user, history, host=_H, price_label=_pm_tool_price_label('attribution_iq_daily', '$100'), stash=_pm_pending_intake)
        if _add:
            return {**base, **_add}
        parse_fn = _aa.with_sheet_urls(parse_fn, body, user, text, host=_H)
    _pre = None
    _low = ' '.join(str(text or '').lower().split()).strip(' .!')
    if not first and _low == 'name another subject':
        return {**base, 'reply': ask_copy, collect_key: True, 'followups': ['Cancel']}
    _page = str((((_pm_validate_page_context((body or {}).get('page_context')) or (None,))[0] or {}).get('primary') or {}).get('name') or '').strip()
    if kind and _page:
        # Never bound to the open profile unless told (Jenna 2026-10-16: "never ever just start there"). "Use <page>" after the
        # question, or an ask that points at the screen ("this profile"), drafts from it; an ask that names no subject gets the question.
        if _low in (f"use {_page.lower()}", 'use the open profile', 'use this profile', 'yes, use it') or (first and _PM_PAGE_DEIXIS_RE.search(_low)):
            drafted = _pm_intake_screen_draft(kind, body)
            if drafted:
                return drafted
        if first:
            _pre = _ir.first_read(text, parse_fn, fallback_fn, usage_extras=ppu)   # the model, then the rules when it read nothing
            if not _ir.names_subject(_pre):
                _pm_ask_hint(outcome='intake_page_question', subject=_page)
                return {**base, 'reply': _pm_intake_page_question(kind, _page), collect_key: True,
                        'followups': [f'Use {_page}', 'Name another subject', 'Cancel']}
    from prometheus import precedent as _prec
    template = _prec.reference(text) or _ir.reading_precedent(body)   # "like the one you did for X" (Jenna 2026-10-13); the Reader's precedent flag too (2026-10-14)
    ask_text = _ir.strip_template_reference(text) if template else text
    _reuse = (lambda t, usage_extras=None, _p=_pre: _p if (t == text and isinstance(_p, dict) and _p) else parse_fn(t, usage_extras=usage_extras))
    parsed, fault = _pm_intake_resolve(flow, ask_text, history, _reuse, complete_fn, required, ask_copy,
                                       user=user, usage_extras=ppu, fallback_fn=fallback_fn,
                                       propose_fn=None if template else propose_fn, alert=not first)
    found = None
    if template:   # the prior deliverable lends its shape before any default is proposed
        try:
            found = _prec.resolve(template, (user or {}).get('username') or '', host=_H)
        except Exception:
            found = None
        if flow == 'digital_journey' and not found:
            parsed = _ir.apply_journey_template(parsed, _pm_prior_journey_shape(template, user), template)
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
        return _pm_intake_fault_payload()
    if flow == 'digital_journey' and isinstance(parsed, dict):
        parsed = _ir.apply_any_of_platforms(parsed, ask_text)   # several services as alternatives: one cross-service end step (Carolyn 2026-11-02)
        parsed = _ir.apply_direction(parsed)
        if _ir.direction_unclear(parsed) and parsed.get('subject') and _ir.direction_asked_already(history):
            parsed = _ir.default_direction(parsed)   # asked once already: the default, never the same question twice (2026-10-26)
        if _ir.direction_unclear(parsed) and parsed.get('subject'):
            # which way does it run (Jenna 2026-10-19): how they came TO WATCH, or what watchers do after and in addition
            _pm_ask_hint(outcome='clarified', subject=parsed.get('subject'))
            _pm_pending_intake('stash', user, flow, parsed, run_label)
            return {**base, 'reply': _ir.direction_question(parsed.get('subject')), collect_key: True,
                    'followups': list(_ir.DIRECTION_CHIPS) + ['Cancel']}
        parsed = _ir.watch_defaults(parsed)   # a title on a video platform starts on social (Jenna 2026-10-13)
        parsed = _ir.watch_definition_default(parsed, (user or {}).get('username') if isinstance(user, dict) else user)   # click chain or saw-then-watched (2026-10-22)
    if flow == 'attribution' and isinstance(parsed, dict) and parsed.get('daily_refresh') and _pm_aiq_days(parsed) < 1 \
            and parsed.get('campaign_name') and parsed.get('urls') and parsed.get('conversion_event'):
        # daily tracking with no stop date: propose 30 days and confirm rather than ask (the intake contract; golden g058, 2026-10-14)
        parsed['end_tracking_date'] = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        parsed.setdefault('_proposed', {'field': 'end_tracking_date', 'value': parsed['end_tracking_date']})
    _df = __import__('prometheus.data_floor', fromlist=['gate']).intake(parsed) or {}   # no data before the data floor (Jenna 2026-11-18): a window before it is not confirmed; one across it starts on it and the card says so first
    if _df.get('refuse'):
        return {**base, 'reply': _df['refuse'], collect_key: True, 'followups': ['Cancel'], 'data_floor': 'before'}
    if complete_fn(parsed):
        _pnote = parsed.pop('_precedent_note', '') if isinstance(parsed, dict) else ''
        reply = (_df['note'] + '\n\n' if _df.get('note') else '') + _pm_intake_finish_confirm(parsed, globals()[confirm_name](parsed), run_label)
        if _pnote:
            reply = reply.rstrip() + '\n\n' + _pnote
        _pm_pending_intake('stash', user, flow, parsed, run_label)   # a later "Run the journey" still finds it
        return {**base, 'reply': reply, payload_key: parsed, 'followups': [run_label, 'Cancel']}
    missing = _ir_labels(parsed, labels) or list(labels)
    if flow == 'attribution' and parsed.get('daily_refresh') and _pm_aiq_days(parsed) < 1:
        missing = ['a stop date after today for the daily tracking']
    return {**base, 'reply': ('Almost there - I still need ' + ', '.join(str(m) for m in missing)
                              + '. Send the missing piece(s) and I will line it up.'),
            collect_key: True, 'followups': ['Cancel']}


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



_PM_PAGE_DEIXIS_RE = re.compile(r"\b(?:this|the open|the current|my open) (?:profile|audience|page|screen)\b|\bprofile on (?:my|the) screen\b|\bwhat(?:'s| is) (?:open|on (?:my|the) screen)\b")


def _pm_intake_page_question(kind, page):
    """The question, never the draft, when an ask names no subject and a profile is open (Jenna 2026-10-16)."""
    what = 'journey' if kind == 'journey' else 'valuation'
    return (f"Do you want this {what} on {page}, the profile open on your screen, or on another subject? "
            f"Say 'Use {page}' and I will draft it from that profile, or name the subject and I will start from there.")


def _pm_intake_fault_payload():
    return {'success': True, 'action': 'answer',
            'reply': _PM_INTAKE_FAULT_REPLY,
            'followups': [], 'offer_deck': False, 'deck_angle': None}


def _pm_intake_screen_draft(kind, body):
    """The full brief drafted from the profile open on screen (item 4,
    2026-10-09): one confirm card instead of a field-by-field ask.
    None when nothing is open or the profile cannot carry the brief."""
    try:
        ctx, _err = _pm_validate_page_context((body or {}).get('page_context'))
        if not ctx:
            return None
        page = str((ctx.get('primary') or {}).get('name') or '').strip()
        journey = kind == 'journey'
        parsed = (_intake_draft.journey_draft if journey else _intake_draft.bpiq_draft)(ctx, _H.s3_client, _H.S3_BUCKET)
        if not parsed or not (_pm_jiq_inputs_complete if journey else _pm_bpiq_inputs_complete)(parsed):
            return None
        parsed.pop('_drafted_from', None)
        reply = _intake_draft.draft_lead(kind, page) + '\n\n' + (_pm_jiq_confirm_reply if journey else _pm_bpiq_confirm_reply)(parsed)
        payload = ({'jiq_confirm_payload': parsed, 'followups': ['Run the journey', 'Cancel']} if journey
                   else {'bpiq_confirm_payload': parsed, 'followups': ['Run it', 'Cancel']})
        _pm_ask_hint(outcome='intake_drafted_from_screen', subject=page)
        return {'success': True, 'action': 'answer', 'reply': reply, 'offer_deck': False, 'deck_angle': None, **payload}
    except Exception:
        traceback.print_exc()
        return None


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
            _pm_jiq_parse, _pm_jiq_inputs_complete,
            ('subject', 'platform', 'conversion_event'),
            _PM_JIQ_ASK_COPY, keyword_parse_journey,
            propose_journey_field),
        'flywheel': (
            _pm_fw_parse, _pm_fw_inputs_complete,
            ('subject', 'captured_action', 'ecosystem',
             'conversion_event'),
            _PM_FW_ASK_COPY, keyword_parse_flywheel,
            propose_flywheel_field),
        'brand_partnership': (
            _pm_bpiq_parse, _pm_bpiq_inputs_complete,
            ('brand_partner', 'qualifier', 'event_start', 'event_end'),
            _PM_BPIQ_ASK_COPY, keyword_parse_brand_partnership,
            lambda parsed: propose_brand_partnership_field(parsed, researcher=_pm_bpiq_flight_research)),
        'attribution': (
            _pm_aiq_parse, _pm_aiq_inputs_complete,
            ('campaign_name', 'urls', 'conversion_event'),
            _PM_AIQ_ASK_COPY, keyword_parse_attribution,
            propose_attribution_field),
        'digital_twin': _twin_lane.flow_table_entry(),
    }


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
    table = _pm_intake_flow_table()
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
    parsed, fault = _pm_intake_resolve(
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


_PM_INTENT_HYDRATE_CACHE = {}


def _pm_intent_compact_numbers(doc):
    """Compact, reader-safe summary of a campaign's latest numbers:
    journey steps with per-step fall-off, ticket/checkout surfaces,
    first/last touch and assists, time to convert, and per-audience
    checkout fall-off. Bounded by construction (a few KB). Returns {}
    on any shape surprise."""
    try:
        if not isinstance(doc, dict) or not doc:
            return {}
        ov = (doc or {}).get('overall') or {}
        pt = ov.get('paths') or {}
        out = {'campaign': str((doc or {}).get('display_name')
                               or '')[:80],
               'as_of': str((doc or {}).get('as_of') or '')[:10]}
        cr = ov.get('conversion_rate')
        if isinstance(cr, (int, float)):
            out['conversion_rate_pct'] = round(cr * 100, 1)
        steps = []
        for n in (pt.get('nest') or [])[:6]:
            if not isinstance(n, dict) or n.get('stage') == '0_tam':
                continue
            acc = n.get('us_accounts')
            if isinstance(acc, (int, float)):
                steps.append({'step': str(n.get('label') or '')[:90],
                              'accounts': int(acc)})
        if steps:
            out['journey_steps'] = steps
            drops = []
            for i in range(1, len(steps)):
                a, b = steps[i - 1], steps[i]
                lost = a['accounts'] - b['accounts']
                if a['accounts'] > 0:
                    drops.append({
                        'from': a['step'], 'to': b['step'],
                        'lost_accounts': lost,
                        'lost_pct': round(100.0 * lost
                                          / a['accounts'], 1)})
            if drops:
                out['step_falloff'] = drops
        wh = pt.get('where') or {}
        if isinstance(wh.get('ticketer_partition'), list):
            out['ticket_checkout_surfaces'] = [
                {'surface': s.get('surface'),
                 'accounts': s.get('us_accounts'), 'pct': s.get('pct')}
                for s in wh['ticketer_partition'][:8]
                if isinstance(s, dict)]
        at = pt.get('attribution') or {}
        for fld in ('first_touch', 'last_touch', 'assists'):
            if isinstance(at.get(fld), list):
                out[fld] = [
                    {'touchpoint': x.get('touchpoint'),
                     'accounts': x.get('us_accounts'),
                     'pct': x.get('pct')}
                    for x in at[fld][:6] if isinstance(x, dict)]
        if isinstance(pt.get('time_to_conversion'), list):
            out['time_to_convert'] = [
                {'bucket': b.get('bucket'),
                 'accounts': b.get('us_accounts'), 'pct': b.get('pct')}
                for b in pt['time_to_conversion'][:6]
                if isinstance(b, dict)]
        for f in (pt.get('forks') or [])[:4]:
            if isinstance(f, dict) and f.get('of_stage') == '3_ticketer':
                out['compared_multiple_ticket_surfaces'] = {
                    'yes': f.get('yes'), 'no': f.get('no')}
        auds = (doc or {}).get('audiences')
        if isinstance(auds, dict):
            arows = []
            for k, a in list(auds.items())[:12]:
                if not isinstance(a, dict):
                    continue
                an = {n.get('stage'): n for n in
                      ((a.get('paths') or {}).get('nest') or [])
                      if isinstance(n, dict)}
                t = (an.get('3_ticketer') or {}).get('us_accounts')
                c = (an.get('4_paid') or {}).get('us_accounts')
                if isinstance(t, (int, float)) and t                         and isinstance(c, (int, float)):
                    arows.append({
                        'audience': str(k).replace('_', ' ').title(),
                        'ticket_page_accounts': int(t),
                        'checkout_accounts': int(c),
                        'checkout_falloff_pct':
                            round(100.0 * (t - c) / t, 1)})
            if arows:
                arows.sort(
                    key=lambda r: -r['checkout_falloff_pct'])
                out['audience_checkout_falloff'] = arows
        return out if out.get('journey_steps') else {}
    except Exception:
        traceback.print_exc()
        return {}


def _pm_intent_view_hydrate(view_ctx):
    """Attach the open campaign's own numbers to the intentIQ view
    context (2026-09-30 Jenna: "where is the fall-off from the ticket
    checkout page?" on the Attribution view must answer from the
    campaign on screen). The widget summary carries the campaign name
    and audience list but none of the journey numbers; this loads the
    campaign's latest daily numbers server-side, compacts them, and
    rides them under data.campaign_numbers so a grounded ask answers
    with real figures. Fail-soft: any miss returns the context
    unchanged. Cached per (campaign, day)."""
    data = (view_ctx or {}).get('data') or {}
    slug = re.sub(r'[^a-z0-9_\-]', '',
                  str(data.get('title') or '').lower())
    if not slug:
        return view_ctx
    from datetime import date as _pm_ivh_date
    ck = (slug, _pm_ivh_date.today().isoformat())
    hit = _PM_INTENT_HYDRATE_CACHE.get(ck)
    if hit is None:
        doc = None
        try:
            pfx = f'intent/{slug}/mta/coefficients_'
            resp = _H.s3_client.list_objects_v2(
                Bucket=_H.S3_BUCKET, Prefix=pfx)
            keys = sorted(o['Key']
                          for o in resp.get('Contents', []))
            if keys:
                doc = json.loads(_H.s3_client.get_object(
                    Bucket=_H.S3_BUCKET,
                    Key=keys[-1])['Body'].read())
        except Exception:
            doc = None
        hit = _pm_intent_compact_numbers(doc) if doc else {}
        if doc:
            # The campaign's own words and tables (phases, asset types,
            # channels, creative formats) ride with the numbers, so an
            # ask typed from the screen is the screen's ask and the read
            # answers from the campaign table, never a same-title profile
            # (2026-10-22, Alexia / The Influencer Project).
            try:
                from prometheus import view_base as _vb
                _assets = _formats = _phases = None
                try:
                    _adoc = json.loads(_H.s3_client.get_object(
                        Bucket=_H.S3_BUCKET, Key=f'intent/{slug}/source/normalized_assets.json')['Body'].read()) or {}
                    _assets, _phases = _adoc.get('assets'), _adoc.get('phases')   # the phases date the campaign window (2026-10-26)
                    _formats = json.loads(_H.s3_client.get_object(
                        Bucket=_H.S3_BUCKET, Key=f'intent/{slug}/mta/creative_formats.json')['Body'].read())
                except Exception:
                    pass
                hit = dict(hit or {}, **_vb.intent_vocabulary(doc, _assets, _formats, phases_in=_phases))
            except Exception:
                traceback.print_exc()
        if len(_PM_INTENT_HYDRATE_CACHE) > 16:
            _PM_INTENT_HYDRATE_CACHE.clear()
        _PM_INTENT_HYDRATE_CACHE[ck] = hit
    if hit:
        data = dict(data)
        data['campaign_numbers'] = hit
        view_ctx = dict(view_ctx)
        view_ctx['data'] = data
    return view_ctx


# One list (prometheus/view_base.py). intentIQ is the Attribution IQ view id
# the widget sends; it was missing here, so a profile left in the picker rode
# along as `primary` and became the base of a campaign ask (2026-10-22).
_PM_SELF_CONTAINED_VIEWS = __import__('prometheus.view_base', fromlist=['SELF_CONTAINED']).SELF_CONTAINED


def _pm_primary_key_for_name(name):
    """The base profile key for a display name the client sent without
    its key: an exact display-name match in the catalog (the Total
    Universe when the family has one), never Gen Pop, never a guess.
    '' when the library does not hold that exact name."""
    try:
        n = str(name or '').strip()
        if not n:
            return ''
        _norm = _H._normalize_for_match
        want = _norm(n)
        if not want:
            return ''
        best, best_score = '', None
        for e in _profile_catalog_for_chat():
            key = str(e.get('s3_key') or '').strip()
            if not key:
                continue
            disp = str(e.get('display_name') or '').strip()
            fam = disp.split(' - ')[0].strip()
            if _norm(disp) != want and not (_norm(fam) == want and ' - ' not in disp):
                continue
            try:
                from prometheus import named_subject as _ns_chk
                if _ns_chk.is_gen_pop(fam, _norm):
                    continue
            except Exception:
                pass
            score = (1 if ' - ' not in disp else 0, str(e.get('last_modified') or e.get('created_at') or ''))
            if best_score is None or score > best_score:
                best, best_score = key, score
        return best
    except Exception:
        traceback.print_exc()
        return ''


def _pm_validate_page_context(page_context):
    """Access-gate every s3 key in the page context. Returns
    (clean_ctx_or_None, err_response_or_None). A missing/keyless
    context is not an error; it means nothing is open.

    2026-08-26 (Jenna): the context may also carry `view_context`, a
    compact frontend summary of whatever dashboard view is on screen
    (Subscriber IQ, Trends, Microdramas IQ, ...). It is validated,
    field-whitelisted, and byte-capped server-side in
    prometheus_analysis.validate_view_context, and it can stand alone:
    a user on a data-bearing view with no profile open still gets an
    analysis grounded in the on-screen data."""
    if not isinstance(page_context, dict):
        return None, None
    view_ctx = None
    try:
        import prometheus_analysis as _pma_vc
        view_ctx = _pma_vc.validate_view_context(
            page_context.get('view_context'))
    except Exception:
        traceback.print_exc()
        view_ctx = None
    if view_ctx and str(view_ctx.get('view_id') or '') == 'intentIQ':
        try:
            view_ctx = _pm_intent_view_hydrate(view_ctx)
        except Exception:
            traceback.print_exc()
    primary = page_context.get('primary') or {}
    p_key = str(primary.get('s3_key') or '').strip()
    if not p_key and isinstance(primary, dict) and str(primary.get('name') or '').strip():
        # A primary named without its key (a thin client, the golden set:
        # {"primary": {"name": "Gunna"}}) is the base profile of that
        # name when the library holds exactly that subject (golden g065,
        # 2026-11-02: "what is the median age here" quoted a custom read
        # instead of reading the open profile).
        p_key = _pm_primary_key_for_name(str(primary.get('name')).strip())
        if p_key:
            primary = dict(primary, s3_key=p_key)
    # Self-contained views (2026-10-05, Alexia): a Digital Journey IQ
    # or Brand Partnership IQ screen carries its own subject. The
    # profile left selected in the picker (Netflix, in her case) is
    # stale context there, not the question; binding it made the chat
    # ask "Do you mean on Netflix?" about a ticketing journey.
    if view_ctx and str(view_ctx.get('view_id') or '') in _PM_SELF_CONTAINED_VIEWS:
        p_key = ''
    if not p_key:
        if view_ctx:
            return {'primary': None, 'cuts': [], 'extras': [],
                    'view_context': view_ctx}, None
        return None, None
    ok, err = _H._require_profile_run_access(p_key)
    if not ok and _faults.is_probe():
        ok, err = True, None   # the golden set and the canary run with no session; they read the library as the staff seat they stand in for
    if not ok:
        return None, err
    clean_cuts = []
    for c in (page_context.get('cuts') or [])[:3]:
        ck = str((c or {}).get('s3_key') or '').strip()
        if not ck or ck == p_key:
            continue
        c_ok, _c_err = _H._require_profile_run_access(ck)
        if not c_ok:
            continue
        clean_cuts.append({'s3_key': ck,
                           'name': str((c or {}).get('name') or '')[:200]})
    # Extras (2026-08-21): independent profiles pulled in for
    # cross-profile convergence / whitespace analysis (other open tabs
    # or picker selections). Same access gate as everything else.
    clean_extras = []
    seen_extra = set()
    for c in (page_context.get('extras') or [])[:3]:
        ck = str((c or {}).get('s3_key') or '').strip()
        if not ck or ck == p_key or ck in seen_extra:
            continue
        c_ok, _c_err = _H._require_profile_run_access(ck)
        if not c_ok:
            continue
        seen_extra.add(ck)
        clean_extras.append({'s3_key': ck,
                             'name': str((c or {}).get('name') or '')[:200]})
    return {'primary': {'s3_key': p_key,
                        'name': str(primary.get('name') or '')[:200]},
            'cuts': clean_cuts,
            'extras': clean_extras,
            'view_context': view_ctx}, None


def _pm_meter_answer(surface, ppu_extras=None):
    """Metered-usage record for an answer served from what's already
    there (insights-ledger replay, cache-served read) with no fresh
    model call.

    2026-09-14 (Jenna, verbatim: "nothing should EVER be free. if it
    doesnt have a set price it but is answerable from what's already
    there that should all be the metered usage."). Replaces the retired
    _pm_charge_async no-op (2026-09-09, which retired the per-ask
    credit debit in favor of session metering - do NOT restore a
    credit debit here). The taxonomy: set-price products charge their
    set price; every other answered ask is metered. Model-backed asks
    meter naturally through record_call; replayed answers record no
    tokens, so without this call they billed nothing. Rate:
    pricing.json:metered_answer_usd (super-admin editable in
    /admin/billing next to the markup), billed through the same
    pay-per-use session sweep as model calls. Subscribed full-tier
    users: the record carries attribution only (no pay_per_use flag),
    covered by their tier like their other asks. Never raises."""
    try:
        import render_usage_log as _rul
        extras = _pm_merge_extras(_pm_attrib_extras(), ppu_extras)
        _rul.record_metered_answer(surface, extras=extras)
    except Exception:
        traceback.print_exc()


def _pm_ask_hint(route=None, outcome=None, subject=None, mode=None):
    """Set ask-log inference hints on flask.g. Safe outside a request
    context (tests, threads): failures are swallowed."""
    try:
        from flask import g as _g
        if route:
            _g._pm_ask_route = route
        if outcome:
            _g._pm_ask_outcome = outcome
        if subject:
            _g._pm_ask_subject = str(subject)[:120]
        if mode:
            _g._pm_ask_mode = mode
    except Exception as _fx:
        _faults.note('chat', _fx)


def _pm_ask_stage(key, t0=None, ms=None, count=None):
    """Accumulate one per-stage duration (or count) on flask.g for the
    ask-log record (2026-08-28 latency instrumentation). Pass `t0` (a
    time.monotonic() start) for the common case, or an explicit `ms` /
    `count`. Safe outside a request context (tests, background
    threads): failures are swallowed, overhead is one dict write."""
    __import__('prometheus.progress', fromlist=['stage']).stage(key)   # the live line the widget shows (2026-10-14)
    try:
        from flask import g as _g
        stages = getattr(_g, '_pm_ask_stages', None)
        if stages is None:
            stages = {}
            _g._pm_ask_stages = stages
        if count is not None:
            stages[str(key)[:40]] = int(count)
        elif ms is not None:
            stages[str(key)[:40]] = int(ms)
        elif t0 is not None:
            stages[str(key)[:40]] = int((time.monotonic() - t0) * 1000)
    except Exception as _fx:
        _faults.note('chat', _fx)


def _pm_gate_analyze(user):
    """True when the user may hit an analysis / read / deck route.

    Super admins bypass. Everyone else must resolve to 'analysis' or
    'both'. Callers hitting the master switch off never reach this
    helper because `_synth_chat_gate` rejects first."""
    if not isinstance(user, dict):
        return False
    if str(user.get('role') or '').strip().lower() == 'super_admin':
        return True
    return _H._prometheus_mode_of(user) in ('analysis', 'both')


def _pm_gate_pull(user):
    """True when the user may hit a new-profile-pull route.

    Super admins bypass. Everyone else must resolve to 'pull' or
    'both'. Callers hitting the master switch off never reach this
    helper because `_synth_chat_gate` rejects first."""
    if not isinstance(user, dict):
        return False
    if str(user.get('role') or '').strip().lower() == 'super_admin':
        return True
    return _H._prometheus_mode_of(user) in ('pull', 'both')


# GARBLED-INPUT NORMALIZATION (2026-09-28, Phase 4): "top 1 0 starz
# series" hit the calm fallback because a split number reads as noise.
# Join single digits split by a space when they form a plausible
# number, and collapse runs of spaces. Routing and the model both see
# the repaired text; nothing else changes.
_PM_SPLIT_DIGIT_RE = re.compile(r'\b(\d)\s+(\d)\b')


def _pm_normalize_ask(text):
    t = str(text or '')
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _PM_SPLIT_DIGIT_RE.sub(r'\1\2', t)
    return re.sub(r'[ \t]{2,}', ' ', t)


# NUMERIC FOLLOW-UP (2026-09-28, Phase 3): "what is that as a share of
# the US population?" after a delivered count is arithmetic on the
# last answer, not a fresh read. Resolve the count from the last
# agent turn and answer instantly.
_PM_US_SHARE_RE = re.compile(
    r'\b(?:as a |what )?(?:share|percent(?:age)?)\s+of\s+(?:the\s+)?'
    r'(?:us|u\.s\.|american)\s*(?:population|pop|adults|country|'
    r'gen pop)?\b', re.IGNORECASE)


_PM_COUNT_IN_REPLY_RE = re.compile(
    r'(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[MKB])\s*'
    r'(?:us\s+)?(?:unique\s+)?(?:viewers|people|users|accounts|'
    r'subscribers|buyers|players|individuals)', re.IGNORECASE)


def _pm_parse_count(tok):
    tok = str(tok).strip()
    try:
        if tok[-1] in 'MKBmkb':
            mult = {'k': 1e3, 'm': 1e6, 'b': 1e9}[tok[-1].lower()]
            return float(tok[:-1].strip()) * mult
        return float(tok.replace(',', ''))
    except Exception:
        return None


def _pm_numeric_followup_reply(text, history):
    """US-population share arithmetic on the last delivered count, or
    None when the ask is not that shape or no count resolves."""
    if not _PM_US_SHARE_RE.search(str(text or '')) \
            or len(str(text or '')) > 120:
        return None
    for turn in reversed(list(history or [])[-8:]):
        if (turn.get('role') or '') == 'user':
            continue
        matches = _PM_COUNT_IN_REPLY_RE.findall(
            str(turn.get('text') or ''))
        if not matches:
            continue
        vals = [v for v in (_pm_parse_count(m) for m in matches)
                if v and v > 999]
        if not vals:
            continue
        n = max(vals)
        share = n / 329_900_000.0 * 100.0
        share_s = (f"{share:.2f}%" if share < 1 else f"{share:.1f}%")
        return (f"That is {share_s} of the US population - "
                f"{int(n):,} of 329.9M people.")
    return None


# STATUS-CHECK INTERCEPT (2026-09-28 Jenna, keith's "is eastside golf
# running?" got an empty step on one surface and a duplicate 5-credit
# build offer on the other: "these were clearly a status update check.
# needs to recognize that in the future"). Subject-named status
# questions answer from the caller's own runs; they never reach the
# analysis pass, the answer library, or the build interpreter. The
# interceptor only speaks when a run actually matches the named
# subject, so "is yellowstone still running?" as an airing question
# falls through to analysis untouched when no Yellowstone run exists.
_PM_STATUS_ASK_RES = (
    re.compile(r"^\s*(?:is|are)\s+(?:my\s+|the\s+)?(.+?)\s+(?:still\s+)?"
               r"(?:running|building|done|finished|ready|complete(?:d)?|"
               r"going|in\s+progress|live\s+yet)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:did|has|have)\s+(?:my\s+|the\s+)?(.+?)\s+"
               r"(?:finish(?:ed)?|complete(?:d)?|land(?:ed)?|"
               r"come\s+back|run(?:\s+yet)?)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:status|progress|eta)\s+(?:of|on|for)\s+"
               r"(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*how(?:'s|\s+is)\s+(?:my\s+|the\s+)?(.+?)\s+"
               r"(?:coming(?:\s+along)?|going|doing|progressing)"
               r"\s*\??\s*$", re.I),
)


_PM_STATUS_TAIL_TOKENS = {'build', 'builds', 'profile', 'profiles',
                          'run', 'runs', 'pull', 'pulls', 'cut',
                          'cuts', 'report', 'job', 'request'}


def _pm_status_ask_subject(text):
    """Return the subject phrase of a status question, or ''."""
    t = str(text or '').strip()
    if not t or len(t) > 120:
        return ''
    for rx in _PM_STATUS_ASK_RES:
        m = rx.match(t)
        if not m:
            continue
        words = [w for w in re.split(r'\s+', m.group(1).strip()) if w]
        while words and _H._normalize_for_match(words[-1]) in \
                _PM_STATUS_TAIL_TOKENS:
            words.pop()
        if words:
            return ' '.join(words[:6])
    return ''


def _pm_status_matching_runs(user, phrase, limit=40):
    """The caller's runs whose subject matches the phrase (token
    containment either way), most recent first. Empty on any listener
    trouble - the ask then falls through to normal routing."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return []
    uid = (user.get('email') or user.get('username') or '').strip()
    if not uid:
        return []
    try:
        import requests as _rq
        resp = _rq.get(
            f"{_H.SYNTH_QUEUE_URL}/synth/list",
            params={'limit': limit, 'user': uid},
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=12)
        if resp.status_code != 200:
            return []
        docs = resp.json() or []
    except Exception:
        return []
    want = {w for w in _H._normalize_for_match(phrase).split()
            if w not in _PM_BASE_GENERIC_TOKENS}
    if not want:
        return []
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        subj = {w for w in _H._normalize_for_match(
                    str(doc.get('subject') or '')).split()
                if w not in _PM_BASE_GENERIC_TOKENS}
        if subj and (want <= subj or subj <= want):
            out.append(doc)
    return out


def _pm_status_reply_for_runs(runs):
    """One plain status line per matching run (up to 3). Failed runs
    read as still building - the ops-hold convention keeps internal
    failures invisible while the rerun completes under the same id."""
    lines = []
    for doc in runs[:3]:
        subj = str(doc.get('subject') or 'Your profile').strip()
        st = str(doc.get('status') or '').strip().lower()
        if st == 'complete':
            if doc.get('subiq_s3_key'):
                lines.append(
                    f"{subj} is finished. The Subscriber IQ read is "
                    f"live in the Subscriber IQ tab now.")
            else:
                line = (f"{subj} is finished and live in the Select "
                        f"Profile dropdown.")
                if doc.get('tu_key'):
                    line += f" File: {doc['tu_key']}"
                lines.append(line)
        elif st in ('failed', 'error', 'ops_hold'):
            from prometheus.status_lane import client_failure_line
            _cl = client_failure_line(doc.get('error')) if st == 'failed' else ''
            if _cl:
                lines.append(f"{subj}: {_cl}")
            else:
                lines.append(
                    f"{subj} is still building - it is taking a little "
                    f"longer than usual. It lands in the Select Profile "
                    f"dropdown and this chat confirms the moment it "
                    f"finishes.")
        else:
            si, tot = doc.get('step_index'), doc.get('step_total') or 11
            lab = str(doc.get('step_label') or '').strip()
            if isinstance(si, int) and tot:
                pct = max(0, min(100, int(round(100.0 * si / tot))))
                line = f"{subj} is building now - step {si} of {tot}"
                if lab:
                    line += f" ({lab})"
                line += f", about {pct}%."
            else:
                line = f"{subj} is {st or 'queued'}."
            line += (" It lands in the Select Profile dropdown and "
                     "this chat confirms the moment it finishes.")
            lines.append(line)
    return '\n'.join(lines)


_PM_STATUS_LEAD_TOKENS = {'my', 'the', 'our', 'that', 'this'}


def _pm_library_match(phrase):
    """The catalog profile the phrase names (distinctive-token
    containment either way, like the run match), or None."""
    pt = [w for w in _H._normalize_for_match(phrase).split()
          if w not in _PM_STATUS_TAIL_TOKENS]
    if not pt:
        return None
    pset = set(pt)
    best = None
    try:
        for entry in _profile_catalog_for_chat():
            subj = str(entry.get('subject')
                       or entry.get('display_name') or '').strip()
            if not subj or ' - ' in subj:
                continue
            st = set(_H._normalize_for_match(subj).split())
            if not st:
                continue
            if st <= pset or pset <= st:
                score = len(st & pset)
                if best is None or score > best[0]:
                    best = (score, {
                        'subject': subj,
                        's3_key': str(entry.get('s3_key') or ''),
                        'last_modified': str(entry.get('last_modified')
                                             or ''),
                    })
    except Exception:
        traceback.print_exc()
    return best[1] if best else None


def _pm_status_lane(user, text):
    """Deterministic answer for a subject-named status question on
    either chat surface (2026-10-02 S3 lanes). Returns
    (reply, followups, outcome, subject) or None when the text is not
    a status question. A status question never reaches the model: a
    matching run reads its step, a library profile reads as finished,
    anything else reads as not started with a build chip."""
    phrase = _pm_status_ask_subject(text)
    if not phrase:
        return None
    words = phrase.split()
    while len(words) > 1 and words[0].lower() in _PM_STATUS_LEAD_TOKENS:
        words.pop(0)
    phrase = ' '.join(words)
    try:
        runs = _pm_status_matching_runs(user, phrase)
    except Exception:
        runs = []
    if runs:
        return (_pm_status_reply_for_runs(runs), [], 'answered',
                str(runs[0].get('subject') or phrase))
    lib = _pm_library_match(phrase)
    if lib:
        when = ''
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(str(lib.get('last_modified') or '')[:10],
                             '%Y-%m-%d')
            when = d.strftime('%b %-d, %Y')
        except Exception:
            when = ''
        reply = (f"Nothing named {phrase} is building for your account "
                 f"right now. {lib['subject']} is already in the library"
                 + (f" (finished {when})." if when else ".")
                 + " Open it from Select Profile, or ask me to read it "
                 "here.")
        return (reply, [f"Analyze {lib['subject']}"], 'in_library',
                lib['subject'])
    reply = (f"Nothing named {phrase} is building for your account, and "
             f"it is not in the library yet. Say 'Build a profile for "
             f"{phrase}' and I will set up the brief for you to approve.")
    return (reply, [f"Build a profile for {phrase}"], 'not_found', phrase)


# TYPED APPROVAL WITH NO BRIEF (2026-10-02 S3 lanes). The widget turns
# "approved" into the Approve button while a brief is on screen; the
# bare word only reaches this surface when the card is gone. Approval
# vocabulary only - a plain "yes" can still answer a server question.
_PM_APPROVAL_WORD_RE = re.compile(
    r"^\s*(?:yes[,.!]?\s*)?(?:approved?|approve\s+(?:it|this|that|the\s+"
    r"brief)|run\s+it|ship\s+it|proceed|start\s+(?:it|the\s+build)|"
    r"looks\s+good[,.]?\s*(?:approve|run\s+it|go)|go\s+ahead(?:\s+and\s+"
    r"(?:run|build)\s+it)?)\s*[.! ]*$", re.I)

_PM_APPROVE_NO_DRAFT_COPY = (
    "There is no brief waiting for approval in this thread, so there is "
    "nothing to run yet. Tell me what you want (for example 'Build a "
    "profile for Eastside Golf' or 'Compare Hulu and Peacock') and I "
    "will set up the brief for you to approve.")


from prometheus import kpi_definitions as _kpi  # noqa: E402


def _pm_kpi_view(body_or_ctx):
    """(view_id, on_screen_labels) from a request body or a validated
    page context. Profile IQ when nothing else is open."""
    src_ = body_or_ctx if isinstance(body_or_ctx, dict) else {}
    pc = src_.get('page_context') if 'page_context' in src_ else src_
    pc = pc if isinstance(pc, dict) else {}
    vc = pc.get('view_context') if isinstance(pc.get('view_context'), dict) \
        else {}
    vid = str(vc.get('view_id') or pc.get('view') or 'profileIQ')
    try:
        labels = _kpi.on_screen_labels(vc.get('data') or {})
    except Exception:
        labels = []
    return vid, labels


def _pm_kpi_definition_for(text, body):
    """The glossary entry a definition-shaped ask names, or None."""
    try:
        if not _kpi.is_definition_ask(text):
            return None
        vid, labels = _pm_kpi_view(body)
        return _kpi.find_definition(text, vid, labels)
    except Exception:
        return None


def _pm_kpi_prompt_extras(text, history, ctx, led_block):
    """Append the thread number bank and the view glossary to the
    binding block on a reconcile or definition ask. Never raises."""
    try:
        if not (_kpi.is_reconcile_ask(text) or _kpi.is_definition_ask(text)):
            return led_block
        vid, labels = _pm_kpi_view(ctx)
        parts = [str(led_block or '').strip()]
        nb = _kpi.numbers_block(history)
        if nb:
            parts.append(nb)
        db = _kpi.view_definitions_block(vid, labels)
        if db:
            parts.append(db)
        return '\n\n'.join(p for p in parts if p)
    except Exception:
        return led_block


def _pm_last_agent_asked(history):
    """True when the most recent agent turn in the thread ended on a
    question, so a bare confirm word is an answer to it."""
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role in ('agent', 'assistant'):
            txt = str(h.get('text') or h.get('content') or '').strip()
            return txt.endswith('?')
        if role == 'user':
            return False
    return False


def _pm_approval_word(text):
    """True when the message is approval vocabulary and nothing else."""
    t = str(text or '')
    try:
        from prometheus import user_signal as _us
        t = _us._DATE_RANGE_SUFFIX_RE.sub('', t)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return bool(_PM_APPROVAL_WORD_RE.match(t))


# WORK-ORDER VERBS (2026-09-30 Jenna: "do all of them"). Status, ETA,
# and cancel are account services: subjectless asks ("status?", "are
# you still working on my report?", "how long will this take?",
# "stop") answer deterministically from the caller's own runs on BOTH
# chat surfaces, free, before any gate or model call. Evidence from
# the ask log: emma's nine-turn work-order thread got an ETA shrug and
# a page-analysis answer to "Are you still working on my report?";
# jessie typed "stop" three times and got three page analyses.
_PM_WO_WORK_NOUNS = (
    r"(?:report|profile|build|run|pull|file|request|order|job|"
    r"deliverable|csv|deck|cut|analysis)")


_PM_WO_STATUS_RES = (
    re.compile(r"^\s*(?:status|any\s+updates?|progress|"
               r"status\s+update|what(?:'s|\s+is)\s+the\s+status)"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*are\s+you\s+still\s+working(?:\s+on\s+"
               r"(?:it|that|this|my\s+[\w ]{1,40}|the\s+[\w ]{1,40}))?"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*(?:is|are)\s+(?:it|that|this|they|my\s+"
               + _PM_WO_WORK_NOUNS + r"s?)\s+(?:done|ready|"
               r"finished|complete[d]?)(?:\s+yet)?\s*[?.!]*\s*$",
               re.I),
    re.compile(r"^\s*where(?:'s|\s+is)\s+(?:my|the)\s+"
               r"[\w ]{0,30}?" + _PM_WO_WORK_NOUNS +
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*did\s+(?:it|my\s+" + _PM_WO_WORK_NOUNS +
               r")\s+(?:finish|complete|land|go\s+through)"
               r"(?:\s+yet)?\s*[?.!]*\s*$", re.I),
)


_PM_WO_ETA_RES = (
    re.compile(r"^\s*(?:what(?:'s|\s+is)\s+the\s+)?eta"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*how\s+much\s+longer(?:\s+(?:will|does|is)\s+"
               r"(?:it|this|that)[\w ]{0,20})?\s*[?.!]*\s*$", re.I),
    re.compile(r"\bhow\s+long\s+(?:will|does|do|should)\s+"
               r"(?:it|this|that|the\s+(?:build|run|report|profile|"
               r"pull)|my\s+[\w ]{1,30}?)\s*.{0,40}?\btake\b", re.I),
    re.compile(r"\bwhen\s+will\s+(?:it|that|this|my\s+[\w ]{1,40}|"
               r"the\s+[\w ]{1,40})\s+be\s+"
               r"(?:done|ready|finished|complete)\b", re.I),
)


_PM_WO_CANCEL_RE = re.compile(
    r"^\s*(?:please\s+|ok(?:ay)?[,\s]+|never\s*mind[,\s]+|"
    r"actually[,\s]+)?"
    r"(?:stop|cancel|abort|kill)\b"
    r"(?!\s+(?:showing|sending|giving|telling|asking|using|"
    r"putting|adding|including|counting|saying|repeating)\b)"
    r"(?P<tail>(?:\s+(?:it|that|this|everything|all|the|my))?"
    r"[\w .&'-]{0,50}?)\s*[.!]*\s*$", re.I)


def _pm_subiq_lookup_answer(user, title):
    """Answer "do you see the X Subscriber IQ?" from the library and
    the caller's own runs (2026-10-02, Bria: the question drafted a
    10-credit duplicate of a read finished twelve hours earlier).
    Returns (reply, followups). Never charges, never drafts."""
    import prometheus_analysis as _pma
    title = str(title or '').strip()
    rows = []
    try:
        if title == '*':
            rows = _pma.list_subiq_shows(
                _H.s3_client, _H.SUBSCRIBER_S3_BUCKET)[:5]
        else:
            rows = _pma.match_subiq_shows(
                _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, title)
    except Exception:
        traceback.print_exc()
        rows = []

    def _when(lm):
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(str(lm or '')[:16], '%Y-%m-%dT%H:%M')
            return d.strftime('%b %-d, %Y')
        except Exception:
            return ''

    if rows:
        if title == '*':
            names = ', '.join(r[0] for r in rows)
            return (f"The newest Subscriber IQ reads in the library are "
                    f"{names}. Name one and I will pull it up, or open "
                    f"any of them from the Subscriber IQ tab.",
                    [f"Analyze {rows[0][0]}"])
        show, _key, lm = rows[0]
        when = _when(lm)
        lead = (f"Yes. {show} is in the Subscriber IQ library"
                + (f" (finished {when})." if when else "."))
        if len(rows) > 1:
            others = ', '.join(r[0] for r in rows[1:])
            lead += f" {others} {'is' if len(rows) == 2 else 'are'} there too."
        reply = (lead + " Open it from the Subscriber IQ tab, or ask me "
                 "about it here. No charge to view it.")
        return reply, [f"Analyze {show}",
                       f"Top 3 insights on {show}"]
    # Not in the library: still building?
    try:
        runs = _pm_status_matching_runs(user, title) if title != '*' else []
    except Exception:
        runs = []
    if runs:
        return _pm_status_reply_for_runs(runs), []
    nice = title if title != '*' else 'that title'
    return (f"I do not see a Subscriber IQ for {nice} yet. I can build "
            f"it{_plan.build_cost_clause(_pm_seat_reads_included(_oq.build_usd(_H, 'subscriber_iq')), _pm_usd_label(_oq.build_usd(_H, 'subscriber_iq')))}"
            f", and it lands in the Subscriber IQ tab when it "
            f"finishes.",
            [f"Pull Subscriber IQ for {nice}"] if title != '*' else [])


def _pm_workorder_intent(text):
    """'status' | 'eta' | 'cancel' | None. Anchored, short, and
    subjectless-friendly so real analysis questions never match."""
    t = str(text or '').strip()
    if not t or len(t) > 90 or '\n' in t:
        return None
    if _PM_WO_CANCEL_RE.match(t) and '?' not in t:
        return 'cancel'
    for rx in _PM_WO_ETA_RES:
        if rx.search(t):
            return 'eta'
    for rx in _PM_WO_STATUS_RES:
        if rx.match(t):
            return 'status'
    return None


def _pm_user_runs(user, limit=40, active=False, status=None):
    """The caller's runs from the build engine, newest first. [] on
    any listener trouble so the ask falls through to normal routing."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return []
    uid = (user.get('email') or user.get('username') or '').strip()
    if not uid and not status:
        return []
    try:
        import requests as _rq
        params = {'limit': limit}
        if uid and status is None:
            params['user'] = uid
        if active:
            params['active'] = 1
        if status:
            params['status'] = status
        resp = _rq.get(
            f"{_H.SYNTH_QUEUE_URL}/synth/list", params=params,
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=12)
        if resp.status_code != 200:
            return []
        docs = resp.json() or []
        return [d for d in docs if isinstance(d, dict)]
    except Exception:
        return []


def _pm_typical_build_minutes():
    """Median wall minutes of recently completed builds, or None.
    Honest ETA basis: measured durations, never a made-up promise."""
    docs = _pm_user_runs({}, limit=60, status='complete')
    mins = []
    for d in docs:
        try:
            q = datetime.fromisoformat(
                str(d.get('queued_at') or '').replace('Z', '+00:00'))
            u = datetime.fromisoformat(
                str(d.get('updated_at') or '').replace('Z', '+00:00'))
            m = (u - q).total_seconds() / 60.0
            if 3 <= m <= 360:
                mins.append(m)
        except Exception:
            continue
    if not mins:
        return None
    mins.sort()
    return mins[len(mins) // 2]


def _pm_eta_line(doc, typical=None):
    """Plain remaining-time sentence for one active run, or ''."""
    try:
        typical = typical or _pm_typical_build_minutes()
        if not typical:
            return ''
        q = datetime.fromisoformat(
            str(doc.get('queued_at') or '').replace('Z', '+00:00'))
        elapsed = (datetime.now(timezone.utc) - q).total_seconds() / 60.0
        remaining = typical - elapsed
        if elapsed > 2 * typical:
            return ("It is taking longer than a typical build. It "
                    "finishes on its own and the completion email "
                    "goes out the moment it lands.")
        if remaining <= 4:
            return "Expect it within the next few minutes."
        return (f"Recent builds like this one finish in about "
                f"{int(round(typical))} minutes; this one has roughly "
                f"{int(round(remaining))} minutes to go.")
    except Exception:
        return ''


def _pm_cancel_target(user, text):
    """(doc, others) - the active run the cancel names, or the only
    active run. doc None when nothing matches / nothing active."""
    active = _pm_user_runs(user, active=True)
    if not active:
        return None, []
    m = _PM_WO_CANCEL_RE.match(str(text or '').strip())
    tail = (m.group('tail') or '').strip() if m else ''
    tail = re.sub(r'^(?:it|that|this|everything|all|the|my)\b\s*', '',
                  tail, flags=re.I).strip()
    tail = re.sub(r'\b(?:build|run|profile|pull|report|request|job)s?'
                  r'\s*$', '', tail, flags=re.I).strip()
    if tail:
        want = {w for w in _H._normalize_for_match(tail).split()
                if w not in _PM_BASE_GENERIC_TOKENS}
        for doc in active:
            subj = {w for w in _H._normalize_for_match(
                        str(doc.get('subject') or '')).split()
                    if w not in _PM_BASE_GENERIC_TOKENS}
            if want and subj and (want <= subj or subj <= want):
                return doc, [d for d in active if d is not doc]
        return None, active
    if len(active) == 1:
        return active[0], []
    return None, active


def _pm_workorder_reply(user, text, intent):
    """Deterministic answer for a work-order verb, or None to fall
    through to normal routing. Never raises."""
    try:
        if intent == 'cancel':
            doc, others = _pm_cancel_target(user, text)
            if doc is None and not others:
                return ("Nothing is building for your account right "
                        "now, so there is nothing to stop. If you "
                        "meant something else, say the word and I "
                        "will take care of it.")
            if doc is None and others:
                names = ', '.join(
                    str(d.get('subject') or 'unnamed')
                    for d in others[:4])
                return (f"You have more than one build going: "
                        f"{names}. Say 'cancel' plus the name and I "
                        f"will stop that one.")
            run_id = str(doc.get('run_id') or '')
            subj = str(doc.get('subject') or 'that build')
            try:
                import requests as _rq
                resp = _rq.post(
                    f"{_H.SYNTH_QUEUE_URL}/synth/cancel/{run_id}",
                    headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET},
                    timeout=12)
                ok = resp.status_code == 200
                st = (resp.json() or {}).get('status', '') if ok else ''
            except Exception:
                ok, st = False, ''
            if ok and st == 'cancelled':
                return (f"Stopped. {subj} was cancelled before it "
                        f"started and the charge comes back to your "
                        f"balance automatically. Nothing else was "
                        f"touched.")
            if ok:
                return (f"Stopping {subj} now. It unwinds at the next "
                        f"safe point and the charge comes back to "
                        f"your balance automatically. Nothing else "
                        f"was touched.")
            _H._chatbot_error_email(
                'pm/cancel',
                f'cancel request did not reach the engine '
                f'(run {run_id}, subject {subj})',
                user_email=(user.get('email') or user.get('username')),
                payload={'question': text[:300], 'run_id': run_id})
            return _H._CHATBOT_CALM_MESSAGE
        # status / eta share the same data
        active = _pm_user_runs(user, active=True)
        if intent == 'eta':
            if active:
                lead = _pm_status_reply_for_runs(active[:1])
                eta = _pm_eta_line(active[0])
                return (lead + ('\n' + eta if eta else '')).strip()
            typical = _pm_typical_build_minutes()
            if typical:
                return (f"Nothing is building for your account right "
                        f"now. For reference, a typical build "
                        f"finishes in about {int(round(typical))} "
                        f"minutes end to end.")
            return ("Nothing is building for your account right now. "
                    "Ask for any audience and I will kick one off.")
        # status
        if active:
            lead = _pm_status_reply_for_runs(active[:3])
            eta = _pm_eta_line(active[0])
            return (lead + ('\n' + eta if eta else '')).strip()
        recent = _pm_user_runs(user, limit=6)
        for doc in recent:
            st = str(doc.get('status') or '').lower()
            if st == 'complete':
                return _pm_status_reply_for_runs([doc])
        return ("Nothing is building for your account right now. "
                "Everything you have run is already live in the "
                "Select Profile dropdown. Ask for any audience and I "
                "will kick off a new one.")
    except Exception:
        traceback.print_exc()
        return None


# DELIVERY + FEEDBACK INTERCEPTS (2026-09-28, the W39 weekly review):
# "please email david carter@... when it is ready" and "the key art is
# from an older series, please replace it" each burned a full paid
# read. A delivery request registers the ready-notification on the
# caller's in-flight builds; product feedback and methodology
# challenges acknowledge, forward to ops, and never generate.
_PM_EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


_PM_EMAIL_WHEN_READY_RE = re.compile(
    # '.' stays inside the spans: addresses carry dots
    # (david.carter@spe.sony.com). 2026-09-30: notify / tell /
    # ping / alert / let me know / update me all register the
    # ready-notification, same as email.
    r"\b(?:e-?mail|send|notify|tell|ping|alert|update|let\s+me\s+know)\b"
    r"[^?!\n]{0,80}?\bwhen\b[^?!\n]{0,30}?"
    r"\b(?:ready|done|finish(?:e[sd])?|complete[sd]?|lands?|arrives?)\b",
    re.IGNORECASE)


_PM_WRONG_ANSWER_RES = (
    # "thats not what i asked for" and family (2026-09-28, Casey).
    re.compile(r"\bnot\s+what\s+i\s+(?:was\s+)?ask(?:ed|ing)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:wrong|incorrect)\s+"
               r"(?:answer|read|response|data|numbers?)\b", re.IGNORECASE),
    re.compile(r"\b(?:that|this)\s+(?:is|was|'?s)\s+"
               r"(?:wrong|incorrect|not\s+right)\b", re.IGNORECASE),
    re.compile(r"\b(?:you\s+)?did\s*n[o']?t\s+answer\s+"
               r"(?:my|the)\b", re.IGNORECASE),
    re.compile(r"\bdoes\s*n[o']?t\s+answer\s+(?:my|the)\s+"
               r"question\b", re.IGNORECASE),
    re.compile(r"\banswer\s+(?:my|the)\s+(?:actual\s+)?"
               r"question\b", re.IGNORECASE),
    re.compile(r"\btry\s+(?:that\s+)?again\b", re.IGNORECASE),
    re.compile(r"\bstill\s+(?:wrong|not\s+right)\b", re.IGNORECASE),
    re.compile(r"\bre\s*-?\s*read\s+my\s+question\b", re.IGNORECASE),
)


def _pm_prev_user_question(history, complaint):
    """The reader's last substantive question before a wrong-answer
    complaint: newest user turn that is not itself complaint-shaped
    and long enough to be a real ask."""
    try:
        for turn in reversed(list(history or [])):
            if str((turn or {}).get('role') or '') != 'user':
                continue
            t = str(turn.get('text') or '').strip()
            if not t or len(t) < 15:
                continue
            if t == str(complaint or '').strip():
                continue
            if any(rx.search(t) for rx in _PM_WRONG_ANSWER_RES):
                continue
            return t
    except Exception as _fx:
        _faults.note('chat', _fx)
    return ''


def _pm_replay_repeat_block(pm_user, question, window_s=1500):
    """True when this user was ALREADY served a library replay for
    this same question within the window. Re-asking the identical
    question minutes after a replay means the stored read did not
    satisfy - the ask runs fresh instead of replaying again
    (2026-09-28, the same entry served four times in 90 seconds)."""
    try:
        import prometheus_memory as pmm
        qn = ' '.join(re.sub(r"[^a-z0-9 ]+", ' ',
                             str(question or '').lower()).split())
        if not qn or not pm_user:
            return False
        now = datetime.now(timezone.utc)
        for rec in (pmm.recall(pm_user, 12) or []):
            if str((rec or {}).get('route') or '') != 'replay':
                continue
            rqn = ' '.join(re.sub(r"[^a-z0-9 ]+", ' ',
                                  str(rec.get('question') or '')
                                  .lower()).split())
            if rqn != qn:
                continue
            try:
                ts = datetime.fromisoformat(
                    str(rec.get('ts') or '').replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if (now - ts).total_seconds() <= window_s:
                return True
        return False
    except Exception:
        return False


def _pm_replay_rejected_in_thread(entry, history):
    """True when the stored entry's reply already appeared in this thread
    and the reader's next message pushed back on it (prometheus.user_signal.
    rejected_reply). A replay of those words is the loop Jessie hit on
    2026-11-04; the ask runs fresh instead. Never raises."""
    try:
        from prometheus import user_signal as _us
        reply = str((entry or {}).get('reply') or '')
        if reply and _us.rejected_reply(reply, history):
            print(f"[pm-replay] stored read {str((entry or {}).get('k') or '')!r} was pushed back on in this thread; running fresh")
            return True
    except Exception as _fx:
        _faults.note('chat', _fx)
    return False


_PM_REFUSAL_RX = re.compile(
    r"(?:re-?\s?aim|could\s+not\s+lock|cannot\s+lock|"
    r"rather\s+than\s+guess|tell\s+me\s+the\s+specific\s+read|"
    r"name\s+the\s+cohort,\s+the\s+category|"
    r"rephrase\s+(?:the|your)\s+question|"
    r"ask\s+(?:me\s+)?(?:a\s+|the\s+)?(?:different|another)\s+"
    r"question)", re.IGNORECASE)


def _pm_reads_as_refusal(reply, mode=''):
    """A generated answer that declines to answer, or carries no
    numbers at all, is not a read (2026-09-28: the model authored
    "could not lock the numbers... re-aim than guess" and it shipped).
    Mode chips are render-shape commands and skip the digit test."""
    t = str(reply or '')
    if not t:
        return False
    if _PM_REFUSAL_RX.search(t):
        return True
    if mode:
        return False
    digits = len(re.findall(r"\d", t))
    return digits < 2 and len(t) < 900


_PM_FEEDBACK_RES = (
    # wrong / stale artwork, images, titles on a card
    re.compile(r"\b(?:key\s*art|artwork|thumbnail|poster|image|logo)\b"
               r"[^.?!\n]{0,60}\b(?:wrong|older|old|incorrect|replace|"
               r"outdated|from an?\b)", re.IGNORECASE),
    re.compile(r"\breplace\s+(?:the|this|that)\s+"
               r"(?:key\s*art|artwork|thumbnail|poster|image|logo)\b",
               re.IGNORECASE),
)


_PM_METHOD_CHALLENGE_RES = (   # "how is X calculated" is a definition question, answered, never a challenge (Jenna 2026-10-14)
    re.compile(r"\b(?:seems?|looks?|is)\s+(?:very|too|way too)\s+"
               r"(?:high|low)\b", re.IGNORECASE),
)


_PM_FEEDBACK_SENT = {}


_PM_REGRESSION_CASES_KEY = 'system/pm_regression_cases.json'


def _pm_regression_q_key(text):
    """Stable key for one question: the shared fold (prometheus.question_key), md5[:16]."""
    from prometheus import question_key as _qk   # local: this function is extracted by source in a test
    return _qk.key(text)


def _pm_compact_for_bank(value, depth=0):
    """Trim a view-context payload for the regression registry: long
    strings cut, lists and dicts capped, depth capped. Keeps the
    on-screen vocabulary the nightly replay matches against."""
    if depth >= 4:
        return None
    if isinstance(value, str):
        return value[:400]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_pm_compact_for_bank(v, depth + 1)
                for v in value[:25]]
    if isinstance(value, dict):
        out = {}
        for k in list(value.keys())[:40]:
            out[str(k)[:80]] = _pm_compact_for_bank(
                value[k], depth + 1)
        return out
    return str(value)[:200]


def _pm_bank_regression_case(username, question, complaint, history,
                             view_ctx):
    """A question the reader called wrong becomes a permanent nightly
    regression case (Jenna 2026-09-30: a mistake fixed once stays
    fixed). Registry: system/pm_regression_cases.json, replayed every
    night by migration/pm_regression_nightly.py on the build server.
    Dedupe by normalized question. Fire-and-forget off the request
    thread; never raises into the chat path."""
    def _bank():
        try:
            key = _pm_regression_q_key(question)
            if not key or not str(question or '').strip():
                return
            try:
                resp = _H.s3_client.get_object(
                    Bucket=_H.S3_BUCKET, Key=_PM_REGRESSION_CASES_KEY)
                doc = json.loads(resp['Body'].read().decode('utf-8'))
            except Exception:
                doc = {}
            cases = doc.get('cases') if isinstance(doc, dict) else None
            if not isinstance(cases, list):
                cases = []
            if any(isinstance(c, dict) and c.get('id') == key
                   for c in cases):
                return
            rejected = ''
            for turn in reversed(list(history or [])):
                if isinstance(turn, dict)                         and turn.get('role') != 'user':
                    rejected = str(turn.get('text') or '')[:400]
                    break
            # The learning loop (2026-10-06): the figures the rejected
            # reply banked leave the corpus catalog, so the regenerated
            # answer and every later one cannot lean on them.
            try:
                from migration import corpus_catalog as _cc_rej
                _full_rejected = ''
                for turn in reversed(list(history or [])):
                    if isinstance(turn, dict) and turn.get('role') != 'user':
                        _full_rejected = str(turn.get('text') or '')
                        break
                _tid = str(getattr(_PM_REQ_THREAD, 'tid', '') or '')
                if not _tid and username:
                    try:
                        _tid = str((_load_threads_index(username) or {}).get('active') or '')
                    except Exception:
                        _tid = ''
                if _full_rejected:
                    _cc_rej.retire_answer(username or '', _tid, _full_rejected)
            except Exception:
                traceback.print_exc()
            vc = view_ctx if isinstance(view_ctx, dict) else {}
            view_id = str(vc.get('view_id') or '').strip()
            view_data = (_pm_compact_for_bank(vc.get('data'))
                         if vc.get('data') else None)
            subj = ''
            try:
                import prometheus_analysis as pma
                subj = str(pma.guess_subject_from_text(question)
                           or '').strip()
            except Exception:
                subj = ''
            blob_len = (len(json.dumps(view_data))
                        if view_data is not None else 0)
            if view_id and blob_len >= 80:
                check = 'view_grounded'
            elif subj:
                check = 'subject_named'
            else:
                check = 'context_followup'
            cases.append({
                'id': key,
                'created': _pm_iso_now(),
                'user': str(username or '')[:80],
                'question': str(question or '')[:500],
                'complaint': str(complaint or '')[:300],
                'rejected_answer': rejected,
                'view_id': view_id,
                'view_title': str(vc.get('view_title') or '')[:200],
                'view_data': view_data,
                'subject_at_capture': subj[:120],
                'check': check,
                'muted': False,
            })
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=_PM_REGRESSION_CASES_KEY,
                Body=json.dumps({'cases': cases}).encode('utf-8'),
                ContentType='application/json')
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_bank, daemon=True).start()


def _pm_forward_user_feedback(username, text, kind):
    """One SES note to ops per distinct feedback message per hour.
    Never raises, never blocks the reply. A probe never mails."""
    try:
        if _pm_probe_caller() or _pm_is_probe_user(username):
            return
        sig = hashlib.md5(
            f"{username}|{str(text)[:200]}".encode()).hexdigest()
        now = time.time()
        if now - _PM_FEEDBACK_SENT.get(sig, 0) < 3600:
            return
        _PM_FEEDBACK_SENT[sig] = now

        def _send():
            try:
                import boto3 as _b3
                ses = _b3.client('ses', region_name='us-east-2')
                ses.send_email(
                    Source='BehavioralGraph <jenna@crosswalknyc.com>',
                    Destination={'ToAddresses': [
                        'jenna@crosswalknyc.com',
                        'jessie@crosswalknyc.com']},
                    Message={
                        'Subject': {'Data':
                                    f'Prometheus user feedback: '
                                    f'{username or "unknown"}'},
                        'Body': {'Text': {'Data': (
                            f'Kind: {kind}\n'
                            f'User: {username}\n\n'
                            f'{str(text)[:1500]}\n')}}})
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_send, daemon=True).start()
    except Exception:
        traceback.print_exc()


def _pm_cant_do_lane(username, text):
    """(reply, chips, kind) when the message asks for an action this
    chat cannot take (add a user, refund credits, rename or re-image a
    profile, edit a number by hand, contact a third party, set up a
    recurring run, push a file into another tool), else None. The
    note goes to the team on the same hourly-deduped path as product
    feedback; the reply says what Prometheus can do instead. No model
    call (2026-10-02 S8)."""
    try:
        from prometheus import capabilities as _pc
        if not _pc.enabled():
            return None
        kind = _pc.action_request(text)
        if not kind:
            return None
        reply, chips = _pc.cant_do_reply(kind)
        _pm_forward_user_feedback(username, text, 'action_request_' + kind)
        return reply, chips, kind
    except Exception:
        traceback.print_exc()
        return None


_PM_BUILD_NOTIFY_PREFIX = 'system/pm_build_notify/'


def _pm_register_build_notify(run_id, emails, requested_by):
    """Stash a ready-notification opt-in for one queue run. The worker
    merges these into the recipient list of the branded completion
    email at send time."""
    try:
        _H.s3_client.put_object(
            Bucket=_H.S3_BUCKET,
            Key=f"{_PM_BUILD_NOTIFY_PREFIX}{run_id}.json",
            Body=json.dumps({
                'run_id': run_id,
                'emails': list(emails),
                'requested_by': requested_by,
                'requested_at': time.time(),
            }).encode('utf-8'),
            ContentType='application/json')
        return True
    except Exception:
        traceback.print_exc()
        return False


# CUT-REQUEST INTERCEPT (2026-09-28 Jenna, keith's "Let's do one cut
# of males only and another cut of black consumers" answered with a
# replayed brand read: "it should have asked him what profile he was
# talking about and then helped prompt him to run cuts of it").
# Imperative cut vocabulary near 'cut(s)'; analytical questions about
# an existing cut ("what's the male cut of the audience") stay on the
# analysis path.
_PM_CUT_INTENT_RE = re.compile(
    r"\b(?:do|run|make|create|add|build|pull|need|want|get|give|lets|"
    r"let's|can you|could you|please)\b[^.?!\n]{0,40}?\bcuts?\b"
    r"|\bcut\s+(?:this|that|it|the)\b"
    # imperative-start command, incl. the composed chip:
    # 'Cut USA Today by males only and black consumers'
    r"|^\s*cuts?\b", re.IGNORECASE)


# 'one cut of males only and another cut of black consumers' ->
# ['males only', 'black consumers']
_PM_CUT_COHORT_RE = re.compile(
    r"\bcuts?\s+(?:of|for|by)\s+([a-z0-9][a-z0-9 +&'\-]{1,40}?)"
    r"(?=\s+(?:and|plus)\b|\s*[,.;!?\n]|$)", re.IGNORECASE)


_PM_CUT_IDIOM_RE = re.compile(
    r"\bcut\s+(?:to the chase|corners|it (?:out|down|short))\b",
    re.IGNORECASE)


def _pm_text_names_catalog_subject(text):
    """True when the ask itself names a catalog profile (every
    distinctive token of some catalog subject appears in the text)."""
    toks = set(_H._normalize_for_match(text).split())
    if not toks:
        return False
    try:
        for entry in _subject_rules.library_named(_profile_catalog_for_chat(), text):
            st = [w for w in _H._normalize_for_match(
                      str(entry.get('subject') or '')).split()
                  if w not in _PM_BASE_GENERIC_TOKENS]
            if st and sum(len(w) for w in st) >= 4 and set(st) <= toks:
                return True
    except Exception:
        traceback.print_exc()
    return False


def _pm_gate_refusal(kind):
    """Build a partner-safe 403 for a mode-blocked chatbot request.

    `kind` is 'analyze' when the caller tried to reach a read / deck
    surface but their mode is 'pull'; 'pull' when they tried to reach
    a new-build surface but their mode is 'analysis'. The reply carries
    `guidance=True` so the widget renders it as a plain agent bubble
    (see the `data.guidance && data.error` branch in the analyze /
    approve / interpret handlers). No internal vocabulary."""
    if kind == 'analyze':
        msg = ('This account covers new profile pulls only. To '
               'unlock analysis of existing profiles, please '
               'contact your account manager.')
    else:
        msg = ('This account covers analysis only. To unlock the '
               'ability to pull new profiles, please contact your '
               'account manager.')
    return jsonify({
        'success': False,
        'guidance': True,
        'error': msg,
    }), 403


def _pm_access_gate(user):
    """Prometheus tier gate for the analysis surfaces (2026-08-26).

    Admin sets prometheus_access per user: 'full' (analysis and
    everything else - the default; every existing user resolves here)
    or 'pulls_only' (Profile IQ / Subscriber IQ builds only). A
    pulls_only user who has NOT opted into pay as you go gets Jenna's
    exact offer copy with Yes/No chips instead of the analysis; the
    affordance itself never hides. Returns None when the user may
    proceed."""
    import pay_per_use as ppu
    if ppu.analysis_allowed(user):
        return None
    _pm_ask_hint(outcome='declined_not_subscribed')
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': ppu.OFFER_MESSAGE,
        'followups': ['Yes', 'No'],
        'pay_per_use_offer': True,
        'offer_deck': False, 'deck_angle': None})


def _pm_has_funding(user, username, data=None):
    """Pure funding decision for the Prometheus ask surfaces (Jenna
    2026-09-16: "make sure that no one gets free metered usage of
    prometheus if they do not have any credits/money in their account
    unless they've been set to unlimited").

    Returns (allowed, reason). Allowed when ANY funding source is open:
      * unlimited: personal credits == -1, the admin 'unlimited' flag,
        or an unlimited company pool reachable by this user
        (check_user_credits returns -1),
      * credits: personal / company-pool credits remaining > 0 within
        the user's ceiling,
      * money: the resolved billing subject (personal wallet or the
        company-shared wallet) can absorb one metered answer at the
        configured rate - covers prepaid balance, auto-reload with a
        card on file, and monthly-invoice room.
    Role is not a free pass. Liz (super_admin, $0, 0 credits) still
    ran Prometheus until this check treated her like any other
    drained account (Jenna 2026-09-16).
    Blocked only when every source above is exhausted."""
    user = user or {}
    try:
        import wallet as _w
    except Exception:
        traceback.print_exc()
        return False, 'wallet_unavailable'
    try:
        if _w.is_unlimited(user):
            return True, 'unlimited'
    except Exception as _fx:
        _faults.note('chat', _fx)
    try:
        if data is None:
            data = _H.load_users()
        if _w.opening_funding_unmet(user, data):
            return False, 'opening_topup'
    except Exception:
        traceback.print_exc()
    try:
        has_cr, left = _H.check_user_credits(username)
        if left == -1:
            return True, 'unlimited_pool'
        if has_cr and left > 0:
            return True, 'credits'
    except Exception:
        traceback.print_exc()
    try:
        if data is None:
            data = _H.load_users()
        subject, _kind, _key = _w.resolve_billing_subject(user, data)
    except Exception:
        subject = user
    try:
        rate = float(_w.metered_answer_usd())
    except Exception:
        rate = 2.10
    try:
        ok, _why = _w.wallet_can_absorb(subject or {}, rate)
        if ok:
            return True, 'wallet'
    except Exception:
        traceback.print_exc()
    return False, 'no_funding'


def _pm_funds_gate(user):
    """Prometheus funds gate: None when the user may proceed, else the
    partner-safe paused reply. Session surfaces only. Fails open on
    internal lookup errors (a billing-file hiccup must never take
    Prometheus down for funded users); the pure decision lives in
    _pm_has_funding."""
    try:
        uname = (session.get('username') or (user or {}).get('username')
                 or '').strip()
        allowed, reason = _pm_has_funding(user, uname)
        if allowed:
            return None
        try:
            _pm_ask_hint(outcome='declined_no_funds')
        except Exception as _fx:
            _faults.note('chat', _fx)
        print(f"[prometheus] funds gate blocked {uname!r} ({reason})")
        if reason == 'opening_topup':
            try:
                import lock_hit_notify as _lhn
                _lhn.notify_opening_lock_hit(user, uname, where="prometheus")
            except Exception:
                pass
        reply = (_H.opening_funds_message(user) if reason == 'opening_topup'
                 else _plan.out_of_plan_reply(_H, uname, user, _H.NO_FUNDS_MESSAGE))   # a plan allowance: the plain top-up ask, one notice
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': reply,
            'followups': [],
            'no_funds': True,
            'top_up_url': '/wallet',
            'top_up_label': 'Add funds',
            'offer_deck': False, 'deck_angle': None})
    except Exception:
        traceback.print_exc()
        return None


def _pm_usage_extras(user):
    """Attribution extras for a pay-as-you-go user's model calls.


    Returns None when billing_active is false (unlimited, super_admin,
    or pulls_only without the opt-in). Everyone else, including
    full-tier dashboard users and Kartel, gets username, email, the
    active session id (30-minute idle window), and a per-request id
    so one ask that fans out into several model calls still counts
    as one ask on the bill."""
    import pay_per_use as ppu
    try:
        if not ppu.billing_active(user):
            return None
        email = (user.get('email') or '').strip().lower()
        uname = (session.get('username') or user.get('username')
                 or '').strip()
        rid = None
        try:
            from flask import g as _g
            rid = getattr(_g, '_pm_ppu_request_id', None)
            if not rid:
                rid = uuid.uuid4().hex[:12]
                _g._pm_ppu_request_id = rid
        except Exception:
            rid = uuid.uuid4().hex[:12]
        return {'user': uname or email,
                'user_email': email or uname,
                'session_id': ppu.touch_session(email or uname),
                'request_id': rid,
                'pay_per_use': True}
    except Exception:
        traceback.print_exc()
        return None


def _pm_attrib_extras():
    """Always-on per-user attribution for a dashboard (Prometheus) model
    call, independent of pay-as-you-go billing.

    Returns {'user', 'user_email'} for the logged-in user when a request
    context is present, else {} (background threads such as the deck job
    have no request context and pass attribution explicitly instead).

    This tags every Prometheus chat record with WHO caused it so the
    daily spend email can break Prometheus cost out per user. It NEVER
    sets pay_per_use, so billing and the credit gate (both keyed off
    _pm_usage_extras returning None for subscribed users) are untouched.
    Never raises."""
    try:
        from flask import has_request_context
        if not has_request_context():
            return {}
        u = _H.get_current_user() or {}
        email = (u.get('email') or '').strip().lower()
        uname = (session.get('username') or u.get('username') or '').strip()
        if not (email or uname):
            return {}
        return {'user': uname or email, 'user_email': email or uname}
    except Exception:
        return {}


def _pm_merge_extras(*parts):
    """Merge attribution dicts left-to-right (later parts win on key
    collisions). Returns a dict, or None when nothing merged (None keeps
    the untagged code path in _run_nflx_claude_agent). Never raises."""
    out = {}
    for p in parts:
        if isinstance(p, dict):
            for k, v in p.items():
                if v is not None:
                    out[k] = v
    return out or None


_PM_BASE_GENERIC_TOKENS = {
    'viewers', 'viewer', 'fans', 'fan', 'series', 'audience', 'buyers',
    'buyer', 'shoppers', 'shopper', 'watchers', 'universe', 'total',
    'tu', 'the', 'of', 'and', 'a', 'an', 'profile', 'consumers',
    'consumer', 'customers', 'customer', 'avid', 'casual', 'movie',
    'show', 'subscribers', 'members', 'users', 'potential',
    'prospective', 'purchasers', 'purchaser',
    # 2026-09-24 (PA-09 vs Florida Gubernatorial): audience nouns that
    # let unrelated electorates / cohorts partial-match on the noun
    # alone carry no identity.
    'voters', 'voter', 'listeners', 'listener', 'readers', 'reader',
}


# BASE CLARIFY (2026-09-24 Jenna, verbatim: 'so that this kind of
# thing doesnt happen if someone makes a request can prometheus say
# "did you want this on X (whatever is open on the screen) or
# something else?"'). The rdesocio asks: six Emily in Paris messages
# sent while the Landman profile was open, answered against Landman.
# When an ask NAMES a subject in subject position (a profile / journey
# / data / audience phrase) that shares no distinctive token with the
# open page, Prometheus asks before answering. The chips ride the
# existing memory-confirm machinery: the widget re-sends the original
# ask with bind_subject, which lands a measured read on the picked
# subject or the build-first offer when that subject has no base yet.
_PM_CLARIFY_STOP_TOKENS = _PM_BASE_GENERIC_TOKENS | {
    'this', 'that', 'these', 'those', 'their', 'them', 'they', 'it',
    'my', 'our', 'your', 'here', 'there', 'iq', 'for', 'in', 'on',
    'to', 'with', 'about', 'at', 'from', 'by', 'data', 'numbers',
    'cut', 'cuts', 'file', 'view', 'page', 'one', 'same',
    # Interrogatives / auxiliaries / ask-verbs: lead-strip fodder so
    # 'how big is the yellowstone audience' resolves to Yellowstone,
    # not the whole question.
    'how', 'what', 'whats', 'who', 'whos', 'when', 'where', 'why',
    'which', 'is', 'are', 'was', 'were', 'am', 'be', 'been', 'do',
    'does', 'did', 'can', 'could', 'will', 'would', 'should', 'may',
    'might', 'have', 'has', 'had', 'give', 'get', 'gets', 'tell',
    'me', 'please', 'many', 'much', 'i', 'we', 'you', 'us', 'big',
    'bigger', 'biggest', 'lets', 'let', 'want', 'wants', 'need',
    'needs', 'see', 'look', 'looks', 'people', 'person', 'folks',
    'everyone', 'anyone', 'anybody',
    # Category / metric nouns that sit in subject position on page
    # questions ('report on their qsr numbers') but never name a
    # subject on their own.
    'qsr', 'retailers', 'categories', 'category', 'brands', 'brand',
    'spend', 'sales', 'revenue', 'churn', 'retention', 'engagement',
    'penetration', 'reach', 'index', 'indexes', 'metrics', 'kpis',
    'kpi', 'stats', 'breakdowns', 'summary', 'summaries', 'percent',
    'percentages', 'share', 'shares', 'split', 'splits', 'totals',
}


# One copy of the subject-position rules: prometheus/named_subject.py.
from prometheus import named_subject as _ns_mod  # noqa: E402
_PM_CLARIFY_NAME_RES = _ns_mod.NAME_RES
_PM_CLARIFY_COMPARE_RE = _ns_mod.COMPARE_CUE_RX
_PM_CLARIFY_METRIC_RE = _ns_mod.METRIC_RX


def _pm_ask_names_its_audiences(text):
    """True when the ask already names who it is about (two cuts, or a
    total universe named alongside another audience)."""
    return _ns_mod.ask_names_its_audiences(text)


_PM_FILE_ASK_RE = re.compile(
    r"\b(?:as|in|into|to)\s+(?:a\s+|an\s+)?(?:csv|spreadsheet|excel|xlsx?)\b"
    r"|\b(?:provide|give|send|export|download|output)\b[^.?!]{0,40}"
    r"\b(?:csv|spreadsheet|excel|xlsx?)\b"
    r"|\bcsv\s+(?:file|format|output|export)\b",
    re.I)


_PM_CLARIFY_TURN_RE = re.compile(
    r"(?:do|did) you want this on |which audience should i use", re.I)


_PM_CLARIFY_DECLINE_WORDS = frozenset((
    'no', 'nope', 'neither', 'something else', 'not that', 'none',
    'none of those', 'none of these', 'not those', 'not these',
    'not that one', 'no thanks', 'not on screen', 'not the screen',
    'not whats open', "not what's open", 'different audience',
    'a different one'))

# "answer this now: <question>" and its cousins (2026-10-02 S5, scott
# on 2026-09-30 typed it after two open-screen questions and got the
# question a third time). The prefix is a command: do not ask again.
_PM_ANSWER_NOW_RE = re.compile(
    r"^\W*(?:(?:just|please|ok|okay)[\s,]+)*"
    r"(?:answer (?:this|it|me|the question)(?: now| already| please)?"
    r"|stop asking(?: me)?(?: questions)?|don'?t ask(?: me)?(?: again)?"
    r"|no (?:more )?questions|skip the question|just tell me)"
    r"\s*[:,.!-]*\s*", re.I)


def _pm_answer_now_strip(text):
    """Return (clean_text, True) when the ask opens with an answer-now
    command, else (text, False). Only strips when a real question is
    left over."""
    t = str(text or '')
    m = _PM_ANSWER_NOW_RE.match(t)
    if not m:
        return t, False
    rest = t[m.end():].strip()
    if len(rest) < 8:
        return t, False
    return rest, True


def _pm_answer_now_active():
    try:
        from flask import g as _g
        return bool(getattr(_g, '_pm_answer_now', False))
    except Exception:
        return False


def _pm_clarify_declined(history, text):
    """True when the previous agent turn was the open-screen or
    which-audience question and this turn turns it down ("no",
    "neither", "something else"). The original ask then runs AWAY
    from the page, never back into the same question (2026-10-02 S5)."""
    tl = str(text or '').strip().lower().strip(' .!?')
    if tl not in _PM_CLARIFY_DECLINE_WORDS:
        return False
    turns = [h for h in (history or []) if isinstance(h, dict)]
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = str(turns[i].get('text')
                             or turns[i].get('content') or '')
            return bool(_PM_CLARIFY_TURN_RE.search(last_agent))
        if role == 'user':
            return False
    return False


def _pm_clarify_answer_merge(history, text):
    """When the previous agent turn asked which audience the ask is
    about, this turn is the answer. Merge it back into the question
    that triggered the clarify so the original ask is not lost.

    Casey Pearson, 2026-09-29: hours by genre and platform, two cuts,
    as a csv. Her answer to the which-audience prompt was routed as a
    brand new ask, the original question fell away, and the confirm
    fired again. Returns the merged question, or '' when this turn is
    not a clarify answer."""
    t = str(text or '').strip()
    if not t or len(t) > 240 or '?' in t:
        return ''
    turns = [h for h in (history or []) if isinstance(h, dict)]
    last_agent = ''
    idx = -1
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role in ('agent', 'assistant'):
            last_agent = str(turns[i].get('text')
                             or turns[i].get('content') or '')
            idx = i
            break
        if role == 'user':
            break
    if not last_agent or not _PM_CLARIFY_TURN_RE.search(last_agent):
        return ''
    orig = ''
    for i in range(idx - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        if role != 'user':
            continue
        cand = str(turns[i].get('text')
                   or turns[i].get('content') or '').strip()
        if len(cand) >= 25 and cand.lower() not in (
                'something else', 'yes', 'no'):
            orig = cand
            break
    if not orig or orig.strip().lower() == t.lower():
        return ''
    # A bare yes / no / something-else / open-on-screen answer (typos
    # included: scott, "open on sceren", 2026-09-29) re-runs the
    # original question clean - gluing "Audience: no" onto it would
    # read as an audience named no.
    tl = t.lower().strip(' .!?')
    if tl in ('yes', 'yep', 'yeah', 'correct', 'sure', 'no', 'nope',
              'neither', 'something else', 'not that', 'the screen',
              'on screen', 'whats open', "what's open",
              'use the screen') or tl.startswith('open on'):
        return orig
    return f"{orig}\n\nAudience: {t}"


_PM_SCREEN_DEIXIS_RE = re.compile(
    r"\b(?:this|that|these|those)\s+(?:audience|profile|cohort|cut|"
    r"view|page|data|chart|file|group|base|universe|fan\s*base|"
    r"fans?|viewers?|people|subscribers?|users?|buyers?|shoppers?)\b"
    r"|\b(?:on|for|from|about)\s+(?:this|the)\s+(?:screen|page|view)\b"
    r"|\bopen(?:ed)?\s+on\s+(?:my|the|your)\s+screen\b"
    r"|\b(?:they|them|their|themselves)\b",
    re.I)


_PM_PROFILE_SHAPE_RE = re.compile(
    r"\b(?:age|gender|income|ethnicit\w*|education|occupation|"
    r"demograph\w*|demos?|breakdown|split|skew\w*|over.?index\w*|"
    r"index(?:es|ing)?|penetration|avid|casual)\b", re.I)


_PM_MARKET_SCOPE_RE = re.compile(
    r"\b(?:the\s+us|in\s+the\s+us|u\.s\.|usa|america(?:ns?)?|"
    r"nationwide|nationally|overall|in\s+general|gen\s?pop|"
    r"the\s+market|industry|everyone|average\s+(?:person|american|"
    r"household)|us\s+(?:adults|households|consumers|viewers|"
    r"population|homes))\b", re.I)


_PM_DEFINITE_REF_RE = re.compile(
    r"\b(?:the|its)\s+(?:show|title|series|movie|film|brand|"
    r"audience|profile|fan\s*base)\b", re.I)


def _pm_screen_bind_verdict(text, page, base, page_key=''):
    """page | confirm (Jenna 2026-10-06: nothing is inferred from the
    screen; the page binds only when the ask names it outright)."""
    return _ns_mod.screen_bind_verdict(text, page, base, page_key,
                                       _H._normalize_for_match,
                                       _PM_CLARIFY_STOP_TOKENS)


_PM_VIEW_DEIXIS_RE = re.compile(
    r"\bthis\s+(?:campaign|window|screen|page|view|board|leaderboard|"
    r"journey|study|chart|table|data|dashboard|report)\b"
    r"|\bon\s+(?:this|the)\s+screen\b"
    r"|\bthese\s+(?:numbers|results|rows|trends)\b", re.I)


_PM_CAMPAIGN_ASK_RE = re.compile(
    r"\b(?:campaigns?|attribution|roas|ad\s+spend)\b", re.I)


# Journey-step vocabulary (2026-09-30 Jenna: "where is the fall-off
# from the ticket checkout page?" on the Attribution view drew a
# profile disambiguation). Anyone asking about a conversion journey
# uses these nouns whatever the campaign; on the Attribution view they
# ground the ask, and at the no-base last rung a subjectless hit gets
# the campaign steer instead of memory options.
_PM_FUNNEL_ASK_RE = re.compile(
    r"\b(?:fall[\s-]?offs?|drop[\s-]?offs?|funnel|check[\s-]?outs?|"
    r"tickets?|ticket(?:ing)?\s+pages?|carts?|purchase\s+pages?|"
    r"abandon\w*|first[\s-]touch|last[\s-]touch|assists?|"
    r"retarget\w*|touch[\s-]?points?|converters?|"
    r"conversion\s+rates?|exposed|info[\s-]?seek\w*)\b", re.I)


_PM_VIEW_VOCAB_STOP = frozenset((
    'this', 'that', 'what', 'where', 'when', 'which', 'with', 'from',
    'have', 'does', 'data', 'view', 'show', 'tell', 'about', 'many',
    'much', 'they', 'them', 'then', 'than', 'were', 'will', 'your',
    'mean', 'like', 'into', 'over', 'under', 'most', 'more', 'less',
    'here', 'there', 'page', 'pages', 'screen', 'week', 'month',
    'year', 'window', 'number', 'numbers', 'share', 'total', 'rate',
    'rates', 'percent', 'account', 'accounts', 'people', 'audience'))


def _pm_view_vocab_hit(text, vc):
    """The ask speaks the open view's own vocabulary: distinctive
    tokens from the ask that appear in the serialized on-screen data.
    Two distinctive hits, or one distinctive bigram, bind the view.
    Never raises; an empty or tiny context never matches."""
    try:
        import json as _json
        blob = _json.dumps(vc, ensure_ascii=False, default=str).lower()
    except Exception:
        return False
    if len(blob) < 80:
        return False
    toks = re.findall(r"[a-z][a-z0-9'-]{3,}", str(text or '').lower())
    distinct = [t for t in toks if t not in _PM_VIEW_VOCAB_STOP]
    hits = {t for t in distinct if t in blob}
    if len(hits) >= 2:
        return True
    for a, b in zip(toks, toks[1:]):
        if a in _PM_VIEW_VOCAB_STOP and b in _PM_VIEW_VOCAB_STOP:
            continue
        if f"{a} {b}" in blob:
            return True
    return False


def _pm_foreign_names(text, vc):
    """Capitalized names in the ask that this view does not carry.

    Shared words are not a subject. Debi Pomerantz, 2026-10-21: she
    asked about Colin and Samir's Lexus campaign with the NCIS: New
    York premiere journey open. The journey text contains audience,
    episode, conversion, and youtube, so the vocabulary overlap bound
    the view and the reply came back as that journey's conversion.
    """
    try:
        import json as _json
        blob = _json.dumps(vc, ensure_ascii=False, default=str).lower()
    except Exception:
        blob = ''
    title = str((vc or {}).get('view_title') or '').lower()
    blob = blob + ' ' + title
    skip = _PM_VIEW_VOCAB_STOP | frozenset((
        'have', 'has', 'had', 'this', 'that', 'they', 'them', 'with',
        'from', 'your', 'what', 'when', 'where', 'which', 'would',
        'could', 'should', 'there', 'their', 'about', 'after', 'before',
        'into', 'over', 'under', 'also', 'just', 'only', 'been', 'were',
        'will', 'each', 'other', 'than', 'then', 'here'))
    raw = str(text or '')
    absent = []
    for m in re.finditer(r'\b([A-Z][A-Za-z0-9&]{3,})\b', raw):
        w = m.group(1).lower()
        if w in skip or w in blob:
            continue
        # A capital that only exists because it starts a sentence
        # ("Analyze the data") is not a name. A sentence start still
        # counts when a second name follows ("Colin and Samir").
        prev = raw[:m.start()]
        at_start = (not prev.strip()) or bool(
            re.search(r'[.!?]\s*$', prev))
        if at_start:
            rest = raw[m.end():]
            if not re.match(
                    r'\s+(?:and|&)\s+[A-Z]|\s+[A-Z][A-Za-z0-9&]{3,}',
                    rest):
                continue
        absent.append(w)
    return absent


def _pm_view_owns_ask(text, ctx):
    """True when the on-screen view owns this ask (2026-09-30 Jenna:
    "What drove conversion in this campaign window?" on the
    Attribution view must ground in the campaign on screen, never
    disambiguate between profiles). The view context only exists when
    the user is on a data-bearing non-profile view, so Profile IQ
    asks never land here.

    A name the ask brings that the view does not carry wins. The view
    is not the subject of a different campaign."""
    if not isinstance(ctx, dict):
        return False
    vc = ctx.get('view_context') or {}
    view_id = str(vc.get('view_id') or '').strip()
    if not view_id:
        return False
    t = str(text or '')
    # An ask that names the open product's own title or the product itself
    # is the product's ask; the other capitalized words are its labels
    # typed from the screen (Trailer Launch, Reviews and Opening Week), not
    # foreign names (2026-10-22, Alexia). A different subject still wins
    # through names_other_subject at the call site.
    try:
        from prometheus import view_base as _vb
        if _vb.names_view(t, vc, product_nouns=False):
            return True
    except Exception:
        traceback.print_exc()
    if _pm_foreign_names(t, vc):
        return False
    if _PM_VIEW_DEIXIS_RE.search(t):
        return True
    if __import__('prometheus.screen_scope', fromlist=['enabled']).enabled():
        return bool(__import__('prometheus.screen_scope', fromlist=['view_named']).view_named(t, vc))   # the words name what the view is about or point at it, or the lane asks (Jenna 2026-11-20)
    if view_id == 'intentIQ' and (
            _PM_CAMPAIGN_ASK_RE.search(t)
            or _PM_FUNNEL_ASK_RE.search(t)
            or re.search(r"\bconversions?\b", t, re.I)):
        return True
    # The ask uses words that are literally on the screen (audience
    # names, journey step labels, surfaces): the view owns it whatever
    # the phrasing (2026-09-30 Jenna, checkout fall-off follow-up).
    if _pm_view_vocab_hit(t, vc):
        return True
    return False


def _pm_open_screen_confirm(text, ctx, history=None):
    """Route an ask against the profile open on screen.

    Jenna 2026-10-06 (supersedes the 2026-09-29 question-driven
    default, which itself supersedes the 2026-09-28 always-confirm):
    "Strip the dashboard assumptions out of the lanes ... just ensure
    it always asks to confirm." Returns:
    - {'route': 'bind', 'subject': named} when the ask names its own
      subject (in the text or as a catalog subject) - the caller
      answers on it and the first line says so.
    - None only when no profile is open, the ask names its audiences,
      another handler owns the ask, or the ask names the open page
      outright (the user said it; nothing is assumed).
    - otherwise the one-tap confirm with the page as the first chip,
      the catalog alternative when one resolved, and "Something else".
    """
    page = str((ctx.get('primary') or {}).get('name') or '').strip()
    if not page:
        return None
    # The ask already names its audiences. Do not reduce it to the
    # profile that happens to be open.
    if _pm_ask_names_its_audiences(text):
        return None
    try:
        if _pm_titles_ask_needs_scope(text, page):
            return None
    except Exception:
        traceback.print_exc()
    # A question about the previous answer or a term in it ("what's the
    # 10 million sample you're mentioning") is a turn in the
    # conversation, never a reason to offer the page (2026-10-20 Jenna,
    # Tania Nasr at Paramount+ drew "Do you want this on BET?").
    try:
        if _subject_rules.definition_turn(text, page=page):
            _pm_ask_hint(route='screen_bind', outcome='definition_turn', subject=page)
            return None
    except Exception:
        traceback.print_exc()
    # One subject, bound once (I1, 2026-10-20): a continuation never
    # confirms the page, and a subject the words name (any case, inside
    # "people who watched the Among Us series on Paramount+", even when
    # the page is named as its platform) binds before any page test.
    _b_route = _subject_rules.bind_or_continue(text, ctx, history, _pm_ask_hint)
    if _b_route is not None:
        return _b_route or None
    page_key = str((ctx.get('primary') or {}).get('s3_key') or '')
    # What the user names is not an assumption (2026-10-07 Jenna: Gunna
    # asked on the Warriors page drew "Golden State or Gen_Pop?"; "a
    # script for Brock" on the Brock page drew a confirm). A named
    # library subject binds (its cut when cued); the page named
    # outright, first name included, binds the page or its named cut.
    _norm, _stop = _H._normalize_for_match, _PM_CLARIFY_STOP_TOKENS
    try:
        _ent = _ns_mod.named_entry(text, _profile_catalog_for_chat(),
                                   _norm, _stop)
    except Exception:
        traceback.print_exc()
        _ent = None
    _pg_named = _ns_mod.page_named(text, page, _norm, _stop)
    # On screen, continued (2026-10-13): deixis, the thread's last answer on this page, or a brand that is a row on it binds the page
    _page_signal = (_ns_mod.view_deixis(text) or _ns_mod.thread_on_page(history or [], page, _norm)
                    or (bool(_pm_page_brand_named(text, page_key)) and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled()))   # a row named is not a pointer (Jenna 2026-11-20)
    if _ent and str(_ent.get('s3_key') or '') and _ns_mod.platform_reference(
            text, str(_ent.get('display_name') or _ent.get('subject') or '').split(' - ')[0]):
        _ent = None   # "his youtube channel link" names a surface, not the YouTube profile
    if _ent and str(_ent.get('s3_key') or ''):
        _ent_name = str(_ent.get('display_name')
                        or _ent.get('subject') or '').strip()
        if not _ns_mod.same_family(_ent, page, _norm, _stop):
            _pm_ask_hint(route='screen_bind', outcome='bound_named_subject',
                         subject=_ent_name)
            return {'route': 'bind', 'subject': _ent_name}
        if _pg_named and str(_ent.get('s3_key')) != page_key:
            _pm_ask_hint(route='screen_bind', outcome='bound_named_cut',
                         subject=_ent_name)
            return {'route': 'bind', 'subject': _ent_name}
    named = ''
    if not _pg_named:
        # The ask names a subject of its own (2026-10-08 Jenna): it binds
        # that subject, in the library or not. The page is only ever
        # offered for an ask that names nothing.
        try:
            named = _pm_page_clarify_subject(text, page) or _pm_consumption_subject(text)
            if not named:   # a phrase lifted from prose must read as a name ("Age Or Income" did not, 2026-10-07)
                named = _ns_mod.mentioned_subject(text, page, _norm, _stop, _pm_plausible_subject)
                _in_lib = bool(named) and any(_norm(_ns_mod.family_name(e)) == _norm(named)
                                               for e in _profile_catalog_for_chat())
                if named and not _ns_mod.alternative_ok(named, text, _in_lib, _pm_plausible_subject):
                    named = ''
        except Exception:
            traceback.print_exc()
        if not named:
            # the Reader's own subject for the words wins over the open tab, lowercase or not (2026-10-20 Jay Patel, "the film mutiny")
            named = _subject_rules.current_reading_subject(page, _norm)
            if named:
                _pm_ask_hint(route='screen_bind', outcome='bound_reader_subject', subject=named)
        if named and _norm(named) != _norm(page):
            _pm_ask_hint(route='screen_bind', outcome='bound_named_text',
                         subject=named)
            return {'route': 'bind', 'subject': named}
    _alt_named = named
    attach = True
    try:
        base = _pm_generation_base('', text, ctx=ctx, prefer_catalog=True)
    except Exception:
        traceback.print_exc()
        base = None
    if base and str(base.get('source') or '') != 'page':
        bkey = str(base.get('s3_key') or '')
        bsub = _H._normalize_for_match(
            str(base.get('subject') or '').split(' - ')[0])
        psub = _H._normalize_for_match(page.split(' - ')[0])
        same = (bool(page_key) and bkey == page_key) or (
            bool(bsub) and bool(psub)
            and (bsub == psub or bsub in psub or psub in bsub))
        attach = same
    if not attach:
        # a catalog subject outside the page's family. Written in the
        # ask (its distinctive tokens are all there): it binds, the
        # page is not offered (2026-10-08 Jenna). Only a fuzzy hit
        # the user did not write still confirms with both chips.
        _bsub = str((base or {}).get('subject') or '').strip()
        if _bsub and _ns_mod.platform_reference(text, _bsub.split(' - ')[0]):
            _bsub = ''
        _bsub_d = _ns_mod.distinct_tokens(_bsub.split(' - ')[0], _norm, _stop)
        if _bsub and _bsub_d and _bsub_d <= set(_norm(text).split()):
            _pm_ask_hint(route='screen_bind', outcome='bound_named_subject',
                         subject=_bsub)
            return {'route': 'bind', 'subject': _bsub}
        if _bsub and (_pg_named or _page_signal):
            # the page is named or on screen; an unwritten library hit is a near-miss of it, not a second subject (Brock vs Block)
            _pm_ask_hint(route='screen_bind', outcome='bound_screen',
                         subject=page)
            return None
        if _bsub and not _alt_named:
            _alt_named = _bsub
    # Always confirm (2026-10-06 Jenna): nothing named, nothing assumed;
    # one "Yes, {page}" per thread binds later asks on the same page.
    verdict = _pm_screen_bind_verdict(text, page, base, page_key)
    # The open profile is context, never a default subject (2026-10-20
    # Jenna, Mark at Bent Pixels: three turns into DraftKings users, with
    # "I am not interested in salish matter" already said, the deck lane
    # still asked "Do you want this on Salish Matter?"). A page the
    # reader has said no to in this thread is never the lead chip, never
    # bound by an earlier confirm, never the answer-now base; the
    # thread's own subjects come first.
    _osq = _subject_rules.open_screen_question(text, page, history)
    if _osq and (_osq['outcome'].endswith('page_rejected') or not _alt_named):
        _pm_ask_hint(route='open_screen_confirm', outcome=_osq.pop('outcome'), subject=page)
        return jsonify(_osq)
    if _alt_named:
        verdict = 'confirm'
    elif verdict == 'confirm' and (
            _page_signal
            or _pm_thread_confirmed_page(history, page)
            or (_pm_page_confirmed_recently(_pm_ask_log_user(''), page) and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled())):   # another thread's yes binds nothing here (2026-11-20)
        _pm_ask_hint(route='screen_bind', outcome='bound_thread_confirmed',
                     subject=page)
        return {'route': 'bind', 'subject': page}
    if verdict == 'confirm' and _pm_answer_now_active():
        # The reader said answer now: the thing on their screen is
        # the answer's base, stated in the reply with the other file
        # as a switch chip. No third question (2026-10-02 S5).
        verdict = 'page'
    if verdict == 'confirm' and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled() and any(   # asked once is not answered once: the question is asked again (Jenna 2026-11-20)
            str(t.get('role') or '') != 'user' and str(t.get('text') or t.get('content') or '').startswith(f'Do you want this on {page}')
            for t in (history or [])):
        # never twice in a thread (2026-10-13 audit); asked once, the page is the base
        _pm_ask_hint(route='screen_bind', outcome='bound_after_one_confirm', subject=page)
        verdict = 'page'
    if verdict == 'page':
        _pm_ask_hint(route='screen_bind', outcome='bound_screen',
                     subject=page)
        return None
    yes = f'Yes, {page}'
    _opts = [{'label': yes, 'subject': page}]
    _alt = _alt_named or str((base or {}).get('subject') or '').strip()
    if _alt and not _ns_mod.alternative_ok(_alt, text, bool((base or {}).get('s3_key')), _pm_plausible_subject):
        _alt = ''   # a platform surface or a phrase that is not a name is never a chip (2026-10-13)
    if _alt and _H._normalize_for_match(_alt) != _H._normalize_for_match(page):
        _opts.append({'label': f'On {_alt}', 'subject': _alt})
    _pm_ask_hint(route='open_screen_confirm',
                 outcome='asked_open_screen', subject=page)
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': (f'Do you want this on {page} (open on your screen)'
                  + (f' or on {_alt}?' if len(_opts) > 1 else '?')),
        'followups': [o['label'] for o in _opts] + ['Something else'],
        'offer_deck': False, 'deck_angle': None,
        'memory_confirm': {'question': text, 'options': _opts},
    })


def _pm_page_brand_named(text, page_key):   # a brand on the open profile that the ask names, or '' (2026-10-13)
    return _ns_mod.page_brand_on_file(text, page_key, _H.s3_client, _H.S3_BUCKET, _H._normalize_for_match)


def _pm_short_name_identity(toks, raw_text):
    """Short-name subject identity (2026-09-23 Jenna, 'How many people
    have watched BET content in the last 12 months' quoted a research
    report instead of answering from the BET profile).

    The catalog matcher's identity-token floor (distinctive tokens
    must total >= 4 chars) keeps generic short words from binding
    wrongly, but it also made 2-5 letter subjects (BET, CNN, NFL, GAP)
    unmatchable as bases. Mirror of the hostmap initialism rule: a
    single 2-5 letter subject token IS an identity when the ask
    carries it as a standalone ALL-CAPS word. 'watched BET content'
    binds the BET profile; 'how much do people bet' stays unbound."""
    try:
        if len(toks) != 1:
            return False
        tok = str(toks[0] or '')
        if not (2 <= len(tok) <= 5) or not tok.isalpha():
            return False
        return bool(re.search(r'\b' + re.escape(tok.upper()) + r'\b',
                              str(raw_text or '')))
    except Exception:
        return False


# <<extracted: prometheus.subject_verify block 1>>


# <<extracted: prometheus.subject_verify block 2>>
# <<extracted: prometheus.subject_verify block 3>>
# <<extracted: prometheus.subject_verify block 4>>


# <<extracted: prometheus.subject_verify block 5>>


# <<extracted: prometheus.subject_verify block 6>>


# <<extracted: prometheus.subject_verify block 7>>


# <<extracted: prometheus.subject_verify block 8>>


# <<extracted: prometheus.subject_verify block 9>>


# <<extracted: prometheus.subject_verify block 10>>


# <<extracted: prometheus.subject_verify block 11>>


# <<extracted: prometheus.subject_verify block 12>>


# <<extracted: prometheus.subject_verify block 13>>


# <<extracted: prometheus.subject_verify block 14>>


# <<extracted: prometheus.subject_verify block 15>>


# <<extracted: prometheus.subject_verify block 16>>


# <<extracted: prometheus.generation_base block 1>>

# <<extracted: prometheus.generation_base block 2>>
# <<extracted: prometheus.generation_base block 3>>
_PM_DATA_FILE_PREFIX = 'generated_data/'


def _pm_csv_task_filename(entry):
    """Filename for the CSV export (2026-08-28 Jenna): name the file
    after the title of the task on screen (the active view's label,
    e.g. 'Digital Journey IQ') when the ask came from a titled data
    view; otherwise a short subject-based name. The browser saves it
    like any regular download; the chat never shows the raw link."""
    title = ''
    try:
        body = request.get_json(silent=True) or {}
        vc = (body.get('page_context') or {}).get('view_context') or {}
        title = str(vc.get('view_title') or '').strip()
    except Exception:
        title = ''
    if not title:
        try:
            subject = str(entry.get('subject') or '').strip()
            bd = entry.get('breakdown') \
                if isinstance(entry.get('breakdown'), dict) else {}
            dim = str((bd or {}).get('dimension') or '').strip()
            title = ' '.join(p for p in (subject, dim) if p)
        except Exception:
            title = ''
    try:
        import prometheus_analysis as _pma_fn
        title = _pma_fn.scrub_user_text(title) or title
    except Exception as _fx:
        _faults.note('chat', _fx)
    title = re.sub(r'[\\/:*?"<>|]+', ' ', title or '')
    title = re.sub(r'\s+', ' ', title).strip()
    if len(title) > 80:   # whole words, no half-open parenthetical (2026-11-11: '... Faith, Yo.csv')
        try:
            title = _pma_fn._clip_label(title, 80)
        except Exception:
            title = title[:80]
    title = title.strip(' .') or 'Data Export'
    return f"{title}.csv"


def _pm_answer_file_payload(entry, auto_save=False, username='',
                            question=''):
    """Build, upload, and stash the CSV for a data answer (the replay
    path; the generate pass builds inline). Returns file_link always,
    plus download_url / filename when the ask explicitly requested a
    file. Failures return {} and never block the answer."""
    import prometheus_analysis as pma
    try:
        fname, csv_text = pma.build_generated_csv(entry)
        rng = ''
        if entry.get('ws') and entry.get('we'):
            rng = (f"{_H._fmt_study_date(entry['ws'])} - "
                   f"{_H._fmt_study_date(entry['we'])}")
        elif entry.get('wl'):
            rng = str(entry['wl'])
        csv_text = _H._stamp_csv_text(csv_text, rng)
        fname = _pm_csv_task_filename(entry) or fname
        s3_key = f"{_PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
        _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=s3_key,
                             Body=csv_text.encode('utf-8'),
                             ContentType='text/csv')
        url = _H.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        _file_names.disposition(fname)},
            ExpiresIn=7 * 24 * 3600)
    except Exception:
        traceback.print_exc()
        return {}
    _pm_file_stash_write(username, url, fname, s3_key,
                         subject=entry.get('subject'),
                         question=question or entry.get('question'))
    payload = {'file_link': {'url': url, 'label': f"Download {fname}", 'name': fname, 'key': s3_key}}
    if auto_save:
        payload.update({'download_url': url, 'filename': fname})
    return payload


def _pm_replay_file(entry, text, username, chips):
    """The CSV for a read served from the library rides the answer and
    the Email chip joins the followups (2026-09-29 rule; Bria 2026-11-04:
    the download chip these paths offered had no file behind it). {} when
    the entry has no table or the upload fails; never blocks the reply."""
    try:
        if not (isinstance(entry, dict) and (entry.get('breakdown') or entry.get('metrics'))):
            return {}
        payload = _pm_answer_file_payload(
            entry, auto_save=bool(_PM_FILE_ASK_RE.search(str(text or ''))),
            username=username, question=text)
        if payload and isinstance(chips, list) and 'Email me this file' not in chips:
            chips.append('Email me this file')
        return payload or {}
    except Exception:
        traceback.print_exc()
        return {}


_PM_EMAIL_FILE_RE = re.compile(
    r"\b(?:e-?mail|mail)\b[^.?!\n]{0,50}"
    r"\b(?:csv|file|spreadsheet|data|it|this|that)\b"
    r"|\b(?:send|shoot)\b[^.?!\n]{0,30}\b(?:e-?mail|inbox)\b",
    re.I)


_PM_EMAIL_ADDR_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _pm_email_file_intent(text):
    """True when a short message asks to email the file just handed
    over. Long asks that mention email while requesting new data flow
    to the normal path."""
    t = str(text or '').strip()
    if not t or len(t) > 90:
        return False
    if t.lower().strip(' .!') == 'email me this file':
        return True
    return bool(_PM_EMAIL_FILE_RE.search(t))


_pm_email_file_intent = __import__('prometheus.insight_ask', fromlist=['not_a_send']).not_a_send(_pm_email_file_intent)   # "an insight we can email to Lego" is what the reader will do with the answer, never an order to send the last file (Jenna 2026-11-19)


def _pm_email_file_response(user, text, history=None):
    """Email the very last thing in the thread (Jenna 2026-11-04). A
    file the last reply carried goes as it is; a read with a table is
    exported from the stored entry it shipped from and attached with
    the read in the body; a reply with no table goes as the body. Sent
    by Prometheus; an address typed in the message wins, otherwise the
    account email on file."""
    from prometheus import last_thing as _lt
    _pm_ask_hint(route='email_file')
    uname = (session.get('username') or user.get('username')
             or '').strip()
    last = _pm_last_thing(user, history)
    if last['kind'] == 'none':
        pf = _pm_page_file()
        if pf:
            last.update(kind='file', file=pf, subject=pf.get('label') or '', why='the profile open on the page')
    if last['kind'] == 'none':
        _pm_ask_hint(outcome='no_file')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Nothing has landed in this chat yet for me to send. '
                      'Ask me a data question first and then say Email me this file.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    m = _PM_EMAIL_ADDR_RE.search(str(text or ''))
    addr = m.group(0) if m else ''
    if not addr:
        addr = str(user.get('email') or '').strip()
    if not addr:
        try:
            _udoc = json.loads(_H.s3_client.get_object(
                Bucket=_H.S3_BUCKET, Key=_H.S3_USERS_KEY)['Body'].read())
            _urec = (_udoc.get('users') or _udoc or {}).get(uname) or {}
            addr = str(_urec.get('email') or '').strip()
        except Exception:
            addr = ''
    if not addr:
        _pm_ask_hint(outcome='no_email')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('There is no email on file for your account. '
                      'Tell me the address to use (for example: email '
                      'it to name@company.com) and I will send it '
                      'right over.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    try:
        from prometheus import send_file as _sfm
        from prometheus import outbound_mail as _om
        _sub = str(last.get('subject') or '').strip()
        _ltxt = str(last.get('text') or '').strip()
        # the words of the read ride along, for a table or a file that a read handed over
        words = _sfm.mail_safe(_ltxt) if (last['kind'] in ('read', 'text') or len(_ltxt) > 120) else ''
        fname, fbytes, mime = '', None, 'text/csv'
        if last['kind'] == 'file':
            f = last['file']
            fname = str(f.get('filename') or 'file')
            mime = str(f.get('mime') or 'application/octet-stream')
            from prometheus import key_scope as _ks   # a link a turn or the page carries is checked for this reader first (2026-11-18)
            fbytes = _lt.file_bytes(f, _H.s3_client, bucket=_H.S3_BUCKET, extra_buckets=('svod-acquisition',), allowed=_ks.allowed_here(user, uname))
            if fbytes is None:   # nothing this reader can open sits behind that link: a plain no, never an alert
                return jsonify({'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                                'reply': 'I do not have a file to send for that. Open the profile you mean and ask again.'})
        elif last['kind'] == 'read':
            fname, csv_text = _pm_entry_csv(last['entry'], question=last.get('question'))
            fbytes = csv_text.encode('utf-8')
        if fbytes is not None:
            what = ('The file you asked for' if last['kind'] == 'file' else 'The data you asked for')
            _bl = f"{what}{' on ' + _sub if _sub else ''} is attached."
            if words:
                _bl += "\n\nThe read it came from:\n\n" + words
        else:
            _bl = (f"Here is the read you asked for{' on ' + _sub if _sub else ''}:\n\n" + words) if words \
                else f"Here is what I had for you{' on ' + _sub if _sub else ''}."
        _bl += "\n\nPrometheus\nCrosswalk"
        subj_line = (fname.rsplit('.', 1)[0].replace('_', ' ') if fname else (_sub or 'Your read from Prometheus'))
        kw = {}
        if fbytes is not None and mime == 'text/csv':
            kw = {'csv': fbytes, 'csv_name': fname}
        elif fbytes is not None:
            kw = {'files': [(fbytes, fname, mime)]}
        r = _om.send_user_email(
            to=addr, subject=subj_line, body=_bl, instructed=True,
            caller='csv-by-email', bcc_liz=False, **kw)
        if isinstance(r, dict) and r.get('sent') is False:
            raise RuntimeError(str(r.get('reason') or 'mail door closed'))
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _pm_ask_hint(outcome='error')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('The email did not go through just now. Ask me to '
                      'email it again in a minute.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    _pm_ask_hint(outcome='answered', subject=last.get('subject'))
    sent_what = fname if fname else 'the read'
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': f"Sent. {sent_what} is on its way to {addr}.",
        'followups': [], 'offer_deck': False, 'deck_angle': None})


def _pm_csv_point(subject, question, family):
    """Remember which ledger entry the CSV offer chip points at, so
    the download builds from the SAME entry the chat reply shipped
    from. Session-scoped; failures never block the reply."""
    try:
        session['pm_csv_last'] = {
            'subject': str(subject or '')[:120],
            'question': str(question or '')[:300],
            'family': str(family or '')[:32]}
    except Exception as _fx:
        _faults.note('chat', _fx)


def _pm_upload_csv(fname, csv_text):
    """Upload a CSV under generated_data/ and return (s3_key, 7-day url)."""
    fname = str(fname)   # a CSV's name is not deck copy: the door rule is the deck's alone (2026-11-11)
    fname = _file_names.safe_name(fname, 'read.csv')   # and it is one the link can carry (an emoji in it broke the link, 2026-11-16)
    s3_key = f"{_PM_DATA_FILE_PREFIX}{uuid.uuid4().hex[:12]}/{fname}"
    _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=s3_key,
                            Body=csv_text.encode('utf-8'),
                            ContentType='text/csv')
    url = _H.s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                'ResponseContentDisposition':
                    _file_names.disposition(fname)},
        ExpiresIn=7 * 24 * 3600)
    return s3_key, url


def _pm_entry_csv(entry, question=''):
    """(filename, stamped csv_text) for a stored read with a table."""
    import prometheus_analysis as pma
    fname, csv_text = pma.build_generated_csv(entry)
    _rng = ''
    try:
        if entry.get('ws') and entry.get('we'):
            _rng = (f"{_H._fmt_study_date(entry['ws'])} - "
                    f"{_H._fmt_study_date(entry['we'])}")
        elif entry.get('wl'):
            _rng = str(entry['wl'])
    except Exception:
        _rng = ''
    csv_text = _H._stamp_csv_text(csv_text, _rng)
    fname = _pm_csv_task_filename(entry) or fname
    return fname, csv_text


def _pm_page_file(bucket=''):
    """The profile open on the page, as a resolved file, or None."""
    try:
        body = request.get_json(silent=True) or {}
        prim = (body.get('page_context') or {}).get('primary') or {}
        key = str(prim.get('s3_key') or '').strip()
        if not key:
            return None
        fname = key.rsplit('/', 1)[-1]
        return {'url': '', 's3_key': key, 'filename': fname, 'bucket': bucket or _H.S3_BUCKET,
                'same_origin': False, 'ext': 'csv', 'mime': 'text/csv',
                'label': str(prim.get('name') or fname), 'page': True}
    except Exception:
        return None


def _pm_last_thing(user, history):
    """The very last thing Prometheus handed over in this thread (Jenna
    2026-11-04: "take the very last thing that happened in chat and
    that's what they want emailed, downloaded, converted"). The thread
    first; the account's last file and the session pointer only when
    the thread holds nothing."""
    from prometheus import last_thing as _lt
    import insights_ledger as il
    uname = (session.get('username') or user.get('username') or '').strip()
    stash = _pm_file_stash_read(uname)
    last = _lt.resolve(history, consult=il.consult, stash=stash, bucket=_H.S3_BUCKET)
    if last['kind'] != 'none':
        return last
    if any(str((t or {}).get('role') or '') != 'user' for t in (history or []) if isinstance(t, dict)):
        return last   # a thread with nothing delivered yet answers as such; a file from another day is not "this"
    # no thread in hand at all (an API caller): the session's pointer to
    # the read the last reply shipped from, when it has a table; a
    # text-only twin never counts
    try:
        ptr = session.get('pm_csv_last') or {}
    except Exception:
        ptr = {}
    if ptr.get('subject') and ptr.get('question'):
        try:
            led = il.consult(subject=ptr['subject'], question=ptr['question'])
            for e in [led.get('exact')] + list(reversed(led.get('entries') or [])):
                if _lt.has_data(e):
                    last.update(kind='read', entry=e, subject=str(e.get('subject') or ''),
                                question=str(ptr.get('question') or ''), why='the session pointer')
                    return last
        except Exception:
            traceback.print_exc()
    if stash.get('s3_key'):
        fname = str(stash.get('filename') or stash['s3_key'].rsplit('/', 1)[-1])
        last.update(kind='file', subject=str(stash.get('subject') or ''),
                    question=str(stash.get('question') or ''), why='the account file',
                    file={'url': str(stash.get('url') or ''), 's3_key': stash['s3_key'], 'filename': fname,
                          'bucket': _H.S3_BUCKET, 'same_origin': False,
                          'ext': fname.rsplit('.', 1)[-1].lower() if '.' in fname else '', 'mime': 'text/csv'})
    return last


def _pm_csv_download_response(user, text, history=None):
    """Hand over the very last thing in the thread as a download (Jenna
    2026-11-04). A file the last reply carried is re-linked as it is; a
    read with a table behind it is exported from the SAME stored entry
    the reply shipped from, so file and chat numbers match exactly; a
    reply with no table is turned into a table of its figures. Nothing
    in the thread yet: the profile open on the page. No charge: handing
    over what was already delivered is a formatting step."""
    from prometheus import last_thing as _lt
    _pm_ask_hint(route='csv_download')
    uname = (session.get('username') or user.get('username') or '').strip()
    last = _pm_last_thing(user, history)
    if last['kind'] == 'none':
        pf = _pm_page_file()
        if pf:
            last.update(kind='file', file=pf, subject=pf.get('label') or '', why='the profile open on the page')
    if last['kind'] == 'none':
        _pm_ask_hint(outcome='declined_no_csv_source')
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': ('Nothing has landed in this chat yet for me to hand over. '
                      'Ask me for the data and the file will ride the answer.'),
            'followups': [], 'offer_deck': False, 'deck_angle': None})
    try:
        if last['kind'] == 'file':
            # the file as it is, on a link that opens now (a week-old
            # presigned link is re-signed; a same-origin link stays)
            f = last['file']
            fname = str(f.get('filename') or 'file')
            from prometheus import key_scope as _ks   # only a file this reader can open is signed (2026-11-18)
            url = _lt.fresh_url(f, _H.s3_client, bucket=_H.S3_BUCKET, allowed=_ks.allowed_here(user, uname))
            s3_key = str(f.get('s3_key') or '')
        elif last['kind'] == 'read':
            fname, csv_text = _pm_entry_csv(last['entry'], question=last.get('question'))
            s3_key, url = _pm_upload_csv(fname, csv_text)
        else:
            fname, csv_text = _lt.text_csv(last['text'], subject=last.get('subject'),
                                           question=last.get('question'))
            csv_text = _H._stamp_csv_text(csv_text)
            s3_key, url = _pm_upload_csv(fname, csv_text)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    if not url:   # nothing this reader can open sits behind that link (file_access, 2026-11-18)
        return jsonify({'success': True, 'action': 'answer', 'followups': [], 'offer_deck': False, 'deck_angle': None,
                        'reply': 'I do not have a file to hand over for that. Open the profile you mean and ask again.'})
    if s3_key and not (last['kind'] == 'file' and last['file'].get('page')):
        _pm_file_stash_write(uname, url, fname, s3_key,
                             subject=last.get('subject'), question=last.get('question') or text)
    _pm_ask_hint(outcome='answered', subject=last.get('subject'))
    reply = f"Saved. {fname} is in your browser downloads."
    return jsonify({
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': ['Email me this file'], 'offer_deck': False, 'deck_angle': None,
        'download_url': url, 'filename': fname,
        'file_link': {'url': url, 'label': f"Download {fname}"}})


def _pm_search_demand_response(user, text, history):
    """Search-journey demand read (2026-08-26 Jenna directive): how
    people find, search for, and first touch a title or brand. Shaped
    on the Normal (Bob Odenkirk) HBO Max study: first-touch splits,
    rival hunt, destination share, interest clusters. Needs no page
    context; the subject comes from the question itself. Every count
    passes the server-side coherence pass (messy last digits, sub-counts
    sum exactly to parents) and the vocabulary scrub before it ships.

    Consistency (2026-08-26): the insights ledger is consulted first.
    A repeat of a stored ask replays the stored reply verbatim; prior
    published numbers for the same subject ride the prompt as binding
    constraints; every delivered study persists back to the ledger."""
    import prometheus_analysis as pma
    import insights_ledger as il
    _pm_ask_hint(route='search_demand')
    _pm_user = (session.get('username') or user.get('username') or '').strip()
    _pm_ppu = _pm_usage_extras(user)
    # Prometheus asks are session-metered (2026-09-09 Jenna). No
    # per-pull credit gate on this route: pay-per-use accounts still
    # bill via the session close, subscribed accounts are covered by
    # their tier. Real pipeline pulls (Profile IQ, Subscriber IQ)
    # remain credit-gated elsewhere.
    led = {'block': '', 'exact': None, 'entries': []}
    _t_ledger = time.monotonic()
    try:
        led = il.consult(question=text)
    except Exception:
        traceback.print_exc()
    _pm_ask_stage('ledger', t0=_t_ledger)
    exact = led.get('exact')
    if exact and exact.get('route') == 'search_demand' \
            and exact.get('reply'):
        _pm_ask_hint(outcome='answered', subject=led.get('subject'))
        # Served from the library: metered, never free (2026-09-14).
        _pm_meter_answer('replay_search', _pm_ppu)
        _replay_chips = [c for c in list(exact.get('followups') or [])[:3]
                         if c != pma.CSV_OFFER_CHIP]
        _pm_csv_point(led.get('subject'), text, 'search')
        _rp_file = _pm_replay_file(exact, text, _pm_user, _replay_chips)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': exact['reply'],
            'followups': _replay_chips,
            'offer_deck': False, 'deck_angle': None,
            'profile': led.get('subject'),
            **_rp_file})
    _sd_led_block = led.get('block') or ''
    try:
        _cat_block = _pm_catalog_block(led.get('subject')
                                       or pma.guess_subject_from_text(text))
        if _cat_block:
            _sd_led_block = (_cat_block + '\n' + _sd_led_block) if _sd_led_block else _cat_block
    except Exception:
        traceback.print_exc()
    user_prompt = pma.build_search_demand_user_prompt(
        text, history, ledger_block=_sd_led_block)
    try:
        import prometheus_knowledge as _pmk
        _kb = _pmk.knowledge_block('search_demand', text=text,
                                   s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
        if _kb:
            user_prompt = f"{user_prompt}\n\n{_kb}"
    except Exception as _fx:
        _faults.note('chat', _fx)
    _t_model = time.monotonic()
    # The window is the title's real premiere, looked up when the model is
    # not sure of it (Cara Dorfman 2026-11-10: Off Campus, released on Prime
    # Video May 13 2026, was read on an invented Oct 2 to Oct 18 2026
    # window). Research is additive: the tool plan falls back to no tools.
    result = _pm_claude_json(pma.SEARCH_DEMAND_SYSTEM_PROMPT, user_prompt,
                             max_tokens=7500, temperature=0.4,
                             usage_extras=_pm_ppu, tools=[pma.WEB_SEARCH_TOOL])
    _pm_ask_stage('model', t0=_t_model)
    if not result.get('success'):
        _H._chatbot_error_email(
            'brief-chat/analyze',
            'search-demand model call failed: '
            + str(result.get('error') or 'unknown')[:400],
            tb='(model chain exhausted without a usable reply)')
        _pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    data = result.get('data') or {}
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), {})
    if str(data.get('action') or '').strip().lower() == 'clarify':
        q = pma.scrub_user_text(
            str(data.get('clarify_question') or '').strip()
            or 'Which title or brand should I read the search demand '
               'for, and on which platform?')
        opts = [pma.scrub_user_text(str(o).strip())[:160]
                for o in (data.get('clarify_options') or [])
                if str(o).strip()][:4]
        _pm_ask_hint(outcome='clarify')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': q,
            'followups': opts, 'offer_deck': False, 'deck_angle': None})
    try:
        study = pma.enforce_demand_coherence(data)
        reply = pma.format_search_demand_reply(study)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/analyze', e)
        _pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    # Base-profile boundary (2026-08-27, Jenna): a generated search
    # study only ships for a subject already pulled somewhere (profile
    # catalog or a Subscriber IQ run). No base = steer to the build,
    # no numbers.
    _sd_base = None
    try:
        _sd_base = _pm_generation_base(study.get('subject'), text)
    except Exception:
        traceback.print_exc()
    if not _sd_base:
        _sd_subj = str(study.get('subject')
                       or pma.guess_subject_from_text(text) or '').strip()
        _sd_user = (session.get('username') or user.get('username')
                    or '').strip()
        if _sd_subj:
            _sd_label = _pm_panel_price_label(_sd_user)[0]
            _b_reply, _b_chips, _b_offer = pma.build_panel_report_offer(
                _sd_subj, _sd_label, question=text)
            _pm_ask_hint(outcome='panel_offer', subject=_sd_subj)
            return jsonify({
                'success': True, 'action': 'answer', 'reply': _b_reply,
                'followups': _b_chips, 'offer_deck': False,
                'deck_angle': None, 'panel_offer': _b_offer})
        _b_reply, _b_chips = pma.build_profile_required_reply(
            '', price_label=_pm_panel_price_label(_sd_user)[0])
        _pm_ask_hint(outcome='clarify_subject')
        return jsonify({
            'success': True, 'action': 'answer', 'reply': _b_reply,
            'followups': _b_chips, 'offer_deck': False,
            'deck_angle': None})
    if not reply.strip():
        _H._chatbot_error_email('brief-chat/analyze',
                             'search-demand study rendered empty',
                             tb='(coherence pass left no sections)')
        _pm_ask_hint(outcome='error')
        return jsonify(_H._chatbot_calm_payload())
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip() and str(f).strip() != pma.CSV_OFFER_CHIP][:3]
    _sd_metrics = il.metrics_from_study(study)
    try:
        _sd_universe = (f"US people who searched for {study.get('subject')}"
                        + (f" on {study.get('platform')}" if study.get('platform') else '') + " in the window")
        il.persist(
            subject=study.get('subject'), metric_family='search',
            question=text, route='search_demand',
            metrics=il.attach_sample_people(_sd_metrics, universe=_sd_universe),
            universe=_sd_universe,
            anchors=[b for b in (
                f"platform: {study.get('platform')}"
                if study.get('platform') else None,
                f"rival: {study.get('rival')}"
                if study.get('rival') else None) if b],
            window_start=study.get('window_start'),
            window_end=study.get('window_end'),
            window_label=study.get('window_label'),
            reply=reply, followups=followups,
            base_profile_key=_sd_base.get('s3_key'))
    except Exception:
        traceback.print_exc()
    _pm_csv_point(study.get('subject'), text, 'search')
    _sd_file = _pm_replay_file(
        {'subject': study.get('subject'), 'metrics': _sd_metrics, 'question': text,
         'ws': study.get('window_start'), 'we': study.get('window_end'), 'wl': study.get('window_label')},
        text, _pm_user, followups)
    _pm_ask_hint(outcome='answered', subject=study.get('subject'))
    return jsonify({
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': study.get('subject'),
        **_sd_file})


# <<extracted: prometheus.ask_memory block 1>>


# <<extracted: prometheus.ask_memory block 2>>


# <<extracted: prometheus.ask_memory block 3>>


# <<extracted: prometheus.ask_memory block 4>>


# <<extracted: prometheus.ask_memory block 5>>


# <<extracted: prometheus.ask_memory block 6>>


# <<extracted: prometheus.generated_read block 1>>


# <<extracted: prometheus.ask_memory block 7>>


# <<extracted: prometheus.generated_read block 2>>


# <<extracted: prometheus.ask_memory block 8>>


# <<extracted: prometheus.ask_memory block 9>>


# <<extracted: prometheus.read_jobs block 1>>


# <<extracted: prometheus.read_jobs block 2>>


# <<extracted: prometheus.thread_history block 17>>


# <<extracted: prometheus.thread_history block 18>>


# <<extracted: prometheus.thread_history block 19>>


# <<extracted: prometheus.thread_history block 20>>


# <<extracted: prometheus.thread_history block 21>>


# <<extracted: prometheus.thread_history block 22>>


# <<extracted: prometheus.thread_history block 23>>


# <<extracted: prometheus.thread_history block 24>>


# <<extracted: prometheus.read_jobs block 3>>


# <<extracted: prometheus.read_jobs block 4>>


# <<extracted: prometheus.read_jobs block 5>>


# <<extracted: prometheus.read_jobs block 6>>


# <<extracted: prometheus.read_jobs block 7>>


# <<extracted: prometheus.read_jobs block 8>>


# <<extracted: prometheus.read_jobs block 9>>


# <<extracted: prometheus.read_jobs block 10>>


# <<extracted: prometheus.read_jobs block 11>>


# <<extracted: prometheus.read_jobs block 12>>


# <<extracted: prometheus.read_jobs block 13>>


# <<extracted: prometheus.read_jobs block 14>>


# <<extracted: prometheus.read_jobs block 15>>


# <<extracted: prometheus.read_jobs block 16>>


# <<extracted: prometheus.read_jobs block 17>>


# <<extracted: prometheus.read_jobs block 18>>


# <<extracted: prometheus.read_jobs block 19>>


# <<extracted: prometheus.read_jobs block 20>>


# <<extracted: prometheus.read_jobs block 21>>


# <<extracted: prometheus.read_jobs block 22>>


# <<extracted: prometheus.read_jobs block 23>>


@_H.app.route('/api/brief-chat/analyze', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/analyze')
@_ask_logged('analyze')
def api_synth_chat_analyze():
    """Analyze the data open on the dashboard (primary profile + any
    checked Data Cuts). First-party digest is the primary evidence.

    Session-authenticated dashboard users only."""
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): analyze is the primary
    # read surface; 'pull'-only users are blocked here. Runs before
    # the JSON body parse so an empty pull-only request also gets the
    # friendly refusal instead of the calm-fallback line.
    if not _pm_gate_analyze(user):
        return _pm_gate_refusal('analyze')
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
    return _pm_analyze_core(user, body, text, history)


# <<extracted: core_analyze block 1>>


_PM_DECK_FILE_PREFIX = 'generated_decks/'


# ---------------------------------------------------------------------------
# Brand Partnership Valuation via Prometheus (Jenna 2026-09-16): the
# "Pull Brand Partnership Valuation" chip collects the partnership
# inputs in chat, confirms, charges the standard Brand Partnership IQ
# pull, and runs the research + reasoning build on a background thread.
# The finished read lands in brand-partnership-iq/ and renders in the
# dashboard's Brand Partnership tab like every other valuation.
# ---------------------------------------------------------------------------
_PM_BPIQ_JOB_PREFIX = 'system/pm_bpiq_jobs/'


_PM_BPIQ_CHIP = 'Pull Brand Partnership Valuation'


_PM_BPIQ_CREDITS = 15


_PM_BPIQ_ASK_COPY = _intake_draft.BPIQ_ASK_COPY  # text lives in prometheus/intake_draft.py


def _pm_read_intent(body, intent, min_conf=0.7):
    return _intents.read_intent(body, intent, min_conf)


def _pm_bpiq_intent(text):
    return _intents.bpiq_intent(text, _PM_BPIQ_CHIP)


def _pm_bpiq_parse(text, usage_extras=None):
    """One small model call: free text -> the valuation inputs."""
    from migration.bpiq_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='bpiq_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


def _pm_bpiq_flight_research(brand, campaign, qualifier=''):
    """One web-search call dating a brand's named initiative (prometheus.bpiq_window)."""
    from prometheus import bpiq_window as _bw
    return _bw.flight_research(brand, campaign, qualifier, claude_data=_pm_claude_data)


def _pm_bpiq_inputs_complete(parsed):
    return bool(parsed.get('brand_partner') and parsed.get('qualifier')
                and parsed.get('event_start')
                and parsed.get('event_end'))


def _pm_bpiq_confirm_reply(parsed):
    ev = f"{parsed.get('event_start')} to {parsed.get('event_end')}"
    pre = (f"{parsed['pre_start']} to {parsed['pre_end']}"
           if parsed.get('pre_start') and parsed.get('pre_end')
           else 'the year before the campaign')
    post = (f"{parsed['post_start']} to {parsed['post_end']}"
            if parsed.get('post_start') and parsed.get('post_end')
            else 'campaign end through today')
    aud = parsed.get('audience') or 'the partner audience'
    from prometheus import bpiq_window as _bw
    return (
        f"Here's the valuation I'll run:\n"
        f"- {parsed['qualifier']} x {parsed['brand_partner']}\n"
        f"{_bw.context_lines(parsed)}"
        f"- Campaign window: {ev}\n"
        f"- Baseline window: {pre}\n"
        f"- Post window: {post}\n"
        f"- Audience: {aud}\n\n"
        f"It prices at $500 without a control group, or $1,000 "
        f"with one, and lands in the Brand Partnership tab when "
        f"finished.")


def _pm_run_bpiq_job(job_id, username, inputs, extras):
    """Background build: research + reason the partnership read, write
    the payload, register the metadata sidecar, and open access for
    the requesting user. Mirrors the deck job's thread pattern.

    One run per ask (2026-10-28): the run registry is checked before
    anything is built; a key another job owns is followed, never
    rebuilt. The campaign research and the primitives persist per key
    so a repeat reads them back."""
    reg = None
    try:
        from migration.bpiq_synthesis import synthesize, s3_key_for, S3Underpinnings
        from prometheus import bpiq_job as _bj
        # one run per ask (2026-10-28): known casing, the key claimed, a
        # key another job owns followed to its file instead of rebuilt
        inputs, reg, _settled = _bj.prepare(job_id, username, inputs, extras, _H, _pm_bpiq_status_write)
        if _settled:
            return
        _pm_bpiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': f"{inputs['qualifier']} x "
                       f"{inputs['brand_partner']}",
            'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_b
            tools = [_pma_b.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cj(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _pm_claude_data(system, user_prompt, **kw)

        payload = synthesize(inputs, _cj, tools=tools,
                             created_by=username or 'prometheus',
                             underpinnings=S3Underpinnings(_H.s3_client, _H.S3_BUCKET),
                             job_id=job_id)
        category = payload.pop('_bpiq_category', None)
        out_key = s3_key_for(inputs)
        _H.s3_client.put_object(
            Bucket=_H.S3_BUCKET, Key=out_key,
            Body=json.dumps(payload, indent=2).encode('utf-8'),
            ContentType='application/json')
        # Corpus catalog (2026-10-05): the read's audience and value
        # figures join the shared subject record as they land.
        try:
            from migration import corpus_catalog as _cc_b
            _cc_b.index_bpiq(out_key, payload, user=username or '')
        except Exception as _cc_err:
            print(f"[bpiq-job {job_id}] corpus catalog hook failed: {_cc_err}")
        bare_key = out_key.replace('brand-partnership-iq/', '')
        # Metadata sidecar: title + category so the tab and the admin
        # CMS render it immediately, plus the thumbnail every hand-built
        # read carries (2026-10-05, Willow Smith x Free People shipped
        # without a photo).
        _img_url = None
        try:
            from prometheus.bpiq_image import resolve_bpiq_image
            _img_url, _img_src = resolve_bpiq_image(_H, inputs)
            print(f"[bpiq-job {job_id}] image {_img_src}: {_img_url}")
        except Exception as img_err:
            print(f"[bpiq-job {job_id}] image lookup failed "
                  f"(non-fatal): {img_err}")
        try:
            meta = _H.load_bpiq_metadata()
            _prev = meta.get(bare_key) or {}
            meta[bare_key] = {
                **_prev,
                'display_name': payload.get('project_name') or bare_key,
                'category': (category or 'ENTERTAINMENT'),
            }
            if _img_url and not _prev.get('image_url'):
                meta[bare_key]['image_url'] = _img_url
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=_H.BPIQ_METADATA_KEY,
                Body=json.dumps(meta, indent=2).encode('utf-8'),
                ContentType='application/json')
        except Exception as meta_err:
            print(f"[bpiq-job {job_id}] metadata sidecar failed "
                  f"(non-fatal): {meta_err}")
        # Company wallet owns the pull so teammates see it. Crosswalk
        # sees every pull; other companies never inherit the library.
        try:
            _H._stamp_bpiq_ownership(out_key, username)
        except Exception as acc_err:
            print(f"[bpiq-job {job_id}] access grant skipped: {acc_err}")
        _pm_bpiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': payload.get('project_name'),
            's3_key': bare_key,
            'total_brand_value': (payload.get('valuation') or {})
            .get('total_brand_value'),
            'finished_at': time.time(), **_bj.hand_over(job_id, username, inputs, bare_key, payload, category, _H)})   # the valuation deck, one builder (2026-11-11)
        print(f"[bpiq-job {job_id}] done -> {out_key}")
        _bj.settle(reg, inputs, job_id, username, bare_key, payload)   # registry closes; the thread gets its note
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _val = payload.get('valuation') or {}
            _tbv = _val.get('total_brand_value')
            _em_lead = ''
            try:
                _em_subj, _em_body = _bpiq_wiz.email_copy({
                    'talent': inputs.get('qualifier'),
                    'campaign': (inputs.get('campaign')
                                 or inputs.get('brand_partner')),
                    'qualifier': inputs.get('qualifier'),
                    'brand_partner': inputs.get('brand_partner'),
                })
                _em_lead = (_em_body.split('\n\n') or [''])[0]
            except Exception:
                _em_lead = ''
            _on_done('bpiq', username=username, subject=str(payload.get('project_name') or ''), s3_key=bare_key, payload=payload,
                     host=_H, s3_client=_H.s3_client, job_id=job_id, files=_bj.deck_files(job_id),
                     ready_message=_em_lead, intro=_em_lead,
                     figures=([{'name': 'Total brand value', 'value': _tbv, 'unit': 'usd', 'definition': 'Brand Partnership IQ valuation'}] if _tbv else []),
                     facts=([('Total brand value', f"${float(_tbv):,.2f}")] if _tbv else []))
        except Exception as _dl_err:
            print(f"[bpiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _pm_bpiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        try:
            from prometheus import bpiq_job as _bj_f
            _bj_f.fail(reg, inputs, job_id, e)
        except Exception:
            pass
        _pm_engine_failure_alert(
            'brief-chat/bpiq-job', e, job_id, username, inputs, 'the valuation error line in the widget',
            f"Brand Partnership: {(inputs or {}).get('brand') or (inputs or {}).get('subject') or ''} x {(inputs or {}).get('partner') or (inputs or {}).get('brand_partner') or ''}")


# ---------------------------------------------------------------------------
# Digital Journey via Prometheus (Jenna 2026-09-16): the "Pull a
# Digital Journey" chip walks the user through the playbook inputs
# (subject, platform, conversion event, window, TAM), charges the
# $500 Digital Journey pull, and builds the research-anchored journey
# on a background thread. The finished run lands in the Journey IQ
# store and renders in the Digital Journey tab exactly like the Luxury
# Fragrance on TikTok Shop read.
# ---------------------------------------------------------------------------
_PM_JIQ_JOB_PREFIX = 'system/pm_jiq_jobs/'


_PM_JIQ_CHIP = 'Pull a Digital Journey'


_PM_JIQ_CREDITS = 15


_PM_JIQ_ASK_COPY = _intake_draft.JIQ_ASK_COPY  # text lives in prometheus/intake_draft.py


def _pm_jiq_intent(text):
    return _intents.jiq_intent(text, _PM_JIQ_CHIP)


def _pm_jiq_parse(text, usage_extras=None):
    from migration.journey_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='jiq_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


def _pm_jiq_inputs_complete(parsed):
    from prometheus.intake_reader import journey_inputs_complete
    return journey_inputs_complete(parsed)   # two directions (Jenna 2026-10-19): after-watch needs the title only; unclear is asked


def _pm_jiq_is_ticketing(inputs):
    try:
        from migration.journey_synthesis import is_ticketing_journey
        return bool(is_ticketing_journey(inputs or {}))
    except Exception:
        return False


def _pm_jiq_ready_message(payload, inputs):
    """The ready notice is written here, not in the widget (2026-10-05
    Jenna: Prometheus's words live with the API). Names the count by
    what it is: ticketing-site visitors, viewers, or buyers, always
    people in the US, and carries the no-purchase line on a film."""
    subj = str((payload.get('meta') or {}).get('project_name')
               or inputs.get('subject') or 'the journey')
    n = (payload.get('kpis') or {}).get('total_users')
    try:
        n_txt = f"{int(n):,}"
    except Exception:
        n_txt = ''
    ticketing = _pm_jiq_is_ticketing(inputs) or bool(
        (payload.get('meta') or {}).get('no_purchase_claim'))
    kind = str(inputs.get('journey_kind') or '').lower()
    msg = (f"Your Digital Journey is ready: {subj} is live in the "
           f"Digital Journey tab now.")
    if n_txt and ticketing:
        msg += (f" Ticketing-site visitors in window: {n_txt} people in "
                "the US. No claim is made on whether any of them bought "
                "a ticket; Crosswalk does not predict box office.")
    elif n_txt and kind in ('watch', 'media_week'):
        msg += f" Viewers in window: {n_txt} people in the US."
    elif n_txt:
        msg += f" Buyers in window: {n_txt} people in the US."
    return msg


def _pm_jiq_confirm_reply(parsed):
    win = (f"{parsed['start_date']} to {parsed['end_date']}"
           if parsed.get('start_date') and parsed.get('end_date')
           else 'trailing 12 months')
    if str(parsed.get('journey_kind') or '') == 'media_week':   # what watchers do after and in addition (Jenna 2026-10-19)
        from prometheus.intake_reader import media_week_confirm_reply
        return media_week_confirm_reply(parsed, win, _pm_tool_price_label('journey_iq', '$500'),
                                        price_sentence=_pm_price_sentence('journey_iq', '$500'))
    tam = parsed.get('tam_label') or 'US gen pop (329.9M)'
    start_line = (f"- Starting point: {parsed['start_behavior']} "
                  f"(out of {tam})"
                  if parsed.get('start_behavior')
                  else f"- Starting universe: {tam}")
    end_step = parsed['conversion_event']
    if _pm_jiq_is_ticketing(parsed):
        # Movie tickets (2026-10-05 Jenna): the furthest point the read
        # sees is the ticketing site. Say so at the confirm, before the
        # run, so the end step is never a purchase in the reader's mind
        # (Alexia's first ask was "purchased a digital movie ticket").
        parsed['journey_kind'] = 'ticketing'
        parsed['no_purchase_claim'] = True
        end_step = ("Went to the ticketing site for a ticket. This is "
                    "the furthest point we see; it makes no claim on "
                    "what happened after the ticketing site, and "
                    "Crosswalk does not predict box office")
        parsed['conversion_event'] = (
            "Went to a ticketing site or app for a ticket to "
            f"{parsed['subject']} (no purchase claim)")
        shape = ("It's a full discovery-to-ticketing-site path - where "
                 "they see the campaign, act on it, look the film up, "
                 "look up showtimes, and reach the ticketing site")
    elif str(parsed.get('journey_kind') or '') == 'before_after':
        clip = parsed.get('clip_url') or 'this clip'
        shape = ("It's a 20-minute before-and-after around the clip "
                 f"({clip}) - last surface before they opened it, "
                 "first surface after, then research and action in "
                 "the rest of the window")
    elif str(parsed.get('journey_kind') or '') == 'discovery_existing':
        shape = ("It's new-to-the-platform vs already on it - where "
                 "each group arrived, what they opened first, and "
                 "who stayed")
    elif str(parsed.get('journey_kind') or '') == 'music':
        shape = ("It's a music-first path into the title - the song, "
                 "the sound page, the title page, and the watch")
    elif str(parsed.get('journey_kind') or '') == 'watch':
        # the Babylon 5 shape, every watch journey (Jenna 2026-10-14); the saw-then-watched read when the brief carries it (Carolyn Bisson 2026-10-22)
        from prometheus.intake_reader import watch_definition_line
        shape = watch_definition_line(parsed)
    else:
        # the same depth as a watch journey, plus why the cart leavers
        # left (Jenna 2026-10-16, Ghost Energy)
        shape = ("It's a full discovery-to-purchase path - where they learn the name, research, compare, hunt a code, "
                 "cart and leave, get retargeted, and pay - with the first content they saw and where it sat, how many "
                 "posts before they looked it up, where they looked before the cart, the time to the order, what they "
                 "bought first, the last touch and the assists, and for everyone who left the cart, what they did next "
                 "and why: coupon hunting, price checks, safety searches, reviews, competitors")
    clip_line = (f"- Clip: {parsed['clip_url']}\n"
                 if parsed.get('clip_url') else '')
    return (
        f"Here's the Digital Journey I'll build:\n"
        f"- {parsed['subject']} on {parsed['platform']}\n"
        f"- End step: {end_step}\n"
        f"{clip_line}"
        f"- Window: {win}\n"
        f"{start_line}\n\n"
        f"{shape}. Every journey carries Pathways, One viewer and "
        f"Clickstream sections: the routes people took, one person's "
        f"walk step by step, and the public URLs on each step. It lands "
        f"in the Digital Journey tab when finished. "
        f"{_pm_price_sentence('journey_iq', '$500')} Run it?")


def _pm_run_jiq_job(job_id, username, inputs, extras):
    try:
        from migration.journey_synthesis import synthesize, persist
        subj = (f"{inputs['subject']} viewers, a week in the life" if str(inputs.get('journey_kind') or '') == 'media_week'
                else f"{inputs['subject']} on {inputs.get('platform') or 'the platform'}")
        _pm_jiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': subj, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_j
            tools = [_pma_j.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cj(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _pm_claude_data(system, user_prompt, **kw)

        payload = synthesize(inputs, _cj, tools=tools,
                             created_by=username or 'prometheus')
        if str((_pm_s3_json(f"{_PM_JIQ_JOB_PREFIX}{job_id}.json", {}) or {}).get('status') or '') == 'cancelled':
            print(f"[jiq-job {job_id}] cancelled while building; nothing persisted"); return   # Jenna 2026-10-16: stop it
        # Hero image (2026-10-05): the tab shows the title's poster or
        # the brand's image instead of initials. Best-effort, never
        # blocks the persist.
        try:
            from prometheus.jiq_image import resolve_jiq_image, attach_hero
            _img_url, _img_src = resolve_jiq_image(_H, inputs)
            if _img_url:
                attach_hero(payload, _img_url)
            print(f"[jiq-job {job_id}] hero image: {_img_src}")
        except Exception as _img_err:
            print(f"[jiq-job {job_id}] hero image skipped: {_img_err}")
        out_key = persist(_H.s3_client, payload, username or 'prometheus',
                          job_id)
        # Access: the requester must see their own run. Default-open
        # ('*' / unset) users already do; explicit-list users get the
        # key appended; users without the product get a scoped grant.
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                cur = u.get('allowed_journey_iq_runs')
                changed = False
                if isinstance(cur, list) and '*' not in cur \
                        and out_key not in cur:
                    u['allowed_journey_iq_runs'] = cur + [out_key]
                    changed = True
                if not u.get('has_journey_iq_access'):
                    u['has_journey_iq_access'] = True
                    changed = True
                return data if changed else None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[jiq-job {job_id}] access grant skipped: {acc_err}")
        _pm_jiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': payload['meta']['project_name'],
            's3_key': out_key,
            'conversions': (payload.get('kpis') or {}).get('total_users'),
            'count_noun': ('ticketing-site visitors'
                           if (payload.get('meta') or {}).get('no_purchase_claim')
                           else None),
            'ready_message': _pm_jiq_ready_message(payload, inputs),
            'finished_at': time.time()})
        print(f"[jiq-job {job_id}] done -> {out_key}")
        # Done means the user has it (Jenna 2026-10-13): ledger, email with the
        # breakdown attached, delivery record, shelf. One contract, every product.
        try:
            from prometheus.delivered import on_done as _on_done
            from migration.journey_iq import shelf_for as _shelf_for, SHELF_LABELS as _SHELF_LABELS
            _on_done('journey', username=username, subject=str(payload['meta'].get('project_name') or subj), s3_key=out_key,
                     payload=payload, host=_H, s3_client=_H.s3_client, job_id=job_id,
                     shelf=_SHELF_LABELS.get(_shelf_for(payload.get('meta') or {}), ''),
                     ready_message=_pm_jiq_ready_message(payload, inputs))
        except Exception as _dl_err:
            print(f"[jiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _pm_jiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _pm_engine_failure_alert(
            'brief-chat/jiq-job', e, job_id, username, inputs, 'the journey error line in the widget',
            f"Digital Journey: {(inputs or {}).get('subject') or ''} on {(inputs or {}).get('platform') or ''}")


# --- Build a Flywheel through Prometheus (2026-09-17, Jenna) ----------------
# Method: the revised acquired/reactivated playbook. The nest starts
# at the captured users (the people who did the thing the ask wants
# to capture) - never at US gen pop.
def _pm_price_sentence(tool_key, fallback, username=None):
    """The price sentence a confirm card prints: 'It prices at $500.' for a
    paying seat; 'Included with your plan.' with no price for an unlimited
    or staff seat (the 2026-11-08 rule, priced_read.included_with_plan, the
    one check every priced surface asks; the journey card printed $500 to
    Jenna's unlimited seat on 2026-11-10). Never raises."""
    try:
        # the reader named, else the session's, else the one the ask door stamped (a Prometheus app user has no
        # session); never a field of the request body (prometheus.ask_outcome.seat_username, 2026-11-17)
        uname = __import__('prometheus.ask_outcome', fromlist=['seat_username']).seat_username(username)
        if uname:
            data = _H.load_users()
            user = (data.get('users') or {}).get(uname) or {}
            from prometheus import priced_read as _pr
            if user and _pr.included_with_plan(user, uname, _H):
                return _pr.INCLUDED_LINE
    except Exception as _fx:
        _faults.note('chat', _fx)
    return f"It prices at {_pm_tool_price_label(tool_key, fallback, username)}."


def _pm_included_seat(username=None):
    """True when priced reads are included with this seat's plan (unlimited or staff)."""
    try:
        return _pm_price_sentence('journey_iq', '$500', username) == __import__('prometheus.priced_read', fromlist=['INCLUDED_LINE']).INCLUDED_LINE
    except Exception:
        return False


def _pm_tool_price_label(tool_key, fallback, username=None):
    """Live per-pull price from the billing panel (system/pricing.json
    via wallet.tool_price_usd), so chat copy never drifts from what
    admins set. Pass username so a company sticker (Kartel $925 BPIQ)
    prints the number that will be charged. Falls back to the last
    known label on any failure."""
    try:
        import wallet as _w
        subject = None
        # the reader named, else the session's, else the one the ask door stamped (a Prometheus app user has no
        # session); never a field of the request body (prometheus.ask_outcome.seat_username, 2026-11-17)
        uname = __import__('prometheus.ask_outcome', fromlist=['seat_username']).seat_username(username)
        if uname:
            try:
                data = _H.load_users()
                user = (data.get('users') or {}).get(uname) or {}
                if user:
                    subject, _k, _n = _w.resolve_billing_subject(
                        user, data)
            except Exception:
                subject = None
        v = float(_w.tool_price_usd(tool_key, subject=subject) or 0)
        if v > 0:
            return _pm_usd_label(v, uname or None)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return fallback


_PM_FW_JOB_PREFIX = 'system/pm_fw_jobs/'


_PM_FW_CHIP = 'Build a Flywheel'


_PM_FW_CREDITS = 5


_PM_FW_ASK_COPY = (
    "Happy to build a Flywheel. Give me, in one message:\n"
    "1. The title or brand the event is about (e.g. Gilmore Girls)\n"
    "2. The captured action that STARTS the file - the thing the "
    "users did, in one sentence (e.g. acquired or reactivated Prime "
    "after a first Gilmore Girls play following 180 days off). The "
    "flywheel always starts at those users, never the whole "
    "country. If you already have the count, give it and I lock it.\n"
    "3. The owned ecosystem the flywheel lives on (e.g. Amazon-owned "
    "surfaces, the TikTok Shop ecosystem)\n"
    "4. The conversion inside that ecosystem (e.g. an Amazon-owned "
    "checkout inside 30 days of the play)\n"
    "Optional: a two-way split (new vs reactivated), the before / "
    "after windows (default: matched, 30 days each - a long before "
    "against a short after is a clock artifact, not a compare), and "
    "the overall window (default: trailing 12 months).\n\n"
    "Example: \"Gilmore Girls, start from the 22,764 who acquired or "
    "reactivated Prime after their first play, Amazon-owned "
    "ecosystem, conversion is an Amazon-owned checkout inside 30 "
    "days, split new vs reactivated\"")


def _pm_fw_intent(text):
    return _intents.fw_intent(text, _PM_FW_CHIP)


def _pm_fw_parse(text, usage_extras=None):
    from migration.flywheel_synthesis import PARSE_SYSTEM_PROMPT
    parsed = _pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=1200, temperature=0.0,
                             surface='fw_parse',
                             usage_extras=usage_extras)
    return parsed if isinstance(parsed, dict) else {}


def _pm_fw_inputs_complete(parsed):
    return bool(parsed.get('subject') and parsed.get('captured_action')
                and parsed.get('ecosystem')
                and parsed.get('conversion_event'))


def _pm_fw_confirm_reply(parsed):
    win = (f"{parsed['start_date']} to {parsed['end_date']}"
           if parsed.get('start_date') and parsed.get('end_date')
           else 'trailing 12 months')
    lock = (f" (locked at {int(parsed['cohort_count']):,} accounts)"
            if parsed.get('cohort_count') else '')
    split = (f"\n- Split: {parsed['splits_hint']}"
             if parsed.get('splits_hint') else '')
    pre = int(parsed.get('pre_days') or 30)
    post = int(parsed.get('post_days') or pre)
    return (
        f"Here's the Flywheel I'll build:\n"
        f"- {parsed['subject']} on {parsed['ecosystem']} surfaces\n"
        f"- Starting point: {parsed['captured_action']}{lock}\n"
        f"- Conversion: {parsed['conversion_event']}\n"
        f"- Windows: {pre} days before vs {post} days after the "
        f"event (matched), inside {win}{split}\n\n"
        f"It starts at those captured users (never the whole "
        f"country) and shows three things: who they are, every "
        f"owned touch point before the event against after it, and "
        f"what the converters bought. It lands on the Flywheel IQ "
        f"page when finished. It prices at "
        f"{_pm_tool_price_label('flywheel_iq', '$500')}. Run it?")


def _pm_engine_failure_alert(route, err, job_id, username, inputs, told_user, what):
    """The ops alert for an engine job that failed on the worker (2026-11-03). The worker has no request, so
    the alert used to read "User: (unknown) / Question: (no question captured)" and the three Gilmore Girls
    flywheel failures (Grace Yin on the jaypatel14 seat) went to Jenna and Jessie looking like anonymous
    noise. The alert now carries the seat and its email, the ask as the person framed it, the job id, and
    exactly what the user was told; the promise banner is off because the user was told the build could
    not be completed and the credits went back, so nothing is owed by email."""
    try:
        who = str(username or '')
        try:
            em = _pm_user_email(username)
        except Exception:
            em = ''
        label = f"{who} ({em})" if em and em.lower() != who.lower() else (em or who or '(unknown seat)')
        payload = {'_no_promise': True, 'text': str(what or '')[:400], 'job_id': job_id,
                   'user_told': str(told_user or ''), 'inputs': _smart.jsonable(inputs) if inputs else None}
        _H._chatbot_error_email(route, err, user_email=label, payload=payload)
    except Exception as _ax:
        _faults.note('chat', _ax)


def _pm_run_fw_job(job_id, username, inputs, extras):
    try:
        from migration.flywheel_synthesis import synthesize, persist
        subj = f"{inputs['subject']} {inputs['ecosystem']} flywheel"
        _pm_fw_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': subj, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        tools = None
        try:
            import prometheus_analysis as _pma_f
            tools = [_pma_f.WEB_SEARCH_TOOL]
        except Exception as _fx:
            _faults.note('chat', _fx)

        def _cf(system, user_prompt, **kw):
            kw.setdefault('usage_extras', extras)
            return _pm_claude_data(system, user_prompt, **kw)

        csv_text, study_name, summary = synthesize(
            inputs, _cf, tools=tools,
            created_by=username or 'prometheus')
        out_key = persist(_H.s3_client, csv_text, study_name)
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                if not u.get('has_flywheel_iq_access'):
                    u['has_flywheel_iq_access'] = True
                    return data
                return None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[fw-job {job_id}] access grant skipped: {acc_err}")
        _pm_fw_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': summary['title'],
            's3_key': out_key,
            'conversions': summary.get('conversions'),
            'finished_at': time.time()})
        print(f"[fw-job {job_id}] done -> {out_key}")
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _on_done('flywheel', username=username, subject=str(summary.get('title') or ''), s3_key=out_key, payload=summary,
                     host=_H, s3_client=_H.s3_client, job_id=job_id,
                     figures=([{'name': 'Captured users who converted', 'value': summary.get('conversions'), 'unit': 'people', 'definition': 'Flywheel read'}]
                              if isinstance(summary.get('conversions'), (int, float)) else []),
                     facts=([('Converted', f"{int(summary['conversions']):,}")] if isinstance(summary.get('conversions'), (int, float)) else []))
        except Exception as _dl_err:
            print(f"[fw-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        refunded = False
        try:   # the build could not be completed: the credits go back (no "snag", no hold)
            _H.refund_credit(username, credits=_PM_FW_CREDITS, reason=f"Flywheel did not deliver - {(inputs or {}).get('subject') or 'flywheel'}", job_id=str(job_id or ''))
            refunded = True
        except Exception as _rx:
            _faults.note('chat', _rx)
        try:
            _pm_fw_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error', 'refunded': refunded,
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _pm_engine_failure_alert(
            'brief-chat/fw-job', e, job_id, username, inputs,
            'This build could not be completed. Credits for this run have been refunded.' if refunded
            else 'This build could not be completed. (the refund did not post; check the seat)',
            f"Flywheel: {(inputs or {}).get('entry_point') or (inputs or {}).get('captured_action') or (inputs or {}).get('subject') or ''}"
            f" / {(inputs or {}).get('ecosystem') or ''} ecosystem")


# ---- Attribution IQ tracking pull (Jenna 2026-09-22) -----------------
# "$500 for the first setup pull ... toggle it on to refresh daily (at
# 100$ x day) ... put in when you want it to stop tracking. so youre
# charged up front for tracking through the day you want the tracking
# to end ... input all of their urls and tag them as paid or organic,
# name the campaign, etc."
_PM_AIQ_JOB_PREFIX = 'system/pm_aiq_jobs/'


_PM_AIQ_CHIP = 'Set up Attribution Tracking'


_PM_AIQ_SETUP_CREDITS = 5      # $500 at the standard $100/credit


_PM_AIQ_DAILY_CREDITS = 1      # $100 per prepaid daily-refresh day


from prometheus.aiq_assets import ASK_COPY as _PM_AIQ_ASK_COPY  # noqa: E402


def _pm_aiq_intent(text):
    return _intents.aiq_intent(text, _PM_AIQ_CHIP)


def _pm_aiq_stop_intent(text):
    low = ' '.join(str(text or '').lower().split())
    return (('stop' in low or 'end' in low or 'cancel' in low)
            and ('tracking' in low or 'attribution' in low)
            and 'attribution' in low)


def _pm_aiq_parse(text, usage_extras=None):
    """Model extraction merged with the deterministic URL/tag parser -
    the tags are the user's own, so the deterministic read wins
    whenever it finds tagged lines (the model never guesses tags)."""
    from migration.attribution_synthesis import (PARSE_SYSTEM_PROMPT,
                                                 parse_url_lines)
    parsed = _pm_claude_data(PARSE_SYSTEM_PROMPT, str(text or ''),
                             max_tokens=2500, temperature=0.0,
                             surface='aiq_parse',
                             usage_extras=usage_extras)
    parsed = parsed if isinstance(parsed, dict) else {}
    det = parse_url_lines(text)
    if det:
        parsed['urls'] = det
    return parsed


def _pm_aiq_days(parsed):
    """Prepaid daily-refresh days: tomorrow through the stop date
    inclusive (the setup pull itself carries today's read)."""
    from datetime import date as _d
    if not parsed.get('daily_refresh'):
        return 0
    try:
        end = datetime.strptime(
            str(parsed.get('end_tracking_date') or ''),
            '%Y-%m-%d').date()
    except Exception:
        return 0
    return max(0, (end - _d.today()).days)


def _pm_aiq_inputs_complete(parsed):
    urls = [u for u in (parsed.get('urls') or [])
            if isinstance(u, dict) and u.get('url')
            and str(u.get('tag') or '').lower() in ('paid', 'organic')]
    if not (parsed.get('campaign_name') and urls
            and parsed.get('conversion_event')):
        return False
    if parsed.get('daily_refresh') is None:
        return False
    if parsed.get('daily_refresh') and _pm_aiq_days(parsed) < 1:
        return False
    return True


def _pm_aiq_confirm_reply(parsed):
    from prometheus import aiq_assets as _aa
    return _aa.confirm_card(parsed, _pm_aiq_days(parsed), _pm_tool_price_label('attribution_iq_setup', '$500'),
                            _pm_tool_price_label('attribution_iq_daily', '$100'), _pm_usd_label)


def _pm_run_aiq_job(job_id, username, inputs, extras):
    try:
        from migration.attribution_synthesis import (build_campaign,
                                                     register_tracker)
        name = str(inputs.get('campaign_name') or 'Campaign')
        _pm_aiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running',
            'subject': name, 'started_at': time.time(), 'inputs': _smart.jsonable(inputs)})
        if inputs.get('add_to_slug'):   # assets joining a campaign that exists (Alexia 2026-10-16)
            from migration.attribution_synthesis import add_assets_to_campaign
            out = add_assets_to_campaign(str(inputs['add_to_slug']), list(inputs.get('urls') or []), requested_by=username, s3_client=_H.s3_client)
        else:
            out = build_campaign(inputs, requested_by=username, s3_client=_H.s3_client)
        days = _pm_aiq_days(inputs)
        if inputs.get('daily_refresh') and days > 0:
            register_tracker(out['slug'], name, username,
                             daily=True,
                             end_date=str(
                                 inputs.get('end_tracking_date')),
                             s3_client=_H.s3_client)
        # Access: the requester sees their campaign in the tab.
        try:
            def _grant(data):
                u = (data.get('users') or {}).get(username)
                if not u:
                    return None
                changed = False
                if not u.get('has_intent_iq_access'):
                    u['has_intent_iq_access'] = True
                    changed = True
                runs = u.get('allowed_intent_iq_runs')
                if isinstance(runs, list) and '*' not in runs \
                        and out['slug'] not in runs:
                    runs.append(out['slug'])
                    changed = True
                return data if changed else None
            _H._users_cas_mutate(_grant)
        except Exception as acc_err:
            print(f"[aiq-job {job_id}] access grant skipped: "
                  f"{acc_err}")
        _pm_aiq_status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'done',
            'subject': name, 'slug': out['slug'],
            'asset_count': out['asset_count'],
            'daily': bool(inputs.get('daily_refresh')),
            'end_date': str(inputs.get('end_tracking_date') or ''),
            'finished_at': time.time()})
        print(f"[aiq-job {job_id}] done -> {out['slug']}")
        try:   # delivered contract (2026-10-13)
            from prometheus.delivered import on_done as _on_done
            _on_done('attribution', username=username, subject=str(name or ''), s3_key=str(out.get('slug') or ''), payload=out,
                     host=_H, s3_client=_H.s3_client, job_id=job_id,
                     facts=[('Tracked assets', f"{int(out.get('asset_count') or 0):,}")] + ([('Added', f"{int(out.get('added') or 0):,}")] if out.get('added') else []),
                     email=not bool(inputs.get('notify_manual')))
        except Exception as _dl_err:
            print(f"[aiq-job {job_id}] delivery skipped: {_dl_err}")
    except Exception as e:
        traceback.print_exc()
        try:
            _pm_aiq_status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'error',
                'finished_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)
        _pm_engine_failure_alert(
            'brief-chat/aiq-job', e, job_id, username, inputs, 'the tracking error line in the widget',
            f"Attribution tracking: {(inputs or {}).get('campaign_name') or (inputs or {}).get('subject') or ''}")


def _pm_deck_fuzzy_suggestions(query):
    """Closest-catalog deck suggestions for a subject / ask that did not
    resolve to one exact profile (Jenna 2026-08-31: fuzzy-match the typed
    name and suggest the similar-named profiles instead of a generic
    punt). Reuses the interpret path's token-overlap shortlister
    (_shortlist_profile_matches) and layers per-user access gating; the
    grouping, family-set binding, and chip payloads live in
    prometheus_analysis (pure + unit-tested).

    Returns a list of confirm-chip payloads
    ({kind, label, subtitle, bind}); [] when nothing is close enough."""
    import prometheus_analysis as pma
    q = str(query or '').strip()
    if not q:
        return []
    try:
        catalog = _profile_catalog_for_chat()
    except Exception:
        traceback.print_exc()
        return []
    # Rank first, in memory, then gate ONLY the shortlist. The access
    # check reads the user record on every call; gating all 4,700
    # catalog entries up front took a regular seat 17 minutes on one
    # deck ask (Armine Meseldzhyan at Lionsgate, 2026-10-31) and the
    # turn came back as the calm failure line. Admin seats short-circuit
    # the check, which is why it never showed for us.
    try:
        ranked = _shortlist_profile_matches(
            q, catalog or [], max_candidates=max(SYNTH_CHAT_MAX_CANDIDATES, 24))
    except Exception:
        ranked = None
    pool = list(ranked or [])
    if not pool:
        try:
            pool = pma.deck_fuzzy_pool(q, catalog or [])[:24] if hasattr(pma, 'deck_fuzzy_pool') else []
        except Exception:
            pool = []
    accessible = []
    for c in pool[:40]:
        k = str((c or {}).get('s3_key') or '').strip()
        if not k:
            continue
        try:
            ok, _err = _H._require_profile_run_access(k)
        except Exception:
            ok = False
        if ok:
            accessible.append(c)
    if not accessible:
        return []
    try:
        return pma.build_deck_suggestions(q, accessible, ranked=accessible)
    except Exception:
        traceback.print_exc()
        return []


def _pm_resolve_deck_subject(text, ctx):
    """Resolve a typed deck ask to a profile in the catalog.

    Returns (resolution, err). resolution is a dict:
      {'ctx': page-context-shaped dict, 'subject': display subject,
       'partner': partner or '', 'ask': the ask text,
       'clarify': None | {'question', 'options'}}
    err is a (jsonify, status) tuple when the ask cannot proceed.

    Order: a subject named in the ask wins over the open profile; the
    open profile covers subject-less asks ("Build a deck from this");
    multiple plausible catalog matches come back as clarify options
    (the existing clarify-chip pattern); no subject anywhere is a
    guidance error."""
    import prometheus_analysis as pma
    brief = pma.extract_deck_brief(text) if text else \
        {'subject': '', 'partner': ''}
    partner = brief.get('partner') or ''
    wanted = (brief.get('subject') or '').strip()
    _from_delivered = bool(isinstance(ctx, dict) and ctx.get('deck_source'))   # the material is the delivered journey or the attached file, never a library profile of the same name
    if _from_delivered:
        wanted, partner = '', ''

    def _norm(s):
        return _H._normalize_for_match(str(s or ''))

    def _match(phrase):
        """Catalog candidates for a subject phrase, TU files only
        (cuts are attached separately). Returns a list of catalog
        entries, best first, deduped by subject."""
        pn = _norm(phrase)
        if not pn:
            return []
        exact, prefix, contains = [], [], []
        for entry in _profile_catalog_for_chat():
            disp = str(entry.get('display_name') or '')
            if ' - ' in disp:
                continue  # cuts never lead a deck; the TU does
            dn = _norm(disp)
            sn = _norm(entry.get('subject'))
            if not dn:
                continue
            if pn in (dn, sn):
                exact.append(entry)
            elif dn.startswith(pn) or sn.startswith(pn):
                prefix.append(entry)
            elif pn in dn or pn in sn:
                contains.append(entry)
        seen, out = set(), []
        for entry in exact + prefix + contains:
            k = _norm(entry.get('display_name'))
            if k in seen:
                continue
            seen.add(k)
            out.append(entry)
        return out

    chosen, resolved_partner = None, partner
    if wanted:
        cands = _match(wanted)
        if not cands:   # "mistletoe murders series viewers" is Mistletoe Murders Viewers: the plain title, only after the name as written matched nothing (2026-11-12)
            wanted, cands = _deck_subject.plain_match(wanted, _match)
        if not cands and partner:
            # "deck for Noah Wyle" reads partner-shaped but names the
            # subject; if the partner matches a profile, it IS the
            # subject.
            cands = _match(partner)
            if cands:
                resolved_partner = ''
        if len(cands) == 1:
            chosen = cands[0]
        elif len(cands) > 1:
            if _norm(cands[0].get('display_name')) == _norm(wanted) or \
                    _norm(cands[0].get('subject')) == _norm(wanted):
                chosen = cands[0]
            else:
                opts = []
                for c in cands[:4]:
                    nm = str(c.get('display_name') or '').strip()
                    if nm:
                        opts.append(f"Build the {nm} insights deck")
                return ({'clarify': {
                    'question': 'Which profile should the deck cover?',
                    'options': opts}}, None)
    elif partner and not (ctx and ctx.get('primary')):
        cands = _match(partner)
        if len(cands) == 1:
            chosen = cands[0]
            resolved_partner = ''

    # Session fallback (2026-08-27, Paige Bueckers ad CTR follow-up):
    # "Build a deck from this data" right after a delivered read names
    # no subject and may have nothing open, but the session remembers
    # which subject the last read covered. Resolve that subject so the
    # deck consumes the data just delivered instead of erroring.
    if chosen is None and not wanted and not (ctx and ctx.get('primary')) and not _from_delivered:
        try:
            _last_read = session.get('pm_csv_last') or {}
        except Exception:
            _last_read = {}
        _last_subj = str(_last_read.get('subject') or '').strip()
        if _last_subj:
            cands = _match(_last_subj)
            if cands:
                chosen = cands[0]

    if chosen is not None:
        p_key = str(chosen.get('s3_key') or '').strip()
        ok, err = _probe_files.run_access(_H._require_profile_run_access, p_key)   # the golden door has no session (prometheus.probe_files)
        if not ok:
            return None, err
        subject = str(chosen.get('subject')
                      or chosen.get('display_name') or '').strip()
        cuts = []
        base_norm = _norm(chosen.get('display_name'))
        for entry in _profile_catalog_for_chat():
            disp = str(entry.get('display_name') or '')
            if ' - ' not in disp:
                continue
            head, _, tail = disp.partition(' - ')
            if _norm(head) == base_norm and 'avid' in tail.lower():
                ck = str(entry.get('s3_key') or '').strip()
                c_ok, _c = _probe_files.run_access(_H._require_profile_run_access, ck)
                if c_ok and ck and ck != p_key:
                    cuts.append({'s3_key': ck, 'name': disp[:200]})
                break
        new_ctx = {'primary': {'s3_key': p_key,
                               'name': str(chosen.get('display_name')
                                           or subject)[:200]},
                   'cuts': cuts, 'extras': [],
                   'view_context': (ctx or {}).get('view_context')}
        return ({'ctx': new_ctx, 'subject': subject,
                 'partner': resolved_partner, 'clarify': None}, None)

    # A dashboard view on screen is deck material (2026-10-09, Liz: a deck
    # asked for on the Digital Journey view came back as slide text because
    # decks only knew profiles). With no library profile matched, the open
    # view (its on-screen summary plus the reads already delivered in this
    # thread) is the digest; the named subject, the view's subject, or the
    # view's title names the deck.
    _vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    # any view with figures (data or its on-screen summary) is deck material (2026-10-13 audit)
    if isinstance(_vc, dict) and ((_vc.get('data') or {}) or str(_vc.get('summary') or _vc.get('view_summary') or '').strip()) \
            and not (ctx and ctx.get('primary')):
        _vsubj = (pma.resolve_subject(ctx, _vc) or str(_vc.get('view_title') or '').strip()
                  or wanted or 'this view')   # the view names the deck; a typed phrase that matched no profile does not (2026-10-13)
        return ({'ctx': {'primary': None, 'cuts': [], 'extras': [], 'view_context': _vc},
                 'subject': _vsubj[:120], 'partner': resolved_partner, 'clarify': None,
                 'view_deck': True}, None)

    # Fuzzy suggestions (2026-08-31, Jenna): a NAMED subject that did not
    # resolve to one exact profile offers the closest catalog profiles as
    # confirm chips (including a whole-family set), not a generic punt.
    # An explicitly named subject wins even over an open profile, since
    # the user asked for something specific by name.
    if wanted:
        suggestions = _pm_deck_fuzzy_suggestions(wanted)
        if suggestions:
            return ({'clarify': {
                'question': (f'I could not find an exact match for '
                             f'"{wanted}". Did you mean one of these?'),
                'suggestions': suggestions}}, None)

    if ctx and ctx.get('primary'):
        name = str((ctx.get('primary') or {}).get('name') or '').strip()
        subject = name.split(' - ')[0].strip() or name or 'this audience'
        return ({'ctx': ctx, 'subject': subject,
                 'partner': _deck_subject.buyer(resolved_partner, subject), 'clarify': None,   # the open profile named after "for" is the subject, never its own buyer (2026-11-12)
                 'used_open_page': True}, None)

    # No named subject and nothing open: recover a profile name from the
    # raw ask itself (the extractor can miss a name inside a long,
    # multi-part project brief). Same confirm-chip suggestions.
    if not wanted and text:
        suggestions = _pm_deck_fuzzy_suggestions(text)
        if suggestions:
            return ({'clarify': {
                'question': ('Here are the closest profiles in the '
                             'library. Which should the deck cover?'),
                'suggestions': suggestions}}, None)

    if wanted:
        return None, (jsonify({
            'success': False, 'guidance': True,
            'error': (f'"{wanted}" is not in the library yet. Open the '
                      'profile from Select Profile, or build it first, '
                      'then ask for the deck again.')}), 404)
    if isinstance(_vc, dict) and str(_vc.get('view_title') or '').strip():   # a view with no figures: one honest line
        return None, (jsonify({
            'success': False, 'guidance': True,
            'error': (f"The {str(_vc.get('view_title')).strip()} page has no figures loaded for me to put on slides yet. "
                      "Open the read or profile you want the deck on, or name the subject, and ask again.")}), 400)
    return None, (jsonify({
        'success': False, 'guidance': True,
        'error': ('Name the subject ("build the Paige Bueckers insights '
                  'deck") or open a profile first, then ask for the deck '
                  'again.')}), 400)


# Pay-as-you-go attribution for deck jobs (2026-08-26): the kickoff
# request computes the extras (session id needs the request context)
# and stashes them here for the background thread to ride on the
# slide-plan model call. Keyed by job_id; popped when the job starts.
_PM_DECK_PPU_EXTRAS = {}


@_ledger_gate.job_scope(probe_kw='')
def _pm_run_deck_job(job_id, username, ctx, history, angle,
                     charge_user='', subject='', partner=''):
    """Background insights-deck build: digest -> full slide plan ->
    finished PPTX in the Crosswalk deck system -> S3 -> presigned URL.
    Status written to S3 at every phase so any worker can serve the
    poll. `charge_user` is the billing username charged at kickoff;
    refunded here if the build fails."""
    import tempfile
    base = {'job_id': job_id, 'user': username, 'angle': angle,
            'started_at': time.time()}
    _ppu_extras = _PM_DECK_PPU_EXTRAS.pop(job_id, None)
    try:
        import prometheus_analysis as pma
        import deck_builder
        _pm_deck_status_write(job_id, {**base, 'status': 'planning'})
        if isinstance(ctx, dict) and not ctx.get('primary') and isinstance(ctx.get('view_context'), dict):
            # View deck (2026-10-09, Liz): the on-screen view plus the reads
            # already delivered in this thread are the digest.
            digest, p_meta = _pm_view_deck_digest(ctx['view_context'], history, subject)
        else:
            digest, p_meta = pma.get_digest_bundle(_H.s3_client, _H.S3_BUCKET, ctx)
        subject = subject or p_meta.get('name') or 'this audience'
        _deck_prompt = pma.build_insights_deck_user_prompt(
            subject, partner, digest, history, angle)
        # Delivered reads ride along (2026-08-27, ad CTR follow-up):
        # "build a deck from this data" right after a measurement
        # read must consume that read even when the visible history
        # got truncated. The ledger holds every delivered number for
        # the subject; a deck that restates them stays consistent
        # with what the chat already said.
        try:
            import insights_ledger as _il_deck
            _led = _il_deck.consult(subject=subject)
            if _led.get('block'):
                _deck_prompt = (
                    f"{_deck_prompt}\n\n"
                    "DELIVERED READS (numbers already shipped for "
                    "this subject; keep any you restate consistent "
                    "with these)\n"
                    "==========================================\n"
                    f"{_led['block']}")
        except Exception:
            traceback.print_exc()
        try:
            import prometheus_knowledge as _pmk
            _kb = _pmk.knowledge_block(
                'deck', text=str(angle or ''), subject=subject,
                s3_client=_H.s3_client, bucket=_H.S3_BUCKET)
            if _kb:
                _deck_prompt = f"{_deck_prompt}\n\n{_kb}"
        except Exception as _fx:
            _faults.note('chat', _fx)
        # Corpus catalog (2026-10-06): the dashboard's own figures on
        # the subject bind the deck's headline numbers.
        try:
            _cat_block = _pm_catalog_block(subject)
            if _cat_block:
                _deck_prompt = f"{_deck_prompt}\n\n{_cat_block}"
        except Exception as _fx:
            _faults.note('chat', _fx)
        plan_result = _pm_claude_json(
            pma.INSIGHTS_DECK_SYSTEM_PROMPT, _deck_prompt,
            max_tokens=20000, temperature=0.4, surface='deck',
            usage_extras=_ppu_extras)
        if not plan_result.get('success'):
            raise RuntimeError(plan_result.get('error')
                               or 'slide plan failed')
        plan = plan_result.get('data') or {}
        if isinstance(plan, list):
            plan = next((d for d in plan if isinstance(d, dict)), {})
        # The read's own data, as text, and the names in it (2026-11-11): a
        # title, a brand or a person's name the read carries is never
        # flagged or rewritten by a spelling, casing or word-ban pass, and
        # a page of web addresses carries only addresses the read holds.
        _read_text, _read_names = '', []
        try:
            _read_text = pma.deck_read_text(digest, history)
            _read_names = pma.deck_entities(_read_text, subject)
        except Exception as _fx:
            _faults.note('chat', _fx)
        plan = pma.enforce_insights_plan(plan, subject, names=_read_names)
        if not plan.get('slides'):
            raise RuntimeError('slide plan came back empty')
        plan = _deck_pick.hold_plan_to_read(   # a deck made from a read keeps the read's figures (2026-11-11)
            plan, ctx, replan=lambda _note: _pm_claude_json(
                pma.INSIGHTS_DECK_SYSTEM_PROMPT, f"{_deck_prompt}\n\n{_note}", max_tokens=20000, temperature=0.4,
                surface='deck', usage_extras=_ppu_extras).get('data'),
            post=lambda _p: pma.enforce_insights_plan(_p, subject, names=_read_names))
        from prometheus import category_reach as _cre   # a card that is a group of brands carries its reach and index (2026-11-11)
        plan = _cre.annotate_deck(plan, ctx, subject)
        _pm_deck_status_write(job_id, {**base, 'status': 'rendering'})
        stem = re.sub(r'[^A-Za-z0-9_]+', '_',
                      str(plan.get('filename_stem')
                          or subject
                          or p_meta.get('name')
                          or 'Insights')).strip('_')[:60] or 'Insights'
        fname = _door_words.scrub_filename(f"{stem}_Insights.pptx")
        local = os.path.join(tempfile.gettempdir(),
                             f"pm_deck_{job_id}.pptx")
        static_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'static')
        # The finisher repairs whatever the plain-English check flags, in
        # place: a model pass over only the flagged lines first, then its
        # fixed repairs (2026-11-11: two decks shipped READY with a failed
        # readability check and were fixed by hand).
        try:
            _copy_rewriter = pma.make_deck_copy_rewriter(_pm_claude_json, usage_extras=_ppu_extras)
        except Exception as _fx:
            _faults.note('chat', _fx)
            _copy_rewriter = None
        deck_builder.render_insights_deck(
            plan, local, static_dir=static_dir,
            photo_subject=str(subject or p_meta.get('name') or ''),
            photo_kind=deck_builder.photo_kind_for_category(p_meta.get('brand_category')),
            copy_rewriter=_copy_rewriter, protected_names=_read_names, read_text=_read_text)
        base.update(image_subject=plan.get('_photo_subject') or plan.get('image_subject'),
                    photo_sources=plan.get('_photo_sources') or [],
                    # the pre-ship pass on the finished deck: ready, lint and
                    # readability counts, what it changed (2026-11-10)
                    finish=plan.get('_finish') or {})
        s3_key = f"{_PM_DECK_FILE_PREFIX}{job_id}/{fname}"
        with open(local, 'rb') as fh:
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=s3_key, Body=fh.read(),
                ContentType=('application/vnd.openxmlformats-officedocument'
                             '.presentationml.presentation'))
        # A PDF copy when the ask wants one ("send me the deck as a pdf",
        # "PDF to PowerPoint and back", 2026-10-30 audit item 2) and the
        # build host can render it. Best effort: the PowerPoint always ships.
        _pdf_extra = {}
        if re.search(r"\bpdf\b", str(angle or ''), re.I):
            try:
                import shutil as _sh
                import subprocess as _sp
                _soff = _sh.which('soffice') or _sh.which('libreoffice')
                if _soff:
                    _pdf_dir = tempfile.mkdtemp(prefix='pm_deck_pdf_')
                    _sp.run([_soff, '--headless', '--convert-to', 'pdf', '--outdir', _pdf_dir, local],
                            timeout=300, capture_output=True)
                    _pdf_local = os.path.join(_pdf_dir, os.path.splitext(os.path.basename(local))[0] + '.pdf')
                    if os.path.exists(_pdf_local):
                        _pdf_name = f"{stem}_Insights.pdf"
                        _pdf_key = f"{_PM_DECK_FILE_PREFIX}{job_id}/{_pdf_name}"
                        with open(_pdf_local, 'rb') as _pfh:
                            _H.s3_client.put_object(Bucket=_H.S3_BUCKET, Key=_pdf_key, Body=_pfh.read(),
                                                    ContentType='application/pdf')
                        _pdf_extra = {
                            'pdf_s3_key': _pdf_key, 'pdf_filename': _pdf_name,
                            'pdf_url': _H.s3_client.generate_presigned_url(
                                'get_object',
                                Params={'Bucket': _H.S3_BUCKET, 'Key': _pdf_key,
                                        'ResponseContentDisposition': _file_names.disposition(_pdf_name)},
                                ExpiresIn=7 * 24 * 3600)}
                        try:
                            os.remove(_pdf_local)
                        except OSError:
                            pass
            except Exception:
                traceback.print_exc()
        try:
            os.remove(local)
        except OSError:
            pass
        url = _H.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': _H.S3_BUCKET, 'Key': s3_key,
                    'ResponseContentDisposition':
                        _file_names.disposition(fname)},
            ExpiresIn=7 * 24 * 3600)
        _pm_deck_status_write(job_id, {
            **base, 'status': 'done', 'url': url, 'filename': fname,
            'title': plan.get('title'),
            'slides': len(plan.get('slides') or []), **_pdf_extra})
        # Delivered-deck anchors (2026-08-27): the shipped deck's
        # headline figures become binding ledger anchors so every
        # later read or deck for this subject stays commensurate
        # with what was delivered.
        try:
            import insights_ledger as _il_anchor
            _anchor_metrics = pma.extract_plan_anchors(plan)
            if _anchor_metrics:
                _il_anchor.ingest_deck_anchors(
                    subject=subject, metrics=_anchor_metrics,
                    source_name=fname)
        except Exception:
            traceback.print_exc()
        print(f"[prometheus] deck {job_id} done for {username}: {fname}")
        # Land the finished deck in the requester's chat thread so the
        # download link is waiting when they return, and (if they
        # opted in) email them the link.
        _deck_status = {'url': url, 'filename': fname, 's3_key': s3_key,
                        'title': plan.get('title'),
                        'slides': len(plan.get('slides') or []), **_pdf_extra}
        _pm_append_deck_to_history(username, job_id, _deck_status)
        if not _pm_flush_notify(job_id, 'deck', _deck_status):
            # A finished deck always reaches the requester's inbox with the
            # file attached (2026-10-09, Jenna: "sent to her email and that
            # she could download"), not only when they tapped notify.
            _pm_send_output_email('deck', _pm_user_email(username), _deck_status, sync=True)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/deck-job', e,
                             user_email=username,
                             payload={'job_id': job_id,
                                      'angle': str(angle)[:200]})
        _pm_deck_status_write(job_id, {**base, 'status': 'error',
                                       'error': str(e)[:400]})
        _PT.job_failed('deck', username, job_id, str(angle)[:300], land=lambda _p: _PT.land_note(_pm_thread_for_job(username, job_id), username, job_id, _p))
        _pm_notify_delete(job_id)
        # 2026-09-09 Jenna: decks are session-metered, not per-pull
        # charged, so there is no per-pull refund to issue on
        # failure. `charge_user` is always empty in the new flow;
        # branch retained as a defensive no-op for legacy callers.
        if charge_user:
            pass


@_H.app.route('/api/brief-chat/deck', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/deck')
def api_synth_chat_deck():
    """Kick off an async insights-deck build. The ask can name any
    profile in the library ("build the Paige Bueckers insights deck")
    or lean on the open page's profile ("Build a deck from this").
    Returns a job_id the frontend polls via deck-status, or a clarify
    payload when several profiles match the named subject."""
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): decks are analysis
    # outputs. Pull-only users cannot kick off a deck build.
    if not _pm_gate_analyze(user):
        return _pm_gate_refusal('analyze')
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        _H._chatbot_error_email('brief-chat/deck', e)
        return jsonify(_H._chatbot_calm_payload())
    return _pm_deck_core(user, body)


def _pm_deck_core(user, body):
    """The deck surface for an already-gated user. Split out of
    ``api_synth_chat_deck`` (2026-10-01, Prometheus Phase 1); the
    tier, funds, and usage gates below run for every caller."""
    _pm_req_thread_set(body)
    # Prometheus tier gate (2026-08-26): decks are an analysis-tier
    # feature. pulls_only users without pay-as-you-go get the offer.
    _t_deck0 = time.monotonic()
    _gate_resp = _pm_access_gate(user)
    if _gate_resp is not None:
        return _gate_resp
    _pm_ask_stage('deck_access_gate', _t_deck0)
    _pm_ppu = _pm_usage_extras(user)
    text = str(body.get('text') or '').strip()[:600]
    angle = ((body.get('angle') or '').strip()
             or text
             or 'The strongest commercial story in this data.')[:400]
    history = body.get('history') or []
    if not isinstance(history, list):
        history = []
    ctx, ctx_err = _pm_validate_page_context(body.get('page_context'))
    if ctx_err:
        return ctx_err
    _ds = _deck_sources.resolve(text, ctx, history, user, _H, tid=str(body.get('thread_id') or ''))   # a PPT of the deck already delivered or attached (Debi 2026-10-21)
    if _ds:
        ctx, history = _ds
        angle = 'The same deck, slide for slide: every number, table and read exactly as delivered, in the same order, nothing added.'
        if re.search(r"\b(?:as|to|into|in)\s+(?:an?\s+)?pdf\b|\bpdf\s+(?:version|copy|export|file)\b", text, re.I):
            angle += ' Deliver a PDF copy too.'   # the deck job renders one where it can (2026-10-30)
    try:
        # a which-subject chip picked for a deck names the subject outright
        # (2026-10-31): resolve that name, keep the ask as the angle
        _bind_pick = str(body.get('bind_subject') or '').strip()
        if _bind_pick and body.get('confirm_open_screen'):
            _dp_pick = _bind_pick
        else:   # else the name the request carries ("a deck for mistletoe murders"); a name we do not hold, with another audience open, is asked once
            _dp_pick, _dp_q = ('', None) if _ds else _deck_subject.pick_or_ask(body, text, ctx, history, _profile_catalog_for_chat, hint=_pm_ask_hint)
            if _dp_q:
                return jsonify(_dp_q)
        # the picked or named subject, or no deck: a library profile, else the read delivered on it in this thread,
        # never another candidate from the list (prometheus.deck_pick, Samantha Kolton 2026-11-11)
        _pvd = None if _ds else _bpiq_wiz.deck_gate(user, body, text, history, _dp_pick, globals())   # a deck of a read that put a dollar value on a partnership is the valuation order (Jenna 2026-11-11)
        if _pvd is not None:
            return jsonify(_pvd)
        if _ds:   # the material is something already delivered or attached: the words alone resolve it
            resolution, res_err = _pm_resolve_deck_subject(text, ctx)
            _dp_msg = ''
        else:
            resolution, res_err, _dp_msg = _deck_pick.settle(
                _dp_pick, text, ctx, history, _pm_resolve_deck_subject, load_read=lambda _j: _deck_pick.load_read_doc(
                    _H.s3_client, _H.S3_BUCKET, _PM_READ_PREFIX, _j, user.get('username') or user.get('email') or ''))
        if _dp_msg:
            return jsonify({'success': False, 'guidance': True, 'error': _dp_msg}), 404
        if resolution and resolution.get('from_read'):
            angle = _deck_pick.read_angle(resolution.get('subject'), text)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/deck', e,
                             payload={'text': text[:200]})
        return jsonify(_H._chatbot_calm_payload())
    if res_err:
        return res_err
    _pm_ask_stage('deck_resolve', _t_deck0)
    if resolution.get('clarify'):
        return jsonify(_deck_subject.choice_payload(resolution['clarify']))   # the did-you-mean carries its own sentence: as a block alone it read as an empty reply and the calm promise was recorded over it (2026-11-12)
    _page_named = False
    try:   # "make me a deck on New Heights" with New Heights Podcast Listeners open: the words confirm the page (2026-10-30, item 13)
        _page_named = bool(_subject_rules.words_name_page(text, str(resolution.get('subject') or ''))) \
            or bool(_subject_rules.deixis_points_at_screen(text, history)) or bool(_deck_subject.choice_picked(history, resolution.get('ctx')))   # "make this a deck" on an empty thread: this is the screen (golden g083); a did-you-mean choice just tapped is the answer, never confirmed again (2026-11-12)
    except Exception:
        _page_named = False
    if resolution.get('used_open_page') \
            and not body.get('confirm_open_screen') and not _page_named:
        page = str(resolution.get('subject') or 'this profile').strip()
        # the thread's own subject leads; a page the reader said no to is
        # never the lead chip (2026-10-20 Jenna, Mark at Bent Pixels)
        _osq = _subject_rules.open_screen_question(text, page, history, deck=True, what='the deck')
        if _osq:
            _pm_ask_hint(route='deck', outcome=_osq.pop('outcome'), subject=page)
            return jsonify(_osq)
        if body.get('_pm_correction') or body.get('bind_subject'):
            # the reader just corrected the page away (I2): never the page
            # yes/no again; the bound subject leads the which-subject question
            _subs = [x for x in [str(body.get('bind_subject') or '').strip()] + list(_subject_rules.thread_subjects(history, page)) if x]
            _wsp = _subject_rules.which_subject_payload(page, _subs[:3], what='the deck')
            _pm_ask_hint(route='deck', outcome='asked_which_subject_correction', subject=_subs[0] if _subs else page)
            return jsonify({'success': True, 'action': 'answer', 'reply': _wsp['reply'], 'followups': _wsp['followups'],
                            'offer_deck': False, 'deck_angle': None,
                            'memory_confirm': {'question': text, 'deck': True, 'options': [dict(o, deck=True) for o in _wsp['options']]}})
        yes = f'Yes, {page}'
        return jsonify({
            'success': True,
            'reply': (f'Do you want this on {page} (open on your '
                      f'screen)?'),
            'followups': [yes, 'Something else'],
            'memory_confirm': {
                'question': text,
                'deck': True,
                'options': [{'label': yes, 'subject': page,
                             'deck': True}],
            },
        })
    # Funds gate (2026-09-16, Jenna): decks are metered work too. It runs
    # AFTER the question (I8, 2026-10-20: a yes/no about the open tab
    # costs nothing and must not wait on the wallet).
    _funds_resp = _pm_funds_gate(user)
    if _funds_resp is not None:
        return _funds_resp
    _pm_ask_stage('deck_funds_gate', _t_deck0)
    ctx = resolution['ctx']
    deck_subject = resolution.get('subject') or ''
    deck_partner = resolution.get('partner') or ''
    job_id = uuid.uuid4().hex[:12]
    username = (user.get('username') or user.get('email') or '').strip()
    # 2026-09-09 Jenna: deck builds are session-metered, not per-pull
    # charged. Pay-per-use accounts bill via the session close;
    # subscribed accounts are covered by their tier. `_charge_user` is
    # kept as an empty string for downstream compatibility (the refund
    # path below reads it and no-ops when empty), but no per-pull
    # credit is deducted for a deck.
    _charge_user = ''
    # Attribute the deck's model spend to the enqueuing user for ALL
    # users (not just pay-as-you-go), so the daily spend email's
    # per-user Prometheus breakdown captures deck builds. The bg thread
    # has no request context, so identity is captured here at enqueue;
    # any pay-as-you-go billing fields in _pm_ppu are preserved.
    _deck_extras = _pm_merge_extras(_pm_attrib_extras(), _pm_ppu)
    if _deck_extras:
        _PM_DECK_PPU_EXTRAS[job_id] = _deck_extras
    _head = {'job_id': job_id, 'user': username, 'status': 'queued', 'angle': angle, 'started_at': time.time()}
    _pm_job_bind_thread(job_id, username)
    _args = (job_id, username, ctx, history[-14:], angle, _charge_user, deck_subject, deck_partner)
    if _faults.is_probe():   # the golden set grades the routing; a probe never builds a deck (2026-11-02)
        print(f"[pm-deck] probe ask: deck {job_id} on {deck_subject!r} not started")
        return jsonify({'success': True, 'job_id': job_id, 'subject': deck_subject, 'confirm_line': _deck_pick.confirm_line(resolution)})
    if _rr.queue_enabled():   # decks build on the Hetzner read worker like reads (2026-10-09); unclaimed in 150 s, the sweeper builds it here
        _head['resume'] = {'ctx': ctx, 'history': history[-14:], 'angle': angle, 'charge_user': _charge_user,
                           'subject': deck_subject, 'partner': deck_partner, 'extras': _deck_extras or {}}
        _rr.enqueue_deck(job_id, _head)
    else:
        _pm_deck_status_write(job_id, _head)
        _ledger_gate.note_job(job_id, username)
        threading.Thread(target=_pm_run_deck_job, args=_args, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id, 'subject': deck_subject, 'confirm_line': _deck_pick.confirm_line(resolution)})


@_H.app.route('/api/brief-chat/pay-per-use', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/pay-per-use')
def api_synth_chat_pay_per_use():
    """The user's Yes on the pay-as-you-go offer (2026-08-26, Jenna).

    Flips pay_per_use_enabled on the user's own record (ETag CAS via
    save_users), notifies Jenna/Jessie/Liz that pay per use started
    for this user, and returns enabled=True so the frontend replays
    the original ask seamlessly. Idempotent: a second Yes from an
    already-enabled user just confirms. The opt-in persists until an
    admin changes the user's tier in the admin (a tier change resets
    it)."""
    user, err = _synth_chat_gate(allow_api_key=False)
    if err:
        return err
    import pay_per_use as ppu
    tier, already = ppu.resolve_access(user)
    if tier != ppu.ACCESS_PULLS_ONLY:
        # Full-tier (or super_admin) users are subscribed; nothing to
        # turn on.
        return jsonify({'success': True, 'enabled': False,
                        'already_subscribed': True})
    username = (session.get('username') or user.get('username')
                or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'no user'}), 400
    if not already:
        started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        try:
            data = _H.load_users()
            u = (data.get('users') or {}).get(username)
            if not u:
                return jsonify({'success': False,
                                'error': 'user not found'}), 404
            u['pay_per_use_enabled'] = True
            u['pay_per_use_started_at'] = started
            _H.save_users(data)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False,
                            'error': 'could not update your access'}), 500
        display = (f"{user.get('first_name', '')} "
                   f"{user.get('last_name', '')}").strip() or username
        try:
            ppu.send_start_email_async(display,
                                       (user.get('email') or username))
        except Exception:
            traceback.print_exc()
        print(f"[pay-per-use] {username} opted in at {started}")
    return jsonify({'success': True, 'enabled': True})


# <<extracted: prometheus.date_snapshot block 1>>


# <<extracted: prometheus.date_snapshot block 2>>


# <<extracted: prometheus.date_snapshot block 3>>

_EXPORTS = (
    '_pm_record_held_reply',   # host 'record_held' (turn_gate.calm_hold, 2026-10-21)
    '_pm_watch_flag',          # host 'watch_flag' (I10, 2026-10-20)
    'SYNTH_CHAT_BATCH_MAX',
    'SYNTH_CHAT_FRESH_DAYS',
    'SYNTH_CHAT_HISTORY_KEY_PREFIX',
    'SYNTH_CHAT_MAX_CANDIDATES',
    'SYNTH_CHAT_THREADS_PREFIX',
    '_ASK_SUBJECT_VIEWS',
    '_ASK_SUBJ_STOP',
    '_PM_AIQ_ASK_COPY',
    '_PM_AIQ_CHIP',
    '_PM_AIQ_DAILY_CREDITS',
    '_PM_AIQ_JOB_PREFIX',
    '_PM_AIQ_SETUP_CREDITS',
    '_PM_ANALYZE_CANDIDATES',
    '_PM_ANALYZE_MODEL_ENV',
    '_PM_BASE_GENERIC_TOKENS',
    '_PM_BPIQ_ASK_COPY',
    '_PM_BPIQ_CHIP',
    '_PM_BPIQ_CREDITS',
    '_PM_BPIQ_JOB_PREFIX',
    '_PM_BUILD_NOTIFY_PREFIX',
    '_PM_CAMPAIGN_ASK_RE',
    '_PM_CHALLENGE_RES',
    '_PM_CLARIFY_COMPARE_RE',
    '_PM_CLARIFY_METRIC_RE',
    '_PM_CLARIFY_NAME_RES',
    '_PM_CLARIFY_STOP_TOKENS',
    '_PM_CLARIFY_TURN_RE',
    '_PM_CLASSIFY_CANDIDATES',
    '_PM_CLASSIFY_MODEL_ENV',
    '_PM_CLASSIFY_SURFACES',
    '_PM_COHORT_WORDS_RE',
    '_PM_COUNT_IN_REPLY_RE',
    '_PM_CUT_COHORT_RE',
    '_PM_CUT_IDIOM_RE',
    '_PM_CUT_INTENT_RE',
    '_PM_DATA_FILE_PREFIX',
    '_PM_DECK_FILE_PREFIX',
    '_PM_DECK_PPU_EXTRAS',
    '_PM_DECK_PREFIX',
    '_PM_DEFINITE_REF_RE',
    '_PM_EMAIL_ADDR_RE',
    '_PM_EMAIL_FILE_RE',
    '_PM_EMAIL_RE',
    '_PM_EMAIL_WHEN_READY_RE',
    '_PM_FEEDBACK_RES',
    '_PM_FEEDBACK_SENT',
    '_PM_FILE_ASK_RE',
    '_PM_FUNNEL_ASK_RE',
    '_PM_FW_ASK_COPY',
    '_PM_FW_CHIP',
    '_PM_FW_CREDITS',
    '_PM_FW_JOB_PREFIX',
    '_PM_IDLE_NEW_THREAD_SECONDS',
    '_PM_INTENT_HYDRATE_CACHE',
    '_PM_JIQ_ASK_COPY',
    '_PM_JIQ_CHIP',
    '_PM_JIQ_CREDITS',
    '_PM_JIQ_JOB_PREFIX',
    '_PM_JOB_THREAD',
    '_PM_LAST_FILE_PREFIX',
    '_PM_MANUAL_LOOK_COOLDOWN_S',
    '_PM_MANUAL_LOOK_STAMP_FILE',
    '_PM_MARKET_SCOPE_RE',
    '_PM_MAX_THREADS',
    '_PM_METHOD_CHALLENGE_RES',
    '_PM_MY_LAST_RE',
    '_PM_MY_NYEAR_RE',
    '_PM_MY_RANGE_RE',
    '_PM_MY_SINCE_RE',
    '_PM_MY_SPAN_RE',
    '_PM_MY_YOY_RE',
    '_PM_NAMED_SERVICE_RE',
    '_PM_NOTIFY_PREFIX',
    '_PM_PENDING_Q_S3_KEY',
    '_PM_PRICING_COPY',
    '_PM_PROFILE_SHAPE_RE',
    '_PM_RANKER_FAMILIES',
    '_PM_READ_INFLIGHT_PREFIX',
    '_PM_READ_PREFIX',
    '_PM_RECENT_BUILDS_KEY',
    '_PM_REFUSAL_RX',
    '_PM_REGRESSION_CASES_KEY',
    '_PM_REPORT_ASK_RE',
    '_PM_REQ_THREAD',
    '_PM_SCREEN_DEIXIS_RE',
    '_PM_SNAPSHOT_MONTHS',
    '_PM_SPLIT_DIGIT_RE',
    '_PM_STATUS_ASK_RES',
    '_PM_STATUS_TAIL_TOKENS',
    '_PM_SUBJECT_ALIASES',
    '_PM_TITLES_SCOPE_RE',
    '_PM_TWO_BASE_VOCAB_RE',
    '_PM_US_SHARE_RE',
    '_PM_VIEW_DEIXIS_RE',
    '_PM_VIEW_VOCAB_STOP',
    '_PM_WO_CANCEL_RE',
    '_PM_WO_ETA_RES',
    '_PM_WO_STATUS_RES',
    '_PM_WO_WORK_NOUNS',
    '_PM_WRONG_ANSWER_RES',
    '_RUN_MINUTES_TABLE',
    '_SYNTH_CHAT_INTERPRET_MODEL',
    '_ask_infer_route_outcome',
    '_ask_logged',
    '_ask_mentions_subject',
    '_estimate_run_minutes',
    '_jitter_draft_est_sample',
    '_load_synth_chat_history',
    '_load_threads_index', '_pm_project_label', '_turn_epoch', '_is_server_turn', 'keep_server_turns', 'fill_blank_agent_turns',
    '_pm_access_gate',
    '_pm_aiq_confirm_reply',
    '_pm_aiq_days',
    '_pm_aiq_inputs_complete',
    '_pm_aiq_intent',
    '_pm_aiq_parse',
    '_pm_aiq_status_write',
    '_pm_aiq_stop_intent',
    '_pm_analyze_core',
    '_pm_answer_file_payload',
    '_pm_append_deck_to_history',
    '_pm_append_read_to_history',
    '_pm_ask_hint',
    '_pm_ask_names_its_audiences',
    '_pm_ask_stage',
    '_pm_attrib_extras',
    '_pm_bank_regression_case',
    '_pm_bpiq_confirm_reply',
    '_pm_bpiq_inputs_complete',
    '_pm_bpiq_intent',
    '_pm_bpiq_parse',
    '_pm_bpiq_status_write',
    '_pm_twin_confirm_reply', '_pm_twin_intent',
    '_pm_cancel_target',
    '_pm_cant_do_lane',
    '_pm_challenge_headsup',
    '_pm_clarify_answer_merge',
    '_pm_classify_chain',
    '_pm_claude_data',
    '_pm_claude_json',
    '_pm_clean_notify_email',
    '_pm_compact_for_bank',
    '_pm_correct_esc',
    '_pm_correct_page',
    '_pm_csv_download_response',
    '_pm_csv_point',
    '_pm_csv_task_filename',
    '_pm_deck_core',
    '_pm_deck_fuzzy_suggestions',
    '_pm_deck_status_write',
    '_pm_detect_multi_year_ask',
    '_pm_edit_distance',
    '_pm_email_file_intent',
    '_pm_email_file_response',
    '_pm_eta_line',
    '_pm_expand_alias_tokens',
    '_pm_file_stash_read',
    '_pm_file_stash_write',
    '_pm_flush_notify',
    '_pm_forward_user_feedback',
    '_pm_funds_gate',
    '_pm_fuzzy_catalog_subject',
    '_pm_fw_confirm_reply',
    '_pm_fw_inputs_complete',
    '_pm_fw_intent',
    '_pm_fw_parse',
    '_pm_fw_status_write',
    '_pm_gate_analyze',
    '_pm_gate_pull',
    '_pm_gate_refusal',
    '_pm_generate_metrics_response',
    '_pm_generate_read_core',
    '_pm_generation_base',
    '_pm_has_funding',
    '_pm_held_read_clarify',
    '_pm_history_bind_text',
    '_pm_history_has_job_turn',
    '_pm_history_has_running_job',
    '_pm_intent_compact_numbers',
    '_pm_intent_view_hydrate',
    '_pm_interpret_core',
    '_pm_iso_now',
    '_pm_jiq_confirm_reply',
    '_pm_jiq_inputs_complete',
    '_pm_jiq_intent',
    '_pm_jiq_parse',
    '_pm_jiq_status_write',
    '_pm_job_bind_thread',
    '_pm_job_owner_ok',
    '_pm_load_thread_or_active',
    '_pm_looks_report_ask',
    '_pm_manual_look_lock',
    '_pm_manual_look_stamps',
    '_pm_memory_last_window',
    '_pm_merge_extras',
    '_pm_meter_answer',
    '_pm_model_chain',
    '_pm_model_lock',
    '_pm_new_thread_into',
    '_pm_normalize_ask',
    '_pm_notify_delete',
    '_pm_notify_read',
    '_pm_notify_write',
    '_pm_numeric_followup_reply',
    '_pm_open_screen_confirm',
    '_pm_overall_rankers_reply',
    '_pm_page_clarify_subject',
    '_pm_panel_fact_response',
    '_pm_panel_price_label',
    '_pm_panel_refund',
    '_pm_parse_count',
    '_pm_parse_iso',
    '_pm_pending_q_tokens',
    '_pm_pop_pending_question',
    '_pm_prev_user_question',
    '_pm_pricing_copy',
    '_pm_pricing_question',
    '_pm_promote_date_snapshot_ask',
    '_pm_rankers_board_block',
    '_pm_read_inflight_check',
    '_pm_read_inflight_doc_key',
    '_pm_read_inflight_mark',
    '_pm_read_recent_check',
    '_pm_read_status_write',
    '_pm_reads_as_refusal',
    '_pm_recent_build_guard',
    '_pm_register_build_notify',
    '_pm_regression_q_key',
    '_pm_remember_ask',
    '_pm_remember_ask_build',
    '_pm_replay_repeat_block',
    '_pm_req_thread_set',
    '_pm_rescue_unverified_draft',
    '_pm_resolve_deck_subject',
    '_pm_resolve_snapshot_date',
    '_pm_resolved_classify',
    '_pm_resolved_model',
    '_pm_rotate_idle_thread',
    '_pm_run_aiq_job',
    '_pm_run_bpiq_job',
    '_pm_run_deck_job',
    '_pm_run_fw_job',
    '_pm_run_jiq_job',
    '_pm_run_read_job',
    '_pm_s3_json',
    '_pm_s3_put_json',
    '_pm_safe_user',
    '_pm_save_thread_or_active',
    '_pm_screen_bind_verdict',
    '_pm_search_demand_response',
    '_pm_send_output_email',
    '_pm_short_name_identity',
    '_pm_stash_pending_question',
    '_pm_status_ask_subject',
    '_pm_status_matching_runs',
    '_pm_status_reply_for_runs',
    '_pm_text_names_catalog_subject',
    '_pm_thread_for_job',
    '_pm_thread_key',
    '_pm_thread_title_from',
    '_pm_threads_index_key',
    '_pm_titles_ask_needs_scope',
    '_pm_token_typo_eq',
    '_pm_tool_price_label',
    '_pm_trajectory_files',
    '_pm_trajectory_metrics',
    '_pm_trajectory_reply',
    '_pm_two_base_detect',
    '_pm_typical_build_minutes',
    '_pm_universe_phrase',
    '_pm_usage_extras',
    '_pm_user_runs',
    '_pm_validate_page_context',
    '_pm_verify_prior_entries',
    '_pm_view_owns_ask',
    '_pm_view_vocab_hit',
    '_pm_watch_notify',
    '_pm_workorder_intent',
    '_pm_workorder_reply',
    '_pm_year_files_for_subject',
    '_pm_year_package_reply',
    '_profile_catalog_for_chat',
    '_save_synth_chat_history',
    '_shortlist_profile_matches',
    '_spec_from_draft',
    '_synth_chat_cut_strategist',
    '_synth_chat_discovery_options',
    '_synth_chat_gate',
    '_synth_chat_history_key',
    '_synth_chat_incidence_check',
    '_synth_chat_interpret_batch',
    '_synth_chat_interpret_one_subject',
    '_synth_chat_interpret_prompts',
    '_synth_chat_is_discovery_request',
    '_synth_chat_is_incidence_request',
    'api_prometheus_proactive',
    'api_synth_chat_active_runs',
    'api_synth_chat_aiq_status',
    'api_synth_chat_analyze',
    'api_synth_chat_approve',
    'api_synth_chat_bpiq_status',
    'api_synth_chat_clarify',
    'api_synth_chat_deck',
    'api_synth_chat_deck_status',
    'api_synth_chat_fw_status',
    'api_synth_chat_health',
    'api_synth_chat_history',
    'api_synth_chat_interpret',
    'api_synth_chat_jiq_status',
    'api_synth_chat_notify_when_done',
    'api_synth_chat_pay_per_use',
    'api_synth_chat_read_status',
    'api_synth_chat_rebind_window',
    'api_synth_chat_status',
    'api_synth_chat_threads',
    'api_internal_intake_canary',
    'api_synth_chat_threads_activate',
    'api_synth_chat_threads_bind',
    'api_synth_chat_threads_delete',
    'api_synth_chat_threads_fresh',   # the fresh-pane stamp (prometheus.thread_presence, 2026-11-06)
    'api_synth_chat_threads_new',
    'api_synth_chat_threads_rename',
)


# ---------------------------------------------------------------------------
# Families extracted from this module (2026-10-02 RCA W3). They read this
# module's names through ``prometheus.legacy.C`` at call time, so they are
# bound and imported LAST, after every helper they lean on exists. The
# moved names are re-exported here so ``_EXPORTS`` and in-module call
# sites keep resolving.
# ---------------------------------------------------------------------------
from prometheus.legacy import bind_core as _bind_core  # noqa: E402
_bind_core(sys.modules[__name__])
from prometheus.legacy.threads import (  # noqa: E402,F401
    api_synth_chat_threads,
    api_synth_chat_threads_new,
    api_synth_chat_threads_activate,
    api_synth_chat_threads_bind,
    api_synth_chat_threads_rename,
    api_synth_chat_threads_delete,
    api_synth_chat_threads_fresh,
    _pm_threads_index_key,
    _pm_thread_key,
    _pm_thread_title_from,
    _pm_project_label,
    _load_threads_index,
    _turn_epoch,
    _is_server_turn,
    keep_server_turns,
    fill_blank_agent_turns,
)
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
from prometheus.legacy.notify import (  # noqa: E402,F401
    _PM_NOTIFY_PREFIX, _pm_clean_notify_email, _pm_notify_write, _pm_notify_read, _pm_notify_delete,
    _pm_send_output_email, _pm_flush_notify, _pm_user_email, _pm_view_deck_digest,
)
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
from prometheus.legacy.rankers import (  # noqa: E402,F401  (Rankers boards + multi-year reads, 2026-11-08)
    _pm_overall_rankers_reply,
    _PM_RANKER_FAMILIES,
    _pm_rankers_board_block,
    _PM_MY_RANGE_RE,
    _PM_MY_LAST_RE,
    _PM_MY_NYEAR_RE,
    _PM_MY_SINCE_RE,
    _PM_MY_SPAN_RE,
    _PM_MY_YOY_RE,
    _pm_detect_multi_year_ask,
    _pm_year_files_for_subject,
    _pm_year_package_reply,
    _pm_trajectory_files,
    _pm_trajectory_metrics,
    _pm_trajectory_reply,
)
# <<reimport: core_analyze>>
from prometheus.legacy.core_analyze import (  # noqa: E402,F401  (stage 5: Page analysis over the open profile: the analyze core)
    _pm_analyze_core,
)
# <</reimport>>
# <<reimport: prometheus.library_match>>
from prometheus.library_match import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The saved-profile library as the chat reads it: the catalog, the shortlist of matches for an ask, and the gate that decides whether a match stands)
    SYNTH_CHAT_FRESH_DAYS,
    SYNTH_CHAT_MAX_CANDIDATES,
    _profile_catalog_for_chat,
    _shortlist_profile_matches,
    _synth_chat_gate,
)
# <</reimport>>
# <<reimport: prometheus.thread_history>>
from prometheus.thread_history import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Chat thread history: the per-user store, idle rotation, corrected turns, the active thread, the history route, and the thread a background job lands in)
    SYNTH_CHAT_HISTORY_KEY_PREFIX,
    _synth_chat_history_key,
    SYNTH_CHAT_THREADS_PREFIX,
    _PM_MAX_THREADS,
    _load_synth_chat_history,
    _PM_IDLE_NEW_THREAD_SECONDS,
    _pm_parse_iso,
    _pm_history_has_running_job,
    _pm_new_thread_into,
    _pm_rotate_idle_thread,
    _pm_keep_corrected_turns,
    _pm_merge_save_thread,
    _save_synth_chat_history,
    _pm_active_thread_history,
    api_synth_chat_history,
    api_prometheus_proactive,
    _PM_REQ_THREAD,
    _PM_JOB_THREAD,
    _pm_req_thread_set,
    _pm_read_job_doc,
    _pm_job_bind_thread,
    _pm_thread_for_job,
    _pm_load_thread_or_active,
    _pm_save_thread_or_active,
)
# <</reimport>>
# <<reimport: prometheus.model_chain>>
from prometheus.model_chain import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The model chain behind the chat: which model answers an analysis or a classification call, the resolved-model caches, and the JSON call every surface makes)
    _PM_ANALYZE_MODEL_ENV,
    _PM_ANALYZE_CANDIDATES,
    _pm_model_lock,
    _pm_resolved_model,
    _PM_CLASSIFY_MODEL_ENV,
    _PM_CLASSIFY_CANDIDATES,
    _PM_CLASSIFY_SURFACES,
    _PM_FAST_SURFACES,
    _pm_resolved_classify,
    _pm_model_chain,
    _pm_classify_chain,
    _pm_claude_json,
    _pm_claude_data,
)
# <</reimport>>
# <<reimport: prometheus.date_snapshot>>
from prometheus.date_snapshot import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Point-in-time snapshots: a request for the audience as it stood on a date becomes a dated cut of the existing file)
    _PM_SNAPSHOT_MONTHS,
    _pm_resolve_snapshot_date,
    _pm_promote_date_snapshot_ask,
)
# <</reimport>>
# <<reimport: prometheus.brief_prompts>>
from prometheus.brief_prompts import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The brief drafter's rulebook: the system and user prompts the interpret step sends for a profile request)
    _synth_chat_interpret_prompts,
)
# <</reimport>>
# <<reimport: prometheus.brief_draft>>
from prometheus.brief_draft import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Drafting a brief from a request: the run-time estimate, the per-subject draft and the batch fan-out over a list of subjects)
    SYNTH_CHAT_BATCH_MAX,
    _SYNTH_CHAT_INTERPRET_MODEL,
    _RUN_MINUTES_TABLE,
    _estimate_run_minutes,
    _jitter_draft_est_sample,
    _synth_chat_interpret_one_subject,
    _synth_chat_interpret_batch,
)
# <</reimport>>
# <<reimport: prometheus.answer_gate>>
from prometheus.answer_gate import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The answer gate on every chat surface: the watch notice, the ask taxonomy, the held and repeat messages, and the response unwrap)
    _pm_watch_notify,
    _PM_TAXONOMY_OVERRIDES,
    _pm_swallow,
    _pm_ask_apply_taxonomy,
    _PM_GATE_HELD_MESSAGE,
    _PM_GATE_TASK_MESSAGE,
    _PM_GATE_REPEAT_MESSAGE,
    _PM_GATE_REPEAT_PICK_MESSAGE,
    _PM_GATE_HELD_NEXT_MESSAGE,
    _PM_GATE_RETRY_BUDGET_S,
    _PM_GATE_HELD,
    _pm_unpack_resp,
    _pm_draft_decision,
    _pm_answer_gate,
)
# <</reimport>>
# <<reimport: prometheus.interpret_turn>>
from prometheus.interpret_turn import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The interpret turn: the route that drafts a brief from a chat message, its core, and the auto-start of an order the words already approve)
    api_synth_chat_interpret,
    _pm_auto_start_order,
    _pm_interpret_core,
)
# <</reimport>>
# <<reimport: prometheus.spec_from_draft>>
from prometheus.spec_from_draft import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: From an approved brief draft to the build spec the engine runs)
    _spec_from_draft,
)
# <</reimport>>
# <<reimport: prometheus.incidence_check>>
from prometheus.incidence_check import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Sizing and discovery asks in the chat: the incidence check (how big is an audience), the discovery options, the cut strategist, and the sample held to the catalog)
    _synth_chat_is_incidence_request,
    _pm_hold_sample_to_catalog,
    _pm_window_from_labels,
    _pm_window_days,
    _synth_chat_incidence_check,
    _synth_chat_is_discovery_request,
    _synth_chat_discovery_options,
    _synth_chat_cut_strategist,
)
# <</reimport>>
# <<reimport: prometheus.clarify_route>>
from prometheus.clarify_route import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The clarify route: the pricing copy and question, the Subscriber IQ notes, the manual-look heads-up, the universe phrase, and the turn that answers a chat message against the open brief)
    _PM_PRICING_COPY,
    _pm_pricing_copy,
    _PM_SUBJECT_FIELDS,
    _pm_sanitize_draft_subjects,
    _pm_subiq_platform_only_question,
    _pm_note_existing_subiq_read,
    _pm_pricing_question,
    _PM_MANUAL_LOOK_COOLDOWN_S,
    _PM_MANUAL_LOOK_STAMP_FILE,
    _pm_manual_look_stamps,
    _pm_manual_look_lock,
    _pm_universe_phrase,
    api_synth_chat_clarify,
)
# <</reimport>>
# <<reimport: prometheus.approve_route>>
from prometheus.approve_route import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Approving a brief: the window rebind, the rescue of an unverified draft, the recent-build guard, the seat and cut pricing, and the approve route with its core)
    api_synth_chat_rebind_window,
    _pm_rescue_unverified_draft,
    _PM_RECENT_BUILDS_KEY,
    _pm_recent_build_guard,
    _pm_seat_reads_included,
    _pm_cut_usd,
    _PM_NO_DRAFT_REPLY,
    api_synth_chat_approve,
    _pm_approve_core,
)
# <</reimport>>
# <<reimport: prometheus.ask_memory>>
from prometheus.ask_memory import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: What the chat remembers about an ask: the remembered question and build, the last window, the history bind, the panel refund and the panel fact answer, prior entries, two named audiences, and the persisted identity)
    _pm_remember_ask,
    _pm_remember_ask_build,
    _pm_memory_last_window,
    _pm_history_bind_text,
    _pm_panel_refund,
    _pm_panel_fact_response,
    _pm_verify_prior_entries,
    _pm_name_two_audiences,
    _pm_persist_identity,
)
# <</reimport>>
# <<reimport: prometheus.subject_verify>>
from prometheus.subject_verify import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Which subject an ask names: the pending question key, the verify cache and pre-verify, the chat subject verify, the entity bind, the plausibility check, the fuzzy catalog match with its aliases and typo tolerance, and the two-base detect)
    _PM_PENDING_Q_S3_KEY,
    _PM_VERIFY_CACHE,
    _PM_VERIFY_CACHE_LOCK,
    _PM_VERIFY_TTL_S,
    _pm_verify_cached,
    _pm_start_preverify,
    _pm_chat_subject_verify,
    _pm_entity_core_bind,
    _pm_plausible_subject,
    _PM_SUBJECT_ALIASES,
    _pm_edit_distance,
    _pm_token_typo_eq,
    _pm_expand_alias_tokens,
    _pm_fuzzy_catalog_subject,
    _PM_TWO_BASE_VOCAB_RE,
    _pm_two_base_detect,
)
# <</reimport>>
# <<reimport: prometheus.generation_base>>
from prometheus.generation_base import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The base a generated read runs on: the open profile, the named subject, or a question with no base at all)
    _pm_generation_base,
)
# <</reimport>>
# <<reimport: prometheus.generated_read>>
from prometheus.generated_read import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The generated read: the metrics response that frames a question against its base, and the read core that researches, synthesizes, verifies and banks the answer)
    _pm_generate_metrics_response,
    _pm_generate_read_core,
)
# <</reimport>>
# <<reimport: prometheus.read_jobs>>
from prometheus.read_jobs import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Background read jobs: the matrix read in pieces and its finish, the read job runner, the in-flight and recent-read checks, the notify-when-done route, the titles scope question, the held-read clarifier, and the challenged-number heads-up)
    _pm_finish_matrix_read,
    _PM_READ_PREFIX,
    _PM_READ_INFLIGHT_PREFIX,
    _pm_read_inflight_doc_key,
    _pm_read_inflight_check,
    _pm_read_recent_check,
    _pm_read_inflight_mark,
    _PM_EMAIL_RE,
    _pm_iso_now,
    _pm_history_has_job_turn,
    _pm_payload_has_figures,
    _pm_run_matrix_read,
    _GENPOP_DIMENSION_COLUMNS,
    _pm_genpop_dimension_shares,
    _pm_run_read_job,
    api_synth_chat_notify_when_done,
    _PM_COHORT_WORDS_RE,
    _PM_TITLES_SCOPE_RE,
    _PM_NAMED_SERVICE_RE,
    _pm_titles_ask_needs_scope,
    _pm_held_read_clarify,
    _PM_CHALLENGE_RES,
    _pm_challenge_headsup,
)
# <</reimport>>
# Screen warm-up route (2026-10-02 S7): /api/brief-chat/warm primes
# the digest caches when a profile loads so the first ask skips the
# five-second digest stage.
import prometheus.warm  # noqa: E402,F401
from prometheus import read_recovery as _rr  # noqa: E402  (orphaned reads)
_rr.start_background()
