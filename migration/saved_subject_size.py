"""A saved audience size starts every fresh build of its subject.

Jenna, 2026-11-20 (verbatim): "you'd save the number so that you have the
audience size to use if someone were to pull it." The VICE Sports audience
was researched and saved that day (48,172 in the data, 1,589,194 US people)
for the Just For Men proposal; a later pull of VICE Sports has to land on
that audience, not on a fresh guess at intake.

Where a saved size lives (first found wins)
-------------------------------------------
1. A decision of record in the insights ledger (``bank_record``): an
   audience-family record entry on the subject's bucket that carries the
   whole audience as ``<subject_key>_us_audience`` (or ``us_audience``),
   with its count in the data on ``sample_people``. A group inside the
   audience (``segment_us``, men 35 and older) is never the whole.
2. ``scripts/_subject_estimates.ESTIMATES``: an entry that is already
   messy (the house no-round rule: last digit 1 to 9, never a placeholder
   literal). Round entries are the older placeholder table the sweeps and
   audits read (most sit 2x to 12x above their subject's Gen Pop row);
   they never size a build. ``WINDOWS`` dates an entry, ``ALIASES`` names
   the other ways the subject is written.
3. The saved-size table (``system/saved_subject_sizes.json``): the size a
   read on a subject we do not hold found, saved by
   migration/unheld_subjects (2026-11-23, Jenna approved building profiles
   for subjects people ask about that we do not hold). The first size
   saved for a subject stands.
4. The Prometheus anchor store (``system/pm_anchors/``): a record-sourced
   audience anchor named for the subject on the subject's own document.
   The largest one is the whole audience (the others are groups in it).

When it applies
---------------
* Fresh builds only: ``new_build``, the parent Total Universe of a
  ``cut_needs_parent``, and a refresh that has no parent at all. A
  time-shifted refresh with a parent sizes off it under the refresh clamp,
  and a cut sizes off its parent; neither is touched here.
* The subject is a Total Universe name (no `` - Cut`` suffix), matched case
  and punctuation insensitively with a leading "the" and a trailing "fans"
  or "audience" set aside, or through an alias. A US build of the whole
  subject: not a title narrowed to a season or a service, not another
  country.
* The window is the saved one's: about the same length and mostly
  overlapping (an undated entry stands for a trailing year).
* Not when the reader set the size: a sample locked from a sample check, a
  client-supplied count, or a sample size written in the ask (this turn or
  the reader's last few turns).
* Not when the subject's own Gen Pop row or prior Total Universe puts the
  saved size outside the benchmark band
  (``profile_build_size_guards.benchmark_verdict``); the house's read
  governs there, as it already does.

What it does
------------
``subject_raw_tu`` becomes the saved count exactly (it is already messy, so
the no-round rule holds without another salt); a public-metric ceiling
still caps it. The avid sample keeps its share of the Total Universe (the
worker re-sizes the Avid cut from the final Total Universe and its reasoned
AVID FAN share). The anchor chain reads the saved US people with an engaged
share of 1.0 (a Total Universe already counts its audience), so the anchor
guard keeps the figure. The draft or spec carries ``sample_size_saved`` so
the later steps (the brief size hold, the sizing ring, the worker's messy
pass, creator sizing and metric-slip snap) hold it, and
``sample_size_user_set`` when the reader set the size.

Lives in both repos (migration/ in the parent, bg-webapp/migration/ in the
submodule); keep the twins byte-equal. Never raises; any failure leaves the
draft or spec exactly as it came. Regression:
scripts/test_saved_subject_size.py.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
import unicodedata

US_POP = 329_900_000
PANEL = 10_000_000
PANEL_TO_US = US_POP / PANEL
BUCKET = 'dashboard-inputs'
ANCHOR_PREFIX = 'system/pm_anchors/'
TABLE_KEY = 'system/saved_subject_sizes.json'
CACHE_TTL_S = 300
FRESH = ('new_build', 'cut_needs_parent', '')
WINDOW_LEN_RATIO = (0.8, 1.25)   # a window this close in length measures the same year
WINDOW_MIN_OVERLAP = 0.5         # ... and shares at least half of it
UNDATED_MAX_AGE_DAYS = 400       # an undated save stands for a trailing year ending about now
MIN_TU = 800
MAX_TU = 9_500_000
US_NAMES = ('', 'US', 'USA', 'UNITED STATES', 'UNITED STATES OF AMERICA')

_GENERIC_TAIL = ('fans', 'fan', 'audience', 'audiences', 'fanbase')
_RECORD_METRIC_TAILS = ('us_audience', 'audience_us')
_RECORD_METRIC_NAMES = ('us_audience', 'audience_us')
_PEOPLE_UNITS = ('people', 'individuals', 'persons', 'count', '')

# What a written sample size looks like in an ask: "a sample size of 25,000",
# "sample of 30k", "set the sample to 40,000", "a 25,000-person sample",
# "n = 20,001", "size it to 1.2 million".
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|m|mm|million)?\b"
_EXPLICIT_SIZE_RXS = (
    re.compile(r"\bsample(?:\s*-?\s*size)?\s*(?:of|should\s+be|to|at|=|:|is|around|about|near)?\s*~?\s*" + _NUM, re.I),
    re.compile(r"\b(?:use|set|lock|make|keep|fix|with)\b[^.?!\n]{0,40}?\bsample(?:\s*-?\s*size)?\b[^.?!\n\d]{0,20}" + _NUM, re.I),
    re.compile(_NUM + r"\s*-?\s*(?:person|people|panelist|individual|respondent)s?\s+sample\b", re.I),
    re.compile(r"\bn(?:\s*-?\s*size)?\s*(?:=|:)\s*" + _NUM, re.I),
    re.compile(r"\bsize\s+(?:it|this|the\s+(?:audience|universe|profile|build|sample))\s+(?:to|at)\s+~?\s*" + _NUM, re.I),
)
_MULT = {'k': 1_000, 'thousand': 1_000, 'm': 1_000_000, 'mm': 1_000_000, 'million': 1_000_000}

_cache: dict = {}
_cache_lock = threading.Lock()

# Hermetic hooks for tests: a fake ledger module, anchor store or benchmark.
LEDGER_FOR_TESTS = None
ANCHOR_STORE_FOR_TESTS = None
ANCHORS_FOR_TESTS = None
STORE_FOR_TESTS = None      # an S3-like store for the saved-size table and the anchor documents
TITLE_ANCHOR_FOR_TESTS = None   # a stand-in for title_anchors.get_title_anchor in a test


def _hermetic():
    return str(os.environ.get('REGRESSION_TEST_MODE', '')).strip() == '1'


# ---------------------------------------------------------------- names
def fold(s) -> str:
    """Lower case, letters and digits only, words kept apart."""
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().replace('&', ' and ').replace("'", '').replace('\u2019', '')
    return ' '.join(re.findall(r'[a-z0-9]+', s))


def _strip_generic(s) -> str:
    words = fold(s).split()
    if len(words) > 1 and words[0] == 'the':
        words = words[1:]
    while len(words) > 1 and words[-1] in _GENERIC_TAIL:
        words = words[:-1]
    return ' '.join(words)


def core(s) -> str:
    """The subject's identity: folded, a leading 'the' and a trailing
    'fans' / 'audience' set aside, spaces dropped ('The VICE Sports fans'
    and 'vice-sports' are one subject; 'VICE Sports Viewers' is not)."""
    return _strip_generic(s).replace(' ', '')


def is_cut_name(s) -> bool:
    return ' - ' in str(s or '')


def entity_name(s) -> str:
    return str(s or '').split(' - ', 1)[0].strip()


def is_messy(v) -> bool:
    """The house no-round rule (scripts/_sample_size_jitter): a positive
    count whose last digit is 1 to 9 and that is not a placeholder."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return False
    if v <= 0 or v % 10 == 0:
        return False
    try:
        from scripts._sample_size_jitter import FORBIDDEN_LITERALS
    except Exception:
        FORBIDDEN_LITERALS = frozenset({2001, 12345, 54321, 99999, 88888, 77777, 66666, 55555,
                                        44444, 33333, 22222, 11111, 123456, 654321})
    return v not in FORBIDDEN_LITERALS


def _messy(subject, value):
    try:
        from scripts._sample_size_jitter import ensure_messy_sample_size
        return int(ensure_messy_sample_size(subject, value))
    except Exception:
        v = max(MIN_TU + 7, int(value))
        while v % 10 == 0:
            v += 3
        return v


def projected(sample) -> int:
    return int(round(float(sample) / PANEL * US_POP))


def sample_for(people) -> int:
    return int(round(float(people) / PANEL_TO_US))


def sample_near(people):
    """The messy count in the data nearest a number of US people (the
    projection of the count lands within a few hundred people of it), or
    None when the audience is under the smallest sample a build carries."""
    try:
        s = sample_for(people)
    except (TypeError, ValueError):
        return None
    if s < MIN_TU:
        return None
    s = min(s, MAX_TU)
    for d in (0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7, 8, -8, 9, -9, 11, -11):
        if MIN_TU <= s + d <= MAX_TU and is_messy(s + d):
            return s + d
    return None


# --------------------------------------------------------------- windows
def _date(s):
    try:
        return _dt.date.fromisoformat(str(s or '').strip()[:10])
    except ValueError:
        return None


def parse_window(w):
    """(start, end) dates from a {'start','end'} dict, a (start, end) pair,
    or a 'YYYY-MM-DD TO YYYY-MM-DD' string; None when there is none."""
    if isinstance(w, dict):
        a, b = _date(w.get('start')), _date(w.get('end'))
    elif isinstance(w, (list, tuple)) and len(w) >= 2:
        a, b = _date(w[0]), _date(w[1])
    else:
        ds = re.findall(r'\d{4}-\d{2}-\d{2}', str(w or ''))
        a, b = (_date(ds[0]), _date(ds[1])) if len(ds) >= 2 else (None, None)
    if a and b and a <= b:
        return a, b
    return None


def window_of(d):
    """The build window a draft or spec states, or None."""
    if not isinstance(d, dict):
        return None
    for k in ('engine_date_range', 'date_range'):
        w = parse_window(d.get(k))
        if w:
            return w
    return None


def window_ok(build_window, saved_window, today=None) -> bool:
    """A saved size holds for a build window of about the same length that
    shares at least half of it. No stated build window is the standing
    trailing year; an undated save stands for a trailing year ending about
    now."""
    today = today or _dt.date.today()
    bw = parse_window(build_window) if build_window is not None else None
    sw = parse_window(saved_window) if saved_window else None
    if bw is None:
        return sw is None or (today - sw[1]).days <= UNDATED_MAX_AGE_DAYS
    lb = (bw[1] - bw[0]).days + 1
    if sw is None:
        return (WINDOW_LEN_RATIO[0] <= lb / 365.0 <= WINDOW_LEN_RATIO[1]
                and (today - bw[1]).days <= UNDATED_MAX_AGE_DAYS)
    ls = (sw[1] - sw[0]).days + 1
    if not (WINDOW_LEN_RATIO[0] <= lb / float(ls) <= WINDOW_LEN_RATIO[1]):
        return False
    overlap = (min(bw[1], sw[1]) - max(bw[0], sw[0])).days + 1
    return overlap >= WINDOW_MIN_OVERLAP * min(lb, ls)


# ------------------------------------------------------- the reader's size
def explicit_size_in_text(text):
    """The sample size an ask states outright, as an int, or None."""
    t = str(text or '')
    if not t:
        return None
    for rx in _EXPLICIT_SIZE_RXS:
        for m in rx.finditer(t):
            raw = m.group(1).replace(',', '')
            try:
                v = float(raw)
            except ValueError:
                continue
            unit = (m.group(2) or '').lower()
            if not unit and v.is_integer() and 1900 <= v <= 2100:
                continue    # a year, not a size
            n = int(round(v * _MULT.get(unit, 1)))
            if n >= 100:
                return n
    return None


def _history_texts(history, turns=4):
    out = []
    for t in (history or [])[-8:]:
        if isinstance(t, dict) and str(t.get('role') or '').lower() == 'user':
            out.append(str(t.get('text') or t.get('content') or ''))
    return out[-turns:]


def user_set_size(d, text='', history=None) -> bool:
    """True when the reader set the size: a sample locked from a sample
    check, a client-supplied count, or a sample size written in the ask
    (this turn or the reader's last few turns)."""
    d = d if isinstance(d, dict) else {}
    if d.get('sample_size_locked') or d.get('sample_size_user_set'):
        return True
    try:
        if int(float(d.get('user_supplied_anchor') or 0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if str(d.get('anchor_source') or '').strip().lower().startswith('client-supplied'):
        return True
    for t in [text, d.get('user_prompt'), d.get('prompt')] + _history_texts(history):
        if explicit_size_in_text(t):
            return True
    return False


def mark_user_set(d, text='', history=None) -> bool:
    """Stamp ``sample_size_user_set`` on a draft whose ask set the size, so
    the spec step and the worker keep the reader's figure. Returns the
    verdict. Never raises."""
    try:
        if isinstance(d, dict) and user_set_size(d, text, history):
            d['sample_size_user_set'] = True
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- sources
def _estimates():
    try:
        try:
            import scripts._subject_estimates as m
        except Exception:
            import _subject_estimates as m  # type: ignore
        return (dict(getattr(m, 'ESTIMATES', {}) or {}), dict(getattr(m, 'ALIASES', {}) or {}),
                dict(getattr(m, 'WINDOWS', {}) or {}))
    except Exception:
        return {}, {}, {}


def from_estimates(subject):
    want = core(subject)
    if not want:
        return None
    est, aliases, windows = _estimates()
    for name, value in est.items():
        names = [name] + list(aliases.get(name) or ())
        if want not in {core(n) for n in names}:
            continue
        if not is_messy(value) or not (MIN_TU <= int(value) <= MAX_TU):
            return None     # a placeholder from the older table never sizes a build
        v = int(value)
        return {'tu': v, 'us': projected(v), 'source': 'estimates', 'ref': name,
                'window': windows.get(name), 'subject': name}
    return None


def _ledger():
    if LEDGER_FOR_TESTS is not None:
        return LEDGER_FOR_TESTS
    try:
        import insights_ledger as il
        return il
    except Exception:
        return None


def _record_metric(entry, skey):
    for m in (entry.get('metrics') or []):
        if not isinstance(m, dict):
            continue
        name = str(m.get('name') or '').strip().lower()
        if name not in _RECORD_METRIC_NAMES and not (
                skey and name.startswith(skey + '_') and name.endswith(_RECORD_METRIC_TAILS)):
            continue
        if str(m.get('unit') or 'people').strip().lower() not in _PEOPLE_UNITS:
            continue
        try:
            sp = int(m.get('sample_people') or 0)
        except (TypeError, ValueError):
            sp = 0
        try:
            us = int(round(float(m.get('value') or 0)))
        except (TypeError, ValueError):
            us = 0
        tu = sp if sp > 0 else (sample_for(us) if us > 0 else 0)
        if MIN_TU <= tu <= MAX_TU:
            return tu, (us or projected(tu))
    return None


def _bucket_names(key, bucket):
    return [bucket.get('subject') or str(key).replace('_', ' ')] + list(bucket.get('aliases') or [])


def _ledger_buckets(il, doc, subject):
    """The ledger buckets that are this subject: the bucket the ledger files
    it under (its own fold), then any bucket that lists it as an alias;
    each one's names must carry the subject's identity."""
    subjects = (doc or {}).get('subjects') or {}
    want = core(subject)
    keys = []
    for s in dict.fromkeys([str(subject), _strip_generic(subject)]):
        try:
            k = il.bucket_key_for(doc, s)
        except Exception:
            k = None
        if k in subjects and k not in keys:
            keys.append(k)
    for k, b in subjects.items():
        if k not in keys and isinstance(b, dict) and b.get('aliases'):
            keys.append(k)
    out = []
    for k in keys:
        b = subjects.get(k)
        if isinstance(b, dict) and want in {core(n) for n in _bucket_names(k, b)}:
            out.append((k, b))
    return out


def from_ledger(subject):
    il = _ledger()
    if il is None or not core(subject):
        return None
    try:
        doc = il._load_index()
    except Exception:
        return None
    best = None
    for key, bucket in _ledger_buckets(il, doc, subject):
        skey = re.sub(r'[^a-z0-9]+', '_', str(bucket.get('subject') or key).lower()).strip('_')
        for e in (bucket.get('entries') or []):
            if not isinstance(e, dict) or e.get('retired'):
                continue
            try:
                if not il.is_record_entry(e) or il.canon_family(e.get('family')) != 'audience':
                    continue
            except Exception:
                continue
            got = _record_metric(e, skey) or _record_metric(e, str(key).lower())
            if not got:
                continue
            ts = str(e.get('ts') or '')
            if best is None or ts > best[0]:
                best = (ts, got, e, bucket)
    if not best:
        return None
    _ts, (tu, us), e, bucket = best
    ws, we = str(e.get('ws') or ''), str(e.get('we') or '')
    return {'tu': int(tu), 'us': int(us), 'source': 'ledger_record', 'ref': str(e.get('k') or ''),
            'window': (ws, we) if ws and we else None, 'subject': str(bucket.get('subject') or subject)}


def anchor_ask_keys(subject):
    """The anchor store's ask keys a saved size is read back from: the
    subject's any-window audience documents for the read and the insight
    (prometheus.knowledge.ask_key's shape)."""
    try:
        il = _ledger()
        subj = il.fold_subject(subject).replace(' ', '_')[:80] or il.subject_key(subject)
    except Exception:
        subj = re.sub(r'[^a-z0-9]+', '_', fold(subject)).strip('_')[:80]
    return [f"{subj}|{product}|audience|any|any|" for product in ('read', 'insight')]


def _anchor_keys(subject):
    for k in anchor_ask_keys(subject):
        yield f"{ANCHOR_PREFIX}{hashlib.sha1(k.encode()).hexdigest()[:20]}.json"


def _store_json(key):
    try:
        obj = STORE_FOR_TESTS.get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _load_anchor_doc(key):
    if STORE_FOR_TESTS is not None:
        return _store_json(key)
    if ANCHOR_STORE_FOR_TESTS is not None:
        return ANCHOR_STORE_FOR_TESTS.get(key)
    if _hermetic():
        return None
    try:
        import boto3
        obj = boto3.client('s3').get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _load_table():
    if STORE_FOR_TESTS is not None:
        return _store_json(TABLE_KEY)
    if _hermetic():
        return None
    try:
        import boto3
        obj = boto3.client('s3').get_object(Bucket=BUCKET, Key=TABLE_KEY)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def from_saved_table(subject):
    want = core(subject)
    if not want:
        return None
    doc = _load_table()
    for key, e in ((doc or {}).get('subjects') or {}).items():
        if not isinstance(e, dict):
            continue
        names = [e.get('name') or key] + list(e.get('aliases') or [])
        if want != key and want not in {core(n) for n in names}:
            continue
        try:
            tu = int(e.get('sample') or 0)
        except (TypeError, ValueError):
            return None
        if not (MIN_TU <= tu <= MAX_TU) or not is_messy(tu):
            return None
        w = e.get('window')
        return {'tu': tu, 'us': projected(tu), 'source': 'saved_table', 'ref': str(key),
                'window': (str(w[0]), str(w[1])) if isinstance(w, (list, tuple)) and len(w) >= 2 else None,
                'subject': str(e.get('name') or subject)}
    return None


def from_anchor_store(subject):
    head, want = fold(subject), core(subject)
    if not head:
        return None
    best = None
    for key in _anchor_keys(subject):
        doc = _load_anchor_doc(key)
        if not isinstance(doc, dict):
            continue
        asked = (doc.get('ask') or {}).get('subject') if isinstance(doc.get('ask'), dict) else None
        if asked and core(asked) != want:
            continue    # the document is filed for another spelling of a wider subject
        for a in (doc.get('anchors') or []):
            if not isinstance(a, dict):
                continue
            if str(a.get('source_type') or '').lower() != 'record' or str(a.get('klass') or '').lower() != 'audience':
                continue
            if str(a.get('unit') or '').lower() not in ('people', 'individuals'):
                continue
            name = fold(a.get('name'))
            if not (name == head or name.startswith(head + ' ')):
                continue
            try:
                us = int(round(float(a.get('value') or 0)))
            except (TypeError, ValueError):
                continue
            if us > 0 and (best is None or us > best[0]):
                w = a.get('window') or {}
                best = (us, key, (w.get('start'), w.get('end')) if isinstance(w, dict) and w.get('start') else None)
    if not best:
        return None
    us, key, w = best
    tu = sample_for(us)
    if not (MIN_TU <= tu <= MAX_TU) or not is_messy(tu):
        return None
    return {'tu': tu, 'us': us, 'source': 'anchor_store', 'ref': key, 'window': w, 'subject': str(subject)}


def from_title_anchor(subject):
    """The title universe another product fixed first (migration/
    title_anchors: Subscriber IQ, a journey, a Profile IQ build): the US
    viewers of the title stand as the title's audience size, so a Profile
    IQ build of the title starts where the other products already are
    (one set of figures across products, Jenna 2026-11-23). Only a title
    the registry holds; a cut name never."""
    want = core(subject)
    if not want or is_cut_name(subject):
        return None
    if TITLE_ANCHOR_FOR_TESTS is not None:
        get_title_anchor = TITLE_ANCHOR_FOR_TESTS
    elif _hermetic():
        return None                       # a test process never reads the live registry
    else:
        try:
            from migration.title_anchors import get_title_anchor
        except Exception:
            return None
    try:
        ta = get_title_anchor(str(subject))
    except Exception:
        return None
    if not isinstance(ta, dict):
        return None
    try:
        us = int(round(float(ta.get('us_viewers') or 0)))
    except (TypeError, ValueError):
        return None
    if us <= 0:
        return None
    tu = int(ta.get('sample_size') or 0) or sample_for(us)
    if not (MIN_TU <= tu <= MAX_TU) or not is_messy(tu):
        tu = sample_for(us)
        if not (MIN_TU <= tu <= MAX_TU) or not is_messy(tu):
            return None
    w = ta.get('window') if isinstance(ta.get('window'), dict) else {}
    return {'tu': tu, 'us': us, 'source': 'title_anchor', 'ref': str(ta.get('source_product') or 'title_anchor'),
            'window': (str(w.get('start')), str(w.get('end'))) if w.get('start') and w.get('end') else None,
            'subject': str(ta.get('title') or subject)}


def lookup(subject):
    """The saved size for a subject (dict: tu, us, source, ref, window,
    subject) or None. Cached for a few minutes per subject."""
    want = core(subject)
    if not want:
        return None
    now = time.time()
    with _cache_lock:
        hit = _cache.get(want)
        if hit and now - hit[0] < CACHE_TTL_S:
            return dict(hit[1]) if hit[1] else None
    found = None
    for fn in (from_ledger, from_estimates, from_saved_table, from_title_anchor, from_anchor_store):
        try:
            found = fn(subject)
        except Exception:
            found = None
        if found:
            break
    with _cache_lock:
        _cache[want] = (now, dict(found) if found else None)
    return dict(found) if found else None


def clear_cache():
    with _cache_lock:
        _cache.clear()


# -------------------------------------------------------------- the scope
def _narrowed(d, name):
    """A build that is not the whole subject's US audience: a title
    narrowed to its consumers, a season or a service, a held title anchor,
    another country."""
    d = d if isinstance(d, dict) else {}
    if str(d.get('country') or 'US').strip().upper() not in US_NAMES:
        return 'country'
    if str(d.get('ip_scope') or '').strip().lower() == 'consumers':
        return 'consumers'
    if d.get('_title_anchor'):
        return 'title_anchor'
    vs = d.get('viewer_scope')
    if isinstance(vs, dict) and str(vs.get('mode') or '').strip().lower() in ('latest', 'specific'):
        return 'viewer_scope'
    try:
        from migration import service_scope_sizing as _sss
        if _sss.is_scoped(d, name):
            return 'service_scope'
    except Exception:
        pass
    return ''


# -------------------------------------------------------------- benchmark
def _benchmark_conflict(name, saved_us, anchors, build_window, log):
    """True when the subject's own Gen Pop row or prior Total Universe puts
    the saved size outside the benchmark band (the house's read governs)."""
    try:
        from migration import profile_build_size_guards as G
    except Exception:
        return False
    bw = {'start': build_window[0].isoformat(), 'end': build_window[1].isoformat()} if build_window else None
    try:
        v = G.benchmark_verdict(int(saved_us), anchors or {}, build_window=bw)
    except Exception:
        return False
    if v and v.get('anchor'):
        try:
            log(f"[saved-size] {name!r}: the saved {int(saved_us):,} US people sit at {v['ratio']:.2f}x the "
                f"subject's {('Gen Pop row' if v['anchor'] == 'genpop' else 'prior Total Universe')} "
                f"({v['anchor_people']:,}); outside the benchmark band, the benchmark governs this build")
        except Exception:
            pass
        return True
    return False


def _anchors_for(name, d, audience_type, s3=None):
    if ANCHORS_FOR_TESTS is not None:
        return ANCHORS_FOR_TESTS
    if _hermetic():
        return {}
    try:
        from migration import profile_build_size_guards as G
    except Exception:
        return {}
    d = d if isinstance(d, dict) else {}
    w = window_of(d)
    probe = {'audience_type': audience_type or 'general', 'country': d.get('country') or 'US',
             'date_range': f"{w[0].isoformat()} TO {w[1].isoformat()}" if w else '',
             'file_stem': re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_'),
             'content_show': d.get('content_show'), 'viewer_scope': d.get('viewer_scope'),
             'seed_source': d.get('seed_source')}
    try:
        sa = G.size_anchors(name, probe, s3=s3)
    except Exception:
        return {}
    gp = sa.get('genpop') if (sa.get('genpop') or {}).get('usable') else None
    return {'genpop': gp, 'prior': sa.get('prior')}


# ----------------------------------------------------------------- settle
def _salted(subject, salt):
    h = int(hashlib.sha256(f"{subject}|{salt}".encode()).hexdigest()[:8], 16)
    return (h % 10000) / 10000.0


def settle(subject, tu, avid, *, decision='new_build', d=None, text='', history=None,
           audience_type='general', follower_ceiling=None, anchors=None, s3=None, log=print,
           parent_s3_key=None, refresh_locked=False):
    """The size a fresh build starts from. Returns a trail dict:

    * ``{'applied': True, 'tu', 'avid', 'universe_anchor', 'anchor_source',
      'engaged_share', 'marker'}`` when the saved size sizes this build;
    * ``{'applied': False, 'user_set': True}`` when the reader set the size
      of a subject that has a saved one;
    * ``{'applied': False, 'reason': ...}`` when a saved size exists but
      does not hold here; None when the subject has none.

    Never raises."""
    try:
        dec = str(decision or '').strip().lower()
        if dec == 'time_shifted_refresh':
            if refresh_locked or parent_s3_key:
                return None     # the refresh clamp sizes off the parent
            dec = 'new_build'   # a refresh with no parent at all is a fresh build
        if dec not in FRESH:
            return None
        name = entity_name(subject) if dec == 'cut_needs_parent' else str(subject or '').strip()
        if not name or is_cut_name(name):
            return None
        saved = lookup(name)
        if not saved:
            return None
        if user_set_size(d, text, history):
            return {'applied': False, 'user_set': True}
        why = _narrowed(d, name)
        if why:
            return {'applied': False, 'reason': why}
        bw = window_of(d)
        if not window_ok(bw, saved.get('window')):
            log(f"[saved-size] {name!r}: the saved size ({saved['source']}) is for another window; not used")
            return {'applied': False, 'reason': 'window'}
        if anchors is None:
            anchors = _anchors_for(name, d, audience_type, s3=s3)
        if _benchmark_conflict(name, saved['us'], anchors, bw, log):
            return {'applied': False, 'reason': 'benchmark'}
        new_tu = int(saved['tu'])
        capped = False
        try:
            from migration.follower_ceiling import is_capped_audience, cap_subject_raw
            if follower_ceiling and is_capped_audience(audience_type):
                c, meta = cap_subject_raw(new_tu, follower_ceiling)
                if meta.get('applied') and int(c) < new_tu:
                    new_tu = min(_messy(f"{name}|{audience_type}|tu", int(c)),
                                 int(meta.get('max_allowed_raw') or c))
                    capped = True
        except Exception:
            pass
        try:
            old_tu, old_av = int(tu or 0), int(avid or 0)
        except (TypeError, ValueError):
            old_tu, old_av = 0, 0
        if old_tu == new_tu and 0 < old_av < new_tu:
            new_av = old_av
        else:
            if 0 < old_av < old_tu:
                new_av = int(round(old_av * new_tu / float(old_tu)))
            else:
                new_av = int(round(new_tu * (0.19 + 0.14 * _salted(name, 'saved-size-avid'))))
            new_av = _messy(f"{name}|avid|saved-size", max(new_av, 500))
        if new_av >= new_tu:
            new_av = _messy(f"{name}|avid|saved-size|subset", max(int(new_tu * 0.31), 500))
            if new_av >= new_tu:
                new_av = max(1, new_tu - 13)
        people = projected(new_tu)
        label = 'decision of record' if saved['source'] == 'ledger_record' else 'saved audience size'
        marker = {'source': saved['source'], 'ref': saved.get('ref'), 'subject': saved.get('subject') or name,
                  'sample': int(saved['tu']), 'people': int(saved['us']), 'window': saved.get('window'),
                  'tu': int(new_tu), 'avid': int(new_av), 'capped': capped,
                  'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        if old_tu != new_tu:
            log(f"[saved-size] {name!r}: starts from the {label} {new_tu:,} in the data "
                f"({people:,} US people; {saved['source']} {saved.get('ref')}); drafted {old_tu:,}"
                + (" (held under the public-metric ceiling)" if capped else ""))
        return {'applied': True, 'tu': int(new_tu), 'avid': int(new_av), 'universe_anchor': people,
                'anchor_source': f"the saved Total Universe for {saved.get('subject') or name}",
                'engaged_share': 1.0, 'marker': marker, 'drafted_tu': old_tu, 'drafted_avid': old_av}
    except Exception as e:
        try:
            log(f"[saved-size] {subject!r}: skipped ({type(e).__name__}: {e})")
        except Exception:
            pass
        return None


def _write(d, trail):
    d['subject_raw_tu'] = int(trail['tu'])
    d['subject_raw_avid'] = int(trail['avid'])
    d['universe_anchor'] = int(trail['universe_anchor'])
    d['anchor_source'] = trail['anchor_source']
    d['engaged_share'] = 1.0
    d['sample_size_saved'] = dict(trail['marker'])


def holds(d) -> bool:
    """True while a draft or spec still carries the saved size it was given
    (a later step that resized it ends the hold)."""
    m = (d or {}).get('sample_size_saved') if isinstance(d, dict) else None
    try:
        return isinstance(m, dict) and int(m.get('tu') or 0) > 0 \
            and int(d.get('subject_raw_tu') or 0) == int(m['tu'])
    except (TypeError, ValueError):
        return False


def apply_to_draft(draft, text='', *, history=None, decision=None, log=print, anchors=None, s3=None):
    """The interpret steps' call (the dashboard and Prometheus interpret
    before the brief quotes a size, the batch cards, the partner API):
    writes the saved size into the draft (sample, avid, anchor chain,
    ``sample_size_saved``) or stamps ``sample_size_user_set``. A refresh is
    left to the worker. Returns the trail or None. Never raises."""
    try:
        if not isinstance(draft, dict):
            return None
        dec = str(decision if decision is not None else draft.get('decision') or '').strip().lower()
        if dec == 'time_shifted_refresh':
            return None
        trail = settle(draft.get('subject') or draft.get('name') or '', draft.get('subject_raw_tu'),
                       draft.get('subject_raw_avid'), decision=dec, d=draft, text=text, history=history,
                       audience_type=str(draft.get('audience_type') or 'general'),
                       follower_ceiling=draft.get('follower_ceiling'), anchors=anchors, s3=s3, log=log)
        if trail and trail.get('applied'):
            _write(draft, trail)
        else:
            draft.pop('sample_size_saved', None)
            if trail and trail.get('user_set'):
                draft['sample_size_user_set'] = True
        return trail
    except Exception:
        return None


def settle_for_spec_step(draft, subject, tu, avid, *, audience_type='general', follower_ceiling=None,
                         anchors=None, s3=None, log=print):
    """``_spec_from_draft``'s call, after the public-metric cap and before
    the anchor guard. Returns the trail; the anchor chain is written onto
    the draft so the guard reads it. A refresh is left to the worker.
    Never raises."""
    try:
        if not isinstance(draft, dict):
            return None
        dec = str(draft.get('decision') or '').strip().lower()
        if dec == 'time_shifted_refresh':
            return None
        if holds(draft):   # the brief already carries it: start from its own figures, not the salted copies
            tu, avid = int(draft['subject_raw_tu']), int(draft.get('subject_raw_avid') or 0)
        trail = settle(subject, tu, avid, decision=dec, d=draft, text=str(draft.get('user_prompt') or ''),
                       audience_type=audience_type, follower_ceiling=follower_ceiling,
                       anchors=(anchors if dec != 'cut_needs_parent' else None), s3=s3, log=log)
        if trail and trail.get('applied'):
            draft['universe_anchor'] = int(trail['universe_anchor'])
            draft['anchor_source'] = trail['anchor_source']
            draft['engaged_share'] = 1.0
        return trail
    except Exception:
        return None


def spec_step(draft, subject, tu, avid, audience_type='general', follower_ceiling=None, bench=None, prior=None,
              s3=None, log=print):
    """``_spec_from_draft``'s one call (the legacy chat module carries a
    line ceiling): (tu, avid, trail), at the saved size when it applies,
    else the inputs as they came. ``bench`` / ``prior`` are the subject's
    usable Gen Pop row and prior Total Universe the spec step already
    looked up. Never raises."""
    trail = settle_for_spec_step(draft, subject, tu, avid, audience_type=audience_type,
                                 follower_ceiling=follower_ceiling, anchors={'genpop': bench, 'prior': prior},
                                 s3=s3, log=log)
    if trail and trail.get('applied'):
        return int(trail['tu']), int(trail['avid']), trail
    return tu, avid, trail


def spec_markers(spec, trail):
    """Stamp the spec step's outcome on the spec: ``sample_size_saved`` (the
    figure the spec settled on, after the catalog hold: the worker holds it
    through its own messy pass),
    ``sample_size_user_set``, or ``_saved_size_checked`` (a saved size that
    does not hold here, so the worker does not look again)."""
    try:
        if not isinstance(spec, dict) or not trail:
            return spec
        if trail.get('applied'):
            spec['sample_size_saved'] = dict(trail['marker'], tu=int(spec.get('subject_raw_tu') or trail['tu']),
                                             avid=int(spec.get('subject_raw_avid') or trail['avid']))
        elif trail.get('user_set'):
            spec['sample_size_user_set'] = True
        else:
            spec['_saved_size_checked'] = str(trail.get('reason') or 'checked')
    except Exception:
        pass
    return spec


def apply_to_spec(spec, subject, *, decision='new_build', text='', parent_s3_key=None, log=print, s3=None):
    """The worker's call (migration/synth_queue_worker._run_new_build),
    right after the messy pass and before the quote floor and the anchor
    guard. Puts back a saved size the spec step applied (the messy pass
    salts every input) and applies one the spec step never saw (a direct
    queue post, a refresh with no parent). Returns the trail or None.
    Never raises."""
    try:
        if not isinstance(spec, dict):
            return None
        if spec.get('_refresh_sample_locked') or spec.get('sample_size_user_set') \
                or spec.get('_saved_size_checked'):
            return None
        held = spec.get('sample_size_saved')
        if isinstance(held, dict) and int(held.get('tu') or 0) > 0:
            tu = int(held['tu'])
            av = int(held.get('avid') or 0)
            if tu != int(spec.get('subject_raw_tu') or 0):
                log(f"[saved-size] {subject!r}: the saved size holds: {spec.get('subject_raw_tu')} -> {tu:,}")
            spec['subject_raw_tu'] = tu
            if 0 < av < tu:
                spec['subject_raw_avid'] = av
            spec['universe_anchor'] = projected(tu)
            spec['engaged_share'] = 1.0
            if not str(spec.get('anchor_source') or '').strip():
                spec['anchor_source'] = f"the saved Total Universe for {held.get('subject') or subject}"
            return {'applied': True, 'tu': tu, 'avid': int(spec.get('subject_raw_avid') or 0), 'reasserted': True}
        trail = settle(subject, spec.get('subject_raw_tu'), spec.get('subject_raw_avid'), decision=decision,
                       d=spec, text=text, audience_type=str(spec.get('audience_type') or 'general'),
                       follower_ceiling=spec.get('follower_ceiling'), s3=s3, log=log,
                       parent_s3_key=parent_s3_key, refresh_locked=bool(spec.get('_refresh_sample_locked')))
        if trail and trail.get('applied'):
            _write(spec, trail)
        elif trail and trail.get('user_set'):
            spec['sample_size_user_set'] = True
        return trail
    except Exception:
        return None


def for_check(subject, window, tu, avid, text='', log=print):
    """The sample check's call (the free 'how big would the sample be'
    read): (tu, avid) at the saved size when the subject has one for this
    window, else None. A check quotes what a build would start from."""
    try:
        d = {'decision': 'new_build'}
        if window:
            d['date_range'] = window
        trail = settle(subject, tu, avid, decision='new_build', d=d, text=text, log=log)
        if trail and trail.get('applied'):
            return int(trail['tu']), int(trail['avid'])
    except Exception:
        pass
    return None


__all__ = ['fold', 'core', 'is_messy', 'parse_window', 'window_ok', 'explicit_size_in_text', 'user_set_size',
           'mark_user_set', 'from_estimates', 'from_ledger', 'from_saved_table', 'from_anchor_store', 'lookup',
           'clear_cache', 'sample_near', 'anchor_ask_keys',
           'settle', 'holds', 'apply_to_draft', 'settle_for_spec_step', 'spec_step', 'spec_markers', 'apply_to_spec',
           'for_check']
