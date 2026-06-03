"""
blue_iq.py — Political Tracker module.

Top-level surface used by `app.py`:
    get_filter_options() -> dict
    compute_panel_view(filters: dict) -> dict
    impute_party(uid: str, lookback_days=90) -> tuple[str, float]
    roll_up_political_issues(queries: list[str]) -> dict
    blue_iq_cache_key(filters: dict) -> str

Card output shape (returned by `compute_panel_view`):
{
  "success": True,
  "filters": {...echoed...},
  "panel_size": int,
  "suppressed": bool,
  "generated_at": ISO8601,
  "stale_until": ISO8601,
  "cards": {
    "issue_buckets":   [{bucket, count, share, sample_queries, trend}, ...],
    "search_engines":  [{name, panelists, share}, ...],
    "social_media":    [{name, panelists, share}, ...],
    "top_politicians": [{name, panelists, mention_score}, ...],
    "top_articles":    [{title, source, url, panelists, tone, image}, ...],
    "turnout_intent":  {pct, sample_queries: [...]},
    "compare":         {dems: {...}, reps: {...}, national: {...}}  # optional
    "demo_crosstab":   {age: [...], gender: [...], ethnicity: [...], income: [...]}
  }
}

The frontend never sees a source-attribution field. We blend panel + external
(Google Trends, GDELT, Wikipedia) into the SAME numbers under the hood.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
# These mirror app.py constants exactly so we don't drift.
S3_CACHE_BUCKET    = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_CACHE_PREFIX    = os.environ.get('BLUE_IQ_CACHE_PREFIX', 'blue_iq/cache/')
S3_PARTY_PREFIX    = os.environ.get('BLUE_IQ_PARTY_PREFIX', 'blue_iq/party_imputed/')
CACHE_TTL_S        = int(os.environ.get('BLUE_IQ_CACHE_TTL', '86400'))   # 24h
MIN_CELL_SIZE      = int(os.environ.get('BLUE_IQ_MIN_CELL_SIZE', '100')) # privacy floor
DEFAULT_LOOKBACK_DAYS = int(os.environ.get('BLUE_IQ_LOOKBACK_DAYS', '30'))
OPENAI_MODEL       = os.environ.get('BLUE_IQ_OPENAI_MODEL', 'gpt-4o')

VALID_PARTIES   = ['Democrat', 'Republican', 'Independent', 'Undecided', 'All']
VALID_GEO_TYPES = ['National', 'State', 'DMA']

# Curated allowlists — load once, lazily.
_POLITICIANS: list[str] | None = None
_MEDIA_DOMAINS: set[str] | None = None
_LEAN_LEFT_MEDIA: set[str] | None = None
_LEAN_RIGHT_MEDIA: set[str] | None = None

# Issue-bucket canonical labels (also used by the AI classifier prompt).
ISSUE_BUCKETS = [
    'Economy & Inflation',
    'Gas & Energy',
    'Housing & Rent',
    'Healthcare',
    'Immigration',
    'Abortion & Reproductive Rights',
    'Education & Student Loans',
    'Crime & Safety',
    'Jobs & Wages',
    'Climate',
    'Taxes',
    'Social Security & Medicare',
    'Foreign Policy',
    'Election Integrity & Voting',
    'Guns',
    'Other Policy',
]
NON_POLICY = 'Non-Policy'  # internal label, dropped from output


# ── Lazy reference loaders ──────────────────────────────────────────────────

def _ref_path(filename: str) -> str:
    """Return absolute path to a file in the repo `reference/` directory.

    bg-webapp/ is a submodule, so we look one level up first, then in the
    submodule itself as a fallback.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    candidates = [
        os.path.join(repo_root, 'reference', filename),
        os.path.join(here,      'reference', filename),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]  # return the canonical path even if missing


def _load_politicians() -> list[str]:
    global _POLITICIANS
    if _POLITICIANS is not None:
        return _POLITICIANS
    path = _ref_path('politicians_canonical.txt')
    rows: list[str] = []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                rows.append(s.split('|')[0].strip())
    except FileNotFoundError:
        logger.warning("politicians_canonical.txt not found at %s", path)
    _POLITICIANS = rows
    return _POLITICIANS


def _load_politician_parties() -> dict[str, str]:
    """Returns {name: 'D' | 'R' | 'I'} from `politicians_canonical.txt`.
    File format: `Name|party_code` (one per line). Lines without a pipe default to 'I'.
    """
    path = _ref_path('politicians_canonical.txt')
    out: dict[str, str] = {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                parts = s.split('|')
                name = parts[0].strip()
                code = parts[1].strip().upper() if len(parts) > 1 else 'I'
                if code not in ('D', 'R', 'I'):
                    code = 'I'
                out[name] = code
    except FileNotFoundError:
        pass
    return out


def _load_media_domains() -> tuple[set[str], set[str], set[str]]:
    """Returns (all_political_domains, lean_left, lean_right)."""
    global _MEDIA_DOMAINS, _LEAN_LEFT_MEDIA, _LEAN_RIGHT_MEDIA
    if _MEDIA_DOMAINS is not None and _LEAN_LEFT_MEDIA is not None and _LEAN_RIGHT_MEDIA is not None:
        return _MEDIA_DOMAINS, _LEAN_LEFT_MEDIA, _LEAN_RIGHT_MEDIA
    path = _ref_path('political_media_domains.txt')
    all_d: set[str] = set()
    left: set[str] = set()
    right: set[str] = set()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                parts = [p.strip() for p in s.split('|')]
                dom = parts[0].lower()
                lean = parts[1].upper() if len(parts) > 1 else 'C'  # C = center
                all_d.add(dom)
                if lean == 'L':
                    left.add(dom)
                elif lean == 'R':
                    right.add(dom)
    except FileNotFoundError:
        logger.warning("political_media_domains.txt not found at %s", path)
    _MEDIA_DOMAINS = all_d
    _LEAN_LEFT_MEDIA = left
    _LEAN_RIGHT_MEDIA = right
    return _MEDIA_DOMAINS, _LEAN_LEFT_MEDIA, _LEAN_RIGHT_MEDIA


# ── ClickHouse connection (lazy + reused) ────────────────────────────────────

def _ch():
    """Returns a fresh ClickHouse connection. Each caller closes their own."""
    # Local import so module load doesn't require the connector being importable
    # in environments where Blue IQ isn't enabled.
    try:
        from clickhouse_connector import connect_clickhouse  # type: ignore
    except ImportError:
        from migration.clickhouse_connector import connect_clickhouse  # type: ignore
    return connect_clickhouse()


def _ch_query(sql: str, params: dict | None = None) -> list[tuple]:
    """Run a SELECT and return rows. Tiny wrapper to keep callers small."""
    conn = _ch()
    try:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── S3 cache (mirrors app.py.load_json_from_s3 pattern) ──────────────────────

def _s3():
    """Return the dashboard S3 client. Imports from app.py at call time to
    reuse the configured session (signing v4, us-east-2). Falls back to a
    fresh boto3 client if app.py isn't importable yet."""
    try:
        from app import s3_client  # type: ignore
        if s3_client is not None:
            return s3_client
    except Exception:
        pass
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def blue_iq_cache_key(filters: dict) -> str:
    """Deterministic cache key from a filter dict."""
    canonical = json.dumps({
        'party':     filters.get('party') or 'All',
        'geo_type':  filters.get('geo_type') or 'National',
        'geo_value': filters.get('geo_value') or '',
        'lookback':  int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS),
        'version':   1,
    }, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _cache_get(filters: dict) -> Optional[dict]:
    key = S3_CACHE_PREFIX + blue_iq_cache_key(filters) + '.json'
    try:
        s3 = _s3()
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        last_mod = resp.get('LastModified')
        if last_mod and (datetime.now(timezone.utc) - last_mod).total_seconds() > CACHE_TTL_S:
            return None
        data = json.loads(resp['Body'].read().decode('utf-8'))
        return data
    except Exception as e:
        msg = str(e)
        if 'NoSuchKey' not in msg and '404' not in msg:
            logger.debug("blue_iq cache miss: %s", msg)
        return None


def _cache_put(filters: dict, payload: dict) -> None:
    key = S3_CACHE_PREFIX + blue_iq_cache_key(filters) + '.json'
    try:
        s3 = _s3()
        s3.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=key,
            Body=json.dumps(payload).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as e:
        logger.warning("blue_iq cache write failed: %s", e)


# ── Filter validation ───────────────────────────────────────────────────────

def _normalize_filters(filters: dict | None) -> dict:
    f = dict(filters or {})
    party = (f.get('party') or 'All').strip()
    if party not in VALID_PARTIES:
        party = 'All'
    geo_type = (f.get('geo_type') or 'National').strip()
    if geo_type not in VALID_GEO_TYPES:
        geo_type = 'National'
    geo_value = (f.get('geo_value') or '').strip()
    if geo_type == 'National':
        geo_value = ''
    try:
        lookback_days = int(f.get('lookback_days') or DEFAULT_LOOKBACK_DAYS)
    except Exception:
        lookback_days = DEFAULT_LOOKBACK_DAYS
    lookback_days = max(7, min(180, lookback_days))
    return {
        'party':         party,
        'geo_type':      geo_type,
        'geo_value':     geo_value,
        'lookback_days': lookback_days,
    }


# ── Filter options (states/DMAs/parties) ─────────────────────────────────────

def get_filter_options() -> dict:
    """Returns the dropdown choices for the filter bar. Cached for 24h in-process."""
    cache_key = '_filter_options_v1'
    cached = _FILTER_OPTIONS_CACHE.get(cache_key)
    if cached and (time.time() - cached['ts'] < 3600):
        return cached['data']

    states: list[str] = []
    dmas: list[str]   = []
    try:
        rows = _ch_query("""
            SELECT STATE, count() AS n
            FROM userdata.user_data_sanitized
            WHERE STATE IS NOT NULL AND STATE != ''
            GROUP BY STATE
            HAVING n >= %(floor)s
            ORDER BY STATE
        """, {'floor': MIN_CELL_SIZE})
        states = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.warning("filter_options: state pull failed: %s", e)
    try:
        rows = _ch_query("""
            SELECT DMA, count() AS n
            FROM userdata.user_data_sanitized
            WHERE DMA IS NOT NULL AND DMA != ''
            GROUP BY DMA
            HAVING n >= %(floor)s
            ORDER BY DMA
        """, {'floor': MIN_CELL_SIZE})
        dmas = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.warning("filter_options: dma pull failed: %s", e)

    data = {
        'parties':       VALID_PARTIES,
        'geo_types':     VALID_GEO_TYPES,
        'states':        states,
        'dmas':          dmas,
        'min_cell_size': MIN_CELL_SIZE,
        'default_lookback_days': DEFAULT_LOOKBACK_DAYS,
    }
    _FILTER_OPTIONS_CACHE[cache_key] = {'ts': time.time(), 'data': data}
    return data


_FILTER_OPTIONS_CACHE: dict[str, dict] = {}


# ── Party imputation ────────────────────────────────────────────────────────

def impute_party(uid: str, lookback_days: int = 90, source: str = 'heuristic_v1'
                  ) -> tuple[str, float]:
    """Return (party, confidence in 0..1). For one UID. Mostly called in bulk
    by `bulk_impute_party_to_s3`, not per-request.
    """
    polparty = _load_politician_parties()
    _, left_media, right_media = _load_media_domains()
    if not (polparty or left_media or right_media):
        return ('Undecided', 0.0)

    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    rows = _ch_query("""
        SELECT lower(COMMON_NAME) AS cn, lower(DOMAIN) AS dom, URL
        FROM clickstream.clickstream_final
        WHERE UID = %(uid)s AND DELIVERED >= toDate(%(start)s)
    """, {'uid': uid, 'start': start})

    return _score_party_from_rows(rows, polparty, left_media, right_media)


def _score_party_from_rows(rows: Iterable, polparty: dict[str, str],
                             left_media: set[str], right_media: set[str]
                             ) -> tuple[str, float]:
    """Pure scoring from already-fetched (cn, dom, url) rows."""
    d_score = 0.0
    r_score = 0.0
    political_signal = 0

    # Donor brand short-circuit (strong signal).
    DONOR_LEFT  = {'actblue', 'actblue.com', 'dccc', 'dccc.org', 'dnc', 'democrats.org'}
    DONOR_RIGHT = {'winred', 'winred.com', 'nrcc', 'nrcc.org', 'gop.com', 'rnc'}

    pol_tokens_d: set[str] = set()
    pol_tokens_r: set[str] = set()
    for name, code in polparty.items():
        norm = name.lower()
        if code == 'D':
            pol_tokens_d.add(norm)
        elif code == 'R':
            pol_tokens_r.add(norm)

    for r in rows or []:
        try:
            cn = (r[0] or '').lower()
            dom = (r[1] or '').lower()
            url = (r[2] or '').lower() if len(r) > 2 else ''
        except (IndexError, TypeError):
            continue

        if cn in DONOR_LEFT or dom in DONOR_LEFT:
            d_score += 5.0
            political_signal += 5
            continue
        if cn in DONOR_RIGHT or dom in DONOR_RIGHT:
            r_score += 5.0
            political_signal += 5
            continue

        if dom in left_media:
            d_score += 1.0
            political_signal += 1
        elif dom in right_media:
            r_score += 1.0
            political_signal += 1

        # Politician name match (URL or common_name)
        hay = (cn or '') + ' ' + (url or '')
        for tok in pol_tokens_d:
            if len(tok) >= 5 and tok in hay:
                d_score += 0.5
                political_signal += 1
                break
        for tok in pol_tokens_r:
            if len(tok) >= 5 and tok in hay:
                r_score += 0.5
                political_signal += 1
                break

    total = d_score + r_score
    if political_signal < 3:
        return ('Undecided', max(0.0, min(0.5, political_signal / 6.0)))
    if total == 0:
        return ('Independent', 0.1)
    lean = (d_score - r_score) / total
    conf = abs(lean)
    if conf < 0.35:
        return ('Independent', round(conf, 3))
    if lean > 0:
        return ('Democrat', round(conf, 3))
    return ('Republican', round(conf, 3))


def bulk_impute_party_to_s3(lookback_days: int = 90, max_uids: int = 0
                              ) -> dict[str, int]:
    """Run the imputer over every UID with recent activity, persist to S3.

    Output: `s3://dashboard-inputs/blue_iq/party_imputed/all.json`
        { uid: {party, confidence, computed_at}, ... }

    Returns a count breakdown. Idempotent — overwrites.
    """
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    polparty = _load_politician_parties()
    _, left_media, right_media = _load_media_domains()

    # Pull all relevant clicks once, group by UID in-process. Cheaper than
    # per-UID SELECTs because the clickstream table is sorted by (DELIVERED, UID).
    # We narrow to politically-relevant rows first via a domain/cn filter.
    rel_domains = list((left_media | right_media | {
        'actblue.com','dccc.org','democrats.org','winred.com','nrcc.org','gop.com',
    }))
    pol_likes = ' OR '.join([f"position(lower(URL), %(pol{i})s) > 0"
                              for i in range(min(50, len(polparty)))])
    polparts = {f'pol{i}': name.lower() for i, name in enumerate(list(polparty.keys())[:50])}

    limit_clause = f" LIMIT {int(max_uids)} BY UID" if max_uids and max_uids > 0 else ''
    sql = f"""
        SELECT UID, lower(COMMON_NAME), lower(DOMAIN), URL
        FROM clickstream.clickstream_final
        WHERE DELIVERED >= toDate(%(start)s)
          AND (lower(DOMAIN) IN %(rel)s OR ({pol_likes or '1=0'}))
        ORDER BY UID
        {limit_clause}
    """
    rows = _ch_query(sql, {'start': start, 'rel': rel_domains, **polparts})

    by_uid: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_uid[r[0]].append((r[1], r[2], r[3]))

    out: dict[str, dict] = {}
    counts = Counter()
    now_iso = datetime.now(timezone.utc).isoformat()
    for uid, urows in by_uid.items():
        party, conf = _score_party_from_rows(urows, polparty, left_media, right_media)
        out[uid] = {'party': party, 'confidence': conf, 'computed_at': now_iso}
        counts[party] += 1

    s3 = _s3()
    s3.put_object(
        Bucket=S3_CACHE_BUCKET,
        Key=S3_PARTY_PREFIX + 'all.json',
        Body=json.dumps(out).encode('utf-8'),
        ContentType='application/json',
    )
    counts['total_imputed'] = sum(counts.values())
    return dict(counts)


def _load_imputed_party_map() -> dict[str, dict]:
    """Loads the bulk-imputed (uid -> {party, confidence}) map from S3."""
    try:
        s3 = _s3()
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=S3_PARTY_PREFIX + 'all.json')
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.debug("party_imputed map not yet available: %s", e)
        return {}


# ── AI issue-bucket rollup (forked from build_search_themes_for_day) ─────────

def _openai_client():
    """Return a configured OpenAI client (or None if no API key)."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return None
    try:
        # Prefer the shared app client if already initialized
        try:
            from app import get_openai_client  # type: ignore
            client = get_openai_client()
            if client is not None:
                return client
        except Exception:
            pass
        from openai import OpenAI
        return OpenAI(api_key=api_key, timeout=120.0)
    except Exception as e:
        logger.warning("openai client init failed: %s", e)
        return None


_BUCKETS_LIST_FOR_PROMPT = '\n'.join(f'- {b}' for b in ISSUE_BUCKETS)


def roll_up_political_issues(queries: list[dict], use_external: bool = True
                              ) -> list[dict]:
    """Classify search queries into political-issue buckets via OpenAI.

    `queries`: [{'term': str, 'count': int}, ...]

    Returns:
      [{'bucket': str, 'count': int, 'share': float, 'sample_queries': [str, ...]}, ...]
    Sorted by count desc. Non-policy queries are dropped from the output.
    """
    kept = []
    for q in queries or []:
        term = (q.get('term') or '').strip()
        try:
            cnt = int(round(float(q.get('count', 0) or 0)))
        except Exception:
            cnt = 0
        if term and cnt > 0 and len(term) < 400:
            kept.append({'term': term, 'count': cnt})

    if not kept:
        return []

    client = _openai_client()
    if client is None:
        # Without AI we can't reliably bucket; return raw top-K as "Other Policy".
        kept.sort(key=lambda x: -x['count'])
        top = kept[:50]
        total = sum(t['count'] for t in top) or 1
        return [{
            'bucket': 'Other Policy',
            'count':  total,
            'share':  1.0,
            'sample_queries': [t['term'] for t in top[:10]],
            'trend':  0.0,
        }]

    sys_msg = (
        'You classify analytics search queries to support a political dashboard.\n'
        'For each query, decide:\n'
        '  1. Is it about a POLICY issue a politician could address?\n'
        '     (Drop sports, celebrity, weather, shopping, dating, recipes, gaming,\n'
        '      music, movies, TV, generic curiosity. KEEP: cost of living,\n'
        '      housing affordability, healthcare access, immigration, taxes,\n'
        '      voting, candidate positions, civil rights, climate policy, etc.)\n'
        '  2. If policy, assign exactly ONE bucket from this list:\n'
        f'{_BUCKETS_LIST_FOR_PROMPT}\n'
        f'     If non-policy, return "{NON_POLICY}".\n'
        'INPUT FORMAT: each line is INDEX<TAB>JSON_STRING_QUERY\n'
        'OUTPUT FORMAT: strict JSON: {"items":[{"i":0,"b":"..."},...]}\n'
        'one entry per input line, same indices, no commentary.'
    )

    bucket_count: dict[str, int] = defaultdict(int)
    bucket_examples: dict[str, list[str]] = defaultdict(list)
    assignments: dict[int, str] = {}

    batch_size = 75
    for start in range(0, len(kept), batch_size):
        batch = kept[start:start + batch_size]
        lines = [f'{start + i}\t{json.dumps(row["term"], ensure_ascii=False)}'
                 for i, row in enumerate(batch)]
        block = '\n'.join(lines)
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {'role': 'system', 'content': sys_msg},
                    {'role': 'user',   'content': f'Classify each query:\n{block}'},
                ],
                response_format={'type': 'json_object'},
                temperature=0.0,
                max_tokens=4096,
            )
            raw = resp.choices[0].message.content or ''
            parsed = json.loads(raw)
            for it in (parsed.get('items') or []):
                try:
                    ii = int(it.get('i'))
                    b = (str(it.get('b') or '').strip())
                    if b not in ISSUE_BUCKETS and b != NON_POLICY:
                        b = 'Other Policy'
                    assignments[ii] = b
                except Exception:
                    continue
        except Exception as e:
            logger.warning("issue-bucket batch %d failed: %s", start // batch_size, e)
        for i in range(len(batch)):
            assignments.setdefault(start + i, NON_POLICY)

    for i, row in enumerate(kept):
        b = assignments.get(i) or NON_POLICY
        if b == NON_POLICY:
            continue
        bucket_count[b] += row['count']
        if len(bucket_examples[b]) < 12:
            bucket_examples[b].append(row['term'])

    total = sum(bucket_count.values()) or 1
    out = []
    for b in sorted(bucket_count, key=lambda k: -bucket_count[k]):
        out.append({
            'bucket': b,
            'count':  int(bucket_count[b]),
            'share':  round(bucket_count[b] / total, 4),
            'sample_queries': bucket_examples[b][:10],
            'trend':  0.0,
        })
    return out


# ── Card queries (the 5 panel queries) ──────────────────────────────────────

def _geo_filter_clause(geo_type: str, geo_value: str) -> tuple[str, dict]:
    """Returns (SQL fragment that filters user_data_sanitized U, params)."""
    if geo_type == 'State' and geo_value:
        return ("U.STATE = %(geo_value)s", {'geo_value': geo_value})
    if geo_type == 'DMA' and geo_value:
        return ("U.DMA = %(geo_value)s", {'geo_value': geo_value})
    return ("1=1", {})


def _party_filter_uids(party: str) -> Optional[set[str]]:
    """Returns the set of UIDs that match the party filter. None means no filter.

    Reads from the pre-computed `blue_iq/party_imputed/all.json` map. If the
    map is missing (first run), this returns None and the caller falls back to
    No party filter (so cards still render — but party-specific cuts won't
    work until the cron has run once).
    """
    if party == 'All' or not party:
        return None
    party_map = _load_imputed_party_map()
    if not party_map:
        return None
    uids = {uid for uid, v in party_map.items() if v.get('party') == party}
    return uids if uids else set()


def _panel_uids(party: str, geo_type: str, geo_value: str) -> set[str]:
    """Return the set of UIDs that match BOTH party + geo filters."""
    geo_clause, geo_params = _geo_filter_clause(geo_type, geo_value)
    rows = _ch_query(f"""
        SELECT DISTINCT UID
        FROM userdata.user_data_sanitized AS U
        WHERE {geo_clause}
    """, geo_params)
    geo_uids = {r[0] for r in rows if r and r[0]}
    party_uids = _party_filter_uids(party)
    if party_uids is None:
        return geo_uids
    return geo_uids & party_uids


def _card_search_engines(uids: set[str], start_date: str) -> list[dict]:
    """Card B: Search engine share among the filtered panel."""
    if not uids:
        return []
    rows = _ch_query(f"""
        WITH search_brands AS (
            SELECT DISTINCT BRAND
            FROM reference.host_mapping
            WHERE CATEGORY = 'Search Engine/AI'
              AND coalesce(SECTION, '') != 'Hidden'
        )
        SELECT COMMON_NAME, uniqExact(UID) AS panelists
        FROM clickstream.clickstream_final
        WHERE UID IN %(uids)s
          AND DELIVERED >= toDate(%(start)s)
          AND COMMON_NAME IN (SELECT BRAND FROM search_brands)
        GROUP BY COMMON_NAME
        ORDER BY panelists DESC
        LIMIT 20
    """, {'uids': list(uids), 'start': start_date})
    total = sum(int(r[1]) for r in rows) or 1
    return [{
        'name': r[0],
        'panelists': int(r[1]),
        'share': round(int(r[1]) / total, 4),
    } for r in rows]


def _card_social_media(uids: set[str], start_date: str) -> list[dict]:
    if not uids:
        return []
    rows = _ch_query(f"""
        WITH social_brands AS (
            SELECT DISTINCT BRAND
            FROM reference.host_mapping
            WHERE CATEGORY = 'Social Media'
              AND coalesce(SECTION, '') != 'Hidden'
        )
        SELECT COMMON_NAME, uniqExact(UID) AS panelists
        FROM clickstream.clickstream_final
        WHERE UID IN %(uids)s
          AND DELIVERED >= toDate(%(start)s)
          AND COMMON_NAME IN (SELECT BRAND FROM social_brands)
        GROUP BY COMMON_NAME
        ORDER BY panelists DESC
        LIMIT 20
    """, {'uids': list(uids), 'start': start_date})
    total = sum(int(r[1]) for r in rows) or 1
    return [{
        'name': r[0],
        'panelists': int(r[1]),
        'share': round(int(r[1]) / total, 4),
    } for r in rows]


def _card_top_politicians(uids: set[str], start_date: str,
                            external: dict | None = None) -> list[dict]:
    politicians = _load_politicians()
    if not politicians or not uids:
        # Even without panel data, surface GDELT + Wikipedia external signal.
        return _blend_politicians({}, external or {}, politicians)

    # Build a single OR-clause of position(lower(URL), 'name') matches.
    where_parts = []
    params: dict = {'uids': list(uids), 'start': start_date}
    for i, name in enumerate(politicians[:60]):
        k = f'pol{i}'
        params[k] = name.lower()
        where_parts.append(f"position(lower(URL), %({k})s) > 0")
    where = ' OR '.join(where_parts) if where_parts else '1=0'

    rows = _ch_query(f"""
        SELECT URL
        FROM clickstream.clickstream_final
        WHERE UID IN %(uids)s
          AND DELIVERED >= toDate(%(start)s)
          AND ({where})
    """, params)

    panelist_count: Counter = Counter()
    # We re-resolve which politician each URL hit (CH doesn't easily report it)
    for r in rows:
        url_l = (r[0] or '').lower()
        for name in politicians[:60]:
            if name.lower() in url_l:
                panelist_count[name] += 1
                break

    return _blend_politicians(dict(panelist_count), external or {}, politicians)


def _blend_politicians(panel_counts: dict[str, int], external: dict,
                        politicians: list[str]) -> list[dict]:
    """Blend panel mentions + Google Trends + GDELT + Wikipedia into one score."""
    trends = external.get('google_trends_politicians') or {}
    gdelt  = external.get('gdelt_politician_mentions') or {}
    wiki   = external.get('wiki_pageviews') or {}

    # Normalize each source to 0..1 by max for blending.
    def norm(d: dict[str, int | float]) -> dict[str, float]:
        if not d:
            return {}
        mx = max(d.values()) or 1
        return {k: (v / mx) for k, v in d.items()}

    p = norm(panel_counts)
    t = norm(trends)
    g = norm(gdelt)
    w = norm(wiki)

    names = set(politicians) | set(panel_counts) | set(trends) | set(gdelt) | set(wiki)
    out = []
    for name in names:
        # Weighted blend. Panel signal dominates when present; external fills in.
        score = (
            0.55 * p.get(name, 0.0) +
            0.20 * t.get(name, 0.0) +
            0.15 * g.get(name, 0.0) +
            0.10 * w.get(name, 0.0)
        )
        if score <= 0:
            continue
        out.append({
            'name':            name,
            'panelists':       int(panel_counts.get(name, 0)),
            'mention_score':   round(score, 4),
        })
    out.sort(key=lambda r: -r['mention_score'])
    return out[:25]


def _card_top_articles(uids: set[str], start_date: str,
                         external: dict | None = None) -> list[dict]:
    """Card E: Top political articles. Panel signal (which URLs were read by
    the filtered panel) blended with GDELT (gives us titles + source images
    that aren't in our clickstream).
    """
    domains_all, _, _ = _load_media_domains()
    panel_url_counts: Counter = Counter()
    if uids and domains_all:
        rows = _ch_query(f"""
            SELECT URL, lower(DOMAIN) AS dom, uniqExact(UID) AS p
            FROM clickstream.clickstream_final
            WHERE UID IN %(uids)s
              AND DELIVERED >= toDate(%(start)s)
              AND lower(DOMAIN) IN %(doms)s
              AND length(URL) > 30
            GROUP BY URL, dom
            HAVING p >= 2
            ORDER BY p DESC
            LIMIT 200
        """, {'uids': list(uids), 'start': start_date, 'doms': list(domains_all)})
        for url, dom, p in rows:
            panel_url_counts[(url, dom)] = int(p)

    gdelt_articles = (external or {}).get('gdelt_articles') or []
    by_url: dict[str, dict] = {}

    # Seed from GDELT (gives us nice titles).
    for art in gdelt_articles:
        u = art.get('url') or ''
        if not u:
            continue
        by_url[u] = {
            'title':  art.get('title') or _title_from_url(u),
            'source': art.get('source') or '',
            'url':    u,
            'panelists': 0,
            'tone':   float(art.get('tone') or 0.0),
            'image':  art.get('social_image') or '',
        }
    # Overlay panel counts (and add panel-only URLs that GDELT missed).
    for (url, dom), p in panel_url_counts.items():
        if url in by_url:
            by_url[url]['panelists'] = max(by_url[url].get('panelists', 0), p)
        else:
            by_url[url] = {
                'title':  _title_from_url(url),
                'source': dom,
                'url':    url,
                'panelists': p,
                'tone':   0.0,
                'image':  '',
            }

    # Rank: panelists first, then tone-adjusted GDELT reach.
    ranked = list(by_url.values())
    ranked.sort(key=lambda a: (-a['panelists'], -abs(a.get('tone', 0.0))))
    return ranked[:30]


def _title_from_url(url: str) -> str:
    try:
        path = urllib.parse.urlparse(url).path
        slug = path.rstrip('/').split('/')[-1]
        slug = urllib.parse.unquote(slug).replace('-', ' ').replace('_', ' ')
        slug = re.sub(r'\.[a-z]{2,5}$', '', slug, flags=re.I).strip()
        return slug.title()[:140] if slug else url
    except Exception:
        return url


# ── Card A: issue buckets (panel queries + Trends, then AI rollup) ──────────

def _fetch_panel_search_queries(uids: set[str], start_date: str,
                                  limit: int = 3000) -> list[dict]:
    """Pulls panel members' search query strings via reference.search_text_mapping."""
    if not uids:
        return []
    rows = _ch_query("""
        WITH q AS (
            SELECT
                lower(BRAND_NAME) AS qstr,
                SEARCH_TEXT_NORMALIZED
            FROM reference.search_text_mapping
            WHERE TYPE = 'query'
              AND BRAND_NAME IS NOT NULL
              AND BRAND_NAME != ''
        )
        SELECT
            SEARCH_TEXT_NORMALIZED AS term,
            uniqExact(C.UID) AS users,
            count() AS clicks
        FROM clickstream.clickstream_final AS C
        ANY INNER JOIN q
            ON position(lower(C.URL), q.SEARCH_TEXT_NORMALIZED) > 0
        WHERE C.UID IN %(uids)s
          AND C.DELIVERED >= toDate(%(start)s)
          AND length(SEARCH_TEXT_NORMALIZED) BETWEEN 6 AND 200
        GROUP BY term
        HAVING users >= 2
        ORDER BY users DESC
        LIMIT %(lim)s
    """, {'uids': list(uids), 'start': start_date, 'lim': int(limit)})
    return [{'term': r[0], 'count': int(r[1])} for r in rows if r and r[0]]


def _card_issue_buckets(uids: set[str], start_date: str,
                          external: dict | None = None) -> list[dict]:
    panel_q = _fetch_panel_search_queries(uids, start_date, limit=3000)

    # Blend in Google Trends top issues (so even thin panels surface signal).
    trends_top = (external or {}).get('google_trends_top') or []
    blended: list[dict] = list(panel_q)
    for row in trends_top:
        term = (row.get('term') or '').strip()
        if not term:
            continue
        blended.append({'term': term, 'count': max(1, int(row.get('score', 0)) // 1000)})
        for rq in (row.get('related') or [])[:5]:
            if rq:
                blended.append({'term': rq, 'count': 1})

    return roll_up_political_issues(blended)


# ── Bonus cards (F. turnout intent, J. compare, L. demo crosstab) ────────────

_TURNOUT_PATTERNS = [
    'register to vote', 'voter registration', 'how to vote', 'where to vote',
    'polling location', 'polling place', 'absentee ballot', 'mail in ballot',
    'mail-in ballot', 'early voting', 'vote by mail', 'voter id',
    'election day', 'ballot drop box',
]


def _card_turnout_intent(uids: set[str], start_date: str) -> dict:
    """Pct of the filtered panel who searched for voter-action terms."""
    if not uids:
        return {'pct': 0.0, 'sample_queries': []}
    like_terms = [f"%{t}%" for t in _TURNOUT_PATTERNS]
    rows = _ch_query("""
        SELECT lower(URL) AS u, uniqExact(UID) AS p
        FROM clickstream.clickstream_final
        WHERE UID IN %(uids)s
          AND DELIVERED >= toDate(%(start)s)
          AND multiMatchAny(lower(URL), %(terms)s) > 0
        GROUP BY u
        ORDER BY p DESC
        LIMIT 30
    """, {'uids': list(uids), 'start': start_date, 'terms': _TURNOUT_PATTERNS})

    sample_queries: list[str] = []
    matched_users: set[str] = set()
    if rows:
        # Quick second pass to count unique users
        urows = _ch_query("""
            SELECT uniqExact(UID)
            FROM clickstream.clickstream_final
            WHERE UID IN %(uids)s
              AND DELIVERED >= toDate(%(start)s)
              AND multiMatchAny(lower(URL), %(terms)s) > 0
        """, {'uids': list(uids), 'start': start_date, 'terms': _TURNOUT_PATTERNS})
        if urows:
            n_users = int(urows[0][0] or 0)
            return {
                'pct': round(n_users / max(1, len(uids)), 4),
                'panelists': n_users,
                'sample_queries': [r[0] for r in rows[:8]],
            }
    return {'pct': 0.0, 'panelists': 0, 'sample_queries': []}


def _card_demo_crosstab(uids: set[str]) -> dict:
    if not uids:
        return {}
    out: dict[str, list[dict]] = {}
    for col, label in [('AGE', 'age'), ('GENDER', 'gender'),
                       ('ETHNICITY', 'ethnicity'), ('INCOME', 'income')]:
        try:
            rows = _ch_query(f"""
                SELECT {col} AS v, count() AS n
                FROM userdata.user_data_sanitized
                WHERE UID IN %(uids)s
                  AND {col} IS NOT NULL AND {col} != ''
                GROUP BY v
                ORDER BY n DESC
            """, {'uids': list(uids)})
            total = sum(int(r[1]) for r in rows) or 1
            out[label] = [{
                'value': r[0],
                'panelists': int(r[1]),
                'share': round(int(r[1]) / total, 4),
            } for r in rows]
        except Exception as e:
            logger.debug("demo crosstab %s failed: %s", col, e)
            out[label] = []
    return out


# ── Main entry point ────────────────────────────────────────────────────────

def compute_panel_view(filters: dict, *, force_refresh: bool = False) -> dict:
    """Build a Blue IQ dashboard view for the filter combo. Cache-aware."""
    f = _normalize_filters(filters)

    # 1. Try cache
    if not force_refresh:
        cached = _cache_get(f)
        if cached:
            cached['cache_hit'] = True
            return cached

    start_date = (datetime.now(timezone.utc) - timedelta(days=f['lookback_days'])).strftime('%Y-%m-%d')

    # 2. Resolve the panel slice
    try:
        uids = _panel_uids(f['party'], f['geo_type'], f['geo_value'])
    except Exception as e:
        logger.warning("panel uid resolution failed: %s", e)
        uids = set()

    suppressed = len(uids) < MIN_CELL_SIZE
    panel_size = len(uids)

    # 3. External signals (best-effort, parallel-safe; we just sequentially call them).
    try:
        from external_signals import fetch_all_external  # type: ignore
    except ImportError:
        from .external_signals import fetch_all_external  # type: ignore

    politicians_for_external = _load_politicians()[:20]
    external = fetch_all_external(
        state=f['geo_value'] if f['geo_type'] == 'State' else None,
        lookback_days=f['lookback_days'],
        politician_names=politicians_for_external,
    )

    # 4. Build cards. If suppressed, still let external sources fill where they can —
    # but mark the suppression and zero out panel-specific stats.
    if suppressed:
        cards = {
            'issue_buckets':   _card_issue_buckets(set(), start_date, external=external),
            'search_engines':  [],
            'social_media':    [],
            'top_politicians': _card_top_politicians(set(), start_date, external=external),
            'top_articles':    _card_top_articles(set(), start_date, external=external),
            'turnout_intent':  {'pct': 0.0, 'panelists': 0, 'sample_queries': []},
            'demo_crosstab':   {},
        }
    else:
        cards = {
            'issue_buckets':   _card_issue_buckets(uids, start_date, external=external),
            'search_engines':  _card_search_engines(uids, start_date),
            'social_media':    _card_social_media(uids, start_date),
            'top_politicians': _card_top_politicians(uids, start_date, external=external),
            'top_articles':    _card_top_articles(uids, start_date, external=external),
            'turnout_intent':  _card_turnout_intent(uids, start_date),
            'demo_crosstab':   _card_demo_crosstab(uids),
        }

    # 5. Compare-mode (J) — only when geo is set, so we have a meaningful slice.
    compare = {}
    if f['geo_type'] != 'National' and f['geo_value']:
        try:
            compare = _build_compare(f, start_date, external)
        except Exception as e:
            logger.debug("compare build failed: %s", e)
            compare = {}

    now = datetime.now(timezone.utc)
    payload = {
        'success':      True,
        'filters':      f,
        'panel_size':   panel_size,
        'suppressed':   suppressed,
        'min_cell_size': MIN_CELL_SIZE,
        'generated_at': now.isoformat(),
        'stale_until':  (now + timedelta(seconds=CACHE_TTL_S)).isoformat(),
        'cards':        cards,
        'compare':      compare,
        'cache_hit':    False,
    }
    if suppressed:
        payload['message'] = (
            f'Sample below minimum cell size ({MIN_CELL_SIZE} panelists). '
            'External signal (Trends, news, Wikipedia) shown where available; '
            'panel-derived cards are hidden.'
        )

    # 6. Cache and return.
    _cache_put(f, payload)
    return payload


def _build_compare(filters: dict, start_date: str, external: dict) -> dict:
    """Card J: side-by-side Dems / Reps / Indep / National for the same geo."""
    out = {}
    for label, party in [('dems', 'Democrat'), ('reps', 'Republican'),
                          ('indeps', 'Independent'), ('national', 'All')]:
        uids = _panel_uids(party, filters['geo_type'], filters['geo_value']) if party != 'All' \
            else _panel_uids('All', filters['geo_type'], filters['geo_value'])
        if len(uids) < MIN_CELL_SIZE:
            out[label] = {'panel_size': len(uids), 'suppressed': True}
            continue
        out[label] = {
            'panel_size': len(uids),
            'suppressed': False,
            'search_engines': _card_search_engines(uids, start_date)[:6],
            'social_media':   _card_social_media(uids, start_date)[:6],
            'turnout_pct':    _card_turnout_intent(uids, start_date).get('pct', 0.0),
        }
    return out
