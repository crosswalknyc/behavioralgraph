"""Cross-tab across behaviors: the share of one audience that does
behavior A and B and C, with the same figure for the US and the index.

2026-11-11, Jenna (feedback she forwarded): "The cross-tab
functionality, open up to across behaviors. between behavior A, B and
C... For example the Chime users who shop at Target and have an
interest in X."

A profile carries one penetration per row. The share of people who do
several things at once is not in the file, and neither easy answer is
right: multiplying the shares treats related behaviors as strangers, and
the smallest share assumes one behavior sits wholly inside the other.

ONE estimator, here, for every surface (a Prometheus answer, a page, a
deck, a derived cut's size). Nothing else may reimplement it. It is the
same family the category reach uses (migration/category_rollup.py): each
behavior's share is held exactly and the behaviors move with one common
driver.

The estimator
-------------
Every behavior i has its share p_i in the audience, its share g_i in
the US, and so its index. How much a behavior moves with the audience's
common driver is its loading a_i, read off the index: nothing at or
under the US average, rising with the index, full at IDX_FULL, never
above A_MAX. Two behaviors are tied by a_i * a_j, so a pair of rows the
audience is known for sits above the unrelated figure and a pair of
average rows sits on it. The share doing all of them is integrated on a
fixed grid, so the same inputs always give the same output.

Properties (each one is tested):
- never above the smallest single share, never below
  max(0, sum of shares - (k - 1));
- never below the unrelated figure, and well inside the bottom third of
  the range above it, on purpose: Jenna's standing rule is that we
  anchor low (calibrate-low-anchor-to-actuals);
- the same for any order the behaviors are named in;
- nested: A and B and C never exceeds A and B, which never exceeds A,
  for every sub-group, and every sub-group is returned with the read;
- the SAME loadings run on the audience and on the US, so the index is
  like for like;
- a figure already on the record stands, and every new figure is held
  inside the ones on the record (``held``).

Counts are house math: people in the data = share x the profile's
sample; US people = that / 10,000,000 x 329,900,000. No count ends in
a zero or carries a placeholder run of zeros.

Pure: frames and numbers in, a dict out. No model, no S3, no host.
Never raises into a caller that uses ``cross`` or ``bind``.
Regression: scripts/test_pm_crosstab_behaviors.py.
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import math
import re
import unicodedata
from statistics import NormalDist

__all__ = ['A_MAX', 'IDX_FULL', 'MAX_CONDITIONS', 'US_POP', 'PANEL', 'loading', 'joint', 'unrelated',
           'cross', 'set_key', 'times_of', 'profile_rows', 'genpop_map', 'sample_of', 'window_of',
           'bind', 'category_condition', 'DEMO_COLS', 'pretty_column', 'pretty_value']

US_POP = 329_900_000
PANEL = 10_000_000
# The most a behavior moves with the audience's common driver. Two fully
# tied behaviors share A_MAX squared (0.36), which lands a pair between a
# fifth and a third of the way from the unrelated figure to the smallest
# single share: the low end of the defensible range.
A_MAX = 0.60
# The index at which a behavior is fully tied (3x the US average).
IDX_FULL = 300.0
# At or under this index a behavior is not tied at all.
IDX_FLOOR = 105.0
# A row at or above this share is the whole audience (the subject's own
# row), not a behavior to cross.
PIN_AT = 99.95
# Below this US share an index is not stated.
MIN_GENPOP_FOR_INDEX = 0.0005
MAX_CONDITIONS = 5

_ND = NormalDist()
_GRID = [-8.0 + 0.1 * i for i in range(161)]
_W = [math.exp(-0.5 * z * z) for z in _GRID]
_WS = sum(_W)
_W = [w / _WS for w in _W]

METADATA_COLS = frozenset(('BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'INPUT_METADATA',
                           'INPUT METADATA', 'AVID FAN', 'CASUAL FAN'))
DEMO_COLS = ('GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION', 'PARENTAL_STATUS',
             'RELATIONSHIP', 'SEXUAL_ORIENTATION')
_DEMO_SET = frozenset(DEMO_COLS) | frozenset(c.replace('_', ' ') for c in DEMO_COLS)
# never a behavior to cross: where people live, and the generic purchase list
_NOT_CROSSED = frozenset(('LOCATION', 'MOST PURCHASED CATEGORIES', 'AGE OF CHILDREN', 'AGE_OF_CHILDREN',
                          'NUMBER OF CHILDREN', 'NUMBER_OF_CHILDREN', 'PRIMARY LANGUAGE', 'PRIMARY_LANGUAGE'))

# Where a named brand is looked up first when the words carry no verb
# (the purchase family and the places people shop lead; people come last).
COLUMN_PRIORITY = (
    'WHERE THEY SHOP', 'MOST PURCHASED BRANDS', 'STREAMING/PLATFORM', 'STREAMING VIDEO', 'QSR', 'WHERE THEY DINE',
    'AUTOMOBILE', 'SOCIAL MEDIA', 'APP/PLATFORM USAGE', 'APP/PLATFORM', 'STREAMING/MUSIC', 'STREAMING MUSIC',
    'BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER', 'INVESTMENTS', 'TRADING', 'INSURANCE', 'TELECOM', 'TRAVEL',
    'BETTING', 'PHARMACY', 'WORKOUT FACILITY', 'TICKETING', 'VIRTUAL MVPD FAST', 'VIRTUAL MVPD/FAST',
    'BROADCAST/CABLE', 'MEDIA', 'SEARCH ENGINE/AI', 'TECHNOLOGY/DEVICE', 'TECHNOLOGY BRAND', 'CPG',
    'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS', 'BEVERAGE', 'GAMES',
    'EVENTS', 'VENUE', 'AMUSEMENT PARKS', 'MOVIE THEATER', 'FRANCHISE', 'SPORTS TEAM', 'SPORTS ORGANIZATIONS',
    'PODCAST', 'INTEREST',
)
# What a verb in the ask says about where the row lives.
HINT_COLUMNS = {
    'shop': ('WHERE THEY SHOP', 'MOST PURCHASED BRANDS'),
    'buy': ('MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'HOME/OUTDOOR', 'ACCESSORIES',
            'PETS', 'TOYS', 'TECHNOLOGY BRAND', 'BEVERAGE', 'WHERE THEY SHOP'),
    'interest': ('INTEREST',),
    'watch': ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'VIRTUAL MVPD FAST', 'VIRTUAL MVPD/FAST', 'BROADCAST/CABLE',
              'FAST PLATFORM', 'FAST CHANNEL', 'MEDIA', 'SOCIAL MEDIA', 'APP/PLATFORM USAGE', 'APP/PLATFORM'),
    'use': ('STREAMING/PLATFORM', 'SOCIAL MEDIA', 'APP/PLATFORM USAGE', 'APP/PLATFORM', 'STREAMING/MUSIC',
            'SEARCH ENGINE/AI', 'BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER', 'TELECOM', 'TECHNOLOGY/DEVICE',
            'INVESTMENTS', 'TRADING', 'BETTING', 'TICKETING', 'PHARMACY', 'INSURANCE', 'WHERE THEY SHOP'),
    'eat': ('QSR', 'WHERE THEY DINE'),
    'drive': ('AUTOMOBILE', 'AUTOMOTIVE PARTS'),
    'bank': ('BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER', 'INVESTMENTS', 'TRADING'),
    'listen': ('STREAMING/MUSIC', 'STREAMING MUSIC', 'PODCAST', 'MUSICIAN/BAND'),
    'play': ('GAMES', 'BETTING'),
    'follow': ('TALENT', 'SPORTS TEAM', 'MUSICIAN/BAND', 'ATHLETE', 'ACTOR', 'CREATOR/INFLUENCER', 'SOCIAL MEDIA',
               'SPORTS ORGANIZATIONS', 'FRANCHISE'),
    'travel': ('TRAVEL',),
    'wear': ('APPAREL/FOOTWEAR', 'ACCESSORIES', 'MOST PURCHASED BRANDS'),
    'bet': ('BETTING',),
    'read': ('MEDIA', 'INTEREST'),
    'insure': ('INSURANCE',),
    'workout': ('WORKOUT FACILITY',),
}
# How a column reads in a sentence ("shop at Target", "use Hulu").
COLUMN_VERB = {
    'WHERE THEY SHOP': 'shop at', 'MOST PURCHASED BRANDS': 'bought', 'CPG': 'bought', 'APPAREL/FOOTWEAR': 'bought',
    'BEAUTY/WELLNESS': 'bought', 'HOME/OUTDOOR': 'bought', 'ACCESSORIES': 'bought', 'PETS': 'bought', 'TOYS': 'bought',
    'TECHNOLOGY BRAND': 'bought', 'BEVERAGE': 'bought', 'INTEREST': 'have an interest in', 'QSR': 'eat at',
    'WHERE THEY DINE': 'eat at', 'AUTOMOBILE': 'engage with', 'STREAMING/PLATFORM': 'use', 'STREAMING VIDEO': 'use',
    'STREAMING/MUSIC': 'use', 'STREAMING MUSIC': 'use', 'SOCIAL MEDIA': 'use', 'APP/PLATFORM USAGE': 'use',
    'APP/PLATFORM': 'use', 'SEARCH ENGINE/AI': 'use', 'BANKING': 'use', 'DIGITAL BANKING': 'use',
    'CREDIT PROVIDER': 'use', 'INVESTMENTS': 'use', 'TRADING': 'use', 'TELECOM': 'use', 'INSURANCE': 'use',
    'BETTING': 'use', 'PHARMACY': 'use', 'TICKETING': 'use', 'VIRTUAL MVPD FAST': 'use', 'TRAVEL': 'engage with',
    'GAMES': 'play', 'WORKOUT FACILITY': 'engage with',
}
# The plain name of a whole column, for "any QSR" reads and for nearest-row lines.
COLUMN_ALIASES = {
    'QSR': ('qsr', 'fast food', 'quick service', 'quick service restaurant', 'fast food chain', 'fast food restaurant'),
    'WHERE THEY DINE': ('restaurant', 'casual dining', 'sit down restaurant', 'dining'),
    'WHERE THEY SHOP': ('retailer', 'store', 'retail store'),
    'STREAMING/PLATFORM': ('streaming service', 'streaming platform', 'streamer', 'svod', 'streaming video service'),
    'STREAMING/MUSIC': ('music service', 'music streaming service', 'streaming music service'),
    'SOCIAL MEDIA': ('social media', 'social platform', 'social network', 'social app'),
    'BANKING': ('bank',), 'DIGITAL BANKING': ('digital bank', 'neobank', 'online bank'),
    'CREDIT PROVIDER': ('credit card', 'credit provider', 'card issuer'),
    'INSURANCE': ('insurer', 'insurance company', 'insurance brand', 'insurance'),
    'TELECOM': ('telecom', 'carrier', 'wireless carrier', 'phone carrier', 'mobile carrier'),
    'TRAVEL': ('travel brand', 'airline', 'hotel', 'travel site'),
    'AUTOMOBILE': ('car brand', 'automaker', 'auto brand', 'automobile brand', 'car maker'),
    'BETTING': ('betting app', 'sportsbook', 'betting site', 'betting brand'),
    'WORKOUT FACILITY': ('gym', 'workout facility', 'fitness club'),
    'GAMES': ('game', 'video game'), 'PODCAST': ('podcast',),
    'INVESTMENTS': ('investment company', 'brokerage', 'investment brand'),
    'PHARMACY': ('pharmacy', 'drugstore'), 'TICKETING': ('ticketing site', 'ticketing app', 'ticket seller'),
    'SEARCH ENGINE/AI': ('search engine', 'ai assistant', 'ai tool'),
    'APP/PLATFORM USAGE': ('app',), 'APPAREL/FOOTWEAR': ('apparel brand', 'footwear brand', 'clothing brand'),
    'BEAUTY/WELLNESS': ('beauty brand',), 'CPG': ('cpg brand', 'grocery brand', 'packaged goods brand'),
    'SPORTS TEAM': ('sports team', 'team'),
}
# Plain phrasings of interest rows (looked up when the words are not a row).
_INTEREST_NEAR = {
    'home improvement': ('BUILDING/DIY', 'HOME & LIVING', 'GARDENING', 'HOME DECOR', 'INTERIOR DESIGN'),
    'diy': ('BUILDING/DIY',), 'fitness': ('EXERCISE & FITNESS', 'HEALTH & WELLNESS'), 'tech': ('TECHNOLOGY',),
    'working out': ('EXERCISE & FITNESS',), 'personal finance': ('FINANCE', 'FINANCIAL PLANNING', 'INVESTING'),
    'money': ('FINANCE', 'FINANCIAL PLANNING'), 'food': ('COOKING', 'RECIPES', 'DINING OUT'),
    'ai': ('ARTIFICIAL INTELLIGENCE',), 'video games': ('GAMING',), 'cars': ('CARS',), 'autos': ('CARS',),
    'fashion': ('FASHION', 'FOOTWEAR'), 'beauty': ('SKINCARE', 'HAIR CARE', 'BEAUTY'), 'kids': ('LIFESTYLE-KIDS & FAMILY',),
    'parenting': ('LIFESTYLE-KIDS & FAMILY',), 'hip hop': ('MUSIC-RAP & HIP HOP',), 'news': ('NEWS-GENERAL',),
}
# Plain names of demographic buckets.
_DEMO_ALIASES = {
    ('GENDER', 'FEMALE'): ('female', 'females', 'women', 'woman'),
    ('GENDER', 'MALE'): ('male', 'males', 'men', 'man'),
    ('PARENTAL_STATUS', 'HAS CHILDREN'): ('parents', 'parent', 'has children', 'have children', 'have kids',
                                          'with kids', 'with children', 'moms and dads'),
    ('PARENTAL_STATUS', 'NO CHILDREN'): ('no children', 'no kids', 'without kids', 'without children', 'non parents'),
    ('ETHNICITY', 'HISPANIC OR LATINO'): ('hispanic', 'latino', 'latina', 'latinx', 'hispanic or latino'),
    ('ETHNICITY', 'BLACK OR AFRICAN AMERICAN'): ('black', 'african american', 'black or african american'),
    ('ETHNICITY', 'ASIAN'): ('asian', 'asian american'), ('ETHNICITY', 'WHITE'): ('white', 'caucasian'),
    ('RELATIONSHIP', 'MARRIED'): ('married',), ('RELATIONSHIP', 'SINGLE'): ('single', 'singles'),
    ('SEXUAL_ORIENTATION', 'LGBTQ+'): ('lgbtq', 'lgbtq+', 'lgbt', 'gay or lesbian', 'queer'),
    ('EDUCATION', 'BACHELORS DEGREE'): ('college graduates', 'bachelors degree', 'college grads'),
    ('AGE', '18-24'): ('18-24', '18 to 24', 'aged 18 to 24', 'ages 18 to 24', 'age 18-24', 'aged 18-24'),
    ('AGE', '25-34'): ('25-34', '25 to 34', 'aged 25 to 34', 'ages 25 to 34', 'age 25-34', 'aged 25-34'),
    ('AGE', '35-44'): ('35-44', '35 to 44', 'aged 35 to 44', 'ages 35 to 44', 'age 35-44', 'aged 35-44'),
    ('AGE', '45-54'): ('45-54', '45 to 54', 'aged 45 to 54', 'ages 45 to 54', 'age 45-54', 'aged 45-54'),
    ('AGE', '55-64'): ('55-64', '55 to 64', 'aged 55 to 64', 'ages 55 to 64', 'age 55-64', 'aged 55-64'),
    ('AGE', '65 OR OLDER'): ('65 or older', '65+', '65 and older', '65 plus', 'over 65', 'seniors'),
}


# ----------------------------------------------------------------- math
def loading(index):
    """How much a behavior moves with the audience's common driver, 0 to
    A_MAX, from its index against the US (100 = average)."""
    try:
        ix = float(index)
    except (TypeError, ValueError):
        return 0.0
    if ix != ix or ix <= IDX_FLOOR:
        return 0.0
    x = math.log(ix / 100.0) / math.log(IDX_FULL / 100.0)
    return round(A_MAX * max(0.0, min(1.0, x)), 6)


def unrelated(shares):
    """The figure the behaviors would give if they had nothing to do
    with one another (a fraction). The floor of the read."""
    out = 1.0
    for p in shares:
        out *= max(0.0, min(1.0, float(p)))
    return out


def _bounds(shares):
    ps = [max(0.0, min(1.0, float(p))) for p in shares]
    return max(0.0, sum(ps) - (len(ps) - 1)), (min(ps) if ps else 0.0)


def joint(shares, loadings):
    """The share (a fraction) doing every one of the behaviors, each
    held at its own share, tied by its own loading. Symmetric in its
    arguments by construction, so the order never matters."""
    pairs = sorted((max(0.0, min(1.0, float(p))), max(0.0, min(0.999, float(a)))) for p, a in zip(shares, loadings))
    if not pairs:
        return 0.0
    if any(p <= 0.0 for p, _ in pairs):
        return 0.0
    lo, hi = _bounds([p for p, _ in pairs])
    live = [(p, a) for p, a in pairs if p < 1.0]
    if not live:
        return 1.0
    if len(live) == 1:
        return live[0][0]
    if all(a <= 0.0 for _, a in live):
        est = unrelated([p for p, _ in live])
    else:
        thr = [(_ND.inv_cdf(min(max(p, 1e-12), 1.0 - 1e-12)), a, math.sqrt(1.0 - a * a)) for p, a in live]
        est = 0.0
        for z, w in zip(_GRID, _W):
            pr = w
            for t, a, s in thr:
                pr *= _ND.cdf((t - a * z) / s) if a > 0.0 else _ND.cdf(t)
            est += pr
        est = max(est, unrelated([p for p, _ in live]))
    return max(lo, min(hi, est))


def times_of(index):
    """The plain comparison beside an index: 212 -> '2.1x the US
    average'; within five points of 100 -> 'in line with the US
    average'. Same words as the category reach."""
    if index is None:
        return ''
    try:
        ix = float(index)
    except (TypeError, ValueError):
        return ''
    if 95 <= ix <= 105:
        return 'in line with the US average'
    return f"{ix / 100.0:.1f}x the US average"


def _salt_int(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8', 'replace')).hexdigest()
    return int(h[:8], 16)


def _natural(n, *salt):
    """A count that never ends in a zero and never carries three zeros
    in a row in its thousands and hundreds places; moved by a hair,
    always downward (we anchor low), the same on every run."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0

    def ok(x):
        return x % 10 != 0 and not (x > 100_000 and '000' in str(x)[-6:-2])
    if ok(v):
        return v
    h = _salt_int(*salt, v)
    span = max(9, min(99_999, int(v * 0.0004)))
    for i in range(200):
        cand = v - 1 - ((h + i * 7919) % span)
        if cand > 0 and ok(cand):
            return cand
    while v > 1 and not ok(v):
        v -= 1
    return v


# ----------------------------------------------------------------- keys
def _fold(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = s.encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+plus\b', '+', s.lower().replace('&', ' and '))
    return re.sub(r'[^a-z0-9+]+', '', s)


def _tokens(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = s.encode('ascii', 'ignore').decode('ascii').lower().replace("'", '')
    return [t for t in re.split(r'[^a-z0-9+]+', s) if t]


def norm_col(c):
    return re.sub(r'\s+', ' ', str(c or '').strip().upper())


def set_key(keys):
    """The one key for a group of behaviors, whatever order they came in."""
    return ' & '.join(sorted({str(k) for k in keys if str(k)}))


_KEEP_UPPER = frozenset(('QSR', 'CPG', 'NFL', 'NBA', 'MLB', 'NHL', 'MLS', 'WNBA', 'UFC', 'AI', 'TV', 'HBO', 'ESPN',
                         'AMC', 'BET', 'CBS', 'NBC', 'ABC', 'CNN', 'DIY', 'LGBTQ+', 'BMW', 'GMC', 'KFC', 'IHOP',
                         'CVS', 'UPS', 'USAA', 'AT&T', 'H&M', 'IKEA', 'NASCAR', 'F1', 'MVPD', 'US', 'USA', 'DC',
                         'NYC', 'HP', 'LG', 'TJ', 'BJ', 'BJS', 'AARP', 'PBS', 'TNT', 'TBS', 'FX', 'MTV', 'VH1'))


def pretty_value(v):
    """A row's name as it reads in a sentence: 'TARGET' -> 'Target',
    'Disney+/Hulu' stays, 'BUILDING/DIY' -> 'Building/DIY'."""
    s = str(v or '').strip()
    if not s or not s.isupper():
        return s

    def word(w):
        if w in _KEEP_UPPER or (len(w) <= 3 and not w.isalpha()):
            return w
        if w in ('OR', 'AND', 'OF', 'THE', 'A', 'TO', 'IN', 'FOR'):
            return w.lower()
        return w[:1] + w[1:].lower()
    out = re.sub(r"[A-Z0-9+&']+", lambda m: word(m.group(0)), s)
    return out[:1].upper() + out[1:]


def pretty_column(c):
    cu = norm_col(c)
    names = {'WHERE THEY SHOP': 'Where They Shop', 'MOST PURCHASED BRANDS': 'Most Purchased Brands', 'QSR': 'QSR',
             'CPG': 'CPG', 'INTEREST': 'Interest', 'PARENTAL_STATUS': 'Parental Status',
             'SEXUAL_ORIENTATION': 'Sexual Orientation', 'STREAMING/PLATFORM': 'Streaming', 'AUTOMOBILE': 'Automobile', 'STREAMING/MUSIC': 'Streaming Music',
             'APP/PLATFORM USAGE': 'App and Platform Usage', 'SEARCH ENGINE/AI': 'Search Engine and AI',
             'WHERE THEY DINE': 'Where They Dine'}
    return names.get(cu) or pretty_value(cu.replace('_', ' '))


# ------------------------------------------------------------- profiles
def _num(v):
    try:
        f = float(str(v).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _col(df, *needles, avoid=()):
    for c in df.columns:
        lc = str(c).lower()
        if all(n in lc for n in needles) and not any(a in lc for a in avoid):
            return c
    return None


def profile_rows(df):
    """[{'column', 'value', 'pct', 'gp', 'raw'}] for every row that can
    be crossed (behaviors, interests and demographic buckets), percent
    as written on the file."""
    out = []
    if df is None or 'Column' not in getattr(df, 'columns', ()) or 'Value' not in df.columns:
        return out
    bp = _col(df, 'penetration', 'row') or _col(df, 'penetration', avoid=('gen pop',))
    if bp is None:
        return out
    gpc = _col(df, 'gen pop', 'penetration')
    rawc = _col(df, 'raw')
    for i, (col, val, pct) in enumerate(zip(df['Column'], df['Value'], df[bp])):
        cu = norm_col(col)
        if not cu or cu in METADATA_COLS or cu in _NOT_CROSSED:
            continue
        p = _num(pct)
        if p is None or p <= 0:
            continue
        out.append({'column': cu, 'value': str(val).strip(), 'pct': min(p, 100.0),
                    'gp': (_num(df[gpc].iloc[i]) if gpc is not None else None),
                    'raw': (_num(df[rawc].iloc[i]) if rawc is not None else None)})
    return out


def genpop_map(gdf):
    """{(COLUMN, folded value): percent} from the US general population
    frame."""
    gp = {}
    if gdf is None or 'Column' not in getattr(gdf, 'columns', ()):
        return gp
    bp = _col(gdf, 'penetration', 'row') or _col(gdf, 'penetration', avoid=('gen pop',))
    if bp is None:
        return gp
    for col, val, pct in zip(gdf['Column'], gdf['Value'], gdf[bp]):
        cu = norm_col(col)
        p = _num(pct)
        if cu and cu not in METADATA_COLS and p is not None:
            gp[(cu, _fold(val))] = p
    return gp


def sample_of(df):
    """The profile's sample: the people in the data behind the audience."""
    try:
        rawc = _col(df, 'raw')
        cols = df['Column'].astype(str).str.strip().str.upper()
        for name in ('SAMPLE SIZE', 'BRAND INPUT'):
            hit = df.loc[cols == name]
            if len(hit) and rawc is not None:
                v = _num(hit[rawc].iloc[0])
                if v and v > 0:
                    return int(round(v))
    except Exception:
        pass
    return 0


_WINDOW_RX = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+TO\s+(\d{4})-(\d{2})-(\d{2})", re.I)
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def window_of(df):
    """{'start', 'end', 'label'} from the profile's SAMPLE SIZE row, or {}."""
    try:
        cols = df['Column'].astype(str).str.strip().str.upper()
        hit = df.loc[cols == 'SAMPLE SIZE', 'Value']
        m = _WINDOW_RX.search(str(hit.iloc[0])) if len(hit) else None
        if not m:
            return {}
        y1, m1, d1, y2, m2, d2 = (int(x) for x in m.groups())
        return {'start': f"{y1:04d}-{m1:02d}-{d1:02d}", 'end': f"{y2:04d}-{m2:02d}-{d2:02d}",
                'label': f"{_MONTHS[m1 - 1]} {d1}, {y1} to {_MONTHS[m2 - 1]} {d2}, {y2}"}
    except Exception:
        return {}


# -------------------------------------------------------------- binding
def _value_matches(val, want_fold):
    fv = _fold(val)
    if not fv or not want_fold:
        return False
    if fv == want_fold:
        return True
    if '/' in str(val):   # a compound row ('Disney+/Hulu') answers to either half
        return any(_fold(part) == want_fold for part in str(val).split('/'))
    return False


def _column_rank(cu, prefer):
    if cu in prefer:
        return (0, prefer.index(cu))
    if cu in COLUMN_PRIORITY:
        return (1, COLUMN_PRIORITY.index(cu))
    return (2, 0)


def _condition(row, rows, gp_map, kind=None):
    cu, val, pct = row['column'], row['value'], row['pct']
    f = _fold(val)
    # the same brand mirrored at one value across columns is one behavior
    twins = sorted({r['column'] for r in rows if _fold(r['value']) == f and abs(r['pct'] - pct) < 5e-5} | {cu})
    g = gp_map.get((cu, f)) if gp_map else None
    if g is None:
        g = next((gp_map.get((t, f)) for t in twins if gp_map and gp_map.get((t, f)) is not None), None)
    if g is None:
        g = row.get('gp')
    if g is not None and g <= 0:
        g = None
    kind = kind or ('demo' if cu in _DEMO_SET else ('interest' if cu == 'INTEREST' else 'brand'))
    return {'key': f"{twins[0]}|{f}", 'kind': kind, 'column': cu, 'value': val, 'label': pretty_value(val),
            'pct': round(pct, 4), 'gp': (round(g, 4) if g is not None else None)}


def category_condition(df, gdf, column, subject=None):
    """A whole column as one behavior ('any QSR'): its reach comes from
    the category reach module, never from here. None when that module is
    not on this build or carries no figure for the column."""
    cu = norm_col(column)
    try:
        try:
            from migration import category_rollup as _cr
        except Exception:
            import category_rollup as _cr  # type: ignore
        rolls = _cr.category_rollups(df, _cr.genpop_map_from_df(gdf) if gdf is not None else {}, subject=subject)
        r = rolls.get(_cr.norm_cat(cu)) if isinstance(rolls, dict) else None
        if not r or r.get('reach') is None:
            return None
        g = r.get('genpop')
        return {'key': f"ANY|{cu}", 'kind': 'category', 'column': cu, 'value': cu,
                'label': f"any {pretty_column(cu)} brand" if cu != 'QSR' else 'any QSR',
                'pct': round(float(r['reach']), 4), 'gp': (round(float(g), 4) if g else None),
                'brands': r.get('brands'), 'top': r.get('top')}
    except Exception:
        return None


def _nearest(rows, name, prefer, limit=3):
    want = _fold(name)
    wt = [t for t in _tokens(name) if len(t) >= 3]
    scored = {}
    near = _INTEREST_NEAR.get(' '.join(_tokens(name)))
    for r in rows:
        if r['column'] in _DEMO_SET:
            continue
        f = _fold(r['value'])
        if not f:
            continue
        rt = _tokens(r['value'])
        score = difflib.SequenceMatcher(None, want, f).ratio()
        if wt and rt[:len(wt)] == wt:
            score = max(score, 0.93)       # a short form of the row's name
        elif wt and any(t in rt for t in wt if len(t) >= 4):
            score = max(score, 0.62)
        if near and r['column'] == 'INTEREST' and r['value'].upper() in near:
            score = max(score, 0.97 - 0.01 * near.index(r['value'].upper()))
        if score < 0.6:
            continue
        if r['column'] in prefer:
            score += 0.05
        cur = scored.get(f)
        if cur is None or (score, -_column_rank(r['column'], prefer)[0]) > (cur[0], -_column_rank(cur[1]['column'], prefer)[0]):
            scored[f] = (score, r)
    best = sorted(scored.values(), key=lambda s: (-s[0], -s[1]['pct'], s[1]['value']))[:limit]
    return [{'column': r['column'], 'value': r['value'], 'label': pretty_value(r['value']), 'pct': round(r['pct'], 4)}
            for _, r in best]


def _column_for(name):
    """The column a plain phrase names ('fast food' -> QSR), or ''."""
    f = ' '.join(_tokens(name))
    f1 = re.sub(r"\b(?:brands?|chains?|restaurants?|services?|platforms?|apps?|sites?|companies|company)$", '', f).strip()
    for cu, als in COLUMN_ALIASES.items():
        folded = {a for a in als} | {a + 's' for a in als}
        if f in folded or (f1 and f1 in folded) or f == cu.lower() or f1 == cu.lower():
            return cu
    return ''


def bind(df, name, *, hint='', whole=False, genpop=None, gdf=None, subject=None):
    """Find the row a named behavior means on THIS profile.

    Returns {'status': 'bound', 'condition': {...}}, or
    {'status': 'missing', 'name', 'nearest': [...]} when the profile
    does not carry it (the caller says so and offers the nearest rows;
    nothing is ever substituted), or {'status': 'everyone', 'name'} when
    the row is the audience itself. ``hint`` is a HINT_COLUMNS key read
    off the verb; ``whole`` says the words asked for a whole column
    ('any QSR'). ``genpop`` is a ``genpop_map``; ``gdf`` the US frame
    (only a whole-column read needs it)."""
    name = ' '.join(str(name or '').split()).strip(' ?.!,;:"\'')
    rows = profile_rows(df)
    gp_map = genpop or {}
    if not name or not rows:
        return {'status': 'missing', 'name': name, 'nearest': []}
    prefer = tuple(HINT_COLUMNS.get(str(hint or ''), ()))
    cols_here = {r['column'] for r in rows}
    # a whole column: 'any QSR', 'a streaming service'
    col_named = _column_for(name)
    if whole or (col_named and not any(_value_matches(r['value'], _fold(name)) for r in rows)):
        cu = col_named or (norm_col(name) if norm_col(name) in cols_here else '')
        if cu and cu in cols_here and cu not in _DEMO_SET:
            cond = category_condition(df, gdf, cu, subject)
            if cond:
                return {'status': 'bound', 'condition': cond}
            return {'status': 'missing', 'name': name, 'nearest': [], 'column': cu, 'whole': True}
        if whole:
            return {'status': 'missing', 'name': name, 'nearest': _nearest(rows, name, prefer)}
    # a demographic bucket by its plain name
    fname = ' '.join(_tokens(name))
    for (dc, bucket), als in _DEMO_ALIASES.items():
        if fname in als or fname == bucket.lower():
            for r in rows:
                if r['column'].replace(' ', '_') == dc and _fold(r['value']) == _fold(bucket):
                    return {'status': 'bound', 'condition': _condition(r, rows, gp_map, kind='demo')}
    want = _fold(name)
    hits = [r for r in rows if _value_matches(r['value'], want)]
    if not hits:
        # the audience noun rode along ('Target shoppers', 'Netflix users')
        bare = re.sub(r"\s+(?:shoppers?|users?|viewers?|customers?|subscribers?|members?|buyers?|fans?|drivers?|owners?|"
                      r"listeners?|players?|watchers?|diners?|visitors?|brands?|interest|interests|content)$", '', name, flags=re.I)
        if bare != name:
            hits = [r for r in rows if _value_matches(r['value'], _fold(bare))]
    if not hits:
        return {'status': 'missing', 'name': name, 'nearest': _nearest(rows, name, prefer)}
    demo_hits = [r for r in hits if r['column'] in _DEMO_SET]
    beh_hits = [r for r in hits if r['column'] not in _DEMO_SET]
    pool = beh_hits or demo_hits
    best = sorted(pool, key=lambda r: (_column_rank(r['column'], prefer), -r['pct'], r['column']))[0]
    if best['pct'] >= PIN_AT:
        others = [r for r in pool if r['pct'] < PIN_AT]
        if not others:
            return {'status': 'everyone', 'name': name, 'label': pretty_value(best['value'])}
        best = sorted(others, key=lambda r: (_column_rank(r['column'], prefer), -r['pct'], r['column']))[0]
    return {'status': 'bound', 'condition': _condition(best, rows, gp_map)}


# ---------------------------------------------------------------- cross
def _index(a, g):
    if a is None or g is None or g < MIN_GENPOP_FOR_INDEX:
        return None
    return int(round(a / g * 100.0))


def _held_share(held, key, field):
    try:
        v = (held or {}).get(key, {}).get(field)
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def cross(conditions, sample, *, held=None, ties=None, salt=''):
    """The read for a group of behaviors on one profile.

    ``conditions``: bound conditions (``bind``) or dicts with 'key',
    'pct' (the audience share in percent) and 'gp' (the US share in
    percent, or None). ``sample``: the profile's sample. ``held``:
    {set key: {'share', 'gp'}} figures already on the record for this
    profile (fractions); they stand, and every new figure is held inside
    them. ``ties``: {condition key: 0..1} from a reasoning step; it can
    only loosen a behavior's tie, never tighten it. ``salt``: the
    profile's identity, for the count digits.

    Returns None for fewer than two distinct behaviors, else
    {'key', 'conditions' (in key order, each with 'share', 'gp_share',
    'index', 'times', 'loading', 'raw', 'us'), 'share', 'gp', 'index',
    'times', 'raw', 'us', 'unrelated', 'cells': {set key: {...}} for
    every sub-group of two or more, 'sample'}. Shares are fractions."""
    seen, conds = set(), []
    for c in conditions or ():
        if not isinstance(c, dict) or not c.get('key') or c['key'] in seen:
            continue
        p = _num(c.get('pct'))
        if p is None:
            continue
        seen.add(c['key'])
        g = _num(c.get('gp'))
        d = dict(c)
        d['share'] = max(0.0, min(1.0, p / 100.0))
        d['gp_share'] = (max(0.0, min(1.0, g / 100.0)) if g is not None and g > 0 else None)
        d['index'] = _index(d['share'], d['gp_share'])
        d['times'] = times_of(d['index'])
        tie = 1.0
        try:
            if ties and c['key'] in ties:
                tie = max(0.0, min(1.0, float(ties[c['key']])))
        except (TypeError, ValueError):
            tie = 1.0
        d['loading'] = round(loading(d['index']) * tie, 6)
        conds.append(d)
    conds.sort(key=lambda d: d['key'])
    if len(conds) < 2 or len(conds) > MAX_CONDITIONS:
        return None
    try:
        n = int(round(float(sample or 0)))
    except (TypeError, ValueError):
        n = 0
    by_key = {d['key']: d for d in conds}
    for d in conds:
        d['raw'] = _natural(d['share'] * n, salt, d['key'], 'raw') if n else 0
        d['us'] = _natural(d['raw'] / PANEL * US_POP, salt, d['key'], 'us') if n else 0
    held = held if isinstance(held, dict) else {}
    held_sets = {k: frozenset(k.split(' & ')) for k in held}
    shares = {frozenset([d['key']]): d['share'] for d in conds}
    gps = {frozenset([d['key']]): d['gp_share'] for d in conds}
    all_gp = all(d['gp_share'] is not None for d in conds)
    cells = {}
    for size in range(2, len(conds) + 1):
        for combo in itertools.combinations([d['key'] for d in conds], size):
            fs = frozenset(combo)
            sk = set_key(combo)
            members = [by_key[k] for k in combo]
            base = unrelated([m['share'] for m in members])

            def clamp(model, table, hfield, singles):
                subs = [table[fs - {k}] for k in combo]
                hi = min(subs)
                lo = max(0.0, sum(singles) - (len(singles) - 1))
                for i, j in itertools.combinations(combo, 2):
                    rest = fs - {i, j}
                    lo = max(lo, table[fs - {i}] + table[fs - {j}] - (table[rest] if rest else 1.0))
                if size == 3:
                    hi = min(hi, 1.0 - sum(singles) + sum(subs))
                # anything on the record for a wider group sits inside this one
                for hk, hset in held_sets.items():
                    if hset > fs:
                        hv = _held_share(held, hk, hfield)
                        if hv is not None:
                            lo = max(lo, hv)
                on_record = _held_share(held, sk, hfield)
                val = on_record if on_record is not None else model
                if lo > hi:
                    lo = hi
                return max(lo, min(hi, val)), on_record is not None

            share, kept = clamp(joint([m['share'] for m in members], [m['loading'] for m in members]),
                                shares, 'share', [m['share'] for m in members])
            shares[fs] = share
            gp = None
            if all(m['gp_share'] is not None for m in members):
                gp, _ = clamp(joint([m['gp_share'] for m in members], [m['loading'] for m in members]),
                              gps, 'gp', [m['gp_share'] for m in members])
                gps[fs] = gp
            raw = _natural(share * n, salt, sk, 'raw') if n else 0
            # a narrower group never counts more people than a wider one
            cap = min([by_key[k]['raw'] for k in combo] + [cells[set_key(fs - {k})]['raw'] for k in combo if size > 2])
            if n and raw > cap:
                raw = _natural(cap, salt, sk, 'cap')
            cells[sk] = {'key': sk, 'members': list(combo), 'share': share, 'gp': gp, 'index': _index(share, gp),
                         'times': times_of(_index(share, gp)), 'unrelated': base, 'raw': raw,
                         'us': (_natural(raw / PANEL * US_POP, salt, sk, 'us') if n else 0), 'on_record': kept}
    for sk, cell in cells.items():
        # the same guard on the US figure (digit moves can cross two near-equal counts)
        subs = [cells[set_key(frozenset(cell['members']) - {k})]['us'] for k in cell['members'] if len(cell['members']) > 2]
        cap = min([by_key[k]['us'] for k in cell['members']] + subs)
        if cell['us'] > cap:
            cell['us'] = _natural(cap, salt, sk, 'uscap')
    top = cells[set_key([d['key'] for d in conds])]
    return {'key': top['key'], 'conditions': conds, 'share': top['share'], 'gp': top['gp'] if all_gp else None,
            'index': top['index'] if all_gp else None, 'times': top['times'] if all_gp else '',
            'raw': top['raw'], 'us': top['us'], 'unrelated': top['unrelated'], 'cells': cells, 'sample': n,
            'on_record': top['on_record']}
