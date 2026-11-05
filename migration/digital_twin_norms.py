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

* stand-in (2026-11-05, Liz Huszarik's review of the NCIS: New York
  report): a cell whose anchor is the NEAREST instrument's (the pilot
  cells carry the feature recruited-screening card) reads as 'the
  feature screening norm' at its published value, never as 'the pilot
  norm', until MIN_OWN_TESTS of our own tests of that format sit in it.
  ``compared_label`` / ``compared_line`` / ``norm_word`` are the one
  source of the comparison's name for the card, the report and the CSV;
  ``format_note`` / ``genre_note`` / ``metric_note`` are the honest
  'no norm yet' lines, so no page shows a blank or a borrowed figure
  under the wrong name.

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


# ------------------------------------------------- stand-ins and the honest lines
# A cell whose published anchor is the NEAREST instrument's (the pilot and episode cells
# carry the feature recruited-screening card: excellent 30, total positive 65, definitely
# recommend 50, the character and component norms) is a STAND-IN. It reads as 'the feature
# screening norm' at its published value, never as 'the pilot norm', until MIN_OWN_TESTS of
# our own tests of that format sit in it; then the running mean reads as the format's norm.
# Liz Huszarik, 2026-11-05, on the NCIS: New York report: the 48% definitely recommend
# 'norm' was the feature figure (top box, films) presented as the all-pilots norm.
STAND_IN_FEATURE = 'feature screening'
STAND_IN_PLURAL = {'feature screening': 'feature screenings', 'pilot test': 'pilot tests', 'episode test': 'episode tests'}
_STAND_IN_BASIS = 'nearest instrument'
# the short adjective a measure takes in the honest line ('No would-finish norm yet')
METRIC_SHORT = {'finish': 'would-finish', 'tune_in': 'second-episode', 'urgency_opening': 'how-soon', 'confusion': 'confusion',
                'component_pace_just_right': 'pace', 'follow_second_episode': 'would-follow', 'walkouts': 'walkout'}
MONEY_METRICS = ('excellent', 'total_positive', 'definitely_recommend')


def _stand_in(cell: Optional[dict]) -> str:
    """The instrument a cell borrows ('feature screening') while it is not
    yet a norm of its own format; '' for a true anchor or a cell with
    MIN_OWN_TESTS of our own tests in it."""
    if not isinstance(cell, dict):
        return ''
    if int(cell.get('our_n') or 0) >= MIN_OWN_TESTS:
        return ''
    si = str(cell.get('stand_in') or '').strip().lower()
    if si:
        return si
    return STAND_IN_FEATURE if _STAND_IN_BASIS in str(cell.get('basis') or '') else ''


def _count_words(n: int) -> str:
    return f"{min(max(int(n or 0), 0), MIN_OWN_TESTS)} of {MIN_OWN_TESTS} so far"


def genre_note(fmt: str, genre: str, n: int) -> str:
    """'No procedural norm yet; it fills as tests are filed under
    procedural, 1 of 5 so far.'"""
    g = GENRE_LABEL.get(_genre(genre), str(genre or '').title()).lower()
    return f"No {g} norm yet; it fills as tests are filed under {g}, {_count_words(n)}."


def format_note(fmt: str, n: int) -> str:
    """'No pilot norm yet; it fills as pilot tests are filed, 1 of 5 so
    far.' (the all column is still a stand-in from another instrument)."""
    one = FORMAT_SINGULAR.get(_fmt(fmt), str(fmt or 'pilot'))
    return f"No {one} norm yet; it fills as {one} tests are filed, {_count_words(n)}."


def metric_note(metric: str, n: int) -> str:
    """'No would-finish norm yet; it fills as tests are filed, 1 of 5 so
    far.' for a measure with no published anchor and too few tests."""
    m = str(metric or '').strip().lower()
    short = METRIC_SHORT.get(m) or METRIC_LABEL.get(m, m.replace('_', ' ')).lower()
    return f"No {short} norm yet; it fills as tests are filed, {_count_words(n)}."


def compared_label(choice: dict) -> str:
    """The norm a card is actually read against, from its norm choice:
    'Procedural pilots' when the genre has a norm, 'All pilots' when the
    all column is a norm of the format, 'Feature screenings' while the
    all column is a stand-in. One source for the card, the report pages
    and the CSV."""
    ch = choice if isinstance(choice, dict) else {}
    if ch.get('compared_label'):
        return str(ch['compared_label'])
    if ch.get('has_genre_norm') and ch.get('label'):
        return str(ch['label'])
    si = str(ch.get('stand_in') or '').strip().lower()
    if si:
        return STAND_IN_PLURAL.get(si, si + 's').capitalize()
    return str(ch.get('all_label') or 'All pilots')


def compared_line(choice: dict) -> str:
    """'Compared to all pilots.' / 'Compared to procedural pilots.' /
    'Compared to feature screenings.'"""
    lab = compared_label(choice)
    return f"Compared to {lab[0].lower() + lab[1:]}." if lab else ''


def norm_word(choice: dict) -> str:
    """'the feature screening norm' / 'the all-pilots norm' / 'the
    procedural pilot norm': the words a comparison sentence uses."""
    ch = choice if isinstance(choice, dict) else {}
    si = str(ch.get('stand_in') or '').strip().lower()
    if si and not ch.get('has_genre_norm'):
        return f"the {si} norm"
    lab = compared_label(ch)
    parts = lab.split(' ', 1)
    if len(parts) == 2 and parts[0].lower() == 'all':
        return f"the all-{parts[1].lower()} norm"
    if len(parts) == 2:
        noun = parts[1].lower()
        one = next((FORMAT_SINGULAR[f] for f, p in FORMAT_LABEL.items() if p == noun), noun)
        return f"the {parts[0].lower()} {one} norm"
    return f"the {lab.lower()} norm"


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
    basis = str(c.get('basis') or 'published norm')
    # the instrument a nearest-instrument anchor borrows (the seed names it; an older seed file carries it in the basis)
    stand_in = str(c.get('stand_in') or (STAND_IN_FEATURE if _STAND_IN_BASIS in basis else '')).strip().lower()
    return {'value': v, 'n': 0, 'seeded': True, 'seed_value': v, 'seed_weight': int(c.get('seed_weight') or SEED_WEIGHT),
            'seed_n_hint': c.get('n_hint'), 'range': ([_r1(rng[0]), _r1(rng[1])] if isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] is not None else None),
            'our_sum': 0.0, 'our_n': 0, 'tests': [], 'sources': srcs, 'basis': basis, 'stand_in': stand_in,
            'as_of': str(c.get('as_of') or ''), 'context': str(c.get('context') or ''), 'populations': list(c.get('populations') or []), 'updated_at': _now()}


def _same_seed(cur: dict, want: dict) -> bool:
    return (_r1(cur.get('seed_value')) == _r1(want.get('seed_value')) and (cur.get('range') or None) == (want.get('range') or None)
            and [s.get('url') for s in cur.get('sources') or []] == [s.get('url') for s in want.get('sources') or []]
            and [s.get('note') for s in cur.get('sources') or []] == [s.get('note') for s in want.get('sources') or []]
            and str(cur.get('stand_in') or '') == str(want.get('stand_in') or ''))


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
                                'basis': want['basis'], 'as_of': want['as_of'], 'stand_in': want.get('stand_in') or ''})
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
                        # a cell of another format borrows that format's instrument too: a pilot cell started from the feature
                        # card is a feature-screening stand-in until MIN_OWN_TESTS pilot tests sit in it
                        borrowed = _stand_in(dict(near, our_n=0)) or (STAND_IN_FEATURE if near_fmt == 'feature' and fmt != 'feature' else '')
                        cell.update({'seed_value': float(near['seed_value']), 'seed_weight': int(near.get('seed_weight') or SEED_WEIGHT), 'range': near.get('range'),
                                     'sources': [dict(x) for x in (near.get('sources') or []) if not str(x.get('note') or '').startswith('Seed ')],
                                     'basis': f"{near.get('basis') or 'published norm'} ({near_fmt} {near_genre if near_genre != 'all' else 'all genres'} anchor)", 'as_of': near.get('as_of') or '',
                                     'anchor_genre': near_genre, 'anchor_format': near_fmt, 'stand_in': borrowed})
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
    """The cell as a page reads it. A stand-in reads at its PUBLISHED
    value (the feature screening norm is 50, not 50 blended with one
    pilot) and names the instrument it borrows; a norm of the format
    reads at the running mean."""
    si = _stand_in(cell)
    value = _r1(cell.get('seed_value')) if (si and cell.get('seed_value') is not None) else _r1(cell.get('value'))
    return {'value': value, 'n': int(cell.get('n') or cell.get('our_n') or 0), 'seeded': bool(cell.get('seeded', True)),
            'format_used': fmt_used, 'genre_used': genre_used, 'stand_in': si}


def _pending(cells: dict, fmt: str, genre: str, metric: str) -> dict:
    """How far a measure with no norm yet has come: {own_tests, needed}
    from the cell our tests are filling (the genre's, else the all
    column's)."""
    n = 0
    for g in ((genre, 'all') if genre != 'all' else ('all',)):
        c = cells.get(cell_key(fmt, g, metric))
        if isinstance(c, dict):
            n = max(n, int(c.get('our_n') or 0))
    return {'own_tests': n, 'needed': MIN_OWN_TESTS, 'note': metric_note(metric, n)}


def norms_for(fmt: str, genre: str = 'all', s3=None, doc: Optional[dict] = None) -> dict:
    """{format, genre, genre_used, all: {metric: {value, n, seeded,
    stand_in}}, genre: {metric: ...}, pending: {metric: {own_tests,
    needed, note}}, stand_in, choice} with no sources. ``genre`` falls
    back to all when the genre has no cell; a format falls back to its
    nearest format. Values at one decimal. ``pending`` carries the
    measures with no norm yet (no anchor, fewer than MIN_OWN_TESTS of
    our tests) so a page can say so in words instead of leaving a
    blank; ``stand_in`` names the instrument the all column borrows
    while it is not yet a norm of the format ('feature screening')."""
    fmt = _fmt(fmt)
    genre = _genre(genre)
    doc = doc if isinstance(doc, dict) and isinstance(doc.get('cells'), dict) else load(s3)
    cells = doc['cells']
    out_all, out_genre, pending = {}, {}, {}
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
        if c is None and (genre == 'all' or m not in out_genre):
            pending[m] = _pending(cells, fmt, genre, m)
    stand_in = _column_stand_in(out_all)
    return {'format': fmt, 'genre_key': genre, 'genre_used': genre if genre_hits else 'all', 'all': out_all, 'genre': out_genre if genre != 'all' else dict(out_all),
            'pending': pending, 'stand_in': stand_in,
            'genre_label': GENRE_LABEL.get(genre, genre.title()), 'format_label': FORMAT_LABEL.get(fmt, fmt), 'from_seed_only': bool(doc.get('in_memory')),
            'choice': norm_choice(fmt, genre, cells, has_genre_norm=bool(genre_hits), stand_in=stand_in)}


def _column_stand_in(column: dict) -> str:
    """The instrument the money cells of a column borrow, when they all
    do ('feature screening'); '' when the column is a norm of the
    format (or carries no money cell)."""
    sis = [str((column.get(m) or {}).get('stand_in') or '') for m in MONEY_METRICS if column.get(m)]
    return sis[0] if sis and all(sis) and len(set(sis)) == 1 else ''


def norm_choice(fmt: str, genre: str, cells: Optional[dict] = None, has_genre_norm: Optional[bool] = None, stand_in: Optional[str] = None) -> dict:
    """What a test is compared to, for the card, the report pages, the
    CSV and the dial header: {genre, label ('Procedural pilots', the
    chosen genre), all_label ('All pilots'), has_genre_norm, own_tests,
    needed, stand_in, compared_label, compared_line, notes, note}.

    ``compared_label`` is the norm the card is actually read against:
    the genre's when it has a norm, else 'All pilots' when the all
    column is a norm of the format, else the instrument the all column
    borrows ('Feature screenings'). ``notes`` are the honest lines, each
    once: 'No pilot norm yet; it fills as pilot tests are filed, 1 of 5
    so far.' while the all column is a stand-in, and 'No procedural
    norm yet; it fills as tests are filed under procedural, 1 of 5 so
    far.' when the chosen genre has none. ``note`` joins them."""
    fmt, genre = _fmt(fmt), _genre(genre)
    cells = cells if isinstance(cells, dict) else {}
    if has_genre_norm is None:
        has_genre_norm = genre != 'all' and any(_lookup(cells, fmt, genre, m)[0] is not None for m in METRICS)
    if stand_in is None:
        col = {}
        for m in MONEY_METRICS:
            c, f = _lookup(cells, fmt, 'all', m)
            if c is not None:
                col[m] = _public(c, f, 'all')
        stand_in = _column_stand_in(col)
    has_genre_norm = bool(has_genre_norm) if genre != 'all' else False
    n = own_tests(cells, fmt, genre) if genre != 'all' else 0
    n_all = own_tests(cells, fmt, 'all')
    notes = []
    if not has_genre_norm and stand_in:
        notes.append(format_note(fmt, n_all))
    if genre != 'all' and not has_genre_norm:
        notes.append(genre_note(fmt, genre, n))
    out = {'genre': genre if genre != 'all' else '', 'label': choice_label(fmt, genre), 'all_label': choice_label(fmt, 'all'),
           'has_genre_norm': has_genre_norm, 'own_tests': n, 'needed': MIN_OWN_TESTS, 'stand_in': stand_in if not has_genre_norm else ''}
    out['compared_label'] = compared_label(out)
    out['compared_line'] = compared_line(out)
    out['notes'] = notes
    out['note'] = ' '.join(notes)
    return out


_POINT_WORDS = {0: 'right at', 1: 'one point', 2: 'two points', 3: 'three points', 4: 'four points', 5: 'five points', 6: 'six points', 7: 'seven points',
                8: 'eight points', 9: 'nine points', 10: 'ten points', 11: 'eleven points', 12: 'twelve points'}


def norm_line(metric: str, ours, norms: dict, fmt: Optional[str] = None, genre: Optional[str] = None, tally: str = '') -> str:
    """The plain comparison under a money number, naming the norm it is
    read against: 'Definitely recommend 54.0%, two points above the
    procedural pilot norm of 52%.' A chosen genre with no norm yet reads
    against the all-genres column and says so ('the all-pilots norm');
    a column that is still a stand-in names the instrument it borrows
    ('the feature screening norm'). A measure with no norm yet says so
    in words, never a bare figure. ``tally`` ('5 of 16') rides beside
    the share when given."""
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
    shown = f"{label} {v:.1f}%" + (f" ({tally})" if tally else '')
    if not pick:
        pend = ((norms or {}).get('pending') or {}).get(metric) if isinstance((norms or {}).get('pending'), dict) else None
        return f"{shown}. {pend['note']}" if isinstance(pend, dict) and pend.get('note') else f"{shown}."
    used = str(pick.get('format_used') or fmt)
    used = used if used in FORMATS else fmt
    si = str(pick.get('stand_in') or '')
    if si and not (pick is g and genre != 'all'):
        who = f"the {si} norm"
    elif pick is g and genre != 'all':
        who = f"the {GENRE_LABEL.get(genre, genre).lower()} {FORMAT_SINGULAR.get(used, used)} norm"
    else:
        who = f"the all-{FORMAT_LABEL.get(used, used)} norm"
    nv = float(pick.get('value') or 0)
    pts = int(round(abs(v - nv)))
    if pts == 0:
        return f"{shown}, right at {who} of {nv:.0f}%."
    gap = _POINT_WORDS.get(pts, f"{pts} points")
    return f"{shown}, {gap} {'above' if v > nv else 'below'} {who} of {nv:.0f}%."


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
           'MIN_OWN_TESTS', 'MONEY_METRICS', 'STAND_IN_FEATURE', 'cell_key', 'load', 'update', 'seed', 'record', 'forget_test', 'forget_test_genre', 'own_tests',
           'norms_for', 'norm_choice', 'norm_line', 'choice_label', 'norm_options', 'sources_report',
           'compared_label', 'compared_line', 'norm_word', 'genre_note', 'format_note', 'metric_note']
