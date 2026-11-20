"""A partnership's dollar value comes from the Brand Partnership valuation,
and from nowhere else (Jenna 2026-11-11, Samantha Kolton at CAA).

She opened the valuation order, answered its first question in one
sentence, and a general read ran in the order's place: it printed a
dollar value for the partnership ($4,217,683), mailed it, and a deck
was built on it, all on the session meter. The $500 order never reached
its quote. Jenna: "why wasnt she charged? these errors cannot happen."

What a partnership is worth in dollars (its value, worth, valuation,
return, earned media value) is the priced valuation's to state. This
module is the one place that says so:

- `owns(text, history)`: 'value' when the words ask what a brand or
  talent partnership is worth in dollars (asked cold, or about the
  partnership this thread is on); 'deck' when the words ask for a deck
  of a thread whose read stated a partnership's dollar value; '' for
  anything else. Who the shared audience is, the overlap, the fit and
  which brands suit a talent are ordinary reads and are never taken.
- `seed(text, history, kind)`: the words the valuation order fills its
  first steps from (the pairing the question or the thread names, the
  dates the thread's read ran on), so the reader is never asked for a
  name they already gave.
- `lead(kind, draft)`: the one line that says why the order opened.
- `prompt_block(text)`: the binding note a read on a partnership
  carries, so the read never prices the partnership.
- `guard_read(payload, ...)` / `guard_payload(raw, ...)`: the last
  check on the way out. A reply that still states a partnership's
  dollar value loses those lines and gains one line pointing at the
  valuation. The reply always ships.

Two exceptions, both decided here: a valuation already saved for the
pairing (bought by this seat, or held in the library) may be restated,
and a seat whose plan includes the valuation walks the same order with
no price on it (the order's own "Included with your plan").

An analysis of a partnership is the valuation's too (Jenna 2026-11-16,
the same reader: "we should never do reads. you only get it if you buy
the valuation"). She asked for "a brand partnership analysis for Charli
XCX and Vinted" with the campaign's dates; no lane took it as an order,
and a general read answered from the valuation of another partnership
that was open on her screen, under the name she had typed. So:

- `owns` also returns 'analysis' when the words ask for an analysis of
  a partnership (how it performed, its results, a recap, a brand
  partnership analysis), or name a partnership on the Brand Partnership
  page that is not the one open there. The order opens filled from the
  words. A partnership whose valuation is saved is never taken: a read
  may restate what was bought.
- the guards treat a value written with no currency sign ("1,149,665
  in earned media value") and a value mix by platform the same as a
  dollar figure, and a read whose own table is a partnership's value
  mix is replaced whole by the line that points at the valuation.

Pure text and the saved-valuation lookup. User-facing copy is
Prometheus first person. No em dashes. Nothing here says how a number
was produced.
"""
from __future__ import annotations

import re

CHIP = 'Value a brand partnership'
KIND_VALUE = 'value'
KIND_DECK = 'deck'
KIND_ANALYSIS = 'analysis'

LEAD_VALUE = ("What a partnership is worth in dollars comes from a Brand Partnership valuation. "
              "I'll set that up.")
LEAD_VALUE_OPEN = "That figure is what this valuation produces."
LEAD_DECK = ("A deck on what this partnership is worth is the Brand Partnership valuation: it measures "
             "the campaign and comes with its own deck.")
LEAD_ANALYSIS = ("A brand partnership analysis comes from a Brand Partnership valuation. "
                 "I'll set that up.")
LEAD_ANALYSIS_OPEN = "That analysis is what this valuation produces."
POINTER_ANALYSIS = ("An analysis of this partnership comes from a Brand Partnership valuation. "
                    "Say 'Value a brand partnership' and I will set it up.")
BPIQ_VIEW = 'brandPartnershipIQ'
POINTER_LINE = ("What this partnership is worth in dollars comes from a Brand Partnership valuation. "
                "Say 'Value a brand partnership' and I will set it up.")

# A test (or a caller that already holds one) can supply the saved
# valuation lookup: (a, b) -> the saved valuation dict or None.
SAVED_LOOKUP = None

_PARTNER_NOUN = (r"(?:partnerships?|sponsorships?|sponsor(?:ship)?\s+deals?|collabs?|collaborations?|endorsements?(?:\s+deals?)?|"
                 r"brand\s+deals?|ambassadorships?|tie[- ]?ups?|co[- ]?brand(?:ed|ing)?(?:\s+deals?)?|deals?)")
_PARTNER_RX = re.compile(r"\b" + _PARTNER_NOUN + r"\b", re.I)
_CAMPAIGN_RX = re.compile(r"\b(?:campaign|activation|integration)s?\b", re.I)
_PAIR_RX = re.compile(r"(?<![\w&'])[\w&'.\-]{2,30}(?:\s+[\w&'.\-]{1,30}){0,3}\s+[xX\u00d7]\s+[\w&'.\-]{2,30}")
_AND_PAIR_RX = re.compile(
    r"(?:between\s+|the\s+|of\s+|for\s+)?([A-Z0-9][\w&'.\-]*(?:\s+[A-Z0-9][\w&'.\-]*){0,3})\s+(?:and|&|with)\s+"
    r"([A-Z0-9][\w&'.\-]*(?:\s+[A-Z0-9][\w&'.\-]*){0,3})(?:'s)?\s+" + _PARTNER_NOUN, re.I)
_POSSESSIVE_PAIR_RX = re.compile(
    r"([A-Z0-9][\w&.\-]*(?:\s+[A-Z0-9][\w&.\-]*){0,3})'s\s+" + _PARTNER_NOUN + r"\s+with\s+"
    r"([A-Z0-9][\w&'.\-]*(?:\s+[A-Z0-9][\w&'.\-]*){0,3})")

# 'a brand partnership analysis for Charli XCX and Vinted': the noun comes first, the names after
_NAME_TOK = r"[A-Z0-9][\w&'.\-]*"
_NAME_CS = _NAME_TOK + r"(?:\s+" + _NAME_TOK + r"){0,3}"
_AFTER_NOUN = (r"(?:\s+iq)?(?:\s+(?:analysis|analyses|valuation|read|report|study|review|recap|evaluation|assessment|"
               r"measurement|performance|results))?\s+(?:for|between|of|on|with)\s+(?:the\s+)?")
_NOUN_FIRST_PAIR_RX = re.compile(
    r"(?i:\b" + _PARTNER_NOUN + _AFTER_NOUN + r")(" + _NAME_CS + r")\s+(?:(?i:and|with|x)|&|\u00d7)\s+(" + _NAME_CS + r")")
_NOUN_FIRST_PAIR_LOWER_RX = re.compile(
    r"\b" + _PARTNER_NOUN + _AFTER_NOUN + r"([\w&'.\-]+(?:\s+[\w&'.\-]+){0,3}?)\s+(?:and|with|x|&|\u00d7)\s+"
    r"([\w&'.\-]+(?:\s+[\w&'.\-]+){0,2})\s*(?=$|[?!,;:]|\.(?:\s|$))", re.I)
_NAME_TAIL_STOP = {'here', 'the', 'from', 'campaign', 'dates', 'date', 'it', 'we', 'they', 'this', 'that', 'ran', 'running',
                   'launched', 'in', 'on', 'for', 'over', 'during', 'between', 'starting', 'started', 'please', 'thanks',
                   'and', 'with', 'i', 'can', 'could'}
_NOT_A_NAME = {'what', 'how', 'the', 'a', 'an', 'its', 'their', 'why', 'tell', 'show', 'give', 'i', 'we', 'you', 'whether',
               'if', 'then', 'also', 'see', 'let', 'send', 'include', 'it', 'this', 'that', 'them', 'they', 'who', 'which',
               'when', 'where', 'me', 'us', 'our', 'my', 'your', 'other', 'others', 'more', 'all', 'any', 'some'}

# an analysis of a partnership, asked for (2026-11-16)
_STRICT_PARTNER = (r"(?:(?:brand\s+)?partnerships?|sponsorships?|collabs?|collaborations?|endorsements?|brand\s+deals?|"
                   r"ambassadorships?|co[- ]?brand(?:ed|ing)?)")
_ANALYSIS_NOUN = (r"(?:analysis|analyses|analytics|read|report|study|assessment|evaluation|measurement|recap|review|"
                  r"breakdown|deep\s+dive|audit|post[- ]?mortem|wrap[- ]?up|results|performance|impact)")
_ANALYSIS_PHRASE_RX = re.compile(
    r"\b" + _STRICT_PARTNER + r"\s+(?:iq\s+)?" + _ANALYSIS_NOUN + r"\b|"
    r"\b" + _ANALYSIS_NOUN + r"\s+(?:of|on|for)\s+[^.?!]{0,70}?\b" + _STRICT_PARTNER + r"\b", re.I)
_ANALYSIS_VERB_RX = re.compile(
    r"\b(?:analy[sz]e|analy[sz]ing|measure|measuring|evaluate|evaluating|assess|assessing|break\s+down|recap|review|"
    r"dig\s+into|look\s+into|report\s+on)\b|"
    r"\bhow\s+(?:did|has|have|is|was|are|were)\b[^.?!]{0,90}\b(?:do|doing|done|perform\w*|go|going|gone|land\w*|"
    r"work(?:ed|ing)?|deliver\w*|fare[ds]?|faring|pan(?:ned)?\s+out)\b|"
    r"\bwhat\s+did\b[^.?!]{0,90}\b(?:do|drive|deliver|achieve|generate|accomplish)\b|"
    r"\b(?:results|performance|impact|lift)\s+(?:of|from|for)\b", re.I)
_STRICT_REFERENT_RX = re.compile(r"\b(?:this|that|the|our|my|their|his|her)\s+" + _STRICT_PARTNER + r"\b", re.I)
_AUDIENCE_ASK_RX = re.compile(
    r"\b(?:audiences?|overlap\w*|demo(?:s|graphics?)?|who\s+(?:are|is|watch\w*|follow\w*|buys?|shops?|engag\w*)|"
    r"fans?\s+of|(?:good|right|strong|natural|best|better)\s+fit|fit\s+(?:for|with|between)|suit(?:s|ed|able)?|"
    r"(?:which|what)\s+(?:brands?|talent|creators?|partners?|influencers?)|should\s+\w+(?:\s+\w+)?\s+partner|"
    r"potential\s+partners?|find\s+(?:me\s+)?(?:a\s+|some\s+)?partners?|makes?\s+sense)\b", re.I)
_WHAT_IS_ANALYSIS_RX = re.compile(
    r"^\W*(?:so\s+|ok\s+|and\s+)?(?:what(?:'s|\s+is|\s+are)|explain|describe|tell\s+me\s+about)\s+(?:a\s+|an\s+|the\s+|your\s+)?"
    r"(?:brand\s+)?partnership\s+(?:iq\s+)?" + _ANALYSIS_NOUN + r"\s*[?.!]*$|"
    r"\b(?:do|can|could)\s+you\s+(?:do|run|offer|have|make)\s+(?:brand\s+)?partnership\s+analys[ie]s\s*[?.!]*$", re.I)
_NEW_ORDER_RX = re.compile(
    r"\b(?:run|pull|start|build|create|make|order|queue|set\s+up|do)\s+(?:me\s+)?(?:a|an|another|one|the|new)\b|"
    r"\b(?:new|another)\s+(?:one|" + _STRICT_PARTNER + r"|valuation|analysis)\b", re.I)

# the dollar value of something, asked for
_WORTH_RX = re.compile(r"\bworth\b(?!\s+(?:it\b|\w+ing\b|a\s+(?:look|shot|try|test|watch)\b|the\s+(?:effort|time|hype|wait)\b))", re.I)
_VALUE_WORDS_RX = re.compile(
    r"\bvaluations?\b|\bvalued\s+at\b|\bvalue\s+of\b|\b(?:partnership|sponsorship|brand|media|earned\s+media|deal|collab|campaign|"
    r"dollar|monetary|total)\s+value\b|\bearned\s+media\b|\bemv\b|\broi\b|\broas\b|\breturn\s+on\s+(?:investment|ad\s+spend|spend|"
    r"the\s+(?:deal|sponsorship|partnership|investment|spend))\b|\bpay(?:s|ing|back)?\s+(?:back|off|out)\b|\bpayback\b|"
    r"\bprice\s+tag\b|\bput\s+an?\s+(?:dollar\s+|price\s+)?(?:figure|number|value|price)\s+on\b|\bin\s+dollars\b|"
    r"\bdollar\s+(?:figure|amount|number|worth)\b|\bhow\s+valuable\b", re.I)
_VALUE_VERB_RX = re.compile(
    r"(?:^|[.;!?]\s+|\b(?:please|can\s+you|could\s+you|would\s+you|can\s+we|i\s+want\s+(?:you\s+)?to|i\s+need\s+(?:you\s+)?to|"
    r"i'?d\s+like\s+(?:you\s+)?to|let'?s|help\s+me)\s+)(?:value|valuate|appraise|price)\s+(?:the|this|that|our|my|a|an|[A-Z0-9])", re.I)
_YIELD_RX = re.compile(
    r"\bhow\s+much\s+(?:money|revenue|value|brand\s+value|media\s+value|sales)?\s*(?:did|does|has|have|will|would|could|is|was)\b"
    r"[^.?!]{0,80}\b(?:generat\w+|driv\w+|drove|deliver\w*|creat\w+|bring\w*|brought|earn\w*|ma[dk]e\w*|return\w*|produc\w+|worth)\b", re.I)
# asks that carry value words and are about something else
_ABOUT_PRODUCT_RX = re.compile(
    r"\b(?:cost|costs|priced|pricing|charges?|charged|fees?|rate\s+card|subscription|invoice|refund|billing|billed|"
    r"how\s+(?:does|do|is|are|did)\s+(?:the\s+|a\s+|an\s+|your\s+|it\s+|you\s+|that\s+|this\s+)?(?:brand\s+partnership\s+|partnership\s+)?"
    r"(?:valuations?|iq)\s+(?:work|get|calculated|computed|measured|made|built|priced)|"
    r"methodolog\w+|how\s+(?:do|did|would|will)\s+you\s+(?:calculate|compute|value|measure|get|arrive|come\s+up)|"
    r"how\s+(?:is|was|are|were)\s+(?:it|that|this|these|those|the\s+(?:value|figure|number|total|valuation)s?)\s+"
    r"(?:calculated|computed|measured|derived|made|worked\s+out)|what(?:'s|\s+is)\s+included|what\s+does\s+(?:it|that|the\s+\w+)\s+mean|"
    r"where\s+(?:does|did|do)\s+[^.?!]{0,60}\bcome\s+from|how\s+did\s+you\s+get|why\s+(?:is|does|did|was)\b|defin(?:e|ition)|"
    r"(?:status|ready\s+yet|finished\s+yet|done\s+yet|still\s+running|how\s+long\s+(?:will|does|until))|"
    r"where\s+is\s+(?:my|the|that)\s+valuation|when\s+will\s+(?:my|the|that)\s+valuation)\b", re.I)
_WHAT_IS_PRODUCT_RX = re.compile(
    r"^\W*(?:so\s+|ok\s+|and\s+)?(?:what(?:'s|\s+is|\s+are)|explain|describe|tell\s+me\s+about)\s+(?:a\s+|an\s+|the\s+|your\s+)?"
    r"(?:brand\s+partnership(?:\s+(?:valuations?|iq))?|bpiq|partnership\s+valuations?|control\s+groups?|valuations?)\s*[?.!]*$", re.I)
_HOW_MUCH_PRODUCT_RX = re.compile(
    r"\bhow\s+much\s+(?:is|are|does|do|for|would)\s+(?:a|an|the|one|your)?\s*(?:brand\s+)?(?:partnership\s+)?"
    r"(?:valuations?|analys[ie]s)\b", re.I)
_REFERENT_RX = re.compile(
    r"\b(?:it|this|that|they|these|those|the\s+(?:partnership|deal|collab(?:oration)?|sponsorship|campaign|pairing|two))\b", re.I)

_DECK_RX = re.compile(r"\b(?:deck|slides?|slide\s+deck|presentation|power\s?point|pptx?|keynote)\b", re.I)
_DECK_DEICTIC_RX = re.compile(r"\b(?:this|that|it|these|those|the\s+(?:read|answer|above|analysis|numbers?|figures?|findings?|results?))\b", re.I)
_DECK_STOP = {
    'can', 'could', 'would', 'will', 'you', 'we', 'i', 'please', 'turn', 'make', 'build', 'create', 'put', 'convert', 'export',
    'give', 'get', 'want', 'need', 'like', 'into', 'in', 'to', 'a', 'an', 'the', 'me', 'us', 'as', 'of', 'on', 'for', 'from', 'with',
    'and', 'this', 'that', 'it', 'these', 'those', 'deck', 'decks', 'slide', 'slides', 'presentation', 'powerpoint', 'power',
    'point', 'ppt', 'pptx', 'keynote', 'insights', 'insight', 'read', 'answer', 'above', 'analysis', 'numbers', 'number',
    'figures', 'findings', 'results', 'now', 'too', 'also', 'together', 'one', 'run', 'pull', 'do', 'something', 'about',
    'quick', 'short', 'client', 'ready', 'up', 'out', 'my', 'our', 'partnership', 'value', 'valuation', 'x', 'so', 'ok', 'okay',
    'hey', 'hi', 'thanks', 'thank', 'great', 'nice', 'now', 'then', 'just', 'new', 'is', 'be', 'there', 'way', 'any', 'chance',
    # asking for a deck again, or as a file, names no other subject (2026-11-11)
    'send', 'resend', 'again', 'same', 'copy', 'version', 'file', 'link', 'download', 'share', 'pdf', 'editable', 'have', 'may',
    'see', 'show', 'here', 'where', 'full', 'whole', 'final', 'finished', 'brand',
}

# a sentence that states a partnership's dollar value
_MONEY_RX = re.compile(r"(?:[$\u00a3\u20ac]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|mm|b|bn|million|billion|thousand)?\b|"
                       r"\b\d[\d,]*(?:\.\d+)?\s?(?:million|billion|thousand|k|m|mm|bn)?\s+(?:us\s+)?dollars\b|\busd\s?\d[\d,]*)", re.I)
_STATED_VALUE_RX = re.compile(
    r"\bworth\b|\bvalued\s+at\b|\bvaluation\b|\b(?:of|in)\s+(?:total\s+|brand\s+|earned\s+media\s+|media\s+|partnership\s+)?value\b|"
    r"\b(?:partnership|sponsorship|brand|media|earned\s+media|deal|collab|total)\s+value\b|\bvalue\s+(?:of|to\s+date|so\s+far|is|was|"
    r"comes\s+to|came\s+to|reads\s+as|stands\s+at)\b|\bcarr(?:y|ies|ied)\b[^.]{0,40}\bvalue\b|\broi\b|\breturn\s+on\b|\bearned\s+media\b|"
    r"\bemv\b", re.I)
# a value written with no currency sign: "1,149,665 in earned media value" (2026-11-16)
_BARE_AMOUNT_RX = re.compile(r"(?<![\w.,$])(?:\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d+)?(?![\w%])(?!\s*(?:%|percent|people|viewers|"
                             r"users|accounts|fans|followers|subscribers|visits|views|sessions|clicks|impressions|posts|"
                             r"mentions|searches|orders|buyers|shoppers|households|individuals)\b)")
_STRONG_VALUE_RX = re.compile(
    r"\bearned\s+media(?:\s+value)?\b|\bemv\b|\bmedia\s+value\b|\b(?:total\s+)?brand\s+value\b|\b(?:partnership|"
    r"sponsorship|deal|collab(?:oration)?|campaign)\s+value\b|\bbrand\s+(?:engagement|lift)\s+value\b|"
    r"\bconversion\s+value\b|\b(?:in|of)\s+(?:total\s+)?value\b", re.I)
_VALUE_SHARE_RX = re.compile(
    r"\bshare\s+of\s+(?:the\s+)?(?:total\s+)?(?:campaign\s+|partnership\s+)?(?:earned\s+media\s+value|earned\s+media|"
    r"media\s+value|brand\s+value|partnership\s+value|value|emv)\b|\bvalue\s+(?:component\s+)?mix\b|"
    r"\bmix\b[^\n.]{0,60}\b(?:earned\s+media|media\s+value|brand\s+value|emv)\b", re.I)
_PRICE_TALK_RX = re.compile(
    r"\bthis\s+is\s+[$\u00a3\u20ac]|\baccept\b|\bcosts?\b|\bpriced?\b|\bpricing\b|\bcharged?\b|\bfee\b|\brate\s+card\b|\bper\s+(?:valuation|"
    r"pull|read)\b|\bincluded\s+with\s+your\s+plan\b|\bwallet\b|\bbalance\b|\badd\s+funds\b|\brefund|\bcontrol\s+group\b|"
    r"\bvaluations?\s+(?:is|are|runs?|starts?\s+at)\s+[$\u00a3\u20ac]", re.I)
_VALUE_LABEL_RX = re.compile(
    r"(?:partnership|sponsorship|collab(?:oration)?|deal|endorsement)\b[^,;:]{0,40}\b(?:value|worth|valuation|roi|return)\b|"
    r"\b(?:value|worth|valuation|roi|return)\b[^,;:]{0,40}\b(?:partnership|sponsorship|collab(?:oration)?|deal|endorsement)\b|"
    r"\bearned\s+media(?:\s+value)?\b|\bmedia\s+value\b|\btotal\s+brand\s+value\b|\bbrand\s+value\b|\bemv\b", re.I)
_USD_UNITS = ('usd', '$', 'dollar', 'dollars', 'money', 'currency')


def _norm(text):
    return ' '.join(str(text or '').split())


def _fold(text):
    return re.sub(r"[^a-z0-9]+", '', str(text or '').lower())


# ------------------------------------------------------------------ asks
def pairing(text):
    """(a, b) when the words name two parties to a partnership: 'Gap x
    JYT', 'the Nike and Gunna sponsorship', "Nike's deal with Gunna",
    'JYT is the talent and Gap is the brand'. None otherwise."""
    t = _norm(text)
    if not t:
        return None
    try:
        from migration import bpiq_anchors as _ba
        pr = _ba.pairing_in(t)
        if pr and _fold(pr[0]) and _fold(pr[1]):
            return _trim_pair(pr)
    except Exception:
        pass
    for rx in (_POSSESSIVE_PAIR_RX, _AND_PAIR_RX):
        m = rx.search(t)
        if m and _fold(m.group(1)) and _fold(m.group(2)):
            return _trim_pair((m.group(1), m.group(2)))
    return _noun_first_pair(t)


def _drop_tail(name):
    words = str(name or '').split()
    while len(words) > 1 and words[-1].lower().strip(".,'") in _NAME_TAIL_STOP:
        words.pop()
    return ' '.join(words)


def _noun_first_pair(t):
    """'a brand partnership analysis for Charli XCX and Vinted', 'the
    sponsorship between nike and gunna.': the names after the noun."""
    for rx in (_NOUN_FIRST_PAIR_RX, _NOUN_FIRST_PAIR_LOWER_RX):
        m = rx.search(t)
        if not m:
            continue
        a, b = _drop_tail(m.group(1)).strip(" .,:;?!'\""), _drop_tail(m.group(2)).strip(" .,:;?!'\"")
        if not _fold(a) or not _fold(b) or _fold(a) == _fold(b):
            continue
        if a.split()[0].lower() in _NOT_A_NAME or b.split()[0].lower() in _NOT_A_NAME:
            continue
        return a, b
    return None


_PAIR_LEAD_RX = re.compile(
    r"^(?:(?:what|how|is|was|are|were|the|a|an|of|for|on|about|between|value|valuation|worth|much|did|does|do|can|could|you|"
    r"please|me|tell|show|give|whats|what's|roi|return|earned|media|dollar|deck)\s+)+", re.I)
_PAIR_TAIL_RX = re.compile(r"\s+(?:" + _PARTNER_NOUN + r"|campaign|worth|value|valuation|roi)\b.*$", re.I)


# 'run a brand partnership analysis on Gap' -> 'Gap': what follows the last "<analysis word> on / for / of"
_PAIR_ORDER_LEAD_RX = re.compile(
    r"^.*\b(?:" + _ANALYSIS_NOUN + r"|valuation|" + _PARTNER_NOUN + r")\s+(?:on|for|of|between|about)\s+(?:the\s+)?(?=\S)|"
    r"^(?:(?:hey|hi|ok|okay|so|please|can|could|would|will|you|we|i|want|need|to|like|'d|let's|lets|help|me|us)\s+)*"
    r"(?:analy[sz]e|measure|evaluate|assess|recap|review|break\s+down|dig\s+into|look\s+into|report\s+on)\s+(?:the\s+)?(?=\S)", re.I)


def _trim_pair(pr):
    a0 = _PAIR_ORDER_LEAD_RX.sub('', _norm(pr[0]), count=1)
    pr = ((a0 if _fold(a0) else pr[0]), pr[1])
    a = _PAIR_LEAD_RX.sub('', _norm(pr[0])).strip(" .,:;?!'\"")
    b = _PAIR_TAIL_RX.sub('', _norm(pr[1])).strip(" .,:;?!'\"")
    return (a or _norm(pr[0])), (b or _norm(pr[1]))


def names_partnership(text):
    """The words are about a partnership: a pairing, or a partnership noun."""
    t = _norm(text)
    return bool(t) and bool(_PARTNER_RX.search(t) or _PAIR_RX.search(t) or pairing(t))


def asks_dollar_value(text):
    """The words ask what something is worth in dollars (its value, its
    worth, a valuation, the return, the earned media value). Says
    nothing about what the something is."""
    t = _norm(text)
    if not t:
        return False
    if _ABOUT_PRODUCT_RX.search(t) or _HOW_MUCH_PRODUCT_RX.search(t) or _WHAT_IS_PRODUCT_RX.search(t):
        return False
    return bool(_WORTH_RX.search(t) or _VALUE_WORDS_RX.search(t) or _VALUE_VERB_RX.search(t) or _YIELD_RX.search(t))


def value_ask(text):
    """The words ask the dollar value of a partnership they name."""
    t = _norm(text)
    if not t or not asks_dollar_value(t):
        return False
    if _PARTNER_RX.search(t) or _PAIR_RX.search(t) or pairing(t):
        return True
    # a campaign's dollar value: said in so many words, or a short ask
    # ('what is this campaign worth'); a long brief that only carries
    # the words somewhere is not one
    if not _CAMPAIGN_RX.search(t):
        return False
    if re.search(r"\bin\s+dollars\b|\bdollar\s+(?:value|figure|amount|worth)\b|\bearned\s+media\b|\bemv\b|\broi\b|\bbrand\s+value\b", t, re.I):
        return True
    return len(t.split()) <= 16


def analysis_ask(text):
    """The words ask for an analysis of a partnership: 'a brand
    partnership analysis for Charli XCX and Vinted', 'how did the Gap x
    JYT collab perform', 'recap the Nike sponsorship'. Who the shared
    audience is, the overlap, the fit and which brands suit a talent
    are not one, and neither is a question about the product itself."""
    t = _norm(text)
    if not t:
        return False
    if _ABOUT_PRODUCT_RX.search(t) or _HOW_MUCH_PRODUCT_RX.search(t) or _WHAT_IS_PRODUCT_RX.search(t) \
            or _WHAT_IS_ANALYSIS_RX.search(t):
        return False
    if _AUDIENCE_ASK_RX.search(t):
        return False
    if _ANALYSIS_PHRASE_RX.search(t):
        return True
    if not _ANALYSIS_VERB_RX.search(t):
        return False
    return bool(_PAIR_RX.search(t) or _STRICT_REFERENT_RX.search(t) or (_STRICT_PARTNER_RX.search(t) and pairing(t)))


_STRICT_PARTNER_RX = re.compile(r"\b" + _STRICT_PARTNER + r"\b", re.I)


def page_entry(page):
    """What the open page says about itself when it is the Brand
    Partnership page with a valuation on it: {'name': the valuation's
    own name or ''}. None for any other page (or none)."""
    vc = page if isinstance(page, dict) else None
    if isinstance(vc, dict) and isinstance(vc.get('view_context'), dict):
        vc = vc.get('view_context')
    if not isinstance(vc, dict) or str(vc.get('view_id') or '') != BPIQ_VIEW:
        return None
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    if not data:
        return None
    name = ''
    for k in ('partnership', 'project_name', 'campaign_name', 'subject'):
        if str(data.get(k) or '').strip():
            name = _norm(data.get(k))
            break
    return {'name': name}


def _same_pair(pr, name):
    """The pairing `pr` is the one `name` ('A x B') is on, in either order."""
    if not pr or not name:
        return False
    m = re.match(r"^(.{2,80}?)\s+[xX\u00d7]\s+(.{2,80})$", _norm(name))      # a valuation's own name is 'A x B'
    other = (m.group(1), m.group(2)) if m else pairing(name)
    if not other:
        return False
    a, b = {_fold(x) for x in pr}, {_fold(x) for x in other}
    if a == b:
        return True
    # one side written shorter ('Matchbox' for 'Matchbox the Film on Apple TV')
    def _near(x, ys):
        return any(x == y or (len(x) >= 4 and len(y) >= 4 and (x in y or y in x)) for y in ys)
    return all(_near(x, b) for x in a)


# ---------------------------------------------------------------- thread
def _turn_text(turn):
    return str((turn or {}).get('text') or (turn or {}).get('content') or '')


def _meta(turn):
    m = (turn or {}).get('meta')
    return m if isinstance(m, dict) else {}


def _is_agent(turn):
    return isinstance(turn, dict) and str(turn.get('role') or '') in ('agent', 'assistant')


# a promise or a notice, never the read itself
_ACK_RX = re.compile(
    r"^\s*(?:\d\)\s[^\n]*\n)?\s*(?:on it\b|already on it\b|working on it\b|deck ready\b|building the\b|it'?s running\b|"
    r"included with your plan\. on it\b)|the read will land right here", re.I)


def _is_read_turn(turn):
    """An agent turn that delivered a read (figures, a read job, or a long answer)."""
    if not _is_agent(turn):
        return False
    meta = _meta(turn)
    txt = _turn_text(turn)
    if meta.get('bpiq_wizard') or meta.get('deck_job_id') or _ACK_RX.search(txt[:400]):
        return False
    if meta.get('figures') or meta.get('kind') == 'read':
        return True
    return len(txt) >= 280


def stated_value_sentences(text):
    """The sentences (or lines) of `text` that state a partnership's
    dollar value: a money amount, a value word, and the partnership in
    the same sentence. Price talk (a quote, a charge) is never one."""
    out = []
    body = str(text or '')
    if not body or not (_MONEY_RX.search(body) or (_BARE_AMOUNT_RX.search(body) and _STRONG_VALUE_RX.search(body))):
        return out
    about = bool(_PARTNER_RX.search(body) or _PAIR_RX.search(body))
    for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$\"'(\[-])|\n+", body):
        s = piece.strip()
        if not s or _PRICE_TALK_RX.search(s):
            continue
        money = bool(_MONEY_RX.search(s))
        # a value written with no currency sign: the amount and the value's own name in one sentence
        bare = (not money) and bool(_BARE_AMOUNT_RX.search(s)) and bool(_STRONG_VALUE_RX.search(s))
        if not money and not bare:
            continue
        if not _STATED_VALUE_RX.search(s) and not bare:
            continue
        if _PARTNER_RX.search(s) or _PAIR_RX.search(s) or (about and _VALUE_LABEL_RX.search(s)) or (bare and about):
            out.append(s)
    return out


def value_share_lines(text):
    """The lines of `text` that state a partnership's value as a mix or
    a share ('share of total campaign earned media value')."""
    return [ln.strip() for ln in str(text or '').split('\n') if ln.strip() and _VALUE_SHARE_RX.search(ln)]


def turn_states_value(turn):
    """An agent read turn that stated a partnership's dollar value."""
    if not _is_read_turn(turn):
        return False
    txt = _turn_text(turn)
    if stated_value_sentences(txt):
        return True
    # the metric line a read prints under its lead: "Partnership value, Sep 22 to Nov 11 2026: 4,217,683"
    return bool(re.search(r"\b(?:partnership|sponsorship|deal|collab(?:oration)?)\s+value\b[^:\n]{0,60}:\s*[$\u00a3\u20ac]?\s?\d[\d,]{4,}", txt, re.I))


def last_read_turn(history):
    for turn in reversed(list(history or [])):
        if _is_read_turn(turn):
            return turn
    return None


def thread_value_read(history):
    """The thread's most recent read, when it stated a partnership's
    dollar value; None when the last read is about something else."""
    turn = last_read_turn(history)
    return turn if turn is not None and turn_states_value(turn) else None


def _thread_partnership(history, look=8):
    """True when the thread's recent turns are on a partnership: its last
    read names one, or a recent message of the reader's does."""
    turn = last_read_turn(history)
    if turn is not None:
        subj = str(_meta(turn).get('subject') or '')
        head = _turn_text(turn)[:320]
        if _PAIR_RX.search(subj) or _PARTNER_RX.search(head) or _PAIR_RX.search(head):
            return True
    seen = 0
    for t in reversed(list(history or [])):
        if isinstance(t, dict) and str(t.get('role') or '') == 'user':
            seen += 1
            if names_partnership(_turn_text(t)):
                return True
            if seen >= look:
                break
    return False


def thread_pair(history):
    """(talent, brand, oriented) for the partnership the thread is on.
    `oriented` is True when the reader said which is which ('JYT is the
    talent and GAP is the brand'); otherwise the pair is in the order
    the thread wrote it. None when the thread names no pairing."""
    hist = list(history or [])
    try:
        from prometheus import bpiq_slots as _sl
    except Exception:
        _sl = None
    if _sl is not None:
        for t in reversed(hist):
            if isinstance(t, dict) and str(t.get('role') or '') == 'user':
                try:
                    got = _sl.parse(_turn_text(t))
                except Exception:
                    got = {}
                if got.get('talent') and got.get('brand'):
                    return str(got['talent']), str(got['brand']), True
    turn = last_read_turn(hist)
    for src in ((str(_meta(turn).get('subject') or '') if turn else ''), (_turn_text(turn)[:320] if turn else '')):
        pr = pairing(src) if src else None
        if pr:
            return pr[0], pr[1], False
    for t in reversed(hist):
        if isinstance(t, dict) and str(t.get('role') or '') == 'user':
            pr = pairing(_turn_text(t))
            if pr:
                return pr[0], pr[1], False
    return None


def deck_ask(text):
    return bool(_DECK_RX.search(_norm(text)))


def _names_other_subject(text, pair):
    """The deck ask names a subject that is not the thread's pairing."""
    t = _norm(text)
    names = {_fold(x) for x in (pair or ()) if x}
    rest = []
    for w in re.findall(r"[A-Za-z0-9][\w&'.\-]*", t):
        f = _fold(w)
        if not f or w.lower().strip("'.") in _DECK_STOP:
            continue
        if any(f == n or (len(f) >= 3 and (f in n or n in f)) for n in names):
            continue
        rest.append(w)
    return bool(rest)


def deck_on_value_read(text, history, pick=''):
    """The words (or the subject picked for a deck) ask for a deck of
    the read in this thread that stated a partnership's dollar value."""
    turn = thread_value_read(history)
    if turn is None:
        return False
    pr = thread_pair(history)
    pair = (pr[0], pr[1]) if pr else ()
    p = _norm(pick)
    if p:
        return bool(pair) and not _names_other_subject(p, pair)
    t = _norm(text)
    if not t or not deck_ask(t):
        return False
    return not _names_other_subject(t, pair)


# ----------------------------------------------------------- exceptions
def saved_valuation(text='', history=None, pair=None, thread_note=True):
    """The Brand Partnership valuation already saved on the pairing the
    words (or the thread) name, in either order, or None. A saved
    valuation may be restated; nothing here asks for it again.
    `thread_note` lets the note that handed a valuation over in this
    thread count as one (so a deck asked for after it is never a second
    order); the read guard passes False and asks the library only, since
    a thread's turns are words and the library is the record."""
    pr = pair or pairing(text)
    if not pr and history:
        got = thread_pair(history)
        pr = (got[0], got[1]) if got else None
    if history and not pair and thread_note:
        # the valuation this thread itself delivered (the note that handed it over) is saved by
        # definition: a deck asked for after it is never a second order (2026-11-11)
        try:
            from prometheus import bpiq_deck as _bd
            _i, turn = _bd.valuation_turn(history)
            if turn is not None:
                m = _meta(turn)
                names = pairing(str(m.get('subject') or ''))
                if not pr or not names or {_fold(x) for x in names} == {_fold(x) for x in pr}:
                    return {'subject': str(m.get('subject') or ''), 'source_key': str(m.get('s3_key') or ''),
                            'job_id': str(m.get('bpiq_job_id') or ''), 'in_thread': True}
        except Exception:
            pass
    if not pr:
        return None
    try:
        if callable(SAVED_LOOKUP):
            return SAVED_LOOKUP(pr) or None
        from migration import bpiq_anchors as _ba
        return _ba.saved_valuation(tuple(pr)) or None
    except Exception:
        return None


def _is_start(text):
    try:
        from prometheus import bpiq_wizard as _w
        return bool(_w.is_start(text))
    except Exception:
        return False


# ------------------------------------------------------------------ owns
def owns(text, history=None, pick='', page=None):
    """'value', 'deck', 'analysis' or ''. See the module docstring.
    `page` is the open page's view context (or the page context that
    carries it)."""
    try:
        t = _norm(text)
        if not t and not pick:
            return ''
        if not pick and _is_start(t):
            return ''             # the order's own start phrase: the wizard opens it as it always did
        if pick or deck_ask(t):
            if deck_on_value_read(t, history, pick=pick) and not saved_valuation('', history):
                return KIND_DECK
            return ''
        if value_ask(t):
            return '' if saved_valuation(t, None) else KIND_VALUE
        if asks_dollar_value(t) and _REFERENT_RX.search(t) and len(t.split()) <= 24 and _thread_partnership(history):
            return '' if saved_valuation('', history) else KIND_VALUE
        return _owns_analysis(t, history, page)
    except Exception:
        return ''


def _owns_analysis(t, history, page):
    """'analysis' when the words ask for an analysis of a partnership
    with no saved valuation behind it (2026-11-16), or name one on the
    Brand Partnership page that is not the valuation open there."""
    entry = page_entry(page)
    pr = pairing(t)
    asked = analysis_ask(t)
    if pr:
        if entry and entry.get('name') and _same_pair(pr, entry['name']):
            return ''                                  # the valuation open on the page: a read may restate it
        if saved_valuation('', None, pair=pr):
            return ''
        if asked:
            return KIND_ANALYSIS
        # on the Brand Partnership page, a partnership that is not the one open there
        if entry and (_PAIR_RX.search(t) or _STRICT_PARTNER_RX.search(t)) and not _AUDIENCE_ASK_RX.search(t) \
                and not _ABOUT_PRODUCT_RX.search(t):
            return KIND_ANALYSIS
        return ''
    if not asked:
        return ''
    if entry and not _NEW_ORDER_RX.search(t):
        return ''                                      # about the valuation open on the page
    if _STRICT_REFERENT_RX.search(t) and not _NEW_ORDER_RX.search(t) and saved_valuation('', history):
        return ''                                      # about the valuation this thread already holds
    return KIND_ANALYSIS


def _window_in(text):
    """(start, end) ISO dates a read's own lines carry ('Sep 22 to Nov 11 2026'), or (None, None)."""
    try:
        from prometheus import bpiq_wizard as _w
        for m in re.finditer(r"[A-Z][a-z]{2,8}\.?\s+\d{1,2}(?:,?\s+20\d{2})?\s*(?:-|to|through)\s*(?:[A-Z][a-z]{2,8}\.?\s+)?\d{1,2},?\s+20\d{2}",
                             str(text or '')[:1600]):
            a, b = _w.parse_dates(m.group(0))
            if a and b:
                return a, b
    except Exception:
        pass
    return None, None


def seed(text, history=None, kind=KIND_VALUE):
    """The sentence the valuation order fills ahead from. '' when the
    words and the thread name nothing to fill."""
    pr = None
    oriented = False
    if kind in (KIND_VALUE, KIND_ANALYSIS):
        got = pairing(text)
        if got:
            pr = got
    if not pr:
        got = thread_pair(history)
        if got:
            pr, oriented = (got[0], got[1]), bool(got[2])
    if not pr:
        return ''
    if oriented:
        out = f"{pr[0]} is the talent and {pr[1]} is the brand."
    else:
        out = f"{pr[0]} x {pr[1]}"
    turn = thread_value_read(history) or last_read_turn(history)
    a, b = _window_in(_turn_text(turn)) if turn else (None, None)
    if a and b:
        out = f"{out} {a} to {b}" if not oriented else f"{out} {a} to {b}"
    return out


def lead(kind, had_draft=False):
    if kind == KIND_DECK:
        return LEAD_DECK
    if kind == KIND_ANALYSIS:
        return LEAD_ANALYSIS_OPEN if had_draft else LEAD_ANALYSIS
    return LEAD_VALUE_OPEN if had_draft else LEAD_VALUE


# ---------------------------------------------------------------- guards
def prompt_block(text):
    """The binding note for a read whose question is on a partnership
    with no saved valuation. '' for any other question."""
    try:
        t = _norm(text)
        if not t or not names_partnership(t) or saved_valuation(t, None):
            return ''
    except Exception:
        return ''
    return ("PARTNERSHIP DOLLAR VALUE (binding). This read never states what the partnership is worth: no dollar value, "
            "no valuation, no return in dollars, no earned media value (with or without a currency sign), no value mix "
            "or share of value by platform, for any window. It is not an analysis of how the partnership performed. "
            "Figures on an open Brand Partnership page belong to the valuation named on that page and are never restated "
            "under another partnership's name. Who the partnership "
            "reaches, the shared audience, the overlap, the fit, buyers and behavior are in scope and are stated as usual. "
            "If the question asks what the partnership is worth or how it performed, answer what is in scope and say in "
            "one line that it comes from a Brand Partnership valuation.")


def _usd_metric(m):
    if not isinstance(m, dict):
        return False
    unit = str(m.get('unit') or m.get('kind') or '').strip().lower()
    return unit in _USD_UNITS or unit.startswith('usd') or unit.startswith('$')


def _value_metric(m):
    if not _usd_metric(m):
        return False
    blob = ' '.join(str(m.get(k) or '') for k in ('name', 'label')).replace('_', ' ')
    return bool(_VALUE_LABEL_RX.search(blob))


def _strip_metric_segments(line, labels):
    """Drop 'Label: 1,234' segments of a metrics line whose label is one of `labels`."""
    out = line
    for lbl in labels:
        if not lbl:
            continue
        out = re.sub(r"(?:^|(?<=[,;]\s)|(?<=\.\s))\s*" + re.escape(lbl) + r"\s*:\s*[$\u00a3\u20ac]?\s?[\d,]+(?:\.\d+)?\s*[,;.]?\s*", '', out, flags=re.I)
    return out


def _clean_reply(reply, labels):
    """(text, removed): `reply` without the sentences and lines that
    state a partnership's dollar value."""
    removed = 0
    lines_out = []
    in_mix = False
    # read whole, so a sentence is judged with the partnership the reply is on in view
    whole_hits = stated_value_sentences(reply)
    for line in str(reply or '').split('\n'):
        raw = line
        # a value mix ("Platform mix ... (share of total campaign earned media value)") and the rows under it
        if _VALUE_SHARE_RX.search(line):
            in_mix = True
            removed += 1
            continue
        if in_mix:
            if re.match(r"^\s*(?:[-\u2022*]|\d+[.)])\s+\S", line):
                removed += 1
                continue
            in_mix = False
        if labels:
            line = _strip_metric_segments(line, labels)
        if line != raw:
            removed += 1
        hits = [h for h in whole_hits if h in line] or stated_value_sentences(line)
        if hits and re.match(r"^\s*(?:[-\u2022*]|\d+[.)])\s+\S", raw):
            removed += 1
            continue                  # a bullet is one claim: the value was it
        for h in hits:
            if h in line:
                line = line.replace(h, ' ')
                removed += 1
        line = re.sub(r"[ \t]{2,}", ' ', line).rstrip()
        if hits and raw[:1].strip():
            line = line.lstrip()
        if raw.strip() and not line.strip(" -\u2022*:;,."):
            continue                  # the whole line was the value
        lines_out.append(line)
    text = '\n'.join(lines_out)
    text = re.sub(r"\n{3,}", '\n\n', text).strip()
    return text, removed


_VALUE_FIG_RX = re.compile(r"emv|earned[_\s]media|media[_\s]value|brand[_\s]value|partnership[_\s]value|\bvalue\b|_value\b", re.I)
_PLATFORM_WORD_RX = re.compile(
    r"\b(?:value|earned|press|facebook|instagram|youtube|tiktok|reddit|twitter|snapchat|pinterest|linkedin|twitch|"
    r"platforms?|social|direct|brand\s+site)\b", re.I)
_PEOPLE_WORD_RX = re.compile(
    r"\b(?:people|persons?|viewers?|users?|accounts?|fans?|followers?|subscribers?|buyers?|shoppers?|customers?|audiences?|"
    r"orders?|visits?|sessions?|views?|clicks?|searches|individuals)\b", re.I)


def _mlabel(m):
    return ' '.join(str(m.get(k) or '') for k in ('name', 'label')).replace('_', ' ')


def _value_amounts(payload, bd):
    out = set()
    for r in ((bd or {}).get('rows') or []):
        if isinstance(r, dict):
            for k in ('pool', 'value', 'amount'):
                try:
                    v = float(r.get(k))
                    if v >= 1000:
                        out.add(int(round(v)))
                except (TypeError, ValueError):
                    pass
    for f in (payload.get('figures') or []) if isinstance(payload.get('figures'), list) else []:
        if not isinstance(f, dict) or str(f.get('source') or '') != 'view_data':
            continue
        if not _VALUE_FIG_RX.search(str(f.get('label') or '').replace('"', '')):
            continue
        try:
            v = float(f.get('value'))
            if v >= 1000:
                out.add(int(round(v)))
        except (TypeError, ValueError):
            pass
    return out


def _drop_amount_lines(text, nums, loose=False):
    """`text` without the sentences that carry one of `nums` (a value
    written bare). With `loose` (the reply printed a value mix), a
    sentence with a bare amount beside a platform or value word and no
    people word goes too."""
    if not text or not (nums or loose):
        return text
    forms = set()
    for n in nums:
        forms.add(f"{n:,}")
        forms.add(str(n))

    def _bad(sentence):
        if any(re.search(r"(?<![\d,.])" + re.escape(f) + r"(?![\d,]|\.\d)", sentence) for f in forms):
            return True
        return bool(loose and _BARE_AMOUNT_RX.search(sentence) and _PLATFORM_WORD_RX.search(sentence)
                    and not _PEOPLE_WORD_RX.search(sentence) and not _PRICE_TALK_RX.search(sentence))

    keep = []
    for line in text.split('\n'):
        if not line.strip():
            keep.append(line)
            continue
        m = re.match(r"^(\s*(?:[-\u2022*]|\d+[.)])\s+)(.*)$", line)
        head, body = (m.group(1), m.group(2)) if m else ('', line)
        parts = [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$\"'(\[])", body) if x.strip()]
        left = [x for x in parts if not _bad(x)]
        if not left or (m and len(left) < len(parts)):
            continue                      # a bullet is one claim: the value was it
        keep.append(head + ' '.join(left))
    return re.sub(r"\n{3,}", '\n\n', '\n'.join(keep)).strip()


def _substance(text):
    """True when `text` still says something: a line of six words or
    more that is not a heading ('On Charli XCX x Vinted:')."""
    for line in str(text or '').split('\n'):
        ln = line.strip(" -\u2022*")
        if not ln or ln.endswith(':'):
            continue
        if len(ln.split()) >= 6:
            return True
    return False


def guard_read(payload, question='', history=None, saved=None):
    """A read that states a partnership's dollar value with no saved
    valuation behind it loses those lines, its dollar value metrics and
    a value mix table, and gains one line pointing at the valuation.
    In place; returns True when anything changed. Never raises."""
    try:
        if not isinstance(payload, dict) or not payload.get('reply'):
            return False
        if payload.get('bpiq_wizard') or payload.get('bpiq_job_id') or payload.get('bpiq_confirm_payload'):
            return False
        reply = str(payload.get('reply') or '')
        metrics = payload.get('metrics') if isinstance(payload.get('metrics'), list) else []
        bad = [m for m in metrics if _value_metric(m)]
        bd = payload.get('breakdown') if isinstance(payload.get('breakdown'), dict) else None
        bd_text = ' '.join(str(bd.get(k) or '') for k in ('dimension', 'share_basis')) if bd else ''
        profile = str(payload.get('profile') or '')
        about = bool(names_partnership(question) or _PAIR_RX.search(profile) or names_partnership(reply[:400]))
        bd_named = bool(bd) and bool(_VALUE_SHARE_RX.search(bd_text) or _STRONG_VALUE_RX.search(bd_text))
        bd_value = bd_named or (bool(bd) and bool(re.search(r"\bvalue\b", bd_text, re.I))
                                and bool(_PARTNER_RX.search(bd_text + ' ' + reply[:400])))
        hits = stated_value_sentences(reply)
        share = value_share_lines(reply) if about else []
        if not (bad or bd_value or hits or share):
            return False
        if saved is None:
            # each place a pairing can be named is asked on its own: run together, a one-word
            # answer ("Vinted") and the read's own heading make a pairing nobody typed (2026-11-16)
            for src in (profile, question, reply[:400]):
                pr = pairing(src) if src else None
                if pr:
                    saved = saved_valuation(pair=pr, thread_note=False)
                    if saved:
                        break
            if saved is None:
                saved = saved_valuation(f"{question} {profile} {reply[:400]}", history, thread_note=False)
        if saved:
            return False
        labels = [str(m.get('label') or '') for m in bad]
        text, _n = _clean_reply(reply, labels)
        # the amounts that are the value itself, written bare: a value mix's own rows, and what the
        # open page handed over as value ("emv_value"). A sentence that carries one goes (2026-11-16).
        nums = _value_amounts(payload, bd if bd_value else None)
        text = _drop_amount_lines(text, nums, loose=bool(share))
        if not _substance(text) and (bd_named or share or nums):
            # nothing but the partnership's value was in the read: it was an analysis of the
            # partnership, and that comes from the valuation alone. Its figures and its file go too.
            payload['reply'] = POINTER_ANALYSIS
            for k in ('breakdown', 'file_link', 'files', 'deck_angle'):
                payload.pop(k, None)
            for k in ('metrics', 'figures'):
                if isinstance(payload.get(k), list):
                    payload[k] = []
            payload['followups'] = [CHIP]
            payload['offer_deck'] = False
            payload['partnership_value_withheld'] = True
            payload['partnership_read_withheld'] = True
            return True
        if bd_value or nums:
            payload.pop('file_link', None)        # the file is the value table
            payload.pop('files', None)
            if isinstance(payload.get('metrics'), list):
                metrics = [m for m in metrics if not (isinstance(m, dict) and (_VALUE_SHARE_RX.search(_mlabel(m))
                                                                               or _STRONG_VALUE_RX.search(_mlabel(m))
                                                                               or re.search(r"\bshare\s+of\s+(?:total\s+)?value\b", _mlabel(m), re.I)))]
                payload['metrics'] = metrics
            if isinstance(payload.get('followups'), list):
                payload['followups'] = [f for f in payload['followups']
                                        if not re.search(r"\bearned\s+(?:media\s+)?value\b|\bemv\b|\bmedia\s+value\b|\bbrand\s+value\b|"
                                                         r"\bpartnership\s+value\b|\bemail\s+me\s+this\s+file\b", str(f), re.I)]
        if bd_value:
            # the mix table's own lines ("Partnership value component mix, ..." and its rows) go with it
            rows = [str(r.get('label') or '') for r in (bd.get('rows') or []) if isinstance(r, dict)]
            keep = []
            for line in text.split('\n'):
                low = line.lower()
                if re.search(r"\bvalue\b[^\n]{0,60}\bmix\b|\bmix\b[^\n]{0,60}\bvalue\b", low) and _PARTNER_RX.search(line):
                    continue
                if any(r and line.strip(' -\u2022*').lower().startswith(r.lower()) for r in rows):
                    continue
                keep.append(line)
            text = re.sub(r"\n{3,}", '\n\n', '\n'.join(keep)).strip()
            payload.pop('breakdown', None)
        if bad:
            payload['metrics'] = [m for m in metrics if not _value_metric(m)]
        if isinstance(payload.get('figures'), list) and bad:
            gone = set()
            for m in bad:
                try:
                    gone.add(round(float(m.get('value')), 2))
                except (TypeError, ValueError):
                    pass

            def _is_gone(f):
                try:
                    return isinstance(f, dict) and round(float(f.get('value')), 2) in gone
                except (TypeError, ValueError):
                    return False
            payload['figures'] = [f for f in payload['figures'] if not _is_gone(f)]
        first = next((ln.strip() for ln in reply.split('\n') if ln.strip()), '')
        lead_gone = bool(first) and not text.startswith(first[:60])
        if not text:
            payload['reply'] = POINTER_LINE
        elif lead_gone:
            payload['reply'] = POINTER_LINE + '\n\n' + text       # the read's lead was the value: the pointer leads
        else:
            payload['reply'] = text + '\n\n' + POINTER_LINE
        fu = payload.get('followups')
        fu = list(fu) if isinstance(fu, list) else []
        if CHIP not in fu:
            fu.insert(0, CHIP)
        payload['followups'] = fu
        payload['offer_deck'] = False
        payload['partnership_value_withheld'] = True
        return True
    except Exception:
        return False


# decision reasons whose replies are a product's own words, never a read
_SKIP_REASONS = frozenset(('bpiq_wizard', 'pricing', 'product_facts', 'definitions', 'status', 'status_fast', 'capability',
                           'help_writing', 'intake_cancel', 'number_provenance', 'deliverable_lookup', 'catalog',
                           'catalog_exists', 'wayfinding', 'email_file',
                           'valuation_referent'))   # the delivered valuation's own figure, said again (2026-11-20)


def guard_payload(raw, decision=None, question='', history=None):
    """The same check for an answer written in the request itself (a
    page answer, a follow-up). Only an analyze answer is looked at."""
    try:
        if not isinstance(raw, dict) or raw.get('action') not in ('answer', None) or not raw.get('reply'):
            return False
        if str((decision or {}).get('reason') or '') in _SKIP_REASONS:
            return False
        if any(raw.get(k) for k in ('bpiq_wizard', 'bpiq_collect', 'bpiq_job_id', 'bpiq_confirm_payload', 'read_job_id',
                                    'job_id', 'panel_offer', 'spec_draft', 'spec_drafts', 'guidance')):
            return False
        return guard_read(raw, question=question, history=history)
    except Exception:
        return False


__all__ = ['CHIP', 'KIND_VALUE', 'KIND_DECK', 'KIND_ANALYSIS', 'POINTER_LINE', 'POINTER_ANALYSIS', 'LEAD_VALUE',
           'LEAD_VALUE_OPEN', 'LEAD_DECK', 'LEAD_ANALYSIS', 'LEAD_ANALYSIS_OPEN', 'owns', 'seed', 'analysis_ask',
           'page_entry', 'value_share_lines',
           'lead', 'value_ask', 'asks_dollar_value', 'names_partnership', 'pairing', 'thread_pair', 'thread_value_read',
           'turn_states_value', 'stated_value_sentences', 'deck_on_value_read', 'saved_valuation', 'prompt_block',
           'guard_read', 'guard_payload']
