"""Digital Twins IQ: the twin and the runs it answers (2026-10-26, Jenna).

Jenna, verbatim: "let's start building out digital twins iq do whatever
you need to do to build it out. it can again all be synthetic based on
external research, corpus data and high level reasoning to be
predictive as to how content and things would go. ... i want full
finished product."

A twin is a cohort model: identity + traits + response curves +
lifecycle, derived from the corpus and researched anchors. This module
is the deterministic half of the product: every number a run reports
comes out of the twin's traits and response curves through one seeded
jitter, so the same twin on the same scenario gives the same answer
and no two twins collide on a figure. The model only writes the
plain-English read (``read_fn``); when it is off the deterministic
read ships.

Scenario kinds (``simulate(twin, scenario)``):

  greenlight     RETIRED from the product (Jenna 2026-10-28: Greenlight
                 is two options now, a Script read and a Concept test,
                 both Pilot Testing sessions). The runner stays so a
                 stored run still loads; ``RETIRED_RUN_KINDS`` hides
                 those runs from every surface and ``simulate`` refuses
                 a new one with ``GREENLIGHT_RETIRED_LINE``.
  launch         a plan and budget: three plans side by side, odds and
                 ranges week by week, levers, a holdout sized to prove
                 lift at the lowest spend
  content        a season / sequel / episode drop
  platform_move  a window or home change
  partnership    a brand x twin
  pricing        a price or packaging change
  talent_attach  a cast or creator attach
  merch_drop     a product drop
  compare        two twins on the same scenario (``compare``)

Numeric discipline (hard, every run): counts end in 1-9 on a tile,
percentages carry one decimal in prose and four in tables, chains stay
coherent (low <= expected <= high, shares sum to the total), and
everything is deterministic per (twin, scenario, version).
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from typing import Callable, Optional

VERSION = 1
US_POP = 329_900_000

TRAITS = (
    ('discovery_social_vs_search', 'Finds things through social, not search'),
    ('binge_vs_appointment', 'Binges rather than watching on a schedule'),
    ('price_sensitivity', 'Reacts to price'),
    ('churn_propensity', 'Likely to cancel when the title ends'),
    ('franchise_loyalty', 'Comes back for the next entry'),
    ('merch_propensity', 'Buys the product'),
    ('second_screen', 'Phone in hand while watching'),
    ('live_events', 'Shows up in person'),
    ('creator_follow', 'Follows the people behind it'),
    ('ad_tolerance', 'Sits through ads'),
    ('trial_speed', 'Tries a new title fast'),
    ('word_of_mouth', 'Tells other people'),
)
TRAIT_KEYS = tuple(k for k, _l in TRAITS)
TRAIT_LABEL = dict(TRAITS)

CHANNELS = ('Meta', 'Google', 'Amazon', 'TikTok', 'YouTube', 'CTV', 'Programmatic', 'Podcast', 'Other')
# published US benchmarks shape these priors (CPM in dollars, first-action rate
# as a share of people reached); the twin's traits move them per channel
_CHANNEL_PRIOR = {
    'Meta':         {'cpm': 11.80, 'action': 0.024, 'ceiling': 0.72, 'freq': 3.1},
    'Google':       {'cpm': 9.40,  'action': 0.052, 'ceiling': 0.64, 'freq': 2.4},
    'Amazon':       {'cpm': 8.70,  'action': 0.061, 'ceiling': 0.48, 'freq': 2.2},
    'TikTok':       {'cpm': 7.90,  'action': 0.017, 'ceiling': 0.58, 'freq': 3.6},
    'YouTube':      {'cpm': 12.30, 'action': 0.021, 'ceiling': 0.77, 'freq': 2.9},
    'CTV':          {'cpm': 28.50, 'action': 0.009, 'ceiling': 0.69, 'freq': 2.6},
    'Programmatic': {'cpm': 6.20,  'action': 0.006, 'ceiling': 0.81, 'freq': 4.2},
    'Podcast':      {'cpm': 22.00, 'action': 0.013, 'ceiling': 0.36, 'freq': 2.0},
    'Other':        {'cpm': 14.00, 'action': 0.012, 'ceiling': 0.40, 'freq': 2.5},
}
_CHANNEL_ALIASES = {
    'facebook': 'Meta', 'instagram': 'Meta', 'ig': 'Meta', 'fb': 'Meta', 'meta': 'Meta',
    'google': 'Google', 'search': 'Google', 'sem': 'Google', 'paid search': 'Google', 'bing': 'Google',
    'amazon': 'Amazon', 'amazon ads': 'Amazon', 'amazon dsp': 'Amazon',
    'tiktok': 'TikTok', 'tik tok': 'TikTok',
    'youtube': 'YouTube', 'yt': 'YouTube',
    'ctv': 'CTV', 'connected tv': 'CTV', 'ott': 'CTV', 'streaming tv': 'CTV', 'hulu ads': 'CTV', 'roku': 'CTV',
    'programmatic': 'Programmatic', 'display': 'Programmatic', 'dsp': 'Programmatic', 'ttd': 'Programmatic',
    'podcast': 'Podcast', 'podcasts': 'Podcast', 'audio': 'Podcast', 'spotify': 'Podcast',
}
PLATFORM_PRIOR_REACH = {   # share of US people on the service in a year, the fallback when the twin carries no footprint row
    'Netflix': 68.4, 'Prime Video': 55.2, 'Amazon Prime Video': 55.2, 'Hulu': 36.7, 'Disney+': 34.1, 'Max': 29.3, 'HBO Max': 29.3,
    'Peacock': 27.6, 'Paramount+': 26.9, 'Apple TV+': 16.8, 'Apple TV': 16.8, 'Tubi': 24.5, 'Pluto TV': 20.9, 'Roku Channel': 18.7,
    'YouTube': 83.2, 'Spotify': 38.6, 'Apple Music': 17.4, 'Audible': 9.8, 'Kindle': 14.6, 'Theatrical': 46.3, 'Starz': 8.4,
    'AMC+': 6.1, 'Crunchyroll': 7.2, 'Twitch': 12.1, 'Steam': 15.3, 'PlayStation': 22.4, 'Xbox': 17.8, 'Nintendo Switch': 21.6,
}
_WORD_ODDS = ('less than one in ten', 'one in ten', 'two in ten', 'three in ten', 'four in ten', 'five in ten',
              'six in ten', 'seven in ten', 'eight in ten', 'nine in ten', 'better than nine in ten')
_FORBIDDEN = frozenset({2001, 12345, 54321, 99999, 88888, 77777, 66666, 55555, 44444, 33333, 22222, 11111,
                        100000, 1000000, 123456, 654321})


# ------------------------------------------------------------- numerics
def seed_int(*parts) -> int:
    return int(hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16], 16)


def unit(*parts) -> float:
    """A deterministic draw in [0, 1)."""
    return (seed_int(*parts) % 10_000_019) / 10_000_019.0


def jit(lo: float, hi: float, *parts) -> float:
    return lo + (hi - lo) * unit(*parts)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def messy_count(subject, kpi, value) -> int:
    """A count whose last digit is 1-9, deterministic on (subject, kpi,
    value). Never one of the placeholder literals."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v % 10 != 0 and v not in _FORBIDDEN:
        return v
    h = seed_int(subject, kpi, v)
    span = max(9, int(abs(v) * 0.008))
    v = v + (h % (2 * span + 1)) - span
    guard = 0
    while (v % 10 == 0 or v in _FORBIDDEN or v <= 0) and guard < 40:
        v += 1 + ((h >> (guard + 3)) % 8)
        guard += 1
    return max(1, v)


def natural_count(subject, kpi, value) -> int:
    """A count for a long table: the last digit follows the natural
    distribution (a zero about a tenth of the time), never a value
    divisible by 10,000 or a sub-million value divisible by 1,000."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v in _FORBIDDEN or v % 10_000 == 0 or (v < 1_000_000 and v % 1_000 == 0):
        return messy_count(subject, kpi, v)
    return v


def messy_money(subject, kpi, value) -> float:
    """Dollars with real cents (never .00), the integer part off a round
    hundred, deterministic."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    whole = int(v)
    if whole >= 1000 and whole % 100 == 0:
        whole = messy_count(subject, kpi + '|$', whole)
    h = seed_int(subject, kpi, whole)
    cents = (h % 97) + 1          # 1..97, never 0
    if cents % 10 == 0:
        cents += 1
    return round(whole + cents / 100.0, 2)


def pct4(x) -> float:
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return 0.0


def pct1(x) -> float:
    try:
        return round(float(x), 1)
    except (TypeError, ValueError):
        return 0.0


def off_boundary(x: float, *parts) -> float:
    """A 4dp percentage that never sits on a 2dp boundary."""
    x = pct4(x)
    if abs(x - round(x, 2)) < 0.00005:
        d = 0.0011 + 0.0038 * unit('ob', x, *parts)
        x = pct4(x + d if unit('ob2', x, *parts) < 0.5 or x - d <= 0 else x - d)
    return x


def normalize_100(items, *parts) -> list:
    """[{label, pct}] summing to exactly 100.0000, every row at 4dp and
    off a 2dp boundary, with salted micro-jitter. Zero-weight rows stay
    at 0."""
    rows = [(str(it.get('label') if isinstance(it, dict) else it[0]),
             max(0.0, float(it.get('pct') if isinstance(it, dict) else it[1]) if (it.get('pct') if isinstance(it, dict) else it[1]) is not None else 0.0))
            for it in (items or [])]
    total = sum(p for _l, p in rows)
    if not rows or total <= 0:
        return [{'label': l, 'pct': 0.0} for l, _p in rows]
    out = []
    for i, (l, p) in enumerate(rows):
        base = p / total * 100.0
        if base > 0:
            base = base * (1 + (unit('n100', l, i, *parts) - 0.5) * 0.012)
        out.append([l, base])
    tot = sum(p for _l, p in out)
    out = [[l, p / tot * 100.0] for l, p in out]
    fixed = []
    for i, (l, p) in enumerate(out):
        v = off_boundary(p, l, i, *parts) if p > 0 else 0.0
        fixed.append([l, v])
    # the largest row absorbs the rounding so the column sums to 100.0000
    gap = round(100.0 - sum(p for _l, p in fixed), 4)
    k = max(range(len(fixed)), key=lambda i: fixed[i][1])
    fixed[k][1] = pct4(fixed[k][1] + gap)
    if abs(fixed[k][1] - round(fixed[k][1], 2)) < 0.00005 and len(fixed) > 1:
        # the absorber landed on a boundary: trade 0.0013 with the second largest
        j = sorted(range(len(fixed)), key=lambda i: -fixed[i][1])[1]
        if fixed[j][1] > 0.01:
            fixed[k][1] = pct4(fixed[k][1] + 0.0013)
            fixed[j][1] = pct4(fixed[j][1] - 0.0013)
    return [{'label': l, 'pct': pct4(p)} for l, p in fixed]


def odds_words(pct: float) -> str:
    p = clamp(float(pct), 0.0, 100.0)
    if p < 5:
        return 'less than one in twenty'
    if p >= 95:
        return 'better than nineteen in twenty'
    i = int(round(p / 10.0))
    return _WORD_ODDS[clamp(i, 0, 10)]


def _ncdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def odds_of_clearing(expected: float, goal: float, sigma: float) -> float:
    """P(outcome >= goal) under a lognormal centered on expected."""
    if goal <= 0:
        return 99.4
    if expected <= 0:
        return 0.6
    z = (math.log(goal) - math.log(expected)) / max(0.05, sigma)
    return clamp((1.0 - _ncdf(z)) * 100.0, 0.4, 99.6)


def fmt_people(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f"{n:,}"


def fmt_money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def human_money(v) -> str:
    """Prose form: $1.26M, $412.3K; exact figures belong in tables."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 1_000_000_000:
        return f"${v / 1e9:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1e6:.2f}M"
    if v >= 10_000:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.2f}"


def human_people(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 10_000:
        return f"{n / 1e3:.1f}K"
    return f"{int(round(n)):,}"


# -------------------------------------------------------------- traits
def trait(twin: dict, key: str, default: float = 50.0) -> float:
    t = ((twin or {}).get('traits') or {}).get(key)
    if isinstance(t, dict):
        t = t.get('value')
    try:
        return clamp(float(t), 0.0, 100.0)
    except (TypeError, ValueError):
        return default


_TRAIT_PHRASES = {   # (high, middle, low): complete predicates after "they ..."
    'discovery_social_vs_search': ('lean social-first for discovery', 'read as split between social and search for discovery', 'lean search-led for discovery'),
    'binge_vs_appointment': ('skew binge; full drops land harder than weekly ones', 'read as open to either a weekly or a full drop', 'skew appointment; a weekly drop holds them longer'),
    'price_sensitivity': ('watch the price closely', 'read as price-aware but not price-led', 'barely notice a price change'),
    'churn_propensity': ('lean toward canceling when a title ends', 'read as middling on canceling when a title ends', 'tend to stay subscribed after a title ends'),
    'franchise_loyalty': ('come back for the next entry almost every time', 'read as loyal to a franchise when the last entry earned it', 'treat each entry on its own and rarely come back by habit'),
    'merch_propensity': ('buy the product when it exists', 'read as occasional product buyers', 'rarely buy the product'),
    'second_screen': ('keep a phone in hand while watching', 'read as split on second-screening', 'tend to watch with the phone down'),
    'live_events': ('show up in person when something is on', 'show up in person now and then', 'rarely show up in person'),
    'creator_follow': ('follow the people behind a title', 'read as following a few of the people behind a title', 'follow the title more than the people in it'),
    'ad_tolerance': ('sit through ads without much friction', 'read as tolerating a light ad load', 'skew away from ad-supported viewing'),
    'trial_speed': ('try a new title in its first week', 'read as trying new titles at an ordinary pace', 'wait for a title to prove itself'),
    'word_of_mouth': ('tell other people about what they watch', 'read as moderate on word of mouth', 'rarely talk about what they watch'),
}


def trait_read(key: str, value: float) -> str:
    """Tier-2 language for a dial, as a predicate after "they": leans /
    skews / reads as. Never a hard count."""
    v = clamp(float(value), 0, 100)
    hi, mid, lo = _TRAIT_PHRASES.get(key, (f"lean high on {key.replace('_', ' ')}", f"sit near the middle on {key.replace('_', ' ')}", f"lean low on {key.replace('_', ' ')}"))
    if v >= 58:
        return hi
    if v <= 42:
        return lo
    return mid


def trait_block(values: dict, seed: str) -> dict:
    """{key: {value, read}} with every dial off a round number."""
    out = {}
    for k in TRAIT_KEYS:
        v = clamp(float(values.get(k, 50.0)), 1.0, 99.0)
        v = round(v + (unit('trait', k, seed) - 0.5) * 1.8, 1)
        if abs(v - round(v)) < 0.05:
            v = round(v + 0.3, 1)
        v = clamp(v, 1.1, 98.9)
        out[k] = {'value': v, 'label': TRAIT_LABEL[k], 'read': trait_read(k, v)}
    return out


def response_curves(traits: dict, meta: dict, anchors: Optional[dict] = None, seed: str = '') -> dict:
    """The curves the runs use, each a small parameter dict with a plain
    explanation. Parameters come from the dials; prices and reach from
    the anchors when they exist."""
    g = lambda k: trait({'traits': traits}, k)
    a = anchors or {}
    prices = a.get('prices') or {}
    ref_price = float(prices.get('reference_price') or prices.get('svod_arpu') or 0) or round(11.0 + 6.0 * unit('refp', seed), 2)
    e = -(0.65 + 1.55 * g('price_sensitivity') / 100.0) * (1 + (unit('pe', seed) - 0.5) * 0.08)
    fmt = str(meta.get('format') or 'none')
    weeks = []
    binge = g('binge_vs_appointment') / 100.0
    for w in range(8):
        if binge >= 0.5:
            share = math.exp(-w * (0.55 + 0.35 * binge))
        else:
            share = math.exp(-w * (0.22 + 0.25 * binge)) * (1.0 if w else 0.78)
        weeks.append(share)
    tot = sum(weeks)
    week_shares = [pct4(s / tot * 100.0 * (1 + (unit('ws', w, seed) - 0.5) * 0.02)) for w, s in enumerate(weeks)]
    gap = round(100.0 - sum(week_shares), 4)
    week_shares[0] = pct4(week_shares[0] + gap)
    sat = (180_000 + 2_600_000 * (g('ad_tolerance') / 100.0) * (0.6 + 0.4 * g('trial_speed') / 100.0)) * (1 + (unit('sat', seed) - 0.5) * 0.1)
    return {
        'price_elasticity': {
            'elasticity': round(e, 3), 'reference_price': messy_money(seed, 'refprice', ref_price),
            'read': (f"Each 10% on the price moves take-up about {abs(e) * 10:.1f}% the other way; this group "
                     + ('watches the price closely.' if g('price_sensitivity') >= 60 else 'absorbs small changes.'))},
        'platform_move_retention': {
            'base_retention_pct': pct1(44 + 0.38 * g('franchise_loyalty') - 0.22 * g('churn_propensity') + (unit('pmr', seed) - 0.5) * 3),
            'loyalty_bonus_pct': pct1(4 + 0.12 * g('franchise_loyalty')),
            'overlap_weight': round(0.55 + 0.3 * unit('ow', seed), 3),
            'read': 'The share that follows the title to a new home, before the two services\' overlap is counted.'},
        'cast_attach_lift': {
            'reach_lift_pct': pct1(3.2 + 0.19 * g('creator_follow') + (unit('cal', seed) - 0.5) * 1.5),
            'return_lift_pct': pct1(1.4 + 0.08 * g('franchise_loyalty')),
            'read': 'What a name this group already follows adds to first-month reach and to coming back.'},
        'clip_volume_lift': {
            'reach_elasticity': round(0.06 + 0.0024 * g('discovery_social_vs_search') + (unit('cve', seed) - 0.5) * 0.02, 4),
            'saturation_clips_per_week': int(5 + 9 * g('second_screen') / 100.0),
            'read': 'Reach gained per extra clip a week, until the feed is saturated.'},
        'release_window_curve': {
            'week_shares': week_shares, 'binge_tilt': pct1(g('binge_vs_appointment')),
            'read': ('Front-loaded: most of the audience arrives in the first two weeks.' if binge >= 0.5
                     else 'Spread out: a weekly drop keeps the audience arriving for a month or more.')},
        'marketing_weight_curve': {
            'saturation_spend_usd': messy_money(seed, 'satspend', sat),
            'half_reach_spend_usd': messy_money(seed, 'halfspend', sat * 0.31),
            'read': 'Paid reach rises fast then flattens; past the saturation spend each dollar buys less.'},
        'sequel_return_rate': {
            'return_pct': pct1(28 + 0.42 * g('franchise_loyalty') - 0.12 * g('churn_propensity') + (unit('srr', seed) - 0.5) * 3),
            'decay_per_entry_pct': pct1(6 + 7 * (1 - g('franchise_loyalty') / 100.0)),
            'read': 'The share of this audience that comes back for the next entry, and how much that slips each time.'},
        'retarget_return_rate': {
            'return_pct': pct1(6 + 0.14 * g('word_of_mouth') + 0.06 * g('second_screen') + (unit('rrr', seed) - 0.5) * 1.2),
            'window_days': 21 + int(10 * unit('rrw', seed)),
            'read': 'The share of people who left without acting and come back inside three weeks when reminded.'},
        'format': fmt,
    }


# ------------------------------------------------------------ channels
def canon_channel(name: str) -> str:
    low = ' '.join(str(name or '').lower().split())
    if not low:
        return 'Other'
    if low in _CHANNEL_ALIASES:
        return _CHANNEL_ALIASES[low]
    for k, v in _CHANNEL_ALIASES.items():
        if k in low:
            return v
    for c in CHANNELS:
        if c.lower() == low:
            return c
    return 'Other'


def channel_params(twin: dict, channel: str, momentum: float = 1.0) -> dict:
    """Per-channel response for this twin: cpm, first-action rate, the
    reachable ceiling (people) and effective frequency."""
    c = canon_channel(channel)
    p = dict(_CHANNEL_PRIOR[c])
    g = lambda k: trait(twin, k)
    size = int(((twin or {}).get('meta') or {}).get('size_us') or 1_000_000)
    social = g('discovery_social_vs_search') / 100.0
    key = str(((twin or {}).get('meta') or {}).get('subject') or '')
    if c in ('Meta', 'TikTok', 'YouTube'):
        p['action'] *= 0.7 + 0.6 * social
        p['ceiling'] *= 0.85 + 0.3 * social
    elif c == 'Google':
        p['action'] *= 0.7 + 0.6 * (1 - social)
    elif c == 'Amazon':
        p['action'] *= 0.6 + 0.8 * g('merch_propensity') / 100.0
    elif c == 'CTV':
        p['action'] *= 0.7 + 0.6 * g('ad_tolerance') / 100.0
        p['ceiling'] *= 0.8 + 0.4 * g('binge_vs_appointment') / 100.0
    elif c == 'Podcast':
        p['ceiling'] *= 0.6 + 0.8 * g('creator_follow') / 100.0
    p['action'] *= (0.75 + 0.5 * g('trial_speed') / 100.0) * clamp(momentum, 0.6, 1.6)
    p['cpm'] *= 1 + (unit('cpm', c, key) - 0.5) * 0.12
    p['action'] *= 1 + (unit('act', c, key) - 0.5) * 0.1
    p['ceiling_people'] = max(1000, int(size * clamp(p['ceiling'], 0.05, 0.95)))
    p['channel'] = c
    return p


def channel_signal(twin: dict, channel: str, spend: float, momentum: float = 1.0, days: int = 1) -> dict:
    """reach, first_actions and cpa for one channel at one spend."""
    p = channel_params(twin, channel, momentum)
    if spend <= 0:
        return {'channel': p['channel'], 'spend': 0.0, 'reach': 0, 'first_actions': 0, 'cpa': 0.0, 'marginal_actions_per_dollar': p['action'] / p['cpm'] * 1000 / p['freq']}
    impressions = spend / p['cpm'] * 1000.0
    ceiling = p['ceiling_people']
    reach = ceiling * (1 - math.exp(-impressions / (ceiling * p['freq'])))
    actions = reach * p['action']
    marginal = (ceiling - reach) / (ceiling * p['freq']) * (1000.0 / p['cpm']) * p['action']
    cpa = spend / actions if actions > 0 else 0.0
    return {'channel': p['channel'], 'spend': round(spend, 2), 'reach': int(reach), 'first_actions': int(actions),
            'cpa': round(cpa, 2), 'marginal_actions_per_dollar': marginal}


def allocate_signal_led(twin: dict, channels: list, total: float, floor_pct: float = 5.0, momentum: float = 1.0, step: int = 400) -> list:
    """Greedy marginal-return allocation of ``total`` across the same
    channels, each kept at or above ``floor_pct`` of the total."""
    names = [canon_channel(c.get('channel') if isinstance(c, dict) else c) for c in channels] or ['Meta']
    names = list(dict.fromkeys(names))
    floor = total * floor_pct / 100.0
    alloc = {n: floor for n in names}
    left = total - floor * len(names)
    steps = max(1, int(left / max(1, step)))
    inc = left / steps if steps else 0
    for _ in range(steps):
        best, best_m = None, -1.0
        for n in names:
            m = channel_signal(twin, n, alloc[n] + inc, momentum)['marginal_actions_per_dollar']
            if m > best_m:
                best, best_m = n, m
        alloc[best] += inc
    return [{'channel': n, 'spend': round(alloc[n], 2)} for n in names]


def plan_outcome(twin: dict, channels: list, momentum: float = 1.0) -> dict:
    by = [channel_signal(twin, c['channel'], float(c.get('spend') or 0), momentum) for c in channels]
    spend = sum(float(c.get('spend') or 0) for c in channels)
    actions = sum(b['first_actions'] for b in by)
    reach = sum(b['reach'] for b in by)
    return {'spend': round(spend, 2), 'reach': reach, 'first_actions': actions, 'cpa': round(spend / actions, 2) if actions else 0.0, 'by_channel': by}


# --------------------------------------------------------- the scenarios
def _meta(twin):
    return (twin or {}).get('meta') or {}


def _size(twin) -> int:
    try:
        return max(1000, int(_meta(twin).get('size_us') or 0))
    except (TypeError, ValueError):
        return 1000


def _subject(twin) -> str:
    return str(_meta(twin).get('subject') or _meta(twin).get('name') or 'the audience')


def platform_reach_pct(twin: dict, platform: str) -> float:
    """Share of the twin on a service: the twin's own footprint row,
    else the published prior for the service."""
    plats = ((twin or {}).get('identity') or {}).get('platforms') or []
    low = str(platform or '').lower().strip()
    for p in plats:
        lab = str(p.get('label') or '').lower()
        if lab == low or (low and (low in lab or lab in low)):
            try:
                return clamp(float(p.get('reach_pct') or 0), 0.5, 99.5)
            except (TypeError, ValueError):
                break
    for k, v in PLATFORM_PRIOR_REACH.items():
        if k.lower() == low or (low and (low in k.lower() or k.lower() in low)):
            return v * (1 + (unit('prior_reach', k, _subject(twin)) - 0.5) * 0.1)
    return 18.0 + 20.0 * unit('prior_reach_other', low, _subject(twin))


def _sigma(twin) -> float:
    return 0.26 + 0.22 * (1 - trait(twin, 'word_of_mouth') / 100.0)


def _cohorts(twin: dict, total: int, seed: str) -> list:
    """Audience by cohort: gender x age cells sized from the identity,
    plus the avid core. Shares sum to 100."""
    ident = (twin or {}).get('identity') or {}
    demo = ident.get('demographics') or {}
    gender = demo.get('gender') or [{'label': 'Women', 'pct': 51.2}, {'label': 'Men', 'pct': 48.8}]
    ages = demo.get('age') or [{'label': '18-24', 'pct': 14}, {'label': '25-34', 'pct': 24}, {'label': '35-44', 'pct': 22},
                               {'label': '45-54', 'pct': 17}, {'label': '55-64', 'pct': 13}, {'label': '65+', 'pct': 10}]
    cells = []
    for g in gender[:2]:
        gl = str(g.get('label') or '')
        gnoun = 'Women' if gl.lower().startswith(('f', 'w')) else 'Men' if gl.lower().startswith('m') else gl
        for a in ages:
            al = str(a.get('label') or '').replace('-', ' to ').replace('+', ' and over')
            w = float(g.get('pct') or 0) * float(a.get('pct') or 0) / 100.0
            w *= 1 + (unit('cell', gl, al, seed) - 0.5) * 0.3
            cells.append({'label': f"{gnoun} {al}", 'pct': w})
    cells.sort(key=lambda c: -c['pct'])
    top = cells[:6]
    rest = sum(c['pct'] for c in cells[6:])
    if rest > 0:
        top.append({'label': 'Everyone else', 'pct': rest})
    rows = normalize_100(top, 'cohorts', seed)
    out = []
    for r in rows:
        out.append({'cohort': r['label'], 'share_pct': r['pct'], 'people': natural_count(seed, 'cohort|' + r['label'], total * r['pct'] / 100.0)})
    return out


def _levers(twin: dict, expected: int, kind: str, seed: str, platforms: Optional[list] = None, transactional: bool = False) -> list:
    rc = (twin or {}).get('response') or {}
    cal = rc.get('cast_attach_lift') or {}
    cvl = rc.get('clip_volume_lift') or {}
    mwc = rc.get('marketing_weight_curve') or {}
    pe = rc.get('price_elasticity') or {}
    levers = []
    reach_lift = float(cal.get('reach_lift_pct') or 6.0)
    levers.append({'lever': 'Attach a name this group already follows',
                   'delta_pct': pct1(reach_lift),
                   'read': 'A face they follow is worth more than another week of ads here.' if trait(twin, 'creator_follow') >= 55
                   else 'Helps at the margin; this group follows the title more than the people in it.'})
    clips = float(cvl.get('reach_elasticity') or 0.1) * 100 * 0.6
    levers.append({'lever': 'Three more clips a week in the feed',
                   'delta_pct': pct1(clips),
                   'read': 'They find things in the feed, so clip volume moves first-month reach.' if trait(twin, 'discovery_social_vs_search') >= 55
                   else 'Clips help less than search presence for a group that looks things up.'})
    binge = trait(twin, 'binge_vs_appointment')
    levers.append({'lever': 'Weekly drop instead of a full drop' if binge < 50 else 'Full drop instead of weekly',
                   'delta_pct': pct1(abs(binge - 50) / 50.0 * 9.5 + 1.1),
                   'read': 'Holds them in the conversation for a month instead of a weekend.' if binge < 50 else 'They binge; a full drop lands the audience in the first two weeks.'})
    sat = float(mwc.get('saturation_spend_usd') or 800_000)
    levers.append({'lever': f"A paid plan at {human_money(sat * 0.35)}",
                   'delta_pct': pct1(8 + 10 * trait(twin, 'ad_tolerance') / 100.0),
                   'read': 'Paid reach adds on top of the organic first month; past the saturation spend each dollar buys less.'})
    if platforms and len(platforms) > 1:
        levers.append({'lever': f"Make {platforms[0]} the exclusive home",
                       'delta_pct': pct1(4 + 6 * trait(twin, 'franchise_loyalty') / 100.0),
                       'read': 'One home concentrates the audience and the conversation; a split launch dilutes both.'})
    if transactional:
        e = abs(float(pe.get('elasticity') or 1.4))
        levers.append({'lever': 'Price 10% under the reference',
                       'delta_pct': pct1(e * 10),
                       'read': 'Take-up moves with price for this group.' if e > 1.3 else 'Price moves take-up only a little here.'})
    for lv in levers:
        lv['delta_pct'] = pct1(lv['delta_pct'] * (1 + (unit('lever', lv['lever'], seed) - 0.5) * 0.12))
        lv['delta_people'] = messy_count(seed, 'lever|' + lv['lever'], expected * lv['delta_pct'] / 100.0)
    levers.sort(key=lambda l: -l['delta_people'])
    for i, lv in enumerate(levers, 1):
        lv['rank'] = i
    return levers


def _package(twin: dict, platform: str, levers: list, expected: int, seed: str) -> dict:
    binge = trait(twin, 'binge_vs_appointment')
    social = trait(twin, 'discovery_social_vs_search')
    rc = (twin or {}).get('response') or {}
    sat = float((rc.get('marketing_weight_curve') or {}).get('saturation_spend_usd') or 800_000)
    spend = messy_money(seed, 'pkgspend', sat * 0.35)
    lead = 'Meta and YouTube' if social >= 55 else 'Google and YouTube'
    cadence = int(3 + 4 * trait(twin, 'second_screen') / 100.0)
    holdout_people = messy_count(seed, 'pkghold', max(8000, expected * 0.045))
    return {
        'platform': platform,
        'window': 'Weekly drop over six weeks' if binge < 50 else 'Full drop, with the first two weeks carrying the push',
        'lead_asset': 'The cast-forward trailer' if trait(twin, 'creator_follow') >= 55 else 'The premise trailer',
        'clip_cadence': f"{cadence} clips a week, cut from the first two episodes" if (_meta(twin).get('format') in ('series', 'none', None)) else f"{cadence} clips a week",
        'spend': spend, 'spend_mix': f"{human_money(spend)} weighted to {lead}",
        'holdout': f"Hold out {fmt_people(holdout_people)} matched people so the lift can be read",
        'top_lever': levers[0]['lever'] if levers else '',
        'read': (f"Launch on {platform} with a {'weekly' if binge < 50 else 'full'} drop, lead with "
                 f"{'the cast' if trait(twin, 'creator_follow') >= 55 else 'the premise'}, and put {human_money(spend)} behind it weighted to {lead}."),
    }


def _norm_goal(goal, default_metric='viewers') -> dict:
    g = goal if isinstance(goal, dict) else {}
    try:
        value = int(float(str(g.get('value') if g else goal or 0).replace(',', '').replace('$', '')))
    except (TypeError, ValueError):
        value = 0
    metric = str(g.get('metric') or default_metric)
    return {'metric': metric, 'value': value, 'platform': g.get('platform') or '', 'window_days': int(g.get('window_days') or 30)}


def _noun_for(twin: dict, metric: str = '') -> str:
    if metric and metric not in ('viewers', 'buyers', 'listeners', 'players', 'readers'):
        return metric
    fmt = str(_meta(twin).get('format') or '')
    return {'film': 'viewers', 'series': 'viewers', 'book': 'readers', 'game': 'players', 'podcast': 'listeners',
            'creator': 'viewers', 'brand': 'buyers'}.get(fmt, metric or 'viewers')


def run_greenlight(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    subj = _subject(twin)
    title = str(inputs.get('title') or inputs.get('idea') or inputs.get('logline') or 'the idea')[:160]
    is_idea = not inputs.get('title') and bool(inputs.get('idea') or inputs.get('logline'))
    platforms = [str(p) for p in (inputs.get('platforms') or []) if str(p).strip()] or [str(_meta(twin).get('platform_home') or 'Netflix')]
    goal = _norm_goal(inputs.get('goal'), _noun_for(twin))
    if not goal['value']:
        # no bar named: the odds are read against the usual bar, three in ten of the twin in the first month
        goal['value'] = messy_count(f"{subj}|{title}", 'goal', size * 0.3)
        goal['usual_bar'] = True
    trial = 0.14 + 0.36 * trait(twin, 'trial_speed') / 100.0
    fit = float(inputs.get('fit') or 1.0)
    if is_idea:
        fit *= 0.62
    anchors = inputs.get('anchors') or {}
    hist = anchors.get('title_fit') if isinstance(anchors, dict) else None
    if hist:
        try:
            fit *= clamp(float(hist), 0.5, 1.6)
        except (TypeError, ValueError):
            pass
    per_platform = []
    for p in platforms:
        reach = platform_reach_pct(twin, p) / 100.0
        exp_raw = size * reach * trial * fit * (1 + (unit('gl', p, title, seed) - 0.5) * 0.14)
        sigma = _sigma(twin)
        expected = messy_count(seed, f"gl|{p}|exp", exp_raw)
        low = messy_count(seed, f"gl|{p}|low", exp_raw * math.exp(-sigma * 1.04))
        high = messy_count(seed, f"gl|{p}|high", exp_raw * math.exp(sigma * 1.04))
        low, high = min(low, expected - 1), max(high, expected + 1)
        row = {'platform': p, 'expected': expected, 'low': low, 'high': high, 'reach_pct': pct1(reach * 100)}
        if goal['value'] and (not goal['platform'] or goal['platform'].lower() in p.lower()):
            row['odds_pct'] = pct1(odds_of_clearing(exp_raw, goal['value'], sigma))
            row['odds_words'] = odds_words(row['odds_pct'])
        per_platform.append(row)
    per_platform.sort(key=lambda r: -r['expected'])
    best = per_platform[0]
    noun = _noun_for(twin, goal['metric'])
    completion = pct1(clamp(41 + 0.31 * trait(twin, 'binge_vs_appointment') + 0.08 * trait(twin, 'franchise_loyalty') + (unit('comp', seed) - 0.5) * 4, 20, 93))
    ret = pct1(float(((twin.get('response') or {}).get('sequel_return_rate') or {}).get('return_pct') or 40))
    cohorts = _cohorts(twin, best['expected'], seed + '|gl')
    levers = _levers(twin, best['expected'], 'greenlight', seed, platforms=[r['platform'] for r in per_platform], transactional=bool(_meta(twin).get('format') in ('film', 'book', 'game')))
    package = _package(twin, best['platform'], levers, best['expected'], seed)
    odds = best.get('odds_pct')
    outputs = {
        'title': title, 'is_idea': is_idea, 'noun': noun, 'goal': goal,
        'expected': best['expected'], 'low': best['low'], 'high': best['high'], 'platform': best['platform'],
        'per_platform': per_platform, 'completion_pct': completion, 'return_pct': ret,
        'odds_pct': odds, 'odds_words': best.get('odds_words'),
        'cohorts': cohorts, 'levers': levers, 'package': package,
    }
    drivers = [
        f"{pct1(best['reach_pct'])}% of this audience is on {best['platform']} in a year, the biggest footprint of the services named." if len(per_platform) > 1
        else f"{pct1(best['reach_pct'])}% of this audience is on {best['platform']} in a year.",
        f"They try new titles {'fast' if trait(twin, 'trial_speed') >= 55 else 'slowly'}: about {pct1(trial * 100)}% of the reachable group shows up in the first month.",
    ]
    if is_idea:
        drivers.append('A bare idea carries no title history, so the first month runs lighter than a known title would.')
    comparison = {'baseline_label': f"the whole {subj} audience", 'baseline_value': size,
                  'share_pct': pct1(best['expected'] / size * 100.0), 'read': f"The expected first month is {pct1(best['expected'] / size * 100.0)}% of the {human_people(size)} people in this twin."}
    rec = package['read']
    if odds is not None:
        bar = f"the usual bar of {fmt_people(goal['value'])} {noun} (three in ten of the twin)" if goal.get('usual_bar') else f"{fmt_people(goal['value'])} {noun}"
        rec = f"{odds_words(odds).capitalize()} chance it clears {bar} in its first {goal['window_days']} days on {best['platform']}. " + rec
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def _holdout(twin: dict, plan_outcome_: dict, seed: str) -> dict:
    reach = max(1000, int(plan_outcome_.get('reach') or 0))
    actions = max(1, int(plan_outcome_.get('first_actions') or 0))
    spend = float(plan_outcome_.get('spend') or 0)
    p1 = actions / reach
    base_rate = clamp(p1 * (0.42 + 0.3 * (1 - trait(twin, 'trial_speed') / 100.0)), 0.0005, 0.5)
    lift_rel = clamp(p1 / base_rate - 1.0, 0.08, 4.0)
    mde = base_rate * lift_rel * 0.8
    pbar = (base_rate + p1) / 2.0
    n = 2 * (1.96 + 0.84) ** 2 * pbar * (1 - pbar) / (mde ** 2)
    n = int(clamp(n, 4000, reach * 0.3))
    cost_per_person = spend / reach if reach else 2.5
    hold_spend = messy_money(seed, 'holdout_spend', max(1200, n * cost_per_person))
    hold_n = messy_count(seed, 'holdout_size', n)
    return {
        'size': hold_n,
        'spend': hold_spend,
        'expected_lift_pct': pct1(lift_rel * 100),
        'lift_read': (f"Hold {fmt_people(hold_n)} matched people out of the plan. At {human_money(hold_spend)} "
                      f"of exposure on the matched group that is the smallest holdout that can read a {pct1(lift_rel * 100)}% lift cleanly."),
    }


def size_holdout(twin: dict, outcome: dict, seed: str) -> dict:
    """Public name for the holdout sizing (the Spend Engine uses it)."""
    return _holdout(twin, outcome, seed)


def run_launch(twin: dict, inputs: dict, seed: str) -> dict:
    plan = inputs.get('plan') if isinstance(inputs.get('plan'), dict) else inputs
    raw_channels = plan.get('channels') or []
    channels = []
    for c in raw_channels:
        if isinstance(c, dict):
            try:
                sp = float(str(c.get('spend') or 0).replace('$', '').replace(',', ''))
            except (TypeError, ValueError):
                sp = 0.0
            channels.append({'channel': canon_channel(c.get('channel') or c.get('name')), 'spend': sp})
    if not channels:
        total = float(plan.get('budget') or inputs.get('budget') or 250_000)
        channels = [{'channel': 'Meta', 'spend': total * 0.4}, {'channel': 'YouTube', 'spend': total * 0.3}, {'channel': 'Google', 'spend': total * 0.3}]
    total = sum(c['spend'] for c in channels)
    weeks = int(plan.get('window_weeks') or inputs.get('window_weeks') or 6)
    weeks = clamp(weeks, 1, 16)
    momentum = float(inputs.get('momentum') or 1.0)
    goal = _norm_goal(plan.get('goal') or inputs.get('goal'), _noun_for(twin))
    noun = _noun_for(twin, goal['metric'])
    # organic first month from the twin, paid first actions on top
    size = _size(twin)
    home = str(plan.get('platform') or inputs.get('platform') or _meta(twin).get('platform_home') or 'Netflix')
    organic = size * platform_reach_pct(twin, home) / 100.0 * (0.14 + 0.36 * trait(twin, 'trial_speed') / 100.0) * 0.72
    conv = 0.34 + 0.38 * trait(twin, 'trial_speed') / 100.0
    sigma = _sigma(twin)

    def _plan(label, chans, note):
        oc = plan_outcome(twin, chans, momentum)
        outcome_raw = organic + oc['first_actions'] * conv
        exp = messy_count(seed, f"launch|{label}|exp", outcome_raw)
        low = messy_count(seed, f"launch|{label}|low", outcome_raw * math.exp(-sigma))
        high = messy_count(seed, f"launch|{label}|high", outcome_raw * math.exp(sigma))
        low, high = min(low, exp - 1), max(high, exp + 1)
        odds = pct1(odds_of_clearing(outcome_raw, goal['value'], sigma)) if goal['value'] else None
        return {'label': label, 'note': note, 'channels': [{'channel': c['channel'], 'spend': round(c['spend'], 2)} for c in chans],
                'spend': round(oc['spend'], 2), 'reach': messy_count(seed, f"launch|{label}|reach", oc['reach']),
                'first_actions': messy_count(seed, f"launch|{label}|fa", oc['first_actions']),
                'cpa': round(oc['cpa'], 2), 'expected': exp, 'low': low, 'high': high, 'odds_pct': odds,
                'odds_words': odds_words(odds) if odds is not None else None, 'by_channel': oc['by_channel'], '_raw': outcome_raw}

    as_alloc = _plan('As allocated', channels, 'Your plan as written.')
    sig_ch = allocate_signal_led(twin, channels, total, momentum=momentum)
    signal = _plan('Signal-led', sig_ch, 'The same money, moved to where this audience answers.')
    # the minimum that still hits the goal: scale the signal-led mix down until odds sit near 56%
    if goal['value']:
        lo, hi = 0.08, 1.0
        best_scale = 1.0
        for _ in range(18):
            mid = (lo + hi) / 2
            chans = [{'channel': c['channel'], 'spend': c['spend'] * mid} for c in sig_ch]
            oc = plan_outcome(twin, chans, momentum)
            odds = odds_of_clearing(organic + oc['first_actions'] * conv, goal['value'], sigma)
            if odds >= 56.0:
                best_scale, hi = mid, mid
            else:
                lo = mid
        min_ch = [{'channel': c['channel'], 'spend': c['spend'] * best_scale} for c in sig_ch]
        minimum = _plan('Minimum that still hits the goal', min_ch, f"{pct1(best_scale * 100)}% of the money, on the signal-led mix.")
        if minimum['odds_pct'] is not None and minimum['odds_pct'] < 50 and best_scale >= 0.999:
            minimum['note'] = 'Even the full budget on the signal-led mix sits under even odds; the goal needs more money or a lower bar.'
    else:
        minimum = _plan('Minimum', [{'channel': c['channel'], 'spend': c['spend'] * 0.6} for c in sig_ch], 'Six tenths of the money on the signal-led mix; no goal was set, so this is the lean version.')
    plans = [as_alloc, signal, minimum]
    # week by week on the signal-led plan
    rc = (twin or {}).get('response') or {}
    shares = list((rc.get('release_window_curve') or {}).get('week_shares') or [40, 22, 13, 9, 6, 4, 3, 3])
    shares = shares[:weeks] if len(shares) >= weeks else shares + [shares[-1] * 0.7 ** (i + 1) for i in range(weeks - len(shares))]
    tot = sum(shares)
    cum = 0.0
    week_rows = []
    for w in range(weeks):
        cum += shares[w] / tot
        exp_w = signal['_raw'] * cum
        s_w = sigma * (1.0 - 0.45 * (w / max(1, weeks - 1)))
        row = {'week': w + 1, 'expected': messy_count(seed, f"wk|{w}|exp", exp_w),
               'low': messy_count(seed, f"wk|{w}|low", exp_w * math.exp(-s_w)),
               'high': messy_count(seed, f"wk|{w}|high", exp_w * math.exp(s_w))}
        if goal['value']:
            row['odds_pct'] = pct1(odds_of_clearing(signal['_raw'], goal['value'], s_w))
            row['odds_words'] = odds_words(row['odds_pct'])
        week_rows.append(row)
    for p in plans:
        p.pop('_raw', None)
    # levers ranked by what they earn in first actions
    levers = []
    for p_ in plans[1]['by_channel']:
        if p_['first_actions'] > 0:
            plus = channel_signal(twin, p_['channel'], p_['spend'] * 1.25, momentum)
            gain = plus['first_actions'] - p_['first_actions']
            levers.append({'lever': f"Another 25% on {p_['channel']}", 'delta_people': messy_count(seed, 'lv|' + p_['channel'], max(1, gain * conv)),
                           'delta_pct': pct1(gain / max(1, signal['first_actions']) * 100),
                           'read': f"Cost per first action on {p_['channel']} is {fmt_money(p_['cpa'])}; the next dollar there still buys reach." if plus['marginal_actions_per_dollar'] > 0.001
                           else f"{p_['channel']} is close to saturated for this audience."})
    cast = float((rc.get('cast_attach_lift') or {}).get('reach_lift_pct') or 6)
    levers.append({'lever': 'Lead with the cast in the creative', 'delta_pct': pct1(cast), 'delta_people': messy_count(seed, 'lv|cast', signal['expected'] * cast / 100.0),
                   'read': 'This group follows the people; a face in the first frame lifts first actions on every channel.' if trait(twin, 'creator_follow') >= 55 else 'A modest lift here; the premise sells more than the names.'})
    levers.sort(key=lambda l: -l['delta_people'])
    for i, lv in enumerate(levers, 1):
        lv['rank'] = i
    holdout = _holdout(twin, plan_outcome(twin, sig_ch, momentum), seed)
    saved = round(as_alloc['spend'] - minimum['spend'], 2)
    outputs = {'goal': goal, 'noun': noun, 'platform': home, 'window_weeks': weeks, 'plans': plans, 'weeks': week_rows,
               'levers': levers, 'holdout': holdout, 'organic_first_month': messy_count(seed, 'organic', organic),
               'expected': signal['expected'], 'low': signal['low'], 'high': signal['high'], 'odds_pct': signal['odds_pct'], 'odds_words': signal['odds_words'],
               'signal_led_vs_allocated_pct': pct1((signal['expected'] - as_alloc['expected']) / max(1, as_alloc['expected']) * 100),
               'saved_vs_allocated': saved}
    drivers = [
        f"The signal-led mix lands {pct1(outputs['signal_led_vs_allocated_pct'])}% more {noun} than the plan as allocated, for the same {human_money(total)}.",
        f"Organic first month on {home} runs about {human_people(outputs['organic_first_month'])} before any paid reach.",
    ]
    top_ch = max(plans[1]['by_channel'], key=lambda b: b['first_actions']) if plans[1]['by_channel'] else None
    if top_ch:
        drivers.append(f"{top_ch['channel']} carries the plan at {fmt_money(top_ch['cpa'])} per first action.")
    comparison = {'baseline_label': 'the plan as allocated', 'baseline_value': as_alloc['expected'], 'index': int(round(signal['expected'] / max(1, as_alloc['expected']) * 100)),
                  'read': f"Signal-led indexes {int(round(signal['expected'] / max(1, as_alloc['expected']) * 100))} against the plan as allocated (100)."}
    mix_txt = ', '.join(f"{c['channel']} {human_money(c['spend'])}" for c in sig_ch)
    rec = (f"Run the signal-led plan: {mix_txt}. "
           + (f"{signal['odds_words'].capitalize()} chance it clears {fmt_people(goal['value'])} {noun}. " if signal['odds_words'] else '')
           + f"If money is tight, {minimum['label'].lower()} runs at {human_money(minimum['spend'])}. {holdout['lift_read']}")
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_content(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    kind = str(inputs.get('kind') or inputs.get('drop') or 'season').lower()
    number = inputs.get('number') or inputs.get('season') or 2
    platform = str(inputs.get('platform') or _meta(twin).get('platform_home') or 'Netflix')
    rc = (twin or {}).get('response') or {}
    srr = rc.get('sequel_return_rate') or {}
    ret_pct = float(srr.get('return_pct') or 40) / 100.0
    decay = float(srr.get('decay_per_entry_pct') or 8) / 100.0
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 2
    returning = size * ret_pct * ((1 - decay) ** max(0, n - 2))
    wom = trait(twin, 'word_of_mouth') / 100.0
    new = returning * (0.18 + 0.42 * wom) * (1.0 if kind in ('season', 'sequel') else 0.35)
    reach_raw = (returning + new) * (1.0 if kind != 'episode' else 0.62)
    viewers_raw = reach_raw * (0.74 + 0.16 * trait(twin, 'trial_speed') / 100.0)
    sigma = _sigma(twin) * 0.8
    expected = messy_count(seed, 'content|exp', viewers_raw)
    low = messy_count(seed, 'content|low', viewers_raw * math.exp(-sigma))
    high = messy_count(seed, 'content|high', viewers_raw * math.exp(sigma))
    low, high = min(low, expected - 1), max(high, expected + 1)
    completion = pct1(clamp(44 + 0.3 * trait(twin, 'binge_vs_appointment') + 0.1 * trait(twin, 'franchise_loyalty') + (unit('ccomp', seed) - 0.5) * 4, 20, 94))
    renew = pct1(clamp(22 + 0.35 * trait(twin, 'franchise_loyalty') - 0.1 * trait(twin, 'churn_propensity') + (unit('renew', seed) - 0.5) * 3, 3, 80))
    churn_risk = pct1(clamp(9 + 0.3 * trait(twin, 'churn_propensity') - 0.08 * trait(twin, 'franchise_loyalty') + (unit('crisk', seed) - 0.5) * 2, 1.5, 60))
    outputs = {'kind': kind, 'number': n, 'platform': platform, 'reach': messy_count(seed, 'content|reach', reach_raw),
               'returning': messy_count(seed, 'content|ret', returning), 'new': messy_count(seed, 'content|new', new),
               'expected': expected, 'low': low, 'high': high, 'completion_pct': completion, 'return_pct': pct1(ret_pct * 100),
               'subscribe_renew_pct': renew, 'churn_risk_at_end_pct': churn_risk, 'noun': _noun_for(twin)}
    drivers = [f"{pct1(ret_pct * 100)}% of the audience comes back for the next entry, slipping {pct1(decay * 100)}% each time.",
               f"Word of mouth adds {human_people(outputs['new'])} people who did not watch the last one." if new > 0 else 'Little new audience arrives without a push.']
    comparison = {'baseline_label': 'the current audience', 'baseline_value': size, 'index': int(round(viewers_raw / size * 100)),
                  'read': f"The next {kind} draws {pct1(viewers_raw / size * 100)}% of today's audience back."}
    rec = (f"Expect {human_people(expected)} {outputs['noun']} for {kind} {n} on {platform}, with {renew}% renewing or subscribing to watch it and "
           f"{churn_risk}% at risk of leaving when it ends. Line up the next title before the finale to hold them.")
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_platform_move(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    src = str(inputs.get('from') or inputs.get('from_platform') or _meta(twin).get('platform_home') or 'Netflix')
    dst = str(inputs.get('to') or inputs.get('to_platform') or 'Hulu')
    window_days = inputs.get('window_change_days')
    rc = (twin or {}).get('response') or {}
    pmr = rc.get('platform_move_retention') or {}
    base = float(pmr.get('base_retention_pct') or 55) / 100.0
    overlap = (platform_reach_pct(twin, dst) / 100.0) * float(pmr.get('overlap_weight') or 0.7)
    retention = clamp(base * (0.55 + 0.45 * overlap / 0.6) + float(pmr.get('loyalty_bonus_pct') or 5) / 100.0 * 0.4, 0.08, 0.96)
    retained = size * retention
    gained = size * platform_reach_pct(twin, dst) / 100.0 * (1 - platform_reach_pct(twin, src) / 100.0) * (0.12 + 0.2 * trait(twin, 'trial_speed') / 100.0)
    lost = size - retained
    win_note = ''
    if window_days:
        try:
            d = int(window_days)
            shift = clamp(-d / 90.0 * 0.06, -0.12, 0.12)
            retained *= 1 + shift
            win_note = f"Shortening the window {abs(d)} days moves the carried audience {'up' if shift > 0 else 'down'} about {pct1(abs(shift) * 100)}%."
        except (TypeError, ValueError):
            pass
    sigma = _sigma(twin) * 0.6
    expected = messy_count(seed, 'pm|exp', retained + gained)
    low = messy_count(seed, 'pm|low', (retained + gained) * math.exp(-sigma))
    high = messy_count(seed, 'pm|high', (retained + gained) * math.exp(sigma))
    low, high = min(low, expected - 1), max(high, expected + 1)
    outputs = {'from': src, 'to': dst, 'retention_pct': pct1(retention * 100), 'retained': messy_count(seed, 'pm|ret', retained),
               'gained': messy_count(seed, 'pm|gain', gained), 'lost': messy_count(seed, 'pm|lost', lost),
               'expected': expected, 'low': low, 'high': high, 'net_change_pct': pct1((retained + gained - size) / size * 100), 'window_note': win_note}
    drivers = [f"{pct1(platform_reach_pct(twin, dst))}% of this audience is already on {dst}; the rest has to follow or be found again.",
               f"Loyalty carries {pct1(retention * 100)}% of them across; churn-prone groups leave more at a move."]
    if win_note:
        drivers.append(win_note)
    comparison = {'baseline_label': f"the audience on {src}", 'baseline_value': size, 'index': int(round((retained + gained) / size * 100)),
                  'read': f"After the move the audience indexes {int(round((retained + gained) / size * 100))} against today (100)."}
    rec = (f"Moving from {src} to {dst} keeps {pct1(retention * 100)}% of the audience and picks up {human_people(outputs['gained'])} new people on {dst}, "
           f"a net {outputs['net_change_pct']:+.1f}%. {'Worth it if the deal pays for the lost ' + human_people(outputs['lost']) + '.' if outputs['net_change_pct'] < 0 else 'The move grows the audience on its own.'}")
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_partnership(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    brand = str(inputs.get('brand') or 'the brand')
    category = str(inputs.get('category') or '')
    basket = float(inputs.get('basket') or inputs.get('price') or 0) or round(24 + 60 * unit('basket', brand, seed), 2)
    # affinity: the twin's purchase rows name the brand or its category
    purchase = ((twin or {}).get('identity') or {}).get('purchase') or []
    aff = 1.0
    for p in purchase:
        lab = str(p.get('label') or '').lower()
        if brand.lower() in lab or (category and category.lower() in lab):
            aff = 1.0 + clamp(float(p.get('pct') or 0) / 25.0, 0.1, 1.6)
            break
    first_action = clamp((0.012 + 0.03 * trait(twin, 'ad_tolerance') / 100.0 + 0.02 * trait(twin, 'creator_follow') / 100.0) * aff, 0.003, 0.25)
    conversion = clamp((0.09 + 0.26 * trait(twin, 'merch_propensity') / 100.0) * (0.8 + 0.4 * unit('conv', brand, seed)), 0.02, 0.6)
    actions_raw = size * first_action
    conv_raw = actions_raw * conversion
    value_raw = conv_raw * basket
    sigma = _sigma(twin) * 0.9
    outputs = {'brand': brand, 'category': category, 'first_action_rate_pct': pct1(first_action * 100), 'conversion_pct': pct1(conversion * 100),
               'first_actions': messy_count(seed, 'bp|fa', actions_raw), 'conversions': messy_count(seed, 'bp|conv', conv_raw),
               'basket': messy_money(seed, 'bp|basket', basket),
               'expected': messy_money(seed, 'bp|value', value_raw), 'low': messy_money(seed, 'bp|low', value_raw * math.exp(-sigma)),
               'high': messy_money(seed, 'bp|high', value_raw * math.exp(sigma)), 'unit': 'usd', 'affinity_index': int(round(aff * 100))}
    outputs['low'] = min(outputs['low'], round(outputs['expected'] - 0.01, 2))
    outputs['high'] = max(outputs['high'], round(outputs['expected'] + 0.01, 2))
    drivers = [f"{pct1(first_action * 100)}% of the audience takes a first action on a {brand} message; {pct1(conversion * 100)}% of those buy.",
               (f"{brand} already shows up in what this group buys (index {int(round(aff * 100))})." if aff > 1.0 else f"{brand} is new to this group's basket; the fit is the audience, not the habit.")]
    comparison = {'baseline_label': 'a general US audience', 'baseline_value': 100, 'index': int(round(aff * 100 * (0.9 + 0.2 * trait(twin, 'ad_tolerance') / 100.0))),
                  'read': f"This audience indexes {int(round(aff * 100 * (0.9 + 0.2 * trait(twin, 'ad_tolerance') / 100.0)))} on acting on {brand} against a general US audience (100)."}
    rec = (f"A {brand} x {_subject(twin)} partnership is worth about {human_money(outputs['expected'])} in first-year purchases ({human_people(outputs['conversions'])} buyers). "
           + ('Lead with the people they follow; the first action comes from the face, the purchase from the fit.' if trait(twin, 'creator_follow') >= 55 else 'Keep the offer simple and the product in frame; this group buys on fit, not on personality.'))
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_pricing(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    rc = (twin or {}).get('response') or {}
    pe = rc.get('price_elasticity') or {}
    e = float(pe.get('elasticity') or -1.4)
    try:
        p0 = float(str(inputs.get('current_price') or pe.get('reference_price') or 12.99).replace('$', ''))
        p1 = float(str(inputs.get('new_price') or inputs.get('price') or p0 * 1.1).replace('$', ''))
    except (TypeError, ValueError):
        p0, p1 = float(pe.get('reference_price') or 12.99), float(pe.get('reference_price') or 12.99) * 1.1
    packaging = str(inputs.get('packaging') or '')
    take0 = clamp(0.22 + 0.4 * trait(twin, 'trial_speed') / 100.0 - 0.1 * trait(twin, 'price_sensitivity') / 100.0, 0.03, 0.9)
    ratio = (p1 / p0) ** e if p0 > 0 else 1.0
    take1 = clamp(take0 * ratio, 0.005, 0.95)
    if packaging and any(w in packaging.lower() for w in ('bundle', 'annual', 'ad-supported', 'ad supported', 'with ads')):
        take1 *= 1.06
    churn0 = clamp(0.05 + 0.3 * trait(twin, 'churn_propensity') / 100.0, 0.01, 0.6)
    churn1 = clamp(churn0 * (1 + max(0.0, (p1 / p0 - 1)) * (0.6 + 0.8 * trait(twin, 'price_sensitivity') / 100.0)), 0.01, 0.8)
    subs0 = size * take0
    subs1 = size * take1
    rev0 = subs0 * p0 * 12 * (1 - churn0 / 2)
    rev1 = subs1 * p1 * 12 * (1 - churn1 / 2)
    sigma = _sigma(twin) * 0.5
    outputs = {'current_price': round(p0, 2), 'new_price': round(p1, 2), 'packaging': packaging, 'elasticity': round(e, 3),
               'take_rate_before_pct': pct1(take0 * 100), 'take_rate_after_pct': pct1(take1 * 100),
               'subscribers_before': messy_count(seed, 'pr|s0', subs0), 'subscribers_after': messy_count(seed, 'pr|s1', subs1),
               'churn_before_pct': pct1(churn0 * 100), 'churn_after_pct': pct1(churn1 * 100),
               'revenue_before': messy_money(seed, 'pr|r0', rev0), 'expected': messy_money(seed, 'pr|r1', rev1),
               'low': messy_money(seed, 'pr|low', rev1 * math.exp(-sigma)), 'high': messy_money(seed, 'pr|high', rev1 * math.exp(sigma)),
               'revenue_change_pct': pct1((rev1 - rev0) / max(1, rev0) * 100), 'unit': 'usd'}
    outputs['low'] = min(outputs['low'], round(outputs['expected'] - 0.01, 2))
    outputs['high'] = max(outputs['high'], round(outputs['expected'] + 0.01, 2))
    drivers = [f"Each 10% on the price moves take-up about {abs(e) * 10:.1f}% the other way for this group.",
               f"Churn moves from {pct1(churn0 * 100)}% to {pct1(churn1 * 100)}% at the new price."]
    comparison = {'baseline_label': f"today at {fmt_money(p0)}", 'baseline_value': outputs['revenue_before'], 'index': int(round(rev1 / max(1, rev0) * 100)),
                  'read': f"Annual revenue indexes {int(round(rev1 / max(1, rev0) * 100))} against today (100)."}
    direction = 'raises' if rev1 > rev0 else 'lowers'
    rec = (f"Moving from {fmt_money(p0)} to {fmt_money(p1)} {direction} annual revenue {abs(outputs['revenue_change_pct']):.1f}% "
           f"({human_money(rev0)} to {human_money(rev1)}): take-up goes from {pct1(take0 * 100)}% to {pct1(take1 * 100)}%. "
           + ('The price holds; this group absorbs it.' if rev1 > rev0 else 'The lost subscribers cost more than the extra dollars earn.'))
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_talent_attach(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    talent = str(inputs.get('talent') or inputs.get('name') or 'the name')
    role = str(inputs.get('role') or 'lead')
    rc = (twin or {}).get('response') or {}
    cal = rc.get('cast_attach_lift') or {}
    fame = float(inputs.get('fame') or 1.0)
    talent_rows = ((twin or {}).get('identity') or {}).get('talent') or []
    for t in talent_rows:
        if talent.lower() in str(t.get('label') or '').lower():
            fame *= 1 + clamp(float(t.get('pct') or 0) / 20.0, 0.1, 1.5)
            break
    reach_lift = clamp(float(cal.get('reach_lift_pct') or 6) * fame * (1.0 if role.lower() in ('lead', 'star', 'host') else 0.55), 0.5, 60)
    return_lift = clamp(float(cal.get('return_lift_pct') or 2) * fame, 0.2, 30)
    base_first_month = size * (0.14 + 0.36 * trait(twin, 'trial_speed') / 100.0) * 0.6
    added = base_first_month * reach_lift / 100.0
    sigma = _sigma(twin) * 0.7
    outputs = {'talent': talent, 'role': role, 'reach_lift_pct': pct1(reach_lift), 'return_lift_pct': pct1(return_lift),
               'expected': messy_count(seed, 'ta|exp', added), 'low': messy_count(seed, 'ta|low', added * math.exp(-sigma)),
               'high': messy_count(seed, 'ta|high', added * math.exp(sigma)), 'base_first_month': messy_count(seed, 'ta|base', base_first_month),
               'already_follow_index': int(round(fame * 100))}
    outputs['low'], outputs['high'] = min(outputs['low'], outputs['expected'] - 1), max(outputs['high'], outputs['expected'] + 1)
    drivers = [f"{talent} as {role} lifts first-month reach {pct1(reach_lift)}% and the share that comes back {pct1(return_lift)}%.",
               ('This group follows the people behind a title, so a name they know pays.' if trait(twin, 'creator_follow') >= 55 else 'This group follows the title more than the names; the lift is real but modest.')]
    comparison = {'baseline_label': 'the first month without the attach', 'baseline_value': outputs['base_first_month'], 'index': int(round(100 + reach_lift)),
                  'read': f"With {talent} attached the first month indexes {int(round(100 + reach_lift))} against the title alone (100)."}
    rec = f"Attaching {talent} adds about {human_people(outputs['expected'])} people to the first month (a {pct1(reach_lift)}% lift) and {pct1(return_lift)}% more of them come back. " + ('Worth the fee at the usual range.' if reach_lift >= 8 else 'Only worth it at a modest fee.')
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


def run_merch_drop(twin: dict, inputs: dict, seed: str) -> dict:
    size = _size(twin)
    product = str(inputs.get('product') or 'the drop')
    rc = (twin or {}).get('response') or {}
    pe = rc.get('price_elasticity') or {}
    try:
        price = float(str(inputs.get('price') or 39.0).replace('$', '').replace(',', ''))
    except (TypeError, ValueError):
        price = 39.0
    ref = float(inputs.get('reference_price') or 34.0)
    e = float(pe.get('elasticity') or -1.4)
    prop = trait(twin, 'merch_propensity') / 100.0
    base_rate = clamp(0.004 + 0.07 * prop * (0.7 + 0.6 * trait(twin, 'franchise_loyalty') / 100.0), 0.001, 0.2)
    rate = clamp(base_rate * (price / ref) ** e, 0.0003, 0.3)
    buyers_raw = size * rate
    cap = inputs.get('units_cap')
    if cap:
        try:
            buyers_raw = min(buyers_raw, float(cap))
        except (TypeError, ValueError):
            pass
    rev_raw = buyers_raw * price
    sigma = _sigma(twin) * 0.9
    outputs = {'product': product, 'price': round(price, 2), 'buy_rate_pct': pct4(rate * 100), 'buyers': messy_count(seed, 'md|buyers', buyers_raw),
               'buyers_low': messy_count(seed, 'md|blow', buyers_raw * math.exp(-sigma)), 'buyers_high': messy_count(seed, 'md|bhigh', buyers_raw * math.exp(sigma)),
               'expected': messy_money(seed, 'md|rev', rev_raw), 'low': messy_money(seed, 'md|low', rev_raw * math.exp(-sigma)),
               'high': messy_money(seed, 'md|high', rev_raw * math.exp(sigma)), 'unit': 'usd'}
    outputs['low'] = min(outputs['low'], round(outputs['expected'] - 0.01, 2))
    outputs['high'] = max(outputs['high'], round(outputs['expected'] + 0.01, 2))
    drivers = [f"{pct4(rate * 100)}% of the audience buys at {fmt_money(price)}; the drop runs {'above' if price > ref else 'under'} the {fmt_money(ref)} reference.",
               ('Merch is part of how this group shows up for the title.' if prop >= 0.5 else 'Merch is a small habit here; keep the run short.')]
    comparison = {'baseline_label': f"the same drop at {fmt_money(ref)}", 'baseline_value': messy_count(seed, 'md|refbuyers', size * base_rate),
                  'index': int(round(rate / max(1e-6, base_rate) * 100)), 'read': f"At {fmt_money(price)} buyers index {int(round(rate / max(1e-6, base_rate) * 100))} against the reference price (100)."}
    rec = f"Expect {human_people(outputs['buyers'])} buyers and {human_money(outputs['expected'])} for {product} at {fmt_money(price)}. " + ('Price under the reference and the buyer count climbs faster than the dollars fall.' if abs(e) > 1.3 else 'Price is not the lever here; the window and the cast are.')
    return {'outputs': outputs, 'drivers': drivers, 'comparison': comparison, 'recommendation': rec}


RUNNERS = {
    'greenlight': run_greenlight, 'launch': run_launch, 'content': run_content, 'platform_move': run_platform_move,
    'partnership': run_partnership, 'pricing': run_pricing, 'talent_attach': run_talent_attach, 'merch_drop': run_merch_drop,
}
KIND_LABEL = {
    'greenlight': 'Greenlight Lab', 'launch': 'Launch Lab', 'content': 'Content drop', 'platform_move': 'Platform move',
    'partnership': 'Partnership', 'pricing': 'Pricing', 'talent_attach': 'Talent attach', 'merch_drop': 'Merch drop',
    'compare': 'Compare', 'spend': 'Spend Engine',
}
# The old Greenlight run (odds a title clears a goal) left the product on
# 2026-10-28 (Jenna: "remove what we currently have since I think it's
# nothing and replace with two options within it script reads and
# concepts"). Stored runs of this kind are kept on the twin but never
# shown, exported or counted; a new one is refused with the plain line.
RETIRED_RUN_KINDS = ('greenlight',)
GREENLIGHT_RETIRED_LINE = ('Greenlight is two options now: a Script read or a Concept test. Pick one on the Greenlight tab, '
                           'or type "Test this concept in Greenlight on the <twin> twin: ..." or "Read this script in Greenlight on the <twin> twin" to Prometheus.')


def is_retired_run(run) -> bool:
    """True for a stored run the product no longer shows: the old
    Greenlight run, and a compare that ran that scenario on two twins."""
    if not isinstance(run, dict):
        return False
    kind = str(run.get('kind') or '').lower()
    if kind in RETIRED_RUN_KINDS:
        return True
    if kind == 'compare':
        return str(((run.get('inputs') or {}).get('scenario_kind')) or '').lower() in RETIRED_RUN_KINDS
    return False


def product_runs(payload) -> list:
    """The runs a twin shows, exports and counts: everything stored on
    it except the retired kinds."""
    return [r for r in ((payload or {}).get('runs') or []) if isinstance(r, dict) and not is_retired_run(r)]


def default_question(kind: str, inputs: dict, twin: dict) -> str:
    subj = _subject(twin)
    if kind == 'greenlight':
        goal = _norm_goal(inputs.get('goal'), _noun_for(twin))
        t = inputs.get('title') or inputs.get('idea') or 'this idea'
        plats = ', '.join(inputs.get('platforms') or [str(_meta(twin).get('platform_home') or 'its home')])
        return f"What are the odds {t} clears {fmt_people(goal['value'])} {goal['metric']} in its first {goal['window_days']} days on {plats}?" if goal['value'] else f"How does {t} land with the {subj} twin on {plats}?"
    if kind == 'launch':
        return f"How does this plan land with the {subj} twin, and what is the least that still hits the goal?"
    if kind == 'content':
        return f"How does the next {inputs.get('kind') or 'season'} land with the {subj} twin?"
    if kind == 'platform_move':
        return f"What happens to the {subj} audience in a move from {inputs.get('from') or 'its home'} to {inputs.get('to') or 'a new home'}?"
    if kind == 'partnership':
        return f"What is {inputs.get('brand') or 'the brand'} x {subj} worth?"
    if kind == 'pricing':
        return f"What does a move to {inputs.get('new_price') or 'the new price'} do to take-up and revenue for the {subj} twin?"
    if kind == 'talent_attach':
        return f"What does attaching {inputs.get('talent') or 'the name'} do for the {subj} twin?"
    if kind == 'merch_drop':
        return f"How does {inputs.get('product') or 'the drop'} sell to the {subj} twin at {inputs.get('price') or 'the price'}?"
    return f"Run forward on the {subj} twin."


def plain_read(run: dict, twin: dict) -> dict:
    """The deterministic read: three plain sentences with different
    shapes, a few reads, and what to do. Used when the model is off."""
    o = run.get('outputs') or {}
    kind = run.get('kind')
    subj = _subject(twin)
    unit_ = o.get('unit')
    exp = o.get('expected')
    exp_txt = human_money(exp) if unit_ == 'usd' else f"{human_people(exp)} {o.get('noun') or 'people'}"
    lo_txt = human_money(o.get('low')) if unit_ == 'usd' else human_people(o.get('low'))
    hi_txt = human_money(o.get('high')) if unit_ == 'usd' else human_people(o.get('high'))
    tldr = []
    if o.get('odds_words') and (o.get('goal') or {}).get('value'):
        tldr.append(f"{o['odds_words'].capitalize()} chance it clears {fmt_people(o['goal']['value'])} {o.get('noun', 'people')}.")
    tldr.append(f"Expected: {exp_txt}, with a range of {lo_txt} to {hi_txt}.")
    comp = run.get('comparison') or {}
    if comp.get('read'):
        tldr.append(comp['read'])
    reads = list(run.get('drivers') or [])[:4]
    what = [run.get('recommendation') or '']
    if kind == 'greenlight' and (o.get('levers') or []):
        lv = o['levers'][0]
        what.append(f"The lever that earns most is '{lv['lever']}': about {human_people(lv['delta_people'])} more people.")
    return {'tldr': [t for t in tldr if t], 'reads': [r for r in reads if r], 'what_to_do': [w for w in what if w]}


def simulate(twin: dict, scenario: dict, read_fn: Optional[Callable] = None, created_by: str = '') -> dict:
    """One run on one twin. ``scenario``: {kind, question?, inputs}.
    Returns the run record to append to the twin's ``runs``."""
    kind = str((scenario or {}).get('kind') or '').lower().replace('-', '_')
    if kind in RETIRED_RUN_KINDS or not kind:
        raise ValueError(GREENLIGHT_RETIRED_LINE)
    if kind not in RUNNERS:
        raise ValueError(f'unknown scenario kind: {kind}')
    inputs = dict((scenario or {}).get('inputs') or {})
    seed = '|'.join([_subject(twin), str(_meta(twin).get('window_start') or ''), str(VERSION), kind,
                     '|'.join(f"{k}={inputs.get(k)}" for k in sorted(inputs) if k not in ('anchors', 'momentum'))])
    body = RUNNERS[kind](twin, inputs, seed)
    run = {
        'id': uuid.uuid4().hex[:10], 'kind': kind, 'label': KIND_LABEL.get(kind, kind),
        'question': str((scenario or {}).get('question') or default_question(kind, inputs, twin))[:300],
        'inputs': {k: v for k, v in inputs.items() if k != 'anchors'},
        'outputs': body['outputs'], 'drivers': body['drivers'], 'comparison': body['comparison'],
        'recommendation': body['recommendation'],
        'created_by': created_by or 'prometheus', 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'version': VERSION,
    }
    run['read'] = plain_read(run, twin)
    if read_fn is not None:
        try:
            better = read_fn(run, twin)
            if isinstance(better, dict) and better.get('tldr'):
                run['read'] = {'tldr': [str(x) for x in better.get('tldr') or []][:5],
                               'reads': [str(x) for x in better.get('reads') or run['read']['reads']][:6],
                               'what_to_do': [str(x) for x in better.get('what_to_do') or run['read']['what_to_do']][:5]}
        except Exception:
            pass
    return run


def compare(twin_a: dict, twin_b: dict, scenario: dict, read_fn: Optional[Callable] = None, created_by: str = '') -> dict:
    """Two twins on the same scenario, side by side."""
    ra = simulate(twin_a, scenario, created_by=created_by)
    rb = simulate(twin_b, scenario, created_by=created_by)
    a, b = ra['outputs'], rb['outputs']
    unit_ = a.get('unit')
    ea, eb = float(a.get('expected') or 0), float(b.get('expected') or 0)
    delta_pct = pct1((ea - eb) / max(1e-9, eb) * 100) if eb else 0.0
    na, nb = _subject(twin_a), _subject(twin_b)
    lead = na if ea >= eb else nb
    run = {
        'id': uuid.uuid4().hex[:10], 'kind': 'compare', 'label': 'Compare',
        'question': str((scenario or {}).get('question') or f"{na} against {nb} on the same {KIND_LABEL.get(ra['kind'], ra['kind']).lower()}")[:300],
        'inputs': {'scenario_kind': ra['kind'], 'twins': [na, nb], **{k: v for k, v in (scenario.get('inputs') or {}).items() if k != 'anchors'}},
        'outputs': {'a': {'twin': na, **a}, 'b': {'twin': nb, **b}, 'delta_pct': delta_pct, 'leader': lead, 'unit': unit_,
                    'expected': a.get('expected'), 'low': a.get('low'), 'high': a.get('high'), 'noun': a.get('noun')},
        'drivers': [f"{na}: {human_money(ea) if unit_ == 'usd' else human_people(ea)} expected.", f"{nb}: {human_money(eb) if unit_ == 'usd' else human_people(eb)} expected."] + ra['drivers'][:1] + rb['drivers'][:1],
        'comparison': {'baseline_label': nb, 'baseline_value': b.get('expected'), 'index': int(round(ea / max(1e-9, eb) * 100)) if eb else 100,
                       'read': f"{na} indexes {int(round(ea / max(1e-9, eb) * 100)) if eb else 100} against {nb} (100) on this run."},
        'recommendation': f"{lead} leads by {abs(delta_pct):.1f}%. " + (ra['recommendation'] if lead == na else rb['recommendation']),
        'sub_runs': [ra, rb], 'created_by': created_by or 'prometheus',
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'version': VERSION,
    }
    run['read'] = {'tldr': [f"{lead} leads by {abs(delta_pct):.1f}% on this run.", run['comparison']['read']],
                   'reads': run['drivers'], 'what_to_do': [run['recommendation']]}
    if read_fn is not None:
        try:
            better = read_fn(run, twin_a)
            if isinstance(better, dict) and better.get('tldr'):
                run['read'] = {'tldr': [str(x) for x in better['tldr']][:5], 'reads': [str(x) for x in better.get('reads') or run['read']['reads']][:6],
                               'what_to_do': [str(x) for x in better.get('what_to_do') or run['read']['what_to_do']][:5]}
        except Exception:
            pass
    return run


def parse_channels(text: str) -> list:
    """'Meta $120k, Google 80,000, TikTok 40k' -> [{channel, spend}]."""
    out = []
    t = str(text or '')
    rx = re.compile(r"(?P<name>[A-Za-z][A-Za-z +/&-]{1,24}?)\s*[:=-]?\s*\$?\s*(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?P<k>[kKmM])?\b")
    for m in rx.finditer(t):
        name = canon_channel(m.group('name').strip(' ,;:-'))
        if m.group('name').strip().lower() in ('week', 'weeks', 'day', 'days', 'goal', 'budget', 'total', 'window', 'spend'):
            continue
        try:
            amt = float(m.group('amt').replace(',', ''))
        except ValueError:
            continue
        k = (m.group('k') or '').lower()
        amt *= 1000 if k == 'k' else 1_000_000 if k == 'm' else 1
        if name == 'Other' and not any(a in m.group('name').lower() for a in ('other', 'ooh', 'radio', 'print')):
            continue
        out.append({'channel': name, 'spend': round(amt, 2)})
    merged = {}
    for c in out:
        merged[c['channel']] = merged.get(c['channel'], 0.0) + c['spend']
    return [{'channel': k, 'spend': round(v, 2)} for k, v in merged.items() if v > 0]


__all__ = ['simulate', 'compare', 'RUNNERS', 'KIND_LABEL', 'RETIRED_RUN_KINDS', 'GREENLIGHT_RETIRED_LINE', 'is_retired_run', 'product_runs', 'TRAITS', 'TRAIT_KEYS', 'TRAIT_LABEL', 'CHANNELS',
           'messy_count', 'natural_count', 'messy_money', 'pct4', 'pct1', 'off_boundary', 'normalize_100', 'unit', 'jit', 'seed_int',
           'odds_words', 'odds_of_clearing', 'trait', 'trait_block', 'trait_read', 'response_curves', 'channel_signal', 'channel_params',
           'allocate_signal_led', 'plan_outcome', 'canon_channel', 'platform_reach_pct', 'parse_channels', 'plain_read', 'default_question',
           'fmt_people', 'fmt_money', 'human_money', 'human_people', 'PLATFORM_PRIOR_REACH', 'VERSION', 'US_POP']
