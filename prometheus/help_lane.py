"""Help, writing and engineering asks are answered in plain words, never
built or priced (2026-10-30 audit, item 3).

Liz: "I am speaking at TVOT, a Digital Media, AI, Media Ad Conference. I
need 3 killer bullets on what the audience will learn based on the
Crosswalk Dashboard and Prometheus." The build flow drafted a $300
profile named "TVOT Session", and a second pass quoted a $100 read that
then invented a 47,382-person TVOT audience. Scott: "For the electronics
vertical, build a classification that assigns every relevant URL to one
page type ..." drew a profile brief.

An ask for words (bullets, talking points, a blurb, an email, a
summary), for help ("how do I", "what should I say", "explain X to my
client") or for engineering work (a classification scheme, a taxonomy,
a regex, a query) is not an audience. This lane answers it with one
model call that knows what Crosswalk is and what the thread holds, in
the house voice, with nothing to approve and nothing charged beyond
metered usage.

Boundaries (an order wins over this lane):
  * a profile / cut / deck / journey / valuation order ("run a profile
    on X", "build a deck on X", "value the X partnership") is never a
    help ask, however it is phrased;
  * a data question ("how many people watched X", "who are the top
    QSRs for X viewers") is never a help ask;
  * "build a classification / scheme / taxonomy / regex / query" IS a
    help ask (engineering work, not an audience).

Pure detection; the answer is one call through ``answer`` with the
model client the caller hands in. Kill switch ``PM_HELP_LANE=0``.
"""
from __future__ import annotations

import os
import re

_WRITING_RX = re.compile(
    r"\b(?:bullets?|bullet points?|talking points?|key points?|one[- ]liners?|tag ?lines?|blurbs?|elevator pitch|"
    r"speaker notes?|a (?:short |quick |brief )?(?:summary|write[- ]?up|paragraph|note|intro|bio|abstract|description) (?:of|on|about|for)|"
    r"write (?:me |us )?(?:a|an|the|some|three|3|five|5|two|2)\b|draft (?:me |us )?(?:a|an|the|some)\b|"
    r"help me (?:write|draft|explain|describe|pitch|position|frame|word|phrase|present|prepare|summarize)|"
    r"what should i (?:say|tell|write|present)|how (?:do|should|would) i (?:explain|describe|pitch|present|position|frame)|"
    r"i am (?:speaking|presenting|pitching) (?:at|to|on)|i'?m (?:speaking|presenting|pitching) (?:at|to|on)|for my (?:talk|panel|presentation|keynote|pitch|meeting|deck to the board)|"
    r"summarize (?:what (?:crosswalk|prometheus|the dashboard) (?:does|can do)|crosswalk|prometheus|the dashboard|the product)|"
    r"explain (?:crosswalk|prometheus|the dashboard|the product|how (?:this|it) works) (?:to|for)\b)", re.I)

_ENGINEERING_RX = re.compile(
    r"\b(?:(?:build|make|create|write|design|propose|draft|define|set up) (?:me |us )?(?:a |an |the )?"
    r"(?:classification|classifier|taxonomy|scheme|schema|rubric|regex|regular expression|sql|query|script|mapping table|lookup table|"
    r"decision tree|rule ?set|labeling (?:scheme|guide)|url (?:classification|mapping|taxonomy))|"
    r"assigns? (?:every|each|all) (?:relevant )?(?:urls?|hostnames?|domains?|pages?) to|"
    r"(?:classify|categori[sz]e|tag|label) (?:every|each|all|the) (?:relevant )?(?:urls?|hostnames?|domains?|pages?)|"
    r"page[- ]type(?:s)?\b.*\b(?:search_engine_query|retailer_search|category_page|product_page))", re.I)

# the words that mean an audience order or a data question; they win
_ORDER_RX = re.compile(
    r"\b(?:run|pull|build|make|create|refresh|cut|split|derive) (?:me |us )?(?:a |an |the |new )?(?:profile|audience profile|cut|deck|slides|digital journey|journey|valuation|subscriber iq|twin)\b|"
    r"\bvalue (?:a |the )?(?:brand )?partnership\b|\bprofile (?:on|for|of) [A-Z]", re.I)
_DATA_Q_RX = re.compile(
    r"^\W*(?:how many|how much (?:of|did|do|does)|what (?:share|percent|percentage|is the (?:size|audience|penetration|index))|who (?:are|is|watches|buys)|which (?:brands|titles|qsr|platforms|creators)|"
    r"where (?:do|did|does)|compare\b|size\b|rank\b)", re.I)


def enabled():
    return str(os.environ.get('PM_HELP_LANE', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def is_help_ask(text):
    """True for a writing, help or engineering ask; False for an order
    or a data question."""
    if not enabled():
        return False
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1500:
        return False
    if _ORDER_RX.search(t) and not _ENGINEERING_RX.search(t):
        return False
    if _DATA_Q_RX.match(t):
        return False
    return bool(_WRITING_RX.search(t) or _ENGINEERING_RX.search(t))


_SYSTEM = (
    "You are Prometheus, the analyst inside Crosswalk's dashboard. Crosswalk sees a US consumer's entire "
    "digital life on one opted-in identity and turns it into audience reads: Profile IQ (the audience of any "
    "person, title, brand or behavior, with demographics and the brands, platforms and talent they over-index on), "
    "Subscriber IQ (a title on a streaming service: accounts that watched, new and reactivated subscribers, how long they stayed), "
    "Digital Journey IQ (the path from a post, clip or search to the watch, purchase or ticket), Attribution IQ "
    "(a campaign's creators and assets traced to the people who reached a ticketing site, product page or signup), "
    "Brand Partnership IQ (one dollar figure for a talent or title against a brand), Digital Twins IQ (a twin of an "
    "audience that reacts to scripts, concepts, trailers and prices), and Prometheus itself, which answers questions "
    "in plain English and builds decks.\n\n"
    "The person is asking for WORDS or for HELP, not for a read. Answer the ask directly and completely in plain "
    "English a high school student could follow: short sentences, no jargon, no bullet that opens the same way as "
    "the one before. Never invent a figure; use only numbers that appear in the thread context given. Never say "
    "synthetic, modeled, estimated, panel, pipeline, or name any model or data source. Never use an em dash. "
    "Never offer a price, a brief, a build or a deck unless the person asked for one. Do not restate the question."
)


def answer(text, history=None, page=None, *, reason=None, max_tokens=900):
    """{'reply', 'followups'} for a help ask, or None when the model
    gives nothing. ``reason(system, user)`` is the model call; it
    returns text."""
    if reason is None:
        try:
            from claude_client import claude_reason_json as _crj
            reason = lambda sys_, usr: _crj(system=sys_, user=usr, max_tokens=max_tokens, temperature=0.3, usage_tag='pm-help')  # noqa: E731
        except Exception:
            return None
    ctx_lines = []
    turns = [t for t in (history or []) if isinstance(t, dict)][-8:]
    for t in turns:
        role = 'Person' if str(t.get('role') or '') == 'user' else 'Prometheus'
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        if txt:
            ctx_lines.append(f"{role}: {txt[:600]}")
    if page:
        ctx_lines.append(f"(The profile open on their screen is {page}; mention it only if the ask is about it.)")
    user = ("THREAD CONTEXT:\n" + ('\n'.join(ctx_lines) if ctx_lines else '(none)') +
            "\n\nTHE ASK:\n" + ' '.join(str(text or '').split()) +
            "\n\nWrite the answer now.")
    try:
        out = str(reason(_SYSTEM, user) or '').strip()
    except Exception:
        out = ''
    if not out:
        return None
    out = out.replace('\u2014', ' - ').replace('\u2013', '-')
    return {'reply': out, 'followups': []}
