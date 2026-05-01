"""
clickhouse_connector.py  —  ClickHouse adapter that mimics the
snowflake.connector DBAPI (cursor / execute / fetch*).

Originally written as a drop-in replacement during the Snowflake -> ClickHouse
migration. The migration is now complete; SF is fully decommissioned.
Translates legacy SF SQL patterns to ClickHouse SQL automatically.

Usage:
    from clickhouse_connector import connect_clickhouse
    conn = connect_clickhouse()
    cur  = conn.cursor()
    cur.execute("SELECT ...")
    rows = cur.fetchall()
    conn.close()
"""

import os
import re
import logging
from typing import Any, Optional

import clickhouse_connect
import pandas as pd

logger = logging.getLogger(__name__)

CH_HOST       = os.environ.get('CH_HOST',       '37.27.140.111')
CH_PORT       = int(os.environ.get('CH_PORT',   '8123'))
CH_USER       = os.environ.get('CH_USER',       'bgapp')
CH_PASSWORD   = os.environ.get('CH_PASSWORD',   '4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ')
CH_DATABASE   = os.environ.get('CH_DATABASE',   'clickstream')


# ── Default per-query settings ────────────────────────────────────────────────
# These are sent on every query for this client. They roughly reproduce the
# behavior we used to lean on Snowflake's 6XL warehouse for: fully utilize
# every core, exploit the (DELIVERED, UID, COMMON_NAME) sort order on
# clickstream_final, pick a smart JOIN algorithm per query, and spill to disk
# instead of OOM'ing if a query gets bigger than expected.
#
# Numbers picked for the AX162-S box (768 GB RAM, 4× 3.84 TB NVMe).
CH_DEFAULT_SETTINGS = {
    # Use all available cores for both reads and aggregations.
    "max_threads": 0,
    # Stream rows in primary-key order — huge win for clickstream_final since
    # most Profile-Analysis queries filter on DELIVERED first.
    "optimize_read_in_order": 1,
    "optimize_aggregation_in_order": 1,
    # Let the planner choose between parallel_hash / hash / partial_merge /
    # grace_hash on a per-query basis. parallel_hash is by far the fastest
    # for the small-build-side joins Profile Analysis does (TEMP_SAMPLED_UIDS,
    # ELIGIBLE_UIDS, HOST_MAPPING).
    "join_algorithm": "parallel_hash,hash,partial_merge,grace_hash",
    # Spill to disk on large group-by/sort instead of crashing.
    "max_bytes_before_external_group_by": 40_000_000_000,   # 40 GB
    "max_bytes_before_external_sort":     40_000_000_000,   # 40 GB
    # Hard cap per query so one runaway query can't take the whole box down.
    "max_memory_usage":                   120_000_000_000,  # 120 GB
    # Mutations issued through this connection are awaited before returning,
    # so callers can safely read-after-write on temp tables.
    "mutations_sync": 2,
    # Deduplicate identical INSERTs within a 60-second window — protects against
    # accidental double-runs from the dashboard.
    "insert_deduplicate": 1,
    # Profile Analysis builds long inline IN-lists of hostnames (one per
    # quick-select). The default 256 KiB query parser limit blows up at
    # ~262 KB with `Max query size exceeded`. 16 MiB is plenty headroom
    # and well under the server-side hard limit.
    "max_query_size": 16 * 1024 * 1024,
}


def connect_clickhouse():
    """Connect to ClickHouse. Returns a Snowflake-compatible wrapper."""
    client = clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASSWORD,
        database=CH_DATABASE,
        connect_timeout=30,
        send_receive_timeout=3600,
        settings=CH_DEFAULT_SETTINGS,
    )
    return ClickHouseConnection(client)


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
    # DATEADD: handled by _rewrite_dateadd (balanced-paren walker) before
    # FUNC_REPLACEMENTS runs. Regex form left behind for reference only.
    # DATEDIFF
    (r"DATEDIFF\s*\(\s*'?day'?\s*,\s*([^,]+),\s*([^)]+)\)",
     r"dateDiff('day', \1, \2)"),
    # CONVERT_TIMEZONE
    (r"CONVERT_TIMEZONE\s*\([^,]+,\s*'([^']+)'\s*,\s*([^)]+)\)",
     r"toTimeZone(\2, '\1')"),
    # TO_DATE handled by _rewrite_to_date before FUNC_REPLACEMENTS runs.
    # TO_TIMESTAMP
    (r'\bTO_TIMESTAMP\s*\(([^)]+)\)',   r'toDateTime(\1)'),
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


# ── Balanced-paren helpers for Snowflake→ClickHouse rewrites ─────────────────
# These walkers correctly handle nested calls and quoted strings, which the
# simple [^)]+ regexes in FUNC_REPLACEMENTS cannot.

_SF_FMT_TOKENS = [
    ('YYYY', '%Y'), ('YY', '%y'),
    ('MONTH', '%B'), ('MON', '%b'), ('MM', '%m'),
    ('DAY', '%A'), ('DY', '%a'), ('DD', '%d'),
    ('HH24', '%H'), ('HH12', '%I'), ('HH', '%H'),
    ('MI', '%M'), ('SS', '%S'),
]
_SF_FMT_TOKENS.sort(key=lambda p: -len(p[0]))


def _sf_format_to_ch(fmt: str) -> str:
    out = []
    i = 0
    while i < len(fmt):
        matched = False
        for tok, repl in _SF_FMT_TOKENS:
            if fmt[i:i + len(tok)].upper() == tok:
                out.append(repl)
                i += len(tok)
                matched = True
                break
        if not matched:
            out.append(fmt[i])
            i += 1
    return ''.join(out)


def _find_matching_paren(text: str, open_idx: int) -> int:
    """Given the index of an opening '(' in text, return the index of its
    matching ')'. Respects single-quoted strings (with '' escape). Returns -1
    if no match."""
    depth = 1
    j = open_idx + 1
    n = len(text)
    while j < n and depth > 0:
        c = text[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return j
        elif c == "'":
            j += 1
            while j < n:
                if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
        j += 1
    return -1


def _split_top_level_args(inner: str) -> list[str]:
    """Split a comma-separated argument list at the top paren/quote level."""
    args = []
    depth = 0
    buf = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == '(':
            depth += 1
            buf.append(c)
        elif c == ')':
            depth -= 1
            buf.append(c)
        elif c == "'":
            buf.append(c)
            i += 1
            while i < n:
                if inner[i] == "'" and i + 1 < n and inner[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                buf.append(inner[i])
                if inner[i] == "'":
                    break
                i += 1
        elif c == ',' and depth == 0:
            args.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    tail = ''.join(buf).strip()
    if tail or args:
        args.append(tail)
    return args


def _rewrite_call(text: str, name: str, handler) -> str:
    """Find every case-insensitive call `name(...)` in text, pass its
    balanced arg list to `handler(args) -> str`, and substitute."""
    out = []
    i = 0
    n = len(text)
    pat = re.compile(r'\b' + re.escape(name) + r'\s*\(', re.IGNORECASE)
    while i < n:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        open_paren = m.end() - 1
        close = _find_matching_paren(text, open_paren)
        if close == -1:
            out.append(text[m.start():])
            break
        inner = text[open_paren + 1:close]
        args = _split_top_level_args(inner)
        replacement = handler(args)
        if replacement is None:
            out.append(text[m.start():close + 1])
        else:
            out.append(replacement)
        i = close + 1
    return ''.join(out)


def _rewrite_to_char(text: str) -> str:
    def h(args):
        if len(args) != 2:
            return None
        expr = args[0]
        fmt_arg = args[1].strip()
        if len(fmt_arg) >= 2 and fmt_arg[0] == "'" and fmt_arg[-1] == "'":
            fmt = fmt_arg[1:-1]
            return f"formatDateTime({expr}, '{_sf_format_to_ch(fmt)}')"
        return None
    return _rewrite_call(text, 'TO_CHAR', h)


def _rewrite_to_date(text: str) -> str:
    def h(args):
        if len(args) == 1:
            return f'toDate({args[0]})'
        if len(args) == 2:
            expr = args[0]
            fmt_arg = args[1].strip()
            if len(fmt_arg) >= 2 and fmt_arg[0] == "'" and fmt_arg[-1] == "'":
                fmt = fmt_arg[1:-1].upper()
                if fmt in ('YYYY-MM-DD', 'YYYY/MM/DD'):
                    return f'toDate({expr})'
                return f"toDate(parseDateTimeOrNull({expr}, '{_sf_format_to_ch(fmt)}'))"
        return None
    return _rewrite_call(text, 'TO_DATE', h)


_DATEADD_UNIT_MAP = {
    'DAY': 'addDays', 'DAYS': 'addDays',
    'WEEK': 'addWeeks', 'WEEKS': 'addWeeks',
    'MONTH': 'addMonths', 'MONTHS': 'addMonths',
    'QUARTER': 'addQuarters', 'QUARTERS': 'addQuarters',
    'YEAR': 'addYears', 'YEARS': 'addYears',
    'HOUR': 'addHours', 'HOURS': 'addHours',
    'MINUTE': 'addMinutes', 'MINUTES': 'addMinutes',
    'SECOND': 'addSeconds', 'SECONDS': 'addSeconds',
}


def _rewrite_dateadd(text: str) -> str:
    def h(args):
        if len(args) != 3:
            return None
        unit = args[0].strip().strip("'").upper()
        fn = _DATEADD_UNIT_MAP.get(unit)
        if not fn:
            return None
        n_arg = args[1].strip()
        expr = args[2].strip()
        return f'{fn}({expr}, {n_arg})'
    return _rewrite_call(text, 'DATEADD', h)


def translate_sql(sql: str) -> str:
    """Translate Snowflake SQL to ClickHouse SQL."""
    result = sql

    # Table name replacements (case-insensitive)
    for pattern, replacement in TABLE_MAP.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # ── Balanced-paren rewrites (must run before FUNC_REPLACEMENTS) ───────
    # Handle nested calls like DATEADD(MONTH, 1, TO_DATE(x || '-01', 'YYYY-MM-DD'))
    # that the FUNC_REPLACEMENTS regexes (which use [^)]+) would mangle.
    result = _rewrite_to_char(result)
    result = _rewrite_to_date(result)
    result = _rewrite_dateadd(result)

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
    # ClickHouse's LIKE uses '\' as the default escape character, which is
    # exactly what Snowflake's  `ESCAPE '\\'`  requests.  ClickHouse rejects
    # the explicit ESCAPE clause with a SYNTAX_ERROR, so we drop it.
    # Only safe when the callers escape with '\' (bg.py's `_escape_brand_for_sql`
    # does).  If a caller ever used a different escape char this would quietly
    # change semantics, but we have none in this repo.
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
    # ClickHouse only supports native SAMPLE on tables built with a
    # `SAMPLE BY` key, which none of our production tables have. Previously
    # we just stripped the clause, but that meant a query asking for 15% of
    # clickstream_final actually scanned 100% — exactly the behavior the
    # caller was trying to avoid.
    #
    # Replacement strategy: convert into deterministic hash-bucket sampling
    # on UID by wrapping the table in a subquery:
    #
    #   FROM clickstream_final SAMPLE (15) c
    #     →  FROM (SELECT * FROM clickstream_final
    #              WHERE cityHash64(UID) % 10000 < 1500) c
    #
    # Properties:
    #   • Reproducible: same UIDs picked across runs, so PASS 1 / PASS 2
    #     temp tables stay consistent.
    #   • All tables Profile Analysis SAMPLEs from have a UID column, so this
    #     is safe for our use cases.
    #   • cityHash64 is column-pruning friendly; CH skips reading any column
    #     other than UID until the predicate passes.
    #   • Date filtering on the outer query still does partition pruning
    #     because the wrapper preserves all columns.
    def _sample_to_hash(m):
        table = m.group(1)
        pct   = float(m.group(2))
        # Cap at 100, allow fractional percents down to 0.01.
        bucket = max(1, min(10000, int(round(pct * 100))))
        return (
            f'FROM (SELECT * FROM {table} '
            f'WHERE cityHash64(UID) % 10000 < {bucket})'
        )

    result = re.sub(
        r'\bFROM\s+([\w.]+)\s+SAMPLE\s*\(\s*(\d+(?:\.\d+)?)\s*\)',
        _sample_to_hash, result, flags=re.IGNORECASE,
    )

    # Anything that didn't match the FROM-table pattern (rare; usually a
    # SAMPLE clause attached to a subquery alias) gets stripped as before so
    # the query at least parses.
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
    # Snowflake "OR REPLACE" semantic = drop-then-create; we must emit an
    # explicit DROP so stale rows from an earlier statement/session can't
    # persist (CREATE TEMP TABLE IF NOT EXISTS would silently skip the
    # SELECT if the table already exists in the session).
    #
    # Self-referential case (CREATE OR REPLACE TEMP TABLE X AS ... FROM X):
    # if we just emitted DROP X then CREATE X AS SELECT FROM X, the SELECT
    # would fail because X is gone. Snapshot via a swap temp table first so
    # the rebuild is safe. This pattern is common in bg.py whenever an
    # iterative pipeline applies a cap/sample/transform onto its own
    # working table (PRE_SAMPLED_CLICKSTREAM, BEHAVIOR_EVENTS, BEHAVIOR_FINAL).
    def _replace_create_or_replace_temp(m):
        name = m.group(1)
        # Look at the rest of the SQL after "AS" for any FROM <name> or
        # JOIN <name> reference. We strip string literals first so a brand
        # name like "Burger King X" doesn't false-positive.
        after = result[m.end():]
        after_sanitized = re.sub(r"'[^']*'", "''", after)
        # Stop scanning at the first statement terminator outside strings.
        stop = re.search(r';\s*$', after_sanitized, re.MULTILINE)
        scope = after_sanitized[:stop.start()] if stop else after_sanitized
        is_self_ref = bool(re.search(
            rf'\b(?:FROM|JOIN)\s+{re.escape(name)}\b',
            scope, re.IGNORECASE,
        ))
        if is_self_ref:
            swap = f'_ch_swap_{name}'
            return (
                f'DROP TABLE IF EXISTS {swap};\n'
                f'CREATE TEMPORARY TABLE {swap} ENGINE = Memory AS'
            ), swap, name
        return (
            f'DROP TABLE IF EXISTS {name};\n'
            f'CREATE TEMPORARY TABLE {name} ENGINE = Memory AS'
        ), None, name

    # We need post-processing for self-ref: after the SELECT body, append
    # the swap → rename sequence. Easiest is two passes.
    _swap_targets: list[tuple[str, str]] = []  # [(swap_name, real_name), ...]

    def _ctas_pass1(m):
        replacement, swap, name = _replace_create_or_replace_temp(m)
        if swap:
            _swap_targets.append((swap, name))
        return replacement

    result = re.sub(
        r'CREATE\s+OR\s+REPLACE\s+TEMP(?:ORARY)?\s+TABLE\s+(\w+)\s+AS\b',
        _ctas_pass1, result, flags=re.IGNORECASE,
    )

    # Append the swap sequence at the very end of the translated SQL for
    # any self-referential rebuilds we detected. Order matters: each pair
    # was emitted in source order, so we tail-append in the same order.
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
        return (f'DROP TABLE IF EXISTS {name};\n'
                f'CREATE TEMPORARY TABLE {name} ({translated_cols}) ENGINE = Memory')
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


_CTAS_PREFIX_RE = re.compile(
    r'^(\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+\w+\s+AS\s+)',
    re.IGNORECASE,
)


def _split_ctas_prefix(inner_sql: str) -> tuple[str, str]:
    """If `inner_sql` starts with `CREATE [OR REPLACE] [TEMP] TABLE <name> AS`,
    split off that prefix so QUALIFY wrapping happens *only* on the SELECT.
    Returns (ctas_prefix, rest). When no CTAS prefix, ctas_prefix == ''.
    """
    m = _CTAS_PREFIX_RE.match(inner_sql)
    if not m:
        return '', inner_sql
    return inner_sql[:m.end()], inner_sql[m.end():]


def _rewrite_qualify(sql: str) -> str:
    """Rewrite QUALIFY clauses to nested subqueries.

    Handles patterns like:
      QUALIFY ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) = 1
      QUALIFY rn <= N

    When the enclosing statement is  ``CREATE [OR REPLACE] [TEMP] TABLE x AS
    SELECT ... QUALIFY ...``  the wrapper must apply only to the SELECT, not
    to the whole DDL, otherwise we emit  ``SELECT * FROM (CREATE TABLE ...)``
    which ClickHouse (rightly) rejects.
    """
    # Pattern 0 (fast path): QUALIFY ROW_NUMBER() OVER (PARTITION BY p ORDER BY o) <= N
    # → ORDER BY o LIMIT N BY p
    #
    # ClickHouse executes `LIMIT N BY` as a streaming top-N per group and never
    # materializes the full window function — typically 5-15× faster than the
    # subquery rewrite below for the wide PARTITION BY UID / ORDER BY DELIVERED
    # patterns Profile Analysis uses. We only take this path when the outer
    # SELECT has no other ORDER BY or LIMIT (otherwise we'd silently change
    # query semantics) and the comparator is `<=` or `=` against a positive int.
    fast_match = re.search(
        r'\bQUALIFY\s+ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(\s*'
        r'PARTITION\s+BY\s+(?P<part>[^()]+?)\s+'
        r'ORDER\s+BY\s+(?P<order>[^()]+?)\s*\)\s*'
        r'(?P<op><=|=)\s*(?P<n>\d+)\s*$',
        sql, re.IGNORECASE,
    )
    if fast_match:
        head = sql[:fast_match.start()].rstrip()
        # Bail out if the SELECT already has its own LIMIT or ORDER BY at the
        # top level — merging them is too risky to do via regex.
        head_no_strings = re.sub(r"'[^']*'", "''", head)
        if not re.search(r'\bORDER\s+BY\b', head_no_strings, re.IGNORECASE) \
                and not re.search(r'\bLIMIT\b', head_no_strings, re.IGNORECASE):
            part  = fast_match.group('part').strip()
            order = fast_match.group('order').strip()
            n     = fast_match.group('n')
            return f"{head}\nORDER BY {order} LIMIT {n} BY {part}"

    # Pattern 1: QUALIFY ROW_NUMBER() OVER (...) = 1
    qualify_match = re.search(
        r'\bQUALIFY\s+ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(([^)]+)\)\s*=\s*(\d+)',
        sql, re.IGNORECASE
    )
    if qualify_match:
        partition_clause = qualify_match.group(1)
        target_val = qualify_match.group(2)
        head = sql[:qualify_match.start()].rstrip()
        after = sql[qualify_match.end():]
        ctas, inner_select = _split_ctas_prefix(head)
        inner_select_with_rn = re.sub(
            r'\bFROM\b',
            f', ROW_NUMBER() OVER ({partition_clause}) AS _qual_rn FROM',
            inner_select, count=1, flags=re.IGNORECASE
        )
        return (
            f"{ctas}SELECT * FROM ({inner_select_with_rn}) _q "
            f"WHERE _q._qual_rn = {target_val}{after}"
        )

    # Pattern 2: QUALIFY alias <= N or QUALIFY alias = N
    qualify_match2 = re.search(
        r'\bQUALIFY\s+(\w+)\s*(<=?|>=?|=|!=)\s*(\S+)',
        sql, re.IGNORECASE
    )
    if qualify_match2:
        alias = qualify_match2.group(1)
        op = qualify_match2.group(2)
        val = qualify_match2.group(3)
        head = sql[:qualify_match2.start()].rstrip()
        after = sql[qualify_match2.end():]
        ctas, inner_select = _split_ctas_prefix(head)
        return (
            f"{ctas}SELECT * FROM ({inner_select}) _q "
            f"WHERE _q.{alias} {op} {val}{after}"
        )

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

    def __init__(self, client: clickhouse_connect.driver.Client):
        self._client = client
        self._rows:   list[tuple] = []
        self._pos:    int = 0
        self._description = None
        self.rowcount: int = -1
        self._column_names: list[str] = []

    def execute(self, sql: str, params=None):
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

        # Handle compound statements (DROP + CREATE from CREATE OR REPLACE TABLE)
        if ';\n' in translated:
            statements = [s.strip() for s in translated.split(';\n') if s.strip()]
            for stmt in statements[:-1]:
                self._client.command(stmt)
            translated = statements[-1]

        upper = translated.strip().upper()
        if upper.startswith('SELECT') or upper.startswith('WITH') or upper.startswith('SHOW'):
            try:
                result = self._client.query(translated, parameters=params or {})
                self._rows = [tuple(row) for row in result.result_rows]
                self._column_names = result.column_names if hasattr(result, 'column_names') else []
                self._description = [
                    (name, None, None, None, None, None, True)
                    for name in (self._column_names or [])
                ]
            except Exception as e:
                logger.warning("ClickHouse query error: %s\nSQL: %s", e, translated[:500])
                raise
        else:
            self._client.command(translated)
            self._rows = []
            self._column_names = []
            self._description = []

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

    def __init__(self, client: clickhouse_connect.driver.Client):
        self._client = client

    def cursor(self) -> ClickHouseCursor:
        return ClickHouseCursor(self._client)

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
