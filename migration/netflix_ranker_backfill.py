#!/usr/bin/env python3
"""Box-side runner for the Netflix ranker ingest (same code as the cron).

Use when the Render cron has been down long enough that the backlog will
not fit inside its HTTP time budget. Runs on the ClickHouse host:

    cd /root/finished_codes/bg-webapp
    nohup python3 -m migration.netflix_ranker_backfill > /tmp/nfx_backfill.log 2>&1 &

Options:
    --max-days N     enrich at most N days this invocation
    --skip-raw       skip Phase 0a (clickstream_final -> netflix_clickstream)
    --no-aggregate   skip Phase 1 (netflix -> netflix_ranker_daily)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from migration.clickhouse_connector import connect_clickhouse
from migration import netflix_ranker_ingest as nri


def _log(msg: str) -> None:
    print(f'[{datetime.utcnow().strftime("%H:%M:%S")}] {msg}', flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-days', type=int, default=None)
    ap.add_argument('--skip-raw', action='store_true')
    ap.add_argument('--no-aggregate', action='store_true')
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    conn = connect_clickhouse()
    cur = conn.cursor()

    ingest = nri.ingest_backlog(cur, max_days=args.max_days,
                                skip_raw=args.skip_raw, log=_log)
    _log(f'ingest: {ingest}')

    if not args.no_aggregate:
        days = nri.missing_ranker_days(cur)
        _log(f'Phase 1: {len(days)} day(s) missing from netflix_ranker_daily')
        res = nri.aggregate_days(cur, days, log=_log)
        inserted = sum(1 for v in res.values() if v == 'inserted')
        _log(f'Phase 1 done: {inserted} inserted, {len(res) - inserted} skipped')

    _log(f'finished in {time.monotonic() - t0:.0f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
