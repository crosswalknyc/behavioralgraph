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


def keyword_parse_journey(text):
    """Digital Journey brief -> the PARSE_SYSTEM_PROMPT shape, best effort.
    Fields it cannot read are None and listed in ``missing``."""
    t = str(text or '')
    plats = find_platforms(t)
    start = find_start_behavior(t)
    conv, kind = find_conversion(t, start)
    platform = _primary_platform(plats, kind, conv)
    subject = find_subject(t, plats)
    sd, ed = find_window(t)
    out = {
        'subject': subject, 'platform': platform,
        'conversion_event': conv, 'journey_kind': kind or None,
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
    optional = ('journey_kind', 'start_behavior', 'captured_action',
                'start_date', 'end_date')
    for k in tuple(required) + optional:
        if not out.get(k) and sec.get(k):
            out[k] = sec[k]
    out['missing'] = [k for k in required if not out.get(k)]
    return out


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
