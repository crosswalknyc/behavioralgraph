"""Category reach: the share of an audience that engages with at least
one brand in a group, with the same figure for the US and the index.

2026-11-11, Armine: the Wicked Tuna deck grouped purchase behavior into
four categories with example brands under each, and she asked for the
overall percentage and index of each category, the same on the Profiles
page ("the percentage of viewers engaging with ... automobiles, alongside
the index"). A profile carries one penetration per brand. The reach of a
group of brands is none of the easy answers: the sum counts a person once
per brand, and the largest brand ignores everyone who only touched a
smaller one.

ONE estimator, here, for every surface (the deck, the Profiles page, a
Prometheus answer). Nothing else may reimplement it.

The estimator
-------------
Brands inside one category move together: a person who shops one truck
brand is far likelier to shop a second. Each brand's penetration p_i is
held exactly, and the brands share one common driver with a fixed
pairwise association RHO (a one-factor model). The reach is the share of
people with at least one brand under that model, integrated on a fixed
grid, so the same inputs always give the same output.

Properties (each one is tested):
- never below the largest brand in the group;
- never above the sum of the brands, 100, or the figure the brands would
  give if they were unrelated to one another;
- adding a brand never lowers it;
- well below the unrelated-brands figure, on purpose: Jenna's standing
  rule is that we anchor low (calibrate-low-anchor-to-actuals);
- the SAME function with the SAME constants runs on the audience and on
  the US general population, so the index is like for like;
- a category whose leader is already near-universal never prints 100.0:
  the figure stops at CAP unless a single brand is itself higher.

It is computed when it is read. Nothing is added to a profile file or to
Gen Pop.

Scope: behavioral groups only. Demographics (they sum to 100), metadata
rows, location, and the subject's own row (a 100 self-pin) are left out.
A cross of several behaviors (people who do A and B and C) is a different
read and lives in prometheus/crosstab.py; the two stay coherent because a
group's reach here is never below any one brand in it.
"""
from __future__ import annotations

import math
import re
import unicodedata
from statistics import NormalDist

import numpy as np

try:
    from . import behavior_ties as _ties
except Exception:      # run as a loose module
    import behavior_ties as _ties  # type: ignore

# ------------------------------------------------------------ constants
# One association for every group, both sides. Higher = brands overlap
# more = lower reach. 0.70 lands toward the low end of the defensible
# range (the unrelated-brands figure is the ceiling, the largest brand
# is the floor).
RHO = _ties.RHO       # one definition, shared with the cross of behaviors (migration/behavior_ties.py)
# A group's reach stops here unless one brand in it is higher on its own.
CAP = _ties.CAP * 100.0
# A row at or above this is a pin (the subject's own row), not a brand.
PIN_AT = 99.95
# Below this US reach an index is not stated.
MIN_GENPOP_FOR_INDEX = 0.05

_GRID = np.arange(-8.0, 8.0 + 1e-9, 0.1)
_W = np.exp(-0.5 * _GRID * _GRID)
_W = _W / _W.sum()
_SQ_RHO = math.sqrt(RHO)
_SQ_1M = math.sqrt(1.0 - RHO)
_ND = NormalDist()

METADATA_COLS = frozenset((
    'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT',
    'INPUT_METADATA', 'INPUT METADATA',
))
DEMO_COLS = frozenset((
    'AGE', 'GENDER', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION',
    'PARENTAL STATUS', 'PARENTAL_STATUS', 'RELATIONSHIP',
    'RELATIONSHIP STATUS', 'SEXUAL ORIENTATION', 'SEXUAL_ORIENTATION',
    'AGE OF CHILDREN', 'AGE_OF_CHILDREN', 'NUMBER OF CHILDREN',
    'NUMBER_OF_CHILDREN', 'PRIMARY LANGUAGE', 'PRIMARY_LANGUAGE',
    'LOCATION', 'AVID FAN', 'CASUAL FAN',
))
# Not a set of brands a person engages with one by one: the interest
# list and the generic purchase categories carry their own rows.
NON_BRAND_COLS = frozenset((
    'INTEREST', 'INTERESTS', 'MOST PURCHASED CATEGORIES',
    'ORGANIZATIONAL MEMBERSHIPS',
))
SKIP_COLS = METADATA_COLS | DEMO_COLS | NON_BRAND_COLS

# Where a named brand is looked up first when a group is a list of names
# (the purchase family leads; people and places come last).
CLUSTER_PRIORITY = (
    'MOST PURCHASED BRANDS', 'CPG', 'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS',
    'HOME/OUTDOOR', 'ACCESSORIES', 'PETS', 'TOYS', 'TECHNOLOGY BRAND',
    'TECHNOLOGY/DEVICE', 'HEAVY MACHINERY', 'BEVERAGE', 'AUTOMOBILE',
    'AUTOMOTIVE PARTS', 'WHERE THEY SHOP', 'QSR', 'WHERE THEY DINE',
    'TRAVEL', 'INSURANCE', 'BANKING', 'DIGITAL BANKING', 'CREDIT PROVIDER',
    'INVESTMENTS', 'TRADING', 'TELECOM', 'BETTING', 'PHARMACY',
    'WORKOUT FACILITY', 'TICKETING', 'STREAMING/PLATFORM',
    'STREAMING/MUSIC', 'SOCIAL MEDIA', 'APP/PLATFORM USAGE',
    'APP/PLATFORM', 'SEARCH ENGINE/AI', 'MEDIA', 'BROADCAST/CABLE',
    'GAMES', 'EVENTS', 'VENUE', 'AMUSEMENT PARKS', 'MOVIE THEATER',
)
_PEOPLE_HINT = re.compile(
    r"TALENT|ACTOR|ATHLETE|MUSICIAN|CREATOR|INFLUENCER|HOST/|COMEDIAN|"
    r"PODCASTER|POLITICS|WRITER|LEADERS|CONTENT$|COLLEGE", re.I)


# ----------------------------------------------------------------- math
def _phi(x):
    """Standard normal CDF on an array (fractional error under 1.2e-7
    everywhere, tails included)."""
    z = np.abs(x) * 0.7071067811865476
    t = 1.0 / (1.0 + 0.5 * z)
    poly = (-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (
        0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (
            -1.13520398 + t * (1.48851587 + t * (
                -0.82215223 + t * 0.17087277)))))))))
    erfc = t * np.exp(poly)
    half = 0.5 * erfc
    return np.where(x >= 0, 1.0 - half, half)


def _clean(pcts):
    out = []
    for p in pcts or ():
        try:
            v = float(p)
        except (TypeError, ValueError):
            continue
        if v != v or v <= 0.0:
            continue
        out.append(min(v, 100.0))
    return out


def unrelated_reach(pcts):
    """The reach the brands would have if they had nothing to do with
    one another (percent). The ceiling, never the answer."""
    ps = _clean(pcts)
    if not ps:
        return 0.0
    return 100.0 * (1.0 - float(np.prod(1.0 - np.asarray(ps) / 100.0)))


def union_reach(pcts):
    """Share of people with at least one of the brands (percent, not
    rounded). ``pcts`` are per-brand penetrations in percent."""
    ps = _clean(pcts)
    if not ps:
        return 0.0
    top = max(ps)
    if len(ps) == 1:
        return top
    arr = np.clip(np.asarray(ps, dtype=float) / 100.0, 1e-9, 1.0 - 1e-9)
    thr = np.fromiter((_ND.inv_cdf(float(p)) for p in arr), dtype=float,
                      count=len(arr))
    with np.errstate(divide='ignore', over='ignore', invalid='ignore'):
        log_none = np.zeros_like(_GRID)
        # in slices, so a 6,000-row category stays small in memory
        for i in range(0, len(thr), 2048):
            q = _phi((thr[i:i + 2048, None] + _SQ_RHO * _GRID[None, :])
                     / _SQ_1M)
            log_none += np.log1p(-np.minimum(q, 1.0)).sum(axis=0)
        none = np.exp(log_none)
    est = 100.0 * float(((1.0 - none) * _W).sum())
    ceiling = min(100.0, sum(ps), unrelated_reach(ps))
    est = min(est, ceiling)
    est = min(est, CAP)
    return max(est, top)


# -------------------------------------------------------------- roll-up
def norm_cat(c):
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def norm_brand(b):
    s = unicodedata.normalize('NFKD', str(b or ''))
    s = s.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def _num(v):
    try:
        f = float(str(v).replace('%', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _bp_col(df):
    cols = [str(c) for c in df.columns]
    for c in cols:
        if 'penetration' in c.lower() and 'row' in c.lower():
            return c
    for c in cols:
        if 'penetration' in c.lower() and 'gen pop' not in c.lower():
            return c
    return None


def times_of(index):
    """The plain comparison beside an index: 212 -> '2.1x the US
    average'; within five points of 100 -> 'in line with the US
    average'."""
    if index is None:
        return ''
    if 95 <= index <= 105:
        return 'in line with the US average'
    return f"{index / 100.0:.1f}x the US average"


def rollup_pairs(pairs, name='', column_sums=None):
    """Roll one group up.

    ``pairs``: iterable of (brand, audience_pct, genpop_pct or None) or
    (brand, audience_pct, genpop_pct or None, COLUMN). ``column_sums``:
    {COLUMN: (sum of the column's audience percents, sum of its US
    percents)} for an either/or column when the group is only part of it
    (a named list of brands); a whole column needs none.
    Returns {'name', 'reach', 'genpop', 'index', 'times', 'brands',
    'matched', 'top', 'top_pct'} or None when no brand carries a
    penetration. ``reach`` and ``genpop`` are one-decimal percents,
    ``index`` a whole number (100 = US average) or None when the US
    figure cannot carry one.

    The index is like for like: both sides are rolled up over the brands
    the US file also carries. ``reach`` covers every brand in the group,
    so it is never below any one of them.
    """
    seen = {}
    for item in pairs or ():
        brand, a, g = item[0], item[1], item[2]
        col = norm_cat(item[3]) if len(item) > 3 else ''
        a = _num(a)
        if a is None or a <= 0:
            continue
        key = norm_brand(brand)
        if not key:
            continue
        g = _num(g)
        if g is not None and g <= 0:
            g = None
        prev = seen.get(key)
        if prev is None or a > prev[1]:
            seen[key] = (str(brand), min(a, 100.0), g, col)
    if not seen:
        return None
    rows = sorted(seen.values(), key=lambda r: (-r[1], r[0]))
    all_a = [r[1] for r in rows]
    matched = [r for r in rows if r[2] is not None]
    # A column where a person typically holds one row at a time (a
    # carrier, an insurer) is read under the either/or part of the one
    # law (migration/behavior_ties.py), the same on both sides, so the
    # reach and a cross of two of its rows add up. Every other group is
    # the fixed association above, unchanged.
    either_or = any(_ties.is_either_or(r[3]) for r in rows)
    if either_or:
        sums = dict(column_sums or {})
        for c in {r[3] for r in rows if _ties.is_either_or(r[3])}:
            if c not in sums:
                sums[c] = (sum(r[1] for r in rows if r[3] == c),
                           sum(r[2] for r in matched if r[3] == c))

        def reach_of(use, side):
            law = [_ties.Row(r[1 + side] / 100.0,
                             'pick' if _ties.is_either_or(r[3]) else 'brand', 0.0, r[3],
                             (sums.get(r[3]) or (0.0, 0.0))[side] / 100.0)
                   for r in use]
            return 100.0 * _ties.reach_share(law)
    else:
        def reach_of(use, side):
            return union_reach([r[1 + side] for r in use])
    reach_all = reach_of(rows, 0)
    out = {
        'name': str(name or ''),
        'reach': round(reach_all, 1),
        'genpop': None, 'index': None, 'times': '',
        'brands': len(rows), 'matched': len(matched),
        'top': rows[0][0], 'top_pct': round(rows[0][1], 1),
    }
    if matched:
        gp = reach_of(matched, 1)
        a_m = reach_all if len(matched) == len(rows) else reach_of(matched, 0)
        gp1 = round(gp, 1)
        out['genpop'] = gp1
        if gp1 >= MIN_GENPOP_FOR_INDEX:
            out['index'] = int(round(round(a_m, 1) / gp1 * 100.0))
            out['times'] = times_of(out['index'])
    return out


def subject_names(df):
    """The subject's own names on a profile (its SUBJECT row), folded."""
    names = set()
    try:
        cols = df['Column'].astype(str).str.strip().str.upper()
        for v in df.loc[cols == 'SUBJECT', 'Value'].astype(str):
            k = norm_brand(v)
            if k:
                names.add(k)
    except Exception:
        pass
    return names


def _is_own_row(value_key, pct, own):
    return pct >= PIN_AT or (value_key in own)


def profile_rows(df, subject=None):
    """{COLUMN: [(brand, pct)]} for every behavioral column, the
    subject's own row and every pin left out, largest first."""
    bp = _bp_col(df)
    if bp is None or 'Column' not in df.columns or 'Value' not in df.columns:
        return {}
    # The file's own SUBJECT row names the subject, so every surface
    # leaves out the same row whatever display name it was handed. A
    # caller's name only stands in when the file carries no SUBJECT row.
    own = subject_names(df)
    if not own:
        for s in ([subject] if isinstance(subject, str) else (subject or ())):
            k = norm_brand(str(s).split(' - ')[0])
            if k:
                own.add(k)
    out = {}
    for col, val, raw in zip(df['Column'], df['Value'], df[bp]):
        cu = norm_cat(col)
        if not cu or cu in SKIP_COLS:
            continue
        p = _num(raw)
        if p is None or p <= 0:
            continue
        if _is_own_row(norm_brand(val), p, own):
            continue
        out.setdefault(cu, []).append((str(val), min(p, 100.0)))
    for cu in out:
        out[cu].sort(key=lambda r: (-r[1], r[0]))
    return out


def genpop_map_from_df(gdf):
    """{(COLUMN, folded brand): pct} from the Gen Pop frame."""
    gp = {}
    bp = _bp_col(gdf) if gdf is not None else None
    if bp is None:
        return gp
    for col, val, raw in zip(gdf['Column'], gdf['Value'], gdf[bp]):
        cu = norm_cat(col)
        if not cu or cu in METADATA_COLS:
            continue
        p = _num(raw)
        if p is not None:
            gp[(cu, norm_brand(val))] = p
    return gp


def category_rollups(df, genpop_map, subject=None, min_brands=1):
    """Every behavioral column on the profile, rolled up:
    {COLUMN: rollup dict}."""
    out = {}
    gp = genpop_map or {}
    for cu, rows in profile_rows(df, subject).items():
        if len(rows) < min_brands:
            continue
        r = rollup_pairs(
            ((b, p, gp.get((cu, norm_brand(b))), cu) for b, p in rows), name=cu)
        if r:
            out[cu] = r
    return out


def _tokens(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = s.encode('ascii', 'ignore').decode('ascii').lower().replace("'", '')
    return [t for t in re.split(r'[^a-z0-9]+', s) if t]


def _column_rank(cu, prefer):
    pref = [norm_cat(c) for c in (prefer or ())]
    if cu in pref:
        return (0, pref.index(cu))
    if cu in CLUSTER_PRIORITY:
        return (1, CLUSTER_PRIORITY.index(cu))
    if _PEOPLE_HINT.search(cu):
        return (3, 0)
    return (2, 0)


def resolve_brands(df, brands, genpop_map, subject=None, prefer=()):
    """Find each named brand's row on the profile.

    Returns (pairs, missing): pairs is [(brand as written on the file,
    audience_pct, genpop_pct or None, COLUMN)], one per brand, looked up
    in the caller's preferred columns first, then the purchase family,
    then the other brand columns, people and places last."""
    rows = profile_rows(df, subject)
    index = {}
    for cu, lst in rows.items():
        for b, p in lst:
            index.setdefault(norm_brand(b), []).append((cu, b, p))
    gp = genpop_map or {}
    pairs, missing, seen = [], [], set()
    for want in brands or ():
        key = norm_brand(want)
        if not key or key in seen:
            continue
        hits = index.get(key)
        if not hits and len(key) >= 4:
            # a short form of one brand's name ('Costa' for 'Costa
            # Sunglasses', 'Milwaukee' for 'Milwaukee Tools'): the
            # brand columns only, never a person or a place
            wt = _tokens(want)
            hits = [h for k, lst in index.items() for h in lst
                    if _column_rank(h[0], prefer)[0] <= 1
                    and _tokens(h[1])[:len(wt)] == wt]
        if not hits:
            missing.append(str(want))
            continue
        cu, b, p = sorted(hits, key=lambda h: (_column_rank(h[0], prefer),
                                               -h[2], h[1]))[0]
        found = norm_brand(b)
        if found in seen:
            continue
        seen.add(key)
        seen.add(found)
        pairs.append((b, p, gp.get((cu, found)), cu))
    return pairs, missing


def column_sums(df, genpop_map, subject=None):
    """{COLUMN: (sum of the column's audience percents, sum of its US
    percents over the rows the US file carries)} for every either/or
    column on the profile. The either/or part of the one law reads these,
    here and in the cross of behaviors, so both see the same column."""
    out = {}
    gp = genpop_map or {}
    for cu, rows in profile_rows(df, subject).items():
        if not _ties.is_either_or(cu):
            continue
        g = [gp.get((cu, norm_brand(b))) for b, _ in rows]
        out[cu] = (sum(p for _, p in rows), sum(x for x in g if x is not None and x > 0))
    return out


def cluster_rollup(df, genpop_map, brands, name='', subject=None,
                   prefer=()):
    """Roll a named list of brands up on one profile. The rollup dict
    plus 'members' (the brands found, as written on the file) and
    'missing' (names the profile does not carry), or None when none of
    the brands is on the profile."""
    pairs, missing = resolve_brands(df, brands, genpop_map, subject, prefer)
    sums = None
    if any(_ties.is_either_or(cu) for _, _, _, cu in pairs):
        sums = column_sums(df, genpop_map, subject)
    r = rollup_pairs(((b, a, g, cu) for b, a, g, cu in pairs), name=name, column_sums=sums)
    if not r:
        return None
    r['members'] = [b for b, _, _, _ in pairs]
    r['missing'] = missing
    return r


# --------------------------------------------------------- presentation
def figure(r):
    """'48.6%, index 212' (or just the percent when there is no index)."""
    if not r:
        return ''
    s = f"{r['reach']:.1f}%"
    if r.get('index') is not None:
        s += f", index {r['index']}"
    return s


def sentence(r, label=None, verb='engage with', noun='brands'):
    """'48.6% engage with workwear and tools brands, index 212 (2.1x the
    US average)'. Flat, one decimal, whole-number index, the plain
    comparison beside it."""
    if not r:
        return ''
    label = str(label if label is not None else r.get('name') or '').strip()
    tail = f" {noun}" if noun and not re.search(
        r"\b(?:brands?|services?|platforms?|teams?|apps?)\s*$", label, re.I) else ''
    s = f"{r['reach']:.1f}% {verb} {label}{tail}".rstrip()
    if r.get('index') is not None:
        s += f", index {r['index']} ({r['times']})"
    return s


def public(r):
    """The fields a page or a payload may carry (no internals)."""
    if not r:
        return None
    keep = ('name', 'reach', 'genpop', 'index', 'times', 'brands', 'top',
            'top_pct')
    return {k: r.get(k) for k in keep}
