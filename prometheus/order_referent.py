"""A referent in an order resolves before the build flow reads it (2026-11-10).

The Sony Television seat, 19:28 UTC: "Can you look into My Life With the
Walter Boys viewers? I want to add them as a profile". The second
sentence is the order; "them" is the audience the first sentence named.
Prometheus ran a read and never queued the build.

The rule: an order whose subject slot holds a referent ("them", "this",
"it", "this audience", "these viewers": ``orders.referent``) resolves
the way the thread's pronouns resolve everywhere else (prometheus
.last_thing, the thread_followups lane, prometheus.audience_referent):

  1. the subject named earlier in the SAME message, before the order
     sentence ("Can you look into X viewers? I want to add them as a
     profile" -> X viewers);
  2. the last read in the thread: the newest landed read's subject
     (meta.subject or the "On X:" lead), with the consumer noun the
     question that drew it used (viewers, listeners, readers, players);
  3. the audience open on the reader's screen.

The platform rides when the reader's own words named one (the order,
the sentence before it, the question that drew the read) or the read's
subject label carries it ("Gilmore Girls Viewers ON HULU"); the window
rides when the reader's words set one (prometheus.inline_dates). Nothing
the reader did not say is invented: with no platform the interpret
step resolves where the title lives; with no window the standing
default applies. ``rewrite`` hands back the order in the plain shape
the build flow binds (``orders.with_subject``), or None when nothing
resolves, in which case the build flow's own one-line clarifier asks.

Pure: no model, no S3, no host. Never raises. Regression:
scripts/test_pm_order_referents.py.
"""
from __future__ import annotations

import re

from . import orders as _orders

__all__ = ['resolve', 'rewrite', 'platform_in', 'window_in', 'consumer_noun']

# the streaming, audio, reading and gaming surfaces a reader names a title on
_PLATFORMS = (
    ('Netflix', r"netflix"),
    ('Hulu', r"hulu"),
    ('Prime Video', r"(?:amazon\s+)?prime\s+video|amazon\s+prime|prime"),
    ('Max', r"hbo\s+max|\bmax\b|hbo"),
    ('Disney+', r"disney\s*\+|disney\s+plus|disney"),
    ('Peacock', r"peacock"),
    ('Paramount+', r"paramount\s*\+|paramount\s+plus|paramount"),
    ('Apple TV+', r"apple\s+tv\s*\+?|apple\s+tv\s+plus"),
    ('YouTube', r"youtube"),
    ('Tubi', r"tubi"),
    ('Pluto TV', r"pluto\s+tv|pluto"),
    ('The Roku Channel', r"roku"),
    ('Starz', r"starz"),
    ('AMC+', r"amc\s*\+|amc\s+plus"),
    ('Spotify', r"spotify"),
    ('Apple Podcasts', r"apple\s+podcasts"),
    ('SiriusXM', r"sirius\s*xm|siriusxm"),
    ('Pandora', r"pandora"),
    ('Audible', r"audible"),
    ('Kindle', r"kindle"),
    ('Twitch', r"twitch"),
    ('Steam', r"\bsteam\b"),
    ('PlayStation', r"playstation|\bps5\b|\bps4\b"),
    ('Xbox', r"xbox"),
)
_PLATFORM_RX = [(label, re.compile(r"\b(?:on|via|through|from|at)\s+(?:the\s+)?(?:" + rx + r")\b", re.I),
                 re.compile(r"\b(?:" + rx + r")\s+(?:viewers|watchers|subscribers|listeners|readers|players|users|audience)\b", re.I),
                 re.compile(r"\bon\s+(?:" + rx + r")\b|\b(?:" + rx + r")\s+viewers\b", re.I))
                for label, rx in _PLATFORMS]
_NOUN_RX = re.compile(
    r"\b(?P<noun>viewers|watchers|listeners|readers|players|fans|subscribers|buyers|shoppers|customers|users|members|"
    r"streamers|gamers|attendees|followers|audience)\b", re.I)
# the question openers a sentence before the order wears
_OPENER_RX = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok(?:ay)?|so|and|also|but|quick question|question)[,:!.\s]+)*"
    r"(?:(?:can|could|would|will) you\s+(?:please\s+)?(?:look (?:into|at|up)|check(?: on| out)?|pull up|tell me about|"
    r"dig into|research|read|size|see|show me|give me a read on|run a read on)|"
    r"(?:tell me about|what about|how about|who (?:are|is|watches|watched|listens to|reads|plays)|"
    r"how (?:big|large|many|much) (?:is|are)|what does|what is|what are|i(?:'m| am) curious about|"
    r"look (?:into|at|up)|let'?s look at)\s+)(?:the\s+)?", re.I)
_TRAIL_RX = re.compile(r"\s*(?:\?|\.|!|,)+\s*$")
_SENT_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Za-z0-9\"'(\[])|\n+")
_DEIXIS = {'them', 'this', 'it', 'these', 'those', 'that', 'this one', 'that one', 'the same', 'this audience',
           'these viewers', 'this group', 'the data', 'the read', 'the numbers'}


def _clean(s):
    return ' '.join(str(s or '').split()).strip()


def platform_in(text):
    """The platform the words name a title on ('Gilmore Girls Viewers ON
    HULU' -> 'Hulu'; 'who watched Fallout on Prime Video' -> 'Prime
    Video'), else ''. Only a platform beside a watch word or an 'on'
    counts; a title that happens to carry the word does not."""
    t = _clean(text)
    if not t:
        return ''
    for label, on_rx, noun_rx, _any in _PLATFORM_RX:
        if on_rx.search(t) or noun_rx.search(t):
            return label
    return ''


def window_in(text, today=None):
    """The window the words set, as the house label ('Nov 1 2025 to Oct
    31 2026'), else ''. A date that describes the subject ('the show
    that premiered in January 2024') is not a window (inline_dates)."""
    try:
        from . import inline_dates as _idt
        w = _idt.parse_inline_window(text, today=today)
        if not w:
            return ''
        return _idt.window_label(w['start'], w['end']) or ''
    except Exception:
        return ''


def consumer_noun(text):
    """'viewers' in 'who watched X' / 'X viewers'; '' when the words carry no consumer noun."""
    t = _clean(text)
    m = _NOUN_RX.search(t)
    if m:
        return m.group('noun').lower()
    if re.search(r"\b(?:watch|watched|watches|watching|stream|streamed|streaming)\b", t, re.I):
        return 'viewers'
    if re.search(r"\b(?:listen|listened|listens|listening)\b", t, re.I):
        return 'listeners'
    if re.search(r"\b(?:read|reads|reading)\s+(?:the\s+)?(?:book|novel|comic|manga|series)\b", t, re.I):
        return 'readers'
    if re.search(r"\b(?:play|played|plays|playing)\b", t, re.I):
        return 'players'
    return ''


def _with_noun(subject, noun):
    s = _clean(subject)
    if not s or not noun:
        return s
    if _NOUN_RX.search(s):
        return s
    if re.search(r"\((?:[^)]*\b(?:cut|universe|vs)\b[^)]*)\)", s, re.I):
        return s          # a read label like 'X (Season 3 cut vs full viewers universe)' names its own universe
    return f"{s} {noun}"


def _subject_in_sentence(sentence, page=''):
    """The subject a sentence before the order names, else ''."""
    s = _clean(sentence)
    if not s:
        return ''
    try:
        from . import subject_rules as _sr
        b = _sr.bind(s, None, page)
        disp = _clean(getattr(b, 'display', '') or '')
        if disp and str(getattr(b, 'source', '') or '') == 'words' and disp.lower() not in _DEIXIS:
            return disp
    except Exception:
        pass
    stripped = _OPENER_RX.sub('', s, count=1)
    stripped = _TRAIL_RX.sub('', stripped).strip()
    if stripped and stripped != s and stripped.lower() not in _DEIXIS and re.search(r"[A-Za-z]{3}", stripped) \
            and len(stripped.split()) <= 14:
        return stripped
    return ''


def _own_message(text, page=''):
    """(subject, words) from the sentences before the order sentence."""
    t = _clean(text)
    sents = [x.strip() for x in _SENT_SPLIT_RX.split(t) if x.strip()]
    order_i = None
    for i, s in enumerate(sents):
        if _orders.is_order(s) and _orders.referent(s):
            order_i = i
            break
    if order_i is None or order_i == 0:
        return '', ''
    before = sents[:order_i]
    for s in reversed(before):
        subj = _subject_in_sentence(s, page)
        if subj:
            return subj, ' '.join(before)
    return '', ' '.join(before)


def _is_landed_read(turn):
    try:
        from .read_landing import _is_landed_turn as _landed
        if _landed(turn):
            return True
    except Exception:
        pass
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    return bool(meta.get('read_job_id')) and str(turn.get('role') or '') in ('agent', 'assistant') \
        and len(_clean(turn.get('text'))) >= 60


def _last_read(history):
    """(subject, read text, the user question that drew it) for the
    newest landed read in the thread, else ('', '', '')."""
    turns = [h for h in (history or []) if isinstance(h, dict)]
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') == 'user':
            continue
        if not _is_landed_read(t):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        txt = _clean(t.get('text') or t.get('content') or '')
        subj = _clean(meta.get('subject') or '')
        if not subj:
            mm = re.match(r"^On\s+(?P<s>.{2,120}?)(?:\s+-\s+[^:]{1,80})?:", txt)
            subj = _clean(mm.group('s')) if mm else ''
        if not subj or subj.lower() in _DEIXIS:
            continue
        question = ''
        for j in range(i - 1, -1, -1):
            if str(turns[j].get('role') or '') == 'user':
                question = _clean(turns[j].get('text') or turns[j].get('content') or '')
                break
        return subj, txt, question
    # a thread with no landed read yet: the last build or promise names its subject
    try:
        from . import continuation as _ct
        prev = _ct.previous_deliverable(history)
        if prev.get('kind') in ('read', 'build', 'journey', 'promise', 'deck') and _clean(prev.get('subject')):
            return _clean(prev['subject']), _clean(prev.get('text')), _clean(prev.get('question'))
    except Exception:
        pass
    return '', '', ''


def resolve(text, history=None, ctx=None, page=''):
    """{'subject', 'platform', 'window', 'noun', 'referent', 'source'} for
    an order whose subject slot holds a referent; None when the text is
    not such an order or nothing in the message, the thread or the
    screen names the subject. Never raises."""
    try:
        ref = _orders.referent(text)
        if not ref:
            return None
        t = _clean(text)
        window = window_in(t)
        platform = platform_in(_orders.order_core(t))
        subj, before = _own_message(t, page)
        source = 'message'
        read_text, question = '', ''
        if subj:
            platform = platform or platform_in(before)
            window = window or window_in(before)
            noun = consumer_noun(before) or consumer_noun(subj)
        else:
            subj, read_text, question = _last_read(history)
            source = 'thread'
            noun = consumer_noun(question) or consumer_noun(subj)
            if subj:
                platform = platform or platform_in(question) or platform_in(subj)
                window = window or window_in(question)
        if not subj:
            try:
                from . import audience_referent as _ar
                subj = _ar.screen_audience(ctx)
            except Exception:
                subj = ''
            source = 'screen'
            noun = consumer_noun(subj)
            if subj:
                platform = platform or platform_in(subj)
        subj = _clean(subj).strip(' .,;:?!')
        if not subj or subj.lower() in _DEIXIS:
            return None
        # a referent that itself names the kind of people ("these viewers") carries its noun
        rn = consumer_noun(ref)
        if rn and rn != 'audience' and not noun:
            noun = rn
        subject = _with_noun(subj, noun if noun and noun != 'audience' else '')
        return {'subject': subject, 'platform': platform, 'window': window, 'noun': noun or '',
                'referent': ref, 'source': source}
    except Exception:
        return None


def rewrite(text, history=None, ctx=None, page=''):
    """The order the build flow reads with its referent resolved
    ('Please queue one Profile IQ: My Life With the Walter Boys viewers
    on Netflix.'), or None when the words name their subject already or
    nothing resolves."""
    r = resolve(text, history=history, ctx=ctx, page=page)
    if not r:
        return None
    out = _orders.with_subject(text, r['subject'], platform=r.get('platform') or '', window=r.get('window') or '')
    return out if out and _orders.is_order(out) else None
