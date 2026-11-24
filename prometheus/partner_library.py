"""GET /api/v1/profiles/library and GET /api/v1/profiles/library/<id>
(Jenna 2026-11-23).

A partner building an app on the API asked: "does the API have the
ability to return a list of existing profiles and their associated meta
data? ... Profile names, windows of time the profile is covering, etc...
similar to how someone can see the library in the app ... basically
library api endpoint would reduce agentic turns". Jenna approved it the
same day.

What a key sees: every profile in the shared library
(partner-api-shared-catalog.mdc), Total Universes and their cuts, each
with an id, name, file name (``s3_key``), subject, type, category,
window, updated_at, US audience (and the worldwide audience on a
worldwide profile), the sections it holds in plain words, its parent,
and a Total Universe's cuts. The detail call adds every category inside
each section, for the profile and each of its cuts. Free, rate-limited,
an ETag for caching.

The file name is the one file field a key reads (the client's ask,
approved by Jenna 2026-11-23 4:14 PM PT): exactly the value /check
returns as ``existing_match_s3_key`` for the profile, already partner
visible there. It is a file at the top of the profile bucket, so it never
carries a folder or the bucket's name.

What a key never sees: any other path, bucket names, sample counts, how
a file was made, admin-hidden files, and the general population
baselines (served by GET /api/v1/genpop). Work private to one company
never reaches this
list: Brand Partnership valuations, Digital Twins rooms, journeys and the
decks and data files we deliver each live in their own folder, and only a
.csv at the top of the profile bucket that is in the dashboard's shared
list can be listed (the file_access 'profile' door).

The routes in app.py gate, rate-limit and hand over to list_route /
detail_route. Every string a key reads passes the partner belt
(guards.scrub_partner_payload); names, ids, labels and dates are the
library's own identity data and pass as written, the same as the
subject and file name fields on the check and status calls.

The window, audience and categories come from
migration/partner_library_index.py (one record per file). A file the
index has not seen yet lists with those fields empty while a background
read fills them in.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from functools import lru_cache

TYPE_TU = 'Total Universe'
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
RATE_LIMIT = (120, 2000)
LIST_PARAMS = ('q', 'category', 'type', 'updated_since', 'limit', 'cursor')
NOT_FOUND = 'profile not found'

# The order sections are listed in (the dashboard's own reading order:
# who they are, what they watch and use, what they buy, where they go).
SECTION_ORDER = (
    'demographics', 'locations', 'streaming', 'music streaming', 'TV and news', 'series and movies',
    'podcasts', 'social media', 'search and AI', 'apps', 'games', 'talent', 'creators by topic',
    'sports', 'brands', 'shopping', 'quick service restaurants', 'restaurants', 'technology',
    'finance', 'betting', 'travel', 'cars', 'health and fitness', 'events and places', 'education',
    'causes and groups', 'interests', 'adult sites', 'other',
)

_T = {}


def _put(section, pairs):
    for col, plain in pairs:
        _T[col] = (section, plain)


_put('demographics', [
    ('GENDER', 'gender'), ('AGE', 'age'), ('ETHNICITY', 'ethnicity'), ('EDUCATION', 'education'),
    ('INCOME', 'income'), ('OCCUPATION', 'occupation'), ('PARENTAL STATUS', 'parental status'),
    ('RELATIONSHIP', 'relationship status'), ('RELATIONSHIP STATUS', 'relationship status'),
    ('SEXUAL ORIENTATION', 'sexual orientation'), ('PRIMARY LANGUAGE', 'main language'),
    ('NUMBER OF CHILDREN', 'number of children'), ('AGE OF CHILDREN', 'ages of children'),
    ('RELIGION', 'religion'), ('POLITICAL AFFILIATION', 'political party'),
])
_put('locations', [('LOCATION', 'cities and metro areas'), ('STATE', 'states'), ('COUNTRY', 'countries')])
_put('streaming', [
    ('STREAMING/PLATFORM', 'streaming services'), ('STREAMING VIDEO', 'streaming services'),
    ('STREAMING PLATFORM', 'streaming services'), ('VIRTUAL MVPD FAST', 'live TV and free streaming services'),
    ('VIRTUAL MVPD/FAST', 'live TV and free streaming services'), ('VMVPD/FAST', 'live TV and free streaming services'),
    ('VMVPD', 'live TV streaming services'), ('FAST PLATFORM', 'free streaming services'),
    ('FAST CHANNEL', 'free streaming channels'),
])
_put('music streaming', [('STREAMING/MUSIC', 'music streaming services'), ('STREAMING MUSIC', 'music streaming services')])
_put('TV and news', [('BROADCAST/CABLE', 'TV networks'), ('TV NETWORK', 'TV networks'), ('MEDIA', 'news and media sites')])
_put('series and movies', [
    ('SERIES', 'series'), ('TV SHOW', 'TV shows'), ('MOVIE', 'movies'), ('TV/MOVIE', 'TV shows and movies'),
    ('MOVIE THEATER', 'movie theaters'), ('FRANCHISE', 'franchises'), ('GENRE', 'genres'),
])
_put('podcasts', [('PODCAST', 'podcasts'), ('PODCAST RANKER', 'top podcasts')])
_put('social media', [('SOCIAL MEDIA', 'social media')])
_put('search and AI', [('SEARCH ENGINE/AI', 'search engines and AI tools'), ('SEARCH ENGINE', 'search engines')])
_put('apps', [('APP/PLATFORM USAGE', 'apps'), ('APP/PLATFORM', 'apps'), ('PLATFORMS', 'platforms')])
_put('games', [
    ('GAMES', 'games'), ('VIDEO GAME', 'video games'), ('GAME PLAYERS', 'game players'),
    ('GAMES - PLAYERS', 'game players'), ('ESPORTS', 'esports'),
])
_put('talent', [
    ('TALENT', 'talent'), ('ACTOR', 'actors'), ('MUSICIAN/BAND', 'musicians and bands'), ('ATHLETE', 'athletes'),
    ('CREATOR/INFLUENCER', 'creators'), ('INFLUENCER/CREATOR', 'creators'),
    ('HOST/PERSONALITY', 'hosts and personalities'), ('COMEDIAN', 'comedians'), ('PODCASTER', 'podcasters'),
    ('POLITICS/ACTIVIST', 'politicians and activists'), ('POLITICIAN', 'politicians'),
    ('WRITER/DIRECTOR/AUTHOR/ARTIST', 'writers, directors, authors and artists'), ('BOOK/AUTHOR', 'authors'),
    ('EMERGING TALENT', 'rising talent'), ('BUSINESS/TECH LEADERS', 'business and tech leaders'),
])
_put('sports', [
    ('SPORTS TEAM', 'sports teams'), ('MLB', 'MLB teams'), ('MILB', 'Minor League Baseball teams'),
    ('NFL', 'NFL teams'), ('NBA', 'NBA teams'), ('NHL', 'NHL teams'), ('MLS', 'MLS teams'),
    ('WNBA', 'WNBA teams'), ('NWSL', 'NWSL teams'), ('AUSL', 'AUSL teams'),
    ('PREMIER LEAGUE', 'Premier League teams'), ('EPL', 'Premier League teams'), ('LA LIGA', 'La Liga teams'),
    ('SERIE A', 'Serie A teams'), ('BUNDESLIGA', 'Bundesliga teams'), ('LIGUE 1', 'Ligue 1 teams'),
    ('LIGA MX', 'Liga MX teams'), ('CFB', 'college football teams'), ('SOCCER', 'soccer'),
    ('AFC', 'AFC teams'), ('NFC', 'NFC teams'), ('AL', 'American League teams'),
    ('NL', 'National League teams'), ('GOLF', 'golf'), ('TENNIS', 'tennis'), ('RUGBY', 'rugby'),
    ('VOLLEYBALL', 'volleyball'), ('HORSE RACING', 'horse racing'), ('F1', 'Formula 1'),
    ('GRAND SLAMS', 'Grand Slam tennis'), ('MASTERS 1000', 'Masters 1000 tennis'),
    ('SPORTS ORGANIZATIONS', 'sports leagues and groups'), ('SPORTS ORGANIZATION', 'sports leagues and groups'),
])
_put('brands', [
    ('MOST PURCHASED BRANDS', 'brands they buy'), ('MOST PURCHASED CATEGORIES', 'kinds of products they buy'),
    ('CPG', 'packaged goods'), ('APPAREL/FOOTWEAR', 'clothing and shoes'), ('APPAREL', 'clothing'),
    ('FOOTWEAR', 'shoes'), ('ACTIVEWEAR', 'activewear'), ('BEAUTY/WELLNESS', 'beauty and wellness'),
    ('BEAUTY', 'beauty'), ('HOME/OUTDOOR', 'home and outdoor'), ('ACCESSORIES', 'accessories'),
    ('JEWELRY', 'jewelry'), ('INTIMATES', 'intimates'), ('PETS', 'pet products'), ('TOYS', 'toys'),
    ('TOY', 'toys'), ('TECHNOLOGY BRAND', 'tech brands'), ('HEAVY MACHINERY', 'heavy machinery'),
    ('BEVERAGE', 'drinks'), ('GROCERY', 'groceries'), ('B2B', 'business brands'),
])
_put('shopping', [
    ('WHERE THEY SHOP', 'stores they shop at'), ('RETAILERS', 'retailers'), ('RETAILER', 'retailers'),
    ('SHOPPING INTENT', 'shopping plans'), ('MEMBERSHIP', 'memberships'),
])
_put('quick service restaurants', [('QSR', 'quick service restaurants')])
_put('restaurants', [
    ('WHERE THEY DINE', 'restaurants they visit'), ('CASUAL DINING', 'casual dining'), ('RESTAURANT', 'restaurants'),
])
_put('technology', [('TECHNOLOGY/DEVICE', 'devices'), ('TELECOM', 'phone and internet providers')])
_put('finance', [
    ('BANKING', 'banks'), ('BANK', 'banks'), ('BANKS', 'banks'), ('DIGITAL BANKING', 'digital banks'),
    ('DIGITAL BANK', 'digital banks'), ('CREDIT PROVIDER', 'credit cards and lenders'),
    ('CREDIT PROVIDERS', 'credit cards and lenders'), ('INVESTMENTS', 'investing'), ('TRADING', 'trading apps'),
    ('INSURANCE', 'insurance'),
])
_put('betting', [('BETTING', 'betting')])
_put('travel', [('TRAVEL', 'travel')])
_put('cars', [('AUTOMOBILE', 'car brands'), ('AUTOMOTIVE PARTS', 'car parts')])
_put('health and fitness', [
    ('PHARMACY', 'pharmacies'), ('PHARMA', 'medicines'), ('HEALTH & WELLNESS', 'health and wellness'),
    ('HEALTHCARE', 'healthcare'), ('WORKOUT FACILITY', 'gyms'),
])
_put('events and places', [
    ('EVENTS', 'events'), ('TICKETING', 'ticket sites'), ('VENUE', 'venues'), ('AMUSEMENT PARKS', 'amusement parks'),
    ('MUSEUM', 'museums'), ('FESTIVAL', 'festivals'),
])
_put('education', [('COLLEGE/UNIVERSITY', 'colleges'), ('EDUCATION & LEARNING', 'learning sites')])
_put('causes and groups', [('NON PROFIT/CHARITY', 'charities'), ('ORGANIZATIONAL MEMBERSHIPS', 'groups they belong to')])
_put('interests', [('INTEREST', 'interests')])
_put('adult sites', [('PORN MEDIA', 'adult sites')])

_ACRONYMS = frozenset({'TV', 'AI', 'NFL', 'MLB', 'NBA', 'NHL', 'MLS', 'WNBA', 'NWSL', 'AUSL', 'AFC', 'NFC', 'DIY',
                       'US', 'UK', 'F1', 'MX', 'B2B', 'QSR', 'CPG', 'ESPN', 'HBO', 'PGA', 'LPGA', 'NCAA', 'UFC',
                       'WWE', 'NASCAR'})
_DEMO_BLOCK_RX = re.compile(r'^(AGE|GENDER|ETHNICITY|INCOME|EDUCATION|OCCUPATION|RELATIONSHIP|PARENTAL STATUS|'
                            r'SEXUAL ORIENTATION) \| (.+)$')
_PREFIX_RX = re.compile(r'^(.+?) - (.+)$')
_ATHLETE_RX = re.compile(r'^(.+) ATHLETES?$')
_CONTENT_RX = re.compile(r'^(.+) CONTENT$')
_DIVISION_RX = re.compile(r'^(AFC|NFC|AL|NL) (EAST|WEST|NORTH|SOUTH|CENTRAL)$')
_NAMED_DIVISION_RX = re.compile(r'^([A-Z]+) (DIVISION|CONFERENCE)$')

# The dashboard's own reading of a name (bg-webapp/app.py _fam_suffix_info,
# which mirrors the page's getProfileSuffixInfo): the first ' - ' splits
# subject from cut; a trailing 'Total Universe' (or 'Total Universe 2025')
# is the base itself; a few bare tokens (a year, Avid, Casual) mark a cut.
_DASH_SUFFIX_RE = re.compile(r'^(.+?)\s[\-\u2013\u2014]\s+(.+)$')
_TOKEN_SUFFIX_RE = re.compile(
    r'[\s_\-]+(20\d{2}|\d+\+|\d+\s*Plus|Avid(?:\s+Fans?)?|Casual(?:\s+Fans?)?|'
    r'Super\s*Fans?|Heavy(?:\s+Users?)?|Light(?:\s+Users?)?|Lite|Premium|Standard)$',
    re.I)
_BASE_UNIVERSE_RE = re.compile(r'^total universe(\s+\d{4})?$', re.I)
_GLOBAL_SEG_RX = re.compile(r'^(?:global|worldwide|world-?wide)(?:\s*-\s*|$)', re.I)
_STAMP_RX = re.compile(r'_(\d{2})_(\d{2})_(\d{4})(?:_(\d{2})_(\d{2}))?\.csv$', re.I)
_SINCE_RX = re.compile(r'^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$')


def _idx():
    from migration import partner_library_index as _pli
    return _pli


def _ws(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def _fold(s):
    return re.sub(r'[^0-9a-z]', '', str(s or '').lower())


def split_name(name):
    """(subject, cut label) for a display name; '' label for a Total
    Universe."""
    name = _ws(name)
    if not name:
        return '', ''
    dm = _DASH_SUFFIX_RE.match(name)
    if dm:
        base, suffix = _ws(dm.group(1)), _ws(dm.group(2))
        if base and suffix:
            if _BASE_UNIVERSE_RE.match(suffix):
                return base, ''
            return base, suffix
    m = _TOKEN_SUFFIX_RE.search(name)
    if not m:
        return name, ''
    base = _ws(name[:m.start()])
    if not base:
        return name, ''
    suffix = _ws(m.group(1))
    if _BASE_UNIVERSE_RE.match(suffix):
        return base, ''
    return base, suffix


def _plain_words(label):
    t = str(label or '').replace('&', ' and ').replace('/', ' and ').replace('_', ' ')
    out = []
    for w in t.split():
        u = w.upper().strip(',.')
        out.append(u if u in _ACRONYMS else w.lower())
    return _ws(' '.join(out))


def _country(label):
    try:
        from migration import international_profiles as _ip
        return _ip.canonical_country(label) or ''
    except Exception:
        return {'UK': 'United Kingdom', 'UNITED KINGDOM': 'United Kingdom', 'CANADA': 'Canada'}.get(
            _ws(label).upper(), '')


@lru_cache(maxsize=4096)
def section_of(column):
    """(section, plain category name) for a category a profile holds, or
    None for a row that describes the file itself."""
    c = re.sub(r'[_\s]+', ' ', str(column or '').strip().upper())
    if not c:
        return None
    try:
        if c in _idx().META_COLS:
            return None
    except Exception:
        pass
    hit = _T.get(c)
    if hit:
        return hit
    m = _DEMO_BLOCK_RX.match(c)
    if m:
        base = _T.get(m.group(1), ('demographics', _plain_words(m.group(1))))
        return 'demographics', f"{base[1]} ({_country(m.group(2)) or m.group(2).title()})"
    m = _PREFIX_RX.match(c)
    if m and not c.startswith(('SERIES -', 'GAMES -')):
        where = _country(m.group(1))
        inner = section_of(m.group(2)) if where else None
        if inner:
            return inner[0], f"{inner[1]} ({where})"
    if c.startswith('SERIES - '):
        return 'series and movies', f"{_plain_words(c[len('SERIES - '):])} series"
    if ',' in c:
        for part in reversed([p.strip() for p in c.split(',') if p.strip()]):
            inner = section_of(part)
            if inner and inner[0] != 'other':
                return inner
    m = _ATHLETE_RX.match(c)
    if m:
        lg = _T.get(m.group(1))
        return 'talent', f"{(lg[1].replace(' teams', '') if lg and lg[0] == 'sports' else _plain_words(m.group(1)))} athletes"
    m = _CONTENT_RX.match(c)
    if m:
        return 'creators by topic', f"{_plain_words(m.group(1))} creators"
    m = _DIVISION_RX.match(c)
    if m:
        return 'sports', f"{m.group(1)} {m.group(2).title()} teams"
    m = _NAMED_DIVISION_RX.match(c)
    if m:
        return 'sports', f"{m.group(1).title()} {m.group(2).title()} teams"
    return 'other', _plain_words(c)


def sections_for(columns):
    """(section names in reading order, [{section, categories}])."""
    order, details = _sections_cached(tuple(columns or ()))
    return list(order), [{'section': d[0], 'categories': list(d[1])} for d in details]


@lru_cache(maxsize=4096)
def _clean_word(text):
    """``text`` when the partner belt leaves it exactly as it is, else ''
    (a category name the belt would change is not shown at all)."""
    try:
        from . import guards as _g
        out = _g.scrub_partner_text(text, fallback='')
    except Exception:
        out = text
    return text if out == text else ''


@lru_cache(maxsize=4096)
def _sections_cached(columns):
    by = {}
    for col in columns:
        hit = section_of(col)
        if not hit:
            continue
        sec, plain = hit[0], _clean_word(hit[1])
        if not plain:
            continue
        lst = by.setdefault(sec, [])
        if plain and plain not in lst:
            lst.append(plain)
    order = tuple(s for s in SECTION_ORDER if s in by)
    return order, tuple((s, tuple(by[s])) for s in order)


# ---------------------------------------------------------------- items
@lru_cache(maxsize=512)
def _canonical_category(raw):
    label = _ws(raw)
    if not label:
        return ''
    try:
        from migration.brand_category_canon import canonicalize_brand_category
        val, _note = canonicalize_brand_category(label)
        return _ws(val).upper() or label.upper()
    except Exception:
        return label.upper()


def _is_baseline(name, category):
    return _fold(category) == 'genpop' or bool(re.match(r'^gen\s*pop\b', _ws(name), re.I))


def _servable(key):
    try:
        import file_access as _fa
        return _fa.servable('profile', key) is not None
    except Exception:
        k = str(key or '')
        return bool(k) and '/' not in k and k.lower().endswith('.csv')


def _is_global(name, label, market):
    if str(market or '').upper() == 'GLOBAL':
        return True
    if label and _GLOBAL_SEG_RX.match(label):
        return True
    try:
        from migration import international_profiles as _ip
        return bool(_ip.name_is_global(name))
    except Exception:
        return False


def _stamp_iso(key):
    m = _STAMP_RX.search(str(key or ''))
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mi = int(m.group(4) or 0), int(m.group(5) or 0)
    try:
        return datetime(y, mo, d, hh, mi, tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return None


def _window_label(start, end):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return f"{a:%b} {a.day}, {a.year} to {b:%b} {b.day}, {b.year}"


def _entries(catalog):
    out = []
    for e in catalog or []:
        if not isinstance(e, dict):
            continue
        key = str(e.get('s3_key') or e.get('key') or '').strip()
        name = _ws(e.get('display_name') or e.get('name') or '')
        if not key or not name or not _servable(key) or _is_baseline(name, e.get('category')):
            continue
        out.append((key, name, _ws(e.get('category') or '')))
    return out


def build_items(catalog, index):
    """(items in list order, {id: item}). An item carries its public fields
    plus private ones (leading underscore) that never leave this module."""
    pli = _idx()
    raw = []
    for key, name, cat in _entries(catalog):
        rec = (index or {}).get(key) or {}
        base, label = split_name(name)
        start, end = rec.get('start'), rec.get('end')
        it = {
            'id': pli.profile_id(key), 'name': name, 'subject': base or name,
            'type': label or TYPE_TU, 'category': _canonical_category(cat),
            'window': {'start': start, 'end': end, 'label': _window_label(start, end)},
            'updated_at': _stamp_iso(key), 'us_audience': None, 'worldwide_audience': None,
            'sections': [], 'cuts': [], 'parent_id': None,
            '_key': key, '_label': label, '_indexed': bool(rec), '_proj': rec.get('proj'),
            '_us_share': rec.get('us_share'), '_details': [],
            '_global': _is_global(name, label, rec.get('market')),
        }
        it['sections'], it['_details'] = sections_for(rec.get('columns') or [])
        raw.append(it)

    fams = {}
    for it in raw:
        fams.setdefault(it['subject'].lower(), []).append(it)
    for fam in fams.values():
        tus = [i for i in fam if not i['_label']]

        def _newest(i):
            return (i['updated_at'] or (i['window']['end'] or '') + 'T00:00:00Z', i['_key'])

        primary = max(tus, key=_newest) if tus else None
        for it in fam:
            if it['updated_at'] is None and it['_label'] and primary is not None \
                    and primary['updated_at'] and it['window']['end'] \
                    and it['window']['end'] == primary['window']['end']:
                it['updated_at'] = primary['updated_at']
            if it['updated_at'] is None and it['window']['end']:
                it['updated_at'] = f"{it['window']['end']}T00:00:00Z"
        if primary is not None:
            for it in fam:
                if it['_label']:
                    it['parent_id'] = primary['id']
                    it['subject'] = primary['subject']
            primary['cuts'] = sorted(({'id': i['id'], 'label': i['_label']} for i in fam if i['_label']),
                                     key=lambda c: (c['label'].lower(), c['id']))
        for it in fam:
            proj = it['_proj'] if isinstance(it['_proj'], int) and it['_proj'] > 0 else None
            if not it['_global']:
                it['us_audience'] = proj
                continue
            it['worldwide_audience'] = proj
            us = None
            if proj and it['_us_share']:
                us = int(round(proj * float(it['_us_share']) / 100.0))
            if us is None:
                want = _fold(_GLOBAL_SEG_RX.sub('', it['_label'] or '', count=1))
                twins = [i for i in fam if not i['_global'] and _fold(i['_label']) == want]
                if not want and primary is not None and not primary['_global']:
                    twins = [primary]
                vals = [t['_proj'] for t in twins if isinstance(t['_proj'], int) and t['_proj'] > 0]
                us = vals[0] if vals else None
            if us is not None and proj:
                us = min(us, proj)
            it['us_audience'] = us
    for it in raw:
        it['_sort'] = '\x1f'.join((it['subject'].lower(), '0' if not it['_label'] else '1', it['name'].lower(), it['id']))
        it['_q'] = _fold(it['name']) + ' ' + _fold(it['subject']) + ' ' + _fold(it['_key'])
    raw.sort(key=lambda i: i['_sort'])
    return raw, {i['id']: i for i in raw}


def public(it, details=False):
    """The fields a key reads for one item."""
    out = {
        'id': it['id'], 'name': it['name'], 's3_key': it['_key'], 'subject': it['subject'], 'type': it['type'],
        'category': it['category'], 'window': dict(it['window']), 'updated_at': it['updated_at'],
        'us_audience': it['us_audience'], 'worldwide_audience': it['worldwide_audience'],
        'sections': list(it['sections']), 'cuts': [dict(c) for c in it['cuts']], 'parent_id': it['parent_id'],
    }
    if details:
        out['section_details'] = [{'section': d['section'], 'categories': list(d['categories'])}
                                  for d in it['_details']]
    return out


# ---------------------------------------------------------------- the library, cached
_LIB = {'fp': None, 'items': None, 'by_id': None, 'at': 0.0}
_LIB_LOCK = threading.Lock()


def _catalog_fp(catalog):
    h = hashlib.sha1()
    for e in catalog or []:
        if isinstance(e, dict):
            h.update(f"{e.get('s3_key')}|{e.get('display_name')}|{e.get('category')}\n".encode('utf-8', 'replace'))
    return h.hexdigest()


def library(catalog, index=None):
    """(items, by_id) for the current list, rebuilt only when the list or
    the index changes."""
    if index is None:
        index, ver = _idx().load_index()
    else:
        ver = hashlib.sha1(json.dumps(index, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    fp = f"{_catalog_fp(catalog)}|{ver}"
    with _LIB_LOCK:
        if _LIB['fp'] == fp and _LIB['items'] is not None:
            return _LIB['items'], _LIB['by_id']
    items, by_id = build_items(catalog, index)
    with _LIB_LOCK:
        _LIB.update(fp=fp, items=items, by_id=by_id, at=time.time())
    return items, by_id


def reset_cache():
    with _LIB_LOCK:
        _LIB.update(fp=None, items=None, by_id=None, at=0.0)


def _fill_gaps(items):
    missing = [i['_key'] for i in items if not i['_indexed']]
    if missing:
        try:
            _idx().fill_async(missing)
        except Exception:
            pass


def id_names(catalog):
    """{library id: display name} over the current list (names only: no
    index read, so the check, run and bulk calls stay as fast as before)."""
    try:
        pli = _idx()
        return {pli.profile_id(key): name for key, name, _cat in _entries(catalog)}
    except Exception:
        return {}


def name_for_id(pid, catalog):
    """The display name of the library profile with this id, or None. Lets
    a library id stand in for a prompt on the check and run calls."""
    try:
        pid = str(pid or '').strip()
        if not _idx().ID_RX.match(pid):
            return None
        return id_names(catalog).get(pid)
    except Exception:
        return None


# ---------------------------------------------------------------- the belt, the ETag
# Identity fields the belt leaves as written. 's3_key' is the profile's file
# name, the value /check already returns as existing_match_s3_key
# (2026-11-23); a name like Bill_Pullman_... must reach the key whole. No
# other file field is ever listed here.
SKIP_KEYS = frozenset({'id', 'parent_id', 'name', 's3_key', 'subject', 'type', 'label', 'updated_at', 'next_cursor',
                       'window', 'start', 'end'})


def _skip_keys():
    try:
        from . import guards as _g
        return _g._PARTNER_SKIP_KEYS | SKIP_KEYS
    except Exception:
        return SKIP_KEYS


def _drop_blank(obj):
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, list) and k in ('sections', 'categories'):
                obj[k] = [x for x in v if not (isinstance(x, str) and not x.strip())]
            else:
                _drop_blank(v)
    elif isinstance(obj, list):
        for v in obj:
            _drop_blank(v)
    return obj


def scrub(doc):
    """The partner belt over every string in a library response, in place."""
    try:
        from . import guards as _g
        _g.scrub_partner_payload(doc, skip_keys=_skip_keys())
    except Exception:
        pass
    return _drop_blank(doc)


def violations(doc):
    """[(path, words)] for any string that still carries a banned word."""
    try:
        from . import guards as _g
        return _g.partner_payload_violations(doc, skip_keys=_skip_keys())
    except Exception:
        return []


def etag_of(body):
    blob = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(blob.encode('utf-8')).hexdigest()[:32] + '"'


def etag_matches(header, etag):
    for part in str(header or '').split(','):
        p = part.strip()
        if p == '*':
            return True
        if p.startswith('W/'):
            p = p[2:]
        if p and p == etag:
            return True
    return False


# ---------------------------------------------------------------- the request
def _parse_since(raw):
    m = _SINCE_RX.match(str(raw or '').strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mi, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
    tz = m.group(7) or 'Z'
    try:
        dt = datetime(y, mo, d, hh, mi, ss, tzinfo=timezone.utc)
        if tz != 'Z':
            sign = 1 if tz[0] == '+' else -1
            digits = tz[1:].replace(':', '')
            dt = dt - sign * (datetime(2000, 1, 1, int(digits[:2]), int(digits[2:4])) - datetime(2000, 1, 1))
    except ValueError:
        return None
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _b64(s):
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode('ascii').rstrip('=')


def _unb64(s):
    s = str(s or '')
    return base64.urlsafe_b64decode((s + '=' * (-len(s) % 4)).encode('ascii')).decode('utf-8')


def _cursor(after, fp):
    return _b64(json.dumps({'a': after, 'f': fp}, separators=(',', ':')))


def _read_cursor(raw, fp):
    try:
        doc = json.loads(_unb64(raw))
        if isinstance(doc, dict) and doc.get('f') == fp and isinstance(doc.get('a'), str) and doc['a']:
            return doc['a']
    except Exception:
        pass
    return None


def _arg(args, name):
    try:
        v = args.get(name)
    except Exception:
        v = None
    return '' if v is None else str(v)


def _unknown(args, allowed):
    try:
        names = list(args.keys())
    except Exception:
        names = []
    bad = sorted(k for k in names if k not in allowed)
    return f'unknown query parameter: "{bad[0][:40]}"' if bad else None


def parse_list_args(args):
    """(params, None) or (None, the plain reason for a 400)."""
    err = _unknown(args, LIST_PARAMS)
    if err:
        return None, err
    q = _ws(_arg(args, 'q'))
    if len(q) > 200:
        return None, '"q" exceeds 200 characters'
    category = _ws(_arg(args, 'category'))
    typ = _ws(_arg(args, 'type'))
    if len(category) > 80 or len(typ) > 80:
        return None, '"category" and "type" are at most 80 characters'
    since_raw = _arg(args, 'updated_since').strip()
    since = None
    if since_raw:
        since = _parse_since(since_raw)
        if since is None:
            return None, '"updated_since" must be a date like 2026-11-01 or a time like 2026-11-01T09:30:00Z'
    lim_raw = _arg(args, 'limit').strip()
    limit = DEFAULT_LIMIT
    if lim_raw:
        if not re.fullmatch(r'\d{1,4}', lim_raw) or not 1 <= int(lim_raw) <= MAX_LIMIT:
            return None, '"limit" must be a whole number from 1 to 200'
        limit = int(lim_raw)
    tokens = [t for t in (_fold(w) for w in q.split()) if t]
    cats = set()
    if category:
        cats = {_fold(category), _fold(_canonical_category(category))} - {''}
    fp = hashlib.sha1(json.dumps([tokens, sorted(cats), _fold(typ), since], separators=(',', ':'))
                      .encode('utf-8')).hexdigest()[:16]
    after = None
    cur = _arg(args, 'cursor').strip()
    if cur:
        after = _read_cursor(cur, fp)
        if after is None:
            return None, '"cursor" does not fit these filters; start again without it'
    return {'tokens': tokens, 'cats': cats, 'type': _fold(typ), 'since': since, 'limit': limit,
            'after': after, 'fp': fp}, None


def _match(it, p):
    if p['tokens'] and not all(t in it['_q'] for t in p['tokens']):
        return False
    if p['cats'] and _fold(it['category']) not in p['cats']:
        return False
    if p['type']:
        if p['type'] in ('cut', 'cuts'):
            if not it['_label']:
                return False
        elif _fold(it['type']) != p['type']:
            return False
    if p['since'] and not (it['updated_at'] and it['updated_at'] >= p['since']):
        return False
    return True


def _reply(body, if_none_match):
    scrub(body)
    etag = etag_of(body)
    headers = {'ETag': etag, 'Cache-Control': 'private, no-cache'}
    if etag_matches(if_none_match, etag):
        return None, 304, headers
    return body, 200, headers


def list_route(args, if_none_match, catalog, index=None):
    """(body, status, headers) for GET /api/v1/profiles/library; body is
    None on a 304."""
    p, err = parse_list_args(args)
    if err:
        return {'success': False, 'error': err}, 400, {}
    items, _by_id = library(catalog, index)
    _fill_gaps(items)
    hits = [it for it in items if _match(it, p)]
    total = len(hits)
    if p['after']:
        hits = [it for it in hits if it['_sort'] > p['after']]
    page = hits[:p['limit']]
    nxt = _cursor(page[-1]['_sort'], p['fp']) if page and len(hits) > p['limit'] else None
    body = {'success': True, 'profiles': [public(it) for it in page], 'count': len(page), 'total': total,
            'limit': p['limit'], 'next_cursor': nxt}
    return _reply(body, if_none_match)


def detail_route(pid, args, if_none_match, catalog, index=None):
    """(body, status, headers) for GET /api/v1/profiles/library/<id>."""
    err = _unknown(args, ())
    if err:
        return {'success': False, 'error': err}, 400, {}
    pid = str(pid or '').strip()
    if not _idx().ID_RX.match(pid):
        return {'success': False, 'error': NOT_FOUND}, 404, {}
    items, by_id = library(catalog, index)
    it = by_id.get(pid)
    if it is None:
        return {'success': False, 'error': NOT_FOUND}, 404, {}
    fam = [it] + [by_id[c['id']] for c in it['cuts'] if c['id'] in by_id]
    _fill_gaps(fam)
    body = {'success': True, 'profile': public(it, details=True),
            'cuts': [public(by_id[c['id']], details=True) for c in it['cuts'] if c['id'] in by_id]}
    return _reply(body, if_none_match)


__all__ = ['TYPE_TU', 'DEFAULT_LIMIT', 'MAX_LIMIT', 'RATE_LIMIT', 'LIST_PARAMS', 'NOT_FOUND', 'SECTION_ORDER',
           'split_name', 'section_of', 'sections_for', 'build_items', 'public', 'library', 'reset_cache',
           'id_names', 'name_for_id', 'scrub', 'violations', 'etag_of', 'etag_matches', 'parse_list_args', 'list_route',
           'detail_route']
