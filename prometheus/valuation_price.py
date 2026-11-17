"""What a Brand Partnership valuation costs, said one way (Jenna 2026-11-16).

Samantha Kolton at CAA declined a $1,000 quote and asked "Can you do it
without the control group?" and then "Can we narrow the asset scope to
lower the cost". Both went to a written answer, which told her the
comparison group "does not change the price of the run" and that handing
over links or dropping platforms would make it cheaper. Neither is true.

The price of a valuation depends on one thing: the control group. With
one it is the control price, without one it is the base price, and the
base price is the lowest it goes. The number of assets, the platforms,
the window, and who finds the assets change nothing.

This module holds the words that decide whether a message is about that
price, the fixed sentences that answer it (the order itself, in
`bpiq_wizard`, fills in the seat's own two prices), and the last check on
a written answer: a sentence that talks about what a valuation costs, or
what would make it cheaper, comes out, and one line that says what the
price depends on goes in.

Pure text. Prometheus first person. No em dashes.
"""
from __future__ import annotations

import re

_CTRL = r"(?:control|comparison|matched(?:\s+comparison)?)\s+groups?"
_NAMES_RX = re.compile(r"\b(?:valuations?|brand\s+partnership|partnership\s+analys[ie]s|bpiq|" + _CTRL + r")\b", re.I)
_PRICE_WORD_RX = re.compile(
    r"\b(?:cost|costs|costing|price|prices|priced|pricing|cheap|cheaper|cheapest|expensive|pricey|discount\w*|budget|"
    r"afford\w*|fee|fees|charge|charges|spend|spending)\b", re.I)
# value words that carry 'cost' or 'spend' and are about the campaign, not about what the valuation costs
_NOT_PRICE_RX = re.compile(
    r"\bcost\s+per\b|\bcp[mca]\b|\bmedia\s+cost|\bad\s+spend|\bmedia\s+spend|\bprice\s+point|\bprice\s+sensitiv|"
    r"\bcost\s+equivalent|\bwould\s+(?:have\s+)?cost\s+to\s+buy|\bpaid\s+media\b|\bmedia\s+buy\b|"
    # what the campaign cost the brand, or what the talent was paid, is about the deal
    r"\bdid\b[^?.!]{0,60}\b(?:cost|spend|pay)\b|\bcost\s+(?:the\s+brand|them|him|her)\b|"
    r"\b(?:talent|deal|sponsorship|endorsement)\s+(?:fee|fees|cost|costs|price)\b|\bfee\s+for\s+the\s+(?:deal|talent)\b", re.I)
# a price question about something else we sell, or about a charge already taken, is not this one
_OTHER_PRODUCT_RX = re.compile(
    r"\b(?:profiles?|decks?|journeys?|credits?|subscriptions?|plans?|seats?|licen[cs]es?|focus\s+groups?|twins?|"
    r"pilot\s+tests?|talent\s+lists?|datasets?|data\s+pulls?|cuts?\s+of|api|newsletter|sizing|custom\s+(?:read|pull|ask))\b",
    re.I)
_BILLING_RX = re.compile(
    r"\b(?:charged|refund\w*|balance|wallet|invoice\w*|receipts?|billed|billing|statement|"
    r"did\s+(?:you|it|that)\s+charge|was\s+i\s+charged|get\s+charged|been\s+charged)\b", re.I)
_LOWER_RX = re.compile(
    r"\b(?:lower|cheaper|cheapest|reduce|reduced|less|discount\w*|narrow\w*|trim|cut|save|saving|smaller|"
    r"bring\s+(?:it|that|the\s+\w+)\s+down|too\s+(?:much|expensive|high|pricey)|afford\w*|budget|only\s+have|"
    r"can(?:'|\u2019)?t\s+(?:do|pay|spend))\b", re.I)
_HOW_MUCH_RX = re.compile(
    r"\bhow\s+much\s+(?:is|are|does|do|would|will|for)\s+(?:it|this|that|one|the\s+run|"
    r"(?:a|an|the|one|your)?\s*(?:brand\s+)?(?:partnership\s+)?(?:valuations?|analys[ie]s))\b", re.I)
_WORTH_RX = re.compile(r"\bworth\b|\bvalued?\b|\bgenerat\w+|\bdr[io]ve\w*|\bearn\w*|\bma[dk]e\b|\bbring\w*\s+in\b", re.I)
_WITHOUT_RX = re.compile(
    r"\b(?:without|no|drop|dropping|skip|skipping|remove|removing|exclude|excluding|minus|take\s+out|leave\s+out|"
    r"leaving\s+out|turn\s+off|not\s+(?:include|including|use|using|need))\b[^.?!]{0,24}\b" + _CTRL, re.I)
_WITH_RX = re.compile(r"\b(?:with|add|adding|include|including|keep|keeping|use|using)\b[^.?!]{0,16}\b" + _CTRL, re.I)
_ORDER_RX = re.compile(
    r"\b(?:run|rerun|redo|do|quote|price|start|try|switch|change|make\s+it|go\s+(?:with|ahead)|let(?:'|\u2019)?s|"
    r"can\s+(?:you|we|i)|could\s+(?:you|we|i)|i(?:'|\u2019)?d\s+like|i\s+would\s+like|i\s+want|please)\b", re.I)
_ASKS_ABOUT_RX = re.compile(
    r"^\W*(?:so\s+|and\s+|but\s+)?(?:what|why|how|which|is|are|does|do\s+i|would\s+i|will\s+i|explain|tell\s+me)\b", re.I)

PRICE_ASK_CHIP = 'What does a valuation cost?'
RUN_WITHOUT_CHIP = 'Run it without a control group'
RUN_WITH_CHIP = 'Run it with a control group'

NOTHING_ELSE = ("The number of assets, the platforms, the window, and whether you send the links or I find the "
                "assets do not change the price.")
WITHOUT_WHAT = ("Without a control group you do not get the lift read against a matched group of people who never "
                "saw the campaign. Every valuation still compares the people reached with the average American "
                "and with their own activity before launch.")
INCLUDED = ("A Brand Partnership valuation is included with your plan, with or without a control group, so there "
            "is no price to lower.")
# the line a written answer gains when a sentence about the price came out of it; it names no figure, so it is
# true for every seat (the order's own answer carries the seat's two prices)
POINTER = ("The price of a valuation depends on one thing: whether it includes a control group. Nothing else "
           "changes it. Ask me what a valuation costs and I will give you both prices.")
# the whole answer when the question was about the price and the written answer argued it
POINTER_FULL = ("The price of a valuation depends on one thing: whether it includes a control group. Running it "
                "without one is the only way to lower the price, and that is the lowest it goes.\n\n"
                + NOTHING_ELSE + "\n\nAsk me what a valuation costs and I will give you both prices.")


def _norm(text):
    return ' '.join(str(text or '').split())


def names_valuation(text):
    """The words name a valuation, a brand partnership or its control group."""
    return bool(_NAMES_RX.search(_norm(text)))


def control_change(text):
    """False when the words say to run without a control group, True
    when they say to run with one, None when they order neither. A
    question about the control group ('what do I lose if I drop it') is
    not an order."""
    t = _norm(text)
    if not t or _ASKS_ABOUT_RX.match(t) or not _ORDER_RX.search(t):
        return None
    if _WITHOUT_RX.search(t):
        return False
    if _WITH_RX.search(t):
        return True
    return None


def is_price_question(text):
    """The words ask what a valuation costs or how to make it cost less.
    Says nothing about whether a valuation is what they are about: the
    caller holds the thread."""
    t = _norm(text)
    if not t or _NOT_PRICE_RX.search(t) or _BILLING_RX.search(t):
        return False
    if _OTHER_PRODUCT_RX.search(t) and not names_valuation(t):
        return False
    if _PRICE_WORD_RX.search(t):
        return True
    return bool(_HOW_MUCH_RX.search(t)) and not _WORTH_RX.search(t)


def asks_lower(text):
    return bool(_LOWER_RX.search(_norm(text)))


def answer(yes, no, lower=False, included=False, control=None):
    """The fixed answer. `yes` / `no` are the seat's two prices as
    labels ('$1,000', '$500'); `control` is the control group of the
    order this thread last quoted, when there is one."""
    if included:
        return INCLUDED
    if lower and control is False:
        lead = (f"This valuation is already at its lowest price, {no}. That is without a control group. "
                f"With one it is {yes}.")
        return f"{lead}\n\n{NOTHING_ELSE}"
    if lower:
        lead = (f"The only way to lower the price is to run it without a control group. With a control group a "
                f"valuation is {yes}. Without one it is {no}, and that is the lowest it goes.")
        return f"{lead}\n\n{NOTHING_ELSE}\n\n{WITHOUT_WHAT}"
    return (f"A valuation is {yes} with a control group and {no} without one. Those are the only two prices.\n\n"
            f"{NOTHING_ELSE}")


def change_lead(yes, no, control, included=False):
    """The line above the order when its control group was changed."""
    if included:
        return 'Yes. Here it is with a control group.' if control else 'Yes. Here it is without a control group.'
    if control:
        return f"Yes. With a control group this valuation is {yes}. Without one it is {no}."
    return (f"Yes. Without a control group this valuation is {no}. With one it is {yes}. "
            f"Those are the only two prices.\n\n{WITHOUT_WHAT}")


# ---------------------------------------------------------------- guard
# a sentence in a written answer that talks about what the valuation costs
_SVC_RX = re.compile(
    r"\b(?:valuations?|" + _CTRL + r"|(?:price|cost|costs)\s+of\s+(?:the|this|a)\s+run|(?:the|this|a)\s+run\s+costs?|"
    r"the\s+quote|(?:asset|narrowed|narrower|smaller|reduced)\s+scope|standard\s+build|add-on)\b", re.I)
_GUARD_PRICE_RX = re.compile(
    r"\b(?:price|prices|priced|pricing|cost|costs|costly|cheaper|cheapest|sav(?:e|es|ed|ing)|spend|add-on|"
    r"no\s+extra|free\s+of\s+charge)\b", re.I)
_GUARD_ALONE_RX = re.compile(r"\b(?:cheap|cheaper|cheapest|expensive|pricey|discount\w*)\b", re.I)
_SENT_RX = re.compile(r"(?<=[.!?])\s+")
_SKIP_KEYS = ('bpiq_wizard', 'bpiq_collect', 'bpiq_job_id', 'bpiq_confirm_payload', 'read_job_id', 'job_id',
              'panel_offer', 'spec_draft', 'spec_drafts', 'guidance', 'valuation_price')
_SKIP_REASONS = frozenset(('bpiq_wizard', 'pricing', 'product_facts', 'definitions', 'status', 'status_fast',
                           'capability', 'help_writing', 'intake_cancel', 'number_provenance', 'deliverable_lookup',
                           'catalog', 'catalog_exists', 'wayfinding', 'email_file'))


def price_talk(sentence, asked=False):
    """A sentence that says what the valuation costs or what would
    change it. `asked` is True when the question itself was about the
    price: then a bare 'cheap' or 'expensive' is about the price too."""
    s = _norm(sentence)
    if not s or _NOT_PRICE_RX.search(s):
        return False
    if asked and _GUARD_ALONE_RX.search(s):
        return True
    return bool(_GUARD_PRICE_RX.search(s) and _SVC_RX.search(s))


def _chip_text(c):
    if isinstance(c, dict):
        return str(c.get('send') or c.get('label') or '')
    return str(c or '')


def guard_payload(raw, decision=None, question=''):
    """The last check on an answer written in the request: when the
    answer or the question is about a valuation, sentences that talk
    about its price come out, chips that offer a cheaper scope come out,
    and one line that says what the price depends on goes in. When the
    question itself was about the price, an answer that argued it is
    replaced whole: what is left of a case for a cheaper scope still
    reads as one. In place; True when anything changed. The reply always
    ships. Never raises."""
    try:
        if not isinstance(raw, dict) or raw.get('action') not in ('answer', None) or not raw.get('reply'):
            return False
        if str((decision or {}).get('reason') or '') in _SKIP_REASONS:
            return False
        if any(raw.get(k) for k in _SKIP_KEYS):
            return False
        reply = str(raw.get('reply') or '')
        if not (names_valuation(reply) or names_valuation(question)):
            return False
        removed = 0
        asked = is_price_question(question)
        out_lines = []
        for line in reply.split('\n'):
            if not line.strip():
                out_lines.append(line)
                continue
            head = re.match(r"^\s*(?:[-*\u2022]\s+|\d+[.)]\s+)?", line).group(0)
            keep = [s for s in _SENT_RX.split(line[len(head):]) if s.strip()]
            kept = [s for s in keep if not price_talk(s, asked=asked)]
            removed += len(keep) - len(kept)
            if kept:
                out_lines.append(head + ' '.join(kept))
        chips = raw.get('followups')
        chips_out = None
        if isinstance(chips, list):
            chips_out = [c for c in chips
                         if not (is_price_question(_chip_text(c)) and _chip_text(c).strip() != PRICE_ASK_CHIP)]
        if not removed and (chips_out is None or len(chips_out) == len(chips)):
            return False
        if removed and asked:
            raw['reply'] = POINTER_FULL
            chips_out = [PRICE_ASK_CHIP]
        elif removed:
            text = re.sub(r"\n{3,}", '\n\n', '\n'.join(out_lines)).strip()
            raw['reply'] = (text + '\n\n' + POINTER) if text else POINTER
            chips_out = list(chips_out if chips_out is not None else [])
            if PRICE_ASK_CHIP not in [_chip_text(c) for c in chips_out]:
                chips_out.insert(0, PRICE_ASK_CHIP)
        if chips_out is not None:
            raw['followups'] = chips_out
        raw['valuation_price_guarded'] = True
        return True
    except Exception:
        return False


__all__ = ['names_valuation', 'control_change', 'is_price_question', 'asks_lower', 'answer', 'change_lead',
           'price_talk', 'guard_payload', 'PRICE_ASK_CHIP', 'RUN_WITHOUT_CHIP', 'RUN_WITH_CHIP', 'POINTER', 'POINTER_FULL',
           'NOTHING_ELSE', 'WITHOUT_WHAT', 'INCLUDED']
