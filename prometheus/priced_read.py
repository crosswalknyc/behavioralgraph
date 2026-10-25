"""Confirm-before-numbers for new data (Jenna 2026-10-21).

Jay Patel, Emmet Riley and Debi Pomerantz were getting incrementality,
path tables, ranked lists, where-watched and sample sizes at the
metered chat rate. Jenna: quote first, they accept, then answer.

The line is drawn on WORK, not on whether a profile exists (Jenna,
2026-10-21, Liz's QSR ask on the open Paramount+ profile: "this
should have charged her $100"):

  FREE (metered only)   a lookup of what is already on the screen or
                        in the thread: a single figure or definition,
                        which-row-is-this, how-is-this-calculated, a
                        chip-sized restatement, the deterministic
                        lanes, and a question about the previous
                        answer (a continuation).
  CUSTOM ASK            any put-together read, on an open profile or
                        not: a ranked list, a table, a cross of two or
                        more things, a top N, what they watched next,
                        a cohort within the audience, anything that
                        reasons over the rows into a new artifact.
                        $100 for the first custom ask on a subject in
                        a thread, $50 for each follow-up on the same
                        subject (Jenna, same day).
  SIZING                $100 (the derived-read row).
  VIEWERSHIP            $500 per year of window (its own row).
  BREAKDOWN             $100 (a delivered figure split by a dimension).

One offer shape everywhere (Jenna, verbatim: "just say something like
pulling this data will cost $500 accept or decline with chips"): one
sentence on what they get, then "Pulling this data will cost $N.",
then exactly two chips, Accept and Decline. Never "The path for X",
never "I will not answer until you accept". Crosswalk staff see the
same offer and are not charged; their Accept chip says so.

A company wallet is shared by every seat on the company: a seat on a
paying company is charged for every priced product unless the seat is
unlimited or the company is Crosswalk. There is no "free because the
profile exists" path.
"""
from __future__ import annotations

import re

# ----------------------------------------------------------------- the shape
# The one offer shape (Jenna 2026-10-21, twice: "just say something like
# pulling this data will cost $500 accept or decline with chips" and then
# "it should not say 'I will not answer until you accept', just do the
# chips approve | cancel"). One sentence on what the reader gets, then the
# cost line, then two chips. Never a refusal, never 'That is a $N read',
# never 'It runs $N'.
COST_LINE = "Pulling this data will cost {price}."
OFFER_LINE = COST_LINE
APPROVE_CHIP = "Approve"
CANCEL_CHIP = "Cancel"
ACCEPT_CHIP, DECLINE_CHIP = APPROVE_CHIP, CANCEL_CHIP   # historic names, same chips
STAFF_ACCEPT_CHIP = "Approve ({price}, not billed to a staff account)"
# Kept under its historic name for callers that import it; the offer text
# no longer carries a refusal sentence.
CONFIRM = ""

CUSTOM_FAMILY = frozenset(('report', 'custom', 'overlap', 'path', 'list', 'cross'))
# Never priced: a lookup of carriage (where a named title is watched, the
# content map, the library, one research call) is the free where-watched
# lane in prometheus/service.py, ahead of every quote. classify() returns
# None for it so neither service_turn nor work_gate can offer it.
NEVER_PRICED = frozenset(('where_watched',))
CUSTOM_KEY, CUSTOM_DEFAULT = 'panel_report', 100.0
FOLLOWUP_KEY, FOLLOWUP_DEFAULT = 'panel_report_followup', 50.0
CUSTOM_PULL_TYPE, FOLLOWUP_PULL_TYPE = 'Panel Report', 'Panel Report Follow-up'

_ACCEPT_WORDS_RX = re.compile(
    r"^\W*(?:approve\b.*|accept\b.*|yes|yeah|yep|ok(?:ay)?|sure|run it|go ahead|do it|charge (?:it|me)|"
    r"run the full read\b.*|run the breakdown\b.*|yes,? run it again|run it again)\W*$", re.I)
_DECLINE_WORDS_RX = re.compile(
    r"^\W*(?:cancel(?: (?:that|it))?|decline|never ?mind|no(?: thanks?| thank you)?|not now|skip|pass)\W*$", re.I)

_TALK_RX = re.compile(
    r"\b(?:what does (?:this|that|it|the (?:number|figure|percent)).{0,24}mean|"
    r"craziest insights|what does (?:this|the) data tell|"
    r"where (?:do|can) i (?:find|see|get)|"
    r"are you still working|still (?:working|running)|"
    r"rewrite|script for|"
    r"is (?:it |that )?(?:on|in) (?:the |that |this )?(?:list|table|csv)|"
    r"how do you know)\b",
    re.I)
_LIST_RX = re.compile(
    r"\b(?:top\s+\d+|ranked?\s+lists?|who are the (?:top|biggest)|"
    r"list (?:of )?(?:the )?(?:top|biggest))\b.{0,80}"
    r"\b(?:creators?|influencers?|youtubers?|titles?|shows?|films?|"
    r"webtoons?|books?)\b",
    re.I)
_INC_RX = re.compile(
    r"\b(?:how\s+incremental(?:ly)?|incrementality|"
    r"incremental\s+(?:to|for|reach|lift|viewers?))\b",
    re.I)
# An offer turn in a thread (the current shape and the retired ones) is
# never mistaken for a delivered read when a frame is checked.
_OFFER_COPY_RX = re.compile(
    r"Pulling this data will cost|I will not answer until you accept|It runs \$|"
    r"Run the full read|Run the breakdown",
    re.I)
# Put-together work on the rows: a ranked list, a table, a cross, a top
# N, what they did next, a cohort within the audience.
_CUSTOM_WORK_RX = re.compile(
    r"\b(?:top\s+(?:\d+|three|five|ten|twenty|fifteen|twenty-five)\b|rank(?:ed|ing)?\s+(?:list|the|them|by|order)|"
    r"as\s+a\s+table|in\s+a\s+table|(?:results|numbers|figures|data)\s+by\s+\w+\s+as\s+a\s+table|"
    r"over-?\s?index|under-?\s?index|index(?:es|ed)?\s+(?:on|against|vs\.?|versus|compared|relative)|"
    r"compared?\s+(?:with|to|against)|comparison\s+(?:with|to|of)|"
    r"overlap|also\s+(?:watch|stream|read|listen|buy|bought|subscribe|use|pay)|"
    r"watch(?:ed)?\s+next|(?:did|do)\s+they\s+(?:watch|stream|buy|read)\s+(?:next|after|also)|"
    r"other\s+(?:titles|shows|series|films|movies|brands|platforms|services|podcasts)\s+(?:watched|they\s+watch(?:ed)?|streamed|bought)|"
    r"who\s+also\b|(?:people|viewers|fans|users|customers|subscribers)\s+(?:who|that)\s+(?:watch|watched|drink|buy|bought|read|listen|play|stream|subscribe)\b|"
    r"(?:cohort|segment|slice)\s+(?:of|within)|within\s+(?:the|this)\s+(?:audience|profile)|"
    r"\bx\b|cross(?:ed|ing)?\s+(?:with|against)|share\s+who\s+(?:subscribe|pay|watch|buy|use)|"
    r"which\s+(?:other\s+)?(?:streaming\s+)?(?:services|platforms|brands)\s+do\s+they|"
    r"incremental|incrementality|(?:what|which)\s+(?:brands|titles|shows|services|platforms)\s+do\s+.{1,40}\s+(?:over-?index|index|watch|use|buy)|"
    # a market read put together across retailers, platforms or weeks: which one is winning, the latest
    # period against the one before, a share that shifted, an outlook (Scott Carlin 2026-10-24, run unpriced on an open file)
    r"which\s+(?:retailer|brand|platform|service|title|show|network|app|store|chain)\s+is\s+(?:increasingly\s+)?(?:winning|gaining|losing|taking|leading)|"
    r"(?:latest|last|past|most\s+recent)\s+(?:\d+|two|three|four|six|eight|twelve)\s+weeks?\s+(?:with|against|vs\.?|versus|to)\s+the\s+(?:preceding|prior|previous|earlier)|"
    r"(?:purchase|consideration|repeat|market|wallet|visit|category)\s+share\s+(?:is\s+)?shift(?:s|ed|ing)?|share\s+(?:is\s+)?shift(?:s|ed|ing)|"
    r"directional\s+outlook|outlook\s+for\s+the\s+next|(?:examine|assess|evaluate)\s+(?:observed\s+)?\w[\w-]*\s+(?:shopping|purchases?|purchasing|viewing|behavior|spend))\b",
    re.I)
# A single figure already on the screen: free.
_SINGLE_FIGURE_RX = re.compile(
    r"^\W*(?:what(?:'s| is| was)|how (?:big|many|much|high|low) (?:is|was|are)?)\s+(?:the\s+)?"
    r"(?P<name>[A-Za-z0-9&+'. -]{2,48}?)\s+(?:penetration|index|share|reach|count|number|figure|rate|percentage|percent|value|row)\b"
    r"(?:\s+(?:here|on\s+this\s+(?:profile|page|file)|in\s+this\s+(?:profile|file|view)|on\s+screen|in\s+the\s+file))?\W*$",
    re.I)
_OFFER_COPY_RX = re.compile(
    r"Pulling this data will cost|I will not answer until you accept|It runs \$|"
    r"That is a \$\d|Run the full read|Run the breakdown", re.I)


def is_talk(text):
    """Insights, meaning, file-find, in-flight: metered, never a quote."""
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_TALK_RX.search(t))


def is_ranked_list(text):
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_LIST_RX.search(t))


def is_single_figure(text):
    """'what is the McDonald's penetration here': a lookup of one figure
    on the screen, free."""
    t = ' '.join(str(text or '').split())
    return bool(t) and bool(_SINGLE_FIGURE_RX.match(t))


def custom_work(text):
    """True when the ask puts the rows together into a new artifact:
    the custom-ask line (work, not whether a profile exists)."""
    t = ' '.join(str(text or '').split())
    if not t or is_talk(t) or is_single_figure(t):
        return False
    return bool(_CUSTOM_WORK_RX.search(t)) or is_ranked_list(t) or bool(_INC_RX.search(t))


def is_accept(text):
    return bool(_ACCEPT_WORDS_RX.match(str(text or '')))


def is_decline(text):
    return bool(_DECLINE_WORDS_RX.match(str(text or '')))


def frame_of(kind, text):
    t = ' '.join(str(text or '').lower().split())
    if kind in ('overlap', 'path') and _INC_RX.search(t):
        target = 'prime video' if 'prime' in t else 'generic'
        return 'incrementality|' + target
    if kind == 'list':
        if 'youtube' in t:
            return 'list|youtube'
        return 'list|generic'
    if kind in ('path', 'sizing', 'viewership', 'cross', 'custom'):
        return kind
    return str(kind or 'report')


def _looks_delivered(agent_text):
    t = str(agent_text or '')
    if not t or _OFFER_COPY_RX.search(t):
        return False
    return len(t) > 80 and bool(re.search(r'\d', t))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def _strip_audience(s):
    return re.sub(r"\s+(?:viewers|fans|readers|listeners|players|watchers|users|customers|buyers|"
                  r"shoppers|subscribers|drinkers|audience)\s*$", '', str(s or '').strip(), flags=re.I)


def _same_subject(a, b):
    na, nb = _norm(_strip_audience(a)), _norm(_strip_audience(b))
    return bool(na) and bool(nb) and (na == nb or (len(na) >= 5 and (na in nb or nb in na)))


def prior_custom_on_subject(history, subject):
    """True when this thread already carried a custom ask on the same
    subject that the reader accepted (the offer turn followed by an
    accept, or a paid read recorded on a turn): the next one is the
    $50 follow-up."""
    if not subject:
        return False
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for i, t in enumerate(turns):
        role = str(t.get('role') or '')
        if role not in ('agent', 'assistant'):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        paid = meta.get('priced_paid') or t.get('priced_paid')
        if isinstance(paid, dict) and _same_subject(paid.get('subject'), subject):
            return True
        # a delivered priced read on this subject (the read job's turn)
        if (meta.get('read_job_id') or meta.get('panel_charge')) and _same_subject(meta.get('subject'), subject):
            return True
        off = meta.get('panel_offer') or t.get('panel_offer')
        if isinstance(off, dict) and _same_subject(off.get('subject'), subject):
            # the offer accepted on the next user turn
            for nxt in turns[i + 1:i + 3]:
                if str(nxt.get('role') or '') == 'user':
                    if is_accept(nxt.get('text') or nxt.get('content') or ''):
                        return True
                    break
    return False


def same_frame(history, frame):
    """True when this thread already delivered a paid (or previously
    answered) read on the same frame."""
    if not frame:
        return False
    last_user = ''
    for t in history or []:
        if not isinstance(t, dict):
            continue
        role = str(t.get('role') or '')
        if role == 'user':
            last_user = str(t.get('text') or t.get('content') or '')
            continue
        if role not in ('agent', 'assistant') or not last_user:
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        paid = meta.get('priced_paid') or t.get('priced_paid')
        if isinstance(paid, dict) and str(paid.get('frame') or '') == frame:
            return True
        prev = classify(last_user, history=None)
        if prev and prev.get('frame') == frame and _looks_delivered(
                t.get('text') or t.get('content') or ''):
            return True
    return False


def _cross_ask(text):
    try:
        from . import cross_sizing as _cx
        return bool(_cx.parse(text))
    except Exception:
        return False


def _panel_self(text, ctx=None):
    try:
        from . import sizing_lane as _sz
        if not _sz.wants_sizing(text):
            return False
        subs = [s for s in _sz.subjects(text, ctx) if not _sz.is_panel_noun(s)
                and s != '__page__']
        return not subs
    except Exception:
        return False


def sizes_open_page(text, ctx=None):
    """True when a sizing ask points at the audience already on the
    screen ('how big is this audience', 'how many people is that', a
    size ask that names nothing while a profile is open). That figure
    is on the page; reading it back is a lookup, free and metered, under
    the work line Jenna set on 2026-10-21 (a lookup of what is on screen
    is free; a put-together read is the custom ask). A cross, or a
    subject that is not the open page, stays priced in the sizing lane."""
    try:
        from . import sizing_lane as _sz
        if not _sz.wants_sizing(text):
            return False
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        except Exception:
            page = ''
        if not page:
            return False
        subs = [s for s in _sz.subjects(text, ctx) if s and not _sz.is_panel_noun(s)]
        return bool(subs) and all(s == '__page__' or s.strip().lower() == page.lower() for s in subs)
    except Exception:
        return False


def _sizing_subject(text, ctx=None):
    try:
        from . import sizing_lane as _sz
        for s in _sz.subjects(text, ctx):
            if s and s != '__page__' and not _sz.is_panel_noun(s):
                return _sz.strip_audience_nouns(s) or s
        page = ''
        try:
            page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        except Exception:
            page = ''
        return page or 'that audience'
    except Exception:
        return 'that audience'


_PRODUCT_ORDER_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+|would you\s+|i need\s+|i want\s+|i'd like\s+|let's\s+)?"
    r"(?:run|build|pull|create|queue|start|launch|generate|make|kick off|spin up|refresh|re-?run|cut|draft|put together)\b"
    r"[^.;\n]{0,80}\b(?:profiles?|profile iq|subscriber iq|digital journey|journey|flywheel|brand partnership|"
    r"valuation|attribution|campaign|avid|cuts?|universe|deck|one-?pager|slides?|presentation|workbook)\b|"
    r"^\W*(?:please\s+)?cut\b[^.;\n]{0,80}\bby\b", re.I)


def is_product_order(text):
    """A build order for a priced product (a profile, a cut, a journey,
    a valuation, an attribution campaign, a deck): never a custom-ask
    quote. The product's own lane prices and confirms it."""
    t = str(text or '')
    if _PRODUCT_ORDER_RX.search(t):
        return True
    try:
        from . import guards as _g
        return bool(_g.is_build_order(t))
    except Exception:
        return False


def is_where_watched(text, history=None, ctx=None):
    """True when the words ask where a named title is watched (the free
    where-watched lane's own parse). Never priced."""
    try:
        from . import where_watched as _ww
        return bool(_ww.parse(text, history, page=_page_name(ctx)))
    except Exception:
        return False


def _page_name(ctx):
    try:
        return str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def bound_subject(text, history=None, ctx=None, fallback='', reading=None):
    """The one subject every offer shows (I1): the binder's label when
    the words name one, else the fallback cleaned, else the page. The
    Reader's reading, when the caller has one, feeds the binder."""
    page = _page_name(ctx)
    try:
        from . import subject_rules as _sr
        s = _sr.bind(text, history, page, reading=reading) if reading else _sr.bind(text, history, page)
        if s and s.source == 'words':
            verb = str(s.qualifiers.get('verb') or '')
            plat = str(s.qualifiers.get('platform') or '')
            if s.kind == 'title' and verb and not re.search(r"\b" + re.escape(verb) + r"\b", s.display, re.I):
                return f"{s.display} {verb}" + (f" on {plat}" if plat else '')
            return s.label()
        # The thread's subject beats the open page (Jay's "what did the
        # BookTok exposed viewers watch next" with Prime Video open is on
        # The Love Hypothesis, never on Prime Video). A cohort the words
        # name rides the lead.
        if s and s.source not in ('page', 'none', '') and s.name:
            coh = reading_cohort(text, page)
            return coh or s.label()
        coh = reading_cohort(text, page)
        if coh:
            return coh
        if fallback:
            return _sr.clean_display(fallback, text) or str(fallback)
        if s:
            return s.label()
    except Exception:
        pass
    # The open page is never an implicit default (Jenna 2026-10-24, Scott
    # Carlin's small-kitchen-appliance ask with LeBron James open ran on
    # LeBron James). A caller that wants the page passes it as fallback;
    # the binder already returns the page when the words point at it.
    return str(fallback or '').strip()


def reading_cohort(text, page=''):
    """A cohort the Reader found in the words that is not the page
    ('the BookTok exposed viewers'), as written, '' otherwise."""
    try:
        from . import subject_rules as _sr
        rd = _sr._request_reading()
    except Exception:
        rd = None
    if not isinstance(rd, dict):
        return ''
    pn = re.sub(r"[^a-z0-9]+", "", str(page or '').lower())
    for sub in (rd.get('subjects') or []):
        if not isinstance(sub, dict) or str(sub.get('kind') or '').lower() != 'cohort':
            continue
        aw = ' '.join(str(sub.get('as_written') or '').split())
        if not aw or len(aw) > 80:
            continue
        n = re.sub(r"[^a-z0-9]+", "", aw.lower())
        if pn and (n == pn or pn in n):
            continue
        if re.search(r"\b(?:viewers|watchers|listeners|readers|players|buyers|shoppers|subscribers|users|fans|members|customers|people|audience)\b", aw, re.I):
            return aw
    return ''


# "consumption" is the viewing verb of a consumption report (Jordan 2026-10-08 "Show me monthly consumption for
# The Office US"; Bria 2026-10-23 "run the same type of consumption report by month"): both ran unpriced without it
_VIDEO_VERB_RX = re.compile(r"\b(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing|ers|ership)?|binge[ds]?|consum(?:ption|ed|e|ing))\b", re.I)


def _viewership_ask(text):
    """A count of people who watched or streamed a title is the
    Viewership read ($500 a year), whatever else rides the sentence
    (Marie's "how many people watched the series and what other
    platforms did those viewers also watch"). A readership or
    listenership count inside an overlap ask (Jay's Lore Olympus) is a
    custom ask."""
    try:
        from prometheus.legacy.watch import _pm_is_viewership_ask
        return bool(_pm_is_viewership_ask(text)) and bool(_VIDEO_VERB_RX.search(str(text or '')))
    except Exception:
        return False


def classify(text, history=None, ctx=None):
    """A priced read, or None when the ask stays metered / free / another
    product path. ``via`` is 'service' for deterministic lanes (sizing,
    where-watched, cross) and 'generate' for compiled new-numbers."""
    # the person's own words, apart from anything they pasted (Bria 2026-10-23: a
    # pasted report under a three-sentence ask ran the message past the guard and
    # the read went out with no price and no Approve)
    try:
        from .pasted import own_words as _own
        t = ' '.join(_own(text).split())
    except Exception:
        t = ' '.join(str(text or '').split())
    if not t or len(t) > 800:
        return None
    if is_talk(t) or is_single_figure(t):
        return None
    if is_product_order(t):
        return None     # a build / journey / cut / deck / valuation order has its own lane and price
    if is_where_watched(t, history, ctx):
        return None     # the free lane (service.py 'where_watched'), never a quote
    if _viewership_ask(t):
        return {'kind': 'viewership', 'via': 'generate', 'usd': 500, 'same_frame': False,
                'frame': 'viewership', 'subject': ''}
    if _cross_ask(t):
        return {'kind': 'cross', 'via': 'service', 'usd': 100, 'same_frame': False,
                'frame': 'cross', 'subject': _sizing_subject(t, ctx)}
    try:
        from . import overlap_read as _ov
        if _ov.is_overlap_read(t):
            kind = 'overlap'
            if re.search(r"\b(?:30[- ]days?|thirty\s+days?|t30d|after|next)\b", t, re.I) \
                    and re.search(r"\b(?:watch|stream|read)\b", t, re.I):
                kind = 'path'
            frame = frame_of(kind, t)
            return {'kind': kind, 'via': 'generate', 'usd': 100,
                    'same_frame': same_frame(history, frame) if history is not None else False,
                    'frame': frame, 'subject': ''}
    except Exception:
        pass
    if is_ranked_list(t):
        frame = frame_of('list', t)
        return {'kind': 'list', 'via': 'generate', 'usd': 100,
                'same_frame': same_frame(history, frame) if history is not None else False,
                'frame': frame, 'subject': ''}
    try:
        from . import sizing_lane as _sz
        if _sz.wants_sizing(t) and not _panel_self(t, ctx) and not sizes_open_page(t, ctx):
            # the size of the audience on the open page is a lookup of a
            # figure already on screen: free and metered, never a quote
            return {'kind': 'sizing', 'via': 'service', 'usd': 100,
                    'same_frame': False, 'frame': 'sizing',
                    'subject': _sizing_subject(t, ctx)}
    except Exception:
        pass
    if custom_work(t):
        return {'kind': 'custom', 'via': 'generate', 'usd': 100,
                'same_frame': False, 'frame': 'custom', 'subject': ''}
    return None


def needs_generate_quote(text, history=None, ctx=None):
    """Compiled new-numbers that must quote even when a file is open."""
    hit = classify(text, history, ctx)
    return bool(hit and hit.get('via') == 'generate')


# ----------------------------------------------------------------- prices
def _usd(tool_key, default, username=None):
    try:
        from prometheus.legacy.watch import _pm_usd
        v = float(_pm_usd(tool_key, float(default), username=username) or 0)
        return v if v > 0 else float(default)
    except Exception:
        return float(default)


def _label(usd, username=None):
    """The price in the seat's currency. Only a real string reaches a
    reader: a host that answers with anything else (a hermetic test's
    stub, a broken seat lookup) falls back to the dollar form rather
    than printing the object into the offer line."""
    try:
        from prometheus.legacy.watch import _pm_usd_label
        lbl = _pm_usd_label(usd, username)
        if isinstance(lbl, str) and lbl.strip() and re.match(r"^[^\w\s]?\s?[\d,]+(?:\.\d+)?$", lbl.strip()):
            return lbl
    except Exception:
        pass
    v = float(usd)
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


def custom_price(username=None, history=None, subject=''):
    """The custom-ask price for this thread: the first custom ask on a
    subject is panel_report ($100), every later one on the same subject
    is panel_report_followup ($50). Live prices from the billing panel
    through the seat's company rates."""
    followup = bool(subject) and prior_custom_on_subject(history, subject)
    if not followup and history:
        # the ask stayed on the thread's subject (a cohort within it, a
        # second cut of the same read): the follow-up price applies
        try:
            from . import subject_rules as _sr
            for ts in (_sr.thread_subjects(history) or [])[:2]:
                if prior_custom_on_subject(history, ts):
                    followup = True
                    break
        except Exception:
            pass
    key = FOLLOWUP_KEY if followup else CUSTOM_KEY
    usd = _usd(key, FOLLOWUP_DEFAULT if followup else CUSTOM_DEFAULT, username)
    return {'label': _label(usd, username), 'credits': 1,
            'pull_type': FOLLOWUP_PULL_TYPE if followup else CUSTOM_PULL_TYPE,
            'tool_key': key, 'usd': usd, 'followup': followup}


def _derived_price(username=None):
    try:
        from prometheus.legacy.watch import _pm_derived_read_price
        return _pm_derived_read_price(username)
    except Exception:
        return '$100', 1


def _panel_price(username=None):
    p = custom_price(username, None, '')
    return p['label'], p['credits']


_PLACEHOLDER_SUBJECTS = frozenset({
    'that subject', 'that audience', 'that title', 'this audience',
    'this subject', 'this title', 'the page', '__page__'})
_WH_LEAD_RX = re.compile(r"^(?:how|what|which|where|who|when|why)\b", re.I)
_SIZE_PREFIX_RX = re.compile(
    r"^(?:the\s+)?(?:market\s+size\s+(?:for|of)|size\s+of|audience\s+size\s+(?:for|of)|"
    r"sample\s+size\s+(?:for|of)|how\s+big\s+is|how\s+many)\s+(?:the\s+)?", re.I)
# the tail of a how-many question that is not part of the thing counted:
# 'how many UFC fans are there' -> 'UFC fans'
_SIZE_TAIL_RX = re.compile(
    r"\s+(?:(?:are|is)\s+(?:out\s+)?there|exist(?:s)?|out\s+there|in\s+(?:the\s+)?(?:us|u\.s\.|united\s+states|country)|"
    r"in\s+total|total|overall|right\s+now|today|do\s+(?:you|we)\s+(?:have|see|carry|cover))\s*$", re.I)
_SHARE_OF_RX = re.compile(
    r"(?:how\s+penetrated\s+is|penetration\s+of|share\s+of|how\s+(?:much|far)\s+(?:does\s+)?)\s*"
    r"(?P<plat>[A-Z][\w+&'. -]{1,40}?)(?:\s+(?:in|into|across|on)\b|'s\b|\?|$)")


def _fold(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def display_subject(subject, question='', page=''):
    """The subject a reader sees in an offer: the binder's clean display
    for the words in hand, '' when the raw is a placeholder, a metric
    phrase, a question fragment, a descriptor ('market size for ...'),
    or the open page's own label that the words did not name
    (2026-10-21, Casey Pearson's offer read 'market size for prank
    content', the open profile's title; Liz Huszarik's read 'The path
    for Netflix', the open tab). An empty display writes the line
    without a subject rather than with the wrong one."""
    s = ' '.join(str(subject or '').split()).strip(" ,;:.'\"")
    if not s or s.lower() in _PLACEHOLDER_SUBJECTS or _WH_LEAD_RX.match(s):
        return ''
    if _SIZE_PREFIX_RX.match(s):
        return ''
    try:
        from . import subject_rules as _sr
        if _sr.is_metric_phrase(s):
            return ''
        if page and _fold(s) == _fold(page):
            # the page's label stands only when the words bind it; a
            # subject the words bind instead replaces it; otherwise the
            # line is written without one
            q = ' '.join(str(question or '').split())
            try:
                b = _sr.bind(q, None, '')
            except Exception:
                b = None
            if b and b.source == 'words' and b.display:
                return _sr.clean_display(b.display, q) or ''
            return ''
        return _sr.clean_display(s, question) or ''
    except Exception:
        return s


def _join(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return ', '.join(parts[:-1]) + ' and ' + parts[-1]


_AUDIENCE_NOUN_RX = re.compile(
    r"\b(?:audience|viewers|listeners|readers|players|fans|subscribers|buyers|shoppers|users|customers|"
    r"members|visitors|households|people|market)\b", re.I)
_ARTICLE_RX = re.compile(r"^(?:the|a|an|its|their|our|your|this|these|those|all|every)\b", re.I)


def _with_article(s):
    """'Babylon 5 audience on Prime Video' -> 'the Babylon 5 audience on
    Prime Video'. A bare brand or title ('Mountain Dew', 'Gilmore Girls')
    and a phrase that already opens with a determiner are left alone."""
    s = ' '.join(str(s or '').split())
    if not s or _ARTICLE_RX.match(s) or not _AUDIENCE_NOUN_RX.search(s):
        return s
    return 'the ' + s


def sizing_lead(text, ctx=None):
    """The one sentence a sizing offer opens with, built from the things
    the ask names: 'I can size the market for prank content, horror and
    comedy and Paramount+'s share of each.' '' when nothing in the ask
    names a thing (the caller writes the neutral lead)."""
    t = ' '.join(str(text or '').split())
    things = []
    try:
        from . import sizing_lane as _sz
        from . import subject_rules as _sr
        for s in _sz.subjects(t, ctx):
            s = ' '.join(str(s or '').split()).strip(" ,;:.'\"")
            if not s or s == '__page__' or _sz.is_panel_noun(s) or _WH_LEAD_RX.match(s):
                continue
            s = _SIZE_PREFIX_RX.sub('', s).strip(" ,;:.'\"")
            for _ in range(3):   # 'are there in the US' peels one tail at a time
                s2 = _SIZE_TAIL_RX.sub('', s).strip(" ,;:.'\"")
                if s2 == s:
                    break
                s = s2
            if not s or _sr.is_metric_phrase(s):
                continue
            core = _sz.strip_audience_nouns(s) or s
            # 'UFC fans' sizes as the UFC audience and says so; a bare
            # brand or title stays as written
            s = f"the {core} audience" if core != s and not _AUDIENCE_NOUN_RX.search(core) else core
            if s and s.lower() not in [x.lower() for x in things]:
                things.append(s)
    except Exception:
        things = []
    if not things:
        return ''
    market = bool(re.search(r"\bmarket\s+size\b", t, re.I))
    # an audience phrase takes its article: 'the Babylon 5 audience on Prime
    # Video', never the fragment 'Babylon 5 audience on Prime Video'
    things = [_with_article(s) for s in things]
    lead = ("I can size the market for " if market else "I can size ") + _join(things)
    m = _SHARE_OF_RX.search(t)
    if m:
        plat = m.group('plat').strip(" ,;:.'\"")
        if plat and plat.lower() not in [x.lower() for x in things]:
            lead += f" and {plat}'s share of each" if len(things) > 1 else f" and {plat}'s share of it"
    return lead + '.'


def _lead(kind, subj):
    """The default first sentence per offer kind; written without a
    subject when none reads clean."""
    if kind == 'sizing':
        return (f"I can size {subj}: share and US people in the window."
                if subj else "I can size this audience: share and US people in the window.")
    if kind == 'list':
        return (f"I can pull the ranked list you asked for on {subj}." if subj
                else "I can pull the ranked list you asked for.")
    if kind == 'path':
        return (f"I can pull the path for {subj}." if subj
                else "I can pull the path you asked for.")
    if kind == 'overlap':
        return (f"I can pull the overlap you asked for on {subj}." if subj
                else "I can pull the overlap you asked for.")
    if kind == 'derived':
        return (f"I can add that title to the same read on {subj}." if subj
                else "I can add that title to the same read.")
    return (f"I can pull that as a custom read on {subj} and deliver the numbers in this chat."
            if subj else "I can pull that as a custom read and deliver the numbers in this chat.")


def page_name(ctx=None):
    """The open profile's label from the page context, else from the
    request's page context (flask.g._pm_page_ctx, set at the door), ''
    when none."""
    try:
        name = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
        if name:
            return name
    except Exception:
        pass
    try:
        from flask import g as _g
        pc = getattr(_g, '_pm_page_ctx', None) or {}
        return str(((pc or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def price_plan(hit, username=None, history=None):
    """(label, credits, pull_type, offer_kind) for a classify() hit."""
    kind = str((hit or {}).get('kind') or 'report')
    if kind in ('sizing', 'derived'):
        label, credits = _derived_price(username)
        return label, credits, 'Derived Read', kind
    p = custom_price(username, history, str((hit or {}).get('subject') or ''))
    offer_kind = kind if kind in CUSTOM_FAMILY else 'report'
    return p['label'], p['credits'], p['pull_type'], offer_kind


# ----------------------------------------------------------------- the offer
_AUDIENCE_TAIL_RX = re.compile(
    r"\b(?:viewers|fans|readers|listeners|players|watchers|users|customers|buyers|shoppers|"
    r"subscribers|drinkers|audience|owners|members|attendees|households|people)\s*$", re.I)


def _as_audience(subj):
    s = str(subj or '').strip()
    return s if _AUDIENCE_TAIL_RX.search(s) else f"the {s} audience"


def lead_for(kind, subject, **kw):
    """One plain sentence on what the reader gets, ahead of the price.
    A caller-written sentence (``what``) wins; with no subject that
    reads clean the sentence is written without one."""
    what = ' '.join(str(kw.get('what') or '').split())
    if what:
        return what
    subj = str(subject or '').strip()
    if not subj:
        return _lead(kind, '')
    aud = _as_audience(subj)
    if kind == 'sizing':
        return f"I can size {subj}: US people in the window."
    if kind == 'list':
        return f"I can pull the ranked list on {subj}."
    if kind == 'path':
        return f"I can pull what {aud} did next."
    if kind == 'overlap':
        return f"I can pull the overlap for {aud}."
    if kind == 'cross':
        return f"I can pull that split for {aud}."
    if kind == 'derived':
        dim = str(kw.get('dimension') or '').strip()
        return (f"I can split the figures for {subj} by {dim}." if dim
                else f"I can add that breakdown to the read on {subj}.")
    if kind == 'viewership':
        verb = str(kw.get('verb') or 'watched').strip()
        span = str(kw.get('span') or 'one year').strip()
        cond = str(kw.get('condition') or '').strip(' ,.;:')
        what = (f"month by month viewers and hours watched for {subj} across {span}" if kw.get('series')
                else f"how many people in the US {verb} {subj}" + (f" {cond}" if cond else '') + f" across {span}")
        return f"I can pull {what}."
    if kind == 'rerun':
        noun = str(kw.get('noun') or 'read')
        wp = str(kw.get('window') or '').strip()
        return (f"I can run the {subj} {noun} again on {wp}." if wp
                else f"I can run the {subj} {noun} again.")
    if kind == 'talent_list':
        return str(kw.get('lead') or f"I can pull that list for {subj}.")
    cond = str(kw.get('condition') or '').strip(' ,.')
    verb = str(kw.get('verb') or '').strip()
    if cond and verb and not re.match(r"^(?:" + re.escape(verb) + r"|who|that|which)\b", cond, re.I):
        cond = f"{verb} {cond}"
    return f"I can pull that read on {subj}" + (f", {cond}." if cond else ".")


def compose_offer(lead, price_label, question, offer_body, staff=False):
    """(reply, chips, offer): '<lead> Pulling this data will cost $N.'
    with exactly Accept and Decline. Staff see the price and that it is
    not billed to them."""
    price = str(price_label or '').strip() or _label(CUSTOM_DEFAULT)
    reply = (str(lead or '').strip() + ' ' + OFFER_LINE.format(price=price)).strip()
    chips = [STAFF_ACCEPT_CHIP.format(price=price) if staff else ACCEPT_CHIP, DECLINE_CHIP]
    body = dict(offer_body or {})
    body.setdefault('price_label', price)
    return reply, chips, body


_POINTS_AT_PAGE_RX = re.compile(r"\b(?:this|these|here|on (?:my )?screen|open (?:profile|tab|file))\b", re.I)


def offer(subject, price_label, question, kind='report', frame='', years=1,
          staff=False, what='', page='', **kw):
    """(reply, followups, offer dict) in the one shape. ``what`` is a
    caller-written first sentence (the sizing lead). The subject shown
    is the binder's clean display, never the open page's label unless
    the words point at the page; when nothing reads clean the line is
    written without one."""
    raw_subj = str(subject or '').strip()
    pg = page or page_name()
    subj = display_subject(raw_subj, question, pg)
    if not subj and raw_subj and pg and _fold(raw_subj) == _fold(pg) and _POINTS_AT_PAGE_RX.search(str(question or '')):
        subj = raw_subj
    if subj and raw_subj and _fold(raw_subj) != _fold(subj) and _fold(raw_subj).startswith(_fold(subj)) \
            and (not pg or _fold(raw_subj) != _fold(pg)):
        # the binder's fuller label ('Scream 7 viewers on Paramount+') is the one the reader saw bound
        subj = raw_subj
    if not what and kind in ('overlap', 'custom', 'report'):
        # a two-service co-viewing ask reads its own question back, with the
        # window as asked (Bria 2026-10-22: never 'across one year' for a
        # September to March ask)
        try:
            from . import overlap_read as _ov
            what = _ov.also_watched_lead(question)
        except Exception:
            what = ''
    kw = dict(kw, what=what)
    # the confirm and the ledger line carry the subject the reader saw
    offer_body = {'question': str(question or '')[:600],
                  'subject': (subj or raw_subj or 'that subject')[:120],
                  'kind': kind, 'frame': frame}
    if years and years != 1:
        offer_body['years'] = years
    if kw.get('dimension'):
        offer_body['dimension'] = str(kw['dimension'])
    return compose_offer(lead_for(kind, subj, **kw), price_label, question,
                         offer_body, staff=staff)


def out_of_credits(subject, price_label):
    subj = str(subject or 'that read').strip()
    price = str(price_label or '').strip()
    return {
        'success': False, 'guidance': True, 'analysis_read': True,
        'error': (f"The {subj} read runs {price} and your account cannot "
                  f"cover it right now. Top up or ask your admin, and I "
                  f"will pick it right back up."),
        'followups': [],
    }


def staff_user(user):
    try:
        from . import guards as _g
        return bool(_g.is_staff(user))
    except Exception:
        return False


def charge(host, username, subject, pull_type, credits, description, quantity=1):
    """True when the seat covered it (or is unlimited). False = no funds.
    The ledger line carries the tool (through pull_type) and the subject."""
    uname = str(username or '').strip()
    if not uname:
        return True
    consume = None
    if host is not None and hasattr(host, 'consume_credit'):
        consume = host.consume_credit
    if consume is None:
        return True
    try:
        return bool(consume(uname, description=description,
                            pull_type=pull_type, credits_used=int(credits or 1),
                            quantity=int(quantity or 1)))
    except Exception:
        return False


def charge_description(kind, subject, question='', dimension='', years=1):
    """The ledger line: the product, the subject, the ask."""
    q = ' '.join(str(question or '').split())[:90]
    subj = str(subject or 'that subject').strip()
    if kind == 'viewership':
        return f"Viewership read ({years} yr) - {subj}" + (f": {q}" if q else '')
    if kind == 'derived':
        return f"Derived read ({dimension or 'breakdown'}) - {subj}" + (f": {q}" if q else '')
    if kind == 'sizing':
        return f"Audience size - {subj}"
    if kind == 'rerun':
        return f"Re-run - {subj}" + (f": {q}" if q else '')
    return f"Custom ask - {subj}" + (f": {q}" if q else '')


def offer_or_charge(*, text, subject, kind, panel_confirm, username, user=None,
                    history=None, host=None, consume=None, years=1, dimension='', what='',
                    verb='watched', series=False, condition='', frame=''):
    """The one gate for a priced read on any chat path.

    Returns ('offer', payload) when the ask has not been accepted yet,
    ('charged', panel_charge) once the charge landed on the confirm,
    ('error', payload) when the seat cannot cover it.
    ``kind``: report | custom | path | overlap | list |
    cross | sizing | derived | viewership. The price for the custom
    family is the thread's: $100 first on this subject, $50 after.
    """
    kind = str(kind or 'report')
    subj = str(subject or '').strip() or 'that subject'
    staff = staff_user(user)
    if kind == 'viewership':
        try:
            from prometheus.legacy.watch import _pm_viewership_read_price
            vw = _pm_viewership_read_price(text, username)
        except Exception:
            vw = None
        label, credits, qty = (vw if vw else (_label(_usd('viewership_read', 500.0, username), username), 5, int(years or 1)))
        pull_type = 'Viewership Read'
    elif kind in ('sizing', 'derived'):
        label, credits = _derived_price(username)
        qty, pull_type = 1, 'Derived Read'
    else:
        p = custom_price(username, history, subj)
        label, credits, qty, pull_type = p['label'], p['credits'], 1, p['pull_type']
    if not isinstance(panel_confirm, dict):
        span = 'one year' if int(years or 1) == 1 else f"{int(years)} years"
        reply, chips, off = offer(subj, label, text, kind=kind, frame=frame, years=years,
                                  staff=staff, dimension=dimension, verb=verb, series=series,
                                  condition=condition, span=span, what=what)
        return 'offer', {'success': True, 'action': 'answer', 'reply': reply,
                         'followups': chips, 'offer_deck': False, 'deck_angle': None,
                         'panel_offer': off, 'subject': subj}
    rc = panel_confirm.get('resume_charge')
    if isinstance(rc, dict):
        return 'charged', rc
    desc = charge_description(kind, subj, text, dimension=dimension, years=qty)
    if staff:
        # Crosswalk staff see the offer and are never billed
        return 'charged', {'user': username, 'credits': 0, 'subject': subj, 'kind': kind,
                           'pull_type': pull_type, 'label': label, 'staff': True}
    fn = consume
    if fn is None and host is not None and hasattr(host, 'consume_credit'):
        fn = host.consume_credit
    ok = True
    if username and fn is not None:
        try:
            ok = bool(fn(username, description=desc, pull_type=pull_type,
                         credits_used=int(credits or 1), quantity=int(qty or 1)))
        except Exception:
            ok = False
    if not ok:
        return 'error', out_of_credits(subj, label)
    return 'charged', {'user': username, 'credits': credits, 'subject': subj,
                       'kind': kind, 'pull_type': pull_type, 'label': label}


def service_turn(text, *, user=None, ctx=None, history=None, host=None,
                 panel_confirm=None):
    """Offer, charge-then-answer, or None (let the other lanes run).

    Handles sizing and the cross here so a confirm never falls through
    to a compiled read. Where-watched is never priced (the free lane in
    service.py answers it ahead of this). Overlap / list / path / custom
    return None so generate_metrics quotes them.
    """
    if host is None:
        try:
            from .host import host as _h
            host = _h
        except Exception:
            host = None
    uname = ''
    try:
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
    except Exception:
        uname = ''
    pc = panel_confirm if isinstance(panel_confirm, dict) else None
    pc_kind = str((pc or {}).get('kind') or '')
    hit = classify(text, history, ctx)
    if pc and pc_kind in ('sizing', 'cross') and not hit:
        hit = {'kind': pc_kind, 'via': 'service', 'usd': 100, 'same_frame': False,
               'frame': frame_of(pc_kind, text), 'subject': str(pc.get('subject') or '')}
    if not hit or hit.get('via') != 'service' or hit.get('kind') in NEVER_PRICED:
        return None
    subject = str(hit.get('subject') or (pc or {}).get('subject') or '').strip() or _sizing_subject(text, ctx)
    bound = bound_subject(text, history, ctx, fallback=subject)
    if bound and hit['kind'] != 'sizing':
        subject = bound
    if pc is None:
        state, payload = offer_or_charge(text=text, subject=subject, kind=hit['kind'],
                                         panel_confirm=None, username=uname, user=user,
                                         history=history, host=host, frame=hit.get('frame') or '',
                                         what=(sizing_lead(text, ctx) if hit['kind'] == 'sizing' else ''))
        return payload
    if hit['kind'] == 'sizing' and pc_kind not in ('sizing', 'derived', ''):
        return None
    state, payload = offer_or_charge(text=text, subject=subject, kind=hit['kind'],
                                     panel_confirm=pc, username=uname, user=user,
                                     history=history, host=host, frame=hit.get('frame') or '')
    if state == 'error':
        return payload
    from . import sizing_lane as _sz
    raw = _sz.answer(text, ctx, host=host, history=history, staff=staff_user(user))
    paid = {'kind': hit['kind'], 'frame': hit.get('frame'), 'usd': hit.get('usd'),
            'subject': subject, 'pull_type': (payload or {}).get('pull_type')}
    if not raw:
        return {
            'success': True, 'action': 'answer',
            'reply': f"I could not finish that {hit['kind'].replace('_', ' ')} on {subject}. Ask again and I will retry.",
            'followups': [], 'offer_deck': False, 'deck_angle': None,
            'priced_paid': paid, 'subject': subject,
        }
    out = dict(raw)
    out['priced_paid'] = paid
    out.setdefault('subject', subject)
    out['priced_lane'] = hit['kind']
    return out


# ----------------------------------------------------------------- chat helpers
def sizing_offer_payload(text, history=None, username=None):
    """The interpret surface's sizing pre-check: the $100 offer for an
    audience-size ask, or None when the words are not a sizing ask."""
    try:
        from . import sizing_lane as _sz
    except Exception:
        _sz = None
    hit = classify(text, history)
    if not ((hit and hit.get('kind') == 'sizing') or (
            _sz is not None and _sz.wants_sizing(text) and _sz.names_an_audience(text))):
        return None
    subj = (hit or {}).get('subject') or _sizing_subject(text) or 'that audience'
    label, credits, pull_type, _k = price_plan(hit or {'kind': 'sizing', 'usd': 100}, username)
    reply, chips, off = offer(subj, label, text, kind='sizing',
                              frame=(hit or {}).get('frame') or 'sizing')
    return {'success': False, 'guidance': True, 'analysis_read': True, 'error': reply,
            'followups': chips, 'panel_offer': off, 'subject': subj}


def charge_rerun(username, subj_hint, consume):
    """The re-run accepted on its card (I2) is charged at the quoted
    price before anything is generated. Returns None when no re-run
    charge is armed, {} when it landed, or the out-of-funds payload."""
    try:
        from flask import g as _g
    except Exception:
        return None
    rrc = getattr(_g, '_pm_rerun_charge', None)
    if not isinstance(rrc, dict) or getattr(_g, '_pm_rerun_charged', False):
        return None
    _g._pm_rerun_charged = True
    subj = str(rrc.get('subject') or subj_hint or 'the read')
    try:
        from .continuation import window_phrase as _wp
        win = _wp(rrc.get('window'))
    except Exception:
        win = ''
    desc = charge_description('rerun', subj, f"window {win}" if win else '')
    pull_type = ('Viewership Read' if str(rrc.get('price_key') or '') == 'viewership_read'
                 else (FOLLOWUP_PULL_TYPE if str(rrc.get('price_key') or '') == FOLLOWUP_KEY else CUSTOM_PULL_TYPE))
    credits = 5 if pull_type == 'Viewership Read' else 1
    ok = True
    if username and consume is not None:
        try:
            ok = bool(consume(username, description=desc, pull_type=pull_type, credits_used=credits, quantity=1))
        except Exception:
            ok = False
    if ok:
        return {}
    out = out_of_credits(subj, rrc.get('price_label') or 'the quoted price')
    out['error'] = out['error'].replace(f"The {subj} read runs", f"The {subj} re-run runs")
    out['subject'] = subj
    return out


# The whole panel as a subject: a market question that names no audience
# runs on everyone in the US once the reader picks it (never on the
# profile that happens to be open).
GEN_POP_LABEL = "Everyone in the US"


def is_everyone(label):
    try:
        from .named_subject import is_everyone as _ie
        return _ie(label)
    except Exception:
        return _fold(label) in ('everyoneintheus', 'everyone', 'genpop', 'usgenpop', 'generalpopulation', 'allusadults', 'usadults')


def which_audience(text, ctx=None):
    """The one question when a put-together read names no audience and
    does not point at the page (Jenna 2026-10-06: nothing named, nothing
    assumed; 2026-10-24: never the open profile by default). The page is
    a chip, never the answer; everyone in the US is the other chip; the
    widget's "Something else" asks the reader to name one. No quote and
    no charge until the audience is settled."""
    page = _page_name(ctx)
    opts = []
    if page:
        opts.append({'label': f'{page} (open on your screen)', 'subject': page})
    opts.append({'label': GEN_POP_LABEL, 'subject': GEN_POP_LABEL})
    if page:
        reply = (f"Which audience should this run on? The question does not name one, and {page} is only "
                 f"the profile open on your screen. Pick it, pick everyone in the US, or name another audience.")
    else:
        reply = "Which audience should this run on? The question does not name one. Pick everyone in the US, or name an audience."
    return {'success': True, 'action': 'answer', 'reply': reply,
            'followups': [o['label'] for o in opts] + ['Something else'],
            'offer_deck': False, 'deck_angle': None, 'audience_confirm': True,
            'memory_confirm': {'question': str(text or ''), 'options': opts}}


def work_gate(text, history=None, ctx=None, user=None, body=None, username=None, consume=None):
    """The analyze surface's gate (service.py), ahead of every lane that
    could answer on an open file: a put-together read is a custom ask.

    No confirm yet -> the offer payload (one shape). A confirm on the
    body (Accept) -> the charge lands here, once, and rides the body's
    panel_confirm as ``resume_charge`` so no lane charges again; the
    out-of-funds payload when the seat cannot cover it. None when the
    ask is not a custom ask (a lookup, talk, a continuation, a
    deterministic lane), so the lanes run as before."""
    body = body if isinstance(body, dict) else {}
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    pc = extra.get('panel_confirm') or body.get('panel_confirm')
    pc = pc if isinstance(pc, dict) else None
    hit = classify(text, history, ctx)
    if not hit or hit.get('via') != 'generate':
        return None
    uname = str(username or (user or {}).get('username') or (user or {}).get('email') or '').strip()
    try:
        from .pasted import own_words as _own
        words = ' '.join(_own(text).split()) or text
    except Exception:
        words = text
    reading = body.get('reading') if isinstance(body.get('reading'), dict) else None
    # The subject, in order: the chip the reader tapped (bind_subject), the
    # subject the quote named (the Approve carries it back), the words. The
    # open page is never the default: a read that names no audience asks
    # which one before any quote (Jenna 2026-10-24, Scott Carlin).
    bind = str(extra.get('bind_subject') or body.get('bind_subject') or '').strip()
    quoted = str((pc or {}).get('subject') or '').strip() if isinstance(pc, dict) else ''
    if bind:
        subject = GEN_POP_LABEL if is_everyone(bind) else bind
    elif quoted and quoted.lower() != 'that subject':
        subject = GEN_POP_LABEL if is_everyone(quoted) else quoted
    else:
        subject = bound_subject(words, history, ctx, fallback='', reading=reading)
        if subject and is_everyone(subject):
            subject = GEN_POP_LABEL
    if not subject:
        try:
            # The open product's own view ("this campaign", its title) is the
            # base and its lanes answer, metered, as before (2026-10-22,
            # Alexia). Only an ask that names nothing and points at nothing
            # on screen gets the which-audience question.
            from . import view_base as _vb
            _vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
            if _vb.view_base(_vc) and _vb.names_view(words, _vc):
                return None
        except Exception:
            pass
        return which_audience(words, ctx)
    kind = hit['kind'] if hit['kind'] in CUSTOM_FAMILY or hit['kind'] == 'viewership' else 'report'
    extra = {}
    try:
        from prometheus.legacy import watch as _w
        if kind == 'viewership':
            extra = {'years': int(_w._pm_window_years(words) or 1), 'verb': _w._pm_viewership_verb(words),
                     'series': bool(_w._pm_is_viewership_series_ask(words))}
        else:
            extra = {'verb': _w._pm_viewership_verb(words) if _w._pm_is_consumption_count_ask(words) else 'watched'}
    except Exception:
        extra = {}
    try:
        from .named_subject import ask_condition as _ask_cond
        extra['condition'] = str(_ask_cond(words) or '')
    except Exception:
        pass
    state, payload = offer_or_charge(text=words, subject=subject, kind=kind, panel_confirm=pc,
                                     username=uname, user=user, history=history, consume=consume,
                                     frame=str(hit.get('frame') or ''), **extra)
    if state == 'offer':
        return payload
    if state == 'error':
        return payload
    if isinstance(pc, dict) and not isinstance(pc.get('resume_charge'), dict):
        pc['resume_charge'] = payload
    # The read runs on exactly what was quoted and approved: the subject
    # rides the body as the bound subject so the generation pass never
    # resolves a base of its own (the open page) behind the quote.
    if subject and not bind and not str(body.get('bind_subject') or '').strip():
        body['bind_subject'] = subject
    return None
