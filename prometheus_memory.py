"""Per-user conversational memory (2026-08-27, Jenna).

Jenna, verbatim: "when you ask questions too it needs to remember your
chat log and previous questions so if I go back and ask for toy white
space it can say do you mean for paw patrol viewers parents blah blah
and know context between sessions and threads."

A compact per-user record of recent resolved asks, persisted so it
survives sessions, threads, and reloads. Used two ways:

1. RESOLUTION LADDER, last rung. An underspecified ask ("toy white
   space", "size that opportunity") first tries thread anaphora and
   the same-session history (both bind silently). When neither
   resolves, the user's cross-session memory supplies the referent -
   but a cross-session hit never binds silently: the caller asks a
   grounded confirm ("Do you mean for parents of Paw Patrol viewers
   (kids 4-7)?") with the remembered referent(s) as chips.
2. GROUNDED CLARIFIES. Whenever a clarifying question has a plausible
   remembered default (last subject, last cohort), the clarify leads
   with it as the first chip instead of asking cold.

Layout on S3 (bucket dashboard-inputs):

    system/prometheus_memory/<user_key>.json
        {"user": "<email>", "asks": [<record>...]}   newest first

    One object per user - one user's memory NEVER informs another's
    session. ETag-CAS writes (read -> mutate -> conditional PUT ->
    retry on 412), the same pattern as insights_ledger.py.

Retention is light: the newest MAX_ASKS records inside RETENTION_DAYS.
Writes ride a daemon thread (remember() adds zero reply latency);
reads are cached briefly per user.
"""

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone

from botocore.exceptions import ClientError, ParamValidationError

import insights_ledger as il

S3_BUCKET = il.S3_BUCKET
MEMORY_PREFIX = 'system/prometheus_memory/'

MAX_ASKS = 50
RETENTION_DAYS = 30

# Test hook: when True, remember() runs inline so tests can assert on
# the written store synchronously.
_SYNC_FOR_TESTS = False

_READ_TTL_S = 20.0
_read_cache = {}          # user_key -> (ts, doc)
_cache_lock = threading.Lock()

_SUPPORTS_CONDITIONAL_PUT = True


def _client():
    return il._client()


def _user_key(user):
    """Stable, S3-safe key for a user email/username. The readable
    slug plus a short hash so distinct users can never collide."""
    u = str(user or '').strip().lower()
    if not u:
        return ''
    slug = re.sub(r'[^a-z0-9]+', '_', u).strip('_')[:48]
    h = hashlib.sha256(u.encode()).hexdigest()[:10]
    return f"{slug}_{h}"


def _store_key(user):
    uk = _user_key(user)
    return (MEMORY_PREFIX + uk + '.json') if uk else ''


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _read_with_etag(user):
    key = _store_key(user)
    if not key:
        return {'user': '', 'asks': []}, None
    try:
        resp = _client().get_object(Bucket=S3_BUCKET, Key=key)
        doc = json.loads(resp['Body'].read().decode('utf-8'))
        if not isinstance(doc, dict):
            doc = {}
        doc.setdefault('asks', [])
        return doc, resp.get('ETag')
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('NoSuchKey', '404', 'NotFound'):
            return {'user': str(user or ''), 'asks': []}, None
        raise


def _put_cas(user, doc, etag):
    """Conditional PUT. True on success, False on detected conflict."""
    global _SUPPORTS_CONDITIONAL_PUT
    key = _store_key(user)
    if not key:
        return False
    s3 = _client()
    body = json.dumps(doc, ensure_ascii=False).encode('utf-8')
    kwargs = {'Bucket': S3_BUCKET, 'Key': key, 'Body': body,
              'ContentType': 'application/json'}
    if _SUPPORTS_CONDITIONAL_PUT:
        try:
            if etag:
                kwargs['IfMatch'] = etag.strip('"')
            else:
                kwargs['IfNoneMatch'] = '*'
            s3.put_object(**kwargs)
            return True
        except ParamValidationError:
            _SUPPORTS_CONDITIONAL_PUT = False
            kwargs.pop('IfMatch', None)
            kwargs.pop('IfNoneMatch', None)
        except ClientError as e:
            code = (e.response.get('Error') or {}).get('Code', '')
            status = (e.response.get('ResponseMetadata')
                      or {}).get('HTTPStatusCode')
            if code in ('PreconditionFailed', '412') or status == 412:
                return False
            raise
    # Degraded path: verify current ETag, then plain put.
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=kwargs['Key'])
        current = (head.get('ETag') or '').strip('"')
    except ClientError:
        current = None
    expected = etag.strip('"') if etag else None
    if current != expected:
        return False
    s3.put_object(**kwargs)
    return True


def _update(user, mutate_fn, max_retries=4):
    """GET -> mutate -> conditional PUT with retry on conflict."""
    import random
    for attempt in range(max_retries + 1):
        try:
            doc, etag = _read_with_etag(user)
        except Exception as e:
            print(f"[pm-memory] read failed: {e}")
            return None
        new_doc = mutate_fn(doc)
        if new_doc is None:
            return None
        try:
            if _put_cas(user, new_doc, etag):
                with _cache_lock:
                    _read_cache[_user_key(user)] = (time.time(), new_doc)
                return new_doc
        except Exception as e:
            print(f"[pm-memory] put failed: {e}")
            return None
        time.sleep(min(0.15 * (2 ** attempt), 1.5)
                   + random.uniform(0, 0.15))
    print(f"[pm-memory] update gave up after {max_retries + 1} conflicts")
    return None


def _trim(asks):
    """Newest first, capped, and inside the retention window."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    kept = []
    for a in asks:
        if not isinstance(a, dict):
            continue
        ts = str(a.get('ts') or '')
        try:
            t = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ') \
                .replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            t = time.time()
        if t >= cutoff:
            kept.append(a)
    return kept[:MAX_ASKS]


def _record_now(user, rec):
    def mutate(doc):
        doc['user'] = str(user or '')
        asks = [rec] + [a for a in (doc.get('asks') or [])]
        doc['asks'] = _trim(asks)
        return doc
    _update(user, mutate)


# Standing default build window (default-date-range rule). A stored
# window only counts as a rememberable preference when it differs.
DEFAULT_WINDOW_START = '2025-07-01'
DEFAULT_WINDOW_END = '2026-06-30'


def remember(user, question, subject=None, cohort=None, ledger_key=None,
             thread_id=None, view=None, route=None, window=None,
             region=None):
    """Record a resolved ask into the user's memory. Fire-and-forget
    (daemon thread) unless _SYNC_FOR_TESTS: never adds reply latency,
    never raises. `window` is {'start': 'YYYY-MM-DD', 'end': ...} when
    the ask ran with a confirmed build window; `region` is the named
    market(s) when the build carried market cuts."""
    try:
        if not str(user or '').strip() or not str(question or '').strip():
            return
        rec = {
            'q': str(question)[:300],
            'subject': (str(subject)[:120] if subject else None),
            'cohort': (str(cohort)[:120] if cohort else None),
            'ledger_key': (str(ledger_key)[:200] if ledger_key else None),
            'thread': (str(thread_id)[:64] if thread_id else None),
            'view': (str(view)[:48] if view else None),
            'route': (str(route)[:32] if route else None),
            'ts': _now_iso(),
        }
        if isinstance(window, dict) and window.get('start') \
                and window.get('end'):
            rec['window'] = {'start': str(window['start'])[:10],
                             'end': str(window['end'])[:10]}
        if region:
            if isinstance(region, (list, tuple)):
                region = ', '.join(str(r) for r in region if r)
            region = str(region).strip()
            if region:
                rec['region'] = region[:160]
        if _SYNC_FOR_TESTS:
            _record_now(user, rec)
            return
        threading.Thread(target=_record_now, args=(user, rec),
                         daemon=True).start()
    except Exception:
        pass


def recall(user, k=MAX_ASKS):
    """The user's recent asks, newest first. Briefly cached. Strictly
    per-user: the store key derives from THIS user only."""
    uk = _user_key(user)
    if not uk:
        return []
    now = time.time()
    with _cache_lock:
        hit = _read_cache.get(uk)
        if hit and now - hit[0] < _READ_TTL_S:
            return list((hit[1].get('asks') or [])[:k])
    try:
        doc, _ = _read_with_etag(user)
    except Exception as e:
        print(f"[pm-memory] recall failed: {e}")
        return []
    with _cache_lock:
        _read_cache[uk] = (now, doc)
    return list((doc.get('asks') or [])[:k])


def recent_referents(user, k=2):
    """The user's most recent distinct (subject, cohort) referents,
    newest first - the candidates a grounded confirm offers when an
    underspecified ask has no thread or session referent."""
    seen, out = set(), []
    for a in recall(user):
        subj = str(a.get('subject') or '').strip()
        if not subj:
            continue
        cohort = str(a.get('cohort') or '').strip() or None
        sig = (subj.lower(), (cohort or '').lower())
        if sig in seen:
            continue
        seen.add(sig)
        out.append({'subject': subj, 'cohort': cohort,
                    'ledger_key': a.get('ledger_key'),
                    'ts': a.get('ts')})
        if len(out) >= k:
            break
    return out


def last_window(user, default_start=DEFAULT_WINDOW_START,
                default_end=DEFAULT_WINDOW_END):
    """The user's most recent NON-DEFAULT build window, or None. A
    user who always accepts the standing default has no window
    preference to lead with, so the clarify keeps its current
    wording."""
    for a in recall(user):
        w = a.get('window')
        if not isinstance(w, dict):
            continue
        ws, we = str(w.get('start') or ''), str(w.get('end') or '')
        if not (ws and we):
            continue
        if ws == default_start and we == default_end:
            continue
        return {'start': ws, 'end': we}
    return None


def last_region(user):
    """The user's most recent named market(s) from a build, or None."""
    for a in recall(user):
        r = str(a.get('region') or '').strip()
        if r:
            return r
    return None


def referent_label(ref):
    """Human phrasing for a remembered referent, the way the confirm
    names it: 'parents of Paw Patrol viewers (kids 4-7)' for subject
    'Paw Patrol Series' + cohort 'Parents of Kids 4-7'; otherwise
    'Subject (Cohort)' or just the subject."""
    subj = str((ref or {}).get('subject') or '').strip()
    cohort = str((ref or {}).get('cohort') or '').strip()
    if not subj:
        return ''
    subj_clean = re.sub(r'\s+(series|tu)$', '', subj,
                        flags=re.IGNORECASE).strip()
    m = re.match(r'^parents of (kids?\s+[\d\s\-to]+)$', cohort,
                 flags=re.IGNORECASE)
    if m:
        return (f"parents of {subj_clean} viewers "
                f"({m.group(1).strip().lower()})")
    if cohort:
        return f"{subj_clean} ({cohort})"
    return subj_clean
