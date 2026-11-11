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
                        a thread; a follow-up on the same subject is
                        metered, never priced (Jenna 2026-10-30).
  SIZING                $100 (the derived-read row).
  VIEWERSHIP            $100 for the first ask on a subject, whatever
                        the window; follow-ups on it are metered
                        (Jenna 2026-10-30; was $500 per year).
  BREAKDOWN             $100 (a delivered figure split by a dimension).

One offer shape everywhere (Jenna, verbatim: "just say something like
pulling this data will cost $500 accept or decline with chips"): one
sentence on what they get, then "Pulling this data will cost $N.",
then exactly two chips, Accept and Decline. Never "The path for X",
never "I will not answer until you accept". An unlimited seat and
Crosswalk staff see no offer at all (Jenna 2026-11-08: "for anyone
with unlimited credit it just should say included with your plan
instead of giving a price or staff chip"): the reply opens "Included
with your plan." and the read starts in the same turn, charged at zero.

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
# A client seat the plan covers (an unlimited company seat) is never
# called a staff account (Grace Yin at Amazon, 2026-11-08, read "not
# billed to a staff account" twice on her own offers). The chip survives
# for compose_offer callers; the gate itself no longer shows a covered
# seat an offer at all (below).
COVERED_ACCEPT_CHIP = "Approve ({price}, included in your plan)"
# Included with your plan (Jenna 2026-11-08, verbatim: "for anyone with
# unlimited credit it just should say included with your plan instead of
# giving a price or staff chip"). An unlimited seat (and Crosswalk staff)
# never sees a price, an Approve chip or the staff chip: the reply opens
# with this line and the read starts in the same turn. The charge stays
# at zero; `included_charge` is the panel_charge such a read carries.
INCLUDED_LINE = "Included with your plan."
INCLUDED_LABEL = "Included with your plan"
# Kept under its historic name for callers that import it; the offer text
# no longer carries a refusal sentence.
CONFIRM = ""
# The one line a reader gets when a priced pass came back with nothing
# (app.py _CHATBOT_CALM_MESSAGE, verbatim): the answer comes by email, ops
# is told. Never a line that says the read could not finish.
CALM_LINE = ("Working on it. Confirmed your task is in progress and I will email you when it "
             "completes.")

CUSTOM_FAMILY = frozenset(('report', 'custom', 'overlap', 'path', 'list', 'cross', 'adaptation'))
# Never priced: a lookup of carriage (where a named title is watched, the
# content map, the library, one research call) is the free where-watched
# lane in prometheus/service.py, ahead of every quote. classify() returns
# None for it so neither service_turn nor work_gate can offer it.
NEVER_PRICED = frozenset(('where_watched',))
CUSTOM_KEY, CUSTOM_DEFAULT = 'panel_report', 100.0
# 2026-10-30 (Jenna): follow-ups on a subject already paid for in the thread are metered, not priced (custom and viewership alike)
FOLLOWUP_KEY, FOLLOWUP_DEFAULT = 'panel_report_followup', 0.0
CUSTOM_PULL_TYPE, FOLLOWUP_PULL_TYPE = 'Panel Report', 'Panel Report Follow-up'
METERED_LABEL = 'metered'

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
    # a question about a figure already shown ("Is that 55% of total viewers
    # or of new and reactivated watchers?"): its definition, never a new read
    # (golden g077, 2026-11-02)
    r"(?:is|was|does|do|are|were) (?:that|this|it|the) \d[\d,.]*\s*(?:%|percent|k|m|million|thousand)?\b.{0,80}\bo[fr]\b|"
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
    if _CUSTOM_WORK_RX.search(t) or is_ranked_list(t) or _INC_RX.search(t):
        return True
    # A viewing chain between titles (what the viewers of one title did
    # next, how many went on, how many finished) is the content-journey
    # read on the titles in the words (Jenna 2026-11-05: "how many power
    # raising kanan season 5 viewers autoplay'ed into fightland and then
    # of those what percent completed that entire season of fightland"
    # was sized as the profile on screen).
    try:
        from . import sizing_lane as _sz
        return bool(_sz.is_viewing_chain_ask(t))
    except Exception:
        return False


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


def _ov_first(text):
    """A subscribe / pay / incremental overlap keeps its own lane."""
    try:
        from . import overlap_read as _ov
        return bool(_ov.is_overlap_read(text))
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
        if not subs or not all(s == '__page__' or s.strip().lower() == page.lower() for s in subs):
            return False
        # the page is the subject only when the words name nothing else
        # (a title, a person, a chain of titles): Jenna 2026-11-05
        return not _sz.names_other_subject(text, [page], ctx)
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


_VALUATION_ORDER_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+|would you\s+|i want (?:you )?to\s+|i need (?:you )?to\s+|i'?d like (?:you )?to\s+|let'?s\s+)?"
    r"(?:value|valuate|price|appraise)\s+(?:the\s+|a\s+|this\s+)?[^.?!;\n]{2,80}?"
    r"(?:\s+x\s+|\s+and\s+|\s+with\s+|\s+for\s+)[^.?!;\n]{1,60}?(?:\s+(?:partnership|deal|campaign|sponsorship|collab(?:oration)?))?\s*(?:[.?!;]|$)|"
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:value|valuate|price)\s+(?:the\s+)?[^.?!;\n]{2,80}?\s+(?:partnership|deal|sponsorship|collab(?:oration)?)\b", re.I)


# The product noun leads and no verb is written: "journey for Young Sheldon
# on Netflix, start from people who watched a clip on TikTok" (golden g054,
# 2026-11-02). A question about a product ("how big is the journey for
# Landman") never matches: the noun has to open the message.
_NOUN_LED_ORDER_RX = re.compile(
    r"^\W*(?:a\s+|another\s+|new\s+|one\s+)?(?:digital\s+)?"
    r"(?:journey|valuation|attribution(?: campaign)?|flywheel|deck|one-?pager|profile)\s+(?:for|on|of)\s+[A-Za-z0-9][^?\n]*$", re.I)


def is_product_order(text):
    """A build order for a priced product (a profile, a cut, a journey,
    a valuation, an attribution campaign, a deck): never a custom-ask
    quote. The product's own lane prices and confirms it."""
    t = str(text or '')
    if _PRODUCT_ORDER_RX.search(t):
        return True
    if _VALUATION_ORDER_RX.search(t):
        return True     # "can you value Gunna x Nike?", "Value the Stanley x Target partnership" (goldens g055, g056, 2026-11-02)
    if _NOUN_LED_ORDER_RX.search(t):
        return True     # "journey for Young Sheldon on Netflix, start from ..." (golden g054, 2026-11-02)
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
        # under a self-contained product (Digital Journey IQ, Attribution
        # IQ, ...) the study on screen is the page, never a profile left
        # in the picker (2026-11-05: an approval on the Music to Long Form
        # study ran as a read on Olipop, the picker's profile), and never
        # the tab label with its icon
        try:
            from . import view_base as _vb
            vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
            if isinstance(vc, dict) and _vb.is_self_contained(vc.get('view_id')):
                name = str(_vb.view_label(vc) or '').strip()
                if name:
                    return name
        except Exception:
            pass
        return str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        return ''


def bound_subject(text, history=None, ctx=None, fallback='', reading=None):
    """The one subject every offer shows (I1): the binder's label when
    the words name one, else the fallback cleaned, else the page. The
    Reader's reading, when the caller has one, feeds the binder."""
    page = _page_name(ctx)
    try:
        # a demographic segment is its own subject: the segment as the words name it, never a library file
        # one of the filter's words resembles and never the page (2026-11-11)
        from . import segment_sizing as _seg
        if _seg.is_segment_ask(text):
            return _seg.segment_label(_seg.parse(text))
    except Exception:
        pass
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
# hours and watch time are the viewing metric too (Cara Dorfman, Amazon, 2026-11-10: "total hours by month for 2025 ... for
# Prime Video and YouTube" carried no viewing verb and fell to the capability lane)
_VIDEO_VERB_RX = re.compile(r"\b(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing|ers|ership)?|binge[ds]?|consum(?:ption|ed|e|ing)|"
                            r"(?:total\s+)?hours|watch\s*time|time\s+spent)\b", re.I)


def _viewership_ask(text):
    """A count of people who watched or streamed a title is the
    Viewership read ($100 first on a subject), whatever else rides the sentence
    (Marie's "how many people watched the series and what other
    platforms did those viewers also watch"). A readership or
    listenership count inside an overlap ask (Jay's Lore Olympus) is a
    custom ask."""
    try:
        from prometheus.legacy.watch import _pm_is_viewership_ask
        return bool(_pm_is_viewership_ask(text)) and bool(_VIDEO_VERB_RX.search(str(text or '')))
    except Exception:
        return False


# A what-if about the audience on screen (Jay Patel, Amazon, 2026-11-03, with
# the Ordeal readers open): "if there was an animated adaptation of the series,
# how many of these readers do you think would tune in and sign up for Prime
# Video?" Two numbers about the people on screen: how many would watch, and how
# many of those would be new to the service. A put-together read on the open
# audience, never a sizing of "sign up for Prime Video".
_ADAPT_WORK_RX = re.compile(
    r"\b(?:an? |the )?(?:animated |anime |live[- ]action |tv |television |streaming |film |movie |series |limited[- ]series |podcast |video ?game |stage )?"
    r"(?:adaptation|spin-?off|reboot|remake|sequel|prequel|revival|movie version|film version|series version)\b|"
    r"\b(?:adapted (?:into|as|for)|turned into|made into|becomes?) (?:an? |the )?(?:animated |live[- ]action |tv |streaming )?(?:series|show|film|movie|season|podcast|game)\b|"
    r"\bif (?:there (?:was|were)|(?:\w+ ){0,3}(?:made|launched|released|greenlit|produced|put out|dropped|ordered|picked up)) (?:an? |the |another )?(?:[\w&':.+-]+ ){0,5}?(?:series|show|season|film|movie|adaptation|spin-?off|reboot|podcast|game)\b", re.I)
_ADAPT_ACT_RX = re.compile(
    r"\b(?:tune in|sign ?up|sign up for|subscribe|watch|stream|convert|would (?:come|show up|follow|pay|buy|try|join))\b", re.I)
# a what-if, not a measured past ("how many people watched the Netflix adaptation of One Piece" is viewership)
_ADAPT_IF_RX = re.compile(r"\b(?:would|could|might|if there|if \w+ (?:made|make|launched|launch|released|release|greenlit|produced|ordered|picked up|did)|what if|were to|hypothetical(?:ly)?|imagine)\b", re.I)
_SERVICE_RX = re.compile(
    r"\b(prime video|amazon prime video|amazon prime|netflix|hulu|max|hbo max|disney\+|disney plus|peacock|paramount\+|paramount plus|"
    r"apple tv\+|apple tv plus|apple tv|youtube|youtube tv|tubi|pluto tv|roku channel|crunchyroll|starz|amc\+|britbox|discovery\+|espn\+|fubo|sling|mgm\+)\b", re.I)


_PRETTY_SERVICE = {'amazon prime video': 'Prime Video', 'amazon prime': 'Prime Video', 'prime video': 'Prime Video',
                   'hbo max': 'HBO Max', 'disney plus': 'Disney+', 'paramount plus': 'Paramount+', 'apple tv plus': 'Apple TV+',
                   'youtube tv': 'YouTube TV', 'pluto tv': 'Pluto TV', 'roku channel': 'The Roku Channel', 'youtube': 'YouTube',
                   'netflix': 'Netflix', 'hulu': 'Hulu', 'max': 'Max', 'peacock': 'Peacock', 'tubi': 'Tubi',
                   'crunchyroll': 'Crunchyroll', 'starz': 'Starz', 'britbox': 'BritBox', 'fubo': 'Fubo', 'sling': 'Sling'}


def platform_subject(text):
    """The first streaming service the words name, in its display spelling
    ('Prime Video' for "PRIME video"), or '' when none. The subject of a
    viewing-metric read that names a service and no title."""
    m = _SERVICE_RX.search(str(text or ''))
    if not m:
        return ''
    raw = m.group(1).lower()
    return _PRETTY_SERVICE.get(raw) or (m.group(1) if m.group(1)[:1].isupper() else m.group(1).title())


def is_adaptation_ask(text):
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 600:
        return False
    return bool(_ADAPT_WORK_RX.search(t) and _ADAPT_ACT_RX.search(t) and _ADAPT_IF_RX.search(t)
                and re.search(r"\b(?:how many|what (?:share|percent|percentage|portion|fraction)|would|do you think)\b", t, re.I))


def adaptation_details(text):
    """(the adaptation as written, the service named) for the offer line."""
    t = ' '.join(str(text or '').split())
    m = _ADAPT_WORK_RX.search(t)
    work = (m.group(0).strip() if m else 'an adaptation')
    work = re.sub(r"^if there (?:was|were) ", '', work, flags=re.I)
    work = re.sub(r"^if (?:\w+ ){0,3}(?:made|launched|released|greenlit|produced|put out|dropped|ordered|picked up) ", '', work, flags=re.I)
    work = re.sub(r"^(?:adapted (?:into|as|for)|turned into|made into|becomes?) ", '', work, flags=re.I)
    work = work[:1].lower() + work[1:] if re.match(r"^(?:An?|The|Another|Animated|Live|Tv|Television|Streaming|Film|Movie|Series|Limited|Podcast|Video|Stage|Adaptation|Spin|Reboot|Remake|Sequel|Prequel|Revival)\b", work) else work
    if not re.match(r"^(?:an?|the|another)\b", work, re.I):
        work = ('an ' if re.match(r"^[aeiou]", work, re.I) else 'a ') + work
    sm = _SERVICE_RX.search(t)
    service = sm.group(1) if sm else ''
    pretty = {'amazon prime video': 'Prime Video', 'amazon prime': 'Prime Video', 'prime video': 'Prime Video', 'hbo max': 'HBO Max', 'disney plus': 'Disney+',
              'paramount plus': 'Paramount+', 'apple tv plus': 'Apple TV+', 'youtube tv': 'YouTube TV', 'pluto tv': 'Pluto TV', 'roku channel': 'The Roku Channel'}
    service = pretty.get(service.lower(), service)
    if service and not pretty.get(service.lower()) and service.lower() in ('netflix', 'hulu', 'max', 'peacock', 'tubi', 'crunchyroll', 'starz', 'britbox', 'fubo', 'sling', 'youtube'):
        service = service.capitalize() if service.lower() != 'youtube' else 'YouTube'
    return work, service


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
    try:
        # a count of the people who fit a demographic profile, not yet on record, is new numbers on ONE joint
        # segment: a custom ask on the segment the words name, never sized attribute by attribute and never a
        # metered single figure (prometheus.segment_sizing, 2026-11-11). A segment on record answered upstream.
        from . import segment_sizing as _seg
        if _seg.is_segment_ask(t):
            return {'kind': 'custom', 'via': 'generate', 'usd': 100, 'same_frame': False, 'frame': 'custom',
                    'subject': _seg.segment_label(_seg.parse(t))}
    except Exception:
        pass
    if is_talk(t) or is_single_figure(t):
        return None
    if is_product_order(t):
        return None     # a build / journey / cut / deck / valuation order has its own lane and price
    if is_where_watched(t, history, ctx):
        return None     # the free lane (service.py 'where_watched'), never a quote
    if is_adaptation_ask(t):
        # how many of the audience on screen would watch a what-if adaptation, and how many would be new to the service
        return {'kind': 'adaptation', 'via': 'generate', 'usd': 100, 'same_frame': False,
                'frame': 'adaptation', 'subject': ''}
    if _viewership_ask(t):
        return {'kind': 'viewership', 'via': 'generate', 'usd': 100, 'same_frame': False,
                'frame': 'viewership', 'subject': ''}
    if _cross_ask(t):
        return {'kind': 'cross', 'via': 'service', 'usd': 100, 'same_frame': False,
                'frame': 'cross', 'subject': _sizing_subject(t, ctx)}
    try:
        # a cross of behaviors inside one audience ("Chime users who shop at Target and are
        # interested in X", prometheus.crosstab): a cross of two or more things, quoted here
        from . import crosstab as _xt
        if not _ov_first(t) and not _xt.declined(t) and _xt.wants(t, ctx):
            return {'kind': 'cross', 'via': 'service', 'usd': 100, 'same_frame': False,
                    'frame': 'crosstab', 'subject': _xt.subject_hint(t, ctx)}
    except Exception:
        pass
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
    subject is panel_report ($100); every later one on the same subject
    is metered (Jenna 2026-10-30), no quote and no flat charge. Live
    prices from the billing panel through the seat's company rates."""
    followup = bool(subject) and prior_custom_on_subject(history, subject)
    if not followup and history:
        # the ask stayed on the thread's subject (a cohort within it, a
        # second cut of the same read): the follow-up price applies. A
        # different named subject is a new first ask at $100 (2026-10-21);
        # the thread's remembered subject never prices it as a follow-up
        try:
            from . import subject_rules as _sr
            # the reads the thread's turns record, never the remembered
            # conversation subject (prometheus.thread_subject): a pin says
            # what the thread is about, not what was paid for
            for ts in (_sr.thread_subjects(history, include_pin=False) or [])[:2]:
                if not prior_custom_on_subject(history, ts):
                    continue
                # the ask stayed on that subject (named it, a cohort inside it, or
                # named nothing); a different named audience is a new first ask
                # (2026-10-30: 'top QSRs for Gilmore Girls viewers' after a paid
                # Love Hypothesis read is a new $100 ask, not a follow-up)
                sj = str(subject or '').strip()
                if (not sj or not looks_like_subject(sj) or _same_subject(sj, ts)
                        or _fold(ts) in _fold(sj) or _fold(sj) in _fold(ts)):
                    followup = True
                    break
        except Exception:
            pass
    if followup:
        # metered: no quote, no flat charge; the answer's own usage bills on the Prometheus meter
        return {'label': METERED_LABEL, 'credits': 0, 'pull_type': FOLLOWUP_PULL_TYPE,
                'tool_key': FOLLOWUP_KEY, 'usd': 0.0, 'followup': True, 'metered': True}
    usd = _usd(CUSTOM_KEY, CUSTOM_DEFAULT, username)
    return {'label': _label(usd, username), 'credits': 1,
            'pull_type': CUSTOM_PULL_TYPE, 'tool_key': CUSTOM_KEY, 'usd': usd, 'followup': False, 'metered': False}


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
        from . import segment_sizing as _seg
        if _seg.is_segment_label(s):
            return s          # a demographic segment reads as the words name it, never title-cased as a brand (2026-11-11)
    except Exception:
        pass
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


_AUD_NOUN_IN_TITLE_RX = re.compile(
    r"\s+(?:viewers|watchers|audience|fans|streamers|subscribers|readers|listeners|players|users)\b(?=\s+on\s+|\s*$)", re.I)


def _title_of_audience(subj):
    """'Law & Order: SVU viewers on Peacock' -> 'Law & Order: SVU on Peacock'."""
    return _AUD_NOUN_IN_TITLE_RX.sub('', str(subj or '')).strip()


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
        if dim == 'geography':
            dim = 'region'
        return (f"I can split the figures for {subj} by {dim}." if dim
                else f"I can add that breakdown to the read on {subj}.")
    if kind == 'viewership':
        verb = str(kw.get('verb') or 'watched').strip()
        span = str(kw.get('span') or 'one year').strip()
        cond = str(kw.get('condition') or '').strip(' ,.;:')
        # "watched <title>", never "watched <title> viewers on Peacock" (Bria
        # 2026-10-21): the audience noun comes off the title, the service
        # rides as "on <service>"
        title = _title_of_audience(subj)
        what = (f"month by month viewers and hours watched for {subj} across {span}" if kw.get('series')
                else f"how many people in the US {verb} {title}" + (f" {cond}" if cond else '') + f" across {span}")
        return f"I can pull {what}."
    if kind == 'adaptation':
        work = str(kw.get('work') or 'an adaptation')
        service = str(kw.get('service') or '')
        where = f" on {service}" if service else ''
        who = aud if _AUDIENCE_TAIL_RX.search(subj) else f"the {subj} audience"
        return (f"I can read how many of {who} would tune in to {work}{where}, and how many of those would be new "
                f"{service + ' ' if service else ''}sign-ups rather than people already on the service.")
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
    if staff == 'covered':
        accept = COVERED_ACCEPT_CHIP.format(price=price)
    elif staff:
        accept = STAFF_ACCEPT_CHIP.format(price=price)
    else:
        accept = ACCEPT_CHIP
    chips = [accept, DECLINE_CHIP]
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


def _host_cap(host, name):
    """Read a host capability without raising HostNotBound on a miss."""
    if host is None:
        return None
    try:
        if callable(getattr(host, 'has', None)) and not host.has(name):
            return None
        return getattr(host, name)
    except Exception:
        return None


def live_user(host, username, user=None):
    """The billed seat. Prefer the live users.json row so a thin
    session object cannot hide unlimited / super_admin flags."""
    uname = str(username or '').strip()
    if not uname and isinstance(user, dict):
        uname = str(user.get('username') or user.get('email') or '').strip()
    if not uname and not isinstance(user, dict):
        uname = str(user or '').strip()
    rec = None
    load = _host_cap(host, 'load_users')
    if load is None:
        # the bound host first (a hermetic test binds its own users
        # document there); the app module only as the last resort
        try:
            from .host import host as _gh
            load = _host_cap(_gh, 'load_users')
        except Exception:
            load = None
    if load is None:
        # the web app's own loader, only when the app is already running in
        # this process (the web and the read worker both import it first);
        # a hermetic test never pulls the 73,000-line module in from here
        try:
            import sys as _sys
            _app = _sys.modules.get('app')
            load = getattr(_app, 'load_users', None) if _app is not None else None
        except Exception:
            load = None
    if callable(load) and uname:
        try:
            data = load() or {}
            users = data.get('users') or {}
            rec = users.get(uname)
            if rec is None:
                want = uname.lower()
                for k, v in users.items():
                    if not isinstance(v, dict):
                        continue
                    if str(v.get('email') or '').strip().lower() == want:
                        rec = v
                        break
                    if str(v.get('username') or k).strip().lower() == want:
                        rec = v
                        break
        except Exception:
            rec = None
    if isinstance(rec, dict) and rec:
        out = dict(rec)
        if not out.get('username'):
            out['username'] = uname
        return out
    return user if isinstance(user, dict) else {}


def _pool_unlimited(username, host):
    """True when the seat draws on an unlimited company pool with no
    ceiling of its own (app.check_user_credits -> (True, -1)). Only
    consulted once the web app is loaded (a host capability or the app
    module already imported), so a hermetic test never pulls it in."""
    uname = str(username or '').strip()
    if not uname:
        return False
    chk = _host_cap(host, 'check_user_credits')
    if chk is None:
        try:
            from .host import host as _gh
            chk = _host_cap(_gh, 'check_user_credits')
        except Exception:
            chk = None
    if chk is None:
        try:
            import sys as _sys
            _app = _sys.modules.get('app')
            chk = getattr(_app, 'check_user_credits', None) if _app is not None else None
        except Exception:
            chk = None
    if not callable(chk):
        return False
    try:
        ok, left = chk(uname)
        return bool(ok) and int(left) == -1
    except Exception:
        return False


def seat_is_free(user, username=None, host=None):
    """True when this seat is never billed: staff, super_admin, or
    unlimited (credits == -1, unlimited flag, billing_mode unlimited,
    no_cap, or an unlimited company pool with no ceiling on the seat).
    Looks up the live row when the passed user is thin."""
    u = live_user(host, username, user)
    if staff_user(u):
        return True
    if not isinstance(u, dict) or not u:
        return _pool_unlimited(username, host)
    role = str(u.get('role') or '').strip().lower().replace(' ', '_')
    if role in ('super_admin', 'superadmin'):
        return True
    if bool(u.get('is_super_admin')) or bool(u.get('unlimited')) or bool(u.get('no_cap')):
        return True
    if str(u.get('billing_mode') or '').strip().lower() == 'unlimited':
        return True
    try:
        import wallet as _w
        if _w.is_unlimited(u):
            return True
    except Exception:
        try:
            if int(u.get('credits', 0) or 0) == -1:
                return True
        except (TypeError, ValueError):
            pass
    return _pool_unlimited(username or u.get('username') or u.get('email'), host)


def included_with_plan(user, username=None, host=None):
    """True when priced reads are included with this seat's plan: an
    unlimited seat (personal or company pool) or Crosswalk staff. The
    one check every priced surface asks before it shows a price or an
    Approve chip (Jenna 2026-11-08). A probe identity (the golden set,
    the canary, the replay harness, a sandbox key) is never included: it
    reads the paying seat's shape, which is what gets graded."""
    try:
        uname = str(username or '').strip()
        if not uname and isinstance(user, dict):
            uname = str(user.get('username') or user.get('email') or '').strip()
        if uname:
            from . import probe_identity as _pi
            if _pi.is_probe_user(uname, user if isinstance(user, dict) else None):
                return False
    except Exception:
        pass
    try:
        return bool(staff_user(user) or seat_is_free(user, username, host))
    except Exception:
        return False


def included_charge(username, subject, kind, pull_type, label='', staff=False):
    """The panel_charge an included read carries: zero credits, the
    included label, the quoted price kept for the record only. ``staff``
    is a Crosswalk seat; a covered client seat is never called staff."""
    return {'user': username, 'credits': 0, 'subject': subject, 'kind': kind, 'pull_type': pull_type,
            'label': INCLUDED_LABEL, 'quoted': str(label or ''), 'included': True,
            'staff': bool(staff), 'covered': not bool(staff)}


def with_included_line(reply):
    """'Included with your plan. <reply>' once, never twice."""
    r = str(reply or '').strip()
    if not r:
        return INCLUDED_LINE
    if r.startswith(INCLUDED_LINE):
        return r
    return INCLUDED_LINE + ' ' + r


def charge(host, username, subject, pull_type, credits, description, quantity=1):
    """True when the seat covered it (or is unlimited). False = no funds.
    The ledger line carries the tool (through pull_type) and the subject."""
    uname = str(username or '').strip()
    if not uname:
        return True
    if seat_is_free(None, uname, host):
        return True
    consume = _host_cap(host, 'consume_credit')
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


# ---------------------------------------------------------------- the subject behind a price
# Two weeks of the ask log (2026-10-19 to 10-30) carried quotes on "see
# waht the", "high level on this", "Name of show", "Unique", "What About
# Gender Age and International", "The 8,102,006 Mentions", "The Love
# Hypotheis" ($500, three times, for a follow-up on the thread's own
# read). A price is never put on words that do not name an audience, and
# a misspelling of the thread's subject is the thread's subject.
_JUNK_SUBJECT_WORDS = frozenset((
    'unique', 'name', 'show', 'data', 'audience', 'sample', 'high', 'level', 'just', 'this', 'that', 'these',
    'those', 'it', 'them', 'they', 'see', 'what', 'about', 'the', 'a', 'an', 'of', 'on', 'in', 'and', 'or',
    'mentions', 'results', 'answer', 'numbers', 'figures', 'breakdown', 'trends', 'viewership', 'size',
    'dma', 'county', 'counts', 'panelist', 'panelists', 'related', 'campaign', 'ad', 'read', 'pull', 'profile',
    'waht', 'teh', 'hte', 'wht', 'ont', 'form', 'tihs', 'adn',
))
_QUESTION_LEAD_RX = re.compile(r"^(?:what|which|how|who|where|when|why|is|are|do|does|did|can|could|would|should)\b", re.I)


def looks_like_subject(subject):
    """True when the words could name an audience: a brand, a title, a
    person, a place, a behavior phrase. False for question fragments,
    figures, pronoun phrases, single generic nouns and typo salad."""
    s = ' '.join(str(subject or '').split()).strip(" .,;:'\"")
    if len(s) < 3:
        return False
    if _QUESTION_LEAD_RX.match(s):
        return False
    try:
        from . import segment_sizing as _seg
        if _seg.is_segment_label(s):
            return True       # 'men aged 30 to 55 with household income of $50,000 to $200,000 in blue-collar occupations': the dollar figures are the filter (2026-11-11)
    except Exception:
        pass
    if re.search(r"\d{1,3}(?:,\d{3})+|\d{5,}", s):
        return False
    toks = [t for t in re.findall(r"[A-Za-z0-9&'+.-]+", s)]
    if not toks:
        return False
    low = [t.lower() for t in toks]
    if all(t in _JUNK_SUBJECT_WORDS for t in low):
        return False
    content = [t for t in low if t not in _JUNK_SUBJECT_WORDS]
    if not content:
        return False
    # an all-lowercase phrase of three or more words with no content word
    # longer than three letters is not a name ("see waht the")
    if s == s.lower() and len(toks) >= 3 and max(len(t) for t in content) <= 3:
        return False
    return True


def snap_to_thread_subject(subject, history, page=''):
    """The thread's own subject when ``subject`` is a near spelling of it
    ('The Love Hypotheis' -> 'The Love Hypothesis'); else ``subject``."""
    s = ' '.join(str(subject or '').split())
    if not s or not history:
        return subject
    try:
        import difflib
        from . import subject_rules as _sr
        cands = list(_sr.thread_subjects(history, page) or [])
    except Exception:
        return subject
    sf = _fold(s)
    for c in cands:
        cf = _fold(c)
        if not cf or cf == sf:
            return c if cf == sf else subject
        if min(len(cf), len(sf)) >= 6 and difflib.SequenceMatcher(None, sf, cf).ratio() >= 0.86:
            return c
    return subject


_FOLLOWUP_REF_RX = re.compile(
    r"\b(?:the|that|this|those|these|your) (?:list|read|figure|figures|number|numbers|count|share|result|results|answer|"
    r"film|title|show|audience|viewers|people|cohort|segment|group|one|ones|breakdown|table|top \d+)\b|"
    r"\byou (?:started|began|opened|led|used|quoted|gave|said|listed|showed|pulled|ran)\b|"
    r"^\W*(?:what|how) about\b|^\W*and (?:the|those|them|it|what|how)\b|\b(?:of|for|among) (?:them|those|these)\b|"
    r"\bshould (?:\w+ )?be on the list\b|\bexpect (?:\w+ )?(?:on|in) (?:the|that) list\b", re.I)
_STRONG_REF_RX = re.compile(
    r"\b(?:of|for|among|within|inside) (?:them|those|these|that (?:group|cohort|audience)|the (?:same )?(?:group|cohort|audience|viewers|people))\b|"
    r"\b(?:those|these) (?:viewers|people|readers|listeners|buyers|fans|users|subscribers)\b|\bthey\b|"
    r"\bthe (?:list|film|title|show|read) you (?:started|began|gave|listed|used|opened|led|ran|pulled)\b|\bon the list\b", re.I)
_LAST_READ_RX = re.compile(r"^On [^:]{2,120}:\s|\bUS people\b|\bprojected US audience\b|\bviewers\b|\bpenetration\b", re.I)


def _last_read_turn(history):
    """The most recent agent turn that delivered a read (a job turn, a
    paid read, or an answer opening 'On <subject>:'), or None."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns):
        if str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        txt = ' '.join(str(t.get('text') or '').split())
        if meta.get('read_job_id') or meta.get('priced_paid') or meta.get('panel_charge'):
            return t
        if txt and _LAST_READ_RX.search(txt) and len(txt) > 120 and not meta.get('panel_offer'):
            return t
    return None


def thread_read_subject(history):
    """The subject of the last delivered read in the thread, or ''."""
    t = _last_read_turn(history)
    if not t:
        return ''
    meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
    for src in (meta.get('subject'), (meta.get('priced_paid') or {}).get('subject') if isinstance(meta.get('priced_paid'), dict) else None):
        if src:
            return str(src).strip()
    m = re.match(r"^On ([^:]{2,120}?)(?:\s+-\s+[^:]{1,120})?:\s", ' '.join(str(t.get('text') or '').split()))
    return m.group(1).strip() if m else ''


def is_followup_on_last_read(text, history, subject=''):
    """True when the words point back at the last delivered read and name
    no new audience of their own: a referent phrase, or a figure the
    last reply quoted."""
    t = ' '.join(str(text or '').split())
    last = _last_read_turn(history)
    if not t or last is None:
        return False
    last_txt = ' '.join(str(last.get('text') or '').split())
    # the cohort itself pointed at ("of them", "those viewers", "the list you
    # gave") is a follow-up whatever else the words name (a platform or
    # brand being measured against it)
    if _STRONG_REF_RX.search(t):
        return True
    # a new audience named outright is a new ask, not a follow-up
    subj = str(subject or '').strip()
    rs = thread_read_subject(history)
    if subj and looks_like_subject(subj) and not (rs and (_fold(subj) == _fold(rs) or _fold(subj) in _fold(rs) or _fold(rs) in _fold(subj))):
        return False
    if _FOLLOWUP_REF_RX.search(t):
        return True
    nums = re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,}|\d+(?:\.\d+)?%", t)
    return any(n in last_txt for n in nums)


def offer_or_charge(*, text, subject, kind, panel_confirm, username, user=None,
                    history=None, host=None, consume=None, years=1, dimension='', what='',
                    verb='watched', series=False, condition='', frame='', work='', service=''):
    """The one gate for a priced read on any chat path.

    Returns ('offer', payload) when the ask has not been accepted yet,
    ('charged', panel_charge) once the charge landed on the confirm,
    ('error', payload) when the seat cannot cover it.
    ``kind``: report | custom | path | overlap | list |
    cross | sizing | derived | viewership. The price for the custom
    family and viewership is the thread's: $100 first on this subject,
    metered after (Jenna 2026-10-30).
    """
    kind = str(kind or 'report')
    subj = str(subject or '').strip() or 'that subject'
    if not isinstance(panel_confirm, dict):
        # a misspelling of the thread's subject is the thread's subject; a
        # price never lands on words that name no audience (2026-10-30)
        subj = str(snap_to_thread_subject(subj, history) or subj)
        # a question that points back at the last reply ("the list", "the
        # film you started with", "those viewers", "what about gender and
        # age", a figure it quoted) is a follow-up on that read: metered,
        # never a new quote (2026-10-30 audit, item 4: Jay was quoted $500
        # three times for one follow-up)
        if kind not in ('sizing', 'derived') and is_followup_on_last_read(text, history, subj):
            ts = thread_read_subject(history) or subj
            return 'charged', {'user': username, 'credits': 0, 'subject': ts, 'kind': kind,
                               'pull_type': FOLLOWUP_PULL_TYPE, 'label': METERED_LABEL, 'metered': True,
                               'followup_ref': True}
        if kind not in ('derived',) and not looks_like_subject(subj):
            try:
                from . import subject_rules as _sr
                line = _sr.clarify_for(text)
            except Exception:
                line = ('Which subject should this be on? Name the show, brand or person '
                        '(one line is enough) and I will take it from there.')
            return 'offer', {'success': True, 'action': 'answer', 'reply': line, 'followups': [],
                             'offer_deck': False, 'deck_angle': None, 'subject': '', 'subject_unclear': True}
    # Included with your plan (Jenna 2026-11-08): an unlimited seat (a
    # covered client seat, never called staff) or Crosswalk staff never
    # sees the offer, the price or a chip; the read is charged at zero
    # and starts in the same turn.
    is_staff = staff_user(user)
    included = included_with_plan(user, username, host)
    if kind not in ('sizing', 'derived') and custom_price(username, history, subj).get('metered'):
        # a follow-up on a subject this thread already paid for (custom or viewership): metered, never quoted (Jenna 2026-10-30)
        return 'charged', {'user': username, 'credits': 0, 'subject': subj, 'kind': kind,
                           'pull_type': FOLLOWUP_PULL_TYPE, 'label': METERED_LABEL, 'metered': True}
    if kind == 'viewership':
        try:
            from prometheus.legacy.watch import _pm_viewership_read_price
            vw = _pm_viewership_read_price(text, username)
        except Exception:
            vw = None
        # one flat price for the first viewership read on a subject, whatever the window (Jenna 2026-10-30)
        label, credits = (vw[0], vw[1]) if vw else (_label(_usd('viewership_read', 100.0, username), username), 1)
        qty, pull_type = 1, 'Viewership Read'
    elif kind in ('sizing', 'derived'):
        label, credits = _derived_price(username)
        qty, pull_type = 1, 'Derived Read'
    else:
        p = custom_price(username, history, subj)
        label, credits, qty, pull_type = p['label'], p['credits'], 1, p['pull_type']
    if included:
        # no offer step: the caller runs the read now and opens with INCLUDED_LINE
        return 'charged', included_charge(username, subj, kind, pull_type, label, staff=is_staff)
    if not isinstance(panel_confirm, dict):
        span = 'one year' if int(years or 1) == 1 else f"{int(years)} years"
        reply, chips, off = offer(subj, label, text, kind=kind, frame=frame, years=years,
                                  dimension=dimension, verb=verb, series=series,
                                  condition=condition, span=span, what=what, work=work, service=service)
        return 'offer', {'success': True, 'action': 'answer', 'reply': reply,
                         'followups': chips, 'offer_deck': False, 'deck_angle': None,
                         'panel_offer': off, 'subject': subj}
    rc = panel_confirm.get('resume_charge')
    if isinstance(rc, dict):
        return 'charged', rc
    desc = charge_description(kind, subj, text, dimension=dimension, years=int(years or 1))
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
                       'kind': kind, 'pull_type': pull_type, 'label': label,
                       'description': desc}


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
    xt_plan = None
    if hit and hit.get('frame') == 'crosstab':
        # a cross of behaviors inside one audience (prometheus.crosstab): every behavior binds
        # to its row on the audience's profile; a row the profile does not carry is said
        # plainly. An audience with no profile is not that lane's: the words are read again
        # without it and the standing lanes take the ask as before. A reader who cannot open
        # the profile gets the standing quote below, unchanged.
        from . import crosstab as _xt
        xt_plan = _xt.plan(text, ctx, host=host, history=history)
        if xt_plan and xt_plan.get('reply'):
            return xt_plan['reply']
        if xt_plan and _xt.can_open(user, xt_plan['key'], host):
            # Free (Jenna 2026-11-11: "Build it on the page, free for anyone with the profile"):
            # every behavior is a row on a profile this reader can open, the page sizes the
            # same group at no charge, so chat does too. No quote, no Approve, no wallet touch;
            # the read is answered now and goes on the record like any other. Only this lane,
            # only when every behavior bound: a build, a new pull or a group that is not on a
            # profile keeps its price and its gate.
            free = _xt.answer(text, ctx, host=host, history=history, user=user, plan_=xt_plan)
            if free:
                out = dict(free)
                out['priced_lane'] = 'crosstab'
                out['free_read'] = True
                return out
            xt_plan = None
        if not xt_plan:
            _xt.decline(text)
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
    if xt_plan:
        subject = xt_plan['subject']
    included = False
    if pc is None:
        state, payload = offer_or_charge(text=text, subject=subject, kind=hit['kind'],
                                         panel_confirm=None, username=uname, user=user,
                                         history=history, host=host, frame=hit.get('frame') or '',
                                         what=(sizing_lead(text, ctx) if hit['kind'] == 'sizing'
                                               else (xt_plan['lead'] if xt_plan else '')))
        if state != 'charged':
            return payload
        # no offer step (included with the plan, or a metered follow-up):
        # the read runs in this same turn
        included = bool((payload or {}).get('included'))
    else:
        if hit['kind'] == 'sizing' and pc_kind not in ('sizing', 'derived', ''):
            return None
        state, payload = offer_or_charge(text=text, subject=subject, kind=hit['kind'],
                                         panel_confirm=pc, username=uname, user=user,
                                         history=history, host=host, frame=hit.get('frame') or '')
        if state == 'error':
            return payload
        included = bool((payload or {}).get('included'))
    from . import sizing_lane as _sz
    if xt_plan:
        from . import crosstab as _xt
        raw = _xt.answer(text, ctx, host=host, history=history, user=user, plan_=xt_plan)
    else:
        raw = _sz.answer(text, ctx, host=host, history=history, staff=staff_user(user), username=uname)
    paid = {'kind': hit['kind'], 'frame': hit.get('frame'), 'usd': (0 if included else hit.get('usd')),
            'subject': subject, 'pull_type': (payload or {}).get('pull_type'), 'included': included}
    if not raw:
        # the sizing pass came back with nothing: the reader gets the calm
        # line (the answer comes by email), ops gets the cause. Never "I
        # could not finish that ... ask again" (Jenna 2026-11-04: "it
        # should never say anything like that ever. it should only send
        # it to us.")
        try:
            _mail = _host_cap(host, 'error_email')
            if callable(_mail):
                _mail('prometheus/priced-read',
                      f"priced {hit['kind'].replace('_', ' ')} on {subject} came back with no figures; the user got the "
                      f"calm line and needs the answer by email | {str(text or '')[:200]}")
        except Exception:
            pass
        return {
            'success': True, 'action': 'answer',
            'reply': (with_included_line(CALM_LINE) if included else CALM_LINE),
            'followups': [], 'offer_deck': False, 'deck_angle': None,
            'priced_paid': paid, 'subject': subject, 'held': True,
        }
    out = dict(raw)
    out['priced_paid'] = paid
    out.setdefault('subject', subject)
    out['priced_lane'] = hit['kind']
    if included:
        # the opener an unlimited seat reads instead of a price (Jenna 2026-11-08)
        out['reply'] = with_included_line(out.get('reply') or '')
        out['included_with_plan'] = True
    return out


# ----------------------------------------------------------------- chat helpers
def sizing_offer_payload(text, history=None, username=None, user=None):
    """The interpret surface's sizing pre-check: the $100 offer for an
    audience-size ask, or None when the words are not a sizing ask (or
    the seat's plan includes the read, so no price is ever shown)."""
    try:
        from . import sizing_lane as _sz
    except Exception:
        _sz = None
    hit = classify(text, history)
    if not ((hit and hit.get('kind') == 'sizing') or (
            _sz is not None and _sz.wants_sizing(text) and _sz.names_an_audience(text))):
        return None
    if included_with_plan(user, username, None):
        return None      # Included with your plan (Jenna 2026-11-08): the size runs without a quote
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
    if rrc.get('included') or included_with_plan(None, username, None):
        return {}       # included with the plan: nothing to charge (Jenna 2026-11-08)
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
    if hit and hit.get('frame') == 'crosstab':
        # a cross of behaviors is the sizing lane's (prometheus.crosstab); an ask that got
        # this far was not answered there, so the words are read here without it
        try:
            from . import crosstab as _xt
            _xt.decline(text)
            hit = classify(text, history, ctx)
        except Exception:
            hit = None
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
        if not subject and hit['kind'] == 'viewership':
            # a viewing metric of a named service ("total hours by month for
            # 2025 ... for Prime Video and YouTube") is a read on that
            # service; the words name it even when no reading rode in
            # (Cara Dorfman 2026-11-10)
            subject = platform_subject(words)
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
        # A question about the open profile's own read ("are these people
        # new sign ups", read as a definition that refers to the page) is
        # on the page: the lanes answer it there, metered, never the
        # which-audience question (Grace Yin 2026-11-08, the Hazbin Hotel
        # Season 1 sign-ups profile open; she quoted the file's own cohort
        # figure and window and was asked to pick an audience).
        try:
            page = _page_name(ctx)
            rd = reading
            if rd is None:
                from . import subject_rules as _sr
                rd = _sr._request_reading()
            pointed = ('refers_to_page' in (rd.get('flags') or [])) if isinstance(rd, dict) \
                else bool(_POINTS_AT_PAGE_RX.search(words))
            if page and pointed:
                body['bind_subject'] = page
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
    if kind == 'adaptation':
        work, service = adaptation_details(words)
        extra = {'work': work, 'service': service}
    state, payload = offer_or_charge(text=words, subject=subject, kind=kind, panel_confirm=pc,
                                     username=uname, user=user, history=history, consume=consume,
                                     frame=str(hit.get('frame') or ''), **extra)
    if state == 'offer':
        return payload
    if state == 'error':
        return payload
    if isinstance(pc, dict) and not isinstance(pc.get('resume_charge'), dict):
        pc['resume_charge'] = payload
    if isinstance(payload, dict) and payload.get('included'):
        # Included with your plan (Jenna 2026-11-08): no offer, the read
        # runs now; the service opens the reply with INCLUDED_LINE
        body['_pm_included'] = True
    # The read runs on exactly what was quoted and approved: the subject
    # rides the body as the bound subject so the generation pass never
    # resolves a base of its own (the open page) behind the quote.
    if subject and not bind and not str(body.get('bind_subject') or '').strip():
        body['bind_subject'] = subject
    return None
