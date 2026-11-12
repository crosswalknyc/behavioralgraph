"""A platform named in a question scopes the subject; it does not become
the subject (2026-11-11, Armine Meseldzhyan at Lionsgate).

Armine had a read on digital original content categories (Comedy,
Lifestyle / Arts & Culture, Faith, Young Adult, Horror). Hours later, in
a new chat: "Let's pull performance data on YouTube original content
across five categories: Comedy, Lifestyle/Arts & Culture, Faith, Young
Adult, and Horror?" The words carry a library name, YouTube, so the base
resolved to the YouTube profile. Every category was then sized against
everyone who uses YouTube (98,443,018 for Comedy against the 4.7M on
record), the ledger was asked for the subject `youtube`, and the read
held under the digital original content subject was never seen. A third
pass then carried the all-platform shares into a table that covered
YouTube only.

Three rules, one reading:

  * `resolve(text, base_subject, ...)`: when the base the lane picked is
    a platform's own profile, the words name that platform as where
    something happens ("on YouTube", "YouTube original content",
    "YouTube only"), and the ledger or the thread holds another subject
    the rest of the words name, the platform scopes that subject. The
    lane runs on the held subject with the platform as its cohort, and
    the held figures bound the answer. A question about the platform's
    own audience ("who uses YouTube", "YouTube viewers", "the YouTube
    audience") still takes the platform's profile.
  * `held_bucket(index_doc, skey, question)`: the ledger's side of the
    same rule. A lookup that resolved to the platform's bucket moves to
    the bucket of the subject the words name; the bucket already filed
    on that platform's scope first, the all-platform bucket otherwise
    (its figures still bound a platform-only read, but never replay as
    one).
  * `scope_of(*texts)` / `same_scope(a, b)`: the platforms a table is
    scoped to. A figure on record binds a new table only on the same
    scope: an all-platform share never lands in a YouTube-only table.

Pure text and dicts; no S3, no model. Never raises.
Regression: scripts/test_pm_platform_scopes_subject.py.
"""
from __future__ import annotations

import re

__all__ = ['is_cut_of', 'on_record', 'cut_subject', 'PLATFORMS', 'canon', 'named', 'scope_of', 'same_scope', 'held_bucket', 'resolve', 'for_read', 'apply_to_read', 'quote_subject']

# canonical name -> the ways it is written
PLATFORMS = {
    'YouTube': ('youtube', 'you tube', 'yt'),
    'TikTok': ('tiktok', 'tik tok'),
    'Instagram': ('instagram', 'ig', 'insta'),
    'Facebook': ('facebook',),
    'Snapchat': ('snapchat',),
    'X': ('twitter',),
    'Reddit': ('reddit',),
    'Twitch': ('twitch',),
    'Pinterest': ('pinterest',),
    'Spotify': ('spotify',),
    'Netflix': ('netflix',),
    'Hulu': ('hulu',),
    'Peacock': ('peacock',),
    'HBO Max': ('hbo max', 'hbomax', 'max'),
    'Paramount+': ('paramount+', 'paramount plus', 'paramount'),
    'Prime Video': ('amazon prime video', 'prime video', 'amazon prime'),
    'Disney+': ('disney+', 'disney plus'),
    'Apple TV+': ('apple tv+', 'apple tv plus', 'apple tv'),
    'Tubi': ('tubi',),
    'Pluto TV': ('pluto tv', 'pluto'),
    'Roku': ('the roku channel', 'roku channel', 'roku'),
    'Starz': ('starz',),
    'Discovery+': ('discovery+', 'discovery plus'),
}
# a platform's name inside a longer product name is that product, not the
# platform as a place ("started through Amazon Prime Video Channels" is a
# billing path; "YouTube TV" is a television service)
_PRODUCT_TAILS = {
    'Prime Video': re.compile(r"\s+channels?\b", re.I),
    'YouTube': re.compile(r"\s+(?:tv|music|kids|premium)\b", re.I),
    'Hulu': re.compile(r"\s*(?:\+|with|plus)\s*live\b", re.I),
    'Peacock': re.compile(r"\s+premium\b", re.I),
    'Spotify': re.compile(r"\s+premium\b", re.I),
    'Paramount+': re.compile(r"\s+(?:with showtime|pictures|global|network)\b", re.I),
    'Roku': re.compile(r"\s+(?:devices?|players?|tvs?|sticks?)\b", re.I),
    'Apple TV+': re.compile(r"\s+(?:devices?|box(?:es)?|4k|hd)\b", re.I),
}
_AMBIGUOUS = frozenset(('max', 'ig', 'yt', 'paramount', 'pluto', 'roku'))   # a scope only beside scope words
_ALIAS = {}
for _c, _names in PLATFORMS.items():
    _ALIAS[_c.lower()] = _c
    for _n in _names:
        _ALIAS[_n] = _c
_ALT = '|'.join(sorted((re.escape(a) for a in _ALIAS), key=len, reverse=True))
_P = rf"(?P<p>{_ALT})(?![\w+])"

# the thing that happens on the platform: the platform is where, not who
_CONTENT = (r"(?:original(?:s| content| programming| series| shows| videos)?|content|videos?|shows?|series|channels?|"
            r"creators?|categories|category|genres?|programming|viewing|viewership|views|titles?|clips?|shorts?|episodes?|"
            r"podcasts?|performance|engagement)")
# the platform's own audience: the platform is who
_AUDIENCE = (r"(?:users?|audiences?|viewers?|watchers?|subscribers?|members?|fans?|customers?|shoppers?|profile|file|"
             r"universe|households|people)")
_SCOPE_RXS = (
    re.compile(rf"\b(?:on|in|within|inside|across|via|through|for|at)\s+(?:the\s+)?{_P}(?!\s+{_AUDIENCE}\b)", re.I),
    re.compile(rf"(?<![\w+]){_P}\s*[- ]\s*only\b|\bonly\s+(?:on\s+)?{_P.replace('<p>', '<p2>')}", re.I),
    re.compile(rf"(?<![\w+]){_P}(?:'s)?\s+(?:digital\s+|big\s+|top\s+)?{_CONTENT}\b", re.I),
)
_SUBJECT_RXS = (
    re.compile(rf"(?<![\w+]){_P}(?:'s)?\s+{_AUDIENCE}\b", re.I),
    re.compile(rf"\b(?:who|people who|those who|anyone who)\s+(?:use[sd]?|watch(?:es|ed)?|(?:is|are)\s+on|subscribe[sd]?\s+to)\s+{_P}", re.I),
    re.compile(rf"\b(?:the\s+)?{_AUDIENCE}\s+(?:of|on|for)\s+{_P}", re.I),
    re.compile(rf"\b(?:size|sizing|profile|demographics?|audience)\s+(?:of|for)\s+{_P}", re.I),
)
# a platform listed as an example of the kind of thing is not a scope
_EXAMPLE_RX = re.compile(rf"\b(?:such as|like|e\.?g\.?|including|for example|for instance)\s+(?:[\w+' ]+,\s*)*(?:and\s+)?{_P}", re.I)
_ETC_RX = re.compile(r"\betc\b|\band (?:others|more|the rest)\b", re.I)

_POPULATION_RX = re.compile(r"\b(?:people|everyone|anyone|americans|adults|households|consumers|gen pop|general population|"
                            r"the us|us population)\b", re.I)
_STOP = frozenset("""
a an and are as at be by can could do does for from give have how i in include including is it its let lets me my of on or our
please pull run show tell that the their them these this those to us was we what when where which who why will with would you
your across each every all big top more most also both per between into over about data performance audience size viewership
examples example engagement content category five six four three two one seven eight nine ten many some any other
""".split())
_GENERIC = frozenset(('viewers', 'viewer', 'fans', 'fan', 'audience', 'users', 'user', 'profile', 'universe', 'total',
                      'series', 'show', 'movie', 'subscribers', 'customers', 'consumers', 'on', 'only'))


def canon(name):
    """The canonical platform a name is, or ''."""
    n = re.sub(r"\s+", " ", str(name or '').strip().lower())
    n = re.sub(r"\s*-\s.*$", "", n)          # 'YouTube - Avid Fan' is YouTube's own cut
    return _ALIAS.get(n, '')


def _words(text):
    t = re.sub(r"['\u2018\u2019]", "", str(text or '').lower())
    return [w for w in re.split(r"[^a-z0-9+]+", t) if w]


def _platform_words(platform):
    out = set()
    for a in (platform.lower(),) + tuple(PLATFORMS.get(platform, ())):
        out.update(_words(a))
    return out


def named(text):
    """{platform: how} for every platform the words carry: 'scope' (where
    something happens), 'subject' (the platform's own audience),
    'example' (listed as a kind of thing) or 'mention'."""
    t = str(text or '')
    out = {}
    try:
        for m in re.finditer(rf"(?<![\w+]){_P}", t, re.I):
            c = _ALIAS.get(m.group('p').lower())
            if c and not (m.group('p').lower() in _AMBIGUOUS and c not in out):
                out.setdefault(c, 'mention')
        for m in _EXAMPLE_RX.finditer(t):
            # every platform in the listed run is an example
            run = t[m.start():m.end() + 80]
            for m2 in re.finditer(rf"(?<![\w+]){_P}", run, re.I):
                c = _ALIAS.get(m2.group('p').lower())
                if c:
                    out[c] = 'example'
        for rx in _SCOPE_RXS:
            for m in rx.finditer(t):
                grp = 'p' if m.groupdict().get('p') else 'p2'
                raw = (m.groupdict().get(grp) or '').lower()
                c = _ALIAS.get(raw)
                if not c or out.get(c) == 'example':
                    continue
                tail = _PRODUCT_TAILS.get(c)
                if tail and tail.match(t[m.end(grp):]):
                    continue            # 'Amazon Prime Video Channels', 'YouTube TV': a product of its own, not where
                out[c] = 'scope'
        for rx in _SUBJECT_RXS:
            for m in rx.finditer(t):
                c = _ALIAS.get(m.group('p').lower())
                if c and out.get(c) != 'example':
                    out[c] = 'subject'
    except Exception:
        return {}
    return out


def scope_of(*texts):
    """The platforms a read or a table is scoped to, as a frozenset. An
    all-platform read, or one that lists platforms as examples, has no
    scope."""
    found = set()
    for t in texts:
        if not str(t or '').strip():
            continue
        n = named(t)
        scoped = {p for p, how in n.items() if how == 'scope'}
        if scoped and _ETC_RX.search(str(t)) and len(n) > 1:
            continue        # 'on YouTube, TikTok, etc.' is every platform
        found |= scoped
    return frozenset(found)


def same_scope(a, b):
    """Two reads sit on the same platform scope (both unscoped counts)."""
    return frozenset(a or ()) == frozenset(b or ())


def _distinct(subject, drop=()):
    return [w for w in dict.fromkeys(_words(subject)) if w not in _GENERIC and w not in drop and len(w) > 1]


def _coverage(subject, qwords, drop=()):
    toks = _distinct(subject, drop)
    if not toks:
        return 0, 0.0, 0
    hit = [w for w in toks if w in qwords]
    return len(hit), len(hit) / float(len(toks)), sum(len(w) for w in hit)


def held_bucket(index_doc, skey, question, live=None):
    """The bucket of the subject the words name when the lookup resolved
    to a platform's own bucket and the words name that platform as a
    scope. Returns {'key', 'subject', 'platform', 'same_scope'} or None."""
    try:
        subjects = (index_doc or {}).get('subjects') or {}
        cur = subjects.get(skey) or {}
        plat = canon(cur.get('subject') or str(skey or '').replace('_', ' '))
        if not plat:
            return None
        if named(question).get(plat) != 'scope':
            return None
        pw = _platform_words(plat)
        qwords = set(_words(question)) - pw
        best, best_score = None, None
        for k, v in subjects.items():
            if k == skey or not isinstance(v, dict):
                continue
            subj = str(v.get('subject') or k.replace('_', ' '))
            if canon(subj):
                continue                         # another platform's bucket is not the subject
            if live is not None and not live(v):
                continue
            n, frac, chars = _coverage(subj, qwords, drop=pw)
            if n < 3 or frac < 0.6 or chars < 14:
                continue
            # a title's bucket whose own label names the platform ('... (HBO Max viewers)', '... Viewers - Paramount+')
            # is that title on that platform: it is the scoped bucket, ahead of the title's other buckets (2026-11-11,
            # an HBO Max ask about Georgie & Mandy's First Marriage was handed the Paramount+ profile's figures)
            on_scope = plat in scope_of(subj) or plat in named(subj) \
                or any(plat in named(str(a)) for a in (v.get('aliases') or []))
            score = (1 if on_scope else 0, frac, n)
            if best_score is None or score > best_score:
                best, best_score = {'key': k, 'subject': subj, 'platform': plat, 'same_scope': on_scope}, score
        return best
    except Exception:
        return None


def _thread_subject(history):
    """The subject the thread's last answer landed on ('' when none)."""
    try:
        for turn in reversed(list(history or [])[-12:]):
            if not isinstance(turn, dict) or str(turn.get('role') or '') in ('user',):
                continue
            meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
            s = str(meta.get('subject') or turn.get('subject') or '').strip()
            if s:
                return s
            m = re.match(r"\s*On\s+([^:\n]{2,140}?)\s*(?::|\n)", str(turn.get('text') or turn.get('content') or ''))
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ''


def resolve(text, base_subject, *, ledger_doc=None, history=None, thread_subject='', live=None):
    """The held subject a platform scopes, when the lane's base is the
    platform's own profile. Returns {'platform', 'subject', 'bucket',
    'source', 'cohort', 'same_scope'} or None (the base stands)."""
    try:
        plat = canon(str(base_subject or '').split(' - ')[0])
        if not plat:
            return None
        how = named(text).get(plat)
        if how != 'scope':
            return None                          # the platform's own audience, or only a mention
        hit = held_bucket({'subjects': dict((ledger_doc or {}).get('subjects') or {}, **{'__p__': {'subject': plat}})},
                          '__p__', text, live=live) if ledger_doc else None
        if hit:
            return {'platform': plat, 'subject': hit['subject'], 'bucket': hit['key'], 'source': 'ledger',
                    'cohort': f"on {plat}", 'same_scope': bool(hit['same_scope'])}
        ts = str(thread_subject or '').strip() or _thread_subject(history)
        if ts and not canon(ts.split(' - ')[0]) and plat.lower() not in ts.lower() \
                and not _POPULATION_RX.search(str(text or '')):
            # the thread is on another subject and the words only add where
            # ("and on YouTube?", "the same split on TikTok only"); a
            # question about people at large is its own universe
            pw = _platform_words(plat)
            rest = [w for w in _words(text) if w not in pw and w not in _STOP]
            if len(rest) <= 4 or _coverage(ts, set(rest), drop=pw)[0] >= 1:
                return {'platform': plat, 'subject': ts, 'bucket': '', 'source': 'thread',
                        'cohort': f"on {plat}", 'same_scope': False}
        return None
    except Exception:
        return None


def for_read(text, history, base, ledger, bind_subject='', subj_hint=''):
    """The read lane's hook: the held subject a platform scopes, or None
    when the lane's own reading stands.

    Two ways the platform's profile takes a question it only scopes:
    the base resolves to the profile (the catalog names it), or the base
    was dropped as a platform surface and the subject the quote and the
    read are then given is the platform's bare name ("I can pull how
    many people in the US watched YouTube"; on an included plan that
    quote runs at once, bound to the YouTube profile). Both are read
    here. A subject the quote carried back counts as ours, not the
    reader's: it is overridden only when the ledger holds the subject
    the words name (never on the thread alone)."""
    try:
        cands = []
        if isinstance(base, dict) and str(base.get('source') or '') in ('catalog', 'page'):
            cands.append(str(base.get('subject') or '').split(' - ')[0])
        elif not base:
            hint = str(bind_subject or '').strip() or str(subj_hint or '').strip()
            if hint and not canon(hint.split(' - ')[0]):
                # the subject the lane was handed is one of the genres the words list ("Comedy", first of six
                # categories of digital original content on YouTube): a cut of the subject on record, not a
                # subject. The words decide, as they do when the lane was handed the platform (Armine 2026-11-11:
                # "I can pull that read on Comedy")
                try:
                    _doc = ledger._load_index() if ledger is not None else None
                except Exception:
                    _doc = None
                _live = getattr(ledger, '_has_live_entries', None) if ledger is not None else None
                for _p in [p for p, how in named(text).items() if how == 'scope']:
                    _hit = resolve(text, _p, ledger_doc=_doc, history=None, live=_live)
                    if _hit and _hit.get('source') == 'ledger' and is_cut_of(hint, _hit['subject'], text):
                        print(f"[pm-base] {hint!r} is a cut, not who: the words name {_hit['subject']!r} on {_p}")
                        return _hit
            for name in (bind_subject, subj_hint):
                if str(name or '').strip():
                    cands.append(str(name).split(' - ')[0])
            if not cands:
                cands.extend(p for p, how in named(text).items() if how == 'scope')
        plat = next((canon(c) for c in cands if canon(c)), '')
        if not plat:
            return None
        bound = str(bind_subject or '').strip()
        if bound and not canon(bound.split(' - ')[0]) and isinstance(base, dict) \
                and canon(str(base.get('subject') or '').split(' - ')[0]) == plat:
            # the question rides on a subject it was bound to (a part of a longer question, a chip) and the only
            # thing the library offered as its base is the platform's own profile, picked up from the words around
            # it: the platform is where, the bound subject is who (Armine 2026-11-11: "Can we give some examples?",
            # second in a question about digital original content on YouTube, was quoted on YouTube)
            print(f"[pm-base] {plat!r} is where, not who: the question is bound to {bound!r}; the platform's own profile is not the base")
            return {'platform': plat, 'subject': bound, 'bucket': '', 'source': 'bound',
                    'cohort': '' if plat.lower() in bound.lower() else f"on {plat}", 'same_scope': False}
        if any(str(n or '').strip() and not canon(str(n).split(' - ')[0]) for n in (bind_subject, subj_hint)) and not base:
            return None                          # the lane already holds a subject that is not the platform
        doc = None
        try:
            doc = ledger._load_index() if ledger is not None else None
        except Exception:
            doc = None
        live = getattr(ledger, '_has_live_entries', None) if ledger is not None else None
        hit = resolve(text, plat, ledger_doc=doc, history=history, live=live)
        if not hit:
            return None
        bound_to_platform = bool(str(bind_subject or '').strip()) and canon(str(bind_subject).split(' - ')[0]) == plat
        if (bound_to_platform or not base) and hit.get('source') != 'ledger':
            return None                          # a bound or guessed platform yields only to a subject on record
        print(f"[pm-base] {plat!r} is where, not who: the words scope {hit['subject']!r} to {hit['platform']} "
              f"({hit['source']}); the platform's own profile is not the base")
        return hit
    except Exception:
        return None


def apply_to_read(text, history, ledger, base, digest_block, subj_hint, bind_subject, bind_cohort, switch_page):
    """The read lane's one call: its own (base, digest_block, subj_hint,
    bind_subject, bind_cohort, switch_page), changed only when a platform
    the words name scopes a subject on record. Then the read has no
    profile base and no profile rows, runs on the held subject with the
    platform as its cohort, and the platform is never offered back as
    the page to switch to. Never raises."""
    same = (base, digest_block, subj_hint, bind_subject, bind_cohort, switch_page)
    try:
        hit = for_read(text, history, base, ledger, bind_subject, subj_hint)
        if not hit:
            return same
        sw = switch_page if (switch_page and canon(str(switch_page).split(' - ')[0]) != hit['platform']) else None
        return None, '', hit['subject'], hit['subject'], (bind_cohort or hit['cohort']), sw
    except Exception:
        return same


def quote_subject(text, history, subject, ledger=None):
    """The subject a quote names. When the words would put the quote on a
    platform's bare name (or on nothing) and the ledger holds the subject
    the words name, scoped to that platform, the quote names that
    subject; otherwise the subject it was given, unchanged. Never raises."""
    try:
        s = str(subject or '').strip()
        if ledger is None:
            import insights_ledger as ledger
        if s and not canon(s.split(' - ')[0]):
            # a cut of the subject on record is not the subject: one of the genres the words list ("Comedy" in a
            # question about digital original content on YouTube across six categories) is a row of that subject's
            # table (Armine 2026-11-11: the first part of her question was quoted on Comedy)
            hit = for_read(text, history, None, ledger, '', '')
            if hit and hit.get('source') == 'ledger' and is_cut_of(s, hit['subject'], text):
                return hit['subject']
            return subject
        hit = for_read(text, history, None, ledger, '', s)
        return hit['subject'] if hit else subject
    except Exception:
        return subject


def on_record(text, history=None, ledger=None):
    """The subject on record the words name where a platform only scopes
    it ('' when the read store holds none, or the words name no platform
    as where). The one answer every lane asks before it takes a platform
    or a genre as the subject."""
    try:
        if ledger is None:
            import insights_ledger as ledger
        hit = for_read(text, history, None, ledger, '', '')
        return str(hit['subject']) if hit and hit.get('source') == 'ledger' else ''
    except Exception:
        return ''


def cut_subject(text, history, name, ledger=None):
    """`name`, unless it is one of the things the subject on record is
    split by: then that subject. A platform is left as given (a chip
    that chose the platform keeps it)."""
    try:
        s = str(name or '').strip()
        if not s or canon(s.split(' - ')[0]):
            return name
        held = on_record(text, history, ledger)
        return held if held and is_cut_of(s, held, text) else name
    except Exception:
        return name


def is_cut_of(name, held_subject, text=''):
    """True when `name` is one of the things the held subject is split
    by, not a subject of its own: every word of it sits inside the held
    subject's name, it is shorter than that name, and the question
    lists it beside others (two or more commas or an "and")."""
    try:
        mine = _distinct(name)
        held = set(_words(held_subject))
        if not mine or not all(w in held for w in mine):
            return False
        if len(mine) >= len(_distinct(held_subject)):
            return False
        t = str(text or '')
        if not t:
            return True
        m = re.search(r"(?<![\w+])" + r"\W+".join(re.escape(w) for w in _words(name)) + r"(?![\w+])", t, re.I)
        if not m:
            return True                  # the words never said it: it came from somewhere else, and the held subject names it
        around = t[max(0, m.start() - 120): m.end() + 160]
        return around.count(',') >= 2 or bool(re.search(r",\s*(?:and|or)\s", around, re.I))
    except Exception:
        return False
