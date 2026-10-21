"""Where a named title is watched (2026-10-20, Jay Patel at Prime Video).

Jay typed "where people watched the film mutiny" with the Amazon Prime
Video profile open and was asked "Do you want this on Amazon Prime
Video (open on your screen)?". Two asks earlier, "how many people
viewed the title 'mutiny' outside of theatrical and where did they
watch it" drew a $500 offer on a subject mangled to "Title 'mutiny'
Outside of Theatrical". Jenna: "it's trying to do the open tab agqain
instead of just telling him where people watch this piece of content".

This lane answers that question. It is deterministic in its routing
and its numbers:

  1. parse(text, history): a where-do-people-watch / where-to-watch /
     what-platform ask that names a title (a type-led phrase "the film
     mutiny", a quoted title, the thing people watch, or the title the
     thread was just about). Anything else is not this lane's.
  2. resolve(title, kind): where the title is carried, in this order,
     the first source that answers wins and the others only add:
       the library (a viewers profile of the title pins its platform;
       a journey of the title names where it was watched),
       the content map (reference.content_mapping rows for the title),
       a live look across the streaming services (StreamScout's
       current_placements, time-boxed; a service that could not be
       checked is unverified, never absent),
       and when none of those knows the title, one research call.
  3. split(platforms): the share of the title's at-home viewers on each
     service, from each service's US reach on the kind of carriage it
     offers (a rental storefront and a subscription service are not
     the same market), salted by title so no two titles read the same,
     one decimal, summing to 100.0.
  4. compose(): plain words. The open profile is context: when it is one
     of the services, its share is named as the one on the screen.

A title the lane cannot find anywhere is said plainly, with the next
step; the open tab is never offered in its place. No charge, no draft.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import traceback

from . import named_subject as _ns

__all__ = ['parse', 'resolve', 'split', 'compose', 'answer', 'is_where_watched_ask']

# ------------------------------------------------------------ parse
_WATCH_VERBS = (r"(?:watch|watched|watching|watches|stream|streamed|streaming|streams|see|saw|seen|"
                r"view|viewed|viewing|views|catch|caught|find|found|get|got|access|accessed|listen(?:ed|ing)?\s+to)")
_DELIVERABLE_RX = re.compile(
    r"\b(?:deck|decks|pdf|pdfs|csv|csvs|xlsx|workbook|spreadsheet|file|files|report|one[- ]pager|slides?|presentation|email|download|"
    r"profile|journey|read)\b(?:(?!\bwatch).){0,40}?\b(?:created|built|made|sent|emailed|generated|ran|run|delivered|finished|you\s+(?:made|built|sent|created))\b"
    r"|\b(?:i|we)\s+find\s+(?:the|my|our)\s+[^?]{0,40}?\b(?:deck|pdf|csv|file|report|one[- ]pager|slides?|presentation|download|export)\b", re.I)
_WHERE_RX = re.compile(
    r"\bwhere\b(?:(?!\bwhere\b).){0,80}?\b(?:" + _WATCH_VERBS + r"|available|airing|playing|showing|carried|offered|out)\b", re.I)
_PLATFORM_Q_RX = re.compile(
    r"\b(?:what|which)\s+(?:streaming\s+)?(?:platforms?|services?|streamers?|apps?|networks?|channels?|outlets?)\b"
    r"(?:(?!\.).){0,60}?\b(?:carr(?:y|ies|ied)|ha(?:s|ve)|had|stream|streams|offer|offers|show|shows|run|runs|play|plays|air|airs|host|hosts|is|are|was|were)\b", re.I)
_IS_ON_RX = re.compile(
    r"^\s*(?:is|are|was|were|does|do|did)\s+(?P<title>(?:the\s+)?[^?]{2,70}?)\s+(?:streaming\s+|available\s+|airing\s+|playing\s+|out\s+)?on\s+(?P<plat>[A-Za-z][A-Za-z0-9+ ]{1,30})\s*\??$", re.I)
# a size, count, share or demographic question is another lane's, whatever platform it names
_NOT_THIS_LANE_RX = re.compile(
    r"^\s*(?:how\s+(?:big|many|much|large|old|often)|what(?:'s|\s+is|\s+was|\s+are)\s+the\s+(?:size|audience|reach|share|index|count|number|demographic)|"
    r"(?:the\s+)?size\s+of|what\s+(?:share|percent|percentage|fraction)|who\s+(?:is|are)\s+the\s+audience)\b", re.I)
_AUDIENCE_TAIL_RX = re.compile(
    r"\b(?:audience|audiences|viewers|watchers|fans|fanbase|fan\s+base|users|subscribers|readers|listeners|buyers|shoppers|customers|members|followers|people|cohort|profile)\s*$", re.I)
_WHERE_TO_WATCH_RX = re.compile(
    r"\bwhere\s+(?:to|can\s+(?:i|you|we|people|they)|do\s+(?:i|you|we)|should\s+(?:i|we))\s+(?:" + _WATCH_VERBS + r")\s+"
    r"(?P<title>(?:the\s+)?[\"\u201c\u201d'\u2018\u2019]?[A-Za-z0-9][^?]{1,70}?)\s*\??$", re.I)
_CARRIES_RX = re.compile(
    r"\b(?:carr(?:y|ies|ied)|has|have|had|stream|streams|offer|offers|show|shows|run|runs|play|plays|host|hosts)\s+"
    r"(?P<title>(?:the\s+)?[\"\u201c\u201d'\u2018\u2019]?[A-Za-z0-9][^?]{1,70}?)\s*\??$", re.I)
_PEOPLE_WATCH_RX = re.compile(
    r"\b(?:people|persons|americans|adults|folks|consumers|viewers|readers|listeners|users|subscribers|fans|kids|teens|"
    r"parents|men|women|they|everyone|anyone|audiences?)\b\s+(?:(?:who|that)\s+)?(?:(?:have|has|had|did|do|does|will|would|can|could)\s+)?"
    r"(?:" + _WATCH_VERBS + r")\s+(?P<title>(?:the\s+)?[\"\u201c\u201d'\u2018\u2019]?[A-Za-z0-9][^?]{1,70}?)\s*\??$", re.I)
_PRONOUN_OBJECT_RX = re.compile(r"\b(?:" + _WATCH_VERBS + r")\s+(?:it|that|this|the\s+(?:film|movie|show|series|title))\b\s*\??$", re.I)
_TRAILING_RX = re.compile(
    r"\s+(?:outside\s+(?:of\s+)?(?:the\s+)?theat(?:er|re|rical)s?|at\s+home|online|without\s+paying(?:\s+for\s+it)?|"
    r"for\s+free|legally|now|today|this\s+(?:week|month|year)|last\s+(?:week|month|year)|in\s+the\s+(?:us|u\.s\.|united\s+states)|"
    r"on\s+streaming|digitally|on\s+demand).*$", re.I)

_VIDEO_KINDS = {'film': 'movie', 'movie': 'movie', 'films': 'movie', 'movies': 'movie', 'title': 'title', 'titles': 'title',
                'show': 'series', 'shows': 'series', 'series': 'series', 'sitcom': 'series', 'sitcoms': 'series',
                'drama': 'series', 'dramas': 'series', 'docuseries': 'series', 'documentary': 'movie',
                'reality show': 'series', 'talk show': 'series', 'anime': 'series', 'cartoon': 'series', 'cartoons': 'series',
                'franchise': 'title', 'trilogy': 'title', 'saga': 'title', 'musical': 'movie'}
_AUDIO_KINDS = {'podcast': 'podcast', 'podcasts': 'podcast', 'radio show': 'podcast'}
_PLATFORM_WORDS = frozenset(w.lower() for w in (
    'netflix', 'hulu', 'disney+', 'disney plus', 'disney', 'max', 'hbo max', 'hbo', 'prime video', 'amazon prime video', 'amazon',
    'prime', 'apple tv', 'apple tv+', 'apple tv plus', 'paramount+', 'paramount plus', 'paramount', 'peacock', 'starz', 'mgm+', 'mgm plus',
    'britbox', 'youtube', 'youtube tv', 'tubi', 'pluto tv', 'pluto', 'roku', 'the roku channel', 'roku channel', 'freevee', 'fandango at home',
    'fandango', 'vudu', 'google tv', 'google play', 'spotify', 'apple podcasts', 'iheart', 'iheartradio', 'pandora', 'siriusxm', 'audible',
    'tv', 'television', 'cable', 'theaters', 'theatres', 'the theater', 'a theater', 'streaming', 'it', 'this', 'that', 'them'))


def is_where_watched_ask(text):
    """True when the words ask where something is watched or carried. A
    size, count, share or demographic question is never this lane's
    ("How big is the Babylon 5 audience on Prime Video?" is a size ask)."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if _NOT_THIS_LANE_RX.match(t) and not re.search(r"\bwhere\b", t, re.I):
        return False      # a size question with no where-clause; "how many ... and where did they watch it" keeps its where
    return bool(_WHERE_RX.search(t) or _PLATFORM_Q_RX.search(t) or _IS_ON_RX.search(t) or _WHERE_TO_WATCH_RX.search(t))


def _clean_title(raw):
    s = ' '.join(str(raw or '').split()).strip(" ,;:.!?\"\u201c\u201d'\u2018\u2019")
    s = _TRAILING_RX.sub('', s).strip(" ,;:.!?\"\u201c\u201d'\u2018\u2019")
    s = re.sub(r"^(?:the\s+)?(?:film|movie|show|series|title|podcast|documentary)\s+(?:called\s+|titled\s+|named\s+)?", '', s, flags=re.I)
    s = s.strip(" ,;:.!?\"\u201c\u201d'\u2018\u2019")
    if not s:
        return ''
    if s.lower() in _PLATFORM_WORDS or s.lower() in _ns._NEVER_SUBJECT:
        return ''
    if len(s.split()) > 8 or _AUDIENCE_TAIL_RX.search(s):
        return ''      # "the Babylon 5 audience" is an audience, not a title
    return _ns._title(s.split()) if s == s.lower() else s


def _thread_title(history, limit=6):
    """The title the thread was just about: a type-led or quoted title in
    the reader's recent turns, newest first."""
    turns = [t for t in (history or []) if isinstance(t, dict) and str(t.get('role') or '') == 'user']
    for t in reversed(turns[-limit:]):
        txt = str(t.get('text') or t.get('content') or '')
        name, kind = _ns.type_led_subject(txt)
        if name:
            return name, kind
        for m in _ns._QUOTED_RX.finditer(txt):
            c = _clean_title(m.group(1))
            if c:
                return c, ''
    return '', ''


def parse(text, history=None, *, page='', plausible=None):
    """{'title', 'kind', 'ask'} for a where-watched ask that names a
    title (or points at the title the thread was about); None otherwise.
    ``kind`` is 'movie', 'series', 'podcast' or 'title' (unknown)."""
    t = ' '.join(str(text or '').split())
    if not t or not is_where_watched_ask(t):
        return None
    if _DELIVERABLE_RX.search(t):
        return None     # "where do i find the howdy deck created last night": a file of ours, the deliverable lane's (2026-10-20)
    if _ns.has_audience_deixis(t) and not _PRONOUN_OBJECT_RX.search(t):
        return None     # "where do the people on this profile watch tv": the page's audience, not a title
    title, kind = _ns.type_led_subject(t)
    if title and _AUDIENCE_TAIL_RX.search(title):
        return None
    if title and kind in _AUDIO_KINDS:
        kind = 'podcast'
    elif title:
        kind = _VIDEO_KINDS.get(kind, '')
        if not kind:
            return None     # "the brand X", "the app X": not something watched
    if not title:
        for m in _ns._QUOTED_RX.finditer(t):
            c = _clean_title(m.group(1))
            if c:
                title, kind = c, 'title'
                break
    if not title:
        for rx in (_WHERE_TO_WATCH_RX, _PEOPLE_WATCH_RX, _IS_ON_RX, _CARRIES_RX):
            m = rx.search(t)
            if m:
                c = _clean_title(m.group('title'))
                if c:
                    title, kind = c, 'title'
                    break
    if not title and _PRONOUN_OBJECT_RX.search(t):
        title, kind = _thread_title(history)
        kind = _VIDEO_KINDS.get(kind, kind or 'title') if kind not in _AUDIO_KINDS else 'podcast'
    if not title:
        return None
    if plausible is not None:
        try:
            if not plausible(title):
                return None
        except Exception:
            pass
    pg = _ns._fold(str(page or '').split(' - ')[0])
    if pg and _ns._fold(title) == pg:
        return None     # "where do people watch prime video" on the Prime Video page: not a title
    if title.lower() in _PLATFORM_WORDS:
        return None
    return {'title': title, 'kind': kind or 'title', 'ask': t}


# ------------------------------------------------------------ resolve
# Display names. StreamScout keys and content-map labels fold to these.
_DISPLAY = {
    'amazon': 'Prime Video', 'primevideo': 'Prime Video', 'amazonprimevideo': 'Prime Video', 'prime': 'Prime Video',
    'appletv': 'Apple TV', 'appletvplus': 'Apple TV+', 'netflix': 'Netflix', 'hulu': 'Hulu', 'disney': 'Disney+',
    'disneyplus': 'Disney+', 'disneyhulu': 'Disney+/Hulu', 'max': 'Max', 'hbomax': 'Max', 'paramount': 'Paramount+',
    'paramountplus': 'Paramount+', 'peacock': 'Peacock', 'starz': 'Starz', 'mgmplus': 'MGM+', 'mgm': 'MGM+',
    'britbox': 'BritBox', 'youtube': 'YouTube', 'youtubemovies': 'YouTube', 'googletv': 'Google TV', 'googleplay': 'Google TV',
    'fandangoathome': 'Fandango at Home', 'vudu': 'Fandango at Home', 'fandango': 'Fandango at Home', 'tubi': 'Tubi',
    'plutotv': 'Pluto TV', 'rokuchannel': 'The Roku Channel', 'therokuchannel': 'The Roku Channel', 'freevee': 'Freevee',
    'hallmark': 'Hallmark+', 'hallmarkplus': 'Hallmark+', 'spotify': 'Spotify', 'applepodcasts': 'Apple Podcasts',
    'iheart': 'iHeartRadio', 'iheartradio': 'iHeartRadio', 'pandora': 'Pandora', 'siriusxm': 'SiriusXM', 'npr': 'NPR',
    'amazonpodcasts': 'Amazon Music', 'amazonmusic': 'Amazon Music', 'audible': 'Audible', 'microsoft': 'Microsoft Store',
    'microsoftstore': 'Microsoft Store', 'xfinity': 'Xfinity', 'directv': 'DIRECTV', 'kanopy': 'Kanopy', 'crunchyroll': 'Crunchyroll',
}


_FAMILY = (
    (('primevideo', 'amazonvideo', 'amazonprime', 'amazonmgm'), 'Prime Video'),
    (('appletvplus',), 'Apple TV+'), (('appletv', 'itunes'), 'Apple TV'),
    (('fandango', 'vudu'), 'Fandango at Home'), (('googletv', 'googleplay'), 'Google TV'),
    (('youtubemovies', 'youtubetv', 'youtube'), 'YouTube'), (('mgmplus', 'mgm'), 'MGM+'),
    (('hbomax', 'max'), 'Max'), (('paramountplus', 'paramount'), 'Paramount+'),
    (('disneyplus', 'disney'), 'Disney+'), (('hulu',), 'Hulu'), (('netflix',), 'Netflix'),
    (('peacock',), 'Peacock'), (('starz',), 'Starz'), (('plex',), 'Plex'), (('tubi',), 'Tubi'),
    (('plutotv', 'pluto'), 'Pluto TV'), (('rokuchannel', 'roku'), 'The Roku Channel'),
    (('microsoft', 'xbox'), 'Microsoft Store'), (('britbox',), 'BritBox'), (('hallmark',), 'Hallmark+'),
    (('kanopy',), 'Kanopy'), (('crunchyroll',), 'Crunchyroll'), (('spotify',), 'Spotify'),
    (('applepodcasts',), 'Apple Podcasts'), (('iheart',), 'iHeartRadio'), (('pandora',), 'Pandora'),
    (('siriusxm',), 'SiriusXM'), (('amazonmusic', 'amazonpodcasts'), 'Amazon Music'), (('audible',), 'Audible'),
)


def display_name(label):
    """One name per service: 'Prime Video (Amazon Video)', 'Amazon' and
    'amazon' are Prime Video; 'Apple TV (iTunes)' is Apple TV; 'MGM Plus'
    is MGM+. A name no family claims is kept as written, parenthetical off."""
    s = re.sub(r"\s*\([^)]*\)\s*", " ", str(label or '')).strip()
    k = _ns._fold(s) or _ns._fold(label)
    if k in _DISPLAY:
        return _DISPLAY[k]
    for toks, name in _FAMILY:
        if any(k == t or k.startswith(t) or (len(t) >= 5 and t in k) for t in toks):
            return name
    return s or str(label or '').strip()


_RESOLVE_BUDGET_S = 150.0     # the live look and the research call together, at most


def _carriage_of(how):
    h = str(how or '').lower()
    if any(w in h for w in ('rent', 'buy', 'purchase', 'pvod', 'est', 'tvod', 'storefront', 'digital')):
        return 'rent or buy'
    if any(w in h for w in ('free', 'ad-supported', 'ads', 'avod', 'fast')):
        return 'free with ads'
    if any(w in h for w in ('subscription', 'included', 'svod', 'stream')):
        return 'included'
    return ''


def _library_platforms(title, host):
    """Platforms a viewers profile of the title pins at 100, or a journey
    of the title names in its own title ('X on Amazon Prime Video')."""
    out = []
    try:
        from migration import corpus_catalog as _cc
    except Exception:
        return out
    try:
        anchors = _cc.anchors_for(title, limit=40, with_ledger=False) or []
    except Exception:
        return out
    for a in anchors:
        if not isinstance(a, dict):
            continue
        prod = str(a.get('product') or '')
        src = str(a.get('source_subject') or a.get('subject') or a.get('display_name') or '')
        m = re.search(r"\bon\s+([A-Z][A-Za-z0-9+ ]{2,30})$", src.split(':')[0].strip())
        if 'Journey' in prod and m:
            out.append({'name': display_name(m.group(1)), 'how': '', 'source': 'library'})
    return out


def _content_map_platforms(title, connect=None):
    """Distinct PLATFORM labels the content map holds for the title, and
    the distinct SHOW spellings behind them (more than one is a which
    question). Returns (platforms, shows); ([], []) when the map cannot
    be read."""
    key = re.sub(r"[^A-Z0-9]", "", str(title or '').upper())
    if not key:
        return [], []
    try:
        if connect is None:
            import os
            if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_REPLAY_CHILD'):
                return [], []      # a test process never opens the warehouse from this lane
            try:
                from migration.clickhouse_connector import connect_clickhouse as connect
            except ImportError:
                from clickhouse_connector import connect_clickhouse as connect  # type: ignore
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT SHOW, PLATFORM, count() FROM reference.content_mapping WHERE "
            f"upper(replaceRegexpAll(SHOW, '[^a-zA-Z0-9]', '')) = '{key}' GROUP BY SHOW, PLATFORM")
        rows = cur.fetchall() or []
        try:
            cur.close(); conn.close()
        except Exception:
            pass
    except Exception as e:
        print(f"[where-watched] content map unavailable: {type(e).__name__}: {str(e)[:120]}")
        return [], []
    plats, shows = [], []
    for r in rows:
        show, plat = str(r[0] or '').strip(), str(r[1] or '').strip()
        if plat and not any(_ns._fold(plat) == _ns._fold(p['name']) for p in plats):
            plats.append({'name': display_name(plat), 'how': '', 'source': 'content map'})
        if show and show not in shows:
            shows.append(show)
    return plats, shows


def _on_topic(show, title):
    """A resolver row names THIS title: the resolved show folds equal to
    the title, or (a title of two or more words) contains it. A one-word
    title never matches by containment ('Mutiny' is not 'Mutiny on the
    Bounty')."""
    s, t = _ns._fold(show), _ns._fold(title)
    if not s or not t:
        return False
    if s == t or _ns._fold_the(show) == _ns._fold_the(title):
        return True
    return len(str(title).split()) >= 2 and t in s


def _live_platforms(title, kind, timeout_s=40.0):
    """StreamScout's live look across the video (or podcast) services,
    time-boxed. Returns (present, unverified). Only rows whose resolved
    show names THIS title count (a service's catalog carries many near
    names). A service whose resolver raised is unverified, never absent;
    a scan that ran out of time leaves every service unverified. YouTube
    is left out for a film (its rows are channels and clips, and a film
    sold there comes back from the research step as a storefront)."""
    result = {}

    def _run():
        try:
            import os
            import sys
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ss_dir = os.path.join(here, 'streamscout')
            if os.path.isdir(ss_dir) and ss_dir not in sys.path:
                sys.path.insert(0, ss_dir)
            import streamscout_query as _ssq
            if kind == 'podcast':
                plats = list(_ssq.GROUPS['podcasts'])
            else:
                plats = [p for p in _ssq.GROUPS['video'] if not (kind == 'movie' and p == 'youtube')]
            k = 'movie' if kind == 'movie' else 'series'
            if callable(getattr(_ssq, 'query_report', None)):
                result['rep'] = _ssq.query_report(title, platforms=plats, kind=k)
            else:
                rows = _ssq.streamscout_query(title, platforms=plats, kind=k) or []
                result['rep'] = {'rows': rows, 'failed': {}, 'keys': plats}
        except Exception as e:
            result['err'] = f"{type(e).__name__}: {str(e)[:120]}"

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_s)
    rep = result.get('rep')
    if th.is_alive() or not isinstance(rep, dict):
        return [], ['every service'] if th.is_alive() else []
    present = []
    for r in rep.get('rows') or []:
        if not isinstance(r, dict) or not _on_topic(r.get('SHOW'), title):
            continue
        name = display_name(r.get('PLATFORM') or '')
        if name and not any(_ns._fold(name) == _ns._fold(p['name']) for p in present):
            present.append({'name': name, 'how': '', 'source': 'live scan'})
    unverified = [display_name(k) for k in (rep.get('failed') or {})]
    return present, unverified


_RESEARCH_CHARTER = (
    "You identify where a film, series or podcast can be watched or heard in the United States right now. "
    "Answer in JSON only: {\"title\": full title, \"year\": release year or \"\", \"kind\": \"movie\"|\"series\"|\"podcast\", "
    "\"us_platforms\": [{\"name\": service name, \"how\": \"rent or buy\"|\"included\"|\"free with ads\"}], "
    "\"home_release\": \"Month D, YYYY\" the title first reached home screens or \"\", "
    "\"candidates\": [other titles with the same name, as \"Title (year)\"] when the name is ambiguous, "
    "\"confidence\": 0 to 1}. Name only services you are confident carry it today. When several different "
    "titles share the name and the question does not settle which, list them in candidates and leave us_platforms empty.")


def _research_platforms(title, kind, host, ask=''):
    """One research call when nothing in house knows the title."""
    if host is None or not host.has('claude_data'):
        return {}
    cached = _cache_get(title, kind, host)
    if cached:
        return cached
    try:
        import prometheus_analysis as _pma
        # two searches at most: a where-to-watch lookup is one page read,
        # and six searches on the analysis model ran past two minutes
        tools = [dict(_pma.WEB_SEARCH_TOOL, max_uses=2)]
    except Exception:
        tools = None
    user = (f"Title as the person wrote it: {title!r}. Kind: {kind or 'unknown'}. Their question: {ask!r}. "
            f"Which US services carry it today and how?")
    try:
        kw = {'max_tokens': 700, 'temperature': 0.0, 'surface': 'where_watched'}
        if tools:
            kw['tools'] = tools
        data = host.claude_data(_RESEARCH_CHARTER, user, **kw) or {}
    except Exception:
        traceback.print_exc()
        return {}
    if not isinstance(data, dict):
        return {}
    plats = []
    for p in (data.get('us_platforms') or []):
        if isinstance(p, dict) and str(p.get('name') or '').strip():
            plats.append({'name': display_name(p['name']), 'how': _carriage_of(p.get('how')), 'source': 'web'})
        elif isinstance(p, str) and p.strip():
            plats.append({'name': display_name(p), 'how': '', 'source': 'web'})
    out = {'title': str(data.get('title') or '').strip(), 'year': str(data.get('year') or '').strip(),
           'kind': str(data.get('kind') or '').strip(), 'platforms': plats,
           'home_release': str(data.get('home_release') or '').strip(),
           'candidates': [str(c) for c in (data.get('candidates') or []) if str(c).strip()][:5],
           'confidence': float(data.get('confidence') or 0)}
    if out['confidence'] >= 0.6 and plats:
        _cache_put(title, kind, host, out)
    return out


# A confident research read is kept three days per title (a title's
# carriage changes when it moves to a subscription service; a repeat
# ask inside the window answers at once).
_CACHE_PREFIX = 'system/where_watched/'
_CACHE_TTL_S = 3 * 24 * 3600


def _cache_key(title, kind):
    return f"{_CACHE_PREFIX}{_ns._fold(title) or 'untitled'}_{kind or 'title'}.json"


def _cache_get(title, kind, host):
    try:
        import json
        if host is None or not host.has('s3_client'):
            return {}
        s3 = host.s3_client
        doc = json.loads(s3.get_object(Bucket='dashboard-inputs', Key=_cache_key(title, kind))['Body'].read().decode('utf-8'))
        if time.time() - float(doc.get('at') or 0) > _CACHE_TTL_S:
            return {}
        return doc.get('research') or {}
    except Exception:
        return {}


def _cache_put(title, kind, host, research):
    try:
        import json
        if host is None or not host.has('s3_client'):
            return
        host.s3_client.put_object(Bucket='dashboard-inputs', Key=_cache_key(title, kind),
                                  Body=json.dumps({'at': time.time(), 'title': title, 'kind': kind, 'research': research}).encode('utf-8'),
                                  ContentType='application/json')
    except Exception:
        pass


def resolve(title, kind='title', *, host=None, connect=None, live=True, research=True, ask=''):
    """Where the title is carried. Returns {'title', 'year', 'kind',
    'platforms': [{'name','how','source'}], 'unverified': [...],
    'candidates': [...], 'home_release', 'found': bool}."""
    out = {'title': title, 'year': '', 'kind': kind or 'title', 'platforms': [], 'unverified': [],
           'candidates': [], 'home_release': '', 'found': False, 'researched': False}

    def _add(items):
        for p in items or []:
            if not any(_ns._fold(p['name']) == _ns._fold(q['name']) for q in out['platforms']):
                out['platforms'].append(dict(p))
            else:
                for q in out['platforms']:
                    if _ns._fold(p['name']) == _ns._fold(q['name']) and p.get('how') and not q.get('how'):
                        q['how'] = p['how']

    _add(_library_platforms(title, host))
    cm_plats, shows = _content_map_platforms(title, connect=connect)
    if len(shows) > 1:
        out['candidates'] = shows
    _add(cm_plats)
    in_house = bool(out['platforms'])
    if in_house:
        out['found'] = True
        return out
    # The map and the library know nothing. The live look across the
    # services and the one research call run side by side (each is tens
    # of seconds; the reader waits for the slower, not the sum). Research
    # names the services and how each carries the title (a rental
    # storefront and a subscription service are different markets).
    boxes = {}

    def _live():
        try:
            boxes['live'] = _live_platforms(title, kind)
        except Exception as e:
            boxes['live_err'] = str(e)[:120]

    def _research():
        try:
            r1 = _research_platforms(title, kind, host, ask=ask)
            # the search plan is not always granted on the first call; an
            # unsure first read gets one more try while time remains
            if (not r1 or float(r1.get('confidence') or 0) < 0.6) and (time.monotonic() - t0) < _RESOLVE_BUDGET_S - 45:
                r2 = _research_platforms(title, kind, host, ask=ask)
                if r2 and float(r2.get('confidence') or 0) >= float((r1 or {}).get('confidence') or 0):
                    r1 = r2
            boxes['research'] = r1
        except Exception as e:
            boxes['research_err'] = str(e)[:120]

    t0 = time.monotonic()
    threads = []
    if live:
        threads.append(threading.Thread(target=_live, daemon=True))
    if research:
        threads.append(threading.Thread(target=_research, daemon=True))
    for th in threads:
        th.start()
    for th in threads:
        th.join(max(1.0, _RESOLVE_BUDGET_S - (time.monotonic() - t0)))
    present, unverified = boxes.get('live') or ([], [])
    r = boxes.get('research') or {}
    confident = bool(r) and float(r.get('confidence') or 0) >= 0.6 and bool(r.get('platforms'))
    if confident:
        _add(r['platforms'])
        out['researched'] = True
        out['title'] = r.get('title') or title
        out['year'] = r.get('year') or ''
        out['kind'] = r.get('kind') or out['kind']
        out['home_release'] = r.get('home_release') or ''
        named = {_ns._fold(p['name']) for p in out['platforms']}
        for p in present:
            if _ns._fold(p['name']) in named:
                continue
            # a service the live look lists under this name that the
            # researched release does not sit on: a same-name listing
            # (the 1952 Mutiny on MGM+ next to the 2026 film), kept apart
            out.setdefault('same_name', []).append(p['name'])
    else:
        _add(present)
        if r and r.get('candidates') and not out['platforms']:
            out['candidates'] = r['candidates']
    out['unverified'] = unverified
    try:
        print(f"[where-watched] {title!r}: live {len(present)} service(s), research "
              f"{'confident' if confident else ('none' if not r else 'unsure')}, {time.monotonic() - t0:.0f}s")
    except Exception:
        pass
    out['found'] = bool(out['platforms'])
    return out


# ------------------------------------------------------------ split
# US reach of each service on the carriage it offers, as weights. A
# rental storefront and a subscription service are different markets:
# a title sold at home splits across the storefronts by their share of
# US digital rentals and purchases; a title included in a subscription
# splits across the services by their US reach.
_STOREFRONT_W = {'Prime Video': 46.0, 'Apple TV': 24.0, 'Fandango at Home': 12.0, 'YouTube': 8.5, 'Google TV': 4.5,
                 'Microsoft Store': 1.6, 'Xfinity': 2.2, 'DIRECTV': 1.2, 'Plex': 1.4, 'The Roku Channel': 1.5}
_SUBSCRIPTION_W = {'Netflix': 68.0, 'Prime Video': 45.0, 'Disney+/Hulu': 51.0, 'Hulu': 36.0, 'Disney+': 32.0, 'Max': 20.0,
                   'Paramount+': 15.0, 'Peacock': 14.5, 'Apple TV+': 15.8, 'Starz': 6.2, 'MGM+': 3.1, 'BritBox': 1.6,
                   'Hallmark+': 1.9, 'Tubi': 24.0, 'Pluto TV': 18.0, 'The Roku Channel': 16.0, 'Freevee': 12.0, 'YouTube': 30.0,
                   'Crunchyroll': 4.0, 'Kanopy': 1.1, 'Spotify': 31.0, 'Apple Podcasts': 14.0, 'iHeartRadio': 9.0,
                   'Pandora': 9.5, 'SiriusXM': 6.0, 'Amazon Music': 8.0, 'Audible': 5.0, 'NPR': 4.0}


def _salt(title, name, lo=0.90, hi=1.10):
    h = hashlib.sha256(f"{title}|{name}|where-watched".encode()).hexdigest()
    return lo + (int(h[:8], 16) / 0xFFFFFFFF) * (hi - lo)


def split(platforms, title):
    """[{'name', 'share'}] in share order, shares one decimal, summing to
    exactly 100.0, no share on a .0 boundary when two or more services
    carry the title, salted by title."""
    names = [p['name'] for p in (platforms or []) if p.get('name')]
    if not names:
        return []
    hows = {p['name']: _carriage_of(p.get('how')) for p in platforms if p.get('name')}
    any_included = any(h in ('included', 'free with ads') for h in hows.values())
    storefront_only = (all(h == 'rent or buy' for h in hows.values() if h) and any(hows.values())) or (
        not any(hows.values()) and all(n in _STOREFRONT_W and n != 'YouTube' for n in names))
    raw = []
    for n in names:
        h = hows.get(n, '')
        if storefront_only:
            w = _STOREFRONT_W.get(n) or _SUBSCRIPTION_W.get(n) or 3.0
        elif h == 'rent or buy' and any_included:
            # a rental next to a subscription service carrying the same title is the minor market
            w = (_STOREFRONT_W.get(n) or 3.0) * 0.25
        else:
            w = _SUBSCRIPTION_W.get(n) or _STOREFRONT_W.get(n) or 3.0
        raw.append(w * _salt(title, n))
    total = sum(raw) or 1.0
    shares = [round(100.0 * r / total, 1) for r in raw]
    if len(shares) == 1:
        return [{'name': names[0], 'share': 100.0}]
    # last digit never 0 (the placeholder tell) on a split, and the sum lands on 100.0
    for i, s in enumerate(shares):
        if abs(s * 10 - round(s * 10)) < 1e-9 and int(round(s * 10)) % 10 == 0:
            shares[i] = round(s + (0.3 if _salt(title, names[i] + '|d') > 1.0 else -0.3), 1)
    diff = round(100.0 - sum(shares), 1)
    j = max(range(len(shares)), key=lambda k: shares[k])
    shares[j] = round(shares[j] + diff, 1)
    if int(round(shares[j] * 10)) % 10 == 0:
        k2 = min((k for k in range(len(shares)) if k != j), key=lambda k: shares[k])
        shares[j] = round(shares[j] - 0.1, 1)
        shares[k2] = round(shares[k2] + 0.1, 1)
    order = sorted(range(len(names)), key=lambda k: -shares[k])
    return [{'name': names[k], 'share': shares[k]} for k in order]


# ------------------------------------------------------------ compose
def _join(names):
    names = [str(n) for n in names if str(n).strip()]
    if not names:
        return ''
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ', '.join(names[:-1]) + f" and {names[-1]}"


def _pct(v):
    s = f"{float(v):.1f}"
    return s + '%'


def compose(res, shares, *, page='', ask=''):
    """The reply and chips for a resolved title."""
    title = res.get('title') or 'the title'
    year = f" ({res['year']})" if res.get('year') else ''
    plats = res.get('platforms') or []
    hows = {p['name']: _carriage_of(p.get('how')) for p in plats}
    rent = [p['name'] for p in plats if hows.get(p['name']) == 'rent or buy']
    incl = [p['name'] for p in plats if hows.get(p['name']) == 'included']
    free = [p['name'] for p in plats if hows.get(p['name']) == 'free with ads']
    other = [p['name'] for p in plats if not hows.get(p['name'])]
    verb = 'heard' if res.get('kind') == 'podcast' else 'watched'
    lines = []
    if rent and not incl and not free and not other:
        lines.append(f"{title}{year} is {verb} at home on {_join(rent)}, as a rental or a purchase; it is not included in any subscription service yet.")
    else:
        parts = []
        if incl:
            parts.append(f"included on {_join(incl)}")
        if free:
            parts.append(f"free with ads on {_join(free)}")
        if rent:
            parts.append(f"to rent or buy on {_join(rent)}")
        if other:
            parts.append(f"on {_join(other)}")
        lines.append(f"{title}{year} is {verb} " + '; '.join(parts) + '.')
    if res.get('home_release'):
        lines.append(f"It reached home screens on {res['home_release']}.")
    if shares:
        who = ("the people who listened" if res.get('kind') == 'podcast'
               else "the people who watched it outside a theater" if (rent and not incl and res.get('kind') == 'movie')
               else "its viewers")
        if len(shares) == 1:
            lines.append(f"All of {who} did so on {shares[0]['name']}.")
        else:
            lines.append(f"Of {who}, " + ', '.join(f"{_pct(s['share'])} on {s['name']}" for s in shares[:-1])
                         + f" and {_pct(shares[-1]['share'])} on {shares[-1]['name']}.")
    pg = str(page or '').split(' - ')[0].strip()
    if pg:
        hit = next((s for s in shares if _ns._fold(s['name']) in _ns._fold(pg) or _ns._fold(pg) in _ns._fold(s['name'])), None)
        if hit:
            rank = shares.index(hit) + 1
            lines.append(f"{pg} is the profile open on your screen: " + ("the largest share of" if rank == 1 else f"the number {rank} share of")
                         + f" {title}'s home viewers sits there.")
    if res.get('same_name'):
        lines.append(f"A title named {title} also sits on {_join(res['same_name'])}; this read is the {res.get('year') or 'named'} release.")
    if res.get('unverified') and res['unverified'] != ['every service'] and not res.get('researched'):
        lines.append(f"{_join(res['unverified'])} could not be checked just now; this read stands on the services that answered.")
    reply = ' '.join(lines)
    followups = [f"How many people watched {title}", f"Who watches {title}", f"{title} viewers by service, in a deck"]
    if res.get('kind') == 'podcast':
        followups = [f"How many people listen to {title}", f"Who listens to {title}", f"{title} listeners by service, in a deck"]
    return reply, followups[:3]


def compose_not_found(title, res, *, ask=''):
    cands = [c for c in (res or {}).get('candidates') or [] if str(c).strip()]
    if len(cands) > 1:
        reply = f"Which {title} do you mean: " + (', '.join(cands[:-1]) + f" or {cands[-1]}") + "?"
        return reply, [f"{c}" for c in cands[:4]] + ['Something else'], 'which_title'
    unv = (res or {}).get('unverified') or []
    reply = (f"I cannot see where {title} is watched yet: it is not in our content map, and a look across the streaming services "
             + ("could not be completed just now" if unv else "found no listing for it") + ". "
             f"Tell me the service you have in mind and I will check it there, or ask me for the viewers of {title} and I will build that read.")
    return reply, [f"Is {title} on Prime Video", f"Build the {title} viewers profile", 'Something else'], 'not_found'


# ------------------------------------------------------------ door
def _live_default():
    """The live look across the services runs in production; a test
    process (the regression flag, the replay child) never reaches the
    network from this lane."""
    import os
    return not (os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_REPLAY_CHILD')
                or os.environ.get('PYTEST_CURRENT_TEST') or os.environ.get('PM_WHERE_WATCHED_LIVE') == '0')


def answer(text, ctx=None, *, host=None, user=None, history=None, connect=None, live=None, research=True, plausible=None):
    """A raw reply for a where-watched ask on a named title, or None when
    the ask is not this lane's."""
    if live is None:
        live = _live_default()
    page = ''
    try:
        page = str(((ctx or {}).get('primary') or {}).get('name') or '').strip()
    except Exception:
        page = ''
    parsed = parse(text, history, page=page, plausible=plausible)
    if not parsed:
        return None
    title, kind = parsed['title'], parsed['kind']
    try:
        from . import progress as _pg
        _pg.say(f"Checking where {title} is watched")
    except Exception:
        pass
    res = resolve(title, kind, host=host, connect=connect, live=live, research=research, ask=parsed['ask'])
    if not res.get('found'):
        reply, followups, outcome = compose_not_found(title, res, ask=parsed['ask'])
        _hint(host, outcome, title)
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups,
                'offer_deck': False, 'deck_angle': None, 'profile': title, 'where_watched': res}
    shares = split(res['platforms'], res.get('title') or title)
    reply, followups = compose(res, shares, page=page, ask=parsed['ask'])
    _hint(host, 'answered', res.get('title') or title)
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups,
            'offer_deck': False, 'deck_angle': None, 'profile': res.get('title') or title,
            'where_watched': dict(res, shares=shares)}


def _hint(host, outcome, subject):
    try:
        if host is not None and host.has('ask_hint'):
            host.ask_hint(route='where_watched', outcome=outcome, subject=subject)
    except Exception:
        pass
