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
S3_CUBE_KEY        = os.environ.get('BLUE_IQ_CUBE_KEY', 'blue_iq/aggregates/latest.json')
# Per-lookback cube keys. These mirror the aggregator's output layout.
# The reader picks the file whose lookback matches the user's selected
# window (Live=1d, default=30d). If a per-lookback key is missing, the
# reader falls back to the legacy `latest.json` (which is always the
# 30d cube — written by the aggregator's also_write_legacy=True path).
def _cube_key_for_lookback(lookback_days: int) -> str:
    return f"blue_iq/aggregates/cube_{int(lookback_days)}d.json"
CACHE_TTL_S        = int(os.environ.get('BLUE_IQ_CACHE_TTL', '86400'))   # 24h
CUBE_STALE_S       = int(os.environ.get('BLUE_IQ_CUBE_STALE_S', '172800'))  # 48h before warning
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
    File format: `Name|party_code|cycle_flags` (one per line). Lines without a
    pipe default to 'I'. cycle_flags is optional and consumed by
    _load_politician_cycle_flags().
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


def _load_politician_cycle_flags() -> dict[str, set[str]]:
    """Returns {name: {'2026', '2028p', ...}} from `politicians_canonical.txt`.

    The 3rd pipe-delimited column on each line is a comma-separated set of
    cycle/role flags. Empty / missing → no flags. Used to derive the
    "Top Candidates" card (filter to entries with the '2026' flag) and
    the optional "2028 Presidential Field" view.
    """
    path = _ref_path('politicians_canonical.txt')
    out: dict[str, set[str]] = {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                parts = s.split('|')
                name = parts[0].strip()
                flags_raw = parts[2].strip() if len(parts) > 2 else ''
                flags = {t.strip() for t in flags_raw.split(',') if t.strip()}
                if name in out:
                    out[name] |= flags
                else:
                    out[name] = flags
    except FileNotFoundError:
        pass
    return out


def _load_candidates_2026() -> set[str]:
    """Names flagged as 2026-cycle candidates in `politicians_canonical.txt`."""
    return {n for n, flags in _load_politician_cycle_flags().items() if '2026' in flags}


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
    """Deterministic cache key from a filter dict.

    Version bumps invalidate all previously-cached payloads in one move.
    Bump whenever the payload SCHEMA changes (new card field, renamed
    field, etc.) so stale payloads written before the schema change
    don't keep serving for up to CACHE_TTL_S.

    History:
      v1 — initial release
      v2 — 2026-06-05: added issue_geo, trending_local, trending_meta,
            top_candidates, candidate race_type/role fields, engaged-
            politician role/engagement_drivers fields, national_share
            on search/social rows. Stale v1 caches were serving an
            empty Issue × Geo heatmap because issue_geo wasn't in the
            payload at write time.
      v3 — 2026-06-05: Google retired /trends/api/dailytrends, so all
            v2 payloads have raw_trends_count=0 / trending_local=[]
            (including National). Switched to the RSS endpoint
            (geo=US for National, geo=US-XX for states) which is now
            actually returning data. Bump invalidates the v2 empty
            payloads so users see real US-wide trending political
            searches when no state filter is set.
      v4 — 2026-06-05: issue_buckets now carries trend_score,
            trend_queries, news_count, news_headlines, blended_score,
            external_only fields (Google Trends + GDELT mixed into
            each bucket). The card UI shows trending chips + Google
            Trends sample terms alongside panel samples. v3 payloads
            don't have these fields and would render with the chips
            missing, so bump invalidates them.
      v5 — 2026-06-05: tightened _filter_trends_to_political to use
            word-bounded regex (was substring), so 'irs' no longer
            matches inside 'first' (which let 'Lioness season 3'
            through via its 'first look' related text). Also dropped
            keyword-in-related path — only politician-name-in-related
            qualifies now. Old v4 cached payloads were carrying
            non-political bleed in trending_local.
    """
    canonical = json.dumps({
        'party':     filters.get('party') or 'All',
        'geo_type':  filters.get('geo_type') or 'National',
        'geo_value': filters.get('geo_value') or '',
        'lookback':  int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS),
        'version':   5,
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
    # Allow 1 ("Live (latest day)") through 180. 7/30/90 are the standard UI options.
    lookback_days = max(1, min(180, lookback_days))
    return {
        'party':         party,
        'geo_type':      geo_type,
        'geo_value':     geo_value,
        'lookback_days': lookback_days,
    }


# ── Filter options (states/DMAs/parties) ─────────────────────────────────────

# Country filter for filter-option fallback queries. Matches the aggregator's
# US_COUNTRY_FILTER (blue_iq_aggregator.py:112) so the dropdown universe
# matches the cube universe exactly. Without this, the fallback DMA query
# pulled every distinct DMA string in user_data_sanitized regardless of the
# panelist's country — leaking Canadian / international DMA values.
_US_COUNTRY_FILTER = "COUNTRY IN ('USA','United States','US','U.S.','U.S.A.')"

# Strings that look like a DMA but aren't real Nielsen markets. The cube
# theoretically only emits US-country DMA values, but garbage rows
# (mistagged country, null-passthrough placeholders, ingestion glitches)
# still surface. Reject any DMA value matching this set, or anything that
# looks like a country / continent / "Unknown".
_DMA_REJECTS_EXACT = {
    '', '(null)', 'NULL', 'null', 'None', '(none)', 'unknown', 'Unknown',
    'UNKNOWN', 'N/A', 'na', 'NA', '0', '-', '--', 'Other', 'OTHER',
    'NotApplicable', 'Not Applicable', 'DMA', 'foreign', 'Foreign',
    'International', 'INTL', 'Various',
}
_DMA_REJECTS_SUBSTR = {
    # Country / continent names that occasionally end up in the DMA column
    'canada', 'mexico', 'united kingdom', 'australia', 'germany', 'france',
    'india', 'japan', 'china', 'brazil', 'south africa', 'europe', 'asia',
    'africa', 'oceania', 'south america', 'central america',
}


def _is_valid_us_dma(name: str) -> bool:
    """Return True if `name` plausibly names a US Nielsen DMA.

    Used to filter the dropdown universe so non-US / garbage DMA values
    don't surface. Conservative: rejects an explicit set of placeholder
    strings, anything containing a country / continent substring, and
    pathological strings (too short, too long, all-digit, all-punct).
    Real US DMAs are 8-50 chars, contain at least one letter, and don't
    match any reject token.
    """
    if not name:
        return False
    s = str(name).strip()
    if not s:
        return False
    if s in _DMA_REJECTS_EXACT:
        return False
    sl = s.lower()
    if any(tok in sl for tok in _DMA_REJECTS_SUBSTR):
        return False
    # Length sanity. Real DMAs span "Macon" (5) to long hyphenated ones
    # ("San Francisco-Oak-San Jose", 28). Allow 3-60 to be generous.
    if not (3 <= len(s) <= 60):
        return False
    # Must contain at least one alpha char (rejects all-digit codes).
    if not any(c.isalpha() for c in s):
        return False
    return True


def get_filter_options() -> dict:
    """Returns the dropdown choices for the filter bar.

    Fast path: read state/DMA list straight from the nightly cube
    (`all_states` and `all_dmas` keys). Sub-millisecond, no CH hit.

    Fallback: if cube is missing, run two small GROUP BY queries on
    `userdata.user_data_sanitized` (still fast — that table is small).
    BOTH paths filter DMAs through _is_valid_us_dma so non-US garbage
    is silently dropped, and the fallback DMA query is gated on
    _US_COUNTRY_FILTER for the same reason.
    """
    cache_key = '_filter_options_v3'  # bumped: US-only DMA filter
    cached = _FILTER_OPTIONS_CACHE.get(cache_key)
    if cached and (time.time() - cached['ts'] < 3600):
        return cached['data']

    states: list[str] = []
    dmas:   list[str] = []

    # Filter dropdown reads from the 30d cube (it has the broadest geo
    # coverage). The Live cube may have fewer states/DMAs if some panel
    # cells fell below MIN_CELL_SIZE on a single-day window.
    cube = _load_cube(DEFAULT_LOOKBACK_DAYS)
    if cube:
        states = list(cube.get('all_states') or [])
        dmas   = [d for d in (cube.get('all_dmas') or []) if _is_valid_us_dma(d)]

    if not states:
        try:
            # PROVINCE is the 2-letter USPS code column in user_data_sanitized.
            # Translate to full state name via the canonical _USPS_TO_NAME map
            # so the dropdown shows "California" instead of "CA".
            try:
                from external_signals import _USPS_TO_NAME  # type: ignore
            except Exception:
                _USPS_TO_NAME = {}
            rows = _ch_query(f"""
                SELECT PROVINCE, count() AS n
                FROM userdata.user_data_sanitized
                WHERE PROVINCE IS NOT NULL AND PROVINCE != ''
                  AND {_US_COUNTRY_FILTER}
                GROUP BY PROVINCE
                HAVING n >= %(floor)s
                ORDER BY PROVINCE
            """, {'floor': MIN_CELL_SIZE})
            states = sorted({_USPS_TO_NAME.get(r[0], r[0])
                              for r in rows if r and r[0]})
        except Exception as e:
            logger.warning("filter_options: state pull failed: %s", e)
    if not dmas:
        try:
            rows = _ch_query(f"""
                SELECT DMA, count() AS n
                FROM userdata.user_data_sanitized
                WHERE DMA IS NOT NULL AND DMA != ''
                  AND {_US_COUNTRY_FILTER}
                GROUP BY DMA
                HAVING n >= %(floor)s
                ORDER BY DMA
            """, {'floor': MIN_CELL_SIZE})
            dmas = [r[0] for r in rows if r and r[0] and _is_valid_us_dma(r[0])]
        except Exception as e:
            logger.warning("filter_options: dma pull failed: %s", e)

    data = {
        'parties':       VALID_PARTIES,
        'geo_types':     VALID_GEO_TYPES,
        'states':        states,
        'dmas':          dmas,
        'min_cell_size': MIN_CELL_SIZE,
        'default_lookback_days': DEFAULT_LOOKBACK_DAYS,
        'cube_built_at': (cube or {}).get('computed_at'),
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


def roll_up_political_issues(queries: list[dict], use_external: bool = True,
                              return_assignments: bool = False):
    """Classify search queries into political-issue buckets via OpenAI.

    `queries`: [{'term': str, 'count': int}, ...]

    Returns:
      [{'bucket': str, 'count': int, 'share': float, 'sample_queries': [str, ...]}, ...]
    Sorted by count desc. Non-policy queries are dropped from the output.

    If `return_assignments=True`, returns a tuple
      (buckets_list, term_to_bucket_map)
    where `term_to_bucket_map[norm_term] = bucket_name`. This is what the
    Issue\u00d7Journey cross step uses: it has hundreds of touchpoint-panelist
    search terms, and matching by sample_queries (10 per bucket) misses
    99%+ of them. The full term map lets us bucket every observed term.
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
        return ([], {}) if return_assignments else []

    client = _openai_client()
    if client is None:
        # Without AI we can't reliably bucket; return raw top-K as "Other Policy".
        kept.sort(key=lambda x: -x['count'])
        top = kept[:50]
        total = sum(t['count'] for t in top) or 1
        buckets = [{
            'bucket': 'Other Policy',
            'count':  total,
            'share':  1.0,
            'sample_queries': [t['term'] for t in top[:10]],
            'trend':  0.0,
        }]
        if return_assignments:
            tmap = {t['term'].strip().lower(): 'Other Policy' for t in top}
            return buckets, tmap
        return buckets

    sys_msg = (
        'You classify analytics search queries to support a U.S. political dashboard.\n'
        'AUDIENCE: U.S. registered voters and constituents that a U.S. politician\n'
        '(federal, state, or local) could address with policy. Drop everything else.\n'
        '\n'
        'For each query, decide:\n'
        '  1. Is the query (a) U.S.-relevant AND (b) a POLICY issue an elected U.S.\n'
        '     official could plausibly address?\n'
        '\n'
        f'     Return "{NON_POLICY}" if ANY of the following are true:\n'
        '       - Non-U.S. jurisdiction (UK, India, EU, LATAM, Russia, Canada specifics).\n'
        '         Examples to REJECT: "aadhar card", "uk financial news", "gilt yields",\n'
        '         "dolar hoy", "sanitas", "ration card", "annapurna yojana",\n'
        '         "infonavit", ".gov.in", ".gov.uk", ".co.uk".\n'
        '       - Non-English-script terms (Cyrillic, Devanagari, CJK, Arabic, etc.).\n'
        '       - Generic non-policy: shopping, recipes, weather, sports, celebrity,\n'
        '         dating, gaming, music, movies, TV, technical/coding queries,\n'
        '         job-search board names without policy context ("zillow", "indeed"\n'
        '         alone is non-policy).\n'
        '       - Government services that are pure transactions, not policy debates\n'
        '         ("renew driver license", "irs login", "social security login").\n'
        '\n'
        '     KEEP only U.S. policy debate topics: cost of living, housing affordability,\n'
        '     healthcare access, immigration, taxes, voting/elections, candidate\n'
        '     positions, civil rights, gun policy, abortion policy, climate policy,\n'
        '     student loans, infrastructure, foreign policy positions, etc.\n'
        '\n'
        '  2. If policy, assign exactly ONE bucket from this list:\n'
        f'{_BUCKETS_LIST_FOR_PROMPT}\n'
        f'     If non-policy OR non-U.S., return "{NON_POLICY}".\n'
        '\n'
        'When in doubt, prefer NON_POLICY. False negatives are cheap; false positives\n'
        'pollute the dashboard.\n'
        '\n'
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
    if return_assignments:
        # term_to_bucket map keyed by normalized term (lowercase, stripped).
        # Excludes NON_POLICY assignments — those terms have no policy bucket.
        tmap: dict[str, str] = {}
        for i, row in enumerate(kept):
            b = assignments.get(i) or NON_POLICY
            if b == NON_POLICY:
                continue
            tmap[row['term'].strip().lower()] = b
        return out, tmap
    return out


# ── Card queries (the 5 panel queries) ──────────────────────────────────────

def _geo_filter_clause(geo_type: str, geo_value: str) -> tuple[str, dict]:
    """Returns (SQL fragment that filters user_data_sanitized U, params).

    Note: user_data_sanitized's state column is `PROVINCE` (USPS 2-letter
    code), not `STATE`. Frontend passes full state names ("California"),
    so we map the incoming value back to its USPS code on the fly.
    """
    if geo_type == 'State' and geo_value:
        try:
            from external_signals import _USPS_TO_NAME  # type: ignore
            name_to_usps = {v: k for k, v in _USPS_TO_NAME.items()}
        except Exception:
            name_to_usps = {}
        usps = name_to_usps.get(geo_value, geo_value)
        return ("U.PROVINCE = %(geo_value)s", {'geo_value': usps})
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
    """Blend panel mentions + Google Trends + GDELT + Wikipedia into one score.

    Weights are renormalized to the sources that ACTUALLY returned data so a
    single live source (e.g. just Wikipedia when Trends/GDELT are rate-limited)
    still produces a meaningful ranking instead of collapsing to ~0.
    """
    trends = external.get('google_trends_politicians') or {}
    gdelt  = external.get('gdelt_politician_mentions') or {}
    wiki   = external.get('wiki_pageviews') or {}
    parties = _load_politician_parties()

    def norm(d: dict[str, int | float]) -> dict[str, float]:
        # Treat all-zero dicts as empty (Trends often returns 12 zeros).
        if not d or max(d.values() or [0]) <= 0:
            return {}
        mx = max(d.values()) or 1
        return {k: (float(v) / mx) for k, v in d.items()}

    sources = {
        'panel':  (norm(panel_counts), 0.55),
        'trends': (norm(trends),       0.20),
        'gdelt':  (norm(gdelt),        0.15),
        'wiki':   (norm(wiki),         0.10),
    }
    # Renormalize across sources that returned any data.
    live = {name: w for name, (d, w) in sources.items() if d}
    wsum = sum(live.values()) or 1.0
    live_weights = {name: w / wsum for name, w in live.items()}

    names = set(politicians) | set(panel_counts) | set(trends) | set(gdelt) | set(wiki)
    out = []
    for n in names:
        score = sum(
            sources[name][0].get(n, 0.0) * live_weights[name]
            for name in live
        )
        if score <= 0:
            continue
        # Provenance: which sources contributed (so the card can show a
        # tiny badge like "external" when panel is empty).
        contribs = [name for name in live if sources[name][0].get(n, 0.0) > 0]
        out.append({
            'name':           n,
            'party_code':     parties.get(n, 'I'),
            'panelists':      int(panel_counts.get(n, 0)),
            'mention_score':  round(score, 4),
            'sources':        contribs,
        })
    out.sort(key=lambda r: -r['mention_score'])
    return out[:60]


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


# ── Aggregate cube loader + slicer (PRIMARY fast path) ──────────────────────

_CUBE_CACHE: dict[int, dict] = {}        # {lookback_days: {'cube': ..., 'fetched_at': ts}}
_CUBE_INPROC_TTL_S = 300                  # re-fetch each cube from S3 at most once every 5 min


def _cube_cell_key(party: str, geo_type: str, geo_value: str) -> str:
    """Cube file uses '{party}|{state}|{dma}'. Empty for the dim we're not slicing."""
    if geo_type == 'State':
        return f"{party}|{geo_value}|"
    if geo_type == 'DMA':
        return f"{party}||{geo_value}"
    return f"{party}||"


def _load_cube(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Optional[dict]:
    """Load the per-lookback cube from S3 with a short in-process TTL.

    Lookback resolves to a specific S3 key (`cube_{N}d.json`). If that key
    is missing AND the user asked for the default 30d window, we fall
    through to the legacy `latest.json` key for backward compat. For the
    1d ("Live") cube, no fallback — missing means missing.

    Returns None if the cube is missing entirely (frontend then falls
    through to a degraded "external-only" view). Logs a warning if the
    cube is older than CUBE_STALE_S but still returns it so the dashboard
    never goes dark unnecessarily.
    """
    now = time.time()
    cached = _CUBE_CACHE.get(int(lookback_days))
    if cached and (now - float(cached.get('fetched_at', 0)) < _CUBE_INPROC_TTL_S):
        return cached.get('cube')  # may be None if last fetch confirmed missing
    primary_key = _cube_key_for_lookback(lookback_days)
    fallback_key = S3_CUBE_KEY if int(lookback_days) == DEFAULT_LOOKBACK_DAYS else None

    def _try_key(key: str) -> Optional[dict]:
        s3 = _s3()
        resp = s3.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(resp['Body'].read().decode('utf-8'))

    cube: Optional[dict] = None
    for k in [primary_key, fallback_key]:
        if not k:
            continue
        try:
            cube = _try_key(k)
            break
        except Exception as e:
            msg = str(e)
            if 'NoSuchKey' in msg or '404' in msg:
                continue
            logger.warning("Blue IQ cube load failed for %s: %s", k, e)
            continue

    if cube is not None:
        try:
            built = datetime.fromisoformat(cube.get('computed_at', '').replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - built).total_seconds()
            if age > CUBE_STALE_S:
                logger.warning("Blue IQ %dd cube is %.0fh old — aggregator may be failing.",
                               lookback_days, age / 3600)
        except Exception:
            pass
    else:
        logger.warning("Blue IQ %dd cube missing at s3://%s/%s — run blue_iq_aggregator.py --lookback %d",
                       lookback_days, S3_CACHE_BUCKET, primary_key, lookback_days)

    _CUBE_CACHE[int(lookback_days)] = {'cube': cube, 'fetched_at': now}
    return cube


def _slice_cube(cube: dict, filters: dict) -> tuple[Optional[dict], int]:
    """Look up the relevant cell in the cube. Returns (cell_payload_or_None, panel_size)."""
    if not cube:
        return None, 0
    cells = cube.get('cells') or {}
    key = _cube_cell_key(filters['party'], filters['geo_type'], filters['geo_value'])
    cell = cells.get(key)
    if cell:
        return cell, int(cell.get('uid_count', 0))
    # If party-specific cell missing, try the 'All' party variant (still useful info)
    if filters['party'] != 'All':
        alt = cells.get(_cube_cell_key('All', filters['geo_type'], filters['geo_value']))
        if alt:
            return alt, int(alt.get('uid_count', 0))
    return None, 0


def _bucket_search_terms_via_global_map(top_search_queries: list[dict],
                                          issue_buckets_global: list[dict]) -> list[dict]:
    """Map a cell's top search queries to political-issue buckets using the
    GLOBAL bucket assignments from the cube (no fresh OpenAI call needed).
    """
    if not top_search_queries or not issue_buckets_global:
        return []
    # Build a fast lookup from sample_queries -> bucket. Terms not in the
    # samples fall through; they're skipped from per-cell rollup (they're
    # represented in the absolute-national 'issue_buckets_global' card).
    term_to_bucket: dict[str, str] = {}
    for b in issue_buckets_global:
        for q in (b.get('sample_queries') or []):
            term_to_bucket[q.strip().lower()] = b['bucket']

    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    for row in top_search_queries:
        term = (row.get('term') or '').strip().lower()
        c = int(row.get('count') or 0)
        if not term or c <= 0:
            continue
        b = term_to_bucket.get(term)
        if not b:
            continue
        counts[b] += c
        if len(examples[b]) < 8:
            examples[b].append(row.get('term'))

    if not counts:
        # No per-cell mapping found — surface the global buckets instead so
        # the card isn't blank. This is the "small slice" graceful path.
        return [dict(b, sample_queries=(b.get('sample_queries') or [])[:8]) for b in issue_buckets_global[:12]]

    total = sum(counts.values()) or 1
    return [{
        'bucket': b,
        'count':  c,
        'share':  round(c / total, 4),
        'sample_queries': examples[b][:8],
        'trend':  0.0,
    } for b, c in sorted(counts.items(), key=lambda x: -x[1])]


def _compute_issue_geo(cube: Optional[dict], issue_buckets_global: list[dict],
                         *, party_filter: str = 'All') -> list[dict]:
    """For each (state, issue) pair, return panel-search volume.

    Iterates every state-level cell in the cube (cells where state is set
    and dma is empty), buckets the cell's top search queries via the global
    issue-bucket map, and emits one row per (state, issue, panelists) tuple.

    The result powers the Issue × Geo heatmap on the dashboard:
      [
        {"state": "California", "issue": "Healthcare",  "panelists": 184,
         "cell_size": 12480, "share": 0.0147},
        {"state": "California", "issue": "Gas Prices",  "panelists": 91,
         "cell_size": 12480, "share": 0.0073},
        ...
      ]

    party_filter constrains which cells contribute (e.g. 'D' → only the
    Democrat-leaning cells per state). Defaults to 'All' which sums across
    all party imputations.
    """
    if not cube:
        return []
    cells = cube.get('cells') or {}
    out: list[dict] = []
    for cell_key, cell in cells.items():
        try:
            party, state, dma = cell_key.split('|', 2)
        except ValueError:
            continue
        # state-level cells only (no DMA-only, no national)
        if not state or dma:
            continue
        # Party slice: 'All' keeps the All-party cells, anything else
        # restricts to matching party rows. Cube was built with separate
        # per-party cells, so we just pick the right key.
        if party != party_filter:
            continue
        panel_top_queries = cell.get('top_search_queries') or []
        if not panel_top_queries:
            continue
        cell_size = int(cell.get('uid_count') or 0)
        buckets = _bucket_search_terms_via_global_map(
            panel_top_queries, issue_buckets_global)
        for b in buckets:
            panel = int(b.get('count') or 0)
            if panel <= 0:
                continue
            out.append({
                'state':     state,
                'issue':     b['bucket'],
                'panelists': panel,
                'cell_size': cell_size,
                'share':     round(panel / cell_size, 4) if cell_size else 0.0,
            })
    return out


# DMA → primary state lookup. Google Trends only exposes state-level
# regional data via geo=US-XX, so a DMA filter (e.g. "Los Angeles") needs
# to fall through to the parent state ("California") to fetch local
# trending terms. Covers the top ~50 US DMAs which account for >80% of
# US TV households. DMAs not in this map fall back to US-wide Trends.
DMA_TO_STATE = {
    'New York': 'New York',
    'Los Angeles': 'California',
    'Chicago': 'Illinois',
    'Philadelphia': 'Pennsylvania',
    'Dallas-Ft. Worth': 'Texas',
    'San Francisco-Oak-San Jose': 'California',
    'Atlanta': 'Georgia',
    'Houston': 'Texas',
    'Washington DC (Hagrstwn)': 'District of Columbia',
    'Boston (Manchester)': 'Massachusetts',
    'Phoenix (Prescott)': 'Arizona',
    'Tampa-St. Pete (Sarasota)': 'Florida',
    'Seattle-Tacoma': 'Washington',
    'Detroit': 'Michigan',
    'Minneapolis-St. Paul': 'Minnesota',
    'Miami-Ft. Lauderdale': 'Florida',
    'Denver': 'Colorado',
    'Orlando-Daytona Bch-Melbrn': 'Florida',
    'Cleveland-Akron (Canton)': 'Ohio',
    'Sacramnto-Stkton-Modesto': 'California',
    'St. Louis': 'Missouri',
    'Portland, OR': 'Oregon',
    'Pittsburgh': 'Pennsylvania',
    'Raleigh-Durham (Fayetvlle)': 'North Carolina',
    'Charlotte': 'North Carolina',
    'Indianapolis': 'Indiana',
    'Baltimore': 'Maryland',
    'San Diego': 'California',
    'Nashville': 'Tennessee',
    'Hartford & New Haven': 'Connecticut',
    'Kansas City': 'Missouri',
    'Salt Lake City': 'Utah',
    'Columbus, OH': 'Ohio',
    'Milwaukee': 'Wisconsin',
    'Cincinnati': 'Ohio',
    'Greenville-Spart-Ashevll-And': 'South Carolina',
    'San Antonio': 'Texas',
    'West Palm Beach-Ft. Pierce': 'Florida',
    'Las Vegas': 'Nevada',
    'Austin': 'Texas',
    'Birmingham (Ann and Tusc)': 'Alabama',
    'Norfolk-Portsmth-Newpt Nws': 'Virginia',
    'Jacksonville': 'Florida',
    'New Orleans': 'Louisiana',
    'Memphis': 'Tennessee',
    'Greensboro-H.Point-W.Salem': 'North Carolina',
    'Oklahoma City': 'Oklahoma',
    'Buffalo': 'New York',
    'Albuquerque-Santa Fe': 'New Mexico',
    'Louisville': 'Kentucky',
    'Providence-New Bedford': 'Rhode Island',
    'Richmond-Petersburg': 'Virginia',
    'Wilkes Barre-Scranton-Hztn': 'Pennsylvania',
    'Fresno-Visalia': 'California',
    'Tulsa': 'Oklahoma',
    'Mobile-Pensacola (Ft Walt)': 'Alabama',
    'Tucson (Sierra Vista)': 'Arizona',
    'Knoxville': 'Tennessee',
}


def _filter_trends_to_political(trends_top: list[dict],
                                  politicians: set[str]) -> list[dict]:
    """Keep only Trends terms that look political.

    Uses a cheap keyword + politician-name heuristic with WORD-BOUNDARY
    matching (re.IGNORECASE + \\b) so short keywords like 'irs' don't
    substring-match inside non-political words like 'f-IRS-t' (which
    used to let 'Lioness season 3' through because its related text
    contained 'first look').

    Politician-name matches use lowercased substring with whitespace
    flanking — politicians are stored as full multi-word names so the
    risk of false positives is minimal, but we still require either
    bounded-edge or full-name presence.

    The related-text fallback ONLY accepts politician-name matches, NOT
    keyword matches — keyword-in-related is too weak a signal and was
    the primary source of non-political bleed into this card.
    """
    if not trends_top:
        return []
    # Word-bounded keywords. Patterns are compiled once with IGNORECASE.
    POLITICAL_KEYWORDS = [
        # offices / institutions
        'president', 'senator', 'senate', 'congress', 'house of',
        'governor', 'mayor', 'attorney general', 'secretary of',
        'supreme court', 'scotus', 'court ruling',
        'white house', 'capitol', 'pentagon', 'state department',
        'cabinet', 'congresswoman', 'congressman',
        # process / mechanics
        'election', 'campaign', 'primary election', 'caucus', 'debate stage',
        'voter', 'voters', 'voting', 'voted', 'ballot', 'ballots',
        'turnout', 'redistricting',
        'impeachment', 'impeach', 'impeached', 'indictment', 'indicted',
        'subpoena', 'testimony',
        # policy
        'healthcare', 'health care', 'obamacare', 'medicare', 'medicaid',
        'minimum wage', 'inflation', 'gas prices', 'tariff', 'tariffs',
        'immigration', 'immigrant', 'immigrants', 'border patrol',
        'asylum', 'deportation', 'deported', 'deport',
        'abortion', 'roe v wade', 'dobbs', 'reproductive', 'planned parenthood',
        'gun control', 'second amendment', 'mass shooting', 'assault weapon',
        'climate change', 'global warming', 'fracking',
        'student loan', 'student loans', 'pell grant',
        'social security', 'federal reserve', 'fed rate',
        'ceasefire', 'gaza', 'ukraine', 'nato', 'foreign aid',
        'tax cut', 'tax cuts', 'tax bill', 'tax reform',
        # outcomes / processes (bounded variants only — no bare 'won'/'wins')
        'concedes', 'concession', 'recount', 'recall election',
        # parties (bounded only — no bare 'gop' since it false-matched)
        'democrat', 'democrats', 'republican', 'republicans',
        'rnc ', 'dnc ', 'libertarian party', 'green party',
        # newsworthy
        'political rally', 'campaign rally', 'protest', 'protests',
        'sanctions on', 'executive order', 'presidential veto',
        # bounded short tokens that previously substring-bled:
        # 'gop' was matching 'logo'-like fragments; require word boundaries
    ]
    SHORT_BOUNDED_KEYWORDS = [
        # These are short / English-common-fragment risks. Word-bounded only.
        'irs', 'gop', 'roe', 'snap', 'epa', 'vote', 'vote',
    ]
    import re
    kw_pattern = re.compile(
        '(' + '|'.join(re.escape(k) for k in POLITICAL_KEYWORDS + SHORT_BOUNDED_KEYWORDS) + ')',
        re.IGNORECASE,
    )
    # For short bounded keywords we need true \b on both sides. The pattern
    # below combines all keywords with \b boundaries so 'irs' won't match
    # 'first', 'vote' won't match 'devoted', etc.
    bounded_pattern = re.compile(
        r'\b(' + '|'.join(re.escape(k) for k in POLITICAL_KEYWORDS + SHORT_BOUNDED_KEYWORDS) + r')\b',
        re.IGNORECASE,
    )

    out = []
    pol_lower = {p.lower() for p in politicians if p}
    # Build a word-bounded politician regex. We need to match BOTH the full
    # name ('Donald Trump' in 'Donald Trump rally') AND the last name alone
    # ('Trump' in 'trump freedom 250 rally performers') so Trends headlines
    # that use just the surname still classify.
    #
    # Last-name alternates are gated: we only add the last name when it's
    # >= 5 chars AND not a common English word. Otherwise we'd false-match
    # ('Will' from 'Will Hurd' would match 'Will the senator vote', etc.)
    COMMON_WORDS = {
        'will', 'gray', 'long', 'rich', 'young', 'green', 'brown', 'wells',
        'cole', 'crow', 'porter', 'hill', 'love', 'kim', 'price', 'foster',
        'cooper', 'walker', 'turner', 'roy', 'gold', 'good', 'black', 'house',
        'bass', 'lee', 'reed', 'rice', 'rose', 'ross', 'webb', 'wood', 'king',
        'fields', 'kelly', 'mills', 'rivers', 'banks', 'grove', 'lake',
        'castro', 'banks', 'flores',
    }
    if pol_lower:
        alternates: set[str] = set()
        for p in pol_lower:
            alternates.add(p)  # full name
            parts = p.split()
            if len(parts) >= 2:
                last = parts[-1]
                # Strip trailing punctuation like commas / periods
                last = re.sub(r'[^a-z]', '', last)
                if len(last) >= 5 and last not in COMMON_WORDS:
                    alternates.add(last)
        # Sort longest-first so 'donald trump' beats 'trump' in the alternation.
        pol_sorted = sorted(alternates, key=lambda x: -len(x))
        pol_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(p) for p in pol_sorted) + r')\b',
            re.IGNORECASE,
        )
    else:
        pol_pattern = None

    for row in trends_top:
        term = (row.get('term') or '').strip()
        if not term:
            continue
        # 1. Politician name in TERM (strongest signal).
        if pol_pattern and pol_pattern.search(term):
            out.append({**row, 'why_political': 'politician_name'})
            continue
        # 2. Bounded keyword match in TERM.
        m = bounded_pattern.search(term)
        if m:
            out.append({**row, 'why_political': 'keyword:' + m.group(1).lower()})
            continue
        # 3. Politician name in RELATED (medium signal). Keyword-in-related
        #    is intentionally NOT a path — too many false positives (the
        #    'irs in first' bug). If the related text only has a generic
        #    political keyword and no politician, it's probably tangential.
        rel = ' '.join((row.get('related') or []))
        if rel and pol_pattern and pol_pattern.search(rel):
            out.append({**row, 'why_political': 'related_query'})
            continue
    return out


# ── Per-bucket keyword classifier for EXTERNAL terms (Trends + GDELT) ───────
#
# The panel-side bucketing already goes through OpenAI in roll_up_political_issues
# and that result is baked into the cube as `issue_buckets_global` (with a
# `sample_queries` exemplar list per bucket). We can't reuse that exact-match
# lookup for external terms because Google Trends headlines ("arizona prosecution
# of fake electors") and GDELT article titles will essentially never match a
# panel sample_query exactly.
#
# So: bucket external terms via case-insensitive keyword/substring match against
# this hand-tuned per-bucket vocabulary. First bucket that matches wins. Terms
# that match nothing are skipped (they don't get dumped into "Other Policy" —
# that creates noise). Vocabulary is intentionally narrow on bucket-defining
# terms so we don't cross-classify (e.g. "border" is Immigration, not Foreign
# Policy, even though "border crossing" sounds like both).

BUCKET_KEYWORDS: dict[str, list[str]] = {
    'Economy & Inflation': [
        'inflation', 'recession', 'cost of living', 'consumer price', 'cpi report',
        'gdp', 'fed rate', 'federal reserve', 'rate hike', 'rate cut',
        'stock market', 'wall street', 's&p 500', 'nasdaq', 'dow jones',
        'jobs report', 'unemployment rate',
    ],
    'Gas & Energy': [
        'gas prices', 'gas price', 'gasoline', 'oil prices', 'crude oil',
        'opec', 'pipeline', 'energy bill', 'electricity rates', 'utility bill',
        'fracking',
    ],
    'Housing & Rent': [
        'housing', 'mortgage', 'home prices', 'eviction',
        'section 8', 'affordable housing', 'homeownership',
        'real estate market', 'rent control', 'rental market',
    ],
    'Healthcare': [
        'healthcare', 'health care', 'health insurance', 'obamacare',
        'affordable care act', 'prescription drug', 'drug prices',
        'medical bill', 'insulin price', 'hospital bill',
    ],
    'Immigration': [
        'immigration', 'immigrant', 'border patrol', 'border crossing',
        'border wall', 'asylum', 'deport', 'migrant',
        'ice raid', 'dreamers', 'daca', 'visa policy', 'green card',
        'sanctuary city',
    ],
    'Abortion & Reproductive Rights': [
        'abortion', 'roe v wade', 'dobbs', 'reproductive rights',
        'planned parenthood', 'contraception', 'pro-life',
        'pro-choice', 'abortion ban',
    ],
    'Education & Student Loans': [
        'student loan', 'pell grant', 'tuition',
        'public school funding', 'school board', 'fafsa', 'student debt',
        'college costs', 'school choice', 'voucher program',
    ],
    'Crime & Safety': [
        'crime rate', 'violent crime', 'police shooting', 'homicide', 'carjacking',
        'fentanyl', 'drug bust', 'criminal justice', 'parole', 'sentencing',
        'shoplifting',
    ],
    'Jobs & Wages': [
        'minimum wage', 'union strike', 'labor strike', 'auto workers',
        'paid leave', 'overtime pay', 'gig worker',
    ],
    'Climate': [
        'climate change', 'global warming', 'carbon emissions',
        'wildfire', 'drought', 'green new deal',
        'paris accord', 'electric vehicle', 'solar tax',
    ],
    'Taxes': [
        'tax cut', 'tax bill', 'tax reform',
        'tariff', 'property tax', 'sales tax', 'tax refund',
        'tax credit',
    ],
    'Social Security & Medicare': [
        'social security', 'medicare', 'medicaid', 'retirement age',
        'cola adjustment', 'pension cut',
    ],
    'Foreign Policy': [
        'gaza', 'israel', 'palestin', 'ukraine', 'nato',
        'taiwan', 'iran ', 'foreign aid', 'ceasefire',
        'hamas', 'sanctions on',
    ],
    'Election Integrity & Voting': [
        'voter', 'voting', 'ballot', 'mail-in ballot',
        'redistricting', 'gerrymander', 'fake elector', 'election fraud',
        'recount', 'polling place', 'senate vote', 'house vote',
        'voter integrity', 'consecutive senate', 'senate record',
    ],
    'Guns': [
        'second amendment', 'mass shooting', 'assault weapon',
        'concealed carry', 'gun control', 'red flag law', 'background check',
        'gun violence', 'gun reform',
    ],
}


def _bucket_external_term_to_issue(term: str, related: Optional[list[str]] = None) -> Optional[str]:
    """Return the first ISSUE_BUCKETS label that matches `term`, or None.

    Case-insensitive substring match against the per-bucket vocabulary.
    Iterates buckets in dict order; first match wins. Falls through to
    related-text (Trends RSS news titles / Trends related queries) when
    the bare term doesn't match — captures cases like the Trends term
    "arizona prosecution of fake electors" whose related news is about
    voting and election integrity.
    """
    if not term:
        return None
    haystack = term.lower()
    for bucket, kws in BUCKET_KEYWORDS.items():
        for kw in kws:
            if kw in haystack:
                return bucket
    if related:
        rel_h = ' '.join(r.lower() for r in related if r)
        for bucket, kws in BUCKET_KEYWORDS.items():
            for kw in kws:
                if kw in rel_h:
                    return bucket
    return None


def _augment_buckets_with_external(buckets: list[dict],
                                    trends_political: list[dict],
                                    gdelt_articles: list[dict]) -> list[dict]:
    """Add external signal (Google Trends + GDELT) into existing issue buckets.

    For each bucket already in `buckets` we attach:
      - trend_score:      sum of Google Trends `score` for terms bucketing here
      - trend_queries:    top Trends terms that mapped to this bucket
      - news_count:       number of GDELT political articles bucketing here
      - news_headlines:   top GDELT headlines that mapped to this bucket

    If an issue bucket has zero panel data BUT has external signal, we add
    a synthesized row with count=0 / share=0 so the user still sees it as
    a "trending issue with no panel chatter yet". This is the magic of
    mixing — the card stops being purely retrospective.
    """
    # Pre-bucket the external data once.
    trend_by_bucket: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in (trends_political or []):
        term = (row.get('term') or '').strip()
        if not term:
            continue
        b = _bucket_external_term_to_issue(term, row.get('related'))
        if not b:
            continue
        trend_by_bucket[b].append((term, int(row.get('score', 0) or 0)))

    news_by_bucket: dict[str, list[str]] = defaultdict(list)
    for art in (gdelt_articles or []):
        title = (art.get('title') or '').strip()
        if not title:
            continue
        b = _bucket_external_term_to_issue(title)
        if not b:
            continue
        news_by_bucket[b].append(title)

    # Index existing panel buckets by name for in-place augmentation.
    by_name = {b['bucket']: b for b in buckets}

    # Augment panel-present buckets with external signal.
    for name, row in by_name.items():
        trend_hits = sorted(trend_by_bucket.get(name, []), key=lambda x: -x[1])
        news_hits  = news_by_bucket.get(name, [])
        row['trend_score']    = sum(s for _, s in trend_hits)
        row['trend_queries']  = [t for t, _ in trend_hits[:5]]
        row['news_count']     = len(news_hits)
        row['news_headlines'] = news_hits[:3]

    # Add buckets that have external-only signal (no panel chatter).
    # These get count=0 / share=0 so they sort to the bottom by panel
    # signal, but a high trend_score will float them up after re-rank.
    for name in set(list(trend_by_bucket.keys()) + list(news_by_bucket.keys())):
        if name in by_name:
            continue
        if name not in ISSUE_BUCKETS:
            continue
        trend_hits = sorted(trend_by_bucket.get(name, []), key=lambda x: -x[1])
        news_hits  = news_by_bucket.get(name, [])
        buckets.append({
            'bucket': name,
            'count':  0,
            'share':  0.0,
            'sample_queries': [],
            'trend':  0.0,
            'trend_score':    sum(s for _, s in trend_hits),
            'trend_queries':  [t for t, _ in trend_hits[:5]],
            'news_count':     len(news_hits),
            'news_headlines': news_hits[:3],
            'external_only':  True,
        })

    return buckets


def _rerank_buckets_blended(buckets: list[dict]) -> list[dict]:
    """Re-rank buckets by blended panel + external score.

    Panel signal is the BACKBONE (what people in this slice are actually
    searching). External signal is the OVERLAY (what's hot nationally /
    in news right now). We weight panel 70% and external 30% so a
    breaking-news topic with light panel chatter can rise above a
    steady-state bucket, but the panel still dominates the order for
    slices with strong first-party signal.
    """
    if not buckets:
        return buckets
    max_panel = max((b.get('count') or 0)        for b in buckets) or 1
    max_trend = max((b.get('trend_score') or 0)  for b in buckets) or 1
    max_news  = max((b.get('news_count')  or 0)  for b in buckets) or 1
    for b in buckets:
        panel_n = (b.get('count') or 0)       / max_panel
        trend_n = (b.get('trend_score') or 0) / max_trend
        news_n  = (b.get('news_count')  or 0) / max_news
        # 70% panel, 20% Trends, 10% news. Keep panel dominant.
        b['blended_score'] = round(0.70 * panel_n + 0.20 * trend_n + 0.10 * news_n, 4)
    buckets.sort(key=lambda b: -b.get('blended_score', 0))
    return buckets


# ── Main entry point ────────────────────────────────────────────────────────

def compute_panel_view(filters: dict, *, force_refresh: bool = False) -> dict:
    """Build a Blue IQ dashboard view for the filter combo.

    Order of operations:
      1. Try the per-filter S3 result cache (24h TTL).
      2. Load the nightly aggregate CUBE from S3 (sub-second S3 GetObject).
      3. Slice the cube for this filter cell.
      4. In parallel: fetch external signals (Trends + GDELT + Wikipedia).
      5. Blend cube + external into the card output.
      6. Cache result and return.

    If the cube is missing entirely (first-day boot, before any nightly run),
    the response still goes out with external-only cards and a clear
    `cube_missing=true` flag so the operator knows to run the aggregator.
    """
    f = _normalize_filters(filters)

    # 1. Per-request cache (24h, identical filter combo)
    if not force_refresh:
        cached = _cache_get(f)
        if cached:
            cached['cache_hit'] = True
            return cached

    # 2. Cube lookup (sub-second S3 GetObject, then in-process cache for 5 min).
    # Pick the cube file that matches the user's selected lookback window —
    # so "Live (1 day)" reads cube_1d.json and "30 days" reads cube_30d.json.
    cube = _load_cube(int(f.get('lookback_days') or DEFAULT_LOOKBACK_DAYS))
    cell, panel_size = _slice_cube(cube, f) if cube else (None, 0)
    suppressed = panel_size < MIN_CELL_SIZE
    cube_missing = cube is None

    # 3. External signals — ALWAYS fetched (parallel ThreadPoolExecutor inside).
    try:
        from external_signals import fetch_all_external  # type: ignore
    except ImportError:
        from .external_signals import fetch_all_external  # type: ignore

    # Fetch external signal data for the top-20 politicians by file order
    # PLUS every 2026 candidate (so the Candidates card always has Trends data
    # for the people the user actually wants to rank, not just the marquee
    # 20). Dedupe + keep order stable so the Trends batch hashes the same.
    _pol_all = _load_politicians()
    _cands = _load_candidates_2026()
    _seen: set[str] = set()
    politicians_for_external: list[str] = []
    for name in (_pol_all[:20] + sorted(_cands)):
        if name in _seen:
            continue
        _seen.add(name)
        politicians_for_external.append(name)
    # Resolve which state to pass to Google Trends. Trends only exposes
    # state-level regional data, so a DMA filter (e.g. "Los Angeles")
    # gets resolved to its primary state ("California") so the user sees
    # state-local trending terms instead of US-wide.
    trends_state: Optional[str] = None
    if f['geo_type'] == 'State':
        trends_state = f['geo_value']
    elif f['geo_type'] == 'DMA' and f['geo_value']:
        trends_state = DMA_TO_STATE.get(f['geo_value'])
    external = fetch_all_external(
        state=trends_state,
        lookback_days=f['lookback_days'],
        politician_names=politicians_for_external,
    )

    # 4. Build cards from cube (panel-side) + external (Trends/GDELT/Wiki).
    issue_buckets_global = (cube or {}).get('issue_buckets_global') or []

    if cell:
        panel_top_queries  = cell.get('top_search_queries') or []
        panel_search       = cell.get('search_engines') or []
        panel_social       = cell.get('social_media') or []
        panel_politicians  = cell.get('top_politicians') or []
        panel_articles     = cell.get('top_articles') or []
        panel_turnout      = cell.get('turnout') or {'panelists': 0, 'sample_urls': []}
        panel_demo         = cell.get('demo') or {}
        panel_journey      = cell.get('voter_journey') or []
    else:
        panel_top_queries = []
        panel_search = []
        panel_social = []
        panel_politicians = []
        panel_articles = []
        panel_turnout = {'panelists': 0, 'sample_urls': []}
        panel_demo = {}
        panel_journey = []

    # Card A — issue buckets: prefer per-cell panel mapping; fall back to global.
    issue_buckets = _bucket_search_terms_via_global_map(panel_top_queries, issue_buckets_global)
    if not issue_buckets and external.get('google_trends_top'):
        # External-only fallback: bucket Trends terms via the global map too
        synth = [{'term': r.get('term', ''), 'count': max(1, int(r.get('score', 0)) // 1000)}
                 for r in external['google_trends_top']]
        issue_buckets = _bucket_search_terms_via_global_map(synth, issue_buckets_global)

    # Card A — mix in EXTERNAL search-trend signal (Google Trends + GDELT news).
    # Panel data tells us what THIS audience is searching; external tells us
    # what's hot nationally + in news right now. Augment every bucket with
    # both signals, surface new buckets that exist external-only, then
    # re-rank by a blended score so a breaking news topic with light panel
    # chatter floats up. We do this BEFORE the politician/articles section
    # so re-ranked buckets propagate to journey + heatmap + playbook.
    raw_trends_for_buckets = (external or {}).get('google_trends_top') or []
    # Re-use the cheap political filter so non-political Trends headlines
    # (sports, celebrity) don't pollute the buckets.
    politicians_for_filter = set(_load_politicians()[:300])
    trends_political_for_buckets = _filter_trends_to_political(
        raw_trends_for_buckets, politicians_for_filter)
    gdelt_for_buckets = (external or {}).get('gdelt_articles') or []
    issue_buckets = _augment_buckets_with_external(
        issue_buckets, trends_political_for_buckets, gdelt_for_buckets)
    issue_buckets = _rerank_buckets_blended(issue_buckets)

    # Card D — politicians: blend panel + external (Trends + GDELT + Wiki)
    panel_pol_counts = {r.get('name'): int(r.get('panelists', 0))
                        for r in panel_politicians if r.get('name')}
    # Cards D + D2 — agent web-search discovery, per geography:
    #
    #   D  "Top politicians engaged"      → discover_engaged_politicians
    #         Current officeholders + national figures the area is
    #         ACTIVELY ENGAGING WITH right now (Trump, the state's
    #         Senators, governor, principal-city mayor, etc.)
    #
    #   D2 "Top candidates (2026 cycle)"  → discover_candidates
    #         DECLARED / ACTIVE candidates for upcoming 2026 races + 2028
    #         presidential prospects.
    #
    # Both share the same scaffolding (24h S3 cache, threading lock,
    # truncation-tolerant parser); separate agent prompts so each card
    # surfaces the right kind of names. Agent failures fall back open —
    # we use the existing panel + Trends/GDELT/Wiki blend universe.
    try:
        from candidate_discovery import discover_candidates, discover_engaged_politicians
        agent_cands   = discover_candidates(f['geo_type'], f['geo_value']) or []
        agent_engaged = discover_engaged_politicians(f['geo_type'], f['geo_value']) or []
    except Exception as _e:  # pragma: no cover - defensive
        log.warning("candidate/engaged agents unavailable; using fallback: %s", _e)
        agent_cands = []
        agent_engaged = []

    agent_cand_names    = [c['name'] for c in agent_cands]
    agent_engaged_names = [p['name'] for p in agent_engaged]
    static_2026 = sorted(_load_candidates_2026())
    # Politician blend universe: agent-discovered ENGAGED names first
    # (those are who the area is actually talking about), then top-60
    # panel/external politicians, then agent-discovered CANDIDATES, then
    # the static 2026-flagged candidates as defense-in-depth.
    _pol_blend = list(dict.fromkeys(
        agent_engaged_names + _load_politicians()[:60] + agent_cand_names + static_2026
    ))
    top_politicians = _blend_politicians(panel_pol_counts, external, _pol_blend)

    # Re-rank the politicians card by the engaged-agent universe when
    # available — the agent has already verified these names are driving
    # current discourse in this geography, so they should sit on top. We
    # still keep the blended mention_score (panel + Trends + GDELT + Wiki)
    # because that's the SIGNAL OF INTEREST INTENSITY, but we let the
    # agent's engagement_score break ties and pull in names the blend
    # would have missed (e.g. a mayor not in the panel-search index).
    if agent_engaged:
        _eng_by_name = {p['name'].lower(): p for p in agent_engaged}
        _pol_by_name = {r['name'].lower(): r for r in top_politicians}
        merged: list[dict] = []
        # First pass: every engaged-agent name gets a row, with the
        # blended mention_score if any internal signal hit, else the
        # agent's engagement_score scaled to 0..1.
        for p in agent_engaged:
            base = _pol_by_name.get(p['name'].lower(), {})
            blended_score = float(base.get('mention_score') or 0.0)
            agent_norm = float(p.get('engagement_score', 0)) / 100.0
            merged.append({
                'name':              p['name'],
                'party_code':        p['party_code'] if p['party_code'] != '?' else base.get('party_code', 'I'),
                'role':              p.get('role', ''),
                'scope':             p.get('scope', 'national'),
                'state':             p.get('state', ''),
                'engagement_score':  int(p.get('engagement_score') or 0),
                'engagement_drivers': p.get('engagement_drivers') or [],
                # Composite: 60% blended internal interest + 40% agent's
                # engagement estimate (when no internal signal, agent's
                # estimate is the only thing we have).
                'mention_score':     round(0.6 * blended_score + 0.4 * agent_norm if blended_score > 0 else agent_norm, 4),
                'panelists':         int(base.get('panelists', 0)),
            })
        # Second pass: catch any internal-blend politicians the agent
        # didn't return (long tail of panel mentions). De-dupe by lower-
        # cased name. Cap the appended list so we never balloon the card.
        existing = {row['name'].lower() for row in merged}
        for r in top_politicians:
            if r['name'].lower() in existing:
                continue
            if r.get('mention_score', 0) <= 0:
                continue
            merged.append({**r, 'role': '', 'scope': 'national', 'state': '',
                            'engagement_score': 0, 'engagement_drivers': []})
            existing.add(r['name'].lower())
            if len(merged) >= 30:
                break
        merged.sort(key=lambda r: (-(r.get('mention_score') or 0),
                                     -(r.get('engagement_score') or 0)))
        top_politicians = merged[:25]

    # Build the candidates card payload. Prefer agent-discovered rows
    # (they carry race / race_type / state / status). Cross-reference with
    # top_politicians by name to pull in mention_score, party, sources.
    if agent_cands:
        pol_by_name = {r['name'].lower(): r for r in top_politicians}
        top_candidates = []
        for c in agent_cands:
            blended = pol_by_name.get(c['name'].lower(), {})
            top_candidates.append({
                'name':          c['name'],
                'party_code':    c['party_code'] if c['party_code'] != '?' else blended.get('party_code', 'I'),
                'race':          c.get('race', ''),
                'race_type':     c.get('race_type', 'other'),
                'state':         c.get('state', ''),
                'office_held':   c.get('office_held', ''),
                'status':        c.get('status', 'declared'),
                # Score: prefer blended (real interest signal) if non-zero;
                # else use agent's estimated interest score, scaled to 0..1
                # for parity with mention_score.
                'mention_score': (blended.get('mention_score')
                                   if blended.get('mention_score', 0) > 0
                                   else round(float(c.get('agent_score', 0)) / 100.0, 4)),
                'panelists':     int(blended.get('panelists', 0)),
                'sources':       (list(blended.get('sources', []))
                                   + (['agent'] if c['name'] not in {p['name'] for p in top_politicians} else [])),
            })
        # Sort: by mention_score desc, agent_score as tiebreaker.
        top_candidates.sort(key=lambda r: (-r.get('mention_score', 0)))
    else:
        # Static fallback (no agent / no cache / blank result): use the
        # pre-existing 2026-flagged file. No race_type / race info, so
        # the frontend slicer becomes a no-op for these rows.
        cands_2026 = set(static_2026)
        top_candidates = [{**r, 'race_type': 'other', 'race': '', 'state': '', 'status': 'declared'}
                           for r in top_politicians if r.get('name') in cands_2026]

    # Card E — articles: blend panel URLs with GDELT (GDELT supplies titles + images)
    top_articles = _blend_articles_cube(panel_articles, external.get('gdelt_articles') or [])

    # Card G — issue × geo heatmap: per-state issue panel count, sliced from
    # the cube's per-state cells through the global issue bucket map. Computed
    # at request time so the same cube serves every party filter.
    issue_geo = _compute_issue_geo(cube, issue_buckets_global, party_filter=f['party'])

    # Card T — "Trending in this area right now" (live Google Trends, AI-filtered
    # to political). Geographically scoped via trends_state above; falls back to
    # US-wide when the geo is National or the DMA isn't in our lookup. Surfaces
    # things the panel won't catch yet (e.g. a hot local mayoral race).
    trends_state_label = trends_state or 'United States'
    raw_trends = (external or {}).get('google_trends_top') or []
    pol_set = set(_load_politicians())
    trending_local = _filter_trends_to_political(raw_trends, pol_set)[:20]
    trending_meta = {
        'geo_label':         trends_state_label,
        'geo_type':          f['geo_type'],
        'geo_value':         f['geo_value'],
        'raw_trends_count':  len(raw_trends),
        'kept_after_filter': len(trending_local),
        'is_state_local':    trends_state is not None,
        'dma_resolved_via':  (DMA_TO_STATE.get(f['geo_value']) if f['geo_type'] == 'DMA' else None),
    }

    # Turnout
    turnout_pct = 0.0
    if panel_size > 0 and panel_turnout.get('panelists'):
        turnout_pct = round(panel_turnout['panelists'] / panel_size, 4)

    # Issue × Journey cross and Voter Journey: national-only (cube
    # top-level for cross, per-cell for journey). The 30d cube can't
    # carry these two cards because the touchpoint scan blows the CH
    # 80 GiB memory cap on the 30d window — see
    # blue_iq_aggregator.py's "lookback_days <= 14" gate. We fall back
    # to the Live (1d) cube for these specific fields when the current
    # cube doesn't have them, so the user sees a populated card
    # regardless of which lookback they picked.
    issue_journey_cross = (cube or {}).get('issue_journey_cross') or []
    if (not issue_journey_cross or not panel_journey) and int(f.get('lookback_days') or DEFAULT_LOOKBACK_DAYS) > 14:
        # Try cubes in order of "closest in size to what was asked, but
        # still inside the journey-query OOM gate (lookback_days <= 14)".
        # 7d gives the richest cross data (more search terms per touchpoint
        # panelist, more issue buckets after AI rollup) while still fitting
        # in CH's 80 GiB memory cap; 1d is the fallback if 7d isn't built
        # yet.
        for _fb_days in (7, 1):
            try:
                fb_cube = _load_cube(_fb_days)
                if not fb_cube:
                    continue
                if not issue_journey_cross:
                    issue_journey_cross = fb_cube.get('issue_journey_cross') or []
                if not panel_journey:
                    fb_cells = fb_cube.get('cells') or {}
                    fb_key = _cube_cell_key(filters['party'], filters['geo_type'], filters['geo_value'])
                    fb_cell = fb_cells.get(fb_key) or fb_cells.get('All||') or {}
                    panel_journey = fb_cell.get('voter_journey') or []
                if issue_journey_cross and panel_journey:
                    break
            except Exception as _exc:  # pragma: no cover - defensive
                log.debug("Fallback cube %dd for journey cards failed: %s", _fb_days, _exc)

    # Per-row "vs national" baselines for the engagement cards. Pull the
    # All-National cell's search/social rows once, then attach
    # `national_share` to each per-cohort row so the frontend can render an
    # index chip (e.g. "1.4x" when Democrats over-index on YouTube). When
    # the active filter IS All-National the index is ~1.0x and the frontend
    # hides the chip.
    nat_cell = ((cube or {}).get('cells') or {}).get('All||') or {}
    nat_search_rows = _attach_share(nat_cell.get('search_engines') or [])
    nat_social_rows = _attach_share(nat_cell.get('social_media') or [])
    _nat_search_share = {(r.get('name') or '').lower(): float(r.get('share') or 0.0)
                          for r in nat_search_rows}
    _nat_social_share = {(r.get('name') or '').lower(): float(r.get('share') or 0.0)
                          for r in nat_social_rows}
    def _with_baseline(rows: list[dict], baseline: dict[str, float]) -> list[dict]:
        out = []
        for r in rows:
            r2 = dict(r)
            r2['national_share'] = baseline.get((r.get('name') or '').lower(), 0.0)
            out.append(r2)
        return out

    cards = {
        'issue_buckets':       issue_buckets,
        'search_engines':      _with_baseline(_attach_share(panel_search), _nat_search_share),
        'social_media':        _with_baseline(_attach_share(panel_social), _nat_social_share),
        'top_politicians':     top_politicians,
        'top_candidates':      top_candidates,
        'top_articles':        top_articles,
        'turnout_intent':      {
            'pct':            turnout_pct,
            'panelists':      panel_turnout.get('panelists', 0),
            'sample_queries': panel_turnout.get('sample_urls', [])[:8],
        },
        'demo_crosstab':       panel_demo,
        'voter_journey':       panel_journey,
        'issue_journey_cross': issue_journey_cross,
        'issue_geo':           issue_geo,
        'trending_local':      trending_local,
        'trending_meta':       trending_meta,
    }

    # Compare card (only when geo is set)
    compare = {}
    if cube and f['geo_type'] != 'National' and f['geo_value']:
        compare = _build_compare_from_cube(cube, f)

    now = datetime.now(timezone.utc)
    # Pull gen-pop projection metadata from the cube. Frontend uses this to
    # show every panel count BOTH as raw panelists AND projected to the
    # US adult population (e.g. 1.78M panel → ~15.9M US adults).
    gen_pop_factor = float((cube or {}).get('gen_pop_factor') or 1.0)
    us_gen_pop     = int((cube or {}).get('us_gen_pop') or 329_900_000)
    us_panel_total = int((cube or {}).get('us_panel_total') or 0)

    payload = {
        'success':         True,
        'filters':         f,
        'panel_size':      panel_size,
        'panel_projected': int(round(panel_size * gen_pop_factor)),
        'gen_pop_factor':  round(gen_pop_factor, 4),
        'us_gen_pop':      us_gen_pop,
        'us_panel_total':  us_panel_total,
        'suppressed':      suppressed,
        'cube_missing':    cube_missing,
        'cube_built_at':   (cube or {}).get('computed_at'),
        'min_cell_size':   MIN_CELL_SIZE,
        'generated_at':    now.isoformat(),
        'stale_until':     (now + timedelta(seconds=CACHE_TTL_S)).isoformat(),
        'cards':           cards,
        'compare':         compare,
        'cache_hit':       False,
    }
    if cube_missing:
        payload['message'] = (
            'Nightly panel aggregate is missing. Showing external signals only '
            '(Google Trends, GDELT, Wikipedia). Run blue_iq_aggregator.py to '
            'populate the cube.'
        )
    elif suppressed:
        payload['message'] = (
            f'Panel sample for this slice is below minimum cell size ({MIN_CELL_SIZE} panelists). '
            'External signals shown where available.'
        )

    _cache_put(f, payload)
    return payload


def _attach_share(rows: list[dict]) -> list[dict]:
    """Given [{name, panelists}, ...] add a 'share' field summing to 1.0."""
    if not rows:
        return []
    total = sum(int(r.get('panelists', 0)) for r in rows) or 1
    return [{**r, 'share': round(int(r.get('panelists', 0)) / total, 4)} for r in rows]


def _blend_articles_cube(panel_articles: list[dict], gdelt: list[dict]) -> list[dict]:
    """Merge cube's panel-URL list with GDELT's title+image-bearing list."""
    by_url: dict[str, dict] = {}
    for a in gdelt:
        u = a.get('url')
        if not u:
            continue
        by_url[u] = {
            'title':     a.get('title') or _title_from_url(u),
            'source':    a.get('source') or '',
            'url':       u,
            'panelists': 0,
            'tone':      float(a.get('tone') or 0.0),
            'image':     a.get('social_image') or '',
        }
    for p in panel_articles:
        u = p.get('url')
        if not u:
            continue
        if u in by_url:
            by_url[u]['panelists'] = max(by_url[u]['panelists'], int(p.get('panelists', 0)))
        else:
            by_url[u] = {
                'title':     _title_from_url(u),
                'source':    p.get('source') or '',
                'url':       u,
                'panelists': int(p.get('panelists', 0)),
                'tone':      0.0,
                'image':     '',
            }
    ranked = list(by_url.values())
    ranked.sort(key=lambda a: (-int(a['panelists']), -abs(a.get('tone', 0.0))))
    return ranked[:30]


def _build_compare_from_cube(cube: dict, filters: dict) -> dict:
    """Sliced compare card built entirely from cube cells (no fresh CH)."""
    out = {}
    for label, party in [('dems', 'Democrat'), ('reps', 'Republican'),
                          ('indeps', 'Independent'), ('national', 'All')]:
        key = _cube_cell_key(party, filters['geo_type'], filters['geo_value'])
        c = (cube.get('cells') or {}).get(key)
        if not c or int(c.get('uid_count', 0)) < MIN_CELL_SIZE:
            out[label] = {'panel_size': int(c.get('uid_count', 0)) if c else 0, 'suppressed': True}
            continue
        size = int(c.get('uid_count', 0)) or 1
        out[label] = {
            'panel_size':     size,
            'suppressed':     False,
            'search_engines': _attach_share(c.get('search_engines', []))[:6],
            'social_media':   _attach_share(c.get('social_media', []))[:6],
            'turnout_pct':    round((c.get('turnout', {}).get('panelists', 0) or 0) / size, 4),
        }
    return out


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
