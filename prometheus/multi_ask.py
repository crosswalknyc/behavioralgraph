"""Several asks in one message (Jenna 2026-10-30; numbered lists 2026-11-04).

"How big is the Reba audience, and what are the top five QSRs for
Gilmore Girls viewers?" used to get one quote (the size) and the second
question was dropped once the first was paid: the priced gate ran on the
whole message and the split-and-answer-both logic lived only on the plain
analysis path, after the gate.

Now each question routes on its own, through the same door:

  * every question that comes back as a priced offer is listed in ONE
    itemized quote with its own price and a total; Approve charges each
    part at its quoted price (its own ledger line) and runs each read;
    Cancel runs none of them;
  * a question that answers at the metered rate, or free, answers right
    away, in the same reply, next to the quote;
  * one question is one question: the lane steps aside and the single
    path runs exactly as before.

Jenna asked whether to quote the asks together or one at a time. One
itemized quote: a reader with two questions approves once, sees what each
costs, and gets both answers. The charges stay per ask on the ledger.

A NUMBERED LIST IS ALL OF ITS ITEMS (Jenna 2026-11-04, on Jay Patel's
seven numbered asks that came back as three: "this is more than 3 reads
... just so next time it realizes it's all of those"). The sentence
splitter cut Jay's message at the three question marks that sat inside
items (the quoted items end in ?" and the last chunk opened with
"Describe", so four items vanished and the survivors were tails of one
item glued to the head of the next; "Break this down by age, gender,
ethnicity, household income and region" read as a geography split). Now:

  * a list numbered 1., 2., 3. (or 1), 2)) is parsed item by item, quoted
    or not, with "As of [June 2023]:" windows and bracketed placeholders,
    up to MAX_ITEMS; past the cap the reply says how many were left and
    that they can be sent next;
  * each item carries its own one-line description built from its own
    words: the measure, the subject, every breakdown dimension it names,
    and its window;
  * pricing follows priced_read: the first priced read on a subject in
    the message carries its price, a later item on the same subject is
    included at no extra charge (metered), an item the lanes answered
    from what we hold is listed as answered at no charge, and the total
    is the sum of the priced lines;
  * a rider ("for each answer above, show the sample size and say whether
    the data is measured or ...") is not a read: it rides every item as a
    reporting instruction and is acknowledged in plain words;
  * an item whose brackets name no date ("[a title WWSTA passed on]") is
    held for its names: listed, never quoted blind, and asked for by name
    through the referents clarify path when Approve lands.

The lane sits in prometheus.service.ask ahead of the where_watched and
sizing lanes; each part re-enters ask() with ``_multi_part`` set so the
lane never recurses, and a per-part ``panel_confirm`` carries the chip's
own confirm on Approve. The widget and the pending-offer store pass the
``parts`` through unchanged.
"""
from __future__ import annotations

import re

MAX_ITEMS = 10          # items quoted in one reply; the rest are named as left over, never dropped silently
MAX_LIST_CHARS = 6000   # a numbered list of long items; the sentence splitter keeps its 1500

_CLAUSE_OPENERS = (r"what|which|who|how|where|when|why|do|does|did|is|are|was|were|can|could|"
                   r"would|will|should|has|have|tell me|show me|list|compare")
# ", and what are ..." / "; how many ..." / " and also which ..." splits a
# one-sentence double ask into its two questions
_CLAUSE_SPLIT_RX = re.compile(
    r"(?:[,;]\s*|\s+)(?:and|plus|also|then)(?:\s+(?:also|then))?\s+(?=(?:" + _CLAUSE_OPENERS + r")\b)", re.I)
_PRICE_RX = re.compile(r"[\d,]+(?:\.\d+)?")
# a clause that stands as its own question names something: a capitalized word past its first, a figure, a quoted title
_SUBJECT_SIGNAL_RX = re.compile(r"(?<!^)(?<![.?!]\s)\b[A-Z][A-Za-z0-9&'+.-]+|\d|[\"\u201c\u2018']\w")
_MIN_WORDS = 3

# ----------------------------------------------------------- numbered lists
_NUM_HEAD_RX = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)]\s+(?=\S)", re.M)
_QUOTE_CHARS = '"\u201c\u201d\u2018\u2019\''
_RIDER_RX = re.compile(
    r"\b(?:for|in|on|with|across|to|alongside)\s+(?:each|every|all(?:\s+of)?)\s+(?:of\s+)?(?:the\s+|these\s+|those\s+|your\s+)?"
    r"(?:answers?|items?|questions?|reads?|results?|responses?|replies|outputs?|numbers?|figures?|of\s+the\s+above|the\s+above|above)\b"
    r"|\b(?:each|every|all)\s+(?:of\s+)?(?:the\s+)?(?:answers?|items?|questions?|reads?|results?|responses?)\s+(?:above|below|here)\b"
    r"|\ball\s+of\s+the\s+above\b|\bevery\s+answer\b|\beach\s+answer\b", re.I)
# the rider's asks, in our words: how many people sit behind the figures and what each figure counts
RIDER_NOTE = "Each answer will state the people in the data behind it and what it counts."
RIDER_INSTRUCTION = ("State the people in the data behind the figures and what each figure counts "
                     "(people with at least one digital touchpoint with the subject in the window, projected to the US).")

_NUM_WORDS = {2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}


def _dashes(s):
    return str(s or '').replace('\u2014', '-').replace('\u2013', '-')


def _unquote(s):
    s = str(s or '').strip()
    # the item was written in quotes: "…?" or "…." with the quote after the stop
    while s and s[0] in _QUOTE_CHARS:
        s = s[1:].lstrip()
    while s and s[-1] in _QUOTE_CHARS:
        s = s[:-1].rstrip()
    return s.strip()


def numbered_items(text):
    """The items of a list numbered 1., 2., 3. (or 1), 2)) in order, each
    cleaned of its number and its quotes, every item kept whole (a second
    sentence rides its item). [] when the message is not such a list."""
    t = str(text or '').replace('\r', '\n')
    if not t.strip() or len(t) > MAX_LIST_CHARS:
        return []
    heads, expect = [], 1
    for m in _NUM_HEAD_RX.finditer(t):
        if int(m.group(1)) == expect:
            heads.append((m.start(), m.end()))
            expect += 1
    if len(heads) < 2:
        return []
    lead = t[:heads[0][0]].strip()
    if len(lead.split()) > 40:
        return []            # a paragraph with a list under it is the paragraph's question
    items = []
    for i, (s, e) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(t)
        body = ' '.join(t[e:end].split())
        body = _unquote(body)
        if not body:
            return []
        if len(body.split()) < 3 and not body.endswith('?'):
            return []        # "1. growth 2. retention" is a list of words, not of asks
        items.append(body)
    return items


def is_rider(item):
    """True when an item is an instruction about every answer rather than
    a question of its own ("For each answer above, show the sample size
    and say whether the viewing data is measured or ...")."""
    t = ' '.join(str(item or '').split())
    if not t or len(t) > 400:
        return False
    return bool(_RIDER_RX.search(t))


def normalize_item(item):
    """One item as it runs: whitespace folded, en and em dashes to
    hyphens, a bracketed date unbracketed ('as of [June 2023]' -> 'as of
    June 2023'); a placeholder that names no date keeps its brackets."""
    t = _unquote(_dashes(' '.join(str(item or '').split())))
    try:
        from . import referents as _refs
        t = _refs.unbracket_windows(t)
    except Exception:
        pass
    return t.strip()


def placeholders_in(item):
    try:
        from . import referents as _refs
        return list(_refs.placeholders(item) or [])
    except Exception:
        return []


def parse(text, pma=None):
    """The asks in one message: {'items': [...], 'numbers': [...],
    'rider': str, 'left': int, 'numbered': bool}; None for one question.
    Items are in the reader's order and keep the reader's numbers; a
    rider item is set aside (it rides every other item); past MAX_ITEMS
    the rest are counted in ``left``."""
    try:
        from . import segment_sizing as _seg
        if _seg.is_segment_ask(text):
            return None          # a demographic segment and its brand split are one read, never two asks (2026-11-11)
    except Exception:
        pass
    listed = numbered_items(text)
    if len(listed) >= 2:
        rider_parts, items, numbers = [], [], []
        for no, it in enumerate(listed, start=1):
            if is_rider(it):
                rider_parts.append(normalize_item(it))
            else:
                items.append(normalize_item(it))
                numbers.append(no)
        if len(items) < 2:
            return None          # one ask (with or without a rider) is the single path
        left = max(0, len(items) - MAX_ITEMS)
        return {'items': items[:MAX_ITEMS], 'numbers': numbers[:MAX_ITEMS],
                'rider': (RIDER_INSTRUCTION if rider_parts else ''), 'rider_text': ' '.join(rider_parts),
                'left': left, 'numbered': True}
    qs = split(text, pma=pma)
    if len(qs) < 2:
        return None
    return {'items': qs, 'numbers': list(range(1, len(qs) + 1)), 'rider': '', 'rider_text': '',
            'left': 0, 'numbered': False}


def split(text, pma=None):
    """The distinct questions in one message, in order; [] for one.
    A numbered list is its items (riders set aside); otherwise the
    sentence splitter."""
    listed = numbered_items(text)
    if len(listed) >= 2:
        items = [normalize_item(it) for it in listed if not is_rider(it)]
        return items[:MAX_ITEMS] if len(items) >= 2 else []
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1500:
        return []
    qs = []
    try:
        if pma is None:
            import prometheus_analysis as pma   # noqa: WPS433
        qs = list(pma.split_questions(t) or [])
    except Exception:
        qs = []
    qs = fold_facets(qs, t)
    if len(qs) >= 2:
        return qs[:MAX_ITEMS]
    # a single sentence carrying two questions joined by ", and what ..."
    parts = [re.sub(r"\s+(?:and|plus|also|then)$", '', p.strip(' ,;'), flags=re.I).strip() for p in _CLAUSE_SPLIT_RX.split(t)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return []
    if any(len(p.split()) < _MIN_WORDS for p in parts):
        return []
    # the later clauses must bring their own subject (a name, a number, a
    # quoted title); "compare Reba and Gilmore Girls and tell me which
    # skews older" is one ask with its framing, not two
    for p in parts[1:]:
        if not _SUBJECT_SIGNAL_RX.search(p):
            return []
    parts = [p if p.endswith('?') else p + '?' for p in parts]
    return parts[:MAX_ITEMS]


# ------------------------------------------------------- facets of one ask
# ONE ASK WITH FACET QUESTIONS IS ONE ASK (Jenna 2026-11-10). Her message
# "do a digital journey of people who watched my life with the walter boys
# ... showing how they got there. was it from the blog on wattpad? social
# media contrnt? digital ads? just suggested as content to watch next? that
# kind of thing." was split at its question marks into four unrelated asks
# and one started a stray read. Trailing fragment questions that enumerate
# facets of the one ask (no subject or verb of their own, short, a marker
# like "that kind of thing" / "etc" / "for example" nearby, each naming a
# channel, path, option or attribute of the stated subject) are part of the
# single ask and ride into it as the facets to cover; the lane splits only
# on full independent questions.
_FACET_MARK_RX = re.compile(
    r"\b(?:that (?:kind|sort|type) of thing|things? like th(?:at|ose|is)|stuff like that|and so on|and so forth|etc\.?|"
    r"for example|for instance|e\.g\.|such as|or similar|and the like|whatever applies|anything like that)\b", re.I)
_FACET_MAX_WORDS = 8
_FACET_LEAD_RX = re.compile(r"^(?:just|or|and|maybe|perhaps|also|even|only)\s+", re.I)
_PRONOUN_Q_RX = re.compile(
    r"\b(?:was|is|were|are|did|does|do)\s+(?:it|they|that|this|those|these)\s+(?:(?:come|coming|get|getting|arrive|arriving)\s+)?"
    r"(?:from|via|through|because of|out of|off|by|by way of)?\s*(?P<what>[^?]{2,80}?)\s*\?\s*$", re.I)


def _is_facet_fragment(q):
    """A chunk with no subject or verb of its own: eight words or fewer,
    not opened by a question word, an auxiliary or an imperative, and
    naming no subject of its own (a capitalized name, a figure, a quoted
    title would make it a question about something else)."""
    body = str(q or '').strip().rstrip('?.! ')
    words = body.split()
    if not words or len(words) > _FACET_MAX_WORDS:
        return False
    if _Q_OPEN_RX.match(body) or _IMPERATIVE_RX.match(body):
        return False
    if _SUBJECT_SIGNAL_RX.search(body):
        return False
    return True


def fold_facets(qs, text=''):
    """Fold trailing facet fragments into the question they enumerate.
    ``qs`` is the sentence splitter's list; a fragment folds when the
    message carries a facet marker, or when it follows a question (the
    chunk before it ends in '?') and a run of such fragments closes the
    message. Full independent questions are never folded."""
    qs = [str(q or '').strip() for q in (qs or []) if str(q or '').strip()]
    if len(qs) < 2:
        return qs
    marker = bool(_FACET_MARK_RX.search(str(text or '')))
    out = [qs[0]]
    for q in qs[1:]:
        prev_q = out[-1].rstrip().endswith('?')
        if _is_facet_fragment(q) and (marker or prev_q):
            out[-1] = out[-1] + ' ' + q
            continue
        out.append(q)
    # a facet marker as its own tail ("that kind of thing.") rides the ask too
    if len(out) >= 2 and _FACET_MARK_RX.fullmatch(out[-1].strip().rstrip('.!? ')):
        out[-2] = out[-2] + ' ' + out.pop()
    return out


def facets_of(text):
    """The facets a single ask enumerates, in order: the object of its
    pronoun question ("was it from the blog on wattpad?" -> "the blog on
    wattpad") and each fragment question after it ("social media content",
    "digital ads", "suggested as content to watch next"). [] when the
    message enumerates none."""
    t = ' '.join(str(text or '').split())
    if not t:
        return []
    chunks = [c.strip() for c in re.split(r'(?<=\?)\s+', t) if c.strip()]
    facets = []
    started = False
    for c in chunks:
        body = c.rstrip('?.! ').strip()
        if _FACET_MARK_RX.fullmatch(body):
            continue
        m = _PRONOUN_Q_RX.search(c)
        if m and not started:
            facets.append(m.group('what').strip())
            started = True
            continue
        if started and _is_facet_fragment(c):
            facets.append(_FACET_LEAD_RX.sub('', body).strip())
    return [f for f in facets if f]


# --------------------------------------------------------- item descriptions
_AS_OF_LEAD_RX = re.compile(
    r"^(?:(?:using|with|based on|on)\s+(?:only\s+)?(?:the\s+)?data\s+(?:available\s+|that was available\s+)?)?"
    r"as\s+(?:of|at)\s+(?P<when>[^:;,]{4,40}?)\s*[:;,]\s*", re.I)
_AS_OF_TAIL_RX = re.compile(r"[,;]?\s+as\s+(?:of|at)\s+(?P<when>[^:;,?.]{4,40}?)\s*(?=[?.]?\s*$)", re.I)
_SENT_SPLIT_RX = re.compile(r"(?<=[?!])\s+|(?<=\.)\s+(?=[A-Z\"\u201c(\[])")
_BREAKDOWN_SENT_RX = re.compile(
    r"^(?:please\s+|and\s+|then\s+|also\s+)?(?:break(?:ing)?|broken|split(?:ting)?|cut(?:ting)?|slice|slicing|segment(?:ing)?|divide|dividing)\s+"
    r"(?:(?:this|that|it|them|each|the\s+(?:answer|answers|result|results|figure|figures|number|numbers|share|shares|percentage|percentages|percent|total|data|breakdown|above))\s+)?"
    r"(?:down|out|up)?\s*by\s+(?P<dims>.+?)[.?!]?$", re.I)
_BREAKDOWN_INLINE_RX = re.compile(
    r",?\s+(?:broken\s+(?:down|out)|split|cut|segmented|sliced)\s+by\s+(?P<dims>[^.?!]+?)(?=[.?!]?\s*$)", re.I)
_RANK_SENT_RX = re.compile(
    r"^(?:please\s+|and\s+|then\s+)?(?:rank(?:ed|ing)?|order(?:ed)?|sort(?:ed)?)\s+(?:the|these|those|them|all|each)?\s*(?P<noun>[A-Za-z][\w -]*?)?\s*(?:by|on)\s+(?P<by>.+?)[.?!]?$", re.I)
_DESCRIBE_SENT_RX = re.compile(
    r"^(?:please\s+|and\s+|then\s+|also\s+)?(?:describe|profile|characteri[sz]e|detail)\s+(?P<obj>.+?)(?:\s+by\s+(?P<by>.+?))?[.?!]?$", re.I)
_WHO_LEAD_RX = re.compile(r"^(?:among|for|of|within|across)\s+(?P<who>[^,]{3,160}?),\s+(?P<q>.+)$", re.I)
_SHARE_Q_RX = re.compile(
    r"^what\s+(?P<n>percentage|percent|share|proportion|fraction)(?:\s+of\s+(?P<of>.+?))?(?:\s+of\s+(?:them|these|those))?\s+"
    r"(?P<pred>(?:did|do|does|have|has|had|were|was|are|is|also|never|rarely|still|ever|would|could|only|went|watched|watch|bought|buy|used|use|streamed|stream|subscribed|subscribe)\b.*)$", re.I)
_SHARE_AND_RX = re.compile(r",?\s+and\s+what\s+(percentage|percent|share|proportion|fraction)\s+(?=\w)", re.I)
_WHAT_DO_RX = re.compile(
    r"^(?:what|which)\s+(?P<noun>.+?)\s+(?:do|does|did)\s+(?P<who>.+?)\s+"
    r"(?P<verb>buy|purchase|watch|stream|use|read|listen\s+to|play|subscribe\s+to|shop\s+at|shop|visit|prefer|follow|own|drive|eat|drink|wear|order|book|search\s+for|search)\b(?P<rest>.*)$", re.I)
_WHAT_ARE_RX = re.compile(r"^what\s+(?:are|is|were|was)\s+(?P<np>the\s+.+)$", re.I)
# "what does the conversion rate mean here" -> "what the conversion rate means here"
_WHAT_DOES_RX = re.compile(
    r"^what\s+(?P<aux>does|do)\s+(?P<subj>.+?)\s+(?P<verb>mean|measure|count|include|cover|represent|show|tell us|track|capture)\b(?P<rest>.*)$", re.I)
_THIRD_PERSON = {'mean': 'means', 'measure': 'measures', 'count': 'counts', 'include': 'includes', 'cover': 'covers',
                 'represent': 'represents', 'show': 'shows', 'tell us': 'tells us', 'track': 'tracks', 'capture': 'captures'}
# "what" ahead of a verb stays "what" ("what happened to churn"); ahead of a noun it is a which-question
_WHAT_VERB_RX = re.compile(
    r"^what\s+(?:is|are|was|were|do|does|did|has|have|had|will|would|should|can|could|might|happened|happens|drove|drives|"
    r"changed|changes|made|makes|explains|matters|caused|causes|comes|came|went|goes)\b", re.I)
_WHICH_RX = re.compile(r"^(?:what|which)\s+(?P<rest>.+)$", re.I)
_RANK_Q_RX = re.compile(
    r"^(?:please\s+)?rank\s+(?:(?P<dem>these|those|the\s+following|the|all)\s+)?(?P<noun>[A-Za-z][\w -]*?)\s+by\s+(?P<by>[^:]+?)(?:\s*:\s*(?P<list>.+))?$", re.I)
_HOW_MANY_RX = re.compile(r"^how\s+many\s+(?P<rest>.+)$", re.I)
_HOW_BIG_RX = re.compile(r"^how\s+(?:big|large)\s+(?:is|are|was|were)\s+(?P<rest>.+)$", re.I)
_YES_NO_RX = re.compile(r"^(?:do|does|did|are|is|were|was|have|has|had|can|could|would|will|should)\s+(?P<rest>.+)$", re.I)
_IMPERATIVE_RX = re.compile(r"^(?:please\s+)?(?:compare|show|list|give|tell\s+me|identify|map|pull|run|find|estimate|measure|size|count|summari[sz]e|break\s+down|lay\s+out)\b", re.I)
_Q_OPEN_RX = re.compile(r"^(?:what|which|who|how|where|when|why|do|does|did|is|are|was|were|can|could|would|will|should|has|have|rank)\b", re.I)


def _join(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return ', '.join(parts[:-1]) + ' and ' + parts[-1]


def _split_list(s):
    out = []
    for piece in re.split(r",|;|\s+and\s+|\s*&\s*|\s+plus\s+", str(s or ''), flags=re.I):
        p = ' '.join(piece.split()).strip(' .')
        if p and p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return out


_LC_FIRST_WORDS = frozenset((
    'the', 'a', 'an', 'which', 'what', 'how', 'who', 'where', 'when', 'why', 'whether', 'compare', 'show', 'list',
    'give', 'tell', 'identify', 'map', 'pull', 'run', 'find', 'estimate', 'measure', 'size', 'count', 'summarize',
    'summarise', 'break', 'lay', 'include', 'add', 'note', 'also', 'and', 'then', 'for', 'in', 'on', 'with', 'rank',
    'describe', 'please', 'do', 'does', 'did', 'is', 'are', 'was', 'were', 'can', 'could', 'would', 'will', 'should',
    'has', 'have', 'had', 'say', 'state', 'report', 'split', 'cut', 'order', 'sort', 'limit', 'keep', 'use', 'treat'))


def _lc(s):
    """A sentence folded into a line keeps a name's capital ('Prime
    Video is ...') and lowers an ordinary opener ('Compare ...')."""
    s = str(s or '')
    first = re.match(r"^([A-Za-z]+)", s)
    if first and first.group(1).lower() in _LC_FIRST_WORDS:
        return s[:1].lower() + s[1:]
    return s


def _cap(s):
    s = str(s or '')
    return s[:1].upper() + s[1:] if s else s


def _window_of(core):
    """('as of June 2023', the item without that clause)."""
    try:
        from . import referents as _refs
        is_win = _refs.is_window_text
    except Exception:
        def is_win(_s):
            return bool(re.search(r"(?:19|20)\d{2}", str(_s or '')))
    m = _AS_OF_LEAD_RX.match(core)
    if m and is_win(m.group('when')):
        return f"as of {m.group('when').strip()}", core[m.end():].strip()
    m = _AS_OF_TAIL_RX.search(core)
    if m and is_win(m.group('when')):
        rest = (core[:m.start()] + core[m.end():]).strip()
        return f"as of {m.group('when').strip()}", rest
    return '', core


def _sentences(core):
    parts = [p.strip() for p in _SENT_SPLIT_RX.split(core) if p and p.strip()]
    return parts or [core]


def _measure(main, who=''):
    """The main question as a plain noun phrase or an indirect question,
    without its question mark; '' when no shape fits."""
    q = ' '.join(str(main or '').split()).rstrip('?.! ').strip()
    if not q:
        return ''
    m = _WHO_LEAD_RX.match(q)
    if m and not who:
        who, q = m.group('who').strip(), m.group('q').strip()
    m = _SHARE_Q_RX.match(q)
    if m:
        n = m.group('n').lower()
        of = (m.group('of') or '').strip() or who
        pred = m.group('pred').strip()
        pred = _SHARE_AND_RX.sub(lambda mm: f", and the {mm.group(1).lower()} who ", pred)
        return f"the {n} of {of} who {pred}" if of else f"the {n} who {pred}"
    m = _WHAT_DO_RX.match(q)
    if m:
        out = f"the {m.group('noun').strip()} {m.group('who').strip()} {m.group('verb').strip()}{m.group('rest')}"
        return out + (f" for {who}" if who else '')
    m = _WHAT_ARE_RX.match(q)
    if m:
        return m.group('np').strip() + (f" for {who}" if who else '')
    m = _WHAT_DOES_RX.match(q)
    if m:
        verb = m.group('verb').lower()
        verb = _THIRD_PERSON.get(verb, verb) if m.group('aux').lower() == 'does' else verb
        return f"what {m.group('subj').strip()} {verb}{m.group('rest')}" + (f" for {who}" if who else '')
    m = _RANK_Q_RX.match(q)
    if m:
        noun, by, lst, dem = m.group('noun').strip(), m.group('by').strip(), (m.group('list') or '').strip(), (m.group('dem') or '').lower()
        if lst and not placeholders_in(lst):
            head = lst
        else:
            head = f"{'these' if dem in ('these', 'the following') else dem or 'the'} {noun}" if dem != 'those' else f"those {noun}"
        return f"{head} ranked by {by}" + (f" for {who}" if who else '')
    m = _HOW_BIG_RX.match(q)
    if m:
        return f"the size of {m.group('rest').strip()}" + (f" for {who}" if who else '')
    m = _HOW_MANY_RX.match(q)
    if m:
        return f"how many {m.group('rest').strip()}" + (f" for {who}" if who else '')
    m = _WHICH_RX.match(q)
    if m:
        lead = 'what' if _WHAT_VERB_RX.match(q) else 'which'
        return f"{lead} {m.group('rest').strip()}" + (f" for {who}" if who else '')
    if re.match(r"^(?:who|where|when|why|how)\b", q, re.I):
        return _lc(q) + (f" for {who}" if who else '')
    m = _YES_NO_RX.match(q)
    if m:
        return f"whether {m.group('rest').strip()}" + (f" for {who}" if who else '')
    if _IMPERATIVE_RX.match(q):
        return _lc(q) + (f" for {who}" if who else '')
    return ''


def describe(item):
    """One accurate line for an item, built from the item's own words.

    Returns {'line', 'measure', 'window', 'dimensions', 'rank',
    'placeholders', 'core'}; ``line`` is '' when no shape fits (the
    caller then quotes the item back or keeps the lane's lead)."""
    t = normalize_item(item)
    window, core = _window_of(t)
    phs = placeholders_in(core)
    sents = _sentences(core)
    main_i = next((i for i, s in enumerate(sents) if s.rstrip().endswith('?') or _Q_OPEN_RX.match(s)), 0)
    main = sents[main_i]
    tails = [s for i, s in enumerate(sents) if i != main_i]
    dims, rank, described, other = [], '', '', []
    m = _BREAKDOWN_INLINE_RX.search(main.rstrip('?.! '))
    if m:
        dims = _split_list(m.group('dims'))
        main = (main[:m.start()] + main[m.end():]).strip()
    for s in tails:
        s2 = s.strip()
        m = _BREAKDOWN_SENT_RX.match(s2)
        if m:
            dims = dims + [d for d in _split_list(m.group('dims')) if d.lower() not in [x.lower() for x in dims]]
            continue
        m = _RANK_SENT_RX.match(s2)
        if m:
            rank = f"ranked by {m.group('by').strip()}"
            continue
        m = _DESCRIBE_SENT_RX.match(s2)
        if m:
            obj, by = m.group('obj').strip(), (m.group('by') or '').strip()
            described = f"with {obj} described by {by}" if by else f"with {obj} described"
            continue
        other.append(s2.rstrip('.'))
    measure = _measure(main)
    if not measure:
        return {'line': '', 'measure': '', 'window': window, 'dimensions': dims, 'rank': rank,
                'placeholders': phs, 'core': core}
    line = measure
    if rank:
        line += f", {rank}"
    if dims:
        line += f", by {_join(dims)}"
    if described:
        line += f", {described}"
    if other:
        line += '; ' + '; '.join(_lc(o) for o in other)
    if window:
        line += f", {window}"
    line = _cap(line.strip().rstrip(',;')) + '.'
    return {'line': line, 'measure': measure, 'window': window, 'dimensions': dims, 'rank': rank,
            'placeholders': phs, 'core': core}


def _raw_of(result):
    """The legacy payload inside whatever a part's ask returned."""
    if result is None:
        return {}
    env = result[0] if isinstance(result, tuple) else result
    if hasattr(env, 'get_json'):
        try:
            env = env.get_json(silent=True) or {}
        except Exception:
            env = {}
    if not isinstance(env, dict):
        return {}
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else env.get('_raw')
    return raw if isinstance(raw, dict) else env


def _reply_of(raw):
    for k in ('reply', 'text', 'error'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _price_value(label):
    m = _PRICE_RX.search(str(label or ''))
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def _currency_prefix(label):
    s = str(label or '').strip()
    return s[:1] if s and not s[0].isdigit() else '$'


def _fmt_total(labels):
    vals = [_price_value(x) for x in labels]
    if not vals or any(v is None for v in vals):
        return ''
    total = sum(vals)
    pre = _currency_prefix(labels[0])
    return f"{pre}{total:,.0f}" if float(total).is_integer() else f"{pre}{total:,.2f}"


def _lead_of(offer_reply):
    """The offer's plain sentence on what the reader gets, without its
    own cost line."""
    s = str(offer_reply or '').strip()
    s = re.sub(r"\s*Pulling this data will cost[^.]*\.\s*$", '', s, flags=re.I).strip()
    return s


def _jobs(raws):
    out = []
    for r in raws:
        if not isinstance(r, dict):
            continue
        j = r.get('read_job_id') or r.get('job_id')
        if j and str(j) not in out:
            out.append(str(j))
    return out


def _followups(raws, limit=4):
    out = []
    for r in raws:
        if not isinstance(r, dict):
            continue
        for c in (r.get('followups') or []):
            if isinstance(c, str) and c.strip() and c not in out:
                out.append(c)
    return out[:limit]


def _part_block(i, question, raw):
    text = _reply_of(raw or {}) or 'Working on it. That answer will land in this thread when it is ready.'
    return f"{i}) {question}\n{text}"


def _same_subject(a, b):
    try:
        from . import priced_read as _prd
        return bool(_prd._same_subject(a, b))
    except Exception:
        na = re.sub(r"[^a-z0-9]+", "", str(a or '').lower())
        nb = re.sub(r"[^a-z0-9]+", "", str(b or '').lower())
        return bool(na) and bool(nb) and (na == nb or (len(na) >= 5 and (na in nb or nb in na)))


def _is_question_back(raw):
    """The lane asked the reader something (which audience, which
    subject, which ones) instead of answering."""
    if not isinstance(raw, dict):
        return False
    if str(raw.get('action') or '') == 'clarify':
        return True
    return any(raw.get(k) for k in ('memory_confirm', 'audience_confirm', 'subject_unclear', 'referent_clarify'))


def _nos_phrase(nos):
    return _join([str(n) for n in nos])


def combine_answers(parts, raws, numbers=None, rider=''):
    """One reply for several answered questions: numbered (the reader's
    own numbers), the question restated, the answer under it.
    Background reads ride as a job list (the widget polls each) and the
    first job in the single field."""
    numbers = list(numbers or range(1, len(parts) + 1))
    blocks = [_part_block(numbers[k] if k < len(numbers) else k + 1, q, r) for k, (q, r) in enumerate(zip(parts, raws))]
    jobs = _jobs(raws)
    out = {'success': True, 'action': 'answer', 'reply': '\n\n'.join(blocks),
           'followups': _followups(raws), 'offer_deck': False, 'deck_angle': None,
           'multi_ask': {'count': len(parts)}}
    if jobs:
        out['read_job_id'] = jobs[0]
        out['read_job_ids'] = jobs
    for r in raws:
        if not isinstance(r, dict):
            continue
        if r.get('subject') and not out.get('subject'):
            out['subject'] = r['subject']
        for k in ('memory_confirm', 'bpiq_wizard'):
            if r.get(k) and k not in out:
                out[k] = r[k]
    if rider:
        out['multi_ask']['rider'] = True
    return out


def _held_noun(item):
    """What the brackets stand for ('titles', 'brands', 'creators')."""
    try:
        from . import referents as _refs
        ref = _refs.placeholder_referent(item)
        noun = str((ref or {}).get('noun') or '').strip()
        return noun or 'names'
    except Exception:
        return 'names'


def _held_clarify(item, rider=''):
    """The which-ones question for an item whose brackets name no date."""
    try:
        from . import referents as _refs
        ref = _refs.placeholder_referent(item)
        if ref:
            return _refs.clarify_payload(ref, item, append=rider)
    except Exception:
        pass
    return {'success': True, 'action': 'clarify',
            'reply': 'Which ones? Name them and I will run this one on exactly those.',
            'followups': [], 'offer_deck': False, 'deck_angle': None,
            'referent_clarify': {'question': str(item or ''), 'noun': 'names', 'count': 0, 'phrase': ''}}


def _plan_lines(parts, raws, descs, numbers, numbered):
    """One entry per item: the line to print, its status and its offer
    part. Same-subject pricing is applied here: the first priced read on
    a subject carries its price, later ones on that subject are included."""
    entries = []
    first_priced = []          # [(subject, item_no)]
    for k, (q, r) in enumerate(zip(parts, raws)):
        d = descs[k] if k < len(descs) else {}
        no = numbers[k] if k < len(numbers) else k + 1
        po = r.get('panel_offer') if isinstance(r, dict) and isinstance(r.get('panel_offer'), dict) else None
        desc = str((d or {}).get('line') or '').strip()
        held = bool((d or {}).get('placeholders')) and r is None
        if held:
            phs = list(d.get('placeholders') or [])
            cw = _NUM_WORDS.get(len(phs), str(len(phs)))
            noun = _held_noun(q)
            line = (desc or f'"{q}"') + (f" The {cw} in brackets are not named yet: name the {noun} and it runs with the rest."
                                        if len(phs) > 1 else " The one in brackets is not named yet: name it and it runs with the rest.")
            part = {'text': str(q)[:600], 'subject': '', 'kind': 'report', 'dimension': '', 'frame': '',
                    'price_label': '', 'no': no, 'status': 'held', 'placeholders': phs, 'noun': noun}
            entries.append({'no': no, 'status': 'held', 'line': line, 'part': part, 'raw': None, 'q': q})
            continue
        if po:
            label = str(po.get('price_label') or '').strip() or 'the quoted price'
            subject = str(po.get('subject') or '').strip()
            dims = list((d or {}).get('dimensions') or [])
            dimension = _join(dims) if len(dims) > 1 else (dims[0] if dims else str(po.get('dimension') or ''))
            lead = desc if (numbered and desc) else (_lead_of(_reply_of(r)) or desc or f'"{q}"')
            part = {'text': str(po.get('question') or q)[:600] if not numbered else str(q)[:600],
                    'subject': subject[:120], 'kind': str(po.get('kind') or 'report'),
                    'dimension': str(dimension or ''), 'frame': str(po.get('frame') or ''), 'price_label': label, 'no': no}
            if po.get('years') and po.get('years') != 1:
                part['years'] = po['years']
            inc = next((n for s, n in first_priced if subject and _same_subject(s, subject)), None)
            if inc is not None:
                part.update({'status': 'included', 'included_with': inc, 'price_label': 'included'})
                entries.append({'no': no, 'status': 'included', 'line': f"{lead} Included with {inc} at no extra charge.",
                                'part': part, 'raw': r, 'q': q, 'included_with': inc})
            else:
                first_priced.append((subject, no))
                part['status'] = 'priced'
                entries.append({'no': no, 'status': 'priced', 'line': f"{lead} {label}.", 'part': part, 'raw': r,
                                'q': q, 'label': label})
            continue
        text = _reply_of(r or {})
        if _is_question_back(r):
            status, tag = 'question', ''
        elif isinstance(r, dict) and (r.get('read_job_id') or r.get('job_id')):
            status, tag = 'answered', ' Running now, no charge:'
        else:
            status, tag = 'answered', ' Answered from what we hold, no charge:'
        lead = desc if (numbered and desc) else f'"{q}"'
        body = text or 'Working on it. That answer will land in this thread when it is ready.'
        entries.append({'no': no, 'status': status, 'line': f"{lead}{tag}\n{body}", 'part': None, 'raw': r, 'q': q})
    return entries


def combined_offer(text, parts, raws, approve_chip='Approve', cancel_chip='Cancel', *, plan=None):
    """One itemized quote for the priced parts; metered and free answers
    ride along. ``panel_offer`` carries every part Approve runs: the
    priced ones at their price, the included ones at no extra charge,
    the held ones waiting on their names. ``plan`` is parse()'s result
    (numbers, rider, left, numbered) with ``descs`` from describe()."""
    plan = plan or {}
    numbered = bool(plan.get('numbered'))
    numbers = list(plan.get('numbers') or range(1, len(parts) + 1))
    descs = list(plan.get('descs') or [])
    rider = str(plan.get('rider') or '')
    left = int(plan.get('left') or 0)
    entries = _plan_lines(parts, raws, descs, numbers, numbered)
    priced = [e for e in entries if e['status'] == 'priced']
    included = [e for e in entries if e['status'] == 'included']
    held = [e for e in entries if e['status'] == 'held']
    answered = [e for e in entries if e['status'] in ('answered', 'question')]
    n = len(priced)
    labels = [e['label'] for e in priced]
    total = _fmt_total(labels)
    lines = []
    if numbered:
        listed = len(entries)
        if n == listed:
            head = ("Two priced reads here" if n == 2 else f"{n} priced reads here") + ", quoted together and run together:"
        else:
            head = (f"{listed} asks here, quoted together and run together; "
                    + (f"{n} carries a price:" if n == 1 else f"{n} carry a price:"))
        lines.append(head)
        for e in entries:
            lines.append(f"{e['no']}. {e['line']}")
    else:
        for e in answered:
            lines.append(_part_block(e['no'], e['q'], e['raw']))
        head = ("Two of these are priced reads" if n == 2 else f"{n} of these are priced reads") if answered \
            else ("Two priced reads here" if n == 2 else f"{n} priced reads here")
        lines.append(head + ", quoted together and run together:")
        k = 0
        for e in entries:
            if e['status'] in ('priced', 'included', 'held'):
                k += 1
                lines.append(f"{k}. {e['line']}")
    if rider:
        lines.append(RIDER_NOTE)
    if left > 0:
        first = max(numbers) + 1 if numbers else len(parts) + 1
        span = f"{first}" if left == 1 else f"{first} to {first + left - 1}"
        lines.append(f"That leaves {left} more ({span}); send {'it' if left == 1 else 'them'} next and I take {'it' if left == 1 else 'them'} the same way.")
    run_nos = [e['no'] for e in entries if e['status'] in ('priced', 'included', 'held')]
    if numbered and (answered or held):
        tail = f"Approve runs {_nos_phrase(run_nos)}" + (f" for {total} in all" if total else '')
        if held:
            hn = [e['no'] for e in held]
            nouns = []
            for e in held:
                nn = str(e['part'].get('noun') or 'names')
                if nn not in nouns:
                    nouns.append(nn)
            tail += f" ({_nos_phrase(hn)} as soon as you name the {_join(nouns)})"
    elif total and n == 2 and len(run_nos) == 2:
        tail = f"Approve runs both for {total} in all"
    elif total:
        tail = f"Approve runs all {len(run_nos)} for {total} in all"
    else:
        tail = "Approve runs them"
    lines.append(tail + "; Cancel runs none of them.")
    offer_parts = []
    for e in entries:
        if e['part'] is None:
            continue
        part = dict(e['part'])
        if rider:
            part['rider'] = rider
        offer_parts.append(part)
    jobs = _jobs([e['raw'] for e in answered])
    first_subject = next((e['part']['subject'] for e in priced if e['part'].get('subject')), '')
    out = {'success': True, 'action': 'answer', 'reply': '\n\n'.join(lines),
           'followups': [approve_chip, cancel_chip], 'offer_deck': False, 'deck_angle': None,
           'subject': first_subject,
           'panel_offer': {'question': str(text)[:600], 'subject': first_subject,
                           'kind': 'multi', 'frame': '', 'price_label': total or (labels[0] if labels else ''),
                           'parts': offer_parts},
           'multi_ask': {'count': len(parts), 'priced': n, 'included': len(included), 'held': len(held),
                         'answered': len(answered), 'rider': bool(rider), 'left': left}}
    if rider:
        out['panel_offer']['rider'] = rider
    if jobs:
        out['read_job_id'] = jobs[0]
        out['read_job_ids'] = jobs
    return out


def confirm_parts(panel_confirm):
    """The per-part confirms inside a multi confirm, or []: [(text,
    panel_confirm)] in order. An included part (same subject as an
    earlier priced part) carries a metered ``resume_charge`` so no lane
    charges it again; a held part carries ``held`` with its placeholders
    and is not run until its names land."""
    if not isinstance(panel_confirm, dict) or str(panel_confirm.get('kind') or '') != 'multi':
        return []
    out = []
    for p in (panel_confirm.get('parts') or []):
        if not isinstance(p, dict) or not str(p.get('text') or '').strip():
            continue
        pc = {'subject': str(p.get('subject') or ''), 'kind': str(p.get('kind') or 'report'),
              'dimension': str(p.get('dimension') or '')}
        if p.get('years') and p.get('years') != 1:
            pc['years'] = p['years']
        status = str(p.get('status') or '')
        if status == 'included':
            pc['resume_charge'] = {'credits': 0, 'subject': pc['subject'], 'kind': pc['kind'],
                                   'pull_type': 'Panel Report Follow-up', 'label': 'metered', 'metered': True,
                                   'included_with': p.get('included_with')}
        if status == 'held':
            pc['held'] = True
            pc['placeholders'] = list(p.get('placeholders') or [])
        if p.get('no'):
            pc['no'] = p['no']
        if str(p.get('rider') or '').strip():
            pc['rider'] = str(p['rider']).strip()
        out.append((str(p['text']).strip(), pc))
    return out


def _run_confirmed(confirms, run_one):
    """Approve landed on the joint quote: every listed part runs in order
    with its own subject and window (the included ones at no extra
    charge), a held part asks for its names instead of running, and the
    rider rides every run as a reporting instruction."""
    parts, raws, numbers = [], [], []
    ran = []
    referent = None
    for k, (q, pc) in enumerate(confirms):
        no = int(pc.get('no') or 0) or (k + 1)
        rider = str(pc.get('rider') or '')
        parts.append(q)
        numbers.append(no)
        if pc.get('held'):
            cl = _held_clarify(q, rider=rider)
            raws.append(cl)
            if referent is None and isinstance(cl.get('referent_clarify'), dict):
                referent = dict(cl['referent_clarify'])
            continue
        run_pc = {kk: v for kk, v in pc.items() if kk not in ('held', 'placeholders', 'no', 'rider')}
        run_q = q + (' ' + rider if rider and rider.lower() not in q.lower() else '')
        raws.append(_raw_of(run_one(run_q, run_pc, list(ran))))
        ran.append(q)
    out = combine_answers(parts, raws, numbers=numbers, rider=any(str(pc.get('rider') or '') for _, pc in confirms))
    if referent is not None:
        out['referent_clarify'] = referent
    out['multi_ask']['confirmed'] = True
    return out


def handle(text, body, run_one, *, pma=None, approve_chip='Approve', cancel_chip='Cancel'):
    """None when the lane does not apply; else the combined raw payload.

    ``run_one(question, panel_confirm, prior_parts)`` runs one question
    through the whole door, with the message's earlier questions behind
    it, and returns the ask result (envelope tuple or payload).
    """
    if not isinstance(body, dict) or body.get('_multi_part'):
        return None
    pc = body.get('panel_confirm')
    if not isinstance(pc, dict) or not pc:
        extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
        pc = extra.get('panel_confirm') if isinstance(extra.get('panel_confirm'), dict) else None
    confirms = confirm_parts(pc)
    if confirms:
        return _run_confirmed(confirms, run_one)
    if isinstance(pc, dict) and pc:
        return None             # a single confirm rides the single path
    # one title asked across streaming services is ONE build with service
    # cuts, never three questions (Jay Patel 2026-11-02: "three distinct
    # profiles ... on any streaming service / on Amazon Prime Video / on
    # HBO Max" ran as three "Working on it" parts); prometheus.platform_cuts
    try:
        from . import platform_cuts as _pcuts
        if _pcuts.detect(text):
            return None
    except Exception:
        pass
    plan = parse(text, pma=pma)
    if not plan or len(plan.get('items') or []) < 2:
        return None
    parts = list(plan['items'])
    # a numbered list of build orders is the build flow's batch, not a
    # set of questions: the interpret step drafts one brief per subject
    try:
        from . import priced_read as _prd
        if all(_prd.is_product_order(p) for p in parts):
            return None
    except Exception:
        pass
    rider = str(plan.get('rider') or '')
    descs = [describe(q) for q in parts]
    plan['descs'] = descs
    # a later part reads with the earlier ones behind it ("do they also
    # watch Yellowstone" binds "they" to the first question's audience);
    # an item whose brackets name no date is never run blind
    raws, prior = [], []
    for k, q in enumerate(parts):
        if descs[k].get('placeholders'):
            raws.append(None)
        else:
            run_q = q + (' ' + rider if rider else '')
            raws.append(_raw_of(run_one(run_q, None, list(prior))))
        prior.append(q)
    if any(isinstance(r, dict) and isinstance(r.get('panel_offer'), dict) and r.get('panel_offer') for r in raws):
        return combined_offer(text, parts, raws, approve_chip=approve_chip, cancel_chip=cancel_chip, plan=plan)
    # nothing priced: the answers, with a which-ones question for any held item
    referent = None
    for k, r in enumerate(raws):
        if r is None:
            cl = _held_clarify(parts[k], rider=rider)
            raws[k] = cl
            if referent is None and isinstance(cl.get('referent_clarify'), dict):
                referent = dict(cl['referent_clarify'])
    out = combine_answers(parts, raws, numbers=plan.get('numbers'), rider=rider)
    if referent is not None:
        out['referent_clarify'] = referent
    if plan.get('left'):
        first = max(plan.get('numbers') or [len(parts)]) + 1
        left = int(plan['left'])
        span = f"{first}" if left == 1 else f"{first} to {first + left - 1}"
        out['reply'] += f"\n\nThat leaves {left} more ({span}); send {'it' if left == 1 else 'them'} next and I take {'it' if left == 1 else 'them'} the same way."
        out['multi_ask']['left'] = left
    return out
