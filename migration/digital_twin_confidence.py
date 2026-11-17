"""How firm a count is (Digital Twins IQ, 2026-11-17).

Jenna, 2026-11-17: a predictive product has to say how sure it is. Every
count the twins publish (a room's split, a scorecard measure, a
forecast figure) carries a range and a plain word for how firm it is,
and a room that has too little to go on says so instead of publishing a
number as if it were settled.

THE FIVE SIGNALS (version 1, weights documented here; version 2 trains
the same mapping on the ledger once it holds a few hundred scored rows)

  evidence    what the count rests on: a counted result (1.0), a published
              poll before the event (0.85), the house's own counted read on
              this audience and this thing (0.8), the desk's anchor derived
              from research (0.6), the room's habit prior alone (0.3), no
              research landed (0.2).                                       weight .35
  agreement   how far the seats agree, weighted by the people they stand
              for and their own 1 to 5 sureness: one side carrying most of
              the room is firmer than a four-way split. Capped when the
              room is far more agreeable than the audience's own prior says
              it should be (the under-dispersion a twin room is prone to).   weight .20
  stability   how wide the anchor's own range is (the analyst's low and
              high on the engaged rows): a narrow range is a steadier count. weight .15
  history     the ledger: the typical error on rows like this one (same
              kind of question, same class of audience), once three or
              more are scored; neutral until then.                          weight .20
  novelty     how much the desk found: a question on a thing with facts on
              file is firmer than one on a thing nobody has written about.   weight .10

The weighted score (0 to 1) maps to a predicted error in points:
2 + 18 x (1 - score), so a count never claims better than about two
points and never worse than twenty. The word: firm at 5 points or
under, leaning to 12, early above. A room with no evidence and no
research declines to call it: the split still publishes (nothing is
ever held), marked early, with one line saying what would settle it.

THE RANGE  a share: the point plus or minus the predicted error (clipped
to 0..100), the people scaled the same way; a count: the ledger's own
relative error when it has one for this measure, else the forecast's
calibration range, else plus or minus a quarter.

WHAT THE READER SEES  a range and one of three words (firm, leaning,
early) on the count, and the decline line when it applies. Never a tier
label, never a score, never the word confidence, never a method word.
``public_firmness`` is the only shape that leaves the record; the
signals stay on the trail. Companion rule carve-out:
analysis-confidence-calibration.mdc, Digital Twins IQ section.
"""
from __future__ import annotations

import math
from typing import Optional

VERSION = 1
WEIGHTS = {'evidence': 0.35, 'agreement': 0.20, 'stability': 0.15, 'history': 0.20, 'novelty': 0.10}
EVIDENCE = {'result': 1.0, 'poll': 0.85, 'house': 0.8, 'derived': 0.6, 'prior': 0.3, 'unresearched': 0.2, 'unanchored': 0.25, '': 0.3}
ERROR_FLOOR_PP = 2.0
ERROR_CEILING_PP = 20.0
FIRM_PP = 5.0
LEANING_PP = 12.0
WORDS = ('firm', 'leaning', 'early')
WORD_LINE = {'firm': 'Firm', 'leaning': 'Leaning', 'early': 'An early read'}
WORD_PLAIN = {'firm': 'the room is sure of this one', 'leaning': 'a lean, not a lock', 'early': 'an early read; treat it as a direction, not a count'}


def _clamp(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


def _entropy(ps: list) -> float:
    ps = [p for p in ps if p and p > 0]
    if len(ps) <= 1:
        return 0.0
    tot = sum(ps)
    return -sum((p / tot) * math.log(p / tot) for p in ps)


def agreement_signal(answers: list, roster: list, options: list, prior: Optional[dict] = None) -> dict:
    """How far the seats agree, by the people they stand for and their
    sureness, against the prior's own spread. Returns the signal (0..1)
    and the audit fields (the dispersion check)."""
    ppl = {}
    for r in roster or []:
        try:
            ppl[int(r.get('seat'))] = max(1.0, float(r.get('people') or 1))
        except (TypeError, ValueError):
            continue
    ids = [o.get('id') for o in options or []]
    mass = {i: 0.0 for i in ids}
    confs = []
    for a in answers or []:
        try:
            sid = int(a.get('seat'))
        except (TypeError, ValueError):
            continue
        oid = a.get('option') if a.get('option') in mass else None
        if oid is None:
            continue
        mass[oid] += ppl.get(sid, 1.0)
        try:
            confs.append(float(a.get('confidence') or 3))
        except (TypeError, ValueError):
            confs.append(3.0)
    tot = sum(mass.values())
    if tot <= 0 or len(ids) <= 1:
        return {'signal': 0.5, 'lead_share': None, 'seat_entropy': None, 'prior_entropy': None, 'dispersion_ratio': None, 'mean_sureness': None, 'agreeable': False}
    shares = [mass[i] / tot for i in ids]
    lead = max(shares)
    h_room = _entropy(shares)
    h_max = math.log(len(ids))
    h_prior = _entropy([float((prior or {}).get(i) or 0) for i in ids]) if prior else None
    ratio = (h_room / h_prior) if (h_prior and h_prior > 0) else None
    sure = (sum(confs) / len(confs) - 1.0) / 4.0 if confs else 0.5     # 1..5 -> 0..1
    base = 0.55 * (1.0 - h_room / h_max if h_max > 0 else 0.5) + 0.45 * sure
    # a room far more agreeable than the audience's own prior is a known distortion, not certainty: the credit is capped
    agreeable = bool(ratio is not None and ratio < 0.45 and h_prior and h_prior > 0.5)
    if agreeable:
        base = min(base, 0.55)
    return {'signal': round(_clamp(base, 0.0, 1.0), 4), 'lead_share': round(lead * 100.0, 1), 'seat_entropy': round(h_room, 4), 'prior_entropy': (round(h_prior, 4) if h_prior is not None else None),
            'dispersion_ratio': (round(ratio, 3) if ratio is not None else None), 'mean_sureness': (round(sum(confs) / len(confs), 2) if confs else None), 'agreeable': agreeable}


def stability_signal(anchor_basis: Optional[dict]) -> dict:
    """How steady the anchor is: the width of the analyst's range on the
    engaged rows (points). Narrow is steady. 0.5 when there is none."""
    rows = []
    for r in ((anchor_basis or {}).get('rows') or (anchor_basis or {}).get('options') or []):
        try:
            lo, hi = float(r.get('low_pct') if r.get('low_pct') is not None else r.get('low')), float(r.get('high_pct') if r.get('high_pct') is not None else r.get('high'))
            rows.append(abs(hi - lo))
        except (TypeError, ValueError, AttributeError):
            continue
    if not rows:
        return {'signal': 0.5, 'range_width_pp': None}
    width = sum(rows) / len(rows)
    return {'signal': round(_clamp(1.0 - width / 30.0, 0.0, 1.0), 4), 'range_width_pp': round(width, 1)}


def history_signal(history: Optional[dict], unit: str = 'pct') -> dict:
    """The ledger's word on rows like this one."""
    h = history or {}
    n = int(h.get('n_scored') or 0)
    if n < 3:
        return {'signal': 0.5, 'n_scored': n, 'typical_error': None}
    if unit == 'pct':
        err = h.get('typical_error')
        sig = 1.0 - _clamp(float(err or 0) / 20.0, 0.0, 1.0) if err is not None else 0.5
    else:
        rel = h.get('mean_rel_error')
        sig = 1.0 - _clamp(float(rel or 0) / 0.5, 0.0, 1.0) if rel is not None else 0.5
    return {'signal': round(_clamp(sig, 0.0, 1.0), 4), 'n_scored': n, 'typical_error': h.get('typical_error') if unit == 'pct' else h.get('mean_rel_error')}


def novelty_signal(study: Optional[dict], facts_n: int = 0) -> dict:
    """How much the desk found: facts on file make a firmer count."""
    st = study or {}
    if st.get('error') or st.get('ok') is False:
        return {'signal': 0.2, 'facts_n': facts_n, 'searches': int(st.get('searches') or 0)}
    n = max(int(facts_n or 0), len(st.get('figures') or []) + len(st.get('reach') or []) + len(st.get('overlap') or []))
    sig = 0.4 + 0.15 * min(4, n)
    return {'signal': round(_clamp(sig, 0.0, 1.0), 4), 'facts_n': n, 'searches': int(st.get('searches') or 0)}


def predicted_error_pp(signals: dict) -> float:
    score = sum(WEIGHTS[k] * float((signals.get(k) or {}).get('signal', 0.5)) for k in WEIGHTS)
    return round(ERROR_FLOOR_PP + (ERROR_CEILING_PP - ERROR_FLOOR_PP) * (1.0 - _clamp(score, 0.0, 1.0)), 1)


def word_for(error_pp: float) -> str:
    return 'firm' if error_pp <= FIRM_PP else ('leaning' if error_pp <= LEANING_PP else 'early')


def firmness_for_room(rec: dict, roster: list, *, prior: Optional[dict] = None, history: Optional[dict] = None) -> dict:
    """The firmness block for a room question record (after the count is
    published): the signals, the predicted error, the word, the per
    option ranges, the decline. Internal fields and the public view."""
    trail = rec.get('trail') or {}
    status = str(trail.get('anchor_status') or '')
    kind = str(rec.get('kind') or '')
    if kind in ('rating', 'probe'):
        status = status or 'prior'
    ev = EVIDENCE.get(status, 0.3)
    ag = agreement_signal(rec.get('answers') or [], roster, rec.get('options') or [], prior)
    anchor = trail.get('anchor') or {}
    stb = stability_signal((anchor.get('basis') or {}) if isinstance(anchor, dict) else None)
    if status in ('result', 'poll', 'house'):
        stb = {'signal': max(stb['signal'], 0.85), 'range_width_pp': stb.get('range_width_pp')}
    hist = history_signal(history, 'pct')
    nov = novelty_signal(trail.get('study'), len(rec.get('facts_used') or []))
    signals = {'evidence': {'signal': ev, 'anchor_status': status}, 'agreement': ag, 'stability': stb, 'history': hist, 'novelty': nov}
    err = predicted_error_pp(signals)
    # a count set by a counted result is the result: firm whatever the desk found; a published poll before the event caps at a lean
    if status == 'result':
        err = min(err, 3.0)
    elif status == 'poll':
        err = min(err, 6.0)
    word = word_for(err)
    decline = bool(status in ('unresearched', 'unanchored', 'prior', '') and nov['signal'] <= 0.4 and kind not in ('rating', 'probe'))
    total = int(sum(int(o.get('people') or 0) for o in rec.get('options') or []) or 0)
    ranges = []
    for o in rec.get('options') or []:
        try:
            p = float(o.get('pct') or 0.0)
        except (TypeError, ValueError):
            p = 0.0
        lo, hi = round(_clamp(p - err, 0.0, 100.0), 1), round(_clamp(p + err, 0.0, 100.0), 1)
        ranges.append({'id': o.get('id'), 'label': o.get('label'), 'pct': p, 'low_pct': lo, 'high_pct': hi,
                       'low_people': int(round(total * lo / 100.0)), 'high_people': int(round(total * hi / 100.0))})
    lead = max(ranges, key=lambda r: r['pct']) if ranges else None
    what_settles = _settles_line(status, nov, ag)
    return {'version': VERSION, 'word': word, 'predicted_error_pp': err, 'decline': decline, 'ranges': ranges, 'lead': lead,
            'signals': signals, 'what_settles': what_settles, 'line': firmness_line(word, lead, decline, what_settles)}


def _settles_line(status: str, nov: dict, ag: dict) -> str:
    if status in ('result', 'poll'):
        return ''
    parts = []
    if status in ('unresearched', 'unanchored', '', 'prior') or nov.get('signal', 0) <= 0.4:
        parts.append('a counted read on this audience, or a check with real people')
    if ag.get('agreeable'):
        parts.append('a wider room')
    return ('What would settle it: ' + ' and '.join(parts) + '.') if parts else ''


def firmness_line(word: str, lead: Optional[dict], decline: bool = False, what_settles: str = '') -> str:
    """The one line a reader sees. 'Firm: the lead sits between 38.1%
    and 44.9%.' An early read that declines says so first."""
    if not lead:
        return f"{WORD_LINE.get(word, 'Leaning')}."
    core = f"the lead ({str(lead.get('label') or '').strip().rstrip('.')}) sits between {lead['low_pct']:.1f}% and {lead['high_pct']:.1f}%"
    if decline:
        line = f"Not enough to call it yet: {core}, {WORD_PLAIN['early']}."
    else:
        line = f"{WORD_LINE.get(word, 'Leaning')}: {core}; {WORD_PLAIN.get(word, '')}."
    if what_settles and word != 'firm':
        line += ' ' + what_settles
    return line


def firmness_for_count(point: float, *, low: Optional[float] = None, high: Optional[float] = None, calibration_scope: str = '', history: Optional[dict] = None,
                       appeal_known: bool = True, unit: str = 'people') -> dict:
    """The firmness block for a forecast figure: the range from the
    ledger's relative error on this measure when it has three scored
    rows, else the calibration range given; the word from how the count
    was anchored (this show's own anchors: firm; the platform's:
    leaning; nothing yet: early)."""
    hist = history_signal(history, 'count')
    try:
        pt = float(point)
    except (TypeError, ValueError):
        pt = 0.0
    rel = None
    if (history or {}).get('n_scored', 0) >= 3 and (history or {}).get('mean_rel_error') is not None:
        rel = _clamp(float(history['mean_rel_error']) * 1.5, 0.05, 0.6)     # the ledger's typical miss, with headroom
    if rel is not None and pt > 0:
        lo, hi = pt * (1.0 - rel), pt * (1.0 + rel)
    elif low is not None and high is not None:
        lo, hi = float(low), float(high)
    else:
        lo, hi = pt * 0.75, pt * 1.25
    scope = str(calibration_scope or 'none')
    word = 'firm' if scope == 'show' else ('leaning' if scope in ('platform', 'all') else 'early')
    if hist['n_scored'] >= 3 and hist['signal'] >= 0.8 and word == 'leaning':
        word = 'firm'
    if not appeal_known:
        word = 'early'
    err_rel = round((hi - lo) / (2.0 * pt), 3) if pt > 0 else None
    return {'version': VERSION, 'word': word, 'low': lo, 'high': hi, 'relative_error': err_rel, 'decline': False,
            'signals': {'history': hist, 'calibration_scope': scope},
            'line': f"{WORD_LINE[word]}: {WORD_PLAIN[word]}."}


def public_firmness(block: Optional[dict]) -> Optional[dict]:
    """The reader's view: the word, the line, the ranges. No signals, no
    score, nothing about how it was weighed."""
    if not isinstance(block, dict) or not block.get('word'):
        return None
    out = {'word': block['word'], 'line': block.get('line') or '', 'decline': bool(block.get('decline'))}
    if block.get('ranges'):
        out['ranges'] = [{k: v for k, v in r.items() if k in ('id', 'label', 'pct', 'low_pct', 'high_pct', 'low_people', 'high_people')} for r in block['ranges']]
    if block.get('lead'):
        out['lead'] = {k: v for k, v in block['lead'].items() if k in ('id', 'label', 'pct', 'low_pct', 'high_pct', 'low_people', 'high_people')}
    for k in ('low', 'high'):
        if block.get(k) is not None:
            out[k] = block[k]
    if block.get('what_settles'):
        out['what_settles'] = block['what_settles']
    return out


def range_words(lo: float, hi: float, unit: str = 'pct') -> str:
    if unit == 'pct':
        return f"{float(lo):.1f}% to {float(hi):.1f}%"
    return f"{int(round(float(lo))):,} to {int(round(float(hi))):,}"


__all__ = ['VERSION', 'WEIGHTS', 'EVIDENCE', 'WORDS', 'firmness_for_room', 'firmness_for_count', 'public_firmness', 'agreement_signal', 'stability_signal',
           'history_signal', 'novelty_signal', 'predicted_error_pp', 'word_for', 'firmness_line', 'range_words']
