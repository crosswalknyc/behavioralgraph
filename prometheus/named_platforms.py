"""A platform the client names is the universe's platform (Jenna
2026-11-10, Armine Meseldzhyan at Lionsgate; Jessie the same night).

Armine: "Can we build a profile for Las Culturistas Awards that aired on
Peacock", then "Can we build the profiles only for the viewers on
peacock". The brief became "Las Culturistas Culture Awards Viewers",
seeded on the audience name with no Peacock placements. Jessie: "run a
profile on Viewers of GEORGIE & MANDY'S FIRST MARRIAGE, viewers ONLY on
Amazon and HBO MAX"; the build used one platform's placements.

The interpret prompt already asks the model to list the platforms the
user named (``named_platforms``, the StreamScout contract, 2026-10-16),
and the engine is strict on them: the seed comes from the content map
and the live scan for that platform, a platform that could not be
checked holds its stored placements, an honest zero refuses with the
client's own line, and no other platform's seeds are ever substituted.
What was missing is the same thing the window binder fixed for dates:
the model can lose it, above all when the platform arrives in a
follow-up. This module is the deterministic reader and binder.

``find(text)``                the platforms the words name as where the
                              audience watches or listens, and how
                              ('audience': "viewers on Peacock", "only
                              on Amazon and HBO Max", "Hulu viewers of
                              Gilmore Girls", "watched it on Netflix";
                              'described': "that aired on Peacock").
``from_thread(text, history, title)``
                              the ask first, then the turns before it
                              back to the last order that was started
                              (a platform named before approval still
                              counts; one named for an earlier build
                              does not ride into the next).
``bind(draft, text, history)``  writes the scope onto an interpret draft
                              for a title's audience: ``named_platforms``
                              (strict), ``seed_source`` = content_map,
                              ``content_show``, the consumers scope, the
                              home platform row, the house name
                              ('<Title> Viewers on Peacock'; two
                              platforms join with 'and') and the confirm
                              line. A described platform ("aired on
                              Peacock") scopes a viewers universe and is
                              kept aside (``described_platforms``) on a
                              broad one, so a later "viewers only"
                              answer picks it up (``promote``).
``disambiguation(text)``      "the special, not the podcast": the kind
                              the reader means and the kind they ruled
                              out; ``bind`` carries it as the draft's
                              ``content_kind`` / ``content_exclude`` and
                              the spec's ``medium`` (the kind every
                              placement lookup is told), so the special
                              is one title and the podcast another.
``universe_phrase(draft, base)``  'total viewers universe on Peacock'
                              for the confirm copy.

A multi-item ask that lists the same title on several services (Jay
Patel's three profiles, 2026-11-02) is the cut shape and belongs to
``prometheus.platform_cuts``; the caller passes ``cuts_hit`` and this
module stands aside.

Pure text in, dicts out. Never raises into a build. Regression:
scripts/test_pm_window_scope_2026_11_10.py.
"""
from __future__ import annotations

import re

from .platform_cuts import PLATFORM_ALIASES

__all__ = ['find', 'from_thread', 'bind', 'promote', 'disambiguation', 'scoped_name', 'universe_phrase',
           'stamp_spec']

# aliases a sentence can carry by accident ('max', 'kick', 'amazon' the
# store) count only behind an audience cue, never as a description
_LOOSE = frozenset(('max', 'kick', 'amazon', 'disney', 'roku', 'paramount', 'hbo', 'apple tv', 'pluto', 'kindle',
                    'audible', 'twitch', 'google play'))
_AUDIO = frozenset(('Spotify', 'Apple Music', 'Amazon Music', 'Pandora', 'SiriusXM', 'Audible'))

_ALIAS_RX = '|'.join(sorted((re.escape(k).replace(r'\ ', r'\s+') for k in PLATFORM_ALIASES), key=len, reverse=True))
_P = r"(?:the\s+)?(?:" + _ALIAS_RX + r")(?![A-Za-z0-9+])(?:\s+plus\b)?"
_LIST = r"(?P<list>" + _P + r"(?:\s*(?:,|/|&|\band\b|\bor\b|\bplus\b)\s*(?:,\s*)?(?:and\s+)?(?:on\s+)?" + _P + r")*)"
_NOUN = (r"(?:viewers?|watchers?|streamers?|listeners?|readers?|players?|audiences?|people|fans?|subscribers?|"
         r"customers?|users?|those|anyone|households|accounts)")
_VERB = (r"(?:watch(?:ed|ing|es)?|stream(?:ed|ing|s)?|listen(?:ed|ing|s)?(?:\s+to)?|saw|seen|view(?:ed|ing)|"
         r"play(?:ed|ing)|binged?|caught)")
_DESCRIBE = r"(?:aired|airs|airing|premiered|premieres|debuted|dropped|lives|is\s+available|was\s+available|available|is|was)"

_AUDIENCE_RXS = (
    # "viewers ONLY on Amazon and HBO MAX", "the viewers on peacock", "viewers of The Bear on Hulu"
    re.compile(r"\b" + _NOUN + r"\b(?P<mid>[^.?!\n]{0,90}?)\bon\s+" + _LIST, re.I),
    # "watched it on Netflix", "streamed Fallout Season 2 on Prime Video"
    re.compile(r"\b" + _VERB + r"\b(?P<mid>[^.?!\n]{0,90}?)\bon\s+" + _LIST, re.I),
    # "only on Peacock", "just on HBO Max", "on Peacock only"
    re.compile(r"\b(?:only|just|solely|exclusively|specifically)\s+(?:for\s+)?(?P<mid>)(?:on\s+)?" + _LIST, re.I),
    re.compile(r"\bon\s+(?P<mid>)" + _LIST + r"\s+(?:only|alone|specifically)\b", re.I),
    # "Hulu viewers of Gilmore Girls", "Peacock viewers"
    re.compile(r"(?P<mid>)" + _LIST + r"\s+(?:viewers?|watchers?|streamers?|listeners?)\b", re.I),
)
_WATCH_NOUN_RX = re.compile(r"^(?:viewers?|watchers?|streamers?|listeners?|readers?|players?)$", re.I)
_DESCRIBED_RX = re.compile(r"\b" + _DESCRIBE + r"\b(?P<mid>[^.?!\n]{0,30}?)\bon\s+" + _LIST, re.I)
# an order for a title on a service: "Subscriber IQ for The Paper on Peacock", "a profile of X on Hulu"
_ORDER_ON_RX = re.compile(
    r"\b(?:profiles?|subscriber\s+iq|audience|universe)\s+(?:for|of|on)\b(?P<mid>[^.?!\n]{2,90}?)\bon\s+" + _LIST
    + r"\s*(?:[.?!,;]|$)", re.I)
_NEG_RX = re.compile(r"\b(?:not|no|never|without|excluding|except|other\s+than|outside(?:\s+of)?|instead\s+of|"
                     r"rather\s+than|versus|vs\.?|besides|don'?t|doesn'?t|didn'?t)\s+(?:\w+\s+){0,2}$", re.I)
_ANY_RX = re.compile(r"\b(?:any|all|every|whatever|whichever)\s+(?:streaming\s+)?(?:service|platform|streamer|app)s?\b"
                     r"|\banywhere\b|\beverywhere\b|\bregardless\s+of\b", re.I)
_SPLIT_RX = re.compile(r"\s*(?:,|/|&|\band\b|\bor\b)\s*(?:on\s+)?", re.I)

_STARTED_RX = re.compile(
    r"\bprofile building\b|\bis building\b|\bbuild (?:is|has) (?:started|queued|running)\b|\bqueued\b|"
    r"\bi will email you when (?:it|they) (?:is|are) (?:complete|ready)\b|\bis ready\b|\bhas finished\b", re.I)
_CONSUMER_NOUNS = ('viewers', 'watchers', 'listeners', 'readers', 'players', 'streamers')
_TITLE_STOP = frozenset((
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'viewers', 'watchers', 'listeners', 'readers', 'players',
    'audience', 'profile', 'profiles', 'series', 'show', 'movie', 'film', 'season', 'awards', 'special', 'only',
    'total', 'universe', 'avid', 'fans', 'people', 'build', 'pull', 'make', 'can', 'you', 'please'))

_KINDS = {
    'special': 'special', 'specials': 'special', 'tv special': 'special', 'awards show': 'special', 'awards special': 'special',
    'podcast': 'podcast', 'podcasts': 'podcast', 'pod': 'podcast',
    'series': 'series', 'show': 'series', 'tv show': 'series', 'tv series': 'series',
    'movie': 'movie', 'film': 'movie', 'documentary': 'movie', 'doc': 'movie',
    'book': 'book', 'novel': 'book', 'game': 'game', 'video game': 'game', 'album': 'album', 'tour': 'tour',
}
_KIND_WORDS = '|'.join(sorted((re.escape(k) for k in _KINDS), key=len, reverse=True))
_NOT_KIND_RX = re.compile(
    r"\b(?:the\s+)?(?P<want>" + _KIND_WORDS + r")\b\s*[,;(-]?\s*(?:and\s+)?(?:not|rather\s+than|instead\s+of|as\s+opposed\s+to)\s+"
    r"(?:the\s+)?(?P<notk>" + _KIND_WORDS + r")\b"
    r"|\bnot\s+(?:the\s+)?(?P<notk2>" + _KIND_WORDS + r")\b\s*[,;-]?\s*(?:but\s+)?(?:the\s+)?(?P<want2>" + _KIND_WORDS + r")\b", re.I)
_ONLY_NOT_RX = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:about\s+)?(?:the\s+)?(?P<notk>" + _KIND_WORDS + r")\b", re.I)


def _canon(raw):
    low = ' '.join(str(raw or '').lower().replace('_', ' ').split()).strip(' .,;:')
    low = re.sub(r"^(?:the)\s+", '', low)
    low = re.sub(r"\s+plus$", ' plus', low)
    if low in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[low], low
    squashed = re.sub(r"[^a-z0-9+]+", '', low)
    for k, v in PLATFORM_ALIASES.items():
        if re.sub(r"[^a-z0-9+]+", '', k) == squashed:
            return v, k
    return '', low


def _platforms_in(list_text, loose_ok):
    out = []
    for piece in _SPLIT_RX.split(str(list_text or '')):
        name, key = _canon(piece)
        if not name:
            continue
        if key in _LOOSE and not loose_ok:
            continue
        if name not in out:
            out.append(name)
    return out


def find(text):
    """{'platforms': [...], 'how': 'audience' | 'described'} for the
    platforms the words name as where the audience watches or listens;
    None when they name none. Negated mentions ("not on Hulu", "no Prime
    Video") and "any service" are not a scope."""
    try:
        t = ' '.join(str(text or '').split())
        if not t or len(t) > 2500:
            return None
        aud, desc, strong = [], [], False

        def _take(m, bucket, loose_ok):
            lead = t[max(0, m.start('list') - 40):m.start('list')]
            if _NEG_RX.search(re.sub(r"\bon\s*$", '', lead)) or _NEG_RX.search(m.group('mid') or ''):
                return False
            got = False
            for p in _platforms_in(m.group('list'), loose_ok):
                got = True
                if p not in bucket:
                    bucket.append(p)
            return got
        for i, rx in enumerate(_AUDIENCE_RXS):
            for m in rx.finditer(t):
                mid = m.group('mid') or ''
                head = m.group(0).split()[0].lower() if m.group(0).split() else ''
                # the words say people WATCH there: a consumption noun or verb, or 'only on'
                watch = i in (1, 2, 3, 4) or bool(_WATCH_NOUN_RX.match(head)) or bool(re.search(r"\b" + _VERB + r"\b", mid, re.I))
                if i == 0 and not watch and re.search(r"\b" + _DESCRIBE + r"\s*$", mid.strip(), re.I):
                    continue          # 'fans of the show that aired on Peacock' describes the show
                if _take(m, aud, watch) and watch:
                    strong = True
        for m in _ORDER_ON_RX.finditer(t):
            if re.search(r"\b" + _DESCRIBE + r"\s*$", (m.group('mid') or '').strip(), re.I):
                continue              # 'a profile for X that aired on Peacock' is a description (below)
            _take(m, aud, False)
        for m in _DESCRIBED_RX.finditer(t):
            _take(m, desc, False)
        desc = [p for p in desc if p not in aud]
        if aud:
            return {'platforms': aud, 'how': 'audience', 'described': desc, 'strong': strong}
        if desc:
            return {'platforms': desc, 'how': 'described', 'described': desc, 'strong': False}
        return None
    except Exception:
        return None


def _title_tokens(title):
    toks = re.findall(r"[a-z0-9]+", str(title or '').lower())
    return {w for w in toks if len(w) >= 4 and w not in _TITLE_STOP}


def from_thread(text, history=None, title=''):
    """The scope the ask names, else the one a turn before it named for
    the same title, back to the last order that was started. Returns the
    dict ``find`` returns plus 'source' ('ask' | 'earlier'), or None."""
    hit = find(text)
    if hit and hit['how'] == 'audience':
        return {**hit, 'source': 'ask'}
    want = _title_tokens(title)
    turns = [t for t in (history or [])[-10:] if isinstance(t, dict)]
    seen_users = 0
    for i in range(len(turns) - 1, -1, -1):
        role = str(turns[i].get('role') or '').lower()
        body = str(turns[i].get('text') or turns[i].get('content') or '')
        if role != 'user':
            if _STARTED_RX.search(body):
                break                    # what was said before a build started belongs to that build
            continue
        seen_users += 1
        if seen_users > 4:
            break
        got = find(body)
        if not got:
            continue
        # the turn is about this title: it names it, or it is a short
        # follow-up to the ask before it that does
        about = bool(want & _title_tokens(body))
        if not about and len(body.split()) <= 14:
            for j in range(i - 1, -1, -1):
                if str(turns[j].get('role') or '').lower() == 'user':
                    about = bool(want & _title_tokens(str(turns[j].get('text') or turns[j].get('content') or '')))
                    break
        if not about:
            continue
        if got['how'] == 'audience' or not hit:
            return {**got, 'source': 'earlier'}
    if hit:
        return {**hit, 'source': 'ask'}
    return None


def disambiguation(text, history=None):
    """{'kind': 'special', 'exclude': ['podcast']} when the words say
    which thing they mean ("the special, not the podcast", "not the
    podcast, the special", "not the podcast"); None otherwise. The ask
    first, then the last three user turns."""
    texts = [str(text or '')]
    users = [t for t in (history or [])[-8:] if isinstance(t, dict) and str(t.get('role') or '').lower() == 'user']
    texts += [str(t.get('text') or t.get('content') or '') for t in reversed(users[-3:])]
    for body in texts:
        m = _NOT_KIND_RX.search(body)
        if m:
            want = _KINDS.get((m.group('want') or m.group('want2') or '').lower(), '')
            notk = _KINDS.get((m.group('notk') or m.group('notk2') or '').lower(), '')
            if want and notk and want != notk:
                return {'kind': want, 'exclude': [notk]}
        m = _ONLY_NOT_RX.search(body)
        if m:
            notk = _KINDS.get(m.group('notk').lower(), '')
            if notk:
                return {'kind': '', 'exclude': [notk]}
    return None


# ---------------------------------------------------------------- the draft
def _is_title_audience(draft):
    """The draft is the audience of a title (a series, a film, a special,
    a podcast, a game): the model said so, it is already seeded from the
    content map, its category is a content one, or it is a viewers /
    listeners name with a home platform researched for it. A brand, a
    person or a persona is not, whatever nouns its name carries."""
    cat = str(draft.get('brand_category') or '').strip().upper()
    if draft.get('is_ip_content') is False:
        return False
    return bool(
        draft.get('is_ip_content') is True or draft.get('content_show')
        or str(draft.get('seed_source') or '').startswith('content_map')
        or str(draft.get('ip_scope') or '').lower() == 'consumers'
        or cat.startswith(('SERIES', 'MOVIE', 'PODCAST', 'GAMES', 'VERTICAL SHORTS'))
        or (_carries_noun(draft.get('subject') or draft.get('name')) and bool(draft.get('home_platform_rows'))))


def _carries_noun(name):
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(name or '').lower()) + ' '
    return any(f' {w} ' in low for w in _CONSUMER_NOUNS)


def _is_consumers(draft):
    return (str(draft.get('ip_scope') or '').lower() == 'consumers'
            or str(draft.get('seed_source') or '').startswith('content_map')
            or _carries_noun(draft.get('subject') or draft.get('name')))


def _names(platform):
    return {k for k, v in PLATFORM_ALIASES.items() if v == platform} | {platform.lower()}


def _mentions(name, platform):
    low = ' ' + re.sub(r"[^a-z0-9+]+", ' ', str(name or '').lower()) + ' '
    return any(f" {re.sub(r'[^a-z0-9+]+', ' ', a).strip()} " in low for a in _names(platform) if a not in _LOOSE or a == platform.lower())


def _join(platforms):
    ps = [p for p in platforms if p]
    if len(ps) <= 1:
        return ''.join(ps)
    return ', '.join(ps[:-1]) + ' and ' + ps[-1]


def _bare_title(name):
    """The title inside an audience name: 'Las Culturistas Culture Awards
    Viewers on Peacock' -> 'Las Culturistas Culture Awards'."""
    s = ' '.join(str(name or '').split())
    s = re.sub(r"\s+on\s+\S.*$", '', s, flags=re.I) if _carries_noun(s) else s
    # the services in brackets after the noun ('... Viewers (HBO Max & Amazon Prime)') are not part of the title
    s = re.sub(r"(?i)(\b(?:" + '|'.join(_CONSUMER_NOUNS) + r"))\s*\([^()]*\)\s*$", r"\1", s)
    s = re.sub(r"\s+(?:" + '|'.join(_CONSUMER_NOUNS) + r")$", '', s, flags=re.I)
    s = re.sub(r"^(?:viewers|watchers|listeners|readers|players)\s+of\s+", '', s, flags=re.I)
    return s.strip(' -,')


# what is left of a tail once the services it held are taken out of it:
# '( /)', '( & )', '(and)', '[ , ]'
_EMPTY_TAIL_RX = re.compile(
    r"\s*[\(\[]\s*(?:(?:/|&|\+|,|-|\u2013|\band\b|\bor\b|\bplus\b|\bonly\b|\bon\b)\s*)*[\)\]]", re.I)


def _title_without(title, platforms):
    """The title with a named service taken off its start or end
    ('Gilmore Girls Hulu' from the audience name 'Gilmore Girls Hulu
    viewers' is the title 'Gilmore Girls'; 2026-11-12, a build ordered
    from a twin seeded nothing because its title carried the service).
    Only a name that can be nothing but the service is taken ('Hulu',
    'HBO Max', 'Prime Video'); 'Max' and 'Amazon' can be part of a title
    and stay. Never empties a title."""
    t = ' '.join(str(title or '').split())
    for p in (platforms or []):
        for a in sorted(_names(p), key=len, reverse=True):
            if a in _LOOSE and a != str(p).lower():
                continue
            for rx in (r"^\s*" + re.escape(a) + r"(?![A-Za-z0-9+])\s*[-:,]?\s+(?=\S)", r"(?<=\S)\s+(?:on\s+)?" + re.escape(a) + r"(?![A-Za-z0-9+])\s*$"):
                cut = re.sub(rx, '', t, count=1, flags=re.I).strip(' -,')
                if cut and cut != t:
                    t = cut
    return t


def scoped_name(name, platforms, verb='viewers'):
    """The house name for a platform-scoped audience: '<Title> Viewers
    on Peacock' for one service, '<Title> Viewers (Amazon Prime Video &
    HBO Max)' for several (the shape the live two-service file carries:
    '... Viewers (HBO Max & Amazon Prime)'). A name that already carries
    every platform is left as written ('Gilmore Girls Viewers ON HULU',
    '... Viewers (HBO Max & Amazon Prime)')."""
    subj = ' '.join(str(name or '').split())
    ps = [p for p in (platforms or []) if p]
    if not subj or not ps:
        return subj
    if all(_mentions(subj, p) for p in ps):
        return subj
    # a platform the name carries in another shape ('X Viewers - HBO Max',
    # 'X Hulu Viewers') is folded into the one house shape
    for p in ps:
        for a in sorted(_names(p), key=len, reverse=True):
            subj = re.sub(r"\s*(?:-|\u2013|,)?\s*\b(?:on\s+)?" + re.escape(a) + r"(?![A-Za-z0-9+])", '', subj, flags=re.I)
    # the brackets and joiners the services sat in go with them (2026-11-12:
    # '... Viewers (Amazon / HBO Max)' came back as '... Viewers ( /) on
    # Amazon Prime Video and HBO Max')
    subj = _EMPTY_TAIL_RX.sub('', subj)
    subj = re.sub(r"\s+(?:on|and|or|only)\s*$", '', ' '.join(subj.split()).strip(' -,/&+'), flags=re.I).strip(' -,/&+')
    if not _carries_noun(subj):
        v = verb if verb in _CONSUMER_NOUNS else 'viewers'
        subj = f"{subj} {v.title()}"
    if len(ps) > 1:
        return f"{subj} ({' & '.join(ps)})"
    return f"{subj} on {_join(ps)}"


def universe_phrase(draft, base='total viewers universe'):
    """'total viewers universe on Peacock' when the draft is scoped to
    the platform the client named; ``base`` otherwise."""
    try:
        ps = [str(p).strip() for p in (draft or {}).get('named_platforms') or [] if str(p).strip()]
        if ps and 'universe' in str(base) and ' on ' not in str(base):
            return f"{base} on {_join(ps)}"
    except Exception:
        pass
    return base


def _apply(draft, platforms, how):
    """Write a strict platform scope onto a title-audience draft."""
    have = [str(p).strip() for p in (draft.get('named_platforms') or []) if str(p).strip()] \
        if isinstance(draft.get('named_platforms'), list) else []
    merged = []
    for p in have + list(platforms):
        c = _canon(p)[0] or p
        if c and c not in merged:
            merged.append(c)
    merged = merged[:6]
    draft['named_platforms'] = merged
    draft['platform_scope_strict'] = True
    verb = str(draft.get('consumer_verb') or '').strip().lower()
    if verb not in _CONSUMER_NOUNS:
        verb = 'listeners' if all(p in _AUDIO for p in merged) else 'viewers'
        draft['consumer_verb'] = verb
    subj = str(draft.get('subject') or draft.get('name') or '').strip()
    title = str(draft.get('content_show') or '').strip() or _title_without(_bare_title(subj), merged)
    draft['is_ip_content'] = True
    draft['ip_scope'] = 'consumers'
    draft.pop('ask_ip_scope', None)
    draft.pop('ip_scope_data', None)
    if str(draft.get('seed_source') or '').strip() != 'content_map':
        draft['seed_source'] = 'content_map'
    if not str(draft.get('content_show') or '').strip() and title:
        draft['content_show'] = title
    # the platform is where a consumer watches: its row pins with the universe
    rows = [list(r) for r in (draft.get('home_platform_rows') or []) if isinstance(r, (list, tuple)) and len(r) >= 2]
    named_low = {p.lower() for p in merged}
    rows = [r for r in rows if str(r[1]).strip().lower() in named_low]       # a researched home the client did not name is not the scope
    have_rows = {str(r[1]).strip().lower() for r in rows}
    for p in merged:
        if p.lower() not in have_rows:
            rows.append(['STREAMING/MUSIC' if p in _AUDIO else 'STREAMING/PLATFORM', p])
    draft['home_platform_rows'] = rows
    if len(merged) == 1:
        draft['platform'] = merged[0]
    new = scoped_name(subj, merged, verb)
    if new and new != subj:
        draft['subject'] = new
        draft['name'] = new
        draft.pop('file_stem', None)
    line = f"{verb} on {_join(merged)} only"
    rid = str(draft.get('resolved_identity') or '').strip()
    if _join(merged).lower() not in rid.lower() or 'only' not in rid.lower():
        draft['resolved_identity'] = (f"{rid.rstrip('.')}; {line}" if rid else f"{title}, {line}")
    draft['platform_scope_note'] = (
        f"Scoped to {verb} on {_join(merged)}. If the title cannot be seen there, the build stops and says so; "
        f"another service is never used in its place.")
    draft.pop('described_platforms', None)
    return merged


def bind(draft, text, history=None, cuts_hit=None, catalog=None):
    """Bind the platform the client named onto an interpret draft.
    Returns the platforms bound (strict), or []. Mutates in place; never
    raises. No-op for an entity build, a reuse / cut decision, and the
    multi-service cut shape (``cuts_hit``).

    With the ``catalog`` in hand the decision is settled first
    (prometheus.service_scope_match, 2026-11-12): the same title on
    exactly the same services and window as a live profile is that
    profile, and a profile on other services is never the match."""
    try:
        if not isinstance(draft, dict) or cuts_hit or draft.get('platform_cuts'):
            return []
        if catalog:
            from . import service_scope_match as _ssm
            _acted, _note = _ssm.settle(draft, text, history, catalog)
            if _acted:
                print(f"[named-platforms] {_note}")
        dec = str(draft.get('decision') or 'new_build').strip().lower()
        if dec in ('existing_match', 'derive_cut'):
            return []
        dis = disambiguation(text, history)
        if dis:
            if dis.get('kind'):
                draft['content_kind'] = dis['kind']
            draft['content_exclude'] = list(dis.get('exclude') or [])
            show = str(draft.get('content_show') or '').strip()
            for ex in draft['content_exclude']:
                if show and re.search(r"\b" + re.escape(ex) + r"s?\b", show, re.I):
                    draft['content_show'] = _bare_title(draft.get('subject') or draft.get('name')) or show
        title = str(draft.get('content_show') or '').strip() or _bare_title(draft.get('subject') or draft.get('name'))
        hit = from_thread(text, history, title)
        if not hit or _ANY_RX.search(str(text or '')):
            return []
        if not _is_title_audience(draft):
            return []              # a brand, a person or a persona: 'customers on Amazon' is the store, not a viewing scope
        if hit['how'] == 'audience':
            return _apply(draft, hit['platforms'], 'audience')
        # described ("that aired on Peacock"): the scope of a viewers universe; kept aside on a broad one
        if _is_consumers(draft):
            return _apply(draft, hit['platforms'], 'described')
        draft['described_platforms'] = list(hit['platforms'])
        return []
    except Exception:
        return []


def promote(draft):
    """A broad title draft that became a viewers universe (the reader
    answered 'viewers only') takes the platform the ask described."""
    try:
        if not isinstance(draft, dict):
            return []
        ps = [p for p in (draft.get('described_platforms') or []) if str(p).strip()]
        if ps and _is_consumers(draft) and not draft.get('platform_cuts'):
            return _apply(draft, ps, 'described')
        # a scope bound earlier keeps its name through a later rename
        named = [p for p in (draft.get('named_platforms') or []) if str(p).strip()]
        if named and draft.get('platform_scope_strict'):
            subj = str(draft.get('subject') or draft.get('name') or '').strip()
            new = scoped_name(subj, named, str(draft.get('consumer_verb') or 'viewers'))
            if new and new != subj:
                draft['subject'] = new
                draft['name'] = new
                draft.pop('file_stem', None)
            return named
    except Exception:
        pass
    return []


def stamp_spec(spec, draft, scrub, subject):
    """The kind the reader meant rides the spec as ``medium`` (the kind
    every placement lookup is told) with what they ruled out beside it.
    Only a consumption universe (a seed_source on the spec) carries it."""
    try:
        if not isinstance(spec, dict) or not isinstance(draft, dict) or not spec.get('seed_source'):
            return spec
        kind = str(draft.get('content_kind') or '').strip().lower()
        if kind in set(_KINDS.values()):
            spec['medium'] = kind
        ex = [str(x).strip().lower() for x in (draft.get('content_exclude') or []) if str(x).strip().lower() in set(_KINDS.values())]
        if ex:
            spec['content_exclude'] = ex[:4]
        if draft.get('platform_scope_strict') and spec.get('content_seed_platforms_named'):
            spec['content_seed_platforms_strict'] = True
    except Exception:
        pass
    return spec
