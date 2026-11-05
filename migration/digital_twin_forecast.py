"""The performance forecast a scored pilot test carries (Jenna 2026-11-03).

Jenna, verbatim: "we also need to add something that will help predict how
a show/episode will perform based on the pilot test. we can calibrate it
based on actuals. like after the test is done when it calculates it needs
to give a viewership estimate for how that episode would do if released
and then based on the outcome how it would likely do as a series showing
forecast season completion rate and second screen rate (meaning how many
people were not paying attention and on another device). it should also
estimate what percentage of people would likely subscribe to the streaming
platform just to watch that show (that do not currently have
subscriptions) and what percent would reactivate a dormant account to
watch the show."

What a scored test gets (``session['forecast']``):

  episode        the week-one audience if the episode were released (people
                 who watch within seven days, every screen), with a range
  series         the season completion rate (the share of episode viewers
                 who watch through the finale) and the second screen rate
                 (the share on another device during the episode), plus the
                 season average and finale audiences that follow
  subscriptions  the share of people without the platform who would
                 subscribe for this show, and the share of dormant accounts
                 that would come back for it, each with the people it means
  calibration    the factor applied, where it came from (this show, this
                 platform, everything, or nothing yet) and the anchors

How it is read. The room's own scores against the norms set the appeal of
the cut (total positive, definitely recommend, would finish). The twin
says how many people stand behind the room, how many of them have lapsed,
how they watch (second screen, churn, loyalty). Priors sit at the low end
of the defensible range (calibrate-low-anchor-to-actuals.mdc): the first
read of a show is low until its actuals say otherwise.

Calibration. ``record_actual`` writes what an episode really did next to
what the forecast said before any calibration, and the ledger
(``system/digital_twins/forecast_calibration.json``) turns those pairs
into a factor: this show's own pairs first, then the platform's, then
everything. Every later forecast on that show or platform carries the
factor, and the residual spread of the anchors sets the range. The first
anchors are NCIS: New York's first two episodes.

Nothing here names a source in anything a reader sees; the notes say
"calibrated to the first two episodes' audiences" and no more.
"""
from __future__ import annotations

import json
import math
import time
from typing import Optional

from migration.digital_twin_engine import clamp, messy_count, unit

S3_BUCKET = 'dashboard-inputs'
LEDGER_KEY = 'system/digital_twins/forecast_calibration.json'
VERSION = 1

US_ADULTS = 262_000_000            # the people who could hold a subscription
PRIOR_REACH_RATE = 0.06            # share of the release footprint that watches a new episode in week one, at an average cut
APPEAL_ELASTICITY = 0.5            # a cut twice as liked draws about 1.4x the audience; marketing, slot and lead-in carry the rest
RANGE_DEFAULT = 0.25               # the range with no anchors
POSITION = {'pilot': 1.0, 'feature': 1.0, 'episode': 0.85, 'binge': 0.85, 'trailer': 1.0, 'spot': 1.0}
COMPLETION_BASE = {'action': 0.56, 'crime': 0.56, 'procedural': 0.56, 'thriller': 0.54, 'drama': 0.5, 'comedy': 0.52, 'reality': 0.45,
                   'documentary': 0.47, 'animation': 0.5, 'family': 0.5, 'horror': 0.48, 'romance': 0.5, 'scifi': 0.5}
COMPLETION_DEFAULT = 0.5
SUBSCRIBE_BASE_PCT = 0.9           # of people without the platform, at an average cut
REACTIVATE_BASE_PCT = 2.4          # of dormant accounts, at an average cut
NORM_FALLBACK = {'total_positive': 65.0, 'definitely_recommend': 50.0, 'finish': 85.0}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _meta(twin):
    return (twin or {}).get('meta') or {}


def _trait(twin, key, default=50.0) -> float:
    row = ((twin or {}).get('traits') or {}).get(key)
    try:
        return float(row.get('value') if isinstance(row, dict) else row)
    except (TypeError, ValueError, AttributeError):
        return default


def _norm(sc: dict, metric: str) -> float:
    cols = (sc or {}).get('norm_columns') or {}
    row = cols.get(metric) or {}
    for k in ('genre_norm', 'norm'):
        try:
            v = float(row.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return NORM_FALLBACK.get(metric, 50.0)


def appeal(sc: dict) -> dict:
    """How the cut played against the norms: each headline number as an
    index to its norm, and one appeal score (total positive carries most
    of it, would-finish next, recommend last). 1.0 is an average cut."""
    hd = ((sc or {}).get('battery') or {}).get('headline') or {}

    def idx(key, metric):
        try:
            v = float(hd.get(key))
        except (TypeError, ValueError):
            return 1.0
        return clamp(v / _norm(sc, metric), 0.0, 2.5)
    tp, rec, fin = idx('total_positive_pct', 'total_positive'), idx('definitely_recommend_pct', 'definitely_recommend'), idx('finish_pct', 'finish')
    score = clamp(0.45 * tp + 0.25 * rec + 0.30 * fin, 0.08, 2.2)
    return {'total_positive_idx': round(tp, 3), 'recommend_idx': round(rec, 3), 'finish_idx': round(fin, 3), 'score': round(score, 3)}


def second_screen_rate(sess: dict, twin: dict) -> float:
    """The share of the audience on another device during the episode: the
    seats that checked out in the room (the larger part) with the twin's
    own second-screen habit behind them."""
    roster = ((sess or {}).get('group') or {}).get('roster') or []
    n = max(1, len(roster))
    seats_out = {e.get('seat') for e in (sess.get('events') or []) if str(e.get('type') or '') == 'check-out'}
    room = len(seats_out) / float(n)
    trait = clamp(_trait(twin, 'second_screen') / 100.0, 0.0, 1.0) * 0.6
    return clamp(0.6 * room + 0.4 * trait, 0.05, 0.85)


def _genre_key(sess: dict, twin: Optional[dict] = None) -> str:
    """The genre the completion base reads: the session's own, else the
    genre another test of the same show on this twin carries."""
    def key_of(g):
        g = str(g or '').lower()
        for k in COMPLETION_BASE:
            if k in g:
                return k
        return ''
    own = key_of((sess or {}).get('genre') or ((sess or {}).get('scorecard') or {}).get('genre'))
    if own or not twin:
        return own
    show = _show_key(_show_name(twin, sess))
    for other in (twin.get('pilot_sessions') or []):
        if not isinstance(other, dict) or other.get('id') == (sess or {}).get('id'):
            continue
        if _show_key(_show_name(twin, other)) == show:
            k = key_of(other.get('genre') or (other.get('scorecard') or {}).get('genre'))
            if k:
                return k
    return ''


def _dial_trajectory(sess: dict) -> float:
    d = (sess or {}).get('dial') or {}
    vs = [float(x) for x in (d.get('value') or []) if x is not None]
    if len(vs) < 4:
        return 0.0
    head = sum(vs[:max(1, len(vs) // 10)]) / float(max(1, len(vs) // 10))
    tail = sum(vs[-max(1, len(vs) // 10):]) / float(max(1, len(vs) // 10))
    return clamp((tail - head) / 100.0, -0.5, 0.5)


def _show_name(twin: dict, sess: dict) -> str:
    media = (sess or {}).get('media') or {}
    t = str(media.get('title') or sess.get('title') or _meta(twin).get('name') or '').strip()
    # "NCIS: New York Episode 2" -> "NCIS: New York"
    import re
    t = re.sub(r"\s*[:,-]?\s*(?:episode|ep\.?|pilot|season|s\d+e\d+|part)\b.*$", '', t, flags=re.I).strip() or t
    return t


def _show_key(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", '_', str(name or '').lower()).strip('_')


# ------------------------------------------------------------------ the ledger
def _empty_ledger() -> dict:
    return {'schema': VERSION, 'records': [], 'updated_at': ''}


def load_ledger(s3) -> dict:
    if s3 is None:
        return _empty_ledger()
    try:
        raw = s3.get_object(Bucket=S3_BUCKET, Key=LEDGER_KEY)['Body'].read()
        doc = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
        if isinstance(doc, dict) and isinstance(doc.get('records'), list):
            return doc
    except Exception:
        pass
    return _empty_ledger()


def save_ledger(s3, doc: dict) -> bool:
    if s3 is None:
        return False
    doc = dict(doc)
    doc['updated_at'] = _now()
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=LEDGER_KEY, Body=json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8'), ContentType='application/json')
        return True
    except Exception:
        return False


def _geo_mean(xs: list) -> float:
    xs = [float(x) for x in xs if x and float(x) > 0]
    if not xs:
        return 1.0
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def factor_for(ledger: dict, twin: dict, sess: dict) -> dict:
    """The calibration that applies to this test: this show's own anchors,
    else the platform's, else everything on the ledger, else none."""
    recs = [r for r in (ledger or {}).get('records') or [] if isinstance(r, dict) and r.get('actual') and r.get('predicted_uncalibrated')]
    show = _show_key(_show_name(twin, sess))
    plat = str(_meta(twin).get('platform_home') or _meta(twin).get('name') or '').strip().lower()
    for scope, pick in (('show', [r for r in recs if r.get('show_key') == show]),
                        ('platform', [r for r in recs if str(r.get('platform') or '').lower() == plat and plat]),
                        ('all', recs)):
        if pick:
            ratios = [float(r['actual']) / float(r['predicted_uncalibrated']) for r in pick]
            k = _geo_mean(ratios)
            spread = max(abs(math.log(x / k)) for x in ratios) if len(ratios) > 1 else 0.3
            rng = clamp(math.exp(spread) - 1.0, 0.12, 0.6)
            return {'factor': round(k, 4), 'scope': scope, 'n': len(pick), 'range': round(rng, 3),
                    'anchors': [{'show': r.get('show'), 'episode': r.get('episode'), 'actual': r.get('actual'), 'predicted_uncalibrated': r.get('predicted_uncalibrated'),
                                 'aired': r.get('aired'), 'measure': r.get('measure')} for r in pick]}
    return {'factor': 1.0, 'scope': 'none', 'n': 0, 'range': RANGE_DEFAULT, 'anchors': []}


# ----------------------------------------------------------------- the read
def _base_people(twin: dict) -> int:
    try:
        return int(float(_meta(twin).get('size_us') or 0))
    except (TypeError, ValueError):
        return 0


def uncalibrated_week_one(twin: dict, sess: dict) -> dict:
    """The week-one audience before any calibration: the people behind the
    room, the prior reach rate, the cut's appeal, the episode's position."""
    sc = sess.get('scorecard') or {}
    a = appeal(sc)
    base = _base_people(twin)
    mode = str(sess.get('mode') or 'pilot')
    pos = POSITION.get(mode, 0.9)
    raw = base * PRIOR_REACH_RATE * (a['score'] ** APPEAL_ELASTICITY) * pos
    return {'people': raw, 'appeal': a, 'position_factor': pos, 'base_people': base}


def forecast_for_session(twin: dict, sess: dict, *, s3=None, ledger: Optional[dict] = None, actual: Optional[dict] = None, notes: Optional[list] = None) -> dict:
    """The forecast block for a scored test. ``actual`` ({people, measure,
    aired, note}) rides along when the episode has aired; the ledger
    (loaded when not given) supplies the calibration."""
    sc = sess.get('scorecard') or {}
    if not sc:
        return {}
    ledger = ledger if isinstance(ledger, dict) else load_ledger(s3)
    show = _show_name(twin, sess)
    seed = f"forecast|{sess.get('id')}|{show}"
    unc = uncalibrated_week_one(twin, sess)
    cal = factor_for(ledger, twin, sess)
    week_one = unc['people'] * cal['factor']
    rng = cal['range']
    a = unc['appeal']
    ss = second_screen_rate(sess, twin)
    gk = _genre_key(sess, twin)
    comp_base = COMPLETION_BASE.get(gk, COMPLETION_DEFAULT)
    traj = _dial_trajectory(sess)
    completion = clamp(comp_base * (a['finish_idx'] ** 0.7) * (1.0 + 0.3 * traj) * (1.0 - 0.5 * ss), 0.12, 0.92)
    loyalty = _trait(twin, 'franchise_loyalty')
    churn = _trait(twin, 'churn_propensity')
    pull = clamp(1.0 + (loyalty - 50.0) / 100.0, 0.6, 1.5)
    sub_pct = clamp(SUBSCRIBE_BASE_PCT * (0.5 + 0.5 * a['recommend_idx']) * (0.7 + 0.3 * a['total_positive_idx']) * pull, 0.05, 6.0)
    react_pct = clamp(REACTIVATE_BASE_PCT * (0.5 + 0.5 * a['recommend_idx']) * pull * (0.6 + 0.4 * churn / 100.0), 0.1, 12.0)
    base = unc['base_people']
    lc = (twin or {}).get('lifecycle') or {}
    try:
        lapsing = float(lc.get('lapsing_pct') or 18.0) / 100.0
    except (TypeError, ValueError):
        lapsing = 0.18
    non_subs = max(int(US_ADULTS * 0.3), US_ADULTS - base)
    dormant = int(base * lapsing)
    # the season that follows: a procedural's episodes settle to about three quarters of week one, and the finale
    # holds the completers
    season_avg = week_one * clamp(0.62 + 0.25 * completion, 0.55, 0.9)
    finale = week_one * completion * 1.08

    def people(kpi, v):
        return messy_count(seed, kpi, v)

    def pct(v):
        v = round(float(v), 1)
        if abs(v * 10 - round(v * 10)) < 1e-9 and int(round(v * 10)) % 10 == 0:
            v = round(v + 0.1 + 0.2 * unit(seed, 'pct', v), 1)
        return v
    def people_or_none(kpi, v):
        return people(kpi, v) if base > 0 else None   # a twin with no audience size behind it carries the rates, not the counts
    out = {
        'version': VERSION, 'computed_at': _now(), 'show': show, 'mode': str(sess.get('mode') or 'pilot'),
        'base_people': base,
        'appeal': a,
        'episode': {
            'week_one_audience': people_or_none('week_one', week_one),
            'low': people_or_none('week_one_low', week_one * (1.0 - rng)),
            'high': people_or_none('week_one_high', week_one * (1.0 + rng)),
            'uncalibrated': people_or_none('week_one_raw', unc['people']),
            'position_factor': unc['position_factor'],
            'window_days': 7,
            'label': 'Week-one audience if released',
            'definition': 'People who watch the episode within seven days of release, on every screen.',
        },
        'series': {
            'season_completion_pct': pct(completion * 100.0),
            'second_screen_pct': pct(ss * 100.0),
            'season_average_audience': people_or_none('season_avg', season_avg),
            'finale_audience': people_or_none('finale', finale),
            'completion_definition': 'The share of episode viewers who watch through the season finale.',
            'second_screen_definition': 'The share of viewers on another device during the episode.',
        },
        'subscriptions': {
            'subscribe_pct_of_nonsubscribers': pct(sub_pct),
            'subscribe_people': people_or_none('subscribe', non_subs * sub_pct / 100.0),
            'nonsubscribers_people': non_subs if base > 0 else None,
            'reactivate_pct_of_dormant': pct(react_pct),
            'reactivate_people': people_or_none('reactivate', dormant * react_pct / 100.0),
            'dormant_people': dormant if base > 0 else None,
            'platform': str(_meta(twin).get('platform_home') or _meta(twin).get('name') or ''),
        },
        'calibration': {'factor': cal['factor'], 'scope': cal['scope'], 'n': cal['n'], 'range_pct': round(rng * 100.0, 1), 'anchors': cal['anchors']},
        'actual': None,
        'notes': [],
    }
    if isinstance(actual, dict) and actual.get('people'):
        try:
            act = int(float(actual['people']))
        except (TypeError, ValueError):
            act = 0
        if act > 0:
            resid = ((out['episode']['week_one_audience'] or 0) - act) / float(act) * 100.0 if out['episode']['week_one_audience'] else None
            out['actual'] = {'week_one_audience': act, 'measure': str(actual.get('measure') or out['episode']['definition']), 'aired': str(actual.get('aired') or ''),
                             'note': str(actual.get('note') or ''), 'forecast_vs_actual_pct': (round(resid, 1) if resid is not None else None)}
    plat = out['subscriptions']['platform'] or 'the service'
    notes = list(notes or [])
    if cal['scope'] == 'show':
        notes.append(f"Calibrated to {show}'s first {cal['n']} episode{'s' if cal['n'] != 1 else ''} as aired.")
    elif cal['scope'] == 'platform':
        notes.append(f"Calibrated to {cal['n']} aired episode{'s' if cal['n'] != 1 else ''} on {plat}.")
    elif cal['scope'] == 'all':
        notes.append(f"Calibrated to {cal['n']} aired episode{'s' if cal['n'] != 1 else ''} across the library.")
    else:
        # plain English only: the reader never sees the word ledger (2026-11-04)
        notes.append('No aired episode to compare against yet. The forecast sits at the low end and calibrates as actuals come in.')
    out['notes'] = notes
    return out


def record_actual(s3, twin: dict, sess: dict, *, people: int, measure: str, aired: str = '', note: str = '', episode=None, ledger: Optional[dict] = None, save: bool = True) -> dict:
    """What an episode really did, next to what the forecast said before
    calibration, on the ledger. Returns the ledger."""
    ledger = ledger if isinstance(ledger, dict) else load_ledger(s3)
    unc = uncalibrated_week_one(twin, sess)
    show = _show_name(twin, sess)
    rec = {'show': show, 'show_key': _show_key(show), 'episode': episode, 'session_id': str(sess.get('id') or ''), 'twin': str(_meta(twin).get('name') or ''),
           'platform': str(_meta(twin).get('platform_home') or _meta(twin).get('name') or ''), 'mode': str(sess.get('mode') or ''),
           'predicted_uncalibrated': int(round(unc['people'])), 'appeal': unc['appeal'], 'actual': int(people), 'measure': str(measure or ''),
           'aired': str(aired or ''), 'note': str(note or ''), 'recorded_at': _now()}
    recs = [r for r in ledger.get('records') or [] if not (isinstance(r, dict) and str(r.get('session_id')) == rec['session_id'])]
    recs.append(rec)
    ledger['records'] = recs
    if save:
        save_ledger(s3, ledger)
    return ledger


def attach_forecast(twin: dict, sess: dict, *, s3=None, ledger: Optional[dict] = None) -> Optional[dict]:
    """Put the forecast on a scored session (keeps a recorded actual).
    Never raises; None when the session has no scorecard."""
    try:
        if not (sess or {}).get('scorecard'):
            return None
        prior = sess.get('forecast') if isinstance(sess.get('forecast'), dict) else {}
        actual = prior.get('actual') if isinstance(prior.get('actual'), dict) else None
        if actual:
            actual = {'people': actual.get('week_one_audience'), 'measure': actual.get('measure'), 'aired': actual.get('aired'), 'note': actual.get('note')}
        fc = forecast_for_session(twin, sess, s3=s3, ledger=ledger, actual=actual)
        if prior.get('board_notes'):
            fc['board_notes'] = list(prior['board_notes'])   # our own board read for the title, kept across re-reads
        sess['forecast'] = fc
        return fc
    except Exception as e:
        print(f"[twin-forecast] skipped: {e}")
        return None


__all__ = ['forecast_for_session', 'attach_forecast', 'record_actual', 'load_ledger', 'save_ledger', 'factor_for', 'appeal', 'second_screen_rate',
           'uncalibrated_week_one', 'LEDGER_KEY', 'VERSION']
