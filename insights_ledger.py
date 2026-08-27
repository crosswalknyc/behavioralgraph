"""Insights ledger (2026-08-26, Jenna).

Every reasoned measurement Prometheus delivers is persisted here so the
same or a similar question is always answered from the same data
underpinnings: identical asks replay the exact stored numbers, adjacent
asks (same subject, overlapping metric family, different window or
slice) are generated under the stored numbers as binding constraints.

Jenna, verbatim: "you will need to save all synth oututs so that if the
same or similar question is ever asked the same data or similar asks
are based on the same data underpinings".

Layout on S3 (bucket dashboard-inputs):

    system/insights_ledger/index.json
        {"subjects": {"<subject_key>": {"subject": "Landman",
                                        "entries": [<compact entry>...]}}}
        Maintained with ETag CAS (read -> mutate -> conditional PUT ->
        retry on 412), same pattern as migration/s3_json_state.py.
        bg-webapp deploys standalone on Render, so the loop is inlined
        here rather than imported.

    system/insights_ledger/entries/YYYY-MM-DD/<hhmmss>_<id>.json
        One append-only audit object per delivered read (never listed
        on the hot path; the index is the lookup surface).

Consult path (before any answer is generated):
    consult(subject=..., question=...) ->
        {'subject', 'entries', 'block', 'exact'}
    - 'block' is the PUBLISHED MEASUREMENTS body handed to the prompt
      builders (binding constraints on the model).
    - 'exact' is a stored entry whose normalized question or normalized
      key matches this ask: the caller replays its reply verbatim and
      skips generation entirely.

Persist path (after any generated read ships):
    persist(...) -> fire-and-forget: per-entry object + CAS index
    update on a daemon thread, zero added latency on the reply.
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, ParamValidationError

S3_BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
LEDGER_PREFIX = 'system/insights_ledger/'
INDEX_KEY = LEDGER_PREFIX + 'index.json'
ENTRY_PREFIX = LEDGER_PREFIX + 'entries/'

MAX_ENTRIES_PER_SUBJECT = 40
MAX_METRICS_PER_ENTRY = 10
MAX_REPLY_CHARS = 4000

# Test hook: when True, persist() runs inline so unit tests can assert
# on the written index synchronously.
_SYNC_FOR_TESTS = False

_s3 = None
_s3_lock = threading.Lock()

_INDEX_TTL_S = 45.0
_index_cache = {'ts': 0.0, 'doc': None}
_cache_lock = threading.Lock()

# Older botocore lacks IfMatch/IfNoneMatch on put_object; degrade to
# verify-ETag-then-put for the process lifetime (same posture as
# migration/s3_json_state.py).
_SUPPORTS_CONDITIONAL_PUT = True


def _client():
    global _s3
    if _s3 is None:
        with _s3_lock:
            if _s3 is None:
                _s3 = boto3.client('s3')
    return _s3


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_FAMILY_ALIASES = (
    ('viewership', ('viewership', 'view', 'watch', 'stream', 'play',
                    'tune')),
    ('subscribers', ('subscri', 'signup', 'sign up', 'sign-up', 'member',
                     'churn')),
    ('search', ('search', 'quer', 'demand')),
    ('purchases', ('purchas', 'buy', 'bought', 'shop', 'sale', 'ecommerce',
                   'e-commerce', 'transaction', 'rent', 'order')),
    ('engagement', ('engag', 'session', 'visit', 'social', 'follow',
                    'app activity')),
    ('revenue', ('revenue', 'spend', 'dollar', 'arpu')),
    ('audience', ('audience', 'reach', 'fans', 'cohort')),
)


def canon_family(family):
    f = str(family or '').strip().lower()
    if not f:
        return 'audience'
    for canon, aliases in _FAMILY_ALIASES:
        if f == canon:
            return canon
    for canon, aliases in _FAMILY_ALIASES:
        if any(a in f for a in aliases):
            return canon
    return 'audience'


def normalize_subject(subject):
    return re.sub(r'[^a-z0-9]+', ' ', str(subject or '').lower()).strip()


def subject_key(subject):
    return (normalize_subject(subject).replace(' ', '_')[:80]
            or 'unknown')


def normalize_question(question):
    return re.sub(r'[^a-z0-9]+', ' ',
                  str(question or '').lower()).strip()[:300]


def entry_key(subject, metric_family, window_start=None, window_end=None):
    ws = str(window_start or '').strip() or 'any'
    we = str(window_end or '').strip() or 'any'
    return f"{subject_key(subject)}|{canon_family(metric_family)}|{ws}|{we}"


# ---------------------------------------------------------------------------
# S3 plumbing: CAS index update + per-entry audit objects
# ---------------------------------------------------------------------------

def _read_index_with_etag():
    try:
        resp = _client().get_object(Bucket=S3_BUCKET, Key=INDEX_KEY)
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('NoSuchKey', '404', 'NotFound'):
            return None, None
        raise
    body = resp['Body'].read().decode('utf-8')
    try:
        doc = json.loads(body) if body.strip() else None
    except Exception:
        doc = None
    return doc, resp.get('ETag')


def _is_precondition_failed(err):
    code = (err.response.get('Error') or {}).get('Code', '')
    status = (err.response.get('ResponseMetadata')
              or {}).get('HTTPStatusCode')
    return code in ('PreconditionFailed', '412') or status == 412


def _put_index_cas(doc, etag):
    """Conditional PUT of the index. True on success, False on a
    detected conflict. Degrades to verify-then-put on old botocore."""
    global _SUPPORTS_CONDITIONAL_PUT
    s3 = _client()
    kwargs = dict(Bucket=S3_BUCKET, Key=INDEX_KEY,
                  Body=json.dumps(doc).encode('utf-8'),
                  ContentType='application/json')
    if _SUPPORTS_CONDITIONAL_PUT:
        k2 = dict(kwargs)
        if etag:
            k2['IfMatch'] = etag.strip('"')
        else:
            k2['IfNoneMatch'] = '*'
        try:
            s3.put_object(**k2)
            return True
        except ParamValidationError:
            _SUPPORTS_CONDITIONAL_PUT = False
        except ClientError as e:
            if _is_precondition_failed(e):
                return False
            raise
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=INDEX_KEY)
        current = (head.get('ETag') or '').strip('"')
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('404', 'NoSuchKey', 'NotFound'):
            current = None
        else:
            raise
    expected = etag.strip('"') if etag else None
    if current != expected:
        return False
    s3.put_object(**kwargs)
    return True


def _update_index(mutate_fn, max_retries=5):
    """GET -> mutate -> conditional PUT with retry on conflict. Quietly
    gives up after max_retries (the per-entry audit object always
    lands, so a lost index update is recoverable, never fatal)."""
    import random
    for attempt in range(max_retries + 1):
        try:
            doc, etag = _read_index_with_etag()
        except Exception as e:
            print(f"[insights-ledger] index read failed: {e}")
            return None
        doc = doc if isinstance(doc, dict) else {}
        doc.setdefault('subjects', {})
        new_doc = mutate_fn(doc)
        if new_doc is None:
            return None
        try:
            if _put_index_cas(new_doc, etag):
                with _cache_lock:
                    _index_cache['doc'] = new_doc
                    _index_cache['ts'] = time.time()
                return new_doc
        except Exception as e:
            print(f"[insights-ledger] index put failed: {e}")
            return None
        time.sleep(min(0.2 * (2 ** attempt), 2.0)
                   + random.uniform(0, 0.2))
    print("[insights-ledger] index update gave up after "
          f"{max_retries + 1} conflicts")
    return None


def _load_index(force=False):
    now = time.time()
    with _cache_lock:
        if (not force and _index_cache['doc'] is not None
                and now - _index_cache['ts'] < _INDEX_TTL_S):
            return _index_cache['doc']
    try:
        doc, _etag = _read_index_with_etag()
    except Exception as e:
        print(f"[insights-ledger] index load failed: {e}")
        doc = None
    doc = doc if isinstance(doc, dict) else {'subjects': {}}
    doc.setdefault('subjects', {})
    with _cache_lock:
        _index_cache['doc'] = doc
        _index_cache['ts'] = now
    return doc


def _put_entry_object(entry):
    day = entry['ts'][:10]
    key = (f"{ENTRY_PREFIX}{day}/"
           f"{entry['ts'][11:19].replace(':', '')}_"
           f"{uuid.uuid4().hex[:10]}.json")
    _client().put_object(Bucket=S3_BUCKET, Key=key,
                         Body=json.dumps(entry).encode('utf-8'),
                         ContentType='application/json')


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def make_entry(*, subject, metric_family, question, route, metrics,
               anchors=None, window_start='', window_end='',
               window_label='', reply='', followups=None,
               base_profile_key=''):
    """Build one ledger entry. `metrics` is a list of dicts with
    name/label/value/unit/definition (extra keys dropped).

    `base_profile_key` (2026-08-27, Jenna): the s3 key of the base
    profile (or the pulled read, e.g. a Subscriber IQ run) the
    generated numbers derive from. Generated reads only exist as
    derivations of an existing base; callers on the generation paths
    always pass it."""
    clean_metrics = []
    for m in (metrics or [])[:MAX_METRICS_PER_ENTRY]:
        if not isinstance(m, dict):
            continue
        name = str(m.get('name') or '').strip()[:48]
        if not name:
            continue
        val = m.get('value')
        if not isinstance(val, (int, float)):
            continue
        clean_metrics.append({
            'name': name,
            'label': str(m.get('label') or name)[:90],
            'value': val,
            'unit': str(m.get('unit') or 'count')[:24],
            'definition': str(m.get('definition') or '')[:220],
        })
    entry = {
        'k': entry_key(subject, metric_family, window_start, window_end),
        'subject': str(subject or '')[:120],
        'family': canon_family(metric_family),
        'q': str(question or '')[:300],
        'qn': normalize_question(question),
        'route': str(route or '')[:40],
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'ws': str(window_start or '')[:12],
        'we': str(window_end or '')[:12],
        'wl': str(window_label or '')[:80],
        'metrics': clean_metrics,
        'anchors': [str(a)[:120] for a in (anchors or []) if str(a)][:6],
        'base': str(base_profile_key or '')[:220],
        'reply': str(reply or '')[:MAX_REPLY_CHARS],
        'followups': [str(f)[:160] for f in (followups or [])
                      if str(f)][:4],
    }
    return entry


def _record_entry_now(entry):
    try:
        _put_entry_object(entry)
    except Exception as e:
        print(f"[insights-ledger] entry put failed: {e}")
    skey = subject_key(entry.get('subject'))

    def mutate(doc):
        subj = doc['subjects'].setdefault(
            skey, {'subject': entry.get('subject') or skey, 'entries': []})
        ents = subj.get('entries') or []
        ents.append(entry)
        subj['entries'] = ents[-MAX_ENTRIES_PER_SUBJECT:]
        doc['updated'] = entry['ts']
        return doc

    try:
        _update_index(mutate)
    except Exception as e:
        print(f"[insights-ledger] index update failed: {e}")


def persist(*, subject, metric_family, question, route, metrics,
            anchors=None, window_start='', window_end='',
            window_label='', reply='', followups=None,
            base_profile_key=''):
    """Persist one delivered read. Never raises; never blocks the
    caller (daemon thread) unless _SYNC_FOR_TESTS."""
    try:
        entry = make_entry(
            subject=subject, metric_family=metric_family,
            question=question, route=route, metrics=metrics,
            anchors=anchors, window_start=window_start,
            window_end=window_end, window_label=window_label,
            reply=reply, followups=followups,
            base_profile_key=base_profile_key)
        if not entry['metrics'] and not entry['reply']:
            return
        if _SYNC_FOR_TESTS:
            _record_entry_now(entry)
            return
        threading.Thread(target=_record_entry_now, args=(entry,),
                         daemon=True).start()
    except Exception as e:
        print(f"[insights-ledger] persist failed: {e}")


# ---------------------------------------------------------------------------
# Consult
# ---------------------------------------------------------------------------

def _resolve_subject(index_doc, subject=None, question=None):
    """Find the subject bucket for this ask. Explicit subject first
    (normalized match), else the longest ledger subject whose
    normalized form appears inside the normalized question."""
    subjects = index_doc.get('subjects') or {}
    if subject:
        skey = subject_key(subject)
        if skey in subjects:
            return skey
        norm = normalize_subject(subject)
        for k, v in subjects.items():
            if normalize_subject(v.get('subject')) == norm:
                return k
        return None
    qn = ' ' + normalize_question(question) + ' '
    best_key, best_len = None, 0
    for k, v in subjects.items():
        s_norm = normalize_subject(v.get('subject'))
        if not s_norm or len(s_norm) < 3:
            continue
        if f' {s_norm} ' in qn and len(s_norm) > best_len:
            best_key, best_len = k, len(s_norm)
    return best_key


def render_block(entries, limit=12):
    """PUBLISHED MEASUREMENTS body for the prompt builders: one line
    per stored metric, most recent entries first."""
    lines = []
    for e in reversed(entries[-limit:]):
        win = e.get('wl') or (f"{e.get('ws')} to {e.get('we')}"
                              if e.get('ws') and e.get('we') else 'any window')
        for m in (e.get('metrics') or [])[:6]:
            unit = m.get('unit') or ''
            if unit in ('pct', 'percent', 'percentage', '%'):
                val = f"{m['value']:.1f}%"
            else:
                val = f"{m['value']:,}"
                if unit and unit != 'count':
                    val += f" {unit}"
            d = f" ({m['definition']})" if m.get('definition') else ''
            lines.append(f"- {e.get('subject')} | {e.get('family')} | "
                         f"{win}: {m.get('label')} = {val}{d}")
        if len(lines) >= 30:
            break
    return '\n'.join(lines[:30])


def find_exact(entries, question=None, key=None):
    """Most recent stored entry matching this exact ask: normalized
    question match first (strongest), else normalized key match with a
    non-empty stored reply."""
    qn = normalize_question(question) if question else None
    for e in reversed(entries):
        if qn and e.get('qn') == qn and e.get('reply'):
            return e
    if key:
        for e in reversed(entries):
            if e.get('k') == key and e.get('reply'):
                return e
    return None


def consult(subject=None, question=None, metric_family=None,
            window_start=None, window_end=None):
    """Look up ledger history for this ask.

    Returns {'subject', 'skey', 'entries', 'block', 'exact'}:
    - entries: stored entries for the resolved subject (oldest first)
    - block:   PUBLISHED MEASUREMENTS body ('' when no history)
    - exact:   entry to replay verbatim, or None
    Never raises; empty result on any failure.
    """
    empty = {'subject': None, 'skey': None, 'entries': [],
             'block': '', 'exact': None}
    try:
        doc = _load_index()
        skey = _resolve_subject(doc, subject=subject, question=question)
        if not skey:
            return empty
        bucket = (doc.get('subjects') or {}).get(skey) or {}
        entries = [e for e in (bucket.get('entries') or [])
                   if isinstance(e, dict)]
        if not entries:
            return empty
        key = None
        if metric_family:
            key = entry_key(bucket.get('subject') or subject or '',
                            metric_family, window_start, window_end)
        exact = find_exact(entries, question=question, key=key)
        return {'subject': bucket.get('subject'), 'skey': skey,
                'entries': entries, 'block': render_block(entries),
                'exact': exact}
    except Exception as e:
        print(f"[insights-ledger] consult failed: {e}")
        return empty


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def metrics_from_study(study):
    """Map a search-demand study (enforce_demand_coherence output) to
    ledger metrics so search reads join the same consistency surface."""
    out = []
    if not isinstance(study, dict):
        return out
    if study.get('unique_cohort'):
        out.append({'name': 'unique_cohort',
                    'label': study.get('cohort_label') or 'Unique cohort',
                    'value': study['unique_cohort'], 'unit': 'viewers',
                    'definition': 'unique US individuals in the window'})
    rh = study.get('rival_hunt') or {}
    if rh.get('union'):
        rival = study.get('rival') or 'rival platform'
        out.append({'name': 'rival_hunt_union',
                    'label': f"Unique people who hunted it on {rival}",
                    'value': rh['union'], 'unit': 'people',
                    'definition': 'in-app plus platform-named Google '
                                  'searches, deduped'})
    if rh.get('converted_24h'):
        out.append({'name': 'rival_hunt_converted_24h',
                    'label': 'Hunters who played inside 24 hours',
                    'value': rh['converted_24h'], 'unit': 'people',
                    'definition': 'subset of the hunt union with a home-'
                                  'platform play inside 24 hours'})
    hs = study.get('home_search') or {}
    if hs.get('union'):
        out.append({'name': 'home_search_union',
                    'label': 'Search pointed at the home platform',
                    'value': hs['union'], 'unit': 'people',
                    'definition': 'in-app plus platform-named Google '
                                  'searches, deduped'})
    q = study.get('quality') or {}
    if q.get('new_accounts'):
        out.append({'name': 'new_accounts',
                    'label': 'New accounts opened off a first play',
                    'value': q['new_accounts'], 'unit': 'accounts',
                    'definition': 'no home-platform visit in the prior '
                                  '180 days'})
    if q.get('completion_pct') is not None:
        out.append({'name': 'completion_pct',
                    'label': 'Completion rate',
                    'value': q['completion_pct'], 'unit': 'pct',
                    'definition': 'share of the runtime completed'})
    return out


__all__ = ['consult', 'persist', 'make_entry', 'render_block',
           'find_exact', 'metrics_from_study', 'entry_key',
           'canon_family', 'subject_key', 'normalize_question',
           'normalize_subject', 'LEDGER_PREFIX', 'INDEX_KEY',
           'ENTRY_PREFIX']
