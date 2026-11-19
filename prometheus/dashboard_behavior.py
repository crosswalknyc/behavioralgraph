"""A question about the dashboard itself is answered as one, never as a
read of whatever profile is open (Jenna 2026-11-18).

Jenna, verbatim, on a question copy from a Dick Wolf Productions seat:
"this is wrong its still trying to snap to the open profile instead of
listenint to what the user is asking. no need to do this for hwr but
needs to be fixed for the future".

The message: "when i try to put a start of 1/1/2021 it reverts it to
1/1/2026 but it lets me ass an end date of 12/31/2023". The reader was
reporting a date field on the Rankers page. A streaming service had been
named a turn earlier and its profile sat in the picker, so every
which-subject test passed and a read of that profile ran. The reply led
with the profile's name, listed its audience figures, explained the
field ("the run window asserting itself, not a bug in the field") and
promised a "back-window build". None of it was known and none of it was
asked.

Every earlier fix for this family settled WHICH subject a data question
is about (the words, the thread, the page). None asked whether the
message is a data question at all. This module is that question.

  1. ``detect`` / ``is_dashboard_question``: the message is about how the
     dashboard is behaving for the person typing: a field that will not
     take a value, a control that changes what was entered, a button or a
     download that does nothing, a page that will not load or looks
     wrong. First-person interaction ("when I try to", "it won't let
     me", "I click and nothing happens") or a control with a fault verb.
     A data question that happens to use the same nouns ("which button
     gets the most clicks on the product page") is not one, and the asks
     other lanes own (where a thing is, the reader's own runs, a design
     change, an action this chat cannot take, an order) stay with them.
  2. ``answer``: one fixed reply, no model, no price, no figures and no
     profile's name. What the dashboard map really knows about the thing
     is said in the map's own words; otherwise the reply says plainly
     that Prometheus cannot see what the control is doing, what the
     person can do right now (for a date range: ask for the read with
     the dates in words), and that the note went to the team. The note
     is the held record the ops watch and the daily digest already read
     (prometheus.promise_truth.hold); the question copy carries it too.
     No new mail, no new recipients.
  3. ``scrub``: the words that reply made up never reach a reader on any
     path: a made-up mechanism ("run window", "the open set", "keyed
     to", "partitions"), a made-up product ("back-window build", "quoted
     first, confirmed, then produced") and any sentence that states how
     a control behaves as fact ("the start field snaps to ... because",
     "not a bug in the field"). Wired into the house output scrub.
  4. ``PROMPT_RULE``: the same rule for the two model passes that answer
     on a page (the page analyst and the reasoned read), so a wording
     the detector misses is still not explained by guesswork.

Pure text plus one fail-safe record write. Nothing here raises. Kill
switch ``PM_DASHBOARD_BEHAVIOR=0``. Regression:
scripts/test_pm_dashboard_behavior.py.
"""
from __future__ import annotations

import os
import re

__all__ = ['enabled', 'detect', 'self_serve', 'self_serve_reply', 'is_dashboard_question', 'steps_aside', 'reply_for', 'answer', 'core_hook',
           'from_decline', 'scrub', 'scrub_chips', 'invented', 'READER_INTENT', 'ROUTE', 'KIND', 'PROMPT_RULE', 'PROMPT_RULE_SHORT']

ROUTE = 'dashboard_behavior'
KIND = 'dashboard_behavior'
READER_INTENT = 'dashboard_issue'
_MAX_LEN = 700


def enabled():
    return str(os.environ.get('PM_DASHBOARD_BEHAVIOR', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(s):
    return ' '.join(str(s or '').replace('\u2019', "'").replace('\u2018', "'").split())


# ------------------------------------------------------------------ the words
# Things on the page a person operates. `_UI` is only ever a control;
# `_VIEW` nouns are also things a data question counts (a chart of
# downloads, a link in a post), so they need a hard fault beside them.
_DATE = (r"(?:(?:start|end|from|to|begin(?:ning)?|as[- ]of)[- ]?(?:date|field)s?|date ?(?:field|picker|range|box|input|selector|"
         r"filter|control|window)s?|calendar|date ?pickers?|dates?)")
_UI = (r"(?:" + _DATE + r"|field|input|text ?box|search (?:box|bar|field)|drop ?-?downs?|drop ?-?down menus?|menus?|pickers?|"
       r"selectors?|buttons?|toggles?|check ?box(?:es)?|sliders?|filter bar|tabs?|pages?|screens?|dashboard|site|website|"
       r"scroll ?bars?|side ?bars?|pop-?ups?|modals?|tooltips?|log-?in|sign-?in|widget|chat (?:box|window|panel)|layout|display)")
_VIEW = r"(?:charts?|graphs?|tables?|tiles?|cards?|panels?|downloads?|exports?|csv|links?|filters?|views?|boards?|lists?|images?)"

# "it won't let me", "but it lets me", "when I try to put", "I click and nothing happens"
_FIRST_PERSON = (
    re.compile(r"\b(?:wo ?n'?t|will not|does ?n'?t|does not|did ?n'?t|did not|is ?n'?t|is not|would ?n'?t|would not|never|not)\s+"
               r"(?:let(?:ting)?|allow(?:ing)?)\s+(?:me|us)\b", re.I),
    re.compile(r"\b(?:but|and|yet|while)\s+(?:then\s+)?it\s+(?:still\s+)?(?:lets|let|allows|allowed)\s+(?:me|us)\b", re.I),
    re.compile(r"\bit\s+(?:only\s+)?(?:lets|allows)\s+(?:me|us)\b[^.?!]{0,90}\b(?:but|not|only)\b", re.I),
    re.compile(r"\b(?:when(?:ever)?|every ?time|each time|any ?time|as soon as|once|after)\s+(?:i|we)\s+(?:try|tried|go|went|attempt(?:ed)?)\s+to\s+"
               r"(?:put|enter|type|key|select|choose|pick|click|press|tap|change|set|add|open|load|download|export|scroll|filter|"
               r"apply|save|log|sign|switch|drag|hit|use|search|input|move|expand|close|edit|update|adjust|clear|reset)\b", re.I),
    re.compile(r"\b(?:when(?:ever)?|every ?time|each time|any ?time|as soon as|once|after)\s+(?:i|we)\s+(?:click(?:ed)?|press(?:ed)?|"
               r"tap(?:ped)?|hit|select(?:ed)?|choose|chose|pick(?:ed)?|type[ds]?|enter(?:ed)?|put|change[ds]?|scroll(?:ed)?|appl(?:y|ied)|"
               r"hover(?:ed)?|set)\b", re.I),
    re.compile(r"\b(?:i|we)\s+(?:click(?:ed)?|press(?:ed)?|tap(?:ped)?|hit)\b[^.?!]{0,70}\b(?:and|but)\s+(?:then\s+)?(?:nothing|it\s+"
               r"(?:did|does)(?:n'?t| not)|no(?:thing)?\s+happen)", re.I),
    re.compile(r"\b(?:i|we)\s+(?:ca ?n'?t|cannot|can not|am unable to|was unable to|could ?n'?t|could not|am not able to|"
               r"was not able to)\s+(?:seem to\s+)?(?:click|select|choose|pick|type|enter|change|set|scroll|expand|collapse|drag|"
               r"press|tap|toggle|check|uncheck|apply|save|submit|edit|adjust|clear|get\s+(?:it|this|that|the\s+" + _UI + r"|the\s+"
               + _VIEW + r")\s+to)\b", re.I),
    re.compile(r"\b(?:ca ?n'?t|cannot|could ?n'?t|why\s+can)\s+(?:i|we)\s+(?:not\s+)?(?:click|select|choose|pick|type|enter|change|set|"
               r"scroll|expand|collapse|drag|press|tap|toggle|check|uncheck|apply|save|submit|edit|adjust|clear|go\s+(?:back|past|before))\b", re.I),
)
# what a control does when it is not doing what the person expects
_FAULT = (r"(?:reverts?|reverted|reverting|resets?|resetting|snaps?\s+(?:back|to)|snapped|jumps?\s+(?:back|to)|jumped|goes\s+back|"
          r"went\s+back|changes?\s+(?:it(?:self)?\s+)?back|changed\s+(?:it\s+)?back|switches\s+back|switched\s+back|defaults?\s+(?:back\s+)?to|"
          r"defaulted\s+(?:back\s+)?to|keeps?\s+(?:changing|resetting|reverting|going\s+back|defaulting|loading|spinning|crashing|"
          r"freezing|closing|disappearing|failing|timing\s+out|jumping|snapping|kicking|logging|refreshing|reloading|clearing|"
          r"buffering|erroring)|"
          r"(?:is|are|was|were|looks?|seems?|appears?|got|gets|went|goes|stays?)\s+(?:not\s+working|broken|broke|frozen|stuck|blank|"
          r"empty|gr[ae]yed\s+out|disabled|unclickable|unresponsive|cut\s+off|glitchy|buggy|messed\s+up|locked|"
          r"spinning|loading\s+forever|down)|"
          r"(?:is ?n'?t|is\s+not|are ?n'?t|are\s+not|was ?n'?t|not|wo ?n'?t|will\s+not|does ?n'?t|does\s+not|did ?n'?t|did\s+not|"
          r"never|stopped|quit|no\s+longer)\s+(?:work(?:ing|s)?|load(?:ing|s)?|respond(?:ing|s)?|click(?:ing|able)?|scroll(?:ing)?|"
          r"sav(?:e|es|ing)|appl(?:y|ies|ying)|updat(?:e|es|ing)|refresh(?:es|ing)?|download(?:s|ing)?|export(?:s|ing)?|accept(?:s|ing)?|"
          r"tak(?:e|es|ing)|do(?:es|ing)?\s+anything|let(?:ting)?|open(?:ing|s)?|clos(?:e|es|ing)|expand(?:ing|s)?|go(?:ing)?\s+(?:back|past|before)|"
          r"hold(?:ing|s)?|stick(?:ing|s)?|stay(?:ing|s)?|chang(?:e|es|ing))|"
          r"freez(?:es|ing)|froze|crash(?:es|ed|ing)?|hangs?|hung|does\s+nothing|did\s+nothing|nothing\s+happens|nothing\s+happened|"
          r"throws?\s+an\s+error|gives?\s+(?:me\s+|us\s+)?an\s+error|errors?\s+out|errored\s+out|times?\s+out|timed\s+out)")
_SOFT_FAULT = r"(?:(?:is|are|was|were|looks?|seems?|appears?)\s+(?:wrong|off|weird|strange|odd|slow|missing|gone)|disappear(?:s|ed)|vanish(?:es|ed)|fails?|failed|failing)"
_UI_FAULT_RX = re.compile(r"\b" + _UI + r"\b[^.?!]{0,60}?\b(?:" + _FAULT + r"|" + _SOFT_FAULT + r")", re.I)
_FAULT_UI_RX = re.compile(r"\b" + _FAULT + r"\b[^.?!]{0,40}?\b" + _UI + r"\b", re.I)
_VIEW_FAULT_RX = re.compile(r"\b" + _VIEW + r"\b[^.?!]{0,50}?\b" + _FAULT, re.I)
_BUG_RX = re.compile(r"\b(?:bug|glitch|issue|problem|error|something\s+(?:is\s+)?wrong)\s+(?:with|in|on)\s+(?:the|this|that|your|my)\s+"
                     r"(?:[\w-]+\s+){0,3}?(?:" + _UI + r"|" + _VIEW + r")\b", re.I)
_ERROR_MSG_RX = re.compile(r"\b(?:error\s+message|an?\s+error\s+(?:pops?\s+up|appears?|comes?\s+up|shows?(?:\s+up)?|page|screen)|"
                           r"(?:get(?:ting)?|got|see(?:ing)?|saw|shows?(?:\s+me)?|giving\s+me|gives\s+me)\s+an?\s+(?:\w+\s+)?error)\b", re.I)
_WHY_CONTROL_RX = re.compile(r"\bwhy\s+(?:does|did|is|are|would|wo ?n'?t|ca ?n'?t|does ?n'?t|is ?n'?t)\s+(?:it|the|this|that|my)\s+"
                             r"(?:[\w-]+\s+){0,3}?" + _UI + r"\b", re.I)
_UI_RX = re.compile(r"\b" + _UI + r"\b", re.I)
_DATE_RX = re.compile(r"\b(?:" + _DATE + r"|years?|months?|\d{1,2}/\d{1,2}/\d{2,4}|20\d{2})\b", re.I)
_FAULT_RX = re.compile(r"\b" + _FAULT + r"\b", re.I)

# a data question in the same words: counted people, a share, a ranking
_DATA_ASK_RX = re.compile(
    r"\b(?:how\s+many|how\s+much|what\s+(?:share|percent|percentage|portion|fraction)|what\s+is\s+the\s+(?:size|share|reach|index|penetration)|"
    r"who\s+(?:are|is|watch|buy|use|stream)|which\s+(?:brands?|titles?|shows?|creators?|audiences?|platforms?|services?|cohorts?|"
    r"buttons?|pages?|links?|posts?|clips?)\s+(?:get|gets|got|drive|drives|drove|convert|converts|has|have|had|is|are|"
    r"perform|performs)|top\s+\d+|rank(?:ed|ing|s)?\s+(?:by|the)|^\W*compare\b|viewers\s+of|audience\s+of|fans\s+of|share\s+of|"
    r"click[- ]through|conversion\s+rate|churn(?:ed)?|sign(?:ed)?\s*ups?|subscribers?\s+(?:who|that)|users\s+(?:who|that)|"
    r"people\s+(?:who|that))\b", re.I)
# "what is the Info-Seek % and Action Page % for @x as it did not export in the excel" asks for the figures
_DATA_FIGURE_RX = re.compile(
    r"\bwhat\s+(?:is|are|was|were)\s+(?:the|my|our)\s+[^.?!]{0,80}?(?:%|\bpercent(?:age)?\b|\brates?\b|\bcounts?\b|\bnumbers?\b|"
    r"\btotals?\b|\bfigures?\b|\bshares?\b|\bindex\b|\bviewers\b|\bminutes\b|\bhours\b)", re.I)


def _data_ask(t):
    return bool(_DATA_ASK_RX.search(t) or _DATA_FIGURE_RX.search(t))


# the thing failing is the reader's work, not the page: the status lane's
_WORK_RX = re.compile(r"\b(?:my|the|our|this|that)\s+(?:[\w-]+\s+){0,2}?(?:build|run|pull|profile|read|deck|report|job|order|query|journey|"
                      r"valuation|twin|tracker|brief|campaign|file|email)\b(?!\s+(?:page|screen|tab|view|list|picker|menu|button|chart|table|"
                      r"drop ?-?down|field|filter)s?\b)[^.?!]{0,40}?\b(?:" + _FAULT + r"|" + _SOFT_FAULT
                      + r"|still\s+(?:running|building|loading|queued))", re.I)
_WORK_STATUS_RX = re.compile(r"\b(?:build|run|pull|profile|read|deck|report|job|order|query|journey|valuation|twin|tracker|brief|campaign)\b"
                             r"(?!\s+(?:page|screen|tab|view|list|picker|menu|button)s?\b)[^.?!]{0,40}?\b(?:stuck|fail(?:ed|ing|s)?|done|ready|"
                             r"finished|running|queued|late|completed?)\b", re.I)
# a complaint about the answer, not the page ("you keep giving me the wrong show")
_ABOUT_REPLY_RX = re.compile(r"\byou(?:'re|\s+are|\s+keep|\s+gave|\s+said|\s+told|\s+did|\s+did ?n'?t|\s+were|\s+just)\b", re.I)


def _first_person(t):
    return any(rx.search(t) for rx in _FIRST_PERSON)


_SENT_SPLIT_RX = re.compile(r"(?<=[.?!])\s+|;\s+|\n+")
_ASK_OPEN_RX = re.compile(r"^\W*(?:what|which|who|whose|how|where|when|can\s+you|could\s+you|would\s+you|please|show\s+me|tell\s+me|"
                          r"give\s+me|send\s+me|pull|run|build|compare|i\s+(?:need|want|would\s+like)|we\s+(?:need|want))\b", re.I)


def _signal(t):
    """The sentence itself is about the dashboard's behaviour."""
    return bool(_first_person(t) or _UI_FAULT_RX.search(t) or _FAULT_UI_RX.search(t) or _BUG_RX.search(t)
                or _VIEW_FAULT_RX.search(t) or _ERROR_MSG_RX.search(t) or _WHY_CONTROL_RX.search(t))


def _other_question(t):
    """The message also asks something that is not about the dashboard
    ("the date field will not take 2021. How many people watched Psych
    that year?"): the question is what the person asked, and it keeps
    its own lane; the remark about the page rides along as context."""
    parts = [p.strip() for p in _SENT_SPLIT_RX.split(t) if p and p.strip()]
    if len(parts) < 2:
        return False
    for p in parts:
        if _signal(p):
            continue
        if p.endswith('?') or _data_ask(p) or (_ASK_OPEN_RX.search(p) and len(p.split()) >= 4):
            return True
    return False


def _family(t, thing=''):
    low = (t + ' ' + thing).lower()
    if re.search(r"\b(?:downloads?|downloading|exports?|exporting|csv|xlsx|excel|spreadsheet)\b", low):
        return 'download'
    if _DATE_RX.search(low) and re.search(r"\b(?:date|dates|calendar|start|end|range|window|year|years|month|months)\b|\d{1,2}/\d{1,2}/\d{2,4}", low):
        return 'date'
    if re.search(r"\b(?:pages?|screens?|dashboard|site|website|blank|load(?:s|ing)?|spinning|frozen|froze|freez\w+|crash\w*|stuck)\b", low):
        return 'page'
    return 'control'


_THING_WORDS = (
    (re.compile(r"\b(?:date|dates|calendar|start|end|as[- ]of)\b", re.I), 'date field'),
    (re.compile(r"\bbuttons?\b", re.I), 'button'),
    (re.compile(r"\b(?:download|downloads|downloading|export|exports|exporting|csv)\b", re.I), 'download'),
    (re.compile(r"\bdrop ?-?downs?\b|\bmenus?\b|\bpickers?\b|\bselectors?\b", re.I), 'menu'),
    (re.compile(r"\bfilters?\b|\bfilter bar\b", re.I), 'filter'),
    (re.compile(r"\bsearch (?:box|bar|field)\b", re.I), 'search box'),
    (re.compile(r"\btabs?\b", re.I), 'tab'),
    (re.compile(r"\bcharts?\b|\bgraphs?\b", re.I), 'chart'),
    (re.compile(r"\btables?\b", re.I), 'table'),
    (re.compile(r"\bfields?\b|\binputs?\b|\btext ?box\b", re.I), 'field'),
    (re.compile(r"\bpages?\b|\bscreens?\b|\bdashboard\b|\bsite\b|\bwebsite\b", re.I), 'page'),
)


def _thing(t):
    for rx, name in _THING_WORDS:
        if rx.search(t):
            return name
    return 'part of the page'


def steps_aside(text):
    """True when another lane owns the words: an order, a where-is
    question, the reader's own runs and files, a design change, an action
    this chat cannot take. Never raises."""
    t = _clean(text)
    try:
        from . import orders as _ord
        if _ord.is_order(t) or _ord.is_profile_order(t):
            return True
    except Exception:
        pass
    try:
        from . import wayfinding as _wf
        if _wf.wants(t):
            return True
    except Exception:
        pass
    try:
        from . import design_request as _dr
        if _dr.is_design_request(t):
            return True
    except Exception:
        pass
    try:
        from . import capabilities as _cap
        if _cap.action_request(t):
            return True
    except Exception:
        pass
    try:
        from . import deliverables as _dlv
        if _dlv.parse(t):
            return True
    except Exception:
        pass
    try:
        from . import send_file as _sf
        if _sf.parse(t):
            return True
    except Exception:
        pass
    try:
        from . import status_lane as _stl
        if _stl.wants_status(t) or _stl.wants_status_fast(t):
            return True
    except Exception:
        pass
    return False


def detect(text):
    """{'family', 'thing', 'why'} when the message is about how the
    dashboard is behaving for the person typing, else None."""
    if not enabled():
        return None
    t = _clean(text)
    if not t or len(t) > _MAX_LEN:
        return None
    fp = _first_person(t)
    ui = bool(_UI_RX.search(t))
    why = ''
    if fp and (ui or _FAULT_RX.search(t) or re.search(r"\b" + _VIEW + r"\b", t, re.I)):
        why = 'first_person'
    elif _UI_FAULT_RX.search(t) or _FAULT_UI_RX.search(t):
        why = 'control_fault'
    elif _BUG_RX.search(t):
        why = 'named_fault'
    elif _VIEW_FAULT_RX.search(t) and not _data_ask(t):
        why = 'view_fault'
    elif _ERROR_MSG_RX.search(t) and (ui or re.search(r"\b(?:i|we|me|my)\b", t, re.I)):
        why = 'error_message'
    elif _WHY_CONTROL_RX.search(t) and (_FAULT_RX.search(t) or re.search(r"\b(?:only|keep|keeps|always|never|every time)\b", t, re.I)):
        why = 'why_control'
    if not why:
        return None
    # a data question that uses the same nouns is a data question, unless
    # the person is describing what happens when they operate the page
    if _data_ask(t) and why != 'first_person':
        return None
    # the reader's own work that did not finish is the status lane's; a
    # complaint about the answer is the wrong-answer lane's
    if _WORK_RX.search(t) and not (ui and fp):
        return None
    if _ABOUT_REPLY_RX.search(t) and not ui:
        return None
    if _other_question(t):
        return None
    thing = _thing(t)
    return {'family': _family(t, thing), 'thing': thing, 'why': why}


# "I want to run the report myself": the person wants to do it on the
# dashboard without asking. Answered from the dashboard map (what is
# where), never with steps for a control nobody here can see.
_DO = r"(?:run|do|pull|build|make|get|create|generate|produce|find|look\s+(?:it|this|that)\s+up)"
_SELF = r"(?:myself|ourselves|on\s+my\s+own|on\s+our\s+own|by\s+myself|without\s+(?:you|asking(?:\s+you)?))"
_SELF_SERVE_RX = re.compile(
    r"\b(?:i|we)(?:'d|\s+would)?\s+(?:want|wanna|need|would\s+like|like|prefer|rather)\s+to\s+" + _DO + r"\b[^.?!]{0,60}?\b" + _SELF + r"\b"
    r"|\b(?:how\s+(?:do|can|would|could)\s+(?:i|we)|can\s+(?:i|we)|could\s+(?:i|we)|is\s+there\s+a\s+way\s+(?:for\s+(?:me|us)\s+)?to|"
    r"let\s+me|show\s+me\s+how\s+to|teach\s+me\s+(?:how\s+)?to)\s+" + _DO + r"\b[^.?!]{0,60}?\b" + _SELF + r"\b"
    r"|\b(?:i(?:'ll|\s+will|\s+can)?|we(?:'ll|\s+will|\s+can)?)\s+(?:just\s+)?" + _DO + r"\s+(?:it|this|that|them)\s+" + _SELF + r"\b", re.I)


def self_serve(text):
    """True when the person says they want to do it themselves on the
    dashboard ("i want to run the report myself")."""
    try:
        t = _clean(text)
        if not t or len(t) > 300 or not _SELF_SERVE_RX.search(t):
            return False
        parts = [p.strip() for p in _SENT_SPLIT_RX.split(t) if p and p.strip()]
        return not any((p.endswith('?') or _data_ask(p)) and not _SELF_SERVE_RX.search(p) for p in parts)
    except Exception:
        return False


def is_dashboard_question(text):
    try:
        return (bool(detect(text)) or self_serve(text)) and not steps_aside(text)
    except Exception:
        return False


def from_decline(data, text, uname='', ask_hint=None):
    """The reasoned read came back saying the message is about the
    dashboard's own controls (action=decline, decline_reason=dashboard):
    the lane's reply, or None for any other decline."""
    try:
        if str((data or {}).get('decline_reason') or '').strip().lower() != 'dashboard':
            return None
        if not uname:
            try:
                from . import promise_truth as _pt
                uname = _pt._who()
            except Exception:
                uname = ''
        raw = answer(text, uname=uname, force=True)
        if raw is not None and callable(ask_hint):
            ask_hint(route=ROUTE, outcome='raised')
        return raw
    except Exception:
        return None


# ------------------------------------------------------------------ the reply
_LEAD = ("That is a question about the dashboard itself, not about the data. I cannot see what that {thing} is doing "
         "from here, so I will not guess at why it did that.")
_NOW = {
    'date': ("What you can do right now: tell me what you want to see and the dates in words, and I will take it from there."),
    'download': ("What you can do right now: every answer with figures in this chat carries its own file link. "
                 "Say 'email me the file' and it goes to your inbox."),
    'page': ("What you can do right now: reload the page. If it still looks wrong, tell me what you were trying to see "
             "and I will get it for you here."),
    'control': ("What you can do right now: tell me what you were trying to get, and I will get it for you here."),
}
_RAISED = "I have passed your note to the Crosswalk team so they can look at it."


def reply_for(hit):
    """(reply, followups) for a detected dashboard question. No figures,
    no profile's name, nothing about why the control did what it did."""
    hit = hit if isinstance(hit, dict) else {}
    fam = str(hit.get('family') or 'control')
    thing = str(hit.get('thing') or 'part of the page')
    parts = [_LEAD.format(thing=thing), _NOW.get(fam, _NOW['control']), _RAISED]
    return '\n\n'.join(parts), []


_SELF_LEAD = "You can do a lot of it yourself."
_SELF_CLOSE = ("I cannot see the controls on a page, so I will not tell you which field or filter to set. "
               "If you tell me what you want to see and the dates in words, I will get it for you here.")


def self_serve_reply(user=None, host=None):
    """The dashboard map's own words on what is where, for the seat's
    own products; then what Prometheus cannot see and what it can do."""
    where = ''
    try:
        from . import dashboard_map as _dm
        try:
            access = _dm.access_for(user, host) if isinstance(user, dict) and user else None
            hidden = _dm.hidden_products(host) if access is not None else None
        except Exception:
            access, hidden = None, None
        where = str(_dm.where_things_are(access, hidden) or '').strip()
        rail = str((_dm.RAIL or {}).get('line') or '')
        if rail and where.endswith(rail):
            where = where[:-len(rail)].strip()       # the chat rail is not part of doing it yourself
    except Exception:
        where = ''
    return '\n\n'.join(p for p in (_SELF_LEAD + ((' ' + where) if where else ''), _SELF_CLOSE) if p), []


def _view_of(ctx):
    """The page the note was typed on, for the team's record only."""
    try:
        vc = (ctx or {}).get('view_context') or {}
        vid = str(vc.get('view_id') or '').strip()
        label = ''
        if vid:
            from . import dashboard_map as _dm
            label = str((_dm.product(vid) or {}).get('label') or '')
        return (label or vid or ('Profile IQ' if (ctx or {}).get('primary') else ''))[:60]
    except Exception:
        return ''


def _raise_to_team(uname, text, reply, ctx, thread_id, hit, hold=None):
    """The held record the ops watch and the daily digest read. A probe
    or a test process writes none (promise_truth decides). Returns the
    record's key or ''."""
    try:
        if hold is None:
            from . import promise_truth as _pt
            hold = _pt.hold
        where = _view_of(ctx)
        reason = ("a reader reported the dashboard itself misbehaving (" + str((hit or {}).get('thing') or 'a control')
                  + (", on " + where if where else '') + "); nothing was run and nothing was charged")
        return str(hold(uname, text, kind=KIND, reason=reason, told=reply, thread_id=str(thread_id or '')) or '')
    except Exception:
        return ''


def answer(text, *, uname='', user=None, ctx=None, history=None, thread_id='', host=None, force=False, hold=None):
    """The lane: the raw analyze payload for a dashboard question, or
    None. ``force`` is the Reader's own verdict (the words still have to
    not be an order, a data question or another lane's ask)."""
    try:
        if not enabled():
            return None
        t = _clean(text)
        if not t or len(t) > _MAX_LEN or steps_aside(t):
            return None
        hit = detect(t)
        if hit is None and self_serve(t):
            reply, chips = self_serve_reply(user, host)
            return {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips), 'offer_deck': False,
                    'deck_angle': None, 'dashboard_behavior': {'family': 'self_serve', 'thing': '', 'why': 'self_serve',
                                                               'raised': False}}
        if hit is None and force:
            if _data_ask(t) or _WORK_RX.search(t) or _WORK_STATUS_RX.search(t) or _other_question(t):
                return None
            thing = _thing(t)
            hit = {'family': _family(t, thing), 'thing': thing, 'why': 'reader'}
        if hit is None:
            return None
        reply, chips = reply_for(hit)
        ref = _raise_to_team(uname, t, reply, ctx, thread_id, hit, hold=hold)
        raw = {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips), 'offer_deck': False,
               'deck_angle': None, 'dashboard_behavior': {'family': hit['family'], 'thing': hit['thing'], 'why': hit['why'],
                                                          'raised': True}}
        if ref:
            raw['held_ref'] = ref
        return raw
    except Exception:
        return None


def core_hook(text, body, history, user, ask_hint=None):
    """The same lane for an ask that reached the analyze core without
    passing the door's lane (a question an open intake let through, an
    older client). None when the words are not a dashboard question or a
    step is being answered."""
    try:
        b = body if isinstance(body, dict) else {}
        if any(b.get(k) for k in ('panel_confirm', 'bind_subject', 'mode', 'overall_ranks', 'bpiq_confirm', 'jiq_confirm',
                                  'fw_confirm', 'aiq_confirm', 'twin_confirm', '_pm_order', '_meta_replay')):
            return None
        u = user if isinstance(user, dict) else {}
        uname = str(u.get('username') or u.get('email') or '').strip()
        raw = answer(text, uname=uname, user=u, ctx=b.get('page_context') or b.get('context'), history=history,
                     thread_id=str(b.get('thread_id') or ''))
        if raw is not None and callable(ask_hint):
            ask_hint(route=ROUTE, outcome=('raised' if (raw.get('dashboard_behavior') or {}).get('raised') else 'answered'))
        return raw
    except Exception:
        return None


# ------------------------------------------------------------------ the output scrub
# Words the 2026-11-18 reply made up. None is a product term; each one
# reads as how the dashboard works inside. A sentence that states a
# control's behaviour as fact goes whole when anything else remains.
_CONTROL_CLAIM_RX = re.compile(
    r"\b(?:start|end|date|from|to|as[- ]of)\s+(?:date\s+)?(?:field|picker|input|box)\b[^.?!\n]{0,80}\b(?:snaps?|reverts?|resets?|"
    r"clamps?|locks?|defaults?|accepts?|rejects?)\b"
    r"|\b(?:date\s+picker|filter\s+bar|drop ?-?down|picker)\b[^.?!\n]{0,60}\b(?:snaps?|reverts?|resets?|clamps?)\b"
    r"|\bthe\s+(?:start|end)\s+(?:date\s+)?(?:snaps?|reverts?|resets?|clamps?)\b"
    r"|\bnot\s+a\s+bug\b|\b(?:is|are)\s+a\s+free\s+field\b|\b(?:window|field|picker|filter)\s+asserting\s+itself\b"
    r"|\bis\s+(?:already\s+)?in\s+historic\s+mode\b|\bextend(?:ing)?\s+the\s+historic\s+lookback\b"
    r"|\bwill\s+not\s+hold\s+inside\s+that\s+(?:set|profile)\b", re.I)
_PROMISE_RX = re.compile(
    r"\bback[- ]window\b|\bquoted\s+first,\s+confirmed,\s+then\s+produced\b|\bships?\s+quoted\s+first\b|"
    r"\b(?:runs?|comes?\s+back|ships?|is\s+delivered)\s+as\s+an?\s+[\w-]+[- ]window\s+(?:build|read|pull|run)\b", re.I)
_SWAPS = (
    (re.compile(r"\s*\((?:the\s+)?(?:live\s+)?run\s+window(?:\s+on\s+the\s+open\s+set)?\)", re.I), ''),
    (re.compile(r"\b(?:the\s+)?live\s+run\s+window\b", re.I), 'the window'),
    (re.compile(r"\b(?:its|their)\s+own\s+run\s+window\b", re.I), 'its own window'),
    (re.compile(r",?\s*run\s+window\s*(?=:)", re.I), ''),
    (re.compile(r"\brun\s+window\b", re.I), 'window'),
    (re.compile(r"\bthe\s+([A-Z][\w+&' -]{1,40}?)\s+set\s+(?:open\s+on\s+your\s+screen|you\s+(?:have|had)\s+open)\b"), r'the \1 profile'),
    (re.compile(r"\bthe\s+set\s+(?:open\s+on\s+your\s+screen|you\s+(?:have|had)\s+open)\b", re.I), 'the profile'),
    (re.compile(r"\bthe\s+open\s+set\b(?!\s+of\b)", re.I), 'the profile'),
    (re.compile(r"\bon\s+the\s+same\s+bases\s+and\s+the\s+same\s+partitions\b", re.I), 'on the same audience'),
    (re.compile(r"\b(?:is|are|was|were)\s+keyed\s+to\b", re.I), 'covers'),
    (re.compile(r"\bkeyed\s+to\b", re.I), 'set to'),
    (re.compile(r"\bthe\s+same\s+partitions\b", re.I), 'the same splits'),
    (re.compile(r"\bpartitions\b", re.I), 'splits'),
)
_SENT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_HIT_RX = re.compile(r"\brun\s+window\b|\bthe\s+open\s+set\b(?!\s+of\b)|\bset\s+(?:open\s+on\s+your\s+screen|you\s+(?:have|had)\s+open)\b|\bkeyed\s+to\b|"
                     r"\bpartitions\b|\bback[- ]window\b", re.I)


def invented(text):
    """True when the text carries a made-up mechanism, a made-up product
    or a control's behaviour stated as fact."""
    t = str(text or '')
    return bool(t.strip()) and bool(_HIT_RX.search(t) or _PROMISE_RX.search(t) or _CONTROL_CLAIM_RX.search(t))


def scrub(text):
    """The text with those words gone. A sentence that states how a
    control behaves, or promises a made-up deliverable, is dropped whole
    when something else remains; the leftover words are swapped for plain
    ones. A text with none of them comes back byte for byte. Idempotent,
    never raises."""
    s = str(text or '')
    try:
        if not invented(s):
            return s
        out_lines = []
        for line in s.split('\n'):
            if not line.strip() or not invented(line):
                out_lines.append(line)
                continue
            lead = re.match(r"^\s*(?:[-*\u2022]\s+)?", line).group(0)
            body = line[len(lead):]
            sents = _SENT_RX.split(body)
            keep = [x for x in sents if not (_CONTROL_CLAIM_RX.search(x) or _PROMISE_RX.search(x))]
            kept = ' '.join(k.strip() for k in keep if k.strip())
            for rx, rep in _SWAPS:
                kept = rx.sub(rep, kept)
            kept = re.sub(r"[ \t]{2,}", ' ', kept).strip()
            if kept:
                out_lines.append(lead + kept)
        out = '\n'.join(out_lines)
        out = re.sub(r"\n{3,}", '\n\n', out).strip()
        return out if out else s
    except Exception:
        return s


def scrub_chips(chips):
    """The chips a reply offers, with a made-up product's chip gone and
    the leftover words swapped. Non-strings pass through."""
    out = []
    try:
        for c in (chips or []):
            if not isinstance(c, str):
                out.append(c)
                continue
            if _PROMISE_RX.search(c) or _CONTROL_CLAIM_RX.search(c):
                continue
            fixed = c
            for rx, rep in _SWAPS:
                fixed = rx.sub(rep, fixed)
            fixed = re.sub(r"[ \t]{2,}", ' ', fixed).strip()
            if fixed:
                out.append(fixed)
        return out
    except Exception:
        return list(chips or [])


# ------------------------------------------------------------------ the prompt rule
PROMPT_RULE = (
    "- THE DASHBOARD'S OWN CONTROLS: you cannot see how a field, date picker, filter, button, download or page behaves. "
    "A message about one (it will not take a value, it changed what was typed, it will not load) is not a data question: "
    "never explain it and never answer it with figures. Say in two plain sentences that you cannot see what that control "
    "is doing and that they can give you the subject and dates in words. Never name a product, build or deliverable this "
    "prompt does not name.")
PROMPT_RULE_SHORT = (
    "- A message about how the dashboard's own controls behave (a field, a date picker, a filter, a button, a page) is "
    "not a measurement: return action=decline with decline_reason=dashboard. Never explain a control, and never name a "
    "product, build or deliverable this prompt does not name.")
