"""The people a room stands for (Jenna, 2026-11-05).

Jenna, verbatim: "somewhere we need to show a total of what the audience in
the focus group or panel represents if the audience has been cut like in
the ncis pilot. it wouldn't project up to that full smple size because that
sample size is cumulative of all people not just the cuts I made. does this
make sense? so the room would need to be sized accordingly. and if it's a
show like ncis new york it could even externally research on average how
many people watch it to help figure out where the size sjould land"

Two steps, one figure (``group.size_people``, mirrored in ``group.sizing``):

1. THE CUTS, NOT THE TWIN. ``digital_twin_pilot.represented_sizing`` reads
   the people inside the chosen cuts from the twin's own composition, as
   one union (a cut nested in another is counted once, two cuts that
   cannot overlap add up exactly). A room with no cuts stands for the
   whole twin as before.
2. A TITLE THAT HAS AIRED. When a pilot, episode, feature or binge test
   is cut from a title that has already aired or been released, one
   research call (the room's media desk model, web search on) finds the
   title's average audience per episode (US people, every screen, first
   week or season average) and its audience skews. The room then stands
   for the smaller of the cut people and the title's audience times the
   cuts' share of it (the cuts' share of the platform audience as the
   proxy, moved by the title's published skews when the research gives
   them), taken low (the bottom third of the researched range, per
   calibrate-low-anchor-to-actuals.mdc). Cached per twin, title and day.
   A title that has not aired keeps step 1.

The basis (what was researched, the shares, the figures) stays on the
record under ``sizing.basis`` for audit and never reaches a reader:
``public_sizing`` drops it, and nothing rendered says researched,
calibrated, or names a source (focus-group-calibration-silent.mdc). The
one rendered line is plain: "Standing for the 4 groups you chose: 9.6M
of the 83.8M people in this audience."

Every figure downstream reads the sized total: the segments' people are
re-spread to partition it (so the seats' stands-for lines, the cohort
quadrants and the whole-room quadrant agree), the forecast projects from
it, the report, the exports, the notices and the partner payload print
it.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from migration.digital_twin_engine import clamp, messy_count

S3_BUCKET = 'dashboard-inputs'
CACHE_PREFIX = 'system/digital_twins/title_audience/'
VERSION = 1
AUDIENCE_MIN_PEOPLE = 50_000          # a count under this is noise, not an audience
AUDIENCE_MAX_PEOPLE = 330_000_000
LOW_THIRD_POSITION = 1.0 / 6.0        # the middle of the bottom third of a range
SKEW_INDEX_MIN, SKEW_INDEX_MAX = 0.2, 5.0
AIRED_MODES = ('pilot', 'feature', 'episode', 'binge')
_OLD = {'45-54', '55-64', '65 or Older'}
_YOUNG = {'17 and Under', '18-24', '25-34'}
_MID = {'35-44'}

TITLE_SYSTEM = """You size the US audience of a television series, a season or a film that may already have been released. Use the web_search tool when you have it. Never invent a number or a date; null when you cannot support it. Prefer the lowest well-supported figures when sources disagree.
What to find, when the title has been released:
- the AVERAGE AUDIENCE PER EPISODE (for a film, its audience) in US people across every screen (linear plus streaming) within the first seven days of each release, from the network's or platform's own published counts, Nielsen (including its cross-platform and streaming reads), Samba TV, Luminate, Parrot Analytics, or trade press reporting those counts. Give a low (the smallest well-supported figure, for example live plus same-day only) and a high (the widest well-supported multi-platform figure) and say what each measures. For a season still running, the season to date.
- the PLATFORM'S US PEOPLE: the US people who use the network or platform it runs on (people, not accounts; about 1.8 to 2.0 people use one streaming account).
- the AUDIENCE SKEWS the measurement services or the network published for the title: the share of its audience that is female, 45 or older, under 35, Hispanic, Black, and its median age. Only figures actually published for this title; null otherwise.
Return JSON only:
{"released": true|false|null, "title": "<proper name>", "released_on": "YYYY-MM-DD"|null,
 "audience": {"low": <people or null>, "high": <people or null>, "typical": <people or null>, "measure": "<what the figures count, plain words>", "window": "<first seven days per episode|season average|first week|other>", "as_of": "YYYY-MM-DD"|null, "source_class": "<platform published|Nielsen|Samba TV|Luminate|Parrot Analytics|trade press|other>"},
 "platform_people": {"value": <US people or null>, "measure": "<plain words>", "as_of": "YYYY-MM-DD"|null},
 "skews": {"female_pct": <0-100 or null>, "age_45_plus_pct": <0-100 or null>, "age_under_35_pct": <0-100 or null>, "hispanic_pct": <0-100 or null>, "black_pct": <0-100 or null>, "median_age": <years or null>},
 "note": "<one plain sentence>"}"""

_MEMO: dict = {}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _day():
    return time.strftime('%Y-%m-%d', time.gmtime())


def _meta(twin):
    return (twin or {}).get('meta') or {}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", '_', str(s or '').lower()).strip('_')[:60] or 'x'


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _pct(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0.0 < x < 100.0 else None


def people_short(n) -> str:
    """9.6M, 412.3K, 8,421: the one-decimal form the sizing line prints."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 10_000:
        return f"{n / 1e3:.1f}K"
    return f"{int(round(n)):,}"


def clear_cache() -> None:
    _MEMO.clear()


# ------------------------------------------------------------ the research
def _cache_key(twin: dict, title: str, day: str) -> str:
    return f"{CACHE_PREFIX}{_slug(_meta(twin).get('name') or _meta(twin).get('key'))}/{_slug(title)}/{day}.json"


def _cache_get(s3, key: str) -> Optional[dict]:
    if key in _MEMO:
        return _MEMO[key]
    if s3 is None:
        return None
    try:
        raw = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
        doc = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
        if isinstance(doc, dict):
            _MEMO[key] = doc
            return doc
    except Exception:
        pass
    return None


def _cache_put(s3, key: str, doc: dict) -> None:
    _MEMO[key] = doc
    if s3 is None:
        return
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8'), ContentType='application/json')
    except Exception:
        pass


def _normalize_research(data: dict, title: str) -> dict:
    """The research answer, shaped and bounded. ``audience_low`` is the
    figure the sizing uses: the bottom third of the researched range."""
    aud = data.get('audience') if isinstance(data.get('audience'), dict) else {}
    vals = {}
    for k in ('low', 'high', 'typical'):
        v = _int(aud.get(k))
        vals[k] = v if AUDIENCE_MIN_PEOPLE <= v <= AUDIENCE_MAX_PEOPLE else 0
    low, high, typ = vals['low'], vals['high'], vals['typical']
    if low and high and high < low:
        low, high = high, low
    if not low:
        low = typ or high
    if not high:
        high = typ or low
    if low and high and high > low:
        use = low + (high - low) * LOW_THIRD_POSITION
    else:
        use = low or 0
    plat = data.get('platform_people') if isinstance(data.get('platform_people'), dict) else {}
    pv = _int(plat.get('value'))
    sk = data.get('skews') if isinstance(data.get('skews'), dict) else {}
    skews = {k: _pct(sk.get(k)) for k in ('female_pct', 'age_45_plus_pct', 'age_under_35_pct', 'hispanic_pct', 'black_pct')}
    try:
        skews['median_age'] = float(sk.get('median_age')) if sk.get('median_age') not in (None, '') else None
    except (TypeError, ValueError):
        skews['median_age'] = None
    released = data.get('released')
    return {'version': VERSION, 'title': str(data.get('title') or title)[:160], 'released': (bool(released) if released is not None else None),
            'released_on': str(data.get('released_on') or '')[:10], 'audience_low': int(round(use)) if use else 0,
            'audience_range': {'low': low or None, 'high': high or None, 'typical': typ or None},
            'measure': str(aud.get('measure') or '')[:200], 'window': str(aud.get('window') or '')[:60], 'as_of': str(aud.get('as_of') or '')[:10],
            'source_class': str(aud.get('source_class') or '')[:40],
            'platform_people': (pv if AUDIENCE_MIN_PEOPLE <= pv <= AUDIENCE_MAX_PEOPLE else None), 'skews': skews,
            'note': str(data.get('note') or '')[:240], 'researched_at': _now()}


def title_audience(twin: dict, title: str, *, episode_words: str = '', platform: str = '', model_fn=None, s3=None, day: str = '') -> Optional[dict]:
    """The title's researched audience, cached per (twin, title, day).
    None when no model answers or the title is blank; a title that has
    not been released comes back with ``released`` False (cached too, so
    the day's later rooms do not ask again)."""
    title = ' '.join(str(title or '').split())
    if not title:
        return None
    day = day or _day()
    key = _cache_key(twin, title, day)
    hit = _cache_get(s3, key)
    if hit is not None:
        return hit
    try:
        from migration.digital_twin_room import Models, model_available
    except Exception:
        return None
    if not model_available(model_fn):
        return None
    user = json.dumps({'title': title, 'episode': episode_words or None, 'network_or_platform': platform or str(_meta(twin).get('platform_home') or _meta(twin).get('name') or '') or None,
                       'today': day, 'question': 'Has this been released to the public in the US? If so, on average how many US people watch an episode of it (every screen, first seven days), who is its audience, and how many US people use the platform it runs on?'},
                      ensure_ascii=False)
    try:
        data, _rec = Models(model_fn=model_fn).call('research', TITLE_SYSTEM, user, max_tokens=700, temperature=0.1)
    except Exception as e:
        print(f"[twin-sizing] title research failed: {str(e)[:160]}")
        return None
    if not isinstance(data, dict):
        return None
    doc = _normalize_research(data, title)
    if doc.get('released') is not None:
        _cache_put(s3, key, doc)        # a definite answer holds for the day; an answer that could not be supported is asked again
    return doc


# ------------------------------------------------------- the cuts' share
def skew_index(twin: dict, cut: dict, skews: dict) -> float:
    """How much more (or less) of the title's audience a cut holds than
    of the twin: the title's published share over the twin's own, one
    factor per dimension the research gave (gender, age, ethnicity),
    1.0 where it gave nothing."""
    from migration.digital_twin_pilot import cut_share
    skews = skews or {}
    idx = 1.0
    demos = cut.get('demos') or {}

    def _apply(key, vals, title_share):
        nonlocal idx
        if title_share is None:
            return
        twin_share = cut_share(twin, {'demos': {key: set(vals)}, 'scalars': {}})
        if twin_share > 0:
            idx *= clamp(title_share / twin_share, SKEW_INDEX_MIN, SKEW_INDEX_MAX)
    g = set(demos.get('GENDER') or [])
    f = skews.get('female_pct')
    if g and f is not None:
        if g <= {'Female', 'Trans Female'}:
            _apply('GENDER', g, f / 100.0)
        elif g <= {'Male', 'Trans Male'}:
            _apply('GENDER', g, 1.0 - f / 100.0)
    a = set(demos.get('AGE') or [])
    old, young = skews.get('age_45_plus_pct'), skews.get('age_under_35_pct')
    if a:
        if a == _OLD and old is not None:
            _apply('AGE', a, old / 100.0)
        elif a == (_YOUNG | _MID) and old is not None:
            _apply('AGE', a, 1.0 - old / 100.0)
        elif a >= {'18-24', '25-34'} and a <= _YOUNG and young is not None:
            _apply('AGE', a, young / 100.0)
        elif a == (_OLD | _MID) and young is not None:
            _apply('AGE', a, 1.0 - young / 100.0)
        elif a == _MID and old is not None and young is not None:
            _apply('AGE', a, max(0.02, 1.0 - (old + young) / 100.0))
    e = set(demos.get('ETHNICITY') or [])
    if e == {'Hispanic or Latino'} and skews.get('hispanic_pct') is not None:
        _apply('ETHNICITY', e, skews['hispanic_pct'] / 100.0)
    elif e == {'Black or African American'} and skews.get('black_pct') is not None:
        _apply('ETHNICITY', e, skews['black_pct'] / 100.0)
    return idx


def share_of_title(twins, segments: list, research: Optional[dict]) -> float:
    """The cuts' share of the title's audience: the union of the cuts as
    a share of the twin, each term moved by the title's skews where the
    research published them. 1.0 for a room with no cuts."""
    from migration import digital_twin_pilot as PI
    skews = (research or {}).get('skews') or {}

    def fn(t, c):
        return clamp(PI.cut_share(t, c) * skew_index(t, c, skews), 0.0, 1.0)
    tw = twins if isinstance(twins, (list, tuple)) else [twins]
    tw = [t for t in tw if isinstance(t, dict)]
    by_key = {PI._twin_key(t): t for t in tw}
    groups = {}
    for s in segments or []:
        if isinstance(s, dict):
            groups.setdefault(str(s.get('twin') or (PI._twin_key(tw[0]) if tw else '')), []).append(s)
    if not groups:
        return 1.0
    num, den = 0.0, 0.0
    for tkey, group in groups.items():
        t = by_key.get(tkey) or (tw[0] if tw else {})
        size = float(_int(_meta(t).get('size_us')) or 1)
        cuts = [PI.segment_cut(s, t) for s in group]
        num += PI.union_share(t, cuts, fn) * size
        den += size
    return clamp(num / den, 0.0, 1.0) if den > 0 else 1.0


# ------------------------------------------------------------ the sizing
def _partition(segs: list, total: int, seed: str) -> None:
    """The segments' people re-spread to add up to ``total`` exactly, in
    proportion to the people each cut holds in the twin, as messy
    integers (the last segment takes the rounding)."""
    segs = [s for s in segs if isinstance(s, dict)]
    if not segs or total <= 0:
        return
    weights = [max(1, _int(s.get('twin_people') or s.get('people'))) for s in segs]
    wsum = float(sum(weights)) or 1.0
    # the same inputs always land on the same figures (a recompute of a saved room is a no-op): a share that would end
    # in 0 moves up by a few units, by its place in the room, never by a salted span
    vals = []
    for i, (s, w) in enumerate(zip(segs, weights)):
        v = max(1, int(round(total * w / wsum)))
        if v % 10 == 0 and v > 20:
            v += (i % 9) + 1
        vals.append(v)
    diff = total - sum(vals)
    k = max(range(len(vals)), key=lambda i: vals[i])
    if vals[k] + diff >= 1:
        vals[k] += diff
    for s, v in zip(segs, vals):
        s['people'] = int(v)


def sizing_line(sz: dict) -> str:
    """The one line a reader sees under the header when the room stands
    for less than the whole audience: plain words, no source."""
    sz = sz if isinstance(sz, dict) else {}
    people, twin_people = _int(sz.get('people')), _int(sz.get('twin_people'))
    if not people or not twin_people or people >= twin_people:
        return ''
    n = _int(sz.get('cuts_n'))
    head = (f"Standing for the {n} groups you chose" if n > 1 else ('Standing for the group you chose' if n == 1 else 'Standing for'))
    return f"{head}: {people_short(people)} of the {people_short(twin_people)} people in this audience."


def public_sizing(sz: Optional[dict]) -> Optional[dict]:
    """What travels to the page: the figures and the line, never the
    basis (the research, the shares, the title's numbers)."""
    if not isinstance(sz, dict):
        return sz
    out = {k: sz.get(k) for k in ('version', 'people', 'footprint', 'twin_people', 'share_of_twin', 'cuts_n', 'is_cut') if k in sz}
    out['line'] = sizing_line(sz)
    return out


def apply_sizing(group: dict, people: int, sizing: dict, seed: str = '') -> dict:
    """Write the sized total onto a room: ``size_people``, ``twin_people``,
    the segments' people as a partition of it (by twin, then by cut), the
    seats' people and their stands-for lines, and ``sizing`` (with its
    basis). In place; returns the group."""
    from migration.digital_twin_panel import annotate_seat_stands_for, spread_seat_people
    g = group if isinstance(group, dict) else {}
    segs = [s for s in (g.get('segments') or []) if isinstance(s, dict)]
    people = max(1, _int(people)) if segs or _int(people) > 0 else _int(people)
    by_twin = sizing.get('by_twin') if isinstance(sizing.get('by_twin'), dict) else {}
    footprint = max(1, _int(sizing.get('footprint')) or sum(_int(v.get('people')) for v in by_twin.values()) or people)
    groups = {}
    for s in segs:
        if 'twin_people' not in s:
            s['twin_people'] = _int(s.get('people'))
        groups.setdefault(str(s.get('twin') or ''), []).append(s)
    if len(groups) <= 1:
        _partition(segs, people, seed)
    else:
        # several twins in one room: each twin's cuts take their share of the sized total
        shares = {k: float(_int((by_twin.get(k) or {}).get('people')) or sum(_int(s.get('twin_people')) for s in grp)) for k, grp in groups.items()}
        tot = sum(shares.values()) or 1.0
        left = people
        keys = list(groups)
        for i, k in enumerate(keys):
            part = int(round(people * shares[k] / tot)) if i < len(keys) - 1 else left
            part = max(1, min(part, left - (len(keys) - 1 - i)))
            _partition(groups[k], part, f"{seed}|{k}")
            left -= part
    g['size_people'] = int(people)
    g['twin_people'] = _int(sizing.get('twin_people'))
    sz = dict(sizing)
    sz['version'] = VERSION
    sz['people'] = int(people)
    sz['footprint'] = int(footprint)
    sz['is_cut'] = bool(_int(sz.get('cuts_n')) > 0 and footprint < _int(sz.get('twin_people')))
    sz['line'] = sizing_line(sz)
    g['sizing'] = sz
    spread_seat_people(g.get('roster') or [], segs, seed)
    annotate_seat_stands_for(g, force=True)
    return g


def _default_s3():
    """The cache's client when the caller passed none: boto3 outside
    REGRESSION_TEST_MODE, else None (the memo alone)."""
    try:
        from migration.digital_twin_room import regression_mode
        if regression_mode():
            return None
        import boto3
        return boto3.client('s3')
    except Exception:
        return None


def _title_words(twin: dict, sess: dict) -> tuple:
    """(show, episode words) for a pilot test, the forecast's own read of
    the media record."""
    try:
        from migration.digital_twin_forecast import _episode_words, _show_name
        return _show_name(twin, sess), _episode_words(sess)
    except Exception:
        media = (sess or {}).get('media') or {}
        return str(media.get('title') or sess.get('title') or ''), ''


def size_session(twin, sess: dict, *, twins=None, model_fn=None, s3=None, research: bool = True, seed: str = '') -> dict:
    """Size a test or a room to what it stands for and write it onto the
    record. Step 1 always (the cuts' union from the twin's composition);
    step 2 for a pilot, episode, feature or binge test whose title has
    aired (the title's researched audience times the cuts' share of it,
    taken low), when ``research`` is on and a model answers. Returns the
    sizing; never raises."""
    from migration import digital_twin_pilot as PI
    g = (sess or {}).get('group') if isinstance(sess, dict) else None
    if not isinstance(g, dict):
        return {}
    tw = [t for t in (twins or [twin]) if isinstance(t, dict)]
    if not tw:
        return g.get('sizing') or {}
    segs = [s for s in (g.get('segments') or []) if isinstance(s, dict)]
    seed = seed or f"sizing|{sess.get('id')}|{'|'.join(PI._twin_key(t) for t in tw)}"
    try:
        sz = PI.represented_sizing(tw, segs, seed=seed)
        people = _int(sz.get('people'))
        basis = {'step': 'cuts' if sz.get('is_cut') else 'twin', 'union_share': sz.get('share_of_twin'), 'by_twin': sz.get('by_twin')}
        if research and sess.get('kind') == 'pilot' and str(sess.get('mode') or 'pilot') in AIRED_MODES:
            show, ep = _title_words(tw[0], sess)
            res = title_audience(tw[0], show, episode_words=ep, model_fn=model_fn, s3=(s3 if s3 is not None else _default_s3()))
            if isinstance(res, dict):
                basis['title'] = {'name': res.get('title'), 'released': res.get('released'), 'released_on': res.get('released_on'), 'audience_low': res.get('audience_low'),
                                  'audience_range': res.get('audience_range'), 'measure': res.get('measure'), 'window': res.get('window'), 'as_of': res.get('as_of'),
                                  'source_class': res.get('source_class'), 'platform_people': res.get('platform_people'), 'skews': res.get('skews'),
                                  'note': res.get('note'), 'researched_at': res.get('researched_at')}
                if res.get('released') and _int(res.get('audience_low')) > 0:
                    share = share_of_title(tw, segs, res)
                    cap = _int(res['audience_low']) * share
                    basis['share_of_title'] = round(share, 6)
                    basis['title_cut_people'] = int(round(cap))
                    if cap > 0 and cap < people:
                        people = int(clamp(messy_count(seed, 'title-cap', cap), 1, people))
                        basis['step'] = 'title'
        sz['basis'] = basis
        sz['step'] = basis['step']
        apply_sizing(g, people, sz, seed)
        sess['updated_at'] = _now()
        return g.get('sizing') or {}
    except Exception as e:
        print(f"[twin-sizing] skipped: {str(e)[:200]}")
        return g.get('sizing') or {}


def _scale_rows(rows: list, total: int) -> None:
    """Each row's people as its share of ``total`` (the largest row takes
    the rounding), the way the battery publishes them."""
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get('pct') is not None]
    if not rows or total <= 0:
        return
    vals = []
    for i, r in enumerate(rows):
        try:
            v = int(round(float(r.get('pct') or 0) / 100.0 * total))
        except (TypeError, ValueError):
            v = 0
        if v > 20 and v % 10 == 0:
            v += (i % 9) + 1
        vals.append(max(0, v))
    k = max(range(len(vals)), key=lambda i: vals[i])
    vals[k] = max(0, vals[k] + (total - sum(vals)))
    for r, v in zip(rows, vals):
        r['people'] = int(v)


def rescale_session_figures(sess: dict, *, twin: Optional[dict] = None, s3=None, ledger: Optional[dict] = None) -> dict:
    """Every people figure a test derives from the people its room stands
    for, brought to the sized total: the scorecard's count and its line,
    the battery's quadrants (the whole room, each cohort as its segment's
    people, the other splits in proportion), the battery's rows, the
    questions the room was asked (whole room and by segment), and the
    forecast (recomputed from the record's own scores and the ledger, no
    new research). Ratings, shares and the room's words are not touched.
    Returns {before, after} totals."""
    from migration import digital_twin_pilot as PI
    g = (sess or {}).get('group') if isinstance(sess, dict) else None
    if not isinstance(g, dict):
        return {}
    total = _int(g.get('size_people'))
    if total <= 0:
        return {}
    seg_people = {str(s.get('label')): _int(s.get('people')) for s in (g.get('segments') or []) if isinstance(s, dict)}
    sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else None
    before = _int((sc or {}).get('people')) if sc else None
    if sc:
        bat = sc.get('battery') if isinstance(sc.get('battery'), dict) else {}
        quads = [q for q in (bat.get('quadrants') or []) if isinstance(q, dict)]
        old_total = next((_int(q.get('people')) for q in quads if q.get('key') == 'total'), 0) or _int(sc.get('people')) or total
        before = old_total
        ratio = total / float(old_total) if old_total > 0 else 1.0
        for i, q in enumerate(quads):
            if q.get('key') == 'total':
                q['people'] = int(total)
            elif q.get('group') == 'cohort' and seg_people.get(str(q.get('label'))):
                q['people'] = int(seg_people[str(q.get('label'))])
            elif _int(q.get('people')) > 0:
                v = int(round(_int(q.get('people')) * ratio))
                q['people'] = v + ((i % 9) + 1) if (v > 20 and v % 10 == 0) else v
        for q in (bat.get('questions') or []):
            if isinstance(q, dict):
                _scale_rows(q.get('rows'), total)
                rows = [r for r in (q.get('rows') or []) if isinstance(r, dict)]
                if rows and 'top_people' in q:
                    q['top_people'] = rows[0].get('people')
                if len(rows) > 1 and 'total_positive_people' in q:
                    q['total_positive_people'] = _int(rows[0].get('people')) + _int(rows[1].get('people'))
        sc['people'] = int(total)
        try:
            sc['denominator_line'] = PI.denominator_line(_int(g.get('in_room')) or len(g.get('roster') or []), total)
        except Exception:
            pass
    old_room = before or total
    for q in (sess.get('questions') or []):
        if not isinstance(q, dict):
            continue
        for key in ('rows', 'distribution', 'options'):
            _scale_rows(q.get(key), total)
        if _int(q.get('headline_people')) > 0 and old_room > 0:
            lead = next((r for r in (q.get('options') or []) if isinstance(r, dict) and _int(r.get('people')) == _int(q.get('headline_people'))), None)
            q['headline_people'] = int(lead['people']) if lead else int(round(_int(q.get('headline_people')) * total / float(old_room)))
        for sr in (q.get('by_segment') or []):
            if isinstance(sr, dict) and seg_people.get(str(sr.get('segment'))):
                sr['people'] = int(seg_people[str(sr.get('segment'))])
                for key in ('rows', 'distribution'):
                    _scale_rows(sr.get(key), sr['people'])
    if sc and twin is not None:
        try:
            from migration.digital_twin_forecast import attach_forecast
            attach_forecast(twin, sess, s3=s3, ledger=ledger, research=False)
        except Exception as e:
            print(f"[twin-sizing] forecast not re-read: {str(e)[:160]}")
    return {'before': before, 'after': int(total)}


def ensure_sizing(twin, sess: dict, *, twins=None) -> bool:
    """A record from before the sizing gets step 1 on the way out (no
    research on a read), and its derived figures follow. True when the
    record changed."""
    g = (sess or {}).get('group') if isinstance(sess, dict) else None
    if not isinstance(g, dict) or not g.get('roster') or isinstance(g.get('sizing'), dict):
        return False
    tw = [t for t in (twins or [twin]) if isinstance(t, dict)]
    have = {str(_meta(t).get('key') or '') for t in tw}
    need = {str(s.get('twin') or '') for s in (g.get('segments') or []) if isinstance(s, dict) and s.get('twin')}
    if need - have:
        return False          # a room seating twins the caller did not load keeps its figures until they are all in hand
    before = g.get('size_people')
    size_session(twin, sess, twins=twins, research=False)
    changed = isinstance(g.get('sizing'), dict) and g.get('size_people') != before
    if changed and sess.get('kind') == 'pilot':
        rescale_session_figures(sess, twin=twin)
    return changed


def share_of_title_for(sess: dict) -> float:
    """The share of the title's audience the sized room stands for (the
    forecast's scale), 1.0 when the room was never sized to a title."""
    sz = ((sess or {}).get('group') or {}).get('sizing') if isinstance(sess, dict) else None
    if not isinstance(sz, dict):
        return 1.0
    basis = sz.get('basis') if isinstance(sz.get('basis'), dict) else {}
    try:
        v = float(basis.get('share_of_title'))
        if 0.0 < v <= 1.0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        v = float(sz.get('share_of_twin'))
        return v if 0.0 < v <= 1.0 else 1.0
    except (TypeError, ValueError):
        return 1.0


__all__ = ['title_audience', 'share_of_title', 'skew_index', 'size_session', 'ensure_sizing', 'apply_sizing', 'rescale_session_figures', 'sizing_line',
           'public_sizing', 'people_short', 'share_of_title_for', 'clear_cache', 'TITLE_SYSTEM', 'AIRED_MODES', 'VERSION']
