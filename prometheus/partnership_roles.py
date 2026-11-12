"""Which side of a pairing is the talent and which is the brand
(Jenna 2026-11-11).

A cold ask, "what is the Gap x JYT partnership worth", was answered "Got
it: Gap is the talent and JYT is the brand." The order had read the
pairing in the order it was written. Word order says nothing: a reader
writes "Gap x JYT" as easily as "JYT x Gap".

`orient(a, b, ...)` uses what is known, in this order:

1. what the reader said in this thread ("JYT is the talent and GAP is
   the brand");
2. a read already delivered on the pairing: the question it answered,
   or its own lines ("JYT projected US audience", "Gap buyers");
3. the library: a name that is a brand there (apparel, retail, CPG, a
   bank, a platform) is the brand; a person, a creator, a title or a
   team is the talent or property. One side known settles the other.

When none of that settles it, `orient` returns None and the order asks
one short question (`which_question`) rather than assert a guess.

Pure apart from the two lookups, which are read-only, cached, and
never reach the network in a hermetic run. Tests set `KIND_LOOKUP`.
"""
from __future__ import annotations

import os
import re
import time

TALENT, BRAND = 'talent', 'brand'

# A test (or a caller that already holds one) can supply the library:
# folded name -> 'brand' | 'talent' | ''.
KIND_LOOKUP = None

_TALENT_CATEGORIES = frozenset((
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'EMERGING TALENT', 'HOST/PERSONALITY',
    'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'POLITICIAN', 'WRITER/DIRECTOR/AUTHOR/ARTIST',
    # a title, a show, a team or a league is the property side of a partnership
    'GAME PLAYERS', 'GAMES', 'GAMES - PLAYERS', 'MOVIE', 'PODCAST', 'VERTICAL SHORTS', 'SERIES',
    'MILB', 'MLB', 'NBA', 'NFL', 'NHL', 'MLS', 'WNBA', 'SPORTS TEAM', 'SPORTS ORGANIZATIONS', 'SPORTS ORGANIZATION',
))
_BRAND_CATEGORIES = frozenset((
    'ACCESSORIES', 'ACTIVEWEAR', 'AMUSEMENT PARKS', 'APPAREL', 'APPAREL/FOOTWEAR', 'AUTOMOBILE', 'AUTOMOTIVE PARTS', 'B2B',
    'BANK', 'BANKS', 'BANKING', 'BEAUTY', 'BETTING', 'BEVERAGE', 'CASUAL DINING', 'CPG', 'CREDIT PROVIDERS', 'CREDIT PROVIDER',
    'DIGITAL BANKING', 'FOOTWEAR', 'GROCERY', 'INTIMATES', 'JEWELRY', 'MEMBERSHIP', 'PHARMA', 'QSR', 'RETAILERS', 'SECURITY',
    'TELECOM', 'TICKETING', 'TOY', 'TRADING', 'TRAVEL', 'WHERE THEY SHOP', 'WORKOUT FACILITY', 'TECHNOLOGY/DEVICE',
    'INSURANCE', 'INVESTMENTS', 'HEALTHCARE',
    'APP/PLATFORM', 'STREAMING MUSIC', 'STREAMING VIDEO', 'STREAMING/PLATFORM', 'SOCIAL MEDIA', 'SEARCH ENGINE/AI',
    'FAST PLATFORM', 'VIRTUAL MVPD/FAST', 'VMVPD', 'MOVIE THEATER',
))
_AUDIENCE_TAIL_RX = re.compile(
    r"\s+(?:customers?|shoppers?|buyers?|purchasers?|viewers?|watchers?|listeners?|readers?|players?|subscribers?|users?|fans?|"
    r"audience|members?|owners?|drinkers?|eaters?|consumers?)$", re.I)
_CACHE = {'at': 0.0, 'table': None}
_CACHE_TTL_S = 600.0
PROFILE_CACHE_KEY = 'system/s3_cache.json'


def _fold(text):
    return re.sub(r"[^a-z0-9]+", '', str(text or '').lower())


def _head(display):
    """'Gap Customers - Avid Fan' -> 'gap' (the subject, no cut suffix, no audience noun)."""
    head = str(display or '').split(' - ')[0].strip()
    bare = _AUDIENCE_TAIL_RX.sub('', head).strip()
    return _fold(bare), _fold(head)


def kind_of_category(category):
    c = ' '.join(str(category or '').upper().split())
    if not c:
        return ''
    if c in _TALENT_CATEGORIES or c.startswith('SERIES'):
        return TALENT
    if c in _BRAND_CATEGORIES:
        return BRAND
    return ''


def table_from_jobs(jobs):
    """folded name -> kind, from the library's profile list. A name the
    library holds under both a brand and a talent category is left out
    (it settles nothing)."""
    seen = {}
    for j in jobs or []:
        if not isinstance(j, dict):
            continue
        kind = kind_of_category(j.get('category'))
        if not kind:
            continue
        for f in set(_head(j.get('display_name') or j.get('name') or '')):
            if f:
                seen.setdefault(f, set()).add(kind)
    return {f: next(iter(k)) for f, k in seen.items() if len(k) == 1}


def _library_table():
    """The cached table, or {} when the library cannot be read here."""
    if os.environ.get('REGRESSION_TEST_MODE'):
        return {}
    now = time.time()
    if _CACHE['table'] is not None and now - _CACHE['at'] < _CACHE_TTL_S:
        return _CACHE['table']
    table = {}
    try:
        import json
        from .host import host as _host
        s3 = getattr(_host, 's3_client', None) if (not callable(getattr(_host, 'has', None)) or _host.has('s3_client')) else None
        bucket = getattr(_host, 'bucket', None) if (not callable(getattr(_host, 'has', None)) or _host.has('bucket')) else None
        if s3 is not None:
            doc = json.loads(s3.get_object(Bucket=bucket or 'dashboard-inputs', Key=PROFILE_CACHE_KEY)['Body'].read().decode('utf-8'))
            jobs = doc.get('jobs') if isinstance(doc, dict) else None
            table = table_from_jobs(jobs.values() if isinstance(jobs, dict) else jobs)
    except Exception as e:
        print(f"[partnership-roles] library kinds unavailable: {type(e).__name__}: {e}")
        table = {}
    _CACHE['table'], _CACHE['at'] = table, now
    return table


# The brand universe's own columns: a name carried under a people or a
# title column is the talent or property side; one carried under a
# purchase, retail, dining, auto, finance or platform column is a brand.
_UNIVERSE_TALENT = frozenset((
    'TALENT', 'ACTOR', 'ATHLETE', 'COMEDIAN', 'CREATOR/INFLUENCER', 'INFLUENCER/CREATOR', 'HOST/PERSONALITY',
    'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST', 'TV SHOW', 'FRANCHISE',
    'SPORTS TEAM', 'SPORTS ORGANIZATIONS', 'PODCAST', 'GAMES',
))
_UNIVERSE_BRAND = frozenset((
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS',
    'TECHNOLOGY BRAND', 'TECHNOLOGY/DEVICE', 'HEAVY MACHINERY', 'WHERE THEY SHOP', 'WHERE THEY DINE', 'QSR', 'AUTOMOBILE',
    'AUTOMOTIVE PARTS', 'TELECOM', 'BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER', 'INVESTMENTS', 'TRADING', 'INSURANCE',
    'TRAVEL', 'BETTING', 'PHARMACY', 'WORKOUT FACILITY', 'TICKETING', 'BEVERAGE',
))
UNIVERSE_KEY = 'Gen_Pop_2026.csv'
_UCACHE = {'at': 0.0, 'table': None}


def kind_of_column(column):
    c = ' '.join(str(column or '').upper().split())
    if c in _UNIVERSE_TALENT or c.endswith(' CONTENT'):
        return TALENT
    if c in _UNIVERSE_BRAND:
        return BRAND
    return ''


def table_from_rows(rows):
    """folded name -> kind from (column, value) rows of the brand
    universe. A name carried on both sides is left out."""
    seen = {}
    for col, val in rows or []:
        kind = kind_of_column(col)
        f = _fold(val)
        if kind and f:
            seen.setdefault(f, set()).add(kind)
    return {f: next(iter(k)) for f, k in seen.items() if len(k) == 1}


def _universe_table():
    """The cached brand-universe table, or {} when it cannot be read here."""
    if os.environ.get('REGRESSION_TEST_MODE'):
        return {}
    now = time.time()
    if _UCACHE['table'] is not None and now - _UCACHE['at'] < _CACHE_TTL_S:
        return _UCACHE['table']
    table = {}
    try:
        from .host import host as _host
        from .brand_coverage import _frame
        df = _frame(_host, UNIVERSE_KEY)
        if df is not None and len(df):
            table = table_from_rows(zip(df['Column'].astype(str), df['Value'].astype(str)))
    except Exception as e:
        print(f"[partnership-roles] brand universe kinds unavailable: {type(e).__name__}: {e}")
        table = {}
    _UCACHE['table'], _UCACHE['at'] = table, now
    return table


def kind_of(name):
    """'brand', 'talent' or '' for a name: the library's own profile of
    it first, then the column the brand universe carries it under."""
    f = _fold(name)
    if not f:
        return ''
    try:
        if callable(KIND_LOOKUP):
            return str(KIND_LOOKUP(f) or '')
        bare = _fold(_AUDIENCE_TAIL_RX.sub('', str(name or '').strip()))
        table = _library_table()
        got = str(table.get(f) or table.get(bare) or '')
        if got:
            return got
        table = _universe_table()
        return str(table.get(f) or table.get(bare) or '')
    except Exception:
        return ''


def _match(name, a, b):
    """a or b, whichever `name` is (case and punctuation aside), else ''."""
    f = _fold(name)
    if f and f == _fold(a):
        return a
    if f and f == _fold(b):
        return b
    return ''


def _from_said(text, a, b):
    """(talent, brand) when the sentence gives both roles for this pair."""
    try:
        from . import bpiq_slots as _sl
        got = _sl.parse(str(text or ''))
    except Exception:
        return None
    t, br = _match(got.get('talent'), a, b), _match(got.get('brand'), a, b)
    if t and br and _fold(t) != _fold(br):
        return t, br
    if t and not got.get('brand'):
        return t, (b if _fold(t) == _fold(a) else a)
    if br and not got.get('talent'):
        return (b if _fold(br) == _fold(a) else a), br
    return None


_AUDIENCE_OF_RX = r"(?:'s)?\s+(?:projected\s+)?(?:US\s+)?(?:audience|fans|fan\s*base|followers|viewers|listeners|engagers)\b"
_BUYERS_OF_RX = r"(?:'s)?\s+(?:buyers|customers|shoppers|purchasers|orders|customer\s+file|purchase\s+penetration)\b"


def roles_from_read(read, a, b):
    """(talent, brand) from a read already delivered on the pairing: the
    question it answered, else its own lines (the side whose audience it
    sizes is the talent, the side whose buyers it counts is the brand)."""
    if not isinstance(read, dict):
        return None
    got = _from_said(read.get('question') or read.get('q') or '', a, b)
    if got:
        return got
    labels = ' | '.join(str(x) for x in (read.get('labels') or []))
    if not labels:
        return None
    votes = {}
    for name, other in ((a, b), (b, a)):
        n = re.escape(str(name))
        if re.search(r"(?<![\w])" + n + _AUDIENCE_OF_RX, labels, re.I):
            votes[_fold(name)] = TALENT
        elif re.search(r"(?<![\w])" + n + _BUYERS_OF_RX, labels, re.I):
            votes[_fold(name)] = BRAND
    return _settle(a, b, votes.get(_fold(a), ''), votes.get(_fold(b), ''))


def _settle(a, b, ka, kb):
    """(talent, brand) from the two kinds; None when they do not settle it."""
    if ka == TALENT and kb != TALENT:
        return a, b
    if kb == TALENT and ka != TALENT:
        return b, a
    if ka == BRAND and kb != BRAND:
        return b, a
    if kb == BRAND and ka != BRAND:
        return a, b
    return None


def orient(a, b, history=None, read=None):
    """(talent, brand, how) for the pair, or None when nothing known
    settles it. `how` is 'said', 'read' or 'library'. Never raises."""
    a, b = ' '.join(str(a or '').split()), ' '.join(str(b or '').split())
    if not _fold(a) or not _fold(b) or _fold(a) == _fold(b):
        return None
    try:
        for t in reversed(list(history or [])):
            if isinstance(t, dict) and str(t.get('role') or '') == 'user':
                got = _from_said(t.get('text') or t.get('content') or '', a, b)
                if got:
                    return got[0], got[1], 'said'
        got = roles_from_read(read, a, b) if read else None
        if got:
            return got[0], got[1], 'read'
        got = _settle(a, b, kind_of(a), kind_of(b))
        if got:
            return got[0], got[1], 'library'
    except Exception:
        return None
    return None


def which_question(a, b):
    """The one short question when the pair's roles are not known."""
    return f"Which one is the talent or property: {a} or {b}?"


__all__ = ['TALENT', 'BRAND', 'kind_of', 'kind_of_category', 'table_from_jobs', 'orient', 'roles_from_read', 'which_question']
