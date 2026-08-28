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
               base_profile_key='', cohort='', breakdown=None,
               derivation=''):
    """Build one ledger entry. `metrics` is a list of dicts with
    name/label/value/unit/definition (extra keys dropped).

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
        'cohort': str(cohort or '')[:120],
        'breakdown': _clean_breakdown(breakdown),
        'reply': str(reply or '')[:MAX_REPLY_CHARS],
        'followups': [str(f)[:160] for f in (followups or [])
                      if str(f)][:4],
        # Internal derivation trail (2026-08-27, the generation loop):
        # what grounded the read (base, neighbors weighed, research
        # gaps, examples). Logs and audit only - render_block never
        # includes it, so it can never reach a prompt or a user.
        'derivation': str(derivation or '')[:600],
    }
    return entry


def _clean_breakdown(breakdown):
    """Bound-checked copy of a breakdown table, or None."""
    if not isinstance(breakdown, dict):
        return None
    rows = []
    for r in (breakdown.get('rows') or [])[:16]:
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
        ents = [e for e in ents if not _supersedes(entry, e)]
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
            base_profile_key='', cohort='', breakdown=None,
            derivation=''):
    """Persist one delivered read. Never raises; never blocks the
    caller (daemon thread) unless _SYNC_FOR_TESTS."""
    try:
        entry = make_entry(
            subject=subject, metric_family=metric_family,
            question=question, route=route, metrics=metrics,
            anchors=anchors, window_start=window_start,
            window_end=window_end, window_label=window_label,
            reply=reply, followups=followups,
            base_profile_key=base_profile_key, cohort=cohort,
            breakdown=breakdown, derivation=derivation)
        if not entry['metrics'] and not entry['reply']:
            return
        if _SYNC_FOR_TESTS:
            _record_entry_now(entry)
            return
        threading.Thread(target=_record_entry_now, args=(entry,),
                         daemon=True).start()
    except Exception as e:
        print(f"[insights-ledger] persist failed: {e}")


ANCHOR_ROUTE = 'delivered_deck'


def ingest_deck_anchors(*, subject, metrics, source_name='',
                        window_start='', window_end='',
                        window_label='', basis_notes=None):
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
    never raises."""
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
                subject=subject, metric_family='delivered_anchors',
                question=marker, route=ANCHOR_ROUTE,
                metrics=clean[i:i + 6], window_start=window_start,
                window_end=window_end, window_label=window_label)
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


def _resolve_subject(index_doc, subject=None, question=None):
    """Find the subject bucket for this ask. Explicit subject first
    (normalized match), else the longest ledger subject whose
    normalized form appears inside the normalized question, else the
    subject whose distinctive tokens all appear in the question."""
    subjects = index_doc.get('subjects') or {}
    if subject:
        skey = subject_key(subject)
        if skey in subjects:
            return skey
        norm = normalize_subject(subject)
        for k, v in subjects.items():
            if normalize_subject(v.get('subject')) == norm:
                return k
        # No bucket carries this exact subject name. A derived base
        # name still resolves its parent bucket ("Parents of Paw
        # Patrol Series Viewers" -> "Paw Patrol Series"): fall through
        # to the token scan with the subject text folded in
        # (2026-08-27, rephrased toy ask).
        question = f"{subject} {question or ''}"
    qn = ' ' + normalize_question(question) + ' '
    best_key, best_len = None, 0
    for k, v in subjects.items():
        s_norm = normalize_subject(v.get('subject'))
        if not s_norm or len(s_norm) < 3:
            continue
        if f' {s_norm} ' in qn and len(s_norm) > best_len:
            best_key, best_len = k, len(s_norm)
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
            if e.get('qn') == qn and e.get('reply'):
                if best is None or (e.get('ts') or '') > (best.get('ts')
                                                          or ''):
                    best_key, best = k, e
                break
    return best_key, best


def render_block(entries, limit=12):
    """PUBLISHED MEASUREMENTS body for the prompt builders: one line
    per stored metric. Delivered-deck anchor entries render first
    (they are the figures already shipped for the subject and always
    survive the line cap), then the most recent reads."""
    anchors = [e for e in entries if e.get('route') == ANCHOR_ROUTE]
    reads = [e for e in entries if e.get('route') != ANCHOR_ROUTE]
    ordered = anchors[-limit:] + list(reversed(reads[-limit:]))
    lines = []
    for e in ordered[:limit]:
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
        bd = e.get('breakdown') or {}
        coh = f", {e.get('cohort')}" if e.get('cohort') else ''
        for r in (bd.get('rows') or [])[:6]:
            lines.append(
                f"- {e.get('subject')}{coh} | {e.get('family')} | {win}: "
                f"{bd.get('dimension') or 'Category'} share, "
                f"{r.get('label')} = {float(r.get('share_pct') or 0):.1f}%")
        if len(lines) >= 30:
            break
    return '\n'.join(lines[:30])


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
    ('viewership', ('watching', 'watch next', 'streaming', 'binge',
                    'tune', 'viewing')),
    ('revenue', ('revenue', 'arpu')),
    ('engagement', ('engagement', 'session', 'follow')),
)
_DIM_STOP = {'category', 'categories', 'share', 'mix', 'breakdown',
             'of', 'the', 'by', 'top'}
_DIM_CLASS_WORDS = ('brand', 'retailer', 'store', 'platform', 'market',
                    'dma', 'city', 'state', 'genre', 'network',
                    'channel')


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
    if dtoks and not any(w.rstrip('s') in qn for w in dtoks):
        return False
    for c in _DIM_CLASS_WORDS:
        if c in qn and c not in dim:
            return False
    return True


def find_semantic(entries, question):
    """Meaning-level replay candidate: the newest stored read whose
    (family, cohort, slice dimension) matches the ask. Overlapping
    age ranges bind the closest stored cohort."""
    fam = family_from_question(question)
    qn = normalize_question(question)
    qsig = cohort_signature(question)
    best, best_dist = None, None
    for e in reversed(entries):   # newest first; ties keep the newest
        if not isinstance(e, dict) or not e.get('reply'):
            continue
        if e.get('route') == ANCHOR_ROUTE:
            continue
        if fam:
            if e.get('family') != fam:
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
        if best is None or dist < best_dist:
            best, best_dist = e, dist
    return best


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
        subjects = doc.get('subjects') or {}
        skey = _resolve_subject(doc, subject=subject, question=question)
        if not skey:
            # Exact-ask replay must not depend on subject resolution:
            # scan all buckets for this normalized question.
            gkey, gexact = _find_exact_anywhere(subjects, question)
            if not gexact:
                return empty
            bucket = subjects.get(gkey) or {}
            entries = [e for e in (bucket.get('entries') or [])
                       if isinstance(e, dict)]
            return {'subject': bucket.get('subject'), 'skey': gkey,
                    'entries': entries, 'block': render_block(entries),
                    'exact': gexact}
        bucket = subjects.get(skey) or {}
        entries = [e for e in (bucket.get('entries') or [])
                   if isinstance(e, dict)]
        if not entries:
            return empty
        key = None
        if metric_family:
            key = entry_key(bucket.get('subject') or subject or '',
                            metric_family, window_start, window_end)
        exact = find_exact(entries, question=question, key=key)
        if not exact:
            gkey, gexact = _find_exact_anywhere(subjects, question)
            if gexact:
                exact = gexact
        if not exact:
            # Meaning-level replay (2026-08-27): same family + cohort +
            # slice dimension = the same read, whatever the wording.
            exact = find_semantic(entries, question)
        return {'subject': bucket.get('subject'), 'skey': skey,
                'entries': entries, 'block': render_block(entries),
                'exact': exact}
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
           'find_exact', 'metrics_from_study', 'entry_key',
           'canon_family', 'subject_key', 'normalize_question',
           'normalize_subject', 'LEDGER_PREFIX', 'INDEX_KEY',
           'ENTRY_PREFIX']
