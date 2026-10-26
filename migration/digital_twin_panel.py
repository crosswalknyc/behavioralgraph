"""Focus Group IQ (the panel engine): the focus group you moderate (2026-10-26, Jenna: "a panel
lab where you are the moderator and you create your digital twin group
and ask questions and are then given responses and %s from the digital
twin group. it's your focus group where you can ask and interact with
them in real time").

The user opens a room from one or more twins (or cohorts inside a
twin), seats a number of twins in it (12 by default) and asks anything
in plain words. Every answer lands in seconds: a distribution with %s
and people counts, the split by segment in the room, a one-line read,
and a few twin voices (first-person lines from the named twins in the
room). The twins in the room get stable names, ages and cities for the
session; none of them is an identified real person.

Question kinds: open, yes_no, multiple_choice, rating (1 to 5), price
(would you pay $X), ab (A vs B), show_of_hands, probe (why? / what would
change your mind?, following the last question). A re-ask with a
changed stimulus (price, cast, platform) moves the answer the way the
twin's curves say it should.

Distributions are deterministic from the traits, identity and response
curves (subject-and-question salted jitter), coherent with the twin: a
price question honors price_sensitivity through the elasticity, a watch
question honors the platform footprint and the lifecycle. The voices
and the read come from one short model call under a hard time budget
(about 12 seconds) with a deterministic fallback keyed on the traits,
so an answer always lands in real time.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Callable, Optional

from migration.digital_twin_engine import (
    VERSION, clamp, fmt_money, natural_count, pct1, platform_reach_pct, trait, unit,
)

DEFAULT_IN_ROOM = 12
MAX_IN_ROOM = 40
TIME_BUDGET_S = 12.0
KINDS = ('open', 'yes_no', 'multiple_choice', 'rating', 'price', 'ab', 'show_of_hands', 'probe')

_NAMES_F = ('Maya', 'Priya', 'Dana', 'Elena', 'Jasmine', 'Nora', 'Carmen', 'Leah', 'Ava', 'Simone', 'Rachel', 'Keisha', 'Mei', 'Sofia',
            'Hannah', 'Tessa', 'Monica', 'Grace', 'Aaliyah', 'Brooke', 'Lauren', 'Renee', 'Isabel', 'Jordan', 'Camila', 'Erin', 'Yara', 'Nia')
_NAMES_M = ('Marcus', 'Diego', 'Ethan', 'Andre', 'Caleb', 'Omar', 'Tyler', 'Luis', 'Jamal', 'Noah', 'Victor', 'Sean', 'Raj', 'Miles',
            'Owen', 'Derek', 'Mateo', 'Isaiah', 'Kenji', 'Brandon', 'Troy', 'Elijah', 'Gabriel', 'Reed', 'Hugo', 'Devin', 'Aaron', 'Cole')
_CITIES = ('Austin', 'Atlanta', 'Chicago', 'Phoenix', 'Denver', 'Seattle', 'Nashville', 'Dallas', 'Charlotte', 'Tampa', 'Portland',
           'Columbus', 'Minneapolis', 'San Diego', 'Philadelphia', 'Houston', 'Sacramento', 'Kansas City', 'Raleigh', 'Pittsburgh',
           'Orlando', 'Las Vegas', 'Boston', 'Baltimore', 'Detroit', 'Salt Lake City', 'Indianapolis', 'Milwaukee', 'St. Louis', 'Richmond')
_AGE_BUCKETS = (('18-24', 18, 24), ('25-34', 25, 34), ('35-44', 35, 44), ('45-54', 45, 54), ('55-64', 55, 64), ('65+', 65, 74))

_YES_RX = re.compile(r"^\s*(would|do|does|did|are|is|can|could|will|have|has|should|were|was|might|am)\b", re.I)
_HANDS_RX = re.compile(r"\b(show of hands|raise (?:your|a) hands?|hands up|how many of you|who (?:here )?(?:has|have|watched|saw|bought|pays|uses|follows|went|would|is|are))\b", re.I)
_RATING_RX = re.compile(r"\b(rate|rating|scale of|1 to 5|one to five|out of 5|out of five|stars?)\b", re.I)
_PRICE_RX = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_PRICE_WORD_RX = re.compile(r"\b(pay|price|cost|worth it|subscribe at|per month|a month|a year|dollars)\b", re.I)
_AB_RX = re.compile(r"(.+?)\s+(?:vs\.?|versus|or)\s+(.+?)\s*\??$", re.I)
_PROBE_RX = re.compile(r"^\s*(why(?: not)?|how come|say more|tell me more|what would change (?:your|their) mind|what would (?:make|get) you|what else|because\?|and why|why is that|what's holding you back|what would it take)\b", re.I)
_WHICH_RX = re.compile(r"^\s*(which|what)\b.*?(?::|\?|$)", re.I)
_SPLIT_LIST_RX = re.compile(r",\s*|\s+or\s+|\s*/\s*|\s*\|\s*")

_TOPICS = (
    ('watch', re.compile(r"\b(watch|stream|tune in|see it|see the|binge|episode|season|premiere|finale|movie|film|show|series)\b", re.I)),
    ('pay', re.compile(r"\b(pay|price|subscribe|subscription|cost|dollar|\$|rent|buy|purchase|ticket)\b", re.I)),
    ('merch', re.compile(r"\b(merch|box|kit|product|shirt|mug|collectible|drop|bundle|toy|book)\b", re.I)),
    ('creator', re.compile(r"\b(follow|creator|host|cast|actor|star|influencer|channel|podcast)\b", re.I)),
    ('live', re.compile(r"\b(live|concert|tour|event|in person|attend|theater|theatre|screening|convention|meet)\b", re.I)),
    ('recommend', re.compile(r"\b(recommend|tell (?:a )?friend|share|post about|word of mouth|talk about)\b", re.I)),
    ('return', re.compile(r"\b(come back|return|sequel|next season|spin-?off|reboot|revival|another season|season 2|part two)\b", re.I)),
    ('cancel', re.compile(r"\b(cancel|churn|quit|leave|drop the|unsubscribe|stop paying|switch)\b", re.I)),
    ('ads', re.compile(r"\b(ads?|commercials?|ad-supported|with ads|sponsored)\b", re.I)),
    ('platform', re.compile(r"\b(netflix|hulu|peacock|max|disney|paramount|prime video|apple tv|youtube|spotify|tubi|roku|theaters?)\b", re.I)),
)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _meta(t):
    return (t or {}).get('meta') or {}


def _twin_name(t):
    return str(_meta(t).get('name') or _meta(t).get('subject') or 'the twin')


def _twin_key(t):
    return str(_meta(t).get('key') or (t or {}).get('key') or _twin_name(t))


# --------------------------------------------------------------- the room
def _demo(t, cat):
    rows = ((t or {}).get('identity') or {}).get('demographics') or {}
    return [r for r in (rows.get(cat) or []) if isinstance(r, dict) and r.get('label') is not None]


def _share_of(t, cat, pred) -> float:
    rows = _demo(t, cat)
    if not rows:
        return 1.0
    tot = sum(float(r.get('pct') or 0) for r in rows) or 100.0
    hit = sum(float(r.get('pct') or 0) for r in rows if pred(str(r.get('label') or '')))
    return clamp(hit / tot, 0.0, 1.0) if hit else 1.0


def parse_segment(label: str) -> dict:
    """'women 25 to 44 in Dallas' -> {gender, age:(25,44), city, lifecycle}."""
    low = ' '.join(str(label or '').lower().split())
    f = {}
    if re.search(r"\b(women|woman|female|females|girls|moms|mothers)\b", low):
        f['gender'] = 'F'
    elif re.search(r"\b(men|man|male|males|guys|dads|fathers)\b", low):
        f['gender'] = 'M'
    m = re.search(r"(\d{2})\s*(?:to|-|\u2013|through)\s*(\d{2})", low)
    if m:
        f['age'] = (int(m.group(1)), int(m.group(2)))
    elif re.search(r"\b(gen z|gen-z|genz)\b", low):
        f['age'] = (18, 28)
    elif re.search(r"\bmillennials?\b", low):
        f['age'] = (29, 44)
    elif re.search(r"\bgen x\b", low):
        f['age'] = (45, 60)
    elif re.search(r"\b(boomers?|65\+|over 65|seniors?)\b", low):
        f['age'] = (65, 80)
    elif re.search(r"\b(under 35|younger)\b", low):
        f['age'] = (18, 34)
    m = re.search(r"\b(?:in|from)\s+([A-Za-z .]{3,30}?)(?:,|$|\swho|\sthat)", str(label or ''))
    if m:
        f['city'] = m.group(1).strip()
    for lc in ('lapsed', 'lapsing', 'new', 'avid', 'casual', 'returning', 'habit', 'trying', 'discovering'):
        if re.search(rf"\b{lc}\b", low):
            f['lifecycle'] = lc
            break
    return f


def _age_pred(rng):
    lo, hi = rng

    def pred(label):
        m = re.match(r"\s*(\d{2})\s*[-\u2013]\s*(\d{2})", str(label))
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return not (b < lo or a > hi)
        m = re.match(r"\s*(\d{2})\s*\+", str(label))
        if m:
            return int(m.group(1)) <= hi
        return False
    return pred


def _gender_pred(g):
    return (lambda l: l.lower().startswith(('f', 'w'))) if g == 'F' else (lambda l: l.lower().startswith('m'))


def _segment_share(t, f: dict) -> float:
    s = 1.0
    if f.get('gender'):
        s *= _share_of(t, 'gender', _gender_pred(f['gender']))
    if f.get('age'):
        s *= _share_of(t, 'age', _age_pred(f['age']))
    if f.get('city'):
        geo = ((t or {}).get('identity') or {}).get('geography') or []
        hit = next((float(r.get('pct') or 0) for r in geo if f['city'].lower() in str(r.get('label') or '').lower()), 0.0)
        s *= clamp(hit / 100.0, 0.004, 1.0) if hit else 0.012
    if f.get('lifecycle'):
        lc = (t or {}).get('lifecycle') or {}
        key = {'lapsed': 'lapsing_pct', 'lapsing': 'lapsing_pct', 'new': 'discovering_pct', 'discovering': 'discovering_pct', 'trying': 'trying_pct',
               'habit': 'habit_pct', 'returning': 'returning_pct', 'avid': 'habit_pct', 'casual': 'trying_pct'}.get(f['lifecycle'])
        if key and lc.get(key) is not None:
            s *= clamp(float(lc.get(key) or 0) / 100.0, 0.02, 1.0)
    return clamp(s, 0.002, 1.0)


def build_group(twins: list, segments: Optional[list] = None, in_room: int = DEFAULT_IN_ROOM, seed: str = '') -> dict:
    """The group: segments with their share of the room and the people
    they stand for, and a seated roster with stable names, ages and
    cities."""
    in_room = int(clamp(int(in_room or DEFAULT_IN_ROOM), 2, MAX_IN_ROOM))
    by_key = {_twin_key(t): t for t in twins}
    segs = []
    spec = segments or [{'label': _twin_name(t), 'twin': _twin_key(t)} for t in twins]
    for i, s in enumerate(spec):
        if isinstance(s, str):
            s = {'label': s}
        label = str(s.get('label') or '').strip()
        tkey = str(s.get('twin') or s.get('twin_key') or '')
        t = by_key.get(tkey) or next((tw for tw in twins if _twin_name(tw).lower() in label.lower()), None) or (twins[i % len(twins)] if twins else {})
        f = dict(s.get('filter') or {}) or parse_segment(label)
        share = _segment_share(t, f)
        people = max(50, int(float(_meta(t).get('size_us') or 1_000_000) * share))
        segs.append({'label': label or _twin_name(t), 'twin': _twin_key(t), 'twin_name': _twin_name(t), 'filter': {k: (list(v) if isinstance(v, tuple) else v) for k, v in f.items()},
                     'people': natural_count(seed, f"seg|{label}|people", people), 'share_pct': 0.0, 'seats': 0})
    total_people = sum(s['people'] for s in segs) or 1
    for s in segs:
        s['share_pct'] = pct1(s['people'] / total_people * 100.0)
    # seats: largest remainder, at least one each
    raw = [s['people'] / total_people * in_room for s in segs]
    seats = [max(1, int(r)) for r in raw]
    while sum(seats) < in_room:
        k = max(range(len(segs)), key=lambda i: raw[i] - seats[i])
        seats[k] += 1
    while sum(seats) > in_room:
        k = max(range(len(segs)), key=lambda i: seats[i] if seats[i] > 1 else -1)
        seats[k] -= 1
    roster = []
    seat_no = 0
    for s, n in zip(segs, seats):
        s['seats'] = n
        t = by_key.get(s['twin']) or {}
        f = s['filter'] or {}
        for j in range(n):
            seat_no += 1
            u = unit('seat', seed, s['label'], j)
            g = f.get('gender')
            if not g:
                fem = _share_of(t, 'gender', _gender_pred('F'))
                g = 'F' if u < fem else 'M'
            bank = _NAMES_F if g == 'F' else _NAMES_M
            name = bank[int(unit('name', seed, s['label'], j) * len(bank))]
            if f.get('age'):
                lo, hi = f['age'] if isinstance(f['age'], (list, tuple)) else (18, 54)
                age = int(lo + (hi - lo) * unit('age', seed, s['label'], j))
            else:
                ages = _demo(t, 'age')
                age = _draw_age(ages, unit('age', seed, s['label'], j))
            city = f.get('city') or _draw_city(t, unit('city', seed, s['label'], j))
            roster.append({'seat': seat_no, 'name': name, 'age': age, 'city': city, 'gender': g, 'segment': s['label'], 'twin': s['twin']})
    # no two seats share a name
    seen = set()
    for r in roster:
        base = r['name']
        bank = _NAMES_F if r['gender'] == 'F' else _NAMES_M
        k = 0
        while r['name'] in seen:
            k += 1
            if k < len(bank):
                r['name'] = bank[(bank.index(base) + k * 7) % len(bank)]
            else:   # the bank is spent in a big room: a last initial keeps the seat distinct
                r['name'] = f"{base} {chr(ord('A') + (k - len(bank)) % 26)}."
        seen.add(r['name'])
    return {'twins': [_twin_key(t) for t in twins], 'segments': [{k: v for k, v in s.items()} for s in segs],
            'size_people': total_people, 'in_room': in_room, 'roster': roster}


def _draw_age(ages, u):
    if not ages:
        return 22 + int(40 * u)
    tot = sum(float(a.get('pct') or 0) for a in ages) or 100.0
    acc = 0.0
    for a in ages:
        acc += float(a.get('pct') or 0) / tot
        if u <= acc:
            lab = str(a.get('label') or '')
            for key, lo, hi in _AGE_BUCKETS:
                if lab.startswith(key[:2]):
                    return lo + int((hi - lo) * ((u * 97) % 1.0))
            return 30 + int(20 * ((u * 97) % 1.0))
    return 34


def _draw_city(t, u):
    geo = ((t or {}).get('identity') or {}).get('geography') or []
    names = [re.sub(r"\s+(?:ca|tx|ny|fl|ga|il|pa|az|co|wa|or|nc|mi|oh|mn|nv|tn|mo|md|ma|va|in|wi|ut|dc|dma)$", '', str(r.get('label') or '').strip(), flags=re.I).title() for r in geo[:12] if r.get('label')]
    names = [n for n in names if n]
    if names and u < 0.72:
        return names[int((u / 0.72) * len(names))]
    return _CITIES[int(u * len(_CITIES))]


# ---------------------------------------------------------- the questions
def _options_from(q: str) -> list:
    m = re.search(r":\s*(.+?)\??$", q)
    tail = m.group(1) if m else ''
    if not tail:
        m = re.search(r"\b(?:between|prefer|pick|choose)\s+(.+?)\??$", q, re.I)
        tail = m.group(1) if m else ''
    parts = [re.sub(r"^(?:or|and)\s+", '', p.strip(' .'), flags=re.I).strip() for p in _SPLIT_LIST_RX.split(tail) if p.strip(' .')]
    parts = [p for p in parts if p]
    return parts if 2 <= len(parts) <= 8 else []


def classify_question(q: str, options: Optional[list] = None, last: Optional[dict] = None, kind: Optional[str] = None) -> dict:
    """{kind, options, price, stimulus} for a question in plain words."""
    text = ' '.join(str(q or '').split())
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    out = {'kind': None, 'options': opts, 'price': None}
    if kind in KINDS:
        out['kind'] = kind
    pm = _PRICE_RX.search(text)
    if pm:
        try:
            out['price'] = float(pm.group(1).replace(',', ''))
        except ValueError:
            pass
    if out['kind']:
        if out['kind'] == 'ab' and len(opts) < 2:
            m = _AB_RX.match(re.sub(r"^(which|what|do you prefer|would you (?:rather|pick|choose))\s*:?\s*", '', text, flags=re.I))
            if m:
                out['options'] = [m.group(1).strip(' ,'), m.group(2).strip(' ,?')]
        if out['kind'] == 'multiple_choice' and len(opts) < 2:
            out['options'] = _options_from(text)
        return out
    if last and _PROBE_RX.match(text) and len(text.split()) <= 14:
        out['kind'] = 'probe'
        return out
    if _HANDS_RX.search(text):
        out['kind'] = 'show_of_hands'
        return out
    if _RATING_RX.search(text):
        out['kind'] = 'rating'
        return out
    if out['price'] is not None or (_PRICE_WORD_RX.search(text) and re.search(r"\d", text)):
        out['kind'] = 'price'
        return out
    if len(opts) >= 2:
        out['kind'] = 'ab' if len(opts) == 2 else 'multiple_choice'
        return out
    stripped = re.sub(r"^(which (?:one |do you prefer|would you (?:rather |pick |choose )?)?|what do you prefer|do you prefer|would you rather)\s*:?\s*", '', text, flags=re.I)
    m = _AB_RX.match(stripped)
    if m and re.search(r"\b(vs\.?|versus)\b", text, re.I):
        out['kind'] = 'ab'
        out['options'] = [m.group(1).strip(' ,'), m.group(2).strip(' ,?')]
        return out
    if _WHICH_RX.match(text):
        o = _options_from(text)
        if len(o) == 2 and re.search(r"\bor\b", text, re.I):
            out['kind'] = 'ab'
            out['options'] = o
            return out
        if len(o) >= 2:
            out['kind'] = 'multiple_choice'
            out['options'] = o
            return out
    if m and re.search(r"\s+or\s+", stripped, re.I) and _YES_RX.match(text) and len(m.group(1).split()) <= 6 and len(m.group(2).split()) <= 6:
        out['kind'] = 'ab'
        out['options'] = [re.sub(r"^(would|do|did|are|is|can|will|should) (?:you|they) (?:rather )?", '', m.group(1), flags=re.I).strip(' ,'), m.group(2).strip(' ,?')]
        return out
    if _YES_RX.match(text):
        out['kind'] = 'yes_no'
        return out
    out['kind'] = 'open'
    return out


def topic_of(q: str) -> str:
    for name, rx in _TOPICS:
        if rx.search(str(q or '')):
            return name
    return 'general'


def _platform_in(q: str) -> str:
    m = re.search(r"\b(netflix|hulu|peacock|max|disney\+?|paramount\+?|prime video|apple tv\+?|youtube|spotify|tubi|roku|theaters?)\b", str(q or ''), re.I)
    return m.group(1).title() if m else ''


def propensity(twin: dict, q: str, topic: str, stimulus: Optional[dict] = None, seat: Optional[dict] = None) -> float:
    """The share of this twin that answers yes to the question, before
    the segment tilt: traits, footprint and lifecycle, with the stimulus
    (price, cast, platform) applied through the response curves."""
    g = lambda k: trait(twin, k) / 100.0
    lc = (twin or {}).get('lifecycle') or {}
    engaged = clamp((float(lc.get('habit_pct') or 30) + float(lc.get('returning_pct') or 10) + 0.5 * float(lc.get('trying_pct') or 20)) / 100.0, 0.1, 0.95)
    rc = (twin or {}).get('response') or {}
    st = stimulus or {}
    if topic == 'watch':
        p = 0.30 + 0.45 * engaged + 0.12 * g('trial_speed')
        plat = st.get('platform') or _platform_in(q)
        if plat:
            p *= clamp(platform_reach_pct(twin, plat) / 100.0 / 0.6, 0.35, 1.25)
    elif topic == 'pay':
        p = 0.22 + 0.28 * engaged - 0.18 * g('price_sensitivity') + 0.1 * g('franchise_loyalty')
    elif topic == 'merch':
        p = 0.08 + 0.5 * g('merch_propensity') * (0.6 + 0.4 * g('franchise_loyalty'))
    elif topic == 'creator':
        p = 0.2 + 0.6 * g('creator_follow')
    elif topic == 'live':
        p = 0.06 + 0.5 * g('live_events')
    elif topic == 'recommend':
        p = 0.25 + 0.55 * g('word_of_mouth')
    elif topic == 'return':
        p = float((rc.get('sequel_return_rate') or {}).get('return_pct') or 40) / 100.0 * (0.85 + 0.3 * g('franchise_loyalty'))
    elif topic == 'cancel':
        p = 0.05 + 0.45 * g('churn_propensity')
    elif topic == 'ads':
        p = 0.15 + 0.6 * g('ad_tolerance')
    elif topic == 'platform':
        p = 0.3 + 0.4 * engaged
        plat = st.get('platform') or _platform_in(q)
        if plat:
            p *= clamp(platform_reach_pct(twin, plat) / 100.0 / 0.6, 0.35, 1.25)
    else:
        p = 0.28 + 0.3 * engaged + 0.15 * g('trial_speed') + 0.1 * g('franchise_loyalty')
    # price stimulus through the elasticity
    price = st.get('price')
    if price is None:
        pm = _PRICE_RX.search(str(q or ''))
        price = float(pm.group(1).replace(',', '')) if pm else None
    if price is not None:
        pe = rc.get('price_elasticity') or {}
        ref = float(pe.get('reference_price') or 14.0)
        if topic == 'merch' or re.search(r"\b(box|kit|product|merch|shirt|mug)\b", str(q or ''), re.I):
            ref = max(ref, 29.0)
        e = float(pe.get('elasticity') or -1.4)
        try:
            p *= clamp((float(price) / ref) ** e, 0.15, 1.6)
        except (ZeroDivisionError, ValueError, OverflowError):
            pass
    if st.get('cast'):
        p *= 1 + float((rc.get('cast_attach_lift') or {}).get('reach_lift_pct') or 6) / 100.0
    # the seat's tilt: age and gender move the answer a little, deterministically
    if seat:
        age = int(seat.get('age') or 35)
        if topic in ('merch', 'creator', 'platform') and age < 30:
            p *= 1.08
        if topic in ('pay', 'cancel') and age >= 50:
            p *= 0.94 if topic == 'cancel' else 1.05
        if topic == 'live' and age < 35:
            p *= 1.1
    return clamp(p, 0.02, 0.97)


def _people_rows(labels_pcts: list, people: int, seed: str) -> list:
    """[{label, pct, people}] with pcts summing to 100.0 and people
    summing to ``people`` exactly (natural digits on long tables)."""
    tot = sum(max(0.0, p) for _l, p in labels_pcts) or 1.0
    rows = [[l, max(0.0, p) / tot * 100.0] for l, p in labels_pcts]
    pcts = [round(p, 1) for _l, p in rows]
    gap = round(100.0 - sum(pcts), 1)
    k = max(range(len(pcts)), key=lambda i: pcts[i])
    pcts[k] = round(pcts[k] + gap, 1)
    counts = [int(people * p / 100.0) for p in pcts]
    diff = people - sum(counts)
    counts[k] += diff
    out = []
    for (l, _p), pc, n in zip(rows, pcts, counts):
        out.append({'label': l, 'pct': pc, 'people': max(0, n)})
    return out


def distribution(twin: dict, q: str, kind: str, options: list, people: int, seed: str, stimulus: Optional[dict] = None,
                 last: Optional[dict] = None, seat: Optional[dict] = None) -> dict:
    """The answer for one twin (or one segment of it) as rows."""
    topic = topic_of(q)
    p = propensity(twin, q, topic, stimulus, seat)
    p = clamp(p * (1 + (unit('p', seed, q) - 0.5) * 0.08), 0.02, 0.97)
    out = {'topic': topic}
    if kind in ('yes_no', 'show_of_hands'):
        maybe = (1 - p) * (0.22 + 0.3 * (1 - trait(twin, 'word_of_mouth') / 100.0))
        if kind == 'show_of_hands':
            rows = _people_rows([('Hands up', p), ('Hands down', 1 - p)], people, seed)
        else:
            rows = _people_rows([('Yes', p), ('Maybe', maybe), ('No', 1 - p - maybe)], people, seed)
        out['rows'] = rows
        out['yes_pct'] = rows[0]['pct']
    elif kind == 'rating':
        mean = 1 + 4 * clamp(0.15 + 0.85 * p, 0.05, 0.98)
        kappa = 3.2 + 3.0 * trait(twin, 'word_of_mouth') / 100.0
        a = max(0.3, (mean - 1) / 4 * kappa)
        b = max(0.3, kappa - a)
        weights = []
        for s in range(1, 6):
            x = (s - 0.5) / 5.0
            weights.append(x ** (a - 1) * (1 - x) ** (b - 1) + 0.004)
        rows = _people_rows([(f"{s} star{'s' if s != 1 else ''}", w) for s, w in zip(range(1, 6), weights)], people, seed)
        out['rows'] = rows
        out['average'] = round(sum((i + 1) * r['pct'] for i, r in enumerate(rows)) / 100.0, 2)
        out['top_two_box_pct'] = round(rows[3]['pct'] + rows[4]['pct'], 1)
    elif kind == 'price':
        pm = _PRICE_RX.search(str(q or ''))
        price = (stimulus or {}).get('price') or (float(pm.group(1).replace(',', '')) if pm else None)
        take = p
        rc = (twin or {}).get('response') or {}
        pe = rc.get('price_elasticity') or {}
        e = float(pe.get('elasticity') or -1.4)
        if price:
            # the price at which half of the yes group falls away
            indifference = float(price) * (0.5 ** (1.0 / e)) if e else float(price)
            lower = float(price) * 0.75
            take_lower = clamp(take * (0.75 ** e), take, 0.97)
        else:
            indifference, lower, take_lower = None, None, take
        would_if_less = clamp(take_lower - take, 0.0, 1 - take)
        rows = _people_rows([('Would pay', take), ('Would pay at a lower price', would_if_less), ('Would not pay', 1 - take - would_if_less)], people, seed)
        out['rows'] = rows
        out['yes_pct'] = rows[0]['pct']
        out['price'] = price
        if indifference:
            out['price_half_fall_away'] = round(indifference, 2)
            out['lower_price_tested'] = round(lower, 2)
    elif kind == 'ab':
        a_txt, b_txt = (options + ['A', 'B'])[:2]
        wa = 1.0 + _affinity(twin, a_txt) + (unit('ab', seed, a_txt) - 0.5) * 0.2
        wb = 1.0 + _affinity(twin, b_txt) + (unit('ab', seed, b_txt) - 0.5) * 0.2
        neither = 0.06 + 0.1 * (1 - p)
        rows = _people_rows([(a_txt, wa * (1 - neither)), (b_txt, wb * (1 - neither)), ('Neither', neither * (wa + wb))], people, seed)
        out['rows'] = rows
        out['winner'] = rows[0]['label'] if rows[0]['pct'] >= rows[1]['pct'] else rows[1]['label']
    elif kind == 'multiple_choice':
        opts = options or ['Yes', 'No']
        ws = [(o, 1.0 + _affinity(twin, o) + (unit('mc', seed, o) - 0.5) * 0.3) for o in opts]
        rows = _people_rows(ws, people, seed)
        out['rows'] = sorted(rows, key=lambda r: -r['pct'])
    elif kind == 'probe':
        out['rows'] = _probe_rows(twin, q, last, people, seed)
    else:   # open: themes
        out['rows'] = _theme_rows(twin, q, topic, p, people, seed)
    out['propensity'] = round(p, 4)
    return out


_AFFINITY = (
    (re.compile(r"\b(cast|star|actor|host|creator|name|celebrity|lead)\b", re.I), 'creator_follow', 0.5),
    (re.compile(r"\b(trailer|clip|teaser|tiktok|short|reel)\b", re.I), 'discovery_social_vs_search', 0.35),
    (re.compile(r"\b(cheap|cheaper|free|discount|lower|bundle|with ads)\b", re.I), 'price_sensitivity', 0.45),
    (re.compile(r"\b(premium|exclusive|limited|collector|signed|deluxe)\b", re.I), 'merch_propensity', 0.35),
    (re.compile(r"\b(weekly|every week|appointment|live)\b", re.I), 'live_events', 0.25),
    (re.compile(r"\b(binge|all at once|full season|drop)\b", re.I), 'binge_vs_appointment', 0.4),
    (re.compile(r"\b(sequel|season 2|next season|return|reunion|revival)\b", re.I), 'franchise_loyalty', 0.4),
    (re.compile(r"\b(new|original|fresh|different|unknown)\b", re.I), 'trial_speed', 0.3),
)


def _affinity(twin: dict, text: str) -> float:
    a = 0.0
    for rx, key, w in _AFFINITY:
        if rx.search(str(text or '')):
            a += (trait(twin, key) / 100.0 - 0.5) * 2 * w
    if re.search(r"\b(weekly|every week|appointment)\b", str(text or ''), re.I):
        a -= (trait(twin, 'binge_vs_appointment') / 100.0 - 0.5) * 0.8
    return clamp(a, -0.6, 0.9)


def _theme_rows(twin, q, topic, p, people, seed):
    g = lambda k: trait(twin, k) / 100.0
    if topic in ('watch', 'return', 'platform', 'general'):
        themes = [('The cast and the people in it', 0.5 + g('creator_follow')), ('A story they have not seen before', 0.4 + g('trial_speed')),
                  ('Easy to find on the service they already pay for', 0.5 + (1 - g('discovery_social_vs_search'))), ('Friends talking about it', 0.3 + g('word_of_mouth')),
                  ('Nothing would; not for them', 0.25 + (1 - p))]
    elif topic in ('pay', 'merch'):
        themes = [('Worth it at the right price', 0.4 + (1 - g('price_sensitivity'))), ('Only with a discount or a bundle', 0.3 + g('price_sensitivity')),
                  ('Would rather wait for it to be free', 0.25 + g('price_sensitivity') * 0.6), ('Already paying for too much', 0.3 + g('churn_propensity')),
                  ('Yes, no hesitation', 0.2 + g('franchise_loyalty') * 0.8)]
    elif topic == 'cancel':
        themes = [('Nothing else to watch there', 0.3 + g('churn_propensity')), ('The price went up', 0.3 + g('price_sensitivity')),
                  ('Would keep it for the next season', 0.3 + g('franchise_loyalty')), ('Would pause and come back', 0.25 + g('trial_speed'))]
    elif topic in ('creator', 'recommend'):
        themes = [('Already follow and tell friends', 0.3 + g('word_of_mouth')), ('Would watch but not post about it', 0.4), ('Depends on the next thing they make', 0.35 + g('trial_speed')),
                  ('Not really their person', 0.3 + (1 - g('creator_follow')))]
    elif topic == 'live':
        themes = [('Would go if it came to their city', 0.3 + g('live_events')), ('Too expensive to travel for', 0.3 + g('price_sensitivity')),
                  ('Would rather watch at home', 0.4 + (1 - g('live_events'))), ('Yes, would buy a ticket today', 0.15 + g('live_events') * g('franchise_loyalty'))]
    else:
        themes = [('Interested', 0.4 + p), ('Interested if the price is right', 0.3 + g('price_sensitivity')), ('Not for them', 0.3 + (1 - p)), ('Need to hear more', 0.3)]
    rows = _people_rows([(t, w * (1 + (unit('theme', seed, t) - 0.5) * 0.15)) for t, w in themes], people, seed)
    return sorted(rows, key=lambda r: -r['pct'])


def _probe_rows(twin, q, last, people, seed):
    g = lambda k: trait(twin, k) / 100.0
    low = str(q or '').lower()
    lk = (last or {}).get('kind') or 'open'
    topic = (last or {}).get('topic') or topic_of((last or {}).get('q') or '')
    change = bool(re.search(r"change (?:your|their) mind|what would (?:make|get|it take)|holding you back", low))
    if change:
        levers = [('A lower price or a bundle', 0.3 + g('price_sensitivity')), ('A cast member or creator they follow', 0.3 + g('creator_follow')),
                  ('Friends watching it too', 0.25 + g('word_of_mouth')), ('It landing on the service they already pay for', 0.4 + (1 - g('churn_propensity')) * 0.4),
                  ('A trailer that shows more of the story', 0.3 + g('discovery_social_vs_search') * 0.5), ('Nothing; they are a no', 0.15 + (1 - g('trial_speed')) * 0.5)]
        rows = _people_rows([(t, w * (1 + (unit('lever', seed, t) - 0.5) * 0.15)) for t, w in levers], people, seed)
        return sorted(rows, key=lambda r: -r['pct'])
    # why: reasons split between the yes side and the no side of the last answer
    yes_share = float((last or {}).get('yes_pct') or 50) / 100.0
    if topic in ('pay', 'merch', 'price') or lk == 'price':
        yes_r = [('Worth it for something they love', 0.5 + g('franchise_loyalty')), ('The price feels fair', 0.4 + (1 - g('price_sensitivity')))]
        no_r = [('Too expensive for what it is', 0.4 + g('price_sensitivity')), ('Already paying for too many things', 0.3 + g('churn_propensity')), ('Would wait for a deal', 0.3)]
    elif topic in ('watch', 'return', 'platform'):
        yes_r = [('Loved the last one', 0.4 + g('franchise_loyalty')), ('The cast', 0.3 + g('creator_follow')), ('Friends are watching', 0.25 + g('word_of_mouth'))]
        no_r = [('Not on a service they have', 0.35 + g('price_sensitivity') * 0.3), ('Too much else to watch', 0.35 + g('churn_propensity')), ('Lost interest after the last one', 0.3 + (1 - g('franchise_loyalty')))]
    else:
        yes_r = [('It fits what they already like', 0.5), ('Curious to see it', 0.3 + g('trial_speed'))]
        no_r = [('Not for them', 0.4), ('Would need to hear more first', 0.3), ('Time and money go elsewhere', 0.3 + g('churn_propensity'))]
    rows_in = [(f"Yes side: {t}", w * yes_share) for t, w in yes_r] + [(f"No side: {t}", w * (1 - yes_share)) for t, w in no_r]
    rows = _people_rows([(t, w * (1 + (unit('why', seed, t) - 0.5) * 0.15)) for t, w in rows_in], people, seed)
    return sorted(rows, key=lambda r: -r['pct'])


# ------------------------------------------------------------------ voices
_VOICE_BANK = {
    ('yes', 'creator_follow'): ["I would watch anything {they} put out. I have followed since the start.", "The people in it are the reason I show up."],
    ('yes', 'franchise_loyalty'): ["I was in before you finished the question. I have been waiting for the next one.", "This is comfort viewing for me; of course I would."],
    ('yes', 'word_of_mouth'): ["Yes, and I would be texting my group chat about it the same night.", "I would watch it and then make three friends watch it."],
    ('yes', 'trial_speed'): ["I try most new things in the first week. This would be one of them.", "Sure. New title, I am in early, then I decide."],
    ('yes', 'default'): ["Yes. It fits what I already watch.", "I would, as long as it is where I already pay."],
    ('maybe', 'price_sensitivity'): ["Depends on the price. Under twenty and I am in; over that I wait.", "Maybe. I would look for a deal first."],
    ('maybe', 'default'): ["Maybe. Show me the trailer and I will tell you.", "I am not against it; I just need a reason this month."],
    ('no', 'price_sensitivity'): ["Not at that price. I would wait for it to come around free.", "No. I am already paying for three services I barely use."],
    ('no', 'churn_propensity'): ["No. I cancel the minute a show ends; I would not add another thing.", "I would skip it. There is too much already."],
    ('no', 'default'): ["Not for me. I would hear about it and move on.", "No. Nothing in it pulls me in."],
    ('rating', 'high'): ["Four or five. It does what I want it to do.", "A five from me; I would tell people to try it."],
    ('rating', 'low'): ["A two. It is fine, it is just not for me.", "Three at best. I would not go out of my way."],
    ('ab', 'a'): ["{a}. No question.", "{a}, because that is the one that would make me stop scrolling."],
    ('ab', 'b'): ["{b}. The other one feels like it is trying too hard.", "{b} for me."],
    ('open', 'default'): ["Honestly, the cast would decide it for me.", "If my friends were talking about it I would be there.", "I would need it to be on something I already have."],
    ('probe', 'default'): ["Because the last one earned it.", "Mostly the price. Everything else is fine.", "It is about time more than money for me."],
}


def fallback_voices(twin: dict, roster: list, q: str, kind: str, dist: dict, options: list, qi: int, seed: str, n: int = 4) -> list:
    """First-person lines from named twins in the room, keyed on the
    traits and the answer, when the model is off or late."""
    rows = dist.get('rows') or []
    yes = float(dist.get('yes_pct') or (rows[0]['pct'] if rows else 50))
    dominant = max(('creator_follow', 'franchise_loyalty', 'word_of_mouth', 'trial_speed'), key=lambda k: trait(twin, k))
    topic = dist.get('topic') or topic_of(q)
    hesitant = 'price_sensitivity' if (topic in ('pay', 'merch') or kind == 'price') else ('churn_propensity' if trait(twin, 'churn_propensity') >= 45 else 'default')
    out = []
    seats = roster[:]
    if not seats:
        return out
    start = (qi * 5) % len(seats)
    picks = [seats[(start + i * max(1, len(seats) // n)) % len(seats)] for i in range(min(n, len(seats)))]
    for i, s in enumerate(picks):
        u = unit('voice', seed, qi, s['name'])
        if kind in ('yes_no', 'show_of_hands', 'price'):
            side = 'yes' if u * 100 < yes else ('maybe' if u * 100 < yes + (rows[1]['pct'] if len(rows) > 2 else 0) else 'no')
            key = (side, dominant if side == 'yes' else hesitant if side != 'maybe' else 'price_sensitivity')
            bank = _VOICE_BANK.get(key) or _VOICE_BANK.get((side, 'default'))
        elif kind == 'rating':
            bank = _VOICE_BANK[('rating', 'high' if float(dist.get('average') or 3) >= 3.4 else 'low')]
        elif kind == 'ab':
            w = dist.get('winner') or (options[0] if options else 'A')
            bank = _VOICE_BANK[('ab', 'a' if u < 0.65 else 'b')]
        elif kind == 'probe':
            bank = _VOICE_BANK[('probe', 'default')]
        else:
            bank = _VOICE_BANK[('open', 'default')]
        line = bank[int(u * len(bank)) % len(bank)]
        a_txt, b_txt = (options + ['A', 'B'])[:2]
        if kind == 'ab':
            win = dist.get('winner') or a_txt
            other = b_txt if win == a_txt else a_txt
            line = line.replace('{a}', win if u < 0.65 else other).replace('{b}', other if u < 0.65 else win)
        line = line.replace('{they}', 'they')
        out.append({'name': s['name'], 'age': s['age'], 'city': s['city'], 'segment': s['segment'], 'line': line})
    return out


def with_time_budget(fn: Callable, seconds: float, *args, **kwargs):
    """Run fn on a thread; None when it does not return in time."""
    box = {}

    def _go():
        try:
            box['v'] = fn(*args, **kwargs)
        except Exception as e:   # the fallback carries the answer
            box['e'] = e
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    t.join(max(0.5, float(seconds)))
    return box.get('v') if t.is_alive() is False or 'v' in box else None


def one_line_read(q: str, kind: str, dist: dict, by_segment: list, options: list) -> str:
    rows = dist.get('rows') or []
    if not rows:
        return ''
    top = rows[0]
    seg_line = ''
    if by_segment and len(by_segment) > 1:
        def _score(s):
            if kind == 'rating':
                return float(s.get('average') or 0)
            if kind in ('yes_no', 'show_of_hands', 'price'):
                return float(s.get('yes_pct') or 0)
            d = s.get('distribution') or []
            return float(next((r['pct'] for r in d if r['label'] == top['label']), 0))
        best = max(by_segment, key=_score)
        worst = min(by_segment, key=_score)
        if best is not worst and abs(_score(best) - _score(worst)) >= (0.15 if kind == 'rating' else 1.5):
            unit_txt = ' out of 5' if kind == 'rating' else '%'
            seg_line = f" Strongest among {best['segment']} ({_score(best)}{unit_txt}), softest among {worst['segment']} ({_score(worst)}{unit_txt})."
    if kind in ('yes_no', 'show_of_hands', 'price'):
        second = rows[1] if len(rows) > 1 else None
        lead = f"{top['pct']}% {top['label'].lower()}" + (f", {second['pct']}% {second['label'].lower()}" if second else '') + (f", {rows[2]['pct']}% {rows[2]['label'].lower()}" if len(rows) > 2 else '') + '.'
        if kind == 'price' and dist.get('price_half_fall_away'):
            lead += f" Half of the yes side falls away above {fmt_money(dist['price_half_fall_away'])}."
        return lead + seg_line
    if kind == 'rating':
        return f"Average {dist.get('average')} out of 5; {dist.get('top_two_box_pct')}% give it a 4 or a 5." + seg_line
    if kind == 'ab':
        win = dist.get('winner') or rows[0]['label']
        w_row = next((r for r in rows if r['label'] == win), rows[0])
        l_row = next((r for r in rows[:2] if r['label'] != win), rows[1] if len(rows) > 1 else rows[0])
        return f"{win} wins, {w_row['pct']}% to {l_row['pct']}%, with {rows[2]['pct'] if len(rows) > 2 else 0}% picking neither." + seg_line
    return f"{top['label']} leads at {top['pct']}%" + (f", then {rows[1]['label'].lower()} at {rows[1]['pct']}%" if len(rows) > 1 else '') + '.' + seg_line


def open_session(twins: list, *, segments: Optional[list] = None, in_room: int = DEFAULT_IN_ROOM, title: str = '',
                 created_by: str = '', session_id: str = '') -> dict:
    sid = session_id or uuid.uuid4().hex[:10]
    seed = f"panel|{sid}|{'|'.join(_twin_key(t) for t in twins)}|{VERSION}"
    group = build_group(twins, segments, in_room, seed)
    names = ', '.join(dict.fromkeys(_twin_name(t) for t in twins))
    return {'id': sid, 'title': title or f"Room: {names}", 'group': group, 'questions': [], 'created_by': created_by or 'prometheus',
            'created_at': _now(), 'updated_at': _now(), 'version': VERSION}


def ask(session: dict, twins_by_key: dict, q: str, *, kind: Optional[str] = None, options: Optional[list] = None,
        stimulus: Optional[dict] = None, voices_fn: Optional[Callable] = None, time_budget_s: float = TIME_BUDGET_S,
        asked_by: str = '') -> dict:
    """Ask the room one question. Appends the record to the session and
    returns it."""
    q = ' '.join(str(q or '').split())[:400]
    last = session['questions'][-1] if session.get('questions') else None
    cls = classify_question(q, options, last, kind)
    kind = cls['kind']
    opts = cls['options']
    group = session['group']
    seed = f"{session['id']}|{len(session['questions'])}|{q}|{VERSION}"
    stim = dict(stimulus or {})
    if cls.get('price') is not None and 'price' not in stim:
        stim['price'] = cls['price']
    if kind == 'probe' and last:
        base_q = str(last.get('q') or '')
    else:
        base_q = q
    seg_rows = []
    for seg in group['segments']:
        t = twins_by_key.get(seg['twin']) or next(iter(twins_by_key.values()), {})
        seat = next((r for r in group['roster'] if r['segment'] == seg['label']), None)
        d = distribution(t, base_q if kind != 'probe' else q, kind, opts, int(seg['people']), seed + '|' + seg['label'], stim, last, seat)
        seg_rows.append({'segment': seg['label'], 'people': seg['people'], 'distribution': d['rows'], 'yes_pct': d.get('yes_pct'), 'average': d.get('average')})
    # the room: people-weighted blend of the segments
    labels = []
    for sr in seg_rows:
        for r in sr['distribution']:
            if r['label'] not in labels:
                labels.append(r['label'])
    total_people = sum(int(s['people']) for s in group['segments']) or 1
    blended = []
    for lab in labels:
        ppl = sum(next((r['people'] for r in sr['distribution'] if r['label'] == lab), 0) for sr in seg_rows)
        blended.append((lab, ppl))
    rows = _people_rows(blended, total_people, seed + '|room')
    if kind in ('multiple_choice', 'open', 'probe'):
        rows.sort(key=lambda r: -r['pct'])
    first_twin = twins_by_key.get(group['segments'][0]['twin']) if group.get('segments') else next(iter(twins_by_key.values()), {})
    dist = {'rows': rows, 'topic': topic_of(base_q)}
    if kind in ('yes_no', 'show_of_hands', 'price'):
        dist['yes_pct'] = rows[0]['pct']
    if kind == 'rating':
        dist['average'] = round(sum((i + 1) * r['pct'] for i, r in enumerate(rows)) / 100.0, 2)
        dist['top_two_box_pct'] = round(rows[3]['pct'] + rows[4]['pct'], 1) if len(rows) >= 5 else None
    if kind == 'ab' and len(rows) >= 2:
        dist['winner'] = rows[0]['label'] if rows[0]['pct'] >= rows[1]['pct'] else rows[1]['label']
    if kind == 'price':
        d0 = distribution(first_twin, base_q, 'price', opts, 1000, seed + '|p', stim, last)
        for k in ('price', 'price_half_fall_away', 'lower_price_tested'):
            if d0.get(k) is not None:
                dist[k] = d0[k]
    qi = len(session['questions'])
    read = one_line_read(q, kind, dist, seg_rows, opts)
    voices = None
    if voices_fn is not None:
        ctx = {'question': q, 'kind': kind, 'options': opts, 'distribution': rows, 'read': read, 'roster': group['roster'][:8],
               'twin': _twin_name(first_twin), 'traits': {k: (v.get('read') if isinstance(v, dict) else v) for k, v in ((first_twin or {}).get('traits') or {}).items()},
               'last_question': (last or {}).get('q'), 'stimulus': stim}
        try:
            got = with_time_budget(voices_fn, time_budget_s, ctx)
            if isinstance(got, dict):
                vv = got.get('voices')
                if got.get('read') and isinstance(got.get('read'), str) and len(got['read']) < 400:
                    read = got['read']
            else:
                vv = got
            if isinstance(vv, list) and vv:
                names = {r['name']: r for r in group['roster']}
                voices = []
                for v in vv[:5]:
                    if isinstance(v, dict) and v.get('line'):
                        r = names.get(str(v.get('name') or ''))
                        if r:
                            voices.append({'name': r['name'], 'age': r['age'], 'city': r['city'], 'segment': r['segment'], 'line': str(v['line'])[:300]})
                voices = voices or None
        except Exception:
            voices = None
    if not voices:
        voices = fallback_voices(first_twin, group['roster'], q, kind, dist, opts, qi, seed)
    rec = {'id': f"q{qi + 1}", 'q': q, 'kind': kind, 'options': opts, 'topic': dist['topic'], 'distribution': rows,
           'by_segment': seg_rows, 'voices': voices, 'read': read, 'stimulus': stim, 'follows': last['id'] if (kind == 'probe' and last) else None,
           'yes_pct': dist.get('yes_pct'), 'average': dist.get('average'), 'top_two_box_pct': dist.get('top_two_box_pct'), 'winner': dist.get('winner'),
           'price': dist.get('price'), 'price_half_fall_away': dist.get('price_half_fall_away'), 'asked_by': asked_by or session.get('created_by'),
           'asked_at': _now()}
    session['questions'].append(rec)
    session['updated_at'] = _now()
    return rec


def session_lines(session: dict, rec: Optional[dict] = None) -> list:
    """Plain lines for chat: the % table and the voices."""
    rec = rec or (session['questions'][-1] if session.get('questions') else None)
    if not rec:
        return [f"{session.get('title')}: {session['group']['in_room']} twins in the room standing for {session['group']['size_people']:,} people."]
    out = [f"{rec['q']}"]
    for r in rec['distribution'][:8]:
        out.append(f"{r['label']}: {r['pct']}% ({r['people']:,} people)")
    if rec.get('read'):
        out.append(rec['read'])
    for v in (rec.get('voices') or [])[:4]:
        out.append(f"{v['name']}, {v['age']}, {v['city']}: {v['line']}")
    return out


def session_csv(session: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Session', 'Question id', 'Question', 'Kind', 'Segment', 'Answer', 'Share %', 'People'])
    for q in session.get('questions') or []:
        for r in q.get('distribution') or []:
            w.writerow([session.get('id'), q.get('id'), q.get('q'), q.get('kind'), 'Whole room', r.get('label'), r.get('pct'), r.get('people')])
        for sr in q.get('by_segment') or []:
            for r in sr.get('distribution') or []:
                w.writerow([session.get('id'), q.get('id'), q.get('q'), q.get('kind'), sr.get('segment'), r.get('label'), r.get('pct'), r.get('people')])
    w.writerow([])
    w.writerow(['Session', 'Question id', 'Twin', 'Age', 'City', 'Segment', 'Line'])
    for q in session.get('questions') or []:
        for v in q.get('voices') or []:
            w.writerow([session.get('id'), q.get('id'), v.get('name'), v.get('age'), v.get('city'), v.get('segment'), v.get('line')])
    return buf.getvalue()


__all__ = ['open_session', 'ask', 'build_group', 'classify_question', 'distribution', 'propensity', 'parse_segment', 'fallback_voices',
           'with_time_budget', 'session_lines', 'session_csv', 'one_line_read', 'topic_of', 'KINDS', 'DEFAULT_IN_ROOM', 'MAX_IN_ROOM', 'TIME_BUDGET_S']
