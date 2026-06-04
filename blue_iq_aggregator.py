"""
blue_iq_aggregator.py — Nightly Blue IQ cube builder.

Replaces the old "compute one filter combo at a time" warm strategy with
a single pass per CARD-shape that GROUP BYs across (party, state) and
(party, dma) at once. CH does ~6 group-bys instead of ~2,500 per-combo
SELECT scans. Output is one ~3-5 MB JSON file in S3 that the dashboard
slices in-process at request time.

Output: s3://dashboard-inputs/blue_iq/aggregates/latest.json

Cube shape:
{
  "version": 1,
  "computed_at": "2026-06-03T08:01:00+00:00",
  "lookback_days": 30,
  "min_cell_size": 100,
  "all_parties": ["All", "Democrat", "Republican", "Independent", "Undecided"],
  "all_states":  [...],
  "all_dmas":    [...],
  "cells": {
      "Democrat|California|":     {...},   # state-level slice
      "Democrat||Los Angeles":    {...},   # DMA-level slice
      "Democrat||":               {...},   # national-per-party
      "All||":                    {...},   # absolute national
      ...
  },
  "issue_buckets_global": [{bucket, count, share, sample_queries}, ...],
  "issue_buckets_by_cell": { "Democrat|California|": [...], ... }
}

Schedule on Hetzner (well AFTER the nightly clickstream ETL completes):

    1 8 * * *  cd /root/finished_codes/bg-webapp && /usr/bin/python3 blue_iq_aggregator.py >> /var/log/blue_iq_aggregator.log 2>&1

That's 08:01 UTC = 12:01 AM US Pacific Standard Time. The ETL job
(sp_nightly_etl_clickhouse.py) typically wraps before 06:00 UTC, leaving
us a clean two-hour window with no contention for the CH server's heavy-
query semaphore.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# Allow running from anywhere — make `import blue_iq` work.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import blue_iq  # type: ignore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger('blue_iq_aggregator')

# Config (env-overridable).
LOOKBACK_DAYS = int(os.environ.get('BLUE_IQ_AGG_LOOKBACK_DAYS', '30'))
MIN_CELL_SIZE = int(os.environ.get('BLUE_IQ_MIN_CELL_SIZE', '100'))
TOP_K         = int(os.environ.get('BLUE_IQ_AGG_TOPK', '20'))     # top-K per card
TOP_K_LONG    = int(os.environ.get('BLUE_IQ_AGG_TOPK_LONG', '60'))  # for politicians/articles
# US adult population for gen-pop projection. Single source of truth; the
# Profile IQ pipeline uses the same constant for Raw/Proj math.
US_GEN_POP    = int(os.environ.get('BLUE_IQ_US_GEN_POP', '329900000'))
S3_BUCKET     = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
PARTY_KEY     = os.environ.get('BLUE_IQ_PARTY_KEY', 'blue_iq/party_imputed/all.json')
# Per-lookback cube keys. The legacy `latest.json` key is also written by
# the default 30d run so older clients keep working during the rollout.
LEGACY_CUBE_KEY = os.environ.get('BLUE_IQ_CUBE_KEY', 'blue_iq/aggregates/latest.json')


def cube_key_for_lookback(lookback_days: int) -> str:
    return f"blue_iq/aggregates/cube_{lookback_days}d.json"


def _ch():
    try:
        from clickhouse_connector import connect_clickhouse  # type: ignore
    except ImportError:
        from migration.clickhouse_connector import connect_clickhouse  # type: ignore
    return connect_clickhouse()


# USPS code -> full state name. We import lazily so this file stays usable
# even if external_signals.py changes; the map is used to expand the
# `PROVINCE` 2-letter codes in user_data_sanitized to the human-readable
# names the dashboard's filter dropdown uses ("California", not "CA").
def _usps_to_name_pairs() -> list[tuple[str, str]]:
    try:
        from external_signals import _USPS_TO_NAME  # type: ignore
    except ImportError:
        from .external_signals import _USPS_TO_NAME  # type: ignore
    return list(_USPS_TO_NAME.items())


# US-only filter applied to every user_data_sanitized read. COUNTRY is
# messy too — 'USA', 'United States', 'US' all mean US; everything else
# (including the leaked education/school strings like
# 'Complete College/University') gets rejected. Without this filter the
# search-queries card surfaces UK '+uk' terms, Russian Cyrillic, Indian
# '.gov.in', and Mexican 'infonavit' results that shouldn't be in a US
# political dashboard.
US_COUNTRY_FILTER = "uds.COUNTRY IN ('USA','United States','US','U.S.','U.S.A.')"


def _ch_state_transform_expr(province_col_sql: str) -> str:
    """Return a CH SQL expression that NORMALIZES the messy PROVINCE column
    to a clean US state name, OR an empty string if the value isn't a
    recognized US state.

    PROVINCE in user_data_sanitized contains a mix of:
      * USPS 2-letter codes ('CA', 'TX')      → expand to 'California'
      * Full US state names ('California')    → keep as-is
      * Canadian provinces ('ON', 'AB')        → reject (return '')
      * Military codes ('AA', 'AE', 'AP')      → reject
      * Garbage / empty ('Province', '')        → reject

    Only rows that map to a known US state survive the (party, state)
    grouping; everything else gets state='' which means "no state cell".
    """
    pairs = _usps_to_name_pairs()
    keys     = "[" + ",".join(f"'{k}'" for k, _ in pairs) + "]"   # USPS codes
    vals     = "[" + ",".join(f"'{v}'" for _, v in pairs) + "]"   # full names
    full_set = "[" + ",".join(f"'{v}'" for _, v in pairs) + "]"   # set of allowed full names
    # Step 1: transform USPS -> name, leaving original on miss.
    # Step 2: if the result is NOT in the full-names allowlist, blank it.
    return (f"if({province_col_sql} IN {full_set}, {province_col_sql}, "
            f"if(transform({province_col_sql}, {keys}, {vals}, '') != '', "
            f"transform({province_col_sql}, {keys}, {vals}, ''), ''))")


def _start_date(lookback: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime('%Y-%m-%d')


def _cell_key(party: str, state: str, dma: str) -> str:
    return f"{party or ''}|{state or ''}|{dma or ''}"


def _route_grouping_row(party, state, dma,
                         gp: int, gs: int, gd: int) -> Optional[str]:
    """Given a row from a GROUPING SETS aggregation tagged with CH's
    `grouping(col)` flags (1 = column rolled up / not in this set,
    0 = column actively grouped), produce a single cell-key string
    OR None if the row should be skipped.

    Routing matrix:
      gp gs gd | meaning                       | cell key
      ---------|-------------------------------|----------------------
      1  1  1  | () empty grouping = total     | All||
      0  1  1  | (party) only                  | {party}||
      0  0  1  | (party, state)                | {party}|{state}|
      0  1  0  | (party, dma)                  | {party}||{dma}
      anything else                            | skip

    For rows where state/dma is grouped but the underlying value is
    empty (e.g. a non-US province that got blanked by the normalizer),
    we skip — those would collide with the rolled-up cell otherwise.
    """
    if gp == 1 and gs == 1 and gd == 1:
        return "All||"
    p = party or 'All'
    if gp == 0 and gs == 1 and gd == 1:
        return f"{p}||"
    if gp == 0 and gs == 0 and gd == 1:
        s = (state or '').strip()
        return f"{p}|{s}|" if s else None
    if gp == 0 and gs == 1 and gd == 0:
        d = (dma or '').strip()
        return f"{p}||{d}" if d else None
    return None


# ── Step 1: Party imputer (with bulk INSERT into a per-session temp table) ──

def _try_reuse_party_map_from_s3(max_age_days: int = 7) -> Optional[dict[str, str]]:
    """If `s3://dashboard-inputs/blue_iq/party_imputed/all.json` exists and
    is < max_age_days old, load it and return a {uid: party} dict.
    Otherwise return None so the caller does a fresh CH scan.
    """
    try:
        from app import s3_client  # type: ignore
    except Exception:
        import boto3
        s3_client = boto3.client('s3', region_name='us-east-2')
    try:
        head = s3_client.head_object(Bucket=S3_BUCKET, Key=PARTY_KEY)
        last_mod = head.get('LastModified')
        if last_mod:
            age_days = (datetime.now(timezone.utc) - last_mod.astimezone(timezone.utc)).days
            if age_days > max_age_days:
                return None
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=PARTY_KEY)
        payload = json.loads(obj['Body'].read().decode('utf-8'))
        return {uid: (v.get('party') if isinstance(v, dict) else v)
                for uid, v in payload.items()
                if (v.get('party') if isinstance(v, dict) else v)}
    except Exception:
        return None


def _build_party_temp_table(conn, lookback: int) -> dict[str, str]:
    """Run the heuristic party imputer over recent panelists, INSERT the
    resulting (uid, party) map into a session-scoped temp table the rest
    of the aggregator can JOIN against.

    Optimizations:
      * Tries to reuse a recently-computed party map from S3 first (if it
        exists and is < 7 days old) — avoids the heavy CH scan entirely.
      * Uses multiMatchAny() (Hyperscan SIMD) instead of N OR'd position()
        calls — ~10x faster on the politician-name predicate.
      * No ORDER BY (we group in Python).

    Returns the {uid: party} dict (also persisted to S3).
    """
    polparties = blue_iq._load_politician_parties()
    _, left_media, right_media = blue_iq._load_media_domains()
    rel_domains = list((left_media | right_media | {
        'actblue.com', 'dccc.org', 'democrats.org', 'winred.com', 'nrcc.org', 'gop.com',
    }))
    pol_names = [n.lower() for n in list(polparties.keys())[:50]]

    # Fast path: reuse the existing party map if it's fresh enough.
    party_map = _try_reuse_party_map_from_s3(max_age_days=7)
    if party_map:
        log.info("  party imputer: reusing %d-UID map from s3 (fresh enough, skipping CH scan)",
                 len(party_map))
    else:
        log.info("  party imputer: scanning %d-day clickstream window (domain-only predicate) ...",
                 lookback)
        start = _start_date(lookback)
        cur = conn.cursor()
        # Domain-only predicate is dramatically faster than OR'ing with a
        # URL-substring politician match. Skipping indexes on DOMAIN keep
        # this scan to minutes instead of hours. We lose ~10% of signal
        # (politician URL exposure for visitors of non-political domains)
        # but the donor + lean-media signal is the dominant party indicator.
        cur.execute("""
            SELECT UID, lower(COMMON_NAME), lower(DOMAIN), URL
            FROM clickstream.clickstream_final
            WHERE DELIVERED >= toDate(%(start)s)
              AND lower(DOMAIN) IN %(rel)s
        """, {'start': start, 'rel': rel_domains})
        rows = cur.fetchall()
        log.info("  party imputer: scored %d political rows", len(rows))

        by_uid: dict[str, list[tuple]] = defaultdict(list)
        for r in rows:
            by_uid[r[0]].append((r[1], r[2], r[3]))

        party_map = {}
        for uid, urows in by_uid.items():
            party, _conf = blue_iq._score_party_from_rows(urows, polparties, left_media, right_media)
            party_map[uid] = party
        counts: Counter = Counter()
        for p in party_map.values():
            counts[p] += 1
        log.info("  party imputer breakdown (fresh scan): %s", dict(counts))

    cur = conn.cursor()
    # Create session-scoped temp table.
    cur.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS blue_iq_party
        (uid String, party LowCardinality(String))
        ENGINE = Memory
    """)
    cur.execute("TRUNCATE TABLE blue_iq_party")

    # Bulk insert via clickhouse-connect's raw client (much faster than per-row INSERTs).
    try:
        raw_client = getattr(conn, '_client', None) or getattr(conn, 'client', None)
        if raw_client is not None:
            data = [(uid, p) for uid, p in party_map.items()]
            raw_client.insert('blue_iq_party', data, column_names=['uid', 'party'])
        else:
            raise AttributeError("no raw client on connection")
    except Exception as e:
        log.warning("  bulk insert via raw client failed (%s); falling back to chunked INSERT VALUES", e)
        # Fallback: chunked INSERT VALUES
        items = list(party_map.items())
        for i in range(0, len(items), 5000):
            chunk = items[i:i+5000]
            values_clause = ','.join(f"('{uid}','{p}')" for uid, p in chunk)
            cur.execute(f"INSERT INTO blue_iq_party (uid, party) VALUES {values_clause}")
    log.info("  party imputer: temp table loaded with %d UIDs", len(party_map))

    # Persist to S3 for the live dashboard's degraded-fallback path AND so
    # the next aggregator run can skip the heavy CH scan via the S3-reuse
    # fast path. Use boto3 directly — `from app import s3_client` requires
    # Flask which isn't installed in headless cron contexts.
    try:
        import boto3
        s3_client = boto3.client('s3', region_name='us-east-2')
        payload = {
            uid: {'party': p, 'computed_at': datetime.now(timezone.utc).isoformat()}
            for uid, p in party_map.items()
        }
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=PARTY_KEY,
            Body=json.dumps(payload).encode('utf-8'),
            ContentType='application/json',
        )
        log.info("  party imputer: persisted %d UIDs to s3://%s/%s",
                 len(payload), S3_BUCKET, PARTY_KEY)
    except Exception as e:
        log.warning("  party imputer: S3 persist failed (non-fatal): %s", e)

    return party_map


# ── Step 2: Per-card GROUP BYs ──────────────────────────────────────────────

# COMMON_NAME values in clickstream are lowercase. These brand sets are
# curated rather than pulled from reference.host_mapping because the big
# infrastructure brands (Google, Bing, DuckDuckGo) have empty CATEGORY
# strings in the hostmap so a category-based lookup misses them entirely.
SEARCH_ENGINE_BRANDS = [
    'google', 'bing', 'yahoo', 'duckduckgo', 'brave search', 'ecosia',
    'startpage', 'qwant', 'kagi',
    'chatgpt', 'claude', 'gemini', 'perplexity', 'copilot', 'you.com',
]
SOCIAL_MEDIA_BRANDS = [
    'facebook', 'instagram', 'x', 'twitter', 'tiktok', 'linkedin',
    'pinterest', 'snapchat', 'reddit', 'threads', 'bluesky', 'mastodon',
    'youtube', 'tumblr', 'discord', 'whatsapp', 'telegram', 'truth social',
]

_BRAND_SETS = {
    'Search Engine/AI': SEARCH_ENGINE_BRANDS,
    'Social Media':     SOCIAL_MEDIA_BRANDS,
}


def _q_top_by_cat(conn, start: str, category: str, top_k: int) -> dict[str, list[dict]]:
    """One query that returns top-K COMMON_NAME by uniqExact(UID), for every
    (party, geo) cell. Cells are emitted at four granularities at once via
    GROUPING SETS: (party, state), (party, dma), (party), ().

    Returns: {cell_key: [{name, panelists}, ...]}
    """
    cur = conn.cursor()
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    brand_list = _BRAND_SETS.get(category) or []
    if not brand_list:
        log.warning("  no curated brand list for category=%s — skipping", category)
        return {}
    cur.execute(f"""
        WITH base AS (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                {state_expr}                    AS state,
                uds.DMA                         AS dma,
                lower(cs.COMMON_NAME)           AS cn,
                cs.UID                          AS uid
            FROM clickstream.clickstream_final     AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND lower(cs.COMMON_NAME) IN %(brands)s
              AND {US_COUNTRY_FILTER}
        )
        SELECT party, state, dma, cn,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid)  AS panelists
        FROM base
        GROUP BY GROUPING SETS (
            (party, state, cn),
            (party, dma, cn),
            (party, cn),
            (cn)
        )
        HAVING panelists > 0
    """, {'brands': brand_list, 'start': start})

    # Two-level dedupe: a cell can receive multiple rows for the same brand
    # if COMMON_NAME has invisible whitespace variants (e.g. 'google' vs
    # 'google\xa0'). Group by normalized name within each cell and take MAX.
    by_cell_brand: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for party, state, dma, cn, gp, gs, gd, panelists in cur.fetchall():
        if not cn:
            continue
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        norm = ' '.join(cn.strip().lower().split())  # collapse whitespace
        display = ' '.join(w.capitalize() for w in norm.split())
        by_cell_brand[cell][display] = max(by_cell_brand[cell][display], int(panelists))

    out: dict[str, list[dict]] = {}
    for cell, brand_map in by_cell_brand.items():
        items = sorted(brand_map.items(), key=lambda x: -x[1])
        out[cell] = [{'name': n, 'panelists': p} for n, p in items[:top_k]]
    return out


def _q_cell_sizes(conn) -> dict[str, int]:
    """uniqExact(UID) per cell — used for cell suppression + denominators."""
    cur = conn.cursor()
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    cur.execute(f"""
        SELECT party, state, dma,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid) AS panelists
        FROM (
            SELECT coalesce(bp.party, 'Undecided') AS party,
                   {state_expr}                    AS state,
                   uds.DMA                         AS dma,
                   uds.UID                         AS uid
            FROM userdata.user_data_sanitized AS uds
            LEFT JOIN blue_iq_party AS bp ON bp.uid = uds.UID
            WHERE {US_COUNTRY_FILTER}
        )
        GROUP BY GROUPING SETS (
            (party, state),
            (party, dma),
            (party),
            ()
        )
        HAVING panelists > 0
    """)
    out: dict[str, int] = {}
    for party, state, dma, gp, gs, gd, panelists in cur.fetchall():
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        out[cell] = int(panelists)
    return out


def _q_demos(conn) -> dict[str, dict[str, list[dict]]]:
    """Per-cell demographic breakdown (no clickstream needed — pure user_data_sanitized)."""
    cur = conn.cursor()
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    for col, label in [('AGE', 'age'), ('GENDER', 'gender'),
                       ('ETHNICITY', 'ethnicity'), ('INCOME', 'income')]:
        cur.execute(f"""
            SELECT party, state, dma, val,
                   grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
                   count() AS n
            FROM (
                SELECT coalesce(bp.party, 'Undecided') AS party,
                       {state_expr}                    AS state,
                       uds.DMA                         AS dma,
                       uds.{col}                       AS val
                FROM userdata.user_data_sanitized AS uds
                LEFT JOIN blue_iq_party AS bp ON bp.uid = uds.UID
                WHERE uds.{col} IS NOT NULL AND uds.{col} != ''
                  AND {US_COUNTRY_FILTER}
            )
            GROUP BY GROUPING SETS (
                (party, state, val),
                (party, dma, val),
                (party, val)
            )
            HAVING n > 0
        """)
        bucket_by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for party, state, dma, val, gp, gs, gd, n in cur.fetchall():
            cell = _route_grouping_row(party, state, dma, gp, gs, gd)
            if cell is None or not val:
                continue
            bucket_by_cell[cell].append((val, int(n)))
        for cell, items in bucket_by_cell.items():
            items.sort(key=lambda x: -x[1])
            total = sum(n for _, n in items) or 1
            out[cell][label] = [{
                'value':     v,
                'panelists': n,
                'share':     round(n / total, 4),
            } for v, n in items[:12]]
    return out


def _q_turnout(conn, start: str) -> dict[str, dict]:
    """Per-cell turnout-intent panelists + a few sample matched URLs."""
    cur = conn.cursor()
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    cur.execute(f"""
        WITH matched AS (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                {state_expr}                    AS state,
                uds.DMA                         AS dma,
                cs.UID                          AS uid,
                lower(cs.URL)                   AS u
            FROM clickstream.clickstream_final AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND multiMatchAny(lower(cs.URL), %(terms)s) > 0
              AND {US_COUNTRY_FILTER}
        )
        SELECT party, state, dma,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid) AS panelists, groupUniqArray(20)(u) AS samples
        FROM matched
        GROUP BY GROUPING SETS (
            (party, state),
            (party, dma),
            (party),
            ()
        )
        HAVING panelists > 0
    """, {'start': start, 'terms': blue_iq._TURNOUT_PATTERNS})

    out: dict[str, dict] = {}
    for party, state, dma, gp, gs, gd, panelists, samples in cur.fetchall():
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        out[cell] = {
            'panelists':    int(panelists),
            'sample_urls':  list(samples or [])[:8],
        }
    return out


def _q_politicians(conn, start: str, top_k: int) -> dict[str, list[dict]]:
    """Per-cell politician URL-match counts."""
    cur = conn.cursor()
    politicians = blue_iq._load_politicians()[:60]
    if not politicians:
        return {}
    # We build a positional-match clause per politician inline; CH evaluates
    # multiMatchAllIndices over a constant pattern array which gives us the
    # index of each match per row — that's how we know WHICH politician hit.
    patterns = [n.lower() for n in politicians]
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    cur.execute(f"""
        WITH hits AS (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                {state_expr}                    AS state,
                uds.DMA                         AS dma,
                cs.UID                          AS uid,
                arrayJoin(multiMatchAllIndices(lower(cs.URL), %(pats)s)) AS pol_idx
            FROM clickstream.clickstream_final AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND multiMatchAny(lower(cs.URL), %(pats)s) > 0
              AND {US_COUNTRY_FILTER}
        )
        SELECT party, state, dma, pol_idx,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid) AS panelists
        FROM hits
        GROUP BY GROUPING SETS (
            (party, state, pol_idx),
            (party, dma, pol_idx),
            (party, pol_idx),
            (pol_idx)
        )
        HAVING panelists > 0
    """, {'pats': patterns, 'start': start})

    by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for party, state, dma, idx, gp, gs, gd, panelists in cur.fetchall():
        try:
            name = politicians[int(idx) - 1]  # CH multiMatchAllIndices is 1-based
        except (IndexError, TypeError, ValueError):
            continue
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        by_cell[cell].append((name, int(panelists)))

    out: dict[str, list[dict]] = {}
    for cell, items in by_cell.items():
        items.sort(key=lambda x: -x[1])
        out[cell] = [{'name': n, 'panelists': p} for n, p in items[:top_k]]
    return out


def _q_articles(conn, start: str, top_k: int) -> dict[str, list[dict]]:
    """Per-cell political article URL counts (panel-side only — GDELT layers in at request time)."""
    cur = conn.cursor()
    domains_all, _, _ = blue_iq._load_media_domains()
    if not domains_all:
        return {}
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    cur.execute(f"""
        SELECT party, state, dma, url, dom,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid) AS panelists
        FROM (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                {state_expr}                    AS state,
                uds.DMA                         AS dma,
                cs.URL                          AS url,
                lower(cs.DOMAIN)                AS dom,
                cs.UID                          AS uid
            FROM clickstream.clickstream_final AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND lower(cs.DOMAIN) IN %(doms)s
              AND length(cs.URL) > 30
              AND {US_COUNTRY_FILTER}
        )
        GROUP BY GROUPING SETS (
            (party, state, url, dom),
            (party, dma, url, dom),
            (party, url, dom),
            (url, dom)
        )
        HAVING panelists >= 2
    """, {'doms': list(domains_all), 'start': start})

    by_cell: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for party, state, dma, url, dom, gp, gs, gd, panelists in cur.fetchall():
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        by_cell[cell].append((url, dom, int(panelists)))

    out: dict[str, list[dict]] = {}
    for cell, items in by_cell.items():
        items.sort(key=lambda x: -x[2])
        out[cell] = [{'url': u, 'source': d, 'panelists': p}
                     for u, d, p in items[:top_k]]
    return out


def _q_search_queries(conn, start: str, top_k: int) -> tuple[dict[str, list[dict]], list[dict]]:
    """Per-cell top search terms via ANY INNER JOIN to reference.search_text_mapping,
    plus a GLOBAL (national, all-party) flat list to feed the AI bucketer.
    """
    cur = conn.cursor()
    state_expr = _ch_state_transform_expr("uds.PROVINCE")
    # Extract the search query directly from the URL's standard `q=`
    # parameter (covers Google, Bing, DuckDuckGo, Yahoo Search, etc.).
    # Decoded, lowercased, and bounded between 6 and 200 chars. Avoids
    # the heavy `ANY INNER JOIN reference.search_text_mapping` join that
    # the newer CH analyzer rejects (`INVALID_JOIN_ON_EXPRESSION` on
    # non-equality JOIN conditions).
    cur.execute(f"""
        WITH hits AS (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                {state_expr}                    AS state,
                uds.DMA                         AS dma,
                cs.UID                          AS uid,
                lower(decodeURLComponent(extractURLParameter(cs.URL, 'q'))) AS term
            FROM clickstream.clickstream_final AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND length(extractURLParameter(cs.URL, 'q')) BETWEEN 6 AND 200
              AND {US_COUNTRY_FILTER}
        )
        SELECT party, state, dma, term,
               grouping(party) AS gp, grouping(state) AS gs, grouping(dma) AS gd,
               uniqExact(uid) AS panelists
        FROM hits
        WHERE term != ''
          -- ASCII-only: reject Cyrillic, '+uk' UK-localized terms, Devanagari, etc.
          AND match(term, '^[\\x20-\\x7e]+$')
          AND positionCaseInsensitive(term, '+uk') = 0
          AND positionCaseInsensitive(term, '+india') = 0
          AND positionCaseInsensitive(term, '+canada') = 0
          AND positionCaseInsensitive(term, '+australia') = 0
          AND positionCaseInsensitive(term, '.gov.in') = 0
          AND positionCaseInsensitive(term, '.gov.uk') = 0
          AND positionCaseInsensitive(term, '.co.uk') = 0
          AND positionCaseInsensitive(term, 'infonavit') = 0
        GROUP BY GROUPING SETS (
            (party, state, term),
            (party, dma, term),
            (party, term),
            (term)
        )
        HAVING panelists >= 2
    """, {'start': start})

    by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
    global_terms: Counter = Counter()
    for party, state, dma, term, gp, gs, gd, panelists in cur.fetchall():
        if not term:
            continue
        cell = _route_grouping_row(party, state, dma, gp, gs, gd)
        if cell is None:
            continue
        by_cell[cell].append((term, int(panelists)))
        # Absolute-national row = ALL dims rolled up
        if gp == 1 and gs == 1 and gd == 1:
            global_terms[term] += int(panelists)
    out: dict[str, list[dict]] = {}
    for cell, items in by_cell.items():
        items.sort(key=lambda x: -x[1])
        out[cell] = [{'term': t, 'count': p} for t, p in items[:top_k * 5]]  # keep more — buckets need raw

    # Global list capped at 8000 to keep AI classifier cost bounded.
    global_list = [{'term': t, 'count': n}
                   for t, n in sorted(global_terms.items(), key=lambda x: -x[1])[:8000]]
    return out, global_list


# ── Step 3: AI rollup of global search terms into political-issue buckets ──

def _global_bucket_map(global_terms: list[dict]) -> dict[str, str]:
    """Returns {normalized_query: bucket_name} learned from the global term list."""
    if not global_terms:
        return {}
    bucketed = blue_iq.roll_up_political_issues(global_terms, use_external=False)
    # Map each query back to its bucket by scanning the bucket samples.
    # roll_up_political_issues returned aggregated buckets with sample_queries;
    # but we sent in the full term list with counts, so we need to redo the
    # per-term assignment. Simplest: re-run the lookup against the bucket
    # samples (each bucket holds up to 10 samples). For high-volume terms
    # not in samples, we don't have a mapping — they fall through as
    # "Other Policy" at slice time.
    qmap: dict[str, str] = {}
    for b in bucketed:
        for q in b.get('sample_queries', []):
            qmap[q.strip().lower()] = b['bucket']
    return qmap


# ── Step 3b: Digital Voter Journey ──────────────────────────────────────────

# Destination categories for the post-touchpoint visit. Each one represents
# a clear action a voter took after encountering political content.
JOURNEY_CANDIDATE_DOMAINS = {
    # Trump ecosystem
    'donaldjtrump.com', 'trump.com', 'truthsocial.com', 'rnc.org',
    # Harris/Biden ecosystem
    'kamalaharris.com', 'joebiden.com', 'whitehouse.gov',
    'democrats.org', 'dnc.org',
    # Major candidate / officeholder sites
    'berniesanders.com', 'aoc.house.gov', 'warren.senate.gov',
    'cruz.senate.gov', 'rubio.senate.gov', 'tedcruz.org',
}
JOURNEY_DONATION_DOMAINS = {
    'actblue.com', 'winred.com', 'secure.actblue.com', 'secure.winred.com',
    'givebutter.com', 'classy.org',
}
JOURNEY_VOTING_INFO_DOMAINS = {
    'vote.gov', 'usa.gov', 'ballotpedia.org', 'rockthevote.org', 'rockthevote.com',
    'iwillvote.com', 'turbovote.org', 'eac.gov', 'votersedge.org',
    'fec.gov', 'opensecrets.org',
}
JOURNEY_SEARCH_DOMAINS = {
    'google.com', 'bing.com', 'duckduckgo.com', 'yahoo.com',
    'chatgpt.com', 'chat.openai.com', 'gemini.google.com',
    'perplexity.ai', 'claude.ai',
}
JOURNEY_SOCIAL_DOMAINS = {
    'reddit.com', 'twitter.com', 'x.com', 'facebook.com', 'instagram.com',
    'tiktok.com', 'threads.net', 'youtube.com', 'linkedin.com',
}


def _q_voter_journey(conn, start: str) -> dict[str, list[dict]]:
    """For panelists who encountered a political touchpoint in the window,
    what did they DO next?

    Touchpoint = visit to a political-media domain OR a URL containing a
    politician name. We then ASOF-LEFT-JOIN to the same panelist's next
    clickstream visit (any URL, within the same lookback window), and
    categorize that next visit into a destination bucket:

        candidate_site, candidate_social, search, news_dive,
        voting_info, donation, social_discussion, other, abandoned

    Returns cell_key → list of {destination, panelists, share} for the
    All|| and {party}|| cells only (this card is national / party-level;
    the per-state/DMA breakouts are too sparse on a 1d window).
    """
    cur = conn.cursor()
    _, left_media, right_media = blue_iq._load_media_domains()
    media_domains = list(left_media | right_media)
    polparties = blue_iq._load_politician_parties()
    pol_pats = [n.lower() for n in list(polparties.keys())[:60]]  # cap at 60 for hyperscan

    if not media_domains and not pol_pats:
        return {}

    candidate_set       = sorted(JOURNEY_CANDIDATE_DOMAINS)
    donation_set        = sorted(JOURNEY_DONATION_DOMAINS)
    voting_info_set     = sorted(JOURNEY_VOTING_INFO_DOMAINS)
    search_set          = sorted(JOURNEY_SEARCH_DOMAINS)
    social_set          = sorted(JOURNEY_SOCIAL_DOMAINS)
    political_media_set = sorted({d.lower() for d in media_domains})

    # `journey_step` categorizes the NEXT visit. We layer the predicates
    # so candidate-social (e.g. twitter.com/realDonaldTrump) beats raw
    # social. Order matters: first match wins via multiIf().
    #
    # MEMORY: the prior implementation ASOF-joined against the full
    # clickstream_final on the right side. CH OOM'd at 80GiB because the
    # right-side scan wasn't pruned. We now:
    #   1. Materialize the unique touchpoint UIDs in a CTE.
    #   2. Restrict the right side to ONLY those UIDs via `WHERE c2.UID IN`.
    #      That prunes the scan to a few thousand panelists' rows instead of
    #      all ~180M-300M rows in the window.
    #   3. We also sample the touchpoints to the first PER-UID timestamp so
    #      each panelist contributes one journey row, not N (where N = #
    #      touchpoint visits they made).
    cur.execute(f"""
        WITH touchpoints_raw AS (
            SELECT
                coalesce(bp.party, 'Undecided') AS party,
                cs.UID                          AS uid,
                cs.VISIT_TS                     AS tp_ts
            FROM clickstream.clickstream_final     AS cs
            INNER JOIN userdata.user_data_sanitized AS uds ON uds.UID = cs.UID
            LEFT  JOIN blue_iq_party                AS bp  ON bp.uid  = cs.UID
            WHERE cs.DELIVERED >= toDate(%(start)s)
              AND {US_COUNTRY_FILTER}
              AND (lower(cs.DOMAIN) IN %(media)s
                   OR multiMatchAny(lower(cs.URL), %(pols)s) > 0)
        ),
        touchpoints AS (
            -- Earliest touchpoint per UID, so each panelist contributes one row.
            SELECT party, uid, min(tp_ts) AS tp_ts
            FROM touchpoints_raw
            GROUP BY party, uid
        ),
        touchpoint_uids AS (
            SELECT DISTINCT uid FROM touchpoints
        ),
        -- Restrict the right side to touchpoint panelists ONLY. Without
        -- this filter, CH would scan all clickstream rows in the window
        -- (200M+) and OOM during the ASOF join.
        c2_filtered AS (
            SELECT cf.UID AS uid, cf.VISIT_TS AS ts,
                   lower(cf.DOMAIN) AS dom, lower(cf.URL) AS url
            FROM clickstream.clickstream_final AS cf
            WHERE cf.DELIVERED >= toDate(%(start)s)
              AND cf.UID IN (SELECT uid FROM touchpoint_uids)
        ),
        next_visits AS (
            SELECT
                t.party     AS party,
                t.uid       AS uid,
                c2.dom      AS next_dom,
                c2.url      AS next_url
            FROM touchpoints AS t
            ASOF LEFT JOIN c2_filtered AS c2
                ON c2.uid = t.uid AND c2.ts > t.tp_ts
        ),
        categorized AS (
            SELECT
                party,
                uid,
                multiIf(
                    next_dom = '' OR next_dom IS NULL, 'abandoned',
                    next_dom IN %(candidates)s, 'candidate_site',
                    next_dom IN %(donations)s,  'donation',
                    next_dom IN %(voting)s,     'voting_info',
                    -- candidate social (e.g. twitter.com/realDonaldTrump) beats raw social
                    next_dom IN %(social)s AND multiMatchAny(next_url, %(pols)s) > 0,
                        'candidate_social',
                    next_dom IN %(search)s AND multiMatchAny(extractURLParameter(next_url, 'q'), %(pols)s) > 0,
                        'candidate_search',
                    next_dom IN %(search)s, 'search',
                    next_dom IN %(media)s,  'news_dive',
                    next_dom IN %(social)s, 'social_discussion',
                    'other'
                ) AS destination
            FROM next_visits
        )
        SELECT party, destination,
               grouping(party) AS gp,
               uniqExact(uid)  AS panelists
        FROM categorized
        GROUP BY GROUPING SETS ((party, destination), (destination))
        HAVING panelists > 0
    """, {
        'start':      start,
        'media':      political_media_set,
        'pols':       pol_pats or ['__none__'],
        'candidates': candidate_set,
        'donations':  donation_set,
        'voting':     voting_info_set,
        'search':     search_set,
        'social':     social_set,
    })

    by_cell: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for party, dest, gp, panelists in cur.fetchall():
        if not dest:
            continue
        cell = "All||" if gp == 1 else f"{party or 'All'}||"
        by_cell[cell][dest] = max(by_cell[cell][dest], int(panelists))

    out: dict[str, list[dict]] = {}
    for cell, dests in by_cell.items():
        total = sum(dests.values()) or 1
        items = sorted(dests.items(), key=lambda x: -x[1])
        out[cell] = [{
            'destination': d,
            'panelists':   c,
            'share':       round(c / total, 4),
        } for d, c in items]
    return out


# ── Step 4: Build & ship ─────────────────────────────────────────────────────

def build_cube(lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Run all queries, assemble the cube dict, return it."""
    log.info("Connecting to ClickHouse ...")
    conn = _ch()
    try:
        start = _start_date(lookback_days)

        log.info("Step 1/8: party imputer + temp table")
        t0 = time.time()
        # Imputer window: at least 30 days for stable party signal, but never
        # more than 60. The 1d Live cube reuses the cached party map from S3
        # so it doesn't pay for a fresh scan at all (see _try_reuse_party_map_from_s3).
        imp_lookback = max(30, min(60, lookback_days))
        party_map = _build_party_temp_table(conn, lookback=imp_lookback)
        log.info("  done in %.1fs (%d UIDs)", time.time() - t0, len(party_map))

        log.info("Step 2/8: cell sizes")
        t0 = time.time()
        cell_sizes = _q_cell_sizes(conn)
        log.info("  done in %.1fs (%d cells)", time.time() - t0, len(cell_sizes))

        log.info("Step 3/8: search engines (Search Engine/AI)")
        t0 = time.time()
        search_engines = _q_top_by_cat(conn, start, 'Search Engine/AI', TOP_K)
        log.info("  done in %.1fs", time.time() - t0)

        log.info("Step 4/8: social platforms (Social Media)")
        t0 = time.time()
        social_media = _q_top_by_cat(conn, start, 'Social Media', TOP_K)
        log.info("  done in %.1fs", time.time() - t0)

        log.info("Step 5/8: demographics")
        t0 = time.time()
        demos = _q_demos(conn)
        log.info("  done in %.1fs", time.time() - t0)

        log.info("Step 6/8: turnout intent")
        t0 = time.time()
        turnout = _q_turnout(conn, start)
        log.info("  done in %.1fs", time.time() - t0)

        log.info("Step 7/8: politicians + political articles + search queries")
        t0 = time.time()
        politicians = _q_politicians(conn, start, TOP_K_LONG)
        log.info("  politicians done in %.1fs", time.time() - t0)

        t0 = time.time()
        articles = _q_articles(conn, start, TOP_K_LONG)
        log.info("  articles done in %.1fs", time.time() - t0)

        t0 = time.time()
        search_per_cell, search_global = _q_search_queries(conn, start, TOP_K)
        log.info("  search queries done in %.1fs (%d global terms)",
                 time.time() - t0, len(search_global))

        log.info("Step 8/9: AI issue-bucket rollup (one-shot, national)")
        t0 = time.time()
        issue_buckets_global = blue_iq.roll_up_political_issues(search_global)
        log.info("  AI rollup done in %.1fs (%d buckets)",
                 time.time() - t0, len(issue_buckets_global))

        log.info("Step 9/9: digital voter journey (post-touchpoint destinations)")
        t0 = time.time()
        try:
            voter_journey = _q_voter_journey(conn, start)
            log.info("  voter journey done in %.1fs (%d cells)",
                     time.time() - t0, len(voter_journey))
        except Exception as e:
            log.warning("  voter journey query failed (non-fatal): %s", e)
            voter_journey = {}

        # Assemble cube. We only emit cells whose total-UID count clears MIN_CELL_SIZE.
        log.info("Assembling cube ...")
        cells: dict[str, dict] = {}
        all_cell_keys = set(cell_sizes.keys()) | set(search_engines.keys()) | set(social_media.keys()) \
                        | set(turnout.keys()) | set(politicians.keys()) | set(articles.keys()) \
                        | set(search_per_cell.keys()) | set(demos.keys())
        for k in all_cell_keys:
            size = cell_sizes.get(k, 0)
            if size < MIN_CELL_SIZE:
                # Don't emit panel-suppressed cells — frontend will fall through
                # to external-only display for those slices.
                continue
            cells[k] = {
                'uid_count':           size,
                'search_engines':      search_engines.get(k, []),
                'social_media':        social_media.get(k, []),
                'turnout':             turnout.get(k, {'panelists': 0, 'sample_urls': []}),
                'demo':                demos.get(k, {}),
                'top_politicians':     politicians.get(k, []),
                'top_articles':        articles.get(k, []),
                'top_search_queries':  search_per_cell.get(k, []),
                'voter_journey':       voter_journey.get(k, []),
            }
        log.info("  %d cells emitted (suppressed: %d)",
                 len(cells), len(all_cell_keys) - len(cells))

        # Collect all distinct states + dmas for the filter dropdown. Only
        # values that survived the state-normalization pipeline appear here
        # (so no Canadian provinces, no military codes, no garbage).
        all_states = sorted({k.split('|')[1] for k in cells if k.split('|')[1] and not k.split('|')[2]})
        all_dmas   = sorted({k.split('|')[2] for k in cells if k.split('|')[2] and not k.split('|')[1]})
        log.info("  filter universe: %d states, %d dmas", len(all_states), len(all_dmas))

        # Gen-pop projection factor.
        # The "All||" cell holds the total US-panel size (uniqExact UIDs in
        # user_data_sanitized with COUNTRY in the US set). Multiplying any
        # panelist count in any cell by gen_pop_factor projects it to the
        # ~329.9M-adult US population.
        us_panel_total = int((cells.get('All||') or {}).get('uid_count') or 0)
        gen_pop_factor = (US_GEN_POP / us_panel_total) if us_panel_total > 0 else 1.0
        log.info("  US panel total: %s  → gen_pop_factor=%.3f×",
                 f"{us_panel_total:,}", gen_pop_factor)

        cube = {
            'version':            1,
            'computed_at':        datetime.now(timezone.utc).isoformat(),
            'lookback_days':      lookback_days,
            'min_cell_size':      MIN_CELL_SIZE,
            'us_panel_total':     us_panel_total,
            'us_gen_pop':         US_GEN_POP,
            'gen_pop_factor':     round(gen_pop_factor, 4),
            'all_parties':        blue_iq.VALID_PARTIES,
            'all_states':         all_states,
            'all_dmas':           all_dmas,
            'cells':              cells,
            'issue_buckets_global': issue_buckets_global,
        }
        return cube
    finally:
        try:
            conn.close()
        except Exception:
            pass


def ship_cube(cube: dict, *, lookback_days: Optional[int] = None,
              also_write_legacy: bool = True) -> None:
    """Write cube to S3 at the per-lookback key (cube_{N}d.json).

    For backward compat, the 30d cube is ALSO written to the legacy
    `latest.json` key so any clients still pointing there get a fresh
    copy. Override with `also_write_legacy=False` to skip.
    """
    try:
        from app import s3_client  # type: ignore
    except Exception:
        import boto3
        s3_client = boto3.client('s3', region_name='us-east-2')
    if lookback_days is None:
        lookback_days = int(cube.get('lookback_days') or LOOKBACK_DAYS)
    primary_key = cube_key_for_lookback(lookback_days)
    payload = json.dumps(cube, separators=(',', ':')).encode('utf-8')
    raw_size = len(payload)
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=primary_key,
        Body=payload,
        ContentType='application/json',
    )
    log.info("Cube written to s3://%s/%s (%.2f MB raw)",
             S3_BUCKET, primary_key, raw_size / (1024 * 1024))
    # Mirror the 30d cube into the legacy key so any reader still pointing
    # at `latest.json` (e.g. an older deploy of blue_iq.py) keeps working.
    if also_write_legacy and lookback_days == 30:
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=LEGACY_CUBE_KEY,
            Body=payload,
            ContentType='application/json',
        )
        log.info("  (mirrored to legacy key %s)", LEGACY_CUBE_KEY)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lookback', type=int, default=LOOKBACK_DAYS,
                    help=f'days of clickstream history (default {LOOKBACK_DAYS}). '
                         'Use 1 for the "Live" cube (yesterday only).')
    ap.add_argument('--all', action='store_true',
                    help='build both the Live (1d) and the default (30d) cubes in one run')
    ap.add_argument('--dry-run', action='store_true',
                    help='build the cube but skip S3 upload (smoke test)')
    args = ap.parse_args()

    lookbacks = [1, 30] if args.all else [args.lookback]
    t_total = time.time()
    for lb in lookbacks:
        log.info("=" * 70)
        log.info("Blue IQ cube build starting at %s (lookback=%dd)",
                 datetime.now(timezone.utc).isoformat(), lb)
        log.info("=" * 70)
        cube = build_cube(lookback_days=lb)
        if args.dry_run:
            log.info("--dry-run: skipping S3 upload. Cube has %d cells, %d global buckets.",
                     len(cube.get('cells', {})), len(cube.get('issue_buckets_global', [])))
        else:
            ship_cube(cube, lookback_days=lb)
    log.info("Total wall time across %d cube(s): %.1f minutes",
             len(lookbacks), (time.time() - t_total) / 60.0)


if __name__ == '__main__':
    main()
