"""
clickhouse_connector.py  —  Drop-in replacement for snowflake.connector

Makes ClickHouse look identical to the Snowflake API:
    conn = connect_clickhouse()
    cur  = conn.cursor()
    cur.execute("SELECT ...")
    rows = cur.fetchall()
    conn.close()

Also translates common Snowflake SQL patterns to ClickHouse SQL automatically.

Usage in any script — change ONE import:
    # OLD:
    conn = connect_snowflake()
    # NEW:
    from migration.clickhouse_connector import connect_clickhouse as connect_snowflake
    conn = connect_snowflake()
"""

import os
import re
import time
import threading
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Optional

import clickhouse_connect
import pandas as pd

logger = logging.getLogger(__name__)


# ── Cross-tool ClickHouse query throttle ──────────────────────────────────────
# Caps the number of HEAVY (SELECT/WITH/SHOW) queries that hit CH simultaneously
# across every workload that imports this connector — Profile Analysis (BG),
# Subscriber IQ, SF→LF Conversion, etc. The current ClickHouse box (Hetzner
# AX162-S, 128 GiB RAM, 16 cores) starts to wobble somewhere around 10-15
# concurrent heavy aggregations on `clickstream_final`. With the gate, the
# *Nth+1* caller waits cheaply at this semaphore (in-process, microseconds)
# instead of piling on CH, where it would burn server RAM, queue at the network
# thread pool, and slow down EVERY other in-flight query.
#
# Tune via env var `BG_CLICKHOUSE_CONCURRENCY` (default 10). Set to 0 to
# disable the gate entirely (not recommended in prod).
#
# DDL (CREATE/DROP/ALTER), INSERTs, and small `command()` calls are NOT gated —
# they're fast and don't compete for CH working memory.
_CH_QUERY_CONCURRENCY = max(0, int(os.environ.get('BG_CLICKHOUSE_CONCURRENCY', '10')))
_CH_QUERY_SEMAPHORE: Optional[threading.BoundedSemaphore] = (
    threading.BoundedSemaphore(_CH_QUERY_CONCURRENCY) if _CH_QUERY_CONCURRENCY > 0 else None
)
_CH_QUERY_WAIT_LOG_THRESHOLD_S = 1.0  # log a warning when wait > this many seconds
_CH_QUERY_STATS = {
    'inflight':   0,
    'total_run':  0,
    'total_wait': 0,
    'lock':       threading.Lock(),
}


@contextmanager
def _ch_query_slot(sql_preview: str):
    """Acquire a heavy-query slot on the cross-tool CH semaphore.

    Logs a warning if the caller had to wait more than
    `_CH_QUERY_WAIT_LOG_THRESHOLD_S` seconds — that's the signal that you're
    bottlenecked on CH capacity and should either raise the limit or scale CH.
    """
    if _CH_QUERY_SEMAPHORE is None:
        yield
        return
    t_wait_start = time.monotonic()
    _CH_QUERY_SEMAPHORE.acquire()
    waited = time.monotonic() - t_wait_start
    with _CH_QUERY_STATS['lock']:
        _CH_QUERY_STATS['inflight']   += 1
        _CH_QUERY_STATS['total_run']  += 1
        _CH_QUERY_STATS['total_wait'] += waited
        inflight_now = _CH_QUERY_STATS['inflight']
    try:
        if waited > _CH_QUERY_WAIT_LOG_THRESHOLD_S:
            logger.warning(
                "ClickHouse throttle: waited %.1fs for slot (now %d/%d in-flight) — "
                "consider raising BG_CLICKHOUSE_CONCURRENCY or scaling CH. SQL: %s",
                waited, inflight_now, _CH_QUERY_CONCURRENCY, sql_preview[:140],
            )
        yield
    finally:
        with _CH_QUERY_STATS['lock']:
            _CH_QUERY_STATS['inflight'] -= 1
        _CH_QUERY_SEMAPHORE.release()


def get_clickhouse_throttle_stats() -> dict:
    """Return current CH-throttle stats. Useful for /api/queue/status endpoints."""
    with _CH_QUERY_STATS['lock']:
        return {
            'capacity':        _CH_QUERY_CONCURRENCY,
            'inflight':        _CH_QUERY_STATS['inflight'],
            'total_run':       _CH_QUERY_STATS['total_run'],
            'total_wait_secs': round(_CH_QUERY_STATS['total_wait'], 2),
        }

CH_HOST       = os.environ.get('CH_HOST',       '168.119.215.48')
CH_PORT       = int(os.environ.get('CH_PORT',   '8123'))
CH_USER       = os.environ.get('CH_USER',       'bgapp')
CH_PASSWORD   = os.environ.get('CH_PASSWORD',   '4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ')
CH_DATABASE   = os.environ.get('CH_DATABASE',   'clickstream')


# Default per-query settings applied to every connection. These match the
# heavy-aggregation profile used by Subscriber IQ and SF→LF Conversion
# (long full-table scans of clickstream_final). Override per-call by passing
# `settings={...}` to `connect_clickhouse(...)` or per-query via
# `cur.execute(sql, settings={...})`.
DEFAULT_QUERY_SETTINGS: dict[str, Any] = {
    # 2-hour cap; full-year profile scans with 29 brand LIKE conditions
    # regularly exceed 30 min on the 48B-row clickstream table.
    'max_execution_time':           7200,
    # Hard cap per query: 80 GiB (was 50 GiB).
    #
    # Host inventory as of 2026-05-18:
    #   - 168.119.215.48 — 1,133 GiB total RAM (NOT 755 as the prior
    #     comment claimed; the connector had been tuned for an older,
    #     smaller box).
    #   - ClickHouse server-side `max_server_memory_usage` = 963 GiB
    #     (85% of host RAM).
    #   - Server-side `max_memory_usage` for the bgapp user = 466 GiB
    #     (effectively no per-user cap — the connector's cap is the
    #     active constraint).
    #
    # Sizing math:
    #   - Reserve ~150 GiB for OS page cache (CH leans on this heavily
    #     for hot-data table scans).
    #   - Usable budget for query memory ≈ 983 GiB.
    #   - Heaviest observed single query (Costco PRE_SAMPLED_CLICKSTREAM
    #     CTAS, 47B-row scan + window-function sort): 94 GiB peak.
    #   - At 80 GiB cap × 10 in-process concurrent slots = 800 GiB peak.
    #     Fits with ~180 GiB headroom.
    #   - For parallel profile runs (launched as separate processes,
    #     each with its own 10-slot semaphore), the practical ceiling
    #     is ~10 simultaneous profile pipelines before the 800 GiB
    #     ceiling becomes uncomfortable. See `/tmp/run_parallel.py`
    #     for the staged launcher.
    #
    # The spill settings below keep the in-memory portion bounded —
    # large group-by/sort spills to disk before hitting this cap, so
    # legitimate huge aggregations still finish (just slower).
    #
    # Override per-query via `settings={...}` when a single-shot job
    # (admin reconciliation, full-clickstream backfill) needs more
    # headroom. Override globally via `BG_CH_QUERY_MEMORY_GIB` env var.
    'max_memory_usage':             int(os.environ.get(
        'BG_CH_QUERY_MEMORY_GIB', '80')) * 1024 * 1024 * 1024,
    # Spill to disk on large group-by/sort instead of crashing.
    'max_bytes_before_external_group_by': 100 * 1024 * 1024 * 1024,
    'max_bytes_before_external_sort':     100 * 1024 * 1024 * 1024,
    'max_threads':                  48,
    # Stream rows in primary-key order — clickstream_final is sorted by
    # (DELIVERED, UID, COMMON_NAME), so date-bounded queries skip the
    # full-merge step.
    'optimize_read_in_order':        1,
    # If the data is sorted by the GROUP BY columns, skip the hash table.
    # Big win on time-series rollups (per-month, per-day).
    'optimize_aggregation_in_order': 1,
    # Let the planner choose between parallel_hash / hash / partial_merge /
    # grace_hash on a per-query basis. parallel_hash is by far the fastest
    # for the small-build-side joins we usually do; grace_hash is the
    # graceful fallback when the build side is enormous.
    'join_algorithm':               'parallel_hash,hash,partial_merge,grace_hash',
    # Larger batches over HTTP — fewer round-trips for big result sets.
    'max_block_size':               65536,
    # Profile Analysis builds long inline IN-lists of hostnames (one per
    # quick-select). The default 256 KiB query parser limit blows up at
    # ~262 KB with `Max query size exceeded`. 16 MiB is plenty headroom
    # and well under the server-side hard limit.
    'max_query_size':               16 * 1024 * 1024,
    # Apply ALTER mutations synchronously so callers can read-after-write
    # safely. Profile Analysis doesn't issue mutations but other migration
    # scripts on this connector do.
    'mutations_sync':               2,
}


def connect_clickhouse(settings: Optional[dict] = None):
    """Connect to ClickHouse. Returns a Snowflake-compatible wrapper.

    `settings` (optional) overrides/augments DEFAULT_QUERY_SETTINGS for
    every query made on this connection. Use this when one workload (e.g.
    Subscriber IQ) needs a longer timeout or a different memory ceiling
    than the defaults.
    """
    merged_settings = dict(DEFAULT_QUERY_SETTINGS)
    if settings:
        merged_settings.update(settings)
    # Pin an explicit session_id per connection so CREATE TEMPORARY TABLE
    # survives across separate HTTP requests on the same Client. Without
    # this, pandas read_sql (which opens its own cursor for each query)
    # silently lands on a fresh anonymous session and any temp table
    # built by an earlier cur.execute() is gone -> "Unknown table
    # expression identifier TEMP_XXX". A per-connection uuid keeps
    # parallel connections isolated while making every cursor on this
    # connection share the same session.
    #
    # session_timeout MUST be raised well above ClickHouse's 60s default.
    # The Ticket Sales Tracker's TEMP_DEMOS_WITH_THEATER CTAS routinely
    # runs >60s on real movie panels (large INNER JOIN over
    # userdata.user_data_sanitized) and any subsequent SELECT on that
    # temp table arrives AFTER the session has expired, returning a
    # "Unknown table expression identifier" error. 3600s is the server's
    # current max_session_timeout cap.
    session_id = f"bg-{uuid.uuid4().hex}"
    merged_settings.setdefault('session_timeout', 3600)
    client = clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        session_id=session_id,
        connect_timeout=30,
        send_receive_timeout=10800,
        settings=merged_settings,
    )
    return ClickHouseConnection(client, query_settings=merged_settings)


# ── SQL translation ───────────────────────────────────────────────────────────

TABLE_MAP = {
    # Clickstream
    r'PROCESSEDCLICKSTREAM\.PUBLIC\.CLICKSTREAM_FINAL':  'clickstream.clickstream_final',
    r'PROCESSEDCLICKSTREAM\.PUBLIC\.AGGREGATED_TICKERS': 'tickers.aggregated_tickers',

    # User data
    r'PROCESSEDUSERFILES\.PUBLIC\.USER_DATA_SANITIZED':  'userdata.user_data_sanitized',
    r'PROCESSEDUSERFILES\.PUBLIC\.USER_DATA\b':          'userdata.user_data',

    # Reference
    r'BEHAVIORALGRAPH\.PUBLIC\.HOST_MAPPING':            'reference.host_mapping',
    r'BEHAVIORALGRAPH\.PUBLIC\.ORDER_CONFIRMS':          'reference.order_confirms',
    r'CLICKBRAND\.CB_WAREHOUSE\.SEARCH_TEXT_MAPPING':    'reference.search_text_mapping',
    r'CLICKBRAND\.CB_WAREHOUSE\.BRAND':                  'reference.brand',
    r'CLICKBRAND\.CB_WAREHOUSE\.TICKER':                 'reference.ticker',
}

FUNC_REPLACEMENTS = [
    # ZEROIFNULL → ifNull(x, 0)
    (r'ZEROIFNULL\s*\(([^)]+)\)',        r'ifNull(\1, 0)'),
    # NVL → ifNull
    (r'\bNVL\s*\(([^,]+),\s*([^)]+)\)',  r'ifNull(\1, \2)'),
    # IFF → if
    (r'\bIFF\s*\(',                      r'if('),
    # CONVERT_TIMEZONE
    (r"CONVERT_TIMEZONE\s*\([^,]+,\s*'([^']+)'\s*,\s*([^)]+)\)",
     r"toTimeZone(\2, '\1')"),
    # TO_TIMESTAMP
    (r'\bTO_TIMESTAMP\s*\(([^)]+)\)',   r'toDateTime(\1)'),
    # NOTE: DATEADD, DATEDIFF, TO_DATE, TO_CHAR are handled separately below
    # via balanced-paren scanners (`_rewrite_dateadd_datediff_todate`,
    # `_rewrite_to_char`) because their arguments can contain nested function
    # calls with commas/parens that a regex `[^)]+` can't capture safely.
    # CURRENT_DATE → today()
    (r'\bCURRENT_DATE\(\)',             r'today()'),
    (r'\bCURRENT_DATE\b',              r'today()'),
    # CURRENT_TIMESTAMP → now()
    (r'\bCURRENT_TIMESTAMP\(\)',        r'now()'),
    (r'\bCURRENT_TIMESTAMP\b',         r'now()'),
    # COUNT(DISTINCT x) → uniqExact(x)
    (r'\bCOUNT\s*\(\s*DISTINCT\s+([^)]+)\)', r'uniqExact(\1)'),
    # ILIKE (ClickHouse supports it natively)
    (r'\bILIKE\b', r'ilike'),
    # LISTAGG → groupArray + arrayStringConcat
    (r"LISTAGG\s*\(\s*([^,)]+)\s*,\s*'([^']+)'\s*\)\s*WITHIN\s+GROUP\s*\([^)]+\)",
     r"arrayStringConcat(groupArray(\1), '\2')"),
    # SPLIT_PART (literal index only; dynamic index handled separately)
    (r'SPLIT_PART\s*\(([^,]+),\s*([^,]+),\s*(\d+)\)',
     r'splitByString(\2, \1)[\3]'),
    # TRY_CAST → accurateCastOrNull
    (r'TRY_CAST\s*\(([^)]+)\s+AS\s+([^)]+)\)',
     r"accurateCastOrNull(\1, '\2')"),
    # TRY_TO_NUMBER / TRY_TO_DECIMAL → toFloat64OrNull
    (r'\bTRY_TO_NUMBER\s*\(([^)]+)\)',  r'toFloat64OrNull(toString(\1))'),
    (r'\bTRY_TO_DECIMAL\s*\(([^)]+)\)', r'toFloat64OrNull(toString(\1))'),
    # REGEXP_SUBSTR(str, pattern) → extract(str, pattern)
    (r"REGEXP_SUBSTR\s*\(\s*([^,]+),\s*'([^']+)'\s*\)",
     r"extract(\1, '\2')"),
    # REGEXP_SUBSTR with 6 args (str, pat, pos, occ, flags, group) → extract
    (r"REGEXP_SUBSTR\s*\(\s*([^,]+),\s*'([^']+)'\s*,\s*\d+\s*,\s*\d+\s*,\s*'[^']*'\s*,\s*\d+\s*\)",
     r"extract(\1, '\2')"),
    # ARRAY_SIZE(x) → length(x)
    (r'\bARRAY_SIZE\s*\(',              r'length('),
    # MEDIAN → median
    (r'\bMEDIAN\s*\(',                  r'median('),
    # DATE(x) as Snowflake-style date cast → toDate(x)
    (r'\bDATE\s*\(\s*([^)]+)\)',        r'toDate(\1)'),
]

DATE_TRUNC_MAP = {
    'day':    'toStartOfDay',
    'week':   'toStartOfWeek',
    'month':  'toStartOfMonth',
    'year':   'toStartOfYear',
    'hour':   'toStartOfHour',
    'minute': 'toStartOfMinute',
}

# Snowflake types → ClickHouse types for DDL
TYPE_MAP = {
    'VARCHAR':   'String',
    'STRING':    'String',
    'TEXT':      'String',
    'NUMBER':    'Float64',
    'INTEGER':   'Int64',
    'INT':       'Int64',
    'FLOAT':     'Float64',
    'DOUBLE':    'Float64',
    'BOOLEAN':   'UInt8',
    'DATE':      'Date',
    'TIMESTAMP': 'DateTime',
    'VARIANT':   'String',
}

# Snowflake :: cast types → ClickHouse equivalents
CAST_TYPE_MAP = {
    'DATE':      'Date',
    'VARCHAR':   'String',
    'STRING':    'String',
    'NUMBER':    'Float64',
    'INTEGER':   'Int64',
    'INT':       'Int64',
    'FLOAT':     'Float64',
    'TIMESTAMP': 'DateTime',
}


def translate_sql(sql: str) -> str:
    """Translate Snowflake SQL to ClickHouse SQL."""
    result = sql

    # Table name replacements (case-insensitive)
    for pattern, replacement in TABLE_MAP.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Balanced-paren rewrites for date functions whose args can themselves
    # contain nested function calls (e.g. DATEADD(MONTH, 1, TO_DATE(x || y, 'fmt'))).
    # These run BEFORE the regex-based FUNC_REPLACEMENTS to avoid a regex
    # like `[^)]+` greedily eating an inner ')'.
    result = _rewrite_balanced_funcs(result)

    # Function replacements
    for pattern, replacement in FUNC_REPLACEMENTS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # DATE_TRUNC special handling
    def replace_date_trunc(m):
        unit = m.group(1).lower()
        expr = m.group(2)
        fn = DATE_TRUNC_MAP.get(unit, 'toStartOfDay')
        return f'{fn}({expr})'
    result = re.sub(
        r"DATE_TRUNC\s*\(\s*'(\w+)'\s*,\s*([^)]+)\)",
        replace_date_trunc, result, flags=re.IGNORECASE
    )

    # Snowflake ::TYPE casts → ClickHouse-compatible types
    def replace_cast(m):
        sf_type = m.group(1).upper().strip()
        ch_type = CAST_TYPE_MAP.get(sf_type, sf_type)
        return f'::{ch_type}'
    result = re.sub(r'::\s*(DATE|VARCHAR|STRING|NUMBER|INTEGER|INT|FLOAT|TIMESTAMP)\b',
                    replace_cast, result, flags=re.IGNORECASE)

    # Remove Snowflake-specific clauses
    result = re.sub(r'\bWITHIN\s+GROUP\s*\([^)]+\)', '', result, flags=re.IGNORECASE)

    # Remove schema prefix for session-level tables
    result = re.sub(r'\bPUBLIC\.', '', result, flags=re.IGNORECASE)

    # ── QUALIFY → subquery rewrite ────────────────────────────────────────
    # Snowflake QUALIFY is not supported in ClickHouse.
    # Strategy: wrap the SELECT in a subquery and convert QUALIFY to WHERE.
    result = _rewrite_qualify(result)

    # ── LIKE ... ESCAPE 'x'  → strip ESCAPE clause ────────────────────────
    # ClickHouse's LIKE uses '\' as the default escape character, matching
    # Snowflake's `ESCAPE '\\'`. ClickHouse rejects the explicit ESCAPE
    # clause with a SYNTAX_ERROR, so we drop it. Safe as long as callers
    # use '\' for escapes (bg.py's `_escape_brand_for_sql` does).
    result = re.sub(
        r"\s+ESCAPE\s+'(?:\\\\|\\|[^'])'",
        '',
        result,
        flags=re.IGNORECASE,
    )

    # ── SAMPLE → ClickHouse LIMIT / strip ─────────────────────────────────
    # Snowflake: FROM table SAMPLE (N ROWS)  → explicit row cap
    def replace_sample_rows(m):
        n = m.group(1).strip()
        return f'ORDER BY rand() LIMIT {n}'
    result = re.sub(r'\bSAMPLE\s*\(\s*(\d+)\s+ROWS?\s*\)',
                    replace_sample_rows, result, flags=re.IGNORECASE)
    # Percentage-based: FROM table SAMPLE (N) / SAMPLE (N.N)
    # Convert to a deterministic hash-bucket filter on UID.
    #
    # IMPORTANT: we do NOT wrap in a subquery — that prevents ClickHouse
    # from using partition pruning on DELIVERED when the date filter is in
    # the outer WHERE.  Instead we strip the SAMPLE clause and inject the
    # hash condition directly into the query's WHERE clause so all
    # predicates live at the same level and the optimizer can prune
    # partitions + use primary-key ordering.
    _SQL_KW = frozenset({
        'WHERE', 'INNER', 'LEFT', 'RIGHT', 'OUTER', 'CROSS', 'JOIN',
        'ON', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'QUALIFY', 'AND',
        'OR', 'UNION', 'EXCEPT', 'INTERSECT', 'SET', 'INTO', 'CREATE',
        'AS', 'WITH', 'FULL', 'NATURAL', 'USING', 'BETWEEN', 'SELECT',
        'FROM', 'TABLE', 'INSERT', 'UPDATE', 'DELETE', 'DROP', 'REPLACE',
        'TEMP', 'TEMPORARY', 'NOT', 'NULL', 'IS', 'CASE', 'WHEN', 'THEN',
        'ELSE', 'END', 'IN', 'EXISTS', 'LIKE', 'ILIKE', 'PREWHERE',
    })
    _pending_hash_filters = []

    def _sample_to_hash(m):
        table = m.group(1)
        pct = float(m.group(2))
        bucket = max(1, min(10000, int(round(pct * 100))))

        after = m.string[m.end():]

        # Inside a BG.py subquery like FROM (SELECT * FROM t SAMPLE(N)):
        # can't inject into outer WHERE, so keep inline with PREWHERE.
        if re.match(r'\s*\)', after):
            return (
                f'FROM {table}\n'
                f'    PREWHERE cityHash64(UID) % 10000 < {bucket}'
            )

        # Normal case: flatten. Detect optional table alias.
        alias_m = re.match(r'\s+(\w+)', after)
        alias = None
        if alias_m and alias_m.group(1).upper() not in _SQL_KW:
            alias = alias_m.group(1)

        uid_ref = f'{alias}.UID' if alias else 'UID'
        _pending_hash_filters.append(
            f'cityHash64({uid_ref}) % 10000 < {bucket}')
        return f'FROM {table}'

    result = re.sub(
        r'\bFROM\s+([\w.]+)\s+SAMPLE\s*\(\s*(\d+(?:\.\d+)?)\s*\)',
        _sample_to_hash, result, flags=re.IGNORECASE,
    )

    # Inject hash filters into WHERE. ClickHouse's auto-PREWHERE
    # (optimize_move_to_prewhere=1) will move the DELIVERED date
    # conditions to PREWHERE automatically since DELIVERED is the
    # first column in the primary key. Execution order becomes:
    #   1. Partition prune by DELIVERED (skip non-matching months)
    #   2. PREWHERE on DELIVERED within partitions (granule skip)
    #   3. Read remaining columns, apply hash + brand filters
    for hf in _pending_hash_filters:
        where_m = re.search(r'\bWHERE\b', result, re.IGNORECASE)
        if where_m:
            pos = where_m.end()
            result = result[:pos] + f' {hf} AND' + result[pos:]
        else:
            for kw in ('GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT'):
                kw_m = re.search(rf'\b{kw}\b', result, re.IGNORECASE)
                if kw_m:
                    pos = kw_m.start()
                    result = result[:pos] + f'WHERE {hf}\n' + result[pos:]
                    break
            else:
                result += f'\nWHERE {hf}'

    # Anything remaining (rare) gets stripped so the query parses.
    result = re.sub(r'\s+SAMPLE\s*\(\s*\d+(?:\.\d+)?\s*\)',
                    '', result, flags=re.IGNORECASE)

    # ── LATERAL FLATTEN → ARRAY JOIN ──────────────────────────────────────
    _flatten_aliases = []

    def _replace_flatten(m):
        col = m.group('col')
        delim = m.group('delim')
        alias = m.group('alias')
        _flatten_aliases.append(alias)
        return f"\nARRAY JOIN splitByString({delim}, {col}) AS {alias}_value"

    result = re.sub(
        r',?\s*(?:CROSS\s+JOIN\s+)?LATERAL\s+FLATTEN\s*\(\s*input\s*=>\s*SPLIT\s*\('
        r'(?P<col>[^,]+),\s*(?P<delim>[^)]+)\)\s*\)\s*(?:AS\s+)?(?P<alias>\w+)',
        _replace_flatten, result, flags=re.IGNORECASE
    )
    for alias in _flatten_aliases:
        result = re.sub(rf'\b{alias}\.value\b', f'{alias}_value', result)

    # ── CREATE OR REPLACE TEMP TABLE → CH temporary table ─────────────────
    # Snowflake "CREATE OR REPLACE" semantics = drop-then-create. ClickHouse
    # has no native equivalent for temporary tables, so we emit a paired
    # DROP + CREATE (the cursor's compound-statement path handles the `;\n`).
    # The OLD `CREATE TEMPORARY TABLE IF NOT EXISTS` translation was buggy:
    # if the same connection reused a temp table name (retry, multi-stage
    # job), the second CREATE was a silent no-op and downstream reads would
    # see stale data from the first run.
    # Self-referential case (CREATE OR REPLACE TEMP TABLE X AS ... FROM X):
    # the naive DROP X then CREATE X AS SELECT FROM X breaks because X is gone
    # before the SELECT runs. Snapshot via a swap temp table first so the
    # rebuild is safe. This pattern is common in iterative pipelines that
    # apply a cap/sample/transform onto their own working table.
    _swap_targets: list[tuple[str, str]] = []  # [(swap_name, real_name), ...]

    def _ctas_self_aware(m):
        name = m.group(1)
        after = result[m.end():]
        after_sanitized = re.sub(r"'[^']*'", "''", after)
        stop = re.search(r';\s*$', after_sanitized, re.MULTILINE)
        scope = after_sanitized[:stop.start()] if stop else after_sanitized
        is_self_ref = bool(re.search(
            rf'\b(?:FROM|JOIN)\s+{re.escape(name)}\b',
            scope, re.IGNORECASE,
        ))
        if is_self_ref:
            swap = f'_ch_swap_{name}'
            _swap_targets.append((swap, name))
            return (
                f'DROP TABLE IF EXISTS {swap};\n'
                f'CREATE TEMPORARY TABLE {swap} ENGINE = Memory AS'
            )
        return (
            f'DROP TABLE IF EXISTS {name};\n'
            f'CREATE TEMPORARY TABLE {name} ENGINE = Memory AS'
        )

    result = re.sub(
        r'CREATE\s+OR\s+REPLACE\s+TEMP(?:ORARY)?\s+TABLE\s+(\w+)\s+AS\b',
        _ctas_self_aware, result, flags=re.IGNORECASE,
    )
    for swap, name in _swap_targets:
        result = result.rstrip().rstrip(';') + (
            f';\nDROP TABLE IF EXISTS {name};\n'
            f'CREATE TEMPORARY TABLE {name} ENGINE = Memory AS '
            f'SELECT * FROM {swap};\n'
            f'DROP TABLE IF EXISTS {swap}'
        )
    # With explicit column DDL (no AS) — translate Snowflake types
    def replace_temp_ddl(m):
        name = m.group(1)
        col_defs = m.group(2)
        translated_cols = _translate_column_defs(col_defs)
        return f'CREATE TEMPORARY TABLE IF NOT EXISTS {name} ({translated_cols}) ENGINE = Memory'
    result = re.sub(
        r'CREATE\s+OR\s+REPLACE\s+TEMP(?:ORARY)?\s+TABLE\s+(\w+)\s*\(([^)]+)\)',
        replace_temp_ddl, result, flags=re.IGNORECASE
    )
    # Non-temp: CREATE OR REPLACE TABLE → DROP + CREATE
    result = re.sub(
        r'CREATE\s+OR\s+REPLACE\s+TABLE\s+(\w+)\s+AS\b',
        r'DROP TABLE IF EXISTS \1;\nCREATE TABLE \1 ENGINE = Memory AS',
        result, flags=re.IGNORECASE
    )

    # ── SELECT ... FROM VALUES ────────────────────────────────────────────
    # Snowflake: SELECT column1 AS UID FROM VALUES ('a'),('b')
    # ClickHouse: SELECT arrayJoin(['a','b']) AS UID
    def replace_from_values(m):
        alias = m.group(1)
        values_str = m.group(2)
        items = re.findall(r"\('([^']*)'\)", values_str)
        if items:
            arr = ', '.join(f"'{v}'" for v in items)
            return f"SELECT arrayJoin([{arr}]) AS {alias}"
        return m.group(0)
    result = re.sub(
        r'SELECT\s+column1\s+AS\s+(\w+)\s+FROM\s+VALUES\s*(\([^;]+)',
        replace_from_values, result, flags=re.IGNORECASE
    )

    # Snowflake (also seen in Subscriber IQ): "SELECT col FROM VALUES (1),(2),(3) AS t(col)"
    # → ClickHouse: "SELECT arrayJoin([1, 2, 3]) AS col"
    def replace_from_values_aliased(m):
        select_col = m.group(1).strip()
        values_str = m.group(2)
        alias_col = m.group(3).strip()
        # Pull every (literal) item — strings or numbers
        items = re.findall(r"\(\s*('[^']*'|[^),\s]+)\s*\)", values_str)
        if not items or select_col != alias_col:
            return m.group(0)
        arr = ', '.join(items)
        return f"SELECT arrayJoin([{arr}]) AS {alias_col}"
    result = re.sub(
        r"SELECT\s+(\w+)\s+FROM\s+VALUES\s+(\([^)]+\)(?:\s*,\s*\([^)]+\))*)\s+AS\s+\w+\s*\(\s*(\w+)\s*\)",
        replace_from_values_aliased, result, flags=re.IGNORECASE
    )

    # ── TO_CHAR(date_expr, 'format') → formatDateTime ─────────────────────
    # Run AFTER DATE_TRUNC translation so nested DATE_TRUNC has been
    # rewritten to toStartOfMonth(...) etc. before we wrap it.
    result = _rewrite_to_char(result)

    # ── Snowflake `||` string concat → ClickHouse concat() ────────────────
    # In Snowflake, `||` is ALWAYS string concatenation (Snowflake uses OR
    # for boolean OR). ClickHouse's `||` is logical OR — using it on strings
    # silently produces wrong results. We translate every `a || b` occurrence
    # (repeatedly, to handle chains like `a || b || c`) into `concat(a, b)`.
    result = _rewrite_pipe_concat(result)

    # ── REGEXP operator → match() ─────────────────────────────────────────
    # Snowflake: WHERE col REGEXP 'pattern'
    # ClickHouse: WHERE match(col, 'pattern')
    def replace_regexp_op(m):
        col = m.group(1).strip()
        pattern = m.group(2)
        return f"match({col}, '{pattern}')"
    result = re.sub(
        r"(\b\w+(?:\([^)]*\))?)\s+REGEXP\s+'([^']+)'",
        replace_regexp_op, result, flags=re.IGNORECASE
    )

    # ── UPDATE → ALTER TABLE ... UPDATE ───────────────────────────────────
    # Only for non-temp tables; Memory engine temp tables actually support UPDATE
    # in recent ClickHouse versions, so we leave UPDATE as-is and handle in execute()

    # ── Snowflake metadata queries → no-ops ───────────────────────────────
    # LAST_QUERY_ID(), CURRENT_WAREHOUSE(), INFORMATION_SCHEMA.WAREHOUSES,
    # SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY — return empty results
    if re.search(r'LAST_QUERY_ID|CURRENT_WAREHOUSE|INFORMATION_SCHEMA\.WAREHOUSES|'
                 r'SNOWFLAKE\.ACCOUNT_USAGE', result, re.IGNORECASE):
        return "SELECT 'N/A' AS result"

    return result


_DATEADD_UNIT_TO_FN = {
    'day':    'addDays',
    'days':   'addDays',
    'week':   'addWeeks',
    'weeks':  'addWeeks',
    'month':  'addMonths',
    'months': 'addMonths',
    'year':   'addYears',
    'years':  'addYears',
    'hour':   'addHours',
    'hours':  'addHours',
    'minute': 'addMinutes',
    'minutes':'addMinutes',
    'second': 'addSeconds',
    'seconds':'addSeconds',
}


def _split_top_level_args(s: str) -> list[str]:
    """Split a comma-separated arg list at top level (depth 0), respecting
    parens and string literals."""
    out: list[str] = []
    depth = 0
    in_str = False
    start = 0
    for i, c in enumerate(s):
        if c == "'" and (i == 0 or s[i - 1] != "\\"):
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c == ',' and depth == 0:
            out.append(s[start:i].strip())
            start = i + 1
    tail = s[start:].strip()
    if tail:
        out.append(tail)
    return out


def _find_matching_paren(sql: str, open_pos: int) -> int:
    """Given the index of an '(' in sql, return index of its matching ')',
    or -1 if unbalanced. Respects string literals."""
    depth = 1
    in_str = False
    j = open_pos + 1
    while j < len(sql):
        c = sql[j]
        if c == "'" and (j == 0 or sql[j - 1] != "\\"):
            in_str = not in_str
        elif not in_str:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    return -1


def _rewrite_balanced_funcs(sql: str) -> str:
    """Rewrite DATEADD, DATEDIFF, and TO_DATE using a proper balanced-paren
    scanner. Repeats until no further rewrites happen so nested calls
    (e.g. DATEADD(month, 1, TO_DATE(...))) get the inner one converted first.

    NOTE: case-SENSITIVE match on the function names. Snowflake SQL
    consistently uses uppercase `DATEADD`/`DATEDIFF`/`TO_DATE`, while our
    ClickHouse replacements are camelCase (`addMonths`, `dateDiff`, `toDate`).
    A case-insensitive match here would re-find already-translated lowercase
    forms, burn iterations, and leave the real Snowflake calls untouched.
    """
    func_pat = re.compile(r"\b(DATEADD|DATEDIFF|TO_DATE)\s*\(")
    for _ in range(20):  # bound iterations — depth of date-func nesting we'd ever see
        m = func_pat.search(sql)
        if not m:
            break
        # Find the right "innermost" call: scan for any match whose args
        # contain no further DATEADD/DATEDIFF/TO_DATE call. By rewriting
        # innermost-first we avoid the broken-paren issue.
        target: tuple[int, int, str, str] | None = None  # (start, end, fname, args)
        for mm in func_pat.finditer(sql):
            open_paren = mm.end() - 1
            close_paren = _find_matching_paren(sql, open_paren)
            if close_paren == -1:
                continue
            inner = sql[open_paren + 1:close_paren]
            if not func_pat.search(inner):
                target = (mm.start(), close_paren + 1, mm.group(1).upper(), inner)
                break
        if not target:
            break
        start, end, fname, inner = target
        args = _split_top_level_args(inner)
        replacement = _convert_date_func(fname, args)
        if replacement is None:
            # Couldn't translate — bail to avoid infinite loop. Use a
            # placeholder marker and retry next pass without rematching.
            break
        sql = sql[:start] + replacement + sql[end:]
    return sql


def _convert_date_func(fname: str, args: list[str]) -> Optional[str]:
    """Convert a single DATEADD/DATEDIFF/TO_DATE call (after splitting args
    at top level) to its ClickHouse equivalent."""
    if fname == 'TO_DATE':
        # TO_DATE(expr) or TO_DATE(expr, 'fmt') → toDate(expr).
        # ClickHouse parses ISO format YYYY-MM-DD natively; the format hint
        # is dropped (matches Snowflake semantics for our use cases).
        return f"toDate({args[0]})" if args else None
    if fname == 'DATEADD':
        if len(args) != 3:
            return None
        unit_raw = args[0].strip().strip("'\"").lower()
        ch_fn = _DATEADD_UNIT_TO_FN.get(unit_raw)
        if not ch_fn:
            return None
        # Snowflake: DATEADD(unit, n, date)  →  ClickHouse: addXxx(date, n)
        return f"{ch_fn}({args[2]}, {args[1]})"
    if fname == 'DATEDIFF':
        if len(args) != 3:
            return None
        unit_raw = args[0].strip().strip("'\"").lower()
        # Map common Snowflake aliases to canonical CH unit
        unit_canonical = {
            'day': 'day', 'days': 'day',
            'week': 'week', 'weeks': 'week',
            'month': 'month', 'months': 'month',
            'year': 'year', 'years': 'year',
            'hour': 'hour', 'hours': 'hour',
            'minute': 'minute', 'minutes': 'minute',
            'second': 'second', 'seconds': 'second',
        }.get(unit_raw)
        if not unit_canonical:
            return None
        return f"dateDiff('{unit_canonical}', {args[1]}, {args[2]})"
    return None


_TO_CHAR_FORMAT_MAP = {
    'YYYY-MM-DD': '%Y-%m-%d',
    'YYYY-MM':    '%Y-%m',
    'YYYY':       '%Y',
    'MM-DD-YYYY': '%m-%d-%Y',
    'MM/DD/YYYY': '%m/%d/%Y',
    'YYYYMMDD':   '%Y%m%d',
    'YYYY-MM-DD HH24:MI:SS': '%Y-%m-%d %H:%M:%S',
}


def _rewrite_to_char(sql: str) -> str:
    """Rewrite TO_CHAR(date_expr, 'format') → formatDateTime(date_expr, '<ch fmt>').

    Uses a balanced-paren scanner to find the matching ')' so the date_expr
    can contain nested function calls (DATE_TRUNC, MIN, etc.) which a
    simple regex would mishandle.
    """
    out_parts: list[str] = []
    i = 0
    pat = re.compile(r"\bTO_CHAR\s*\(", re.IGNORECASE)
    while i < len(sql):
        m = pat.search(sql, i)
        if not m:
            out_parts.append(sql[i:])
            break
        out_parts.append(sql[i:m.start()])
        # Walk forward from inside the open paren, tracking depth, to find
        # the matching close paren.
        j = m.end()
        depth = 1
        in_str = False
        while j < len(sql) and depth > 0:
            c = sql[j]
            if c == "'" and (j == 0 or sql[j - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
            j += 1
        if depth != 0:
            out_parts.append(sql[m.start():])  # malformed — give up gracefully
            break
        inner = sql[m.end():j - 1]  # everything inside TO_CHAR(...)
        # Split inner on the LAST top-level comma so the format literal is the
        # right operand (date_expr can contain commas inside subcalls).
        depth = 0
        in_str = False
        last_comma = -1
        for k, c in enumerate(inner):
            if c == "'" and (k == 0 or inner[k - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                elif c == ',' and depth == 0:
                    last_comma = k
        if last_comma == -1:
            # No format arg — leave untouched
            out_parts.append(sql[m.start():j])
        else:
            date_expr = inner[:last_comma].strip()
            fmt_lit = inner[last_comma + 1:].strip()
            sf_fmt_match = re.fullmatch(r"'([^']+)'", fmt_lit)
            if not sf_fmt_match:
                out_parts.append(sql[m.start():j])
            else:
                sf_fmt = sf_fmt_match.group(1)
                ch_fmt = _TO_CHAR_FORMAT_MAP.get(sf_fmt, sf_fmt)
                out_parts.append(f"formatDateTime({date_expr}, '{ch_fmt}')")
        i = j
    return ''.join(out_parts)


def _rewrite_pipe_concat(sql: str) -> str:
    """Rewrite Snowflake `expr || expr` (string concat) into ClickHouse
    `concat(expr, expr)`. Repeatedly fold the leftmost binary `||` until
    none remain so chains (a || b || c) collapse into nested concats.

    The operand regex matches a single "atom":
        - quoted string literal ('...')
        - column / qualified column (foo, foo.bar)
        - balanced function call: name(...)  (no nested parens)
        - parenthesized subexpression (no nested parens)
    """
    atom = (
        r"(?:"
        r"'(?:[^']|'')*'"          # 'literal' (handles '' escapes)
        r"|\w+\s*\([^()]*\)"      # func(args)  — no nested parens
        r"|\([^()]*\)"            # (subexpr)   — no nested parens
        r"|\w+(?:\.\w+)*"         # ident or qualified.ident
        r")"
    )
    pattern = re.compile(rf"({atom})\s*\|\|\s*({atom})")
    prev = None
    for _ in range(50):  # bound iterations defensively
        if sql == prev:
            break
        prev = sql
        sql = pattern.sub(r"concat(\1, \2)", sql)
    return sql


def _rewrite_qualify(sql: str) -> str:
    """Rewrite QUALIFY clauses to nested subqueries.

    Handles patterns like:
      QUALIFY ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) = 1
      QUALIFY rn <= N
    """
    # Pattern 0 (fast path): QUALIFY ROW_NUMBER() OVER (PARTITION BY p ORDER BY o) <= N
    # → ORDER BY o LIMIT N BY p
    #
    # ClickHouse executes `LIMIT N BY` as a streaming top-N per group and never
    # materializes the full window function — typically 5-15× faster than the
    # subquery rewrite below for the wide PARTITION BY UID / ORDER BY DELIVERED
    # patterns Profile Analysis uses. Only safe when the outer SELECT has no
    # other ORDER BY or LIMIT (otherwise we'd silently change query semantics).
    fast_match = re.search(
        r'\bQUALIFY\s+ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(\s*'
        r'PARTITION\s+BY\s+(?P<part>[^()]+?)\s+'
        r'ORDER\s+BY\s+(?P<order>[^()]+?)\s*\)\s*'
        r'(?P<op><=|=)\s*(?P<n>\d+)\s*$',
        sql, re.IGNORECASE,
    )
    if fast_match:
        head = sql[:fast_match.start()].rstrip()
        head_no_strings = re.sub(r"'[^']*'", "''", head)
        if not re.search(r'\bORDER\s+BY\b', head_no_strings, re.IGNORECASE) \
                and not re.search(r'\bLIMIT\b', head_no_strings, re.IGNORECASE):
            part = fast_match.group('part').strip()
            order = fast_match.group('order').strip()
            n = fast_match.group('n')
            return f"{head}\nORDER BY {order} LIMIT {n} BY {part}"

    # Pattern 1: QUALIFY ROW_NUMBER() OVER (...) = 1
    # → wrap in subquery with ROW_NUMBER as _rn, filter WHERE _rn = 1
    qualify_match = re.search(
        r'\bQUALIFY\s+ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(([^)]+)\)\s*=\s*(\d+)',
        sql, re.IGNORECASE
    )
    if qualify_match:
        partition_clause = qualify_match.group(1)
        target_val = qualify_match.group(2)
        qualify_text = qualify_match.group(0)
        inner_sql = sql[:qualify_match.start()].rstrip()
        after_sql = sql[qualify_match.end():]
        ctas_m = re.match(
            r'(\s*CREATE\s+(?:OR\s+REPLACE\s+)?TEMP(?:ORARY)?\s+TABLE\s+\w+\s+AS\s)',
            inner_sql, re.IGNORECASE,
        )
        prefix = ''
        if ctas_m:
            prefix = ctas_m.group(1)
            inner_sql = inner_sql[ctas_m.end():]
        inner_sql_with_rn = re.sub(
            r'\bFROM\b',
            f', ROW_NUMBER() OVER ({partition_clause}) AS _qual_rn FROM',
            inner_sql, count=1, flags=re.IGNORECASE
        )
        return f"{prefix}SELECT * FROM ({inner_sql_with_rn}) _q WHERE _q._qual_rn = {target_val}{after_sql}"

    # Pattern 2: QUALIFY alias <= N or QUALIFY alias = N
    qualify_match2 = re.search(
        r'\bQUALIFY\s+(\w+)\s*(<=?|>=?|=|!=)\s*(\S+)',
        sql, re.IGNORECASE
    )
    if qualify_match2:
        alias = qualify_match2.group(1)
        op = qualify_match2.group(2)
        val = qualify_match2.group(3)
        inner_sql = sql[:qualify_match2.start()].rstrip()
        after_sql = sql[qualify_match2.end():]
        # If the statement starts with CREATE ... TABLE ... AS, strip the DDL
        # prefix so we only wrap the SELECT portion in the subquery.
        ctas_m = re.match(
            r'(\s*CREATE\s+(?:OR\s+REPLACE\s+)?TEMP(?:ORARY)?\s+TABLE\s+\w+\s+AS\s)',
            inner_sql, re.IGNORECASE,
        )
        prefix = ''
        if ctas_m:
            prefix = ctas_m.group(1)
            inner_sql = inner_sql[ctas_m.end():]
        return f"{prefix}SELECT * FROM ({inner_sql}) _q WHERE _q.{alias} {op} {val}{after_sql}"

    return sql


def _translate_column_defs(col_defs: str) -> str:
    """Translate Snowflake column type definitions to ClickHouse types."""
    parts = []
    for col_def in col_defs.split(','):
        col_def = col_def.strip()
        if not col_def:
            continue
        tokens = col_def.split()
        if len(tokens) >= 2:
            col_name = tokens[0]
            sf_type = tokens[1].upper().split('(')[0]
            ch_type = TYPE_MAP.get(sf_type, 'String')
            parts.append(f'{col_name} {ch_type}')
        else:
            parts.append(col_def)
    return ', '.join(parts)


# ── Compatibility wrappers ────────────────────────────────────────────────────

class ClickHouseCursor:
    """Mimics snowflake.connector.cursor for full Snowflake API compatibility."""

    def __init__(self, client: clickhouse_connect.driver.Client,
                 default_settings: Optional[dict] = None):
        self._client = client
        self._default_settings = dict(default_settings or {})
        self._rows:   list[tuple] = []
        self._pos:    int = 0
        self._description = None
        self.rowcount: int = -1
        self._column_names: list[str] = []

    def execute(self, sql: str, params=None, settings: Optional[dict] = None):
        sql_stripped = sql.strip().upper()
        ignore_prefixes = (
            'USE WAREHOUSE', 'ALTER WAREHOUSE', 'ALTER SESSION',
            'USE DATABASE', 'USE SCHEMA', 'USE ROLE',
            'SET STATEMENT_TIMEOUT', 'SET USE_CACHED',
        )
        if any(sql_stripped.startswith(p) for p in ignore_prefixes):
            self._rows = []
            self._pos  = 0
            self._column_names = []
            return self

        translated = translate_sql(sql)

        # Per-query settings = connection defaults overlaid with caller overrides.
        merged_settings = dict(self._default_settings)
        if settings:
            merged_settings.update(settings)

        # Handle compound statements (DROP + CREATE from CREATE OR REPLACE TABLE).
        # Pass merged_settings through to command() so DDL/DML benefit from
        # per-query overrides too — specifically `max_memory_usage` and the
        # `max_bytes_before_external_*` spill thresholds, which DO matter for
        # CREATE TEMPORARY TABLE X AS SELECT (the SELECT half allocates the
        # full intermediate result and ClickHouse-Profile caps win out unless
        # we override per-statement). Profile Analysis's PRE_SAMPLED_CLICKSTREAM
        # CTAS hit the 100 GiB user-profile cap on full-year ranges before
        # this was wired up; the override raises the budget to ~300 GiB only
        # for that one statement, the connection default still applies
        # everywhere else.
        if ';\n' in translated:
            statements = [s.strip() for s in translated.split(';\n') if s.strip()]
            for stmt in statements[:-1]:
                # CTAS (`CREATE TEMPORARY TABLE x AS SELECT ...`) and any
                # statement that contains a heavy SELECT in a compound block
                # also takes a slot — the SELECT half is what hammers CH.
                stmt_u = stmt.lstrip().upper()
                is_heavy = any(stmt_u.startswith(p) for p in ('SELECT', 'WITH'))           \
                    or 'AS SELECT' in stmt_u or 'AS WITH' in stmt_u                        \
                    or stmt_u.startswith('INSERT') and ' SELECT ' in stmt_u
                if is_heavy:
                    with _ch_query_slot(stmt):
                        self._client.command(stmt, settings=merged_settings or None)
                else:
                    self._client.command(stmt, settings=merged_settings or None)
            translated = statements[-1]

        upper = translated.strip().upper()
        if upper.startswith('SELECT') or upper.startswith('WITH') or upper.startswith('SHOW'):
            try:
                with _ch_query_slot(translated):
                    result = self._client.query(
                        translated,
                        parameters=params or {},
                        settings=merged_settings or None,
                    )
                self._rows = [tuple(row) for row in result.result_rows]
                self._column_names = result.column_names if hasattr(result, 'column_names') else []
                self._description = [
                    (name, None, None, None, None, None, True)
                    for name in self._column_names
                ] if self._column_names else None
            except Exception as e:
                logger.warning("ClickHouse query error: %s\nSQL: %s", e, translated[:500])
                raise
        else:
            # DDL / DML — Memory-engine temp table CREATE / DROP, INSERT, etc.
            # NOT gated: these are fast and don't contend for CH working memory.
            self._client.command(translated, settings=merged_settings or None)
            self._rows = []
            self._column_names = []
            self._description = None

        self._pos = 0
        self.rowcount = len(self._rows)
        return self

    def executemany(self, sql: str, params_list):
        for params in params_list:
            self.execute(sql, params)

    def fetchone(self) -> Optional[tuple]:
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self) -> list[tuple]:
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size: int = 100) -> list[tuple]:
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetch_pandas_all(self) -> 'pd.DataFrame':
        """Snowflake-compatible: return all results as a pandas DataFrame."""
        rows = self.fetchall()
        columns = self._column_names or [f'col_{i}' for i in range(len(rows[0]))] if rows else []
        return pd.DataFrame(rows, columns=columns)

    def __iter__(self):
        return iter(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass

    @property
    def description(self):
        return self._description


class ClickHouseConnection:
    """Mimics snowflake.connector.SnowflakeConnection for full API compatibility."""

    def __init__(self, client: clickhouse_connect.driver.Client,
                 query_settings: Optional[dict] = None):
        self._client = client
        self._query_settings = dict(query_settings or {})

    def cursor(self) -> ClickHouseCursor:
        return ClickHouseCursor(self._client, default_settings=self._query_settings)

    def close(self):
        self._client.close()

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
