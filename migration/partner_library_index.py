"""Partner library index: what the dashboard's profile list does not
carry, one compact record per library file (Jenna 2026-11-23).

A partner building an app on the API asked for "a list of existing
profiles and their associated meta data ... similar to how someone can
see the library in the app"; Jenna approved GET /api/v1/profiles/library
the same day. The list itself is the dashboard's own profile list (the
shared library, admin-hidden files left out). This index adds each
file's window, its audience size and the categories it holds, so the
API answers from one small object instead of opening thousands of files.

Store: s3://dashboard-inputs/system/partner_library/index.json
    {"version": 1, "updated": iso,
     "columns": [<category name>, ...],         shared vocabulary
     "profiles": {<s3_key>: {"etag", "start", "end", "proj", "market",
                             "us_share", "cols": [<vocabulary index>],
                             "at"}}}

Kept current three ways, all additive and fail-safe:
  * registration (migration/dashboard_register) indexes a file the
    moment it ships;
  * the nightly per-profile summary job (bg-webapp/scripts/
    build_prometheus_profile_indexes.py) calls ``refresh``: every listed
    file whose bytes changed since its record is read again, files that
    left the library are dropped;
  * the API's library read calls ``fill_async`` for a listed file the
    index has not seen yet.

A record comes from the nightly per-profile summary
(system/prometheus_profile_indexes/) when that summary was built from
the same bytes, else from the file itself. The audience is the file's
own US projection cell (the worldwide one on a worldwide file); the
sample count is never stored. Nothing here changes a profile; the only
object written is the index. Never raises.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

BUCKET = 'dashboard-inputs'
INDEX_KEY = 'system/partner_library/index.json'
SUMMARY_PREFIX = 'system/prometheus_profile_indexes/'
CATALOG_KEY = 'system/s3_cache.json'
QUICK_SELECTS_KEY = 'metadata/admin_quick_selects.json'
VERSION = 1
# US people per person in the 10M sample (329.9M / 10M): the canonical
# chain, used only for an older file with no projection cell.
PANEL_TO_US = 32.99
ID_RX = re.compile(r'^pf_[0-9a-f]{20}$')
_ID_SALT = 'partner-library|'
_INDEX_TTL_S = 120
FILL_LIMIT = 40
FILL_COOLDOWN_S = 600
# Files read in full by one nightly refresh when no summary matches; the
# rest wait for the next run (a summary usually lands first).
MAX_FILE_READS = 400

# Rows that describe the file, never a category the profile holds.
META_COLS = frozenset({'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT', 'INPUT METADATA',
                       'AVID FAN', 'CASUAL FAN'})
_ISO_PAIR_RX = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:TO|To|to|-)\s*(\d{4}-\d{2}-\d{2})')
_ANY_DATE_RX = re.compile(r'(\d{4})-(\d{2})-(\d{2})|(\d{1,2})[/.](\d{1,2})[/.](\d{4})')
_MARKET_RX = re.compile(r'\|\s*MARKET:\s*([^|]+)', re.I)
_US_NAMES = frozenset({'us', 'usa', 'unitedstates', 'unitedstatesofamerica'})

_client_override = None
_LOCK = threading.Lock()
_CACHE = {'records': None, 'etag': '', 'at': 0.0}
_FILL = {'running': False, 'tried': {}}


def set_client(client):
    """Tests: a fake S3 client (None restores the real one)."""
    global _client_override
    _client_override = client
    with _LOCK:
        _CACHE.update(records=None, etag='', at=0.0)
        _FILL.update(running=False, tried={})


def enabled():
    """Off under PARTNER_LIBRARY_INDEX=0, and in the regression harness
    (REGRESSION_TEST_MODE=1) unless a test hands in its own client."""
    if os.environ.get('PARTNER_LIBRARY_INDEX', '1').strip() == '0':
        return False
    if os.environ.get('REGRESSION_TEST_MODE', '').strip() == '1' and _client_override is None:
        return False
    return True


def _client():
    if _client_override is not None:
        return _client_override
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _err_code(e):
    try:
        return str(((getattr(e, 'response', None) or {}).get('Error') or {}).get('Code') or '')
    except Exception:
        return ''


def profile_id(s3_key):
    """The partner-facing id of a library file. Stable for the life of the
    file (an in-place fix keeps the key, so it keeps the id); never the
    key itself, and the key cannot be read back out of it."""
    k = str(s3_key or '').strip()
    if not k:
        return ''
    return 'pf_' + hashlib.sha256((_ID_SALT + k).encode('utf-8')).hexdigest()[:20]


def summary_key(s3_key):
    """The nightly per-profile summary's key (the same rule as
    prometheus_analysis.profile_index_s3_key)."""
    h = hashlib.sha1(str(s3_key).encode('utf-8')).hexdigest()[:24]
    return f"{SUMMARY_PREFIX}{h}.json"


def norm_col(c):
    """A category name as the summaries write it: upper case, runs of
    underscores and spaces as one space."""
    return re.sub(r'[_\s]+', ' ', str(c or '').strip().upper())


def _num(v):
    try:
        s = str(v).replace(',', '').replace('%', '').strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def _iso(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def parse_window(text):
    """(start, end) ISO dates of the first date pair in ``text``, else
    (None, None). Reads '2025-11-23 TO 2026-11-23' and month/day/year."""
    t = str(text or '')
    m = _ISO_PAIR_RX.search(t)
    if m:
        a = _iso(*m.group(1).split('-'))
        b = _iso(*m.group(2).split('-'))
        if a and b and a <= b:
            return a, b
    found = []
    for mm in _ANY_DATE_RX.finditer(t):
        d = _iso(mm.group(1), mm.group(2), mm.group(3)) if mm.group(1) else _iso(mm.group(6), mm.group(4), mm.group(5))
        if d:
            found.append(d)
        if len(found) == 2:
            break
    if len(found) == 2 and found[0] <= found[1]:
        return found[0], found[1]
    return None, None


def parse_market(text):
    """'GLOBAL' or a country from '| MARKET: X' on the SAMPLE SIZE row;
    '' for a US file."""
    m = _MARKET_RX.search(str(text or ''))
    if not m:
        return ''
    v = re.sub(r'\s+', ' ', m.group(1)).strip().upper()
    return '' if re.sub(r'[^a-z]', '', v.lower()) in _US_NAMES else v


def _is_us(label):
    return re.sub(r'[^a-z]', '', str(label or '').lower()) in _US_NAMES


def _finish(start, end, proj, sample, market, us_share, cols):
    if not proj and sample:
        proj = sample * PANEL_TO_US
    out = {'start': start, 'end': end, 'proj': int(round(proj)) if proj and proj > 0 else None,
           'market': market or '', 'columns': list(cols)}
    if us_share is not None and 0 < us_share <= 100:
        out['us_share'] = round(float(us_share), 4)
    return out


def record_from_rows(rows):
    """One record from a profile's rows (dicts carrying the profile CSV
    columns). The audience is the BRAND INPUT row's projection cell (the
    SAMPLE SIZE row's when that is all a file carries); window and market
    come from the SAMPLE SIZE row; the US share of a worldwide file from
    its COUNTRY row for the United States."""
    start = end = None
    market = ''
    bi_proj = bi_sample = ss_proj = ss_sample = None
    us_share = None
    cols, seen = [], set()
    for r in rows or []:
        try:
            col = norm_col(r.get('Column'))
        except Exception:
            continue
        if not col:
            continue
        val = str(r.get('Value') or '')
        if col == 'SAMPLE SIZE':
            if not start:
                start, end = parse_window(val)
            market = market or parse_market(val)
            if ss_proj is None:
                ss_proj = _num(r.get('US Gen Pop Projection'))
                ss_sample = _num(r.get('Original Raw Numbers'))
        elif col == 'BRAND INPUT' and bi_proj is None and bi_sample is None:
            bi_proj = _num(r.get('US Gen Pop Projection'))
            bi_sample = _num(r.get('Original Raw Numbers'))
        elif col == 'COUNTRY' and us_share is None and _is_us(val):
            us_share = _num(r.get('Brand Penetration (Row)'))
        if col not in META_COLS and col not in seen:
            seen.add(col)
            cols.append(col)
    proj = bi_proj or ss_proj
    sample = bi_sample or ss_sample
    return _finish(start, end, proj, sample, market, us_share, cols)


def record_from_summary(doc):
    """One record from a nightly per-profile summary document."""
    meta = doc.get('meta') or {}
    start, end = parse_window(meta.get('window') or '')
    proj = _num(meta.get('proj')) if meta.get('proj') is not None else _num(doc.get('projection'))
    sample = _num(meta.get('sample')) if meta.get('sample') is not None else _num(doc.get('sample'))
    cols, seen = [], set()
    for c in list((doc.get('demos') or {}).keys()) + list((doc.get('categories') or {}).keys()):
        n = norm_col(c)
        if n and n not in META_COLS and n not in seen:
            seen.add(n)
            cols.append(n)
    us_share = None
    for row in ((doc.get('categories') or {}).get('COUNTRY') or {}).get('rows') or []:
        if row and _is_us(row[0]):
            us_share = _num(row[1])
            break
    return _finish(start, end, proj, sample, '', us_share, cols)


def build_record(s3_key, etag='', s3=None):
    """(record, 'summary' | 'file') for one library file, or (None, '').
    The summary is used only when it was built from these exact bytes."""
    s3 = s3 or _client()
    etag = str(etag or '').strip('"')
    if etag:
        try:
            doc = json.loads(s3.get_object(Bucket=BUCKET, Key=summary_key(s3_key))['Body'].read())
            if str(doc.get('etag') or '').strip('"') == etag and str(doc.get('s3_key') or s3_key) == s3_key:
                rec = record_from_summary(doc)
                rec['etag'] = etag
                return rec, 'summary'
        except Exception:
            pass
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
        live = str(obj.get('ETag') or '').strip('"')
        text = obj['Body'].read().decode('utf-8', 'replace')
        rec = record_from_rows(csv.DictReader(io.StringIO(text)))
        rec['etag'] = live or etag
        return rec, 'file'
    except Exception as e:
        print(f"[partner-library] could not read {s3_key}: {_err_code(e) or type(e).__name__}")
        return None, ''


# ---------------------------------------------------------------- the store
def _expand(doc):
    vocab = list((doc or {}).get('columns') or [])
    out = {}
    for k, rec in ((doc or {}).get('profiles') or {}).items():
        if not isinstance(rec, dict):
            continue
        r = {kk: rec.get(kk) for kk in ('etag', 'start', 'end', 'proj', 'market', 'us_share', 'at')}
        r['columns'] = [vocab[i] for i in (rec.get('cols') or []) if isinstance(i, int) and 0 <= i < len(vocab)]
        out[k] = r
    return out


def load_index(s3=None, force=False):
    """({s3_key: record with 'columns' names}, version). Cached in process
    (two minutes); ({}, '') when the store is absent or unreadable."""
    if not enabled():
        return {}, ''
    now = time.time()
    with _LOCK:
        if not force and _CACHE['records'] is not None and now - _CACHE['at'] < _INDEX_TTL_S:
            return _CACHE['records'], _CACHE['etag']
        have_records, have_etag = _CACHE['records'], _CACHE['etag']
    try:
        s3 = s3 or _client()
        kw = {'Bucket': BUCKET, 'Key': INDEX_KEY}
        if have_records is not None and have_etag:
            kw['IfNoneMatch'] = have_etag
        obj = s3.get_object(**kw)
        records = _expand(json.loads(obj['Body'].read()))
        etag = str(obj.get('ETag') or '')
    except Exception as e:
        code = _err_code(e)
        if code in ('304', 'NotModified') and have_records is not None:
            records, etag = have_records, have_etag
        elif code in ('NoSuchKey', '404'):
            records, etag = {}, ''
        else:
            if have_records is None:
                print(f"[partner-library] index unreadable: {code or type(e).__name__}")
            records, etag = (have_records if have_records is not None else {}), have_etag
    with _LOCK:
        _CACHE.update(records=records, etag=etag, at=time.time())
    return records, etag


def upsert(records, drop=(), s3=None):
    """Merge ``records`` ({s3_key: record}) into the store and drop the
    keys in ``drop``, in one conditional write. Returns the number of
    records written; -1 when the store could not be written."""
    records = {k: v for k, v in (records or {}).items() if k and isinstance(v, dict)}
    drop = [k for k in (drop or ()) if k]
    if not records and not drop:
        return 0
    try:
        from migration.s3_json_state import update_json
    except Exception as e:
        print(f"[partner-library] store helper unavailable: {e}")
        return -1

    def _mutate(doc):
        if not isinstance(doc, dict) or doc.get('version') != VERSION:
            doc = {'version': VERSION, 'columns': [], 'profiles': {}}
        vocab = list(doc.get('columns') or [])
        pos = {c: i for i, c in enumerate(vocab)}
        profs = dict(doc.get('profiles') or {})
        for k in drop:
            profs.pop(k, None)
        stamp = _now()
        for k, rec in records.items():
            idx = []
            for c in rec.get('columns') or []:
                if c not in pos:
                    pos[c] = len(vocab)
                    vocab.append(c)
                idx.append(pos[c])
            compact = {kk: rec.get(kk) for kk in ('etag', 'start', 'end', 'proj', 'market', 'us_share')
                       if rec.get(kk) not in (None, '')}
            compact['cols'] = idx
            compact['at'] = stamp
            profs[k] = compact
        doc.update(version=VERSION, columns=vocab, profiles=profs, updated=stamp)
        return doc

    try:
        update_json(BUCKET, INDEX_KEY, _mutate, s3=s3 or _client(), indent=None,
                    put_extra_args={'CacheControl': 'no-cache, max-age=0'})
    except Exception as e:
        print(f"[partner-library] index write failed: {_err_code(e) or type(e).__name__}")
        return -1
    with _LOCK:
        _CACHE.update(at=0.0)
    return len(records)


def index_profiles(keys, s3=None):
    """Index these files now (a registration, a gap the API saw). Returns
    how many records were written. Never raises."""
    if not enabled():
        return 0
    try:
        s3 = s3 or _client()
        recs = {}
        for k in [str(x).strip() for x in (keys or []) if str(x or '').strip()]:
            etag = ''
            try:
                etag = str(s3.head_object(Bucket=BUCKET, Key=k).get('ETag') or '').strip('"')
            except Exception:
                pass
            rec, _src = build_record(k, etag, s3=s3)
            if rec:
                recs[k] = rec
        return max(upsert(recs, s3=s3), 0) if recs else 0
    except Exception as e:
        print(f"[partner-library] index failed: {type(e).__name__}")
        return 0


def fill_async(keys, limit=FILL_LIMIT, cooldown_s=FILL_COOLDOWN_S):
    """Index, on a background thread, listed files the index has not seen
    (the API's read calls this). One fill at a time per process; a file
    is not tried again for ten minutes. Off under PARTNER_LIBRARY_FILL=0.
    Returns True when a fill started."""
    if os.environ.get('PARTNER_LIBRARY_FILL', '1') == '0' or not enabled():
        return False
    now = time.time()
    with _LOCK:
        if _FILL['running']:
            return False
        todo = [k for k in (keys or []) if now - _FILL['tried'].get(k, 0.0) > cooldown_s][:max(int(limit), 0)]
        if not todo:
            return False
        for k in todo:
            _FILL['tried'][k] = now
        _FILL['running'] = True

    def _run():
        try:
            if index_profiles(todo) > 0:
                load_index(force=True)
        finally:
            with _LOCK:
                _FILL['running'] = False

    try:
        threading.Thread(target=_run, name='partner-library-fill', daemon=True).start()
        return True
    except Exception:
        with _LOCK:
            _FILL['running'] = False
        return False


# ---------------------------------------------------------------- nightly
def _root_csv_etags(s3):
    out, tok = {}, None
    while True:
        kw = {'Bucket': BUCKET, 'Delimiter': '/'}
        if tok:
            kw['ContinuationToken'] = tok
        resp = s3.list_objects_v2(**kw)
        for o in resp.get('Contents') or []:
            k = str(o.get('Key') or '')
            if '/' not in k and k.lower().endswith('.csv'):
                out[k] = str(o.get('ETag') or '').strip('"')
        if not resp.get('IsTruncated'):
            return out
        tok = resp.get('NextContinuationToken')
        if not tok:
            return out


def library_keys(s3):
    """Files in the dashboard's profile list, minus admin-hidden ones (the
    same list the API reads)."""
    doc = json.loads(s3.get_object(Bucket=BUCKET, Key=CATALOG_KEY)['Body'].read())
    hidden = set()
    try:
        qs = json.loads(s3.get_object(Bucket=BUCKET, Key=QUICK_SELECTS_KEY)['Body'].read())
        prof = qs.get('profiles') if isinstance(qs.get('profiles'), dict) else qs
        hidden = {k for k, v in (prof or {}).items() if v is False}
    except Exception:
        pass
    out = []
    for j in doc.get('jobs') or []:
        k = str((j or {}).get('s3_key') or '').strip()
        if k and '/' not in k and k.lower().endswith('.csv') and k not in hidden:
            out.append(k)
    return out


def refresh(s3=None, workers=16, max_file_reads=MAX_FILE_READS, verbose=True):
    """Bring the store into line with the library. Returns a stats dict.
    Never raises."""
    stats = {'listed': 0, 'changed': 0, 'from_summary': 0, 'from_file': 0, 'waiting': 0,
             'dropped': 0, 'written': 0, 'error': ''}
    if not enabled():
        stats['error'] = 'off'
        return stats
    try:
        s3 = s3 or _client()
        etags = _root_csv_etags(s3)
        listed = [k for k in library_keys(s3) if k in etags]
        stats['listed'] = len(listed)
        current, _v = load_index(s3=s3, force=True)
        todo = [k for k in listed if (current.get(k) or {}).get('etag') != etags[k]]
        stats['changed'] = len(todo)
        recs = {}
        file_reads = [0]
        lock = threading.Lock()

        def _one(k):
            try:
                doc_ok = None
                try:
                    doc = json.loads(s3.get_object(Bucket=BUCKET, Key=summary_key(k))['Body'].read())
                    if str(doc.get('etag') or '').strip('"') == etags[k]:
                        doc_ok = doc
                except Exception:
                    doc_ok = None
                if doc_ok is not None:
                    rec = record_from_summary(doc_ok)
                    rec['etag'] = etags[k]
                    return k, rec, 'summary'
                with lock:
                    if file_reads[0] >= max_file_reads:
                        return k, None, 'waiting'
                    file_reads[0] += 1
                rec, src = build_record(k, '', s3=s3)
                return k, rec, src or 'waiting'
            except Exception:
                return k, None, 'waiting'

        with ThreadPoolExecutor(max(1, int(workers))) as ex:
            for k, rec, src in ex.map(_one, todo):
                if rec:
                    recs[k] = rec
                    stats['from_summary' if src == 'summary' else 'from_file'] += 1
                else:
                    stats['waiting'] += 1
        listed_set = set(listed)
        drop = [k for k in current if k not in listed_set]
        stats['dropped'] = len(drop)
        stats['written'] = upsert(recs, drop=drop, s3=s3) if (recs or drop) else 0
    except Exception as e:
        stats['error'] = _err_code(e) or type(e).__name__
    if verbose:
        print(f"[partner-library] refresh: {stats}")
    return stats


__all__ = ['BUCKET', 'INDEX_KEY', 'ID_RX', 'META_COLS', 'enabled', 'profile_id', 'summary_key', 'norm_col',
           'parse_window', 'parse_market', 'record_from_rows', 'record_from_summary', 'build_record',
           'load_index', 'upsert', 'index_profiles', 'fill_async', 'library_keys', 'refresh', 'set_client']
