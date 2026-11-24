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


# <<extracted: prometheus.page_analysis block 1>>


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


# <<extracted: prometheus.intake_turn block 1>>

# <<extracted: prometheus.intake_turn block 2>>

# <<extracted: prometheus.intake_turn block 3>>


# <<extracted: prometheus.intake_turn block 4>>


# <<extracted: prometheus.intake_turn block 5>>


from prometheus.intake_reader import missing_labels as _ir_labels  # noqa: E402  (plain words for missing intake fields)


# <<extracted: prometheus.intake_turn block 6>>


# <<extracted: prometheus.intake_turn block 7>>


# <<extracted: prometheus.intake_turn block 8>>


# <<extracted: prometheus.intake_turn block 9>>


# <<extracted: prometheus.intake_turn block 10>>


# <<extracted: prometheus.intake_turn block 11>>
# <<extracted: prometheus.intake_turn block 12>>
# <<extracted: prometheus.intake_turn block 13>>


# <<extracted: prometheus.intake_turn block 14>>


# <<extracted: prometheus.intake_turn block 15>>


# <<extracted: prometheus.intake_turn block 16>>



# <<extracted: prometheus.intake_turn block 17>>


# <<extracted: prometheus.intake_turn block 18>>


# <<extracted: prometheus.intake_turn block 19>>


# <<extracted: prometheus.intake_turn block 20>>


# <<extracted: prometheus.intake_turn block 21>>


# <<extracted: prometheus.intake_turn block 22>>


# <<extracted: prometheus.page_context block 1>>


# <<extracted: prometheus.page_context block 2>>


# <<extracted: prometheus.page_context block 3>>


# <<extracted: prometheus.page_context block 4>>


# <<extracted: prometheus.page_context block 5>>


# <<extracted: prometheus.page_context block 6>>


# <<extracted: prometheus.page_context block 7>>


# <<extracted: prometheus.page_context block 8>>


# <<extracted: prometheus.page_context block 9>>


# <<extracted: prometheus.page_context block 10>>


# <<extracted: prometheus.page_context block 11>>


# <<extracted: prometheus.ask_intercepts block 1>>


# <<extracted: prometheus.ask_intercepts block 2>>


# <<extracted: prometheus.ask_intercepts block 3>>


# <<extracted: prometheus.ask_intercepts block 4>>


# <<extracted: prometheus.ask_intercepts block 5>>


# <<extracted: prometheus.ask_intercepts block 6>>


# <<extracted: prometheus.ask_intercepts block 7>>


# <<extracted: prometheus.ask_intercepts block 8>>


# <<extracted: prometheus.ask_intercepts block 9>>


# <<extracted: prometheus.ask_intercepts block 10>>


# <<extracted: prometheus.ask_intercepts block 11>>


# <<extracted: prometheus.ask_intercepts block 12>>


# <<extracted: prometheus.ask_intercepts block 13>>


# <<extracted: prometheus.ask_intercepts block 14>>


# <<extracted: prometheus.ask_intercepts block 15>>

# <<extracted: prometheus.ask_intercepts block 16>>


from prometheus import kpi_definitions as _kpi  # noqa: E402


# <<extracted: prometheus.page_context block 12>>


# <<extracted: prometheus.page_context block 13>>


# <<extracted: prometheus.page_context block 14>>


# <<extracted: prometheus.ask_intercepts block 17>>


# <<extracted: prometheus.ask_intercepts block 18>>


# <<extracted: prometheus.ask_intercepts block 19>>


# <<extracted: prometheus.ask_intercepts block 20>>


# <<extracted: prometheus.ask_intercepts block 21>>


# <<extracted: prometheus.ask_intercepts block 22>>


# <<extracted: prometheus.ask_intercepts block 23>>


# <<extracted: prometheus.ask_intercepts block 24>>


# <<extracted: prometheus.ask_intercepts block 25>>


# <<extracted: prometheus.ask_intercepts block 26>>


# <<extracted: prometheus.ask_intercepts block 27>>


# <<extracted: prometheus.ask_intercepts block 28>>


# <<extracted: prometheus.ask_intercepts block 29>>


# <<extracted: prometheus.feedback_bank block 1>>


# <<extracted: prometheus.feedback_bank block 2>>


# <<extracted: prometheus.feedback_bank block 3>>


# <<extracted: prometheus.feedback_bank block 4>>


# <<extracted: prometheus.feedback_bank block 5>>


# <<extracted: prometheus.feedback_bank block 6>>


# <<extracted: prometheus.feedback_bank block 7>>


# <<extracted: prometheus.feedback_bank block 8>>


# <<extracted: prometheus.feedback_bank block 9>>


# <<extracted: prometheus.feedback_bank block 10>>


# <<extracted: prometheus.feedback_bank block 11>>


# <<extracted: prometheus.feedback_bank block 12>>


# <<extracted: prometheus.feedback_bank block 13>>


# <<extracted: prometheus.feedback_bank block 14>>


# <<extracted: prometheus.feedback_bank block 15>>


# <<extracted: prometheus.feedback_bank block 16>>


# <<extracted: prometheus.feedback_bank block 17>>


# <<extracted: prometheus.feedback_bank block 18>>


# <<extracted: prometheus.feedback_bank block 19>>


# <<extracted: prometheus.ask_intercepts block 30>>


# <<extracted: prometheus.ask_intercepts block 31>>


# <<extracted: prometheus.ask_intercepts block 32>>


# <<extracted: prometheus.ask_intercepts block 33>>


# <<extracted: prometheus.access_gates block 1>>


# <<extracted: prometheus.access_gates block 2>>


# <<extracted: prometheus.access_gates block 3>>


# <<extracted: prometheus.access_gates block 4>>


# <<extracted: prometheus.access_gates block 5>>


# <<extracted: prometheus.access_gates block 6>>


# <<extracted: prometheus.access_gates block 7>>


# <<extracted: prometheus.base_clarify block 1>>


# <<extracted: prometheus.base_clarify block 2>>


# One copy of the subject-position rules: prometheus/named_subject.py.
from prometheus import named_subject as _ns_mod  # noqa: E402
# <<extracted: prometheus.base_clarify block 3>>
# <<extracted: prometheus.base_clarify block 4>>
# <<extracted: prometheus.base_clarify block 5>>


# <<extracted: prometheus.base_clarify block 6>>


# <<extracted: prometheus.base_clarify block 7>>


# <<extracted: prometheus.base_clarify block 8>>


# <<extracted: prometheus.base_clarify block 9>>

# <<extracted: prometheus.base_clarify block 10>>


# <<extracted: prometheus.base_clarify block 11>>


# <<extracted: prometheus.base_clarify block 12>>


# <<extracted: prometheus.base_clarify block 13>>


# <<extracted: prometheus.base_clarify block 14>>


# <<extracted: prometheus.screen_bind block 1>>


# <<extracted: prometheus.screen_bind block 2>>


# <<extracted: prometheus.screen_bind block 3>>


# <<extracted: prometheus.screen_bind block 4>>


# <<extracted: prometheus.screen_bind block 5>>


# <<extracted: prometheus.screen_bind block 6>>


# <<extracted: prometheus.screen_bind block 7>>


# <<extracted: prometheus.screen_bind block 8>>


# <<extracted: prometheus.screen_bind block 9>>


# <<extracted: prometheus.screen_bind block 10>>


# <<extracted: prometheus.screen_bind block 11>>


# <<extracted: prometheus.screen_bind block 12>>


# <<extracted: prometheus.screen_bind block 13>>


# <<extracted: prometheus.screen_bind block 14>>


# <<extracted: prometheus.screen_bind block 15>>


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


# <<extracted: prometheus.feedback_bank block 20>>


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


# <<extracted: prometheus.page_analysis block 2>>


# <<extracted: prometheus.page_analysis block 3>>


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
# <<reimport: prometheus.page_context>>
from prometheus.page_context import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The page the chat is looking at: the compact numbers and the hydrated view behind an ask, the primary key for a name, the page context check, the meter and hints, the analyze and pull gates, and the KPI view helpers)
    _PM_INTENT_HYDRATE_CACHE,
    _pm_intent_compact_numbers,
    _pm_intent_view_hydrate,
    _PM_SELF_CONTAINED_VIEWS,
    _pm_primary_key_for_name,
    _pm_validate_page_context,
    _pm_meter_answer,
    _pm_ask_hint,
    _pm_ask_stage,
    _pm_gate_analyze,
    _pm_gate_pull,
    _pm_kpi_view,
    _pm_kpi_definition_for,
    _pm_kpi_prompt_extras,
)
# <</reimport>>
# <<reimport: prometheus.base_clarify>>
from prometheus.base_clarify import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Which audience a question is about when the words do not say: the base clarify tokens and questions, the file ask, the clarify turn and its decline, answer-this-now, and the merge of a clarify answer into the ask)
    _PM_BASE_GENERIC_TOKENS,
    _PM_CLARIFY_STOP_TOKENS,
    _PM_CLARIFY_NAME_RES,
    _PM_CLARIFY_COMPARE_RE,
    _PM_CLARIFY_METRIC_RE,
    _pm_ask_names_its_audiences,
    _PM_FILE_ASK_RE,
    _PM_CLARIFY_TURN_RE,
    _PM_CLARIFY_DECLINE_WORDS,
    _PM_ANSWER_NOW_RE,
    _pm_answer_now_strip,
    _pm_answer_now_active,
    _pm_clarify_declined,
    _pm_clarify_answer_merge,
)
# <</reimport>>
# <<reimport: prometheus.screen_bind>>
from prometheus.screen_bind import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Whether an ask points at the open screen: the deixis, shape and scope patterns, the screen bind verdict, the view vocabulary, foreign names, whether the view owns the ask, the open-screen confirm, and the short-name identity)
    _PM_SCREEN_DEIXIS_RE,
    _PM_PROFILE_SHAPE_RE,
    _PM_MARKET_SCOPE_RE,
    _PM_DEFINITE_REF_RE,
    _pm_screen_bind_verdict,
    _PM_VIEW_DEIXIS_RE,
    _PM_CAMPAIGN_ASK_RE,
    _PM_FUNNEL_ASK_RE,
    _PM_VIEW_VOCAB_STOP,
    _pm_view_vocab_hit,
    _pm_foreign_names,
    _pm_view_owns_ask,
    _pm_open_screen_confirm,
    _pm_page_brand_named,
    _pm_short_name_identity,
)
# <</reimport>>
# <<reimport: prometheus.page_analysis>>
from prometheus.page_analysis import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Page analysis over the open profile: the ask log decorator, the analyze route and its core)
    _ask_logged,
    api_synth_chat_analyze,
    _pm_analyze_core,
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
# <<reimport: prometheus.intake_turn>>
from prometheus.intake_turn import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The product intake in the chat: the stalled and resumed intake, the breakout, the collect flags, the resolve and finish, the intake turn, the pending intake, the page question, the screen draft, the flow table, and the internal canary route)
    _PM_INTAKE_STALL_PREFIX,
    _PM_INTAKE_AGENT_PREFIXES,
    _PM_INTAKE_FAULT_REPLY,
    _pm_intake_prior_user_turns,
    _pm_intake_last_agent_stalled,
    _pm_intake_breakout,
    _pm_intake_resume_reply,
    _pm_collect_flag_applies,
    _pm_intake_resolve,
    _pm_intake_finish_confirm,
    _PM_INTAKE_UI,
    _pm_twin_confirm_reply,
    _pm_twin_intent,
    _pm_intake_turn,
    _pm_pending_intake,
    _pm_prior_journey_shape,
    _PM_PAGE_DEIXIS_RE,
    _pm_intake_page_question,
    _pm_intake_fault_payload,
    _pm_intake_screen_draft,
    _pm_intake_flow_table,
    api_internal_intake_canary,
)
# <</reimport>>
# <<reimport: prometheus.ask_intercepts>>
from prometheus.ask_intercepts import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Short answers before any work starts: the garbled-input normalization, the numeric follow-up, the status check on a running build, the typed approval with no brief, the work-order verbs (status, ETA, cancel), the Subscriber IQ lookup, and the cut-request intercept)
    _PM_SPLIT_DIGIT_RE,
    _pm_normalize_ask,
    _PM_US_SHARE_RE,
    _PM_COUNT_IN_REPLY_RE,
    _pm_parse_count,
    _pm_numeric_followup_reply,
    _PM_STATUS_ASK_RES,
    _PM_STATUS_TAIL_TOKENS,
    _pm_status_ask_subject,
    _pm_status_matching_runs,
    _pm_status_reply_for_runs,
    _PM_STATUS_LEAD_TOKENS,
    _pm_library_match,
    _pm_status_lane,
    _PM_APPROVAL_WORD_RE,
    _PM_APPROVE_NO_DRAFT_COPY,
    _pm_last_agent_asked,
    _pm_approval_word,
    _PM_WO_WORK_NOUNS,
    _PM_WO_STATUS_RES,
    _PM_WO_ETA_RES,
    _PM_WO_CANCEL_RE,
    _pm_subiq_lookup_answer,
    _pm_workorder_intent,
    _pm_user_runs,
    _pm_typical_build_minutes,
    _pm_eta_line,
    _pm_cancel_target,
    _pm_workorder_reply,
    _PM_CUT_INTENT_RE,
    _PM_CUT_COHORT_RE,
    _PM_CUT_IDIOM_RE,
    _pm_text_names_catalog_subject,
)
# <</reimport>>
# <<reimport: prometheus.feedback_bank>>
from prometheus.feedback_bank import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: What a reader says back: the email-when-ready and wrong-answer intercepts, the repeat and rejected-answer replays, the refusal reader, the feedback forward, the regression bank, the cannot-do lane, and the build notify registration)
    _PM_EMAIL_ADDR_RE,
    _PM_EMAIL_WHEN_READY_RE,
    _PM_WRONG_ANSWER_RES,
    _pm_prev_user_question,
    _pm_replay_repeat_block,
    _pm_replay_rejected_in_thread,
    _PM_REFUSAL_RX,
    _pm_reads_as_refusal,
    _PM_FEEDBACK_RES,
    _PM_METHOD_CHALLENGE_RES,
    _PM_FEEDBACK_SENT,
    _PM_REGRESSION_CASES_KEY,
    _pm_regression_q_key,
    _pm_compact_for_bank,
    _pm_bank_regression_case,
    _pm_forward_user_feedback,
    _pm_cant_do_lane,
    _PM_BUILD_NOTIFY_PREFIX,
    _pm_register_build_notify,
)
# <</reimport>>
# <<reimport: prometheus.access_gates>>
from prometheus.access_gates import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Who may ask and who pays: the gate refusal, the access gate, the funding check and funds gate, and the usage and attribution extras merged onto a call)
    _pm_gate_refusal,
    _pm_access_gate,
    _pm_has_funding,
    _pm_funds_gate,
    _pm_usage_extras,
    _pm_attrib_extras,
    _pm_merge_extras,
)
# <</reimport>>
# Screen warm-up route (2026-10-02 S7): /api/brief-chat/warm primes
# the digest caches when a profile loads so the first ask skips the
# five-second digest stage.
import prometheus.warm  # noqa: E402,F401
from prometheus import read_recovery as _rr  # noqa: E402  (orphaned reads)
_rr.start_background()
