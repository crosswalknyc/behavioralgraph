"""An order is queued, never deflected (Jenna 2026-11-10, Grace Yin).

Grace, Amazon, 2026-11-10 20:53 UTC: "Please queue one Profile IQ: US
people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31
2026. Include reach, age and gender split, top other titles and genres
they watch, and their streaming platform mix. Deliver as a CSV with the
unprojected panelist count for each figure." The word "unprojected" in
the deliverable notes sent the whole message to the unprojected-number
lane, which answered that it held no profile to unproject and told her
to say "run a profile on Fallout Season 2". She had.

The rule: a message that is an ORDER, an imperative to queue, run,
build, pull, set up, start or open a product ("please queue one Profile
IQ", "run a profile on", "build a twin of", "pull Subscriber IQ for",
"set up attribution tracking", "open a focus group"), routes to that
product's build or brief flow FIRST. No keyword lane may take it: an
order that also says "unprojected", "reconcile", "how many", "raw" or
"what does X cost" is still an order. The deliverable notes that ride
behind it ("Include reach, ... Deliver as a CSV with ...") are
requirements on the file, never a second subject and never a cut; they
ride the brief as ``deliverable_notes`` and the confirm reply says what
the file will carry.

One detector, shared: ``is_order`` is read by ``service.ask`` ahead of
the deterministic lanes, by each side lane's ``wants`` and by
``understand.decide`` (an order for a profile is the interpret surface).
``split`` hands the build flow the order itself and the deliverable
notes apart. ``build_line`` is the one sentence an unlimited seat reads
when its order starts with no approve chip.

Not an order: a question about the product or a run ("did you queue
it", "is the build done", "what does a profile cost", "how long does a
build take", "do we have a profile for X", "can you measure watch
time"), a negated build ("don't build a new profile"), a correction or a
pushback ("why do you keep building", "I already pulled it"), a cancel.

Pure text; no model, no S3, no host. Never raises.
"""
from __future__ import annotations

import re

__all__ = ['is_order', 'is_profile_order', 'product_of', 'split', 'deliverable_notes', 'order_core',
           'build_line', 'deliverable_line']

# the products an order can name, longest first where one contains another
_PRODUCTS = (
    r"profile iqs?|profiles?|subscriber iqs?|digital journey(?: iq)?s?|journey iqs?|journeys?|"
    r"flywheel(?: iq)?s?|attribution(?: iq)?(?: tracking| campaign)?|trends iq|impact iq|analysis iq|"
    r"brand partnership(?: iq| valuation)?s?|valuations?|digital twins?(?: iq)?|twins?|focus groups?|"
    r"pilot tests?(?:ing)?|episode tests?|screenings?|binge tests?|greenlight|launch lab|"
    r"audiences?|universes?|cohorts?|segments?|cuts?|trackers?|tracking|campaigns?|stud(?:y|ies)|"
    r"decks?|one[- ]pagers?|reads?|pulls?|datasets?|data sets?|builds?|runs?|"
    # the typo forms of profile the build order carries (subject_rules.EXPLICIT_BUILD_RX)
    r"aprofiles?|porfiles?|profles?|proifles?|profiels?|prfiles?|profil|profie|profl"
)
# a pronoun standing for the thing to build ("ok queue it", "build it on Mountain Dew"); never a named product,
# so is_profile_order stays False and the surface keeps its own read of a bare "run it"
_PRONOUN_PRODUCT = r"it|that|this|them|those|these|the same|another one|one more|that one|this one"
_VERBS = (
    r"queue|re-?queue|run|re-?run|build|re-?build|pull|re-?pull|create|make|generate|draft|set ?up|spin up|"
    r"start|open|launch|kick ?off|order|commission|fire off|schedule|put together|get going on|produce"
)
# the words a person puts before the verb
_LEAD = (
    r"(?:(?:please|pls|plz|kindly|just|also|and|then|now|next|so|ok(?:ay)?|yes|yep|sure|go ahead and|"
    r"no|nope|nah|not that|wait|hold on|actually|sorry|hmm|instead|"
    r"can you|could you|would you|will you|can we|could we|i(?:'d| would) like(?: you)?(?: to)?|i need(?: you to)?|"
    r"i want(?: you to)?|we need(?: you to)?|we want(?: you to)?|we(?:'d| would) like(?: you to)?|let'?s|lets)[,\s]+)*"
)
# what sits between the verb and the product noun
_BETWEEN = (
    r"(?:(?:me|us|for me|for us)\s+)?"
    r"(?:(?:a|an|the|one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2}|those|these|them|each|every|all|"
    r"another|other|new|fresh|full|separate|individual|single|quick|same|this|that|its|our|your|my|as|into|up|out|"
    r"also|too)\s+)*"
    # a name typed before the noun ("build me a Nike profile", "run a Chipotle buyers profile"): up to four
    # words, none of them a preposition or a figure word (those open a different sentence shape)
    r"(?:(?!(?:on|of|for|about|from|with|in|to|at|by|and|or|numbers?|figures?|data|size|how|what|which)\b)[^\s.,;:?!()]+\s+){0,4}?"
)
# a want-led order with no verb ("I need a profile of Chipotle buyers", "we want a twin on the pilot")
_WANT_LEAD = (
    r"(?:(?:please|pls|also|and|then|ok(?:ay)?|so)[,\s]+)*"
    r"(?:(?:i|we)(?:'d| would)? (?:need|want|like|am after|'m after|am looking for|'m looking for)(?: you to (?:" + _VERBS + r"))?|"
    r"(?:get|give|send|make) (?:me|us))\s+"
)
_SENTENCE_START = r"(?:^|(?<=[.;:!?\n])\s*|(?<=[.;:!?])\s+)"
_ORDER_RX = re.compile(
    _SENTENCE_START + r"\W*(?:" + _LEAD + r"(?P<verb>" + _VERBS + r")\s+" + _BETWEEN
    + r"(?P<product>" + _PRODUCTS + r")\b|" + _WANT_LEAD + _BETWEEN + r"(?P<product2>" + _PRODUCTS + r")\b|"
    + _LEAD + r"(?P<verb2>queue|re-?queue|run|re-?run|build|re-?build|pull|re-?pull|start|launch|kick ?off)\s+"
    + r"(?P<pronoun>" + _PRONOUN_PRODUCT + r")\b)(?P<rest>[^\n]{0,400})", re.I)
# "profile X for me" / "profile the Y audience" are not orders here (a bare noun order is the interpret step's call)

# the products whose build flow is the Profile IQ interpret surface
_PROFILE_PRODUCT_RX = re.compile(
    r"^(?:profile iqs?|profiles?|audiences?|universes?|cohorts?|segments?|cuts?|builds?|datasets?|data sets?|pulls?|"
    r"aprofiles?|porfiles?|profles?|proifles?|profiels?|prfiles?|profil|profie|profl)$", re.I)
_PRODUCT_FAMILY = (
    ('subscriber_iq', re.compile(r"^subscriber iq", re.I)),
    ('journey', re.compile(r"^(?:digital journey|journey)", re.I)),
    ('flywheel', re.compile(r"^flywheel", re.I)),
    ('attribution', re.compile(r"^(?:attribution|tracking|trackers?|campaigns?)$|^attribution", re.I)),
    ('bpiq', re.compile(r"^(?:brand partnership|valuations?)", re.I)),
    ('twin', re.compile(r"^(?:digital twins?|twins?|focus groups?|pilot tests?|episode tests?|screenings?|binge tests?|greenlight|launch lab)", re.I)),
    ('deck', re.compile(r"^(?:decks?|one[- ]pagers?)$", re.I)),
    ('read', re.compile(r"^(?:reads?|stud(?:y|ies))$", re.I)),
    ('profile', _PROFILE_PRODUCT_RX),
)

# a question about the product or the run, never an order
_QUESTION_OPEN_RX = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok(?:ay)?|so|and|also|but|quick question|question)[,:!.\s]+)*"
    r"(?:did|do|does|have|has|had|is|are|was|were|will|would|should|when|where|why|who|what|which|how)\b", re.I)
_NEGATED_RX = re.compile(
    r"\b(?:do ?n[o']?t|don'?t|do not|never|no need to|not|without|instead of|rather than|stop)\s+"
    r"(?:(?:re-?)?(?:queue|run|build|pull|create|make|generate|draft|set ?up|start|open|launch|order)|"
    r"(?:a |another |the |any )?(?:new |fresh |second )?(?:build|profile|run|pull))\b", re.I)
_PUSHBACK_RX = re.compile(
    r"\b(?:why (?:do|did|does|would|are) (?:you|it|this|prometheus) (?:keep|still|always|again)|"
    r"(?:i|we)(?:'ve| have)? (?:already|just) (?:pulled|ran|run|built|queued|ordered|have|had|got|made|requested)|"
    r"(?:is|was) (?:it|this|that|the (?:build|run|profile|pull))\s+(?:done|ready|finished|complete|queued|running|building)|"
    r"(?:did|have) you (?:already )?(?:queue|run|build|pull|start|finish|get to|deliver)\b|"
    r"how long (?:does|will|did) (?:a |the |this )?(?:build|run|pull|profile)|what does (?:a |the |an )?\w+ cost|"
    r"cancel (?:it|that|the (?:build|run|profile|pull))|never ?mind)", re.I)
# a question about what the product can do, with no subject after the verb
_CAPABILITY_RX = re.compile(
    r"^\W*(?:can|could|do|does|are|is|will|would) (?:you|we|prometheus|crosswalk|it) (?:(?:even|also|still|actually) )?"
    r"(?:queue|run|build|pull|create|make|generate|draft|set ?up|start|open|launch)\s+(?:a |an |the |me |us )*"
    r"(?:" + _PRODUCTS + r")s?\s*\??\s*$", re.I)

# the sentences that describe the file, not the universe
_NOTE_OPEN_RX = re.compile(
    r"^\W*(?:please\s+|and\s+|also\s+|then\s+)?(?:include|including|includes|deliver|delivered|delivery|provide|providing|"
    r"list|listing|show|showing|split|break(?:ing)? (?:it |them |the results )?(?:down|out)|report|output|export|attach|"
    r"add|give (?:me|us)|send (?:me|us|it)|return|with |as a |format|cut (?:it|them|the results) by)\b", re.I)
_NOTE_ANY_RX = re.compile(
    r"\b(?:as a (?:csv|downloadable|download|file|spreadsheet|xlsx|table|deck|one[- ]pager)|"
    r"downloadable|deliver(?:ed|able)?s? (?:as|in|with)|un-?projected|raw (?:count|number|figure)s?|"
    r"panelist counts?|(?:people|persons|count) (?:in|behind) (?:the|our|your) data|index(?:ed)? (?:vs|against)|"
    r"per row|each row|for each (?:figure|row|line|service|title)|penetration|people reached)\b", re.I)
_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
# a trailing deliverable clause glued to the order sentence by a comma
_TRAIL_NOTE_RX = re.compile(
    r",\s*(?:and\s+)?(?:(?:please\s+)?(?:include|including|with|listing|list|showing|show|split|broken (?:down|out)|"
    r"delivered?|as a (?:csv|downloadable|file))\b[^.!?\n]*)$", re.I)


def _squash(text):
    return ' '.join(str(text or '').split())


def _order_match(text):
    t = str(text or '')
    if not t.strip() or len(t) > 6000:
        return None
    try:
        for m in _ORDER_RX.finditer(t):
            if m:
                return m
    except Exception:
        return None
    return None


def product_of(text):
    """'profile' | 'subscriber_iq' | 'journey' | 'flywheel' | 'attribution' |
    'bpiq' | 'twin' | 'deck' | 'read' | '' for the product an order names."""
    m = _order_match(text)
    if not m:
        return ''
    prod = _squash(m.group('product') or m.group('product2') or '').lower()
    if not prod:
        return ''
    for fam, rx in _PRODUCT_FAMILY:
        if rx.match(prod):
            return fam
    return ''


def is_order(text):
    """True when the message is an imperative to queue, run, build, pull,
    set up, start or open a product, however many deliverable notes,
    windows, dates or questions ride behind it. A question about the
    product, a negated build, a pushback, a cancel and a capability
    question are not orders."""
    t = _squash(text)
    if not t or len(t) > 6000:
        return False
    m = _order_match(t)
    if not m:
        return False
    # the sentence the order sits in
    start = next(p for p in (m.start('verb'), m.start('verb2'), m.start('product2')) if p >= 0)
    prod_end = next(e for e in (m.end('product'), m.end('product2'), m.end('pronoun')) if e >= 0)
    sent_start = max((t.rfind(ch, 0, start) for ch in '.;:!?\n'), default=-1) + 1
    sent_end_candidates = [t.find(ch, prod_end) for ch in '.;!?\n']
    sent_end = min([e for e in sent_end_candidates if e >= 0], default=len(t))
    sentence = t[sent_start:sent_end].strip()
    lead = t[sent_start:start]
    if _NEGATED_RX.search(lead + sentence[len(lead):len(lead) + 40]) and _NEGATED_RX.search(sentence):
        return False
    if _PUSHBACK_RX.search(t) and not _ORDER_RX.match(t):
        return False
    if _CAPABILITY_RX.match(sentence):
        return False
    # a question opener on the order's own sentence ("did you queue the profile?")
    if _QUESTION_OPEN_RX.match(sentence) and not re.match(
            r"^\W*(?:can|could|would|will) (?:you|we)\b", sentence, re.I):
        return False
    if re.match(r"^\W*(?:can|could|would|will) (?:you|we)\b", sentence, re.I) and sentence.rstrip().endswith('?') \
            and not _squash(m.group('rest')).strip(' ?'):
        return False      # "can you build profiles?" asks what the product does
    # the subject slot: an order names what it is on, or points at the list before it
    rest = re.sub(r"^\W*(?:please|pls|now|today|asap|thanks?|thank you|for me|for us)\b", '', _squash(m.group('rest')), flags=re.I)
    if not rest.strip(' .!?,') and not m.group('pronoun'):
        # a name typed before the noun ("build me a Nike profile") names the subject too
        prod_start = next(p for p in (m.start('product'), m.start('product2')) if p >= 0)
        between = re.sub(r"\b(?:me|us|for|a|an|the|one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2}|another|other|new|"
                         r"fresh|full|separate|individual|single|quick|same|its|our|your|my|as|into|up|out|also|too)\b", ' ',
                         t[start:prod_start], flags=re.I)
        between = re.sub(r"\b(?:" + _VERBS + r")\b", ' ', between, count=1, flags=re.I)
        if not re.search(r"\b(?:those|these|them|each|every|all|it|the same)\b", m.group(0), re.I) \
                and not re.search(r"[A-Za-z0-9]{2,}", between):
            return False
    return True


def is_profile_order(text):
    """An order whose build flow is the Profile IQ interpret surface."""
    return is_order(text) and product_of(text) == 'profile'


def _sentences(text):
    out = []
    for s in _SENT_SPLIT_RX.split(str(text or '')):
        s = s.strip()
        if s:
            out.append(s)
    return out


def _is_note(sentence, order_sentence):
    if sentence == order_sentence:
        return False
    if _NOTE_OPEN_RX.match(sentence):
        return True
    if _NOTE_ANY_RX.search(sentence) and not _ORDER_RX.search(sentence):
        return True
    return False


def split(text):
    """(order, notes): the order as the build flow should read it and the
    deliverable notes that rode behind it, each squashed to one line.
    A message that is not an order comes back as (text, '')."""
    raw = str(text or '')
    m = _order_match(raw)
    if not m or not is_order(raw):
        return _squash(raw), ''
    sents = _sentences(raw)
    order_sent = ''
    for s in sents:
        if _ORDER_RX.search(s):
            order_sent = s
            break
    notes, keep = [], []
    for s in sents:
        if s and order_sent and _is_note(s, order_sent):
            notes.append(s)
        else:
            keep.append(s)
    # a deliverable clause glued to the order sentence by a comma
    core = ' '.join(keep)
    tm = _TRAIL_NOTE_RX.search(order_sent)
    if tm and not re.search(r"\b(?:19|20)\d{2}\b", tm.group(0)):
        clause = tm.group(0).lstrip(', ').strip()
        notes.insert(0, clause[0].upper() + clause[1:] if clause else clause)
        core = core.replace(tm.group(0), '', 1)
    return _squash(core), _squash(' '.join(notes))


def order_core(text):
    return split(text)[0]


def deliverable_notes(text):
    return split(text)[1]


# ---------------------------------------------------------------- copy
INCLUDED_LINE = 'Included with your plan.'
RAW_PHRASE = 'the count inside the 10 million US consumers we measure (the Raw column)'

_NOTE_WORDS = (
    (re.compile(r"\bpanelists?\b", re.I), 'people'),
    (re.compile(r"\bun-?projected\b", re.I), 'unprojected'),
    (re.compile(r"\bthe panel\b", re.I), 'the data'),
    (re.compile(r"\bpanel\b", re.I), 'data'),
    (re.compile(r"\bsample\b", re.I), 'data'),
    (re.compile(r"\u2013|\u2014", re.I), 'to'),
)


def _clean_notes(notes):
    n = _squash(notes)
    for rx, rep in _NOTE_WORDS:
        n = rx.sub(rep, n)
    return n


def deliverable_line(notes=''):
    """One plain paragraph on what a Profile IQ CSV carries, shaped to the
    notes the order gave (reach, splits, titles and genres, platform mix,
    the unprojected count), ending on the Raw column line."""
    n = _clean_notes(notes).lower()
    parts = []
    if not n or re.search(r"\breach\b", n):
        parts.append('reach (the projected US audience and the Avid tier)')
    if not n or re.search(r"\b(?:age|gender|demograph|split)", n):
        parts.append('the age and gender split')
    if not n or re.search(r"\b(?:titles?|genres?|shows?|series|films?|movies?|watch)", n):
        parts.append('the other titles and genres this audience watches')
    if not n or re.search(r"\b(?:platforms?|services?|streaming)", n):
        parts.append('their streaming platform mix')
    if re.search(r"\b(?:brands?|purchas|buy|shop|retail)", n):
        parts.append('the brands they buy and where they shop')
    if re.search(r"\b(?:index|indexes|indexed)\b", n):
        parts.append('an index against the US average on every row')
    if len(parts) > 1:
        body = ', '.join(parts[:-1]) + ' and ' + parts[-1]
    else:
        body = parts[0] if parts else 'reach, the demographic splits and the behaviors of the audience'
    tail = (f" Every row carries both the projected US figure and {RAW_PHRASE}, so the unprojected count for each figure "
            "is already in the file.") if (not n or re.search(r"\b(?:unprojected|raw|count|data|people behind|csv|file|download)", n)) \
        else f" Every row carries both the projected US figure and {RAW_PHRASE}."
    return (f"The Profile IQ CSV carries {body}.{tail} It lands in your Select Profile dropdown and in your inbox when "
            "it finishes.")


def build_line(name, window_label='', *, avid=True, included=True, pending_position=None, notes=''):
    """The turn an order reads when the build starts: the included line
    for a covered seat, the build line, the deliverable paragraph."""
    name = _squash(name) or 'this profile'
    win = f", {_squash(window_label)}" if window_label else ''
    tier = ', the Total Universe with its Avid Fan tier' if avid else ', the Total Universe'
    pos = f" (number {int(pending_position)} in line)" if pending_position and int(pending_position) > 1 else ''
    first = f"Profile building: {name}{win}{tier}{pos}. I will email you when it is complete."
    if included:
        first = INCLUDED_LINE + ' ' + first
    return first + '\n\n' + deliverable_line(notes)
