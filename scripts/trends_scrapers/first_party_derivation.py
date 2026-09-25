"""Readings derived from the figures a platform publishes on its own rows.

Why this exists
---------------
Netflix was fixed by using the view counts Netflix publishes instead
of reasoning each title in isolation (`chart_set_reasoning`). Three
more panels carry first-party figures on every row and were throwing
them away:

  * Wattpad rows carry cumulative `reads`, `votes`, `chapters`,
    `is_completed` and the Originals flag. A story's own read count is
    a platform-published engagement figure; researching a fanfiction
    title on the open web is not, and it was the research that kept
    coming back implausibly low and being refused.
  * Libby rows carry `holds`, the library's own waitlist count.
  * Apple Books Comics rows carry no figure, but every row that kept
    failing is one VOLUME of a series whose other volumes ARE priced
    on the same chart. The set is the anchor: a volume sits between
    the volumes of its own series that bracket it on the chart.

So the rate and the share are reasoned ONCE per chart (one call for
the whole Wattpad chart set, no per-title research), and every row's
number is then derived from its own figures. Comics volumes are
placed inside their series on the same chart; an orphan series gets
one research call for the SERIES, never for a bare volume.

What a first-party reading is allowed to be
-------------------------------------------
A row on service X shows a reading for X derived from X's own
figures, or nothing. Never a figure derived from where the row sits
in a list. The one signal that looks like a position and is used here
is a volume's place RELATIVE TO ITS OWN SERIES on a ranked bestseller
chart, which is a bracket between two real readings of the same work,
the same way `chart_set_reasoning._bracket_unpublished` places an
unpublished Netflix title between its published neighbours.

A derived reading can honestly sit under 100. A story with 65
lifetime reads has a handful of readers a day, and a deep-catalog
Apple Comics volume sells a few dozen copies. Those rows carry
`est_basis='first_party'`, which is what tells the coverage gate and
the render-side coverage pass that the number is a derivation from the
platform's own count rather than a failed research call, so the
sub-100 credibility floor does not apply to it.

Every value passes through natural last digits, sits under the
platform's daily cap, and is written through the normal snapshot
boundary so the 60-day distinctness backstop gets the last word.

Never queries clickstream (`trends-rankers-never-clickstream.mdc`).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

FIRST_PARTY_BASIS = 'first_party'

_MODEL = (os.environ.get('FIRST_PARTY_PARAMS_MODEL')
          or os.environ.get('STREAM_ESTIMATES_MODEL_HI')
          or 'claude-sonnet-4-5')
_MAX_TOKENS = 3000


def _se():
    try:
        from . import stream_estimates as se
    except ImportError:
        from scripts.trends_scrapers import stream_estimates as se
    return se


# ===========================================================================
# Wattpad
# ===========================================================================
# Rail slug -> (label, rail class). The class picks the read-rate
# table: the Hot list is Wattpad's own velocity ranking so its stories
# are being read NOW; the Originals rail is an editorial shelf of
# completed paid stories in their long tail; a genre rail is the
# platform's hot-for-tag module topped up with new-for-tag stories.
WATTPAD_RAILS = (
    ('wattpad_hot',          'Wattpad - Hot Stories',  'hot'),
    ('wattpad_originals',    'Wattpad - Originals',    'originals'),
    ('wattpad_romance',      'Wattpad - Romance',      'genre'),
    ('wattpad_teen_fiction', 'Wattpad - Teen Fiction', 'genre'),
    ('wattpad_fanfiction',   'Wattpad - Fanfiction',   'genre'),
    ('wattpad_fantasy',      'Wattpad - Fantasy',      'genre'),
)
_WATTPAD_PLATFORM = 'wattpad'
_WATTPAD_KIND = 'wattpad_story'

# Published anchors the parameter call reasons from, and the defaults
# used when the call is unavailable. Wattpad reports 90M+ monthly
# users (company figure, 2024); its US share of traffic sits near a
# fifth (Similarweb / Statista class traffic shares), higher on the
# Originals shelf, which Wattpad Studios positions to a North American
# audience. A read on Wattpad is one PART opened, so readers a day are
# part-reads a day over the parts an active reader opens.
DEFAULT_WATTPAD_PARAMS: dict[str, Any] = {
    'us_share': 0.20,
    'us_share_originals': 0.30,
    'parts_per_reader_day': 3.0,
    # Relative read velocity falls with size: a story at 10M lifetime
    # reads does not add 1% of that a day the way a 50K story can.
    # frac(reads) = base(rail, state) x (reads / 100K) ^ -decay
    'decay': 0.30,
    # A story voted more heavily per read than its chart peers is being
    # read by a more engaged, more current audience.
    'votes_elasticity': 0.25,
    'day_factor': 1.0,
    # Daily part-reads as a fraction of lifetime reads, at the 100K
    # reference size, by rail class and completion state.
    'rails': {
        'hot':       {'ongoing': 0.030, 'completed': 0.012},
        'originals': {'ongoing': 0.006, 'completed': 0.0035},
        'genre':     {'ongoing': 0.010, 'completed': 0.0035},
    },
}

_BOUNDS = {
    'us_share':             (0.12, 0.45),
    'us_share_originals':   (0.15, 0.60),
    'parts_per_reader_day': (1.5, 6.0),
    'decay':                (0.10, 0.45),
    'votes_elasticity':     (0.0, 0.50),
    'day_factor':           (0.80, 1.25),
}
_FRAC_BOUNDS = (0.0003, 0.15)

_params_cache: dict[str, dict] = {}

# Where the parameter set the chart was derived with is kept on the
# stream_estimates snapshot, so the next run reasons from it instead
# of from a blank page and the panel's level does not drift with the
# wording of one reply.
PARAMS_KEY = 'first_party_wattpad_params'


def wattpad_rows_from_snapshot(snap: dict) -> list[dict]:
    """One record per story across the six rails, carrying the union
    of its native figures and the rails (with positions) it sits on."""
    se = _se()
    per: dict[str, dict] = {}
    for slug, label, klass in WATTPAD_RAILS:
        panel = ((snap or {}).get('sources') or {}).get(slug) or {}
        for i, it in enumerate(panel.get('items') or []):
            title = (it.get('title') or '').strip()
            artist = (it.get('artist') or it.get('author') or '').strip()
            if not title:
                continue
            key = se._lookup_key(_WATTPAD_KIND, title, artist)
            if not se._cp_normalize(f'{title} {artist}'):
                continue
            try:
                rank = int(it.get('rank') or (i + 1))
            except (TypeError, ValueError):
                rank = i + 1

            def _i(v):
                try:
                    return max(0, int(v or 0))
                except (TypeError, ValueError):
                    return 0

            rec = per.setdefault(key, {
                'key': key, 'title': title, 'artist': artist,
                'reads': 0, 'votes': 0, 'chapters': 0,
                'completed': False, 'is_new': False, 'originals': False,
                'rails': {}, 'classes': set(), 'chart_labels': [],
                'best_rank': rank,
                'image': it.get('cover_url') or it.get('image'),
                'url': it.get('story_url') or it.get('url'),
            })
            rec['reads'] = max(rec['reads'], _i(it.get('reads')))
            rec['votes'] = max(rec['votes'], _i(it.get('votes')))
            rec['chapters'] = max(rec['chapters'], _i(it.get('chapters')))
            rec['completed'] = rec['completed'] or bool(it.get('is_completed'))
            rec['is_new'] = rec['is_new'] or bool(it.get('is_new'))
            rec['originals'] = rec['originals'] or bool(
                it.get('wattpad_originals_flag'))
            rec['rails'][slug] = rank
            rec['classes'].add(klass)
            lab = f'{label} #{rank}'
            if lab not in rec['chart_labels']:
                rec['chart_labels'].append(lab)
            rec['best_rank'] = min(rec['best_rank'], rank)
    return list(per.values())


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return min(hi, max(lo, f))


def _validate_params(raw: Any) -> Optional[dict]:
    """Clamp a returned parameter set into its bands. None when the
    reply is not a parameter set at all."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for k, (lo, hi) in _BOUNDS.items():
        out[k] = _clamp(raw.get(k), lo, hi, DEFAULT_WATTPAD_PARAMS[k])
    rails_in = raw.get('rails') if isinstance(raw.get('rails'), dict) else {}
    rails_out: dict[str, dict] = {}
    for klass, dflt in DEFAULT_WATTPAD_PARAMS['rails'].items():
        blk = rails_in.get(klass) if isinstance(rails_in.get(klass), dict) else {}
        rails_out[klass] = {
            state: _clamp(blk.get(state), _FRAC_BOUNDS[0], _FRAC_BOUNDS[1],
                          dflt[state])
            for state in ('ongoing', 'completed')
        }
        # An ongoing story on a rail is being read at least as fast as
        # a completed one on the same rail.
        if rails_out[klass]['ongoing'] < rails_out[klass]['completed']:
            rails_out[klass]['ongoing'] = rails_out[klass]['completed']
    out['rails'] = rails_out
    if out['us_share_originals'] < out['us_share']:
        out['us_share_originals'] = out['us_share']
    out['reason'] = str(raw.get('reason') or '')[:600]
    return out


def _rail_stats(rows: list[dict]) -> dict[str, dict]:
    """Per rail class: what the chart set looks like, for the prompt."""
    out: dict[str, dict] = {}
    for _slug, _label, klass in WATTPAD_RAILS:
        sub = [r for r in rows if klass in r['classes']]
        if not sub:
            continue
        reads = sorted(r['reads'] for r in sub)
        n = len(reads)
        q = lambda p: reads[min(n - 1, int(p * n))]  # noqa: E731
        ratios = [r['votes'] / r['reads'] for r in sub
                  if r['votes'] > 0 and r['reads'] > 0]
        out[klass] = {
            'stories': n,
            'reads_p10': q(0.10), 'reads_median': q(0.50),
            'reads_p90': q(0.90),
            'share_completed': round(sum(1 for r in sub if r['completed'])
                                     / n, 2),
            'share_under_1k_reads': round(
                sum(1 for r in sub if r['reads'] < 1000) / n, 2),
            'median_votes_per_read': round(statistics.median(ratios), 4)
            if ratios else None,
        }
    return out


def _params_prompt(stats: dict, target_date_iso: str,
                   yesterday: Optional[dict] = None) -> str:
    try:
        weekday = date.fromisoformat(target_date_iso).strftime('%A')
    except (TypeError, ValueError):
        weekday = 'unknown'
    d = DEFAULT_WATTPAD_PARAMS
    if yesterday:
        ref = (f"YESTERDAY'S PARAMETERS, reasoned the same way: "
               f"{json.dumps({k: v for k, v in yesterday.items() if k in _BOUNDS})}, "
               f"rails {json.dumps(yesterday.get('rails') or {})}. A "
               f"level is a measurement, not a draft: keep each "
               f"parameter where it was unless the chart set above or "
               f"the day gives you a reason to move it, and say what "
               f"moved and why.")
    else:
        ref = (f"Reference defaults if you have no reason to move one: "
               f"{json.dumps({k: v for k, v in d.items() if k != 'rails'})}, "
               f"rails {json.dumps(d['rails'])}.")
    return (
        f"You are setting the parameters that turn every Wattpad story's "
        f"OWN published figures into its daily US readers for "
        f"{target_date_iso} ({weekday}). You set the parameters ONCE for "
        f"the whole chart set; the per-story arithmetic is then done "
        f"mechanically from each story's cumulative reads, votes, part "
        f"count and completion state. You never size an individual "
        f"story.\n\n"
        f"THE ARITHMETIC YOUR PARAMETERS FEED, per story:\n"
        f"  frac = rails[class][state] x (reads / 100000) ^ (-decay)\n"
        f"        (daily part-reads as a share of lifetime reads; the\n"
        f"         power term makes big backlist stories turn over more\n"
        f"         slowly than small current ones)\n"
        f"  frac x= (votes_per_read / chart median) ^ votes_elasticity\n"
        f"        (only when the row carries a vote count)\n"
        f"  daily_part_reads = reads x frac x day_factor\n"
        f"  daily_readers    = daily_part_reads / parts_per_reader_day\n"
        f"  daily_US_readers = daily_readers x us_share "
        f"(us_share_originals on a Wattpad Original)\n\n"
        f"THE THREE RAIL CLASSES:\n"
        f"  hot        Wattpad's own Hot list: its velocity ranking, so "
        f"every story on it is being read heavily right now.\n"
        f"  originals  Wattpad Originals shelf: completed, studio-backed "
        f"paid stories, mostly in their long tail, North-America-led "
        f"audience.\n"
        f"  genre      a genre browse rail: the platform's hot-for-tag "
        f"module (about 20 stories) topped up with new-for-tag stories "
        f"that have very few reads yet.\n\n"
        f"WHAT THE CHART SET LOOKS LIKE TODAY (from the rows themselves):\n"
        f"{json.dumps(stats, indent=2)}\n\n"
        f"PUBLISHED ANCHORS. Wattpad reports 90M+ monthly users "
        f"worldwide. The United States is its largest single market but "
        f"a minority of it: published traffic shares put the US near a "
        f"fifth of the platform, with the Originals shelf skewing "
        f"higher. A Wattpad 'read' is one PART opened, not one reader, "
        f"and an active reader opens a few parts a day. A completed "
        f"story with millions of lifetime reads adds a small fraction "
        f"of that a day; a story that is hot right now with tens of "
        f"thousands of reads can add several percent of its lifetime "
        f"total in a single day. Reason each parameter from what you "
        f"know and state the reasoning briefly.\n\n"
        f"BANDS (hard, the code clamps to them):\n"
        f"  us_share {_BOUNDS['us_share'][0]}-{_BOUNDS['us_share'][1]}, "
        f"us_share_originals {_BOUNDS['us_share_originals'][0]}-"
        f"{_BOUNDS['us_share_originals'][1]}, "
        f"parts_per_reader_day {_BOUNDS['parts_per_reader_day'][0]}-"
        f"{_BOUNDS['parts_per_reader_day'][1]}, "
        f"decay {_BOUNDS['decay'][0]}-{_BOUNDS['decay'][1]}, "
        f"votes_elasticity {_BOUNDS['votes_elasticity'][0]}-"
        f"{_BOUNDS['votes_elasticity'][1]}, "
        f"day_factor {_BOUNDS['day_factor'][0]}-{_BOUNDS['day_factor'][1]} "
        f"(1.0 is a flat day; reason it from the weekday), every rails "
        f"fraction {_FRAC_BOUNDS[0]}-{_FRAC_BOUNDS[1]}.\n"
        f"{ref}\n\n"
        f"Return ONLY JSON:\n"
        f'{{"us_share": <float>, "us_share_originals": <float>, '
        f'"parts_per_reader_day": <float>, "decay": <float>, '
        f'"votes_elasticity": <float>, "day_factor": <float>, '
        f'"rails": {{"hot": {{"ongoing": <f>, "completed": <f>}}, '
        f'"originals": {{"ongoing": <f>, "completed": <f>}}, '
        f'"genre": {{"ongoing": <f>, "completed": <f>}}}}, '
        f'"reason": "<three or four sentences>"}}'
    )


def _extract_json(text: str) -> Optional[dict]:
    try:
        from .chart_set_reasoning import _extract_json as _x
    except ImportError:
        from scripts.trends_scrapers.chart_set_reasoning import \
            _extract_json as _x
    return _x(text)


def _meter(spend_monitor, resp) -> None:
    """Meter the parameter call the same way the research calls are."""
    if spend_monitor is None or resp is None:
        return
    try:
        spend_monitor.record_response(getattr(resp, 'usage', None),
                                      model=_MODEL, batch=False)
    except Exception:
        pass


def reason_wattpad_params(client, rows: list[dict], target_date_iso: str,
                          spend_monitor=None,
                          yesterday: Optional[dict] = None) -> dict:
    """One call per run for the whole Wattpad chart set. Falls back to
    yesterday's set, then to the documented defaults, so a failed call
    never leaves a row blank; `basis` says which it was."""
    cached = _params_cache.get(target_date_iso)
    if cached:
        return cached
    prior = _validate_params(yesterday) if isinstance(yesterday, dict) \
        else None
    if prior:
        params = prior
        params['basis'] = 'yesterday'
    else:
        params = dict(DEFAULT_WATTPAD_PARAMS)
        params['rails'] = {k: dict(v) for k, v in
                           DEFAULT_WATTPAD_PARAMS['rails'].items()}
        params['basis'] = 'defaults'
        params['reason'] = ''
    if client is None or not rows:
        _params_cache[target_date_iso] = params
        return params
    prompt = _params_prompt(_rail_stats(rows), target_date_iso,
                            yesterday=prior)
    try:
        try:
            resp = client.messages.create(
                model=_MODEL, max_tokens=_MAX_TOKENS, temperature=0,
                messages=[{'role': 'user', 'content': prompt}])
        except TypeError:
            resp = client.messages.create(
                model=_MODEL, max_tokens=_MAX_TOKENS,
                messages=[{'role': 'user', 'content': prompt}])
        _meter(spend_monitor, resp)
        text = ''.join(getattr(b, 'text', '') or ''
                       for b in (getattr(resp, 'content', None) or [])
                       if getattr(b, 'type', None) == 'text')
        got = _validate_params(_extract_json(text))
        if got:
            got['basis'] = 'reasoned'
            params = got
            logger.info("first_party wattpad: parameters reasoned for %s: "
                        "us_share %.2f (originals %.2f), parts/reader %.1f, "
                        "decay %.2f, day %.2f, rails %s; %s",
                        target_date_iso, params['us_share'],
                        params['us_share_originals'],
                        params['parts_per_reader_day'], params['decay'],
                        params['day_factor'], json.dumps(params['rails']),
                        (params.get('reason') or '')[:300])
        else:
            logger.warning("first_party wattpad: parameter call returned "
                           "no usable set; using %s", params['basis'])
    except Exception as e:
        logger.warning("first_party wattpad: parameter call failed (%s); "
                       "using %s", e, params['basis'])
    params['target_date'] = target_date_iso
    _params_cache[target_date_iso] = params
    return params


def _wattpad_frac(rec: dict, params: dict, median_ratio: Optional[float]
                  ) -> float:
    state = 'completed' if rec['completed'] else 'ongoing'
    base = max(params['rails'][k][state] for k in rec['classes']) \
        if rec['classes'] else params['rails']['genre'][state]
    ref = max(rec['reads'], 200) / 100_000.0
    size_adj = min(6.0, max(0.20, ref ** (-params['decay'])))
    frac = base * size_adj
    if rec['votes'] > 0 and rec['reads'] > 0 and median_ratio:
        ratio = rec['votes'] / rec['reads']
        adj = (ratio / median_ratio) ** params['votes_elasticity']
        frac *= min(1.6, max(0.6, adj))
    return min(0.6, max(0.0001, frac))


def derive_wattpad(rows: list[dict], params: dict, target_date_iso: str,
                   ) -> dict[str, dict]:
    """{item key: full store item} for every story with reads > 0."""
    se = _se()
    ceiling_weekly = 0
    for p in se._WATTPAD_PLATFORMS:
        if p.get('key') == _WATTPAD_PLATFORM:
            ceiling_weekly = int(p.get('ceiling') or 0)
    daily_cap = max(1, ceiling_weekly // 7) if ceiling_weekly else 0

    ratios = [r['votes'] / r['reads'] for r in rows
              if r['votes'] > 0 and r['reads'] > 0]
    median_ratio = statistics.median(ratios) if ratios else None
    salt = f'{target_date_iso}|wattpad|{FIRST_PARTY_BASIS}'

    out: dict[str, dict] = {}
    for rec in rows:
        if rec['reads'] <= 0:
            continue
        frac = _wattpad_frac(rec, params, median_ratio)
        part_reads = rec['reads'] * frac * params['day_factor']
        readers = part_reads / params['parts_per_reader_day']
        share = params['us_share_originals'] if rec['originals'] \
            else params['us_share']
        us = readers * share
        # Two stories with identical figures must not land on one
        # integer; a small salted factor, never a level change.
        us *= 0.94 + se._h01(f'{rec["key"]}|{salt}|fp') * 0.12
        mid = max(1, int(round(us)))
        if daily_cap and mid > daily_cap:
            mid = int(daily_cap * (0.85 + se._h01(f'{rec["key"]}|cap') * 0.1))
        mid = max(1, se._natural_last_digits(mid, rec['key'], salt))
        low = max(1, se._natural_last_digits(int(round(mid * 0.74)),
                                             rec['key'], f'{salt}|low'))
        high = max(mid, se._natural_last_digits(int(round(mid * 1.36)),
                                                rec['key'], f'{salt}|high'))
        low = min(low, mid)
        state = 'complete' if rec['completed'] else 'ongoing'
        parts = f', {rec["chapters"]} parts' if rec['chapters'] else ''
        votes = f' and {rec["votes"]:,} votes' if rec['votes'] else ''
        orig = ' Wattpad Original,' if rec['originals'] else ''
        note = (f'Read from the story\'s own Wattpad figures: '
                f'{rec["reads"]:,} lifetime reads{votes}{parts}, '
                f'{state}.{orig} Current read rate for a story of this '
                f'size and state on this chart, US share of Wattpad '
                f'readers applied.')
        conf = 'medium' if rec['reads'] >= 1000 else 'low'
        block = {
            'us_estimate': mid, 'us_estimate_low': low,
            'us_estimate_high': high, 'confidence': conf,
            'note': note, 'est_basis': FIRST_PARTY_BASIS,
            'as_of_date': target_date_iso,
        }
        out[rec['key']] = {
            'kind': _WATTPAD_KIND,
            'display_title': rec['title'],
            'artist': rec['artist'],
            'chart_labels': list(rec['chart_labels']),
            'best_rank': rec['best_rank'],
            'image': rec.get('image'),
            'url': rec.get('url'),
            'us_estimate': mid, 'us_estimate_low': low,
            'us_estimate_high': high,
            'unit_label': se._default_unit_for_kind(_WATTPAD_KIND),
            'confidence': conf,
            'method': note,
            'sources': [],
            'as_of_date': target_date_iso,
            'est_basis': FIRST_PARTY_BASIS,
            'first_party': {
                'reads': rec['reads'], 'votes': rec['votes'],
                'chapters': rec['chapters'], 'completed': rec['completed'],
                'originals': rec['originals'],
                'params_basis': params.get('basis'),
            },
            'by_platform': {_WATTPAD_PLATFORM: block},
        }
    return out


def _carry_trend(new_item: dict, old_item: dict, platform: str) -> None:
    """Keep the day-over-day chip honest when a reading is replaced
    inside the day: the previous day's value stays what it was, and
    direction and delta are recomputed against the new reading."""
    se = _se()
    for tgt, src in ((new_item, old_item),
                     ((new_item.get('by_platform') or {}).get(platform),
                      (old_item.get('by_platform') or {}).get(platform))):
        if not isinstance(tgt, dict) or not isinstance(src, dict):
            continue
        try:
            prev = int(src.get('prev_estimate') or 0)
            cur = int(tgt.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        if prev <= 0 or cur <= 0:
            continue
        direction, delta = se._direction_and_delta(cur, prev)
        tgt['prev_estimate'] = prev
        if src.get('prev_date'):
            tgt['prev_date'] = src['prev_date']
        tgt['direction'] = direction
        tgt['delta_pct'] = delta


def _spearman(pairs: list[tuple[int, int]]) -> Optional[float]:
    """Rank correlation between two orderings, or None when too few."""
    n = len(pairs)
    if n < 5:
        return None

    def _ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1.0
        return r

    a = _ranks([p[0] for p in pairs])
    b = _ranks([p[1] for p in pairs])
    d2 = sum((x - y) ** 2 for x, y in zip(a, b))
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


def wattpad_order_check(rows: list[dict], derived: dict[str, dict]
                        ) -> dict[str, Any]:
    """How the derived values sort against Wattpad's own order on each
    rail. Reported, not enforced: the Hot list ranks by recent read
    velocity and the figure we derive from is lifetime reads, so the
    two are expected to disagree and forcing them together would put
    a position-derived number back on the rail."""
    out: dict[str, Any] = {}
    for slug, _label, _klass in WATTPAD_RAILS:
        pairs = []
        for rec in rows:
            pos = rec['rails'].get(slug)
            it = derived.get(rec['key'])
            if pos and it:
                # Higher value should mean an earlier position, so
                # compare position against the NEGATIVE value.
                pairs.append((pos, -int(it['us_estimate'])))
        rho = _spearman(pairs)
        if rho is not None:
            out[slug] = round(rho, 3)
    return out


def run_wattpad(researched: dict[str, dict], target_date_iso: str, *,
                client=None, spend_monitor=None,
                only_keys: Optional[set] = None,
                yesterday_params: Optional[dict] = None) -> dict[str, Any]:
    """Derive every Wattpad row from its own figures and write the
    items into `researched` (replacing per-title research). Returns
    stats, including the `params` the set was derived with so the
    caller can keep them on the snapshot under `PARAMS_KEY`.
    `only_keys` restricts the write to those keys."""
    se = _se()
    stats: dict[str, Any] = {'rows': 0, 'derived': 0, 'written': 0,
                             'no_reads': [], 'params_basis': 'none',
                             'order_check': {}, 'params': None}
    snap = se._read_snapshot('wattpad_charts') or {}
    rows = wattpad_rows_from_snapshot(snap)
    stats['rows'] = len(rows)
    if not rows:
        return stats
    if yesterday_params is None:
        yesterday_params = (se._read_snapshot('stream_estimates') or {}
                            ).get(PARAMS_KEY)
    params = reason_wattpad_params(client, rows, target_date_iso,
                                   spend_monitor=spend_monitor,
                                   yesterday=yesterday_params)
    stats['params_basis'] = params.get('basis')
    stats['params'] = params
    derived = derive_wattpad(rows, params, target_date_iso)
    stats['derived'] = len(derived)
    stats['no_reads'] = [r['title'] for r in rows if r['reads'] <= 0]
    stats['order_check'] = wattpad_order_check(rows, derived)
    for key, it in derived.items():
        if only_keys is not None and key not in only_keys:
            continue
        prev = researched.get(key)
        if isinstance(prev, dict):
            # Keep the trail fields the trend attach and the window
            # sum read; everything about the level is new.
            for f in ('prev_day_estimate', 'prev_day_date',
                      'window_days_covered', 'window_days_total'):
                if prev.get(f) is not None and it.get(f) is None:
                    it[f] = prev[f]
            _carry_trend(it, prev, _WATTPAD_PLATFORM)
        researched[key] = it
        stats['written'] += 1
    logger.info("first_party wattpad: %d stories on the chart set, %d "
                "derived from their own reads, %d written (%s params), "
                "%d with no reads at all; order check vs Wattpad's own "
                "rails %s", stats['rows'], stats['derived'],
                stats['written'], stats['params_basis'],
                len(stats['no_reads']), json.dumps(stats['order_check']))
    return stats


# ===========================================================================
# Comics: a volume sits inside its series on the same chart
# ===========================================================================
COMICS_PANELS = (
    ('amazon_kindle', 'Amazon Comics'),
    ('apple_comics',  'Apple Books Comics'),
    ('libby_comics',  'Libby Comics'),
)
_COMIC_KIND = 'comic'

# Volume / issue / part markers, and the packaging words that make two
# editions of one series read as two series.
_NUM_WORD = (r'(?:\d+[a-z]?|one|two|three|four|five|six|seven|eight|nine|'
             r'ten|eleven|twelve|[ivx]{1,4})')
_MARKER_RE = re.compile(
    r"""
    (?:[\s,:\-]*\(?\b(?:vol(?:ume)?s?\.?|v\.|part|pt\.?|book|bk\.?|
        chapter|ch\.?|issue|tome)\s*""" + _NUM_WORD + r"""\b\)?)
    | (?:\s*\#\s*\d+\b)
    | (?:\s*\(\d{4}\s*-?\s*\d{0,4}\))
    | (?:\s*\((?:manga|comic|comics|light\ novel|omnibus|graphic\ novel)\))
    | (?:\b\d+\s*-\s*in\s*-\s*\d+\s+edition\b)
    | (?:\b(?:omnibus|compendium|box\ set)\b)
    """, re.IGNORECASE | re.VERBOSE)


def series_key(title: str) -> tuple[str, bool]:
    """(normalized series key, True when the title carried a volume
    marker). A title with no marker is its own series."""
    se = _se()
    t = title or ''
    m = _MARKER_RE.search(t)
    had_marker = bool(m)
    if had_marker:
        # "Absolute Batman Vol. 3: Devil's Workshop" -> a subtitle AFTER
        # the marker names the volume, not the series. A colon before
        # the marker ("Avatar: The Last Airbender - The Search Part 3")
        # is part of the series name and stays.
        cut = t.find(':', m.end())
        if cut > 0:
            t = t[:cut]
    stripped = _MARKER_RE.sub(' ', t)
    key = se._cp_normalize(stripped)
    if not key:
        key = se._cp_normalize(title or '')
    return key, had_marker


def series_display(title: str) -> str:
    """The series name as it would be written: the title with its
    volume marker and any volume subtitle removed."""
    t = title or ''
    m = _MARKER_RE.search(t)
    if m:
        cut = t.find(':', m.end())
        if cut > 0:
            t = t[:cut]
    t = _MARKER_RE.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' ,:-')
    return t or (title or '').strip()


def _block_state(item: Optional[dict], platform: str) -> tuple[str, int]:
    """('priced' | 'unpriced', value). A block under 100 from research
    reads as unpriced, the same way the gate reads it; a first-party
    block counts at any positive value."""
    if not isinstance(item, dict):
        return 'unpriced', 0
    blk = (item.get('by_platform') or {}).get(platform)
    if not isinstance(blk, dict):
        return 'unpriced', 0
    try:
        v = int(blk.get('us_estimate') or 0)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        return 'unpriced', 0
    if v < 100 and blk.get('est_basis') != FIRST_PARTY_BASIS:
        return 'unpriced', v
    return 'priced', v


def _put_block(researched: dict, key: str, seed: dict, platform: str,
               value: int, note: str, target_date_iso: str,
               confidence: str = 'medium') -> bool:
    """Write one platform block, creating the item when absent, and
    let the aggregate move by exactly the block's change."""
    se = _se()
    value = max(1, int(value))
    it = researched.get(key)
    created = False
    if not isinstance(it, dict):
        it = {
            'kind': seed.get('kind') or _COMIC_KIND,
            'display_title': seed.get('title') or '',
            'artist': seed.get('artist') or '',
            'chart_labels': list(seed.get('chart_labels') or []),
            'best_rank': seed.get('best_rank'),
            'image': seed.get('image'),
            'url': seed.get('url'),
            'us_estimate': 0, 'us_estimate_low': 0, 'us_estimate_high': 0,
            'unit_label': se._default_unit_for_kind(seed.get('kind')
                                                    or _COMIC_KIND),
            'confidence': confidence,
            'method': note,
            'sources': [],
            'as_of_date': target_date_iso,
            'by_platform': {},
        }
        researched[key] = it
        created = True
    bp = it.setdefault('by_platform', {})
    if not isinstance(bp, dict):
        bp = {}
        it['by_platform'] = bp
    old = 0
    prev = bp.get(platform)
    if isinstance(prev, dict):
        try:
            old = int(prev.get('us_estimate') or 0)
        except (TypeError, ValueError):
            old = 0
    salt = f'{target_date_iso}|{platform}|{FIRST_PARTY_BASIS}'
    low = min(value, max(1, se._natural_last_digits(
        int(round(value * 0.76)), key, f'{salt}|low')))
    high = max(value, se._natural_last_digits(
        int(round(value * 1.34)), key, f'{salt}|high'))
    new_blk = {
        'us_estimate': value, 'us_estimate_low': low,
        'us_estimate_high': high, 'confidence': confidence,
        'note': note, 'est_basis': FIRST_PARTY_BASIS,
        'as_of_date': target_date_iso,
    }
    if isinstance(prev, dict):
        try:
            p = int(prev.get('prev_estimate') or 0)
        except (TypeError, ValueError):
            p = 0
        if p > 0:
            d, dp = se._direction_and_delta(value, p)
            new_blk.update({'prev_estimate': p, 'direction': d,
                            'delta_pct': dp})
            if prev.get('prev_date'):
                new_blk['prev_date'] = prev['prev_date']
    bp[platform] = new_blk
    try:
        agg = int(it.get('us_estimate') or 0)
    except (TypeError, ValueError):
        agg = 0
    new_agg = max(1, agg - old + value)
    it['us_estimate'] = se._natural_last_digits(new_agg, key, f'{salt}|agg') \
        if not created else value
    try:
        lo = int(it.get('us_estimate_low') or 0)
        hi = int(it.get('us_estimate_high') or 0)
    except (TypeError, ValueError):
        lo = hi = 0
    it['us_estimate_low'] = min(it['us_estimate'], lo if lo > 0 else low)
    it['us_estimate_high'] = max(it['us_estimate'], hi if hi > 0 else high)
    if not it.get('as_of_date'):
        it['as_of_date'] = target_date_iso
    return True


def _place_between(upper: Optional[int], lower: Optional[int], n: int,
                   keys: list[str], salt: str, cap: int) -> list[int]:
    """Values for `n` unpriced rows sharing one interval, descending,
    spaced unevenly on the log scale. Edges step off the one anchor
    they have by a drawn margin."""
    se = _se()
    vals: list[int] = []
    if upper is None and lower is None:
        return vals
    if upper is None:
        # Above every priced sibling below: a drawn step up each.
        base = float(lower)
        steps = []
        for k in reversed(keys):
            base *= 1.06 + se._h01(f'{k}|{salt}|up') * 0.22
            steps.append(base)
        vals = [int(round(v)) for v in reversed(steps)]
    elif lower is None:
        base = float(upper)
        for k in keys:
            base *= 0.70 + se._h01(f'{k}|{salt}|down') * 0.22
            vals.append(int(round(max(base, 1.0))))
    else:
        hi, lo = float(upper), float(lower)
        if hi <= lo:
            mid = math.sqrt(max(hi, 1.0) * max(lo, 1.0))
            hi, lo = mid * 1.05, mid * 0.95
        lhi, llo = math.log(max(hi, 1.0)), math.log(max(lo, 1.0))
        weights = [0.55 + se._h01(f'{k}|{salt}|w') for k in keys]
        weights.append(0.55 + se._h01(f'{keys[-1]}|{salt}|wtail'))
        total = sum(weights) or 1.0
        acc = 0.0
        for w in weights[:-1]:
            acc += w
            vals.append(int(round(math.exp(lhi - (lhi - llo) * acc / total))))
    out = []
    for k, v in zip(keys, vals):
        if cap and v >= cap:
            v = int(cap * (0.80 - se._h01(f'{k}|{salt}|cap') * 0.2))
        out.append(max(1, se._natural_last_digits(v, k, salt)))
    # Strict descent after the digit draw.
    for i in range(1, len(out)):
        if out[i] >= out[i - 1]:
            out[i] = max(1, out[i - 1] - 1 - int(se._h01(f'{keys[i]}|{salt}|s')
                                                 * 5))
    return out


def _holds_scale(panel_rows: list[dict], researched: dict,
                 platform: str) -> float:
    """Daily US library borrows per LA County hold, read off the
    panel's own priced rows. LA County is about three percent of the
    US, so the population scale alone is near 33; the band keeps a
    thin panel from pulling it anywhere absurd."""
    ratios = []
    for r in panel_rows:
        holds = r.get('holds') or 0
        if holds <= 0:
            continue
        state, v = _block_state(researched.get(r['key']), platform)
        if state == 'priced' and v > 0:
            ratios.append(v / float(holds))
    if len(ratios) >= 5:
        return min(80.0, max(8.0, statistics.median(ratios)))
    return 30.0


def _panel_rows(comics_snap: dict, slug: str, label: str) -> list[dict]:
    se = _se()
    panel = ((comics_snap or {}).get('sources') or {}).get(slug) or {}
    rows = []
    for i, it in enumerate(panel.get('items') or []):
        title = (it.get('title') or '').strip()
        artist = (it.get('artist') or '').strip()
        if not title or not se._cp_normalize(f'{title} {artist}'):
            continue
        try:
            pos = int(it.get('rank') or (i + 1))
        except (TypeError, ValueError):
            pos = i + 1
        try:
            holds = int(it.get('holds') or 0)
        except (TypeError, ValueError):
            holds = 0
        skey, marked = series_key(title)
        rows.append({
            'key': se._lookup_key(_COMIC_KIND, title, artist),
            'title': title, 'artist': artist, 'pos': pos,
            'holds': holds, 'series': skey, 'marked': marked,
            'kind': _COMIC_KIND,
            'chart_labels': [f'{label} #{pos}'], 'best_rank': pos,
            'image': it.get('image'), 'url': it.get('url'),
        })
    rows.sort(key=lambda r: r['pos'])
    return rows


def _daily_cap(platform: str) -> int:
    se = _se()
    for p in se._COMICS_PLATFORMS:
        if p.get('key') == platform:
            weekly = int(p.get('ceiling') or 0)
            return max(1, weekly // 7) if weekly else 0
    return 0


def run_comics(researched: dict[str, dict], target_date_iso: str, *,
               client=None, spend_monitor=None,
               only_keys: Optional[set] = None,
               research_orphans: bool = True) -> dict[str, Any]:
    """Give every unpriced comics row a reading from its own panel's
    first-party figures: Libby holds where the row has them, else its
    place inside its own series on the chart, else one research call
    for the SERIES. Rows nothing can be derived for are named.

    `only_keys` limits the rows considered to those keys (the gate
    passes what the board rendered blank); None means every unpriced
    row on the three panels."""
    se = _se()
    stats: dict[str, Any] = {'unpriced': 0, 'holds': 0, 'series': 0,
                             'orphan_research': 0, 'orphan_calls': 0,
                             'cannot': [], 'trail': [], 'written_keys': []}
    comics_snap = se._read_snapshot('comics_charts') or {}
    if not comics_snap:
        return stats

    for platform, label in COMICS_PANELS:
        rows = _panel_rows(comics_snap, platform, label)
        if not rows:
            continue
        cap = _daily_cap(platform)
        salt = f'{target_date_iso}|{platform}|{FIRST_PARTY_BASIS}'
        wanted = [r for r in rows
                  if (only_keys is None or r['key'] in only_keys)
                  and _block_state(researched.get(r['key']),
                                   platform)[0] == 'unpriced']
        if not wanted:
            continue
        stats['unpriced'] += len(wanted)
        wanted_keys = {r['key'] for r in wanted}

        # 1. Libby: the row's own hold count is the demand figure.
        if platform == 'libby_comics':
            scale = _holds_scale(rows, researched, platform)
            for r in list(wanted):
                if r['holds'] <= 0:
                    continue
                v = int(round(r['holds'] * scale
                              * (0.92 + se._h01(f'{r["key"]}|{salt}|h') * 0.16)))
                if cap and v >= cap:
                    v = int(cap * 0.8)
                v = max(1, se._natural_last_digits(v, r['key'], salt))
                note = (f'Read from the title\'s own Libby waitlist: '
                        f'{r["holds"]:,} holds at a large US library '
                        f'system, scaled to US public-library borrowing.')
                _put_block(researched, r['key'], r, platform, v, note,
                           target_date_iso, confidence='low')
                stats['holds'] += 1
                stats['written_keys'].append(r['key'])
                stats['trail'].append((platform, r['title'], v, 'holds'))
                wanted_keys.discard(r['key'])
            wanted = [r for r in wanted if r['key'] in wanted_keys]
            if not wanted:
                continue

        # 2. A volume inside its series on this chart.
        by_series: dict[str, list[dict]] = {}
        for r in rows:
            by_series.setdefault(r['series'], []).append(r)
        orphans: list[tuple[str, list[dict]]] = []
        for skey, members in by_series.items():
            members = sorted(members, key=lambda r: r['pos'])
            todo = [m for m in members if m['key'] in wanted_keys]
            if not todo:
                continue
            anchors = {m['key']: _block_state(researched.get(m['key']),
                                              platform)[1]
                       for m in members
                       if _block_state(researched.get(m['key']),
                                       platform)[0] == 'priced'}
            if not anchors:
                orphans.append((skey, members))
                continue
            n = len(members)
            i = 0
            while i < n:
                if members[i]['key'] in anchors:
                    i += 1
                    continue
                j = i
                while j < n and members[j]['key'] not in anchors:
                    j += 1
                run = members[i:j]
                upper = None
                for k in range(i - 1, -1, -1):
                    a = anchors.get(members[k]['key'])
                    if a:
                        upper = a if upper is None else min(upper, a)
                lower = None
                for k in range(j, n):
                    a = anchors.get(members[k]['key'])
                    if a:
                        lower = a if lower is None else max(lower, a)
                vals = _place_between(upper, lower, len(run),
                                      [m['key'] for m in run], salt, cap)
                for m, v in zip(run, vals):
                    if m['key'] not in wanted_keys:
                        continue
                    sibs = len(anchors)
                    note = (f'One volume of a series with {sibs} other '
                            f'volume{"s" if sibs != 1 else ""} on this '
                            f'chart; placed inside the series by where '
                            f'it charts relative to them.')
                    _put_block(researched, m['key'], m, platform, v, note,
                               target_date_iso, confidence='low')
                    stats['series'] += 1
                    stats['written_keys'].append(m['key'])
                    stats['trail'].append((platform, m['title'], v,
                                           'series'))
                    wanted_keys.discard(m['key'])
                i = j

        # 3. An orphan series: research the SERIES once, then place
        #    its volumes under that anchor by chart position.
        if orphans and research_orphans and client is not None:
            items = []
            for skey, members in orphans:
                marked = [m for m in members if m['marked']]
                if not marked:
                    # A standalone title is not a set; it stays on the
                    # ordinary research path.
                    continue
                top = min(members, key=lambda r: r['pos'])
                series_title = series_display(top['title'])
                if not series_title:
                    continue
                items.append({
                    'kind': _COMIC_KIND,
                    'display_title': series_title,
                    'artist': top['artist'],
                    'best_rank': top['pos'],
                    'chart_labels': [f'{label} #{top["pos"]}'],
                    # The question is the SERIES' standing on this
                    # store, which a chart position does not answer:
                    # the deeper model with a live search, whatever
                    # the volume's rank would have picked.
                    'force_tier': 'hi',
                    'force_search': True,
                    '_series': skey,
                })
            if items:
                stats['orphan_calls'] += len(items)
                try:
                    res = se._research_all(items,
                                           target_date_iso=target_date_iso,
                                           spend_monitor=spend_monitor)
                except Exception:
                    logger.exception("first_party comics: series "
                                     "research failed (non-fatal)")
                    res = {}
                for it in items:
                    rk = se._lookup_key(_COMIC_KIND, it['display_title'],
                                        it.get('artist') or '')
                    got = res.get(rk)
                    blk = ((got or {}).get('by_platform') or {}).get(platform)
                    try:
                        anchor = int((blk or {}).get('us_estimate') or 0)
                    except (TypeError, ValueError):
                        anchor = 0
                    members = dict(orphans).get(it['_series']) or []
                    members = sorted(members, key=lambda r: r['pos'])
                    if anchor < 100:
                        for m in members:
                            if m['key'] in wanted_keys:
                                stats['cannot'].append(
                                    (platform, m['title'],
                                     'the series could not be sized on '
                                     'this service'))
                                wanted_keys.discard(m['key'])
                        continue
                    # The series reading is the reading of its best-
                    # charting volume here; the rest descend from it.
                    vals = _place_between(anchor, None, len(members),
                                          [m['key'] for m in members],
                                          salt, cap)
                    for m, v in zip(members, vals):
                        if m['key'] not in wanted_keys:
                            continue
                        note = (f'One volume of the series '
                                f'{it["display_title"]}; the series was '
                                f'sized on this service and the volume '
                                f'placed inside it by where it charts.')
                        _put_block(researched, m['key'], m, platform, v,
                                   note, target_date_iso, confidence='low')
                        stats['orphan_research'] += 1
                        stats['written_keys'].append(m['key'])
                        stats['trail'].append((platform, m['title'], v,
                                               'series-research'))
                        wanted_keys.discard(m['key'])

        for r in wanted:
            if r['key'] in wanted_keys:
                why = ('a standalone title with no series on this chart '
                       'and no first-party figure of its own'
                       if not r['marked'] else
                       'no priced volume of its series on this chart')
                stats['cannot'].append((platform, r['title'], why))

    logger.info("first_party comics: %d unpriced row(s); %d from Libby "
                "holds, %d placed inside their series, %d under a series "
                "sized in %d call(s); %d could not be derived",
                stats['unpriced'], stats['holds'], stats['series'],
                stats['orphan_research'], stats['orphan_calls'],
                len(stats['cannot']))
    for platform, title, v, how in stats['trail']:
        logger.info("first_party comics: %s / %s -> %s (%s)", platform,
                    title, f'{v:,}', how)
    for platform, title, why in stats['cannot']:
        logger.info("first_party comics: %s / %s not derived: %s",
                    platform, title, why)
    return stats


def anthropic_client():
    """A client for the parameter and series calls, or None."""
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        logger.info("first_party: no client (%s)", e)
        return None
