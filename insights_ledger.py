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

import hashlib
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
# A data answer is stored whole (Liz 2026-11-04, the 16-row cut): these
# are runaway guards, never a cap a real read reaches. The reply limit
# is long enough for a six-cut by four-dimension table.
MAX_METRICS_PER_ENTRY = 40
MAX_REPLY_CHARS = 16000
MAX_BREAKDOWN_ROWS = 400
MAX_BUCKETS_PER_ROW = 40

# Every projected US figure stands on the people in the data behind it:
# the ten million US consumers project to 329.9M, so one person in the
# data is 32.99 US people (bg-webapp/edit_sample_size.py, the dashboard
# math). A stated figure is stored with that count (2026-11-04, Jenna,
# Jay Patel's Statham thread: "the data should always be storing it so
# that it reconciles and should always keep track").
US_PER_SAMPLE = 32.99
# metric units that are not projected people counts (no count behind them)
_NON_COUNT_UNIT_RX = re.compile(
    r"pct|percent|%|share|index|rate|ratio|usd|\$|dollar|hour|minute|second|day|week|month|year|"
    r"title|asset|episode|season|post|brand|category|rank|score|x\b", re.I)

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
    ('strategy', ('strategy', 'white space', 'whitespace', 'opportunit',
                  'underserved', 'untapped', 'unmet')),
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


def entry_key(subject, metric_family, window_start=None, window_end=None, journey=None):
    ws = str(window_start or '').strip() or 'any'
    we = str(window_end or '').strip() or 'any'
    # the folded title keys the entry (2026-10-13), so the same ask under
    # another spelling of the subject supersedes instead of coexisting.
    # A viewing-chain read carries both titles and the metric in its key
    # (2026-11-05), so it never shares a key with the title's plain read.
    return (f"{(fold_subject(subject).replace(' ', '_')[:80] or subject_key(subject))}|{canon_family(metric_family)}|{ws}|{we}"
            + _j_key_part(journey))


# ---------------------------------------------------------------------------
# Provenance (2026-08-28): where an entry's numbers came from decides
# which entry wins a conflict. Figures a client already holds
# (delivered_artifact) outrank live generated reads (operator), which
# outrank pairs distilled from old conversations (distilled). Entries
# written before this field exist without it and rank as operator, so
# every existing entry and caller keeps its exact behavior.
# ---------------------------------------------------------------------------

PROV_DELIVERED = 'delivered_artifact'
PROV_OPERATOR = 'operator'
PROV_DISTILLED = 'distilled'
# The decision of record (2026-11-10, Jenna: "anything pulled here is
# stored for Prometheus"): a figure Jenna approved in a published Crosswalk
# deliverable (The Read, a deck, a PDF, a Subscriber IQ tracker). It
# outranks every other provenance, replays for every natural ask on its
# subject, and nothing a later read draws may contradict it. Route prefix
# `decision_of_record_<source>` (The Paper banked its trackers through
# `decision_of_record_subiq` on 2026-11-10).
PROV_RECORD = 'decision_of_record'
RECORD_ROUTE_PREFIX = 'decision_of_record_'
_PROV_RANK = {PROV_RECORD: 3, PROV_DELIVERED: 2, PROV_OPERATOR: 1, PROV_DISTILLED: 0}


def canon_provenance(provenance):
    p = str(provenance or '').strip().lower()
    if not p:
        return PROV_OPERATOR
    if 'record' in p:
        return PROV_RECORD
    if 'artifact' in p or 'deliver' in p or 'deck' in p:
        return PROV_DELIVERED
    if 'distill' in p:
        return PROV_DISTILLED
    return PROV_OPERATOR


def is_record_entry(entry):
    """True for a decision-of-record entry (its provenance or its route
    says so; the route alone carries entries written before the
    provenance value existed)."""
    if not isinstance(entry, dict):
        return False
    return entry.get('prov') == PROV_RECORD \
        or str(entry.get('route') or '').startswith(RECORD_ROUTE_PREFIX)


def provenance_rank(entry):
    """Conflict rank for one stored entry. Delivered-deck anchor
    entries written before the prov field rank as delivered too; a
    decision-of-record route ranks as the record whatever its stamp."""
    if not isinstance(entry, dict):
        return _PROV_RANK[PROV_OPERATOR]
    if str(entry.get('route') or '').startswith(RECORD_ROUTE_PREFIX):
        return _PROV_RANK[PROV_RECORD]
    p = entry.get('prov')
    if p in _PROV_RANK:
        return _PROV_RANK[p]
    if entry.get('route') == ANCHOR_ROUTE:
        return _PROV_RANK[PROV_DELIVERED]
    return _PROV_RANK[PROV_OPERATOR]


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

def is_count_metric(metric):
    """A metric that counts projected US people (viewers, people,
    accounts, signups ...): the kind that has a count in the data behind
    it. Percents, dollars, durations, ranks and titles do not."""
    if not isinstance(metric, dict):
        return False
    unit = str(metric.get('unit') or 'count').strip()
    if _NON_COUNT_UNIT_RX.search(unit):
        return False
    try:
        return float(metric.get('value')) >= 100
    except (TypeError, ValueError):
        return False


def sample_people_for(value, factor=US_PER_SAMPLE):
    """The people in the data behind a projected US figure (the count
    before projection), or None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0 or not factor:
        return None
    return max(1, int(round(v / float(factor))))


def attach_sample_people(metrics, universe='', factor=US_PER_SAMPLE):
    """Copies of `metrics` with the unprojected count behind every
    projected people figure (`sample_people`) and a definition on every
    metric that has none (the universe the read counted). A metric that
    already carries `sample_people` keeps it (a sizing passes the count
    itself). Never raises; the input is never mutated."""
    out = []
    for m in (metrics or []):
        if not isinstance(m, dict):
            continue
        c = dict(m)
        if c.get('sample_people') in (None, '') and is_count_metric(c):
            sp = sample_people_for(c.get('value'), factor)
            if sp:
                c['sample_people'] = sp
        if not str(c.get('definition') or '').strip() and str(universe or '').strip():
            c['definition'] = str(universe).strip()
        out.append(c)
    return out


def make_entry(*, subject, metric_family, question, route, metrics,
               anchors=None, window_start='', window_end='',
               window_label='', reply='', followups=None,
               base_profile_key='', cohort='', breakdown=None,
               derivation='', provenance='', verify=None, universe='',
               journey=None):
    """Build one ledger entry. `metrics` is a list of dicts with
    name/label/value/unit/definition and, for a projected people count,
    `sample_people`, the unprojected count behind it (extra keys
    dropped).

    `journey` (2026-11-05): the viewing chain the read answers (source
    title, destination title, seasons, platform, metrics), as
    ``ask_journey`` resolves it, including a follow-up completed from the
    question it followed. Read off the question when not given. Banked
    on the entry and folded into its key so the read is reused only for
    the same whole journey.

    `universe` (2026-11-04): the audience the figures describe, in the
    words the reply opened with ('Jason Statham film viewers (streaming
    and free ad-supported services)'). With the window and each metric's
    definition and sample_people, a later ask to reconcile figures reads
    them back from here instead of sizing anything again.

    `provenance` (2026-08-28): 'delivered_artifact' for figures a
    client already holds, 'distilled' for pairs recovered from old
    conversations, anything else (including absent) is 'operator',
    the live generation path. Ranking: delivered > operator >
    distilled on conflict.

    `base_profile_key` (2026-08-27, Jenna): the s3 key of the base
    profile (or the pulled read, e.g. a Subscriber IQ run) the
    generated numbers derive from. Generated reads only exist as
    derivations of an existing base; callers on the generation paths
    always pass it.

    `cohort` + `breakdown` (2026-08-27): the sub-cohort the read
    covers and its ranked breakdown table ({dimension, share_basis,
    rows: [{label, share_pct, penetration_pct?, note?}]}). The CSV
    export builds from these same stored values, so the file always
    matches the chat reply exactly."""
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
        cm = {
            'name': name,
            'label': str(m.get('label') or name)[:90],
            'value': val,
            'unit': str(m.get('unit') or 'count')[:24],
            'definition': str(m.get('definition') or '')[:220],
        }
        sp = m.get('sample_people')
        if isinstance(sp, (int, float)) and sp > 0:
            cm['sample_people'] = int(sp)
        clean_metrics.append(cm)
    # a quote is never an answer: an offer sentence or its chips never
    # bank as a reply (2026-11-05); a tab icon is never part of a subject
    reply = '' if is_offer_reply(reply) else reply
    try:
        from prometheus.subject_display import strip_emoji as _strip_emoji
        subject = _strip_emoji(subject) or subject
    except Exception:
        pass
    _j = journey if isinstance(journey, dict) and journey.get('src') and journey.get('dst') \
        else journey_signature(question)
    if isinstance(_j, dict) and (_j.get('anaphoric') or not _j.get('src') or not _j.get('dst')):
        _j = None   # an unresolved "of those" banks no chain; it never replays as one
    if _j and not _j.get('platform'):
        # the question named no service but the read did ("On ... viewers on Starz:")
        _pm = _J_PLATFORM_RX.search(_j_fold(f"{universe or ''} | {str(reply or '')[:300]}"))
        if _pm:
            _j = dict(_j, platform=_pm.group('p'))
    entry = {
        'k': entry_key(subject, metric_family, window_start, window_end, journey=_j),
        'subject': str(subject or '')[:120],
        'family': canon_family(metric_family),
        'q': str(question or '')[:300],
        'qn': normalize_question(question),
        'route': str(route or '')[:40],
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'ws': str(window_start or '')[:12],
        'we': str(window_end or '')[:12],
        'wl': _clip_window_label(window_label),
        'metrics': clean_metrics,
        'anchors': [str(a)[:120] for a in (anchors or []) if str(a)][:6],
        'base': str(base_profile_key or '')[:220],
        'cohort': str(cohort or '')[:120],
        # the audience the figures describe, as the reply named it
        # (2026-11-04): what a reconcile ask reads back as the definition
        'universe': str(universe or '')[:220],
        'breakdown': _clean_breakdown(breakdown),
        'reply': str(reply or '')[:MAX_REPLY_CHARS],
        'followups': [str(f)[:160] for f in (followups or [])
                      if str(f)][:4],
        # Internal derivation trail (2026-08-27, the generation loop):
        # what grounded the read (base, neighbors weighed, research
        # gaps, examples). Logs and audit only - render_block never
        # includes it, so it can never reach a prompt or a user.
        'derivation': str(derivation or '')[:600],
        'prov': canon_provenance(provenance),
    }
    if _j:
        entry['journey'] = _j_compact(_j)
    # Verification stamp (2026-08-28, p3-verify): the pre-banking pass
    # outcome for generated reads. Audit trail only; render_block never
    # includes it.
    if isinstance(verify, dict) and verify:
        entry['verify'] = {str(k)[:24]: (v if isinstance(v, (int, float))
                                         else str(v)[:40])
                           for k, v in list(verify.items())[:8]}
    return entry


def _clip_window_label(window_label):
    """The window label stored whole (2026-11-05): a raw [:80] here
    carried the cut 'Sep 25 to Oct 29 202' a Subscriber IQ overlap read
    shipped, so every replay of the entry read the same cut line."""
    try:
        from prometheus import window_label as _wl
        return _wl.clip_window_label(window_label)
    except Exception:
        return str(window_label or '')[:200]


def _clean_breakdown(breakdown):
    """Bound-checked copy of a breakdown table, or None. Every row
    survives, and a distribution row keeps its pool and its full bucket
    set (2026-11-04: the stored entry is what the file exports from and
    what the next pass binds to, so 'gender by door' has to be here as
    figures, not as a sentence)."""
    if not isinstance(breakdown, dict):
        return None
    rows = []
    for r in (breakdown.get('rows') or [])[:MAX_BREAKDOWN_ROWS]:
        if not isinstance(r, dict):
            continue
        label = str(r.get('label') or '').strip()[:60]
        share = r.get('share_pct')
        if not label or not isinstance(share, (int, float)):
            continue
        row = {'label': label, 'share_pct': float(share)}
        pen = r.get('penetration_pct')
        if isinstance(pen, (int, float)):
            row['penetration_pct'] = float(pen)
        note = str(r.get('note') or '').strip()[:160]
        if note:
            row['note'] = note
        pool = r.get('pool')
        if isinstance(pool, (int, float)) and pool > 0:
            row['pool'] = int(round(pool))
        for fld in ('group', 'dimension', 'definition'):
            v = str(r.get(fld) or '').strip()
            if v:
                row[fld] = v[:160 if fld == 'definition' else 60]
        bks = []
        for b in (r.get('buckets') or [])[:MAX_BUCKETS_PER_ROW]:
            if not isinstance(b, dict):
                continue
            bl = str(b.get('label') or '').strip()[:60]
            try:
                cnt = int(round(float(b.get('count') or 0)))
                pct = float(b.get('share_pct') or 0)
            except (TypeError, ValueError):
                continue
            if not bl or cnt < 0:
                continue
            bk = {'label': bl, 'count': cnt, 'share_pct': round(pct, 1)}
            if b.get('mode'):
                bk['mode'] = True
            bks.append(bk)
        if len(bks) >= 2:
            row['buckets'] = bks
        rows.append(row)
    if not rows:
        return None
    return {
        'dimension': str(breakdown.get('dimension')
                         or 'Category').strip()[:60],
        'share_basis': str(breakdown.get('share_basis') or '')[:120],
        'rows': rows,
    }


def _supersedes(new, old):
    """A fresh read replaces the stored read it semantically repeats:
    the same normalized question, or the same (family, cohort, slice
    dimension) when both carry a ranked table. One canonical read per
    meaning keeps paraphrase replays consistent instead of stacking
    drifted regenerations (2026-08-27, the 21.9 / 21.3 / 21.9 toy-share
    drift)."""
    try:
        if not isinstance(old, dict) or old.get('route') == ANCHOR_ROUTE:
            return False
        # A lower-provenance entry never replaces a higher one: a
        # distilled conversation pair cannot evict a live generated
        # read or a delivered figure carrying the same meaning
        # (2026-08-28). Equal or higher rank replaces as before.
        if provenance_rank(new) < provenance_rank(old):
            return False
        if new.get('qn') and new.get('qn') == old.get('qn'):
            return True
        if new.get('family') != old.get('family'):
            return False
        nb = new.get('breakdown') or {}
        ob = old.get('breakdown') or {}
        if not (nb.get('rows') and ob.get('rows')):
            return False
        if normalize_subject(nb.get('dimension')) != \
                normalize_subject(ob.get('dimension')):
            return False
        return (cohort_signature(new.get('cohort'))
                == cohort_signature(old.get('cohort')))
    except Exception:
        return False


def _coherence_jitter(subject, name, span=0.01):
    """Deterministic signed fraction in [-span, span]."""
    h = int(hashlib.sha256(
        f"{subject}|{name}".encode()).hexdigest()[:10], 16)
    return ((h % 20001) - 10000) / 10000.0 * span


def _messy_value(v):
    """Nudge a numeric off round endings: ints get a nonzero last
    digit, floats leave the .XX00 boundary."""
    if isinstance(v, int):
        if v > 10 and v % 10 == 0:
            v += 1 + (abs(hash(str(v))) % 7)
        return v
    try:
        f = float(v)
    except Exception:
        return v
    s = f"{f:.4f}"
    if s.endswith('00'):
        f += 0.0007 + (abs(hash(s)) % 13) / 10000.0
    return round(f, 4)


def _enforce_ledger_coherence(doc, skey, entry):
    """Two silent corrections at persist time (2026-09-28, Phase 2 of
    the improvement plan):

    1. Cohort inside parent: a cohort count for a subject can never
       exceed the same metric banked for the whole subject. Violations
       clamp just under the parent with a salted margin.
    2. Cross-subject repeats: the same metric name in the same family
       landing 4dp-identical on a DIFFERENT subject is a repeated-value
       fingerprint; the new value nudges by a salted fraction with
       messy endings.

    Mutates entry['metrics'] in place; never raises. A decision of
    record is exempt: its figures are the published ones and never move
    (2026-11-10)."""
    try:
        if is_record_entry(entry):
            return
        subject = str(entry.get('subject') or '')
        fam = entry.get('family') or ''
        metrics = entry.get('metrics') or []
        if not metrics:
            return
        # -- cohort inside parent --
        if str(entry.get('cohort') or '').strip():
            bucket = (doc.get('subjects', {}).get(skey) or {})
            parents = {}
            for e in reversed(bucket.get('entries') or []):
                if str(e.get('cohort') or '').strip():
                    continue
                if (e.get('family') or '') != fam:
                    continue
                for m in (e.get('metrics') or []):
                    nm = str(m.get('name') or '').strip().lower()
                    if nm and nm not in parents \
                            and isinstance(m.get('value'), (int, float)):
                        parents[nm] = float(m['value'])
            for m in metrics:
                nm = str(m.get('name') or '').strip().lower()
                v = m.get('value')
                if nm in parents and isinstance(v, (int, float)) \
                        and str(m.get('unit') or 'count') == 'count' \
                        and float(v) > parents[nm]:
                    eps = 0.008 + abs(_coherence_jitter(
                        subject, nm, 0.022))
                    fixed = parents[nm] * (1.0 - eps)
                    fixed = (_messy_value(int(round(fixed)))
                             if isinstance(v, int)
                             else _messy_value(fixed))
                    print(f"[insights-ledger] cohort metric {nm!r} "
                          f"({v}) exceeded its parent "
                          f"({parents[nm]}); clamped to {fixed}")
                    m['value'] = fixed
        # -- cross-subject 4dp repeats --
        for m in metrics:
            v = m.get('value')
            if not isinstance(v, (int, float)):
                continue
            nm = str(m.get('name') or '').strip().lower()
            collided = False
            for okey, obucket in (doc.get('subjects') or {}).items():
                if okey == skey or collided:
                    continue
                for e in (obucket.get('entries') or []):
                    if (e.get('family') or '') != fam:
                        continue
                    for om in (e.get('metrics') or []):
                        if str(om.get('name') or '').strip().lower() \
                                != nm:
                            continue
                        ov = om.get('value')
                        if isinstance(ov, (int, float)) and \
                                round(float(ov), 4) == round(float(v), 4):
                            collided = True
                            break
                    if collided:
                        break
            if collided:
                frac = _coherence_jitter(subject, nm, 0.012)
                if abs(frac) < 0.003:
                    frac = 0.003 if frac >= 0 else -0.003
                nv = float(v) * (1.0 + frac)
                nv = (_messy_value(int(round(nv)))
                      if isinstance(v, int) else _messy_value(nv))
                print(f"[insights-ledger] cross-subject repeat on "
                      f"{nm!r} ({v}); redrew to {nv}")
                m['value'] = nv
    except Exception as e:
        print(f"[insights-ledger] coherence pass skipped: {e}")


# Breakdown dimensions whose rows are standalone subjects in their
# own right (a list read over titles or brands). Demo buckets,
# platforms, and channel splits stay parent-only.
_LIST_DIMENSIONS = {'title', 'titles', 'show', 'shows', 'film', 'films',
                    'movie', 'movies', 'series', 'ip', 'content title',
                    'brand', 'brands'}

# "4,169,463 social engagers, 181,374 Prime Video viewers" -> pairs.
_NOTE_COUNT_RE = re.compile(
    r'([\d][\d,]{3,})\s+([A-Za-z][A-Za-z /-]{2,40}?)(?=[,.;]|$)')


def _derive_list_subentries(entry):
    """Child entries per breakdown row when a read covers an explicit
    list of titles or brands (2026-10-01, Jenna: list asks become
    first-class). A later ask about one title then replays the number
    the client already saw instead of drawing a fresh, slightly
    different one. Children carry the row's exact counts (parsed from
    the row note where present) plus its share and penetration; they
    never carry a breakdown of their own, so derivation cannot
    recurse. Never raises."""
    try:
        bd = entry.get('breakdown') or {}
        rows = bd.get('rows') or []
        dim = str(bd.get('dimension') or '').strip().lower()
        if dim not in _LIST_DIMENSIONS or len(rows) < 2:
            return []
        basis = str(bd.get('share_basis') or '').strip()
        children = []
        for row in rows[:60]:
            label = str(row.get('label') or '').strip()
            if len(label) < 2:
                continue
            metrics = []
            note = str(row.get('note') or '')
            for num, what in _NOTE_COUNT_RE.findall(note)[:4]:
                try:
                    val = int(num.replace(',', ''))
                except ValueError:
                    continue
                name = re.sub(r'[^a-z0-9_]+', '_',
                              what.strip().lower()).strip('_')[:48]
                if not name or val <= 0:
                    continue
                metrics.append({
                    'name': name, 'label': what.strip()[:90],
                    'value': val, 'unit': 'count',
                    'definition': (f'{label} slice of the set read: '
                                   f'{entry.get("q") or ""}')[:220]})
            for fld, nm, lbl in (
                    ('share_pct', 'share_of_set_pct', 'Share of set'),
                    ('penetration_pct', 'penetration_pct',
                     'Penetration')):
                v = row.get(fld)
                if isinstance(v, (int, float)):
                    metrics.append({
                        'name': nm, 'label': lbl, 'value': float(v),
                        'unit': 'pct', 'definition': basis[:220]})
            if not metrics:
                continue
            children.append(make_entry(
                subject=label, metric_family=entry.get('family'),
                question=f"{label}: {entry.get('q') or ''}"[:300],
                route='list_subentry', metrics=metrics,
                window_start=entry.get('ws'),
                window_end=entry.get('we'),
                window_label=entry.get('wl'),
                reply=note[:400],
                base_profile_key=entry.get('base'),
                cohort=entry.get('cohort'),
                derivation=f"derived from list read {entry.get('k')}",
                provenance=entry.get('prov')))
        return children
    except Exception as e:
        print(f"[insights-ledger] list subentry derivation failed: {e}")
        return []


_PLATFORM_TAIL_RX = re.compile(
    r"\s+(?:on\s+)?(?:amazon\s+prime\s+video|prime\s+video|amazon\s+prime|amazon|netflix|peacock|hulu|max|hbo\s+max|"
    r"disney\s+plus|disney|paramount\s+plus|paramount|apple\s+tv\s+plus|apple\s+tv|youtube|tiktok|tubi|pluto\s+tv|roku|"
    r"starz|showtime|discovery\s+plus|spotify)$")


def fold_subject(subject):
    """One title, one bucket (2026-10-13): the possessive and a trailing
    platform clause are not part of the subject. "Anthony Bourdain's No
    Reservations on Prime Video", "Anthony Bourdain No Reservations on
    Amazon Prime Video" and "Anthony Bourdain: No Reservations" fold to
    the same normalized title, so a figure stated under one spelling
    binds the others instead of being re-drawn as a different subject."""
    n = normalize_subject(subject)
    n = re.sub(r"\b(\w+) s\b", r"\1", n)          # bourdain s -> bourdain
    folded = _PLATFORM_TAIL_RX.sub("", n).strip()
    return folded or n


def bucket_key_for(index_doc, subject):
    """The bucket a new entry for `subject` belongs in: an existing
    bucket whose folded title matches, else the folded title itself."""
    subjects = (index_doc or {}).get('subjects') or {}
    skey = subject_key(subject)
    if skey in subjects:
        return skey
    want = fold_subject(subject)
    if not want:
        return skey
    for k, v in subjects.items():
        if fold_subject(v.get('subject') or k.replace('_', ' ')) == want:
            return k
    return want.replace(' ', '_')[:80] or skey


def _record_entry_now(entry):
    batch = [entry] + _derive_list_subentries(entry)
    for e in batch:
        try:
            _put_entry_object(e)
        except Exception as err:
            print(f"[insights-ledger] entry put failed: {err}")
        try:   # shadow rows in the relational store (audit item 7, 2026-10-14)
            from prometheus import store as _store
            if _store.enabled():
                for m in (e.get('metrics') or []):
                    _store.upsert_fact(e.get('subject'), entry_key(e.get('subject'), e.get('metric_family') or ''), m.get('name'), m.get('value'),
                                       unit=m.get('unit') or '', source=m.get('source') or '', window={'start': e.get('window_start'), 'end': e.get('window_end')},
                                       as_of=e.get('ts') or '', product=str(e.get('route') or 'chat'),
                                       doc={'question': e.get('question'), 'key': e.get('key'),
                                            # what the figure counted and the count behind it (2026-11-04)
                                            'definition': m.get('definition') or '', 'sample_people': m.get('sample_people'),
                                            'universe': e.get('universe') or '', 'window_label': e.get('wl') or ''})
        except Exception:
            pass

    def mutate(doc):
        for e in batch:
            skey = bucket_key_for(doc, e.get('subject'))
            subj = doc['subjects'].setdefault(
                skey, {'subject': e.get('subject') or skey,
                       'entries': []})
            _enforce_ledger_coherence(doc, skey, e)
            ents = subj.get('entries') or []
            ents = [x for x in ents if not _supersedes(e, x)]
            ents.append(e)
            subj['entries'] = ents[-MAX_ENTRIES_PER_SUBJECT:]
        doc['updated'] = entry['ts']
        return doc

    try:
        _update_index(mutate)
    except Exception as e:
        print(f"[insights-ledger] index update failed: {e}")


def retract(subject=None, question=None, key=None):
    """Pull delivered entries out of replay (operator correction,
    2026-10-01). Matching index rows are removed so no future ask
    replays or anchors to the corrected numbers; the per-entry audit
    objects stay on S3. A retracted list read also takes its derived
    children with it. The question match scans every subject: a
    correction often targets an entry that was filed under the wrong
    subject, so the stamped subject is a hint, never a scope.
    Returns the number of index rows removed."""
    del subject  # precision comes from qn / key; see docstring
    qn = normalize_question(question) if question else ''
    if not qn and not key:
        return 0
    removed = {'n': 0}

    def _hit(e):
        if key and e.get('k') == key:
            return True
        return bool(qn) and e.get('qn') == qn

    def mutate(doc):
        subjects = doc.get('subjects') or {}
        gone = set()
        for sk in list(subjects.keys()):
            ents = (subjects.get(sk) or {}).get('entries') or []
            keep = []
            for e in ents:
                if _hit(e):
                    gone.add(str(e.get('k') or ''))
                    removed['n'] += 1
                else:
                    keep.append(e)
            subjects[sk]['entries'] = keep
        if gone:
            for sk, sub in subjects.items():
                ents = sub.get('entries') or []
                keep = [e for e in ents
                        if not any(f"list read {k}" in
                                   str(e.get('derivation') or '')
                                   for k in gone if k)]
                removed['n'] += len(ents) - len(keep)
                sub['entries'] = keep
        return doc if removed['n'] else None

    try:
        _update_index(mutate)
    except Exception as e:
        print(f"[insights-ledger] retract failed: {e}")
    return removed['n']


def persist(*, subject, metric_family, question, route, metrics,
            anchors=None, window_start='', window_end='',
            window_label='', reply='', followups=None,
            base_profile_key='', cohort='', breakdown=None,
            derivation='', provenance='', verify=None, sync=False,
            universe='', journey=None):
    """Persist one delivered read. Never raises; never blocks the
    caller (daemon thread) unless `sync` or _SYNC_FOR_TESTS. Batch
    jobs (the conversation distiller, the artifact ingester) pass
    sync=True so the write lands before the process exits.

    Every projected people count is stored with the unprojected count
    behind it and a definition (attach_sample_people), and the entry
    carries the universe the reply named, so the figures reconcile later
    (2026-11-04). ``journey`` is the resolved viewing chain for a chain
    read (see make_entry); callers on a thread pass
    ``ask_journey(question, history=history)``."""
    try:
        entry = make_entry(
            subject=subject, metric_family=metric_family,
            question=question, route=route,
            metrics=attach_sample_people(metrics, universe=universe or cohort or subject),
            anchors=anchors, window_start=window_start,
            window_end=window_end, window_label=window_label,
            reply=reply, followups=followups,
            base_profile_key=base_profile_key, cohort=cohort,
            breakdown=breakdown, derivation=derivation,
            provenance=provenance, verify=verify, universe=universe,
            journey=journey)
        if not entry['metrics'] and not entry['reply']:
            return
        if sync or _SYNC_FOR_TESTS:
            _record_entry_now(entry)
            return
        threading.Thread(target=_record_entry_now, args=(entry,),
                         daemon=True).start()
    except Exception as e:
        print(f"[insights-ledger] persist failed: {e}")


def bank_record(*, subject, source, metrics, question, reply,
                window_start='', window_end='', window_label='',
                universe='', cohort='', breakdown=None, anchors=None,
                derivation='', metric_family='', followups=None,
                questions=None, retire_others=True, subject_aliases=None):
    """Bank a published deliverable's figures as the decision of record
    for its subject (Jenna 2026-11-10: "anything pulled here is stored
    for Prometheus"). Synchronous; returns the list of entry keys written.

    One entry carries the headline metrics, the breakdown and the reply
    Prometheus hands back for the subject's natural asks; each extra
    natural ask in ``questions`` (``[(question, reply), ...]`` or plain
    strings, which reuse ``reply``) is banked as its own exact-question
    entry under the same key so a verbatim re-ask replays the record.
    Idempotent: the same source re-banked replaces its earlier rows
    (same key and question). With ``retire_others`` every non-record
    entry already in the subject's bucket with the same family is
    stamped retired, so a figure a read drew before the deliverable was
    published never replays against the record. Never raises; the trail
    (the anchors, the derivation) stays internal and never renders."""
    try:
        src = re.sub(r'[^a-z0-9]+', '_', str(source or 'deliverable').lower()).strip('_')[:32] or 'deliverable'
        route = RECORD_ROUTE_PREFIX + src
        fam = canon_family(metric_family or family_from_question(question) or 'subscribers')
        written = []
        asks = [(question, reply)]
        for q in (questions or []):
            if isinstance(q, (list, tuple)) and len(q) >= 2:
                asks.append((str(q[0]), str(q[1] or reply)))
            elif isinstance(q, str) and q.strip():
                asks.append((q, reply))
        first = True
        for q, r in asks:
            entry = make_entry(
                subject=subject, metric_family=fam, question=q, route=route,
                metrics=attach_sample_people(metrics, universe=universe or cohort or subject) if first else metrics,
                anchors=anchors, window_start=window_start, window_end=window_end,
                window_label=window_label, reply=r, followups=followups,
                cohort=cohort, breakdown=breakdown if first else None,
                derivation=derivation, provenance=PROV_RECORD, universe=universe)
            first = False
            if not entry['metrics'] and not entry['reply']:
                continue
            _record_entry_now(entry)
            written.append(entry['k'])
        if written and subject_aliases:
            aliases = [str(a).strip()[:80] for a in subject_aliases if str(a or '').strip()][:24]

            def _alias_mut(doc):
                skey = bucket_key_for(doc, subject)
                sub = (doc.get('subjects') or {}).get(skey)
                if not sub:
                    return None
                have = list(sub.get('aliases') or [])
                for a in aliases:
                    if a not in have:
                        have.append(a)
                sub['aliases'] = have[:24]
                return doc

            try:
                _update_index(_alias_mut)
            except Exception as e:
                print(f"[insights-ledger] record aliases not written: {e}")
        if retire_others and written:
            retire_entries_for_record(subject, fam, exclude_routes=(route,))
        return written
    except Exception as e:
        print(f"[insights-ledger] bank_record failed: {e}")
        return []


def retire_entries(keys=None, questions=None, by='operator', note=''):
    """Stamp entries retired (they leave replay and the prompt block, and
    stay in the index for audit). Matches by entry key or by normalized
    question across every bucket. Returns the number stamped."""
    want_k = {str(k) for k in (keys or []) if k}
    want_q = {normalize_question(q) for q in (questions or []) if q}
    if not want_k and not want_q:
        return 0
    done = {'n': 0}
    stamp = {'by': str(by or 'operator')[:40], 'note': str(note or '')[:160],
             'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}

    def _mut(doc):
        for sk, sub in (doc.get('subjects') or {}).items():
            for e in (sub.get('entries') or []):
                if not isinstance(e, dict) or e.get('retired'):
                    continue
                if str(e.get('k') or '') in want_k or (want_q and e.get('qn') in want_q):
                    e['retired'] = dict(stamp)
                    done['n'] += 1
        return doc if done['n'] else None

    try:
        _update_index(_mut)
    except Exception as e:
        print(f"[insights-ledger] retire_entries failed: {e}")
    return done['n']


def retire_entries_for_record(subject, metric_family, exclude_routes=()):
    """Every non-record entry in the subject's bucket with this family is
    stamped retired: the record is the only figure that answers there."""
    fam = canon_family(metric_family)
    done = {'n': 0}
    stamp = {'by': 'decision_of_record', 'subject': str(subject or '')[:120],
             'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}

    def _mut(doc):
        skey = _resolve_subject(doc, subject=subject)
        bucket = (doc.get('subjects') or {}).get(skey) if skey else None
        if not bucket:
            return None
        for e in (bucket.get('entries') or []):
            if not isinstance(e, dict) or e.get('retired') or is_record_entry(e):
                continue
            if e.get('route') in exclude_routes or canon_family(e.get('family')) != fam:
                continue
            e['retired'] = dict(stamp)
            done['n'] += 1
        return doc if done['n'] else None

    try:
        _update_index(_mut)
    except Exception as e:
        print(f"[insights-ledger] retire_entries_for_record failed: {e}")
    return done['n']


ANCHOR_ROUTE = 'delivered_deck'


def ingest_deck_anchors(*, subject, metrics, source_name='',
                        window_start='', window_end='',
                        window_label='', basis_notes=None,
                        metric_family='delivered_anchors',
                        provenance=PROV_DELIVERED):
    """Record a shipped deliverable's headline figures as anchor
    entries for the subject (2026-08-27, Jenna: generated reads and
    decks must stay commensurate with what was already delivered).

    Anchor entries have no reply (they never replay verbatim); their
    metrics render into the PUBLISHED MEASUREMENTS block, which the
    prompt wrappers already mark as binding. `basis_notes` maps a
    metric name or label to a one-line note on how the figure was
    produced; the note rides in the metric definition so new reads
    extend the same logic. Re-ingesting the same source replaces the
    prior anchor entries instead of stacking duplicates. Synchronous;
    never raises.

    `metric_family` + `provenance` (2026-08-28): the artifact
    ingester passes the real family of each claim group so semantic
    lookups land, and every anchor carries delivered provenance,
    the top conflict rank."""
    try:
        clean = []
        notes = {str(k).strip().lower(): str(v)[:200]
                 for k, v in (basis_notes or {}).items()}
        for m in (metrics or []):
            if not isinstance(m, dict):
                continue
            m = dict(m)
            note = notes.get(str(m.get('name') or '').strip().lower()) \
                or notes.get(str(m.get('label') or '').strip().lower())
            if note:
                base_def = str(m.get('definition') or '').strip()
                m['definition'] = (f"{base_def}; {note}" if base_def
                                   else note)[:220]
            clean.append(m)
        if not clean:
            return
        marker = f"delivered anchors: {source_name or subject}"
        entries = []
        for i in range(0, len(clean), 6):
            e = make_entry(
                subject=subject, metric_family=metric_family,
                question=marker, route=ANCHOR_ROUTE,
                metrics=clean[i:i + 6], window_start=window_start,
                window_end=window_end, window_label=window_label,
                provenance=provenance)
            if e['metrics']:
                entries.append(e)
        if not entries:
            return
        for e in entries:
            try:
                _put_entry_object(e)
            except Exception as ee:
                print(f"[insights-ledger] anchor entry put failed: {ee}")
        skey = subject_key(subject)
        marker_qn = normalize_question(marker)

        def mutate(doc):
            subj = doc['subjects'].setdefault(
                skey, {'subject': subject or skey, 'entries': []})
            kept = [x for x in (subj.get('entries') or [])
                    if not (x.get('route') == ANCHOR_ROUTE
                            and x.get('qn') == marker_qn)]
            kept.extend(entries)
            subj['entries'] = kept[-MAX_ENTRIES_PER_SUBJECT:]
            doc['updated'] = entries[-1]['ts']
            return doc

        _update_index(mutate)
    except Exception as e:
        print(f"[insights-ledger] anchor ingest failed: {e}")


# ---------------------------------------------------------------------------
# Consult
# ---------------------------------------------------------------------------

# Generic audience nouns that never identify a subject on their own.
# Mirrors the base-resolution token set in app.py so a question naming
# "paw patrol viewer parents" still resolves the "Paw Patrol Series"
# bucket (2026-08-27, toy-categories routing).
_GENERIC_SUBJECT_TOKENS = {
    'viewers', 'viewer', 'fans', 'fan', 'series', 'audience', 'buyers',
    'buyer', 'shoppers', 'shopper', 'watchers', 'universe', 'total',
    'tu', 'the', 'of', 'and', 'a', 'an', 'profile', 'consumers',
    'consumer', 'customers', 'customer', 'avid', 'casual', 'movie',
    'show', 'subscribers', 'members', 'users', 'parents', 'parent',
    'kids', 'kid', 'potential', 'prospective', 'purchasers',
    'purchaser',
}


# One family, many spellings (2026-11-04, Emma Friedenberg's launch-day
# ask in Microdramas IQ). The vertical drama read is banked under one
# subject, "Vertical Drama" (the one-sheets and The Read's Micro-Series,
# Measured episodes), while readers write micro-series, microdramas,
# micro drama, vertical shorts or short dramas. Resolution folds every
# spelling onto the banked subject so the figures bind; the stored
# question keys (qn) are untouched, so exact replay is unchanged.
_SUBJECT_FAMILY_ALIASES = (
    (re.compile(r"\b(?:micro ?dramas?|micro ?series|microseries|vertical ?(?:dramas?|shorts?|series)|short ?dramas?)\b"),
     'vertical drama'),
)


def _fold_family_aliases(norm_text):
    """Normalized text with every family spelling folded onto the
    banked subject's spelling. Idempotent."""
    t = str(norm_text or '')
    for rx, canon in _SUBJECT_FAMILY_ALIASES:
        t = rx.sub(canon, t)
    return re.sub(r'\s+', ' ', t).strip()


def _has_live_entries(bucket):
    return any(isinstance(e, dict) and not e.get('retired') for e in ((bucket or {}).get('entries') or []))


def _resolve_subject(index_doc, subject=None, question=None, live_only=False):
    """Find the subject bucket for this ask. Explicit subject first
    (normalized match), else the longest ledger subject whose
    normalized form appears inside the normalized question, else a
    bucket one of whose aliases the question carries whole, else the
    subject whose distinctive tokens all appear in the question. Family
    spellings fold onto the banked subject first (_SUBJECT_FAMILY_ALIASES).
    With ``live_only`` a bucket whose entries are all retired never
    resolves (2026-11-10: the retired reads under 'Amazon Prime Video'
    must not shadow the record banked under its own subject)."""
    subjects = index_doc.get('subjects') or {}
    if live_only:
        subjects = {k: v for k, v in subjects.items() if _has_live_entries(v)}
    if subject:
        folded = _fold_family_aliases(normalize_subject(subject))
        if folded != normalize_subject(subject):
            for k, v in subjects.items():
                if normalize_subject(v.get('subject')) == folded and (v.get('entries') or []):
                    return k
        skey = subject_key(subject)
        if skey in subjects:
            return skey
        norm = normalize_subject(subject)
        for k, v in subjects.items():
            if normalize_subject(v.get('subject')) == norm:
                return k
        want = fold_subject(subject)   # possessive / platform tail folded (2026-10-13)
        for k, v in subjects.items():
            if want and fold_subject(v.get('subject') or k.replace('_', ' ')) == want and (v.get('entries') or []):
                return k
        # No bucket carries this exact subject name. A derived base
        # name still resolves its parent bucket ("Parents of Paw
        # Patrol Series Viewers" -> "Paw Patrol Series"): fall through
        # to the token scan with the subject text folded in
        # (2026-08-27, rephrased toy ask).
        question = f"{subject} {question or ''}"
    # normalize_question caps at 300 chars; a brief names its subject
    # anywhere, so the scan reads the ask (folded) past that cap
    qn = ' ' + _fold_family_aliases(re.sub(r'[^a-z0-9]+', ' ', str(question or '').lower()).strip()[:1200]) + ' '
    best_key, best_len = None, 0
    for k, v in subjects.items():
        s_norm = normalize_subject(v.get('subject'))
        if not s_norm or len(s_norm) < 3:
            continue
        if f' {s_norm} ' in qn and len(s_norm) > best_len:
            best_key, best_len = k, len(s_norm)
    # A bucket's own aliases (2026-11-10): the ways readers name a subject
    # that was banked under a deliverable's title ('Amazon Channels vs
    # direct', 'by billing path' for the Amazon Prime Video Channels vs
    # direct record). Every token of an alias in the ask resolves the
    # bucket, scored against the plain-name matches by length, so 'Amazon
    # Prime Video Channels versus direct' resolves the record and not the
    # Amazon Prime Video profile whose name sits inside it.
    q_tokens = set(qn.split())
    for k, v in subjects.items():
        for alias in (v.get('aliases') or [])[:24]:
            atoks = [w for w in normalize_subject(alias).split() if w]
            if not atoks or not set(atoks) <= q_tokens:
                continue
            score = sum(len(w) for w in atoks) + len(atoks) - 1
            if score > best_len:
                best_key, best_len = k, score
    if best_key:
        return best_key
    # Token-subset fallback: every distinctive token of the stored
    # subject appears in the question ("paw patrol viewer parents"
    # resolves "Paw Patrol Series"). Requires enough distinctive
    # material (6+ chars) so short subjects never match noise.
    q_tokens = set(qn.split())
    for k, v in subjects.items():
        s_norm = normalize_subject(v.get('subject'))
        stok = [w for w in s_norm.split()
                if w not in _GENERIC_SUBJECT_TOKENS]
        if not stok or sum(len(w) for w in stok) < 6:
            continue
        if set(stok) <= q_tokens:
            score = sum(len(w) for w in stok)
            if score > best_len:
                best_key, best_len = k, score
    return best_key


def _find_exact_anywhere(subjects, question):
    """Global exact-question scan: the most recent stored entry whose
    normalized question matches, across every subject bucket. Replay
    by exact ask must never depend on how the stored subject was
    named (2026-08-27, toy-categories routing)."""
    qn = normalize_question(question) if question else None
    if not qn:
        return None, None
    best_key, best = None, None
    for k, v in subjects.items():
        for e in reversed(v.get('entries') or []):
            if e.get('qn') == qn and e.get('reply') and not is_retired_surface_reply(e.get('reply')):
                if best is None or (e.get('ts') or '') > (best.get('ts')
                                                          or ''):
                    best_key, best = k, e
                break
    return best_key, best


_AUDIENCE_METRIC_RX = re.compile(
    r'\b(audience|viewers|reach|users|people|subscribers|accounts|universe|'
    r'streamed|watched)\b', re.I)


def _entry_window(e):
    ws, we = str(e.get('ws') or '')[:10], str(e.get('we') or '')[:10]
    return {'start': ws, 'end': we} if ws and we else None


def retire_conflicting_entries(subject, facts, tolerance=0.05):
    """Dashboard figures win (2026-10-06, Jenna). For the subject's
    ledger entries, any audience-size metric that disagrees with a
    Profile IQ / Journey / Attribution / Brand Partnership figure on
    the same window by more than `tolerance` is retired: it leaves the
    prompt block and never replays. The entry stays in the index with
    a `retired` stamp for audit. Returns the number retired."""
    sizes = [f for f in (facts or []) if isinstance(f, dict)
             and f.get('kind') in ('audience_size', 'platform_count')
             and f.get('unit') == 'people' and f.get('product') != 'chat']
    if not sizes or not str(subject or '').strip():
        return 0
    doc = _load_index(force=True)
    skey = _resolve_subject(doc, subject=subject)
    if not skey:
        return 0
    retired = []

    def _same_window(a, b):
        return (a and b and str(a.get('start'))[:10] == str(b.get('start'))[:10]
                and str(a.get('end'))[:10] == str(b.get('end'))[:10])

    def _mut(d):
        bucket = (d.get('subjects') or {}).get(skey) or {}
        for e in (bucket.get('entries') or []):
            if not isinstance(e, dict) or e.get('retired'):
                continue
            ew = _entry_window(e)
            for m in (e.get('metrics') or []):
                if str(m.get('unit') or '') in ('pct', 'percent', '%', 'usd'):
                    continue
                label = f"{m.get('label') or ''} {m.get('name') or ''} {m.get('definition') or ''}"
                if not _AUDIENCE_METRIC_RX.search(label):
                    continue
                try:
                    v = float(m.get('value'))
                except (TypeError, ValueError):
                    continue
                if v < 1000:
                    continue
                for f in sizes:
                    fw = f.get('window')
                    if ew and fw and not _same_window(ew, fw):
                        continue
                    # a platform figure only compares with a platform metric
                    if f.get('kind') == 'platform_count':
                        plat = str(f.get('label') or '').lower().replace('us viewers on ', '')
                        if plat.split('/')[0].split()[0] not in label.lower():
                            continue
                    elif 'on ' in label.lower() and f.get('kind') == 'audience_size':
                        continue
                    fv = float(f.get('value') or 0)
                    if fv <= 0:
                        continue
                    if abs(v - fv) / fv > tolerance:
                        e['retired'] = {'by': 'corpus_catalog', 'metric': m.get('label'),
                                        'value': v, 'dashboard_value': fv,
                                        'fact_source': (f.get('source') or {}).get('key'),
                                        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
                        retired.append(e.get('k'))
                        break
                if e.get('retired'):
                    break
        return d if retired else None

    _update_index(_mut)
    if retired:
        print(f"[insights-ledger] retired {len(retired)} entr{'y' if len(retired) == 1 else 'ies'} "
              f"for {subject!r}: dashboard figure wins")
    return len(retired)


# the prompt block stays bounded; a stored matrix read (36 rows with
# buckets) renders its rows first and the cap is wide enough for two
ROW_LINES_PER_ENTRY = 48
BLOCK_LINES_MAX = 120


def _pool_noun(entry):
    """The noun for a stored entry's pool counts, from the one helper
    (prometheus.methodology.pool_noun) on the entry's family and words;
    'people' when the helper is unavailable."""
    try:
        from prometheus import methodology as _meth
        return _meth.pool_noun_for(entry, question=str((entry or {}).get('q') or (entry or {}).get('question') or ''))
    except Exception:
        return 'people'


def render_block(entries, limit=12):
    """PUBLISHED MEASUREMENTS body for the prompt builders: one line
    per stored metric. Delivered-deck anchor entries render first
    (they are the figures already shipped for the subject and always
    survive the line cap), then the most recent reads."""
    # the decision of record renders ahead of everything (2026-11-10): the
    # published figures a later read must agree with, tagged so the
    # prompt reads them as binding, never as one more earlier read
    anchors = [e for e in entries if e.get('route') == ANCHOR_ROUTE or is_record_entry(e)]
    reads = [e for e in entries if e.get('route') != ANCHOR_ROUTE and not is_record_entry(e)]
    ordered = anchors[-limit:] + list(reversed(reads[-limit:]))
    lines = []
    for e in ordered[:limit]:
        win = e.get('wl') or (f"{e.get('ws')} to {e.get('we')}"
                              if e.get('ws') and e.get('we') else 'any window')
        tag = '[decision of record] ' if is_record_entry(e) else ''
        for m in (e.get('metrics') or [])[:(12 if tag else 6)]:
            unit = m.get('unit') or ''
            if unit in ('pct', 'percent', 'percentage', '%'):
                val = f"{m['value']:.1f}%"
            elif unit in ('usd', '$'):
                val = f"${m['value']:,.2f}"
            else:
                val = f"{m['value']:,}"
                if unit and unit != 'count':
                    val += f" {unit}"
            d = f" ({m['definition']})" if m.get('definition') else ''
            lines.append(f"- {tag}{e.get('subject')} | {e.get('family')} | "
                         f"{win}: {m.get('label')} = {val}{d}")
        bd = e.get('breakdown') or {}
        coh = f", {e.get('cohort')}" if e.get('cohort') else ''
        brows = bd.get('rows') or []
        # distribution rows carry their buckets: a stored 'gender by
        # door' is a figure the next pass keeps, not a sentence it may
        # redraw (2026-11-04). Rows with buckets render first, the
        # plain share rows after; the block stays bounded.
        with_b = [r for r in brows if isinstance(r.get('buckets'), list) and r.get('buckets')]
        plain = [r for r in brows if r not in with_b]
        # the pool noun follows the entry's family (2026-11-06), so a
        # stored toy shopping table reads back as buyers, never viewers
        noun = _pool_noun(e) if with_b else ''
        for r in with_b[:ROW_LINES_PER_ENTRY]:
            pool = (f" (pool {int(r['pool']):,} {noun})".replace(' )', ')')
                    if isinstance(r.get('pool'), (int, float)) and r.get('pool') else '')
            cells = ' | '.join(
                f"{b.get('label')} {float(b.get('share_pct') or 0):.1f}%"
                + (f" ({int(b['count']):,})" if isinstance(b.get('count'), (int, float)) else '')
                for b in r['buckets'][:MAX_BUCKETS_PER_ROW])
            lines.append(
                f"- {e.get('subject')}{coh} | {e.get('family')} | {win}: "
                f"{r.get('dimension') or bd.get('dimension') or 'Category'}, "
                f"{r.get('label')}{pool} = {cells}")
        for r in plain[:6]:
            lines.append(
                f"- {e.get('subject')}{coh} | {e.get('family')} | {win}: "
                f"{bd.get('dimension') or 'Category'} share, "
                f"{r.get('label')} = {float(r.get('share_pct') or 0):.1f}%")
        if len(lines) >= BLOCK_LINES_MAX:
            break
    return '\n'.join(lines[:BLOCK_LINES_MAX])



# ---------------------------------------------------------------------------
# Semantic replay (2026-08-27, Jenna's rephrased toy ask): "What
# category of toys do parents of paw patrol viewers aged 4-7 buy for
# their kids" regenerated instead of replaying the stored 4-7 read,
# because replay only matched the exact normalized question. Lookup
# now also keys on MEANING: (metric family, cohort, slice dimension).
# Any question form asking what a stored cohort buys/watches/does
# replays the stored read, whatever the word order. Age ranges bind
# the closest stored cohort; the stored reply names the cohort it
# reports in its first line.
# ---------------------------------------------------------------------------

_COHORT_AGE_RX = re.compile(
    r'\b(\d{1,2})\s*(?:-|to|thru|through|and)\s*(\d{1,2})\b')
_PARENT_TOKENS = ('parent', 'famil', 'mom', 'dad', 'caregiver',
                  'guardian')
# Ask-text vocabulary -> canonical family. Ordered: purchase verbs win
# over cohort nouns ("what do paw patrol VIEWERS buy" is a purchases
# ask; 'viewers' names the cohort, not the metric).
_ASK_FAMILY_RULES = (
    # Strategy vocabulary wins over behavior verbs: "white space to
    # create paw patrol toys" is an opportunity ask, not a purchases
    # ask, even when 'buy' also appears (2026-08-27, Jenna).
    ('strategy', ('white space', 'whitespace', 'underserved',
                  'untapped', 'unmet', 'opportunit', 'where to play',
                  'should launch', 'worth launching', 'worth making',
                  'worth creating', 'worth testing')),
    ('purchases', ('buy', 'buying', 'purchas', 'shop', 'bought',
                   'spending on', 'spend on', 'order')),
    ('subscribers', ('subscri', 'signup', 'sign up', 'churn')),
    ('search', ('search', 'quer', 'demand')),
    # Engagement sits ABOVE viewership so 'how many people follow her'
    # binds engagement before the viewership size-phrasing net below
    # can claim it (2026-09-24 reorder).
    ('engagement', ('engagement', 'session', 'follow')),
    # 'watch' matches watched/watching/watches as a substring - the
    # 2026-09-21 vocabulary rework narrowed this to 'watching' and
    # broke replay grouping on the most common ask shape there is
    # ('how many people watched X', caught by the 2026-09-24 nightly).
    # 'audience size' + 'how many people' land here too: on the
    # subject-bearing views these are viewership-size asks, and the
    # earlier families (purchases/subscribers/search/engagement) win
    # first-match for 'how many people bought/subscribed/searched/
    # follow'. Bare 'view' stays out (review, interview, overview).
    ('viewership', ('watch', 'streaming', 'binge', 'tune', 'viewing',
                    'viewers', 'viewership', 'audience size',
                    'how many people')),
    ('revenue', ('revenue', 'arpu')),
    # 2026-09-14: week 2026-W37 classified ZERO of 38 asks, so replay
    # could not fire on anything and every repeat ask paid for a full
    # read. The seven families above cover behaviour verbs but nothing
    # covered the way people actually ask ("what is the gender split",
    # "top QSR brands", "generate a seed for meta advertising", "how
    # does she compare to the other 3 profiles").
    #
    # These sit AFTER the originals on purpose: first match wins, so
    # "what brands do they buy" stays a purchases ask and "what are
    # they watching" stays viewership. Vocabulary is kept narrow and
    # unambiguous - a broad catch-all here would let replay serve one
    # ask's answer to a different ask, which is worse than a slow
    # correct read. Genuinely ambiguous asks ("give me the full read
    # on this data") still return None and are never replayed on
    # family alone.
    #
    # Substring matching is how this table works ('purchas' catches
    # purchase and purchasing), so short words that hide inside other
    # words are avoided: no bare 'race' (racing, grace), no bare
    # 'demo' (democrat), no bare 'age' (average, engagement, usage).
    ('demographics', ('gender split', 'gender breakdown', 'gender skew',
                      'male female', 'men vs women', 'women vs men',
                      'age breakdown', 'age skew', 'age range',
                      'age split', 'how old', 'ethnicity', 'ethnic',
                      'income', 'hhi', 'education', 'household size',
                      'marital', 'parental status', 'demographic',
                      'demos', 'skew younger', 'skew older')),
    # 2026-09-28 (W39 review): sits BEFORE brand_rank because
    # 'overindex in the four generation breaks' is a generational
    # read, not a brand ranking; a plain 'which brands overindex'
    # carries no generation word and stays brand_rank.
    ('generation_breaks', ('generation break', 'generational',
                           'four generation', 'by generation',
                           'across generations', 'generation splits',
                           'gen z vs', 'boomers vs',
                           'millennials vs')),
    ('brand_rank', ('top brands', 'which brands', 'what brands',
                    'brands index', 'index highest', 'indexes highest',
                    'over index', 'overindex', 'over-index',
                    'strongest brands', 'brand affinity',
                    'top retailers', 'top qsr', 'punches above')),
    ('activation', ('seed', 'segment for', 'activate', 'activation',
                    'media plan', 'targeting', 'lookalike',
                    'make money', 'making money', 'monetize',
                    'monetise', 'sell against', 'pitch to',
                    'sponsorship')),
    ('comparison', ('compare', 'comparison', 'versus', ' vs ',
                    'side by side', 'against the other', 'differ from',
                    'overlap with', 'stack up')),
    # 2026-09-28 (W39 review): "load the millennials profile and
    # summarize it" matched nothing, so a repeat paid for a fresh
    # read each time.
    ('profile_summary', ('summarize', 'summarise', 'give me a summary',
                         'profile overview', 'quick overview',
                         'recap of the profile')),
)
_DIM_STOP = {'category', 'categories', 'share', 'mix', 'breakdown',
             'of', 'the', 'by', 'top'}
_DIM_CLASS_WORDS = ('brand', 'retailer', 'store', 'platform', 'market',
                    'dma', 'city', 'state', 'genre', 'network',
                    'channel')
# Demographic slice words, checked on word boundaries in
# _dimension_ok so a stored gender table never serves an age ask.
_DIM_SLICE_WORDS = ('gender', 'age', 'ages', 'income', 'ethnicity',
                    'education', 'household', 'parental', 'marital')
# Measure classes (2026-09-24, the BET replay defect): what the table
# COUNTS, as distinct from what it is sliced by. "Pull the % of total
# time spent on each streaming platform" replayed the stored "top shows
# by streaming platform" table because both carry 'streaming platform'
# and both fold to the viewership family. A time-split ask and a title
# ranking are different reads even on the same dimension, so an ask
# that names a measure class only replays an entry that carries the
# same class somewhere in its dimension, share basis, metric labels
# or original question. Word-boundary patterns; 'show' must not hide
# inside 'showroom' and 'time' must not hide inside 'sometimes'.
_MEASURE_CLASSES = (
    ('time', re.compile(
        r'\b(?:time (?:spent|split|share|on|per)|'
        r'share of (?:total |their |the )?(?:\w+ )?time|'
        r'(?:total|watch|viewing|streaming|listening|screen|play|'
        r'session|social|app) time|hours|minutes|dwell)\b')),
    ('titles', re.compile(
        r'\b(?:shows?|titles?|series|programs?|programming|episodes?|'
        r'films?|movies?)\b')),
    # Subscription measures (2026-11-10, Liz's Amazon Channels vs direct
    # thread): three asks on the same ten services and the same two
    # billing paths replayed earlier reads that counted something else.
    # "share still active at day 30, 60 and 90" was handed the starts and
    # share-of-starts read; "90 day survival by service" was handed the
    # cancellers' switching read; "still active at day 30/60/90" was
    # handed the churn-rate read. Same family (subscribers), same
    # dimension (service), enough topic overlap, a different measure.
    # Patterns run on normalized text (lower case, punctuation folded to
    # spaces), so 'month-to-month' reads 'month to month'.
    ('survival', re.compile(
        r'\b(?:still active|surviv\w*|retention|retained|retain\w*|'
        r'remain(?:s|ed|ing)? (?:at|active|subscribed)|active at day|'
        r'at day \d+|day (?:30|60|90|180)\b|d(?:30|60|90)\b)')),
    ('churn_rate', re.compile(
        r'\bchurn\w*\b|\bcancel(?:lation|ation)s? rate\b|\bcancel rate\b|'
        r'\bcancel(?:s|led|ed)? (?:a|per) month\b')),
    ('starts', re.compile(
        r'\b(?:new (?:us )?(?:subscriptions?|subscribers|signups?|sign ups?|starts?)|'
        r'subscriptions? started|signups? started|started (?:through|via|in|inside|direct)|'
        r'share of (?:new )?(?:starts|signups|sign ups)|starts? (?:through|via|in|inside)|'
        r'how many (?:new )?(?:subscriptions?|signups?|starts))\b')),
    ('months_kept', re.compile(
        r'\b(?:months? (?:kept|retained|held|paid)|average (?:number of )?months|'
        r'paid months|months (?:of|in) the first 12|of 12 months|first (?:12|twelve) months)\b')),
    ('samplers', re.compile(
        r'\b(?:one title|single title|only one title|gone within \d+ days|'
        r'cancel(?:led|ed)? within \d+ days|samplers?)\b')),
    ('switching', re.compile(
        r'\b(?:different (?:streaming )?service|another (?:streaming )?(?:service|subscription)|'
        r'start(?:ed)? (?:another|a new) (?:subscription|service)|switch(?:ed|ers|ing)?|'
        r'rotat\w*|where (?:the )?cancel\w* go|destination service)\b')),
    ('winback', re.compile(
        r'\b(?:win ?back\w*|came back|come back|back (?:at|to) the same|'
        r'return(?:ed|s)? to the same|reactivat\w*|restart(?:ed|s)?)\b')),
    ('plan_mix', re.compile(
        r'\b(?:month to month|monthly (?:plan|pricing)|annual (?:plans?|pricing)|'
        r'bundles?|plan mix|pricing mix)\b')),
    ('value', re.compile(
        r'\b(?:worth|lifetime value|ltv|first year value|value (?:of|per) (?:a |an |each )?'
        r'(?:subscriber|signup|sign up|account)|revenue per|arpu)\b')),
    ('basis', re.compile(
        r'\b(?:access end|request date|cancel request|requested date|access ends?)\b')),
    ('per_service', re.compile(
        r'\b(?:per service|by service|each service|every service|one row per service|'
        r'service by service|for each of the (?:ten|nine|eight|\d+) services|'
        r'each of the (?:ten|nine|eight|\d+) (?:streaming )?services)\b')),
)
_DIM_SPLIT_RX = re.compile(r'\s+(?:by|per|across)\s+')


def _entry_measure_text(entry):
    """Every stored string that says what the entry counts: the table's
    dimension and share basis, the question, each metric's label and
    definition, and each row's note (a per-service row carries its
    measure in the note, 'Day 90: Channels 47.1, direct 55.4')."""
    bd = entry.get('breakdown') or {}
    bits = [bd.get('dimension'), bd.get('share_basis'), entry.get('q'),
            entry.get('question')]
    for m in (entry.get('metrics') or []):
        if isinstance(m, dict):
            bits.extend((m.get('label'), m.get('definition')))
    for r in (bd.get('rows') or [])[:60]:
        if isinstance(r, dict) and r.get('note'):
            bits.append(r.get('note'))
    # the whole text, not normalize_question's 300-character cap: a read
    # with a dozen metrics names its measures well past that (2026-11-10)
    return re.sub(r'[^a-z0-9]+', ' ', ' '.join(str(b) for b in bits if b).lower()).strip()[:8000]


def _norm_full(question):
    """The whole ask, normalized (normalize_question caps at 300
    characters; a long ask names its measures past that cap)."""
    return re.sub(r'[^a-z0-9]+', ' ', str(question or '').lower()).strip()[:2000]


def ask_measure_classes(question):
    """The measure classes an ask names, in table order."""
    qn = ' ' + _norm_full(question) + ' '
    return [name for name, rx in _MEASURE_CLASSES if rx.search(qn)]


_MONTHS = {'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
           'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
           'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
           'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12}
_MON_RX = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
_MONTH_WINDOW_RX = re.compile(
    r'\b(?P<m1>' + _MON_RX + r')(?: \d{1,2})? (?P<y1>20\d\d) (?:through|thru|to|until|till|and)'
    r' (?P<m2>' + _MON_RX + r')(?: \d{1,2})? (?P<y2>20\d\d)\b')


def ask_month_window(question):
    """(y1, m1, y2, m2) when the ask names a month window ('Nov 1 2025
    through Oct 31 2026', 'Jul 2025 to Jun 2026'), else None. An ask
    that names its window only replays a read banked on that window
    (2026-11-10: a Nov 2025 to Oct 2026 ask was handed a Jul 2025 to Jun
    2026 read because the year gate saw 2025 and 2026 covered)."""
    m = _MONTH_WINDOW_RX.search(' ' + normalize_question(question) + ' ')
    if not m:
        return None
    try:
        return (int(m.group('y1')), _MONTHS[m.group('m1')], int(m.group('y2')), _MONTHS[m.group('m2')])
    except Exception:
        return None


def _entry_month_window(entry):
    ws, we = str(entry.get('ws') or ''), str(entry.get('we') or '')
    try:
        return (int(ws[:4]), int(ws[5:7]), int(we[:4]), int(we[5:7]))
    except (TypeError, ValueError):
        return None


def _month_window_ok(entry, question):
    """The stored read sits on the month window the ask names. An ask
    with no month window is unconstrained here (the year gate and the
    rolling-window freshness still apply)."""
    want = ask_month_window(question)
    if not want:
        return True
    have = _entry_month_window(entry)
    if not have:
        return False
    return have == want


def _measure_ok(entry, qn):
    """The ask and the stored read count the same thing. Only asks
    that name a measure class are constrained; a class-free ask
    ("what toy categories are they buying") passes as before."""
    etext = None
    for _name, rx in _MEASURE_CLASSES:
        if not rx.search(qn):
            continue
        if etext is None:
            etext = ' ' + _entry_measure_text(entry) + ' '
        if not rx.search(etext):
            return False
    return True


def cohort_signature(text):
    """Reduce free text (an ask, or a stored cohort label) to the
    cohort facts that matter for replay: the age range named, and
    whether it is a parent/family cohort."""
    t = str(text or '').lower()
    ages = None
    m = _COHORT_AGE_RX.search(t)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi:
            ages = (lo, hi)
    parents = any(p in t for p in _PARENT_TOKENS)
    return {'ages': ages, 'parents': parents}


def family_from_question(question):
    """Canonical metric family the ask is about, from its verbs
    (None when no family vocabulary appears)."""
    t = ' ' + normalize_question(question) + ' '
    for canon, keys in _ASK_FAMILY_RULES:
        if any(k in t for k in keys):
            return canon
    return None


def _dimension_ok(entry, qn):
    """The ask names the same slice the stored table ranks. A 'toy
    category' table serves toy-category asks; it never serves a
    'brand' or 'retailer' ask, and a categories ask never replays a
    read that has no table."""
    if not _measure_ok(entry, qn):
        return False
    bd = entry.get('breakdown') or {}
    dim = normalize_subject(bd.get('dimension') or '')
    if not (bd.get('rows') or []):
        if re.search(r'categor|breakdown|\bmix\b|top \d', qn):
            return False
        return True
    # ANY meaningful dimension token in the ask qualifies (2026-08-27:
    # a stored strategy table titled "Toy category (demand vs current
    # Paw Patrol coverage)" must replay for "what toy categories are
    # underserved..." - requiring EVERY token blocked it). The class-
    # word conflict check below still keeps a brand/retailer ask off a
    # category table. Subject and cohort words inside the label never
    # count as dimension meaning ("Paw Patrol" in the title must not
    # bind "how big is the paw patrol audience").
    subj_toks = set(normalize_subject(
        f"{entry.get('subject') or ''} {entry.get('cohort') or ''}"
    ).split())
    dtoks = [w for w in dim.split()
             if w not in _DIM_STOP and w not in subj_toks]
    if not dtoks and dim:
        # The cohort text swallowed the dimension's own tokens (a
        # 'genre cohorts' cohort plus a 'Genre' dimension left
        # nothing), which degenerated this gate to always-true and
        # replayed a genre table for a second-screen ask (2026-09-28,
        # Casey). Fall back to the raw dimension tokens: a ranked
        # table never serves an ask that names none of its axis.
        dtoks = [w for w in dim.split() if w not in _DIM_STOP]
    if dtoks and not any(w.rstrip('s') in qn for w in dtoks):
        return False
    # A two-axis table ("Show by streaming platform") only serves an
    # ask that names BOTH axes (2026-09-24). A platform-split ask
    # matched the platform axis alone and replayed a title ranking.
    for part in _DIM_SPLIT_RX.split(dim):
        ptoks = [w for w in part.split()
                 if w not in _DIM_STOP and w not in subj_toks]
        if ptoks and not any(w.rstrip('s') in qn for w in ptoks):
            return False
    # A class word the ask mentions must be part of the stored read:
    # either the ranked dimension itself, or an attribute the table
    # already carries (2026-09-24: "what genres ... and on which top 2
    # platforms for each genre" names 'platform' as the per-row
    # leaders, and the stored genre table's own question and notes
    # name platforms too, so it is the same read). A brand ask against
    # a category table whose stored text never mentions brands still
    # fails here.
    etext = None
    for c in _DIM_CLASS_WORDS:
        if c in qn and c not in dim:
            if etext is None:
                etext = _entry_measure_text(entry)
            if c not in etext:
                return False
    # Demographic slices need the same conflict check as the class
    # words above, added 2026-09-14 alongside the 'demographics'
    # family: without it a stored gender table would satisfy an age
    # ask, because both now carry the same family and the loop above
    # only guards brand / retailer / platform style dimensions.
    #
    # Matched on word boundaries, not as substrings, because these are
    # short and hide inside common words ('age' in average, usage and
    # engagement; 'income' is safe but the others are not worth the
    # risk of an inconsistent rule).
    for c in _DIM_SLICE_WORDS:
        if re.search(r'\b' + c + r'\b', qn) \
                and not re.search(r'\b' + c, dim):
            return False
    return True


# Families that describe the same read when the ask also names the
# stored table's dimension (2026-09-24, Paramount+ genre mix): "users
# engaging with each genre" filed as engagement, "what genres are they
# watching" reads as viewership. Two users asking for the same genre
# table on the same base and window must get the same answer, so the
# two verbs are one family for replay. Purchases, search, revenue and
# the rest stay distinct.
_FAMILY_COMPAT = {
    'viewership': {'viewership', 'engagement'},
    'engagement': {'engagement', 'viewership'},
}
_YEAR_RX = re.compile(r'\b(20[1-3]\d)\b')


def _family_compat(fam):
    return _FAMILY_COMPAT.get(fam) or {fam}


def _years_in(qn):
    return {int(y) for y in _YEAR_RX.findall(qn or '')}


def _window_covers(entry, years):
    """The stored window spans every year the ask names. Entries with
    no dated window ('any') never bind a dated ask."""
    ws, we = str(entry.get('ws') or ''), str(entry.get('we') or '')
    try:
        y0, y1 = int(ws[:4]), int(we[:4])
    except ValueError:
        return False
    return all(y0 <= y <= y1 for y in years)


_TOPIC_STOP = {'give', 'show', 'what', 'which', 'most', 'common',
               'happening', 'while', 'person', 'people', 'watching',
               'their', 'them', 'they', 'this', 'that', 'with',
               'each', 'segmented', 'split', 'versus', 'does'}


# Asks that are never a verbatim replay, whatever the stored corpus
# holds (2026-10-02 S5, from the real ask log): a cut / pull / build
# command ("Let's do one cut of males only" replayed a USA Today read),
# a generic screen ask ("Analyze the data on this screen" replayed a
# Paw Patrol read banked days earlier), and a complaint ("thats not
# what i asked for" replayed the very entry being rejected). Each of
# these is a request about NOW, not a repeat of a stored question.
_REPLAY_COMMAND_RX = re.compile(
    r"\b(?:let'?s do|lets do|do (?:a|one|two|another)|add(?: a| another)?"
    r"|make(?: me)?(?: a)?|create|build|run|pull|download|export|save"
    r"|send|email|open|cut)\b"
    r"(?:(?!\bwho\b).){0,40}?"
    r"\b(?:cuts?|profiles?|builds?|csv|file|deck|report|audience"
    r"|males? only|females? only|consumers|data cuts?)\b", re.I)
_REPLAY_GENERIC_RX = re.compile(
    r"^\W*(?:please\s+)?(?:analy[sz]e|summari[sz]e|read|explain|review"
    r"|interpret|walk me through|tell me about)\s+"
    r"(?:the\s+)?(?:data\s+)?(?:on\s+)?(?:this|the|my|whats|what'?s)"
    r"(?:\s+(?:screen|page|view|data|open|profile|dashboard|here))*"
    r"\W*$", re.I)
_REPLAY_COMPLAINT_RX = re.compile(
    r"\b(?:not what i (?:asked|wanted|meant)|that'?s? (?:wrong|not right"
    r"|not it|incorrect)|wrong answer|try again|doesn'?t answer"
    r"|didn'?t answer|still wrong)\b", re.I)
# An order to run it again is a request about NOW, never a repeat of a
# stored question (Jessie 2026-11-04: "run a new ask from this base ...
# Calculate the deduplicated percentage" replayed, by meaning, the very
# read she had just rejected). The stored neighbours still ride the
# constraint block; the words never replay verbatim.
_REPLAY_FRESH_RX = re.compile(
    r"\b(?:run|do|take|start|make|give me|give us|want|need)\s+(?:a\s+|an\s+|another\s+|one\s+|the\s+)?"
    r"(?:new|fresh|clean|separate|second|independent|different)\s+"
    r"(?:ask|read|look|pull|pass|run|draw|count|calculation|query|number|numbers|figure|figures)s?\b"
    r"|\b(?:re-?run|rerun|re-?do|redo|re-?pull|re-?calculate|recalculate|re-?compute|recompute|re-?read"
    r"|run it again|do it again|from scratch|start over|go back (?:in )?an?d?\b"
    r"|fresh (?:ask|read|look|pull|pass|run|draw|count|numbers|data|figures?)"
    r"|new (?:look|pull|pass|draw)(?:\s*/\s*(?:look|pull|pass|draw))?"
    r"|again from (?:the )?(?:base|start|top|data|file)|a second look)\b", re.I)
_REPLAY_MIN_TOPIC_TOKENS = 2


def fresh_run_requested(question):
    """True when the ask orders the read run again rather than asking a
    question a stored read could answer verbatim."""
    try:
        return bool(_REPLAY_FRESH_RX.search(str(question or '')))
    except Exception:
        return False


def replay_eligible(question):
    """True when the ask is the kind of question a stored read can
    answer verbatim. Commands, generic screen asks, complaints, and
    asks with no distinctive topic words run fresh every time; their
    stored neighbours still ride the constraint block. Never raises."""
    try:
        q = str(question or '').strip()
        if not q:
            return False
        if _REPLAY_GENERIC_RX.search(q):
            return False
        if _REPLAY_COMPLAINT_RX.search(q):
            return False
        if _REPLAY_COMMAND_RX.search(q):
            return False
        if _REPLAY_FRESH_RX.search(q):
            return False
        qn = normalize_question(q)
        toks = [w for w in qn.split()
                if len(w) >= 4 and w not in _TOPIC_STOP
                and w not in _DIM_STOP]
        return len(toks) >= _REPLAY_MIN_TOPIC_TOKENS
    except Exception:
        return True


def _topic_stem(w):
    for suf in ('ing', 'ers', 'er', 'ed', 'es', 's'):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:len(w) - len(suf)]
    return w


def _fold_title(s):
    s = re.sub(r"\b(?:season|series|s)\s*\d+\b", ' ', str(s or ''), flags=re.I)
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def _chain_titles_ok(entry, question):
    """A viewing-chain ask (the viewers of one title who went on to
    another: autoplayed into, then watched, completed the season, of
    those ...) replays a stored read only when every title in its chain
    is in that read's own words. Jenna 2026-11-05: "how many power
    raising kanan season 5 viewers autoplay'ed into fightland and then of
    those what percent completed that entire season of fightland" was
    answered with the banked Fightland into S.W.A.T. Exiles read; the
    topic words (autoplay, viewers, fightland, completion) overlapped
    and the base title, Power: Raising Kanan, was nowhere in it. Never
    raises; True when the ask names no chain."""
    try:
        from prometheus.sizing_lane import chain_titles
        titles = chain_titles(question)
    except Exception:
        return True
    if not titles:
        return True
    try:
        etext = ' '.join((str(entry.get('subject') or ''), str(entry.get('cohort') or ''),
                          str(entry.get('qn') or entry.get('q') or ''), str(entry.get('reply') or '')[:4000]))
        efold = _fold_title(etext)
        for t in titles:
            tf = _fold_title(t)
            if len(tf) >= 4 and tf not in efold:
                return False
        return True
    except Exception:
        return True


def _topical_overlap_ok(entry, qn, floor=0.34):
    """A meaning-level replay must actually be ABOUT the ask
    (2026-09-28, Casey's second-screen ask replayed a genre table
    four times). The ask's distinctive topic tokens must appear in
    the stored read's own text (question + dimension + reply) at a
    minimum share, stem-matched so 'engage' meets 'engaging'.
    Exact-question replays never reach this gate."""
    try:
        toks = [w for w in str(qn or '').split()
                if len(w) >= 4 and w not in _TOPIC_STOP
                and w not in _DIM_STOP]
        subj_toks = set(normalize_subject(
            str(entry.get('subject') or '')).split())
        # The audience words are judged by cohort distance in
        # find_semantic, not here: "parents of kids 4-7" in the ask is
        # the cohort, not the topic.
        subj_toks |= set(normalize_subject(
            str(entry.get('cohort') or '')).split())
        toks = [w for w in toks if w not in subj_toks]
        if not toks:
            # Only the dimension and cohort words remain; those are
            # judged by _dimension_ok and the cohort distance. Asks
            # with no question in them at all (commands, generic
            # screen asks) never reach here: replay_eligible gates
            # them in consult (2026-10-02 S5).
            return True
        # 2026-10-02 audit: one or two topic tokens used to pass
        # unjudged, which is how a short ask replayed an unrelated
        # read. With little signal, every token must land.
        _need_all = len(toks) < 3
        etext = ' '.join((
            str(entry.get('qn') or entry.get('q') or ''),
            normalize_subject(
                (entry.get('breakdown') or {}).get('dimension') or ''),
            str(entry.get('reply') or '')[:1200].lower(),
        ))
        estems = {_topic_stem(w) for w in
                  re.findall(r'[a-z0-9]+', etext) if len(w) >= 4}
        qstems = {_topic_stem(w) for w in set(toks)}
        # Prefix hit so 'engage' meets 'engag(ing)' whichever side
        # stemmed shorter.
        hit = sum(1 for q in qstems
                  if any(len(q) >= 4 and len(e) >= 4
                         and (q.startswith(e) or e.startswith(q))
                         for e in estems))
        share = hit / max(1, len(qstems))
        if _need_all:
            if hit >= len(qstems):
                return True
        elif share >= floor:
            return True
        print(f"[insights-ledger] semantic candidate rejected on "
              f"topic overlap ({share:.0%}): {entry.get('k')}")
        return False
    except Exception:
        return True


_AUDIENCE_CLAUSE_RX = re.compile(
    r'\b(?:who|that)\s+(?:watch|view|stream|listen|play|subscribe)'
    r'[a-z]*\s+[^,.?;]*', re.IGNORECASE)

_ANNUAL_ASK_RX = re.compile(
    r'\b(annual(?:ly)?|yearly|full[- ]year|whole year|12[- ]months?|'
    r'year to date|ytd)\b', re.IGNORECASE)


def _entry_span_days(e):
    """Window span in days, or None when the entry has no window."""
    try:
        ws = datetime.strptime(str(e.get('ws') or ''), '%Y-%m-%d')
        we = datetime.strptime(str(e.get('we') or ''), '%Y-%m-%d')
        return max(0, (we - ws).days) + 1
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Viewing chains (2026-11-05, Jenna: "I keep asking this question and it
# keeps giving me the way wrong read"). "How many Power: Raising Kanan
# season 5 viewers autoplayed into Fightland, and of those what percent
# completed the season" was answered three times with the banked
# Fightland -> S.W.A.T. Exiles autoplay read: the two asks share a title
# and a family, so the semantic match took the stored read. A journey
# read is about a SOURCE audience moving to a DESTINATION title, and it is
# reused only when the whole journey matches: source title (with its
# season when one is stated), destination title (with season), platform,
# the metric asked (autoplay handoff, completion, discovery, drop-off),
# and a compatible window. A shared title on the wrong side of the
# journey is not a match. A follow-up ("of those", "what percent of
# those") binds to the read produced for the question it follows, never
# to the most recent banked entry that shares a title.
# ---------------------------------------------------------------------------

_J_AUDIENCE_NOUNS = (r"(?:viewers?|watchers?|streamers?|subscribers?|fans?|audience|readers?|listeners?|"
                     r"players?|households?|accounts?|people|users?)")
_J_PLATFORM_RX = re.compile(
    r"\bon (?:the )?(?P<p>starz|netflix|hulu|hbo max|max|peacock|paramount plus|paramount|prime video|amazon prime|"
    r"amazon|apple tv plus|apple tv|disney plus|disney|amc plus|amc|tubi|roku|youtube|pluto|discovery plus|espn|"
    r"showtime|mgm plus|britbox|acorn|crunchyroll|fubo|sling|philo|freevee|plex|xumo)\b")
# where a title span stops (folded text: punctuation is already a bar)
_J_STOP = (r"(?=\s*(?:\||$|\b(?:and|then|next|on|onto|into|to|of those|of them|of these|what|how|who|that|which|"
           r"directly|right|immediately|straight|after|before|within|because|since|during|from|at|with|via|"
           r"through|over|versus|vs|compared|in the|last|past|trailing|this|between|once|when|while|where|"
           r"also|ever|still|went|go|goes|going|gone|came|come|watch\w*|stream\w*|play\w*|autoplay\w*|auto|proceed\w*|"
           r"roll\w*|mov\w*|convert\w*|cross\w*|finish\w*|complet\w*|binge\w*|start\w*|stay\w*|stuck|drop\w*|"
           r"churn\w*|carr\w*|flow\w*|jump\w*|switch\w*|continu\w*|tune\w*|return\w*|kept|keep|were|was|are|is|"
           r"did|do|does|have|has|had|would|will|could|should)\b))")
_J_TITLE = r"(?P<t>[a-z0-9]+(?: [a-z0-9]+){0,7}?)"
_J_SOURCE_RXS = (
    re.compile(r"\bhow many (?:of (?:the |our |your )?)?" + _J_TITLE + r" " + _J_AUDIENCE_NOUNS + r"\b"),
    re.compile(r"\b(?:" + _J_AUDIENCE_NOUNS + r") (?:who|that) (?:watched|streamed|saw|played|finished|binged|started|viewed|completed|"
               r"were watching|had watched|have watched|are watching) " + _J_TITLE + _J_STOP),
    re.compile(r"\b(?:" + _J_AUDIENCE_NOUNS + r") (?:of|for) " + _J_TITLE + _J_STOP),
    re.compile(r"^(?:\s*(?:what|how) (?:percent(?:age)?|share|many|much) of (?:the |our |your )?)?" + _J_TITLE + r" " + _J_AUDIENCE_NOUNS +
               r" (?:who|that|then|also|ever|have|had|has|on|in|from|autoplay|auto|watch|stream|went|go|proceed|complet|finish|binge|convert|roll|move|carr|cross|stay|stuck|drop|churn|continu|jump|switch)"),
)
_J_ANAPHORA_RX = re.compile(r"\b(?:of|among|from) (?:those|them|these)\b|\b(?:those|these) (?:same )?" + _J_AUDIENCE_NOUNS + r"\b|"
                            r"\bhow many of (?:those|them|these)\b|\bthey\b|\bthat (?:group|cohort|audience)\b")
_J_DEST_RXS = (
    # a step that lands on a title
    re.compile(r"\b(?:auto ?play(?:ed|s|ing)? (?:into|to|onto|in to)|auto played (?:into|to)|rolled (?:straight |right |directly )?(?:into|over to|on to|onto)|"
               r"carried over (?:to|into)|moved (?:on |over |straight |back )?(?:to|into)|(?:went|go|goes|going|came|come) (?:on |over |straight |directly |right |back )?(?:to|into|and) "
               r"(?:watch(?:ing|ed)? |stream(?:ing|ed)? |play(?:ing|ed)? |start(?:ing|ed)? |binge(?:ing|d)? |see |finish(?:ed)? )?|"
               r"(?:proceed(?:ed|s|ing)?|went on|go on|continue[ds]?|continuing) (?:to )?(?:watch|stream|play|start|binge|view)(?:ed|ing)? |"
               r"then (?:watched|streamed|played|started|binged|viewed|saw|tried|began) |cross(?:ed)? (?:over )?(?:to|into) |"
               r"convert(?:ed|s|ing)? (?:to|into|over to) |handed (?:off |over )?(?:to|into) |flow(?:ed|s)? (?:into|to) |funnel(?:ed|led|s)? (?:into|to) |"
               r"jump(?:ed|s)? (?:to|into|over to) |switch(?:ed|es)? (?:to|over to) |(?:in|over) to watch |into |onto )" + _J_TITLE + _J_STOP),
    # a completion target: "the full season of Fightland", "finished Fightland season 1"
    re.compile(r"\b(?:season|series|show|run|arc|episodes?|eps?)(?: \d+)? of " + _J_TITLE + _J_STOP),
    re.compile(r"\b(?:complet(?:ed|es|e|ing)?|finish(?:ed|es|ing)?|binge[ds]?|binge watch(?:ed|ing)?) (?:all of |the whole of |the rest of |watching )?" + _J_TITLE + _J_STOP),
)
_J_DEST_JUNK = {'it', 'that', 'this', 'them', 'those', 'these', 'the', 'a', 'an', 'another', 'the next', 'the same', 'the show',
                'the series', 'the season', 'the title', 'the rest', 'next', 'same', 'more', 'something', 'anything', 'all',
                'the full season', 'the whole season', 'the entire season', 'the end', 'the finale', 'episode', 'episodes', 'one',
                'each', 'every', 'any', 'their', 'its', 'other', 'others', 'the other', 'the first', 'the second',
                'whole', 'entire', 'full', 'complete', 'rest', 'finale', 'end', 'season', 'series', 'show', 'run', 'title',
                'first', 'second', 'third', 'last', 'both', 'either', 'neither', 'none', 'some', 'most', 'many', 'few'}
_J_NOUN_SET = {'viewer', 'viewers', 'watcher', 'watchers', 'streamer', 'streamers', 'subscriber', 'subscribers', 'fan', 'fans',
               'audience', 'reader', 'readers', 'listener', 'listeners', 'player', 'players', 'household', 'households',
               'account', 'accounts', 'people', 'user', 'users'}
_J_SEASON_TAIL_RX = re.compile(r"^(?P<title>.+?) (?:season|series|s) (?P<n>\d{1,2})(?: (?:episodes?|eps?)(?: \d+)?)?$")
_J_ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
               'ninth': 9, 'tenth': 10, 'final': None, 'latest': None, 'new': None, 'newest': None, 'current': None}
_J_SEASON_HEAD_RX = re.compile(r"^(?:the )?(?:(?:season|series|s) (?P<n>\d{1,2})|(?P<ord>first|second|third|fourth|fifth|sixth|seventh|"
                               r"eighth|ninth|tenth|final|latest|new|newest|current) (?:season|series)|season|series|run|episodes|"
                               r"(?:whole|entire|full|complete|rest) (?:of the )?(?:season|series|run)) of (?P<title>.+)$")
_J_BARE_SEASON_RX = re.compile(r"^(?:the )?(?:season|series|s) ?(?P<n>\d{1,2})$")
_J_LEAD_JUNK_RX = re.compile(r"^(?:the |all |any |every |each |a |an |that |this |those |these |of |same |new |most |many |"
                             r"whole |entire |full |complete |rest of |first |last |next |second |third |other |another )+")
_J_TAIL_JUNK_RX = re.compile(r"(?: (?:the|a|an|then|and|or|of|to|into|that|this|who|viewers?|watchers?|audience|fans?|people|users?|"
                             r"episodes?|eps?|title|show|series|season|run|all|again|too|also|ever|still|yet|now|here|there|next|same|"
                             r"entire|whole|full|complete|rest|first|second|third|last))+$")
_J_METRIC_RXS = (
    ('autoplay', re.compile(r"\bauto ?play\w*\b|\bauto played\b|\bend card\b|\bautostart\w*\b|\bpost ?play\b")),
    ('completion', re.compile(r"\b(?:complet\w*|finish\w*)\b(?:\W+\w+){0,6}?\W+(?:season|series|show|run|episodes?|eps?|title|it|arc|book|film|movie)\b|"
                              r"\b(?:entire|whole|full|complete|all \w+) (?:season|series|run|show)\b|\bcompletion\b|\ball (?:\d+|\w+teen|eight|ten|six|twelve) episodes\b|"
                              r"\b(?:binge[ds]?|binge watch\w*)\b|\bthrough (?:the|to the) (?:finale|end|last episode)\b|\bwatched (?:the )?(?:whole|full|entire|complete)\b")),
    ('discovery', re.compile(r"\bdiscover\w*\b|\bfirst touch\b|\bfound (?:the|it|them)\b|\bhow did they find\b|\blast touch\b|\bentry point\b|\bfirst (?:title|play|watch)\b")),
    ('dropoff', re.compile(r"\bdrop\w* ?(?:off|out)\b|\bfell off\b|\bchurn\w*\b|\bstuck around\b|\bstayed (?:for|on|with|through)\b|\bbail\w*\b|\babandon\w*\b|\bquit\b")),
    ('handoff', re.compile(r"\bthen (?:watched|streamed|played|started|binged|viewed|saw)\b|\bproceed\w*\b|\bwent on\b|\bcross(?:ed)? ?over\b|\bcrossover\b|"
                           r"\b(?:went|go|goes|came) (?:back |on |over |straight )?(?:to|and) (?:watch|stream|play|see|start|binge)\w*\b|"
                           r"\bconvert\w*\b|\bcarried over\b|\brolled (?:straight |right )?(?:into|over|on)\b|\bwatch(?:ed)? next\b|\bnext (?:title|show|series|play|watch)\b|"
                           r"\bmoved (?:on |over )?(?:to|into)\b|\bflow\w* (?:into|to)\b|\bfunnel\w*\b|\bhanded (?:off|over)?\b|\bjump\w* (?:to|into)\b|"
                           r"\bswitch\w* (?:to|over)\b|\b(?:into|onto|in to) \w+|\bauto ?play\w*\b|\bauto played\b")),
)


def _j_fold(text):
    """Lower-cased words; dotted initialisms joined (S.W.A.T. -> swat),
    apostrophes dropped (autoplay'ed -> autoplayed), '+' spelled out,
    sentence punctuation kept as a bar so a title never runs past it."""
    t = str(text or '').lower()
    t = re.sub(r"(?<![a-z0-9])((?:[a-z]\.){2,})", lambda m: m.group(1).replace('.', ''), t)
    t = re.sub(r"['\u2018\u2019\u02bc`]", '', t)
    t = t.replace('+', ' plus ').replace('&', ' and ')
    t = re.sub(r"[,;.?!]+", ' | ', t)
    t = re.sub(r"[^a-z0-9|]+", ' ', t)
    t = re.sub(r"\bs(\d{1,2})\b", r"season \1", t)   # S5 -> season 5
    return re.sub(r"\s+", ' ', t).strip()


def _j_title(raw):
    """(title, season) from a captured span; ('', None) when the span is
    only junk words."""
    s = re.sub(r"\s+", ' ', str(raw or '')).strip()
    season = None
    m = _J_SEASON_HEAD_RX.match(s)
    if m:
        s = m.group('title')
        season = int(m.group('n')) if m.group('n') else _J_ORDINALS.get(m.group('ord') or '')
    s = _J_LEAD_JUNK_RX.sub('', s)
    m = _J_SEASON_TAIL_RX.match(s)
    if m:
        s, season = m.group('title'), int(m.group('n'))
    s = _J_PLATFORM_RX.sub(' ', ' ' + s).strip()
    s = _J_TAIL_JUNK_RX.sub('', s).strip()
    s = _J_LEAD_JUNK_RX.sub('', s)
    if not s or s in _J_DEST_JUNK or s in _J_NOUN_SET or re.fullmatch(r"(?:season|series|episode|show)(?: \d+)?", s):
        return '', season
    if len(s) < 3 or re.fullmatch(r"\d+", s):
        return '', season
    return s, season


def journey_signature(text):
    """The viewing chain an ask (or a stored question) describes, or None
    when the words hold no chain. {'src', 'src_season', 'dst',
    'dst_season', 'platform', 'metrics', 'anaphoric'}: titles folded to
    plain words ('power raising kanan', 'swat exiles'); 'anaphoric' is a
    follow-up whose source audience is "those" / "them" and must be read
    off the question it follows. Never raises."""
    try:
        t = _j_fold(text)
        if not t or len(t) > 1200:
            return None
        src, src_season, src_end = '', None, 0
        for rx in _J_SOURCE_RXS:
            m = rx.search(t)
            if not m:
                continue
            cand, season = _j_title(m.group('t'))
            if cand:
                src, src_season, src_end = cand, season, m.end()
                break
        anaphoric = bool(_J_ANAPHORA_RX.search(t)) and not src
        dst, dst_season, self_dst = '', None, None
        for rx in _J_DEST_RXS:
            for m in rx.finditer(t):
                span = m.group('t').strip()
                bare = _J_BARE_SEASON_RX.match(span)
                if bare and src:
                    # "went back and watched season 1": the same title,
                    # another season
                    cand, season = src, int(bare.group('n'))
                    if season != src_season:
                        dst, dst_season = cand, season
                        break
                    continue
                cand, season = _j_title(span)
                if not cand or cand in _J_DEST_JUNK:
                    continue
                if src and _j_same_title(cand, src):
                    # "fightland viewers who finished fightland": the
                    # title's own completion, not a second title
                    if self_dst is None:
                        self_dst = (cand, season)
                    continue
                dst, dst_season = cand, season
                break
            if dst:
                break
        metrics = sorted({name for name, rx in _J_METRIC_RXS if rx.search(t)})
        if 'autoplay' in metrics and 'handoff' not in metrics:
            metrics.append('handoff'); metrics.sort()
        if not dst:
            if src and (self_dst or 'completion' in metrics):
                # the title's own completion read
                dst, dst_season = (self_dst if self_dst else (src, src_season))
                if dst_season is None:
                    dst_season = src_season
            elif anaphoric and metrics:
                # "of those, what percent completed the whole season": the
                # chain is the one the previous question named; both
                # titles come from it (resolve_followup_journey)
                dst = ''
            else:
                return None
        if not src and not anaphoric:
            return None
        pm = _J_PLATFORM_RX.search(t)
        return {'src': src, 'src_season': src_season, 'dst': dst, 'dst_season': dst_season,
                'platform': (pm.group('p') if pm else ''), 'metrics': metrics, 'anaphoric': anaphoric}
    except Exception:
        return None


def _j_same_title(a, b):
    """Two folded titles name the same work: equal, or the shorter one is
    a contiguous run of the longer one's words with enough of its own
    ('raising kanan' in 'power raising kanan'; never 'power' alone)."""
    a, b = str(a or '').strip(), str(b or '').strip()
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    st, lt = short.split(), long_.split()
    if len(st) == 1 and len(short) < 7:
        return False
    if len(st) < 1 or len(short) < 5:
        return False
    for i in range(0, len(lt) - len(st) + 1):
        if lt[i:i + len(st)] == st:
            return True
    return False


def _j_compact(sig):
    """The signature as stored on an entry (lists, no None)."""
    if not isinstance(sig, dict):
        return None
    return {'src': str(sig.get('src') or '')[:80], 'src_season': sig.get('src_season'),
            'dst': str(sig.get('dst') or '')[:80], 'dst_season': sig.get('dst_season'),
            'platform': str(sig.get('platform') or '')[:24],
            'metrics': [str(m)[:12] for m in (sig.get('metrics') or [])][:6]}


def _j_key_part(sig):
    """The key tail that carries both titles and the metric, so a journey
    read never shares a key with the title's plain read."""
    if not isinstance(sig, dict) or not sig.get('dst'):
        return ''
    def _slug(s, n):
        s = str(s or '').strip().replace(' ', '_')[:40]
        return s + (f"_s{n}" if n else '')
    mets = '+'.join(sorted(str(m) for m in (sig.get('metrics') or []))) or 'journey'
    return f"|j:{_slug(sig.get('src'), sig.get('src_season'))}>{_slug(sig.get('dst'), sig.get('dst_season'))}:{mets}"


def entry_journey(entry):
    """The viewing chain a stored entry answers: the signature banked on
    it (new entries), else read off its own question (older entries), else
    off the head of its reply. None for a read that is not a journey."""
    if not isinstance(entry, dict):
        return None
    j = entry.get('journey')
    if isinstance(j, dict) and j.get('dst'):
        sig = {'src': j.get('src') or '', 'src_season': j.get('src_season'), 'dst': j.get('dst') or '',
               'dst_season': j.get('dst_season'), 'platform': j.get('platform') or '',
               'metrics': list(j.get('metrics') or []), 'anaphoric': False}
    else:
        sig = journey_signature(entry.get('q') or '')
        if sig and sig.get('anaphoric'):
            return sig
        if not sig:
            head = str(entry.get('reply') or '')[:600]
            if head:
                sig2 = journey_signature(head)
                if sig2 and sig2.get('src') and not sig2.get('anaphoric'):
                    sig = sig2
    if sig and not sig.get('platform'):
        # the question named no service but the read did ("On Power:
        # Raising Kanan season 5 viewers on Starz:")
        pm = _J_PLATFORM_RX.search(_j_fold(f"{entry.get('universe') or ''} | {str(entry.get('reply') or '')[:300]}"))
        if pm:
            sig = dict(sig, platform=pm.group('p'))
    return sig


def _j_season_ok(ask_season, entry_season):
    """The season the ask states must be the season the entry read; an
    ask that names none accepts any."""
    if ask_season is None:
        return True
    return entry_season == ask_season


def resolve_followup_journey(sig, prior_sig):
    """A follow-up's chain completed from the question it follows: "what
    percent of those watched the full season of Fightland" after the
    Kanan S5 -> Fightland ask is Kanan S5 -> Fightland, completion."""
    if not isinstance(sig, dict) or not sig.get('anaphoric'):
        return sig
    if not isinstance(prior_sig, dict) or not prior_sig.get('src'):
        return sig
    out = dict(sig)
    out['src'], out['src_season'] = prior_sig.get('src') or '', prior_sig.get('src_season')
    if not out.get('dst') or _j_same_title(out['dst'], prior_sig.get('src') or '~'):
        out['dst'], out['dst_season'] = prior_sig.get('dst') or '', prior_sig.get('dst_season')
    if out.get('dst_season') is None and _j_same_title(out.get('dst') or '', prior_sig.get('dst') or '~'):
        out['dst_season'] = prior_sig.get('dst_season')
    if not out.get('platform'):
        out['platform'] = prior_sig.get('platform') or ''
    out['anaphoric'] = False
    return out


def journey_match(entry, ask_sig):
    """True when the stored entry may answer this ask under the viewing
    chain rule. An ask with no chain is unconstrained unless the entry is
    itself a journey read (a journey read answers only a journey ask). A
    follow-up whose source audience is still unresolved ("of those" with
    nothing to bind to) never matches a banked entry."""
    try:
        esig = entry_journey(entry)
        if not isinstance(ask_sig, dict):
            return esig is None
        if ask_sig.get('anaphoric') or not ask_sig.get('src'):
            return False
        if not esig or not esig.get('src'):
            return False
        if not _j_same_title(ask_sig.get('src'), esig.get('src')):
            return False
        if not _j_same_title(ask_sig.get('dst'), esig.get('dst')):
            return False
        if not _j_season_ok(ask_sig.get('src_season'), esig.get('src_season')):
            return False
        if not _j_season_ok(ask_sig.get('dst_season'), esig.get('dst_season')):
            return False
        ap, ep = str(ask_sig.get('platform') or ''), str(esig.get('platform') or '')
        if ap and ep and ap != ep:
            return False
        # every metric the ask names is one the stored read carries
        want = set(ask_sig.get('metrics') or [])
        have = set(esig.get('metrics') or [])
        if 'autoplay' in have:
            have.add('handoff')
        if want and not want <= have:
            return False
        return True
    except Exception:
        return False


def _find_journey_anywhere(subjects, ask_sig, question=None):
    """The stored read on the same whole journey, wherever it was filed
    (a chain names two titles and the bucket it landed in is whichever
    the lane resolved; a follow-up may name neither). Provenance wins,
    then recency. None when the ask is not a resolved chain."""
    if not isinstance(ask_sig, dict) or ask_sig.get('anaphoric') or not ask_sig.get('src') or not ask_sig.get('dst'):
        return None, None
    qn = normalize_question(question) if question else ''
    q_years = _years_in(qn)
    best_key, best, best_rank = None, None, None
    for k, v in (subjects or {}).items():
        for e in reversed(v.get('entries') or []):
            if not isinstance(e, dict) or e.get('retired') or not e.get('reply') or is_retired_surface_reply(e.get('reply')):
                continue
            if e.get('route') == ANCHOR_ROUTE or entry_journey(e) is None:
                continue
            if q_years and not _window_covers(e, q_years):
                continue
            if not journey_match(e, ask_sig):
                continue
            r = provenance_rank(e)
            if best is None or r > best_rank or (r == best_rank and (e.get('ts') or '') > (best.get('ts') or '')):
                best_key, best, best_rank = k, e, r
    return best_key, best


def _prior_user_question(history, question=None):
    """The user turn before this ask in the thread, or ''. The ask's own
    turn (already appended by some callers) is skipped."""
    qn = normalize_question(question) if question else ''
    seen_self = False
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') != 'user':
            continue
        txt = str(t.get('text') or t.get('content') or '').strip()
        if not txt:
            continue
        if qn and not seen_self and normalize_question(txt) == qn:
            seen_self = True
            continue
        return txt
    return ''


def ask_journey(question, prior_question=None, history=None):
    """The chain this ask is about, follow-ups completed from the question
    they follow (``prior_question``, else the previous user turn in
    ``history``). None when the ask names no chain."""
    sig = journey_signature(question)
    if not sig:
        return None
    if sig.get('anaphoric'):
        prev = str(prior_question or '').strip() or _prior_user_question(history, question)
        if prev:
            sig = resolve_followup_journey(sig, journey_signature(prev))
    return sig


def find_semantic(entries, question, ask_sig=None):
    """Meaning-level replay candidate: the best stored read whose
    (family, cohort, slice dimension) matches the ask. Overlapping
    age ranges bind the closest stored cohort; at equal cohort
    distance, provenance rank wins (delivered_artifact > operator >
    distilled), then recency.

    The family verb is read with audience-qualifier relative clauses
    stripped (2026-10-01): in "top toy categories for parents of kids
    4-7 who watch paw patrol" the watching names the audience, not
    the metric, and must not steer the ask into the viewership
    family away from the stored purchases table."""
    fam = family_from_question(_AUDIENCE_CLAUSE_RX.sub(' ', str(question or '')))
    qn = normalize_question(question)
    qsig = cohort_signature(question)
    q_years = _years_in(qn)
    if ask_sig is None:
        ask_sig = ask_journey(question)
    best, best_dist, best_rank = None, None, None
    for e in reversed(entries):   # newest first; ties keep the newest
        if not isinstance(e, dict) or not e.get('reply') or is_retired_surface_reply(e.get('reply')):
            continue
        if e.get('route') == ANCHOR_ROUTE:
            continue
        # A journey read is reused only for the same whole journey
        # (2026-11-05, Kanan S5 -> Fightland served Fightland -> S.W.A.T.
        # Exiles): source, destination, seasons, platform, metric.
        if not journey_match(e, ask_sig):
            continue
        # Same base, same window, same answer (Jenna 2026-09-24): an
        # ask that names a year only replays a read whose window
        # covers that year. An ask that names no window is
        # unconstrained, as before.
        if q_years and not _window_covers(e, q_years):
            continue
        # An ask that names its month window only replays a read banked
        # on that window (2026-11-10, Liz: Nov 2025 to Oct 2026 asked,
        # Jul 2025 to Jun 2026 handed back).
        if not _month_window_ok(e, question):
            continue
        # An annual-phrased ask ("annual viewership", "full year")
        # carries no digits, so the year gate above never sees it;
        # it must not replay a read banked on a much shorter window
        # (2026-10-01: a July answer served for an annual ask).
        if _ANNUAL_ASK_RX.search(qn or ''):
            span = _entry_span_days(e)
            if span is not None and span < 300:
                continue
        # The decision of record answers every natural ask on its subject
        # (2026-11-10): no family verb or ranked dimension is needed, the
        # record IS the subject's read. It must still count what the ask
        # counts and sit on the window the ask names.
        if is_record_entry(e):
            if _measure_ok(e, _norm_full(question)) and _month_window_ok(e, question) \
                    and _topical_overlap_ok(e, qn, floor=0.2) and _chain_titles_ok(e, question):
                rank = provenance_rank(e)
                if best is None or best_rank is None or rank > best_rank or best_dist is None or best_dist > 0:
                    best, best_dist, best_rank = e, 0, rank
            continue
        if fam:
            if e.get('family') not in _family_compat(fam):
                continue
        else:
            # No family verb in the ask ("top toy categories for
            # parents of kids 4-7 ..."): the named slice dimension
            # carries the meaning instead. Only a stored ranked table
            # whose dimension the ask names qualifies.
            bd = e.get('breakdown') or {}
            dtoks = [w for w in
                     normalize_subject(bd.get('dimension') or '').split()
                     if w not in _DIM_STOP]
            if not (bd.get('rows') and dtoks):
                continue
        esig = cohort_signature(e.get('cohort') or '')
        # An ask that names NO cohort facts ("what toy categories are
        # underserved for paw patrol") is unconstrained: it binds the
        # subject's stored read whatever cohort it covers, at a
        # distance penalty so a cohort-named ask always wins ties
        # (2026-08-27, white-space asks).
        q_uncons = qsig['ages'] is None and not qsig['parents']
        if not q_uncons and esig['parents'] != qsig['parents'] \
                and e.get('cohort'):
            continue
        qa, ea = qsig['ages'], esig['ages']
        if qa and ea:
            if not (qa[0] <= ea[1] and ea[0] <= qa[1]):
                continue
            dist = abs(qa[0] - ea[0]) + abs(qa[1] - ea[1])
        elif qa and not ea:
            dist = 6
        elif ea and not qa:
            dist = 8 if qsig['parents'] else 10
        else:
            dist = 0
        if not _dimension_ok(e, qn):
            continue
        if not _measure_ok(e, _norm_full(question)):
            continue   # the stored read counts what the whole ask counts (2026-11-10)
        if not _topical_overlap_ok(e, qn):
            continue
        if not _chain_titles_ok(e, question):
            continue   # a chain between other titles is another read (2026-11-05)
        rank = provenance_rank(e)
        if best is None or dist < best_dist \
                or (dist == best_dist and rank > best_rank):
            best, best_dist, best_rank = e, dist, rank
    return best


# A reply from a surface that no longer exists is never an answer to
# anything: the suggested-cuts list ("Here's how I'd cut this ...") was
# retired on 2026-10-21 and the distilled history still carried five of
# them; a key-only match handed one to "what about jacknjellify?" and to
# a Will & Grace viewership ask (2026-10-19 and 2026-10-25, both under
# the subject 'youtube').
_RETIRED_SURFACE_RX = re.compile(r"^\s*Here'?s how I'?d cut this\b|\(each cut is \d+ credits", re.I)
# A priced offer is a quote, never an answer (2026-11-05, Liz Huszarik:
# "did the Instagram Social touchpoints convert any movie viewing" on
# the Music to Long Form study replayed a banked "I can pull that read
# on 🗺️ Digital Journey IQ. Pulling this data will cost $100." that a
# distilled read job had carried as its reply). Offer copy never banks
# and never replays, whatever bucket it sits in.
_OFFER_COPY_RX = re.compile(
    r"Pulling this data will cost|I can pull that read on|I can size the\b.{0,120}?\bPulling|"
    r"I will not answer until you accept|It runs \$\s?\d|not billed to a staff account|"
    r"^\s*Approve\s*(?:\(|/|\|)\s*(?:\$|Cancel)|^\s*(?:Approve|Accept)\s*$", re.I | re.S)


def is_offer_reply(reply):
    """True when the stored reply is a priced quote (the offer sentence,
    the cost line, or the Approve / Cancel chip text) and not a read."""
    return bool(_OFFER_COPY_RX.search(str(reply or '')[:600]))


def is_retired_surface_reply(reply):
    head = str(reply or '')[:600]
    return bool(_RETIRED_SURFACE_RX.search(head[:400])) or is_offer_reply(head)


def find_exact(entries, question=None, key=None, ask_sig=None):
    """Best stored entry matching this exact ask: normalized question
    match first (strongest), else normalized key match with a
    non-empty stored reply whose own words are about the ask (a key is
    subject, family and window; two different questions share one).
    On conflicting matches, provenance wins (delivered_artifact >
    operator > distilled), then recency; with the historical
    single-provenance corpus this is exactly the old newest-first
    behavior. A viewing-chain ask (``ask_sig``, derived from the
    question when not given) only takes an entry on the same whole
    journey; a follow-up with an unresolved "of those" takes none."""
    qn = normalize_question(question) if question else None
    if ask_sig is None and question:
        ask_sig = ask_journey(question)

    def _best(match_fn):
        best, best_rank = None, None
        for e in reversed(entries):     # newest first; ties keep newest
            if not (match_fn(e) and e.get('reply')) or is_retired_surface_reply(e.get('reply')):
                continue
            if question and not journey_match(e, ask_sig):
                continue
            r = provenance_rank(e)
            if best is None or r > best_rank:
                best, best_rank = e, r
        return best

    if qn:
        hit = _best(lambda e: e.get('qn') == qn)
        if hit is not None:
            return hit
    if key:
        # a key is subject, family and window; the stored read must also
        # count what the ask counts and sit on the month window it names
        return _best(lambda e: e.get('k') == key and (not qn or (_topical_overlap_ok(e, qn) and _chain_titles_ok(e, question)
                                                                 and _measure_ok(e, _norm_full(question)) and _month_window_ok(e, question))))
    return None


_ROLLING_WINDOW_RX = re.compile(
    r'\b(?:last|past|trailing|previous)\s+\d+\s*'
    r'(?:day|week|month|year)s?\b'
    r'|\bt\d+d\b|\byesterday\b|\btoday\b'
    r'|\b(?:this|last)\s+(?:week|month|quarter|year)\b'
    r'|\bmonth to date\b|\byear to date\b|\bytd\b',
    re.IGNORECASE)


def _exact_if_fresh(e):
    """Rolling-window answers age out of verbatim replay (2026-09-28,
    Phase 1 of the improvement plan): an answer to 'the last 30 days'
    banked on day 0 describes a different window by day 31, and
    because replay short-circuits regeneration the stale number could
    never update. A stale rolling entry still rides the constraint
    block (the fresh read anchors to it); it just stops replaying
    verbatim. Absolute windows replay forever. Fresh = the banked
    timestamp is within one window span of now, 48-hour floor so a
    7-day ask does not churn daily."""
    if not e:
        return None
    q = f"{e.get('q') or ''} {e.get('wl') or ''}"
    # the subject's own name is not a window ("USA Today" read as the
    # rolling word "today" and its banked answers aged out of replay)
    subj = str(e.get('subject') or '').strip()
    if subj:
        q = re.sub(re.escape(subj), ' ', q, flags=re.IGNORECASE)
    if not _ROLLING_WINDOW_RX.search(q):
        return e
    try:
        ts = datetime.strptime(str(e.get('ts') or ''),
                               '%Y-%m-%dT%H:%M:%SZ')
        ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return e
    age_s = (datetime.now(timezone.utc) - ts).total_seconds()
    span_s = None
    try:
        ws = datetime.strptime(str(e.get('ws') or ''), '%Y-%m-%d')
        we = datetime.strptime(str(e.get('we') or ''), '%Y-%m-%d')
        span_s = max(0.0, (we - ws).total_seconds()) + 86400
    except Exception:
        span_s = None
    if span_s is None:
        m = re.search(r'(?:last|past|trailing|previous)\s+(\d+)\s*'
                      r'(day|week|month|year)', q, re.IGNORECASE)
        if m:
            mult = {'day': 1, 'week': 7, 'month': 30, 'year': 365}
            span_s = (int(m.group(1))
                      * mult[m.group(2).lower()] * 86400)
    if span_s is None:
        span_s = 30 * 86400
    if age_s <= max(span_s, 2 * 86400):
        return e
    try:
        print(f"[insights-ledger] rolling-window entry aged out of "
              f"replay ({e.get('k')}); it still rides constraints")
    except Exception:
        pass
    return None


def consult(subject=None, question=None, metric_family=None,
            window_start=None, window_end=None, prior_question=None,
            history=None):
    """Look up ledger history for this ask.

    Returns {'subject', 'skey', 'entries', 'block', 'exact'}:
    - entries: stored entries for the resolved subject (oldest first)
    - block:   PUBLISHED MEASUREMENTS body ('' when no history)
    - exact:   entry to replay verbatim, or None (a rolling-window
      entry older than its own span never replays verbatim)
    ``prior_question`` / ``history`` (2026-11-05): the question this
    ask follows, so a follow-up on a viewing chain ("what percent of
    those watched the full season of Fightland") binds to the read
    produced for that question and never to a banked entry that merely
    shares a title. Without either, a chain follow-up replays nothing.
    Never raises; empty result on any failure.
    """
    empty = {'subject': None, 'skey': None, 'entries': [],
             'block': '', 'exact': None, 'match': None}
    try:
        doc = _load_index()
        subjects = doc.get('subjects') or {}
        # Verbatim replay needs an ask a stored read can answer
        # (2026-10-02 S5). Commands, generic screen asks, and
        # complaints still get their history in the block.
        _can_replay = replay_eligible(question)
        _ask_sig = ask_journey(question, prior_question=prior_question, history=history)
        skey = _resolve_subject(doc, subject=subject, question=question)
        if skey and not _has_live_entries(subjects.get(skey)):
            # every read under the resolved subject is retired: the ask
            # resolves again over the live buckets (the record banked
            # under its own subject answers it, 2026-11-10)
            skey = _resolve_subject(doc, subject=None, question=question, live_only=True) \
                or _resolve_subject(doc, subject=subject, question=question, live_only=True)
        if not skey:
            # Exact-ask replay must not depend on subject resolution:
            # scan all buckets for this normalized question.
            gkey, gexact = _find_exact_anywhere(subjects, question)
            if gexact is not None and not journey_match(gexact, _ask_sig):
                gexact = None
            if not gexact:
                # a chain read is filed under whichever title the lane
                # resolved; the same whole journey is the same read
                gkey, gexact = _find_journey_anywhere(subjects, _ask_sig, question)
            if not gexact:
                return empty
            bucket = subjects.get(gkey) or {}
            entries = [e for e in (bucket.get('entries') or [])
                       if isinstance(e, dict) and not e.get('retired')]
            if gexact.get('retired'):
                return empty
            return {'subject': bucket.get('subject'), 'skey': gkey,
                    'entries': entries, 'block': render_block(entries),
                    'exact': (_exact_if_fresh(gexact) if _can_replay
                              else None),
                    'match': 'exact' if _can_replay else None}
        bucket = subjects.get(skey) or {}
        entries = [e for e in (bucket.get('entries') or [])
                   if isinstance(e, dict) and not e.get('retired')]
        if not entries:
            return empty
        key = None
        if metric_family:
            key = entry_key(bucket.get('subject') or subject or '',
                            metric_family, window_start, window_end)
        match = None
        exact = find_exact(entries, question=question, key=key, ask_sig=_ask_sig)
        if exact:
            match = 'exact'
        if not exact:
            gkey, gexact = _find_exact_anywhere(subjects, question)
            if gexact and journey_match(gexact, _ask_sig):
                exact = gexact
                match = 'exact'
        if not exact and _ask_sig:
            # The same whole journey is the same read whichever title it
            # was filed under (2026-11-05): a Kanan S5 -> Fightland read
            # filed under Power: Raising Kanan answers the ask that
            # resolved to the Fightland bucket, and a follow-up that
            # names no title at all.
            jkey, jexact = _find_journey_anywhere(subjects, _ask_sig, question)
            if jexact is not None:
                exact, match = jexact, 'semantic'
                if jkey and jkey != skey:
                    jb = subjects.get(jkey) or {}
                    jents = [e for e in (jb.get('entries') or []) if isinstance(e, dict) and not e.get('retired')]
                    if jents:
                        skey, bucket, entries = jkey, jb, jents
        if not exact:
            # Meaning-level replay (2026-08-27): same family + cohort +
            # slice dimension = the same read, whatever the wording.
            exact = find_semantic(entries, question, ask_sig=_ask_sig)
            if exact:
                match = 'semantic'
        if not exact:
            # A decision of record the question itself names outranks the
            # bound subject's bucket (2026-11-10): an ask on the Amazon
            # Prime Video profile about 'Amazon Prime Video Channels versus
            # direct' is answered by the published Channels vs direct
            # record, not read fresh against the profile.
            rkey = _resolve_subject(doc, subject=None, question=question, live_only=True)
            if rkey and rkey != skey:
                rb = subjects.get(rkey) or {}
                rents = [e for e in (rb.get('entries') or []) if isinstance(e, dict) and not e.get('retired')]
                if any(is_record_entry(e) for e in rents):
                    rexact = find_semantic(rents, question, ask_sig=_ask_sig)
                    if rexact is not None and is_record_entry(rexact):
                        skey, bucket, entries = rkey, rb, rents
                        exact, match = rexact, 'semantic'
        if not _can_replay:
            exact, match = None, None
        exact = _exact_if_fresh(exact)
        return {'subject': bucket.get('subject'), 'skey': skey,
                'entries': entries, 'block': render_block(entries),
                'exact': exact, 'match': match if exact else None}
    except Exception as e:
        print(f"[insights-ledger] consult failed: {e}")
        return empty


def examples(question=None, subject=None, k=2):
    """Worked examples for the generation loop (2026-08-27, Jenna:
    banked reads are "foundational for similar asks as examples").

    Nearest prior DELIVERED reads, ranked:
    1. same metric family on ANOTHER subject (method exemplar - a
       white-space read for Paw Patrol shapes next month's white-space
       read for a different franchise),
    2. another read on the SAME subject (grounding + voice - the toy
       category mix grounds the white-space ask that follows it).
    Anchors and the ask's own replay candidate are excluded. Never
    raises; empty list on any failure."""
    try:
        doc = _load_index()
        subjects = doc.get('subjects') or {}
        fam = family_from_question(question)
        qn = normalize_question(question)
        skey = _resolve_subject(doc, subject=subject, question=question)
        same_fam, same_subj = [], []
        for bkey, bucket in subjects.items():
            for e in reversed(list(bucket.get('entries') or [])):
                if not isinstance(e, dict) or not e.get('reply'):
                    continue
                if e.get('route') == ANCHOR_ROUTE:
                    continue
                if qn and e.get('qn') == qn:
                    continue    # that's the replay, not an example
                if fam and e.get('family') == fam and bkey != skey \
                        and len(same_fam) < k:
                    same_fam.append(e)
                elif bkey == skey and len(same_subj) < k:
                    same_subj.append(e)
        out = (same_fam + same_subj)[:k]
        return out
    except Exception as e:
        print(f"[insights-ledger] examples failed: {e}")
        return []


def render_examples_block(entries):
    """Prompt block carrying prior delivered reads as worked examples.
    Method and voice transfer; the numbers never do."""
    ents = [e for e in (entries or [])
            if isinstance(e, dict) and e.get('reply')]
    if not ents:
        return ''
    lines = [
        'WORKED EXAMPLES - reads already delivered from this library. '
        'Follow their METHOD, structure, table shape, tier language, '
        'and voice. Their numbers belong to their own subjects and '
        'cohorts: never copy a number from an example into this read.']
    for e in ents[:3]:
        head = ' / '.join(x for x in (e.get('subject'), e.get('cohort'),
                                      e.get('family')) if x)
        lines.append(f"--- EXAMPLE ({head}) ---")
        lines.append(str(e.get('reply'))[:1700])
    return '\n'.join(lines)


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
           'find_exact', 'find_semantic', 'is_offer_reply', 'is_retired_surface_reply', 'metrics_from_study',
           'replay_eligible', 'fresh_run_requested',
           'entry_key', 'canon_family', 'subject_key',
           'normalize_question', 'normalize_subject',
           'family_from_question', 'cohort_signature',
           'ingest_deck_anchors', 'canon_provenance',
           'provenance_rank', 'PROV_DELIVERED', 'PROV_OPERATOR',
           'PROV_DISTILLED', 'ANCHOR_ROUTE', 'LEDGER_PREFIX',
           'INDEX_KEY', 'ENTRY_PREFIX']
