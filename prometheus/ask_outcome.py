"""Failure taxonomy for every Prometheus reply (2026-10-02, Jenna RCA).

The ask log used to know two bad states, 'error' and 'declined'. A
model that returned nothing, a transport string shown as an answer, a
scaffold label leaking out of a prompt, or the same clarify sent twice
to the same person all logged as 'answered'. Carolyn's three stalled
Babylon 5 turns sit in the log as profile_build / answered.

This module classifies the reply the user actually received into one
of these outcomes (the ask log and the alert rule both read it):

    answered    a real answer or confirm card
    built       a build draft (decision names ride in `extra.decision`)
    confirmed   a confirm card for a guided pull (all fields present)
    proposed    a confirm card where one field was proposed, not typed
    clarified   a clarifying question back to the user
    clarified_repeat  the same clarify the user already received
    rerouted    a handoff to another flow (the widget runs the build
                interpret next); the blank reply is the contract
    empty       nothing the user could read
    faulted     transport / auth / scaffold text shown as content
    declined    an honest decline (credits, not quantifiable, no context)
    error       the route raised

Pure functions, no network. `classify` never raises.
"""
from __future__ import annotations

import re

ALERT_OUTCOMES = frozenset({'empty', 'faulted', 'clarified_repeat'})

# Analyze replies that hand the turn to another flow. The widget reads
# `action` / `route_hint` and calls the build interpret next, so the
# blank `reply` is the contract, not a failure (2026-10-05: Sydney's
# Soulidified build ask was classified empty, held by the answer gate,
# alerted, and the user was told to wait for an email instead of
# seeing the build card).
HANDOFF_ACTIONS = frozenset({'build_profile'})


def is_handoff(payload):
    """True when the payload routes the widget to another flow."""
    if not isinstance(payload, dict):
        return False
    if payload.get('success') is False:
        return False
    if str(payload.get('action') or '') in HANDOFF_ACTIONS:
        return True
    return bool(str(payload.get('route_hint') or '').strip())

# Strings a user must never see as the body of an answer. Transport,
# auth, Python, and HTML leakage. Matched against the whole reply when
# the reply is short, and against the first line otherwise.
_TRANSPORT_RE = re.compile(
    r'^\s*(?:not authenticated|unauthori[sz]ed|forbidden|bad request|'
    r'internal server error|bad gateway|service unavailable|gateway '
    r'timeout|request timed out|timeout|none|null|true|false|\{\}|\[\]|'
    r'undefined|nan|error|an error occurred|something went wrong)\s*\.?\s*$',
    re.IGNORECASE)
_PY_LEAK_RE = re.compile(
    r'traceback \(most recent call last\)|\b(?:key|name|type|value|'
    r'attribute|index)error\b\s*[:(]|<!doctype|<html|\bhttp\s?[45]\d\d\b|'
    r'\bstatus\s+code\s+[45]\d\d\b',
    re.IGNORECASE)
# Scaffold labels that belong to a prompt, not to a reader. House style
# allows an all-caps headed block (e.g. "SODA IS A CONQUEST CATEGORY
# HERE"), so only meta labels are flagged.
_SCAFFOLD_RE = re.compile(
    r'(?m)^\s*(?:\*\*|#+\s*)?(?:answer first|tl;?dr|so what|evidence|'
    r'reasoning|thinking|analysis plan|system prompt|assistant:|user:|'
    r'\[inst\]|<\/?(?:system|user_request|prior_chat_context)>)'
    r'\s*[:\-]?\s*(?:\*\*)?\s*$',
    re.IGNORECASE)
# A scold or a debugging question is never an answer (Jenna 2026-10-21,
# Jay Patel was told "I have answered this twice and it has not landed, so
# I will stop repeating myself. Tell me which part is off"). Wording of
# that family is a scaffold: the gate holds it and the calm promise ships.
_SCOLD_RE = re.compile(
    r"\b(?:i have answered (?:this|that) (?:twice|already|before|once)|i (?:already )?answered (?:this|that) (?:twice|already|before)|"
    r"stop repeating myself|repeating myself|i will not ask again|i won'?t ask again|which part is off|"
    r"tell me which part|i could not place that answer|i have told you|as i (?:said|told you) (?:before|already|twice)|"
    r"(?:you|you've|you have) (?:already )?asked (?:this|that|me)(?: twice| already| before| again)|"
    r"i will stop (?:asking|repeating))\b", re.IGNORECASE)
# The intake / clarify shapes the guided pulls and the base-clarify
# path send back.
_CLARIFY_RE = re.compile(
    r"^\s*(?:almost there|happy to (?:build|run|set up|value)|"
    r"give me,? in one message|which (?:one|profile|title|campaign)|"
    r"did you (?:want|mean)|do you (?:want|mean)|before i (?:answer|run)|"
    r"i still need|one more thing|which ones)\b",
    re.IGNORECASE)
_PROPOSED_RE = re.compile(r'you did not spell out', re.IGNORECASE)
_CONFIRM_RE = re.compile(
    r"here(?:'|\u2019)s the (?:digital journey|flywheel|brand partnership|"
    r"attribution|profile|subscriber iq)|say ['\u2018\u2019\"]?run the",
    re.IGNORECASE)


def _norm(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


def reply_text(payload):
    """The text the user read, from any of the reply shapes."""
    if not isinstance(payload, dict):
        return ''
    for k in ('reply', 'answer', 'message', 'text'):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # a clarify or a guidance line on the build surface rides as
    # guidance + error text (2026-10-13 audit: 75 of 77 clarified asks
    # logged no reply, so the morning review could not see what was
    # asked back)
    if payload.get('guidance') or payload.get('clarify') or payload.get('question'):
        for k in ('error', 'question', 'clarify', 'prompt'):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v
    # the sample check and guided discovery answer from their own
    # block (2026-10-13: the digest read "No written answer on this
    # step" for a sample check the user had read in full)
    for k in ('incidence_check', 'discovery'):
        block = payload.get(k)
        if isinstance(block, dict):
            v = block.get('message')
            if isinstance(v, str) and v.strip():
                return v
    raw = payload.get('raw')
    if isinstance(raw, dict):
        return reply_text(raw)
    # a brief card has no prose; the log keeps what the card said
    # (2026-10-13 audit: the digest read "No written answer" for every
    # drafted build)
    drafts = payload.get('spec_drafts') or ([payload['spec_draft']] if isinstance(payload.get('spec_draft'), dict) else [])
    lines = []
    for d in drafts if isinstance(drafts, list) else []:
        if not isinstance(d, dict):
            continue
        subj = str(d.get('subject') or d.get('universe_label') or '').strip()
        dec = str(d.get('decision') or 'new_build').replace('_', ' ')
        cred = d.get('estimated_credits')
        lines.append(f"Brief drafted: {subj or 'this audience'} ({dec}"
                     + (f", {cred} credits" if cred not in (None, '') else '') + ')')
    return '; '.join(lines)


def last_agent_text(history):
    """Most recent agent turn in a widget history list."""
    try:
        for h in reversed(list(history or [])):
            if not isinstance(h, dict):
                continue
            if str(h.get('role') or '').lower() in ('agent', 'assistant'):
                return str(h.get('text') or h.get('content') or '')
    except Exception:
        pass
    return ''


def is_faulted_text(text):
    """Transport, auth, Python, or HTML leakage, or a scaffold label."""
    t = str(text or '').strip()
    if not t:
        return False
    first = t.splitlines()[0] if t else ''
    if _TRANSPORT_RE.match(t) or _TRANSPORT_RE.match(first):
        return True
    if _PY_LEAK_RE.search(t[:2000]):
        return True
    if _SCAFFOLD_RE.search(t[:600]):
        return True
    if _SCOLD_RE.search(t[:800]):
        return True
    return False


def is_clarify_text(text):
    return bool(_CLARIFY_RE.match(str(text or '').strip()))


def classify(surface, payload, status_code=200, history=None,
             base_outcome=None):
    """(outcome, detail) for the reply the user received.

    `base_outcome` is the legacy inference (decision names, declines);
    it is kept when nothing in the reply contradicts it. `history` is
    the widget history list sent with the ask, used to see whether
    the same clarify went out on the previous turn."""
    try:
        detail = {}
        if not isinstance(payload, dict):
            return ('error' if (status_code or 200) >= 500 else
                    (base_outcome or 'unknown')), detail
        if payload.get('held') and payload.get('guidance'):
            return 'held', {'promise': True}   # the calm promise on a repeat (turn_gate.calm_hold)
        if payload.get('guidance') and (payload.get('read_job_id') or payload.get('job_id')):
            return 'answered', {'async': True}   # a read promise shaped for the build flow (service._interpret_shape)
        if payload.get('success') is False:
            err = str(payload.get('error') or '')
            if base_outcome and base_outcome.startswith('declined'):
                return base_outcome, detail
            if payload.get('guidance') or (status_code == 402):
                return 'declined', detail
            detail['error'] = err[:200]
            return 'error', detail
        text = reply_text(payload)
        has_draft = bool(payload.get('spec_draft') or payload.get('spec_drafts'))
        has_card = bool(payload.get('draft') and payload.get('next_step'))
        has_structured = any(payload.get(k) for k in (
            'incidence_check', 'discovery', 'csv_url', 'download_url',
            'deck_url', 'blocks', 'table', 'chart'))
        if has_draft:
            if base_outcome and base_outcome not in ('answered', 'unknown',
                                                     'draft'):
                detail['decision'] = base_outcome
            return 'built', detail
        if is_handoff(payload) and not is_faulted_text(text):
            detail['handoff'] = (str(payload.get('route_hint') or '').strip()
                                 or 'interpret')
            return 'rerouted', detail
        if not text.strip():
            if has_card or has_structured:
                return (base_outcome or 'answered'), detail
            if payload.get('job_id') or payload.get('read_job_id') \
                    or payload.get('pending'):
                return 'answered', {'async': True}
            return 'empty', detail
        if is_faulted_text(text):
            detail['sample'] = text.strip()[:160]
            return 'faulted', detail
        if _PROPOSED_RE.search(text):
            return 'proposed', detail
        if _CONFIRM_RE.search(text[:400]):
            return 'confirmed', detail
        if has_card or is_clarify_text(text):
            prev = last_agent_text(history)
            if prev and _norm(prev)[:160] == _norm(text)[:160]:
                detail['sample'] = text.strip()[:160]
                return 'clarified_repeat', detail
            return 'clarified', detail
        if base_outcome and base_outcome.startswith('declined'):
            return base_outcome, detail
        return 'answered', detail
    except Exception:
        return (base_outcome or 'unknown'), {}


def route_pick(hint, inferred):
    """The route the ask log keeps. The door presets
    'prometheus/ask:<surface>' before a core runs; that is a
    placeholder, so a route the body itself names (incidence,
    discovery, clarify, profile_build) wins over it (2026-10-13: a
    sample check logged as the placeholder, and the digest could not
    tell what the reader got). A lane's own hint always wins."""
    hint = str(hint or '')
    if hint and not hint.startswith('prometheus/ask:'):
        return hint
    if inferred and inferred != 'unknown':
        return inferred
    return hint or inferred


# ---------------------------------------------------------------------
# Ask-log inference (moved out of prometheus/legacy/chat.py 2026-10-20):
# best-effort route / outcome / subject from a response body, and the
# views where the subject describes what the ask is ABOUT. Pure text.
# ---------------------------------------------------------------------
def _ask_infer_route_outcome(surface, payload, status_code):
    """Best-effort (route, outcome, subject) from a response body."""
    route, outcome, subject = 'unknown', 'unknown', None
    if not isinstance(payload, dict):
        return route, outcome, subject
    if surface == 'interpret':
        route = 'profile_build'
        if 'incidence_check' in payload:
            route, outcome = 'incidence', 'answered'
            subject = (payload.get('incidence_check') or {}).get('subject')
        elif 'discovery' in payload:
            route, outcome = 'discovery', 'answered'
        elif 'spec_drafts' in payload:
            outcome = 'batch_draft'
        elif 'spec_draft' in payload:
            draft = payload.get('spec_draft') or {}
            outcome = str(draft.get('decision') or 'draft')[:40]
            subject = draft.get('subject') or draft.get('subject_label')
        elif 'draft' in payload and payload.get('next_step'):
            route, outcome = 'clarify', 'clarify'
        elif payload.get('success') is False:
            outcome = ('declined' if payload.get('guidance') else 'error')
        return route, outcome, subject
    if surface == 'deck':   # the deck surface: a queued file, a clarify, or a decline (2026-10-13)
        if payload.get('job_id'):
            return 'deck', 'queued', payload.get('subject')
        return 'deck', ('clarified' if payload.get('clarify') or payload.get('memory_confirm')
                        else ('declined' if payload.get('success') is False else 'answered')), subject
    # analyze surface
    route = 'page_analysis'
    if payload.get('success') is False:
        err = str(payload.get('error') or '').lower()
        if status_code == 402 or 'credit' in err:
            outcome = 'declined_credits'
        else:
            outcome = 'error'
        return route, outcome, subject
    action = str(payload.get('action') or '').strip().lower()
    reply = str(payload.get('reply') or '')
    subject = payload.get('profile')
    if action == 'build_profile':
        route, outcome = 'profile_build', 'answered'
    elif payload.get('not_quantifiable'):
        route, outcome = 'quantifiability_gate', 'declined_not_quantifiable'
    elif reply.startswith('Nothing is open to analyze yet'):
        outcome = 'declined_no_context'
    else:
        outcome = 'answered'
    return route, outcome, subject


# Views where the subject describes what the ask is ABOUT. Elsewhere
# the handlers still resolve a subject in order to answer (page
# context, cross-session memory), but it is whatever the reader had
# open last rather than the topic of this question.
_ASK_SUBJECT_VIEWS = {'profileIQ', 'subscriberIQ', 'journeyIQ'}


_ASK_SUBJ_STOP = {'the', 'a', 'an', 'and', 'of', 'show', 'series',
                  'movie', 'audience', 'fans', 'viewers', 'profile'}


def _ask_mentions_subject(question, subject):
    """The ask names the subject, so recording it is meaningful even
    on a view that does not own one ("how is Shark Tank trending" in
    Trends IQ). Any distinctive subject token is enough."""
    try:
        q = re.sub(r'[^a-z0-9 ]+', ' ', str(question or '').lower())
        toks = [t for t in re.sub(r'[^a-z0-9 ]+', ' ',
                                  str(subject or '').lower()).split()
                if len(t) >= 4 and t not in _ASK_SUBJ_STOP]
        return any(t in q for t in toks)
    except Exception:
        return True


_JOB_KEYS = ('read_job_id', 'deck_job_id', 'bpiq_job_id', 'jiq_job_id', 'fw_job_id', 'aiq_job_id', 'tlist_job_id', 'job_id', 'run_id')


def promised_job(payload):
    """The job id a reply promised (I4, 2026-10-20), or ''. A golden
    probe's pseudo id is not a promise."""
    if not isinstance(payload, dict):
        return ''
    for k in _JOB_KEYS:
        v = payload.get(k)
        if v and isinstance(v, (str, int)) and not str(v).startswith('golden-'):
            return str(v)[:40]
    return ''
