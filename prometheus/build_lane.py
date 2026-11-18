"""An order for a profile goes to the build step, and so does a reader
who says the lane was wrong (Jenna 2026-11-11).

Jessie, thread ebf520b02f, with a profile open on her screen:

  22:14 UTC  "One profile of Georgie & Mandy's First Marriage Viewers
             ONLY on Paramount+. Using the 49 Paramount Plus Placements
             you see in the content_map"            -> a read
  23:04 UTC  "it's wrong. you only ran the profile on the 5 Paramount
             Plus placements. The profile of ... ONLY on Paramount+.
             Window: Oct 25, 2024 to Nov 10, 2026. needs to be run on
             all 49 Paramount Plus placements. Here they are: ..."
                                                    -> a read
  23:05 UTC  "NO! not a read. a full profile"       -> a read

The cause: with a profile on the page the client sits on the analyze
surface, and only an order ``prometheus.orders`` recognizes is handed
to the build flow. Those three are orders it did not know: a profile
named with no verb, an instruction about how the build must be run,
and a correction of the lane. Everything else with a page open is read
as a question about the page.

The rule: these are orders for a profile, and they route to the build
interpret step exactly as "run a profile on X" does. Nothing here
chooses what the build is: the interpret step's decision (existing
match, cut, refresh, new build) stays the authority and no flag rides
the request. This module only hands that step an order it can read:

  * a profile named with no verb ("One profile of X only on Paramount+",
    "a full profile of X") becomes the order on the same words;
  * a correction of the lane ("not a read", "a full profile", "I want
    the profile", "I asked for a profile") and an instruction about the
    run ("needs to be run on all 49", "you only ran it on 5", "use all
    49 placements") become the order the reader last gave in this
    thread, in their own words; when the thread holds no order, the
    subject of its last read; when it holds neither, the audience on
    screen;
  * when nothing names a subject the reply is one plain line asking for
    it. Never a read.

A pasted list of ids rides out of the order (it is not a subject); the
count is kept in a note so the build step knows the reader sent one.

  * the profile itself asked for on whatever the thread is on ("its not
    there you need to pull the full profile", "pull the full profile",
    "I need the full profile for this", "build the profile", 2026-11-17)
    becomes the order for the subject the reader was last working on:
    the order they gave for it in this thread, else the subject of the
    newest read, else the audience on screen. A subject taken from a
    read or the screen, or a profile asked for as a question, is shown
    as a brief for the reader's yes. A plain "its not there" / "where
    is my read" / "is it done" names no profile and keeps the status
    answer.

``kind(text, history)``      'noun_order' | 'correction' | 'instruction' | 'thread_ask' | 'full_profile' | ''
``rewrite(text, history, ctx)``
                             {'text': the order, 'kind', 'source'} or
                             {'ask': the one line, 'kind'} or None when
                             the words are none of these.

Pure text plus the thread it is handed. No model, no S3. Never raises.
Regression: scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import re

from . import orders as _orders

__all__ = ['kind', 'rewrite', 'strip_pasted', 'bare_demand', 'keep_order_route', 'ASK_LINE']

ASK_LINE = ("Which audience should the profile be on? Name the show, brand or person (and the platform or window if "
            "you have one) and I will line up the build.")
MAX_CHARS = 6000
_LOOKBACK_USER_TURNS = 8

_QUESTION_RX = re.compile(
    r"^\W*(?:(?:hey|hi|ok(?:ay)?|so|and|also|but)[,:!.\s]+)*(?:did|do|does|have|has|had|is|are|was|were|will|would|should|"
    r"when|where|why|who|what|which|how|can|could)\b", re.I)
_LEAD = r"(?:(?:no+|nope|nah|not that|wait|actually|ok(?:ay)?|so|please|pls|again|sorry)[,.!\s]+)*"
# a profile named with no verb: "One profile of X ...", "a full profile on X", "a new Profile IQ profile for X"
_NOUN_ORDER_RX = re.compile(
    r"^\W*" + _LEAD + r"(?:just\s+)?(?:one|1|a|an|another|single|new|full)\s+(?:(?:new|full|single|separate|whole|complete|"
    r"proper|real)\s+)*(?:profile\s+iq\s+)?profile(?:\s+iq)?\s+(?:of|on|for)\s+(?P<rest>\S[\s\S]*)$", re.I)
# the lane was wrong
_NOT_READ_RX = re.compile(
    r"\bnot\s+(?:a|an|another|the|just\s+a)?\s*(?:quick\s+|short\s+|small\s+)?(?:read|analysis|summary|answer|estimate|look)\b|"
    r"\b(?:i\s+)?(?:did\s*n[o']?t|didn'?t|never)\s+(?:ask(?:ed)?|want(?:ed)?|order(?:ed)?)\s+(?:for\s+)?(?:a|an|another)\s+read\b|"
    r"\bno\s+(?:more\s+)?reads?\b|\bstop\s+(?:giving|sending)\s+me\s+reads?\b|\binstead\s+of\s+a\s+read\b", re.I)
_WANT_PROFILE_RX = re.compile(
    r"\b(?:a|the|one|my|that|this)\s+(?:(?:full|whole|new|real|actual|complete|proper|entire|separate)\s+)*"
    r"(?:profile\s+iq\s+profile|profile\s+iq|profile|build)\b|\bfull\s+profile\b|"
    r"\bbuild\s+(?:it|the\s+profile|that|this)\b|\b(?:run|make|create|queue)\s+(?:it|the\s+profile)\b|"
    r"\bi\s+(?:want|need|asked\s+for|ordered|requested)\s+(?:a|the|an?other)\s+(?:\w+\s+){0,2}profile\b", re.I)
# how the build must be run
_INSTRUCTION_RX = re.compile(
    r"\b(?:needs?|has|have)\s+to\s+be\s+(?:re-?)?(?:run|built|pulled)\s+(?:on|with|using|from|across)\b|"
    r"\byou\s+only\s+(?:ran|built|pulled|used|ran\s+it|ran\s+the\s+profile)\b|"
    r"\b(?:re-?)?(?:run|build|pull)\s+(?:it|the\s+profile|the\s+build)\s+(?:again\s+)?(?:on|with|using|from|across)\s+all\b|"
    r"\buse\s+all\s+(?:\d+\s+)?(?:of\s+(?:the|those|these)\s+)?(?:paramount\s*(?:\+|plus)\s+|hulu\s+|netflix\s+|\w+\s+)?"
    r"(?:placements?|inputs?|urls?|seeds?|episodes?|ids?)\b", re.I)
_PROFILE_OF_RX = re.compile(
    r"\bprofile\s+(?:of\s+(?:r?ofile\s+of\s+)?|on\s+|for\s+)(?P<subj>[^.\n]{3,200}?)(?=\s*(?:\.|\n|$|\bneeds?\s+to\b|\bwindow\s*:|"
    r"\bdate\s+range\b))", re.I)
# what follows "profile on" in a complaint about the run is not a subject ("you only ran the profile on the 5 placements")
_NOT_A_SUBJECT_RX = re.compile(r"^(?:only\s+)?(?:the\s+|all\s+|just\s+)*(?:\d+|those|these|them|it|that|this)\b|"
                               r"\b(?:placements?|inputs?|urls?|seeds?|ids?)\b", re.I)


def _named_profile(words):
    """The subject a 'profile of X' clause names, or ''."""
    for m in _PROFILE_OF_RX.finditer(words or ''):
        subj = _squash(m.group('subj')).strip(' .,;:')
        if subj and len(subj.split()) <= 24 and not _NOT_A_SUBJECT_RX.search(subj):
            return subj
    return ''
# a pasted id: one token of letters, digits, _ and - with no spaces, twenty or more characters
# the whole message is the want, or the refusal of a read, and nothing else
_WANT_ONLY_RX = re.compile(
    r"^\W*" + _LEAD + r"(?:(?:i|we)\s+(?:want|need|asked\s+for|ordered|requested|meant)\s+)?(?:just\s+)?"
    r"(?:(?:a|the|one|my)\s+(?:(?:full|whole|new|real|actual|complete|proper|entire)\s+)*(?:profile\s+iq\s+profile|profile\s+iq|profile)|"
    r"full\s+profile|build\s+(?:it|the\s+profile)|(?:run|make|create|queue)\s+the\s+profile|make\s+it\s+a\s+profile)"
    r"(?:\s+(?:please|instead|now))?\W*$", re.I)
_NOT_READ_ONLY_RX = re.compile(
    r"^\W*" + _LEAD + r"(?:this\s+is\s+|that\s+is\s+|that'?s\s+|it'?s\s+)?not\s+(?:just\s+)?(?:a|another)\s+read\W*$", re.I)
# "can we build profile", "can you run a profile on this", "could we get a full profile for them": a profile asked
# for behind a can-we frame, naming no subject of its own (Armine 2026-11-11, one turn after a read on Mistletoe
# Murders Viewers; she was told what a Profile IQ profile is and asked for the subject)
_THREAD_ASK_RX = re.compile(
    r"^\W*" + _LEAD + r"(?:can|could|would|will|shall|may)\s+(?:we|you|i|u)\s+(?:please\s+|now\s+|also\s+|just\s+)*"
    r"(?:build|run|make|create|pull|get|do|have|start|queue|order)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|that\s+|this\s+)?"
    r"(?:(?:full|new|whole|complete|proper)\s+)?(?:profile\s+iq\s+profile|profile\s+iq|profile)"
    r"(?:\s+(?:on|for|of|from)\s+(?:this|that|it|them|these|those|the\s+same|this\s+(?:audience|read|one)|that\s+(?:audience|read|one)|"
    r"the\s+(?:audience|read|above)))?(?:\s+(?:please|now|too|as\s+well))?\W*$", re.I)
# The profile itself is asked for, on whatever the thread is on (2026-11-17, a guest on Jenna's seat, thread
# a2673cecd7: after two reads on luxury resort engagers and directions to Profile IQ, "its not there you need to
# pull the full profile" was read by the status lane for "its not there" and answered with the list of finished
# reads). "pull the full profile", "you need to build the profile", "I need the full profile for this", "create
# the profile": a build verb on "the profile", or a want of "the full profile". Never the file ("the profile
# csv"), the page ("the profile tab") or "pull up the profile".
_DEMAND_VERB = r"(?:re-?)?(?:pull|run|build|create|make|queue|generate|produce|start)"
_DEMAND_FULL = r"(?:full|whole|complete|entire|actual|real|proper|new)"
_DEMAND_NOUN = (r"(?:profile\s+iq\s+profile|profile\s+iq|profile)\b"
                r"(?!\s+(?:iq\s+)?(?:profile\s+)?(?:tab|page|picker|dropdown|selector|view|screen|menu|button|link|csv|file|pdf|deck|"
                r"name|list|picture|image)\b)")
# "the profile" or "a full profile": never a bare "a profile" (that names nothing the thread holds)
_DEMAND_ART = (r"(?:(?:the|that|this|my|our)\s+(?:" + _DEMAND_FULL + r"\s+)*|(?:(?:a|an|one)\s+)?(?:" + _DEMAND_FULL + r"\s+)+)")
_DEMAND_BODY = (
    r"(?:" + _DEMAND_VERB + r"\s+(?:me\s+|us\s+)?" + _DEMAND_ART + _DEMAND_NOUN + r"|"
    r"(?:need|want|get|give|send|provide)\s+(?:me\s+|us\s+)?(?:(?:a|an|the|that|this|my|our|one)\s+)?(?:" + _DEMAND_FULL + r"\s+)+"
    + _DEMAND_NOUN + r")")
_DEMAND_RX = re.compile(r"\b" + _DEMAND_BODY, re.I)
# the same thing asked politely ("its not there, can you pull the full profile?"): the brief waits for the yes
# ("when will you build the profile?" asks about it: the request opens its own clause)
_POLITE_DEMAND_RX = re.compile(
    r"(?:^\W*|[.!?;,]\s+|\b(?:please|so|and|then|but|ok(?:ay)?)\s+)(?:can|could|would|will)\s+(?:you|we|u)\s+"
    r"(?:(?:please|just|now|then|go\s+ahead\s+and)\s+)*" + _DEMAND_BODY, re.I)
_DEMAND_MAX_WORDS = 40
_LEAD_NO_RX = re.compile(r"^\W*(?:no+|nope|nah)\b[,.!:;\s]+", re.I)
_OTHER_PRODUCT_RX = re.compile(r"\b(?:deck|one[- ]pager|journey|twin|valuation|tracker|focus group|campaign)\b", re.I)
_PASTED_LINE_RX = re.compile(r"^\s*[A-Za-z0-9_\-]{20,}\s*$")
_PASTED_INLINE_RX = re.compile(r"(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}(?![A-Za-z0-9_\-])")
_BUILD_LINE_RX = re.compile(r"^\s*(?:Profile building:|Queued:)|\bis building\b|\bis finished and ready in the Select Profile\b", re.I)
_READ_TURN_RX = re.compile(r"^\s*(?:On it\b|Included with your plan\. On it\b|On\s+[^\n:]{2,140}:)", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def strip_pasted(text):
    """(the words with a pasted list of ids taken out, how many ids)."""
    raw = str(text or '')
    lines, n = [], 0
    for ln in raw.replace('\r', '\n').split('\n'):
        if _PASTED_LINE_RX.match(ln):
            n += 1
            continue
        lines.append(ln)
    out = '\n'.join(lines)
    more = _PASTED_INLINE_RX.findall(out)
    if more:
        n += len(more)
        out = _PASTED_INLINE_RX.sub(' ', out)
    out = re.sub(r"\b(?:here\s+they\s+are|they\s+are|listed\s+below|as\s+follows)\s*:?\s*$", '', _squash(out), flags=re.I).strip(' :,;')
    return out, n


def _is_question(t):
    core = t.strip()
    return core.endswith('?') or bool(_QUESTION_RX.match(core))


def _last_agent(history):
    for t in reversed(list(history or [])):
        if isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant'):
            return t
    return None


def _thread_has_build(history):
    """True when this thread ordered or ran a profile build."""
    for t in list(history or [])[-40:]:
        if not isinstance(t, dict):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        if str(t.get('role') or '') == 'user':
            core, _n = strip_pasted(txt)
            if _orders.is_profile_order(core) or _NOUN_ORDER_RX.match(core):
                return True
        else:
            meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
            if meta.get('run_id') or meta.get('build_run_id') or _BUILD_LINE_RX.search(txt[:300]):
                return True
    return False


def _last_was_read(history):
    prev = _last_agent(history)
    if not prev:
        return False
    meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
    txt = str(prev.get('text') or prev.get('content') or '')
    return bool(meta.get('read_job_id') or meta.get('kind') == 'read' or _READ_TURN_RX.match(txt))


_DEMAND_TAIL_RX = re.compile(r"^(?:\W|\b(?:profile|iq|please|pls|now|today|asap|thanks?|thank\s+you|for\s+me|for\s+us|instead)\b)*$", re.I)


def bare_demand(t):
    """True when the words ask for 'the full Profile IQ profile' and name
    nothing after it: the order gate reads the trailing 'profile' as a
    subject, and there is none."""
    m = _DEMAND_RX.search(t)
    return bool(m) and bool(_DEMAND_TAIL_RX.match(t[m.end():]))


def kind(text, history=None):
    """What kind of profile order these words are, when
    ``orders.is_profile_order`` does not already know them."""
    try:
        raw = str(text or '')
        if not raw.strip() or len(raw) > MAX_CHARS:
            return ''
        t, _n = strip_pasted(raw)
        if not t:
            return ''
        if _orders.is_profile_order(t) and not bare_demand(t):
            return ''                      # the order gate already has it
        if _NOUN_ORDER_RX.match(t) and not t.rstrip().endswith('?'):
            return 'noun_order'
        if _THREAD_ASK_RX.match(t) and _last_was_read(history):
            return 'thread_ask'            # "can we build profile" right after a read: a brief on that read's subject
        negated = False
        try:
            from . import smarter as _sm
            negated = bool(_sm.negated_build(t))   # "don't build a profile" is the opposite
        except Exception:
            negated = False
        words = len(t.split())
        if _is_question(t):
            # "its not there, can you pull the full profile?" asks for the profile; "did you pull the full
            # profile?" and "where is the full profile" ask about it and keep their lanes
            if not negated and words <= _DEMAND_MAX_WORDS and _POLITE_DEMAND_RX.search(t):
                return 'full_profile'
            return ''
        if negated:
            return ''
        not_read, want = bool(_NOT_READ_RX.search(t)), bool(_WANT_PROFILE_RX.search(t))
        if not_read and want and words <= 40:
            return 'correction'            # "NO! not a read. a full profile"
        if _NOT_READ_ONLY_RX.match(t) and (_last_was_read(history) or _thread_has_build(history)):
            return 'correction'            # "No. Not a read." after a read
        if _WANT_ONLY_RX.match(t) and _last_was_read(history):
            if re.match(r"^\W*" + _LEAD + r"build\s+it\b", t, re.I) and _OTHER_PRODUCT_RX.search(
                    str((_last_agent(history) or {}).get('text') or '')[-400:]):
                return ''                  # "build it" after an offer of a deck or a journey is that offer's
            return 'correction'            # "a full profile", "I want the profile", "build it" right after a read
        if _INSTRUCTION_RX.search(t) and _thread_has_build(history):
            return 'instruction'
        if words <= _DEMAND_MAX_WORDS and _DEMAND_RX.search(t):
            return 'full_profile'          # "its not there you need to pull the full profile", "build the profile"
        return ''
    except Exception:
        return ''


def _order_from(words):
    """The order a build step reads, from words that name a profile with
    no verb. '' when they name none."""
    m = _NOUN_ORDER_RX.match(words)
    if m:
        rest = _squash(m.group('rest')).strip(' .,;:')
        return f"Please queue one Profile IQ: {rest}" if rest else ''
    return ''


def _from_thread(history):
    """(order text, source) from the newest earlier message in this thread
    that asked for a profile and named its subject."""
    seen = 0
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') != 'user':
            continue
        seen += 1
        if seen > _LOOKBACK_USER_TURNS:
            break
        core, _n = strip_pasted(str(t.get('text') or t.get('content') or ''))
        if not core:
            continue
        if _orders.is_profile_order(core) and not _orders.referent(core) and len(core) <= 700:
            return core, 'thread_order'
        got = _order_from(core)
        if got:
            return got, 'thread_order'
        subj = '' if _is_question(core) else _named_profile(core)
        if subj:
            win = ''
            try:
                from . import order_referent as _oref
                win = _oref.window_in(core)
            except Exception:
                win = ''
            return f"Please queue one Profile IQ: {subj}" + (f", {win}" if win else ''), 'thread_order'
    return '', ''


def _latest_subject_order(history):
    """(order, source) for the subject the reader was last working on.

    The reader's newest profile order in this thread wins, in their own
    words, while every read that landed after it answered that order or
    a follow-up on it. A read on another question that landed after the
    order (the reader moved on) makes that read's subject the one, and a
    thread with reads and no order uses its newest read. ('', '') leaves
    the choice to the older rules."""
    try:
        turns = [t for t in (history or []) if isinstance(t, dict)]
        order_at, order_text = -1, ''
        seen = 0
        for i in range(len(turns) - 1, -1, -1):
            t = turns[i]
            if str(t.get('role') or '') != 'user':
                continue
            seen += 1
            if seen > _LOOKBACK_USER_TURNS:
                break
            core, _n = strip_pasted(str(t.get('text') or t.get('content') or ''))
            if not core or len(core) > 700:
                continue
            if _orders.is_profile_order(core) and not _orders.referent(core):
                # "No pull a new profile IQ for ...": the no answered the reply before it; the order is what follows
                bare = _LEAD_NO_RX.sub('', core, count=1)
                order_at, order_text = i, (bare if bare != core and _orders.is_profile_order(bare) else core)
                break
            got = _order_from(core)
            if got:
                order_at, order_text = i, got
                break
        from . import order_referent as _oref
        subj, _txt, question = _oref._last_read(history)
        read_at = -1
        if subj:
            for i in range(len(turns) - 1, -1, -1):
                t = turns[i]
                if str(t.get('role') or '') != 'user' and _oref._is_landed_read(t):
                    read_at = i
                    break
        if order_text and (read_at < order_at or not subj or _same_ask(question, turns[order_at])
                           or _shares_subject(subj, order_text)):
            return order_text, 'thread_order'
        if subj and read_at >= 0:
            return _orders.with_subject('build a profile of them', subj,
                                        platform=_oref.platform_in(question) or _oref.platform_in(subj),
                                        window=_oref.window_in(question)), 'thread_read'
        return (order_text, 'thread_order') if order_text else ('', '')
    except Exception:
        return '', ''


def _same_ask(question, turn):
    a = _squash(question).lower()
    b = _squash(strip_pasted(str((turn or {}).get('text') or (turn or {}).get('content') or ''))[0]).lower()
    return bool(a) and (a == b or a in b or b in a)


# the words a read's label and an order can share without being about the same thing
_GENERIC_SUBJECT_WORDS = frozenset((
    'with', 'from', 'that', 'this', 'these', 'those', 'their', 'them', 'they', 'have', 'more', 'less', 'over', 'under', 'into',
    'income', 'household', 'people', 'users', 'viewers', 'audience', 'profile', 'profiles', 'engagers', 'engaged', 'touchpoint',
    'touchpoints', 'year', 'years', 'window', 'date', 'range', 'only', 'cross', 'brand', 'brands', 'company', 'full', 'pull',
    'please', 'queue', 'build', 'read', 'reads', 'data', 'digital', 'least', 'each', 'four', 'three', 'both', 'what', 'which',
    'show', 'series', 'title', 'titles', 'fans', 'buyers', 'shoppers', 'customers', 'watched', 'watch', 'last', 'months'))


def _subject_words(text):
    out = set()
    for w in re.findall(r"[a-z0-9][a-z0-9'&+]{3,}", str(text or '').lower()):
        w = w.rstrip("s'") if len(w) > 4 else w
        if w and w not in _GENERIC_SUBJECT_WORDS and not w.isdigit():
            out.add(w)
    return out


def _shares_subject(read_subject, order_text):
    """True when the read that landed after the order is on the order's own
    audience (its label shares a naming word with the order): a follow-up
    on the same subject, so the order stands. 'Landman' after an order on
    four resort brands shares none: the reader moved on."""
    a, b = _subject_words(read_subject), _subject_words(order_text)
    return bool(a) and bool(a & b)


def _from_read_or_page(history, ctx):
    try:
        from . import order_referent as _oref
        subj, _txt, question = _oref._last_read(history)
        if subj:
            return _orders.with_subject('build a profile of them', subj, platform=_oref.platform_in(question) or _oref.platform_in(subj),
                                        window=_oref.window_in(question)), 'thread_read'
    except Exception:
        pass
    try:
        from . import audience_referent as _ar
        subj = _ar.screen_audience(ctx)
        if subj:
            return _orders.with_subject('build a profile of them', subj), 'screen'
    except Exception:
        pass
    return '', ''


def _instruction_note(words, n_pasted):
    """The reader's instruction about the run, as one trailing sentence."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", words) if s.strip()]
    keep = [s for s in sents if _INSTRUCTION_RX.search(s)]
    note = _squash(' '.join(keep))[:300].rstrip(' .')
    if n_pasted and not re.search(r"\b\d+\b", note):
        note = (note + f" ({n_pasted} listed)").strip()
    return note


def rewrite(text, history=None, ctx=None):
    """The order the build step reads, or the one line that asks for the
    subject, or None."""
    try:
        k = kind(text, history)
        if not k:
            return None
        words, n_pasted = strip_pasted(text)
        if k == 'noun_order':
            out = _order_from(words)
            return {'text': out, 'kind': k, 'source': 'words'} if out and _orders.is_order(out) else None
        if k == 'thread_ask':
            # the subject is the thread's own: the read just delivered. The brief comes back for the reader to
            # confirm (`confirm`): it was asked as a question, so nothing starts on its own. With no subject on
            # the thread the lane stands aside and the question is answered as it always was.
            out, source = _from_read_or_page(history, None)
            if out and _orders.is_order(out):
                return {'text': out, 'kind': k, 'source': source, 'confirm': True}
            return None
        out, source = '', ''
        # the correcting words may name the profile themselves ("... The profile of X ONLY on Paramount+ ...")
        subj = _named_profile(words) if k in ('instruction', 'full_profile') else ''
        if subj:
            win = ''
            try:
                from . import order_referent as _oref
                win = _oref.window_in(words)
            except Exception:
                win = ''
            out, source = f"Please queue one Profile IQ: {subj}" + (f", {win}" if win else ''), 'words'
        if not out and k == 'full_profile':
            # the subject the reader was last working on: the order they gave for it in this thread, in their own
            # words, unless a read on something else landed after it; then that read's subject
            out, source = _latest_subject_order(history)
        if not out:
            out, source = _from_thread(history)
        if not out:
            out, source = _from_read_or_page(history, ctx)
        if not out or not _orders.is_order(out):
            return {'ask': ASK_LINE, 'kind': k}
        if k == 'instruction':
            note = _instruction_note(words, n_pasted)
            if note and note.lower() not in out.lower():
                out = out.rstrip(' .') + '. ' + note + '.'
        res = {'text': out, 'kind': k, 'source': source}
        if k == 'full_profile' and (source in ('thread_read', 'screen') or _is_question(words)):
            # a subject taken from a read or the screen, or a profile asked for as a question: the brief is shown
            # for the reader's yes, nothing starts on its own
            res['confirm'] = True
        return res
    except Exception:
        return None


def keep_order_route(route, body, text, hint=None):
    """The route the build step's own router chose, unless it would turn
    an order for a profile into a read.

    An order is queued, never deflected (Jenna 2026-11-10). The build
    step asks its router what an ask that reached it wants; an ask whose
    words read as analysis, on a subject the library seems to hold, is
    sent to a read ('generate'). "Please pull a group who has engaged
    with Auberge Resorts, Discovery Land Company, Aman Resorts and Nobu
    Hotels ... only those who have a household income of 250k+" read
    that way, and the library profile with the nearest name stood in as
    its subject (2026-11-17). Words the service already read as an order
    for a profile (`_pm_order` on the request) are drafted as that
    order. Every other route is returned as it came. Never raises."""
    r = str(route or '')
    try:
        if r == 'generate' and isinstance(body, dict) and body.get('_pm_order') \
                and _orders.is_profile_order(str(body.get('_pm_order_text') or text or '')):
            if callable(hint):
                try:
                    hint(route='build_order', outcome='kept_from_read')
                except Exception:
                    pass
            return 'build_interpret'
    except Exception:
        pass
    return r
