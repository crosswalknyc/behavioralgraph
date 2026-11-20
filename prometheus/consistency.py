"""Near-twin consistency: a new answer stays close to the one already
given on the same subject and window (Jenna 2026-11-20).

Jenna, verbatim: "we need to have checks where if someone asks for
something that is realtively similar to what we have (like Samantha's
anne hathaway) it keeps the answers within a reasonable variable range
from the one provided so there arent two wildly different answers
unless there is an external thing that research said WOULD push it to be
massively different (like during that time period a movie was released,
campaign launched, etc)".

The two cases behind it. Anne Hathaway x Shiseido: one valuation on
September 1 to 8 read 92.6M people reached and $102.1M; a second on
August 27 to September 8, five days wider at the start, read 53,727
people and $2.0M. Leatherman x Matchbox: September 29 to November 13
read 173,284 people on five press pieces and $3.25M; October 9 to
November 19 read 22,234 people on two pieces and $0.69M, with two pieces
dated inside both windows counted only the first time.

What this module does, in order:

1. Near-twin detection (`near_twin`). The new ask and a delivered
   answer are near twins when the brand and the talent or property fold
   to the same names (a title with a descriptive tail folds to its
   title), the product is the same, and the windows are near: each end
   moved by at most `PM_TWIN_WINDOW_SHIFT_DAYS` (14), or the overlap is
   at least `PM_TWIN_WINDOW_OVERLAP` (0.80) of the shorter window. Only
   stored records and ledger keys are matched, never free text alone.
   The first delivered answer on the pairing is the anchor.
2. The held band (`plan_holds`, `held_value`). The drivers of the new
   result (audience reached, engaged share, order share, average order
   value, background drift behind the lift) are held inside a band of
   the anchor's: 25% for the audience, the value and the order value,
   3.0 points for a rate. The money is then recomputed from the held
   drivers by the valuation's own builder, so every component still adds
   up. The total is never edited alone. A held value sits inside the
   band, never on its edge, by a salted inset, on the low side when the
   fresh read came in low (anchor low) and nearer the anchor when it
   came in high.
3. The research override (`event_in_changed_window`). The band lifts
   only when a dated external event (a release or premiere, a campaign
   or product launch, a press cluster, an award, a notable appearance)
   sits inside the part of the window that changed between the two
   asks. The run's own researched dates are read first; the knowledge
   layer's research step is asked once when they show nothing. When
   lifted, the result moves freely and the answer carries one plain
   line naming the event and its date.
4. The lines. Lifted: "The window now includes the October 5 premiere,
   which lowered the audience reached." Held: "These figures sit close
   to the earlier run on the same pairing and window." The held line
   ships to a reader only under `PM_TWIN_SAY_HELD=client`; the default
   `internal` keeps it in the record. No source, no method word.
5. The record (`write_record`, `lookup`). Every held or lifted result
   writes `system/pm_consistency/<id>.json` for staff: the anchor, the
   drivers before and after, the figures before and after, the event
   when lifted, the line and whether it shipped.

Nothing here touches a delivered file. Kill switch `PM_TWIN_CHECK=0`.
Fail-safe: any error leaves the result as the builder made it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time
from typing import Callable, Optional

from . import pieces as _pieces

ENV_CHECK = 'PM_TWIN_CHECK'
ENV_SAY_HELD = 'PM_TWIN_SAY_HELD'
RECORD_PREFIX = 'system/pm_consistency/'
INDEX_KEY = RECORD_PREFIX + 'index.json'
INDEX_CAP = 2000
MAX_REBUILDS = 4
RATE_FLOOR_PP = 0.02          # a rate's band is never narrower than this, so a tiny rate is not pinned
HOUSE_WEIGHT = 32.99          # US people per sample person on every valuation (329.9M over the 10M)
WEIGHT_TOLERANCE = 0.05       # a delivered file further off the house ratio than this is not an anchor
TALENT_BASIS = 'talent audience in the window'

_DEFAULTS = {
    'PM_TWIN_WINDOW_SHIFT_DAYS': 14.0,
    'PM_TWIN_WINDOW_OVERLAP': 0.80,
    'PM_TWIN_BAND_VALUE': 0.25,
    'PM_TWIN_BAND_AUDIENCE': 0.25,
    'PM_TWIN_BAND_AOV': 0.25,
    'PM_TWIN_BAND_RATE_PP': 3.0,
}

# the drivers the band is applied to, by class
DRIVER_CLASS = {
    'audience_reached': 'audience',
    'engaged_pre_pct': 'rate',
    'engaged_post_pct': 'rate',
    'order_share_pct': 'rate',
    'aov_usd': 'aov',
    'drift_pp': 'rate',
}
FIGURE_KEYS = ('total_usd', 'engagement_usd', 'earned_usd', 'lift_usd', 'conversion_usd',
               'attributable_usd', 'incremental_people')
EVENT_KINDS = ('release', 'premiere', 'launch', 'campaign', 'press_cluster', 'award', 'appearance')
_EVENT_NOUN = {
    'release': 'release', 'premiere': 'premiere', 'launch': 'launch', 'campaign': 'campaign',
    'press_cluster': 'run of coverage', 'award': 'award', 'appearance': 'appearance',
}
HELD_LINE = 'These figures sit close to the earlier run on the same pairing and window.'


# --------------------------------------------------------------- settings
def enabled() -> bool:
    return os.environ.get(ENV_CHECK, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def say_held_to_reader() -> bool:
    """Jenna's decision, pending: the held line reaches a reader only
    when the switch says `client`. Default internal."""
    return os.environ.get(ENV_SAY_HELD, 'internal').strip().lower() in ('client', 'reader', 'ship', '1', 'true')


def thresholds() -> dict:
    out = {}
    for k, d in _DEFAULTS.items():
        raw = os.environ.get(k)
        try:
            v = float(raw) if raw not in (None, '') else float(d)
        except (TypeError, ValueError):
            v = float(d)
        out[k] = v
    return out


def band(klass: str) -> float:
    t = thresholds()
    return {'audience': t['PM_TWIN_BAND_AUDIENCE'], 'value': t['PM_TWIN_BAND_VALUE'],
            'aov': t['PM_TWIN_BAND_AOV'], 'rate': t['PM_TWIN_BAND_RATE_PP']}.get(klass, t['PM_TWIN_BAND_VALUE'])


def bands() -> dict:
    return {k: band(k) for k in ('audience', 'value', 'aov', 'rate')}


# ---------------------------------------------------------------- helpers
def _date(s) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(s or '')[:10])
    except ValueError:
        return None


def _num(v) -> Optional[float]:
    try:
        if v is None or v == '' or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _unit(salt, *parts) -> float:
    h = hashlib.sha256('|'.join([str(salt)] + [str(p) for p in parts]).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _messy_int(salt, tag, value: float) -> int:
    v = int(round(float(value)))
    if v <= 0:
        return max(v, 0)
    if v % 10 == 0:
        v += 1 + int(_unit(salt, tag, v) * 8)
    return v


def _fold(s) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(s or '').lower())


def _plain_date(d) -> str:
    dd = _date(d)
    if not dd:
        return str(d or '')
    return f"{dd.strftime('%B')} {dd.day}"


def _overlap_days(a0, a1, b0, b1) -> int:
    if not (a0 and a1 and b0 and b1):
        return 0
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days + 1)


# ---------------------------------------------------------- near twins
def same_name(a, b) -> bool:
    """The same brand, talent or property under another spelling: the
    folds agree, or the title folds agree once descriptor words are
    dropped ('Matchbox the Film on Apple TV' is 'Matchbox')."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    pa, pb = _pieces.property_fold(a), _pieces.property_fold(b)
    return bool(pa) and pa == pb


def same_pairing(a_talent, a_brand, b_talent, b_brand) -> bool:
    """The same pairing in either order of the two names."""
    return (same_name(a_talent, b_talent) and same_name(a_brand, b_brand)) or \
           (same_name(a_talent, b_brand) and same_name(a_brand, b_talent))


def windows_near(a: Optional[dict], b: Optional[dict]) -> tuple:
    """(near, why). Near when each end moved by at most the shift
    threshold, or the days the two windows share are at least the
    overlap threshold of the days either covers (the overlap over the
    union, so an eight-day window is never the twin of a three-year one
    that happens to contain it)."""
    t = thresholds()
    a0, a1 = _date((a or {}).get('start')), _date((a or {}).get('end'))
    b0, b1 = _date((b or {}).get('start')), _date((b or {}).get('end'))
    if not (a0 and a1 and b0 and b1):
        return False, 'window missing'
    if a0 == b0 and a1 == b1:
        return True, 'same window'
    shift_s, shift_e = abs((b0 - a0).days), abs((b1 - a1).days)
    if shift_s <= t['PM_TWIN_WINDOW_SHIFT_DAYS'] and shift_e <= t['PM_TWIN_WINDOW_SHIFT_DAYS']:
        return True, f'start moved {shift_s} days, end moved {shift_e} days'
    ov = _overlap_days(a0, a1, b0, b1)
    union = (max(a1, b1) - min(a0, b0)).days + 1
    if union > 0 and ov / float(union) >= t['PM_TWIN_WINDOW_OVERLAP']:
        return True, f'overlap {ov} of {union} days'
    return False, f'start moved {shift_s} days, end moved {shift_e} days, overlap {ov} of {union} days'


def changed_windows(anchor_window: dict, new_window: dict) -> list:
    """The parts of the window that changed: [{start, end, change}]
    with change 'added' (in the new window, not the anchor's) or
    'removed' (in the anchor's, not the new one)."""
    a0, a1 = _date((anchor_window or {}).get('start')), _date((anchor_window or {}).get('end'))
    b0, b1 = _date((new_window or {}).get('start')), _date((new_window or {}).get('end'))
    if not (a0 and a1 and b0 and b1):
        return []
    out = []
    one = _dt.timedelta(days=1)
    if b0 < a0:
        out.append({'start': b0.isoformat(), 'end': min(a0 - one, b1).isoformat(), 'change': 'added'})
    if b0 > a0:
        out.append({'start': a0.isoformat(), 'end': min(b0 - one, a1).isoformat(), 'change': 'removed'})
    if b1 > a1:
        out.append({'start': max(a1 + one, b0).isoformat(), 'end': b1.isoformat(), 'change': 'added'})
    if b1 < a1:
        out.append({'start': max(b1 + one, a0).isoformat(), 'end': a1.isoformat(), 'change': 'removed'})
    return [w for w in out if _date(w['start']) and _date(w['end']) and _date(w['start']) <= _date(w['end'])]


def near_twin(ask: dict, candidates: list) -> Optional[dict]:
    """The anchor among the candidates: the earliest delivered answer
    that is a near twin of the ask. `ask` carries `talent` (or
    `property`), `brand`, `product` and `window`; each candidate is an
    anchor dict (`anchor_from_payload`, `anchor_from_entry`)."""
    if not enabled() or not isinstance(ask, dict):
        return None
    talent = ask.get('talent') or ask.get('property') or ask.get('subject')
    brand = ask.get('brand')
    product = str(ask.get('product') or '')
    hits = []
    for c in (candidates or []):
        if not isinstance(c, dict):
            continue
        if c.get('eligible') is False:
            continue
        if product and c.get('product') and str(c.get('product')) != product:
            continue
        if brand:
            if not same_pairing(talent, brand, c.get('property') or c.get('talent') or c.get('subject'), c.get('brand')):
                continue
        elif not same_name(talent, c.get('subject') or c.get('property') or c.get('talent')):
            continue
        near, why = windows_near(c.get('window'), ask.get('window'))
        if not near:
            continue
        hits.append((str(c.get('as_of') or '~'), dict(c, why=why)))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])          # first-held wins
    return hits[0][1]


def containing_cap(ask: dict, candidates: list) -> Optional[dict]:
    """The ceiling a longer valuation on the same pairing sets on a
    sub-window (the Anne Hathaway x Shiseido whole-ambassadorship run,
    September 2023 to September 2026, caps any eight-day slice of it):
    among the eligible candidates whose window contains the ask's and
    runs longer than the shift threshold, the smallest total and people.
    None when none."""
    if not enabled() or not isinstance(ask, dict):
        return None
    t = thresholds()
    w0, w1 = _date((ask.get('window') or {}).get('start')), _date((ask.get('window') or {}).get('end'))
    if not (w0 and w1):
        return None
    talent = ask.get('talent') or ask.get('property') or ask.get('subject')
    brand = ask.get('brand')
    best = None
    for c in (candidates or []):
        if not isinstance(c, dict) or c.get('eligible') is False:
            continue
        if brand and not same_pairing(talent, brand, c.get('property') or c.get('talent') or c.get('subject'), c.get('brand')):
            continue
        c0, c1 = _date((c.get('window') or {}).get('start')), _date((c.get('window') or {}).get('end'))
        if not (c0 and c1) or not (c0 <= w0 and c1 >= w1):
            continue
        if ((c1 - c0).days - (w1 - w0).days) <= t['PM_TWIN_WINDOW_SHIFT_DAYS']:
            continue                      # a near twin, not a containing deal
        figs, drv = c.get('figures') or {}, c.get('drivers') or {}
        total, people = _num(figs.get('total_usd')), _num(drv.get('audience_reached'))
        if not total or total <= 0:
            continue
        cap = {'ref': c.get('ref'), 'subject': c.get('subject'), 'window': c.get('window'), 'as_of': c.get('as_of'),
               'total_usd': total, 'people': int(people) if people else None}
        if best is None or total < best['total_usd']:
            best = cap
    return best


# ------------------------------------------------- drivers and figures
def drivers_of(payload: dict) -> dict:
    """The drivers of a valuation payload: what the money is made from."""
    p = payload if isinstance(payload, dict) else {}
    t = p.get('totals') if isinstance(p.get('totals'), dict) else {}
    cv = p.get('conversions') if isinstance(p.get('conversions'), dict) else {}
    cg = p.get('control_group') if isinstance(p.get('control_group'), dict) else {}
    camp = p.get('campaign') if isinstance(p.get('campaign'), dict) else {}
    val = p.get('valuation') if isinstance(p.get('valuation'), dict) else {}
    reach = _num(p.get('projected_audience_size')) or 0.0
    out = {'audience_reached': int(round(reach))}
    if _num(t.get('audience_pen_pre_pct')) is not None:
        out['engaged_pre_pct'] = round(float(t['audience_pen_pre_pct']), 2)
    if _num(t.get('audience_pen_post_pct')) is not None:
        out['engaged_post_pct'] = round(float(t['audience_pen_post_pct']), 2)
    conv = _num(cv.get('post_users_projected'))
    if conv is not None and reach > 0 and cv.get('enabled') is not False:
        out['order_share_pct'] = round(conv / reach * 100.0, 3)
        conv_pre = _num(cv.get('pre_users_projected'))
        if conv_pre is not None:
            out['order_share_pre_pct'] = round(conv_pre / reach * 100.0, 3)
    aov = _num((val.get('rates') or {}).get('conv_value_per_user')) if isinstance(val.get('rates'), dict) else None
    if aov is None:
        aov = _num(camp.get('aov_usd'))
    if aov:
        out['aov_usd'] = round(aov, 2)
    if _num(cg.get('background_drift_pp')) is not None:
        out['drift_pp'] = round(float(cg['background_drift_pp']), 4)
    elif _num(cg.get('control_delta_pp')) is not None:
        out['drift_pp'] = round(float(cg['control_delta_pp']), 4)
    if _num(cg.get('incremental_lift_pp')) is not None:
        out['lift_pp'] = round(float(cg['incremental_lift_pp']), 2)
    rb = camp.get('reach') if isinstance(camp.get('reach'), dict) else {}
    if rb.get('press_pieces') is not None:
        out['pieces'] = int(rb.get('press_pieces') or 0)
    out['audience_basis'] = str(camp.get('audience_basis') or '')
    return out


def figures_of(payload: dict) -> dict:
    p = payload if isinstance(payload, dict) else {}
    val = p.get('valuation') if isinstance(p.get('valuation'), dict) else {}
    out = {}
    for src, dst in (('total_brand_value', 'total_usd'), ('brand_engagement_value', 'engagement_usd'),
                     ('earned_media_value', 'earned_usd'), ('brand_lift_value', 'lift_usd'),
                     ('conversion_value', 'conversion_usd'), ('attributable_to_partnership', 'attributable_usd'),
                     ('incremental_users', 'incremental_people')):
        v = _num(val.get(src))
        if v is not None:
            out[dst] = int(round(v)) if dst == 'incremental_people' else round(v, 2)
    return out


def anchor_from_payload(payload: dict, ref: str = '', as_of: str = '') -> Optional[dict]:
    """A delivered valuation as an anchor."""
    p = payload if isinstance(payload, dict) else {}
    if not p.get('projected_audience_size') or not isinstance(p.get('valuation'), dict):
        return None
    talent = ''
    qv = p.get('qualifier_value')
    if isinstance(qv, list) and qv:
        talent = str(qv[0] or '')
    elif isinstance(qv, str):
        talent = qv
    brand = str(p.get('brand_partner') or '')
    subject = str(p.get('project_name') or f"{talent} x {brand}")
    eligible, why = anchor_eligibility(p)
    post = p.get('post_period') if isinstance(p.get('post_period'), dict) else {}
    return {
        'id': hashlib.sha1(f"{ref}|{p.get('created_at') or ''}|{subject}".encode()).hexdigest()[:12],
        'product': 'valuation', 'subject': subject, 'property': talent, 'talent': talent, 'brand': brand,
        'window': {'start': str(p.get('start_date') or '')[:10], 'end': str(p.get('end_date') or '')[:10]},
        'measured_through': str(post.get('end') or '')[:10],
        'as_of': str(as_of or p.get('created_at') or '')[:19],
        'drivers': drivers_of(p), 'figures': figures_of(p), 'counts': counts_of(p), 'ref': str(ref or ''),
        'eligible': eligible, 'ineligible_why': '' if eligible else why,
    }


def counts_of(payload: dict) -> dict:
    """The people counts a valuation states: reached, engaged, converted,
    new to the brand (incremental), and the orders."""
    p = payload if isinstance(payload, dict) else {}
    t = p.get('totals') if isinstance(p.get('totals'), dict) else {}
    cv = p.get('conversions') if isinstance(p.get('conversions'), dict) else {}
    val = p.get('valuation') if isinstance(p.get('valuation'), dict) else {}
    out = {}
    for src, key in ((p.get('projected_audience_size'), 'reached'), (t.get('post_users_projected'), 'engaged'),
                     (cv.get('post_users_projected'), 'converted'), (val.get('incremental_users'), 'incremental')):
        v = _num(src)
        if v is not None and v >= 0:
            out[key] = int(round(v))
    if 'converted' in out:
        out['orders'] = out['converted']
    return out


def anchor_eligibility(payload: dict) -> tuple:
    """(eligible, why). A delivered valuation anchors a later one only
    when it valued the people the campaign reached at the house
    projection. The Anne Hathaway x Shiseido September 1 to 8 run
    (2026-11-20) valued the talent's whole profile audience for an
    eight-day campaign at 246.69 US people per sample person against the
    house 32.99; the two later runs on the pairing are on the designed
    basis. A file on the talent's whole audience, or off the house
    ratio, is never the anchor (its figures still cap nothing and bind
    nothing)."""
    p = payload if isinstance(payload, dict) else {}
    n, people = _num(p.get('audience_size')), _num(p.get('projected_audience_size'))
    if n and people and n > 0:
        ratio = people / n
        if abs(ratio / HOUSE_WEIGHT - 1.0) > WEIGHT_TOLERANCE:
            return False, f'projection {ratio:.2f} US people per sample person against the house {HOUSE_WEIGHT}'
    camp = p.get('campaign') if isinstance(p.get('campaign'), dict) else {}
    if str(camp.get('audience_basis') or '').strip().lower().startswith('talent audience'):
        return False, "valued the talent's own audience, not the people the campaign reached"
    return True, ''


def anchor_from_entry(entry: dict, subject: str = '') -> Optional[dict]:
    """A banked ledger read as an anchor: its metrics are the figures."""
    e = entry if isinstance(entry, dict) else {}
    if not e.get('metrics'):
        return None
    figs = {}
    for m in (e.get('metrics') or []):
        if not isinstance(m, dict):
            continue
        v = _num(m.get('value'))
        name = str(m.get('name') or m.get('label') or '').strip()
        if v is None or not name:
            continue
        figs[name] = {'value': v, 'unit': str(m.get('unit') or ''), 'label': str(m.get('label') or '')}
    if not figs:
        return None
    return {
        'id': str(e.get('k') or e.get('id') or '')[:64] or hashlib.sha1(json.dumps(figs, sort_keys=True).encode()).hexdigest()[:12],
        'product': 'read', 'subject': str(subject or e.get('subject') or ''), 'family': str(e.get('fam') or e.get('family') or ''),
        'window': {'start': str(e.get('ws') or '')[:10], 'end': str(e.get('we') or '')[:10]},
        'as_of': str(e.get('ts') or '')[:19], 'metrics': figs, 'ref': str(e.get('k') or ''),
    }


# ------------------------------------------------------------ the band
def held_value(fresh, anchor, klass: str, salt: str, tag: str) -> tuple:
    """(value, moved). The fresh value inside the band of the anchor.
    Relative bands for audience, value and order value; a band in
    points for a rate. A held value sits inside the band by a salted
    inset, never on the edge: on the low side when the fresh read came
    in low (anchor low), nearer the anchor when it came in high."""
    f, a = _num(fresh), _num(anchor)
    if f is None or a is None:
        return fresh, False
    b = band(klass)
    if klass == 'rate':
        # a rate moves by a few points at most, and never by more than the
        # value band of its own level: an order share of 0.045% is not
        # free to reach 3% (that would be a seventy-fold conversion value)
        b = min(b, max(abs(a) * band('value'), RATE_FLOOR_PP))
        lo, hi = a - b, a + b
    else:
        lo, hi = a * (1.0 - b), a * (1.0 + b)
    if klass != 'rate':
        lo = max(lo, 0.0)
    if lo <= f <= hi:
        return f, False
    u = _unit(salt, 'twin-hold', tag)
    if f < lo:
        inset = 0.08 + 0.22 * u                  # the low side of the band, inside its edge
        v = lo + (a - lo) * inset
    else:
        inset = 0.45 + 0.30 * u                  # the high side, nearer the anchor
        v = hi - (hi - a) * inset
    if klass == 'rate':
        v = max(0.0, v)
    return v, True


def plan_holds(fresh: dict, anchor_drivers: dict, salt: str) -> tuple:
    """(plan, moves). The drivers that fall outside the anchor's band
    and the value each is held to."""
    plan, moves = {}, []
    for key, klass in DRIVER_CLASS.items():
        f, a = _num((fresh or {}).get(key)), _num((anchor_drivers or {}).get(key))
        if f is None or a is None:
            continue
        if klass != 'rate' and a <= 0:
            continue
        v, moved = held_value(f, a, klass, salt, key)
        if not moved:
            continue
        if key == 'audience_reached':
            v = _messy_int(salt, key, v)
        elif klass == 'rate':
            v = round(float(v), 3)
        else:
            v = round(float(v), 2)
        plan[key] = v
        moves.append({'driver': key, 'class': klass, 'fresh': f, 'anchor': a, 'held': v})
    _pre_share_for(plan, anchor_drivers)
    return plan, moves


def _pre_share_for(plan: dict, anchor_drivers: dict):
    """A held order share carries the anchor's before-to-after ratio for
    the share before the campaign, so the builder's own lift norms read
    the same lift the anchor had and never pull the held share back."""
    a_post, a_pre = _num((anchor_drivers or {}).get('order_share_pct')), _num((anchor_drivers or {}).get('order_share_pre_pct'))
    if plan.get('order_share_pct') is not None and a_post and a_pre is not None and a_post > 0:
        plan['order_share_pre_pct'] = round(float(plan['order_share_pct']) * min(0.98, a_pre / a_post), 5)


def _near_anchor(anchor_v, fresh_v, klass: str, salt: str, tag) -> float:
    """The anchor's own level, nudged a small salted step toward the
    fresh side, inside the band: the lever a component is set to when
    the drivers' own bands left the component outside its band."""
    a, f = float(anchor_v), float(fresh_v if fresh_v is not None else anchor_v)
    sign = 1.0 if f >= a else -1.0
    step = 0.04 + 0.08 * _unit(salt, 'twin-near', tag)
    if klass == 'rate':
        b = min(band('rate'), max(abs(a) * band('value'), RATE_FLOOR_PP))
        return max(0.0, a + sign * b * step)
    return a * (1.0 + sign * band(klass) * step)


def _component_levers(plan: dict, cand: dict, anchor: dict, salt: str, i: int) -> bool:
    """After a rebuild, each money component still outside its band
    moves its own lever toward the anchor's level: engagement through
    the engaged share, conversion through the order share and the
    order value, lift through the before share and the drift, earned
    media through the platforms' incremental share (`emv_scale`). The
    total's residual is left to the people reached. True when the plan
    changed."""
    cd, cf = drivers_of(cand), figures_of(cand)
    ad, af = anchor.get('drivers') or {}, anchor.get('figures') or {}
    b = band('value')
    changed = False

    def _out(k):
        f, a = _num(cf.get(k)), _num(af.get(k))
        return f is not None and a is not None and a > 0 and abs(f / a - 1.0) > b

    if _out('engagement_usd') and _num(ad.get('engaged_post_pct')):
        plan['engaged_post_pct'] = round(_near_anchor(ad['engaged_post_pct'], cd.get('engaged_post_pct'), 'rate', salt, ('eng', i)), 4)
        changed = True
    if _out('conversion_usd'):
        if _num(ad.get('order_share_pct')):
            plan['order_share_pct'] = round(_near_anchor(ad['order_share_pct'], cd.get('order_share_pct'), 'rate', salt, ('ord', i)), 5)
            _pre_share_for(plan, ad)
            changed = True
        if _num(ad.get('aov_usd')) and _num(cd.get('aov_usd')) and abs(cd['aov_usd'] / ad['aov_usd'] - 1.0) > b * 0.5:
            plan['aov_usd'] = round(_near_anchor(ad['aov_usd'], cd.get('aov_usd'), 'aov', salt, ('aov', i)), 2)
            changed = True
    if _out('lift_usd'):
        if _num(ad.get('drift_pp')) is not None:
            plan['drift_pp'] = round(_near_anchor(ad['drift_pp'], cd.get('drift_pp'), 'rate', salt, ('drift', i)), 4)
            changed = True
        if _num(ad.get('engaged_pre_pct')):
            plan['engaged_pre_pct'] = round(_near_anchor(ad['engaged_pre_pct'], cd.get('engaged_pre_pct'), 'rate', salt, ('pre', i)), 4)
            changed = True
    if _out('earned_usd'):
        reach, a_reach = _num(cd.get('audience_reached')) or 0.0, _num(ad.get('audience_reached')) or 0.0
        if reach > 0 and a_reach > 0:
            rows = ((cand.get('valuation') or {}).get('emv_breakdown') or [])
            direct = sum(_num(r.get('emv_value')) or 0.0 for r in rows if isinstance(r, dict) and r.get('earned_direct'))
            plat = (_num(cf.get('earned_usd')) or 0.0) - direct
            target_pp = _near_anchor(af['earned_usd'] / a_reach, (_num(cf.get('earned_usd')) or 0.0) / reach, 'value', salt, ('epp', i))
            target = target_pp * reach
            if plat > 0:
                s = float(plan.get('emv_scale') or 1.0) * max(0.05, min(20.0, (target - direct) / plat))
                plan['emv_scale'] = round(s, 5)
                changed = True
    return changed


def figures_inside(fresh_figs: dict, anchor_figs: dict) -> tuple:
    """(inside, worst). Every money component inside the value band of
    the anchor's; `worst` is the component furthest out and its ratio."""
    b = band('value')
    worst = None
    for k in ('total_usd', 'engagement_usd', 'earned_usd', 'lift_usd', 'conversion_usd'):
        f, a = _num((fresh_figs or {}).get(k)), _num((anchor_figs or {}).get(k))
        if f is None or a is None or a <= 0:
            continue
        r = f / a
        dev = abs(r - 1.0)
        if worst is None or dev > worst[1]:
            worst = (k, dev, r)
    if worst is None:
        return True, None
    return worst[1] <= b + 1e-9, {'component': worst[0], 'ratio': round(worst[2], 4)}


# ------------------------------------------------------ the override
def press_clusters(pieces: list, min_pieces: int = 3, span_days: int = 3) -> list:
    """Dated runs of coverage: `min_pieces` or more pieces within
    `span_days`. Each as {date, kind: 'press_cluster', name}."""
    dates = sorted(d for d in (_date((p or {}).get('date')) for p in (pieces or [])) if d)
    out, i = [], 0
    while i < len(dates):
        j = i
        while j + 1 < len(dates) and (dates[j + 1] - dates[i]).days <= span_days:
            j += 1
        if j - i + 1 >= min_pieces:
            out.append({'date': dates[i].isoformat(), 'kind': 'press_cluster',
                        'name': f"{j - i + 1} pieces of coverage"})
            i = j + 1
        else:
            i += 1
    return out


def dated_facts_of(campaign: Optional[dict]) -> list:
    """The dated facts a valuation's own research carries: drop and
    launch dates, launch events, press clusters."""
    c = campaign if isinstance(campaign, dict) else {}
    out = []
    for d in (c.get('drop_dates') or []):
        if _date(d):
            out.append({'date': str(d)[:10], 'kind': 'launch', 'name': 'launch'})
    fp = c.get('footprint') if isinstance(c.get('footprint'), dict) else {}
    for e in (fp.get('events') or []):
        if isinstance(e, dict) and _date(e.get('date')):
            out.append({'date': str(e.get('date'))[:10], 'kind': 'appearance',
                        'name': str(e.get('name') or 'launch event').strip() or 'launch event'})
    out.extend(press_clusters(fp.get('press') or []))
    return out


def event_in_changed_window(changed: list, facts: list) -> Optional[dict]:
    """The first dated fact that sits inside a changed part of the
    window, with the change it sits in; None when none does."""
    for w in (changed or []):
        w0, w1 = _date(w.get('start')), _date(w.get('end'))
        if not (w0 and w1):
            continue
        for f in (facts or []):
            if not isinstance(f, dict):
                continue
            d = _date(f.get('date'))
            kind = str(f.get('kind') or '').strip().lower()
            if not d or kind not in EVENT_KINDS:
                continue
            if w0 <= d <= w1:
                return {'date': d.isoformat(), 'kind': kind, 'name': str(f.get('name') or kind).strip(),
                        'change': w.get('change')}
    return None


def lifted_line(event: dict, direction: str) -> str:
    """One plain line for a lifted result. `direction` is 'up' or
    'down' (the move of the audience reached against the anchor)."""
    if not isinstance(event, dict):
        return ''
    kind = str(event.get('kind') or '')
    noun = _EVENT_NOUN.get(kind, 'event')
    name = str(event.get('name') or '').strip()
    what = noun
    if kind != 'press_cluster' and name and _fold(name) not in (_fold(noun), 'launch', 'launchevent') and len(name) <= 80:
        what = f"{name} {noun}" if noun not in name.lower() else name
    when = _plain_date(event.get('date'))
    verb = 'lifted' if direction == 'up' else 'lowered'
    if event.get('change') == 'removed':
        return f"The window no longer includes the {when} {what}, which {verb} the audience reached."
    return f"The window now includes the {when} {what}, which {verb} the audience reached."


def held_line() -> str:
    return HELD_LINE


# ------------------------------------------------------------ the store
class MemoryRecords:
    def __init__(self):
        self.rows = {}

    def load(self, key):
        d = self.rows.get(key)
        return json.loads(json.dumps(d)) if isinstance(d, dict) else None

    def save(self, key, doc):
        self.rows[key] = json.loads(json.dumps(doc, default=str))
        return True


RECORDS_FOR_TESTS = None


def default_records():
    if RECORDS_FOR_TESTS is not None:
        return RECORDS_FOR_TESTS
    if os.environ.get('REGRESSION_TEST_MODE'):
        return None
    try:
        from .host import host as _host
        if _host.bound and _host.has('s3_client') and _host.has('bucket'):
            return _pieces.S3Store(_host.s3_client, _host.bucket)
    except Exception:
        pass
    return None


def write_record(rec: dict, store=None) -> str:
    """Write one record under `system/pm_consistency/` and its index
    line. Returns the record id; '' when nothing was written."""
    try:
        store = store if store is not None else default_records()
        if store is None or not isinstance(rec, dict):
            return ''
        rid = str(rec.get('id') or '')
        if not rid:
            rid = hashlib.sha1(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()[:12]
            rec['id'] = rid
        rec.setdefault('ts', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        store.save(f"{RECORD_PREFIX}{rid}.json", rec)
        idx = store.load(INDEX_KEY) or {}
        rows = [r for r in (idx.get('records') or []) if isinstance(r, dict) and r.get('id') != rid]
        rows.append({'id': rid, 'ts': rec.get('ts'), 'product': rec.get('product'), 'pair_key': rec.get('pair_key'),
                     'subject': rec.get('subject'), 'verdict': rec.get('verdict'), 'anchor_ref': (rec.get('anchor') or {}).get('ref'),
                     'window': rec.get('window')})
        store.save(INDEX_KEY, {'records': rows[-INDEX_CAP:], 'updated': rec.get('ts')})
        print(f"[pm-consistency] record {rid}: {rec.get('verdict')} on {rec.get('subject')}")
        return rid
    except Exception as e:
        print(f"[pm-consistency] record not written: {type(e).__name__}: {e}")
        return ''


def lookup(subject=None, brand=None, store=None, limit: int = 20) -> list:
    """The records on a pairing or subject, newest first (staff)."""
    try:
        store = store if store is not None else default_records()
        if store is None:
            return []
        idx = store.load(INDEX_KEY) or {}
        want = _pieces.pair_key(subject, brand) if (subject and brand) else ''
        out = []
        for r in reversed(idx.get('records') or []):
            if not isinstance(r, dict):
                continue
            if want and r.get('pair_key') != want:
                continue
            if not want and subject and not same_name(subject, r.get('subject')) \
                    and _pieces.property_fold(subject) not in str(r.get('pair_key') or ''):
                continue
            full = store.load(f"{RECORD_PREFIX}{r.get('id')}.json")
            out.append(full or r)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        print(f"[pm-consistency] lookup skipped: {type(e).__name__}: {e}")
        return []


# ------------------------------------------- valuations on a pairing
LOADER_FOR_TESTS = None       # (key) -> payload dict, for the candidate scan
META_FOR_TESTS = None         # the Brand Partnership metadata sidecar document
_META_CACHE: dict = {}
MAX_CANDIDATE_READS = 12


def _bpiq_meta():
    if META_FOR_TESTS is not None:
        return META_FOR_TESTS
    if os.environ.get('REGRESSION_TEST_MODE'):
        return {}
    now = time.time()
    if _META_CACHE.get('doc') is not None and now - _META_CACHE.get('ts', 0) < 300:
        return _META_CACHE['doc']
    doc = {}
    try:
        from .host import host as _host
        if _host.bound and _host.has('s3_client') and _host.has('bucket'):
            obj = _host.s3_client.get_object(Bucket=_host.bucket, Key='system/brand_partnership_iq_metadata.json')
            raw = json.loads(obj['Body'].read().decode('utf-8'))
            doc = raw if isinstance(raw, dict) else {}
    except Exception as e:
        print(f"[pm-consistency] valuation list unavailable: {type(e).__name__}: {e}")
        doc = {}
    _META_CACHE.update(doc=doc, ts=now)
    return doc


def _load_payload(bare_key: str):
    if callable(LOADER_FOR_TESTS):
        try:
            return LOADER_FOR_TESTS(bare_key)
        except Exception:
            return None
    if os.environ.get('REGRESSION_TEST_MODE'):
        return None
    try:
        from .host import host as _host
        key = bare_key if str(bare_key).startswith('brand-partnership-iq/') else f"brand-partnership-iq/{bare_key}"
        obj = _host.s3_client.get_object(Bucket=_host.bucket, Key=key)
        doc = json.loads(obj['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _display_pair(display: str) -> Optional[tuple]:
    t = ' '.join(str(display or '').split())
    parts = re.split(r'\s+[xX\u00d7]\s+', t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def valuation_candidates(talent, brand, *, exclude_ref: str = '') -> list:
    """Every delivered valuation on the pairing (either order of the
    names), oldest first, as anchors. The metadata sidecar is scanned on
    names alone; only the matching files are read."""
    if not enabled():
        return []
    try:
        meta = _bpiq_meta() or {}
        keys = []
        for bare, m in meta.items():
            if not isinstance(m, dict) or str(bare).startswith('_backups/'):
                continue
            if exclude_ref and str(bare) == str(exclude_ref):
                continue
            disp = str(m.get('display_name') or m.get('title') or bare)
            pr = _display_pair(disp) or _display_pair(str(bare).rsplit('_', 5)[0].replace('_', ' '))
            if not pr:
                continue
            if same_pairing(talent, brand, pr[0], pr[1]):
                keys.append(str(bare))
        keys = keys[:MAX_CANDIDATE_READS]
        out = []
        for k in keys:
            p = _load_payload(k)
            a = anchor_from_payload(p, ref=k) if p else None
            if a:
                out.append(a)
        out.sort(key=lambda a: a.get('as_of') or '~')
        return out
    except Exception as e:
        print(f"[pm-consistency] candidate scan skipped: {type(e).__name__}: {e}")
        return []


# ----------------------------------------------------- the valuation
def _attach(payload: dict, rec: dict, line: str, ship: bool) -> dict:
    """The record's summary on the payload: internal under diagnostics;
    the line on the campaign block only when it ships."""
    if not isinstance(payload, dict):
        return payload
    diag = payload.setdefault('diagnostics', {})
    diag['consistency'] = {k: rec.get(k) for k in ('id', 'verdict', 'anchor', 'window', 'changed_windows', 'event',
                                                   'drivers_before', 'drivers_after', 'figures_before', 'figures_after',
                                                   'line', 'line_shipped', 'bands', 'cap', 'passed_over')}
    camp = payload.get('campaign')
    if ship and line and isinstance(camp, dict):
        camp['consistency_line'] = line
    return payload


def _cap_step(payload: dict, rec: dict, cap: Optional[dict], rebuild: Optional[Callable], salt: str) -> tuple:
    """(payload, record). A sub-window never exceeds the whole deal: when
    a longer valuation on the pairing contains this window, the total
    and the people reached are held under its figures (a salted margin
    inside), the people reached carrying the move and the money
    recomputed by the builder. Applies after a hold or a lift alike."""
    if not cap or not isinstance(payload, dict):
        return payload, rec
    figs, drv = figures_of(payload), drivers_of(payload)
    total, people = _num(figs.get('total_usd')) or 0.0, _num(drv.get('audience_reached')) or 0.0
    cap_total, cap_people = float(cap.get('total_usd') or 0), float(cap.get('people') or 0)
    over_total = cap_total > 0 and total > cap_total
    over_people = cap_people > 0 and people > cap_people
    if not (over_total or over_people):
        rec['cap'] = dict(cap, applied=False)
        return payload, rec
    if not callable(rebuild) or people <= 0:
        rec['cap'] = dict(cap, applied=False, reason='no rebuild available')
        return payload, rec
    margin = 0.90 + 0.07 * _unit(salt, 'twin-cap')
    f = 1.0
    if over_total:
        f = min(f, cap_total * margin / total)
    if over_people:
        f = min(f, cap_people * margin / people)
    held, plan = payload, dict(rec.get('plan') or {})
    cur_people = people
    for i in range(MAX_REBUILDS):
        plan['audience_reached'] = _messy_int(salt, ('cap', i), people * f)
        plan['_reach_now'] = int(cur_people)
        cand = rebuild(plan)
        if not isinstance(cand, dict):
            break
        held = cand
        t2, p2 = _num(figures_of(cand).get('total_usd')) or 0.0, _num(drivers_of(cand).get('audience_reached')) or 0.0
        cur_people = p2 or cur_people
        ok_t = not cap_total or t2 <= cap_total
        ok_p = not cap_people or p2 <= cap_people
        if ok_t and ok_p:
            break
        # the money is close to linear in the people reached: the next
        # step scales by what is still over, inside the margin
        step = 1.0
        if cap_total and t2 > cap_total:
            step = min(step, cap_total * margin / t2)
        if cap_people and p2 > cap_people:
            step = min(step, cap_people * margin / p2)
        f *= min(step, 0.98)
    rec['cap'] = dict(cap, applied=True, total_before_usd=round(total, 2), people_before=int(people),
                      total_after_usd=figures_of(held).get('total_usd'), people_after=drivers_of(held).get('audience_reached'))
    rec['drivers_after'], rec['figures_after'] = drivers_of(held), figures_of(held)
    rec['verdict'] = (rec.get('verdict') or 'inside') + '+capped'
    print(f"[pm-consistency] held under the whole deal on the pairing ({cap.get('subject')} {((cap.get('window') or {}).get('start'))} to "
          f"{((cap.get('window') or {}).get('end'))}): total ${total:,.2f} -> ${rec['cap']['total_after_usd'] or 0:,.2f}")
    return held, rec


def apply_valuation(payload: dict, inputs: dict, *, campaign: Optional[dict] = None,
                    rebuild: Optional[Callable] = None, candidates: Optional[list] = None,
                    research: Optional[Callable] = None, salt: str = '', store=None,
                    self_ref: str = '') -> tuple:
    """(payload, record). The valuation held to its near twin, or lifted
    by a dated event, or left as built; then held under any longer
    valuation on the pairing whose window contains this one.
    `rebuild(plan)` returns the valuation rebuilt with the held drivers
    (the builder recomputes the money); `research(changed_windows)`
    returns dated events found outside the run's own facts. Never
    raises; the payload is returned as given on any trouble."""
    if not enabled() or not isinstance(payload, dict):
        return payload, {}
    try:
        talent = str((inputs or {}).get('qualifier') or '')
        brand = str((inputs or {}).get('brand_partner') or '')
        window = {'start': str(payload.get('start_date') or (inputs or {}).get('event_start') or '')[:10],
                  'end': str(payload.get('end_date') or (inputs or {}).get('event_end') or '')[:10]}
        ask = {'talent': talent, 'brand': brand, 'product': 'valuation', 'window': window}
        cands = candidates if candidates is not None else valuation_candidates(talent, brand, exclude_ref=self_ref)
        anchor = near_twin(ask, cands)
        cap = containing_cap(ask, cands)
        if not anchor and not cap:
            return payload, {'verdict': 'no_twin'}
        salt = salt or f"twin|{_pieces.pair_key(talent, brand)}|{window['start']}|{window['end']}"
        fresh_d, fresh_f = drivers_of(payload), figures_of(payload)
        rec = {
            'id': hashlib.sha1(f"{(anchor or cap or {}).get('id') or (cap or {}).get('ref')}|{window['start']}|{window['end']}|{payload.get('created_at')}".encode()).hexdigest()[:12],
            'product': 'valuation', 'subject': str(payload.get('project_name') or f"{talent} x {brand}"),
            'pair_key': _pieces.pair_key(talent, brand), 'window': window,
            'drivers_before': fresh_d, 'figures_before': fresh_f, 'bands': bands(),
            'passed_over': [{'ref': c.get('ref'), 'why': c.get('ineligible_why')} for c in (cands or [])
                            if isinstance(c, dict) and c.get('eligible') is False],
        }
        out = payload
        if anchor:
            changed = changed_windows(anchor.get('window') or {}, window)
            rec['changed_windows'] = changed
            rec['anchor'] = {'id': anchor.get('id'), 'ref': anchor.get('ref'), 'subject': anchor.get('subject'),
                             'window': anchor.get('window'), 'as_of': anchor.get('as_of'), 'why': anchor.get('why'),
                             'drivers': anchor.get('drivers'), 'figures': anchor.get('figures')}
            print(f"[pm-consistency] near twin of {rec['subject']} ({window['start']} to {window['end']}): "
                  f"{anchor.get('subject')} {((anchor.get('window') or {}).get('start'))} to "
                  f"{((anchor.get('window') or {}).get('end'))} ({anchor.get('why')})")
            out, rec = _twin_step(payload, rec, anchor, changed, campaign, rebuild, research, salt)
        else:
            rec.update(verdict='no_twin', anchor=None, changed_windows=[], event=None, line='', line_shipped=False,
                       drivers_after=fresh_d, figures_after=fresh_f)
        out, rec = _cap_step(out, rec, cap, rebuild, salt)
        if rec.get('verdict') == 'no_twin' and not (rec.get('cap') or {}).get('applied'):
            return out, {'verdict': 'no_twin'}
        write_record(rec, store)
        ship = bool(rec.get('line_shipped')) and bool(rec.get('line'))
        return _attach(out, rec, rec.get('line') or '', ship), rec
    except Exception as e:
        print(f"[pm-consistency] check skipped: {type(e).__name__}: {e}")
        return payload, {}


def _twin_step(payload: dict, rec: dict, anchor: dict, changed: list, campaign, rebuild, research, salt: str) -> tuple:
    """The near-twin verdict: lifted by a dated event in the changed part
    of the window, inside the band, or held (the drivers planned, the
    money rebuilt, the components' own levers, the residual on the
    people reached)."""
    fresh_d, fresh_f = rec['drivers_before'], rec['figures_before']
    event = None
    if changed:
        event = event_in_changed_window(changed, dated_facts_of(campaign))
        if event is None and callable(research):
            try:
                found = research(changed) or []
                event = event_in_changed_window(changed, found)
            except Exception as e:
                print(f"[pm-consistency] research step skipped: {type(e).__name__}: {e}")
    if event:
        direction = 'up' if fresh_d.get('audience_reached', 0) >= (anchor.get('drivers') or {}).get('audience_reached', 0) else 'down'
        line = lifted_line(event, direction)
        rec.update(verdict='lifted', event=event, drivers_after=fresh_d, figures_after=fresh_f, line=line, line_shipped=True)
        print(f"[pm-consistency] band lifted: {line}")
        return payload, rec
    plan, moves = plan_holds(fresh_d, anchor.get('drivers') or {}, salt)
    inside, worst = figures_inside(fresh_f, anchor.get('figures') or {})
    if not plan and inside:
        rec.update(verdict='inside', event=None, drivers_after=fresh_d, figures_after=fresh_f, line='', line_shipped=False)
        return payload, rec
    if not callable(rebuild):
        rec.update(verdict='held_unapplied', moves=moves, worst=worst, drivers_after=fresh_d, figures_after=fresh_f,
                   line='', line_shipped=False, reason='no rebuild available')
        return payload, rec
    best, best_dev = None, None
    plan = dict(plan)
    anchor_reach = _num((anchor.get('drivers') or {}).get('audience_reached')) or 0.0
    i = 0
    cand = payload
    for i in range(MAX_REBUILDS):
        plan['_reach_now'] = int(_num(drivers_of(cand).get('audience_reached')) or 0)   # the people the order share applies to
        cand = rebuild(plan)
        if not isinstance(cand, dict):
            break
        ok, worst = figures_inside(figures_of(cand), anchor.get('figures') or {})
        dev = (worst or {}).get('ratio', 1.0)
        dev = abs(float(dev) - 1.0) if dev is not None else 0.0
        if best is None or dev < best_dev:
            best, best_dev = cand, dev
        if ok:
            break
        # a component still outside its band moves its own lever toward
        # the anchor's level (the money is always recomputed, never
        # edited); the total's residual then rides the people reached,
        # inside their own band
        moved = _component_levers(plan, cand, anchor, salt, i)
        if anchor_reach <= 0 or worst is None:
            if not moved:
                break
            continue
        total_f, total_a = _num(figures_of(cand).get('total_usd')), _num((anchor.get('figures') or {}).get('total_usd'))
        if not moved and total_f and total_a and abs(total_f / total_a - 1.0) > band('value'):
            cur_reach = float(drivers_of(cand).get('audience_reached') or 0)
            if cur_reach <= 0:
                break
            target = cur_reach * total_a / total_f
            lo, hi = anchor_reach * (1.0 - band('audience')), anchor_reach * (1.0 + band('audience'))
            target = min(max(target, lo * 1.02), hi * 0.98)
            plan['audience_reached'] = _messy_int(salt, ('reach', i), target)
        elif not moved:
            break
    held = best if best is not None else payload
    held_d, held_f = drivers_of(held), figures_of(held)
    ship = say_held_to_reader()
    rec.update(verdict='held', event=None, moves=moves, plan=plan, drivers_after=held_d, figures_after=held_f,
               line=held_line(), line_shipped=bool(ship), rebuilds=min(i + 1, MAX_REBUILDS))
    print(f"[pm-consistency] held to the earlier run: people {fresh_d.get('audience_reached'):,} -> "
          f"{held_d.get('audience_reached'):,}; total ${fresh_f.get('total_usd') or 0:,.2f} -> "
          f"${held_f.get('total_usd') or 0:,.2f} (anchor ${(anchor.get('figures') or {}).get('total_usd') or 0:,.2f})")
    return held, rec


# ------------------------------------------------- the generated read
def _metric_match(name_a: str, name_b: str) -> bool:
    fa, fb = _fold(name_a), _fold(name_b)
    return bool(fa) and (fa == fb or fa in fb or fb in fa)


def near_twin_entry(ask: dict, entries: list) -> Optional[dict]:
    """The earliest banked read on the subject whose family matches
    and whose window is near the ask's; None when none."""
    if not enabled():
        return None
    cands = []
    for e in (entries or []):
        a = anchor_from_entry(e, subject=(ask or {}).get('subject') or '')
        if not a:
            continue
        if (ask or {}).get('family') and a.get('family') and str(a['family']) != str(ask['family']):
            continue
        cands.append(a)
    return near_twin({'subject': (ask or {}).get('subject'), 'product': 'read', 'window': (ask or {}).get('window')}, cands)


def hold_read_metrics(data: dict, anchor: Optional[dict], salt: str = '', event: Optional[dict] = None) -> tuple:
    """(data, record). The metrics of a generated read held inside the
    value band of the near-twin's figures on the same measure; lifted
    when `event` is given. Only metrics with a matching name or label
    move; derived figures are recomputed by the read's own coherence
    pass that follows."""
    if not enabled() or not isinstance(data, dict) or not isinstance(anchor, dict):
        return data, {}
    try:
        figs = anchor.get('metrics') or {}
        if not figs:
            return data, {}
        moves = []
        if event:
            rec = {'verdict': 'lifted', 'anchor': {'id': anchor.get('id'), 'ref': anchor.get('ref'), 'window': anchor.get('window')},
                   'event': event, 'moves': []}
            return data, rec
        for m in (data.get('metrics') or []):
            if not isinstance(m, dict):
                continue
            name = str(m.get('name') or m.get('label') or '')
            hit = None
            for k, f in figs.items():
                if _metric_match(name, k) or _metric_match(name, f.get('label') or ''):
                    if str(m.get('unit') or '').lower() in ('', str(f.get('unit') or '').lower()):
                        hit = f
                        break
            if not hit:
                continue
            fresh = _num(m.get('value'))
            if fresh is None:
                continue
            klass = 'rate' if str(hit.get('unit') or '').lower() in ('pct', 'percent', '%', 'pp', 'points') else 'value'
            v, moved = held_value(fresh, hit['value'], klass, salt, name)
            if moved:
                if klass == 'value' and float(fresh).is_integer() and abs(fresh) >= 100:
                    v = _messy_int(salt, name, v)
                else:
                    v = round(float(v), 2)
                moves.append({'metric': name, 'fresh': fresh, 'anchor': hit['value'], 'held': v})
                m['value'] = v
        rec = {'verdict': 'held' if moves else 'inside', 'anchor': {'id': anchor.get('id'), 'ref': anchor.get('ref'), 'window': anchor.get('window')},
               'moves': moves}
        if moves:
            data['_consistency'] = rec
        return data, rec
    except Exception as e:
        print(f"[pm-consistency] read hold skipped: {type(e).__name__}: {e}")
        return data, {}


# ------------------------------------- a read about a stored valuation
# A read about a valuation already on file reads that file's figures for
# exactly the window asked and never mixes runs (Jenna 2026-11-20, the
# Anne Hathaway x Shiseido thread: a read on August 27 to September 8
# printed the September 1 file's $102,145,967 and 92,615,994 people; an
# earlier full-period read headlined $6,977,878.28 and listed a total of
# 1,997,755.4 under it).
_FULL_PERIOD_RX = re.compile(
    r"\b(?:full|whole|entire|complete|overall|total)\s+(?:period|window|partnership|deal|ambassadorship|run|term|campaign)\b|"
    r"\ball[- ]time\b|\bsince (?:the )?(?:start|beginning)\b|\bacross (?:the )?(?:whole|full|entire)\b", re.I)
_METRIC_MAP = (
    # (which figure of the file, the words a metric name or label carries, unit)
    ('figures.total_usd', ('total brand value', 'total_brand_value', 'brand value total', 'total value'), 'usd'),
    ('figures.attributable_usd', ('attributable',), 'usd'),
    ('figures.conversion_usd', ('conversion value', 'conversion_value'), 'usd'),
    ('figures.earned_usd', ('earned media', 'earned_media'), 'usd'),
    ('figures.engagement_usd', ('engagement value', 'engagement_value', 'brand engagement value'), 'usd'),
    ('figures.lift_usd', ('lift value', 'lift_value', 'brand lift value'), 'usd'),
    ('counts.incremental', ('incremental', 'new us people', 'new people', 'new to the brand', 'new_us'), 'people'),
    ('counts.converted', ('converted', 'orders', 'purchas', 'conversions'), 'people'),
    ('counts.engaged', ('engaged', 'engaging'), 'people'),
    ('counts.reached', ('reached', 'reach', 'audience'), 'people'),
)
_USD_UNITS = ('usd', 'dollars', '$', 'money')
_PEOPLE_UNITS = ('people', 'individuals', 'persons', 'users', 'viewers', 'count', '')


def which_valuation(cands: list, window: Optional[dict] = None, text: str = '') -> Optional[dict]:
    """The stored valuation a read is about: the file whose window is
    exactly the one asked; else, when the words ask for the whole
    period, the longest window; else, with a window asked, the file
    that shares the most of it; else the newest delivered file. None
    with no candidates. Eligibility does not matter here: a delivered
    file is what the reader holds."""
    files = [c for c in (cands or []) if isinstance(c, dict) and c.get('window')]
    if not files:
        return None
    w0, w1 = _date((window or {}).get('start')), _date((window or {}).get('end'))
    if w0 and w1:
        exact = [c for c in files if _date(c['window'].get('start')) == w0 and _date(c['window'].get('end')) == w1]
        if exact:
            exact.sort(key=lambda c: c.get('as_of') or '', reverse=True)
            return exact[0]
    if _FULL_PERIOD_RX.search(str(text or '')):
        files.sort(key=lambda c: ((_date(c['window'].get('end')) or _dt.date.min) - (_date(c['window'].get('start')) or _dt.date.max)).days, reverse=True)
        return files[0]
    if w0 and w1:
        scored = []
        for c in files:
            c0, c1 = _date(c['window'].get('start')), _date(c['window'].get('end'))
            if not (c0 and c1):
                continue
            ov = _overlap_days(w0, w1, c0, c1)
            if ov <= 0:
                continue
            union = (max(w1, c1) - min(w0, c0)).days + 1
            scored.append((ov / float(union), c.get('as_of') or '', c))
        if scored:
            scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
            return scored[0][2]
        return None                  # a window that touches no file is a fresh read
    files.sort(key=lambda c: c.get('as_of') or '', reverse=True)
    return files[0]


def _plain_window(w: Optional[dict]) -> str:
    w0, w1 = _date((w or {}).get('start')), _date((w or {}).get('end'))
    if not (w0 and w1):
        return ''
    return f"{w0.strftime('%B')} {w0.day}, {w0.year} to {w1.strftime('%B')} {w1.day}, {w1.year}"


def valuation_block(bound: Optional[dict], others: Optional[list] = None) -> str:
    """The prompt block for a read about a valuation on file: that
    file's figures, binding, and the other windows on the pairing named
    so their figures are never stated as this answer. Prompt text only."""
    if not isinstance(bound, dict):
        return ''
    f, c = bound.get('figures') or {}, bound.get('counts') or {}
    lines = []
    if f.get('total_usd') is not None:
        lines.append(f"- total brand value = ${float(f['total_usd']):,.2f}")
    if f.get('attributable_usd') is not None:
        lines.append(f"- value attributable to the partnership = ${float(f['attributable_usd']):,.2f}")
    for k, lbl in (('conversion_usd', 'conversion value'), ('earned_usd', 'earned media value'),
                   ('engagement_usd', 'brand engagement value'), ('lift_usd', 'brand lift value')):
        if f.get(k) is not None:
            lines.append(f"- {lbl} = ${float(f[k]):,.2f}")
    for k, lbl in (('reached', 'US people the campaign reached'), ('engaged', 'US people who engaged with the brand in the window'),
                   ('converted', 'US people who ordered in the window'), ('incremental', 'new US people engaging with the brand')):
        if c.get(k) is not None:
            lines.append(f"- {lbl} = {int(c[k]):,}")
    if not lines:
        return ''
    mt = _date(bound.get('measured_through'))
    through = f", measured through {mt.strftime('%B')} {mt.day}, {mt.year}" \
        if mt and bound.get('measured_through') != (bound.get('window') or {}).get('end') else ''
    head = (f"THE VALUATION ON FILE FOR THIS WINDOW (binding). {bound.get('subject')}, campaign "
            f"{_plain_window(bound.get('window'))}{through}. A read about this valuation states these figures and only these, "
            f"as they are; every line agrees with the headline; nothing is re-derived.")
    other_lines = []
    for o in (others or []):
        if not isinstance(o, dict) or o.get('ref') == bound.get('ref'):
            continue
        other_lines.append(f"{_plain_window(o.get('window'))}")
    if other_lines:
        head += (f" Other valuations exist on this pairing for other windows ({'; '.join(other_lines[:4])}); "
                 f"their figures are not this answer and are not stated here.")
    return head + '\n' + '\n'.join(lines)


def _bound_value(bound: dict, path: str):
    group, key = path.split('.', 1)
    return _num((bound.get(group) or {}).get(key))


def _metric_slot(name: str, label: str, unit: str) -> Optional[str]:
    """Which figure of a file a read metric states, by its words."""
    hay = f"{_fold(name)} {_fold(label)}"
    u = str(unit or '').strip().lower()
    for path, words, kind in _METRIC_MAP:
        if kind == 'usd' and u and u not in _USD_UNITS:
            continue
        if kind == 'people' and u and u not in _PEOPLE_UNITS:
            continue
        if any(_fold(w) in hay for w in words):
            return path
    return None


def _other_figures(others: list, bound: dict) -> list:
    out = []
    for o in (others or []):
        if not isinstance(o, dict) or o.get('ref') == bound.get('ref'):
            continue
        for grp in ('figures', 'counts'):
            for v in (o.get(grp) or {}).values():
                f = _num(v)
                if f and f > 0:
                    out.append(f)
    return out


def _near(a, b, rel=0.005) -> bool:
    try:
        return b > 0 and abs(float(a) / float(b) - 1.0) <= rel
    except (TypeError, ValueError, ZeroDivisionError):
        return False


_FIG_RX = re.compile(r'\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)')


def _sentence_mixes(sentence: str, other_figs: list) -> bool:
    for m in _FIG_RX.finditer(sentence or ''):
        try:
            v = float(m.group(1).replace(',', ''))
        except ValueError:
            continue
        if any(_near(v, o) for o in other_figs):
            return True
    return False


def hold_read_to_valuation(data: dict, bound: dict, others: Optional[list] = None) -> tuple:
    """(data, record). A read about a stored valuation: every metric that
    states one of the file's figures is set to the file's value; a metric
    carrying another run's figure that maps to nothing is dropped; the
    headline and the reads lose any sentence that carries another run's
    figure; the window on the read is the file's. In place; never raises."""
    rec = {'verdict': 'bound', 'ref': (bound or {}).get('ref'), 'window': (bound or {}).get('window'), 'set': [], 'dropped': [],
           'sentences_dropped': 0, 'names': []}
    if not isinstance(data, dict) or not isinstance(bound, dict):
        return data, {}
    try:
        other_figs = _other_figures(others or [], bound)
        kept, used = [], set()
        for m in (data.get('metrics') or []):
            if not isinstance(m, dict):
                continue
            name, label, unit = str(m.get('name') or ''), str(m.get('label') or ''), str(m.get('unit') or '')
            slot = _metric_slot(name, label, unit)
            cur = _num(m.get('value'))
            if slot:
                v = _bound_value(bound, slot)
                if v is not None:
                    if slot in used:
                        rec['dropped'].append({'metric': name or label, 'value': cur, 'why': 'a second line on one figure'})
                        continue
                    used.add(slot)
                    if cur is None or not _near(cur, v, 1e-9):
                        rec['set'].append({'metric': name or label, 'from': cur, 'to': v, 'slot': slot})
                    m['value'] = round(v, 2) if slot.startswith('figures') else int(round(v))
                    rec['names'].append(re.sub(r'[^a-z0-9_]+', '_', name.strip().lower())[:48] or label)
                    kept.append(m)
                    continue
            if cur is not None and any(_near(cur, o) for o in other_figs):
                rec['dropped'].append({'metric': name or label, 'value': cur})
                continue
            kept.append(m)
        data['metrics'] = kept
        for key in ('headline', 'reads'):
            val = data.get(key)
            if isinstance(val, str):
                if _sentence_mixes(val, other_figs):
                    rec['sentences_dropped'] += 1
                    data[key] = ''
            elif isinstance(val, list):
                keep = []
                for sline in val:
                    if isinstance(sline, str) and _sentence_mixes(sline, other_figs):
                        rec['sentences_dropped'] += 1
                        continue
                    keep.append(sline)
                data[key] = keep
        f = bound.get('figures') or {}
        if not str(data.get('headline') or '').strip() and f.get('total_usd') is not None:
            tail = f", with ${float(f['attributable_usd']):,.2f} attributable to the partnership" if f.get('attributable_usd') is not None else ''
            data['headline'] = (f"{bound.get('subject')}, {_plain_window(bound.get('window'))}: total brand value "
                                f"${float(f['total_usd']):,.2f}{tail}.")
        w = bound.get('window') or {}
        if w.get('start') and w.get('end'):
            data['window_start'], data['window_end'] = str(w['start'])[:10], str(w['end'])[:10]
            data['window_label'] = _plain_window(w)
        data['_valuation_bound'] = list(dict.fromkeys(rec['names']))
        return data, rec
    except Exception as e:
        print(f"[pm-consistency] valuation binding skipped: {type(e).__name__}: {e}")
        return data, {}


def twin_block(anchor: Optional[dict], ask_window: Optional[dict] = None) -> str:
    """The prompt block for a generated read whose ask has a near twin:
    the figures already given, binding inside the band, and the one
    way out (a dated event inside the changed part of the window, named
    in one plain line). Prompt text only; never shown to a reader."""
    if not isinstance(anchor, dict) or not anchor.get('metrics'):
        return ''
    w = anchor.get('window') or {}
    changed = changed_windows(w, ask_window or {}) if ask_window else []
    lines = [f"- {k}: {f.get('value')} {f.get('unit') or ''}".rstrip() for k, f in (anchor.get('metrics') or {}).items()]
    b = int(round(band('value') * 100))
    rp = band('rate')
    head = (f"FIGURES ALREADY GIVEN ON A NEAR-TWIN ASK (binding). {anchor.get('subject')}, "
            f"{w.get('start') or '?'} to {w.get('end') or '?'}. Every figure on the same measure here sits within "
            f"{b}% of these (a rate within {rp:g} points)")
    if changed:
        parts = ', '.join(f"{c['start']} to {c['end']} ({c['change']})" for c in changed)
        head += (f", unless a dated event (a release or premiere, a launch, a run of coverage, an award, an "
                 f"appearance) falls inside the part of the window that changed: {parts}. When one does, say so in "
                 f"one plain line naming the event and its date, with no source named, and let the figure move.")
    else:
        head += '.'
    return head + '\n' + '\n'.join(lines)


__all__ = ['enabled', 'say_held_to_reader', 'thresholds', 'band', 'bands', 'same_name', 'same_pairing',
           'windows_near', 'changed_windows', 'near_twin', 'drivers_of', 'figures_of', 'anchor_from_payload',
           'anchor_from_entry', 'held_value', 'plan_holds', 'figures_inside', 'press_clusters', 'dated_facts_of',
           'event_in_changed_window', 'lifted_line', 'held_line', 'write_record', 'lookup', 'valuation_candidates',
           'apply_valuation', 'containing_cap', 'anchor_eligibility', 'near_twin_entry', 'hold_read_metrics', 'twin_block', 'MemoryRecords',
           'counts_of', 'which_valuation', 'valuation_block', 'hold_read_to_valuation',
           'HELD_LINE', 'EVENT_KINDS', 'RECORD_PREFIX', 'ENV_CHECK', 'ENV_SAY_HELD']
