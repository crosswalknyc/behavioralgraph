"""Incremental Netflix ranker ingest, chunked by day.

Shared by the Render cron endpoint (``/api/cron/netflix-ranker-daily`` in
``bg-webapp/app.py``) and the box-side backfill runner
(``migration/netflix_ranker_backfill.py``).

Why chunked (2026-09-30): the cron pointed at a suspended host from early
July, so ``netflix.netflix_all`` fell 83 days (988M raw rows) behind while
Phase 0a kept appending to ``netflix.netflix_clickstream``. The original
single-shot Phase 0b then tried to window every backlogged row at once and
hit the 80 GiB per-query cap. Processing one calendar day per INSERT keeps
each query near the normal nightly size (~15M rows) no matter how long the
cron was down, and a wall-clock budget lets an HTTP caller return before
its timeout and pick the remainder up on the next run.

Tables (all incremental, cursor = max(VISIT_TS) of the target):
  0a  clickstream.clickstream_final  -> netflix.netflix_clickstream
  0b  netflix.netflix_clickstream    -> netflix.netflix_all   (enrich)
  0c  netflix.netflix_all            -> netflix.netflix       (AVAILABLE)
  1   netflix.netflix                -> netflix.netflix_ranker_daily
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

GiB = 1024 ** 3

# Per-query overrides for the heavy enrich step. A single day is ~15M rows
# plus a 111M-UID DMA build side; 160 GiB leaves 2x headroom over the
# connector default without approaching the server budget.
ENRICH_SETTINGS = {
    'max_memory_usage': 160 * GiB,
    'max_execution_time': 3600,
    'join_algorithm': 'parallel_hash,hash,grace_hash',
}

_LOG = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _scalar(cur, sql: str):
    cur.execute(sql)
    row = cur.fetchone()
    return row[0] if row else None


def _ts(v) -> str:
    """Render a ClickHouse DateTime64 value as a literal safe for comparison."""
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S.%f')
    return str(v)


def append_raw_clickstream(cur, log: _LOG = _noop) -> str:
    """Phase 0a: new netflix.com/watch rows from clickstream_final."""
    log('Phase 0a: appending clickstream...')
    cur.execute("""
        INSERT INTO netflix.netflix_clickstream
            (UID, URL, VISIT_TS, TIME_COMPUTED, BROWSER, PLATFORM)
        SELECT UID, URL, VISIT_TS, false AS TIME_COMPUTED, BROWSER, PLATFORM
        FROM clickstream.clickstream_final
        WHERE URL LIKE '%netflix.com/watch/%'
          AND VISIT_TS > (SELECT max(VISIT_TS) FROM netflix.netflix_clickstream)
    """)
    return 'ok'


def pending_enrich_days(cur) -> tuple[str, list[date]]:
    """Return (cursor literal, calendar days with raw rows past the cursor)."""
    cursor = _scalar(cur, "SELECT max(VISIT_TS) FROM netflix.netflix_all")
    cursor_lit = _ts(cursor)
    cur.execute(f"""
        SELECT DISTINCT toDate(VISIT_TS) AS d
        FROM netflix.netflix_clickstream
        WHERE VISIT_TS > toDateTime64('{cursor_lit}', 6)
        ORDER BY d
    """)
    days = [r[0] for r in cur.fetchall()]
    return cursor_lit, days


def enrich_day(cur, cursor_lit: str, day: date, log: _LOG = _noop) -> None:
    """Phase 0b for one calendar day of raw rows past the cursor.

    The window function only sees this day's rows, so the last visit of the
    day gets a NULL TIME_ON_PAGE. The original single-shot query had the same
    boundary at every cron run; the effect is one row per user per day.
    """
    d = day.strftime('%Y-%m-%d')
    log(f'Phase 0b: enriching {d} into netflix_all...')
    cur.execute(f"""
        INSERT INTO netflix.netflix_all
        SELECT
            cs.UID,
            cs.VISIT_TS,
            cs.URL,
            CASE
                WHEN next_ts IS NULL
                  OR dateDiff('minute', cs.VISIT_TS, next_ts) > 210
                THEN NULL
                ELSE toString(dateDiff('second', cs.VISIT_TS, next_ts))
            END AS TIME_ON_PAGE,
            ud.DMA,
            m.NAME_OF_SHOW,
            m.SEASON,
            m.EPISODE,
            m.EPISODE_NAME,
            m.RUN_TIME,
            m.GENRE,
            m.CAST,
            m.AGE_RATING,
            m.YEAR_RELEASED,
            m.TYPE,
            m.AVAILABLE
        FROM (
            SELECT
                UID, VISIT_TS, URL, BROWSER, PLATFORM,
                leadInFrame(VISIT_TS) OVER (
                    PARTITION BY UID
                    ORDER BY VISIT_TS
                    ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING
                ) AS next_ts
            FROM netflix.netflix_clickstream
            WHERE VISIT_TS > toDateTime64('{cursor_lit}', 6)
              AND toDate(VISIT_TS) = toDate('{d}')
        ) cs
        JOIN netflix.netflix_url_map m
            ON concat('https://www.netflix.com/title/',
                      splitByChar('?', splitByString('/watch/', cs.URL)[2])[1]) = m.URL
        LEFT JOIN (
            SELECT UID, DMA FROM (
                SELECT UID, max(DMA) AS DMA
                FROM userdata.user_data_sanitized
                GROUP BY UID
            ) WHERE DMA != ''
        ) ud ON cs.UID = ud.UID
    """, settings=ENRICH_SETTINGS)


def filter_available(cur, log: _LOG = _noop) -> str:
    """Phase 0c: AVAILABLE='TRUE' rows past the netflix cursor."""
    log('Phase 0c: filtering into netflix...')
    cur.execute("""
        INSERT INTO netflix.netflix
        SELECT * FROM netflix.netflix_all
        WHERE AVAILABLE = 'TRUE'
          AND VISIT_TS > (SELECT max(VISIT_TS) FROM netflix.netflix)
    """)
    return 'ok'


def ingest_backlog(cur, *, time_budget_s: float | None = None,
                   max_days: int | None = None,
                   skip_raw: bool = False,
                   log: _LOG = _noop) -> dict:
    """Run phases 0a-0c, enriching one day per query.

    Stops early (leaving the rest for the next call) once ``time_budget_s``
    seconds have elapsed or ``max_days`` days are done. Phase 0c runs after
    whatever was enriched so downstream tables never trail netflix_all.
    """
    t0 = time.monotonic()
    out: dict = {}
    if not skip_raw:
        out['clickstream'] = append_raw_clickstream(cur, log)
    cursor_lit, days = pending_enrich_days(cur)
    out['enrich_pending_days'] = len(days)
    done: list[str] = []
    for day in days:
        if max_days is not None and len(done) >= max_days:
            break
        if time_budget_s is not None and time.monotonic() - t0 > time_budget_s:
            break
        enrich_day(cur, cursor_lit, day, log)
        done.append(day.strftime('%Y-%m-%d'))
    out['enriched_days'] = done
    out['enrich_remaining_days'] = len(days) - len(done)
    out['netflix_all'] = 'ok' if not (len(days) - len(done)) else 'partial'
    if done or not days:
        out['netflix'] = filter_available(cur, log)
    log(f'Phase 0 complete: {len(done)} day(s) enriched, '
        f'{len(days) - len(done)} remaining.')
    return out


def missing_ranker_days(cur, upto: date | None = None) -> list[date]:
    """Days present in netflix.netflix but absent from netflix_ranker_daily."""
    upto = upto or (datetime.utcnow().date() - timedelta(days=1))
    cur.execute(f"""
        SELECT DISTINCT toDate(VISIT_TS) AS d
        FROM netflix.netflix
        WHERE AVAILABLE = 'TRUE'
          AND toDate(VISIT_TS) <= toDate('{upto.strftime('%Y-%m-%d')}')
          AND toDate(VISIT_TS) > ifNull(
                (SELECT max(DAY) FROM netflix.netflix_ranker_daily),
                toDate('1970-01-01'))
        ORDER BY d
    """)
    return [r[0] for r in cur.fetchall()]


def aggregate_days(cur, days: Iterable[date], log: _LOG = _noop) -> dict:
    """Phase 1: one row per show/season/episode/day. Skips populated days."""
    results: dict = {}
    for d in days:
        d_str = d.strftime('%Y-%m-%d')
        existing = _scalar(
            cur, f"SELECT count() FROM netflix.netflix_ranker_daily WHERE DAY = '{d_str}'")
        if existing:
            results[d_str] = f'skipped (already has {existing} rows)'
            continue
        log(f'Phase 1: aggregating {d_str}...')
        cur.execute(f"""
            INSERT INTO netflix.netflix_ranker_daily
            SELECT
                toDate(VISIT_TS) AS DAY,
                ifNull(NAME_OF_SHOW, '') AS NAME_OF_SHOW,
                ifNull(SEASON, '') AS SEASON,
                ifNull(EPISODE, '') AS EPISODE,
                EPISODE_NAME, TYPE, GENRE, RUN_TIME,
                count() AS VIEW_COUNT
            FROM netflix.netflix
            WHERE AVAILABLE = 'TRUE' AND toDate(VISIT_TS) = '{d_str}'
            GROUP BY DAY, NAME_OF_SHOW, SEASON, EPISODE, EPISODE_NAME, TYPE, GENRE, RUN_TIME
        """)
        results[d_str] = 'inserted'
    return results
