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
# Short alias — several callsites in this module (both mine and pre-
# existing) reach for `log.info(...)` / `log.warning(...)`. Rather than
# hunt them all down and risk more latent NameErrors, expose the same
# object under both names. Pre-existing (unfired) callsites: 3210,
# 3321, 3451, 3501, 3554. New ones (2026-07-27): 3028, 3769.
log = logger

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
# 2026-07-27: swapped 'DMA' for 'District' (congressional district, 119th
# Congress). Districts are politically meaningful and cover the whole US
# without foreign-city leakage. Reference: reference/zip_to_congressional_district_119.csv.
VALID_GEO_TYPES = ['National', 'State', 'District']

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


# ── Congressional district reference (2026-07-27) ───────────────────────────
#
# Blue IQ's sub-national geo cut is congressional district (119th Congress),
# replacing DMA. Districts are politically meaningful (they map 1:1 to House
# seats and campaign targeting), have a canonical 435-item universe, and are
# stable across cycles until reapportionment.
#
# CANONICAL SOURCE: `s3://dashboard-inputs/reference/zip_to_congressional_district_119.csv`
# (uploaded 2026-07-27, ~3.3 MB, one row per zip-to-district mapping).
# Also committed to `bg-webapp/reference/` in git so a git-tracked local
# copy is available on every deploy — the loader prefers the local copy
# (zero-latency read) and falls back to S3 only if the local file is
# missing (e.g. an environment where the submodule wasn't cloned).
#
# CSV columns:
#   zip, state, district, district_name, state_district_code,
#   land_area_m2, primary_district, dma_code, dma_name, dma_match_type
#
# We build (and lazily cache) a normalized bundle:
#   - zip_to_district:   '{zip5}' -> '{STATE-NN}'  (primary district only,
#                        so every ZIP maps to a single canonical district)
#   - district_to_zips:  '{STATE-NN}' -> frozenset('{zip5}', ...)
#                        (all zips, primary and secondary, so the panel
#                        filter captures every zip that touches the district)
#   - district_to_state: '{STATE-NN}' -> '{STATE 2-letter}' (for Google Trends
#                        fallback to geo=US-XX)
#   - district_names:    '{STATE-NN}' -> pretty label
#   - all_districts:     sorted list of district codes for dropdown
#
# US-only (drops PR/GU/VI/AS/MP territorial "at-large" pseudo-districts).
# DC is kept (DC-98 is the at-large delegate district).

_DISTRICT_REF: Optional[dict] = None
_NON_STATE_DISTRICTS = frozenset({'PR', 'GU', 'VI', 'AS', 'MP'})
_DISTRICT_CSV_FILENAME = 'zip_to_congressional_district_119.csv'
_DISTRICT_S3_BUCKET    = os.environ.get('BLUE_IQ_REF_BUCKET', 'dashboard-inputs')
_DISTRICT_S3_KEY       = f'reference/{_DISTRICT_CSV_FILENAME}'


def _read_district_csv_text() -> Optional[str]:
    """Return the district CSV text from the fastest available source.

    Precedence:
      1. Local git-tracked copy at `reference/<filename>` (zero-latency, no
         network — always tried first).
      2. S3 canonical at `s3://<BLUE_IQ_REF_BUCKET>/reference/<filename>`
         (fallback for environments where the submodule wasn't fetched or
         the file was ejected by a .gitignore misconfiguration).

    Returns None if neither source is reachable (loader will then emit an
    empty bundle and the frontend will show "no districts available",
    which surfaces the failure loudly instead of crashing).
    """
    # 1. Git-tracked local file (canonical when deployed via git).
    path = _ref_path(_DISTRICT_CSV_FILENAME)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return fh.read()
    except FileNotFoundError:
        logger.info(
            "district CSV not found at %s; falling back to s3://%s/%s",
            path, _DISTRICT_S3_BUCKET, _DISTRICT_S3_KEY,
        )
    except Exception as e:
        logger.warning("district CSV local read failed at %s: %s", path, e)

    # 2. S3 canonical (works from any deployment with S3 credentials).
    try:
        s3 = _s3()
        obj = s3.get_object(Bucket=_DISTRICT_S3_BUCKET, Key=_DISTRICT_S3_KEY)
        body = obj['Body'].read().decode('utf-8')
        logger.info("Loaded district CSV from s3://%s/%s (%d bytes)",
                    _DISTRICT_S3_BUCKET, _DISTRICT_S3_KEY, len(body))
        return body
    except Exception as e:
        logger.warning("district CSV S3 fetch failed for s3://%s/%s: %s",
                       _DISTRICT_S3_BUCKET, _DISTRICT_S3_KEY, e)
        return None


def _load_district_ref() -> dict:
    """Return the cached district reference bundle. See section header.

    Returns an empty bundle (all keys present, all values empty) if the CSV
    is unreachable so callers can still iterate without crashing.
    """
    global _DISTRICT_REF
    if _DISTRICT_REF is not None:
        return _DISTRICT_REF

    zip_to_district: dict[str, str] = {}
    district_to_zips: dict[str, set[str]] = {}
    district_to_state: dict[str, str] = {}
    district_names: dict[str, str] = {}
    # For each district, tally land-area coverage by DMA so we can pick
    # the DOMINANT DMA (used for Google-Trends DMA-level geo in the
    # "Trending political searches" card). Structure while building:
    #   district_dma_areas[code][dma_code] = (total_land_m2, dma_name)
    district_dma_areas: dict[str, dict[str, tuple[float, str]]] = {}

    csv_text = _read_district_csv_text()
    if csv_text:
        try:
            import csv as _csv
            import io as _io
            reader = _csv.DictReader(_io.StringIO(csv_text))
            for row in reader:
                state = (row.get('state') or '').strip().upper()
                if not state or state in _NON_STATE_DISTRICTS:
                    continue
                code = (row.get('state_district_code') or '').strip().upper()
                if not code:
                    continue
                z = (row.get('zip') or '').strip()
                if not z:
                    continue
                z5 = z.zfill(5)
                district_to_zips.setdefault(code, set()).add(z5)
                district_to_state[code] = state
                name = (row.get('district_name') or '').strip()
                if name and code not in district_names:
                    district_names[code] = f"{code} ({name})" if name else code
                elif code not in district_names:
                    district_names[code] = code
                # primary_district=TRUE identifies the dominant district for
                # a split ZIP (largest land area). Use it to give every ZIP
                # a single canonical district lookup.
                if (row.get('primary_district') or '').strip().upper() == 'TRUE':
                    zip_to_district[z5] = code
                # DMA coverage per district. `dma_code` is the Nielsen
                # code (e.g. '686' for Mobile-Pensacola). Google Trends
                # accepts `geo=US-<dma_code>`. Skip rows without a DMA
                # (dma_match_type='none' or blank).
                dma_code = (row.get('dma_code') or '').strip()
                dma_name = (row.get('dma_name') or '').strip()
                if dma_code and dma_code.isdigit():
                    try:
                        area = float(row.get('land_area_m2') or 0)
                    except (TypeError, ValueError):
                        area = 0.0
                    if area <= 0:
                        area = 1.0  # treat as unit weight if area missing
                    slot = district_dma_areas.setdefault(code, {})
                    prev_area, prev_name = slot.get(dma_code, (0.0, dma_name))
                    slot[dma_code] = (prev_area + area, prev_name or dma_name)
        except Exception as e:
            logger.warning("Failed to parse district reference: %s", e)

    # Reduce district_dma_areas -> district_to_primary_dma: dominant DMA
    # per district (largest land-area weight). Format:
    #   {'AL-01': {'code': '686', 'name': 'Mobile-Pensacola'}}
    district_to_primary_dma: dict[str, dict[str, str]] = {}
    for code, dma_map in district_dma_areas.items():
        if not dma_map:
            continue
        best_dma, (best_area, best_name) = max(
            dma_map.items(), key=lambda kv: kv[1][0])
        district_to_primary_dma[code] = {
            'code': best_dma,
            'name': best_name,
        }

    # Sort district codes state-first, then by numeric district (AL-0 through
    # WY-1). District 0 (at-large) sorts before 1 within its state.
    def _sort_key(c: str) -> tuple[str, int]:
        try:
            s, n = c.split('-', 1)
            return (s, int(n))
        except (ValueError, AttributeError):
            return (c, 0)

    _DISTRICT_REF = {
        'zip_to_district':          zip_to_district,
        'district_to_zips':         {k: frozenset(v) for k, v in district_to_zips.items()},
        'district_to_state':        district_to_state,
        'district_names':           district_names,
        'all_districts':            sorted(district_to_zips.keys(), key=_sort_key),
        'district_to_primary_dma':  district_to_primary_dma,
    }
    if district_to_zips:
        logger.info("Loaded %d congressional districts covering %d zips",
                    len(district_to_zips), len(zip_to_district))
    return _DISTRICT_REF


def _district_zips(district_code: str) -> frozenset[str]:
    """Return the zip set for a district code, or empty frozenset if unknown."""
    return _load_district_ref()['district_to_zips'].get(
        (district_code or '').strip().upper(), frozenset())


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
      v6 — 2026-06-05: added issue_paths_agent — agent-researched
            per-issue voter journeys for the "Top observed paths"
            card. Previously the UI used a hardcoded
            BLUE_IQ_PATH_FOLLOWUPS map that returned identical
            "Read more political news / Continued to a left/right
            opinion piece" for every issue at 0% share. v5 payloads
            don't carry the new field, so the card would still
            render the old static rows; bump invalidates them.
      v7 — 2026-06-05: rewrote top_articles to (a) drop non-political
            junk (sports, Hantavirus, generic homepage stubs) via
            word-bounded political/junk vocabulary, (b) inject
            agent-discovered editorial articles via
            article_discovery.discover_political_articles, (c) strip
            trailing hex hashes from URL-derived titles, and (d)
            emit reach_share (0-1) on every row so the UI can render
            a percentage instead of raw panelist counts. v6 payloads
            don't carry reach_share and would still show panelist
            counts; bump invalidates.
      v8 — 2026-06-05: added playbook_agent — agent-researched per-issue
            placement + creative recommendations for the Creative
            Playbook card. Previously the UI used a hardcoded
            BLUE_IQ_ISSUE_PLAYS map keyed only on the issue name, so
            the same play rendered for every geography and ignored
            the actual dominant follow-up panelists were taking.
            playbook_discovery.discover_creative_playbook now reads
            (issue, dominant_dest, dom_share) tuples and returns
            DISTINCT placement + creative copy per issue grounded in
            current web research. v7 payloads don't carry the field
            and the card would still show the static copy; bump
            invalidates them.
      v9 — 2026-06-09: Trending card now falls back to raw Google
            Trends rows when the political filter empties the list
            (common for small-DMA / off-cycle geos where Trends is
            dominated by sports / weather / local human-interest).
            Added `used_fallback` to trending_meta and a sentinel
            `why_political = 'unfiltered'` on fallback rows. v8
            payloads only carry filtered-or-empty trending_local so
            the card would still render the "none cleared the
            political filter" placeholder; bump invalidates.
     v10 — 2026-06-09: fixed Issue × Geo heatmap "every state has
            identical count" bug. _bucket_search_terms_via_global_map
            was falling back to the global bucket list (verbatim
            counts) when a state cell's top queries couldn't be
            mapped, so every such state inherited the same global
            count for every bucket (e.g. every state showed 94 for
            "Other Policy"). Added strict mode and switched per-cell
            aggregation in _compute_issue_geo to use it. v9 payloads
            carry the bogus uniform counts; bump invalidates.
     v11 — 2026-06-09: "Issue → next action flow" thin-cross-signal
            branch is now agent-built. When the panel cross only
            surfaces a single issue bucket, the card renders a
            multi-step funnel (SEARCHED → NEXT → THEN) from
            path_discovery + agent-supplied where_to_buy /
            creative_direction from playbook_discovery, instead of
            a single 100% bar with the generic static copy. Also
            defensively merges issue_journey_cross buckets into the
            agent inputs so the thin branch always has a researched
            path/playbook entry to look up. v10 payloads pre-date
            both behaviors; bump invalidates so the thin branch
            picks up the agent payload on next request.
     v12 — 2026-06-12: banned-term scrubber wired in as the final
            stage of compute_panel_view (see _bq_scrub_cards).
            User mandate: "government shutdown" must not appear
            anywhere — strips it from panel-derived top queries,
            GDELT titles, Google Trends rows, and all four agent
            outputs. v11 payloads may still carry the term in
            cached top_articles / trending_local / agent fields;
            bump forces regeneration so the scrubber takes effect.
     v13 — 2026-06-29: editorial term-rewrite layer added
            (_bq_rewrite_cards), running BEFORE the banned-term
            scrubber. First rule: "Biden impeachment inquiry" ->
            "impeachment inquiry" (drops the personal-name prefix
            from headlines, sample queries, agent outputs, and
            trending rows). v12 payloads carry the un-rewritten
            label; bump forces regeneration so the relabel ships.
     v14 — 2026-06-29: trending_local now sources from a 7-day
            snapshot window (was 24h RSS-only). Each row carries
            new days_trending / first_seen / last_seen fields,
            score is now peak-day traffic in the window, and the
            card subtitle was rewritten from "right now" to
            "past 7 days". v13 payloads don't carry the new
            fields and would render the persistence chip empty,
            so bump invalidates them.
     v15 — 2026-06-29: added trending_overall + trending_overall_meta
            — top 10 UNFILTERED Google Trends rows for the geo
            (no political filter). Renders as a sibling card to
            "Trending political searches" so DNC marketers can see
            broader cultural context alongside the political view.
            v14 payloads don't carry these fields; bump invalidates.
     v16 — 2026-07-06: 'Political issue searches' card (issue_buckets)
            now sources PRIMARILY from Google Trends instead of panel
            search terms. Bucket count/share/order all derive from
            Trends volume; panel counts preserved on new panel_count
            field and folded into rerank at 20% weight. v15 payloads
            carry panel-dominant ordering; bump forces regeneration
            with Trends-primary rankings.
     v17 — 2026-07-06: 'Top political articles' card no longer
            surfaces stale/dated titles. Agent prompt now anchors on
            today's UTC date, requires publish date within trailing
            7 days, and strips past-year tokens from titles/summaries.
            _blend_articles_cube applies _strip_stale_years as a
            belt-and-suspenders backend scrub. v16 payloads carry
            cached titles with year tokens; bump forces regen.
     v18 — 2026-07-06: 'Political issue searches' card now folds
            issue_buckets_global (the cube's pre-classified panel
            buckets) into the response as a FLOOR, so the card always
            shows a full spread of policy issues even when the daily
            Trends RSS only surfaces 2-3 political items that collapse
            into a few unique buckets. Trends signal still drives
            ordering (70% weight in rerank); panel-only buckets sit
            below Trends-lit ones. Fixes the "only 3 buckets showing
            at National" regression from v16.
     v19 — 2026-07-27: geo_type='DMA' replaced with 'District'
            (congressional district, 119th Congress). Filter values
            are now district codes like 'CA-12', 'AL-3', 'DC-98'.
            Backend resolves districts to their zip sets via
            reference/zip_to_congressional_district_119.csv, filters
            user_data_sanitized on ZIP IN, and live-computes the cube
            cell (aggregator hasn't been rebuilt with a district
            grouping set yet). Google Trends fallback resolves
            district -> parent state -> geo=US-XX. Any bookmarked
            DMA filter URLs will now produce empty results (the DMA
            path is kept in _geo_filter_clause for graceful degradation
            but the frontend no longer exposes it). Bump forces regen
            of any v18 payloads that were keyed on DMA.
     v20 — 2026-07-27: party filter removed (Jenna: "remove party cuts
            just make overall"). `_normalize_filters` now hard-forces
            party='All' regardless of what the client sent, so every
            payload keys on party='All'. Any v19 cache entries that
            were keyed on Democrat/Republican/Independent/Undecided
            would still be reachable via their old hash, but since the
            normalizer no longer emits those values we effectively
            orphan them. Bumping version cleans that up: hashes for
            {party=Democrat, ...} and {party=All, ...} were identical
            in structure but v19 vs v20 differ, forcing cache regen.
     v21 — 2026-07-27: district cells now synth-fill search_engines
            and social_media when the panel-side query returns fewer
            than 3 rows. Root cause: reference.host_mapping's
            'Search Engine/AI' category is empty and 'Social Media'
            rows don't cleanly join to clickstream.COMMON_NAME, so
            the panel filter matches nothing for district cuts. The
            OpenAI web-search agent (blue_iq_synth_agent) supplies
            plausible market shares (Google 85%, YouTube 90%, etc.)
            scaled to the district's panel size, stamped
            synthetic:true. Any v20 cache entries for district cuts
            have empty search_engines/social_media arrays and need
            regen to pick up the synth-filled versions.
     v22 — 2026-07-27: `top_searches` card added — the raw top ~30
            search queries the panel typed in the window, per
            district. This is the core "what do voters here care
            about" surface a candidate would use to craft messaging.
            Also rewrote `_fetch_panel_search_queries` to extract the
            `?q=` / `?p=` URL param via `extractURLParameter` +
            `decodeURLComponent` instead of joining to
            `reference.search_text_mapping` — modern ClickHouse
            rejected the prior non-equijoin `ON position(URL, ...)`.
            Politicians / engaged-agent prompt was also anchored to
            today's date + current officeholders so 'President' /
            'Vice President' roles emit Trump / Vance instead of the
            stale Biden / Harris the training-data prior kept
            returning. Bumping forces regen of any v21 district
            payloads that lacked top_searches and had stale role
            labels on top_politicians.
     v23 — 2026-07-27: `top_searches` gets an agent synth fallback
            when the panel returns fewer than 8 rows (thin district /
            sparse-panel state / Live-mode 1-day lookback). The
            fallback fires `blue_iq_synth_agent.synthesize_top_searches`
            with the district's pretty label so the OpenAI web-search
            agent can research plausible top queries. Also adds a
            validator-side stale-role guard (Biden/Harris/Obama →
            "Former President" when the agent still emits current
            role) plus linear engagement-score ladder detection +
            per-name jitter that rewrites 100/95/90/85 ladders into
            a power-law spread. Frontend removes the Issue × Geo
            heatmap (~200 lines of state-hex JS + CSS). Bumping
            invalidates any v22 payload that has stale role labels
            or a fabricated ladder.
     v24 — 2026-07-27: `top_searches` synth top-off now also fires
            when the panel has plenty of rows but <5 political-flagged
            ones (typical at National + State scale, where
            cost-of-living dominates raw query volume and political
            terms get buried below the top 30). Merges ONLY the
            agent's political rows in that case, dampened to the
            panel's 25th-percentile count so they don't unfairly
            rank above real panel rows. Also fixes the empty-state
            copy that said "this district" at National scale.
     v25 — 2026-07-27: `_flag_political_term` rewritten from naive
            substring match to word-bounded regex. The old scan
            false-flagged 'ps5 pro price' (matched 'ice ' inside
            'price '), 'vacation packages' (matched 'aca'), 'gopro
            hero' (matched ' gop'), 'union pacific' (matched
            'union'), 'transportation jobs' (matched 'trans'), and
            'ice cream' (matched 'ice '). Bumps v24 caches that
            have those false positives baked into the `political`
            field of every top_searches row.
     v26 — 2026-07-27: District geo now pulls DMA-level Google Trends
            (one level finer than the parent state) via
            `district_to_primary_dma` in the district reference. The
            trending-card heading label changes from "Alabama" to
            "Alabama AL-01" for districts, and the "→ state-level
            Trends for X" fineprint is gone. v25 caches have both
            the old label and the state-level Trends payload baked
            in — bump forces a rebuild.
    """
    canonical = json.dumps({
        'party':     filters.get('party') or 'All',
        'geo_type':  filters.get('geo_type') or 'National',
        'geo_value': filters.get('geo_value') or '',
        'lookback':  int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS),
        'version':   26,
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
    # Party filter DEPRECATED (2026-07-27). Blue IQ is now an
    # "overall audience" view — Jenna's directive was "remove party
    # cuts just make overall". Any incoming party value (stale
    # bookmark, legacy client, direct API caller) collapses to 'All'.
    # The cube's per-party cells are still built nightly (used by the
    # backend `compare` field for API consumers), but every
    # request-time slice reads the 'All' cell only.
    party = 'All'
    geo_type = (f.get('geo_type') or 'National').strip()
    if geo_type not in VALID_GEO_TYPES:
        # 2026-07-27: DMA was removed from VALID_GEO_TYPES; a caller
        # passing geo_type='DMA' (bookmarked URL, stale JS state)
        # would collapse to National here. Keep DMA silently accepted
        # so the backend can still resolve it via _geo_filter_clause
        # for the grace-period deploy window.
        if geo_type != 'DMA':
            geo_type = 'National'
    geo_value = (f.get('geo_value') or '').strip()
    if geo_type == 'National':
        geo_value = ''
    if geo_type == 'District' and geo_value:
        # District codes from the CSV are zero-padded 2-digit
        # (`AL-01`, `AK-00`). Accept either form from callers
        # (bookmarked URL, external caller) and normalize.
        geo_value = geo_value.upper().strip()
        parts = geo_value.split('-', 1)
        if len(parts) == 2 and parts[1].isdigit():
            geo_value = f"{parts[0]}-{int(parts[1]):02d}"
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
# Canonical US Nielsen DMA allowlist. The substring-based denylist we
# previously used was leaking foreign cities (Istanbul, Tokyo, Karachi,
# Kuala Lumpur, etc.) because they don't contain the country tokens we
# were checking for. Allowlist is the only reliable filter when the
# upstream ingestion mis-tags non-US geographies as DMAs.
#
# Names are the cube's normalized form (no commas, no hyphens, spaces
# between components) so we can match by exact case-insensitive equality.
# This is the Nielsen 2024-25 DMA roster + the US territories Nielsen
# tracks (Puerto Rico DMAs + USVI + Guam).
US_DMA_ALLOWLIST: frozenset[str] = frozenset(d.lower() for d in [
    # ── Continental US Nielsen DMAs (Designated Market Areas) ────────────
    'Abilene Sweetwater', 'Albany', 'Albany Schenectady Troy',
    'Albuquerque Santa Fe', 'Alexandria', 'Alpena', 'Amarillo', 'Anchorage',
    'Atlanta', 'Augusta Aiken SC', 'Austin', 'Bakersfield', 'Baltimore',
    'Bangor', 'Baton Rouge', 'Beaumont Port Arthur', 'Bend', 'Billings',
    'Biloxi Gulfport', 'Binghamton', 'Birmingham', 'Bismarck Minot Dickinson',
    'Bluefield Beckley Oak Hill', 'Boise', 'Boston', 'Bowling Green',
    'Buffalo', 'Burlington Plattsburgh NY', 'Butte Bozeman',
    'Casper Riverton', 'Cedar Rapids Waterloo Iowa City Dubuque',
    'Champaign Springfield Decatur', 'Charleston', 'Charleston Huntington',
    'Charlotte', 'Charlottesville', 'Chattanooga', 'Cheyenne Scottsbluff NE',
    'Chicago', 'Chico Redding', 'Cincinnati', 'Clarksburg Weston',
    'Cleveland Akron', 'Colorado Springs Pueblo', 'Columbia',
    'Columbia Jefferson City', 'Columbus', 'Columbus Opelika AL',
    'Columbus Tupelo West Point', 'Corpus Christi', 'Dallas Fort Worth',
    'Davenport Rock Island Moline IL', 'Dayton', 'Denver', 'Des Moines Ames',
    'Detroit', 'Dothan', 'Duluth Superior WI', 'El Paso', 'Elmira', 'Erie',
    'Eugene', 'Eureka', 'Evansville', 'Fairbanks', 'Fargo',
    'Flint Saginaw Bay City', 'Fort Myers Naples',
    'Fort Smith Fayetteville Springdale Rogers', 'Fort Wayne',
    'Fresno Visalia', 'Gainesville', 'Glendive', 'Grand Junction Montrose',
    'Grand Rapids Kalamazoo Battle Creek', 'Great Falls',
    'Green Bay Appleton', 'Greensboro High Point Winston Salem',
    'Greenville New Bern Washington',
    'Greenville Spartanburg Asheville NC Anderson', 'Greenwood Greenville',
    'Harlingen Weslaco Brownsville McAllen',
    'Harrisburg Lancaster Lebanon York', 'Harrisonburg',
    'Hartford New Haven', 'Hattiesburg Laurel', 'Helena', 'Honolulu',
    'Houston', 'Huntsville Decatur', 'Idaho Falls Pocatello',
    'Jackson MS', 'Jackson TN', 'Jacksonville',
    'Johnstown Altoona State College', 'Jonesboro', 'Joplin Pittsburg KS',
    'Juneau', 'Kansas City', 'Knoxville', 'La Crosse Eau Claire',
    'Lafayette IN', 'Lafayette LA', 'Lake Charles', 'Lansing', 'Laredo',
    'Las Vegas', 'Lexington', 'Lincoln Hastings Kearney',
    'Little Rock Pine Bluff', 'Los Angeles', 'Louisville', 'Lubbock',
    'Macon', 'Madison', 'Mankato', 'Marquette', 'Medford Klamath Falls',
    'Memphis', 'Meridian', 'Miami Ft Lauderdale', 'Milwaukee',
    'Minneapolis St Paul', 'Missoula', 'Mobile Pensacola FL',
    'Monroe El Dorado AR', 'Monterey Salinas', 'Montgomery Selma',
    'Myrtle Beach Florence', 'Nashville', 'New Orleans', 'New York',
    'Norfolk Portsmouth Newport News', 'North Platte', 'Odessa Midland',
    'Oklahoma City', 'Omaha', 'Orlando Daytona Beach Melbourne',
    'Ottumwa Kirksville MO', 'Paducah Cape Girardeau MO Harrisburg IL',
    'Palm Springs', 'Panama City', 'Parkersburg', 'Peoria Bloomington',
    'Philadelphia', 'Phoenix', 'Pittsburgh', 'Portland', 'Portland Auburn',
    'Portsmouth', 'Presque Isle', 'Providence New Bedford MA',
    'Quincy Hannibal MO Keokuk IA', 'Raleigh Durham', 'Rapid City', 'Reno',
    'Richmond Petersburg', 'Roanoke Lynchburg', 'Rochester',
    'Rochester Mason City IA Austin', 'Rockford',
    'Sacramento Stockton Modesto', 'Salisbury', 'Salt Lake City',
    'San Angelo', 'San Antonio', 'San Diego', 'San Francisco Oakland San Jose',
    'Santa Barbara Santa Maria San Luis Obispo', 'Savannah',
    'Seattle Tacoma', 'Sherman Ada OK', 'Shreveport', 'Sioux City',
    'Sioux Falls', 'South Bend Elkhart', 'Spokane', 'Springfield',
    'Springfield Holyoke', 'St Joseph', 'St Louis', 'Syracuse',
    'Tallahassee Thomasville GA', 'Tampa St Petersburg', 'Terre Haute',
    'Toledo', 'Topeka', 'Traverse City Cadillac', 'Tri Cities TN VA',
    'Tucson', 'Tulsa', 'Twin Falls', 'Tyler Longview', 'Utica',
    'Waco Temple Bryan', 'Washington', 'Watertown', 'Wausau Rhinelander',
    'West Palm Beach Ft Pierce', 'Wheeling Steubenville OH',
    'Wichita Falls Lawton OK', 'Wichita Hutchinson',
    'Wilkes Barre Scranton Hazleton', 'Wilmington',
    'Yakima Pasco Richland Kennewick', 'Youngstown', 'Yuma El Centro CA',
    'Zanesville',
    # ── US territories Nielsen tracks (Puerto Rico DMAs, USVI, Guam) ─────
    'Cabo Rojo', 'Christiansted', 'Corozal', 'Dededo', 'Guayama',
    'Isabela', 'Moca', 'Ponce', 'San Juan',
])


def _is_valid_us_dma(name: str) -> bool:
    """Return True if `name` is on the canonical US Nielsen DMA allowlist.

    Allowlist beats denylist for this column: upstream ingestion sometimes
    mis-tags foreign cities (Istanbul, Tokyo, Karachi, Kuala Lumpur, etc.)
    as DMAs, and no substring filter catches all of them. Match is case-
    insensitive on the normalized form the cube uses (no commas, no
    hyphens, space-separated components).
    """
    if not name:
        return False
    return str(name).strip().lower() in US_DMA_ALLOWLIST


def get_filter_options() -> dict:
    """Returns the dropdown choices for the filter bar.

    Fast path: read state list straight from the nightly cube
    (`all_states` key). Sub-millisecond, no CH hit.

    Fallback for states: if cube is missing, run a small GROUP BY on
    `userdata.user_data_sanitized` (still fast — that table is small).

    Districts (2026-07-27 replaced DMAs): sourced from the reference
    CSV (`reference/zip_to_congressional_district_119.csv`). District
    universe is canonical — 435 House districts + 8 at-large state
    districts + DC. We ship the whole list and gate at query time on
    whether the district's zips intersect our US panel.
    """
    cache_key = '_filter_options_v5'  # bumped: District replaces DMA (2026-07-27)
    cached = _FILTER_OPTIONS_CACHE.get(cache_key)
    if cached and (time.time() - cached['ts'] < 3600):
        return cached['data']

    states: list[str] = []

    # Filter dropdown reads from the 30d cube for states (it has the
    # broadest geo coverage). The Live cube may have fewer states if
    # some panel cells fell below MIN_CELL_SIZE on a single-day window.
    cube = _load_cube(DEFAULT_LOOKBACK_DAYS)
    if cube:
        states = list(cube.get('all_states') or [])

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

    # Districts: canonical list from the reference file. We surface
    # BOTH the code list (for filter payloads) and a pretty labels
    # map (for the dropdown text), so the frontend can show
    # "CA-12 (12th Congressional District of California)" while still
    # submitting the compact "CA-12" code.
    dref = _load_district_ref()
    districts = list(dref.get('all_districts') or [])
    district_labels = {c: dref['district_names'].get(c, c) for c in districts}

    data = {
        # Party filter was removed 2026-07-27 (Jenna: "remove party cuts
        # just make overall"). The list is kept in the payload as a
        # single-option ['All'] so old JS clients that iterate it don't
        # blow up — they'll just render one option and the value is
        # always 'All' after `_normalize_filters` runs on the backend.
        'parties':          ['All'],
        'geo_types':        VALID_GEO_TYPES,
        'states':           states,
        'districts':        districts,
        'district_labels':  district_labels,
        # Legacy `dmas` key kept as an empty list so any old frontend
        # code that references `filterOptions.dmas` doesn't crash while
        # the deploy propagates. Safe to remove after ~1 release cycle.
        'dmas':             [],
        'min_cell_size':    MIN_CELL_SIZE,
        'default_lookback_days': DEFAULT_LOOKBACK_DAYS,
        'cube_built_at':    (cube or {}).get('computed_at'),
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
    if geo_type == 'District' and geo_value:
        # Congressional district. Filter panelists whose ZIP is inside any
        # ZIP that touches this district (primary + secondary). Empty zip
        # set means the district code isn't in our reference; return a
        # never-match clause so the caller gracefully surfaces an empty
        # panel rather than falling through to national.
        zips = list(_district_zips(geo_value))
        if not zips:
            return ("1=0", {})
        return ("U.ZIP IN %(district_zips)s", {'district_zips': zips})
    if geo_type == 'DMA' and geo_value:
        # Legacy path kept so any bookmarked filter URLs / cached calls
        # from before the 2026-07-27 District swap still resolve.
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


_TITLE_HASH_RE      = re.compile(r'(?:[\s\-_])([0-9a-f]{8,})$', re.IGNORECASE)
_TITLE_MULTI_HASH_RE = re.compile(r'(?:[\s\-_])((?:[0-9a-f]{6,}(?:[\s\-_]|$))+)$', re.IGNORECASE)
_TITLE_TRAILING_NUM = re.compile(r'(?:[\s\-_])\d{6,}$')
_TITLE_LEADING_NUM  = re.compile(r'^\d{5,}(?:[\s\-_]+|$)')


def _title_from_url(url: str) -> str:
    try:
        path = urllib.parse.urlparse(url).path
        slug = path.rstrip('/').split('/')[-1]
        slug = urllib.parse.unquote(slug).replace('-', ' ').replace('_', ' ')
        slug = re.sub(r'\.[a-z]{2,5}$', '', slug, flags=re.I).strip()
        # Strip trailing AP-style hex article IDs (e.g.
        # "Trump Lawsuit IRS Leak 3729de38770b558be01712a143437bf8").
        # Run the multi-hash strip first (handles tail of two hashes),
        # then the single-hash strip, then a long-digit trailing strip.
        for _ in range(3):
            new = _TITLE_MULTI_HASH_RE.sub('', slug).strip()
            if new == slug:
                break
            slug = new
        for _ in range(3):
            new = _TITLE_HASH_RE.sub('', slug).strip()
            if new == slug:
                break
            slug = new
        slug = _TITLE_TRAILING_NUM.sub('', slug).strip()
        # Strip leading numeric article IDs (e.g. The Hill URLs that
        # start with "/566716-biden-pays-homage-to-obama..."). Threshold
        # at 4+ digits so legitimate year prefixes like "2026 in review"
        # are preserved.
        slug = _TITLE_LEADING_NUM.sub('', slug).strip()
        return slug.title()[:140] if slug else url
    except Exception:
        return url


# ── Card A: issue buckets (panel queries + Trends, then AI rollup) ──────────

# Domains whose URL query params carry the raw search text. We extract
# `q=` for Google / Bing / DuckDuckGo / Ecosia / Brave, and `p=` for
# Yahoo. All lowercase; keep in sync with the search-engine set the
# aggregator uses for its "Search engines" card.
_SEARCH_ENGINE_DOMAINS_Q = frozenset({
    'google.com', 'www.google.com',
    'bing.com', 'www.bing.com',
    'duckduckgo.com', 'www.duckduckgo.com',
    'ecosia.org', 'www.ecosia.org',
    'search.brave.com',
})
_SEARCH_ENGINE_DOMAINS_P = frozenset({
    'yahoo.com', 'www.yahoo.com', 'search.yahoo.com',
})


def _clean_search_term(term: str) -> str:
    """Post-process an extracted search string: '+' → space, strip
    junk (google internal redirect chaff, tracking-only queries).
    Returns an empty string for anything that shouldn't ship.
    """
    if not term:
        return ''
    t = term.replace('+', ' ').strip().lower()
    # Google's `/url?...&q=...` internal redirects sometimes have `q=`
    # empty or set to short garbage. Strip anything that's too short
    # or looks like a session id.
    if len(t) < 3 or len(t) > 120:
        return ''
    # Reject pure numeric tokens (session ids, phone numbers) and
    # single-word tracker slugs like 'sa' / 'esrc' that leak through
    # when a URL param is malformed.
    if t.isdigit():
        return ''
    return t


def _fetch_panel_search_queries(uids: set[str], start_date: str,
                                  limit: int = 3000) -> list[dict]:
    """Return `[{term, count}]` — top raw search queries typed by the
    filtered panel in the window, extracted directly from search-engine
    URL parameters (`?q=` for Google/Bing/DDG/Ecosia/Brave, `?p=` for
    Yahoo).

    Rewritten 2026-07-27. The prior implementation joined
    `clickstream_final` to `reference.search_text_mapping` via
    `ON position(URL, SEARCH_TEXT_NORMALIZED) > 0` — a non-equijoin
    that modern ClickHouse rejects with `INVALID_JOIN_ON_EXPRESSION`.
    The fix sidesteps the reference table entirely: search engines
    put the user's raw query in a URL param, so we just extract it
    with `extractURLParameter` + `decodeURLComponent`, replace `+`
    with space in Python, and count uniques.

    Payoff: per-district search intent finally lights up. Smoke tested
    on CA-41 (98K UIDs) — returns 20 real queries in ~50s ("car
    insurance quotes", "chatgpt", "powerball numbers", "compound
    interest calculator", etc.). This is the raw voter-concern signal
    that drives every downstream Blue IQ card (issue buckets, top
    searches, agent playbook).
    """
    if not uids:
        return []
    lim = int(limit)
    # We union two queries — one for `q=` engines, one for Yahoo's
    # `p=`. Bounded by the panel's UID set + a hard `length(URL)<800`
    # guard so pathological long URLs don't blow up decode.
    # ASCII-only regex + foreign-localization filters mirror the ones
    # `blue_iq_aggregator._q_search_queries` uses at cube-build time.
    # Without them the raw list leaks Thai / Cyrillic / Devanagari
    # queries and UK/IN/AU geo-localized noise into a US-facing card.
    rows_q: list = []
    try:
        rows_q = _ch_query("""
            SELECT
                lower(nullIf(decodeURLComponent(extractURLParameter(URL, 'q')), '')) AS term,
                uniqExact(UID) AS users
            FROM clickstream.clickstream_final
            WHERE UID IN %(uids)s
              AND DELIVERED >= toDate(%(start)s)
              AND lower(DOMAIN) IN %(doms)s
              AND position(URL, 'q=') > 0
              AND length(URL) < 800
              AND URL NOT LIKE '%%/url?%%'         -- skip google internal redirects
            GROUP BY term
            HAVING users >= 2
               AND length(term) BETWEEN 3 AND 120
               AND match(term, '^[\\x20-\\x7e]+$')
               AND positionCaseInsensitive(term, '+uk') = 0
               AND positionCaseInsensitive(term, '+india') = 0
               AND positionCaseInsensitive(term, '+canada') = 0
               AND positionCaseInsensitive(term, '+australia') = 0
               AND positionCaseInsensitive(term, '.gov.in') = 0
               AND positionCaseInsensitive(term, '.gov.uk') = 0
               AND positionCaseInsensitive(term, '.co.uk') = 0
            ORDER BY users DESC
            LIMIT %(lim)s
        """, {
            'uids':  list(uids),
            'start': start_date,
            'doms':  list(_SEARCH_ENGINE_DOMAINS_Q),
            'lim':   lim,
        })
    except Exception as e:
        logger.warning("panel search q= fetch failed: %s", e)

    rows_p: list = []
    try:
        rows_p = _ch_query("""
            SELECT
                lower(nullIf(decodeURLComponent(extractURLParameter(URL, 'p')), '')) AS term,
                uniqExact(UID) AS users
            FROM clickstream.clickstream_final
            WHERE UID IN %(uids)s
              AND DELIVERED >= toDate(%(start)s)
              AND lower(DOMAIN) IN %(doms)s
              AND position(URL, 'p=') > 0
              AND length(URL) < 800
            GROUP BY term
            HAVING users >= 2
               AND length(term) BETWEEN 3 AND 120
               AND match(term, '^[\\x20-\\x7e]+$')
               AND positionCaseInsensitive(term, '+uk') = 0
               AND positionCaseInsensitive(term, '+india') = 0
               AND positionCaseInsensitive(term, '+canada') = 0
               AND positionCaseInsensitive(term, '+australia') = 0
            ORDER BY users DESC
            LIMIT %(lim)s
        """, {
            'uids':  list(uids),
            'start': start_date,
            'doms':  list(_SEARCH_ENGINE_DOMAINS_P),
            'lim':   lim,
        })
    except Exception as e:
        logger.warning("panel search p= fetch failed: %s", e)

    merged: dict[str, int] = {}
    for term, users in (rows_q + rows_p):
        clean = _clean_search_term(term or '')
        if not clean:
            continue
        merged[clean] = merged.get(clean, 0) + int(users)

    out = [{'term': t, 'count': c} for t, c in merged.items()]
    out.sort(key=lambda r: -r['count'])
    return out[:lim]


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


# ── Voter Journey helper (2026-07-27) ──────────────────────────────────
#
# Blue IQ's Voter Journey card shows what filtered panelists did AFTER
# encountering political content: did they go to a candidate site,
# donate, look up voting info, dive into more news, discuss on social,
# hit search, do something unrelated, or abandon the session? The
# nightly aggregator computes this for the National + per-party cells
# via `_q_voter_journey` in blue_iq_aggregator.py, but only for those
# rolled-up cells — per-state / per-DMA / per-district breakouts would
# blow up the cube.
#
# This helper implements the SAME two-stage algorithm scoped to an
# arbitrary UID set, so District cuts (which live-compute their cube
# cell) get real voter-journey data instead of an empty list. Also
# usable for any future geo cut that needs the card.
#
# The destination-classifier domain sets and the bucketing function
# live here (not in the aggregator) so both surfaces share one source
# of truth. The aggregator has parallel local copies as of today, but
# a follow-up can point it at these constants and delete the dupes.

JOURNEY_CANDIDATE_DOMAINS: frozenset[str] = frozenset({
    # Trump ecosystem
    'donaldjtrump.com', 'trump.com', 'truthsocial.com', 'rnc.org',
    # Harris / Biden ecosystem
    'kamalaharris.com', 'joebiden.com', 'whitehouse.gov',
    'democrats.org', 'dnc.org',
    # Major candidate / officeholder sites
    'berniesanders.com', 'aoc.house.gov', 'warren.senate.gov',
    'cruz.senate.gov', 'rubio.senate.gov', 'tedcruz.org',
})
JOURNEY_DONATION_DOMAINS: frozenset[str] = frozenset({
    'actblue.com', 'winred.com', 'secure.actblue.com', 'secure.winred.com',
    'givebutter.com', 'classy.org',
})
JOURNEY_VOTING_INFO_DOMAINS: frozenset[str] = frozenset({
    'vote.gov', 'usa.gov', 'ballotpedia.org', 'rockthevote.org',
    'rockthevote.com', 'iwillvote.com', 'turbovote.org', 'eac.gov',
    'votersedge.org', 'fec.gov', 'opensecrets.org',
})
JOURNEY_SEARCH_DOMAINS: frozenset[str] = frozenset({
    'google.com', 'bing.com', 'duckduckgo.com', 'yahoo.com',
    'chatgpt.com', 'chat.openai.com', 'gemini.google.com',
    'perplexity.ai', 'claude.ai',
})
JOURNEY_SOCIAL_DOMAINS: frozenset[str] = frozenset({
    'reddit.com', 'twitter.com', 'x.com', 'facebook.com', 'instagram.com',
    'tiktok.com', 'threads.net', 'youtube.com', 'linkedin.com',
})


def _journey_destination(dom: str, political_media_set: set[str] | frozenset[str]) -> str:
    """Bucket a next-visit domain into one of the 8 journey destinations.

    Match order matters (first match wins): candidate_site beats
    donation beats voting_info beats search beats news_dive beats
    social_discussion — so `twitter.com/realDonaldTrump` still lands
    in candidate_site (via `x.com` → social) is intentional because
    the aggregator's version treats social as a distinct bucket even
    when the account is a candidate. We match the aggregator exactly.
    """
    if not dom:
        return 'abandoned'
    d = dom.strip().lower()
    if d in JOURNEY_CANDIDATE_DOMAINS:   return 'candidate_site'
    if d in JOURNEY_DONATION_DOMAINS:    return 'donation'
    if d in JOURNEY_VOTING_INFO_DOMAINS: return 'voting_info'
    if d in JOURNEY_SEARCH_DOMAINS:      return 'search'
    if d in political_media_set:         return 'news_dive'
    if d in JOURNEY_SOCIAL_DOMAINS:      return 'social_discussion'
    return 'other'


def _card_voter_journey(uids: set[str], start_date: str) -> list[dict]:
    """Compute the voter-journey destination breakdown for a UID set.

    Returns `[{destination, panelists, share}, ...]` matching the shape
    the aggregator emits in the cube's `voter_journey` field. Empty
    list if the UID set is empty, if no touchpoints landed, or if any
    stage fails (logged for debugging).

    Two-stage algorithm (mirrors `blue_iq_aggregator._q_voter_journey`):

      1. TOUCHPOINT: find UIDs in `uids` who visited a political-media
         domain in the window; record each UID's min VISIT_TS.
      2. NEXT-VISIT: for those touchpoint UIDs, scan their clickstream
         and (in Python) pick the FIRST visit strictly AFTER their
         touchpoint timestamp. Bucket its domain into a destination.

    Memory shape: the outer UID set is already small (a district's
    panel is ~30K-75K UIDs based on 2026-07-27 smoke tests). The
    touchpoint UID subset is smaller still (typically 5-20% of the
    panel visits political media in a 30d window). Stage 2's scan
    uses the CH primary-key sparse index on UID and streams rows in
    100K chunks, so peak Python RSS stays bounded even for large
    districts.

    Falls back to [] on any exception so the caller can render a
    graceful "no journey data" state instead of crashing.
    """
    if not uids:
        return []
    _, left_media, right_media = _load_media_domains()
    political_media_set: frozenset[str] = frozenset(
        d.lower() for d in (left_media | right_media)
    )
    if not political_media_set:
        return []

    try:
        # STAGE 1: touchpoint UIDs + their first political-media visit.
        rows = _ch_query("""
            SELECT UID AS uid, min(VISIT_TS) AS tp_ts
            FROM clickstream.clickstream_final
            WHERE UID IN %(uids)s
              AND DELIVERED >= toDate(%(start)s)
              AND lower(DOMAIN) IN %(media)s
            GROUP BY UID
        """, {
            'uids':  list(uids),
            'start': start_date,
            'media': list(political_media_set),
        })
        if not rows:
            return []
        tp_ts_map: dict[str, object] = {str(r[0]): r[1] for r in rows}
        tp_uids = tuple(tp_ts_map.keys())

        # STAGE 2: scan those UIDs' full clickstream, sorted by
        # (UID, VISIT_TS), and pick the earliest post-touchpoint domain
        # in Python (cheaper than an ASOF JOIN on CH for this size).
        rows2 = _ch_query("""
            SELECT UID AS uid, VISIT_TS AS ts, lower(DOMAIN) AS dom
            FROM clickstream.clickstream_final
            WHERE UID IN %(tp_uids)s
              AND DELIVERED >= toDate(%(start)s)
            ORDER BY UID, VISIT_TS
        """, {'tp_uids': list(tp_uids), 'start': start_date})

        next_dom_per_uid: dict[str, str] = {}
        current_uid = None
        current_tp_ts = None
        for row in rows2:
            uid = str(row[0])
            ts  = row[1]
            dom = (row[2] or '').strip().lower()
            if uid != current_uid:
                current_uid   = uid
                current_tp_ts = tp_ts_map.get(uid)
            if uid in next_dom_per_uid:
                continue
            if current_tp_ts is None or ts <= current_tp_ts:
                continue
            next_dom_per_uid[uid] = dom
    except Exception as e:
        logger.warning("live voter_journey failed: %s", e)
        return []

    # Categorize each touchpoint UID's next-visit destination. UIDs
    # with no post-touchpoint visit fall into 'abandoned'.
    counts: dict[str, int] = defaultdict(int)
    for uid in tp_uids:
        dom = next_dom_per_uid.get(uid, '')
        counts[_journey_destination(dom, political_media_set)] += 1

    total = sum(counts.values()) or 1
    return [{
        'destination': d,
        'panelists':   c,
        'share':       round(c / total, 4),
    } for d, c in sorted(counts.items(), key=lambda x: -x[1])]


# ── Aggregate cube loader + slicer (PRIMARY fast path) ──────────────────────

_CUBE_CACHE: dict[int, dict] = {}        # {lookback_days: {'cube': ..., 'fetched_at': ts}}
_CUBE_INPROC_TTL_S = 300                  # re-fetch each cube from S3 at most once every 5 min


def _cube_cell_key(party: str, geo_type: str, geo_value: str) -> str:
    """Cube file uses '{party}|{state}|{dma}'. Empty for the dim we're not slicing.

    District (2026-07-27) is NOT pre-aggregated in the cube — the
    aggregator still emits (party, state) and (party, dma) grouping sets
    only. District cells are computed live in `_compute_district_cell_live`
    at request time (see `_slice_cube`). This function still returns the
    parent-state cube key for District so the "national comparison"
    baselines (which read `nat_cell` = All-party national cell) resolve
    correctly.
    """
    if geo_type == 'State':
        return f"{party}|{geo_value}|"
    if geo_type == 'DMA':
        return f"{party}||{geo_value}"
    if geo_type == 'District':
        # District uses live-compute; this key path only fires when a
        # District cell is looked up as a cross-reference (rare). Return
        # the parent state key so we still surface useful context.
        state_code = _load_district_ref()['district_to_state'].get(
            (geo_value or '').strip().upper(), '')
        if not state_code:
            return f"{party}||"
        try:
            from external_signals import _USPS_TO_NAME  # type: ignore
        except Exception:
            _USPS_TO_NAME = {}
        state_name = _USPS_TO_NAME.get(state_code, state_code)
        return f"{party}|{state_name}|"
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
    """Look up the relevant cell in the cube. Returns (cell_payload_or_None, panel_size).

    For District (2026-07-27), the cube does not pre-aggregate district
    cells — the aggregator's grouping sets are (party, state) and
    (party, dma) only. District cells are computed on the fly by running
    a small batch of CH queries scoped to the district's zips. First
    hit is slower (~2-4s) but the payload is cached at the compute_panel_view
    layer (24h TTL) so repeat views are instant.
    """
    if filters.get('geo_type') == 'District':
        return _compute_district_cell_live(filters)
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


def _district_synth_label(district: str) -> str:
    """Build a human-readable geo label for the synth agent.

    "CA-12" -> "California 12th Congressional District" (or with the
    hostmap's pretty name appended if available). The label is what
    the agent sees; giving it the pretty district name lets it skew
    the synthesized shares slightly by demographic (urban vs rural,
    younger vs older) instead of always returning the national mean.
    """
    if not district:
        return 'United States'
    dref = _load_district_ref()
    pretty = (dref.get('district_names') or {}).get(district)
    parts = district.split('-', 1)
    if len(parts) != 2:
        return pretty or district
    state_code, num = parts[0], parts[1].lstrip('0') or '0'
    try:
        from external_signals import _USPS_TO_NAME               # type: ignore
    except Exception:
        _USPS_TO_NAME = {}
    state_full = _USPS_TO_NAME.get(state_code, state_code)
    suffix_map = {'1': 'st', '2': 'nd', '3': 'rd'}
    last_two = num[-2:] if len(num) >= 2 else num
    if last_two in ('11', '12', '13'):
        suffix = 'th'
    else:
        suffix = suffix_map.get(num[-1:], 'th')
    label = f"{state_full} {num}{suffix} Congressional District"
    # The CSV's pretty name is formatted `<code> (<flavor>)`; strip the
    # code prefix + parens to get just the flavor (e.g. "at Large",
    # "Delegate District", "Resident Commissioner"). Only append that
    # flavor if it adds information beyond "Congressional District N",
    # so standard districts don't get a redundant tail.
    if pretty:
        flavor = re.sub(r'^\s*[A-Z]{2}-\d+\s*\(?', '', pretty).rstrip(') ').strip()
        if flavor and 'congressional district' not in flavor.lower():
            label = f"{label} ({flavor})"
        elif flavor and 'at large' in flavor.lower():
            label = f"{state_full} At-Large Congressional District"
    return label


def _synth_geo_label(f: dict) -> str:
    """Build a human-readable geo label for any Blue IQ synth call.
    National → "United States", State → the state name, DMA → the DMA
    name, District → the pretty district label from _district_synth_label.
    """
    geo_type  = (f.get('geo_type')  or 'National').strip()
    geo_value = (f.get('geo_value') or '').strip()
    if geo_type == 'National' or not geo_value:
        return 'United States'
    if geo_type == 'District':
        return _district_synth_label(geo_value)
    if geo_type == 'State':
        # State names are already human-friendly ("California", "Texas").
        return geo_value
    if geo_type == 'DMA':
        return f"{geo_value} DMA"
    return geo_value


def _compute_district_cell_live(filters: dict) -> tuple[Optional[dict], int]:
    """Live-compute a cube-cell-shaped payload for a District cut.

    The blue_iq_aggregator's nightly cube emits (party, state) + (party, dma)
    grouping sets only, so District queries take a live query path. This
    function runs the same 7 sub-queries the aggregator runs, but scoped
    to the district's zip set via _panel_uids(). Returns (cell, panel_size)
    matching _slice_cube's contract, so downstream compute_panel_view code
    is untouched.

    Cost: ~2-4s for a small district on first hit; sub-second for cached
    repeat views (compute_panel_view wraps this in a per-filter 24h cache).
    Returns (None, 0) if the district's panel is below MIN_CELL_SIZE
    (privacy suppression).
    """
    party = filters.get('party') or 'All'
    district = (filters.get('geo_value') or '').strip().upper()
    lookback = int(filters.get('lookback_days') or DEFAULT_LOOKBACK_DAYS)
    if not district:
        return None, 0

    uids = _panel_uids(party, 'District', district)
    if len(uids) < MIN_CELL_SIZE:
        return None, len(uids)

    # Start-date for card queries is `today - lookback`. Match the format
    # the _card_* helpers expect (they call toDate on this string).
    start_date = (datetime.now(timezone.utc).date() -
                  timedelta(days=lookback)).isoformat()

    # Each card is wrapped in its own try/except so a single upstream
    # failure (e.g. reference.search_text_mapping join hitting the CH
    # 403 INVALID_JOIN_ON_EXPRESSION error we saw on 2026-07-27) can
    # only null out its own card, not the whole district cell. Before
    # this change, one exception aborted the entire live-compute and
    # sent voter_journey to [] alongside everything else.
    def _safe(label: str, fn, default):
        try:
            return fn()
        except Exception as e:
            logger.warning("District live-compute [%s] failed for %s: %s",
                           label, district, e)
            return default

    panel_search      = _safe('search_engines',
                              lambda: _card_search_engines(uids, start_date), [])
    panel_social      = _safe('social_media',
                              lambda: _card_social_media(uids, start_date), [])

    # Synthetic fallback (2026-07-27): if the panel-side query for
    # search engines or social platforms comes back with <3 rows for
    # a district cell, ask an agent for the current published-data
    # market-share breakdown and scale it to the district's panel
    # size. Root cause of the empty result today is that
    # reference.host_mapping has zero rows tagged 'Search Engine/AI'
    # and the 'Social Media' rows don't line up with clickstream's
    # COMMON_NAME, so the panel query filter is effectively empty. A
    # host_mapping fix is Jessie/Ana's job; in the meantime cards
    # never render blank.
    #
    # We DON'T overwrite non-empty panel results — if the panel has
    # even a handful of legit rows, those are more accurate than a
    # web-search synthesis. Only fully-empty (or near-empty)
    # responses trip the fallback. Every synthetic row is stamped
    # `synthetic:true`; the frontend can surface that as a hint.
    if len(panel_search) < 3 or len(panel_social) < 3:
        try:
            from blue_iq_synth_agent import synthesize_shares
            geo_label = _district_synth_label(district)
            if len(panel_search) < 3:
                synth = _safe('synth_search_engines',
                              lambda: synthesize_shares('search_engines',
                                                        geo_label, len(uids)),
                              [])
                if synth:
                    panel_search = synth
            if len(panel_social) < 3:
                synth = _safe('synth_social_media',
                              lambda: synthesize_shares('social_media',
                                                        geo_label, len(uids)),
                              [])
                if synth:
                    panel_social = synth
        except ImportError:
            logger.debug("blue_iq_synth_agent not importable; skipping synth fallback")

    panel_politicians = _safe('top_politicians',
                              lambda: _card_top_politicians(uids, start_date), [])
    panel_articles    = _safe('top_articles',
                              lambda: _card_top_articles(uids, start_date), [])
    panel_turnout     = _safe('turnout_intent',
                              lambda: _card_turnout_intent(uids, start_date),
                              {'panelists': 0, 'sample_queries': []})
    panel_demo        = _safe('demo_crosstab',
                              lambda: _card_demo_crosstab(uids), {})
    panel_top_queries = _safe('top_search_queries',
                              lambda: _fetch_panel_search_queries(uids, start_date, limit=200),
                              [])
    # voter_journey (2026-07-27): live-compute via the two-stage
    # touchpoint→next-visit algorithm scoped to the district's UID
    # set. Runs the same pattern the aggregator uses at cube-build
    # time but scoped, so memory stays bounded even on the 30d
    # window. Adds ~1-3s to the first-hit response for a district;
    # subsequent hits reuse the 24h per-filter cache. Already fails
    # safely to [] internally, but we still route through _safe so
    # any unexpected upstream error surfaces in the logs.
    panel_journey     = _safe('voter_journey',
                              lambda: _card_voter_journey(uids, start_date), [])

    cell = {
        'uid_count':         len(uids),
        'search_engines':    panel_search,
        'social_media':      panel_social,
        'top_politicians':   panel_politicians,
        'top_articles':      panel_articles,
        'turnout':           {
            'panelists':   int(panel_turnout.get('panelists', 0) or 0),
            'sample_urls': list(panel_turnout.get('sample_queries', []) or []),
        },
        'demo':              panel_demo,
        'top_search_queries': panel_top_queries,
        'voter_journey':     panel_journey,
    }
    return cell, len(uids)


def _bucket_search_terms_via_global_map(top_search_queries: list[dict],
                                          issue_buckets_global: list[dict],
                                          *, strict: bool = False) -> list[dict]:
    """Map a cell's top search queries to political-issue buckets using the
    GLOBAL bucket assignments from the cube (no fresh OpenAI call needed).

    strict=False (default): when none of the cell's top queries can be
        mapped to the global bucket map, surface the global buckets
        verbatim so a card never goes blank. SAFE for the single
        national rollup card.

    strict=True: when no mapping is found, return an empty list. REQUIRED
        for per-cell aggregations (e.g. per-state heatmap) — otherwise
        every cell that misses the map would be credited with the exact
        same global counts, producing the "every state has identical 94
        searches for Other Policy" bug.
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
        if strict:
            return []
        # No per-cell mapping found — surface the global buckets instead so
        # the card isn't blank. This is the "small slice" graceful path
        # ONLY appropriate for the single national rollup card. Per-cell
        # aggregations must use strict=True so they don't all inherit the
        # same global counts.
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
        # strict=True: don't fall back to global bucket counts for
        # cells whose top queries miss the map — doing so would credit
        # every such state with the same global counts (= every state
        # shows identical "94 for Other Policy" in the heatmap).
        buckets = _bucket_search_terms_via_global_map(
            panel_top_queries, issue_buckets_global, strict=True)
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
    """Re-rank buckets by blended Trends + panel + news score.

    Trends signal is the BACKBONE (2026-07-06 change — users wanted the
    "Political issue searches" card to reflect what people are actively
    searching for right now per Google Trends, not what the panel logged).
    Panel + news signal are OVERLAYS that nudge order for slices with
    strong first-party chatter or a breaking-news moment.

    Panel signal is read from b['panel_count'] (preserved by
    compute_panel_view when the primary count field is overwritten with
    Trends volume). Falls back to b['count'] for legacy fallback paths
    that never overwrote count.
    """
    if not buckets:
        return buckets
    def _panel_of(b: dict) -> int:
        # Prefer the preserved panel_count. Fall back to count only when
        # panel_count is missing (legacy panel-only fallback path).
        if 'panel_count' in b:
            return int(b.get('panel_count') or 0)
        return int(b.get('count') or 0)

    max_panel = max(_panel_of(b) for b in buckets) or 1
    max_trend = max(int(b.get('trend_score') or 0) for b in buckets) or 1
    max_news  = max(int(b.get('news_count')  or 0) for b in buckets) or 1
    for b in buckets:
        panel_n = _panel_of(b)                  / max_panel
        trend_n = (b.get('trend_score') or 0)   / max_trend
        news_n  = (b.get('news_count')  or 0)   / max_news
        # 70% Trends, 20% panel, 10% news. Trends dominates.
        b['blended_score'] = round(0.70 * trend_n + 0.20 * panel_n + 0.10 * news_n, 4)
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
    #
    # District cuts (2026-07-27) bypass the cube and live-compute their
    # cell from ClickHouse via `_slice_cube -> _compute_district_cell_live`.
    # We still call `_load_cube` for District so `issue_buckets_global`
    # and the National baseline (`nat_cell`) are available.
    cube = _load_cube(int(f.get('lookback_days') or DEFAULT_LOOKBACK_DAYS))
    if f.get('geo_type') == 'District':
        cell, panel_size = _slice_cube(cube or {}, f)
    else:
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
    # Resolve which geo to pass to Google Trends.
    #
    # Google Trends' RSS endpoint supports three geo scopes:
    #   - `US`         → National
    #   - `US-<USPS>`  → State (e.g. `US-CA` California)
    #   - `US-<NNN>`   → Nielsen DMA (e.g. `US-686` Mobile-Pensacola)
    #
    # Congressional district isn't a native scope, but every district
    # sits inside one dominant DMA (per zip_to_congressional_district_119
    # land-area weighting). For District filters we resolve to that
    # dominant DMA — one level finer than the parent state.
    trends_state: Optional[str] = None
    trends_geo_override: Optional[str] = None
    trends_dma_name: Optional[str] = None
    trends_dma_code: Optional[str] = None
    if f['geo_type'] == 'State':
        trends_state = f['geo_value']
    elif f['geo_type'] == 'DMA' and f['geo_value']:
        trends_state = DMA_TO_STATE.get(f['geo_value'])
    elif f['geo_type'] == 'District' and f['geo_value']:
        # District (2026-07-27, refined): pull DMA-level trends when we
        # can resolve the district's dominant DMA. Fall back to state-
        # level otherwise. Also keep `trends_state` populated so GDELT /
        # Wikipedia (which don't understand DMA codes) still work.
        _dref = _load_district_ref()
        _dcode = (f['geo_value'] or '').strip().upper()
        _usps = _dref['district_to_state'].get(_dcode)
        if _usps:
            try:
                from external_signals import _USPS_TO_NAME  # type: ignore
                trends_state = _USPS_TO_NAME.get(_usps, _usps)
            except Exception:
                trends_state = _usps
        _dma = _dref.get('district_to_primary_dma', {}).get(_dcode)
        if _dma and _dma.get('code'):
            trends_dma_code = str(_dma['code'])
            trends_dma_name = str(_dma.get('name') or '')
            trends_geo_override = f"US-{trends_dma_code}"
            log.info("blue_iq: district %s -> DMA %s (%s), trends geo=%s",
                     _dcode, trends_dma_code, trends_dma_name, trends_geo_override)
    external = fetch_all_external(
        state=trends_state,
        lookback_days=f['lookback_days'],
        politician_names=politicians_for_external,
        trends_geo_override=trends_geo_override,
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

    # Card A — issue buckets: PRIMARY source is Google Trends (2026-07-06).
    # Rationale: users want the "Political issue searches" card to reflect
    # what people are actively SEARCHING for per Google Trends, not just
    # what the panel happens to have logged in the lookback window. Panel
    # data now folds in as supplementary evidence (sample_queries per
    # bucket + a secondary weight in the rerank), and the panel is used
    # as a graceful fallback when Trends returns nothing.
    raw_trends_for_buckets = (external or {}).get('google_trends_top') or []
    politicians_for_filter = set(_load_politicians()[:300])
    trends_political_for_buckets = _filter_trends_to_political(
        raw_trends_for_buckets, politicians_for_filter)
    gdelt_for_buckets = (external or {}).get('gdelt_articles') or []

    # Step 1: seed buckets from Trends (+ GDELT) via topical bucketing.
    # _augment_buckets_with_external, called with an empty starter list,
    # emits one row per issue-bucket-that-has-Trends-or-news signal,
    # each pre-populated with trend_score / trend_queries / news_count /
    # news_headlines. Every row is initially flagged external_only=True.
    issue_buckets = _augment_buckets_with_external(
        [], trends_political_for_buckets, gdelt_for_buckets)

    # Step 2: promote trend_score into the primary count / share fields
    # so bucket ordering AND the frontend panel-bar width reflect Trends
    # volume. Un-flag external_only when we have a real Trends signal
    # (the dashed placeholder-bar was designed for "no signal at all",
    # not "Trends signal but no panel").
    total_trend_score = sum((b.get('trend_score') or 0) for b in issue_buckets) or 1
    for b in issue_buckets:
        ts = int(b.get('trend_score') or 0)
        b['panel_count'] = 0  # will be filled from panel_top_queries below
        b['count']       = ts
        b['share']       = round(ts / total_trend_score, 4) if ts > 0 else 0.0
        if ts > 0:
            b['external_only'] = False

    # Step 3: fold panel_top_queries into sample_queries as EVIDENCE.
    # Panel counts also get preserved on b['panel_count'] so the rerank
    # can give panel a secondary weight without letting it dominate.
    if panel_top_queries:
        panel_bucketed = _bucket_search_terms_via_global_map(
            panel_top_queries, issue_buckets_global, strict=True)
        panel_map = {pb['bucket']: pb for pb in panel_bucketed} if panel_bucketed else {}
        for b in issue_buckets:
            pb = panel_map.get(b.get('bucket'))
            if not pb:
                continue
            b['panel_count'] = int(pb.get('count') or 0)
            # Trends terms lead sample_queries (this card is Trends-primary);
            # panel queries follow as first-party evidence.
            trend_qs = b.get('trend_queries') or []
            panel_qs = pb.get('sample_queries') or []
            merged = list(trend_qs)
            seen   = {(q or '').lower() for q in merged}
            for q in panel_qs:
                k = (q or '').lower()
                if k and k not in seen:
                    seen.add(k)
                    merged.append(q)
            if merged:
                b['sample_queries'] = merged[:8]

        # Add panel-only buckets (buckets with panel chatter that Trends
        # / GDELT didn't hit this window) at count=0 so they still show
        # as first-party evidence.
        by_name = {b['bucket']: b for b in issue_buckets}
        for pb in (panel_bucketed or []):
            if pb['bucket'] in by_name:
                continue
            issue_buckets.append({
                'bucket':         pb['bucket'],
                'count':          0,
                'share':          0.0,
                'sample_queries': pb.get('sample_queries') or [],
                'trend':          0.0,
                'trend_score':    0,
                'trend_queries':  [],
                'news_count':     0,
                'news_headlines': [],
                'panel_count':    int(pb.get('count') or 0),
                'external_only':  True,
            })

    # Step 3b: ALWAYS fold in the cube-computed `issue_buckets_global`
    # as a FLOOR so the card shows a full spread of policy issues even
    # when the Trends daily-RSS only surfaces 3-5 political items that
    # collapse into 2-3 unique buckets. issue_buckets_global is the
    # nightly OpenAI-classified panel result; each row already carries
    # a sample_queries exemplar list. We dedupe by bucket name and only
    # ADD new rows — buckets that already have Trends/panel signal keep
    # their scores; buckets with no signal get added with panel_count=0
    # and external_only=True, so they sit at the bottom of the rerank
    # but are still visible to the user. This is the fix for the
    # 2026-07-06 "only 3 buckets showing" regression.
    if issue_buckets_global:
        by_name = {b['bucket']: b for b in issue_buckets}
        for gb in issue_buckets_global:
            name = gb.get('bucket')
            if not name or name in by_name:
                continue
            panel_ct = int(gb.get('count') or 0)
            samples  = list((gb.get('sample_queries') or [])[:8])
            issue_buckets.append({
                'bucket':         name,
                'count':          0,   # Trends-primary: no Trends signal here
                'share':          0.0,
                'sample_queries': samples,
                'trend':          0.0,
                'trend_score':    0,
                'trend_queries':  [],
                'news_count':     0,
                'news_headlines': [],
                'panel_count':    panel_ct,
                'external_only':  True,
            })

    # Step 4: graceful fallback — if Trends returned nothing AND we had
    # no panel data either, fall back to the (pre-2026-07-06) panel-map
    # path so the card never goes empty.
    if not issue_buckets:
        issue_buckets = _bucket_search_terms_via_global_map(
            panel_top_queries, issue_buckets_global)
        for b in issue_buckets:
            b.setdefault('panel_count', int(b.get('count') or 0))
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

    # Card E — articles: blend agent-discovered + panel + GDELT into a
    # single ranked list. Agent provides editorial titles + topic tags
    # for genuinely political stories the panel/GDELT side missed
    # (Hantavirus + College Football were both leaking through pre-filter).
    # Each row carries reach_share (0-1) so the UI renders a percentage.
    try:
        from article_discovery import discover_political_articles
        agent_articles = discover_political_articles(f['geo_type'], f['geo_value']) or []
    except Exception as _e:  # pragma: no cover - defensive
        log.warning("article_discovery unavailable; using panel+GDELT only: %s", _e)
        agent_articles = []
    top_articles = _blend_articles_cube(
        panel_articles,
        external.get('gdelt_articles') or [],
        agent_articles=agent_articles,
        panel_total=panel_size,
    )

    # Card G — issue × geo heatmap: per-state issue panel count, sliced from
    # the cube's per-state cells through the global issue bucket map. Computed
    # at request time so the same cube serves every party filter.
    issue_geo = _compute_issue_geo(cube, issue_buckets_global, party_filter=f['party'])

    # Card T — "Trending in this area right now" (live Google Trends, AI-filtered
    # to political). Geographically scoped via trends_state above; falls back to
    # US-wide when the geo is National or the DMA isn't in our lookup. Surfaces
    # things the panel won't catch yet (e.g. a hot local mayoral race).
    # Human label for the trending card heading.
    #   - National → 'United States'
    #   - State    → state name
    #   - DMA      → parent state (DMA name lives in fineprint)
    #   - District → '<state> <district-code>' (e.g. 'Alabama AL-01')
    #                so the operator can see EXACTLY which district
    #                without needing a parenthetical.
    if f['geo_type'] == 'District' and f['geo_value']:
        trends_state_label = f"{trends_state or ''} {f['geo_value']}".strip() \
            if trends_state else f['geo_value']
    else:
        trends_state_label = trends_state or 'United States'
    raw_trends = (external or {}).get('google_trends_top') or []
    pol_set = set(_load_politicians())
    trending_political = _filter_trends_to_political(raw_trends, pol_set)[:25]
    # Defensive fallback: when the political filter rejects every
    # trending term for a slice (common for small-DMA / off-cycle
    # geos where the trending topics are sports, weather, or local
    # human-interest), surface the raw Google Trends list instead of
    # showing an empty "nothing cleared the filter" placeholder.
    # The card is still labeled "Trending political searches" so we
    # tag fallback rows with `why_political = 'unfiltered'` —
    # frontend treats this as a no-chip row but otherwise renders
    # the term, traffic, and related queries normally.
    used_fallback = False
    if trending_political:
        trending_local = trending_political
    elif raw_trends:
        trending_local = [
            {**row, 'why_political': 'unfiltered'}
            for row in raw_trends[:25]
        ]
        used_fallback = True
    else:
        trending_local = []
    trending_meta = {
        'geo_label':              trends_state_label,
        'geo_type':               f['geo_type'],
        'geo_value':              f['geo_value'],
        'raw_trends_count':       len(raw_trends),
        'kept_after_filter':      len(trending_political),
        'used_fallback':          used_fallback,
        'is_state_local':         trends_state is not None,
        'dma_resolved_via':       (DMA_TO_STATE.get(f['geo_value']) if f['geo_type'] == 'DMA' else None),
        'district_resolved_via':  (trends_state if f['geo_type'] == 'District' else None),
        # DMA-level trends metadata for the District branch. Frontend
        # can use these to render a tiny "· via <DMA name>" suffix, or
        # not — the card heading already carries the district code.
        'trends_dma_code':        trends_dma_code,
        'trends_dma_name':        trends_dma_name,
        'trends_scope':           ('dma' if trends_geo_override else
                                    ('state' if trends_state else 'national')),
    }

    # Overall (unfiltered) top trending searches for this geo. Same
    # Google Trends source as trending_local, but without the political
    # filter — the user gets a 7-day "what's hot here, period" view
    # alongside the political one. Surfaces the broader cultural
    # context a DNC marketer needs (a viral non-political topic in the
    # geo can still inform creative timing / placement). Capped at 10.
    trending_overall = raw_trends[:10] if raw_trends else []
    trending_overall_meta = {
        'geo_label':             trends_state_label,
        'geo_type':              f['geo_type'],
        'geo_value':             f['geo_value'],
        'raw_trends_count':      len(raw_trends),
        'shown':                 len(trending_overall),
        'is_state_local':        trends_state is not None,
        'dma_resolved_via':      (DMA_TO_STATE.get(f['geo_value']) if f['geo_type'] == 'DMA' else None),
        'district_resolved_via': (trends_state if f['geo_type'] == 'District' else None),
        'trends_dma_code':       trends_dma_code,
        'trends_dma_name':       trends_dma_name,
        'trends_scope':          ('dma' if trends_geo_override else
                                   ('state' if trends_state else 'national')),
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

    # Card E "Top observed paths (after an issue)" — agent-researched
    # per-issue journeys. Replaces the prior hardcoded
    # BLUE_IQ_PATH_FOLLOWUPS map that produced identical "Read more
    # political news -> Continued to a left/right opinion piece" rows
    # for every bucket. The agent uses web search + reasoning to
    # produce DISTINCT next-action / follow-up text per issue with
    # realistic shares grounded in public research (Pew, Knight
    # Foundation, Brennan Center, eMarketer). 24h S3 cache by
    # (geo, issue-list hash). Fails open — frontend keeps its old
    # static fallback if agent returns [].
    try:
        from path_discovery import discover_issue_paths
        _issue_names = [b.get('bucket') for b in (issue_buckets or [])[:12]
                          if b and b.get('bucket')]
        # Defensive merge: any bucket that appears in issue_journey_cross
        # but NOT in the top-12 issue_buckets (e.g. the single bucket the
        # thin-cross-signal branch will render) still gets researched, so
        # the frontend's thin-branch agent lookup never misses.
        _cross_names = [c.get('bucket') for c in (issue_journey_cross or [])
                          if c and c.get('bucket')]
        for _name in _cross_names:
            if _name and _name not in _issue_names:
                _issue_names.append(_name)
        issue_paths_agent = discover_issue_paths(
            f['geo_type'], f['geo_value'], _issue_names
        ) if _issue_names else []
    except Exception as _e:  # pragma: no cover - defensive
        log.warning("path_discovery unavailable; UI will use static fallback: %s", _e)
        issue_paths_agent = []

    # Card P "Creative playbook" — per-issue placement + creative
    # recommendation researched by an OpenAI agent with web search.
    # Replaces the prior frontend-only BLUE_IQ_ISSUE_PLAYS static dict
    # that returned the same generic copy ("buy NYT/Fox political news
    # inventory + issue-anchored explainer creative") for every slice
    # regardless of geography or panel behavior. Feeds the agent each
    # bucket + the dominant follow-up destination panelists take after
    # touching the issue (so e.g. an issue whose voters candidate_site
    # gets a direct-response recommendation, while news_dive issues
    # get news-adjacency placements). 24h S3 cache keyed by
    # (geo, sorted issue+dest hash). Fails open — frontend keeps its
    # static BLUE_IQ_ISSUE_PLAYS fallback if the agent returns [].
    try:
        from playbook_discovery import discover_creative_playbook
        _cross_by_issue = {(c.get('bucket') or ''): c
                            for c in (issue_journey_cross or [])}
        # Same defensive merge as the path agent: include any bucket
        # that appears in the cross but not in the top-12 panel
        # buckets, so the thin-cross single-issue UI branch always has
        # an agent-supplied placement + creative recommendation.
        _seen_buckets: set[str] = set()
        _ordered_buckets: list[str] = []
        for _b in (issue_buckets or [])[:12]:
            _name = _b.get('bucket') if _b else None
            if _name and _name not in _seen_buckets:
                _seen_buckets.add(_name)
                _ordered_buckets.append(_name)
        for _name in _cross_by_issue.keys():
            if _name and _name not in _seen_buckets:
                _seen_buckets.add(_name)
                _ordered_buckets.append(_name)
        _playbook_ctx: list[dict] = []
        for _bucket in _ordered_buckets[:12]:
            _cross = _cross_by_issue.get(_bucket) or {}
            _dests = sorted(
                [d for d in (_cross.get('destinations') or [])
                  if d.get('destination') not in (None, '', 'abandoned', 'other')],
                key=lambda d: float(d.get('panelists') or 0),
                reverse=True,
            )
            _dom = _dests[0] if _dests else {}
            _playbook_ctx.append({
                'bucket':         _bucket,
                'dominant_dest':  (_dom.get('destination') or 'news_dive'),
                'dom_share':      float(_dom.get('share') or 0.0),
            })
        playbook_agent = discover_creative_playbook(
            f['geo_type'], f['geo_value'], _playbook_ctx
        ) if _playbook_ctx else []
    except Exception as _e:  # pragma: no cover - defensive
        log.warning("playbook_discovery unavailable; UI will use static fallback: %s", _e)
        playbook_agent = []

    # `top_searches`: the raw top ~30 search queries the filtered panel
    # typed in the window — what the district is ACTUALLY Googling, not
    # bucketed into policy issues. This is the core "what do voters
    # here care about" surface: a candidate can see verbatim that CA-41
    # is Googling "car insurance quotes", "compound interest calculator",
    # "tax brackets 2026" and craft messaging around cost-of-living.
    # `political` flag marks rows that touch policy vocabulary or a
    # known politician name so the UI can offer a "political only"
    # filter.
    top_searches_out = _shape_top_searches(panel_top_queries)

    # Synth top-off: two triggers can fire the agent-fill path.
    #
    #   1. Thin panel (total < 8 rows): small district, sparse-panel
    #      state, or Live-mode 1-day window. Merge ALL agent rows so
    #      the card is never empty.
    #
    #   2. Political drought (political count < 5): typical at
    #      National + State scale, where cost-of-living / commerce
    #      queries ("walmart", "chatgpt", "car insurance quotes")
    #      dominate raw volume and political terms get buried below
    #      the top 30. Without this top-off, the "Political-flavored
    #      only" chip lands on an empty view even though political
    #      search intent obviously exists — it's just deeper in the
    #      long tail than the top 30 reaches. When this fires we
    #      merge in ONLY the political-flagged agent rows (leaving
    #      the real panel-derived cost-of-living rows intact for the
    #      default "All searches" view).
    #
    # Every synth row carries `synthetic: True` + `source_note` so
    # the UI shows a "modeled" pill for transparency.
    panel_row_count       = len(top_searches_out)
    panel_political_count = sum(1 for r in top_searches_out if r.get('political'))
    need_thin_topoff      = panel_row_count < 8
    need_political_topoff = panel_political_count < 5

    if need_thin_topoff or need_political_topoff:
        try:
            from blue_iq_synth_agent import synthesize_top_searches
            geo_label  = _synth_geo_label(f)
            synth_size = max(1000, int(panel_size or 0))  # min 1K so shares scale reasonably
            synth_rows = synthesize_top_searches(geo_label, synth_size)
            if synth_rows:
                have = {(r.get('term') or '').lower() for r in top_searches_out}
                # Thin-panel case: merge everything. Political-drought
                # case: merge only political rows so we don't crowd
                # out the panel's real cost-of-living signal.
                if need_thin_topoff:
                    candidates = synth_rows
                else:
                    candidates = [r for r in synth_rows if r.get('political')]

                # Dampen synth `count` so agent-modeled rows can't
                # unfairly rank above real panel rows in the merged
                # view. Rationale: synth `count` is `share * panel_size`
                # where share reflects the AGENT'S "share of political
                # conversation" — not the search's share of all
                # queries. If a real panel row has 4M panelists on
                # "walmart" and a synth row lands at share=0.20 * 33M
                # panel = 6.6M, the synth would rank above walmart,
                # which massively overstates the political term's
                # actual query volume relative to everyday commerce.
                # Anchor synth counts to the panel distribution's
                # LOWER quartile so they surface in the tail of the
                # "All searches" view but always show when the user
                # switches to "Political-flavored only".
                if candidates and top_searches_out:
                    panel_counts = sorted(int(r.get('count') or 0) for r in top_searches_out)
                    if panel_counts:
                        # 25th percentile of the panel distribution
                        p25 = panel_counts[len(panel_counts) // 4]
                        # Rescale each synth row's count so the max
                        # synth row lands right at p25 and lower-weight
                        # synth rows scale proportionally below it.
                        max_synth_count = max(int(r.get('count') or 0) for r in candidates) or 1
                        for r in candidates:
                            raw = int(r.get('count') or 0)
                            r['count'] = max(1, int(round(raw / max_synth_count * p25)))

                # Merge cap: leave headroom past 30 so we don't exclude
                # ALL synth rows just because the panel already returned
                # 30. Frontend applies `.slice(0, 30)` to the "All
                # searches" view (some synth rows will make the top 30
                # by count and some won't — dampened to p25, they'll
                # land in positions 15-25 of the merged sort). The
                # "Political-flavored only" chip runs `.filter` BEFORE
                # `.slice(0, 30)` so every synth row is visible there.
                MERGE_CAP = 35
                for r in candidates:
                    key = (r.get('term') or '').lower()
                    if key and key not in have:
                        have.add(key)
                        top_searches_out.append(r)
                        if len(top_searches_out) >= MERGE_CAP:
                            break
                # Re-sort so the merged list is still count-descending.
                # Panel rows keep their real counts; synth rows now sit
                # in the lower quartile of the panel distribution,
                # which is the visually-honest position: agent-modeled
                # political intent is smaller volume than the top
                # cost-of-living queries.
                top_searches_out.sort(key=lambda r: -int(r.get('count') or 0))
                logger.info(
                    "top_searches synth-filled for %s|%s: %d panel + %d synth = %d total (thin=%s political_drought=%s)",
                    f['geo_type'], f['geo_value'] or 'National',
                    panel_row_count,
                    len([r for r in top_searches_out if r.get('synthetic')]),
                    len(top_searches_out),
                    need_thin_topoff, need_political_topoff)
        except Exception as e:  # never break the payload on synth failure
            logger.warning("top_searches synth fallback failed: %s", e)

    cards = {
        'issue_buckets':       issue_buckets,
        'top_searches':        top_searches_out,
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
        'issue_paths_agent':   issue_paths_agent,
        'playbook_agent':      playbook_agent,
        'issue_geo':           issue_geo,
        'trending_local':         trending_local,
        'trending_meta':          trending_meta,
        'trending_overall':       trending_overall,
        'trending_overall_meta':  trending_overall_meta,
    }

    # Editorial term rewrites + banned-term scrub. Both run as the
    # FINAL stage of compute_panel_view, after every panel / GDELT /
    # Google Trends / agent payload has been assembled and BEFORE the
    # cards dict is sealed to UI / S3 cache.
    #
    # Order matters:
    #   1. _bq_rewrite_cards relabels phrases in-place (e.g. user
    #      mandate 2026-06-29: "biden impeachment inquiry" ->
    #      "impeachment inquiry"). Rewrites keep the surrounding
    #      content but neutralize the labeling.
    #   2. _bq_scrub_cards drops rows whose primary labels still
    #      contain a banned term (user mandate 2026-06-12:
    #      "government shutdown" anywhere). Runs AFTER rewrites so a
    #      rewrite to a still-banned phrase is still caught.
    cards = _bq_rewrite_cards(cards)
    cards = _bq_scrub_cards(cards)

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

    # Don't cache a payload that came back empty on the two
    # agent-dependent cards. If `top_politicians` or `top_candidates`
    # ended up as [], one of two things happened: the agent hit its
    # 45s wall-clock timeout mid-run, or the OpenAI call errored.
    # Either way the S3 candidate/engaged cache probably DID fill in
    # the trailing seconds — writing this half-empty payload to the
    # outer Blue IQ cache would freeze that empty state for
    # CACHE_TTL_S (~24h) and the frontend's polling loop would keep
    # getting the same empty response. Skip the write; the next
    # request will recompute, hit the now-warm S3 caches, and
    # populate. (2026-07-27)
    outer_cards = payload.get('cards') or {}
    if outer_cards.get('top_politicians') and outer_cards.get('top_candidates'):
        _cache_put(f, payload)
    else:
        log.info("blue_iq: skipping outer cache write for %s|%s (politicians=%d, "
                 "candidates=%d) so next request retries the agent path.",
                 f.get('geo_type'), f.get('geo_value'),
                 len(outer_cards.get('top_politicians') or []),
                 len(outer_cards.get('top_candidates') or []))
    return payload


def _attach_share(rows: list[dict]) -> list[dict]:
    """Given [{name, panelists}, ...] add a 'share' field summing to 1.0."""
    if not rows:
        return []
    total = sum(int(r.get('panelists', 0)) for r in rows) or 1
    return [{**r, 'share': round(int(r.get('panelists', 0)) / total, 4)} for r in rows]


# Political-flavor detection for raw search terms.
#
# Bug we're fixing (2026-07-27): the previous implementation used naive
# substring matching, which false-flagged "ps5 pro price" as political
# because 'ice ' (meant to catch ICE agents) appears inside 'pr**ice **'.
# Same class of bug for 'aca' matching 'vacation', 'trans' matching
# 'transportation', 'union' matching 'union pacific', ' gop' matching
# 'gopro', etc.
#
# Fix: use word-bounded regex and be conservative on any token that's
# ambiguous outside a political phrase. Multi-word phrases are safer
# than short tokens (we prefer 'labor union' over bare 'union', 'ice
# raid' over bare 'ice', 'gun control' over bare 'gun').
_TOP_SEARCH_POLITICAL_PATTERNS: tuple[str, ...] = (
    # Politicians (surnames + short aliases)
    r'trump', r'biden', r'harris', r'obama', r'vance', r'newsom',
    r'desantis', r'aoc', r'ocasio[- ]cortez', r'pelosi', r'mcconnell',
    r'schumer', r'jeffries', r'sanders', r'warren', r'rubio', r'cruz',
    r'haley', r'ramaswamy', r'gaetz', r'greene', r'fetterman',
    r'manchin', r'mike johnson', r'speaker johnson', r'kamala',
    # Offices & elections
    r'president', r'vice president', r'senator', r'senators', r'senate',
    r'congress', r'congressman', r'congresswoman', r'congressional',
    r'governor', r'mayor', r'election', r'elections', r'ballot',
    r'ballots', r'vote', r'votes', r'voter', r'voters', r'voting',
    r'campaign', r'campaigns', r'caucus', r'candidate', r'candidates',
    r'primary election', r'presidential primary', r'midterms?',
    # Parties & ideologies
    r'democrat', r'democrats', r'democratic party', r'republican',
    r'republicans', r'gop', r'libertarian', r'liberal', r'conservative',
    r'progressive', r'maga',
    # Immigration
    r'immigration', r'immigrant', r'immigrants', r'border',
    r'deportation', r'deport', r'asylum', r'ice raid', r'ice agents',
    r'ice arrests?', r'sanctuary city', r'sanctuary cities',
    # Abortion / reproductive
    r'abortion', r'roe v(?:\.|ersus)? wade', r'reproductive rights',
    r'pro[- ]choice', r'pro[- ]life', r'planned parenthood',
    r'abortion pill', r'dobbs decision',
    # Guns
    r'gun control', r'gun rights', r'gun laws?', r'firearm',
    r'firearms', r'second amendment', r'2nd amendment', r'assault weapon',
    r'assault rifle',
    # Climate
    r'climate change', r'climate policy', r'green new deal',
    r'fossil fuel', r'fossil fuels', r'oil drilling', r'paris accord',
    # Entitlements / healthcare policy
    r'medicare', r'medicaid', r'social security', r'obamacare',
    r'affordable care act',
    # Economic policy
    r'inflation', r'stimulus check', r'stimulus checks', r'tariff',
    r'tariffs', r'tax bill', r'tax cut', r'tax cuts', r'tax hike',
    r'tax hikes', r'tax reform', r'minimum wage',
    # Labor
    r'labor union', r'labor unions', r'union strike', r'strike vote',
    r'right to work',
    # Culture / education
    r'affirmative action', r'transgender', r'trans rights',
    r'trans athletes?', r'title ix', r'critical race theory',
    r'book ban', r'book bans', r'school choice',
    # Foreign policy
    r'ukraine (?:war|aid|invasion)', r'israel[- ]gaza', r'israel[- ]hamas',
    r'gaza', r'russia sanctions', r'china tariffs', r'nato',
    r'foreign aid',
    # Judicial
    r'supreme court', r'scotus', r'chief justice',
    # Jan 6 / impeachment
    r'january 6', r'jan\.? 6', r'j6 committee', r'insurrection',
    r'impeachment', r'impeach',
    # Agencies (only when there's political-flavor context;
    # bare 'irs'/'doj'/'fbi' can appear in tax-help searches, so
    # require a modifier)
    r'irs audit', r'doj investigation', r'fbi raid', r'fbi investigation',
    # Voting mechanics
    r'voter id', r'voter fraud', r'voter suppression',
    r'mail[- ]in ballot', r'mail[- ]in ballots', r'polling place',
    r'polling places', r'early voting', r'absentee ballot',
    r'absentee ballots',
    # Debates / civic events
    r'presidential debate', r'vp debate', r'town hall meeting',
    r'inauguration', r'inaugural',
)


_POLITICAL_TERM_RE = re.compile(
    r'\b(?:' + '|'.join(_TOP_SEARCH_POLITICAL_PATTERNS) + r')\b',
    flags=re.IGNORECASE,
)


def _flag_political_term(term: str) -> bool:
    """Word-bounded regex scan — returns True iff `term` contains an
    unambiguously political token or phrase.

    Non-political cost-of-living searches ('car insurance quotes',
    'mortgage rates', 'gas prices near me', 'ps5 pro price') MUST NOT
    light up. Those are economic signal for the campaign, but they're
    not what an operative would call a "political search."

    The chip on the UI is aggressive-looking (blue "POLITICAL" pill), so
    a false-positive here is louder than a false-negative. When in
    doubt, err toward NOT flagging.
    """
    if not term:
        return False
    return bool(_POLITICAL_TERM_RE.search(term))


def _shape_top_searches(panel_top_queries: list[dict],
                          limit: int = 30) -> list[dict]:
    """Turn the raw `[{term, count}]` pull from `_fetch_panel_search_queries`
    into the payload shape the frontend renders as the "Top searches"
    card: `[{term, count, share, political}]` sorted by count desc.

    `political` is a soft flag (see `_flag_political_term`) so the UI
    can offer a "Political only" filter without having to re-run the
    AI bucketer client-side.
    """
    if not panel_top_queries:
        return []
    total = sum(int(r.get('count', 0)) for r in panel_top_queries) or 1
    out: list[dict] = []
    for r in panel_top_queries[:limit]:
        term = (r.get('term') or '').strip()
        if not term:
            continue
        cnt = int(r.get('count', 0))
        out.append({
            'term':      term,
            'count':     cnt,
            'share':     round(cnt / total, 4),
            'political': _flag_political_term(term),
        })
    return out


# ── Strict political-article filter ──────────────────────────────────────
# The panel side (raw URLs panelists visited) and GDELT have no editorial
# filter, so they leak sports (AP Top 25 College Football Poll), human-
# interest (Hantavirus cruise ship), generic homepages (Main Page,
# Entertainment), and SEO bait into the "Top political articles" card.
# We word-bound match against (a) a political vocabulary that must appear
# in the title and (b) a junk vocabulary that auto-rejects regardless.

_POLITICAL_TITLE_PATTERNS = [re.compile(r'\b' + p + r'\b', re.IGNORECASE) for p in [
    'trump', 'biden', 'harris', 'newsom', 'desantis', 'pence', 'sanders',
    'warren', 'pelosi', 'mcconnell', 'schumer', 'johnson', 'jeffries',
    'aoc', 'ocasio', 'manchin', 'sinema', 'rubio', 'cruz', 'gaetz',
    'fetterman', 'vance', 'haley', 'ramaswamy',
    'congress', 'senate', 'senator', 'house', 'representative', 'rep\\.',
    'governor', 'gubernatorial', 'mayor', 'mayoral', 'council',
    'attorney general', 'secretary of state',
    'president', 'presidential', 'white house', 'oval office',
    'supreme court', 'scotus', 'doj', 'justice department', 'fbi',
    'irs', 'sec', 'fcc', 'ftc',
    'democrat', 'democratic', 'republican', 'gop', 'progressive',
    'liberal', 'conservative', 'libertarian', 'green party',
    'election', 'campaign', 'primary', 'caucus', 'ballot', 'polling',
    'voter', 'vote', 'voting', 'registrar',
    'policy', 'legislation', 'bill', 'amendment', 'law',
    'immigration', 'border', 'asylum', 'deportation', 'ice',
    'healthcare', 'medicare', 'medicaid', 'aca', 'affordable care',
    'tax', 'taxes', 'irs', 'tariff',
    'housing', 'rent', 'mortgage', 'hud',
    'gas prices?', 'energy', 'oil', 'opec', 'pipeline',
    'climate', 'environment', 'epa', 'emissions',
    'education', 'student loan', 'school board', 'curriculum',
    'crime', 'police', 'shooting', 'gun', 'firearm', '2nd amendment',
    'foreign policy', 'ukraine', 'russia', 'china', 'israel',
    'palestin', 'iran', 'nato',
    'abortion', 'reproductive', 'roe',
    'civil rights', 'lgbtq', 'transgender', 'race',
    'inflation', 'economy', 'jobs', 'unemployment', 'wage', 'minimum wage',
    'union', 'strike',
    'reform', 'regulation', 'sanction',
    'pac', 'super pac', 'donor', 'fundrais',
    'protest', 'rally', 'march', 'riot',
    'indict', 'lawsuit', 'plea', 'verdict', 'sentencing', 'impeach',
    'whistleblow', 'leak',
    'kamala', 'mike johnson', 'kevin mccarthy', 'liz cheney',
    'lloyd austin', 'antony blinken', 'pete buttigieg',
]]

_JUNK_TITLE_PATTERNS = [re.compile(r'\b' + p + r'\b', re.IGNORECASE) for p in [
    # Sports
    'nfl', 'nba', 'mlb', 'nhl', 'ncaa', 'college football', 'football poll',
    'soccer', 'baseball', 'basketball', 'hockey', 'tennis', 'golf',
    'olympics?', 'fifa', 'super bowl', 'world cup',
    # Entertainment / celeb
    'box office', 'oscars?', 'grammys?', 'emmys?', 'golden globe',
    'taylor swift', 'kardashian', 'kanye', 'kim k', 'beyonce',
    'movie review', 'film review', 'tv show', 'streaming guide',
    # Human-interest / weather / disease
    'hantavirus', 'cruise ship', 'shark attack', 'missing person',
    'hurricane', 'tornado', 'wildfire',
    # Generic / homepage
    'main page', '^entertainment$', '^home$', '^trending$', '^sports$',
    'breaking news', 'top stories', 'live updates',
    # Tech reviews
    'iphone review', 'gadget', 'unboxing',
    # Listicles / clickbait
    'you won\\u2019t believe', 'best deals', 'shop now',
]]

_JUNK_DOMAINS = frozenset([
    'tmz.com', 'people.com', 'usweekly.com', 'eonline.com', 'espn.com',
    'sports.yahoo.com', 'bleacherreport.com', 'cbssports.com',
    'foxsports.com', 'nbcsports.com', 'sbnation.com', 'rotoworld.com',
    'pagesix.com', 'dailymail.co.uk',
])


def _looks_political(title: str, url: str = '', source: str = '') -> bool:
    """Title-and-domain political-article filter. True = keep."""
    if not title:
        return False
    src = (source or '').lower()
    if src in _JUNK_DOMAINS:
        return False
    # Auto-reject on obvious junk vocabulary first.
    for rx in _JUNK_TITLE_PATTERNS:
        if rx.search(title):
            return False
    # Generic stub titles ("Main Page", "Entertainment", single-word
    # category labels) carry no political signal — drop them.
    t = title.strip()
    if len(t) < 12 and t.lower() in {
        'main page', 'home', 'entertainment', 'sports', 'trending',
        'news', 'politics', 'top stories', 'breaking news', 'opinion',
    }:
        return False
    # Require at least one political vocabulary hit.
    for rx in _POLITICAL_TITLE_PATTERNS:
        if rx.search(title):
            return True
    # Ballotpedia / opensecrets / fec / propublica URLs are inherently
    # political even when the title is generic — let them through.
    u = (url or '').lower()
    if any(d in u for d in (
        'ballotpedia.org', 'opensecrets.org', 'fec.gov', 'propublica.org/elections',
        'politico.com', 'thehill.com', 'rollcall.com',
    )):
        return True
    return False


# ── Banned-term scrubber ─────────────────────────────────────────────────
# User mandate 2026-06-12: "don't let government shutdown appear anywhere."
# Acts as a final-stage filter run inside compute_panel_view AFTER every
# panel / GDELT / Google Trends / agent-output has been assembled and
# BEFORE the cards dict ships to the UI / S3 cache. Catches the term no
# matter where it originates (panel-derived top queries, GDELT article
# titles, Google Trends rows, OpenAI agent outputs from path_discovery /
# playbook_discovery / article_discovery / candidate_discovery).
#
# Scrub strategy per surface:
#   - List-of-string fields (sample_queries, top_terms, sample_urls):
#     filter members out item-by-item.
#   - List-of-dict fields with a primary label (top_articles.title,
#     trending_local.term, top_search_queries.query, issue_buckets[i]
#     .sample_queries, issue_geo[i].sample_queries, top_politicians.name,
#     top_candidates.name): drop the entire row when the label hits the
#     banned regex.
#   - Long-form agent fields (path_discovery.next_action /
#     follow_up_action / rationale, playbook_discovery.where_to_buy /
#     creative_direction / rationale): if banned text appears, drop the
#     ENTIRE entry (don't redact mid-string — splicing breaks grammar
#     and the agent's intent). The UI is wired to gracefully fall back
#     when an agent payload is missing.
#
# To extend, add a pattern to BLUE_IQ_BANNED_TERMS. Use raw regex
# fragments (no leading/trailing \b — the helper adds them).
BLUE_IQ_BANNED_TERMS: list[str] = [
    r'government\s+shutdown',
    r'gov(?:[\s.\-]?\s*)shutdown',
    r'shutdown\s+of\s+the\s+(?:federal\s+)?government',
    r'federal\s+government\s+shutdown',
]
_BLUE_IQ_BANNED_RE = re.compile(
    r'(?:' + r'|'.join(BLUE_IQ_BANNED_TERMS) + r')',
    re.IGNORECASE,
)


def _bq_contains_banned(text: object) -> bool:
    """True iff `text` is a non-empty string containing a banned term."""
    if not text or not isinstance(text, str):
        return False
    return bool(_BLUE_IQ_BANNED_RE.search(text))


def _bq_scrub_str_list(items: list) -> list:
    """Filter banned strings out of a list of plain strings. None-safe."""
    if not items:
        return items
    return [it for it in items if not _bq_contains_banned(it)]


def _bq_scrub_dict_list(rows: list[dict], *, label_keys: tuple[str, ...],
                         subarray_keys: tuple[str, ...] = ()) -> list[dict]:
    """Filter a list of dicts.

    A row is dropped when ANY value at `label_keys` contains a banned
    term. Surviving rows have their `subarray_keys` (string-lists)
    scrubbed item-by-item. None-safe.
    """
    if not rows:
        return rows
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if any(_bq_contains_banned(r.get(k)) for k in label_keys):
            continue
        if subarray_keys:
            r = dict(r)
            for sak in subarray_keys:
                if sak in r and isinstance(r[sak], list):
                    r[sak] = _bq_scrub_str_list(r[sak])
        out.append(r)
    return out


# ── Term-rewrite layer ───────────────────────────────────────────────────
# Editorial relabels: rewrite specific phrases in place WITHOUT dropping
# the surrounding content. Used when the underlying topic IS legitimately
# in scope, but the labeling needs to be neutralized / depersonalized.
# Runs BEFORE the banned-term scrubber inside compute_panel_view, so a
# rewrite to a still-banned phrase would still get caught downstream.
#
# Each rule is (pattern, replacement). Patterns are case-insensitive and
# applied via re.sub. To preserve original casing of surrounding text,
# the replacement is treated literally — don't include backrefs unless
# you intend them. Patterns are auto-wrapped with word boundaries so
# "biden impeachment" doesn't accidentally rewrite mid-token.
#
# User mandates:
#   2026-06-29: "biden impeachment inquiry change to just say
#                impeachment inquiry."
#
# To extend, append a (pattern, replacement) tuple below. Use raw regex
# fragments (no leading/trailing \b — the helper adds them).
BLUE_IQ_REWRITES: list[tuple[str, str]] = [
    # Strip the "biden" prefix from impeachment-inquiry mentions. Catches
    # "Biden impeachment inquiry", "Biden's impeachment inquiry",
    # "Biden-impeachment inquiry", etc.
    (r"biden(?:['\u2019]s)?[\s\-]+impeachment\s+inquiry", "impeachment inquiry"),
]
_BLUE_IQ_REWRITE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b' + pat + r'\b', re.IGNORECASE), rep)
    for pat, rep in BLUE_IQ_REWRITES
]


def _bq_rewrite_text(text: object) -> object:
    """Apply all rewrites to a string. Non-strings pass through unchanged."""
    if not text or not isinstance(text, str):
        return text
    out = text
    for rx, rep in _BLUE_IQ_REWRITE_RULES:
        out = rx.sub(rep, out)
    return out


def _bq_rewrite_str_list(items: list) -> list:
    """Apply rewrites to every string in a list. None-safe."""
    if not items:
        return items
    return [_bq_rewrite_text(it) for it in items]


def _bq_rewrite_dict_list(rows: list[dict], *,
                            text_keys: tuple[str, ...] = (),
                            subarray_keys: tuple[str, ...] = ()) -> list[dict]:
    """Apply rewrites to specific text fields + string-list subarrays of
    each dict in `rows`. Returns a fresh list of dicts with the rewrites
    applied (does not mutate inputs).
    """
    if not rows:
        return rows
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        r2 = dict(r)
        for tk in text_keys:
            if tk in r2 and isinstance(r2[tk], str):
                r2[tk] = _bq_rewrite_text(r2[tk])
        for sak in subarray_keys:
            if sak in r2 and isinstance(r2[sak], list):
                r2[sak] = _bq_rewrite_str_list(r2[sak])
        out.append(r2)
    return out


def _bq_rewrite_cards(cards: dict) -> dict:
    """Walk every Blue IQ card surface and apply BLUE_IQ_REWRITES.

    Runs BEFORE the banned-term scrubber. Touches the same surfaces the
    scrubber touches but rewrites text in place instead of dropping
    rows. Idempotent — running it twice is a no-op on already-rewritten
    data (the replacement strings don't match the source patterns).
    """
    if not isinstance(cards, dict):
        return cards

    cards['issue_buckets'] = _bq_rewrite_dict_list(
        cards.get('issue_buckets') or [],
        text_keys=('bucket',),
        subarray_keys=('sample_queries', 'top_terms'),
    )
    cards['top_articles'] = _bq_rewrite_dict_list(
        cards.get('top_articles') or [],
        text_keys=('title', 'description', 'summary'),
        subarray_keys=('topics',),
    )
    cards['trending_local'] = _bq_rewrite_dict_list(
        cards.get('trending_local') or [],
        text_keys=('term', 'query'),
    )
    cards['trending_overall'] = _bq_rewrite_dict_list(
        cards.get('trending_overall') or [],
        text_keys=('term', 'query'),
    )

    # Issue × Geo heatmap cells — rewrite sample queries in place.
    igeo = cards.get('issue_geo') or {}
    if isinstance(igeo, dict):
        rows = igeo.get('rows') or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            buckets = row.get('buckets') or []
            for b in buckets:
                if isinstance(b, dict) and isinstance(b.get('sample_queries'), list):
                    b['sample_queries'] = _bq_rewrite_str_list(b['sample_queries'])
        cards['issue_geo'] = igeo

    cards['top_politicians'] = _bq_rewrite_dict_list(
        cards.get('top_politicians') or [],
        text_keys=('engagement_driver', 'rationale', 'headline'),
        subarray_keys=('engagement_drivers',),
    )
    cards['top_candidates'] = _bq_rewrite_dict_list(
        cards.get('top_candidates') or [],
        text_keys=('rationale', 'headline', 'race'),
    )
    cards['issue_paths_agent'] = _bq_rewrite_dict_list(
        cards.get('issue_paths_agent') or [],
        text_keys=('bucket', 'next_action', 'follow_up_action', 'rationale'),
    )
    cards['playbook_agent'] = _bq_rewrite_dict_list(
        cards.get('playbook_agent') or [],
        text_keys=('bucket', 'where_to_buy', 'creative_direction', 'rationale'),
    )
    cards['issue_journey_cross'] = _bq_rewrite_dict_list(
        cards.get('issue_journey_cross') or [],
        text_keys=('bucket',),
        subarray_keys=('top_terms',),
    )

    tu = cards.get('turnout_intent') or {}
    if isinstance(tu, dict) and isinstance(tu.get('sample_queries'), list):
        tu['sample_queries'] = _bq_rewrite_str_list(tu['sample_queries'])
        cards['turnout_intent'] = tu

    return cards


def _bq_scrub_cards(cards: dict) -> dict:
    """Walk every Blue IQ card and strip banned-term content in place.

    Returns the same dict for convenience. Idempotent — running it
    twice is a no-op on already-clean data.
    """
    if not isinstance(cards, dict):
        return cards

    # Issue buckets (panel-classified policy buckets).
    cards['issue_buckets'] = _bq_scrub_dict_list(
        cards.get('issue_buckets') or [],
        label_keys=('bucket',),
        subarray_keys=('sample_queries', 'top_terms'),
    )

    # Top articles (panel + GDELT + agent blend) — drop by title or url.
    cards['top_articles'] = _bq_scrub_dict_list(
        cards.get('top_articles') or [],
        label_keys=('title', 'url'),
        subarray_keys=('topics',),
    )

    # Trending Google Trends rows — drop by term.
    cards['trending_local'] = _bq_scrub_dict_list(
        cards.get('trending_local') or [],
        label_keys=('term', 'query'),
    )
    cards['trending_overall'] = _bq_scrub_dict_list(
        cards.get('trending_overall') or [],
        label_keys=('term', 'query'),
    )

    # Issue × Geo heatmap cells — drop sample queries that mention it.
    igeo = cards.get('issue_geo') or {}
    if isinstance(igeo, dict):
        rows = igeo.get('rows') or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            buckets = row.get('buckets') or []
            for b in buckets:
                if isinstance(b, dict) and isinstance(b.get('sample_queries'), list):
                    b['sample_queries'] = _bq_scrub_str_list(b['sample_queries'])
        cards['issue_geo'] = igeo

    # Top politicians / candidates — drop only when the NAME is banned
    # (the term shouldn't ever match a person, but be defensive).
    cards['top_politicians'] = _bq_scrub_dict_list(
        cards.get('top_politicians') or [],
        label_keys=('name', 'engagement_driver', 'rationale'),
    )
    cards['top_candidates'] = _bq_scrub_dict_list(
        cards.get('top_candidates') or [],
        label_keys=('name', 'race', 'rationale'),
    )

    # path_discovery rows — drop entry if any agent field mentions it.
    cards['issue_paths_agent'] = _bq_scrub_dict_list(
        cards.get('issue_paths_agent') or [],
        label_keys=('bucket', 'next_action', 'follow_up_action', 'rationale'),
    )

    # playbook_discovery rows — drop entry if any creative field mentions it.
    cards['playbook_agent'] = _bq_scrub_dict_list(
        cards.get('playbook_agent') or [],
        label_keys=('bucket', 'where_to_buy', 'creative_direction', 'rationale'),
    )

    # Issue-journey-cross rows (per-issue destinations) — drop by bucket
    # name; per-destination labels are fixed enum so they can't carry
    # the term.
    cards['issue_journey_cross'] = _bq_scrub_dict_list(
        cards.get('issue_journey_cross') or [],
        label_keys=('bucket',),
        subarray_keys=('top_terms',),
    )

    # Turnout-intent sample queries — list of strings.
    tu = cards.get('turnout_intent') or {}
    if isinstance(tu, dict) and isinstance(tu.get('sample_queries'), list):
        tu['sample_queries'] = _bq_scrub_str_list(tu['sample_queries'])
        cards['turnout_intent'] = tu

    return cards


def _blend_articles_cube(panel_articles: list[dict], gdelt: list[dict],
                          agent_articles: list[dict] | None = None,
                          panel_total: int = 0) -> list[dict]:
    """Merge agent-discovered articles + cube's panel-URL list + GDELT's
    title+image feed into the single ranked Top Articles list.

    Each output row carries a `reach_share` (0-1) so the UI can render
    a percentage instead of raw panelist counts. The share is computed
    against the sum of all surviving rows' "engagement weight" — that's
    panelist count for panel/GDELT rows and a synthesized weight for
    agent rows (agent's interest_score scaled so a top agent pick lands
    at roughly the same magnitude as the top panel-engaged row).

    Filters non-political junk (sports, entertainment, generic
    homepages) on the panel + GDELT side via _looks_political. Agent
    articles are assumed already-filtered upstream (the agent prompt
    is explicit) so we only de-dupe by URL/title against earlier rows.
    """
    by_url: dict[str, dict] = {}

    # 1. GDELT first (carries titles, sources, images, tone).
    for a in (gdelt or []):
        u = (a.get('url') or '').strip()
        if not u:
            continue
        title = a.get('title') or _title_from_url(u)
        source = a.get('source') or ''
        if not _looks_political(title, u, source):
            continue
        by_url[u] = {
            'title':          title,
            'source':         source,
            'url':            u,
            'panelists':      0,
            'tone':           float(a.get('tone') or 0.0),
            'image':          a.get('social_image') or '',
            'source_kind':    'panel',
            'interest_score': 0,
            'topic':          '',
            'summary':        '',
        }

    # 2. Panel URLs (carry panelist counts; title may need synthesis).
    for p in (panel_articles or []):
        u = (p.get('url') or '').strip()
        if not u:
            continue
        title = _title_from_url(u)
        source = p.get('source') or ''
        # Existing GDELT entry was already title-filtered, so re-checking
        # is cheap; for fresh panel rows we filter here.
        if u not in by_url and not _looks_political(title, u, source):
            continue
        if u in by_url:
            by_url[u]['panelists'] = max(by_url[u]['panelists'], int(p.get('panelists', 0)))
        else:
            by_url[u] = {
                'title':          title,
                'source':         source,
                'url':            u,
                'panelists':      int(p.get('panelists', 0)),
                'tone':           0.0,
                'image':          '',
                'source_kind':    'panel',
                'interest_score': 0,
                'topic':          '',
                'summary':        '',
            }

    # 3. Agent-discovered articles. These come pre-filtered by the
    # article_discovery agent prompt. De-dupe by URL (case-insensitive)
    # AND by title (so a panel URL and an agent URL that point at the
    # same story collapse into one row, with the agent's title/summary
    # winning since the agent has clean editorial titles).
    seen_titles_lc = {row['title'].lower(): u for u, row in by_url.items()}
    for a in (agent_articles or []):
        u = (a.get('url') or '').strip()
        if not u:
            continue
        u_lc = u.lower()
        t_lc = (a.get('title') or '').lower()
        # Collapse onto existing row if URL or title matches.
        target_url = None
        if u in by_url:
            target_url = u
        elif u_lc in {k.lower() for k in by_url}:
            target_url = next(k for k in by_url if k.lower() == u_lc)
        elif t_lc in seen_titles_lc:
            target_url = seen_titles_lc[t_lc]
        if target_url:
            row = by_url[target_url]
            row['title']          = a.get('title') or row['title']
            row['source']         = a.get('source') or row['source']
            row['topic']          = a.get('topic') or row['topic']
            row['summary']        = a.get('summary') or row['summary']
            row['interest_score'] = max(int(row.get('interest_score') or 0),
                                          int(a.get('interest_score') or 0))
            row['source_kind']    = 'blended' if row['panelists'] > 0 else 'agent'
        else:
            by_url[u] = {
                'title':          a.get('title') or _title_from_url(u),
                'source':         a.get('source') or '',
                'url':            u,
                'panelists':      0,
                'tone':           0.0,
                'image':          '',
                'source_kind':    'agent',
                'interest_score': int(a.get('interest_score') or 0),
                'topic':          a.get('topic') or '',
                'summary':        a.get('summary') or '',
            }
            seen_titles_lc[t_lc] = u

    # 4. Compute engagement weight per row. Panel-engaged rows use
    # `panelists`. Agent-only rows use a synthesized weight = the median
    # panel weight × (interest_score / 50). This puts a top agent pick
    # (score 100) at 2× the median panel row — high enough to compete
    # for the top of the list, low enough that genuine panel-driven
    # stories still win when they exist.
    rows = list(by_url.values())
    panel_weights = sorted([r['panelists'] for r in rows if r['panelists'] > 0])
    median_panel = panel_weights[len(panel_weights) // 2] if panel_weights else 0
    for r in rows:
        if r['panelists'] > 0:
            r['_weight'] = float(r['panelists'])
        else:
            base = float(median_panel) if median_panel > 0 else 1.0
            r['_weight'] = base * (float(r['interest_score']) / 50.0)

    # 5. Compute reach_share (0-1 normalized within the slice).
    total_weight = sum(r['_weight'] for r in rows) or 1.0
    for r in rows:
        r['reach_share'] = round(r['_weight'] / total_weight, 4)
        r.pop('_weight', None)

    # 6. Belt-and-suspenders: strip stale past-year tokens from title +
    # summary on ALL blended rows (agent already did this pre-cache but
    # GDELT + panel-URL-derived titles don't go through the agent). Also
    # covers legacy S3-cached agent payloads written before the v4 prompt.
    for r in rows:
        if r.get('title'):
            r['title'] = _strip_stale_years(r['title'])
        if r.get('summary'):
            r['summary'] = _strip_stale_years(r['summary'])

    rows.sort(key=lambda a: (-float(a.get('reach_share') or 0),
                              -int(a.get('interest_score') or 0),
                              -abs(a.get('tone', 0.0))))
    return rows[:30]


def _strip_stale_years(s: str) -> str:
    """Remove past-year tokens (< current UTC year) from a headline / summary.

    Keeps current year and forward-looking cycle references ("2026 Senate
    race", "2028 presidential") since those are legitimate labels, not
    stale date markers. Mirrors article_discovery._strip_stale_years and
    the frontend _bqStripStaleYears helper so all three surfaces produce
    identical output.

    Also cleans up the leftover punctuation debris after a year is
    removed: empty parens `()`, empty brackets `[]`, dangling "in and",
    orphan " and " / " , " sequences, doubled whitespace, and trailing
    dash / conjunction fragments. This is what turns
    "Voter rolls purged in Georgia (2024)" into
    "Voter rolls purged in Georgia" rather than the ugly intermediate
    "Voter rolls purged in Georgia ()".
    """
    if not s:
        return s
    current_year = datetime.now(timezone.utc).year

    def _sub(m: 're.Match') -> str:
        y = int(m.group(0))
        return '' if y < current_year else str(y)

    out = re.sub(r'\b(?:19|20)\d{2}\b', _sub, s)
    # Empty bracket / paren pairs left behind by year removal.
    out = re.sub(r'\(\s*\)', '', out)
    out = re.sub(r'\[\s*\]', '', out)
    # Dangling connectors: "in and", "in ,", "and and", ", and", etc.
    out = re.sub(r'\b(?:in|of|from|between|during|since|by)\s+and\b',
                 'and', out, flags=re.IGNORECASE)
    out = re.sub(r'\band\s+and\b', 'and', out, flags=re.IGNORECASE)
    out = re.sub(r',\s*and\b', ' and', out, flags=re.IGNORECASE)
    out = re.sub(r'\s+,', ',', out)
    # Doubled whitespace + punctuation-adjacent whitespace.
    out = re.sub(r'\s+', ' ', out)
    out = re.sub(r'\s+([,.:;)\]!?])', r'\1', out)
    out = re.sub(r'([(\[])\s+', r'\1', out)
    # Trailing / leading connective debris ("… in and", "… of", "and …").
    out = re.sub(r'\b(?:in|of|from|between|during|since|by|and)\s*$',
                 '', out, flags=re.IGNORECASE).rstrip()
    out = re.sub(r'^\s*[\-\u2013\u2014,]\s*', '', out)
    out = re.sub(r'\s*[\-\u2013\u2014,]\s*$', '', out)
    return out.strip()


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
