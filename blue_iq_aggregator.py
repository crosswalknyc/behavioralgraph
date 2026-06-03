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
S3_BUCKET     = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_CUBE_KEY   = os.environ.get('BLUE_IQ_CUBE_KEY', 'blue_iq/aggregates/latest.json')
PARTY_KEY     = os.environ.get('BLUE_IQ_PARTY_KEY', 'blue_iq/party_imputed/all.json')


def _ch():
    try:
        from clickhouse_connector import connect_clickhouse  # type: ignore
    except ImportError:
        from migration.clickhouse_connector import connect_clickhouse  # type: ignore
    return connect_clickhouse()


def _start_date(lookback: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime('%Y-%m-%d')


def _cell_key(party: str, state: str, dma: str) -> str:
    return f"{party or ''}|{state or ''}|{dma or ''}"


# ── Step 1: Party imputer (with bulk INSERT into a per-session temp table) ──

def _build_party_temp_table(conn, lookback: int) -> dict[str, str]:
    """Run the heuristic party imputer over recent panelists, INSERT the
    resulting (uid, party) map into a session-scoped temp table the rest
    of the aggregator can JOIN against.

    Returns the {uid: party} dict (also persisted to S3 as a backup).
    """
    log.info("  party imputer: pulling political clickstream rows ...")
    start = _start_date(lookback)
    polparties = blue_iq._load_politician_parties()
    _, left_media, right_media = blue_iq._load_media_domains()

    rel_domains = list((left_media | right_media | {
        'actblue.com', 'dccc.org', 'democrats.org', 'winred.com', 'nrcc.org', 'gop.com',
    }))
    pol_names = list(polparties.keys())[:50]
    if pol_names:
        pol_pred = ' OR '.join(f"position(lower(URL), %(pol{i})s) > 0"
                                for i in range(len(pol_names)))
        pol_params = {f'pol{i}': n.lower() for i, n in enumerate(pol_names)}
    else:
        pol_pred = '1=0'
        pol_params = {}

    cur = conn.cursor()
    cur.execute(f"""
        SELECT UID, lower(COMMON_NAME), lower(DOMAIN), URL
        FROM clickstream.clickstream_final
        WHERE DELIVERED >= toDate(%(start)s)
          AND (lower(DOMAIN) IN %(rel)s OR ({pol_pred}))
        ORDER BY UID
    """, {'start': start, 'rel': rel_domains, **pol_params})
    rows = cur.fetchall()
    log.info("  party imputer: scored %d political rows", len(rows))

    by_uid: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_uid[r[0]].append((r[1], r[2], r[3]))

    party_map: dict[str, str] = {}
    counts: Counter = Counter()
    for uid, urows in by_uid.items():
        party, conf = blue_iq._score_party_from_rows(urows, polparties, left_media, right_media)
        party_map[uid] = party
        counts[party] += 1
    log.info("  party imputer breakdown: %s", dict(counts))

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

    # Persist to S3 for the live dashboard's degraded-fallback path.
    try:
        from app import s3_client  # type: ignore
        if s3_client is not None:
            payload = {
                uid: {'party': p, 'computed_at': datetime.now(timezone.utc).isoformat()}
                for uid, p in party_map.items()
            }
            s3_client.put_object(
                Bucket=S3_BUCKET, Key=PARTY_KEY,
                Body=json.dumps(payload).encode('utf-8'),
                ContentType='application/json',
            )
            log.info("  party imputer: persisted %d UIDs to s3://%s/%s", len(payload), S3_BUCKET, PARTY_KEY)
    except Exception as e:
        log.warning("  party imputer: S3 persist failed (non-fatal): %s", e)

    return party_map


# ── Step 2: Per-card GROUP BYs ──────────────────────────────────────────────

def _q_top_by_cat(conn, start: str, category: str, top_k: int) -> dict[str, list[dict]]:
    """One query that returns top-K COMMON_NAME by uniqExact(UID), for every
    (party, geo) cell. Cells are emitted at three granularities at once:
        - (party, state)         — geo = STATE, dma = ''
        - (party, dma)           — geo = '',    dma = DMA
        - (party, '', '')        — national per party
        - ('All', state, '')     — All-party by state (re-derive below)

    Returns: {cell_key: [{name, panelists}, ...]}
    """
    cur = conn.cursor()
    cur.execute("""
        WITH brands AS (
            SELECT DISTINCT BRAND
            FROM reference.host_mapping
            WHERE CATEGORY = %(cat)s
              AND coalesce(SECTION, '') != 'Hidden'
        ),
        base AS (
            SELECT
                p.party                  AS party,
                U.STATE                  AS state,
                U.DMA                    AS dma,
                C.COMMON_NAME            AS cn,
                C.UID                    AS uid
            FROM clickstream.clickstream_final AS C
            INNER JOIN userdata.user_data_sanitized AS U ON U.UID = C.UID
            INNER JOIN blue_iq_party        AS p ON p.uid = C.UID
            WHERE C.DELIVERED >= toDate(%(start)s)
              AND C.COMMON_NAME IN (SELECT BRAND FROM brands)
        )
        SELECT party, state, dma, cn, uniqExact(uid) AS p
        FROM base
        GROUP BY GROUPING SETS (
            (party, state, cn),
            (party, dma, cn),
            (party, cn),
            (cn)
        )
        HAVING p > 0
    """, {'cat': category, 'start': start})

    by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for party, state, dma, cn, p in cur.fetchall():
        party = party or 'All'
        state = state or ''
        dma   = dma   or ''
        # Convert the "(cn)"-only grouping set (everything else NULL → defaults above) to absolute national
        cell = _cell_key(party, state, dma)
        by_cell[cell].append((cn, int(p)))

    out: dict[str, list[dict]] = {}
    for cell, items in by_cell.items():
        items.sort(key=lambda x: -x[1])
        out[cell] = [{'name': n, 'panelists': p} for n, p in items[:top_k]]
    return out


def _q_cell_sizes(conn) -> dict[str, int]:
    """uniqExact(UID) per cell — used for cell suppression + denominators."""
    cur = conn.cursor()
    cur.execute("""
        SELECT party, state, dma, uniqExact(uid) AS p
        FROM (
            SELECT p.party AS party, U.STATE AS state, U.DMA AS dma, U.UID AS uid
            FROM userdata.user_data_sanitized AS U
            INNER JOIN blue_iq_party AS p ON p.uid = U.UID
        )
        GROUP BY GROUPING SETS (
            (party, state),
            (party, dma),
            (party),
            ()
        )
        HAVING p > 0
    """)
    out: dict[str, int] = {}
    for party, state, dma, p in cur.fetchall():
        out[_cell_key(party or 'All', state or '', dma or '')] = int(p)
    return out


def _q_demos(conn) -> dict[str, dict[str, list[dict]]]:
    """Per-cell demographic breakdown (no clickstream needed — pure user_data_sanitized)."""
    cur = conn.cursor()
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for col, label in [('AGE', 'age'), ('GENDER', 'gender'),
                       ('ETHNICITY', 'ethnicity'), ('INCOME', 'income')]:
        cur.execute(f"""
            SELECT party, state, dma, val, count() AS n
            FROM (
                SELECT p.party AS party, U.STATE AS state, U.DMA AS dma, U.{col} AS val
                FROM userdata.user_data_sanitized AS U
                INNER JOIN blue_iq_party AS p ON p.uid = U.UID
                WHERE U.{col} IS NOT NULL AND U.{col} != ''
            )
            GROUP BY GROUPING SETS (
                (party, state, val),
                (party, dma, val),
                (party, val)
            )
            HAVING n > 0
        """)
        bucket_by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for party, state, dma, val, n in cur.fetchall():
            cell = _cell_key(party or 'All', state or '', dma or '')
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
    cur.execute("""
        WITH matched AS (
            SELECT
                p.party AS party, U.STATE AS state, U.DMA AS dma,
                C.UID AS uid, lower(C.URL) AS u
            FROM clickstream.clickstream_final AS C
            INNER JOIN userdata.user_data_sanitized AS U ON U.UID = C.UID
            INNER JOIN blue_iq_party AS p ON p.uid = C.UID
            WHERE C.DELIVERED >= toDate(%(start)s)
              AND multiMatchAny(lower(C.URL), %(terms)s) > 0
        )
        SELECT party, state, dma, uniqExact(uid) AS p, groupUniqArray(20)(u) AS samples
        FROM matched
        GROUP BY GROUPING SETS (
            (party, state),
            (party, dma),
            (party),
            ()
        )
        HAVING p > 0
    """, {'start': start, 'terms': blue_iq._TURNOUT_PATTERNS})

    out: dict[str, dict] = {}
    for party, state, dma, p, samples in cur.fetchall():
        cell = _cell_key(party or 'All', state or '', dma or '')
        out[cell] = {
            'panelists':    int(p),
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
    cur.execute("""
        WITH hits AS (
            SELECT
                p.party AS party, U.STATE AS state, U.DMA AS dma,
                C.UID   AS uid,
                arrayJoin(multiMatchAllIndices(lower(C.URL), %(pats)s)) AS pol_idx
            FROM clickstream.clickstream_final AS C
            INNER JOIN userdata.user_data_sanitized AS U ON U.UID = C.UID
            INNER JOIN blue_iq_party        AS p ON p.uid = C.UID
            WHERE C.DELIVERED >= toDate(%(start)s)
              AND multiMatchAny(lower(C.URL), %(pats)s) > 0
        )
        SELECT party, state, dma, pol_idx, uniqExact(uid) AS p
        FROM hits
        GROUP BY GROUPING SETS (
            (party, state, pol_idx),
            (party, dma, pol_idx),
            (party, pol_idx),
            (pol_idx)
        )
        HAVING p > 0
    """, {'pats': patterns, 'start': start})

    by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for party, state, dma, idx, p in cur.fetchall():
        try:
            name = politicians[int(idx) - 1]  # CH multiMatchAllIndices is 1-based
        except (IndexError, TypeError, ValueError):
            continue
        cell = _cell_key(party or 'All', state or '', dma or '')
        by_cell[cell].append((name, int(p)))

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
    cur.execute("""
        SELECT party, state, dma, url, dom, uniqExact(uid) AS p
        FROM (
            SELECT
                p.party AS party, U.STATE AS state, U.DMA AS dma,
                C.URL AS url, lower(C.DOMAIN) AS dom, C.UID AS uid
            FROM clickstream.clickstream_final AS C
            INNER JOIN userdata.user_data_sanitized AS U ON U.UID = C.UID
            INNER JOIN blue_iq_party        AS p ON p.uid = C.UID
            WHERE C.DELIVERED >= toDate(%(start)s)
              AND lower(C.DOMAIN) IN %(doms)s
              AND length(C.URL) > 30
        )
        GROUP BY GROUPING SETS (
            (party, state, url, dom),
            (party, dma, url, dom),
            (party, url, dom),
            (url, dom)
        )
        HAVING p >= 2
    """, {'doms': list(domains_all), 'start': start})

    by_cell: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for party, state, dma, url, dom, p in cur.fetchall():
        cell = _cell_key(party or 'All', state or '', dma or '')
        by_cell[cell].append((url, dom, int(p)))

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
    cur.execute("""
        WITH q AS (
            SELECT lower(SEARCH_TEXT_NORMALIZED) AS qstr
            FROM reference.search_text_mapping
            WHERE TYPE = 'query'
              AND SEARCH_TEXT_NORMALIZED IS NOT NULL
              AND length(SEARCH_TEXT_NORMALIZED) BETWEEN 6 AND 200
        ),
        hits AS (
            SELECT
                p.party AS party, U.STATE AS state, U.DMA AS dma,
                C.UID AS uid, q.qstr AS term
            FROM clickstream.clickstream_final AS C
            INNER JOIN userdata.user_data_sanitized AS U ON U.UID = C.UID
            INNER JOIN blue_iq_party AS p ON p.uid = C.UID
            ANY INNER JOIN q ON position(lower(C.URL), q.qstr) > 0
            WHERE C.DELIVERED >= toDate(%(start)s)
        )
        SELECT party, state, dma, term, uniqExact(uid) AS p
        FROM hits
        GROUP BY GROUPING SETS (
            (party, state, term),
            (party, dma, term),
            (party, term),
            (term)
        )
        HAVING p >= 2
    """, {'start': start})

    by_cell: dict[str, list[tuple[str, int]]] = defaultdict(list)
    global_terms: Counter = Counter()
    for party, state, dma, term, p in cur.fetchall():
        cell = _cell_key(party or 'All', state or '', dma or '')
        by_cell[cell].append((term, int(p)))
        # The "(term)"-only grouping = absolute national. Feed that into the global classifier.
        if (party is None or party == '') and not state and not dma:
            global_terms[term] += int(p)
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


# ── Step 4: Build & ship ─────────────────────────────────────────────────────

def build_cube(lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Run all queries, assemble the cube dict, return it."""
    log.info("Connecting to ClickHouse ...")
    conn = _ch()
    try:
        start = _start_date(lookback_days)

        log.info("Step 1/8: party imputer + temp table")
        t0 = time.time()
        party_map = _build_party_temp_table(conn, lookback=max(90, lookback_days))
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

        log.info("Step 8/8: AI issue-bucket rollup (one-shot, national)")
        t0 = time.time()
        issue_buckets_global = blue_iq.roll_up_political_issues(search_global)
        log.info("  AI rollup done in %.1fs (%d buckets)",
                 time.time() - t0, len(issue_buckets_global))

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
            }
        log.info("  %d cells emitted (suppressed: %d)",
                 len(cells), len(all_cell_keys) - len(cells))

        # Collect all distinct states + dmas for the filter dropdown.
        all_states = sorted({k.split('|')[1] for k in cells if k.split('|')[1] and not k.split('|')[2]})
        all_dmas   = sorted({k.split('|')[2] for k in cells if k.split('|')[2] and not k.split('|')[1]})

        cube = {
            'version':            1,
            'computed_at':        datetime.now(timezone.utc).isoformat(),
            'lookback_days':      lookback_days,
            'min_cell_size':      MIN_CELL_SIZE,
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


def ship_cube(cube: dict) -> None:
    """Write cube to S3 (compressed via Content-Encoding for transparent decompression)."""
    try:
        from app import s3_client  # type: ignore
    except Exception:
        import boto3
        s3_client = boto3.client('s3', region_name='us-east-2')
    payload = json.dumps(cube, separators=(',', ':')).encode('utf-8')
    raw_size = len(payload)
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=S3_CUBE_KEY,
        Body=payload,
        ContentType='application/json',
    )
    log.info("Cube written to s3://%s/%s (%.2f MB raw)", S3_BUCKET, S3_CUBE_KEY, raw_size / (1024 * 1024))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lookback', type=int, default=LOOKBACK_DAYS,
                    help=f'days of clickstream history (default {LOOKBACK_DAYS})')
    ap.add_argument('--dry-run', action='store_true',
                    help='build the cube but skip S3 upload (smoke test)')
    args = ap.parse_args()

    t_total = time.time()
    log.info("Blue IQ cube build starting at %s (lookback=%dd)",
             datetime.now(timezone.utc).isoformat(), args.lookback)
    cube = build_cube(lookback_days=args.lookback)
    if args.dry_run:
        log.info("--dry-run: skipping S3 upload. Cube has %d cells, %d global buckets.",
                 len(cube.get('cells', {})), len(cube.get('issue_buckets_global', [])))
    else:
        ship_cube(cube)
    log.info("Total wall time: %.1f minutes", (time.time() - t_total) / 60.0)


if __name__ == '__main__':
    main()
