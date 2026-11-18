"""A follow-up is the ask before it with one thing changed (Grace Yin,
Amazon, 2026-11-18 01:08 UTC; Jenna: "incorrectly answering").

She asked for the demo breakdown of the people who watched Tony, a 2026
film streaming on Amazon Prime Video ("released on October 26, 2026").
Her next message was

    "but the title just launched SVOD on Prime video in 2026 so i just
     want recent data after it launched"

and the reply was "I could not find But The Title Just Launched Svod as
a brand, person, or title". The draft step had read the thread and
drafted Tony correctly. Three guards behind it read only the words of
that one turn:

  * the identity backstop (app._resolve_subject_identity) saw a subject
    whose name was not in the turn, took it for a famous lookalike, and
    reset it to "the reader's own words": the head of the sentence, each
    word capitalised;
  * the name check then looked that sentence up as an entity and refused
    with the sentence in the reply;
  * the copy of the question that goes to the team carried the sentence
    as its subject.

The rule: a turn that points back at the thread ("the title", "it",
"that show", "same thing but ...", a turn that opens on but / and / so /
also, a turn that only changes a window, a platform or a cut) names
nothing itself. Its subject is the thread's, everything the reader did
not change carries over, and the one thing they changed is applied. A
sentence is never a subject and is never said back as one.

This module is the reader for that, in one place:

  refers_back(text)             the turn points at the thread
  residue(text, prior)          the words that could still be a name
  prior_ask(history, current)   the last ask that named something:
                                {'question', 'subject', 'platform'}
  resolve(text, history, ...)   None (an ask of its own), or
                                {'kind': 'refinement' | 'needs_title', ...}
                                with the change read out: the window, the
                                platform, the launch date when the reader
                                gave one
  thread_words(fu)              the reader's earlier words, for the
                                identity backstop
  reads_as_sentence(candidate)  a subject candidate that is a sentence
  never_echo(candidate, ask)    a candidate that is never said back as a name
  guard_subject(...)            what the name check does with one:
                                adopt the thread's subject, or ask which
  settle_window(...)            "after it launched": the launch day the
                                reader gave through the last full day, or
                                one short question for the date
  subject_line(subject, ...)    the subject shown on the team's copy

Pure text plus the thread. No model, no S3, no Flask, no network. Never
raises. Nothing here reads a flag from the request: the thread's own
turns decide. Wired in prometheus.legacy.chat (_pm_interpret_core,
_pm_chat_subject_verify), app._resolve_subject_identity,
prometheus.match_question.unresolved and prometheus_watch_notify.
Regression: scripts/test_pm_followup_window_refinement.py.
"""
from __future__ import annotations

import datetime as _dt
import re

__all__ = ['refers_back', 'residue', 'prior_ask', 'resolve', 'thread_words', 'reads_as_sentence', 'never_echo',
           'guard_subject', 'settle_window', 'subject_line', 'launch_date', 'which_title_copy',
           'launch_question', 'WHICH_TITLE']

WHICH_TITLE = ("Which title do you mean? Give me its name (the platform it is on helps) "
               "and I will pick it up from there.")
_WHICH_ANY = ("Which title, brand or person is this about? Give me the name (the platform it lives on helps) "
              "and I will pick it up from there.")

_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
           'october', 'november', 'december')
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"

# The kinds of thing a reader points at instead of naming.
_KIND = (r"(?:title|show|movie|film|series|program|programme|book|novel|podcast|game|album|documentary|doc|special|"
         r"season|franchise|brand|product|profile|audience|read|pull|report|analysis|breakdown|table|thing|one|"
         r"question|ask|request)")
# What follows a pointer when it is a pointer: a predicate, a preposition, the end. "the show severance" is a name.
_AFTER_POINTER = (r"(?=\s*(?:$|[,.;:!?)]|\s(?:just|only|recently|already|has|had|have|is|was|were|will|would|did|does|do|"
                  r"launched|premiered|debuted|released|aired|dropped|came|went|started|moved|landed|hit|arrived|ended|"
                  r"on|in|for|from|since|after|before|but|and|so|instead|too|also|again|itself|i|we|you|that|which|when|"
                  r"got|gets|streams|streaming|though|please|over|during|across|by|with|without|to|at|as)\b))")
_POINTER_RX = re.compile(r"\b(?:the|that|this)\s+(?:same\s+)?" + _KIND + r"\b" + _AFTER_POINTER, re.I)
_SAME_RX = re.compile(r"\b(?:the\s+)?same\s+(?:exact\s+)?" + _KIND + r"\b|\bsame\s+(?:as\s+(?:before|above|last\s+time)|again)\b", re.I)
_LAUNCH_VERB = (r"(?:launch(?:ed|es)?|premier(?:ed|es)|debut(?:ed|s)?|released?|was\s+released|came\s+out|comes\s+out|"
                r"dropped|aired|went\s+live|goes\s+live|go\s+live|landed|arrived|started\s+streaming|began\s+streaming|"
                r"hit\s+(?:the\s+)?(?:service|platform|streaming))")
_IT_RX = re.compile(
    r"\b(?:after|since|when|once|before|until|from\s+when|now\s+that|because|as)\s+(?:it|it's|its)\b|"
    r"\bit\s+(?:just\s+|only\s+|recently\s+|has\s+just\s+|has\s+only\s+)?" + _LAUNCH_VERB + r"\b|"
    r"\b(?:for|on|about|of|with)\s+(?:it|them|those|these|that\s+one|this\s+one)\b|"
    r"\b(?:run|rerun|re-run|pull|redo|cut|split|break|filter|limit|narrow)\s+(?:it|that|this|them)\b", re.I)
_OPENER_RX = re.compile(
    r"^\W*(?:(?:but|and|so|also|actually|ok(?:ay)?|oh|wait|well|no|nope|sorry|plus|then|hmm|right|yes|yeah|sure|"
    r"thanks|thank\s+you|please|now|again|instead)\b[,.!\s]*)+", re.I)
_CONJ_OPEN_RX = re.compile(r"^\W*(?:but|and|so|also|actually|plus|then|instead|wait|sorry)\b", re.I)
_ELLIPTICAL_RX = re.compile(
    r"^\W*(?:(?:what|how)\s+about|just|only|for|on|in|over|during|since|after|from|across|within|same|"
    r"make\s+it|change\s+(?:it|that|the\s+window|the\s+dates)|narrow\s+(?:it|that)|limit\s+(?:it|that))\b", re.I)
# "what about <something>": a name follows unless the next word is a preposition, a window or a cut
_ABOUT_NAME_RX = re.compile(
    r"^\W*(?:(?:but|and|so|also|ok(?:ay)?|now|then)[,\s]+)*(?:what|how)\s+about\s+"
    r"(?!(?:on|for|in|over|during|since|after|from|across|within|just|only|with|without|by|among|the\s+(?:last|past|first|next)\s+\d|"
    r"the\s+same|last\s+\d|past\s+\d|q[1-4]\b|\d|women|men|females?|males?|adults?|kids|parents|avid|casual|"
    r"(?:it|that|this)\s*(?:$|[?.!]|on\b|for\b|in\b))\b)", re.I)

_FUNCTION = frozenset("""
a an the this that these those it its it's is are was were be been being am do does did done doing has have had
having will would should could can may might must shall i i'd i'll i'm i've id ill im ive me my mine we we'd we'll
we're we've us our ours you you'd you'll you're your yours they them their he she his her and or but so nor yet if
then than as of on in at to for from by with without about into onto over under after before since until till
during across between through per via not no nope yes yeah yep ok okay oh well just only also too again still even
really actually basically please thanks thank instead rather though although because while when where what which
who whom whose why how there here now today yesterday currently recently recent latest new newest current once
out up down off live very much more most less least many some any all both each every either neither such own
whole entire rest else other another same different got get gets getting let lets let's s t d ll re ve m
""".split())
_REQUEST = frozenset("""
want wants wanted wanting need needs needed like likes liked give gives show shows tell tells see run runs rerun
re-run pull pulls repull re-pull redo make makes send sends look looking check use using include including exclude
excluding limit limited narrow restrict restricted filter filtered change changed switch switched update updated
keep kept thing things one ones hoping hope mean meant asked asking ask say said know knew think thought care
interested try trying start starting begin beginning focus focused cover covering covers read reads compare
""".split())
_DATA = frozenset("""
data info information number numbers figure figures stat stats result results report reports analysis breakdown
breakdowns table tables view views viewer viewers viewership audience audiences demo demos demographic demographics
profile profiles cut cuts split splits segment segments cohort cohorts people users user customers customer
subscribers subscriber subs members member fans watchers streamers accounts account sample size share percent
percentage rate count counts total totals metric metrics detail details version versions reach read pull signups
sign-ups churn retention engagement composition mix overlap index indexes
""".split())
_TIME = frozenset("""
day days week weeks month months year years quarter quarters q1 q2 q3 q4 h1 h2 ytd date dates window windows period
periods range timeframe time frame last past trailing previous prior next first since launch launched launches
launching premiere premiered premieres debut debuted debuts release released releases dropped aired came went
started start began ended end through thru until onward onwards forward to-date calendar fiscal weekend weekends
opening post pre mid early late th st nd rd am pm
""".split()) | frozenset(_MONTHS) | frozenset(m[:3] for m in _MONTHS) | frozenset(('sept',))
_MEDIA = frozenset("""
title titles show shows movie movies film films series season seasons episode episodes program programs book books
podcast podcasts game games svod avod tvod pvod est fast vod streaming stream streamed streams streamer watched
watch watching watches platform platforms service services app apps us u.s usa domestic digital online theatrical
theaters theatres
""".split())
_CUTS = frozenset("""
women men woman man female females male males adult adults kid kids parent parents age ages aged gender genders
income ethnicity education avid casual young old older younger millennial millennials gen z genz boomers hispanic
black white asian latino latina under plus
""".split())
_SCAFFOLD = _FUNCTION | _REQUEST | _DATA | _TIME | _MEDIA | _CUTS

# Services a reader names as the place, never as the subject, when a preposition leads in ("on Netflix").
_PLATFORMS = (
    ('amazon prime video', 'Amazon Prime Video'), ('prime video', 'Amazon Prime Video'), ('amazon prime', 'Amazon Prime Video'),
    ('netflix', 'Netflix'), ('hulu', 'Hulu'), ('hbo max', 'HBO Max'), ('max', 'Max'), ('hbo', 'HBO'),
    ('disney plus', 'Disney+'), ('disney+', 'Disney+'), ('peacock', 'Peacock'), ('paramount plus', 'Paramount+'),
    ('paramount+', 'Paramount+'), ('apple tv plus', 'Apple TV+'), ('apple tv+', 'Apple TV+'), ('apple tv', 'Apple TV+'),
    ('youtube tv', 'YouTube TV'), ('youtube', 'YouTube'), ('tubi', 'Tubi'), ('pluto tv', 'Pluto TV'), ('pluto', 'Pluto TV'),
    ('the roku channel', 'The Roku Channel'), ('roku channel', 'The Roku Channel'), ('roku', 'Roku'), ('freevee', 'Freevee'),
    ('starz', 'Starz'), ('showtime', 'Showtime'), ('amc plus', 'AMC+'), ('amc+', 'AMC+'), ('espn plus', 'ESPN+'),
    ('espn+', 'ESPN+'), ('discovery plus', 'Discovery+'), ('discovery+', 'Discovery+'), ('crunchyroll', 'Crunchyroll'),
    ('britbox', 'BritBox'), ('mgm plus', 'MGM+'), ('mgm+', 'MGM+'), ('fubo', 'Fubo'), ('sling', 'Sling TV'),
    ('spotify', 'Spotify'), ('tiktok', 'TikTok'), ('instagram', 'Instagram'), ('audible', 'Audible'), ('kindle', 'Kindle'),
    ('prime', 'Amazon Prime Video'),
)
_PLAT_ALT = '|'.join(re.escape(k) for k, _v in sorted(_PLATFORMS, key=lambda kv: -len(kv[0])))
_PLAT_CANON = dict(_PLATFORMS)
_PLATFORM_AT_RX = re.compile(
    r"\b(?:on|onto|for|in|from|to|at|via|through|with|across|within|inside|versus|vs\.?|over\s+to|instead\s+of|"
    r"(?:launched|premiered|debuted|released|dropped|streaming|available|svod|avod|tvod|pvod)(?:\s+(?:svod|avod|tvod|pvod))?\s+on)\s+"
    r"(?:the\s+)?(?P<p>" + _PLAT_ALT + r")(?![a-z0-9+])", re.I)
_PLATFORM_ANY_RX = re.compile(r"(?<![a-z0-9])(?P<p>" + _PLAT_ALT + r")(?![a-z0-9+])", re.I)

# "after it launched", "since launch", "post-launch", "recent data after the premiere"
_LAUNCH_WINDOW_RX = re.compile(
    r"\b(?:after|since|from|following|post)\b[^.;\n]{0,24}?\b(?:launch(?:ed)?|premiere[ds]?|debut(?:ed)?|release[ds]?|"
    r"came\s+out|went\s+live|dropped|landed|arrived|started\s+streaming)\b|\bpost[- ]?launch\b|\bsince\s+(?:day\s+one|it\s+started)\b|"
    r"\b(?:just|only|recently)\s+(?:launched|premiered|debuted|released|came\s+out|dropped|landed|arrived)\b[^.;\n]{0,90}?"
    r"\b(?:recent|after|since|post|only|just|new|latest)\b", re.I)
_DATE = (r"(?:(?P<{p}mon>" + _MON + r")\s+(?P<{p}d>\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<{p}y>(?:19|20)\d{{2}}))?|"
         r"(?P<{p}nm>\d{{1,2}})/(?P<{p}nd>\d{{1,2}})/(?P<{p}ny>(?:19|20)?\d{{2}})|"
         r"(?P<{p}iy>(?:19|20)\d{{2}})-(?P<{p}im>\d{{2}})-(?P<{p}id>\d{{2}}))")
_RELEASE_WORD = (r"(?:release[ds]?|release\s+date|premier(?:e|ed|es)|launch(?:ed|es|ing)?|debut(?:ed|s)?|dropped|drops|came\s+out|"
                 r"comes\s+out|went\s+live|goes\s+live|streaming\s+since|available\s+since|out\s+since|arrived|landed|started\s+streaming)")
_RELEASED_ON_RX = re.compile(r"\b" + _RELEASE_WORD + r"\b[^.;\n]{0,40}?\b(?:on\s+|as\s+of\s+|since\s+|from\s+)?" + _DATE.format(p='a'), re.I)
_DATE_RELEASE_RX = re.compile(_DATE.format(p='b') + r"\s+(?:" + _RELEASE_WORD + r")\b", re.I)
_ANY_DATE_RX = re.compile(_DATE.format(p='c'), re.I)
# the question launch_question() asks; a date in the reply to it is the launch day
_LAUNCH_Q_RX = re.compile(r"\bWhat date did\b.{1,200}?\bgo live\b", re.I | re.S)

# A subject candidate that is a sentence. The pointer is read more narrowly here than on a whole turn: a kind of
# content and a predicate about it ("The Title Just Launched"). "The One That Got Away" and "The Show Must Go On"
# are names.
_CAND_POINTER_RX = re.compile(
    r"\b(?:the|that|this)\s+(?:title|show|movie|film|series|program|programme|book|podcast|game|documentary|season)\s+"
    r"(?:just|only|recently|already|has|had|is|was|launched|premiered|debuted|released|aired|dropped|came|went|started|"
    r"moved|landed|arrived)\b", re.I)
_CAND_SAME_RX = re.compile(r"\bsame\s+(?:thing|read|ask|question|pull|analysis|report|breakdown|table)\b", re.I)
_CUT_RX = re.compile(
    r"\b(?:women|men|females?|males?|adults?\s+\d{2}|(?:ages?\s+)?\d{2}\s*(?:-|to)\s*\d{2}|\d{2}\+|under\s+\d{2}|over\s+\d{2}|"
    r"avid|casual|gen\s*z|millennials?|boomers|parents|kids)\b", re.I)
_FIRST_PERSON_RX = re.compile(
    r"\bi\s+(?:just\s+|only\s+|really\s+|still\s+|also\s+)?(?:want|need|would\s+like|am\s+looking|was\s+hoping|meant|mean|asked)\b|"
    r"\b(?:can|could|would)\s+you\b|\bgive\s+me\b|\bshow\s+me\b|\btell\s+me\b|\bplease\b", re.I)
_PREDICATE_RX = re.compile(
    r"\b(?:just|only|recently|already)\s+(?:launched|premiered|debuted|came|dropped|released|aired|started|landed|went|arrived)\b|"
    r"\b(?:after|since|when|once)\s+it\s+\w+|\b(?:has|have|had)\s+(?:just|only|recently|already|not)\b", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def _fold(s):
    return ' '.join(re.sub(r"[^a-z0-9+ ]+", ' ', str(s or '').lower()).split())


def _words(s):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'+&-]*", str(s or ''))


def _canon_platform(raw):
    return _PLAT_CANON.get(' '.join(str(raw or '').lower().split()), '')


def platform_change(text):
    """The service a turn moves the ask to ("and what about on Netflix"), or ''."""
    try:
        m = _PLATFORM_AT_RX.search(str(text or ''))
        return _canon_platform(m.group('p')) if m else ''
    except Exception:
        return ''


def refers_back(text):
    """True when the turn points at the thread instead of naming its
    subject: a pointer ("the title", "that show", "it launched", "same
    thing"), an opening but / and / so / also, or an elliptical turn
    ("just the last 90 days", "what about on Netflix", "for 2025")."""
    try:
        t = _squash(text)
        if not t:
            return False
        return bool(_POINTER_RX.search(t) or _SAME_RX.search(t) or _IT_RX.search(t)
                    or _CONJ_OPEN_RX.match(t) or _ELLIPTICAL_RX.match(t))
    except Exception:
        return False


def _strong_pointer(text):
    t = _squash(text)
    return bool(_POINTER_RX.search(t) or _SAME_RX.search(t) or _IT_RX.search(t))


def residue(text, prior=None):
    """The words of a turn that could still be a name once the scaffold
    is taken out: function words, request words, data and window words,
    a service named as the place, numbers, and the thread's own subject
    said again. A word typed with a capital in the middle of a sentence
    (not a month, a service or a known short form) always stays."""
    try:
        t = _squash(text)
        if not t:
            return []
        prior = prior if isinstance(prior, dict) else {}
        own = set(_fold(prior.get('subject') or '').split())
        own |= set(_fold(prior.get('platform') or '').split())
        work = _PLATFORM_AT_RX.sub(lambda m: m.group(0)[:m.start('p') - m.start(0)] + ' ', t)
        prior_plat = _fold(prior.get('platform') or '')
        if prior_plat:
            work = _PLATFORM_ANY_RX.sub(
                lambda m: ' ' if _fold(_canon_platform(m.group('p'))) == prior_plat else m.group(0), work)
        out = []
        toks = _words(work)
        for i, w in enumerate(toks):
            low = w.lower().strip("'")
            bare = re.sub(r"'s$", '', low)
            if not low or len(low) == 1:
                continue
            if re.fullmatch(r"\d[\d,./-]*(?:st|nd|rd|th|k|m|%)?", low) or re.fullmatch(r"(?:19|20)\d{2}s?", low):
                continue
            if bare in own or low in own:
                continue
            scaffold = low in _SCAFFOLD or bare in _SCAFFOLD or (low.endswith('s') and low[:-1] in _SCAFFOLD)
            capital = w[:1].isupper() and not w.isupper() and i > 0
            if scaffold and not capital:
                continue
            if scaffold and capital and (low in _TIME or bare in ('i', "i'm", "i'd", "i'll", "i've")):
                continue      # a month, "I"
            if w.isupper() and low in _SCAFFOLD:
                continue      # SVOD, US, YTD
            out.append(w)
        return out
    except Exception:
        return ['?']


def _turns(history):
    return [t for t in (history or []) if isinstance(t, dict)]


def _text(t):
    return _squash(t.get('text') or t.get('content') or '')


def _is_user(t):
    return str(t.get('role') or '').lower() == 'user'


def _as_typed(name, question):
    """A name typed in capitals and again in ordinary case in the same
    ask ("TONY ... the film Tony") is shown in ordinary case."""
    try:
        n = _squash(name)
        if n and n.isupper() and len(n) >= 3:
            for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", str(question or ''), re.I):
                v = m.group(0)
                if not v.isupper() and not v.islower():
                    return v
        return n
    except Exception:
        return _squash(name)


def _named_subject(question, later_agent):
    """The subject an earlier ask named: what the reply recorded for it,
    else what its own words name."""
    try:
        from . import subject_rules as _sr
    except Exception:
        _sr = None
    for a in later_agent:
        meta = a.get('meta') if isinstance(a.get('meta'), dict) else {}
        for k in ('subject', 'bound_subject'):
            s = _squash(meta.get(k) or '')
            if s and not reads_as_sentence(s) and not _canon_platform(s):
                return (_sr.clean_display(s) or s) if _sr else s
    if _sr is None:
        return ''
    try:
        b = _sr.bind(question, None, '')
        if b and b.source == 'words' and b.name and b.kind not in ('metric', 'none') \
                and not _sr.is_metric_phrase(b.display) and not reads_as_sentence(b.display):
            return _as_typed(_sr.clean_display(b.display) or b.display, question)
    except Exception:
        pass
    return ''


def _prior_platform(question):
    try:
        from . import named_platforms as _np
        hit = _np.find(question)
        if hit and hit.get('platforms'):
            return str(hit['platforms'][0])
    except Exception:
        pass
    return platform_change(question)


def prior_ask(history, current=''):
    """The last ask in the thread that named something of its own:
    {'question', 'subject', 'platform'}; None when the thread has none.
    Earlier follow-ups, yes / no replies and chip presses are passed
    over. ``current`` is this turn's text (a history that already holds
    it does not answer for itself)."""
    try:
        turns = _turns(history)
        cur = _fold(current)
        fallback = None
        for i in range(len(turns) - 1, -1, -1):
            t = turns[i]
            if not _is_user(t):
                continue
            q = _text(t)
            if not q or (cur and _fold(q) == cur):
                continue
            later = []
            for j in range(i + 1, len(turns)):
                if _is_user(turns[j]):
                    break
                later.append(turns[j])
            subj = _named_subject(q, later)
            if not subj and not residue(q):
                continue          # a follow-up or a short reply: nothing named
            entry = {'question': q, 'subject': subj, 'platform': _prior_platform(q)}
            if subj:
                return entry
            if len(q.split()) >= 4:      # an ask whose subject no rule could name still stands as the ask; "hello" does not
                fallback = fallback or entry
        try:
            from . import subject_rules as _sr
            pin = _squash(_sr.pinned_thread_subject() or '')
        except Exception:
            pin = ''
        if pin and not reads_as_sentence(pin):
            base = fallback or {'question': '', 'platform': ''}
            return dict(base, subject=pin)
        return fallback
    except Exception:
        return None


def _to_date(y, m, d):
    try:
        y = int(y)
        if y < 100:
            y += 2000
        return _dt.date(y, int(m), int(d))
    except Exception:
        return None


def _mon_num(s):
    s = str(s or '').lower().rstrip('.')[:3]
    for i, m in enumerate(_MONTHS, 1):
        if m[:3] == s:
            return i
    return 0


def _date_from(m, p, today):
    try:
        if m.group(p + 'mon'):
            y = m.group(p + 'y') or today.year
            d = _to_date(y, _mon_num(m.group(p + 'mon')), m.group(p + 'd'))
            if d and not m.group(p + 'y') and d > today:
                d = _to_date(today.year - 1, d.month, d.day)
            return d
        if m.group(p + 'nm'):
            return _to_date(m.group(p + 'ny'), m.group(p + 'nm'), m.group(p + 'nd'))
        if m.group(p + 'iy'):
            return _to_date(m.group(p + 'iy'), m.group(p + 'im'), m.group(p + 'id'))
    except Exception:
        return None
    return None


def launch_date(texts, today=None):
    """The day the reader said the subject launched ("released on
    October 26, 2026", "premiered 10/26/2026", "the October 26 launch"),
    newest text first; None when their words give none. Never a day
    that has not come."""
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    for txt in texts or []:
        t = str(txt or '')
        for rx, p in ((_RELEASED_ON_RX, 'a'), (_DATE_RELEASE_RX, 'b')):
            for m in rx.finditer(t):
                d = _date_from(m, p, today)
                if d and _dt.date(2000, 1, 1) <= d <= today:
                    return d
    return None


def _pretty(d):
    return f"{_MONTHS[d.month - 1].title()} {d.day}, {d.year}"


def _window_change(text, today):
    """(kind, window, phrase) a turn states: ('launch', None, ''), an
    explicit or relative window, a calendar year, or ('', None, '')."""
    t = _squash(text)
    if _LAUNCH_WINDOW_RX.search(t):
        return 'launch', None, ''
    try:
        from . import continuation as _c
        w = _c.rerun_window(t, today=today)
        if w:
            return 'explicit', (w[0], w[1]), ''
        y = _c.year_window(t, today=today)
        if y:
            return 'year', (y[0], y[1]), ''
    except Exception:
        pass
    try:
        from . import inline_dates as _idw
        f = _idw.parse_inline_window(t, today=today)
        if f and not f.get('coarse'):
            return 'explicit', (f['start'], f['end']), str(f.get('phrase') or '')
    except Exception:
        pass
    return '', None, ''


def resolve(text, history, today=None, reading=None, page=''):
    """What a turn is against its thread.

    None: an ask of its own (it names something, or it is neither a
    pointer nor a change). Otherwise::

        {'kind': 'refinement',           # the thread has an ask to refine
         'subject', 'question', 'platform',      # carried over
         'change': {'window_kind': 'launch'|'explicit'|'year'|'',
                    'window': (start, end) | None,
                    'platform': 'Netflix' | '', 'cut': 'women' | ''},
         'launch_date': date | None,     # the reader's own words
         'needs_launch_date': bool,
         'words': [earlier user turns, newest first]}
        {'kind': 'needs_title', ...}     # it points back and nothing is there

    ``reading`` is the Reader's understanding of the turn when there is
    one: a subject it found IN these words makes the turn its own ask.
    ``page`` is the profile open on screen: a pointer with a page open
    and no thread points at the page, so nothing is asked here."""
    try:
        t = _squash(text)
        if not t or len(t) > 420:
            return None
        today = today or _dt.datetime.now(_dt.timezone.utc).date()
        try:
            from . import subject_rules as _sr
            if _sr.explicit_build_subject(t):
                return None          # an order names its own subject
        except Exception:
            pass
        try:
            from . import orders as _ord
            if _ord.is_order(t):
                return None          # "run it", "ok queue it": the order lanes answer those
        except Exception:
            pass
        if _ABOUT_NAME_RX.match(t):
            return None              # "what about Severance": a name follows
        prior = prior_ask(history, current=t)
        words = []
        for h in reversed(_turns(history)):
            if _is_user(h):
                q = _text(h)
                if q and _fold(q) != _fold(t) and q not in words:
                    words.append(q)
        # the reply to the launch-day question: the date is the launch day, the ask is the thread's
        agents = [a for a in _turns(history) if not _is_user(a)]
        if agents and _LAUNCH_Q_RX.search(_text(agents[-1])) and len(t.split()) <= 16 \
                and prior and (prior.get('subject') or prior.get('question')):
            dm = _ANY_DATE_RX.search(t)
            d = _date_from(dm, 'c', today) if dm else None
            if d and _dt.date(2000, 1, 1) <= d <= today:
                return {'kind': 'refinement', 'subject': str(prior.get('subject') or ''),
                        'question': str(prior.get('question') or ''), 'platform': str(prior.get('platform') or ''),
                        'change': {'window_kind': 'launch', 'window': None, 'platform': '', 'cut': ''},
                        'launch_date': d, 'needs_launch_date': False, 'words': words[:6]}
        if residue(t, prior):
            return None
        wk, win, _phrase = _window_change(t, today)
        plat = platform_change(t)
        if prior and plat and _fold(plat) == _fold(prior.get('platform') or ''):
            plat = ''                # the same service said again changes nothing
        cm = _CUT_RX.search(t)
        cut = _squash(cm.group(0)).lower() if cm else ''
        strong = _strong_pointer(t)
        if not (strong or ((wk or plat or cut) and refers_back(t))):
            return None
        if isinstance(reading, dict):
            ft = ' ' + _fold(t) + ' '
            for s in (reading.get('subjects') or []):
                name = _fold((s or {}).get('as_written') if isinstance(s, dict) else s)
                if name and len(name) > 2 and not _canon_platform(name) and (' ' + name + ' ') in ft \
                        and not (prior and name == _fold(prior.get('subject') or '')) \
                        and not re.fullmatch(r"(?:the|that|this|it|same)(?:\s+same)?(?:\s+" + _KIND + r")?", name, re.I):
                    return None      # the Reader found a name in these very words
        change = {'window_kind': wk, 'window': win, 'platform': plat, 'cut': cut}
        if not prior or not (prior.get('subject') or prior.get('question')):
            if not strong or _squash(page):
                return None          # nothing to point at here; the page, if any, answers for itself
            return {'kind': 'needs_title', 'subject': '', 'question': '', 'platform': plat, 'change': change,
                    'launch_date': None, 'needs_launch_date': False, 'words': words}
        ld = launch_date([t] + words[:4], today) if wk == 'launch' else None
        return {'kind': 'refinement', 'subject': str(prior.get('subject') or ''),
                'question': str(prior.get('question') or ''),
                'platform': plat or str(prior.get('platform') or ''), 'change': change,
                'launch_date': ld, 'needs_launch_date': bool(wk == 'launch' and not ld), 'words': words[:6]}
    except Exception:
        return None


def thread_words(fu, limit=1800):
    """The reader's earlier words in this thread, for the guards that
    ask whether a name is the reader's own. '' for a turn that is not a
    follow-up."""
    try:
        if not isinstance(fu, dict) or fu.get('kind') != 'refinement':
            return ''
        return ' \n'.join(str(w) for w in (fu.get('words') or []) if w)[:limit]
    except Exception:
        return ''


def reads_as_sentence(candidate, ask_text=''):
    """True when a subject candidate is a sentence or a piece of one,
    never a name: "But The Title Just Launched Svod", "I Just Want
    Recent Data", "Same Thing But For 2025". A title that happens to
    open on a conjunction (And Just Like That, But I'm a Cheerleader)
    is a name: it has no pointer, no request and no predicate about the
    thing it would be naming."""
    try:
        c = _squash(candidate)
        words = c.split()
        if len(words) < 3:
            return False
        if _CAND_POINTER_RX.search(c) or _CAND_SAME_RX.search(c) or _FIRST_PERSON_RX.search(c) or _PREDICATE_RX.search(c):
            return True
        if _IT_RX.search(c) and _CONJ_OPEN_RX.match(c):
            return True
        ask = _fold(ask_text)
        fc = _fold(c)
        # a piece of the reader's own turn, said back with capitals, when that turn names nothing at all
        if ask and fc and (' ' + fc + ' ') in (' ' + ask + ' ') and refers_back(ask_text) \
                and not _ABOUT_NAME_RX.match(_squash(ask_text)) and not residue(ask_text) and not _plausible(c):
            return True
        return False
    except Exception:
        return False


def _chunk_of(candidate, ask_text):
    fc, ask = _fold(candidate), _fold(ask_text)
    return bool(fc and ask and (' ' + fc + ' ') in (' ' + ask + ' '))


def never_echo(candidate, ask_text=''):
    """True when a candidate must not be said back to the reader as a
    name. Wider than reads_as_sentence, because the only cost is copy:
    a sentence, or a piece of a turn that points back and is made of
    ordinary words only."""
    try:
        c = _squash(candidate)
        if not c:
            return False
        if reads_as_sentence(c, ask_text):
            return True
        if len(c.split()) >= 3 and _chunk_of(c, ask_text) and refers_back(ask_text) \
                and not _ABOUT_NAME_RX.match(_squash(ask_text)) and not residue(c.lower()):
            return True
        return False
    except Exception:
        return False


def _plausible(name):
    try:
        from . import referents as _refs
        return bool(_refs.plausible_subject(name))
    except Exception:
        return False


def which_title_copy(text=''):
    """The one plain question when a turn points back and the thread
    holds nothing to point at. Never says the words back as a name."""
    t = _squash(text)
    if re.search(r"\b(?:title|show|movie|film|series|season|episode|book|podcast|game)\b", t, re.I):
        return WHICH_TITLE
    return _WHICH_ANY


def launch_question(subject='', platform=''):
    """The one short question for the launch day."""
    what = _squash(subject) or 'it'
    where = f" on {_squash(platform)}" if _squash(platform) else ''
    return (f"Happy to. What date did {what} go live{where}? "
            f"I will read from that day forward, through the most recent full day.")


def guard_subject(candidate, text, history=None, fu=None):
    """What the name check does with a build subject before it looks
    the name up.

    None: the candidate is a name; look it up as before.
    {'adopt': subject}: the candidate is a sentence and the thread has a
        subject; the build carries on with that one.
    {'ask': copy}: the candidate is a sentence and the thread has none;
        the reader is asked which title, in plain words."""
    try:
        cand = _squash(candidate)
        if not cand:
            return None
        follow = isinstance(fu, dict) and fu.get('kind') in ('refinement', 'needs_title')
        # a turn already read as a follow-up names nothing: a piece of it is not a name either
        if not (reads_as_sentence(cand, text) or (follow and len(cand.split()) >= 3 and _chunk_of(cand, text))):
            return None
        subj = ''
        if isinstance(fu, dict) and fu.get('kind') == 'refinement':
            subj = _squash(fu.get('subject') or '')
        if not subj:
            p = prior_ask(history, current=text)
            subj = _squash((p or {}).get('subject') or '')
        if subj and _fold(subj) != _fold(cand) and not reads_as_sentence(subj):
            return {'adopt': subj}
        return {'ask': which_title_copy(text)}
    except Exception:
        return None


def settle_window(draft, fu, ev_state, bind=None, decision=None, today=None):
    """"After it launched" on a follow-up. Returns (ev_state, ask).

    The reader gave the launch day in the thread: the window is that
    day through the last full day, bound on the draft ahead of every
    other binder and marked explicit (``bind`` is the host's inline
    window binder; the brief echoes the window). They did not, and the
    draft holds no confident window of its own: ``ask`` is the one
    short question for the date. Anything else is left as it was."""
    try:
        if not isinstance(fu, dict) or fu.get('kind') != 'refinement' or not isinstance(draft, dict):
            return ev_state, None
        if str((fu.get('change') or {}).get('window_kind') or '') != 'launch':
            return ev_state, None
        if str(decision or draft.get('decision') or '').strip().lower() in ('existing_match',):
            return ev_state, None
        ld = fu.get('launch_date')
        if isinstance(ld, _dt.date):
            from . import inline_dates as _idw
            end = _idw.yesterday(today)
            if ld > end:
                return ev_state, None        # launched within the day: no full day to read yet; the window guards speak
            bound = False
            try:
                bound = callable(bind) and bind(draft, f"since {_pretty(ld)}", chat_history=None,
                                                decision=decision, question='') is True
            except Exception:
                bound = False
            dr = draft.get('date_range') if isinstance(draft.get('date_range'), dict) else {}
            if not bound or str(dr.get('start') or '') != ld.isoformat():
                # the reader gave the day: it is the window whatever the binder made of the words
                draft['date_range'] = {'start': ld.isoformat(), 'end': end.isoformat()}
                draft['date_range_explicit'] = True
                try:
                    from . import window_scope as _wsc
                    label = _wsc.label_for(ld.isoformat(), end.isoformat())
                except Exception:
                    label = ''
                draft['date_window_label'] = label or f"{ld.isoformat()} to {end.isoformat()}"
            ew = draft.get('event_window')
            if isinstance(ew, dict):     # the window is settled: nothing is left for a later step to resolve
                draft['event_window'] = {'query': str(ew.get('query') or '')[:300],
                                         'label': ' '.join(str(ew.get('label') or '').split())[:120],
                                         'confident': True}
            draft.pop('event_window_query', None)
            return 'confident', None
        if ev_state == 'confident':
            return ev_state, None
        subj = str(draft.get('resolved_title') or draft.get('subject') or fu.get('subject') or '').split(' - ', 1)[0]
        return ev_state, launch_question(subj, str(fu.get('platform') or draft.get('platform') or ''))
    except Exception:
        return ev_state, None


def subject_line(subject, question='', history=None):
    """The subject shown on the copy of a question that goes to the
    team. A sentence is never shown as one: the thread's subject stands
    in when there is one, else the line is left off."""
    try:
        s = _squash(subject)
        if not s:
            return ''
        long_ = len(s.split()) > 12 or len(s) > 110
        if not long_ and not never_echo(s, question):
            return s
        p = prior_ask(history, current=question) if history else None
        alt = _squash((p or {}).get('subject') or '')
        return alt if alt and not reads_as_sentence(alt) else ''
    except Exception:
        return _squash(subject)


# ---------------------------------------------------------------------------
# The build intake's four call sites (prometheus.legacy.chat). Kept here so
# the legacy module carries one line each; every one is fail-safe.
# ---------------------------------------------------------------------------
def _hint(ask_hint, **kw):
    try:
        if callable(ask_hint):
            ask_hint(**kw)
    except Exception:
        pass


def _guidance(copy):
    return {'success': False, 'guidance': True, 'error': str(copy), 'followups': []}


def at_interpret(text, history, body=None, ask_hint=None):
    """Read the turn against its thread before any draft. Returns
    (fu, early): ``fu`` rides to the identity backstop, the name check
    and the window step; ``early`` is the which-title question when the
    turn points back and neither the thread nor the page holds anything
    to point at (no draft, no lookup, nothing said back as a name)."""
    try:
        # a question about a word on the thread's own brief is answered, never drafted (Grace Yin 2026-11-18:
        # "wjat does Avid tier: ~3,837 mean" drew a third brief; prometheus.brief_terms)
        from . import brief_terms as _bt
        # what the reader typed this turn: an order that re-runs the thread's last ask ("run it") is an order, even
        # when that ask was a question about the brief
        typed = _bt.typed_this_turn(text)
        bt = _bt.answer(typed, history) if _bt.is_term_question(typed) else None
        if bt and bt.get('reply'):
            _hint(ask_hint, route='brief_terms', outcome='answered', subject=bt.get('subject') or None)
            return None, dict(_guidance(bt['reply']), brief_term=bt.get('term'))
    except Exception:
        pass
    try:
        b = body if isinstance(body, dict) else {}
        page = ''
        try:
            from . import build_guards as _bg
            page = _bg.page_name(b) or ''
        except Exception:
            page = ''
        fu = resolve(text, history, reading=b.get('reading'), page=page)
        if not fu:
            return None, None
        if fu.get('kind') == 'needs_title':
            _hint(ask_hint, route='followup', outcome='asked_which_title')
            return fu, _guidance(which_title_copy(text))
        try:
            print(f"[pm-followup] refines the thread's ask on {fu.get('subject')!r}: {fu.get('change')}")
        except Exception:
            pass
        if fu.get('subject'):
            _hint(ask_hint, subject=fu['subject'])
        return fu, None
    except Exception:
        return None, None


def identity_kwargs(fu):
    """{'thread_words': ...} for app._resolve_subject_identity on a
    follow-up, {} on any other turn (the call is then exactly as it
    was)."""
    w = thread_words(fu)
    return {'thread_words': w} if w else {}


def settle_subject(draft, text, history=None, fu=None, ask_hint=None):
    """The name check's first step, on the draft itself. A drafted
    subject that is a sentence is replaced by the thread's own (written
    onto the draft, so the lookup that follows runs on a name); with
    nothing to carry on with, the which-title question comes back as the
    payload to send. None when the drafted subject is a name."""
    try:
        if not isinstance(draft, dict):
            return None
        subj = str(draft.get('subject') or '').split(' - ', 1)[0].strip()
        gs = guard_subject(subj, text, history, fu) if subj else None
        if not gs:
            return None
        if gs.get('adopt'):
            name = str(gs['adopt'])
            try:
                print(f"[pm-followup] {subj!r} is a sentence, not a name; the thread's subject {name!r} carries the build")
            except Exception:
                pass
            draft['subject'] = draft['name'] = name
            for k in ('file_stem', 'resolved_title', 'resolved_identity', 'identity_note', 'ask_identity',
                      'identity_data'):
                draft.pop(k, None)
            _hint(ask_hint, subject=name)
            return None
        _hint(ask_hint, route='followup', outcome='asked_which_title')
        return _guidance(gs['ask'])
    except Exception:
        return None


def settle_window_on_host(draft, fu, ev_state, host, decision=None, ask_hint=None):
    """settle_window with the host's inline window binder. Returns
    (ev_state, ask_payload)."""
    try:
        def bind(*a, **k):       # the host is only asked for its binder when a launch day is in hand
            return getattr(host, '_bind_inline_date_window')(*a, **k)
        state, ask = settle_window(draft, fu, ev_state, bind=bind, decision=decision)
        if ask:
            _hint(ask_hint, route='followup', outcome='asked_launch_date',
                  subject=str((draft or {}).get('subject') or '') or None)
            return state, _guidance(ask)
        return state, None
    except Exception:
        return ev_state, None
