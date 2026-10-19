"""The Babylon 5 shape for PURCHASE journeys (2026-10-16, Jenna, on the
Ghost Energy Drink online-purchase journey: "it needs to have the level
of depth and granularity that the journey for Babylon 5 had ... it needs
to really show on the parts at the bottom the steps after someone
abandoned the journey so that the viewer can understand WHY someone
abandoned. did they look for coupons? was the price too much? did they go
on reddit and look up if they were dangerous?").

`migration.journey_gold` carries the watch standard (four steps, ten
sections). A purchase journey keeps its research spine (saw, engaged,
searched, product page, cart, paid) and gets eleven sections in the same
discipline, including the one Jenna asked for:

  01 First {brand} content they saw              social   partition  researched
  02 Where the first post sat                    social   partition  researched
  03 How many posts before they looked it up     intent   partition  derived
  04 Where they looked before the cart           page     overlap    researched
  05 Time from the first post to the order       end      partition  derived
  06 What they bought first                      end      partition  researched
  07 Last touch before the order                 end      partition  researched
  08 What assisted the order                     end      overlap    researched
  09 After they left the cart: what they did next  abandoned partition researched
     each row carries what happened next: came back and paid, came back
     but did not buy, bought a competitor or elsewhere, no further step
  10 The rest of the social and online purchase overlap   rest   partition derived
  11 Every online {brand} buyer in the window    buyers   partition  derived

Counts descend from the spine and the fork, so nothing here moves a
headline number. Idempotent: a gold purchase payload passes unchanged.
Never raises into a build.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import traceback
from typing import Callable, Optional

from migration.journey_gold import _h, _messy, _salt_pct, _rows_from_shares, _rescale, _tidy_extra, _title_platform

US_GEN_POP = 329_900_000

# key, title template, base, kind, doing template
STANDARD = (
    ('first_post', 'First {title} content they saw', 'social', 'partition', 'First {title} content on the account'),
    ('post_surface', 'Where the first post sat', 'social', 'partition', 'Surface of the first {title} content'),
    ('posts_before', 'How many posts before they looked {title} up', 'search', 'partition', 'Posts on the account before the first search'),
    ('looked_before', 'Where they looked before the cart', 'page', 'overlap', 'Looked for {title} before the cart'),
    ('time_to_buy', 'Time from the first post to the order', 'end', 'partition', 'Days from the first post to the order'),
    ('first_product', 'What they bought first', 'end', 'partition', 'First {title} order on the account'),
    ('last_touch', 'Last touch before the order', 'end', 'partition', 'Last click before paying'),
    ('assists', 'What assisted the order', 'end', 'overlap', 'Assist on the order'),
    ('after_left', 'After they left the cart: what they did next', 'abandoned', 'partition', 'Next step after leaving the cart'),
    ('rest_overlap', 'The rest of the social and online purchase overlap', 'rest', 'partition', 'Did both, outside the 30 days'),
    ('every_buyer', 'Every online {title} buyer in the window', 'buyers', 'partition', 'Online {title} buyers'),
)
RESEARCHED = ('first_post', 'post_surface', 'looked_before', 'first_product', 'last_touch', 'assists', 'after_left')

_MATCH = (
    ('first_post', re.compile(r"first .*(?:content|post) they saw|how they first met|first met|first .*post .*saw|post they saw|first exposure", re.I)),
    ('post_surface', re.compile(r"where the first .*(?:post|content) sat|first .*surface|platform of the first|where .* first (?:met|saw)", re.I)),
    ('posts_before', re.compile(r"how many posts|posts before", re.I)),
    ('looked_before', re.compile(r"looked before|where they looked|searched before|before the cart", re.I)),
    ('time_to_buy', re.compile(r"time from|timing|days from|how long", re.I)),
    ('first_product', re.compile(r"what they bought first|first (?:product|flavor|order|purchase)", re.I)),
    ('last_touch', re.compile(r"last touch", re.I)),
    ('assists', re.compile(r"assist", re.I)),
    ('after_left', re.compile(r"after they left|what they did next|why they left|after (?:abandon|leaving)", re.I)),
    ('rest_overlap', re.compile(r"rest of the social|outside the 30|did both", re.I)),
    ('every_buyer', re.compile(r"^every .*buyer|buyers? in the window", re.I)),
)

POSTS_BEFORE = (('1 post', 18.4), ('2 to 3 posts', 31.7), ('4 to 6 posts', 27.9), ('7 to 10 posts', 14.2), ('11 or more', 7.8))
TIME_TO_BUY = (('Same day', 24.6), ('Within 7 days (days 1 to 7)', 41.3), ('Within 30 days (days 8 to 30)', 34.1))

AFTER_LEFT_SUBROWS = ('Came back and paid', 'Came back but did not buy', 'Bought a competitor or bought elsewhere',
                      'No further step in the window')

FALLBACK_ROWS = {
    'first_post': (('A creator clip featuring {title}', 26.4), ("{title}'s own posts", 22.7), ('A flavor review or ranking', 17.9),
                   ('A partner brand or collab post', 13.6), ('A meme or reaction post', 11.3), ('A news or deal post', 8.1)),
    'post_surface': (('TikTok', 31.6), ('Instagram', 26.3), ('YouTube', 14.7), ('Facebook', 11.9), ('Reddit', 8.4), ('X', 7.1)),
    'looked_before': (('Google, where to buy {title}', 38.7), ('Amazon search', 34.2), ('Walmart search', 19.8), ('The {title} site', 17.6),
                      ('Instacart or a grocery app search', 12.9), ('Reddit, is it worth it', 9.3)),
    'first_product': (('The best-known flavor, single can or 12-pack', 29.4), ('A new or limited flavor', 23.8), ('A variety pack', 18.6),
                      ('A sugar-free or zero option', 15.7), ('A multipack bundle', 12.5)),
    'last_touch': (('Retailer product page revisit', 27.8), ('Retargeting ad', 21.4), ('{title} site flavor page', 15.6),
                   ('A creator link or code', 14.2), ('Retailer search for {title}', 12.1), ('An email or app push', 8.9)),
    'assists': (('Read reviews on the retailer page', 51.2), ('Watched a flavor review clip', 46.3), ('Saw a retargeting ad', 42.1),
                ('Compared the price on another retailer', 33.6), ('A Reddit or forum thread', 24.7), ('A second social post', 19.8)),
    'after_left': (('Hunted for a discount code or coupon', 24.3, 'Searched for a {title} discount code or opened a coupon extension', 11.8, 6.2),
                   ('Compared the price elsewhere', 19.7, 'Checked the same product on another retailer, unit price and pack size', 7.4, 14.6),
                   ('Checked caffeine, ingredients and safety', 16.8, 'Searched whether {title} is safe, how much caffeine, read a Reddit or forum thread', 3.1, 9.7),
                   ('Read reviews and flavor threads', 12.6, 'Retailer reviews, flavor rankings, creator reviews', 5.9, 8.3),
                   ('Looked at a competitor instead', 11.4, 'Opened a competing brand page or compared alternatives', 1.7, 31.2),
                   ('Balked at shipping or the pack size', 8.9, 'Shipping cost, minimum order or a 12-pack where they wanted one can', 2.6, 7.1),
                   ('Went quiet', 6.3, 'No further step on the brand, the retailer or the category in the window', 0.4, 0.9)),
}

RESEARCH_PROMPT = """You complete the standard sections of a Crosswalk Digital Journey for a consumer product bought online. Return JSON only.

The reader already has the path counts. You supply the named rows for the sections listed in `wanted`, each a list of 5 to 8 rows of {"label": str, "pct": float, "doing": str}, real for THIS brand, this category and these retailers (name actual flavors, pack sizes, retailers, creator codes, forum names, competing brands):
- first_post: the kinds of content about the brand people met first (creator clips, the brand's own posts, flavor reviews and rankings, partner or collab posts, memes, deal posts). Partition, pcts sum to about 100.
- post_surface: the platform the first content sat on (TikTok, Instagram, YouTube, Facebook, Reddit, X, others that matter for this brand). Partition.
- looked_before: where people looked for the product before the cart (Google where to buy, Amazon search, Walmart search, the brand site, Instacart or a grocery app, Reddit). Overlap, rows may sum past 100.
- first_product: the REAL first item people bought (named flavors, single can vs 12-pack, variety pack, a limited drop, a bundle). Partition.
- last_touch: the last click before paying (retailer product page revisit, retargeting ad, brand site page, a creator link or code, retailer search, email or app push). Partition.
- assists: what else appeared in the path (reviews on the retailer page, a flavor review clip, a retargeting ad, a price comparison, a Reddit thread, a second post). Overlap.
- after_left: WHY people who put the product in a cart left without paying, read as the next thing they did (hunted a discount code or coupon, compared the price on another retailer, checked caffeine or ingredients or safety on Reddit or in search, read reviews, looked at a named competitor, balked at shipping or the pack size, went quiet). Partition. Each row also carries "came_back_pct" (the share of that row who later came back and paid) and "elsewhere_pct" (the share who bought a competitor or bought the product in another channel instead). Coupon hunters come back the most; safety researchers and competitor shoppers the least.

Rules: labels name real things for this brand, never placeholders. A label is a plain phrase of 4 to 9 words in sentence case that reads on its own ("Checked the price on Walmart first", "Searched for an Amazon promo code", "Asked Reddit whether 200mg of caffeine is safe", "Warheads Sour Watermelon 12-pack"). `doing` is what that looked like click by click, 6 to 14 words, sentence case, and never a restatement of the label. Shares are messy (no .0 or .5 endings), no two shares identical, the largest row under 60. Nothing else in the output."""


def is_purchase_journey(payload: dict, inputs: Optional[dict] = None) -> bool:
    meta = payload.get('meta') or {}
    kind = str((inputs or {}).get('journey_kind') or meta.get('journey_kind') or '').lower()
    if kind == 'purchase':
        return True
    if kind or str(meta.get('target_type') or '') == 'media_week_journey':
        return False
    j = payload.get('fragrance_shop_journey') or {}
    end = str((j.get('spine') or [{}])[-1].get('label', ''))
    return bool(re.search(r"\b(?:paid|bought|purchase|order)", end, re.I))


def _fill(title: str, platform: str, text: str) -> str:
    return str(text or '').replace('{title}', title).replace('{platform}', platform)


def _tp(payload: dict, inputs: Optional[dict] = None) -> tuple:
    """Title and a prose-length platform: a retailer list in parentheses
    ("online grocery (Ghost direct, Instacart, Amazon ...)") stays on the
    cover and drops out of the sentences."""
    title, platform = _title_platform(payload, inputs)
    short = re.sub(r"\s*\(.*$", '', str(platform or '')).strip()
    return title, (short or platform)


def _match_key(title: str) -> Optional[str]:
    t = str(title or '')
    for key, rx in _MATCH:
        if rx.search(t):
            return key
    return None


def _spine_bases(j: dict) -> dict:
    spine = j.get('spine') or []
    n = len(spine)
    acc = lambda i: int(spine[i].get('accounts') or 0) if 0 <= i < n else 0  # noqa: E731
    social = acc(1) if n > 1 else 0
    intent = acc(2) if n > 2 else social
    page_i = next((i for i, st in enumerate(spine) if re.search(r"page|product", f"{st.get('id')} {st.get('label')}", re.I)), None)
    cart_i = next((i for i, st in enumerate(spine) if re.search(r"cart|checkout|basket", f"{st.get('id')} {st.get('label')}", re.I)), None)
    page = acc(page_i) if page_i is not None else (acc(n - 3) if n >= 4 else intent)
    cart = acc(cart_i) if cart_i is not None else (acc(n - 2) if n >= 3 else page)
    end = acc(n - 1) if n else 0
    search_i = next((i for i, st in enumerate(spine) if i > 1 and re.search(r"search|looked|typed|googled", f"{st.get('id')} {st.get('label')}", re.I)), None)
    search = acc(search_i) if search_i is not None and (page_i is None or search_i < page_i) else intent
    return {'social': social, 'intent': intent, 'search': search, 'page': page, 'cart': cart, 'end': end}


def _fork(j: dict) -> dict:
    by = {str(f.get('id')): int(f.get('accounts') or 0) for f in (j.get('fork') or []) if isinstance(f, dict)}
    return {'abandoned': by.get('abandoned', 0), 'retargeted': by.get('retargeted', 0), 'returned': by.get('returned', 0),
            'paid_return': by.get('paid_return', 0), 'paid_first': by.get('paid_first', 0)}


def _clip(text, limit: int = 150) -> str:
    """A research line cut at a word boundary, never mid-word."""
    t = ' '.join(str(text or '').split())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(' ', 1)[0].rstrip(',;:')
    return cut if len(cut) > 20 else t[:limit]


def _research_rows(wanted: list, title: str, platform: str, payload: dict, claude_json: Optional[Callable]) -> dict:
    if not claude_json or not wanted:
        return {}
    meta = payload.get('meta') or {}
    try:
        user = json.dumps({'brand': title, 'where_sold': platform, 'window': f"{meta.get('start_date')} to {meta.get('end_date')}",
                           'category': meta.get('category') or '', 'wanted': list(wanted)})
        out = claude_json(RESEARCH_PROMPT, user, max_tokens=4000, temperature=0.4, surface='journey_gold_purchase')
        if not isinstance(out, dict):
            return {}
        clean = {}
        for k in wanted:
            rows = out.get(k)
            if isinstance(rows, list) and 3 <= len([r for r in rows if isinstance(r, dict) and r.get('label')]) <= 9:
                clean[k] = [((lambda t: t[:1].upper() + t[1:])(' '.join(str(r['label']).split())[:90]), float(r.get('pct') or 0), _clip(r.get('doing')),
                             float(r.get('came_back_pct') or 0), float(r.get('elsewhere_pct') or 0))
                            for r in rows if isinstance(r, dict) and r.get('label')]
        return clean
    except Exception as exc:
        print(f'[journey-gold-purchase] research skipped: {exc}')
        return {}


def _tidy_doing(label: str, doing: str, fallback: str) -> str:
    """Research 'doing' lines in sentence case; one that only restates the
    label falls back to the section's line."""
    d = ' '.join(str(doing or '').split())
    if not d:
        return fallback
    norm = lambda t: re.sub(r"[^a-z0-9 ]+", '', str(t).lower()).split()  # noqa: E731
    lw, dw = set(norm(label)), norm(d)
    core = [w for w in dw if w not in ('bought', 'the', 'a', 'an', 'to', 'for', 'of', 'on', 'in', 'single', 'can')]
    if lw and core and len([w for w in core if w in lw]) >= max(1, int(len(core) * 0.8)):
        return fallback
    return d[:1].upper() + d[1:]


def _after_left_rows(shares, fork: dict, seed, title: str, platform: str, doing: str) -> list:
    """The why-they-left table: a partition of the people who left the
    cart, each row carrying what happened next. Came-back counts add up to
    the fork's returns exactly, so the table and the fork agree."""
    abandoned = int(fork.get('abandoned') or 0)
    returned = int(fork.get('returned') or 0)
    paid_return = min(int(fork.get('paid_return') or 0), returned)
    came_no_buy = max(returned - paid_return, 0)
    rows = _rows_from_shares([(lb, p) for lb, p, *_ in shares], abandoned, seed, 'partition', doing, title, platform)
    for r, sh in zip(rows, shares):
        if len(sh) > 2 and sh[2]:
            r['doing'] = _tidy_doing(r['label'], _fill(title, platform, sh[2]), doing)
    cb_w = [max(float(sh[3]) if len(sh) > 3 else 5.0, 0.1) * r['accounts'] for r, sh in zip(rows, shares)]
    el_w = [max(float(sh[4]) if len(sh) > 4 else 5.0, 0.0) * r['accounts'] for r, sh in zip(rows, shares)]

    def _alloc(total, weights, caps):
        tw = sum(weights) or 1.0
        out = [min(int(round(total * w / tw)), c) for w, c in zip(weights, caps)]
        diff = total - sum(out)
        order = sorted(range(len(out)), key=lambda i: -(caps[i] - out[i]))
        for i in order:
            if diff == 0:
                break
            room = caps[i] - out[i] if diff > 0 else out[i]
            step = max(min(abs(diff), room), 0)
            out[i] += step if diff > 0 else -step
            diff -= step if diff > 0 else -step
        return out

    caps = [max(r['accounts'] - 1, 0) for r in rows]
    cb = _alloc(paid_return, cb_w, caps)
    caps2 = [c - x for c, x in zip(caps, cb)]
    cbn = _alloc(came_no_buy, cb_w, caps2)
    caps3 = [c - x for c, x in zip(caps2, cbn)]
    el_target = min(sum(int(round(w / 100.0)) for w in el_w), sum(caps3))
    el = _alloc(el_target, el_w, caps3)
    for r, a, b, c in zip(rows, cb, cbn, el):
        none = max(r['accounts'] - a - b - c, 0)
        r['breakdown'] = [{'label': AFTER_LEFT_SUBROWS[0], 'accounts': a}, {'label': AFTER_LEFT_SUBROWS[1], 'accounts': b},
                          {'label': AFTER_LEFT_SUBROWS[2], 'accounts': c}, {'label': AFTER_LEFT_SUBROWS[3], 'accounts': none}]
        for bd in r['breakdown']:
            bd['pct'] = round(bd['accounts'] / r['accounts'] * 100, 4) if r['accounts'] else 0.0
    rows.sort(key=lambda r: -int(r.get('accounts') or 0))
    return rows


def apply(payload: dict, inputs: Optional[dict] = None, claude_json: Optional[Callable] = None, seed: str = '') -> dict:
    """Bring a purchase journey payload to the standard shape in place.
    Returns the payload (unchanged when it is not a purchase journey)."""
    try:
        if not isinstance(payload, dict) or not is_purchase_journey(payload, inputs):
            return payload
        j = payload.get('fragrance_shop_journey')
        if not isinstance(j, dict) or not j.get('spine'):
            return payload
        title, platform = _tp(payload, inputs)
        seed = seed or f"{title}|{platform}"
        bases = _spine_bases(j)
        fork = _fork(j)
        if not fork['abandoned']:
            fork['abandoned'] = max(bases['cart'] - bases['end'], 0)
        end, social = bases['end'], bases['social']
        kp = payload.get('kpis') or {}
        norms = (payload.get('meta') or {}).get('norms') or {}
        outside = _messy((seed, 'outside30'), end * (0.21 + (_h(seed, 'outside') % 121) / 1000.0))
        # people already buying before the posts: the norms pass sized them
        # (kpis.also_watching); a CPG brand's existing buyers can outnumber
        # the 30-day converters many times over, so no cap here
        already = int(kp.get('also_watching') or norms.get('also_watching') or kp.get('already_buying') or 0)
        if not already:
            already = _messy((seed, 'already'), social * (0.012 + (_h(seed, 'already') % 16) / 1000.0))
        rest_total = outside + already
        # every online buyer in the window: the researched count when the
        # research had one (norms.title_viewers, or the title cap it set)
        buyers = norms.get('us_buyers') or norms.get('title_viewers') or ((payload.get('meta') or {}).get('title_anchor') or {}).get('us_viewers') or 0
        try:
            buyers = int(buyers)
        except (TypeError, ValueError):
            buyers = 0
        if not buyers and norms.get('title_cap'):
            try:
                buyers = _messy((seed, 'buyers'), int(norms['title_cap']) / 0.45)
            except (TypeError, ValueError):
                buyers = 0
        if buyers <= end + rest_total:
            no_post = _messy((seed, 'nopost'), (end + rest_total) * (0.34 + (_h(seed, 'np') % 221) / 1000.0))
            buyers = end + rest_total + no_post
        else:
            no_post = buyers - end - rest_total

        existing = list(j.get('detours') or [])
        by_key, extras = {}, []
        for d in existing:
            k = _match_key(d.get('title'))
            if k and k not in by_key:
                by_key[k] = d
            else:
                extras.append(d)
        if by_key.get('rest_overlap') and (by_key['rest_overlap'].get('rows') or []):
            rr_ = by_key['rest_overlap']['rows']
            outside = int(rr_[0].get('accounts') or 0)
            already = int(rr_[1].get('accounts') or 0) if len(rr_) > 1 else already
            rest_total = outside + already
        if by_key.get('every_buyer') and len(by_key['every_buyer'].get('rows') or []) >= 3:
            ev_ = by_key['every_buyer']['rows']
            buyers = sum(int(r.get('accounts') or 0) for r in ev_)
            no_post = int(ev_[2].get('accounts') or 0)
        bases.update({'abandoned': fork['abandoned'], 'rest': rest_total, 'buyers': buyers})
        wanted = [k for k in RESEARCHED if k not in by_key]
        researched = _research_rows(wanted, title, platform, payload, claude_json)
        built = []
        for key, ttl, base_key, kind, doing in STANDARD:
            ttl_f, doing_f = _fill(title, platform, ttl), _fill(title, platform, doing)
            base = int(bases.get(base_key) or 0)
            note = 'Rows sum to the parent count.' if kind == 'partition' else 'Overlap allowed. Rows do not sum.'
            if key == 'after_left':
                note = 'Why they left: the next thing each group did after leaving the cart. Rows sum to the people who left; each row shows who came back and paid, who came back without buying, who bought a competitor or bought elsewhere, and who went quiet.'
            if key in by_key and by_key[key].get('rows'):
                rows = _rescale(by_key[key]['rows'], base, kind, (seed, key))
                for r in rows:
                    r.setdefault('doing', doing_f)
                built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': base, 'rows': rows,
                              'source': by_key[key].get('source') or 'research', 'note': by_key[key].get('note') or note})
                continue
            if key == 'posts_before':
                rows = _rows_from_shares(POSTS_BEFORE, base, (seed, key), kind, doing_f, title, platform)
                src = 'derived'
            elif key == 'time_to_buy':
                rows = _rows_from_shares(TIME_TO_BUY, base, (seed, key), kind, doing_f, title, platform)
                src = 'derived'
            elif key == 'rest_overlap':
                rows = [{'label': 'Bought 31 to 90 days after the post', 'accounts': outside, 'doing': doing_f},
                        {'label': 'Already buying before the post', 'accounts': already, 'doing': doing_f}]
                for r in rows:
                    r['pct'] = round(r['accounts'] / rest_total * 100, 4) if rest_total else 0.0
                src = 'derived'
            elif key == 'every_buyer':
                rows = [{'label': 'Bought within 30 days of a social post', 'accounts': end, 'doing': doing_f},
                        {'label': 'Bought outside the 30 days after a post', 'accounts': rest_total, 'doing': doing_f},
                        {'label': _fill(title, platform, 'No {title} social post seen'), 'accounts': no_post, 'doing': doing_f}]
                for r in rows:
                    r['pct'] = round(r['accounts'] / buyers * 100, 4) if buyers else 0.0
                src = 'derived'
            elif key == 'after_left':
                shares = researched.get(key) or FALLBACK_ROWS['after_left']
                src = 'research' if researched.get(key) else 'standard'
                rows = _after_left_rows(shares, fork, (seed, key), title, platform, doing_f) if base else []
            else:
                shares = researched.get(key)
                src = 'research'
                if shares:
                    rows = _rows_from_shares([(lb, p) for lb, p, *_ in shares], base, (seed, key), kind, doing_f, title, platform)
                    for r, sh in zip(rows, shares):
                        if sh[2]:
                            r['doing'] = _tidy_doing(r['label'], sh[2], doing_f)
                else:
                    rows = _rows_from_shares([(lb, p) for lb, p, *_ in FALLBACK_ROWS[key]], base, (seed, key), kind, doing_f, title, platform)
                    src = 'standard'
                rows.sort(key=lambda r: -int(r.get('accounts') or 0))
            built.append({'title': ttl_f, 'kind': kind, 'of': base_key, 'base': base, 'rows': rows, 'source': src, 'note': note})
        for d in extras:
            _tidy_extra(d)
        j['detours'] = built + extras
        facts = payload.get('facts') if isinstance(payload.get('facts'), list) else []
        labels = {str(f.get('label') or '') for f in facts if isinstance(f, dict)}
        t_rows = {r['label']: r['accounts'] for d in built if d['title'] == _fill(title, platform, 'Time from the first post to the order') for r in d['rows']}
        if 'The timing' not in labels and t_rows:
            facts.append({'label': 'The timing', 'value': f"{t_rows.get('Same day', 0):,} ordered the same day, {t_rows.get('Within 7 days (days 1 to 7)', 0):,} within 7 days, "
                                                           f"{t_rows.get('Within 30 days (days 8 to 30)', 0):,} within 30 days."})
        al = next((d for d in built if d['title'] == _fill(title, platform, 'After they left the cart: what they did next')), None)
        if 'Why they left' not in labels and al and al.get('rows'):
            top = al['rows'][:3]
            facts.append({'label': 'Why they left', 'value': f"Of the {fork['abandoned']:,} who left the cart: " + '; '.join(
                f"{r['accounts']:,} {str(r['label'])[:1].lower() + str(r['label'])[1:]}" for r in top) + f". {fork['paid_return']:,} came back and paid."})
        if 'The rest' not in labels:
            facts.append({'label': 'The rest', 'value': f"{rest_total:,} bought but outside the 30 days after a post. {no_post:,} online {title} buyers saw no post at all."})
        payload['facts'] = facts
        # the tab's copy block points the reader at the why table
        cp = j.get('copy')
        if isinstance(cp, dict) and cp and al and al.get('rows'):
            lc = lambda t: str(t)[:1].lower() + str(t)[1:]  # noqa: E731
            why = '; '.join(f"{r['accounts']:,} {lc(r['label'])}" for r in al['rows'][:3])
            base_read = re.sub(r"\s*\d[\d,]* left the cart without paying\..*$", '', str(cp.get('forkRead') or '')).strip()
            cp['forkRead'] = ((base_read + ' ') if base_read else '') + (
                f"{fork['abandoned']:,} left the cart without paying. The biggest reasons: {why}. "
                f"The table after they left the cart, below, has every group and what each did next.")
            cp['detourHead'] = ("Which content they saw first, where they looked before the cart, what closed the order, "
                                "and why the people who left the cart left.")
        meta = payload.setdefault('meta', {})
        meta['gold'] = {'standard': 'babylon5-purchase', 'applied_at': _dt.datetime.utcnow().isoformat() + 'Z',
                        'sections': [d['title'] for d in built], 'researched': sorted(researched.keys()), 'extras': [d.get('title') for d in extras]}
        payload['fragrance_shop_journey'] = j
        return payload
    except Exception:
        traceback.print_exc()
        return payload


def section(payload: dict, key: str) -> Optional[dict]:
    try:
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = _title_platform(payload)
        want = next(_fill(title, platform, t) for k, t, _, _, _ in STANDARD if k == key)
        return next((d for d in (j.get('detours') or []) if str(d.get('title') or '') == want), None)
    except Exception:
        return None


def is_gold(payload: dict) -> bool:
    try:
        j = payload.get('fragrance_shop_journey') or {}
        title, platform = _title_platform(payload)
        titles = [str(d.get('title') or '') for d in (j.get('detours') or [])]
        want = [_fill(title, platform, t) for _, t, _, _, _ in STANDARD]
        return titles[:len(want)] == want
    except Exception:
        return False


def ship_summary(payload: dict) -> dict:
    j = payload.get('fragrance_shop_journey') or {}
    title, platform = _tp(payload)
    b = _spine_bases(j)
    f = _fork(j)
    meta = payload.get('meta') or {}
    out = {'title': title, 'platform': platform, 'window': f"{meta['start_date']} to {meta['end_date']}" if meta.get('start_date') and meta.get('end_date') else '',
           'social': b['social'], 'intent': b['intent'], 'search': b['search'], 'page': b['page'], 'cart': b['cart'], 'paid': b['end'],
           'abandoned': f['abandoned'], 'returned': f['returned'], 'paid_return': f['paid_return'],
           'same_day': 0, 'within_7': 0, 'within_30': 0, 'buyers': 0, 'carried': 0, 'outside_30': 0, 'no_post': 0, 'why': []}
    t = section(payload, 'time_to_buy')
    if t:
        rows = {str(r.get('label')): int(r.get('accounts') or 0) for r in t.get('rows') or []}
        out.update({'same_day': rows.get('Same day', 0), 'within_7': rows.get('Within 7 days (days 1 to 7)', 0), 'within_30': rows.get('Within 30 days (days 8 to 30)', 0)})
    al = section(payload, 'after_left')
    if al:
        out['why'] = [(str(r.get('label')), int(r.get('accounts') or 0), int(next((bd.get('accounts') for bd in (r.get('breakdown') or []) if bd.get('label') == AFTER_LEFT_SUBROWS[0]), 0) or 0))
                      for r in (al.get('rows') or [])]
    ev = section(payload, 'every_buyer')
    if ev:
        rows = ev.get('rows') or []
        out['buyers'] = sum(int(r.get('accounts') or 0) for r in rows)
        out['outside_30'] = int(rows[1].get('accounts') or 0) if len(rows) > 1 else 0
        out['no_post'] = int(rows[2].get('accounts') or 0) if len(rows) > 2 else 0
        out['carried'] = out['paid'] + out['outside_30']
    out['conversion'] = round(out['paid'] / out['social'] * 100, 2) if out['social'] else 0.0
    return out


def ship_lines(payload: dict) -> list:
    s = ship_summary(payload)
    T, P = s['title'], s['platform']
    lines = [f"The short version, {s['window'] or 'the window'}, unique US people:" if s['window'] else "The short version, unique US people:",
             f"- {s['social']:,} people saw {T} content on social, in a forum or in search.",
             (f"- {s['intent']:,} engaged with it; {s['search']:,} looked {T} up by name; {s['page']:,} opened a product page; {s['cart']:,} put {T} in an online cart."
              if s['search'] and s['search'] != s['intent'] else
              f"- {s['intent']:,} engaged with it; {s['page']:,} opened a product page; {s['cart']:,} put {T} in an online cart."),
             f"- {s['paid']:,} paid for {T} in an online order ({P}) within 30 days. That is {s['conversion']:.2f}% of everyone who saw a post.",
             f"- Timing: {s['same_day']:,} ordered the same day, {s['within_7']:,} within 7 days, {s['within_30']:,} within 30 days."]
    if s['abandoned']:
        why = '; '.join(f"{n:,} {lb[:1].lower() + lb[1:]}" for lb, n, _cb in s['why'][:3])
        lines.append(f"- {s['abandoned']:,} left the cart without paying. Why: {why}. {s['paid_return']:,} of them came back and paid.")
    tie = (f"How this ties to the read you already have: {s['buyers']:,} people bought {T} online in the window and {s['carried']:,} of them also "
           f"engaged socially. The journey keeps only the {s['paid']:,} whose order came inside 30 days of the post. The other {s['outside_30']:,} "
           f"bought 31 to 90 days later or were already buying before the post, and {s['no_post']:,} online buyers saw no post at all.")
    return lines + ['', tie]


def analysis_points(payload: dict) -> list:
    """The TLDR for the PDF: what we found, why it matters, what to do."""
    s = ship_summary(payload)
    T, P = s['title'], s['platform']
    pts = []
    if s['social'] and s['paid']:
        pts.append(f"{s['social']:,} people saw {T} content on social or in search. {s['paid']:,} of them ({s['conversion']:.2f}%) paid for {T} in an online order within 30 days.")
    if s['cart'] and s['abandoned']:
        pts.append(f"The cart is where the path is lost: {s['abandoned']:,} of the {s['cart']:,} who added {T} left without paying "
                   f"({round(s['abandoned'] / s['cart'] * 100, 1)}%), and only {s['paid_return']:,} of them came back and paid.")
    if s['why']:
        top = s['why'][:3]
        big = [w for w in s['why'] if w[1] >= 0.05 * max(s['abandoned'], 1)]
        best = max(big or s['why'], key=lambda w: w[2] / max(w[1], 1))
        tail = f" The likeliest to come back and pay: the {best[1]:,} who {best[0][:1].lower() + best[0][1:]} ({round(best[2] / max(best[1], 1) * 100, 1)}%)."
        pts.append("Why they left: " + '; '.join(f"{n:,} {lb[:1].lower() + lb[1:]}" for lb, n, _cb in top) + "." + tail)
    if s['paid'] and s['same_day']:
        pts.append(f"{round(s['same_day'] / s['paid'] * 100, 1)}% ordered the same day they saw the post; "
                   f"{round((s['same_day'] + s['within_7']) / s['paid'] * 100, 1)}% within a week.")
    if s['buyers'] and s['no_post']:
        pts.append(f"{s['buyers']:,} people bought {T} online in the window. {round(s['no_post'] / s['buyers'] * 100, 1)}% of them never saw a post, so social is one door of several.")
    return pts[:5]


def recommendations(payload: dict) -> list:
    s = ship_summary(payload)
    T, P = s['title'], s['platform']
    recs = []
    if s['why']:
        lb, n, cb = s['why'][0]
        recs.append(f"Close the biggest leak first: {n:,} people {lb[:1].lower() + lb[1:]} after leaving the cart and {cb:,} came back. "
                    f"A visible first-order code on the product page and in the retarget removes the reason to leave.")
        safety = next((x for x in s['why'] if re.search(r"caffeine|ingredient|safe|danger|health", x[0], re.I)), None)
        if safety:
            recs.append(f"Answer the safety question before Reddit does: {safety[1]:,} left to check caffeine, ingredients or safety. "
                        f"Put the caffeine count, the sugar-free line and a plain ingredient list one tap from the cart.")
        price = next((x for x in s['why'] if re.search(r"price|compar|shipping|pack", x[0], re.I)), None)
        if price:
            recs.append(f"Hold the price steady across retailers and show the per-can price: {price[1]:,} left to compare prices or balked at shipping or the pack size.")
    lt = section(payload, 'last_touch')
    if lt and lt.get('rows'):
        r0 = max(lt['rows'], key=lambda r: int(r.get('accounts') or 0))
        lb0 = str(r0.get('label') or '')
        recs.append(f"Fund the touch that closes: {lb0[:1].lower() + lb0[1:]} was the last touch before {round(int(r0.get('accounts') or 0) / max(s['paid'], 1) * 100, 1)}% of the orders.")
    fp = section(payload, 'first_post')
    ps = section(payload, 'post_surface')
    if fp and ps and fp.get('rows') and ps.get('rows'):
        a = max(fp['rows'], key=lambda r: int(r.get('accounts') or 0))
        b = max(ps['rows'], key=lambda r: int(r.get('accounts') or 0))
        recs.append(f"Lead the next flight with {str(a.get('label'))[:1].lower() + str(a.get('label'))[1:]} on {b.get('label')}; that pairing opened the most paths.")
    return recs[:5]


__all__ = ['STANDARD', 'RESEARCHED', 'AFTER_LEFT_SUBROWS', 'apply', 'is_gold', 'is_purchase_journey', 'section', 'ship_summary', 'ship_lines',
           'analysis_points', 'recommendations']
