"""What Prometheus does, in one or two sentences (Jenna 2026-11-05:
"update the prometheus client guide ... and all other updates like the
brand partnership valuation wizard").

Three questions people ask the chat about the product itself, answered
the same way the client guide answers them, from the product as it
reads today, with the live prices:

  overview    "what can you do", "what can I ask you", "how can you help"
  bulk_list   "can I upload a list of names", "do you do bulk profiles",
              "how do I pull profiles for everyone on a spreadsheet"
  valuation   "how do I value a partnership", "how does the brand
              partnership valuation work", "what is Brand Partnership IQ"
  twin_pricing "what does a focus group cost", "how much is a pilot
              test", "digital twins pricing": the per-group card
              (Jenna 2026-11-05, migration/digital_twin_pricing)
  data_history "how far back can we pull a window", "what is the
              earliest date", "how much history do you have", "what
              years do you have", "can I pull 2019": the data's reach,
              in one plain line, the date read from the one place that
              defines it (migration.event_window.DATA_FLOOR, through
              prometheus.data_floor). Jenna 2026-11-23, after Armine
              Meseldzhyan (Lionsgate) asked "how back can we pull a
              window" and got the working-on-it line from a read that
              had started: "this should have answered her you can go
              back to jan 1 2021". An ask that names a year before
              the floor is told plainly the window would start on the
              floor; one that names a covered year is told it is
              covered. The typo is matched on purpose.
  dashboard   "list every Ranker in the Rankers product and what each
              ranks", "does the dashboard have a Share of Time view",
              "which social platforms does the Social feed in Trends
              cover", "what tabs does Trends have": the dashboard's own
              products, tabs, views and feeds, answered from
              prometheus.dashboard_map, which reads them from the place
              the dashboard defines them (trends_tabs, trends_iq's
              platform lists and the live store, the dropdown). Liz
              2026-11-23: the question was split into three parts, the
              first drew "Which audience should I read for this?" and
              the rest the stock read line. Jenna: "should list what
              products we have within these as shes asking". A single
              dashboard question is one question: multi_ask never splits
              it, and a view the dashboard does not have is a plain no.

A question any of these answers never starts a read and never gets the
working-on-it line: ``read_guard`` is the check the read lane and the
priced classifier run before any work (the same Armine miss).

Boundaries (an order wins over this lane):
  * a bulk order with a names sheet on the thread ("pull profiles for
    everyone on this list" with the CSV attached) is the bulk door's
    (prometheus.bulk_pull); the caller passes ``has_names_list`` so the
    question form is answered here and the order form runs there;
  * a valuation order that names its subject ("value the Gunna x Nike
    partnership", "how do I value the Stanley deal") is the wizard's;
    only the general question lands here;
  * a question about an attached file, a status ask, a data question
    and a deck ask keep their own lanes (none of them match here).

Pure module: no Flask, no S3, no model. Prices come from the caller's
``price_fn(tool_key, default_usd)`` (the live rate for the seat) and
fall back to the published rate card. Kill switch ``PM_PRODUCT_FACTS=0``.
"""
from __future__ import annotations

import os
import re

from . import rate_card as _rc

__all__ = ['FAMILIES', 'enabled', 'looks_like', 'family_of', 'answer', 'read_guard', 'price_fn_for', 'thread_has_names', 'facts',
           'positioning_ask', 'positioning_answer', 'POSITIONING_LINES', 'data_history_ask', 'data_history_answer',
           'dashboard_ask', 'dashboard_answer', 'dashboard_cues']

FAMILIES = ('overview', 'bulk_list', 'valuation', 'twin_pricing', 'positioning', 'data_history', 'dashboard')
_MAX_LEN = 320

_LEAD = r"^\W*(?:(?:hi|hey|hello|ok|okay|so|and|also|prometheus|quick question|one more|one more thing)[,!.:\s]+)*"
_OVERVIEW_RX = re.compile(
    _LEAD + r"(?:"
    r"what (?:all |else )?(?:can|could|do|does) (?:you|prometheus|crosswalk|this (?:thing|tool|chat)|it) (?:do|help (?:me |us )?with|answer|handle|offer|know)\b"
    r"|what (?:are|is) (?:you|prometheus|this) (?:able to do|for|good at|capable of)\b"
    r"|what (?:kinds? of |sorts? of |types? of )?(?:things|questions|asks|stuff) can (?:i|we) ask(?: (?:you|here|prometheus))?\b"
    r"|what can (?:i|we) (?:ask|do|get|run|pull|build) (?:you|here|from you|in here|with you|with prometheus)\b"
    r"|what (?:are|is) your capabilit(?:y|ies)\b"
    r"|(?:tell|show) me what (?:you|prometheus) (?:can do|do(?:es)?)\b"
    r"|how (?:can|could|do) (?:you|prometheus) help(?: me| us)?\b"
    r"|(?:give me |i want )?(?:an? )?(?:quick )?(?:overview|rundown|tour|summary) of (?:what (?:you|prometheus) (?:can )?do|prometheus|your (?:features|capabilities|products))\b"
    r"|what (?:do|does) (?:you|prometheus) do\b"
    r"|help me understand what (?:you|prometheus) (?:can )?do\b"
    r")", re.I)

_Q_FRAME = (r"(?:can|could|may|do|does|would|will|should|is it possible(?: for me)? to|is there (?:a|any) way (?:for me )?to|"
            r"how (?:do|can|would|could|should) (?:i|we|you)|what (?:about|if)|are you able to|do you (?:support|allow|handle|offer|take|accept|do))")
_LIST_NOUN = r"(?:csv|list|spreadsheet|sheet|xlsx|excel|file|roster|batch|column)"
_PEOPLE = r"(?:names|people|talent|talents|subjects|brands|titles|profiles|creators|influencers|celebrities|celebs|athletes|artists|companies|shows|accounts|entities|rows)"
_BULK_RX = re.compile(
    r"(?:"
    r"\b" + _Q_FRAME + r"\b[^?]{0,40}?\b(?:upload|attach|send|give|paste|drop|hand|load|import|submit)\b[^?]{0,30}?\b" + _LIST_NOUN + r"\b"
    r"|\b" + _Q_FRAME + r"\b[^?]{0,50}?\b(?:profiles?|pulls?|builds?)\b[^?]{0,40}?\b(?:in bulk|for (?:a|the|my|an entire|a whole) " + _LIST_NOUN + r"|for (?:everyone|everybody|each (?:name|person|one)|all (?:of )?(?:the |these |those )?" + _PEOPLE + r"|all of them) (?:on|in) (?:a|an|the|my|our|this|that) " + _LIST_NOUN + r"|at once|all at once|in one go|from (?:a|an|the|my|our|this|that) " + _LIST_NOUN + r")"
    r"|\b(?:bulk|batch) (?:upload|pull|pulls|build|builds|profiles?|profile iq|order|request)s?\b"
    r"|\bprofiles? (?:in bulk|for (?:a|the|my|a whole) (?:list|csv|spreadsheet|roster))\b"
    r"|\b(?:many|multiple|several|a lot of|dozens of|hundreds of|\d{2,4}) (?:profiles|people|names|subjects|talent|brands) (?:at once|at a time|in one (?:go|shot|call)|from (?:a|one) " + _LIST_NOUN + r")\b"
    r"|\bupload (?:a |an |my |the )?" + _LIST_NOUN + r" of " + _PEOPLE + r"\b"
    r")", re.I)
_TWIN_RX = re.compile(r"\b(?:digital\s+)?twins?\b|\burls?\b|\blinks?\b", re.I)
_STATUS_RX = re.compile(r"\b(?:how (?:is|are|'s) (?:my|the) (?:list|bulk|batch)|status of (?:my|the) (?:list|bulk|batch)|where is my (?:list|bulk))\b", re.I)

_VAL_FRAME = (r"(?:how (?:do|can|would|should|could|does|did) (?:i|we|you|one|it|this|that|prometheus|crosswalk)|what (?:is|does|are|do)|"
              r"is there a way to|can (?:i|we|you)|could (?:i|we|you)|do you|explain|walk me through|tell me (?:about|how))")
_VALUATION_RX = re.compile(
    r"(?:"
    r"\b" + _VAL_FRAME + r"\b[^?]{0,60}?\b(?:value|valuation|valuing|valuate|price|measure|assess|size up|quantify)\b[^?]{0,40}?\b(?:(?:brand |talent |creator |celebrity )?partnerships?|sponsorships?|brand deals?|endorsements?|collab(?:oration)?s?)\b"
    r"|\b" + _VAL_FRAME + r"\b[^?]{0,60}?\b(?:partnership|sponsorship)\s+(?:valuation|value)s?\b"
    r"|\b(?:brand partnership(?: iq| valuation)?|bpiq|partnership valuation|the valuation wizard)\b[^?]{0,40}?\b(?:work|works|do|does|steps?|how|involve|include|cost|price)\b"
    r"|\b(?:how|what) (?:does|is|do) (?:the )?(?:brand )?partnership (?:valuation|iq|tool|wizard|read)\b"
    r")", re.I)
_NAME_OK = frozenset(('i', 'prometheus', 'crosswalk', 'brand', 'partnership', 'partnerships', 'valuation', 'iq', 'bpiq', 'profile',
                      'flywheel', 'digital', 'twins', 'twin', 'journey', 'attribution', 'subscriber', 'us', 'a', 'the', 'what', 'how',
                      'can', 'is', 'does', 'do', 'explain', 'tell', 'walk', 'value', 'please', 'hi', 'hey', 'hello', 'ok', 'okay'))


# positioning: what Prometheus (or Crosswalk) is, how it differs from an AI tool, a description, tagline or
# headline for it, "how would you define yourself" (Liz 2026-11-20: "give me a 3-5 word description for CW
# prometheus versus AI" drew "Which subject should this be on?", and the re-ask ran as a read and died). A
# question about the product itself is answered from product knowledge, never routed to a subject.
_SELF = r"(?:cw\s+prometheus|crosswalk\s+prometheus|prometheus|crosswalk|cw|you|yourself|yourselves|this\s+(?:tool|chat|chatbot|assistant|product)|the\s+(?:tool|chatbot|assistant))"
_AI = r"(?:ai|a\.i\.|an?\s+ai(?:\s+tool)?|chat\s?gpt|gpt|gemini|claude|copilot|perplexity|an?\s+llm|llms?|(?:a\s+)?(?:general|generic|regular|normal|plain|other)\s+(?:ai|chatbot|assistant)s?|(?:other\s+)?(?:ai\s+)?(?:tools|chatbots|assistants))"
_DESC_NOUN = r"(?:descriptions?|taglines?|headlines?|one[- ]liners?|slogans?|positioning(?:\s+statement)?|elevator\s+pitch|pitch|blurbs?|boilerplate|summary|definition|bio|intro(?:duction)?|value\s+prop(?:osition)?|differentiators?)"
_POSITIONING_RX = re.compile(
    _LEAD + r"(?:"
    r"(?:give|write|draft|send|get|need|want|create|come\s+up\s+with|suggest|tell)\s+(?:me\s+|us\s+)?(?:an?\s+|the\s+|some\s+)?(?:\d+(?:\s*(?:-|to)\s*\d+)?\s*[- ]?words?\s+)?(?:short\s+|quick\s+|crisp\s+|punchy\s+|one[- ]line\s+|marketing\s+)?" + _DESC_NOUN + r"\s+(?:for|of|on|about|describing)\s+" + _SELF + r"\b"
    r"|(?:an?\s+|the\s+)?(?:short\s+|quick\s+|marketing\s+)?" + _DESC_NOUN + r"\s+(?:for|of|on|about)\s+" + _SELF + r"\b"
    r"|.*?\b" + _SELF + r"\s+(?:versus|vs\.?|compared\s+(?:to|with)|against|over)\s+" + _AI + r"\b"
    r"|.*?\b(?:how|what)\s+(?:is|are|does|do|'s)\s+" + _SELF + r"\s+(?:different|differ|better|unlike|not)\b"
    r"|.*?\bhow\s+(?:would|do|should|can|could)\s+(?:you|prometheus|crosswalk)\s+(?:define|describe|position|explain|introduce|sum\s+up|summari[sz]e)\s+(?:yourself|yourselves|itself|prometheus|crosswalk|what\s+you\s+(?:are|do))\b"
    r"|.*?\b(?:define|describe|position|introduce|sum\s+up|summari[sz]e)\s+(?:yourself|yourselves|prometheus|crosswalk|cw\s+prometheus)\b"
    r"|.*?\bwhat\s+(?:is|are|'s)\s+(?:cw\s+prometheus|crosswalk\s+prometheus|prometheus|crosswalk)(?:\s+(?:exactly|really|anyway|in\s+a\s+(?:nutshell|sentence|line|few\s+words)))?\s*\??$"
    r"|(?:who|what)\s+are\s+you\s*\??$"
    r"|.*?\bwhat\s+(?:makes|sets)\s+(?:you|prometheus|crosswalk)\s+(?:different|apart|unique|special|stand\s+out)\b"
    r"|.*?\bwhy\s+(?:use|choose|pick|trust)\s+(?:you|prometheus|crosswalk)\s+(?:over|instead\s+of|rather\s+than|versus|vs\.?)\s+" + _AI + r"\b"
    r"|.*?\b(?:are|is)\s+(?:you|prometheus|this)\s+(?:just\s+)?(?:an?\s+)?(?:ai|chat\s?gpt|llm|chatbot|ai\s+tool|ai\s+model|language\s+model)\b"
    r"|.*?\bheadline\s+on\s+prometheus\b|.*?\bmarketing\s+headline\b.{0,40}\bprometheus\b|.*?\bprometheus\b.{0,40}\bmarketing\s+headline\b"
    r")", re.I)

# Jenna's own answer to Liz (2026-11-20 21:07Z), word for word; the one approved copy for the positioning question
POSITIONING_LINES = ('Answers from real consumer behavior.', 'Real behavior, not internet guesses.', 'Grounded in what people do.')
POSITIONING_WHY = ("My pick is the first. It says in plain words what sets Prometheus apart from a general AI tool: a general tool "
                   "answers from what has been written online, and Prometheus answers from what 30M+ opted-in US consumers actually do "
                   "across their full digital life.")


def positioning_ask(text):
    """True when the words ask what Prometheus or Crosswalk is, how it
    differs from an AI tool, or for a description, tagline or headline
    of it."""
    t = _clean(text)
    if not t or len(t) > _MAX_LEN:
        return False
    if re.search(r"\bprometheus\s+(?:the\s+)?(?:titan|myth|god|film|movie|show|series|book|play|opera|bound)\b", t, re.I):
        return False   # the Titan, not the product
    return bool(_POSITIONING_RX.search(t))


POSITIONING_WHAT = ("Prometheus answers from what 30M+ opted-in US consumers actually do across their full digital life: what they watch, "
                    "stream, buy, search and subscribe to. A general AI tool answers from what has been written online. Ask me about any US "
                    "audience and the answer comes from real behavior, not internet guesses.")
_WANTS_LINES_RX = re.compile(r"\b(?:" + _DESC_NOUN + r"|\d+\s*(?:-|to)\s*\d+\s*[- ]?words?|words?\s+or\s+fewer|in\s+\d+\s+words|define\s+yourself|"
                             r"describe\s+yourself|sum\s+up|summari[sz]e|headline)\b", re.I)


def positioning_answer(text=''):
    """Jenna's three lines with her pick for a description, tagline or
    headline ask; the one-paragraph answer for "what is Prometheus" and
    "how is it different from AI"."""
    if text and not _WANTS_LINES_RX.search(_clean(text)):
        return POSITIONING_WHAT
    lines = '\n'.join(f"{i}. {x}" for i, x in enumerate(POSITIONING_LINES, 1))
    return f"Here are three, each five words or fewer:\n\n{lines}\n\n{POSITIONING_WHY}"


# data_history: how far back the data goes (Armine Meseldzhyan, Lionsgate, 2026-11-23: "how back can we pull a
# window" ran as a read and drew the working-on-it line; Jenna: "this should have answered her you can go back
# to jan 1 2021"). The gate in prometheus.data_floor already answers "how far back does the data go" ahead of
# every lane; this family is the net under it for the shapes that gate does not read (the typo, "can we pull a
# window", "how much history", "what years do you have", a bare year after "can I pull"). The date comes from
# the one place that defines it; nothing here spells it.
_YR20 = r"20\d{2}"
_YEAR_TOKEN_RX = re.compile(r"(?<![\d/.$-])(" + _YR20 + r")(?![\d/.%-])")
_DATA_NOUN = r"(?:data|dataset|history|numbers|records|coverage|windows?|reads?|pulls?|measurement|tracking|information|info|panel)"
_PULL_VERB = r"(?:pull|go|look|run|reach|see|get|read|query|search|measure|track|take)"
_WE = r"(?:we|i|you|it|the\s+data|data|that|this|your\s+data|our\s+data)"
_DATA_HISTORY_RX = re.compile(
    r"(?:"
    # "how far back can we pull", "how back can we pull a window" (the typo), "how long back does it go"
    r"\bhow\s+(?:far\s+|long\s+|much\s+|many\s+(?:years|months)\s+)?back\s+(?:can|could|do|does|did|are|is|will|would|should|may|might)\b"
    r"|\bhow\s+(?:far|long)\s+(?:back\s+)?(?:does|do|can|could|will|would)\s+(?:the\s+|your\s+|our\s+|this\s+)?" + _DATA_NOUN + r"\b"
    r"|\bhow\s+(?:far|long)\s+(?:can|could|do|does|will|would)\s+" + _WE + r"\s+(?:" + _PULL_VERB + r"\s+)?back\b"
    r"|\bhow\s+much\s+(?:history|historical\s+data|back\s+data|data\s+history|lookback|look\s+back)\b"
    r"|\bhow\s+many\s+years\s+(?:of\s+(?:data|history)|back|do\s+you\s+(?:have|cover|go)|can\s+(?:we|i|you)|does\s+(?:the\s+data|it))\b"
    r"|\b(?:what(?:'s|\s+is|\s+are)?|which(?:\s+is)?)\s+(?:the\s+|your\s+)?(?:earliest|oldest|first)\s+(?:date|data|day|year|month|window|period|point|start(?:\s+date)?)\b"
    r"|\b(?:earliest|oldest)\s+(?:date|data|day|year|month|window|period)\s+(?:available|possible|you\s+(?:have|can\s+pull)|we\s+(?:have|can\s+pull)|i\s+can\s+pull|on\s+file|in\s+the\s+data)\b"
    r"|\bwhat\s+years?\s+(?:do\s+(?:you|we)\s+(?:have|cover|go\s+back\s+to)|(?:are|is)\s+(?:available|covered|in\s+the\s+data|on\s+file)|can\s+(?:we|i)\s+(?:pull|run|get|look\s+at|see|use))\b"
    r"|\bwhen\s+(?:does|do|did)\s+(?:the\s+|your\s+|our\s+)?(?:data|history|coverage|tracking|records?|measurement)\s+(?:begin|start)\b"
    r"|\b(?:data|history|coverage)\s+(?:go(?:es)?|went|reach(?:es)?|extends?|stretch(?:es)?)\s+back\b"
    r"|\b(?:can|could|do|does|is|are|will|would)\s+" + _WE + r"\s+(?:even\s+|only\s+)?(?:go|look|reach|pull|run|get|see|read)\s+back\s+"
    r"(?:to\s+(?:(?:19|20)\d{2}|\d+\s+years?|the\s+(?:start|beginning)|before|earlier|then)|that\s+far|further|farther|earlier|as\s+far\s+as|before|past|beyond)\b"
    # a bare year after the asking frame: "can I pull 2019?", "can we pull 2019 data", "could we run a window in 2020"
    r"|\b(?:can|could)\s+(?:we|i|you)\s+(?:pull|run|get|see|look\s+at|read|do|use|query|access|have)\s+(?:a\s+|any\s+)?(?:(?:window|read|pull|data|numbers|anything|something|figures)\s+(?:in|for|from|back\s+to|as\s+far\s+back\s+as|starting(?:\s+in)?)\s+|(?:data\s+|numbers\s+)?(?:for|from|in)\s+)?" + _YR20 + r"\b"
    r"|\bis\s+" + _YR20 + r"\s+(?:available|covered|possible|in\s+(?:the\s+)?(?:data|range|window)|on\s+file|too\s+(?:far|early|old))\b"
    r"|\b(?:do|does)\s+(?:you|we|the\s+data|it)\s+(?:have|cover|include|carry|hold)\s+(?:any\s+)?(?:data\s+|numbers\s+|history\s+)?(?:for|from|in|back\s+to)?\s*" + _YR20 + r"\b"
    r"|\b(?:do|does)\s+(?:you|we|the\s+data|it)\s+(?:have|carry|hold|keep)\s+(?:any\s+)?(?:historical|older|earlier|past|back)\s+(?:data|history|windows?|years|reads?)\b"
    r"|\bwhat\s+(?:is|'s)\s+(?:the\s+|your\s+)?(?:data|history|coverage)\s+(?:range|start\s+date|lookback|look\s+back)\b"
    r")", re.I)


def _floor():
    """(label, sentence, year) from the one place the earliest date lives."""
    from . import data_floor as _dfl
    return _dfl.FLOOR_LABEL, _dfl.SENTENCE, int(str(_dfl.FLOOR)[:4])


def _years_named(t):
    """The years the words name as years (a year that only describes the
    subject, 'came out in 2019', is not one), ascending."""
    try:
        from . import inline_dates as _idw
        told = set(int(y) for y in _idw.described_years(t))
    except Exception:
        told = set()
    return sorted(set(int(y) for y in _YEAR_TOKEN_RX.findall(t)) - told)


def data_history_ask(text):
    """The words ask how far back the data goes. A year-naming frame that
    also names a subject ('can we pull 2024 data for Netflix') is work,
    not this question."""
    t = _clean(text)
    if not t or len(t) > _MAX_LEN or not _DATA_HISTORY_RX.search(t):
        return False
    if _YEAR_TOKEN_RX.search(t) and _names_a_subject(t):
        return False
    return True


def data_history_answer(text=''):
    """One plain line: any window from the floor to today, the house
    sentence, and what that means for a year the words name."""
    label, sentence, floor_year = _floor()
    base = f"You can pull any window from {label} to today. {sentence}"
    years = _years_named(_clean(text))
    early = [y for y in years if y < floor_year]
    if early:
        return f"{base} A window from {early[0]} would start {label}."
    from datetime import date as _date
    covered = [y for y in years if floor_year <= y <= _date.today().year]
    if covered:
        span = f"{covered[0]}" if len(covered) == 1 else f"{covered[0]} to {covered[-1]}"
        return f"{base} {span} is covered."
    return base


# dashboard: the dashboard's own products, tabs, views and feeds (Liz 2026-11-23). What exists and what each one
# does; never where it is (that is prometheus.wayfinding, ahead of this lane) and never the data inside a board.
_DASH_MAX_LEN = 900
_DASH_SELF_RX = re.compile(
    r"\b(?:(?:question|ask(?:ing)?)\s+about\s+(?:the\s+)?(?:crosswalk\s+)?dashboard\s+itself|about\s+the\s+dashboard\s+itself|"
    r"not\s+about\s+a\s+(?:title|subject|show|brand|person))\b", re.I)
_DASH_NOUN = r"(?:rankers?|ranker\s+tabs?|boards?|tabs?|views?|pages?|screens?|feeds?|products?|sections?|lists?|modules?|lenses)"
_DASH_PLACE = r"(?:the\s+)?(?:crosswalk\s+)?(?:dashboard|select\s+product\s+(?:dropdown|menu)|product\s+(?:menu|dropdown)|rankers\s+product|trends\s+product|rankers|trends)"
_DASH_LIST_RX = re.compile(
    r"\b(?:list|name|show\s+me|tell\s+me|give\s+me|what\s+are|what\s+is|what's|which|how\s+many|enumerate|walk\s+me\s+through|describe)\b"
    r"[^?.!]{0,40}?\b(?:every|all|each|the|your|our|the\s+full\s+list\s+of|the\s+list\s+of)?\s*" + _DASH_NOUN + r"\b[^?.!]{0,60}?\b(?:in|on|of|inside|within|under|across)\s+" + _DASH_PLACE + r"\b", re.I)
_DASH_HAVE_RX = re.compile(
    r"\b(?:what|which)\s+" + _DASH_NOUN + r"\s+(?:do|does|are|is)\s+(?:there|available|offered|live|on|in)\b"
    r"|\b(?:what|which)\s+" + _DASH_NOUN + r"\s+(?:do\s+(?:you|we)|does\s+(?:the\s+dashboard|rankers|trends|it|crosswalk))\s+(?:have|offer|carry|include|cover|show)\b"
    r"|\b(?:does|do|is|are)\s+(?:the\s+)?(?:dashboard|rankers|trends|crosswalk|you|we|it)\s+(?:have|has|got|offer|include|carry|show|cover)\s+(?:a|an|any|the)?\s*[^?.!]{1,50}?\b(?:view|tab|page|board|ranker|feed|product|screen|section|lens)s?\b"
    r"|\bis\s+there\s+(?:a|an|any|still\s+a|still\s+an)\s+[^?.!]{1,50}?\b(?:view|tab|page|board|ranker|feed|product|screen|section|lens)s?\b"
    r"|\bwhether\s+(?:the\s+)?(?:dashboard|rankers|trends|crosswalk|it|we|you)\s+(?:has|have|offers?|includes?|carr(?:y|ies)|shows?|covers?|still\s+has|still\s+have)\s+(?:a|an|any|the)?\s*[^?.!]{1,50}?\b(?:view|tab|page|board|ranker|feed|product|screen|section|lens)s?\b", re.I)
_DASH_RANKS_RX = re.compile(
    r"\bwhat\s+(?:does|do)\s+(?:each|every|the|a)?\s*(?:one|ranker|rankers|board|tab|tabs|list|lists|it|they)\b[^?.!]{0,30}?\brank\b"
    r"|\bwhat\s+(?:each|every)\s+(?:one|ranker|board|tab|list)\s+(?:ranks|shows|covers|tracks|lists|measures)\b"
    r"|\bwhat\s+(?:is|are|'s)\s+(?:ranked|covered|tracked|listed)\s+(?:in|on|under)\s+(?:the\s+)?[^?.!]{1,40}?\b(?:ranker|rankers|tab|board)s?\b"
    r"|\bwhat\s+(?:does|do)\s+(?:the\s+)?[A-Za-z][\w+ ]{1,30}?\s+(?:ranker|tab|board|list)\s+(?:rank|show|cover|track|list|measure)\b", re.I)
_DASH_FEED_RX = re.compile(
    r"\b(?:which|what)\s+(?:social\s+)?(?:platforms?|services?|sources?|networks?|apps?|sites?)\s+(?:does|do|are|is)\s+[^?.!]{0,40}?\b(?:feed|tab|board|lens|section|ranker|rankers|trends|dashboard)\b"
    r"|\b(?:social\s+(?:feed|tab|board|section))\b[^?.!]{0,40}?\b(?:cover|covers|include|includes|track|tracks|pull|pulls|read|reads|platforms?)\b"
    r"|\bplatforms?\s+(?:the|in|on)\s+(?:the\s+)?social\s+(?:feed|tab|board|section)\b", re.I)
_DASH_ANY_RX = re.compile(
    r"\b(?:dashboard|select\s+product|rankers?|trends|share\s+of\s+time|social\s+feed|social\s+tab|tabs?|views?|boards?|product\s+menu|dropdown)\b", re.I)
# the words the data lanes own: a question about the titles on a board, an audience, a figure
_DASH_DATA_RX = re.compile(
    r"\b(?:top\s+\d+|number\s+one|#\s*1|most\s+(?:watched|streamed|played|popular)|trending\s+(?:now|today|right\s+now)|this\s+week|yesterday|"
    r"how\s+(?:many|much)\s+(?:people|viewers|users|subscribers|streams|views)|audience\s+(?:of|for)|viewers\s+of|profile\s+(?:of|for)|"
    r"index(?:es)?\b|penetration|reach\s+of|who\s+(?:watches|streams|buys|listens))\b", re.I)
_DASH_VIEW_NAME_RX = re.compile(
    r"\b(?:has|have|got|offer|offers|include|includes|carry|carries|show|shows|cover|covers|there\s+(?:a|an|any|still\s+a|still\s+an)|still\s+has|still\s+have|"
    r"or|and)\s+"
    r"(?:a|an|any|the|its|your|our)?\s*(?P<name>[A-Za-z][\w+&'/.-]*(?:\s+[A-Za-z0-9][\w+&'/.-]*){0,4}?)\s+(?P<kind>view|tab|page|board|ranker|feed|product|screen|section|lens)s?\b", re.I)
_DASH_NAME_STOP = frozenset(('which', 'what', 'who', 'how', 'do', 'does', 'is', 'are', 'the', 'a', 'an', 'in', 'on', 'for', 'to',
                             'platforms', 'platform', 'services', 'sources', 'every', 'each', 'all', 'say', 'whether', 'if', 'me', 'you', 'we'))
_DASH_CUES = (
    ('dashboard itself', re.compile(r"\bdashboard\s+itself\b", re.I)),
    ('list every', re.compile(r"\b(?:list|name)\s+(?:every|all|each)\b", re.I)),
    ('tabs', re.compile(r"\btabs?\b", re.I)),
    ('Rankers product', re.compile(r"\brankers?\s+product\b|\brankers\b", re.I)),
    ('Social feed', re.compile(r"\bsocial\s+(?:feed|tab)\b", re.I)),
    ('view', re.compile(r"\bviews?\b", re.I)),
    ('Trends', re.compile(r"\btrends\b", re.I)),
    ('what each ranks', re.compile(r"\b(?:each|every)\s+(?:one|ranker|tab|board)\b|\brank(?:s|ed)?\b", re.I)),
)


def dashboard_cues(text):
    """The words that read the ask as a question about the dashboard itself
    (the Understanding's evidence)."""
    t = _clean(text)
    return [name for name, rx in _DASH_CUES if rx.search(t)]


def dashboard_ask(text):
    """The words ask what the dashboard holds: which Rankers or tabs exist,
    what each ranks, whether a named view exists, which platforms a feed
    covers. Never a where-is (wayfinding), never the data on a board, never
    an order."""
    t = _clean(text)
    if not t or len(t) > _DASH_MAX_LEN:
        return False
    if _DASH_SELF_RX.search(t) and _DASH_ANY_RX.search(t):
        return True
    if not _DASH_ANY_RX.search(t):
        return False
    if _DASH_DATA_RX.search(t):
        return False
    return bool(_DASH_LIST_RX.search(t) or _DASH_HAVE_RX.search(t) or _DASH_RANKS_RX.search(t) or _DASH_FEED_RX.search(t))


def _dash_view_names(t):
    """The views the words ask after by name ('a Share of Time view', 'is
    there a Books tab'), each (name, kind), in order, deduped."""
    out, seen = [], set()
    for m in _DASH_VIEW_NAME_RX.finditer(t):
        name = ' '.join(m.group('name').split()).strip(" '\"")
        name = re.sub(r"^(?:a|an|the|any|its|your|our)\s+", '', name, flags=re.I)
        name = re.sub(r"^(?:still|also|even)\s+", '', name, flags=re.I)
        low = name.lower()
        if not low or low in ('dashboard', 'crosswalk', 'this', 'that', 'it', 'the', 'every', 'each', 'all', 'any', 'same', 'such', 'own',
                              'separate', 'dedicated', 'specific', 'single', 'second', 'another', 'other', 'new'):
            continue
        if set(low.split()) & _DASH_NAME_STOP:
            continue        # "which social platforms the Social feed": a clause, not a view's name
        if low in seen:
            continue
        seen.add(low)
        out.append((name, m.group('kind').lower()))
    return out


def dashboard_answer(text, *, host=None, user=None):
    """The facts the words ask for, from prometheus.dashboard_map: the
    Rankers tabs and what each ranks (with the services inside when they
    can be read), the Trends tabs, whether a named view exists, which
    platforms the social feed covers, the products on the dropdown. One
    plain list; a view the dashboard does not have is a plain no."""
    from . import dashboard_map as _dm
    t = _clean(text)
    low = t.lower()
    names = _dash_view_names(t)
    named_lower = {n.lower() for n, _k in names}
    # "is there a Books ranker" / "does the dashboard have a Gaming board": the named view is the question; the
    # whole list is read out only when the words ask for it
    asks_list = bool(_DASH_LIST_RX.search(t) or _DASH_RANKS_RX.search(t) or _DASH_SELF_RX.search(t))
    wants_rankers = bool(re.search(r"\brankers?\b|\bboards?\b|\bleaderboards?\b|\brank(?:s|ed|ing)?\b", low))
    wants_trends = bool(re.search(r"\btrends\b", low)) and bool(re.search(r"\b(?:tabs?|sections?|lenses|boards?)\b", low)) and not wants_rankers
    wants_social = bool(re.search(r"\bsocial\b", low))
    wants_products = bool(re.search(r"\b(?:what|which|list|name|how\s+many|every|all)\b[^?.!]{0,30}?\b(?:products|modules|options|iqs)\b"
                                    r"|\b(?:select\s+product|product\s+(?:menu|dropdown|list)|the\s+dropdown|drop\s*down\s+menu)\b", low))
    parts = []
    if wants_rankers and (asks_list or not names):
        tabs = _dm.rankers_tabs()
        lead = (f"Rankers opens from the {_dm.DROPDOWN} dropdown and has {len(tabs)} tabs across the top. Pick a tab, then a service.")
        lines = []
        for tb in tabs:
            services = [s for s in _dm.section_services(tb['key'], host=host) if s.lower() != tb['label'].lower()]
            svc = f" The services are {_dm.join_names(services)}." if services else ''
            lines.append(f"- {tb['label']}: {tb['ranks']}.{svc}")
        parts.append(lead + "\n\n" + "\n".join(lines))
        # a Trends tab the words took for a Ranker (Books, Comics, Films ...)
        elsewhere = [tb['label'] for tb in _dm.trends_tabs() if re.search(r"\b" + re.escape(tb['label'].lower()) + r"\b", low)]
        elsewhere = [l for l in elsewhere if l.lower() not in named_lower]
        if elsewhere:
            verb = 'is a tab' if len(elsewhere) == 1 else 'are tabs'
            parts.append(f"{_dm.join_names(elsewhere)} {verb} in Trends, not in Rankers. The Trends tabs are "
                         f"{_dm.join_names([x['label'] for x in _dm.trends_tabs()])}.")
    if wants_trends:
        tabs = _dm.trends_tabs()
        lines = [f"- {tb['label']}: {tb['ranks']}." for tb in tabs]
        parts.append(f"Trends has {len(tabs)} tabs across the top.\n\n" + "\n".join(lines))
    for name, kind in names:
        if name.lower() in ('social', 'social media') and wants_social:
            continue            # the social paragraph below answers it
        hit = _dm.view_lookup(name)
        if hit is None:
            parts.append(f"{name}: no. The dashboard does not have a {name} {kind} today.")
        elif hit['kind'] == 'product':
            parts.append(f"{hit['label']}: yes. It is an option on the {_dm.DROPDOWN} dropdown.")
        elif hit.get('ranks'):
            parts.append(f"{hit['label']}: yes. It is a tab in {hit['product']}: {hit['ranks']}.")
        else:
            parts.append(f"{hit['label']}: yes. It is in {hit['product']}.")
    if wants_rankers and names and not asks_list:
        # the named view was the question; the boards in one line so the reader has the list
        parts.append(f"The Rankers tabs are {_dm.join_names([x['label'] for x in _dm.rankers_tabs()])}.")
    if wants_social:
        plats = _dm.social_platforms()
        if _dm.social_tab_live():
            parts.append(f"The Social feed in Trends covers {_dm.join_names(plats)}." if plats else "The Social feed in Trends is live.")
        else:
            tail = (f" {_dm.join_names(plats)} still feed the Trending people and Movers tabs behind the scenes." if plats else '')
            parts.append("The Social feed: Trends has no Social tab today." + tail)
    if wants_products or not parts:
        prods = _dm.products_for(user, host)
        if prods:
            parts.append(f"The {_dm.DROPDOWN} dropdown holds {_dm.join_names(prods)}.")
        else:
            parts.append(f"Your products are on the {_dm.DROPDOWN} dropdown at the top right of the dashboard.")
    return "\n\n".join(parts)


def enabled():
    return str(os.environ.get('PM_PRODUCT_FACTS', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(str(text or '').split())


def _names_a_subject(t):
    """A capitalized word that is not the product's own vocabulary, or a
    'X x Y' pairing: the ask names its subject and is an order."""
    if ' x ' in f" {t.lower()} ":
        return True
    for i, w in enumerate(re.findall(r"[A-Za-z][\w'&.+-]*", t)):
        if i == 0:
            continue
        if w[:1].isupper() and w.lower() not in _NAME_OK:
            return True
    return False


def _twin_pricing_ask(t):
    try:
        from migration.digital_twin_pricing import is_pricing_ask
        return bool(is_pricing_ask(t))
    except Exception:
        return False


def family_of(text):
    """'overview' | 'bulk_list' | 'valuation' | 'twin_pricing' | 'positioning' | 'data_history' | None."""
    if not enabled():
        return None
    t = _clean(text)
    if not t:
        return None
    if dashboard_ask(t):
        return 'dashboard'       # its own length cap: a dashboard question can run long (Liz 2026-11-23)
    if len(t) > _MAX_LEN:
        return None
    if _OVERVIEW_RX.search(t):
        return 'overview'
    if positioning_ask(t):
        return 'positioning'
    if data_history_ask(t):
        return 'data_history'
    if _STATUS_RX.search(t):
        return None
    if _twin_pricing_ask(t):
        return 'twin_pricing'
    if _BULK_RX.search(t) and not _TWIN_RX.search(t):
        return 'bulk_list'
    if _VALUATION_RX.search(t) and not _names_a_subject(t):
        return 'valuation'
    return None


def looks_like(text):
    return family_of(text) is not None


def price_fn_for(username=None):
    """The live rate for a seat (prometheus.legacy.watch._pm_usd), or
    None so the published rate card renders (tests, a process without
    the web app)."""
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return None
    try:
        from prometheus.legacy.watch import _pm_usd
        return lambda key, dflt: _pm_usd(key, dflt, username)
    except Exception:
        return None


def thread_has_names(uname, tid, host):
    """A names sheet or text list on the thread (the bulk door's own
    check); False when it cannot be read."""
    try:
        from . import bulk_pull as _bp
        return bool(_bp.thread_has_names(uname, tid, host))
    except Exception:
        return False


def _money(price_fn, key, dflt):
    return _rc.money(_rc._price(price_fn, key, dflt))


def facts(price_fn=None):
    """The prices the answers quote, from the live card."""
    profile = _rc._price(price_fn, 'profile_iq_build', _rc.default('profile_iq_build'))
    bp_control = _rc._price(price_fn, 'brand_partnership_iq', _rc.default('brand_partnership_iq'))
    try:
        from . import bulk_pull as _bp
        list_max = int(_bp.MAX_SUBJECTS)
        build_minutes = int(_bp.BUILD_MINUTES)
    except Exception:
        list_max, build_minutes = 2000, 65
    # where finished things land, from the one map of the dashboard
    # (prometheus.dashboard_map, Jenna 2026-11-10); the wayfinding lane
    # gives directions from the same names
    try:
        from . import dashboard_map as _dm
        where = {k: _dm.where_lands(k) for k in ('journey', 'profile', 'twin', 'valuation', 'flywheel', 'campaign')}
        where['dropdown'] = _dm.DROPDOWN_WHERE
    except Exception:
        where = {}
    return {'profile': _rc.money(profile), 'bp_base': _rc.money(bp_control / 2.0), 'bp_control': _rc.money(bp_control),
            'list_max': list_max, 'build_minutes': build_minutes, 'where': where}


INCLUDED_LINE = "Included with your plan."
INCLUDED_PHRASE = "included with your plan"


def answer(text, *, price_fn=None, has_names_list=None, included=False, host=None, user=None):
    """{'reply', 'followups', 'family'} or None. ``has_names_list`` is a
    callable (or bool) saying whether a names sheet sits on the thread:
    a bulk ORDER with the list attached is never answered here.
    ``included`` is an unlimited seat (or Crosswalk staff): every price
    in the answer reads "included with your plan" (Jenna 2026-11-08).
    ``host`` / ``user`` let the dashboard family read the live store and
    the seat's own dropdown; both optional."""
    fam = family_of(text)
    if not fam:
        return None
    if fam == 'dashboard':
        reply = dashboard_answer(text, host=host, user=user)
        return {'reply': reply, 'followups': ['Open Rankers', 'Open Trends'] if 'Rankers' in reply or 'Trends' in reply else [], 'family': fam}
    if fam == 'data_history':
        # no price in this answer; the line reads the same for every seat
        return {'reply': data_history_answer(text), 'followups': [], 'family': fam}
    f = facts(price_fn)
    if included:
        f = dict(f, profile=INCLUDED_PHRASE, bp_base=INCLUDED_PHRASE, bp_control=INCLUDED_PHRASE)
    if fam == 'bulk_list':
        try:
            from . import bulk_pull as _bp
            is_order = _bp.is_bulk_ask(_clean(text), True)
        except Exception:
            is_order = False
        if is_order:
            try:
                has = has_names_list() if callable(has_names_list) else bool(has_names_list)
            except Exception:
                has = False
            if has:
                return None       # the door reads the list back and runs it
        readback = (f"how many names are already in the library (reused as is), how many are new builds ({INCLUDED_PHRASE}), "
                    f"the window and the time at the current pace" if included else
                    f"how many names are already in the library (reused at no charge), how many are new builds at "
                    f"{f['profile']} each, the window, the price and the time at the current pace")
        reply = (f"Yes. Attach a CSV, TXT or XLSX with one name per row (a column named subject, name, talent, brand or title, "
                 f"else the first text column; up to {f['list_max']:,} names) and say 'pull profiles for everyone on this list'. "
                 f"I read the list back first: {readback} (about {f['build_minutes']} minutes a build, "
                 f"several at a time); say 'Queue the list' and the builds run as capacity allows, each profile landing in Select Profile as it "
                 f"finishes, with a summary email as the list progresses. Ask 'how is my list going' any time.")
        return {'reply': reply, 'followups': ['How is my list going?'], 'family': fam}
    if fam == 'positioning':
        return {'reply': positioning_answer(text), 'followups': ['What can you do?', 'What does a profile cost?'], 'family': fam}
    if fam == 'twin_pricing':
        if included:
            reply = (INCLUDED_LINE + " Focus groups and pilot tests open on your company's plan with nothing charged to your account, "
                     "in any mode and at either size (1-20 or 1-40 questions). Open a room on the Focus Group IQ tab or a test on the "
                     "Pilot Testing tab, or say 'Open a focus group with the <subject> twin'.")
        else:
            from migration.digital_twin_pricing import rate_card_text
            reply = (rate_card_text(price_fn) + "\n\nThe plan is set per company; every seat on the company opens groups at that rate, and the group is charged "
                     "to the company wallet when it opens. Open a room on the Focus Group IQ tab or a test on the Pilot Testing tab, or say 'Open a focus group with the <subject> twin'.")
        return {'reply': reply, 'followups': ['Open a focus group', 'Open Pilot Testing', 'How do I use Digital Twins IQ?'], 'family': fam}
    if fam == 'valuation':
        recap = ("You approve the recap, and the valuation lands" if included else "You approve the recap with the price on it, and the valuation lands")
        tail = (f" It is {INCLUDED_PHRASE}, with or without a control group." if included
                else f": {f['bp_base']}, or {f['bp_control']} with a control group.")
        reply = (f"Say 'Value a brand partnership' and I walk you through it one question at a time: the talent or property, the campaign, "
                 f"its dates, the attribution window (the campaign dates plus 30 days, or your own), whether to include a control group, and "
                 f"the assets (paste the links or I find them). {recap} in the "
                 f"Brand Partnership tab with one dollar figure for the partnership and the audience fit, earned media, brand lift and conversion "
                 f"lenses behind it" + ('.' if included else '') + tail)
        return {'reply': reply, 'followups': ['Value a brand partnership'], 'family': fam}
    priced = ("each included with your plan" if included else "each priced before it runs")
    where = f.get('where') or {}
    lands = (f" Each lands on the dashboard: a profile {where['profile']}, a journey {where['journey']}, a twin {where['twin']}; ask me 'where is X' any time."
             if where.get('profile') and where.get('journey') and where.get('twin') else '')
    reply = (f"I answer questions about any US audience from what people actually do, in plain English: who they are, what they watch, "
             f"stream, buy, search and subscribe to, and how two audiences compare. I also build what is not on file yet: a profile of any "
             f"person, title, brand or behavior ({f['profile']}, about an hour), profiles for a whole list of names from an uploaded CSV, a "
             f"Brand Partnership valuation, a Flywheel, a Digital Journey, a Subscriber IQ read and a Digital Twin, {priced}. "
             f"Every data answer comes with a CSV you can download or have emailed." + lands)
    return {'reply': reply, 'followups': ['Run a profile', 'Value a brand partnership', 'Build a Flywheel', 'How do I pull profiles for a list?'],
            'family': fam}


def read_guard(text, *, price_fn=None, username=None):
    """A question the product facts answer never starts a read and never
    gets the working-on-it line (Armine Meseldzhyan 2026-11-23: "how
    back can we pull a window" was handed to the read lane, which
    promised a job). The read lane and the priced classifier call this
    before any work: the answer payload to return instead, or None when
    the words are not one of these questions. Never raises."""
    try:
        out = answer(text, price_fn=(price_fn if price_fn is not None else price_fn_for(username)))
    except Exception:
        return None
    if not out:
        return None
    return {'success': True, 'action': 'answer', 'reply': out['reply'], 'followups': list(out.get('followups') or []),
            'offer_deck': False, 'deck_angle': None, 'product_facts': out.get('family') or ''}
