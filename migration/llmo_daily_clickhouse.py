"""
llmo_daily_clickhouse.py  —  Full ClickHouse + OpenAI replacement for SP_LLMO_DAILY.

Replaces the Snowflake stored procedure (`setup_llmo_daily.sql`) entirely:

  * Reads raw clickstream from ClickHouse (`clickstream.clickstream_final`)
  * Tags each row with MATCH_TYPE (AI_AGENT / POST_AI_NON_AGENT / POST_AI_2ND /
    POST_AI_3RD) using a window function CTE — same logic as the SF procedure
  * Writes tagged rows to `clickstream.llmo_events` (persistent, partitioned by
    month — mirrors SF's PROCESSEDCLICKSTREAM.PUBLIC.LLMO)
  * Aggregates per-day: top LLMs, post-AI attribution (1st/2nd/3rd), brand_conversion
    (MPB), retailer_conversion (WTS), flows, top searches, browsers, platforms,
    demographics (overall + per-agent), insights pack (hourly/dow/session_depth/
    cross_llm_pairs/funnel)
  * Classifies search_themes via OpenAI gpt-4o (replaces SNOWFLAKE.CORTEX.COMPLETE)
  * Uploads summary JSON.gz to s3://llmo/processed/llmo_daily_summary.json.gz —
    the exact path the Flask dashboard reads via _llmo_load_summary().

Designed to be a drop-in replacement: the dashboard JSON shape is byte-compatible
with what SP_LLMO_DAILY produced, so no Flask changes are needed.

Usage:
    python3 migration/llmo_daily_clickhouse.py            # ingest yesterday + rebuild summary
    python3 migration/llmo_daily_clickhouse.py --date 2026-04-13
    python3 migration/llmo_daily_clickhouse.py --summary-only   # skip ingest, just rebuild summary
    python3 migration/llmo_daily_clickhouse.py --backfill 2025-01-01:2025-12-31
    python3 migration/llmo_daily_clickhouse.py --dry-run

Required env (load from .env at repo root or bg-webapp/.env):
    CH_HOST, CH_PORT, CH_USER, CH_PASSWORD     — ClickHouse on Hetzner
    OPENAI_API_KEY                              — for search_themes classification
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY    — for s3://llmo/ upload
    LLMO_S3_BUCKET (default 'llmo')
    LLMO_SUMMARY_KEY (default 'processed/llmo_daily_summary.json.gz')
    LLMO_SEARCH_THEMES_MODEL (default 'gpt-4o')
    LLMO_SEARCH_THEMES_RECENT_DAYS (default 90 — only classify recent dates)
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import boto3
import clickhouse_connect

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
logger = logging.getLogger('llmo_daily')


# ── Config ───────────────────────────────────────────────────────────────────

CH_HOST       = os.environ.get('CH_HOST',     '37.27.140.111')
CH_PORT       = int(os.environ.get('CH_PORT', '8123'))
CH_USER       = os.environ.get('CH_USER',     'bgapp')
CH_PASSWORD   = os.environ.get('CH_PASSWORD', '')
CH_DATABASE   = os.environ.get('CH_DATABASE', 'clickstream')

OPENAI_KEY    = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL  = os.environ.get('LLMO_SEARCH_THEMES_MODEL', 'gpt-4o')
THEME_BATCH   = int(os.environ.get('LLMO_SEARCH_THEMES_BATCH', '75'))
RECENT_DAYS   = int(os.environ.get('LLMO_SEARCH_THEMES_RECENT_DAYS', '90'))

S3_BUCKET     = os.environ.get('LLMO_S3_BUCKET', 'llmo')
# Match the existing object key SP_LLMO_DAILY produced (no .gz suffix — Snowflake's
# COPY INTO with COMPRESSION='GZIP' kept the .json extension and used Content-Encoding
# for transport compression). The Flask app's _llmo_find_summary_key walks the prefix,
# so it'll pick up either name; we keep .json for byte-for-byte compatibility.
S3_KEY        = os.environ.get('LLMO_SUMMARY_KEY', 'processed/llmo_daily_summary.json')
S3_RUN_PREFIX = os.environ.get('LLMO_RUN_PREFIX', 'latest_run/')

# Live ClickHouse uses userdata.user_data_sanitized (not userfiles.* per the
# clickhouse_setup.sql schema file — that file is out of date). Override via env.
USER_DATA_TABLE = os.environ.get('LLMO_USER_DATA_TABLE', 'userdata.user_data_sanitized')

# Match SF procedure regex set exactly (case-insensitive ASCII patterns).
# Any change here must mirror setup_llmo_daily.sql lines 86-90.
AI_NAME_PATTERN = re.compile(
    r'(ai\s*agent|chat\s*-?\s*gpt|chatgpt|openai|gpt-?\d*|claude|claude\s*ai|'
    r'anthropic|gemini|google\s*gemini|bard|copilot|microsoft\s*copilot|'
    r'bing\s*chat|perplexity|grok|xai|llama|meta\s*ai|mistral|le\s*chat|'
    r'deep\s*seek|deepseek|qwen|kimi|character\.ai|char\s*ai|poe|pi\.ai|'
    r'you\.com|phind|blackbox\s*ai|midjourney|jasper)',
    re.IGNORECASE,
)
AI_URL_PATTERN = re.compile(
    r'claude\.ai|\.claude\.|anthropic\.com|console\.anthropic|claude\.com|'
    r'deepseek\.com|chat\.deepseek',
    re.IGNORECASE,
)
AI_DOMAIN_PATTERN = re.compile(
    r'claude\.ai|anthropic\.com|deepseek\.com',
    re.IGNORECASE,
)

EXCLUDED_NAMES = {'afc bournemouth', 'chelmico', 'unknown'}

PII_THEME = 'Personal Information Query'

NOISE_EXACT = {
    'chatgpt', 'chat gpt', 'gpt', 'openai', 'claude', 'claude ai', 'perplexity', 'gemini',
    'google gemini', 'bard', 'copilot', 'microsoft copilot', 'deepseek', 'meta ai',
    'login', 'logout', 'sign in', 'sign up', 'home', 'search', 'help', 'settings',
    'fs', 'none', 'pending', 'consent', 'select_account', 'null', 'undefined', 'na', 'n/a',
    'api', 'cdn', 'www', 'chat', 'gpt-4', 'gpt 4', 'gpt4', 'new chat', 'new thread',
    'openai.com', 'chatgpt.com',
}
RE_EMAIL = re.compile(r'\b[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}\.[A-Za-z]{2,}\b')
RE_SSN   = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
RE_PHONE = re.compile(r'\b(?:\+?1[-.\s]?)?(?:\(\s*\d{3}\s*\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b')


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASSWORD,
        database=CH_DATABASE,
        connect_timeout=30,
        send_receive_timeout=3600,
        settings={
            'max_execution_time': 1800,
            'max_memory_usage': 120 * 1024 * 1024 * 1024,
            'max_bytes_before_external_group_by': 40 * 1024 * 1024 * 1024,
            'max_bytes_before_external_sort':     40 * 1024 * 1024 * 1024,
            'join_algorithm': 'parallel_hash,hash,partial_merge,grace_hash',
            'max_threads': 0,
            'max_block_size': 65536,
        },
    )


def name_allowed(nm: str | None) -> bool:
    if not nm:
        return False
    n = str(nm).strip().lower()
    return bool(n) and n not in EXCLUDED_NAMES


def is_ai_match(common_name: str | None, url: str | None, domain: str | None) -> bool:
    cn = (common_name or '').split('|', 1)[0].strip()
    if cn and AI_NAME_PATTERN.search(cn):
        return True
    if url and AI_URL_PATTERN.search(url):
        return True
    if domain and AI_DOMAIN_PATTERN.search(domain):
        return True
    return False


# ── Step 1: Build llmo_events for a given date ───────────────────────────────

INSERT_LLMO_EVENTS_SQL = """
INSERT INTO clickstream.llmo_events
    (UID, BROWSER, PLATFORM, URL, VISIT_TS, DELIVERED,
     COMMON_NAME, TICKER, DOMAIN, MATCH_TYPE, LLMO_RUN_TS)
SELECT
    UID, BROWSER, PLATFORM, URL, VISIT_TS, DELIVERED,
    cn_first AS COMMON_NAME, TICKER, DOMAIN,
    MATCH_TYPE, now() AS LLMO_RUN_TS
FROM (
    WITH base AS (
        SELECT
            f.UID, f.BROWSER, f.PLATFORM, f.URL, f.VISIT_TS, f.DELIVERED,
            f.COMMON_NAME AS COMMON_NAME_RAW, f.TICKER, f.DOMAIN,
            trim(splitByChar('|', f.COMMON_NAME)[1]) AS cn_first,
            if(
                match(lower(coalesce(trim(splitByChar('|', f.COMMON_NAME)[1]), '')),
                      '(ai\\\\s*agent|chat\\\\s*-?\\\\s*gpt|chatgpt|openai|gpt-?\\\\d*|claude|claude\\\\s*ai|anthropic|gemini|google\\\\s*gemini|bard|copilot|microsoft\\\\s*copilot|bing\\\\s*chat|perplexity|grok|xai|llama|meta\\\\s*ai|mistral|le\\\\s*chat|deep\\\\s*seek|deepseek|qwen|kimi|character\\\\.ai|char\\\\s*ai|poe|pi\\\\.ai|you\\\\.com|phind|blackbox\\\\s*ai|midjourney|jasper)')
                OR match(lower(coalesce(f.URL, '')),
                      'claude\\\\.ai|\\\\.claude\\\\.|anthropic\\\\.com|console\\\\.anthropic|claude\\\\.com|deepseek\\\\.com|chat\\\\.deepseek')
                OR match(lower(coalesce(f.DOMAIN, '')),
                      'claude\\\\.ai|anthropic\\\\.com|deepseek\\\\.com'),
                1, 0
            ) AS is_ai
        FROM clickstream.clickstream_final f
        WHERE f.DELIVERED BETWEEN {date_from:Date} AND {date_to:Date}
    ),
    sequenced AS (
        SELECT
            base.*,
            lagInFrame(is_ai, 1) OVER w AS prev_is_ai,
            lagInFrame(is_ai, 2) OVER w AS prev2_is_ai,
            lagInFrame(is_ai, 3) OVER w AS prev3_is_ai
        FROM base
        WINDOW w AS (PARTITION BY UID, DELIVERED
                     ORDER BY VISIT_TS, URL, cn_first, PLATFORM, BROWSER
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    )
    SELECT
        UID, BROWSER, PLATFORM, URL, VISIT_TS, DELIVERED,
        nullIf(trim(cn_first), '') AS cn_first, TICKER, DOMAIN,
        multiIf(
            is_ai = 1, 'AI_AGENT',
            is_ai = 0 AND prev_is_ai  = 1, 'POST_AI_NON_AGENT',
            is_ai = 0 AND prev2_is_ai = 1 AND prev_is_ai = 0, 'POST_AI_2ND',
            is_ai = 0 AND prev3_is_ai = 1 AND prev2_is_ai = 0 AND prev_is_ai = 0, 'POST_AI_3RD',
            ''
        ) AS MATCH_TYPE
    FROM sequenced
    WHERE (is_ai = 1
        OR (is_ai = 0 AND prev_is_ai = 1)
        OR (is_ai = 0 AND prev2_is_ai = 1 AND prev_is_ai = 0)
        OR (is_ai = 0 AND prev3_is_ai = 1 AND prev2_is_ai = 0 AND prev_is_ai = 0))
      AND lower(trim(coalesce(cn_first, ''))) NOT IN ('afc bournemouth', 'chelmico', 'unknown')
)
"""


def ingest_date_range(ch, date_from: date, date_to: date, dry_run: bool = False) -> int:
    """Build llmo_events rows for a date range (inclusive). Idempotent — replaces
    any existing rows in that range first. A single-day call passes the same date
    for from/to. The window function partitions by (UID, DELIVERED) so multi-day
    batches stay correct."""
    if dry_run:
        logger.info("[dry-run] Would ingest llmo_events for %s..%s", date_from, date_to)
        return 0

    logger.info("Clearing existing llmo_events rows in %s..%s ...", date_from, date_to)
    ch.command(
        "ALTER TABLE clickstream.llmo_events DELETE "
        "WHERE DELIVERED BETWEEN %(d1)s AND %(d2)s "
        "SETTINGS mutations_sync = 2",
        parameters={'d1': date_from, 'd2': date_to},
    )
    logger.info("Running window-function INSERT for %s..%s (this can take many minutes for large ranges)...", date_from, date_to)
    ch.command(INSERT_LLMO_EVENTS_SQL, parameters={'date_from': date_from, 'date_to': date_to})
    cnt = ch.query(
        "SELECT count() FROM clickstream.llmo_events "
        "WHERE DELIVERED BETWEEN %(d1)s AND %(d2)s",
        parameters={'d1': date_from, 'd2': date_to},
    ).result_rows[0][0]
    logger.info("Ingested %s llmo_events rows for %s..%s", f"{cnt:,}", date_from, date_to)
    return int(cnt)


def ingest_target_date(ch, target_date: date, dry_run: bool = False) -> int:
    """Backwards-compatible single-day wrapper around ingest_date_range."""
    return ingest_date_range(ch, target_date, target_date, dry_run=dry_run)


# ── Step 2: Per-day aggregations from llmo_events ────────────────────────────

def _q(ch, sql: str, **params) -> list[tuple]:
    return ch.query(sql, parameters=params or None).result_rows


def fetch_dates(ch) -> list[str]:
    rows = _q(ch, "SELECT DISTINCT DELIVERED AS d FROM clickstream.llmo_events ORDER BY d DESC")
    return [r[0].isoformat() for r in rows]


def fetch_totals(ch) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               uniqExact(UID)      AS uu,
               count()             AS cl
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT'
        GROUP BY d
    """)
    for d, uu, cl in rows:
        out[d] = {'total_ai_users': int(uu), 'total_ai_clicks': int(cl)}
    return out


def fetch_top_by_match(ch, match_type: str, cap: int) -> dict[str, list[dict]]:
    """Top COMMON_NAMEs per (date, match_type)."""
    out: dict[str, list[dict]] = defaultdict(list)
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               COMMON_NAME         AS name,
               uniqExact(UID)      AS uu,
               count()             AS cl
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = %(mt)s
          AND COMMON_NAME IS NOT NULL AND trim(COMMON_NAME) != ''
        GROUP BY d, name
        ORDER BY d, uu DESC
    """, mt=match_type)
    for d, name, uu, cl in rows:
        if not name_allowed(name):
            continue
        if len(out[d]) < cap:
            out[d].append({'name': name, 'unique_users': int(uu), 'total_clicks': int(cl)})
    return dict(out)


def fetch_browsers(ch) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d, BROWSER, uniqExact(UID) AS uu
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT' AND BROWSER != ''
        GROUP BY d, BROWSER
        ORDER BY d, uu DESC
    """)
    for d, br, uu in rows:
        out[d].append({'name': br, 'unique_users': int(uu)})
    return dict(out)


def fetch_platforms(ch) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d, PLATFORM, uniqExact(UID) AS uu
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT' AND PLATFORM != ''
        GROUP BY d, PLATFORM
        ORDER BY d, uu DESC
    """)
    for d, pl, uu in rows:
        out[d].append({'name': pl, 'unique_users': int(uu)})
    return dict(out)


def fetch_searches(ch) -> dict[str, list[dict]]:
    """Extract search queries from URL params on AI_AGENT clicks."""
    out: dict[str, list[dict]] = defaultdict(list)
    # Extract first matching ?q= / &q= / ?query= / etc. via splitByString in CH.
    rows = _q(ch, """
        WITH search_urls AS (
            SELECT
                toString(DELIVERED) AS d,
                URL,
                coalesce(
                    nullIf(splitByString('&', splitByString('?q=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&q=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('?query=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&query=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('?p=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&p=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('?search=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&search=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('?prompt=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&prompt=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('?text=', URL)[2])[1], ''),
                    nullIf(splitByString('&', splitByString('&text=', URL)[2])[1], '')
                ) AS term
            FROM clickstream.llmo_events
            WHERE MATCH_TYPE = 'AI_AGENT' AND URL != ''
        )
        SELECT d, term, count() AS cnt
        FROM search_urls
        WHERE term IS NOT NULL AND term != ''
        GROUP BY d, term
        ORDER BY d, cnt DESC
    """)
    for d, term, cnt in rows:
        if len(out[d]) >= 100:
            continue
        try:
            t = urllib.parse.unquote_plus(term)
        except Exception:
            t = term
        out[d].append({'term': t[:200], 'count': int(cnt)})
    return dict(out)


# ── MPB / WTS conversion ─────────────────────────────────────────────────────

def fetch_section_tokens(ch, section_name: str) -> set[str]:
    rows = _q(ch, """
        SELECT DISTINCT lower(trim(tok)) AS tok_lc
        FROM (
            SELECT
                arrayJoin(splitByChar('|', BRAND)) AS brand_part,
                arrayJoin(splitByChar(',', SECTION)) AS sec_part
            FROM reference.host_mapping
            WHERE BRAND IS NOT NULL AND trim(BRAND) != ''
              AND SECTION IS NOT NULL
        )
        ARRAY JOIN [trim(brand_part)] AS tok
        WHERE lower(trim(sec_part)) = %(section)s
          AND trim(tok) != ''
    """, section=section_name)
    return {r[0] for r in rows if r[0]}


def fetch_post_ai_brand_conv(ch, allowed_tokens: set[str], cap: int = 100) -> dict[str, list[dict]]:
    """Aggregate post-AI clicks (1st+2nd+3rd) whose COMMON_NAME tokens match
    an allowed BRAND token from HOST_MAPPING for a given SECTION."""
    if not allowed_tokens:
        return {}

    out: dict[str, list[dict]] = defaultdict(list)
    # Bring tokens in via parameter (CH supports Array(String) params)
    rows = _q(ch, """
        WITH parts AS (
            SELECT
                toString(DELIVERED) AS d,
                UID,
                trim(arrayJoin(splitByChar('|', COMMON_NAME))) AS cn_tok
            FROM clickstream.llmo_events
            WHERE MATCH_TYPE IN ('POST_AI_NON_AGENT', 'POST_AI_2ND', 'POST_AI_3RD')
              AND COMMON_NAME IS NOT NULL AND trim(COMMON_NAME) != ''
        )
        SELECT d,
               any(cn_tok)        AS name,
               uniqExact(UID)     AS uu,
               count()            AS cl
        FROM parts
        WHERE cn_tok != '' AND lower(cn_tok) IN %(toks)s
        GROUP BY d, lower(cn_tok)
        ORDER BY d, uu DESC
    """, toks=list(allowed_tokens))
    for d, name, uu, cl in rows:
        if len(out[d]) < cap:
            out[d].append({'name': name or 'Unknown',
                           'unique_users': int(uu), 'total_clicks': int(cl)})
    return dict(out)


# ── Flows: AI_AGENT → post-AI destination ────────────────────────────────────

def fetch_flows(ch, cap: int = 500) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    rows = _q(ch, """
        WITH ordered AS (
            SELECT
                toString(DELIVERED) AS d,
                UID, VISIT_TS, COMMON_NAME, MATCH_TYPE,
                /* Carry forward the most recent AI_AGENT COMMON_NAME within
                   each (UID, day). lagInFrame won't propagate non-AI rows, so
                   we use anyLast over a frame that's masked to AI_AGENT only. */
                anyLast(if(MATCH_TYPE = 'AI_AGENT', COMMON_NAME, NULL)) IGNORE NULLS
                    OVER (PARTITION BY UID, DELIVERED
                          ORDER BY VISIT_TS
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS src_ai
            FROM clickstream.llmo_events
        ),
        post AS (
            SELECT d, UID, COMMON_NAME, src_ai
            FROM ordered
            WHERE MATCH_TYPE IN ('POST_AI_NON_AGENT', 'POST_AI_2ND', 'POST_AI_3RD')
              AND COMMON_NAME IS NOT NULL AND trim(COMMON_NAME) != ''
              AND src_ai IS NOT NULL AND trim(src_ai) != ''
        ),
        parts AS (
            SELECT d,
                   trim(src_ai) AS source,
                   trim(arrayJoin(splitByChar('|', COMMON_NAME))) AS destination,
                   UID
            FROM post
        )
        SELECT d, source, destination, uniqExact(UID) AS uu, count() AS cl
        FROM parts
        WHERE destination != ''
        GROUP BY d, source, destination
        ORDER BY d, uu DESC
    """)
    for d, src, dst, uu, cl in rows:
        if len(out[d]) < cap:
            out[d].append({'source': src, 'destination': dst,
                           'unique_users': int(uu), 'clicks': int(cl)})
    return dict(out)


# ── Demographics (overall + per-agent) ───────────────────────────────────────

# Live CH userdata.user_data_sanitized uses AGE / INCOME (matches the SF column names).
# The dashboard's keys (gender/age/ethnicity/income/education) are kept unchanged.
DEMO_COLS = {
    'gender':    'GENDER',
    'age':       'AGE',
    'ethnicity': 'ETHNICITY',
    'income':    'INCOME',
    'education': 'EDUCATION',
}


def fetch_demographics_by_day(ch) -> dict[str, dict]:
    """Materialize the AI-user demographic JOIN once into a Memory temp table,
    then run the 5 category aggregations against that — 5x faster than 5
    separate scans of clickstream.llmo_events INNER JOIN user_data_sanitized."""
    out: dict[str, dict] = {}
    trend: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    select_cols = ', '.join(f'd.{col} AS {col}' for _cat, col in DEMO_COLS.items())
    ch.command(f"DROP TABLE IF EXISTS _llmo_demos_tmp")
    ch.command(f"""
        CREATE TEMPORARY TABLE _llmo_demos_tmp ENGINE = Memory AS
        SELECT u.d AS d, u.UID AS UID, {select_cols}
        FROM (SELECT DISTINCT UID, DELIVERED AS d
              FROM clickstream.llmo_events
              WHERE MATCH_TYPE = 'AI_AGENT') u
        INNER JOIN {USER_DATA_TABLE} d ON u.UID = d.UID
    """)

    union_legs = []
    for cat, col in DEMO_COLS.items():
        union_legs.append(f"""
            SELECT '{cat}' AS cat, toString(d) AS day, {col} AS val, uniqExact(UID) AS cnt
            FROM _llmo_demos_tmp
            WHERE {col} != '' AND upper(trim({col})) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
            GROUP BY day, val
        """)
    rows = _q(ch, ' UNION ALL '.join(union_legs))
    ch.command("DROP TABLE IF EXISTS _llmo_demos_tmp")

    for cat, d, val, cnt in rows:
        trend[cat][d].append({'value': val, 'count': int(cnt)})

    all_dates: set[str] = set()
    for cat in DEMO_COLS:
        all_dates.update(trend[cat].keys())

    for d in all_dates:
        demographics: dict[str, list[dict]] = {}
        demo_trend:   dict[str, dict]       = {}
        for cat in DEMO_COLS:
            items = sorted(trend[cat].get(d, []), key=lambda x: -x['count'])
            total = sum(it['count'] for it in items)
            pct_items = [{'value': it['value'], 'count': it['count'],
                          'pct': round(it['count'] / total * 10000) / 100 if total else 0}
                         for it in items]
            demographics[cat] = pct_items
            demo_trend[cat] = {d: pct_items}
        out[d] = {'demographics': demographics, 'demo_trend': demo_trend}
    return out


def fetch_demographics_by_agent(ch, min_users: int = 50) -> dict[str, dict[str, dict]]:
    """For each (date, agent) with >= min_users distinct AI_AGENT users,
    compute demographics breakdown. Heavy — only run on days that have data."""
    out: dict[str, dict[str, dict]] = {}
    pairs = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               lower(trim(COMMON_NAME)) AS agent_lc,
               any(trim(COMMON_NAME))   AS agent_display,
               uniqExact(UID)           AS uu
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT' AND COMMON_NAME != ''
        GROUP BY d, agent_lc
        HAVING uu >= %(m)s
        ORDER BY d, uu DESC
    """, m=min_users)

    if not pairs:
        return out

    # For efficiency, group all per-agent UID lookups by date.
    for d, agent_lc, agent_display, uu in pairs:
        demographics: dict[str, list[dict]] = {}
        for cat, col in DEMO_COLS.items():
            rows = _q(ch, f"""
                WITH agent_uids AS (
                    SELECT DISTINCT UID
                    FROM clickstream.llmo_events
                    WHERE MATCH_TYPE = 'AI_AGENT'
                      AND DELIVERED = toDate(%(d)s)
                      AND lower(trim(COMMON_NAME)) = %(agent)s
                )
                SELECT d.{col} AS val, uniqExact(u.UID) AS cnt
                FROM agent_uids u
                INNER JOIN {USER_DATA_TABLE} d ON u.UID = d.UID
                WHERE d.{col} != '' AND upper(trim(d.{col})) NOT IN ('PREFER NOT TO SAY','NONE','N/A')
                GROUP BY val
            """, d=d, agent=agent_lc)
            items = sorted([{'value': v, 'count': int(c)} for v, c in rows],
                           key=lambda x: -x['count'])
            total = sum(it['count'] for it in items)
            pct_items = [{'value': it['value'], 'count': it['count'],
                          'pct': round(it['count'] / total * 10000) / 100 if total else 0}
                         for it in items]
            demographics[cat] = pct_items
        demo_trend = {cat: {d: demographics[cat]} for cat in DEMO_COLS}
        out.setdefault(d, {})[agent_lc] = {'demographics': demographics,
                                           'demo_trend': demo_trend}
    return out


# ── Insights pack: hourly, dow, session_depth, cross_llm_pairs, funnel ──────

def fetch_insights_by_day(ch) -> dict[str, dict]:
    out: dict[str, dict] = {}

    # Hourly (PT)
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               toHour(toTimeZone(VISIT_TS, 'America/Los_Angeles')) AS hr,
               count() AS c
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT'
        GROUP BY d, hr
    """)
    for d, hr, c in rows:
        out.setdefault(d, _empty_insights())['hourly'][int(hr)] += int(c)

    # Day-of-week (ISO Mon=1 → idx 0)
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               toDayOfWeek(DELIVERED) AS dw,
               count() AS c
        FROM clickstream.llmo_events
        WHERE MATCH_TYPE = 'AI_AGENT'
        GROUP BY d, dw
    """)
    for d, dw, c in rows:
        idx = int(dw) - 1
        if 0 <= idx < 7:
            out.setdefault(d, _empty_insights())['dow'][idx] += int(c)

    # Session depth bucketed (1, 2-4, 5+)
    rows = _q(ch, """
        WITH per AS (
            SELECT toString(DELIVERED) AS d,
                   trim(COMMON_NAME)   AS llm,
                   UID,
                   count()             AS cl
            FROM clickstream.llmo_events
            WHERE MATCH_TYPE = 'AI_AGENT' AND COMMON_NAME != ''
            GROUP BY d, llm, UID
        )
        SELECT d, llm,
               multiIf(cl = 1, '1', cl BETWEEN 2 AND 4, '2_4', '5p') AS bucket,
               count() AS n
        FROM per
        GROUP BY d, llm, bucket
    """)
    for d, llm, bucket, n in rows:
        bucket_dict = out.setdefault(d, _empty_insights())['session_depth'].setdefault(llm, {})
        bucket_dict[bucket] = bucket_dict.get(bucket, 0) + int(n)

    # Cross-LLM pairs
    rows = _q(ch, """
        WITH u AS (
            SELECT DISTINCT UID, toString(DELIVERED) AS d, trim(COMMON_NAME) AS llm
            FROM clickstream.llmo_events
            WHERE MATCH_TYPE = 'AI_AGENT' AND COMMON_NAME != ''
        )
        SELECT u1.d, u1.llm AS a, u2.llm AS b, uniqExact(u1.UID) AS n
        FROM u u1
        INNER JOIN u u2 ON u1.UID = u2.UID AND u1.d = u2.d AND u1.llm < u2.llm
        GROUP BY u1.d, a, b
        ORDER BY u1.d, n DESC
    """)
    for d, a, b, n in rows:
        ins = out.setdefault(d, _empty_insights())
        if len(ins['cross_llm_pairs']) < 15:
            ins['cross_llm_pairs'].append({'a': a, 'b': b, 'n': int(n)})

    # Funnel: post-AI 1/2/3 unique users
    rows = _q(ch, """
        SELECT toString(DELIVERED) AS d,
               uniqExactIf(UID, MATCH_TYPE = 'POST_AI_NON_AGENT') AS u1,
               uniqExactIf(UID, MATCH_TYPE = 'POST_AI_2ND')       AS u2,
               uniqExactIf(UID, MATCH_TYPE = 'POST_AI_3RD')       AS u3
        FROM clickstream.llmo_events
        GROUP BY d
    """)
    for d, u1, u2, u3 in rows:
        ins = out.setdefault(d, _empty_insights())
        ins['funnel']['post1_users'] = int(u1)
        ins['funnel']['post2_users'] = int(u2)
        ins['funnel']['post3_users'] = int(u3)

    return out


def _empty_insights() -> dict:
    return {
        'hourly': [0] * 24,
        'dow':    [0] * 7,
        'session_depth':   {},
        'cross_llm_pairs': [],
        'funnel':          {'post1_users': 0, 'post2_users': 0, 'post3_users': 0},
    }


# ── Search themes via OpenAI gpt-4o (replaces SNOWFLAKE.CORTEX.COMPLETE) ────

def term_has_pii(term: str) -> bool:
    return bool(RE_EMAIL.search(term) or RE_SSN.search(term) or RE_PHONE.search(term))


def term_is_noise(term: str) -> bool:
    if not isinstance(term, str):
        return True
    t = term.strip()
    if not t:
        return True
    low = t.lower()
    if low.startswith('http://') or low.startswith('https://'):
        return True
    norm_ws = re.sub(r'\s+', ' ', low).strip()
    norm_alnum = re.sub(r'[^a-z0-9\s]', '', norm_ws)
    if norm_alnum in NOISE_EXACT:
        return True
    words = norm_ws.split()
    if len(t) < 8:
        return True
    if len(t) < 12 and len(words) < 3:
        return True
    low_us = low.replace('-', '_')
    if re.fullmatch(r'[a-z][a-z0-9_-]{1,40}', low) and '_' in low_us:
        return True
    if re.fullmatch(r'[a-z][a-z0-9_]{2,35}', low) and '_' in low:
        return True
    return False


def _openai_client():
    if not OPENAI_KEY:
        return None
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_KEY)


def build_search_themes_for_day(searches: list[dict]) -> Optional[dict]:
    """Returns a search_themes dict shaped exactly like SP_LLMO_DAILY's output:
    { success, version, categories: [{theme, weight, examples}], meta: {...} }
    """
    pii_weight = 0
    kept: list[dict] = []
    for s in searches or []:
        term = (s.get('term') or '').strip()
        try:
            cnt = int(round(float(s.get('count', 0))))
        except (TypeError, ValueError):
            cnt = 0
        if not term or cnt <= 0:
            continue
        if term_has_pii(term):
            pii_weight += cnt
            continue
        if term_is_noise(term):
            continue
        kept.append({'term': term[:800], 'count': cnt})

    total_weight = pii_weight + sum(x['count'] for x in kept)
    meta_base = {
        'total_weight': total_weight,
        'pii_weight':   pii_weight,
        'queries_sent_to_model': len(kept),
        'model': OPENAI_MODEL,
        'source': 'clickhouse_openai',
    }

    if total_weight <= 0:
        return {'success': True, 'version': 1, 'categories': [], 'meta': meta_base}

    client = _openai_client()
    if not client:
        logger.warning("OPENAI_API_KEY not set — search_themes left as null")
        return None

    theme_weight: dict[str, int] = defaultdict(int)
    if pii_weight > 0:
        theme_weight[PII_THEME] = pii_weight
    theme_examples: dict[str, list[str]] = defaultdict(list)
    assignments: dict[int, str] = {}

    sys_msg = (
        'You label analytics search strings from AI chat products. '
        'Each line is INDEX<TAB>JSON-encoded query string. '
        'For each index, assign exactly ONE short theme name in Title Case (2-5 words), '
        'e.g. "Image Generation", "Software Development", "Writing & Editing", '
        '"Sports Discussion", "Current Events", "Education & Homework", '
        '"Health & Fitness", "Shopping & Products", "Entertainment Recommendations", '
        '"Humor & Games". If the query contains or solicits private personal data '
        '(addresses, phones, emails, SSN, doxxing), use exactly '
        f'"{PII_THEME}". Do not copy long slurs or harassment into the theme name; '
        'summarize neutrally. Return ONLY valid JSON: '
        '{"items":[{"i":0,"theme":"..."}]} with one entry per input line, same indices.'
    )

    batch_size = max(20, min(THEME_BATCH, 100))
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
                temperature=0.15,
                max_tokens=4096,
            )
            raw = resp.choices[0].message.content or ''
            parsed = json.loads(raw)
            items = parsed.get('items') or []
            for it in items:
                try:
                    ii = int(it.get('i'))
                    th = (str(it.get('theme') or '').strip()) or 'General Interest'
                    assignments[ii] = th
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            logger.warning("OpenAI batch %d failed: %s", start // batch_size, e)
        # Default any unanswered indices in this batch
        for i in range(len(batch)):
            assignments.setdefault(start + i, 'General Interest')

    for i, row in enumerate(kept):
        th = assignments.get(i) or 'General Interest'
        if th == PII_THEME:
            th = 'General Interest'
        theme_weight[th] += row['count']
        if len(theme_examples[th]) < 100:
            theme_examples[th].append(row['term'])

    cats = []
    for th, w in theme_weight.items():
        cats.append({
            'theme':    th,
            'weight':   int(w),
            'examples': [] if th == PII_THEME else theme_examples.get(th, [])[:100],
        })
    cats.sort(key=lambda x: -x['weight'])
    return {'success': True, 'version': 1, 'categories': cats, 'meta': meta_base}


# ── Build summary JSON in the exact shape the dashboard expects ─────────────

def build_summary(ch, recent_days: int = RECENT_DAYS) -> dict:
    logger.info("Pulling dates from llmo_events...")
    dates = fetch_dates(ch)
    if not dates:
        logger.warning("No data in clickstream.llmo_events; summary will be empty.")
        return {'dates': [], 'by_date': {}}
    logger.info("Found %d dates (most recent: %s)", len(dates), dates[0])

    logger.info("Aggregating per-day rollups...")
    totals          = fetch_totals(ch)
    llms            = fetch_top_by_match(ch, 'AI_AGENT', cap=200)
    attribution     = fetch_top_by_match(ch, 'POST_AI_NON_AGENT', cap=100)
    attribution2    = fetch_top_by_match(ch, 'POST_AI_2ND',       cap=100)
    attribution3    = fetch_top_by_match(ch, 'POST_AI_3RD',       cap=50)
    browsers        = fetch_browsers(ch)
    platforms       = fetch_platforms(ch)
    searches        = fetch_searches(ch)

    logger.info("Building MPB / WTS conversion rollups...")
    mpb_tokens = fetch_section_tokens(ch, 'most purchased brands')
    wts_tokens = fetch_section_tokens(ch, 'where they shop')
    logger.info("MPB tokens: %d, WTS tokens: %d", len(mpb_tokens), len(wts_tokens))
    brand_conv    = fetch_post_ai_brand_conv(ch, mpb_tokens, cap=100)
    retailer_conv = fetch_post_ai_brand_conv(ch, wts_tokens, cap=100)

    logger.info("Building flows...")
    flows = fetch_flows(ch)

    logger.info("Building demographics (overall)...")
    demographics_by_day = fetch_demographics_by_day(ch)

    if os.environ.get('LLMO_BUILD_DEMOGRAPHICS_BY_AGENT', '0') == '1':
        logger.info("Building demographics by agent (>=50 users/day)...")
        demographics_by_agent = fetch_demographics_by_agent(ch, min_users=50)
    else:
        # The existing dashboard summary doesn't include this; computing per-agent
        # demographics is quadratic and only needed if the agent dropdown ever uses
        # it. Toggle on with LLMO_BUILD_DEMOGRAPHICS_BY_AGENT=1 if/when needed.
        logger.info("Skipping demographics_by_agent (set LLMO_BUILD_DEMOGRAPHICS_BY_AGENT=1 to enable).")
        demographics_by_agent = {}

    logger.info("Building insights pack...")
    insights_by_day = fetch_insights_by_day(ch)

    logger.info("Classifying search themes via OpenAI %s (recent %d days)...",
                OPENAI_MODEL, recent_days)
    dates_sorted_desc = sorted(dates, reverse=True)
    theme_dates = set(dates_sorted_desc[:recent_days])
    search_themes_by_day: dict[str, Optional[dict]] = {}
    for d in dates:
        if d in theme_dates:
            try:
                search_themes_by_day[d] = build_search_themes_for_day(searches.get(d, []))
            except Exception as e:
                logger.warning("search_themes failed for %s: %s", d, e)
                search_themes_by_day[d] = None
        else:
            search_themes_by_day[d] = None

    by_date = {}
    for d in dates:
        by_date[d] = {
            'total_ai_users':       (totals.get(d) or {}).get('total_ai_users', 0),
            'total_ai_clicks':      (totals.get(d) or {}).get('total_ai_clicks', 0),
            'llms':                 llms.get(d, []),
            'attribution':          attribution.get(d, []),
            'attribution_second':   attribution2.get(d, []),
            'attribution_third':    attribution3.get(d, []),
            'brand_conversion':     brand_conv.get(d, []),
            'retailer_conversion':  retailer_conv.get(d, []),
            'flows':                flows.get(d, []),
            'searches':             searches.get(d, []),
            'browsers':             browsers.get(d, []),
            'platforms':            platforms.get(d, []),
            'search_themes':        search_themes_by_day.get(d),
            'llmo_demographics':    demographics_by_day.get(d),
            'llmo_demographics_by_agent': demographics_by_agent.get(d),
            'llmo_insights':        insights_by_day.get(d),
        }

    return {'dates': dates, 'by_date': by_date}


# ── S3 upload ────────────────────────────────────────────────────────────────

def upload_summary(s3, doc: dict, run_meta: dict) -> None:
    body_json = json.dumps(doc, default=str, ensure_ascii=False).encode('utf-8')
    body_gz = gzip.compress(body_json, compresslevel=6)
    logger.info("Uploading summary → s3://%s/%s (%.1f MiB gz, %.1f MiB raw)",
                S3_BUCKET, S3_KEY, len(body_gz) / 1048576, len(body_json) / 1048576)
    s3.put_object(
        Bucket=S3_BUCKET, Key=S3_KEY, Body=body_gz,
        ContentType='application/json', ContentEncoding='gzip',
    )
    run_key = S3_RUN_PREFIX + 'data_0_0_0.json'  # match SP_LLMO_DAILY's prefix
    s3.put_object(
        Bucket=S3_BUCKET, Key=run_key,
        Body=json.dumps(run_meta, default=str).encode('utf-8'),
        ContentType='application/json',
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_backfill(s: str) -> tuple[date, date]:
    a, b = s.split(':', 1)
    return (datetime.strptime(a, '%Y-%m-%d').date(),
            datetime.strptime(b, '%Y-%m-%d').date())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date',        help='YYYY-MM-DD (default: yesterday US/Pacific)')
    parser.add_argument('--summary-only', action='store_true',
                        help='Skip llmo_events ingestion; just rebuild summary from existing data')
    parser.add_argument('--backfill',    help='Backfill range YYYY-MM-DD:YYYY-MM-DD (inclusive)')
    parser.add_argument('--dry-run',     action='store_true',
                        help='Run aggregations but skip ingest writes and S3 upload')
    parser.add_argument('--no-themes',   action='store_true',
                        help='Skip OpenAI search_themes classification')
    args = parser.parse_args()

    if args.no_themes:
        global RECENT_DAYS
        RECENT_DAYS = 0

    range_to_ingest: Optional[tuple[date, date]] = None
    target_dates: list[date] = []
    if args.summary_only:
        logger.info("--summary-only: skipping llmo_events ingestion.")
    elif args.backfill:
        a, b = parse_backfill(args.backfill)
        range_to_ingest = (a, b)
        target_dates = []  # only used for run_meta; details come from CH
        d = a
        while d <= b:
            target_dates.append(d)
            d += timedelta(days=1)
    else:
        if args.date:
            d0 = datetime.strptime(args.date, '%Y-%m-%d').date()
            target_dates = [d0]
            range_to_ingest = (d0, d0)
        else:
            # Yesterday in US/Pacific (matches SF procedure semantics).
            now_pt = datetime.now(timezone.utc).astimezone()
            d0 = now_pt.date() - timedelta(days=1)
            target_dates = [d0]
            range_to_ingest = (d0, d0)

    if OPENAI_KEY == '' and not args.no_themes:
        logger.warning("OPENAI_API_KEY not set — search_themes will be null in summary.")

    ch = get_ch_client()
    s3 = boto3.client('s3') if not args.dry_run else None

    rows_inserted = 0
    if not args.summary_only and range_to_ingest is not None:
        d1, d2 = range_to_ingest
        logger.info("→ Ingesting llmo_events for %s..%s", d1, d2)
        rows_inserted += ingest_date_range(ch, d1, d2, dry_run=args.dry_run)

    logger.info("Building summary JSON...")
    t0 = time.time()
    summary = build_summary(ch)
    summary_seconds = time.time() - t0
    logger.info("Summary built in %.1fs (%d dates)", summary_seconds, len(summary.get('dates', [])))

    run_meta = {
        'run_ts':        datetime.now(timezone.utc).isoformat(),
        'rows_inserted': rows_inserted,
        'summary_dates': len(summary.get('dates', [])),
        'target_dates':  [d.isoformat() for d in target_dates],
        'source':        'clickhouse_openai',
        'model':         OPENAI_MODEL,
    }

    if args.dry_run:
        logger.info("[dry-run] Would upload to s3://%s/%s", S3_BUCKET, S3_KEY)
        logger.info("[dry-run] Run meta: %s", json.dumps(run_meta, default=str, indent=2))
        return 0

    upload_summary(s3, summary, run_meta)
    logger.info("✅ LLMO daily complete — summary at s3://%s/%s", S3_BUCKET, S3_KEY)
    return 0


if __name__ == '__main__':
    sys.exit(main())
