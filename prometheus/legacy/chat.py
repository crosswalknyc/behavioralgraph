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


# <<extracted: prometheus.correction_page block 1>>


# <<extracted: prometheus.correction_page block 2>>


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


# <<extracted: prometheus.deck_route block 1>>


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
# <<extracted: prometheus.file_replies block 1>>


# <<extracted: prometheus.file_replies block 2>>


# <<extracted: prometheus.file_replies block 3>>


# <<extracted: prometheus.file_replies block 4>>


# <<extracted: prometheus.file_replies block 5>>


# <<extracted: prometheus.feedback_bank block 20>>


# <<extracted: prometheus.file_replies block 6>>


# <<extracted: prometheus.file_replies block 7>>


# <<extracted: prometheus.file_replies block 8>>


# <<extracted: prometheus.file_replies block 9>>


# <<extracted: prometheus.file_replies block 10>>


# <<extracted: prometheus.file_replies block 11>>


# <<extracted: prometheus.file_replies block 12>>


# <<extracted: prometheus.file_replies block 13>>


# <<extracted: prometheus.file_replies block 14>>


# <<extracted: prometheus.file_replies block 15>>


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


# <<extracted: prometheus.deck_route block 2>>


# <<extracted: prometheus.product_orders block 1>>


# <<extracted: prometheus.product_orders block 2>>


# <<extracted: prometheus.product_orders block 3>>


# <<extracted: prometheus.product_orders block 4>>


# <<extracted: prometheus.product_orders block 5>>


# <<extracted: prometheus.product_orders block 6>>


# <<extracted: prometheus.product_orders block 7>>


# <<extracted: prometheus.product_orders block 8>>


# <<extracted: prometheus.product_orders block 9>>


# <<extracted: prometheus.product_orders block 10>>


# <<extracted: prometheus.product_orders block 11>>


# <<extracted: prometheus.product_orders block 12>>


# <<extracted: prometheus.product_orders block 13>>


# <<extracted: prometheus.product_orders block 14>>


# <<extracted: prometheus.product_orders block 15>>


# <<extracted: prometheus.product_orders block 16>>


# <<extracted: prometheus.product_orders block 17>>


# <<extracted: prometheus.product_orders block 18>>


# <<extracted: prometheus.product_orders block 19>>


# <<extracted: prometheus.product_orders block 20>>


# <<extracted: prometheus.product_orders block 21>>


# <<extracted: prometheus.product_orders block 22>>


# <<extracted: prometheus.product_orders block 23>>


# <<extracted: prometheus.product_orders block 24>>


# <<extracted: prometheus.product_orders block 25>>


# <<extracted: prometheus.product_orders block 26>>


# <<extracted: prometheus.product_orders block 27>>


# <<extracted: prometheus.product_orders block 28>>


# <<extracted: prometheus.product_orders block 29>>


# <<extracted: prometheus.product_orders block 30>>


# <<extracted: prometheus.product_orders block 31>>


# <<extracted: prometheus.product_orders block 32>>


# <<extracted: prometheus.product_orders block 33>>


# <<extracted: prometheus.product_orders block 34>>


# <<extracted: prometheus.product_orders block 35>>


# <<extracted: prometheus.product_orders block 36>>


# <<extracted: prometheus.product_orders block 37>>


# <<extracted: prometheus.product_orders block 38>>


# <<extracted: prometheus.product_orders block 39>>


from prometheus.aiq_assets import ASK_COPY as _PM_AIQ_ASK_COPY  # noqa: E402


# <<extracted: prometheus.product_orders block 40>>


# <<extracted: prometheus.product_orders block 41>>


# <<extracted: prometheus.product_orders block 42>>


# <<extracted: prometheus.product_orders block 43>>


# <<extracted: prometheus.product_orders block 44>>


# <<extracted: prometheus.product_orders block 45>>


# <<extracted: prometheus.product_orders block 46>>


# <<extracted: prometheus.deck_route block 3>>


# <<extracted: prometheus.deck_route block 4>>


# <<extracted: prometheus.deck_route block 5>>


# <<extracted: prometheus.deck_route block 6>>


# <<extracted: prometheus.deck_route block 7>>


# <<extracted: prometheus.deck_route block 8>>


# <<extracted: prometheus.deck_route block 9>>


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
    '_super_admin_gate', 'admin_prometheus_chat',
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
    _super_admin_gate,
    admin_prometheus_chat,
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
# <<reimport: prometheus.file_replies>>
from prometheus.file_replies import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Files in the chat: the data file name and payload, the replayed file, the email-the-file intent and response, the CSV points and uploads, the page file, the last thing delivered, the CSV download, and the search demand response)
    _PM_DATA_FILE_PREFIX,
    _pm_csv_task_filename,
    _pm_answer_file_payload,
    _pm_replay_file,
    _PM_EMAIL_FILE_RE,
    _pm_email_file_intent,
    _pm_email_file_response,
    _pm_csv_point,
    _pm_upload_csv,
    _pm_entry_csv,
    _pm_page_file,
    _pm_last_thing,
    _pm_csv_download_response,
    _pm_search_demand_response,
)
# <</reimport>>
# <<reimport: prometheus.product_orders>>
from prometheus.product_orders import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Product orders placed in the chat: the Brand Partnership valuation, the Journey IQ study, the Flywheel and the Attribution IQ tracking pull, each with its intent, its parse, its confirm and its background job)
    _PM_BPIQ_JOB_PREFIX,
    _PM_BPIQ_CHIP,
    _PM_BPIQ_CREDITS,
    _PM_BPIQ_ASK_COPY,
    _pm_read_intent,
    _pm_bpiq_intent,
    _pm_bpiq_parse,
    _pm_bpiq_flight_research,
    _pm_bpiq_inputs_complete,
    _pm_bpiq_confirm_reply,
    _pm_run_bpiq_job,
    _PM_JIQ_JOB_PREFIX,
    _PM_JIQ_CHIP,
    _PM_JIQ_CREDITS,
    _PM_JIQ_ASK_COPY,
    _pm_jiq_intent,
    _pm_jiq_parse,
    _pm_jiq_inputs_complete,
    _pm_jiq_is_ticketing,
    _pm_jiq_ready_message,
    _pm_jiq_confirm_reply,
    _pm_run_jiq_job,
    _pm_price_sentence,
    _pm_included_seat,
    _pm_tool_price_label,
    _PM_FW_JOB_PREFIX,
    _PM_FW_CHIP,
    _PM_FW_CREDITS,
    _PM_FW_ASK_COPY,
    _pm_fw_intent,
    _pm_fw_parse,
    _pm_fw_inputs_complete,
    _pm_fw_confirm_reply,
    _pm_engine_failure_alert,
    _pm_run_fw_job,
    _PM_AIQ_JOB_PREFIX,
    _PM_AIQ_CHIP,
    _PM_AIQ_SETUP_CREDITS,
    _PM_AIQ_DAILY_CREDITS,
    _pm_aiq_intent,
    _pm_aiq_stop_intent,
    _pm_aiq_parse,
    _pm_aiq_days,
    _pm_aiq_inputs_complete,
    _pm_aiq_confirm_reply,
    _pm_run_aiq_job,
)
# <</reimport>>
# <<reimport: prometheus.deck_route>>
from prometheus.deck_route import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: Decks from the chat: the fuzzy subject suggestions, the deck subject resolve, the deck job and its route, the deck core, and the pay-per-use route)
    _PM_DECK_PREFIX,
    _PM_DECK_FILE_PREFIX,
    _pm_deck_fuzzy_suggestions,
    _pm_resolve_deck_subject,
    _PM_DECK_PPU_EXTRAS,
    _pm_run_deck_job,
    api_synth_chat_deck,
    _pm_deck_core,
    api_synth_chat_pay_per_use,
)
# <</reimport>>
# <<reimport: prometheus.correction_page>>
from prometheus.correction_page import (  # noqa: E402,F401  (rebuild stage 7, 2026-11-23: The page a staff correction link opens: the HTML shell and its escape)
    _pm_correct_page,
    _pm_correct_esc,
)
# <</reimport>>
# Screen warm-up route (2026-10-02 S7): /api/brief-chat/warm primes
# the digest caches when a profile loads so the first ask skips the
# five-second digest stage.
import prometheus.warm  # noqa: E402,F401
from prometheus import read_recovery as _rr  # noqa: E402  (orphaned reads)
_rr.start_background()
