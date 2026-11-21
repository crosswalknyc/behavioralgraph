"""The title the words name is the title the answer is about (Jenna
2026-11-19).

A client seat asked for Chicago P.D. and was answered about Law & Order,
twice, on 2026-11-18:

    "can you do the same for chicago pd i only need for the year 2020
     viewership minutes on amazon prime"
        -> "I can run the Law & Order read again on January 1 to
            December 31, 2020. Pulling this data will cost $100."
    "no i need chicago pd"
        -> "On it: reading Law & Order: Special Victims Unit now."
    "no i need CHICAGO PD show minutes viewed in 2020 on amazon prime"
        -> "On it: reading Law & Order: Special Victims Unit now."

Four things went wrong, each of them general:

  1. "the same for <title>" was read as "the last read again, on another
     window", and "no i need <title>" as a correction that keeps the last
     read's subject. Neither asked whether the words name another title.
  2. The binder's own promise is "words beat thread beat page", but it
     could not see a title typed in lower case that is not on the shelf
     ("chicago pd"), so the thread won.
  3. The header of a landed read ("On Law & Order: Special Victims Unit
     on Amazon Prime Video:") was cut at its first colon, so the thread
     carried the franchise ("Law & Order") in place of the title. The
     same cut turns NCIS: Sydney into NCIS, Outlander: Blood of My Blood
     into Outlander and Power Book II: Ghost into Power Book II.
  4. A file named "<Title> on <Service>" was taken as the base of a
     question about another title on that service, because the service's
     words alone are two thirds of its name ("Power On Amazon Prime" for
     a Special Victims Unit question on Amazon Prime).

The rule, in one place:

  * When the message names a title, the answer is about that title. A
    subject carried from earlier turns (the last read, the thread, the
    page, a stored figure) applies only when the message names no title,
    or points back at it ("them", "these viewers").
  * Two titles of one franchise are two titles. Law & Order: Special
    Victims Unit is not Law & Order, NCIS: Sydney is not NCIS, Power Book
    II: Ghost is not Power, Outlander: Blood of My Blood is not
    Outlander, 1883 is not Yellowstone, a film is not its series.
  * One title has one name. "Chicago PD", "Chicago P.D." and "chicago
    pd" are Chicago P.D.
  * When nothing is held for the named title, the reply is the standing
    one for a title that is not on the dashboard yet, about that title.
    A sibling's file, figure or stored answer is never used in its place.

What is here: the family list (the titles whose names nest or that
readers shorten), ``known`` (the titles a text names), ``title_key`` /
``same_title`` (is this label that title), ``other_than`` (the title the
words name when it is not the carried one), ``slot_title`` (the name in
"the same for X", "what about X", "no i need X"), ``read_head`` (the
subject of a landed read, colon and all), and the small hooks the binder,
the continuation classifier, the base resolver and the ledger call.

Pure text. No model, no S3, no Flask import at module level. Never
raises. Kill switch ``PM_NAMED_TITLE=0``. Nothing here takes a request
field. Regression: scripts/test_pm_named_title_is_the_subject.py.
"""
from __future__ import annotations

import os
import re
from collections import namedtuple
from functools import lru_cache

__all__ = ['enabled', 'fold', 'FAMILIES', 'Hit', 'known', 'asked', 'is_list', 'canonical', 'respell', 'core', 'title_key', 'same_title', 'siblings',
           'points_back', 'slot_title', 'extension', 'other_than', 'named', 'sole', 'mate_named', 'read_head', 'bound_words', 'stand_down',
           'base_fits', 'held', 'bucket_fits', 'say_left_out', 'tidy', 'rebound', 'relabel', 'spelling_key', 'NOT_HELD']

# what a reader is told when the title they named is not on the dashboard
# and a sibling of it is: one plain sentence ahead of the standing reply
NOT_HELD = "There is nothing on {title} on the dashboard yet. {sibling} is a different title, so I have left it out."


def enabled():
    return os.environ.get('PM_NAMED_TITLE', '1') != '0'


def fold(s):
    """One spelling for comparing names: case, the ampersand, and the
    marks inside a name do not count ("Chicago P.D." and "chicago pd",
    "Hawai'i" and "Hawaii", "Law & Order" and "law and order")."""
    t = str(s or '').lower().replace('&', ' and ').replace('+', ' plus ')
    t = re.sub(r"['\u2019\u2018`]", '', t)
    t = re.sub(r"(?<=[a-z])\.(?=[a-z])", '', t)        # p.d -> pd
    t = re.sub(r"(?<=\b[a-z])\.(?=\s|$)", '', t)       # the closing dot of an initialism
    return ' '.join(re.sub(r"[^a-z0-9]+", ' ', t).split())


# ----------------------------------------------------------------- the families
def _t(display, *aliases, cased=(), ident=(), before=None, after=None, form=''):
    return {'display': display, 'aliases': tuple(aliases), 'cased': tuple(cased), 'ident': tuple(ident),
            'before': before, 'after': after, 'form': form}


# Titles whose names nest inside one another, or that readers shorten. An
# alias is written folded; ``cased`` is matched as typed (a word that is
# also an ordinary word); ``ident`` names the title when it stands alone as
# a label and is never looked for inside a sentence.
FAMILIES = (
    ('Law & Order', (
        _t('Law & Order: Special Victims Unit', 'law and order special victims unit', 'law and order svu',
           'special victims unit', 'special victims uni', 'svu'),
        _t('Law & Order: Organized Crime', 'law and order organized crime', cased=('Organized Crime',)),
        _t('Law & Order: Criminal Intent', 'law and order criminal intent', cased=('Criminal Intent',)),
        _t('Law & Order: Trial by Jury', 'law and order trial by jury'),
        _t('Law & Order: LA', 'law and order la', 'law and order los angeles'),
        _t('Law & Order: UK', 'law and order uk'),
        _t('Law & Order Toronto: Criminal Intent', 'law and order toronto criminal intent', 'law and order toronto'),
        _t('Law & Order True Crime', 'law and order true crime'),
        _t('Law & Order', 'law and order'),
    )),
    ('One Chicago', (
        _t('Chicago P.D.', 'chicago pd', 'chicago p d'),
        _t('Chicago Fire', 'chicago fire', after=r"\s+(?:fc|f\.c\.|soccer|mls)\b"),
        _t('Chicago Med', 'chicago med'),
        _t('Chicago Justice', 'chicago justice'),
    )),
    ('FBI', (
        _t('FBI: Most Wanted', 'fbi most wanted'),
        _t('FBI: International', 'fbi international'),
        _t('FBI', cased=('FBI',), ident=('fbi',), after=r"\s+(?:agents?|director|investigation|raid|files)\b"),
    )),
    ('NCIS', (
        _t('NCIS: Los Angeles', 'ncis los angeles', 'ncis la'),
        _t('NCIS: New Orleans', 'ncis new orleans', 'ncis nola'),
        _t("NCIS: Hawai'i", 'ncis hawaii'),
        _t('NCIS: Sydney', 'ncis sydney'),
        _t('NCIS: Origins', 'ncis origins'),
        _t('NCIS: Tony & Ziva', 'ncis tony and ziva', 'tony and ziva'),
        _t('NCIS: New York', 'ncis new york', 'ncis ny'),
        _t('NCIS', 'ncis'),
    )),
    ('Power', (
        _t('Power Book II: Ghost', 'power book ii ghost', 'power book 2 ghost', 'power book ii', 'power book 2', 'power ghost'),
        _t('Power Book III: Raising Kanan', 'power book iii raising kanan', 'power book 3 raising kanan', 'power book iii',
           'power book 3', 'power raising kanan', 'raising kanan'),
        _t('Power Book IV: Force', 'power book iv force', 'power book 4 force', 'power book iv', 'power book 4', 'power force'),
        _t('Power: Origins', 'power origins'),
        _t('Power', ident=('power',)),
    )),
    ('Outlander', (
        _t('Outlander: Blood of My Blood', 'outlander blood of my blood', 'blood of my blood'),
        _t('Outlander', 'outlander', before=r"mitsubishi\s+$"),
    )),
    ('Yellowstone', (
        _t('1883', '1883', before=r"(?:[/\-.]|\b(?:in|since|from|year|of)\s+)$", after=r"\s*[/\-.]\s*\d"),
        _t('1923', '1923', before=r"(?:[/\-.]|\b(?:in|since|from|year|of)\s+)$", after=r"\s*[/\-.]\s*\d"),
        _t('The Madison', cased=('The Madison',), ident=('the madison', 'madison')),
        _t('Marshals', cased=('Marshals',), ident=('marshals', 'y marshals')),
        _t('Yellowstone', 'yellowstone', after=r"\s+(?:national\s+park|park|caldera|supervolcano)\b"),
    )),
    ('The Walking Dead', (
        _t('Fear the Walking Dead', 'fear the walking dead'),
        _t('The Walking Dead: Daryl Dixon', 'the walking dead daryl dixon', 'walking dead daryl dixon', cased=('Daryl Dixon',)),
        _t('The Walking Dead: Dead City', 'the walking dead dead city', 'walking dead dead city', cased=('Dead City',)),
        _t('The Walking Dead: The Ones Who Live', 'the walking dead the ones who live', 'walking dead the ones who live',
           'the ones who live'),
        _t('The Walking Dead: World Beyond', 'the walking dead world beyond', 'walking dead world beyond'),
        _t('The Walking Dead', 'the walking dead', 'walking dead'),
    )),
    ('Game of Thrones', (
        _t('House of the Dragon', 'house of the dragon'),
        _t('A Knight of the Seven Kingdoms', 'a knight of the seven kingdoms', 'knight of the seven kingdoms'),
        _t('Game of Thrones', 'game of thrones'),
    )),
    ('9-1-1', (
        _t('9-1-1: Lone Star', '9 1 1 lone star', '911 lone star'),
        _t('9-1-1: Nashville', '9 1 1 nashville', '911 nashville'),
        _t('9-1-1', '9 1 1', ident=('911',)),
    )),
    ('CSI', (
        _t('CSI: Miami', 'csi miami'),
        _t('CSI: NY', 'csi ny', 'csi new york'),
        _t('CSI: Vegas', 'csi vegas'),
        _t('CSI: Cyber', 'csi cyber'),
        _t('CSI: Crime Scene Investigation', 'csi crime scene investigation', 'csi'),
    )),
    ('Criminal Minds', (
        _t('Criminal Minds: Evolution', 'criminal minds evolution'),
        _t('Criminal Minds', 'criminal minds'),
    )),
    ('Dexter', (
        _t('Dexter: New Blood', 'dexter new blood'),
        _t('Dexter: Original Sin', 'dexter original sin'),
        _t('Dexter: Resurrection', 'dexter resurrection'),
        _t('Dexter', cased=('Dexter',), ident=('dexter',)),
    )),
    ("Grey's Anatomy", (
        _t('Station 19', 'station 19'),
        _t("Grey's Anatomy", 'greys anatomy'),
    )),
    ('Bosch', (
        _t('Bosch: Legacy', 'bosch legacy'),
        _t('Bosch', cased=('Bosch',), ident=('bosch',)),
    )),
    ('Vikings', (
        _t('Vikings: Valhalla', 'vikings valhalla'),
        _t('Vikings', ident=('vikings',)),
    )),
    ('The Rookie', (
        _t('The Rookie: Feds', 'the rookie feds', 'rookie feds'),
        _t('The Rookie', 'the rookie'),
    )),
    ('Suits', (
        _t('Suits LA', 'suits la', 'suits l a'),
        _t('Suits', ident=('suits',)),
    )),
    ('Dune', (
        _t('Dune: Prophecy', 'dune prophecy'),
        _t('Dune: Part Two', 'dune part two', 'dune part 2', 'dune 2', form='film'),
        _t('Dune', ident=('dune',)),
    )),
)

Hit = namedtuple('Hit', 'display key family start end raw')

_SEP = r"[\s:,\-\u2013\u2014/]+"


def _word_rx(w):
    if w == 'and':
        return r"(?:and|n|plus)"
    if w.isdigit():
        return re.escape(w)
    if w.isalpha() and len(w) <= 3:
        return r"(?:\.\s?)?".join(re.escape(c) for c in w) + r"\.?"           # pd: PD, P.D., P. D.
    return r"['\u2019\u2018`]?".join(re.escape(c) for c in w)                  # hawaii: Hawai'i; greys: Grey's


def _alias_rx(alias, cased=False):
    words = alias.split()
    body = _SEP.join((re.escape(w) if cased else _word_rx(w)) for w in words)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", 0 if cased else re.I)


_TITLES = {}        # key -> entry (with family)
_EXACT = {}         # a folded name standing alone -> key
_MATCHERS = []      # (length, rx, key)
for _fam, _entries in FAMILIES:
    for _e in _entries:
        _k = fold(_e['display'])
        _TITLES[_k] = dict(_e, key=_k, family=_fam,
                           before_rx=re.compile(_e['before'], re.I) if _e.get('before') else None,
                           after_rx=re.compile(_e['after'], re.I) if _e.get('after') else None)
        _EXACT[_k] = _k
        for _a in _e['aliases']:
            _EXACT[fold(_a)] = _k
            _MATCHERS.append((len(_a), _alias_rx(_a), _k))
        for _a in _e['cased']:
            _EXACT[fold(_a)] = _k
            _MATCHERS.append((len(_a), _alias_rx(_a, cased=True), _k))
        for _a in _e['ident']:
            _EXACT.setdefault(fold(_a), _k)
        if fold(_e['display']) not in [fold(a) for a in _e['aliases']] and not _e['cased'] and not _e['ident']:
            _MATCHERS.append((len(_k), _alias_rx(_k), _k))
_MATCHERS.sort(key=lambda x: -x[0])


def _prep(text):
    return str(text or '').replace('&amp;', '&').replace('&', ' and ')


def known(text):
    """The listed titles a text names, in the order it names them, each
    once. The longest name wins a run of words, so "Law & Order: Special
    Victims Unit" is that title and not Law & Order as well."""
    return list(_known(str(text or '')))


@lru_cache(maxsize=2048)
def _known(text):
    t = _prep(text)
    if not t.strip():
        return ()
    hits, claimed = [], []
    try:
        for _n, rx, key in _MATCHERS:
            for m in rx.finditer(t):
                a, b = m.span()
                if any(a < cb and b > ca for ca, cb in claimed):
                    continue
                e = _TITLES[key]
                if e['before_rx'] is not None and e['before_rx'].search(t[max(0, a - 24):a]):
                    continue
                if e['after_rx'] is not None and e['after_rx'].match(t[b:b + 24]):
                    continue
                claimed.append((a, b))
                hits.append(Hit(e['display'], key, e['family'], a, b, t[a:b]))
    except Exception:
        return ()
    hits.sort(key=lambda h: h.start)
    out, seen = [], set()
    for h in hits:
        if h.key not in seen:
            seen.add(h.key)
            out.append(h)
    return tuple(out)


# ----------------------------------------------------------------- a label's title
try:
    from .platform_scope import _ALIAS as _PS_ALIAS
    _PLAT_NAMES = set(_PS_ALIAS)
except Exception:                                       # pragma: no cover
    _PLAT_NAMES = set()
_PLAT_NAMES |= {'amazon prime video', 'prime video', 'amazon prime', 'amazon', 'hbo max', 'hbo', 'netflix', 'hulu', 'peacock',
                'paramount+', 'paramount plus', 'disney+', 'disney plus', 'apple tv+', 'apple tv', 'starz', 'showtime', 'amc+',
                'bet+', 'tubi', 'pluto tv', 'youtube', 'max', 'the roku channel', 'discovery+', 'britbox', 'mgm+', 'fubo',
                'sling', 'philo', 'youtube tv', 'hulu live', 'crunchyroll', 'espn+', 'fox nation', 'vix'}
_PLAT_ALT = '|'.join(sorted((re.escape(p) for p in _PLAT_NAMES), key=len, reverse=True))
_PLAT_WORDS = frozenset(w for p in _PLAT_NAMES for w in re.sub(r"[^a-z0-9]+", ' ', p).split()) | {'platform', 'service', 'app', 'plus'}
_AUD = (r"(?:viewers?|fans?|audiences?|watchers?|streamers?|subscribers?|listeners?|readers?|players?|buyers?|customers?|"
        r"shoppers?|users?|members?|consumers?|households?|people)")
_PLAT_TAIL_RX = re.compile(
    rf"\s+(?:{_AUD}\s+)?(?:on|in|at|from|across|via|through)\s+(?:the\s+)?(?:{_PLAT_ALT})(?![\w+])(?:\s+(?:platform|service|app))?"
    rf"(?:\s*(?:&|and|,|or)\s*(?:the\s+)?(?:{_PLAT_ALT})(?![\w+]))*\s*$", re.I)
_PLAT_BARE_TAIL_RX = re.compile(rf"\s+(?:{_PLAT_ALT})(?![\w+])\s*$", re.I)
_AUD_TAIL_RX = re.compile(rf"\s+{_AUD}\s*$", re.I)
_SEASON_TAIL_RX = re.compile(r"\s+(?:season|series|vol(?:ume)?\.?)\s*\d+\b.*$|\s+s\d{1,2}(?:e\d{1,3})?\b.*$", re.I)
_TYPE_LEAD_RX = re.compile(r"^(?:the\s+)?(?:tv\s+)?(?:show|series|film|movie|title|program|programme|drama|sitcom)\s+(?=\S)", re.I)
# a kind word typed after a name in lower case ("psych show", "Landman the series"); a capital is part of the title
# ("The Morning Show", "Jet Lag: The Game")
_TYPE_TAIL_RX = re.compile(r"\s+(?:the\s+)?(?:tv\s+)?(?:show|series|film|movie|title|program|programme)\s*$")
_SLOT_TYPE_TAIL_RX = re.compile(r"\s+(?:the\s+)?(?:tv\s+)?(?:show|series|film|movie|title|program|programme|webtoon|comic|manga|book|novel|"
                                r"podcast|game|franchise)\s*$", re.I)
_GENERIC_LEFT = frozenset(('the', 'a', 'an', 'show', 'shows', 'series', 'tv', 'title', 'titles', 'program', 'drama', 'of', 'on',
                           'for', 'all', 'seasons', 'season', 'episodes', 'full', 'original', 'new', 'us'))
_FILM_RX = re.compile(r"\b(?:film|movie|feature|theatrical)\b", re.I)
_SERIES_RX = re.compile(r"\b(?:series|tv show|show|season|seasons|episodes?|sitcom)\b", re.I)


def _is_platform(s):
    return ' '.join(str(s or '').lower().split()) in _PLAT_NAMES


def core(label):
    """A stored label down to the title it is about (see ``_core``)."""
    return _core(str(label or ''))


@lru_cache(maxsize=4096)
def _core(label):
    """A stored label down to the title it is about: the cut, the service
    it was watched on, the audience noun and the season come off ("Law &
    Order: Special Victims Unit on Amazon Prime Video", "Chicago P.D.
    Viewers on Peacock - Avid Fan", "Fallout Season 2 Viewers On Prime
    Video"). A service's own name stays whole."""
    s = ' '.join(str(label or '').split())
    if not s:
        return ''
    try:
        if _is_platform(s):
            return s
        s = re.sub(r"\s+-\s+.*$", '', s)
        s = re.sub(r"\s*\([^)]*\)", '', s).strip()
        for _i in range(4):
            before = s
            for rx in (_PLAT_TAIL_RX, _AUD_TAIL_RX, _SEASON_TAIL_RX, _TYPE_TAIL_RX):
                cut = rx.sub('', s).strip()
                if cut and not _is_platform(s):
                    s = cut
            cut = _PLAT_BARE_TAIL_RX.sub('', s).strip()
            if cut and len(s.split()) > len(cut.split()) and not _is_platform(s) and fold(cut) not in _PLAT_WORDS:
                s = cut
            if s == before:
                break
        s = _TYPE_LEAD_RX.sub('', s).strip() or s
        return s.strip(' ,;:')
    except Exception:
        return ' '.join(str(label or '').split())


def title_key(label):
    """The one key for the title a label is about (see ``_title_key``)."""
    return _title_key(str(label or ''))


@lru_cache(maxsize=4096)
def _title_key(label):
    """The one key for the title a label is about ('' for an empty
    label). A listed title's key is its full name's, however the label
    spells it."""
    c = core(label)
    f = fold(c)
    if not f:
        return ''
    if f in _EXACT:
        return _EXACT[f]
    bare = re.sub(r"^(?:the|a|an) ", '', f)
    if bare in _EXACT:
        return _EXACT[bare]
    hits = known(c)
    if len(hits) == 1:
        left = fold(_prep(c)[:hits[0].start] + ' ' + _prep(c)[hits[0].end:]).split()
        fam = set(fold(hits[0].family).split())
        if all(w in _GENERIC_LEFT or w in fam for w in left):
            return hits[0].key
    return ' '.join(w for w in bare.split() if w not in ('tv', 'show', 'series')) or bare


def _plain(s):
    """A name with its case and its dots set aside, nothing else."""
    return ' '.join(str(s or '').lower().replace('.', '').split())


def respell(name):
    """A listed title typed without its capitals or its dots comes back
    with them ("Chicago P.D", "chicago pd" and "CHICAGO PD" are Chicago
    P.D.). A title typed another way ("FBI Most Wanted", "law and
    order"), a short form ("SVU") and any other name come back as given:
    a title keeps the name it was typed under."""
    try:
        raw = ' '.join(str(name or '').split())
        c = core(raw)
        e = _TITLES.get(title_key(raw))
        if e is None or not c or _plain(c) != _plain(e['display']) or c == e['display']:
            return str(name or '')
        i = raw.lower().find(c.lower())
        return (raw[:i] + e['display'] + raw[i + len(c):]) if i >= 0 else str(name or '')
    except Exception:
        return str(name or '')


def canonical(name):
    """The one name a listed title is shown under ("chicago pd" ->
    "Chicago P.D."); any other name comes back as given."""
    k = title_key(name)
    e = _TITLES.get(k)
    if e is None:
        return str(name or '')
    tail = ''
    try:
        raw = ' '.join(str(name or '').split())
        c = core(raw)
        if c and raw.lower().startswith(c.lower()) and len(raw) > len(c):
            tail = raw[len(c):]
    except Exception:
        tail = ''
    return e['display'] + tail


def _form(s):
    t = str(s or '')
    if _FILM_RX.search(t):
        return 'film'
    if _SERIES_RX.search(t):
        return 'series'
    return ''


def same_title(a, b):
    """True when two labels are about one title. Franchise mates are two
    titles; so are a film and a series of one name when both labels say
    which they are."""
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb or ka != kb:
        return False
    fa, fb = _form(a), _form(b)
    return not (fa and fb and fa != fb)


def siblings(a, b):
    """True when two labels are two different titles of one franchise:
    two listed titles of a family, or one name carrying the other with a
    subtitle after it ("Dune" and "Dune: Prophecy")."""
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb or ka == kb:
        return False
    ea, eb = _TITLES.get(ka), _TITLES.get(kb)
    if ea and eb:
        return ea['family'] == eb['family']
    ca, cb = core(a), core(b)
    return bool(_subtitle_after(cb, ca) or _subtitle_after(ca, cb))


_SUBTITLE_RX = (r"\s*(?::\s*(?=[A-Z0-9])(?!(?:Season|Seasons|Series|Episode|Episodes|Ep|Vol|Volume|S\d|Q[1-4])\b)"
                r"|\s+(?:Book|Part|Chapter)\s+(?:[IVX]+|\d+)\b|\s+(?:II|III|IV)\b)")


def _subtitle_after(text, base):
    """The longer title when ``base`` appears in ``text`` with a subtitle
    after it ("NCIS: Sydney", "Power Book II", "Top Gun 2"); '' otherwise."""
    b = ' '.join(str(base or '').split())
    t = str(text or '')
    if len(b) < 3 or not t:
        return ''
    try:
        words = [re.escape(w) for w in re.split(r"\s+", _prep(b)) if w]
        rx = re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?P<sub>" + _SUBTITLE_RX + r")", re.I)
        m = rx.search(_prep(t))
        if not m:
            return ''
        rest = _prep(t)[m.end():]
        sm = re.match(r"(?:[A-Z0-9][\w'\u2019&.]*|of|the|and|a|in|my|to|for|vs\.?)(?:\s+(?:[A-Z0-9][\w'\u2019&.]*|of|the|and|a|in|my|to|for))*", rest)
        sub = (sm.group(0) if sm else '').strip()
        sub = re.sub(r"\s+(?:of|the|and|a|in|my|to|for)$", '', sub)
        head = _prep(t)[m.start():m.end()]
        if not sub and not re.search(r"(?:Book|Part|Chapter|\d|II|III|IV)\b", head[len(b):], re.I):
            return ''
        return ' '.join((head + sub).split()).replace(' and ', ' & ') if '&' in str(text) else ' '.join((head + sub).split())
    except Exception:
        return ''


def extension(text, held):
    """The longer title the words name when they carry the held title's
    name with a subtitle after it; '' otherwise."""
    return _subtitle_after(text, core(held))


# ----------------------------------------------------------------- what the words name
_POINTS_BACK_RX = re.compile(
    r"\b(?:this|these|those|that)\s+(?:same\s+)?(?:audience|profile|viewers?|people|fans?|users?|subscribers?|group|cohort|file|"
    r"data|page|customers?|shoppers?|buyers?|listeners?|readers?|members?|households?|consumers?|segment|universe)\b|"
    r"\b(?:them|they|their|theirs)\b|\bthe\s+same\s+(?:audience|people|viewers|group|cohort|fans|users|subscribers)\b|"
    r"\b(?:its|the)\s+(?:viewers|audience|fans|subscribers)\b(?!\s+(?:of|for|to)\b)|\bhere\b(?!\s+(?:is|are|'s)\b)", re.I)


_PUSHED_RX = re.compile(
    r"(?:\b(?:not|isn'?t|wasn'?t|instead\s+of|rather\s+than|other\s+than|nothing\s+to\s+do\s+with|unrelated\s+to|"
    r"stop\s+(?:bringing\s+up|showing|giving|using|mentioning)(?:\s+me)?|don'?t\s+(?:want|need|care\s+about)|"
    r"never\s+asked\s+(?:for|about)|did\s*n'?t\s+ask\s+(?:for|about)|"
    r"why\s+(?:is|are|do|does|did|would)\s+(?:this|it|you|that|prometheus)\b[^.?!]{0,50}?(?:\b(?:as|me|up|with|to|on|about|from)\b)?)"
    r"\s+(?:(?:about|on|for|the|a|an|any|more|my|your)\s+)*)$", re.I)
LIST_AT = 3          # this many listed titles in one ask is a list, not a subject
LONG_ASK = 350       # an ask this long lists things that are not its subject (the base resolver's own line)


def asked(text):
    """The listed titles an ask names as what it wants: ``known`` with
    the ones it pushes away set aside ("not NCIS", "why is this coming
    back as NCIS?")."""
    t = _prep(text)
    out = []
    for h in known(t):
        try:
            if _PUSHED_RX.search(t[max(0, h.start - 90):h.start]):
                continue
        except Exception:
            pass
        out.append(h)
    return out


def is_list(text, hits=None):
    """True when the words are a list or a long brief: three or more
    listed titles, or an ask long enough that the names in it are what is
    measured and compared, not its one subject. A list keeps whatever
    subject the lanes already give it (the group, the page, the brief)."""
    hs = hits if hits is not None else asked(text)
    return len(hs) >= LIST_AT or len(str(text or '')) > LONG_ASK


def points_back(text):
    """True when the words point back at the subject in hand ("these
    viewers", "them", "the same audience"): a title they also name is
    what is measured on it, not a new subject."""
    try:
        return bool(_POINTS_BACK_RX.search(str(text or '')))
    except Exception:
        return False


_STOP_AT = (r"(?:(?=\s+(?:"
            # words that end the name
            r"(?:i\s+(?:only|just|really|still)?\s*(?:need|want)|we\s+(?:only|just)?\s*(?:need|want)|"
            r"for\s+(?:the\s+)?(?:year|month|week|period|window|calendar|same|full|whole|entire|past|last|first)|"
            r"(?:total\s+|unique\s+|monthly\s+|weekly\s+|yearly\s+)?(?:viewership|minutes|viewers|hours|views|streams|numbers|data|"
            r"figures|stats|audience\s+size|demographics|demos|breakdown|read|report)|"
            r"(?:show|series|tv\s+show|movie|film)\s+(?:minutes|viewership|viewers|hours|views|numbers|data)|"
            r"by\s+(?:month|week|year|day|quarter|season|platform|service)|per\s+(?:month|week|year)|"
            r"please|too|as\s+well|also|instead|only|but|not|and\s+(?:then|also|give|show|tell|the)|then|now|again|though|thanks?|"
            r"thank\s+you)\b"
            # a window
            r"|(?:for|in|during|over|across|from|since|through|between|thru)\s+(?:the\s+)?(?:\d|q[1-4]\b|jan|feb|mar|apr|may\b|jun|jul|"
            r"aug|sep|oct|nov|dec|last\b|past\b|this\b|trailing\b|all\s+of\b|calendar\b)"
            # the service it is watched on
            rf"|on\s+(?:the\s+)?(?:{_PLAT_ALT})(?![\w+])"
            r"))|(?=\s*(?:[,;?!\n]|\.(?:\s|$)|$)))")
_NAME = r"(?P<x>[A-Za-z0-9][^,;?!\n]{0,80}?)"
_SLOT_RXS = (
    # the same (thing) for X; same for X; do that for X
    re.compile(r"\b(?:the\s+)?same(?:\s+(?:thing|read|analysis|report|pull|breakdown|numbers?|table|data|info(?:rmation)?|question|"
               r"view|cut|exercise))?\s+(?:but\s+)?(?:for|on|with|about)\s+(?:the\s+(?:show|series|film|movie)\s+)?" + _NAME + _STOP_AT, re.I),
    re.compile(r"\b(?:do|run|pull|repeat|redo|give\s+me|show\s+me)\s+(?:it|that|this|those|these)(?:\s+(?:same\s+)?(?:analysis|read|report|"
               r"thing|pull|breakdown|table|exercise|study|question))?\s+(?:again\s+)?(?:but\s+)?(?:for|on|with)\s+"
               r"(?:the\s+(?:show|series|film|movie)\s+)?" + _NAME + _STOP_AT, re.I),
    # what about X; how about X
    re.compile(r"^\W*(?:(?:and|ok(?:ay)?|now|so|then|great|thanks|cool)[,\s]+)*(?:what|how)\s+about\s+(?:for\s+)?(?:the\s+(?:show|series|film|movie)\s+)?"
               + _NAME + _STOP_AT, re.I),
    # no i need X; no, X; i meant X
    re.compile(r"^\W*(?:no+|nope|nah|not\s+that(?:\s+one)?|not\s+quite|wrong(?:\s+(?:show|title|one|series|movie|film))?|incorrect)\b[\s,.!:-]*"
               r"(?:(?:i|we)\s+(?:need|want|meant|mean|said|asked\s+(?:for|about)|am\s+asking\s+(?:for|about)|was\s+asking\s+(?:for|about))\s+|"
               r"(?:it|that|this)(?:'s|\s+is|\s+was)\s+|i(?:'m|\s+am)\s+(?:asking|talking)\s+about\s+|give\s+me\s+|just\s+|only\s+)?"
               r"(?:(?:for|about|on)\s+)?(?:the\s+(?:show|series|film|movie)\s+)?" + _NAME + _STOP_AT, re.I),
    re.compile(r"\b(?:i|we)\s+(?:meant|mean|said|asked\s+(?:for|about)|was\s+asking\s+(?:for|about)|am\s+asking\s+(?:for|about))\s+"
               r"(?:(?:for|about|on)\s+)?(?:the\s+(?:show|series|film|movie)\s+)?" + _NAME + _STOP_AT, re.I),
    # now do X; next X
    re.compile(r"^\W*(?:now|next|then|also)\s+(?:do|run|pull|give\s+me|show\s+me|try)\s+(?:the\s+(?:show|series|film|movie)\s+)?"
               + _NAME + _STOP_AT, re.I),
)
_PRONOUN = frozenset(('it', 'that', 'this', 'them', 'those', 'these', 'one', 'ones', 'same', 'all', 'everything', 'both', 'each',
                      'me', 'us', 'you', 'what', 'which', 'something', 'anything', 'nothing', 'more', 'less', 'help', 'here',
                      'there', 'else', 'other', 'others', 'another', 'again', 'yes', 'no', 'ok', 'okay', 'thanks'))
_VERB_LEAD = frozenset(('run', 'build', 'do', 'give', 'show', 'pull', 'make', 'get', 'use', 'tell', 'try', 'start', 'send', 'create',
                        'stop', 'just', 'please', 'go', 'let', 'lets', 'keep', 'find', 'look', 'check', 'see', 'answer', 'explain',
                        'add', 'remove', 'change', 'fix', 'redo', 'rerun', 'repeat', 'compare', 'include', 'exclude', 'drop', 'take',
                        'put', 'set', 'say', 'read', 'open', 'close', 'download', 'export', 'email', 'draft', 'write', 'quote',
                        'cancel', 'approve', 'continue', 'proceed', 'wait', 'hold', 'help', 'know', 'understand', 'figure'))
_SENTENCE_WORDS = frozenset(('was', 'is', 'are', 'were', 'has', 'have', 'had', 'did', 'does', 'will', 'would', 'can', 'could', 'should',
                             'where', 'when', 'why', 'how', 'what', 'who', 'asked', 'said', 'meant', 'created', 'because', 'if',
                             'that', 'which', 'you', 'your', 'me', 'my', 'we', 'our', 'i'))
_PERSON_LEAD = frozenset(('i', 'we', 'you', 'he', 'she', 'they', 'their', 'its', 'his', 'her', 'our', 'my', 'your', 'im', "i'm",
                          "i'd", "i've", "we're", "don't", 'dont', 'not', 'never', 'to', 'if', 'when', 'why', 'how', 'where', 'who'))
# the kinds of thing a profile is cut or read by: a follow-up on the subject in hand, never a new subject
_CATEGORY_WORDS = frozenset((
    'competitors', 'competitor', 'competition', 'brands', 'brand', 'retail', 'retailers', 'retailer', 'stores', 'store', 'shopping',
    'qsr', 'restaurants', 'restaurant', 'dining', 'food', 'fast', 'beverage', 'beverages', 'drinks', 'alcohol', 'beer', 'wine',
    'spirits', 'streaming', 'streamers', 'services', 'service', 'social', 'media', 'apps', 'app', 'platforms', 'apparel',
    'footwear', 'fashion', 'beauty', 'wellness', 'health', 'healthcare', 'pharma', 'travel', 'airlines', 'hotels', 'banking',
    'banks', 'finance', 'financial', 'credit', 'cards', 'insurance', 'telecom', 'wireless', 'carriers', 'auto', 'automotive',
    'automobile', 'cars', 'trucks', 'sports', 'teams', 'leagues', 'music', 'artists', 'musicians', 'gaming', 'games', 'podcasts',
    'news', 'networks', 'channels', 'cable', 'broadcast', 'talent', 'actors', 'celebrities', 'creators', 'influencers', 'athletes',
    'tech', 'technology', 'devices', 'electronics', 'toys', 'pets', 'home', 'grocery', 'groceries', 'cpg', 'snacks', 'categories',
    'category', 'genres', 'genre', 'comedy', 'drama', 'horror', 'reality', 'documentaries', 'kids', 'animation', 'anime',
    'movies', 'shows', 'sponsors', 'sponsorship', 'sponsorships', 'partners', 'partnerships', 'advertisers', 'advertising', 'ads',
    'marketing', 'strategy', 'recommendations', 'insights', 'takeaways', 'trends', 'interests', 'hobbies', 'lifestyle', 'politics',
    'political', 'religion', 'education', 'occupation', 'ethnicity', 'race', 'location', 'geography', 'language', 'spanish',
    'english', 'overlap', 'crossover', 'affinity', 'affinities', 'behavior', 'behaviors', 'habits', 'purchases', 'purchase',
    'spending', 'loyalty', 'churn', 'retention', 'growth', 'decline', 'cost', 'costs', 'price', 'pricing', 'budget', 'deck',
    'slides', 'email', 'detail', 'details', 'context', 'examples', 'example', 'options', 'option', 'ideas', 'idea', 'luxury',
    'premium', 'online', 'digital', 'linear', 'theatrical', 'box', 'office', 'ticket', 'tickets', 'merch', 'merchandise'))
_CITIES = frozenset((
    'new york', 'new york city', 'nyc', 'los angeles', 'la', 'chicago', 'philadelphia', 'philly', 'dallas', 'fort worth', 'houston',
    'atlanta', 'boston', 'san francisco', 'bay area', 'phoenix', 'seattle', 'detroit', 'miami', 'denver', 'minneapolis', 'tampa',
    'orlando', 'cleveland', 'sacramento', 'portland', 'charlotte', 'st louis', 'st. louis', 'pittsburgh', 'raleigh', 'baltimore',
    'nashville', 'san diego', 'salt lake city', 'san antonio', 'kansas city', 'columbus', 'milwaukee', 'cincinnati', 'las vegas',
    'vegas', 'austin', 'indianapolis', 'new orleans', 'washington', 'washington dc', 'dc', 'san jose', 'oklahoma city', 'memphis',
    'louisville', 'richmond', 'buffalo', 'hartford', 'jacksonville', 'honolulu', 'albuquerque', 'tucson', 'omaha', 'el paso'))
_WINDOW_WORDS = frozenset((
    'year', 'years', 'month', 'months', 'week', 'weeks', 'day', 'days', 'quarter', 'quarters', 'ytd', 'q1', 'q2', 'q3', 'q4',
    'last', 'past', 'this', 'next', 'trailing', 'calendar', 'full', 'whole', 'entire', 'window', 'period', 'range', 'date', 'dates',
    'to', 'through', 'thru', 'until', 'from', 'since', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december', 'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov',
    'dec', 'today', 'yesterday', 'now', 'recent', 'latest', 'earlier', 'later', 'premiere', 'launch', 'the', 'of', 'and', 'a',
    'for', 'in', 'time', 'frame', 'timeframe'))
_CUT_WORDS = frozenset((
    'women', 'woman', 'men', 'man', 'female', 'females', 'male', 'males', 'adults', 'adult', 'kids', 'kid', 'children', 'teens',
    'teen', 'parents', 'parent', 'moms', 'mom', 'dads', 'dad', 'mothers', 'fathers', 'avid', 'casual', 'heavy', 'light', 'core',
    'fans', 'fan', 'viewers', 'viewer', 'subscribers', 'subscriber', 'users', 'user', 'gen', 'z', 'x', 'millennials',
    'millennial', 'boomers', 'boomer', 'zoomers', 'hispanic', 'latino', 'latina', 'latinos', 'black', 'white', 'asian', 'aapi',
    'african', 'american', 'americans', 'age', 'ages', 'aged', 'under', 'over', 'plus', 'older', 'younger', 'young', 'old',
    'income', 'households', 'household', 'high', 'low', 'middle', 'affluent', 'urban', 'rural', 'suburban', 'national',
    'nationally', 'regional', 'region', 'regions', 'state', 'states', 'dma', 'dmas', 'market', 'markets', 'city', 'cities',
    'us', 'usa', 'uk', 'canada', 'international', 'global', 'domestic', 'new', 'returning', 'lapsed', 'churned', 'everyone',
    'everybody', 'people', 'total', 'universe', 'general', 'population', 'pop', 'the', 'and', 'or', 'only', 'just', 'of', 'in',
    'with', 'without', 'who', 'are', 'is', 'to', 'cut', 'cohort', 'segment', 'group', 'audience', 'east', 'west', 'north',
    'south', 'coast', 'midwest', 'northeast', 'southeast', 'southwest', 'northwest', 'lgbtq', 'single', 'married', 'college',
    'graduates', 'students', 'homeowners', 'renters', 'sign', 'ups', 'signups', 'buyers', 'shoppers', 'customers', 'members'))
_METRIC_EXTRA = frozenset((
    'watched', 'watch', 'viewed', 'view', 'viewing', 'streamed', 'stream', 'streaming', 'played', 'plays', 'episodes', 'episode',
    'season', 'seasons', 'ranking', 'rank', 'ranked', 'top', 'list', 'chart', 'graph', 'export', 'csv', 'file', 'deck', 'pdf',
    'summary', 'overview', 'details', 'detail', 'information', 'info', 'report', 'read', 'analysis', 'answer', 'question',
    'version', 'format', 'way', 'thing', 'things', 'stuff', 'time', 'times', 'show', 'shows', 'series', 'movie', 'movies', 'film',
    'films', 'title', 'titles', 'content', 'how', 'many', 'much', 'long', 'did', 'does', 'do', 'was', 'were', 'be', 'been', 'on',
    'at', 'it', 'that', 'this', 'please', 'again', 'too', 'also', 'instead', 'but', 'i', 'we', 'you', 'need', 'want', 'like',
    'get', 'see', 'know', 'have', 'has', 'had', 'give', 'me', 'my', 'our', 'your', 'right', 'wrong', 'correct', 'different',
    'same', 'first', 'second', 'third', 'one', 'two', 'three', 'both', 'neither', 'either', 'still', 'really', 'actually'))


def _states():
    try:
        from .cohort_scope import US_STATES
        return set(US_STATES)
    except Exception:
        return set()


def _not_a_title(x):
    """True when a slot's words are a pronoun, a window, a cut, a metric,
    a place or a service: the carried subject stays."""
    low = ' '.join(str(x or '').lower().split()).strip(" .,;:'\"")
    if not low:
        return True
    if _is_platform(low) or re.sub(r"^the\s+", '', low) in _PLAT_NAMES:
        return True
    if low in _states() or low in _CITIES or re.sub(r"^the\s+", '', low) in _CITIES:
        return True
    words = [w for w in re.split(r"[^a-z0-9+']+", low) if w]
    if not words or len(words) > 7:
        return True
    if words[0] in _PRONOUN and (len(words) == 1 or words[1] in _WINDOW_WORDS | _CUT_WORDS | _METRIC_EXTRA | {'for', 'on', 'in', 'by'}):
        return True
    if words[0] in _PERSON_LEAD and (not str(x or '').strip()[:1].isupper() or words[0] in ('i', 'we', "i'm", 'im')):
        return True
    if re.search(r"\b(?:don'?t|do not|didn'?t|did not|can'?t|cannot|won'?t|isn'?t|wasn'?t|aren'?t|doesn'?t)\b", low):
        return True
    raw = ' '.join(str(x or '').split())
    titled = bool(raw) and all(w[:1].isupper() or w[:1].isdigit() or w.lower() in ('of', 'the', 'and', 'a', 'in', 'to', 'for', 'is', 'on')
                               for w in raw.split()) and raw.split()[0][:1].isupper()
    if not titled:
        # an order or a sentence is not a name ("run what I asked", "build fresh", "a pdf was created")
        if words[0] in _VERB_LEAD or any(w in _SENTENCE_WORDS for w in words):
            return True
    if re.fullmatch(r"(?:(?:ages?|aged)\s+)?\d{1,2}\s*(?:-|to|\+)\s*\d{0,2}\+?|\d{1,2}\+|(?:20|19)\d{2}|q[1-4](?:\s+(?:20)?\d{2})?", low):
        return True
    try:
        from . import subject_rules as _sr
        metric = set(_sr._METRIC_WORDS)
    except Exception:
        metric = set()
    plain = _WINDOW_WORDS | _CUT_WORDS | _METRIC_EXTRA | metric | _PRONOUN | _PLAT_WORDS | _CATEGORY_WORDS
    rest = [w for w in words if w not in plain and not w.isdigit() and not re.fullmatch(r"\d+(?:st|nd|rd|th|s|\+)?|\d{1,2}/\d{1,2}(?:/\d{2,4})?", w)]
    return not rest


def _plausible(name):
    """A name a reader would recognise as one: not a run of ordinary
    words. A name typed with capitals is taken at its word."""
    s = ' '.join(str(name or '').split())
    if not s:
        return False
    if re.search(r"[A-Z]", s[1:]) or (s[:1].isupper() and len(s.split()) >= 2) or re.search(r"\d", s):
        return True
    try:
        from . import referents as _refs
        return bool(_refs.plausible_subject(s))
    except Exception:
        return len(s) >= 4


def _cased(name):
    s = ' '.join(str(name or '').split()).strip(" .,;:'\"")
    if not s:
        return ''
    if s.isupper() and len(s) > 5 or s.islower():
        try:
            from . import subject_rules as _sr
            return _sr._cased(s.lower()) or s.title()
        except Exception:
            return s.title()
    return s


def _typed_as_a_name(x, text):
    """True when the reader's own typing marks X as a name: a capital in
    it (when the message is not all capitals), or a kind word beside it
    ("the show X", "X series")."""
    xs = ' '.join(str(x or '').split())
    t = str(text or '')
    letters = [c for c in t if c.isalpha()]
    shouting = bool(letters) and sum(c.isupper() for c in letters) > 0.7 * len(letters)
    if not shouting and re.search(r"[A-Z]", xs):
        return True
    try:
        rx = re.escape(xs)
        if re.search(r"\b(?:the\s+)?(?:tv\s+)?(?:show|series|film|movie|title|sitcom|drama)\s+(?:called\s+|named\s+)?[\"'\u201c]?" + rx, t, re.I) \
                or re.search(rx + r"[\"'\u201d]?\s+(?:the\s+)?(?:tv\s+)?(?:show|series|film|movie|sitcom)\b", t, re.I):
            return True
    except Exception:
        pass
    return False


def _thread_spelling(x, history):
    """How the thread itself wrote X when it named it with a capital (a
    chip, a reply, an earlier question): the evidence that X is a name
    here, and the spelling to show. '' when the thread never did."""
    fx = fold(x)
    if not fx or len(fx) < 3:
        return ''
    try:
        words = [re.escape(w) for w in str(x).split()]
        rx = re.compile(r"(?<![A-Za-z0-9])" + r"[\s.:'\u2019-]*".join(r"[.'\u2019]?".join(re.escape(c) for c in w) for w in str(x).split()) + r"\.?(?![A-Za-z0-9])", re.I)
        for t in reversed([h for h in (history or []) if isinstance(h, dict)][-24:]):
            blob = str(t.get('text') or t.get('content') or '')
            meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
            for o in (meta.get('options') or []):
                blob += '\n' + str((o or {}).get('label') if isinstance(o, dict) else o)
            for m in rx.finditer(blob):
                got = m.group(0).strip()
                if re.search(r"[A-Z]", got) and not got.isupper():
                    return got
    except Exception:
        return ''
    return ''


def slot_title(text, history=None, strict=True):
    """The name in "the same for X", "what about X", "no i need X", "now
    do X": the listed title when X is one, else X as a reader would write
    it, else '' (X is a window, a cut, a metric, a kind of thing, a
    service, a place, a pronoun or ordinary words).

    ``strict`` (the binder, the base of a read): a name that is not a
    listed title counts only when the reader typed it as a name (a
    capital, "the show X") or the thread already named it. Where the
    shape itself says a subject is being swapped (a re-run "for X", a
    correction "no i need X") the caller passes ``strict=False``."""
    t = ' '.join(str(text or '').split())
    if not t or not enabled():
        return ''
    try:
        for rx in _SLOT_RXS:
            m = rx.search(t)
            if not m:
                continue
            tail = t[m.start('x'):]
            hits = known(tail)
            if hits and hits[0].start <= 12:
                return hits[0].display
            x = m.group('x').strip(" .,;:'\"")
            if not re.search(r"[a-z]", x) or not re.search(r"\s[A-Z][a-z]+\s*$", x):
                x = _SLOT_TYPE_TAIL_RX.sub('', x)      # "unOrdinary the WEBTOON", "chicago pd show"; "Squid Game" keeps its Game
            x = _TYPE_TAIL_RX.sub('', _TYPE_LEAD_RX.sub('', x)).strip(" .,;:'\"")
            x = re.sub(r"^(?:a|an)\s+(?=[a-z])", '', x)
            x = _AUD_TAIL_RX.sub('', ' ' + x).strip() or x
            x = re.sub(r"^(?:avid|casual|heavy|light|core)\s+", '', x, flags=re.I)
            if _not_a_title(x) or not _plausible(re.sub(r"^the\s+", '', x, flags=re.I)):
                continue
            seen = _thread_spelling(x, history)
            if strict and not seen and not _typed_as_a_name(x, t):
                continue
            out = canonical(seen or _cased(x))
            return out[:1].upper() + out[1:] if out[:4].lower() == 'the ' else out
    except Exception:
        return ''
    return ''


def _names_label(text, label):
    """True when the words carry the label's own title, whole, with no
    subtitle after it."""
    c = core(label)
    fc = fold(c)
    if not fc or len(fc) < 3:
        return False
    # a listed title of another name is taken out first: "ncis sydney" does not name NCIS
    lk = title_key(label)
    t = _prep(text)
    for h in sorted(known(t), key=lambda x: -x.start):
        if h.key != lk:
            t = t[:h.start] + ' | ' + t[h.end:]
    text = t
    ft = ' ' + ' # '.join(fold(x) for x in text.split('|')) + ' '
    if f' {fc} ' not in ft:
        bare = re.sub(r"^(?:the|a|an) ", '', fc)
        if bare == fc or f' {bare} ' not in ft:
            return False
    return not _subtitle_after(text, c)


def _service_is_where(text, held):
    """True when ``held`` is a service's own record and the words name it
    only as where something is watched ("on Amazon Prime", "the Hulu
    platform"), not as whose audience is asked about ("Prime Video
    viewers", "who uses Hulu"): the service is the scope of the title the
    words name, never its stand-in (prometheus.title_on_service)."""
    c = core(held)
    if not _is_platform(c):
        return False
    try:
        from . import platform_scope as _ps
        canon = _ps.canon(c) or _ps.canon(re.sub(r"^the\s+", '', c, flags=re.I))
        roles = _ps.named(text) or {}
        if canon:
            return roles.get(canon, '') != 'subject'
        return not re.search(re.escape(c) + r"(?:'s)?\s+" + _AUD + r"\b", str(text or ''), re.I)
    except Exception:
        return False


def _binder_name(text, reading=None):
    """The title the binder's own patterns read in the words (a quoted
    title, "the film X", "people who watched X on Y", "X viewers"), or
    ''. The thread and the page are never asked."""
    try:
        from . import subject_rules as _sr
        s = _sr._bind_words(text, None, '', reading if isinstance(reading, dict) else {})
        if s is not None and s.source == 'words' and s.name and s.kind in ('title', 'brand', 'person', 'audience') and not s.pointed:
            return str(s.name)
    except Exception:
        pass
    return ''


def other_than(text, held, *, slot=True, binder=True, reading=None, strict=True, history=None):
    """The title the words name when it is not the one ``held`` is about;
    '' when they name the held title itself, point back at it, or name no
    title at all. ``held`` is whatever is being carried: the last read's
    subject, the thread's, the open page, a file, a stored figure's."""
    t = ' '.join(str(text or '').split())
    h = ' '.join(str(held or '').split())
    if not t or not h or not enabled():
        return ''
    try:
        hits = asked(t)
        if is_list(t, hits):
            return ''                           # a list or a long brief: the names in it are not its one subject
        hk = title_key(h)
        where = _service_is_where(t, h)
        if hits:
            if any(x.key == hk for x in hits) or (_names_label(t, h) and not where):
                return ''                       # the held title is named too: a comparison, an overlap, a list
            if points_back(t):
                return ''                       # "do they also watch Chicago P.D.": measured on the subject in hand
            return hits[0].display
        ext = extension(t, h)
        if ext:
            return ext
        if (_names_label(t, h) and not where) or points_back(t):
            return ''
        if slot:
            x = slot_title(t, history, strict=strict)
            if x and not same_title(x, h) and not _within(x, h):
                return x
        if binder:
            b = _binder_name(t, reading)
            if b and not same_title(b, h) and not _is_platform(b) and not _within(b, h) and not _not_a_title(b):
                return canonical(b)
    except Exception:
        return ''
    return ''


def _within(name, held):
    """True when a name's words all sit inside the held label ("Special
    Victims Unit" inside "Law & Order: Special Victims Unit on Amazon
    Prime Video"; "Prime Video" inside the same): no other title."""
    fn, fh = fold(name), ' ' + fold(held) + ' '
    if not fn:
        return True
    if f' {fn} ' in fh:
        return not siblings(name, held)
    return False


def mate_named(text, label):
    """The franchise mate the words name when they name one and not
    ``label`` itself ("FBI: Most Wanted viewers" with FBI in hand, "ncis
    sydney" with NCIS): the label's name inside a longer title is not the
    label. '' otherwise."""
    if not enabled():
        return ''
    try:
        o = other_than(text, label, slot=False, binder=False)
        return o if o and siblings(o, label) else ''
    except Exception:
        return ''


def sole(text):
    """The one listed title the words name, when they name exactly one
    and do not point back at the subject in hand; '' otherwise."""
    try:
        hits = asked(text)
        return hits[0].display if len(hits) == 1 and not points_back(text) and len(str(text or '')) <= LONG_ASK else ''
    except Exception:
        return ''


def named(text, reading=None):
    """The titles the words name, in order: the listed ones first, then
    the name in a "same for X" slot. [] when they name none."""
    out = [h.display for h in asked(text)]
    x = slot_title(text)
    if x and all(not same_title(x, o) for o in out):
        out.append(x)
    return out


# ----------------------------------------------------------------- a landed read's subject
_HEAD_LINE_RX = re.compile(r"^\s*On\s+(?P<who>[^\n]{2,160}?):\s*$")
_HEAD_FLAT_RX = re.compile(r"^\s*On\s+(?P<a>[^:\n(]{1,120}?):\s+(?P<b>[^:\n]{2,90}?):\s")


def read_head(text, meta_subject=''):
    """The subject a landed read opens on ("On <subject>:"), colon and
    all. The header is its own first line; a title that carries a colon
    ("Law & Order: Special Victims Unit on Amazon Prime Video", "NCIS:
    Sydney") is read whole, where a cut at the first colon carried the
    franchise in its place. '' when the reply does not open that way."""
    raw = str(text or '')
    try:
        first = raw.strip().split('\n', 1)[0]
        m = _HEAD_LINE_RX.match(first) if '\n' in raw.strip() else None
        who = m.group('who').strip() if m else ''
        if not who:
            flat = ' '.join(raw.split())
            m2 = _HEAD_FLAT_RX.match(flat)
            if m2:
                a, b = m2.group('a').strip(), m2.group('b').strip()
                whole = f"{a}: {b}"
                ms = ' '.join(str(meta_subject or '').split())
                if (ms and fold(ms).startswith(fold(whole)[:max(8, len(fold(a)) + 2)]) and fold(a) != fold(ms)) \
                        or title_key(whole) in _TITLES or _looks_like_subtitle(b):
                    who = whole
        if who.lower() in ('it', 'this', 'that') or who.lower().startswith('it:'):
            return ''
        return who
    except Exception:
        return ''


def _looks_like_subtitle(b):
    words = str(b or '').split()
    if not words or len(words) > 9:
        return False
    body = re.split(r"\s+(?:on|in|at)\s+", str(b), 1)[0].split()
    small = {'of', 'the', 'and', 'a', 'in', 'my', 'to', 'for', 'vs', '&'}
    return bool(body) and all(w[:1].isupper() or w[:1].isdigit() or w.lower() in small for w in body) and body[0][:1].isupper() \
        and body[0].lower() not in ('the', 'a', 'on', 'in', 'this', 'that', 'these', 'people', 'viewers', 'total', 'here')


# ----------------------------------------------------------------- the binder
def bound_words(text, history=None, page='', current=None, reading=None):
    """The binder's last look at the words (prometheus.subject_rules.bind).
    Returns (name, source) to replace what it bound, or None to leave it:

      * a listed title it bound under another spelling, or cut short at a
        dot ("Chicago P"), comes back under its one name;
      * a subject it took from the thread or the page, or found none,
        while the words name a title of their own, is that title."""
    if not enabled():
        return None
    try:
        t = ' '.join(str(text or '').split())
        if not t:
            return None
        src = str(getattr(current, 'source', '') or '')
        name = str(getattr(current, 'name', '') or '')
        if src == 'words' and name:
            if str(getattr(current, 'kind', '')) not in ('title', 'audience', 'brand', 'person'):
                return None
            if _is_platform(core(name)) and not points_back(t):
                # the service is where; the listed title the words name is what (prometheus.title_on_service)
                try:
                    from . import platform_scope as _ps
                    role = (_ps.named(t) or {}).get(_ps.canon(core(name)) or '', '')
                except Exception:
                    role = ''
                hs = asked(t)
                if hs and not is_list(t, hs) and role in ('scope', 'mention') and not re.search(
                        re.escape(core(name)) + r"(?:'s)?\s+" + _AUD + r"\b", t, re.I):
                    return hs[0].display, 'words'
                return None
            for h in asked(t):
                raw_f, name_f = fold(h.raw), fold(name)
                if not name_f:
                    continue
                same_words = _plain(name) == _plain(h.display) and name != h.display       # its capitals or its dots
                cut_short = len(name_f) >= 5 and raw_f.startswith(name_f) and 0 < len(raw_f) - len(name_f) <= 3     # "Chicago P"
                piece = name_f != raw_f and f' {name_f} ' in f' {raw_f} ' and title_key(name) not in _TITLES  # "Blood"
                if same_words or cut_short or piece:
                    return h.display, 'words'
            return None             # a title keeps the name it was typed under ("FBI Most Wanted")
        if getattr(current, 'pointed', False) or points_back(t):
            return None
        hits = asked(t)
        if is_list(t, hits):
            return None
        if hits:
            carried = name if src in ('thread', 'page') else ''
            if carried and (any(h.key == title_key(carried) for h in hits) or _names_label(t, carried)):
                return None
            if page and (any(h.key == title_key(page) for h in hits) or _names_label(t, page)):
                return None
            return hits[0].display, 'words'
        if src in ('thread', 'page') and name:
            ext = extension(t, name)
            if ext:
                return ext, 'words'
        x = slot_title(t, history)
        if x and not (src in ('thread', 'page') and name and (same_title(x, name) or _within(x, name))) \
                and not (page and (same_title(x, page) or _within(x, page))):
            return x, 'words'
    except Exception:
        return None
    return None


# ----------------------------------------------------------------- the continuation
def stand_down(out, text, history=None, page='', understanding_title=None):
    """A continuation that carries the last deliverable's subject (a
    re-run on another window, a different cut of the last read, a
    correction) stands down when the words name another title:

      * a re-run or a different cut becomes ``named_other``: the same
        question, asked on the title the words name;
      * a correction keeps its kind and continues on the named title.

    ``out`` is prometheus.continuation.classify's result; it is returned
    changed in place, or untouched."""
    if not enabled() or not isinstance(out, dict):
        return out
    try:
        kind = str(out.get('kind') or '')
        if kind not in ('rerun_window', 'parameter_change', 'correction'):
            return out
        deliv = out.get('deliverable') if isinstance(out.get('deliverable'), dict) else {}
        carried = str(out.get('subject') or deliv.get('subject') or '').strip()
        t = ' '.join(str(text or '').split())
        # the Understanding's own reading of the title the words name (it compared the words against the thread's
        # carried subject and the page); when the named_title flip is live, a title it read wins and a title it did
        # not read is still the gate's own call (2026-11-20, night)
        u_title = ' '.join(str(understanding_title or '').split())
        if u_title and carried and title_key(u_title) != title_key(carried):
            params = dict(out.get('params') or {})
            if kind == 'correction':
                params.update(named_title=u_title, carried=carried, by='understanding')
                out.update(subject=u_title, params=params)
            else:
                params.update(named_title=u_title, carried=carried, was=kind, by='understanding')
                out.update(kind='named_other', subject=u_title, params=params)
            return out
        if kind == 'correction':
            try:
                from . import subject_rules as _sr
                t_ask = _sr.strip_lead_negation(t)
            except Exception:
                t_ask = t
            title = other_than(t, carried, strict=False, history=history) if carried else ''
            if not title:
                ks = [] if is_list(t_ask) else asked(t_ask)
                title = (ks[0].display if ks and not points_back(t) and not (carried and any(k.key == title_key(carried) for k in ks)) else '') \
                    or ('' if carried else slot_title(t, history, strict=False))
            if not title:
                return out
            params = dict(out.get('params') or {})
            params['named_title'] = title
            params['carried'] = carried
            out.update(subject=title, params=params)
            return out
        title = other_than(t, carried, strict=False, history=history) if carried else ''
        if not title:
            return out
        params = dict(out.get('params') or {})
        params.update(named_title=title, carried=carried, was=kind)
        out.update(kind='named_other', subject=title, params=params)
    except Exception:
        return out
    return out


# ----------------------------------------------------------------- the base of a read
def _name_tokens(s):
    return [w for w in fold(s).split() if w]


def base_fits(base, subject_hint, text, *, bound=False):
    """May this file be the base of this question? (ok, why). A file that
    is not the page:

      * is never the base when the words name a title and the file is
        about another one (a franchise mate, or any other title);
      * is never taken on its service's words alone: "<Title> on
        <Service>" needs a word of its title in the question.

    The open page is held to the first rule only (a question that points
    at the screen names nothing and still uses it)."""
    if not enabled() or not isinstance(base, dict):
        return True, ''
    try:
        src = str(base.get('source') or '')
        subj = str(base.get('subject') or '').strip()
        key = str(base.get('s3_key') or '')
        if src not in ('catalog', 'page', 'subscriber_iq') or not subj:
            return True, ''
        words = f"{subject_hint or ''} {text or ''}".strip()
        # the reader's own words first: a hint that repeats the file's name (the page, a guess) is not the
        # reader naming it
        other = other_than(text, subj, slot=not bound, binder=False) or other_than(words, subj, slot=not bound, binder=False)
        if other:
            return False, f"the words name {other!r}; {subj!r} is another title"
        if src == 'catalog' and not bound and not key.startswith('subiq:'):
            c = core(subj)
            title_toks = [w for w in _name_tokens(c) if w not in _PLAT_WORDS and w not in _GENERIC_LEFT]
            all_toks = [w for w in _name_tokens(subj.split(' - ')[0]) if w not in _GENERIC_LEFT and w not in ('viewers', 'fans', 'audience')]
            plat_toks = [w for w in all_toks if w in _PLAT_WORDS]
            if title_toks and plat_toks and not _is_platform(c):
                asked = set(_name_tokens(words))
                try:
                    from . import base_pair as _bp
                    asked |= set(_bp.alias_tokens(words))
                except Exception:
                    pass
                if not (set(title_toks) & asked):
                    return False, f"{subj!r} was matched on its service's words alone"
    except Exception:
        return True, ''
    return True, ''


def held(fn):
    """Wrap the base resolver (legacy/chat.py ``_pm_generation_base``): a
    file that does not fit the title the words name is no base. The
    resolver is asked once more for the named title on its own (its own
    file, or its own tracker); with nothing held for it the answer is no
    base, and the lane goes on as it does for any title that is not on
    the dashboard yet."""
    def _resolve(subject_hint, text, ctx=None, prefer_catalog=False, bound=False):
        base = fn(subject_hint, text, ctx=ctx, prefer_catalog=prefer_catalog, bound=bound)
        try:
            ok, why = base_fits(base, subject_hint, text, bound=bound)
            if ok:
                return base
            print(f"[pm-named-title] base {str((base or {}).get('subject'))!r} dropped: {why}")
            title = other_than(f"{subject_hint or ''} {text or ''}", (base or {}).get('subject'), slot=not bound, binder=False)
            if title:
                own = None
                try:
                    own = fn(title, title, ctx=None, prefer_catalog=True, bound=True)
                except Exception:
                    own = None
                if isinstance(own, dict) and (own.get('s3_key') or own.get('subject')) and same_title(title, own.get('subject')):
                    print(f"[pm-named-title] the named title's own base: {str(own.get('subject'))!r}")
                    return own
                _note_left_out(title, base)
            return None
        except Exception:
            return base
    _resolve.__name__ = getattr(fn, '__name__', '_pm_generation_base')
    _resolve.__doc__ = getattr(fn, '__doc__', None)
    _resolve.__wrapped__ = fn
    return _resolve


def _note_left_out(title, base):
    """Remember, for this request, that a franchise mate of the named
    title was on the shelf and was left out; the reply says so in one
    plain sentence (``say_left_out``)."""
    try:
        subj = str((base or {}).get('subject') or '')
        if not title or not siblings(title, subj):
            return
        from flask import g as _g
        _g._pm_named_title_note = {'title': str(title), 'sibling': canonical(core(subj))}
    except Exception:
        pass


def say_left_out(raw):
    """One plain sentence ahead of a quote or a started read when the
    title the reader named has nothing on the dashboard and a franchise
    mate of it was left out. Changes ``raw`` in place; never touches a
    landed read, and says it once."""
    if not isinstance(raw, dict) or not enabled():
        return raw
    try:
        from flask import g as _g
        note = getattr(_g, '_pm_named_title_note', None)
        if not isinstance(note, dict) or not note.get('title') or not note.get('sibling'):
            return raw
        reply = raw.get('reply')
        if not isinstance(reply, str) or not reply.strip():
            return raw
        if not (raw.get('panel_offer') or reply.startswith('On it') or reply.startswith('I can ')):
            return raw
        nk = title_key(note['title'])
        if 'is a different title' in reply or not (note['title'] in reply or any(h.key == nk for h in known(reply))):
            return raw
        ask = str(getattr(_g, '_pm_ask_text', '') or '')
        said = [h.display for h in asked(ask)] + [slot_title(ask, strict=False)]
        if not any(x and same_title(x, note['title']) for x in said) and not extension(ask, note['sibling']):
            return raw                          # carried from an earlier turn: nothing new to say
        raw['reply'] = NOT_HELD.format(title=note['title'], sibling=note['sibling']) + ' ' + reply
        raw['named_title_left_out'] = dict(note)
        _g._pm_named_title_note = None
    except Exception:
        return raw
    return raw


_DOUBLE_STOP_RX = re.compile(r"(?<=[A-Z]\.[A-Z]\.)\.(?=\s|$)")


def tidy(text):
    """A title that ends in a dot ends its sentence once ("... that read
    on Chicago P.D.. Pulling" -> "... on Chicago P.D. Pulling")."""
    try:
        return _DOUBLE_STOP_RX.sub('', str(text)) if isinstance(text, str) and '..' in text else text
    except Exception:
        return text


# ----------------------------------------------------------------- stored figures and answers
def bucket_fits(question, bucket_subject, explicit_subject=''):
    """May a stored subject's figures and answers be used for this
    question? False when the words (the subject asked for, then the
    question) name a title and the stored subject is another one."""
    if not enabled():
        return True
    try:
        b = str(bucket_subject or '').strip()
        if not b:
            return True
        if explicit_subject:
            es = str(explicit_subject)
            if same_title(es, b):
                return True
            if (siblings(es, b) or extension(es, b)) and not _names_label(str(question or ''), b):
                return False                    # a franchise mate asked for by name: never the other one's record
        # a record the words name too (the title a journey ends on, the other side of a comparison) still rides
        return not other_than(f"{explicit_subject or ''} {question or ''}".strip(), b, slot=False, binder=False)
    except Exception:
        return True


def rebound(bind_subject, text, panel_confirm=None):
    """The subject a lane bound for a read (the entity a name collapses
    to, the one library subject a reading carried, the thread's pin) is
    the title the words name when they name one: a franchise mate, the
    service it is watched on or a longer title is never run in its place
    ("Power Book II: Ghost minutes" was bound to the Power file, "ncis
    sydney viewers" to NCIS). A subject the reader chose (a chip, an
    approved quote) is left as it is."""
    b = str(bind_subject or '').strip()
    if not b or not enabled() or isinstance(panel_confirm, dict):
        return b
    try:
        try:
            from flask import g as _g
            if str(getattr(_g, '_pm_referent_confirmed', '') or '').strip() == b:
                return b                        # the reader's own tap, or the gate's rewrite
        except Exception:
            pass
        other = other_than(text, b, slot=False, binder=False)
        if other:
            print(f"[pm-named-title] bound {b!r} is not the title the words name; the read is on {other!r}")
            return other
    except Exception:
        return b
    return b


def relabel(label, text):
    """A label read out of the words comes back under the listed title's
    one name when it is that title's name, a piece of it or another
    spelling ("Blood viewers" out of "blood of my blood viewers" is
    Outlander: Blood of My Blood viewers). Any other label is returned as
    it came."""
    lab = ' '.join(str(label or '').split())
    if not lab or not enabled():
        return str(label or '')
    try:
        c = core(lab)
        cf = fold(c)
        if not cf:
            return lab
        ck = title_key(c)
        for h in asked(text):
            raw_f = fold(h.raw)
            same_words = _plain(c) == _plain(h.display)
            cut_short = len(cf) >= 5 and raw_f.startswith(cf) and 0 < len(raw_f) - len(cf) <= 3
            piece = cf != raw_f and f' {cf} ' in f' {raw_f} ' and ck not in _TITLES
            if same_words or cut_short or piece:
                if c == h.display:
                    return lab
                i = lab.lower().find(c.lower())
                return (lab[:i] + h.display + lab[i + len(c):]) if i >= 0 else lab
    except Exception:
        return lab
    return lab


def spelling_key(label):
    """One key for a label however its marks are typed (see ``_spelling_key``)."""
    return _spelling_key(str(label or ''))


@lru_cache(maxsize=4096)
def _spelling_key(label):
    """One key for a label however its marks are typed, the service it
    was watched on set aside: "Chicago P.D. viewers", "chicago pd viewers
    on Amazon Prime Video" and "Chicago PD Viewers" are one subject. The
    audience noun and the cut stay in the key."""
    try:
        s = ' '.join(str(label or '').split())
        s = _PLAT_TAIL_RX.sub('', s) or s
        k = _EXACT.get(fold(s))
        return k or fold(s)
    except Exception:
        return ''
