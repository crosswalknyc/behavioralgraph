"""Deterministic second reader for the Prometheus guided intakes.

The guided pulls (Digital Journey, Flywheel, Brand Partnership,
Attribution) ask the user for a short brief and hand it to a model to
structure. When that read comes back thin on a message that plainly
carries the brief, this module reads the same text with plain rules:
a title in quotes, in caps, or named outright ("X is the series"),
platform names, and the verbs that mark an end step (watch / buy /
subscribe / rent). Whatever it finds fills ONLY the fields the model
left empty, so a good model read is never overwritten.

It also proposes the one missing field when two of three are present
(the "confirm instead of interrogate" step), so the user is asked to
nod at a sentence rather than retype what they already said.

Pure functions, no network, no Flask. Jenna 2026-10-02, after the
Babylon 5 intake answered "Almost there" twice on a complete brief.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------
# Platforms. Aliases are matched longest-first, case-insensitive, on
# word boundaries. ``kind`` decides how a bare "Amazon" resolves and
# which default end step a proposal uses.
# ---------------------------------------------------------------------
PLATFORMS = [
    # (canonical, kind, aliases)
    ('Prime Video', 'video', ('prime video', 'amazon prime video',
                              'amazon prime', 'primevideo', 'prime')),
    ('Netflix', 'video', ('netflix',)),
    ('Hulu', 'video', ('hulu',)),
    ('Max', 'video', ('hbo max', 'max')),
    ('Disney+', 'video', ('disney+', 'disney plus', 'disneyplus')),
    ('Peacock', 'video', ('peacock',)),
    ('Paramount+', 'video', ('paramount+', 'paramount plus',
                             'paramountplus')),
    ('Apple TV+', 'video', ('apple tv+', 'apple tv plus', 'apple tv')),
    ('Tubi', 'video', ('tubi',)),
    ('Pluto TV', 'video', ('pluto tv', 'pluto')),
    ('The Roku Channel', 'video', ('roku channel', 'the roku channel')),
    ('Starz', 'video', ('starz',)),
    ('AMC+', 'video', ('amc+', 'amc plus')),
    ('Crunchyroll', 'video', ('crunchyroll',)),
    ('Discovery+', 'video', ('discovery+', 'discovery plus')),
    ('BritBox', 'video', ('britbox',)),
    ('ESPN+', 'video', ('espn+', 'espn plus')),
    ('Fubo', 'video', ('fubo', 'fubotv')),
    ('Sling TV', 'video', ('sling tv', 'sling')),
    ('YouTube TV', 'video', ('youtube tv',)),
    ('YouTube', 'video', ('youtube',)),
    ('Twitch', 'video', ('twitch',)),
    ('Fandango at Home', 'video', ('fandango at home', 'vudu')),
    ('Spotify', 'audio', ('spotify',)),
    ('Apple Music', 'audio', ('apple music',)),
    ('Audible', 'audio', ('audible',)),
    ('TikTok Shop', 'commerce', ('tiktok shop', 'tik tok shop')),
    ('TikTok', 'social', ('tiktok', 'tik tok')),
    ('Instagram', 'social', ('instagram',)),
    ('Facebook', 'social', ('facebook',)),
    ('Reddit', 'social', ('reddit',)),
    ('X', 'social', ('twitter',)),
    ('Amazon', 'commerce', ('amazon.com', 'amazon')),
    ('Walmart', 'commerce', ('walmart',)),
    ('Target', 'commerce', ('target.com',)),
    ('Sephora', 'commerce', ('sephora',)),
    ('Ulta', 'commerce', ('ulta',)),
    ('Shopify', 'commerce', ('shopify',)),
    ('Etsy', 'commerce', ('etsy',)),
    ('eBay', 'commerce', ('ebay',)),
    ('Fandango', 'ticketing', ('fandango',)),
    ('Ticketmaster', 'ticketing', ('ticketmaster',)),
    ('StubHub', 'ticketing', ('stubhub',)),
    ('Steam', 'games', ('steam',)),
    ('PlayStation Store', 'games', ('playstation store', 'playstation')),
    ('Xbox', 'games', ('xbox',)),
    ('Nintendo eShop', 'games', ('nintendo eshop', 'nintendo')),
    ('App Store', 'apps', ('app store',)),
    ('Google Play', 'apps', ('google play',)),
    ('DTC site', 'commerce', ('dtc site', 'dtc', 'brand site',
                              'their own site', 'own website')),
]

_PLATFORM_BY_ALIAS = []
for _canon, _kind, _aliases in PLATFORMS:
    for _a in _aliases:
        _PLATFORM_BY_ALIAS.append((_a, _canon, _kind))
_PLATFORM_BY_ALIAS.sort(key=lambda t: -len(t[0]))
_PLATFORM_KIND = {c: k for c, k, _ in PLATFORMS}
_PLATFORM_NAMES_LOW = {c.lower() for c, _, _ in PLATFORMS} | {
    a for a, _, _ in _PLATFORM_BY_ALIAS}

# Verbs that mark an end step, by kind.
_WATCH = r'(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing)?|' \
         r'binge(?:d|s|ing)?|finish(?:ed|es|ing)?|play(?:ed|s|ing)?|' \
         r'listen(?:ed|s|ing)?(?: to)?)'
_BUY = r'(?:bought|buy(?:s|ing)?|purchas(?:ed|es|ing|e)|paid|pay(?:s|ing)?|' \
       r'order(?:ed|s|ing)?|checked out|check(?:s|ing)? out|added to cart)'
_SUB = r'(?:subscrib(?:ed|es|ing|e)|signed up|sign(?:s|ing)? up|' \
       r'start(?:ed|s|ing)? a (?:free )?trial|joined|join(?:s|ing)?)'
_RENT = r'(?:rent(?:ed|s|ing)?|pre-?order(?:ed|s|ing)?)'
_TICKET = r'(?:booked|book(?:s|ing)?|bought tickets?|attend(?:ed|s|ing)?)'
_INSTALL = r'(?:download(?:ed|s|ing)?|install(?:ed|s|ing)?)'

_KIND_PATTERNS = [
    ('purchase', _BUY), ('watch', _WATCH), ('purchase', _SUB),
    ('purchase', _RENT), ('purchase', _TICKET), ('purchase', _INSTALL),
]
_ANY_VERB = '(?:' + '|'.join(p for _, p in _KIND_PATTERNS) + ')'

# Objects that mark a STARTING behavior, not an end step.
_START_OBJECTS = re.compile(
    r'\b(?:social posts?|posts?|clips?|shorts?|reels?|tiktoks?|ads?|'
    r'trailers?|teasers?|promos?|creators?|influencers?|videos? about|'
    r'searched|search(?:ed|es|ing)?)\b', re.I)

_START_LEAD = re.compile(
    r'\b(?:start(?:ing)?(?: point)?(?: is| with| from|:)|begin(?:ning)?'
    r'(?: with| from)|cohort(?: is|:)|audience(?: is|:))\s+'
    r'(?P<c>(?:[^.;\n]|\.(?=\d)){6,})', re.I)
_START_WHO = re.compile(
    r'\b(?P<c>(?:people|accounts|users|viewers|those|anyone|fans) '
    r'(?:who|that) (?:[^.;\n]|\.(?=\d))*)', re.I)
_START_CUT = re.compile(
    r'\s*(?:,|;| and (?:then |went on|go on|later|eventually|converted)'
    r'| then\b| went on\b| go on\b| conversion\b| end step\b| goal\b)',
    re.I)

_CONV_LEAD = re.compile(
    r'\b(?:conversion|end step|end-step|goal|outcome|success)\s*'
    r'(?:event\s*)?(?:is|=|:|would be|should be|being)?\s*'
    r'(?P<c>(?:[^.;\n]|\.(?=\d))+)', re.I)
_CONV_WENT_ON = re.compile(
    r'\b(?:went on to|go on to|goes on to|then|and then|eventually|'
    r'later|converted by|convert(?:s|ed)? (?:by|when|once))\s+'
    r'(?P<c>' + _ANY_VERB + r'\b(?:[^.;\n]|\.(?=\d))*)', re.I)

_TITLE_IS = re.compile(
    r'(?P<t>["\u201c\u2018\']?[A-Z0-9][\w\'&:!+-]*(?:[ ]'
    r'(?:[A-Z0-9][\w\'&:!+-]*|of|the|and|&|in|on|a|to|vs\.?))*'
    r'["\u201d\u2019\']?)\s+(?:is|=)\s+(?:the|our|my)?\s*'
    r'(?:series|show|title|brand|movie|film|game|podcast|book|product|'
    r'category|subject|ip|property|franchise)\b', re.I)
_QUOTED = re.compile(
    r'["\u201c](?P<t>[^"\u201d]{2,80})["\u201d]|'
    r'\u2018(?P<t2>[^\u2019]{2,80})\u2019')
_ABOUT = re.compile(
    r'\b(?:about|of|on|for|around)\s+(?P<t>[A-Z0-9][\w\'&:!+-]*'
    r'(?:[ ](?:[A-Z0-9][\w\'&:!+-]*|of|the|and|&))*)')
_LEAD_ON = re.compile(
    r'^\W*(?P<t>[A-Z0-9][\w\'&:!+-]*(?:[ ](?:[A-Z0-9][\w\'&:!+-]*|of|'
    r'the|and|&))*)\s+(?:on|via|at|through)\s+', re.I)
_LEAD_COMMA = re.compile(
    r'^\W*(?P<t>[A-Za-z0-9][\w\'&:!+-]*(?:[ ][\w\'&:!+-]+){0,4})\s*[,:]')
_CAP_RUN = re.compile(
    r'(?<![\w\'])(?P<t>[A-Z][\w\'&:!+-]*(?:[ ](?:[A-Z0-9][\w\'&:!+-]*|'
    r'of|the|and|&)){0,5}[\w])')

_SUBJECT_STOP = {
    'conversion', 'trailing', 'start', 'starting', 'the', 'a', 'an',
    'and', 'or', 'with', 'on', 'in', 'to', 'of', 'for', 'is', 'are',
    'those', 'people', 'accounts', 'users', 'viewers', 'social', 'posts',
    'post', 'months', 'month', 'days', 'day', 'window', 'default', 'us',
    'gen', 'pop', 'please', 'build', 'pull', 'run', 'digital', 'journey',
    'flywheel', 'end', 'step', 'goal', 'platform', 'series', 'show',
    'title', 'brand', 'movie', 'film', 'game', 'episode', 'episodes',
    'season', 'optional', 'yes', 'no', 'ok', 'okay', 'thanks', 'i',
    'it', 'we', 'they', 'he', 'she', 'this', 'that', 'these', 'then',
    'within', 'after', 'before', 'during', 'from', 'by', 'at', 'as',
    'who', 'that', 'what', 'when', 'where', 'ecosystem', 'captured',
    'action', 'event', 'events', 'cohort', 'audience', 'campaign',
    'tracking', 'valuation', 'partner', 'partnership', 'owned',
    'surfaces', 'surface', 'app', 'site', 'website', 'twelve', 'last',
    'past', 'next', 'first', 'one', 'two', 'three', 'january',
    'february', 'march', 'april', 'may', 'june', 'july', 'august',
    'september', 'october', 'november', 'december', 'q1', 'q2', 'q3',
    'q4', 'ytd', 'tv',
}

_TRAILING = re.compile(
    r'\b(?:trailing|last|past|previous)\s+(?P<n>\d{1,2}|twelve|six|'
    r'three)\s+(?P<u>months?|weeks?|days?)\b', re.I)
_ISO_DATE = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')


def _clean(s):
    s = re.sub(r'\s+', ' ', str(s or '')).strip(' \t\r\n,;:-')
    return s.strip('"\u201c\u201d\u2018\u2019\'')


def _strip_platform_tail(title, platforms):
    """'Babylon 5 on Prime Video' -> 'Babylon 5'."""
    low = title.lower()
    for alias, _c, _k in _PLATFORM_BY_ALIAS:
        for sep in (' on ', ' via ', ' at ', ' through '):
            idx = low.find(sep + alias)
            if idx > 0:
                return title[:idx].strip()
    return title


def find_platforms(text):
    """Every platform named in the text, in order of first mention,
    as (canonical, kind, start_index). Aliases match longest-first so
    'Prime Video' wins over 'Prime' and 'TikTok Shop' over 'TikTok'."""
    low = str(text or '').lower()
    taken = [False] * (len(low) + 1)
    hits = []
    for alias, canon, kind in _PLATFORM_BY_ALIAS:
        for m in re.finditer(r'(?<![\w])' + re.escape(alias) + r'(?![\w])',
                             low):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            hits.append((canon, kind, m.start()))
    hits.sort(key=lambda h: h[2])
    out, seen = [], set()
    for canon, kind, pos in hits:
        if canon not in seen:
            seen.add(canon)
            out.append((canon, kind, pos))
    return out


def _is_start_clause(clause):
    return bool(_START_OBJECTS.search(clause or ''))


def find_start_behavior(text):
    t = str(text or '')
    m = _START_LEAD.search(t) or _START_WHO.search(t)
    if not m:
        return None
    clause = m.group('c')
    cut = _START_CUT.search(clause)
    if cut and cut.start() > 8:
        clause = clause[:cut.start()]
    clause = _clean(clause)
    return clause or None


_BUY_STRICT = re.compile(
    r'\b(?:bought|buy(?:s|ing)?|purchas(?:ed|es|ing|e)|order(?:ed|s|ing)?|'
    r'checked out|check(?:s|ing)? out|added to cart|'
    r'paid (?:for|to|\$|\d)|pay(?:s|ing)? (?:for|\$|\d))|\$\s?\d', re.I)


def _kind_of(clause):
    low = (clause or '').lower()
    # 'paid for', 'paid $', 'bought' are purchases; 'a paid episode' is
    # an adjective on a watch, so the watch verb decides there.
    if _BUY_STRICT.search(low):
        return 'purchase'
    if re.search(r'\b' + _WATCH + r'\b', low):
        return 'watch'
    for kind, pat in _KIND_PATTERNS:
        if re.search(r'\b' + pat + r'\b', low):
            return kind
    if re.search(r'\b(?:episodes?|season|stream|binge)\b', low):
        return 'watch'
    return None


def find_conversion(text, start_behavior=None):
    """(clause, kind) for the end step, or (None, None)."""
    t = str(text or '')
    cands = []
    m = _CONV_LEAD.search(t)
    if m:
        cands.append(m.group('c'))
    for m in _CONV_WENT_ON.finditer(t):
        cands.append(m.group('c'))
    # Any sentence/clause carrying an end-step verb whose object is not
    # a starting-behavior object (posts, clips, ads, searches).
    for part in re.split(r'[;\n]|\.(?!\d)', t):
        vm = re.search(r'\b' + _ANY_VERB + r'\b', part, re.I)
        if vm:
            head = part[:vm.start()]
            # 'Wicked For Good, paid $19.99 ...' -> start at the verb's
            # own clause, not the title that led the sentence.
            if ',' in head:
                part = part[head.rfind(',') + 1:]
            cands.append(part)
    sb = (start_behavior or '').lower()
    for c in cands:
        c = _clean(c)
        if len(c) < 6:
            continue
        low = c.lower()
        if sb and low in sb:
            continue
        # Drop the start-behavior half of a compound clause.
        if _is_start_clause(c):
            tail = _CONV_WENT_ON.search(c)
            if tail:
                c = _clean(tail.group('c'))
                low = c.lower()
            elif not re.search(r'\b(?:paid|bought|purchas|subscrib|'
                               r'rent|episode|season|\$)', low):
                continue
        kind = _kind_of(c)
        if kind:
            # Trim trailing window qualifiers that belong to the brief,
            # not the step ("... Trailing 12 months").
            c = re.sub(r'\s*(?:trailing|over the (?:last|past)).*$', '',
                       c, flags=re.I).strip(' ,;')
            return c, kind
    return None, None


def find_subject(text, platforms=None):
    t = str(text or '')
    plats = {c.lower() for c, _, _ in (platforms or find_platforms(t))}

    def ok(cand):
        cand = _clean(cand)
        if not cand:
            return None
        cand = _strip_platform_tail(cand, plats)
        low = cand.lower()
        if low in plats or low in _PLATFORM_NAMES_LOW:
            return None
        words = [w for w in re.findall(r"[\w'&+-]+", low)]
        if not words or all(w in _SUBJECT_STOP for w in words):
            return None
        if len(words) > 7:
            return None
        return cand

    m = _TITLE_IS.search(t)
    if m:
        c = ok(m.group('t'))
        if c:
            return c
    m = _QUOTED.search(t)
    if m:
        c = ok(m.group('t') or m.group('t2'))
        if c:
            return c
    for m in _ABOUT.finditer(t):
        c = ok(m.group('t'))
        if c:
            return c
    m = _LEAD_ON.match(t)
    if m:
        c = ok(m.group('t'))
        if c:
            return c
    m = _LEAD_COMMA.match(t)
    if m:
        c = ok(m.group('t'))
        if c:
            return c
    # Most repeated capitalized run that is not a platform or a brief
    # keyword. Sentence-initial words count only when repeated.
    counts = {}
    first = {}
    for m in _CAP_RUN.finditer(t):
        raw = m.group('t')
        # trim trailing connector words
        raw = re.sub(r'(?:\s+(?:of|the|and|&))+$', '', raw)
        c = ok(raw)
        if not c:
            continue
        key = c.lower()
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, (m.start(), c))
    if counts:
        def score(k):
            pos, _ = first[k]
            sentence_start = pos == 0 or t[max(0, pos - 2):pos].strip() in (
                '.', '!', '?', '')
            return (counts[k], 0 if sentence_start else 1,
                    len(k.split()), -pos)
        best = max(counts, key=score)
        if counts[best] > 1 or score(best)[1] == 1:
            return first[best][1]
    return None


def find_window(text):
    t = str(text or '')
    dates = _ISO_DATE.findall(t)
    if len(dates) >= 2:
        return dates[0], dates[1]
    return None, None


def _primary_platform(platforms, kind, conversion):
    """Pick the platform where the END STEP happens."""
    if not platforms:
        return None
    conv_low = (conversion or '').lower()
    # A platform named inside the conversion clause wins.
    for canon, pkind, _ in platforms:
        for alias, c2, _k in _PLATFORM_BY_ALIAS:
            if c2 == canon and re.search(r'(?<!\w)' + re.escape(alias)
                                         + r'(?!\w)', conv_low):
                return _resolve_amazon(canon, kind)
    # Otherwise the first non-social platform (social is where journeys
    # start, not where they end), else the first.
    for canon, pkind, _ in platforms:
        if pkind != 'social':
            return _resolve_amazon(canon, kind)
    return platforms[0][0]


def _resolve_amazon(canon, kind):
    if canon == 'Amazon' and kind == 'watch':
        return 'Prime Video'
    return canon


_CLIP_URL = re.compile(
    r'https?://(?:www\.)?(?:'
    r'instagram\.com/(?:p|reel|reels)/[A-Za-z0-9_-]+/?'
    r'|youtube\.com/(?:shorts/[A-Za-z0-9_-]+|watch\?v=[A-Za-z0-9_-]+)'
    r'|youtu\.be/[A-Za-z0-9_-]+'
    r'|tiktok\.com/@[\w.]+(?:/video/\d+)?'
    r'|tiktok\.com/t/[\w]+)',
    re.I)


def find_clip_url(text):
    m = _CLIP_URL.search(str(text or ''))
    return (m.group(0).rstrip('/') if m else None)


def _platform_from_clip(url):
    u = str(url or '').lower()
    if 'instagram.com' in u:
        return 'Instagram'
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'YouTube'
    if 'tiktok.com' in u:
        return 'TikTok'
    return None


# The two directions a journey about a title's viewers can run (Jenna
# 2026-10-19): how they came TO WATCH (Carolyn's shape: social short-form
# to the title, journey_kind watch) or what watchers do AFTER and IN
# ADDITION (the Survivor / NCIS / Sistas week-in-the-life shape,
# journey_kind media_week). An ask that says neither gets the question.
_AFTER_WATCH_RE = re.compile(
    r"after (?:they|people|viewers|fans|watchers|someone|you) (?:watch|stream|finish)|after watching|post[- ]?watch|"
    r"in addition to (?:watching|the (?:show|episode|title))|(?:day|week) in the (?:media |digital )?life|"
    r"what (?:else )?(?:do|are|does) [^.?!]{0,50}\b(?:viewers|watchers|fans|audience|people who watch)\b [^.?!]{0,20}"
    r"\b(?:do|doing|watch|listen|use|engage|read|stream|open)\b|"
    r"rest of (?:their|the) (?:week|day)|around the episode|between episodes|what watchers do|"
    r"(?:behavior|behaviour|habits) (?:after|around|beyond) (?:the|a) (?:watch|episode|show)|"
    r"what (?:else )?(?:they|watchers|viewers) (?:watch|listen to|use|do)",
    re.I)
_TO_WATCH_RE = re.compile(
    r"(?:came|come|get|got) to watch|how (?:they|people|viewers) (?:found|find|discover|discovered|came|come|arrived|got)|"
    r"from (?:social|a (?:post|clip|reel|short)|short[- ]form|tiktok|instagram|youtube)|"
    r"to the (?:title|show|first episode|watch)|(?:drove|drives|led|leads) (?:them |people )?to (?:watch|the title)|"
    r"\bend(?:s|ing)? at\b|\bstart(?:s|ing)? (?:from|at|with)\b|social (?:post|clip|view) to|discovery",
    re.I)
_VIEWER_JOURNEY_RE = re.compile(
    r"\b(?:viewer|viewership|watcher|audience|fan)s?'?\b[^.?!]{0,40}\bjourney\b|\bjourney\b[^.?!]{0,60}\b(?:viewer|viewership|watcher|audience|fan)s?\b",
    re.I)
DIRECTION_CHIPS = ('How they came to watch', 'What watchers do after and in addition')


_BOTH_DIRECTIONS_RE = re.compile(
    r"^\W*(?:i\s+want\s+|i'?d\s+like\s+|give\s+me\s+|do\s+|run\s+|show\s+me\s+)?(?:both|all\s+of\s+(?:that|it|them|the\s+above)|all\s+(?:of\s+)?(?:that|it)|everything|"
    r"either(?:\s+one)?|whichever|you\s+(?:pick|choose|decide)|(?:the\s+)?default|no\s+preference|whatever\s+you\s+(?:recommend|suggest|think)|"
    r"the\s+(?:full|whole|complete)\s+(?:picture|path|journey|thing))\W*(?:please)?\W*$", re.I)
BOTH_DIRECTIONS_NOTE = ("You asked for both directions. This journey runs as how they came to watch; say "
                        "'the week after' and I will set up what watchers do after and in addition as its own journey.")


def journey_direction_from_text(text):
    """'after_watch', 'to_watch', 'unclear' or None for an ask about a title's viewers."""
    low = ' '.join(str(text or '').lower().split())
    if not low:
        return None
    if _BOTH_DIRECTIONS_RE.match(low):
        return 'to_watch'   # "I want all of that", "both", "you pick": the discovery shape, with the other offered (Debi 2026-10-21)
    if low.strip(' .!') == DIRECTION_CHIPS[1].lower() or _AFTER_WATCH_RE.search(low):
        return 'after_watch'
    if low.strip(' .!') == DIRECTION_CHIPS[0].lower() or _TO_WATCH_RE.search(low):
        return 'to_watch'
    if _VIEWER_JOURNEY_RE.search(low) and not find_clip_url(text) \
            and not re.search(r'\b(?:bought|buy|purchase|ticket|showtime|song|soundtrack)\b', low):
        return 'unclear'
    return None


def direction_question(subject=None):
    who = f"{subject} " if subject else ''
    return (f"One question before I line it up: do you want how {who}viewers came TO WATCH (the path from social "
            f"and short-form to the title), or what {who}watchers do after and in addition to watching (their week: the other "
            f"shows, services, podcasts, social, news and games around the episode)?")


def direction_unclear(parsed):
    return str((parsed or {}).get('journey_direction') or '').lower() == 'unclear'


def direction_asked_already(history):
    """True when the last thing Prometheus said in this thread was the
    direction question: it is never asked twice in a row. The reader's
    reply, whatever it was, takes the default (Debi 2026-10-21 was asked
    the same question three times: "is US gen pop", "I want all of that")."""
    for t in reversed([x for x in (history or []) if isinstance(x, dict)]):
        if str(t.get('role') or '') == 'user':
            continue
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        return txt.startswith('One question before I line it up: do you want how')
    return False


def default_direction(parsed, both=False):
    """The discovery shape when the reader did not pick one, with the note
    that names the other."""
    p = apply_direction({**(parsed or {}), 'journey_direction': 'to_watch'})
    p['_precedent_note'] = ((str(p.get('_precedent_note') or '') + '\n\n') if p.get('_precedent_note') else '') + (
        BOTH_DIRECTIONS_NOTE if both else
        "I ran this as how they came to watch. Say 'the week after' and I will set up what watchers do after and in addition as its own journey.")
    return p


def apply_direction(parsed):
    """Make journey_kind and journey_direction agree: after_watch is the
    media-week kind, to_watch is a watch journey unless another kind is
    already set; a media_week kind implies after_watch."""
    p = dict(parsed or {})
    d = str(p.get('journey_direction') or '').lower()
    kind = str(p.get('journey_kind') or '').lower()
    if kind == 'media_week':
        p['journey_direction'] = 'after_watch'
    elif d == 'after_watch':
        p['journey_kind'] = 'media_week'
    elif d == 'to_watch' and kind in ('', 'media_week'):
        p['journey_kind'] = 'watch'
    if p.get('journey_kind') == 'media_week':
        p['missing'] = [k for k in (p.get('missing') or []) if k not in ('platform', 'conversion_event', 'journey_direction')]
        if not p.get('conversion_event'):
            p['conversion_event'] = f"watched the next new episode of {p.get('subject') or 'the title'} within 7 days"
    elif p.get('journey_direction') not in (None, '', 'unclear'):
        p['missing'] = [k for k in (p.get('missing') or []) if k != 'journey_direction']
    return p


def journey_inputs_complete(parsed):
    """The Digital Journey intake is complete when: the direction is known
    (an ask that has not said which way it runs is asked, never assumed),
    and either it is the after-watch read with a title, or it names the
    title, the platform and the end step."""
    p = parsed or {}
    if direction_unclear(p):
        return False
    if str(p.get('journey_kind') or '').lower() == 'media_week':
        return bool(p.get('subject'))
    return bool(p.get('subject') and p.get('platform') and p.get('conversion_event'))


def media_week_confirm_reply(parsed, window_label, price_label):
    """The confirm card for the after-watch read: the Survivor / NCIS / Sistas shape."""
    home = parsed.get('platform') or 'its streaming home, confirmed from the title'
    return (
        f"Here's the Digital Journey I'll build:\n"
        f"- {parsed['subject']} viewers, a week in the life\n"
        f"- The read: what they do after and in addition to watching, not how they came to watch\n"
        f"- Streaming home: {home}\n"
        f"- Window: {window_label}\n\n"
        "It's the same shape as the Survivor, NCIS and Sistas reads: the viewers, who comes back to the title "
        "off-screen in a week, and who watches the next episode; episode night or later in the week; then where "
        "they stream it, how fast they watch a new episode, their week day by day, an episode day by daypart, "
        "and the other shows, streaming services, podcasts and audio, social platforms, news, sports and games "
        "around the episode, each against US adults. It lands in the Digital Journey tab when finished. It "
        f"prices at {price_label}. Run it?")


def _kind_from_ask(text, kind):
    low = str(text or '').lower()
    if journey_direction_from_text(text) == 'after_watch':
        return 'media_week'
    if re.search(r'before and after|before/after|20\s*-?\s*min', low) \
            and find_clip_url(text):
        return 'before_after'
    if re.search(r'\bnew to\b', low) and re.search(
            r'\balready\b|\bexisting\b', low):
        return 'discovery_existing'
    if re.search(r'\bsong to\b|music[- ]first|soundtrack|needle-?drop', low):
        return 'music'
    return kind


# ---------------------------------------------------------------------
# A new ask typed while an intake is collecting (2026-10-07 Eliot: the
# Digital Journey intake was waiting for a platform when he asked "is
# there a way to build an audience of people who have attended a Gunna
# concert?" and got "I still need platform"). Field answers are short,
# or URLs, or numbered lines, or name a platform / verb the brief
# needs. A question or a build order about something else is not an
# answer; the caller drops the intake and routes it normally.
# ---------------------------------------------------------------------
_NEW_ASK_OPEN = re.compile(
    r'^\W*(?:is there a way|can (?:you|we|i)|could (?:you|we|i)|'
    r'would (?:you|it be possible)|how (?:do|can|would|could) (?:i|we|you)|'
    r'what|who|which|why|where|when|do you|does|did|are there|is it possible|'
    r'help me|i need|i want|i am looking|we are looking|tell me|show me|give me|'
    r'find|identify|recommend|suggest|please help)\b',
    re.I)
# A sentence that names someone and asks for direction ("Austin Sprinz is
# thinking about new sponsorship opportunities. Help me identify where he
# should be going.") is a new ask even mid-sentence (2026-10-12, Debi: it
# was read as flywheel fields and answered with raw field names).
_NEW_ASK_INLINE = re.compile(
    r'\b(?:help (?:me|us) (?:identify|find|figure out|understand|see|decide)|'
    r'(?:who|where|what|which) (?:should|could|would) (?:he|she|they|we|i)\b|'
    r'is (?:thinking about|looking for|trying to find|exploring|considering)\b)',
    re.I)
_BUILD_ASK = re.compile(
    r'\b(?:build|create|pull|run|make|put together|set up)\b[^.?!]{0,40}'
    r'\b(?:an?\s+|the\s+)?(?:audience|universe|profile|segment|cohort)s?\b'
    r'[^.?!]{0,10}\b(?:of|for|on)\b', re.I)
_FIELD_SHAPE = re.compile(
    r'https?://|^\s*\d+[.)]\s|^\s*(?:platform|conversion|title|category|'
    r'brand|partner|window|campaign|urls?)\s*[:=-]', re.I | re.M)


def is_new_ask(text):
    """True when a message sent during a guided intake is a new ask
    rather than the fields the intake is waiting for."""
    t = ' '.join(str(text or '').split())
    if not t or _FIELD_SHAPE.search(t):
        return False
    if t.strip(' .!').lower() in {c.lower() for c in DIRECTION_CHIPS}:
        return False   # the answer to "which way does the journey run" belongs to the intake
    words = t.split()
    if _BUILD_ASK.search(t):
        return True
    if _NEW_ASK_OPEN.match(t) and (t.endswith('?') or len(words) >= 8):
        return True
    if _NEW_ASK_INLINE.search(t) and len(words) >= 8:
        return True
    return False


def missing_labels(parsed, fallback):
    """The plain words for an intake's missing fields ('the starting
    point', never 'captured_action'). ``fallback`` is the full list the
    stall copy used when the parser named nothing (2026-10-12, Debi:
    "I still need captured_action, ecosystem, conversion_event")."""
    keys = [k for k in ((parsed or {}).get('missing') or []) if k]
    if not keys:
        return list(fallback or [])
    out = []
    for k in keys:
        lbl = FIELD_LABELS.get(str(k)) or str(k).replace('_', ' ').strip()
        if lbl not in out:
            out.append(lbl)
    return out


def keyword_parse_journey(text):
    """Digital Journey brief -> the PARSE_SYSTEM_PROMPT shape, best effort.
    Fields it cannot read are None and listed in ``missing``."""
    t = str(text or '')
    plats = find_platforms(t)
    start = find_start_behavior(t)
    conv, kind = find_conversion(t, start)
    clip_url = find_clip_url(t)
    kind = _kind_from_ask(t, kind)
    if clip_url and not conv:
        conv = 'watched this clip'
        kind = kind or 'before_after'
    platform = _primary_platform(plats, kind, conv) \
        or _platform_from_clip(clip_url)
    subject = find_subject(t, plats)
    sd, ed = find_window(t)
    direction = journey_direction_from_text(t)
    if kind == 'media_week':
        direction = 'after_watch'
    elif direction == 'unclear' and (conv or start):
        direction = 'to_watch'
    try:
        from migration import journey_gold_watch as _gw
        watch_definition = _gw.definition_from_text(t) or None   # "saw a clip and then went on to view", "no click needed"
    except Exception:
        watch_definition = None
    out = {
        'subject': subject, 'platform': platform,
        'conversion_event': conv, 'journey_kind': kind or None,
        'journey_direction': direction,
        'watch_definition': watch_definition,
        'clip_url': clip_url,
        'start_behavior': start, 'start_date': sd, 'end_date': ed,
        'tam_label': None, 'tam_accounts': None, 'notes': None,
    }
    if kind == 'media_week':
        out['missing'] = [k for k in ('subject',) if not out.get(k)]
    else:
        out['missing'] = [k for k in ('subject', 'platform', 'conversion_event')
                          if not out.get(k)]
        if direction == 'unclear':
            out['missing'].append('journey_direction')
    return out


def keyword_parse_flywheel(text):
    """Flywheel brief -> subject / captured_action / ecosystem /
    conversion_event, best effort."""
    t = str(text or '')
    plats = find_platforms(t)
    start = find_start_behavior(t)
    conv, kind = find_conversion(t, start)
    eco = _primary_platform(plats, kind, conv)
    out = {
        'subject': find_subject(t, plats),
        'captured_action': start,
        'ecosystem': eco,
        'conversion_event': conv,
        'start_date': None, 'end_date': None, 'notes': None,
    }
    sd, ed = find_window(t)
    out['start_date'], out['end_date'] = sd, ed
    out['missing'] = [k for k in ('subject', 'captured_action', 'ecosystem',
                                  'conversion_event') if not out.get(k)]
    return out


def merge_missing(primary, secondary, required):
    """Fill ONLY empty required (and known optional) fields of
    ``primary`` from ``secondary``. The model's read is never
    overwritten; ``missing`` is recomputed."""
    out = dict(primary or {})
    sec = secondary or {}
    optional = ('journey_kind', 'journey_direction', 'clip_url', 'start_behavior',
                'watch_definition', 'captured_action',
                'start_date', 'end_date',
                # brand partnership
                'pre_start', 'pre_end', 'post_start', 'post_end',
                'audience', 'campaign', 'period_hint', 'links',
                # attribution
                'end_tracking_date')
    for k in tuple(required) + optional:
        if not out.get(k) and sec.get(k):
            out[k] = sec[k]
    # A tri-state the model left unread (None) takes the rules read
    # even when that read is False ("daily tracking off").
    if out.get('daily_refresh') is None \
            and sec.get('daily_refresh') is not None:
        out['daily_refresh'] = sec['daily_refresh']
    out['missing'] = [k for k in required if not out.get(k)]
    return out


# ---------------------------------------------------------------------
# Dates: "Apr 2024 - Dec 2024", "April 1, 2024 to December 31, 2024",
# "2024-04-01 through 2024-12-31", "post through Jun 2025".
# ---------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july',
     'august', 'september', 'october', 'november', 'december'))}
for _m, _i in list(_MONTHS.items()):
    _MONTHS[_m[:3]] = _i
_MONTHS['sept'] = 9
_MON_RX = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|'
           r'jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|'
           r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)')
_DATE_RX = (r'(?:(?P<iso>\d{4}-\d{2}-\d{2})|'
            r'(?P<mon>' + _MON_RX + r')\.?\s*(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s*)?'
            r'(?P<year>(?:19|20)\d{2}))')
# hyphen, en dash (U+2013), em dash (U+2014) as typed by users
_RANGE_SEP = r'\s*(?:-|\u2013|\u2014|to|through|thru|until|till)\s*'
_DATE_RANGE = re.compile(_DATE_RX + _RANGE_SEP
                         + _DATE_RX.replace('(?P<', '(?P<b_'), re.I)
_SHORT_RANGE = re.compile(  # "Apr-Dec 2024", "Apr to Dec 2024"
    r'\b(?P<m1>' + _MON_RX + r')\.?' + _RANGE_SEP + r'(?P<m2>' + _MON_RX
    + r')\.?\s+(?P<year>(?:19|20)\d{2})\b', re.I)
_SINGLE_DATE = re.compile(_DATE_RX, re.I)


def _days_in_month(y, m):
    if m == 12:
        return 31
    import datetime as _dt
    return (_dt.date(y, m + 1, 1) - _dt.date(y, m, 1)).days


def _to_iso(iso, mon, day, year, end=False):
    if iso:
        return iso
    if not (mon and year):
        return None
    m = _MONTHS.get(mon.lower().rstrip('.'))
    if not m:
        return None
    y = int(year)
    if day:
        d = max(1, min(int(day), _days_in_month(y, m)))
    else:
        d = _days_in_month(y, m) if end else 1
    return f'{y:04d}-{m:02d}-{d:02d}'


_NUMERIC_DATE = re.compile(r'(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4}|\d{2})(?![\d/])')


def normalize_numeric_dates(text):
    """"7/14/26" and "07/14/2026" (US order) read as 2026-07-14 so every
    date rule below sees one spelling (Debi 2026-10-21: "launched the
    first promo on 7/14/26")."""
    def _sub(m):
        mm, dd, yy = int(m.group('m')), int(m.group('d')), int(m.group('y'))
        if yy < 100:
            yy += 2000
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            return m.group(0)
        try:
            import datetime as _dt
            return _dt.date(yy, mm, dd).isoformat()
        except Exception:
            return m.group(0)
    return _NUMERIC_DATE.sub(_sub, str(text or ''))


_OPEN_END_RX = re.compile(
    r'\b(?:to|through|thru|until|till)\s+(?:date|today|now|present|the\s+present)\b|\b(?:still|currently)\s+(?:running|live|on\s+air|going)\b|'
    r'\b(?:is|are)\s+(?:still\s+)?(?:running|live|ongoing)\b|\bongoing\b|\bruns?\s+for\s+(?:a|one|the\s+next)\s+(?:year|month|quarter|season)|'
    r'\bruns?\s+(?:through|until|till)\s+(?:next\s+)?(?:year|spring|summer|fall|winter|20\d\d)\b', re.I)
_START_ONLY_RX = re.compile(
    r'\b(?:launch(?:ed|es|ing)?|start(?:ed|s|ing)?|kick(?:ed|s)?\s+off|went\s+live|go(?:es)?\s+live|began|begins|beginning|premiered|debuted|dropped|'
    r'first\s+(?:promo|post|video|ad|spot|episode|drop)(?:\s+\w+){0,3}?|since|starting|as\s+of|from)\s+(?:on\s+|in\s+|back\s+in\s+)?' + _DATE_RX, re.I)


def find_date_ranges(text):
    """Every (start_iso, end_iso, span) range in the text, in order."""
    t = normalize_numeric_dates(text)
    out = []
    for m in _DATE_RANGE.finditer(t):
        s = _to_iso(m.group('iso'), m.group('mon'), m.group('day'),
                    m.group('year'))
        e = _to_iso(m.group('b_iso'), m.group('b_mon'), m.group('b_day'),
                    m.group('b_year'), end=True)
        if s and e and s <= e:
            out.append((s, e, m.span()))
    for m in _SHORT_RANGE.finditer(t):
        if any(a <= m.start() < b for _, _, (a, b) in out):
            continue
        s = _to_iso(None, m.group('m1'), None, m.group('year'))
        e = _to_iso(None, m.group('m2'), None, m.group('year'), end=True)
        if s and e and s <= e:
            out.append((s, e, m.span()))
    out.sort(key=lambda r: r[2][0])
    return out


_WINDOW_LABEL = re.compile(
    r'\b(?P<label>campaign|event|pre|post|baseline|before|after|'
    r'measurement)\b(?:\s+window)?\s*(?:was|is|ran|runs|of|:)?\s*$', re.I)


def _label_before(text, pos):
    lead = text[max(0, pos - 40):pos]
    m = _WINDOW_LABEL.search(lead)
    if not m:
        return None
    lab = m.group('label').lower()
    return {'campaign': 'event', 'event': 'event', 'measurement': 'event',
            'pre': 'pre', 'baseline': 'pre', 'before': 'pre',
            'post': 'post', 'after': 'post'}[lab]


def find_windows(text):
    """{'event_start','event_end','pre_*','post_*'} from the labelled
    ranges; the first unlabelled range is the campaign. A start with no
    end ("launched on 7/14/26 and runs for a year", "from 7/14/26 to
    date") is the campaign start; an open end reads through today."""
    t = normalize_numeric_dates(text)
    out = {}
    unlabelled = []
    for s, e, (a, b) in find_date_ranges(t):
        lab = _label_before(t, a)
        if lab and f'{lab}_start' not in out:
            out[f'{lab}_start'], out[f'{lab}_end'] = s, e
        else:
            unlabelled.append((s, e))
    if 'event_start' not in out and unlabelled:
        out['event_start'], out['event_end'] = unlabelled.pop(0)
    if 'post_start' not in out and unlabelled:
        out['post_start'], out['post_end'] = unlabelled.pop(0)
    # "post through Jun 2025": an end only.
    m = re.search(r'\bpost(?:\s+window)?\s+(?:through|thru|until|till|to)'
                  r'\s+' + _DATE_RX, t, re.I)
    if m and 'post_end' not in out:
        e = _to_iso(m.group('iso'), m.group('mon'), m.group('day'),
                    m.group('year'), end=True)
        if e:
            out['post_end'] = e
            if out.get('event_end'):
                out['post_start'] = out['event_end']
    if 'event_start' not in out:
        m = _START_ONLY_RX.search(t)
        if m:
            s0 = _to_iso(m.group('iso'), m.group('mon'), m.group('day'), m.group('year'))
            if s0:
                out['event_start'] = s0
                if _OPEN_END_RX.search(t):
                    import datetime as _dt
                    today = _dt.date.today().isoformat()
                    if s0 <= today:
                        out['event_end'] = today
    return out


# ---------------------------------------------------------------------
# Brand Partnership: brand being valued + partner (talent / show /
# event / franchise) + campaign window.
# ---------------------------------------------------------------------
_CAPS_RUN = r"[A-Z0-9][\w.&'+-]*(?:\s+[A-Z0-9][\w.&'+-]*){0,5}"
_BP_X = re.compile(
    r'(?P<a>' + _CAPS_RUN + r')'
    r'(?:\s*(?:×|\+|&)\s*|\s+(?:x|X|and|with)\s+)'
    r'(?P<b>' + _CAPS_RUN + r')')
_BP_X_STRICT = re.compile(
    r'(?P<a>' + _CAPS_RUN + r')'
    r'(?:\s*(?:×|\+)\s*|\s+(?:x|X)\s+)'
    r'(?P<b>' + _CAPS_RUN + r')')
_BP_LABELLED = {
    'brand_partner': re.compile(
        r'\b(?:brand(?:\s+being\s+valued)?|advertiser|sponsor)\s*(?:is|:|=)'
        r'\s*(?P<v>[^,.;\n]{2,60})', re.I),
    'qualifier': re.compile(
        r'\b(?:partner|talent|show|series|event|franchise|property)\s*'
        r'(?:is|:|=)\s*(?P<v>[^,.;\n]{2,60})', re.I),
    'audience': re.compile(
        r'\b(?:audience|measure(?:d)?\s+against|against)\s*(?:is|:|=|of)?'
        r'\s*(?P<v>[^,.;\n]{3,60})', re.I),
}
# a name that may carry a lowercase joiner inside it ("Colin and Samir",
# "Rhett & Link", "Dude Perfect"); used only where a verb phrase, not the
# joiner, separates the two parties
_DUO_RUN = r"[A-Z0-9][\w.&'+-]*(?:\s+(?:(?:and|&|of|the)\s+)?[A-Z0-9][\w.&'+-]*){0,5}"
_BP_PARTNERING = re.compile(
    r'(?P<a>' + _DUO_RUN + r')'
    r"(?i:(?:\s+(?:is|are|was|were|will\s+be|has|have|had|to|should|could|would|might|may|wants?\s+to|plans?\s+to|decided\s+to|agreed\s+to|officially|now|just|recently))*"
    r"\s+(?:partner(?:ing|ed|s)?|team(?:ing|ed|s)?\s+up|collab(?:orat(?:e|ing|ed|es))?|sign(?:ing|ed|s)?(?:\s+a\s+deal)?|tie\s+up)\s+with\s+)"
    r'(?P<b>' + _DUO_RUN + r')')
_BP_DEAL = re.compile(
    r'(?P<p>' + _DUO_RUN + r')'
    r"(?i:\s+(?:have|has|had|signed|landed|got|announced|struck|inked|closed|ran|run|did|launched|fronted|shot|filmed|started)\s+(?:a|an|the|their)\s+(?:new\s+)?)"
    r'(?P<b>' + _DUO_RUN + r')'
    r"(?i:\s+(?:deal|sponsorship|partnership|campaign|integration|collab(?:oration)?)\b)")
# "Lexus signed Colin and Samir", "Wendy's tapped Rhett and Link": the one
# doing the signing is the brand, the people signed are the talent
_BP_SIGNED = re.compile(
    r'(?P<b>' + _DUO_RUN + r')'
    r"(?i:\s+(?:has|have|had|just|recently|officially|reportedly)?\s*(?:signed|hired|tapped|booked|enlisted|recruited|brought\s+on|brought\s+in|named)\s+)"
    r'(?P<p>' + _DUO_RUN + r')'
    r"(?i:\s+(?:for|as|to\s+front|to\s+lead|to\s+star|to\s+promote|in)\b)")
# "Wendy's is a good fit with Rhett and Link", "is Mastercard the right partner for MrBeast"
_BP_FIT = re.compile(
    r'(?P<a>' + _DUO_RUN + r')'
    r"(?i:\s+(?:is|are|would\s+be|be|as|makes?|seems?(?:\s+like)?|looks?\s+like)?\s*(?:a|an|the)?\s*(?:good|great|strong|right|natural|smart|perfect|bad|poor|wrong|odd)?\s*(?:fit|match|partner|pairing)\s+(?:with|for)\s+)"
    r'(?P<b>' + _DUO_RUN + r')')
_BP_DUO_BRAND = re.compile(
    r"\b(?:the|this|their)\s+(?P<p>[A-Z][\w.'+-]*\s+(?:and|&)\s+[A-Z][\w.'+-]*)\s+(?P<b>[A-Z][\w.&'+-]*(?:\s+[A-Z][\w.&'+-]*){0,2})"
    r"\s+(?:campaign|deal|partnership|sponsorship|collab(?:oration)?|integration|run|flight)\b")
_BP_PREP = re.compile(
    r'\b(?:valu(?:e|ation\s+(?:of|for)|ing)|measure|price|partnership\s+(?:of|for))'
    r'\s+(?:the\s+)?(?P<brand>' + _CAPS_RUN + r')'
    r'(?:\s+(?:partnership|deal|sponsorship|campaign|integration))?'
    r'\s+(?P<conn>with|on|and|x|×)\s+(?P<partner>' + _DUO_RUN + r')')   # the partner may be a duo ("with Colin and Samir")
_BP_NOISE = {'brand', 'partnership', 'valuation', 'campaign', 'window',
             'pull', 'run', 'the', 'a', 'an', 'value', 'please', 'post',
             'pre', 'through', 'optional', 'audience'}


def _bp_clean(v):
    v = _clean(v)
    v = re.sub(r'^(?:the|a|an)\s+', '', v, flags=re.I)
    v = re.sub(r'\s*(?:,|;|\(|-)\s*$', '', v).strip()
    return v or None


def _looks_like_people(v):
    """A duo ("Colin and Samir", "Rhett & Link") or an honorific name
    ("Mr. Beast", "Dr. Mike") reads as talent, never as the brand."""
    t = ' '.join(str(v or '').split())
    return bool(re.match(r"^[A-Z][\w'.-]+\s+(?:and|&)\s+[A-Z][\w'.-]+$", t) or re.match(r"^(?:Mr|Mrs|Ms|Dr|Sir)\.?\s+[A-Z]", t))


_BRAND_SUFFIX_RX = re.compile(
    r"\b(?:inc|llc|co|corp|corporation|company|group|brands?|motors?|trucks?|bank|financial|airlines?|airways|foods?|beverages?|stores?|markets?|"
    r"pharmacy|insurance|wireless|mobile|energy|hotels?|resorts?|casinos?|cosmetics|beauty|apparel|outfitters|supply|hardware|depot|mart|express)\.?$", re.I)


def _looks_like_brand(v):
    """A possessive trade name ("Lowe's", "Wendy's", "Dick's") or a company
    suffix ("RAM Trucks", "Capital One Bank") reads as the brand side."""
    t = ' '.join(str(v or '').split())
    return bool(re.search(r"[\w]['\u2019]s$", t) or _BRAND_SUFFIX_RX.search(t))


def _bp_is_name(v):
    if not v:
        return False
    toks = re.sub(r'[^a-z0-9 ]+', ' ', v.lower()).split()
    if not toks or all(w in _BP_NOISE for w in toks):
        return False
    if re.search(r'\d{4}', v) or v.lower() in _PLATFORM_NAMES_LOW:
        return False
    return True


_URL_ANY = re.compile(r'https?://[^\s,;<>"\')\]]+')
_BP_CAMPAIGN = re.compile(
    r"(?:['\"](?P<v>[^'\"\n]{3,60})['\"]\s+(?:initiative|initiate|campaign|program|programme|push|activation|flight|platform)\b)"
    r"|(?:\bthe\s+(?P<v2>[A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,4})\s+(?:initiative|campaign|program|programme|push|activation|flight)\b)",
    re.I)
_SEASONS = {'spring': (3, 1, 5, 31), 'summer': (6, 1, 8, 31), 'fall': (9, 1, 11, 30), 'autumn': (9, 1, 11, 30), 'winter': (12, 1, 2, 28),
            'holiday': (11, 1, 12, 31), 'holidays': (11, 1, 12, 31)}
_PERIOD_HINT = re.compile(
    r"\b(?:last|this\s+past|the\s+past|this|past)\s+(?:year|month|quarter|spring|summer|fall|autumn|winter|holiday\s+season|holidays|nfl\s+season|season)\b"
    r"|\b(?:in|during|over|across)\s+(?:the\s+)?(?:(?:spring|summer|fall|autumn|winter|holiday\s+season)\s+(?:of\s+)?)?(?:20\d\d)\b"
    r"|\bq[1-4]\s*(?:of\s*)?20\d\d\b|\b20\d\d\s*q[1-4]\b"
    r"|\b(?:the\s+)?20\d\d(?:[-/]\d\d)?\s+(?:nfl|nba|mlb|nhl|college\s+football|holiday)\s+season\b",
    re.I)


def _period_from_hint(hint, today=None):
    """A relative period ("last year", "this past fall", "Q4 2025",
    "in 2025", "the 2025 NFL season") as (start, end) ISO dates, clipped
    to today. None when the words do not name a period."""
    import datetime as _dt
    import calendar as _cal
    today = today or _dt.date.today()
    h = ' '.join(str(hint or '').lower().split())
    if not h:
        return None

    def _clip(a, b):
        if b > today:
            b = today
        return (a.isoformat(), b.isoformat()) if a <= b else None
    m = re.search(r'q([1-4])\s*(?:of\s*)?(20\d\d)|(20\d\d)\s*q([1-4])', h)
    if m:
        q = int(m.group(1) or m.group(4)); y = int(m.group(2) or m.group(3))
        a = _dt.date(y, 3 * (q - 1) + 1, 1); em = 3 * q
        return _clip(a, _dt.date(y, em, _cal.monthrange(y, em)[1]))
    m = re.search(r'(20\d\d)(?:[-/](\d\d))?\s+(nfl|college football)\s+season', h)
    if m:
        y = int(m.group(1))
        return _clip(_dt.date(y, 9, 1), _dt.date(y + 1, 2, 15))
    m = re.search(r'(20\d\d)\s+(nba|nhl)\s+season', h)
    if m:
        y = int(m.group(1))
        return _clip(_dt.date(y, 10, 1), _dt.date(y + 1, 6, 30))
    m = re.search(r'(20\d\d)\s+mlb\s+season', h)
    if m:
        y = int(m.group(1))
        return _clip(_dt.date(y, 3, 20), _dt.date(y, 11, 5))
    m = re.search(r'(spring|summer|fall|autumn|winter|holiday season)\s+(?:of\s+)?(20\d\d)', h)
    if m:
        season, y = m.group(1).split()[0], int(m.group(2))
        sm, sd, em, ed = _SEASONS[season]
        ey = y + 1 if season == 'winter' else y
        return _clip(_dt.date(y, sm, sd), _dt.date(ey, em, min(ed, _cal.monthrange(ey, em)[1])))
    m = re.search(r'\b(20\d\d)\b', h)
    if m and not re.search(r'last|this|past', h):
        y = int(m.group(1))
        return _clip(_dt.date(y, 1, 1), _dt.date(y, 12, 31))
    last = bool(re.search(r'\b(?:last|this past|the past|past)\b', h))
    if 'year' in h:
        y = today.year - 1 if last else today.year
        return _clip(_dt.date(y, 1, 1), _dt.date(y, 12, 31))
    if 'quarter' in h:
        q = (today.month - 1) // 3 + 1
        y = today.year
        if last:
            q -= 1
            if q == 0:
                q, y = 4, y - 1
        a = _dt.date(y, 3 * (q - 1) + 1, 1); em = 3 * q
        return _clip(a, _dt.date(y, em, _cal.monthrange(y, em)[1]))
    if 'month' in h:
        y, mo = today.year, today.month
        if last:
            mo -= 1
            if mo == 0:
                mo, y = 12, y - 1
        return _clip(_dt.date(y, mo, 1), _dt.date(y, mo, _cal.monthrange(y, mo)[1]))
    if 'nfl season' in h or ('season' in h and 'holiday' not in h):
        # the most recent season that has started: Sept to mid-Feb
        y = today.year if today.month >= 9 else today.year - 1
        if last and today.month >= 9:
            y -= 1
        return _clip(_dt.date(y, 9, 1), _dt.date(y + 1, 2, 15))
    for season, (sm, sd, em, ed) in _SEASONS.items():
        if season in h:
            y = today.year
            start_this = _dt.date(y, sm, sd)
            if last or start_this > today:
                y -= 1
            ey = y + 1 if season == 'winter' else y
            return _clip(_dt.date(y, sm, sd), _dt.date(ey, em, min(ed, _cal.monthrange(ey, em)[1])))
    return None


def _youtube_publish_date(url, timeout=8):
    """The publish date of a public YouTube video or Short, or None.
    One GET, fail-safe, no API key."""
    try:
        if not re.search(r'(?:youtube\.com/(?:watch|shorts/)|youtu\.be/)', str(url or ''), re.I):
            return None
        import urllib.request
        req = urllib.request.Request(str(url), headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read(1_500_000).decode('utf-8', 'ignore')
        m = re.search(r'"(?:publishDate|uploadDate)":"(\d{4}-\d{2}-\d{2})', html)
        return m.group(1) if m else None
    except Exception:
        return None


def link_dates(links, fetch=_youtube_publish_date):
    """ISO dates of the campaign assets the links point at (public
    YouTube videos today; other hosts need a login and return nothing)."""
    out = []
    for u in list(links or [])[:8]:
        d = fetch(u)
        if d:
            out.append(d)
    return sorted(set(out))


def _pretty(iso):
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(iso).strftime('%b %-d, %Y')
    except Exception:
        return str(iso)


def resolve_campaign_window(parsed, researcher=None, today=None, fetch=_youtube_publish_date):
    """The campaign window an explicit ask implies when it gives no
    dates: the brand's named initiative (researched when a researcher
    is supplied), the creator's own posts (dated from the links), and
    a relative period ("last year"). Returns (start, end, display) or
    None. A creator partnership inside a brand flight runs from the
    first creator post through the end of the flight.

    researcher(brand, campaign, qualifier) -> {'start','end','basis'} or None.
    """
    import datetime as _dt
    p = parsed or {}
    today = today or _dt.date.today()
    links = [str(u) for u in (p.get('links') or []) if u]
    campaign = str(p.get('campaign') or '').strip()
    brand = str(p.get('brand_partner') or '').strip()
    who = str(p.get('qualifier') or 'the partner').strip()
    if not (links or campaign or p.get('period_hint')):
        return None
    posts = link_dates(links, fetch=fetch) if links else []
    flight = None
    if campaign and brand and researcher:
        try:
            r = researcher(brand, campaign, who) or {}
            if isinstance(r, dict) and r.get('start'):
                flight = {'start': str(r['start'])[:10], 'end': str(r.get('end') or '')[:10] or None,
                          'basis': str(r.get('basis') or '')}
        except Exception:
            flight = None
    period = _period_from_hint(p.get('period_hint'), today=today) if p.get('period_hint') else None
    start = end = None
    start_src = end_src = ''
    flight_label = f"the {campaign} flight" + (f" at {brand}" if brand else '')   # no possessive on a brand name (Lowe's, RAM Trucks)
    if posts:
        start, start_src = posts[0], f"{who}'s first post"
    if flight:
        if not start:
            start, start_src = flight['start'], flight_label
        if flight.get('end'):
            end, end_src = flight['end'], flight_label
    if period:
        if not start:
            start, start_src = period[0], str(p.get('period_hint'))
        if not end:
            end, end_src = period[1], str(p.get('period_hint'))
    if start and not end:
        # a dated creator post with no flight end: the post plus the
        # thirty days a partnership read measures
        end, end_src = (_dt.date.fromisoformat(start) + _dt.timedelta(days=30)).isoformat(), '30 days after it'
    if not (start and end):
        return None
    if end > today.isoformat():
        end = today.isoformat()
    if start > end:
        start = end
    if posts and end < posts[-1]:
        end = posts[-1]
    if start_src == end_src:
        basis = start_src
    elif end_src == flight_label:
        basis = f"{start_src} through the end of {flight_label}"
    else:
        basis = f"{start_src} through {end_src}"
    display = f"{_pretty(start)} to {_pretty(end)}" + (f" ({basis})" if basis else '')
    return start, end, display


def keyword_parse_brand_partnership(text):
    """Brand Partnership brief -> brand_partner / qualifier / event
    window (+ pre / post / audience when stated), best effort."""
    t = str(text or '').replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
    out = {'brand_partner': None, 'qualifier': None,
           'event_start': None, 'event_end': None,
           'pre_start': None, 'pre_end': None,
           'post_start': None, 'post_end': None, 'audience': None,
           'notes': None, 'campaign': None, 'period_hint': None, 'links': []}
    out.update({k: v for k, v in find_windows(t).items()})
    # The campaign context an explicit ask carries (MMG x Lowe's, Jenna
    # 2026-10-16): every link, the named initiative, and a relative
    # period given instead of dates. The window resolver turns these
    # into a proposed campaign window instead of a stall.
    out['links'] = [m.group(0).rstrip('.,;)') for m in _URL_ANY.finditer(t)]
    m = _BP_CAMPAIGN.search(t)
    if m:
        out['campaign'] = _clean(m.group('v') or m.group('v2'))
    m = _PERIOD_HINT.search(t)
    if m:
        out['period_hint'] = _clean(m.group(0))
    for k, rx in _BP_LABELLED.items():
        m = rx.search(t)
        if m:
            v = _bp_clean(m.group('v'))
            if v and (k == 'audience' or _bp_is_name(v)):
                out[k] = v
    # Strip dates, links and parentheticals so the pair readers do not
    # swallow a window or a platform list: "MMG (YouTube, IG, TikTok) x
    # Lowe's" reads MMG and Lowe's.
    bare = _DATE_RANGE.sub(' ', normalize_numeric_dates(t))
    bare = _SHORT_RANGE.sub(' ', bare)
    bare = _SINGLE_DATE.sub(' ', bare)
    bare = _URL_ANY.sub(' ', bare)
    bare = re.sub(r'\s*\([^)]*\)', ' ', bare)
    bare = re.sub(r'\s*\n+\s*', ' . ', bare)   # a line break ends a name
    if not (out['brand_partner'] and out['qualifier']):
        m = _BP_PREP.search(bare)
        if m:
            b, p = _bp_clean(m.group('brand')), _bp_clean(m.group('partner'))
            if m.group('conn') in ('x', '×'):   # "value the Glen Powell x RAM Trucks partnership": partner x brand
                b, p = p, b
            if _bp_is_name(b) and _bp_is_name(p):
                out['brand_partner'] = out['brand_partner'] or b
                out['qualifier'] = out['qualifier'] or p
    if not (out['brand_partner'] and out['qualifier']):
        # the explicit "Glen Powell x RAM Trucks" form first (partner x brand);
        # a plain "and" / "with" between two names is read last, after the
        # partnering verbs, so "Colin and Samir have a Lexus deal" is a duo
        # and a brand, not two halves (Debi 2026-10-26)
        for m in _BP_X_STRICT.finditer(bare):
            a, b = _bp_clean(m.group('a')), _bp_clean(m.group('b'))
            if not (_bp_is_name(a) and _bp_is_name(b)):
                continue
            out['qualifier'] = out['qualifier'] or a
            out['brand_partner'] = out['brand_partner'] or b
            break
    if not (out['brand_partner'] and out['qualifier']):
        # "Mastercard is partnering with Mr. Beast" / "Mastercard to partner
        # with Mr. Beast": the one partnering is the brand, the one partnered
        # with is the talent; "Colin and Samir have a Lexus deal": the duo
        # is the talent and the named deal is the brand (Debi 2026-10-26)
        m = _BP_PARTNERING.search(bare)
        if m:
            b, p = _bp_clean(m.group('a')), _bp_clean(m.group('b'))
            if (_looks_like_people(b) and not _looks_like_people(p)) or (_looks_like_brand(p) and not _looks_like_brand(b)):
                b, p = p, b      # "Rhett and Link are teaming up with Wendy's", "MMG partnered with Lowe's": the trade name is the brand
            if _bp_is_name(b) and _bp_is_name(p):
                out['brand_partner'], out['qualifier'] = b, p
        if not (out['brand_partner'] and out['qualifier']):
            # "the Colin and Samir Lexus campaign": a duo, then the brand, then the noun
            m = _BP_DUO_BRAND.search(bare)
            if m:
                p, b = _bp_clean(m.group('p')), _bp_clean(m.group('b'))
                if _bp_is_name(b) and _bp_is_name(p):
                    out['brand_partner'], out['qualifier'] = b, p
        if not (out['brand_partner'] and out['qualifier']):
            m = _BP_DEAL.search(bare)
            if m:
                p, b = _bp_clean(m.group('p')), _bp_clean(m.group('b'))
                if _bp_is_name(b) and _bp_is_name(p):
                    out['brand_partner'], out['qualifier'] = b, p
        if not (out['brand_partner'] and out['qualifier']):
            m = _BP_SIGNED.search(bare)
            if m:
                b, p = _bp_clean(m.group('b')), _bp_clean(m.group('p'))
                if _looks_like_people(b) and not _looks_like_people(p):
                    b, p = p, b
                if _bp_is_name(b) and _bp_is_name(p):
                    out['brand_partner'], out['qualifier'] = b, p
        if not (out['brand_partner'] and out['qualifier']):
            m = _BP_FIT.search(bare)
            if m:
                b, p = _bp_clean(m.group('a')), _bp_clean(m.group('b'))
                if _looks_like_people(b) and not _looks_like_people(p):
                    b, p = p, b
                if _bp_is_name(b) and _bp_is_name(p):
                    out['brand_partner'], out['qualifier'] = b, p
    if not (out['brand_partner'] and out['qualifier']):
        for m in _BP_X.finditer(bare):
            a, b = _bp_clean(m.group('a')), _bp_clean(m.group('b'))
            if not (_bp_is_name(a) and _bp_is_name(b)):
                continue
            # The ask copy's example is "Glen Powell x RAM Trucks":
            # partner x brand.
            out['qualifier'] = out['qualifier'] or a
            out['brand_partner'] = out['brand_partner'] or b
            break
    # "the colin and samir campaign" names the partner, not an initiative
    if out.get('campaign') and out.get('qualifier') and out.get('brand_partner'):
        _cn = re.sub(r'[^a-z0-9]+', ' ', str(out['campaign']).lower()).strip()
        _names = {re.sub(r'[^a-z0-9]+', ' ', str(v).lower()).strip() for v in (out['qualifier'], out['brand_partner'])}
        _names.add(' '.join(sorted(_names)))
        if _cn in _names or _cn in {f"{a} {b}" for a in _names for b in _names if a != b}:
            out['campaign'] = None
    out['missing'] = [k for k in ('brand_partner', 'qualifier',
                                  'event_start', 'event_end')
                      if not out.get(k)]
    return out


_PROSPECTIVE_RX = re.compile(
    r"\b(?:why\s+(?:it\s+)?(?:makes?|would\s+make|does)\s+sense|makes?\s+sense\s+(?:for|to)|good\s+(?:idea|fit|match)|right\s+(?:fit|partner|creator)|"
    r"should\s+\w+\s+partner|is\s+(?:partnering|teaming\s+up|about\s+to)|(?:potential|prospective|possible|proposed|upcoming)\s+(?:partner|deal|partnership|campaign|creators?)|"
    r"over\s+(?:other|potential|the\s+other)\s+(?:creators?|talent|partners?|options)|case\s+for|rationale|whether\s+to\s+partner)\b", re.I)


def is_prospective_partnership(text):
    """The ask is about a partnership being weighed, not a campaign that
    ran: no dates to measure yet (Debi 2026-10-26, Mastercard x MrBeast)."""
    return bool(_PROSPECTIVE_RX.search(' '.join(str(text or '').split())))


def propose_brand_partnership_field(parsed, researcher=None):
    """A campaign with a start and no end is running: propose today as
    the end. A brand and a partner with no dates but a named initiative,
    campaign links or a relative period ("last year") get the window
    those imply (resolve_campaign_window), proposed on the confirm card.
    A prospective partnership (why it makes sense, is partnering with)
    gets the last 12 months as the fit window. Never proposes the brand
    or the partner."""
    p = parsed or {}
    ask = str(p.pop('_ask', '') or '')
    required = ('brand_partner', 'qualifier', 'event_start', 'event_end')
    missing = [k for k in required if not p.get(k)]
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if missing == ['event_end']:
        if str(p.get('event_start')) > today:
            return None
        return 'event_end', today, f'through today ({today})'
    if missing == ['event_start', 'event_end']:
        win = resolve_campaign_window(p, researcher=researcher)
        if win:
            return 'event_window', {'event_start': win[0], 'event_end': win[1]}, win[2]
        if ask and is_prospective_partnership(ask):
            t = _dt.date.today()
            start = t.replace(year=t.year - 1)
            return ('event_window', {'event_start': start.isoformat(), 'event_end': t.isoformat()},
                    f"{_pretty(start.isoformat())} to {_pretty(t.isoformat())} (no campaign dates yet, so the fit reads on the last 12 months)")
    return None


# ---------------------------------------------------------------------
# Attribution: campaign name + tagged URLs + conversion + daily
# tracking on/off (+ stop date when on).
# ---------------------------------------------------------------------
_URL_LINE = re.compile(
    r'(?P<url>https?://[^\s,;]+)'
    r'(?:[\s,;|]+(?P<tag>paid|organic)\b)?'
    r'(?:[\s,;|]+(?P<label>[^\n]{1,80}))?', re.I)
_AIQ_NAME = re.compile(
    r'(?:\bcampaign(?:\s+name)?\s*(?:is|:|=|called|named)\s*'
    r'(?P<v1>[^,.;\n]{2,80}))|'
    r'(?:\bthe\s+(?P<v2>[^,.;\n]{2,60}?)\s+campaign\b)|'
    r'(?:^\s*(?P<v3>[^,.;\n]{2,60}?)\s+campaign\b)', re.I | re.M)
_AIQ_CONV = re.compile(
    r'\bconversion(?:\s+event)?\s*(?:is|:|=|counts?\s+as)\s*'
    r'(?P<v>[^\n]{3,160}?)(?:\.\s|\.$|\n|$)', re.I)
_AIQ_CONV_VERB = re.compile(
    r'\b(?:what\s+counts|counts?\s+as\s+a\s+conversion|a\s+conversion\s+is)'
    r'\s*(?:is|:)?\s*(?P<v>[^\n]{3,160}?)(?:\.\s|\.$|\n|$)', re.I)
_AIQ_DAILY_ON = re.compile(
    r'\bdaily(?:\s+tracking|\s+refresh(?:es)?)?\s*(?:is\s+)?(?:on|yes|'
    r'enabled|through|thru|until|till|to)\b|\btrack(?:ing)?\s+(?:it\s+)?'
    r'daily\b|\bevery\s+(?:day|morning)\b', re.I)
_AIQ_DAILY_OFF = re.compile(
    r'\bdaily(?:\s+tracking|\s+refresh(?:es)?)?\s*(?:is\s+)?(?:off|no)\b|'
    r'\bno\s+daily\b|\bone[- ]time\b|\bjust\s+once\b|\bsingle\s+read\b|'
    r'\bone\s+read\b', re.I)
_AIQ_STOP = re.compile(
    r'\b(?:through|thru|until|till|stop(?:s|ping)?\s+(?:on\s+)?|'
    r'ends?\s+(?:on\s+)?|to)\s+' + _DATE_RX, re.I)


def parse_tagged_urls(text):
    """[{'url','tag','label'}] from the message lines; tag is the
    user's own word (paid / organic) or None when absent."""
    out, seen = [], set()
    for line in str(text or '').splitlines():
        for m in _URL_LINE.finditer(line):
            url = m.group('url').rstrip('.,;)')
            if url in seen:
                continue
            seen.add(url)
            tag = (m.group('tag') or '').lower() or None
            label = _clean(m.group('label') or '') or None
            if label and tag is None:
                # "url Hero spot paid" - tag after the label
                mt = re.search(r'\b(paid|organic)\b\s*$', label, re.I)
                if mt:
                    tag = mt.group(1).lower()
                    label = _clean(label[:mt.start()]) or None
            out.append({'url': url, 'tag': tag, 'label': label})
    return out


def keyword_parse_attribution(text):
    t = str(text or '')
    out = {'campaign_name': None, 'urls': [], 'conversion_event': None,
           'daily_refresh': None, 'end_tracking_date': None,
           'notes': None}
    m = _AIQ_NAME.search(t)
    if m:
        v = _clean(m.group('v1') or m.group('v2') or m.group('v3') or '')
        v = re.sub(r'^(?:the|a|an)\s+', '', v, flags=re.I).strip(' "\'')
        if v and not v.lower().startswith('http'):
            out['campaign_name'] = v
    urls = parse_tagged_urls(t)
    if urls:
        out['urls'] = urls
    m = _AIQ_CONV.search(t) or _AIQ_CONV_VERB.search(t)
    if m:
        v = _clean(m.group('v')).strip(' "\'')
        if v:
            out['conversion_event'] = v
    if _AIQ_DAILY_OFF.search(t):
        out['daily_refresh'] = False
    elif _AIQ_DAILY_ON.search(t):
        out['daily_refresh'] = True
        ms = _AIQ_STOP.search(t)
        if ms:
            out['end_tracking_date'] = _to_iso(
                ms.group('iso'), ms.group('mon'), ms.group('day'),
                ms.group('year'), end=True)
    out['missing'] = [k for k in ('campaign_name', 'urls',
                                  'conversion_event') if not out.get(k)]
    return out


def propose_attribution_field(parsed):
    """Name, tagged URLs and conversion in hand, daily tracking never
    mentioned: propose a one-time read (the no-charge default). Never
    proposes a stop date (that is money) or a tag (that is the user's
    own word)."""
    p = parsed or {}
    urls = [u for u in (p.get('urls') or [])
            if isinstance(u, dict) and u.get('url')
            and str(u.get('tag') or '').lower() in ('paid', 'organic')]
    if not (p.get('campaign_name') and urls and p.get('conversion_event')):
        return None
    if p.get('daily_refresh') is None:
        return 'daily_refresh', False, 'off (a one-time read, no daily tracking)'
    return None


# ---------------------------------------------------------------------
# Confirm instead of interrogate: propose the one missing field.
# ---------------------------------------------------------------------
_DEFAULT_STEP = {
    'video': ('watch', 'watched an episode of {subject} on {platform}'),
    'audio': ('watch', 'streamed {subject} on {platform}'),
    'commerce': ('purchase', 'paid for {subject} on {platform}'),
    'ticketing': ('purchase', 'bought a ticket for {subject} on {platform}'),
    'games': ('purchase', 'bought or installed {subject} on {platform}'),
    'apps': ('purchase', 'installed {subject} from the {platform}'),
    'social': (None, None),
}

FIELD_LABELS = {
    'subject': 'the title', 'platform': 'the platform',
    'conversion_event': 'the end step', 'captured_action':
    'the starting point', 'ecosystem': 'the ecosystem',
    'brand_partner': 'the brand', 'qualifier': 'the partner',
    'event_start': 'the campaign start', 'event_end': 'the campaign end',
    'event_window': 'the campaign window',
    'campaign_name': 'the campaign name', 'urls': 'the campaign URLs',
    'daily_refresh': 'daily tracking',
    'end_tracking_date': 'the stop date for daily tracking',
    'journey_direction': 'which way the journey runs (how they came to watch, or what watchers do after and in addition)',
}


def propose_journey_field(parsed):
    """When exactly one of subject / platform / conversion_event is
    missing and the other two make the answer obvious, return
    (field, value). Otherwise None. Never proposes a subject."""
    p = parsed or {}
    if str(p.get('journey_kind') or '').lower() == 'media_week' or direction_unclear(p):
        return None   # a week-in-the-life read has no end step to propose; an unclear direction is asked, not guessed
    required = ('subject', 'platform', 'conversion_event')
    missing = [k for k in required if not p.get(k)]
    if len(missing) != 1:
        return None
    field = missing[0]
    if field == 'conversion_event':
        plat = str(p.get('platform') or '')
        pkind = _PLATFORM_KIND.get(plat)
        if not pkind:
            pkind = 'video' if p.get('journey_kind') == 'watch' else None
        if not pkind or pkind == 'social':
            return None
        jk, tmpl = _DEFAULT_STEP[pkind]
        if p.get('journey_kind') == 'watch' and pkind != 'video':
            tmpl = 'watched {subject} on {platform}'
        value = tmpl.format(subject=p.get('subject'), platform=plat)
        return field, value
    if field == 'platform':
        plats = find_platforms(' '.join(str(p.get(k) or '') for k in (
            'conversion_event', 'start_behavior', 'notes')))
        kind = p.get('journey_kind') or _kind_of(p.get('conversion_event'))
        choice = _primary_platform(plats, kind, p.get('conversion_event'))
        if choice:
            return field, choice
        if kind == 'watch' and p.get('subject'):
            return None
    return None


def propose_flywheel_field(parsed):
    p = parsed or {}
    required = ('subject', 'captured_action', 'ecosystem',
                'conversion_event')
    missing = [k for k in required if not p.get(k)]
    if len(missing) != 1:
        return None
    field = missing[0]
    if field == 'conversion_event':
        eco = str(p.get('ecosystem') or '')
        pkind = _PLATFORM_KIND.get(eco)
        if not pkind or pkind == 'social':
            return None
        _jk, tmpl = _DEFAULT_STEP[pkind]
        return field, tmpl.format(subject=p.get('subject'), platform=eco)
    if field == 'ecosystem':
        plats = find_platforms(' '.join(str(p.get(k) or '') for k in (
            'conversion_event', 'captured_action', 'notes')))
        choice = _primary_platform(plats, _kind_of(p.get('conversion_event')),
                                   p.get('conversion_event'))
        if choice:
            return field, choice
    return None


SUBJECT_FIELDS = ('subject', 'talent', 'brand_partner', 'qualifier', 'campaign_name')


def names_subject(parsed):
    """Whether a first read named who or what the ask is about."""
    return any(str((parsed or {}).get(k) or '').strip() for k in SUBJECT_FIELDS)


def first_read(text, parse_fn, fallback_fn=None, usage_extras=None):
    """The first read of an intake message: the model's parse, and when
    that names nothing, the rules reader's (MMG x Lowe's, 2026-10-16: a
    model outage must not turn an explicit ask into the page question).
    Short messages (under four words) are not parsed. Never raises."""
    pre = {}
    try:
        pre = (parse_fn(text, usage_extras=usage_extras) if len(str(text or '').split()) >= 4 else {}) or {}
    except Exception:
        pre = {}
    if not names_subject(pre) and fallback_fn:
        try:
            pre = dict(fallback_fn(text) or {}, **{k: v for k, v in (pre or {}).items() if v})
        except Exception:
            pass
    return pre if isinstance(pre, dict) else {}


def apply_proposal(parsed, prop, keep_existing=False):
    """Set a proposal's field (or fields, when the value is a dict such as
    a campaign window) on ``parsed``; returns the keys it set so a caller
    can pop them when the brief still does not complete."""
    field, value = prop[0], prop[1]
    display = prop[2] if len(prop) > 2 else value
    if isinstance(value, dict):
        parsed.update(value)
        keys = list(value)
    else:
        parsed[field] = value
        keys = [field]
    if not (keep_existing and parsed.get('_proposed')):
        parsed['_proposed'] = {'field': field, 'value': display}
    return keys


def proposed_note(field, value, run_label='Run the journey', source=''):
    label = FIELD_LABELS.get(field, field.replace('_', ' '))
    if source:   # the shape came from a prior deliverable the ask pointed at (2026-10-13)
        return (f"\n\nI took {label} from your {source} journey: \"{value}\". "
                f"Say '{run_label}' if that is right, or send the correction and I will update it.")
    return (f"\n\nYou did not spell out {label}, so I took it as "
            f"\"{value}\". Say '{run_label}' if that is right, or send "
            f"the correction and I will update it.")


# ---------------------------------------------------------------------------
# "Just like the one you did for X" (2026-10-13, Carolyn Bisson / WBD)
# ---------------------------------------------------------------------------
# "Build a digital journey for Anthony Bourdain No Reservations on Amazon
# Prime just like the one you did for Babylon 5" names the title, the
# platform and the shape in one line. The shape is the prior journey:
# its start step and its end step, re-pointed at the new title and
# platform. Nothing is asked that the sentence already answered.
_TEMPLATE_RXS = (
    re.compile(r"\b(?:just\s+)?(?:like|same\s+as|similar\s+to|mirroring|modeled\s+on|copy(?:ing)?)\s+"
               r"(?:the\s+)?(?:one|journey|read|version|build)\s+(?:you|we|I)\s+"
               r"(?:did|built|made|ran|got|had|created|produced)\s+(?:for|on|with)\s+"
               r"(?P<title>[^.?!,;]+?)\s*(?:[.?!,;]|$)", re.I),
    re.compile(r"\b(?:just\s+)?(?:like|same\s+as|similar\s+to)\s+(?:the|your|our|my)\s+"
               r"(?P<title>[^.?!,;]+?)\s+(?:journey|one|read|version|build)\b", re.I),
    # "the same analysis you did for Babylon 5 for No Reservations" (golden g095, 2026-10-14)
    re.compile(r"\b(?:the\s+)?same\s+(?:analysis|read|thing|journey|report|numbers|breakdown|look|view|study)\s+(?:that\s+)?(?:you|we|I)\s+"
               r"(?:did|built|made|ran|gave|produced)\s+(?:for|on)\s+(?P<title>[^.?!,;]+?)(?=\s+(?:for|on|but for|against)\s+|\s*(?:[.?!,;]|$))", re.I),
)
_TEMPLATE_RXS = _TEMPLATE_RXS + (
    # "Same structure as the Babylon 5 journey" (Carolyn's paste text, 2026-10-14)
    re.compile(r"\b(?:the\s+)?same\s+(?:structure|format|shape|layout|template|depth|tables|breakdown)\s+as\s+"
               r"(?:the|your|our|my)?\s*(?P<title>[^.?!,;]+?)\s+(?:journey|one|read|version|build|file|report)\b", re.I),
    re.compile(r"\b(?:the\s+)?same\s+(?:structure|format|shape|layout|template|depth|tables|breakdown)\s+as\s+"
               r"(?:the|your|our|my)?\s*(?P<title>[^.?!,;]+?)\s*(?:[.?!,;]|$)", re.I),
)
_TEMPLATE_TAIL_RX = re.compile(r"\s+(?:journey|one|read|version|build|file|report)$", re.I)


def find_template_reference(text):
    """The prior deliverable an ask points at ("just like the one you
    did for Babylon 5" -> "Babylon 5"), or ''."""
    t = ' '.join(str(text or '').split())
    for rx in _TEMPLATE_RXS:
        m = rx.search(t)
        if m:
            title = _TEMPLATE_TAIL_RX.sub('', m.group('title').strip(' "\'')).strip()
            if 2 <= len(title) <= 80:
                return title
    return ''


def reading_precedent(body):
    """The precedent the Reader flagged ('precedent:Babylon 5'), or ''."""
    try:
        r = (body or {}).get('reading') if isinstance(body, dict) else None
        for f in (r or {}).get('flags') or []:
            f = str(f or '')
            if f.lower().startswith('precedent:') and 2 <= len(f) - 10 <= 80:
                return f[10:].strip()
    except Exception:
        pass
    return ''


def strip_template_reference(text):
    """The ask without its template clause, so the subject reader does
    not take the referenced title for the subject."""
    t = ' '.join(str(text or '').split())
    for rx in _TEMPLATE_RXS:
        t = rx.sub(' ', t)
    t = re.sub(r"\s*\.(?:\s*\.)+", ".", t)
    return ' '.join(t.split()).strip(' ,;')


_STAGE_SKIP_RX = re.compile(r"^(?:us\s+gen\s+pop|gen\s+pop|everyone|us\s+population)$", re.I)


def journey_shape(prior, title):
    """{start_behavior, conversion_event, journey_kind} read off a prior
    journey ({stages: {label: count}, ...} as corpus_catalog.prior_journey
    returns it, or a spine list), with the prior title swapped out for a
    placeholder so the caller can re-point it. None when the prior has
    no readable spine."""
    labels = []
    if isinstance(prior, dict):
        st = prior.get('stages')
        if isinstance(st, dict):
            labels = [str(k) for k in st.keys()]
        elif isinstance(st, list):
            labels = [str(s.get('label') or s.get('name') or '') for s in st if isinstance(s, dict)]
    elif isinstance(prior, list):
        labels = [str(s.get('label') or s.get('name') or '') for s in prior if isinstance(s, dict)]
    labels = [l.strip() for l in labels if l and not _STAGE_SKIP_RX.match(l.strip())]
    if len(labels) < 2:
        return None
    start, end = labels[0], labels[-1]
    kind = 'watch' if re.search(r"\bwatch", end, re.I) else ('purchase' if re.search(r"\b(?:paid|bought|purchas)", end, re.I) else None)
    platform = str((prior or {}).get('platform') or '') if isinstance(prior, dict) else ''
    if title:
        # the run name is often '<Title> on <Platform>' while the stage labels
        # carry the bare title: swap both forms, and keep the platform as part
        # of the shape (2026-10-14, golden g095)
        bare = re.sub(r"\s+on\s+[A-Z][\w+ .&-]{1,30}$", '', str(title)).strip()
        m_plat = re.search(r"\s+on\s+([A-Z][\w+ .&-]{1,30})$", str(title))
        if m_plat and not platform:
            platform = m_plat.group(1).strip()
        for t in sorted({str(title), bare}, key=len, reverse=True):
            if t:
                rx = re.compile(re.escape(t), re.I)
                start, end = rx.sub('{subject}', start), rx.sub('{subject}', end)
    start = re.sub(r"^(?:saw|viewed|watched)\b", lambda m: m.group(0).lower(), start)
    return {'start_behavior': start[:1].lower() + start[1:], 'conversion_event': end[:1].lower() + end[1:],
            'journey_kind': kind, 'platform': platform or None}


def apply_journey_template(parsed, shape, template_title=''):
    """Fill the empty required fields of a journey brief from a prior
    journey's shape, re-pointed at this brief's subject and platform.
    Marks the proposal so the confirm says where the shape came from."""
    p = dict(parsed or {})
    if not shape:
        return p
    if not p.get('platform') and shape.get('platform'):
        p['platform'] = str(shape['platform'])      # the same analysis means the same platform unless the ask says otherwise
    subj, plat = str(p.get('subject') or ''), str(p.get('platform') or '')
    filled = []
    for field in ('start_behavior', 'conversion_event', 'journey_kind'):
        if p.get(field) or not shape.get(field):
            continue
        v = str(shape[field]).replace('{subject}', subj or 'the title')
        if field == 'conversion_event' and plat and ' on ' not in f' {v} ' and 'platform' not in v.lower():
            m = re.search(r"\s+within\b", v)   # "watched an episode on Prime Video within 30 days of the post"
            v = (v[:m.start()] + f" on {plat}" + v[m.start():]) if m else f"{v} on {plat}"
        p[field] = v
        filled.append(field)
    if 'conversion_event' in filled:
        p['_proposed'] = {'field': 'conversion_event', 'value': p['conversion_event'],
                          'from': template_title or 'the prior journey'}
    p['missing'] = [k for k in ('subject', 'platform', 'conversion_event') if not p.get(k)]
    return p


def watch_defaults(parsed):
    """A title on a video platform is a watch journey that starts on
    social (Jenna 2026-10-13: the journey has to show how people came
    to watch the show from social content, the Young Sheldon / Babylon
    5 shape). Fills journey_kind and start_behavior only when the brief
    left them empty."""
    p = dict(parsed or {})
    if str(p.get('journey_kind') or '').lower() == 'media_week' or direction_unclear(p):
        return p   # the after-watch read starts at the viewers, never on social; an unclear ask is asked first
    plat = str(p.get('platform') or '')
    if _PLATFORM_KIND.get(plat) != 'video' and p.get('journey_kind') != 'watch':
        return p
    p.setdefault('journey_kind', 'watch')
    if not p.get('journey_kind'):
        p['journey_kind'] = 'watch'
    if not p.get('start_behavior') and p.get('subject'):
        p['start_behavior'] = f"saw a social post or short-form clip about {p['subject']}"
    return p


def watch_definition_default(parsed, username=''):
    """Which read a watch journey carries (Carolyn Bisson, 2026-10-22):
    the click chain (saw a post, clicked through, opened the page,
    watched) or saw-then-watched (saw a post, then watched within 30 days
    whether or not they clicked). The ask's own words win; an empty field
    takes the reader's default, else the house default. Set on the brief
    before the confirm so the reader sees it and the build carries it."""
    p = dict(parsed or {})
    if str(p.get('journey_kind') or '').lower() != 'watch':
        return p
    try:
        from migration import journey_gold_watch as _gw
        d = _gw.normalize_definition(p.get('watch_definition'))
        if not d:
            d = _gw.definition_of(p, None, username=str(username or ''))
        if d:
            p['watch_definition'] = d
    except Exception:
        pass
    return p


def watch_definition_line(parsed):
    """The shape line of the confirm card for a watch journey, by definition."""
    try:
        from migration import journey_gold_watch as _gw
        d = _gw.normalize_definition((parsed or {}).get('watch_definition'))
    except Exception:
        d = ''
    if d == 'saw_then_watched':
        return ("Four steps from the social post to the first episode: saw a post, clicked through, opened the title page, watched within "
                "30 days of the post, with the watch step sized on everyone who saw a post and then watched (the same day, within 7 days, "
                "within 30 days), then the first post they saw and where it sat, how many posts before they clicked through, where they "
                "looked before the platform, the first episode, the last touch and the assists")
    return ("It's the same shape as every watch journey: four steps from the social post to the first episode, "
            "then the first post they saw and where it sat, how many posts before they clicked through, where they "
            "looked before the platform, the time from the view to the first episode, the first episode, the last touch and the assists")
