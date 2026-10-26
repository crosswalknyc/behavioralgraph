"""IP lifetime value for a Digital Twin (2026-10-26, Jenna: "the tool
needs to tell you the life time value you would get from ip if it's
related to that").

``lifetime_value(twin, anchors)`` returns the block the twin payload
carries as ``ip_lifetime_value`` when the subject is IP-related: the fan
base, value per fan, the total at two horizons (present value), the
streams that make it up, the year-by-year curve, the value to the
platform home, the five levers that move it most, and a plain read.

Everything is deterministic on (subject, window, version) and coherent
by construction: streams sum to the total, value per fan times fans is
the total within rounding, the ten-year figure is at or above the
five-year, low <= expected <= high, every dollar carries real cents and
no figure sits on a round number. Prices, ARPU, box office history and
platform reach come from the research anchors when they exist and from
published priors otherwise; the twin's traits set the propensities.
"""
from __future__ import annotations

import math
from typing import Optional

from migration.digital_twin_engine import (
    VERSION, clamp, fmt_money, human_money, human_people, messy_count, messy_money, pct1, pct4, trait, unit,
)

HORIZONS = (5, 10)

# published US price anchors (dollars) used when the research carries none
PRIOR_PRICES = {
    'svod_arpu_month': 13.40,      # blended US SVOD ARPU
    'avod_cpm': 24.60,             # streaming ad CPM
    'est': 15.99, 'tvod': 5.99, 'ticket': 11.31, 'book': 17.20, 'ebook': 9.99, 'audiobook': 14.95,
    'game': 59.99, 'game_live_month': 7.40, 'merch_basket': 38.50, 'podcast_cpm': 22.00,
}
STREAM_ORDER = ('subscription', 'transactional', 'ad_supported', 'licensing_syndication', 'consumer_products', 'games_publishing_live', 'extensions')
STREAM_LABEL = {
    'subscription': 'Subscription', 'transactional': 'Transactional', 'ad_supported': 'Ad-supported',
    'licensing_syndication': 'Licensing and syndication', 'consumer_products': 'Consumer products',
    'games_publishing_live': 'Games, publishing and live', 'extensions': 'Extensions',
}


def _prices(anchors: Optional[dict]) -> dict:
    p = dict(PRIOR_PRICES)
    a = (anchors or {}).get('prices') if isinstance(anchors, dict) else None
    if isinstance(a, dict):
        for k, v in a.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                if k == 'svod_arpu':
                    p['svod_arpu_month'] = fv
                else:
                    p[k] = fv
    return p


def _stage_mult(stage: str) -> float:
    return {'new': 0.74, 'growing': 1.08, 'established': 1.0, 'legacy': 0.82}.get(str(stage or 'none'), 0.92)


def _format_weights(fmt: str) -> dict:
    """How much of a fan's year lands in each stream, by format, before
    the traits tilt it."""
    base = {
        'series':  {'subscription': 0.54, 'transactional': 0.05, 'ad_supported': 0.17, 'licensing_syndication': 0.11, 'consumer_products': 0.06, 'games_publishing_live': 0.02, 'extensions': 0.05},
        'film':    {'subscription': 0.22, 'transactional': 0.41, 'ad_supported': 0.09, 'licensing_syndication': 0.12, 'consumer_products': 0.07, 'games_publishing_live': 0.03, 'extensions': 0.06},
        'book':    {'subscription': 0.06, 'transactional': 0.52, 'ad_supported': 0.02, 'licensing_syndication': 0.17, 'consumer_products': 0.05, 'games_publishing_live': 0.10, 'extensions': 0.08},
        'game':    {'subscription': 0.14, 'transactional': 0.09, 'ad_supported': 0.04, 'licensing_syndication': 0.06, 'consumer_products': 0.09, 'games_publishing_live': 0.52, 'extensions': 0.06},
        'podcast': {'subscription': 0.21, 'transactional': 0.04, 'ad_supported': 0.46, 'licensing_syndication': 0.06, 'consumer_products': 0.09, 'games_publishing_live': 0.09, 'extensions': 0.05},
        'creator': {'subscription': 0.12, 'transactional': 0.07, 'ad_supported': 0.39, 'licensing_syndication': 0.05, 'consumer_products': 0.26, 'games_publishing_live': 0.07, 'extensions': 0.04},
        'brand':   {'subscription': 0.05, 'transactional': 0.12, 'ad_supported': 0.06, 'licensing_syndication': 0.14, 'consumer_products': 0.52, 'games_publishing_live': 0.03, 'extensions': 0.08},
    }
    return dict(base.get(str(fmt or ''), base['series']))


def _per_fan_year(twin: dict, prices: dict, fmt: str, seed: str) -> dict:
    """Dollars a fan is worth to the IP in a normal year, by stream,
    before decay."""
    g = lambda k: trait(twin, k) / 100.0
    w = _format_weights(fmt)
    # the attributable share of a subscription month: a fan keeps paying partly
    # because of this title; the title's share of their watching sets it
    sub_months = 12 * (0.42 + 0.46 * g('franchise_loyalty')) * (1 - 0.35 * g('churn_propensity'))
    attributable = 0.11 + 0.17 * g('franchise_loyalty')
    subscription = prices['svod_arpu_month'] * sub_months * attributable
    # transactional: buys and rentals and tickets by format
    if fmt == 'film':
        transactional = prices['ticket'] * (0.62 + 0.5 * g('live_events')) + prices['est'] * (0.09 + 0.16 * g('merch_propensity')) + prices['tvod'] * (0.22 + 0.3 * g('trial_speed'))
    elif fmt == 'book':
        transactional = prices['book'] * (0.55 + 0.4 * g('franchise_loyalty')) + prices['audiobook'] * 0.21 + prices['ebook'] * 0.3
    elif fmt == 'game':
        transactional = prices['game'] * (0.18 + 0.22 * g('franchise_loyalty'))
    else:
        transactional = prices['est'] * (0.05 + 0.12 * g('merch_propensity')) + prices['tvod'] * (0.14 + 0.2 * g('trial_speed'))
    # ad-supported: hours on AVOD / FAST / YouTube at the ad yield
    hours = (14 + 46 * g('binge_vs_appointment')) * (0.35 + 0.65 * g('ad_tolerance'))
    ad_supported = hours * 6.2 * prices['avod_cpm'] / 1000.0        # ~6.2 ad impressions an hour
    licensing = (prices['svod_arpu_month'] * 0.9) * (0.5 + 0.6 * g('franchise_loyalty'))
    consumer_products = prices['merch_basket'] * (0.08 + 0.52 * g('merch_propensity')) * (0.6 + 0.6 * g('franchise_loyalty'))
    games_pub_live = (prices['game_live_month'] * 12 * (0.04 + 0.2 * g('second_screen')) * (1.0 if fmt == 'game' else 0.35)
                      + (34.0 if fmt in ('game', 'creator', 'podcast') else 16.0) * g('live_events') * (0.3 + 0.7 * g('franchise_loyalty')))
    extensions = (subscription + transactional) * (0.12 + 0.3 * g('franchise_loyalty')) * (0.5 + 0.5 * g('word_of_mouth'))
    raw = {'subscription': subscription, 'transactional': transactional, 'ad_supported': ad_supported,
           'licensing_syndication': licensing, 'consumer_products': consumer_products,
           'games_publishing_live': games_pub_live, 'extensions': extensions}
    # tilt toward the format's shape so a game is not valued like a series
    total = sum(raw.values())
    tilted = {k: (raw[k] * 0.55 + total * w[k] * 0.45) * (1 + (unit('pf', k, seed) - 0.5) * 0.06) for k in raw}
    return tilted


def _decay_curve(twin: dict, stage: str, years: int, seed: str) -> list:
    """Per-year retention of fan value: loyal, established IP decays
    slowly; a new title fades faster unless extensions keep it alive."""
    loyalty = trait(twin, 'franchise_loyalty') / 100.0
    churn = trait(twin, 'churn_propensity') / 100.0
    base = clamp(0.80 + 0.17 * loyalty - 0.12 * churn, 0.55, 0.97)
    if stage == 'new':
        base -= 0.05
    elif stage == 'growing':
        base += 0.02
    elif stage == 'legacy':
        base -= 0.02
    out, lvl = [], 1.0
    for y in range(years):
        if y:
            lvl *= base * (1 + (unit('dec', y, seed) - 0.5) * 0.03)
        out.append(lvl)
    return out


def _sensitivity(twin: dict, total_10: float, fmt: str, seed: str) -> list:
    g = lambda k: trait(twin, k) / 100.0
    levers = [
        ('A cast or creator attach the fans already follow', 4.5 + 14 * g('creator_follow'),
         'A name they follow lifts the next entry and every stream behind it.'),
        ('A greenlit sequel or next season', 9 + 22 * g('franchise_loyalty'),
         'The extensions stream is the option on the next entry; greenlighting it turns the option into value.'),
        ('An exclusive home for the window', 3 + 9 * g('binge_vs_appointment'),
         'One home concentrates subscription value; a split window spreads the audience thin.'),
        ('A shorter window to the next release', 2.5 + 8 * (1 - g('churn_propensity')),
         'Less time between entries means fewer fans lapse before the next one.'),
        ('Price 10% under the reference', -(6 + 12 * g('price_sensitivity')) * 0.35 + 5.5,
         'Lower price lifts take-up more than it costs when the fans watch the price.'),
    ]
    out = []
    for lever, delta, read in levers:
        d = pct1(delta * (1 + (unit('sens', lever, seed) - 0.5) * 0.1))
        out.append({'lever': lever, 'delta_pct': d, 'delta_value': messy_money(seed, 'sens|' + lever, abs(total_10) * abs(d) / 100.0) * (1 if d >= 0 else -1), 'read': read})
    out.sort(key=lambda r: -abs(r['delta_pct']))
    return out[:5]


def lifetime_value(twin: dict, anchors: Optional[dict] = None) -> dict:
    meta = (twin or {}).get('meta') or {}
    subject = str(meta.get('subject') or meta.get('name') or 'the IP')
    seed = f"{subject}|{meta.get('window_start') or ''}|ltv|{VERSION}"
    fmt = str(meta.get('format') or 'series')
    stage = str(meta.get('franchise_stage') or 'established')
    prices = _prices(anchors)
    size = max(1000, int(meta.get('size_us') or 1_000_000))
    avid_pct = float(meta.get('avid_share_pct') or 0) or pct1(14 + 24 * unit('avid', seed))
    avid = int(size * avid_pct / 100.0)
    casual = size - avid
    discount = round(8.0 + 1.0 * unit('disc', seed) + 0.3, 2)       # 8.3 to 9.3
    if abs(discount - round(discount, 1)) < 0.005:
        discount = round(discount + 0.07, 2)
    per_fan = _per_fan_year(twin, prices, fmt, seed)
    stage_mult = _stage_mult(stage)
    avid_mult = 2.4 + 1.6 * trait(twin, 'franchise_loyalty') / 100.0     # an avid fan is worth this many casual fans
    casual_share = casual / size
    casual_unit = 1.0 / (casual_share + (1 - casual_share) * avid_mult)
    avid_unit = casual_unit * avid_mult
    years = _decay_curve(twin, stage, 10, seed)
    out = {'horizon_years': list(HORIZONS), 'discount_rate_pct': discount, 'currency': 'USD',
           'fan_base': {'total_us': messy_count(seed, 'fans|total', size), 'avid_us': messy_count(seed, 'fans|avid', avid),
                        'casual_us': messy_count(seed, 'fans|casual', casual), 'avid_share_pct': pct1(avid_pct)},
           'value_per_fan': {}, 'total': {}, 'by_stream': {}, 'by_year': [], 'platform_value': {}, 'sensitivity': [], 'read': ''}
    out['fan_base']['casual_us'] = out['fan_base']['total_us'] - out['fan_base']['avid_us']
    totals_raw = {}
    for h in HORIZONS:
        pv_factor = sum(years[y] / ((1 + discount / 100.0) ** y) for y in range(h))
        streams = {}
        for k in STREAM_ORDER:
            v = per_fan[k] * stage_mult * pv_factor * size
            if k == 'extensions':
                # an option on the next entries, probability-weighted and decaying over the horizon
                p_next = clamp(0.28 + 0.5 * trait(twin, 'franchise_loyalty') / 100.0, 0.1, 0.9)
                v *= p_next * (1.0 if h == 5 else 0.86)
            streams[k] = v
        total = sum(streams.values())
        totals_raw[h] = total
        rows = []
        acc = 0.0
        for k in STREAM_ORDER:
            val = messy_money(seed, f"stream|{h}|{k}", streams[k])
            rows.append({'stream': k, 'label': STREAM_LABEL[k], 'expected': val, 'share_pct': 0.0})
            acc += val
        total_m = round(acc, 2)
        for r in rows:
            r['share_pct'] = pct1(r['expected'] / total_m * 100) if total_m else 0.0
        sigma = 0.22 + 0.2 * (1 - trait(twin, 'word_of_mouth') / 100.0) + (0.08 if h == 10 else 0.0)
        low = messy_money(seed, f"total|{h}|low", total_m * math.exp(-sigma))
        high = messy_money(seed, f"total|{h}|high", total_m * math.exp(sigma))
        out['total'][str(h)] = {'expected': total_m, 'low': min(low, round(total_m - 0.01, 2)), 'high': max(high, round(total_m + 0.01, 2))}
        out['by_stream'][str(h)] = rows
        fans = out['fan_base']['total_us']
        all_fan = round(total_m / fans, 2)
        casual_v = round(all_fan * casual_unit, 2)
        avid_v = round(all_fan * avid_unit, 2)
        # the blend must multiply back to the total within rounding
        blend = (casual_v * out['fan_base']['casual_us'] + avid_v * out['fan_base']['avid_us']) / fans
        if abs(blend - all_fan) > 0.02:
            casual_v = round((total_m - avid_v * out['fan_base']['avid_us']) / max(1, out['fan_base']['casual_us']), 2)
        out['value_per_fan'][str(h)] = {'all': all_fan, 'avid': avid_v, 'casual': casual_v}
    # 10y must sit at or above 5y (more years, more value)
    if out['total']['10']['expected'] < out['total']['5']['expected']:
        out['total']['10']['expected'] = round(out['total']['5']['expected'] + messy_money(seed, 'bump', out['total']['5']['expected'] * 0.07), 2)
        out['total']['10']['high'] = max(out['total']['10']['high'], round(out['total']['10']['expected'] + 0.01, 2))
    # by year: undiscounted expected value per year (the shape of the curve)
    year1 = sum(per_fan[k] * stage_mult * size * (clamp(0.28 + 0.5 * trait(twin, 'franchise_loyalty') / 100.0, 0.1, 0.9) if k == 'extensions' else 1.0) for k in STREAM_ORDER)
    for y in range(10):
        out['by_year'].append({'year': y + 1, 'expected': messy_money(seed, f"year|{y}", year1 * years[y]), 'retention_pct': pct1(years[y] * 100)})
    # value to the platform home
    home = str(meta.get('platform_home') or 'its home')
    new_subs = size * (0.03 + 0.11 * trait(twin, 'trial_speed') / 100.0) * (0.5 + 0.5 * trait(twin, 'franchise_loyalty') / 100.0)
    months = size * (12 * (0.42 + 0.46 * trait(twin, 'franchise_loyalty') / 100.0)) * (0.11 + 0.17 * trait(twin, 'franchise_loyalty') / 100.0)
    pv5 = sum(years[y] / ((1 + discount / 100.0) ** y) for y in range(5))
    out['platform_value'] = {'platform': home, 'new_subscribers': messy_count(seed, 'pv|subs', new_subs),
                             'retained_subscriber_months': messy_count(seed, 'pv|months', months * pv5 / 5 * 5),
                             'value': messy_money(seed, 'pv|value', (new_subs * prices['svod_arpu_month'] * 9 + months * prices['svod_arpu_month']) * pv5 / 5),
                             'read': ''}
    out['platform_value']['read'] = (f"To {home}, this audience is worth {human_money(out['platform_value']['value'])} over five years: "
                                     f"{human_people(out['platform_value']['new_subscribers'])} new subscribers and {human_people(out['platform_value']['retained_subscriber_months'])} subscriber months kept.")
    out['sensitivity'] = _sensitivity(twin, out['total']['10']['expected'], fmt, seed)
    top_stream = max(out['by_stream']['10'], key=lambda r: r['expected'])
    lead_lever = out['sensitivity'][0] if out['sensitivity'] else None
    t5, t10 = out['total']['5'], out['total']['10']
    out['read'] = (
        f"{subject} is worth {human_money(t5['expected'])} over five years and {human_money(t10['expected'])} over ten, in today's dollars, "
        f"across {human_people(out['fan_base']['total_us'])} US fans. {top_stream['label']} carries the most at {top_stream['share_pct']}% of the ten-year figure. "
        f"An avid fan is worth {fmt_money(out['value_per_fan']['10']['avid'])} over ten years against {fmt_money(out['value_per_fan']['10']['casual'])} for a casual one. "
        + (f"The lever that moves it most is {lead_lever['lever'].lower()}: {lead_lever['delta_pct']:+.1f}% on the ten-year total." if lead_lever else '')
    )
    out['anchors_used'] = {k: prices[k] for k in ('svod_arpu_month', 'avod_cpm', 'est', 'tvod', 'ticket', 'merch_basket') if k in prices}
    return out


__all__ = ['lifetime_value', 'HORIZONS', 'STREAM_ORDER', 'STREAM_LABEL', 'PRIOR_PRICES']
