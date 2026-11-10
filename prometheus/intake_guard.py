"""The intake guard: an armed step only consumes an answer (2026-11-05).

A guided flow that is waiting on something (the Brand Partnership
Valuation wizard on a name or on dates, the Flywheel wizard on an entry
point, a which-ones clarifier, a twin or bulk intake on a name) consumes
the next message ONLY when that message reads as an answer to the step
it is on. A question typed while a step is armed is a new question: the
service routes it normally, the reply carries one chip back to the open
flow ('Back to the valuation', or the intake's own name), and the flow
stays pending on the server at the same step; that chip or 'back to it'
resumes it.

Jenna, 2026-11-05 (21:01 to 21:10 UTC): the valuation flow, armed at
the talent step, swallowed a Subscriber IQ question, a Power: Raising
Kanan autoplay question ('What are the campaign dates?') and a Jeopardy
discovery question ('I could not read those dates.'). "I keep asking a
question and prometheus keeps trying to force me into a brand
partnership valuation fix."

What reads as an answer to a step:

- a chip label the step offered; yes / no / cancel / skip / decline /
  accept / a numbered pick;
- dates for a dates step: a parseable range, or an attempt at one
  (month words, a day span, a relative window);
- links for a links step;
- a short name for a name step (a talent, a brand, a campaign): a few
  words, no question mark, no figure asked;
- any other reply of a dozen words or fewer with no interrogative
  opener; a description step (an entry point, a purpose) a little more.

What is a new question: a question mark; an interrogative opener (what,
which, who, how, why, where, when, is there, can you, show me ...); a
figure asked of a subject (how many, what percent, which creator, who
drove); a build order on something else (build a profile for X); or a
message well past a dozen words that carries no date range and no URL.

Step kinds: 'name' (a talent, a brand: a few words), 'short' (a campaign
or any one-line answer), 'dates', 'yes_no', 'choice', 'links', 'text' (a
description), 'fields' (a brief the legacy intakes read across turns).

Per-thread arming rides with this: a flow armed on one thread never
reads a message on another. `last_agent_is_notice` tells a stale armed
flag the widget carried across threads (its page state follows the
reader, not the thread) from a step that is really waiting.
User-facing copy is Prometheus first person. No em dashes.
"""
from __future__ import annotations

import contextvars
import re

STEP_KINDS = ('name', 'short', 'dates', 'yes_no', 'choice', 'links', 'text', 'fields')

# word budgets: a name reply, any short reply, a description, a brief
NAME_WORDS = 8
SHORT_WORDS = 12
TEXT_WORDS = 30
FIELDS_WORDS = 60

# Flow -> the chip that brings the reader back to the open step. The
# intake's own name, never a product label the reader may not know.
RESUME_CHIPS = {
    'brand_partnership': 'Back to the valuation',
    'brand_partnership_wizard': 'Back to the valuation',
    'flywheel': 'Back to the flywheel',
    'flywheel_wizard': 'Back to the flywheel',
    'digital_journey': 'Back to the journey',
    'attribution': 'Back to the campaign setup',
    'digital_twin': 'Back to the twin',
    'twin_url_list': 'Back to the twin list',
    'bulk_pull': 'Back to the list',
    'referents': 'Back to the question',
    'memory_confirm': 'Back to the question',
    'send_file': 'Back to the file',
}
DEFAULT_RESUME_CHIP = 'Back to it'

_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTH_WORD_RX = re.compile(r"\b" + _MON + r"\b\.?", re.I)
_ISO_DATE_RX = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MDY_RX = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_JOIN = r"(?:-|\u2013|\u2014|to|through|thru|until|till|and)"
_ISO_RANGE_RX = re.compile(r"\d{4}-\d{2}-\d{2}\s*" + _JOIN + r"\s*\d{4}-\d{2}-\d{2}", re.I)
_MDY_RANGE_RX = re.compile(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*" + _JOIN + r"\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?", re.I)
_NAMED_RANGE_RX = re.compile(
    _MON + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{2})?\s*" + _JOIN +
    r"\s*(?:" + _MON + r"\.?\s+)?\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{2})?", re.I)
_MONTH_SPAN_RX = re.compile(_MON + r"\.?(?:\s+(?:19|20)\d{2})?\s*" + _JOIN + r"\s*" + _MON + r"\.?(?:\s+(?:19|20)\d{2})?", re.I)
_DAY_SPAN_RX = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s*(?:-|\u2013|to|through|thru)\s*\d{1,2}(?:st|nd|rd|th)?\b")
_RELATIVE_WINDOW_RX = re.compile(
    r"\b(?:last|past|trailing|next|previous|prior)\s+(?:\d{1,3}|few|couple of|two|three|four|five|six|seven|eight|nine|ten|twelve|"
    r"eighteen|twenty four)\s+(?:days?|weeks?|months?|quarters?|years?)\b|"
    r"\b(?:this|last|next|previous|prior|current)\s+(?:week|month|quarter|year|fall|spring|summer|winter|holiday season)\b|"
    r"\bq[1-4]\b|\bytd\b|\bt12\b|\b(?:calendar|fiscal|cy|fy)\s*(?:19|20)?\d{2}\b|\byear to date\b|\btrailing year\b", re.I)
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
_URL_RX = re.compile(r"https?://[^\s,;<>\"]+", re.I)

# chip-like control words: these always answer the step that is open
_CONTROL_RX = re.compile(
    r"^\W*(?:(?:yes|yeah|yep|ok|okay|sure)[,.!]?\s+)?(?:please\s+)?"
    r"(?:yes|yeah|yep|y|sure|ok|okay|fine|correct|right|no|nope|nah|n|cancel(?: (?:that|it|this))?|never ?mind|not now|skip(?: (?:that|it|this))?|"
    r"decline|accept(?:\s+\$?\s*[\d,]+)?|approve[d]?|confirm(?:ed)?|go ahead|do it|run it|run that|run this|proceed|ship it|"
    r"both|either|neither|none|all of them|the first(?: one)?|the second(?: one)?|the third(?: one)?|the last(?: one)?|first|second|third|"
    r"\d{1,2}|option \d{1,2}|other|another|different|something else|thanks?|thank you)\s*(?:please|thanks?)?\W*$", re.I)

# lead-ins a person types before the question itself
_LEAD_RX = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok|okay|so|and|also|but|now|then|actually|quick question|one more|another one|question|q|prometheus)[,:.!\s]+)+", re.I)
_WH_OPEN_RX = re.compile(r"^(?:what|which|who|whom|whose|why|where|when|how)\b", re.I)
_AUX_WORDS = (r"(?:is|are|was|were|do|does|did|can|could|would|should|will|shall|have|has|had|am|may|might)")
_AUX_PRONOUN_RX = re.compile(
    r"^" + _AUX_WORDS + r"\s+(?:you|we|i|it|they|he|she|there|this|that|these|those|the|a|an|any|anyone|anybody|someone|somebody|"
    r"my|our|your|his|her|their|its|all|some|more|most|many|people|viewers|users|fans|subscribers|prometheus|crosswalk|\d)\b", re.I)
_AUX_OPEN_RX = re.compile(r"^(?:is|are|was|were|do|does|did|can|could|would|should|have|has|had)\b", re.I)
_ASKING_RX = re.compile(
    r"^(?:show me|tell me|give me|get me|find me|send me|pull up|look up|is there|are there|any idea|i need|i want|i'?d like|"
    r"i would like|i'?m looking|we need|we want|we'?re looking|help me|explain|describe|compare|rank|estimate|calculate|"
    r"figure out|break down|identify|recommend|suggest|please help|remind me|walk me through)\b", re.I)
_FIGURE_ASK_RX = re.compile(
    r"\b(?:how many|how much|how often|how long|what percent(?:age)?|what share|what portion|what fraction|what %|percent(?:age)? of|"
    r"share of|% of|which (?:one|ones|of|creator|creators|influencer|influencers|show|shows|title|titles|platform|platforms|brand|brands|"
    r"campaign|campaigns|audience|viewers|episode|episodes|season|seasons)|"
    r"how did|what did|what was|what is|what are|what'?s the|did they|do they|of those(?: who)?|last touch ?point|touch ?points?)\b", re.I)
_BUILD_ORDER_RX = re.compile(
    r"\b(?:build|create|pull|run|make|put together|set up|queue|draft|generate|spin up)\b[^.?!]{0,40}"
    r"\b(?:an?\s+|the\s+|me\s+an?\s+|me\s+the\s+)?(?:audience|universe|profile|segment|cohort|twin|journey|flywheel|deck|report|read|"
    r"valuation|study|tracking|pull)s?\b[^.?!]{0,10}\b(?:of|for|on|about)\b", re.I)
# an order that names a product ('Pull Subscriber IQ for Dark Matter on Apple TV+', 'start tracking the Nike
# campaign'): the words belong to that product's lane, never to the step that happens to be open
_PRODUCT_ORDER_RX = re.compile(
    r"\b(?:build|create|pull|run|make|set up|queue|draft|generate|spin up|start|open|value|track|launch)\b[^.?!]{0,30}"
    r"\b(?:subscriber iq|profile iq|digital journey|journey iq|flywheel(?: iq)?|attribution iq|trends iq|impact iq|analysis iq|"
    r"brand partnership(?: iq| valuation)?|digital twins?(?: iq)?|focus group|greenlight|launch lab|pilot test(?:ing)?|"
    r"the (?:valuation|journey|flywheel|twin|campaign|tracking|study|deck))\b", re.I)
# a title or a name typed as one: every word capitalized, a connective or a number ('What We Do in the Shadows',
# 'Who Wants to Be a Millionaire', 'Gunna x Nike'); a wh-opener here is the title's, not a question's
_TITLE_SMALL = {'a', 'an', 'the', 'of', 'in', 'on', 'to', 'for', 'and', 'or', 'at', 'by', 'with', 'x', 'vs', 'vs.', '&', 'feat', 'feat.',
                'ft', 'ft.', 'from', 'as', 'is', 'it', 'be', 'de', 'la', 'le', 'du', 'del', 'y', 'e', 'into', 'over', 'up', 'out', 'off'}
# a sentence that names someone and asks for direction mid-sentence (2026-10-12, Debi)
_INLINE_ASK_RX = re.compile(
    r"\b(?:help (?:me|us) (?:identify|find|figure out|understand|see|decide)|"
    r"(?:who|where|what|which) (?:should|could|would) (?:he|she|they|we|i)\b|"
    r"is (?:thinking about|looking for|trying to find|exploring|considering)\b)", re.I)
# field-shaped lines are an intake's own answers, never a new ask
_FIELD_SHAPE_RX = re.compile(
    r"https?://|^\s*\d+[.)]\s|^\s*(?:platform|conversion|title|category|brand|partner|window|campaign|urls?|talent|dates?|"
    r"ecosystem|entry(?: point)?|subject|purpose|name)\s*[:=-]", re.I | re.M)

_RESUME_RX = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|yes|sure|right|and)[,.!]?\s+)?(?:please\s+)?(?:"
    r"(?:let'?s |lets |can we |could we |now )?(?:go )?back to (?:it|that|this|the \w+(?: \w+){0,2}|where we were|where we left off)|"
    r"(?:let'?s |lets )?(?:resume|continue|pick up|carry on|finish|keep going)(?: (?:with |on ))?(?: ?(?:it|that|this|the \w+(?: \w+){0,2}|where we (?:were|left off)))?|"
    r"where were we|where did we leave off|take me back(?: to (?:it|the \w+(?: \w+){0,2}))?|return to (?:it|the \w+(?: \w+){0,2})"
    r")\W*$", re.I)


def _norm(text):
    return ' '.join(str(text or '').split())


def _fold(text):
    return re.sub(r"[^a-z0-9$]+", ' ', str(text or '').lower()).strip()


def _words(text):
    return len(_norm(text).split())


def strip_lead(text):
    """The message without a conversational lead-in ('ok so', 'quick question:')."""
    t = _norm(text)
    m = _LEAD_RX.match(t)
    if m and m.end() < len(t):
        return t[m.end():].strip()
    return t


def is_control(text):
    """yes / no / cancel / skip / decline / accept / a numbered pick / a bare 'other'."""
    t = _norm(text)
    return bool(t) and bool(_CONTROL_RX.match(t))


def is_chip(text, chips=None):
    """The message is one of the chips the step offered (case and punctuation aside)."""
    f = _fold(text)
    if not f:
        return False
    for c in chips or ():
        if isinstance(c, dict):
            c = c.get('send') or c.get('label') or ''
        if f == _fold(c):
            return True
    return False


def has_interrogative_opener(text):
    """A wh-word, an asking verb, or an auxiliary that opens a question.
    An auxiliary needs a pronoun or determiner after it, or a sentence
    of four words or more ('Do Revenge' and 'Is This It' are titles;
    'did dark matter get renewed' is a question)."""
    t = strip_lead(text)
    if not t:
        return False
    if _WH_OPEN_RX.match(t) or _ASKING_RX.match(t):
        return True
    if _AUX_PRONOUN_RX.match(t):
        return True
    if _AUX_OPEN_RX.match(t) and _words(t) >= 4:
        return True
    if re.match(r"^will\s+(?:you|we|i|it|they|he|she|there|this|that|the|a|an|any|anyone|people|viewers|users|fans|subscribers)\b", t, re.I):
        return True
    return False


def asks_a_figure(text):
    """The message asks for a count, a share, a which-one or a who-did ('how many ... autoplayed', 'what percent completed')."""
    return bool(_FIGURE_ASK_RX.search(_norm(text)))


def is_build_order(text):
    """An order to build or run something on a subject ('build a profile
    for Paw Patrol'), or an order that names a product ('Pull Subscriber
    IQ for Dark Matter on Apple TV+')."""
    t = _norm(text)
    if _BUILD_ORDER_RX.search(t) or _PRODUCT_ORDER_RX.search(t):
        return True
    try:
        from . import orders as _orders
        return bool(_orders.is_order(t))      # the shared detector (Grace Yin 2026-11-10)
    except Exception:
        return False


def looks_like_title(text):
    """A name or title typed as one: two words or more, no question mark,
    every word capitalized, a connective, a number or a symbol. A
    question is typed in sentence case; a title's own wh-word ('What We
    Do in the Shadows') is not an interrogative opener."""
    t = _norm(text)
    if not t or '?' in t:
        return False
    words = t.split()
    if len(words) < 2:
        return False
    caps = 0
    for w in words:
        bare = w.strip("()[]\"'.,:;!")
        if not bare:
            continue
        if bare.lower() in _TITLE_SMALL or not bare[0].isalpha():
            continue
        if not bare[0].isupper():
            return False
        caps += 1
    return caps >= 1


def has_date_range(text):
    """A parseable range: two dates, two month names, or a relative window."""
    t = _norm(text)
    if not t:
        return False
    return bool(_ISO_RANGE_RX.search(t) or _MDY_RANGE_RX.search(t) or _NAMED_RANGE_RX.search(t)
                or _MONTH_SPAN_RX.search(t) or _RELATIVE_WINDOW_RX.search(t))


def is_dates_attempt(text):
    """The reader tried to give dates: a range, a month word, a day span,
    a date, a year, a relative window. The wizard's 'I could not read
    those dates' line is for these; a reply that never tried is re-asked
    plainly."""
    t = _norm(text)
    if not t or is_control(t):
        return False
    if has_date_range(t):
        return True
    if _ISO_DATE_RX.search(t) or _MDY_RX.search(t) or _MONTH_WORD_RX.search(t) or _DAY_SPAN_RX.search(t):
        return True
    if _YEAR_RX.search(t) and _words(t) <= SHORT_WORDS:
        return True
    return False


def is_question_shaped(text):
    """A question mark, an interrogative opener, a figure asked, a build
    order on something else, or an ask folded into a sentence."""
    t = _norm(text)
    if not t or is_control(t) or _FIELD_SHAPE_RX.search(t):
        return False
    return bool('?' in t or has_interrogative_opener(t) or asks_a_figure(t) or is_build_order(t)
                or _INLINE_ASK_RX.search(t))


def is_new_ask_for_brief(text):
    """The question test for the legacy intakes that read a brief across
    turns (a journey, a campaign, a twin): a question mark, a figure
    asked, an order on something else, or a question-shaped message of
    eight words or more. A short wh-opener with none of those ('what
    watchers do after', 'how they came to watch') is the brief's own
    answer to a direction question and stays with the intake."""
    t = _norm(text)
    if not t or is_control(t) or _FIELD_SHAPE_RX.search(t):
        return False
    if not is_question_shaped(t):
        return False
    return bool('?' in t or _words(t) >= 8 or asks_a_figure(t) or is_build_order(t))


def looks_like_new_question(text, long_words=SHORT_WORDS):
    """True when a message typed under an armed step is a question or an
    order of its own, not the answer the step is waiting for. A message
    past `long_words` words with no date range and no URL is a new
    question too (an answer to a step is short)."""
    t = _norm(text)
    if not t or is_control(t) or _FIELD_SHAPE_RX.search(t):
        return False
    if is_question_shaped(t):
        return True
    if _words(t) > int(long_words) and not has_date_range(t) and not _URL_RX.search(t):
        return True
    return False


def is_answer_to_step(text, step_kind, chips=None, answer_patterns=None):
    """True when `text` reads as the answer to an armed step of `step_kind`
    (one of STEP_KINDS). `chips` are the labels the step offered;
    `answer_patterns` are the step's own regexes (an 'I'll paste links'
    shape, an 'accept' shape) that always count as answers."""
    t = _norm(text)
    if not t:
        return False
    if is_chip(t, chips) or is_control(t):
        return True
    for p in answer_patterns or ():
        try:
            rx = re.compile(p, re.I) if isinstance(p, str) else p
            if rx.search(t):
                return True
        except Exception:
            continue
    kind = str(step_kind or 'text').strip().lower()
    if kind == 'dates' and is_dates_attempt(t) and '?' not in t and not has_interrogative_opener(t):
        return True
    if kind == 'links' and _URL_RX.search(t):
        return True
    budget = {'name': NAME_WORDS, 'short': SHORT_WORDS, 'text': TEXT_WORDS, 'fields': FIELDS_WORDS}.get(kind, SHORT_WORDS)
    if kind in ('name', 'short') and _words(t) <= budget and looks_like_title(t) and not is_build_order(t) \
            and not asks_a_figure(t):
        return True      # 'What We Do in the Shadows' is the name, not a question
    if looks_like_new_question(t, long_words=budget):
        return False
    return _words(t) <= budget


# ----------------------------------------------------------- per thread
_NOTICE_RX = re.compile(
    r"^\s*(?:your\b|it'?s running\b|it is running\b|on it\b|done\b|working on it\b|the (?:read|deck|build|journey|flywheel|twin|"
    r"valuation|profile|study|campaign) (?:is|are) (?:ready|live|done|complete)\b|that (?:valuation|journey|flywheel|twin|read) "
    r"is already running\b)", re.I)
_JOB_META_KEYS = ('read_job_id', 'deck_job_id', 'bpiq_job_id', 'jiq_job_id', 'fw_job_id', 'aiq_job_id', 'twin_job_id',
                  'twin_key', 'bulk_id', 'job_id')


def last_agent_turn(history):
    for t in reversed(list(history or [])):
        if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant'):
            return t
    return None


def last_agent_is_notice(history):
    """True when the last thing Prometheus said in this thread was a job
    notice or a running line ('Your twin is ready', 'It's running', 'On
    it'), never a step's question. A collect flag the widget still
    carries over such a turn came from another thread or an earlier
    conversation: it does not arm this one."""
    turn = last_agent_turn(history)
    if not turn:
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if any(meta.get(k) or turn.get(k) for k in _JOB_META_KEYS):
        return True
    txt = str(turn.get('text') or turn.get('content') or '').strip()
    return bool(txt) and bool(_NOTICE_RX.match(txt))


def armed_flag_applies(history):
    """A collect flag on the body ('fw_inputs', 'twin_inputs', ...) arms
    the turn only when the thread itself could be waiting: no agent turn
    yet, or a last agent turn that is not a notice. The widget keeps
    these flags in page state, so they follow the reader across threads
    (Jenna, 2026-11-05)."""
    return not last_agent_is_notice(history)


def same_thread(doc_thread_id, thread_id):
    """A stored draft belongs to this thread when either side is unknown
    or the ids agree. A draft stamped with another thread's id never
    arms this one."""
    a = str(doc_thread_id or '').strip()
    b = str(thread_id or '').strip()
    return not a or not b or a == b


# ----------------------------------------------------------------- resume
def chip_for(flow):
    return RESUME_CHIPS.get(str(flow or '').strip().lower()) or DEFAULT_RESUME_CHIP


def is_resume(text):
    """'Back to the valuation', 'back to it', 'where were we', 'resume'."""
    t = _norm(text)
    if not t or _words(t) > 9:
        return False
    if is_resume_chip(t):
        return True
    return bool(_RESUME_RX.match(t))


def is_resume_chip(text):
    f = _fold(text)
    return bool(f) and any(f == _fold(c) for c in set(RESUME_CHIPS.values()) | {DEFAULT_RESUME_CHIP})


def resume_flow(text, history=None):
    """The flow a resume phrase points at: the chip's own flow, else the
    most recent 'Back to ...' chip in the thread (an agent turn's
    options), else ''."""
    f = _fold(text)
    for flow, chip in RESUME_CHIPS.items():
        if f == _fold(chip) and flow in ('brand_partnership', 'flywheel', 'digital_journey', 'attribution',
                                         'digital_twin', 'twin_url_list', 'bulk_pull', 'referents', 'memory_confirm', 'send_file'):
            return flow
    m = re.search(r"back to the (\w+(?: \w+){0,2})", f)
    if m:
        want = _fold(m.group(1))
        for flow, chip in RESUME_CHIPS.items():
            if _fold(chip).endswith(want) and flow in ('brand_partnership', 'flywheel', 'digital_journey', 'attribution',
                                                       'digital_twin', 'twin_url_list', 'bulk_pull', 'referents', 'memory_confirm', 'send_file'):
                return flow
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        if meta.get('intake_resume'):
            return str(meta['intake_resume'])
        labels = []
        for o in (meta.get('options') or turn.get('options') or turn.get('followups') or []):
            labels.append(str((o.get('send') or o.get('label')) if isinstance(o, dict) else o))
        for lbl in labels:
            fl = _fold(lbl)
            for flow, chip in RESUME_CHIPS.items():
                if fl == _fold(chip) and flow in ('brand_partnership', 'flywheel', 'digital_journey', 'attribution',
                                                  'digital_twin', 'twin_url_list', 'bulk_pull', 'referents', 'memory_confirm', 'send_file'):
                    return flow
    return ''


# The mark: a lane that steps aside for a new question says so here, and
# the reply that goes out carries the way back. One per ask; reset at the
# start of every service.ask so nothing leaks between requests.
_PENDING = contextvars.ContextVar('pm_intake_pending_resume', default=None)


def reset_resume():
    _PENDING.set(None)


def mark_resume(flow, chip=None, step=''):
    """An armed `flow` let this message through as a new question; its
    reply gets `chip` (default: the flow's own) and the flow stays
    pending at `step`."""
    hit = {'flow': str(flow or ''), 'chip': str(chip or chip_for(flow)), 'step': str(step or '')}
    _PENDING.set(hit)
    return hit


def pending_resume():
    return _PENDING.get()


def attach_resume_chip(env, hit=None):
    """Add the one trailing resume chip to an envelope (its options and
    its raw followups). Idempotent. Returns the envelope."""
    hit = hit or pending_resume()
    if not hit or not isinstance(env, dict):
        return env
    chip = str(hit.get('chip') or DEFAULT_RESUME_CHIP)
    try:
        opts = env.get('options')
        if not isinstance(opts, list):
            opts = []
            env['options'] = opts
        have = {_fold((o.get('send') or o.get('label')) if isinstance(o, dict) else o) for o in opts}
        if _fold(chip) not in have:
            opts.append({'label': chip, 'send': chip})
        raw = env.get('raw') if isinstance(env.get('raw'), dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else None)
        if raw is not None:
            fu = raw.get('followups')
            if not isinstance(fu, list):
                fu = []
                raw['followups'] = fu
            if _fold(chip) not in {_fold(x) for x in fu}:
                fu.append(chip)
            raw['intake_resume'] = hit.get('flow') or ''
    except Exception:
        pass
    return env


__all__ = ['STEP_KINDS', 'RESUME_CHIPS', 'DEFAULT_RESUME_CHIP', 'is_answer_to_step', 'looks_like_new_question', 'is_question_shaped',
           'is_dates_attempt', 'has_date_range', 'has_interrogative_opener', 'asks_a_figure', 'is_build_order', 'looks_like_title',
           'is_new_ask_for_brief',
           'is_control', 'is_chip', 'strip_lead', 'chip_for', 'is_resume', 'is_resume_chip', 'resume_flow', 'reset_resume',
           'mark_resume', 'pending_resume', 'attach_resume_chip', 'last_agent_turn', 'last_agent_is_notice', 'armed_flag_applies',
           'same_thread']
