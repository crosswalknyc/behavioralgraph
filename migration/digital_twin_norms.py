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
MIN_OWN_TESTS = 5         # a cell with no published anchor needs this many of our tests before it reads as a norm (2026-11-03:
                          # three trial runs of one pilot had become the pilot norm, 43.7 total positive)

FORMATS = ('feature', 'pilot', 'episode', 'trailer', 'title', 'key_art', 'script', 'concept')
# the norm sets a test can be compared to, in the order the Compare-to list shows them (Jenna 2026-11-04: the user
# sets the norm; 'procedural' joined for TV, NCIS: New York read against the all-pilots column until then)
GENRES = ('all', 'procedural', 'drama', 'comedy', 'action', 'thriller', 'horror', 'romance', 'family', 'animation', 'documentary', 'unscripted')
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
GENRE_LABEL = {'all': 'All', 'procedural': 'Procedural', 'comedy': 'Comedy', 'drama': 'Drama', 'action': 'Action', 'horror': 'Horror', 'thriller': 'Thriller',
               'romance': 'Romance', 'family': 'Family', 'animation': 'Animation', 'documentary': 'Documentary', 'unscripted': 'Unscripted'}
FORMAT_LABEL = {'feature': 'features', 'pilot': 'pilots', 'episode': 'episodes', 'trailer': 'trailers', 'title': 'titles',
                'key_art': 'key art', 'script': 'scripts', 'concept': 'concepts'}
FORMAT_SINGULAR = {'feature': 'feature', 'pilot': 'pilot', 'episode': 'episode', 'trailer': 'trailer', 'title': 'title',
                   'key_art': 'key art', 'script': 'script', 'concept': 'concept'}
# the mode a test was opened in, when it is not one of the formats, for the Compare-to list
_MODE_FORMAT = {'binge': 'episode', 'spot': 'trailer'}


def choice_label(fmt: str, genre: str = 'all') -> str:
    """The norm a test is compared to, in words: 'All pilots',
    'Procedural pilots', 'All key art'."""
    f = _fmt(_MODE_FORMAT.get(str(fmt or '').strip().lower(), fmt))
    g = _genre(genre)
    noun = FORMAT_LABEL.get(f, f)
    return f"All {noun}" if g == 'all' else f"{GENRE_LABEL.get(g, g.title())} {noun}"


def norm_options(fmt: str) -> list:
    """The Compare-to list for a format: the all-genre column first
    ('All pilots'), then every genre by name."""
    return [{'value': '', 'label': choice_label(fmt, 'all')}] + [{'value': g, 'label': GENRE_LABEL.get(g, g.title())} for g in GENRES if g != 'all']

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
             'scifi': 'action', 'sci-fi': 'action', 'adventure': 'action', 'superhero': 'action', 'crime': 'thriller', 'mystery': 'thriller',
             'procedurals': 'procedural', 'police procedural': 'procedural', 'crime procedural': 'procedural', 'cop': 'procedural', 'cop show': 'procedural',
             'police': 'procedural', 'detective': 'procedural', 'dramas': 'drama', 'prestige': 'drama', 'prestige drama': 'drama'}
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


def seed(s3=None, cells: Optional[list] = None, dry_run: bool = False, drop_tests: Optional[list] = None, prune_unseeded: bool = False) -> dict:
    """Write the published anchors. Idempotent: an existing seeded cell
    with the same seed is left alone; a cell our own tests have blended
    into is never overwritten (its seed fields are refreshed only).
    ``drop_tests`` names test ids whose reads leave every cell first
    (trial runs of the product); a cell left with no tests of its own is
    rebuilt from its seed, or removed when it has none. ``prune_unseeded``
    also removes cells that have no seed and no tests. Returns counts and
    the distinct source URLs."""
    cells = list(cells if cells is not None else SEED_CELLS)
    stats = {'written': 0, 'updated': 0, 'skipped': 0, 'kept_accumulated': 0, 'cells': 0, 'sources': 0, 'dropped_tests': 0, 'removed': 0}
    drop = {str(t) for t in (drop_tests or []) if str(t)}
    urls = set()
    for c in cells:
        for s in c.get('sources') or []:
            if isinstance(s, dict) and s.get('url'):
                urls.add(str(s['url']))
    stats['sources'] = len(urls)
    stats['cells'] = len(cells)

    def mutate(doc):
        changed = False
        if drop:
            for k, cur in list(doc['cells'].items()):
                tests = [t for t in (cur.get('tests') or [])]
                hit = [t for t in tests if t in drop]
                if not hit:
                    continue
                # the trial reads leave the running mean; the cell goes back to its seed (or away, when it never had one)
                stats['dropped_tests'] += len(hit)
                kept = [t for t in tests if t not in drop]
                if kept:
                    # the per-test values are not kept, so the mean of the remaining tests cannot be rebuilt exactly: scale the sum
                    cur['our_sum'] = round(float(cur.get('our_sum') or 0.0) * len(kept) / float(len(tests)), 4)
                    cur['our_n'] = len(kept)
                    cur['tests'] = kept
                    cur['n'] = cur['our_n']
                    cur['value'] = _blend(cur)
                elif cur.get('seed_value') is not None:
                    cur.update({'our_sum': 0.0, 'our_n': 0, 'tests': [], 'n': 0, 'seeded': True, 'value': float(cur['seed_value'])})
                    cur['sources'] = [x for x in (cur.get('sources') or []) if not str(x.get('note') or '').startswith('Seed ')]
                else:
                    del doc['cells'][k]
                    stats['removed'] += 1
                    changed = True
                    continue
                cur['updated_at'] = _now()
                changed = True
        if prune_unseeded:
            for k, cur in list(doc['cells'].items()):
                if cur.get('seed_value') is None and not int(cur.get('our_n') or 0):
                    del doc['cells'][k]
                    stats['removed'] += 1
                    changed = True
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
                    # a format with no cell of its own starts from the nearest format's published anchor (a pilot from the recruited-screening
                    # norms), so the first test blends into the anchor instead of standing as the norm (2026-11-03)
                    near, near_fmt = _lookup(doc['cells'], fmt, g, m)
                    near_genre = g
                    if near is None and g != 'all':
                        near, near_fmt = _lookup(doc['cells'], fmt, 'all', m)
                        near_genre = 'all'
                    if near is not None and near.get('seed_value') is not None:
                        # a genre cell that borrows the all-genres anchor is not a genre norm yet: it reads as one only once
                        # MIN_OWN_TESTS of our own tests sit in it (the anchor_genre field is what _lookup checks, 2026-11-04)
                        cell.update({'seed_value': float(near['seed_value']), 'seed_weight': int(near.get('seed_weight') or SEED_WEIGHT), 'range': near.get('range'),
                                     'sources': [dict(x) for x in (near.get('sources') or []) if not str(x.get('note') or '').startswith('Seed ')],
                                     'basis': f"{near.get('basis') or 'published norm'} ({near_fmt} {near_genre if near_genre != 'all' else 'all genres'} anchor)", 'as_of': near.get('as_of') or '',
                                     'anchor_genre': near_genre, 'anchor_format': near_fmt})
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


def forget_test(s3, test_id: str) -> Optional[dict]:
    """A deleted test leaves every cell it joined (its share of the
    running mean goes with it; a cell left with no tests of its own goes
    back to its published anchor, or away when it never had one). The
    test's own values are not kept per cell, so the remaining mean is
    rebuilt by scaling the sum. Idempotent; never raises into a route."""
    tid = str(test_id or '').strip()
    if not tid:
        return None

    def mutate(doc):
        changed = False
        for k, cur in list(doc['cells'].items()):
            tests = list(cur.get('tests') or [])
            if tid not in tests:
                continue
            kept = [t for t in tests if t != tid]
            if kept:
                cur['our_sum'] = round(float(cur.get('our_sum') or 0.0) * len(kept) / float(len(tests)), 4)
                cur['our_n'] = len(kept)
                cur['tests'] = kept
                cur['n'] = cur['our_n']
                cur['value'] = _blend(cur)
            elif cur.get('seed_value') is not None:
                cur.update({'our_sum': 0.0, 'our_n': 0, 'tests': [], 'n': 0, 'seeded': True, 'value': float(cur['seed_value'])})
                cur['sources'] = [x for x in (cur.get('sources') or []) if not str(x.get('note') or '').startswith('Seed ')]
            else:
                del doc['cells'][k]
                changed = True
                continue
            cur['updated_at'] = _now()
            changed = True
        return doc if changed else None
    try:
        return update(s3, mutate)
    except Exception as e:
        print(f"[norms] forget {tid} skipped: {e}")
        return None


def _is_num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _own_anchor(cell: dict, genre: str) -> bool:
    """A published anchor for THIS genre. A genre cell that only borrowed
    the all-genres anchor (record() seeds one so the first test blends in)
    does not read as a genre norm until MIN_OWN_TESTS of our tests sit in
    it. Older ledgers carry the borrow in the basis string."""
    if cell.get('seed_value') is None:
        return False
    if _genre(genre) == 'all':
        return True
    ag = cell.get('anchor_genre')
    if ag is not None:
        return str(ag) != 'all'
    return 'all genres anchor' not in str(cell.get('basis') or '')


def _lookup(cells: dict, fmt: str, genre: str, metric: str):
    """The cell for (format, genre, metric), walking the format fallback.
    Returns (cell, format_used) or (None, None)."""
    for f in (fmt,) + tuple(FORMAT_FALLBACK.get(fmt) or ()):
        c = cells.get(cell_key(f, genre, metric))
        if c is not None and (_own_anchor(c, genre) or int(c.get('our_n') or 0) >= MIN_OWN_TESTS):
            return c, f
    return None, None


def own_tests(cells: dict, fmt: str, genre: str) -> int:
    """How many of our own tests sit in a genre's cells for a format
    (distinct test ids across the format's cells for that genre)."""
    fmt, genre = _fmt(fmt), _genre(genre)
    seen = set()
    for m in METRICS:
        c = cells.get(cell_key(fmt, genre, m))
        if c is not None:
            seen.update(str(t) for t in (c.get('tests') or []))
    return len(seen)


def forget_test_genre(s3, test_id: str, fmt: str, genre: str) -> Optional[dict]:
    """A test re-filed under another norm leaves the cells of the genre
    it was filed under (the all-genres cells keep it). Same arithmetic as
    forget_test. Never raises into a route."""
    tid = str(test_id or '').strip()
    fmt, genre = _fmt(fmt), _genre(genre)
    if not tid or genre == 'all':
        return None
    prefix = f"{fmt}|{genre}|"

    def mutate(doc):
        changed = False
        for k, cur in list(doc['cells'].items()):
            if not k.startswith(prefix):
                continue
            tests = list(cur.get('tests') or [])
            if tid not in tests:
                continue
            kept = [t for t in tests if t != tid]
            if kept:
                cur['our_sum'] = round(float(cur.get('our_sum') or 0.0) * len(kept) / float(len(tests)), 4)
                cur['our_n'] = len(kept)
                cur['tests'] = kept
                cur['n'] = cur['our_n']
                cur['value'] = _blend(cur)
            elif cur.get('seed_value') is not None:
                cur.update({'our_sum': 0.0, 'our_n': 0, 'tests': [], 'n': 0, 'seeded': True, 'value': float(cur['seed_value'])})
                cur['sources'] = [x for x in (cur.get('sources') or []) if not str(x.get('note') or '').startswith('Seed ')]
            else:
                del doc['cells'][k]
                changed = True
                continue
            cur['updated_at'] = _now()
            changed = True
        return doc if changed else None
    try:
        return update(s3, mutate)
    except Exception as e:
        print(f"[norms] re-file {tid} out of {prefix} skipped: {e}")
        return None


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
            'genre_label': GENRE_LABEL.get(genre, genre.title()), 'format_label': FORMAT_LABEL.get(fmt, fmt), 'from_seed_only': bool(doc.get('in_memory')),
            'choice': norm_choice(fmt, genre, cells, has_genre_norm=bool(genre_hits))}


def norm_choice(fmt: str, genre: str, cells: Optional[dict] = None, has_genre_norm: Optional[bool] = None) -> dict:
    """What a test is compared to, for the card and the dial header:
    {genre, label ('Procedural pilots'), all_label ('All pilots'),
    has_genre_norm, own_tests, needed, note}. The note is the honest
    line when the chosen genre has no norm yet: 'No norm yet for
    procedural pilots (1 of 5 tests).'"""
    fmt, genre = _fmt(fmt), _genre(genre)
    cells = cells if isinstance(cells, dict) else {}
    if has_genre_norm is None:
        has_genre_norm = genre != 'all' and any(_lookup(cells, fmt, genre, m)[0] is not None for m in METRICS)
    n = own_tests(cells, fmt, genre) if genre != 'all' else 0
    note = ''
    if genre != 'all' and not has_genre_norm:
        note = f"No norm yet for {GENRE_LABEL.get(genre, genre).lower()} {FORMAT_LABEL.get(fmt, fmt)} ({min(n, MIN_OWN_TESTS)} of {MIN_OWN_TESTS} tests)."
    return {'genre': genre if genre != 'all' else '', 'label': choice_label(fmt, genre), 'all_label': choice_label(fmt, 'all'),
            'has_genre_norm': bool(has_genre_norm) if genre != 'all' else False, 'own_tests': n, 'needed': MIN_OWN_TESTS, 'note': note}


_POINT_WORDS = {0: 'right at', 1: 'one point', 2: 'two points', 3: 'three points', 4: 'four points', 5: 'five points', 6: 'six points', 7: 'seven points',
                8: 'eight points', 9: 'nine points', 10: 'ten points', 11: 'eleven points', 12: 'twelve points'}


def norm_line(metric: str, ours, norms: dict, fmt: Optional[str] = None, genre: Optional[str] = None) -> str:
    """The plain comparison under a money number, naming the norm it is
    read against: 'Definitely recommend 54.0%, two points above the
    procedural pilot norm of 52%.' A chosen genre with no norm yet reads
    against the all-genres column and says so ('the all-pilots norm')."""
    label = METRIC_LABEL.get(metric, metric.replace('_', ' ').title())
    try:
        v = float(ours)
    except (TypeError, ValueError):
        return ''
    fmt = _fmt(fmt or (norms or {}).get('format') or 'pilot')
    genre = _genre(genre or (norms or {}).get('genre_key') or 'all')
    g = ((norms or {}).get('genre') or {}).get(metric) if isinstance((norms or {}).get('genre'), dict) else None
    a = ((norms or {}).get('all') or {}).get(metric)
    pick = g if (g and g.get('genre_used') not in (None, 'all')) else a
    if not pick:
        return f"{label} {v:.1f}%."
    used = str(pick.get('format_used') or fmt)
    used = used if used in FORMATS else fmt
    if pick is g and genre != 'all':
        who = f"the {GENRE_LABEL.get(genre, genre).lower()} {FORMAT_SINGULAR.get(used, used)} norm"
    else:
        who = f"the all-{FORMAT_LABEL.get(used, used)} norm"
    nv = float(pick.get('value') or 0)
    pts = int(round(abs(v - nv)))
    if pts == 0:
        return f"{label} {v:.1f}%, right at {who} of {nv:.0f}%."
    gap = _POINT_WORDS.get(pts, f"{pts} points")
    return f"{label} {v:.1f}%, {gap} {'above' if v > nv else 'below'} {who} of {nv:.0f}%."


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


__all__ = ['NORMS_KEY', 'BACKUP_PREFIX', 'FORMATS', 'GENRES', 'METRICS', 'METRIC_LABEL', 'GENRE_LABEL', 'FORMAT_LABEL', 'FORMAT_SINGULAR', 'SEED_CELLS', 'SEED_WEIGHT',
           'MIN_OWN_TESTS', 'cell_key', 'load', 'update', 'seed', 'record', 'forget_test', 'forget_test_genre', 'own_tests', 'norms_for', 'norm_choice', 'norm_line',
           'choice_label', 'norm_options', 'sources_report']
