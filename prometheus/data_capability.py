"""A question about whether the data can answer something gets "Yes."
(Jenna 2026-10-26).

Mark Valdez at Bent Pixels asked, with a YouTube profile open: "is it
possible from your data to know how much of connected TV watchtime is
spent on youtube in general for the US?" The reply was a product pitch
that quoted the reader's own third-person note back to him ("From what
you describe (They want to know whether Crosswalk can measure ...), that
is the one. Tell me the subject and the window ..."). Jenna: the answer
here should just be "yes".

So: a yes-answerable question about what the data can show is answered
with the word and nothing else in prose. The person's own question, with
the capability preamble taken off and the first letter capitalized, rides
as the one chip when it reads as a question on its own ("How much of
connected TV watchtime is spent on youtube in general for the US?"). It is
cut from their words by pattern, never paraphrased; when the preamble does
not match cleanly there is no chip.

What this lane is NOT (the existing boundaries decide; nothing new is
drawn here):
  * behavior with no digital trace (linear TV, in-store, foot traffic,
    radio) is declined by the quantifiability gate with the nearest
    measurable read; ``answer`` returns None for it so the gate's copy
    stands wherever the two are called in either order;
  * an order with manners ("can you build me a Nike profile", "could you
    size the Costco audience": smarter.polite_order) and work asked for
    behind a can-you frame ("can you look at X and tell me Y", "can you
    tell me how many ...": smarter.advisory_order, the 2026-10-22 audit)
    stay the work they are;
  * a question about what the product does (a cut, a deck, a profile, a
    journey, a campaign, a comparison, a Subscriber IQ tracker, a price)
    keeps its product answer;
  * a question about material on the thread ("can you see the full url
    now?") belongs to the document lane;
  * an action Prometheus cannot take (rename, refund, schedule) keeps the
    honest cant-do answer; a status or delivery question keeps its lane.

Pure module: no Flask, no S3, no model. Kill switch ``PM_DATA_CAPABILITY=0``.
"""
from __future__ import annotations

import os
import re

REPLY = 'Yes.'

_MAX_LEN = 400
_MAX_CHIP = 160

# optional lead words before the question itself
_LEAD = (r"^\W*(?:(?:so|and|also|hey|hi|hello|ok|okay|just curious|quick question|one more|"
         r"one more question|question|prometheus|crosswalk)[,.:!\s]+)*")

_DATA_NOUN = (r"(?:your|the|crosswalk'?s?|prometheus'?s?|this|that|our|its) "
              r"(?:(?:own |first[- ]party |current |existing |available |us |u\.s\. )?"
              r"(?:data|dataset|data set|numbers|figures|measurements?|tracking|database|records|coverage))")
_FROM_DATA = rf"(?:(?:from|with|in|using|through|via|based on|given|off|off of|out of|within) {_DATA_NOUN})"
_FOR_YOU = r"(?:for (?:you|crosswalk|prometheus) )?"

# knowledge and visibility verbs: the ask is whether the data can show it
_KNOW = (r"(?:know|see|tell|measure|track|quantify|find out|figure out|determine|identify|understand|learn|"
         r"say|show|answer|gauge|calculate|count|capture|detect|distinguish|estimate|assess|confirm|verify|check|"
         r"speak to|reveal|indicate|isolate|look at)")
# the narrower set behind "can you" / "are you able to": an opinion
# ("would you say") or a check on something ("can you confirm") is not a
# question about what the data can show
_KNOW_YOU = (r"(?:know|see|tell|measure|track|quantify|find out|figure out|determine|identify|count|capture|"
             r"detect|distinguish|isolate|gauge|calculate)")

_OPENERS = tuple(re.compile(_LEAD + p, re.I) for p in (
    # is it possible (from your data) to know X
    rf"is (?:it|this|that) (?:(?:even |at all |actually |really )?possible|feasible|doable|realistic) "
    rf"{_FROM_DATA}? ?{_FOR_YOU}to {_KNOW}(?: {_FROM_DATA})?",
    # is there (any) way (from your data) to know X / would it be possible to know X
    rf"(?:is there (?:a|any|some) way|would it be possible|would there be a way) {_FROM_DATA}? ?{_FOR_YOU}to {_KNOW}"
    rf"(?: {_FROM_DATA})?",
    # can your data show X / does the data tell X / is your data able to tell X
    rf"(?:can|could|does|do|would|will|is|are) {_DATA_NOUN} "
    rf"(?:(?:able|enough|good enough|granular enough|detailed enough|deep enough) to )?{_KNOW}(?: (?:me|us))?",
    # do you have data on X / is there data on X
    r"(?:do|does|would|did) (?:you|crosswalk|prometheus|it) (?:(?:already |actually |even |happen to )?"
    r"(?:have|hold|carry|keep|collect|capture|track|get|offer|provide|include|contain)) "
    r"(?:any |the |some |good |reliable |enough |solid |real |hard |actual |first[- ]party |us |u\.s\. )?"
    # ("do you have anything on X" / "what do you have on X" ask about the
    # library and stay the catalog lane's)
    r"(?:data|datasets?|numbers|figures|stats|statistics|metrics|measurements?|information|info|insight|insights|"
    r"visibility|a view|a read|reads|coverage|intel|signal) "
    r"(?:on|about|for|into|of|around|regarding|covering|concerning|related to|that (?:shows?|covers?|tracks?|tells?))",
    rf"is there (?:any |some |good |reliable |enough )?(?:data|a dataset|visibility|insight|coverage|signal|a read) "
    rf"(?:{_FROM_DATA} )?(?:on|about|for|into|of|around|regarding|covering)",
    # do you know X / would you know X / do you have any idea X
    r"(?:do|would|did) (?:you|crosswalk|prometheus) (?:happen to )?(?:know|have (?:any |an )?(?:idea|sense|insight|"
    r"visibility|read|view)(?: (?:of|on|into|about|as to))?)",
    # are you able to see X / could you measure X / can you track X
    rf"(?:are|is) (?:you|crosswalk|prometheus|it) able to {_KNOW_YOU}",
    rf"(?:can|could|would|will) (?:you|crosswalk|prometheus|it) (?:(?:actually|even|also|please) )?{_KNOW_YOU}",
))

# the product and its actions: a question that carries these keeps the
# product answer (smarter.capability_ask, guards.capability_answer, the
# pricing lane, the status and deliverable lanes); the actions Prometheus
# cannot take are read by capabilities.action_request below
_PRODUCT_RX = re.compile(
    r"\b(?:build|builds|building|built|run|runs|running|pull|pulls|pulling|create|creating|make|making|generate|"
    r"draft|drafting|set up|setting up|spin up|refresh|re-?run|rebuild|cut|cuts|cutting|split|splitting|"
    r"compare|comparing|comparison|side by side|price|prices|pricing|cost|costs|credits?|"
    r"profile|profiles|deck|decks|slides?|presentation|powerpoint|pptx|one[- ]pager|csv|file|files|spreadsheet|"
    r"download|export|e-?mail|send|queue|queued|status|eta|finished|ready|avid|"
    r"profile iq|subscriber iq|digital journey|trends iq|impact iq|analysis iq|brand partnership|attribution)\b", re.I)

_WH_RX = re.compile(r"^(?:how|what|which|where|when|who|whom|whose|why)\b", re.I)
_PRONOUN_LEAD_RX = re.compile(r"^(?:me|us|you)\b[,\s]*", re.I)


def enabled():
    return str(os.environ.get('PM_DATA_CAPABILITY', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').split())


def _match(t):
    for rx in _OPENERS:
        m = rx.match(t)
        if m:
            return m
    return None


def _product_question(t):
    """The ask is about what the product does, or is an order for it."""
    if _PRODUCT_RX.search(t):
        return True
    try:
        from . import smarter as _sm
        if _sm.polite_order(t) or _sm.advisory_order(t) or _sm._MATERIAL_RX.search(t) or _sm._DECK_ASK_RX.search(t):
            return True
        # the product-specific families (attribution, journey, partnership,
        # subscriber) keep the product answer; the generic last entry is
        # the catch-all every data question would hit
        if any(rx.search(t) for rx, _n, _w, _c in _sm._PRODUCT_WORDS[:-1]):
            return True
    except Exception:
        pass
    try:
        from . import guards as _g
        if _g.capability_answer(t) or _g.is_provenance_question(t) or _g.is_sample_size_ask(t):
            return True
    except Exception:
        pass
    try:
        from . import capabilities as _caps
        if _caps.action_request(t):
            return True
    except Exception:
        pass
    return False


def _owned_elsewhere(t):
    """A status, delivery, roster or no-digital-trace ask keeps its lane."""
    try:
        import prometheus_analysis as _pma
        if _pma.classify_quantifiability(t):
            return True
    except Exception:
        pass
    try:
        from . import status_lane as _stl
        from . import deliverables as _dlv
        if _stl.wants_status_fast(t) or _dlv.parse(t):
            return True
    except Exception:
        pass
    try:
        from . import catalog_lane as _cl
        if _cl.roster_ask(t):
            return True
    except Exception:
        pass
    return False


def is_data_capability_question(text):
    """True for a yes-answerable question about what the data can show."""
    if not enabled():
        return False
    t = _clean(text)
    if not t or len(t) > _MAX_LEN:
        return False
    m = _match(t)
    if not m:
        return False
    rest = t[m.end():].strip(' ,:;-')
    rest = _PRONOUN_LEAD_RX.sub('', rest).strip(' ?.!')
    if len(rest.split()) < 2:
        return False
    if _product_question(t) or _owned_elsewhere(t):
        return False
    return True


def question(text):
    """The person's own question with the capability preamble removed and
    the first letter capitalized, or '' when the preamble does not match
    cleanly or what remains does not read as a question on its own."""
    t = _clean(text)
    m = _match(t)
    if not m:
        return ''
    rest = t[m.end():].strip(' ,:;-')
    rest = _PRONOUN_LEAD_RX.sub('', rest).strip()
    rest = rest.rstrip(' ?.!').strip()
    if not rest or not _WH_RX.match(rest) or len(rest) > _MAX_CHIP - 1:
        return ''
    return rest[0].upper() + rest[1:] + '?'


def answer(text):
    """{'reply': 'Yes.', 'followups': [...]} for a yes-answerable question
    about what the data can show, else None."""
    if not is_data_capability_question(text):
        return None
    chip = question(text)
    return {'success': True, 'action': 'answer', 'reply': REPLY,
            'followups': [chip] if chip else [], 'offer_deck': False, 'deck_angle': None,
            'capability_yes': True}
