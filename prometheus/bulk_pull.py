"""Bulk Profile IQ pulls from an uploaded list (Jenna 2026-11-05:
"could we add a prometheus feature where you could upload a csv with a
list of the subjects you want pulled from profile iq and then it would
bulk queue those profiles to pull so that it would be easy to upload a
list of 1000 people and it could do those profiles however the
throttling allows?").

The door is the chat: a CSV / TXT / XLSX with a names column on the
thread plus pull words ('pull profiles for everyone on this list',
'bulk pull these', 'run profile iq on this list'). The list is read
back with the count, the duplicates collapsed, the blank rows dropped,
how many names are already in the library (reused at no charge, the
library's own model) and how many are new builds, the standing window,
the price and the time at the current pace, then one confirm ('Queue
the list').

On confirm one ledger lands at system/bulk_pulls/<bulk_id>.json with a
row per name (state queued / reused / posted / running / done / failed),
the new builds are charged once, and the feeder on the build server
(prometheus.bulk_feeder, a thread of the read worker) walks the ledger:
every row goes through the real interpret step to a complete spec, the
library is checked first (an existing_match is reused, never rebuilt,
never charged), and a capped number of builds stays in flight on the
profile engine. Each finished profile registers in Select Profile as any
build does; the per-profile completion emails collapse into one digest
per list (every DIGEST_EVERY completions or every hour, plus a final
summary). A row the engine could not complete carries the partner-safe
line and its charge comes back.

Status ('how is my bulk pull going', 'status of the list') answers from
the ledger on both chat surfaces and on the partner status endpoint
(GET /api/v1/profiles/<bulk_id>).

No override flags: this is a product feature. Nothing here says how a
profile is built; nothing a reader sees carries internal vocabulary.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import time
import traceback
from datetime import date

from . import twin_url_list as _TUL

FLOW = 'bulk_profile_pull'
RUN_LABEL = 'Queue the list'
LEDGER_PREFIX = 'system/bulk_pulls/'
BUCKET = 'dashboard-inputs'
MAX_SUBJECTS = 2000
PROFILE_CREDITS = _TUL.PROFILE_CREDITS            # 5 credits = one Chatbot Profile IQ build
PROFILE_PULL_TYPE = _TUL.PROFILE_PULL_TYPE        # 'Chatbot Profile IQ (new_build)'
PRICE_KEY, PRICE_DEFAULT = _TUL.PROFILE_PRICE_KEY, _TUL.PROFILE_PRICE_DEFAULT
FAIL_LINE = _TUL.FAIL_LINE
BUILD_MINUTES = 65                                # a build at the current pace, start to Select Profile (measured 61 to 67 min on the batch lane, 2026-11-05)
DEFAULT_IN_FLIGHT = 10                            # bulk builds in flight at once (the feeder reads the live cap)
DIGEST_EVERY = 25                                 # completions per digest
DIGEST_HOURS = 1.0                                # or an hour with new completions, whichever first
TERMINAL = ('done', 'reused', 'failed', 'skipped')
ACTIVE_STATES = ('queued', 'interpreting', 'posted', 'running', 'waiting')

_NAME_HEADER_RX = re.compile(
    r"^(?:subjects?|names?|talents?|brands?|titles?|persons?|people|entit(?:y|ies)|profiles?|compan(?:y|ies)|shows?|"
    r"artists?|celebrit(?:y|ies)|creators?|influencers?|athletes?|teams?|who|list|full name|display name|profile name|"
    r"subject name|brand name|talent name|celebrity name|person name)$", re.I)
_CATEGORY_HEADER_RX = re.compile(r"^(?:categor(?:y|ies)|brand categor(?:y|ies)|category hint|hint|type|kind|vertical)$", re.I)
_NOTES_HEADER_RX = re.compile(r"^(?:notes?|comments?|context|description|details?|about)$", re.I)
_AVID_HEADER_RX = re.compile(r"^(?:avid|run[ _]?avid|avid[ _]?fan|avid[ _]?cut|with avid|include avid)$", re.I)
_URL_RX = re.compile(r"https?://|^www\.", re.I)
_NUMERIC_RX = re.compile(r"^[\d\s.,%$+-]+$")
_YES = ('1', 'true', 'yes', 'y', 'x', 'on', 'avid', 'include')
_NO = ('0', 'false', 'no', 'n', 'off', 'none', 'skip')

# ---- detection -------------------------------------------------------
_PULL_RX = re.compile(r"\b(pull|run|build|queue|make|create|generate|start|kick off|spin up|do|get|launch|process)\b", re.I)
_PROFILE_RX = re.compile(r"\b(profiles?|profile iq|total universes?|audiences?)\b", re.I)
_LIST_RX = re.compile(
    r"\b(everyone|everybody|each (?:one|name|person|subject|row)|all of (?:them|these|those)|all the (?:names|people|subjects|rows)|"
    r"this list|the list|that list|my list|the whole list|these (?:names|people|subjects|folks|brands|titles|rows)|the attached|attached|"
    r"uploaded|the (?:csv|file|sheet|spreadsheet|upload|xlsx|excel)|in bulk|bulk|batch)\b", re.I)
_TWIN_RX = re.compile(r"\b(?:digital\s+)?twins?\b", re.I)
_STATUS_RX = re.compile(
    r"\b(how(?:'s| is| are)\s+(?:my|the|that|those|this)\s+(?:bulk|list|batch)|status\s+(?:of|on)\s+(?:my|the|that|this)\s+(?:bulk|list|batch)|"
    r"bulk\s+(?:pull\s+)?(?:status|progress|update)|(?:list|batch)\s+(?:pull\s+)?(?:status|progress|update)|"
    r"how\s+(?:far|many)\s+(?:along\s+)?(?:is|are|has|have)\s+(?:my|the)\s+(?:list|bulk|batch)|where\s+(?:is|are)\s+(?:my|the)\s+(?:bulk|list|batch)|"
    r"how\s+(?:is|are)\s+(?:my|the)\s+(?:bulk|list|batch)\s+(?:pulls?|builds?|profiles?)?\s*(?:going|doing|coming|progressing)?)\b", re.I)
_CANCEL_RX = re.compile(r"^\s*(never ?mind|no( thanks?)?|not now|cancel( (that|it|the list))?|stop( the list)?)[.! ]*$", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def _clean_subject(raw):
    """A plain subject name, or '' when the cell is not one (blank, a
    link, a number, an overlong string)."""
    s = ' '.join(str(raw or '').replace('\u00a0', ' ').split()).strip(' \t"\'“”‘’.,;:')
    if not s or len(s) < 2 or len(s) > 120:
        return ''
    if _URL_RX.search(s) or _NUMERIC_RX.match(s) or '@' in s and ' ' not in s and s.count('@') == 1 and '.' in s.split('@')[-1]:
        return ''
    return s


def _yes_no(raw, default=True):
    v = str(raw or '').strip().lower()
    if not v:
        return default
    if v in _YES:
        return True
    if v in _NO:
        return False
    return default


def _pick_columns(headers, rows):
    """(name_col, category_col, notes_col, avid_col) indexes, -1 when absent.
    Headers win; without one the names column is the first column whose
    values read as names (text, not links, not numbers)."""
    name_i = cat_i = notes_i = avid_i = -1
    for i, h in enumerate(headers or []):
        hs = str(h or '').strip()
        if name_i < 0 and _NAME_HEADER_RX.match(hs):
            name_i = i
        elif cat_i < 0 and _CATEGORY_HEADER_RX.match(hs):
            cat_i = i
        elif notes_i < 0 and _NOTES_HEADER_RX.match(hs):
            notes_i = i
        elif avid_i < 0 and _AVID_HEADER_RX.match(hs):
            avid_i = i
    if name_i < 0:
        width = max([len(headers or [])] + [len(r) for r in rows[:300]] or [0])
        for i in range(width):
            if i in (cat_i, notes_i, avid_i):
                continue
            vals = [str(r[i]).strip() for r in rows[:300] if i < len(r) and str(r[i]).strip()]
            if vals and sum(1 for v in vals if _clean_subject(v)) >= max(1, (len(vals) + 1) // 2):
                name_i = i
                break
    return name_i, cat_i, notes_i, avid_i


def _looks_like_header(first, rows):
    """The first row is a header when a cell names a known column, or
    when it reads as labels while the next rows read as names."""
    cells = [str(c or '').strip() for c in first]
    if any(_NAME_HEADER_RX.match(c) or _CATEGORY_HEADER_RX.match(c) or _NOTES_HEADER_RX.match(c) or _AVID_HEADER_RX.match(c) for c in cells):
        return True
    return False


def subjects_from_rows(rows):
    """{'items': [{'subject','category_hint','notes','run_avid'}], 'dropped': n, 'duplicates': n, 'columns': {...}}
    from a table (list of row lists). Duplicates collapse case and
    punctuation insensitively; blank and unusable rows are dropped."""
    raw = list(rows or [])
    rows = [list(r) for r in raw if any(str(c or '').strip() for c in r)]
    out = {'items': [], 'dropped': len(raw) - len(rows), 'duplicates': 0, 'columns': {}}
    if not rows:
        return out
    header = _looks_like_header(rows[0], rows[1:])
    if not header and len(rows) >= 3:
        # an unnamed label row ('who we want') reads lowercase while the names below it read Title Case
        n_i, _c, _n, _a = _pick_columns([], rows)
        if n_i >= 0 and n_i < len(rows[0]):
            first = str(rows[0][n_i] or '').strip()
            below = [str(r[n_i]).strip() for r in rows[1:6] if n_i < len(r) and str(r[n_i]).strip()]
            if first and first == first.lower() and below and sum(1 for b in below if b[:1].isupper()) >= max(2, (len(below) + 1) // 2):
                header = True
    headers = [str(c or '') for c in rows[0]] if header else []
    data = rows[1:] if header else rows
    name_i, cat_i, notes_i, avid_i = _pick_columns(headers, data)
    out['columns'] = {'subject': headers[name_i] if header and 0 <= name_i < len(headers) else (f"column {name_i + 1}" if name_i >= 0 else ''),
                      'category': headers[cat_i] if header and cat_i >= 0 else '', 'notes': headers[notes_i] if header and notes_i >= 0 else '',
                      'run_avid': headers[avid_i] if header and avid_i >= 0 else ''}
    if name_i < 0:
        out['dropped'] = len(data)
        return out
    seen = set()
    for r in data:
        cell = r[name_i] if name_i < len(r) else ''
        subj = _clean_subject(cell)
        if not subj:
            out['dropped'] += 1
            continue
        k = _norm(subj)
        if not k:
            out['dropped'] += 1
            continue
        if k in seen:
            out['duplicates'] += 1
            continue
        seen.add(k)
        item = {'subject': subj, 'category_hint': '', 'notes': '', 'run_avid': True}
        if cat_i >= 0 and cat_i < len(r):
            item['category_hint'] = ' '.join(str(r[cat_i] or '').split())[:80]
        if notes_i >= 0 and notes_i < len(r):
            item['notes'] = ' '.join(str(r[notes_i] or '').split())[:300]
        if avid_i >= 0 and avid_i < len(r):
            item['run_avid'] = _yes_no(r[avid_i], True)
        out['items'].append(item)
        if len(out['items']) >= MAX_SUBJECTS:
            break
    return out


def subjects_from_text(text):
    """One name per line (a TXT or a pasted list); a comma-only line is
    split on commas."""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", str(text or '')) if ln.strip()]
    if len(lines) == 1 and lines[0].count(',') >= 2 and len(lines[0]) < 4000:
        lines = [p.strip() for p in lines[0].split(',') if p.strip()]
    return subjects_from_rows([[ln] for ln in lines])


def subjects_from_bytes(name, data):
    """The list from an uploaded CSV / TSV / TXT / MD / XLSX. Only the
    names column and the optional hint columns are read."""
    ext = os.path.splitext(str(name or '').lower())[1]
    data = data or b''
    if ext in ('.xlsx', '.xlsm'):
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for r in ws.iter_rows(values_only=True):
                    rows.append(['' if c is None else str(c) for c in r])
                if rows:
                    break
            return subjects_from_rows(rows)
        except Exception:
            traceback.print_exc()
            return {'items': [], 'dropped': 0, 'duplicates': 0, 'columns': {}}
    text = data.decode('utf-8-sig', errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)
    if ext in ('.csv', '.tsv'):
        delim = '\t' if ext == '.tsv' else ','
        try:
            sample = text[:4096]
            if ext == '.csv' and sample.count(';') > sample.count(',') and sample.count(';') > 0:
                delim = ';'
            rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        except Exception:
            rows = [[ln] for ln in text.splitlines()]
        return subjects_from_rows(rows)
    return subjects_from_text(text)


# ---- detection -------------------------------------------------------
def is_bulk_ask(text, has_names_sheet):
    """A bulk order: pull words plus profile words plus list words, with
    a names list on the thread. Deterministic. Never a twin, never a
    plain 'pull a profile of X', never the inline 'run individual
    profiles for X, Y, Z' (no sheet, no list words)."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1500 or not has_names_sheet:
        return False
    if _TWIN_RX.search(t):
        return False
    if re.match(r"^\s*(what|why|how|where|when|who|which)\b", t, re.I) or re.search(r"\bmeans?\b", t, re.I):
        return False      # a question about the feature, never an order
    if re.search(r"\bbulk\b", t, re.I) and (_PROFILE_RX.search(t) or re.search(r"\b(these|this|the list|them)\b", t, re.I)):
        return True
    if not _PULL_RX.search(t):
        return False
    return bool(_PROFILE_RX.search(t) and _LIST_RX.search(t))


def is_status_ask(text):
    t = ' '.join(str(text or '').split())
    return bool(t) and len(t) <= 240 and bool(_STATUS_RX.search(t))


# ---- the library -----------------------------------------------------
def _base_name(display, key):
    """The clean subject name of a base Total Universe entry, else ''
    (cuts carry ' - '; the display name wins over the key stem)."""
    name = ' '.join(str(display or '').split())
    if not name:
        stem = str(key or '').rsplit('/', 1)[-1]
        stem = re.sub(r"\.csv$", '', stem, flags=re.I)
        stem = re.sub(r"_\d{2}_\d{2}_\d{4}(?:_\d{2}_\d{2})?$", '', stem)
        name = stem.replace('_', ' ').strip()
    if ' - ' in name:
        return ''
    name = re.sub(r"\s*\(\d{4}\)\s*$", '', name)
    return name


def library_index(catalog):
    """{normalized name: (key, display)} for every base Total Universe
    in the catalog (list of {s3_key, display_name} dicts, the Select
    Profile dropdown's own list)."""
    idx = {}
    for j in catalog or []:
        if not isinstance(j, dict):
            continue
        key = str(j.get('s3_key') or j.get('key') or '')
        if not key.endswith('.csv') or '/' in key:
            continue
        name = _base_name(j.get('display_name') or j.get('subject') or '', key)
        if not name:
            continue
        k = _norm(name)
        if not k:
            continue
        prev = idx.get(k)
        # the newest dated file wins (the date refresh rule: the newest Total Universe is the primary)
        if prev is None or _stamp(key) > _stamp(prev[0]):
            idx[k] = (key, name)
    return idx


def _stamp(key):
    m = re.search(r"_(\d{2})_(\d{2})_(\d{4})(?:_(\d{2})_(\d{2}))?\.csv$", str(key or ''))
    if not m:
        return ''
    return f"{m.group(3)}{m.group(1)}{m.group(2)}{m.group(4) or '00'}{m.group(5) or '00'}"


def library_match(subject, index):
    """(key, display) when the library holds a base profile of exactly
    this name (case and punctuation insensitive), else None. A near miss
    is left to the interpret step, which has the candidate list."""
    k = _norm(subject)
    if not k:
        return None
    hit = index.get(k)
    return hit if hit else None


def catalog_from_cache(s3, bucket=BUCKET):
    """The catalog off system/s3_cache.json (the dropdown's own source),
    for a process without the web app's in-memory copy. [] on trouble."""
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key='system/s3_cache.json')['Body'].read().decode('utf-8'))
        return [j for j in (doc.get('jobs') or []) if isinstance(j, dict)]
    except Exception:
        return []


# ---- window, price, time ---------------------------------------------
default_window = _TUL.default_window
window_from_text = _TUL.window_from_text
window_words = _TUL.window_words
_normalize_window = _TUL._normalize_window


def price(username=None):
    """The per-build price (the Chatbot Profile IQ rate the charge uses)."""
    p = _TUL._usd(PRICE_KEY, PRICE_DEFAULT, username)
    return {'usd': p, 'label': _TUL._label(p, username)}


def total_label(n_new, username=None):
    p = price(username)
    return _TUL._label(p['usd'] * max(0, int(n_new)), username), p


def in_flight_cap():
    """How many bulk builds stay in flight at once: BULK_IN_FLIGHT, else
    half the engine's worker count, else the default."""
    try:
        v = int(os.environ.get('BULK_IN_FLIGHT') or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        n = int(os.environ.get('SYNTH_WORKER_COUNT') or 0)
        if n > 0:
            return max(1, n // 2)
    except (TypeError, ValueError):
        pass
    return DEFAULT_IN_FLIGHT


def eta_minutes(n_new, cap=None, build_minutes=BUILD_MINUTES):
    n = max(0, int(n_new or 0))
    if n == 0:
        return 0
    cap = max(1, int(cap or in_flight_cap()))
    return int(math.ceil(n / cap) * build_minutes)


def eta_words(n_new, cap=None):
    mins = eta_minutes(n_new, cap)
    if mins <= 0:
        return 'no build time: every name is already in the library'
    if mins < 60:
        return f"about {mins} minutes"
    hours = mins / 60.0
    if hours < 36:
        h = int(round(hours))
        return f"about {h} hour{'s' if h != 1 else ''}"
    days = hours / 24.0
    d = round(days * 2) / 2.0
    d_words = f"{int(d)}" if d == int(d) else f"{d:.1f}"
    return f"about {d_words} day{'s' if d != 1 else ''}"


# ---- the ledger -------------------------------------------------------
def _s3():
    return _TUL._s3()


def ledger_key(bulk_id):
    return f"{LEDGER_PREFIX}{re.sub(r'[^A-Za-z0-9_-]+', '', str(bulk_id or ''))}.json"


def is_bulk_id(s):
    return bool(re.fullmatch(r"bulk_[A-Za-z0-9_-]{6,40}", str(s or '')))


def new_bulk_id():
    return 'bulk_' + secrets.token_urlsafe(9).replace('-', 'x').replace('_', 'y')


def _row_run_id():
    return secrets.token_urlsafe(10)


def plan_rows(items, index):
    """The ledger rows: a library hit is reused on the spot (its key on
    the row, no charge); everything else is queued for a build."""
    rows = []
    for i, it in enumerate(items or []):
        subj = str(it.get('subject') or '').strip()
        row = {'i': i, 'subject': subj, 'category_hint': str(it.get('category_hint') or ''), 'notes': str(it.get('notes') or ''),
               'run_avid': bool(it.get('run_avid', True)), 'state': 'queued', 'run_id': _row_run_id(), 'key': '', 'avid_key': '',
               'error': '', 'charged': True, 'refunded': False, 'attempts': 0, 'posted_at': 0, 'finished_at': 0}
        hit = library_match(subj, index or {})
        if hit:
            row.update({'state': 'reused', 'key': hit[0], 'library_name': hit[1], 'charged': False, 'finished_at': time.time()})
        rows.append(row)
    return rows


def counts(ledger):
    c = {k: 0 for k in ('queued', 'interpreting', 'posted', 'running', 'waiting', 'done', 'reused', 'failed', 'skipped')}
    for r in (ledger or {}).get('rows') or []:
        c[r.get('state') or 'queued'] = c.get(r.get('state') or 'queued', 0) + 1
    c['total'] = len((ledger or {}).get('rows') or [])
    c['in_flight'] = c['posted'] + c['running'] + c['interpreting']
    c['open'] = c['queued'] + c['waiting'] + c['in_flight']
    c['terminal'] = c['done'] + c['reused'] + c['failed'] + c['skipped']
    return c


def new_ledger(*, requester, items, index, window, file_name='', thread_id='', ask_text='', source='prometheus', usd_each=0.0, run_avid=True):
    rows = plan_rows(items, index)
    n_new = sum(1 for r in rows if r['state'] == 'queued')
    now = time.time()
    req = requester if isinstance(requester, dict) else {'username': str(requester or '')}
    return {'bulk_id': new_bulk_id(), 'status': 'active', 'created_at': now, 'updated_at': now,
            'requester': {'username': str(req.get('username') or ''), 'email': str(req.get('email') or '')},
            'thread_id': str(thread_id or ''), 'ask_text': str(ask_text or '')[:400], 'source': source,
            'file_name': str(file_name or '')[:160], 'window': _normalize_window(window), 'run_avid': bool(run_avid),
            'charged': {'rows': n_new, 'per_build': PROFILE_CREDITS, 'credits': PROFILE_CREDITS * n_new, 'usd_each': float(usd_each or 0.0),
                        'usd_total': float(usd_each or 0.0) * n_new, 'at': 0, 'free_seat': False},
            'rows': rows, 'digest': {'last_at': now, 'sent': 0, 'reported': [], 'history': []},
            'feeder': {'last_tick': 0, 'backoff_until': 0, 'note': ''}}


def save_ledger(ledger, s3=None, bucket=None):
    if s3 is None:
        s3, bucket = _s3()
    ledger['updated_at'] = time.time()
    s3.put_object(Bucket=bucket or BUCKET, Key=ledger_key(ledger['bulk_id']), Body=json.dumps(ledger, default=str).encode('utf-8'),
                  ContentType='application/json')
    return ledger


def load_ledger(bulk_id, s3=None, bucket=None):
    if s3 is None:
        s3, bucket = _s3()
    try:
        doc = json.loads(s3.get_object(Bucket=bucket or BUCKET, Key=ledger_key(bulk_id))['Body'].read().decode('utf-8'))
        return doc if isinstance(doc, dict) and doc.get('bulk_id') else None
    except Exception:
        return None


def update_ledger(bulk_id, mutate, s3=None, bucket=None):
    """Read, mutate(ledger) -> ledger or None, write under compare-and-
    swap (two writers: the feeder and the engine's workers). Falls back
    to a plain read-modify-write where the conditional put is not
    available. Returns the written ledger or None."""
    if s3 is None:
        s3, bucket = _s3()
    bucket = bucket or BUCKET
    key = ledger_key(bulk_id)

    def _fn(doc):
        if not isinstance(doc, dict) or not doc.get('bulk_id'):
            return None
        out = mutate(doc)
        if out is None:
            return None
        out['updated_at'] = time.time()
        return out
    try:
        from migration.s3_json_state import update_json
        return update_json(bucket, key, _fn, s3=s3, indent=None)
    except Exception:
        pass
    doc = load_ledger(bulk_id, s3=s3, bucket=bucket)
    out = _fn(doc) if doc else None
    if out is None:
        return None
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(out, default=str).encode('utf-8'), ContentType='application/json')
    return out


def list_ledgers(s3=None, bucket=None, *, active_only=True, username=None, limit=200):
    """Ledgers, newest first; active ones by default; one requester's
    when a username is given."""
    if s3 is None:
        s3, bucket = _s3()
    bucket = bucket or BUCKET
    out = []
    try:
        token = None
        while True:
            kw = {'Bucket': bucket, 'Prefix': LEDGER_PREFIX}
            if token:
                kw['ContinuationToken'] = token
            resp = s3.list_objects_v2(**kw)
            for o in resp.get('Contents') or []:
                k = o.get('Key') or ''
                if not k.endswith('.json'):
                    continue
                try:
                    doc = json.loads(s3.get_object(Bucket=bucket, Key=k)['Body'].read().decode('utf-8'))
                except Exception:
                    continue
                if not isinstance(doc, dict) or not doc.get('bulk_id'):
                    continue
                if active_only and str(doc.get('status') or '') != 'active':
                    continue
                if username and str((doc.get('requester') or {}).get('username') or '').lower() != str(username).lower():
                    continue
                out.append(doc)
            token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
            if not token:
                break
    except Exception:
        traceback.print_exc()
    out.sort(key=lambda d: -float(d.get('created_at') or 0))
    return out[:limit]


# ---- copy -------------------------------------------------------------
def _n(n, noun, plural=None):
    n = int(n or 0)
    return f"{n:,} {noun if n == 1 else (plural or noun + 's')}"


def list_words(read, file_name=''):
    """One line reading the list back: names, duplicates, blanks."""
    items = read.get('items') or []
    parts = [f"{_n(len(items), 'name')} read from {file_name or 'your list'}"]
    extra = []
    if read.get('duplicates'):
        extra.append(f"{_n(read['duplicates'], 'duplicate')} collapsed")
    if read.get('dropped'):
        extra.append(f"{_n(read['dropped'], 'blank or unusable row')} dropped")
    if extra:
        parts[0] += f" ({', '.join(extra)})"
    return parts[0] + '.'


def confirm_reply(read, rows, window, username=None, file_name='', cap=None, run_avid=True):
    n_reused = sum(1 for r in rows if r['state'] == 'reused')
    n_new = sum(1 for r in rows if r['state'] == 'queued')
    total, p = total_label(n_new, username)
    cap = cap or in_flight_cap()
    lines = ['Here is the list:',
             f"- {list_words(read, file_name)}"]
    if n_reused:
        lines.append(f"- {_n(n_reused, 'name')} already in the library: reused as is, no charge. {_n(n_new, 'new build')}.")
    else:
        lines.append(f"- None of the names is in the library yet: {_n(n_new, 'new build')}.")
    lines.append(f"- Window: {window_words(window)}")
    lines.append(f"- Each new build is a Total Universe{' with its Avid Fan cut' if run_avid else ''}; a profile lands in Select Profile the moment it finishes.")
    if n_new:
        lines.append(f"- Price: {total} charged now ({_n(n_new, 'new build')} at {p['label']} each; reused profiles are free). "
                     f"A name that turns out to be in the library when its turn comes is not charged, and a build that cannot be completed is refunded.")
        lines.append(f"- Time: {eta_words(n_new, cap)} at the current pace (up to {cap} builds at a time, about {BUILD_MINUTES} minutes each). "
                     f"I send you a summary email as the list progresses and a final one when it is done; ask me 'how is my list going' any time.")
    else:
        lines.append("- Price: nothing to charge; every name is already on file.")
    lines.append(f"Say '{RUN_LABEL}' and I will start.")
    return '\n'.join(lines)


def promise(ledger):
    c = counts(ledger)
    if c['queued'] == 0:
        return (f"Done already: all {_n(c['total'], 'name')} on your list are in the library. Open Select Profile and search any of them. "
                f"Nothing was charged.")
    first = min(c['queued'], in_flight_cap())
    return (f"On it. {_n(c['queued'], 'build')} queued" + (f", {_n(c['reused'], 'name')} reused from the library" if c['reused'] else '')
            + f". The first {first} start now and the rest follow as slots open; each profile lands in Select Profile as it finishes. "
            f"I will email you a summary as the list progresses and when it is done. Ask me 'how is my list going' any time.")


def _ago(ts):
    try:
        mins = int((time.time() - float(ts)) // 60)
    except Exception:
        return ''
    if mins < 1:
        return 'just now'
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hrs = mins // 60
    if hrs < 36:
        return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
    return f"{hrs // 24} days ago"


def status_words(ledger, cap=None):
    """The status line for one ledger: done X of Y, reused, failed, next up, time left."""
    c = counts(ledger)
    name = ledger.get('file_name') or 'your list'
    built = c['done']
    total_builds = c['total'] - c['reused']
    parts = [f"Your list from {name} ({_ago(ledger.get('created_at'))}): {built:,} of {total_builds:,} builds done"]
    if c['reused']:
        parts[0] += f", {c['reused']:,} reused from the library"
    if c['failed']:
        parts[0] += f", {c['failed']:,} could not be completed (refunded)"
    parts[0] += '.'
    if str(ledger.get('status') or '') != 'active' or c['open'] == 0:
        parts.append('The list is complete; every finished profile is in Select Profile.')
        return ' '.join(parts)
    running = [r['subject'] for r in ledger.get('rows') or [] if r.get('state') in ('posted', 'running', 'interpreting')]
    nxt = [r['subject'] for r in ledger.get('rows') or [] if r.get('state') == 'queued'][:3]
    if running:
        parts.append(f"Building now: {', '.join(running[:6])}" + (f" and {len(running) - 6} more" if len(running) > 6 else '') + '.')
    if nxt:
        parts.append(f"Next up: {', '.join(nxt)}.")
    left = eta_words(c['queued'] + c['waiting'] + c['in_flight'], cap)
    parts.append(f"Time left: {left}.")
    return ' '.join(parts)


def status_reply(username, s3=None, bucket=None, cap=None):
    """The chat answer for a status ask, or None when the reader has no
    list on record."""
    ledgers = list_ledgers(s3=s3, bucket=bucket, active_only=False, username=username, limit=400)
    if not ledgers:
        return None
    recent = [l for l in ledgers if time.time() - float(l.get('created_at') or 0) < 14 * 86400] or ledgers[:1]
    active = [l for l in recent if str(l.get('status') or '') == 'active']
    picked = active[:3] if active else recent[:1]
    lines = [status_words(l, cap) for l in picked]
    chips = ['Email me when it lands'] if active else []
    return {'success': True, 'action': 'answer', 'reply': '\n\n'.join(lines), 'followups': chips, 'offer_deck': False, 'deck_angle': None,
            'bulk_ids': [l['bulk_id'] for l in picked]}


def status_items(username, s3=None, bucket=None):
    """Items for the generic status lane: one per active list."""
    out = []
    for l in list_ledgers(s3=s3, bucket=bucket, active_only=True, username=username, limit=20):
        c = counts(l)
        out.append({'kind': 'bulk', 'subject': f"your list from {l.get('file_name') or 'the upload'}", 'status': 'running',
                    'started': l.get('created_at'), 'line': status_words(l)})
    return out


def digest_lines(ledger, row_indexes):
    """The body of a digest: the rows finished since the last one, then
    the running total. Partner-safe; no internal words."""
    rows = {r['i']: r for r in ledger.get('rows') or []}
    done = [rows[i] for i in row_indexes if i in rows and rows[i].get('state') == 'done']
    reused = [rows[i] for i in row_indexes if i in rows and rows[i].get('state') == 'reused']
    failed = [rows[i] for i in row_indexes if i in rows and rows[i].get('state') == 'failed']
    c = counts(ledger)
    lines = []
    if done:
        lines.append(f"Finished and in Select Profile ({len(done)}):")
        lines.extend(f"- {r['subject']}" + (' (with its Avid Fan cut)' if r.get('avid_key') else '') for r in done)
        lines.append('')
    if reused:
        lines.append(f"Already in the library, reused at no charge ({len(reused)}):")
        lines.extend(f"- {r['subject']}" for r in reused)
        lines.append('')
    if failed:
        lines.append(f"Could not be completed ({len(failed)}): {FAIL_LINE}")
        lines.extend(f"- {r['subject']}" for r in failed)
        lines.append('')
    total_builds = c['total'] - c['reused']
    lines.append(f"Progress: {c['done']:,} of {total_builds:,} builds done" + (f", {c['reused']:,} reused" if c['reused'] else '')
                 + (f", {c['failed']:,} refunded" if c['failed'] else '') + f"; {c['open']:,} still to go.")
    return lines


def digest_text(ledger, row_indexes, final=False):
    name = ledger.get('file_name') or 'your list'
    c = counts(ledger)
    if final:
        subject = f"Your list from {name} is done: {c['done']:,} built" + (f", {c['reused']:,} reused" if c['reused'] else '') \
                  + (f", {c['failed']:,} refunded" if c['failed'] else '')
        head = f"Every name on your list from {name} has been handled."
    else:
        subject = f"Your list from {name}: {c['done']:,} of {c['total'] - c['reused']:,} builds done"
        head = f"An update on your list from {name}."
    body = [head, ''] + digest_lines(ledger, row_indexes)
    body += ['', 'Open Select Profile on the dashboard to use any of them; search by name.', '', 'Prometheus', 'Crosswalk']
    return subject, '\n'.join(body)


# ---- charging ---------------------------------------------------------
def charge(user, uname, ledger, *, consume_credit=None, is_free=None):
    """Charge the new builds once, at confirm. Returns (ok, funds_error).
    Free seats are never charged (recorded on the ledger)."""
    n_new = int((ledger.get('charged') or {}).get('rows') or 0)
    free, consume_credit = _TUL.billing(user, uname, consume_credit=consume_credit, is_free=is_free)
    ledger['charged']['free_seat'] = bool(free or consume_credit is None)
    if free or consume_credit is None or n_new <= 0:
        for r in ledger['rows']:
            if r.get('state') == 'queued':
                r['charged'] = False
        ledger['charged']['credits'] = 0
        ledger['charged']['at'] = time.time()
        return True, None
    total, p = total_label(n_new, uname)
    try:
        ok = bool(consume_credit(uname, description=f"Profile IQ list: {n_new} profiles from {ledger.get('file_name') or 'an uploaded list'}",
                                 pull_type=PROFILE_PULL_TYPE, credits_used=PROFILE_CREDITS * n_new, job_id=str(ledger['bulk_id']), quantity=n_new))
    except Exception:
        traceback.print_exc()
        ok = False
    if not ok:
        return False, (f"That list runs {total} ({_n(n_new, 'new build')} at {p['label']} each) and your account cannot cover it right now. "
                       f"Add funds or ask your admin, and it runs the moment you are set.")
    ledger['charged']['at'] = time.time()
    return True, None


def start(user, uname, read, window, *, index, thread_id='', file_name='', ask_text='', run_avid=True, s3=None, bucket=None,
          consume_credit=None, is_free=None, usd_each=None):
    """The one starter: plan the rows against the library, charge the new
    builds once, write the ledger. Returns (ledger, promise, funds_error)."""
    if s3 is None:
        s3, bucket = _s3()
    email = str((user or {}).get('email') or '').strip() if isinstance(user, dict) else ''
    if usd_each is None:
        usd_each = price(uname)['usd']
    ledger = new_ledger(requester={'username': uname, 'email': email}, items=read.get('items') or [], index=index, window=window,
                        file_name=file_name, thread_id=thread_id, ask_text=ask_text, usd_each=usd_each, run_avid=run_avid)
    ok, err = charge(user, uname, ledger, consume_credit=consume_credit, is_free=is_free)
    if not ok:
        return None, '', err
    if counts(ledger)['open'] == 0:
        ledger['status'] = 'done'
    save_ledger(ledger, s3=s3, bucket=bucket)
    return ledger, promise(ledger), None


# ---- partner status ---------------------------------------------------
def partner_status(ledger):
    """The scrubbed status document for GET /api/v1/profiles/<bulk_id>."""
    c = counts(ledger)
    st = 'complete' if str(ledger.get('status') or '') != 'active' or c['open'] == 0 else 'running'
    rows = []
    for r in ledger.get('rows') or []:
        state = r.get('state')
        out = {'subject': r.get('subject'), 'status': {'done': 'complete', 'reused': 'complete', 'failed': 'failed'}.get(state, 'running')}
        if state in ('done', 'reused') and r.get('key'):
            out['s3_key'] = r['key']
            out['reused'] = state == 'reused'
        if state == 'failed':
            out['reason'] = FAIL_LINE
        rows.append(out)
    return {'bulk_id': ledger.get('bulk_id'), 'status': st, 'total': c['total'], 'complete': c['done'] + c['reused'], 'built': c['done'],
            'reused': c['reused'], 'failed': c['failed'], 'in_progress': c['open'], 'rows': rows}


# ---- the Prometheus door ---------------------------------------------
_ASK_LIST = ("Attach the list (a CSV, TXT or XLSX with one name per row; a column named subject, name, talent, brand or title is read, "
             "else the first text column) and tell me to pull profiles for everyone on it. I read it back with the price before anything runs.")


def _attached_names(uname, tid, host):
    """(read, file_name) from the most recent attached sheet or text file
    that carries a names column, else (None, '')."""
    try:
        from . import documents as _docs
        s3, bucket = _TUL._host_s3(host)
        recs = _docs.attachments(uname, tid, s3, bucket) if uname else []
        for r in reversed(list(recs or [])):
            if r.get('kind') not in ('sheet', 'text'):
                continue
            data = _docs.document_bytes(r, s3, bucket)
            read = subjects_from_bytes(r.get('name') or 'list.txt', data)
            if read.get('items') and len(read['items']) >= 2:
                return read, str(r.get('name') or '')
    except Exception:
        traceback.print_exc()
    return None, ''


def thread_has_names(uname, tid, host):
    """A sheet or text file on the thread that is not a link list."""
    try:
        from . import documents as _docs
        s3, bucket = _TUL._host_s3(host)
        for r in reversed(list(_docs.attachments(uname, tid, s3, bucket) or [])):
            if r.get('kind') == 'sheet' and not any((s or {}).get('url_count') for s in (r.get('sheets') or [])):
                return True
            if r.get('kind') == 'text' and not re.search(r"https?://", str(r.get('excerpt') or '')):
                return True
    except Exception:
        pass
    return False


def intake_armed(history):
    for t in reversed(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        return txt.startswith('Here is the list:') and RUN_LABEL in txt
    return False


def _payload(reply, followups=None, **extra):
    try:
        from migration.digital_twin_synthesis import scrub_text as _sc
    except Exception:
        _sc = lambda s: s  # noqa: E731
    out = {'success': True, 'action': 'answer', 'reply': _sc(reply), 'followups': [_sc(f) for f in (followups or [])], 'offer_deck': False, 'deck_angle': None}
    out.update(extra)
    return out


def analyze_hook(text, body, history, user, ppu, uname, *, host, pending, catalog=None, consume_credit=None, is_free=None, cap=None):
    """The bulk door on the analyze surface. ``pending(op, user, flow,
    *args)`` is the chat's stash / pick. ``catalog`` is the dropdown's
    list (injected in tests; the web app's own when None). Returns a
    reply payload, or None when the ask is not this door's."""
    try:
        t = str(text or '').strip()
        tid = str((body or {}).get('thread_id') or '')
        s3, bucket = _TUL._host_s3(host)
        # 1. the confirm: the chip, the stashed brief, or the echoed card
        confirm = (body or {}).get('bulk_confirm') or pending('pick', user, FLOW, t)
        if isinstance(confirm, dict) and confirm.get('list_key'):
            try:
                read = json.loads(s3.get_object(Bucket=bucket, Key=confirm['list_key'])['Body'].read().decode('utf-8'))
            except Exception:
                read = None
            if not read or not read.get('items'):
                return _payload('That list is no longer on the thread. Attach it again and I will read it back.', ['Cancel'])
            if catalog is None:
                catalog = _web_catalog()
            ledger, line, err = start(user, uname, read, confirm.get('window'), index=library_index(catalog), thread_id=tid,
                                      file_name=str(confirm.get('file_name') or ''), ask_text=str(confirm.get('ask_text') or t), run_avid=bool(confirm.get('run_avid', True)),
                                      s3=s3, bucket=bucket, consume_credit=consume_credit, is_free=is_free)
            if err:
                return _payload(err)
            return _payload(line, ['How is my list going?'], bulk_id=ledger['bulk_id'], job_type='bulk_profile_pull')
        armed = intake_armed(history)
        if armed and _CANCEL_RX.match(t):
            pending('clear', user, FLOW)
            return _payload('No problem, nothing was charged. Ask me anything else when ready.')
        # 2. a status ask
        if is_status_ask(t):
            rep = status_reply(uname, s3=s3, bucket=bucket, cap=cap)
            if rep is not None:
                return rep
            return None
        # 3. the order
        if not (_PULL_RX.search(t) and (_PROFILE_RX.search(t) or re.search(r"\bbulk\b", t, re.I))):
            return None      # the cheap gate: no S3 read on an ordinary message
        has_rows = thread_has_names(uname, tid, host)
        if not is_bulk_ask(t, has_rows):
            return None
        read, file_name = _attached_names(uname, tid, host)
        if not read:
            return _payload(_ASK_LIST, ['Cancel'])
        window = window_from_text(t)
        run_avid = not re.search(r"\b(no|without|skip)\s+(?:the\s+)?avid\b|\btotal universes? only\b|\btu only\b", t, re.I)
        if catalog is None:
            catalog = _web_catalog()
        rows = plan_rows(read['items'], library_index(catalog))
        list_key = f"{LEDGER_PREFIX}_lists/{uname and re.sub(r'[^A-Za-z0-9_.@-]+', '_', uname)[:60] or 'x'}_{secrets.token_urlsafe(6).replace('-', 'x').replace('_', 'y')}.json"
        s3.put_object(Bucket=bucket, Key=list_key, Body=json.dumps(read, default=str).encode('utf-8'), ContentType='application/json')
        brief = {'list_key': list_key, 'window': _normalize_window(window), 'file_name': file_name, 'count': len(read['items']), 'run_avid': run_avid, 'ask_text': t[:400]}
        pending('stash', user, FLOW, brief, RUN_LABEL)
        return _payload(confirm_reply(read, rows, window, uname, file_name=file_name, cap=cap, run_avid=run_avid), [RUN_LABEL, 'Cancel'],
                        bulk_confirm_payload=brief)
    except Exception:
        traceback.print_exc()
        return None


def _web_catalog():
    """The web app's own catalog (the Select Profile list), else the
    persisted cache off S3."""
    try:
        from prometheus.legacy import chat as _chat
        cat = _chat._profile_catalog_for_chat()
        if cat:
            return cat
    except Exception:
        pass
    try:
        s3, bucket = _s3()
        return catalog_from_cache(s3, bucket)
    except Exception:
        return []


__all__ = ['FLOW', 'RUN_LABEL', 'LEDGER_PREFIX', 'MAX_SUBJECTS', 'PROFILE_CREDITS', 'FAIL_LINE', 'DIGEST_EVERY', 'DIGEST_HOURS', 'TERMINAL', 'ACTIVE_STATES',
           'subjects_from_rows', 'subjects_from_text', 'subjects_from_bytes', 'is_bulk_ask', 'is_status_ask', 'library_index', 'library_match',
           'catalog_from_cache', 'price', 'total_label', 'in_flight_cap', 'eta_minutes', 'eta_words', 'ledger_key', 'is_bulk_id', 'new_bulk_id',
           'plan_rows', 'counts', 'new_ledger', 'save_ledger', 'load_ledger', 'update_ledger', 'list_ledgers', 'list_words', 'confirm_reply', 'promise',
           'status_words', 'status_reply', 'status_items', 'digest_lines', 'digest_text', 'charge', 'start', 'partner_status', 'analyze_hook',
           'thread_has_names', 'intake_armed']
