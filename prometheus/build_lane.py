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

``kind(text, history)``      'noun_order' | 'correction' | 'instruction' | ''
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

__all__ = ['kind', 'rewrite', 'strip_pasted', 'ASK_LINE']

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
        if _orders.is_profile_order(t):
            return ''                      # the order gate already has it
        if _NOUN_ORDER_RX.match(t) and not t.rstrip().endswith('?'):
            return 'noun_order'
        if _THREAD_ASK_RX.match(t) and _last_was_read(history):
            return 'thread_ask'            # "can we build profile" right after a read: a brief on that read's subject
        if _is_question(t):
            return ''
        try:
            from . import smarter as _sm
            if _sm.negated_build(t):
                return ''                  # "don't build a profile" is the opposite
        except Exception:
            pass
        words = len(t.split())
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
        subj = _named_profile(words) if k == 'instruction' else ''
        if subj:
            win = ''
            try:
                from . import order_referent as _oref
                win = _oref.window_in(words)
            except Exception:
                win = ''
            out, source = f"Please queue one Profile IQ: {subj}" + (f", {win}" if win else ''), 'words'
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
        return {'text': out, 'kind': k, 'source': source}
    except Exception:
        return None
