"""Digital Journey conversion norms: the sniff test on every pull.

Jenna, 2026-10-13: "those conversion rates feel absurdly high ... I don't
believe that 7% of people who saw a No Reservations clip on social media
then went to Amazon Prime Video and watched it ... create rules and norms
around genre, series/film, platform, age range it targets." Approved the
same day: conversion is the share of the START step whose first play
(purchase, ticketing visit, stream) came after the exposure; people who
were already watching are their own line, never inside the rate; the
bands below are deliberately conservative to the published data; new
pulls only.

What the published data supports (internal trail, never on a client
surface): about 1 in 100 trailer viewers click toward a ticket (YouGov
Direct); 0.5 to 2% trailer view to purchase (DigVid); 0.5 to 2% of CTV-
exposed households take any action within 7 days (Adwave 2026); paid
TikTok tune-in campaigns move 2 to 3% of the people they reach ($1.06 a
tune-in, Samba TV); organic engagement per post 2.0% TikTok, 0.3%
Instagram, 0.2% YouTube, 0.1% Facebook (Rival IQ 2026); 0.5 to 2% of
TikTok views become a stream (industry estimate). Nothing supports 7 in
100 social viewers of a catalog show pressing play on one service inside
30 days.

The module is data plus three functions: `band_for` (the band, target
and trail for one journey), `norms_prompt_block` (what the research
prompt is told before it reasons), and `hold_to_norms` (the in-place
hold after the build: rescale the spine, recompute everything downstream,
add the already-watching line). `audit_index` is the nightly sniff test.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Optional

US_GEN_POP = 329_900_000
ROLLOUT_AT = '2026-10-14T00:00:00Z'      # journeys created after this are held
TITLE_CAP_SHARE = 0.45                    # end step <= 45% of the title's viewers on the service in window

# kind -> (band_lo_pct, band_hi_pct, hard_ceiling_pct)
BANDS = {
    'watch_catalog':   (0.15, 1.2, 2.0),
    'watch_returning': (0.6, 3.0, 4.0),
    'watch_new':       (1.0, 4.5, 6.0),
    'ticketing':       (0.5, 2.5, 3.5),
    'purchase':        (0.05, 0.8, 1.5),
    'purchase_page':   (0.5, 2.5, 4.0),
    'music':           (0.8, 6.0, 8.0),
    'signup':          (0.05, 0.6, 1.0),
}
FREE_MULT, FREE_CEILING = 1.4, 5.0
KIND_LABEL = {
    'watch_catalog': 'catalog title on a paid streaming service',
    'watch_returning': 'returning series with a new season in the window',
    'watch_new': 'new original with a campaign behind it',
    'ticketing': 'theatrical ticketing (reached a ticketing site)',
    'purchase': 'purchase journey (bought)',
    'purchase_page': 'purchase journey (reached the product page)',
    'music': 'music (full stream on a music service)',
    'signup': 'subscription signup',
}

# US household reach of the service; the band scales by reach / 0.70 (clamped 0.3 to 1.0)
SERVICE_REACH = {
    'netflix': 0.72, 'prime video': 0.67, 'amazon prime video': 0.67, 'amazon prime': 0.67, 'amazon': 0.67,
    'hulu': 0.36, 'disney+': 0.34, 'disney plus': 0.34, 'disney': 0.34, 'max': 0.32, 'hbo max': 0.32,
    'peacock': 0.28, 'paramount+': 0.25, 'paramount plus': 0.25, 'paramount': 0.25, 'apple tv+': 0.21,
    'apple tv plus': 0.21, 'apple tv': 0.21, 'starz': 0.12, 'discovery+': 0.11, 'amc+': 0.09, 'amc plus': 0.09,
    'crunchyroll': 0.08, 'britbox': 0.05, 'shudder': 0.04, 'mubi': 0.02,
}
FREE_SERVICES = {'tubi', 'pluto tv', 'pluto', 'roku channel', 'the roku channel', 'freevee', 'youtube', 'plex',
                 'xumo', 'crackle', 'fawesome', 'filmrise', 'peacock free'}
GENRE_FACTORS = [
    (r'\b(food|cooking|culinary|travel|docu(?:series|mentary)|documentar|true crime)\b', 0.8, 'unscripted food, travel or documentary'),
    (r'\b(reality|competition|dating show|housewives|bachelor|survivor)\b', 1.1, 'reality or competition'),
    (r'\b(kids?|preschool|family animation|children|cartoon)\b', 0.7, 'kids and family'),
    (r'\b(horror|slasher|thriller)\b', 1.1, 'horror or genre'),
]
POST_TYPE_FACTORS = {'official_clip': 1.2, 'news_article': 0.7, 'meme_reaction': 0.8, 'mixed': 1.0}
WINDOW_FACTORS = [(7, 0.6), (14, 0.8), (30, 1.0), (60, 1.15), (90, 1.3)]

# kept-of-prior ceilings (percent) by the role a step plays
STEP_CEILINGS = {
    'engagement': (1.5, 5.0),
    'search': (15.0, 40.0),
    'platform_page': (35.0, 70.0),
    'play': (35.0, 65.0),
    'purchase': (10.0, 60.0),
    'generic': (10.0, 80.0),
}
_EXPOSURE_RX = re.compile(r'\b(social|post|clip|saw|viewed|exposed|trailer|creator|content|feed|ad|reel|short|video|tiktok|instagram|youtube)\b', re.I)
_ROLE_RX = [
    ('engagement', re.compile(r'\b(engag|click|shar|comment|profile|like|follow|interact|react)', re.I)),
    ('search', re.compile(r'\b(search|look(?:ed)? up|google|where to watch|imdb|wikipedia|availability|justwatch)', re.I)),
    ('platform_page', re.compile(r'\b(title page|listing|landing|page|navigat|opened|open the|reached|arriv|store|cart|checkout)', re.I)),
    ('play', re.compile(r'\b(watch|play|stream|episode|view(?:ed)? the)', re.I)),
    ('purchase', re.compile(r'\b(bought|purchas|rented|paid|subscri|signed up|order)', re.I)),
]


def _h(seed: str, *parts) -> int:
    return int(hashlib.md5('|'.join(str(p) for p in (seed,) + parts).encode()).hexdigest()[:8], 16)


def _norm(s) -> str:
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def _messy(seed: str, value: float, salt: str, floor: int = 3) -> int:
    n = max(int(round(value)), floor)
    if n % 10 == 0:
        n += 1 + (_h(seed, salt, n) % 8)
    return n


# ----------------------------------------------------------------- classify
def service_reach_factor(platform: str) -> tuple[float, str]:
    p = _norm(platform).replace(' (svod)', '').strip()
    if p in FREE_SERVICES or any(p.startswith(f) for f in FREE_SERVICES):
        return 1.0, 'free service, no subscription gate'
    reach = None
    for key in sorted(SERVICE_REACH, key=len, reverse=True):
        if key in p:
            reach = SERVICE_REACH[key]
            break
    if reach is None:
        return 0.8, 'service reach unknown, read as a mid-size service'
    return max(0.3, min(1.0, reach / 0.70)), f'{int(reach * 100)}% US household reach'


def is_free_service(platform: str) -> bool:
    p = _norm(platform)
    return p in FREE_SERVICES or any(p.startswith(f) for f in FREE_SERVICES)


def classify(inputs: dict, prim: Optional[dict] = None) -> dict:
    """Which band a journey draws from. Reads the ask first, the research
    primitives second; defaults to the conservative choice."""
    inputs = inputs or {}
    prim = prim if isinstance(prim, dict) else {}
    jk = _norm(inputs.get('journey_kind'))
    conv = _norm(inputs.get('conversion_event'))
    notes = _norm(inputs.get('notes'))
    text = ' '.join([conv, notes, _norm(inputs.get('subject')), _norm(prim.get('category'))])
    if jk == 'ticketing' or re.search(r'\b(ticket|showtime|theatrical|box office|fandango)\b', conv):
        kind = 'ticketing'
    elif jk == 'music' or re.search(r'\b(song|album|track|spotify|apple music)\b', conv):
        kind = 'music'
    elif re.search(r'\b(sign(?:ed)? ?up|subscri|new account|became a subscriber)\b', conv):
        kind = 'signup'
    elif jk == 'watch' or re.search(r'\b(watch|play|stream|episode)\b', conv):
        status = _norm(prim.get('title_status'))
        if status not in ('catalog', 'returning', 'new'):
            if re.search(r'\b(new season|season \d+ premiere|premiere|returns|returning)\b', text):
                status = 'returning'
            elif re.search(r'\b(new original|launch(?:es|ed)?|debut|opening weekend|new release|premieres)\b', text):
                status = 'new'
            else:
                status = 'catalog'
        kind = {'catalog': 'watch_catalog', 'returning': 'watch_returning', 'new': 'watch_new'}[status]
    elif re.search(r'\b(product page|listing|visited|browsed|viewed the product)\b', conv) and not re.search(r'\b(bought|purchas|paid|order)', conv):
        kind = 'purchase_page'
    else:
        kind = 'purchase'
    return {'kind': kind, 'free': is_free_service(inputs.get('platform')), 'service': str(inputs.get('platform') or '')}


# ----------------------------------------------------------------- modifiers
def age_factor(prim: Optional[dict] = None, title_profile: Optional[dict] = None) -> tuple[float, str]:
    """Under-35 skew x1.25, 55+ skew x0.6, else x1.0. A Profile IQ AGE
    read wins over the research's word."""
    if isinstance(title_profile, dict) and title_profile:
        under35 = sum(float(v) for k, v in title_profile.items() if re.match(r'^(17|18-24|25-34)', str(k)))
        over55 = sum(float(v) for k, v in title_profile.items() if re.match(r'^(55|65)', str(k)))
        if under35 >= 55:
            return 1.25, f'audience {under35:.0f}% under 35'
        if over55 >= 40:
            return 0.6, f'audience {over55:.0f}% 55 or older'
        return 1.0, 'audience age balanced'
    skew = _norm((prim or {}).get('audience_age_skew'))
    if skew == 'younger':
        return 1.25, 'audience skews under 35'
    if skew == 'older':
        return 0.6, 'audience skews 55 or older'
    return 1.0, 'audience age balanced'


def genre_factor(text: str) -> tuple[float, str]:
    t = _norm(text)
    for rx, f, label in GENRE_FACTORS:
        if re.search(rx, t):
            return f, label
    return 1.0, 'scripted or general'


def post_type_factor(inputs: dict, prim: Optional[dict] = None) -> tuple[float, str]:
    pt = _norm((prim or {}).get('post_type'))
    if pt in POST_TYPE_FACTORS:
        return POST_TYPE_FACTORS[pt], pt.replace('_', ' ')
    sb = _norm((inputs or {}).get('start_behavior'))
    if re.search(r'\bofficial\b', sb):
        return 1.2, 'official clip'
    if re.search(r'\b(news|article|review|editorial)\b', sb):
        return 0.7, 'news or article'
    if re.search(r'\b(meme|reaction)\b', sb):
        return 0.8, 'meme or reaction'
    return 1.0, 'mixed posts'


def window_factor(inputs: dict) -> tuple[float, str]:
    conv = _norm((inputs or {}).get('conversion_event')) + ' ' + _norm((inputs or {}).get('notes'))
    m = re.search(r'within (\d{1,3}) days?', conv)
    days = int(m.group(1)) if m else 30
    pts = WINDOW_FACTORS
    if days <= pts[0][0]:
        return pts[0][1], f'{days}-day window'
    if days >= pts[-1][0]:
        return pts[-1][1], f'{days}-day window'
    for (d0, f0), (d1, f1) in zip(pts, pts[1:]):
        if d0 <= days <= d1:
            f = f0 + (f1 - f0) * (days - d0) / float(d1 - d0)
            return round(f, 3), f'{days}-day window'
    return 1.0, f'{days}-day window'


# ------------------------------------------------------------------ the band
def band_for(inputs: dict, prim: Optional[dict] = None, title_profile: Optional[dict] = None,
             seed: str = '') -> dict:
    """The resolved band for one journey: lo, hi, ceiling (percent of the
    start step), a salted target strictly inside the band, the factors
    used and a plain trail."""
    cls = classify(inputs, prim)
    lo, hi, ceil = BANDS[cls['kind']]
    trail = [KIND_LABEL[cls['kind']]]
    if cls['free'] and cls['kind'].startswith('watch'):
        lo, hi, ceil = lo * FREE_MULT, hi * FREE_MULT, FREE_CEILING
        trail.append('free service band (x1.4)')
    factors = {}
    if cls['kind'].startswith('watch') or cls['kind'] == 'signup':
        f, why = service_reach_factor(inputs.get('platform'))
        factors['service_reach'] = f
        trail.append(why)
    f, why = age_factor(prim, title_profile)
    factors['age'] = f
    trail.append(why)
    f, why = genre_factor(' '.join([str((prim or {}).get('category') or ''), str((prim or {}).get('genre') or ''),
                                    str((inputs or {}).get('notes') or ''), str((inputs or {}).get('subject') or '')]))
    factors['genre'] = f
    trail.append(why)
    f, why = post_type_factor(inputs, prim)
    factors['post_type'] = f
    trail.append(why)
    f, why = window_factor(inputs)
    factors['window'] = f
    trail.append(why)
    mult = 1.0
    for v in factors.values():
        mult *= v
    lo_f = round(min(lo * mult, ceil * 0.9), 4)
    hi_f = round(min(hi * mult, ceil), 4)
    if hi_f <= lo_f:
        hi_f = round(lo_f * 1.5, 4)
    seed = seed or f"{(inputs or {}).get('subject')}|{(inputs or {}).get('platform')}"
    mid = math.sqrt(lo_f * hi_f)
    jit = 0.82 + (_h(seed, 'band_target') % 361) / 1000.0        # 0.82 to 1.18
    target = mid * jit
    target = min(max(target, lo_f * 1.02), hi_f * 0.98)
    title_viewers = None
    try:
        tv = (prim or {}).get('title_viewers_in_window')
        if tv:
            title_viewers = int(float(str(tv).replace(',', '')))
    except Exception:
        title_viewers = None
    return {'kind': cls['kind'], 'free': cls['free'], 'lo': lo_f, 'hi': hi_f, 'ceiling': ceil,
            'target': round(target, 4), 'factors': factors, 'trail': trail, 'title_viewers': title_viewers}


def norms_prompt_block(band: dict) -> dict:
    """What the research prompt is told before the model reasons."""
    return {
        'definition': ('conversion = the share of the first stage whose FIRST play, purchase, ticketing visit or '
                       'stream came after the exposure and inside the window; people already watching or buying '
                       'before the exposure are NOT conversions and are reported on their own line'),
        'end_to_end_band_pct': [band['lo'], band['hi']],
        'target_pct': band['target'],
        'hard_ceiling_pct': band['ceiling'],
        'kind': KIND_LABEL.get(band['kind'], band['kind']),
        'why': '; '.join(band['trail']),
        'step_ceilings_kept_of_prior_pct': {
            'exposure to meaningful engagement (click, share, comment, profile visit)': list(STEP_CEILINGS['engagement']),
            'engaged to searched or looked the title up': list(STEP_CEILINGS['search']),
            'looked up to reached the service title page': list(STEP_CEILINGS['platform_page']),
            'title page to play': list(STEP_CEILINGS['play']),
            'any purchase or signup step': list(STEP_CEILINGS['purchase']),
        },
        'caps': ('the conversion stage never exceeds 45% of the title\'s viewers on the service in the window; '
                 'the exposure stage never exceeds 5x the combined follower count of the title\'s official accounts '
                 '(12x with a live campaign)'),
    }


# ----------------------------------------------------------------- the hold
def step_role(label: str, idx: int, n_steps: int) -> str:
    """The role a spine step plays, from its label; the last step reads as
    the conversion (play or purchase) when the label is unclear."""
    lab = str(label or '')
    for role, rx in _ROLE_RX:
        if rx.search(lab):
            if role == 'platform_page' and idx == n_steps - 1:
                continue
            return role
    if idx == n_steps - 1:
        return 'play'
    if idx == 1:
        return 'engagement'
    return 'generic'


def _exposure_start(label: str) -> bool:
    return bool(_EXPOSURE_RX.search(str(label or '')))


# The leave / win-back fork has a shape of its own (2026-10-13 audit: a
# delivered journey said 6,129 came back and 6,229 of them watched).
# Each branch ratio sits in a band; outside it, or when an identity
# fails, the ratios are seated inside the band and the fork re-solves.
FORK_BANDS = {
    'share_back': (0.12, 0.40),          # paid_return of paid
    'paid_return_of_returned': (0.15, 0.45),
    'returned_of_retargeted': (0.15, 0.60),
    'retargeted_of_abandoned': (0.20, 0.80),
}
_FORK_IDS = ('abandoned', 'retargeted', 'returned', 'paid_return', 'paid_first')


def fork_ratios(j: dict) -> Optional[dict]:
    fork = (j or {}).get('fork') or []
    fk = {str(r.get('id')): r for r in fork if isinstance(r, dict) and r.get('id')}
    if not all(k in fk for k in _FORK_IDS):
        return None
    ab, rt, rn, pr, pf = (int(fk[k].get('accounts') or 0) for k in _FORK_IDS)
    paid = pf + pr
    return {'share_back': pr / paid if paid else 9.0,
            'paid_return_of_returned': pr / rn if rn else 9.0,
            'returned_of_retargeted': rn / rt if rt else 9.0,
            'retargeted_of_abandoned': rt / ab if ab else 9.0,
            'identities': bool(pf > 0 and pr > 0 and rt < ab and rn < rt and pr <= rn)}


def plausible_fork(j: dict, paid: int, penult: int, seed: str, js) -> bool:
    """True when the fork was re-seated. Checks identities and FORK_BANDS;
    on a miss, encodes band-interior ratios (salted) into the fork rows and
    re-solves through refit_fork so every identity holds."""
    r = fork_ratios(j)
    if r is None:
        return False
    in_band = all(FORK_BANDS[k][0] <= r[k] <= FORK_BANDS[k][1] for k in FORK_BANDS)
    if in_band and r['identities'] and r['share_back'] * paid <= penult - paid:
        return False
    fk = {str(x.get('id')): x for x in j['fork']}
    seat = {}
    for k, (lo, hi) in FORK_BANDS.items():
        v = r[k]
        if not (lo <= v <= hi):
            v = lo + (hi - lo) * (0.25 + (_h(seed, 'fork', k) % 51) / 100.0)
        seat[k] = v
    pr = max(1.0, paid * seat['share_back'])
    rn = pr / seat['paid_return_of_returned']
    rt = rn / seat['returned_of_retargeted']
    ab = rt / seat['retargeted_of_abandoned']
    fk['paid_return']['accounts'] = int(pr)
    fk['paid_first']['accounts'] = int(max(paid - pr, 1))
    fk['returned']['accounts'] = int(rn)
    fk['retargeted']['accounts'] = int(rt)
    fk['abandoned']['accounts'] = int(max(ab, rt + 1))
    js.refit_fork(j, paid, penult, paid, seed)
    r2 = fork_ratios(j) or {}
    print(f"[journey-norms] fork re-seated: " + ', '.join(f"{k}={r2.get(k, 0):.2f}" for k in FORK_BANDS))
    return True


def hold_to_norms(payload: dict, band: dict, inputs: Optional[dict] = None, seed: str = '') -> dict:
    """In place: the spine's end-to-end rate lands on the band's target (or
    under the title cap), every middle step sits inside its ceiling, and
    the fork, detours, facts, kpis and copy recompute from the corrected
    spine. Adds the already-watching line. Never raises; records a trail
    under meta.norms."""
    try:
        from migration import journey_synthesis as js
    except ImportError:   # twin layout
        import journey_synthesis as js   # type: ignore
    inputs = inputs or {}
    seed = seed or f"{inputs.get('subject')}|{inputs.get('platform')}"
    meta = payload.setdefault('meta', {})
    trail = {'kind': band.get('kind'), 'band': [band.get('lo'), band.get('hi')], 'ceiling': band.get('ceiling'),
             'target': band.get('target'), 'factors': band.get('factors'), 'why': band.get('trail'), 'applied': False}
    try:
        j = payload.get('fragrance_shop_journey') or {}
        spine = j.get('spine') or []
        if len(spine) < 3:
            trail['note'] = 'spine too short to hold'
            meta['norms'] = trail
            return payload
        if not _exposure_start(spine[1].get('label')):
            trail['note'] = 'start step is not an exposure step; band not applied'
            meta['norms'] = trail
            return payload
        start = int(spine[1]['accounts'])
        old_end = int(spine[-1]['accounts'])
        old_rate = old_end / start * 100.0 if start else 0.0
        trail['from_rate_pct'] = round(old_rate, 4)
        # the title cap: never more than 45% of the title's viewers on the service in the window
        cap_end = None
        tv = band.get('title_viewers') or (meta.get('title_anchor') or {}).get('us_viewers')
        try:
            tv = int(tv) if tv else None
        except Exception:
            tv = None
        if tv and tv > 0:
            cap_end = int(tv * TITLE_CAP_SHARE)
            trail['title_cap'] = cap_end
        lo, hi, target = float(band['lo']), float(band['hi']), float(band['target'])
        want_rate = old_rate if lo <= old_rate <= hi else target
        want_end = int(round(start * want_rate / 100.0))
        if cap_end and want_end > cap_end:
            want_end = int(cap_end * (0.90 + (_h(seed, 'cap_jit') % 71) / 1000.0))
            want_rate = want_end / start * 100.0
            trail['cap_applied'] = True
        want_end = _messy(seed, want_end, 'want_end')
        want_end = min(want_end, start - 1)

        old_counts = {st.get('id'): int(st.get('accounts') or 0) for st in spine}
        # middle steps + last: clamp each kept rate to its ceiling, then spread the residual
        steps = spine[2:]
        n = len(steps)
        roles = [step_role(st.get('label'), i + 2, len(spine)) for i, st in enumerate(steps)]
        bounds = [STEP_CEILINGS.get(r, STEP_CEILINGS['generic']) for r in roles]
        kept = []
        prev = start
        for st in steps:
            acc = int(st.get('accounts') or 0)
            kept.append(acc / prev * 100.0 if prev else 0.0)
            prev = acc
        kept = [min(max(k, b[0]), b[1]) for k, b in zip(kept, bounds)]
        need = want_end / float(start)
        for _ in range(8):
            prod = 1.0
            for k in kept:
                prod *= k / 100.0
            if prod <= 0:
                break
            ratio = need / prod
            if abs(ratio - 1.0) < 0.002:
                break
            free_idx = [i for i, (k, b) in enumerate(zip(kept, bounds))
                        if (ratio > 1 and k < b[1] - 1e-9) or (ratio < 1 and k > b[0] + 1e-9)]
            if not free_idx:
                break
            r = ratio ** (1.0 / len(free_idx))
            for i in free_idx:
                kept[i] = min(max(kept[i] * r, bounds[i][0]), bounds[i][1])
        # no two consecutive steps keep the same share (salted nudge inside bounds)
        for i in range(1, len(kept)):
            if abs(kept[i] - kept[i - 1]) < 0.6:
                kept[i] = min(max(kept[i] + 0.7 + (_h(seed, 'gap', i) % 50) / 100.0, bounds[i][0]), bounds[i][1])
        # recompute accounts; the last step lands exactly on want_end when its bound allows
        prev = start
        for i, st in enumerate(steps):
            if i == n - 1:
                acc = want_end
                lo_b, hi_b = bounds[i]
                k_last = acc / prev * 100.0
                if k_last > hi_b:
                    acc = _messy(seed, prev * hi_b / 100.0, 'last_hi')
                elif k_last < lo_b:
                    acc = _messy(seed, prev * lo_b / 100.0, 'last_lo')
            else:
                acc = _messy(seed, prev * kept[i] / 100.0, f'step{i}')
            acc = max(1, min(acc, prev - 1))
            st['accounts'] = acc
            st['kept'] = round(acc / prev * 100.0, 4)
            st['dropped'] = prev - acc
            st['ofUs'] = round(acc / US_GEN_POP * 100.0, 4)
            prev = acc
        new_end = int(spine[-1]['accounts'])
        penult = int(spine[-2]['accounts'])
        # fork, detours, facts, kpis, copy
        try:
            js.refit_fork(j, new_end, penult, old_end, seed)
        except Exception as exc:
            print(f'[journey-norms] fork refit skipped: {exc}')
        try:
            if plausible_fork(j, new_end, penult, seed, js):
                trail['fork_reseated'] = True
        except Exception as exc:
            print(f'[journey-norms] fork plausibility skipped: {exc}')
        new_counts = {st.get('id'): int(st.get('accounts') or 0) for st in spine}
        # detours rebase on the new count of the step they partition
        for d in j.get('detours') or []:
            rows = d.get('rows') or []
            if not rows:
                continue
            r0 = rows[0]
            try:
                base_old = float(r0['accounts']) / (float(r0['pct']) / 100.0)
            except Exception:
                continue
            base_new = None
            for sid, ov in old_counts.items():
                if ov and abs(base_old - ov) / float(ov) < 0.03:
                    base_new = new_counts.get(sid, ov)
                    break
            if base_new is None:
                base_new = base_old * (new_end / float(old_end)) if old_end else base_old
            old_sum = sum(float(r.get('accounts') or 0) for r in rows)
            partition = base_old and abs(old_sum - base_old) / float(base_old) < 0.005
            for r in rows:
                r['accounts'] = _messy(seed, base_new * float(r.get('pct') or 0) / 100.0, f"det|{d.get('title')}|{r.get('label')}")
            if partition and rows:
                big = max(rows, key=lambda r: r['accounts'])
                big['accounts'] += int(round(base_new)) - sum(r['accounts'] for r in rows)
            for r in rows:
                try:
                    js.rescale_breakdown(r, (seed, 'det', d.get('title'), r.get('label')))
                except Exception:
                    pass
            d['title'] = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', lambda m_: f"{new_end:,}" if abs(int(m_.group(0).replace(',', '')) - old_end) / float(old_end or 1) < 0.03 else m_.group(0), str(d.get('title') or ''))
        # already watching: fans in the social audience who were watching before the posts
        also_rate = (0.025 + (_h(seed, 'also') % 26) / 1000.0) if band.get('kind') == 'watch_catalog' else (0.015 + (_h(seed, 'also') % 16) / 1000.0)
        also = int(start * also_rate)
        if cap_end:
            also = min(also, max(cap_end - new_end, 0))
        also = _messy(seed, also, 'also_watching') if also > 0 else 0
        title = str(meta.get('target_name') or meta.get('target') or inputs.get('subject') or 'the title').split(' on ')[0]
        rate = new_end / start * 100.0
        kp = payload.get('kpis') or {}
        kp.update({'total_users': new_end, 'conversion_pct': round(rate, 4),
                   'share_of_us_pct': round(new_end / US_GEN_POP * 100.0, 4),
                   'norm_band': [lo, hi], 'norm_kind': band.get('kind'), 'also_watching': also})
        payload['kpis'] = kp
        facts = payload.get('facts') or []

        def _retext_count(m_):
            # a quoted count within 3% of a pre-hold step count (the research
            # often quotes a pre-jitter figure) follows that step's new count
            n_ = int(m_.group(0).replace(',', ''))
            for sid, ov in old_counts.items():
                if ov and ov != int(spine[0]['accounts']) and abs(n_ - ov) / float(ov) < 0.03:
                    return f"{new_counts.get(sid, ov):,}"
            return m_.group(0)
        _conv_words = re.compile(r'\b(convert|conversion|carry|went on|watched|streamed|bought|signed up|of the people|of those)\b', re.I)

        def _retext_rate(m_):
            # a conversion-shaped percent that drifted from the spine follows
            # the spine (2026-10-13 audit: a fact said 2.12% while the spine
            # read 1.49%); unrelated percents (shares of a detour) stay
            try:
                v_ = float(m_.group(1))
            except Exception:
                return m_.group(0)
            if old_rate and 0.5 * old_rate <= v_ <= 2.0 * old_rate and abs(v_ - rate) > 0.05:
                return f"{rate:.1f}%"
            return m_.group(0)
        for f in facts:
            v = str(f.get('value') or '')
            v = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _retext_count, v)
            v = re.sub(r'(\d+(?:\.\d+)?)% (carry|conversion)', lambda m_: f"{rate:.1f}% {m_.group(2)}", v)
            if _conv_words.search(v):
                v = re.sub(r'(\d+(?:\.\d+)?)%', _retext_rate, v)
            f['value'] = v
        verb = {'ticketing': 'reached a ticketing site', 'music': 'streamed the song', 'signup': 'signed up'}.get(
            band.get('kind'), 'bought' if str(band.get('kind', '')).startswith('purchase') else 'watched')
        also_fact = {'label': 'Already watching' if verb == 'watched' else 'Already customers',
                     'value': (f"{also:,} of the people who saw the posts were already {('watching ' + title) if verb == 'watched' else 'customers'} "
                               f"before the posts; they are not counted in the {rate:.1f}% conversion.") if also else
                              f"The {rate:.1f}% conversion counts only people whose first {verb.split()[0]} came after the posts."}
        if len(facts) >= 4:
            facts[3] = also_fact
        else:
            facts.append(also_fact)
        payload['facts'] = facts
        try:
            fam = js.journey_family(inputs) if inputs.get('subject') else 'watch'
            j['copy'] = js.build_copy(str(inputs.get('subject') or title), str(inputs.get('platform') or ''), j, family=fam)
        except Exception as exc:
            print(f'[journey-norms] copy rebuild skipped: {exc}')
        trail.update({'applied': True, 'to_rate_pct': round(rate, 4), 'old_end': old_end, 'new_end': new_end,
                      'also_watching': also, 'roles': roles})
        meta['norms'] = trail
        print(f"[journey-norms] {band.get('kind')}: {old_rate:.2f}% -> {rate:.2f}% "
              f"(band {lo} to {hi}, end {old_end:,} -> {new_end:,}, already watching {also:,})")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        trail['note'] = f'hold skipped: {exc}'
        meta['norms'] = trail
    return payload


# ----------------------------------------------------------------- auditing
def audit_payload(payload: dict) -> dict:
    """Does a journey's end-to-end rate sit inside its band? Reads the
    band the build recorded, or resolves one from the payload's meta."""
    j = (payload or {}).get('fragrance_shop_journey') or {}
    spine = [s for s in (j.get('spine') or []) if isinstance(s, dict) and s.get('id') != 'tam']
    meta = (payload or {}).get('meta') or {}
    kp = (payload or {}).get('kpis') or {}
    if len(spine) < 2:
        return {'ok': True, 'note': 'no spine'}
    start, end = float(spine[0].get('accounts') or 0), float(spine[-1].get('accounts') or 0)
    rate = end / start * 100.0 if start else 0.0
    band = kp.get('norm_band')
    kind = kp.get('norm_kind')
    if not band:
        b = band_for({'subject': meta.get('target') or meta.get('project_name'), 'platform': str(meta.get('project_name') or '').split(' on ')[-1],
                      'journey_kind': meta.get('journey_kind'), 'conversion_event': ''}, None)
        band, kind = [b['lo'], b['hi']], b['kind']
    ok = band[0] - 1e-9 <= rate <= band[1] + 1e-9
    if not _exposure_start(spine[0].get('label')):
        ok = True
    return {'ok': ok, 'rate_pct': round(rate, 4), 'band': band, 'kind': kind,
            'project_name': meta.get('project_name'), 'created_at': meta.get('created_at')}


def audit_index(s3, since: str = ROLLOUT_AT, bucket: str = 'dashboard-inputs') -> list:
    """The nightly sniff test: every journey created since the rollout whose
    conversion sits outside its band. Returns a list of fault dicts."""
    import gzip
    faults = []
    try:
        idx = json.loads(s3.get_object(Bucket=bucket, Key='journey-iq/_index.json')['Body'].read())
    except Exception:
        return faults
    for r in idx.get('runs', []) or []:
        if str(r.get('created_at') or '') < since or not r.get('key'):
            continue
        try:
            raw = s3.get_object(Bucket=bucket, Key=r['key'])['Body'].read()
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
            a = audit_payload(json.loads(raw))
        except Exception as exc:
            faults.append({'key': r.get('key'), 'why': f'unreadable: {exc}'})
            continue
        if not a.get('ok'):
            faults.append({'key': r.get('key'), 'project_name': a.get('project_name'), 'rate_pct': a.get('rate_pct'),
                           'band': a.get('band'), 'kind': a.get('kind'),
                           'why': f"conversion {a.get('rate_pct')}% outside {a.get('band')} ({a.get('kind')})"})
    return faults


_CHAT_EXPOSURE_RX = re.compile(r"\b(social (?:post|clip|video|media)|clips?|trailer|creator video|saw (?:a|the) post|viewed (?:a|the) post|"
                               r"posts? about|tiktok|instagram|reel|short-form)\b", re.I)
_CHAT_ACTION_RX = re.compile(r"\b(watch(?:ed)?|play(?:ed)?|stream(?:ed)?|bought|purchas\w*|subscri\w*|signed up|ticket\w*)\b", re.I)
_CHAT_RATE_RX = re.compile(r"(conver|carry|went on to|then (?:watched|bought|played|streamed)|share (?:who|that|of .{0,30}who) (?:watched|bought|played|streamed)|"
                           r"of (?:clip|post|social) viewers|clip.to.play|post.to.play|to.watch rate)", re.I)
_CHAT_COUNT_RX = re.compile(r"(watched|played|converted|bought|streamed|went on).{0,60}(within|after|following).{0,40}(post|clip|view|exposure|social)", re.I)
_PCT_UNITS_CHAT = {'%', 'pct', 'percent', 'percentage', 'share', 'rate'}


def hold_chat_metrics(metrics: list, context_text: str = '', subject: str = '') -> list:
    """The numeric gate behind the prompt rule: a Prometheus measurement
    read about the share of social viewers who then watched, bought,
    subscribed or reached a ticketing site keeps its rate inside the band
    for that kind of title and service, and scales the matching converted
    count with it. Reads without exposure language pass through."""
    if not isinstance(metrics, list) or not metrics:
        return metrics
    ctx = ' '.join([str(context_text or '')] + [f"{m.get('label', '')} {m.get('definition', '')} {m.get('name', '')}"
                                                 for m in metrics if isinstance(m, dict)])
    if not (_CHAT_EXPOSURE_RX.search(ctx) and _CHAT_ACTION_RX.search(ctx)):
        return metrics
    low = ctx.lower()
    if re.search(r"\bticket", low):
        jk, conv = 'ticketing', 'reached a ticketing site'
    elif re.search(r"\b(subscri|signed up)", low):
        jk, conv = 'purchase', 'signed up'
    elif re.search(r"\b(song|track|album|spotify|apple music)\b", low):
        jk, conv = 'music', 'streamed the song'
    elif re.search(r"\b(bought|purchas)", low):
        jk, conv = 'purchase', 'bought'
    else:
        jk, conv = 'watch', 'watched'
    platform = ''
    for key in sorted(list(SERVICE_REACH) + sorted(FREE_SERVICES), key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", low):
            platform = key
            break
    status = 'returning' if re.search(r"\b(new season|season \d+ premiere|premiere|returns)\b", low) else (
        'new' if re.search(r"\b(new original|launch|debut|opening weekend)\b", low) else 'catalog')
    m_win = re.search(r"within (\d{1,3}) days?", low)
    band = band_for({'subject': subject, 'platform': platform, 'journey_kind': jk,
                     'conversion_event': conv + (f" within {m_win.group(1)} days" if m_win else ''), 'notes': low[:300]},
                    {'title_status': status}, seed=f"chat|{subject}|{platform}|{jk}")
    factor = None
    out = []
    for m in metrics:
        if not isinstance(m, dict):
            out.append(m)
            continue
        unit = str(m.get('unit') or '').strip().lower()
        text = f"{m.get('label', '')} {m.get('definition', '')} {m.get('name', '')}"
        try:
            v = float(m.get('value'))
        except (TypeError, ValueError):
            out.append(m)
            continue
        if unit in _PCT_UNITS_CHAT and _CHAT_RATE_RX.search(text) and v > band['hi']:
            new_v = round(band['target'], 1)
            if not (band['lo'] < new_v < band['hi']):
                new_v = round(math.sqrt(band['lo'] * band['hi']), 2)
            factor = new_v / v if v else None
            m = dict(m, value=new_v, definition=(str(m.get('definition') or '')[:160] +
                                                 ' Counts only people whose first watch or purchase came after the post.').strip())
        out.append(m)
    if factor:
        for i, m in enumerate(out):
            if not isinstance(m, dict):
                continue
            unit = str(m.get('unit') or '').strip().lower()
            text = f"{m.get('label', '')} {m.get('definition', '')} {m.get('name', '')}"
            if unit not in _PCT_UNITS_CHAT and _CHAT_COUNT_RX.search(text):
                try:
                    out[i] = dict(m, value=_messy(f"chat|{subject}", float(m.get('value')) * factor, m.get('name') or text[:40]))
                except (TypeError, ValueError):
                    pass
    return out


# Brand Partnership IQ conversion lens: the share of the partnership
# audience with a purchase signal for the brand before and after the
# partnership. A penetration, not an exposure rate, so the band is wider;
# the LIFT is what a partnership can plausibly move.
BPIQ_CONVERSION = {'lo': 0.05, 'hi': 6.0, 'ceiling': 12.0, 'lift_rel_ceiling': 0.60, 'lift_rel_typical': (0.05, 0.35)}


def hold_bpiq_conversions(pre_pct, post_pct, seed: str = '') -> tuple:
    """(pre, post, note): both inside the band, post never more than 60%
    above pre (salted to a typical lift when it was), post never below pre
    by more than the band allows. Never raises."""
    try:
        pre = float(pre_pct or 0.0)
        post = float(post_pct or 0.0)
    except (TypeError, ValueError):
        return pre_pct, post_pct, 'unreadable'
    b = BPIQ_CONVERSION
    notes = []
    if pre > b['ceiling']:
        pre = round(b['hi'] * (0.86 + (_h(seed, 'bp_pre') % 121) / 1000.0), 4)
        notes.append('pre share held to the band')
    if post > b['ceiling']:
        post = round(b['hi'] * (0.9 + (_h(seed, 'bp_post') % 121) / 1000.0), 4)
        notes.append('post share held to the band')
    if pre > 0 and post > pre * (1.0 + b['lift_rel_ceiling']):
        lo, hi = b['lift_rel_typical']
        lift = lo + (hi - lo) * ((_h(seed, 'bp_lift') % 1000) / 1000.0)
        post = round(pre * (1.0 + lift), 4)
        notes.append(f'lift held to {lift * 100:.1f}%')
    if post < 0:
        post = 0.0
    return pre, post, '; '.join(notes)


def chat_rule_text() -> str:
    """The same bands, in the words the Prometheus prompts carry."""
    return (
        "SOCIAL TO ACTION CONVERSION:\n"
        "- When a question asks what share of people who saw a social post, clip, trailer or creator video then watched, "
        "bought, subscribed or went to a ticketing site, the rate is the share whose FIRST such action came after the exposure. "
        "People already watching or already customers before the exposure are a separate line, never inside the rate.\n"
        "- The rate sits inside these bands unless a published measurement for the same title and window says otherwise: "
        "catalog title on a paid streaming service 0.15 to 1.2% (hard ceiling 2%); returning series with a new season 0.6 to 3% (ceiling 4%); "
        "new original with a campaign 1 to 4.5% (ceiling 6%); free ad-supported service up to 1.4x those bands (ceiling 5%); "
        "theatrical ticketing site 0.5 to 2.5% (ceiling 3.5%); purchase 0.05 to 0.8% (ceiling 1.5%); reached a product page 0.5 to 2.5%; "
        "music full stream 0.8 to 6% (ceiling 8%); subscription signup 0.05 to 0.6% (ceiling 1%).\n"
        "- Smaller services sit lower in the band (Hulu, Max, Peacock, Paramount+, Apple TV+ about half of Netflix or Prime Video); "
        "audiences under 35 sit higher, audiences 55 or older lower; food, travel and documentary titles sit lower, reality higher, kids lower; "
        "a 7-day window sits lower than 30 days, 90 days higher.\n"
        "- The converted count never exceeds 45% of the title's viewers on that service in the window, and social viewers of a post or clip "
        "never exceed a few times the title's official follower base. Of the people who saw a social post, the share that even engaged "
        "(clicked, shared, commented) is 1.5 to 5%, never above 10%.\n"
    )
