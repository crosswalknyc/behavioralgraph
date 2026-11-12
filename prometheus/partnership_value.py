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

Pure text and the saved-valuation lookup. User-facing copy is
Prometheus first person. No em dashes. Nothing here says how a number
was produced.
"""
from __future__ import annotations

import re

CHIP = 'Value a brand partnership'
KIND_VALUE = 'value'
KIND_DECK = 'deck'

LEAD_VALUE = ("What a partnership is worth in dollars comes from a Brand Partnership valuation. "
              "I'll set that up.")
LEAD_VALUE_OPEN = "That figure is what this valuation produces."
LEAD_DECK = ("A deck on what this partnership is worth is the Brand Partnership valuation: it measures "
             "the campaign and comes with its own deck.")
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
    r"\bhow\s+much\s+(?:is|are|does|do|for|would)\s+(?:a|an|the|one|your)?\s*(?:brand\s+)?(?:partnership\s+)?valuations?\b", re.I)
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
}

# a sentence that states a partnership's dollar value
_MONEY_RX = re.compile(r"(?:[$\u00a3\u20ac]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|mm|b|bn|million|billion|thousand)?\b|"
                       r"\b\d[\d,]*(?:\.\d+)?\s?(?:million|billion|thousand|k|m|mm|bn)?\s+(?:us\s+)?dollars\b|\busd\s?\d[\d,]*)", re.I)
_STATED_VALUE_RX = re.compile(
    r"\bworth\b|\bvalued\s+at\b|\bvaluation\b|\b(?:of|in)\s+(?:total\s+|brand\s+|earned\s+media\s+|media\s+|partnership\s+)?value\b|"
    r"\b(?:partnership|sponsorship|brand|media|earned\s+media|deal|collab|total)\s+value\b|\bvalue\s+(?:of|to\s+date|so\s+far|is|was|"
    r"comes\s+to|came\s+to|reads\s+as|stands\s+at)\b|\bcarr(?:y|ies|ied)\b[^.]{0,40}\bvalue\b|\broi\b|\breturn\s+on\b|\bearned\s+media\b|"
    r"\bemv\b", re.I)
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
    return None


_PAIR_LEAD_RX = re.compile(
    r"^(?:(?:what|how|is|was|are|were|the|a|an|of|for|on|about|between|value|valuation|worth|much|did|does|do|can|could|you|"
    r"please|me|tell|show|give|whats|what's|roi|return|earned|media|dollar|deck)\s+)+", re.I)
_PAIR_TAIL_RX = re.compile(r"\s+(?:" + _PARTNER_NOUN + r"|campaign|worth|value|valuation|roi)\b.*$", re.I)


def _trim_pair(pr):
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
    if not body or not _MONEY_RX.search(body):
        return out
    about = bool(_PARTNER_RX.search(body) or _PAIR_RX.search(body))
    for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$\"'(\[-])|\n+", body):
        s = piece.strip()
        if not s or not _MONEY_RX.search(s) or _PRICE_TALK_RX.search(s):
            continue
        if not _STATED_VALUE_RX.search(s):
            continue
        if _PARTNER_RX.search(s) or _PAIR_RX.search(s) or (about and _VALUE_LABEL_RX.search(s)):
            out.append(s)
    return out


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
def saved_valuation(text='', history=None, pair=None):
    """The Brand Partnership valuation already saved on the pairing the
    words (or the thread) name, in either order, or None. A saved
    valuation may be restated; nothing here asks for it again."""
    pr = pair or pairing(text)
    if not pr and history:
        got = thread_pair(history)
        pr = (got[0], got[1]) if got else None
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
def owns(text, history=None, pick=''):
    """'value', 'deck' or ''. See the module docstring."""
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
        return ''
    except Exception:
        return ''


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
    if kind == KIND_VALUE:
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
            "no valuation, no return in dollars, no earned media value, no value mix, for any window. Who the partnership "
            "reaches, the shared audience, the overlap, the fit, buyers and behavior are in scope and are stated as usual. "
            "If the question asks what the partnership is worth, answer what is in scope and say in one line that the "
            "dollar value comes from a Brand Partnership valuation.")


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
    for line in str(reply or '').split('\n'):
        raw = line
        if labels:
            line = _strip_metric_segments(line, labels)
        if line != raw:
            removed += 1
        hits = stated_value_sentences(line)
        for h in hits:
            if h in line:
                line = line.replace(h, ' ')
                removed += 1
        line = re.sub(r"[ \t]{2,}", ' ', line).rstrip()
        if raw.strip() and not line.strip(" -\u2022*:;,."):
            continue                  # the whole line was the value
        lines_out.append(line)
    text = '\n'.join(lines_out)
    text = re.sub(r"\n{3,}", '\n\n', text).strip()
    return text, removed


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
        bd_value = bool(bd) and bool(re.search(r"\bvalue\b", ' '.join(str(bd.get(k) or '') for k in ('dimension', 'share_basis')), re.I)) \
            and bool(_PARTNER_RX.search(' '.join(str(bd.get(k) or '') for k in ('dimension', 'share_basis')) + ' ' + reply[:400]))
        hits = stated_value_sentences(reply)
        if not (bad or bd_value or hits):
            return False
        if saved is None:
            saved = saved_valuation(f"{question} {payload.get('profile') or ''} {reply[:400]}", history)
        if saved:
            return False
        labels = [str(m.get('label') or '') for m in bad]
        text, _n = _clean_reply(reply, labels)
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
                           'catalog_exists', 'wayfinding', 'email_file'))


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


__all__ = ['CHIP', 'KIND_VALUE', 'KIND_DECK', 'POINTER_LINE', 'LEAD_VALUE', 'LEAD_VALUE_OPEN', 'LEAD_DECK', 'owns', 'seed',
           'lead', 'value_ask', 'asks_dollar_value', 'names_partnership', 'pairing', 'thread_pair', 'thread_value_read',
           'turn_states_value', 'stated_value_sentences', 'deck_on_value_read', 'saved_valuation', 'prompt_block',
           'guard_read', 'guard_payload']
