"""The open profile is context, never a default subject.

Jenna 2026-10-06: "strip the dashboard assumptions out of the lanes ...
just ensure it always asks to confirm." Jenna 2026-10-24, after Scott
Carlin's small-kitchen-appliance market question ran on LeBron James
because that profile was open: "it should never default to that."

What went wrong on 2026-10-24, in order: the quote named "Amazon" (the
first brand the Reader saw, though Amazon, Walmart and Target were the
places people shop, not the audience); the Approve re-resolved the base
from scratch and the resolver's page fallback picked the open profile;
the read ran on LeBron James; "no! Lebron james has nothing to do with
this" was read as a new question and ran on LeBron James again.

Three rules, all here so the generation pass in legacy/chat.py stays
small:

  1. quoted_subject: the subject a quote named is the subject the
     Approve runs on. One resolution, carried by the confirm.
  2. page_base_stands: a page base stands only when the words point at
     the page, name it or one of its rows, the thread already landed on
     it or confirmed it, or the reader bound it by chip. A page the
     reader has said no to (this turn or earlier) never stands.
  3. no_base_subject: with no base, the subject is the bound or quoted
     one, "Everyone in the US" when the reader picked the whole panel,
     the Reader's cohort as written, or nothing, in which case the
     which-audience question goes out (priced_read.which_audience) and
     nothing is quoted or run.
"""
from __future__ import annotations

import re
import traceback

EVERYONE = "Everyone in the US"
_PLACEHOLDERS = frozenset(('', 'that subject', 'this subject', 'the subject'))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def is_everyone(label):
    try:
        from .named_subject import is_everyone as _ie
        return bool(_ie(label))
    except Exception:
        return _norm(label) in ('everyoneintheus', 'everyone', 'genpop', 'generalpopulation', 'allusadults', 'usadults')


def quoted_subject(panel_confirm, bind_subject=''):
    """The subject to bind for this turn: the chip the reader tapped, else
    the subject the quote named (the Approve carries it back), else ''."""
    b = str(bind_subject or '').strip()
    if b:
        return EVERYONE if is_everyone(b) else b
    if isinstance(panel_confirm, dict):
        q = str(panel_confirm.get('subject') or '').strip()
        if q.lower() not in _PLACEHOLDERS:
            return EVERYONE if is_everyone(q) else q
    return ''


def page_rejected(text, history, page):
    """The reader said no to the page, now or earlier in the thread."""
    try:
        from . import subject_rules as _sr
        turns = list(history or []) + [{'role': 'user', 'text': str(text or '')}]
        return bool(_sr.page_rejected_in_thread(turns, page))
    except Exception:
        traceback.print_exc()
        return False


def page_base_stands(text, history, page, page_key, bind_subject='', *, normalize, stop_tokens,
                     brand_named=None, ask_user=''):
    """(stands, why). The page is the base only on a positive signal."""
    page = str(page or '').strip()
    if not page:
        return False, 'no page'
    if page_rejected(text, history, page):
        return False, 'rejected'
    b = str(bind_subject or '').strip()
    if b and normalize(b.split(' - ')[0]) == normalize(page.split(' - ')[0]):
        return True, 'bound'
    try:
        from . import named_subject as _ns
        if _ns.view_deixis(text):
            return True, 'deixis'
        if _ns.page_named(text, page, normalize, stop_tokens):
            return True, 'named'
        if _ns.thread_on_page(history or [], page, normalize):
            return True, 'thread on page'
    except Exception:
        traceback.print_exc()
    try:
        from .legacy import watch as _w
        if _w._pm_thread_confirmed_page(history, page):
            return True, 'thread confirmed'
        if ask_user and _w._pm_page_confirmed_recently(ask_user, page):
            return True, 'confirmed recently'
    except Exception:
        traceback.print_exc()
    try:
        if callable(brand_named) and page_key and brand_named(text, page_key):
            return True, 'row named'
    except Exception:
        traceback.print_exc()
    try:
        import prometheus_analysis as _pma
        if _pma.ask_is_anaphoric(text):
            return True, 'anaphora'
    except Exception:
        traceback.print_exc()
    return False, 'not pointed at'


def base_stands(text, history, base, bind_subject='', *, normalize, stop_tokens, brand_named=None, ask_user=''):
    """(stands, why) for a resolved base. A catalog base the words named
    stands unless the reader is saying no to that name; a page base needs
    a positive signal (page_base_stands)."""
    subj = str((base or {}).get('subject') or '')
    if page_rejected(text, history, subj):
        return False, 'rejected'
    if str((base or {}).get('source') or '') == 'page':
        return page_base_stands(text, history, subj, (base or {}).get('s3_key'), bind_subject,
                                normalize=normalize, stop_tokens=stop_tokens, brand_named=brand_named, ask_user=ask_user)
    return True, 'named'


def place_in_list(name, text):
    """`name` only appears as a place after "at", "on", "across" in a list
    of two or more ("shopping at Amazon, Walmart and Target")."""
    try:
        from . import subject_rules as _sr
        if not _sr._place_in_list(name, text):
            return False
        head_rx = re.compile(r"\b(?:at|on|across|between|among|from|via|through|in|to|with)\s+((?:(?:the\s+)?[\w&'.+-]+,?\s+(?:and\s+|or\s+|&\s+)?){1,6})",
                             re.I)
        for m in head_rx.finditer(str(text or '')):
            names = [w for w in re.findall(r"[A-Z][\w&'.+-]*", m.group(1))]
            if len(names) >= 2 and any(_norm(n) == _norm(name) for n in names):
                return True
        return False
    except Exception:
        return False


_FRAGMENT_TAIL = frozenset(('among', 'and', 'or', 'the', 'a', 'an', 'of', 'in', 'on', 'at', 'with', 'for', 'to', 'from',
                            'by', 'as', 'than', 'then', 'that', 'which', 'who', 'whose', 'where', 'when', 'but', 'so', 'if'))


def fragment(name):
    """A guess that crossed a sentence boundary or stopped on a connective
    is prose, not a subject ("Target. Among" from "... Walmart and
    Target. Among people who ...", 2026-10-24)."""
    n = ' '.join(str(name or '').split())
    if not n:
        return False
    if re.search(r"[.?!;:](?:\s|$)", n.rstrip('.?!')) or re.search(r"[.?!]\s+\w", n):
        return True
    toks = re.findall(r"[A-Za-z']+", n.lower())
    return bool(toks) and (toks[-1] in _FRAGMENT_TAIL or toks[0] in ('and', 'or', 'but', 'then', 'which', 'among'))


def not_a_subject(name, text, plausible=None, history=None):
    """A place in a list of places, a prose fragment, ordinary words, or a
    name the reader is saying no to ("no! LeBron James has nothing to do
    with this" names LeBron James to rule it out, 2026-10-24)."""
    n = str(name or '').strip()
    if not n:
        return True
    if fragment(n) or place_in_list(n, text) or page_rejected(text, history, n):
        return True
    try:
        return bool(callable(plausible)) and not plausible(n)
    except Exception:
        return False


def no_base_subject(text, page, bind_subject='', subj_hint='', panel_confirm=None, *, display_for, reading_cohort,
                    plausible):
    """The subject a no-base read is quoted and run on, or ''."""
    q = quoted_subject(panel_confirm, bind_subject)
    if q:
        return q
    hint = str(subj_hint or '').strip()
    if hint and is_everyone(hint):
        return EVERYONE
    name = ''
    try:
        name = str(display_for(hint, text) or '').strip() if hint else ''
    except Exception:
        traceback.print_exc()
    if name and not_a_subject(name, text, plausible):
        name = ''
    if not name:
        try:
            name = str(reading_cohort(text, page) or '').strip()
        except Exception:
            name = ''
    return name


# ---------------------------------------------------------------------------
# A person's profile is not the audience of their films (Jenna 2026-11-04,
# Jay Patel's Statham thread). The Jason Statham Profile IQ file counts
# people with digital touchpoints to HIM (search, social, media, commerce,
# owned). A question about viewers of his films, their Prime Video share or
# "total viewers" is about another universe: the people who watched a
# title. That read is anchored on title viewing (the trackers, the content
# map, the read's own research), never on the person's profile, and it
# opens by naming the universe it used. The profile stays the base when
# the question is about the person's own audience ("his fans", "the Jason
# Statham audience", "what do his fans watch").
# ---------------------------------------------------------------------------
PERSON_CATEGORIES = frozenset((
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'EMERGING TALENT',
    'HOST/PERSONALITY', 'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST',
    'TALENT', 'PERSON'))
# the works a person is known for: a question about them is about titles
_WORKS_RX = re.compile(
    r"\b(?:films?|movies?|titles?|episodes?|series|shows?|filmography|catalog(?:ue)?|box office|premieres?|"
    r"releases?|seasons?|specials?|albums?|songs?|tracks?|tour dates?|concerts?)\b", re.I)
_PLATFORMS = (r"prime video|amazon prime(?: video)?|netflix|hulu|hbo max|max|peacock|paramount\+?|disney\+?|apple tv\+?|"
              r"tubi|pluto tv|the roku channel|roku|youtube|starz|freevee|amc\+?|crunchyroll|discovery\+?|espn\+?|"
              r"any streaming service|streaming|free tv|free ad-supported")
_PLATFORM_RX = re.compile(r"\bon (?:the )?(?P<p>" + _PLATFORMS + r")(?![\w+])", re.I)   # (?![\w+]): 'Paramount+' keeps its plus
_NEGATED_BEFORE_RX = re.compile(r"(?:\bnot\b|\bnever\b|n't\b|\bno\b|\brarely\b|\bwithout\b|\bexclusive to\b)[^.?!]{0,30}$", re.I)
_VIEW_VERBS = r"(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing)?|play(?:ed|s|ing)?|s(?:aw|een|ee)|binge[ds]?|tuned in to)"


def is_person_category(category):
    return str(category or '').strip().upper() in PERSON_CATEGORIES


def _name_rx(name):
    toks = [re.escape(t) for t in str(name or '').split() if t]
    return r"\s+".join(toks) if toks else ''


def audience_of_person(text, name):
    """The ask is about the person's own audience: his fans, the X
    audience, people who engage with him."""
    n = _name_rx(name)
    if not n:
        return False
    t = str(text or '')
    return bool(re.search(rf"\b{n}(?:'s)?\s+(?:fans?|audience|followers|engagers|subscribers|supporters|listeners|base)\b", t, re.I)
                or re.search(rf"\b(?:fans|followers|audience|engagers|supporters) of\s+{n}\b", t, re.I)
                or re.search(rf"\b(?:engag(?:e|ed|ing) with|follow(?:ed|ing)?|interact(?:ed|ing)? with)\s+{n}\b", t, re.I))


def title_viewing_ask(text, name=''):
    """The ask is about viewers of titles: it names the works (films,
    movies, titles, episodes, series), or watching / streaming the person
    on a service, or viewers of the person."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if _WORKS_RX.search(t):
        return True
    n = _name_rx(name)
    if n:
        if re.search(rf"\b(?:viewers|viewership|watchers|streamers) of\s+(?:a |the )?{n}\b", t, re.I):
            return True
        if re.search(rf"\b{_VIEW_VERBS}\s+(?:a |the |any |his |her |their )?{n}\b", t, re.I):
            return True
        if re.search(rf"\b{n}\s+(?:viewers|viewership|on (?:{_PLATFORMS}))\b", t, re.I):
            return True
    return bool(re.search(rf"\b{_VIEW_VERBS}\b[^.?!]{{0,80}}\bon (?:the )?(?:{_PLATFORMS})\b", t, re.I))


def person_page_for_title_ask(text, category, name=''):
    """True when a profile of a PERSON would be bound as the base of a
    question about viewers of titles: the profile is not the anchor
    then. False for a brand or title profile, and for a question about
    the person's own audience."""
    if not is_person_category(category):
        return False
    if audience_of_person(text, name):
        return False
    return title_viewing_ask(text, name)


def profile_category(s3_key='', name='', catalog=None):
    """The catalog's BRAND CATEGORY for a profile ('ACTOR', 'QSR', ...)
    by s3 key, else by display name; '' when unknown. `catalog` is the
    compact catalog list ([{s3_key, display_name, subject, category}]);
    None reads the dashboard's own. Never raises."""
    try:
        if catalog is None:
            from .legacy import chat as _chat
            catalog = _chat._profile_catalog_for_chat()
    except Exception:
        catalog = []
    key = str(s3_key or '').strip()
    nm = _norm(str(name or '').split(' - ')[0])
    by_name = ''
    for e in (catalog or []):
        if not isinstance(e, dict):
            continue
        if key and str(e.get('s3_key') or '').strip() == key:
            return str(e.get('category') or '').strip().upper()
        if nm and not by_name and (_norm(str(e.get('display_name') or '').split(' - ')[0]) == nm
                                   or _norm(str(e.get('subject') or '').split(' - ')[0]) == nm):
            by_name = str(e.get('category') or '').strip().upper()
    return by_name


def _canon_platform(p):
    low = str(p or '').lower()
    if low in ('streaming', 'any streaming service'):
        return 'any streaming service'
    if low in ('free tv', 'free ad-supported'):
        return 'free ad-supported TV'
    return {'prime video': 'Prime Video', 'netflix': 'Netflix', 'hulu': 'Hulu', 'max': 'Max', 'hbo max': 'HBO Max',
            'peacock': 'Peacock', 'youtube': 'YouTube', 'tubi': 'Tubi', 'pluto tv': 'Pluto TV', 'roku': 'Roku',
            'the roku channel': 'The Roku Channel', 'starz': 'Starz', 'freevee': 'Freevee', 'crunchyroll': 'Crunchyroll'}.get(low) \
        or _PLATFORM_NAMES.get(low, p)


def viewing_platform(text):
    """The service the ask says the viewing happened on ('Prime Video'),
    or '' when none is named without a negation in front of it."""
    t = ' '.join(str(text or '').split())
    for m in _PLATFORM_RX.finditer(t):
        if _NEGATED_BEFORE_RX.search(t[:m.start()]):
            continue
        return _canon_platform(m.group('p'))
    return ''


# A platform belongs to the audience whose clause names it (Grace Yin,
# 2026-11-08: "the overlap between Hazbin Hotel viewers and viewers of
# adult animation and anime on Netflix, Crunchyroll and HIDIVE" opened
# "On people who watched Hazbin Hotel on Netflix in the window:" because
# the first platform anywhere in the ask was taken as the title's). The
# gap between the title and its "on <service>" never crosses a clause
# break or a word that starts the other audience.
_CLAUSE_BREAK_WORD = r"\b(?:and|or|vs\.?|versus|against|compared|while|but|overlap(?:s|ping|ped)?|with|who|that|which|also|plus|then)\b"
_SAME_CLAUSE_GAP = r"(?:(?!" + _CLAUSE_BREAK_WORD + r")[^.?!,;:()]){0,60}?"
_ANY_PLATFORM_RX_CACHE = []


def _any_platform_rx():
    """Every service name we know (``_PLATFORM_NAMES`` below, plus HIDIVE),
    bare, longest first; 'max' only counts with 'on' in front of it."""
    if not _ANY_PLATFORM_RX_CACHE:
        names = sorted((re.escape(k) for k in _PLATFORM_NAMES if k != 'max'), key=len, reverse=True)
        _ANY_PLATFORM_RX_CACHE.append(re.compile(r"(?<![\w+])(?P<p>" + "|".join(names) + r"|hidive)(?![\w+])", re.I))
    return _ANY_PLATFORM_RX_CACHE[0]


def title_platform(text, title):
    """The service the ask names for THIS title's own viewing ('Prime
    Video' in 'watched The Love Hypothesis on Prime Video'), or '' when
    the title's clause names none. A service named for another audience
    in the same ask ('... and viewers of anime on Netflix') is never
    borrowed."""
    n = _name_rx(re.sub(r"\s*\([^()]*\)\s*$", "", str(title or '')))   # 'Dune (2021)' is asked about as 'Dune'
    t = ' '.join(str(text or '').split())
    if not n or not t:
        return ''
    after = re.compile(rf"\b{n}(?:'s)?\b{_SAME_CLAUSE_GAP}\bon (?:the )?(?P<p>{_PLATFORMS})(?![\w+])", re.I)
    for m in after.finditer(t):
        if _NEGATED_BEFORE_RX.search(t[:m.start('p')]):
            continue
        return _canon_platform(m.group('p'))
    before = re.compile(rf"(?:\bon (?:the )?(?P<p>{_PLATFORMS})(?![\w+]),?\s+(?:the\s+)?|(?P<p2>{_PLATFORMS})'s\s+){n}\b", re.I)
    m = before.search(t)
    if m and not _NEGATED_BEFORE_RX.search(t[:m.start()]):
        return _canon_platform(m.group('p') or m.group('p2'))
    return ''


def platforms_named(text):
    """Every service the ask names, in order, deduped (with or without
    'on' in front of it; a negated mention does not count)."""
    t = ' '.join(str(text or '').split())
    hits = []
    for m in _PLATFORM_RX.finditer(t):          # 'on Max' counts; a bare 'max' is a word
        hits.append((m.start(), _canon_platform(m.group('p'))))
    for m in _any_platform_rx().finditer(t):
        low = m.group('p').lower()
        hits.append((m.start(), _PLATFORM_NAMES.get(low) or ('HIDIVE' if low == 'hidive' else m.group('p'))))
    out = []
    for pos, p in sorted(hits):
        if _NEGATED_BEFORE_RX.search(t[:pos]) or p in out:
            continue
        out.append(p)
    return out


def _two_audience_ask(text):
    try:
        from . import overlap_read as _ov
        return bool(_ov.two_audiences(text))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The universe label is a plain sentence, never a concatenated key (Jenna
# 2026-11-04, Jay Patel's Love Hypothesis read opened with "On people who
# watched a The Love Hypothesis (Amazon Prime Video) - New to Prime Video
# (zero plays in the six months before Sep 23 2026) with prior Netflix or
# Hulu activity series on Prime Video in the window:"). The subject's
# ' - <cut>' suffix becomes a plain cohort clause, a trailing '(<platform>)'
# comes off the title, a title takes no article and no noun (the title
# names itself; 'film' / 'series' is for a person's works), and an
# imperative "Show" in the ask is not a series.
# ---------------------------------------------------------------------------
_PLATFORM_NAMES = {
    'amazon prime video': 'Prime Video', 'prime video': 'Prime Video', 'amazon prime': 'Prime Video',
    'netflix': 'Netflix', 'hulu': 'Hulu', 'max': 'Max', 'hbo max': 'HBO Max', 'peacock': 'Peacock',
    'paramount+': 'Paramount+', 'paramount plus': 'Paramount+', 'disney+': 'Disney+', 'disney plus': 'Disney+',
    'apple tv+': 'Apple TV+', 'apple tv': 'Apple TV+', 'youtube': 'YouTube', 'tubi': 'Tubi',
    'pluto tv': 'Pluto TV', 'roku': 'Roku', 'the roku channel': 'The Roku Channel', 'starz': 'Starz',
    'freevee': 'Freevee', 'crunchyroll': 'Crunchyroll', 'amc+': 'AMC+', 'discovery+': 'Discovery+',
    'espn+': 'ESPN+', 'spotify': 'Spotify', 'apple music': 'Apple Music', 'siriusxm': 'SiriusXM',
}
_TITLE_CATEGORY_RX = re.compile(r"^(?:MOVIE|SERIES\b.*|PODCAST|GAMES?(?:\s*-\s*PLAYERS)?|GAME PLAYERS|VERTICAL SHORTS|FRANCHISE)$", re.I)
_COMMON_CUT_WORDS = frozenset((
    'gift', 'digital', 'online', 'heavy', 'light', 'new', 'lapsed', 'active', 'frequent', 'loyal', 'first',
    'time', 'repeat', 'weekly', 'daily', 'monthly', 'premium', 'paid', 'free', 'mobile', 'sports', 'music',
    'movie', 'film', 'series', 'podcast', 'game', 'video', 'streaming', 'prime', 'holiday', 'early', 'late',
    'young', 'adult', 'teen', 'senior', 'super', 'casual', 'avid', 'potential', 'current', 'former', 'recent'))
_PEOPLE_NOUN_TAIL_RX = re.compile(
    r"\b(?:givers|users|members|subscribers|shoppers|buyers|viewers|owners|renters|parents|students|"
    r"listeners|players|readers|fans|customers|drinkers|eaters|travelers|gamers|switchers|voters|"
    r"enthusiasts|collectors|bettors|purchasers|watchers)$", re.I)


def split_subject(subject):
    """(head, cut) for 'Head - Cut' subjects: the split is the first
    ' - ' outside parentheses. ('Head', '') when there is none."""
    s = ' '.join(str(subject or '').split()).strip()
    depth = 0
    for i, ch in enumerate(s):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif ch == '-' and depth == 0 and i > 0 and s[i - 1] == ' ' and i + 1 < len(s) and s[i + 1] == ' ':
            return s[:i].strip(), s[i + 2:].strip()
    return s, ''


def strip_platform_paren(head):
    """('The Love Hypothesis', 'Prime Video') for 'The Love Hypothesis
    (Amazon Prime Video)'; (head, '') when the parenthetical is not a
    service we know."""
    s = ' '.join(str(head or '').split()).strip()
    m = re.search(r"\s*\(\s*(?:on\s+)?([^()]{2,40}?)\s*\)\s*$", s)
    if m:
        plat = _PLATFORM_NAMES.get(m.group(1).strip().lower())
        if plat:
            return s[:m.start()].strip(), plat
    return s, ''


def _works_noun(text, category=''):
    """'film' / 'series' / 'song' / 'title' for a person's works, from
    the profile's category first (MOVIE -> film, SERIES -> series), then
    the ask. An imperative 'show' ("Show panel counts") is not a series."""
    cat = str(category or '').strip().upper()
    if cat == 'MOVIE':
        return 'film'
    if cat.startswith('SERIES'):
        return 'series'
    t = str(text or '')
    if re.search(r"\b(?:films?|movies?|box office)\b", t, re.I):
        return 'film'
    if re.search(r"\b(?:series|episodes?|seasons?)\b", t, re.I) \
            or re.search(r"(?<![.!?]\s)(?<!^)\b(?:shows?)\b(?!\s+(?:me|us|the|a|an|how|what|which|each|both|only|all|"
                         r"counts?|figures?|numbers?|results?|them|it|this|that|these|those|your|our|by|per))", t, re.I | re.M):
        return 'series'
    if re.search(r"\b(?:albums?|songs?|tracks?|concerts?|tour)\b", t, re.I):
        return 'song'
    return 'title'


def cut_clause(cut):
    """The cut's own words as a plain cohort clause for the universe
    label ('' when there is no cut). 'New to Prime Video (zero plays in
    the six months before Sep 23 2026) with prior Netflix or Hulu
    activity' -> ' who had no Prime Video plays in the six months before
    Sep 23 2026 and had watched Netflix or Hulu in that time'."""
    c = ' '.join(str(cut or '').split()).strip(' ,;')
    if not c:
        return ''
    low = c.lower()
    m = re.match(r"^new to (?P<p>[^(]+?)\s*(?:\((?:zero|no)\s+(?:[\w+ ]*?\s)?plays?(?: of any title)?\s+(?:in|during|over)\s+"
                 r"(?P<per>[^)]+)\))?(?:\s*,?\s*(?P<rest>(?:with|and|who|whose)\b.*))?$", c, re.I)
    if m:
        p = m.group('p').strip()
        per = (m.group('per') or '').strip()
        rest = (m.group('rest') or '').strip()
        first = f"who had no {p} plays in {per}" if per else f"who came to {p} new"
        tail = ''
        if rest:
            mr = re.match(r"^with (?:prior |earlier |previous |existing )?(?P<svc>.+?)\s+(?:activity|viewing|use|usage|plays?|habit)$",
                          rest, re.I)
            if mr:
                tail = f" and had watched {mr.group('svc').strip()} " + ('in that time' if per else 'before')
            else:
                tail = ' ' + rest
        return ' ' + first + tail
    if low in ('female', 'women', 'woman'):
        return ' who are women'
    if low in ('male', 'men', 'man'):
        return ' who are men'
    ma = re.match(r"^(?:ages?\s+)?(\d{2})\s*(?:-|to)\s*(\d{2})$", low)
    if ma:
        return f" aged {ma.group(1)} to {ma.group(2)}"
    ma = re.match(r"^(?:ages?\s+)?(\d{2})\s*\+$", low)
    if ma:
        return f" aged {ma.group(1)} and over"
    if low in ('avid fan', 'avid fans', 'avid'):
        return ' who are avid fans'
    if low in ('casual fan', 'casual fans', 'casual'):
        return ' who are casual fans'
    if low in ('gen z', 'millennials', 'gen x', 'boomers', 'gen alpha'):
        return f" who are {c}"
    if _PEOPLE_NOUN_TAIL_RX.search(c):
        words = c.split()
        out = []
        for i, w in enumerate(words):
            if w.isupper() and len(w) > 1:
                out.append(w)                      # an acronym stays
            elif i == len(words) - 1 or w.lower() in _COMMON_CUT_WORDS:
                out.append(w.lower())              # the people noun and ordinary words read lower case
            else:
                out.append(w)                      # a brand or a place keeps its casing
        return f" who are {' '.join(out)}"
    return f" ({c})"


def subject_category(subject, base=None, catalog=None):
    """The catalog category of the subject a universe label is built
    for: the base's own when the subject is the base, else a catalog
    lookup by the subject's head. '' when unknown. Never raises."""
    head, _cut = split_subject(subject)
    try:
        b = base if isinstance(base, dict) else {}
        bhead, _bc = split_subject(b.get('subject') or '')
        if bhead and _norm(bhead) == _norm(head):
            return profile_category(b.get('s3_key') or '', bhead, catalog=catalog)
        if not head:
            return ''
        cat = profile_category('', head, catalog=catalog)
        if not cat:
            title, plat = strip_platform_paren(head)
            if plat and title:
                cat = profile_category('', title, catalog=catalog)
        return cat
    except Exception:
        return ''


def title_universe_label(text, subject, category=''):
    """The universe a title-viewing read counts, in plain words for the
    opener and the Base line. A person's works: 'people who watched a
    Jason Statham film on Prime Video in the window'. A title: 'people
    who watched The Love Hypothesis on Prime Video in the window', plus
    the cut's clause when the subject carries one. '' when the subject
    already names its viewers ('Jason Statham film viewers (...)'), the
    ask is not about titles, or the subject is neither a person nor a
    title (a service, a brand)."""
    head, cut = split_subject(subject)
    if not head or re.search(r"\b(?:viewers?|watchers|audience|fans?|people|users|subscribers|buyers|listeners|readers|players)\b", head, re.I):
        return ''
    title, paren_plat = strip_platform_paren(head)
    if not title:
        return ''
    if not title_viewing_ask(text, title) or audience_of_person(text, title):
        return ''
    cat = str(category or '').strip().upper()
    t = str(text or '')
    if cat and is_person_category(cat):
        is_title = False
    elif cat and _TITLE_CATEGORY_RX.match(cat):
        is_title = True
    elif cat:
        return ''          # a service, a brand, a persona: no title universe to name
    elif paren_plat or title.lower().startswith('the '):
        is_title = True    # a title placement, or a name no person carries
    else:
        n = _name_rx(title)
        works = bool(re.search(rf"\b{n}(?:'s)?\s+(?:films?|movies?|titles?|series|shows?|episodes?|catalog(?:ue)?|filmography|"
                               rf"specials?|albums?|songs?|concerts?)\b", t, re.I)
                     or re.search(rf"\b(?:films?|movies?|titles?|series|shows?|episodes?|songs?|albums?)\s+(?:of|by|from|starring|with|featuring)\s+{n}\b", t, re.I)
                     or re.search(rf"\b{_VIEW_VERBS}\s+(?:a |the |any |his |her |their )?{n}\b", t, re.I))
        if not works:
            return ''
        is_title = False
    # the title's own service: named in its clause, or the only service
    # the ask names when the ask is about one audience; a second
    # audience's service is never borrowed (2026-11-08)
    plat = title_platform(t, title)
    if not plat and not _two_audience_ask(t):
        named = platforms_named(t)
        if len(named) == 1:
            plat = viewing_platform(t) or named[0]
    plat = plat or paren_plat
    where = f" on {plat}" if plat else ''
    if is_title:
        return f"people who watched {title}{where} in the window{cut_clause(cut)}"
    work = _works_noun(t, cat)
    art = 'an' if work[:1] in 'aeiou' else 'a'
    return f"people who watched {art} {title} {work}{where} in the window{cut_clause(cut)}"


def title_read_guidance(universe_label):
    """The prompt block for a title-viewing read: the universe, what
    anchors it, and the one-definition, one-window rule for every figure
    in the reply. Every figure the question asks for is delivered."""
    u = str(universe_label or '').strip()
    return (
        'TITLE READ (house rule, 2026-11-04):\n'
        f'- The universe is {u}: the people who watched the title(s). It is NOT the audience of the person '
        'behind them (the people with digital touchpoints to them: their search, social, media, commerce and owned '
        'activity). Never size it from, or hold it to, the person\'s own profile.\n'
        '- Anchor the universe on title viewing: the tracker figures carried in the grounding above when the titles '
        'have one, where the titles are carried and watched, and public title-viewing anchors; reason the share of '
        'US viewers from those. Size at the low end of what the anchors support.\n'
        '- One universe definition and one window carry EVERY figure in the reply. Each count and each share states '
        'the same base and the same window; a figure on another base (another title, another window, another cut of '
        'the share) is labeled as such.\n'
        '- Every cell the question asks for is in the reply with a figure (every part of a split, every count and '
        'share named). Never say a figure is missing or unavailable, and never leave a cell blank.\n'
        f'- The first sentence names the universe in plain words ("On {u}:") so the reader knows which people the '
        'figures describe.\n')


__all__ = ['EVERYONE', 'is_everyone', 'quoted_subject', 'page_rejected', 'page_base_stands', 'base_stands', 'place_in_list',
           'fragment', 'not_a_subject', 'no_base_subject', 'PERSON_CATEGORIES', 'is_person_category', 'audience_of_person',
           'title_viewing_ask', 'person_page_for_title_ask', 'profile_category', 'viewing_platform', 'title_universe_label',
           'title_read_guidance', 'split_subject', 'strip_platform_paren', 'cut_clause', 'subject_category']
