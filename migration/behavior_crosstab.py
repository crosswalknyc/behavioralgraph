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
How rows sit together is ONE law, written once in
migration/behavior_ties.py and read here and by the category reach
(migration/category_rollup.py): brand rows move with one common driver
at the category reach's own association; rows of an either/or column (a
carrier, an insurer) are a person's one pick plus looking around, so
two of them sit a little under the multiplied figure; interests and
demographic buckets tie by their index. The share doing every behavior
is read off the reaches of the behaviors pooled every which way, so for
any two rows either + both = A + B, and a whole column's reach is what
its own rows' crosses imply.

Properties (each one is tested):
- never above the smallest single share, never below
  max(0, sum of shares - (k - 1));
- the same for any order the behaviors are named in;
- nested: A and B and C never exceeds A and B, which never exceeds A,
  for every sub-group, and every sub-group is returned with the read;
- with the category reach: either + both = A + B for any two rows, to
  the person; a whole column crossed with one of its own rows is that
  row;
- the SAME law runs on the audience and on the US, so the index is like
  for like;
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

try:
    from . import behavior_ties as ties
    from . import category_rollup as _cr
except Exception:      # run as loose modules
    import behavior_ties as ties  # type: ignore
    import category_rollup as _cr  # type: ignore

__all__ = ['A_MAX', 'IDX_FULL', 'MAX_CONDITIONS', 'US_POP', 'PANEL', 'loading', 'joint', 'all_share', 'unrelated', 'profile_context',
           'cross', 'set_key', 'times_of', 'profile_rows', 'genpop_map', 'sample_of', 'window_of',
           'bind', 'category_condition', 'DEMO_COLS', 'pretty_column', 'pretty_value']

US_POP = 329_900_000
PANEL = 10_000_000
# The tie parameters live in migration/behavior_ties.py, one set for this
# read and the category reach.
A_MAX = ties.A_MAX
IDX_FULL = ties.IDX_FULL
IDX_FLOOR = ties.IDX_FLOOR
# A row at or above this share is the whole audience (the subject's own
# row), not a behavior to cross.
PIN_AT = 99.95
# Below this US share an index is not stated.
MIN_GENPOP_FOR_INDEX = 0.0005
MAX_CONDITIONS = 5

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
    """How much an interest or a demographic bucket moves with the common
    driver, from its index against the US (the one law's soft tie)."""
    return ties.soft_loading(index)


def unrelated(shares):
    """The figure the behaviors would give if they had nothing to do
    with one another (a fraction)."""
    out = 1.0
    for p in shares:
        out *= max(0.0, min(1.0, float(p)))
    return out


def _bounds(shares):
    ps = [max(0.0, min(1.0, float(p))) for p in shares]
    return max(0.0, sum(ps) - (len(ps) - 1)), (min(ps) if ps else 0.0)


def all_share(groups):
    """The share (a fraction) with at least one row of EVERY group, under
    the one law: the reaches of the groups pooled every which way, added
    and subtracted. ``groups``: a list of {row id: ties.Row}. Symmetric
    in its arguments by construction, so the order never matters; for
    two single rows it is A + B - (A or B), to the digit."""
    groups = [g for g in groups if g]
    if not groups:
        return 0.0
    if len(groups) == 1:
        return ties.reach_share(list(groups[0].values()))
    total = 0.0
    for size in range(1, len(groups) + 1):
        sign = 1.0 if size % 2 else -1.0
        for combo in itertools.combinations(range(len(groups)), size):
            pool = {}
            for i in combo:
                pool.update(groups[i])
            total += sign * ties.reach_share([pool[k] for k in sorted(pool)])
    singles = [ties.reach_share(list(g.values())) for g in groups]
    lo, hi = _bounds(singles)
    return max(lo, min(hi, total))


def joint(shares, loadings):
    """The share doing every one of single behaviors given as shares and
    soft loadings (kept for callers that hold plain numbers)."""
    groups = [{i: ties.Row(p, 'soft', a)} for i, (p, a) in enumerate(sorted(zip(shares, loadings)))]
    if any(float(p) <= 0.0 for p in shares):
        return 0.0
    return all_share(groups)


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


def _natural(n, *salt, also=None):
    """A count that never ends in a zero and never carries three zeros
    in a row in its thousands and hundreds places; moved by a hair,
    always downward (we anchor low), the same on every run. ``also`` is
    one more test the figure must pass (a pair's either count must read
    naturally too)."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0

    def ok(x):
        return x % 10 != 0 and not (x > 100_000 and '000' in str(x)[-6:-2]) and (also is None or also(x))
    if ok(v):
        return v
    h = _salt_int(*salt, v)
    if not (v > 100_000 and '000' in str(v)[-6:-2]):
        # only the last digit (or the pair's either count) reads wrong: a step of one to nine
        for i in range(9):
            cand = v - (((h + i) % 9) + 1)
            if cand > 0 and ok(cand):
                return cand
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
    n = len(df)
    blank = [None] * n
    rawc = _col(df, 'raw')
    usc = _col(df, 'projection')
    raws = list(df[rawc]) if rawc is not None else blank
    uss = list(df[usc]) if usc is not None else blank
    for col, val, pct, raw, us in zip(df['Column'], df['Value'], df[bp], raws, uss):
        cu = norm_col(col)
        if not cu or cu in METADATA_COLS or cu in _NOT_CROSSED:
            continue
        p = _num(pct)
        if p is None or p <= 0:
            continue
        out.append({'column': cu, 'value': str(val).strip(), 'pct': min(p, 100.0), 'raw': _num(raw), 'us': _num(us)})
    return out


def genpop_map(gdf):
    """{(COLUMN, folded value): percent} from the US general population
    frame, in the category reach's own keys (one lookup for both reads)."""
    try:
        return _cr.genpop_map_from_df(gdf) if gdf is not None else {}
    except Exception:
        return {}


def _gp(gp_map, column, value):
    v = (gp_map or {}).get((_cr.norm_cat(column), _cr.norm_brand(value)))
    return v if v is not None and v > 0 else None


def profile_context(df, gp_map=None, subject=None):
    """What a bind needs from the profile, read once: the rows, the US
    lookup, and each either/or column's sums (the one law reads those)."""
    try:
        sums = _cr.column_sums(df, gp_map or {}, subject)
    except Exception:
        sums = {}
    return {'rows': profile_rows(df), 'gp': gp_map or {}, 'sums': sums, 'subject': subject}


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


def _is_soft(cu):
    """Interests and demographic buckets: rows the category reach does
    not roll up; they tie by their index."""
    return cu in _DEMO_SET or _cr.norm_cat(cu) in _cr.NON_BRAND_COLS or _cr.norm_cat(cu) in _cr.DEMO_COLS


def _condition(row, ctx, kind=None):
    rows, gp_map = ctx['rows'], ctx['gp']
    cu, val, pct = row['column'], row['value'], row['pct']
    f = _fold(val)
    # the same brand mirrored at one value across columns is one behavior
    twins = sorted({r['column'] for r in rows if _fold(r['value']) == f and abs(r['pct'] - pct) < 5e-5} | {cu})
    g = _gp(gp_map, cu, val)
    if g is None:
        g = next((x for x in (_gp(gp_map, t, val) for t in twins) if x is not None), None)
    kind = kind or ('demo' if cu in _DEMO_SET else ('interest' if cu == 'INTEREST' else 'brand'))
    nc = _cr.norm_cat(cu)
    law = 'soft' if _is_soft(cu) else ('pick' if ties.is_either_or(nc) else 'brand')
    sums = (ctx.get('sums') or {}).get(nc) or (0.0, 0.0)
    return {'key': f"{twins[0]}|{f}", 'kind': kind, 'column': cu, 'value': val, 'label': pretty_value(val),
            'pct': round(pct, 4), 'gp': (round(g, 4) if g is not None else None), 'law': law,
            'column_sum': (float(sums[0]), float(sums[1])),
            # the row's own counts as the profile file prints them (the page shows these)
            'file_raw': (int(round(row['raw'])) if row.get('raw') and row['raw'] > 0 else None),
            'file_us': (int(round(row['us'])) if row.get('us') and row['us'] > 0 else None)}


_ANY_LABEL = {'QSR': 'QSR', 'WHERE THEY SHOP': 'retailer', 'WHERE THEY DINE': 'restaurant',
              'STREAMING/PLATFORM': 'streaming service', 'STREAMING/MUSIC': 'music service', 'SOCIAL MEDIA': 'social platform',
              'BANKING': 'bank', 'DIGITAL BANKING': 'digital bank', 'TELECOM': 'telecom brand', 'INSURANCE': 'insurance brand',
              'AUTOMOBILE': 'automobile brand', 'TRAVEL': 'travel brand', 'BETTING': 'betting brand', 'GAMES': 'game',
              'WORKOUT FACILITY': 'gym', 'PHARMACY': 'pharmacy', 'CREDIT PROVIDER': 'credit provider', 'PODCAST': 'podcast'}


def any_label(column):
    """'any QSR', 'any retailer', 'any Technology Brand': a whole column
    in plain words."""
    cu = _cr.norm_cat(column)
    plain = _ANY_LABEL.get(cu)
    if not plain:
        plain = pretty_column(cu)
        if not re.search(r"\b(?:brands?|services?|platforms?|teams?|providers?)$", plain, re.I):
            plain += ' brand'
    return f"any {plain}"


def category_condition(df, ctx, column, subject=None):
    """A whole column as one behavior ('any QSR'): the people with at
    least one of its rows. Its share, US share and index are the category
    reach's own figures (migration/category_rollup.py), and it carries
    the column's rows so a cross with it is read under the same law.
    None when the column has no rows to roll up."""
    cu = _cr.norm_cat(column)
    try:
        gp_map = ctx['gp']
        roll = _cr.category_rollups(df, gp_map, subject=subject).get(cu)
        rows = _cr.profile_rows(df, subject).get(cu) or []
        if not roll or not rows:
            return None
        members = [(f"{cu}|{_cr.norm_brand(b)}", float(p), _gp(gp_map, cu, b)) for b, p in rows]
        sums = (ctx.get('sums') or {}).get(cu) or (sum(m[1] for m in members), sum(m[2] for m in members if m[2]))
        return {'key': f"ANY|{cu}", 'kind': 'category', 'column': cu, 'value': cu, 'label': any_label(cu),
                'pct': float(roll['reach']), 'gp': (float(roll['genpop']) if roll.get('genpop') else None),
                'index_stated': roll.get('index'), 'law': 'pick' if ties.is_either_or(cu) else 'brand',
                'members': members, 'column_sum': (float(sums[0]), float(sums[1])),
                'brands': roll.get('brands'), 'top': roll.get('top')}
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
    return [{'column': r['column'], 'value': r['value'], 'label': pretty_value(r['value']), 'pct': round(r['pct'], 4),
             'score': round(sc, 3)} for sc, r in best]


def _column_for(name):
    """The column a plain phrase names ('fast food' -> QSR), or ''."""
    f = ' '.join(_tokens(name))
    f1 = re.sub(r"\b(?:brands?|chains?|restaurants?|services?|platforms?|apps?|sites?|companies|company)$", '', f).strip()
    for cu, als in COLUMN_ALIASES.items():
        folded = {a for a in als} | {a + 's' for a in als}
        if f in folded or (f1 and f1 in folded) or f == cu.lower() or f1 == cu.lower():
            return cu
    return ''


def bind(df, name, *, hint='', whole=False, genpop=None, subject=None, context=None, column=None, loose=False):
    """Find the row a named behavior means on THIS profile.

    Returns {'status': 'bound', 'condition': {...}}, or
    {'status': 'missing', 'name', 'nearest': [...]} when the profile
    does not carry it (the caller says so and offers the nearest rows;
    nothing is ever substituted), or {'status': 'everyone', 'name'} when
    the row is the audience itself. ``hint`` is a HINT_COLUMNS key read
    off the verb; ``whole`` says the words asked for a whole column
    ('any QSR'). ``genpop`` is a ``genpop_map``; ``context`` a
    ``profile_context`` when several behaviors bind on one profile.
    ``column`` binds the row in exactly that column (a pick on a page),
    never another column's row of the same name. ``loose`` also accepts a
    plain short form of one row's name ('Tech' for Technology); only a
    ceiling on a cut's size reads loosely, never an answer."""
    name = ' '.join(str(name or '').split()).strip(' ?.!,;:"\'')
    ctx = context or profile_context(df, genpop or {}, subject)
    rows = ctx['rows']
    if not name or not rows:
        return {'status': 'missing', 'name': name, 'nearest': []}
    if column:
        cu = norm_col(column)
        if whole:
            cond = category_condition(df, ctx, cu, subject) if cu not in _DEMO_SET else None
            return {'status': 'bound', 'condition': cond} if cond else {'status': 'missing', 'name': name, 'nearest': [],
                                                                         'column': cu, 'whole': True}
        hit = [r for r in rows if r['column'] == cu and _value_matches(r['value'], _fold(name))]
        if not hit:
            return {'status': 'missing', 'name': name, 'nearest': _nearest(rows, name, (cu,))}
        if hit[0]['pct'] >= PIN_AT:
            return {'status': 'everyone', 'name': name, 'label': pretty_value(hit[0]['value'])}
        return {'status': 'bound', 'condition': _condition(hit[0], ctx)}
    prefer = tuple(HINT_COLUMNS.get(str(hint or ''), ()))
    cols_here = {r['column'] for r in rows}
    # a whole column: 'any QSR', 'a streaming service'
    col_named = _column_for(name)
    if whole or (col_named and not any(_value_matches(r['value'], _fold(name)) for r in rows)):
        cu = col_named or (norm_col(name) if norm_col(name) in cols_here else '')
        if cu and cu in cols_here and cu not in _DEMO_SET:
            cond = category_condition(df, ctx, cu, subject)
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
                    return {'status': 'bound', 'condition': _condition(r, ctx, kind='demo')}
    want = _fold(name)
    hits = [r for r in rows if _value_matches(r['value'], want)]
    bare = name
    if not hits:
        # the audience noun rode along ('Target shoppers', 'Netflix users')
        bare = re.sub(r"\s+(?:shoppers?|users?|viewers?|customers?|subscribers?|members?|buyers?|fans?|drivers?|owners?|"
                      r"listeners?|players?|watchers?|diners?|visitors?|brands?|interest|interests|content)$", '', name, flags=re.I)
        if bare != name:
            hits = [r for r in rows if _value_matches(r['value'], _fold(bare))]
    if not hits:
        near = _nearest(rows, name, prefer)
        if loose:
            near_bare = _nearest(rows, bare, prefer) if bare != name else near
            top = max((near_bare or []) + (near or []), key=lambda x: x.get('score', 0), default=None)
            if top and top.get('score', 0) >= 0.93:
                hits = [r for r in rows if r['column'] == top['column'] and r['value'] == top['value']]
        if not hits:
            return {'status': 'missing', 'name': name, 'nearest': near}
    demo_hits = [r for r in hits if r['column'] in _DEMO_SET]
    beh_hits = [r for r in hits if r['column'] not in _DEMO_SET]
    pool = beh_hits or demo_hits
    best = sorted(pool, key=lambda r: (_column_rank(r['column'], prefer), -r['pct'], r['column']))[0]
    if best['pct'] >= PIN_AT:
        others = [r for r in pool if r['pct'] < PIN_AT]
        if not others:
            return {'status': 'everyone', 'name': name, 'label': pretty_value(best['value'])}
        best = sorted(others, key=lambda r: (_column_rank(r['column'], prefer), -r['pct'], r['column']))[0]
    return {'status': 'bound', 'condition': _condition(best, ctx)}


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


def _law_group(cond, side, matched_only=False):
    """{row id: ties.Row} for one behavior on one side (0 the audience, 1
    the US). A whole column carries all its rows; on the US side, and for
    the like-for-like audience figure, only the rows the US file has."""
    col = _cr.norm_cat(cond.get('column'))
    sums = cond.get('column_sum') or (0.0, 0.0)
    csum = float(sums[side] or 0.0) / 100.0
    if cond.get('kind') == 'category':
        out = {}
        for rid, a, g in cond.get('members') or ():
            if (side == 1 or matched_only) and g is None:
                continue
            v = a if side == 0 else g
            if v is None or v <= 0:
                continue
            out[rid] = ties.Row(min(v, 100.0) / 100.0, cond.get('law') or 'brand', 0.0, col, csum)
        return out
    v = cond.get('share') if side == 0 else cond.get('gp_share')
    if v is None:
        return {}
    return {cond['key']: ties.Row(v, cond.get('law') or 'soft', cond.get('loading') or 0.0, col, csum)}


def cross(conditions, sample, *, held=None, salt=''):
    """The read for a group of behaviors on one profile.

    ``conditions``: bound conditions (``bind``). ``sample``: the
    profile's sample. ``held``: {set key: {'share', 'gp'}} figures
    already on the record for this profile (fractions); they stand, and
    every new figure is held inside them. ``salt``: the profile's
    identity, for the count digits.

    Returns None for fewer than two distinct behaviors, else
    {'key', 'conditions' (in key order, each with 'share', 'gp_share',
    'index', 'times', 'raw', 'us'), 'share', 'gp', 'index', 'times',
    'raw', 'us', 'unrelated', 'cells': {set key: {...}} for every
    sub-group of two or more (a pair also carries 'either': the people
    with at least one of the two, so either + both = A + B to the
    person), 'sample'}. Shares are fractions."""
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
        d['law'] = c.get('law') or 'soft'
        if d.get('kind') == 'category':
            d['share'] = ties.reach_share(list(_law_group(dict(d, share=0), 0).values()))
            gp_rows = list(_law_group(d, 1).values())
            d['gp_share'] = ties.reach_share(gp_rows) if gp_rows else None
            d['like_share'] = ties.reach_share(list(_law_group(d, 0, True).values())) if gp_rows else None
            d['index'] = c.get('index_stated') if c.get('index_stated') is not None else _index(d['like_share'], d['gp_share'])
        else:
            d['share'] = max(0.0, min(1.0, p / 100.0))
            d['gp_share'] = (max(0.0, min(1.0, g / 100.0)) if g is not None and g > 0 else None)
            d['like_share'] = d['share']
            d['index'] = _index(d['share'], d['gp_share'])
        d['times'] = times_of(d['index'])
        d['loading'] = ties.soft_loading(d['index']) if d['law'] == 'soft' else 0.0
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
        # a single row prints the profile file's own counts, exactly as the page does
        want = d['share'] * n
        fr, fu = d.get('file_raw'), d.get('file_us')
        if n and fr and abs(fr - want) <= max(2.0, want * 0.001):
            d['raw'] = int(fr)
            d['us'] = int(fu) if fu and abs(fu - fr / PANEL * US_POP) <= max(40.0, fu * 0.001) else int(round(fr / PANEL * US_POP))
        else:
            d['raw'] = _natural(want, salt, d['key'], 'raw') if n else 0
            d['us'] = _natural(d['raw'] / PANEL * US_POP, salt, d['key'], 'us') if n else 0
    held = held if isinstance(held, dict) else {}
    held_sets = {k: frozenset(k.split(' & ')) for k in held}
    shares = {frozenset([d['key']]): d['share'] for d in conds}
    likes = {frozenset([d['key']]): d['like_share'] for d in conds}
    gps = {frozenset([d['key']]): d['gp_share'] for d in conds}
    all_gp = all(d['gp_share'] is not None for d in conds)
    side_a = {d['key']: _law_group(d, 0) for d in conds}
    side_like = {d['key']: _law_group(d, 0, True) for d in conds}
    side_g = {d['key']: _law_group(d, 1) for d in conds}
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
                if hfield:
                    for hk, hset in held_sets.items():
                        if hset > fs:
                            hv = _held_share(held, hk, hfield)
                            if hv is not None:
                                lo = max(lo, hv)
                on_record = _held_share(held, sk, hfield) if hfield else None
                val = on_record if on_record is not None else model
                if lo > hi:
                    lo = hi
                return max(lo, min(hi, val)), on_record is not None

            share, kept = clamp(all_share([side_a[k] for k in combo]), shares, 'share', [m['share'] for m in members])
            shares[fs] = share
            gp = like = None
            if all(m['gp_share'] is not None for m in members):
                gp, _ = clamp(all_share([side_g[k] for k in combo]), gps, 'gp', [m['gp_share'] for m in members])
                gps[fs] = gp
                # the audience figure over the same rows the US side has: equal to the share
                # unless a whole column carries rows the US file does not
                if kept or all(side_like[k] == side_a[k] for k in combo):
                    like = share
                else:
                    like, _ = clamp(all_share([side_like[k] for k in combo]), likes, '', [m['like_share'] for m in members])
                likes[fs] = like
            raw_cap = min([by_key[k]['raw'] for k in combo] + [cells[set_key(fs - {k})]['raw'] for k in combo if size > 2])
            either = None
            if size == 2:
                a_, b_ = members
                ok = (lambda x: (a_['raw'] + b_['raw'] - x) % 10 != 0) if n else None
                raw = _natural(min(share * n, raw_cap), salt, sk, 'raw', also=ok) if n else 0
            else:
                raw = _natural(share * n, salt, sk, 'raw') if n else 0
                if n and raw > raw_cap:
                    raw = _natural(raw_cap, salt, sk, 'cap')
            us = _natural(raw / PANEL * US_POP, salt, sk, 'us') if n else 0
            if size == 2:
                us_cap = min(a_['us'], b_['us'])
                if us > us_cap:
                    us = us_cap
                us = _natural(us, salt, sk, 'us2', also=(lambda x: (a_['us'] + b_['us'] - x) % 10 != 0)) if n else 0
                either = {'share': a_['share'] + b_['share'] - share, 'raw': a_['raw'] + b_['raw'] - raw,
                          'us': a_['us'] + b_['us'] - us}
            cells[sk] = {'key': sk, 'members': list(combo), 'share': share, 'gp': gp, 'index': _index(like, gp),
                         'times': times_of(_index(like, gp)), 'unrelated': base, 'raw': raw, 'us': us,
                         'on_record': kept, 'either': either}
    for sk, cell in cells.items():
        if len(cell['members']) <= 2:
            continue
        # a narrower group never counts more US people than a wider one (digit moves can cross two near-equal counts)
        subs = [cells[set_key(frozenset(cell['members']) - {k})]['us'] for k in cell['members']]
        cap = min([by_key[k]['us'] for k in cell['members']] + subs)
        if cell['us'] > cap:
            cell['us'] = _natural(cap, salt, sk, 'uscap')
    top = cells[set_key([d['key'] for d in conds])]
    return {'key': top['key'], 'conditions': conds, 'share': top['share'], 'gp': top['gp'] if all_gp else None,
            'index': top['index'] if all_gp else None, 'times': top['times'] if all_gp else '',
            'raw': top['raw'], 'us': top['us'], 'unrelated': top['unrelated'], 'cells': cells, 'sample': n,
            'on_record': top['on_record']}
