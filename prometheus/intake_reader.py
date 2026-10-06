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


def _kind_from_ask(text, kind):
    low = str(text or '').lower()
    if re.search(r'before and after|before/after|20\s*-?\s*min', low) \
            and find_clip_url(text):
        return 'before_after'
    if re.search(r'\bnew to\b', low) and re.search(
            r'\balready\b|\bexisting\b', low):
        return 'discovery_existing'
    if re.search(r'\bsong to\b|music[- ]first|soundtrack|needle-?drop', low):
        return 'music'
    return kind


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
    out = {
        'subject': subject, 'platform': platform,
        'conversion_event': conv, 'journey_kind': kind or None,
        'clip_url': clip_url,
        'start_behavior': start, 'start_date': sd, 'end_date': ed,
        'tam_label': None, 'tam_accounts': None, 'notes': None,
    }
    out['missing'] = [k for k in ('subject', 'platform', 'conversion_event')
                      if not out.get(k)]
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
    optional = ('journey_kind', 'clip_url', 'start_behavior',
                'captured_action',
                'start_date', 'end_date',
                # brand partnership
                'pre_start', 'pre_end', 'post_start', 'post_end',
                'audience',
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


def find_date_ranges(text):
    """Every (start_iso, end_iso, span) range in the text, in order."""
    t = str(text or '')
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
    ranges; the first unlabelled range is the campaign."""
    t = str(text or '')
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
_BP_PREP = re.compile(
    r'\b(?:valu(?:e|ation\s+of|ing)|measure|price|partnership\s+(?:of|for))'
    r'\s+(?:the\s+)?(?P<brand>' + _CAPS_RUN + r')'
    r'(?:\s+(?:partnership|deal|sponsorship|campaign|integration))?'
    r'\s+(?:with|on|and|x|×)\s+(?P<partner>' + _CAPS_RUN + r')')
_BP_NOISE = {'brand', 'partnership', 'valuation', 'campaign', 'window',
             'pull', 'run', 'the', 'a', 'an', 'value', 'please', 'post',
             'pre', 'through', 'optional', 'audience'}


def _bp_clean(v):
    v = _clean(v)
    v = re.sub(r'^(?:the|a|an)\s+', '', v, flags=re.I)
    v = re.sub(r'\s*(?:,|;|\(|-)\s*$', '', v).strip()
    return v or None


def _bp_is_name(v):
    if not v:
        return False
    toks = re.sub(r'[^a-z0-9 ]+', ' ', v.lower()).split()
    if not toks or all(w in _BP_NOISE for w in toks):
        return False
    if re.search(r'\d{4}', v) or v.lower() in _PLATFORM_NAMES_LOW:
        return False
    return True


def keyword_parse_brand_partnership(text):
    """Brand Partnership brief -> brand_partner / qualifier / event
    window (+ pre / post / audience when stated), best effort."""
    t = str(text or '')
    out = {'brand_partner': None, 'qualifier': None,
           'event_start': None, 'event_end': None,
           'pre_start': None, 'pre_end': None,
           'post_start': None, 'post_end': None, 'audience': None,
           'notes': None}
    out.update({k: v for k, v in find_windows(t).items()})
    for k, rx in _BP_LABELLED.items():
        m = rx.search(t)
        if m:
            v = _bp_clean(m.group('v'))
            if v and (k == 'audience' or _bp_is_name(v)):
                out[k] = v
    # Strip dates so the pair readers do not swallow a window.
    bare = _DATE_RANGE.sub(' ', t)
    bare = _SHORT_RANGE.sub(' ', bare)
    bare = _SINGLE_DATE.sub(' ', bare)
    if not (out['brand_partner'] and out['qualifier']):
        m = _BP_PREP.search(bare)
        if m:
            b, p = _bp_clean(m.group('brand')), _bp_clean(m.group('partner'))
            if _bp_is_name(b) and _bp_is_name(p):
                out['brand_partner'] = out['brand_partner'] or b
                out['qualifier'] = out['qualifier'] or p
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
    out['missing'] = [k for k in ('brand_partner', 'qualifier',
                                  'event_start', 'event_end')
                      if not out.get(k)]
    return out


def propose_brand_partnership_field(parsed):
    """A campaign with a start and no end is running: propose today as
    the end. Never proposes the brand, the partner, or a start."""
    p = parsed or {}
    required = ('brand_partner', 'qualifier', 'event_start', 'event_end')
    missing = [k for k in required if not p.get(k)]
    if missing != ['event_end']:
        return None
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if str(p.get('event_start')) > today:
        return None
    return 'event_end', today, f'through today ({today})'


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
    'campaign_name': 'the campaign name', 'urls': 'the campaign URLs',
    'daily_refresh': 'daily tracking',
}


def propose_journey_field(parsed):
    """When exactly one of subject / platform / conversion_event is
    missing and the other two make the answer obvious, return
    (field, value). Otherwise None. Never proposes a subject."""
    p = parsed or {}
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


def proposed_note(field, value, run_label='Run the journey'):
    label = FIELD_LABELS.get(field, field.replace('_', ' '))
    return (f"\n\nYou did not spell out {label}, so I took it as "
            f"\"{value}\". Say '{run_label}' if that is right, or send "
            f"the correction and I will update it.")
