"""Corpus catalog: one subject-keyed record of every figure Crosswalk has
published anywhere on the dashboard, kept current as data is created and
read in one hit at ask or build time (2026-10-05, Jenna).

Jenna, verbatim: "we need a way to intelligently start caching and
cataloging all of this data as it's created and ensure there is not a
long lag while a user waits on prometheus to find all the data, scour
the corpus, etc and it should always scour everything in the dashboard
which is all stored in s3."

Why it exists
-------------
Alexia's journey was built cold while an Attribution IQ campaign on the
same title sat in the corpus (20x apart on one number). Scott's sample
check sized a show from scratch while its Profile IQ profile sat on the
dashboard (49,918 vs 31,207 in the sample). Every product had its own
inputs and none of them looked at what another product had already
published on the subject. This module is the shared memory.

Shape
-----
    s3://dashboard-inputs/system/corpus_catalog/index.json
        {"version": 1, "updated": iso,
         "subjects": {<subject_key>: {"subject": display, "aliases": [..],
                                      "products": [..], "n_facts": N,
                                      "updated": iso}},
         "sources":  {<s3_key>: {"etag": .., "product": .., "subject_key": ..,
                                 "indexed_at": iso}}}
    s3://dashboard-inputs/system/corpus_catalog/subjects/<subject_key>.json
        {"subject_key": .., "subject": .., "aliases": [..], "facts": [fact..]}

    fact = {"id", "product", "kind", "label", "value", "unit",
            "window": {"start", "end"} | None, "as_of", "source": {...},
            "note"}

    product: profile | journey | attribution | bpiq | chat | deck
    kind:    sample_size | audience_size | platform_count | stage_count |
             conversion_pct | ticketing_partition | valuation |
             stated_number

Index is small (one line per subject and per source) and cached in
process (TTL 300s, ETag-checked), so a lookup is a dict hit. Subject
pages are fetched on demand and cached. Nothing here lists S3 on the
ask path: listing and extraction happen at WRITE time (the hooks in
dashboard_register, journey_iq, mta_iq, bpiq_synthesis, the Prometheus
answer gate) and in the sweep (`migration/corpus_catalog_sync.py`,
every 30 minutes on Hetzner), which only re-reads objects whose ETag
changed.

Consult
-------
    anchors_for(subject, window=None) -> {"subject_key", "subject",
        "facts": [...], "products": [...], "ledger": [...]}
    anchors_block(anchors)             -> prompt text (binding figures)
    profile_anchor(anchors)            -> the profile fact bundle or None
    prior_journey(anchors, window)     -> a journey fact bundle or None

Every function is fail-safe: any S3 or parse trouble returns an empty
result and the caller proceeds. Kill switch CORPUS_CATALOG=0 (host env
only, never a request field).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError, ParamValidationError
except Exception:  # pragma: no cover - import guard for hermetic tests
    boto3 = None

    class ClientError(Exception):
        response = {}

    class ParamValidationError(Exception):
        pass

S3_BUCKET = os.environ.get('CORPUS_CATALOG_BUCKET', 'dashboard-inputs')
INDEX_KEY = 'system/corpus_catalog/index.json'        # subjects only (hot path)
SOURCES_KEY = 'system/corpus_catalog/sources.json'    # s3_key -> etag (sweep only)
SUBJECT_PREFIX = 'system/corpus_catalog/subjects/'
VERSION = 1

_INDEX_TTL_S = 300
_PAGE_TTL_S = 300
_lock = threading.Lock()
_state = {'client': None, 'index': None, 'index_etag': None, 'index_ts': 0.0,
          'pages': {}}
_SUPPORTS_CONDITIONAL_PUT = True


def enabled():
    return os.environ.get('CORPUS_CATALOG', '1').strip() != '0'


def _client():
    with _lock:
        if _state['client'] is not None:
            return _state['client']
    cl = boto3.client('s3')
    with _lock:
        _state['client'] = cl
    return cl


def set_client(cl):
    """Tests inject a fake S3 client here."""
    with _lock:
        _state['client'] = cl
        _state['index'] = None
        _state['index_etag'] = None
        _state['index_ts'] = 0.0
        _state['pages'] = {}


def _now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ------------------------------------------------------------------ keys
_PLATFORM_RX = re.compile(
    r'\s+(?:on|via|at)\s+(?:netflix|hulu|max|hbo max|hbo|disney\+?|'
    r'disney plus|peacock|paramount\+?|paramount plus|apple tv\+?|'
    r'apple tv plus|prime video|amazon prime video|amazon|prime|starz|'
    r'showtime|amc\+?|discovery\+?|espn\+?|tubi|pluto tv|roku|freevee|'
    r'crunchyroll|britbox|youtube|youtube tv|fubo|sling|philo|cbs|nbc|'
    r'abc|fox|the cw|spotify|tiktok|instagram)\b.*$', re.I)
_TRAILING_LABEL_RX = re.compile(
    r'\s+(series|tu|total universe|viewers|fans|audience|film|movie|'
    r'\(film\)|\(series\))$')
_CUT_SUFFIX_RX = re.compile(
    r'\s+-\s+(avid fan|casual fan|avid|casual|female|male|f|m|'
    r'\d{2}-\d{2}|1[0-9]-[0-9]{2}|gen z|millennials?|boomers?|gen x|'
    r'[a-z ]+ ca|[a-z ]+ ny|[a-z ]+ tx|[a-z ]+ fl|parents of .*|.*users|'
    r'.*members|.*subscribers|.*shoppers)$', re.I)


# ' - Q4 2025', ' CY2025', ' 2023/2024 TU', ' - Jan 2026', ' 2025 YTD':
# a window label on a profile name is not part of the entity.
_WINDOW_LABEL_RX = re.compile(
    r'(?:\s+-\s+|\s+)(?:q[1-4]\s+(?:20)?\d{2}|(?:19|20)\d{2}(?:\s*/\s*(?:19|20)?\d{2})?(?:\s+tu)?|'
    r'cy\s?(?:20)?\d{2}|calendar\s+(?:20)?\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)'
    r'[a-z]*\s+(?:20)?\d{2}|(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}(?:\s+ytd)?(?:\s+tu)?|(?:20)?\d{2}\s+ytd)\s*$')
_QUALIFIER_RX = re.compile(
    r'[,:]?\s*(?:opening (?:weekend|week|day|night)|premiere (?:week|weekend|night)|'
    r'launch (?:week|weekend)|finale (?:week|weekend)|season \d+ (?:premiere|finale))\b.*$')


def entity_fold(name):
    """One key per real-world entity across the spellings the dashboard
    produces: case, '&' vs 'and', punctuation, a trailing platform
    ('on Hulu'), a trailing Series / TU / (film) label, a cut suffix
    (' - Avid Fan', ' - Female')."""
    t = str(name or '').strip().lower()
    t = t.replace('&', ' and ').replace('+', ' plus ').replace('\u00d7', ' x ')
    t = re.sub(r'\([^)]*\)', ' ', t)
    t = ' '.join(t.split())
    t = _QUALIFIER_RX.sub('', t)
    t = _WINDOW_LABEL_RX.sub('', t)
    t = _CUT_SUFFIX_RX.sub('', t)
    t = _PLATFORM_RX.sub('', t)
    t = _TRAILING_LABEL_RX.sub('', t)
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    t = re.sub(r'\bthe\b', ' ', t)
    return ' '.join(t.split())


def subject_key(name):
    f = entity_fold(name)
    return f.replace(' ', '_')[:120] if f else ''


def best_display(aliases, fallback=''):
    """The cleanest name for a subject among its aliases: no cut
    suffix, no parenthetical, then the shortest."""
    cands = [str(a).strip() for a in (aliases or []) if str(a or '').strip()]
    if not cands:
        return fallback
    def _rank(a):
        return (' - ' in a, '(' in a, a.isupper(), len(a))
    return sorted(cands, key=_rank)[0]


def _fact_id(product, source_key, kind, label, window):
    raw = f"{product}|{source_key}|{kind}|{label}|{json.dumps(window, sort_keys=True)}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def make_fact(product, kind, label, value, unit, *, window=None, as_of=None,
              source_key='', source_user='', note=''):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    win = None
    if isinstance(window, dict) and (window.get('start') or window.get('end')):
        win = {'start': str(window.get('start') or '')[:10],
               'end': str(window.get('end') or '')[:10]}
    return {'id': _fact_id(product, source_key, kind, label, win),
            'product': product, 'kind': kind, 'label': str(label)[:160],
            'value': (int(v) if unit in ('people', 'individuals') and abs(v - round(v)) < 1e-6
                      else round(v, 4)),
            'unit': unit, 'window': win, 'as_of': (as_of or _now_iso())[:19],
            'source': {'key': source_key, 'user': source_user},
            'note': str(note or '')[:240]}


# ---------------------------------------------------------- extractors
_WINDOW_RX = re.compile(r'(\d{4}-\d{2}-\d{2})\s*(?:TO|to|-)\s*(\d{4}-\d{2}-\d{2})')


def _parse_window_label(label):
    m = _WINDOW_RX.search(str(label or ''))
    if not m:
        return None
    return {'start': m.group(1), 'end': m.group(2)}


def facts_from_profile_rows(s3_key, display_name, rows, user=''):
    """rows: iterable of dicts with Column / Value / Brand Penetration
    (Row) / Original Raw Numbers / US Gen Pop Projection (the profile
    CSV columns). Only the metadata rows and the platform rows are
    read; the first few KB of the CSV already carry the size facts."""
    facts = []
    sample = proj = None
    window = None
    subject_val = ''
    platform_rows = []
    for r in rows:
        col = str(r.get('Column') or '').strip().upper()
        val = str(r.get('Value') or '').strip()
        if col == 'BRAND INPUT' and sample is None:
            sample = _num(r.get('Original Raw Numbers'))
            proj = _num(r.get('US Gen Pop Projection'))
        elif col == 'SAMPLE SIZE':
            window = window or _parse_window_label(val)
            if sample is None:
                sample = _num(r.get('Original Raw Numbers'))
                proj = _num(r.get('US Gen Pop Projection'))
        elif col == 'SUBJECT':
            subject_val = val
        elif col == 'STREAMING/PLATFORM':
            p = _num(r.get('US Gen Pop Projection'))
            if p:
                platform_rows.append((val, p))
    subject = display_name or subject_val or re.sub(r'_\d{2}_\d{2}_\d{4}.*$', '', s3_key).replace('_', ' ')
    _label = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}\.csv$', '', str(display_name or s3_key)).replace('_', ' ')
    _label = _WINDOW_LABEL_RX.sub('', _label.strip().lower())
    is_cut = bool(re.search(r'\s-\s', _label))
    note = f"Profile IQ: {subject}"
    if sample:
        facts.append(make_fact('profile', 'sample_size', 'viewers inside the 10 million sample',
                               sample, 'individuals', window=window, source_key=s3_key,
                               source_user=user, note=note))
    if proj:
        facts.append(make_fact('profile', 'audience_size', 'US audience', proj, 'people',
                               window=window, source_key=s3_key, source_user=user, note=note))
    for val, p in sorted(platform_rows, key=lambda x: -x[1])[:6]:
        facts.append(make_fact('profile', 'platform_count', f"US viewers on {val}", p, 'people',
                               window=window, source_key=s3_key, source_user=user, note=note))
    facts = [f for f in facts if f]
    for f in facts:
        f['cut'] = is_cut
    return subject, facts


def facts_from_profile_csv_text(s3_key, display_name, text, user=''):
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    return facts_from_profile_rows(s3_key, display_name, rows, user=user)


def facts_from_journey(key, payload, user=''):
    meta = (payload or {}).get('meta') or {}
    body = (payload or {}).get('fragrance_shop_journey') or {}
    kpis = (payload or {}).get('kpis') or {}
    subject = str(meta.get('customer_brand') or meta.get('subject')
                  or meta.get('target') or meta.get('project_name') or '').strip()
    window = {'start': meta.get('start_date') or meta.get('window_start'),
              'end': meta.get('end_date') or meta.get('window_end')}
    as_of = str(meta.get('created_at') or meta.get('generated_at') or '')[:19] or None
    note = f"Digital Journey IQ: {meta.get('project_name') or subject}"
    facts = []
    spine = body.get('spine') or []
    if isinstance(spine, dict):
        spine = spine.get('stages') or []
    for st in spine:
        if not isinstance(st, dict):
            continue
        label = str(st.get('label') or st.get('name') or st.get('id') or '').strip()
        cnt = _num(st.get('accounts') if st.get('accounts') is not None
                   else (st.get('count') if st.get('count') is not None else st.get('users')))
        if str(st.get('id') or '').lower() == 'tam' or cnt == 329_900_000:
            continue
        if label and cnt:
            facts.append(make_fact('journey', 'stage_count', label, cnt, 'people', window=window,
                                   as_of=as_of, source_key=key, source_user=user, note=note))
    if not facts and isinstance(kpis, dict):
        # Story-mode payloads (subscriber lifecycle, flywheel) carry
        # their headline figures in kpis; bank the integer ones.
        for kk, vv in list(kpis.items())[:16]:
            cnt = _num(vv)
            if cnt and cnt >= 100 and 'pct' not in str(kk).lower() and not str(kk).endswith('_m'):
                facts.append(make_fact('journey', 'kpi', str(kk).replace('_', ' '), cnt, 'people',
                                       window=window, as_of=as_of, source_key=key,
                                       source_user=user, note=note))
    if kpis.get('total_users'):
        facts.append(make_fact('journey', 'audience_size', 'journey end point, US people',
                               kpis['total_users'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    if kpis.get('conversion_pct') is not None:
        facts.append(make_fact('journey', 'conversion_pct', 'share reaching the end point',
                               kpis['conversion_pct'], 'pct', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    if not facts:
        # Hand-built story modes carry their figures as fact strings.
        for fx in (payload or {}).get('facts') or []:
            txt = fx.get('fact') if isinstance(fx, dict) else fx
            if isinstance(txt, str) and txt.strip():
                for f in facts_from_answer(subject, txt, user=user, thread_id='', source_label='journey'):
                    f['source']['key'] = key
                    f['note'] = note
                    f['window'] = {'start': window.get('start'), 'end': window.get('end')} if window.get('start') else None
                    facts.append(f)
                if len(facts) >= 40:
                    break
    for det in (body.get('detours') or []):
        if not isinstance(det, dict):
            continue
        title = str(det.get('title') or det.get('label') or '').lower()
        if 'ticket' in title and 'reached' in title or 'partition' in title:
            for row in (det.get('rows') or [])[:8]:
                if isinstance(row, dict) and row.get('label') is not None and row.get('pct') is not None:
                    facts.append(make_fact('journey', 'ticketing_partition', str(row['label']),
                                           row['pct'], 'pct', window=window, as_of=as_of,
                                           source_key=key, source_user=user, note=note))
    return subject, [f for f in facts if f]


def facts_from_attribution(slug, assets, fit, user=''):
    t = (assets or {}).get('title')
    distributor = ''
    if isinstance(t, dict):
        distributor = str(t.get('distributor') or '')
        t = t.get('display_name') or t.get('title_slug') or ''
    title = str(t or slug.replace('_', ' ')).strip()
    # 'The Influencer Project - Hades' carries the distributor; the
    # subject is the title.
    if ' - ' in title:
        head, tail = title.rsplit(' - ', 1)
        if tail.strip() and (tail.strip().lower() in distributor.lower()
                             or tail.strip().lower() in slug.replace('_', ' ')):
            title = head.strip()
    overall = (fit or {}).get('overall') or {}
    paths = overall.get('paths') or {}
    nest = paths.get('nest') or {}
    where = paths.get('where') or overall.get('where') or {}
    as_of = str((fit or {}).get('as_of') or '')[:19] or None
    starts, ends = [], []
    for ph in (assets or {}).get('phases') or []:
        if isinstance(ph, dict):
            st = ph.get('start_date') or ph.get('start')
            en = ph.get('end_date') or ph.get('end')
            if st:
                starts.append(str(st)[:10])
            if en:
                ends.append(str(en)[:10])
    # an open-ended last phase runs to the fit's as_of date
    window = {'start': min(starts) if starts else None,
              'end': (max(ends) if ends and max(ends) >= (as_of or '')[:10] else (as_of or '')[:10])
              or None}
    note = f"Attribution IQ: {title}"
    key = f"intent/{slug}/"
    facts = []
    # Film ladder nouns per no-box-office-prediction.mdc (2026-10-06):
    # stage 3 is a showtimes page, stage 4 is the ticketing-site visit
    # for a ticket. Never a purchase, checkout or buyer claim.
    labels = {'0_tam': 'US addressable', '1_exposed': 'saw tracked campaign content',
              '2_infoseek': 'looked the title up', '3_ticketer': 'reached a showtimes page',
              '4_paid': 'went to a ticketing site or app for a ticket'}
    items = []
    if isinstance(nest, dict):
        items = [(k, v) for k, v in nest.items()]
    elif isinstance(nest, list):
        items = [(str(r.get('stage') or ''), r) for r in nest if isinstance(r, dict)]
    for stage, row in items:
        if stage == '0_tam':
            continue
        cnt = _num((row or {}).get('us_accounts') if isinstance(row, dict) else row)
        if cnt:
            facts.append(make_fact('attribution', 'stage_count', labels.get(stage, stage), cnt,
                                   'people', window=window, as_of=as_of, source_key=key,
                                   source_user=user, note=note))
    part = where.get('ticketer_partition') or {}
    pairs = []
    if isinstance(part, dict):
        pairs = list(part.items())
    elif isinstance(part, list):
        pairs = [(r.get('surface') or r.get('label'), r.get('pct')) for r in part if isinstance(r, dict)]
    for lbl, pct in pairs:
        if lbl is None:
            continue
        p = _num(pct)
        if True:
            if p is not None:
                facts.append(make_fact('attribution', 'ticketing_partition', str(lbl), p, 'pct',
                                       window=window, as_of=as_of, source_key=key,
                                       source_user=user, note=note))
    return title, [f for f in facts if f]


def facts_from_bpiq(key, payload, user=''):
    p = payload or {}
    subject = str(p.get('project_name') or '').strip()
    window = {'start': p.get('start_date'), 'end': p.get('end_date')}
    as_of = str(p.get('created_at') or '')[:19] or None
    note = f"Brand Partnership IQ: {subject}"
    facts = []
    if p.get('projected_audience_size'):
        facts.append(make_fact('bpiq', 'audience_size', 'US audience reached by the partnership',
                               p['projected_audience_size'], 'people', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    if p.get('audience_size'):
        facts.append(make_fact('bpiq', 'sample_size', 'viewers inside the 10 million sample',
                               p['audience_size'], 'individuals', window=window, as_of=as_of,
                               source_key=key, source_user=user, note=note))
    val = p.get('valuation') or {}
    for k, lbl in (('total_brand_value', 'total brand value, USD'),
                   ('attributable_to_partnership', 'value attributable to the partnership, USD'),
                   ('incremental_users', 'incremental US users')):
        if val.get(k) is not None:
            facts.append(make_fact('bpiq', 'valuation', lbl, val[k],
                                   'people' if k == 'incremental_users' else 'usd',
                                   window=window, as_of=as_of, source_key=key,
                                   source_user=user, note=note))
    return subject, [f for f in facts if f]


_NUM_SENT_RX = re.compile(
    r'(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:million|M\b|K\b|k\b)|\d+(?:\.\d+)?%)')


def facts_from_answer(subject, text, user='', thread_id='', source_label='chat'):
    """Every figure a Prometheus reply states, with the sentence it sat
    in, so the next answer on the subject sees it."""
    facts = []
    txt = str(text or '')
    sents = re.split(r'(?<=[.!?])\s+|\n+', txt)
    seen = set()
    for s in sents:
        s = s.strip()
        if not s or len(s) > 400:
            continue
        for m in _NUM_SENT_RX.finditer(s):
            raw = m.group(1)
            v, unit = _parse_stated(raw)
            if v is None or v < 10 and unit != 'pct':
                continue
            sig = (round(v, 4), unit)
            if sig in seen:
                continue
            seen.add(sig)
            f = make_fact(source_label, 'stated_number', s[:160], v, unit,
                          source_key=f"thread/{user}/{thread_id}", source_user=user,
                          note=s[:240])
            if f:
                facts.append(f)
            if len(facts) >= 24:
                return facts
    return facts


def _parse_stated(raw):
    r = raw.replace(',', '').strip()
    try:
        if r.endswith('%'):
            return float(r[:-1]), 'pct'
        m = re.match(r'^(\d+(?:\.\d+)?)\s*(million|M|K|k)$', r)
        if m:
            n = float(m.group(1))
            mult = 1_000_000 if m.group(2).lower().startswith('m') else 1_000
            return n * mult, 'people'
        return float(r), 'people'
    except ValueError:
        return None, None


def _num(v):
    try:
        if v is None or v == '':
            return None
        f = float(str(v).replace(',', ''))
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ storage
def _read_json_with_etag(key):
    try:
        resp = _client().get_object(Bucket=S3_BUCKET, Key=key)
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
    return doc, (resp.get('ETag') or '').strip('"') or None


def _is_precondition_failed(err):
    code = (err.response.get('Error') or {}).get('Code', '')
    status = (err.response.get('ResponseMetadata') or {}).get('HTTPStatusCode')
    return code in ('PreconditionFailed', '412') or status == 412


def _put_json_cas(key, doc, etag):
    global _SUPPORTS_CONDITIONAL_PUT
    s3 = _client()
    kwargs = dict(Bucket=S3_BUCKET, Key=key, Body=json.dumps(doc, separators=(',', ':')).encode('utf-8'),
                  ContentType='application/json')
    if _SUPPORTS_CONDITIONAL_PUT:
        k2 = dict(kwargs)
        if etag:
            k2['IfMatch'] = etag
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
        head = s3.head_object(Bucket=S3_BUCKET, Key=key)
        current = (head.get('ETag') or '').strip('"')
    except ClientError as e:
        code = (e.response.get('Error') or {}).get('Code', '')
        if code in ('404', 'NoSuchKey', 'NotFound'):
            current = None
        else:
            raise
    if current != etag:
        return False
    s3.put_object(**kwargs)
    return True


def _update_json(key, mutate_fn, max_retries=6):
    import random
    for attempt in range(max_retries + 1):
        try:
            doc, etag = _read_json_with_etag(key)
        except Exception as e:
            print(f"[corpus-catalog] read failed {key}: {e}")
            return None
        doc = doc if isinstance(doc, dict) else {}
        new_doc = mutate_fn(doc)
        if new_doc is None:
            return None
        try:
            if _put_json_cas(key, new_doc, etag):
                return new_doc
        except Exception as e:
            print(f"[corpus-catalog] put failed {key}: {e}")
            return None
        time.sleep(min(0.15 * (2 ** attempt), 2.0) + random.uniform(0, 0.15))
    print(f"[corpus-catalog] gave up on {key} after {max_retries + 1} conflicts")
    return None


def _subject_page_key(skey):
    return f"{SUBJECT_PREFIX}{skey}.json"


def load_index(force=False):
    if not enabled():
        return {'version': VERSION, 'subjects': {}}
    now = time.time()
    with _lock:
        cached = _state['index']
        if cached is not None and not force and now - _state['index_ts'] < _INDEX_TTL_S:
            return cached
    try:
        doc, etag = _read_json_with_etag(INDEX_KEY)
    except Exception as e:
        print(f"[corpus-catalog] index load failed: {e}")
        doc, etag = None, None
    doc = doc if isinstance(doc, dict) else {}
    doc.setdefault('version', VERSION)
    doc.setdefault('subjects', {})
    with _lock:
        _state['index'] = doc
        _state['index_etag'] = etag
        _state['index_ts'] = now
    return doc


def load_subject_page(skey, force=False):
    if not skey or not enabled():
        return None
    now = time.time()
    with _lock:
        hit = _state['pages'].get(skey)
        if hit and not force and now - hit[1] < _PAGE_TTL_S:
            return hit[0]
    try:
        doc, _ = _read_json_with_etag(_subject_page_key(skey))
    except Exception as e:
        print(f"[corpus-catalog] page load failed {skey}: {e}")
        doc = None
    with _lock:
        _state['pages'][skey] = (doc, now)
        if len(_state['pages']) > 400:
            oldest = sorted(_state['pages'].items(), key=lambda kv: kv[1][1])[:100]
            for k, _ in oldest:
                _state['pages'].pop(k, None)
    return doc


def source_etag(s3_key):
    return (load_sources().get(s3_key) or {}).get('etag')


def upsert_source(product, s3_key, subject, facts, etag=None, aliases=()):
    """Record every fact a source object yields under its subject.
    Replaces that source's earlier facts (an in-place correction
    changes the object, so its facts change with it). Returns the
    subject key or '' when nothing was recorded."""
    if not enabled():
        return ''
    skey = subject_key(subject)
    if not skey or not facts:
        return ''
    facts = [f for f in facts if isinstance(f, dict)]
    now = _now_iso()
    alias_set = {str(subject).strip()} | {str(a).strip() for a in aliases if a}

    def _mut_page(doc):
        doc.setdefault('subject_key', skey)
        al = set(doc.get('aliases') or []) | alias_set
        doc['aliases'] = sorted(a for a in al if a)[:40]
        doc['subject'] = best_display(doc['aliases'], str(subject).strip())
        kept = [f for f in (doc.get('facts') or [])
                if not (isinstance(f, dict) and (f.get('source') or {}).get('key') == s3_key)]
        kept.extend(facts)
        kept.sort(key=lambda f: (str(f.get('as_of') or ''), str(f.get('kind') or '')), reverse=True)
        doc['facts'] = kept[:600]
        doc['updated'] = now
        return doc

    page = _update_json(_subject_page_key(skey), _mut_page)
    if page is None:
        return ''

    def _mut_index(doc):
        doc.setdefault('version', VERSION)
        subs = doc.setdefault('subjects', {})
        ent = subs.get(skey) or {}
        ent['aliases'] = sorted(set(ent.get('aliases') or []) | alias_set)[:40]
        ent['subject'] = best_display(ent['aliases'], str(subject).strip())
        prods = set(ent.get('products') or []) | {product}
        ent['products'] = sorted(prods)
        ent['n_facts'] = len(page.get('facts') or [])
        ent['updated'] = now
        subs[skey] = ent
        doc.pop('sources', None)
        doc['updated'] = now
        return doc

    def _mut_sources(doc):
        doc[s3_key] = {'etag': etag or '', 'product': product, 'subject_key': skey,
                       'indexed_at': now}
        return doc

    _update_json(INDEX_KEY, _mut_index)
    if not str(s3_key).startswith('thread/'):
        _update_json(SOURCES_KEY, _mut_sources)
        # Dashboard figures outrank chat-stated ones (2026-10-06): a
        # ledger entry that now disagrees with this source is retired.
        try:
            import insights_ledger as _il
            _il.retire_conflicting_entries(str(subject), facts)
        except Exception as e:
            print(f"[corpus-catalog] ledger reconcile skipped: {e}")
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'].pop(skey, None)
    return skey


def bulk_upsert(records):
    """Sweep path: many sources at once. records = iterable of dicts
    {product, s3_key, subject, facts, etag, aliases}. One CAS write per
    subject page and ONE index write, instead of two per source."""
    if not enabled():
        return 0
    by_subject = {}
    for r in records:
        if not r or not r.get('facts'):
            continue
        skey = subject_key(r.get('subject'))
        if not skey:
            continue
        by_subject.setdefault(skey, []).append(r)
    now = _now_iso()
    page_counts = {}

    def _one_subject(skey, recs):
        subject = str(recs[0].get('subject') or '').strip()
        alias_set = set()
        for r in recs:
            alias_set.add(str(r.get('subject') or '').strip())
            alias_set |= {str(a).strip() for a in (r.get('aliases') or ()) if a}
        keys = {r['s3_key'] for r in recs}

        def _mut_page(doc, _recs=recs, _subject=subject, _aliases=alias_set, _keys=keys):
            doc.setdefault('subject_key', skey)
            doc['aliases'] = sorted(a for a in (set(doc.get('aliases') or []) | _aliases) if a)[:40]
            doc['subject'] = best_display(doc['aliases'], _subject)
            kept = [f for f in (doc.get('facts') or [])
                    if not (isinstance(f, dict) and (f.get('source') or {}).get('key') in _keys)]
            for r in _recs:
                kept.extend(f for f in r['facts'] if isinstance(f, dict))
            kept.sort(key=lambda f: (str(f.get('as_of') or ''), str(f.get('kind') or '')), reverse=True)
            doc['facts'] = kept[:600]
            doc['updated'] = now
            return doc

        page = _update_json(_subject_page_key(skey), _mut_page)
        if page is not None:
            page_counts[skey] = len(page.get('facts') or [])

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda kv: _one_subject(*kv), by_subject.items()))

    src_updates = {}

    def _mut_index(doc):
        doc.setdefault('version', VERSION)
        subs = doc.setdefault('subjects', {})
        doc.pop('sources', None)
        for skey, recs in by_subject.items():
            if skey not in page_counts:
                continue
            ent = subs.get(skey) or {}
            al = set(ent.get('aliases') or [])
            prods = set(ent.get('products') or [])
            for r in recs:
                al.add(str(r.get('subject') or '').strip())
                al |= {str(a).strip() for a in (r.get('aliases') or ()) if a}
                prods.add(r['product'])
                src_updates[r['s3_key']] = {'etag': r.get('etag') or '', 'product': r['product'],
                                            'subject_key': skey, 'indexed_at': now}
            ent['aliases'] = sorted(a for a in al if a)[:40]
            ent['subject'] = best_display(ent['aliases'], str(recs[0].get('subject') or '').strip())
            ent['products'] = sorted(prods)
            ent['n_facts'] = page_counts[skey]
            ent['updated'] = now
            subs[skey] = ent
        doc['updated'] = now
        return doc

    _update_json(INDEX_KEY, _mut_index, max_retries=10)

    def _mut_sources(doc):
        doc.update(src_updates)
        return doc

    _update_json(SOURCES_KEY, _mut_sources, max_retries=10)
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'] = {}
    try:
        import insights_ledger as _il
        for skey, recs in by_subject.items():
            facts = [f for r in recs for f in (r.get('facts') or [])]
            _il.retire_conflicting_entries(str(recs[0].get('subject') or ''), facts)
    except Exception as e:
        print(f"[corpus-catalog] ledger reconcile skipped: {e}")
    return len(page_counts)


def load_sources():
    """s3_key -> {etag, product, subject_key, indexed_at}. Sweep only."""
    try:
        doc, _ = _read_json_with_etag(SOURCES_KEY)
    except Exception as e:
        print(f"[corpus-catalog] sources load failed: {e}")
        doc = None
    return doc if isinstance(doc, dict) else {}


def forget_source(s3_key):
    """A deleted object leaves the catalog (sweep only)."""
    src = load_sources().get(s3_key)
    if not src:
        return False
    skey = src.get('subject_key')

    def _mut_page(doc):
        doc['facts'] = [f for f in (doc.get('facts') or [])
                        if (f.get('source') or {}).get('key') != s3_key]
        doc['updated'] = _now_iso()
        return doc

    if skey:
        _update_json(_subject_page_key(skey), _mut_page)

    def _mut_sources(doc):
        doc.pop(s3_key, None)
        return doc

    _update_json(SOURCES_KEY, _mut_sources)
    with _lock:
        _state['index_ts'] = 0.0
        _state['pages'].pop(skey, None)
    return True


# ------------------------------------------------------------- consult
def _window_overlap(a, b):
    """Days of overlap between two {'start','end'} windows; None when
    either side is open-ended."""
    try:
        a0, a1 = _d(a.get('start')), _d(a.get('end'))
        b0, b1 = _d(b.get('start')), _d(b.get('end'))
    except Exception:
        return None
    if not (a0 and a1 and b0 and b1):
        return None
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0, (hi - lo).days + 1)


def _d(s):
    s = str(s or '')[:10]
    return datetime.strptime(s, '%Y-%m-%d').date() if s else None


def same_window(a, b):
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    return (str(a.get('start') or '')[:10] == str(b.get('start') or '')[:10]
            and str(a.get('end') or '')[:10] == str(b.get('end') or '')[:10]
            and bool(a.get('start')))


def resolve_subject(name):
    """The catalog subject key for a name, through aliases."""
    skey = subject_key(name)
    if not skey:
        return ''
    idx = load_index()
    subs = idx.get('subjects') or {}
    if skey in subs:
        return skey
    fold = entity_fold(name)
    for k, ent in subs.items():
        for a in (ent.get('aliases') or []):
            if entity_fold(a) == fold:
                return k
    return ''


def anchors_for(subject, window=None, products=None, limit=60, with_ledger=True):
    """Everything the corpus already says about a subject, newest first,
    with the facts whose window overlaps `window` ranked first."""
    out = {'subject_key': '', 'subject': str(subject or '').strip(), 'facts': [],
           'products': [], 'ledger': []}
    if not enabled() or not str(subject or '').strip():
        return out
    try:
        skey = resolve_subject(subject)
        if skey:
            page = load_subject_page(skey) or {}
            facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)]
            if products:
                facts = [f for f in facts if f.get('product') in set(products)]

            def _score(f):
                ov = 0
                if window and f.get('window'):
                    ov = _window_overlap(window, f['window']) or 0
                    if same_window(window, f['window']):
                        ov += 10_000
                return ov

            # newest first, then the window that matches the ask first
            facts.sort(key=lambda f: str(f.get('as_of') or ''), reverse=True)
            facts.sort(key=_score, reverse=True)
            out['subject_key'] = skey
            out['subject'] = best_display(page.get('aliases') or [], page.get('subject') or out['subject'])
            out['facts'] = facts[:limit]
            out['products'] = sorted({f.get('product') for f in facts if f.get('product')})
    except Exception as e:
        print(f"[corpus-catalog] anchors_for failed: {e}")
    try:
        out['related'] = related_subjects(subject, exclude=out.get('subject_key'))
    except Exception:
        out['related'] = []
    try:
        out['trends'] = trends_for(subject)
    except Exception:
        out['trends'] = []
    if with_ledger:
        try:
            import insights_ledger as _il
            led = _il.consult(subject=str(subject))
            out['ledger'] = list(led.get('entries') or [])[:12]
            if led.get('block'):
                out['ledger_block'] = led['block']
        except Exception:
            pass
    return out


def related_subjects(name, exclude='', limit=5):
    """Catalog subjects whose key carries every token of this name
    (the 'Potential The Influencer Project Ticket Buyer' profiles for
    'The Influencer Project'). Headline facts only."""
    toks = [t for t in entity_fold(name).split() if len(t) > 1]
    if not toks:
        return []
    idx = load_index()
    out = []
    for k, ent in (idx.get('subjects') or {}).items():
        if k == exclude:
            continue
        ktoks = set(k.split('_'))
        if all(t in ktoks for t in toks):
            out.append(k)
    out = out[:limit]
    res = []
    for k in out:
        page = load_subject_page(k) or {}
        facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)
                 and f.get('kind') in ('audience_size', 'sample_size')][:4]
        if facts:
            res.append({'subject_key': k, 'subject': page.get('subject') or k, 'facts': facts})
    return res


def profile_anchor(anchors, window=None):
    """The profile-level size facts (sample, US audience, window) for
    the subject, preferring the one whose window matches."""
    best = None
    for f in anchors.get('facts') or []:
        if f.get('product') != 'profile' or f.get('kind') not in ('sample_size', 'audience_size'):
            continue
        key = f.get('source', {}).get('key')
        best = best or {}
        slot = best.setdefault(key, {'source_key': key, 'window': f.get('window'),
                                     'cut': bool(f.get('cut')), 'as_of': f.get('as_of')})
        slot[f['kind']] = f['value']
    if not best:
        return None
    cands = list(best.values())
    # the parent profile before any of its cuts; newest parent first
    cands.sort(key=lambda c: (c.get('cut', False), ''))
    if window:
        exact = [c for c in cands if same_window(window, c.get('window') or {})]
        if exact:
            return exact[0]
        cands.sort(key=lambda c: (c.get('cut', False),
                                  -(_window_overlap(window, c.get('window') or {}) or 0)))
    return cands[0]


def prior_journey(anchors, window=None):
    """A prior Digital Journey on the subject (same window when one
    exists), as {source_key, window, stages: {label: count}, total}."""
    by_key = {}
    for f in anchors.get('facts') or []:
        if f.get('product') != 'journey':
            continue
        key = f.get('source', {}).get('key')
        slot = by_key.setdefault(key, {'source_key': key, 'window': f.get('window'),
                                       'stages': {}, 'total': None, 'as_of': f.get('as_of')})
        if f.get('kind') == 'stage_count':
            slot['stages'][f['label']] = f['value']
        elif f.get('kind') == 'audience_size':
            slot['total'] = f['value']
    if not by_key:
        return None
    cands = list(by_key.values())
    if window:
        exact = [c for c in cands if same_window(window, c.get('window') or {})]
        if exact:
            exact.sort(key=lambda c: str(c.get('as_of') or ''), reverse=True)
            return exact[0]
    cands.sort(key=lambda c: str(c.get('as_of') or ''), reverse=True)
    return cands[0]


TRENDS_KEY = 'system/corpus_catalog/trends_latest.json'


def write_trends_latest(day, entries):
    """entries: {subject_key: [{source, label, rank, title, as_of}]}
    for the latest Trends IQ snapshot day. One document, replaced
    whole; read lazily by anchors_for."""
    doc = {'day': day, 'updated': _now_iso(), 'subjects': entries}
    try:
        _client().put_object(Bucket=S3_BUCKET, Key=TRENDS_KEY,
                             Body=json.dumps(doc, separators=(',', ':')).encode('utf-8'),
                             ContentType='application/json')
        with _lock:
            _state['trends'] = (doc, time.time())
        return True
    except Exception as e:
        print(f"[corpus-catalog] trends write failed: {e}")
        return False


def trends_for(subject):
    """[{source, label, rank, title, as_of}] for the subject on the
    latest Trends IQ day, or []."""
    if not enabled():
        return []
    skey = subject_key(subject)
    if not skey:
        return []
    now = time.time()
    with _lock:
        hit = _state.get('trends')
    if not hit or now - hit[1] > _PAGE_TTL_S:
        try:
            doc, _ = _read_json_with_etag(TRENDS_KEY)
        except Exception:
            doc = None
        hit = (doc if isinstance(doc, dict) else {}, now)
        with _lock:
            _state['trends'] = hit
    return list(((hit[0] or {}).get('subjects') or {}).get(skey) or [])


def _fmt_value(f):
    v = f.get('value')
    u = f.get('unit')
    try:
        if u == 'pct':
            return f"{float(v):.1f}%"
        if u == 'usd':
            return f"${float(v):,.2f}"
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_window(w):
    if not isinstance(w, dict) or not (w.get('start') or w.get('end')):
        return ''
    return f" [{w.get('start') or '?'} to {w.get('end') or '?'}]"


def anchors_block(anchors, max_lines=40):
    """Prompt block: the figures already published on this subject.
    Binding: a new read must agree with these where it overlaps, and
    must sit inside them for a sub-window."""
    facts = list((anchors or {}).get('facts') or [])
    lines = []
    for f in facts[:max_lines]:
        lines.append(f"- {f.get('note') or f.get('product')}: {f.get('label')} = "
                     f"{_fmt_value(f)}{_fmt_window(f.get('window'))}")
    for tr in ((anchors or {}).get('trends') or [])[:8]:
        lines.append(f"- Trends IQ ({tr.get('as_of')}): {tr.get('title')} ranks #{tr.get('rank')} on {tr.get('label')}")
    for rel in (anchors or {}).get('related') or []:
        for f in rel.get('facts') or []:
            lines.append(f"- RELATED {rel.get('subject')}: {f.get('label')} = "
                         f"{_fmt_value(f)}{_fmt_window(f.get('window'))}")
    led = (anchors or {}).get('ledger_block') or ''
    if not lines and not led:
        return ''
    head = ("PUBLISHED FIGURES ON THIS SUBJECT (binding). Crosswalk already "
            "shows these on the dashboard or stated them in chat. A new read "
            "must agree with them where the windows overlap, must sit inside "
            "them for a sub-window, and must reuse the same partitions and "
            "names. Never contradict them; if the ask needs a different window, "
            "derive from these and say which window you are on. Dashboard "
            "figures (Profile IQ, Digital Journey IQ, Attribution IQ, Brand "
            "Partnership IQ) win over chat-stated figures when they disagree.\n")
    body = '\n'.join(lines)
    if led:
        body = (body + '\n' if body else '') + led
    return head + body


# ---------------------------------------------------------------- hooks
def extract_profile_from_s3(s3_key, display_name='', user='', etag=None, head_bytes=16384):
    """Read-only half of index_profile_from_s3: the record bulk_upsert
    takes, or None."""
    try:
        s3 = _client()
        kw = dict(Bucket=S3_BUCKET, Key=s3_key)
        if head_bytes:
            kw['Range'] = f'bytes=0-{head_bytes - 1}'
        resp = s3.get_object(**kw)
        text = resp['Body'].read().decode('utf-8', errors='replace')
        if head_bytes:
            text = text[:text.rfind('\n')] if '\n' in text else text
        et = etag or (resp.get('ETag') or '').strip('"')
        subject, facts = facts_from_profile_csv_text(s3_key, display_name, text, user=user)
        if head_bytes and not any(f.get('kind') == 'sample_size' for f in facts):
            # Rows sorted by Column (ACCESSORIES first) or a very long
            # BRAND INPUT push the size rows past the head: read it all.
            resp = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
            text = resp['Body'].read().decode('utf-8', errors='replace')
            subject, facts = facts_from_profile_csv_text(s3_key, display_name, text, user=user)
        if not facts:
            return None
        return {'product': 'profile', 's3_key': s3_key, 'subject': subject, 'facts': facts,
                'etag': et, 'aliases': [display_name] if display_name else []}
    except Exception as e:
        print(f"[corpus-catalog] profile extract failed {s3_key}: {e}")
        return None


def index_profile_from_s3(s3_key, display_name='', user='', etag=None, head_bytes=16384):
    """Catalog a profile CSV from its first bytes (BRAND INPUT, SAMPLE
    SIZE rows sit at the top; a full read when they do not). Returns
    subject key or ''."""
    if not enabled():
        return ''
    rec = extract_profile_from_s3(s3_key, display_name, user=user, etag=etag,
                                  head_bytes=head_bytes)
    if not rec:
        return ''
    try:
        return upsert_source('profile', rec['s3_key'], rec['subject'], rec['facts'],
                             etag=rec.get('etag'), aliases=rec.get('aliases') or ())
    except Exception as e:
        print(f"[corpus-catalog] profile index failed {s3_key}: {e}")
        return ''


def index_journey(key, payload, user='', etag=None):
    try:
        subject, facts = facts_from_journey(key, payload, user=user)
        if not subject:
            return ''
        return upsert_source('journey', key, subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] journey index failed {key}: {e}")
        return ''


def index_attribution(slug, assets, fit, user='', etag=None):
    try:
        subject, facts = facts_from_attribution(slug, assets, fit, user=user)
        if not subject:
            return ''
        return upsert_source('attribution', f"intent/{slug}/", subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] attribution index failed {slug}: {e}")
        return ''


def index_bpiq(key, payload, user='', etag=None):
    try:
        subject, facts = facts_from_bpiq(key, payload, user=user)
        if not subject:
            return ''
        return upsert_source('bpiq', key, subject, facts, etag=etag)
    except Exception as e:
        print(f"[corpus-catalog] bpiq index failed {key}: {e}")
        return ''


def record_answer(subject, text, user='', thread_id='', product='chat'):
    """Bank the figures a delivered reply states, under its subject."""
    if not enabled() or not str(subject or '').strip():
        return ''
    try:
        facts = facts_from_answer(subject, text, user=user, thread_id=thread_id,
                                  source_label=product)
        if not facts:
            return ''
        key = f"thread/{user}/{thread_id}/{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:10]}"
        for f in facts:
            f['source']['key'] = key
        return upsert_source(product, key, subject, facts)
    except Exception as e:
        print(f"[corpus-catalog] record_answer failed: {e}")
        return ''


def retire_answer(user, thread_id, text, subject=None):
    """A reply the user rejected (2026-10-06, the learning loop): its
    banked figures leave the catalog so the next answer cannot lean on
    them. Returns the number of facts removed."""
    if not enabled() or not str(text or '').strip():
        return 0
    try:
        key = f"thread/{user}/{thread_id}/{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:10]}"
        idx = load_index(force=True)
        keys = [subject_key(subject)] if subject else list((idx.get('subjects') or {}).keys())
        removed = 0
        for skey in keys:
            if not skey:
                continue
            page = load_subject_page(skey, force=True) or {}
            facts = [f for f in (page.get('facts') or []) if isinstance(f, dict)]
            hit = [f for f in facts if (f.get('source') or {}).get('key') == key]
            if not hit:
                continue

            def _mut(doc, _key=key):
                doc['facts'] = [f for f in (doc.get('facts') or [])
                                if (f.get('source') or {}).get('key') != _key]
                doc['updated'] = _now_iso()
                return doc

            _update_json(_subject_page_key(skey), _mut)
            removed += len(hit)
            with _lock:
                _state['pages'].pop(skey, None)
            if subject:
                break
        if removed:
            print(f"[corpus-catalog] retired {removed} fact(s) from a rejected reply ({user}/{thread_id})")
        return removed
    except Exception as e:
        print(f"[corpus-catalog] retire_answer failed: {e}")
        return 0


def record_answer_async(subject, text, user='', thread_id='', product='chat'):
    t = threading.Thread(target=record_answer, args=(subject, text, user, thread_id, product),
                         daemon=True)
    t.start()
    return t
