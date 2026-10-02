"""The one server-side decision step.

``decide`` reads a fresh ask plus the facts a client can report (is a
profile open, how many tabs, an explicit mode or confirm step) and
returns which surface runs it and why. It replaces the detector chain
the dashboard widget used to run in the browser, in the same order, so
the dashboard, the standalone app, and the API all route an ask the
same way and a fix here reaches every client at once.

Pure and fail-safe: every detector is wrapped, and the fallback is the
interpret flow (which carries its own router and clarify paths), never
an exception. Composes the detectors that already live server-side
(``prometheus_analysis``, ``subiq_intent``) and the four tool-intake
intents bound through ``host``.

Decision shape::

    {'surface': 'analyze' | 'interpret' | 'deck',
     'mode': str | None,        # analyze mode when one applies
     'reason': str,             # short machine-readable why
     'client_hint': str | None} # dashboard-only UI step, if any

``client_hint`` values ('analyze_menu', 'compare_open_tabs',
'compare_picker') name interactive pickers the dashboard can show. A
client without that UI ignores the hint and runs the surface as is.
"""
import re

from . import guards
from .host import host

ANALYZE_MODES = ('exec_summary', 'whitespace', 'new_consumers', 'personas',
                 'easter_eggs', 'convergences', 'linkedin_post', 'full',
                 'search_demand')

# Explicit confirm / input steps the dashboard posts straight to the
# analyze surface. Their presence is the decision.
_ANALYZE_EXTRA_KEYS = ('bind_subject', 'bind_cohort', 'overall_ranks',
                       'panel_confirm', 'bpiq_inputs', 'bpiq_confirm',
                       'jiq_inputs', 'jiq_confirm', 'fw_inputs',
                       'fw_confirm', 'aiq_inputs', 'aiq_confirm')

_BUILD_VERB_RX = re.compile(
    r'\b(run|build|pull|create|queue|start|launch|generate)\b[^.]{0,60}'
    r'\bprofiles?\b', re.I)
_CUT_BY_RX = re.compile(r'\b(cut|slice)\b[^.]{0,40}\bby\b', re.I)
_OPS_WORDS_RX = re.compile(
    r'\brefresh\b|\bsample size\b|\bincidence\b|\bstatus\b|\bcredits?\b',
    re.I)
_QUESTION_MARK_RX = re.compile(r'\?\s*$')
_QUESTION_OPEN_RX = re.compile(
    r'^(what|which|who|whose|how|why|where|when|do|does|did|are|is|was|'
    r'were|can|could|would|should|show me|tell me|give me|compare|'
    r'analy[sz]e|top \d|break ?down)\b', re.I)

_DECK_BUILD_RX = re.compile(
    r'\b(build|make|create|generate|put together|spin up|prepare|draft)\b'
    r'[^.!?]{0,60}\b(deck|slides|presentation|one[- ]?pagers?|pptx)\b', re.I)
_DECK_NOUN_RX = re.compile(
    r'\binsights? deck\b|\b(pitch|talent[- ]value|audience[- ]value|'
    r'partnership) deck\b|\bdeck (on|about|for)\b|'
    r'\bone[- ]?pager (on|about|for)\b', re.I)
_DECK_ARTIFACT_RX = re.compile(r'\bvenn diagram\b|\bon a single slide\b', re.I)

_ANALYZE_MENU_RX = re.compile(
    r'^analy[sz]e (this|the) (data|open profile|profile)[.!]?$', re.I)
_COMPARE_OPEN_RX = re.compile(r'^compare\b', re.I)
_OPEN_TABS_RX = re.compile(r'\b(open|tabs)\b', re.I)
_SELECTED_CUTS_RX = re.compile(r'\bselected cuts\b', re.I)
_COMPARE_OTHERS_RX = re.compile(
    r'^compare (this |it )?(against|vs\.?|with|to) other '
    r'(profiles?|data|audiences?)[.!?]?$', re.I)

_MODE_RXS = (
    ('exec_summary', re.compile(r'\bexec(utive)? summary\b', re.I)),
    ('whitespace', re.compile(r'\bwhite\s?space\b', re.I)),
    ('new_consumers', re.compile(r'\bnew (consumers?|customers?|buyers?)\b', re.I)),
    ('personas', re.compile(r'\bpersonas?\b', re.I)),
    ('easter_eggs', re.compile(
        r'\b(easter eggs?|surprising|unexpected|convergences?)\b', re.I)),
    ('linkedin_post', re.compile(r'\blinked\s?in\b|\bsocial (media )?post\b', re.I)),
    ('full', re.compile(r'\bfull read\b', re.I)),
)


def _safe(fn, *a, default=False):
    try:
        return fn(*a)
    except Exception:
        return default


def mode_for_text(text):
    t = str(text or '')
    for mode, rx in _MODE_RXS:
        if rx.search(t):
            return mode
    return None


def looks_like_deck_ask(text, deck_in_flight=False):
    t = str(text or '').strip()
    if not t or deck_in_flight:
        return False
    if _DECK_BUILD_RX.search(t) or _DECK_NOUN_RX.search(t):
        return True
    if _DECK_ARTIFACT_RX.search(t):
        return True
    try:
        import prometheus_analysis as pma
        if _safe(pma.detect_deck_intent, t):
            return True
    except Exception:
        pass
    return False


def looks_subscriber_iq(text):
    try:
        import subiq_intent as si
        return bool(_safe(si.detect_subscriber_iq_intent, text))
    except Exception:
        return False


def looks_ambiguous_churn(text):
    """``ambiguous_churn_subject`` returns ``(matched, subject)``; only
    the flag decides."""
    try:
        import subiq_intent as si
        out = _safe(si.ambiguous_churn_subject, text, default=(False, ''))
        if isinstance(out, (tuple, list)):
            return bool(out[0]) if out else False
        return bool(out)
    except Exception:
        return False


def looks_search_demand(text):
    try:
        import prometheus_analysis as pma
        return bool(_safe(pma.detect_search_demand_intent, text))
    except Exception:
        return False


def looks_metric_kpi(text):
    try:
        import prometheus_analysis as pma
        return bool(_safe(pma.detect_metric_kpi_intent, text))
    except Exception:
        return False


def tool_intent(text):
    """Which guided tool intake the ask opens, if any."""
    for name in ('aiq', 'fw', 'jiq', 'bpiq'):
        cap = name + '_intent'
        if host.has(cap) and _safe(getattr(host, cap), text):
            return name
    return None


def should_analyze(text, has_ctx):
    """Mirror of the widget's final analyze-or-interpret test."""
    t = str(text or '').strip()
    if not t:
        return False
    if _BUILD_VERB_RX.search(t):
        return False
    if _CUT_BY_RX.search(t):
        return False
    if _OPS_WORDS_RX.search(t):
        return False
    if has_ctx:
        return True
    if looks_metric_kpi(t):
        return True
    if _QUESTION_MARK_RX.search(t):
        return True
    if _QUESTION_OPEN_RX.search(t):
        return True
    # A question mark or an interrogative on ANY sentence, not only
    # the first or the last (2026-10-02 audit: "58.3% stayed for
    # more. is this a strong number? for the other franchises ..."
    # read as a build).
    if guards.is_question_shaped(t):
        return True
    return False


def decide(text, *, has_ctx=False, mode=None, extra=None, open_tabs=0,
           deck_in_flight=False):
    """Route one fresh ask. See the module docstring for the shape."""
    t = str(text or '').strip()
    extra = extra if isinstance(extra, dict) else {}
    d = {'surface': 'interpret', 'mode': None, 'reason': 'default',
         'client_hint': None}
    if not t:
        d['reason'] = 'empty'
        return d

    # 0. An explicit analyze step from a client (mode chip, confirm
    #    payload) is already a decision.
    if mode and str(mode).strip():
        d.update(surface='analyze', mode=str(mode).strip().lower(),
                 reason='explicit_mode')
        return d
    if any(extra.get(k) for k in _ANALYZE_EXTRA_KEYS):
        d.update(surface='analyze', reason='explicit_step')
        return d

    # 1. Guided tool intakes (attribution, flywheel, journey, brand
    #    partnership) open on the analyze surface.
    ti = tool_intent(t)
    if ti:
        d.update(surface='analyze', reason='tool_intake:' + ti)
        return d

    # 2. Deck asks.
    if looks_like_deck_ask(t, deck_in_flight):
        d.update(surface='deck', reason='deck_ask')
        return d

    # 2b. A bare acknowledgement, pick, or refusal with nothing armed
    #     is not an ask. The surface stays whatever the data state
    #     says; the ask service answers it without a reasoning pass
    #     (2026-10-02 audit: "approved" became a fresh build, "no"
    #     re-asked the open-screen question).
    kind = guards.bare_reply_kind(t)
    if kind:
        d.update(surface='analyze' if has_ctx else 'interpret',
                 reason='short_reply:' + kind)
        return d

    # 2c. A capability question with a deterministic answer ("Can I
    #     cut the existing Apple TV+ profile by quarter?") is answered,
    #     never drafted. Only fires when guards can answer it, so a
    #     polite build order ("can you build me a Nike profile") still
    #     builds.
    try:
        if guards.capability_answer(t):
            d.update(surface='analyze', reason='capability_question')
            return d
    except Exception:
        pass

    # 2d. "Do you see the X Subscriber IQ?" is a library lookup. It is
    #     answered from the library and the caller's runs on the
    #     analyze surface, with or without data open (2026-10-02 Bria:
    #     it drafted a 10-credit duplicate of a finished read).
    try:
        if guards.subiq_lookup_title(t):
            d.update(surface='analyze', reason='subiq_lookup')
            return d
    except Exception:
        pass

    # 3. Subscriber IQ asks are build requests, even with a profile open.
    #    A question ABOUT the open Subscriber IQ page, or about what
    #    the product can do, is a question (2026-10-02 audit: "Is that
    #    55% of total viewers or of new and reactivated watchers?"
    #    became a Subscriber IQ draft).
    subiq = looks_subscriber_iq(t) or looks_ambiguous_churn(t)
    if subiq and guards.subiq_question_not_build(t, has_ctx=has_ctx):
        subiq = False
        if has_ctx:
            d.update(surface='analyze', reason='question_about_subiq')
            return d
        if guards.is_capability_question(t):
            d.update(surface='analyze', reason='capability_question')
            return d

    # 4. Dashboard pickers (menus the dashboard can open). The surface
    #    is still analyze for a client without that UI.
    if _ANALYZE_MENU_RX.match(t):
        d.update(surface='analyze', reason='analyze_menu',
                 client_hint='analyze_menu')
        return d
    if (_COMPARE_OPEN_RX.match(t) and _OPEN_TABS_RX.search(t)
            and not _SELECTED_CUTS_RX.search(t)):
        d.update(surface='analyze', reason='compare_open_tabs',
                 client_hint='compare_open_tabs' if open_tabs else None)
        return d
    if _COMPARE_OTHERS_RX.match(t):
        d.update(surface='analyze', reason='compare_others',
                 client_hint='compare_picker')
        return d

    # 5. Search-journey demand carries its own subject.
    if not subiq and looks_search_demand(t):
        d.update(surface='analyze', mode='search_demand',
                 reason='search_demand')
        return d

    # 6. A named analysis mode with data open.
    m = None if subiq else mode_for_text(t)
    if m and has_ctx:
        d.update(surface='analyze', mode=m, reason='mode:' + m)
        return d

    # 7. Question-shaped or data-open asks analyze; the analyze path
    #    still hands build-shaped asks back to interpret.
    if not subiq and should_analyze(t, has_ctx):
        d.update(surface='analyze', reason='question' if not has_ctx
                 else 'data_open')
        return d

    d['reason'] = 'subiq' if subiq else 'build_or_other'
    return d
