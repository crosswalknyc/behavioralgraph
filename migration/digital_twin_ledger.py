"""The outcome ledger for Digital Twins IQ (2026-11-17).

Every figure the twins put in front of a client becomes a record the
house can score: the question or scenario, the audience, the prediction
(point and range, or a distribution), the method version, a timestamp
and a hash of the record so the prediction is provably prior to the
outcome. When the outcome lands (the panel's own count, a certified
result, a published figure, a client's actuals) it is written next to
the prediction and scored: absolute and relative
error, total variation distance for a distribution, whether the outcome
fell inside the range (coverage), Brier for a yes or no call. The
summaries feed the firmness layer (``digital_twin_confidence``) and the
public track record.

Kinds of record
  forecast     a Pilot Testing forecast figure (week-one audience, completion, ...)
  room_count   a Focus Group IQ split (the published shares over the options)
  scorecard    a scorecard measure (total positive, definitely recommend, ...)
  call         a called outcome (an election, a premiere week, an opening weekend)

Storage
  system/digital_twins/ledger/records/<id>.json   one object per record
  system/digital_twins/ledger/index.json          {records: [small rows], updated_at}
The index is rebuilt from the row on every write (read, modify, write;
contention is low and a lost index row is rebuilt by ``rebuild_index``).
Any object with get_object / put_object / list_objects_v2 works (the
tests' fake S3); without S3 the ledger is in memory for the process.

A record whose outcome was already known when the prediction was made
(a room asked about a race after it was called: the anchor was the
result) is marked ``post_hoc`` and never counts toward the track record.
The first rows are the calls the house made before their outcomes on the
2026 midterms and the NCIS: New York premiere (``backfill_known``),
marked ``backfilled`` with the dates they were made public.

What a call is (Jenna 2026-11-20: "Only questions with a real result to
check"): every record carries a ``status``. ``call`` is a question with a
real result to check, and it carries what settles it (``call``: the date
its result is knowable, the check, what is measured, how it is scored;
``migration/digital_twin_calls``). ``not_a_call`` and ``folded`` rows
stay in storage for audit and never reach a reader, a summary or the
firmness layer; ``revision`` is a later prediction on a call already
made. A call is frozen when it is made: a different prediction never
replaces it, it becomes a new dated revision row and the first one
keeps its hash and date. An outcome, once written, is never revised.

Who reads what (Jenna 2026-11-20: "Only calls we deliberately log
before the result; practice rooms stay internal"): a Crosswalk row is
public only when it was logged on purpose before its result
(``logged``) or is one of the backfilled calls; a client's row is its
company's alone until an admin releases it; staff read everything.

Nothing here raises into a room or a build: every public function
catches and logs; a ledger that cannot be written leaves the product
untouched.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Optional

S3_BUCKET = 'dashboard-inputs'
LEDGER_PREFIX = 'system/digital_twins/ledger/'
RECORDS_PREFIX = LEDGER_PREFIX + 'records/'
INDEX_KEY = LEDGER_PREFIX + 'index.json'
VERSION = 2
KINDS = ('forecast', 'room_count', 'scorecard', 'call')
STATUSES = ('call', 'not_a_call', 'folded', 'revision')
# the type of result a reader sees beside a settled call; never a source name
OUTCOME_LABELS = {'certified result': 'certified result', 'certified': 'certified result', 'published': 'published', 'on file': 'published',
                  'panel': 'our own count', 'counted': 'our own count', 'our own count': 'our own count',
                  'client': 'client results', 'client results': 'client results'}
CHECK_LABELS = {'counted': 'our own count', 'published': 'a published result', 'certified': 'a certified result', 'client': 'your own results'}
_MEM: dict = {'records': {}, 'index': None}     # the in-memory ledger when no S3 is given


def outcome_label(source_class) -> str:
    c = str(source_class or '').strip().lower()
    if not c:
        return ''
    return OUTCOME_LABELS.get(c, 'published')


def _status(row) -> str:
    s = str((row or {}).get('status') or 'call')
    return s if s in STATUSES else 'call'


def is_call(row) -> bool:
    return _status(row) == 'call'


def _public_allowed(row) -> bool:
    """A house row reaches the public record only when it was logged on
    purpose before its result, or is a backfilled call; a client row
    only when an admin released it (``public``)."""
    if (row or {}).get('post_hoc') or not is_call(row):
        return False
    if is_house(row):
        return bool(row.get('logged') or row.get('backfilled'))
    return True


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


def record_hash(rec: dict) -> str:
    """The hash that proves the prediction came first: the kind, the
    subject, the question, the measure, the prediction and the time."""
    body = {k: rec.get(k) for k in ('kind', 'subject', 'twin_key', 'session_id', 'question_id', 'question', 'measure', 'prediction', 'created_at', 'method_version')}
    return hashlib.sha256(_canon(body).encode('utf-8')).hexdigest()


def record_id(kind: str, subject: str, session_id: str = '', question_id: str = '', measure: str = '') -> str:
    """Deterministic per (kind, subject, session, question, measure) so a
    re-read of the same test or question updates one row instead of
    adding another."""
    raw = '|'.join(str(x or '') for x in (kind, subject, session_id, question_id, measure))
    return 'lg_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


# ------------------------------------------------------------------- storage
def _get(s3, key: str):
    if s3 is None:
        if key == INDEX_KEY:
            return _MEM['index']
        return _MEM['records'].get(key)
    try:
        raw = s3.get_object(Bucket=S3_BUCKET, Key=key)['Body'].read()
        return json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return None


def _put(s3, key: str, doc: dict) -> bool:
    if s3 is None:
        if key == INDEX_KEY:
            _MEM['index'] = doc
        else:
            _MEM['records'][key] = doc
        return True
    try:
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, ensure_ascii=False, indent=1).encode('utf-8'), ContentType='application/json')
        return True
    except Exception as e:
        print(f"[twin-ledger] write failed {key}: {str(e)[:120]}")
        return False


def load_record(s3, rid: str) -> Optional[dict]:
    doc = _get(s3, RECORDS_PREFIX + rid + '.json')
    return doc if isinstance(doc, dict) else None


def load_index(s3) -> dict:
    doc = _get(s3, INDEX_KEY)
    if isinstance(doc, dict) and isinstance(doc.get('records'), list):
        return doc
    return {'schema': VERSION, 'records': [], 'updated_at': ''}


def _index_row(rec: dict) -> dict:
    sc = rec.get('score') or {}
    return {'id': rec['id'], 'kind': rec.get('kind'), 'subject': rec.get('subject'), 'twin_key': rec.get('twin_key'), 'session_id': rec.get('session_id'),
            'question_kind': rec.get('question_kind'), 'audience_class': rec.get('audience_class'), 'measure': rec.get('measure'), 'unit': (rec.get('prediction') or {}).get('unit'),
            'created_at': rec.get('created_at'), 'observed_at': (rec.get('outcome') or {}).get('observed_at'), 'has_outcome': bool(rec.get('outcome')),
            'post_hoc': bool(rec.get('post_hoc')), 'backfilled': bool(rec.get('backfilled')), 'public': bool(rec.get('public')),
            'company': str(rec.get('company') or ''), 'house': is_house(rec),
            'abs_error': sc.get('abs_error'), 'rel_error': sc.get('rel_error'), 'tvd': sc.get('tvd'), 'covered': sc.get('covered'), 'brier': sc.get('brier'),
            'hit': sc.get('hit'), 'anchor_status': (rec.get('basis') or {}).get('anchor_status'), 'firmness': ((rec.get('basis') or {}).get('firmness') or {}).get('word'),
            'status': _status(rec), 'logged': bool(rec.get('logged')), 'due_at': (rec.get('call') or {}).get('due_at'), 'resolver': (rec.get('call') or {}).get('resolver'),
            'created_by': str(rec.get('created_by') or ''), 'revises': str(rec.get('revises') or '')}


def _save(s3, rec: dict) -> dict:
    _put(s3, RECORDS_PREFIX + rec['id'] + '.json', rec)
    idx = load_index(s3)
    rows = [r for r in idx.get('records') or [] if r.get('id') != rec['id']]
    rows.append(_index_row(rec))
    rows.sort(key=lambda r: str(r.get('created_at') or ''))
    _put(s3, INDEX_KEY, {'schema': VERSION, 'records': rows, 'updated_at': _now()})
    return rec


def rebuild_index(s3) -> dict:
    """The index from the record objects (after a lost write)."""
    rows = []
    if s3 is None:
        recs = list(_MEM['records'].values())
    else:
        recs = []
        try:
            token = None
            while True:
                kw = {'Bucket': S3_BUCKET, 'Prefix': RECORDS_PREFIX, 'MaxKeys': 1000}
                if token:
                    kw['ContinuationToken'] = token
                resp = s3.list_objects_v2(**kw)
                for o in resp.get('Contents') or []:
                    doc = _get(s3, o['Key'])
                    if isinstance(doc, dict) and doc.get('id'):
                        recs.append(doc)
                token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
                if not token:
                    break
        except Exception as e:
            print(f"[twin-ledger] rebuild list failed: {str(e)[:120]}")
    for rec in recs:
        rows.append(_index_row(rec))
    rows.sort(key=lambda r: str(r.get('created_at') or ''))
    doc = {'schema': VERSION, 'records': rows, 'updated_at': _now()}
    _put(s3, INDEX_KEY, doc)
    return doc


def reset_memory() -> None:
    """Tests: the in-memory ledger emptied."""
    _MEM['records'] = {}
    _MEM['index'] = None


# ------------------------------------------------------------------- scoring
def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _dist(rows) -> dict:
    out = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get('label') is not None:
            v = _num(r.get('pct'))
            if v is not None:
                out[str(r['label']).strip().lower()] = v
    tot = sum(out.values())
    if tot > 0 and abs(tot - 100.0) > 1e-6 and tot <= 1.5:
        out = {k: v * 100.0 for k, v in out.items()}
    return out


def tvd(pred_rows, out_rows) -> Optional[float]:
    """Total variation distance in points between two distributions over
    the same labels (case-insensitive); None when they share nothing."""
    p, o = _dist(pred_rows), _dist(out_rows)
    labels = set(p) | set(o)
    if not labels or not (set(p) & set(o)):
        return None
    return round(0.5 * sum(abs(p.get(k, 0.0) - o.get(k, 0.0)) for k in labels), 2)


def score(prediction: dict, outcome: dict) -> dict:
    """The score of one record: abs and relative error on the point,
    coverage of the range, TVD on a distribution, Brier and hit on a
    probability or a call."""
    sc = {}
    pt, val = _num((prediction or {}).get('point')), _num((outcome or {}).get('value'))
    if pt is not None and val is not None:
        sc['abs_error'] = round(abs(pt - val), 4)
        sc['rel_error'] = round(abs(pt - val) / abs(val), 4) if val else None
        lo, hi = _num((prediction or {}).get('low')), _num((prediction or {}).get('high'))
        if lo is not None and hi is not None:
            sc['covered'] = bool(min(lo, hi) <= val <= max(lo, hi))
        sc['direction'] = 'high' if pt > val else ('low' if pt < val else 'on')
    if (prediction or {}).get('distribution') and (outcome or {}).get('distribution'):
        t = tvd(prediction['distribution'], outcome['distribution'])
        if t is not None:
            sc['tvd'] = t
            p, o = _dist(prediction['distribution']), _dist(outcome['distribution'])
            if p and o:
                sc['lead_right'] = bool(max(p, key=p.get) == max(o, key=o.get))
    prob = _num((prediction or {}).get('probability'))
    happened = (outcome or {}).get('happened')
    if prob is not None and happened is not None:
        y = 1.0 if happened else 0.0
        sc['brier'] = round((prob - y) ** 2, 4)
        sc['hit'] = bool((prob >= 0.5) == bool(happened))
    if (prediction or {}).get('call') is not None and (outcome or {}).get('winner') is not None:
        sc['hit'] = bool(str(prediction['call']).strip().lower() == str(outcome['winner']).strip().lower())
    return sc


# ------------------------------------------------------------------- writes
def is_house(rec: dict) -> bool:
    """A row the house made on its own twins (Crosswalk's company, or a
    backfilled call the house made before its outcome). Rows written
    before the company rode on the record read as house only when
    backfilled."""
    if 'house' in (rec or {}):
        return bool(rec.get('house'))
    comp = str((rec or {}).get('company') or '')
    if comp:
        try:
            from migration import digital_twin_company as CO
            return CO.same_company(comp, CO.STAFF_COMPANY)
        except Exception:
            return comp.strip().lower() == 'crosswalk'
    return bool((rec or {}).get('backfilled'))


def visible(row: dict, *, company: Optional[str] = None, staff: bool = False) -> bool:
    """Who may read a row: staff read everything; a seat reads the
    public record and its own company's calls (2026-11-17). A Crosswalk
    practice room never shows to a client (Jenna 2026-11-20): a house
    row is on the public record only when it was logged on purpose
    before its result, or is a backfilled call. A row that is not a call
    (or a revision) reaches no reader but staff."""
    if staff:
        return True
    if not is_call(row):
        return False
    if row.get('public') and _public_allowed(row):
        return True
    if is_house(row):
        return False
    if company and row.get('company'):
        try:
            from migration import digital_twin_company as CO
            return CO.same_company(row.get('company'), company)
        except Exception:
            return str(row.get('company')).strip().lower() == str(company).strip().lower()
    return False


def record_prediction(s3, kind: str, subject: str, prediction: dict, *, measure: str = '', twin_key: str = '', session_id: str = '', question_id: str = '',
                      question: str = '', question_kind: str = '', audience_class: str = '', method_version: str = '', basis: Optional[dict] = None,
                      post_hoc: bool = False, public: Optional[bool] = None, created_at: str = '', backfilled: bool = False, note: str = '',
                      company: str = '', house: Optional[bool] = None, logged: bool = False, logged_by: str = '', call: Optional[dict] = None,
                      created_by: str = '', status: str = 'call') -> Optional[dict]:
    """A prediction on the ledger. ``prediction`` carries any of point /
    low / high / unit, distribution [{label, pct}], probability, call.
    Idempotent per (kind, subject, session, question, measure). A call is
    frozen when it is made (Jenna 2026-11-20): the same prediction again
    returns the row as it was; a different one never replaces it and is
    written as a revision with its own date, so the first call keeps its
    hash and its date. ``call`` is what settles it (the due date, the
    check, the measure, how it is scored). ``company`` is the twin's
    company. A Crosswalk row is public only when it was logged on purpose
    (``logged``) or backfilled; a client's row is private until an admin
    releases it (``release``). Never raises."""
    try:
        kind = kind if kind in KINDS else 'call'
        rid = record_id(kind, subject, session_id, question_id, measure)
        have = load_record(s3, rid)
        pred = {k: v for k, v in (prediction or {}).items() if v is not None}
        if have:
            if _canon(have.get('prediction')) == _canon(pred):
                if call and not have.get('call') and not have.get('outcome'):
                    return update_call(s3, rid, call=call) or have      # what it is measured against can still be added
                return have
            return _revision(s3, have, pred, created_at=created_at, post_hoc=post_hoc, basis=basis, note=note)
        company = str(company or '')
        if house is None:
            house = is_house({'company': company, 'backfilled': backfilled}) if (company or backfilled) else False
        status = status if status in STATUSES else 'call'
        rec = {'id': rid, 'version': VERSION, 'kind': kind, 'subject': str(subject or ''), 'twin_key': str(twin_key or ''), 'session_id': str(session_id or ''),
               'question_id': str(question_id or ''), 'question': ' '.join(str(question or '').split())[:400], 'measure': str(measure or ''),
               'question_kind': str(question_kind or ''), 'audience_class': str(audience_class or ''), 'method_version': str(method_version or ''),
               'prediction': pred, 'basis': dict(basis or {}), 'created_at': created_at or _now(),
               'post_hoc': bool(post_hoc), 'backfilled': bool(backfilled), 'note': str(note or '')[:400],
               'company': company, 'house': bool(house), 'outcome': None, 'score': None, 'status': status,
               'logged': bool(logged), 'logged_by': str(logged_by or '')[:80], 'logged_at': (_now() if logged else ''),
               'call': dict(call) if isinstance(call, dict) else None, 'created_by': str(created_by or '')[:80], 'checks': {}}
        wanted = bool(public) if public is not None else bool(backfilled or (logged and house))
        rec['public'] = wanted and _public_allowed(rec)
        rec['hash'] = record_hash(rec)
        return _save(s3, rec)
    except Exception as e:
        print(f"[twin-ledger] record_prediction failed: {str(e)[:160]}")
        return None


def _revision(s3, have: dict, pred: dict, *, created_at: str = '', post_hoc: bool = False, basis: Optional[dict] = None, note: str = '') -> Optional[dict]:
    """A later prediction on a call already made: its own row, its own
    date and hash, never public, never scored; the first call stands."""
    rid = str(have.get('id') or '') + '_r' + hashlib.sha256(_canon(pred).encode()).hexdigest()[:4]
    ex = load_record(s3, rid)
    if ex:
        return ex
    rec = {k: v for k, v in have.items() if k not in ('hash', 'outcome', 'score', 'checks')}
    rec.update({'id': rid, 'prediction': pred, 'created_at': created_at or _now(), 'post_hoc': bool(post_hoc or have.get('post_hoc')), 'public': False,
                'logged': False, 'logged_by': '', 'logged_at': '', 'outcome': None, 'score': None, 'status': 'revision', 'revises': have.get('id'),
                'basis': dict(basis) if isinstance(basis, dict) else dict(have.get('basis') or {}), 'checks': {},
                'note': (str(note or '') + ' A later read of a call already on the record; the first call stands.').strip()[:400]})
    rec['hash'] = record_hash(rec)
    return _save(s3, rec)


_META_FIELDS = ('created_by', 'status_reason', 'folded_into', 'logged_by')


def update_call(s3, rid: str, *, call: Optional[dict] = None, checks: Optional[dict] = None, status: Optional[str] = None,
                logged: Optional[bool] = None, public: Optional[bool] = None, post_hoc: Optional[bool] = None, **meta) -> Optional[dict]:
    """What a call is measured against, its check trail, its status and
    who it belongs to. Never touches the prediction, the date, the hash
    or a written outcome. A row only ever becomes post hoc, never the
    other way. Never raises."""
    try:
        rec = load_record(s3, rid)
        if not rec:
            return None
        if isinstance(call, dict):
            cur = dict(rec.get('call') or {})
            cur.update({k: v for k, v in call.items() if v is not None})
            rec['call'] = cur
        if isinstance(checks, dict):
            cur = dict(rec.get('checks') or {})
            cur.update(checks)
            rec['checks'] = cur
        if status in STATUSES:
            rec['status'] = status
        if logged:
            rec['logged'] = True
            rec['logged_at'] = rec.get('logged_at') or _now()
        if post_hoc:
            rec['post_hoc'] = True
        for k in _META_FIELDS:
            if k in meta and meta[k] is not None:
                rec[k] = str(meta[k])[:200]
        if public is not None:
            rec['public'] = bool(public)
        rec['public'] = bool(rec.get('public')) and _public_allowed(rec)
        return _save(s3, rec)
    except Exception as e:
        print(f"[twin-ledger] update_call failed: {str(e)[:160]}")
        return None


def _same_outcome(a: dict, b: dict) -> bool:
    keys = ('value', 'distribution', 'happened', 'winner')
    return _canon({k: (a or {}).get(k) for k in keys}) == _canon({k: (b or {}).get(k) for k in keys})


def record_outcome(s3, rid: str, value=None, *, distribution: Optional[list] = None, happened: Optional[bool] = None, winner: Optional[str] = None,
                   source_class: str = '', note: str = '', observed_at: str = '') -> Optional[dict]:
    """The outcome on a call, scored. An outcome is written once: the
    same outcome again returns the row, a different one is refused (a
    miss is never revised). A row that is not a call is never scored.
    Never raises."""
    try:
        rec = load_record(s3, rid)
        if not rec or not is_call(rec):
            return None
        out = {'value': _num(value), 'distribution': [{'label': r.get('label'), 'pct': _num(r.get('pct'))} for r in (distribution or []) if isinstance(r, dict)] or None,
               'happened': happened, 'winner': winner, 'source_class': str(source_class or '')[:60], 'note': str(note or '')[:400], 'observed_at': observed_at or _now()}
        out = {k: v for k, v in out.items() if v is not None and v != []}
        if rec.get('outcome'):
            if _same_outcome(rec['outcome'], out):
                return rec
            print(f"[twin-ledger] outcome on {rid} already written; a different one is refused")
            return None
        rec['outcome'] = out
        rec['score'] = score(rec.get('prediction') or {}, rec['outcome'])
        return _save(s3, rec)
    except Exception as e:
        print(f"[twin-ledger] record_outcome failed: {str(e)[:160]}")
        return None


def twin_company_of(twin: Optional[dict], twin_key: str = '') -> str:
    """The company a twin belongs to, for the row (fail-safe '')."""
    try:
        from migration import digital_twin_company as CO
        return str(CO.twin_company(twin if isinstance(twin, dict) else None, None, str(twin_key or '')) or '')
    except Exception:
        return ''


def restamp(s3) -> int:
    """One pass over rows written before the company rode on the record
    (2026-11-17): the company from the twin key, house from it, and a
    client's row taken off the public record (a house row stays as it
    was). Idempotent; returns the rows changed."""
    n = 0
    for r in list(load_index(s3).get('records') or []):
        rec = load_record(s3, r['id'])
        if not rec or 'house' in rec:
            continue
        comp = str(rec.get('company') or '') or twin_company_of(None, rec.get('twin_key') or '')
        rec['company'] = comp
        rec['house'] = is_house({'company': comp, 'backfilled': rec.get('backfilled')})
        if not rec['house']:
            rec['public'] = False
        rec['public'] = bool(rec.get('public')) and _public_allowed(rec)
        _save(s3, rec)
        n += 1
    return n


def release(s3, rid: str, public: bool) -> Optional[dict]:
    """An admin puts a client's call on the public record, or takes it
    off. A post hoc row can never be public, and neither can a Crosswalk
    row that was not logged on purpose before its result. Never raises."""
    try:
        rec = load_record(s3, rid)
        if not rec:
            return None
        rec['public'] = bool(public) and _public_allowed(rec)
        return _save(s3, rec)
    except Exception as e:
        print(f"[twin-ledger] release failed: {str(e)[:160]}")
        return None


def find(s3, *, kind: str = '', subject: str = '', session_id: str = '', measure: str = '') -> list:
    rows = load_index(s3).get('records') or []
    out = []
    for r in rows:
        if kind and r.get('kind') != kind:
            continue
        if subject and str(r.get('subject') or '').lower() != str(subject).lower():
            continue
        if session_id and r.get('session_id') != session_id:
            continue
        if measure and r.get('measure') != measure:
            continue
        out.append(r)
    return out


# ------------------------------------------------------------------- summaries
def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def summary(s3, *, kind: str = '', question_kind: str = '', audience_class: str = '', measure: str = '', include_backfilled: bool = True,
            company: Optional[str] = None, staff: bool = False, site: bool = False, scope: bool = False) -> dict:
    """How the twins have done: counts, mean and median absolute error,
    mean relative error, coverage of the ranges, mean TVD, Brier and hit
    rate, over the scored rows (never the post hoc ones), by the filters
    given, with a breakdown by kind. With ``scope`` the rows are the ones
    a reader may see (``company`` / ``staff``); with ``site`` only the
    public ones. The firmness layer reads it unscoped (every scored row
    teaches it)."""
    rows = [r for r in (load_index(s3).get('records') or []) if is_call(r) and not r.get('post_hoc') and (include_backfilled or not r.get('backfilled'))]
    if site:
        rows = [r for r in rows if r.get('public') and _public_allowed(r)]
    elif scope:
        rows = [r for r in rows if visible(r, company=company, staff=staff)]
    if kind:
        rows = [r for r in rows if r.get('kind') == kind]
    if question_kind:
        rows = [r for r in rows if str(r.get('question_kind') or '') == question_kind]
    if audience_class:
        rows = [r for r in rows if str(r.get('audience_class') or '') == audience_class]
    if measure:
        rows = [r for r in rows if str(r.get('measure') or '') == measure]
    scored = [r for r in rows if r.get('has_outcome')]

    def block(rs):
        abs_e = [_num(r.get('abs_error')) for r in rs if _num(r.get('abs_error')) is not None]
        rel_e = [_num(r.get('rel_error')) for r in rs if _num(r.get('rel_error')) is not None]
        tv = [_num(r.get('tvd')) for r in rs if _num(r.get('tvd')) is not None]
        cov = [r.get('covered') for r in rs if r.get('covered') is not None]
        br = [_num(r.get('brier')) for r in rs if _num(r.get('brier')) is not None]
        hits = [r.get('hit') for r in rs if r.get('hit') is not None]
        return {'n': len(rs), 'mae': round(sum(abs_e) / len(abs_e), 4) if abs_e else None, 'median_abs_error': round(_median(abs_e), 4) if abs_e else None,
                'mean_rel_error': round(sum(rel_e) / len(rel_e), 4) if rel_e else None, 'coverage': round(sum(1 for c in cov if c) / len(cov), 4) if cov else None,
                'mean_tvd': round(sum(tv) / len(tv), 2) if tv else None, 'mean_brier': round(sum(br) / len(br), 4) if br else None,
                'hit_rate': round(sum(1 for h in hits if h) / len(hits), 4) if hits else None}
    out = {'n': len(rows), 'n_scored': len(scored), **block(scored), 'by_kind': {}, 'by_unit': {}, 'as_of': _now()}
    out['mae'] = None     # absolute error only means something within one unit; see by_unit
    for k in KINDS:
        ks = [r for r in scored if r.get('kind') == k]
        if ks:
            out['by_kind'][k] = block(ks)
    for u in sorted({str(r.get('unit') or '') for r in scored}):
        us = [r for r in scored if str(r.get('unit') or '') == u]
        if us:
            out['by_unit'][u or 'other'] = block(us)
    return out


def history_for(s3, kind: str, *, question_kind: str = '', audience_class: str = '', measure: str = '') -> dict:
    """What the ledger knows about rows like this one, for the firmness
    layer: the scored count and the typical error (points for a share,
    relative for a count). Narrow first, then wider, then the kind."""
    for filt in ({'question_kind': question_kind, 'audience_class': audience_class, 'measure': measure},
                 {'question_kind': question_kind, 'measure': measure}, {'measure': measure}, {}):
        filt = {k: v for k, v in filt.items() if v}
        sm = summary(s3, kind=kind, **filt)
        if sm.get('n_scored', 0) >= 3:
            pct_block = (sm.get('by_unit') or {}).get('pct') or {}
            typical = sm.get('mean_tvd') if sm.get('mean_tvd') is not None else (pct_block.get('mae') if pct_block.get('mae') is not None else None)
            return {'n_scored': sm['n_scored'], 'typical_error': typical, 'mean_rel_error': sm.get('mean_rel_error'), 'coverage': sm.get('coverage'), 'scope': filt or {'kind': kind}}
    return {'n_scored': 0, 'typical_error': None, 'mean_rel_error': None, 'coverage': None, 'scope': None}


# ------------------------------------------------------------------- the first rows
def backfill_known(s3) -> list:
    """The calls the house made public before their outcomes (the
    November 2026 video and the Paramount pilot), on the ledger with the
    dates they were made, marked backfilled. Idempotent."""
    rows = []
    try:
        # Texas Senate 2026: the room read Talarico 54.6 to Paxton 43.3 days out; certified 55.1 to 43.0
        r = record_prediction(s3, 'call', 'Texas likely voters', {'distribution': [{'label': 'Talarico', 'pct': 54.6}, {'label': 'Paxton', 'pct': 43.3}, {'label': 'Other', 'pct': 2.1}], 'call': 'Talarico', 'unit': 'pct'},
                              measure='vote share', question='Who wins the Texas Senate race?', question_kind='call', audience_class='civic', method_version='room-5',
                              created_at='2026-10-30T18:00:00Z', backfilled=True, note='Made public in the house video before election night.')
        if r:
            rows.append(record_outcome(s3, r['id'], distribution=[{'label': 'Talarico', 'pct': 55.1}, {'label': 'Paxton', 'pct': 43.0}, {'label': 'Other', 'pct': 1.9}], winner='Talarico',
                                       source_class='certified result', observed_at='2026-11-04T06:00:00Z', note='Texas Secretary of State, 100% reporting.'))
        # Ohio 2026: 7.9M potential voters, the room identified 4.2M likely to vote; about 4.6M did; the room had Acton from the first ask and she won
        r = record_prediction(s3, 'call', 'Ohio potential voters', {'point': 4_200_000, 'unit': 'people', 'call': 'Acton'}, measure='turnout',
                              question='How many of the 7.9M potential voters vote, and who wins the governor race?', question_kind='call', audience_class='civic', method_version='room-5',
                              created_at='2026-10-30T18:00:00Z', backfilled=True, note='Made public in the house video before election night.')
        if r:
            rows.append(record_outcome(s3, r['id'], value=4_600_000, winner='Acton', source_class='certified result', observed_at='2026-11-04T06:00:00Z', note='Acton 52.3 to Ramaswamy 44.4.'))
        # Nevada 4th: the room said Horsford by 23; the district landed at 22.7
        r = record_prediction(s3, 'call', 'Nevada 4th District voters', {'point': 23.0, 'unit': 'points', 'call': 'Horsford'}, measure='margin',
                              question='What is the margin in NV-4?', question_kind='call', audience_class='civic', method_version='room-5',
                              created_at='2026-10-30T18:00:00Z', backfilled=True, note='Made public in the house video before election night.')
        if r:
            rows.append(record_outcome(s3, r['id'], value=22.7, winner='Horsford', source_class='certified result', observed_at='2026-11-04T06:00:00Z', note='Horsford 60.4 to Whipple 37.7.'))
        # NCIS: New York pilot: week one 10.04M forecast, 10.25M drew; 83.9% would finish, 81.6% did within the week; 85.7% would come back, 86.8% did for episode two
        r = record_prediction(s3, 'forecast', 'NCIS: New York', {'point': 10_040_000, 'unit': 'people'}, measure='week_one_audience', session_id='e259d188bc',
                              question='Week-one audience if released', question_kind='forecast', audience_class='title', method_version='forecast-1',
                              created_at='2026-10-30T22:00:00Z', backfilled=True, note='The pilot test before air.')
        if r:
            rows.append(record_outcome(s3, r['id'], value=10_250_000, source_class='published', observed_at='2026-11-05T00:00:00Z', note="The fall's number one new series in week one."))
        r = record_prediction(s3, 'forecast', 'NCIS: New York', {'point': 83.9, 'unit': 'pct'}, measure='finish_pct', session_id='e259d188bc',
                              question='Share who finish the pilot', question_kind='forecast', audience_class='title', method_version='forecast-1',
                              created_at='2026-10-30T22:00:00Z', backfilled=True)
        if r:
            rows.append(record_outcome(s3, r['id'], value=81.6, source_class='panel', observed_at='2026-11-05T00:00:00Z', note='Finished within the week.'))
        r = record_prediction(s3, 'forecast', 'NCIS: New York', {'point': 85.7, 'unit': 'pct'}, measure='return_pct', session_id='e259d188bc',
                              question='Share who come back for episode two', question_kind='forecast', audience_class='title', method_version='forecast-1',
                              created_at='2026-10-30T22:00:00Z', backfilled=True)
        if r:
            rows.append(record_outcome(s3, r['id'], value=86.8, source_class='panel', observed_at='2026-11-05T00:00:00Z', note='Came back for episode two.'))
    except Exception as e:
        print(f"[twin-ledger] backfill failed: {str(e)[:160]}")
    return [r for r in rows if r]


# ------------------------------------------------------------------- the public view
def _row_view(rec: dict) -> dict:
    pred, out = rec.get('prediction') or {}, rec.get('outcome') or {}
    sc = rec.get('score') or {}
    fb = ((rec.get('basis') or {}).get('firmness') or {})
    cl = rec.get('call') or {}
    return {'id': rec.get('id'), 'subject': rec.get('subject'), 'what': rec.get('question') or rec.get('measure'), 'kind': rec.get('kind'), 'unit': pred.get('unit'),
            'said': pred.get('point') if pred.get('point') is not None else (pred.get('call') or (pred.get('distribution') or [{}])[0].get('label')),
            'said_distribution': pred.get('distribution'), 'low': pred.get('low'), 'high': pred.get('high'),
            'happened': out.get('value') if out.get('value') is not None else (out.get('winner') or (out.get('distribution') or [{}])[0].get('label')),
            'happened_distribution': out.get('distribution'), 'outcome_class': outcome_label(out.get('source_class')),
            'called_on': str(rec.get('created_at') or '')[:10], 'settled_on': str(out.get('observed_at') or '')[:10],
            'due_on': str(cl.get('due_at') or '')[:10], 'check': CHECK_LABELS.get(str(cl.get('resolver') or ''), ''), 'scored_as': str(cl.get('comparator') or ''),
            'options': [d.get('label') for d in (pred.get('distribution') or []) if isinstance(d, dict) and d.get('label')][:8],
            'abs_error': sc.get('abs_error'), 'rel_error': sc.get('rel_error'), 'tvd': sc.get('tvd'), 'covered': sc.get('covered'), 'hit': sc.get('hit'), 'hash': rec.get('hash'),
            'company': str(rec.get('company') or ''), 'house': is_house(rec), 'public': bool(rec.get('public')) and _public_allowed(rec), 'logged': bool(rec.get('logged')),
            'firmness': fb.get('word') if isinstance(fb, dict) else None, 'twin_key': rec.get('twin_key'), 'session_id': rec.get('session_id')}


def open_rows(s3, limit: int = 200, *, company: Optional[str] = None, staff: bool = False) -> list:
    """The calls still waiting on their outcome, newest first: what was
    said and when, dated by the hash. Scoped to what the reader may
    see; never a post hoc row, never a row that is not a call."""
    rows = []
    for r in reversed(load_index(s3).get('records') or []):
        if not is_call(r) or r.get('has_outcome') or r.get('post_hoc') or not visible(r, company=company, staff=staff):
            continue
        rec = load_record(s3, r['id']) or {}
        if rec:
            rows.append(_row_view(rec))
        if len(rows) >= limit:
            break
    return rows


def public_rows(s3, limit: int = 200, *, company: Optional[str] = None, staff: bool = False, site: bool = True) -> list:
    """The track record a reader may see: scored, never post hoc. With
    ``site`` (the default, the website and the partner surface) only the
    public rows: the house's own calls and any client row an admin
    released. With ``site=False`` the rows the seat may read in the
    dashboard: the public record plus its own company's calls; staff
    read everything. No method words, no source names beyond the class
    of the outcome."""
    rows = []
    for r in reversed(load_index(s3).get('records') or []):
        if not is_call(r) or not r.get('has_outcome') or r.get('post_hoc'):
            continue
        if site:
            if not (r.get('public') and _public_allowed(r)):
                continue
        elif not visible(r, company=company, staff=staff):
            continue
        rec = load_record(s3, r['id']) or {}
        if not rec:
            continue
        rows.append(_row_view(rec))
        if len(rows) >= limit:
            break
    return rows


__all__ = ['VERSION', 'KINDS', 'STATUSES', 'OUTCOME_LABELS', 'CHECK_LABELS', 'record_prediction', 'record_outcome', 'update_call', 'record_id', 'record_hash',
           'score', 'tvd', 'summary', 'history_for', 'find', 'load_record', 'load_index', 'rebuild_index', 'backfill_known', 'public_rows', 'open_rows', 'release',
           'visible', 'is_house', 'is_call', 'outcome_label', 'twin_company_of', 'restamp', 'reset_memory']
