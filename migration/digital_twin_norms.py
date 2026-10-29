"""Pilot Testing norms ledger (Digital Twins IQ, 2026-10-28).

Every score on a Pilot Testing scorecard sits next to a norm and a genre
norm. The ledger lives at ``system/digital_twins/norms/norms.json`` and
is keyed by format x genre x metric. Two kinds of cell:

* seeded: a published anchor (industry norms printed in screening
  reports, exit-poll results the trades print every weekend, stated
  thresholds). A seeded cell may stay as published (round is fine for a
  quoted figure). Its sources (URL + note) are INTERNAL and never
  reach a page a user reads.
* accumulated: once one of our own tests blends in, the cell carries the
  running mean of our tests with the seed as a pseudo-count
  (``SEED_WEIGHT`` tests), ``seeded`` flips to False, and the seed stays
  in the note. Displayed at one decimal.

Public surface for the pilot module and Phase 2 (modes, a Norms tab):
``norms_for(format, genre)``, ``record(format, genre, metrics, test_id)``,
``seed()``. Reads fall back to the in-memory seed when the bucket has no
ledger yet, so a Norm column is always available. Writes are
ETag-guarded when the client supports it and retried on a conflict.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

S3_BUCKET = 'dashboard-inputs'
NORMS_KEY = 'system/digital_twins/norms/norms.json'
BACKUP_PREFIX = 'system/digital_twins/norms/_backups/'
LEDGER_VERSION = 1
SEED_WEIGHT = 8          # tests a published anchor stands for once our own tests blend in
MAX_TESTS_PER_CELL = 400  # test ids kept per cell for the dedupe

FORMATS = ('feature', 'pilot', 'episode', 'trailer', 'title', 'key_art', 'script', 'concept')
GENRES = ('all', 'comedy', 'drama', 'action', 'horror', 'thriller', 'romance', 'family', 'animation', 'documentary', 'unscripted')
METRICS = ('excellent', 'total_positive', 'definitely_recommend', 'finish', 'tune_in', 'urgency_opening',
           'lead_character', 'secondary_character', 'supporting_character',
           'component_humor', 'component_story', 'component_beginning', 'component_ending', 'component_music',
           'component_action', 'component_suspense', 'component_settings', 'component_pace_just_right', 'confusion',
           # the screening modes and the diagnostics (2026-10-28)
           'interest_before', 'interest_after', 'definite_interest_lift', 'would_watch',
           'title_appeal', 'title_fit', 'title_unique', 'option_preference', 'art_attention', 'art_interest_lift',
           'concept_interest', 'concept_unique', 'script_story', 'script_structure', 'script_character',
           'pace_too_slow', 'beginning', 'ending', 'key_relationship', 'follow_second_episode', 'critic_score', 'walkouts')
# a format with no cell of its own reads the nearest format's cell
FORMAT_FALLBACK = {
    'pilot': ('episode', 'feature'), 'episode': ('pilot', 'feature'), 'trailer': ('feature',),
    'title': ('feature',), 'key_art': ('feature',), 'script': ('feature',), 'concept': ('feature',), 'feature': ('pilot',),
}
METRIC_LABEL = {
    'excellent': 'Excellent', 'total_positive': 'Total positive', 'definitely_recommend': 'Definitely recommend',
    'finish': 'Would finish', 'tune_in': 'Would watch the next one', 'urgency_opening': 'Opening weekend',
    'lead_character': 'Lead', 'secondary_character': 'Second lead', 'supporting_character': 'Supporting',
    'component_humor': 'Humor', 'component_story': 'Story', 'component_beginning': 'Beginning', 'component_ending': 'Ending',
    'component_music': 'Music', 'component_action': 'Action', 'component_suspense': 'Suspense', 'component_settings': 'Settings',
    'component_pace_just_right': 'Pace just right', 'confusion': 'Confused',
    'interest_before': 'Definitely interested before', 'interest_after': 'Definitely interested after', 'definite_interest_lift': 'Definite interest lift',
    'would_watch': 'Would watch it', 'title_appeal': 'Title appeal', 'title_fit': 'Title fits the logline', 'title_unique': 'Title stands out',
    'option_preference': 'Preferred option', 'art_attention': 'Art holds the eye', 'art_interest_lift': 'Interest lift from the art',
    'concept_interest': 'Definitely interested in the idea', 'concept_unique': 'Idea stands out',
    'script_story': 'Story works', 'script_structure': 'Structure works', 'script_character': 'Characters work',
    'pace_too_slow': 'Too slow', 'beginning': 'Beginning', 'ending': 'Ending', 'key_relationship': 'Key relationship',
    'follow_second_episode': 'Would follow the lead into a second episode', 'critic_score': 'Critic score', 'walkouts': 'Walkouts',
}
GENRE_LABEL = {'all': 'All', 'comedy': 'Comedy', 'drama': 'Drama', 'action': 'Action', 'horror': 'Horror', 'thriller': 'Thriller',
               'romance': 'Romance', 'family': 'Family', 'animation': 'Animation', 'documentary': 'Documentary', 'unscripted': 'Unscripted'}
FORMAT_LABEL = {'feature': 'features', 'pilot': 'pilots', 'episode': 'episodes', 'trailer': 'trailers', 'title': 'titles',
                'key_art': 'key art', 'script': 'scripts', 'concept': 'concepts'}

# ---------------------------------------------------------------- the seed
# Published anchors. Internal: the URL and the note never reach a page a user
# reads. A range stores its midpoint as the value and the range in the note.
_INTERVIEW = ('https://wikileaks.org/sony/docs/03_03/Mktrsch/F%20I%20L%20M%20S/INTERVIEW/Screening%20%233%20and%20%234%20%285-13-14%29/'
              '5.13%20The%20Interview%20Report%20%28730pm%29%20-%20FINAL.pdf')
SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'pilot_norms_seed.json')
_BUILTIN_SEED = [
    # industry norms as printed in a recruited-screening topline (feature, all genres)
    {'format': 'feature', 'genre': 'all', 'metric': 'excellent', 'value': 30.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Industry norm column on the topline: excellent 30%.'}]},
    {'format': 'feature', 'genre': 'all', 'metric': 'total_positive', 'value': 65.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Industry norm column on the topline: total positive (excellent + very good) 65%.'}]},
    {'format': 'feature', 'genre': 'all', 'metric': 'definitely_recommend', 'value': 50.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Industry norm column on the topline: definitely recommend 50%.'}]},
    # the genre norm column on the same topline (blockbuster comedy)
    {'format': 'feature', 'genre': 'comedy', 'metric': 'excellent', 'value': 48.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Genre norm column (blockbuster comedy): excellent 48%.'}]},
    {'format': 'feature', 'genre': 'comedy', 'metric': 'total_positive', 'value': 82.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Genre norm column (blockbuster comedy): total positive 82%.'}]},
    {'format': 'feature', 'genre': 'comedy', 'metric': 'definitely_recommend', 'value': 71.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Genre norm column (blockbuster comedy): definitely recommend 71%.'}]},
    # character norms
    {'format': 'feature', 'genre': 'all', 'metric': 'lead_character', 'value': 50.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Character ratings page: lead norm 50% (top box).'}]},
    {'format': 'feature', 'genre': 'all', 'metric': 'secondary_character', 'value': 45.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Character ratings page: secondary lead norm 45%.'}]},
    {'format': 'feature', 'genre': 'all', 'metric': 'supporting_character', 'value': 40.0, 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Character ratings page: supporting norm 40%.'}]},
    # component norms (a 30% to 40% band; the midpoint is stored, the band stays in the note)
] + [
    {'format': 'feature', 'genre': 'all', 'metric': m, 'value': 35.0, 'range': [30.0, 40.0], 'n_hint': None,
     'sources': [{'url': _INTERVIEW, 'note': 'Component ratings page: norms sit in a 30% to 40% band (top box); midpoint stored.'}]}
    for m in ('component_humor', 'component_story', 'component_beginning', 'component_ending', 'component_music', 'component_action', 'component_suspense')
]


def _load_seed() -> list:
    """The full seed (data/pilot_norms_seed.json: every published anchor
    with its sources, folded from the research on 2026-10-28); the
    built-in anchors when the file is missing or unreadable."""
    try:
        with open(SEED_FILE, encoding='utf-8') as fh:
            doc = json.load(fh)
        cells = [c for c in (doc.get('cells') or []) if isinstance(c, dict) and c.get('value') is not None and c.get('sources')]
        if cells:
            return cells
    except Exception as e:
        print(f"[twin-norms] seed file not read ({e}); built-in anchors")
    return list(_BUILTIN_SEED)


SEED_CELLS = _load_seed()


def cell_key(fmt: str, genre: str, metric: str) -> str:
    return f"{_fmt(fmt)}|{_genre(genre)}|{str(metric or '').strip().lower()}"


def _fmt(fmt) -> str:
    f = str(fmt or 'pilot').strip().lower().replace('-', '_').replace(' ', '_')
    return f if f in FORMATS else ('pilot' if f in ('episode_1', 'ep1', 'tv') else 'feature' if f in ('film', 'movie') else 'pilot')


def _genre(g) -> str:
    s = str(g or 'all').strip().lower()
    alias = {'comedies': 'comedy', 'sitcom': 'comedy', 'romcom': 'romance', 'rom-com': 'romance', 'romantic': 'romance', 'kids': 'family',
             'animated': 'animation', 'doc': 'documentary', 'docu': 'documentary', 'reality': 'unscripted', 'suspense': 'thriller',
             'scifi': 'action', 'sci-fi': 'action', 'adventure': 'action', 'superhero': 'action', 'crime': 'thriller', 'mystery': 'thriller'}
    s = alias.get(s, s)
    return s if s in GENRES else 'all'


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _r1(v) -> float:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ the s3
def _s3_default():
    try:
        from prometheus.host import host
        if host.has('s3_client'):
            return host.s3_client
    except Exception:
        pass
    try:
        import sys as _sys
        a = _sys.modules.get('app')
        if a is not None and getattr(a, 's3_client', None) is not None:
            return a.s3_client
    except Exception:
        pass
    return None


def _read(s3):
    """(doc, etag). (None, None) when the ledger does not exist yet."""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=NORMS_KEY)
    except Exception as e:
        code = ''
        try:
            code = str((getattr(e, 'response', {}) or {}).get('Error', {}).get('Code') or '')
        except Exception:
            code = ''
        if code in ('NoSuchKey', '404', 'NotFound') or isinstance(e, KeyError) or 'NoSuchKey' in str(e) or 'Not Found' in str(e):
            return None, None
        if code:
            raise
        return None, None
    raw = obj['Body'].read()
    try:
        doc = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        doc = None
    etag = str(obj.get('ETag') or '').strip('"') or None
    return (doc if isinstance(doc, dict) else None), etag


def _write(s3, doc: dict, etag: Optional[str], existed: bool) -> bool:
    """One guarded put. True when it landed, False on a conflict."""
    body = json.dumps(doc, ensure_ascii=False, default=str, indent=1).encode('utf-8')
    kw = dict(Bucket=S3_BUCKET, Key=NORMS_KEY, Body=body, ContentType='application/json')
    guarded = dict(kw)
    if etag:
        guarded['IfMatch'] = etag
    elif not existed:
        guarded['IfNoneMatch'] = '*'
    try:
        s3.put_object(**guarded)
        return True
    except TypeError:
        s3.put_object(**kw)
        return True
    except Exception as e:
        name = type(e).__name__
        text = str(e)
        if 'ParamValidation' in name:
            s3.put_object(**kw)
            return True
        if 'PreconditionFailed' in text or '412' in text:
            return False
        raise


def _backup_daily(s3, doc: dict):
    """One copy a day under _backups/, before the first write of the day."""
    key = f"{BACKUP_PREFIX}norms.{time.strftime('%Y%m%d', time.gmtime())}.json"
    try:
        s3.get_object(Bucket=S3_BUCKET, Key=key)
        return
    except Exception:
        pass
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, ensure_ascii=False, default=str).encode('utf-8'), ContentType='application/json')
    except Exception as e:
        print(f"[twin-norms] backup skipped: {e}")


def _empty() -> dict:
    return {'version': LEDGER_VERSION, 'updated_at': _now(), 'cells': {}}


def load(s3=None) -> dict:
    """The ledger as stored; the in-memory seed when the bucket has none."""
    s3 = s3 if s3 is not None else _s3_default()
    doc = None
    if s3 is not None:
        try:
            doc, _etag = _read(s3)
        except Exception as e:
            print(f"[twin-norms] read failed: {e}")
            doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get('cells'), dict):
        doc = _empty()
        for c in SEED_CELLS:
            doc['cells'][cell_key(c['format'], c['genre'], c['metric'])] = _seed_cell(c)
        doc['in_memory'] = True
    return doc


def update(s3, mutate, max_retries: int = 5) -> Optional[dict]:
    """read -> mutate(doc) -> guarded put, retried on a conflict.
    ``mutate`` returns the doc to write or None to leave it alone."""
    s3 = s3 if s3 is not None else _s3_default()
    if s3 is None:
        return None
    last = None
    for attempt in range(max_retries + 1):
        doc, etag = _read(s3)
        existed = doc is not None
        if not isinstance(doc, dict) or not isinstance(doc.get('cells'), dict):
            doc = _empty()
        before = json.dumps(doc, sort_keys=True, default=str)
        new = mutate(doc)
        if new is None:
            return None
        new['version'] = LEDGER_VERSION
        new['updated_at'] = _now()
        new.pop('in_memory', None)
        if existed and json.dumps({k: v for k, v in new.items() if k != 'updated_at'}, sort_keys=True, default=str) == json.dumps({k: v for k, v in json.loads(before).items() if k != 'updated_at'}, sort_keys=True, default=str):
            return new   # nothing changed: no write, no backup
        if existed:
            _backup_daily(s3, json.loads(before))
        if _write(s3, new, etag, existed):
            return new
        last = attempt
        time.sleep(min(0.2 * (2 ** attempt), 2.0))
    raise RuntimeError(f"norms ledger: {last + 1 if last is not None else 0} write conflicts on {NORMS_KEY}")


# ---------------------------------------------------------------- cells
def _seed_cell(c: dict) -> dict:
    v = _r1(c.get('value'))
    rng = c.get('range')
    srcs = [{'url': str(s.get('url') or ''), 'note': str(s.get('note') or '')} for s in (c.get('sources') or []) if isinstance(s, dict) and s.get('url')]
    return {'value': v, 'n': 0, 'seeded': True, 'seed_value': v, 'seed_weight': int(c.get('seed_weight') or SEED_WEIGHT),
            'seed_n_hint': c.get('n_hint'), 'range': ([_r1(rng[0]), _r1(rng[1])] if isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] is not None else None),
            'our_sum': 0.0, 'our_n': 0, 'tests': [], 'sources': srcs, 'basis': str(c.get('basis') or 'published norm'),
            'as_of': str(c.get('as_of') or ''), 'context': str(c.get('context') or ''), 'populations': list(c.get('populations') or []), 'updated_at': _now()}


def _same_seed(cur: dict, want: dict) -> bool:
    return (_r1(cur.get('seed_value')) == _r1(want.get('seed_value')) and (cur.get('range') or None) == (want.get('range') or None)
            and [s.get('url') for s in cur.get('sources') or []] == [s.get('url') for s in want.get('sources') or []]
            and [s.get('note') for s in cur.get('sources') or []] == [s.get('note') for s in want.get('sources') or []])


def seed(s3=None, cells: Optional[list] = None, dry_run: bool = False) -> dict:
    """Write the published anchors. Idempotent: an existing seeded cell
    with the same seed is left alone; a cell our own tests have blended
    into is never overwritten (its seed fields are refreshed only).
    Returns counts and the distinct source URLs."""
    cells = list(cells if cells is not None else SEED_CELLS)
    stats = {'written': 0, 'updated': 0, 'skipped': 0, 'kept_accumulated': 0, 'cells': 0, 'sources': 0}
    urls = set()
    for c in cells:
        for s in c.get('sources') or []:
            if isinstance(s, dict) and s.get('url'):
                urls.add(str(s['url']))
    stats['sources'] = len(urls)
    stats['cells'] = len(cells)

    def mutate(doc):
        changed = False
        for c in cells:
            if not all(c.get(k) for k in ('format', 'genre', 'metric')) or c.get('value') is None:
                stats['skipped'] += 1
                continue
            k = cell_key(c['format'], c['genre'], c['metric'])
            want = _seed_cell(c)
            cur = doc['cells'].get(k)
            if cur is None:
                doc['cells'][k] = want
                stats['written'] += 1
                changed = True
            elif not cur.get('seeded') and int(cur.get('our_n') or 0) > 0:
                # our own tests already blend in: keep the accumulated value, refresh the seed fields only
                if not _same_seed(cur, want):
                    cur.update({'seed_value': want['seed_value'], 'seed_weight': want['seed_weight'], 'range': want['range'], 'sources': want['sources'],
                                'basis': want['basis'], 'as_of': want['as_of']})
                    cur['value'] = _blend(cur)
                    cur['updated_at'] = _now()
                    changed = True
                stats['kept_accumulated'] += 1
            elif _same_seed(cur, want):
                stats['skipped'] += 1
            else:
                doc['cells'][k] = want
                stats['updated'] += 1
                changed = True
        return doc if (changed and not dry_run) else None
    if dry_run or s3 is None and _s3_default() is None:
        doc = load(s3) if not dry_run else _empty()
        mutate(doc)
        return stats
    update(s3, mutate)
    return stats


def _blend(cell: dict) -> float:
    our_n = int(cell.get('our_n') or 0)
    our_sum = float(cell.get('our_sum') or 0.0)
    if cell.get('seed_value') is not None:
        w = float(cell.get('seed_weight') or SEED_WEIGHT)
        return round((float(cell['seed_value']) * w + our_sum) / (w + our_n), 4) if (w + our_n) > 0 else float(cell['seed_value'])
    return round(our_sum / our_n, 4) if our_n else 0.0


def record(s3, fmt: str, genre: str, metrics: dict, test_id: str) -> Optional[dict]:
    """One finished test joins the ledger: every metric lands in the
    genre cell and in the all-genres cell for the format (running mean,
    n + 1). A test id is counted once per cell. Returns the written doc."""
    fmt = _fmt(fmt)
    genre = _genre(genre)
    tid = str(test_id or '').strip()
    rows = {str(k).strip().lower(): float(v) for k, v in (metrics or {}).items() if k in METRICS and v is not None and _is_num(v)}
    if not rows or not tid:
        return None
    genres = ['all'] + ([genre] if genre != 'all' else [])

    def mutate(doc):
        changed = False
        for g in genres:
            for m, v in rows.items():
                k = cell_key(fmt, g, m)
                cell = doc['cells'].get(k)
                if cell is None:
                    cell = {'value': 0.0, 'n': 0, 'seeded': False, 'seed_value': None, 'seed_weight': 0, 'seed_n_hint': None, 'range': None,
                            'our_sum': 0.0, 'our_n': 0, 'tests': [], 'sources': [], 'basis': 'our tests', 'as_of': '', 'updated_at': _now()}
                    doc['cells'][k] = cell
                tests = cell.setdefault('tests', [])
                if tid in tests:
                    continue
                tests.append(tid)
                if len(tests) > MAX_TESTS_PER_CELL:
                    del tests[:len(tests) - MAX_TESTS_PER_CELL]
                cell['our_sum'] = round(float(cell.get('our_sum') or 0.0) + max(0.0, min(100.0, v)), 4)
                cell['our_n'] = int(cell.get('our_n') or 0) + 1
                cell['n'] = cell['our_n']
                cell['value'] = _blend(cell)
                cell['seeded'] = False
                if cell.get('seed_value') is not None and not any('seed' in str(s.get('note') or '').lower() and 'kept' in str(s.get('note') or '').lower() for s in cell.get('sources') or []):
                    cell.setdefault('sources', []).append({'url': '', 'note': f"Seed {cell['seed_value']} kept as {cell.get('seed_weight') or SEED_WEIGHT} tests in the running mean."})
                cell['updated_at'] = _now()
                changed = True
        return doc if changed else None
    return update(s3, mutate)


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _lookup(cells: dict, fmt: str, genre: str, metric: str):
    """The cell for (format, genre, metric), walking the format fallback.
    Returns (cell, format_used) or (None, None)."""
    for f in (fmt,) + tuple(FORMAT_FALLBACK.get(fmt) or ()):
        c = cells.get(cell_key(f, genre, metric))
        if c is not None:
            return c, f
    return None, None


def _public(cell: dict, fmt_used: str, genre_used: str) -> dict:
    return {'value': _r1(cell.get('value')), 'n': int(cell.get('n') or cell.get('our_n') or 0), 'seeded': bool(cell.get('seeded', True)),
            'format_used': fmt_used, 'genre_used': genre_used}


def norms_for(fmt: str, genre: str = 'all', s3=None, doc: Optional[dict] = None) -> dict:
    """{format, genre, genre_used, all: {metric: {value, n, seeded}},
    genre: {metric: ...}} with no sources. ``genre`` falls back to all
    when the genre has no cell; a format falls back to its nearest
    format. Values at one decimal."""
    fmt = _fmt(fmt)
    genre = _genre(genre)
    doc = doc if isinstance(doc, dict) and isinstance(doc.get('cells'), dict) else load(s3)
    cells = doc['cells']
    out_all, out_genre = {}, {}
    genre_hits = 0
    for m in METRICS:
        c, f = _lookup(cells, fmt, 'all', m)
        if c is not None:
            out_all[m] = _public(c, f, 'all')
        if genre != 'all':
            cg, fg = _lookup(cells, fmt, genre, m)
            if cg is not None:
                out_genre[m] = _public(cg, fg, genre)
                genre_hits += 1
            elif c is not None:
                out_genre[m] = dict(out_all[m], genre_used='all')
        else:
            if c is not None:
                out_genre[m] = dict(out_all[m])
    return {'format': fmt, 'genre_key': genre, 'genre_used': genre if genre_hits else 'all', 'all': out_all, 'genre': out_genre if genre != 'all' else dict(out_all),
            'genre_label': GENRE_LABEL.get(genre, genre.title()), 'format_label': FORMAT_LABEL.get(fmt, fmt), 'from_seed_only': bool(doc.get('in_memory'))}


def norm_line(metric: str, ours, norms: dict, fmt: Optional[str] = None, genre: Optional[str] = None) -> str:
    """The plain comparison under a money number: 'Definitely recommend
    68%, comedy pilots usually land near 50%.'"""
    label = METRIC_LABEL.get(metric, metric.replace('_', ' ').title())
    try:
        v = float(ours)
    except (TypeError, ValueError):
        return ''
    fmt = fmt or (norms or {}).get('format') or 'pilot'
    genre = genre or (norms or {}).get('genre_key') or 'all'
    g = ((norms or {}).get('genre') or {}).get(metric) if isinstance((norms or {}).get('genre'), dict) else None
    a = ((norms or {}).get('all') or {}).get(metric)
    pick = g if (g and g.get('genre_used') not in (None, 'all')) else a
    if not pick:
        return f"{label} {v:.1f}%."
    noun = FORMAT_LABEL.get(fmt, fmt) if str(pick.get('format_used') or fmt) == fmt else 'screenings'
    who = f"{GENRE_LABEL.get(genre, genre).lower()} {noun}" if (pick is g and genre != 'all') else noun
    nv = float(pick.get('value') or 0)
    verb = 'usually land near'
    if v >= nv + 8:
        tail = f"{who} {verb} {nv:.0f}%; this one sits well above."
    elif v <= nv - 8:
        tail = f"{who} {verb} {nv:.0f}%; this one sits below."
    else:
        tail = f"{who} {verb} {nv:.0f}%."
    return f"{label} {v:.1f}%, {tail}"


def sources_report(doc: Optional[dict] = None, s3=None) -> dict:
    """Internal audit: how many cells, how many seeded, distinct URLs."""
    doc = doc if isinstance(doc, dict) else load(s3)
    cells = doc.get('cells') or {}
    urls = set()
    seeded = 0
    for c in cells.values():
        if c.get('seeded'):
            seeded += 1
        for s in c.get('sources') or []:
            if s.get('url'):
                urls.add(s['url'])
    return {'cells': len(cells), 'seeded': seeded, 'accumulated': len(cells) - seeded, 'sources': len(urls), 'in_memory': bool(doc.get('in_memory'))}


__all__ = ['NORMS_KEY', 'BACKUP_PREFIX', 'FORMATS', 'GENRES', 'METRICS', 'METRIC_LABEL', 'GENRE_LABEL', 'FORMAT_LABEL', 'SEED_CELLS', 'SEED_WEIGHT',
           'cell_key', 'load', 'update', 'seed', 'record', 'norms_for', 'norm_line', 'sources_report']
