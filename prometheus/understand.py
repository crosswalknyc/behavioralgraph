"""The rule helpers behind the one server-side decision step.

``decide`` reads a fresh ask plus the facts a client can report (is a
profile open, how many tabs, an explicit mode or confirm step) and
returns which surface runs it and why, so the dashboard, the standalone
app, and the API all route an ask the same way and a fix reaches every
client at once. Since 2026-11-23 the decision itself lives in
``prometheus.routing.surface`` (the Understanding decides between
intents; the rules here that are not intents run first in their standing
order); ``decide`` is the thin wrapper for callers that only have the
words. The regex ladder this module carried until then is gone.

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
import os
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
                       'bpiq_wizard',
                       'jiq_inputs', 'jiq_confirm', 'fw_inputs',
                       'fw_confirm', 'aiq_inputs', 'aiq_confirm',
                       'twin_inputs', 'twin_confirm')

_BUILD_VERB_RX = re.compile(
    r'\b(run|build|pull|create|queue|start|launch|generate)\b[^.]{0,60}'
    # "aprofile" (the missing space), "porfile", "profle": the order
    # stands however the noun is typed (2026-10-20 Jenna, "run aprofile
    # on ncis: new york" ran as a read and quoted a $300 custom read)
    r'\b(?:a?profiles?|porfiles?|profles?|proifles?|profiels?)\b'
    # A people-universe order is a build order whatever it is called
    # (2026-10-07 Eliot: "is there a way to build an audience of
    # people who have attended a Gunna concert?" routed as a question).
    r'|\b(run|build|pull|create|make)\b[^.?!]{0,30}\b(?:an?\s+|the\s+)?'
    r'(?:audience|universe|segment|cohort)s?\s+(?:of|for)\s+'
    r'(?:people|those|anyone|everyone|users|fans|folks|consumers|'
    r'viewers|buyers|shoppers|americans|us\b)', re.I)
_CUT_BY_RX = re.compile(r'\b(cut|slice)\b[^.]{0,40}\bby\b', re.I)
# "credit card", "credit union", "credit score" are not about the reader's
# credits (2026-11-13, Samantha Kolton: "What type of credit card does Paige
# Spiranac's have?" was kept on the build surface by the bare word and drew a
# $300 brief; prometheus.question_first)
_OPS_WORDS_RX = re.compile(
    r'\brefresh\b|\bsample size\b|\bincidence\b|\bstatus\b|'
    r'\bcredits?\b(?!\s+(?:cards?|unions?|scores?|providers?|bureaus?|reports?|karma|lines?|limits?|history|ratings?|'
    r'checks?|builders?|repair|monitoring|products?|issuers?|companies|company)\b)',
    re.I)
_QUESTION_MARK_RX = re.compile(r'\?\s*$')
_QUESTION_OPEN_RX = re.compile(
    r'^(what|which|who|whose|how|why|where|when|do|does|did|are|is|was|'
    r'were|can|could|would|should|show me|tell me|give me|compare|'
    r'analy[sz]e|top \d|break ?down)\b', re.I)
# A question stated as a wish (2026-10-08, East Tree Media: "We want
# to understand the consumption over time for The Office US" drafted
# a build). "We want to know", "I'd like to see", "help us understand"
# are questions, not orders.
_STATEMENT_ASK_RX = re.compile(
    r"^(?:(?:please\s+)?help (?:us|me) (?:to )?(?:understand|see|figure out|"
    r"get a sense of|learn)\b|"
    r"(?:we|i|they|our team|the team|the client|my client|our client|"
    r"the brand|our brand|the agency)"
    r"(?:'d|'re|'m| would| are| am| is| was| were| have been| has been)?\s+"
    r"(?:want|wants|would like|like|need|needs|trying|looking|hoping|"
    r"curious|interested|would love|love|wondering)\s+"
    r"(?:to|in|about|if|whether|how|what|who|which|where|when)\b)", re.I)
# A metric asked over time ("monthly consumption", "viewership over
# time", "month by month") is a read, never a build.
_TIME_WORDS = (r"over time|month by month|month over month|monthly|by month|"
               r"each month|every month|per month|a month|"
               r"week by week|week over week|weekly|by week|each week|"
               r"every week|per week|quarter by quarter|quarterly|by quarter|"
               r"year over year|each year|every year|trend(?:s|ed|ing|line)?|"
               r"trajectory|time series")
# "total hours by month" is a viewing metric over time (Cara Dorfman at
# Amazon, 2026-11-10: hours by month for 2025 on Prime Video and YouTube
# was answered as a question about cutting a profile by month)
_METRIC_WORDS = (r"consumption|viewership|viewing|watch(?:ing)? time|"
                 r"hours watched|minutes watched|(?:total\s+)?hours|minutes|time spent|usage|overlap|"
                 r"watch(?:ed|es)?|viewed|views|"
                 r"streams?|streamed|streaming|plays|played|"
                 r"listens|listened|listenership|viewers?|listeners?|users?|"
                 r"visitors?|subscribers?|signups?|churn|engagement|audience|"
                 r"reach|sales|purchases|searches|traffic|downloads")
_TIME_SERIES_RX = re.compile(
    r"\b(?:" + _METRIC_WORDS + r")\b[^.?!]{0,40}\b(?:" + _TIME_WORDS + r")\b"
    r"|\b(?:" + _TIME_WORDS + r")\b[^.?!]{0,24}\b(?:" + _METRIC_WORDS + r")\b",
    re.I)

# A count of the people who watched, streamed, read or listened to a named
# title is a read, however it is worded (Liz Huszarik 2026-11-17, in the
# Prometheus app: "I need the Total Viewers to FBI Most Wanted and FBI
# International, All Seasons on Peacock. The window is Nov 2025-Nov 2026.
# Need the viewers for each series reported independently." carried no
# question word, fell to the build default and drew two $300 profile
# briefs). Three shapes: a counted noun ("total viewers", "number of
# viewers", "viewer count", "viewership"), a how-many, and the wish form
# ("need the viewers for X", "pull the viewers to X"). A bare audience name
# ("Everybody Loves Raymond viewers on Amazon Prime Video, September 2026",
# "Reba McEntire avid fans") names who to build and stays a build; so does
# anything that names a profile, a cut, a segment or a brief.
_COUNT_PEOPLE = r"(?:viewers|readers|listeners|players|streamers)"
_COUNT_READ_RX = re.compile(
    r"\b(?:total|overall|combined|unique|unduplicated|distinct)\s+(?:(?:us|u\.s\.|unique|distinct)\s+)?"
    r"(?:number\s+of\s+)?(?:" + _COUNT_PEOPLE + r"|viewership|readership|listenership)\b"
    r"|\b(?:number|count|counts|tally|sum)\s+of\s+(?:(?:total|unique|distinct|us|u\.s\.)\s+)*"
    r"(?:" + _COUNT_PEOPLE + r"|people\s+who\s+(?:watch|stream|view|read|listen|play|tun)\w*)\b"
    r"|\b(?:viewer|viewership|listener|listenership|reader|readership)\s+(?:counts?|numbers?|totals?|figures?|tally)\b"
    r"|\b(?:viewership|readership|listenership)\b"
    r"|\bhow\s+many\s+(?:(?:us|u\.s\.|unique|total|distinct)\s+)*(?:people|persons|individuals|americans|"
    + _COUNT_PEOPLE + r")\b"
    r"|\b(?:need|want|get|give\s+(?:me|us)|send\s+(?:me|us)|show\s+(?:me|us)|tell\s+(?:me|us)|pull|report|provide|"
    r"looking\s+for|after)\s+(?:(?:me|us)\s+)?(?:the\s+)?(?:" + _COUNT_PEOPLE + r")\s+"
    r"(?:for|to|of|on|across|in|who|that)\b", re.I)
_COUNT_NOT_RX = re.compile(
    r"\b(?:a?profiles?|porfiles?|profles?|cuts?|segments?|cohorts?|briefs?|data\s?sets?|"
    r"subscriber\s+iq|profile\s+iq|journey\s+iq|digital\s+journey)\b", re.I)

_DECK_BUILD_RX = re.compile(
    r'\b(build|make|create|generate|put together|spin up|prepare|draft|'
    r'put|turn|drop|export|convert|render|lay out|package|wrap|compile|'
    r'assemble|consolidate|combine|merge|recreate|re-create|rebuild|remake|redo|reproduce)\b'
    r'[^.!?]{0,60}\b(deck|slides|presentation|one[- ]?pagers?|pptx|powerpoint|ppt)\b', re.I)
# 2026-10-09 (Liz): "I want a deck built for Euphoria ...", "powerpoint
# deck.", "Consolidate all slides into a Single Powerpoint deck." all
# ran as analysis and came back as slide text. A wish for a deck, a
# deck named as built, or a bare deck noun is a deck ask: the reader
# wants the file.
_DECK_WANT_RX = re.compile(
    r'\b(?:i |we )?(?:want|need|would like|d like|like|looking for|could use|'
    r'give me|send me|can (?:you|we|i) (?:get|have|see|build|make|create))\b'
    r'[^.!?]{0,50}\b(?:deck|slides|presentation|one[- ]?pagers?|pptx|powerpoint|ppt)\b', re.I)
_DECK_BUILT_RX = re.compile(
    r'\b(?:deck|slides|presentation|powerpoint|pptx|one[- ]?pager)s?\s+'
    r'(?:built|made|created|put together|generated|rendered|exported)\b', re.I)
_DECK_BARE_RX = re.compile(
    r'^\s*(?:a |the |an |one |single )?(?:powerpoint|pptx|ppt|deck|slides|presentation)'
    r'(?:\s+(?:deck|file|version|please))*[.! ]*$', re.I)
# 2026-10-05 (Scott): 'put that data into a simple deck' is a deck
# ask whatever the verb. So is 'render it' when the reply before it
# was a deck outline (SLIDE 1 ... ). The outline is a draft; the
# user asking to render it wants the file.
_DECK_INTO_RX = re.compile(
    r'\b(?:into|as) (?:a |an |one )?(?:(?:simple|quick|short|small|tight|clean|single|one|full|final|'
    r'complete|consolidated|proper|real|actual|branded|powerpoint|ppt|pptx|one[- ]page) )*'
    r'(?:deck|slides?|presentation|one[- ]?pager|pptx|ppt|powerpoint)\b', re.I)
# 2026-11-11 (Armine): "Can we summarize this in a powerpoint?" and "Summarize in powerpoint" ran as questions
# for the read lane (no verb above covers summarize, and "in a powerpoint" is not "into / as"). A summary, a
# recap or the results asked for in a deck format is a deck ask.
_DECK_SUMMARIZE_RX = re.compile(
    r'\b(?:summari[sz]e|recap|sum up|wrap (?:it|this|that) up|present|show|share|deliver|give (?:me|us)|send (?:me|us)?)\b'
    r'[^.!?]{0,60}\b(?:in|into|as|on|via)\s+(?:a |an |one |the )?(?:(?:short|quick|simple|brief|clean|single) )*'
    r'(?:deck|slides?|presentation|one[- ]?pager|pptx|ppt|power ?point)\b', re.I)
_RENDER_IT_RX = re.compile(
    r'^(?:please\s+)?(?:render|build|make|export|generate|create|produce|'
    r'do|ship|send|give me|finish)\s+(?:it|that|this|them|these|the deck|'
    r'the slides|the file|the pptx|as a deck|the actual deck)'
    r'(?:\s+(?:now|please|as a deck|as slides|as a pptx|for real))?[.! ]*$'
    r'|^render[.! ]*$', re.I)
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


def looks_like_deck_ask(text, deck_in_flight=False,
                        prior_deck_outline=False):
    t = str(text or '').strip()
    if not t or deck_in_flight:
        return False
    try:   # "powerpoing deck" is a PowerPoint deck (2026-10-09, Liz)
        import prometheus_analysis as _pma_typo
        t = _pma_typo.fix_deck_format_typos(t)
    except Exception:
        pass
    if _DECK_BUILD_RX.search(t) or _DECK_NOUN_RX.search(t) \
            or _DECK_INTO_RX.search(t) or _DECK_WANT_RX.search(t) \
            or _DECK_BUILT_RX.search(t) or _DECK_BARE_RX.match(t) \
            or (len(t.split()) <= 24 and _DECK_SUMMARIZE_RX.search(t)):   # the short ask itself; one riding a long question is prometheus.ride_along's
        return True
    if prior_deck_outline and _RENDER_IT_RX.match(t):
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
    try:
        from prometheus.bpiq_wizard import is_start
        if is_start(text):
            return 'bpiq'
    except Exception:
        pass
    for name in ('bpiq', 'aiq', 'fw', 'jiq', 'twin'):
        cap = name + '_intent'
        if host.has(cap) and _safe(getattr(host, cap), text):
            return name
    return None


def looks_count_read(text):
    """True for a count of the people who watched, streamed, read or
    listened to something, asked as a statement or an instruction ("I
    need the total viewers to X and Y on Peacock", "need the viewers
    for X all seasons", "viewer count for X"). Never an order to build
    a profile, a cut or a brief, and never a bare audience name."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1200:
        return False
    if not _COUNT_READ_RX.search(t):
        return False
    if _COUNT_NOT_RX.search(t) or _BUILD_VERB_RX.search(t):
        return False
    try:
        from . import orders as _ord
        if _ord.is_profile_order(t):
            return False
    except Exception:
        pass
    return True


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
    if _STATEMENT_ASK_RX.search(t) or _TIME_SERIES_RX.search(t):
        return True
    # A count of viewers on a named title is a read, never a build
    # (Liz Huszarik 2026-11-17).
    if looks_count_read(t):
        return True
    # A question mark or an interrogative on ANY sentence, not only
    # the first or the last (2026-10-02 audit: "58.3% stayed for
    # more. is this a strong number? for the other franchises ..."
    # read as a build).
    if guards.is_question_shaped(t):
        return True
    return False


_DECK_OUTLINE_RX = re.compile(
    r'(?m)^\s*SLIDE\s+1\b|^Here is the deck\b|^Here is a [\w -]*deck\b|'
    r'^Deck outline\b', re.I)


def prior_deck_outline(history):
    """True when the last substantive agent turn (skipping a clarify
    question) was a deck outline typed into chat instead of a file."""
    skipped = 0
    for t in reversed(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') != 'agent':
            continue
        txt = str(t.get('text') or '')
        if _DECK_OUTLINE_RX.search(txt):
            return True
        # A one-line question ('Do you mean for X, or Y?') sits
        # between the outline and the render ask; look past it once.
        if txt.strip().endswith('?') and len(txt) < 200 and skipped < 2:
            skipped += 1
            continue
        return False
    return False


def dashboard_picker(text, open_tabs=0):
    """The dashboard picker a typed command opens, as ``(reason, client_hint)``,
    or None: "analyze this data" is the analyze menu, "compare with the open
    tabs" the open-tab compare, "compare this against other profiles" the
    compare picker. A client without that UI runs the analyze surface as is."""
    t = str(text or '').strip()
    if _ANALYZE_MENU_RX.match(t):
        return 'analyze_menu', 'analyze_menu'
    if _COMPARE_OPEN_RX.match(t) and _OPEN_TABS_RX.search(t) and not _SELECTED_CUTS_RX.search(t):
        return 'compare_open_tabs', ('compare_open_tabs' if open_tabs else None)
    if _COMPARE_OTHERS_RX.match(t):
        return 'compare_others', 'compare_picker'
    return None


def decide(text, *, has_ctx=False, mode=None, extra=None, open_tabs=0,
           deck_in_flight=False, prior_outline=False):
    """Route one fresh ask from its words. See the module docstring for the
    shape. The decision is ``prometheus.routing.surface`` over the
    Understanding read from the words alone (no model); the ask service
    hands ``surface`` the ask's own Understanding instead, with the
    Reader's reading absorbed.

    Every reason is a row in ``prometheus.routing_table.ROWS`` (2026-10-02
    RCA): the decision carries the row id as ``row`` so the widget and the
    ask log speak the same vocabulary. A reason with no routing table row
    is a contract break: it raises under REGRESSION_TEST_MODE and is
    logged otherwise (``routing._row``)."""
    from . import routing as _routing
    d = _routing.surface(None, text, has_ctx=has_ctx, mode=mode, extra=extra, open_tabs=open_tabs,
                         deck_in_flight=deck_in_flight, prior_outline=prior_outline)
    if d.get('row') is None and d.get('reason') not in (None, ''):
        msg = ('understand.decide emitted a reason with no routing '
               'table row: %r' % (d.get('reason'),))
        if os.environ.get('REGRESSION_TEST_MODE'):
            raise RuntimeError(msg)
        print('[understand] ' + msg)
    return d
